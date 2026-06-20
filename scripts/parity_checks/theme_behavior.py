"""Parity row D7 behavior check: theme / color-scheme selection.

Drives the ACTUAL product theme picker — ``NativeToolReplSession._open_theme_selector``,
the ``/settings`` dialog's "Theme" action — with a stub selector that chooses the
``ocean`` theme. This proves the picker is wired to ``select_theme`` (not just
that the function exists) and that the selected theme changes the rendered chrome
styling: a chrome separator rendered on the default theme carries the ``pi``
palette code, and one rendered after the picker applies ``ocean`` carries the
``ocean`` palette code.

The pipy-only ``/theme`` command was removed in the 2026-06-20 cleanup; theme
selection now lives in the ``/settings`` dialog, so this check exercises that
product path rather than ``select_theme`` in isolation.

It also proves the NO_COLOR / non-TTY fallback always wins: with ``NO_COLOR`` set
(or a non-TTY stream), the picked theme emits no ANSI color styling at all, so a
theme can never override the no-color contract.

Exits 0 when every behavior holds, 1 otherwise. No real network or AI calls.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
from pathlib import Path

from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.repl_input import REPL_INPUT_RUNTIME_PLAIN
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.themes import resolve_palette
from pipy_harness.native.tool_loop_session import NativeToolReplSession

_PI_SEPARATOR = resolve_palette("pi").separator_truecolor
_OCEAN_SEPARATOR = resolve_palette("ocean").separator_truecolor

# SGR (Select Graphic Rendition / color) sequences only. The legacy tool-loop
# input editor legitimately emits cursor-control sequences (erase-line / cursor-
# up) on a real TTY even when color is disabled, so the no-color contract is
# "no styling", i.e. no SGR sequence — not "no escape byte at all".
_SGR_SEQUENCE = re.compile(r"\x1b\[[0-9;]*m")


def _has_color_styling(text: str) -> bool:
    return _SGR_SEQUENCE.search(text) is not None


class _TTYStringIO(io.StringIO):
    """A StringIO that claims to be a TTY so chrome enables color."""

    def isatty(self) -> bool:  # noqa: D401 - test stub
        return True


class _StubThemeSelectorUi:
    """Minimal terminal-UI stub that picks the named theme from the selector.

    ``_open_theme_selector`` only calls ``run_model_selector`` (to choose a row)
    and ``add_notice`` (to report the result), so this duck-typed stub drives the
    real product picker without standing up a live TUI.
    """

    def __init__(self, pick_name: str) -> None:
        self.pick_name = pick_name
        self.notices: list[str] = []
        self.selector_title: str | None = None
        self.offered_labels: list[str] = []

    def run_model_selector(self, options, *, current_index: int = 0, title=None):
        self.selector_title = title
        self.offered_labels = [option.label for option in options]
        for index, option in enumerate(options):
            if option.label.startswith(self.pick_name):
                return index
        return None

    def add_notice(self, message: str) -> None:
        self.notices.append(message)


def _new_session() -> NativeToolReplSession:
    return NativeToolReplSession(
        provider=FakeNativeProvider(supports_tool_calls=True),
        tool_registry={},
        input_runtime=REPL_INPUT_RUNTIME_PLAIN,
    )


def _run_session(*, tty: bool) -> str:
    """Run the REPL (just ``/exit``); return the rendered stderr chrome.

    The startup input separator is painted with whatever theme is currently
    active (``PIPY_THEME`` / the persisted store), so rendering once per theme
    state reveals the palette in use.
    """

    cwd = Path(tempfile.mkdtemp())
    error_stream: io.StringIO = _TTYStringIO() if tty else io.StringIO()
    _new_session().run(
        workspace_root=cwd,
        input_stream=io.StringIO("/exit\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    return error_stream.getvalue()


def _isolate_theme_env() -> None:
    """Truecolor terminal, isolated theme store, default (unset) active theme."""

    os.environ["TERM"] = "xterm-256color"
    os.environ["COLORTERM"] = "truecolor"
    os.environ.pop("PIPY_THEME", None)
    # Honored by default_native_theme_path(), so the picker's persist stays in a
    # temp file and never touches the real user theme store.
    os.environ["PIPY_NATIVE_THEME_PATH"] = str(
        Path(tempfile.mkdtemp()) / "theme.json"
    )


def _pick_ocean_via_settings_picker() -> bool:
    """Drive the real ``/settings`` theme picker to choose ocean.

    Returns True when the picker offered an ``ocean`` row and applied it (setting
    ``PIPY_THEME`` for the next render), proving the product picker is wired to
    ``select_theme``.
    """

    cwd = Path(tempfile.mkdtemp())
    ui = _StubThemeSelectorUi("ocean")
    settings = SettingsManager.for_workspace(cwd)
    _new_session()._open_theme_selector(ui, settings=settings)
    return (
        any(label.startswith("ocean") for label in ui.offered_labels)
        and os.environ.get("PIPY_THEME") == "ocean"
    )


def _selecting_theme_changes_styling() -> bool:
    os.environ.pop("NO_COLOR", None)
    _isolate_theme_env()
    before = _run_session(tty=True)
    if not _pick_ocean_via_settings_picker():
        return False
    after = _run_session(tty=True)
    # The separator before the switch uses the default pi palette; the one after
    # the picker applies ocean uses the ocean palette.
    return (_PI_SEPARATOR in before) and (_OCEAN_SEPARATOR in after)


def _no_color_switch_is_plain() -> bool:
    _isolate_theme_env()
    os.environ["NO_COLOR"] = "1"
    if not _pick_ocean_via_settings_picker():
        return False
    # NO_COLOR wins regardless of the picked theme: no color styling at all
    # (cursor-control from the editor is permitted; SGR color sequences are not).
    return not _has_color_styling(_run_session(tty=True))


def _non_tty_switch_is_plain() -> bool:
    os.environ.pop("NO_COLOR", None)
    _isolate_theme_env()
    if not _pick_ocean_via_settings_picker():
        return False
    return "\x1b[" not in _run_session(tty=False)


def main() -> int:
    if not _selecting_theme_changes_styling():
        return 1
    if not _no_color_switch_is_plain():
        return 1
    if not _non_tty_switch_is_plain():
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
