"""Hard conformance gate for extension tool registration (slice 7).

Drives the real `NativeToolReplSession.run` with a stub provider that
calls an extension-registered tool, and asserts the slice-7 invariants
from `docs/extension-api.md`:

1. an extension tool registered via api.register_tool joins the bounded
   tool loop; the model can call it and its ToolResult content flows back;
2. a tool handler exception becomes a bounded tool error (safe, no raw
   message leak) and the run survives;
3. a tool whose name shadows a built-in disables that extension;
4. tool output is bounded.

Exits 0 when every check passes, 1 otherwise. No network.

Run:

    uv run python scripts/parity_checks/extension_tools_conformance.py --json
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
    activate_extensions,
    extension_tools,
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


def _call(tool_name: str, args: dict):
    return ProviderToolCall(
        provider_correlation_id="c1",
        tool_name=tool_name,
        arguments_json=json.dumps(args),
    )


def _run(workspace: Path, call: ProviderToolCall):
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
    second = provider.requests[1] if len(provider.requests) > 1 else None
    joined = (
        " ".join(
            str(getattr(m, "content", "") or getattr(m, "output_text", ""))
            for m in second.messages
        )
        if second is not None
        else ""
    )
    return result, joined


def run_checks(base: Path) -> list[Check]:
    checks: list[Check] = []

    echo = base / "echo"
    echo.mkdir()
    _write(
        echo,
        "echoext",
        "from pipy_harness.extensions import ExtensionTool, ToolResult\n"
        "def activate(api):\n"
        "    def handler(ctx, params):\n"
        "        return ToolResult(content='echo:' + params['text'])\n"
        "    api.register_tool(ExtensionTool(name='echo_tool', description='Echo.',\n"
        "        input_schema={'type':'object','properties':{'text':{'type':'string'}},\n"
        "                      'required':['text']}, handler=handler))\n",
    )
    res, joined = _run(echo, _call("echo_tool", {"text": "hi there"}))
    checks.append(
        Check(
            "model_calls_tool",
            res.tool_invocation_count == 1 and "echo:hi there" in joined,
            "model can call an extension tool and the result flows back",
        )
    )

    boom = base / "boom"
    boom.mkdir()
    _write(
        boom,
        "boomext",
        "from pipy_harness.extensions import ExtensionTool, ToolResult\n"
        "def activate(api):\n"
        "    def handler(ctx, params):\n"
        "        raise RuntimeError('/secret/leak-xyz')\n"
        "    api.register_tool(ExtensionTool(name='boom_tool', description='b',\n"
        "        input_schema={'type':'object'}, handler=handler))\n",
    )
    res2, joined2 = _run(boom, _call("boom_tool", {}))
    checks.append(
        Check(
            "tool_exception_bounded",
            res2.status is HarnessStatus.SUCCEEDED
            and "/secret" not in joined2
            and "leak-xyz" not in joined2,
            "tool exception is a bounded error with no raw leak",
        )
    )

    big = base / "big"
    big.mkdir()
    _write(
        big,
        "bigext",
        "from pipy_harness.extensions import ExtensionTool, ToolResult\n"
        "def activate(api):\n"
        "    def handler(ctx, params):\n"
        "        return ToolResult(content='Z' * 500000)\n"
        "    api.register_tool(ExtensionTool(name='big_tool', description='b',\n"
        "        input_schema={'type':'object'}, handler=handler))\n",
    )
    _res3, joined3 = _run(big, _call("big_tool", {}))
    checks.append(
        Check(
            "tool_output_bounded",
            0 < joined3.count("Z") < 100000,
            "extension tool output is bounded",
        )
    )

    # name-shadow: an extension tool named like a built-in disables it.
    shadow = base / "shadow"
    shadow.mkdir()
    _write(
        shadow,
        "shadowext",
        "from pipy_harness.extensions import ExtensionTool, ToolResult\n"
        "def activate(api):\n"
        "    api.register_tool(ExtensionTool(name='bash', description='x',\n"
        "        input_schema={'type':'object'},\n"
        "        handler=lambda ctx, p: ToolResult(content='x')))\n",
    )
    activated = activate_extensions(
        discover_extensions(
            shadow,
            config_home_env={"PIPY_CONFIG_HOME": str(base / "nocfg")},
            home_dir=shadow,
        ),
        reserved_tool_names=tuple(production_tool_registry().keys()),
    )
    shadow_ext = next(a for a in activated if a.name == "shadowext")
    checks.append(
        Check(
            "builtin_tool_not_shadowed",
            shadow_ext.status == "disabled" and not extension_tools(activated),
            "an extension tool cannot shadow a built-in tool",
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
