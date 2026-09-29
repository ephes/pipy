# SYS1a: system messages in the transcript

Status: plan, 2026-09-29. Source: the SYS1 follow-on in `docs/backlog.md`
(found by DH1). The gate it turns green: `automation_pi_comparison.py`
(`event_order_and_discriminators_match`, `agent_end_semantics_match`).

## Problem

Pi `9e05370b2` ("Mid conversation system messages") moved the system prompt
and tool declarations into the transcript. Every run that changes them records
a `role: "system"` message. The first run of a session records one with the
whole prompt and every tool. JSON/RPC events carry it:
`agent_start, turn_start, message_start:system, message_end:system,
message_start:user, …`. `agent_end.messages` starts with it, and the session
file stores it. pipy emits and stores no system message, so both comparison
checks fail.

## Pi reference (`~/src/pi-mono` @ `4df157433`)

### Type (`packages/ai/src/types.ts:522`)

```ts
interface SystemMessage {
  role: "system";
  content: string | TextContent[];          // leading: base prompt; later: added instructions
  sections?: Record<string, string | null>; // ordered; later messages patch by name, null removes
  toolsAdded?: Tool[];                      // complete declarations {name, description, parameters, constrainedSampling?}
  toolsRemoved?: ToolReference[];           // {name}
  timestamp: number;
}
```

`Message = SystemMessage | UserMessage | AssistantMessage | ToolResultMessage`.
A tool declaration is `toToolDeclaration(tool)` (`utils/transcript.ts`):
`{name, description, parameters, constrainedSampling?}`. It drops executable
fields.

### Replay (`packages/ai/src/utils/transcript.ts`)

- `getCurrentTools(messages)`: for each system message in order, delete
  `toolsRemoved` names, then set `toolsAdded` by name.
- `getCurrentSystemMessage(messages)`: concatenate non-empty `content` with
  `\n\n`, patch `sections` by name (`null` deletes), resolve the tools, and
  take the first timestamp. It returns undefined when there is no system
  message and no tool.
- `getToolStateChanges(previous, current)`: an added tool is new or changed
  (`declarationsEqual` compares JSON). A removed tool is gone or changed. So a
  changed definition is a removal plus an addition.
- `getSystemMessageText(m)` (`utils/text.ts:15`): `[content, ...non-null
  sections]`, empty parts dropped, joined by `\n\n`.

### Where system messages come from

- `agent-session.ts` `_preparePromptAndToolLoadout` (`:1659`): patch =
  `diffSystemPromptSections(getCurrentSystemMessage(messages)?.sections ?? {},
  buildSystemPromptSections(options))` (`system-prompt.ts`
  `diffSystemPromptSections`: changed or new names get their text, vanished
  names get `null`, undefined when nothing changed). It returns
  `{role:"system", content:"", sections: patch, timestamp}` or undefined.
  `prompt()` puts it in front of the user message (`messages.unshift`, `:2018`).
  `prepareNextTurn` repeats this between turns of one run (`:879`).
- `agent-loop.ts` `declareToolChanges(context, pending)` (`:333`), called at
  run start (`runAgentLoop`) and at every later turn start (`runLoop`) over
  the prepared and steering messages:
  - The baseline is `getCurrentTools(context.messages + pending)`, with the
    pending system message's own tool fields cleared.
  - It diffs that baseline against `context.tools`.
  - When a pending system message exists, it replaces that message's tool
    fields with the delta. Empty lists omit the field.
  - Otherwise, if the delta is not empty, it inserts
    `{role:"system", content:"", toolsAdded?, toolsRemoved?}` before the first
    non-system pending message.
- Each emitted message gets `message_start` then `message_end` right after
  `turn_start` (run start: after `agent_start, turn_start`). It is pushed to
  `context.messages` and to `newMessages`, so `agent_end.messages` includes it.
- A forced `before_agent_start` `systemPrompt` is not recorded. It is a
  request-time projection (`_installAgentForcedPromptProjection`, `:1700`).

### Storage (`core/session-manager.ts`, `docs/session-format.md`)

- `_handleMessageEnd` persists `system` like user/assistant/toolResult: a
  `message` entry (`agent-session.ts:1110`).
- The first request of a session persists the full prompt and every tool.
  Later changes persist patches. A session made before system messages
  existed gets the full state as a later system message on its next request.
