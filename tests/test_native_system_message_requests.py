"""How transcript system messages reach a provider request (SYS1b).

Covers the session tree's anchors (Pi's session projection), the product's
request view (``request_system_messages``), snapshot rules (a forced prompt,
hook-hidden tools), re-anchoring when stopped turns are dropped, the request
budget, and catalog compat resolution.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentStopReason,
    AgentSystemMessage,
    AgentToolDeclaration,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.request import (
    freeze_provider_request,
    snapshot_provider_request,
)
from pipy_harness.native.agent.system_messages import (
    replayed_system_prompt,
    request_system_messages,
    tool_declaration,
)
from pipy_harness.native.agent_loop_policy import materialize_provider_request
from pipy_harness.native.catalog_data import BUILTIN_MODEL_ROWS
from pipy_harness.native.coding.request_budget import estimate_request
from pipy_harness.native.models import ProviderRequest, ProviderSystemMessage
from pipy_harness.native.provider_construction import (
    TranscriptCompat,
    resolve_transcript_compat,
)
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tools import ToolDefinition

_READ = AgentToolDeclaration("read", "Read a file.", '{"type":"object"}')
_GREP = AgentToolDeclaration("grep", "Search.", '{"type":"object"}')


def _user(text: str) -> AgentUserMessage:
    return AgentUserMessage(ProductContent(text))


def _assistant(text: str) -> AgentAssistantMessage:
    return AgentAssistantMessage(ProductContent(text))


def _tree(tmp_path: Path) -> NativeSessionTree:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    return NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")


# ---- session tree anchors -----------------------------------------------------


def test_coding_context_anchors_system_messages_before_their_next_message(
    tmp_path: Path,
) -> None:
    tree = _tree(tmp_path)
    leading = AgentSystemMessage(sections=(("preamble", "p"),), tools_added=(_READ,))
    later = AgentSystemMessage(tools_added=(_GREP,))
    tree.append_message(leading)
    tree.append_message(_user("u1"))
    tree.append_message(_assistant("a1"))
    tree.append_message(later)
    tree.append_message(_user("u2"))

    context = tree.build_coding_context()

    assert [m.content.value for m in context.messages] == ["u1", "a1", "u2"]
    assert context.system_anchors == ((0, leading), (2, later))


def test_compaction_checkpoint_leads_and_replaced_entries_are_dropped(
    tmp_path: Path,
) -> None:
    tree = _tree(tmp_path)
    tree.append_message(
        AgentSystemMessage(sections=(("preamble", "one"),), tools_added=(_READ,))
    )
    first = tree.append_message(_user("first"))
    tree.append_message(_assistant("a1"))
    tree.append_message(AgentSystemMessage(tools_added=(_GREP,)))
    tree.append_message(_user("second"))
    tree.append_compaction(
        summary="summary", first_kept_entry_id=first.id, tokens_before=10
    )
    after = AgentSystemMessage(sections=(("preamble", "two"),))
    tree.append_message(after)
    tree.append_message(_user("third"))

    context = tree.build_coding_context()
    checkpoint = AgentSystemMessage(
        sections=(("preamble", "one"),), tools_added=(_READ, _GREP)
    )

    assert [m.content.value for m in context.messages] == [
        "first",
        "a1",
        "second",
        "third",
    ]
    assert context.system_anchors == ((0, checkpoint), (3, after))


# ---- request view --------------------------------------------------------------


def test_first_turn_message_goes_before_the_accepted_message_with_overlay() -> None:
    persisted = (_user("u1"), _assistant("a1"))
    leading = AgentSystemMessage(sections=(("preamble", "p"),))
    turn = AgentSystemMessage(tools_added=(_GREP,))
    accepted = _user("u2")
    active = AgentActiveInput(accepted, (_user("ctx"),), turn_system_message=turn)

    anchored = request_system_messages(
        (*persisted, accepted), persisted, ((0, leading),), active, 0
    )

    assert anchored == (
        ProviderSystemMessage(0, leading),
        ProviderSystemMessage(2, turn),
    )


def test_later_turn_message_goes_after_the_history_in_the_request_frame() -> None:
    leading = AgentSystemMessage(sections=(("preamble", "p"),))
    first_turn = AgentSystemMessage(sections=(("preamble", "q"),))
    turn = AgentSystemMessage(tools_added=(_GREP,))
    accepted = _user("u2")
    history = (_user("u1"), accepted, _assistant("a2"))
    active = AgentActiveInput(accepted, (_user("ctx"),), turn_system_message=turn)

    anchored = request_system_messages(
        history, history, ((0, leading), (1, first_turn), (3, leading)), active, 1
    )

    # Positions after the accepted message shift by the one overlay message.
    assert [anchor.position for anchor in anchored] == [0, 1, 4, 4]
    assert anchored[-1].message is turn


def test_collapsed_prompt_replays_sections_in_pi_order() -> None:
    leading = AgentSystemMessage(
        sections=(("preamble", "P"), ("cwd", "<cwd>\n/w\n</cwd>"))
    )
    added = AgentSystemMessage(sections=(("addendum", "<addendum>\nA\n</addendum>"),))
    anchored = (ProviderSystemMessage(0, leading), ProviderSystemMessage(2, added))

    # Pi's getCurrentSystemMessage appends a newly added section after the
    # ones the model already had, unlike a freshly built prompt.
    assert replayed_system_prompt(anchored, "fallback") == (
        "P\n\n<cwd>\n/w\n</cwd>\n\n<addendum>\nA\n</addendum>"
    )
    assert replayed_system_prompt((), "fallback") == "fallback"


def test_a_history_the_session_does_not_extend_gets_no_system_messages() -> None:
    accepted = _user("u2")
    active = AgentActiveInput(accepted)
    anchored = request_system_messages(
        (_user("other"), accepted),
        (_user("u1"),),
        ((0, AgentSystemMessage(sections=(("preamble", "p"),))),),
        active,
        0,
    )
    assert anchored == ()


# ---- snapshot and materialization ------------------------------------------


def _tool(name: str) -> ToolDefinition:
    return ToolDefinition(name, f"{name} tool", {"type": "object"})


def _request(**fields: object) -> ProviderRequest:
    leading = AgentSystemMessage(
        sections=(("preamble", "p"),),
        tools_added=(tool_declaration(_tool("alpha")), tool_declaration(_tool("beta"))),
    )
    base = ProviderRequest(
        system_prompt="p",
        user_prompt="u",
        provider_name="test",
        model_id="m",
        cwd=Path("."),
        messages=(_user("u"),),
        available_tools=(_tool("alpha"), _tool("beta")),
        system_messages=(ProviderSystemMessage(0, leading),),
    )
    return replace(base, **fields)  # type: ignore[arg-type]


def test_a_replaced_prompt_is_forced_and_drops_the_system_messages() -> None:
    request = _request()

    kept = snapshot_provider_request(request)
    forced = snapshot_provider_request(request, system_prompt="forced")

    assert kept.request.system_messages == request.system_messages
    assert forced.request.system_messages == ()


def test_a_narrowing_hook_records_the_hidden_tools() -> None:
    snapshot = snapshot_provider_request(_request(), available_tool_names=("alpha",))

    assert snapshot.request.hidden_tool_names == ("beta",)
    assert [tool.name for tool in snapshot.request.available_tools] == ["alpha"]


def test_dropping_stopped_turns_reanchors_middle_and_trailing_messages() -> None:
    stopped = replace(_assistant("partial"), stop_reason=AgentStopReason.ABORTED)
    middle = AgentSystemMessage(sections=(("preamble", "q"),))
    trailing = AgentSystemMessage(tools_added=(tool_declaration(_tool("gamma")),))
    request = _request(
        messages=(_user("u1"), stopped, _user("u2")),
        system_messages=(
            ProviderSystemMessage(0, AgentSystemMessage(sections=(("preamble", "p"),))),
            ProviderSystemMessage(2, middle),
            ProviderSystemMessage(3, trailing),
        ),
    )

    materialized = materialize_provider_request(snapshot_provider_request(request))

    assert [m.content.value for m in materialized.messages] == ["u1", "u2"]
    assert [anchor.position for anchor in materialized.system_messages] == [0, 1, 2]


# ---- budget ----------------------------------------------------------------------


def _estimate(request: ProviderRequest) -> int:
    return estimate_request(
        freeze_provider_request(request), image_count=0, output_reserve=0
    ).input_tokens


def test_collapse_path_estimate_is_unchanged_without_the_carrier() -> None:
    plain = freeze_provider_request(
        _request(system_messages=(), system_prompt="p" * 30)
    )
    estimate = estimate_request(plain, image_count=0, output_reserve=0)

    # The pre-SYS1b accounting: the prompt's own tokens and no extra framing.
    assert estimate.system_tokens == (30 + 2) // 3
    assert estimate.framing_tokens == 32 + 8 * (1 + 2)


def test_repeated_section_updates_and_removed_tools_raise_the_estimate() -> None:
    base = _request()
    big = "x" * 3000
    updates = tuple(
        ProviderSystemMessage(1, AgentSystemMessage(sections=(("notes", big),)))
        for _ in range(3)
    )
    with_updates = replace(
        base,
        system_prompt="p\n\n" + big,
        system_messages=base.system_messages + updates,
    )
    assert _estimate(with_updates) >= _estimate(base) + 3 * 1000

    large_schema = '{"type":"object","description":"' + "y" * 6000 + '"}'
    large = AgentToolDeclaration("large", "large tool", large_schema)
    leading = AgentSystemMessage(sections=(("preamble", "p"),), tools_added=(large,))
    removed = replace(
        base,
        available_tools=(),
        system_messages=(
            ProviderSystemMessage(0, leading),
            ProviderSystemMessage(1, AgentSystemMessage(tools_removed=("large",))),
        ),
    )
    assert _estimate(removed) >= _estimate(replace(removed, system_messages=())) + 2000


# ---- catalog compat --------------------------------------------------------------


def _row(provider: str, model_id: str):  # type: ignore[no-untyped-def]
    return next(
        row
        for row in BUILTIN_MODEL_ROWS
        if row.provider_name == provider and row.model_id == model_id
    )


def test_catalog_rows_resolve_pi_compat_per_family() -> None:
    assert resolve_transcript_compat(_row("openai-codex", "gpt-6.1-sol")) == (
        TranscriptCompat(
            supports_mid_convo_system_messages=True,
            supports_additional_tools=True,
            instruction_role="developer",
        )
    )
    assert resolve_transcript_compat(_row("openai-codex", "gpt-5.5")) == (
        TranscriptCompat(
            supports_mid_convo_system_messages=True, instruction_role="developer"
        )
    )
    assert resolve_transcript_compat(_row("anthropic", "claude-opus-5-5")) == (
        TranscriptCompat(
            supports_mid_convo_system_messages=True,
            supports_mid_convo_tool_changes=True,
            supports_mid_convo_effort=True,
        )
    )
    assert resolve_transcript_compat(_row("anthropic", "claude-opus-4-7")) == (
        TranscriptCompat()
    )
    assert resolve_transcript_compat(_row("openai-completions", "gpt-5.5")) == (
        TranscriptCompat(
            supports_mid_convo_system_messages=True, instruction_role="developer"
        )
    )


def test_completions_developer_role_follows_pi_detect_compat() -> None:
    kimi = _row("openrouter", "moonshotai/kimi-k2.6")
    assert resolve_transcript_compat(kimi).instruction_role == "system"
    detected = replace(kimi, compat=None, model_id="openai/gpt-x")
    assert resolve_transcript_compat(detected).instruction_role == "developer"
    other = replace(kimi, compat=None, model_id="meta/llama")
    assert resolve_transcript_compat(other).instruction_role == "system"
    deepseek = replace(
        kimi, provider_name="custom", base_url="https://API.DeepSeek.com", compat=None
    )
    assert resolve_transcript_compat(deepseek).instruction_role == "system"
    plain = replace(
        kimi, provider_name="custom", base_url="https://x.test", compat=None
    )
    assert resolve_transcript_compat(plain).instruction_role == "developer"
    not_reasoning = replace(plain, reasoning=False)
    assert resolve_transcript_compat(not_reasoning).instruction_role == "system"
