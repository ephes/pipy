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

import pytest

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
    refs = parse_image_references(
        "look at @image:a.png and (@img:b.jpg) and @image:a.png"
    )
    assert refs == ("a.png", "b.jpg")
    # A bare @file (text) reference is not an image reference.
    assert parse_image_references("@file.txt @notes.md") == ()
    # An email-like token is not a reference.
    assert parse_image_references("mail me@image:host") == ()


def test_resolve_loads_supported_image_with_safe_metadata(tmp_path: Path) -> None:
    _write(tmp_path / "shot.png", _PNG)
    resolution = resolve_image_attachments(
        "see @image:shot.png", workspace_root=tmp_path
    )
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


def test_git_ignored_and_outside_paths_attach_like_pi_read(tmp_path: Path) -> None:
    # READ2: images resolve like Pi's read path (no deny list, any path).
    workspace = tmp_path / "ws"
    _write(workspace / ".git" / "a.png", _PNG)
    _write(workspace / ".gitignore", b"build/\n")
    _write(workspace / "build" / "b.png", _PNG)
    _write(tmp_path / "c.png", _PNG)
    resolution = resolve_image_attachments(
        f"@image:.git/a.png @image:build/b.png @image:../c.png @image:{tmp_path}/c.png",
        workspace_root=workspace,
    )
    assert resolution.failed_count == 0
    assert len(resolution.attachments()) == 4


def test_invalid_file_url_fails_closed_without_blocking_others(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "good.png", _PNG)
    resolution = resolve_image_attachments(
        "@image:file://[broken/image.png @image:good.png", workspace_root=tmp_path
    )
    assert resolution.failed_count == 1
    assert len(resolution.attachments()) == 1


def test_missing_traversal_target_fails_closed(tmp_path: Path) -> None:
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


@pytest.mark.parametrize("total,requested", [(64, 65), (48, 49)])
def test_growing_image_read_is_bounded(monkeypatch, tmp_path, total, requested):
    import io
    import os

    path = tmp_path / "growing.png"
    _write(path, _PNG)
    reads = []
    stream = io.BytesIO(_PNG + b"x" * 1000)

    class GrowingFile:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            stream.close()

        def fileno(self):
            return 123

        def read(self, size=-1):
            reads.append(size)
            return stream.read(size)

    monkeypatch.setattr(os, "fdopen", lambda *args, **kwargs: GrowingFile())
    monkeypatch.setattr(os, "open", lambda *args: 123)
    monkeypatch.setattr(os, "fstat", lambda fd: path.stat())
    resolution = resolve_image_attachments(
        "@image:growing.png",
        workspace_root=tmp_path,
        max_attachment_bytes=64,
        max_total_bytes=total,
    )
    assert resolution.attachments() == ()
    assert resolution.references[0].reason == "oversized_image"
    assert reads == [requested]
    assert stream.closed


def test_image_exact_aggregate_boundary(tmp_path):
    _write(tmp_path / "first.png", _PNG)
    _write(tmp_path / "second.png", _PNG)
    resolution = resolve_image_attachments(
        "@image:first.png @image:second.png",
        workspace_root=tmp_path,
        max_attachment_bytes=len(_PNG),
        max_total_bytes=2 * len(_PNG),
    )
    assert resolution.loaded_count == 2


@pytest.mark.parametrize("broken", [False, True])
def test_image_descriptor_validation_closes_on_rejection(monkeypatch, tmp_path, broken):
    import os
    import stat
    from types import SimpleNamespace

    _write(tmp_path / "image.png", _PNG)
    original_open = os.open
    original_fstat = os.fstat
    descriptors = []

    def opened(*args):
        fd = original_open(*args)
        descriptors.append(fd)
        return fd

    def inspected(fd):
        if broken:
            raise OSError("synthetic descriptor error")
        return SimpleNamespace(st_mode=stat.S_IFIFO)

    monkeypatch.setattr(os, "open", opened)
    monkeypatch.setattr(os, "fstat", inspected)
    resolution = resolve_image_attachments("@image:image.png", workspace_root=tmp_path)
    assert resolution.attachments() == ()
    assert resolution.references[0].reason == (
        "read_failed" if broken else "not_a_regular_file"
    )
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        original_fstat(descriptors[0])


def test_image_fdopen_failure_closes_descriptor(monkeypatch, tmp_path):
    import os

    _write(tmp_path / "image.png", _PNG)
    descriptors = []
    original_fdopen = os.fdopen

    def broken(fd, *args, **kwargs):
        descriptors.append(fd)
        raise OSError("synthetic fdopen failure")

    monkeypatch.setattr(os, "fdopen", broken)
    result = resolve_image_attachments("@image:image.png", workspace_root=tmp_path)
    assert result.references[0].reason == "read_failed"
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    monkeypatch.setattr(os, "fdopen", original_fdopen)


def test_real_image_growth_after_path_validation(monkeypatch, tmp_path):
    import os

    path = tmp_path / "image.png"
    _write(path, _PNG)
    original_open = os.open

    def grow(candidate, flags):
        with path.open("ab") as stream:
            stream.write(b"x" * 1000)
        return original_open(candidate, flags)

    monkeypatch.setattr(os, "open", grow)
    result = resolve_image_attachments(
        "@image:image.png",
        workspace_root=tmp_path,
        max_attachment_bytes=64,
        max_total_bytes=64,
    )
    assert result.attachments() == ()
    assert result.references[0].reason == "oversized_image"
