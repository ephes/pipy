# Extension Rich Message Renderers (slice C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade slice-16's text-only extension message renderer to a Pi-faithful, themed `Component` that renders an extension's custom session entry (appended via `ctx.append_entry`) with color, render-once at the current terminal width, fully fail-soft.

**Architecture:** Reuse the slice-17 custom-tool-renderer machinery (`coerce_tool_render_lines`, `build_tool_render_theme` → `ToolRenderTheme`, `lines_component`, `ToolRenderComponent`) — no parallel module. A renderer that accepts a second parameter receives a new `MessageRenderContext` and may return a component; a 1-arg `renderer(data)` keeps its exact slice-16 plain-text behavior. A returned component is committed SGR-preserving under a new `custom_message_custom` TUI line-kind (mirroring `tool_call_custom`); text/lines returns and any failure fall back to today's sanitized generic path.

**Tech Stack:** Python 3 (stdlib only), `uv run pytest`, `mypy`, `ruff`, `just check`. Reference: design spec `docs/superpowers/specs/2026-06-21-extension-rich-message-renderers-design.md`.

---

## Background facts (verified against the working tree)

- `render_extension_message(renderers, custom_type, data)` lives in
  `src/pipy_harness/native/extension_runtime.py:2000`, calls
  `renderer.renderer(data)` (1 arg, `:2016`), and returns a flat
  `tuple[str, ...]`.
- It is wired in the `extension_append_entry` closure at
  `src/pipy_harness/native/tool_loop_session.py:1472-1492`: render → if
  `terminal_ui is not None` call `terminal_ui.add_custom_entry(safe_type, rendered)`
  (`:1484`), else emit a diagnostic (`:1485-1491`).
- `extension_renderer_map` is bound at `tool_loop_session.py:1209` and refreshed
  on `/reload` at `:2299` — **keep both**.
- `add_custom_entry` (`src/pipy_harness/native/tui.py:2422`) sanitizes each line
  via `sanitize_label_text` (strips SGR) and commits a `("custom", …)` block — it
  cannot carry color. The SGR-preserving precedent is `add_tool_call_custom`
  (`tui.py:2477`) committing a `("tool_call_custom", …)` block.
- Styled custom line-kinds are handled in `_styled_line` (`tui.py:3405-3406`,
  `style.tool_custom(...)`), `_block_frame_lines` (`tui.py:3554-3563`), and
  `_line_kind_for_block` (`tui.py:3630-3631`).
- The TTY tool path sources `width=self._ui._dimensions()[0]`,
  `expanded=self._ui.tools_expanded`, `style=chrome_style_for(self._ui.terminal_stream)`,
  `theme=build_tool_render_theme(style)` (`tool_loop_session.py:6515-6521`); the
  captured path uses `width=80`, `expanded=False`,
  `style=chrome_style_for(self._error_stream)` (`:6090-6095`).
- Reusable helpers in `extension_runtime.py`: `coerce_tool_render_lines` (`:793`),
  `lines_component`/`_LinesComponent` (`:826`/`:819`), `ToolRenderComponent`
  (`:726`), `ToolRenderTheme`/`ThemeColor` (`:714`/`:710`),
  `_coerce_rendered_lines` (`:2040`), `_bounded_render_text` (`:2060`),
  `_copy_custom_entry_data` (`:2029`), `_safe_diagnostic` (used at `:2020`).
- Public surface `src/pipy_harness/extensions.py` re-exports from
  `native.extension_runtime` with an `__all__` (renderer names already listed at
  `:51-98`/`:109-171`).
- Existing direct callers of `render_extension_message` that assert a tuple:
  `tests/test_native_extension_dispatch.py:241,259,281,308,312` — these are
  updated in Task 2.

## File structure

- **Modify** `src/pipy_harness/native/extension_runtime.py` — add
  `MessageRenderContext`, `RenderedCustomEntry`, `MessageRenderComponent` alias,
  `_renderer_wants_context`; rewrite `render_extension_message`.
- **Modify** `src/pipy_harness/extensions.py` — re-export the three new names.
- **Modify** `src/pipy_harness/native/tui.py` — add `add_custom_entry_styled` and
  the `custom_message_custom` line-kind in the three rendering sites.
