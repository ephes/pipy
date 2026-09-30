# Reasoning storage and replay (DF1-F2b, DF1-F6b, SYS1c remainder)

Backlog: `docs/backlog.md` follow-ons **DF1-F2b** (reasoning text is not
stored, so restored history has no thinking blocks), **DF1-F6b** (a stopped
turn keeps no partial thinking or partial tool calls; F6 Done row) and the
**SYS1c** item "`providerThinkingLevel` on streamed partials and on stopped
(aborted or failed) assistant messages". Reference: `~/src/pi-mono` at
`1b347794e`. Branch `feat/reasoning-storage`.

## What Pi does

### Message model and storage

- `packages/ai/src/types.ts:389-421,546-570`: `AssistantMessage.content` is an
  ordered array of `TextContent { text, textSignature? }`,
  `ThinkingContent { thinking, thinkingSignature?, redacted? }` and
  `ToolCall { id, name, arguments, thoughtSignature? }`, plus `provider`,
  `api`, `model` and `providerThinkingLevel?`. The session file stores the
  message verbatim (`core/session-manager.ts`), so resume sees the same
  ordered blocks.
- `thinkingSignature` is provider-specific replay data: the Anthropic
  signature, the Anthropic `redacted_thinking` data (with `redacted: true`
  and the placeholder text `[Reasoning redacted]`), the OpenAI Responses
  reasoning item as JSON text, the Gemini `thoughtSignature`, or (Chat
  Completions) the name of the reasoning field. `textSignature` is the
  Responses message id/phase (`TextSignatureV1` JSON `{"v":1,"id",
  "phase"?}`) or the Gemini `thoughtSignature` of a text part. A tool call's
  `thoughtSignature` is Gemini's.

### Cross-provider transform (`packages/ai/src/api/transform-messages.ts`)

Every adapter runs `transformMessages` first. For an assistant message,
`isSameModel = provider === model.provider && api === model.api &&
model === model.id`. Per block:

