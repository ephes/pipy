# Extension chrome widgets (persistent chrome, snapshot runtime) — Design

Status: design drafted 2026-06-21 (brainstorming). Second sub-slice of the
extension-platform **rich-UI layer** follow-on
([../../extension-api.md](../../extension-api.md), the gap audit's largest
remaining area). Tracked as extension-api **slice B** (rich-UI item B, after
slice 17 = custom tool renderers = item A). This spec covers the persistent
chrome surface only; the other rich-UI siblings (rich message renderers C,
editor integration D, theme controls E, extension state/session-manager views F)
are separate slices.

## Decisions locked during brainstorming (2026-06-21)

1. **Full slice B in one spec.** All five Pi chrome APIs ship together:
   `setWidget`, `setHeader`, `setFooter`, `setTitle`, `setWorkingIndicator`
   (pipy spellings `set_widget`/`set_header`/`set_footer`/`set_title`/
   `set_working_indicator`).
2. **Width-reactive snapshot rendering.** Adopt Pi's `Component` contract
   (`render(width) -> lines`, `invalidate()`, optional `dispose()`) **and** accept
   a `Sequence[str]` convenience. Each component is rendered to lines **at set
   time and on resize**, stored as snapshot lines re-painted every frame. **No
   per-frame `render()`, no `requestRender`-driven animation, no reactive
   `footerData` push subscriptions** — these are the documented liveness deferral
   (consistent with the slice-17 "no live runtime" decision; minor versions may
   add them additively). `set_working_indicator` is the one inherently-animated
   API and reuses pipy's **existing** built-in spinner loop (it overrides frames /
   interval), so it needs no new timer machinery.
3. **Exclusive replace for header/footer.** `set_header`/`set_footer` fully OWN
   their region, **replacing** pipy's built-in header/footer; one owner each;
   passing `None` restores the built-in. The footer factory receives a read-only
   footer-data snapshot (git branch + extension statuses, mirroring Pi's
   `ReadonlyFooterDataProvider` minus the deferred `onBranchChange` reactivity).
   If no extension sets a header/footer, built-in chrome is unchanged (no
   regression for default users).
4. **Widgets are keyed and additive.** `set_widget(key, content, placement=…)`;
   multiple widgets coexist keyed by string id; `placement` is `"above_editor"`
   (default) or `"below_editor"`; passing `None` content clears that key (Pi
   parity).
5. **Reuse slice-17 styling.** Component factories receive the slice-17
   `ToolRenderTheme` semantic helper (over `ChromePalette`); reuse `lines_component`
   and the fail-soft `render_*` dispatch posture rather than inventing a parallel
   theme/coercion path.

## Goal

Let a Python extension manage Pi-shaped persistent terminal chrome — widgets
above/below the editor, a custom header and footer region, the OS terminal
title, and the working-spinner frames — using a contract that translates cleanly
from Pi's `ExtensionUIContext` and forward-extends to a live-component runtime
without an API break, while preserving pipy's no-archive-leak and fail-soft
invariants.

## Pi reference (verified against `/Users/jochen/src/pi-mono`)

- `packages/coding-agent/src/core/extensions/types.ts`:
  - `setWidget(key, content: string[] | ((tui, theme) => Component & {dispose?}) | undefined, options?: ExtensionWidgetOptions)` (~162–168).
  - `setHeader(factory: ((tui, theme) => Component & {dispose?}) | undefined)` (~182–183).
  - `setFooter(factory: ((tui, theme, footerData: ReadonlyFooterDataProvider) => Component & {dispose?}) | undefined)` (~170–180).
  - `setTitle(title: string)` (~185–186) → OS terminal title.
  - `setWorkingIndicator(options?: WorkingIndicatorOptions)` (~149–157);
    `WorkingIndicatorOptions = { frames?: string[]; intervalMs?: number }` (~108–114) —
    empty `frames` hides; custom frames rendered verbatim.
  - `WidgetPlacement = "aboveEditor" | "belowEditor"` (~97–103),
    default `"aboveEditor"`.
  - `ReadonlyFooterDataProvider`: `getGitBranch()`, `getExtensionStatuses()`,
    `onBranchChange(cb) -> unsub`.
- `Component` (`packages/tui/src/tui.ts` ~39–63): `render(width) -> string[]`,
  `invalidate()`, optional `handleInput`. Factories return `Component & {dispose?(): void}`.