- **Modify** `src/pipy_harness/native/tool_loop_session.py` — compute
  width/expanded/theme in `extension_append_entry`, branch on `.styled`.
- **Create** `scripts/parity_checks/extension_message_renderer_conformance.py`.
- **Modify** `docs/examples/extensions/pipy-extension-conformance.py` +
  `scripts/parity_checks/extension_conformance_gate.py` — golden marker.
- **Modify** `docs/extension-api.md`, `CHANGELOG.md`, `docs/parity-plan.md`,
  `docs/pi-mono-gap-audit.md` — doc reconciliation.
- **Test files:** `tests/test_native_extension_message_renderer.py` (new, units),
  `tests/test_native_extension_dispatch.py` (update), and a real-PTY test in
  `tests/test_native_tool_loop_tui.py`.

---

### Task 1: Public types — `MessageRenderContext`, `RenderedCustomEntry`, `MessageRenderComponent`

**Files:**
- Modify: `src/pipy_harness/native/extension_runtime.py` (near the other render types, after `ToolRenderContext` at ~`:755`)
- Modify: `src/pipy_harness/extensions.py`
- Test: `tests/test_native_extension_message_renderer.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_native_extension_message_renderer.py
from pipy_harness.extensions import (
    MessageRenderComponent,
    MessageRenderContext,
    RenderedCustomEntry,
    ToolRenderComponent,
    lines_component,
)


def test_message_render_context_fields():
    ctx = MessageRenderContext(
        custom_type="card",
        data={"title": "hi"},
        expanded=True,
        width=80,
        theme=None,
    )
    assert ctx.custom_type == "card"
    assert ctx.data == {"title": "hi"}
    assert ctx.expanded is True
    assert ctx.width == 80
    assert ctx.theme is None


def test_rendered_custom_entry_fields():
    entry = RenderedCustomEntry(lines=("a", "b"), styled=True)
    assert entry.lines == ("a", "b")
    assert entry.styled is True


def test_message_render_component_is_tool_render_component_alias():
    # The alias keeps one component contract across rich-UI slices.
    assert MessageRenderComponent is ToolRenderComponent
    component = lines_component(["x"])
    assert isinstance(component, MessageRenderComponent)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_native_extension_message_renderer.py -q`
Expected: FAIL with `ImportError: cannot import name 'MessageRenderContext'`.

- [ ] **Step 3: Add the types in `extension_runtime.py`**

Insert after the `ToolRenderContext` dataclass (after `:755`):

```python
@dataclass(frozen=True, slots=True)
class MessageRenderContext:
    """Read-only context passed to a rich extension message renderer.

    A renderer that accepts a second positional parameter receives this; a
    1-arg ``renderer(data)`` keeps its slice-16 plain-text behavior. ``theme``
    is a ToolRenderTheme (None only in unit tests / no-color captured runs)."""

    custom_type: str
    data: object | None
    expanded: bool
    width: int
    theme: object  # ToolRenderTheme | None


# A message renderer's component shares the tool-renderer component contract
# (render(width) -> Sequence[str]); one contract across the rich-UI slices.
MessageRenderComponent = ToolRenderComponent


@dataclass(frozen=True, slots=True)
class RenderedCustomEntry:
    """Result of rendering one custom entry.

    ``styled`` True means ``lines`` carry theme SGR and must be committed
    SGR-preserving (the ``custom_message_custom`` TUI kind); False means the
    plain, sanitized back-compat path."""

    lines: tuple[str, ...]
    styled: bool
```

- [ ] **Step 4: Re-export from `src/pipy_harness/extensions.py`**

Add to the import block from `native.extension_runtime` (alphabetically near the other names):

```python
    MessageRenderComponent,
    MessageRenderContext,
    RenderedCustomEntry,
```

Add the same three string names to `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_native_extension_message_renderer.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add src/pipy_harness/native/extension_runtime.py src/pipy_harness/extensions.py tests/test_native_extension_message_renderer.py
git commit -m "feat(extension-api): rich message-renderer public types (slice C)"
```

---

### Task 2: Rich dispatch in `render_extension_message`

