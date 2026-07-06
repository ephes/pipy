# Extension rich message resume/redraw parity plan

Gap: Extension rich message resume/redraw follow-on (from `docs/backlog.md` Next Slice).

Pi reference paths:

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/interactive-mode.ts`
  - `handleResumeSession(...)` calls `this.renderCurrentSessionState()` after a successful session switch.
  - `renderCurrentSessionState()` clears the chat and calls `renderInitialMessages()`.
  - `renderInitialMessages()` rebuilds visible messages from `sessionManager.buildSessionContext()`.
  - `addMessageToChat(...)` handles `role: "custom"` by looking up `this.session.extensionRunner.getMessageRenderer(message.customType)`, creating a `CustomMessageComponent`, and adding it only when `message.display` is true.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/components/custom-message.ts`
  - `CustomMessageComponent` attempts the registered renderer first; if absent or failing, it falls back to a default custom-message box.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/session-manager.ts`
  - `appendCustomMessageEntry(...)` persists `customType`, `content`, `display`, and `details` in the session tree; renderer output is not persisted.

Current pipy behavior:

- `tool_loop_session.py` already replays active-branch custom entries into a startup-opened TUI session with `replay_custom_entries_to_terminal()`.
- `ctx.append_entry(...)` persists JSON-safe `_CustomEntry` data and renders through the current run's `extension_renderer_map` with `render_extension_message(...)`.
- `api.send_message(...)` persists `_CustomMessageEntry` and displays its content when `display=True`; this path remains plain-content display and should not be expanded in this slice.
- `/resume` and picker-based session switching open a new `NativeSessionTree` and call `rebuild_messages_from_tree()`, but the live TUI scrollback is not rebuilt with custom entries from the newly active branch.

Design:

1. Introduce a small product-TUI redraw helper in `tool_loop_session.py` that, when a live `terminal_ui` exists, clears the currently displayed custom-entry rows and replays active-branch extension entries from `session_tree.get_branch()`.
2. Use the existing renderer map for the current run. For `_CustomEntry`, call `render_extension_custom_entry(...)` so two-arg rich renderers receive current width, expansion state, and theme; renderer output remains live-only and fail-soft. For `_CustomMessageEntry` with `display=True`, keep the existing plain `content.splitlines()` fallback.
3. Invoke the redraw helper after successful in-session `/resume` switches (direct argument and picker) once `session_tree` has been replaced and `rebuild_messages_from_tree()` has run. Keep startup-open replay unchanged.
4. Keep scope narrow: do not add live per-frame invalidate, multi-widget message components, full custom editor integration, OAuth auth wiring, or PyPI/npm sources.

Done-when criteria:

- Focused coverage proves a live terminal UI clears stale custom rows and replays only the newly active branch's custom entries after `/resume`, using the registered renderer of the current run.
- `scripts/parity_checks/extension_message_renderer_conformance.py --json` covers the resume/redraw helper in addition to renderer dispatch/coercion.
- `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, and `docs/backlog.md` describe the shipped resume/redraw behavior and remove this gap from the selected Next Slice.
- Required gates pass: `uv run python scripts/parity_checks/extension_message_renderer_conformance.py --json`, `uv run python scripts/parity_checks/extension_conformance_gate.py --json`, and `just check`.
