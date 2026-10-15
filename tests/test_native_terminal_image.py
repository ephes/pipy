"""Inline terminal images: protocol detection and escape bytes (pi-tui
``terminal-image.ts`` and ``components/image.ts``)."""

from __future__ import annotations

import base64
import struct

import pytest

from pipy_harness.native.terminal_image import (
    TerminalImage,
    calculate_image_cell_size,
    clean_base64,
    delete_kitty_image,
    detect_image_protocol,
    encode_iterm2,
    encode_kitty,
    image_rows,
)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, None),
        ({"TERM": "xterm-256color"}, None),
        ({"KITTY_WINDOW_ID": "1"}, "kitty"),
        ({"TERM_PROGRAM": "kitty"}, "kitty"),
        ({"TERM_PROGRAM": "ghostty"}, "kitty"),
        ({"TERM": "xterm-ghostty"}, "kitty"),
        ({"GHOSTTY_RESOURCES_DIR": "/x"}, "kitty"),
        ({"WEZTERM_PANE": "0"}, "kitty"),
        ({"TERM_PROGRAM": "WezTerm"}, "kitty"),
        ({"TERM_PROGRAM": "WarpTerminal"}, "kitty"),
        ({"WARP_SESSION_ID": "1"}, "kitty"),
        ({"TERM_PROGRAM": "herdr", "ITERM_SESSION_ID": "x"}, "kitty"),
        ({"ITERM_SESSION_ID": "w0t0p0"}, "iterm2"),
        ({"TERM_PROGRAM": "iTerm.app"}, "iterm2"),
        ({"TERM_PROGRAM": "vscode"}, None),
        ({"TERM_PROGRAM": "Apple_Terminal"}, None),
        # Under tmux and screen images are off, whatever the outer terminal.
        ({"TMUX": "/tmp/tmux-1/default,1,0", "KITTY_WINDOW_ID": "1"}, None),
        ({"TERM": "tmux-256color", "TERM_PROGRAM": "ghostty"}, None),
        ({"TERM": "screen", "ITERM_SESSION_ID": "x"}, None),
        # An empty variable counts as unset, as in Node.
        ({"KITTY_WINDOW_ID": ""}, None),
    ],
)
def test_the_protocol_follows_pi_tuis_detection(
    env: dict[str, str], expected: str | None
) -> None:
    assert detect_image_protocol(env) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("kitty", "kitty"),
        ("ITERM2", "iterm2"),
        ("none", None),
        ("0", None),
        ("sixel", "kitty"),
    ],
)
def test_pipy_image_protocol_overrides_the_detection(
    value: str, expected: str | None
) -> None:
    env = {"PIPY_IMAGE_PROTOCOL": value, "TERM_PROGRAM": "ghostty"}
    assert detect_image_protocol(env) == expected
    assert detect_image_protocol({"PIPY_IMAGE_PROTOCOL": "kitty", "TMUX": "1"}) == (
        "kitty"
    )


def test_only_plain_base64_is_accepted() -> None:
    assert clean_base64("aGVs\nbG8h\r\n") == "aGVsbG8h"
    assert clean_base64("aGVsbA==") == "aGVsbA=="
    for bad in (
        "",
        "aGVsbA=",
        "aGVs\x1b\\bG8h",
        "aGVs\x07bG8h",
        "aGVs-_8h",
        "aGVsbA==aGVs",
        "a" * 5,
    ):
        assert clean_base64(bad) is None, bad


def test_cell_size_matches_pi_tui() -> None:
    # 640x480 at 60 cells: 60 columns, 60*9/640*480/18 = 22.5 -> 23 rows.
    assert calculate_image_cell_size(640, 480, 60) == (60, 23)
    # Bounded by the height: 30 rows of 18 px.
    assert calculate_image_cell_size(100, 1000, 60, 30) == (6, 30)
    # Kitty picks the rounding that distorts less: 22.5 ideal rows keep 23,
    # 22.4 ideal rows (750x560) go down to 22.
    assert calculate_image_cell_size(640, 480, 60, 30, optimize_aspect_ratio=True) == (
        60,
        23,
    )
    assert calculate_image_cell_size(750, 560, 60, 30) == (60, 23)
    assert calculate_image_cell_size(750, 560, 60, 30, optimize_aspect_ratio=True) == (
        60,
        22,
    )
    # A small image is scaled up to the width, as in Pi.
    assert calculate_image_cell_size(1, 1, 60) == (60, 30)


