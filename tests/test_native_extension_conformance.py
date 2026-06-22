"""Slice 10: golden conformance extension product-path proof.

Drives the real `NativeToolReplSession.run` with the golden conformance
extension (`docs/examples/extensions/pipy-extension-conformance.py`) and a
deterministic provider that calls the registered `conformance_probe` tool.
A single `/pipy-extension-conformance` trigger exercises command + tool
registration/execution, the lifecycle events, input, before_agent_start,
tool_call, tool_result, and minimal UI, writing safe feature markers to a
proof JSONL file. Asserts every required marker is present and the proof
file (and the metadata result) leak no prompt bodies / tool args / tool
results / UI text / injected context.
"""

from __future__ import annotations

import io
import json
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
    Path(__file__).resolve().parents[1]
    / "docs"
    / "examples"
    / "extensions"
    / "pipy-extension-conformance.py"
)


class _ConformanceProvider:
    """Calls `conformance_probe` on the first turn, then finishes."""

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
        tool_calls: tuple[ProviderToolCall, ...] = ()
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


def _markers(proof: Path) -> set[str]:
    features: set[str] = set()
    for line in proof.read_text(encoding="utf-8").splitlines():
        if line.strip():
            features.add(json.loads(line)["feature"])
    return features


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


def test_golden_conformance_extension(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    proof = tmp_path / "proof.jsonl"
    monkeypatch.setenv("PIPY_EXTENSION_CONFORMANCE_PROOF", str(proof))
    sessions_root = tmp_path / "sessions"
    monkeypatch.setenv("PIPY_NATIVE_SESSIONS_ROOT", str(sessions_root))

    ext = tmp_path / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "pipy-extension-conformance.py").write_text(
        _GOLDEN.read_text(encoding="utf-8"), encoding="utf-8"
    )

    provider = _ConformanceProvider()
    session = NativeToolReplSession(
        provider=provider, tool_registry=production_tool_registry(), tool_budget=5
    )
    error_stream = io.StringIO()
    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/pipy-extension-conformance\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    # Every required feature marker is present.
    required = {
        "session_start",
        "command_handler",
        "input",
        "before_agent_start",
        "agent_start",
        "turn_start",
        "tool_call",
        "tool_execute",
        "tool_result",
        "message_renderer_component",
        "turn_end",
        "agent_end",
        "session_shutdown",
    }
    assert required <= _markers(proof)

    # before_agent_start modification reached the provider request.
    assert any(
        "CONFORMANCE_CONTEXT" in (r.system_prompt or "") for r in provider.requests
    )
    # The tool_result patch reached the model-visible observation.
    second = provider.requests[1]
    joined = " ".join(
        str(getattr(m, "content", "") or getattr(m, "output_text", ""))
        for m in second.messages
    )
    assert "PATCHED::probe-output" in joined
    # ctx.ui.notify surfaced to the live UI.
    assert "conformance probe ran" in error_stream.getvalue()
    assert "conformance command ran" in error_stream.getvalue()

    # Archive privacy: neither the proof file nor the default session
    # archive may carry extension side-channel data -- proof-file markers,
    # ctx.ui.notify text, the injected context, or the proof file's
    # contents. (The proof is a side channel; UI text is live-only.)
    sensitive = (
        "run conformance probe",  # queued prompt body
        "SENTINEL_TOOL_ARG_9z7",  # unique tool argument value
        "SENTINEL_PROVIDER_FINAL_9z7",  # unique provider final text
        "command_handler",  # proof marker
        "tool_execute",  # proof marker
        "before_agent_start",  # proof marker
        "tool_result",  # proof marker
        "session_shutdown",  # proof marker
        "conformance probe ran",  # ctx.ui.notify text
        "conformance command ran",  # ctx.ui.notify text
        "probe-output",  # tool result content the extension produced
        "PATCHED::",  # tool_result transform
        "CONFORMANCE_CONTEXT",  # before_agent_start injection
        # Rich renderer BODY: live-rendered output, never archived / never
        # written to any store -- a real privacy guarantee.
        "PIPY_MSGBODY_9f3a2c",
        # Rich renderer DATA: the append_entry payload is product data that
        # lives in the native session-tree store when a session persists; the
        # real guarantee is it must not leak to the proof side-channel. This
        # session is non-persisting (persist=False), so its absence from the
        # archive scan is incidental defense-in-depth, not an archive exclusion.
        "PIPY_MSGDATA_7b1e44",
        str(proof),  # the proof file path itself
    )
    archive_blob = _archive_blob(sessions_root)
    result_blob = str(result)
    for leaked in sensitive:
        assert leaked not in archive_blob
        # The metadata-only result object carries no bodies either.
        assert leaked not in result_blob

    # The proof file is metadata-only (no prompt/tool-arg/provider/UI bodies).
    proof_text = proof.read_text(encoding="utf-8")
    for leaked in (
        "run conformance probe",
        "SENTINEL_TOOL_ARG_9z7",
        "SENTINEL_PROVIDER_FINAL_9z7",
        "probe-output",
        "PATCHED::",
        "CONFORMANCE_CONTEXT",
        "conformance probe ran",
        "conformance command ran",
        # BODY: live-rendered output, never on any store. DATA: append_entry
        # payload -- product data that may live in the persisted session-tree
        # store, but must never leak into the proof/metadata side-channel.
        "PIPY_MSGBODY_9f3a2c",
        "PIPY_MSGDATA_7b1e44",
    ):
        assert leaked not in proof_text

    assert result.status is HarnessStatus.SUCCEEDED
    assert result.tool_invocation_count == 1
