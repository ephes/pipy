"""Safe, local clipboard helper for the product TUI `/copy` command.

The helper copies text through an operating-system clipboard command (a fixed
argv with no shell) or, when none is available, an OSC 52 terminal escape. It
performs no network, provider, tool, or authentication action: copying an
answer is a purely local operation.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import TextIO


# OSC 52 payloads are bounded by terminal/multiplexer buffers; keep the base64
# body well under the common ~100 KB ceiling so passthrough stays reliable.
_OSC52_MAX_BYTES = 74_000


@dataclass(frozen=True, slots=True)
class ClipboardResult:
    """Outcome of a local clipboard copy attempt."""

    copied: bool
    method: str
    byte_count: int
    detail: str


@dataclass(frozen=True, slots=True)
class ImageClipboardResult:
    """Outcome of an OS clipboard-image read (Pi Ctrl+V parity)."""

    found: bool
    data: bytes
    media_type: str
    detail: str


# Magic-byte signatures for the image types pipy attachments accept. Used so a
# text clipboard (or a non-image payload) reads as "no image" rather than a
# corrupt attachment.
_IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _detect_image_media_type(data: bytes) -> str | None:
    for signature, media_type in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return media_type
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _image_clipboard_commands(platform: str) -> tuple[tuple[str, list[str]], ...]:
    if platform.startswith("darwin"):
        return (("pngpaste", ["-"]),)
    if platform.startswith("win"):
        return ()
    return (
        ("wl-paste", ["--type", "image/png"]),
        ("xclip", ["-selection", "clipboard", "-t", "image/png", "-o"]),
    )


def _default_run_capture(argv: list[str]) -> bytes | None:
    try:
        completed = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def read_clipboard_image(
    *,
    platform: str | None = None,
    which: Callable[[str], str | None] | None = None,
    run_capture: Callable[[list[str]], bytes | None] | None = None,
) -> ImageClipboardResult:
    """Read an image from the OS clipboard, returning bytes + media type.

    Tries ``pngpaste`` on macOS and ``wl-paste``/``xclip`` (``image/png``) on
    Linux, validating the result by magic bytes so a text clipboard reads as
    "no image". Errors are treated as "no image" (matching Pi, which silently
    ignores clipboard-read failures). Performs no network/provider/tool action.
    """

    platform = platform if platform is not None else sys.platform
    which = which if which is not None else shutil.which
    run_capture = run_capture if run_capture is not None else _default_run_capture

    commands = _image_clipboard_commands(platform)
    if not commands:
        return ImageClipboardResult(
            found=False,
            data=b"",
            media_type="",
            detail="no clipboard image tool available on this platform",
        )
    saw_tool = False
    for name, args in commands:
        resolved = which(name)
        if not resolved:
            continue
        saw_tool = True
        data = run_capture([resolved, *args])
        if not data:
            continue
        media_type = _detect_image_media_type(data)
        if media_type is None:
            continue
        return ImageClipboardResult(
            found=True,
            data=data,
            media_type=media_type,
            detail=f"read {len(data)} bytes ({media_type}) via {name}",
        )
    detail = (
        "no image on the clipboard"
        if saw_tool
        else "no clipboard image tool available (install pngpaste / wl-paste / xclip)"
    )
    return ImageClipboardResult(found=False, data=b"", media_type="", detail=detail)


def _default_run(argv: list[str], data: bytes) -> bool:
    try:
        completed = subprocess.run(
            argv,
            input=data,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _clipboard_commands(platform: str) -> tuple[tuple[str, list[str]], ...]:
    if platform.startswith("darwin"):
        return (("pbcopy", []),)
    if platform.startswith("win"):
        return (("clip", []),)
    return (
        ("wl-copy", []),
        ("xclip", ["-selection", "clipboard"]),
        ("xsel", ["--clipboard", "--input"]),
    )


def copy_to_clipboard(
    text: str,
    *,
    platform: str | None = None,
    which: Callable[[str], str | None] | None = None,
    run: Callable[[list[str], bytes], bool] | None = None,
    terminal_stream: TextIO | None = None,
) -> ClipboardResult:
    """Copy ``text`` to the clipboard through a safe local path.

    Tries an OS clipboard command first (``pbcopy`` on macOS; ``wl-copy`` /
    ``xclip`` / ``xsel`` on Linux), then falls back to an OSC 52 escape on the
    terminal stream. Returns a :class:`ClipboardResult` describing what
    happened so the caller can report a clear local status.
    """

    platform = platform if platform is not None else sys.platform
    which = which if which is not None else shutil.which
    run = run if run is not None else _default_run
    data = text.encode("utf-8")
    byte_count = len(data)

    for name, args in _clipboard_commands(platform):
        resolved = which(name)
        if not resolved:
            continue
        if run([resolved, *args], data):
            return ClipboardResult(
                copied=True,
                method=name,
                byte_count=byte_count,
                detail=f"copied {byte_count} bytes via {name}",
            )

    if terminal_stream is not None:
        return _copy_via_osc52(data, byte_count, terminal_stream)

    return ClipboardResult(
        copied=False,
        method="none",
        byte_count=byte_count,
        detail=(
            "no clipboard command available "
            "(pbcopy/wl-copy/xclip/xsel) and no terminal for OSC 52"
        ),
    )


def _copy_via_osc52(
    data: bytes, byte_count: int, terminal_stream: TextIO
) -> ClipboardResult:
    truncated = data[:_OSC52_MAX_BYTES]
    payload = base64.b64encode(truncated).decode("ascii")
    try:
        terminal_stream.write(f"\x1b]52;c;{payload}\x07")
        terminal_stream.flush()
    except (OSError, ValueError):
        return ClipboardResult(
            copied=False,
            method="none",
            byte_count=byte_count,
            detail="terminal stream rejected the OSC 52 clipboard escape",
        )
    if len(truncated) < byte_count:
        detail = (
            f"copied first {len(truncated)} of {byte_count} bytes via OSC 52 "
            "(remainder truncated for terminal limits)"
        )
    else:
        detail = f"copied {byte_count} bytes via OSC 52 terminal escape"
    return ClipboardResult(
        copied=True,
        method="osc52",
        byte_count=byte_count,
        detail=detail,
    )


__all__ = [
    "ClipboardResult",
    "ImageClipboardResult",
    "copy_to_clipboard",
    "read_clipboard_image",
]
