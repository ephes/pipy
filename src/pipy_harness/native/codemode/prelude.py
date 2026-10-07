"""Codemode guest prelude: runs inside CPython-on-WASI, never on the host.

The worker passes this file's source to the guest interpreter as
``python -I -S -B -c <source>``, so it runs as ``__main__`` there. Importing it
on the host only defines the helpers (tests use them directly); nothing reads
or writes a stream until :func:`main` runs.

Wire protocol (JSON, one object per line; spike result §4.1). The guest's
stdin and stdout carry it:

- host -> guest, first line: ``{"type": "run", "code": str, "tools": [str]}``
- guest -> host: ``{"type": "call", "id": int, "name": str, "args": object}``
- host -> guest: ``{"type": "result", "id": int, "ok": true, "value": str}`` or
  ``{"type": "result", "id": int, "ok": false, "error": str}``
- guest -> host: ``{"type": "text", "value": str}``, one per ``text()`` call,
  ``print()`` call or direct write to ``sys.stdout``/``sys.stderr``, in order
  and never buffered
- guest -> host, last: ``{"type": "done", "ok": true}`` or ``{"type": "done",
  "ok": false, "error": {"type": str, "message": str, "traceback": str}}``

The traceback keeps only frames from the script (``<codemode>``). A malformed
host message ends the guest with :data:`EXIT_PROTOCOL` before anything else
runs, so a broken channel can never be mistaken for a script outcome.

Everything here is a convenience for well-behaved scripts, not a security
boundary: the script runs in this same interpreter and can write any bytes to
fd 1. The host treats every guest message as untrusted.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import sys

# Guest startup cost matters: keep typing out of the guest's imports.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from typing import IO, Any, NoReturn

SCRIPT_FILENAME = "<codemode>"
EXIT_PROTOCOL = 3
MAX_LISTED_TOOLS = 20

_builtin_print = builtins.print

# Marks an omitted positional argument, so an explicit None is still rejected.
NO_ARGS: Any = object()


class ToolError(Exception):
    """A tool call failed; the message is the tool's error text."""


def unknown_tool_message(name: str, available: Iterable[str]) -> str:
    """Return the ``AttributeError`` text for ``tools.<name>`` (spike §4.6)."""

    import difflib

    names = sorted(available)
    message = f"tools.{name} is not an available tool."
    close = difflib.get_close_matches(name, names, n=1)
    if close:
        return f"{message} Did you mean tools.{close[0]}?"
    if not names:
        return f"{message} No tools are available."
    if len(names) <= MAX_LISTED_TOOLS:
        listed = ", ".join(f"tools.{candidate}" for candidate in names)
        return f"{message} Available tools: {listed}."
    return message


