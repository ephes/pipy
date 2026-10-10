"""Current-branch footer estimates through real coding-session resume."""

from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import Any, TextIO, cast

import pytest

from pipy_harness.native import chrome
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentMessageUsage,
    AgentStopReason,
    AgentSystemMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.usage import (
    AgentProviderUsageSample,
    AgentUsageAccumulator,
)
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.session_tree import NativeSessionTree


def _assistant(text: str, tokens: int = 0, stop: AgentStopReason | None = None):
    return AgentAssistantMessage(
        ProductContent(text),
        usage=AgentMessageUsage(total_tokens=tokens),
        stop_reason=stop,
    )


def _resume(tmp_path: Path, tree: NativeSessionTree, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    assert tree.path is not None
    reopened = NativeSessionTree.open(tree.path, strict=True)
    session = CodingSession(
        provider=FakeNativeProvider(supports_tool_calls=True), native_session=reopened
    )
    observed: list[chrome._ChromeFooterEffects] = []
    original = chrome._ChromeFooterEffects._footer_text

    def capture(
        self: chrome._ChromeFooterEffects,
        *,
        cwd: Path,
        error_stream: TextIO | None = None,
    ) -> str:
        observed.append(self)
        return original(self, cwd=cwd, error_stream=error_stream)

    with monkeypatch.context() as local:
        local.setattr(chrome._ChromeFooterEffects, "_footer_text", capture)
        session.run(
            workspace_root=tmp_path,
            input_stream=io.StringIO("/exit\n"),
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        )
    effects = observed[-1]
    return session, effects, reopened


def _fields(effects: chrome._ChromeFooterEffects, monkeypatch: pytest.MonkeyPatch):
    fields: list[chrome.BottomStatusFields] = []

    def capture(width: int, value: chrome.BottomStatusFields) -> str:
        del width
        fields.append(value)
        return "footer"

    monkeypatch.setattr(chrome, "format_bottom_status_line", capture)
    effects.coding_footer_text()
    return fields[-1]


def _tokens(effects: chrome._ChromeFooterEffects, monkeypatch: pytest.MonkeyPatch):
    last = _fields(effects, monkeypatch)
    budget = chrome._context_budget_for(last.provider_name, last.model_id)
    return last.context_used_pct * budget.token_budget / 100


def test_footer_durable_resume_counts_individually_rounded_trailing_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = NativeSessionTree.create(tmp_path, state_root=tmp_path / "state")
    tree.append_message(_assistant("anchor", 1000))
    tree.append_message(AgentUserMessage(ProductContent("x")))
    tree.append_message(_assistant("y"))
    _, effects, _ = _resume(tmp_path, tree, monkeypatch)
    assert _tokens(effects, monkeypatch) == pytest.approx(1002)


def test_footer_active_branch_ignores_lifetime_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = NativeSessionTree.create(tmp_path, state_root=tmp_path / "state")
    first = tree.append_message(_assistant("first", 1000))
    tree.append_message(_assistant("other branch", 9000))
    session, effects, reopened = _resume(tmp_path, tree, monkeypatch)
    session._coding_state.absorb_usage(
        AgentProviderUsageSample(total_tokens=12345, input_tokens=12345)
    )
    before = session._coding_state.usage_snapshot()
    assert before.last_total_tokens == 12345
    reopened.branch(first.id)
    context = reopened.build_coding_context()
    session._coding_state.rebuild_history(context.messages)
    assert _tokens(effects, monkeypatch) == pytest.approx(1000)
    assert session._coding_state.usage_snapshot() == before


@pytest.mark.parametrize("stop", [AgentStopReason.ERROR, AgentStopReason.ABORTED])
def test_footer_stopped_usage_is_not_an_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop: AgentStopReason
) -> None:
    tree = NativeSessionTree.create(tmp_path, state_root=tmp_path / "state")
    tree.append_message(_assistant("anchor", 1000))
    tree.append_message(_assistant("x", 99000, stop))
    _, effects, _ = _resume(tmp_path, tree, monkeypatch)
    assert _tokens(effects, monkeypatch) == pytest.approx(1001)


