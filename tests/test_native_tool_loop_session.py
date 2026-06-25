"""Slice 4 tests: `NativeToolReplSession` skeleton.

These tests pin the loop's behavior using a test-only `_FixtureTool` that
echoes its `text` argument back. The production tool registry stays empty;
the loop is exercised by injecting the fixture registry directly. Real
providers all advertise `supports_tool_calls=False` at this point, so the
session is also exercised against `FakeNativeProvider` with a programmable
script.
"""

from __future__ import annotations

import io
import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO, cast

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native import (
    FakeNativeProvider,
    NativeToolReplResult,
    NativeToolReplSession,
    ProviderRequest,
    ProviderResult,
    ProviderToolCall,
    production_tool_registry,
)
from pipy_harness.native.catalog_state import ProviderCatalogState
from pipy_harness.native.extension_provider_catalog import (
    extension_reserved_command_names,
    extension_reserved_tool_names,
    load_extension_provider_contributions,
)
from pipy_harness.native.tool_loop_session import _UsageAccumulator
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.repl_state import NativeModelSelection, NativeReplProviderState
from pipy_harness.native.session_resume import ResumeContext
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tui import TURN_ABORTED as _TURN_ABORTED
from pipy_harness.native.tui import ToolLoopTerminalUi
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolPort,
    ToolRequest,
)


