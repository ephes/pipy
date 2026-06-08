"""Tests for the OS clipboard-image read helper (Pi Ctrl+V parity)."""

from __future__ import annotations

import sys
import time

import pytest

from pipy_harness.native.clipboard import _default_run_capture, read_clipboard_image

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def test_reads_png_via_pngpaste_on_macos() -> None:
    captured: list[list[str]] = []

    def run_capture(argv: list[str]) -> bytes | None:
        captured.append(argv)
        return _PNG if argv[0].endswith("pngpaste") else None

    result = read_clipboard_image(
        platform="darwin",
        which=lambda name: f"/usr/local/bin/{name}" if name == "pngpaste" else None,
        run_capture=run_capture,
    )
    assert result.found
    assert result.data == _PNG
    assert result.media_type == "image/png"


def test_reads_via_wl_paste_on_linux() -> None:
    def run_capture(argv: list[str]) -> bytes | None:
        return _JPEG if argv[0].endswith("wl-paste") else None

    result = read_clipboard_image(
        platform="linux",
        which=lambda name: f"/usr/bin/{name}" if name == "wl-paste" else None,
        run_capture=run_capture,
    )
    assert result.found
    assert result.media_type == "image/jpeg"


def test_no_tool_available_returns_not_found() -> None:
    result = read_clipboard_image(
        platform="linux",
        which=lambda name: None,
        run_capture=lambda argv: None,
    )
    assert not result.found
    assert "no clipboard image tool" in result.detail.lower()


def test_non_image_clipboard_returns_not_found() -> None:
    result = read_clipboard_image(
        platform="darwin",
        which=lambda name: f"/usr/local/bin/{name}" if name == "pngpaste" else None,
        run_capture=lambda argv: b"this is just text, not an image",
    )
    assert not result.found


def test_empty_clipboard_returns_not_found() -> None:
    result = read_clipboard_image(
        platform="darwin",
        which=lambda name: f"/usr/local/bin/{name}" if name == "pngpaste" else None,
        run_capture=lambda argv: b"",
    )
    assert not result.found


@pytest.mark.skipif(sys.platform.startswith("win"), reason="posix pipe/select")
def test_default_capture_reads_stdout_bytes() -> None:
    # The bounded reader returns the helper's stdout verbatim (happy path).
    data = _default_run_capture(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'PNGBYTES')"]
    )
    assert data == b"PNGBYTES"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="posix pipe/select")
def test_default_capture_times_out_on_hanging_helper() -> None:
    # A clipboard helper that never writes and never exits must not hang the
    # editor when Ctrl+V is pressed: the bounded reader gives up after the
    # deadline, kills the helper, and reports "no image" (None) promptly.
    start = time.monotonic()
    data = _default_run_capture(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=0.3,
    )
    elapsed = time.monotonic() - start
    assert data is None
    assert elapsed < 10.0, "reader blocked on the hanging helper instead of timing out"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="posix pipe/select")
def test_default_capture_does_not_inherit_stdin() -> None:
    # The helper must not be able to consume the session's terminal stdin: its
    # stdin is /dev/null, so a stdin-reading helper sees immediate EOF and the
    # reader returns its (empty) output rather than blocking on terminal input.
    data = _default_run_capture(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
        ],
        timeout=2.0,
    )
    assert data == b""


def test_oversized_clipboard_image_is_rejected() -> None:
    # A clipboard image larger than the attachment cap must not be returned
    # (so it is never written to disk), bounding memory/disk via Ctrl+V.
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (6 * 1024 * 1024)
    result = read_clipboard_image(
        platform="darwin",
        which=lambda name: f"/usr/local/bin/{name}" if name == "pngpaste" else None,
        run_capture=lambda argv: big,
    )
    assert not result.found
    assert "too large" in result.detail.lower()
