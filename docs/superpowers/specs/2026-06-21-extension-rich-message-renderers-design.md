# Extension rich message renderers (snapshot runtime, Pi-faithful contract) — Design

Status: design drafted 2026-06-21 (brainstorming). Rich-UI sibling **C** of the
extension-platform rich-UI layer follow-on
([../../extension-api.md](../../extension-api.md), the gap audit's largest
remaining area). Builds directly on **slice 16** (the first, text-only
`register_message_renderer` / `ctx.append_entry` slice) and reuses the
**slice 17** custom-tool-renderer machinery. This spec covers the renderer
**rendering contract only**; the other pieces the gap audit bundled under "C"
are deliberately **deferred to their own slices** (see Non-goals): the
`send_message` conversation-injection API (`deliverAs`/`triggerTurn`) and
replay-on-resume.

## Decisions locked during brainstorming (2026-06-21)

1. **Scope = the rich Component renderer upgrade only.** Slice C upgrades
   slice-16's text-only renderer to a Pi-faithful `Component`. `send_message`
   and replay-on-resume are **out of scope** (separate future slices).
2. **Replay-on-resume is deferred because it is blocked.** pipy does **not**
   re-render prior conversation history into the TUI on resume/switch for *any*
   entry type — resumed turns load into the in-memory provider context but the
   TUI shows only a status notice. Replaying only custom-rendered entries while
   the surrounding user/assistant messages stay invisible would be incoherent.
   Replay therefore depends on a broader, not-yet-existing "render prior
   transcript on resume" feature and is its own future slice.
3. **Snapshot runtime, Pi-faithful contract** (mirrors slice 17). The renderer
   runs **render-once** at the current terminal width when the custom entry is
   appended; there is no live `invalidate()`/`requestRender` repaint loop. This
   is honest for pipy because custom entries land in **static committed
   scrollback** (only the live region re-renders on resize). The
   extension-facing contract is still the end-state contract (`render(width)`),
   so the deferred liveness pieces are additive later.
4. **Back-compat by arity.** A 1-argument `renderer(data)` keeps its exact
   slice-16 behavior. A renderer that **requires** a second positional
   parameter (a second positional parameter WITHOUT a default; the
   capture-default idiom `renderer(data, prefix=x)` stays 1-arg) receives a
   `MessageRenderContext` and may return a component. Arity is detected with
   `inspect.signature`, counting only required positional parameters.
5. **Judgment call ① — text stays plain; color only via a Component.** A
   `str`/`Sequence[str]` return is committed through today's sanitized path (no
   color), exactly as in slice 16. **Only** a returned component (whose
   `render(width)` lines may carry `ctx.theme` SGR) is committed
   SGR-preserving. This is zero behavior change for every existing renderer.
6. **Judgment call ② — the styled path has no forced label.** When the renderer
   returns a component, the component owns its full styling and pipy injects
   **no `[custom_type]` label line** (matches Pi: a custom renderer's component
   replaces the default `[customType]` box). The plain/back-compat path keeps
   the `[label]` prefix as today.
7. **Reuse slice-17 machinery, no parallel module** (the slice-17 Pi review
   enforced this): reuse `coerce_tool_render_lines`, `build_tool_render_theme` →
   `ToolRenderTheme`, `lines_component`, and the `ToolRenderComponent` protocol.

## Goal

Let a Python extension control how **its own** custom session entry (appended
via `ctx.append_entry(custom_type, data)`) is drawn in the product TUI and the
captured/non-TTY renderer, with themed color, using a contract that translates
cleanly from Pi's `registerMessageRenderer` and forward-extends to the
live-component runtime without an API break.

## Pi reference (verified against `/Users/jochen/src/pi-mono`)

- `packages/coding-agent/src/core/extensions/types.ts`:
  - `registerMessageRenderer<T>(customType: string, renderer: MessageRenderer<T>)`
    (~1180).
  - `MessageRenderer<T> = (message: CustomMessage<T>, options: MessageRenderOptions,
    theme: Theme) => Component | undefined` (~1056–1064);
    `MessageRenderOptions = { expanded: boolean }`.
  - `appendEntry<T>(customType, data?)` (~1201) appends a state-only `CustomEntry`
    (`session-manager.ts` ~100), **not** rendered in Pi (Pi renders
    `CustomMessage` from `sendMessage`).
- `packages/tui/src/tui.ts` `Component` (~39): `render(width: number): string[]`
  + optional `invalidate()`.
