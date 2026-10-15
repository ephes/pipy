"""Inline terminal images: which protocol a terminal speaks, and its bytes.

Ports pi-tui ``terminal-image.ts`` (``detectCapabilities``' image part,
``calculateImageCellSize``, ``encodeKitty``, ``encodeITerm2``,
``deleteKittyImage``, ``renderImage``) and the drawing part of pi-tui's
``Image`` component (``components/image.ts``): the rows an image takes in
the frame. pipy draws images only where Pi does, after a tool result's row
(``ToolExecutionComponent.updateDisplay``).

Narrower than Pi:

- The cell size is pi-tui's default (9x18 pixels); pipy does not ask the
  terminal (Pi's ``CSI 16 t`` query), so a terminal whose cells have another
  shape draws a Kitty image a little stretched.
- A Kitty terminal only takes PNG. Pi converts other types with its Photon
  transcoder; pipy has none and shows the image's text line instead, as
  pi-tui does when no transcoder is registered.
- No Windows detection (pipy does not run there).
- An iTerm2 image gets the reserved rows as its height, where Pi passes
  ``height=auto``.

Escape safety: the image data goes into the escape sequence, so it is
accepted only as plain base64 (whitespace removed). Data that is not, and
every value pipy cannot place, gives the text line instead.
"""

from __future__ import annotations

import math
import os
import random
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

ImageProtocol = Literal["kitty", "iterm2"]

#: pi-tui's default cell size in pixels (``cellDimensions``).
CELL_WIDTH_PX = 9
CELL_HEIGHT_PX = 18
#: pi-tui ``Image`` without a readable size (``{ widthPx: 800, heightPx: 600 }``).
DEFAULT_IMAGE_SIZE = (800, 600)

_KITTY_CHUNK = 4096
_BASE64_RE = re.compile(r"[A-Za-z0-9+/]*={0,2}")
_WHITESPACE_RE = re.compile(r"[\t\n\x0b\x0c\r ]+")


def detect_image_protocol(
    environ: Mapping[str, str] | None = None,
) -> ImageProtocol | None:
    """pi-tui ``detectCapabilities().images``, ``PIPY_IMAGE_PROTOCOL`` first.

    ``PIPY_IMAGE_PROTOCOL`` (Pi ``PI_IMAGE_PROTOCOL``) is ``kitty``,
    ``iterm2``, or ``none``/``0`` to turn images off; any other value leaves
    the detection to the environment.
    """

    env = os.environ if environ is None else environ
    override = env.get("PIPY_IMAGE_PROTOCOL", "").lower()
    if override in ("kitty", "iterm2"):
        return "kitty" if override == "kitty" else "iterm2"
    if override in ("none", "0"):
        return None
    return _protocol_from_environment(env)


def terminal_image_protocol(stream: object) -> ImageProtocol | None:
    """The protocol to draw with on ``stream``: none unless it is a terminal."""

    isatty = getattr(stream, "isatty", None)
    try:
        tty = bool(isatty()) if callable(isatty) else False
    except (OSError, ValueError):
        return None
    return detect_image_protocol() if tty else None


def _protocol_from_environment(env: Mapping[str, str]) -> ImageProtocol | None:
    term_program = env.get("TERM_PROGRAM", "").lower()
    term = env.get("TERM", "").lower()
    # Image protocols are unreliable under tmux and screen.
    if env.get("TMUX") or term.startswith(("tmux", "screen")):
        return None
    # Herdr before the outer terminal: KITTY_WINDOW_ID can leak into its panes.
    if term_program == "herdr":
        return "kitty"
    kitty = (
        bool(env.get("KITTY_WINDOW_ID"))
        or term_program in ("kitty", "ghostty", "wezterm", "warpterminal")
        or "ghostty" in term
        or any(
            env.get(name)
            for name in (
                "GHOSTTY_RESOURCES_DIR",
                "WEZTERM_PANE",
                "WARP_SESSION_ID",
                "WARP_TERMINAL_SESSION_UUID",
            )
        )
    )
    if kitty:
        return "kitty"
    if env.get("ITERM_SESSION_ID") or term_program == "iterm.app":
        return "iterm2"
    return None


def clean_base64(data: str) -> str | None:
    """``data`` without whitespace, or ``None`` when it is not plain base64."""

    cleaned = _WHITESPACE_RE.sub("", data)
    if not cleaned or len(cleaned) % 4 or _BASE64_RE.fullmatch(cleaned) is None:
        return None
    return cleaned


def allocate_image_id() -> int:
    """pi-tui ``allocateImageId``: a random Kitty image id."""

    return random.randint(1, 0xFFFFFFFE)  # noqa: S311 - an id, not a secret


