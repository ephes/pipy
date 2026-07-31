from __future__ import annotations

import io
import threading
from contextlib import AbstractContextManager
from pathlib import Path
from types import SimpleNamespace
from collections.abc import Callable
from typing import Any, cast

import pytest

import pipy_harness.native.tool_loop_session as tool_loop_session
from pipy_harness.native.extension_chrome_state import (
    ExtensionChromeAttachResult,
    ExtensionChromeEvent,
    ExtensionChromeSink,
    ExtensionChromeSnapshot,
)
from pipy_harness.native.coding.commands import (
    CodingCommandAction,
    CodingCommandFooterPolicy,
    CodingCommandOutcome,
    CodingCommandOutcomeKind,
)
from pipy_harness.native.extension_hooks import _ExtensionLifecycleAgentEventAdapter
from pipy_harness.native.extension_runtime import _ExtensionCandidate
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.session_generation import (
    SessionExtensionGeneration,
    SessionGenerationRef,
)
from pipy_harness.native.tool_loop_session import _ReloadCommandEffects
from pipy_harness.native.tui import (
    ExtensionChromeCommitToken,
    ExtensionChromePrepareInput,
    ExtensionChromePreparePort,
    ToolLoopTerminalUi,
    _LiveExtensionUiDriver,
)


def test_chrome_prepare_port_refuses_or_returns_only_inert_prepared_data() -> None:
    sink = ExtensionChromeSink()
    prepared_input = ExtensionChromePrepareInput(sink)
    token = ExtensionChromeCommitToken(prepared_input)

    refusing = cast(ExtensionChromePreparePort, lambda _value: None)
    accepting: ExtensionChromePreparePort = ExtensionChromeCommitToken

    assert prepared_input.candidate is sink
    assert token.prepared is prepared_input
    assert not hasattr(token, "commit")
    assert not hasattr(token, "commit_callback")
    assert refusing(prepared_input) is None
    accepted = accepting(prepared_input)
    assert accepted is not None and accepted.prepared is prepared_input


class _FakeTerminalUi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.reconciles: list[ExtensionChromeSnapshot] = []
        self.retirement_scopes: list[Callable[[], AbstractContextManager[None]]] = []
        self.semantic_committed = False

    def reconcile_extension_chrome(
        self,
        snapshot: ExtensionChromeSnapshot,
        *,
        retirement_scope: Callable[[], AbstractContextManager[None]],
    ) -> dict[int, object]:
        assert self.semantic_committed
        self.retirement_scopes.append(retirement_scope)
        self.calls.append(("reconcile", snapshot))
        self.reconciles.append(snapshot)
        return {
            listener_id: (lambda: None)
            for listener_id, _handler in snapshot.terminal_input_listeners
        }

    def set_extension_widget(
        self, key: str, content: object, *, placement: str
    ) -> None:
        self.calls.append(("widget", (key, content, placement)))

    def set_extension_header(self, factory: object | None) -> None:
        self.calls.append(("header", factory))

    def set_extension_footer(self, factory: object | None) -> None:
        self.calls.append(("footer", factory))

    def set_extension_title(self, title: str | None) -> None:
        self.calls.append(("title", title))

    def set_extension_working_indicator(self, frames: object, interval: object) -> None:
        self.calls.append(("indicator", (frames, interval)))

    def set_extension_hidden_thinking_label(self, label: str | None) -> None:
        self.calls.append(("hidden", label))

    def add_extension_autocomplete_provider(self, factory: object) -> None:
        self.calls.append(("autocomplete", factory))

    def set_editor_component(self, factory: object | None) -> None:
        self.calls.append(("editor", factory))

    def add_extension_terminal_input_listener(self, handler: object):
        self.calls.append(("listener", handler))
        return lambda: self.calls.append(("listener-dispose", handler))


def _assert_driver_guard_released(driver: _LiveExtensionUiDriver) -> None:
    acquired: list[bool] = []

    def probe() -> None:
        ok = driver._sink_guard.acquire(  # noqa: SLF001 - lock instrumentation
            timeout=1.0
        )
        acquired.append(ok)
        if ok:
            driver._sink_guard.release()  # noqa: SLF001 - lock instrumentation

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(timeout=2.0)
    assert acquired == [True]


def _assert_guard_released(sink: ExtensionChromeSink) -> None:
    acquired: list[bool] = []

    def probe() -> None:
        ok = sink._guard.acquire(timeout=1.0)  # noqa: SLF001 - lock instrumentation
        acquired.append(ok)
        if ok:
            sink._guard.release()  # noqa: SLF001 - lock instrumentation

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(timeout=2.0)
    assert acquired == [True]


def test_sidecar_guard_never_spans_delivery_session_lock_or_disposal() -> None:
    sink = ExtensionChromeSink()
    session_mutex = threading.Lock()
    delivered: list[str] = []
    disposed: list[str] = []

    def deliver(event: ExtensionChromeEvent) -> object:
        _assert_guard_released(sink)
        with session_mutex:
            delivered.append(event.kind)
        if event.kind == "listener":
            handler = cast(str, event.values[1])

            def dispose() -> None:
                _assert_guard_released(sink)
                disposed.append(handler)

            return dispose
        return None

    assert sink.attach(deliver)
    handler = cast(Any, "callback-identity")
    dispose = sink.add_terminal_input_listener(handler)
    sink.set_title("paint-outside-guard")
    dispose()
    sink.add_terminal_input_listener(cast(Any, "close-disposer-identity"))
    sink.close()

    assert delivered == ["reconcile", "listener", "title", "listener"]
    assert disposed == ["callback-identity", "close-disposer-identity"]


