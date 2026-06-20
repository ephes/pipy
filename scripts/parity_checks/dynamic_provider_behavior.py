"""Parity row E5 behavior check: dynamic provider/model swap mid-session.

Proves the live ``/model`` capability in the product tool-loop REPL through the
shared ``NativeReplProviderState`` boundary — not a dead wrapper module.

Tool-loop product path (driven via ``NativeToolReplSession.run`` with captured
streams, recording every ``ProviderRequest``):

  * a successful switch rebinds the live provider/model AND clears the
    provider-visible conversation (the next request carries only the new user
    message);
  * a refused switch (availability gate) preserves both the selection and the
    accumulated conversation;
  * the two ``/model`` commands trigger no extra provider calls.

Exits 0 when every behavior holds, 1 otherwise. No real network or AI calls.
"""

from __future__ import annotations

import io
import tempfile
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
    """Tool-capable provider that appends every request to a shared log."""

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
class _ToolLoopProbe:
    requests: list[ProviderRequest] = field(default_factory=list)

    def factory(self, selection: NativeModelSelection) -> _RecordingToolProvider:
        return _RecordingToolProvider(
            provider_name=selection.provider_name,
            model_id_value=selection.model_id,
            requests=self.requests,
        )


def _tool_loop_requests() -> list[ProviderRequest]:
    """Drive the tool-loop REPL across a successful and a refused switch."""

    probe = _ToolLoopProbe()
    initial_selection = NativeModelSelection("fake", "model-a")
    provider_state = NativeReplProviderState(
        selection=initial_selection,
        provider_factory=probe.factory,
        defaults_store=None,
        persist_defaults=False,
        env={},
    )
    cwd = Path(tempfile.mkdtemp())
    session = NativeToolReplSession(
        provider=probe.factory(initial_selection),
        tool_registry={},
        provider_state=provider_state,
    )
    session.run(
        workspace_root=cwd,
        input_stream=io.StringIO(
            "first\n"
            "/model fake/model-b\n"
            "second\n"
            "/model openai/gpt-5.5\n"
            "third\n"
            "/exit\n"
        ),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    return probe.requests


def _tool_loop_behaviour_holds() -> bool:
    requests = _tool_loop_requests()
    # Three prompts -> three provider calls; the two /model commands add none.
    if len(requests) != 3:
        return False
    # Switch applied on the live provider for the second prompt.
    if requests[0].model_id != "model-a":
        return False
    if requests[1].model_id != "model-b":
        return False
    # Refused switch preserves the prior selection for the third prompt.
    if requests[2].model_id != "model-b":
        return False
    # Successful switch cleared the provider-visible conversation: the second
    # request carries only the freshly typed user message.
    second_messages = requests[1].messages
    if len(second_messages) != 1:
        return False
    if second_messages[0].content.strip() != "second":
        return False
    # Refused switch did NOT clear: the third request still carries the prior
    # exchange ahead of the new prompt.
    third_messages = requests[2].messages
    if len(third_messages) <= 1:
        return False
    if third_messages[0].content.strip() != "second":
        return False
    return True


def main() -> int:
    if not _tool_loop_behaviour_holds():
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
