"""The ``timeout`` of an extension dialog in the terminal (goal check r2, S11).

Pi's interactive dialogs count a ``timeout`` down (``CountdownTimer``): the
title shows the seconds left, and when they run out the dialog is cancelled,
so ``select`` and ``input`` give no value and ``confirm`` gives ``False``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO, cast

import pytest
from test_native_extension_custom_ui import _InputBuffer, _ui

from pipy_harness.native.extension_ui import _CollectingUi
from pipy_harness.native.terminal_driver import TerminalDriver
from pipy_harness.native.tui import _LiveExtensionUiDriver
from pipy_harness.native.ui.components.extension_prompts import (
    ExtensionSelectComponent,
)
from pipy_harness.native.ui.modal_driver import _DialogCountdown
from pipy_harness.native.ui.screen import KEY_WAIT_ENDED, Screen


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_the_countdown_shows_the_seconds_left_and_cancels_at_zero() -> None:
    clock = _Clock()
    results: list[object] = []
    repaints: list[str] = []
    component = ExtensionSelectComponent("Pick", ("a", "b"), results.append)
    countdown = _DialogCountdown(
        component,
        2500,
        results.append,
        lambda: repaints.append(component.title),
        clock,
    )

    assert component.title == "Pick (3s)"
    assert countdown.poll() is False
    clock.now += 0.5
    assert countdown.poll() is False
    assert repaints == []
    clock.now += 1.0
    assert countdown.poll() is False
    assert repaints == ["Pick (2s)"]
    clock.now += 1.6
    assert countdown.poll() is True
    assert results == [None]


def _waiting_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """No key is pressed: the modal waits, polling, until its poll ends it."""

    monkeypatch.setattr(TerminalDriver, "enter_raw_mode", lambda _self: None)
    monkeypatch.setattr(TerminalDriver, "restore_terminal_mode", lambda _self: None)

    def read(_self: Screen, _fd: int, poll: Callable[[], bool] | None = None) -> str:
        assert poll is not None, "a dialog without a timeout waits for a key"
        deadline = time.monotonic() + 5.0
        while not poll():
            assert time.monotonic() < deadline
            time.sleep(0.02)
        return KEY_WAIT_ENDED

    monkeypatch.setattr(Screen, "read_key_polling_resize", read)


@pytest.mark.parametrize(
    ("method", "args", "expected"),
    [
        ("run_extension_select", ("Pick", ("a", "b")), None),
        ("run_extension_input", ("Name", "placeholder"), None),
        ("run_extension_confirm", ("Sure?", "really"), False),
    ],
)
def test_a_terminal_dialog_ends_when_its_timeout_passes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    method: str,
    args: Sequence[object],
    expected: object,
) -> None:
    ui = _ui(tmp_path, input_stream=cast(TextIO, _InputBuffer()))
    _waiting_keys(monkeypatch)
    started = time.monotonic()

    result = getattr(ui.components.modals, method)(*args, timeout=300)

    assert result == expected
    assert 0.2 < time.monotonic() - started < 3.0


def test_the_terminal_driver_takes_the_timeout_of_an_extension_dialog() -> None:
    calls: list[tuple[str, int | None]] = []

    class _Modals:
        def run_extension_select(
            self, title: str, options: Sequence[str], *, timeout: int | None = None
        ) -> str | None:
            calls.append(("select", timeout))
            return None

        def run_extension_input(
            self, title: str, placeholder: str | None = None, *, timeout=None
        ) -> str | None:
            calls.append(("input", timeout))
            return None

        def run_extension_confirm(
            self, title: str, message: str, *, timeout: int | None = None
        ) -> bool:
            calls.append(("confirm", timeout))
            return False

    driver = object.__new__(_LiveExtensionUiDriver)
    driver._modals = _Modals()  # type: ignore[assignment]
    ui = _CollectingUi(has_ui=True, ui_driver=cast(object, driver))  # type: ignore[arg-type]

    ui.select("Pick", ["a"], timeout=1000)
    ui.input("Name", timeout=2000)
    ui.confirm("Sure?", "really", timeout=3000)
    ui.confirm("Sure?", "really")

    assert calls == [
        ("select", 1000),
        ("input", 2000),
        ("confirm", 3000),
        ("confirm", None),
    ]
