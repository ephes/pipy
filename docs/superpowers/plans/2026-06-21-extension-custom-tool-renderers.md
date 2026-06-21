# Extension Custom Tool Renderers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a Python extension control how its own registered tool's call and result rows render in pipy's TUI (and captured output), with themed color, via a Pi-faithful render-once contract that forward-extends to a live runtime without an API break.

**Architecture:** A new public contract (`ToolRenderComponent` / `ToolRenderContext` / `ToolRenderTheme` / `lines_component`) lives in `extension_runtime.py` (re-exported from `pipy_harness.extensions`); `ExtensionTool` gains optional `render_call`/`render_result` fields. A new `native/tool_renderers.py` holds the concrete palette-backed theme helper, the shared line-coercion, and a fail-soft dispatch helper. The two renderers (`_TuiToolLoopRenderer`, `_ToolLoopRenderer`) keep a per-call slot (set in `render_tool_call`, consumed in `render_tool_result` — relying on the existing sequential call→result ordering that `_last_tool_name` already assumes) and dispatch to the extension's renderer, committing pre-styled lines under new `tool_call_custom`/`tool_result_custom` line-kinds. The extension's `ToolResult.details` reaches `render_result` through a tiny shared dict keyed by `provider_correlation_id` that `_ExtensionToolPort.invoke` writes and the renderer reads.

**Tech Stack:** Python 3 stdlib only (dataclasses, typing.Protocol, json, textwrap, re). Tests via `uv run pytest`; real-PTY tests via `pty`; conformance gate as a `scripts/parity_checks/*.py --json` script.

**Refinement vs spec:** The spec's data-flow names `tool_request_id` and adds `render_details` to `ToolExecutionResult`. The implementation refines this to a `provider_correlation_id`-keyed details sink + a renderer-side per-call slot (the id actually present at both render phases without reordering the loop). All spec invariants hold: `details`/`state` reach `render_result`, `state` is shared call→result, nothing extra is archived, and `provider_correlation_id` is never exposed to extension code (it stays internal to the dispatch). Update the spec's data-flow paragraph to match after Task 7.

---

## File Structure

- **Create** `src/pipy_harness/native/tool_renderers.py` — concrete `_PaletteToolRenderTheme` + `build_tool_render_theme` (theme helper) and `render_tool_phase` (fail-soft dispatch). One responsibility: turn an extension renderer + context into safe lines, and turn a `ChromeStyle` into a `ToolRenderTheme`. (The shared `coerce_tool_render_lines` lives in `extension_runtime.py` instead, so `lines_component` can call it without a cross-module import cycle; `render_tool_phase` imports it from there.)
- **Modify** `src/pipy_harness/native/extension_runtime.py` — add the public contract types (`ToolRenderComponent`, `ToolRenderContext`, `ToolRenderTheme`, `ThemeColor`, `lines_component`, `_LinesComponent`) and the two `ExtensionTool` fields.
- **Modify** `src/pipy_harness/extensions.py` — re-export the new public symbols.
- **Modify** `src/pipy_harness/native/themes.py` — add `success`/`warning` palette fields (with defaults) + values in the three built-in palettes.
- **Modify** `src/pipy_harness/native/chrome.py` — add `ChromeStyle.tool_custom(...)` (band-only, SGR-preserving) + the palette-code accessors the theme helper needs + an SGR-visible-length helper.
- **Modify** `src/pipy_harness/native/tool_loop_session.py` — `_ExtensionToolPort` details sink; build the renderer map + details dict; pass them into both renderers; dispatch in `_TuiToolLoopRenderer` and `_ToolLoopRenderer`.
- **Modify** `src/pipy_harness/native/tui.py` — `add_tool_call_custom`/`add_tool_result_custom`; new line-kinds in `_block_frame_lines`, `_line_kind_for_block`, `_styled_line`.
- **Create** tests: `tests/test_tool_render_contract.py`, `tests/test_tool_render_theme.py`, `tests/test_tool_render_dispatch.py`, `tests/test_native_extension_tool_renderer.py`, `tests/test_native_extension_tool_renderer_pty.py`.
- **Create** `scripts/parity_checks/extension_tool_renderer_conformance.py`, `docs/examples/extensions/themed-tool-renderer.py`.
- **Modify** `docs/examples/extensions/pipy-extension-conformance.py` and the docs in Task 7.

---

### Task 1: Public rendering contract + shared coercion

**Files:**
- Modify: `src/pipy_harness/native/extension_runtime.py` (near `ToolResult` ~262 and `CustomComponent` ~683)
- Modify: `src/pipy_harness/extensions.py` (`__all__` ~100 and imports ~26)
- Test: `tests/test_tool_render_contract.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tool_render_contract.py
from collections.abc import Mapping

from pipy_harness.extensions import (
    ExtensionTool,
    ToolRenderContext,
    coerce_tool_render_lines,
    lines_component,
)


def test_lines_component_str_is_one_logical_value_not_char_per_line():
    comp = lines_component("ok\ndone")
    assert comp.render(80) == ["ok", "done"]


def test_lines_component_rejects_char_per_line_for_single_line_str():
    comp = lines_component("hello")
    assert comp.render(80) == ["hello"]


def test_coerce_sequence_elementwise():
    assert coerce_tool_render_lines(["a", 1]) == ("a", "1")


def test_coerce_str_splits_on_newlines():
    assert coerce_tool_render_lines("a\nb") == ("a", "b")


def test_coerce_rejects_bytes():
    assert coerce_tool_render_lines(b"nope") is None


def test_coerce_rejects_unknown_type():
    assert coerce_tool_render_lines(object()) is None


def test_coerce_bounds_huge_output():
    out = coerce_tool_render_lines("x" * 20000)
    assert "tool render truncated" in "\n".join(out)


def test_extension_tool_accepts_renderers():
    tool = ExtensionTool(
        name="t",
        description="d",
        input_schema={"type": "object"},
        handler=lambda ctx, inp: None,
        render_call=lambda ctx: lines_component("call"),
        render_result=lambda ctx: lines_component("result"),
    )
    assert callable(tool.render_call)
    assert callable(tool.render_result)


def test_extension_tool_renderers_default_none():
    tool = ExtensionTool(
        name="t", description="d", input_schema={"type": "object"},
        handler=lambda ctx, inp: None,
    )
    assert tool.render_call is None and tool.render_result is None


def test_render_context_is_frozen_with_state_mapping():
    ctx = ToolRenderContext(
        tool_name="t", args={}, is_result=False, is_error=False,
        content=None, details=None, expanded=False, width=80,
        theme=None, state={},
    )
    assert isinstance(ctx.args, Mapping)
    ctx.state["x"] = 1  # state mapping is mutable even though the dataclass is frozen
    assert ctx.state["x"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tool_render_contract.py -q`
