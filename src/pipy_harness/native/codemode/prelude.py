"""Codemode guest prelude: runs inside CPython-on-WASI, never on the host.

The worker passes this file's source to the guest interpreter as
``python -I -S -B -c <source>``, so it runs as ``__main__`` there. Importing it
on the host only defines the helpers (tests use them directly); nothing reads
or writes a stream until :func:`main` runs.

Wire protocol (JSON, one object per line; spike result §4.1). The guest's
stdin and stdout carry it:

- host -> guest, first line: ``{"type": "run", "code": str, "tools": [{"name":
  str, "id": str, "description": str}], "store": {str: str}}``: the callable
  tools in order, each with its own name, the identifier scripts write and
  its ``ALL_TOOLS`` description; and what ``load()`` reads, each key's value
  as JSON text (``store`` may be left out: an empty store)
- guest -> host: ``{"type": "call", "id": int, "name": str, "args": object}``;
  ids count up from 1. The guest may send further messages before the
  reply, with at most :data:`MAX_PENDING_REQUESTS` requests whose reply it
  has not read
- host -> guest: ``{"type": "result", "id": int, "ok": true, "text": str}``
  (the call returns the tool's text), ``{"type": "result", "id": int, "ok":
  true, "value": <JSON>}`` (the call returns the tool's structured content) or
  ``{"type": "result", "id": int, "ok": false, "error": str}`` (the call
  raises ``ToolError``)
- guest -> host: ``{"type": "discover", "id": int, "name": str, "args":
  object}`` for ``search_tools``, ``describe_tool`` and ``describe_namespace``;
  ids continue the sequence of the tool calls. Replies come in the order
  the requests settle, not the order they were made
- host -> guest: ``{"type": "result", "id": int, "ok": true, "value": <JSON>}``
  (the helper returns the value) or ``{"type": "result", "id": int, "ok":
  false, "error": str}`` (the helper raises ``ValueError``)
- guest -> host: ``{"type": "text", "value": str}``, one per ``text()`` call,
  ``print()`` call or direct write to ``sys.stdout``/``sys.stderr``, in order
  and never buffered
- guest -> host: ``{"type": "image", "data": str, "mimeType": str}``, one per
  ``image()`` call, in order with the text messages: base64 data of a PNG,
  JPEG, GIF or WebP image and the type its first bytes show
- guest -> host, last: ``{"type": "done", "ok": true}`` or ``{"type": "done",
  "ok": false, "error": {"type": str, "message": str, "traceback": str}}``. A
  successful script that changed the store adds ``"writes": [[key, json],
  [key], ...]``: a key's new value as JSON text, or the key alone for one it
  deleted. A ``done`` line with writes may be longer than other lines (the
  host allows for the store's limits)

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
# Requests the guest may have made whose reply it has not read. The host
# counts a request until its reply is written in full and treats one more as
# a protocol violation; a script that submits more waits for a reply first.
MAX_PENDING_REQUESTS = 16

# Pi's limits of ``store()``: characters of one value's JSON, and of every
# key and value together. They count Python characters of the JSON text
# ``store_json`` writes (Pi: UTF-16 units of ``JSON.stringify``).
MAX_STORE_VALUE_CHARS = 256 * 1024
MAX_STORE_TOTAL_CHARS = 1024 * 1024
STORE_HINT = (
    "store() is for small state such as IDs or summaries. Show images with "
    "image(), keep large data in variables, or write it to a file with a tool."
)

# Pi's output cap for one script: characters of base64 image data. The host
# enforces its own (``ScriptLimits.image_bytes``); a test pins that they match.
MAX_IMAGE_CHARS = 16 * 1024 * 1024
IMAGE_HELPER_EXPECTS = (
    "image expects a non-empty image URL string, an object with image_url, or "
    'an image block {"type": "image", "data", "mimeType"}'
)
_BASE64_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
)

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
    key: str,
    name: str,
    call: Callable[[str, dict[str, Any]], Any],
    submit: Callable[[str, dict[str, Any]], Any] | None,
) -> Callable[..., Any]:
    def tool(args: object = NO_ARGS, /, **kwargs: Any) -> Any:
        return call(name, call_arguments(key, args, kwargs))

    def submit_call(args: object = NO_ARGS, /, **kwargs: Any) -> Any:
        """Start the call and return at once; ``.result()`` waits for it."""

        if submit is None:
            raise TypeError(f"tools.{key}.submit() is not available")
        return submit(name, call_arguments(key, args, kwargs))

    tool.__name__ = tool.__qualname__ = key
    submit_call.__name__ = "submit"
    submit_call.__qualname__ = f"{key}.submit"
    tool.submit = submit_call  # type: ignore[attr-defined]
    return tool


class PendingCall:
    """A tool call that runs while the script goes on: what
    ``tools.<name>.submit(args)`` returns."""

    __slots__ = ("_name", "_outcome", "_wait")

    def __init__(self, name: str, wait: Callable[[], Any]) -> None:
        self._name = name
        self._wait = wait
        self._outcome: tuple[bool, Any] | None = None

    def result(self) -> Any:
        """Wait for the call; return what ``tools.<name>(args)`` would
        return, or raise its ``ToolError``."""

        if self._outcome is None:
            try:
                self._outcome = (True, self._wait())
            except ToolError as exc:
                self._outcome = (False, str(exc))
        ok, value = self._outcome
        if ok:
            return value
        raise ToolError(value)

    def __repr__(self) -> str:
        state = "pending" if self._outcome is None else "settled"
        return f"<{state} call of tools.{self._name}>"


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
        submit: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        """``keys`` maps every key to its tool's name; ``listed`` holds the
        ``ALL_TOOLS`` names in order. ``call`` runs a call to its end,
        ``submit`` starts one (``tools.<name>.submit``)."""

        object.__setattr__(self, "_state", (dict(keys), tuple(listed), call, submit))

    def __getattribute__(self, name: str) -> Any:
        if is_interpreter_attribute(name):
            # What the interpreter looks up (methods, copying, pickling).
            return object.__getattribute__(self, name)
        try:
            keys, listed, call, submit = object.__getattribute__(self, "_state")
        except AttributeError:
            # An instance made without __init__, as copying makes one.
            raise AttributeError(name) from None
        if name in keys:
            return _bound_tool(name, keys[name], call, submit)
        # An explicit name=None stops the interpreter from filling in name
        # and obj, so its own "Did you mean" does not repeat ours.
        raise AttributeError(unknown_tool_message(name, listed), name=None, obj=None)

    def __reduce__(self) -> Any:
        # Copying reads slots by attribute, which here would look for a tool.
        return type(self), object.__getattribute__(self, "_state")

    def __getitem__(self, key: object) -> Callable[..., Any]:
        keys, listed, call, submit = object.__getattribute__(self, "_state")
        if not isinstance(key, str):
            raise TypeError(f"tools[...] takes a tool name, not {type(key).__name__}")
        if key not in keys:
            raise KeyError(unknown_tool_message(key, listed))
        return _bound_tool(key, keys[key], call, submit)

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


def image_type(data: str) -> str | None:
    """The type the first bytes of base64 ``data`` show, of the formats
    providers accept inline: PNG, JPEG (not JPEG-LS), GIF and WebP.

    Pi's ``IMAGE_SIGNATURES``: a signature starts at byte 0, so its base64 is
    a prefix of the data.
    """

    if data.startswith("iVBORw0KGg"):
        return "image/png"
    if data.startswith("/9j/") and data[4:5] != "9":
        return "image/jpeg"
    if data.startswith(("R0lGODdh", "R0lGODlh")):
        return "image/gif"
    if data.startswith("UklG") and data[12:16] == "RUJQ":
        return "image/webp"
    return None


def is_base64(data: str) -> bool:
    """Whether ``data`` is complete, padded standard base64."""

    body = data.rstrip("=")
    return (
        bool(body)
        and len(data) % 4 == 0
        and len(data) - len(body) <= 2
        and _BASE64_ALPHABET.issuperset(body)
    )


def image_data_url(value: object) -> str:
    """The URL of an ``image()`` argument (Pi ``imageUrl``)."""

    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        raise TypeError(IMAGE_HELPER_EXPECTS)
    if "image_url" in value:
        if not isinstance(value["image_url"], str):
            raise TypeError(IMAGE_HELPER_EXPECTS)
        return value["image_url"]
    kind = value.get("type")
    if not isinstance(kind, str):
        raise TypeError(IMAGE_HELPER_EXPECTS)
    if kind != "image":
        raise TypeError(f'image only accepts image blocks, got "{kind}"')
    data = value.get("data")
    if not isinstance(data, str) or not data:
        raise TypeError("image expected image block data")
    if data[:5].lower() == "data:":
        return data
    return "data:;base64," + data


def checked_image(value: object) -> tuple[str, str]:
    """The base64 data and detected type of an ``image()`` argument.

    Pi's ``image``: a base64 ``data:`` URL, an ``{"image_url"}`` object or an
    image block. Providers reject a whole request for one bad image, and a
    stored image is sent again on every later turn, so the data is checked
    here: line breaks of wrapped base64 are dropped, and the declared type
    is ignored in favour of the detected one. Anything else raises
    ``TypeError`` in the script.
    """

    url = image_data_url(value)
    if url == "":
        raise TypeError(IMAGE_HELPER_EXPECTS)
    colon = url.find(":")
    scheme = "" if colon == -1 else url[:colon].lower()
    if scheme in ("http", "https"):
        raise TypeError(
            "remote image URLs are not supported in tool outputs. "
            "Pass a base64 data URI instead"
        )
    comma = url.find(",")
    header = [] if comma == -1 else url[colon + 1 : comma].split(";")
    if (
        scheme != "data"
        or comma == -1
        or all(part.lower() != "base64" for part in header[1:])
    ):
        raise TypeError("invalid image output. Pass a base64 data URI instead")
    data = "".join(url[comma + 1 :].split())
    if not is_base64(data):
        raise TypeError(
            "invalid image output. The image data is not valid base64 "
            "(truncated or corrupted?)"
        )
    mime_type = image_type(data)
    if mime_type is None:
        raise TypeError(
            "invalid image output. The image data is not a PNG, JPEG, GIF, "
            "or WebP image"
        )
    return data, mime_type


def image_helper(
    emit: Callable[[str, str], None], limit: int = MAX_IMAGE_CHARS
) -> Callable[[object], None]:
    """The script's ``image``: checks its argument, then emits the image.

    All images of one script together may hold ``limit`` characters of
    base64 data; the image that would pass it raises ``ValueError``.
    """

    shown = 0

    def image(value: object, /) -> None:
        """Add an image to the output: a base64 ``data:`` URL, a dict with
        ``image_url``, or an image block as MCP tools return it."""

        nonlocal shown
        data, mime_type = checked_image(value)
        if shown + len(data) > limit:
            raise ValueError(
                f"the script's images exceeded {limit} characters of base64 "
                "data; show fewer or smaller images"
            )
        shown += len(data)
        emit(data, mime_type)

    return image


def store_json(value: object) -> str:
    """The JSON text ``store()`` keeps for ``value``: compact, non-ASCII as
    is, ``NaN`` and ``Infinity`` refused. Raises ``TypeError``,
    ``ValueError`` or ``RecursionError`` for a value that is not JSON."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class ScriptStore:
    """``store(key, value)`` and ``load(key)`` (Pi's prelude).

    Both are synchronous and work on a snapshot of JSON text: what the host
    read from the session branch when the script started. ``writes()``
    reports what the script changed, for the host to keep when the script
    succeeds. ``store(key, None)`` (or ``store(key)``) deletes a key, which
    is Pi's ``store(key, undefined)``; so ``None`` is never stored at the
    top level, and ``load`` returns ``None`` for a key that is not there.
    Deleting a key the snapshot does not have is no write.
    """

    __slots__ = ("_chars", "_initial", "_stored", "_writes")

    def __init__(self, snapshot: dict[str, str]) -> None:
        self._stored = dict(snapshot)
        self._initial = frozenset(snapshot)
        # key -> new JSON text, or None for a deleted key; in write order.
        self._writes: dict[str, str | None] = {}
        self._chars = sum(len(key) + len(text) for key, text in snapshot.items())

    def store(self, key: object, value: object = None, /) -> None:
        """Keep ``value`` (any JSON value) under ``key`` for later scripts on
        this branch; ``None`` deletes the key."""

        key = _store_key("store", key)
        previous = len(key) + len(self._stored[key]) if key in self._stored else 0
        if value is None:
            if self._stored.pop(key, None) is not None:
                self._chars -= previous
            if key in self._initial:
                self._writes[key] = None
            else:
                self._writes.pop(key, None)
            return
        try:
            text = store_json(value)
        except (TypeError, ValueError, RecursionError) as exc:
            raise TypeError(
                f"store({_quoted(key)}) value is not JSON-serializable: {exc}"
            ) from None
        if len(text) > MAX_STORE_VALUE_CHARS:
            raise ValueError(
                f"store({_quoted(key)}) value has {len(text)} characters of JSON, "
                f"more than the limit of {MAX_STORE_VALUE_CHARS}. {STORE_HINT}"
            )
        chars = self._chars - previous + len(key) + len(text)
        if chars > MAX_STORE_TOTAL_CHARS:
            raise ValueError(
                "store is full: stored values would exceed "
                f"{MAX_STORE_TOTAL_CHARS} characters of JSON. Delete keys with "
                f"store(key, None). {STORE_HINT}"
            )
        self._stored[key] = text
        self._chars = chars
        self._writes[key] = text

    def load(self, key: object, /) -> Any:
        """The value stored under ``key`` (a fresh copy), or ``None``."""

        text = self._stored.get(_store_key("load", key))
        return None if text is None else json.loads(text)

    def writes(self) -> list[list[str]]:
        """The ``writes`` of a ``done`` message: ``[key, json]`` or ``[key]``."""

        return [
            [key] if text is None else [key, text] for key, text in self._writes.items()
        ]


