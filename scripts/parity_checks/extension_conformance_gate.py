"""Golden conformance gate for the pipy extension API (slice 10).

Drives the real `NativeToolReplSession.run` with the golden conformance
extension (`docs/examples/extensions/pipy-extension-conformance.py`) and a
deterministic provider that calls the registered `conformance_probe`
tool. A single `/pipy-extension-conformance` trigger exercises the whole
API and writes safe feature markers to a proof JSONL file. Asserts:

1. every required feature marker is present (command + tool
   registration/execution, lifecycle, input, before_agent_start,
   tool_call, tool_result, agent_end, session_shutdown);
2. the before_agent_start injection reached the provider request;
3. the tool_result patch reached the model-visible observation;
4. ctx.ui.notify surfaced to the live UI;
5. archive privacy: the proof file leaks no prompt bodies / tool result
   content / injected context / UI text.

Exits 0 when every check passes, 1 otherwise. No network.

Run:

    uv run python scripts/parity_checks/extension_conformance_gate.py --json
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

_GOLDEN = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "examples"
    / "extensions"
    / "pipy-extension-conformance.py"
)

_REQUIRED = {
    "session_start",
    "command_handler",
    "input",
    "before_agent_start",
    "agent_start",
    "turn_start",
    "tool_call",
    "render_call",
    "tool_execute",
    "tool_result",
    "render_result",
    "message_renderer_component",
    "send_message",
    "editor_noop",
    "turn_end",
    "agent_end",
    "session_shutdown",
    "set_widget",
    "set_header",
    "set_footer",
    "set_title",
    "set_working_indicator",
}


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


class _Provider:
    name = "stub"
    model_id = "stub-model"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []
        self._turn = 0

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        self._turn += 1
        now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
        tool_calls = ()
        final_text = None
        if self._turn == 1:
            tool_calls = (
                ProviderToolCall(
                    provider_correlation_id="c1",
                    tool_name="conformance_probe",
                    arguments_json=json.dumps({"probe_arg": "SENTINEL_TOOL_ARG_9z7"}),
                ),
            )
        else:
            final_text = "SENTINEL_PROVIDER_FINAL_9z7"
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text=final_text,
            tool_calls=tool_calls,
        )


def _archive_blob(root: Path) -> str:
    if not root.exists():
        return ""
    parts: list[str] = []
    for path in root.rglob("*"):
        if path.is_file():
            try:
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return "".join(parts)


def run_checks(workspace: Path, proof: Path, sessions_root: Path) -> list[Check]:
    ext = workspace / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "pipy-extension-conformance.py").write_text(
        _GOLDEN.read_text(encoding="utf-8"), encoding="utf-8"
    )

    provider = _Provider()
    session = NativeToolReplSession(
        provider=provider, tool_registry=production_tool_registry(), tool_budget=5
    )
    error_stream = io.StringIO()
    result = session.run(
        workspace_root=workspace,
        input_stream=io.StringIO("/pipy-extension-conformance\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    features: set[str] = set()
    if proof.exists():
        for line in proof.read_text(encoding="utf-8").splitlines():
            if line.strip():
                features.add(json.loads(line)["feature"])
    err = error_stream.getvalue()
    proof_text = proof.read_text(encoding="utf-8") if proof.exists() else ""
    second = provider.requests[1] if len(provider.requests) > 1 else None
    joined = (
        " ".join(
            str(getattr(m, "content", "") or getattr(m, "output_text", ""))
            for m in second.messages
        )
        if second is not None
        else ""
    )

    checks = [
        Check(
            "all_markers",
            _REQUIRED <= features,
            f"all required feature markers present ({len(_REQUIRED & features)}/"
            f"{len(_REQUIRED)})",
        ),
        Check(
            "before_agent_start_reached_provider",
            any("CONFORMANCE_CONTEXT" in (r.system_prompt or "") for r in provider.requests),
            "before_agent_start injection reached the provider request",
        ),
        Check(
            "tool_result_patch_reached_model",
            "PATCHED::probe-output" in joined,
            "tool_result patch reached the model-visible observation",
        ),
        Check(
            "ui_notify_surfaced",
            "conformance probe ran" in err and "conformance command ran" in err,
            "ctx.ui.notify surfaced to the live UI",
        ),
        Check(
            "archive_privacy",
            # Bodies (prompt / tool arg / provider / tool result / injected
            # context / UI text) must not appear in the proof file, the
            # default session archive, or the metadata result.
            all(
                body not in proof_text
                and body not in _archive_blob(sessions_root)
                and body not in str(result)
                for body in (
                    "run conformance probe",  # queued prompt body
                    "SENTINEL_TOOL_ARG_9z7",  # unique tool argument value
                    "SENTINEL_PROVIDER_FINAL_9z7",  # unique provider final text
                    "probe-output",  # tool result content
                    "PATCHED::",  # tool_result transform
                    "CONFORMANCE_CONTEXT",  # before_agent_start injection
                    "conformance probe ran",  # ctx.ui.notify text
                    "conformance command ran",
                    # Rich message renderer (slice C), two sentinels with
                    # different guarantees:
                    #
                    # BODY: the live-rendered component output. It is never
                    # archived and never written to any store -- a real privacy
                    # guarantee; its absence from proof + archive is meaningful.
                    "PIPY_MSGBODY_9f3a2c",
                    # DATA: the ctx.append_entry PAYLOAD = product data that
                    # legitimately lives in the native session-tree store when a
                    # session persists. The meaningful guarantee is that it must
                    # NOT leak into the PROOF/metadata side-channel. This
                    # conformance session is non-persisting
                    # (NativeSessionTree.create(persist=False)), so its absence
                    # from the archive scan is incidental defense-in-depth here,
                    # NOT proof of an archive exclusion.
                    "PIPY_MSGDATA_7b1e44",
                )
            )
            # Proof markers + the proof path are a side channel: present in
            # the proof file, but never in the default session archive.
            and all(
                marker not in _archive_blob(sessions_root)
                for marker in (
                    "command_handler",
                    "tool_execute",
                    "before_agent_start",
                    "tool_result",
                    "session_shutdown",
                    "set_widget",
                    "set_header",
                    "set_footer",
                    "set_title",
                    "set_working_indicator",
                    str(proof),
                )
            )
            and result.status is HarnessStatus.SUCCEEDED,
            "proof file + default session archive + result carry no prompt / tool "
            "arg / provider / UI / proof side-channel data",
        ),
    ]
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
        os.environ["PIPY_EXTENSION_CONFORMANCE_PROOF"] = str(base / "proof.jsonl")
        os.environ["PIPY_NATIVE_SESSIONS_ROOT"] = str(base / "sessions")
        checks = run_checks(workspace, base / "proof.jsonl", base / "sessions")

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
