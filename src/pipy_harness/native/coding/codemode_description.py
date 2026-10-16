"""The ``codemode`` tool as the model sees it (Pi ``extensions/codemode/tool.ts``).

While ``codemode`` is declared it presents the tools a script can call:

- its own description lists them under ``Nested tools:``, grouped by
  namespace, within a budget of estimated tokens;
- with mode ``on`` every declared tool a script can call says so in its own
  description, and ``codemode`` lists only the tools that are not ``direct``;
- with mode ``only`` ``codemode`` lists every callable tool, and requests
  leave out the declarations of the ``direct`` ones.

``deferred`` tools are never listed and namespace instructions are never
shown, so the description does not change while such tools register, as the
tools of MCP servers do when the servers connect.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pipy_harness.native.codemode.declarations import render_tool_output_type
from pipy_harness.native.coding.codemode_discovery import CodemodeDiscovery
from pipy_harness.native.tools.base import ToolDefinition, ToolNamespace
from pipy_harness.native.tools.codemode import CODEMODE_DESCRIPTION
from pipy_harness.native.tools.loadout import ToolLoadout, ToolLoadoutChanges

CodemodeMode = Literal["on", "only"]
DEFAULT_CODEMODE_MODE: CodemodeMode = "on"
# Estimated tokens the tool sections may use (Pi
# ``DEFAULT_CODEMODE_INLINE_BUDGET``).
DEFAULT_CODEMODE_INLINE_BUDGET = 3000
# Characters per token when estimating the cost of a tool section.
_CHARS_PER_TOKEN = 4
_CODEMODE_TOOL_NAME = "codemode"
_TEXT_OUTPUT_SCHEMA: Mapping[str, Any] = {"type": "string"}


def codemode_tool_guidelines(
    tools: Iterable[ToolDefinition],
) -> Mapping[str, tuple[str, ...]]:
    """The prompt guidelines of each tool that has some, by tool name.

    The system prompt gives them for declared tools only; a tool that scripts
    reach carries them in its declaration (Pi ``getToolGuidelines``).
    """

    return {
        tool.name: tool.prompt_guidelines for tool in tools if tool.prompt_guidelines
    }


@dataclass(frozen=True, slots=True)
class _Entry:
    name: str
    section: str
    cost: int


@dataclass(slots=True)
class _Group:
    namespace: ToolNamespace | None
    entries: list[_Entry] = field(default_factory=list)


def _section(tool: ToolDefinition, discovery: CodemodeDiscovery) -> str | None:
    """Pi ``renderToolSection``: the key a script uses (and the tool's own
    name when that differs), then its description and declaration."""

    key = discovery.key(tool.name)
    sample = discovery.sample(tool.name)
    if key is None or sample is None:
        return None
    heading = f"### `{key}`" if key == tool.name else f"### `{key}` (`{tool.name}`)"
    return f"{heading}\n{sample.strip()}"


def _cost(section: str) -> int:
    # Pi counts UTF-16 code units (``section.length``). A description may
    # hold a lone surrogate, which is one unit.
    units = len(section.encode("utf-16-le", "surrogatepass")) // 2
    return math.ceil(units / _CHARS_PER_TOKEN)


def _select_catalog(groups: Sequence[_Group], budget: float | None) -> set[str]:
    """Pi ``selectCatalog``: the tool sections that fit the budget.

    In each round every group places its cheapest remaining tool; a group
    whose next tool does not fit drops out while the others continue. Every
    namespace is represented before any namespace is complete.
    """

    if budget is None:
        return {entry.name for group in groups for entry in group.entries}
    queues = [sorted(group.entries, key=lambda entry: entry.cost) for group in groups]
    shown: set[str] = set()
    remaining = budget
    active = [queue for queue in queues if queue]
    while active:
        still_active = []
        for queue in active:
            entry = queue[0]
            if entry.cost > remaining:
                continue
            remaining -= entry.cost
            shown.add(entry.name)
            del queue[0]
            if queue:
                still_active.append(queue)
        active = still_active
    return shown


def _groups(
    tools: Sequence[ToolDefinition], discovery: CodemodeDiscovery
) -> list[_Group]:
    """The listed tools by namespace: tools without one first, then the
    namespaces by name."""

    groups: dict[str | None, _Group] = {None: _Group(None)}
    for tool in tools:
        if tool.exposure == "deferred":
            continue
        section = _section(tool, discovery)
        if section is None:
            continue
        namespace = tool.namespace
        key = None if namespace is None else namespace.name
        group = groups.setdefault(key, _Group(namespace))
        group.entries.append(_Entry(tool.name, section, _cost(section)))
    plain = groups.pop(None)
    # Case-insensitive first, as Pi's ``localeCompare`` orders letters.
    named = sorted(groups.items(), key=lambda item: (str(item[0]).casefold(), item[0]))
    return [plain, *(group for _, group in named)]


def _describe(
    tools: Sequence[ToolDefinition],
    discovery: CodemodeDiscovery,
    inline_budget: float | None,
) -> str:
    groups = _groups(tools, discovery)
    if not any(group.entries for group in groups):
        return CODEMODE_DESCRIPTION
    shown = _select_catalog(groups, inline_budget)
    sections = ["Nested tools:"]
    for group in groups:
        visible = [entry for entry in group.entries if entry.name in shown]
        namespace = group.namespace
        if namespace is not None:
            # Only tools that did not fit the budget count as not listed.
            if len(visible) == len(group.entries):
                listing = ""
            elif visible:
                listing = " (some tools not listed)"
            else:
                listing = " (tools not listed)"
            description = (namespace.description or "").strip()
            sections.append(
                f"## {namespace.name}{listing}"
                + (f"\n{description}" if description else "")
            )
        sections.extend(entry.section for entry in visible)
    return f"{CODEMODE_DESCRIPTION}\n\n" + "\n\n".join(sections)


def create_codemode_description(
    tools: Sequence[ToolDefinition],
    *,
    guidelines: Mapping[str, Sequence[str]] | None = None,
    inline_budget: float | None = None,
    callable_tools: Sequence[ToolDefinition] | None = None,
) -> str:
    """The model-facing description (Pi ``createCodemodeDescription``).

    The call contract and the globals, then one section per tool of
    ``tools``, grouped by namespace. ``deferred`` tools are left out as if
    they were not given. ``inline_budget`` is the number of estimated tokens
    (characters / 4) the tool sections may use; ``None`` lists every tool.
    ``callable_tools`` is everything the script can call, which decides the
    key a listed tool is reached by; it defaults to ``tools``.
    """

    discovery = CodemodeDiscovery(
        tools if callable_tools is None else callable_tools, guidelines=guidelines
    )
    return _describe(tools, discovery, inline_budget)


def _describe_fields(schema: object, depth: int) -> str | None:
    """Pi ``describeFields``: the field names of an object schema,
    ``{ output, exit_code, full_output_path? }``, with the fields of
    object-typed fields ``depth`` levels deep. ``None`` for other schemas."""

    if not isinstance(schema, Mapping):
        return None
    properties = schema.get("properties")
    if schema.get("type") != "object" or not isinstance(properties, Mapping):
        return None
    required = schema.get("required")
    required_names = (
        {name for name in required if isinstance(name, str)}
        if isinstance(required, (list, tuple))
        else set()
    )
    fields = []
    for name, value in properties.items():
        nested = _describe_fields(value, depth - 1) if depth > 0 else None
        fields.append(
            f"{name}{'' if name in required_names else '?'}"
            + (f": {nested}" if nested else "")
        )
    return "{ " + ", ".join(fields) + " }"


def _describe_output(schema: Mapping[str, Any] | None) -> str:
    """Pi ``describeOutput``: what a script call returns, in a few words."""

    rendered = render_tool_output_type(
        _TEXT_OUTPUT_SCHEMA if schema is None else schema
    )
    if rendered == "str":
        return "a string"
    fields = _describe_fields(schema, 1)
    return f"`{fields or ' '.join(rendered.split())}`"


def describe_script_call(tool: ToolDefinition, target: str) -> str:
    """Pi ``describeScriptCall``: a declared tool's description followed by
    how scripts call it (``target`` is ``tools.<identifier>``) and what the
    call returns. The arguments are the tool's declared parameters."""

    return (
        f"{tool.description.strip()}\n\n"
        f"Codemode: `{target}(args)` resolves to "
        f"{_describe_output(tool.output_schema)}."
    )


