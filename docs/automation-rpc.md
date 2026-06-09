# Pi-Style Automation Modes: JSON Event Stream and RPC Protocol

Status: **shipped** (current). The Pi-compatible headless automation surfaces
described here — `--mode json`, `--print`/`-p`, and `--mode rpc` — are
implemented and gated by
`scripts/parity_checks/automation_rpc_conformance.py --json` plus the focused
tests in `tests/test_native_automation_*.py`. This document is both the design
reference and the behavior contract; it is a pipy-owned Python design that reuses
the real native tool loop and session tree, not a TypeScript port. The original
specification was researched from the local Pi reference on 2026-06-02.

Implementation map: `src/pipy_harness/native/automation/` (`jsonl.py` framing,
`events.py`/`serialize.py` the Pi-shaped event vocabulary, `run_modes.py` the
`--mode json`/`--print` one-shot drivers, `rpc.py` the long-lived RPC server);
the event surface is emitted from the real `NativeToolReplSession.run` loop via
the optional `automation_observer`; the CLI wiring is `pipy repl --mode
json|rpc` / `--print` in `src/pipy_harness/cli.py`.

## Sources

Local reference checkout at `/Users/jochen/src/pi-mono`, especially:

- `packages/coding-agent/src/cli/args.ts` — `--mode text|json|rpc`, `--print/-p`
  parsing and help text.
- `packages/coding-agent/src/main.ts` — `resolveAppMode(...)` mode dispatch and
  `toPrintOutputMode(...)`.
- `packages/coding-agent/src/modes/print-mode.ts` — the `--mode json`
  full-event stream emitter and `-p` one-shot text path.
- `packages/coding-agent/docs/json.md` — documented JSON event-stream contract.
- `packages/coding-agent/src/modes/rpc/rpc-types.ts` — the authoritative RPC
  command, response, state, and extension-UI vocabulary.
- `packages/coding-agent/src/modes/rpc/rpc-mode.ts` — the RPC dispatch loop,
  async prompt handling, extension-UI bridge, shutdown, and backpressure.
- `packages/coding-agent/src/modes/rpc/jsonl.ts` — strict LF-only JSONL framing.
- `packages/coding-agent/src/modes/rpc/rpc-client.ts` — request/response
  correlation and the reference client API.
- `packages/coding-agent/src/core/agent-session.ts` — `AgentSessionEvent`
  union and `SessionStats`.
- `packages/agent/src/types.ts` — base `AgentEvent` union.
- `packages/ai/src/types.ts` — `AssistantMessageEvent` streaming-delta union.
- `packages/coding-agent/src/core/bash-executor.ts` — `BashResult`.
- `packages/coding-agent/src/core/compaction/compaction.ts` — `CompactionResult`.

Pipy current state:

- `docs/harness-spec.md` (`--native-output json`, `--stream`, CLI output
  convention, Deferred Work: "Network/wire-protocol RPC daemon").
- `docs/backlog.md` (Streaming Output Parity Track; "RPC and automation modes"
  in Current Largest Gaps item 5).
- `docs/session-tree.md` and `docs/extension-api.md` (house style; the native
  session tree and Python extension API this mode composes with).
- `src/pipy_harness/sdk.py` (`run_native`, `StreamChunkSink`).
- `src/pipy_harness/cli.py` (`pipy run`, `--native-output json`,
  `_native_json_output(...)`, `--stream`).

## Target Outcome / Goal

Pipy should expose Pi-compatible headless automation surfaces through
pipy-owned Python boundaries:

- A Pi-style **`--mode json` full-event stream**: a one-shot run that emits the
  complete session event sequence (message content, tool calls/results,
  lifecycle, queue, compaction, retry) as LF-delimited JSON lines, exactly the
  surface Pi documents in `packages/coding-agent/docs/json.md`.
- A Pi-style **`--mode rpc` protocol**: a long-lived headless process that reads
  JSONL commands on stdin and emits JSONL responses and asynchronous session
  events on stdout, covering Pi's full command vocabulary (prompting, steering,
  follow-up, abort, model/thinking cycling, queue modes, compaction, retry,
  bash, session ops, fork/clone/switch, naming, message/command introspection)
  plus the extension-UI request/response channel.
- A **`--print/-p` one-shot** text mapping.

These are full-content automation surfaces. **The JSON/RPC event stream emits
full session events — assistant message content, tool-call arguments, tool
results, bash output — exactly like Pi.** The "metadata-first privacy" posture
of the `pipy-session` archive does **not** apply to these modes. Only auth
secrets and credential tokens are never emitted (standard). This is a
deliberate, explicit divergence from the archive's redaction rules, scoped to
the automation transport.

Pipy's existing `--native-output json` is a pipy-specific divergence that
emits one final **metadata-only** object (counters, labels, record paths; see
`_native_json_output(...)` in `src/pipy_harness/cli.py`). It is **not** a
full-event stream and has no Pi equivalent. This spec **replaces and retires**
`--native-output json` with the Pi-style `--mode json` full-event stream.

This work composes with two existing target specs:

- `docs/session-tree.md`: the native session tree is the durable store that
  backs RPC session/fork/clone/switch/compaction/tree commands.
- `docs/extension-api.md`: the RPC extension-UI channel is the headless
  transport for the Python extension UI capability surface (`ctx.ui`).

The implementation may land in reviewed milestones, but the objective goal is
the full Pi-style automation vocabulary verified by the conformance gate below.

## (a) `--mode json`: Pi-Style Full-Event Stream

`pipy ... --mode json "<prompt>"` runs a single non-interactive session, sends
the prompt(s), streams every session event as one JSON object per LF-delimited
line to stdout, then exits. This mirrors `runPrintMode(..., { mode: "json" })`
in `packages/coding-agent/src/modes/print-mode.ts`.

