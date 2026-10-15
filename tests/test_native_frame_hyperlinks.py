"""OSC 8 hyperlinks in the frame: the safe path the ``/mcp`` sign-in screen
uses for its authorization URL (Pi ``AuthUrlComponent`` and pi-tui's link
handling in ``wrapTextWithAnsi``)."""

from __future__ import annotations

from pipy_harness.native.ansi_wrap import (
    OSC8_CLOSE,
    hyperlink,
    visible_width,
    wrap_text_with_ansi,
)
from pipy_harness.native.frame_renderer import (
    FrameLine,
    LinkedFrameLine,
    _finish_line,
    clip_custom_text,
    sanitize_custom_text,
    visible_len,
)

_URL = "https://auth.example/authorize?client_id=abc&state=xyz"


def _open(url: str) -> str:
    return f"\x1b]8;;{url}\x1b\\"


def test_a_link_has_no_width() -> None:
    linked = hyperlink("click", _URL)
    assert linked == f"{_open(_URL)}click{OSC8_CLOSE}"
    assert visible_width(linked) == visible_len(linked) == 5


def test_a_wrapped_link_is_closed_and_opened_again_on_every_line() -> None:
    lines = wrap_text_with_ansi(hyperlink(_URL, _URL), 20)
    assert len(lines) == 3
    for line in lines:
        assert line.startswith(_open(_URL))
        assert line.endswith(OSC8_CLOSE)
    assert "".join(visible_line(line) for line in lines) == _URL


def visible_line(line: str) -> str:
    return line.replace(_open(_URL), "").replace(OSC8_CLOSE, "")


def test_links_are_kept_only_where_they_are_asked_for() -> None:
    text = f"see {hyperlink('here', _URL)}"
    assert sanitize_custom_text(text, links=True) == text
    # Elsewhere the escape is made harmless, as before.
    assert "\x1b" not in sanitize_custom_text(text)


def test_a_link_left_open_is_closed_at_the_end() -> None:
    assert sanitize_custom_text(f"{_open(_URL)}here", links=True) == (
        f"{_open(_URL)}here{OSC8_CLOSE}"
    )


def test_only_the_plain_form_is_kept() -> None:
    for text in (
        f"\x1b]8;id=1;{_URL}\x1b\\here",  # parameters
        f"\x1b]8;;{_URL}\x07here",  # BEL terminator
        "\x1b]8;;https://example/a b\x1b\\here",  # a space in the URL
        f"\x1b]8;;https://example/{'x' * 5000}\x1b\\here",  # too long
        "\x1b]0;title\x1b\\here",  # another OSC
    ):
        assert "\x1b" not in sanitize_custom_text(text, links=True), text


def test_clipping_closes_a_link_it_cuts() -> None:
    clipped = clip_custom_text(hyperlink("a" * 30, _URL), 10, links=True)
    assert clipped == f"{_open(_URL)}{'a' * 9}{OSC8_CLOSE}…"
    assert visible_len(clipped) == 10


def test_finishing_a_full_frame_keeps_the_links_of_a_linked_row() -> None:
    text = f" {hyperlink(_URL, _URL)}"
    assert _finish_line(LinkedFrameLine(text), 80, False).text == text
    padded = _finish_line(LinkedFrameLine(text), 80, True)
    assert isinstance(padded, LinkedFrameLine)
    assert visible_len(padded.text) == 80
    assert "\x1b" not in _finish_line(FrameLine(text), 80, False).text
