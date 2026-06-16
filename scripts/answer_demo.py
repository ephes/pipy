"""Deterministic TUI demo runner for the example `answer` extension.

Launches a real `NativeToolReplSession` (real TUI, real extension discovery /
activation / dispatch / overlay) with a *scripted* provider so the `/answer`
flow is fully deterministic for tmux UI verification — no network, no auth.

Usage (typically under tmux):

    uv run python scripts/answer_demo.py <workspace-with-.pipy/extensions/answer.py>

Provider script:
- the first ordinary turn replies with a message that poses two questions, so
  there is a "last assistant message" to extract from;
- the extraction call (system prompt contains "question extractor") returns a
  fixed two-question JSON object;
- the submitted-answers turn replies with a short acknowledgement.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult

_EXTRACTION_JSON = (
    '{"questions": ['
    '{"question": "Which database?", '
    '"context": "We can configure MySQL or PostgreSQL."}, '
    '{"question": "TypeScript or JavaScript?"}'
    "]}"
)
_SEED_REPLY = (
    "Before I scaffold the project I need a couple of decisions. "
    "Which database? And TypeScript or JavaScript?"
)


class _ScriptedProvider:
    """A deterministic provider that drives the answer-extension demo."""

    @property
    def name(self) -> str:
        return "demo"

    @property
    def model_id(self) -> str:
        return "demo-model"

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        now = datetime.now(UTC)
        if "question extractor" in (request.system_prompt or ""):
            text = _EXTRACTION_JSON
        elif "I answered your questions" in (request.user_prompt or ""):
            text = "Thanks — recorded your answers."
        else:
            text = _SEED_REPLY
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text=text,
            usage={},
            tool_calls=(),
        )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: answer_demo.py <workspace>", file=sys.stderr)
        return 2
    workspace = Path(argv[1]).expanduser().resolve()

    from pipy_harness.native.tool_loop_session import NativeToolReplSession

    session = NativeToolReplSession(provider=_ScriptedProvider(), tool_registry={})
    session.run(
        workspace_root=workspace,
        input_stream=sys.stdin,
        output_stream=sys.stdout,
        error_stream=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