@dataclass(frozen=True, slots=True)
class _FixtureEchoTool:
    """Test-only echo tool used to exercise the loop end-to-end."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="echo",
            description="Return the provided text verbatim.",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "maxLength": 1024},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        text = str(request.arguments["text"])
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=text,
            provider_correlation_id=request.provider_correlation_id,
        )


def _make_call(
    tool_name: str,
    arguments_json: str,
    *,
    correlation_id: str = "call_test_1",
) -> ProviderToolCall:
    return ProviderToolCall(
        provider_correlation_id=correlation_id,
        tool_name=tool_name,
        arguments_json=arguments_json,
    )


def _run_session(
    *,
    tool_calls_script: tuple[tuple[ProviderToolCall, ...], ...],
    tool_registry: Mapping[str, ToolPort] | None,
    user_inputs: tuple[str, ...],
    tmp_path: Path,
    tool_budget: int = 10,
) -> tuple[NativeToolReplResult, str, str]:
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_tool_calls=tool_calls_script,
    )
    session = NativeToolReplSession(
        provider=provider,
        tool_registry=dict(tool_registry or {}),
        tool_budget=tool_budget,
    )
    input_stream = io.StringIO("\n".join(user_inputs) + "\n")
    output_stream = io.StringIO()
    error_stream = io.StringIO()
    result = session.run(
        workspace_root=tmp_path,
        input_stream=input_stream,
        output_stream=output_stream,
        error_stream=error_stream,
    )
    return result, output_stream.getvalue(), error_stream.getvalue()


def test_usage_accumulator_cache_hit_uses_provider_specific_denominator():
    openai_usage = _UsageAccumulator()
    openai_usage.bind("openai-codex", "gpt-5.5")
    openai_usage.absorb({"input_tokens": 100, "cached_tokens": 80})

    anthropic_usage = _UsageAccumulator()
    anthropic_usage.bind("custom-anthropic", "claude-test")
    anthropic_usage.absorb(
        {
            "input_tokens": 7,
            "cached_tokens": 2,
            "cache_write_tokens": 4,
            "total_tokens": 13,
        }
    )
    read_only_cache_usage = _UsageAccumulator()
    read_only_cache_usage.bind("custom-anthropic", "claude-test")
    read_only_cache_usage.absorb(
        {"input_tokens": 100, "cached_tokens": 80, "total_tokens": 180}
    )
    all_cache_usage = _UsageAccumulator()
    all_cache_usage.bind("custom-anthropic", "claude-test")
    all_cache_usage.absorb({"input_tokens": 0, "cached_tokens": 20, "total_tokens": 20})

    assert openai_usage.cache_hit_percent == 80.0
    assert anthropic_usage.cache_hit_percent == pytest.approx(100.0 * 2 / 13)
    assert read_only_cache_usage.cache_hit_percent == pytest.approx(100.0 * 80 / 180)
    assert all_cache_usage.cache_hit_percent == 100.0


# --------------------- production registry holds model tools ----------------


def test_production_tool_registry_registers_real_bash():
    registry = production_tool_registry()

    expected = {
        "read",
        "ls",
        "grep",
        "find",
        "write",
        "edit",
        "edit_diff",
        "truncate",
        "bash",
    }
    assert set(registry.keys()) == expected
    assert "bash" in registry
    for name in registry:
        assert registry[name].definition.name == name


# ------------------------- provider capability gate ------------------------


def test_session_rejects_provider_without_tool_call_capability():
    provider = FakeNativeProvider(supports_tool_calls=False)

    with pytest.raises(ValueError, match="supports_tool_calls"):
        NativeToolReplSession(provider=provider)


def test_session_rejects_fake_provider_when_capability_not_flipped():
    provider = FakeNativeProvider()

    with pytest.raises(ValueError, match="supports_tool_calls"):
        NativeToolReplSession(provider=provider)


# --------------------------- tool budget validation -------------------------


def test_session_rejects_tool_budget_outside_supported_range():
    provider = FakeNativeProvider(supports_tool_calls=True)

    with pytest.raises(ValueError, match=r"\[1, 200\]"):
        NativeToolReplSession(provider=provider, tool_budget=0)
    with pytest.raises(ValueError, match=r"\[1, 200\]"):
        NativeToolReplSession(provider=provider, tool_budget=201)


def test_session_rejects_non_int_tool_budget():
    provider = FakeNativeProvider(supports_tool_calls=True)

    with pytest.raises(TypeError, match="tool_budget"):
        NativeToolReplSession(provider=provider, tool_budget=True)


@dataclass(slots=True)
class _CancelObservingProvider:
    """Provider whose turn blocks until cancelled at the provider boundary.

    Models a slow/streaming turn: it waits on the cancel token, and only when
    the tool loop cancels does it observe the cancellation and abort. This
    proves cancellation reaches the provider rather than the loop merely
    hiding late output while the provider runs to completion.
    """

    supports_tool_calls: bool = True
    name: str = "blocking"
    model_id: str = "blocking-model"
    started: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    observed: list[str] = field(default_factory=list)

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del request, reasoning_sink
        self.started.set()
        try:
            if cancel_token is not None and cancel_token.event.wait(timeout=2):
                self.observed.append("cancelled")
                # A late chunk after cancellation must be suppressed by the
                # loop's cancellable sink; the provider then aborts instead of
                # returning a misleading successful result.
                if stream_sink is not None:
                    stream_sink("late text that should be ignored")
                raise ProviderCancelledError("native provider turn cancelled")
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="final text",
                usage={},
            )
        finally:
            self.finished.set()


class _RecordingAbortRenderer:
    def __init__(self) -> None:
        self.chunks: list[str] = []
        self.aborted = False

    @property
    def stream_sink(self) -> StreamChunkSink:
        return self.chunks.append

    @property
    def reasoning_sink(self) -> StreamChunkSink:
        return lambda _chunk: None

    def abort_provider_turn(self) -> None:
        self.aborted = True


def test_provider_turn_escape_abort_cancels_provider_at_boundary(
    tmp_path: Path,
):
    provider = _CancelObservingProvider()

    class InterruptingUi:
        def wait_for_active_turn_interrupt(
            self, done_event, abort_event, **kwargs
        ) -> str:
            assert provider.started.wait(timeout=2)
            assert not done_event.is_set()
            abort_event.set()
            return _TURN_ABORTED

        def restore_pending_to_editor(self) -> None:
            return None

    session = NativeToolReplSession(provider=provider, workspace_root=tmp_path)
    renderer = _RecordingAbortRenderer()

    result = session._complete_provider_turn(
        ProviderRequest(
            system_prompt="",
            user_prompt="hello",
            provider_name="blocking",
            model_id="blocking-model",
            cwd=tmp_path,
        ),
        renderer=cast(Any, renderer),
        terminal_ui=cast(Any, InterruptingUi()),
    )

    assert result is None
    assert renderer.aborted is True
    # The provider OBSERVED cancellation (true cancellation, not a UI-only flag).
    assert provider.observed == ["cancelled"]
    # The worker was reaped, and its late chunk was suppressed.
    assert provider.finished.wait(timeout=2)
    assert renderer.chunks == []


def test_provider_turn_ctrl_c_abort_returns_to_prompt(tmp_path: Path):
    provider = _CancelObservingProvider()

    class CtrlCUi:
        def wait_for_active_turn_interrupt(
            self, done_event, abort_event, **kwargs
        ) -> str:
            assert provider.started.wait(timeout=2)
            assert not done_event.is_set()
            # Mirror the TUI's active-turn Ctrl-C: set the abort flag and raise.
            abort_event.set()
            raise KeyboardInterrupt

        def restore_pending_to_editor(self) -> None:
            return None

    session = NativeToolReplSession(provider=provider, workspace_root=tmp_path)
    renderer = _RecordingAbortRenderer()

    # Ctrl-C during an active turn must NOT propagate out of the turn; it aborts
    # and returns control to the prompt, exactly like Escape.
    result = session._complete_provider_turn(
        ProviderRequest(
            system_prompt="",
            user_prompt="hello",
            provider_name="blocking",
            model_id="blocking-model",
            cwd=tmp_path,
        ),
        renderer=cast(Any, renderer),
        terminal_ui=cast(Any, CtrlCUi()),
    )

    assert result is None
    assert renderer.aborted is True
    assert provider.observed == ["cancelled"]
    assert provider.finished.wait(timeout=2)
    assert renderer.chunks == []


def test_non_cooperative_provider_abort_is_still_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A provider that ignores the cancel token still aborts safely.

    Cancellation is cooperative, so the bounded join can return with the worker
    still alive. The turn must still return ``None`` and render the aborted
    state (no late chunks, no result), so the session cannot be corrupted even
    though the abandoned worker keeps running in the background.
    """

    release = threading.Event()

    @dataclass(slots=True)
    class _IgnoresCancelProvider:
        supports_tool_calls: bool = True
        name: str = "stubborn"
        model_id: str = "stubborn-model"
        started: threading.Event = field(default_factory=threading.Event)

        def complete(
            self,
            request: ProviderRequest,
            *,
            stream_sink: StreamChunkSink | None = None,
            reasoning_sink: StreamChunkSink | None = None,
            cancel_token: CancelToken | None = None,
        ) -> ProviderResult:
            del request, reasoning_sink, cancel_token
            self.started.set()
            # Block on an unrelated event — never observes cancellation.
            release.wait(timeout=5)
            if stream_sink is not None:
                stream_sink("late text that must be suppressed")
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="late final text",
                usage={},
            )

    provider = _IgnoresCancelProvider()

    class InterruptingUi:
        def wait_for_active_turn_interrupt(
            self, done_event, abort_event, **kwargs
        ) -> str:
            assert provider.started.wait(timeout=2)
            abort_event.set()
            return _TURN_ABORTED

        def restore_pending_to_editor(self) -> None:
            return None

    # Keep the bounded join short so the test does not wait the full 2s.
    monkeypatch.setattr(
        NativeToolReplSession, "_CANCEL_JOIN_TIMEOUT_SECONDS", 0.2
    )
    session = NativeToolReplSession(provider=provider, workspace_root=tmp_path)
    renderer = _RecordingAbortRenderer()

    try:
        result = session._complete_provider_turn(
            ProviderRequest(
                system_prompt="",
                user_prompt="hello",
                provider_name="stubborn",
                model_id="stubborn-model",
                cwd=tmp_path,
            ),
            renderer=cast(Any, renderer),
            terminal_ui=cast(Any, InterruptingUi()),
        )

        # The turn aborted and returned None even though the worker is alive.
        assert result is None
        assert renderer.aborted is True
        # No late chunk has been rendered (the cancellable sink drops them).
        assert renderer.chunks == []
    finally:
        # Let the abandoned worker finish; its late chunk is still suppressed.
        release.set()

    assert renderer.chunks == []


