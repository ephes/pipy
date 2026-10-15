"""Which renderers draw the calls to a tool.

Pi ``ExtensionRunner.resolveToolRenderers``: the resolvers that extensions
registered with ``register_tool_renderer`` are asked in load order, each able
to defer to the next, and after the last one comes what the tool itself has.
In Pi the MCP extension is the last resolver (built-in extensions load after
the others): a call to ``mcp__<server>__<tool>`` that nothing else draws is
drawn as an MCP tool's call, so a resumed session shows such calls the same
way before their server connected, and when it never does. Here that rule is
part of what the tool itself has.

pipy draws the rows of its built-in tools, of ``codemode`` and of MCP tools
itself (``tool_rows``). What a lookup answers for them is a marker the rows
recognise, and also real renderers a resolver can call and wrap: they draw
pipy's own rows of the call through the context's row.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pipy_harness.native.tools.name_patterns import mcp_tool_label_from_name

if TYPE_CHECKING:
    from pipy_harness.native.extension_types import (
        ExtensionTool,
        ToolRenderContext,
        ToolRendererResolver,
    )


def _own_rows(ctx: ToolRenderContext, phase: str, label: str | None) -> object:
    """A component with pipy's own rows of ``ctx``'s call, or None where no
    row asked (a context made outside pipy's rows)."""

    from pipy_harness.native.extension_ui import lines_component

    draw = ctx._own_rows
    lines = None if draw is None else draw(phase, label)
    return None if lines is None else lines_component(list(lines))


@dataclass(frozen=True, slots=True)
class BuiltinToolRows:
    """pipy's own rows for the calls to a built-in tool or ``codemode``.

    A resolver gets this from ``next()``. Like Pi's built-in renderers, its
    ``render_call`` and ``render_result`` can be called and wrapped: they
    draw pipy's rows of the call (``ToolRenderers``'s shape).
    """

    def render_call(self, ctx: ToolRenderContext) -> object:
        return _own_rows(ctx, "call", None)

    def render_result(self, ctx: ToolRenderContext) -> object:
        return _own_rows(ctx, "result", None)


@dataclass(frozen=True, slots=True)
class McpToolRows:
    """pipy's own rows for the calls to an MCP server tool (Pi
    ``createMcpToolRenderers``), callable like ``BuiltinToolRows``."""

    # `<server>/<tool>` (Pi `label`), safe to print: see `_printable`.
    label: str

    def render_call(self, ctx: ToolRenderContext) -> object:
        return _own_rows(ctx, "call", self.label)

    def render_result(self, ctx: ToolRenderContext) -> object:
        return _own_rows(ctx, "result", self.label)


@dataclass(frozen=True, slots=True)
class ToolRendererLookup:
    """The renderers of one extension generation and one set of MCP tools.

    ``tools`` are the registered extension tools, by name, also those
    without renderers (Pi ``getToolDefinition``);
    ``mcp_labels`` the titles of the registered MCP server tools, by name;
    ``resolvers`` what extensions registered, in load order; it may be a
    view that changes while the session runs (an extension registers a
    resolver), and each ``resolve`` reads it once.
    """

    tools: Mapping[str, ExtensionTool] = field(default_factory=dict)
    mcp_labels: Mapping[str, str] = field(default_factory=dict)
    resolvers: Sequence[ToolRendererResolver] = ()

    def resolve(self, name: str, *, builtin: bool = False) -> object | None:
        """What draws a call to ``name``; None for pipy's generic row.

        ``builtin`` says that ``name`` is a tool pipy has rows of its own
        for (``tool_rows.OWN_ROWS_TOOLS``). The answer is a
        ``BuiltinToolRows``, a ``McpToolRows``, or an object with
        ``render_call`` and ``render_result`` (an extension tool, or what a
        resolver returned). None is the generic row for a built-in tool
        too: a resolver chose it by returning None without ``next()``, as
        in Pi.
        """

        return self._resolve(tuple(self.resolvers), name, 0, builtin)

    def _resolve(
        self,
        resolvers: tuple[ToolRendererResolver, ...],
        name: str,
        index: int,
        builtin: bool,
    ) -> object | None:
        if index >= len(resolvers):
            return self._own(name, builtin)
        rest: list[object | None] = []

        def next_renderers() -> object | None:
            if not rest:
                rest.append(self._resolve(resolvers, name, index + 1, builtin))
            return rest[0]

        try:
            chosen = resolvers[index](name, next_renderers)
            # Reading the attributes of what an extension returned can raise.
            valid = chosen is None or is_tool_renderers(chosen)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - a bad resolver is left out
            return next_renderers()
        # Not renderers: the resolver is left out, like one that raised.
        return chosen if valid else next_renderers()

    def _own(self, name: str, builtin: bool) -> object | None:
        """What the tool itself has (Pi: the registered tool's definition).

        A registered extension tool comes first, also one without renderers:
        an extension may replace ``codemode``, and a replacement without
        renderers gets the generic rows as in Pi, where only the built-in
        codemode tool brings ``codemodeRenderers``. The names of the other
        built-in tools are reserved, so their calls always keep pipy's rows,
        which is what Pi's ``withBuiltInRenderers`` gives a replacement of
        them without renderers.
        """

        tool = self.tools.get(name)
        if tool is not None:
            return tool
        if builtin:
            return BuiltinToolRows()
        label = self.mcp_labels.get(name) or mcp_tool_label_from_name(name)
        return None if label is None else McpToolRows(_printable(label))


def is_tool_renderers(value: object) -> bool:
    """Whether ``value`` has the shape of ``ToolRenderers``: both attributes,
    each a callable or None."""

    missing = object()
    for attribute in ("render_call", "render_result"):
        renderer = getattr(value, attribute, missing)
        if renderer is missing or not (renderer is None or callable(renderer)):
            return False
    return True


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


__all__ = [
    "BuiltinToolRows",
    "McpToolRows",
    "ToolRendererLookup",
    "as_tool_renderer_lookup",
    "is_tool_renderers",
]
