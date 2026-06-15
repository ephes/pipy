"""Hard conformance gate for extension lifecycle events (slice 5).

Drives the real `NativeToolReplSession.run` with a final-text provider and
an extension that records every lifecycle event, then asserts the slice-5
invariants from `docs/extension-api.md`:

1. a one-prompt session fires the full lifecycle sequence
   (`session_start` -> `agent_start` -> `turn_start` -> `turn_end` ->
   `agent_end` -> `session_shutdown`) to extension observers;
2. `session_start` carries the `"startup"` reason;
3. lifecycle observers are fail-soft: a crashing observer does not break
   the session (the run still completes and later observers still fire).

Exits 0 when every check passes, 1 otherwise. No network.

Run:

    uv run python scripts/parity_checks/extension_lifecycle_conformance.py --json
"""

from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.tool_loop_session import NativeToolReplSession


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


class _FinalText:
    name = "stub"
    model_id = "stub-model"

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="done",
            tool_calls=(),
        )


def run_checks(workspace: Path, proof: Path) -> list[Check]:
    ext = workspace / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "recorder.py").write_text(
        "from pathlib import Path\n"
        f"PROOF = Path({str(proof)!r})\n"
        "def activate(api):\n"
        "    def make(name):\n"
        "        def obs(event, ctx):\n"
        "            with PROOF.open('a') as fh:\n"
        "                fh.write(event.name + ':' + (event.reason or '') + '\\n')\n"
        "        return obs\n"
        "    for n in ('session_start','session_shutdown','agent_start',\n"
        "              'agent_end','turn_start','turn_end'):\n"
        "        api.on(n, make(n))\n",
        encoding="utf-8",
    )
    # A crashing observer on turn_end must not break the session.
    (ext / "crasher.py").write_text(
        "def activate(api):\n"
        "    @api.on('turn_end')\n"
        "    def boom(event, ctx):\n"
        "        raise RuntimeError('boom')\n",
        encoding="utf-8",
    )

    session = NativeToolReplSession(provider=_FinalText(), tool_registry={})
    result = session.run(
        workspace_root=workspace,
        input_stream=io.StringIO("hello\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    recorded = proof.read_text().splitlines() if proof.exists() else []

    checks: list[Check] = []
    required = [
        "session_start:startup",
        "agent_start:",
        "turn_start:",
        "turn_end:",
        "agent_end:",
        "session_shutdown:",
    ]
    checks.append(
        Check(
            "full_sequence",
            all(item in recorded for item in required),
            "full lifecycle sequence fired",
        )
    )
    ordered = (
        "session_start:startup" in recorded
        and "agent_start:" in recorded
        and "session_shutdown:" in recorded
        and recorded.index("session_start:startup") < recorded.index("agent_start:")
        and recorded.index("agent_end:") < recorded.index("session_shutdown:")
    )
    checks.append(Check("ordering", ordered, "session brackets the agent run"))
    checks.append(
        Check(
            "crash_is_failsoft",
            result.status is HarnessStatus.SUCCEEDED,
            "crashing observer did not break the session",
        )
    )
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        workspace = base / "work"
        workspace.mkdir()
        os.environ["PIPY_CONFIG_HOME"] = str(base / "empty-global")
        checks = run_checks(workspace, base / "events.txt")

    passed = all(c.passed for c in checks)
    if args.json:
        report = {
            "passed": passed,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in checks
            ],
        }
        print(json.dumps(report, indent=2))
    else:
        for c in checks:
            status = "PASS" if c.passed else "FAIL"
            print(f"[{status}] {c.name}: {c.detail}")
        print("ALL PASS" if passed else "FAILURES PRESENT")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
