"""Parity row D8 behavior check: image/binary attachment loading.

Seeds a workspace PNG and drives the product tool-loop REPL
(``NativeToolReplSession``) with a real ``@image:`` prompt, capturing the
``ProviderRequest`` the provider receives. It proves:

  * the image reaches the provider as a bounded, type-validated attachment
    whose base64 round-trips back to the exact on-disk bytes;
  * a multimodal adapter (Anthropic) renders it as a native image content
    block on the current user message;
  * a non-image binary attachment fails closed (no provider attachment);
  * the loop reports a safe loaded-image counter and never leaks the raw
    base64 image bytes through its result object (the metadata-first contract).

Exits 0 when every behavior holds, 1 otherwise. No real network or AI calls.
"""

from __future__ import annotations

import base64
import hashlib
import io
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native.anthropic_provider import _messages_payload
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.tool_loop_session import NativeToolReplSession

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_B64 = base64.b64encode(_PNG).decode("ascii")


@dataclass
class _CapturingProvider:
    requests: list[ProviderRequest] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "capturing-fake"

    @property
    def model_id(self) -> str:
        return "capturing-model"

    @property
    def supports_tool_calls(self) -> bool:
        return True

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


def _run(prompt: str, *, seed_png: bool, seed_blob: bool):
    cwd = Path(tempfile.mkdtemp())
    if seed_png:
        (cwd / "shot.png").write_bytes(_PNG)
    if seed_blob:
        (cwd / "blob.bin").write_bytes(b"\x00\x01\x02 not an image at all")
    provider = _CapturingProvider()
    session = NativeToolReplSession(provider=provider, tool_registry={})
    result = session.run(
        workspace_root=cwd,
        input_stream=io.StringIO(f"{prompt}\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    return provider, result


def _image_reaches_provider_and_result_is_safe() -> bool:
    provider, result = _run("describe @image:shot.png", seed_png=True, seed_blob=False)
    if not provider.requests:
        return False
    attachments = provider.requests[0].attachments
    if len(attachments) != 1:
        return False
    att = attachments[0]
    if att.media_type != "image/png":
        return False
    if base64.b64decode(att.data_base64) != _PNG:
        return False
    if att.sha256 != hashlib.sha256(_PNG).hexdigest():
        return False
    # A multimodal adapter renders it as a native image block.
    payload = _messages_payload(provider.requests[0])
    user = payload[-1]
    image_blocks = [b for b in user["content"] if b.get("type") == "image"]
    if len(image_blocks) != 1:
        return False
    # Safe counter is recorded; the metadata-first result never leaks raw bytes.
    if result.image_attachment_loaded_count != 1:
        return False
    if _B64 in repr(result):
        return False
    return True


def _non_image_binary_fails_closed() -> bool:
    provider, result = _run("look @image:blob.bin", seed_png=False, seed_blob=True)
    if not provider.requests:
        return False
    # The non-image binary never becomes a provider attachment.
    if provider.requests[0].attachments != ():
        return False
    return result.image_attachment_loaded_count == 0


def main() -> int:
    if not _image_reaches_provider_and_result_is_safe():
        return 1
    if not _non_image_binary_fails_closed():
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
