"""Codemode guest prelude: runs inside CPython-on-WASI, never on the host.

The worker passes this file's source to the guest interpreter as
``python -I -S -B -c <source>``, so it runs as ``__main__`` there. Importing it
on the host only defines the helpers (tests use them directly); nothing reads
or writes a stream until :func:`main` runs.

Wire protocol (JSON, one object per line; spike result §4.1). The guest's
stdin and stdout carry it:

- host -> guest, first line: ``{"type": "run", "code": str, "tools": [{"name":
  str, "id": str, "description": str}]}``: the callable tools in order, each
  with its own name, the identifier scripts write and its ``ALL_TOOLS``
  description
- guest -> host: ``{"type": "call", "id": int, "name": str, "args": object}``
- host -> guest: ``{"type": "result", "id": int, "ok": true, "text": str}``
  (the call returns the tool's text), ``{"type": "result", "id": int, "ok":
  true, "value": <JSON>}`` (the call returns the tool's structured content) or
  ``{"type": "result", "id": int, "ok": false, "error": str}`` (the call
  raises ``ToolError``)
- guest -> host: ``{"type": "discover", "id": int, "name": str, "args":
  object}`` for ``search_tools``, ``describe_tool`` and ``describe_namespace``;
  ids continue the sequence of the tool calls
- host -> guest: ``{"type": "result", "id": int, "ok": true, "value": <JSON>}``
  (the helper returns the value) or ``{"type": "result", "id": int, "ok":
  false, "error": str}`` (the helper raises ``ValueError``)
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
MAX_CLOSE_MATCHES = 5
DISCOVERY_NAMES = ("search_tools", "describe_tool", "describe_namespace")
DEFAULT_SEARCH_LIMIT = 8

_builtin_print = builtins.print

# Marks an omitted positional argument, so an explicit None is still rejected.
NO_ARGS: Any = object()


class ToolError(Exception):
    """A tool call failed; the message is the tool's error text."""


def _comparable(name: str) -> str:
    return "".join(char for char in name.lower() if char.isascii() and char.isalnum())


def unknown_tool_message(name: str, available: Iterable[str]) -> str:
    """Return the error text for a tool that does not exist.

    ``available`` holds the identifiers of the callable tools, in ``ALL_TOOLS``
    order. Close matches are named as Pi's prelude names them: the tools whose
    name is the same but for case and punctuation, else the ones whose name
    contains the wanted one or is contained in it. Without either, the most
    similar name is offered, which catches a misspelling.
    """

    names = list(available)
    wanted = _comparable(name)
    close = [candidate for candidate in names if _comparable(candidate) == wanted]
    if not close and wanted:
        close = [
            candidate
            for candidate in names
            if (known := _comparable(candidate))
            and (wanted in known or known in wanted)
        ]
    if not close:
        import difflib

        close = difflib.get_close_matches(name, names, n=1)
    message = f"tools.{name} does not exist."
    if close:
        listed = ", ".join(f"tools.{c}" for c in close[:MAX_CLOSE_MATCHES])
        message += f" Did you mean {listed}?"
    elif not names:
        return f"{message} No tools are available."
    elif len(names) <= MAX_LISTED_TOOLS:
        message += f" Available: {', '.join(names)}."
    return (
        f"{message} ALL_TOOLS lists every tool; search_tools(query) finds tools "
        f'by topic. Check for a tool with "{name}" in tools.'
    )


