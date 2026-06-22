# scripts/parity_checks/extension_message_renderer_conformance.py
"""Custom-message-renderer conformance gate.

This gate covers the rich message-renderer dispatch/coercion UNITS in isolation:
``render_extension_message`` (1-arg vs 2-arg dispatch, the component/styled path,
the plain back-compat path, width/expanded/theme threading, length bounding, and
the fail-soft no-leak fallback). It mirrors the tool-renderer gate
``extension_tool_renderer_conformance.py``; the end-to-end session dispatch plus
privacy/no-leak guarantees are proven by ``extension_conformance_gate.py``.

Run: uv run python scripts/parity_checks/extension_message_renderer_conformance.py --json
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

from pipy_harness.extensions import lines_component
from pipy_harness.native.extension_runtime import (
    _CUSTOM_RENDER_MAX_CHARS,
    RegisteredMessageRenderer,
    render_extension_message,
)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


class _FakeTheme:
    """Tiny theme exposing the ToolRenderTheme surface renderers touch."""

    def fg(self, color, text):
        return f"\x1b[1m{text}\x1b[0m"

    def bold(self, text):
        return f"\x1b[1m{text}\x1b[0m"

    def dim(self, text):
        return f"\x1b[2m{text}\x1b[0m"


def _renderers(custom_type, fn):
    return {custom_type: RegisteredMessageRenderer(custom_type, fn, "gate")}


def run_checks() -> list[Check]:
    checks: list[Check] = []
    theme = _FakeTheme()

    # 1. one-arg renderer returning text/lines -> plain (styled False).
    out = render_extension_message(
        _renderers("c", lambda data: f"hello {data['name']}"),
        "c", {"name": "world"}, width=40, theme=theme,
    )
    checks.append(Check(
        "one_arg_plain",
        out.styled is False and out.lines == ("hello world",),
        "1-arg renderer -> styled False with expected lines",
    ))

    # 2. two-arg component renderer (lines_component) -> styled, themed SGR present.
    def comp(data, ctx):
        return lines_component([ctx.theme.fg("success", f"k={data['k']}")])
    out = render_extension_message(
        _renderers("c", comp), "c", {"k": "v"}, width=40, theme=theme,
    )
    checks.append(Check(
        "two_arg_component_styled",
        out.styled is True
        and any("\x1b[1m" in line for line in out.lines)
        and out.lines == ("\x1b[1mk=v\x1b[0m",),
        "2-arg component -> styled True with themed SGR in lines",
    ))

    # 3. two-arg renderer returning a plain str -> plain (back-compat).
    out = render_extension_message(
        _renderers("c", lambda data, ctx: "plain text"),
        "c", {}, width=40, theme=theme,
    )
    checks.append(Check(
        "two_arg_str_plain",
        out.styled is False and out.lines == ("plain text",),
        "2-arg renderer returning str -> styled False",
    ))

    # 4. unknown custom_type -> generic plain fallback, non-empty.
    out = render_extension_message({}, "missing", {"a": 1}, width=40, theme=theme)
    checks.append(Check(
        "unknown_type_fallback",
        out.styled is False and len(out.lines) > 0 and out.lines != ("",),
        "unknown custom_type -> non-empty plain fallback",
    ))

    # 5. renderer raising -> "render error:" prefix, no leak of the message text.
    secret = "TOPSECRET-do-not-leak-12345"

    def boom(data):
        raise RuntimeError(secret)
    out = render_extension_message(_renderers("c", boom), "c", {}, theme=theme)
    joined = "\n".join(out.lines)
    checks.append(Check(
        "renderer_raises_no_leak",
        out.styled is False
        and len(out.lines) >= 1
        and out.lines[0].startswith("render error:")
        and "RuntimeError" in joined
        and secret not in joined,
        "raising renderer -> 'render error:' + only type name (message not leaked)",
    ))

    # 6. component render() raising -> "render error:" prefix, plain.
    class _BoomComponent:
        def render(self, width):
            raise ValueError("boom-inside-render")

    def comp_boom(data, ctx):
        return _BoomComponent()
    out = render_extension_message(_renderers("c", comp_boom), "c", {}, theme=theme)
    checks.append(Check(
        "component_render_raises",
        out.styled is False
        and len(out.lines) >= 1
        and out.lines[0].startswith("render error:")
        and "boom-inside-render" not in "\n".join(out.lines),
        "component render() raising -> 'render error:' plain, message not leaked",
    ))

    # 7. width threaded: 2-arg renderer echoes ctx.width.
    def echo_width(data, ctx):
        return f"width={ctx.width}"
    out = render_extension_message(
        _renderers("c", echo_width), "c", {}, width=137, theme=theme,
    )
    checks.append(Check(
        "width_threaded",
        out.styled is False and out.lines == ("width=137",),
        "ctx.width reflects the width argument passed",
    ))

    # 8. expanded threaded: 2-arg renderer echoes ctx.expanded.
    def echo_expanded(data, ctx):
        return f"expanded={ctx.expanded}"
    out = render_extension_message(
        _renderers("c", echo_expanded), "c", {}, expanded=True, theme=theme,
    )
    checks.append(Check(
        "expanded_threaded",
        out.styled is False and out.lines == ("expanded=True",),
        "ctx.expanded reflects the expanded argument passed",
    ))

    # 9. length bounding: a component render() far over the cap is truncated.
    long_text = "A" * (_CUSTOM_RENDER_MAX_CHARS + 50_000)

    class _LongComponent:
        def render(self, width):
            return long_text

    def comp_long(data, ctx):
        return _LongComponent()
    out = render_extension_message(_renderers("c", comp_long), "c", {}, theme=theme)
    joined = "\n".join(out.lines)
    checks.append(Check(
        "length_bounded",
        out.styled is True
        and len(joined) <= _CUSTOM_RENDER_MAX_CHARS
        and len(long_text) > _CUSTOM_RENDER_MAX_CHARS
        and "truncated" in joined,
        "over-cap component render() is bounded with a truncation marker",
    ))

    # 10. theme None tolerated: a 2-arg renderer guarding ctx.theme works.
    def theme_guard(data, ctx):
        if ctx.theme:
            return lines_component([ctx.theme.fg("success", "styled")])
        return "no-theme"
    out = render_extension_message(_renderers("c", theme_guard), "c", {}, theme=None)
    checks.append(Check(
        "theme_none_tolerated",
        out.styled is False and out.lines == ("no-theme",),
        "theme=None -> ctx.theme falsy, renderer returns plain without crashing",
    ))

    # 11a. capture-default idiom (1-arg) keeps its default; not clobbered by ctx.
    out = render_extension_message(
        _renderers("c", lambda data, prefix="P:": f"{prefix}{data['v']}"),
        "c", {"v": "x"}, width=40, theme=theme,
    )
    capture_ok = out.styled is False and out.lines == ("P:x",)

    # 11b. 1-arg renderer returning an object with .render() stays plain
    # (never enters the styled component path).
    class _RenderObject:
        def render(self, width):
            return "should-not-be-called"

        def __repr__(self):
            return "<RenderObject>"

    out = render_extension_message(
        _renderers("c", lambda data: _RenderObject()),
        "c", {}, width=40, theme=theme,
    )
    render_object_plain = (
        out.styled is False
        and "should-not-be-called" not in "\n".join(out.lines)
    )
    checks.append(Check(
        "one_arg_capture_default_and_render_object_plain",
        capture_ok and render_object_plain,
        "1-arg capture-default keeps default; 1-arg .render() object stays plain",
    ))

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
