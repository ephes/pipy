# SYS1b: system messages to providers

Status: implemented, 2026-09-30. Source: the SYS1b follow-on in `docs/backlog.md`
(after SYS1a, `docs/specs/2026-09-29-sys1-system-messages-plan.md`). Pi
reference: `~/src/pi-mono` @ `1b347794e`.

## Problem

SYS1a records Pi's system messages in the transcript, but every provider
still gets the replayed prompt out of band (Pi's collapse path for every
model). Pi keeps later system messages in place for models whose compat says
they accept them, so the leading prompt (and the prompt cache prefix) stays
fixed while prompt and tool changes arrive as mid-conversation messages. pipy
also records its prompt as one `preamble` section, where Pi records tagged
sections, and still loads deferred tools through
`AgentToolResultMessage.added_tool_names`, which Pi removed in `9e05370b2`.

## Scope

In this slice:

1. Pi's tagged prompt sections for the parts pipy has: `preamble`,
   `addendum`, `project_context`, `skills`, `cwd` (and one pipy-only custom
   section, `resume`).
2. The compat flags `supportsMidConvoSystemMessages`,
   `supportsMidConvoToolAdditions`, `supportsMidConvoToolChanges`,
   `supportsAdditionalTools` and `supportsDeveloperRole` in the catalog and
   in construction; `just catalog-drift` compares them.
3. Provider requests carry the transcript's system messages; one pure
   resolver ports `resolveTranscript`/`collapseSystemMessages`,
   `resolveTranscriptTools`, `getDeclaredTools`, `hasToolRedefinitions`,
   `hasNonAdditiveToolChanges` and `renderSystemMessageUpdate`.
4. Per-adapter serialization: OpenAI Codex, OpenAI Responses and Azure
   Responses (shared wire), Chat Completions (shared wire: OpenAI
   completions, OpenRouter, Cloudflare, Mistral), Anthropic Messages. Google
   and Bedrock keep collapsing (Pi does the same, below).
5. Removing `added_tool_names`, `split_deferred_tools` and
   `supportsToolReferences`: tool loads are system-message `toolsAdded`,
   anchored per adapter.

Deferred (recorded in `docs/backlog.md` as SYS1c, below): Anthropic
mid-conversation effort; `_restoreToolsFromTranscript`; the `tools`/`rules`/
`docs` sections; the leading prompt's role/placement divergences listed under
"Known divergences kept".

## Pi reference

### Sections (`coding-agent/src/core/system-prompt.ts`)

`buildSystemPromptSections(options)` returns an ordered record:

| name | content | condition |
| --- | --- | --- |
| `preamble` | `customPrompt`, else the default identity text | always |
| `tools`, `rules`, `docs` | tool snippets, guideline bullets, Pi docs | only without `customPrompt` |
| `addendum` | `appendSystemPrompt` (appends joined by `\n\n`) | non-empty |
| `project_context` | `renderProjectContext(contextFiles)` | files present |
| `skills` | `formatSkillsForPrompt(skills, readTool)` | a read tool and skills |
| `cwd` | `cwd.replace(/\\/g, "/")` | always |
| custom | extension `sections[name]` | non-empty |

Every section except `preamble` is wrapped `<name>\n{content}\n</name>`.
`getSystemMessageText` joins content and non-null sections with `\n\n`,
dropping empty parts. A `before_agent_start` handler that returns
`systemPrompt` forces the prompt for that run: the transcript keeps the
structured sections and `_installAgentForcedPromptProjection` sends the
request with every system message collapsed into one head holding the forced
text and the current tools (`16292398a`).

### Compat flags (`ai/src/types.ts`), per API, each resolved independently

