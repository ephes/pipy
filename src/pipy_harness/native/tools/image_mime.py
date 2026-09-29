"""Image type sniffing for the ``read`` tool, ported from Pi.

Mirrors ``detectSupportedImageMimeType`` and
``detectSupportedImageMimeTypeFromFile`` in
``packages/coding-agent/src/utils/mime.ts`` (pi-mono ``4df157433``): JPEG
(except ``ff d8 ff f7``), non-animated PNG, GIF, WebP and BMP, judged from
the first 4100 bytes.
"""

from __future__ import annotations

from pathlib import Path

IMAGE_TYPE_SNIFF_BYTES = 4100
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _byte(buffer: bytes, offset: int) -> int:
    return buffer[offset] if 0 <= offset < len(buffer) else 0


def _uint16_le(buffer: bytes, offset: int) -> int:
    return _byte(buffer, offset) + (_byte(buffer, offset + 1) << 8)


def _uint32_le(buffer: bytes, offset: int) -> int:
    return (
        _byte(buffer, offset)
        + (_byte(buffer, offset + 1) << 8)
        + (_byte(buffer, offset + 2) << 16)
        + _byte(buffer, offset + 3) * 0x1000000
    )


def _uint32_be(buffer: bytes, offset: int) -> int:
    return (
        _byte(buffer, offset) * 0x1000000
        + (_byte(buffer, offset + 1) << 16)
        + (_byte(buffer, offset + 2) << 8)
        + _byte(buffer, offset + 3)
    )


def _starts_with_ascii(buffer: bytes, offset: int, text: str) -> bool:
    if len(buffer) < offset + len(text):
        return False
    return buffer[offset : offset + len(text)] == text.encode("ascii")


def _is_png(buffer: bytes) -> bool:
    return (
        len(buffer) >= 16
        and _uint32_be(buffer, len(_PNG_SIGNATURE)) == 13
        and _starts_with_ascii(buffer, 12, "IHDR")
    )


def _is_animated_png(buffer: bytes) -> bool:
    offset = len(_PNG_SIGNATURE)
    while offset + 8 <= len(buffer):
        chunk_length = _uint32_be(buffer, offset)
        chunk_type_offset = offset + 4
        if _starts_with_ascii(buffer, chunk_type_offset, "acTL"):
            return True
        if _starts_with_ascii(buffer, chunk_type_offset, "IDAT"):
            return False
        next_offset = offset + 8 + chunk_length + 4
        if next_offset <= offset or next_offset > len(buffer):
            return False
        offset = next_offset
    return False


def _is_bmp(buffer: bytes) -> bool:
    if len(buffer) < 26:
        return False
    declared_file_size = _uint32_le(buffer, 2)
    pixel_data_offset = _uint32_le(buffer, 10)
    dib_header_size = _uint32_le(buffer, 14)
    if declared_file_size != 0 and declared_file_size < 26:
        return False
    if pixel_data_offset < 14 + dib_header_size:
        return False
    if declared_file_size != 0 and pixel_data_offset >= declared_file_size:
        return False
    if dib_header_size == 12:
        color_planes = _uint16_le(buffer, 22)
        bits_per_pixel = _uint16_le(buffer, 24)
    elif 40 <= dib_header_size <= 124:
        if len(buffer) < 30:
            return False
        color_planes = _uint16_le(buffer, 26)
        bits_per_pixel = _uint16_le(buffer, 28)
    else:
        return False
    return color_planes == 1 and bits_per_pixel in (1, 4, 8, 16, 24, 32)


def detect_supported_image_mime_type(buffer: bytes) -> str | None:
    """Return the image MIME type Pi's ``read`` would attach, or None."""

    if buffer.startswith(b"\xff\xd8\xff"):
        return None if _byte(buffer, 3) == 0xF7 else "image/jpeg"
    if buffer.startswith(_PNG_SIGNATURE):
        return "image/png" if _is_png(buffer) and not _is_animated_png(buffer) else None
    if _starts_with_ascii(buffer, 0, "GIF87a") or _starts_with_ascii(
        buffer, 0, "GIF89a"
    ):
        return "image/gif"
    if _starts_with_ascii(buffer, 0, "RIFF") and _starts_with_ascii(buffer, 8, "WEBP"):
        return "image/webp"
    if _starts_with_ascii(buffer, 0, "BM") and _is_bmp(buffer):
        return "image/bmp"
    return None


def detect_supported_image_mime_type_from_file(path: Path) -> str | None:
    """Sniff the first 4100 bytes of ``path``; raises ``OSError`` on failure."""

    with path.open("rb") as handle:
        head = handle.read(IMAGE_TYPE_SNIFF_BYTES)
    return detect_supported_image_mime_type(head)


__all__ = [
    "IMAGE_TYPE_SNIFF_BYTES",
    "detect_supported_image_mime_type",
    "detect_supported_image_mime_type_from_file",
]
