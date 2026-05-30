"""User-directed ``@image:`` attachment loading for the pipy-native REPLs.

Both pipy-native REPL modes let a genuine user prompt attach a workspace image
with ``@image:<path>`` (``@img:`` is accepted as an alias). This module turns
those references into bounded, fail-closed, *provider-visible* image content
that multimodal provider adapters (Anthropic, OpenAI Responses, Google Gemini)
render as native image blocks — while keeping the user's literal prompt text
intact and keeping the raw image bytes out of the metadata-first archive.

Design boundaries:

- Path policy is reused verbatim from the ``read`` tool's
  ``resolve_tool_path`` / ``_is_ignored_or_generated`` (workspace-relative or
  ``--read-root`` reference roots, ``.git``/``.gitignore`` defenses, traversal
  and shell-expansion refusal). No new path policy is introduced here.
- Loading is bounded: at most :data:`MAX_IMAGE_ATTACHMENTS_PER_TURN` images per
  turn, each at most :data:`MAX_IMAGE_ATTACHMENT_BYTES`, and at most
  :data:`MAX_TOTAL_IMAGE_ATTACHMENT_BYTES` in aggregate.
- Type validation is by magic bytes (not by extension): only PNG/JPEG/GIF/WebP
  are accepted. Arbitrary binary or non-image content fails closed.
- Failures fail closed with a short, safe reason label. One bad reference never
  blocks a good one and never injects unsafe content.
- Only safe metadata crosses the archive boundary via :meth:`safe_metadata`:
  counters plus each loaded image's media type, byte count, and sha256. The raw
  base64 image data lives only on :class:`ProviderImageAttachment` (carried in
  ``ProviderRequest`` and consumed by the adapter) and is never archived.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pipy_harness.native.read_only_tool import (
    _GENERATED_PARTS,
    _matches_root_ignore,
    resolve_tool_path,
)

MAX_IMAGE_ATTACHMENTS_PER_TURN: int = 4
MAX_IMAGE_ATTACHMENT_BYTES: int = 5 * 1024 * 1024
MAX_TOTAL_IMAGE_ATTACHMENT_BYTES: int = 16 * 1024 * 1024
_MAX_REFERENCE_PATH_LENGTH: int = 1024

SUPPORTED_IMAGE_MEDIA_TYPES: frozenset[str] = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)

# ``@image:`` (or ``@img:``) followed by a non-whitespace path, anchored to the
# start or to whitespace / opening punctuation so ordinary prose like
# ``me@image:host`` is never treated as a reference.
_IMAGE_REFERENCE_PATTERN = re.compile(
    r"""(?:^|(?<=[\s(\[{"']))@(?:image|img):(\S+)"""
)
_TRAILING_PUNCTUATION = ").,;:!?]}\"'"

_LOADED_REASON = "loaded"
_INVALID_REASON = "invalid_reference"
_MISSING_REASON = "missing_file"
_IGNORED_REASON = "ignored_or_generated"
_NOT_FILE_REASON = "not_a_regular_file"
_OVERSIZED_REASON = "oversized_image"
_UNSUPPORTED_REASON = "unsupported_or_non_image_type"
_BUDGET_REASON = "attachment_budget_exhausted"
_READ_FAILED_REASON = "read_failed"


@dataclass(frozen=True, slots=True)
class ProviderImageAttachment:
    """One loaded, provider-visible image attachment.

    ``data_base64`` is the only field that carries raw image content; it lives
    in memory and travels through ``ProviderRequest`` to the adapter alone.
    :meth:`safe_metadata` deliberately excludes it so the archive boundary only
    ever sees the media type, byte count, and content hash.
    """

    media_type: str
    data_base64: str
    byte_count: int
    sha256: str
    source_label: str

    def safe_metadata(self) -> dict[str, object]:
        return {
            "media_type": self.media_type,
            "byte_count": self.byte_count,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class ResolvedImageAttachment:
    """One attempted ``@image:`` reference and its bounded outcome."""

    raw: str
    loaded: bool
    reason: str
    attachment: ProviderImageAttachment | None = None


@dataclass(frozen=True, slots=True)
class ImageAttachmentResolution:
    """Aggregate outcome of resolving every ``@image:`` reference in a prompt."""

    references: tuple[ResolvedImageAttachment, ...] = ()
    over_budget_count: int = 0

    @property
    def reference_count(self) -> int:
        return len(self.references) + self.over_budget_count

    @property
    def loaded_count(self) -> int:
        return sum(1 for reference in self.references if reference.loaded)

    @property
    def failed_count(self) -> int:
        return sum(1 for reference in self.references if not reference.loaded)

    @property
    def used(self) -> bool:
        return self.loaded_count > 0

    def attachments(self) -> tuple[ProviderImageAttachment, ...]:
        """Return the loaded provider-visible attachments (raw data included)."""

        return tuple(
            reference.attachment
            for reference in self.references
            if reference.loaded and reference.attachment is not None
        )

    def diagnostics(self) -> tuple[str, ...]:
        """Return safe local diagnostic lines for the user's error stream."""

        lines: list[str] = []
        for reference in self.references:
            if reference.loaded:
                continue
            lines.append(
                f"pipy: @image:{_display_label(reference.raw)} attachment "
                f"skipped: {reference.reason}."
            )
        if self.over_budget_count:
            lines.append(
                "pipy: additional @image references ignored: per-turn limit "
                f"({MAX_IMAGE_ATTACHMENTS_PER_TURN})."
            )
        return tuple(lines)

    def safe_metadata(self) -> dict[str, object]:
        """Return archive-safe metadata only (no raw image data).

        Counters plus, for each loaded image, its media type / byte count /
        sha256. The raw base64 payload is never included.
        """

        loaded = self.attachments()
        return {
            "image_attachment_count": self.reference_count,
            "image_attachment_loaded_count": self.loaded_count,
            "image_attachment_failed_count": self.failed_count,
            "image_attachment_over_budget_count": self.over_budget_count,
            "image_attachment_total_bytes": sum(a.byte_count for a in loaded),
            "image_attachment_media_types": [a.media_type for a in loaded],
            "image_attachment_sha256s": [a.sha256 for a in loaded],
        }


def detect_image_media_type(data: bytes) -> str | None:
    """Return the media type for ``data`` by magic bytes, or None if unknown."""

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def parse_image_references(text: str) -> tuple[str, ...]:
    """Return ordered, de-duplicated ``@image:`` / ``@img:`` paths in ``text``."""

    seen: set[str] = set()
    ordered: list[str] = []
    for match in _IMAGE_REFERENCE_PATTERN.finditer(text):
        token = match.group(1).rstrip(_TRAILING_PUNCTUATION)
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return tuple(ordered)


def resolve_image_attachments(
    text: str,
    *,
    workspace_root: Path,
    reference_roots: tuple[Path, ...] = (),
    max_attachments: int = MAX_IMAGE_ATTACHMENTS_PER_TURN,
    max_attachment_bytes: int = MAX_IMAGE_ATTACHMENT_BYTES,
    max_total_bytes: int = MAX_TOTAL_IMAGE_ATTACHMENT_BYTES,
) -> ImageAttachmentResolution:
    """Resolve every ``@image:`` reference in ``text`` through the bounded loader."""

    tokens = parse_image_references(text)
    if not tokens:
        return ImageAttachmentResolution()

    workspace = workspace_root.expanduser().resolve()
    attempted = tokens[:max_attachments]
    over_budget = len(tokens) - len(attempted)

    references: list[ResolvedImageAttachment] = []
    loaded_bytes = 0
    for token in attempted:
        resolved = _resolve_one(
            token,
            workspace=workspace,
            reference_roots=reference_roots,
            max_attachment_bytes=max_attachment_bytes,
            max_total_bytes=max_total_bytes,
            loaded_bytes=loaded_bytes,
        )
        references.append(resolved)
        if resolved.loaded and resolved.attachment is not None:
            loaded_bytes += resolved.attachment.byte_count

    return ImageAttachmentResolution(
        references=tuple(references),
        over_budget_count=over_budget,
    )


def _resolve_one(
    token: str,
    *,
    workspace: Path,
    reference_roots: tuple[Path, ...],
    max_attachment_bytes: int,
    max_total_bytes: int,
    loaded_bytes: int,
) -> ResolvedImageAttachment:
    if len(token) > _MAX_REFERENCE_PATH_LENGTH:
        return ResolvedImageAttachment(raw=token, loaded=False, reason=_INVALID_REASON)
    try:
        resolved = resolve_tool_path(
            token,
            workspace_root=workspace,
            reference_roots=reference_roots,
        )
    except ValueError:
        return ResolvedImageAttachment(raw=token, loaded=False, reason=_INVALID_REASON)

    candidate = resolved.resolved
    if _is_blocked_path(resolved.relative_label, resolved.root):
        return ResolvedImageAttachment(raw=token, loaded=False, reason=_IGNORED_REASON)
    if not candidate.exists():
        return ResolvedImageAttachment(raw=token, loaded=False, reason=_MISSING_REASON)
    if not candidate.is_file():
        return ResolvedImageAttachment(raw=token, loaded=False, reason=_NOT_FILE_REASON)
    try:
        size = candidate.stat().st_size
    except OSError:
        return ResolvedImageAttachment(raw=token, loaded=False, reason=_READ_FAILED_REASON)
    if size > max_attachment_bytes:
        return ResolvedImageAttachment(raw=token, loaded=False, reason=_OVERSIZED_REASON)
    if loaded_bytes + size > max_total_bytes:
        return ResolvedImageAttachment(raw=token, loaded=False, reason=_BUDGET_REASON)
    try:
        data = candidate.read_bytes()
    except OSError:
        return ResolvedImageAttachment(raw=token, loaded=False, reason=_READ_FAILED_REASON)
    # Re-check the in-memory size after reading: the on-disk file could have
    # grown between stat and read, and the aggregate budget is authoritative.
    if len(data) > max_attachment_bytes or loaded_bytes + len(data) > max_total_bytes:
        return ResolvedImageAttachment(raw=token, loaded=False, reason=_OVERSIZED_REASON)
    media_type = detect_image_media_type(data)
    if media_type is None or media_type not in SUPPORTED_IMAGE_MEDIA_TYPES:
        return ResolvedImageAttachment(
            raw=token, loaded=False, reason=_UNSUPPORTED_REASON
        )

    attachment = ProviderImageAttachment(
        media_type=media_type,
        data_base64=base64.b64encode(data).decode("ascii"),
        byte_count=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        source_label=_display_label(token),
    )
    return ResolvedImageAttachment(
        raw=token, loaded=True, reason=_LOADED_REASON, attachment=attachment
    )


def _is_blocked_path(relative_label: str, root: Path) -> bool:
    """Reject VCS/cache/build paths and ``.gitignore`` matches.

    Reuses the ``read`` tool's ``.git``/cache directory set and
    ``.gitignore`` matcher, but deliberately omits its *generated-suffix*
    rejection: images legitimately carry ``.png``/``.jpg``/``.gif`` suffixes
    that the text reader treats as generated binary to skip.
    """

    posix_path = PurePosixPath(relative_label)
    if any(part in _GENERATED_PARTS for part in posix_path.parts):
        return True
    return _matches_root_ignore(relative_label, root)


def _display_label(raw: str) -> str:
    """Return a terminal-safe rendering of a raw reference token."""

    cleaned = "".join(char for char in raw if ord(char) >= 32 and char != "\x7f")
    return cleaned or "<unprintable>"


__all__ = [
    "MAX_IMAGE_ATTACHMENTS_PER_TURN",
    "MAX_IMAGE_ATTACHMENT_BYTES",
    "MAX_TOTAL_IMAGE_ATTACHMENT_BYTES",
    "SUPPORTED_IMAGE_MEDIA_TYPES",
    "ImageAttachmentResolution",
    "ProviderImageAttachment",
    "ResolvedImageAttachment",
    "detect_image_media_type",
    "parse_image_references",
    "resolve_image_attachments",
]
