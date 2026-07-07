# Live custom message renderer re-render design

## Gap

Pipy's rich custom message renderer path currently renders a custom-entry component once when the entry is appended or replayed. Pi keeps a `CustomMessageComponent` in the chat tree and calls `rebuild()` when the message component is invalidated or when the tool-output expanded flag changes, so a registered `MessageRenderer(message, { expanded }, theme)` can produce different rows for collapsed versus expanded display. Pipy already carries the live view flag as `ToolLoopTerminalUi.tools_expanded` and passes it into `render_extension_message(...)` at append/replay time, but committed `custom_message_custom` rows do not refresh when Ctrl+O (or extension `ctx.ui.set_tools_expanded`) changes the flag.

## Pi reference

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts:1056-1063`: `MessageRenderer<T>` is called with `(message: CustomMessage<T>, options: { expanded: boolean }, theme: Theme)` and returns one `Component | undefined`.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/components/custom-message.ts`: `CustomMessageComponent` stores the message/renderer, `setExpanded(expanded)` calls `rebuild()` when the flag changes, and `invalidate()` also calls `rebuild()`. `rebuild()` removes the prior custom/default component and invokes the registered renderer with the current `expanded` value; renderer failure falls back to the default boxed rendering.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/interactive-mode.ts:3068-3075`: displayed custom messages are materialized as `CustomMessageComponent`, and the interactive mode seeds their expanded state from the current `toolOutputExpanded` flag.

## Pipy ownership boundary

Keep this in pipy's Python-owned TUI/session boundary. Do not add new session-tree fields: Pi's re-render is live UI behavior, not persisted session data. The native product session tree continues to persist only JSON-safe custom entry payloads, and rendered rows remain live-only.

## Implementation sketch

1. Add a small retained custom-message snapshot to `ToolLoopTerminalUi` for rich/styled custom entries only: custom type, JSON-safe data payload, rendered lines, renderer map reference/callable, and the expansion value used for the current lines. Plain slice-16 custom entries keep their existing committed rows.
2. When `add_custom_entry_styled(...)` commits a styled custom message, accept optional metadata needed to re-render later. Store it with the history block so a later rebuild can replace the same block instead of appending a duplicate. Startup replay and `/resume` redraw should set the same metadata for styled entries.
3. Add a `rerender_custom_messages()` helper on the TUI. It walks retained styled custom-message blocks, calls `render_extension_message(..., expanded=self.tools_expanded, width=current_width, theme=current_theme)`, and replaces each styled block with the refreshed styled lines. If a renderer is now absent, fails, or returns plain fallback, use the existing safe fallback rows for that entry rather than leaking raw data or exception details.
4. Invoke the helper when Ctrl+O toggles `tools_expanded` and when `TerminalUiDriver.set_tools_expanded(...)` is called by an extension context, then repaint. Width changes already repaint the frame; this slice may re-render using the current width at toggle time and leaves broader per-resize custom-message invalidation as deferred unless it is trivial.
5. Keep disposal/multi-widget/component lifetime out of scope. Pipy's rich renderer remains a bounded render-to-lines component surface; the slice only refreshes its snapshot when the Pi-visible expanded flag changes.

## Done when

- A focused unit or session test proves a two-argument `register_message_renderer` sees `ctx.expanded=False` at first render and that toggling Ctrl+O / setting tools-expanded to true refreshes the existing custom-message block to the `expanded=True` rendering without appending a duplicate.
- A regression test proves a re-render failure is fail-soft and does not leak custom data or exception text.
- Existing custom-entry persistence remains unchanged: exported/session JSON stores only the custom entry data, not rendered rows.
- Docs (`docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, `docs/backlog.md`, and release notes if applicable) describe live expanded-flag re-rendering and keep resize/multi-widget follow-ons scoped as deferred.
