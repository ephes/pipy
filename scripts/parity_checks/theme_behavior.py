"""Parity row D7 behavior check: theme / color-scheme selection.

Drives the no-tool REPL product path with a TTY-like error stream and an
explicit ``/theme`` switch, proving that the selected theme actually changes
the rendered chrome styling mid-session: the input separator painted before
the switch carries the default ``pi`` palette code, and the separator painted
after the switch carries the chosen ``ocean`` palette code.

It also proves the NO_COLOR / non-TTY fallback always wins: with ``NO_COLOR``
set (or a non-TTY stream), the same scripted ``/theme`` switch emits no ANSI
styling at all, so a theme can never override the no-color contract.

Exits 0 when every behavior holds, 1 otherwise. No real network or AI calls.
"""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

from pipy_harness.adapters.native import PipyNativeReplAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import RunRequest
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.themes import resolve_palette
from pipy_harness.runner import HarnessRunner

_PI_SEPARATOR = resolve_palette("pi").separator_truecolor
_OCEAN_SEPARATOR = resolve_palette("ocean").separator_truecolor


class _TTYStringIO(io.StringIO):
    """A StringIO that claims to be a TTY so chrome enables color."""

    def isatty(self) -> bool:  # noqa: D401 - test stub
        return True


def _run_theme_switch(*, tty: bool) -> str:
    """Run the no-tool REPL with a ``/theme ocean`` switch; return stderr text."""

    root = Path(tempfile.mkdtemp())
    cwd = Path(tempfile.mkdtemp())
    error_stream: io.StringIO = _TTYStringIO() if tty else io.StringIO()
    adapter = PipyNativeReplAdapter(
        provider=FakeNativeProvider(),
        input_stream=io.StringIO("/theme ocean\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    HarnessRunner(adapter=adapter).run(
        RunRequest(
            agent="pipy-native",
            slug="parity-theme",
            command=[],
            cwd=cwd,
            goal="parity theme",
            root=root,
            capture_policy=CapturePolicy(),
        )
    )
    return error_stream.getvalue()


def _colored_switch_changes_styling() -> bool:
    # Enable truecolor; isolate the persisted theme store and clear any ambient
    # PIPY_THEME so the session starts on the default palette.
    os.environ["TERM"] = "xterm-256color"
    os.environ["COLORTERM"] = "truecolor"
    os.environ.pop("NO_COLOR", None)
    os.environ.pop("PIPY_THEME", None)
    os.environ["PIPY_NATIVE_THEME_PATH"] = str(
        Path(tempfile.mkdtemp()) / "theme.json"
    )
    text = _run_theme_switch(tty=True)
    # Separator before the switch uses the default pi palette; the one after
    # uses the freshly selected ocean palette.
    return (_PI_SEPARATOR in text) and (_OCEAN_SEPARATOR in text)


def _no_color_switch_is_plain() -> bool:
    os.environ["TERM"] = "xterm-256color"
    os.environ["COLORTERM"] = "truecolor"
    os.environ["NO_COLOR"] = "1"
    os.environ.pop("PIPY_THEME", None)
    os.environ["PIPY_NATIVE_THEME_PATH"] = str(
        Path(tempfile.mkdtemp()) / "theme.json"
    )
    text = _run_theme_switch(tty=True)
    # NO_COLOR wins regardless of the selected theme: no ANSI styling at all.
    return "\x1b[" not in text


def _non_tty_switch_is_plain() -> bool:
    os.environ["TERM"] = "xterm-256color"
    os.environ["COLORTERM"] = "truecolor"
    os.environ.pop("NO_COLOR", None)
    os.environ.pop("PIPY_THEME", None)
    os.environ["PIPY_NATIVE_THEME_PATH"] = str(
        Path(tempfile.mkdtemp()) / "theme.json"
    )
    text = _run_theme_switch(tty=False)
    return "\x1b[" not in text


def main() -> int:
    if not _colored_switch_changes_styling():
        return 1
    if not _no_color_switch_is_plain():
        return 1
    if not _non_tty_switch_is_plain():
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
