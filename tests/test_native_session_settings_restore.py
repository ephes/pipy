"""Recording and restoring a session's model and thinking level (DF1-F3).

Pi ``createAgentSession`` (``core/sdk.ts:194-263``, ``:427-437``) restores an
opened session's model from its last ``model_change`` (unless the CLI pinned
one) and its thinking level from its last ``thinking_level_change`` (unless
the CLI gave ``--thinking``), and records both for a new session. Model
switches append ``model_change`` (``AgentSession.setModel``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_native_coding_effects import _provider_mutation_fixture

from pipy_harness import cli
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.repl_state import NativeModelSelection
from pipy_harness.native.session_settings import (
    resolve_session_settings,
    session_model,
)
from pipy_harness.native.session_tree import (
    ModelChangeEntry,
    NativeSessionTree,
    SessionEntry,
    ThinkingLevelChangeEntry,
)
from pipy_harness.native.settings import SettingsManager

SOL = NativeModelSelection("openai", "gpt-5.5")
CODEX = NativeModelSelection("openai", "gpt-5.1-codex")
ORPHAN = NativeModelSelection("nowhere", "gone")


def _settings(tmp_path: Path, default_thinking: str | None = None) -> SettingsManager:
    path = tmp_path / "config" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = SettingsManager(global_path=path, env={})
    if default_thinking is not None:
        settings.set_value("defaultThinkingLevel", default_thinking)
    return settings


def _entry_kinds(tree: NativeSessionTree) -> list[tuple[str, ...]]:
    kinds: list[tuple[str, ...]] = []
    for entry in tree.get_entries():
        if isinstance(entry, ModelChangeEntry):
            kinds.append(("model", entry.provider, entry.model_id))
        elif isinstance(entry, ThinkingLevelChangeEntry):
            kinds.append(("thinking", entry.thinking_level))
        else:
            kinds.append((entry.type,))
    return kinds


def _converse(tree: NativeSessionTree) -> None:
    tree.append_message(AgentUserMessage(ProductContent("hi")))
    tree.append_message(AgentAssistantMessage(ProductContent("hello")))


# -- the pure decision ----------------------------------------------------------


def _decide(branch: list[SessionEntry], **overrides: Any) -> Any:
    arguments: dict[str, Any] = {
        "has_messages": True,
        "cli_selection": None,
        "cli_thinking": None,
        "fallback_selection": SOL,
        "current_thinking": "high",
        "default_thinking": "medium",
        "usable": lambda _selection: True,
    }
    arguments.update(overrides)
    return resolve_session_settings(branch, **arguments)


def _branch(tmp_path: Path, *, model: bool = True, level: str | None = "low") -> list:
    tree = NativeSessionTree.create(tmp_path, persist=False)
    if model:
        tree.append_model_change("openai", "gpt-5.2")
        tree.append_model_change(CODEX.provider_name, CODEX.model_id)
    if level is not None:
        tree.append_thinking_level_change("xhigh")
        tree.append_thinking_level_change(level)
    _converse(tree)
    return tree.get_branch()


def test_existing_session_restores_its_latest_model_and_level(tmp_path: Path) -> None:
    decision = _decide(_branch(tmp_path))
    assert decision.selection == CODEX
    assert decision.thinking_level == "low"
    assert decision.fallback_message is None
    assert decision.record_thinking is False


def test_cli_model_and_thinking_win_over_the_session(tmp_path: Path) -> None:
    decision = _decide(_branch(tmp_path), cli_selection=SOL, cli_thinking="minimal")
    assert (decision.selection, decision.thinking_level) == (SOL, "minimal")


def test_unusable_session_model_falls_back_with_pi_message(tmp_path: Path) -> None:
    decision = _decide(_branch(tmp_path), usable=lambda selection: selection != CODEX)
    assert decision.selection == SOL
    assert decision.fallback_message == (
        "Could not restore model openai/gpt-5.1-codex. Using openai/gpt-5.5"
    )
    # The level is still the session's.
    assert decision.thinking_level == "low"


def test_session_without_entries_uses_fallback_and_default_level(
    tmp_path: Path,
) -> None:
    decision = _decide(_branch(tmp_path, model=False, level=None))
    assert decision.selection == SOL
    assert decision.fallback_message is None
    assert decision.thinking_level == "medium"
    assert decision.record_thinking is True


def test_empty_session_keeps_the_live_values(tmp_path: Path) -> None:
    decision = _decide([], has_messages=False)
    assert (decision.selection, decision.thinking_level) == (SOL, "high")
    # Recorded with the first message instead (record_session_start).
    assert decision.record_thinking is False
    pinned = _decide([], has_messages=False, cli_selection=CODEX, cli_thinking="low")
    assert (pinned.selection, pinned.thinking_level) == (CODEX, "low")


def test_session_model_reads_only_model_change_entries(tmp_path: Path) -> None:
    assert session_model(_branch(tmp_path, model=False)) is None
    assert session_model(_branch(tmp_path / "b")) == CODEX


# -- the runtime path (resume, fork, new) ---------------------------------------


def _fixture(tmp_path: Path, **kwargs: Any) -> tuple[Any, Any, NativeSessionTree, list]:
    settings = kwargs.pop("settings", None) or _settings(tmp_path)
    effects, state, _tools, _ref, _coordinator, tree, footers = (
        _provider_mutation_fixture(tmp_path, settings=settings, **kwargs)
    )
    return effects, state, tree, footers


def test_new_session_records_its_model_and_level_with_the_first_message(
    tmp_path: Path,
) -> None:
    effects, state, tree, _footers = _fixture(tmp_path)
    state.assign_thinking_level("low")

    effects.sync_session_settings()
    assert _entry_kinds(tree) == []

    effects.record_session_start()
    tree.append_message(AgentUserMessage(ProductContent("first")))
    effects.record_session_start()
    tree.append_message(AgentUserMessage(ProductContent("second")))

    assert _entry_kinds(tree) == [
        ("model", "openai", "gpt-5.5"),
        ("thinking", "low"),
        ("message",),
        ("message",),
    ]


def test_session_start_keeps_entries_made_before_the_first_message(
    tmp_path: Path,
) -> None:
    effects, _state, tree, _footers = _fixture(tmp_path)
    tree.append_thinking_level_change("high")

    effects.record_session_start()

    assert _entry_kinds(tree) == [
        ("thinking", "high"),
        ("model", "openai", "gpt-5.5"),
    ]


def test_resumed_session_switches_to_its_model_and_level(tmp_path: Path) -> None:
    effects, state, tree, footers = _fixture(tmp_path, persist_defaults=True)
    state.assign_thinking_level("high")
    tree.append_model_change(CODEX.provider_name, CODEX.model_id)
    tree.append_thinking_level_change("low")
    _converse(tree)
    before = _entry_kinds(tree)
    old_provider = effects.coding_state.provider

    effects.sync_session_settings()

    assert state.current_selection() == CODEX
    assert state.current_thinking_level() == "low"
    binding = effects.coding_state.provider_binding
    assert (binding.provider_name, binding.model_id) == ("openai", "gpt-5.1-codex")
    assert effects.coding_state.provider is not old_provider
    # Reopening is not choosing: no entries, no saved default.
    assert _entry_kinds(tree) == before
    assert state.pending_default_value() is None
    assert not (tmp_path / "defaults.json").exists()
    assert footers == ["footer"]


def test_resumed_session_without_thinking_entry_gets_default_level(
    tmp_path: Path,
) -> None:
    effects, state, tree, _footers = _fixture(
        tmp_path, settings=_settings(tmp_path, "minimal")
    )
    state.assign_thinking_level("high")
    tree.append_model_change("openai", "gpt-5.5")
    _converse(tree)

    effects.sync_session_settings()

    # The settings default, clamped to the model (gpt-5.5 maps minimal to
    # low), is applied and recorded (Pi sdk.ts:241-263, :427-430).
    assert state.current_thinking_level() == "low"
    assert _entry_kinds(tree)[-1] == ("thinking", "low")


def test_unrestorable_model_keeps_the_live_one(tmp_path: Path) -> None:
    effects, state, tree, footers = _fixture(tmp_path)
    tree.append_model_change(ORPHAN.provider_name, ORPHAN.model_id)
    tree.append_thinking_level_change("low")
    _converse(tree)

    effects.sync_session_settings()

    assert state.current_selection() == SOL
    # The level is still restored on the live model.
    assert state.current_thinking_level() == "low"
    assert effects.error_stream.getvalue() == ""


def test_cli_pinned_model_wins_on_resume(tmp_path: Path) -> None:
    effects, state, tree, _footers = _fixture(tmp_path)
    state.cli_selection = SOL
    tree.append_model_change(CODEX.provider_name, CODEX.model_id)
    tree.append_thinking_level_change("low")
    _converse(tree)

    effects.sync_session_settings()

    assert state.current_selection() == SOL
    assert state.current_thinking_level() == "low"


def test_model_switch_records_model_change(tmp_path: Path) -> None:
    effects, state, tree, _footers = _fixture(tmp_path)

    ok, _message = effects.apply_model_selection("openai/gpt-5.1-codex")

    assert ok
    assert ("model", "openai", "gpt-5.1-codex") in _entry_kinds(tree)


def test_model_switch_that_clamps_the_level_records_both(tmp_path: Path) -> None:
    effects, state, tree, _footers = _fixture(tmp_path)
    state.assign_thinking_level("xhigh")

    ok, _message = effects.apply_model_selection("openai/gpt-5.1-codex")

    assert ok
    clamped = state.current_thinking_level()
    assert clamped not in (None, "xhigh")
    # Pi setModel appends model_change, then setThinkingLevel the new level.
    assert _entry_kinds(tree) == [
        ("model", "openai", "gpt-5.1-codex"),
        ("thinking", clamped),
    ]


# -- startup (the CLI's createAgentSession analog) ------------------------------


def test_startup_restores_an_opened_sessions_model_and_level(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _effects, state, tree, _footers = _fixture(tmp_path)
    state.assign_thinking_level("medium")
    tree.append_model_change(CODEX.provider_name, CODEX.model_id)
    tree.append_thinking_level_change("low")
    _converse(tree)

    cli._restore_startup_session_settings(state, tree, _settings(tmp_path))

    assert state.selection == CODEX
    assert state.thinking_level == "low"
    assert capsys.readouterr().err == ""


def test_startup_warns_when_the_model_cannot_be_restored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _effects, state, tree, _footers = _fixture(tmp_path)
    tree.append_model_change(ORPHAN.provider_name, ORPHAN.model_id)
    _converse(tree)

    cli._restore_startup_session_settings(state, tree, _settings(tmp_path))

    assert state.selection == SOL
    assert state.thinking_level == "medium"
    assert capsys.readouterr().err == (
        "pipy: Could not restore model nowhere/gone. Using openai/gpt-5.5\n"
    )


def test_startup_leaves_a_new_session_alone(tmp_path: Path) -> None:
    _effects, state, tree, _footers = _fixture(tmp_path)
    state.assign_thinking_level("high")
    tree.append_model_change(CODEX.provider_name, CODEX.model_id)

    cli._restore_startup_session_settings(state, tree, _settings(tmp_path))

    assert (state.selection, state.thinking_level) == (SOL, "high")


def test_restorable_requires_a_catalog_row_and_auth(tmp_path: Path) -> None:
    _effects, state, _tree, _footers = _fixture(tmp_path)
    assert state.restorable(SOL)
    assert not state.restorable(ORPHAN)
    assert not state.restorable(NativeModelSelection("anthropic", "claude-opus-5-5"))
