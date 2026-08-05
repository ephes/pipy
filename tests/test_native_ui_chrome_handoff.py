import pytest

from pipy_harness.native.ui.chrome_handoff import _ExtensionChromeTuiHandle


def test_extension_chrome_tui_handle_routes_both_spellings_without_force() -> None:
    calls: list[str] = []
    handle = _ExtensionChromeTuiHandle(lambda: calls.append("repaint"))

    handle.requestRender()
    handle.requestRender(True)
    handle.request_render()
    handle.request_render(True)

    assert calls == ["repaint"] * 4


@pytest.mark.parametrize("method_name", ["requestRender", "request_render"])
def test_extension_chrome_tui_handle_repaint_failure_is_fail_soft(
    method_name: str,
) -> None:
    def fail_repaint() -> None:
        raise RuntimeError("paint failed")

    handle = _ExtensionChromeTuiHandle(fail_repaint)

    getattr(handle, method_name)(True)


@pytest.mark.parametrize("method_name", ["requestRender", "request_render"])
@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
def test_extension_chrome_tui_handle_propagates_interrupts(
    method_name: str, interrupt_type: type[BaseException]
) -> None:
    interrupt = interrupt_type()

    def interrupt_repaint() -> None:
        raise interrupt

    handle = _ExtensionChromeTuiHandle(interrupt_repaint)

    with pytest.raises(interrupt_type) as raised:
        getattr(handle, method_name)(True)

    assert raised.value is interrupt
