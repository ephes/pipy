from pipy_harness.native.extension_runtime import _CollectingUi


class _RecordingDriver:
    def __init__(self):
        self.calls = []

    def set_widget(self, key, content, placement):
        self.calls.append(("widget", key, content, placement))

    def set_header(self, factory):
        self.calls.append(("header", factory))

    def set_footer(self, factory):
        self.calls.append(("footer", factory))

    def set_title(self, title):
        self.calls.append(("title", title))

    def set_working_indicator(self, frames, interval_ms):
        self.calls.append(("indicator", frames, interval_ms))


def test_collecting_records_widget_and_clears():
    ui = _CollectingUi(has_ui=False)
    ui.set_widget("k", ["a"], placement="below_editor")
    assert ui.widgets["k"] == (["a"], "below_editor")
    ui.set_widget("k", None)
    assert "k" not in ui.widgets


def test_collecting_records_title_and_indicator():
    ui = _CollectingUi(has_ui=False)
    ui.set_title("hello")
    assert ui.title == "hello"
    ui.set_working_indicator(["x"], interval_ms=120)
    assert ui.indicator == (["x"], 120)


def test_collecting_delegates_to_driver_when_has_ui():
    driver = _RecordingDriver()
    ui = _CollectingUi(has_ui=True, ui_driver=driver)
    factory = lambda theme: None  # noqa: E731
    ui.set_header(factory)
    ui.set_footer(factory)
    ui.set_widget("k", ["a"], placement="above_editor")
    ui.set_title("t")
    ui.set_working_indicator(None)
    kinds = [c[0] for c in driver.calls]
    assert kinds == ["header", "footer", "widget", "title", "indicator"]


def test_collecting_failsoft_driver_does_not_raise():
    class _Boom:
        def set_title(self, title):
            raise RuntimeError("x")

    ui = _CollectingUi(has_ui=True, ui_driver=_Boom())
    ui.set_title("t")  # must not raise
    assert ui.title == "t"


def test_collecting_invalid_widget_key_ignored():
    ui = _CollectingUi(has_ui=False)
    ui.set_widget("   ", ["a"])
    assert ui.widgets == {}


def test_collecting_working_indicator_failsoft_on_bad_frames():
    ui = _CollectingUi(has_ui=False)
    ui.set_working_indicator(123)        # non-iterable frames must not raise
    ui.set_working_indicator(["a"], interval_ms=50)
    assert ui.indicator == (["a"], 50)   # a valid call still records
