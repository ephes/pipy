"""Hard conformance gate for input / before_agent_start / send_user_message
(slice 6).

Drives the real `NativeToolReplSession.run` with a recording provider and
asserts the slice-6 invariants from `docs/extension-api.md`:

1. an `input` hook transforms the prompt the provider sees;
2. a `before_agent_start` hook injects bounded context into the turn's
   system prompt;
3. `api.send_user_message` (called from a command) enqueues a deterministic
   provider turn (the command itself issues none);
4. a crashing `input` hook is fail-safe (the prompt is unchanged and the
   run still completes).

Exits 0 when every check passes, 1 otherwise. No network.

Run:

    uv run python scripts/parity_checks/extension_input_hooks_conformance.py --json
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


class _Recorder:
    name = "stub"
    model_id = "stub-model"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
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


def _run(workspace: Path, prompt: str) -> _Recorder:
    provider = _Recorder()
    session = NativeToolReplSession(provider=provider, tool_registry={})
    session.run(
        workspace_root=workspace,
        input_stream=io.StringIO(prompt),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    return provider


def run_checks(base: Path) -> list[Check]:
    checks: list[Check] = []

    ws1 = base / "input"
    ws1.mkdir()
    _write(
        ws1,
        "tag",
        "from pipy_harness.extensions import InputTransform\n"
        "def activate(api):\n"
        "    @api.on('input')\n"
        "    def t(event, ctx):\n"
        "        return InputTransform(text='[TAG] ' + event.text)\n",
    )
    p1 = _run(ws1, "hello\n")
    checks.append(
        Check(
            "input_transform",
            bool(p1.requests) and "[TAG] hello" in p1.requests[0].user_prompt,
            "input hook transforms the provider-visible prompt",
        )
    )

    ws2 = base / "before"
    ws2.mkdir()
    _write(
        ws2,
        "ctx",
        "from pipy_harness.extensions import BeforeAgentStartResult\n"
        "def activate(api):\n"
        "    @api.on('before_agent_start')\n"
        "    def inject(event, ctx):\n"
        "        return BeforeAgentStartResult(append_system_prompt='INJECTED')\n",
    )
    p2 = _run(ws2, "hello\n")
    checks.append(
        Check(
            "before_agent_start_injects",
            bool(p2.requests) and "INJECTED" in (p2.requests[0].system_prompt or ""),
            "before_agent_start injects into the system prompt",
        )
    )

    ws3 = base / "send"
    ws3.mkdir()
    _write(
        ws3,
        "trigger",
        "def activate(api):\n"
        "    def cmd(ctx, args):\n"
        "        api.send_user_message('probe prompt')\n"
        "    api.register_command('go', 'g', cmd)\n",
    )
    p3 = _run(ws3, "/go\n")
    checks.append(
        Check(
            "send_user_message_turn",
            len(p3.requests) == 1 and "probe prompt" in p3.requests[0].user_prompt,
            "send_user_message enqueues a deterministic turn",
        )
    )

    ws5 = base / "bound"
    ws5.mkdir()
    _write(
        ws5,
        "big",
        "from pipy_harness.extensions import BeforeAgentStartResult\n"
        "def activate(api):\n"
        "    @api.on('before_agent_start')\n"
        "    def inject(event, ctx):\n"
        "        return BeforeAgentStartResult(append_system_prompt='X' * 200000)\n",
    )
    p5 = _run(ws5, "hello\n")
    checks.append(
        Check(
            "injection_bounded",
            bool(p5.requests) and len(p5.requests[0].system_prompt or "") < 100000,
            "before_agent_start injection is size-bounded",
        )
    )

    ws4 = base / "crash"
    ws4.mkdir()
    _write(
        ws4,
        "boom",
        "def activate(api):\n"
        "    @api.on('input')\n"
        "    def t(event, ctx):\n"
        "        raise RuntimeError('boom')\n",
    )
    p4 = _run(ws4, "keepme\n")
    checks.append(
        Check(
            "input_hook_failsafe",
            bool(p4.requests) and "keepme" in p4.requests[0].user_prompt,
            "crashing input hook leaves the prompt unchanged",
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