class _PausedWriteSink(ExtensionChromeSink):
    def __init__(self) -> None:
        super().__init__()
        self.write_entered = threading.Event()
        self.write_release = threading.Event()

    def _write(self, event: ExtensionChromeEvent, mutate: Callable[[], object]) -> None:
        self.write_entered.set()
        self.write_release.wait()
        super()._write(event, mutate)


class _CountingCloseSink(ExtensionChromeSink):
    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        super().close()


class _PausedListenerSink(ExtensionChromeSink):
    def __init__(self) -> None:
        super().__init__()
        self.listener_entered = threading.Event()
        self.listener_release = threading.Event()

    def add_terminal_input_listener(
        self, handler: Callable[[str], object]
    ) -> Callable[[], None]:
        self.listener_entered.set()
        self.listener_release.wait()
        return super().add_terminal_input_listener(handler)


@pytest.mark.parametrize(
    "write",
    [
        lambda sink: sink.set_widget("k", object(), "above_editor"),
        lambda sink: sink.set_header(object()),
        lambda sink: sink.set_footer(object()),
        lambda sink: sink.set_title("candidate"),
        lambda sink: sink.set_working_indicator((".", "o"), 40),
        lambda sink: sink.set_hidden_thinking_label("hidden"),
        lambda sink: sink.add_autocomplete_provider(lambda base: base),
        lambda sink: sink.set_editor_component(lambda *_args: object()),
    ],
)
def test_retained_class_b_writes_cross_the_close_boundary_deterministically(
    write,
) -> None:
    sink = _PausedWriteSink()
    returns: list[object] = []

    thread = threading.Thread(target=lambda: returns.append(write(sink)))
    thread.start()
    assert sink.write_entered.wait(timeout=2.0)
    sink.close()
    sink.write_release.set()
    thread.join(timeout=2.0)

    assert returns == [None]
    assert not thread.is_alive()
    assert sink.snapshot() == ExtensionChromeSnapshot()
    assert write(sink) is None
    assert sink.snapshot() == ExtensionChromeSnapshot()


def test_terminal_listener_crosses_the_close_boundary_and_returns_disposer() -> None:
    sink = _PausedListenerSink()
    disposers: list[object] = []

    thread = threading.Thread(
        target=lambda: disposers.append(
            sink.add_terminal_input_listener(lambda key: key)
        )
    )
    thread.start()
    assert sink.listener_entered.wait(timeout=2.0)
    sink.close()
    sink.listener_release.set()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert len(disposers) == 1 and callable(disposers[0])
    assert cast(Any, disposers[0])() is None
    late = sink.add_terminal_input_listener(lambda key: key)
    assert late() is None
    assert sink.snapshot() == ExtensionChromeSnapshot()


def test_attach_queues_concurrent_title_and_listener_until_snapshot_finishes() -> None:
    sink = ExtensionChromeSink()
    sink.set_title("before")
    reconcile_entered = threading.Event()
    reconcile_release = threading.Event()
    delivered: list[ExtensionChromeEvent] = []
    attached: list[ExtensionChromeAttachResult] = []

    def delivery(event: ExtensionChromeEvent) -> object:
        delivered.append(event)
        if event.kind == "reconcile":
            reconcile_entered.set()
            reconcile_release.wait()
            return {}
        if event.kind == "listener":
            return lambda: None
        return None

    thread = threading.Thread(target=lambda: attached.append(sink.attach(delivery)))
    thread.start()
    assert reconcile_entered.wait(timeout=2.0)
    sink.set_title("during")
    sink.add_terminal_input_listener(lambda key: key)
    assert [event.kind for event in delivered] == ["reconcile"]
    reconcile_release.set()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert len(attached) == 1 and attached[0].attached
    assert [event.kind for event in delivered] == ["reconcile", "title", "listener"]
    assert cast(ExtensionChromeSnapshot, delivered[0].values[0]).title == "before"
    assert sink.snapshot().title == "during"
    assert len(sink.snapshot().terminal_input_listeners) == 1


def test_stale_reconcile_disposers_are_isolated_without_masking_attach() -> None:
    sink = ExtensionChromeSink()
    first = sink.add_terminal_input_listener(lambda key: key)
    second = sink.add_terminal_input_listener(lambda key: key)
    reconcile_entered = threading.Event()
    reconcile_release = threading.Event()
    cleaned: list[str] = []

    def bad_disposer() -> None:
        cleaned.append("bad")
        raise RuntimeError("injected stale disposer failure")

    def delivery(event: ExtensionChromeEvent) -> object:
        if event.kind == "reconcile":
            reconcile_entered.set()
            reconcile_release.wait()
            return {0: bad_disposer, 1: lambda: cleaned.append("good")}
        return None

    attached: list[ExtensionChromeAttachResult] = []
    thread = threading.Thread(target=lambda: attached.append(sink.attach(delivery)))
    thread.start()
    assert reconcile_entered.wait(timeout=2.0)
    first()
    second()
    reconcile_release.set()
    thread.join(timeout=2.0)

    assert len(attached) == 1 and attached[0].attached
    assert cleaned == ["bad", "good"]


