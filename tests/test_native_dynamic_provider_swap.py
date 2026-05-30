"""Parity row E5: dynamic provider/model swap mid-session.

These product-path tests complement the end-to-end PTY selector test
(``test_pty_inline_tui_model_selector_selects_and_rebinds``) and the
parity behaviour check (``scripts/parity_checks/dynamic_provider_behavior``).
They prove, through the shared ``NativeReplProviderState`` boundary, that both
REPL paths switch mid-session, surface the change in visible status, clear or
preserve conversation state as documented, and refuse an unavailable target via
the availability gate without provider/tool side effects.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.adapters.native import PipyNativeReplAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import HarnessStatus, RunRequest
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.repl_state import (
    NativeModelSelection,
    NativeReplProviderState,
)
from pipy_harness.native.tool_loop_session import NativeToolReplSession
from pipy_harness.runner import HarnessRunner


def _no_tool_state() -> NativeReplProviderState:
    return NativeReplProviderState(
        selection=NativeModelSelection("fake", "model-a"),
        provider_factory=lambda selection: FakeNativeProvider(
            model_id=selection.model_id
        ),
        defaults_store=None,
        persist_defaults=False,
        env={},
    )


def _run_no_tool(script: str, tmp_path: Path) -> tuple[str, NativeReplProviderState]:
    state = _no_tool_state()
    error_stream = io.StringIO()
    adapter = PipyNativeReplAdapter(
        provider_state=state,
        input_stream=io.StringIO(script),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    HarnessRunner(adapter=adapter).run(
        RunRequest(
            agent="pipy-native",
            slug="e5-unit",
            command=[],
            cwd=tmp_path,
            goal="e5 unit",
            root=tmp_path / "archive",
            capture_policy=CapturePolicy(),
        )
    )
    return error_stream.getvalue(), state


def test_no_tool_switch_updates_visible_settings_status(tmp_path: Path) -> None:
    stderr, state = _run_no_tool("/model fake/model-b\n/settings\n/exit\n", tmp_path)
    assert "selected model fake/model-b" in stderr
    # The visible /settings status reflects the live selection after the switch.
    assert "active: fake/model-b" in stderr
    assert state.current_selection() == NativeModelSelection("fake", "model-b")


def test_no_tool_unavailable_target_refused_by_gate(tmp_path: Path) -> None:
    stderr, state = _run_no_tool(
        "/model fake/model-b\n/model openai/gpt-5.5\n/settings\n/exit\n", tmp_path
    )
    # Availability gate refuses the keyless provider; selection stays put.
    assert "OPENAI_API_KEY is not set" in stderr
    assert "active: fake/model-b" in stderr
    assert state.current_selection() == NativeModelSelection("fake", "model-b")


@dataclass
class _RecordingToolProvider:
    provider_name: str
    model_id_value: str
    requests: list[ProviderRequest]
    supports_tool_calls: bool = True

    @property
    def name(self) -> str:
        return self.provider_name

    @property
    def model_id(self) -> str:
        return self.model_id_value

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.provider_name,
            model_id=self.model_id_value,
            started_at=now,
            ended_at=now,
            final_text="ok",
            usage=None,
            metadata=None,
            tool_calls=(),
        )


@dataclass
class _Probe:
    requests: list[ProviderRequest] = field(default_factory=list)

    def factory(self, selection: NativeModelSelection) -> _RecordingToolProvider:
        return _RecordingToolProvider(
            selection.provider_name, selection.model_id, self.requests
        )


def test_tool_loop_switch_clears_then_refusal_preserves(tmp_path: Path) -> None:
    probe = _Probe()
    initial = NativeModelSelection("fake", "model-a")
    state = NativeReplProviderState(
        selection=initial,
        provider_factory=probe.factory,
        defaults_store=None,
        persist_defaults=False,
        env={},
    )
    session = NativeToolReplSession(
        provider=probe.factory(initial),
        tool_registry={},
        provider_state=state,
    )
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(
            "first\n/model fake/model-b\nsecond\n/model openai/gpt-5.5\nthird\n/exit\n"
        ),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    requests = probe.requests
    # Three prompts -> three provider calls; the two /model commands add none.
    assert [r.model_id for r in requests] == ["model-a", "model-b", "model-b"]
    # Successful switch rebinds and clears the provider-visible conversation.
    assert [
        getattr(m, "content", "").strip() for m in requests[1].messages
    ] == ["second"]
    # Refused switch (availability gate) preserves the accumulated conversation.
    assert len(requests[2].messages) > 1
    assert getattr(requests[2].messages[0], "content", "").strip() == "second"
    # The live selection is unchanged by the refused switch.
    assert state.current_selection() == NativeModelSelection("fake", "model-b")
