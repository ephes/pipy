"""Focused tests for the local clipboard helper used by `/copy`.

The helper must copy through a safe OS/terminal path only: a fixed argv with
no shell, an OSC 52 terminal fallback, and never any network, provider, tool,
or auth action. These tests pin the platform selection order and the
fallbacks without touching the real system clipboard.
"""

from __future__ import annotations

import base64
import io
from typing import cast, TextIO

from pipy_harness.native.clipboard import ClipboardResult, copy_to_clipboard


class _Runner:
    def __init__(self, *, succeeds: bool = True) -> None:
        self.calls: list[tuple[list[str], bytes]] = []
        self._succeeds = succeeds

    def __call__(self, argv: list[str], data: bytes) -> bool:
        self.calls.append((argv, data))
        return self._succeeds


def _which(available: dict[str, str]):
    def resolver(name: str) -> str | None:
        return available.get(name)

    return resolver


def test_copy_uses_pbcopy_on_darwin() -> None:
    runner = _Runner()
    result = copy_to_clipboard(
        "answer text",
        platform="darwin",
        which=_which({"pbcopy": "/usr/bin/pbcopy"}),
        run=runner,
    )

    assert isinstance(result, ClipboardResult)
    assert result.copied is True
    assert result.method == "pbcopy"
    assert result.byte_count == len("answer text".encode("utf-8"))
    assert runner.calls == [(["/usr/bin/pbcopy"], b"answer text")]


def test_copy_prefers_wl_copy_then_xclip_then_xsel_on_linux() -> None:
    runner = _Runner()
    result = copy_to_clipboard(
        "linux answer",
        platform="linux",
        which=_which(
            {
                "xclip": "/usr/bin/xclip",
                "xsel": "/usr/bin/xsel",
            }
        ),
        run=runner,
    )

    assert result.copied is True
    assert result.method == "xclip"
    assert runner.calls[0][0] == ["/usr/bin/xclip", "-selection", "clipboard"]


def test_copy_uses_wl_copy_first_on_linux() -> None:
    runner = _Runner()
    result = copy_to_clipboard(
        "wayland answer",
        platform="linux",
        which=_which(
            {
                "wl-copy": "/usr/bin/wl-copy",
                "xclip": "/usr/bin/xclip",
            }
        ),
        run=runner,
    )

    assert result.method == "wl-copy"
    assert runner.calls[0][0] == ["/usr/bin/wl-copy"]


def test_copy_falls_back_to_osc52_when_no_command() -> None:
    stream = io.StringIO()
    result = copy_to_clipboard(
        "osc answer",
        platform="linux",
        which=_which({}),
        run=_Runner(),
        terminal_stream=cast(TextIO, stream),
    )

    assert result.copied is True
    assert result.method == "osc52"
    encoded = base64.b64encode("osc answer".encode("utf-8")).decode("ascii")
    assert f"\x1b]52;c;{encoded}\x07" in stream.getvalue()


def test_copy_falls_back_to_osc52_when_command_fails() -> None:
    stream = io.StringIO()
    failing = _Runner(succeeds=False)
    result = copy_to_clipboard(
        "answer",
        platform="darwin",
        which=_which({"pbcopy": "/usr/bin/pbcopy"}),
        run=failing,
        terminal_stream=cast(TextIO, stream),
    )

    assert failing.calls  # the OS command was attempted first
    assert result.method == "osc52"
    assert result.copied is True


def test_copy_reports_unavailable_without_command_or_terminal() -> None:
    result = copy_to_clipboard(
        "answer",
        platform="linux",
        which=_which({}),
        run=_Runner(),
        terminal_stream=None,
    )

    assert result.copied is False
    assert result.method == "none"
