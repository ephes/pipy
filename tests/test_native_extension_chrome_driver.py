import subprocess

from pipy_harness.native.extension_runtime import (
    FooterData,
    LifecycleEvent,
    dispatch_lifecycle_hooks,
)
from pipy_harness.native.tool_loop_session import _LiveExtensionUiDriver


class _FakeDriver:
    """Records the ExtensionUiDriver calls _CollectingUi delegates."""

    def __init__(self):
        self.calls = []

    def set_widget(self, key, content, placement):
        self.calls.append(("widget", key, content, placement))


class _FakeUi:
    """Records the set_extension_* calls the driver delegates."""

    def __init__(self):
        self.extension_status = {"s": "v"}
        self.available_provider_count = 2
        self.calls = []
        self.input_text = "draft"
        self.pasted = []

    def set_extension_widget(self, key, content, *, placement):
        self.calls.append(("widget", key, content, placement))

    def set_extension_header(self, factory):
        self.calls.append(("header", factory))

    def set_extension_footer(self, factory, footer_data):
        self.calls.append(("footer", factory, footer_data))

    def set_extension_title(self, title):
        self.calls.append(("title", title))

    def set_extension_working_indicator(self, frames, interval_ms):
        self.calls.append(("indicator", frames, interval_ms))

    def get_input_text(self):
        return self.input_text

    def set_input_text(self, text):
        self.calls.append(("set-input", text))
        self.input_text = text

    def paste_input_text(self, text):
        self.calls.append(("paste-input", text))
        self.pasted.append(text)
        self.input_text = text


def test_driver_delegates_all_five(tmp_path):
    ui = _FakeUi()
    driver = _LiveExtensionUiDriver(ui, tmp_path)
    factory = lambda theme: None  # noqa: E731
    driver.set_widget("k", ["a"], "below_editor")
    driver.set_header(factory)
    driver.set_title("t")
    driver.set_working_indicator(["x"], 120)
    kinds = [c[0] for c in ui.calls]
    assert kinds == ["widget", "header", "title", "indicator"]
    assert ui.calls[0] == ("widget", "k", ["a"], "below_editor")


def test_driver_footer_builds_footerdata_with_branch_and_statuses(tmp_path):
    subprocess.run(["git", "init", "-b", "feature-x"], cwd=tmp_path, check=True,
                   capture_output=True)
    ui = _FakeUi()
    driver = _LiveExtensionUiDriver(ui, tmp_path)
    factory = lambda theme, fd: None  # noqa: E731
    driver.set_footer(factory)
    _kind, passed_factory, footer_data = ui.calls[-1]
    assert passed_factory is factory
    assert isinstance(footer_data, FooterData)
    assert footer_data.git_branch == "feature-x"
    assert footer_data.extension_statuses == {"s": "v"}
    assert footer_data.getAvailableProviderCount() == 2


def test_driver_footer_none_passes_none(tmp_path):
    ui = _FakeUi()
    driver = _LiveExtensionUiDriver(ui, tmp_path)
    driver.set_footer(None)
    assert ui.calls[-1] == ("footer", None, None)


def test_driver_delegates_editor_text_helpers(tmp_path):
    ui = _FakeUi()
    driver = _LiveExtensionUiDriver(ui, tmp_path)

    assert driver.get_editor_text() == "draft"

    driver.set_editor_text("set")
    assert ui.input_text == "set"
    assert ui.calls[-1] == ("set-input", "set")

    ui.input_text = "draft text"
    driver.paste_to_editor("paste")
    assert ui.input_text == "paste"
    assert ui.pasted == ["paste"]
    assert ui.calls[-1] == ("paste-input", "paste")


def test_live_driver_stores_editor_component_in_memory(tmp_path):
    ui = _FakeUi()
    driver = _LiveExtensionUiDriver(ui, tmp_path)
    factory = object()

    assert driver.get_editor_component() is None
    driver.set_editor_component(factory)
    assert driver.get_editor_component() is factory
    driver.set_editor_component(None)
    assert driver.get_editor_component() is None
    assert ui.calls == []


def test_lifecycle_hook_reaches_live_ui_driver(tmp_path):
    driver = _FakeDriver()
    captured = {}

    def hook(event, ctx):
        captured["name"] = event.name
        ctx.ui.set_widget("hdr", ["LIVE"], placement="above_editor")

    dispatch_lifecycle_hooks(
        [hook],
        LifecycleEvent(name="session_start", reason="startup"),
        cwd=str(tmp_path),
        has_ui=True,
        notify_sink=None,
        ui_driver=driver,
    )
    assert captured["name"] == "session_start"
    assert driver.calls == [("widget", "hdr", ["LIVE"], "above_editor")]


def test_lifecycle_hook_no_driver_records_but_does_not_raise(tmp_path):
    # Without a ui_driver the hook still runs (records into _CollectingUi),
    # no error.
    def hook(event, ctx):
        ctx.ui.set_widget("hdr", ["LIVE"], placement="above_editor")

    dispatch_lifecycle_hooks(
        [hook],
        LifecycleEvent(name="session_start", reason="startup"),
        cwd=str(tmp_path),
        has_ui=False,
    )  # must not raise