| API | flag | default | read by |
| --- | --- | --- | --- |
| openai-responses, azure, codex | `supportsMidConvoSystemMessages` | false | `resolveTranscript` |
| openai-responses, codex | `supportsAdditionalTools` | false | `resolveTranscriptTools`, `additional_tools` items |
| openai-responses, codex | `supportsToolSearch` | false | `resolveTranscriptTools`, `tool_search_*` items |
| openai-responses | `supportsDeveloperRole` | true (`!== false`) | later-message role |
| openai-completions | `supportsMidConvoSystemMessages` | detectCompat: false | `resolveTranscript` |
| openai-completions | `supportsMidConvoToolAdditions` | detectCompat: false | kimi `{role:"system", tools}` |
| openai-completions | `supportsDeveloperRole` | detectCompat: `isOpenRouterDeveloperRoleModel \|\| (!isNonStandard && !isOpenRouter)` (`openai-completions.ts:1605-1638`) | role |
| anthropic-messages | `supportsMidConvoSystemMessages` | false | `resolveTranscript` |
| anthropic-messages | `supportsMidConvoToolChanges` | false | `tool_addition`/`tool_removal` |
| mistral-conversations | `supportsMidConvoSystemMessages` | false | `resolveTranscript` |
| google-*, bedrock | none | | always `collapseSystemMessages` |

Azure reads `supportsMidConvoSystemMessages`, `supportsAdditionalTools` and
`supportsToolSearch` like openai-responses (`azure-openai-responses.ts:290-300`).
The completions `supportsDeveloperRole` detection is ported as one bounded
predicate (explicit bool wins, else Pi's `isNonStandard`/`isOpenRouter`
provider-and-baseUrl tests), used only for later messages.

Catalog values come from Pi's generated data (hydrated
`packages/ai/src/providers/data`). The rows pipy carries that gain a flag:
anthropic `claude-fable-5`, `-5-1`, `claude-opus-4-8`, `-5`, `-5-5`,
`claude-sonnet-5-5` (`supportsMidConvoSystemMessages`,
`supportsMidConvoToolChanges`); openai-codex `gpt-5.5` (mid-convo only) and
`gpt-5.6-*`, `gpt-6-*`, `gpt-6.1-sol` (mid-convo, `supportsAdditionalTools`);
openai `gpt-5.4` … `gpt-6.1-sol` and the openai-completions mirror `gpt-5.5`
(mid-convo, `supportsAdditionalTools`); `cloudflare/@cf/moonshotai/kimi-k2.6`
and `openrouter/moonshotai/kimi-k2.6` (`supportsDeveloperRole: false`).
`supportsMidConvoEffort` is not added (it is read only by the deferred
effort work).

### Transcript helpers (`ai/src/utils/transcript.ts`, `utils/text.ts`)

- `resolveTranscript(ctx, supports)`: `supports ? ctx : collapseSystemMessages(ctx)`.
- `collapseSystemMessages`: the replayed `getCurrentSystemMessage` leads,
  every other system message is dropped.
- `getInitialSystemMessage(messages)`: `messages[0]` when it is a system message.
- `getDeclaredTools`: every `toolsAdded` definition, first-declaration order,
  later definitions of a name replace earlier ones (`Map.set`).
- `hasToolRedefinitions`: a name declared twice with different definitions.
- `hasNonAdditiveToolChanges`: any `toolsRemoved`, or any name declared twice.
- `resolveTranscriptTools(messages, supportsAdditions)`: `anchorsAdditions =
  supportsAdditions && !hasNonAdditiveToolChanges`; `requestTools =
  anchorsAdditions ? initial.toolsAdded ?? [] : getCurrentTools(messages)`.
- `renderSystemMessageUpdate(m)`: content (when non-empty), then per section
  `Updated system prompt section "NAME":\n\nTEXT` or
  `Removed system prompt section "NAME".`, joined by `\n\n`. Tool fields are
  not rendered.
