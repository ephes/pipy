"""Hard conformance gate for extension live-session hooks (slice 13).

Drives the real `NativeToolReplSession.run` with local Python extensions and
stub providers to prove Pi-shaped live-session extension behavior:

1. `before_provider_request` can transform a request and narrow active tools;
2. `user_bash` can provide a synthetic local shell result that reaches the next
   provider-visible prompt context;
3. `session_before_compact` can block a session operation fail-closed.

No network is used.

Run:

    uv run python scripts/parity_checks/extension_live_session_conformance.py --json
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
from pipy_harness.native.tool_loop_session import (
    NativeToolReplSession,
    production_tool_registry,
)


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


class _CapturingProvider:
    name = "stub"
    model_id = "stub-model"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="ok",
        )


def _write_ext(root: Path, name: str, body: str) -> None:
    ext = root / ".pipy" / "extensions"
    ext.mkdir(parents=True, exist_ok=True)
    (ext / f"{name}.py").write_text(body, encoding="utf-8")


def _run_request_hook(workspace: Path) -> Check:
    _write_ext(
        workspace,
        "request",
        "from pipy_harness.extensions import ProviderRequestTransform\n"
        "def activate(api):\n"
        "    @api.on('before_provider_request')\n"
        "    def before(event, ctx):\n"
        "        assert ctx.set_active_tools(['bash'])\n"
        "        assert ctx.set_model('fake/fake-native-bootstrap') is False\n"
        "        return ProviderRequestTransform(user_prompt=event.user_prompt + '::hook')\n",
    )
    provider = _CapturingProvider()
    result = NativeToolReplSession(
        provider=provider, tool_registry=production_tool_registry()
    ).run(
        workspace_root=workspace,
        input_stream=io.StringIO("hello\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    request = provider.requests[0] if provider.requests else None
    ok = (
        result.status is HarnessStatus.SUCCEEDED
        and request is not None
        and request.user_prompt == "hello::hook"
        and [tool.name for tool in request.available_tools] == ["bash"]
        and any(
            getattr(message, "content", "") == "hello::hook"
            for message in request.messages
        )
        and not any(
            getattr(message, "content", "") == "hello"
            for message in request.messages
        )
    )
    return Check(
        "before_provider_request_transform_and_tools",
        ok,
        "request hook transforms user prompt and narrows model-visible tools",
    )


def _run_user_bash(workspace: Path) -> Check:
    _write_ext(
        workspace,
        "shell",
        "from pipy_harness.extensions import UserBashDecision\n"
        "def activate(api):\n"
        "    @api.on('user_bash')\n"
        "    def shell(event, ctx):\n"
        "        return UserBashDecision(result='SYNTHETIC-OUTPUT\\n', "
        "exclude_from_context=False)\n",
    )
    provider = _CapturingProvider()
    result = NativeToolReplSession(provider=provider, tool_registry={}).run(
        workspace_root=workspace,
        input_stream=io.StringIO("!echo real\nask\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    request = provider.requests[0] if provider.requests else None
    body = ""
    if request is not None:
        body = " ".join(
            str(getattr(message, "content", "") or getattr(message, "output_text", ""))
            for message in request.messages
        )
    return Check(
        "user_bash_synthetic_result_context",
        result.status is HarnessStatus.SUCCEEDED and "SYNTHETIC-OUTPUT" in body,
        "user_bash hook synthetic result reaches next provider-visible context",
    )


def _run_compact_gate(workspace: Path) -> Check:
    _write_ext(
        workspace,
        "gate",
        "from pipy_harness.extensions import SessionDecision\n"
        "def activate(api):\n"
        "    @api.on('session_before_compact')\n"
        "    def compact(event, ctx):\n"
        "        return SessionDecision(allow=False, reason='no compact')\n",
    )
    err = io.StringIO()
    result = NativeToolReplSession(
        provider=_CapturingProvider(), tool_registry={}
    ).run(
        workspace_root=workspace,
        input_stream=io.StringIO("/compact\n"),
        output_stream=io.StringIO(),
        error_stream=err,
    )
    return Check(
        "session_before_compact_blocks",
        result.status is HarnessStatus.SUCCEEDED
        and "compact blocked by extension: no compact" in err.getvalue(),
        "session_before_compact blocks the local compaction command",
    )


def run_checks(base: Path) -> list[Check]:
    checks: list[Check] = []
    for name, runner in (
        ("request", _run_request_hook),
        ("bash", _run_user_bash),
        ("compact", _run_compact_gate),
    ):
        workspace = base / name
        workspace.mkdir()
        checks.append(runner(workspace))
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        os.environ["PIPY_CONFIG_HOME"] = str(base / "empty-global")
        checks = run_checks(base)

    passed = all(check.passed for check in checks)
    if args.json:
        print(
            json.dumps(
                {
                    "passed": passed,
                    "checks": [
                        {
                            "name": check.name,
                            "passed": check.passed,
                            "detail": check.detail,
                        }
                        for check in checks
                    ],
                },
                indent=2,
            )
        )
    else:
        for check in checks:
            status = "PASS" if check.passed else "FAIL"
            print(f"[{status}] {check.name}: {check.detail}")
        print("ALL PASS" if passed else "FAILURES PRESENT")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
