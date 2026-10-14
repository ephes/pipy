"""Image conversion and resizing for inline provider images, ported from Pi.

Mirrors ``processImage`` (``packages/coding-agent/src/utils/image-process.ts``),
``resizeImageInProcess`` (``image-resize-core.ts``), ``formatDimensionNote``
(``image-resize.ts``) and ``applyExifOrientation`` (``exif-orientation.ts``)
of pi-mono ``a447ba11``.

Pi decodes and encodes with Photon (Rust/WASM). pipy uses Pillow, installed
with the ``images`` extra. Without Pillow every call fails (``None``), which
is what Pi's functions return when Photon cannot be loaded; callers then keep
the image as it is.

Differences from Photon that this module keeps small on purpose:

- Only the formats Photon's image decoder reads are opened (PNG, JPEG, GIF,
  WebP, BMP, ICO, TIFF, TGA, PNM, DDS), so no other Pillow plugin runs on
  data a tool returned.
- An image of more than ``PIL.Image.MAX_IMAGE_PIXELS`` pixels (about 89
  million) is not decoded; Photon has no such limit.
- The resampling filter is Pillow's Lanczos (``a = 3``), Photon's
  ``Lanczos3``; the bytes of a resized image are not Photon's.
"""

from __future__ import annotations

import base64
import binascii
import io
import math
import warnings
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image

DEFAULT_MAX_IMAGE_BYTES = int(4.5 * 1024 * 1024)
"""Pi ``DEFAULT_MAX_BYTES``: 4.5 MB of base64, below Anthropic's 5 MB limit."""

# The decoders Photon's ``image`` crate has; Pillow names them so.
_DECODER_FORMATS = (
    "PNG",
    "JPEG",
    "GIF",
    "WEBP",
    "BMP",
    "ICO",
    "TIFF",
    "TGA",
    "PPM",
    "DDS",
)
_JPEG_QUALITY_STEPS = (85, 70, 55, 40)
_SUPPORTED_MIME_TYPES = {
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
}
_BASE64_ALPHABET = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
)


@dataclass(frozen=True, slots=True)
class ImageResizeOptions:
    """Pi ``ImageResizeOptions`` with its defaults (``image-resize-core.ts``)."""

    max_width: int = 2000
    max_height: int = 2000
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES
    jpeg_quality: int = 80


@dataclass(frozen=True, slots=True)
class ResizedImage:
    """Pi ``ResizedImage``: base64 data and the sizes before and after."""

    data: str
    mime_type: str
    original_width: int
    original_height: int
    width: int
    height: int
    was_resized: bool


@dataclass(frozen=True, slots=True)
class ProcessedImage:
    """A successful Pi ``ProcessImageResult``: the image and its hint lines."""

    data: str
    mime_type: str
    hints: tuple[str, ...]


def decode_base64(data: str) -> bytes:
    """Decode base64 as Node's ``Buffer.from(data, "base64")`` does.

    Characters outside the alphabet are skipped, ``-`` and ``_`` read as
    ``+`` and ``/``, decoding ends at the first ``=``, and a last lone
    character is dropped.
    """

    chars = bytearray()
    for byte in data.encode("ascii", "ignore").replace(b"-", b"+").replace(b"_", b"/"):
        if byte == ord("="):
            break
        if byte in _BASE64_ALPHABET:
            chars.append(byte)
    usable = len(chars) - (1 if len(chars) % 4 == 1 else 0)
    padded = bytes(chars[:usable]) + b"=" * (-usable % 4)
    try:
        return base64.b64decode(padded)
    except binascii.Error:  # pragma: no cover - the input is clean by now
        return b""


def _encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _base_mime_type(mime_type: str) -> str:
    return mime_type.split(";")[0].strip().lower()


def process_image(
    raw: bytes,
    mime_type: str,
    *,
    auto_resize: bool = True,
    options: ImageResizeOptions | None = None,
) -> ProcessedImage | None:
    """Pi ``processImage``: a supported inline image, or ``None``.

    A type other than PNG, JPEG, GIF and WebP is converted to PNG first. With
    ``auto_resize`` the image is then made to fit ``options`` (Pi's defaults
    when ``None``). ``None`` means the image could not be decoded, converted
    or made small enough, or Pillow is not installed.
    """

    normalized = _SUPPORTED_MIME_TYPES.get(_base_mime_type(mime_type))
    converted_from: str | None = None
    if normalized is None:
        png = _convert_to_png(raw)
        if png is None:
            return None
        raw, normalized, converted_from = png, "image/png", _base_mime_type(mime_type)
    if not auto_resize:
        hints = _conversion_hint(converted_from, normalized)
        return ProcessedImage(_encode(raw), normalized, hints)
    resized = resize_image(raw, normalized, options)
    if resized is None:
        return None
    hints = _conversion_hint(converted_from, resized.mime_type)
    note = format_dimension_note(resized)
    return ProcessedImage(
        resized.data, resized.mime_type, hints + ((note,) if note else ())
    )


