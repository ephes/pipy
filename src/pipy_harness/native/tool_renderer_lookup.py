"""Which renderers draw the calls to a tool.

Pi ``ExtensionRunner.resolveToolRenderers`` with the resolver its MCP
extension registers (``extensions/mcp/index.ts``): a registered tool's own
renderers come first; a call to ``mcp__<server>__<tool>`` that has none is
drawn as an MCP tool's call anyway, so a resumed session shows such calls the
same way before their server connected, and when it never does.

pipy draws MCP rows itself (``tool_rows``), so what a lookup answers for an
MCP tool is the row's title, not a pair of extension renderers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pipy_harness.native.tools.name_patterns import mcp_tool_label_from_name

if TYPE_CHECKING:
    from pipy_harness.native.extension_types import ExtensionTool


@dataclass(frozen=True, slots=True)
class McpToolRows:
    """pipy's own rows for the calls to an MCP server tool."""

    # `<server>/<tool>` (Pi `label`), safe to print: see `_printable`.
    label: str


@dataclass(frozen=True, slots=True)
class ToolRendererLookup:
    """The renderers of one extension generation and one set of MCP tools.

    ``tools`` are the extension tools that have a renderer, by name;
    ``mcp_labels`` the titles of the registered MCP server tools, by name.
    """

    tools: Mapping[str, ExtensionTool] = field(default_factory=dict)
    mcp_labels: Mapping[str, str] = field(default_factory=dict)

    def resolve(self, name: str) -> ExtensionTool | McpToolRows | None:
        """What draws a call to ``name``; None for pipy's generic row."""

        tool = self.tools.get(name)
        if tool is not None:
            return tool
        label = self.mcp_labels.get(name) or mcp_tool_label_from_name(name)
        return None if label is None else McpToolRows(_printable(label))


def _printable(label: str) -> str:
    """``label`` with a space for every character that is not printable.

    A server chooses its tools' names, and a title goes to the terminal as
    it is: an escape sequence, a control or a formatting character in a name
    must not get there.
    """

    return "".join(char if char.isprintable() else " " for char in label)


def as_tool_renderer_lookup(
    renderers: Mapping[str, ExtensionTool] | ToolRendererLookup | None,
) -> ToolRendererLookup:
    """A lookup for ``renderers``; a mapping of extension tools is copied."""

    if isinstance(renderers, ToolRendererLookup):
        return renderers
    return ToolRendererLookup(tools=dict(renderers or {}))


__all__ = ["McpToolRows", "ToolRendererLookup", "as_tool_renderer_lookup"]