- TUI layout order (`modes/interactive/interactive-mode.ts` ~620–691):
  `headerContainer → chatContainer → statusContainer → pendingMessagesContainer
  → widgetContainerAbove → editorContainer → widgetContainerBelow → footer`.
  Widgets keyed in `extensionWidgetsAbove`/`extensionWidgetsBelow` maps; **max 10
  lines** per widget (`MAX_WIDGET_LINES`, truncation marker on overflow);
  `undefined` content clears; setting same key disposes+replaces.
  `setExtensionWidget` (~1771–1811), `setExtensionHeader` (~1928–1967),
  `setExtensionFooter` (~1895–1923), `setTitle` (~2010 →
  `terminal.setTitle`), `setWorkingIndicator` (~1749–1753 → `Loader.setIndicator`).
- Header/footer are **exclusive** (custom replaces built-in; `undefined`
  restores). `setTitle` is OS-level (no in-frame render, no reset variant).
- Examples: `examples/extensions/custom-header.ts`, `custom-footer.ts`.

> Note: line numbers above are from the brainstorming exploration pass and are
> approximate; the implementation plan extracts verbatim current signatures.

## Current pipy state (verified against `/Users/jochen/projects/pipy`)

- **Slice 15 chrome state already ships** in `ToolLoopTerminalUi`
  (`src/pipy_harness/native/tui.py` ~436–455): `extension_status: dict[str,str]`,
  `extension_working_message: str | None`, `extension_working_visible: bool`,
  `footer_lines: tuple[str,str]` (two fixed built-in footer rows), `working_text`
  (built-in spinner). Setters `set_extension_status` / `set_extension_working_message`
  / `set_extension_working_visible` (~1573–1602); renderer `_extension_status_lines`
  (~2763–2784, "notice" kind, top-3 + overflow). `ctx.ui.set_status` already maps
  to Pi's `setStatus` (the footer-data status channel).
- **Frame is bottom-pinned live region + scrollback history.** `_history_blocks`
  (finalized rows) are committed to the terminal's own scrollback; the
  redrawn-each-paint **live region** is assembled in `_live_region_lines` /
  `_frame_lines` (~2238–2425, 2522–2587) in order: pending region → input
  separator → input frame → bottom separator → popup menu → extension status →
  footer rows. Slice-17 custom tool rows append to `_history_blocks`
  (`add_tool_call_custom`/`add_tool_result_custom` ~2207–2225).
- **Slice-17 styling primitives** (`src/pipy_harness/native/tool_renderers.py`):
  `ToolRenderTheme` over `ChromeStyle`/`ChromePalette` (semantic `fg`/`bold`/`dim`,
  `ThemeColor = text|accent|success|warning|error|dim`), `build_tool_render_theme`,
  `lines_component`, fail-soft `render_tool_phase`, `coerce_tool_render_lines`
  (special-cases `str` before the generic `Sequence` path). Pre-styled custom
  lines commit under `tool_call_custom`/`tool_result_custom` line-kinds that
  preserve embedded SGR (`_visible_len_allow_sgr` for width).
- **Extension UI plumbing** (`src/pipy_harness/native/extension_runtime.py`):
  `ExtensionUi`/`ExtensionUiDriver` protocols (~798–842), `_CollectingUi`
  (~943–1044, records statuses/working for non-TTY/tests), `CommandContext`
  (~880–918, `ui`, `append_entry`, `set_active_tools`, etc.).
  `_LiveExtensionUiDriver` in `tool_loop_session.py` (~1267–1297) delegates to
  `terminal_ui.set_extension_*`. `_ExtensionRuntime` bundles contributions; the
  driver/sinks are re-wired on `/reload`.
- **No widget/header/footer/title/indicator state exists yet** — slice B adds it.
- **Conformance precedent:** golden extension `docs/examples/extensions/pipy-extension-conformance.py`
  writes metadata-only feature markers to `PIPY_EXTENSION_CONFORMANCE_PROOF`;
  `extension_conformance_gate.py` drives a real session and asserts markers +
  no-leak. Per-feature gates live under `scripts/parity_checks/extension_*_conformance.py`.

> Note: line numbers above are approximate (brainstorming exploration); the plan
> re-extracts verbatim signatures.

## Contract (extension-facing API)