def calculate_image_cell_size(
    width_px: int,
    height_px: int,
    max_width_cells: int,
    max_height_cells: int | None = None,
    *,
    optimize_aspect_ratio: bool = False,
) -> tuple[int, int]:
    """pi-tui ``calculateImageCellSize``: ``(columns, rows)``."""

    max_width = max(1, math.floor(max_width_cells))
    max_height = (
        None if max_height_cells is None else max(1, math.floor(max_height_cells))
    )
    image_width = max(1, width_px)
    image_height = max(1, height_px)
    width_scale = max_width * CELL_WIDTH_PX / image_width
    height_scale = (
        width_scale
        if max_height is None
        else max_height * CELL_HEIGHT_PX / image_height
    )
    scale = min(width_scale, height_scale)
    columns = max(1, min(max_width, math.ceil(image_width * scale / CELL_WIDTH_PX)))
    rows = max(1, math.ceil(image_height * scale / CELL_HEIGHT_PX))
    if max_height is not None:
        rows = min(max_height, rows)
    if not optimize_aspect_ratio:
        return columns, rows
    if width_scale <= height_scale:
        ideal_rows = (columns * CELL_WIDTH_PX * image_height) / (
            image_width * CELL_HEIGHT_PX
        )
        rows = _less_distorted(rows, ideal_rows)
    else:
        ideal_columns = (rows * CELL_HEIGHT_PX * image_width) / (
            image_height * CELL_WIDTH_PX
        )
        columns = _less_distorted(columns, ideal_columns)
    return columns, rows


def _less_distorted(upper: int, ideal: float) -> int:
    """pi-tui ``chooseLessDistortedCellCount``."""

    if upper <= 1:
        return upper
    lower = upper - 1
    upper_distortion = max(upper / ideal, ideal / upper)
    lower_distortion = max(lower / ideal, ideal / lower)
    return lower if lower_distortion < upper_distortion else upper


def encode_kitty(
    data: str, *, columns: int, rows: int, image_id: int, move_cursor: bool = False
) -> str:
    """pi-tui ``encodeKitty``: transmit and place a PNG (``f=100``)."""

    params = ["a=T", "f=100", "q=2"]
    if not move_cursor:
        params.append("C=1")
    params.extend((f"c={columns}", f"r={rows}", f"i={image_id}"))
    head = ",".join(params)
    if len(data) <= _KITTY_CHUNK:
        return f"\x1b_G{head};{data}\x1b\\"
    chunks: list[str] = []
    for offset in range(0, len(data), _KITTY_CHUNK):
        chunk = data[offset : offset + _KITTY_CHUNK]
        last = offset + _KITTY_CHUNK >= len(data)
        if offset == 0:
            chunks.append(f"\x1b_G{head},m=1;{chunk}\x1b\\")
        else:
            chunks.append(f"\x1b_Gm={0 if last else 1};{chunk}\x1b\\")
    return "".join(chunks)


def delete_kitty_image(image_id: int) -> str:
    """pi-tui ``deleteKittyImage``: delete the image and free its data."""

    return f"\x1b_Ga=d,d=I,i={image_id},q=2\x1b\\"


def encode_iterm2(data: str, *, width: int, height: int) -> str:
    """pi-tui ``encodeITerm2``: an inline image in a box of ``width`` by
    ``height`` cells, its aspect ratio kept.

    Pi passes ``height=auto``; pipy passes the rows it reserved, so that a
    column count rounded up can never make the image taller than them.
    """

    size = len(data) * 3 // 4 - len(data) + len(data.rstrip("="))
    params = ["inline=1", f"size={size}", f"width={width}", f"height={height}"]
    return f"\x1b]1337;File={';'.join(params)}:{data}\x07"


@dataclass(frozen=True, slots=True)
class TerminalImage:
    """One image of a tool result as the frame draws it (pi-tui ``Image``).

    ``data`` is plain base64 (``clean_base64``) or ``None`` when the data
    cannot go into an escape sequence; ``fallback`` is the styled text line
    drawn instead of the image.
    """

    data: str | None
    mime_type: str
    protocol: ImageProtocol
    max_width_cells: int
    image_id: int
    size: tuple[int, int]
    fallback: str


@dataclass(frozen=True, slots=True)
class ImageRows:
    """The rows one drawn image takes: ``rows - 1`` empty rows, then
    ``last``, which moves the cursor up to the first row, draws the image
    and leaves the cursor on the last row again."""

    rows: int
    last: str
    kitty_id: int | None


def image_rows(image: TerminalImage, *, width: int, height: int) -> ImageRows | None:
    """pi-tui ``Image.render`` at ``width``, or ``None`` for the text line.

    The image is at most ``height`` rows tall, so that drawing it from its
    last row never moves the cursor above the screen; Pi's main-screen
    renderer draws a taller Kitty image without reserving its rows.
    """

    if image.data is None:
        return None
    if image.protocol == "kitty" and image.mime_type != "image/png":
        return None
    max_width = max(1, min(width - 2, image.max_width_cells))
    default_max_height = max(1, math.ceil(max_width * CELL_WIDTH_PX / CELL_HEIGHT_PX))
    max_height = max(1, min(default_max_height, height))
    columns, rows = calculate_image_cell_size(
        image.size[0],
        image.size[1],
        max_width,
        max_height,
        optimize_aspect_ratio=image.protocol == "kitty",
    )
    up = f"\x1b[{rows - 1}A" if rows > 1 else ""
    if image.protocol == "kitty":
        down = f"\x1b[{rows - 1}B" if rows > 1 else ""
        sequence = encode_kitty(
            image.data, columns=columns, rows=rows, image_id=image.image_id
        )
        return ImageRows(rows, f"{up}{sequence}{down}", image.image_id)
    return ImageRows(
        rows, f"{up}{encode_iterm2(image.data, width=columns, height=rows)}", None
    )