- `modes/interactive/components/custom-message.ts` `rebuild()` (~50–71): tries the
  custom renderer first; **if it returns a component, that component replaces the
  default `[customType]` box**; on `undefined`/throw it falls back to the default
  box. This is the basis for judgment call ②.

**pipy divergence already in place (slice 16):** pipy renders the
`appendEntry`/`CustomEntry` path through `register_message_renderer` (Pi renders
the `sendMessage`/`CustomMessage` path instead). Slice C keeps pipy's existing
surface — the renderer applies to `CustomEntry` appended via `ctx.append_entry`.
Rendering `CustomMessageEntry` arrives with the deferred `send_message` slice.

## Current pipy state (verified against `/Users/jochen/projects/pipy`)

- `register_message_renderer(custom_type, renderer)` +
  `RegisteredMessageRenderer` live in
  `src/pipy_harness/native/extension_runtime.py` (~602, ~620), re-exported from
  `pipy_harness.extensions`.
- `render_extension_message(renderers, custom_type, data)` (~2000) calls
  `renderer.renderer(data)` (**1 arg**, ~2016), coerces text/lines via
  `_coerce_rendered_lines`, returns a flat `tuple[str, ...]` — **no width, no
  theme, no color**.
- Wiring: `tool_loop_session.py` builds the renderer map at ~1209
  (`extension_renderer_map = _ext_runtime.message_renderers`), renders at append
  (~1478 `render_extension_message(...)` → ~1484
  `terminal_ui.add_custom_entry(safe_type, rendered)`), and **refreshes the map
  on `/reload`** at ~2299.
- `tui.py` `add_custom_entry(custom_type, lines)` (~2422) **sanitizes every line**
  via `sanitize_label_text` (strips SGR/control bytes) and commits a
  `("custom", (f"[{label}]", *safe_lines))` block — so today's custom entries
  **cannot carry color**.
- Reusable slice-17 machinery in `extension_runtime.py`: `ThemeColor` (~710),
  `ToolRenderTheme` protocol (~714), `ToolRenderComponent` protocol (~726),
  `coerce_tool_render_lines` (~793), `_LinesComponent`/`lines_component` (~819,
  ~826); `tool_renderers.py` `build_tool_render_theme` (~49) +
  `render_tool_phase` fail-soft dispatch (~53). The `custom`-style SGR-preserving
  line-kinds for tools are `tool_call_custom`/`tool_result_custom`.
- Privacy: `safe_custom_entry_data` (~1977) JSON-safe-copies + bounds `data`
  before persistence; `_copy_custom_entry_data` (~2029) hands the renderer a
  detached copy.

## Proposed design

### Public surface (`pipy_harness.extensions`)

- `register_message_renderer(custom_type, renderer)` — **signature unchanged**;
  the renderer **callable contract** widens:
  - `renderer(data)` → `str | Sequence[str] | None` — unchanged slice-16 form.
  - `renderer(data, ctx)` → `MessageRenderComponent | str | Sequence[str] | None`
    — new rich form.
- `MessageRenderContext` — new frozen dataclass:
  `custom_type: str`, `data: object | None`, `expanded: bool`, `width: int`,
  `theme: ToolRenderTheme`. (Parallels `ToolRenderContext`; no `state`/`details`,
  which are tool-execution concepts.)
- `MessageRenderComponent` — alias of the existing `ToolRenderComponent`
  (`render(width) -> Sequence[str]`), re-exported for discoverability.
- `lines_component`, `ToolRenderTheme`, `ThemeColor` — already public, reused.

### Rendering pipeline (internal)

- `render_extension_message(...)` gains keyword params `*, width: int,
  expanded: bool, theme: ToolRenderTheme | None` and returns a small frozen
  `RenderedCustomEntry(lines: tuple[str, ...], styled: bool)`:
  - **Arity-flex call:** if the renderer accepts ≥2 REQUIRED positional params
    (params without defaults), call
    `renderer(detached_data, MessageRenderContext(...))`; else `renderer(detached_data)`.
    Detection via `inspect.signature`, with a robust fallback to the 1-arg call.
    The capture-default idiom `renderer(data, prefix=captured)` stays 1-arg/plain
    so the context never clobbers its default.
  - **Component return** (`render` is callable): render once at `width`, coerce via
    `coerce_tool_render_lines`, length-bound → `styled=True`.
  - **`str`/`Sequence[str]` return:** coerce via the existing text path →
    `styled=False` (back-compat, plain).
  - **`None`:** generic fallback (`styled=False`).
  - Renderer raises / `render()` raises / uncoercible → bounded
    `"render error: …"` generic line (`styled=False`). `KeyboardInterrupt`/
    `SystemExit` propagate. (Mirrors `render_tool_phase` fail-soft semantics.)
