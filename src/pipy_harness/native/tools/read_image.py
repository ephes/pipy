"""Images for the ``read`` tool: Pi's ``processImage`` without a resizer.

Pi (``utils/image-process.ts``, ``image-resize-core.ts``) normalizes the
type, converts BMP to PNG and resizes an image so it fits 2000 x 2000 pixels
and 4.5 MB of base64, with Photon. pipy has no image codec, so it does what
Pi does where no pixels change: an image within those limits is returned as
it is, and one that would need converting or resizing gets Pi's text for an
image it could not process. No pixels are decoded: the file's structure is
checked and its dimensions read (:func:`image_size`).
"""

from __future__ import annotations

import base64
import zlib
from collections.abc import Iterator
from dataclasses import dataclass

# Pi ``DEFAULT_OPTIONS`` of ``image-resize-core.ts``.
MAX_IMAGE_WIDTH = 2000
MAX_IMAGE_HEIGHT = 2000
# 4.5 MB of base64 payload, below Anthropic's 5 MB limit.
MAX_IMAGE_BASE64_BYTES = int(4.5 * 1024 * 1024)

# Pi ``processImage`` texts.
CONVERT_FAILED = (
    "[Image omitted: could not be converted to a supported inline image format.]"
)
RESIZE_FAILED = (
    "[Image omitted: could not be resized below the inline image size limit.]"
)

_INLINE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
# JPEG start-of-frame markers: every SOFn but DHT (C4), JPG (C8) and DAC (CC).
_JPEG_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


@dataclass(frozen=True, slots=True)
class ProcessedImage:
    """An image to attach: base64 ``data`` of type ``mime_type``."""

    data: str
    mime_type: str


def process_image(data: bytes, mime_type: str) -> ProcessedImage | str:
    """The image to attach, or Pi's text for one that cannot be attached."""

    if mime_type not in _INLINE_TYPES:
        # Pi converts other types (BMP) to PNG with Photon.
        return CONVERT_FAILED
    size = image_size(data, mime_type)
    if size is None:
        # Pi: an image Photon cannot decode is not resized.
        return RESIZE_FAILED
    width, height = size
    # Pi ``inputBase64Size``: the length of the base64 text.
    base64_size = (len(data) + 2) // 3 * 4
    if (
        width > MAX_IMAGE_WIDTH
        or height > MAX_IMAGE_HEIGHT
        or base64_size >= MAX_IMAGE_BASE64_BYTES
    ):
        return RESIZE_FAILED
    if mime_type == "image/png" and not _png_pixels_complete(data):
        return RESIZE_FAILED
    return ProcessedImage(base64.b64encode(data).decode("ascii"), mime_type)


def image_size(data: bytes, mime_type: str) -> tuple[int, int] | None:
    """The width and height of a structurally complete image, or None.

    Pi decodes the image with Photon, so a truncated or malformed file is
    never attached. pipy has no decoder; it walks the file's structure
    (PNG chunks and their CRCs, JPEG segments up to the scan and its end of
    image, GIF blocks with their image data up to the trailer, the WebP RIFF
    chunks with a frame) and refuses a file that ends early or holds no
    image data, before reading the size. A PNG's pixel data is inflated as
    well (:func:`process_image`), once its size is within the limits.
    """

    reader = _READERS.get(mime_type)
    if reader is None:
        return None
    try:
        size = reader(data)
    except IndexError:
        return None
    if size is None or size[0] <= 0 or size[1] <= 0:
        return None
    return size


def _png_size(data: bytes) -> tuple[int, int] | None:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    offset = 8
    size: tuple[int, int] | None = None
    has_data = False
    while True:
        if offset + 12 > len(data):
            return None
        length = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            return None
        body = data[offset + 8 : end - 4]
        if zlib.crc32(kind + body) != int.from_bytes(data[end - 4 : end], "big"):
            return None
        if size is None:
            if kind != b"IHDR" or length != 13:
                return None
            size = int.from_bytes(body[0:4], "big"), int.from_bytes(body[4:8], "big")
        elif kind == b"IDAT":
            has_data = True
        elif kind == b"IEND":
            return size if has_data else None
        offset = end


