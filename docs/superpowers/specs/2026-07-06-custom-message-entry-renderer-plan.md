# Extension CustomMessageEntry Renderer Plan

Gap: render persisted `CustomMessageEntry` values with a registered message renderer instead of always replaying their stored content. This is a single extension-platform follow-on from `docs/backlog.md` and `docs/pi-mono-gap-audit.md`.

Pi reference:
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts`: `registerMessageRenderer<T>(customType, renderer)` registers a renderer for `CustomMessageEntry`; `sendMessage` accepts `customType`, `content`, `display`, and `details`.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/agent-session.ts`: `sendCustomMessage` persists/emits a custom message with `customType`, `content`, `display`, `details`, and `timestamp`; hidden messages are not displayed.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/interactive-mode.ts`: for role `custom`, if `message.display`, Pi fetches `extensionRunner.getMessageRenderer(message.customType)` and builds `CustomMessageComponent(message, renderer, ...)`.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/interactive/components/custom-message.ts`: the renderer receives the full `CustomMessage` object plus `{expanded}` and theme, not only `content` or `details`; if it returns a component, the custom component replaces the default box. If absent/failing/undefined, Pi falls back to default `[customType]` + message content rendering. Expansion changes rebuild the component.

Current pipy state:
- `CustomMessageEntry` persists `custom_type`, `content`, `display`, and JSON-safe `details` in `src/pipy_harness/native/session_tree.py`.
- `api.register_message_renderer` / `ctx.append_entry` already render `CustomEntry` values through `render_extension_message`, including the rich two-arg `(data, ctx)` component path.
- `ctx.send_message` currently displays and replays visible `CustomMessageEntry` values only as plain stored `content` lines in `NativeToolReplSession`; `_custom_entry_redraw_rows` also returns plain rows for displayable custom messages.

Design:
1. Keep persistence unchanged: renderer output stays live-only; session files continue to store only `custom_type`, `content`, `display`, and JSON-safe `details`.
2. Add a helper that converts a `CustomMessageEntry` into a Pi-shaped JSON-safe renderer payload: `{customType, content, display, details}`. This preserves the Pi field list and optionality. `details` is already safe-bounded at message creation/read; `display=False` messages must still be skipped.
3. Reuse the existing `render_extension_message` dispatcher for `CustomMessageEntry` display/replay/redraw. For registered rich two-arg renderers, the data passed to the renderer will be the full payload above, so extensions can inspect `content`, `details`, and `display` like Pi. One-arg renderers keep existing plain behavior with that payload.
4. Apply the helper in all live CustomMessageEntry display paths: immediate `extension_send_message`, startup replay, and in-session `/resume` redraw (`_custom_entry_redraw_rows`). For terminal UI styled results use `add_custom_entry_styled`; otherwise use `add_custom_entry(custom_type, lines)`. Captured/non-TTY diagnostics should render through the same helper at fixed width 80/expanded false.
5. Preserve fallback behavior: no registered renderer continues showing stored `content` as Pi's default message body; unknown/failing renderers remain fail-soft and bounded. Hidden messages remain non-displayed.

Done when:
- Focused tests prove immediate `send_message`, startup/replay redraw row generation, and hidden-message behavior.
- Tests prove rich renderers receive `content`/`details` from a `CustomMessageEntry` and can emit styled rows, while no registered renderer still displays stored content.
- Extension message-renderer conformance, `just check`, and the different-family review gate pass.