# --------------------------- successful invocation --------------------------


def test_session_invokes_fixture_tool_and_reports_metadata(tmp_path: Path):
    tool = _FixtureEchoTool()
    script = (
        (_make_call("echo", '{"text": "hello"}'),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"echo": tool},
        user_inputs=("please echo hello",),
        tmp_path=tmp_path,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.exit_code == 0
    assert result.user_turn_count == 1
    assert result.tool_invocation_count == 1
    assert result.malformed_argument_count == 0
    assert result.consecutive_malformed_streak == 0
    assert result.budget_exhausted_count == 0
    assert result.error_type is None
    assert "pipy v" in stderr  # chrome present


# ----------------------------- unknown tool name ----------------------------


def test_unknown_tool_is_returned_as_error_observation(tmp_path: Path):
    script = (
        (_make_call("missing_tool", "{}"),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={},
        user_inputs=("call missing",),
        tmp_path=tmp_path,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 0
    assert result.malformed_argument_count == 1
    assert result.consecutive_malformed_streak == 1
    assert "pipy v" in stderr  # chrome present


# ------------------------------ malformed JSON ------------------------------


def test_invalid_arguments_json_is_returned_as_error_observation(tmp_path: Path):
    tool = _FixtureEchoTool()
    script = (
        (_make_call("echo", "{not json"),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"echo": tool},
        user_inputs=("call echo",),
        tmp_path=tmp_path,
    )

    assert result.malformed_argument_count == 1
    assert result.tool_invocation_count == 0
    assert "pipy v" in stderr  # chrome present


# --------------------------- schema validation fail -------------------------


def test_schema_violation_is_returned_as_error_observation(tmp_path: Path):
    tool = _FixtureEchoTool()
    script = (
        (_make_call("echo", "{}"),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"echo": tool},
        user_inputs=("call echo",),
        tmp_path=tmp_path,
    )

    assert result.malformed_argument_count == 1
    assert result.consecutive_malformed_streak == 1
    assert result.tool_invocation_count == 0
    assert "pipy v" in stderr  # chrome present


# --------------------- three consecutive malformed = fatal ------------------


def test_three_consecutive_malformed_turns_are_fatal(tmp_path: Path):
    script = (
        (_make_call("missing_a", "{}"),),
        (_make_call("missing_b", "{}"),),
        (_make_call("missing_c", "{}"),),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={},
        user_inputs=("call missing",),
        tmp_path=tmp_path,
    )

    assert result.status == HarnessStatus.FAILED
    assert result.exit_code == 1
    assert result.error_type == "NativeToolLoopMalformedFatal"
    assert result.malformed_argument_count == 3
    assert result.consecutive_malformed_streak == 3
    assert "3 consecutive malformed tool calls" in stderr


def test_three_malformed_in_one_response_are_fatal(tmp_path: Path):
    script = (
        (
            _make_call("missing_a", "{}", correlation_id="a"),
            _make_call("missing_b", "{}", correlation_id="b"),
            _make_call("missing_c", "{}", correlation_id="c"),
        ),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={},
        user_inputs=("call three missing",),
        tmp_path=tmp_path,
    )

    assert result.status == HarnessStatus.FAILED
    assert result.malformed_argument_count == 3
    assert "3 consecutive malformed tool calls" in stderr


# ---------------------- one success resets the streak -----------------------


def test_one_success_resets_malformed_streak(tmp_path: Path):
    tool = _FixtureEchoTool()
    script = (
        (_make_call("missing", "{}", correlation_id="a"),),
        (_make_call("missing", "{}", correlation_id="b"),),
        (_make_call("echo", '{"text": "hi"}', correlation_id="c"),),
        (_make_call("missing", "{}", correlation_id="d"),),
        (_make_call("missing", "{}", correlation_id="e"),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"echo": tool},
        user_inputs=("go",),
        tmp_path=tmp_path,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 1
    assert result.malformed_argument_count == 4
    assert result.consecutive_malformed_streak == 2


# --------------------------- per-turn budget enforcement --------------------


def test_budget_exhausted_emits_observation_without_invoking(tmp_path: Path):
    tool = _FixtureEchoTool()
    script = (
        (_make_call("echo", '{"text": "1"}', correlation_id="a"),),
        (_make_call("echo", '{"text": "2"}', correlation_id="b"),),
        (_make_call("echo", '{"text": "3"}', correlation_id="c"),),
        (),
    )

    result, _stdout, stderr = _run_session(
        tool_calls_script=script,
        tool_registry={"echo": tool},
        user_inputs=("go",),
        tmp_path=tmp_path,
        tool_budget=2,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 2
    assert result.budget_exhausted_count == 1
    assert "pipy v" in stderr  # chrome present


# ---------------------- final text printed on stdout -----------------------


def test_final_text_is_printed_when_no_tool_calls(tmp_path: Path):
    script = ((),)
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_tool_calls=script,
        final_text="hello world",
    )
    session = NativeToolReplSession(provider=provider)
    input_stream = io.StringIO("hi\n")
    output_stream = io.StringIO()
    error_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=input_stream,
        output_stream=output_stream,
        error_stream=error_stream,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.user_turn_count == 1
    assert "hello world" in output_stream.getvalue()
    stderr = error_stream.getvalue()
    assert "pipy v" in stderr  # startup chrome rendered
    assert "escape interrupt" in stderr


# ---------------- session ends on EOF and stays archive-safe ---------------


def test_session_ends_at_eof_with_zero_turns(tmp_path: Path):
    provider = FakeNativeProvider(supports_tool_calls=True)
    session = NativeToolReplSession(provider=provider)
    input_stream = io.StringIO("")
    output_stream = io.StringIO()
    error_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=input_stream,
        output_stream=output_stream,
        error_stream=error_stream,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.user_turn_count == 0
    assert result.tool_invocation_count == 0


def test_native_tool_repl_result_has_only_metadata_fields():
    from dataclasses import fields

    field_names = {field.name for field in fields(NativeToolReplResult)}

    forbidden = {
        "arguments",
        "diff",
        "diffs",
        "file_content",
        "file_contents",
        "model_output",
        "patch",
        "payload",
        "prompt",
        "provider_response",
        "stderr",
        "stdout",
        "tool_payload",
    }
    assert forbidden.isdisjoint(field_names)


def test_compaction_enabled_false_disables_auto_compaction(tmp_path, monkeypatch):
    import pipy_harness.native.tool_loop_session as tls
    from pipy_harness.native.settings import SettingsManager

    # Force the threshold so auto-compaction would fire if enabled.
    monkeypatch.setattr(tls, "should_compact_tool_loop_messages", lambda messages: True)

    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "settings.json").write_text(
        '{"compaction": {"enabled": false}}', encoding="utf-8"
    )
    manager = SettingsManager(
        global_path=tmp_path / "cfg" / "settings.json",
        project_path=tmp_path / ".pipy" / "settings.json",
    )
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        final_text="answer",
    )
    session = NativeToolReplSession(provider=provider, settings_manager=manager)
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("one\ntwo\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    # compaction.enabled=false short-circuits the auto-compaction gate, so the
    # "compacted conversation context (auto; ...)" notice never appears.
    assert "compacted conversation context (auto" not in error_stream.getvalue()


def test_compaction_enabled_true_allows_auto_compaction(tmp_path, monkeypatch):
    import pipy_harness.native.tool_loop_session as tls
    from pipy_harness.native.settings import SettingsManager

    monkeypatch.setattr(tls, "should_compact_tool_loop_messages", lambda messages: True)
    manager = SettingsManager(
        global_path=tmp_path / "cfg" / "settings.json",  # missing -> defaults (enabled)
        project_path=tmp_path / ".pipy" / "settings.json",
    )
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="answer")
    session = NativeToolReplSession(provider=provider, settings_manager=manager)
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("one\ntwo\nthree\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    # Default compaction.enabled=true: the gate allows auto-compaction to run.
    assert "compacted conversation context (auto" in error_stream.getvalue()


def _scoped_models_state(tmp_path, seen):
    from pipy_harness.native import NativeModelSelection, NativeReplProviderState

    class _Rec:
        def __init__(self, provider_name, model_id, supports_tool_calls=True):
            self.name = provider_name
            self.model_id = model_id
            self.supports_tool_calls = supports_tool_calls

        def complete(self, request, **_kwargs):
            seen.append((request.provider_name, request.model_id))
            from datetime import UTC, datetime

            now = datetime.now(UTC)
            from pipy_harness.models import HarnessStatus
            from pipy_harness.native.provider import ProviderResult

            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="ok",
            )

    def factory(selection):
        return _Rec(selection.provider_name, selection.model_id)

    return NativeReplProviderState(
        selection=NativeModelSelection("openai", "gpt-5.5"),
        provider_factory=factory,
        env={"OPENAI_API_KEY": "x", "ANTHROPIC_API_KEY": "x"},
        openai_codex_auth_path=tmp_path / "missing.json",
        persist_defaults=False,
    )


def test_scoped_models_show_set_clear_and_cycle(tmp_path, monkeypatch):
    from pipy_harness.native.settings import SettingsManager

    monkeypatch.setenv("PIPY_NATIVE_DEFAULTS_PATH", str(tmp_path / "nd.json"))
    (tmp_path / "cfg").mkdir()
    settings_path = tmp_path / "cfg" / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    manager = SettingsManager(
        global_path=settings_path, project_path=tmp_path / ".pipy" / "settings.json"
    )
    seen: list = []
    state = _scoped_models_state(tmp_path, seen)
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    session = NativeToolReplSession(
        provider=provider, provider_state=state, settings_manager=manager
    )
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(
            "/scoped-models\n"
            "/scoped-models openai/*\n"
            "/scoped-models\n"
            "/scoped-models clear\n"
            "/exit\n"
        ),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    out = error_stream.getvalue()
    assert "scoped models:" in out
    assert "scoped models set: openai/*" in out
    assert "scoped models cleared" in out
    # Persisted to the settings file (set then cleared -> empty list on disk).
    on_disk = json.loads(settings_path.read_text(encoding="utf-8"))
    assert on_disk.get("enabledModels") == []
    # /scoped-models view/set/clear ran no provider turn.
    assert seen == []


def test_scoped_models_next_cycles_and_rebinds_without_provider_turn(tmp_path, monkeypatch):
    from pipy_harness.native.settings import SettingsManager

    monkeypatch.setenv("PIPY_NATIVE_DEFAULTS_PATH", str(tmp_path / "nd.json"))
    manager = SettingsManager(
        global_path=tmp_path / "cfg" / "settings.json",
        project_path=tmp_path / ".pipy" / "settings.json",
    )
    seen: list = []
    state = _scoped_models_state(tmp_path, seen)
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    session = NativeToolReplSession(
        provider=provider, provider_state=state, settings_manager=manager
    )
    before = state.current_selection().reference
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/scoped-models next\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    after = state.current_selection().reference
    assert after != before  # cycled to a different available model
    assert "selected model" in error_stream.getvalue()
    assert seen == []  # cycling ran no provider turn


def test_reload_rereads_edited_settings_without_provider_turn(tmp_path, monkeypatch):
    from pipy_harness.native.settings import SettingsManager

    monkeypatch.delenv("PIPY_THEME", raising=False)
    (tmp_path / "cfg").mkdir()
    settings_path = tmp_path / "cfg" / "settings.json"
    settings_path.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    manager = SettingsManager(
        global_path=settings_path, project_path=tmp_path / ".pipy" / "settings.json"
    )
    # Edit the file after the manager loaded the original value.
    settings_path.write_text(json.dumps({"theme": "ocean"}), encoding="utf-8")

    provider = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    session = NativeToolReplSession(provider=provider, settings_manager=manager)
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/settings\n/reload\n/settings\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    out = error_stream.getvalue()
    # First /settings shows the originally-loaded theme; after /reload the second
    # /settings reflects the edited file.
    assert "theme: dark" in out.split("reloaded settings")[0]
    assert "theme: ocean" in out.split("reloaded settings")[1]
    assert "reloaded settings, keybindings, and resources." in out
    # /reload and /settings ran no provider turn.
    assert provider._call_counter[0] == 0


def test_reload_malformed_settings_keeps_prior_and_warns(tmp_path):
    from pipy_harness.native.settings import SettingsManager

    (tmp_path / "cfg").mkdir()
    settings_path = tmp_path / "cfg" / "settings.json"
    settings_path.write_text(json.dumps({"theme": "ocean"}), encoding="utf-8")
    manager = SettingsManager(
        global_path=settings_path, project_path=tmp_path / ".pipy" / "settings.json"
    )
    settings_path.write_text("{broken", encoding="utf-8")
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    session = NativeToolReplSession(provider=provider, settings_manager=manager)
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/reload\n/settings\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    out = error_stream.getvalue()
    assert "kept prior global settings" in out
    # Prior good theme survives the malformed reload.
    assert "theme: ocean" in out


def test_reload_refreshes_extension_message_renderers(tmp_path: Path) -> None:
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    marker = extension_dir / "renderer_prefix.txt"
    extension_file = extension_dir / "renderer_reload.py"
    extension_file.write_text(
        "from pathlib import Path\n"
        "def activate(api):\n"
        "    marker = Path(__file__).with_name('renderer_prefix.txt')\n"
        "    prefix = marker.read_text(encoding='utf-8') if marker.exists() else 'old'\n"
        "    api.register_message_renderer('card', lambda data, prefix=prefix: [prefix + ':' + data['title']])\n"
        "    def card(ctx, args):\n"
        "        ctx.append_entry('card', {'title': args})\n"
        "    def flip(ctx, args):\n"
        "        marker.write_text('new', encoding='utf-8')\n"
        "    api.register_command('card', 'card', card)\n"
        "    api.register_command('flip-renderer', 'flip renderer', flip)\n",
        encoding="utf-8",
    )
    provider = FakeNativeProvider(supports_tool_calls=True)
    session = NativeToolReplSession(provider=provider, tool_registry={})
    error_stream = io.StringIO()

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/card one\n/flip-renderer\n/reload\n/card two\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    err = error_stream.getvalue()
    assert marker.read_text(encoding="utf-8") == "new"
    assert "old:one" in err
    assert "new:two" in err
    assert "old:two" not in err


class _TtyBuffer:
    """Minimal TTY-like stream so a real ``terminal_ui`` is built for a run."""

    def __init__(self) -> None:
        self._buffer = io.StringIO()

    def write(self, text: str) -> int:
        return self._buffer.write(text)

    def flush(self) -> None:
        self._buffer.flush()

    def isatty(self) -> bool:
        return True

    def getvalue(self) -> str:
        return self._buffer.getvalue()


def test_reopened_session_replays_extension_custom_entries_live_only(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "cards.py").write_text(
        "from pipy_harness.extensions import lines_component\n"
        "def activate(api):\n"
        "    api.register_message_renderer('plain-card', lambda data: ['PLAIN:' + data['title']])\n"
        "    def render_rich(data, ctx):\n"
        "        text = ctx.theme.fg('accent', 'RICH:' + data['title']) if ctx.theme else 'RICH:' + data['title']\n"
        "        return lines_component([text])\n"
        "    api.register_message_renderer('rich-card', render_rich)\n",
        encoding="utf-8",
    )
    session_dir = tmp_path / "sessions"
    tree = NativeSessionTree.create(tmp_path, session_dir=session_dir)
    plain = tree.append_custom("plain-card", {"title": "ROOT"})
    tree.append_custom("rich-card", {"title": "OFF_BRANCH"})
    tree.branch(plain.id)
    tree.append_custom("rich-card", {"title": "ACTIVE"})
    tree.append_custom("unknown-card", {"title": "FALLBACK"})
    tree.append_custom_message("legacy-card", "LEGACY_SHOW", display=True)
    tree.append_custom_message("legacy-card", "LEGACY_HIDE", display=False)
    assert tree.path is not None
    before = tree.path.read_text(encoding="utf-8")

    reopened = NativeSessionTree.open(tree.path)
    terminal_stream = _TtyBuffer()
    terminal_ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, io.StringIO()),
        terminal_stream=cast(TextIO, terminal_stream),
        cwd=tmp_path,
    )
    queued = [""]

    def _read_line(self, prompt_label, *, footer=None):
        del self, prompt_label, footer
        return queued.pop(0) if queued else ""

    monkeypatch.setattr(ToolLoopTerminalUi, "read_line", _read_line)
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kw: terminal_ui,
    )
    provider = FakeNativeProvider(supports_tool_calls=True)
    session = NativeToolReplSession(
        provider=provider,
        tool_registry={},
        native_session=reopened,
        resume_context=ResumeContext(
            prior_session_id="parent-session",
            prior_provider_name="fake",
            prior_model_id="fake-native-bootstrap",
            prior_turn_count=1,
            prior_workspace_hash="HASH",
            prior_started_at="2026-06-22T00:00:00+00:00",
            prior_ended_at="2026-06-22T00:01:00+00:00",
            prior_summary=None,
        ),
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=cast(TextIO, io.StringIO()),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    committed_frame = "\n".join(
        terminal_ui.render_lines(width=72, height=24, pad=False)
    )
    history = terminal_ui._history_blocks

    notice_index = next(i for i, (kind, _) in enumerate(history) if kind == "notice")
    first_custom_index = next(
        i for i, (kind, _) in enumerate(history) if kind.startswith("custom")
    )
    assert notice_index < first_custom_index
    assert any(kind == "custom_message_custom" for kind, _ in history)
    assert "PLAIN:ROOT" in committed_frame
    assert "RICH:ACTIVE" in committed_frame
    assert "FALLBACK" in committed_frame
    assert "LEGACY_SHOW" in committed_frame
    assert "OFF_BRANCH" not in committed_frame
    assert "LEGACY_HIDE" not in committed_frame
    assert tree.path.read_text(encoding="utf-8") == before


def test_rich_message_renderer_styles_scrollback_and_does_not_leak(
    tmp_path: Path, monkeypatch
) -> None:
    # Product path: a 2-arg component message renderer routes through the live
    # terminal UI via the SGR-preserving ``custom_message_custom`` block (NOT
    # the sanitizing/[label] ``custom`` block), the body shows in the committed
    # frame, and the body never leaks into the archive-safe output stream.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "card.py").write_text(
        "from pipy_harness.extensions import lines_component\n"
        "def activate(api):\n"
        "    def render(data, ctx):\n"
        "        text = ctx.theme.fg('accent', data['title']) if ctx.theme else data['title']\n"
        "        return lines_component([text])\n"
        "    api.register_message_renderer('card', render)\n"
        "    def cmd(ctx, args):\n"
        "        ctx.append_entry('card', {'title': 'SECRET_TITLE'})\n"
        "    api.register_command('mkcard', 'make a card', cmd)\n",
        encoding="utf-8",
    )
    terminal_stream = _TtyBuffer()
    terminal_ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, io.StringIO()),
        terminal_stream=cast(TextIO, terminal_stream),
        cwd=tmp_path,
    )
    # Feed commands without driving raw-mode reads (StringIO has no usable fd):
    # ``read_line`` returns the queued line, then "" to end the loop at EOF.
    queued = ["/mkcard\n", ""]

    def _read_line(self, prompt_label, *, footer=None):
        del self, prompt_label, footer
        return queued.pop(0) if queued else ""

    monkeypatch.setattr(ToolLoopTerminalUi, "read_line", _read_line)
    provider = FakeNativeProvider(supports_tool_calls=True)
    session = NativeToolReplSession(provider=provider, tool_registry={})
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kw: terminal_ui,
    )
    output_stream = io.StringIO()

    session.run(
        workspace_root=tmp_path,
        input_stream=cast(TextIO, io.StringIO()),
        output_stream=output_stream,
        error_stream=io.StringIO(),
    )

    committed_frame = "\n".join(terminal_ui.render_lines(width=72, height=20, pad=False))
    archive_text = output_stream.getvalue()

    # Styled route => SGR-safe ``custom_message_custom`` block, not plain custom.
    assert any(k == "custom_message_custom" for k, _ in terminal_ui._history_blocks)
    assert not any(k == "custom" for k, _ in terminal_ui._history_blocks)
    # Body rendered live in the committed scrollback.
    assert "SECRET_TITLE" in committed_frame
    # No forced ``[card]`` label injected by the component path (judgment 2).
    assert "[card]" not in committed_frame
    # The body never reaches the archive-safe (metadata-only) output stream.
    assert "SECRET_TITLE" not in archive_text