Expected: FAIL with `ImportError: cannot import name 'ToolRenderContext'`.

- [ ] **Step 3: Add the contract types to `extension_runtime.py`**

Add near the `CustomComponent` protocol (after line ~700). Ensure the file imports `MutableMapping` from `collections.abc` and `Literal`/`Protocol`/`runtime_checkable` from `typing` (it already imports `Protocol`, `Sequence`, `Mapping`, `Literal`, `dataclass`).

```python
ThemeColor = Literal["text", "accent", "success", "warning", "error", "dim"]


@runtime_checkable
class ToolRenderTheme(Protocol):
    """Bounded styling helper handed to extension tool renderers.

    Implementations map semantic names onto the active chrome palette and
    emit plain text when color is disabled (captured / NO_COLOR)."""

    def fg(self, color: ThemeColor, text: str) -> str: ...
    def bold(self, text: str) -> str: ...
    def dim(self, text: str) -> str: ...


@runtime_checkable
class ToolRenderComponent(Protocol):
    """A render-once tool-row component returned by render_call/render_result.

    `render(width)` returns the row's content lines (already theme-styled by
    the component). Aligned with `CustomComponent`; `invalidate`/`dispose`/
    `handle_input` are reserved for the later live-runtime slice and are not
    called here."""

    def render(self, width: int) -> Sequence[str]: ...


@dataclass(frozen=True, slots=True)
class ToolRenderContext:
    """Read-once context passed to an extension tool renderer.

    `state` is a single mutable mapping shared across render_call ->
    render_result for one tool execution. `details` is the extension's
    ToolResult.details (None at call phase). `theme` is a ToolRenderTheme."""

    tool_name: str
    args: Mapping[str, object]
    is_result: bool
    is_error: bool
    content: str | None
    details: Mapping[str, object] | None
    expanded: bool
    width: int
    theme: object  # ToolRenderTheme | None (None only in unit tests)
    state: MutableMapping[str, object]


def coerce_tool_render_lines(value: object) -> tuple[str, ...] | None:
    """Normalize a render() return (or lines_component input) to lines.

    Special-cases `str` (split on newlines) BEFORE the generic Sequence path,
    because `str` is itself a `Sequence[str]` and would otherwise render
    character-per-line. Returns None for unusable types (signals fallback)."""

    if isinstance(value, str):
        text = value
    elif isinstance(value, (bytes, bytearray)):
        return None
    elif isinstance(value, Sequence):
        try:
            text = "\n".join(str(item) for item in value)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - a bad sequence falls back
            return None
    else:
        return None
    if len(text) > _CUSTOM_RENDER_MAX_CHARS:
        text = text[: _CUSTOM_RENDER_MAX_CHARS - 64] + "\n[pipy: tool render truncated]"
    return tuple(text.splitlines() or [""])


@dataclass(frozen=True, slots=True)
class _LinesComponent:
    _lines: tuple[str, ...]

    def render(self, width: int) -> list[str]:
        return list(self._lines)


def lines_component(lines: str | Sequence[str]) -> ToolRenderComponent:
    """Convenience: wrap pre-rendered lines as a ToolRenderComponent."""

    coerced = coerce_tool_render_lines(lines)
    if coerced is None:
        coerced = (str(lines),)
    return _LinesComponent(coerced)
```

- [ ] **Step 4: Add the two `ExtensionTool` fields**

Modify `ExtensionTool` (lines 277-291) to add the optional renderer fields after `handler`:

```python
@dataclass(frozen=True, slots=True)
class ExtensionTool:
    name: str
    description: str
    input_schema: Mapping[str, object]
    handler: Callable[..., object]
    render_call: Callable[["ToolRenderContext"], object] | None = None
    render_result: Callable[["ToolRenderContext"], object] | None = None
```

- [ ] **Step 5: Re-export from `pipy_harness.extensions`**

In `src/pipy_harness/extensions.py`, import the new names from `pipy_harness.native.extension_runtime` (alongside the existing `ExtensionTool`/`ToolResult` import) and add them to `__all__`:

```python
# add to the existing `from pipy_harness.native.extension_runtime import (...)`
    ToolRenderComponent,
    ToolRenderContext,
    ToolRenderTheme,
    ThemeColor,
    lines_component,
    coerce_tool_render_lines,
```

```python
# add to __all__
    "ToolRenderComponent",
    "ToolRenderContext",
    "ToolRenderTheme",
    "ThemeColor",
    "lines_component",
    "coerce_tool_render_lines",
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_tool_render_contract.py -q`
Expected: PASS (10 passed).

- [ ] **Step 7: Commit**

```bash
git add src/pipy_harness/native/extension_runtime.py src/pipy_harness/extensions.py tests/test_tool_render_contract.py
git commit -m "feat: add extension tool renderer contract types"
```

---

### Task 2: Palette success/warning + theme helper

**Files:**
- Modify: `src/pipy_harness/native/themes.py` (`ChromePalette` ~36; `_PI_PALETTE` ~68; `_HIGH_CONTRAST_PALETTE` ~90; `_OCEAN_PALETTE` ~111)
- Modify: `src/pipy_harness/native/chrome.py` (`ChromeStyle` ~80; add helpers)
- Create: `src/pipy_harness/native/tool_renderers.py`
- Test: `tests/test_tool_render_theme.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tool_render_theme.py
from pipy_harness.native.chrome import ChromeStyle
from pipy_harness.native.themes import DEFAULT_PALETTE, resolve_palette
from pipy_harness.native.tool_renderers import build_tool_render_theme


def _truecolor_style():
    return ChromeStyle(enabled=True, truecolor=True, palette=DEFAULT_PALETTE)


def test_disabled_theme_is_plain_text():
    theme = build_tool_render_theme(ChromeStyle(enabled=False))
    assert theme.fg("success", "ok") == "ok"
    assert theme.bold("ok") == "ok"
    assert theme.dim("ok") == "ok"


def test_truecolor_success_emits_palette_code_and_resets():
    theme = build_tool_render_theme(_truecolor_style())
    out = theme.fg("success", "ok")
    assert out.startswith("\x1b[") and out.endswith("\x1b[0m") and "ok" in out


def test_fallback_uses_16color_code_when_not_truecolor():
    theme = build_tool_render_theme(
        ChromeStyle(enabled=True, truecolor=False, palette=DEFAULT_PALETTE)
    )
    out = theme.fg("error", "bad")
    # 16-color error fallback is "31"; truecolor "38;2;..." must NOT appear.
    assert "38;2;" not in out and "bad" in out


def test_success_and_warning_resolve_on_all_builtin_palettes():
    for name in ("pi", "high-contrast", "ocean"):
        palette = resolve_palette(name)
        assert palette.success_truecolor and palette.warning_truecolor
        assert palette.success_fallback and palette.warning_fallback
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tool_render_theme.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipy_harness.native.tool_renderers'`.