# PNG channels by color type, and the bit depths each allows.
_PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_PNG_DEPTHS = {
    0: (1, 2, 4, 8, 16),
    2: (8, 16),
    3: (1, 2, 4, 8),
    4: (8, 16),
    6: (8, 16),
}
# Adam7 passes: first column, first row, column step, row step.
_ADAM7 = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)


def _png_pixels_complete(data: bytes) -> bool:
    """Whether a structurally complete PNG's header is valid and its image
    data is one complete zlib stream of exactly the rows it declares, each
    with a valid filter type. Bounded: at most one byte more than the rows
    is inflated."""

    chunks = _png_chunks(data)
    ihdr = chunks[0][1]
    width = int.from_bytes(ihdr[0:4], "big")
    height = int.from_bytes(ihdr[4:8], "big")
    depth, color, compression, filtering, interlace = ihdr[8:13]
    if (
        depth not in _PNG_DEPTHS.get(color, ())
        or (compression, filtering) != (0, 0)
        or interlace not in (0, 1)
    ):
        return False
    kinds = [kind for kind, _ in chunks]
    if color == 3 and (
        b"PLTE" not in kinds or kinds.index(b"PLTE") > kinds.index(b"IDAT")
    ):
        return False
    bits = depth * _PNG_CHANNELS[color]
    passes = _ADAM7 if interlace else ((0, 0, 1, 1),)
    rows: list[int] = []
    for x0, y0, dx, dy in passes:
        columns = (width - x0 + dx - 1) // dx if width > x0 else 0
        lines = (height - y0 + dy - 1) // dy if height > y0 else 0
        if columns:
            rows += [1 + (columns * bits + 7) // 8] * lines
    expected = sum(rows)
    idat = b"".join(body for kind, body in chunks if kind == b"IDAT")
    inflater = zlib.decompressobj()
    try:
        pixels = inflater.decompress(idat, expected + 1)
    except zlib.error:
        return False
    # The stream must also end: rows that are all there in a stream that
    # was cut are still a cut file.
    if len(pixels) != expected or not inflater.eof:
        return False
    offset = 0
    for size in rows:
        if pixels[offset] > 4:
            return False
        offset += size
    return True


def _png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """The chunks of a PNG that :func:`_png_size` accepted, up to IEND."""

    chunks: list[tuple[bytes, bytes]] = []
    offset = 8
    while not chunks or chunks[-1][0] != b"IEND":
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunks.append(
            (data[offset + 4 : offset + 8], data[offset + 8 : offset + 8 + length])
        )
        offset += 12 + length
    return chunks


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    if data[:2] != b"\xff\xd8":
        return None
    size: tuple[int, int] | None = None
    for marker, start, end in _jpeg_segments(data):
        if marker in _JPEG_SOF:
            if end - start < 9:
                return None
            size = (
                int.from_bytes(data[start + 7 : start + 9], "big"),
                int.from_bytes(data[start + 5 : start + 7], "big"),
            )
        elif marker == 0xDA:
            # Start of scan: entropy-coded data up to the end of image.
            if size is None or data.find(b"\xff\xd9", end) <= end:
                return None
            return size
    return None


def _jpeg_segments(data: bytes) -> Iterator[tuple[int, int, int]]:
    """Each segment's marker, start and end, up to the first scan; ends
    early at the end of the data or at anything that is not a segment."""

    offset = 2
    while offset + 4 <= len(data) and data[offset] == 0xFF:
        marker = data[offset + 1]
        if marker == 0xFF:
            # Fill byte before a marker.
            offset += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            # Markers without a length.
            offset += 2
            continue
        length = int.from_bytes(data[offset + 2 : offset + 4], "big")
        end = offset + 2 + length
        if length < 2 or end > len(data) or marker == 0xD9:
            return
        yield marker, offset, end
        if marker == 0xDA:
            return
        offset = end


def _gif_size(data: bytes) -> tuple[int, int] | None:
    if data[:6] not in (b"GIF87a", b"GIF89a") or len(data) < 13:
        return None
    size = int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    offset = 13
    if data[10] & 0x80:
        offset += 3 << ((data[10] & 0x07) + 1)
    images = 0
    while True:
        block = data[offset]
        if block == 0x3B:
            return size if images else None
        if block == 0x21:
            offset = _gif_sub_blocks(data, offset + 2)
        elif block == 0x2C:
            flags = data[offset + 9]
            offset += 10
            if flags & 0x80:
                offset += 3 << ((flags & 0x07) + 1)
            # The LZW minimum code size, then the image data.
            if not 2 <= data[offset] <= 8 or not data[offset + 1]:
                return None
            offset = _gif_sub_blocks(data, offset + 1)
            images += 1
        else:
            return None


def _gif_sub_blocks(data: bytes, offset: int) -> int:
    """The offset after the data sub-blocks at ``offset``; raises
    ``IndexError`` when the data ends before their terminator."""

    while data[offset]:
        offset += data[offset] + 1
    return offset + 1


def _webp_size(data: bytes) -> tuple[int, int] | None:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    riff_end = 8 + int.from_bytes(data[4:8], "little")
    if riff_end > len(data) or riff_end < 20:
        return None
    offset = 12
    canvas: tuple[int, int] | None = None
    while offset + 8 <= riff_end:
        kind = data[offset : offset + 4]
        length = int.from_bytes(data[offset + 4 : offset + 8], "little")
        if offset + 8 + length > riff_end:
            return None
        body = data[offset + 8 : offset + 8 + length]
        if kind == b"VP8X" and length >= 10 and offset == 12:
            canvas = (
                int.from_bytes(body[4:7], "little") + 1,
                int.from_bytes(body[7:10], "little") + 1,
            )
        elif kind in (b"VP8 ", b"VP8L"):
            frame = _webp_frame_size(kind, body)
            if frame is None:
                return None
            return canvas or frame
        elif kind == b"ANMF" and canvas is not None:
            # A frame header, then the frame's own chunks.
            return canvas if _webp_anmf_has_frame(body) else None
        offset += 8 + length + (length & 1)
    return None


def _webp_anmf_has_frame(body: bytes) -> bool:
    """Whether an ``ANMF`` chunk holds a ``VP8 `` or ``VP8L`` frame after
    its 16-byte header (and an optional ``ALPH`` chunk)."""

    offset = 16
    while offset + 8 <= len(body):
        kind = body[offset : offset + 4]
        length = int.from_bytes(body[offset + 4 : offset + 8], "little")
        if offset + 8 + length > len(body):
            return False
        if kind in (b"VP8 ", b"VP8L"):
            frame = body[offset + 8 : offset + 8 + length]
            return _webp_frame_size(kind, frame) is not None
        offset += 8 + length + (length & 1)
    return False


def _webp_frame_size(kind: bytes, body: bytes) -> tuple[int, int] | None:
    """The size a ``VP8 `` or ``VP8L`` chunk's frame header declares."""

    if kind == b"VP8 ":
        # The key frame's start code, then 14-bit width and height.
        if len(body) < 10 or body[3:6] != b"\x9d\x01\x2a":
            return None
        return (
            int.from_bytes(body[6:8], "little") & 0x3FFF,
            int.from_bytes(body[8:10], "little") & 0x3FFF,
        )
    if len(body) < 5 or body[0] != 0x2F:
        return None
    bits = int.from_bytes(body[1:5], "little")
    return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1


_READERS = {
    "image/png": _png_size,
    "image/jpeg": _jpeg_size,
    "image/gif": _gif_size,
    "image/webp": _webp_size,
}