def test_candidate_listener_and_callback_identities_close_without_delivery() -> None:
    sink = ExtensionChromeSink()

    def handler(key: str) -> str:
        return key

    def autocomplete(base: object) -> object:
        return base

    def editor(*_args: object) -> object:
        return object()

    def header(*_args: object) -> object:
        return object()

    dispose = sink.add_terminal_input_listener(handler)
    sink.add_autocomplete_provider(autocomplete)
    sink.set_editor_component(editor)
    sink.set_header(header)
    snapshot = sink.snapshot()

    assert snapshot.terminal_input_listeners[0][1] is handler
    assert snapshot.autocomplete_providers[0] is autocomplete
    assert snapshot.editor_component is editor
    assert snapshot.header is header

    sink.close()
    dispose()
    inert = sink.add_terminal_input_listener(handler)
    assert callable(inert)
    assert inert() is None
    assert sink.snapshot() == ExtensionChromeSnapshot()


def test_live_driver_handoff_queues_write_and_targets_accepted_owner() -> None:
    ui = _FakeTerminalUi()
    ui.semantic_committed = True
    driver = _LiveExtensionUiDriver(cast(Any, ui), Path("."))
    driver.set_title("old")
    candidate = driver.new_candidate_sink()
    candidate.set_title("candidate")
    original_reconcile = ui.reconcile_extension_chrome
    entered = threading.Event()
    release = threading.Event()

    def blocking_reconcile(
        snapshot: ExtensionChromeSnapshot,
        *,
        retirement_scope: Callable[[], AbstractContextManager[None]],
    ) -> dict[int, object]:
        entered.set()
        release.wait()
        return original_reconcile(snapshot, retirement_scope=retirement_scope)

    ui.reconcile_extension_chrome = blocking_reconcile  # type: ignore[method-assign]
    accepted: list[object] = []
    accept_thread = threading.Thread(
        target=lambda: accepted.append(driver.accept_candidate(candidate))
    )
    accept_thread.start()
    assert entered.wait(timeout=2.0)
    writer = threading.Thread(target=lambda: driver.set_title("new-owner-write"))
    writer.start()
    writer.join(timeout=2.0)
    assert not writer.is_alive()
    assert candidate.snapshot().title == "candidate"
    release.set()
    accept_thread.join(timeout=2.0)
    writer.join(timeout=2.0)

    assert cast(Any, accepted[0]).accepted
    assert not accept_thread.is_alive() and not writer.is_alive()
    assert candidate.snapshot().title == "new-owner-write"
    assert ui.calls[-1] == ("title", "new-owner-write")


@pytest.mark.parametrize(
    "failure_type",
    [KeyboardInterrupt, SystemExit, RuntimeError],
    ids=["keyboard-interrupt", "system-exit", "ordinary-error"],
)
def test_handoff_wait_failure_restores_previous_without_leaks_or_lost_writes(
    monkeypatch: pytest.MonkeyPatch,
    failure_type: type[BaseException],
) -> None:
    ui = _FakeTerminalUi()
    ui.semantic_committed = True
    driver = _LiveExtensionUiDriver(cast(Any, ui), Path("."))
    previous = driver._active_sink  # noqa: SLF001 - ownership instrumentation
    lease_entered = threading.Event()
    lease_release = threading.Event()
    wait_entered = threading.Event()
    guard_checks: list[tuple[str, object]] = []

    original_set_title = ui.set_extension_title

    def blocking_guarded_title(title: str | None) -> None:
        _assert_driver_guard_released(driver)
        guard_checks.append(("title", title))
        original_set_title(title)
        if title == "lease-holder":
            lease_entered.set()
            assert lease_release.wait(timeout=2.0)

    original_reconcile = ui.reconcile_extension_chrome

    def guarded_reconcile(
        snapshot: ExtensionChromeSnapshot,
        *,
        retirement_scope: Callable[[], AbstractContextManager[None]],
    ) -> dict[int, object]:
        _assert_driver_guard_released(driver)
        guard_checks.append(("reconcile", snapshot.title))
        return original_reconcile(snapshot, retirement_scope=retirement_scope)

    ui.set_extension_title = blocking_guarded_title  # type: ignore[method-assign]
    ui.reconcile_extension_chrome = guarded_reconcile  # type: ignore[method-assign]

    writer_errors: list[BaseException] = []

    def hold_active_lease() -> None:
        try:
            driver.set_title("lease-holder")
        except BaseException as error:  # noqa: BLE001 - asserted below
            writer_errors.append(error)

    lease_thread = threading.Thread(target=hold_active_lease)
    lease_thread.start()
    assert lease_entered.wait(timeout=2.0)
    assert driver._active_sink_leases == 1  # noqa: SLF001

    original_wait = driver._sink_idle.wait  # noqa: SLF001

    def failing_wait(timeout: float | None = None) -> bool:
        wait_entered.set()
        original_wait(timeout)
        raise failure_type("injected condition wait failure")

    candidate = driver.new_candidate_sink()
    candidate.set_title("failed-candidate")
    acceptance_errors: list[BaseException] = []

    def accept() -> None:
        try:
            driver.accept_candidate(candidate)
        except BaseException as error:  # noqa: BLE001 - asserted below
            acceptance_errors.append(error)

    with monkeypatch.context() as patch:
        patch.setattr(driver._sink_idle, "wait", failing_wait)  # noqa: SLF001
        accept_thread = threading.Thread(target=accept)
        accept_thread.start()
        assert wait_entered.wait(timeout=2.0)
        driver.set_title("queued-during-wait")
        assert ("title", "queued-during-wait") not in ui.calls
        lease_release.set()
        lease_thread.join(timeout=2.0)
        accept_thread.join(timeout=2.0)

    assert not lease_thread.is_alive() and not accept_thread.is_alive()
    assert writer_errors == []
    assert len(acceptance_errors) == 1
    assert type(acceptance_errors[0]) is failure_type
    assert driver._handoff is None  # noqa: SLF001
    assert driver._active_sink_leases == 0  # noqa: SLF001
    assert not cast(Any, driver._sink_idle)._waiters  # noqa: SLF001
    assert driver.owns_sink(previous)
    assert previous.snapshot().title == "queued-during-wait"
    assert ui.calls.count(("title", "queued-during-wait")) == 1
    assert ("reconcile", "failed-candidate") not in guard_checks

    driver.set_title("later-live-write")
    assert ui.calls.count(("title", "later-live-write")) == 1
    subsequent = driver.new_candidate_sink()
    subsequent.set_title("subsequent-candidate")
    result = driver.accept_candidate(subsequent)

    assert result.accepted
    assert driver.owns_sink(subsequent)
    assert ui.calls.count(("title", "queued-during-wait")) == 1
    assert ("reconcile", "subsequent-candidate") in guard_checks
    assert ("title", "later-live-write") in guard_checks


