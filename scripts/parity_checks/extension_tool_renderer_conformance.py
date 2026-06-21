# scripts/parity_checks/extension_tool_renderer_conformance.py
"""Custom-tool-renderer conformance gate.

This gate covers the dispatch/coercion UNITS in isolation (render_tool_phase,
the theme helper, fail-soft fallback, and the optional ExtensionTool fields).
The end-to-end session dispatch plus privacy/no-leak guarantees are proven by
the golden gate ``extension_conformance_gate.py``.

Run: uv run python scripts/parity_checks/extension_tool_renderer_conformance.py --json
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

from pipy_harness.extensions import (
    ExtensionTool, ToolRenderContext, ToolResult, lines_component,
)
from pipy_harness.native.chrome import ChromeStyle
from pipy_harness.native.tool_renderers import build_tool_render_theme, render_tool_phase


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def run_checks() -> list[Check]:
    checks: list[Check] = []

    # 1. render_result receives details + state, returns themed lines.
    seen = {}
    def rr(ctx: ToolRenderContext):
        ctx.state["touched"] = True
        seen.update({"details": ctx.details, "is_result": ctx.is_result})
        return lines_component([ctx.theme.fg("success", f"k={ctx.details['k']}")])
    state: dict[str, object] = {}
    out = render_tool_phase(
        rr,
        ToolRenderContext(
            tool_name="kv", args={}, is_result=True, is_error=False,
            content="x", details={"k": "v"}, expanded=False, width=40,
            theme=build_tool_render_theme(ChromeStyle(enabled=False)), state=state,
        ),
    )
    checks.append(Check("render_result_details",
                        out == ["k=v"] and seen.get("details") == {"k": "v"}
                        and state.get("touched") is True,
                        "render_result sees details + state + returns themed lines"))

    # 2. fail-soft: a crashing renderer yields None (caller falls back).
    def boom(ctx):
        raise RuntimeError("x")
    fell_back = render_tool_phase(
        boom,
        ToolRenderContext(tool_name="t", args={}, is_result=True, is_error=False,
                          content="c", details=None, expanded=False, width=40,
                          theme=None, state={}),
    ) is None
    checks.append(Check("fail_soft", fell_back, "crashing renderer falls back to None"))

    # 3. A component whose render() returns a bare str is not char-per-line.
    class _StrComponent:
        def render(self, width):
            return "hello"

    def s(ctx):
        return _StrComponent()
    line_ok = render_tool_phase(
        s, ToolRenderContext(tool_name="t", args={}, is_result=False, is_error=False,
                             content=None, details=None, expanded=False, width=40,
                             theme=None, state={})) == ["hello"]
    checks.append(Check("str_not_char_per_line", line_ok,
                        "bare-string render output is one line"))

    # 4. renderers attach to ExtensionTool and stay optional.
    tool = ExtensionTool(name="t", description="d", input_schema={"type": "object"},
                         handler=lambda c, i: ToolResult(content="x"),
                         render_result=rr)
    bare = ExtensionTool(name="t2", description="d", input_schema={"type": "object"},
                         handler=lambda c, i: ToolResult(content="x"))
    checks.append(Check("renderer_fields",
                        tool.render_result is not None and bare.render_result is None,
                        "render_call/render_result are optional ExtensionTool fields"))

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
