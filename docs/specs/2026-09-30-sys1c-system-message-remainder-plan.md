# SYS1c: system-message remainder

Status: implemented, 2026-09-30. Source: the SYS1c follow-on in `docs/backlog.md`
(after SYS1b, `docs/specs/2026-09-30-sys1b-provider-system-messages-plan.md`).
Pi reference: `~/src/pi-mono` @ `1b347794e`.

## Scope

In this slice:

1. **Leading prompt shape.** OpenAI Responses and Azure send the leading
   system message as the first `input` item (no `instructions`); Chat
   Completions sends it in the instruction role; an empty leading prompt
   sends no message; Codex keeps `instructions` with Pi's empty-prompt
   default.
2. **Sessions without a leading system message** (recorded before SYS1a):
   a model that accepts later system messages gets no leading prompt and
   the state as a later message, instead of pipy's collapse.
3. **`_restoreToolsFromTranscript`** after `/tree` navigation.
4. **Anthropic mid-conversation effort** (`supportsMidConvoEffort`,
   `providerThinkingLevel`, `output_config` system messages, adaptive
   `block_binding`, the two betas; Pi `4e69b0c28`).

Deferred (stays in the backlog's SYS1c entry): the `tools`/`rules`/`docs`
prompt sections (they need Pi's tool prompt snippets and guidelines, with
the "Skills and prompt" follow-on); tool `strict` fields; Anthropic's other
betas (fine-grained tool streaming, interleaved thinking, server-side
fallback) and temperature handling; OpenRouter Claude through the native
Messages API (`4e69b0c28`); `providerThinkingLevel` on streamed partials and
on stopped (aborted/failed) assistant messages.

## Pi reference

### 1. Leading prompt (`ai/src/api/*`)

- `openai-responses-shared.ts:145-352` `convertResponsesMessages(model,
  context, providers, options)`: `includeInitialSystemMessage =
  options.includeSystemPrompt ?? true`; `instructionRole = model.reasoning &&
  compat.supportsDeveloperRole !== false ? "developer" : "system"`. For the
  leading message (`sourceIndex === 0 && role === "system"`, after
  `resolveTranscript`, so the collapsed head on the collapse path) it pushes
  `{role: instructionRole, content: getSystemMessageText(msg)}` when the text
  is non-empty and `includeInitialSystemMessage`. A user message with string
  content is `{role:"user", content:[{type:"input_text", text}]}`.
- `openai-responses.ts:316` and `azure-openai-responses.ts:293` use the
  default (`includeSystemPrompt` unset → true) and set no `instructions`
  field (`openai-responses.ts:324-332`, `azure-openai-responses.ts:303-309`).
- `openai-codex-responses.ts:542-557`: `includeSystemPrompt: false`;
  `instructions = getSystemMessageText(getInitialSystemMessage(
  context.messages)) || "You are a helpful assistant."` (Pi-forced default;
  the Codex backend requires the field).
- `openai-completions.ts:1225-1252`: `instructionRole = model.reasoning &&
  compat.supportsDeveloperRole ? "developer" : "system"` for the leading
  message too (`i === 0` → `getSystemMessageText`), pushed only when
  non-empty.
- `mistral-conversations.ts:799-800`: leading → `{role:"system"}` when
  non-empty (unchanged role; only the empty case changes).
- Anthropic already sends `system` only when non-empty (SYS1b).

Fields this slice changes on these paths: Responses/Azure `instructions`
(removed) and the first `input` item; the legacy no-messages `input` (Pi has
no string form: a list with the user item); Chat Completions/Mistral leading
message role and presence; Codex `instructions` default. Every other field
on these paths is unchanged.

### 2. No leading system message

`getInitialSystemMessage(messages)` is `messages[0]` when it is a system
message. Pi sessions recorded before `9e05370b2` have none; the first run
after resume appends the full section patch (`_preparePromptAndToolLoadout`)
after the old messages. For a model with `supportsMidConvoSystemMessages`
the transcript is not collapsed, so there is no leading prompt (Anthropic no
`system`, Responses/Completions no leading message, Codex the default
instructions) and every system message is a later message
(`renderSystemMessageUpdate`, tool additions per SYS1b).
`resolveTranscriptTools`: `requestTools = anchorsAdditions ?
initial?.toolsAdded ?? [] : getCurrentTools` → with anchored additions the
top level is empty and the tools load at the later message; Anthropic
`nativeToolChanges` needs initial tools → false → current tools.

### 3. `_restoreToolsFromTranscript` (`coding-agent/src/core/agent-session.ts`)

```ts
private _restoreToolsFromTranscript(): void {
  const current = getCurrentSystemMessage(this.sessionManager.buildSessionContext().messages);
  if (!current) return;
  this.setActiveToolsByName((current.toolsAdded ?? []).map((tool) => tool.name));
}
```

Called in `navigateTree` after `_refreshFinalizedContext()` (line 4040), for
every navigation (with or without a branch summary), and in the constructor
only when `initialActiveToolNames === undefined` (line 484). The CLI never
takes the constructor path: `sdk.ts:268-270` always passes an array
(`options.tools ?? (noTools ? [] : settings default tools ?? DEFAULT_TOOL_NAMES)`),
also on resume and session switches. So in the CLI a resumed session starts
with the configured tools and the next turn records the difference; only
`/tree` restores. `setActiveToolsByName` → `_applyToolLoadout` keeps names
in the (allow/exclude-filtered) registry that are not `hidden`, silently
dropping the rest; an empty `toolsAdded` with a system message present
restores an empty set.

pipy: `/tree` navigation reaches the product through
`session_commands._rebuild_and_render` (plain selection) and
`collaborators._execute_branch_summary` (summary selection). Both call a new
`restore_tools_from_transcript()` after the history is rebuilt: the active
branch's `build_coding_context().system_anchors` replayed by
`current_system_message`; none → no change; else
`NativeToolCapabilities.restore_active_tools(names)` — one critical section
that keeps the names in the registry (and, with a configured
`--allow`/`--exclude` filter, in the filter's visible set, pipy's equivalent
of Pi's filtered registry) and assigns them. `/new`, `/resume`, `/fork`,
`/clone` and startup do not restore (Pi CLI).

### 4. Anthropic mid-conversation effort (`ai/src/api/anthropic-messages.ts`)

- Compat `supportsMidConvoEffort` (read as `=== true`). Hydrated Pi data:
  `anthropic/claude-opus-5`, `claude-opus-5-5`, `claude-sonnet-5-5`,
  `claude-fable-5-1` (the generator also merges `thinkingLevelMap.off =
  null`, which pipy's rows already carry). OpenRouter's Claude rows carrying
  it are `anthropic-messages` rows pipy does not carry.
- `stream` (521-528): `providerThinkingLevel = supportsMidConvoEffort ?
  (options.effort ?? "high") : undefined`, set on the output message.
  `streamSimple` (884-891, forceAdaptiveThinking rows): `effort =
  mapThinkingLevelToEffort(model, reasoning)`; reasoning unset → no effort.
- `getBetaFeatures` (1036-1038): with the flag push
  `mid-conversation-output-config-2026-07-01`,
  `thinking-binding-controls-2026-08-01` before the tool-changes beta;
  deduplicated; a configured `anthropic-beta` header replaces the list; the
  SDK sends `anthropic-beta: a,b`.
- `buildParams` (1072-1080, 1159-1167): `messages =
  insertThinkingLevelMessages(converted, options.effort ?? "high")`;
  `thinking = {type:"adaptive", display: options.thinkingDisplay ??
  "summarized", block_binding:{prefix_mismatch_behavior:"drop_block"}}`;
  top-level `output_config = {effort:"high"}` (Pi-forced constant, the
  active effort rides on the trailing system message); temperature never
  sent (pipy sends none).
- `convertMessages` (1375-1386): records an assistant message's
  `providerThinkingLevel` at its `params` index when `msg.api ===
  "anthropic-messages" && msg.provider === model.provider` and the value is
  one of `low|medium|high|xhigh|max`. Cache markers are placed inside
  `convertMessages` (before insertion).
- `insertThinkingLevelMessages` (1442-1455): before each recorded assistant
  message `{role:"system", content:[], output_config:{effort}}`, and one
  with the active effort at the end.

pipy:

- catalog rows gain `supportsMidConvoEffort: True`; `catalog_drift.py`
  tracks the key;
- construction: `supports_mid_convo_effort = compat.supportsMidConvoEffort
  is True` for `anthropic-messages`, passed to `AnthropicProvider`;
- `AgentAssistantMessage.provider_thinking_level: str | None`
  (`compare=False`, turn metadata like `provider`/`model`), filled from a
  new `ProviderResult.provider_thinking_level`, persisted as
  `providerThinkingLevel` in the session tree and emitted by the automation
  JSON serializer (between `model` and `usage`, Pi's field order);
- adapter: active effort = the mapped reasoning effort
  (`ANTHROPIC_ADAPTIVE_EFFORT`) or `"high"`; the managed thinking shape
  replaces `_apply_anthropic_thinking`; effort system messages inserted after
  the cache breakpoint; the recorded check is `message.provider ==
  self.provider_name` plus a valid effort (only this adapter writes the
  field, so Pi's `api` check is implied); betas as above; the result carries
  `provider_thinking_level`.

## Known divergences kept

- Pi Codex on a model without `supportsMidConvoSystemMessages` sends only the
  *initial* system message as `instructions` (the collapsed head is skipped
  by `includeSystemPrompt: false`), so later section updates are lost; pipy
  keeps sending the replayed prompt.
- pipy's compaction summary rides as an out-of-band prompt tail. Without a
  leading system message the tail alone (without its separator) is the
  leading prompt.
- `providerThinkingLevel` is not on streamed partials or stopped assistant
  messages (pipy's partial has no provider/model either; stopped messages
  are never replayed).

## Tests (done-when)

1. Exact bodies: OpenAI Responses and Azure (leading developer/system item
   first, no `instructions`, empty prompt → no item, legacy no-messages
   list, mid-convo and collapse); Codex (`instructions` unchanged, empty →
   `"You are a helpful assistant."`); Chat Completions (developer vs system
   leading, empty omitted); Mistral (empty omitted).
2. No leading message: Responses (no leading item, full state as a later
   developer message, `additional_tools` at it, empty top-level tools),
   Codex (default instructions), Anthropic (no `system`, current tools,
   text-only later message), collapse path unchanged for models without the
   flag.
3. Restore: `restore_active_tools` filters unknown and filter-hidden names
   and assigns atomically; `/tree select` to a branch with a different tool
   set restores it (the next turn records no tool change); a branch without
   system messages leaves tools unchanged; branch-summary navigation
   restores; `/resume` does not.
4. Anthropic effort: flag on → adaptive thinking with `block_binding`,
   top-level `output_config.effort = "high"`, trailing effort message with
   the mapped effort (unset → `high`), historical messages before recorded
   assistant turns only for the same provider and valid levels, the
   conversation cache marker placed before insertion exactly as SYS1b
   places it (a trailing user block; a trailing held system update's last
   block; none after a trailing assistant) and never on an effort message,
   beta header list
   (with and without native tool changes; configured header wins); flag off
   → unchanged body; result/assistant/persistence/JSON carry
   `providerThinkingLevel`; construction reads the flag.
5. Gates: `just check`, every `scripts/parity_checks/*`,
   `scripts/parity_score.sh`, `just catalog-drift` CLEAN,
   `automation_pi_comparison.py`, `session_tree_pi_comparison.py`; live
   `openai-codex` `gpt-6.1-sol` run with isolated config: a multi-turn
   request is accepted and a resumed session behaves as Pi (no restore;
   `/tree` restores offline test).
6. Docs: `docs/provider-catalog.md`, `docs/automation-rpc.md`,
   `docs/session-tree.md` where they describe these behaviours,
   `docs/pi-parity.md`, `CHANGELOG.md` `[Unreleased]`, backlog SYS1c entry
   (done parts struck, remainder kept).
