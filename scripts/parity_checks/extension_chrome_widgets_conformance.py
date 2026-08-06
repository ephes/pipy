"""Chrome-widget conformance gate (slice B).

Covers the chrome UNITS in isolation: render helper coercion/bounds/fail-soft,
the TUI setters (set/replace/clear, keyed insertion order, both placements,
exclusive header/footer replace+restore, title OSC, indicator override/hide/
restore), resize re-render, dispose-on-replace/clear, and the OSC title bytes.
The end-to-end session dispatch + no-leak guarantee is proven by the golden
gate extension_conformance_gate.py.

Run: uv run python scripts/parity_checks/extension_chrome_widgets_conformance.py --json
"""

from __future__ import annotations

import argparse
import io
import json
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native.tool_renderers import render_chrome_component
from pipy_harness.native.tui import TerminalUi


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _ui(tty: bool = False):
    return TerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=_Tty() if tty else io.StringIO(),
        cwd=Path("."),
    )


class _LC:
    def __init__(self, lines):
        self._lines = lines

    def render(self, width):
        return self._lines


class _WComp:
    def render(self, width):
        return [f"w{width}"]


class _DComp:
    def __init__(self, sink):
        self._sink = sink

    def render(self, width):
        return ["x"]

    def dispose(self):
        self._sink.append(True)


def run_checks() -> list[Check]:
    checks: list[Check] = []

    # 1. render helper coercion + bounds + fail-soft.
    checks.append(
        Check(
            "render_helper",
            render_chrome_component("a\nb", width=20, max_lines=8) == ["a", "b"]
            and render_chrome_component(
                lambda: (_ for _ in ()).throw(RuntimeError()), width=20, max_lines=8
            )
            is None
            and len(
                render_chrome_component(
                    [f"l{i}" for i in range(20)], width=20, max_lines=3
                )
            )
            == 4,
            "coercion/bounds/fail-soft",
        )
    )

    # 2. widget set/replace/clear + insertion order + placement.
    ui = _ui()
    ui._chrome.component.set_widget("z", ["z"])  # noqa: SLF001
    ui._chrome.component.set_widget("a", ["a"])  # noqa: SLF001
    ui._chrome.component.set_widget(  # noqa: SLF001
        "b", ["b"], placement="below_editor"
    )
    order_ok = list(ui._chrome.record.widgets_above) == ["z", "a"]  # noqa: SLF001
    place_ok = "b" in ui._chrome.record.widgets_below  # noqa: SLF001
    ui._chrome.component.set_widget("z", None)  # noqa: SLF001
    cleared = "z" not in ui._chrome.record.widgets_above  # noqa: SLF001
    checks.append(
        Check(
            "widget_lifecycle",
            order_ok and place_ok and cleared,
            "insertion order + placement + clear",
        )
    )

    # 3. header/footer exclusive replace + restore.
    ui = _ui()
    ui._chrome.component.set_header(lambda theme: _LC(["h"]))  # noqa: SLF001
    ui._chrome.footer.set_footer(lambda theme, fd: _LC(["f"]))  # noqa: SLF001
    set_ok = (  # noqa: SLF001
        ui._chrome.record.header is not None and ui._chrome.record.footer is not None
    )
    ui._chrome.component.set_header(None)  # noqa: SLF001
    ui._chrome.footer.set_footer(None)  # noqa: SLF001
    restore_ok = (  # noqa: SLF001
        ui._chrome.record.header is None and ui._chrome.record.footer is None
    )
    checks.append(
        Check("header_footer_exclusive", set_ok and restore_ok, "replace+restore")
    )

    # 4. title OSC on TTY, no-op off.
    ui_tty = _ui(tty=True)
    ui_tty._chrome.component.set_title("hello")  # noqa: SLF001
    osc_ok = "\x1b]0;hello\x07" in ui_tty.terminal_stream.getvalue()
    ui_off = _ui(tty=False)
    ui_off._chrome.component.set_title("hello")  # noqa: SLF001
    noop_ok = ui_off.terminal_stream.getvalue() == ""
    checks.append(Check("title_osc", osc_ok and noop_ok, "OSC on TTY / no-op off"))

    # 5. indicator override / default-frames-custom-interval / hide / restore.
    ui = _ui()
    ui._chrome.component.set_working_indicator(["x"], 120)  # noqa: SLF001
    a = (  # noqa: SLF001
        ui._chrome.record.indicator_frames == ("x",)
        and ui._chrome.record.indicator_interval_ms == 120.0
    )
    # frames=None restores defaults while preserving the explicit interval.
    ui._chrome.component.set_working_indicator(None, 120)  # noqa: SLF001
    b = (  # noqa: SLF001
        ui._chrome.record.indicator_frames is None
        and ui._chrome.record.indicator_interval_ms == 120.0
    )
    ui._chrome.component.set_working_indicator([], None)  # noqa: SLF001
    c = ui._chrome.record.indicator_frames == ()  # noqa: SLF001
    checks.append(
        Check("indicator_semantics", a and b and c, "override / reset / hide")
    )

    # 6. resize re-render of a factory widget.
    ui = _ui()
    ui._chrome.component.set_widget("k", lambda tui, theme: _WComp())  # noqa: SLF001
    l40 = ui._chrome.component.widget_lines("above_editor", 40)  # noqa: SLF001
    l70 = ui._chrome.component.widget_lines("above_editor", 70)  # noqa: SLF001
    checks.append(
        Check(
            "resize_rerender",
            any("40" in fl.text for fl in l40) and any("70" in fl.text for fl in l70),
            "factory widget reflows on width change",
        )
    )

    # 7. same-width live re-render + Pi-shaped requestRender handle.
    ui = _ui()
    state = {"value": "a"}
    handles = []

    class _LiveComp:
        def render(self, width):
            return [state["value"]]

    ui._chrome.component.set_widget(  # noqa: SLF001
        "k", lambda tui, theme: handles.append(tui) or _LiveComp()
    )
    first = ui._chrome.component.widget_lines("above_editor", 70)  # noqa: SLF001
    state["value"] = "b"
    second = ui._chrome.component.widget_lines("above_editor", 70)  # noqa: SLF001
    request_ok = bool(handles) and hasattr(handles[0], "requestRender")
    before = ui.terminal_stream.getvalue()
    handles[0].requestRender()
    after = ui.terminal_stream.getvalue()
    checks.append(
        Check(
            "live_rerender_request_render",
            any("a" in fl.text for fl in first)
            and any("b" in fl.text for fl in second)
            and request_ok
            and after != before,
            "same-width render refresh + requestRender repaint",
        )
    )

    # 8. dispose called on replace + clear.
    ui = _ui()
    disposed = []
    ui._chrome.component.set_widget(  # noqa: SLF001
        "k", lambda theme: _DComp(disposed)
    )
    ui._chrome.component.set_widget("k", ["plain"])  # noqa: SLF001
    ui.clear_extension_chrome()
    checks.append(Check("dispose", disposed == [True], "dispose on replace/clear"))

    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    checks = run_checks()
    passed = all(c.passed for c in checks)
    if args.json:
        print(
            json.dumps(
                {
                    "passed": passed,
                    "checks": [
                        {"name": c.name, "passed": c.passed, "detail": c.detail}
                        for c in checks
                    ],
                },
                indent=2,
            )
        )
    else:
        for c in checks:
            print(f"[{'PASS' if c.passed else 'FAIL'}] {c.name}: {c.detail}")
        print("ALL PASS" if passed else "FAILURES PRESENT")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