### Stream framing

- Output is LF-only JSONL (`"\n"` separator, never `\r\n`). Payload strings may
  contain U+2028/U+2029; readers split on `\n` only. This matches
  `serializeJsonLine(...)` in `packages/coding-agent/src/modes/rpc/jsonl.ts`.
- The **first line** is the native session header (from the session tree store,
  see `docs/session-tree.md`):

  ```json
  {"type":"session","version":1,"id":"<uuid>","timestamp":"<iso>","cwd":"<abs path>"}
  ```

  Pi emits `getHeader()` first when present; pipy emits its native session
  header. The `version` field above is the **pipy native-session-tree format
  version** (`docs/session-tree.md`), **not** Pi's session version. Pi's current
  session format is **version 3** (`CURRENT_SESSION_VERSION = 3` in
  `session-manager.ts:28`; Pi v1 headers omit the field entirely). pipy does not
  copy Pi's version number; the two version namespaces are independent.
- Subsequent lines are session events emitted in occurrence order as the run
  proceeds.
- Diagnostics, warnings, and errors that are not session events go to **stderr**
  (matching pipy's existing CLI output convention and Pi's `console.error`
  usage). stdout carries only the header and event JSON lines.

### Event vocabulary pipy must emit

The event union is Pi's `AgentSessionEvent` =
`AgentEvent ∪ session-extension events`. Pipy must emit the full set with the
same `type` discriminators and semantically equivalent payload fields, sourced
from pipy's own native session/tool-loop event model (`tool_loop_session.py`,
`ProviderPort`), not a literal TypeScript port.

Base agent-lifecycle events (Pi `AgentEvent`, `packages/agent/src/types.ts`):

| `type` | Payload fields | Meaning |
| --- | --- | --- |
| `agent_start` | (none) | One accepted user prompt begins its agent run. |
| `agent_end` | `messages: Message[]`, `willRetry: boolean` | Run settled. pipy's session form adds `willRetry` (Pi's `AgentSessionEvent` overrides `agent_end` to add it). |
| `turn_start` | (none) | One model/tool-loop turn starts. |
| `turn_end` | `message: Message`, `toolResults: ToolResultMessage[]` | One turn ends (assistant message + any tool results). |
| `message_start` | `message: Message` | A user/assistant/tool-result message begins. |
| `message_update` | `message: Message`, `assistantMessageEvent: AssistantMessageEvent` | Streaming delta for an assistant message (assistant only). |
| `message_end` | `message: Message` | A message is finalized. |
| `tool_execution_start` | `toolCallId: string`, `toolName: string`, `args: object` | A tool call begins executing. |
| `tool_execution_update` | `toolCallId`, `toolName`, `args`, `partialResult` | Bounded progress from a running tool. |
| `tool_execution_end` | `toolCallId`, `toolName`, `result`, `isError: boolean` | A tool call finished. |

Session-extension events (Pi `AgentSessionEvent`,
`packages/coding-agent/src/core/agent-session.ts`):

| `type` | Payload fields | Meaning |
| --- | --- | --- |
| `queue_update` | `steering: string[]`, `followUp: string[]` | Full pending steering and follow-up queues whenever they change. |
| `compaction_start` | `reason: "manual" \| "threshold" \| "overflow"` | Compaction begins. |
| `compaction_end` | `reason`, `result: CompactionResult \| undefined`, `aborted: boolean`, `willRetry: boolean`, `errorMessage?: string` | Compaction settled. |
| `session_info_changed` | `name: string \| undefined` | Session display name set/cleared. |
| `thinking_level_changed` | `level: ThinkingLevel` | Thinking/reasoning level changed (where the provider supports it). |
| `auto_retry_start` | `attempt: number`, `maxAttempts: number`, `delayMs: number`, `errorMessage: string` | Auto-retry attempt scheduled. |
| `auto_retry_end` | `success: boolean`, `attempt: number`, `finalError?: string` | Auto-retry attempt settled. |

`assistantMessageEvent` sub-union (Pi `AssistantMessageEvent`,
`packages/ai/src/types.ts`) carried inside `message_update`. Pipy must emit the
delta kinds it supports; the full Pi vocabulary is: `start`, `text_start`,
`text_delta` (`delta: string`), `text_end` (`content: string`),
`thinking_start`, `thinking_delta`, `thinking_end`, `toolcall_start`,
`toolcall_delta`, `toolcall_end` (`toolCall`), `done`
(`reason: "stop" | "length" | "toolUse"`, `message`), `error`
(`reason: "aborted" | "error"`, `error`). Each carries a `contentIndex` and the
in-progress `partial` assistant message where Pi does. Pipy streams `text_delta`
first (its provider streaming path already iterates text deltas); thinking and
tool-call deltas are emitted where the pipy provider/tool-loop produces them and
are otherwise omitted, never faked.

Message content shapes (carried inside the `message`/`messages`/`toolResults`
fields) follow Pi's `UserMessage`, `AssistantMessage`, `ToolResultMessage`, and
the coding-agent extended messages `BashExecutionMessage`, `CustomMessage`,
`BranchSummaryMessage`, `CompactionSummaryMessage`. Pipy reuses its native
message/tool dataclasses serialized to the same role/content discriminators;
exact byte-for-byte field matching with Pi is not the gate (see Verification).

### Canonical `--mode json` sequence (deterministic fake provider, no tools)

The leading `version` below is the pipy native-session-tree format version (see
above), not Pi's session version (Pi is currently at 3):