def test_explicit_reconcile_retirement_route_drops_disposal_reentry_without_fallback() -> (
    None
):
    ui = _FakeTerminalUi()
    ui.semantic_committed = True
    driver = _LiveExtensionUiDriver(cast(Any, ui), Path("."))
    session_mutex = threading.Lock()
    driver.add_terminal_input_listener(lambda key: key)
    previous = driver._active_sink  # noqa: SLF001 - ownership instrumentation
    disposal_reentry: list[str] = []

    def editor_factory(_tui: object, _theme: object, _keybindings: object) -> object:
        _assert_driver_guard_released(driver)
        return object()

    def retired_disposer() -> None:
        _assert_driver_guard_released(driver)
        driver.set_title("retired-sink-disposal-reentry")
        disposal_reentry.append("retired-sink")

    previous._terminal_input_disposers[0] = retired_disposer  # noqa: SLF001
    candidate = driver.new_candidate_sink()
    candidate.set_editor_component(editor_factory)
    original_reconcile = ui.reconcile_extension_chrome

    def instrumented_reconcile(
        snapshot: ExtensionChromeSnapshot,
        *,
        retirement_scope: Callable[[], AbstractContextManager[None]],
    ) -> dict[int, object]:
        _assert_driver_guard_released(driver)
        with retirement_scope():
            driver.set_title("reconcile-disposal-reentry")
            disposal_reentry.append("reconcile")
        with session_mutex:
            if snapshot.editor_component is not None:
                cast(
                    Callable[[object, object, object], object],
                    snapshot.editor_component,
                )(object(), object(), object())
            return original_reconcile(
                snapshot,
                retirement_scope=retirement_scope,
            )

    ui.reconcile_extension_chrome = instrumented_reconcile  # type: ignore[method-assign]
    effects, _ctl = _effects(ui=ui, driver=driver)
    object.__setattr__(
        effects,
        "emitter",
        SimpleNamespace(fire_lifecycle=lambda *_args, **_kwargs: None),
    )
    errors: list[BaseException] = []

    def accept() -> None:
        try:
            effects._finish_candidate_chrome(candidate, replacement_accepted=True)
        except BaseException as error:  # noqa: BLE001 - asserted at boundary
            errors.append(error)

    thread = threading.Thread(target=accept)
    thread.start()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert errors == []
    assert disposal_reentry == ["reconcile", "retired-sink"]
    assert ui.retirement_scopes == [driver._retiring_disposal_route]  # noqa: SLF001
    assert driver.owns_sink(candidate)
    assert candidate.snapshot().title is None


def test_early_closed_candidate_refusal_keeps_previous_without_double_close() -> None:
    ui = _FakeTerminalUi()
    ui.semantic_committed = True
    driver = _LiveExtensionUiDriver(cast(Any, ui), Path("."))
    driver.set_title("old")
    previous = driver._active_sink  # noqa: SLF001 - ownership assertion
    candidate = _CountingCloseSink()
    candidate.set_title("candidate")
    candidate.close()
    effects, _ctl = _effects(ui=ui, driver=driver)
    object.__setattr__(
        effects,
        "emitter",
        SimpleNamespace(fire_lifecycle=lambda *_args, **_kwargs: None),
    )

    diagnostic = effects._finish_candidate_chrome(candidate, replacement_accepted=True)

    assert diagnostic == "pipy: extension chrome candidate is closed."
    assert candidate.close_calls == 1
    assert driver.owns_sink(previous)
    assert ui.reconciles == []
    before = len(ui.calls)
    driver.set_title("old-still-live")
    assert ui.calls[before:] == [("title", "old-still-live")]