def _store_key(name: str, key: object) -> str:
    if not isinstance(key, str):
        raise TypeError(f"{name}() key must be a string")
    return key


def _quoted(key: str) -> str:
    return json.dumps(key, ensure_ascii=False)


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
        # Requests whose reply has not been read, and replies read for a
        # request nobody waited for yet.
        self._waiting: set[int] = set()
        self._replies: dict[int, dict[str, Any]] = {}

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

    def image(self, data: str, mime_type: str) -> None:
        # ``type`` first: the host tells an image line by how it starts.
        self.send({"type": "image", "data": data, "mimeType": mime_type})

    def _request(self, message: dict[str, Any], label: str) -> int:
        """Send one request with the next id; return the id."""

        call_id = self._next_id + 1
        message["id"] = call_id
        try:
            line = encode(message)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                f"{label}() arguments must be JSON serializable: {exc}"
            ) from None
        while len(self._waiting) >= MAX_PENDING_REQUESTS:
            self._read_reply()
        self._next_id = call_id
        self._write(line)
        self._waiting.add(call_id)
        return call_id

    def _read_reply(self) -> None:
        reply = self.receive()
        reply_id = reply.get("id")
        if (
            reply.get("type") != "result"
            or type(reply_id) is not int  # bool is an int subclass: True == 1
            or reply_id not in self._waiting
        ):
            protocol_failure("the host sent an unexpected reply to a tool call")
        self._waiting.discard(reply_id)
        self._replies[reply_id] = reply

    def _reply(self, call_id: int) -> dict[str, Any]:
        """Wait for the reply to request ``call_id``; replies to other
        requests that arrive first are kept for them."""

        while call_id not in self._replies:
            self._read_reply()
        return self._replies.pop(call_id)

    def _exchange(self, message: dict[str, Any], label: str) -> dict[str, Any]:
        """Send one request with the next id and return its ``result`` line."""

        return self._reply(self._request(message, label))

    def call(self, name: str, args: dict[str, Any]) -> Any:
        return self._call_value(
            name,
            self._exchange(
                {"type": "call", "name": name, "args": args}, f"tools.{name}"
            ),
        )

    def submit_call(self, name: str, args: dict[str, Any]) -> PendingCall:
        call_id = self._request(
            {"type": "call", "name": name, "args": args}, f"tools.{name}.submit"
        )
        return PendingCall(name, lambda: self._call_value(name, self._reply(call_id)))

    def _call_value(self, name: str, reply: dict[str, Any]) -> Any:
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


def _run_request(
    message: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], dict[str, str]]:
    code = message.get("code")
    records = message.get("tools")
    snapshot = message.get("store", {})
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
        or not isinstance(snapshot, dict)
        or not all(isinstance(text, str) for text in snapshot.values())
    ):
        protocol_failure("the first host message is not a run request")
    return code, records, snapshot


def main() -> None:
    wire = Wire()
    code, records, snapshot = _run_request(wire.receive())
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

    script_store = ScriptStore(snapshot)
    namespace: dict[str, Any] = {
        "__name__": "__main__",
        "__builtins__": builtins,
        "tools": Tools(
            keys, [entry["name"] for entry in all_tools], wire.call, wire.submit_call
        ),
        "ALL_TOOLS": all_tools,
        "text": text,
        "image": image_helper(wire.image),
        "ToolError": ToolError,
        "store": script_store.store,
        "load": script_store.load,
        **discovery_helpers(wire.discover),
    }
    done = run_script(code, namespace)
    # Pi: only a script that succeeded (an early ``sys.exit()`` included)
    # reports its writes; a failed one's are discarded.
    if done["ok"] and (writes := script_store.writes()):
        done["writes"] = writes
    wire.send(done)


if __name__ == "__main__":
    main()
