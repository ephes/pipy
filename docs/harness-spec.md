# Coding-Agent Harness Spec

Status: slice-42 native verification REPL review and smoke

<style>
.mermaid,
.mermaid svg {
  background: transparent !important;
  background-color: transparent !important;
}
</style>

## Goal

Build pipy's own coding-agent harness deliberately, starting with a small
local-first runner that can launch a coding agent, observe a conservative run
lifecycle, and write a durable pipy session record.

The first harness slice is not another session-capture feature by itself. It is
the foundation for pipy's own agent surface:

- a `pipy` CLI for running coding-agent tasks
- a harness core that owns run lifecycle and status
- adapter boundaries for current external agents
- a native pipy agent runtime behind the same interface
- integration with `pipy-session` for durable, privacy-conscious records

The existing `pipy-session` package remains the recorder and archive layer. The
harness should call it instead of creating a parallel transcript format.

## Non-Goals

- Do not build a broad transcript database.
- Do not import raw native transcripts by default.
- Do not store prompts, assistant messages, tool payloads, stdout, stderr,
  secrets, tokens, credentials, private keys, or sensitive personal data by
  default.
- Do not replace Codex, Claude Code, Pi, Aider, Goose, or Continue in the first
  slice.
- Do not implement a full native model/tool loop in the bootstrap slice.
- Do not add multi-agent orchestration, branching, compaction, repo maps,
  indexing, long-running daemons, or a web UI yet.
- Do not change the finalized session archive layout documented in
  `docs/session-storage.md`.
- Do not add a docs server unless the docs set grows enough to need navigation,
  search, preview, or publishing.

## Design Principles

- Local first. Runs execute from a local workspace and local state remains under
  user control.
- Small core, explicit adapters. The harness owns lifecycle; adapters own how a
  native agent is invoked and observed.
- Conservative capture. Store metadata and summaries that help future work, not
  raw conversations or command output.
- Native stores stay native. When an agent already keeps its own transcript,
  pipy may reference that native record without copying its contents.
- One lifecycle vocabulary. Codex, Claude, Pi, and future pipy-native runs should
  normalize into the same small event vocabulary where possible.
- Testable first slice. The initial implementation should be runnable with fake
  subprocess adapters and temporary session roots.
- Standard library first. New dependencies need a clear near-term payoff.

## Research Notes

### Pipy Foundation

The existing session recorder already establishes the storage invariants the
harness should reuse:

- active records live under `.in-progress/pipy/`
- finalized records move into `pipy/YYYY/MM/`
- JSONL is append-only while active
- finalized files are immutable
- Markdown summaries are intentional human-review artifacts
- catalog/search/inspect/verify avoid printing raw event bodies and payloads
- automatic capture is adapter-specific and partial by default

The harness should treat `pipy_session.recorder` as its durable event sink, and
`pipy_session.catalog` as the archive inspection surface.

### Flue Lessons

Local repo: `/Users/jochen/src/flue`

Useful ideas:

- Separate command modes. `flue run` is one-shot, `flue dev` is long-running,
  and `flue build` creates artifacts. Pipy should start with one-shot `pipy run`
  before designing watch mode, servers, or deployable artifacts.
- Keep a small event stream. Flue normalizes agent activity into events such as
  agent start, text deltas, tool start/end, turn end, command start/end,
  task start/end, compaction start/end, idle, and error. Pipy should define a
  smaller privacy-safe subset first.
- Keep pipeline behavior predictable. Flue prints the final result to stdout and
  sends progress, events, and logs to stderr. Pipy should follow the same CLI
  convention for harness output so `pipy run` composes in shell pipelines.
- Keep harness/session logic below UI and transport layers. Flue's CLI consumes
  events, but core session behavior is not embedded in the CLI.
- Discover runtime context from workspace files such as `AGENTS.md`, `CLAUDE.md`,
  skills, and directory listings, but avoid baking all context into build
  artifacts. Pipy can defer context discovery because external agents already do
  their own instruction loading.

Things not to borrow yet:

- Build and deploy targets.
- Server generation.
- Runtime tool APIs.
- Subagent execution.
- Compaction.

### Pi Lessons

Local repo: `/Users/jochen/src/pi-mono`

Useful ideas:

- Pi separates the small `Agent` event vocabulary from `AgentSession`, which
  composes lifecycle, persistence, settings, retry, compaction, and UI/RPC/print
  modes. This maps directly onto pipy's adapter/runner split: adapters observe
  and normalize events, while the runner owns lifecycle and persistence.
- `AgentSession` is the durable center. Interactive, print, RPC, and SDK modes
  sit on top of one lifecycle/session abstraction. Pipy should likewise keep
  `pipy run` thin and put lifecycle in a harness core.
- Pi supports in-memory sessions through `SessionManager.inMemory()` and
  `--no-session`. Pipy should use an in-memory recorder fake for unit tests and
  reserve real temporary session roots for integration tests.
- Pi uses JSONL session files with structured entries and a tree model. The tree
  model is powerful, but too broad for pipy's first harness slice.
- Pi separates a UUID session id from any human-facing display name. Pipy should
  likewise make `run_id` the stable identity and keep `slug` as a display and
  filename component.
- Pi's print mode and RPC mode show two useful future directions: a one-shot
  final-output path and a JSONL protocol for process integration.
- Pi distinguishes native session data, user-facing UI, extension state, and LLM
  context. Pipy should maintain a similar distinction between native agent
  transcript stores and pipy's conservative run records.
- Pi's session migrations are a warning for pipy because finalized pipy files
  are immutable. Pipy should version harness records from the first slice instead
  of planning in-place archive rewrites later.
- Pipy's lifecycle should complete recorder append/finalize work before the
  runner returns the native process exit code.

Things not to borrow yet:

- Session branching and `/tree`.
- In-place transcript migration.
- Extension UI and RPC protocol.
- Prompt template/skill expansion.
- Native model registry and OAuth handling.

### Web Research

Relevant external patterns:

- Codex CLI is a local coding agent that can read, edit, and run code in the
  selected directory. It has non-interactive `codex exec` and JSONL output where
  stdout becomes a stream of events such as `thread.started`, `turn.started`,
  `turn.completed`, `turn.failed`, `item.*`, and `error`.
  Sources: <https://developers.openai.com/codex/cli>,
  <https://developers.openai.com/codex/noninteractive>.
- Codex hooks receive JSON hook payloads with shared fields such as
  `session_id`, `transcript_path`, `cwd`, `hook_event_name`, and `model`.
  Source: <https://developers.openai.com/codex/hooks>.
- Claude Code hooks also provide lifecycle, prompt, tool, compaction, subagent,
  and session-end events with `transcript_path` and `session_id`. Hook stdout can
  affect context for some events, so pipy hook adapters must avoid accidental
  context injection unless explicitly designed.
  Source: <https://docs.claude.com/en/docs/claude-code/hooks>.
- OpenHands uses an append-only typed event log with message, action,
  observation, state update, error, and condensation events. Its
  action/observation split is useful prior art for later pipy event vocabulary,
  but slice 1 should stay at lifecycle metadata.
  Source: <https://docs.openhands.dev/sdk/arch/events>.
- Inspect AI writes structured eval logs with top-level metadata, samples,
  events, summaries, and APIs for reading headers or samples incrementally. Its
  log shape is useful prior art for keeping records stable enough for later UI,
  search, and analysis.
  Source: <https://inspect.aisi.org.uk/eval-logs.html>.
- OpenTelemetry GenAI semantic conventions define names such as
  `gen_ai.operation.name`, `gen_ai.agent.name`, and conversation identifiers.
  Pipy can keep its own event names while documenting a small mapping later.
  Source: <https://opentelemetry.io/docs/specs/semconv/gen-ai/>.
- Anthropic's Claude Agent SDK exposes a supported headless Python surface with
  `query()` as an async iterator and options such as `cwd`, `allowed_tools`, and
  `permission_mode`. A future Claude adapter should target a supported SDK shape
  before scraping hook output.
  Source: <https://github.com/anthropics/claude-agent-sdk-python>.
- pydantic-ai exposes `Agent.run`, `run_stream`, `iter`, and `RunContext`-based
  dependency injection. It is useful prior art for a future native pipy agent
  runtime behind the same harness port.
  Source: <https://ai.pydantic.dev/api/agent/>.
- Goose's `goose run` starts a session, executes provided instructions, exits,
  and supports `json` and `stream-json` output for automation. Goose also has an
  explicit `--no-session` mode and currently stores sessions in SQLite.
  Sources: <https://goose-docs.ai/docs/guides/running-tasks/>,
  <https://goose-docs.ai/docs/guides/sessions/session-management/>.
- Aider supports `--message` / `--message-file` for one-shot scripting and has a
  repository map concept that gives broad code context. The one-shot CLI shape is
  useful; repo maps should be deferred.
  Sources: <https://aider.chat/docs/scripting.html>,
  <https://aider.chat/docs/repomap.html>.
- Continue CLI headless mode uses `cn -p` for single tasks and requires explicit
  write/tool permissions in headless mode. Pipy should likewise make permission
  posture explicit instead of guessing.
  Source: <https://docs.continue.dev/cli/headless-mode>.
- LangGraph, smolagents, AutoGen, and CrewAI provide vocabulary for checkpoints,
  step callbacks, teams, and task processes, but they are too framework-heavy for
  pipy's first local harness slice.
  Sources: <https://docs.langchain.com/oss/python/langgraph/persistence>,
  <https://huggingface.co/docs/smolagents/reference/agents>,
  <https://microsoft.github.io/autogen/stable/reference/python/autogen_agentchat.teams.html>,
  <https://docs.crewai.com/en/concepts/crews>.
- Zensical can turn Markdown into a documentation site, serve previews, and use
  Mermaid through Markdown extensions, but this project does not need a docs
  server for one spec file.
  Sources: <https://zensical.org/docs/create-your-site/>,
  <https://zensical.org/docs/setup/extensions/>.

## Core Concepts

### Task

A user-requested unit of work. In the first slice, a task is just metadata plus
the external command being run. Later it may include structured prompts,
permissions, workspace policy, expected outputs, or evaluation criteria.

`slug` is a human-facing label and filename component. It is not the stable
identity of a run.

Suggested fields:

- `slug`
- `goal` or short description
- `workspace`
- `agent`
- `created_at`

### Agent

The logical agent selected for the task, such as `codex`, `claude`, `pi`, or
future `pipy-native`.

An agent is not the same as an adapter. The agent is the product-facing choice;
the adapter is the implementation that knows how to run it.

### Agent Port

The protocol the Runner calls to execute an agent. The port is stable harness
surface; adapters are concrete implementations behind it.

Suggested methods:

- `prepare(task, command, cwd) -> PreparedRun`
- `run(prepared, event_sink) -> AdapterResult`

### Adapter

The concrete implementation behind the harness agent port, such as a
subprocess-backed adapter, Codex adapter, Claude adapter, or future pipy-native
runtime adapter.

Responsibilities:

- validate that the native command can be run
- construct the process invocation or hook behavior
- normalize observable lifecycle events into harness events
- optionally report a native session reference
- return exit status and timing

Non-responsibilities:

- storing pipy records directly
- importing raw transcripts by default
- deciding long-term product policy

Adapters report events; the Runner records them. No adapter may mutate a pipy
session record directly.

### Run

One execution of a task through one adapter.

`run_id` is the stable identity of the execution and should be generated by the
Runner before recording starts. `slug` remains a display label and filename
component. Multiple runs may share the same slug.

Aggregate boundary: one Run equals one durable pipy record file and one recorder
unit of work. Only the owning Runner mutates that record.

Suggested fields:

- `run_id`
- `task`
- `agent`
- `adapter`
- `workspace`
- `status`
- `started_at`
- `ended_at`
- `exit_code`
- `session_record`

`session_record` should be a small structured reference to the pipy-side record,
not the raw record body. Suggested fields: active path while running, finalized
JSONL path after finalize, optional Markdown summary path, and capture marker.

### Run Event

A privacy-safe event emitted by the harness or adapter. Run events are not raw
native transcript events. They should be stable enough to support later UI,
search, and analysis.

Every harness event should carry:

- `event_id`: stable unique identifier within the run record
- `run_id`: stable run identity
- `sequence`: monotonically increasing integer assigned by the Runner
- `timestamp`: ISO-8601 timestamp

The first recorded event should also include `harness_protocol_version`. Start
versioning records in the first slice so finalized JSONL files do not need
in-place migration later.

Initial event vocabulary:

- `harness.run.started`
- `harness.run.completed`
- `harness.run.failed`
- `agent.process.started`
- `agent.process.exited`
- `agent.native_session.referenced`
- `workspace.files.changed`
- `verification.performed`
- `session.finalized`

`session.finalized` is a harness lifecycle event. It is appended by the Runner
just before the recorder unit of work is closed and moved into the finalized
archive.

Potential later events:

- `agent.turn.started`
- `agent.turn.completed`
- `agent.tool.observed`
- `agent.approval.requested`
- `agent.approval.resolved`
- `agent.idle`
- `artifact.created`

### Artifact

A durable output from the run. In the first slice this should mean only safe,
explicit artifacts such as a generated file path or final summary path. It
should not mean raw stdout/stderr, prompts, assistant messages, tool payloads,
or diffs.

### Native Session Reference

A metadata-only reference to an external agent's own session record.

Allowed by default:

- source filename
- source file size
- source mtime
- hash of resolved absolute path
- whether raw content was imported: always `false` by default

Disallowed by default:

- absolute source path
- raw native transcript body
- prompt text
- assistant text
- tool args/results

### Pipy Session Record

The durable pipy-side run record stored through `pipy-session`. It is a summary
and event trail for future review, not a complete transcript.