def test_kitty_bytes_are_pi_tuis() -> None:
    assert encode_kitty("QUJD", columns=4, rows=2, image_id=7) == (
        "\x1b_Ga=T,f=100,q=2,C=1,c=4,r=2,i=7;QUJD\x1b\\"
    )
    data = "A" * 4096 * 2 + "QUJD"
    chunked = encode_kitty(data, columns=4, rows=2, image_id=7)
    assert chunked == (
        f"\x1b_Ga=T,f=100,q=2,C=1,c=4,r=2,i=7,m=1;{'A' * 4096}\x1b\\"
        f"\x1b_Gm=1;{'A' * 4096}\x1b\\"
        "\x1b_Gm=0;QUJD\x1b\\"
    )
    assert delete_kitty_image(7) == "\x1b_Ga=d,d=I,i=7,q=2\x1b\\"


def test_iterm2_bytes_are_pi_tuis() -> None:
    data = base64.b64encode(b"hello").decode()
    assert encode_iterm2(data, width=12, height=4) == (
        f"\x1b]1337;File=inline=1;size=5;width=12;height=4:{data}\x07"
    )


def _png(width: int, height: int) -> str:
    raw = b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII", 13, b"IHDR", width, height)
    return base64.b64encode(raw + bytes(8)).decode()


def _image(
    protocol: str = "kitty", mime: str = "image/png", **kw: object
) -> TerminalImage:
    values: dict[str, object] = {
        "data": _png(640, 480),
        "mime_type": mime,
        "protocol": protocol,
        "max_width_cells": 60,
        "image_id": 42,
        "size": (640, 480),
        "fallback": "[Image: [image/png] 640x480]",
    }
    values.update(kw)
    return TerminalImage(**values)  # type: ignore[arg-type]


def test_a_kitty_image_is_drawn_from_its_last_row() -> None:
    rows = image_rows(_image(), width=100, height=40)
    assert rows is not None
    assert (rows.rows, rows.kitty_id) == (23, 42)
    assert rows.last.startswith("\x1b[22A\x1b_Ga=T,f=100,q=2,C=1,c=60,r=23,i=42;")
    assert rows.last.endswith("\x1b\\\x1b[22B")


def test_an_iterm2_image_is_drawn_from_its_last_row() -> None:
    rows = image_rows(_image("iterm2", "image/jpeg"), width=100, height=40)
    assert rows is not None
    assert (rows.rows, rows.kitty_id) == (23, None)
    assert rows.last.startswith("\x1b[22A\x1b]1337;File=inline=1;")
    assert ";width=60;height=23:" in rows.last


def test_an_iterm2_image_stays_in_its_reserved_rows() -> None:
    # Three columns rounded up from 2.4 would be 15 rows high with `auto`.
    tall = _image("iterm2", size=(100, 1000))
    rows = image_rows(tall, width=100, height=12)
    assert rows is not None and rows.rows == 12
    assert ";width=3;height=12:" in rows.last


def test_the_image_fits_the_width_and_the_screen() -> None:
    narrow = image_rows(_image(), width=30, height=40)
    assert narrow is not None and ",c=28," in narrow.last
    short = image_rows(_image(max_width_cells=200), width=300, height=12)
    assert short is not None and short.rows == 12
    one = image_rows(_image(size=(800, 10)), width=100, height=40)
    assert one is not None and one.rows == 1
    assert one.last.startswith("\x1b_G") and one.last.endswith("\x1b\\")


def test_what_cannot_be_drawn_gives_the_text_line() -> None:
    # Kitty takes PNG only and pipy has no transcoder.
    assert image_rows(_image(mime="image/jpeg"), width=100, height=40) is None
    assert image_rows(_image(data=None), width=100, height=40) is None
