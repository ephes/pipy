"""Node-shaped filesystem error texts for the mutation tools.

Pi's ``write`` and ``edit`` let Node's ``fs`` errors propagate, and the agent
loop turns the thrown message into the tool result
(``createErrorToolResult(error.message)``). Node formats those messages as
``{CODE}: {libuv description}, {syscall} '{path}'``. :func:`node_fs_message`
reproduces that shape for an :class:`OSError`; :func:`error_code` gives the
bare code Pi's ``edit`` puts in ``Could not edit file: … Error code: {CODE}.``
"""

from __future__ import annotations

import errno
import re

# libuv's `uv_strerror` texts for the codes a file write or read can raise.
_LIBUV_TEXT = {
    "EACCES": "permission denied",
    "EEXIST": "file already exists",
    "EISDIR": "illegal operation on a directory",
    "ENAMETOOLONG": "name too long",
    "ENOENT": "no such file or directory",
    "ENOSPC": "no space left on device",
    "ENOTDIR": "not a directory",
    "EPERM": "operation not permitted",
    "EROFS": "read-only file system",
    "ELOOP": "too many symbolic links encountered",
}


def error_code(exc: OSError) -> str | None:
    """The symbolic errno name (``ENOENT``), or None when there is none."""

    if exc.errno is None:
        return None
    return errno.errorcode.get(exc.errno)


def node_null_byte_message(path: str) -> str:
    """Node's ``ERR_INVALID_ARG_VALUE`` text for a path holding a NUL byte."""

    inspected = _inspect_string(path)
    # Node's ERR_INVALID_ARG_VALUE cuts the inspected value to 128 UTF-16
    # units; a pair cut in half is written as U+FFFD.
    units = inspected.encode("utf-16-le", "surrogatepass")
    if len(units) > 256:
        inspected = units[:256].decode("utf-16-le", "replace") + "..."
    return (
        "The argument 'path' must be a string, Uint8Array, or URL without null "
        f"bytes. Received {inspected}"
    )


# Node `util.inspect`'s `strEscape` table for the C0 controls.
_C0_ESCAPES = {
    **{code: f"\\x{code:02X}" for code in range(0x20)},
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
}


def _inspect_string(value: str) -> str:
    """Node ``util.inspect`` of a string (its quote choice and escapes)."""

    quote = "'"
    if "'" in value:
        if '"' not in value:
            quote = '"'
        elif "`" not in value and "${" not in value:
            quote = "`"
    out: list[str] = []
    for char in value:
        code = ord(char)
        if code < 0x20:
            out.append(_C0_ESCAPES[code])
        elif 0x7F <= code <= 0x9F:
            out.append(f"\\x{code:02X}")
        elif char == "\\":
            out.append("\\\\")
        elif char == quote:
            out.append(f"\\{quote}")
        elif 0xD800 <= code <= 0xDFFF:
            out.append(f"\\u{code:04x}")
        else:
            out.append(char)
    return f"{quote}{''.join(out)}{quote}"


def node_encodable_text(text: str) -> str:
    """The text Node's UTF-8 encoder writes for a JavaScript string.

    JSON allows lone surrogates, and edits can put a high and a low surrogate
    next to each other. JavaScript strings are UTF-16, so an adjacent pair is
    one character and a lone surrogate is written as U+FFFD. Python keeps
    each surrogate as its own code point and refuses to encode it, so the text
    takes a UTF-16 round trip that joins the pairs and replaces the rest.
    """

    if _SURROGATE.search(text) is None:
        return text
    return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


_SURROGATE = re.compile(f"[{chr(0xD800)}-{chr(0xDFFF)}]")


def node_fs_message(exc: OSError, syscall: str, path: str | None) -> str:
    """Node's message for ``exc`` raised by ``syscall`` on ``path``.

    Node names no path for a failure on an open descriptor (``read``).
    """

    code = error_code(exc)
    if code is None:
        return str(exc)
    text = _LIBUV_TEXT.get(code) or (exc.strerror or code).lower()
    where = f" '{path}'" if path is not None else ""
    return f"{code}: {text}, {syscall}{where}"


__all__ = [
    "error_code",
    "node_fs_message",
    "node_null_byte_message",
    "node_encodable_text",
]
