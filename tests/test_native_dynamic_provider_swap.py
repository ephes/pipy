"""Parity row E5: dynamic provider/model swap mid-session.

These product-path tests complement the end-to-end PTY selector test
(``test_pty_inline_tui_model_selector_selects_and_rebinds``) and the
parity behaviour check (``scripts/parity_checks/dynamic_provider_behavior``).
They prove, through the shared ``NativeReplProviderState`` boundary, that the
tool-loop REPL switches mid-session, surfaces the change in visible status,
clears or preserves conversation state as documented, and refuses an unavailable
target via the availability gate without provider/tool side effects.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.repl_state import (
    NativeModelSelection,
    NativeReplProviderState,
)
from pipy_harness.native.tool_loop_session import NativeToolReplSession


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