def test_reload_rebinds_active_extension_provider_factory(tmp_path):
    marker = tmp_path / "marker.txt"
    marker.write_text("before", encoding="utf-8")
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "reload_provider.py").write_text(
        "from datetime import datetime, timezone\n"
        "from pathlib import Path\n"
        "from pipy_harness.extensions import ExtensionProvider\n"
        "from pipy_harness.models import HarnessStatus\n"
        "from pipy_harness.native.models import ProviderResult\n"
        f"MARKER = Path({str(marker)!r})\n"
        "class _Port:\n"
        "    name = 'reloadext'\n"
        "    supports_tool_calls = True\n"
        "    def __init__(self, ctx):\n"
        "        self.model_id = ctx.model_id\n"
        "        self.final_text = MARKER.read_text(encoding='utf-8')\n"
        "    def complete(self, request, **kwargs):\n"
        "        now = datetime(2026, 6, 18, tzinfo=timezone.utc)\n"
        "        return ProviderResult(status=HarnessStatus.SUCCEEDED,\n"
        "            provider_name=self.name, model_id=self.model_id,\n"
        "            started_at=now, ended_at=now,\n"
        "            final_text=self.final_text, tool_calls=())\n"
        "def _flip(ctx, args):\n"
        "    MARKER.write_text('after', encoding='utf-8')\n"
        "def activate(api):\n"
        "    api.register_command('flip-provider', 'flip provider marker', _flip)\n"
        "    api.register_provider(ExtensionProvider(name='reloadext',\n"
        "        default_model='m', models=('m',), factory=lambda ctx: _Port(ctx)))\n",
        encoding="utf-8",
    )
    providers, unregistered = load_extension_provider_contributions(
        tmp_path,
        reserved_command_names=extension_reserved_command_names(),
        reserved_tool_names=extension_reserved_tool_names(),
    )
    catalog_state = ProviderCatalogState(models_json_path=tmp_path / "absent.json")
    catalog_state.set_extension_provider_contributions(providers, unregistered)
    state = NativeReplProviderState(
        selection=NativeModelSelection("reloadext", "m"),
        provider_factory=lambda _selection: (_ for _ in ()).throw(
            AssertionError("extension provider should be built from the catalog")
        ),
        catalog_state=catalog_state,
        persist_defaults=False,
    )
    provider = state.current_provider()
    session = NativeToolReplSession(provider=provider, provider_state=state)
    output_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/flip-provider\n/reload\nhi\n/exit\n"),
        output_stream=output_stream,
        error_stream=io.StringIO(),
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert "after" in output_stream.getvalue()
    assert "before" not in output_stream.getvalue()


