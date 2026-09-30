"""Replay and diff transcript system messages (Pi ``9e05370b2``).

Ports of Pi ``packages/ai/src/utils/transcript.ts`` (``getCurrentTools``,
``getCurrentSystemMessage``, ``getToolStateChanges``, ``toToolDeclaration``),
``packages/ai/src/utils/text.ts`` (``getSystemMessageText``,
``renderSystemMessageUpdate``),
``packages/coding-agent/src/core/system-prompt.ts``
(``diffSystemPromptSections``) and the tool part of
``packages/agent/src/agent-loop.ts`` ``declareToolChanges``. Pure functions:
no I/O, no product state.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import (
    AgentMessage,
    AgentSystemMessage,
    AgentToolDeclaration,
)
from pipy_harness.native.models import ProviderSystemMessage
from pipy_harness.native.tools.base import ToolDefinition

SystemSections = tuple[tuple[str, str | None], ...]


@dataclass(frozen=True, slots=True)
class AgentSystemPromptInput:
    """The system state one accepted run starts from.

    ``pending`` is the section patch the product computed for this run
    (Pi ``_preparePromptAndToolLoadout``), or ``None`` when the prompt is
    unchanged. ``declared_tools`` is the tool set the transcript declares
    before this run (Pi ``getCurrentTools``); the loop diffs each turn's
    executable tools against it.
    """

    pending: AgentSystemMessage | None
    declared_tools: tuple[AgentToolDeclaration, ...]

    def __post_init__(self) -> None:
        if self.pending is not None and type(self.pending) is not AgentSystemMessage:
            raise TypeError("pending must be an AgentSystemMessage or None")
        if not isinstance(self.declared_tools, tuple) or any(
            type(tool) is not AgentToolDeclaration for tool in self.declared_tools
        ):
            raise TypeError("declared_tools must be a tuple of AgentToolDeclaration")


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def tool_declaration(definition: ToolDefinition) -> AgentToolDeclaration:
    """Pi ``toToolDeclaration``: the model-visible part of one tool."""

    return AgentToolDeclaration(
        name=definition.name,
        description=definition.description,
        parameters_json=json.dumps(
            _plain_json(definition.input_schema),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )


def system_message_to_json(message: AgentSystemMessage) -> dict[str, Any]:
    """Pi ``SystemMessage`` JSON without ``timestamp`` (pipy omits it).

    Empty ``sections``/``toolsAdded``/``toolsRemoved`` are omitted, as Pi
    does; tool declarations are ``{name, description, parameters}``.
    """

    body: dict[str, Any] = {"role": "system", "content": message.content.value}
    if message.sections:
        body["sections"] = dict(message.sections)
    if message.tools_added:
        body["toolsAdded"] = [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": json.loads(tool.parameters_json),
            }
            for tool in message.tools_added
        ]
    if message.tools_removed:
        body["toolsRemoved"] = [{"name": name} for name in message.tools_removed]
    return body


def system_message_from_json(body: Mapping[str, Any]) -> AgentSystemMessage:
    """Decode :func:`system_message_to_json` output; raise ``ValueError``.

    Pi ``contentText``: string content as is, text blocks joined by a newline,
    and a missing content as ``""`` (``sessionEntryToContextMessages``).
    """

    raw_content = body.get("content")
    if raw_content is None:
        content = ""
    elif isinstance(raw_content, str):
        content = raw_content
    elif isinstance(raw_content, list) and all(
        isinstance(block, Mapping) for block in raw_content
    ):
        content = "\n".join(
            str(block.get("text", ""))
            for block in raw_content
            if block.get("type") == "text"
        )
    else:
        raise ValueError("system message content is invalid")
    raw_sections = body.get("sections", {})
    raw_added = body.get("toolsAdded", [])
    raw_removed = body.get("toolsRemoved", [])
    if (
        not isinstance(raw_sections, Mapping)
        or not isinstance(raw_added, list)
        or not isinstance(raw_removed, list)
    ):
        raise ValueError("system message fields are invalid")
    try:
        return AgentSystemMessage(
            content=ProductContent(content),
            sections=tuple((name, text) for name, text in raw_sections.items()),
            tools_added=tuple(
                AgentToolDeclaration(
                    name=tool["name"],
                    description=tool.get("description", ""),
                    parameters_json=json.dumps(
                        tool.get("parameters", {}),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
                for tool in raw_added
            ),
            tools_removed=tuple(tool["name"] for tool in raw_removed),
        )
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("system message fields are invalid") from error


def current_tools(
    messages: Iterable[object],
) -> tuple[AgentToolDeclaration, ...]:
    """Pi ``getCurrentTools``: the tools left after every declared change."""

    tools: dict[str, AgentToolDeclaration] = {}
    for message in messages:
        if not isinstance(message, AgentSystemMessage):
            continue
        for name in message.tools_removed:
            tools.pop(name, None)
        for tool in message.tools_added:
            # Pi ``Map.set`` keeps an existing key's position.
            tools[tool.name] = tool
    return tuple(tools.values())


def current_system_message(
    messages: Iterable[object],
) -> AgentSystemMessage | None:
    """Pi ``getCurrentSystemMessage``: replay every system message into one."""

    materialized = tuple(messages)
    content: list[str] = []
    sections: dict[str, str] = {}
    seen = False
    for message in materialized:
        if not isinstance(message, AgentSystemMessage):
            continue
        seen = True
        if message.content.value:
            content.append(message.content.value)
        for name, text in message.sections:
            if text is None:
                sections.pop(name, None)
            else:
                sections[name] = text
    tools = current_tools(materialized)
    if not seen and not tools:
        return None
    return AgentSystemMessage(
        content=ProductContent("\n\n".join(content)),
        sections=tuple(sections.items()),
        tools_added=tools,
    )


def system_message_text(message: AgentSystemMessage) -> str:
    """Pi ``getSystemMessageText``: content, then non-null sections."""

    parts = [message.content.value]
    parts.extend(text for _, text in message.sections if text is not None)
    return "\n\n".join(part for part in parts if part)


def render_system_message_update(message: AgentSystemMessage) -> str:
    """Pi ``renderSystemMessageUpdate``: a later message for the model.

    Section changes are framed by name; tool fields are not rendered.
    """

    parts: list[str] = []
    if message.content.value:
        parts.append(message.content.value)
    for name, text in message.sections:
        parts.append(
            f'Removed system prompt section "{name}".'
            if text is None
            else f'Updated system prompt section "{name}":\n\n{text}'
        )
    return "\n\n".join(parts)


def diff_sections(
    previous: SystemSections,
    current: Sequence[tuple[str, str]],
) -> SystemSections | None:
    """Pi ``diffSystemPromptSections``: a patch, or ``None`` when unchanged."""

    before = {name: text for name, text in previous if text is not None}
    after = dict(current)
    patch: list[tuple[str, str | None]] = [
        (name, text) for name, text in current if before.get(name) != text
    ]
    patch.extend((name, None) for name in before if name not in after)
    return tuple(patch) if patch else None


def tool_state_changes(
    previous: Sequence[AgentToolDeclaration],
    current: Sequence[AgentToolDeclaration],
) -> tuple[tuple[AgentToolDeclaration, ...], tuple[str, ...]]:
    """Pi ``getToolStateChanges``: a changed definition is removed and re-added."""

    before = {tool.name: tool for tool in previous}
    after = {tool.name: tool for tool in current}
    added = tuple(tool for tool in current if before.get(tool.name) != tool)
    removed = tuple(tool.name for tool in previous if after.get(tool.name) != tool)
    return added, removed


def system_prompt_input(
    transcript: Iterable[object],
    sections: Sequence[tuple[str, str]],
) -> AgentSystemPromptInput:
    """Diff a run's desired prompt sections against the transcript's replay.

    Pi ``_preparePromptAndToolLoadout``: the pending message patches the
    sections the model currently has; the tools it declares are the loop's
    business (:func:`declare_tool_changes`).
    """

    current = current_system_message(transcript)
    patch = diff_sections(current.sections if current is not None else (), sections)
    return AgentSystemPromptInput(
        pending=AgentSystemMessage(sections=patch) if patch is not None else None,
        declared_tools=current.tools_added if current is not None else (),
    )


def declare_tool_changes(
    declared: Sequence[AgentToolDeclaration],
    pending: AgentSystemMessage | None,
    executable: Sequence[AgentToolDeclaration],
) -> AgentSystemMessage | None:
    """Pi ``declareToolChanges`` for one turn start.

    ``declared`` is the tool set the committed transcript declares. A pending
    system message's own tool fields are intent: they are replaced by the
    delta between ``declared`` and the ``executable`` set, so replay always
    yields exactly the executable tools. Without a pending message, a
    non-empty delta becomes a new tools-only system message; an empty one
    emits nothing.
    """

    added, removed = tool_state_changes(tuple(declared), tuple(executable))
    if pending is not None:
        return replace(pending, tools_added=added, tools_removed=removed)
    if not added and not removed:
        return None
    return AgentSystemMessage(tools_added=added, tools_removed=removed)


def replayed_system_prompt(
    anchored: Sequence[ProviderSystemMessage], fallback: str
) -> str:
    """The prompt a collapsed request sends (Pi ``collapseSystemMessages``).

    Replaying the system messages keeps Pi's section order: a section added
    by a later patch follows the ones the model already had. Without system
    messages the ``fallback`` prompt stands.
    """

    replay = current_system_message(anchor.message for anchor in anchored)
    return system_message_text(replay) if replay is not None else fallback


def request_system_messages(
    history: Sequence[AgentMessage],
    persisted: Sequence[AgentMessage],
    persisted_anchors: Sequence[tuple[int, AgentSystemMessage]],
    active_input: AgentActiveInput,
    turn_index: int,
) -> tuple[ProviderSystemMessage, ...]:
    """Anchor the transcript's system messages in one request's messages.

    Pi builds each request from the persisted session projection. pipy's
    ``history`` is the run's coding history; ``persisted`` and
    ``persisted_anchors`` are the session's coding messages and system
    anchors. The run's messages not yet persisted follow them, so when
    ``persisted`` is not a prefix of ``history`` the request gets none (the
    collapse path). The turn's own system message is anchored before the
    accepted message on the first turn and after the history on later ones.
    Positions are in the request's frame: the transient overlay follows the
    accepted message.
    """

    if tuple(history[: len(persisted)]) != tuple(persisted):
        return ()
    accepted = active_input.accepted_index(history)
    overlay = len(active_input.request_overlay)

    def frame(position: int) -> int:
        return position + overlay if position > accepted else position

    anchored = [
        ProviderSystemMessage(frame(position), message)
        for position, message in persisted_anchors
    ]
    if active_input.turn_system_message is not None:
        position = accepted if turn_index == 0 else len(history) + overlay
        anchored.append(
            ProviderSystemMessage(position, active_input.turn_system_message)
        )
    return tuple(anchored)


def remap_system_messages(
    anchored: Sequence[ProviderSystemMessage],
    kept: Sequence[bool],
) -> tuple[ProviderSystemMessage, ...]:
    """Re-anchor system messages after dropping ``messages[i]`` where not ``kept[i]``.

    A position becomes the number of kept messages before it, so each system
    message stays after the same surviving messages.
    """

    prefix = [0]
    for keep in kept:
        prefix.append(prefix[-1] + (1 if keep else 0))
    return tuple(
        replace(anchor, position=prefix[anchor.position]) for anchor in anchored
    )
