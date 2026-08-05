"""Focused lock/effect contracts for the extracted extension UI owners."""

from __future__ import annotations

import io
import threading
from pathlib import Path

from pipy_harness.native.extension_chrome_state import (
    ChromeRegion,
    ExtensionChromeState,
)
from pipy_harness.native.ui.components.footer import FooterComponent
from pipy_harness.native.ui.extension_chrome import ExtensionChromeComponent
from pipy_harness.native.ui.paint_lock import PaintLock
from pipy_harness.native.ui.screen import ScreenRenderInputs
from pipy_harness.native.ui.terminal_input_listeners import TerminalInputListeners


def _lock_is_available(lock: PaintLock) -> bool:
    outcome: list[bool] = []

    def probe() -> None:
        acquired = lock.acquire(timeout=0.1)
        outcome.append(acquired)
        if acquired:
            lock.release()

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    return outcome == [True]


def _chrome(
    record: ExtensionChromeState,
    lock: PaintLock,
    events: list[tuple[str, bool]],
) -> ExtensionChromeComponent:
    return ExtensionChromeComponent(
        record,
        lock,
        lambda: events.append(("repaint", _lock_is_available(lock))),
        tui_handle=object(),
        render_inputs=ScreenRenderInputs(lambda: 80, io.StringIO(), lambda: False),
        push_title=lambda: events.append(("push-title", _lock_is_available(lock))),
        write_title=lambda _title: events.append(
            ("write-title", _lock_is_available(lock))
        ),
        restore_title=lambda: events.append(
            ("restore-title", _lock_is_available(lock))
        ),
        clear_working_text=lambda: events.append(
            ("clear-working", _lock_is_available(lock))
        ),
    )


def test_chrome_state_is_atomic_repaint_unlocked_and_title_effects_serialized() -> None:
    record = ExtensionChromeState(working_visible=True)
    lock = PaintLock(threading.RLock())
    events: list[tuple[str, bool]] = []
    chrome = _chrome(record, lock, events)

    chrome.set_working_visible(False)
    chrome.set_title("accepted")
    chrome.set_title(None)

    assert record.working_visible is False
    assert record.title is None
    assert events == [
        ("clear-working", False),
        ("repaint", True),
        ("push-title", False),
        ("write-title", False),
        ("restore-title", False),
    ]


def test_concurrent_title_update_cannot_pass_an_inflight_terminal_write() -> None:
    record = ExtensionChromeState()
    lock = PaintLock(threading.RLock())
    first_effect_entered = threading.Event()
    release_first_effect = threading.Event()
    second_started = threading.Event()
    second_finished = threading.Event()
    writes: list[str] = []
    errors: list[BaseException] = []

    def write_title(title: str) -> None:
        if title == "A":
            first_effect_entered.set()
            release_first_effect.wait(timeout=2.0)
        writes.append(title)

    chrome = ExtensionChromeComponent(
        record,
        lock,
        lambda: None,
        tui_handle=object(),
        render_inputs=ScreenRenderInputs(lambda: 80, io.StringIO(), lambda: False),
        push_title=lambda: None,
        write_title=write_title,
        restore_title=lambda: None,
        clear_working_text=lambda: None,
    )

    def update(title: str, *, started: threading.Event | None = None) -> None:
        if started is not None:
            started.set()
        try:
            chrome.set_title(title)
        except BaseException as error:  # noqa: BLE001 - asserted after joining
            errors.append(error)
        finally:
            if title == "B":
                second_finished.set()

    first = threading.Thread(target=update, args=("A",))
    first.start()
    assert first_effect_entered.wait(timeout=2.0)

    second = threading.Thread(
        target=update, args=("B",), kwargs={"started": second_started}
    )
    second.start()
    assert second_started.wait(timeout=2.0)
    finished_while_first_effect_blocked = second_finished.wait(timeout=0.1)

    release_first_effect.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert finished_while_first_effect_blocked is False
    assert writes == ["A", "B"]
    assert record.title == "B"


def test_footer_factory_uses_shared_lock_but_branch_callback_and_repaint_do_not(
    tmp_path: Path,
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    head = git_dir / "HEAD"
    head.write_text("ref: refs/heads/main\n", encoding="utf-8")
    record = ExtensionChromeState()
    lock = PaintLock(threading.RLock())
    events: list[tuple[str, bool]] = []
    callbacks: list[object] = []

    def build_region(
        source: object, footer_data: object | None, _max_lines: int
    ) -> ChromeRegion:
        assert _lock_is_available(lock) is False
        registrar = getattr(footer_data, "branch_change_registrar")
        registrar(lambda: events.append(("branch-callback", _lock_is_available(lock))))
        callbacks.append(source)
        return ChromeRegion(source, None, (str(source),), 80, False)

    footer = FooterComponent(
        record,
        lock,
        lambda: events.append(("repaint", _lock_is_available(lock))),
        cwd=tmp_path,
        available_provider_count=lambda: 1,
        build_region=build_region,
        dispose_region=lambda _region: None,
        render_region=lambda *_args, **_kwargs: (),
    )
    footer.set_footer("first")
    head.write_text("ref: refs/heads/next\n", encoding="utf-8")
    record.footer_branch_last_check = 0.0
    footer.poll_branch()

    assert callbacks == ["first", "first"]
    assert record.footer_branch == "next"
    assert events == [
        ("repaint", True),
        ("branch-callback", True),
        ("repaint", True),
    ]


def test_terminal_listener_callbacks_are_unlocked_and_ordered_replacements_atomic() -> (
    None
):
    record = ExtensionChromeState()
    lock = PaintLock(threading.RLock())
    listeners = TerminalInputListeners(record, lock, lambda: None)
    seen: list[tuple[str, bool]] = []

    def replace_with_b(key: str) -> dict[str, str]:
        seen.append((key, _lock_is_available(lock)))
        return {"data": "b"}

    class Result:
        data = "c"
        consume = False

    def replace_with_c(key: str) -> Result:
        seen.append((key, _lock_is_available(lock)))
        return Result()

    listeners.add(replace_with_b)
    listeners.add(replace_with_c)

    assert listeners.apply("a") == "c"
    assert seen == [("a", True), ("b", True)]
    assert record.terminal_input_last_replaced is True

    listeners.add(lambda _key: {"consume": True, "data": "ignored"})
    assert listeners.apply("x") is None
    assert record.terminal_input_last_replaced is True
