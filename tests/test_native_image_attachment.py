"""Image attachment loader and value-object contract tests (D8 parity)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pipy_harness.native.image_attachment import (
    ALLOWED_IMAGE_MIME_PREFIXES,
    IMAGE_ATTACHMENT_MAX_BYTES,
    ImageAttachment,
    ImageAttachmentError,
    load_image_attachment,
    read_image_attachment_bytes,
)


PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def _write_png(path: Path, body: bytes = b"") -> bytes:
    data = PNG_HEADER + body
    path.write_bytes(data)
    return data


def test_image_attachment_archive_metadata_excludes_absolute_path(tmp_path: Path) -> None:
    attachment = ImageAttachment(
        absolute_path=(tmp_path / "img.png").resolve(),
        workspace_relative_path="img.png",
        sha256="a" * 64,
        byte_length=42,
        mime_type="image/png",
    )

    payload = attachment.archive_metadata()

    assert payload == {
        "workspace_relative_path": "img.png",
        "sha256": "a" * 64,
        "byte_length": 42,
        "mime_type": "image/png",
    }
    assert str(tmp_path) not in str(payload)


def test_image_attachment_rejects_relative_absolute_path() -> None:
    with pytest.raises(ValueError):
        ImageAttachment(
            absolute_path=Path("relative.png"),
            workspace_relative_path="relative.png",
            sha256="a" * 64,
            byte_length=1,
            mime_type="image/png",
        )


def test_image_attachment_rejects_bad_sha256_length() -> None:
    with pytest.raises(ValueError):
        ImageAttachment(
            absolute_path=Path("/tmp/img.png"),
            workspace_relative_path="img.png",
            sha256="too-short",
            byte_length=1,
            mime_type="image/png",
        )


def test_load_image_attachment_returns_safe_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "screenshot.png"
    data = _write_png(target, b"PIPY-TEST-PAYLOAD")

    attachment = load_image_attachment(target, workspace_root=workspace)

    assert attachment.workspace_relative_path == "screenshot.png"
    assert attachment.sha256 == hashlib.sha256(data).hexdigest()
    assert attachment.byte_length == len(data)
    assert attachment.mime_type.startswith(ALLOWED_IMAGE_MIME_PREFIXES)


def test_load_image_attachment_accepts_workspace_relative_string(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "img.png"
    _write_png(target)

    attachment = load_image_attachment("img.png", workspace_root=workspace)

    assert attachment.workspace_relative_path == "img.png"


def test_load_image_attachment_refuses_path_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.png"
    _write_png(outside)

    with pytest.raises(ImageAttachmentError):
        load_image_attachment(outside, workspace_root=workspace)


def test_load_image_attachment_refuses_missing_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ImageAttachmentError):
        load_image_attachment(workspace / "ghost.png", workspace_root=workspace)


def test_load_image_attachment_refuses_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    nested = workspace / "nested.png"
    nested.mkdir()

    with pytest.raises(ImageAttachmentError):
        load_image_attachment(nested, workspace_root=workspace)


def test_load_image_attachment_refuses_oversize_file(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "huge.png"
    target.write_bytes(PNG_HEADER + b"\x00" * 64)

    monkeypatch.setattr(
        "pipy_harness.native.image_attachment.IMAGE_ATTACHMENT_MAX_BYTES", 8
    )

    with pytest.raises(ImageAttachmentError):
        load_image_attachment(target, workspace_root=workspace)


def test_load_image_attachment_refuses_non_image_mime(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "notes.txt"
    target.write_text("hello", encoding="utf-8")

    with pytest.raises(ImageAttachmentError):
        load_image_attachment(target, workspace_root=workspace)


def test_load_image_attachment_refuses_symlink_escaping_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.png"
    _write_png(outside, b"OUTSIDE")
    symlink = workspace / "escape.png"
    try:
        symlink.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation not permitted on this platform")

    with pytest.raises(ImageAttachmentError):
        load_image_attachment(symlink, workspace_root=workspace)


def test_image_attachment_max_bytes_default_is_sane() -> None:
    assert IMAGE_ATTACHMENT_MAX_BYTES >= 1 * 1024 * 1024
    assert IMAGE_ATTACHMENT_MAX_BYTES <= 64 * 1024 * 1024


def test_read_image_attachment_bytes_round_trips_disk_content(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "img.png"
    data = _write_png(target, b"DATA")
    attachment = load_image_attachment(target, workspace_root=workspace)

    assert read_image_attachment_bytes(attachment) == data


def test_read_image_attachment_bytes_detects_post_load_mutation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "img.png"
    _write_png(target, b"ORIGINAL")
    attachment = load_image_attachment(target, workspace_root=workspace)

    target.write_bytes(PNG_HEADER + b"MUTATED")

    with pytest.raises(ImageAttachmentError):
        read_image_attachment_bytes(attachment)


def test_read_image_attachment_bytes_detects_deleted_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "img.png"
    _write_png(target)
    attachment = load_image_attachment(target, workspace_root=workspace)

    target.unlink()

    with pytest.raises(ImageAttachmentError):
        read_image_attachment_bytes(attachment)


def test_provider_request_carries_image_attachments_field(tmp_path: Path) -> None:
    from pipy_harness.native.models import ProviderRequest

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "shot.png"
    _write_png(target, b"PNG")
    attachment = load_image_attachment(target, workspace_root=workspace)

    request = ProviderRequest(
        system_prompt="s",
        user_prompt="u",
        provider_name="fake",
        model_id="fake-native-bootstrap",
        cwd=workspace,
        image_attachments=(attachment,),
    )

    assert request.image_attachments == (attachment,)
    assert request.image_attachments[0].archive_metadata() == {
        "workspace_relative_path": "shot.png",
        "sha256": attachment.sha256,
        "byte_length": attachment.byte_length,
        "mime_type": "image/png",
    }
