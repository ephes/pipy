# DF1-F6: persist aborted and failed assistant turns

Backlog: `docs/backlog.md` follow-on **DF1-F6**. Reference: `~/src/pi-mono` at
`4df157433`.

## What Pi does

- `packages/agent/src/agent-loop.ts:241-256`: every provider turn yields one
  `AssistantMessage`. When the stream ends with `stopReason: "aborted"` (the
  abort signal fired) or `"error"` (provider error, also mid-stream), the loop
  still pushes the message, emits `message_end`, `turn_end` and `agent_end`
  with it, and stops. The message keeps the content streamed so far and an
  `errorMessage`.
- `packages/coding-agent/src/core/agent-session.ts:1097-1117`: every
  `message_end` for a user/assistant/toolResult/system message is persisted,
  so the aborted or errored assistant lands in the session file.
- `packages/ai/src/api/transform-messages.ts:189-201`: every provider adapter
  runs `transformMessages`, which skips assistant messages whose `stopReason`
  is `"error"` or `"aborted"` ("incomplete turns that shouldn't be
  replayed"). The next request therefore carries the earlier user message
  followed directly by the new one.
- Rendering, `modes/interactive/components/assistant-message.ts:178-201`:
  after any partial text, a message without tool calls shows
  `Operation aborted` (or its `errorMessage` unless that is
  `Request was aborted`) for `aborted`, and `Error: <errorMessage or
  "Unknown error">` for `error`, both in the error color. The same component
  draws live turns and restored history (`renderSessionItems`,
  `interactive-mode.ts:3900-3945`).
- `/tree` (`tree-selector.ts:344-353, 788-799`): an assistant entry with no
  text is shown when it is aborted or errored: `assistant: (aborted)` or
  `assistant: <errorMessage, first 80 chars>`.
- JSON/RPC: `message_end`, `turn_end` and `agent_end.messages` carry the
  message with `stopReason` and `errorMessage`. Every Pi assistant message
  has a `stopReason` (`stop`, `toolUse`, `length`, `error`, `aborted`).
- Extension `stopReason === "stop"` checks treat aborted/error as not
  complete.

## What pipy does today

`AgentLoop._settle_provider_cancellation` and `_settle_provider_failure`
emit an empty `AgentAssistantMessage` and never append it to history;
`ProductSessionEventProjection` suppresses the assistant after
`RunCancelled`/`ProviderFailed`. The streamed partial text is lost, the tree
holds two consecutive user messages, and resume shows no marker.

## Design

1. **Message model** (`native/agent/messages.py`): add
   `AgentStopReason(StrEnum)` with `ABORTED = "aborted"`, `ERROR = "error"`,
   and two optional fields on `AgentAssistantMessage`:
   `stop_reason: AgentStopReason | None = None` (None = a completed turn, Pi
   `stop`/`toolUse`) and `error_message: str | None = None`. Invariants: an
   `error_message` requires a `stop_reason`; a stopped message has no tool
   calls (pipy has no partial tool calls at cancel/failure time, so the
   replay filter can never orphan a tool result). `coding/state.py`
   exact-message validation checks the new field types.
2. **Partial text** (`native/agent/loop.py`): `_run_iteration` passes the
   provider turn a small recording sink that forwards every event unchanged
   and accumulates this turn's `AssistantTextDelta` text; a `RetryScheduled`
   clears it (a retried attempt starts a new message). The recorded text is
   exactly what the delta gate admitted, i.e. what the user saw. It is capped
   at `CONTENT_MAX_LENGTH`. `provider_turn.py` and the retry loop are not
   touched.
3. **Aborted turn**: `_settle_provider_cancellation` builds
   `AgentAssistantMessage(partial, stop_reason=ABORTED)` (no error message),
   emits `RunCancelled`, `MessageCompleted` and `TurnCompleted(CANCELLED)`
   with it, and appends it to run history (`appended_messages` and
   `state.history`), so `agent_end.messages`, the run result and the mirrored
   coding history include it. Applies to every cancellation reason
   (operator abort, steering, local command, provider-cancelled, and a
   cancellation during request preparation, where the partial is empty).
4. **Failed turn**: `_settle_provider_failure` does the same with
   `stop_reason=ERROR`, `error_message=failure.message.value` (the string
   inside the failure's `ProductContent`; the test asserts it), content = the
   streamed partial, else `result.final_text`, else empty. Only the terminal
   result that reaches the loop is recorded; retried attempts stay inside the
   provider-turn executor (DF1-F4's area).
5. **Persistence** (`agent_adapters.ProductSessionEventProjection`): the
   post-failure/cancel suppression now drops only an assistant *without* a
   stop reason (the one-shot compatibility path in `session.py` still emits
   a synthetic empty one). `session_tree.py` JSON writes `stop_reason` and
   `error_message` only when set (old files load unchanged); unknown values
   are rejected like other malformed fields.
6. **Replay filter** (single shared point):
   `agent_loop_policy.materialize_provider_request` is the one funnel that
   turns every agent-loop request snapshot into the `ProviderRequest` handed
   to a provider adapter (all families: OpenAI completions/responses/Codex,
   Anthropic/Bedrock, Google/Vertex, Mistral, OpenRouter, Cloudflare, fake).
   It drops assistant messages with a stop reason from `messages` (Pi
   `transformMessages`). Extension `before_provider_request` hooks and the
   request budget still see the unfiltered history, as Pi's `context` event
   and `estimateContextTokens` do. A helper `provider_replay_messages` in
   `messages.py` holds the predicate so tests and any later caller share it.
   The only other request that carries history is the compaction/branch
   summary request (`coding/compaction.build_summary_request`, sent straight
   to the provider-turn executor). It sends history as structured messages,
   so it applies the same helper. Pi instead serializes the whole branch,
   aborted partial text included, into one `<conversation>` user text
   (`compaction/utils.ts serializeConversation`); pipy's structured summary
   request cannot carry that text without replaying it as an assistant
   reply, so it drops it (deviation below).
7. **Rendering**:
   - Live: unchanged. Operator abort already draws the partial text then an
     `Operation aborted` error row (`transcript.show_operation_aborted`);
     a failure keeps pipy's diagnostic line.
   - Restored (`ui/components/session_history.py`): an aborted message draws
     its text, then `Operation aborted` (or its `error_message` unless it is
     `Request was aborted`); an errored one draws `Error: <message or
     "Unknown error">`, both as the transcript's `error` row.
     `show_operation_aborted` takes the row text.
   - `/tree` preview: `assistant: (aborted)` / `assistant: <error message>`
     when the stopped message has no text.
8. **JSON/RPC** (`automation/serialize.py`): assistant messages gain
   `stopReason` (`aborted`/`error` when set, else `toolUse` when there are
   tool calls, else `stop`) and `errorMessage` when set. `message_end`,
   `turn_end` and `agent_end.messages` then carry Pi's shape.
9. **Extension view**: `AssistantMessageView.complete` is false for a
   stopped message (Pi `stopReason === "stop"`).

## Deviations (documented, not ported here)

- Pi records partial thinking and partial tool calls in the aborted message;
  pipy messages store text only (reasoning is not stored, DF1-F2b).
- Pi shows the aborted marker live for every abort; pipy's live renderer
  shows it only for operator aborts (steering/local-command interruptions
  are pipy-only). Restored history shows it for every aborted message.
- Live failures keep pipy's `pipy: provider failure during turn: …`
  diagnostic instead of Pi's `Error: …` row; restored history uses Pi's row.
- Pi's `Aborted after N retry attempts` text and retry-attempt persistence
  with context edits belong to DF1-F4.
- Compaction and branch summaries omit a stopped turn's partial text; Pi's
  serialized summary input includes it as `[Assistant]: …`.
- An abort during tool execution: Pi's next loop turn records an empty
  aborted assistant; pipy ends the run after the interrupted tool results
  (unchanged).

## Done when

- Unit tests: cancellation mid-stream yields a `MessageCompleted`/
  `TurnCompleted`/run-result assistant with the partial text and
  `stop_reason=aborted`, appended to final history; a retry clears the
  partial; a provider failure yields `stop_reason=error` with the message.
- The projection persists the stopped message; the tree round-trips the
  fields; old entries load with `None`.
- `materialize_provider_request` drops stopped messages, and each wire
  family (chat completions, OpenAI responses, Codex, Anthropic, Google)
  sends no partial text from a history holding an aborted and an errored
  message; the compaction summary request drops them too.
- Restored history renders `Operation aborted` / `Error: …`; `/tree` preview;
  JSON `message_end`/`agent_end` carry `stopReason`/`errorMessage`.
- tmux PTY (stub provider): stream, Escape, marker, exit, `pipy -r`, marker
  restored, next prompt works and the stub sees no partial text.
- `just check`, every `scripts/parity_checks/` script and
  `scripts/parity_score.sh` pass; docs, CHANGELOG `[Unreleased]` and the
  backlog F6 entry updated.
