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


__all__ = ["ClipboardResult", "copy_to_clipboard"]