**Files:**
- Modify: `src/pipy_harness/native/extension_runtime.py:2000-2027` (rewrite `render_extension_message`); add `_renderer_wants_context` helper above it.
- Test: `tests/test_native_extension_message_renderer.py` (add cases); `tests/test_native_extension_dispatch.py` (update 5 assertions).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_native_extension_message_renderer.py`:

```python
from pipy_harness.native.extension_runtime import (
    RegisteredMessageRenderer,
    render_extension_message,
)


def _renderers(custom_type, fn):
    return {custom_type: RegisteredMessageRenderer(custom_type, fn, "ext")}


def test_one_arg_renderer_returns_plain_lines():
    r = _renderers("note", lambda data: [f"text:{data['t']}"])
    out = render_extension_message(r, "note", {"t": "hi"})
    assert out.lines == ("text:hi",)
    assert out.styled is False


def test_one_arg_renderer_returning_component_like_stays_plain():
    # Critical: a 1-arg (slice-16) renderer must NEVER hit the component path,
    # even if it returns an object exposing a render() attribute.
    class _Componentish:
        def render(self, width):
            return ["should-not-be-used"]

        def __repr__(self):
            return "PLAINREPR"

    out = render_extension_message(
        _renderers("note", lambda data: _Componentish()), "note", {},
    )
    assert out.styled is False
    assert "should-not-be-used" not in "".join(out.lines)


def test_two_arg_component_renderer_is_styled():
    # Component whose render(width) emits a themed line via ctx.theme.
    def renderer(data, ctx):
        text = ctx.theme.fg("accent", data["t"]) if ctx.theme else data["t"]
        return lines_component([text])

    class _Theme:
        def fg(self, color, text):
            return f"\x1b[1m{text}\x1b[0m"

        def bold(self, text):
            return text

        def dim(self, text):
            return text

    out = render_extension_message(
        _renderers("card", renderer), "card", {"t": "hi"},
        width=40, expanded=False, theme=_Theme(),
    )
    assert out.styled is True
    assert out.lines == ("\x1b[1mhi\x1b[0m",)


def test_two_arg_text_return_is_plain():
    out = render_extension_message(
        _renderers("note", lambda data, ctx: f"w={ctx.width}"),
        "note", {}, width=77,
    )
    assert out.lines == ("w=77",)
    assert out.styled is False


def test_unknown_type_renders_generic_plain():
    out = render_extension_message({}, "note", {"t": "x"})
    assert out.styled is False
    assert out.lines and "t" in out.lines[0]


def test_renderer_exception_is_fail_soft():
    def boom(data, ctx):
        raise RuntimeError("kaboom")

    out = render_extension_message(_renderers("card", boom), "card", {})
    assert out.styled is False
    assert out.lines[0].startswith("render error:")
    assert "kaboom" not in out.lines[0]


def test_component_render_exception_is_fail_soft():
    class _Bad:
        def render(self, width):
            raise RuntimeError("render-boom")

    out = render_extension_message(
        _renderers("card", lambda data, ctx: _Bad()), "card", {},
    )
    assert out.styled is False
    assert out.lines[0].startswith("render error:")


def test_expanded_threaded_to_renderer():
    out = render_extension_message(
        _renderers("note", lambda data, ctx: f"e={ctx.expanded}"),
        "note", {}, expanded=True,
    )
    assert out.lines == ("e=True",)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_native_extension_message_renderer.py -q`
Expected: FAIL (`render_extension_message` returns a tuple, has no `.lines`/`.styled`, and ignores `width`/`expanded`/`theme`).

- [ ] **Step 3: Add the arity helper and rewrite the dispatch**

Add above `render_extension_message` (after `extension_message_renderers` ~`:1974`):

```python
def _renderer_wants_context(renderer: Callable[..., object]) -> bool:
    """True if ``renderer`` can accept a second positional MessageRenderContext.

    Defaults to False (1-arg slice-16 form) when the signature is unavailable,
    so back-compat is the safe fallback."""

    try:
        sig = inspect.signature(renderer)
    except (TypeError, ValueError):
        return False
    positional = 0
    for param in sig.parameters.values():
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            positional += 1
        elif param.kind is inspect.Parameter.VAR_POSITIONAL:
            return True
    return positional >= 2