```text
{"type":"session","version":1,"id":"...","timestamp":"...","cwd":"..."}
{"type":"agent_start"}
{"type":"turn_start"}
{"type":"message_start","message":{"role":"assistant","content":[]}}
{"type":"message_update","message":{...},"assistantMessageEvent":{"type":"text_delta","contentIndex":0,"delta":"SEEN:ROOT","partial":{...}}}
{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"SEEN:ROOT"}]}}
{"type":"turn_end","message":{...},"toolResults":[]}
{"type":"agent_end","messages":[...],"willRetry":false}
```

## (b) `--mode rpc`: Headless Stdin/Stdout JSONL Protocol

`pipy ... --mode rpc` starts a long-lived headless process. It reads one JSON
command per line on stdin, dispatches it, and emits JSON responses plus
asynchronous session events on stdout. The process stays alive until stdin EOF,
SIGTERM/SIGHUP, or an extension-requested shutdown. This mirrors `runRpcMode`
in `packages/coding-agent/src/modes/rpc/rpc-mode.ts`.

### Framing, correlation, and concurrency model

- Stdin and stdout both use strict **LF-only JSONL** framing (the same reader
  semantics as `attachJsonlLineReader` / `serializeJsonLine`; pipy implements an
  equivalent stdlib reader, splitting on `\n`, tolerating a trailing `\r`,
  preserving other Unicode separators inside strings).
- Each command may carry an optional `id: string`. The matching response echoes
  the same `id` so clients correlate request→response. Commands without an `id`
  still receive a response (with `id` absent/undefined). **Exception:** for an
  **unknown** command type Pi calls `error(undefined, ...)` — the input `id` is
  dropped, so an unknown-command error response carries no `id` even if the
  request had one (`rpc-mode.ts:665-668`). Clients must not rely on `id`
  correlation for unknown-command errors; pipy matches this behavior.
- Three classes of stdout objects:
  1. **Responses**: `{"type":"response","command":"<cmd>","success":true|false,...}`
     correlated by `id`.
  2. **Session events**: the full `--mode json` event vocabulary from (a),
     emitted asynchronously as the agent runs. These are **not** correlated to
     any command `id`.
  3. **Extension UI requests**: `{"type":"extension_ui_request","id":...,...}`
     correlated by their own `id` against `extension_ui_response` inputs.
- Output is serialized through a single writer so lines never interleave
  mid-record. Pi's stdout **backpressure** (`waitForRawStdoutBackpressure()`) is
  **selective, not per-line**: only normal **command responses**
  (`rpc-mode.ts:736-740`) and **parse-error** responses (`702-715`) await the
  writer drain before the next input line is handled. Asynchronous **session
  events** are fire-and-forget `output(event)` with no await
  (`rpc-mode.ts:353-355`), and the async **prompt-preflight `success`** response
  is also fire-and-forget (`398-402`). Pipy should mirror this: a single
  serialized writer, await drain on command/parse responses, and best-effort
  emission for streamed events and the prompt success. With stdlib only the
  writer is a blocking buffered `sys.stdout.buffer.write(...)` + `flush()` on a
  dedicated writer thread, or a bounded queue drained by the writer (see
  Invariants on asyncio).
- **Dispatch is not strictly serialized in Pi.** Each input line is dispatched
  via `void handleInputLine(line)` with no serial queue
  (`rpc-mode.ts:759-762`), so command handlers can overlap (e.g. a long async
  `prompt` while a later `get_state` resolves). Ordering of *responses* is
  therefore not guaranteed to match input arrival order. pipy **may** choose to
  serialize command handling (e.g. one-at-a-time dispatch with a queue) for
  determinism — but that is a pipy implementation decision, not Pi behavior, and
  it must still let `steer`/`follow_up`/`abort`/`extension_ui_response` reach the
  running prompt promptly rather than blocking behind it.
- `prompt` is **asynchronous**: the command returns its authoritative success
  response only after prompt preflight succeeds, and session events stream while
  the turn runs. All other commands resolve to a single response. `steer`,
  `follow_up`, and `abort` are accepted while a prompt is in flight (mid-turn).
- Client correlation reference: `packages/coding-agent/src/modes/rpc/rpc-client.ts`
  resolves a response when `data.type === "response" && data.id` matches a
  pending request, otherwise routes the line to event listeners.

### Command vocabulary (stdin)

Every command is `{ "type": "<name>", "id"?: string, ...args }`. The complete
set pipy must accept (Pi `RpcCommand`, `rpc-types.ts`), with the response each
produces (Pi `RpcResponse`):

Prompting / run control:

| Command | Args | Response `data` | Notes |
| --- | --- | --- | --- |
| `prompt` | `message: string`, `images?: ImageContent[]`, `streamingBehavior?: "steer" \| "followUp"` | none (`success` only) | Async: success emitted after preflight; events follow. On preflight failure emits an error response. |
| `steer` | `message: string`, `images?` | none | Mid-turn interrupt; queued/applied per steering mode. |
| `follow_up` | `message: string`, `images?` | none | Queue a message after the current run. |
| `abort` | (none) | none | Abort the current run. |
| `new_session` | `parentSession?: string` | `{ cancelled: boolean }` | Starts a new native session; rebinds event subscription. An extension may cancel. |

State / introspection:

| Command | Args | Response `data` |
| --- | --- | --- |
| `get_state` | (none) | `RpcSessionState` (see below) |
| `get_messages` | (none) | `{ messages: Message[] }` |
| `get_session_stats` | (none) | `SessionStats` (see below) |
| `get_last_assistant_text` | (none) | `{ text: string \| null }` |
| `get_fork_messages` | (none) | `{ messages: [{ entryId, text }] }` |
| `get_commands` | (none) | `{ commands: RpcSlashCommand[] }` |

Model / thinking:

| Command | Args | Response `data` |
| --- | --- | --- |
| `set_model` | `provider: string`, `modelId: string` | the selected `Model` (error if not found) |
| `cycle_model` | (none) | `{ model, thinkingLevel, isScoped } \| null` |
| `get_available_models` | (none) | `{ models: Model[] }` |
| `set_thinking_level` | `level: ThinkingLevel` | none |
| `cycle_thinking_level` | (none) | `{ level } \| null` |

Queue modes:

| Command | Args | Response |
| --- | --- | --- |
| `set_steering_mode` | `mode: "all" \| "one-at-a-time"` | none |
| `set_follow_up_mode` | `mode: "all" \| "one-at-a-time"` | none |

Compaction / retry:

| Command | Args | Response `data` |
| --- | --- | --- |
| `compact` | `customInstructions?: string` | `CompactionResult` |
| `set_auto_compaction` | `enabled: boolean` | none |
| `set_auto_retry` | `enabled: boolean` | none |
| `abort_retry` | (none) | none |

Bash:

| Command | Args | Response `data` |
| --- | --- | --- |
| `bash` | `command: string` | `BashResult` (see below) |
| `abort_bash` | (none) | none |

Session ops (native session tree, `docs/session-tree.md`):

| Command | Args | Response `data` |
| --- | --- | --- |
| `switch_session` | `sessionPath: string` | `{ cancelled: boolean }` (rebinds on success) |
| `fork` | `entryId: string` | `{ text: string, cancelled: boolean }` (rebinds on success) |
| `clone` | (none) | `{ cancelled: boolean }` (clones current active branch; error if no current leaf) |
| `set_session_name` | `name: string` | none (error if name is empty after trim) |
| `export_html` | `outputPath?: string` | `{ path: string }` — deferred Pi-feature; may return an error response until implemented |

That is the full Pi vocabulary: **29 command types** — `prompt`, `steer`,
`follow_up`, `abort`, `new_session`, `get_state`, `set_model`, `cycle_model`,
`get_available_models`, `set_thinking_level`, `cycle_thinking_level`,
`set_steering_mode`, `set_follow_up_mode`, `compact`, `set_auto_compaction`,
`set_auto_retry`, `abort_retry`, `bash`, `abort_bash`, `get_session_stats`,
`export_html`, `switch_session`, `fork`, `clone`, `get_fork_messages`,
`get_last_assistant_text`, `set_session_name`, `get_messages`, `get_commands`.
Both `get_messages` and `get_commands` are distinct command types and are
counted. (Plus `extension_ui_response`, which is an input control line, not a
command.)

Pipy command names match Pi exactly so an existing Pi RPC client drives pipy
unchanged. Commands targeting features pipy has not yet implemented (e.g.
`export_html`, or model/thinking ops on a single-provider build) must return a
well-formed **error response**, never a crash or an unknown-command response.

### Response envelope

Success: `{ "id"?, "type": "response", "command": "<cmd>", "success": true, "data"?: <obj> }`
(omit `data` when the command has no payload).

Error (any command may fail):
`{ "id"?, "type": "response", "command": "<cmd>", "success": false, "error": "<message>" }`.

Unknown command: `{ "type": "response", "command": "<type>", "success": false, "error": "Unknown command: <type>" }`.

Unparseable stdin line:
`{ "type": "response", "command": "parse", "success": false, "error": "Failed to parse command: <detail>" }`.

The `error` string carries no secrets/tokens; it is a bounded human-readable
message (it may contain command/model identifiers and tool/bash error text,
which is in-scope full-content for this surface).

### `RpcSessionState` (`get_state` data)

```json
{
  "model": { "provider": "...", "id": "...", "...": "..." },
  "thinkingLevel": "off|minimal|low|medium|high|xhigh",
  "isStreaming": false,
  "isCompacting": false,
  "steeringMode": "all|one-at-a-time",
  "followUpMode": "all|one-at-a-time",
  "sessionFile": "/path/to/native-session.jsonl",
  "sessionId": "<uuid>",
  "sessionName": "<name|undefined>",
  "autoCompactionEnabled": true,
  "messageCount": 0,
  "pendingMessageCount": 0
}
```

`SessionStats` (`get_session_stats`): `sessionFile`, `sessionId`,
`userMessages`, `assistantMessages`, `toolCalls`, `toolResults`,
`totalMessages`, `tokens: { input, output, cacheRead, cacheWrite, total }`,
`cost`, `contextUsage?`.

`BashResult` (`bash`): `output: string` (combined stdout+stderr, possibly
truncated), `exitCode: number | undefined`, `cancelled: boolean`,
`truncated: boolean`, `fullOutputPath?: string`.

`CompactionResult` (`compact`): `summary: string`, `firstKeptEntryId: string`,
`tokensBefore: number`, `details?: object`.

`RpcSlashCommand` (`get_commands`): `name: string`, `description?: string`,
`source: "extension" | "prompt" | "skill"`, `sourceInfo: object`. pipy lists
its extension commands (`docs/extension-api.md`), prompt templates, and skills
(`skill:<name>`).

### Mid-turn steering, follow-up, and abort

While a `prompt` run is in flight:

- `steer` injects a message into the running run subject to `steeringMode`
  (`all` applies every queued steer; `one-at-a-time` applies one per
  opportunity). The change is observable on stdout as a `queue_update` event.
- `follow_up` enqueues a message to run after the current run settles, subject
  to `followUpMode`; also observable via `queue_update`.
- `abort` cancels the active run; the run emits its terminating `message_end` /
  `turn_end` / `agent_end` events and the `abort` response resolves.
- `prompt` itself may carry `streamingBehavior: "steer" | "followUp"` so a
  prompt sent during an active run is treated as a steer or follow-up.