| block | same model | other model |
| --- | --- | --- |
| thinking, `redacted` | kept | dropped |
| thinking with a signature | kept (even with empty text) | text block with the thinking text, or dropped when blank |
| thinking without a signature | dropped when blank, else kept | text block, or dropped when blank |
| text | kept | text without `textSignature` |
| toolCall | kept | `thoughtSignature` removed (id normalized by the adapter's callback) |

Then assistant messages with `stopReason` `error`/`aborted` are skipped
(already ported in F6 as `provider_replay_messages`).

### Per-adapter replay

- **Anthropic Messages** (`api/anthropic-messages.ts:1316-1388`): text
  blocks that are blank after trim are skipped; `redacted` thinking becomes
  `{"type":"redacted_thinking","data":<signature>}`; a thinking block that is
  blank and unsigned is skipped; unsigned (or whitespace signature) becomes a
  `text` block with the thinking text (`allowEmptySignature` models keep an
  empty-signature `thinking` block; pipy has no such compat flag, so the text
  path applies); signed becomes
  `{"type":"thinking","thinking","signature"}`; tool calls `tool_use`.
- **OpenAI Responses / Azure / Codex** (`api/openai-responses-shared.ts:
  145-178,254-324`, used by `openai-responses.ts`, `azure-openai-responses.ts`,
  `openai-codex-responses.ts`): a thinking block with a signature is replayed
  as `JSON.parse(signature)` (the stored reasoning item); unsigned thinking is
  not sent. A text block becomes
  `{"type":"message","role":"assistant","content":[{"type":"output_text",
  "text","annotations":[]}],"status":"completed","id",phase?}` where `id` is
  the signature id (shortened to `msg_<shortHash>` above 64 chars) or
  `msg_pi_<msgIndex>` / `msg_pi_<msgIndex>_<textBlockIndex>`. A tool call
  becomes `function_call` with `call_id` and `id`: the item id is dropped
  for another model of the same provider and API, and whenever it does not
  start with `fc_`. The cross-model id callback (`normalizeToolCallId`) runs
  only for other models: for a target provider in the adapter's allowed set
  (`openai`, `openai-codex`, `opencode`; Azure adds
  `azure-openai-responses`) and a compound `call|item` id, a foreign source
  (other provider or API) gets item id `fc_<shortHash(item)>`.
- **Google Generative AI / Vertex** (`api/google-shared.ts:141-278`):
  `isSameProviderAndModel` (provider and model only). A signature is used
  only for the same model and when it is valid base64 (length % 4 == 0,
  `^[A-Za-z0-9+/]+={0,2}$`). Text: skipped when blank and unsigned, else
  `{"text", "thoughtSignature"?}`. Thinking (same model): skipped when blank
  and unsigned, else `{"thought":true,"text","thoughtSignature"?}`; other
  model: text part when not blank. Tool call: `functionCall` plus
  `thoughtSignature?` on the part.
- **Chat Completions** (`api/openai-completions.ts:1283-1395`): text parts
  that are blank after trim are dropped and the rest joined with `""`;
  reasoning fields, `reasoning_details` and `requiresThinkingAsText` apply to
  same-model thinking only.

### Parsing (what Pi stores)

- Anthropic (`anthropic-messages.ts:628-740`): one block per content block in
  order; `thinking` keeps `signature` (`""` when absent); `redacted_thinking`
  is thinking `[Reasoning redacted]`, signature = `data`, `redacted: true`.
- Responses (`openai-responses-shared.ts:427-716`): one block per output item
  in order. `reasoning` → thinking = summary texts joined `"\n\n"`, else
  content texts joined `"\n\n"`, else the streamed text; signature =
  `JSON.stringify(item)`; encrypted content missing on `output_item.done`
  is backfilled from the terminal response (`:535-552`). `message` → text =
  output_text/refusal texts joined `""`, signature
  `{"v":1,"id",phase?}`. `function_call` → tool call id `call_id|id`.
  While streaming, reasoning summary deltas append to the thinking block and
  `reasoning_summary_part.done` appends `"\n\n"`.
- Google (`google-generative-ai.ts:110-215`): consecutive text parts of the
  same kind (`thought === true` or not) merge into one block; the block keeps
  the last non-empty `thoughtSignature` (`retainThoughtSignature`); a
  `functionCall` part closes the current block and becomes a tool call with
  the part's `thoughtSignature`.

### Stopped turns and providerThinkingLevel

- An aborted or failed message keeps every block streamed so far: partial
  thinking (no signature), partial text, and tool calls with the arguments
  parsed so far (`parseStreamingJson`); `agent-loop.ts` never executes them.
- `anthropic-messages.ts:521-528`: `providerThinkingLevel` is set on the
  output when the request is built, so every partial (`message_start`,
  `message_update`) and the final message (done, aborted or error) carry it.

### Rendering (`modes/interactive/components/assistant-message.ts`)

- Content is drawn in order: non-blank text as Markdown; a run of
  consecutive thinking blocks as one italic `thinkingText` block (non-blank
  blocks trimmed, joined `"\n\n"`) or, when `hideThinkingBlock` is set, the
  italic hidden label (`Thinking...`, or the extension's
  `setHiddenThinkingLabel`). Restored history uses the same component
  (`renderSessionItems`, `interactive-mode.ts:3870-3968`); tool calls of an
  aborted/error message are drawn as tool rows with the error result
  (`Operation aborted` or the error message).
- Ctrl+T (`toggleThinkingBlockVisibility`, `:4450-4463`) flips the setting,
  stores it, re-renders every assistant message (restored and live) and
  shows `Thinking blocks: hidden|visible`. `setHiddenThinkingLabel`
  re-renders them too (`:2290-2301`).

## What pipy does today

`AgentAssistantMessage` holds one text string and tool calls. Every adapter
except `openai-codex` is non-streaming and drops reasoning; Google even adds
thought parts to the answer text. Codex streams summary deltas to the
reasoning sink but stores nothing. The TUI draws live reasoning deltas only
and folds with a pipy-only `deferred_reasoning` buffer. A stopped message
stores the streamed text only; the SYS1c level is stored only on a
successful Anthropic answer.

## Design

### 1. Content model (`native/assistant_content.py`, new leaf module)

Provider-neutral block types importable by both `models.py` and the agent
package (the agent package imports `models`, so they cannot live under
`agent/`):

- `TextContent(text: str, signature: str | None = None)` (Pi `TextContent`).
- `ThinkingContent(thinking: str, signature: str | None = None,
  redacted: bool = False)` (Pi `ThinkingContent`).

`ProviderToolCall` and `AgentToolCall` gain `thought_signature: str | None =
None` (Pi `ToolCall.thoughtSignature`).

`AgentAssistantMessage` gains `blocks: tuple[TextContent | ThinkingContent
| AgentToolCall, ...] = ()`, Pi's ordered content. Invariants
(`__post_init__`): the text blocks joined with `""` equal `content.value`,
and the tool-call blocks in order are exactly `tool_calls`. It is
canonicalized: when the blocks are just the default layout (at most one
unsigned text block equal to a non-empty `content`, then the tool calls),
`blocks` is stored as `()`, so hand-built and legacy messages compare equal.
`ordered_content()` returns the blocks, or the default layout. `blocks`
takes part in equality (it is replay history).

The "a stopped message carries no tool calls" invariant is removed: Pi keeps
a stopped turn's partial tool calls. They are never executed (the loop stops
on a stopped turn) and never replayed (`provider_replay_messages`).