- [ ] **Step 3: Add palette fields (with defaults) to `ChromePalette`**

In `themes.py`, append four fields to `ChromePalette` (lines 36-62) AFTER the existing fields, each with a default so file-backed palettes that omit them still construct:

```python
    success_truecolor: str = "38;2;152;195;121"
    success_fallback: str = "32"
    warning_truecolor: str = "38;2;229;192;123"
    warning_fallback: str = "33"
```

- [ ] **Step 4: Set explicit values in the three built-in palettes**

Add to `_PI_PALETTE` (after `separator_fallback`):

```python
    success_truecolor="38;2;152;195;121",
    success_fallback="32",
    warning_truecolor="38;2;240;198;116",
    warning_fallback="1;33",
```

Add to `_HIGH_CONTRAST_PALETTE`:

```python
    success_truecolor="1;38;2;0;255;0",
    success_fallback="1;92",
    warning_truecolor="1;38;2;255;215;0",
    warning_fallback="1;93",
```

Add to `_OCEAN_PALETTE`:

```python
    success_truecolor="38;2;126;200;160",
    success_fallback="32",
    warning_truecolor="38;2;226;192;141",
    warning_fallback="33",
```

- [ ] **Step 5: Add `chrome.py` helpers**

In `chrome.py`, add a module-level SGR-visible-length helper (top of file, near other module helpers) and a `tool_custom` method + a `palette_code` accessor on `ChromeStyle`:

```python
import re as _re

_CHROME_SGR_RE = _re.compile(r"\x1b\[[0-9;]*m")


def _visible_len_no_sgr(text: str) -> int:
    return len(_CHROME_SGR_RE.sub("", text))
```

Add these methods to `ChromeStyle` (near `tool_result`, ~161):

```python
    def palette_code(self, truecolor_code: str, fallback_code: str) -> str:
        """Pick the truecolor vs 16-color SGR parameter for this style."""
        return truecolor_code if self.truecolor else fallback_code

    def tool_custom(self, text: str, *, width: int) -> str:
        """Band-only framing for extension-rendered tool rows.

        Applies the tool background band + right padding but imposes NO
        foreground color, so the renderer's own SGR is preserved. When color
        is disabled the renderer already produced plain text, so pass it
        through unchanged."""
        if not self.enabled:
            return text
        bg = self.palette.tool_command_bg_truecolor
        visible = _visible_len_no_sgr(text)
        padding = " " * max(0, width - visible)
        if visible == 0:
            return f"\x1b[{bg}m{padding}\x1b[0m"
        return f"\x1b[{bg}m{text}\x1b[0m\x1b[{bg}m{padding}\x1b[0m"
```

- [ ] **Step 6: Create `tool_renderers.py` with the theme helper**

```python
# src/pipy_harness/native/tool_renderers.py
"""Concrete tool-render theme + fail-soft dispatch for extension tool renderers."""

from __future__ import annotations

from pipy_harness.native.chrome import ChromeStyle
from pipy_harness.native.extension_runtime import ThemeColor, ToolRenderTheme


class _PaletteToolRenderTheme:
    """A ToolRenderTheme backed by a ChromeStyle's palette."""

    def __init__(self, style: ChromeStyle) -> None:
        self._style = style

    def _code(self, color: ThemeColor) -> str:
        p = self._style.palette
        table = {
            "text": (p.user_message_text_truecolor, "39"),
            "accent": (p.accent_truecolor, p.accent_fallback),
            "success": (p.success_truecolor, p.success_fallback),
            "warning": (p.warning_truecolor, p.warning_fallback),
            "error": (p.error_truecolor, p.error_fallback),
            "dim": (p.dim_truecolor, p.dim_fallback),
        }
        truecolor_code, fallback_code = table.get(color, table["text"])
        return self._style.palette_code(truecolor_code, fallback_code)

    def fg(self, color: ThemeColor, text: str) -> str:
        if not self._style.enabled:
            return text
        return f"\x1b[{self._code(color)}m{text}\x1b[0m"

    def bold(self, text: str) -> str:
        if not self._style.enabled:
            return text
        return f"\x1b[1m{text}\x1b[0m"

    def dim(self, text: str) -> str:
        return self.fg("dim", text)


def build_tool_render_theme(style: ChromeStyle) -> ToolRenderTheme:
    return _PaletteToolRenderTheme(style)
```

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run pytest tests/test_tool_render_theme.py -q`
Expected: PASS (4 passed).

- [ ] **Step 8: Commit**

```bash
git add src/pipy_harness/native/themes.py src/pipy_harness/native/chrome.py src/pipy_harness/native/tool_renderers.py tests/test_tool_render_theme.py
git commit -m "feat: add success/warning palette colors and tool render theme"
```

---

### Task 3: Fail-soft dispatch helper

**Files:**
- Modify: `src/pipy_harness/native/tool_renderers.py`
- Test: `tests/test_tool_render_dispatch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tool_render_dispatch.py
from pipy_harness.extensions import ToolRenderContext, lines_component
from pipy_harness.native.tool_renderers import render_tool_phase


def _ctx():
    return ToolRenderContext(
        tool_name="t", args={"a": 1}, is_result=True, is_error=False,
        content="raw", details={"k": "v"}, expanded=False, width=40,
        theme=None, state={},
    )


def test_good_renderer_returns_lines():
    out = render_tool_phase(lambda ctx: lines_component(["a", "b"]), _ctx())
    assert out == ["a", "b"]


def test_renderer_that_raises_falls_back_to_none():
    def boom(ctx):
        raise RuntimeError("nope")
    assert render_tool_phase(boom, _ctx()) is None


def test_render_method_that_raises_falls_back():
    class Bad:
        def render(self, width):
            raise ValueError("bad")
    assert render_tool_phase(lambda ctx: Bad(), _ctx()) is None


def test_non_component_return_falls_back():
    assert render_tool_phase(lambda ctx: 123, _ctx()) is None


def test_bad_render_output_type_falls_back():
    class Bad:
        def render(self, width):
            return 5
    assert render_tool_phase(lambda ctx: Bad(), _ctx()) is None


