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
from pipy_harness.native.tui import ToolLoopTerminalUi


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _ui(tty: bool = False):
    return ToolLoopTerminalUi(
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
    checks.append(Check(
        "render_helper",
        render_chrome_component("a\nb", width=20, max_lines=8) == ["a", "b"]
        and render_chrome_component(lambda: (_ for _ in ()).throw(RuntimeError()), width=20, max_lines=8) is None
        and len(render_chrome_component([f"l{i}" for i in range(20)], width=20, max_lines=3)) == 4,
        "coercion/bounds/fail-soft",
    ))

    # 2. widget set/replace/clear + insertion order + placement.
    ui = _ui()
    ui.set_extension_widget("z", ["z"])
    ui.set_extension_widget("a", ["a"])
    ui.set_extension_widget("b", ["b"], placement="below_editor")
    order_ok = list(ui.extension_widgets_above.keys()) == ["z", "a"]
    place_ok = "b" in ui.extension_widgets_below
    ui.set_extension_widget("z", None)
    cleared = "z" not in ui.extension_widgets_above
    checks.append(Check("widget_lifecycle", order_ok and place_ok and cleared,
                        "insertion order + placement + clear"))

    # 3. header/footer exclusive replace + restore.
    ui = _ui()
    ui.set_extension_header(lambda theme: _LC(["h"]))
    ui.set_extension_footer(lambda theme, fd: _LC(["f"]))
    set_ok = ui.extension_header is not None and ui.extension_footer is not None
    ui.set_extension_header(None)
    ui.set_extension_footer(None)
    restore_ok = ui.extension_header is None and ui.extension_footer is None
    checks.append(Check("header_footer_exclusive", set_ok and restore_ok, "replace+restore"))

    # 4. title OSC on TTY, no-op off.
    ui_tty = _ui(tty=True)
    ui_tty.set_extension_title("hello")
    osc_ok = "\x1b]0;hello\x07" in ui_tty.terminal_stream.getvalue()
    ui_off = _ui(tty=False)
    ui_off.set_extension_title("hello")
    noop_ok = ui_off.terminal_stream.getvalue() == ""
    checks.append(Check("title_osc", osc_ok and noop_ok, "OSC on TTY / no-op off"))

    # 5. indicator override / default-frames-custom-interval / hide / restore.
    ui = _ui()
    ui.set_extension_working_indicator(["x"], 120)
    a = ui.extension_indicator_frames == ("x",) and ui.extension_indicator_interval_ms == 120.0
    ui.set_extension_working_indicator(None, 120)   # frames=None -> default frames, interval still applies
    b = ui.extension_indicator_frames is None and ui.extension_indicator_interval_ms == 120.0
    ui.set_extension_working_indicator([], None)    # hide
    c = ui.extension_indicator_frames == ()
    checks.append(Check("indicator_semantics", a and b and c,
                        "override / reset / hide"))

    # 6. resize re-render of a factory widget.
    ui = _ui()
    ui.set_extension_widget("k", lambda theme: _WComp())
    l40 = ui._extension_widgets_lines("above_editor", 40)
    l70 = ui._extension_widgets_lines("above_editor", 70)
    checks.append(Check("resize_rerender",
                        any("40" in fl.text for fl in l40) and any("70" in fl.text for fl in l70),
                        "factory widget reflows on width change"))

    # 7. dispose called on replace + clear.
    ui = _ui()
    disposed = []
    ui.set_extension_widget("k", lambda theme: _DComp(disposed))
    ui.set_extension_widget("k", ["plain"])
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
        print(json.dumps({"passed": passed, "checks": [
            {"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks
        ]}, indent=2))
    else:
        for c in checks:
            print(f"[{'PASS' if c.passed else 'FAIL'}] {c.name}: {c.detail}")
        print("ALL PASS" if passed else "FAILURES PRESENT")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