def test_reload_falls_back_when_shadowing_extension_provider_is_removed(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    extension_file = extension_dir / "shadow_openai.py"
    extension_file.write_text(
        "from datetime import datetime, timezone\n"
        "from pathlib import Path\n"
        "from pipy_harness.extensions import ExtensionProvider\n"
        "from pipy_harness.models import HarnessStatus\n"
        "from pipy_harness.native.models import ProviderResult\n"
        f"EXTENSION_FILE = Path({str(extension_file)!r})\n"
        "class _Port:\n"
        "    name = 'openai'\n"
        "    model_id = 'ext'\n"
        "    supports_tool_calls = True\n"
        "    def complete(self, request, **kwargs):\n"
        "        now = datetime(2026, 6, 18, tzinfo=timezone.utc)\n"
        "        return ProviderResult(status=HarnessStatus.SUCCEEDED,\n"
        "            provider_name=self.name, model_id=self.model_id,\n"
        "            started_at=now, ended_at=now,\n"
        "            final_text='removed extension provider was used', tool_calls=())\n"
        "def _remove(ctx, args):\n"
        "    EXTENSION_FILE.unlink()\n"
        "def activate(api):\n"
        "    api.register_command('remove-shadow', 'remove shadow provider', _remove)\n"
        "    api.register_provider(ExtensionProvider(name='openai',\n"
        "        default_model='ext', models=('ext',), factory=lambda ctx: _Port()))\n",
        encoding="utf-8",
    )
    providers, unregistered = load_extension_provider_contributions(
        tmp_path,
        reserved_command_names=extension_reserved_command_names(),
        reserved_tool_names=extension_reserved_tool_names(),
    )
    catalog_state = ProviderCatalogState(models_json_path=tmp_path / "absent.json")
    catalog_state.set_extension_provider_contributions(providers, unregistered)
    state = NativeReplProviderState(
        selection=NativeModelSelection("openai", "ext"),
        provider_factory=lambda _selection: (_ for _ in ()).throw(
            AssertionError("selection should be built from the catalog")
        ),
        env={"OPENAI_API_KEY": "sk-test"},
        openai_codex_auth_path=tmp_path / "missing-codex.json",
        catalog_state=catalog_state,
        persist_defaults=False,
    )
    session = NativeToolReplSession(
        provider=state.current_provider(),
        provider_state=state,
    )
    error_stream = io.StringIO()
    output_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/remove-shadow\n/reload\n/exit\n"),
        output_stream=output_stream,
        error_stream=error_stream,
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert state.current_selection().reference != "openai/ext"
    assert session.provider.name == state.current_selection().provider_name
    assert "active model disappeared on reload" in error_stream.getvalue()
    assert "removed extension provider was used" not in output_stream.getvalue()


def test_reload_fail_closes_removed_extension_provider_when_no_fallback(
    tmp_path,
):
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    extension_file = extension_dir / "unique_provider.py"
    extension_file.write_text(
        "from datetime import datetime, timezone\n"
        "from pathlib import Path\n"
        "from pipy_harness.extensions import ExtensionProvider\n"
        "from pipy_harness.models import HarnessStatus\n"
        "from pipy_harness.native.models import ProviderResult\n"
        f"EXTENSION_FILE = Path({str(extension_file)!r})\n"
        "class _Port:\n"
        "    name = 'uniqueext'\n"
        "    model_id = 'm'\n"
        "    supports_tool_calls = True\n"
        "    def complete(self, request, **kwargs):\n"
        "        now = datetime(2026, 6, 18, tzinfo=timezone.utc)\n"
        "        return ProviderResult(status=HarnessStatus.SUCCEEDED,\n"
        "            provider_name=self.name, model_id=self.model_id,\n"
        "            started_at=now, ended_at=now,\n"
        "            final_text='removed unique extension provider was used',\n"
        "            tool_calls=())\n"
        "def _remove(ctx, args):\n"
        "    EXTENSION_FILE.unlink()\n"
        "def activate(api):\n"
        "    api.register_command('remove-unique-provider', 'remove provider', _remove)\n"
        "    api.register_provider(ExtensionProvider(name='uniqueext',\n"
        "        default_model='m', models=('m',), factory=lambda ctx: _Port()))\n",
        encoding="utf-8",
    )
    providers, unregistered = load_extension_provider_contributions(
        tmp_path,
        reserved_command_names=extension_reserved_command_names(),
        reserved_tool_names=extension_reserved_tool_names(),
    )
    catalog_state = ProviderCatalogState(
        models_json_path=tmp_path / "absent.json",
        env={},
        openai_codex_auth_path=tmp_path / "missing-codex.json",
    )
    catalog_state.set_extension_provider_contributions(providers, unregistered)
    state = NativeReplProviderState(
        selection=NativeModelSelection("uniqueext", "m"),
        provider_factory=lambda _selection: (_ for _ in ()).throw(
            AssertionError("selection should be built from the catalog")
        ),
        env={},
        openai_codex_auth_path=tmp_path / "missing-codex.json",
        catalog_state=catalog_state,
        persist_defaults=False,
    )
    session = NativeToolReplSession(
        provider=state.current_provider(),
        provider_state=state,
    )
    error_stream = io.StringIO()
    output_stream = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/remove-unique-provider\n/reload\nhi\n/exit\n"),
        output_stream=output_stream,
        error_stream=error_stream,
    )

    stderr = error_stream.getvalue()
    assert result.status == HarnessStatus.SUCCEEDED
    assert state.current_selection().reference == "uniqueext/m"
    assert "no available tool-capable fallback was found" in stderr
    assert "ProviderUnavailableAfterReload" in stderr
    assert "removed unique extension provider was used" not in output_stream.getvalue()