def test_post_reconcile_close_restores_previous_and_cleans_candidate_once() -> None:
    ui = _FakeTerminalUi()
    ui.semantic_committed = True
    driver = _LiveExtensionUiDriver(cast(Any, ui), Path("."))
    driver.set_title("old")

    def old_listener(key: str) -> str:
        return key

    driver.add_terminal_input_listener(old_listener)
    previous = driver._active_sink  # noqa: SLF001 - ownership assertion
    candidate = _CountingCloseSink()
    candidate.set_title("candidate")

    def candidate_listener(key: str) -> str:
        return key

    candidate.add_terminal_input_listener(candidate_listener)
    original_reconcile = ui.reconcile_extension_chrome
    candidate_reconcile_entered = threading.Event()
    candidate_reconcile_release = threading.Event()
    candidate_disposals: list[str] = []

    def blocking_candidate_reconcile(
        snapshot: ExtensionChromeSnapshot,
        *,
        retirement_scope: Callable[[], AbstractContextManager[None]],
    ) -> dict[int, object]:
        if snapshot.title == "candidate":
            candidate_reconcile_entered.set()
            assert candidate_reconcile_release.wait(timeout=2.0)
        result = original_reconcile(snapshot, retirement_scope=retirement_scope)
        if snapshot.title == "candidate":
            return {
                listener_id: lambda: candidate_disposals.append("candidate")
                for listener_id, _handler in snapshot.terminal_input_listeners
            }
        return result

    ui.reconcile_extension_chrome = blocking_candidate_reconcile  # type: ignore[method-assign]
    effects, _ctl = _effects(ui=ui, driver=driver)
    object.__setattr__(
        effects,
        "emitter",
        SimpleNamespace(fire_lifecycle=lambda *_args, **_kwargs: None),
    )
    diagnostics: list[str | None] = []
    accept_thread = threading.Thread(
        target=lambda: diagnostics.append(
            effects._finish_candidate_chrome(candidate, replacement_accepted=True)
        )
    )
    accept_thread.start()
    assert candidate_reconcile_entered.wait(timeout=2.0)
    close_thread = threading.Thread(target=candidate.close)
    close_thread.start()
    with candidate._guard:  # noqa: SLF001 - deterministic close phase
        if not candidate._closed:  # noqa: SLF001
            candidate._idle.wait(timeout=0.1)  # noqa: SLF001
        assert candidate._closed  # noqa: SLF001
    candidate_reconcile_release.set()
    accept_thread.join(timeout=2.0)
    close_thread.join(timeout=2.0)

    assert not accept_thread.is_alive() and not close_thread.is_alive()
    assert diagnostics == [
        "pipy: extension chrome candidate closed during reconciliation; "
        "restored the previous chrome."
    ]
    assert candidate.close_calls == 1
    assert candidate.snapshot() == ExtensionChromeSnapshot()
    assert driver.owns_sink(previous)
    assert ui.reconciles[-1] == previous.snapshot()
    assert [snapshot.title for snapshot in ui.reconciles] == ["candidate", "old"]
    assert candidate_disposals == ["candidate"]
    before = len(ui.calls)
    driver.set_title("old-restored")
    assert ui.calls[before:] == [("title", "old-restored")]


def test_throwing_old_editor_text_fails_soft_during_accepted_reconcile() -> None:
    terminal = io.StringIO()
    ui = ToolLoopTerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=terminal,
        cwd=Path("."),
    )
    driver = _LiveExtensionUiDriver(ui, Path("."))
    disposed: list[str] = []

    class _ExtensionEditorFailure(BaseException):
        pass

    class _OldEditor:
        def get_text(self) -> str:
            raise _ExtensionEditorFailure("must stay bounded")

        def set_text(self, _text: str) -> None:
            return None

        def render(self, _width: int) -> list[str]:
            return ["old editor"]

        def dispose(self) -> None:
            disposed.append("old")

    class _NewEditor:
        def __init__(self) -> None:
            self.text = ""

        def get_text(self) -> str:
            return self.text

        def set_text(self, text: str) -> None:
            self.text = text

        def render(self, _width: int) -> list[str]:
            return [self.text]

    old_editor = _OldEditor()
    new_editor = _NewEditor()
    ui.set_input_text("safe built-in draft")
    driver.set_editor_component(lambda *_args: old_editor)
    driver.set_title("old")
    candidate = driver.new_candidate_sink()
    candidate.set_editor_component(lambda *_args: new_editor)
    candidate.set_title("new")

    result = driver.accept_candidate(candidate)

    assert result.accepted
    assert result.diagnostic is None
    assert driver.owns_sink(candidate)
    assert ui.get_editor_component() is candidate.snapshot().editor_component
    assert new_editor.text == "safe built-in draft"
    assert ui.get_input_text() == "safe built-in draft"
    assert ui.extension_title == "new"
    assert disposed == ["old"]
    assert "must stay bounded" not in terminal.getvalue()
    assert result.retired_sink is not None
    assert driver.dispose_retired_sink(result.retired_sink) is None


def test_accept_failure_restores_previous_owner_and_closes_candidate_once() -> None:
    ui = _FakeTerminalUi()
    driver = _LiveExtensionUiDriver(cast(Any, ui), Path("."))
    driver.set_title("old")
    candidate = driver.new_candidate_sink()
    candidate.set_title("candidate")
    original_reconcile = ui.reconcile_extension_chrome
    attempts = 0

    def fail_candidate_once(
        snapshot: ExtensionChromeSnapshot,
        *,
        retirement_scope: Callable[[], AbstractContextManager[None]],
    ) -> dict[int, object]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected candidate reconcile failure")
        return original_reconcile(snapshot, retirement_scope=retirement_scope)

    ui.reconcile_extension_chrome = fail_candidate_once  # type: ignore[method-assign]
    ui.semantic_committed = True
    result = driver.accept_candidate(candidate)

    assert not result.accepted
    assert result.diagnostic is not None
    assert [snapshot.title for snapshot in ui.reconciles] == ["old"]
    before = len(ui.calls)
    driver.set_title("old-still-live")
    assert ui.calls[before:] == [("title", "old-still-live")]
    candidate.close()
    candidate.close()
    assert candidate.snapshot() == ExtensionChromeSnapshot()


