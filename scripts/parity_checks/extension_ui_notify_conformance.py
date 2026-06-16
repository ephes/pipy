"""Hard conformance gate for extension UI notifications (slice 9).

Drives the real `NativeToolReplSession.run` and asserts the slice-9
invariants from `docs/extension-api.md`:

1. a command handler's `ctx.ui.notify` surfaces to the live UI;
2. a hook handler's `ctx.ui.notify` (here, an `agent_start` observer)
   surfaces to the live UI;
3. notify degrades deterministically in non-interactive mode (the
   captured-stream session here is non-interactive, has_ui is False, and
   the notifications still appear without blocking).

Exits 0 when every check passes, 1 otherwise. No network.

Run:

    uv run python scripts/parity_checks/extension_ui_notify_conformance.py --json
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
from pipy_harness.native.extension_runtime import make_extension_context
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


def _write(workspace: Path, name: str, body: str) -> None:
    ext = workspace / ".pipy" / "extensions"
    ext.mkdir(parents=True, exist_ok=True)
    (ext / f"{name}.py").write_text(body, encoding="utf-8")


def _run(workspace: Path, prompt: str) -> str:
    session = NativeToolReplSession(provider=_FinalText(), tool_registry={})
    error_stream = io.StringIO()
    session.run(
        workspace_root=workspace,
        input_stream=io.StringIO(prompt),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    return error_stream.getvalue()


def run_checks(base: Path) -> list[Check]:
    checks: list[Check] = []

    cmd = base / "cmd"
    cmd.mkdir()
    _write(
        cmd,
        "st",
        "def activate(api):\n"
        "    def status(ctx, args):\n"
        "        ctx.ui.notify('CMD_NOTIFY_OK')\n"
        "    api.register_command('st', 'status', status)\n",
    )
    checks.append(
        Check(
            "command_notify",
            "CMD_NOTIFY_OK" in _run(cmd, "/st\n"),
            "command ctx.ui.notify surfaces to the live UI",
        )
    )

    hook = base / "hook"
    hook.mkdir()
    _write(
        hook,
        "noisy",
        "def activate(api):\n"
        "    @api.on('agent_start')\n"
        "    def obs(event, ctx):\n"
        "        ctx.ui.notify('HOOK_NOTIFY_OK')\n",
    )
    checks.append(
        Check(
            "hook_notify",
            "HOOK_NOTIFY_OK" in _run(hook, "hello\n"),
            "hook ctx.ui.notify surfaces to the live UI",
        )
    )

    # Deterministic non-interactive behavior at the unit level.
    ctx = make_extension_context("/tmp", has_ui=False)
    ctx.ui.notify("recorded")
    checks.append(
        Check(
            "non_interactive_records",
            ctx.has_ui is False and ctx.ui.messages == [("info", "recorded")],
            "notify records deterministically in non-interactive mode",
        )
    )
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        os.environ["PIPY_CONFIG_HOME"] = str(base / "empty-global")
        checks = run_checks(base)

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