def tool_table(
    records: Iterable[dict[str, Any]],
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Return the keys of ``tools`` and the ``ALL_TOOLS`` entries.

    Each run-request record ``{name, id, description}`` is reachable by its
    identifier and by its own name. The first tool wins when two names give
    the same identifier, or when a name is another tool's identifier (Pi's
    prelude). An entry is named by the key that reaches its tool: the
    identifier, or the tool's own name when an earlier tool has the
    identifier. A tool that neither key reaches has no entry.
    """

    keys: dict[str, str] = {}
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for record in records:
        name, identifier = record["name"], record["id"]
        if name in seen:
            continue
        seen.add(name)
        keys.setdefault(identifier, name)
        keys.setdefault(name, name)
        key = tool_key(keys, name, identifier)
        if key is not None:
            entries.append({"name": key, "description": record["description"]})
    return keys, entries


def tool_key(keys: dict[str, str], name: str, identifier: str) -> str | None:
    """The key that reaches the tool ``name``: its identifier when it has it,
    else its own name, else ``None`` (earlier tools hold both keys)."""

    if keys.get(identifier) == name:
        return identifier
    if keys.get(name) == name:
        return name
    return None


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


def is_interpreter_attribute(name: str) -> bool:
    """Whether ``tools.<name>`` is the interpreter's (``__len__``), not a tool.

    A tool with such a name is reached as ``tools["<name>"]`` only.
    """

    return name.startswith("__") and name.endswith("__")


def _bound_tool(
    key: str, name: str, call: Callable[[str, dict[str, Any]], Any]
) -> Callable[..., Any]:
    def tool(args: object = NO_ARGS, /, **kwargs: Any) -> Any:
        return call(name, call_arguments(key, args, kwargs))

    tool.__name__ = tool.__qualname__ = key
    return tool


class Tools:
    """The ``tools`` object: ``tools.<identifier>`` and ``tools["<name>"]``
    return a callable for that tool.

    Every attribute that is not a ``__dunder__`` name is a tool or does not
    exist, so no tool name is shadowed by the object's own state.
    """

    __slots__ = ("_state",)

    def __init__(
        self,
        keys: dict[str, str],
        listed: Iterable[str],
        call: Callable[[str, dict[str, Any]], Any],
    ) -> None:
        """``keys`` maps every key to its tool's name; ``listed`` holds the
        ``ALL_TOOLS`` names in order."""

        object.__setattr__(self, "_state", (dict(keys), tuple(listed), call))

    def __getattribute__(self, name: str) -> Any:
        if is_interpreter_attribute(name):
            # What the interpreter looks up (methods, copying, pickling).
            return object.__getattribute__(self, name)
        try:
            keys, listed, call = object.__getattribute__(self, "_state")
        except AttributeError:
            # An instance made without __init__, as copying makes one.
            raise AttributeError(name) from None
        if name in keys:
            return _bound_tool(name, keys[name], call)
        # An explicit name=None stops the interpreter from filling in name
        # and obj, so its own "Did you mean" does not repeat ours.
        raise AttributeError(unknown_tool_message(name, listed), name=None, obj=None)

    def __reduce__(self) -> Any:
        # Copying reads slots by attribute, which here would look for a tool.
        return type(self), object.__getattribute__(self, "_state")

    def __getitem__(self, key: object) -> Callable[..., Any]:
        keys, listed, call = object.__getattribute__(self, "_state")
        if not isinstance(key, str):
            raise TypeError(f"tools[...] takes a tool name, not {type(key).__name__}")
        if key not in keys:
            raise KeyError(unknown_tool_message(key, listed))
        return _bound_tool(key, keys[key], call)

    def __contains__(self, key: object) -> bool:
        return (
            isinstance(key, str) and key in object.__getattribute__(self, "_state")[0]
        )

    def __iter__(self) -> Any:
        return iter(object.__getattribute__(self, "_state")[1])

    def __len__(self) -> int:
        return len(object.__getattribute__(self, "_state")[1])

    def __dir__(self) -> list[str]:
        return sorted(object.__getattribute__(self, "_state")[1])

    def __repr__(self) -> str:
        listed = object.__getattribute__(self, "_state")[1]
        return f"<tools: {', '.join(listed) or 'none'}>"


def discovery_helpers(
    discover: Callable[[str, dict[str, Any]], Any],
) -> dict[str, Callable[..., Any]]:
    """The script globals that find tools; the host answers every call."""

    def search_tools(
        query: object, limit: object = DEFAULT_SEARCH_LIMIT, namespace: object = None
    ) -> Any:
        """Rank the callable tools; returns ``[{"name", "description"}]``."""

        return discover(
            "search_tools", {"query": query, "limit": limit, "namespace": namespace}
        )

    def describe_tool(name: object, /) -> Any:
        """A tool's description and declaration, or ``None``."""

        return discover("describe_tool", {"name": name})

    def describe_namespace(name: object, /) -> Any:
        """``{"name", "description"?, "instructions"?, "tools"}`` or ``None``."""

        return discover("describe_namespace", {"name": name})

    return {
        "search_tools": search_tools,
        "describe_tool": describe_tool,
        "describe_namespace": describe_namespace,
    }


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

    def _exchange(self, message: dict[str, Any], label: str) -> dict[str, Any]:
        """Send one request with the next id and return its ``result`` line."""

        call_id = self._next_id + 1
        message["id"] = call_id
        try:
            line = encode(message)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                f"{label}() arguments must be JSON serializable: {exc}"
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
        return reply

    def call(self, name: str, args: dict[str, Any]) -> Any:
        reply = self._exchange(
            {"type": "call", "name": name, "args": args}, f"tools.{name}"
        )
        ok = reply.get("ok")
        if ok is True and "value" in reply and "text" not in reply:
            return reply["value"]
        if ok is True and "value" not in reply and isinstance(reply.get("text"), str):
            return str(reply["text"])
        if ok is False and isinstance(reply.get("error"), str):
            raise ToolError(reply["error"])
        protocol_failure("the host sent a malformed tool result")

    def discover(self, name: str, args: dict[str, Any]) -> Any:
        reply = self._exchange({"type": "discover", "name": name, "args": args}, name)
        ok = reply.get("ok")
        if ok is True and "value" in reply:
            return reply["value"]
        if ok is False and isinstance(reply.get("error"), str):
            raise ValueError(reply["error"])
        protocol_failure("the host sent a malformed discovery result")


def protocol_failure(reason: str) -> NoReturn:
    """End the guest at once; never surfaces as a script outcome."""

    os.write(2, f"codemode prelude: {reason}\n".encode())
    os._exit(EXIT_PROTOCOL)


def _run_request(message: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    code = message.get("code")
    records = message.get("tools")
    if (
        message.get("type") != "run"
        or not isinstance(code, str)
        or not isinstance(records, list)
        or not all(
            isinstance(record, dict)
            and all(
                isinstance(record.get(key), str)
                for key in ("name", "id", "description")
            )
            for record in records
        )
    ):
        protocol_failure("the first host message is not a run request")
    return code, records


def main() -> None:
    wire = Wire()
    code, records = _run_request(wire.receive())
    keys, all_tools = tool_table(records)
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
        "tools": Tools(keys, [entry["name"] for entry in all_tools], wire.call),
        "ALL_TOOLS": all_tools,
        "text": text,
        "ToolError": ToolError,
        **discovery_helpers(wire.discover),
    }
    wire.send(run_script(code, namespace))


if __name__ == "__main__":
    main()