def _conversion_hint(converted_from: str | None, to: str) -> tuple[str, ...]:
    if not converted_from or converted_from == to:
        return ()
    return (f"[Image converted from {converted_from} to {to}.]",)


def resize_image(
    raw: bytes, mime_type: str, options: ImageResizeOptions | None = None
) -> ResizedImage | None:
    """Pi ``resizeImageInProcess``: fit the dimensions and the encoded size.

    An image within both limits is returned as it is (``was_resized`` false).
    Otherwise it is scaled to the maximum dimensions and encoded as PNG and
    as JPEG at falling qualities; the first encoding under ``max_bytes``
    wins. If none fits, the dimensions shrink by a quarter until 1x1.
    ``None`` when the image cannot be decoded or never fits.
    """

    opts = options or ImageResizeOptions()
    image = _open_oriented(raw)
    if image is None:
        return None
    try:
        return _fit(image, raw, mime_type, opts)
    except Exception:  # noqa: BLE001 - Pi returns null for any Photon error
        return None
    finally:
        image.close()


def _fit(
    image: Image, raw: bytes, mime_type: str, opts: ImageResizeOptions
) -> ResizedImage | None:
    original_width, original_height = image.size
    if (
        original_width <= opts.max_width
        and original_height <= opts.max_height
        and math.ceil(len(raw) / 3) * 4 < opts.max_bytes
    ):
        return ResizedImage(
            _encode(raw),
            mime_type,
            original_width,
            original_height,
            original_width,
            original_height,
            was_resized=False,
        )
    width, height = original_width, original_height
    if width > opts.max_width:
        height = _js_round(height * opts.max_width / width)
        width = opts.max_width
    if height > opts.max_height:
        width = _js_round(width * opts.max_height / height)
        height = opts.max_height
    qualities = tuple(dict.fromkeys((opts.jpeg_quality, *_JPEG_QUALITY_STEPS)))
    while True:
        for data, candidate_type in _encodings(image, width, height, qualities):
            if len(data) < opts.max_bytes:
                return ResizedImage(
                    data,
                    candidate_type,
                    original_width,
                    original_height,
                    width,
                    height,
                    was_resized=True,
                )
        if width == 1 and height == 1:
            return None
        next_width = 1 if width == 1 else max(1, math.floor(width * 0.75))
        next_height = 1 if height == 1 else max(1, math.floor(height * 0.75))
        if next_width == width and next_height == height:  # pragma: no cover
            return None
        width, height = next_width, next_height


def _encodings(
    image: Image, width: int, height: int, qualities: tuple[int, ...]
) -> list[tuple[str, str]]:
    from PIL import Image as PILImage

    resized = image.resize((width, height), PILImage.Resampling.LANCZOS)
    try:
        candidates = [(_encode(_save(resized, "PNG")), "image/png")]
        rgb = resized.convert("RGB")
        try:
            for quality in qualities:
                candidates.append(
                    (_encode(_save(rgb, "JPEG", quality=quality)), "image/jpeg")
                )
        finally:
            rgb.close()
        return candidates
    finally:
        resized.close()


def _save(image: Image, image_format: str, **params: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=image_format, **params)
    return buffer.getvalue()


def _convert_to_png(raw: bytes) -> bytes | None:
    """Pi ``convertImageBytesToPng``."""

    image = _open_oriented(raw)
    if image is None:
        return None
    try:
        return _save(image, "PNG")
    except Exception:  # noqa: BLE001 - Pi returns null for any Photon error
        return None
    finally:
        image.close()


def _open_oriented(raw: bytes) -> Image | None:
    """Decode ``raw`` (first frame) as RGBA, turned by its EXIF orientation.

    Like Pi's ``applyExifOrientation``, only a JPEG's or a WebP's
    orientation is applied. ``None`` without Pillow or for data that does
    not decode.
    """

    try:
        from PIL import Image as PILImage
        from PIL import ImageOps
    except ImportError:
        return None
    try:
        with warnings.catch_warnings():
            # Over MAX_IMAGE_PIXELS Pillow warns; treat it as a failure.
            warnings.simplefilter("error", PILImage.DecompressionBombWarning)
            opened = PILImage.open(io.BytesIO(raw), formats=_DECODER_FORMATS)
            opened.load()
        oriented = (
            ImageOps.exif_transpose(opened)
            if opened.format in ("JPEG", "WEBP")
            else opened
        )
        rgba = oriented.convert("RGBA")
        if oriented is not opened:
            oriented.close()
        opened.close()
        return rgba
    except Exception:  # noqa: BLE001 - Pi returns null when Photon cannot decode
        return None


def format_dimension_note(result: ResizedImage) -> str | None:
    """Pi ``formatDimensionNote``: how coordinates map to the original."""

    if not result.was_resized:
        return None
    scale = Decimal(result.original_width / result.width).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return (
        f"[Image: original {result.original_width}x{result.original_height}, "
        f"displayed at {result.width}x{result.height}. Multiply coordinates by "
        f"{scale} to map to original image.]"
    )


def _js_round(value: float) -> int:
    """JavaScript ``Math.round`` for a positive number (halves round up)."""

    return math.floor(value + 0.5)
