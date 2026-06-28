# Extension `send_message` Delivery Plan

## Gap

Pi's extension `sendMessage` appends a custom message and can also route that
message into the agent conversation depending on `options`:

- `triggerTurn: true` while idle starts a provider turn with the custom message
  as the prompt message.
- `deliverAs: "nextTurn"` queues the custom message so it is injected alongside
  the next user prompt.
- `deliverAs: "steer" | "followUp"` is used while streaming; pipy's current
  single-turn loop has no mid-stream steering/follow-up channel, so these modes
  should remain accepted and stored/displayed without changing provider flow.
- With no delivery option, Pi stores/displays the custom message and does not
  trigger a provider turn.

Pipy already accepts `api.send_message` / `api.sendMessage` and
`ctx.send_message` / `ctx.sendMessage`, stores `custom_message` entries, and
renders displayed text. The missing reviewable slice is provider-visible idle
`triggerTurn` and `nextTurn` behavior through pipy's Python-owned native
session loop.

Relevant Pi references:

- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/extensions/types.ts`
  (`sendMessage` option shape)
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/agent-session.ts`
  (`sendCustomMessage`, `_pendingNextTurnMessages`, prompt injection)
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/messages.ts`
  (`CustomMessage` converts to a user-role LLM message)

## Design

Keep the persisted native session-tree shape unchanged: custom messages continue
to be archived as `custom_message` entries with bounded `customType`, `content`,
`display`, and `details`. Add only an in-memory delivery queue in
`ToolLoopNativeSession.run`:

1. `extension_send_message(...)` still appends and renders the custom message.
2. If `options.triggerTurn` is true and the call happens while idle, enqueue the
   custom message content as the next deterministic provider prompt. This matches
   Pi's idle `triggerTurn` effect while preserving pipy's existing string-only
   `UserMessage` provider envelope.
3. If `options.deliverAs == "nextTurn"`, enqueue the custom message content in a
   pending-next-turn list instead of starting a turn immediately. When the next
   real/seeded/extension user prompt is accepted, append those pending messages
   to the provider-visible `messages` history immediately after the turn's user
   message, then clear the list. Persisted archive state remains the original
   custom message entry plus the real user prompt entry; the injected context is
   live provider context only, matching pipy's metadata/privacy boundary.
4. Continue to accept `steer` and `followUp` without provider-flow changes,
   because pipy has no concurrent streaming extension steering channel yet.

## Done when

- Focused tests prove `triggerTurn` custom messages cause a deterministic
  provider request, and `nextTurn` custom messages are visible to the next
  provider request without creating a standalone provider turn.
- `scripts/parity_checks/extension_package_conformance.py --json` covers the new
  behavior.
- Docs and parity tracking describe the shipped slice and leave streaming
  steer/follow-up as deferred.
- `just check` and the different-family review gate are clean over the final
  diff.
