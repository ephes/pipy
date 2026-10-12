"""The ``mcp_servers`` section of the system prompt (Pi ``ext/index.ts``
``renderServersSection``).

Neither ``codemode`` nor ``tool_search`` lists the servers whose tools they
reach, so the model learns of them from this section: every enabled server
with ``codemode`` or ``deferred`` tools, how its tools are reached, and one
line of summary. What a server has is known from its configuration, so it is
listed before it connects; only a summary taken from the server's
instructions has to wait for the connection.

Sizes are counted in UTF-16 code units, as Pi's ``length`` does. Different
from Pi: a summary is never cut inside a character outside the Basic
Multilingual Plane (Pi can leave half of one), and the closing line names
pipy's ``search_tools()``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from pipy_harness.native.fuzzy import JS_WHITESPACE, js_trim
from pipy_harness.native.mcp.config import (
    McpServerEntry,
    configured_exposures,
    mcp_namespace,
)
from pipy_harness.native.tools.collation import locale_compare_key

# Name of the system prompt section that lists the servers whose tools are
# not declared.
MCP_SERVERS_SECTION: Final = "mcp_servers"
# Characters of one server's summary (Pi ``MAX_SERVER_DESCRIPTION_CHARS``).
MAX_SERVER_DESCRIPTION_CHARS: Final = 250
# Characters of the whole section. Summaries shrink to fit; when the server
# lines alone do not fit, the last servers are left out and counted in a
# closing line.
MAX_SERVERS_SECTION_CHARS: Final = 4096


@dataclass(frozen=True, slots=True)
class McpServerListing:
    """What the section needs of a server: its entry, and its instructions
    once it connected."""

    entry: McpServerEntry
    instructions: str | None = None


def has_indirect_tools(entry: McpServerEntry) -> bool:
    """Whether some of the server's tools are reached through codemode or
    tool_search (Pi ``hasIndirectTools``); known from the configuration."""

    exposures = configured_exposures(entry)
    return "codemode" in exposures or "deferred" in exposures


def _name_order(name: str) -> tuple[object, ...]:
    """Pi sorts the names with ``localeCompare``: letters without regard to
    case, ``_`` and ``-`` before digits, digits before letters; of two names
    that differ only in case the lowercase one comes first. The collation
    table knows lowercase text, and server names are ASCII."""

    return (
        locale_compare_key(name.lower()),
        tuple(char.isupper() for char in name),
    )


def _utf16_length(text: str) -> int:
    return len(text) + sum(1 for char in text if ord(char) > 0xFFFF)


def _utf16_prefix(text: str, units: int) -> str:
    """The longest prefix of whole characters within ``units`` code units."""

    used = 0
    for index, char in enumerate(text):
        used += 2 if ord(char) > 0xFFFF else 1
        if used > units:
            return text[:index]
    return text


def _truncate(text: str, limit: int) -> str:
    if _utf16_length(text) <= limit:
        return text
    if limit <= 1:
        return ""
    prefix = _utf16_prefix(text, limit - 1)
    end = len(prefix)
    while end and prefix[end - 1] in JS_WHITESPACE:
        end -= 1
    return prefix[:end] + "…"


def _summary(listing: McpServerListing) -> str:
    """First line of the configured description, or of the instructions."""

    description = listing.entry.config.get("description")
    text = js_trim(description) if isinstance(description, str) else ""
    if not text:
        text = listing.instructions or ""
    return js_trim(text.split("\n", 1)[0])


def _intro(reaches: Iterable[str]) -> str:
    """The first line. It explains only the ways of reaching tools that the
    listed servers use."""

    used = set(reaches)
    intro = "MCP servers whose tools are not declared to you."
    if "codemode" in used:
        intro += " Call the tools of `codemode` servers from codemode scripts."
    if "tool_search" in used:
        intro += " Load the tools of `tool_search` servers with `tool_search`."
    return intro


def _omitted(count: int) -> list[str]:
    if count <= 0:
        return []
    plural = "" if count == 1 else "s"
    return [f"- … {count} more server{plural}; find their tools with search_tools()"]


def render_servers_section(servers: Iterable[McpServerListing]) -> str | None:
    """The section's text without its tag, or ``None`` when no enabled
    server has ``codemode`` or ``deferred`` tools."""

    listed = sorted(
        (
            server
            for server in servers
            if server.entry.config.get("enabled") is not False
            and has_indirect_tools(server.entry)
        ),
        key=lambda server: _name_order(server.entry.name),
    )
    if not listed:
        return None
    reaches = [
        "codemode"
        if "codemode" in configured_exposures(server.entry)
        else "tool_search"
        for server in listed
    ]
    intro = _intro(reaches)
    heads = [
        f"- {mcp_namespace(server.entry.name)} ({reach})"
        for server, reach in zip(listed, reaches, strict=True)
    ]

    def size(kept: int) -> int:
        # The intro, the first `kept` server lines without summaries, and the
        # line that counts the servers left out.
        lines = [intro, *heads[:kept], *_omitted(len(listed) - kept)]
        return _utf16_length("\n".join(lines))

    kept = len(listed)
    while kept > 0 and size(kept) > MAX_SERVERS_SECTION_CHARS:
        kept -= 1
    # Each summary also takes a ": " separator.
    per_server = (
        0
        if kept == 0
        else min(
            MAX_SERVER_DESCRIPTION_CHARS,
            (MAX_SERVERS_SECTION_CHARS - size(kept)) // kept - 2,
        )
    )
    lines = []
    for server, head in zip(listed[:kept], heads, strict=False):
        summary = _truncate(_summary(server), per_server) if per_server > 0 else ""
        lines.append(f"{head}: {summary}" if summary else head)
    return "\n".join([intro, *lines, *_omitted(len(listed) - kept)])


__all__ = [
    "MAX_SERVERS_SECTION_CHARS",
    "MAX_SERVER_DESCRIPTION_CHARS",
    "MCP_SERVERS_SECTION",
    "McpServerListing",
    "has_indirect_tools",
    "render_servers_section",
]
