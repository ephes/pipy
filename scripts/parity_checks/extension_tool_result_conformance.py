"""Hard conformance gate for the tool_result hook (slice 8).

Drives the real `NativeToolReplSession.run` with a stub provider that
calls the built-in `bash` tool, plus a tool_result hook, and asserts the
slice-8 invariants from `docs/extension-api.md`:

1. a tool_result hook transforms the bounded observation and the
   transformed content reaches the next model turn (and the renderer);
2. a crashing tool_result hook is fail-safe (the original content is
   preserved and the run completes).

Exits 0 when every check passes, 1 otherwise. No network (bash runs in a
temporary workspace).

Run:

    uv run python scripts/parity_checks/extension_tool_result_conformance.py --json
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
from pipy_harness.native.models import (
    ProviderRequest,
    ProviderResult,
    ProviderToolCall,
)
from pipy_harness.native.tool_loop_session import (
    NativeToolReplSession,
    production_tool_registry,
)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


class _Stub:
    name = "stub"
    model_id = "stub-model"

    def __init__(self, results: list[ProviderResult]) -> None:
        self._results = list(results)
        self.requests: list[ProviderRequest] = []

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        return self._results.pop(0)


def _result(*, tool_calls=(), final_text=None) -> ProviderResult:
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    return ProviderResult(
        status=HarnessStatus.SUCCEEDED,
        provider_name="stub",
        model_id="stub-model",
        started_at=now,
        ended_at=now,
        final_text=final_text,
        tool_calls=tool_calls,
    )


def _write(workspace: Path, name: str, body: str) -> None:
    ext = workspace / ".pipy" / "extensions"
    ext.mkdir(parents=True, exist_ok=True)
    (ext / f"{name}.py").write_text(body, encoding="utf-8")


def _run(workspace: Path):
    (workspace / "note.txt").write_text("hello-note\n", encoding="utf-8")
    call = ProviderToolCall(
        provider_correlation_id="c1",
        tool_name="bash",
        arguments_json=json.dumps({"command": "cat note.txt"}),
    )
    provider = _Stub([_result(tool_calls=(call,)), _result(final_text="ok")])
    session = NativeToolReplSession(
        provider=provider, tool_registry=production_tool_registry(), tool_budget=5
    )
    result = session.run(
        workspace_root=workspace,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    second = provider.requests[1]
    joined = " ".join(
        str(getattr(m, "content", "") or getattr(m, "output_text", ""))
        for m in second.messages
    )
    return result, joined


def run_checks(base: Path) -> list[Check]:
    checks: list[Check] = []

    ok = base / "ok"
    ok.mkdir()
    _write(
        ok,
        "wrap",
        "from pipy_harness.extensions import ToolResultTransform\n"
        "def activate(api):\n"
        "    @api.on('tool_result')\n"
        "    def t(event, ctx):\n"
        "        return ToolResultTransform(content='WRAPPED::' + event.content)\n",
    )
    _res, joined = _run(ok)
    checks.append(
        Check(
            "tool_result_transform",
            "WRAPPED::" in joined and "hello-note" in joined,
            "tool_result hook transforms the observation reaching the model",
        )
    )

    crash = base / "crash"
    crash.mkdir()
    _write(
        crash,
        "boom",
        "def activate(api):\n"
        "    @api.on('tool_result')\n"
        "    def t(event, ctx):\n"
        "        raise RuntimeError('x')\n",
    )
    res2, joined2 = _run(crash)
    checks.append(
        Check(
            "tool_result_failsafe",
            res2.status is HarnessStatus.SUCCEEDED and "hello-note" in joined2,
            "crashing tool_result hook preserves the original content",
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