Added to `pipy_harness.extensions` (impl in `extension_runtime.py`); the
component/theme primitives **reuse** slice 17.

```python
WidgetPlacement = Literal["above_editor", "below_editor"]

@runtime_checkable
class ChromeComponent(Protocol):
    """A width-reactive snapshot component. Only `render(width)` is required (so
    slice-17 `lines_component` output satisfies it structurally). `invalidate()`
    and `dispose()` are OPTIONAL and duck-typed — called if present: `invalidate()`
    before a re-render on resize/theme change, `dispose()` when the component is
    replaced, cleared, on /reload, or shutdown. Reserved for the live slice and
    NOT called this slice: any per-frame repaint or requestRender-driven
    animation."""
    def render(self, width: int) -> Sequence[str]: ...
    # invalidate(self) -> None   # optional, duck-typed
    # dispose(self) -> None      # optional, duck-typed

@dataclass(frozen=True)
class FooterData:
    """Read-only snapshot handed to a footer factory (Pi's
    ReadonlyFooterDataProvider, minus the deferred onBranchChange reactivity)."""
    git_branch: str | None
    extension_statuses: Mapping[str, str]

# Factory signatures (Pi drops the live `tui` param — requestRender is deferred):
HeaderFactory  = Callable[[ToolRenderTheme], ChromeComponent]
WidgetFactory  = Callable[[ToolRenderTheme], ChromeComponent]
FooterFactory  = Callable[[ToolRenderTheme, FooterData], ChromeComponent]
WidgetContent  = str | Sequence[str] | WidgetFactory   # see coercion note below
```

`WidgetContent` is intentionally accepted as a callable factory, a bare `str`, or
a sequence of line strings — but **because `str` is itself a `Sequence[str]` in
Python**, the runtime must disambiguate in a fixed order: (1) `callable(content)`
→ treat as a `WidgetFactory`; (2) `isinstance(content, str)` → one logical block
**split on `\n`**, never iterated character-per-line; (3) any other `Sequence` →
coerced element-wise with `str(...)`; `bytes`/`bytearray` → rejected to fallback.
This is exactly slice-17's `coerce_tool_render_lines` behavior (reused here, not
re-implemented), so a one-line string widget renders as one row.

Five methods on `ctx.ui` (`ExtensionUi`) and the live `ExtensionUiDriver`:

```python
def set_widget(self, key: str, content: WidgetContent | None,
               *, placement: WidgetPlacement = "above_editor") -> None: ...
def set_header(self, factory: HeaderFactory | None) -> None: ...
def set_footer(self, factory: FooterFactory | None) -> None: ...
def set_title(self, title: str) -> None: ...
def set_working_indicator(self, frames: Sequence[str] | None = None,
                          *, interval_ms: int | None = None) -> None: ...
```

Semantics:

- **`set_widget`** — keyed/additive. Multiple widgets coexist (keyed by sanitized
  `key`), rendered in **insertion order** within their placement region (matching
  Pi's `Map` iteration — a plain insertion-ordered `dict` gives this, and
  re-setting an existing key updates in place without moving it). `content`
  is a `WidgetContent` — a `WidgetFactory`, a bare `str`, or a sequence of line
  strings — coerced per the fixed-order rule above (callable → factory; `str` →
  split on `\n`; other `Sequence` → element-wise). `None` content clears that key
  (disposing its component). Re-setting an existing key disposes the old component
  and replaces it.
- **`set_header`** — exclusive. Replaces the built-in top chrome; one owner;
  `None` restores built-in.
- **`set_footer`** — exclusive. Replaces the built-in `footer_lines`; one owner;
  `None` restores built-in. The factory runs **once at set-time** with a
  `FooterData` snapshot captured then; it is not re-invoked on resize (see Data
  flow §3 for the factory-once / refresh-on-re-set rule), so the footer reflects
  branch/statuses as of the last `set_footer` call.
- **`set_title`** — sets the OS terminal title via OSC on a TTY; **no-op** in
  non-TTY/RPC/print modes. No in-frame render; no reset variant (Pi parity),
  though shutdown restores a default.