- `tui.py`: new `custom_message_custom` line-kind (SGR-preserving, clipped to
  visible width, no `textwrap`), paralleling `tool_call_custom`.
  `add_custom_entry(custom_type, lines, *, styled: bool = False)`:
  `styled=True` commits the component lines under the new kind with the slice-17
  band framing and **no injected `[label]` line**; `styled=False` keeps today's
  sanitized `[label]` behavior unchanged.

### Wiring (both render paths)

- **Live TTY** (`_TuiToolLoopRenderer`): pass the real terminal width, the live
  Ctrl+O `expanded` state, and `build_tool_render_theme(chrome_style)`.
- **Captured / non-TTY** (`_ToolLoopRenderer`): fixed `width=80`,
  `expanded=False`, theme emits plain text (captured / `NO_COLOR`). (Mirrors the
  slice-17 captured-path limitation, documented as such.)
- `/reload`: the renderer map is already refreshed at `tool_loop_session.py:2299`
  and stays refreshed — a deliberate edge over slice-17 tool renderers, which do
  not refresh on reload.

### Error handling — fail-soft

A bad renderer never crashes the session: any exception in the renderer or its
`render()`, or an uncoercible/oversized return, falls back to the bounded
generic text rendering. `KeyboardInterrupt`/`SystemExit` propagate. Output is
length-bounded (line count + per-line visible-width clip) like slices 17/18.

### Privacy (unchanged invariants)

`data` is JSON-safe-copied and bounded (`safe_custom_entry_data`) before
persistence; the renderer receives a **detached copy** (`_copy_custom_entry_data`);
rendered lines, theme output, and component results are **live-UI only** — never
archived, never sent to the provider. No new archive fields.

## Acceptance criteria

- A rich renderer (`renderer(data, ctx)` returning a themed component) commits
  SGR-styled, visible-width-clipped lines to the product-TUI scrollback with no
  forced `[custom_type]` label line.
- An existing 1-arg `renderer(data)` returning text/lines renders **byte-for-byte
  as before** (sanitized, `[label]` prefix, no color).
- `expanded` and the live terminal `width` reach the renderer in the TTY path;
  the captured path uses `width=80`, `expanded=False`, and plain text.
- A renderer or `render()` that raises, or returns an uncoercible/oversized
  value, falls back to bounded generic rendering without crashing the session.
- Output is length-bounded; `KeyboardInterrupt`/`SystemExit` propagate.
- `/reload` re-reads renderers (changed/added renderers take effect).
- The default session archive contains no rendered lines, theme output, or
  component results — only the existing safe custom-entry metadata.

## Conformance gate

New `scripts/parity_checks/extension_message_renderer_conformance.py --json`
(paralleling `extension_tool_renderer_conformance.py`) proving: arity-flex
(1-arg vs 2-arg), component → styled SGR, text → plain back-compat, `None` →
generic fallback, raising renderer/`render()` → fail-soft, width + `expanded`
threaded, theme color on/off, length bounding, and reload refresh.

The golden `pipy-extension-conformance.py` +
`scripts/parity_checks/extension_conformance_gate.py --json` gain a
`message_renderer_component` marker proving the end-to-end rich render and the
no-leak guarantee (mirrors how slices 17/18 added markers). One real-PTY test
proves visible color on the live styled path at one width.

`just check` is green (tests + mypy + ruff + `just docs-build`).

## Non-goals (deferred to their own slices)

- **`send_message` / `deliverAs` / `triggerTurn`** — the conversation-injection
  API (separate slice; `deliverAs:"steer"` is itself partly blocked on pipy's
  lack of true in-turn steering).
- **Replay-on-resume** — blocked on a broader "render prior transcript on
  resume" feature pipy lacks for all entry types (decision 2).
- **Rendering `CustomMessageEntry`** — arrives with `send_message`.
- **Width-reactivity of committed scrollback entries** — render-once snapshot,
  like slice 17 (pipy scrollback is static; only the live region reflows).
- **Live `invalidate()` / `requestRender` animation**, **partial-result
  streaming**, and **multi-widget message components**.
- **Overriding built-in/default message rendering** beyond the existing custom
  `CustomEntry` surface.

## Open questions

- Should the styled band framing for `custom_message_custom` match
  `tool_*_custom` exactly, or use a distinct accent so custom entries read
  differently from tool rows? (Default: match slice-17 framing for consistency;
  revisit if it reads ambiguously in the real PTY.)
