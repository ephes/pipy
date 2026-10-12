"""Tool name patterns and the names that count as MCP tools.

``--tools`` and ``--exclude-tools`` entries are exact tool names or patterns
in which ``*`` matches any characters (Pi ``core/mcp-servers.ts``). Standard
library only, so the capability facade and the MCP configuration can share it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from functools import lru_cache

MCP_TOOL_NAME_PREFIX = "mcp__"

# The MCP resource tools reach every server with resources.
MCP_RESOURCE_TOOL_NAMES = frozenset(
    {"list_mcp_resources", "list_mcp_resource_templates", "read_mcp_resource"}
)

# What ``.`` matches in a JavaScript regular expression without the ``s`` flag.
_ANY_CHARACTERS = "[^\n\r  ]*"


def create_tool_name_matcher(entries: Iterable[str]) -> Callable[[str], bool]:
    """Whether a tool name matches any entry, each an exact name or a pattern.

    Pi ``createToolNameMatcher``: an entry without ``*`` matches only itself;
    in an entry with ``*`` every ``*`` matches any characters and everything
    else is literal. The whole name has to match.
    """

    return _matcher(tuple(entries))


@lru_cache(maxsize=64)
def _matcher(entries: tuple[str, ...]) -> Callable[[str], bool]:
    names = frozenset(entry for entry in entries if "*" not in entry)
    patterns = tuple(
        re.compile(_ANY_CHARACTERS.join(re.escape(part) for part in entry.split("*")))
        for entry in entries
        if "*" in entry
    )

    def matches(name: str) -> bool:
        return name in names or any(pattern.fullmatch(name) for pattern in patterns)

    return matches


def is_mcp_tool_name(name: str) -> bool:
    """Whether a tool comes from MCP (Pi ``isMcpToolName``).

    A server tool is named ``mcp__<server>__<tool>``; the resource tools have
    fixed names.
    """

    return name.startswith(MCP_TOOL_NAME_PREFIX) or name in MCP_RESOURCE_TOOL_NAMES