```

Replace `render_extension_message` (`:2000-2027`) with:

```python
def render_extension_message(
    renderers: Mapping[str, RegisteredMessageRenderer],
    custom_type: str,
    data: object | None,
    *,
    width: int = 80,
    expanded: bool = False,
    theme: object | None = None,
) -> RenderedCustomEntry:
    """Render a custom entry through its extension renderer, fail-soft.

    A renderer that accepts a second parameter receives a MessageRenderContext
    and may return a component (committed SGR-preserving, ``styled=True``).
    Text/lines returns and any failure fall back to plain rendering
    (``styled=False``)."""

    def _plain(value: object | None) -> RenderedCustomEntry:
        if value is None:
            return RenderedCustomEntry((), False)
        return RenderedCustomEntry((_bounded_render_text(value),), False)

    renderer = renderers.get(custom_type)
    if renderer is None:
        return _plain(data)
    detached = _copy_custom_entry_data(data)
    wants_context = _renderer_wants_context(renderer.renderer)
    try:
        if wants_context:
            ctx = MessageRenderContext(
                custom_type=custom_type,
                data=detached,
                expanded=expanded,
                width=width,
                theme=theme,
            )
            rendered = renderer.renderer(detached, ctx)
        else:
            rendered = renderer.renderer(detached)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as err:  # noqa: BLE001 - bound a bad renderer
        return RenderedCustomEntry((f"render error: {_safe_diagnostic(err)}",), False)

    # The component (styled) path is reachable ONLY for context-aware (2-arg)
    # renderers. A 1-arg renderer(data) keeps exact slice-16 plain-text
    # behavior even if it returns an object exposing a render() attribute.
    if wants_context:
        render = getattr(rendered, "render", None)
        if callable(render) and not isinstance(rendered, (str, bytes, bytearray)):
            try:
                produced = render(width)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as err:  # noqa: BLE001 - a bad render() falls back
                return RenderedCustomEntry(
                    (f"render error: {_safe_diagnostic(err)}",), False
                )
            coerced = coerce_tool_render_lines(produced)
            if coerced is None:
                return _plain(detached)
            return RenderedCustomEntry(tuple(coerced), True)

    try:
        return RenderedCustomEntry(_coerce_rendered_lines(rendered), False)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as err:  # noqa: BLE001 - bound bad renderer output
        return RenderedCustomEntry((f"render error: {_safe_diagnostic(err)}",), False)