**pipy implementation note (documented boundary).** The list above is Pi's
in-turn-injection target. pipy's current `--mode rpc` does not inject a message
into the model context of the *already-running* turn; instead `steer` and
`follow_up` are **queued during the active run and delivered as the next run(s)
after it settles**, exactly **one message per turn boundary, steering first**
(follow-up only once steering is empty); the delivered run's `agent_end` is the
next boundary that drains the following message. `steeringMode`/`followUpMode`
are accepted and reported in `get_state`, but pipy's after-turn delivery is
uniformly one-per-boundary (a documented simplification of Pi's in-turn `all`
vs `one-at-a-time` application). Every enqueue/drain is observable via
`queue_update`, the queues remain the single source for `pendingMessageCount`
(nothing is bulk-pushed behind the queue's back), and they are always consumed
and cleared — never reporting stale pending messages.
`abort` cancels the active run and **discards queued steering for that run** (it
targeted the aborted turn); queued follow-ups, which are meant to run after,
remain and drain next. This is a deliberate pipy-owned simplification of Pi's
mid-turn injection: queued input still reaches the model promptly (on the next
turn) and the queue stays truthful, without re-entering a live provider turn. A
`prompt` sent while a run is active is likewise routed to the observable queue by
its `streamingBehavior` (`steer` -> steering queue, otherwise the follow-up
queue) rather than being silently deferred, so it appears in `queue_update`/
`pendingMessageCount`. True in-turn injection of a message into the
already-running provider turn remains a follow-on.

### Session switching, fork, clone

`switch_session`, `fork`, `clone`, and `new_session` change the active session
and must **rebind** the event subscription so subsequent session events come
from the new active session (Pi's `rebindSession()`), and re-bind the
extension-UI context. Each may be cancelled by an extension hook
(`docs/extension-api.md` `session_before_switch` / `session_before_fork`),
surfaced as `{ cancelled: true }` with no rebind. These commands read/write the
native session tree (`docs/session-tree.md`).

### Bash, model, and thinking controls

- `bash` runs a command on a worker thread through pipy's real bash
  tool/sandbox and returns a full `BashResult` including bounded, secret-scrubbed
  output. The sandbox is not externally cancellable, so `abort_bash` returns a
  well-formed error while a bash is in flight (and a no-op success when idle)
  rather than falsely claiming a cancel. This is the same bash executor the tool
  loop uses; full output is in-scope for this surface.
- **Model / thinking controls — current build (documented boundary).** The RPC
  model/thinking commands are accepted and their effect is observable in
  `get_state`, but in the current `--mode rpc` build they do **not** mutate the
  live provider or propagate into the next provider request:
  - `get_available_models` returns the configured provider/model; `set_model`
    succeeds for the currently selected provider/model and returns a well-formed
    error for any other (the automation build runs a single configured
    provider); `cycle_model` returns `null` data (nothing to cycle to). No live
    provider switch happens.
  - `set_thinking_level`/`cycle_thinking_level` validate and **record** the
    requested level, surface it in `get_state.thinkingLevel`, and emit a
    `thinking_level_changed` event, but the recorded level is not yet threaded
    into the running session's provider requests.
  Live provider switching and threading the thinking level into the active
  provider request over RPC are explicit follow-ons; the accepted-and-reported
  behavior keeps the command vocabulary complete and the responses well-formed.

### Compaction

`compact` runs a real context compaction and returns a `CompactionResult`;
`compaction_start` / `compaction_end` events bracket it on stdout.
`set_auto_compaction` toggles threshold/overflow compaction, which emits the
same event pair with `reason: "threshold" | "overflow"`. Durable compaction is
written to the native session tree (`docs/session-tree.md`).

### Extension UI request/response channel

When a Python extension (`docs/extension-api.md`) needs user interaction in
headless mode, pipy emits an `extension_ui_request` on stdout and waits for a
matching `extension_ui_response` on stdin, correlated by `id`. Pipy mirrors
Pi's `ExtensionUIContext` request methods (`rpc-types.ts`
`RpcExtensionUIRequest`):

| `method` | Request fields | Expected response | Notes |
| --- | --- | --- | --- |
| `select` | `title`, `options: string[]`, `timeout?` | `{ value }` or `{ cancelled: true }` | Returns chosen option or cancel. |
| `confirm` | `title`, `message`, `timeout?` | `{ confirmed: boolean }` or `{ cancelled: true }` | |
| `input` | `title`, `placeholder?`, `timeout?` | `{ value }` or `{ cancelled: true }` | |
| `editor` | `title`, `prefill?` | `{ value }` or `{ cancelled: true }` | |
| `notify` | `message`, `notifyType?: "info"\|"warning"\|"error"` | (none) | Fire-and-forget. |
| `setStatus` | `statusKey`, `statusText` | (none) | Fire-and-forget. |
| `setWidget` | `widgetKey`, `widgetLines: string[]\|undefined`, `widgetPlacement?: "aboveEditor"\|"belowEditor"` | (none) | String-array widgets only. |
| `setTitle` | `title` | (none) | Fire-and-forget. |
| `set_editor_text` | `text` | (none) | Fire-and-forget. |

Input control line (stdin, not a command):
`{ "type": "extension_ui_response", "id": "<request id>", ... }` with one of
`{ value: string }`, `{ confirmed: boolean }`, or `{ cancelled: true }`. A
pending request with a `timeout` resolves to its safe default if no response
arrives. UI capabilities that require a real TUI (working indicator, custom
header/footer/components, theme switching, autocomplete, tool expansion) are
**not supported** in RPC mode and degrade deterministically — matching Pi's
no-op stubs in `rpc-mode.ts`. Extension errors are surfaced as
`{ "type": "extension_error", "extensionPath", "event", "error" }` on stdout.

### Lifecycle and shutdown

- The process runs until: stdin `end`/EOF (graceful shutdown), SIGTERM (exit
  143) or SIGHUP (exit 129), or an extension shutdown request after the current
  command settles.
- On shutdown pipy unsubscribes event listeners, disposes the runtime, flushes
  stdout (except on SIGTERM), and exits. Pipy must kill any tracked detached
  child processes started by `bash` (Pi's `killTrackedDetachedChildren()`).

## (c) `--print/-p` One-Shot Mapping

`pipy ... -p "<prompt>"` / `--print` runs one non-interactive turn and prints
**only the final assistant text** to stdout, then exits, mirroring
`runPrintMode(..., { mode: "text" })`. Mapping rules from Pi (`args.ts`,
`main.ts`):

- `-p`/`--print` selects text one-shot mode. An immediately following bare
  argument that is not a flag/`@file` is consumed as the prompt.
- Mode resolution precedence matches `resolveAppMode(...)`: `--mode rpc` -> rpc;
  `--mode json` -> json full-event stream; `--print` **or non-TTY stdin** ->
  text one-shot; otherwise interactive. So piping into pipy with no TTY behaves
  like `-p` by default.
- On a failed/aborted final assistant message, text mode prints the error to
  stderr and exits non-zero; otherwise it prints each assistant text content
  block to stdout and exits 0.
- `@file` args and trailing positional messages are sent as prompts in order,
  exactly as in interactive/print parsing.

Pipy's existing `pipy run` text behavior (final native provider text to stdout
on success, diagnostics to stderr) is the same semantic contract and is the
natural home for `-p`/`--print`; the spec does not require a new subcommand name
if `pipy run` grows the `--print` and `--mode` flags instead.

## (d) Relationship to / Replacement of `--native-output json`

`--native-output json` (pipy-only) emits one final metadata object via
`_native_json_output(...)`: `schema: "pipy.native_output"`, `run_id`, `status`,
`exit_code`, record paths, and a `capture` block asserting `prompt_stored:
false`, `model_output_stored: false`, `tool_payloads_stored: false`. It is a
**metadata-only divergence** with no Pi analogue and is explicitly listed in
`docs/harness-spec.md` as "one final metadata-only JSON object … not a JSONL
event stream."

This spec **retires** `--native-output json` and replaces it with `--mode json`:

- `--mode json` is the Pi-compatible full-event JSONL stream (full message
  content, tool calls/results), not a single metadata object.
- The metadata-only summary remains valuable for the `pipy-session` archive
  surface, but it is **not** an automation event stream. The retirement plan:
  1. Add `--mode json` (and `--mode rpc`) emitting the Pi event vocabulary.
  2. Mark `--native-output json` deprecated in `--help` and docs, pointing to
     `--mode json` for full events; optionally re-implement the old single
     metadata object as a final non-Pi `{"type":"run_summary",...}` trailer
     gated behind a separate explicit flag if any consumer still needs it.
  3. Remove `--native-output json` once no caller depends on it, updating
     `docs/harness-spec.md`, `docs/backlog.md`, `docs/parity-criterion.md`, and
     `README.md`.

Archive privacy is unchanged for the `pipy-session` archive: the metadata-first
recorder still stores no prompts, model text, tool payloads, file contents, or
diffs. The **only** thing that changes is that the JSON/RPC *transport* is a
full-content surface, decoupled from the archive's redaction policy.

## (e) Python SDK Relationship

`src/pipy_harness/sdk.py` (`run_native`, `make_native_run_request`,
`StreamChunkSink`) is the **in-process Python** embedding surface — Pi's
TypeScript SDK equivalent. `--mode rpc` is the **out-of-process** embedding
surface — Pi's `RpcClient` equivalent. They are complementary:

- The SDK is for Python callers that link pipy directly (tests, smoke checks,
  library integrations). It returns a `RunResult` and finalizes a record.
- `--mode rpc` is for non-Python or process-isolated callers that want Pi's
  exact JSONL protocol, asynchronous events, and mid-turn control.
- Both reuse the **same** native runtime (`PipyNativeAdapter`,
  `NativeToolReplSession`, `ProviderPort`, the native session tree). The RPC
  loop is a thin JSONL transport + dispatch layer over the same session object
  the SDK and CLI already drive; it must not fork the runtime.
- `StreamChunkSink` (the existing streaming hook) is the in-process analogue of
  `message_update`/`text_delta` events; the RPC/JSON emitter can be built on the
  same event source the sink observes.
- A future pipy `RpcClient`-style Python helper (spawn `pipy --mode rpc`, send
  commands, await correlated responses, subscribe to events) is optional and may
  live next to the SDK; it is not required for the conformance gate.

`docs/harness-spec.md` Deferred Work currently says "Network/wire-protocol RPC
daemon … remains deferred." This spec keeps the **network/socket daemon**
deferred but promotes the **stdin/stdout JSONL RPC mode** (and the full-event
JSON stream) to a planned parity track, because Pi's `--mode rpc` is a
stdin/stdout protocol, not a network daemon.

## Invariants

These hold throughout the track, not as later deferrals:

- **pipy-owned Python boundaries.** This is a Python design that reuses the
  existing native runtime, `ProviderPort`/`ProviderResult`/`ProviderRequest`,
  the native tool loop, and the native session tree. It is not a TypeScript
  port; command/event **names and field names** match Pi for client
  compatibility, but the implementation is pipy's.
- **stdlib only, no new runtime dependencies.** JSON over stdin/stdout via the
  standard library `json` module and a hand-written LF-only JSONL reader/writer.
  No pydantic, jsonschema, attrs. **Analyze asyncio:** Pi's RPC loop is
  promise/event-driven, but pipy must not add anyio/trio/uvloop and should avoid
  taking an asyncio framework dependency it does not already use. The viable
  stdlib designs are (1) a blocking reader thread feeding a dispatcher with a
  single writer thread applying backpressure via blocking flushed writes, or
  (2) the stdlib `asyncio` event loop (stdlib, no third-party dep) if the native
  runtime is already structured around it. The reader must stay responsive to
  `steer`/`abort`/`extension_ui_response` while a `prompt` run streams events;
  the chosen concurrency model must guarantee that and must serialize all stdout
  writes through one writer so records never interleave.
- **Full-content automation surface.** `--mode json` and `--mode rpc` emit full
  session events — assistant message content, tool-call arguments, tool results,
  bash output, compaction summaries. The metadata-first archive privacy posture
  does **not** apply here. The only hard redaction is auth secrets/tokens: API
  keys, OAuth tokens, and credential material are never emitted in events,
  responses, error strings, or state.
- **Archive privacy unchanged elsewhere.** The `pipy-session` metadata recorder
  and the opt-in transcript sidecar contracts are untouched. Running in
  JSON/RPC mode does not cause the archive to start storing prompts, model text,
  or tool payloads. The full-content behavior is confined to the live transport.
- **Strict LF-only JSONL framing** on both directions, one JSON object per line,
  single serialized writer, stdout backpressure honored.
- **Exact command/response vocabulary.** All 29 Pi RPC command types (including
  both `get_messages` and `get_commands`) plus the `extension_ui_response`
  control line (which is not itself a command) are accepted; unknown commands and parse failures
  produce well-formed error responses, never crashes. Unimplemented features
  return errors, not silent success.
- **`.git` default-deny and existing safety posture** for the `bash` tool and
  file tools are preserved exactly; RPC `bash` uses the same sandbox/allowlist
  as the tool loop.
- **Composes with, does not fork, the runtime.** The same native session object
  backs CLI, SDK, JSON mode, and RPC mode. Session switching/fork/clone/new
  rebind the event subscription and extension-UI context.
- Each slice ships focused tests, a green `just check`, updated docs, a
  conventional commit, and stops for review.

## Implementation Milestones (reviewed slices)

1. **Docs only.** This spec, plus links from `docs/pi-parity.md`,
   `docs/backlog.md`, and `docs/harness-spec.md`. No runtime behavior.
2. **JSONL transport core.** A stdlib LF-only JSONL reader/writer with the
   trailing-`\r` tolerance and Unicode-separator preservation, a single
   serialized writer with backpressure, and parse-error → error-response
   handling. Focused tests pin framing, multi-record buffering, and partial
   lines.
3. **Event serialization layer.** Map pipy's native session/tool-loop/provider
   events onto the Pi `AgentSessionEvent` JSON vocabulary (names + fields),
   including `assistantMessageEvent` deltas pipy already produces. Pin the
   serialized shapes against a fixture for the fake provider.
4. **`--mode json` one-shot.** Wire mode resolution (`--mode`, `--print`/`-p`,
   non-TTY default) and emit header + full event stream for a one-shot run with
   the fake provider. Replace the metadata-only path; deprecate
   `--native-output json` in help/docs.
5. **RPC dispatch loop — synchronous commands.** Read commands, dispatch
   `get_state`, `get_messages`, `get_session_stats`, `get_last_assistant_text`,
   `get_commands`, `set_session_name`, model/thinking/queue-mode commands;
   correlate responses by `id`; emit unknown/parse errors. No async prompt yet.
6. **RPC async prompt + mid-turn control.** `prompt` (async preflight success),
   streaming session events, `steer`, `follow_up`, `abort`, `queue_update`
   emission, steering/follow-up modes.
7. **RPC bash + compaction + retry.** `bash`/`abort_bash` over the real
   sandbox, `compact`/`set_auto_compaction` with `compaction_*` events,
   `set_auto_retry`/`abort_retry` with `auto_retry_*` events.
8. **RPC session ops.** `new_session`, `switch_session`, `fork`, `clone`,
   `get_fork_messages` over the native session tree, with event-subscription
   rebind. (Depends on `docs/session-tree.md` landing.)
9. **RPC extension-UI channel.** `extension_ui_request` emission and
   `extension_ui_response` handling with timeout defaults; degrade
   TUI-only capabilities deterministically. (Depends on `docs/extension-api.md`
   UI surface.)
10. **Lifecycle + shutdown hardening.** stdin EOF, SIGTERM/SIGHUP, extension
    shutdown, detached-child cleanup, stdout flush ordering.
11. **Retire `--native-output json`.** Remove the flag (or gate the legacy
    metadata object behind an explicit separate flag), update
    `docs/harness-spec.md`, `docs/backlog.md`, `docs/parity-criterion.md`,
    `README.md`, and re-run `just parity-score`.

## Verification Plan

### Deterministic conformance gate (source of truth)

```sh
uv run python scripts/parity_checks/automation_rpc_conformance.py --json
```

The script drives pipy in RPC mode with the deterministic fake provider in a
temporary workspace and fails unless the protocol and event sequence are
correct. It must verify:

1. The process starts in `--mode rpc`, emits no stray stdout before the first
   command, and reads strict LF-only JSONL on stdin.
2. Sending `{"id":"r1","type":"prompt","message":"ROOT"}` produces, in order on
   stdout: a correlated `{"type":"response","id":"r1","command":"prompt","success":true}`
   (after preflight) and the full event sequence `agent_start`, `turn_start`,
   `message_start`, one or more `message_update` with `text_delta`,
   `message_end`, `turn_end`, `agent_end` (with `willRetry:false`).
3. The fake provider's deterministic assistant text appears in the
   `message_end`/`turn_end` payloads (full-content surface), and the streamed
   `text_delta` deltas concatenate to that final text.
4. `{"id":"r2","type":"get_state"}` returns a correlated success response whose
   `data` is a well-formed `RpcSessionState` (model, thinkingLevel, isStreaming,
   steeringMode, followUpMode, sessionId, messageCount, …).
5. `{"id":"r3","type":"get_messages"}` returns the recorded conversation
   messages with full content.
6. `{"id":"r4","type":"bash","command":"echo hi"}` returns a `BashResult` with
   `output` containing `hi`, `exitCode:0`, `cancelled:false`.
7. A mid-turn `steer` during a long fake prompt emits a `queue_update` event
   reflecting the pending steering queue, and `abort` terminates the run with a
   correlated success response and a final `agent_end`.
8. `set_session_name` then `get_state` shows the new `sessionName`;
   `get_session_stats` returns coherent counters.
9. An unknown command (`{"type":"frobnicate"}`) returns
   `{"type":"response","command":"frobnicate","success":false,"error":"Unknown command: frobnicate"}`,
   and a malformed stdin line returns a `command:"parse"` error response —
   neither crashes the process.
10. No API key, OAuth token, or credential material appears anywhere in stdout
    (events, responses, or error strings).
11. Stdin EOF triggers a clean shutdown with a zero exit code and a flushed
    stdout.
12. `--mode json "<prompt>"` (one-shot) emits the native session header line
    first, then the full event sequence, then exits 0; the metadata-only
    `pipy.native_output` schema is **not** emitted.

`--json` prints a machine-readable pass/fail summary per check, mirroring the
existing `scripts/parity_checks/*.py` style (e.g. `branching_behavior.py`),
exits 0 when all checks pass and 1 otherwise, and makes no real network/AI
calls.

### Focused tests

- JSONL reader: multi-record buffering, partial trailing line, trailing `\r`,
  U+2028/U+2029 preserved inside strings, split only on `\n`.
- Single-writer backpressure: lines never interleave under a slow consumer.
- Event serialization: each `AgentSessionEvent`/`AgentEvent` type maps to the
  correct `type` discriminator and field set for the fake provider; deltas
  concatenate to the final text.
- Response correlation: every command echoes its `id`; commands without `id`
  still get a response; `success:false` carries an `error` string.
- Async `prompt`: success response is emitted only after preflight; events
  stream before the next command is processed; `steer`/`follow_up`/`abort` are
  accepted mid-turn and emit `queue_update`.
- Mode resolution: `--mode rpc`, `--mode json`, `--print`/`-p`, and non-TTY
  stdin default each select the correct mode.
- Session ops rebind: after `switch_session`/`fork`/`clone`/`new_session`,
  events come from the new active session; an extension cancel yields
  `{cancelled:true}` and no rebind.
- Extension-UI channel: `extension_ui_request` is emitted, a matching
  `extension_ui_response` resolves it, and a `timeout` falls back to the safe
  default.
- Secret hygiene: no provider keys/tokens reach any stdout object.
- Retirement: `--native-output json` is rejected or deprecated per the chosen
  slice; `--mode json` is the supported full-event path.

Suggested test files: `tests/test_native_automation_jsonl.py`,
`tests/test_native_automation_json_mode.py`,
`tests/test_native_automation_rpc_dispatch.py`,
`tests/test_native_automation_rpc_prompt.py`.

### Pi comparison harness

`scripts/parity_checks/automation_pi_comparison.py --json` is the deterministic
Pi-vs-pipy comparison. It runs the **same** headless workflow in the local Pi
reference and in pipy with deterministic offline providers on both sides, then
normalizes volatile fields (ids, timestamps, cwd temp paths, token counts) and
the streaming-delta granularity and asserts the two agree on:

- the normalized session-event order and type discriminators (role-tagged
  agent/turn/message lifecycle), treating the assistant streaming-delta run as
  one group — pipy emits the `text_delta` subset its provider produces while Pi
  also frames `text_start`/`text_end`, a documented, allowed divergence;
- the assistant's final text and the concatenation of its streamed deltas;
- `agent_end` semantics (`willRetry` and the run's message roles);
- durable session-tree reconstruction on the pipy side (the native session tree
  rebuilds the same user+assistant conversation the event stream describes).

The Pi side is driven through Pi's real `AgentSession` with the faux `streamFn`
via `scripts/parity_checks/pi_faux_event_driver.mts` (run with the local Pi
checkout's own `tsx`, so it is offline and deterministic); set `PI_MONO_DIR` to
the checkout (default `/Users/jochen/src/pi-mono`). When the Pi checkout/deps/
node are unavailable, the harness reports the Pi leg as **skipped with the
reason** rather than silently passing. Exact byte-for-byte JSON matching with Pi
is **not** the gate; structural/semantic equivalence is, and the deterministic
pipy conformance via `automation_rpc_conformance.py --json` is the hard gate.

Before treating the track as complete, run:

```sh
uv run python scripts/parity_checks/automation_rpc_conformance.py --json
PI_MONO_DIR=/Users/jochen/src/pi-mono \
  uv run python scripts/parity_checks/automation_pi_comparison.py --json
uv run pytest tests/test_native_automation_*.py
just check
just parity-score
```

Update `docs/harness-spec.md`, `docs/backlog.md`, `docs/parity-criterion.md`,
`docs/pi-parity.md`, `README.md`, and this spec to match shipped behavior, and
get an independent review pass for each transport/protocol slice.