def test_changelog_command_renders_without_provider_turn(tmp_path):
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    session = NativeToolReplSession(provider=provider)
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/changelog\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    assert "What's New" in error_stream.getvalue()
    assert provider._call_counter[0] == 0


def test_startup_changelog_shows_new_entries_on_version_bump(tmp_path):
    from pipy_harness.native.settings import SettingsManager

    (tmp_path / "cfg").mkdir()
    settings_path = tmp_path / "cfg" / "settings.json"
    # A stale lastChangelogVersion forces a bump against the shipped version.
    settings_path.write_text(
        json.dumps({"lastChangelogVersion": "0.0.0"}), encoding="utf-8"
    )
    manager = SettingsManager(
        global_path=settings_path, project_path=tmp_path / ".pipy" / "settings.json"
    )
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    session = NativeToolReplSession(provider=provider, settings_manager=manager)
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    out = error_stream.getvalue()
    assert "What's New" in out  # new entries shown at startup
    # The shipped version was recorded so the next run does not re-show.
    on_disk = json.loads(settings_path.read_text(encoding="utf-8"))
    assert on_disk["lastChangelogVersion"] != "0.0.0"


def test_startup_changelog_first_run_records_version_shows_nothing(tmp_path):
    from pipy_harness.native.settings import SettingsManager

    (tmp_path / "cfg").mkdir()
    settings_path = tmp_path / "cfg" / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    manager = SettingsManager(
        global_path=settings_path, project_path=tmp_path / ".pipy" / "settings.json"
    )
    provider = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    session = NativeToolReplSession(provider=provider, settings_manager=manager)
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    assert "What's New" not in error_stream.getvalue()
    on_disk = json.loads(settings_path.read_text(encoding="utf-8"))
    assert on_disk.get("lastChangelogVersion")  # recorded on first run


