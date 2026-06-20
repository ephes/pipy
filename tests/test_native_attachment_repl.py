"""Parity row D8: ``@image:`` attachments through the real REPL product path.

Proves the tool-loop REPL resolves ``@image:`` references into provider-visible
image attachments, and that the metadata-first archive never receives the raw
image bytes.
"""

from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.tool_loop_session import NativeToolReplSession

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


def test_tool_loop_repl_attaches_image_without_leaking_bytes(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    (tmp_path / "shot.png").write_bytes(_PNG)
    provider = _CapturingProvider(tool_capable=True)
    session = NativeToolReplSession(
        provider=provider,
        tool_registry={},
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