def test_factory_failure_never_publishes_candidate_chrome() -> None:
    ui = _FakeTerminalUi()
    driver = _LiveExtensionUiDriver(cast(Any, ui), Path("."))
    driver.set_title("old")
    candidate = driver.new_candidate_sink()

    def bad_factory(_tui: object, _theme: object, _keybindings: object) -> object:
        raise RuntimeError("injected editor factory failure")

    candidate.set_editor_component(bad_factory)
    original_reconcile = ui.reconcile_extension_chrome

    def materialize_before_reconcile(
        snapshot: ExtensionChromeSnapshot,
        *,
        retirement_scope: Callable[[], AbstractContextManager[None]],
    ) -> dict[int, object]:
        if snapshot.editor_component is not None:
            cast(Callable[[object, object, object], object], snapshot.editor_component)(
                object(), object(), object()
            )
        return original_reconcile(snapshot, retirement_scope=retirement_scope)

    ui.reconcile_extension_chrome = materialize_before_reconcile  # type: ignore[method-assign]
    ui.semantic_committed = True
    result = driver.accept_candidate(candidate)

    assert not result.accepted
    assert [snapshot.title for snapshot in ui.reconciles] == ["old"]
    assert not any(kind == "editor" for kind, _value in ui.calls)
    candidate.close()


@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
def test_retired_cleanup_interrupt_propagates_after_live_ownership_transfer(
    interrupt_type: type[BaseException],
) -> None:
    ui = _FakeTerminalUi()
    ui.semantic_committed = True
    driver = _LiveExtensionUiDriver(cast(Any, ui), Path("."))
    driver.add_terminal_input_listener(lambda key: key)
    active = driver._active_sink  # noqa: SLF001 - injected cleanup boundary

    def interrupt_cleanup() -> None:
        raise interrupt_type()

    active._terminal_input_disposers[0] = interrupt_cleanup  # noqa: SLF001
    candidate = _CountingCloseSink()
    candidate.set_title("candidate")
    effects, _ctl = _effects(ui=ui, driver=driver)
    object.__setattr__(
        effects,
        "emitter",
        SimpleNamespace(fire_lifecycle=lambda *_args, **_kwargs: None),
    )

    with pytest.raises(interrupt_type):
        effects._finish_candidate_chrome(candidate, replacement_accepted=True)

    assert driver.owns_sink(candidate)
    assert candidate.close_calls == 0
    before = len(ui.calls)
    driver.set_title("candidate-still-live")
    assert ui.calls[before:] == [("title", "candidate-still-live")]
    assert candidate.snapshot().title == "candidate-still-live"


def test_failed_old_restore_retries_candidate_and_transfers_live_ownership() -> None:
    ui = _FakeTerminalUi()
    driver = _LiveExtensionUiDriver(cast(Any, ui), Path("."))
    driver.set_title("old")
    old_disposals: list[str] = []

    def old_listener(_key: str) -> None:
        return None

    driver.add_terminal_input_listener(old_listener)
    # Replace the fake's disposer with one whose exact call count is observable.
    active = driver._active_sink  # noqa: SLF001 - ownership assertion
    active._terminal_input_disposers[0] = lambda: old_disposals.append("old")  # noqa: SLF001
    candidate = driver.new_candidate_sink()
    candidate.set_title("candidate")
    original_reconcile = ui.reconcile_extension_chrome
    attempts = 0

    def fail_then_recover(
        snapshot: ExtensionChromeSnapshot,
        *,
        retirement_scope: Callable[[], AbstractContextManager[None]],
    ) -> dict[int, object]:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise RuntimeError(f"injected reconcile failure {attempts}")
        return original_reconcile(snapshot, retirement_scope=retirement_scope)

    ui.reconcile_extension_chrome = fail_then_recover  # type: ignore[method-assign]
    ui.semantic_committed = True
    result = driver.accept_candidate(candidate)

    assert result.accepted
    assert result.diagnostic is not None
    assert attempts == 3
    assert [snapshot.title for snapshot in ui.reconciles] == ["candidate"]
    assert result.retired_sink is active
    assert driver.dispose_retired_sink(result.retired_sink) is None
    assert old_disposals == ["old"]
    before = len(ui.calls)
    driver.set_title("candidate-live")
    assert ui.calls[before:] == [("title", "candidate-live")]
    assert old_disposals == ["old"]


class _Ctl:
    def __init__(self, generation: SessionExtensionGeneration, ui: _FakeTerminalUi):
        self.generation_ref = SessionGenerationRef(generation)
        self.workspace_resources = object()
        self.package_roots = SimpleNamespace(extensions=())
        self._ui = ui
        self.before_commit: Callable[[], None] | None = None

    @property
    def extension_generation(self) -> SessionExtensionGeneration:
        return self.generation_ref.current

    @extension_generation.setter
    def extension_generation(self, generation: SessionExtensionGeneration) -> None:
        if self.before_commit is not None:
            self.before_commit()
        self.generation_ref.publish(generation)
        self._ui.semantic_committed = True


def _runtime() -> Any:
    return SimpleNamespace(
        flags=(),
        custom_messages=(),
        activation_hosts=(),
    )


