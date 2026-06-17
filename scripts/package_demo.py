"""Deterministic TUI demo runner for installed-package runtime composition.

Launches a real `NativeToolReplSession` (real TUI, real settings, real
package discovery / theme registry / resource dispatch) with a *scripted*
provider so the flow is fully deterministic for tmux UI verification — no
network, no auth. The demo assumes the workspace already has the example
`demo-pack` package installed (the tmux harness runs `pipy install` first),
so the session discovers the package's skill, prompt, theme, and extension.

Usage (typically under tmux):

    uv run python scripts/package_demo.py <workspace-with-installed-demo-pack>

The scripted provider echoes a fixed acknowledgement for any turn, which is
enough to prove that a package-contributed skill/prompt body actually reaches
a provider turn when invoked as `/skill <name>` or `/template <name>`.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult

_REPLY = "demo-pack provider acknowledged the request."


class _ScriptedProvider:
    """A deterministic provider that echoes a fixed reply for any turn."""

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
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text=_REPLY,
            usage={},
            tool_calls=(),
        )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: package_demo.py <workspace>", file=sys.stderr)
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