def test_footer_same_history_model_switch_changes_only_denominator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = NativeSessionTree.create(tmp_path, state_root=tmp_path / "state")
    tree.append_message(_assistant("anchor", 1000))
    session, effects, _ = _resume(tmp_path, tree, monkeypatch)
    history = session._coding_state.messages
    before = _fields(effects, monkeypatch)
    before_budget = chrome._context_budget_for(before.provider_name, before.model_id)
    assert before.context_used_pct == pytest.approx(
        100 * 1000 / before_budget.token_budget
    )
    session._coding_state.rebind_provider(
        session.provider_port,
        provider_name="openai-codex",
        model_id="gpt-5.5",
        usage_accumulator=AgentUsageAccumulator(),
    )
    session._coding_state.rebuild_history(history)
    after = _fields(effects, monkeypatch)
    after_budget = chrome._context_budget_for(after.provider_name, after.model_id)
    assert after_budget.token_budget != before_budget.token_budget
    assert after.context_used_pct == pytest.approx(
        100 * 1000 / after_budget.token_budget
    )


def test_footer_compaction_counts_system_summary_once_and_distrusts_old_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = NativeSessionTree.create(tmp_path, state_root=tmp_path / "state")
    tree.append_message(AgentSystemMessage(sections=(("base", "abcd"),)))
    kept = tree.append_message(AgentUserMessage(ProductContent("x")))
    tree.append_message(_assistant("y", 9000))
    tree.append_compaction(
        summary="abcde", first_kept_entry_id=kept.id, tokens_before=9000
    )
    _, effects, _ = _resume(tmp_path, tree, monkeypatch)
    # Retained user and assistant each round to one; system rounds to one.
    # Summary suffix includes the existing provider-visible wrapper.
    suffix = effects.coding_state.compaction_suffix
    assert suffix
    assert _tokens(effects, monkeypatch) == pytest.approx(3 + -(-len(suffix) // 4))
    effects.coding_state.append_message(_assistant("z", 8000))
    assert _tokens(effects, monkeypatch) == pytest.approx(4 + -(-len(suffix) // 4))


def test_footer_without_anchor_estimates_system_and_actual_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = NativeSessionTree.create(tmp_path, state_root=tmp_path / "state")
    tree.append_message(AgentSystemMessage(sections=(("base", "abcde"),)))
    tree.append_message(AgentUserMessage(ProductContent("x")))
    tree.append_message(_assistant("abcde"))
    _, effects, _ = _resume(tmp_path, tree, monkeypatch)
    assert _tokens(effects, monkeypatch) == pytest.approx(5)


@pytest.mark.parametrize("stale_active_run", [False, True])
def test_footer_is_readable_with_unavailable_provider_or_stale_active_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stale_active_run: bool
) -> None:
    from pipy_harness.native.repl_state import UnavailableAfterReloadProvider

    tree = NativeSessionTree.create(tmp_path, state_root=tmp_path / "state")
    tree.append_message(AgentSystemMessage(sections=(("base", "abcde"),)))
    session, effects, _ = _resume(tmp_path, tree, monkeypatch)
    witness = session._coding_state.begin_agent_run() if stale_active_run else None
    session._coding_state.mark_provider_unavailable(
        UnavailableAfterReloadProvider("fake", "fake-native-bootstrap", "unavailable")
    )
    try:
        assert _tokens(effects, monkeypatch) == pytest.approx(2)
    finally:
        if witness is not None:
            session._coding_state.end_agent_run(witness)


def test_production_footer_two_lock_capture_does_not_deadlock(tmp_path: Path) -> None:
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(
        """
        import io
        import os
        import sys
        import threading
        import traceback
        from pathlib import Path
        from pipy_harness.native.chrome import _ChromeFooterEffects
        from pipy_harness.native.coding.session import CodingSession
        from pipy_harness.native.coding.state import CodingSessionState
        from pipy_harness.native.fake import FakeNativeProvider
        from pipy_harness.native.session_tree import NativeSessionTree

        root = Path(sys.argv[1])
        os.environ['PIPY_CONFIG_HOME'] = str(root / 'config')
        observed = []
        render = _ChromeFooterEffects._footer_text
        def capture(self, **kwargs):
            observed.append(self)
            return render(self, **kwargs)
        _ChromeFooterEffects._footer_text = capture
        tree = NativeSessionTree.create(root, state_root=root / 'state')
        session = CodingSession(provider=FakeNativeProvider(supports_tool_calls=True),
                                native_session=tree)
        session.run(workspace_root=root, input_stream=io.StringIO('/exit\\n'),
                    output_stream=io.StringIO(), error_stream=io.StringIO())
        footer = observed[-1]
        state = session._coding_state
        outer = tree.mutation_lock
        assert outer is not state.state_lock
        outer_taken = threading.Event()
        snapshot_entered = threading.Event()
        snapshot = CodingSessionState.result_snapshot
        def gated_snapshot(self):
            snapshot_entered.set()
            return snapshot(self)
        CodingSessionState.result_snapshot = gated_snapshot
        def mutate():
            with outer:
                outer_taken.set()
                snapshot_entered.wait(0.5)
                state.provider_binding
        errors = []
        def guarded(function):
            try:
                function()
            except BaseException as exc:
                errors.append(traceback.format_exc())
        writer = threading.Thread(target=lambda: guarded(mutate), daemon=True)
        writer.start()
        assert outer_taken.wait(1)
        reader = threading.Thread(target=lambda: guarded(footer.coding_footer_text), daemon=True)
        reader.start()
        writer.join(1)
        reader.join(1)
        if errors:
            print('worker error (exit 3):\\n' + '\\n'.join(errors), file=sys.stderr)
            raise SystemExit(3)
        if writer.is_alive() or reader.is_alive():
            print('deadlock (exit 2): writer_alive=' + str(writer.is_alive()) +
                  ', reader_alive=' + str(reader.is_alive()), file=sys.stderr)
            raise SystemExit(2)
        raise SystemExit(0)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, (
        f"exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_production_footer_tree_pointer_and_context_capture_are_coherent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipy_harness.native.coding.state import CodingSessionState

    tree = NativeSessionTree.create(tmp_path, state_root=tmp_path / "state")
    tree.append_message(_assistant("anchor", 1000))
    session, effects, reopened = _resume(tmp_path, tree, monkeypatch)
    control = cast(Any, effects.session_tree_section).__self__
    assert control.session_tree is reopened
    assert reopened.mutation_lock is not session._coding_state.state_lock
    replacement = NativeSessionTree.create(
        tmp_path, state_root=tmp_path / "other-state"
    )
    replacement.append_message(AgentSystemMessage(sections=(("base", "abcde"),)))
    replacement.append_message(AgentUserMessage(ProductContent("x")))
    replacement.append_message(_assistant("abcde"))
    captured, release, writer_started, pointer_changed, writer_done = (
        threading.Event() for _ in range(5)
    )
    original = CodingSessionState.result_snapshot

    def snapshot(owner: CodingSessionState):
        value = original(owner)
        if threading.current_thread().name == "footer-capture":
            captured.set()
            assert release.wait(2)
        return value

    monkeypatch.setattr(CodingSessionState, "result_snapshot", snapshot)
    errors: list[BaseException] = []
    values: list[float] = []

    def read() -> None:
        try:
            values.append(_tokens(effects, monkeypatch))
        except BaseException as exc:  # noqa: BLE001 - surface every worker failure
            errors.append(exc)

    def replace_context() -> None:
        try:
            writer_started.set()
            with control.session_tree_section():
                control.session_tree = replacement
                pointer_changed.set()
                session._coding_state.rebuild_history(
                    replacement.build_coding_context().messages
                )
            writer_done.set()
        except BaseException as exc:  # noqa: BLE001 - surface every worker failure
            errors.append(exc)

    reader = threading.Thread(target=read, name="footer-capture", daemon=True)
    writer = threading.Thread(target=replace_context, daemon=True)
    reader.start()
    try:
        assert captured.wait(2)
        writer.start()
        assert writer_started.wait(2)
        assert not pointer_changed.wait(0.1)
        assert not writer_done.is_set()
    finally:
        release.set()
        reader.join(2)
        if writer.ident is not None:
            writer.join(2)
    assert not reader.is_alive() and not writer.is_alive()
    assert not errors
    assert pointer_changed.is_set()
    assert writer_done.is_set()
    assert values == pytest.approx([1000])
    assert _tokens(effects, monkeypatch) == pytest.approx(5)