@dataclass(frozen=True, slots=True)
class CodemodeLoadout:
    """Pi ``prepareCodemodeLoadout`` for one reading of the settings.

    Tools are listed by exposure, not by the active set, so the ``codemode``
    description does not change when a ``deferred`` tool is activated.
    """

    mode: CodemodeMode = DEFAULT_CODEMODE_MODE
    inline_budget: float = DEFAULT_CODEMODE_INLINE_BUDGET

    def __call__(self, loadout: ToolLoadout) -> ToolLoadoutChanges:
        callable_tools = loadout.callable_tools
        # Pi ``prepareCodemodeLoadout``: the listed tools carry their
        # guidelines, which the system prompt has for declared tools only.
        guidelines = {
            tool.name: loadout.get_prompt_guidelines(tool.name)
            for tool in callable_tools
        }
        discovery = CodemodeDiscovery(callable_tools, guidelines=guidelines)
        # A tool no key reaches cannot be called, whatever the loadout says.
        targets = {
            tool.name: target
            for tool in callable_tools
            if (target := discovery.call_target(tool.name)) is not None
        }
        only = self.mode == "only"
        descriptions: dict[str, str] = {}
        if not only:
            for tool in loadout.declared_tools:
                if tool.name in targets:
                    descriptions[tool.name] = describe_script_call(
                        tool, targets[tool.name]
                    )
        listed = [tool for tool in callable_tools if only or tool.exposure != "direct"]
        descriptions[_CODEMODE_TOOL_NAME] = _describe(
            listed, discovery, self.inline_budget
        )
        hidden: frozenset[str] = frozenset()
        if only:
            hidden = frozenset(
                tool.name
                for tool in loadout.declared_tools
                if tool.exposure == "direct" and tool.name in targets
            )
        return ToolLoadoutChanges(descriptions, hidden)