def _effects(
    *,
    ui: _FakeTerminalUi,
    driver: _LiveExtensionUiDriver,
    tokens: tuple[str, ...] = (),
) -> tuple[_ReloadCommandEffects, _Ctl]:
    live = SessionExtensionGeneration(_runtime(), {})
    ctl = _Ctl(live, ui)
    effects = _ReloadCommandEffects(
        session=cast(Any, SimpleNamespace(tool_registry={})),
        ctl=cast(Any, ctl),
        settings=cast(
            Any,
            SimpleNamespace(
                project_trusted=True,
                get_extensions_patterns=lambda: (),
            ),
        ),
        keybindings=cast(Any, None),
        terminal_ui=cast(Any, ui),
        renderer=cast(Any, None),
        error_stream=cast(Any, None),
        emitter=cast(Any, SimpleNamespace(set_flags=lambda _flags: None)),
        provider_mutation=cast(Any, None),
        cwd=Path("."),
        resource_options=RuntimeResourceOptions(
            no_extensions=True,
            extension_flag_tokens=tokens,
        ),
        tool_capabilities=cast(Any, None),
        diag=lambda _message: None,
        redraw_custom_entries_for_active_branch=lambda: None,
        extension_send_message=lambda *_args: None,
        extension_render_details=cast(Any, lambda *_args: None),
        extension_ui_driver=driver,
    )
    return effects, ctl


def _preloaded_candidate() -> ExtensionChromeSink:
    candidate = ExtensionChromeSink()
    candidate.set_title("REJECTED_TITLE")
    candidate.set_widget("rejected", ["REJECTED_WIDGET"], "above_editor")
    candidate.add_terminal_input_listener(lambda key: f"rejected:{key}")
    return candidate


def test_invalid_flags_keep_live_title_widgets_and_listeners_without_candidate_paint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ui = _FakeTerminalUi()
    driver = _LiveExtensionUiDriver(cast(Any, ui), Path("."))

    def old_handler(key: str) -> str:
        return key

    driver.set_title("OLD_TITLE")
    driver.set_widget("old", ["OLD_WIDGET"], "above_editor")
    driver.add_terminal_input_listener(old_handler)
    before = list(ui.calls)
    candidate_sink = _preloaded_candidate()
    monkeypatch.setattr(driver, "new_candidate_sink", lambda: candidate_sink)
    monkeypatch.setattr(
        tool_loop_session,
        "_activate_workspace_extensions",
        lambda *_a, **_k: _runtime(),
    )
    effects, ctl = _effects(ui=ui, driver=driver, tokens=("--missing",))
    live = ctl.extension_generation

    candidate = _ExtensionCandidate()
    effects._reload_extension_generation(candidate)

    assert ctl.extension_generation is live
    assert ui.calls == before
    assert candidate_sink.snapshot() == ExtensionChromeSnapshot()


def test_injected_activation_failure_keeps_live_chrome_and_disposes_candidate_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ui = _FakeTerminalUi()
    driver = _LiveExtensionUiDriver(cast(Any, ui), Path("."))
    driver.set_title("OLD_TITLE")
    before = list(ui.calls)
    candidate_sink = _preloaded_candidate()
    monkeypatch.setattr(driver, "new_candidate_sink", lambda: candidate_sink)

    def fail_activation(*_args, **_kwargs):
        raise RuntimeError("injected activation failure")

    monkeypatch.setattr(
        tool_loop_session, "_activate_workspace_extensions", fail_activation
    )
    effects, ctl = _effects(ui=ui, driver=driver)
    live = ctl.extension_generation

    with pytest.raises(RuntimeError, match="injected activation failure"):
        effects._reload_extension_generation(_ExtensionCandidate())

    assert ctl.extension_generation is live
    assert ui.calls == before
    assert candidate_sink.snapshot() == ExtensionChromeSnapshot()


def test_post_commit_projection_failure_closes_only_detached_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ui = _FakeTerminalUi()
    driver = _LiveExtensionUiDriver(cast(Any, ui), Path("."))
    driver.set_title("old-live")
    sink = driver.new_candidate_sink()
    monkeypatch.setattr(driver, "new_candidate_sink", lambda: sink)
    monkeypatch.setattr(
        tool_loop_session,
        "_activate_workspace_extensions",
        lambda *_args, **_kwargs: _runtime(),
    )
    effects, _ctl = _effects(ui=ui, driver=driver)
    object.__setattr__(
        effects,
        "provider_mutation",
        SimpleNamespace(refresh_provider_after_reload=lambda: None),
    )
    monkeypatch.setattr(
        _ReloadCommandEffects,
        "_reload_configuration_and_resources",
        lambda _self: None,
    )

    def fail_projection(_self: _ReloadCommandEffects) -> None:
        raise RuntimeError("injected post-commit projection failure")

    monkeypatch.setattr(
        _ReloadCommandEffects,
        "_publish_tool_and_lifecycle_projections",
        fail_projection,
    )
    outcome = CodingCommandOutcome(
        kind=CodingCommandOutcomeKind.CONTINUE,
        action=CodingCommandAction.RELOAD,
        footer_policy=CodingCommandFooterPolicy.STANDARD,
    )

    with pytest.raises(RuntimeError, match="post-commit projection failure"):
        effects.execute(outcome)

    assert sink.snapshot() == ExtensionChromeSnapshot()
    before = len(ui.calls)
    driver.set_title("old-still-live")
    assert ui.calls[before:] == [("title", "old-still-live")]


