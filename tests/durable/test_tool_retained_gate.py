"""Actual wrapper capture starts inside retained callback ownership."""

import threading

import pytest

from pipy_harness.native.agent.tools import _InvocationOutputGate
from pipy_harness.native.extension_types import ToolResult
from pipy_harness.native.extensions.tool_port import _partial_update


def test_extension_conversion_is_retained_before_bridge_and_seal():
    entered, release, drained = threading.Event(), threading.Event(), threading.Event()
    emitted = []

    class HeldText:
        def __str__(self):
            entered.set()
            assert release.wait(5)
            return "captured"

    gate = _InvocationOutputGate(emitted.append, retain=True)
    update = _partial_update(gate.sink)
    worker = threading.Thread(target=lambda: update(ToolResult(content=HeldText())))
    closer = threading.Thread(target=lambda: (gate.deactivate(), drained.set()))
    worker.start()
    try:
        assert entered.wait(5)
        closer.start()
        # Inspect actual guarded state; a timed wait is not a cleanup oracle.
        with gate._condition:
            assert gate._active_callbacks == 1
        assert not drained.is_set()
    finally:
        release.set()
        worker.join(5)
        if closer.ident is not None:
            closer.join(5)
    assert not worker.is_alive() and not closer.is_alive()
    assert drained.is_set() and len(emitted) == 1
    assert emitted[0].content == "captured"
    entered.clear()
    update(ToolResult(content=HeldText()))
    assert not entered.is_set()  # late callback never starts conversion


def test_void_retained_capture_failure_returns_normally_and_releases_permit():
    gate = _InvocationOutputGate(lambda value: None, retain=True)
    _partial_update(gate.sink)(object())
    with gate._condition:
        assert gate._capture_failed and gate._active_callbacks == 0
    gate.deactivate()


def test_unexpected_admission_failure_is_contained(monkeypatch):
    gate = _InvocationOutputGate(lambda value: None, retain=True)
    reached = []

    def broken(capture):
        reached.append(True)
        raise RuntimeError("private injected admission failure")

    monkeypatch.setattr(gate, "_dispatch", broken)
    gate.dispatch(lambda: "never")
    assert reached == [True]
    with gate._condition:
        assert gate._capture_failed and gate._active_callbacks == 0


def test_ordinary_extension_callback_keeps_its_exception_policy():
    with pytest.raises(TypeError, match="on_update expects"):
        _partial_update(lambda value: None)(object())


def test_ordinary_callable_with_unrelated_dispatch_is_not_retained():
    from pipy_harness.native.tools.base import dispatch_tool_output

    class Ordinary:
        def __init__(self):
            self.outputs = []

        def __call__(self, value):
            self.outputs.append(value)

        @property
        def dispatch(self):
            pytest.fail("ordinary callable's unrelated dispatch was inspected")

        @property
        def forward_details(self):
            pytest.fail("ordinary callable's unrelated helper was inspected")

        def __getattribute__(self, name):
            if name == "__class__":
                pytest.fail("foreign class attribute was inspected before admission")
            return object.__getattribute__(self, name)

    sink = Ordinary()
    dispatch_tool_output(sink, lambda: "plain")
    _partial_update(sink)(ToolResult(content="partial", details={"tuple": (1,)}))
    assert sink.outputs[0] == "plain"
    assert sink.outputs[1].details == {"tuple": [1]}
