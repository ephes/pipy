"""Parity row D8: ``@image:`` attachments through the real REPL product paths.

Proves the no-tool and tool-loop REPLs resolve ``@image:`` references into
provider-visible image attachments, and that the metadata-first archive /
transcript never receive the raw image bytes.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.adapters.native import PipyNativeReplAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import HarnessStatus, RunRequest
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.tool_loop_session import NativeToolReplSession
from pipy_harness.native.transcripts import TranscriptSink
from pipy_harness.runner import HarnessRunner

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_B64 = base64.b64encode(_PNG).decode("ascii")
_SHA = hashlib.sha256(_PNG).hexdigest()


@dataclass
class _CapturingProvider:
    tool_capable: bool = False
    requests: list[ProviderRequest] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "capturing-fake"

    @property
    def model_id(self) -> str:
        return "capturing-model"

    @property
    def supports_tool_calls(self) -> bool:
        return self.tool_capable

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="ok",
            usage=None,
            metadata=None,
            tool_calls=(),
        )


def test_no_tool_repl_attaches_image_and_keeps_archive_safe(tmp_path: Path) -> None:
    (tmp_path / "shot.png").write_bytes(_PNG)
    provider = _CapturingProvider()
    adapter = PipyNativeReplAdapter(
        provider=provider,
        input_stream=io.StringIO("describe @image:shot.png\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    result = HarnessRunner(adapter=adapter).run(
        RunRequest(
            agent="pipy-native",
            slug="d8-no-tool",
            command=[],
            cwd=tmp_path,
            goal="d8",
            root=tmp_path / "archive",
            capture_policy=CapturePolicy(),
        )
    )
    # Provider received the image as a bounded attachment.
    assert len(provider.requests) == 1
    attachments = provider.requests[0].attachments
    assert len(attachments) == 1
    assert attachments[0].media_type == "image/png"
    assert base64.b64decode(attachments[0].data_base64) == _PNG

    archive = result.record.jsonl_path.read_text(encoding="utf-8")
    # Safe metadata recorded; raw image bytes never archived.
    assert _SHA in archive
    assert _B64 not in archive
    events = [json.loads(line) for line in archive.splitlines()]
    resolved = [e for e in events if e.get("type") == "native.image_attachment.resolved"]
    assert resolved
    assert resolved[0]["payload"]["image_attachment_loaded_count"] == 1


def test_tool_loop_repl_attaches_image_without_leaking_to_transcript(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    (tmp_path / "shot.png").write_bytes(_PNG)
    provider = _CapturingProvider(tool_capable=True)
    sink = TranscriptSink(transcript_id="d8-tool-loop", directory=tmp_path)
    session = NativeToolReplSession(
        provider=provider,
        tool_registry={},
        transcript_sink=sink,
    )
    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("describe @image:shot.png\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    # Provider received the image on the (first) request of the turn.
    assert provider.requests
    assert len(provider.requests[0].attachments) == 1
    assert provider.requests[0].attachments[0].sha256 == _SHA
    # The result counters are safe; no raw bytes anywhere.
    assert result.image_attachment_loaded_count == 1
    assert _B64 not in repr(result)
    # The opt-in transcript sidecar records only the literal user text.
    transcript = sink.path.read_text(encoding="utf-8")
    assert "describe @image:shot.png" in transcript
    assert _B64 not in transcript


def test_no_tool_repl_non_image_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02 not an image")
    provider = _CapturingProvider()
    adapter = PipyNativeReplAdapter(
        provider=provider,
        input_stream=io.StringIO("look @image:blob.bin\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    HarnessRunner(adapter=adapter).run(
        RunRequest(
            agent="pipy-native",
            slug="d8-fail-closed",
            command=[],
            cwd=tmp_path,
            goal="d8",
            root=tmp_path / "archive",
            capture_policy=CapturePolicy(),
        )
    )
    assert provider.requests
    assert provider.requests[0].attachments == ()
