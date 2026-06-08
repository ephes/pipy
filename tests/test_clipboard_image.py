"""Tests for the OS clipboard-image read helper (Pi Ctrl+V parity)."""

from __future__ import annotations

from pipy_harness.native.clipboard import read_clipboard_image

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