- `transformMessages` holds a system message that falls between a tool call
  and its results until the results are emitted. pipy's loop never places
  one there (turn-start messages follow the previous turn's results), so the
  hold is a no-op here; a test pins that placement.

### Per-adapter serialization (later = not the leading message)

- **Responses** (`openai-responses-shared.ts:145-352`): leading message text
  is the prompt (Codex `instructions`, `openai-codex-responses.ts:570`).
  Later message: first its anchored tool additions (when
  `anchorsAdditions`): `supportsAdditionalTools` →
  `{type:"additional_tools", role:"developer", tools:[…]}`; else
  `supportsToolSearch` → `tool_search_call`/`tool_search_output` with
  `call_id = "pi_tool_load_" + shortHash("system:" + msgIndex + ":" +
  names.join(","))`; then, if `renderSystemMessageUpdate` is non-empty,
  `{role: instructionRole, content: text}` with `instructionRole =
  model.reasoning && supportsDeveloperRole !== false ? "developer" :
  "system"`. `msgIndex` counts converted non-leading messages, skipping an
  assistant that produced no items. Top-level `tools` =
  `requestTools`.
- **Chat Completions** (`openai-completions.ts:1185-1260`): later message:
  when `anchorsAdditions` (`supportsMidConvoSystemMessages &&
  supportsMidConvoToolAdditions`, no non-additive changes) and it adds tools,
  `{role:"system", tools:[function tools]}` first; then `{role:
  instructionRole, content: renderSystemMessageUpdate}` if non-empty
  (`instructionRole = reasoning && supportsDeveloperRole ? "developer" :
  "system"`). Top-level `tools` = `requestTools`.
- **Mistral** (`mistral-conversations.ts:130,798`): later message →
  `{role:"system", content: renderSystemMessageUpdate}` if non-empty; tools =
  current tools.
- **Anthropic** (`anthropic-messages.ts:1043-1145, 1235-1432`):
  `nativeToolChanges = supportsMidConvoSystemMessages &&
  supportsMidConvoToolChanges && initial.toolsAdded non-empty &&
  !hasToolRedefinitions`. A later message becomes `{role:"system",
  content:[text block?, tool_removal*, tool_addition*]}` (tool blocks only
  with native tool changes, `{type, tool:{type:"tool_reference", name}}`),
  held and flushed directly before the next assistant message or at the end;
  empty blocks → nothing. Native tool changes: `tools = [initial tools (cache
  marker on the last), __pi_deferred_placeholder__ (defer_loading), later
  declared tools with defer_loading]`, plus the
  `mid-conversation-tool-changes-2026-07-01` beta. Otherwise `tools =
  getCurrentTools`. The conversation cache marker also lands on a trailing
  `system` message's last `text`/`tool_addition`/`tool_removal` block.
  Anthropic's configured `anthropic-beta` header (model or request headers)
  replaces the computed beta list (`getBetaFeatures`).
- **Google, Bedrock**: `collapseSystemMessages` unconditionally
  (`google-shared.ts:193`, `bedrock-converse-stream.ts:132`). No change.

## Design

### 1. Sections (`native/system_prompt_sections.py`, new)

`SystemPromptSections = tuple[tuple[str, str], ...]`, with:

- `build_system_prompt_sections(*, preamble, addendum, project_context,
  skills, cwd, resume)` → ordered, Pi-wrapped sections (empty ones omitted,
  `cwd` always, `resume` last as a custom section);
- `render_system_prompt(sections)` = Pi `getSystemMessageText` over them.

`adapters/native.py` builds them: `preamble` = the default or custom prompt
(READ2 later removed pipy's reference-roots lines); `addendum` = the append texts joined by `\n\n` (split out of
`resolve_system_prompt`, which gains `preamble`/`addendum` fields);
`project_context` = the existing renderer's body; `skills` = the existing
block's body (read-tool gate unchanged); `cwd`; `resume` = the resume block.
The composed `system_prompt` string is `render_system_prompt(sections)`.
Rendering changes: the addendum is wrapped in `<addendum>`; a `<cwd>`
section is added; the resume block is wrapped `<resume>`.

The sections travel beside the string without changing `run`'s signature:
`build_session` sets a new `CodingSession.system_prompt_sections` field,
`_wire` copies it into `SessionWiringInput`, and wiring records
`sections_for_prompt(system_prompt, sections)` on the product phase →
`loop_scope.base_system_sections`. A caller whose prompt string is not the
sections' rendering (or who passes none) gets `(("preamble", string),)`
(today's shape). `coding/session.py` sits at its 399-line ceiling: the two
added lines (field, `_wire` argument) are offset by shortening an existing
field comment, so its count is unchanged. `native/session.py` is untouched.

`loop_step` records `system_prompt_input(transcript, base_sections)`. The
`before_agent_start` suffix is a forced prompt (Pi): not recorded, and the
request for that run carries no system messages (below).

### 2. Request carrier (`native/models.py`)

```python
@dataclass(frozen=True, slots=True)
class ProviderSystemMessage:
    position: int               # number of request.messages before it
    message: AgentSystemMessage

ProviderRequest.system_messages: tuple[ProviderSystemMessage, ...] = ()
ProviderRequest.hidden_tool_names: tuple[str, ...] = ()
```

Invariant when non-empty: positions are non-decreasing and within
`0..len(messages)`, and `system_prompt == system_message_text(replay) + tail`
where `tail` is the out-of-band compaction summary suffix. Empty means the
collapse path. `freeze`/validators check the type and positions;
`snapshot_provider_request` clears it when the `before_provider_request`
hook changes `system_prompt` (a forced prompt), and records the names a
narrowing hook removed from `available_tools` in `hidden_tool_names` (Pi's
explicit `_hiddenDeclarations` set). `materialize_provider_request` drops
aborted/failed assistant messages (`provider_replay_messages`); it re-anchors
each system message to the number of kept messages before it, so it stays
after the same surviving messages.

The collapse path is byte-identical to today's request with two intended
exceptions, each tested: the prompt text changes where the sections do
(§1), and a dynamic tool load is no longer deferred through the removed
marker path (§6): Pi sends the current tools as ordinary definitions on the
collapse path (for example `claude-opus-4-7`, which pipy detected as
supporting `tool_reference` blocks; Pi removed that detection).

### 3. Filling it (product)

Pi builds every request from the persisted session projection
(`_installAgentRequestProjection`). pipy does the same:

- `CodingSessionTreeContext.system_anchors` (new, filled by
  `build_coding_context`; Pi projection rules): anchors relative to its
  `messages` — the compaction
  checkpoint at 0, system messages after the compaction boundary at their
  position, system entries in the retained pre-boundary range dropped.
- The loop computes the turn's system message (`declare_tool_changes`) before
  calling `prepare` and hands it over on the active input:
  `AgentActiveInput.turn_system_message` (request-view data, like
  `request_overlay`; identity checks unchanged). It still emits it after
  `TurnStarted` as today.
- The product attaches system messages only when the bound provider port
  accepts them (`supports_mid_convo_system_messages`, the adapter's resolved
  compat flag; absent → false). Every other request is the collapse path by
  construction: no carrier, unchanged budget estimate.
- `_request_values`: when the run is not forced, take the tree's coding
  messages and anchors; if they are a value-equal prefix of the coding
  context's messages, map anchors into the request frame (positions after the
  accepted message shift by the overlay length) and add the turn's message at
  the accepted message's index (turn 0) or at the end (later turns).
  Otherwise send none (collapse; safe).

### 3a. Budget

`estimate_request` counts what a mid-conversation adapter sends: the system
text is the leading message's text plus the tail (not the current replay,
which can be shorter after a section is removed), plus each later message's
`render_system_message_update` text and one framing unit per later message,
plus every declaration an adapter may keep on the wire beyond
`available_tools` (name, description, parameters JSON, one framing unit
each): all later `toolsAdded`, and the leading message's declarations that
are no longer advertised (Anthropic keeps removed tools declared). This
over-counts a still-current anchored tool. Requests without the carrier
estimate exactly as before. Tests: repeated section replacements, a removed
later tool and a removed large initially declared tool raise the estimate; a
collapse-path request's estimate is unchanged.