- `CompactionEntry.systemMessage` (optional) =
  `getCurrentSystemMessage(buildSessionContext().messages)` at compaction
  time. `sessionEntryToContextMessages(compaction)` returns
  `[systemMessage, summary]`. `buildContextEntries` drops system message
  entries from the retained pre-compaction range (`:506`), so the checkpoint
  replaces them.
- `sessionEntryToContextMessages` turns a stored system message with missing
  content into `content: ""`.

### Consumers

- Interactive mode renders nothing for a system message
  (`interactive-mode.ts:3805` `case "system": break`).
- The `/tree` selector shows it as a dim `[system]` (default filter).
- The HTML export template renders nothing for it.
- `getSessionStats().totalMessages` counts every `message` entry, system
  included.
- Compaction: `getMessagesFromProjectedEntryForCompaction` drops system
  messages from the summarized range. `estimateTokens` counts content,
  sections and `toolsAdded`.
- Providers get a `TranscriptContext`. `resolveTranscript(ctx,
  compat.supportsMidConvoSystemMessages)` keeps later system messages in
  place only when the model supports them. Otherwise
  `collapseSystemMessages` replays them into one leading system message,
  which becomes the provider's system prompt.

## pipy today

- The provider request carries the system prompt out of band
  (`ProviderRequest.system_prompt` = the accepted turn's
  `agent_system_prompt` plus the compaction summary suffix). Messages are
  only user/assistant/tool (`AgentMessage`). The message and event validators
  check exact types.
- pipy's prompt is one composed string (`NATIVE_TOOL_LOOP_SYSTEM_PROMPT`,
  `SYSTEM.md`/`APPEND_SYSTEM.md`, reference roots, `project_context`, skills,
  resume block, `before_agent_start` suffix). It is not Pi's sectioned
  prompt; the backlog's "Skills and prompt" follow-on already records that.
- Durable writes follow `MessageCompleted` events
  (`ProductSessionEventProjection` → `product_session.append_message` →
  coding-state history + session tree).

## Scope decision

The full Pi port touches every provider adapter and the deferred-tool
mechanism. SYS1 is split into two parts:

- **SYS1a (this slice):** the message type, emission (run start and later
  turns), JSON/RPC shape, session storage, the compaction checkpoint, and
  the UI and consumer handling. Providers behave like Pi's collapse path for
  every model: the out-of-band system prompt is the replayed head, and later
  system messages are not sent.
- **SYS1b (remainder, recorded in the backlog):**
  - the `supportsMidConvoSystemMessages` / `supportsMidConvoToolAdditions` /
    `supportsMidConvoToolChanges` compat flags and per-adapter serialization
    of later system messages (Anthropic `tool_addition`/`tool_removal`,
    OpenAI Responses/Codex developer messages and `additional_tools`,
    Chat Completions, Google, Bedrock, Mistral);
  - Anthropic mid-conversation effort (`supportsMidConvoEffort`,
    `output_config` system messages, `4e69b0c28`);
  - replacing pipy's `AgentToolResultMessage.added_tool_names` deferred-tool
    loads with transcript `toolsAdded` (Pi deleted `addedToolNames` and
    `utils/deferred-tools.ts` in `9e05370b2`);
  - restoring the active tool loadout from the transcript on resume
    (`_restoreToolsFromTranscript`);
  - Pi's sectioned prompt text (`preamble`/`tools`/`rules`/`docs`/`addendum`/
    `project_context`/`skills`/`cwd`), which stays with the "Skills and
    prompt" follow-on.

## Design

### 1. Canonical type (`native/agent/messages.py`)

```python
@dataclass(frozen=True, slots=True)
class AgentToolDeclaration:   # Pi toToolDeclaration
    name: str
    description: str
    parameters_json: str      # canonical JSON (sort_keys=False, compact); frozen, comparable

@dataclass(frozen=True, slots=True)
class AgentSystemMessage:
    content: ProductContent = ProductContent("")
    sections: tuple[tuple[str, str | None], ...] = ()   # ordered; None removes
    tools_added: tuple[AgentToolDeclaration, ...] = ()
    tools_removed: tuple[str, ...] = ()
```

- `AgentMessage` gains `AgentSystemMessage`, and so does
  `_AGENT_MESSAGE_TYPES`. `MessageStarted`/`MessageCompleted` and
  `AgentRunResult.messages` accept it.
