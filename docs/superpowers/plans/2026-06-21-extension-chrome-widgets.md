# Extension Chrome Widgets (Slice B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Pi's five persistent-chrome extension APIs (`set_widget`, `set_header`, `set_footer`, `set_title`, `set_working_indicator`) to pipy's extension platform, using a width-reactive snapshot rendering model.

**Architecture:** Mirror the slice-15/16/17 seams. Contract types live in `extension_runtime.py` next to the slice-17 `ToolRender*` types and are re-exported from `pipy_harness.extensions`; the fail-soft render helper + bounds live in `tool_renderers.py` next to `render_tool_phase` (reusing `coerce_tool_render_lines`). `ToolLoopTerminalUi` gains `_ChromeRegion` state + setters + region renderers, woven into the existing `_live_region_lines`/`_frame_lines` assembly under a new `chrome_custom` line-kind. `_LiveExtensionUiDriver` delegates to the TUI; `_CollectingUi` records for non-TTY. Privacy/fail-soft/no-archive-leak invariants match slices 16/17.

**Tech Stack:** Python 3 (stdlib only), pytest, real-PTY harness used by the existing TUI tests, the `scripts/parity_checks/*_conformance.py` gate pattern.

**Spec:** `docs/superpowers/specs/2026-06-21-extension-chrome-widgets-design.md` (Pi-reviewed CLEAN).

**Module-layout note (refinement of spec §4):** the spec sketched a new `native/chrome_widgets.py`; this plan instead folds the contract types into `extension_runtime.py` and the render helper into `tool_renderers.py` to match the exact slice-17 precedent and avoid an import cycle (`tool_renderers` already imports from `extension_runtime`). Module layout is not a spec contract.

---

## File Structure

- **Modify** `src/pipy_harness/native/extension_runtime.py` — add `WidgetPlacement`, `ChromeComponent`, `FooterData`, factory aliases; extend `ExtensionUi` + `ExtensionUiDriver`; extend `_CollectingUi`.
- **Modify** `src/pipy_harness/native/tool_renderers.py` — add `render_chrome_component(...)` + bounds constants (reuse `coerce_tool_render_lines`).
- **Modify** `src/pipy_harness/native/tui.py` — `_ChromeRegion`, chrome state fields, `set_extension_*` setters, region renderers, `chrome_custom` styling, frame integration, resize re-render, OSC title, clear-all helper.
- **Modify** `src/pipy_harness/native/tool_loop_session.py` — extend `_LiveExtensionUiDriver`; `FooterData` snapshot; spinner override; reload/shutdown clearing + title restore.
- **Modify** `src/pipy_harness/extensions.py` — re-export new names.
- **Modify** `docs/examples/extensions/pipy-extension-conformance.py` — exercise the 5 APIs + write 5 markers.
- **Modify** `scripts/parity_checks/extension_conformance_gate.py` — add 5 markers to `_REQUIRED`.
- **Create** `scripts/parity_checks/extension_chrome_widgets_conformance.py` — unit gate.
- **Create** `docs/examples/extensions/chrome-widgets-demo.py` — focused example.
- **Create** tests: `tests/test_native_extension_chrome_contract.py`, `tests/test_native_extension_chrome_collecting.py`, `tests/test_native_tui_chrome_widgets.py`, `tests/test_native_tui_chrome_pty.py`, `tests/test_native_extension_chrome_driver.py`, `tests/test_native_extension_chrome_session.py`.
- **Modify** docs: `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, `docs/parity-plan.md`, `CHANGELOG.md`.

---

## Task 1: Chrome contract types + render helper

**Files:**
- Modify: `src/pipy_harness/native/extension_runtime.py` (add types near `ToolRenderContext`, ~736)
- Modify: `src/pipy_harness/native/tool_renderers.py` (add helper near `render_tool_phase`)
- Modify: `src/pipy_harness/extensions.py` (re-export)
- Test: `tests/test_native_extension_chrome_contract.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_native_extension_chrome_contract.py
from pipy_harness.extensions import (
    ChromeComponent,
    FooterData,
    WidgetPlacement,
    lines_component,
)
from pipy_harness.native.tool_renderers import render_chrome_component


def test_footer_data_snapshot_is_readonly_mapping():
    fd = FooterData(git_branch="main", extension_statuses={"k": "v"})
    assert fd.git_branch == "main"
    assert fd.extension_statuses["k"] == "v"


def test_render_chrome_component_lines_source():
    # A bare list of lines renders verbatim, bounded by max lines.
    out = render_chrome_component(["a", "b"], width=40, max_lines=8)
    assert out == ["a", "b"]


def test_render_chrome_component_str_not_char_per_line():
    out = render_chrome_component("hello", width=40, max_lines=8)
    assert out == ["hello"]


def test_render_chrome_component_factory_receives_width():
    class _Comp:
        def render(self, width):
            return [f"w={width}"]

    out = render_chrome_component(lambda: _Comp(), width=37, max_lines=8)
    assert out == ["w=37"]


def test_render_chrome_component_failsoft_returns_none():
    def boom():
        raise RuntimeError("x")

    assert render_chrome_component(boom, width=40, max_lines=8) is None


def test_render_chrome_component_direct_component_object():
    # A bare ChromeComponent (what lines_component returns) renders, not clears.
    out = render_chrome_component(lines_component(["x", "y"]), width=40, max_lines=8)
    assert out == ["x", "y"]


def test_render_chrome_component_truncates_to_max_lines():
    out = render_chrome_component([f"l{i}" for i in range(20)], width=40, max_lines=3)
    assert len(out) == 4  # 3 lines + a truncation marker
    assert "truncated" in out[-1]


def test_lines_component_is_a_chrome_component():
    # lines_component output structurally satisfies ChromeComponent (render only).
    comp = lines_component(["x"])
    assert isinstance(comp, ChromeComponent)


def test_widget_placement_values():
    assert set(WidgetPlacement.__args__) == {"above_editor", "below_editor"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_native_extension_chrome_contract.py -q`
Expected: FAIL with `ImportError: cannot import name 'ChromeComponent'`.

- [ ] **Step 3: Add the contract types to `extension_runtime.py`**

Insert after the `ToolRenderContext` dataclass (currently ends ~754), before `coerce_tool_render_lines`:

```python
WidgetPlacement = Literal["above_editor", "below_editor"]


@runtime_checkable
class ChromeComponent(Protocol):
    """A width-reactive snapshot chrome component.

    Only ``render(width)`` is required (so ``lines_component`` output satisfies
    it structurally). ``invalidate()`` and ``dispose()`` are OPTIONAL and
    duck-typed — called if present: ``invalidate()`` before a re-render on
    resize, ``dispose()`` when the component is replaced/cleared/reloaded or on
    shutdown. Per-frame repaint and requestRender-driven animation are reserved
    for the later live slice and never invoked here."""

    def render(self, width: int) -> Sequence[str]: ...


@dataclass(frozen=True, slots=True)
class FooterData:
    """Read-only snapshot handed to a footer factory (Pi's
    ReadonlyFooterDataProvider, minus the deferred onBranchChange reactivity).

    ``extension_statuses`` is copied into a read-only proxy so a caller-passed
    ``dict`` cannot be mutated through the snapshot."""

    git_branch: str | None
    extension_statuses: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "extension_statuses",
            MappingProxyType(dict(self.extension_statuses)),
        )
```

`Literal`, `Protocol`, `runtime_checkable`, `dataclass`, `Mapping`, and `Sequence` are already imported at the top of the module (verified: lines 35-44). Add `from types import MappingProxyType` to the module imports (it is not yet imported).

- [ ] **Step 4: Add `render_chrome_component` to `tool_renderers.py`**

Add a module constant + function after `render_tool_phase` (the file currently imports `coerce_tool_render_lines` from `extension_runtime`):

```python
_CHROME_TRUNCATION_MARKER = "  … (chrome truncated)"


