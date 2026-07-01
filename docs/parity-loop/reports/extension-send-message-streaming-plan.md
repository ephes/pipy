# Plan: extension `send_message` streaming delivery modes

## Gap

Pipy already supports Pi-shaped extension `send_message` / `sendMessage` for local custom-message entries, idle `triggerTurn`, and `deliverAs: "nextTurn"`, but `deliverAs: "steer"` and `deliverAs: "followUp"` remain deferred. This slice adds only the command/shortcut/live-session plumbing needed for custom messages to join the existing queued steering/follow-up delivery path when an extension calls `send_message(..., {deliverAs: "steer"|"followUp"})`.

## Pi reference

Pi source: `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/agent-session.ts` `sendCustomMessage` (around lines 1301-1330) and `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts` `ReplacedSessionContext.sendMessage`.

Pinned behavior for this slice:

- Message fields: `customType` (required custom type), `content` (provider-visible content), `display` (optional/bool in pipy coercion, Pi message field), and optional `details` are normalized into a custom role message/entry.
- Options: `triggerTurn?: boolean`; `deliverAs?: "steer" | "followUp" | "nextTurn"`.
- Pi delivery:
  - `deliverAs === "nextTurn"`: queue the custom message for the next accepted turn; no immediate turn.
  - while streaming, `deliverAs === "followUp"`: call `agent.followUp(appMessage)`.
  - while streaming, any other streaming custom message, including explicit `"steer"`: call `agent.steer(appMessage)`.
  - not streaming + `triggerTurn`: run a new agent prompt with the custom message.
  - not streaming without `triggerTurn`: append a custom message entry and emit local message events, no provider turn.

## Pipy implementation shape

Pipy does not carry typed provider messages through the TUI queue; queued steering/follow-up text is provider-visible prompt text. Match the existing pipy-owned boundary by queuing the custom message `content` string into the already-shipped `ToolLoopTerminalUi` pending steering/follow-up drain.

Implementation tasks:

1. Extend the `extension_send_message` closure in `tool_loop_session.py` so `deliverAs`/`deliver_as` values `"steer"` and `"followUp"` / `"follow_up"` are recognized in addition to `"nextTurn"`.
2. Append the custom message entry and render local display exactly as today before any queueing.
3. For `"steer"`, enqueue `content` ahead of the normal steering/follow-up drain, using the same provider-visible path as user Alt+Enter/Ctrl+J steering. If a provider turn is active, this should be picked up at the next safe drain/interrupt point; if idle, it should become the next queued prompt rather than a local slash command.
4. For `"followUp"` / `"follow_up"`, enqueue `content` after steering and before later fresh input using the existing follow-up drain order.
5. Preserve existing behavior for `"nextTurn"`, idle `triggerTurn`, hidden display, and default no-turn custom entry append.

## Done when

- Focused tests prove `ctx.send_message(..., {deliverAs: "steer"})` and `ctx.send_message(..., {deliverAs: "followUp"})` queue provider-visible content through the TUI/session drain in order without treating it as a local command.
- Existing custom-message tests continue to pass.
- `scripts/parity_checks/extension_package_conformance.py --json` and `just check` pass.
- Docs remove the `streaming steer/followUp` deferral for `send_message` and mark this narrow slice shipped while keeping broader custom-message rendering/redraw deferrals intact.