### 2. Provider result and loop (`models.py`, `agent/loop.py`)

- `ProviderResult.content_blocks: tuple[TextContent | ThinkingContent |
  ProviderToolCall, ...] = ()`: the adapter's ordered output, on success and
  (partial) on failure. `ProviderResult.provider_thinking_level` is also set
  on failed results by the Anthropic adapter.
- `ProviderCancelledError` gains an optional `partial: ProviderPartial |
  None` (`content_blocks`, `provider_thinking_level`) that an adapter may
  attach before re-raising; `ProviderTurnOutcome` carries it to the loop.
- `_PartialAssistantText` becomes a segment recorder: consecutive text or
  reasoning deltas of this turn form ordered `TextContent`/`ThinkingContent`
  segments (a `RetryScheduled` clears them).
- Success: the message is built from `result.content_blocks` when present
  (text joined gives `content`, tool calls from the blocks keep
  `thought_signature`). Otherwise, when reasoning was streamed, the blocks
  are the recorded thinking segments, then the final text, then the tool
  calls (this covers streaming providers that return no blocks, such as the
  fake and extension providers); otherwise the old text-plus-calls message.
- Aborted/failed: blocks come from the adapter partial when present, else
  the recorded segments, else the failed result's text (F6). Tool-call
  blocks become the message's `tool_calls`. The message carries
  `provider_thinking_level` from the partial/failed result.

### 3. Replay (`native/providers/replay_content.py`, new)

`transform_assistant_blocks(message, provider, api, model)` ports the
`transformMessages` first pass above: `isSameModel` = the recorded
`provider`, `api` and `model` equal the target's. `models.json` and dynamic
registrations can change a model's API under the same names, so the API is
recorded: `ProviderResult.api` (each adapter's Pi API string, e.g.
`anthropic-messages`, `openai-responses`, `azure-openai-responses`,
`openai-codex-responses`, `google-generative-ai`, `google-vertex`) becomes
`AgentAssistantMessage.api` (Pi `AssistantMessage.api`; turn metadata,
stored and serialized like `provider`/`model`). A message without a recorded
`provider` or `api` (older sessions, providers that report none) is another
model, as in Pi (`undefined !== model.api`). Each adapter calls it with
`request.provider_name`, its own API and `request.model_id`; the Google
signature check additionally uses Pi's provider-and-model comparison:

- Anthropic wire (`anthropic_messages_wire.envelope_to_message`, used by
  Anthropic and Bedrock InvokeModel, whose body is the Messages shape): the
  thinking/redacted rules above. Text and tool-call serialization, and
  emitting an assistant message whose content ends up empty, stay as today
  (not part of this slice; the managed-effort level alignment depends on
  one message per assistant).
- Responses wire (`openai_responses_wire`, shared by OpenAI, Azure and
  Codex): the reasoning-item, message-item and `function_call` id rules
  above. `call_id` stays the D8a portable projection of the call part
  (identity for Pi's safe ids); the item id follows Pi. OpenAI and Azure now
  store Pi's compound `call_id|id` correlation like Codex. Tool results use
  the portable projection of the call part.
- Google wire (`google_generate_content_wire.envelope_to_content`): the
  signature and thought-part rules above. The existing `{"text": ""}`
  placeholder for an empty model message stays.
- Chat Completions (`_provider_helpers.envelope_to_chat_message`, used by
  OpenAI Completions, Mistral, OpenRouter, Cloudflare and ds4): assistant
  text = non-blank text blocks of the transformed message joined `""`, so a
  foreign thinking block arrives as text as in Pi.

### 4. Parsing

- Anthropic wire `parse_response`: ordered blocks (thinking, redacted,
  text, tool_use). `final_text` is still the joined text.
- Responses: a new `responses_output.py` owns Pi's `processResponsesStream`
  slot assembly: `ResponsesOutputAssembler` (item added/delta/done,
  terminal backfill, `blocks()` and `partial_blocks()`), used by the Codex
  stream parser (replacing `_ResponseEventAccumulator`'s text/function-call
  bookkeeping), and `output_blocks(output)` for the non-streaming OpenAI and
  Azure bodies. Deltas and item events are correlated like Pi, by
  `output_index` first, then by item id (`item_id` / `item.id`); only when an
  event carries neither may it fall back to a slot of its type, and only when
  exactly one such slot is open (a text delta with no open message slot opens
  an anonymous text slot, keeping today's id-less fixtures). A test covers
  interleaved function calls whose deltas carry only `output_index`. The
  Codex parser attaches the
  assembler's partial blocks to `ProviderCancelledError` and to parse errors
  (the failed result's `content_blocks`).
- Google: Pi's part merge; thought parts no longer reach the answer text.
- Azure request (`azure-openai-responses.ts:330-345`): an on-state effort
  now sends `reasoning: {effort, summary: "auto"}` and
  `include: ["reasoning.encrypted_content"]` (the construction extras OpenAI
  already uses), so a stored reasoning item carries the encrypted content
  that stateless (`store: false`) replay needs. Tested with an Azure
  multi-turn request.

### 5. Storage (`session_tree.py`)

The assistant JSON gains `blocks` only when non-canonical: Pi-shaped
entries `{"type":"text","text","textSignature"?}`,
`{"type":"thinking","thinking","thinkingSignature"?,"redacted"?}` and
`{"type":"toolCall","index":n}` (a position in `tool_calls`, so arguments
are not duplicated); `tool_calls[]` entries gain `thoughtSignature` when set.
Both the reader and the strict validator accept and check them. Old files
have no `blocks` and load unchanged.

### 6. JSON/RPC (`automation/serialize.py`, `automation/agent_events.py`)

`serialize_message` emits the ordered content: thinking blocks
(`thinkingSignature`, `redacted` when set), text (`textSignature`), tool
calls (`thoughtSignature`). `message_update` partials carry the recorded
segments; an `AssistantReasoningDelta` projects to a `thinking_delta`
event with its segment's `contentIndex`.

### 7. TUI

- Reasoning history rows carry a `ReasoningRenderState(text)`; their lines
  are the text or, while thinking is hidden, the hidden label.
  `set_thinking_hidden` and `set_hidden_thinking_label` re-render every
  reasoning row (live and restored) and redraw the scrollback when a row
  changed, like Ctrl+O. The pipy-only `deferred_reasoning` buffer is
  removed. The live, still-streaming reasoning already shows the label.
- Buffered (non-streamed) completions draw their thinking runs and text in
  block order (`RenderBufferedAssistantMessage`); streamed turns draw as
  today.
- Restored history (`session_history._render_assistant`): block order,
  thinking runs joined `"\n\n"` from trimmed non-blank blocks, the scratch
  transcript inherits the hidden flag and label. A stopped message's tool
  calls are drawn as tool rows with Pi's error result (`Operation aborted`
  or the error message) instead of the stopped marker.

### 8. Request budget

`estimate_request` adds each thinking block's text to the message tokens
(Pi `estimateTokens` counts `thinking`, not signatures).

## Pinned field list (fields this slice changes)

| field | optional | Pi default | pipy |
| --- | --- | --- | --- |
| `thinking.thinkingSignature` | yes | Anthropic `""` when missing | stored as given |
| `thinking.redacted` | yes | absent | written only when true |
| `text.textSignature` | yes | absent | stored as given |
| `toolCall.thoughtSignature` | yes | absent | stored as given |
| Responses message `id` | required on replay | `msg_pi_<i>[_<j>]` | same |
| Responses message `status` | replay | `"completed"` | same |
| Responses `output_text.annotations` | replay | `[]` | same |
| Responses message `phase` | optional | omitted when unset | same |
| Responses `function_call.id` | optional | dropped for other model / non-`fc_` | same |
| `providerThinkingLevel` | optional | set at request build | success, failure and abort |
| `AssistantMessage.api` | required in Pi | the model's API | recorded when the adapter reports it; absent = another model |
| Azure `reasoning.summary` / `include` | on-state only | `"auto"` / encrypted content | same |

## Deviations and follow-ons (backlog)

- Chat Completions and Mistral still do not parse reasoning
  (`reasoning_content`/`reasoning`/`reasoning_text`, `reasoning_details`,
  `requiresThinkingAsText`, `requiresReasoningContentOnAssistantMessages`,
  Mistral thinking chunks): a follow-on.
- `message_start` carries no `providerThinkingLevel`: pipy's Anthropic
  adapter does not stream and picks the level inside the request, after the
  loop emitted `message_start`. Every stored message and every
  `message_end` carries it.
- A stopped turn's partial tool-call arguments are stored as the raw
  streamed JSON text (Pi stores `parseStreamingJson`'s object); they are
  drawn and serialized through the existing lenient parser.
- Live, an aborted Codex turn does not draw its partial tool calls as rows
  (pipy draws tool rows at execution); resume does.
- Pi's per-run mouse toggle of a thinking block has no pipy counterpart.
- JSON mode emits `thinking_delta` (and `text_delta`) without Pi's
  `*_start`/`*_end` events, as before for text.

## Done when

- Unit tests: model invariants and canonicalization; transform table;
  exact request shapes per adapter for same-model replay (Anthropic signed,
  redacted, unsigned; Responses reasoning item, message id/phase, fc id;
  Codex; Google signatures) and cross-provider conversion (Anthropic →
  Responses/Google/Chat, Responses → Anthropic, Codex model switch);
  parsing per adapter; Codex partial on abort and failure; session
  round-trip; JSON serialization; TUI Ctrl+T re-render and restored order.
- PTY evidence vs real Pi (thinking live and restored, Ctrl+T).
- Live openai-codex `gpt-6.1-sol` at medium: multi-turn with tools, resume,
  a further turn accepted with replayed reasoning items, then a switch to
  another Codex model.
- `just check`, `scripts/parity_checks/*`, `scripts/parity_score.sh`,
  `just catalog-drift` green; different-family review CLEAN.