# --------- Pi-faithful slash-command set (no deprecation shims) --------------


def _run_local_commands(tmp_path: Path, script: str) -> str:
    """Drive the tool-loop session over a local-command script, return stderr."""

    provider = FakeNativeProvider(supports_tool_calls=True, final_text="ok")
    session = NativeToolReplSession(provider=provider)
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(script),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    assert provider._call_counter[0] == 0  # local commands run no provider turn
    return error_stream.getvalue()


def test_pipy_only_commands_removed(tmp_path: Path):
    # Pi has no /clear, /status, or /help built-ins; the equivalents are /new,
    # /session, and /hotkeys, which remain canonical and unchanged. The
    # pipy-only aliases are removed outright (no deprecation shims), so each
    # dispatches as an unknown command: no handler runs and no provider turn
    # fires.
    for gone in ("/clear", "/status", "/help"):
        out = _run_local_commands(tmp_path, f"{gone}\n/exit\n")
        assert f"'{gone}' is not handled in tool-loop mode" in out
        # No trace of the old deprecation notices or alias behavior.
        assert "is deprecated" not in out


def test_theme_command_removed(tmp_path: Path):
    # Pi has no /theme command: theme selection now lives in the /settings
    # dialog (covered by the settings-dialog theme-row test). Dispatching
    # /theme is therefore an unknown command — no handler runs, the theme is
    # not switched, and no provider turn fires.
    out = _run_local_commands(tmp_path, "/theme\n/exit\n")
    assert "'/theme' is not handled in tool-loop mode" in out
    # It is not advertised as a supported local command, and nothing about the
    # old list/apply behavior remains.
    assert "available:" not in out