def test_production_reload_stages_real_session_start_chrome_until_acceptance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ext_dir = tmp_path / ".pipy" / "extensions"
    ext_dir.mkdir(parents=True)
    (ext_dir / "candidate-chrome.py").write_text(
        "def activate(api):\n"
        "    @api.on('session_start')\n"
        "    def _start(event, ctx):\n"
        "        ctx.ui.set_title('CANDIDATE_TITLE')\n"
        "        ctx.ui.set_widget('candidate', ['CANDIDATE_WIDGET'])\n"
        "        ctx.ui.on_terminal_input(lambda key: key)\n",
        encoding="utf-8",
    )
    ui = _FakeTerminalUi()
    driver = _LiveExtensionUiDriver(cast(Any, ui), tmp_path)
    driver.set_title("OLD_TITLE")
    live = SessionExtensionGeneration(_runtime(), {})
    ctl = _Ctl(live, ui)
    ctl.workspace_resources = SimpleNamespace(custom_command_slash_names=lambda: ())
    commits = 0

    def old_writer_at_commit() -> None:
        nonlocal commits
        commits += 1
        driver.set_title(f"OLD_WRITER_{commits}")

    ctl.before_commit = old_writer_at_commit
    emitter = _ExtensionLifecycleAgentEventAdapter(
        cast(Any, SimpleNamespace(emit=lambda _event: None)),
        lifecycle_hooks={},
        cwd=str(tmp_path),
        has_ui=True,
        ui_driver=driver,
    )
    diagnostics: list[str] = []
    effects = _ReloadCommandEffects(
        session=cast(Any, SimpleNamespace(tool_registry={})),
        ctl=cast(Any, ctl),
        settings=cast(
            Any,
            SimpleNamespace(
                project_trusted=True,
                get_extensions_patterns=lambda: (),
            ),
        ),
        keybindings=cast(Any, None),
        terminal_ui=cast(Any, ui),
        renderer=cast(Any, None),
        error_stream=cast(Any, None),
        emitter=emitter,
        provider_mutation=cast(
            Any, SimpleNamespace(refresh_provider_after_reload=lambda: None)
        ),
        cwd=tmp_path,
        resource_options=RuntimeResourceOptions(),
        tool_capabilities=cast(Any, None),
        diag=diagnostics.append,
        redraw_custom_entries_for_active_branch=lambda: None,
        extension_send_message=lambda *_args: None,
        extension_render_details=cast(Any, lambda *_args: None),
        extension_ui_driver=driver,
    )

    monkeypatch.setattr(
        _ReloadCommandEffects,
        "_reload_configuration_and_resources",
        lambda _self: None,
    )

    def publish_candidate(self: _ReloadCommandEffects) -> None:
        generation = self.ctl.extension_generation
        self.emitter.set_lifecycle_hooks(generation.runtime.lifecycle_hooks)
        self.emitter.set_flags(generation.flag_values)

    monkeypatch.setattr(
        _ReloadCommandEffects,
        "_publish_tool_and_lifecycle_projections",
        publish_candidate,
    )
    monkeypatch.setattr(
        _ReloadCommandEffects,
        "_refresh_presentation_and_persistence",
        lambda _self: False,
    )
    original_reconcile = ui.reconcile_extension_chrome
    reject_first_candidate = True

    def reject_once(
        snapshot: ExtensionChromeSnapshot,
        *,
        retirement_scope: Callable[[], AbstractContextManager[None]],
    ) -> dict[int, object]:
        nonlocal reject_first_candidate
        if reject_first_candidate and snapshot.title == "CANDIDATE_TITLE":
            reject_first_candidate = False
            raise RuntimeError("injected acceptance rejection")
        return original_reconcile(snapshot, retirement_scope=retirement_scope)

    ui.reconcile_extension_chrome = reject_once  # type: ignore[method-assign]
    outcome = CodingCommandOutcome(
        kind=CodingCommandOutcomeKind.CONTINUE,
        action=CodingCommandAction.RELOAD,
        footer_policy=CodingCommandFooterPolicy.STANDARD,
    )

    effects.execute(outcome)

    assert [snapshot.title for snapshot in ui.reconciles] == ["OLD_WRITER_1"]
    assert not any(call == ("title", "CANDIDATE_TITLE") for call in ui.calls)
    assert any("kept the previous chrome" in message for message in diagnostics)
    first_generation = ctl.extension_generation

    effects.execute(outcome)

    assert ctl.extension_generation is not first_generation
    assert [snapshot.title for snapshot in ui.reconciles] == [
        "OLD_WRITER_1",
        "CANDIDATE_TITLE",
    ]
    assert ui.reconciles[-1].widgets == (
        ("candidate", ["CANDIDATE_WIDGET"], "above_editor"),
    )
    assert len(ui.reconciles[-1].terminal_input_listeners) == 1
    assert ("title", "OLD_WRITER_1") in ui.calls
    assert ("title", "OLD_WRITER_2") in ui.calls
    assert not any(call == ("title", "CANDIDATE_TITLE") for call in ui.calls)


def test_successful_removal_reconciles_empty_chrome_once_after_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ui = _FakeTerminalUi()
    driver = _LiveExtensionUiDriver(cast(Any, ui), Path("."))
    driver.set_title("OLD_TITLE")
    driver.set_widget("old", ["OLD_WIDGET"], "above_editor")
    driver.add_terminal_input_listener(lambda key: key)
    monkeypatch.setattr(
        tool_loop_session,
        "_activate_workspace_extensions",
        lambda *_a, **_k: _runtime(),
    )
    effects, ctl = _effects(ui=ui, driver=driver)
    live = ctl.extension_generation

    replacement_accepted, candidate_sink = effects._reload_extension_generation(
        _ExtensionCandidate()
    )

    assert replacement_accepted
    assert candidate_sink is not None
    assert ctl.extension_generation is not live
    assert ui.reconciles == []
    accepted = driver.accept_candidate(candidate_sink)
    assert accepted.accepted
    assert ui.reconciles == [ExtensionChromeSnapshot()]
    assert [kind for kind, _value in ui.calls].count("reconcile") == 1