- **`set_working_indicator`** — overrides the built-in spinner, mirroring Pi's
  `WorkingIndicatorOptions` where **`frames` and `interval_ms` default
  independently** (each `None` means "use the built-in default for *that* field",
  exactly Pi's optional `frames?`/`intervalMs?`): `set_working_indicator()`
  restores the default spinner (default frames + default interval);
  `set_working_indicator(interval_ms=120)` keeps the **default** frames at a
  120 ms interval; a non-empty `frames` sets custom frames (with `interval_ms`,
  when given, overriding the interval); an **empty** `frames` sequence hides the
  spinner glyph. Custom frames are rendered verbatim (SGR-clipped, bounded).
  Reuses the existing spinner animation loop.

Convenience: `lines_component(seq)` (reused from slice 17) wraps a `str`/sequence
as a `ChromeComponent`, so a static widget is
`ctx.ui.set_widget("k", ["line one", "line two"])` and a themed one is
`ctx.ui.set_widget("k", lambda theme: lines_component(theme.fg("accent", "hi")))`.

Public re-exports from `pipy_harness.extensions`: `ChromeComponent`,
`WidgetPlacement`, `FooterData`, `HeaderFactory`/`WidgetFactory`/`FooterFactory`
(type aliases); `ToolRenderTheme`/`ThemeColor`/`lines_component` are already
exported (slice 17).

## Rendering placement (frame regions) — the one honest adaptation

pipy's live region is **pinned to the bottom**; finalized history scrolls above
it in the terminal's own scrollback. Pi owns the full screen and pins its header
to the literal top. pipy cannot place a persistent header at the literal viewport
top without a full-screen rewrite (out of scope), so the extension header renders
at the **top of the bottom-pinned live region**. **The API and semantics stay
Pi-identical**; only the physical screen position differs because pipy does not
own the whole screen. Live-region order (top → bottom) preserves Pi's relative
ordering of the live elements — crucially `pending → widgetsAbove → editor →
widgetsBelow` (Pi's `pendingMessagesContainer → widgetContainerAbove →
editorContainer → widgetContainerBelow`), so `above_editor` widgets sit
**immediately above the input** (after the pending region) and `below_editor`
widgets immediately below it:

```
[ history / chat  → terminal scrollback, above the live region ]
┌─ live region (bottom-pinned, redrawn each paint) ───────────┐
│  extension header          (NEW, exclusive)                 │
│  pending region            (existing)                       │
│  widgets: above_editor     (NEW, keyed — just above input)  │
│  input separator / input frame / bottom separator (existing)│
│  popup menu                (existing, transient on input)   │
│  widgets: below_editor     (NEW, keyed — below input/popup) │
│  extension status rows     (existing, slice 15)             │
│  extension footer  OR  built-in footer_lines (NEW exclusive)│
└─────────────────────────────────────────────────────────────┘
```

The extension header is the one element pipy cannot place at Pi's literal screen
top (Pi has `header → chat → status → pending`; pipy's chat is scrollback), so it
pins to the top of the live region, above the pending region. The slash/file
popup menu is transient and **takes precedence directly beneath the input** when
shown (it must hug the input it completes); `below_editor` widgets render below
the popup, so in steady state — no popup — they sit directly beneath the input.
The popup has no Pi equivalent, so this precedence is a pipy-local adaptation.

- `set_title` writes OSC `\x1b]0;{sanitized}\x07` directly to the terminal on a
  TTY (no frame line); non-TTY no-op.
- `set_working_indicator` overrides the frames/interval consumed by the existing
  spinner loop that renders `working_text`; empty frames suppress the glyph.

## Data flow / wiring

1. **State on `ToolLoopTerminalUi`** (mirrors slice-15 fields): `extension_header:
   _ChromeRegion | None`, `extension_footer: _ChromeRegion | None`,
   `extension_widgets_above: dict[str, _ChromeRegion]`,
   `extension_widgets_below: dict[str, _ChromeRegion]`, `extension_title: str | None`,
   `extension_indicator_frames: tuple[str, ...] | None`,
   `extension_indicator_interval_ms: float | None`. A `_ChromeRegion` holds the
   source kind (lines or factory), the **built component** (for factory sources,
   created once at set-time), the **last rendered snapshot lines**, and the width
   they were rendered at.

2. **Setters on the TUI** (`set_extension_header/footer/title/widget/working_indicator`):
   sanitize key/title, build the component **once** from the factory (header/widget
   get `(theme)`; footer gets `(theme, footer_data_snapshot)` captured at set-time),
   render the snapshot at the current width (fail-soft → fall back to built-in /
   drop the widget), dispose any replaced component, then `paint()`. A
   `Sequence[str]` source is wrapped via `lines_component`.

3. **Re-render on resize.** When the paint width changes, the **retained**
   component is re-rendered: call `invalidate()` (if present), then
   `render(new_width)`. The factory is **not** re-invoked (Pi's "factory once"),
   so a footer reflects the `FooterData` snapshot captured at its last
   set/re-set — the documented liveness deferral; the extension re-calls
   `set_footer` to refresh. Pure `Sequence[str]` widgets are re-clipped, not
   re-flowed.

4. **Region renderers** (`_extension_header_lines`, `_extension_widgets_lines(placement)`,
   `_extension_footer_lines`) return pre-styled `_FrameLine`s under new line-kinds
   `chrome_custom` (preserve embedded SGR like `tool_*_custom`). `_live_region_lines`
   inserts them in the order above and subtracts their heights from the input
   `max_rows` budget — **never starving the input** (min 1 row; chrome truncates
   with an indicator first).

5. **Driver delegation.** `_LiveExtensionUiDriver` gains the five methods,
   delegating to the TUI setters; it also builds the `FooterData` snapshot from
   the existing git-branch source + the slice-15 `extension_status` map. The
   non-TTY `_CollectingUi` records the five for determinism/tests (no terminal
   side effects; `set_title` records the intended title).

6. **Privacy:** every chrome value (widget/header/footer lines, title, frames,
   `FooterData`) is **in-memory chrome state only** — never written to the session
   archive, the session tree, or a provider request. Same invariant as slices
   16/17.

## Framing, failure, bounds, lifecycle

- **Fail-soft:** a factory that raises, returns a non-component, or whose
  `render()` raises / returns a non-`str`-non-`Sequence` → fall back to the
  built-in region (header/footer) or drop that widget, plus a bounded local
  diagnostic. A broken chrome component never aborts the turn or the paint.
  `KeyboardInterrupt`/`SystemExit` propagate. Reuses the slice-17
  `coerce_tool_render_lines` / `render_tool_phase` posture.
- **Bounds (protect the frame budget):** widget ≤10 lines each (Pi parity) and
  ≤16 widgets total per placement; header ≤8 lines; footer ≤4 lines; title ≤256
  chars; ≤32 indicator frames, each width-clipped. Per-line char cap reuses
  `_CUSTOM_RENDER_MAX_CHARS`. Overflow truncates with a muted indicator row.
- **dispose():** called fail-soft when a widget key is replaced/cleared, a
  header/footer is replaced/restored, on `/reload` (all extension chrome cleared
  before re-activation), and on session shutdown (chrome cleared, terminal title
  restored). Pure `Sequence[str]` widgets have no dispose.
- **`/reload`:** clears all extension-owned chrome (disposing components) and the
  re-activated extensions re-set what they want — avoids stale chrome from an
  unloaded extension. (Unlike the slice-17 renderer-map, chrome state is rebuilt
  from re-activation, so there is no "built once" staleness here.)

## Implementation slices

1. **Contract + non-TTY recording.** Add `ChromeComponent`, `WidgetPlacement`,
   `FooterData`, the factory aliases, and the five methods to the
   `ExtensionUi`/`ExtensionUiDriver` protocols and `_CollectingUi`; re-export from
   `pipy_harness.extensions`. Unit-tested in isolation (recording, key/title
   sanitization, `None`-clears, `lines_component` reuse). Pure additions.
2. **TUI state + setters + snapshot render.** Add the `_ChromeRegion` state and
   `set_extension_*` setters with at-set-time fail-soft snapshot rendering, bounds,
   and dispose-on-replace. Unit tests over the TUI in isolation (set/replace/clear,
   keyed multiples, bounds/truncation, fail-soft, dispose calls).
3. **Frame integration + resize.** Wire the region renderers and the new
   `chrome_custom` line-kind into `_live_region_lines`/`_styled_line`, the budget
   math, and resize re-render. Real-PTY tests at 80×24 and 100×40: header +
   above/below widgets + footer render in order, input stays usable, resize
   reflows factory widgets and re-clips static ones (mirrors slice-17 PTY tests).
4. **Title + working-indicator.** OSC title write (TTY) / no-op (non-TTY) /
   shutdown restore; spinner frame/interval override (set/hide/restore) threaded
   through the existing spinner loop. PTY + unit coverage.
5. **Driver wiring + FooterData.** Extend `_LiveExtensionUiDriver`; build the
   `FooterData` snapshot (git branch + slice-15 statuses); wire reload/shutdown
   clearing. Product-path test through a real session driving all five APIs.
6. **Golden conformance + example + docs.** Extend
   `docs/examples/extensions/pipy-extension-conformance.py` to exercise all five
   APIs and write five metadata-only markers (`set_widget`/`set_header`/
   `set_footer`/`set_title`/`set_working_indicator`); extend
   `extension_conformance_gate.py` to assert them end-to-end + no-leak. Add
   `scripts/parity_checks/extension_chrome_widgets_conformance.py --json` proving
   set/replace/clear, keyed multiples (insertion order preserved), both placements
   (above immediately above the input; below directly under the input, under the
   transient popup when one is shown), exclusive header/footer replace+restore,
   title OSC on TTY / no-op off, indicator override / default-frames-custom-interval
   / hide / restore, resize re-render, fail-soft fallback, bounds/truncation,
   dispose-on-
   replace/clear/reload/shutdown, and **no chrome body leaks** to archive/
   provider/tree. Add a focused example extension under `docs/examples/extensions/`
   (a footer showing git branch + a status, plus an above-editor widget). Update
   `docs/extension-api.md` (new shipped slice-B entry; trim the rich-UI follow-on
   list to C–F), `docs/pi-mono-gap-audit.md`, `docs/parity-plan.md`, and
   `CHANGELOG.md`.

## Conformance gate & testing

```sh
uv run python scripts/parity_checks/extension_chrome_widgets_conformance.py --json
uv run python scripts/parity_checks/extension_conformance_gate.py --json
just check
```

Per-slice unit/PTY tests as listed; `just check` green at each slice end; Pi
review loop per slice, commit only on CLEAN; final slice also runs
`just docs-build`.

## Out of scope (deferred follow-ons)

- **Live runtime:** per-frame `render()`, `requestRender`-driven animation,
  reactive `footerData.onBranchChange` push subscriptions. Added additively later
  (the factory contract already matches Pi's `Component`, so no API break);
  requires re-rendering the chrome region on arbitrary state change rather than
  only on resize / explicit re-set.
- **Literal top-of-screen header** (a sticky header above scrollback) — would
  require a full-screen TUI model; out of scope.
- **Per-widget render shell / borders** beyond the snapshot lines.
- The other rich-UI siblings (rich message renderers C, editor integration D,
  theme controls E, extension state/session-manager views F), and the broader
  extension-platform follow-ons (OAuth-provider registration, RPC extension-UI
  channel, PyPI/npm sources).

## Risks

- **Bottom-pinned vs. top-pinned header.** The header lands at the top of the
  live region, not the literal screen top (documented adaptation). Mitigation:
  keep the API/semantics identical; document the placement in `extension-api.md`;
  PTY-test relative ordering.
- **Frame-budget starvation.** Many/tall chrome regions could crowd out the
  input. Mitigation: subtract chrome heights from `max_rows`, enforce a 1-row
  input minimum, truncate chrome (not input) on overflow; PTY-test a tall-widget
  case.
- **Exclusive footer staleness.** A replaced footer freezes built-in token/branch
  data between re-renders (resize / explicit re-set). Accepted, documented as the
  liveness deferral; `FooterData` gives the extension branch + statuses to rebuild
  on demand.
- **Embedded SGR through the live-region pipeline.** Pre-styled chrome lines flow
  through `_live_region_lines`/`_styled_line`. Mitigation: a dedicated
  `chrome_custom` line-kind (band/clip only, trusts `_visible_len_allow_sgr`),
  reusing the slice-17 approach; unit-test wrap/clip with embedded SGR.
- **Resize re-render correctness.** Factory components must re-render (retained
  component, `render(new_width)`) at the new width while static `Sequence[str]`
  widgets only re-clip. Mitigation: store the source kind on `_ChromeRegion`; PTY
  resize test for both kinds.
- **OSC title portability.** Some terminals ignore/garble OSC titles. Mitigation:
  TTY-guard, sanitize (strip control chars/newlines, bound length), no-op on
  non-TTY, restore on shutdown; unit-test the emitted bytes rather than terminal
  behavior.
