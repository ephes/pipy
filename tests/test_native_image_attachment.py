"""Parity row D8: bounded, fail-closed image/binary attachment loading.

Covers the pure resolver in ``image_attachment.py``: ``@image:`` reference
parsing, the reused workspace path policy, magic-byte type validation, per
-attachment and per-turn size caps, fail-closed diagnostics, and — critically —
the archive-privacy contract that only safe metadata (media type, byte count,
sha256) ever leaves the boundary while the raw base64 image data stays on the
provider-visible value object alone.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from pipy_harness.native.image_attachment import (
    MAX_IMAGE_ATTACHMENT_BYTES,
    MAX_IMAGE_ATTACHMENTS_PER_TURN,
    ImageAttachmentResolution,
    ProviderImageAttachment,
    detect_image_media_type,
    parse_image_references,
    resolve_image_attachments,
)

# Minimal magic-byte headers for each supported image type.
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
_GIF = b"GIF89a" + b"\x00" * 32
_WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 32


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_detect_image_media_type_by_magic_bytes() -> None:
    assert detect_image_media_type(_PNG) == "image/png"
    assert detect_image_media_type(_JPEG) == "image/jpeg"
    assert detect_image_media_type(_GIF) == "image/gif"
    assert detect_image_media_type(_WEBP) == "image/webp"
    assert detect_image_media_type(b"not an image at all") is None
    assert detect_image_media_type(b"") is None


def test_parse_image_references_anchored_and_deduped() -> None:
    refs = parse_image_references("look at @image:a.png and (@img:b.jpg) and @image:a.png")
    assert refs == ("a.png", "b.jpg")
    # A bare @file (text) reference is not an image reference.
    assert parse_image_references("@file.txt @notes.md") == ()
    # An email-like token is not a reference.
    assert parse_image_references("mail me@image:host") == ()


def test_resolve_loads_supported_image_with_safe_metadata(tmp_path: Path) -> None:
    _write(tmp_path / "shot.png", _PNG)
    resolution = resolve_image_attachments("see @image:shot.png", workspace_root=tmp_path)
    assert resolution.used is True
    assert resolution.loaded_count == 1
    attachments = resolution.attachments()
    assert len(attachments) == 1
    att = attachments[0]
    assert att.media_type == "image/png"
    assert att.byte_count == len(_PNG)
    assert att.sha256 == hashlib.sha256(_PNG).hexdigest()
    # The base64 round-trips back to the original bytes.
    assert base64.b64decode(att.data_base64) == _PNG


def test_non_image_binary_fails_closed(tmp_path: Path) -> None:
    _write(tmp_path / "blob.bin", b"\x00\x01\x02\x03 not an image")
    resolution = resolve_image_attachments("@image:blob.bin", workspace_root=tmp_path)
    assert resolution.used is False
    assert resolution.failed_count == 1
    assert resolution.attachments() == ()
    diagnostics = resolution.diagnostics()
    assert any("skipped" in line for line in diagnostics)


def test_missing_reference_fails_closed(tmp_path: Path) -> None:
    resolution = resolve_image_attachments("@image:absent.png", workspace_root=tmp_path)
    assert resolution.used is False
    assert resolution.failed_count == 1


def test_oversized_image_fails_closed(tmp_path: Path) -> None:
    big = _PNG + b"\x00" * (MAX_IMAGE_ATTACHMENT_BYTES + 1)
    _write(tmp_path / "huge.png", big)
    resolution = resolve_image_attachments("@image:huge.png", workspace_root=tmp_path)
    assert resolution.used is False
    assert resolution.failed_count == 1


def test_git_path_refused(tmp_path: Path) -> None:
    _write(tmp_path / ".git" / "secret.png", _PNG)
    resolution = resolve_image_attachments("@image:.git/secret.png", workspace_root=tmp_path)
    assert resolution.used is False


def test_traversal_refused(tmp_path: Path) -> None:
    resolution = resolve_image_attachments(
        "@image:../escape.png", workspace_root=tmp_path / "ws"
    )
    assert resolution.used is False


def test_per_turn_reference_cap(tmp_path: Path) -> None:
    tokens = []
    for index in range(MAX_IMAGE_ATTACHMENTS_PER_TURN + 3):
        name = f"img{index}.png"
        _write(tmp_path / name, _PNG)
        tokens.append(f"@image:{name}")
    resolution = resolve_image_attachments(" ".join(tokens), workspace_root=tmp_path)
    assert resolution.loaded_count <= MAX_IMAGE_ATTACHMENTS_PER_TURN
    assert resolution.over_budget_count >= 1


def test_safe_metadata_excludes_raw_image_data(tmp_path: Path) -> None:
    _write(tmp_path / "a.png", _PNG)
    _write(tmp_path / "b.jpg", _JPEG)
    resolution = resolve_image_attachments(
        "@image:a.png @img:b.jpg", workspace_root=tmp_path
    )
    metadata = resolution.safe_metadata()
    # Counters and per-attachment hashes/types are safe; raw bytes are not.
    assert metadata["image_attachment_loaded_count"] == 2
    serialized = repr(metadata)
    png_b64 = base64.b64encode(_PNG).decode("ascii")
    jpg_b64 = base64.b64encode(_JPEG).decode("ascii")
    assert png_b64 not in serialized
    assert jpg_b64 not in serialized
    # Hashes ARE present (safe), proving identity without the payload.
    assert hashlib.sha256(_PNG).hexdigest() in serialized


def test_provider_image_attachment_safe_metadata_has_no_data() -> None:
    att = ProviderImageAttachment(
        media_type="image/png",
        data_base64=base64.b64encode(_PNG).decode("ascii"),
        byte_count=len(_PNG),
        sha256=hashlib.sha256(_PNG).hexdigest(),
        source_label="shot.png",
    )
    safe = att.safe_metadata()
    assert "data_base64" not in safe
    assert base64.b64encode(_PNG).decode("ascii") not in repr(safe)
    assert safe["media_type"] == "image/png"
    assert safe["sha256"] == hashlib.sha256(_PNG).hexdigest()


def test_empty_when_no_references(tmp_path: Path) -> None:
    resolution = resolve_image_attachments("just text", workspace_root=tmp_path)
    assert resolution == ImageAttachmentResolution()
    assert resolution.attachments() == ()