```

Confirm `inspect` is already imported at the top of `extension_runtime.py` (it is — used by `_coerce_rendered_lines`).

- [ ] **Step 4: Update the existing dispatch tests to the new return type**

In `tests/test_native_extension_dispatch.py`, change the five assertions to read `.lines`:
- `:241` → `assert render_extension_message(renderers, "note", payload).lines == ("done",)`
- `:259` → `rendered = render_extension_message(renderers, "note", {"text": "hello"}).lines`
- `:281` → `rendered = render_extension_message(renderers, "note", {"text": "hello"}).lines`
- `:308` → `assert render_extension_message(renderers, "note", {"text": "hello"}).lines == (`
- `:312` → `assert render_extension_message(renderers, "boom", {}).lines == (`

(Adjust only the call to append `.lines`; the expected tuples are unchanged.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_native_extension_message_renderer.py tests/test_native_extension_dispatch.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pipy_harness/native/extension_runtime.py tests/test_native_extension_message_renderer.py tests/test_native_extension_dispatch.py
git commit -m "feat(extension-api): rich/arity-flex message-renderer dispatch (slice C)"
```

---

### Task 3: TUI `custom_message_custom` line-kind + `add_custom_entry_styled`

**Files:**
- Modify: `src/pipy_harness/native/tui.py` — add `add_custom_entry_styled` (after `add_tool_result_custom` ~`:2495`); extend `_styled_line` (`:3405`), `_block_frame_lines` (`:3554`), `_line_kind_for_block` (`:3630`).
- Test: `tests/test_native_tool_loop_tui.py` (add a unit using the existing `ToolLoopTerminalUi` test harness in that file).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_native_tool_loop_tui.py` (reuse the file's existing `ToolLoopTerminalUi` construction helper; adapt the constructor call to match the others in that file):

```python
def test_add_custom_entry_styled_preserves_sgr_and_clips(make_tui):
    ui = make_tui(width=20)
    ui.add_custom_entry_styled(["\x1b[1mHELLO\x1b[0m", "x" * 50])
    # The styled block is committed under the custom_message_custom kind.
    assert any(kind == "custom_message_custom" for kind, _ in ui._history_blocks)
    frame = "\n".join(ui.render_lines(width=20))
    assert "\x1b[1m" in frame          # SGR preserved (not sanitized away)
    assert "HELLO" in frame
```

If `tests/test_native_tool_loop_tui.py` has no `make_tui` fixture, mirror the construction used by an existing `add_tool_call_custom`/`add_custom_entry` test in that same file and inline it.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_native_tool_loop_tui.py::test_add_custom_entry_styled_preserves_sgr_and_clips -q`
Expected: FAIL with `AttributeError: 'ToolLoopTerminalUi' object has no attribute 'add_custom_entry_styled'`.

- [ ] **Step 3: Add `add_custom_entry_styled` (after `add_tool_result_custom`, ~`:2495`)**

```python
    def add_custom_entry_styled(self, lines: Iterable[str]) -> None:
        """Commit extension-rendered custom-entry lines (pre-styled, SGR-safe).

        Unlike ``add_custom_entry`` (sanitized + ``[label]`` prefix), the rich
        renderer's component owns its full styling; no label line is injected
        (matches Pi's custom-message component replacing the default box)."""

        self._settle_reasoning()
        self.working_text = ""
        self.tool_output_text = ""
        self._history_blocks.append(("custom_message_custom", tuple(lines) or ("",)))
        self.paint()
```

- [ ] **Step 4: Wire the new kind into the three rendering sites**

In `_styled_line` (`:3405`), extend the membership set:

```python
        if line.kind in {"tool_call_custom", "tool_result_custom", "custom_message_custom"}:
            return style.tool_custom(line.text, width=width)
```

In `_block_frame_lines` (`:3554`), extend the custom-render branch guard:

```python
        if kind in {"tool_call_custom", "tool_result_custom", "custom_message_custom"}:
            custom_rendered: list[_FrameLine] = [_FrameLine("", "tool_result")]
            for line in block_lines:
                custom_rendered.append(
                    _FrameLine(_clip_custom_overlay_text(f" {line}", width), kind)
                )
            custom_rendered.append(_FrameLine("", "tool_result"))
            if kind in {"tool_result_custom", "custom_message_custom"}:
                custom_rendered.append(_FrameLine(""))
            return custom_rendered
```

In `_line_kind_for_block` (`:3630`), add the mapping entry:

```python
            "custom_message_custom": "custom_message_custom",
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_native_tool_loop_tui.py::test_add_custom_entry_styled_preserves_sgr_and_clips -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pipy_harness/native/tui.py tests/test_native_tool_loop_tui.py
git commit -m "feat(extension-api): custom_message_custom styled TUI line-kind (slice C)"
```

---

### Task 4: Wire width/expanded/theme into `extension_append_entry` (TTY + captured)

**Files:**
- Modify: `src/pipy_harness/native/tool_loop_session.py:1472-1492` (the `extension_append_entry` closure).
- Test: `tests/test_native_tool_loop_session.py` (product-path test using the file's existing scripted-provider session harness).

- [ ] **Step 1: Write the failing product-path test**

Add to `tests/test_native_tool_loop_session.py`, mirroring an existing test that activates an extension and runs a command through a real `tool_loop_session` with a captured/TTY renderer. The extension registers a rich renderer and a command that appends an entry:

```python
def test_rich_message_renderer_styles_scrollback_and_does_not_leak(tmp_path, ...):
    # Extension file: registers a component renderer + a command that appends.
    ext = tmp_path / "ext.py"
    ext.write_text(
        "from pipy_harness.extensions import lines_component\n"
        "def activate(api):\n"
        "    def render(data, ctx):\n"
        "        text = ctx.theme.fg('accent', data['title']) if ctx.theme else data['title']\n"
        "        return lines_component([text])\n"
        "    api.register_message_renderer('card', render)\n"
        "    def cmd(ctx, args):\n"
        "        ctx.append_entry('card', {'title': 'SECRET_TITLE'})\n"
        "    api.register_command('mkcard', 'make a card', cmd)\n"
    )
    # Run a TTY session, dispatch /mkcard, capture the committed frame + archive.
    # (Use the existing TTY/scripted-provider harness in this test module.)
    ...
    assert "SECRET_TITLE" in committed_frame          # body rendered live
    assert "\x1b[" in committed_frame                  # styled (color) on a TTY
    assert "[card]" not in committed_frame             # no forced label (judgment 2)
    assert "SECRET_TITLE" not in archive_text          # body never archived
```

Fill the harness wiring (`...`) by copying the closest existing extension-command product-path test in `tests/test_native_tool_loop_session.py` (look for one that builds a `tool_loop_session` with a real `ToolLoopTerminalUi` and inspects `_history_blocks` / archive).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_native_tool_loop_session.py::test_rich_message_renderer_styles_scrollback_and_does_not_leak -q`
Expected: FAIL — the entry renders plain (no SGR) because the closure still calls `render_extension_message` without width/expanded/theme and routes everything through `add_custom_entry`.

- [ ] **Step 3: Rewrite the `extension_append_entry` render/commit block**

Replace `tool_loop_session.py:1478-1491` (the `rendered = render_extension_message(...)` through the `else` diagnostic) with:

```python
            if terminal_ui is not None:
                from pipy_harness.native.chrome import chrome_style_for
                from pipy_harness.native.tool_renderers import build_tool_render_theme

                style = chrome_style_for(terminal_ui.terminal_stream)
                rendered = render_extension_message(
                    extension_renderer_map,
                    safe_type,
                    safe_data,
                    width=terminal_ui._dimensions()[0],
                    expanded=terminal_ui.tools_expanded,
                    theme=build_tool_render_theme(style),
                )
                if rendered.styled:
                    terminal_ui.add_custom_entry_styled(rendered.lines)
                else:
                    terminal_ui.add_custom_entry(safe_type, rendered.lines)
            else:
                from pipy_harness.native.chrome import chrome_style_for
                from pipy_harness.native.tool_renderers import build_tool_render_theme

                style = chrome_style_for(error_stream)
                rendered = render_extension_message(
                    extension_renderer_map,
                    safe_type,
                    safe_data,
                    width=80,
                    expanded=False,
                    theme=build_tool_render_theme(style),
                )
                lines = "\n".join(str(line) for line in rendered.lines)
                self._emit_diagnostic(
                    terminal_ui,
                    error_stream,
                    f"{safe_type}:\n{lines}" if lines else safe_type,
                )
            return appended.id
```

(The local imports mirror the existing tool-renderer pattern at
`tool_loop_session.py:6508-6512`; `chrome_style_for` on a non-TTY / `NO_COLOR`
stream yields a disabled style, so `build_tool_render_theme` emits plain text.)

- [ ] **Step 4: Run the product-path test + the dispatch suite**

Run: `uv run pytest tests/test_native_tool_loop_session.py::test_rich_message_renderer_styles_scrollback_and_does_not_leak tests/test_native_extension_dispatch.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pipy_harness/native/tool_loop_session.py tests/test_native_tool_loop_session.py
git commit -m "feat(extension-api): wire rich message render width/expanded/theme (slice C)"
```

---

### Task 5: Conformance gate `extension_message_renderer_conformance.py`

**Files:**
- Create: `scripts/parity_checks/extension_message_renderer_conformance.py`
- Reference shape: `scripts/parity_checks/extension_tool_renderer_conformance.py`

- [ ] **Step 1: Write the gate (mirror the tool-renderer gate's `--json` structure)**

Create `scripts/parity_checks/extension_message_renderer_conformance.py`. Open
`extension_tool_renderer_conformance.py` and copy its argparse/`--json`/Check-row
scaffolding verbatim, then implement these checks against
`render_extension_message`:

```python
# Checks (each a Check row, all must pass, no network):
# 1. one-arg renderer text -> styled=False, expected lines
# 2. two-arg component renderer -> styled=True, themed SGR present
# 3. two-arg renderer returning str -> styled=False (back-compat)
# 4. unknown custom_type -> generic plain fallback, styled=False
# 5. renderer raising -> "render error:" line, styled=False, no exc text leak
# 6. component.render() raising -> "render error:" line, styled=False
# 7. width threaded: renderer echoes ctx.width
# 8. expanded threaded: renderer echoes ctx.expanded
# 9. length bounding: a component emitting > _CUSTOM_RENDER_MAX_CHARS is truncated
# 10. theme None tolerated (renderer guards `if ctx.theme`)
# 11. 1-arg renderer returning a render()-bearing object -> styled=False (never
#     enters the component path; exact slice-16 plain behavior preserved)
```

Use `RegisteredMessageRenderer(custom_type, fn, "gate")` to build the renderer
map and a tiny fake theme object with `fg`/`bold`/`dim` like the unit test.

- [ ] **Step 2: Run the gate**

Run: `uv run python scripts/parity_checks/extension_message_renderer_conformance.py --json`
Expected: JSON with every Check row `"ok": true` and exit 0.

- [ ] **Step 3: Commit**

```bash
git add scripts/parity_checks/extension_message_renderer_conformance.py
git commit -m "test(extension-api): message-renderer conformance gate (slice C)"
```

---

### Task 6: Golden conformance extension + gate marker

**Files:**
- Modify: `docs/examples/extensions/pipy-extension-conformance.py`
- Modify: `scripts/parity_checks/extension_conformance_gate.py`
- Reference: how slice 17/18 added markers (`render_call`/`render_result`, chrome markers).

- [ ] **Step 1: Add a rich renderer + marker to the golden extension**

In `docs/examples/extensions/pipy-extension-conformance.py`, register a rich
component renderer and append a card during the conformance flow, recording a
metadata-only marker (no body):

```python
    from pipy_harness.extensions import lines_component

    # Unique leak canaries: these must never reach the proof file or the
    # metadata archive (the rendered body is live-only; the entry data is
    # archive-excluded). Do NOT assert on the custom_type string itself — it is
    # safe metadata and may legitimately appear.
    _MSG_BODY_SENTINEL = "PIPY_MSGBODY_9f3a2c"
    _MSG_DATA_SENTINEL = "PIPY_MSGDATA_7b1e44"

    def _render_card(data, ctx):
        _record("message_renderer_component", {"styled": ctx.theme is not None})
        body = (
            ctx.theme.fg("accent", _MSG_BODY_SENTINEL)
            if ctx.theme
            else _MSG_BODY_SENTINEL
        )
        return lines_component([body])

    api.register_message_renderer("conformance-card", _render_card)
```

In the command handler (where other markers are recorded), append the entry so
the renderer runs:

```python
    ctx.append_entry("conformance-card", {"sentinel": _MSG_DATA_SENTINEL})
```

Use the file's existing `_record(feature, payload)`/proof helper (match its
current name); the marker payload must stay metadata-only (`{"styled": bool}`),
carrying neither the rendered body nor the `data` body.

- [ ] **Step 2: Assert the marker in the golden gate**

In `scripts/parity_checks/extension_conformance_gate.py`:
- add `"message_renderer_component"` to the required-marker set;
- assert, reusing the gate's existing no-leak scan, that the body sentinel
  `"PIPY_MSGBODY_9f3a2c"` (rendered, live-only) and the data sentinel
  `"PIPY_MSGDATA_7b1e44"` (entry data) are **absent from both the proof file and
  the metadata archive**. (The data sentinel legitimately lives in the native
  session tree — the product store — so do not scan that; scan proof + archive,
  as the existing slice-10/17 no-leak checks do.) Do **not** assert on the
  custom_type string `"conformance-card"` — it is safe metadata.

- [ ] **Step 3: Run the golden gate**

Run: `uv run python scripts/parity_checks/extension_conformance_gate.py --json`
Expected: JSON includes `message_renderer_component` among present markers; no-leak checks pass; exit 0.

- [ ] **Step 4: Commit**

```bash
git add docs/examples/extensions/pipy-extension-conformance.py scripts/parity_checks/extension_conformance_gate.py
git commit -m "test(extension-api): golden message_renderer_component marker (slice C)"
```

---

### Task 7: Real-PTY color test + docs + full check

**Files:**
- Test: `tests/test_native_tool_loop_tui.py` (real-PTY, if the module has PTY tests; otherwise the nearest real-PTY test module used by slices 17/18).
- Modify: `docs/extension-api.md` (slice 16 entry / rich-renderer follow-on), `CHANGELOG.md`, `docs/parity-plan.md`, `docs/pi-mono-gap-audit.md`.

- [ ] **Step 1: Write a real-PTY test asserting visible color on the styled path**

Mirror the slice-17/18 real-PTY harness (search the test suite for the helper that
drives `tool_loop_session` over a pty at a fixed size). Drive a session that
activates the Task-4 example extension, dispatch the append command, and assert the
captured PTY frame contains the entry text wrapped in an SGR sequence:

```python
def test_rich_message_renderer_color_visible_over_pty():
    # The PTY helper drives the Task-4 extension whose component renders a known
    # body sentinel "PTYBODY" (NOT the custom_type, since the styled path injects
    # no label). Assert the body + SGR appear and no "[card]" label is drawn.
    frame = _run_pty_session_with_card(width=80)  # reuse slice-17/18 pty helper
    assert "\x1b[" in frame          # color visible on the styled path
    assert "PTYBODY" in frame        # the component body rendered live
    assert "[card]" not in frame     # no forced label on the styled path
```

- [ ] **Step 2: Run the PTY test**

Run: `uv run pytest tests/test_native_tool_loop_tui.py -k color_visible_over_pty -q`
Expected: PASS.

- [ ] **Step 3: Update docs**

- `docs/extension-api.md`: in the slice-16 entry (`~:1060-1078`), note the rich
  Component upgrade landed as slice C — renderer accepts `(data, ctx)` with a
  `MessageRenderContext` and may return a component (render-once snapshot at
  append width, themed, fail-soft); add a "Rich-UI item C — landed" line near the
  slice-17/18 entries; keep the deferred list (replay-on-resume, `send_message`,
  width-reactivity, live invalidate, `CustomMessageEntry` rendering).
- `CHANGELOG.md`: add an entry under the current unreleased section.
- `docs/parity-plan.md` and `docs/pi-mono-gap-audit.md`: move "rich message
  renderers (C)" from the remaining rich-UI follow-ons to landed; keep
  replay-on-resume and `send_message` as explicit deferrals.

- [ ] **Step 4: Run the full check**

Run: `just check`
Expected: green (tests + mypy + ruff). Then `just docs-build` clean.

- [ ] **Step 5: Commit**

```bash
git add tests/ docs/ CHANGELOG.md
git commit -m "docs(extension-api): land rich message renderers + PTY test (slice C)"
```

---

## Self-review

**Spec coverage:**
- Rich Component contract + `MessageRenderContext` → Tasks 1, 2.
- Arity back-compat (1-arg plain; 2-arg ctx) → Task 2.
- Judgment ① (text plain, color only via Component) → Task 2 dispatch (`styled` only on component path) + Task 3 (`add_custom_entry_styled` vs `add_custom_entry`).
- Judgment ② (no forced label on styled path) → Task 3 (`add_custom_entry_styled` injects no `[label]`).
- Render-once at append width; TTY vs captured (width 80 / expanded False / plain) → Task 4.
- Fail-soft (renderer/`render()` raise, uncoercible) → Task 2 + gate Task 5.
- Privacy (detached copy, no archive leak) → Task 2 (`_copy_custom_entry_data`), Tasks 4 & 6 assertions.
- `/reload` refresh preserved → unchanged `tool_loop_session.py:2299` (no task needed; noted).
- Reuse slice-17 machinery, no parallel module → Tasks 1–4 import from `extension_runtime`/`tool_renderers`.
- Conformance gate + golden marker + real-PTY → Tasks 5, 6, 7.
- Deferred items documented → Task 7 docs.

**Placeholder scan:** Task 4 Step 1 and Task 7 Step 1 intentionally point the implementer at the nearest existing harness to copy (product-path and real-PTY scaffolding are large and module-specific); all *new* logic (dispatch, types, TUI kind, wiring block, gate checks) is given as complete code. No "TODO"/"add error handling"/"similar to Task N" placeholders elsewhere.

**Type consistency:** `RenderedCustomEntry(lines, styled)`, `MessageRenderContext(custom_type, data, expanded, width, theme)`, `render_extension_message(..., *, width, expanded, theme) -> RenderedCustomEntry`, `add_custom_entry_styled(lines)`, line-kind `"custom_message_custom"`, and `_renderer_wants_context` are used identically across Tasks 1–7.