def test_bare_string_render_is_not_char_per_line():
    class S:
        def render(self, width):
            return "hello"
    assert render_tool_phase(lambda ctx: S(), _ctx()) == ["hello"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tool_render_dispatch.py -q`
Expected: FAIL with `ImportError: cannot import name 'render_tool_phase'`.

- [ ] **Step 3: Implement `render_tool_phase`**

Append to `tool_renderers.py`:

```python
from collections.abc import Callable

from pipy_harness.native.extension_runtime import (  # noqa: E402 (grouped import)
    ToolRenderContext,
    coerce_tool_render_lines,
)


def render_tool_phase(
    renderer: Callable[[ToolRenderContext], object],
    ctx: ToolRenderContext,
) -> list[str] | None:
    """Run one extension tool renderer fail-soft.

    Returns the rendered lines, or None to signal the caller should fall back
    to pipy's default rendering. A renderer that raises, returns a non-
    component, whose render() raises, or returns an uncoercible value all
    yield None. KeyboardInterrupt/SystemExit propagate."""

    try:
        component = renderer(ctx)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 - a bad renderer falls back
        return None
    render = getattr(component, "render", None)
    if not callable(render):
        return None
    try:
        produced = render(ctx.width)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 - a bad render() falls back
        return None
    coerced = coerce_tool_render_lines(produced)
    if coerced is None:
        return None
    return list(coerced)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tool_render_dispatch.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/pipy_harness/native/tool_renderers.py tests/test_tool_render_dispatch.py
git commit -m "feat: add fail-soft tool renderer dispatch helper"
```

---

### Task 4: ExtensionToolPort details sink

**Files:**
- Modify: `src/pipy_harness/native/tool_loop_session.py` (`_ExtensionToolPort` ~657-714)
- Test: `tests/test_native_extension_tool_renderer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_native_extension_tool_renderer.py
from pathlib import Path

from pipy_harness.extensions import ExtensionTool, RegisteredTool, ToolResult
from pipy_harness.native.tool_loop_session import _ExtensionToolPort
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolRequest,
    make_tool_request_id,
)


def _registered(handler, **kw):
    tool = ExtensionTool(
        name="kv", description="d",
        input_schema={"type": "object"}, handler=handler, **kw,
    )
    return RegisteredTool(tool=tool, extension="ext")


def test_port_writes_details_to_sink(tmp_path: Path):
    sink: dict[str, object] = {}
    port = _ExtensionToolPort(
        _registered(lambda ctx, inp: ToolResult(content="c", details={"k": "v"})),
        has_ui=False, render_details_sink=sink,
    )
    req = ToolRequest(
        tool_request_id=make_tool_request_id(), tool_name="kv",
        arguments={}, provider_correlation_id="corr-1",
    )
    port.invoke(req, ToolContext(workspace_root=tmp_path.resolve()))
    assert sink["corr-1"] == {"k": "v"}


