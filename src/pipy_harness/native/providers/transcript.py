"""Provider-side view of the transcript's system messages (Pi ``9e05370b2``).

Ports of Pi ``packages/ai/src/utils/transcript.ts`` (``resolveTranscript``,
``collapseSystemMessages``, ``getDeclaredTools``, ``hasToolRedefinitions``,
``hasNonAdditiveToolChanges``, ``resolveTranscriptTools``) and
over pipy's request shape (``renderSystemMessageUpdate`` lives with the
other replay helpers in ``native/agent/system_messages.py``).

A :class:`~pipy_harness.native.models.ProviderRequest` carries provider
history in ``messages`` and the transcript's system messages beside it in
``system_messages`` (each anchored before ``messages[position]``). With no
system messages, or for a model that does not accept them mid-conversation,
every adapter takes the collapse path: the out-of-band ``system_prompt`` and
``available_tools``, exactly the request it sent before system messages
reached providers. Pure functions: no I/O.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace

from pipy_harness.native.agent.messages import (
    AgentMessage,
    AgentSystemMessage,
    AgentToolDeclaration,
)
from pipy_harness.native.agent.system_messages import (
    current_system_message,
    system_message_text,
)
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.tools.base import ToolDefinition

TranscriptItem = AgentMessage | AgentSystemMessage


@dataclass(frozen=True, slots=True)
class ResolvedTranscript:
    """What one adapter serializes (Pi ``resolveTranscript``).

    ``mid_convo`` is false on the collapse path: ``leading_text`` is the
    request's ``system_prompt`` and ``items`` its messages. Otherwise
    ``leading_text`` is the leading system message's text (plus the
    out-of-band tail, pipy's compaction summary) and ``items`` interleaves
    the later system messages with the messages in transcript order.
    ``system_messages`` are all of them in order, with hidden declarations
    removed; ``has_leading`` says whether the first one leads the transcript
    (Pi ``getInitialSystemMessage``). A session recorded before system
    messages existed has none leading: its first system message follows old
    messages and is a later one.
    """

    leading_text: str
    items: tuple[TranscriptItem, ...]
    mid_convo: bool
    system_messages: tuple[AgentSystemMessage, ...] = ()
    has_leading: bool = False

    @property
    def initial_tools(self) -> tuple[AgentToolDeclaration, ...]:
        """Pi ``getInitialSystemMessage(messages)?.toolsAdded ?? []``."""

        return self.system_messages[0].tools_added if self.has_leading else ()


def resolve_request_transcript(
    request: ProviderRequest, *, supports_mid_convo: bool
) -> ResolvedTranscript:
    """Keep later system messages in place when the model accepts them.

    Collapses (the request as sent before SYS1b) when the model does not
    accept them, the request has none, or the out-of-band prompt does not
    extend their replay (a forced prompt). When the first one does not lead
    (a session recorded before system messages existed) there is no leading
    prompt and every system message is a later one, as in Pi; the
    out-of-band tail (pipy's compaction summary) is then the whole leading
    text.
    """

    collapsed = ResolvedTranscript(
        leading_text=request.system_prompt,
        items=tuple(request.messages),
        mid_convo=False,
    )
    anchored = request.system_messages
    if not supports_mid_convo or not anchored:
        return collapsed
    messages = tuple(anchor.message for anchor in anchored)
    replay = current_system_message(messages)
    replay_text = system_message_text(replay) if replay is not None else ""
    if not request.system_prompt.startswith(replay_text):
        return collapsed
    tail = request.system_prompt[len(replay_text) :]
    visible = _without_hidden_declarations(messages, request.hidden_tool_names)
    has_leading = anchored[0].position == 0
    first_later = 1 if has_leading else 0
    items: list[TranscriptItem] = []
    later = iter(zip(anchored[first_later:], visible[first_later:], strict=True))
    pending = next(later, None)
    for index, message in enumerate(request.messages):
        while pending is not None and pending[0].position <= index:
            items.append(pending[1])
            pending = next(later, None)
        items.append(message)
    while pending is not None:
        items.append(pending[1])
        pending = next(later, None)
    return ResolvedTranscript(
        leading_text=(
            system_message_text(visible[0]) + tail
            if has_leading
            else tail.removeprefix("\n\n")
        ),
        items=tuple(items),
        mid_convo=True,
        system_messages=visible,
        has_leading=has_leading,
    )


def _without_hidden_declarations(
    messages: tuple[AgentSystemMessage, ...],
    hidden_tool_names: Sequence[str],
) -> tuple[AgentSystemMessage, ...]:
    """Pi ``_installHiddenDeclarationsProjection``.

    Tools a request hook withheld are removed from every message's additions
    and removals, so the projection stays consistent across requests.
    Ordinary removals and their declarations stay.
    """

    hidden = set(hidden_tool_names)
    if not hidden:
        return messages
    return tuple(
        replace(
            message,
            tools_added=tuple(
                tool for tool in message.tools_added if tool.name not in hidden
            ),
            tools_removed=tuple(
                name for name in message.tools_removed if name not in hidden
            ),
        )
        for message in messages
    )


def declared_tools(
    messages: Sequence[AgentSystemMessage],
) -> tuple[AgentToolDeclaration, ...]:
    """Pi ``getDeclaredTools``: every declaration, first-declaration order."""

    definitions: dict[str, AgentToolDeclaration] = {}
    for message in messages:
        for tool in message.tools_added:
            definitions[tool.name] = tool
    return tuple(definitions.values())


def has_tool_redefinitions(messages: Sequence[AgentSystemMessage]) -> bool:
    """Pi ``hasToolRedefinitions``: one name declared with two definitions."""

    declared: dict[str, AgentToolDeclaration] = {}
    for message in messages:
        for tool in message.tools_added:
            previous = declared.get(tool.name)
            if previous is not None and previous != tool:
                return True
            declared[tool.name] = tool
    return False


def has_non_additive_tool_changes(messages: Sequence[AgentSystemMessage]) -> bool:
    """Pi ``hasNonAdditiveToolChanges``: a removal or a repeated declaration."""

    declared: set[str] = set()
    for message in messages:
        if message.tools_removed:
            return True
        for tool in message.tools_added:
            if tool.name in declared:
                return True
            declared.add(tool.name)
    return False


def request_tools(
    resolved: ResolvedTranscript,
    request: ProviderRequest,
    *,
    supports_additions: bool,
) -> tuple[tuple[ToolDefinition, ...], bool]:
    """Pi ``resolveTranscriptTools``: top-level tools and ``anchorsAdditions``.

    When later messages can carry their own additions (and no tool was
    removed or redeclared), the top level keeps the initial tools and later
    ones load where they were declared. Otherwise it holds the current tools:
    the request's advertised set, as on the collapse path.
    """

    if not resolved.mid_convo:
        return tuple(request.available_tools), False
    anchors = supports_additions and not has_non_additive_tool_changes(
        resolved.system_messages
    )
    if not anchors:
        return tuple(request.available_tools), False
    return tuple(declaration_definition(tool) for tool in resolved.initial_tools), True


def declaration_definition(declaration: AgentToolDeclaration) -> ToolDefinition:
    """A transcript declaration as the tool definition adapters serialize."""

    return ToolDefinition(
        name=declaration.name,
        description=declaration.description,
        input_schema=json.loads(declaration.parameters_json),
    )