def test_tool_filter_options_filter_provider_visible_tools(tmp_path: Path):
    seen: list[tuple[str, ...]] = []

    @dataclass(frozen=True, slots=True)
    class RecordingProvider:
        supports_tool_calls: bool = True
        name: str = "recording"
        model_id: str = "recording-model"

        def complete(
            self,
            request: ProviderRequest,
            *,
            stream_sink: StreamChunkSink | None = None,
            reasoning_sink: StreamChunkSink | None = None,
            cancel_token: CancelToken | None = None,
        ) -> ProviderResult:
            del stream_sink, reasoning_sink, cancel_token
            seen.append(tuple(tool.name for tool in request.available_tools))
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="ok",
                usage={},
                tool_calls=(),
            )

    from pipy_harness.native.tool_loop_session import ToolFilterOptions

    session = NativeToolReplSession(
        provider=RecordingProvider(),
        tool_registry={"echo": _FixtureEchoTool()},
        tool_filter_options=ToolFilterOptions(allow=("echo",)),
    )
    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert seen == [("echo",)]


def test_unfiltered_tool_visibility_includes_extension_tools_added_by_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # No CLI filter is different from an explicit active-name snapshot: after a
    # reload, newly discovered extension tools must become provider-visible.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    dynamic_tool_file = extension_dir / "dynamic_tool.py"
    (extension_dir / "installer.py").write_text(
        "from pathlib import Path\n"
        f"DYNAMIC_TOOL = Path({str(dynamic_tool_file)!r})\n"
        "def install(ctx, args):\n"
        "    DYNAMIC_TOOL.write_text(\n"
        "        \"from pipy_harness.extensions import ExtensionTool, ToolResult\\n\"\n"
        "        \"def activate(api):\\n\"\n"
        "        \"    api.register_tool(ExtensionTool(\\n\"\n"
        "        \"        name='dynamic_tool', description='added on reload',\\n\"\n"
        "        \"        input_schema={'type': 'object'},\\n\"\n"
        "        \"        handler=lambda ctx, params: ToolResult(content='ok'),\\n\"\n"
        "        \"    ))\\n\"\n"
        "    )\n"
        "def activate(api):\n"
        "    api.register_command('install-tool', 'install a tool', install)\n",
        encoding="utf-8",
    )
    seen: list[tuple[str, ...]] = []

    @dataclass(frozen=True, slots=True)
    class RecordingProvider:
        supports_tool_calls: bool = True
        name: str = "recording"
        model_id: str = "recording-model"

        def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
            seen.append(tuple(tool.name for tool in request.available_tools))
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="ok",
                usage={},
                tool_calls=(),
            )

    session = NativeToolReplSession(
        provider=RecordingProvider(), tool_registry={"echo": _FixtureEchoTool()}
    )
    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/install-tool\n/reload\ngo\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status == HarnessStatus.SUCCEEDED
    assert seen == [("echo", "dynamic_tool")]


def test_tool_filter_options_unknown_name_fails_early(tmp_path: Path):
    from pipy_harness.native.tool_loop_session import ToolFilterOptions

    session = NativeToolReplSession(
        provider=FakeNativeProvider(supports_tool_calls=True),
        tool_registry={"echo": _FixtureEchoTool()},
        tool_filter_options=ToolFilterOptions(exclude=("missing",)),
    )

    with pytest.raises(ValueError, match="unknown tool name"):
        session.run(
            workspace_root=tmp_path,
            input_stream=io.StringIO("go\n"),
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        )


def test_no_builtin_tools_removes_builtin_but_keeps_extension_tool(tmp_path: Path):
    from pipy_harness.native.tool_loop_session import ToolFilterOptions, _filtered_tool_names

    assert _filtered_tool_names(
        builtin_names={"read", "bash"},
        all_names={"read", "bash", "ext_tool"},
        options=ToolFilterOptions(no_builtin_tools=True),
    ) == {"ext_tool"}
    assert _filtered_tool_names(
        builtin_names={"read", "bash"},
        all_names={"read", "bash", "ext_tool"},
        options=ToolFilterOptions(no_tools=True),
    ) == set()