def call_arguments(name: str, args: object, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return the JSON object a ``tools.<name>(...)`` call sends."""

    if args is not NO_ARGS and kwargs:
        raise TypeError(
            f"tools.{name}() takes one dict of arguments or keyword arguments, not both"
        )
    if args is NO_ARGS:
        return dict(kwargs)
    if not isinstance(args, dict):
        raise TypeError(
            f"tools.{name}() takes a dict of arguments, not {type(args).__name__}"
        )
    return args


class Tools:
    """The ``tools`` object: attribute access returns a callable for that tool."""

    __slots__ = ("_call", "_names")

    def __init__(
        self, names: Iterable[str], call: Callable[[str, dict[str, Any]], str]
    ) -> None:
        self._names = frozenset(names)
        self._call = call

    def __getattr__(self, name: str) -> Callable[..., str]:
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in self._names:
            # An explicit name=None stops the interpreter from filling in name
            # and obj, so its own "Did you mean" does not repeat ours.
            raise AttributeError(
                unknown_tool_message(name, self._names), name=None, obj=None
            )
        call = self._call

        def tool(args: object = NO_ARGS, /, **kwargs: Any) -> str:
            return call(name, call_arguments(name, args, kwargs))

        tool.__name__ = tool.__qualname__ = name
        return tool

    def __dir__(self) -> list[str]:
        return sorted(self._names)

    def __repr__(self) -> str:
        return f"<tools: {', '.join(sorted(self._names)) or 'none'}>"


def text_value(value: object) -> str:
    """Strings as-is, anything else as JSON (``repr`` when it is not JSON)."""

    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return repr(value)


class TextStream(io.TextIOBase):
    """A ``sys.stdout`` whose every write becomes one ``text`` message."""

    def __init__(self, emit: Callable[[str], None]) -> None:
        super().__init__()
        self._emit = emit

    encoding = "utf-8"

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        if not isinstance(s, str):
            raise TypeError(f"write() argument must be str, not {type(s).__name__}")
        if s:
            self._emit(s)
        return len(s)


def _print(
    *values: object,
    sep: str | None = " ",
    end: str | None = "\n",
    file: IO[str] | None = None,
    flush: bool = False,
) -> None:
    """``print`` that sends one ``text`` message per call to a :class:`TextStream`."""

    target = sys.stdout if file is None else file
    if not isinstance(target, TextStream):
        _builtin_print(*values, sep=sep, end=end, file=target, flush=flush)
        return
    sep = " " if sep is None else sep
    end = "\n" if end is None else end
    if not isinstance(sep, str) or not isinstance(end, str):
        raise TypeError("sep and end must be None or a string")
    target.write(sep.join(str(value) for value in values) + end)


def _trim(exc_info: Any, seen: set[int]) -> None:
    """Keep only ``<codemode>`` frames in a TracebackException and its chain."""

    import traceback

    if exc_info is None or id(exc_info) in seen:
        return
    seen.add(id(exc_info))
    exc_info.stack = traceback.StackSummary.from_list(
        [frame for frame in exc_info.stack if frame.filename == SCRIPT_FILENAME]
    )
    _trim(exc_info.__cause__, seen)
    _trim(exc_info.__context__, seen)
    for member in exc_info.exceptions or ():
        _trim(member, seen)


def _safe_str(exc: BaseException) -> str:
    try:
        return str(exc)
    except Exception:  # noqa: BLE001 - a script's broken __str__ must not hide its error
        return f"<unprintable {type(exc).__name__}>"


def describe_error(exc: BaseException) -> dict[str, str]:
    """Return ``{type, message, traceback}`` with the traceback trimmed to the script."""

    import traceback

    exc_info = traceback.TracebackException.from_exception(exc)
    _trim(exc_info, set())
    return {
        "type": type(exc).__name__,
        "message": _safe_str(exc),
        "traceback": "".join(exc_info.format()),
    }


def run_script(code: str, namespace: dict[str, Any]) -> dict[str, Any]:
    """Run ``code`` in ``namespace``; return the ``done`` message."""

    import linecache

    linecache.cache[SCRIPT_FILENAME] = (
        len(code),
        None,
        code.splitlines(keepends=True),
        SCRIPT_FILENAME,
    )
    try:
        # dont_inherit: this module's __future__ imports must not leak into the script.
        exec(compile(code, SCRIPT_FILENAME, "exec", dont_inherit=True), namespace)
    except SystemExit as exc:
        if exc.code is None or exc.code == 0:
            return {"type": "done", "ok": True}
        return _failed(exc, namespace)
    except BaseException as exc:  # noqa: BLE001 - every script failure becomes the done message
        return _failed(exc, namespace)
    return {"type": "done", "ok": True}


def _failed(exc: BaseException, namespace: dict[str, Any]) -> dict[str, Any]:
    try:
        error = describe_error(exc)
    except MemoryError:
        error = None
    # Free what the script allocated (a MemoryError usually still holds it)
    # before formatting again and encoding the reply.
    namespace.clear()
    if error is None:
        try:
            error = describe_error(exc)
        except MemoryError:
            error = {"type": type(exc).__name__, "message": "", "traceback": ""}
    return {"type": "done", "ok": False, "error": error}


def encode(message: dict[str, Any]) -> bytes:
    """One strict JSON line: NaN and Infinity are not JSON and raise ValueError."""

    return (json.dumps(message, separators=(",", ":"), allow_nan=False) + "\n").encode()


class Wire:
    """The prelude's end of the protocol: raw fds, unbuffered writes."""

    def __init__(self, in_fd: int = 0, out_fd: int = 1) -> None:
        self._in = open(in_fd, "rb", closefd=False)
        self._out_fd = out_fd
        self._next_id = 0

    def send(self, message: dict[str, Any]) -> None:
        self._write(encode(message))

    def _write(self, line: bytes) -> None:
        data = memoryview(line)
        while data:
            data = data[os.write(self._out_fd, data) :]

    def receive(self) -> dict[str, Any]:
        line = self._in.readline()
        if not line:
            protocol_failure("the host closed the channel")
        try:
            message = json.loads(line)
        except ValueError:
            protocol_failure("the host sent a line that is not JSON")
        if not isinstance(message, dict):
            protocol_failure("the host sent a line that is not an object")
        return message

    def text(self, value: str) -> None:
        self.send({"type": "text", "value": value})

    def call(self, name: str, args: dict[str, Any]) -> str:
        call_id = self._next_id + 1
        message = {"type": "call", "id": call_id, "name": name, "args": args}
        try:
            line = encode(message)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                f"tools.{name}() arguments must be JSON serializable: {exc}"
            ) from None
        self._next_id = call_id
        self._write(line)
        reply = self.receive()
        reply_id = reply.get("id")
        if (
            reply.get("type") != "result"
            or type(reply_id) is not int  # bool is an int subclass: True == 1
            or reply_id != call_id
        ):
            protocol_failure("the host sent an unexpected reply to a tool call")
        if reply.get("ok") is True and isinstance(reply.get("value"), str):
            return str(reply["value"])
        if reply.get("ok") is False and isinstance(reply.get("error"), str):
            raise ToolError(reply["error"])
        protocol_failure("the host sent a malformed tool result")


def protocol_failure(reason: str) -> NoReturn:
    """End the guest at once; never surfaces as a script outcome."""

    os.write(2, f"codemode prelude: {reason}\n".encode())
    os._exit(EXIT_PROTOCOL)


def _run_request(message: dict[str, Any]) -> tuple[str, list[str]]:
    code = message.get("code")
    names = message.get("tools")
    if (
        message.get("type") != "run"
        or not isinstance(code, str)
        or not isinstance(names, list)
        or not all(isinstance(name, str) for name in names)
    ):
        protocol_failure("the first host message is not a run request")
    return code, names


def main() -> None:
    wire = Wire()
    code, names = _run_request(wire.receive())
    stream = TextStream(wire.text)
    empty = io.StringIO()
    # setattr: these names are typed as final/overloaded, but reassigning
    # them is exactly what routes the script's output.
    for name, value in (
        ("stdin", empty),
        ("__stdin__", empty),
        ("stdout", stream),
        ("__stdout__", stream),
        ("stderr", stream),
        ("__stderr__", stream),
    ):
        setattr(sys, name, value)
    setattr(builtins, "print", _print)

    def text(value: object, /) -> None:
        """Emit ``value``: a string as-is, anything else as JSON."""

        wire.text(text_value(value))

    namespace: dict[str, Any] = {
        "__name__": "__main__",
        "__builtins__": builtins,
        "tools": Tools(names, wire.call),
        "text": text,
        "ToolError": ToolError,
    }
    wire.send(run_script(code, namespace))


if __name__ == "__main__":
    main()