def test_port_writes_none_details_when_absent(tmp_path: Path):
    sink: dict[str, object] = {}
    port = _ExtensionToolPort(
        _registered(lambda ctx, inp: ToolResult(content="c")),
        has_ui=False, render_details_sink=sink,
    )
    req = ToolRequest(
        tool_request_id=make_tool_request_id(), tool_name="kv",
        arguments={}, provider_correlation_id="corr-2",
    )
    port.invoke(req, ToolContext(workspace_root=tmp_path.resolve()))
    assert sink["corr-2"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_native_extension_tool_renderer.py -q`
Expected: FAIL with `TypeError: _ExtensionToolPort.__init__() got an unexpected keyword argument 'render_details_sink'`.

- [ ] **Step 3: Add the sink to `_ExtensionToolPort`**

Modify `__init__` (lines 657-674) to accept and store the sink (add the param after `flags`):

```python
    def __init__(
        self,
        registered: RegisteredTool,
        *,
        has_ui: bool,
        notify_sink: Callable[[str, str], None] | None = None,
        flags: Mapping[str, object] | None = None,
        render_details_sink: MutableMapping[str, object] | None = None,
    ) -> None:
        self._registered = registered
        self._has_ui = has_ui
        self._notify_sink = notify_sink
        self._flags = dict(flags or {})
        self._render_details_sink = render_details_sink
        tool = registered.tool
        self._definition = ToolDefinition(
            name=tool.name,
            description=str(tool.description),
            input_schema=dict(tool.input_schema),
        )
```

In `invoke` (lines 676-714), after computing the success `content` and BEFORE the final `return ToolExecutionResult(...)`, record the details only when this tool has a result renderer (keep the sink small):

```python
        if (
            self._render_details_sink is not None
            and self._registered.tool.render_result is not None
        ):
            details = result.details if isinstance(result, ToolResult) else None
            self._render_details_sink[request.provider_correlation_id] = (
                dict(details) if isinstance(details, Mapping) else None
            )
```

Ensure `MutableMapping` is importable in this module (add to the `from collections.abc import ...` line if absent).

> Note: the test passes `render_result` is None in the first test? No — the first test's tool has no `render_result`, so the guard above would skip it. **Adjust the first test** to register `render_result=lambda ctx: None` so the sink is populated, matching the real path (only result-rendered tools need details). Update `test_port_writes_details_to_sink` and `test_port_writes_none_details_when_absent` to pass `render_result=lambda ctx: None`.

- [ ] **Step 4: Apply the test adjustment**

Edit both tests in `tests/test_native_extension_tool_renderer.py` to add `render_result=lambda ctx: None` to the `_registered(...)` call.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_native_extension_tool_renderer.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add src/pipy_harness/native/tool_loop_session.py tests/test_native_extension_tool_renderer.py
git commit -m "feat: stash extension tool details for the renderer via a sink"
```

---

### Task 5: TUI renderer dispatch + custom line-kinds

**Files:**
- Modify: `src/pipy_harness/native/tui.py` (`add_*` ~2152-2205; `_block_frame_lines` ~3014; `_line_kind_for_block` ~3073; `_styled_line` ~2802)
- Modify: `src/pipy_harness/native/tool_loop_session.py` (`_TuiToolLoopRenderer` ~6202-6310; construction ~1329; registry build ~1287)
- Test: `tests/test_native_extension_tool_renderer.py` (append)

- [ ] **Step 1: Write the failing test (TUI block emission, no PTY)**

Append to `tests/test_native_extension_tool_renderer.py`:

```python
import io

from pipy_harness.extensions import lines_component
from pipy_harness.native.tool_loop_session import _TuiToolLoopRenderer
from pipy_harness.native.tui import ToolLoopTerminalUi


def _tui(tmp_path):
    return ToolLoopTerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=tmp_path,
    )


def test_tui_renderer_uses_render_result(tmp_path):
    from pipy_harness.native.models import ProviderToolCall

    tool = ExtensionTool(
        name="kv", description="d", input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="ignored", details={"k": "v"}),
        render_result=lambda ctx: lines_component(
            [f"key={ctx.details['k']}", f"err={ctx.is_error}"]
        ),
    )
    ui = _tui(tmp_path)
    sink: dict[str, object] = {"corr-1": {"k": "v"}}
    renderer = _TuiToolLoopRenderer(
        ui=ui,
        tool_renderers={"kv": tool},
        render_details_sink=sink,
    )
    renderer.render_tool_call(
        ProviderToolCall(provider_correlation_id="corr-1", tool_name="kv",
                         arguments_json="{}")
    )
    renderer.render_tool_result(output_text="ignored", is_error=False)
    blocks = [b for b in ui._history_blocks if b[0] == "tool_result_custom"]
    assert blocks, "expected a tool_result_custom block"
    text = "\n".join(blocks[-1][1])
    assert "key=v" in text and "err=False" in text


def test_tui_renderer_falls_back_when_renderer_crashes(tmp_path):
    from pipy_harness.native.models import ProviderToolCall

    def boom(ctx):
        raise RuntimeError("nope")

    tool = ExtensionTool(
        name="kv", description="d", input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="real-output"),
        render_result=boom,
    )
    ui = _tui(tmp_path)
    renderer = _TuiToolLoopRenderer(
        ui=ui, tool_renderers={"kv": tool}, render_details_sink={},
    )
    renderer.render_tool_call(
        ProviderToolCall(provider_correlation_id="c", tool_name="kv",
                         arguments_json="{}")
    )
    renderer.render_tool_result(output_text="real-output", is_error=False)
    kinds = [b[0] for b in ui._history_blocks]
    assert "tool_result" in kinds and "tool_result_custom" not in kinds
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_native_extension_tool_renderer.py -q`
Expected: FAIL with `TypeError: _TuiToolLoopRenderer.__init__() got an unexpected keyword argument 'tool_renderers'`.

- [ ] **Step 3: Add custom block methods to `ToolLoopTerminalUi`**

In `tui.py`, add after `add_tool_result` (~2205):

```python
    def add_tool_call_custom(self, lines: Iterable[str]) -> None:
        """Commit extension-rendered call-row lines (pre-styled, SGR-safe)."""
        self._settle_reasoning()
        self.working_text = ""
        self.tool_output_text = ""
        self._history_blocks.append(("tool_call_custom", tuple(lines) or ("",)))
        self.paint()

    def add_tool_result_custom(
        self, lines: Iterable[str], *, duration_seconds: float | None = None
    ) -> None:
        """Commit extension-rendered result-row lines (pre-styled, SGR-safe)."""
        self._settle_reasoning()
        self.tool_output_text = ""
        rendered = list(lines)
        if duration_seconds is not None:
            rendered.extend(("", f"Took {duration_seconds:.1f}s"))
        self._history_blocks.append(("tool_result_custom", tuple(rendered or [""])))
        self.paint()
```

- [ ] **Step 4: Handle the new kinds in `_block_frame_lines`**

In `_block_frame_lines` (~3014), add the two kinds to the prefix map (value `" "`), and add a dedicated branch that clips SGR-aware WITHOUT `textwrap.wrap` (wrapping miscounts SGR and can split escapes). Insert near the top of the method, right after `width = ...`:

```python
        if kind in {"tool_call_custom", "tool_result_custom"}:
            rendered: list[_FrameLine] = [_FrameLine("", "tool_result")]
            for line in block_lines:
                rendered.append(
                    _FrameLine(_clip_custom_overlay_text(f" {line}", width), kind)
                )
            rendered.append(_FrameLine("", "tool_result"))
            if kind == "tool_result_custom":
                rendered.append(_FrameLine(""))
            return rendered
```

(`_clip_custom_overlay_text` and `_FrameLine` are already in this module.)

- [ ] **Step 5: Map the kinds in `_line_kind_for_block` and `_styled_line`**

In `_line_kind_for_block` (~3073) add:

```python
        "tool_call_custom": "tool_call_custom",
        "tool_result_custom": "tool_result_custom",
```

In `_styled_line` (~2802), add before the final `return text`:

```python
        if line.kind in {"tool_call_custom", "tool_result_custom"}:
            return style.tool_custom(line.text, width=width)
```

(Use `line.text` — NOT the rstripped `text` — so the band padding math in `tool_custom` sees the renderer's content verbatim. The leading blank rows above keep `kind="tool_result"` so they get the plain band.)

- [ ] **Step 6: Add dispatch to `_TuiToolLoopRenderer`**

In `tool_loop_session.py`, change `_TuiToolLoopRenderer.__init__` (~6202) to accept the map + sink and init the per-call slot:

```python
    def __init__(
        self,
        *,
        ui: ToolLoopTerminalUi,
        tool_renderers: Mapping[str, ExtensionTool] | None = None,
        render_details_sink: Mapping[str, object] | None = None,
    ) -> None:
        self._ui = ui
        self._streamed_any = False
        self._stop_working_event: threading.Event | None = None
        self._working_thread: threading.Thread | None = None
        self._last_tool_name = ""
        self._tool_renderers = dict(tool_renderers or {})
        self._render_details_sink = render_details_sink
        self._pending_render: dict[str, object] | None = None
```

Replace `render_tool_call` (~6274):

```python
    def render_tool_call(self, call: ProviderToolCall) -> None:
        self._stop_working(clear=True)
        self._last_tool_name = call.tool_name
        self._pending_render = None
        tool = self._tool_renderers.get(call.tool_name)
        if tool is not None:
            args = _parse_tool_input(call.arguments_json)
            state: dict[str, object] = {}
            self._pending_render = {
                "corr": call.provider_correlation_id,
                "args": args,
                "state": state,
            }
            if tool.render_call is not None:
                lines = self._dispatch_render(tool.render_call, args, state,
                                              is_result=False, content=None,
                                              details=None, is_error=False)
                if lines is not None:
                    self._ui.add_tool_call_custom(lines)
                    return
        self._ui.add_tool_call(_plain_tool_call_header(call))
```

Replace `render_tool_result` (~6282):

```python
    def render_tool_result(
        self,
        *,
        output_text: str,
        is_error: bool,
        duration_seconds: float | None = None,
    ) -> None:
        pending = self._pending_render
        self._pending_render = None
        if pending is not None:
            tool = self._tool_renderers.get(self._last_tool_name)
            if tool is not None and tool.render_result is not None:
                details = None
                if self._render_details_sink is not None:
                    details = self._render_details_sink.get(pending["corr"])
                lines = self._dispatch_render(
                    tool.render_result, pending["args"], pending["state"],
                    is_result=True, content=output_text, details=details,
                    is_error=is_error,
                )
                if lines is not None:
                    self._ui.add_tool_result_custom(
                        lines, duration_seconds=duration_seconds
                    )
                    return
        if self._last_tool_name == "read" and not is_error:
            return
        lines = self._visible_tool_result_lines(output_text.splitlines() or [""])
        if self._ui.tools_expanded:
            rendered = lines
        else:
            preview_lines = lines[: self._RESULT_LINE_PREVIEW_MAX_LENGTH]
            earlier = len(lines) - len(preview_lines)
            if earlier > 0:
                rendered = [
                    f"... ({earlier} earlier lines, ctrl+o to expand)",
                    *lines[-self._RESULT_LINE_PREVIEW_MAX_LENGTH :],
                ]
            else:
                rendered = preview_lines
        self._ui.add_tool_result(
            lines=rendered, is_error=is_error, duration_seconds=duration_seconds
        )
```

Add the shared dispatch helper to `_TuiToolLoopRenderer`:

```python
    def _dispatch_render(self, renderer, args, state, *, is_result, content,
                         details, is_error):
        from pipy_harness.native.chrome import chrome_style_for
        from pipy_harness.native.tool_renderers import (
            build_tool_render_theme,
            render_tool_phase,
        )
        from pipy_harness.extensions import ToolRenderContext

        style = chrome_style_for(self._ui.terminal_stream)
        ctx = ToolRenderContext(
            tool_name=self._last_tool_name, args=args, is_result=is_result,
            is_error=is_error, content=content, details=details,
            expanded=self._ui.tools_expanded,
            width=self._ui._dimensions()[0],
            theme=build_tool_render_theme(style), state=state,
        )
        return render_tool_phase(renderer, ctx)
```

Add the imports at the top of `tool_loop_session.py` if absent: `from pipy_harness.extensions import ExtensionTool` (already imports `ToolResult`/`RegisteredTool` from there — extend that import).

- [ ] **Step 7: Wire the map + sink into construction**

At the renderer construction site (~1329), build the renderer map + details dict from the activated extension tools and pass them. First, near the registry build (~1287), add:

```python
    extension_render_details: dict[str, object] = {}
    extension_tool_renderers: dict[str, ExtensionTool] = {
        rt.tool.name: rt.tool
        for rt in _ext_runtime.tools
        if rt.tool.render_call is not None or rt.tool.render_result is not None
    }
```

Pass the sink into each `_ExtensionToolPort(...)` (the loop at ~1291):

```python
        _port = _ExtensionToolPort(
            _registered_tool,
            has_ui=terminal_ui is not None,
            notify_sink=_extension_notify,
            flags=extension_flag_values,
            render_details_sink=extension_render_details,
        )
```

Update the construction block (~1329):

```python
    renderer: _ToolLoopRenderer | _TuiToolLoopRenderer
    if terminal_ui is not None:
        renderer = _TuiToolLoopRenderer(
            ui=terminal_ui,
            tool_renderers=extension_tool_renderers,
            render_details_sink=extension_render_details,
        )
    else:
        renderer = _ToolLoopRenderer(
            output_stream=output_stream,
            error_stream=error_stream,
            tool_renderers=extension_tool_renderers,
            render_details_sink=extension_render_details,
        )
```

> `_ToolLoopRenderer` does not accept those kwargs yet — Task 6 adds them. To keep this task green, temporarily pass them only to `_TuiToolLoopRenderer` and leave the `_ToolLoopRenderer(...)` call unchanged; Task 6 flips the captured branch.

- [ ] **Step 8: Apply the Step-7 caveat**

Leave the `_ToolLoopRenderer(...)` construction unchanged in this task (no `tool_renderers`/`render_details_sink` args yet).

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run pytest tests/test_native_extension_tool_renderer.py -q`
Expected: PASS (4 passed).

- [ ] **Step 10: Run the broader TUI suite for regressions**

Run: `uv run pytest tests/test_native_tool_loop_tui.py -q`
Expected: PASS (no regressions in default tool rendering).

- [ ] **Step 11: Commit**

```bash
git add src/pipy_harness/native/tui.py src/pipy_harness/native/tool_loop_session.py tests/test_native_extension_tool_renderer.py
git commit -m "feat: dispatch extension tool renderers in the TUI"
```

---

### Task 6: Captured (non-TTY) renderer dispatch

**Files:**
- Modify: `src/pipy_harness/native/tool_loop_session.py` (`_ToolLoopRenderer` ~5506; `render_tool_call` ~5899; `render_tool_result` ~5922; construction ~1333)
- Test: `tests/test_native_extension_tool_renderer.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_captured_renderer_emits_custom_lines(tmp_path):
    from pipy_harness.native.models import ProviderToolCall
    from pipy_harness.native.tool_loop_session import _ToolLoopRenderer

    out, err = io.StringIO(), io.StringIO()
    tool = ExtensionTool(
        name="kv", description="d", input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="x", details={"k": "v"}),
        render_result=lambda ctx: lines_component([f"KV:{ctx.details['k']}"]),
    )
    renderer = _ToolLoopRenderer(
        output_stream=out, error_stream=err,
        tool_renderers={"kv": tool},
        render_details_sink={"c": {"k": "v"}},
    )
    renderer.render_tool_call(
        ProviderToolCall(provider_correlation_id="c", tool_name="kv",
                         arguments_json="{}")
    )
    renderer.render_tool_result(output_text="x", is_error=False)
    assert "KV:v" in err.getvalue()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_native_extension_tool_renderer.py::test_captured_renderer_emits_custom_lines -q`
Expected: FAIL with `TypeError: _ToolLoopRenderer.__init__() got an unexpected keyword argument 'tool_renderers'`.

- [ ] **Step 3: Extend `_ToolLoopRenderer.__init__`**

Add the two kwargs (after `error_stream`) and init the slot (append to the existing `__init__` body, lines 5506-5551):

```python
    def __init__(
        self,
        *,
        output_stream: TextIO,
        error_stream: TextIO,
        tool_renderers: "Mapping[str, ExtensionTool] | None" = None,
        render_details_sink: "Mapping[str, object] | None" = None,
    ) -> None:
        # ... keep all existing assignments ...
        self._tool_renderers = dict(tool_renderers or {})
        self._render_details_sink = render_details_sink
        self._pending_render: dict[str, object] | None = None
        self._last_tool_name = ""
```

- [ ] **Step 4: Dispatch in the captured render methods**

Wrap `render_tool_call` (lines 5899-5908): set the pending slot + last name, try a custom call render, else default:

```python
    def render_tool_call(self, call: ProviderToolCall) -> None:
        self._last_tool_name = call.tool_name
        self._pending_render = None
        tool = self._tool_renderers.get(call.tool_name)
        if tool is not None:
            args = _parse_tool_input(call.arguments_json)
            state: dict[str, object] = {}
            self._pending_render = {
                "corr": call.provider_correlation_id, "args": args, "state": state,
            }
            if tool.render_call is not None:
                lines = self._dispatch_render(tool.render_call, args, state,
                                              is_result=False, content=None,
                                              details=None, is_error=False)
                if lines is not None:
                    self._clear_working()
                    self._close_reasoning()
                    self._error_stream.write(self._tool_panel_blank_line())
                    for line in lines:
                        self._error_stream.write(self._tool_panel_line(line))
                    self._error_stream.write(self._tool_panel_blank_line())
                    self._error_stream.flush()
                    return
        self._clear_working()
        self._close_reasoning()
        self._error_stream.write(self._tool_panel_blank_line())
        rendered = self._format_pi_call_header_rich(call.tool_name, call.arguments_json)
        self._error_stream.write(self._tool_panel_rich_line(rendered))
        self._error_stream.write(self._tool_panel_blank_line())
        self._error_stream.flush()
```

At the top of `render_tool_result` (lines 5922-5966), add a custom-render fast path before the default body:

```python
        pending = self._pending_render
        self._pending_render = None
        if pending is not None:
            tool = self._tool_renderers.get(self._last_tool_name)
            if tool is not None and tool.render_result is not None:
                details = None
                if self._render_details_sink is not None:
                    details = self._render_details_sink.get(pending["corr"])
                lines = self._dispatch_render(
                    tool.render_result, pending["args"], pending["state"],
                    is_result=True, content=output_text, details=details,
                    is_error=is_error,
                )
                if lines is not None:
                    for line in lines:
                        self._error_stream.write(self._tool_panel_line(line))
                    if duration_seconds is not None:
                        self._error_stream.write(self._tool_panel_blank_line())
                        self._error_stream.write(self._tool_panel_line(
                            f"Took {duration_seconds:.1f}s", style=self._ANSI_DIM))
                    self._error_stream.write(self._tool_panel_blank_line())
                    self._error_stream.flush()
                    return
        # ... existing default body unchanged ...
```

Add the `_dispatch_render` helper to `_ToolLoopRenderer` (captured uses `self._error_stream` for the style and a fixed width):

```python
    def _dispatch_render(self, renderer, args, state, *, is_result, content,
                         details, is_error):
        from pipy_harness.native.chrome import chrome_style_for
        from pipy_harness.native.tool_renderers import (
            build_tool_render_theme, render_tool_phase,
        )
        from pipy_harness.extensions import ToolRenderContext

        style = chrome_style_for(self._error_stream)
        ctx = ToolRenderContext(
            tool_name=self._last_tool_name, args=args, is_result=is_result,
            is_error=is_error, content=content, details=details,
            expanded=False, width=80,
            theme=build_tool_render_theme(style), state=state,
        )
        return render_tool_phase(renderer, ctx)
```

- [ ] **Step 5: Flip the captured construction branch**

In the construction block (~1333), pass the kwargs to `_ToolLoopRenderer` (undo the Task-5 caveat):

```python
        renderer = _ToolLoopRenderer(
            output_stream=output_stream,
            error_stream=error_stream,
            tool_renderers=extension_tool_renderers,
            render_details_sink=extension_render_details,
        )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_native_extension_tool_renderer.py -q`
Expected: PASS (5 passed).

- [ ] **Step 7: Commit**

```bash
git add src/pipy_harness/native/tool_loop_session.py tests/test_native_extension_tool_renderer.py
git commit -m "feat: dispatch extension tool renderers in captured output"
```

---

### Task 7: PTY test, golden conformance gate, example, docs

**Files:**
- Test: `tests/test_native_extension_tool_renderer_pty.py`
- Create: `scripts/parity_checks/extension_tool_renderer_conformance.py`
- Create: `docs/examples/extensions/themed-tool-renderer.py`
- Modify: `docs/examples/extensions/pipy-extension-conformance.py`
- Modify: `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, `docs/parity-plan.md`, `CHANGELOG.md`, and the spec's data-flow paragraph.

- [ ] **Step 1: Write the PTY test (real terminal, color)**

```python
# tests/test_native_extension_tool_renderer_pty.py
import io
import os
import pty
import threading
import time
from pathlib import Path
from typing import TextIO, cast

import pytest

from pipy_harness.extensions import ExtensionTool, ToolResult, lines_component
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.tool_loop_session import _TuiToolLoopRenderer
from pipy_harness.native.tui import ToolLoopTerminalUi


def _spawn_drainer(fd: int):
    chunks: list[bytes] = []

    def drain():
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            chunks.append(chunk)

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    return chunks


def _wait_for(chunks, needle: str, timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    enc = needle.encode("utf-8")
    while time.monotonic() < deadline:
        if enc in b"".join(chunks):
            return True
        time.sleep(0.02)
    return False


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
def test_pty_custom_tool_result_renders_colored(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.delenv("NO_COLOR", raising=False)
    err_master, err_slave = pty.openpty()
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8")
    chunks = _spawn_drainer(err_master)
    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, io.StringIO()),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
    )
    tool = ExtensionTool(
        name="kv", description="d", input_schema={"type": "object"},
        handler=lambda ctx, inp: ToolResult(content="x", details={"k": "v"}),
        render_result=lambda ctx: lines_component(
            [ctx.theme.fg("success", f"KV-OK:{ctx.details['k']}")]
        ),
    )
    renderer = _TuiToolLoopRenderer(
        ui=ui, tool_renderers={"kv": tool},
        render_details_sink={"c": {"k": "v"}},
    )
    renderer.render_tool_call(
        ProviderToolCall(provider_correlation_id="c", tool_name="kv",
                         arguments_json="{}")
    )
    renderer.render_tool_result(output_text="x", is_error=False)
    try:
        assert _wait_for(chunks, "KV-OK:v"), "custom tool row never rendered"
        captured = b"".join(chunks).decode("utf-8", "replace")
        assert "\x1b[" in captured, "expected SGR color in the custom row"
    finally:
        terminal.close()
        os.close(err_master)
```

- [ ] **Step 2: Run the PTY test to verify it passes**

Run: `uv run pytest tests/test_native_extension_tool_renderer_pty.py -q`
Expected: PASS (1 passed; skipped on non-posix).

- [ ] **Step 3: Extend the golden conformance extension**

In `docs/examples/extensions/pipy-extension-conformance.py`, change the `conformance_probe` tool registration (~105-120) to include renderers that write proof markers, and import `lines_component`:

```python
from pipy_harness.extensions import ExtensionTool, ToolResult, lines_component  # extend existing import

def _render_call(ctx):
    _proof("render_call", tool_name=ctx.tool_name)
    return lines_component([f"probe call: {sorted(ctx.args)}"])

def _render_result(ctx):
    _proof("render_result", has_details=bool(ctx.details), is_result=ctx.is_result)
    return lines_component([ctx.theme.fg("success", "probe ok")])

api.register_tool(
    ExtensionTool(
        name="conformance_probe",
        description="Run the deterministic conformance probe.",
        input_schema={"type": "object",
                      "properties": {"probe_arg": {"type": "string"}}},
        handler=_probe,
        render_call=_render_call,
        render_result=_render_result,
    )
)
```

- [ ] **Step 4: Write the conformance gate**

```python
# scripts/parity_checks/extension_tool_renderer_conformance.py
"""Custom-tool-renderer conformance gate.

Run: uv run python scripts/parity_checks/extension_tool_renderer_conformance.py --json
"""
from __future__ import annotations

import argparse
import io
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
    out = render_tool_phase(
        rr,
        ToolRenderContext(
            tool_name="kv", args={}, is_result=True, is_error=False,
            content="x", details={"k": "v"}, expanded=False, width=40,
            theme=build_tool_render_theme(ChromeStyle(enabled=False)), state={},
        ),
    )
    checks.append(Check("render_result_details", out == ["k=v"]
                        and seen.get("details") == {"k": "v"},
                        "render_result sees details + returns themed lines"))

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

    # 3. str render output is not char-per-line.
    def s(ctx):
        return "hello"
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
        ], "indent": 2}, indent=2))
    else:
        for c in checks:
            print(f"[{'PASS' if c.passed else 'FAIL'}] {c.name}: {c.detail}")
        print("ALL PASS" if passed else "FAILURES PRESENT")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the gate**

Run: `uv run python scripts/parity_checks/extension_tool_renderer_conformance.py --json`
Expected: JSON with `"passed": true` and 4 checks.

- [ ] **Step 6: Write the example extension**

```python
# docs/examples/extensions/themed-tool-renderer.py
"""Example: a tool that renders its result as a themed key/value table."""
from pipy_harness.extensions import ExtensionTool, ToolResult, lines_component


def activate(api):
    def handler(ctx, params):
        data = {"status": "ok", "items": params.get("items", 0)}
        return ToolResult(content=str(data), details=data)

    def render_result(ctx):
        d = ctx.details or {}
        rows = [ctx.theme.bold("result")]
        for key, value in d.items():
            color = "success" if value not in ("", 0, None) else "dim"
            rows.append(f"  {ctx.theme.dim(key + ':')} {ctx.theme.fg(color, str(value))}")
        return lines_component(rows)

    api.register_tool(ExtensionTool(
        name="kv_report",
        description="Return a small key/value report.",
        input_schema={"type": "object",
                      "properties": {"items": {"type": "integer"}}},
        handler=handler,
        render_result=render_result,
    ))
```

- [ ] **Step 7: Update docs**

- `docs/extension-api.md`: add a **slice 17 — landed** entry to "Suggested Implementation Slices" describing `render_call`/`render_result`, the snapshot runtime, the theme helper, and the deferred live runtime; trim the rich-UI follow-on list in the status paragraph (~38-42) to remove "custom tool rendering".
- `docs/pi-mono-gap-audit.md`: in §"Extension and package platform follow-ons", move "custom tool renderers" from Follow-ons to landed.
- `docs/parity-plan.md`: in the §4 Extension row, add custom tool renderers to the shipped list.
- `CHANGELOG.md`: under `[Unreleased]`, add "Extensions can render their own tool call/result rows (`render_call`/`render_result`) with themed color."
- The spec `docs/superpowers/specs/2026-06-21-extension-custom-tool-renderers-design.md`: replace the data-flow "carry details to render time" mechanism with the implemented `provider_correlation_id`-keyed sink + renderer per-call slot (no `ToolExecutionResult` change).

- [ ] **Step 8: Run the full check suite**

Run: `uv run pytest tests/test_tool_render_contract.py tests/test_tool_render_theme.py tests/test_tool_render_dispatch.py tests/test_native_extension_tool_renderer.py tests/test_native_extension_tool_renderer_pty.py -q`
Expected: PASS (all).

Run: `just check`
Expected: lint + typecheck + full test suite green.

Run: `just docs-build`
Expected: docs build clean.

- [ ] **Step 9: Commit**

```bash
git add docs/ scripts/parity_checks/extension_tool_renderer_conformance.py CHANGELOG.md
git commit -m "feat: golden gate, example, and docs for extension tool renderers"
```

---

## Self-Review

**1. Spec coverage:**
- Contract (component return + shared `state`) → Task 1. ✓
- Bounded theme helper + `success`/`warning` palette → Task 2. ✓
- Fields on `ExtensionTool`, own tools only → Task 1 (fields), Task 5/6 (lookup by name only for registered extension tools). ✓
- `details` to render time (local-only, not archived/provider) → Task 4 sink. ✓
- Render-time dispatch + pre-styled lines committed under `*_custom` kinds → Task 5 (TTY), Task 6 (captured). ✓
- Default framing only; fail-soft; bounds; privacy → Task 3 (fail-soft + bounds), Task 5 (band framing), Tasks 4/5/6 (details never archived — sink is in-memory only). ✓
- Conformance gate + example + docs → Task 7. ✓
- Deferred (`invalidate`/`is_partial`/`renderShell:"self"`/built-in override) → not implemented, consistent with spec out-of-scope. ✓

**2. Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N". Each code step is concrete. The Task-5 Step-7/8 caveat is an explicit, intentional staging note (captured branch wired in Task 6), not a placeholder.

**3. Type consistency:** `render_call`/`render_result` (fields, dispatch), `ToolRenderContext` fields (`tool_name`/`args`/`is_result`/`is_error`/`content`/`details`/`expanded`/`width`/`theme`/`state`) used identically in Tasks 1, 3, 5, 6, 7. `coerce_tool_render_lines`, `render_tool_phase`, `build_tool_render_theme`, `lines_component`, `tool_custom`, `add_tool_call_custom`/`add_tool_result_custom`, line-kinds `tool_call_custom`/`tool_result_custom`, and `render_details_sink` are named consistently across tasks. The sink key is `provider_correlation_id` everywhere.

**Known assumption (documented):** the per-call slot relies on the loop rendering each tool call immediately followed by its result (the same sequential ordering the existing `_last_tool_name` field already depends on). If parallel tool rendering is added later, replace the single slot with a `provider_correlation_id`-keyed store; the deferred live-runtime slice will revisit this.
