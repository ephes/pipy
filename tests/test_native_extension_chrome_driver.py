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

    def set_extension_footer(self, factory, footer_data=None):
        self.calls.append(("footer", factory, footer_data))

    def set_extension_title(self, title):
        self.calls.append(("title", title))

    def set_extension_working_indicator(self, frames, interval_ms):
        self.calls.append(("indicator", frames, interval_ms))

    def set_extension_hidden_thinking_label(self, label=None):
        self.calls.append(("hidden-thinking-label", label))

    def get_input_text(self):
        return self.input_text

    def set_input_text(self, text):
        self.calls.append(("set-input", text))
        self.input_text = text

    def paste_input_text(self, text):
        self.calls.append(("paste-input", text))
        self.pasted.append(text)
        self.input_text = text

    def set_editor_component(self, factory):
        self.calls.append(("set-editor-component", factory))
        self.editor_component = factory

    def get_editor_component(self):
        return getattr(self, "editor_component", None)


def test_driver_delegates_all_five(tmp_path):
    ui = _FakeUi()
    driver = _LiveExtensionUiDriver(ui, tmp_path)
    factory = lambda theme: None  # noqa: E731
    driver.set_widget("k", ["a"], "below_editor")
    driver.set_header(factory)
    driver.set_title("t")
    driver.set_working_indicator(["x"], 120)
    driver.set_hidden_thinking_label("Thinking hard")
    kinds = [c[0] for c in ui.calls]
    assert kinds == ["widget", "header", "title", "indicator", "hidden-thinking-label"]
    assert ui.calls[0] == ("widget", "k", ["a"], "below_editor")


def test_driver_footer_delegates_snapshot_to_terminal_ui(tmp_path):
    ui = _FakeUi()
    driver = _LiveExtensionUiDriver(ui, tmp_path)
    factory = lambda theme, fd: None  # noqa: E731
    driver.set_footer(factory)
    _kind, passed_factory, footer_data = ui.calls[-1]
    assert passed_factory is factory
    assert footer_data is None


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


def test_live_driver_delegates_editor_component_to_terminal_ui(tmp_path):
    ui = _FakeUi()
    driver = _LiveExtensionUiDriver(ui, tmp_path)
    factory = object()

    assert driver.get_editor_component() is None
    driver.set_editor_component(factory)
    assert driver.get_editor_component() is factory
    driver.set_editor_component(None)
    assert driver.get_editor_component() is None
    assert ui.calls == [
        ("set-editor-component", factory),
        ("set-editor-component", None),
    ]


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


def test_footerdata_branch_change_registrar_invokes_disposer():
    callbacks = []

    def registrar(callback):
        callbacks.append(callback)
        return lambda: callbacks.remove(callback)

    footer_data = FooterData(
        git_branch="main",
        extension_statuses={},
        branch_change_registrar=registrar,
    )
    seen = []
    dispose = footer_data.onBranchChange(lambda: seen.append("called"))
    callbacks[0]()
    dispose()
    assert seen == ["called"]
    assert callbacks == []


def test_footerdata_branch_change_noop_disposer_is_idempotent():
    footer_data = FooterData(git_branch=None, extension_statuses={})
    dispose = footer_data.onBranchChange(lambda: None)
    dispose()
    dispose()