def render_chrome_component(
    source: object,
    *,
    width: int,
    max_lines: int,
) -> list[str] | None:
    """Render a chrome source (lines, str, or zero-arg factory) fail-soft.

    Coercion order mirrors ``coerce_tool_render_lines`` (str special-cased
    before the generic Sequence path). ``source`` may be:
      * a callable factory taking no args and returning a component with
        ``render(width) -> Sequence[str]``;
      * a bare ``str`` (split on newlines) or any other ``Sequence[str]``.
    Returns the bounded lines, or ``None`` to signal the caller to fall back
    (clear the region / use the built-in). KeyboardInterrupt/SystemExit
    propagate."""

    component: object | None = None
    if callable(source):
        try:
            component = source()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - a bad factory falls back
            return None
    elif not isinstance(source, (str, bytes, bytearray)) and callable(
        getattr(source, "render", None)
    ):
        # A direct ChromeComponent object (e.g. lines_component(...)).
        component = source
    if component is not None:
        render = getattr(component, "render", None)
        if not callable(render):
            return None
        try:
            produced = render(width)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - a bad render() falls back
            return None
    else:
        produced = source
    coerced = coerce_tool_render_lines(produced)
    if coerced is None:
        return None
    lines = list(coerced)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines.append(_CHROME_TRUNCATION_MARKER)
    return lines
```

- [ ] **Step 5: Re-export from `extensions.py`**

In `src/pipy_harness/extensions.py`, add to the `from pipy_harness.native.extension_runtime import (...)` block: `ChromeComponent,`, `FooterData,`, `WidgetPlacement,`. Add `"ChromeComponent",`, `"FooterData",`, `"WidgetPlacement",` to `__all__`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_native_extension_chrome_contract.py -q`
Expected: PASS (8 passed).

- [ ] **Step 7: Lint + typecheck**

Run: `uv run ruff check src/pipy_harness/native/extension_runtime.py src/pipy_harness/native/tool_renderers.py src/pipy_harness/extensions.py && uv run mypy src/pipy_harness/native/tool_renderers.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add src/pipy_harness/native/extension_runtime.py src/pipy_harness/native/tool_renderers.py src/pipy_harness/extensions.py tests/test_native_extension_chrome_contract.py
git commit -m "feat(extension-api): chrome-widget contract types + render helper (slice B)"
```

---

## Task 2: Non-TTY recording in `_CollectingUi` + protocol methods

**Files:**
- Modify: `src/pipy_harness/native/extension_runtime.py` (`ExtensionUi` ~815, `ExtensionUiDriver` ~798, `_CollectingUi` ~943)
- Test: `tests/test_native_extension_chrome_collecting.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_native_extension_chrome_collecting.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_native_extension_chrome_collecting.py -q`
Expected: FAIL with `AttributeError: '_CollectingUi' object has no attribute 'widgets'`.

- [ ] **Step 3: Extend the `ExtensionUiDriver` protocol**

Append to `ExtensionUiDriver` (after `set_working_visible`, ~813):

```python
    def set_widget(
        self, key: str, content: object, placement: str
    ) -> None: ...

    def set_header(self, factory: object | None) -> None: ...

    def set_footer(self, factory: object | None) -> None: ...

    def set_title(self, title: str) -> None: ...

    def set_working_indicator(
        self, frames: Sequence[str] | None, interval_ms: int | None
    ) -> None: ...
```

- [ ] **Step 4: Extend the `ExtensionUi` protocol**

Append to `ExtensionUi` (after `custom`, ~843):

```python
    def set_widget(
        self,
        key: str,
        content: object,
        *,
        placement: WidgetPlacement = "above_editor",
    ) -> None: ...

    def set_header(self, factory: object | None) -> None: ...

    def set_footer(self, factory: object | None) -> None: ...

    def set_title(self, title: str) -> None: ...

    def set_working_indicator(
        self,
        frames: Sequence[str] | None = None,
        *,
        interval_ms: int | None = None,
    ) -> None: ...
```

- [ ] **Step 5: Extend `_CollectingUi` state + methods**

In `_CollectingUi.__init__`, after `self.working_visible = True`:

```python
        self.widgets: dict[str, tuple[object, str]] = {}
        self.header: object | None = None
        self.footer: object | None = None
        self.title: str | None = None
        self.indicator: tuple[Sequence[str] | None, int | None] | None = None
```

Add the methods (mirror the existing `set_status` fail-soft delegation pattern):

```python
    def set_widget(
        self,
        key: str,
        content: object,
        *,
        placement: WidgetPlacement = "above_editor",
    ) -> None:
        safe_key = _safe_ui_key(key)
        if safe_key is None:
            return
        place = placement if placement in ("above_editor", "below_editor") else "above_editor"
        if content is None:
            self.widgets.pop(safe_key, None)
        else:
            self.widgets[safe_key] = (content, place)
        if self._ui_driver is not None and self.has_ui:
            try:
                self._ui_driver.set_widget(safe_key, content, place)
            except Exception:  # noqa: BLE001 - a UI driver must not break the handler
                pass

    def set_header(self, factory: object | None) -> None:
        self.header = factory
        if self._ui_driver is not None and self.has_ui:
            try:
                self._ui_driver.set_header(factory)
            except Exception:  # noqa: BLE001
                pass

    def set_footer(self, factory: object | None) -> None:
        self.footer = factory
        if self._ui_driver is not None and self.has_ui:
            try:
                self._ui_driver.set_footer(factory)
            except Exception:  # noqa: BLE001
                pass

    def set_title(self, title: str) -> None:
        self.title = str(title)
        if self._ui_driver is not None and self.has_ui:
            try:
                self._ui_driver.set_title(self.title)
            except Exception:  # noqa: BLE001
                pass

    def set_working_indicator(
        self,
        frames: Sequence[str] | None = None,
        *,
        interval_ms: int | None = None,
    ) -> None:
        safe_frames = None if frames is None else [str(f) for f in frames]
        self.indicator = (safe_frames, interval_ms)
        if self._ui_driver is not None and self.has_ui:
            try:
                self._ui_driver.set_working_indicator(safe_frames, interval_ms)
            except Exception:  # noqa: BLE001
                pass
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_native_extension_chrome_collecting.py -q`
Expected: PASS (5 passed).

- [ ] **Step 7: Lint**

Run: `uv run ruff check src/pipy_harness/native/extension_runtime.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add src/pipy_harness/native/extension_runtime.py tests/test_native_extension_chrome_collecting.py
git commit -m "feat(extension-api): record chrome APIs in _CollectingUi + protocols (slice B)"
```

---

## Task 3: TUI chrome state, setters, snapshot render, dispose

**Files:**
- Modify: `src/pipy_harness/native/tui.py` (`ToolLoopTerminalUi` fields ~435; new `_ChromeRegion`; setters near `set_extension_status` ~1573)
- Test: `tests/test_native_tui_chrome_widgets.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_native_tui_chrome_widgets.py
import io

from pipy_harness.native.tui import ToolLoopTerminalUi, _ChromeRegion
from pathlib import Path


def _ui():
    return ToolLoopTerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=Path("."),
    )


def test_set_widget_stores_snapshot_and_clears():
    ui = _ui()
    ui.set_extension_widget("k", ["a", "b"], placement="above_editor")
    region = ui.extension_widgets_above["k"]
    assert isinstance(region, _ChromeRegion)
    assert region.snapshot == ("a", "b")
    ui.set_extension_widget("k", None)
    assert "k" not in ui.extension_widgets_above


def test_widget_insertion_order_preserved():
    ui = _ui()
    ui.set_extension_widget("z", ["z"])
    ui.set_extension_widget("a", ["a"])
    assert list(ui.extension_widgets_above.keys()) == ["z", "a"]


def test_widget_factory_renders_at_width():
    ui = _ui()

    class _Comp:
        def render(self, width):
            return [f"w={width}"]

    ui.set_extension_widget("k", lambda theme: _Comp())
    # snapshot rendered at the UI's current width
    assert ui.extension_widgets_above["k"].snapshot[0].startswith("w=")


def test_header_failsoft_drops_on_bad_factory():
    ui = _ui()

    def boom(theme):
        raise RuntimeError("x")

    ui.set_extension_header(boom)
    assert ui.extension_header is None  # fell back to built-in


def test_footer_replace_and_restore():
    ui = _ui()
    ui.set_extension_footer(lambda theme, footer_data: type("C", (), {"render": lambda self, w: ["f"]})())
    assert ui.extension_footer is not None
    ui.set_extension_footer(None)
    assert ui.extension_footer is None


def test_widget_bounds_truncate():
    ui = _ui()
    ui.set_extension_widget("k", [f"l{i}" for i in range(50)])
    assert len(ui.extension_widgets_above["k"].snapshot) <= 11  # 10 + marker


def test_dispose_called_on_replace_and_clear():
    ui = _ui()
    disposed = []

    class _Comp:
        def render(self, width):
            return ["x"]

        def dispose(self):
            disposed.append(True)

    ui.set_extension_widget("k", lambda theme: _Comp())
    ui.set_extension_widget("k", ["plain"])  # replace -> dispose old
    ui.set_extension_widget("k", None)       # clear
    assert disposed == [True]


def test_clear_extension_chrome_resets_all():
    ui = _ui()
    ui.set_extension_widget("k", ["a"])
    ui.set_extension_header(lambda theme: type("C", (), {"render": lambda self, w: ["h"]})())
    ui.set_extension_title("t")
    ui.clear_extension_chrome()
    assert ui.extension_widgets_above == {}
    assert ui.extension_header is None
    assert ui.extension_title is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_native_tui_chrome_widgets.py -q`