### 4. Resolver (`native/providers/transcript.py`, new, pure)

`resolve_request_transcript(request, *, supports_mid_convo) ->
ResolvedTranscript(leading_text, items, leading_tools, current_tools,
declared_tools, later)` where `items` interleaves `AgentMessage` and later
`AgentSystemMessage` values in request order. Collapse (flag off, no system
messages, no leading message at position 0, or the prompt invariant fails):
`leading_text = system_prompt`, `items = messages`, tools =
`available_tools`. Hidden declarations (Pi
`_installHiddenDeclarationsProjection`): the request's `hidden_tool_names`
are filtered from every message's `toolsAdded`/`toolsRemoved`; ordinary
removals and their original declarations stay (a removal followed by a
re-addition stays non-additive). Also `render_system_message_update`,
`transcript_tools(later_messages, anchors_additions)`,
`has_tool_redefinitions`, `has_non_additive_tool_changes`,
`declared_tools`, and `declaration_definition(decl) -> ToolDefinition`.

### 5. Adapters

- Codex (`openai_codex_provider.py`) and Responses wire: `instructions` =
  leading text; input per the Responses rules above; `tools` =
  `requestTools`. OpenAI, Azure and Codex all read `supportsAdditionalTools`/
  `supportsToolSearch`.
- Chat Completions wire (`chat_messages`): leading system envelope as today,
  later messages per the rules; completions/OpenRouter/Cloudflare read
  `supportsMidConvoToolAdditions`, Mistral does not (Pi Mistral has no tool
  additions).
