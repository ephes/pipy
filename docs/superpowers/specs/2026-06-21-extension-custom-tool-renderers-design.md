# Extension custom tool renderers (snapshot runtime, Pi-faithful contract) — Design

Status: design drafted 2026-06-21 (brainstorming). First sub-slice of the
extension-platform **rich-UI layer** follow-on
([../../extension-api.md](../../extension-api.md), the gap audit's largest
remaining area). Tracked as extension-api **slice 17**. This spec covers
extension-owned tool rendering only; the other rich-UI siblings (chrome widgets,
rich message renderers, editor integration, theme controls, extension
state/session-manager views) are separate slices. (Update: chrome widgets
shipped as slice 18, and rich message renderers shipped as slice 19.)

## Decisions locked during brainstorming (2026-06-21)

1. **Snapshot runtime, Pi-faithful contract.** Renderers run **render-once** per
   phase (call, result) — no live invalidate/repaint loop, no partial-result
   streaming this slice. But the **extension-facing contract is the end-state
   contract**: renderers return a component and receive a shared mutable `state`.
   Deferred runtime pieces (`invalidate()`, `is_partial`) are added later as
   **additive** context surface, which the spec's versioning rule explicitly
   allows ("minor versions may add methods, events, and optional fields"). This
   avoids the lines-shortcut that slice 16 took for message renderers and that is
   now backlogged as rich-renderer follow-on **C**.
2. **Bounded theme helper.** Renderers get a small semantic styling helper over
   the active `ChromePalette` (no raw ANSI passthrough, no plain-text-only).
3. **Fields on `ExtensionTool`, own tools only.** `render_call`/`render_result`
   are optional fields on the tool definition (Pi puts renderers on the tool
   definition). Overriding **built-in** tool rows is deferred.
