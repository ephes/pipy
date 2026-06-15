"""Hard conformance gate for the extension tool_call policy hook (slice 4).

Drives the real `NativeToolReplSession.run` with a stub tool provider and
the production tool registry, plus the real hook dispatcher, and asserts
the slice-4 invariants from `docs/extension-api.md`:

1. a registered `tool_call` hook blocks a real `bash` tool call before
   execution (the dangerous command never runs);
2. the block is surfaced and the tool is not invoked;
3. a hook that returns nothing allows the call (the tool runs);
4. a crashing hook fails closed (blocks with a safe reason, no raw
   message leak);
5. the first hook to return a `ToolBlock` wins.

Exits 0 when every check passes, 1 otherwise. No network (bash runs in a
temporary workspace).

Run:

    uv run python scripts/parity_checks/extension_tool_call_conformance.py --json
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
from pipy_harness.native.extension_runtime import (
    ToolBlock,
    activate_extensions,
    dispatch_tool_call_hooks,
    extension_tool_call_hooks,
)
from pipy_harness.native.extensions import discover_extensions
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
    def __init__(self, results: list[ProviderResult]) -> None:
        self._results = list(results)
        self.name = "stub"
        self.model_id = "stub-model"
        self.requests: list[ProviderRequest] = []

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        return self._results.pop(0)


def _result(*, tool_calls=(), final_text=None) -> ProviderResult:
    now = datetime.now(UTC)
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


def _run_bash(workspace: Path, command: str):
    call = ProviderToolCall(
        provider_correlation_id="c1",
        tool_name="bash",
        arguments_json=json.dumps({"command": command}),
    )
    provider = _Stub([_result(tool_calls=(call,)), _result(final_text="done")])
    session = NativeToolReplSession(
        provider=provider, tool_registry=production_tool_registry(), tool_budget=10
    )
    error_stream = io.StringIO()
    result = session.run(
        workspace_root=workspace,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    return result, error_stream.getvalue()


def run_checks(base: Path) -> list[Check]:
    checks: list[Check] = []

    # Blocking workspace.
    block_ws = base / "block"
    block_ws.mkdir()
    _write(
        block_ws,
        "guard",
        "from pipy_harness.extensions import ToolBlock\n"
        "def activate(api):\n"
        "    @api.on('tool_call')\n"
        "    def gate(event, ctx):\n"
        "        if 'rm -rf' in event.input.get('command', ''):\n"
        "            return ToolBlock(reason='blocked')\n",
    )
    victim = block_ws / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    result, err = _run_bash(block_ws, f"rm -rf {victim}")
    checks.append(
        Check(
            "blocks_real_tool",
            victim.exists()
            and "blocked by extension: blocked" in err
            and result.tool_invocation_count == 0,
            "tool_call hook blocks a real bash call before execution",
        )
    )

    # Allowing workspace (no block) -> the tool runs.
    allow_ws = base / "allow"
    allow_ws.mkdir()
    _write(
        allow_ws,
        "guard",
        "from pipy_harness.extensions import ToolBlock\n"
        "def activate(api):\n"
        "    @api.on('tool_call')\n"
        "    def gate(event, ctx):\n"
        "        if 'rm -rf' in event.input.get('command', ''):\n"
        "            return ToolBlock(reason='blocked')\n",
    )
    (allow_ws / "note.txt").write_text("hello-allow\n", encoding="utf-8")
    allow_result, _ = _run_bash(allow_ws, "cat note.txt")
    checks.append(
        Check(
            "allows_when_no_block",
            allow_result.tool_invocation_count == 1,
            "non-blocking hook lets the tool run",
        )
    )

    # Dispatcher-level: crashing hook fails closed, no raw leak.
    crash_ws = base / "crash"
    crash_ws.mkdir()
    _write(
        crash_ws,
        "crashy",
        "def activate(api):\n"
        "    @api.on('tool_call')\n"
        "    def gate(event, ctx):\n"
        "        raise RuntimeError('/secret/leak-xyz')\n",
    )
    crash_hooks = extension_tool_call_hooks(
        activate_extensions(
            discover_extensions(
                crash_ws,
                config_home_env={"PIPY_CONFIG_HOME": str(base / "nocfg")},
                home_dir=crash_ws,
            )
        )
    )
    crash_block = dispatch_tool_call_hooks(
        crash_hooks, tool_name="read", tool_input={}, cwd=str(crash_ws), has_ui=False
    )
    checks.append(
        Check(
            "crash_fails_closed",
            isinstance(crash_block, ToolBlock)
            and "/secret" not in crash_block.reason
            and "leak-xyz" not in crash_block.reason,
            "crashing hook fails closed with a safe reason",
        )
    )

    # Two extensions both block; the first registered hook wins.
    order_ws = base / "order"
    order_ws.mkdir()
    for name, reason in (("aaa", "first"), ("bbb", "second")):
        _write(
            order_ws,
            name,
            "from pipy_harness.extensions import ToolBlock\n"
            "def activate(api):\n"
            "    @api.on('tool_call')\n"
            "    def gate(event, ctx):\n"
            f"        return ToolBlock(reason='{reason}')\n",
        )
    order_hooks = extension_tool_call_hooks(
        activate_extensions(
            discover_extensions(
                order_ws,
                config_home_env={"PIPY_CONFIG_HOME": str(base / "nocfg2")},
                home_dir=order_ws,
            )
        )
    )
    order_block = dispatch_tool_call_hooks(
        order_hooks, tool_name="read", tool_input={}, cwd=str(order_ws), has_ui=False
    )
    checks.append(
        Check(
            "first_block_wins",
            isinstance(order_block, ToolBlock) and order_block.reason == "first",
            "first registered tool_call hook to block wins",
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
