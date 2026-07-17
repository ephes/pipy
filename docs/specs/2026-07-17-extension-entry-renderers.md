# Durable Extension Entry Renderers

Status: shipped 2026-07-17

## Gap and scope

Pipy already persists `ctx.append_entry(custom_type, data)` as a native-session
`CustomEntry`, but it incorrectly routes those records through the
`register_message_renderer` registry and even renders a diagnostic in headless
modes. This slice ports Pi's separate `registerEntryRenderer` ownership for
durable, non-LLM session entries. It adds the Python-shaped
`api.register_entry_renderer(custom_type, renderer)` registration surface and
uses it only when the product TUI displays or replays an `append_entry` record.

The slice does not change `register_message_renderer` or `send_message`, add
dynamic tool loading, realign package updates, add a general component library,
or change tree-selector/export summaries. It also does not make custom entries
part of provider context.

## Pi reference contract

The reference is `/Users/jochen/src/pi-mono` at the revision recorded in
`docs/pi-mono-gap-audit.md`.

- `packages/coding-agent/src/core/extensions/types.ts:1125-1137` defines
  `EntryRenderer<T>` as `(entry, { expanded }, theme) => Component | undefined`.
  The first argument is the complete durable `CustomEntry`: `type`, `id`,
  `parentId`, `timestamp`, `customType`, and optional `data`.
- `types.ts:1256-1261` and `loader.ts:282-286` register the renderer by custom
  type in an entry-specific map. `runner.ts:560-568` resolves the first matching
  renderer independently from message renderers.
- `agent-session.ts:2357-2364` persists `appendEntry` through the session manager
  and emits an ordinary `entry_appended` event. Rendering does not own storage.
- `modes/interactive/interactive-mode.ts:3148-3168` looks up an entry renderer
  only while adding a `custom` entry to the interactive chat. With no renderer,
  or when the renderer returns `undefined`, the record is not displayed.
- `modes/interactive/components/custom-entry.ts:8-58` rebuilds the component
  with the current expanded flag and theme, including on invalidation. Renderer
  exceptions fail soft in the UI. `interactive-mode.ts:3288` replays active-
  branch custom entries through the same path.
- `modes/interactive/components/tree-selector.ts:833-839` keeps its generic
  `[custom: <type>]` summary instead of invoking the renderer. Print, JSON, and
  RPC modes likewise do not own this TUI component surface.

## Pipy design

### Registration and activation

Add a `RegisteredEntryRenderer` contribution and a separate entry-renderer map
to `ActivatedExtension` and `_ExtensionRuntime`. Registration uses the same
bounded custom-type validator and callable requirement as message renderers,
but duplicate detection is independent: one extension may register both a
message renderer and an entry renderer for the same type. Two entry renderers
for one type fail closed with entry-specific `invalid_entry_renderer` or
`duplicate_entry_renderer` activation reasons. Pending pre-trust activation and
reload must carry the separate registry through the same ordering/collision
checks as other contributions.

### Renderer input and output

The entry renderer receives a detached, JSON-safe mapping for the full stored
entry, with Pi-shaped field names:

- required: `type="custom"`, `id`, `parentId`, `timestamp`, `customType`;
- optional: `data` (present even when its value is `None`, matching the stored
  pipy record's explicit data field).

Its second required positional argument is an `EntryRenderContext` containing
`expanded`, `width`, and `theme`. Unlike the older Python convenience
`register_message_renderer` surface, there is no one-argument compatibility
mode: the Pi-shaped entry renderer is called with `(entry, ctx)`. Its component
return uses the existing bounded `render(width)` contract and SGR-preserving
TUI path. `None` means no visible row. Invalid returns and exceptions fail soft
without exposing exception text or record data; they also produce no visible
custom-entry row, matching Pi's absence semantics more closely than a generic
fallback.

The stored `CustomEntry` remains the source of truth. The renderer receives a
copy, so mutation cannot alter the session tree. Rendered lines stay live-only
and never enter JSONL, JSON/RPC stdout, provider context, or the summary-safe
archive.

### TUI ownership and lifecycle

`extension_append_entry` always validates and persists the record. It invokes
the entry renderer only when `terminal_ui` exists. In print, JSON, and RPC modes
it emits no renderer output or diagnostic. In the product TUI:

1. a newly appended entry is rendered with the stored entry metadata;
2. startup replay and successful `/resume` redraw use the same stored-entry
   dispatch;
3. Ctrl+O / `ctx.ui.set_tools_expanded(...)` rerenders retained rich entry
   components with the current `expanded` value;
4. `/reload` swaps to the new entry-renderer registry and redraws the active
   branch, so removed renderers remove their live rows and changed renderers
   replace them without altering the session file.

Message-renderer state and entry-renderer state remain distinct even if their
custom types match. Displayed `CustomMessageEntry` records continue through the
message registry and retain their current plain fallback; `CustomEntry` records
without a valid entry component are omitted from the chat. Tree-selector and
export representations remain generic durable-record summaries.

## Verification and done-when

Focused tests will prove:

1. valid registration/export metadata, invalid/callable validation, independent
   same-type message+entry registration, duplicate entry failure, pending
   activation finalization, and reload collection;
2. exact full-entry field delivery as a detached JSON-safe copy, current width/
   expanded/theme context, component rendering, `None` omission, and bounded
   fail-soft behavior;
3. append persistence with no renderer, renderer invocation only in the TUI,
   no stderr/protocol/archive/provider-context leak in print/JSON/RPC paths;
4. live append, startup replay, `/resume`, Ctrl+O rerender, and `/reload`
   add/change/remove behavior without duplicate records or session-file writes;
5. message and entry renderers with the same type remain independently routed.

Update `docs/extension-api.md`, `docs/backlog.md`, `docs/parity-plan.md`,
`docs/pi-mono-gap-audit.md`, and `CHANGELOG.md`; add a focused parity check and
include it in the extension golden gate if it contributes a new durable marker.
The slice is done when focused tests and conformance checks pass, `just check`
passes, prek passes if configured, a direct different-family review returns
`CLEAN` over the exact complete diff, and the result is committed on `main`.