Expected: FAIL with `ImportError: cannot import name '_ChromeRegion'`.

- [ ] **Step 3: Add `_ChromeRegion` + a render helper near the top of `tui.py`**

After `_FrameLine` (~203), add (and add `FooterData` to `tui.py`'s import from `pipy_harness.native.extension_runtime` — `tui` already depends on `extension_runtime` transitively via `tool_renderers`, and `extension_runtime` does not import `tui`, so there is no cycle):

```python
from pipy_harness.native.extension_runtime import FooterData
from pipy_harness.native.tool_renderers import (
    build_tool_render_theme,
    render_chrome_component,
)

_WIDGET_MAX_LINES = 10
_WIDGET_MAX_COUNT = 16
_HEADER_MAX_LINES = 8
_FOOTER_MAX_LINES = 4
_TITLE_MAX_CHARS = 256
_INDICATOR_MAX_FRAMES = 32
_MIN_INPUT_ROWS = 1  # the input region is never starved below this


@dataclass(slots=True)
class _ChromeRegion:
    """A stored chrome source + its last rendered snapshot.

    ``source`` is a zero-arg factory (already bound to the theme / footer_data)
    or a pre-coerced lines source. ``component`` is the built component for a
    factory source (created once), used to call ``dispose()``. ``snapshot`` is
    the rendered lines; ``width`` is the width they were rendered at."""

    source: object
    component: object | None
    snapshot: tuple[str, ...]
    width: int
    is_factory: bool
```

(`dataclass` and `Any` are already imported. `build_tool_render_theme`/`render_chrome_component` import is safe: `tool_renderers` does not import `tui`.)

- [ ] **Step 4: Add chrome state fields to `ToolLoopTerminalUi`**

After `extension_status: dict[str, str] = field(default_factory=dict)` (~459):

```python
    extension_widgets_above: dict[str, "_ChromeRegion"] = field(default_factory=dict)
    extension_widgets_below: dict[str, "_ChromeRegion"] = field(default_factory=dict)
    extension_header: "_ChromeRegion | None" = None
    extension_footer: "_ChromeRegion | None" = None
    extension_title: str | None = None
    _extension_title_pushed: bool = False
    extension_indicator_frames: tuple[str, ...] | None = None
    extension_indicator_interval_ms: float | None = None
```

- [ ] **Step 5: Add the setters + helpers near `set_extension_status` (~1603)**

```python
    def _chrome_theme(self) -> object:
        return build_tool_render_theme(chrome_style_for(self.terminal_stream))

    def _build_region(
        self, source: object, *, footer_data: object | None, max_lines: int
    ) -> "_ChromeRegion | None":
        """Build a region by rendering ``source`` once at the current width.

        A callable ``source`` is a factory (built once); a bare component object
        (callable ``render``) is retained directly. BOTH are reactive — their
        ``render(width)`` is re-called on resize and their optional
        ``invalidate()``/``dispose()`` run on resize/replace/clear. A
        ``str``/``Sequence[str]`` source is static."""
        width, _height = self._dimensions()
        component: object | None = None
        is_factory = False
        render_source: object = source
        if callable(source) and not isinstance(source, (str, bytes, bytearray)):
            theme = self._chrome_theme()
            try:
                component = (
                    source(theme, footer_data)
                    if footer_data is not None
                    else source(theme)
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001 - a bad factory falls back
                return None
            is_factory = True
            render_source = lambda: component  # noqa: E731
        elif not isinstance(source, (str, bytes, bytearray)) and callable(
            getattr(source, "render", None)
        ):
            # A bare ChromeComponent object: reactive + lifecycle-managed.
            component = source
            is_factory = True
            render_source = lambda: component  # noqa: E731
        lines = render_chrome_component(render_source, width=width, max_lines=max_lines)
        if lines is None:
            return None
        return _ChromeRegion(
            source=source,
            component=component,
            snapshot=tuple(lines),
            width=width,
            is_factory=is_factory,
        )

    @staticmethod
    def _dispose_region(region: "_ChromeRegion | None") -> None:
        if region is None or region.component is None:
            return
        dispose = getattr(region.component, "dispose", None)
        if callable(dispose):
            try:
                dispose()
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001 - dispose must not break paint
                pass

    def set_extension_widget(
        self, key: str, content: object, *, placement: str = "above_editor"
    ) -> None:
        safe_key = _safe_extension_status_key(key)
        if safe_key is None:
            return
        target = (
            self.extension_widgets_below
            if placement == "below_editor"
            else self.extension_widgets_above
        )
        other = (
            self.extension_widgets_above
            if placement == "below_editor"
            else self.extension_widgets_below
        )
        with self._paint_lock:
            self._dispose_region(target.get(safe_key))
            self._dispose_region(other.pop(safe_key, None))
            if content is None:
                target.pop(safe_key, None)
            else:
                if safe_key not in target and len(target) >= _WIDGET_MAX_COUNT:
                    return
                region = self._build_region(
                    content, footer_data=None, max_lines=_WIDGET_MAX_LINES
                )
                if region is None:
                    target.pop(safe_key, None)
                else:
                    target[safe_key] = region
        self.paint()

    def set_extension_header(self, factory: object | None) -> None:
        with self._paint_lock:
            self._dispose_region(self.extension_header)
            if factory is None:
                self.extension_header = None
            else:
                self.extension_header = self._build_region(
                    factory, footer_data=None, max_lines=_HEADER_MAX_LINES
                )
        self.paint()

    def set_extension_footer(
        self, factory: object | None, footer_data: object | None = None
    ) -> None:
        with self._paint_lock:
            self._dispose_region(self.extension_footer)
            if factory is None:
                self.extension_footer = None
            else:
                # A footer factory is always two-arg (theme, footer_data). When
                # no snapshot is supplied (direct/test callers), synthesize a
                # default from the slice-15 status map so the factory never sees
                # a missing second argument.
                fd = (
                    footer_data
                    if footer_data is not None
                    else FooterData(
                        git_branch=None,
                        extension_statuses=dict(self.extension_status),
                    )
                )
                self.extension_footer = self._build_region(
                    factory, footer_data=fd, max_lines=_FOOTER_MAX_LINES
                )
        self.paint()

    def set_extension_title(self, title: str | None) -> None:
        with self._paint_lock:
            if title is None:
                self.extension_title = None
                self._restore_terminal_title()
            else:
                # Save the pre-extension title once (xterm title stack), then set.
                if not self._extension_title_pushed:
                    self._push_terminal_title()
                self.extension_title = sanitize_label_text(str(title))[:_TITLE_MAX_CHARS]
                self._write_terminal_title(self.extension_title)
        # title is OS-level; no frame repaint needed.

    def set_extension_working_indicator(
        self, frames: object, interval_ms: object
    ) -> None:
        with self._paint_lock:
            if frames is None:
                self.extension_indicator_frames = None
            else:
                cleaned = tuple(
                    sanitize_label_text(str(f)) for f in list(frames)[:_INDICATOR_MAX_FRAMES]
                )
                self.extension_indicator_frames = cleaned
            try:
                self.extension_indicator_interval_ms = (
                    None if interval_ms is None else max(10.0, float(interval_ms))
                )
            except (TypeError, ValueError):
                self.extension_indicator_interval_ms = None
        self.paint()

    def clear_extension_chrome(self) -> None:
        """Dispose + drop all extension-owned chrome (used on /reload + shutdown)."""
        with self._paint_lock:
            for region in (
                *self.extension_widgets_above.values(),
                *self.extension_widgets_below.values(),
                self.extension_header,
                self.extension_footer,
            ):
                self._dispose_region(region)
            self.extension_widgets_above.clear()
            self.extension_widgets_below.clear()
            self.extension_header = None
            self.extension_footer = None
            self.extension_title = None
            self.extension_indicator_frames = None
            self.extension_indicator_interval_ms = None
            # Best-effort restore of the pre-extension title (xterm title stack).
            self._restore_terminal_title()
        self.paint()

    def _write_terminal_title(self, title: str) -> None:
        """Write an OSC 0 title sequence to a TTY; no-op for non-TTY streams."""
        if not bool(getattr(self.terminal_stream, "isatty", lambda: False)()):
            return
        safe = sanitize_label_text(title).replace("\x07", "")[:_TITLE_MAX_CHARS]
        try:
            self.terminal_stream.write(f"\x1b]0;{safe}\x07")
            self.terminal_stream.flush()
        except (OSError, ValueError):
            return

    def _push_terminal_title(self) -> None:
        """Save the current terminal title on the xterm title stack (OSC 22)."""
        if not bool(getattr(self.terminal_stream, "isatty", lambda: False)()):
            return
        try:
            self.terminal_stream.write("\x1b[22;2t")
            self.terminal_stream.flush()
        except (OSError, ValueError):
            return
        self._extension_title_pushed = True

    def _restore_terminal_title(self) -> None:
        """Restore the saved title from the xterm title stack (OSC 23).

        Best-effort: this pops the title saved by ``_push_terminal_title`` so the
        pre-extension title returns (not a blank title). Only acts when a save
        was pushed; terminals that ignore the title stack simply keep the last
        title set."""
        if not self._extension_title_pushed:
            return
        self._extension_title_pushed = False
        if not bool(getattr(self.terminal_stream, "isatty", lambda: False)()):
            return
        try:
            self.terminal_stream.write("\x1b[23;2t")
            self.terminal_stream.flush()
        except (OSError, ValueError):
            return
```

(`chrome_style_for` and `sanitize_label_text` are already imported in `tui.py`.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_native_tui_chrome_widgets.py -q`
Expected: PASS (8 passed).

- [ ] **Step 7: Lint + typecheck**

Run: `uv run ruff check src/pipy_harness/native/tui.py && uv run mypy src/pipy_harness/native/tui.py`
Expected: no errors (resolve any `Any`/Protocol typing with `object` annotations as above).

- [ ] **Step 8: Commit**

```bash
git add src/pipy_harness/native/tui.py tests/test_native_tui_chrome_widgets.py
git commit -m "feat(extension-api): TUI chrome state, setters, snapshot render, dispose (slice B)"
```

---

## Task 4: Frame integration, region renderers, resize re-render, styling

**Files:**
- Modify: `src/pipy_harness/native/tui.py` (`_styled_line` ~2899; `_live_region_lines` ~2522; `_frame_lines` ~2238; new region renderers near `_extension_status_lines` ~2784)
- Test: `tests/test_native_tui_chrome_widgets.py` (extend), `tests/test_native_tui_chrome_pty.py`

- [ ] **Step 1: Write the failing test (frame composition, in-process)**

```python
# add to tests/test_native_tui_chrome_widgets.py

def _frame_text(ui, width=60, height=24):
    return [fl.text for fl in ui._frame_lines(width=width, height=height, pad=False)]


def test_header_renders_above_pending_and_input():
    ui = _ui()
    ui.set_extension_header(lambda theme: type("C", (), {"render": lambda self, w: ["HEADER_ROW"]})())
    text = "\n".join(_frame_text(ui))
    assert "HEADER_ROW" in text


def test_above_widget_renders_in_frame():
    ui = _ui()
    ui.set_extension_widget("k", ["ABOVE_ROW"], placement="above_editor")
    assert any("ABOVE_ROW" in line for line in _frame_text(ui))


def test_below_widget_renders_in_frame():
    ui = _ui()
    ui.set_extension_widget("k", ["BELOW_ROW"], placement="below_editor")
    assert any("BELOW_ROW" in line for line in _frame_text(ui))


def test_footer_replaces_builtin_rows():
    ui = _ui()
    ui.footer_lines = ("builtin-a", "builtin-b")
    ui.set_extension_footer(lambda theme, fd: type("C", (), {"render": lambda self, w: ["EXT_FOOTER"]})())
    text = "\n".join(_frame_text(ui))
    assert "EXT_FOOTER" in text and "builtin-a" not in text


def test_factory_widget_rerenders_on_width_change():
    ui = _ui()

    class _Comp:
        def render(self, width):
            return [f"W{width}"]

    ui.set_extension_widget("k", lambda theme: _Comp())
    _frame_text(ui, width=40)
    assert any("W40" in line for line in _frame_text(ui, width=40))
    assert any("W70" in line for line in _frame_text(ui, width=70))


def test_tall_chrome_clamped_and_input_preserved():
    ui = _ui()
    for i in range(16):  # _WIDGET_MAX_COUNT widgets, each _WIDGET_MAX_LINES tall
        ui.set_extension_widget(
            f"w{i}", [f"r{i}-{j}" for j in range(10)], placement="above_editor"
        )
    frame = ui._frame_lines(width=60, height=24, pad=False)
    assert len(frame) <= 24                                  # fits the viewport
    assert any(fl.kind == "input" for fl in frame)           # input not starved
    assert any(fl.kind == "footer" for fl in frame)          # footer survives
    assert any("chrome clipped" in fl.text for fl in frame)  # truncation marker
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_native_tui_chrome_widgets.py -q -k "frame or widget_renders or footer_replaces or rerenders or header_renders"`
Expected: FAIL (chrome rows absent from the frame).

- [ ] **Step 3: Add region renderers + a re-render helper near `_extension_status_lines` (~2784)**

```python
    def _render_region_lines(
        self, region: "_ChromeRegion", *, width: int, max_lines: int
    ) -> tuple[str, ...] | None:
        """Return the region's snapshot lines (UNCLIPPED; the caller width-clips
        each line at frame-build time), or ``None`` when a factory re-render
        failed (the caller then drops the region — fail soft). A factory region
        re-renders when the width changes (component retained, not re-invoked); a
        static region keeps its original lines unchanged, so narrowing-then-
        widening is non-lossy."""
        if not region.is_factory:
            # Static lines never reflow — return the original, intact. Width
            # clipping is applied per-line by the _extension_*_lines caller, so
            # the stored snapshot is never overwritten with clipped text.
            return region.snapshot
        if region.width == width or region.component is None:
            return region.snapshot
        invalidate = getattr(region.component, "invalidate", None)
        if callable(invalidate):
            try:
                invalidate()
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:  # noqa: BLE001
                pass
        lines = render_chrome_component(
            lambda: region.component, width=width, max_lines=max_lines
        )
        if lines is None:
            # Signal a broken resize-render so the caller actually drops the
            # region (footer -> built-in, widget -> free the key). Returning an
            # empty snapshot would keep the region present and suppress the
            # built-in footer, so return None instead.
            return None
        region.snapshot = tuple(lines)
        region.width = width
        return region.snapshot

    def _clip_custom(self, text: str, width: int) -> str:
        cleaned = _sanitize_custom_overlay_text(text)
        if _visible_len_allow_sgr(cleaned) <= width:
            return cleaned
        return _clip_custom_overlay_text(cleaned, width)

    def _extension_header_lines(self, width: int) -> list[_FrameLine]:
        if self.extension_header is None:
            return []
        with self._paint_lock:
            lines = self._render_region_lines(
                self.extension_header, width=width, max_lines=_HEADER_MAX_LINES
            )
            if lines is None:
                # Fail soft: a broken header re-render drops the region.
                self._dispose_region(self.extension_header)
                self.extension_header = None
                return []
        return [_FrameLine(self._clip_custom(line, width), "chrome_custom") for line in lines]

    def _extension_widgets_lines(self, placement: str, width: int) -> list[_FrameLine]:
        regions = (
            self.extension_widgets_below
            if placement == "below_editor"
            else self.extension_widgets_above
        )
        if not regions:
            return []
        out: list[_FrameLine] = []
        failed: list[str] = []
        with self._paint_lock:
            for key, region in regions.items():  # insertion order
                lines = self._render_region_lines(
                    region, width=width, max_lines=_WIDGET_MAX_LINES
                )
                if lines is None:
                    # Fail soft: a broken widget re-render drops that key, freeing
                    # its count slot. Defer the pop until after iteration.
                    failed.append(key)
                    continue
                for line in lines:
                    out.append(_FrameLine(self._clip_custom(line, width), "chrome_custom"))
            for key in failed:
                self._dispose_region(regions.pop(key, None))
        return out

    def _extension_footer_lines(self, width: int) -> list[_FrameLine] | None:
        """Return custom footer rows, or None to fall back to the built-in footer."""
        if self.extension_footer is None:
            return None
        with self._paint_lock:
            lines = self._render_region_lines(
                self.extension_footer, width=width, max_lines=_FOOTER_MAX_LINES
            )
            if lines is None:
                # Fail soft: a broken footer re-render drops the custom footer so
                # the built-in two rows render instead.
                self._dispose_region(self.extension_footer)
                self.extension_footer = None
                return None
        return [_FrameLine(self._clip_custom(line, width), "chrome_custom") for line in lines]

    def _clamp_chrome_lines(
        self,
        header: list[_FrameLine],
        above: list[_FrameLine],
        below: list[_FrameLine],
        *,
        budget: int,
        width: int,
    ) -> tuple[list[_FrameLine], list[_FrameLine], list[_FrameLine]]:
        """Clip the combined extension-chrome rows (header + above + below) so the
        whole frame fits the viewport. Priority: header > above_editor >
        below_editor; the input region and footer are reserved by the caller and
        never clipped here. A truncation marker replaces the last kept row when
        anything is dropped."""
        budget = max(0, budget)
        if budget == 0:
            # No room for any chrome (incl. the marker) — drop it entirely so the
            # input/footer are never pushed off a tiny viewport.
            return [], [], []
        if len(header) + len(above) + len(below) <= budget:
            return header, above, below
        marker = _FrameLine(self._clip("  … (chrome clipped)", width), "slash_menu_scroll")
        keep = max(0, budget - 1)  # reserve one row for the marker
        out_header = header[:keep]
        keep -= len(out_header)
        out_above = above[:keep]
        keep -= len(out_above)
        out_below = below[:keep]
        # Append the marker to the last non-empty group (or as its own row when
        # everything was clipped to nothing).
        if out_below:
            out_below = out_below + [marker]
        elif out_above:
            out_above = out_above + [marker]
        elif out_header:
            out_header = out_header + [marker]
        else:
            out_header = [marker]
        return out_header, out_above, out_below
```

The contract under test (Step 7b): combined chrome ≤ `budget`, a marker present when clipped, and header retained in preference to widgets.

(`_render_region_lines` now returns `tuple[str, ...] | None`; update its annotation accordingly.)

(`_sanitize_custom_overlay_text`, `_visible_len_allow_sgr`, and `_clip_custom_overlay_text` are module-level functions already in `tui.py`.)

- [ ] **Step 4: Add the `chrome_custom` branch to `_styled_line`**

In `_styled_line`, alongside the existing `tool_call_custom`/`tool_result_custom` branch (~2897):

```python
        if line.kind == "chrome_custom":
            return style.tool_custom(line.text, width=width)
```

- [ ] **Step 5: Weave the regions into `_live_region_lines` and `_frame_lines`**

In **both** `_live_region_lines` (~2522) and the non-overlay path of `_frame_lines` (~2380+), compute the new regions and subtract their heights from the input budget, then insert them in Pi order (`header` at top of live region; `above_editor` after pending, before the top separator; `below_editor` after the bottom separator/menu; footer replaces the two built-in footer rows). Concretely, for `_live_region_lines` replace the body after `pending_lines`/`status_lines` are computed with:

```python
        header_lines = self._extension_header_lines(width)
        above_widgets = self._extension_widgets_lines("above_editor", width)
        below_widgets = self._extension_widgets_lines("below_editor", width)
        custom_footer = self._extension_footer_lines(width)
        footer_rows = (
            custom_footer
            if custom_footer is not None
            else [
                _FrameLine(self._clip(self.footer_lines[0], width), "footer"),
                _FrameLine(self._clip(self.footer_lines[1], width), "footer"),
            ]
        )
        # Cap the extension chrome so the whole frame fits the viewport — input
        # and footer are reserved and never clipped; header > above > below.
        chrome_reserved = (
            2  # input separators
            + len(menu_lines)
            + len(pending_lines)
            + len(status_lines)
            + len(footer_rows)
            + _MIN_INPUT_ROWS
            + 1  # one transient row
        )
        header_lines, above_widgets, below_widgets = self._clamp_chrome_lines(
            header_lines,
            above_widgets,
            below_widgets,
            budget=max(0, height - chrome_reserved),
            width=width,
        )
        input_lines = self._input_frame_lines(
            width,
            max_rows=max(
                1,
                height
                - len(menu_lines)
                - len(pending_lines)
                - len(status_lines)
                - len(header_lines)
                - len(above_widgets)
                - len(below_widgets)
                - max(0, len(footer_rows) - 2)
                - 4,
            ),
        )
        chrome_height = (
            len(input_lines)
            + 2
            + len(menu_lines)
            + len(pending_lines)
            + len(status_lines)
            + len(header_lines)
            + len(above_widgets)
            + len(below_widgets)
            + len(footer_rows)
        )
        transient_budget = max(0, height - chrome_height - 1)
        transient = self._transient_tail_lines(width)
        if len(transient) > transient_budget:
            transient = transient[len(transient) - transient_budget :]
        lines: list[_FrameLine] = [
            *transient,
            *header_lines,
            *pending_lines,
            *above_widgets,
            self._input_frame_separator(width, label=False),
            *input_lines,
            self._input_frame_separator(width, label=True),
            *menu_lines,
            *below_widgets,
            *status_lines,
            *footer_rows,
        ]
        return lines
```

> **Ordering (Pi parity + spec round-4 fix):** `header` → `pending` → `above_editor` → input separators/frame → **`menu_lines` (transient popup hugs the input)** → `below_editor` → status → footer. `below_editor` MUST come *after* `menu_lines`, so a visible slash/file popup stays attached to the input.

Apply the analogous insertion in `_frame_lines` (both the `menu_lines` and no-`menu_lines` branches): compute the same `header_lines`/`above_widgets`/`below_widgets`/`footer_rows`, **apply the same `_clamp_chrome_lines` clamp** (with the same `chrome_reserved` budget) BEFORE the budget math so the chrome can never push the input/footer past `height`, then add `header_lines` after `history_lines`, `above_widgets` after `pending_lines` (before `top_separator`), `below_widgets` after `*menu_lines` (or after `bottom_separator` when there is no menu), and replace the two trailing `_FrameLine(... "footer")` rows with `*footer_rows`; subtract `len(header_lines)+len(above_widgets)+len(below_widgets)+max(0,len(footer_rows)-2)` from both the `input_lines` `max_rows` and `max_history_lines`. (The clamp matters because `_frame_lines` ends with `frame[:height]`, which would otherwise drop the trailing footer/input rather than the chrome.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_native_tui_chrome_widgets.py -q`
Expected: PASS (14 passed).

- [ ] **Step 7: Write the real-PTY test**

```python
# tests/test_native_tui_chrome_pty.py
import pytest

from tests.support.pty_tui import run_tui_script  # existing PTY harness used by slice-17 tests


@pytest.mark.parametrize("size", [(80, 24), (100, 40)])
def test_chrome_regions_render_and_input_usable(size):
    cols, rows = size
    frame = run_tui_script(
        cols=cols,
        rows=rows,
        setup=lambda ui: (
            ui.set_extension_header(lambda theme: type("C", (), {"render": lambda self, w: ["HDR"]})()),
            ui.set_extension_widget("w", ["WIDGET"], placement="above_editor"),
            ui.set_extension_footer(lambda theme, fd: type("C", (), {"render": lambda self, w: ["FTR"]})()),
        ),
        keystrokes="hello",
    )
    assert "HDR" in frame and "WIDGET" in frame and "FTR" in frame
    assert "hello" in frame  # input still visible/usable under chrome
```

> Implementer note: match the exact helper name/signature the slice-17 PTY tests use (`tests/test_native_tui_*pty*` / `tests/support/`). If the harness takes a different shape, adapt this test to it — the assertions (chrome rows present, input still rendered) are the contract.

- [ ] **Step 8: Run the PTY test**

Run: `uv run pytest tests/test_native_tui_chrome_pty.py -q`
Expected: PASS (2 passed).

- [ ] **Step 9: Run the broader TUI suite for regressions**

Run: `uv run pytest tests/ -q -k "tui" `
Expected: PASS (no regressions in existing frame/budget tests).

- [ ] **Step 10: Commit**

```bash
git add src/pipy_harness/native/tui.py tests/test_native_tui_chrome_widgets.py tests/test_native_tui_chrome_pty.py
git commit -m "feat(extension-api): weave chrome regions into the TUI frame + resize (slice B)"
```

---

## Task 5: Working-indicator override (spinner) wiring

**Files:**
- Modify: `src/pipy_harness/native/tool_loop_session.py` (`_TuiToolLoopRenderer.show_working` ~6333)
- Test: `tests/test_native_tui_chrome_widgets.py` (extend with a spinner-frame unit)

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_native_tui_chrome_widgets.py
from pipy_harness.native.tool_loop_session import _TuiToolLoopRenderer


def test_indicator_frames_override_used_by_tui_renderer():
    ui = _ui()
    ui.set_extension_working_indicator(["★"], 50)
    renderer = _TuiToolLoopRenderer(ui=ui)
    frames, interval = renderer._effective_spinner()
    assert frames == ("★",) and interval == 0.05


def test_indicator_default_when_unset():
    ui = _ui()
    renderer = _TuiToolLoopRenderer(ui=ui)
    frames, interval = renderer._effective_spinner()
    assert frames == _TuiToolLoopRenderer._SPINNER_FRAMES
    assert interval == _TuiToolLoopRenderer._SPINNER_INTERVAL_SECONDS


def test_indicator_empty_frames_hides_glyph():
    ui = _ui()
    ui.set_extension_working_indicator([], None)
    renderer = _TuiToolLoopRenderer(ui=ui)
    frames, _interval = renderer._effective_spinner()
    assert frames == ("",)  # blank glyph -> hidden spinner
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_native_tui_chrome_widgets.py -q -k indicator`
Expected: FAIL with `AttributeError: ... has no attribute '_effective_spinner'`.

- [ ] **Step 3: Add `_effective_spinner` and use it in `show_working`**

In `_TuiToolLoopRenderer`, add:

```python
    def _effective_spinner(self) -> tuple[tuple[str, ...], float]:
        frames = self._ui.extension_indicator_frames
        interval = self._ui.extension_indicator_interval_ms
        if frames is None:
            eff_frames = self._SPINNER_FRAMES
        elif len(frames) == 0:
            eff_frames = ("",)  # hide the glyph, keep the message
        else:
            eff_frames = tuple(frames)
        eff_interval = (
            self._SPINNER_INTERVAL_SECONDS if interval is None else interval / 1000.0
        )
        return eff_frames, eff_interval
```

Then in `show_working`, replace the `_animate` body's frame/interval access:

```python
        def _animate() -> None:
            frames, interval = self._effective_spinner()
            frame_index = 0
            while not stop_event.is_set():
                glyph = frames[frame_index % len(frames)]
                message = self._ui.extension_working_message or "Working..."
                # An empty glyph hides the spinner: show the message with no
                # leading space/prefix.
                self._ui.set_working(message if glyph == "" else f"{glyph} {message}")
                frame_index += 1
                stop_event.wait(interval)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_native_tui_chrome_widgets.py -q -k indicator`
Expected: PASS (3 passed).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src/pipy_harness/native/tool_loop_session.py
git add src/pipy_harness/native/tool_loop_session.py tests/test_native_tui_chrome_widgets.py
git commit -m "feat(extension-api): extension working-indicator frame/interval override (slice B)"
```

---

## Task 6: Live driver wiring, FooterData snapshot, reload/shutdown clearing

**Files:**
- Modify: `src/pipy_harness/native/tool_loop_session.py` (lift `_LiveExtensionUiDriver` to module level; instantiation site ~1297; `/reload` block ~2197; `finally` ~3688; reuse `_detect_git_branch` ~530)
- Test: `tests/test_native_extension_chrome_driver.py` (direct driver unit test), `tests/test_native_extension_chrome_session.py` (no-leak smoke + PTY reload-clear)

> **Why a refactor:** today `_LiveExtensionUiDriver` is a class defined *inside* `run()`, closing over `terminal_ui`/`cwd`. A non-TTY session sets `terminal_ui = None`, so a `StringIO` session test never exercises the live driver, `FooterData`, title restore, or reload/shutdown clearing — it would pass trivially via `_CollectingUi`. Lifting the class to module level (`_LiveExtensionUiDriver(terminal_ui, cwd)`) makes the live path directly unit-testable with a fake `terminal_ui`.

- [ ] **Step 1: Write the failing driver unit test (genuinely exercises the live path)**

```python
# tests/test_native_extension_chrome_driver.py
import subprocess
from pathlib import Path

from pipy_harness.native.extension_runtime import FooterData
from pipy_harness.native.tool_loop_session import _LiveExtensionUiDriver


class _FakeUi:
    """Records the set_extension_* calls the driver delegates."""

    def __init__(self):
        self.extension_status = {"s": "v"}
        self.calls = []

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


def test_driver_footer_none_passes_none(tmp_path):
    ui = _FakeUi()
    driver = _LiveExtensionUiDriver(ui, tmp_path)
    driver.set_footer(None)
    assert ui.calls[-1] == ("footer", None, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_native_extension_chrome_driver.py -q`
Expected: FAIL with `ImportError: cannot import name '_LiveExtensionUiDriver'` (it is not yet module-level).

- [ ] **Step 3: Lift `_LiveExtensionUiDriver` to module level + add the 5 methods**

Replace the in-`run()` closure class with a module-level class (near the other renderer classes in `tool_loop_session.py`). Move the existing 6 methods verbatim, swapping the closure `terminal_ui` for `self._terminal_ui`, and add `self._cwd`:

```python
class _LiveExtensionUiDriver:
    """Live `ExtensionUiDriver` backed by the product TUI (one per session)."""

    def __init__(self, terminal_ui: "ToolLoopTerminalUi", cwd: Path) -> None:
        self._terminal_ui = terminal_ui
        self._cwd = cwd

    def select(self, title: str, options: Sequence[str]) -> str | None:
        return self._terminal_ui.run_extension_select(title, options)

    def input(self, title: str, placeholder: str | None = None) -> str | None:
        return self._terminal_ui.run_extension_input(title, placeholder)

    def confirm(self, title: str, message: str) -> bool:
        return self._terminal_ui.run_extension_confirm(title, message)

    def set_status(self, key: str, text: str | None) -> None:
        self._terminal_ui.set_extension_status(key, text)

    def set_working_message(self, message: str | None = None) -> None:
        self._terminal_ui.set_extension_working_message(message)

    def set_working_visible(self, visible: bool) -> None:
        self._terminal_ui.set_extension_working_visible(visible)

    def set_widget(self, key: str, content: object, placement: str) -> None:
        self._terminal_ui.set_extension_widget(key, content, placement=placement)

    def set_header(self, factory: object | None) -> None:
        self._terminal_ui.set_extension_header(factory)

    def set_footer(self, factory: object | None) -> None:
        footer_data = (
            None
            if factory is None
            else FooterData(
                git_branch=_detect_git_branch(self._cwd),
                extension_statuses=dict(self._terminal_ui.extension_status),
            )
        )
        self._terminal_ui.set_extension_footer(factory, footer_data)

    def set_title(self, title: str) -> None:
        self._terminal_ui.set_extension_title(title)

    def set_working_indicator(self, frames: object, interval_ms: object) -> None:
        self._terminal_ui.set_extension_working_indicator(frames, interval_ms)
```

The `terminal_ui is None` guards are no longer needed inside the methods — the instantiation site already skips the driver entirely when there is no TUI. Update that site (currently `extension_ui_driver = _LiveExtensionUiDriver() if terminal_ui is not None else None`):

```python
        extension_ui_driver = (
            _LiveExtensionUiDriver(terminal_ui, cwd) if terminal_ui is not None else None
        )
```

Add `FooterData` to the `from pipy_harness.native.extension_runtime import (...)` block at the top of `tool_loop_session.py` (it already imports `ExtensionTool`, `ToolResult`, etc.). `Path` and `Sequence` are already imported.

- [ ] **Step 4: Run the driver unit test**

Run: `uv run pytest tests/test_native_extension_chrome_driver.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Clear chrome on `/reload`**

In the `/reload` block (~2230, right before re-activating extensions), add:

```python
                    if terminal_ui is not None:
                        terminal_ui.clear_extension_chrome()
```

This disposes prior chrome so a removed/changed extension's chrome does not persist; the re-activated `session_start` hooks re-set what they want.

- [ ] **Step 6: Clear chrome + restore title on shutdown**

In the `finally` block (~3688):

```python
        finally:
            emitter.fire_lifecycle(EVENT_SESSION_SHUTDOWN)
            if terminal_ui is not None:
                terminal_ui.clear_extension_chrome()
```

(`clear_extension_chrome` also resets the title to `""`, restoring the terminal's default.)

- [ ] **Step 7: Write the no-leak smoke + PTY reload-clear tests**

```python
# tests/test_native_extension_chrome_session.py
import io
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.tool_loop_session import NativeToolReplSession, production_tool_registry


_EXT = '''
def activate(api):
    @api.on("session_start")
    def _s(event, ctx):
        ctx.ui.set_widget("demo", ["DEMO_WIDGET"])
        ctx.ui.set_title("demo-title")
'''


class _Provider:
    name = "stub"
    model_id = "m"

    @property
    def supports_tool_calls(self):
        return True

    def complete(self, request: ProviderRequest, **_k) -> ProviderResult:
        now = datetime(2026, 6, 21, tzinfo=UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED, provider_name=self.name, model_id=self.model_id,
            started_at=now, ended_at=now, final_text="ok", tool_calls=(),
        )


def test_chrome_calls_do_not_leak_to_archive(tmp_path, monkeypatch):
    # Non-TTY: terminal_ui is None, so chrome calls are absorbed by _CollectingUi.
    # This asserts that a real extension's chrome BODIES never reach the archive.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("PIPY_NATIVE_SESSIONS_ROOT", str(tmp_path / "sessions"))
    ws = tmp_path / "work"
    (ws / ".pipy" / "extensions").mkdir(parents=True)
    (ws / ".pipy" / "extensions" / "chrome-demo.py").write_text(_EXT, encoding="utf-8")

    session = NativeToolReplSession(
        provider=_Provider(), tool_registry=production_tool_registry(), tool_budget=3
    )
    result = session.run(
        workspace_root=ws,
        input_stream=io.StringIO("hi\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    assert result.status is HarnessStatus.SUCCEEDED
    blob = ""
    sroot = tmp_path / "sessions"
    if sroot.exists():
        for p in sroot.rglob("*"):
            if p.is_file():
                blob += p.read_text(encoding="utf-8", errors="replace")
    assert "DEMO_WIDGET" not in blob
    assert "demo-title" not in blob


def test_pty_session_renders_then_reload_clears_chrome(tmp_path):
    """A PTY-backed session builds a real terminal_ui, so the live driver +
    region rendering + reload-clear are exercised. Drive: start (chrome
    renders) -> delete the extension -> /reload (chrome clears)."""
    pytest.importorskip("pty")
    from tests.support.pty_session import run_pty_session  # existing PTY session harness

    ws = tmp_path / "work"
    extdir = ws / ".pipy" / "extensions"
    extdir.mkdir(parents=True)
    (extdir / "chrome-demo.py").write_text(_EXT, encoding="utf-8")

    frames = run_pty_session(
        workspace=ws,
        provider=_Provider(),
        # Drive the session: observe the first frame, then remove the extension
        # and /reload, then quit.
        script=[
            ("expect", "DEMO_WIDGET"),
            ("run", lambda: (extdir / "chrome-demo.py").unlink()),
            ("send", "/reload\n"),
            ("expect_absent", "DEMO_WIDGET"),
            ("send", "/quit\n"),
        ],
    )
    assert "DEMO_WIDGET" in frames[0]
    assert "DEMO_WIDGET" not in frames[-1]
```

> Implementer note: match the exact PTY session harness the project already uses for end-to-end TUI session tests (grep `tests/` for the helper that boots `NativeToolReplSession.run` over a real pty — e.g. the harness behind the slice-17 / cancellation real-PTY tests). Adapt the `run_pty_session` call to its real signature; the contract is: chrome renders on start, and is gone after `/reload` once the extension is removed. If no reusable session-over-pty harness exists, build the assertion on `_frame_lines` after manually driving a `terminal_ui` whose `terminal_stream.isatty()` is forced `True` (a `_Tty(StringIO)` subclass) and calling `clear_extension_chrome()` directly to prove reload-clear.

- [ ] **Step 8: Run the session tests**

Run: `uv run pytest tests/test_native_extension_chrome_session.py -q`
Expected: PASS (the no-leak smoke always; the PTY test passes or skips if the harness is unavailable — do not leave it as a silent xfail, wire it to the real harness).

- [ ] **Step 9: Lint + typecheck + commit**

```bash
uv run ruff check src/pipy_harness/native/tool_loop_session.py && uv run mypy src/pipy_harness/native/tool_loop_session.py
git add src/pipy_harness/native/tool_loop_session.py tests/test_native_extension_chrome_driver.py tests/test_native_extension_chrome_session.py
git commit -m "feat(extension-api): module-level live chrome driver + FooterData + reload/shutdown clear (slice B)"
```

---

## Task 6b: Thread the live ui_driver into lifecycle-hook dispatch

**Discovered during Task 6 (added 2026-06-21).** Lifecycle hooks (`session_start`,
`agent_start`, …) build a `_CollectingUi` WITHOUT a `ui_driver`, so chrome set from
a `session_start` hook records but never reaches the live TUI — only `/command`
and shortcut dispatch thread the driver. This wires the live driver into lifecycle
dispatch so the headline use case (pin chrome on session start) renders live.
**Scope: lifecycle hooks only**; threading the driver into the other event hooks
(`tool_call`/`tool_result`/`input`/`user_bash`/`before_*`) is a documented
follow-on.

**Files:**
- Modify: `src/pipy_harness/native/extension_runtime.py` (`dispatch_lifecycle_hooks` ~2415)
- Modify: `src/pipy_harness/native/tool_loop_session.py` (`_ExtensionAwareEmitter` ~906; emitter construction ~1391)
- Test: `tests/test_native_extension_chrome_driver.py` (extend) + a `session_start` PTY render test in `tests/test_native_extension_chrome_session.py`

1. `dispatch_lifecycle_hooks`: add an `ui_driver: "ExtensionUiDriver | None" = None`
   keyword param and build the ctx UI as `_CollectingUi(has_ui, notify_sink, ui_driver=ui_driver)`.
2. `_ExtensionAwareEmitter.__init__`: add `ui_driver: "ExtensionUiDriver | None" = None`,
   store `self._lifecycle_ui_driver`, and pass `ui_driver=self._lifecycle_ui_driver`
   in the `fire_lifecycle` → `dispatch_lifecycle_hooks(...)` call.
3. Emitter construction site (~1391, which runs AFTER `extension_ui_driver` is built
   at ~1317): pass `ui_driver=extension_ui_driver`.
4. Tests: a unit test that `dispatch_lifecycle_hooks(..., ui_driver=fake)` delegates a
   hook's `ctx.ui.set_widget(...)` to the fake driver; and a real-PTY test where a
   `session_start` extension sets a widget and it renders live (reusing the Task-6
   PTY harness — this is the end-to-end proof the gap is closed).
5. `just`-style checks: `mypy` + `ruff` clean; extension/tool-loop regression green.

## Task 7: Golden conformance, new gate, example, docs

**Files:**
- Modify: `docs/examples/extensions/pipy-extension-conformance.py`
- Modify: `scripts/parity_checks/extension_conformance_gate.py` (`_REQUIRED`)
- Create: `scripts/parity_checks/extension_chrome_widgets_conformance.py`
- Create: `docs/examples/extensions/chrome-widgets-demo.py`
- Modify: `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, `docs/parity-plan.md`, `CHANGELOG.md`

- [ ] **Step 1: Extend the golden conformance extension**

In `docs/examples/extensions/pipy-extension-conformance.py`, add five chrome calls inside the `session_start` hook (which already runs in the gate) and emit five markers. After the existing `_session_start`:

```python
    @api.on("session_start")
    def _chrome(event, ctx):
        ctx.ui.set_widget("conf", ["conformance widget"], placement="above_editor")
        _proof("set_widget", placement="above_editor")
        ctx.ui.set_header(lambda theme: lines_component(["conformance header"]))
        _proof("set_header")
        ctx.ui.set_footer(lambda theme, fd: lines_component([f"branch={fd.git_branch}"]))
        _proof("set_footer")
        ctx.ui.set_title("pipy conformance")
        _proof("set_title")
        ctx.ui.set_working_indicator(["*"], interval_ms=120)
        _proof("set_working_indicator")
```

(Two `@api.on("session_start")` handlers are both fired; `lines_component` is already imported in this file.)

- [ ] **Step 2: Add the five markers to the golden gate's `_REQUIRED`**

In `scripts/parity_checks/extension_conformance_gate.py`, add to the `_REQUIRED` set:

```python
    "set_widget",
    "set_header",
    "set_footer",
    "set_title",
    "set_working_indicator",
```

Add the new marker names to the archive-privacy `marker not in _archive_blob(...)` tuple as well (they are proof side-channel markers that must not appear in the archive).

- [ ] **Step 3: Run the golden gate to verify it fails then passes**

Run: `uv run python scripts/parity_checks/extension_conformance_gate.py --json`
Expected after Step 1-2: `"passed": true` with the 5 new markers present and no leak. (If it fails on `all_markers`, the session runs in non-TTY capture mode where `terminal_ui is None`; the markers are still written because the proof calls run regardless of `has_ui`. Confirm the `_proof` lines execute — they are unconditional.)

- [ ] **Step 4: Write the new unit conformance gate**

```python
# scripts/parity_checks/extension_chrome_widgets_conformance.py
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
    ui.set_extension_working_indicator(None, 120)   # frames=None -> stored None == "use default frames"
    b = ui.extension_indicator_frames is None
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
```

- [ ] **Step 5: Run the new gate**

Run: `uv run python scripts/parity_checks/extension_chrome_widgets_conformance.py --json`
Expected: `"passed": true` (7 checks).

- [ ] **Step 6: Add the focused example extension**

```python
# docs/examples/extensions/chrome-widgets-demo.py
"""Demo: persistent chrome widgets (slice B).

Copy to `<workspace>/.pipy/extensions/chrome-widgets-demo.py`. On session start
it pins a header, an above-editor widget, and a footer showing the git branch,
and sets the terminal title.
"""
from __future__ import annotations

from pipy_harness.extensions import lines_component


def activate(api):
    @api.on("session_start")
    def _start(event, ctx):
        ctx.ui.set_title("pipy · chrome demo")
        ctx.ui.set_header(lambda theme: lines_component([theme.fg("accent", "── chrome demo ──")]))
        ctx.ui.set_widget("hint", ["tip: this widget sits just above the input"])
        ctx.ui.set_footer(
            lambda theme, fd: lines_component(
                [theme.dim(f"branch: {fd.git_branch or 'n/a'}")]
            )
        )
```

- [ ] **Step 7: Update docs + changelog**

- `docs/extension-api.md`: mark slice B shipped (the five chrome APIs, width-reactive snapshot, exclusive header/footer, the bottom-pinned-header adaptation, the deferred liveness follow-on); trim the rich-UI follow-on list to C–F.
- `docs/pi-mono-gap-audit.md`: move chrome widgets from "remaining" to shipped in the extension follow-on section.
- `docs/parity-plan.md`: update the extension-platform §4 row to note chrome widgets (rich-UI item B) shipped.
- `CHANGELOG.md`: add a `feat(extension-api): persistent chrome widgets (slice B)` entry.

- [ ] **Step 8: Full check + docs build**

Run: `just check && just docs-build`
Expected: green; both conformance gates pass.

- [ ] **Step 9: Commit**

```bash
git add docs/examples/extensions/pipy-extension-conformance.py scripts/parity_checks/extension_conformance_gate.py scripts/parity_checks/extension_chrome_widgets_conformance.py docs/examples/extensions/chrome-widgets-demo.py docs/extension-api.md docs/pi-mono-gap-audit.md docs/parity-plan.md CHANGELOG.md
git commit -m "feat(extension-api): chrome-widget conformance gate, golden markers, example, docs (slice B)"
```

---

## Final verification

- [ ] `just check` green (full suite + all conformance gates).
- [ ] `uv run python scripts/parity_checks/extension_chrome_widgets_conformance.py --json` → passed.
- [ ] `uv run python scripts/parity_checks/extension_conformance_gate.py --json` → passed (incl. 5 new chrome markers, no leak).
- [ ] Per-task Pi review loop (`openai-codex/gpt-5.5`), commit only on CLEAN.
- [ ] Whole-feature review before final commit.

## Notes for the implementer

- **Reuse, don't reinvent:** `coerce_tool_render_lines`, `lines_component`, `build_tool_render_theme`, `_sanitize_custom_overlay_text`, `_visible_len_allow_sgr`, `_clip_custom_overlay_text`, `sanitize_label_text`, `_safe_extension_status_key` all already exist — use them.
- **Privacy invariant:** chrome content (widget/header/footer lines, title, frames, FooterData) is in-memory only. Never write it to the session tree, archive, or provider request. The golden gate's `archive_privacy` check enforces this; if it fails, you leaked.
- **Fail-soft everywhere:** a bad factory/render must fall back (clear region / built-in), never abort a turn or paint. `KeyboardInterrupt`/`SystemExit` always propagate.
- **Budget safety:** chrome heights subtract from the input `max_rows`, but the input keeps a 1-row minimum — verify a tall stack of widgets never zeroes the input (the `max(1, ...)` guards do this; PTY-test it if you change the math).
- **Factory-once:** build the component once at set-time; on resize re-render the retained component (`invalidate()` then `render(width)`), do not re-invoke the factory. A footer reflects its set-time `FooterData` snapshot.
