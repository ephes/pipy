# Extension Custom Entry Replay Plan

Status: design for parity-loop slice

## Gap

Pipy extensions can register `api.register_message_renderer(...)` and append
custom entries with `ctx.append_entry(...)`. Those entries are persisted in the
native product session tree and render immediately when appended, but the
extension API spec still marks replaying custom entries into a startup-opened
TUI session as deferred.

Pi treats extension custom messages as conversation/session entries:
`core/messages.ts` creates a `CustomMessage`, and
`modes/interactive/components/custom-message.ts` renders it from persisted
message content plus the registered renderer. Pipy should match that semantic
shape through its Python-owned session tree and TUI history model.

## Scope

Implement a narrow replay-on-open slice for existing pipy custom entry
surfaces:

1. When a `NativeSessionTree` is supplied at TUI startup, replay active-branch
   custom entries into `ToolLoopTerminalUi` history before accepting new input.
2. Render `CustomEntry` through the registered message renderer when one is
   available, using the same plain/component fail-soft rules as the live
   append path.
3. Render legacy `CustomMessageEntry` with its stored display content only when
   `display` is true.
4. Keep replay live-only: do not mutate the session file, do not write rendered
   bodies to metadata-first archives, and do not add provider-visible context
   beyond the session tree's existing context reconstruction.
5. Keep the slice to replay. Do not implement `send_message`, `deliverAs`,
   `triggerTurn`, multi-widget message components, live invalidate, or a custom
   editor component in this change.

## Implementation Plan

1. Add a small replay helper near the tool-loop session startup path that walks
   the active branch from the loaded `NativeSessionTree`.
   Acceptance: ordinary user/assistant/tool transcript reconstruction remains
   unchanged.
2. For each active `CustomEntry`, call the existing renderer dispatch helper
   used by `extension_append_entry(...)`, then append either
   `terminal_ui.add_custom_component_entry(...)` or `terminal_ui.add_custom_entry(...)`.
   Acceptance: rich renderers get a `MessageRenderContext` with the startup
   width, plain renderers preserve the current sanitized text path, and renderer
   failures fall back without leaking exception text.
3. For each active `CustomMessageEntry`, append stored content through
   `terminal_ui.add_custom_entry(...)` only when `display` is true.
   Acceptance: hidden custom messages remain session/provider data but do not
   appear in the replayed TUI history.
4. Add focused unit/product tests that create a native session file with custom
   entries, reopen it, and prove replayed TUI history includes rendered custom
   rows without duplicating entries or writing new session rows.
   Acceptance: tests cover registered renderer, unknown renderer fallback, rich
   component renderer, `display=false`, and active-branch filtering if the tree
   has off-branch custom entries.
5. Update `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`,
   `docs/parity-plan.md`, and `docs/backlog.md` to mark startup-opened custom
   entry replay as shipped while leaving the remaining message-entry surfaces
   deferred.
   Acceptance: docs still list `send_message`/`deliverAs`/`triggerTurn`,
   `CustomMessageEntry` renderer parity beyond stored display, multi-widget
   components, and live invalidate as follow-ons.

## Verification

Run the focused tests for extension message rendering and session-tree resume,
then the existing extension gates:

```sh
uv run pytest tests/test_native_extension_message_renderer.py tests/test_native_tool_loop_session.py tests/test_native_tool_loop_session_tree.py
uv run python scripts/parity_checks/extension_message_renderer_conformance.py --json
uv run python scripts/parity_checks/extension_conformance_gate.py --json
just check
```

## Done When

- Startup-opened native TUI sessions show active-branch extension custom entries
  with the best available registered renderer.
- The session file is not changed by replay itself.
- Existing append-time rendering behavior and provider-context reconstruction
  do not regress.
- Docs and parity status accurately describe the shipped replay slice and the
  still-deferred message-entry APIs.

## Task Breakdown

1. Add a shared local custom-entry rendering helper in
   `NativeToolReplSession.run(...)`.
   Acceptance: `ctx.append_entry(...)` output is unchanged.
2. Replay `CustomEntry` and displayable `CustomMessageEntry` rows from
   `session_tree.get_branch()` into `ToolLoopTerminalUi` after startup paint.
   Acceptance: off-branch custom entries are not replayed.
3. Add a product-path test with a mocked TUI that proves rich renderer replay,
   plain renderer replay, unknown renderer fallback, legacy display suppression,
   active-branch filtering, and no session-file mutation.
4. Update parity docs and run focused gates before the final `just check`.