- **Invariant:** a system message is transcript state. It never enters
  provider history. The exact-type validators for history (`agent/loop.py`
  `_validate_message`, `agent/request.py`, `coding/state.py`
  `require_exact_agent_message`) keep rejecting it. The provider request
  already carries the replayed prompt out of band. This is Pi's
  `collapseSystemMessages` for every model; SYS1b lifts it per model.
- Declarations store `parameters` as a JSON string, so equality matches Pi's
  `declarationsEqual` (JSON comparison). pipy has no `constrainedSampling`
  field, so none is emitted.

### 2. Replay helpers (`native/agent/system_messages.py`, new, pure)

These are ports of `transcript.ts` / `system-prompt.ts`:
- `current_tools(messages)`: `getCurrentTools`.
- `current_system_message(messages)`: `getCurrentSystemMessage`, returning
  `AgentSystemMessage | None`.
- `diff_sections(previous, current)`: `diffSystemPromptSections`.
- `tool_state_changes(previous, current)`: `getToolStateChanges`.
- `declare_tool_changes(baseline_tools, pending, definitions)`: the
  run-start/turn-start part of `declareToolChanges`. It returns the message
  to emit (the pending message with its tool fields replaced, a new
  tools-only message, or None).
- `system_message_text(message)`: `getSystemMessageText`.
- `tool_declaration(definition: ToolDefinition)`: `toToolDeclaration`.

### 3. pipy's sections

pipy's prompt is one string. It becomes one untagged Pi `preamble` section:
`{"preamble": accepted_turn.agent_system_prompt}`. Replay renders it
verbatim (`content` is empty, so `getSystemMessageText` returns the
preamble), and a changed prompt patches `preamble`. Pi uses the same section
name for untagged text. Splitting it into Pi's tagged sections is the prompt
follow-on above. The compaction summary suffix is conversation, not prompt
state (Pi keeps it as a `compactionSummary` message), so it is excluded. A
`before_provider_request` hook's `system_prompt` transform is request-time,
like Pi's forced prompt, and is not recorded.

### 4. Loop emission (`native/agent/loop.py`)

- `AgentLoopRunInput` gains `system_prompt: AgentSystemPromptInput | None =
  None`, with:
  - `pending: AgentSystemMessage | None`: the section patch;
  - `declared_tools: tuple[AgentToolDeclaration, ...]`: the transcript
    replay before this run.
- The loop keeps a running `declared` list. In each iteration it reads
  `definitions()` once (the same tuple goes to `prepare`) and calls
  `declare_tool_changes` against `declared`. At turn 0 it merges into
  `pending`; at later turns only a tool delta can produce a message, because
  pipy's prompt is fixed for one accepted run. Then `_start_turn` emits
  `TurnStarted`, then the system message's `MessageStarted`/`MessageCompleted`,
  then (turn 0) the user message's. It appends the system message to
  `appended_messages` (so `agent_end.messages` starts with it) but not to
  `state.history`, and updates `declared`.
- Legacy callers pass `system_prompt=None`, which emits nothing. That
  covers the one-shot `NativeSession` bootstrap path in `native/session.py`
  (at its line ceiling, untouched) and the loop unit tests.

### 5. Product wiring

- `repl/loop_step.py` (`_run_agent_turn`): build `AgentSystemPromptInput`
  from `tree.build_context().messages` under `ctl.session_tree_section()`:
  - `declared_tools = current_tools(...)`;
  - `pending = diff_sections(current.sections, {"preamble": prompt})`.

  Pass it through `CodingAgentRunCoordinator.run_turn(...,
  system_prompt=...)`.
- `coding/product_session.py` `append_message`: a system message is persisted
  (port append) but not appended to coding-state history. This is the one
  branch point for the invariant.
- `agent_adapters.py` `ProductSessionEventProjection`: `MessageCompleted` for
  a system message → append (persist).

### 6. Session tree (`native/session_tree.py`)

- Stored shape (Pi field names): `{"role":"system","content":"",
  "sections":{...},"toolsAdded":[{"name","description","parameters"}],
  "toolsRemoved":[{"name"}]}`. Empty `sections`/`toolsAdded`/`toolsRemoved`
  are omitted. There is no per-message timestamp, like pipy's other stored
  messages (the entry has one). Missing `content` loads as `""`. The strict
  loader validates the shape.
