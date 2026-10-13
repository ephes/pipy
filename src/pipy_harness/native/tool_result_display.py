"""What a tool result's row shows of its images.

Pi ``core/tools/render-utils.ts`` ``getTextOutput``: a terminal that cannot
draw images, or a row drawn with ``showImages: false`` as the HTML export
draws it, shows one line per image after the result's text,
``[Image: [image/png] 640x480]`` (pi-tui ``imageFallback``). pipy's frames
carry no inline images, so its rows always show that line. The size is read
from the image's header (pi-tui ``getImageDimensions``) and left out when
the data is not an image of the type it claims.
"""

from __future__ import annotations

import base64
import binascii

from pipy_harness.native.agent.content import ImageContent, tool_result_images
from pipy_harness.native.agent.messages import AgentToolResultMessage

# The headers of PNG, GIF and WebP are at the start; a JPEG's size follows
# its metadata segments, so all of it is decoded.
_HEADER_CHARS = 64


def tool_result_display_text(result: AgentToolResultMessage) -> str:
    """The result's text followed by one line per image."""

    text = result.content.value
    lines = [image_fallback(image) for image in tool_result_images(result.blocks)]
    if not lines:
        return text
    return "\n".join([text, *lines] if text else lines)


def image_fallback(image: ImageContent) -> str:
    """Pi ``imageFallback(mimeType, dimensions)``."""

    size = image_dimensions(image.data, image.mime_type)
    shown = f"[{image.mime_type}]"
    if size is not None:
        shown += f" {size[0]}x{size[1]}"
    return f"[Image: {shown}]"


def image_dimensions(data: str, mime_type: str) -> tuple[int, int] | None:
    """Width and height in pixels of a base64 PNG, JPEG, GIF or WebP image."""

    reader = _READERS.get(mime_type)
    if reader is None:
        return None
    # Wrapped base64 is read like Node's decoder reads it.
    encoded = "".join(
        (data if mime_type == "image/jpeg" else data[: 2 * _HEADER_CHARS]).split()
    )
    if mime_type != "image/jpeg":
        encoded = encoded[:_HEADER_CHARS]
    try:
        header = base64.b64decode(encoded[: len(encoded) // 4 * 4], validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        return reader(header)
    except IndexError:
        return None


def _png(buffer: bytes) -> tuple[int, int] | None:
    if len(buffer) < 24 or buffer[:4] != b"\x89PNG":
        return None
    return int.from_bytes(buffer[16:20], "big"), int.from_bytes(buffer[20:24], "big")


def _jpeg(buffer: bytes) -> tuple[int, int] | None:
    if len(buffer) < 2 or buffer[:2] != b"\xff\xd8":
        return None
    offset = 2
    while offset < len(buffer) - 9:
        if buffer[offset] != 0xFF:
            offset += 1
            continue
        if 0xC0 <= buffer[offset + 1] <= 0xC2:
            height = int.from_bytes(buffer[offset + 5 : offset + 7], "big")
            width = int.from_bytes(buffer[offset + 7 : offset + 9], "big")
            return width, height
        length = int.from_bytes(buffer[offset + 2 : offset + 4], "big")
        if length < 2:
            return None
        offset += 2 + length
    return None


def _gif(buffer: bytes) -> tuple[int, int] | None:
    if len(buffer) < 10 or buffer[:6] not in (b"GIF87a", b"GIF89a"):
        return None
    return int.from_bytes(buffer[6:8], "little"), int.from_bytes(buffer[8:10], "little")


def _webp(buffer: bytes) -> tuple[int, int] | None:
    if len(buffer) < 30 or buffer[:4] != b"RIFF" or buffer[8:12] != b"WEBP":
        return None
    chunk = buffer[12:16]
    if chunk == b"VP8 ":
        return (
            int.from_bytes(buffer[26:28], "little") & 0x3FFF,
            int.from_bytes(buffer[28:30], "little") & 0x3FFF,
        )
    if chunk == b"VP8L":
        bits = int.from_bytes(buffer[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X":
        return (
            int.from_bytes(buffer[24:27], "little") + 1,
            int.from_bytes(buffer[27:30], "little") + 1,
        )
    return None


_READERS = {
    "image/png": _png,
    "image/jpeg": _jpeg,
    "image/gif": _gif,
    "image/webp": _webp,
}