## Architecture

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#eef2ff', 'secondaryColor': '#ecfdf5', 'tertiaryColor': '#fff7ed', 'primaryTextColor': '#111111', 'secondaryTextColor': '#111111', 'tertiaryTextColor': '#111111', 'textColor': '#111111', 'lineColor': '#334155', 'primaryBorderColor': '#1d4ed8', 'edgeLabelBackground': '#ffffff'}, 'flowchart': {'htmlLabels': true}}}%%
flowchart LR
  User["<span style='color:#111111'>User</span>"] --> CLI["<span style='color:#111111'>pipy CLI</span>"]
  CLI --> Runner["<span style='color:#111111'>Harness Runner</span>"]
  Runner --> Adapter["<span style='color:#111111'>Adapter behind Agent<br/>Port</span>"]
  Adapter --> Native["<span style='color:#111111'>Native Agent CLI or<br/>Runtime</span>"]
  Adapter --> Ref["<span style='color:#111111'>Native Session<br/>Reference</span>"]
  Runner --> Recorder["<span style='color:#111111'>pipy-session Recorder</span>"]
  Recorder --> Archive["<span style='color:#111111'>JSONL + Markdown<br/>Archive</span>"]
  Runner --> Terminal["<span style='color:#111111'>Terminal stdout/stderr</span>"]
  Native --> Terminal

  classDef actor fill:#f8fafc,stroke:#334155,color:#111111,stroke-width:1.5px;
  classDef core fill:#eef2ff,stroke:#1d4ed8,color:#111111,stroke-width:1.5px;
  classDef store fill:#ecfdf5,stroke:#047857,color:#111111,stroke-width:1.5px;
  classDef terminal fill:#fff7ed,stroke:#c2410c,color:#111111,stroke-width:1.5px;
  class User,CLI,Native actor;
  class Runner,Adapter core;
  class Ref,Recorder,Archive store;
  class Terminal terminal;
```

Important boundaries:

- CLI parses user intent and passes it to the harness core.
- Harness core owns run lifecycle and recorder integration.
- Agent port defines the protocol the Runner calls.
- Concrete adapters own native agent invocation details.
- Recorder owns active/finalized session file lifecycle.
- Native transcript stores remain outside pipy's archive unless explicitly
  referenced.

Wiring should use explicit constructor injection:

```text
Runner(agent_port, recorder, capture_policy, clock=..., id_factory=...)
bootstrap(agent_name, root) -> Runner
```

No dependency injection framework or global adapter registry is needed in the
first slice. A small bootstrap selector is enough.

### Capture Policy

Capture policy should be a value object passed to the Runner rather than a set
of scattered flags.

Suggested fields:

- `record_argv`: default `false`
- `record_stdout`: default `false`
- `record_stderr`: default `false`
- `record_file_paths`: default `false`, set by `--record-files`
- `import_raw_transcript`: default `false`
- `workspace_path_mode`: `basename_and_hash` by default

### Recorder Unit of Work

Treat the recorder integration as a unit of work:

```text
with recorder.session(run) as session:
    session.append(...)
```

The concrete API may differ, but the semantics should be the same: initialize an
active record, append lifecycle events while the run is active, and finalize the
record exactly once on success, failure, abort, or adapter exception. Finalize
failure should be explicit rather than silently returning a successful run.

## Adapter Boundary

This section describes the concrete data exchanged through the `AgentPort`
methods defined above.

`PreparedRun` should include:

- display-safe command label
- executable name
- resolved working directory
- redacted or omitted argv metadata

`AdapterResult` should include:

- status
- exit code
- start/end timestamps or duration
- optional native session reference
- safe changed-file paths if collected

The event sink is Runner-owned. It may assign `event_id` and `sequence` before
appending to the recorder. A concrete adapter should not expose full command
output to the recorder. It may stream the native process to the user's terminal.

Concrete adapter examples:

- `SubprocessAdapter`
- `CodexAdapter`
- `ClaudeAdapter`
- `PipyNativeAdapter`

### Native Runtime Bootstrap

The native bootstrap slice adds `PipyNativeAdapter` behind the same
`AgentPort`. It does not shell out to Codex, Claude, Pi, or another coding-agent
CLI. The adapter prepares one native turn, constructs a `NativeAgentSession`,
calls a provider through a minimal `ProviderPort`, invokes deterministic no-op
or bounded read-only tools only through supported pipy-owned intent data, and
can run injected supervised patch apply plus allowlisted verification boundaries
only when explicit pipy-owned request and gate objects are supplied by control
flow.

The deterministic `fake` provider remains the default for tests and smoke runs.
It is not a production AI provider and it does not require credentials. A smoke
run is:

```sh
uv run pipy run --agent pipy-native --slug native-smoke --goal "Native bootstrap smoke"
```

The first real provider is the OpenAI Responses API provider. It is selected
explicitly, reads credentials from `OPENAI_API_KEY`, requires `--native-model`,
uses pipy's internally built system prompt as the Responses API `instructions`
field, uses the short native goal as `input`, and requests `store: false`:

```sh
uv run pipy run --agent pipy-native --native-provider openai --native-model <model> --slug openai-smoke --goal "Say hello briefly"
```

The OpenAI provider uses a small injectable standard-library HTTP boundary so
tests can provide fake responses without live credentials or network access. It
does not enable built-in tools, function calling, web search, file search, code
interpreter, computer use, conversation state, background mode, streaming,
retries, model fallback, OAuth, or a provider registry.

The second real provider is the OpenRouter Chat Completions provider. It is
selected explicitly, reads credentials from `OPENROUTER_API_KEY`, requires
`--native-model`, sends pipy's internally built system prompt and short native
goal as `system` and `user` chat messages, and makes one non-streaming request
to `https://openrouter.ai/api/v1/chat/completions`:

```sh
uv run pipy run --agent pipy-native --native-provider openrouter --native-model <provider/model> --slug openrouter-smoke --goal "Say hello briefly"
```

Official OpenRouter docs checked on 2026-05-07 document bearer API-key
authentication, the `/api/v1/chat/completions` endpoint, `model` identifiers
such as `openai/gpt-5.2` and `google/gemini-2.5-pro-preview`, request
`messages`, non-streaming `choices` responses with message content, and token
usage fields such as `prompt_tokens`, `completion_tokens`, and `total_tokens`.
The OpenRouter provider maps those usage counters to pipy's normalized
`input_tokens`, `output_tokens`, and `total_tokens` metadata and omits unknown,
unavailable, negative, non-finite, or provider-native usage fields. It does not
send OpenRouter app-attribution headers, debug options, provider routing
preferences, plugins, tools, function calling, streaming, retries, fallback
routing, OAuth, or provider-side tool settings. It does not store raw request
bodies, raw provider responses, provider response ids, prompts, model output,
auth material, or provider-native payloads in JSONL, Markdown, or
`--native-output json`.

The third real provider is the distinct OpenAI Codex subscription provider. It
is selected explicitly as `--native-provider openai-codex`, requires
`--native-model`, and uses pipy's own OAuth state under
`${PIPY_AUTH_DIR:-~/.local/state/pipy/auth}/openai-codex.json` rather than
Pi's `~/.pi/agent/auth.json` or any Codex/ChatGPT credential store:

```sh
uv run pipy auth openai-codex login
uv run pipy run --agent pipy-native --native-provider openai-codex --native-model <model> --slug codex-smoke --goal "Say hello briefly"
uv run pipy
```

The login boundary follows the local Pi reference shape: PKCE OAuth with client
id `app_EMoamEEZ73f0CkXaXp7hrann`, authorize/token URLs under
`https://auth.openai.com/oauth`, redirect
`http://localhost:1455/auth/callback`, scope
`openid profile email offline_access`, a local callback server, manual-paste
fallback, refresh-token support, and `chatgpt_account_id` extraction from the
access-token JWT. Provider calls use one SSE Responses request to
`https://chatgpt.com/backend-api/codex/responses` with `store: false`,
`stream: true`, Responses-style user input, `Authorization`,
`chatgpt-account-id`, `originator: pipy`,
`OpenAI-Beta: responses=experimental`, and `Accept: text/event-stream`. The
provider maps streamed final text and normalized usage into the existing
provider result shape, and archives only safe metadata such as provider, model,
status, duration, storage booleans, response status, and normalized counters.
Auth material,
authorization URLs, raw request bodies, raw provider responses, headers with
credentials, prompts, model output, provider-native payloads, stdout, stderr,
tool payloads, diffs, file contents, secrets, credentials, tokens, refresh
tokens, private keys, and sensitive personal data remain out of JSONL,
Markdown, catalog/search/inspect surfaces, and `--native-output json`.
The standalone `pipy auth openai-codex login` command remains supported, and
the native shell exposes the same auth boundary through `/login openai-codex`
plus `/logout openai-codex`. Shell login/logout diagnostics stay on stderr and
do not create provider turns or archive auth material.

### OpenAI Subscription-Backed Native Auth Decision

Decision: reopened and implemented as a distinct `openai-codex` provider.

Decision date: 2026-05-07.

Decision update: reopened for a distinct `openai-codex` provider on
2026-05-08 after inspecting local Pi prior art in `/Users/jochen/src/pi-mono`.
The original decision still applies to the existing `openai` provider: it must
remain the OpenAI Platform API-key provider and must not silently become
subscription auth. The reopened path is a separate Codex subscription provider
with explicit login, storage, refresh, provider, archive, and documentation
boundaries.

Official sources checked:

- [OpenAI API authentication](https://developers.openai.com/api/reference/overview):
  the REST API uses API keys provided as HTTP bearer credentials, and usage is
  attributed to an API organization or project.
- [ChatGPT Plus help](https://help.openai.com/en/articles/6950777-what-is-chatgpt-plus):
  ChatGPT Plus is a subscription for the ChatGPT web app, and API usage is
  separate and billed independently.
- [ChatGPT web and API billing help](https://help.openai.com/en/articles/9039756):
  ChatGPT and the API platform use separate billing systems.
- [Codex authentication](https://developers.openai.com/codex/auth): Codex
  supports ChatGPT subscription sign-in and API-key sign-in for OpenAI models,
  but the documented ChatGPT sign-in flow is for the Codex app, CLI, and IDE
  extension.
- [Codex pricing](https://developers.openai.com/codex/pricing): Codex
  subscription access is described for Codex product surfaces, while API-key
  usage pays standard API rates.
- [Codex SDK](https://developers.openai.com/codex/sdk): the SDK controls local
  Codex agents programmatically; it is a Codex product integration surface, not
  a generic OpenAI model provider auth API for third-party native runtimes.

What was checked: ChatGPT subscription versus OpenAI API billing, OpenAI API
authentication, Codex CLI authentication and device-code sign-in behavior,
Codex pricing, and the Codex SDK surface. The checked official docs currently
show a supported API-key path for direct API calls and a supported
subscription-backed sign-in path for Codex product clients. They do not document
an official, stable, locally usable OAuth or device-code provider auth flow that
lets a third-party native application call OpenAI models directly using a
ChatGPT or Codex subscription without running Codex itself.

Original implication, now superseded for the separate `openai-codex` path:
`pipy-native` must not turn the existing `--native-provider openai` Responses
API provider into subscription auth. The existing `openai` provider remains the
OpenAI Platform API-key baseline and continues to require `OPENAI_API_KEY` plus
`--native-model`. Pay-by-token API usage remains compatible but is not promoted
as the preferred self-bootstrap path.

Rejected approaches:

- scraping or reusing ChatGPT, browser, Codex CLI, or IDE extension credential
  stores
- copying access tokens, refresh tokens, cookies, authorization headers, or
  cached `auth.json` values into pipy
- reverse engineering private product endpoints or token refresh behavior
- wrapping Codex, ChatGPT, Claude Code, or another product UI/CLI as
  `pipy-native`'s provider implementation
- archiving any auth material, raw provider response, prompt, model output, or
  provider-native payload

Historical provider priority after the original blocked decision: OpenRouter
provider support with explicit model selection was implemented as the
provider-access path. Local model provider integrations remained deferred
pending benchmark work, and Anthropic subscription-backed native provider
support was not promoted to the near-term provider priority.

Provider priority update after Pi inspection: OpenRouter remains implemented
and usable for manual smoke tests, but it is no longer the preferred
near-term real-provider direction. The desired default real-provider direction
is a separate `openai-codex` provider modeled on Pi's ChatGPT Plus/Pro Codex
subscription flow. Relevant local references:

- `/Users/jochen/src/pi-mono/packages/ai/src/utils/oauth/openai-codex.ts`:
  PKCE OAuth, client id `app_EMoamEEZ73f0CkXaXp7hrann`, authorize/token URLs
  under `https://auth.openai.com/oauth`, redirect
  `http://localhost:1455/auth/callback`, scope
  `openid profile email offline_access`, local callback server, manual paste
  fallback, refresh token exchange, and `chatgpt_account_id` extraction from
  the access-token JWT.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/auth-storage.ts`:
  credential persistence, file locking, OAuth refresh, and credential
  resolution order. Pipy should create its own auth storage rather than read or
  copy Pi's `~/.pi/agent/auth.json`.
- `/Users/jochen/src/pi-mono/packages/ai/src/providers/openai-codex-responses.ts`:
  Codex subscription request boundary against
  `https://chatgpt.com/backend-api/codex/responses`, using
  `Authorization: Bearer <access-token>`, `chatgpt-account-id`, `originator`,
  `OpenAI-Beta`, SSE parsing, `store: false`, and metadata that must stay out
  of pipy archives unless explicitly allowlisted.

The implementation boundary for pipy is therefore `openai-codex`, not
`openai` OAuth. It must keep access tokens, refresh tokens, account ids,
authorization URLs, callback URLs, request bodies, raw provider responses,
provider-native payloads, prompts, model output, stdout, and stderr out of
JSONL, Markdown, catalog/search/inspect surfaces, and structured stdout. It
must also continue to reject credential-store scraping, copying cached tokens
from Pi or Codex, reverse-engineering by archive capture, and CLI/UI wrapping.
The provider is now implemented as a separate native path with focused tests
for OAuth shape, credential storage, refresh, request headers, response
parsing, conservative errors, and CLI selection. It may become the default real
provider only after manual smoke confirms that live login, refresh, provider
calls, and archive privacy all hold.

The native tool boundary defines explicit request/result/status value objects
plus approval and sandbox policy data. The native provider-to-tool bridge first
converts provider metadata into a sanitized internal `NativeToolIntent`; raw
provider tool-call objects are never archived. The only supported intent in the
current slice is `noop` / `internal_noop`. The only implemented tool is the
deterministic fake no-op tool. It does not read, write, edit, delete, diff,
inspect, or execute anything in the workspace. Its current role is to prove
event shape, lifecycle, dependency injection, and privacy-safe records before
real permission prompts or sandbox enforcement exist.

Native runs emit only privacy-safe lifecycle metadata:

- `native.session.started`
- `native.provider.started`
- `native.provider.completed`
- `native.provider.failed`
- `native.tool.intent.detected`
- `native.tool.started`
- `native.tool.completed`
- `native.tool.failed`
- `native.tool.skipped`
- `native.session.completed`

Payloads may include safe labels such as `provider`, `model_id`,
`system_prompt_id`, `system_prompt_version`, `status`, `exit_code`, duration,
normalized usage counters, provider response storage booleans, tool name/kind,
approval policy label, sandbox policy label, storage booleans, and
conservative sanitized error metadata. Normalized provider usage is limited to
finite non-negative `input_tokens`, `output_tokens`, `total_tokens`,
`cached_tokens`, and `reasoning_tokens`; unknown provider-native usage keys and
unavailable counters are omitted rather than guessed. Payloads must not include
the full system prompt, user prompt text beyond the existing short `--goal`
session metadata, model output, raw HTTP request or response bodies, raw
provider usage payloads, tool arguments, tool payloads, stdout, stderr, diffs,
file contents, secrets, tokens, credentials, private keys, or sensitive
personal data.

The native session owns system prompt construction internally. Archive records
store `system_prompt_id` and `system_prompt_version`, not the prompt text. The
provider's final text is not stored in JSONL or Markdown by default. If the
provider succeeds with no safe intent, the session completes without emitting
tool lifecycle events. If the provider fails, the no-op tool path is recorded
as `native.tool.skipped`; if a safe no-op intent is detected and the no-op tool
fails, the native run fails without printing provider final text.

The current native stdout decision is to preserve the human-readable default:
successful provider final text prints to stdout and nothing else in the native
success path is written there by the harness. Session finalization notices,
diagnostics, progress, provider errors, and other harness messages go to
stderr. Failed native runs do not print provider final text to stdout.
Structured machine-readable native stdout is available only through explicit
`--native-output json`; it is not part of the default
`pipy run --agent pipy-native` contract.

### Native Interactive REPL

The first interactive native shell is available as:

```sh
uv run pipy
uv run pipy repl --agent pipy-native --slug native-repl
```

It is intentionally a thin REPL over the same native provider/session/turn core
used by one-shot `pipy run --agent pipy-native`. Bare `pipy` defaults to the
native REPL in the current directory with slug `native-repl`. The REPL creates
a normal harness record and runs a bounded `NativeNoToolReplSession` with one
fresh pipy-owned `NativeConversationState`.

Each non-empty non-command input line becomes one provider turn. Provider
construction is late-bound: immediately before each provider-visible turn, the
REPL resolves the current provider/model selection to a concrete
`ProviderPort`, and the `NativeRunInput` metadata for that turn reflects that
selection. `/help` prints only static supported command shapes on stderr
without invoking the provider or tools. `/login [openai-codex]` reuses
`OpenAICodexAuthManager.login_interactive()` with REPL stdin and stderr,
`/logout [openai-codex]` removes pipy-owned OpenAI Codex credentials through
the same auth-manager boundary, and `/model [<provider>/<model>|<model>]`
prints or changes the current provider/model selection. These local
auth/model commands do not invoke providers, do not consume provider turns, do
not consume the one-read limit, and do not archive raw command text,
authorization URLs, prompts, provider responses, tokens, or auth material.
Successful `/model` selections are persisted as non-secret native defaults
under local pipy state with only provider and model identifiers.
`/exit` and `/quit` terminate the session. `/read
<workspace-relative-path>` remains the display-only workspace command.
`/ask-file <workspace-relative-path> -- <question>` is the first explicit
provider-visible context command: it uses a whitespace-delimited `--`
separator, the same bounded explicit file excerpt path as `/read`, then sends
exactly that in-memory excerpt plus the question to one provider turn. EOF
exits cleanly, interrupt exits with code `130`, and the fixed in-memory turn
bound stops provider turns before they can become unbounded. The first provider
request uses the conversation-state turn index
`0` and label `initial`; later no-tool REPL provider requests use subsequent
conversation-state turn indexes and the closed label `no_tool_repl`. The
`/ask-file` provider request uses the next conversation-state turn index and
the closed label `ask_file_repl`. `/propose-file
<workspace-relative-path> -- <change-request>` uses the same bounded
explicit-file-excerpt path as `/ask-file`, then sends one in-memory excerpt
plus one change request to one provider turn labeled `propose_file_repl`.
`/apply-proposal <workspace-relative-path>` performs no provider turn; it
consumes only one pending in-memory proposal draft from the same REPL session
and exact same normalized workspace-relative path. `/verify just-check`
performs no provider turn; it is accepted only after a successful same-session
`/apply-proposal` mutation and maps the safe command label to the internal
`just check` argv through the existing verification boundary.
These turn indexes and labels are pipy-owned; they are not copied from provider
metadata and are not derived from prompts, model output, filesystem paths,
stdout, stderr, secrets, or credentials.

The REPL stdout/stderr convention is conservative: provider final text from
successful ordinary, `/ask-file`, or `/propose-file` turns and successful
`/read` excerpt text print to stdout, while prompts, help, auth/model status,
malformed-command usage diagnostics, unsupported slash-command diagnostics,
finalization, errors, interrupt handling, apply status, verification status,
command-skip messages, and the turn-limit notice stay on stderr. `/model` with
no arguments prints the current selection and conservative configured-model
information to stderr only. `/ask-file` and `/propose-file` never print their
raw excerpts directly; `/apply-proposal` does not print raw replacement text or
diffs, and `/verify just-check` does not print command stdout or stderr. This
is separate from one-shot
`--native-output json`; the REPL does not add structured stdout, a transcript
stream, or conversation export.

The `/read`, `/ask-file`, and `/propose-file` commands share one per-session
read-only workspace request limit. Each accepted command builds one pipy-owned
`NativeReadOnlyToolRequest` with request kind `explicit-file-excerpt`, a
pipy-owned `NativeExplicitFileExcerptTarget`, and the existing read-only
workspace sandbox policy. Explicit user-entered REPL read/context commands use
`not-required` approval policy data and do not render a visible approval prompt
before invoking `NativeExplicitFileExcerptTool`. Malformed
`/read`, `/ask-file`, and `/propose-file` commands and unsupported slash
commands fail closed before any read, tool event, or provider visibility.
Unsupported, mismatched, unsafe-target, skipped, failed, and repeated
read-command cases fail closed before any read, before any second read request,
or before provider visibility. Static help and usage diagnostics are not
archived and do not consume the one-read limit.

Provider metadata is intentionally omitted from REPL provider lifecycle payloads
so provider-returned tool intent markers cannot become archive content. The
only provider-visible content added by ordinary REPL turns is the current input
line sent as the user prompt for that turn. Successful `/read` excerpts are
printed only to the interactive stdout stream; they are not provider-forwarded,
archived, included in Markdown, included in catalog/search surfaces, or
included in one-shot `--native-output json`. Successful `/ask-file` and
`/propose-file` excerpts are provider-forwarded only in memory to the single
corresponding provider turn and are not printed directly, archived, included in
Markdown, included in catalog/search surfaces, included in one-shot
`--native-output json`, persisted as provider context, or reused by later
turns.

REPL archives reuse existing safe lifecycle event names:

- `native.session.started`
- `native.provider.started`
- `native.provider.completed` or `native.provider.failed`
- `native.tool.started`
- `native.tool.completed`, `native.tool.skipped`, or `native.tool.failed`
- `native.tool.observation.recorded` for successful `/ask-file` and
  `/propose-file` provider visibility only
- `native.patch.proposal.recorded` for the proposal-only `/propose-file`
  boundary only
- `native.patch.apply.recorded` for successful, skipped, or failed
  `/apply-proposal` attempts that reach the patch-apply tool
- `native.verification.recorded` for successful, skipped, or failed
  `/verify just-check` attempts that reach the verification tool
- `native.session.completed`

No conversation or turn export event is emitted. REPL lifecycle payloads remain
metadata-only and may include safe labels and counters such as provider, model,
mode `repl`, `tools_enabled=true`, `read_only_commands_enabled=true`,
`provider_visible_context_enabled=true`, provider-visible-context-used state,
provider turn index/label, status, exit code, duration, normalized usage, turn
count, read-command-used state, ask-file-command-used state, and an exit reason
label. Tool lifecycle payloads remain metadata-only and may include safe
read-tool status, reason labels, approval/sandbox labels, capability booleans,
counts, source labels, path hashes, and storage booleans.
Patch-apply payloads remain the existing metadata-only apply shape with safe
status/reason labels, operation counts and labels, approval/sandbox labels,
capability booleans, workspace mutation state, optional safe scope labels, and
false storage booleans.
JSONL, Markdown, catalog/search/inspect surfaces, and one-shot
`--native-output json` must still omit raw prompts, model output, provider
responses, provider-native payloads, provider metadata, raw approval prompts,
raw tool arguments, raw tool results, stdout, stderr, diffs, patches, full file
contents, command output, auth material, secrets, credentials, tokens, private
keys, and sensitive personal data.

### Implemented REPL Boundary: Proposal-Only File Context

The native REPL now has a proposal-only file-context command,
`/propose-file <workspace-relative-path> -- <change-request>`:

```text
/propose-file <workspace-relative-path> -- <change-request>
```

Rationale: `/ask-file` already proved one bounded explicit-file-excerpt read,
one in-memory provider-visible context handoff, one provider turn label, and
metadata-only archive handling. The smallest useful next step is to let that
interactive path request structured proposal metadata without crossing into
workspace mutation, verification, shell execution, broad search, multiple file
context, provider-side tools, or a general model/tool loop.

The implementation reuses the existing read-only sandbox policy,
explicit-file-excerpt tool, workspace-relative target validation,
ignored/generated-file rejection, secret-looking content rejection, byte and
line limits, and the one-read per-session limit shared by `/read` and
`/ask-file`. After one successful bounded excerpt, it sends the excerpt and
change request only in memory to exactly one provider turn labeled
`propose_file_repl`. That label and its turn index are allocated by
`NativeConversationState`; they are not copied from provider metadata and are
not derived from prompts, model output, filesystem paths, stdout, stderr,
secrets, or credentials.

The successful provider-visible context handoff uses the same
metadata-only `native.tool.observation.recorded` lifecycle event as
`/ask-file`; the raw excerpt and change request remain in memory only. The
proposal parser inspects only one pipy-owned structured provider metadata
key already defined for patch proposals: `pipy_native_patch_proposal`.
Provider lifecycle payloads must continue to store empty provider metadata so
provider-returned tool markers and raw provider payloads do not become archive
content. If the proposal is supported, the REPL emits at most one
`native.patch.proposal.recorded` event using the existing metadata-only
proposal payload allowlist. If proposal data is missing, unsafe, or
unsupported, the safe outcome is no proposal event or one skipped proposal
event with safe reason labels and zero counts. The command hard-stops after the
provider result and proposal parse; it must not apply edits by itself, mutate
files, run verification, run shell commands, request network access, invoke
provider-side tools, create another provider turn, or persist provider-visible
context for later turns. When provider final text contains a strict visible
`pipy-apply-proposal-v1` block for one whole-file modify or delete of the
explicit file, the REPL may keep that draft in memory only for a later
same-session `/apply-proposal` command. The visible draft is the human review
surface; the archive event remains metadata-only and does not store the draft,
replacement text, diff, path, prompt, or provider output. A visible draft
without structured proposal metadata may create only the pending in-memory
apply draft; it must not synthesize `native.patch.proposal.recorded` or reuse
the `structured_proposal_accepted` reason label. The current visible draft
format is line-oriented and does not guarantee exact preservation of non-empty
files without a trailing newline.

The `/propose-file` command is a public REPL command, not a public automation
control and not a broad slash-command surface. Malformed `/propose-file`
syntax and unsupported slash commands must use static stderr diagnostics
without provider/tool execution, read-limit consumption, tool events, raw
command archiving, or provider visibility. Denied, unavailable, unsupported,
unsafe-target, skipped, failed, and repeated read-command cases must fail
closed before any read, before proposal parsing, and before provider
visibility. There is no approval prompt or denial branch on the normal product
REPL path.

Archives, Markdown summaries, catalog/search/inspect surfaces, and
`--native-output json` remain metadata-only. They may record only the existing
proposal fields: pipy-owned `tool_request_id`, `turn_index`, `status`,
`reason_label`, file and operation counts, closed operation labels, and false
storage booleans for patch text, diffs, file contents, prompts, model output,
provider responses, raw transcript import, and workspace mutation. They must
not store raw patch text, raw diffs, replacement file contents, model-selected
paths, raw provider proposal objects, raw provider metadata, raw prompts,
model output, provider responses, provider-native payloads, raw approval
prompts, raw tool arguments, raw tool results, stdout, stderr, command output,
auth material, secrets, credentials, API keys, tokens, private keys, or
sensitive personal data.

This implemented boundary does not change one-shot `pipy run --agent
pipy-native`, default REPL no-tool turns, `/read`, `/ask-file`, `/help`,
stdout/stderr handling, structured stdout, archive schema, or provider
routing.

Review and smoke status: focused review of the `/propose-file` boundary found
the implementation aligned with this contract. Focused CLI, native session, and
proposal value-object tests cover malformed input, unsafe
and repeated read-command paths, supported and skipped proposal metadata,
provider metadata suppression, and archive privacy assertions. A fake-provider
terminal smoke confirmed the no-approval prompt path, `propose_file_repl`
provider turn label, stdout/stderr split, finalized archive shape, and
`pipy-session verify` compatibility. No implementation hardening was required
in the review slice; OpenRouter smoke was skipped because `OPENROUTER_API_KEY`
was unavailable in the local environment.

### Proposal Trial Outcome And Write Boundary Direction

The human-applied native REPL proposal trial completed on 2026-05-09 using the
shell-first flow: `uv run pipy`, `/login openai-codex`,
`/model openai-codex/gpt-5.2`, and `/propose-file pyproject.toml --
<change-request>`. The public shell stayed proposal-only, and the useful
readability suggestion was applied manually outside the REPL as a small TOML
comment. The trial target stayed within the explicit-file-excerpt limits, and
archives remained metadata-only.

The trial also proved two safety/product details:

- a first target containing archive privacy sentinel strings failed closed with
  `secret_looking_content`, and the one-read session limit blocked a second
  read in that same REPL record
- this ChatGPT-backed Codex account accepted `gpt-5.2` and rejected
  `gpt-5.4`, `gpt-5.1-codex`, and `gpt-5.1-codex-mini`, so model availability
  and defaults need a separate policy slice rather than being inferred from
  names

The implemented first public write-capable REPL boundary is a constrained
same-session apply command:

```text
/apply-proposal <workspace-relative-path>
```

The command is deliberately smaller than a general `/apply` command. It is
accepted only after a successful same-session `/propose-file
<workspace-relative-path> -- <change-request>` for the exact same normalized
workspace-relative path. It consumes one pending in-memory proposal draft that
the user has reviewed in the terminal, normalizes that draft into a pipy-owned
`NativePatchApplyRequest`, invokes the existing `NativePatchApplyTool`
boundary, and then clears the pending proposal state. It must not look up raw
proposal text, patch text, diffs, provider output, file contents, or prompts
from JSONL, Markdown, catalog/search/inspect surfaces, structured stdout, or
any other archive surface.

The explicit slash command is the human review signal for the normal Pi-like
interactive shell posture. The first public write path does not add a visible
approval popup. Safety remains non-interactive and fail-closed:

- accept only one pending proposal for one file and one operation
- require the apply path to match the proposal path exactly after
  workspace-relative normalization
- require `request_source=pipy-owned-human-reviewed`
- use `mutating-workspace` sandbox policy with workspace read and filesystem
  mutation allowed and shell/network access forbidden
- validate workspace-relative targets, ignored/generated-file rejection, file
  type, UTF-8 replacement text bounds, and secret-looking replacement content
- require expected SHA-256 hashes for existing files before modify, delete, or
  rename operations, and distinguish missing, malformed, and mismatched hashes
- reject provider-selected paths, multi-file plans, multiple operations,
  shell-looking data, network access, provider-side tools, and another provider
  turn
- clear the pending proposal on any apply attempt, mismatch, unsupported draft,
  provider failure, local REPL command, unsupported slash command, or later
  provider-visible turn

The first public apply command uses the existing metadata-only
`native.patch.apply.recorded` archive event. JSONL, Markdown,
catalog/search/inspect surfaces, default stdout, and `--native-output json`
may record only the existing safe apply metadata: pipy-owned
`tool_request_id`, `turn_index`, status and reason labels, duration, file and
operation counts, closed operation labels, approval/sandbox labels and
booleans, `workspace_mutated`, optional safe scope labels, and false storage
booleans. They must not contain raw proposal text, raw patch text, raw diffs,
replacement file contents, target paths, raw prompts, model output, provider
responses, provider-native payloads, raw provider metadata, raw tool payloads,
stdout, stderr, command output, shell commands, auth material, secrets,
credentials, API keys, tokens, private keys, or sensitive personal data.

Verification is now exposed through the separately named `/verify just-check`
REPL command after a successful same-session `/apply-proposal` mutation.
`/apply-proposal` itself must not run `just check`, execute shell commands, or
emit `native.verification.recorded`.

The verification boundary keeps the apply path's metadata contracts:

- the read-only workspace verification sandbox policy with workspace read and
  allowlisted shell execution only
- the explicit safe command label `just-check`, internally mapped to `just
  check` without shell text or provider-selected command data
- one metadata-only `native.verification.recorded` event only after the command
  reaches the verification tool
- the default stdout/stderr split and metadata-only archive, Markdown, catalog,
  and structured-output contracts

The implemented verification command deliberately does not expose generic
`/verify`, arbitrary shell command text, provider-selected commands, provider
follow-up turns, provider-side tools, multiple file reads, multiple tool
requests, unbounded turns, persistent history, TUI/RPC behavior, retries,
fallback, provider routing, or OAuth changes.

Review and smoke status: focused review of `/verify just-check` found the
implementation aligned with this contract. Focused verification-tool, CLI,
native-session, value-object, and documentation policy tests cover the
post-apply-only gate, unsupported command labels, skipped and failed
verification behavior, metadata-only archive payloads, and the guarantee that
`/apply-proposal` does not run verification itself. Fake-provider terminal
smoke runs exercised propose/apply/verify success and a failing `just check`
path with real workspace mutation and real `NativeVerificationTool` execution.
The success smoke exited `0`; the failure smoke exited `1` after recording only
safe `command_failed` metadata. `pipy-session verify`, `list`, `search`, and
`inspect` remained compatible with both finalized REPL records. No
implementation hardening was required before the first real pipy-applied,
pipy-verified tiny change.

### Native Structured Stdout JSON Mode

Structured native stdout is an explicit opt-in contract. The default stays
unchanged: successful `pipy-native` runs print provider final text only, failed
native runs print no provider final text, and diagnostics, finalization
notices, provider errors, and harness errors remain on stderr.

The implemented flag shape is:

```sh
uv run pipy run --agent pipy-native --native-output json ...
```

Omitting the flag preserves the current human-readable stdout mode. The JSON
mode emits one final JSON object to stdout after the native run and recorder
finalization attempt complete. It is not a JSONL stream, does not interleave
progress events, and does not change the process exit-code contract.
`--native-output` is rejected for non-native agents before creating a session
record. If a later streaming protocol is needed, it should use a separate
explicit mode or flag with its own schema decision.

The JSON object is versioned and metadata-only. Current fields are limited to
summary-safe values:

- `schema`: `pipy.native_output`
- `schema_version`: `1`
- `run_id`
- `status` and `exit_code`
- `agent`, adapter, provider, and model labels
- `duration_seconds`
- normalized usage counters already allowed in archives, when available
- finalized JSONL and Markdown record path references
- storage booleans such as `prompt_stored=false`,
  `model_output_stored=false`, `raw_transcript_imported=false`, and
  `tool_payloads_stored=false`

Structured stdout must remain aligned with the archive privacy policy. It must
not emit raw system prompts, raw user prompts, model output, provider
responses, provider-native payloads, tool arguments, tool results, tool
payloads, stdout, stderr, diffs, patches, file contents, secrets, credentials,
tokens, private keys, or sensitive personal data by default. JSON mode is
therefore a status and metadata surface, not a replacement transcript or a way
to expose final model text.

### Native Fake Tool Intent

The native fake tool-intent slice implements the smallest provider-to-tool path
that proves contract and lifecycle behavior without adding real execution
powers. It remains bounded, deterministic, and metadata-only in the pipy
archive.

The smallest useful loop is:

1. Build the internal native system prompt and one user goal, as the current
   native session does.
2. Call the selected provider through `ProviderPort`.
3. Interpret the provider result as either final text or one sanitized internal
   tool-request intent.
4. If there is no tool intent, complete the native session and print provider
   final text only on success.
5. If there is one supported tool intent, invoke only the injected no-op
   `ToolPort` and record privacy-safe tool lifecycle metadata.
6. If the first provider result also carries an explicitly supported synthetic
   sanitized observation fixture and the no-op tool succeeds, emit one
   metadata-only `native.tool.observation.recorded` event and make exactly one
   follow-up provider call with generated observation metadata only.
7. Hard-stop after the follow-up provider turn. If provider, tool, or supported
   observation validation status is not successful, fail the native session and
   do not print provider final text.

This is intentionally not a general model/tool loop. The current implementation
allows at most one fake provider-emitted intent, one no-op tool invocation, and
one follow-up provider turn from a synthetic sanitized observation fixture. It
does not forward real read-tool output or model-generated tool observations.

Provider-owned raw response content remains provider-owned. Pipy may parse a
provider response inside the provider boundary, but the archive must store only
safe lifecycle fields. Raw provider response bodies, raw model messages,
provider-native tool-call payloads, function arguments, output text, and
provider-specific request ids that could reveal payload content must not be
written to JSONL or Markdown by default.

The internal tool-request intent is a sanitized value produced after provider
parsing, not the raw provider tool-call object. Allowed fields are limited to:

- `request_id`: a pipy-generated opaque id, deterministic in tests. The current
  `native-tool-0001` value is acceptable only while the no-op path has at most
  one invocation per native session; later loop work should generate a
  per-invocation id from pipy-owned turn/request position data.
- `tool_name`: an allowlisted safe label such as `noop`.
- `tool_kind`: an allowlisted safe category such as `internal_noop`.
- `turn_index`: a small integer assigned by pipy.
- `intent_source`: a safe label such as `fake_provider` or `provider_metadata`.
- `approval_policy`: the policy label already represented by
  `NativeToolApprovalPolicy`.
- `approval_required`: a boolean derived from approval policy data.
- `sandbox_policy`: the policy label already represented by
  `NativeToolSandboxPolicy`.
- `filesystem_mutation_allowed`, `shell_execution_allowed`, and
  `network_access_allowed`: booleans derived from sandbox policy data.
- `tool_payloads_stored`, `stdout_stored`, `stderr_stored`, `diffs_stored`, and
  `file_contents_stored`: booleans that remain `false` for the no-op slice.
- optional sanitized metadata containing only counters, booleans, enum labels,
  and short non-secret identifiers.

The internal tool-request intent must not contain or persist raw prompts, model
output, raw provider responses, provider-native tool-call objects, tool
arguments, shell commands, filesystem paths selected by the model, file
contents, diffs, patches, stdout, stderr, credentials, tokens, private keys,
API keys, or sensitive personal data. If a provider can only express a tool
request through raw arguments, the provider adapter must convert that into an
allowlisted intent in memory and drop the raw payload before emitting events.
Unsupported or unsafe provider requests should become sanitized failures or
safe skipped-tool records, not archived payloads.

Deterministic fake behavior remains the first implementation target. The fake
provider has explicit fixture fields that write allowlisted
`ProviderResult.metadata` keys for a no-op intent and, when requested by tests,
a synthetic sanitized observation fixture. Those fixture paths do not inspect
the prompt, echo the prompt, or derive archived content from prompt text. The
fake no-op tool continues to return deterministic safe metadata showing that the
workspace was not inspected or mutated and that no stdout, stderr, or tool
payloads were stored.

The no-op tool is now an injected smoke-test and unit-test tool rather than a
mandatory part of every successful native provider run. Production native runs
do not execute an implicit tool when a provider returns only final text; tool
invocation is driven by an explicit sanitized intent.

The expected lifecycle for a safe fake intent is:

```text
native.session.started
native.provider.started
native.provider.completed
native.tool.intent.detected        # metadata-only, emitted only for a safe intent
native.tool.started
native.tool.completed | native.tool.failed | native.tool.skipped
native.tool.observation.recorded   # metadata-only, fixture-gated
native.provider.started            # optional bounded follow-up turn
native.provider.completed | native.provider.failed
native.patch.proposal.recorded     # metadata-only, read-only follow-up only
native.session.completed
```

Provider failure should still record `native.provider.failed` followed by
`native.tool.skipped` with a safe reason. A provider success with no intent
should not emit `native.tool.started`. A provider success with an unsupported
or unsafe intent should not emit `native.tool.intent.detected` or
`native.tool.started`; it should emit only a metadata-only skipped or failed
lifecycle event with safe labels and no raw payload.

### Native Post-Tool Provider Turn Decision

The current native runtime remains bounded to one initial provider turn plus,
only when the provider result exposes one safe supported intent, one injected
fake no-op tool invocation. It can then make exactly one follow-up provider call
only when the same provider result includes an explicitly supported synthetic
sanitized observation fixture anchored to pipy's `tool_request_id` and
`turn_index`.

The follow-up provider request is generated by pipy from metadata-only
observation labels. It does not include the original user prompt, raw model
output, raw provider response, provider-native tool result, raw tool payload,
stdout, stderr, diffs, patches, file contents, shell commands, filesystem paths,
secrets, credentials, tokens, private keys, or sensitive personal data. Unsafe
or unsupported observation fixture data emits a sanitized skipped observation
record and fails closed before provider visibility.

The archive records only summary-safe metadata such as provider turn index and
label, provider and model labels, status, duration, normalized usage counters,
provider response storage booleans, prompt and model-output storage booleans,
safe tool-observation labels, and storage booleans for tool payloads, stdout,
stderr, diffs, and file contents. Real read-tool output, live repo context,
general model/tool loops, provider-side tools, approval prompts, sandbox
enforcement, retries, streaming, fallback, provider registry, OAuth, and raw
transcript import remain deferred.

### Native Approval And Sandbox Enforcement Baseline

Direction update, 2026-05-08: the visible approval prompt model is no longer
the desired product posture for `pipy-native`. Pipy now follows a Pi-like
native shell posture for explicit user-entered REPL read/context commands:
no permission popups for normal interactive use. Those commands keep internal
workspace-relative path validation, sandbox/capability labels, ignored and
generated-file rejection, size limits, encoding checks, secret-looking content
rejection, metadata-only archive handling, and stdout/stderr separation.

Approval and sandbox enforcement are native gates for tool-capable behavior.
This baseline defines the contract before broad interactive read tools, write
tools, shell execution, network access, verification commands, provider-side
tools, or runtime sandbox enforcement exist. The current `pipy-native` runtime
remains bounded to one initial provider turn plus optional one no-op or
read-only explicit-file-excerpt tool invocation and, for explicitly supported
sanitized fixtures only, one follow-up provider turn. The visible approval
prompt foundation described below is historical helper code and is no longer
wired into `/read`, `/ask-file`, or `/propose-file`; this baseline still does
not add broad interactive tools, network access, provider tool use, live shell
execution, archive writes for live context beyond existing metadata-only
events, or a general model/tool loop.

Approval decision labels are `pending`, `allowed`, `denied`, `skipped`, and
`failed`.

- `pending`: approval is required but has not been resolved yet.
- `allowed`: approval was not required or was granted by a pipy-owned approval
  authority.
- `denied`: approval was requested and refused.
- `skipped`: the request did not reach approval or execution because an earlier
  policy, capability, sandbox, path, context, or safety gate skipped it.
- `failed`: approval resolution or gate processing failed without a safe
  denial or skip decision.

Approval is not delegated to provider output. A future provider may request an
operation only through a sanitized internal pipy request; pipy decides whether
approval is required and records only safe decision metadata. The minimum
future approval posture is:

- no approval required for internal no-op tools with `no-workspace-access`, no
  shell execution, no network access, no repo context production, no stdout or
  stderr storage, and no workspace inspection or mutation
- no approval popup required for explicit user-entered REPL read/context
  commands, which remain bounded by command syntax and internal safety checks
- approval required for provider-selected or automated read-only workspace
  tools before any file read, search, directory inspection, or provider-visible
  repo context production
- approval required for write tools, patch proposal apply, file creation,
  deletion, rename, edit, diff application, or any mutating workspace action
- approval required for shell execution, even when intended to be read-only
- approval required for network access, even when no workspace access is
  requested
- approval required for verification commands such as a future allowlisted
  `just check`, because they may execute code, inspect the workspace, and emit
  stdout or stderr even when archives omit that output

Sandbox modes remain the three labels already represented in native value
objects:

- `no-workspace-access`: the request may not inspect, read, write, diff, patch,
  list, search, or otherwise resolve workspace paths.
- `read-only-workspace`: the request may inspect only approved, validated,
  bounded workspace sources and may not mutate files or execute shell commands
  unless separate gates explicitly allow those capabilities.
- `mutating-workspace`: the request may mutate only approved, validated
  workspace paths and only through the specific future tool contract that
  requested the capability.

Shell execution and network access are independent capability booleans, not
implicit consequences of a workspace sandbox mode. The baseline capability
booleans are `workspace_read_allowed`, `filesystem_mutation_allowed`,
`shell_execution_allowed`, and `network_access_allowed`. A read-only workspace
sandbox does not imply shell execution. A mutating workspace sandbox does not
imply network access. Shell execution does not by itself permit filesystem
mutation; mutating workspace files through shell execution requires both
`shell_execution_allowed` and `filesystem_mutation_allowed`. Network access
does not imply workspace access.

Future tool execution must use this fixed gate order before any real effect:

1. Policy validation: the request must name a supported tool kind, approval
   policy, sandbox mode, and capability booleans.
2. Request normalization and identity: raw provider payloads are converted to a
   sanitized pipy-owned request with pipy's `tool_request_id` and `turn_index`;
   provider-owned ids, raw args, and provider-native payloads are dropped.
3. Approval gate: if approval is required, resolve it through a pipy-owned
   approval path before sandbox or path work proceeds.
4. Sandbox capability gate: compare the approved request with the selected
   sandbox mode and independent capability booleans.
5. Path and context validation: validate pipy-owned scope, workspace-relative
   path labels, ignore/generated-file boundaries, size limits, encodings, and
   redaction rules before any read, write, shell, network, verification, or
   provider-visible context production.
6. Execution gate: execute only if all earlier gates succeeded; otherwise emit
   metadata-only skipped or failed lifecycle metadata.
7. Observation and provider-context gate: derive only sanitized in-memory
   observations or provider-visible context after execution, and archive only
   metadata allowed by this spec.

All future gates fail closed. Missing policy, unsupported approval mode,
unsupported sandbox mode, denied approval, unavailable approval UI, sandbox
mismatch, unsafe request data, model-selected paths, provider-supplied paths,
raw args, unsupported encodings, ignored or generated files, oversized sources,
secret-looking data, and attempted capability escalation must not execute. The
safe outcome is a metadata-only `skipped`, `denied`, or `failed` decision with
safe reason labels. Capability escalation includes any request that asks for
more than its approved policy permits, such as a read tool attempting mutation,
a verification command attempting network access without the network gate, or a
shell request attempting workspace mutation without both shell and mutation
gates.

Future archives, Markdown summaries, and `--native-output json` may record only
metadata-only approval and sandbox fields:

- policy labels, sandbox mode labels, approval required/resolved booleans, and
  decision labels
- safe reason labels and supported capability booleans, including
  `workspace_read_allowed`, `filesystem_mutation_allowed`,
  `shell_execution_allowed`, and `network_access_allowed`
- `tool_request_id`, `turn_index`, safe tool name/kind labels, status,
  `duration_seconds`, counts, byte and line counts, redaction/skipped booleans,
  storage booleans, and optional finalized-record references

Archives, Markdown summaries, and default structured stdout must not store raw
prompts, model output, provider responses, provider-native payloads, raw tool
payloads, stdout, stderr, diffs, patches, full file contents, shell commands,
raw args, model-selected paths, provider-selected paths as authority, secrets,
credentials, API keys, tokens, private keys, or sensitive personal data.
Storage booleans must remain explicit, and metadata-only archives must not
become raw execution, transcript, repo-context, stdout, stderr, diff, patch, or
file-content stores.

This baseline is a prerequisite for bounded read-only tools, provider-visible
repo context production, write tools, patch application, shell or network
access, and verification commands. The first implemented prompt helper remains
historical/test-covered helper code, but it is not on the normal product REPL
path and is not a broad runtime sandbox. Future implementation slices must wire
these gates explicitly and keep the existing pipy-owned `tool_request_id`, `turn_index`,
`native.tool.observation.recorded`, `duration_seconds`, storage booleans,
provider-visible context, and metadata-only archive contracts in sync.

### Native Visible Approval And Sandbox Prompt Path

Status: historical foundation, removed from the product REPL path. The helper
remains as test-covered historical code only; normal `/read`, `/ask-file`, and
`/propose-file` interactive commands do not call it or display its prompt.

The first visible approval/sandbox prompt foundation is implemented in
`pipy_harness.native.approval_prompt`. It is deliberately narrower than a
tool-capable shell. The helper supports only the existing read-only workspace
inspection request class, and only the `explicit-file-excerpt` request kind is
accepted for this first prompt path.

The prompt data model is `NativeApprovalSandboxPrompt`. It stores only safe
labels and booleans:

- operation, tool name, and tool kind labels
- approval policy label and `approval_required`
- sandbox policy label
- `workspace_read_allowed`, `filesystem_mutation_allowed`,
  `shell_execution_allowed`, and `network_access_allowed`
- optional safe scope and reason labels

It does not store or display raw prompts, provider output, raw tool arguments,
workspace paths, command text, stdout, stderr, diffs, patches, file contents,
or excerpt text. `NativeInteractiveApprovalPromptResolver` writes the visible
prompt to an injected output stream and reads an approval answer from an
injected input stream, which keeps the path testable without terminal input and
lets future CLI wiring choose stderr for prompts. A missing resolver, missing
stream, EOF, denied answer, resolver error, unsupported request kind,
unsupported approval policy, unsupported sandbox mode, sandbox mismatch, unsafe
request data, or attempted capability escalation produces a fail-closed
decision before execution.

The public helper `resolve_read_only_workspace_approval(request, resolver)`
returns `NativeReadOnlyApprovalResolution`, including a
`NativeApprovalSandboxDecision` and the existing `NativeReadOnlyGateDecision`
consumed by `NativeExplicitFileExcerptTool`. An approved prompt maps to
`NativeReadOnlyApprovalDecision.ALLOWED`; denial maps to `DENIED`; unavailable
UI and unsupported request kinds map to `SKIPPED`; unsupported policies,
sandbox mismatches, unsafe data, capability escalation, and resolver failures
map to `FAILED`.

This foundation slice added no new archive event type and no new
`--native-output json` fields. The helper exposes `safe_metadata()` methods for
future archive wiring, but it is not wired into the current interactive shell.

### Native Read-Only Tool Request Value Objects

The first bounded read-only implementation needed stable native data contracts
before it could read files or run searches. The implemented native model
surface includes read-only workspace inspection value objects. These value
objects are now consumed by the direct explicit file excerpt tool described
below. `NativeAgentSession` creates and executes one of these requests only
through the supported fixture-gated explicit-file-excerpt path; no-fixture runs
still do not create, archive, execute, or provider-forward read-only requests.

Read-only request kind labels are limited to:

- `explicit-file-excerpt`: a future approved, bounded explicit file excerpt
  request
- `search-excerpt`: a future approved, bounded search-result excerpt request

`NativeReadOnlyToolLimits` represents the same upper bounds as the
provider-visible repo context policy:

- per excerpt: 4 KiB and 80 lines
- per source file per provider turn: 8 KiB and 160 lines
- total provider-visible repo context per provider turn: 24 KiB and 480 lines
- maximum excerpts per provider turn: 12
- maximum distinct source files per provider turn: 6

The value object validates that the represented limits do not exceed those
policy caps. These limits remain metadata and do not authorize execution by
themselves.

`NativeReadOnlyToolRequest` carries only metadata-only contract fields:

- pipy-owned `tool_request_id` and `turn_index`
- safe request kind, tool name, and tool kind labels
- `NativeToolApprovalPolicy` with `required` as the read-only default
- `NativeToolSandboxPolicy` with `read-only-workspace`,
  `workspace_read_allowed=true`, and `filesystem_mutation_allowed=false`,
  `shell_execution_allowed=false`, and `network_access_allowed=false`
- bounded limit metadata
- optional `scope_label` placeholders that are labels, not path authority
- storage booleans that remain false for tool payloads, stdout, stderr, diffs,
  file contents, prompts, model output, provider responses, and raw transcript
  import

The inert read-only request shape must not include raw prompts, model output,
provider responses, provider-native payloads, raw tool payloads, stdout,
stderr, diffs, patches, full file contents, excerpt text, search result text,
shell commands, raw args, model-selected paths, provider-selected paths as
authority, secrets, credentials, API keys, tokens, private keys, or sensitive
personal data. `scope_label` is intentionally not a resolved filesystem path,
not a provider/model-selected path, and not authority to read anything.

Adding these value objects did not add live approval prompts, default-session
sandbox enforcement, search execution, provider-visible repo context
forwarding, live observation emission, archive writes for live context or real
execution, a post-tool provider call, or a general model/tool loop.

### Native Explicit File Excerpt Tool

The first real bounded native read-only workspace tool is the direct
`NativeExplicitFileExcerptTool`. It can be exercised directly in tests and is
also wired into `NativeAgentSession` only through the bounded fixture-gated
read-only provider-context path. Both entry points require explicit pipy-owned
data:

- `NativeReadOnlyToolRequest` with request kind `explicit-file-excerpt`,
  pipy-owned `tool_request_id="native-tool-0001"`, `turn_index=0`, required
  approval policy, read-only sandbox policy, `workspace_read_allowed=true`, and
  mutation, shell, and network booleans all false
- `NativeReadOnlyGateDecision` with pipy-owned decision authority and one of
  the safe labels `allowed`, `denied`, `skipped`, or `failed`
- `NativeExplicitFileExcerptTarget` with a pipy-owned normalized
  workspace-relative target; provider/model authority is rejected

The tool enforces this fixed order before reading:

1. request kind, identity, approval policy, sandbox mode, and capability
   posture are checked
2. approval gate data must be present and `allowed`
3. the target must be pipy-owned, normalized, relative to the workspace, not
   absolute, not `..` escaping, not shell-expanded, not Windows-drive based,
   not sensitive-looking, and still inside the workspace after symlink
   resolution
4. conservative ignored/generated-file checks run before reading, including
   obvious generated directories and suffixes plus simple root `.gitignore`
   patterns; fuller ignore semantics remain deferred
5. the source must exist, be regular, readable by mode bits, UTF-8 text, not
   binary/control-content, not secret-looking, and within the configured byte
   and line limits

Limits are enforced before an excerpt can become provider-visible in memory.
The direct implementation never raises the documented caps from
`NativeReadOnlyToolLimits`; if a configured limit is zero or the file exceeds
the effective per-excerpt, per-source-file, or total-context byte or line
limit, the result is skipped with a safe reason label. Oversized files fail
closed rather than being partially streamed in this first slice.

Successful reads return `NativeExplicitFileExcerptResult` with a
`NativeInMemoryFileExcerpt` containing the sanitized excerpt text. That text may
be sent only as in-memory provider-visible context during the one bounded
read-only follow-up provider turn. It is not archived, not emitted as a native
lifecycle event, not printed to stdout, and not included in
`--native-output json`.

Archive/event-facing metadata for this direct tool is available only through
the result's metadata helper. That helper includes safe labels, status, reason,
`duration_seconds`, approval decision metadata, sandbox labels and capability
booleans including `workspace_read_allowed`, byte and line counts, excerpt and
distinct-source counts, a short source label, a path hash, `tool_request_id`,
`turn_index`, and false storage booleans. It excludes raw excerpt text, file
contents, search result text, stdout, stderr, diffs, patches, shell commands,
raw args, prompts, model output, provider responses, provider-native payloads,
secrets, credentials, tokens, private keys, and sensitive personal data.

The default no-fixture native runtime remains unchanged by this direct tool
boundary: no live approval prompts, no default repo reads, no default search
execution, no archive writes for live context, and no general model/tool loop.
The read-only runtime path is available only when the first provider result
carries a supported safe read-only intent and the supported pipy-owned explicit
file excerpt fixture.

### Native Provider-Visible Repo Context Policy

Provider-visible repo context is provider input, not archive content. The first
implemented boundary is narrow: after one safe supported read-only intent and
one supported pipy-owned explicit-file-excerpt fixture, `NativeAgentSession`
may read one bounded UTF-8 text file, emit metadata-only tool and observation
events, forward the successful excerpt only in memory to exactly one follow-up
provider turn, and hard-stop. No-fixture fake/OpenAI/OpenRouter runs still
perform no repo reads and complete without tool events.

Allowed source types are limited to explicitly bounded, sanitized context
produced after approval and sandbox checks exist:

- bounded explicit file excerpts from approved read-only file requests
- bounded search-result excerpts from approved read-only search requests
- explicit per-turn workspace summaries authored by pipy or the user, not broad
  repo maps or persistent workspace summaries
- short user-provided goal metadata that is already safe enough to send to the
  provider for the current run
- sanitized tool-observation summaries derived from
  `native.tool.observation.recorded` metadata and safe in-memory observation
  labels

Forbidden source types and content must never become provider-visible repo
context through this policy:

- broad repo maps, unbounded file contents, persistent workspace summaries, or
  arbitrary directory listings
- raw diffs, patches, stdout, stderr, shell command output, raw tool payloads,
  raw tool arguments, provider-native tool-call or tool-result payloads, raw
  provider responses, model output, or prompt fragments
- model-selected paths or provider-supplied paths as trusted read targets
- generated files, ignored files, binary or unreadable files, unsupported
  encodings, and large files unless a later bounded-read shape explicitly
  permits a sanitized summary
- secrets, credentials, API keys, tokens, private keys, and sensitive personal
  data

The first live implementation must encode limits no larger than these values
before any real read occurs or any context is sent to a provider:

- per excerpt: 4 KiB and 80 lines
- per source file per provider turn: 8 KiB and 160 lines
- total provider-visible repo context per provider turn: 24 KiB and 480 lines
- maximum excerpts per provider turn: 12
- maximum distinct source files per provider turn: 6

These are upper bounds, not targets. A later slice may choose smaller values for
the first read tool. Raising any limit requires an explicit docs and test update
before runtime wiring changes.

Provider-visible paths are labels, not authority. A future read request must be
authorized from pipy-owned scope and approval data before any path is resolved.
When a path is included for provider context, prefer a normalized relative
workspace path only after it has been validated to stay inside the workspace and
outside ignored or generated areas. If a relative path is sensitive or unsafe to
show, use a source label plus a stable path hash or omit the path. Raw
model-selected paths, provider-supplied paths, absolute paths, shell-expanded
paths, and paths derived from raw tool arguments must not be trusted or
archived as context identity.

Redaction happens before provider visibility. Unsafe data must be dropped or
skipped in memory before provider-visible context is produced; it must not be
archived first and redacted later. Secret-looking keys or values, credentials,
tokens, private keys, sensitive personal data, unsupported encodings, binary
content, unreadable content, generated files, ignored files, oversized files,
and excerpts that cannot be proven within limit must fail closed. The safe
outcome is to skip the source, record summary-safe skip metadata, and continue
only if the remaining context is still useful and policy-compliant. Otherwise
the tool observation or post-tool turn should be skipped or failed with safe
reason labels.

Archives and structured stdout remain metadata-only when repo context is
produced later. JSONL, Markdown, and `--native-output json` may record only
safe metadata such as source labels, counts, byte and line counts, excerpt
counts, distinct file counts, redaction and skipped booleans, safe skip reason
labels, `duration_seconds`, storage booleans, `tool_request_id`, `turn_index`,
and optional finalized-record references. They must not store raw excerpt text,
file contents, search result text, raw prompts, model output, provider
responses, raw tool payloads, stdout, stderr, diffs, patches, shell commands,
raw args, model-selected paths, secrets, credentials, tokens, private keys, or
sensitive personal data. Storage booleans must make the boundary explicit; raw
repo context storage remains false by default.

The implemented explicit-file-excerpt path produces sanitized in-memory context
under these limits, and the post-tool provider turn may receive only that
bounded context plus the metadata-only observation shape anchored to pipy's
`tool_request_id` and `turn_index`. Search-result excerpts, broad repo maps,
multiple sources, and persistent workspace summaries remain deferred.

### Native Patch Proposal Boundary

The native patch proposal boundary is a metadata-only step before supervised
write capability. After one successful bounded read-only tool observation and
one successful follow-up provider turn, `NativeAgentSession` may parse a single
pipy-owned structured proposal from provider result metadata and emit
`native.patch.proposal.recorded`. Without an injected human-reviewed patch apply
request, the runtime hard-stops after this event. It does not apply edits, run
shell commands, run verification commands, request network access, or create
another provider turn.

The accepted provider metadata key is pipy-owned and bounded:
`pipy_native_patch_proposal`. Its value must be a mapping with only these
fields:

- `proposal_source`: `pipy_owned_patch_proposal`
- `tool_request_id`: the current pipy-owned `native-tool-0001`
- `turn_index`: `0`
- `status`: `proposed`
- `reason_label`: `structured_proposal_accepted`
- `file_count`: finite integer metadata, currently at most 50
- `operation_count`: finite integer metadata, currently at most 200
- `operation_labels`: at most 8 closed labels from `create`, `modify`,
  `delete`, and `rename`
- storage booleans that must remain false: `patch_text_stored`,
  `diffs_stored`, `file_contents_stored`, `prompt_stored`,
  `model_output_stored`, `provider_responses_stored`,
  `raw_transcript_imported`, and `workspace_mutated`

The proposal parser does not accept provider-native tool calls, raw model text,
raw diffs, patch text, file contents, filesystem paths, shell commands,
provider response ids, function arguments, or arbitrary provider payloads as
archiveable proposal content. Unknown fields, provider-owned ids, bad counts,
storage booleans set to true, unsupported source/status/reason labels, or
unsupported operation labels fail closed. When a proposal key is present but
unsafe or unsupported, the archive may record a skipped proposal event with
`reason_label` set to `unsafe_proposal` or `unsupported_proposal`; counts are
zero, operation labels are empty, and all storage booleans remain false.

The `native.patch.proposal.recorded` payload allowlist is exactly:

- `tool_request_id`
- `turn_index`
- `status`
- `reason_label`
- `file_count`
- `operation_count`
- `operation_labels`
- `patch_text_stored`
- `diffs_stored`
- `file_contents_stored`
- `prompt_stored`
- `model_output_stored`
- `provider_responses_stored`
- `raw_transcript_imported`
- `workspace_mutated`

Proposal archive and structured stdout boundaries stay metadata-only. JSONL,
Markdown, and `--native-output json` must not include raw patch text, raw
diffs, file contents, file paths proposed by the model, raw prompts, model
output, provider responses, provider-native payloads, tool payloads, stdout,
stderr, shell commands, auth material, secrets, credentials, tokens, private
keys, or sensitive personal data. The current JSON stdout schema does not
include proposal detail; proposal metadata is represented only by finalized
archive events.

### Native Patch Apply Boundary

The native patch apply boundary is the first supervised workspace mutation path.
It is not provider tool calling. Non-interactive `NativeAgentSession` consumes
only an injected in-memory `NativePatchApplyRequest` supplied by pipy-owned
control flow after human review, and only after a supported
`native.patch.proposal.recorded` event with `status=proposed`. The interactive
native REPL now exposes the same boundary through `/apply-proposal
<workspace-relative-path>` after a successful same-session `/propose-file` for
the exact same normalized path. Ordinary provider turns, OpenAI, OpenRouter,
and fake provider runs without an explicit apply command remain proposal-only.

The request requires:

- the current pipy-owned `tool_request_id` and `turn_index`
- `request_source`: `pipy-owned-human-reviewed`
- explicit required approval and a human-reviewed
  `NativePatchApplyGateDecision`
- `mutating-workspace` sandbox policy with workspace read and filesystem
  mutation allowed, and shell/network access forbidden
- at least one and at most 10 operations across at most 5 distinct workspace
  paths
- normalized workspace-relative targets validated to stay inside the workspace
  and outside ignored or generated files
- expected SHA-256 hashes for existing files before modify, delete, or rename
- bounded UTF-8 replacement text for create and modify operations
- non-overlapping operation source and target paths

The first implementation supports conservative whole-file create, modify,
delete, and rename operations. It validates the full operation plan before any
mutation, requires existing-file hashes to match, rejects secret-looking new
content, rejects missing or generated targets, and does not create parent
directories. Verification command execution is a separate post-apply boundary.
General shell execution, network access, provider-side built-in tools,
provider-native function calls, streaming, retries, fallback, OAuth, provider
routing, and additional provider turns remain out of scope.

The terminal archive event is `native.patch.apply.recorded`. Its payload remains
metadata-only:

- `tool_request_id`
- `turn_index`
- `status`
- `reason_label`
- `duration_seconds`
- `file_count`
- `operation_count`
- `operation_labels`
- approval and sandbox labels/booleans
- `workspace_mutated`
- optional safe `scope_label`
- false storage booleans for patch text, diffs, file contents, prompts, model
  output, provider responses, and raw transcript import

JSONL, Markdown, default stdout, and `--native-output json` must not include raw
patch text, raw diffs, replacement file contents, target paths, raw prompts,
model output, provider responses, provider-native payloads, tool payloads,
stdout, stderr, shell commands, auth material, secrets, credentials, tokens,
private keys, or sensitive personal data. Unsafe or unsupported apply data fails
closed before mutation; skipped or failed apply attempts record only safe status
and reason labels. In one-shot injected sessions, a skipped or failed patch
apply result makes the native run fail with only safe error labels; in the
interactive REPL, the shell stays open and prints a safe stderr diagnostic.
Hash failures distinguish missing, malformed, and mismatched expected hashes.
If an unexpected write error happens after one or more operations have already
applied, the terminal result records `reason_label=write_partially_applied` and
`workspace_mutated=true` so archives do not claim a clean no-mutation failure.

### Native Verification Command Boundary

The native verification boundary is the first supervised command-execution path.
It is not arbitrary shell access and it is not provider-selected command
execution. It consumes only an in-memory `NativeVerificationRequest` supplied to
`NativeAgentSession` by pipy-owned control flow after a successful supervised
patch apply. Normal CLI, OpenAI, OpenRouter, and fake provider runs do not
supply this request and remain unchanged.

The request requires:

- the current pipy-owned `tool_request_id` and `turn_index`
- `request_source`: `pipy-owned-human-reviewed`
- command label `just-check`, which is the only supported command label
- explicit required approval and a human-reviewed
  `NativeVerificationGateDecision`
- `read-only-workspace` sandbox policy with workspace read allowed, filesystem
  mutation and network access forbidden, and shell execution allowed only for
  the internal `just check` argv mapping

The first implementation maps the safe label `just-check` internally to the
argv `just check`, resolves the `just` executable before execution, runs from
the requested workspace, and redirects stdin/stdout/stderr to non-archive
channels so command output cannot become CLI stdout, JSON output, Markdown, or
JSONL event content. Unsupported labels, shell-looking labels, missing or
denied approval, unsafe policy, missing `just`, non-zero exit, and execution
errors fail closed with safe reason labels before any provider visibility.

The terminal archive event is `native.verification.recorded`. Its payload
remains metadata-only:

- `tool_request_id`
- `turn_index`
- `command_label`
- `status`
- `reason_label`
- optional safe `error_label`
- `exit_code`
- `duration_seconds`
- approval and sandbox labels/booleans
- optional safe `scope_label`
- false storage booleans for stdout, stderr, command output, prompts, model
  output, provider responses, and raw transcript import

JSONL, Markdown, default stdout, and `--native-output json` must not include raw
command stdout, command stderr, shell command text, raw prompts, model output,
provider responses, provider-native payloads, tool payloads, raw diffs, file
contents, auth material, secrets, credentials, tokens, private keys, or
sensitive personal data. A skipped or failed verification result makes the
native run fail only when a verification request was supplied and the boundary
was invoked. Runs without a verification request preserve the existing
fake/OpenAI/OpenRouter behavior and stdout contracts.

### Native Post-Tool Observation Contract Decision

A post-tool observation is an internal sanitized record that may connect one
native tool result to one bounded follow-up provider turn. The implemented
`NativeToolObservation` value object is not a raw transcript item, not a
provider-native tool result, and not a storage channel for tool output. The
current runtime creates and archives this value from explicitly supported
synthetic sanitized observation fixtures after a supported no-op tool result,
or from one successful bounded explicit-file-excerpt result after safe
pipy-owned read request, gate, and target data. It does not create observations
from provider-native tool result payloads.

The selected future lifecycle event shape is deliberately small: one terminal
event named `native.tool.observation.recorded`. There is no
`native.tool.observation.started` event, because a sanitized observation is
derived after a tool result and must not represent raw payload, stdout, stderr,
diff, patch, prompt, or model-output handling. Separate completed, failed, and
skipped event names are not used; the terminal outcome is carried by the
metadata-only `status` label on the single recorded event. This keeps future
archive compatibility explicit while preserving the current metadata-only
archive surface.

The future `native.tool.observation.recorded` payload allowlist is exactly:

- `tool_request_id`
- `turn_index`
- `tool_name`
- `tool_kind`
- `status`
- `reason_label`
- `duration_seconds`
- `tool_payloads_stored`
- `stdout_stored`
- `stderr_stored`
- `diffs_stored`
- `file_contents_stored`
- `prompt_stored`
- `model_output_stored`
- `provider_responses_stored`
- `raw_transcript_imported`

No normalized counters are included in the first observation event payload
allowlist. A later real tool slice may add finite non-negative counters only
through an explicit schema update and tests.

Allowed observation status labels are terminal only: `succeeded`, `failed`, and
`skipped`. Allowed reason labels are closed safe labels:
`tool_result_succeeded`, `tool_result_failed`, `tool_result_skipped`,
`unsupported_observation`, and `unsafe_observation`. These labels are represented
as inert native model enums so later archive writers do not invent ad hoc
strings.

The identity terms below rely on the pipy-owned request identity defined in
`Native Tool Request Identity And Turn Index`.

Correlation must use pipy-owned identity only:

- `tool_request_id`: the archive-facing id for the pipy-owned tool request.
  It corresponds to the internal `NativeToolRequestIdentity.request_id` and must
  not be copied from provider tool-call ids.
- `turn_index`: the pipy-assigned provider turn that produced the sanitized
  internal tool intent. The current bounded runtime remains `turn_index=0`; a
  bounded follow-up provider turn records its provider turn separately as
  `provider_turn_index=1`; it does not create a second tool request identity.
- safe observation status or reason labels from the closed label sets above.

The first observation event shape is limited to summary-safe metadata:

- safe tool name and kind labels already allowed in current lifecycle events
- result status labels and safe reason/error labels
- `duration_seconds`
- storage booleans that remain explicit, including
  `tool_payloads_stored=false`, `stdout_stored=false`,
  `stderr_stored=false`, `diffs_stored=false`,
  `file_contents_stored=false`, `prompt_stored=false`,
  `model_output_stored=false`, `provider_responses_stored=false`, and
  `raw_transcript_imported=false`

Finite non-negative counters, approval or sandbox policy labels, and optional
sanitized metadata are not included in the first event payload allowlist. They
may be added only through a later explicit observation schema update and tests.

The observation must never contain raw or provider-owned content:

- raw tool result payloads or tool payloads
- stdout or stderr
- diffs, patches, or file contents
- filesystem paths selected by the model
- shell commands or raw tool arguments
- raw system prompts, raw user prompts beyond the short `--goal` session
  metadata, or prompt fragments
- model output
- provider responses, provider-native tool-call objects, provider-native tool
  result objects, function arguments, or provider response ids that could reveal
  payload content
- secrets, credentials, API keys, tokens, private keys, or sensitive personal
  data

A future provider turn may receive only an explicitly designed sanitized
observation derived from this contract. It must not receive raw tool output,
stdout, stderr, diffs, patches, file contents, raw tool arguments,
provider-native tool-call objects, prompts, model output, or provider responses
through the observation path. If real filesystem or shell tool execution is
added later, the observation shape must be implemented alongside approval
prompts and sandbox enforcement so the provider-visible summary cannot bypass
those controls.

Unsupported or unsafe observations must fail closed. The runtime should either
stop the loop safely or emit sanitized skipped/failed lifecycle metadata using
the existing pipy-owned `tool_request_id`, `turn_index`, safe status, safe
reason labels, and storage booleans. Unsafe data must be dropped or redacted in
memory before any archive event, Markdown summary, structured stdout object, or
future provider-visible observation is produced. It must not be archived first
and redacted later.

Still deferred for this boundary:

- additional read-only request kinds such as search excerpts
- a general model/tool loop
- write, patch, shell, or network tool execution
- live approval prompts or broader sandbox enforcement
- multiple tool requests per provider turn
- provider-side built-in tools
- streaming, retries, model fallback, provider registry, OAuth, or raw
  transcript import

### Native Tool Request Identity And Turn Index

The native runtime implements a tool request identity and turn-index contract.
This is narrower than a post-tool observation contract because the current
runtime already has one sanitized internal tool intent and one fake no-op
request. The explicit identity value object prevents later loop work from
guessing whether ids come from providers, archives, or pipy itself.

The selected boundary remains bounded:

- the runtime still makes one initial provider call
- the current fake path still allows at most one no-op tool invocation
- `turn_index` for the pipy-owned tool request remains `0`
- an optional synthetic-observation follow-up provider turn is recorded with
  `provider_turn_index=1`
- `request_id` remains an opaque pipy-owned id, deterministic in tests
- no real tool execution, approval prompt, sandbox enforcement, retry,
  streaming, fallback, provider registry, OAuth, or provider-side built-in tool
  is added

`turn_index` identifies the provider turn that produced the sanitized internal
tool intent. It is assigned by pipy as a small non-negative integer. In the
current bounded tool-request identity there is only one tool-producing provider
turn, so the only valid archived tool `turn_index` value is `0`. Provider call
lifecycle events use separate `provider_turn_index` metadata, where the bounded
synthetic-observation follow-up turn is `1`.

`request_id` identifies one pipy tool request within the native session. It is
generated by pipy after provider parsing, not copied from provider-native
tool-call ids or raw provider payloads. It may be deterministic in tests and
must be stable enough to connect `native.tool.intent.detected`,
`native.tool.started`, and the matching terminal tool event within one record.
It is not a durable cross-run identity and must not encode prompt text, model
output, provider response ids, raw tool arguments, shell commands, filesystem
paths selected by the model, stdout, stderr, diffs, file contents, secrets,
credentials, private keys, tokens, or sensitive personal data.

The current `native-tool-0001` value remains acceptable only because there is
at most one tool request in one native session. The implementation represents
that rule with a small pipy-owned identity value object that derives the safe
request id from the pipy turn/request position rather than from provider
metadata. Tests prove that provider-supplied request ids are rejected as unsafe
input, the archived request id remains safe, and no extra provider/tool turns
are introduced.

Allowed archive fields for this boundary are limited to the existing
metadata-only lifecycle surface: `tool_request_id`, `turn_index`, safe tool
name/kind labels, intent source label, approval and sandbox policy labels,
storage booleans, status, duration, sanitized error type/message, and optional
sanitized counters, booleans, enum labels, or short non-secret identifiers.
The internal native value object may name the same identity `request_id`, but
current archive lifecycle payloads expose it as `tool_request_id`.
Archives and structured stdout must still omit raw prompts, model output,
provider responses, provider-native tool-call objects, function arguments, tool
arguments, tool result payloads, stdout, stderr, diffs, patches, file contents,
secrets, credentials, private keys, tokens, and sensitive personal data.

Still deferred:

- real filesystem or shell tool execution
- approval prompts or sandbox enforcement
- multiple native tool requests per provider turn
- provider retries, streaming, fallback, OAuth, or a provider registry
- provider-side built-in tools such as web search, file search, code
  interpreter, computer use, or background mode
- raw transcript import
- raw prompt, model output, tool argument, tool payload, stdout, stderr, diff,
  patch, file-content, secret, credential, private-key, token, or sensitive
  personal-data storage in JSONL or Markdown by default
- TUI, RPC, compaction, branching, orchestration, and agent delegation

## Run Lifecycle

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#eef2ff', 'secondaryColor': '#ecfdf5', 'tertiaryColor': '#fff7ed', 'primaryTextColor': '#111111', 'secondaryTextColor': '#111111', 'tertiaryTextColor': '#111111', 'textColor': '#111111', 'lineColor': '#334155', 'primaryBorderColor': '#1d4ed8', 'edgeLabelBackground': '#ffffff', 'actorBkg': '#f8fafc', 'actorBorder': '#334155', 'actorTextColor': '#111111', 'actorLineColor': '#334155', 'signalColor': '#334155', 'signalTextColor': '#111111', 'labelBoxBkgColor': '#ffffff', 'labelTextColor': '#111111', 'activationBkgColor': '#eef2ff', 'activationBorderColor': '#1d4ed8'}}}%%
sequenceDiagram
  participant CLI as pipy CLI
  participant Runner as Harness Runner
  participant Adapter as Adapter behind Agent Port
  participant Native as Native Agent
  participant Recorder as pipy-session
  participant Archive as Archive
  participant Terminal as Terminal

  CLI->>Runner: run task
  Runner->>Recorder: init partial session
  Runner->>Recorder: append harness.run.started
  Runner->>Adapter: prepare
  Adapter-->>Runner: prepared run
  Runner->>Adapter: run(prepared, event_sink)
  Adapter->>Native: spawn process
  Adapter-->>Runner: report agent.process.started
  Runner->>Recorder: append agent.process.started
  Native-->>Terminal: stream stdout/stderr
  Native-->>Adapter: exit
  Adapter-->>Runner: report agent.process.exited
  Runner->>Recorder: append agent.process.exited
  Adapter-->>Runner: result
  Runner->>Recorder: append summary events
  Runner->>Recorder: append session.finalized
  Runner->>Recorder: finalize with Markdown summary
  Recorder->>Archive: move JSONL + Markdown
  Runner-->>CLI: exit with native status
```

The Runner should serialize recorder writes through a single async queue or
write lock. Child stdout/stderr streams and adapter lifecycle callbacks may
arrive concurrently; recorder event order must come from Runner-assigned
`sequence`, not thread scheduling.

`session.finalized` is the final JSONL event appended while the record is still
active. The recorder then closes the unit of work and moves the JSONL and
Markdown summary into the finalized archive.

The Runner returns the native exit status only after recorder finalization has
completed or a recording failure has been handled according to the capture
policy.

Status model:

- `pending`: run object created but process not started
- `running`: native process started
- `succeeded`: process exited 0
- `failed`: process exited non-zero or adapter failed
- `aborted`: interrupted by signal or cancellation

## Privacy and Capture Boundaries

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#ecfdf5', 'secondaryColor': '#eef2ff', 'tertiaryColor': '#fff7ed', 'primaryTextColor': '#111111', 'secondaryTextColor': '#111111', 'tertiaryTextColor': '#111111', 'textColor': '#111111', 'lineColor': '#334155', 'primaryBorderColor': '#047857', 'edgeLabelBackground': '#ffffff'}, 'flowchart': {'htmlLabels': true}}}%%
flowchart TB
  subgraph Stored["<span style='color:#111111'>Stored</span>"]
    direction TB
    StoredTitle["<span style='color:#111111'><b>Stored in pipy record<br/>by default</b></span>"]
    A["<span style='color:#111111'>agent name</span>"]
    B["<span style='color:#111111'>adapter name</span>"]
    C["<span style='color:#111111'>workspace basename or path hash</span>"]
    D["<span style='color:#111111'>start/end timestamps</span>"]
    E["<span style='color:#111111'>exit code and status</span>"]
    F["<span style='color:#111111'>safe event summaries</span>"]
    G["<span style='color:#111111'>optional changed file paths</span>"]
    H["<span style='color:#111111'>native session metadata reference</span>"]
    StoredTitle --> A --> B --> C --> D --> E --> F --> G --> H
  end

  subgraph NativeOnly["<span style='color:#111111'>Native store</span>"]
    direction TB
    NativeTitle["<span style='color:#111111'><b>Left in native agent<br/>store</b></span>"]
    I["<span style='color:#111111'>raw transcript</span>"]
    J["<span style='color:#111111'>assistant messages</span>"]
    K["<span style='color:#111111'>tool calls and tool results</span>"]
    L["<span style='color:#111111'>native event stream</span>"]
    NativeTitle --> I --> J --> K --> L
  end

  subgraph NotCaptured["<span style='color:#111111'>Not captured</span>"]
    direction TB
    NotCapturedTitle["<span style='color:#111111'><b>Not captured<br/>by default</b></span>"]
    M["<span style='color:#111111'>prompt text</span>"]
    N["<span style='color:#111111'>stdout and stderr</span>"]
    O["<span style='color:#111111'>full argv</span>"]
    P["<span style='color:#111111'>secrets and credentials</span>"]
    Q["<span style='color:#111111'>file diffs</span>"]
    NotCapturedTitle --> M --> N --> O --> P --> Q
  end

  classDef stored fill:#dcfce7,stroke:#047857,color:#111111,stroke-width:1.5px;
  classDef native fill:#dbeafe,stroke:#1d4ed8,color:#111111,stroke-width:1.5px;
  classDef blocked fill:#ffedd5,stroke:#c2410c,color:#111111,stroke-width:1.5px;
  classDef storedHeading fill:#f0fdf4,stroke:#f0fdf4,color:#111111,stroke-width:0px;
  classDef nativeHeading fill:#eff6ff,stroke:#eff6ff,color:#111111,stroke-width:0px;
  classDef blockedHeading fill:#fff7ed,stroke:#fff7ed,color:#111111,stroke-width:0px;
  class StoredTitle storedHeading;
  class NativeTitle nativeHeading;
  class NotCapturedTitle blockedHeading;
  class A,B,C,D,E,F,G,H stored;
  class I,J,K,L native;
  class M,N,O,P,Q blocked;
  style Stored fill:#f0fdf4,stroke:#047857,color:#111111
  style NativeOnly fill:#eff6ff,stroke:#1d4ed8,color:#111111
  style NotCaptured fill:#fff7ed,stroke:#c2410c,color:#111111
  linkStyle default stroke:#64748b,stroke-width:1px;
```

Default capture policy:

- Store enough lifecycle metadata to find and understand the run later.
- Prefer summaries authored by the harness over raw native content.
- Avoid storing text that came from the user prompt or model output.
- Redact secret-looking metadata keys and values.
- For argv, store only the executable and safe mode flags unless a user
  explicitly requests full command capture in a future opt-in feature.
- For workspace paths, prefer basename plus optional path hash. Full paths are
  sometimes useful locally but can leak private project structure.

## Session Recording Integration

The first harness should initialize a partial pipy session record at run start:

```text
session.started
capture.limitations
harness.run.started
agent.process.started
agent.process.exited
harness.run.completed | harness.run.failed | harness.run.aborted
session.finalized
```

Across the lifecycle, event payloads draw from these fields; not every event
carries every field. Completion-only fields such as `status`, `exit_code`, and
`duration_seconds` should appear on completion, failure, or abort events rather
than every event:

- `adapter`
- `run_id`
- `harness_protocol_version`
- `event_id`
- `sequence`
- `status`
- `exit_code`
- `duration_seconds`
- `cwd_name`
- `cwd_sha256`
- `command_executable`
- `argv_stored`: `false`
- `stdout_stored`: `false`
- `stderr_stored`: `false`
- `raw_transcript_imported`: `false`

`session.started` is a recorder marker. When created by the harness, it should
carry the run envelope fields (`run_id`, `event_id`, `sequence`, `timestamp`)
and `harness_protocol_version`, with `sequence` set to 0. Subsequent harness
events should carry the same envelope fields. `session.finalized` is a harness
event appended before recorder finalization, not a post-archive mutation.

The finalized Markdown summary should include:

- run status
- agent and adapter
- workspace display name
- start/end timestamps or duration
- exit code
- native session reference note, if any
- changed file paths, if enabled
- explicit note that raw transcript content was not imported

## CLI Shape

Add a new product CLI:

```sh
uv run pipy run --agent codex --slug harness-smoke --cwd . -- codex exec "..."
uv run pipy run --agent pi --slug issue-123 --cwd . -- pi -p "..."
uv run pipy run --agent custom --slug smoke --cwd . -- echo "hello"
```

Keep the existing lower-level recorder CLI:

```sh
uv run pipy-session init ...
uv run pipy-session append ...
uv run pipy-session finalize ...
uv run pipy-session list
uv run pipy-session verify
```

Initial `pipy run` flags:

- `--agent <name>`: logical agent name
- `--slug <slug>`: human-facing run label and session filename component
- `--cwd <path>`: working directory for the native command, default current dir
- `--goal <text>`: optional short goal, but avoid storing full prompts by default
- `--record-files`: opt in to recording changed file paths
- `--root <path>`: optional session root override, matching `pipy-session`
- command after `--`: native command to execute

The Runner generates `run_id`; users should not need to provide it in slice 1.
Two invocations may use the same `--slug`; the recorder may disambiguate
filenames, but `run_id` remains the stable identity.

CLI output convention:

- harness diagnostics, event summaries, and progress go to stderr
- child process output streams through according to adapter policy
- native provider final text prints to stdout only when `pipy-native` succeeds
- `pipy-native` session-finalization messages and errors go to stderr
- `--native-output json` emits one final metadata-only JSON object for
  `pipy-native`, not a JSONL event stream, and is rejected for non-native
  agents before record creation

The initial CLI should not have:

- `pipy dev`
- `pipy serve`
- `pipy import-transcript`
- `pipy replay`
- `pipy task spawn`
- `pipy approve`

## First Implementation Slice

Implementation note: this slice is now implemented as the top-level
`pipy run` command backed by `SubprocessAdapter`. It records partial lifecycle
metadata only, streams child stdout/stderr without storing them, finalizes the
record before returning, and keeps changed file path capture opt-in through
`--record-files`.

The first slice should implement a subprocess-backed harness runner. It should
work with real native commands and fake test commands, but it should not parse
native transcripts.

Likely package layout:

```text
src/pipy_harness/__init__.py
src/pipy_harness/models.py
src/pipy_harness/runner.py
src/pipy_harness/capture.py
src/pipy_harness/adapters/__init__.py
src/pipy_harness/adapters/base.py
src/pipy_harness/adapters/subprocess.py
src/pipy_harness/cli.py
```

Likely project metadata change:

```toml
[project.scripts]
pipy = "pipy_harness.cli:main"
pipy-session = "pipy_session.cli:main"
```

Scope:

- create run model and status enum
- create agent port protocol
- create capture policy value object
- implement generic subprocess adapter
- implement `pipy run`
- stream child stdout/stderr through to the terminal
- initialize and finalize a partial pipy session record
- record process start/exit and run completion/failure
- assign `run_id`, `event_id`, and `sequence`
- return the native process exit code
- optionally record changed file paths using git status porcelain output when
  `--record-files` is set

Explicitly out of scope:

- reading native transcript files
- parsing Codex/Claude/Pi JSONL
- storing prompts or model output
- enforcing sandbox policy
- managing approvals
- retrying failed agent runs
- running multiple agents
- serving docs or UI

## Testing Plan

Add focused tests before or alongside implementation:

- runner unit tests use an in-memory recorder fake
- runner success creates and finalizes a partial session record
- runner failure finalizes a record and returns non-zero exit code
- runner returns the native exit code only after recorder finalization completes
- interrupted or adapter error path records failure without mutating finalized
  records
- concurrent adapter event callbacks are serialized with stable `sequence`
- subprocess stdout/stderr are streamed or allowed through but not stored in the
  JSONL or Markdown record
- command prompt text after `--` is not stored by default
- secret-looking argv, metadata keys, and values are redacted or omitted
- `--record-files` records file paths only, not diffs
- without `--record-files`, changed file paths are not recorded
- CLI parser preserves command after `--`
- session archive remains compatible with existing `verify`, `list`,
  `search`, and `inspect`
- integration tests can still use temporary real session roots through `--root`

Suggested test files:

```text
tests/test_harness_runner.py
tests/test_harness_cli.py
tests/test_harness_subprocess_adapter.py
```

## Native Provider And Tool Boundary Slices

Implementation note: the first native slices are implemented as
`--agent pipy-native`. They add:

- native value objects under `src/pipy_harness/native/`
- a minimal `ProviderPort`
- a minimal `ToolPort`
- deterministic `FakeNativeProvider`
- deterministic `FakeNoOpNativeTool`
- allowlisted `NativeVerificationTool` for injected post-apply `just check`
  verification
- explicit in-memory native conversation and turn value objects for
  conversation identity, turn identity, role/status labels, safe per-turn
  metadata, and bounded provider-turn state
- `NativeAgentSession` that owns system prompt construction and allocates its
  bounded provider turn indexes and labels from per-run in-memory conversation
  state
- normalized provider usage metadata limited to finite non-negative
  `input_tokens`, `output_tokens`, `total_tokens`, `cached_tokens`, and
  `reasoning_tokens`
- `PipyNativeAdapter` behind the existing runner boundary
- CLI selection without requiring a command after `--`
- focused provider, tool, session, runner, CLI, and catalog compatibility tests

These slices deliberately remain partial. They do not implement a full
model/tool loop, broad filesystem or shell tool execution, OAuth, provider
registries, retries, broad approval prompts, runtime sandbox enforcement,
external-agent adapters, raw transcript import, TUI/RPC modes, indexed search,
compaction, branching, or multi-agent orchestration.

The native conversation state slice defines inert runtime concepts under
`pipy_harness.native.conversation`. `NativeConversationIdentity`,
`NativeTurnIdentity`, `NativeTurnRole`, `NativeTurnStatus`,
`NativeTurnMetadata`, `NativeConversationTurn`, and
`NativeConversationState` represent bounded provider-turn sequences in memory.
The first state bound is two turns, matching the current one-shot plus optional
post-tool provider call behavior, with a hard internal maximum of eight turns
for later no-tool REPL work. The metadata payload shape is allowlisted and keeps
all storage booleans false: it does not carry raw prompts, model output,
provider responses, provider-native payloads, raw tool observations, stdout,
stderr, diffs, file contents, command output, auth material, secrets,
credentials, tokens, private keys, or sensitive personal data.

`NativeAgentSession.run` now creates a fresh in-memory conversation identity for
each native run and appends provider turns to `NativeConversationState` before
calling the provider. The initial provider request and existing
`native.provider.*` event payloads use turn index `0` and label `initial` from
that state. The optional post-tool provider request and matching provider event
payloads use turn index `1` and label `post_tool_observation` from the same
state. The runtime remains structurally bounded to the existing one initial
provider turn plus at most one follow-up provider turn after a supported
observation.

This rebase does not emit new conversation or turn archive events. Existing
`pipy run --agent pipy-native` text stdout, `--native-output json`,
metadata-only archive, and provider/tool behavior remain unchanged.

`pipy` and `pipy repl --agent pipy-native` now use the same conversation/turn
core for a bounded interactive shell. Its ordinary provider turns are allocated
from `NativeConversationState`, with `initial` for the first turn and
`no_tool_repl` for later REPL turns. Provider construction is late-bound from
the current REPL provider/model selection before each provider-visible turn.
The shell exposes local `/help`, `/login`, `/logout`, and `/model` commands,
one display-only `/read` command, provider-visible `/ask-file <path> --
<question>` and `/propose-file <path> -- <change-request>` commands, the
same-session one-file `/apply-proposal <path>` command, and one post-apply
`/verify just-check` command. The read commands share one bounded
explicit-file-excerpt request per REPL session; `/ask-file` and `/propose-file`
accept a whitespace-delimited `--` separator and forward the bounded excerpt
only in memory to one provider turn labeled `ask_file_repl` or
`propose_file_repl`. Help, auth/model commands, malformed supported slash
commands, and unsupported slash commands print static or safe status diagnostics
on stderr without provider/tool execution, read-limit consumption, tool events,
or raw command archiving. It still does not expose provider metadata, arbitrary
shell execution, non-allowlisted verification commands, streaming, retries,
fallback, TUI rendering, conversation export, or a general model/tool loop.

The proposal-only REPL boundary is available as
`/propose-file <path> -- <change-request>`. It reuses the same bounded
explicit-file-excerpt path and one-read limit, forwards one excerpt plus one
change request only in memory to a single provider turn labeled
`propose_file_repl`, accepts at most one pipy-owned structured patch proposal
metadata object, emits at most one metadata-only
`native.patch.proposal.recorded` event, and stops before patch apply,
verification, shell execution, network access, provider-side tools, multiple
tool requests, or a general model/tool loop.

The follow-up REPL boundary is implemented as
`/apply-proposal <workspace-relative-path>`, the first public write-capable
shell command. It consumes only a same-session, in-memory, human-reviewed
proposal for the exact same normalized path, normalizes that draft into one
`NativePatchApplyRequest`, applies exactly one operation to one file through the
existing mutating-workspace boundary, and records only metadata-only
`native.patch.apply.recorded` status.

The verification REPL boundary is implemented as `/verify just-check`, accepted
only after a successful same-session `/apply-proposal` mutation. It maps the
safe label to the internal `just check` argv through the existing
`NativeVerificationTool`, records only metadata-only
`native.verification.recorded` status, suppresses command stdout and stderr, and
fails the REPL run when verification is skipped or fails. Generic patch
application, arbitrary shell execution, non-allowlisted verification commands,
provider-side tools, and broader tool loops remain deferred to separately named
slices.

## Deferred Work

For the current task-slice backlog and next-step ordering, see
`docs/backlog.md`. The list below records broader deferred design areas.

- Full native pipy agent runtime beyond the bootstrap slice.
- Codex JSONL event adapter.
- Claude hook integration beyond existing conservative `pipy-session auto`.
- Pi-native session inspection beyond metadata references.
- Raw transcript import with explicit opt-in and redaction policy.
- Indexed archive search.
- Repo maps or workspace summaries.
- Permission policy and sandbox profiles.
- Interactive TUI.
- RPC mode.
- Multi-agent task delegation.
- Long-running dev server.
- Docs server such as Zensical.

## Open Questions

### 1. Should the first `pipy run` allow arbitrary subprocess commands?

Recommendation: yes, but frame it as a `custom` or `subprocess` adapter and keep
the capture policy conservative.

Reasoning: this makes the first harness testable without depending on Codex,
Claude, or Pi being installed and authenticated. Named adapters can still add
agent-specific validation later.

### 2. Should changed file paths be recorded by default?

Recommendation: no. Add `--record-files` in the first slice.

Reasoning: file paths can leak project structure. Recording paths is useful, but
it should start as an explicit choice until the redaction and display policy is
settled.

### 3. Should the new CLI be `pipy`?

Recommendation: yes.

Reasoning: `pipy-session` is a useful low-level recorder CLI, but the harness is
the product surface. A top-level `pipy run` command makes the distinction clear:
`pipy` runs agent tasks; `pipy-session` manages records.

### 4. What is the first native-agent target?

Recommendation: after the generic subprocess harness slice, implement a native
pipy runtime bootstrap rather than a thin Codex or Claude adapter. This is now
the implemented second slice.

Reasoning: pipy should own the agent loop, prompt stack, provider boundary,
tool boundary, auth boundary, and durable session semantics. Calling `codex`,
`claude`, or another coding-agent CLI would inherit that product's system
prompt, approval model, transcript shape, and execution loop, which defeats the
purpose of building pipy as a clean-architecture Pi-like agent. External-agent
wrappers are useful for smoke tests and reference records, but they should not
be the main product direction.

The native runtime bootstrap establishes:

- a native `pipy` agent path behind the same runner/adapter boundary
- a minimal provider port and prompt/session construction owned by
  pipy
- a fake no-op tool port with explicit approval and sandbox policy data
- model output, prompts, and tool payloads kept out of the pipy archive by
  default
- deferral of a real tool loop, approval prompts, sandbox enforcement, and raw
  transcript import unless explicitly scoped

### 5. Should a docs server be introduced now?

Recommendation: no.

Reasoning: the near-term review artifact is one Markdown spec plus the existing
session-storage document. Mermaid diagrams in Markdown are enough. Revisit
Zensical or a similar docs server when navigation, search, publishing, or live
preview becomes a real bottleneck.

## Recommendation

The first harness slice is implemented as `pipy run` with a generic subprocess
adapter, lifecycle events, conservative session recording, and focused tests.
The second slice is implemented as a native pipy runtime bootstrap with a
minimal provider/session boundary and deterministic fake provider path. The
next implementation should extend the native runtime deliberately rather than
making Codex or Claude subprocess wrapping the main product path. Track the
current next slice in `docs/backlog.md`.