- Anthropic: rules above; beta header only when native tool changes are used
  and no configured `anthropic-beta` header exists.
- Construction (`provider_construction.py`): each flag resolved on its own
  (explicit `compat.<flag>` bool wins, else the Pi default), passed to the
  adapters as fields.

### 6. Removing the marker path

Delete `AgentToolResultMessage.added_tool_names` (message, validators,
session-tree field, tool capabilities, request budget), `deferred_tools.
split_deferred_tools`/`responses_tool_search_items`, Anthropic
`tool_reference` result blocks and `supports_tool_references`/
`resolve_*_tool_references`, and the `supportsToolReferences` drift key. The
turn after an extension tool adds tools already records a tools-only system
message (SYS1a); the adapters above anchor it.

## Known divergences kept (pre-existing, recorded)

- Responses: pipy sends the leading prompt as `instructions`; Pi's
  openai-responses sends it as the first input message (developer/system).
  Chat Completions: pipy's leading envelope is always `system`; Pi uses the
  instruction role. Neither changes here; later messages use Pi's role.
- Tool `strict` fields, Anthropic's other betas (fine-grained streaming,
  interleaved thinking, fallback), empty-prompt defaults (Codex
  `"You are a helpful assistant."`).
- A session whose first system message is not at position 0 (made before
  SYS1a) collapses; Pi sends the full state as a later message.

## Tests (done-when)

1. Sections: builder order/wrapping/omission; custom prompt + addendum;
   `render` = Pi text; the product records sections and a
   `before_agent_start` suffix is not recorded.
2. Tree anchors: plain, after compaction (checkpoint at 0, retained system
   entries dropped, later ones kept).
3. Request filling: turn 0 anchor before the accepted message, turn k at the
   end, overlay shift, prefix mismatch → none, forced run → none, hook
   prompt change → cleared, hook narrowing → `hidden_tool_names`;
   materialization with an aborted assistant re-anchors (middle and
   trailing anchors).
4. Resolver: collapse cases, tail handling, hidden declarations (and an
   ordinary removal + re-addition that is not hidden), tool predicates
   (redefinition vs removal), `render_system_message_update`.
   Collapse-path regression: an ordinary request is unchanged; a dynamic
   tool load on a model without the flags (`claude-opus-4-7`, a Codex row
   with the flags off) sends the current tools as ordinary definitions.
5. Exact request bodies per family, flag on and off: Codex (additional_tools,
   tool_search with Pi call id, removal → current tools), OpenAI Responses
   (developer role; `supportsDeveloperRole:false` → system), Azure (same
   flags), Chat Completions (developer/system, kimi tools message,
   non-additive fallback), Mistral, Anthropic (text-only system message,
   native tool changes with placeholder + defer_loading + removal blocks +
   beta + cache marker on trailing system block, held placement before the
   next assistant, redefinition fallback, configured beta header wins),
   Google/Bedrock unchanged.
6. Catalog: construction reads each flag independently; `just
   catalog-drift` CLEAN with the new tracked keys.
7. Gates: `just check`; every `scripts/parity_checks/*`;
   `scripts/parity_score.sh`; `automation_pi_comparison.py` and
   `session_tree_pi_comparison.py` against real Pi; a live openai-codex
   `gpt-6.1-sol` multi-turn run with a mid-session tool-set change, isolated
   config.
8. Docs: `docs/provider-catalog.md`, `docs/session-tree.md`/
   `docs/automation-rpc.md` where they describe provider behaviour,
   `docs/pi-parity.md`, `CHANGELOG.md` `[Unreleased]`, backlog (SYS1b entry
   → SYS1c remainder).
