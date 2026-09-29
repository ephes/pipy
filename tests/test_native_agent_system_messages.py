"""Pi transcript system messages (`9e05370b2`): replay, diffs and loop emission."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import pytest
from test_native_agent_loop import (
    _TOOL,
    _make_loop,
    _provider_call,
    _provider_result,
    _run_input,
    _Tools,
)

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.events import (
    MessageCompleted,
    MessageStarted,
    TurnStarted,
)
from pipy_harness.native.agent.messages import (
    AgentSystemMessage,
    AgentToolDeclaration,
    AgentUserMessage,
)
from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.agent.system_messages import (
    AgentSystemPromptInput,
    current_system_message,
    current_tools,
    declare_tool_changes,
    diff_sections,
    system_message_from_json,
    system_message_text,
    system_message_to_json,
    system_prompt_input,
    tool_declaration,
    tool_state_changes,
)
from pipy_harness.native.automation.serialize import serialize_message
from pipy_harness.native.tools.base import ToolDefinition


def _decl(name: str, description: str = "d") -> AgentToolDeclaration:
    return AgentToolDeclaration(name, description, '{"type":"object"}')


def test_replay_patches_sections_and_resolves_tools_in_order() -> None:
    first = AgentSystemMessage(
        sections=(("preamble", "base"), ("cwd", "<cwd>\n/a\n</cwd>")),
        tools_added=(_decl("read"), _decl("bash")),
    )
    second = AgentSystemMessage(
        content=ProductContent("extra"),
        sections=(("cwd", None), ("skills", "<skills/>")),
        tools_removed=("read",),
        tools_added=(_decl("bash", "changed"), _decl("grep")),
    )
    user = AgentUserMessage(ProductContent("hi"))

    current = current_system_message((first, user, second))

    assert current is not None
    assert current.content.value == "extra"
    assert current.sections == (("preamble", "base"), ("skills", "<skills/>"))
    # Pi `Map.set` keeps an existing key's position.
    assert [tool.name for tool in current.tools_added] == ["bash", "grep"]
    assert current.tools_added[0].description == "changed"
    assert system_message_text(current) == "extra\n\nbase\n\n<skills/>"
    assert current_system_message((user,)) is None
    assert current_tools((first, second)) == current.tools_added


def test_diff_sections_matches_pi_diff_system_prompt_sections() -> None:
    previous = (("preamble", "a"), ("cwd", "x"))

    assert diff_sections(previous, (("preamble", "a"), ("cwd", "x"))) is None
    assert diff_sections(previous, (("preamble", "b"), ("cwd", "x"))) == (
        ("preamble", "b"),
    )
    assert diff_sections(previous, (("preamble", "a"),)) == (("cwd", None),)
    assert diff_sections((), (("preamble", "a"),)) == (("preamble", "a"),)


def test_tool_state_changes_treat_a_redefinition_as_remove_and_add() -> None:
    added, removed = tool_state_changes(
        (_decl("read"), _decl("bash")), (_decl("bash", "new"), _decl("grep"))
    )

    assert [tool.name for tool in added] == ["bash", "grep"]
    assert removed == ("read", "bash")


def test_declare_tool_changes_merges_into_pending_or_creates_a_tools_only_message() -> (
    None
):
    pending = AgentSystemMessage(
        sections=(("preamble", "p"),), tools_added=(_decl("stale"),)
    )

    merged = declare_tool_changes((), pending, (_decl("read"),))
    assert merged is not None
    assert merged.sections == pending.sections
    assert merged.tools_added == (_decl("read"),)
    assert merged.tools_removed == ()

    unchanged = declare_tool_changes((_decl("read"),), pending, (_decl("read"),))
    assert unchanged is not None
    assert unchanged.tools_added == () and unchanged.tools_removed == ()

    assert declare_tool_changes((_decl("read"),), None, (_decl("read"),)) is None
    only_tools = declare_tool_changes((_decl("read"),), None, (_decl("grep"),))
    assert only_tools == AgentSystemMessage(
        tools_added=(_decl("grep"),), tools_removed=("read",)
    )


def test_system_prompt_input_diffs_against_the_transcript_replay() -> None:
    fresh = system_prompt_input((), (("preamble", "prompt"),))
    assert fresh.pending == AgentSystemMessage(sections=(("preamble", "prompt"),))
    assert fresh.declared_tools == ()

    stored = AgentSystemMessage(
        sections=(("preamble", "prompt"),), tools_added=(_decl("read"),)
    )
    same = system_prompt_input((stored,), (("preamble", "prompt"),))
    assert same.pending is None
    assert same.declared_tools == (_decl("read"),)

    changed = system_prompt_input((stored,), (("preamble", "new"),))
    assert changed.pending == AgentSystemMessage(sections=(("preamble", "new"),))


def test_json_round_trip_uses_pi_field_names_and_omits_empty_fields() -> None:
    message = AgentSystemMessage(
        sections=(("preamble", "p"), ("cwd", None)),
        tools_added=(tool_declaration(_TOOL),),
        tools_removed=("gone",),
    )

    body = system_message_to_json(message)

    assert list(body) == ["role", "content", "sections", "toolsAdded", "toolsRemoved"]
    assert body["sections"] == {"preamble": "p", "cwd": None}
    assert body["toolsAdded"] == [
        {
            "name": "fixture",
            "description": "Headless fixture tool.",
            "parameters": dict(_TOOL.input_schema),
        }
    ]
    assert body["toolsRemoved"] == [{"name": "gone"}]
    assert system_message_from_json(body) == message
    assert serialize_message(message) == body
    assert system_message_to_json(AgentSystemMessage()) == {
        "role": "system",
        "content": "",
    }
    # Pi `sessionEntryToContextMessages`: missing content loads as "".
    assert system_message_from_json({"role": "system"}) == AgentSystemMessage()
    assert system_message_from_json(
        {"role": "system", "content": [{"type": "text", "text": "a"}]}
    ).content == ProductContent("a")
    with pytest.raises(ValueError):
        system_message_from_json({"role": "system", "toolsAdded": ["read"]})
    with pytest.raises(ValueError):
        system_message_from_json({"role": "system", "sections": {"a": 1}})


def _system_events(events: Sequence[object]) -> list[tuple[str, int]]:
    return [
        (type(event).__name__, event.turn_index)
        for event in events
        if isinstance(event, (MessageStarted, MessageCompleted))
        and isinstance(event.message, AgentSystemMessage)
    ]


def test_loop_emits_the_leading_system_message_before_the_user_message() -> None:
    order: list[str] = []
    loop, _provider, events, _usage = _make_loop(
        order, [ProviderTurnOutcome(result=_provider_result())]
    )
    pending = AgentSystemMessage(sections=(("preamble", "prompt"),))
    run_input = _run_input()
    outcome = loop.run(
        replace(
            run_input,
            system_prompt=AgentSystemPromptInput(pending=pending, declared_tools=()),
        )
    )

    names = [type(event).__name__ for event in events.events]
    assert names[:6] == [
        "AgentRunStarted",
        "TurnStarted",
        "MessageStarted",
        "MessageCompleted",
        "MessageStarted",
        "MessageCompleted",
    ]
    leading = outcome.result.messages[0]
    assert leading == AgentSystemMessage(
        sections=(("preamble", "prompt"),), tools_added=(tool_declaration(_TOOL),)
    )
    assert isinstance(events.events[2], MessageStarted)
    assert events.events[2].message is leading
    assert isinstance(outcome.result.messages[1], AgentUserMessage)
    # Transcript state only: never provider history.
    assert all(
        not isinstance(message, AgentSystemMessage) for message in outcome.final_history
    )


def test_loop_emits_nothing_when_prompt_and_tools_are_unchanged() -> None:
    order: list[str] = []
    loop, _provider, events, _usage = _make_loop(
        order, [ProviderTurnOutcome(result=_provider_result())]
    )
    outcome = loop.run(
        replace(
            _run_input(),
            system_prompt=AgentSystemPromptInput(
                pending=None, declared_tools=(tool_declaration(_TOOL),)
            ),
        )
    )

    assert _system_events(events.events) == []
    assert not any(isinstance(m, AgentSystemMessage) for m in outcome.result.messages)


def test_loop_without_system_input_keeps_the_old_sequence() -> None:
    order: list[str] = []
    loop, _provider, events, _usage = _make_loop(
        order, [ProviderTurnOutcome(result=_provider_result())]
    )

    loop.run(_run_input())

    assert _system_events(events.events) == []


def test_a_tool_change_between_turns_emits_a_tools_only_system_message() -> None:
    order: list[str] = []
    added = ToolDefinition(
        name="loaded",
        description="Loaded mid-run.",
        input_schema={"type": "object", "properties": {}, "required": []},
    )

    class GrowingTools(_Tools):
        def definitions(self, allowed_names=None, /):  # type: ignore[no-untyped-def]
            base = super().definitions(allowed_names)
            return base if not self.executed else (*base, added)

    call = _provider_call("provider-1")
    loop, _provider, events, _usage = _make_loop(
        order,
        [
            ProviderTurnOutcome(result=_provider_result("calling", calls=(call,))),
            ProviderTurnOutcome(result=_provider_result("finished")),
        ],
        tools=GrowingTools(order),
    )
    outcome = loop.run(
        replace(
            _run_input(),
            system_prompt=AgentSystemPromptInput(
                pending=None, declared_tools=(tool_declaration(_TOOL),)
            ),
        )
    )

    assert _system_events(events.events) == [
        ("MessageStarted", 1),
        ("MessageCompleted", 1),
    ]
    turn_one = next(
        index
        for index, event in enumerate(events.events)
        if isinstance(event, TurnStarted) and event.turn_index == 1
    )
    assert isinstance(events.events[turn_one + 1], MessageStarted)
    update = outcome.result.messages[-2]
    assert update == AgentSystemMessage(tools_added=(tool_declaration(added),))
    roles = [type(message).__name__ for message in outcome.result.messages]
    assert roles == [
        "AgentUserMessage",
        "AgentAssistantMessage",
        "AgentToolResultMessage",
        "AgentSystemMessage",
        "AgentAssistantMessage",
    ]


def test_system_message_validation_fails_closed() -> None:
    with pytest.raises(ValueError):
        AgentSystemMessage(sections=(("a", "x"), ("a", "y")))
    with pytest.raises(TypeError):
        AgentSystemMessage(sections=(("a", 1),))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        AgentSystemMessage(tools_added=("read",))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AgentSystemMessage(tools_removed=("",))
    with pytest.raises(TypeError):
        AgentSystemPromptInput(pending=None, declared_tools=("read",))  # type: ignore[arg-type]