- `build_context` (Pi `buildSessionContext`, used by RPC `get_messages` and
  `get_state.messageCount`) includes system messages.
  With a compaction, the checkpoint comes first, then the summary, then the
  retained entries minus system messages.
- `build_coding_context` (the provider history) is unchanged:
  `_project_context_entry` ignores system messages.
- `build_context_entries` (the transcript render) drops system entries from
  the retained pre-compaction range, like Pi `:506`. Renderers ignore them.
- `CompactionEntry.system_message: AgentSystemMessage | None` is stored as
  `systemMessage` = `current_system_message(build_context().messages)` when
  `append_compaction` writes it. It is absent on older entries.

### 7. Surfaces

- `automation/serialize.py`: `{"role":"system","content":<str>,
  "sections"?:{…},"toolsAdded"?:[…],"toolsRemoved"?:[{name}]}`. This is Pi's
  key order minus `timestamp`, which pipy omits on every message.
- UI: `ui/state.py` already ignores non-assistant messages. Restored-history
  rendering (`ui/components/session_history.py`) ignores system messages.
- RPC `get_session_stats`: Pi counts every stored `message` entry in the
  file (all branches, compacted ones included) for all of its counters. pipy
  counts the active context's messages for all of them, a divergence that
  predates this slice and affects every role. Fixing only `totalMessages`
  would leave it inconsistent with the per-role counters, so this slice
  keeps all counters context-based. `totalMessages` now includes the
  context's system messages (after a compaction, the checkpoint counts once
  and the replaced system entries do not). A test pins this. Moving all
  counters to Pi's stored-entry counting is added to the USAGE1 follow-on in
  the backlog.
- Tool declarations in the transcript are the loop's executable set
  (`definitions()`), not the set a `before_provider_request` hook narrows the
  request to. This is Pi: `declareToolChanges` diffs against `context.tools`,
  and a `prepareLoadout` hook hides declarations only at request time
  (`_installHiddenDeclarationsProjection`, `agent-session.ts:1685`, which
  filters `toolsAdded` in `transformContext` without touching the stored
  messages). A forced prompt works the same way. A test pins that a narrowing
  hook leaves the stored declaration intact while the request omits the tool.
- `/tree` preview: `[system]` (Pi `tree-selector.ts:812`).
- The HTML export renders nothing for `role: "system"`, like Pi.

## Tests (done-when)

1. `system_messages.py` unit tests:
   - replay (sections patch/delete, tools add/remove/redefine, content
     concatenation);
   - `diff_sections` (unchanged → None, changed, removed → None value);
   - `tool_state_changes` (redefinition = remove + add);
   - `declare_tool_changes` (pending with and without a delta, no pending
     with an empty delta → None, no pending with a delta → tools-only message).
2. Loop tests:
   - with `system_prompt`, events are `AgentRunStarted, TurnStarted,
     MessageStarted(system), MessageCompleted(system), MessageStarted(user), …`;
   - `result.messages[0]` is the system message, and it is not in
     `final_history`;
   - an unchanged prompt and tools → no system message;
   - a tool change between turns → a tools-only system message after that
     turn's `TurnStarted`;
   - `None` → the old sequence.
3. Session tree: round trip (lenient and strict); `build_context` includes
   system messages; `build_coding_context` excludes them; the compaction
   checkpoint is written and replayed, and retained system entries are
   dropped.
4. Product: the first prompt of a new session persists one system message
   (preamble + every visible tool) before the user entry; a second prompt with
   the same prompt/tools persists none; a changed prompt persists a preamble
   patch; the provider request's messages contain no system message.
5. JSON/RPC:
   - `--mode json` emits `message_start/end:system` after `turn_start`;
   - `agent_end.messages[0].role == "system"`;
   - RPC `get_messages` starts with the system message.
6. Gates:
   - `automation_pi_comparison.py` passes against real Pi;
   - `automation_rpc_conformance.py`, `session_tree_pi_comparison.py` and
     `session_tree_conformance.py` still pass;
   - `just check` is green.
7. Docs: `docs/automation-rpc.md` (the "known red" note), `docs/session-tree.md`
   or `docs/session-storage.md` (the stored shape and compaction checkpoint),
   `CHANGELOG.md` `[Unreleased]`, and `docs/backlog.md` (SYS1a done, SYS1b
   remainder).

No live provider run: provider serialization does not change, because
requests are byte-identical and the system prompt text is unchanged.