4. **Default framing only** (Pi's `renderShell:"default"`); `renderShell:"self"`
   deferred.

## Goal

Let a Python extension control how **its own** registered tool's call and result
rows are drawn in the product TUI (and the captured/non-TTY renderer), with
themed color, using a contract that translates cleanly from Pi's
`renderCall`/`renderResult` and forward-extends to the live-component runtime
without an API break.

## Pi reference (verified against `/Users/jochen/src/pi-mono`)

- `packages/coding-agent/src/core/extensions/types.ts`:
  - `ToolDefinition.renderCall?(args, theme, context) -> Component` (~471),
    `renderResult?(result, options, theme, context) -> Component` (~474),
    `renderShell?: "default" | "self"` (~447).
  - `ToolRenderContext` (~403): `args`, `toolCallId`, **`state: TState`** (shared
    mutable, init `{}`, stable across call & result), `invalidate()`,
    `expanded`, `isPartial`, `isError`, `cwd`, `executionStarted`, `argsComplete`.
  - `ToolRenderResultOptions` (~395): `expanded`, `isPartial`.
- Theme object exposes semantic helpers (`theme.fg(name, text)`, `theme.bold`,
  `theme.bg`) with names like `accent`/`success`/`warning`/`error`/`dim`.
- Example: `packages/coding-agent/examples/extensions/tic-tac-toe.ts` (~950–966)
  — custom `renderCall`/`renderResult` with shared state.

## Current pipy state (verified against `/Users/jochen/projects/pipy`)

- `ExtensionTool`/`ToolResult` live in `src/pipy_harness/native/extension_runtime.py`
  (re-exported from `pipy_harness.extensions`). `ToolResult` (~262) is
  `content: str` + `details: Mapping | None`.
- **`details` is dropped today:** `_ExtensionToolPort.invoke`
  (`src/pipy_harness/native/tool_loop_session.py` ~636–714) returns only
  `content` as `ToolExecutionResult.output_text`; `details` never reaches render.
- **A component protocol already ships:** `CustomComponent`
  (`extension_runtime.py` ~683) = `render(width) -> list[str]` +
  `handle_input(key)`, driven by `ToolLoopTerminalUi.run_custom_component`
  (`src/pipy_harness/native/tui.py` ~1481) as a modal overlay.
- **Tool rows are static committed scrollback:** `_TuiToolLoopRenderer`
  `render_tool_call`/`render_tool_result` (`tool_loop_session.py` ~6274/6282) →
  `ToolLoopTerminalUi.add_tool_call`/`add_tool_result` (`tui.py` ~2162/2190)
  append `("tool", …)`/`("tool_result", …)` blocks to `_history_blocks`, framed
  by `_block_frame_lines` (`tui.py` ~3014) and styled by `_styled_line`
  (`tui.py` ~2802) per line-kind. The captured path is `_ToolLoopRenderer`
  (~5899/5953).
- **Theme palette:** `ChromePalette` (`src/pipy_harness/native/themes.py` ~36),
  `resolve_palette` (~210), `chrome_style_for`. Has `accent`/`error`/`dim`/
  `section`/`title`; **no `success`/`warning`**. No theme object is passed to
  extension renderers today.
- **Slice-16 precedent:** `register_message_renderer` (~1462) +
  `render_extension_message` (~1759) — render-once, plain lines, fail-soft,
  `_CUSTOM_RENDER_MAX_CHARS`-bounded, no theme/state. This is the shortcut this
  slice intentionally does **not** repeat for tools.

## Contract (extension-facing API)

In `pipy_harness.extensions` (impl in `extension_runtime.py`):

```python
@runtime_checkable
class ToolRenderComponent(Protocol):
    """A render-once tool-row component. `render(width)` returns the row's
    content lines (the component applies its own theme styling). Aligns with the
    shipped CustomComponent (which also has render); handle_input/invalidate/
    dispose are reserved for the later live slice and not called this slice."""
    def render(self, width: int) -> Sequence[str]: ...


@dataclass(frozen=True)
class ToolRenderContext:
    tool_name: str
    args: Mapping[str, Any]              # parsed tool input
    is_result: bool                      # False in render_call, True in render_result
    is_error: bool                       # False at call phase
    content: str | None                  # provider-visible result text (None at call)
    details: Mapping[str, Any] | None    # extension ToolResult.details (None at call)
    expanded: bool                       # current Ctrl+O expansion state
    width: int                           # available content width
    theme: ToolRenderTheme               # bounded styling helper
    state: MutableMapping[str, Any]      # shared across render_call -> render_result
    # Reserved (added additively with the live slice): invalidate(), is_partial


class ToolRenderTheme(Protocol):
    def fg(self, color: ThemeColor, text: str) -> str: ...   # ThemeColor =
    def bold(self, text: str) -> str: ...                    #   text|accent|success
    def dim(self, text: str) -> str: ...                     #   |warning|error|dim


def lines_component(lines: str | Sequence[str]) -> ToolRenderComponent: ...
```

`ExtensionTool` gains two optional fields (frozen dataclass, default `None`):

```python
render_call:   Callable[[ToolRenderContext], ToolRenderComponent] | None = None
render_result: Callable[[ToolRenderContext], ToolRenderComponent] | None = None
```

Semantics:

- `render_call` runs when the call row is shown (`args` known; `content`/
  `details` are `None`; `is_result=False`). `render_result` runs when the result
  settles (`content`/`details` populated; `is_result=True`).
- Either may be omitted independently; an omitted phase uses pipy's default
  rendering.
- `state` is a single mutable dict created per tool execution and passed to both
  phases, so `render_call` can stash data `render_result` reads (Pi's
  `context.state`).
- `details` is the handler→`render_result` channel: a handler returns
  `ToolResult(content=…, details={…})`, and `render_result` reads `details`.
- Simple renderers stay one-liners:
  `render_result=lambda ctx: lines_component(ctx.theme.fg("success", "done"))`.

`ToolRenderTheme` maps semantic names → active `ChromePalette` truecolor with a
256/no-color fallback (via the existing `chrome_style_for`/`resolve_palette`
path) and always resets SGR. **Palette addition:** add `success`/`warning`
truecolor+fallback entries to the three built-in palettes (`_PI_PALETTE`,
`_HIGH_CONTRAST_PALETTE`, `_OCEAN_PALETTE`) so the semantic set is Pi-shaped
rather than aliasing onto `accent`/`error`. File-backed package themes that lack
the new keys fall back to a sane default for those two names.

## Data flow / wiring

Three touch points:

1. **Carry `details` to render time (local-only).** *Implemented:* rather than
   adding a field to `ToolExecutionResult`, `_ExtensionToolPort.invoke` writes
   `ToolResult.details` into a small in-memory **details sink** — a
   `MutableMapping` keyed by the call's `provider_correlation_id` — and only for
   tools that actually declare a `render_result`. The renderer reads that sink by
   the same key at result time. The sink is **never archived and never sent to the
   provider** — it exists only to reach the renderer for the just-executed call,
   and `provider_correlation_id` is never surfaced to extension code. `content`
   continues to bound and feed the model exactly as today. (The earlier draft
   proposed `render_details` on `ToolExecutionResult`; the sink avoids changing
   that result type and the `render_tool_result` signature.)

2. **Dispatch at render time.** `_TuiToolLoopRenderer.render_tool_call` /
   `render_tool_result` (and the captured `_ToolLoopRenderer` equivalents) look
   up the per-run extension-tool registry by `tool_name`. If the tool defines the
   matching renderer, build a `ToolRenderContext` (creating/reusing the per-call
   `state`), call the renderer, call `component.render(width)` once, and use the
   returned lines instead of the default header/result lines. *Implemented:* the
   renderer signatures are **unchanged** — `render_tool_result(*, output_text,
   is_error, duration_seconds)` keeps its existing shape and `render_tool_call`
   still just receives the `ProviderToolCall` (carrying the
   `provider_correlation_id` and `arguments_json`). The `details` reach
   `render_result` via the correlation-keyed details sink (point 1), not via a new
   parameter.

   **Per-call render slot (single, sequential).** *Implemented:* because
   `ToolRenderContext.args` and `state` are required in **both** phases but the
   result path does not otherwise carry the parsed args, `render_tool_call`
   stashes a single **pending-render slot** holding the call's
   `provider_correlation_id`, the **parsed call args**, and the shared **`state`**
   dict; `render_tool_result` consumes and clears it. This relies on the same
   sequential call→result ordering the existing `_last_tool_name` field already
   assumes (one tool call is rendered immediately before its result), so a single
   slot suffices instead of a `provider_correlation_id`-keyed store. The slot is
   cleared at the start of the next call and when a result renders, so it cannot
   grow. If parallel tool rendering is added later, replace the single slot with a
   correlation-keyed store.

3. **Commit pre-styled lines.** Renderer lines already carry the helper's SGR, so
   they commit under a new line-kind `tool_result_custom` (and a call-phase
   `tool_call_custom`) that applies the standard block framing (leading/trailing
   blanks + band/prefix via `_block_frame_lines`/`_styled_line`) **but preserves
   embedded SGR** — `_visible_len_allow_sgr` already tolerates inline SGR for
   width math. This keeps custom rows visually native (Pi's `renderShell:"default"`).

Runs in both the TTY (`_TuiToolLoopRenderer`) and captured/non-TTY
(`_ToolLoopRenderer`) paths so output is consistent; the theme helper degrades to
plain text when color is unavailable.

## Framing, failure, bounds, privacy

- **Framing:** default block framing only (`renderShell:"default"`).
  `renderShell:"self"` (renderer owns the whole frame, no band/prefix) is
  deferred.
- **Fail-soft:** a renderer that raises, returns a non-component / non-`render`
  object, or whose `render()` raises or returns a value that is neither a `str`
  nor a `Sequence` → fall back to the **default** rendering for that phase, plus a
  bounded local diagnostic (same posture as `render_extension_message`). A broken
  renderer never aborts the turn.
- **Line coercion (avoid char-per-line).** `render()`'s return is normalized
  exactly as `lines_component` normalizes its input: a bare **`str` is treated as
  one logical value split on `\n`** — never iterated character-per-line (note
  `str` *is* a `Sequence[str]`, so the implementation must special-case `str`
  before the generic sequence path); any other `Sequence` is coerced element-wise
  with `str(...)`. `bytes`/`bytearray` are rejected to fallback. This is the one
  shared coercion used by both `lines_component` and the raw `render()` path.
- **Bounds:** renderer output is capped by total chars (reuse
  `_CUSTOM_RENDER_MAX_CHARS`) and a max line count, then width-clipped like every
  block. `expanded` lets a renderer choose compact vs full output itself
  (it is *informed by* the Ctrl+O state; this slice does not add a new toggle).
- **Privacy (unchanged rules):** renderer output is UI text → **not archived**;
  `details` (carried via the in-memory correlation-keyed sink) and `state` are
  local-only → not archived, not sent to the provider. Archive metadata stays the
  existing safe set (tool name, safe counts, policy outcomes).

## Implementation slices

1. **Contract + theme helper.** Add `ToolRenderComponent`, `ToolRenderContext`,
   `ToolRenderTheme`, `lines_component`, and the two `ExtensionTool` fields to
   `extension_runtime.py` / `pipy_harness.extensions`. Add `success`/`warning` to
   the palettes and implement the helper over `chrome_style_for`. Pure additions;
   unit-tested in isolation (theme degradation truecolor/256/no-color, helper
   reset, `lines_component`).
2. **Carry `details` + per-call `state`.** Add a `provider_correlation_id`-keyed
   in-memory details sink, written by `_ExtensionToolPort.invoke` (no change to
   `ToolExecutionResult`); the renderer keeps a per-call slot (set in
   `render_tool_call`, consumed and evicted in `render_tool_result`) holding the
   shared `state`. Tests: `details` reaches a render context; `state` is shared
   call→result; the sink entry is evicted on consume; nothing new is archived.
3. **TTY dispatch + commit.** Wire `_TuiToolLoopRenderer` render paths, the
   `tool_call_custom`/`tool_result_custom` line-kinds, framing, fallback, bounds.
   Real-PTY test at 80×24: a custom result row renders with color and survives a
   Ctrl+O `expanded` toggle (mirrors the `custom()` PTY tests).
4. **Captured-path parity.** Wire `_ToolLoopRenderer`; assert the same lines
   (color-stripped) appear in captured output.
5. **Golden conformance + example + docs.** Extend the conformance extension and
   add `scripts/parity_checks/extension_tool_renderer_conformance.py --json`
   proving: `render_call`/`render_result` fire, `details`/`state` thread through,
   fallback-on-crash works, and no UI text / `details` leak to the archive. Add an
   example under `docs/examples/extensions/` (a `render_result` that draws a
   themed key/value table). Update `docs/extension-api.md` (new shipped slice-17
   entry; trim the rich-UI follow-on list), `docs/pi-mono-gap-audit.md`,
   `docs/parity-plan.md`, and `CHANGELOG.md`.

## Conformance gate & testing

```sh
uv run python scripts/parity_checks/extension_tool_renderer_conformance.py --json
just check
```

Per-slice unit/PTY tests as listed; `just check` green at each slice end; Pi
review loop per slice, commit only on CLEAN; final slice also runs
`just docs-build`.

## Out of scope (deferred follow-ons)

- **Live runtime:** `invalidate()`-driven repaint, `is_partial` streaming,
  animated tool rows. Added additively to `ToolRenderContext` later — no API
  break. Requires the paint-model change to re-render committed rows (or pin the
  row in the live region until settle).
- **`renderShell:"self"`** (renderer owns the full frame).
- **Built-in tool override** (re-skinning `bash`/`read`/`edit`/`grep` rows; Pi's
  `built-in-tool-renderer` example). Would need a separate name-keyed renderer
  registry over the native default-render path + precedence rules.
- The other rich-UI siblings (chrome widgets, rich message renderers, editor
  integration, theme controls, extension state/session-manager views).

**Known assumption (implemented):** the extension tool-renderer **map is built
once per session** and is **not refreshed across `/reload`** — the renderer is
constructed when the session starts, so renderers added or changed by a reloaded
extension are not picked up until restart (the details sink *is* wired on the
reload path). This is consistent with the reload Open Question in
`docs/extension-api.md`; refreshing the renderer map on reload is a follow-on.

## Risks

- **Embedded SGR through the block pipeline.** Custom lines carry SGR into
  `_block_frame_lines`/`_styled_line`, which were built for plain text + a single
  outer style. Mitigation: a dedicated `*_custom` line-kind that applies only the
  band/prefix and trusts `_visible_len_allow_sgr` for width; cover wrap/clip with
  embedded SGR in unit tests.
- **Per-call `state` lifetime.** *Implemented* as a single pending-render slot
  (not a keyed store): created at call and cleared after result / at the next
  call, relying on the existing sequential call→result ordering, so it cannot grow
  unbounded. Tested for cleanup. Parallel tool rendering would require a
  correlation-keyed store instead.
- **Palette additions.** Adding `success`/`warning` touches the three built-in
  palettes and the theme-file loader. Mitigation: default fallback for theme
  files missing the keys; snapshot the three built-ins in tests.
- **Captured/TTY drift.** Two render paths must agree. Mitigation: shared
  dispatch helper producing the line list once; both paths consume it (TTY styles
  in place, captured strips color).
```