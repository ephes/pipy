"""Image attachment value object and loader for the native pipy runtime.

This module slopforks the useful subset of Pi's `ImageContent` shape
(`packages/ai/src/types.ts`) — a base64-or-bytes payload plus a MIME
type — through pipy-owned Python boundaries. Pipy keeps the metadata
boundary explicit:

- `ImageAttachment` is a value object carrying only safe metadata
  (workspace-relative path, sha256, byte length, MIME type).
- `load_image_attachment(path, *, workspace_root)` constructs that
  value object from a UTF-safe absolute path; bytes are read once
  inside the helper for hashing but the in-memory bytes are not
  retained on the value object.
- Providers that support vision (none today) can fetch the bytes at
  the boundary by calling `read_image_attachment_bytes(attachment)`,
  which re-reads the file from disk at serialization time. The
  pipy session archive only sees the safe metadata.

The closing parity-criterion row is D8 ("Image/binary attachment
loading"). Live wiring to a real vision provider is deferred to a
later track; this module ships the value object, the bounded
loader, and the `ProviderRequest.image_attachments` plumbing so the
boundary exists before a real provider needs it.
"""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar


IMAGE_ATTACHMENT_MAX_BYTES: int = 8 * 1024 * 1024
"""Per-attachment byte cap. Files larger than this are refused at the
loader boundary rather than read into memory."""

ALLOWED_IMAGE_MIME_PREFIXES: tuple[str, ...] = ("image/",)
"""Allowlist for the MIME-type prefix accepted by
`load_image_attachment`. Non-image MIME types are refused at the
loader boundary."""


@dataclass(frozen=True, slots=True)
class ImageAttachment:
    """Metadata-only value object describing one attached image file.

    Bytes are NOT retained on the value object; only the absolute
    file path and a precomputed sha256 + byte length identify the
    file. Providers that support vision read the bytes through
    `read_image_attachment_bytes(...)` at serialization time, so
    callers cannot smuggle raw bytes into archive payloads through
    this dataclass.
    """

    absolute_path: Path
    workspace_relative_path: str
    sha256: str
    byte_length: int
    mime_type: str

    SHA256_HEX_LENGTH: ClassVar[int] = 64

    def __post_init__(self) -> None:
        if not isinstance(self.absolute_path, Path):
            raise TypeError("ImageAttachment.absolute_path must be a Path")
        if not self.absolute_path.is_absolute():
            raise ValueError("ImageAttachment.absolute_path must be absolute")
        if not isinstance(self.workspace_relative_path, str) or not self.workspace_relative_path:
            raise ValueError("ImageAttachment.workspace_relative_path must be a non-empty string")
        if len(self.sha256) != self.SHA256_HEX_LENGTH:
            raise ValueError(
                f"ImageAttachment.sha256 must be {self.SHA256_HEX_LENGTH} hex chars"
            )
        if not isinstance(self.byte_length, int) or self.byte_length < 0:
            raise ValueError("ImageAttachment.byte_length must be a non-negative int")
        if not isinstance(self.mime_type, str) or "/" not in self.mime_type:
            raise ValueError("ImageAttachment.mime_type must be a MIME type string")

    def archive_metadata(self) -> dict[str, object]:
        """Return the dict allowed to ride in safe session payloads.

        The pipy session recorder may include this dict in an event;
        it intentionally does NOT include the absolute path, the raw
        bytes, or any path component outside the workspace.
        """

        return {
            "workspace_relative_path": self.workspace_relative_path,
            "sha256": self.sha256,
            "byte_length": self.byte_length,
            "mime_type": self.mime_type,
        }


class ImageAttachmentError(ValueError):
    """Raised when an image attachment cannot be loaded safely."""


def load_image_attachment(
    path: Path | str,
    *,
    workspace_root: Path,
) -> ImageAttachment:
    """Load metadata for one workspace-resident image file.

    `path` may be absolute or workspace-relative. The resolved file
    must live inside `workspace_root` (symlinks that resolve outside
    are refused), must be a regular file, must be smaller than
    `IMAGE_ATTACHMENT_MAX_BYTES`, and must have a MIME type starting
    with `image/`. The file is read once to compute the sha256, but
    the bytes are NOT retained on the returned value object.
    """

    if not isinstance(workspace_root, Path):
        raise TypeError("workspace_root must be a Path")
    resolved_workspace = workspace_root.expanduser().resolve()
    if not resolved_workspace.is_dir():
        raise ImageAttachmentError(
            f"workspace root is not a directory: {resolved_workspace}"
        )

    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = resolved_workspace / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ImageAttachmentError(f"image attachment not found: {candidate}") from exc

    try:
        resolved.relative_to(resolved_workspace)
    except ValueError as exc:
        raise ImageAttachmentError(
            "image attachment must live inside the workspace root"
        ) from exc

    if not resolved.is_file():
        raise ImageAttachmentError(f"image attachment is not a regular file: {resolved}")

    size = resolved.stat().st_size
    if size > IMAGE_ATTACHMENT_MAX_BYTES:
        raise ImageAttachmentError(
            f"image attachment exceeds {IMAGE_ATTACHMENT_MAX_BYTES} bytes"
        )

    guessed_mime, _ = mimetypes.guess_type(resolved.name)
    if guessed_mime is None or not any(
        guessed_mime.startswith(prefix) for prefix in ALLOWED_IMAGE_MIME_PREFIXES
    ):
        raise ImageAttachmentError(
            f"image attachment MIME type is not allowed: {guessed_mime!r}"
        )

    data = resolved.read_bytes()
    if len(data) != size:
        raise ImageAttachmentError(
            "image attachment size changed between stat and read"
        )

    sha256 = hashlib.sha256(data).hexdigest()
    workspace_relative_path = str(resolved.relative_to(resolved_workspace))

    return ImageAttachment(
        absolute_path=resolved,
        workspace_relative_path=workspace_relative_path,
        sha256=sha256,
        byte_length=size,
        mime_type=guessed_mime,
    )


def read_image_attachment_bytes(attachment: ImageAttachment) -> bytes:
    """Re-read the bytes for one attachment at provider serialization time.

    Raises `ImageAttachmentError` if the file no longer matches the
    sha256 or byte length recorded on the value object — for example,
    if the file was modified between loader and provider call.
    """

    if not attachment.absolute_path.is_file():
        raise ImageAttachmentError(
            f"image attachment file disappeared: {attachment.absolute_path}"
        )

    data = attachment.absolute_path.read_bytes()
    if len(data) != attachment.byte_length:
        raise ImageAttachmentError(
            "image attachment byte length changed since loading"
        )
    if hashlib.sha256(data).hexdigest() != attachment.sha256:
        raise ImageAttachmentError(
            "image attachment sha256 changed since loading"
        )
    return data
