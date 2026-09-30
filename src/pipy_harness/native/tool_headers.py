"""Tool-row headers of the built-in search and mutation tools, from Pi.

Ports the ``renderCall`` text of Pi's ``core/tools/renderers/{grep,find,ls,
write,edit}.ts`` and the helpers they use from ``render-utils.ts`` (pi-mono
``1b347794e``):

- ``grep /{pattern}/ in {path}`` plus `` ({glob})`` and `` limit {limit}``;
- ``find {pattern} in {path}`` plus `` (limit {limit})``;
- ``ls {path}`` plus `` (limit {limit})``;
- ``write {path}`` / ``edit {path}``.

``str()`` keeps a string, turns a missing or ``null`` argument into ``""``
(so the ``.`` and ``...`` fallbacks apply) and shows any other type as
``[invalid arg]``. ``shortenPath`` replaces a leading home directory with
``~`` (a plain string prefix, as in Pi). ``limit`` is shown whenever the
argument is present, formatted as JavaScript prints it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping

INVALID_ARG = "[invalid arg]"


def _js_str(value: object) -> str | None:
    """Pi ``str()``: the string, ``""`` for null/missing, None when invalid."""

    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return None


def _js_text(value: object) -> str:
    """``${value}`` in a JavaScript template literal, for a JSON value."""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int | float | str):
        return str(value)
    if isinstance(value, list):
        return ",".join("" if item is None else _js_text(item) for item in value)
    return "[object Object]" if isinstance(value, Mapping) else json.dumps(value)


def shorten_path(path: str) -> str:
    """Pi ``shortenPath``."""

    home = os.path.expanduser("~")
    if path.startswith(home):
        return f"~{path[len(home) :]}"
    return path


def _tool_path(raw: str | None, *, empty_fallback: str | None = None) -> str:
    """Pi ``renderToolPath`` without the styling and hyperlink."""

    if raw is None:
        return INVALID_ARG
    value = raw or empty_fallback
    if not value:
        return "..."
    return shorten_path(value)


def _search_path(data: Mapping[str, object]) -> str:
    raw = _js_str(data.get("path"))
    return INVALID_ARG if raw is None else shorten_path(raw or ".")


def _file_path(data: Mapping[str, object]) -> str:
    # Pi reads `args.file_path ?? args.path`.
    file_path = data.get("file_path")
    return _tool_path(_js_str(file_path if file_path is not None else data.get("path")))


def pi_tool_call_header(
    tool_name: str, data: Mapping[str, object]
) -> tuple[str, str] | None:
    """``(title, rest)`` of Pi's call header, or None for another tool."""

    if tool_name == "grep":
        pattern = _js_str(data.get("pattern"))
        text = INVALID_ARG if pattern is None else f"/{pattern}/"
        text += f" in {_search_path(data)}"
        glob = _js_str(data.get("glob"))
        if glob:
            text += f" ({glob})"
        if "limit" in data:
            text += f" limit {_js_text(data['limit'])}"
        return "grep", text
    if tool_name == "find":
        pattern = _js_str(data.get("pattern"))
        text = INVALID_ARG if pattern is None else pattern
        text += f" in {_search_path(data)}"
        if "limit" in data:
            text += f" (limit {_js_text(data['limit'])})"
        return "find", text
    if tool_name == "ls":
        text = _tool_path(_js_str(data.get("path")), empty_fallback=".")
        if "limit" in data:
            text += f" (limit {_js_text(data['limit'])})"
        return "ls", text
    if tool_name in {"write", "edit"}:
        return tool_name, _file_path(data)
    return None


__all__ = ["INVALID_ARG", "pi_tool_call_header", "shorten_path"]
