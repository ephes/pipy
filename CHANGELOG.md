# Changelog

All notable changes to pipy are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); `/changelog` renders these
entries oldest-first, and a version bump shows the new entries at startup.

## [Unreleased]

### Added

- Extend internal durable MemoryStorage with current raw-base document create,
  base replacement/version transitions, retirement, detached materialization and
  exact address/scope scans inside the same table candidate, global ID/sequence
  authority and single adoption. Mixed late failures roll back all tables,
  documents, indices and counters. Document cursors preserve Pi's ascending,
  order-ignoring behavior. The cumulative pinned comparison retains table proofs
  and adds actual document operations,14 valid semantic mutants and live native
  mixed rollback/ownership/concurrency tests. Delta/copy/numeric history/fork
  materialization and Session integration remain later slices; no product default
  or parity requirement is closed. Exact built-in document-point admission rejects
  foreign objects/subclasses before comparisons or user hooks under the authority.

- Add an internal atomic in-memory durable table engine with detached records,
  global IDs/commit sequences, replacement indices, ancestry-aware entry scans,
  marker lookup, source-lazy descending/point/head reads, minimum-cap stops,
  UTF-16 string identity and one authority for writes/reads/mint/
  close. A separate pinned comparison runs17 exported Node conformance cases and
  seven paired Node/Python recipes, with valid field/ID/sequence/index/cursor/alias
  mutants and native fault/contention tests. Recorded argument packs receive the
  existing per-operation shape/DTO validation: malformed evidence is exit2, valid
  wrong values remain mismatch1. Raw scan limit0/-1/-2/+1 boundaries are pinned
  by regression coverage, retaining actual Pi outcomes and distinct raw language
  errors without a new positivity policy. Coverage retains101 cumulative rows.
  This DE02a2a stage has no product factory or Session integration and explicitly
  refuses documents; DE02a2b raw-base/cumulative Storage and DE02b remain open,
  together with every parity row.

- Add native owned finite JSON and versioned record-tree codecs, validated
  Storage port DTOs and a bounded actual Pi value/consumed-record comparison.
  Full binary64 payloads, signed zero, lone-surrogate keys and deep ownership
  survive; IDs remain a separate safe-integer domain. Parsed Python objects with
  two UTF-16-equivalent keys reject as unrepresentable, without merging values
  or excluding individual surrogate strings. Coverage now retains99
  cumulative delivery/parity rows. Record omission and versioned envelope
  admission reject nonplain keys before hashing, comparison or copying, without
  invoking caller hooks. Owned equality/hash preserve float bits, Python numeric
  types and ordered representation; versioned record decode rejects constructor-
  only omission. The actual comparison uses16 distinct JS values and reports
  valid run mismatches as exit1 with hashed evidence, while invalid evidence is2.
  This DE02a1 seam has no Python MemoryStorage,
  Session or product-default integration; DE02a2/DE02b and all parity rows remain
  open. The preceding mandatory reference CI/upload gate was fully audited at
  `13d061d2`/CI39370449156; historical review findings remain recorded.

- Add a mandatory pinned durable-reference CI job and closed selective textual
  evidence collector, with early-failure metadata and explicit excluded DB/archive
  attestations. Local implementation does not claim hosted delivery or native
  durable parity; DE02 stays blocked until the actual pushed job/upload is audited.

- The [durable acceptance command](docs/durable-reference.md#fresh-local-acceptance)
  now bootstraps all six pinned reference families once, executes twelve exported
  storage reads and three writes with persisted readbacks before bounded timing,
  records hardware/counts, and requires four freshly derived valid mismatch
  mutants. Manifest3 binds the workload, verification tools and exhaustive95-row
  DE/P current-versus-future index. Coverage statuses and source/contract anchors
  match the ledger/plan, with future CI evidence separated from inherited local
  observations. Malformed mutation inputs fail as invalid evidence; regression
  controls distinguish negativeCLI2 from required semantic mismatch1.
  Python parity remains open; mandatory hosted
  reference CI and selective artifacts remain in DE01b3b.

- The [pinned durable reference runner](docs/durable-reference.md) now executes
  real SQLite recovery across four replay-policy combinations, durable abort
  with refused late writes, and two exported Node environment byte/argv cases.
  Six complete case families and four schema-valid semantic negatives retain
  raw provenance, effect counts and bounded typed projections. Malformed task
  outcomes fail as invalid evidence. Closed database sidecars are checked against
  physical bytes/hashes, and raw chunks must match actual settled streams.
  Python durable
  parity remains open; local workload acceptance follows in DE01b3a and mandatory
  hosted reference CI remains in DE01b3b.

- The [durable reference runner](docs/durable-reference.md) now verifies three
  pinned real cases: the existing byte-identical kernel fixture, a faux-provider
  echo/null-details round, and reset/summary context selection. A strict
  multi-case manifest hashes the complete adapter closure; collision-free
  tagged values and bounded witnessed identity/duration projections retain raw
  evidence and ID-ordered scan relations. Sparse JS arrays are rejected before
  serialization, preserving dense null/undefined distinctions. Real
  lost-field/stale-head mutants remain valid traces and fail comparison.
  Python durable parity remains open.

- A verification-only [pinned durable reference runner](docs/durable-reference.md)
  executes Pi's actual kernel and memory storage in a private exact-commit
  snapshot, validates trace, exact archived source membership and dependency
  provenance, and rejects malformed, missing or mismatched evidence. Python
  durable execution and parity remain pending. Its missing-runtime unit tests
  isolate successful prerequisites from host Node availability and version.
  The existing PTY tree-selector test also waits for the true prompt's input
  acknowledgement before sending selector commands.

- A pinned [native durable execution work order](docs/specs/2026-10-19-durable-execution-plan.md),
  upstream surface audit and evidence ledger track the authorized implementation
  campaign. Its initial import contains experiments, with no product recovery,
  session-format or default change and no completed parity requirements.

- A standalone [canonical-loop durability integration spike](docs/durable-loop-integration.md)
  replays the real native agent loop over persisted rich provider/tool receipts,
  verifies commit-before-completed-message observation and exercises hard-crash
  recovery. Product entry points, defaults and session stores are unchanged.

- An isolated [durable native run prototype](docs/durable-prototype.md) with an
  offline provider, actual native tools, SQLite task checkpoints and reproducible
  process-crash experiments. It changes no product runtime or session storage;
  unsafe interrupted tools remain explicitly blocked for inspection.

- Default Azure Responses requests now stream text and thinking through the
  shared Responses consumer, retaining Azure deployment/API-version, auth,
  cache and replay behavior. Explicit JSON client injection preserves the
  original buffered wire contract.

- Default OpenAI Responses requests now stream text and thinking incrementally,
  including xAI, Copilot Responses and custom rows selecting that API. Streams
  close at the terminal response, retain partial failed/aborted history and
  reject unfinished tool calls. Explicitly injected JSON clients retain buffered
  compatibility.

- Read the Docs build configuration for the existing Zensical documentation,
  using a dedicated locked `docs` dependency group and Python 3.14. The target
  site is published at [pipy-agent.readthedocs.io](https://pipy-agent.readthedocs.io/en/latest/)
  (project imported 2026-10-17). Hosted commands invoke
  `python -m uv` so pip's executable directory need not be on `PATH`.
  Contributor instructions cover setup and local reproduction of the build.

### Changed

- Codex GPT-6.1 Sol now uses a 500K context window, matching Pi `5712b134e`;
  its 128K output limit and other Sol rows are unchanged.
- `just catalog-drift` now requires a manifest timestamp from the last seven
  days. Old, invalid, missing or future timestamps fail as input errors;
  `--snapshot` explicitly permits historical or unstamped input and labels
  the report. Azure comparisons now use Pi's `azure` provider table.
- Python 3.15 is explicitly supported alongside 3.11–3.14. CI adds full tests,
  Linux/macOS real-PTY checks, and Linux runtime-required codemode probes on
  3.15. The optional codemode guest remains pinned to CPython 3.14.7.
- The README now starts with installation, provider setup, product sessions,
  MCP, codemode, and automation. Detailed workflow recorder, external CLI
  capture, Claude hook, and sync instructions moved to
  [Workflow capture and sync](docs/workflow-capture.md). The backlog and parity
  overview reflect the MCP merge and October maintenance/evaluation work;
  contributor review guidance follows the current diminishing-returns policy,
  and `AGENTS.md` reflects the public repository status.

- Escape queued between a Codemode nested read and continuing computation now
  reaches the existing input waiter. The builtin product runner retains raw
  input ownership through executable cleanup, preserving intentional outer
  terminal transitions and foreign-I/O handoffs.

- Generic Chat Completions adapters now retain buffered reasoning and structured
  replay details across same-target continuation and resume. Exact-target
  provenance and focused compatibility flags preserve foreign-history privacy;
  Mistral native thinking and Chat streaming remain separate work.

- Mistral now retains ordered native unsigned thinking across same-target
  continuation and durable resume. Native replay uses content arrays and
  `prefix:false`, omits foreign thinking and forwards no opaque signatures.
  Canonical history provenance is `mistral-conversations`; existing `mistral`
  routing, Chat Completions endpoint and buffered transport remain unchanged.

- The architecture's extension history is replaced by a short overview and a
  [current extension runtime map](docs/extension-runtime.md) covering component
  ownership, activation, generation publication, queues, and shutdown. Existing
  anchors and the controlling transactional reload contract are preserved.

- Documentation now starts with a concepts guide and explicit fake-versus-real
  setup paths, with compact agent-loop/storage diagrams and navigation grouped
  by audience. Mobile navigation supports keyboard opening, bounded focus,
  Escape-to-close, and focus return without changing desktop navigation.

- Five existing catalog metadata values now match the pinned Pi snapshot:
  Sonnet 5.5 cache-read accounting on Anthropic/Copilot, both Sonnet 4.5 context
  aliases and Mistral Small's context. Model overrides, maximum output and
  effort behavior are unchanged; twelve behavior-dependent differences remain
  explicitly deferred in the catalog triage.

### Fixed

- Codex SSE responses now close when explicitly retired before their first
  read and on every parser exit, including provider errors and sink failures.
  Shared HTTP code owns cancellable raw-payload streaming; Codex authentication,
  WebSocket fallback and retry policies remain with the Codex adapter.

- Provider requests now omit thinking from a different provider, API or model,
  matching Pi's replay behavior. Model switches preserve visible text and
  tool-call/result correlations without exposing historical thinking as text.
  Same-model signed/redacted reasoning and original stored history are retained.

- Workflow archive append/finalize operations now serialize across local threads
  and processes, preventing writes after finalization and stale appends from
  recreating retired records. Automatic capture start, event, stop and prune
  share identity ownership so old cleanup cannot delete a replacement mapping.

- The nested-call cancellation regression test waits for the queued call and
  checks its durable settlement order separately from caller-thread completion
  order, removing a scheduling-dependent CI failure without changing runtime
  cancellation behavior.
- OAuth discovery no longer waits for the body of an answer it does not
  use, as Pi's discovery cancels it: a 404, 502 or other failure status of
  a metadata URL is acted on at its headers, so a server whose error body
  stalls no longer holds the sign-in up until the request's time limit (and
  then fails it). The OAuth fetch also follows a redirect without waiting
  for the redirect's body, as Node's `fetch` does, also for registration and
  token requests. A fetch passed to the OAuth library
  (`adapt_oauth_provider(provider, fetch=...)`) takes the new
  `discard_unless_ok` argument. See
  [the OAuth library](docs/mcp.md#the-oauth-library).
- In JSON and RPC mode, the `toolResult` message of a tool that ran other
  tools (a `codemode` script, or an extension tool through
  `ctx.execute_tool`) carries the record of those calls as its own
  `nestedCalls` field, as Pi's `ToolResultMessage` does, in `turn_end`,
  `agent_end` and `get_messages`. Before, it was inside the message's
  `details`, which no longer hold it; the message keeps the rest of the
  result's details. `tool_execution_end` still has the record in its
  `result.details`. See
  [the event vocabulary](docs/automation-rpc.md#event-vocabulary-pipy-must-emit).
- A codemode call's nested call whose arguments are over 8 KiB (or past
  the record's 32 KiB in all) shows the first 200 characters of them in a
  resumed session's row and in the HTML export, as Pi's codemode rows show
  each call's `args` preview, instead of only `[<n> argument bytes]`: the
  codemode result keeps them in `details.argumentPreviews` beside the
  `nestedCalls` record, whose arguments stay bounded. See
  [codemode](docs/codemode.md#nested-evidence-and-availability).
- The export's credential redaction withholds a quoted value that the text
  ends inside to the end of the text: a 200-character argument preview (or
  an error cut to its bound) that stops inside a password no longer shows
  the rest of it after the first word. See
  [the export](docs/export-distribution.md).
- A call to a tool that is not declared, or not callable from a codemode
  script or `ctx.execute_tool`, is answered `Tool <name> not found`, Pi's
  text (`agent-loop.ts` `prepareToolCall`), instead of
  `unknown tool: <name>`. See
  [tool exposure](docs/extension-api.md#tool-model).
- An MCP request that is cancelled or times out keeps its HTTP event
  stream, as in Pi: pipy sends `notifications/cancelled` and goes on
  reading, and resuming, the stream that answers the request, so a log
  message or a tool list change the server sends there afterwards still
  arrives. Before, giving the request up closed its connection and dropped
  them. See [HTTP servers](docs/mcp.md#http-servers).
- An MCP server's event stream that ends before the `Content-Length` it
  announced is a network failure, as for Node's `fetch`. The
  server-to-client stream counts it against its five retries even when it
  delivered events, so a server that keeps cutting it short no longer has
  it reopened for ever. A response stream cut that way fails its request,
  or is resumed, with that error.
- Each `notifications/tools/list_changed` and
  `notifications/resources/list_changed` starts a listing of its own, as in
  Pi, so a listing the server does not answer no longer holds up the next
  one until it times out. Of listings that overlap, the one that ends last
  is kept, as in Pi, also when it was asked for first. See
  [server connections](docs/mcp.md#server-connections).
- In JSON and RPC mode the `toolResult` message of an extension tool that
  ran other tools with `ctx.execute_tool` carries its nested-call record
  (the message's `nestedCalls` field, see above) and the rest of the
  result's `details` in `turn_end`, `agent_end` and `get_messages`, as a
  `codemode` result's message does and as Pi records
  the nested calls on every tool result message. Before, only `codemode`
  messages had it; the record was in `tool_execution_end` only. See
  [the event vocabulary](docs/automation-rpc.md#event-vocabulary-pipy-must-emit).
- A codemode script whose `image()` call passes the output limit of
  16,777,216 characters fails also when it catches the `ValueError`: the
  run has failed before the error is raised, so the output after it and
  the script's `store()` writes are dropped, as Pi's `output()` fails the
  script before it throws. Before, a script could catch the error, go on
  and succeed. See [images](docs/codemode.md#images).
- `/reload` declares again every tool that was declared before it, not
  only MCP tools, once the tool registers before the next prompt starts:
  a `deferred` extension tool that `tool_search` loaded and that the
  reloaded extension registers in a `before_agent_start` handler is
  declared in the next request without another search, as in Pi; one that
  registers after the next prompt started has to be loaded again. Before,
  such a tool was dropped when `--tools`, `--exclude-tools` or
  `defaultTools` were given, and otherwise declared again whenever it
  registered. A tool `/tree` restores while it is `hidden` (the
  resource tools while no server offers resources, a tool of a disabled
  server) is not declared, and once the next prompt has started it has to
  be loaded again when it comes back, as in Pi; before, it was declared
  again without a search. See [Reloading](docs/mcp.md#reloading).
- A tool that `tool_search` loaded under a `--tools` list that does not
  name it, and that its server lists again or an extension registers again
  as `direct`, leaves the active tools for good, as Pi applies the loadout
  on every refresh: when it is `deferred` again it has to be loaded again.
  Before, it was declared again without a search. A name restored from the
  session that the list does not let be active stays pending until the
  next run starts, as in Pi. See [MCP tools](docs/usage.md#mcp-tools).
- The entries of `ctx.tools` (and of `ctx.get_all_tools()`) carry the
  tool's `output_schema` and `execution_mode`, as Pi's `AgentTool` entries
  do, so an extension tool that runs others can tell which return typed
  results. See [running other tools](docs/extension-api.md#running-other-tools).
- An MCP server with `auth.provider` is sent the `--api-key` of the
  session when the server names that key's provider, before a stored login
  or the Codex login, also for a provider an extension registered, as Pi's
  runtime key replaces the stored credential. A provider that signs in with
  OAuth only (`openai-codex` without a `models.json` `apiKey`, or an
  extension provider with `oauth`) then gets no token, as in Pi. Before,
  `openai-codex` was sent the Codex login and an extension provider never
  got the key. See [a provider's token](docs/mcp.md#a-providers-token).
- An SDK product session with `mcp` sends a server whose entry has
  `auth.provider` that provider's token, from `auth.json`, the environment,
  `models.json` and the Codex login in the default files, as a `pipy`
  session does and as Pi's SDK resolves it through its model runtime. Before,
  such a server got no token in an SDK session. See
  [codemode, tool search and MCP](docs/sdk.md#codemode-tool-search-and-mcp).
- An API key stored as `!command` in `auth.json` runs once per process and
  its first result is kept, for model requests and for MCP `auth.provider`
  requests, as Pi caches it. Before, it ran for every MCP request and for
  every provider construction. A `models.json` `apiKey` command still runs
  every time, as in Pi. See
  [credential command helpers](docs/providers.md#credential-command-helpers).
- pipy's own rows that a tool renderer resolver wraps (`next()`'s
  renderers for a built-in tool, `codemode` or an MCP tool) draw the
  context they are called with: its `args`, `content`, `details`,
  `is_error`, `is_partial` and `expanded`, as Pi's renderers draw the
  arguments, result and options they are given. A wrapper that passes
  `dataclasses.replace(ctx, args=..., content=...)` can now change or
  withhold what they show; before, they drew the call's own arguments and
  result whatever the context held. The `edit` diff preview is left out
  under other arguments. See
  [tool rendering](docs/extension-api.md#tool-rendering).
- A codemode script's nested calls are part of codemode's result rows, as
  in Pi's codemode renderer: renderers that replace codemode's, or the
  generic row a resolver chose, no longer show pipy's nested-call lines,
  and a resolver that wraps codemode's rows gets them in the result part,
  also while the script runs (its `render_result` is called with empty
  `content` once the script made a call, and `details` holds a
  `nestedCalls` record of the calls, which the wrapper can change or
  clear). Captured output writes them as
  they run only for codemode's own rows. The export page's "Nested calls"
  section is unchanged. See [codemode](docs/codemode.md) and
  [tool rendering](docs/extension-api.md#tool-rendering).
- Renderers drawing a call for the HTML export (`/export`, `/share`) are
  told `execution_started=True`, as Pi's export renders every call as
  started; before it was false, so a renderer that waits for execution drew
  the call as not started.
- An extension tool whose handler raises fails the call with the
  exception's message as its error result, as Pi's agent loop reports
  `error.message` (Pi's docs tell extension authors to throw to fail a
  call); the type name stands in for an empty message. Characters that
  would steer a terminal become spaces (line breaks and tabs stay) and the
  text is bounded like other tool output. A caller through
  `ctx.execute_tool` or a codemode script gets the same text. Before, the
  result was only `extension tool error: <exception type>`. A raising
  `prepare_arguments` reports its message the same way, without the tool
  name (before: `<name>: prepare_arguments raised <exception type>`). See
  [extension API](docs/extension-api.md).
- A tool renderer's call context has `is_error` set once the call failed,
  as Pi takes `isError` from the call's current result for both phases, so
  a custom `render_call` drawn again after the result in the terminal
  (Ctrl+O, a resize, `invalidate()`) can mark the failure. Before, the call
  context always said `False`. The HTML export draws a call before its
  result, with `is_error` false, as Pi's export does. See
  [tool rendering](docs/extension-api.md#tool-rendering).
- A tool renderer resolver's `next()` gives renderers for the calls to
  `codemode`, as Pi's codemode tool brings its `codemodeRenderers`: they
  draw the rows pipy draws for codemode (in the terminal and the HTML
  export the script output without the `Script completed` header), so a
  resolver can wrap them there and in captured output. Before, `next()` gave
  `None` for `codemode`, and a resolver that returned `None` without
  `next()` left codemode's own result rows in place instead of the generic
  row. An extension tool named `codemode` that has renderers keeps them.
  See [tool rendering](docs/extension-api.md#tool-rendering).
- A registered extension tool without renderers is what a tool renderer
  resolver's `next()` returns, as Pi's registered definition, so
  `next() or mine` no longer fills in for it. An extension tool that
  replaces `codemode` without renderers is drawn with the generic rows,
  partial results included, in the terminal and left to the page in
  `/export` and `/share`; before, it got the built-in codemode rows, which
  hid its partial results and treated its output as a script's (see
  [tool rendering](docs/extension-api.md#tool-rendering)).
- `/share` uploads the page `/export` writes, as Pi's `/share` uploads its
  `exportToHtml` page: the session's system prompt, and the tool calls drawn
  with the loaded extensions' renderers and resolvers and the session's MCP
  tool titles. Before, the shared page had no system prompt and drew only MCP
  calls, titled from their pipy names, so custom MCP renderers and resolvers
  were lost. The page is drawn on the session's thread before the upload
  starts, so no extension code runs on the upload's thread. `pipy --export`
  still runs no extension code. See
  [export](docs/export-distribution.md#artifact-shape).

- `/reload` with `--tools` or `--exclude-tools` given keeps an extension
  tool that `tool_search` loaded declared, as Pi's reload keeps every active
  tool the filters allow; before, only loaded MCP tools came back. See
  [tool search](docs/mcp.md#tool-search).
- An extension's `ctx.set_active_tools([...])` that names `codemode`
  activates the built-in `codemode` also when nothing selected it before:
  pipy registers it then (one runtime self-test), as Pi has it registered
  inactive. Before, the name was dropped. See
  [the tool model](docs/extension-api.md#tool-model).
- `ctx.get_all_tools()` keeps listing an MCP tool that its server withdrew,
  or whose server was disabled, with exposure `hidden`, as Pi's
  `getAllTools` does. It stays neither declared nor callable, and `/mcp`
  does not count it. See [when tools change](docs/mcp.md#when-tools-change).
- An MCP server's tools keep the order they were registered in when the
  server announces a new tool list, as Pi keeps the place of a tool it
  registers again: a list that orders them another way, or drops a tool
  and offers it again, leaves them where they were, and a new tool comes
  after the server's others. Before, the new list's order replaced it, so
  of two tools that match a `tool_search` query equally well the other one
  could come first after the change (see
  [MCP](docs/mcp.md#when-tools-change)).
- A `!` glob in the `extensions`, `skills`, `prompts` or `themes` settings
  matches as Pi's minimatch does for `**/`, which also matches no directory:
  `"extensions": ["!**/builtin:mcp"]` now turns MCP off, where before it
  matched nothing and the servers still started. See
  [turning MCP off](docs/mcp.md#turning-mcp-off).
- A `!` glob in the `extensions` setting is read as Pi's minimatch reads
  it, braces and extended globs included: `"extensions":
  ["!builtin:{mcp,codemode}"]` or `["!builtin:+(mcp|codemode)"]` now turns
  MCP and codemode off, where before both matched nothing and the MCP
  servers still started. As in Pi, the `!`, `+` and `-` entries for a
  built-in extension are also tried on the path relative to the settings
  file's directory (`!../builtin:mcp` in a project's settings), and
  `pipy config enable|disable builtin` replaces such entries too. pipy's own
  `-` and `+` entries in `skills`, `prompts`, `themes` and `extensions` use
  the same globs, so a `*` there no longer crosses a `/` or matches a
  leading dot. pipy ports minimatch and brace-expansion for this
  ([decision](docs/decisions/2026-10-15-minimatch-globs.md)). See
  [turning MCP off](docs/mcp.md#turning-mcp-off).
- An unknown `--extension builtin:<name>`, Pi's `builtin:llama.cpp`
  included, ends the startup as in Pi: `Error: Failed to load extension
  "builtin:<name>": Unknown built-in extension: builtin:<name>`, the hint
  `Hint: Start without extensions using "pipy -ne".` and status 1 (before:
  `pipy: Unknown built-in extension: ...` and status 2). `--list-models`
  ignores it, as Pi's does.
- The codemode page's `store()`/`load()` example reads the page of an MCP
  call from `structuredContent`, and the page says that an MCP tool returns
  its whole `CallToolResult` (`content`, `structuredContent`, `isError`), as
  Pi's documents it. Before, the example read the fields off the envelope
  and would raise `KeyError`. See [codemode](docs/codemode.md).
- An HTTP MCP server that takes longer than 30 seconds to accept
  `notifications/initialized`, or another notification or response pipy
  sends it, no longer fails: as Pi does, pipy waits for that acceptance
  with no deadline, and closing the connection ends the wait. A request
  still times out after the server's `timeout`. See [MCP](docs/mcp.md).
- The streamable HTTP transport of the MCP library
  (`StreamableHttpTransportOptions`) waits for a server to accept a
  notification or a response with no deadline by default, as Pi's transport
  does; `send_timeout_ms` sets one. Before, a transport built without that
  option failed after 30 seconds. See
  [what exists as a library](docs/mcp.md#what-exists-as-a-library).
- `pipy mcp login` and `/mcp login` open the browser as Pi does: with
  `open` on macOS, `rundll32` on Windows and `xdg-open` elsewhere, in a
  session of its own without pipy's input and output, and without waiting
  for it. Before, Python's `webbrowser` ran on the sign-in's thread, so a
  launcher that blocked (a `BROWSER` command, `osascript` on macOS) delayed
  the prompt for the pasted address and made `--timeout` late. See
  [sign-in with OAuth](docs/mcp.md#sign-in-with-oauth).
- A stdio MCP server whose transport cannot start one of its threads (the
  interpreter is out of threads) is killed and the connect fails with that
  error. Before, closing the connection then waited without end. See
  [what exists as a library](docs/mcp.md#what-exists-as-a-library).
- A `!command` value (an API key, or an MCP server's `env`, `headers` or
  `oauth.clientSecret` value) runs with no stdin, as in Pi, so `!cat` or a
  command that asks a question can no longer read a piped prompt or RPC
  input, or wait for the terminal.
- `pipy mcp logout` and `/mcp logout` sign out even when a token refresh
  holds the server's refresh lock for the whole 25-second wait: the
  credentials are then deleted anyway, as Pi deletes them, instead of the
  sign-out failing with `Timed out waiting for another MCP OAuth token
  refresh`. A refresh that is answered afterwards may store its tokens
  again, as in Pi. See [MCP](docs/mcp.md#the-credentials-file).
- Closing a stdio MCP server on macOS with Python 3.11 or 3.12 now ends a
  child of the server that outlives it and keeps its stdout or stderr open:
  the transport waits for the server's exit with a kqueue there (Python has
  no `os.waitid` on macOS before 3.13), so the exited server stays unreaped
  and its process group keeps getting the SIGTERM and SIGKILL of the close,
  and of the exit hook, until the pipes close, as with Pi. Before, the
  server was reaped at once and such a child was never signalled. See
  [MCP](docs/mcp.md).
- A codemode row no longer freezes after 256 nested calls: the live row
  keeps the latest 256 (past that the oldest finished call goes for each
  new one), so the latest calls and failures stay visible, as Pi's renderer
  shows the latest of every call. The collapsed
  `... (N earlier calls, ctrl+o to expand)` counts every call left out, and
  an expanded row starts with `... (N earlier calls, not kept)` when calls
  were let go. Before, later calls were not shown at all. The `nestedCalls`
  record in the result is unchanged (the first 256 calls, `complete: false`
  past that). See [codemode](docs/codemode.md#nested-evidence-and-availability).
- A tool renderer resolver that wraps the renderers `next()` gives for a
  built-in tool keeps the row's live parts, as a wrapper of Pi's built-in
  renderers does: a wrapped `edit` row shows the diff preview while the
  call runs, and a wrapped `bash` row shows `Elapsed` from the start and
  ticks it every second until the result, with the output so far above it.
  Before, both were missing until the result arrived. A resolver that
  leaves the row to the generic renderers (returns `None`, or renderers
  without callables) still gets neither, as in Pi. See
  [tool rendering](docs/extension-api.md#tool-rendering).
- With a `--tools` allowlist, `set_active_tools` (and adding tools to the
  selection) drops an MCP-named tool the allowlist does not let be
  activated, as Pi's `_applyToolLoadout` does, instead of keeping it in the
  selection. Before, a later re-registration of such a tool as `deferred`
  declared it without any activation or `tool_search` load.
- An `auth.json` that starts with a UTF-8 byte order mark is read (Pi
  `stripBom`), by the auth store and by the provider token an
  `auth.provider` MCP server sends. Before, such a file read as empty, so a
  stored login was ignored and the server asked for sign-in.
- A `!command` value (auth `api_key`, `models.json` keys, MCP `env` and
  `headers` values) is read as Pi reads it: the output is decoded with
  invalid UTF-8 replaced by U+FFFD (before, it made the value unavailable)
  and trimmed with JavaScript's `trim()` whitespace set, so a byte order mark
  or U+3000 around the value is dropped while U+001C to U+001F and U+0085,
  which Python's `strip()` removed, are kept.
- A built-in extension's `extensions` entries follow Pi's override rules
  (`isEnabledByOverrides`, `applyAutoloadDisabledPatterns`): a `!pattern`
  glob such as `!builtin:mcp` or `!builtin:*` turns it off, which was ignored
  before, so MCP stayed on and its servers launched. `+builtin:<name>` and
  `-builtin:<name>` now name one exactly (before they were globs, so
  `-builtin:*` turned all three off; in Pi it matches none). In the user
  array a `-` entry wins over a `+` entry, which wins over a `!` entry,
  whatever their order (before, the last matching entry won). A matching
  `!`, `+` or `-` entry in a trusted project's array decides, the last one
  winning (before only `+` and `-` entries counted there).
  `pipy config enable|disable builtin <name>` replaces every `+` or `-`
  entry that names the built-in exactly (also `./builtin:<name>`), so an
  earlier `-` entry cannot keep it off. See
  [turning MCP off](docs/mcp.md#turning-mcp-off).

- A typed `/skill:<name>` line whose skill does not exist or cannot be read
  goes to the model as typed, as in Pi, after pipy reports why; and a typed
  skill, template or custom command line meets the `input` hooks before it
  is expanded or refused, also when it cannot run. Before, such a skill line
  was refused locally before the hooks ran and reached neither the hooks nor
  the model. An empty template or custom command still sends nothing. See
  [event hooks](docs/extension-api.md#event-hooks).
- The calls a tool makes, from a codemode script or with
  `ctx.execute_tool`, no longer count against the per-turn tool budget
  (`--tool-budget`, default 50), as Pi has no limit on them. Before, one
  script with about 50 calls spent the whole turn's budget: its later calls
  failed with `tool budget exhausted` and the model could call no further
  tool that turn. The budget now counts only the calls the model makes. See
  [running other tools](docs/extension-api.md#running-other-tools).
- An extension tool's `ToolResult(terminate=True)` works as in Pi: when
  every result of the tool calls of one model response asks for it, the run
  ends after those results without the automatic follow-up provider turn. A
  `tool_call` hook that blocks a call can ask the same with
  `ToolBlock(reason, terminate=True)`, and JSON and RPC `tool_execution_end`
  show `result.terminate: true`. Before, the documented field did not exist.
  See [ending the batch](docs/extension-api.md#ending-the-batch).
- An extension tool can publish partial results while it runs with
  `ctx.on_update(ToolResult(...))`, Pi's `onUpdate`: each one replaces the
  one before in JSON and RPC `tool_execution_update`, in a calling tool's
  `on_update` and in the call's live row, published on the session thread
  also when the handler's own thread sends it. Before, the docs promised a
  progress callback that handlers did not get. See
  [partial results](docs/extension-api.md#partial-results).
- A nested call that waits for its turn behind a `sequential` tool's call
  starts at once, as in Pi: its start event (JSON and RPC
  `tool_execution_start` with `parentToolCallId`) and its live row come when
  it is asked for, not when its turn comes, and one that never runs because
  its calling tool or script ended gets its end event too. Before, such a
  call was missing from the live rows and the event streams while it
  waited, and a withdrawn one had no events at all. See
  [running other tools](docs/extension-api.md#running-other-tools).
- `bash` reports its live output as Pi does: each partial result is the
  output so far (the last 2000 lines or 50 KB, with `truncation` and
  `fullOutputPath` in its `details` once it is cut), for JSON and RPC
  `tool_execution_update` and for an `on_update` callback of
  `ctx.execute_tool`. Before, each update carried only the next chunk, so a
  consumer that replaces the previous partial result, as Pi's clients do,
  lost the output before it. See
  [the event vocabulary](docs/automation-rpc.md#event-vocabulary-pipy-must-emit).
- Another session in the same run (`/new`, `/resume`, `/fork`, `/clone`,
  `/import`) no longer keeps what the extensions registered after they
  loaded: the tools, tool renderer resolvers and MCP servers registered
  from a `session_start` handler, a command or a tool are gone, as in Pi,
  whose new runtime loads the extensions again. Before, a `session_start`
  handler that called `register_tool_renderer` added its resolver once more
  for every new session, so it was asked once per session for every row, and a
  tool or server a command registered stayed in the next session. See
  [tool rendering](docs/extension-api.md#tool-rendering) and
  [MCP servers](docs/extension-api.md#mcp-servers).
- A model call to an extension tool prepares its arguments once: such a call
  runs as a parent that may call other tools, and that path prepared the
  call's arguments a second time instead of running those the `tool_call`
  hooks saw. The agent loop now hands the parent runner the prepared,
  hook-checked arguments, as the direct path does.
- An extension dialog's `timeout` counts down in the terminal, as in Pi:
  `ctx.ui.select`, `input` and `confirm` show the seconds left in their
  title and are cancelled when they run out (`None`, or `False` for
  `confirm`). Before, only RPC mode honoured it and a terminal dialog
  meant to expire waited for ever. See
  [extension UI](docs/extension-api.md).
- Extension `input` hooks see all input Pi shows them. An RPC `steer`,
  `follow_up`, or `prompt` sent during a run meets the hooks when it is
  queued, before its skill or template is expanded, as Pi's
  `_queueUserInput` does; a hook may handle it (`disposition: "handled"`,
  nothing queued), and the hooks do not see it again when it runs. A
  skill or template line typed into a session meets them before its
  expansion too. Before, both skipped the hooks, so a redacting or
  filtering extension was bypassed. A `prompt` sent as a run ends is
  expanded once (it could be expanded twice). See
  [commands in a prompt](docs/automation-rpc.md#commands-in-a-prompt) and
  [event hooks](docs/extension-api.md#event-hooks).
- Another session in the same run (`/new`, `/resume`, `/fork`, `/clone`,
  `/import`, and the RPC and SDK operations that do the same) is a new
  session for the extensions and the tools, as in Pi: the extensions get
  `session_shutdown` and then `session_start`, with `reason` `new`,
  `resume` or `fork` and the file of the session that follows or came
  before, and the tool selection starts again from the configured tools,
  so a selection an extension made, a tool `tool_search` loaded and a
  `codemode` that MCP servers turned on are gone. Before, extensions got
  neither event and every tool but the MCP ones stayed as it was. The
  `session_shutdown` at exit has `reason` `quit`. See
  [reloading](docs/mcp.md#reloading) and
  [event hooks](docs/extension-api.md#event-hooks).
- Tool selection follows Pi: a name in `--tools`, `--exclude-tools` or the
  `defaultTools` setting that no tool has is ignored with a notice instead of
  stopping startup, so a global `defaultTools` that names one project's
  extension tool no longer breaks every other project. An extension's
  `ctx.set_active_tools([...])` ignores unknown and `hidden` names and applies
  the others, where before one unknown name refused the whole selection. A
  tool that is registered again as `hidden` (an extension's withdrawn tool,
  also through `/reload`, or the MCP resource tools once no server offers
  resources) leaves the active tools, so a tool that `tool_search` had
  loaded has to be loaded again when it comes back. See [tool options](docs/usage.md#tool-options).
- `images.blockImages` also keeps the `@image` attachments of the user's
  turn from the model, as in Pi: the user message gets the line `Image
  reading is disabled.` in their place. Before, they were sent. See
  [providers](docs/providers.md#size-of-tool-result-images-and-images-settings).
- Inline images on Kitty-protocol terminals (kitty, Ghostty, WezTerm, Warp):
  a JPEG, GIF or WebP tool result image is converted to PNG and drawn, as Pi
  does, when Pillow (the optional `images` extra) is installed. Before, only
  PNG images were drawn and the others showed their text line, which is
  still what happens without Pillow. See
  [images in tool results](docs/tui-workflow.md#images-in-tool-results).
- MCP over HTTP: compressed answers work, as with Pi's `fetch`. The
  transport's default fetch asks for `gzip` and `deflate` and decodes an
  answer in those codings, a JSON body as a whole and an event stream as
  it arrives. Before, a server or proxy that compressed its answer (or a
  configured `Accept-Encoding` header) made every request fail. See
  [what exists as a library](docs/mcp.md#what-exists-as-a-library).
- MCP config: with `"clientRegistration": "cimd"` a `callbackUrl` whose
  path is `/callback` once dot segments are removed (`/a/../callback`) is
  accepted, as in Pi. See [configure servers](docs/mcp.md#configure-servers).
- `!command` config values (provider keys, MCP `env` and `headers`) discard
  the command's stderr and accept up to 1 MiB of stdout, as Pi does. Before,
  stderr counted toward a 64 KiB limit, so a helper that logged a lot
  resolved as unavailable. See
  [credential command helpers](docs/providers.md#credential-command-helpers).
- Project trust: a `trust.json` that starts with a UTF-8 byte order mark is
  read, as Pi reads it, instead of failing as unreadable.

- Codemode: a script's output is laid out as Pi lays it out, so the model
  can tell where one item ends and the next begins. With more than one
  `text()` item, each starts with a `==> text N/M <==` line, and what the
  script printed (`print()`, writes to `sys.stdout` and `sys.stderr`)
  follows all other output in one `<console_output>` block. Before, the
  items ran together. A script can also hand a tool arguments of up to
  16 MiB as JSON (a large file for `write`); before, a call over 1 MiB ended
  the script as a sandbox failure, and now larger arguments raise
  `ValueError` in the script. A tool's reply to a script counts the UTF-8
  bytes of non-ASCII text against its 16 MiB limit instead of 6-byte
  escapes, so a 6 MB accented result reaches the script. See
  [writing a script](docs/codemode.md#writing-a-script).

- Extension tools: calls that tools make of other tools
  (`ctx.execute_tool`, codemode scripts and their `submit()`) now follow
  Pi's execution mode. A tool registered with
  `ExtensionTool(..., execution_mode="sequential")` (Pi `executionMode`)
  has its calls run one at a time, in the order they were asked for, while
  other calls run beside them; a call made inside the running sequential
  call does not wait for it. Before, every nested call ran at the same
  time, so two calls of a tool with shared state could overlap. See
  [running other tools](docs/extension-api.md#running-other-tools).

- Security: the key given with `--api-key` is no longer sent to an MCP
  server whose `"auth": {"provider": "<provider>"}` names another provider
  than the one of the model the session starts with. Such a server gets
  that provider's own token, or none, as in Pi; before, it was sent the
  `--api-key` key of any provider. See
  [a provider's token](docs/mcp.md#a-providers-token).

- Security: an MCP server URL with a non-ASCII host is now written in
  punycode as Pi's URL parser writes it (UTS #46, nontransitional), so
  `https://faß.de/mcp` connects to `xn--fa-hia.de`; before, Python's
  network code mapped it to `fass.de`, another domain, and sent it the
  configured headers and tokens. OAuth resource checks, stored sign-ins and
  callback ids use the same host, and a host Pi refuses makes the URL
  invalid. pipy now depends on the `idna` package
  ([decision](docs/decisions/2026-10-15-idna-hosts.md)).

- Keep token-count fields such as `totalTokens` and `tokensBefore` in native
  HTML/JSONL exports instead of redacting them, so exported sessions re-import
  with every assistant message and compaction. Prefixed secret keys such as
  `access_token`, `OPENAI_API_KEY=` and `GITHUB_TOKEN=` inside text are now
  redacted too.

- Reject malformed scalar metadata in workflow archive exports before it can
  leak nested payloads or be stringified into labels; keep errors field-only.

- Serialize automatic capture starts per platform session and clean up owned
  files when mapping publication fails.

- Escape terminal controls in all human workflow catalog fields and summaries,
  preserving stored records and JSON output.

- Refresh context files and system-prompt replace/append inputs on `/reload`,
  removing stale text after edits or deletion while retaining explicit prompt
  precedence, project trust filtering and conversation history.

- Refuse native session export paths that alias the backing session and publish
  exports atomically, preserving existing output on failure. Exports are now
  owner-only (0600) and replace destination symlinks rather than their targets.
- Require imported session cwd paths to be directories, supporting copied-only
  replacement for non-directory paths.
- Reserve private import destinations exclusively so concurrent imports and
  dangling symlink collisions cannot overwrite sessions; clean up failed copies.

- Treat invalid UTF-8 auth-command output as unavailable without exposing it.
- Bound auth-command capture and terminate the owned process group on timeout.

- Enforce image attachment byte budgets during descriptor reads, including file growth.

- Kill and reap clipboard image helpers that hang after stdout closes; reject
  unfinished execution and clean up the owned POSIX process group.

- Treat invalid UTF-8 release notes as unavailable so startup and `/changelog`
  remain usable without modifying the changelog file.

### Added

- `pipy config inherit builtin <name>` puts a project's setting for a
  built-in extension (`mcp`, `codemode`, `tool-search`) back to inherit, as
  the inherit state of Pi's config list does: it removes the `+`, `-` and
  `!` entries of `.pipy/settings.json` that name `builtin:<name>`, and a
  bare `builtin:<name>`, so the user settings decide again. A `!` glob that
  only matches it, such as `!builtin:*`, stays. `inherit` always writes the
  project settings (`--scope global` is refused) and needs a trusted
  project. Before, a project's entry could only be flipped between `+` and
  `-`. See [turning MCP off](docs/mcp.md#turning-mcp-off).
- `ExtensionTool(prepare_arguments=...)` (Pi `prepareArguments`): a callback
  that receives a call's parsed arguments before validation and returns the
  arguments to validate and run. If it raises, the call ends with the
  exception's message as its error result. See
  [extension tools](docs/extension-api.md).
- The automation fake provider (`--native-provider fake --native-model
  fake-tools`) asks for one `bash` call (`echo hi`) when the prompt starts
  with `TOOLCALL`; the RPC conformance gate checks the tool events of that
  call (`json_mode_tool_events`).
- TUI: images in tool results (MCP screenshots, codemode `image()`,
  extension tools) are drawn inline after the tool row in terminals that
  speak the Kitty graphics protocol (kitty, Ghostty, WezTerm, Warp, Herdr)
  or iTerm2 inline images, as Pi's `ToolExecutionComponent` draws a pi-tui
  `Image`. `terminal.showImages` (default on) and `terminal.imageWidthCells`
  (default 60) are honoured; `PIPY_IMAGE_PROTOCOL=kitty|iterm2|none`
  overrides the detection. Under tmux or screen, in other terminals and in
  non-terminal output the row keeps the `[Image: [<mime>] <w>x<h>]` line.
  See [images in tool results](docs/tui-workflow.md#images-in-tool-results).
- Images in tool results (MCP tools and resources, codemode `image()`,
  extension tools) are processed as Pi processes them before they are
  stored, shown and sent: an image larger than 2000x2000 pixels or 4.5 MB
  of base64 is scaled down, with a text that gives the original size and
  the coordinate scale, and an image of another type than PNG, JPEG, GIF or
  WebP is converted to PNG. `images.autoResize: false` turns the resizing
  off. This needs the new optional extra `images` (Pillow); without it the
  images are sent as before. See
  [providers](docs/providers.md#size-of-tool-result-images-and-images-settings).
- `images.blockImages: true` keeps tool result images from every provider
  request; each becomes the text `Image reading is disabled.`. The terminal
  still draws them, as in Pi.
- An `input` hook can return `InputHandled` (Pi's `{action: "handled"}`):
  later hooks do not run and no run starts. A handled prompt of `--print`
  prints nothing, `--mode json` writes only the header, an RPC `prompt` is
  answered with disposition `handled`; a typed line starts no run.
- In RPC mode every extension has the client as its UI, as in Pi:
  `ctx.has_ui` is true, `ctx.ui.select`, `confirm`, `input` and `editor` are
  `extension_ui_request` dialogs the client answers, and `set_status`,
  `set_widget` (lines of text), `set_title`, `set_editor_text` and
  `paste_to_editor` are requests without an answer (`setStatus`,
  `setWidget`, `setTitle`, `set_editor_text`). `select`, `confirm` and
  `input` take Pi's `timeout` option (milliseconds); in RPC mode the dialog
  then ends with its default. Before, extensions had no UI in RPC mode and
  only `/mcp` sent requests.
- `StreamableHttpTransportOptions(fetch=...)`: a program that builds on
  `pipy_harness.native.mcp` can pass the function that sends the HTTP
  requests of a transport, for a proxy or a network of its own (Pi's
  `fetch` option). See `pipy_harness.native.mcp.http_fetch`.
- `pipy_harness.native.mcp.oauth.flow.adapt_oauth_provider(provider)`: a
  program that builds on pipy's MCP library can connect an HTTP MCP server
  that wants OAuth with any OAuth client provider and a store of its own,
  with a shared refresh after a 401 and a step-up authorization for more
  scope (Pi's `adaptOAuthProvider`).
- Keybindings: new action `app.message.copy` (default `ctrl+x`), which can
  be rebound in `keybindings.json`. The sign-in screen of the `/mcp`
  manager copies the authorization URL with it, as Pi's does; before, the
  key was fixed to Ctrl+X.
- A tool renderer's `ToolRenderContext` has Pi's call fields:
  `tool_call_id`, `cwd`, `execution_started`, `args_complete`,
  `is_partial`, `show_images`, `duration_ms`, `last_component` and
  `invalidate()`, which draws the call's row again from any thread. See
  [Tool rendering](docs/extension-api.md#tool-rendering).

- An extension tool can give `prompt_snippet` and `prompt_guidelines`, as
  Pi's `promptSnippet` and `promptGuidelines`: the default system prompt
  lists the tool with its snippet and adds its guidelines to the rules
  while it is declared, and a codemode script sees the guidelines with the
  tool's description. `ctx.get_all_tools()` reports `prompt_guidelines`, and
  the loadout of `prepare_loadout` gains `get_exposure`, `get_namespace` and
  `get_prompt_guidelines`. A tool that replaces `codemode` or `tool_search`
  is shown with its own texts. See
  [Extension tool metadata](docs/extension-api.md#extension-tool-metadata).

- A codemode script may start with Pi's options line,
  `// @options: {"max_output_tokens": 2000, "timeout_ms": 60000}`: a budget
  for the result's text (up to 16000 tokens in pipy) and a deadline for the
  whole script. A script without `timeout_ms` now has no deadline, as in Pi;
  before, every script was stopped after 120 seconds. See
  [Writing a script](docs/codemode.md#writing-a-script).

- `read` attaches JPEG, PNG, GIF and WebP files as images, after a `Read
  image file [<type>]` line, as Pi's `read` does; before, it returned an
  error for images. pipy does not resize or convert images: one over
  2000 x 2000 pixels or 4.5 MB of base64, and a BMP, is left out with Pi's
  `[Image omitted: ...]` text, as is a file that is cut short or malformed.
  An `@file` reference still refuses an image and points to `@image:`.
  A codemode script gets an image as the block
  `{"type": "image", "data", "mimeType", "note"}`, which `image()` shows,
  and a text file as its text. See [Writing a script](docs/codemode.md#writing-a-script).

- A codemode script that calls `bash` gets Pi's structured result,
  `{"output", "truncated", "full_output_path", "exit_code",
  "wall_time_seconds"}`, also when the command exits with a non-zero code,
  which no longer raises `ToolError` in the script. `output` is not cut to
  the 2000 lines or 50 KB the model sees: it holds up to 1 MiB, and longer
  output keeps its first and last 512 KiB, with the full output in
  `full_output_path`. See [Writing a script](docs/codemode.md#writing-a-script).

- Optional Python codemode: install the pinned CPython 3.14.7 WASI runtime with
  `pipy codemode install`, check it with `pipy codemode status`, and opt in via
  `defaultTools: ["+codemode"]` or `--tools +codemode`. Sequential calls to
  advertised native builtins use normal validation, policy, hooks, budgets and
  cancellation. Every script starts a fresh worker with a 120-second wall limit
  (tool time included), 256 MiB memory and bounded output; unavailable codemode
  is omitted with one warning and no interpreter fallback or automatic download.
- Codemode child lifecycle in TUI/JSON/RPC, bounded parent nested-call evidence
  reconstructed on resume/tree, and owner-only full-output spilling on display
  truncation. Only parent results enter durable/provider history. Completed tool
  effects are not undone after failure, timeout or abort. Agent-loop settlement
  and recording now share the direct/nested pipeline without changing direct calls.
- Runtime-required isolation and production delivery tests, PTY Escape/replay
  coverage, and an isolated paired-measurement driver. Recorded live initial pair
  and interactive use passed; the repeat retained correct facts but failed the
  prescribed one-script pattern (two scripts/eight reads). Measurements are
  descriptive, not a general performance claim. See
  [CM1 acceptance](docs/specs/2026-10-09-python-codemode-acceptance.md).

- Images in tool results. A tool result can hold images next to its text:
  the session file stores them, JSON and RPC `toolResult` messages carry
  them, and every provider adapter sends them to the model as Pi does (see
  [providers](docs/providers.md#thinking-images-and-current-limits)); a
  model whose catalog row lists no image input gets the text `(tool image
  omitted: model does not support images)` instead. A tool row shows an
  image as `[Image: [image/png] 640x480]` after the result's text, live,
  after a resume and in an HTML export, where the terminal cannot draw it, and the export page shows the
  images themselves after the result. An image an MCP tool returns and an
  image resource read with `read_mcp_resource` now reach the model as
  images, where they were the text `[image <mime> omitted]`; see
  [images in results](docs/mcp.md#images-in-results). A codemode script
  shows an image with `image(value)` (an MCP image block, a base64 `data:`
  URL or a dict with `image_url`); the result attaches it after a line with
  the path of the temp file it was saved to. See
  [codemode images](docs/codemode.md#images).

- More of the OAuth library for MCP servers.
  `pipy_harness.native.mcp.oauth` now holds the authorization code flow (stored or dynamically registered
  client or a Client ID Metadata Document, PKCE, the `resource` parameter,
  the issuer check of RFC 9207, token exchange and refresh with recovery
  from rejected credentials), a provider that keeps one server's OAuth state
  in a store its caller supplies, the loopback callback server and the
  sign-in pages. See [the OAuth library](docs/mcp.md#the-oauth-library).
- The store for the OAuth credentials of MCP servers:
  `McpOAuthCredentialStore` keeps one entry per server name and URL in
  `mcp-auth.json` next to the user-level `mcp.json`, private to the user,
  and has the lock that keeps two processes from refreshing a server's
  tokens at the same time. See
  [the credentials file](docs/mcp.md#the-credentials-file).
- The auth provider for the connections of an OAuth MCP server:
  `McpServerAuthProvider` sends the stored access token,
  refreshes one that is about to expire or that the server rejected with a
  401, and reports that a sign-in is needed when it cannot. The HTTP
  transport hands a rejected request (401, or 403 asking for more scope) to
  its auth provider once and sends it again.
- Stored OAuth credentials on the connections of HTTP MCP servers, in a
  session and in `pipy mcp list`. A server without an `Authorization` header
  or `auth` is sent the access token stored for it in `mcp-auth.json`, which
  is refreshed shortly before it expires and when the server rejects it.
  A server that wants a sign-in pipy cannot answer from what is stored is in
  the new state `needs-auth`: calls fail with `MCP server "<name>" requires
  sign-in. Run /mcp to sign in.`, and the startup report, `/mcp` and `pipy
  mcp list` show `needs sign-in`. Such a server showed as `failed` with
  `MCP server requires authentication` before. See
  [sign-in with OAuth](docs/mcp.md#sign-in-with-oauth).
- `auth` in an MCP server entry: `"auth": {"provider": "<provider>"}` sends
  an HTTP MCP server the key a model request to that provider would use. The token
  is read for every request and never stored for the server, and the server
  never signs in with OAuth. When the server rejects it, the server shows as
  `needs sign-in` and calls fail with `MCP server "<name>" requires
  sign-in. Run /login <provider> to sign in.` The setting was accepted and
  unused before; such a server that answered 401 showed as `failed`. See
  [a provider's token](docs/mcp.md#a-providers-token).
- `pipy mcp login <server> [--timeout <seconds>]` signs in to an HTTP MCP
  server that uses OAuth: it prints the authorization URL, opens it in the
  browser and waits for the browser to come back to a port on this machine,
  or, in a terminal, for the redirect URL pasted from a browser that cannot
  reach it. A stored refresh token is tried before the browser, a server
  that asked for more scope gets the scopes granted so far as well, and the
  whole sign-in ends after the timeout (300 seconds by default). `pipy mcp
  logout <server>` deletes the stored credentials. See
  [log in and out](docs/mcp.md#log-in-and-out).
- The `/mcp` manager. In the terminal, `/mcp` now opens a manager instead
  of printing the status: it lists every MCP server with its state, exposure
  and source, the ones that need attention first, and follows them while
  they connect. A server's menu shows its transport, configuration file,
  state and whole error and offers what the state allows: `Sign in`,
  `Tools`, `Reconnect`, `Sign out`, `Exposure` (`codemode`, `deferred`,
  `direct`), `Disable` or `Enable`, and for a user-level server in a trusted
  project `Disable in this project` and `Enable in this project`, which
  write an override to the project's `mcp.json`. Enabling, disabling and
  reconnecting run in the background. `Sign in` and `/mcp login` show a
  sign-in screen with the authorization URL, Ctrl+X to copy it and an input
  for the redirect URL pasted from a browser that cannot reach this machine;
  `/mcp login` printed the URL as a notice and could not take a pasted URL
  before. Outside the terminal UI `/mcp` prints the status as before. See
  [the `/mcp` manager](docs/mcp.md#the-mcp-manager).
- Calls to MCP server tools have their own rows in the terminal: the title
  is `<server>/<tool>` in place of the pipy name, a collapsed result shows
  its first 5 lines counted after wrapping (a result is often one long line
  of JSON) with `... (<n> more lines, ctrl+o to expand)` and, for a
  shortened result, `Full output: <path>`; a running call shows its last
  progress line. A resumed session draws such calls the same way before the
  server connected, and when it never does. Before, these calls had the
  generic row: the pipy name as title and the first 10 lines of the result.
  See [how a call is shown](docs/mcp.md#how-a-call-is-shown).
- HTML exports show the calls to MCP server tools, and to tools that an
  extension's renderers draw, as the rows of the transcript: the call, and
  the result collapsed with a click to expand it. Before, such a call was
  shown as its raw arguments, or not at all next to assistant text, and its
  result as plain text. `/export` and `/share` use the loaded extensions'
  renderers; `pipy --export` draws only the MCP rows. See
  [export](docs/export-distribution.md#artifact-shape).
- Extension API: `api.register_mcp_server(name, config)` adds an MCP server
  for the session, with the shape of an `mcpServers` entry of `mcp.json`. A
  server registered while the extension loads connects with the session,
  one registered later at once; `api.unregister_mcp_server(name)` closes it
  and `api.get_mcp_servers()` lists every registration. A server of
  `mcp.json` with the same name wins, and `/mcp` lists the overridden
  registration. Enabling, disabling and changing the exposure of a
  registered server in `/mcp` last for the session. An invalid entry and a
  name another extension has raise `McpServerRegistrationError`. Hooks for
  the new `mcp_servers_change` event receive the registrations after every
  change (`McpServersChangeEvent`). With MCP turned off and no such hook, a
  registered server is reported once as an error of its extension, because
  nothing connects it. See [MCP servers](docs/extension-api.md#mcp-servers).
- Extension API: a tool can run other tools while it runs.
  `ctx.execute_tool(name, arguments)` runs a callable tool (the active
  `direct` tools and every `codemode` or `deferred` tool) through the same
  argument validation, `tool_call` and `tool_result` hooks and tool budget
  as a call the model made, and returns its `ToolResult`; a failure is a
  result with `is_error`, never an exception. `ctx.tools` lists what it can
  call. The calling tool's result keeps a bounded record of the calls as
  `nestedCalls`. Only a tool the model called can do this, not one that was
  itself called from a tool or a script. See
  [running other tools](docs/extension-api.md#running-other-tools).
- Extension API: `ExtensionTool(prepare_loadout=...)` lets a tool that
  orchestrates other tools change what the model sees while it is declared:
  the function gets the declared, callable and registered tools
  (`ToolLoadout`) and returns replacement descriptions and the declarations
  that requests leave out (`ToolLoadoutChanges`). `default_active=False`
  registers a tool without activating it. See
  [presenting the loadout](docs/extension-api.md#presenting-the-loadout).
- An extension that registers the `/mcp` command replaces the built-in MCP
  support for the session: `mcp.json` is not read, no server connects and
  `/mcp` is the extension's. pipy says so at startup and after `/reload`.
  An extension tool named `codemode` or `tool_search` replaces the built-in
  tool of that name; MCP activates it, and waits for servers before its
  calls, only when it declares `reaches_tools`. The `pipy mcp` commands
  keep using the built-in implementation. See
  [replace the built-in MCP support](docs/mcp.md#replace-the-built-in-mcp-support).
- Python SDK: `create_product_session` and `open_product_session` take
  `codemode=CodemodeOptions(...)`, `tool_search=True` and
  `mcp=McpOptions(...)`. A product session has the `codemode` tool, the
  `tool_search` tool and MCP servers only when it is created with them;
  without them it reads no `mcp.json` and connects to nothing, whatever the
  settings say. `CodemodeOptions` replaces the `codemode.mode` and
  `codemode.inlineBudget` settings for the session. `McpOptions` can replace
  the configuration loader, the transport factory, the credential store, the
  log path, the function that opens a sign-in URL, the function that saves a
  change to a server and the time the first prompt waits for servers; what it
  leaves out is what the `pipy` command does. The new example
  `docs/examples/sdk_codemode_mcp.py` runs a prompt with all three. See
  [Codemode, tool search and MCP](docs/sdk.md#codemode-tool-search-and-mcp).
- Extension API: `api.register_tool_renderer(resolver)` chooses how the
  calls to any tool are drawn, also of a tool that is not registered and of
  a built-in tool. `resolver(tool_name, next)` returns a `ToolRenderers`
  (new, with `render_call` and `render_result`) or `None`; `next()` is what
  the later resolvers and the tool itself would use. A transcript that is
  drawn again (resume, `/tree`, `/fork`) now asks the loaded extensions:
  stored calls to an extension tool get its renderers before the session's
  first request, where they had the generic row until then. See
  [tool rendering](docs/extension-api.md#tool-rendering).
- `/mcp login [server]` signs in to an OAuth MCP server from a session in
  the terminal: it shows the authorization URL, opens the browser, waits for
  it and connects the server; Escape cancels. `/mcp logout [server]` deletes
  the stored credentials. A session also notices a sign-in made outside it,
  for example by `pipy mcp login` that the agent ran through `bash`: at the
  start of the next turn it connects the server again, so that turn has the
  server's tools. Ending a session cancels a sign-in that is running. See
  [the `/mcp` command](docs/mcp.md#the-mcp-command) and
  [sign-in with OAuth](docs/mcp.md#sign-in-with-oauth).
- `oauth.clientRegistration: "cimd"` for an HTTP MCP server: pipy signs in
  with a Client ID Metadata Document under `oauth.clientMetadataBaseUrl`
  instead of registering a client, with a document and a redirect address of
  the MCP server alone when the authorization server does not name itself
  in its responses. A server that does not accept such documents fails the
  sign-in. `oauth.clientMetadataBaseUrl` must now be an `https` URL without
  query or fragment. The other `oauth` settings (`clientId`, `clientSecret`,
  `callbackPort`, `callbackUrl`, `clientName`, `scope`,
  `authServerMetadataUrl`) are described in one place. See
  [OAuth settings](docs/mcp.md#oauth-settings).
- HTTP MCP servers. A server configured with a `url` is connected over
  streamable HTTP with its `headers`, whose values may use `${NAME}` and
  `!command`, in a session and by `pipy mcp list`. A connect that fails with
  a network error or with status 408, 429 or 5xx other than 501 is made again
  after 250 ms and after one second; a resource read or listing is made once
  more after such a status, a tool call is not. When the server forgot the
  session (status 404), pipy starts a new one and makes the request once
  more, tool calls included. See
  [HTTP servers](docs/mcp.md#http-servers).
- MCP resources. When a connected server offers resources, the tools
  `list_mcp_resources`, `list_mcp_resource_templates` and `read_mcp_resource`
  exist, with the shapes Codex and OpenCode use. They reach every enabled
  server with resources whose exposure is not `hidden` and take the widest
  exposure among those servers, so they are declared to the model for a
  `direct` server and callable from codemode scripts otherwise. Listings are
  JSON without MCP App resources and icons; read text is shown as text and
  other binary content is saved to a temp file; scripts get the objects. A
  resource link in a tool result now names `read_mcp_resource` and the
  server. An image resource is a text placeholder for the model until tool
  results carry images. See [Use resources](docs/mcp.md#use-resources).
- `pipy mcp list` shows how many resources and URI templates a server
  offers (`resources: <n>, URI templates: <m>`, and `resources` and
  `resourceTemplates` with `--json`), for servers that declare the
  `resources` capability; resources for MCP Apps are not counted. A
  connection lists them when it connects and again when the server announces
  a change. See [MCP](docs/mcp.md#list).
- `/reload` reads `mcp.json` and the `builtin:mcp` setting again: it closes
  the MCP servers and starts the ones configured now, so a server that was
  removed is gone, a new one connects and a changed one runs with its new
  entry. The startup report is made again and the next prompt waits for
  servers with `direct` tools like a first prompt. A tool that `tool_search`
  loaded is declared again when its server has connected before the next
  prompt starts; the same holds for `/tree` to an entry that declares a tool
  of a server that is still connecting. A reload that keeps the previous
  extensions keeps the servers too. A server that connects or changes its
  tools while a reload is prepared no longer makes that reload fail. See
  [Reloading](docs/mcp.md#reloading).
- `/mcp` shows the MCP servers of a session: one line per server with its
  state, tool count and exposure, the whole error of a server that is not
  connected, and the errors of the `mcp.json` files. `/mcp reconnect
  [server]` connects a server again, which restarts a stdio server's
  process, and the terminal completes the action and the server name. The
  command waits for the servers that are still connecting; Escape ends the
  wait.
  Enabling, disabling and changing the exposure of a server in a session are
  built into the MCP manager, with the change saved to the `mcp.json` that
  defines or overrides the server, but nothing in a session reaches them
  until the `/mcp` manager exists. A prompt given on the command line or
  sent by an RPC client is not run as `/mcp`. See [MCP](docs/mcp.md#the-mcp-command).
- `pipy mcp add`, `pipy mcp remove` and `pipy mcp list` configure and check
  MCP servers from the shell, without starting a session. `add` writes a
  stdio or HTTP entry into the user-level `mcp.json`, or with `-l` into the
  project's, after checking it; `list` connects to every enabled server and
  prints state, tools and errors as text or with `--json`, exiting 1 when
  something is wrong. See
  [MCP](docs/mcp.md#the-pipy-mcp-commands).
- The system prompt lists the MCP servers whose tools are not declared to the
  model, in a section named `mcp_servers`: each enabled server with
  `codemode` or `deferred` tools, how its tools are reached, and one line
  from its `description` or, once connected, from its instructions. The
  section is written when a prompt starts and, when it changed, appended to
  the conversation without touching earlier messages or tool declarations.
  See [MCP](docs/mcp.md#the-servers-in-the-system-prompt).
- `before_agent_start` handlers can read and change the prompt sections of
  the coming run through `event.system_prompt_options.sections`: add a
  section, replace one of pipy's by name, or remove the MCP server list.
  Changed sections are recorded in the conversation like pipy's own. See the
  [extension API](docs/extension-api.md#prompt-sections).
- MCP servers turn on the tool that reaches their tools: `codemode` for a
  server with `codemode` exposure (the default) unless `autoEnableCodemode`
  is false in `mcp.json`, `tool_search` for one with `deferred` tools. This
  is decided from the configuration at startup. When neither tool is active
  pipy warns once that the MCP tools cannot be called. After startup one
  warning lists the errors of `mcp.json` and the servers that failed
  (`MCP servers need attention:`). `--no-mcp` turns MCP off for a run,
  `"extensions": ["-builtin:mcp"]` in the settings turns it off for good
  (`pipy config disable builtin mcp`), and `--no-extensions` now turns it off
  too unless `--extension builtin:mcp` is given. See
  [MCP](docs/mcp.md#control-tool-exposure).
- Tool model for MCP and tool reach: tool definitions can carry an exposure
  (`direct`, `model-only`, `codemode`, `deferred`, `hidden`), a namespace,
  annotations, an output schema and a reach function, and tool results can
  carry structured content for scripts. A definition can hold a schema pipy
  did not write (an MCP server's); it reaches the provider unchanged and its
  arguments are checked by a built-in validator for the common JSON Schema
  keywords. Existing tools are unchanged.
- Extension tools can declare that metadata: `ExtensionTool` takes `exposure`,
  `namespace`, `annotations`, `output_schema` and `reaches_tools`, and a
  `ToolResult` can carry `structured_content` and `is_error` (the model sees
  the content as an error, without the handler raising).
  `ctx.get_all_tools()` lists every registered tool with its exposure,
  namespace, annotations and reach function, so a permission hook can decide
  by the annotations. A tool registered with exposure `hidden` is never
  declared to the model.
  The built-in `codemode` tool declares which tools a script may reach.
- Codemode scripts can call extension tools. A tool's exposure now decides how
  it is reached: `direct` and `model-only` tools are declared to the model
  when registered, `codemode` and `deferred` tools are not declared by being
  registered (a direct model call to an undeclared one is an unknown tool;
  activating one by name declares it) and are callable from scripts whenever
  they are registered, `model-only` and `hidden` tools are never callable from
  scripts. Nested calls to extension tools pass argument validation and the
  `tool_call` and `tool_result` hooks and are recorded in the parent result
  like calls to built-ins.
- Codemode scripts receive structured results. A call to a tool that declares
  an output schema returns its structured content as a Python value, also when
  the result is an error that carries one; every other call returns the tool's
  text, and a failed, blocked or invalid call raises `ToolError` with the
  tool's error text (`Tool "<name>" failed` when there is none). One tool
  reply may be up to 16 MiB of JSON, so a large structured result reaches the
  script whole; a larger one raises `ToolError`.
- Tool declarations for codemode: a renderer that writes a tool's JSON Schema
  as Python type text, for example
  `tools.search(args: {"query": str, "limit"?: float}) -> list[str]`, with
  property descriptions and the constraints a type cannot express (`@integer`,
  `@minimum 1`, `@default 20`) on comment lines. Scripts read the declarations
  in `ALL_TOOLS` and from `describe_tool`, and the model reads them in the
  `codemode` tool description.
- Tool search core: a tokenizer (camelCase and punctuation splits, stop
  words, folded plurals), the search text of a tool (name, description, schema
  descriptions and property names, namespace) and an Okapi BM25 ranker.
  `search_tools` in codemode scripts uses it; the `tool_search` tool follows.
- `tool_search`, an optional built-in tool that finds tools the model has not
  been given and loads them. It is registered inactive; enable it with
  `"defaultTools": ["+tool_search"]` or `--tools +tool_search`. It ranks the
  registered tools with exposure `codemode` or `deferred` that are not
  declared yet and activates the matches, so the next model request declares
  them. Loaded tools are recorded in the session like other tool changes and
  stay declared on that branch through `/tree`. Scripts cannot call it. See
  [tool search](docs/mcp.md#tool-search).
- `--tools` and `--exclude-tools` entries may be patterns in which `*` matches
  any characters (`--exclude-tools 'ext_*'`); `+name`/`-name` entries still
  take exact names. A `--tools` list of plain names now keeps MCP tools
  (`mcp__<server>__<tool>` and the resource tools) registered for codemode
  scripts and `tool_search` without declaring them; once an entry starts with
  `mcp__` the list filters them like other tools. `--exclude-tools 'mcp__*'`
  and `--no-tools` remove them. See
  [tool options](docs/usage.md#tool-options).
- Codemode scripts can find the tools they may call. `ALL_TOOLS` lists every
  callable tool as `{"name", "description"}` with the tool's declaration,
  `search_tools(query, limit=8, namespace=None)` ranks them, `describe_tool(name)`
  returns one tool's description and declaration, and
  `describe_namespace(name)` returns a namespace's description, instructions
  and tools. A tool is `tools.<identifier>` (characters an identifier cannot
  hold become `_`, a Python keyword gets a trailing `_`) and also
  `tools["<name>"]`, so a tool whose name starts with `_` or is not an
  identifier is now callable. The helpers run no tool and add no nested call.
  The message for a tool that does not exist changed: it reads
  `tools.<name> does not exist.`, names close matches (also ones that differ
  only in case or punctuation, or that contain the name) and points to
  `ALL_TOOLS` and `search_tools`. `ALL_TOOLS` and that message list tools in
  registration order, no longer alphabetically.
- `tool_result` hooks see a result's structured content
  (`ToolResultEvent.structured_content`) and decide it together with the
  content: `ToolResultTransform` takes an optional `structured_content`, and
  a transform without one drops the result's structured content, which may no
  longer match the replaced content.
- The `codemode` tool description now tells the model which tools a script
  can call. It explains what a call returns (a string, or a structured value
  when the tool's declaration says so), names `ALL_TOOLS`, `search_tools`,
  `describe_tool` and `describe_namespace` as the way to find unlisted tools,
  and lists callable tools under `Nested tools:`, grouped by namespace, each
  with its description, prompt guidelines and declaration. The listing has a
  budget of 3000 estimated tokens, the new `codemode.inlineBudget` setting;
  `deferred` tools are never listed, so the description stays the same while
  such tools register. The fixed sentence `Callable tools: read, ls, grep,
  find, write, edit, bash.` is gone.
- New setting `codemode.mode`. With `on` (default), every declared tool that
  scripts can call says so in its own description
  (``Codemode: `tools.read(args)` resolves to a string.``) and `codemode`
  lists the tools that are not declared by being registered. With `only`,
  `codemode` lists every callable tool and requests leave out the
  declarations of active built-in and extension tools, which stay callable
  from scripts; the system prompt then lists only the declared tools, and the
  hidden tools' guidelines appear in the `codemode` description.
  `describe_tool` and `ALL_TOOLS` carry a tool's prompt guidelines too. Both
  settings are read at session start and on `/reload`.

- MCP client library: `pipy_harness.native.mcp`
  holds the protocol client (handshake, requests with timeouts, progress and
  cancellation, paginated lists, tool calls and resource reads), the stdio
  and streamable HTTP transports, validation, loading and editing of
  `mcp.json` (user-level and trusted-project files, project overrides,
  exposure rules), and OAuth discovery. See
  [MCP](docs/mcp.md#configure-servers) for the file format and
  [the library](docs/mcp.md#what-exists-as-a-library) for the modules.
- MCP server connections: one connection per configured stdio server with
  the states `connecting`,
  `connected`, `disconnected`, `failed` and `closed`, a shared connect for
  concurrent callers, a reconnect on the next call after the server dropped,
  the tool list read again on `notifications/tools/list_changed`, and the
  end of the server's stderr in the error. `env` values resolve `${NAME}`
  and `!command`, a leading `~` in `command`, `args` and `cwd` is the home
  directory, and a relative `cwd` is relative to the session directory.
  Server log messages go to `mcp.log` in the config home, renamed to
  `mcp.log.1` above 5 MB. See
  [server connections](docs/mcp.md#server-connections).
- MCP server tools: a server's tool becomes a pipy tool named `mcp__<server>__<tool>` (other
  characters as `_`, a hash suffix for names over 64 characters and for
  tools whose names would collide) in the namespace `mcp__<server>`, with the
  server's schema, description and annotation hints. Scripts get the whole
  result, `{content, structuredContent?, isError?}`, and the tool declares
  that type. The model gets the text: resource links and binary resources as
  one-line notes (binary data is saved to a temp file), and text over 20 KB
  as its first and last 10 KB with the full text in a file readable by its
  owner only. Progress notifications become live output. See
  [server tools](docs/mcp.md#server-tools).
- MCP servers in a session. pipy reads `mcp.json` from the config home and,
  for a trusted project, `.pipy/mcp.json`, and starts connecting every
  enabled stdio server when a session starts, without waiting for them. A
  connected server's tools are registered as `mcp__<server>__<tool>` with the
  exposure the config gives them: `direct` tools are declared to the model,
  `codemode` (the default) and `deferred` tools are called from codemode
  scripts or loaded by `tool_search`. Calls pass the `tool_call` and `tool_result` hooks, and
  `ctx.get_all_tools()` reports the tools with their exposure, namespace and
  annotation hints. A changed tool list adds and removes tools while the
  session runs, `--tools` and `--exclude-tools` apply to them, `/reload`
  keeps them, and the session's end closes every server. `.pipy/mcp.json`
  now counts as a project resource that needs a trust decision.
  See [MCP](docs/mcp.md#quick-setup).
- Waiting for MCP servers that are still connecting. The first prompt of a
  session waits up to 10 seconds for servers with `direct` tools, so its
  first request declares them, and then says `MCP servers are still
  connecting; their tools become available once connected.` A codemode
  script waits for the servers its source names, or for every server when it
  uses `search_tools`, `describe_namespace`, `describe_tool` or `ALL_TOOLS`;
  `tool_search` waits for every server; a call to an MCP tool waits for its
  own server. Escape or an abort ends a wait and cancels the call. An
  extension tool gets the same wait by declaring `reaches_tools`. See
  [MCP](docs/mcp.md#waiting-for-servers).
- `just mcp-conformance` runs the official MCP client conformance suite
  (`@modelcontextprotocol/conformance@0.2.0-alpha.11`, protocol versions
  `2025-03-26`, `2025-06-18` and `2025-11-25`) against pipy's MCP client and
  compares every check with a committed baseline. All scenarios pass except
  the three that Pi's baseline lists as well (elicitation, `auth/basic-cimd`,
  and the simulated user of `auth/scope-retry-limit`). The recipe is manual:
  it needs Node's `npx` and the network. See
  [MCP conformance](docs/mcp.md#conformance).
- `quietStartup: "header"` keeps startup version and key hints while hiding
  details and resource listings. `--verbose` restores the full display.
- Codemode scripts can keep small JSON values between calls, as Pi's
  scripts do: `store(key, value)` keeps a value, `load(key)` reads it back
  (or `None`), and `store(key, None)` deletes it. A script that succeeds,
  also one that ends early with `sys.exit()`, appends its writes to the
  session as a `codemode-store` custom entry; a failed script keeps none.
  `load()` reads the entries on the current branch, so values follow
  resume, `/tree` and `/fork`. A value may have 256 Ki characters of JSON
  and the store 1 Mi characters in all. The codemode tool description names
  the two globals. See
  [keeping values between scripts](docs/codemode.md#keeping-values-between-scripts).

### Changed

- The built-in `read` tool processes an image file as Pi's `read` does when
  Pillow (the `images` extra) is installed: a BMP is converted to PNG and an
  image over 2000 x 2000 pixels or 4.5 MB of base64 is resized, with Pi's
  hint lines (`[Image converted from image/bmp to image/png.]`, `[Image:
  original WxH, displayed at wxh. ...]`) after `Read image file [<type>]`, in
  direct use and for `image(tools.read(...))` in a script. Before, such an
  image was omitted with `[Image omitted: ...]` even with Pillow. `read`
  follows `images.autoResize`: off, a PNG, JPEG, GIF or WebP file is attached
  as it is and a BMP is still converted. Without Pillow nothing changes: a
  fitting PNG, JPEG, GIF or WebP is attached and a larger image or a BMP gets
  Pi's omission text. See [codemode](docs/codemode.md).
- `search_tools(query, limit=8.0)` in a codemode script is accepted as
  `limit=8`, as Pi's `Number.isInteger` accepts it; `2.5`, `inf`, `nan`,
  `True` and strings still fail with `search_tools() limit must be a positive
  integer`.

- JSON and RPC tool events follow Pi's documented contract (`docs/json.md`):
  `tool_execution_end.result` and `tool_execution_update.partialResult` are
  now `{content, details}` objects instead of strings. `result.content` has
  the result's text and image blocks, as in the `toolResult` message,
  `result.details` the result's details when it has them, and
  `result.structuredContent` the tool's machine-readable output when it gave
  one. `tool_execution_end` carries `durationMs`, how long the tool itself
  ran (Pi times `execute()` alone, without the hooks), for nested calls
  (`parentToolCallId`) too; it is absent when the tool never started. A
  codemode call's `nestedCalls` record moved from a top-level `details` field
  of `tool_execution_end` into `result.details`. Clients that read `result`
  or `partialResult` as a string must read `content[].text` instead. See
  [automation and RPC](docs/automation-rpc.md).
- An MCP tool's progress is a partial result `{content: [{type: "text",
  text}], details: {server, tool}}` that replaces the previous one, as in Pi;
  it was a `"<text>\n"` chunk. `bash` output updates are
  `{content: [{type: "text", text: <chunk>}], details: {}}`, each chunk
  continuing the text before it. The terminal row is unchanged (progress
  lines are appended, plan DEV-18).
- `ctx.execute_tool(..., on_update=)` gives the callback a `ToolResult` per
  partial result (Pi `onUpdate`) instead of a text chunk: the MCP progress
  text with `details={server, tool}`, or the next chunk of `bash` output.
- An extension tool's input schema is now held to the rules of an MCP
  server's tool (`schema_mode="foreign"`) instead of pipy's strict subset, as
  Pi accepts any TypeBox schema: `number`, `null`, unions and `$ref` no longer
  disable the extension with `invalid_tool`. Arguments are converted and
  checked as an MCP tool's are; properties the schema does not declare are
  kept unless `additionalProperties` forbids them.
- When two extensions register the same tool name, the one that loaded first
  keeps it and the other extension stays enabled with its other
  contributions, as in Pi's `getAllRegisteredTools`; it was disabled with
  `duplicate_tool`, which is gone. This holds while the session runs too: a
  later-loaded extension's `register_tool` of that name is left out silently
  (it raised `ExtensionCapabilityError`), an earlier-loaded one's takes the
  name over.
- `api.register_tool_renderer` works while the session runs the extension
  (from `session_start`, a command, a hook or a tool), as Pi allows; it
  raised once loading had finished. The resolver joins its extension's other
  resolvers in load order and is asked for rows drawn from then on.
- MCP: another session in the same run (`/new`, `/resume`, `/fork`,
  `/clone`, `/import`, and the RPC and SDK `new_session`, `switch_session`,
  `fork`, `clone`) closes the MCP servers and starts them again from the
  `mcp.json` files, as Pi's MCP extension does on `session_shutdown` and
  `session_start`. The startup report and the first prompt's wait apply
  again; tools `tool_search` loaded and session-only `/mcp` changes to
  extension-registered servers do not carry over.
- MCP: the startup report and the report of a server an extension
  registered later are shown while the session waits for input, in the
  terminal editor (also behind the `/mcp` manager) and as an RPC `notify`
  request while no command runs, instead of with the next prompt.
- Print and JSON mode have no UI, as in Pi: the notices of extensions and of
  MCP (`ctx.ui.notify`, the status and messages of `/mcp`, the MCP startup
  report) are dropped there instead of written to stderr. An extension
  command that a prompt ran and that raised is Pi's extension error with the
  exception's text: `Extension error (command:<name>): <message>` on stderr
  in print and JSON mode, an `extension_error` object (`extensionPath`,
  `event`, `error`) on stdout in RPC mode; before it was `pipy: extension
  command /<name> failed (<error type>)` on stderr in all three. The report
  of an extension-registered MCP server that nothing connects is such an
  error too (event `register_mcp_server`). See
  [Extension errors](docs/automation-rpc.md#extension-errors).
- The prompt of `--print`, `--mode json` and an RPC `prompt` reaches the
  `input` hooks before skill and template expansion, as in Pi, so a hook
  sees `/review x` and may turn text into a template invocation; the
  session then stores the text the hooks left. A prompt template named like
  a built-in command of the interactive session (`/model`, `/tree`, ...) is
  expanded in such a prompt.
- RPC `steer` and `follow_up` whose text names an extension command (also
  `/mcp`) fail with Pi's `Extension command "/<name>" cannot be queued. Use
  prompt() or execute the command when not streaming.`; a skill command or
  prompt template in them, and in a `prompt` queued behind a run, is
  expanded when it is queued. A `prompt` that names an extension command
  during a run runs it at once and is answered `handled` (a failure is an
  `extension_error`).
- RPC `get_commands` lists the extension commands (with `/mcp`), prompt
  templates (with pipy's custom commands) and skills (`skill:<name>`); it
  answered an empty list before.
- SDK: `ProductSession.submit()` handles its text as Pi's SDK
  `session.prompt()` does: an extension command (also `/mcp`, so
  `McpOptions.open_url` is reached by `/mcp login`) runs and starts no run,
  the `input` hooks may change or handle the text, and skill commands and
  prompt templates are expanded. Built-in commands of the interactive
  session and `!` stay text for the model. Before, `submit()` sent every
  text to the model unchanged.
- MCP: a screen of the `/mcp` manager that is taller than the terminal
  shows a server's details and error, a status message or the
  authorization URL through a window that PageUp and PageDown
  (`tui.select.pageUp`/`pageDown`) scroll, with a line such as
  `… lines 1-9 of 26, pageup/pagedown to scroll`. Before, the text was cut
  short and the rest could not be read.
- MCP: the authorization URL on the `/mcp` sign-in screen and a new
  `Cmd+click to open` hint (`Ctrl+click` outside macOS) are OSC 8
  hyperlinks to the URL, as Pi's `AuthUrlComponent` draws them, so a
  terminal that supports links opens a wrapped URL on a click.
- Codemode: a codemode row shows the script's output as Pi's codemode
  renderer does: without the `Script completed`/`Wall time`/`Output:`
  header, collapsed to the first five wrapped lines with `... (N more
  lines, ctrl+o to expand)`, and `Full output: <path>` when the output was
  spilled to a file. Before, codemode had the generic result preview.
- Export: the HTML page shows the nested calls of a tool result (a codemode
  script's calls) as Pi's page does: `Nested calls: <n>`, and on a click one
  line per call with its status, arguments, duration and error.
- `ToolRenderContext.show_images` is the `terminal.showImages` setting, as
  Pi's `showImages`; it was always false. A change of the setting draws the
  rows of extension renderers again.
- An HTTP MCP server that redirects to another host or port is followed, as
  in Pi. The OAuth or provider token and a configured `Authorization`,
  `Proxy-Authorization`, `Cookie` or `Host` header are not sent to the other
  origin; other configured headers are. Before, such a redirect failed with
  the redirect's status.
- An HTTP MCP server may answer with a JSON body of any size. The 16 MiB
  limit now applies to one event of an event stream, as in Pi; a larger
  JSON answer failed with `MCP HTTP message exceeds <n> bytes` before.
- A server's changed tool list is read again at once, also while the server
  is slow to answer a listing of its resources (and the other way round).
  Before, the two were read one after the other.
- MCP server URLs and OAuth URLs are read as Pi reads them: shortened and
  hex or decimal IPv4 hosts (`http://127.1:8080/mcp`), percent-escapes in
  the host, and missing or extra slashes and backslashes after the scheme
  are accepted, where they were configuration errors before. `127.1` and
  the other spellings of `127.0.0.1` count as loopback hosts.
- An `mcp.json` with bytes that are not UTF-8 inside a string loads, with
  replacement characters there, as in Pi; the whole file was rejected
  before.
- `pipy mcp add --oauth-callback-port` and `pipy mcp login --timeout` read
  their number as Pi does with JavaScript's `Number()`, so `0x50`, `0o17`,
  `0b1` and `1e3` are numbers.
- An MCP server with `"auth": {"provider": "<provider>"}` is also sent the
  `openai-codex` login, the key given with `--api-key` (when the provider is
  that of the model the session starts with) and the `apiKey` that
  `models.json` configures for the provider, as a model request to that
  provider would use them; these sources sent no token before.
- An MCP server with `"auth": {"provider": "<provider>"}` is sent a stored
  login refreshed first when it expires within five minutes, for every
  provider Pi refreshes: `anthropic` and `openai-codex` logins in
  `auth.json` are refreshed and saved under the lock of `auth.json`, and a
  provider an extension registered is refreshed through its
  `oauth.refresh_token` and sent its `oauth.get_api_key` token. Before,
  such logins were sent as stored, expired or not. The stored login of a
  provider without an OAuth sign-in is no token, as in Pi.
- OAuth credentials that an older Pi stored in `mcp-auth.json` under the
  server URL alone are used: the first server with that URL takes them
  over, and `/mcp logout` deletes them. Before, they were ignored.
- `--tools ''` disables every tool, MCP tools included, as `--no-tools`
  does; before, an empty list was the same as no list.
- `--no-tools` and `--no-builtin-tools` only empty the default selection,
  as in Pi: `pipy --no-tools --tools tool_search` declares `tool_search`,
  `--no-builtin-tools --tools read` declares `read`, and
  `--no-builtin-tools --tools +read` activates `read` and keeps the
  extension tools. Under `--no-builtin-tools` the built-in tools stay
  registered (an extension can select one), and `codemode` and
  `tool_search` stay available for MCP.
- `codemode` and `tool_search` are the built-in extensions
  `builtin:codemode` and `builtin:tool-search`, as in Pi:
  `"extensions": ["-builtin:tool-search"]`, `pipy config disable builtin
  codemode` and `--no-extensions` remove the tools, `--extension
  builtin:<name>` loads one for a run, and `/reload` reads the entries again.
- A tool that `tool_search` loaded is no longer declared again on its own
  when its server is disabled and enabled again, or stops offering it and
  offers it again; it has to be loaded again, as in Pi.
- `api.register_tool` works while the session runs the extension, as Pi's
  `registerTool` does: a command can add a tool or register one of its
  tools again, with `exposure="hidden"` to withdraw it, and the next request
  has the change. Before, registration closed when `activate(api)`
  returned, and a withdrawal needed `/reload`. A name another extension
  registered, a reserved name, and an `api` kept from before a `/reload` or
  after the session ended raise `ExtensionCapabilityError`. See
  [Withdrawing a tool](docs/extension-api.md#extension-tool-metadata).

- A tool renderer resolver can call and wrap what `next()` gives for a
  built-in or MCP tool, as Pi's renderers can be: its `render_call(ctx)`
  and `render_result(ctx)` now return pipy's own lines of the row instead
  of being `None`. See [Tool rendering](docs/extension-api.md#tool-rendering).

- A tool's `prepare_loadout` function runs once for each new set of active
  and registered tools, and pipy keeps its result, as Pi does: the
  descriptions, the hidden declarations and the system prompt's tool list
  come from the same call. Before, pipy called it every time it read the
  declarations, several times per turn. See
  [Presenting the loadout](docs/extension-api.md#presenting-the-loadout).

- `event.system_prompt` of a `before_agent_start` handler is rendered from
  the sections whenever it is read, as Pi's getter is, so a handler sees its
  own section changes too; before, it was the text taken when the handler
  was called. See [the prompt and its sections](docs/extension-api.md#prompt-sections).

- `tool_call` hooks run after argument validation, as in Pi: a call whose
  arguments do not fit the tool's schema gets the validation error and
  reaches no `tool_call` or `tool_result` hook (before, the hooks saw the
  raw arguments and the tool rejected them afterwards). `event.input` is the
  validated arguments, and a hook that changes it in place changes what the
  tool runs with; before, changes were ignored. The transcript keeps the
  arguments the model sent. `ToolCallEvent` gains `tool_call_id` and
  `parent_tool_call_id`, so a hook can tell a codemode script's call
  (`<parent id>/<n>`) and its calling tool. `dispatch_tool_call_hooks` takes
  `tool_call_id` (and `parent_tool_call_id`). See
  [Call hooks](docs/extension-api.md#extension-tool-metadata).
- A `tool_result` hook is shown the whole result and may change all of it,
  as in Pi: `ToolResultEvent` gains `blocks` (the text and image blocks of a
  result with images), `details`, `input`, `tool_call_id` and
  `parent_tool_call_id`, and `ToolResultTransform` gains `blocks`,
  `is_error` and `details`; its `content` is now optional. A transform's
  `content` replaces the whole content, images included, so `content=""`
  removes the image of an image-only result (before, images were kept
  whenever the text stayed the same). `dispatch_tool_result_hooks` takes the
  new fields and returns `None` when no hook changed the result. See
  [Result hooks](docs/extension-api.md#extension-tool-metadata).
- Arguments of MCP tools, `tool_search` and other tools with a foreign schema
  are converted before validation as Pi converts them: an optional argument
  sent as `null` is dropped when its schema does not allow `null`, and
  primitives are converted to the type the schema asks for (`"5"` to `5`,
  `"true"` to `true`, a number to a string, `null` to `0`, `false` or `""`).
  Before, such arguments were refused. See
  [MCP tools](docs/mcp.md).
- The arguments of such tools are now checked for the JSON Schema keywords
  and formats Pi checks: `multipleOf`, `uniqueItems`, `contains`,
  `minContains`, `maxContains`, `additionalItems`, `minProperties`,
  `maxProperties`, `propertyNames`, `patternProperties`, `dependentRequired`,
  `dependentSchemas`, `dependencies`, `not`, `if`/`then`/`else`, `oneOf` as
  exactly one match, and `format` (dates, times, e-mail addresses, IP
  addresses, URIs, UUIDs and more, as Pi's TypeBox checks them). Before,
  these were left to the server.
- The prompt of `--print` and `--mode json` and an RPC `prompt` are handled
  as Pi's `prompt` handles them. A text that names an extension command runs
  it and starts no run; `/mcp` is such a command. `/skill:<name>` and prompt
  templates are expanded. Everything else
  goes to the model as it stands. Before, print and JSON mode read the
  prompt as a typed line, so the built-in commands (`/model`, `/exit`, ...)
  and the `!` shortcut ran there and an unknown `/name` ended with a notice
  and no answer, while an RPC prompt went to the model without any of this.
  The RPC `prompt` response now carries `data.disposition` (`started`,
  `queued` or `handled`), `steer` and `follow_up` answer `queued`, and JSON
  mode writes no `agent_settled` for a prompt that started no run. See
  [Commands in a prompt](docs/automation-rpc.md#commands-in-a-prompt).
- RPC mode sends notices to the client as `extension_ui_request` objects
  with `method: "notify"` instead of writing them to stderr: an extension's
  `ctx.ui.notify`, the status and messages of `/mcp` and the MCP startup
  report. `/mcp` asks an RPC client to choose a server with a
  `select` request, and `/mcp login` now works there: it sends the
  authorization URL, opens the browser and asks for the pasted redirect URL
  with an `input` request. `extension_ui_response` lines are no longer
  ignored. See
  [the channel](docs/automation-rpc.md#extension-ui-requestresponse-channel).
- The tools a codemode script can call are fixed when the script starts, from
  the tools registered and active at that moment, instead of from the request
  that declared `codemode`. A script therefore also reaches an extension tool
  that replaces a built-in, and a selection made by a `tool_call` hook on the
  `codemode` call applies to that script. An extension tool registered with
  exposure `codemode` or `deferred` is no longer declared to the model unless
  it is activated by name.
- Python codemode guidance now includes an executable small-file read/filter
  example, explains direct string processing and recommends filtering before
  emitting a compact answer. This guides model behavior without enforcing a
  one-script limit or changing tool authority, retries or output budgets.
- `ctx.execute_tool` returns a `ToolCallOutcome`, Pi's outcome object: the
  call (`tool_call.id`, `name`, `arguments`), the `result`, `is_error` and
  `duration_ms`. It returned the result alone, without its images. A
  `ToolResult` can now hold images as `blocks` of `TextContent` and
  `ImageContent` (exported from `pipy_harness.extensions`), so a tool can
  return the screenshot of an MCP tool it called. See
  [running other tools](docs/extension-api.md#running-other-tools).
- Nested tool calls work as in Pi at every depth: a tool called through
  `ctx.execute_tool` or from a codemode script can run tools itself, also
  itself, and its calls are part of the record of the tool the model
  called, with ids `<calling id>/<n>`. Calls an extension tool makes from
  several threads run at the same time instead of one after the other; the
  session thread runs their validation and handlers, and each counts
  against the tool budget from the moment it is admitted. A handler now
  runs on a worker thread also in a session that cannot be interrupted, so
  calls from threads it starts are served there too instead of refused.
  When the tool the model called returns, its nested calls that still run
  are cancelled. See
  [running other tools](docs/extension-api.md#running-other-tools).
- `ctx.execute_tool` takes Pi's options: `signal` (a `threading.Event`)
  cancels one nested call, by default with the calling tool's own signal,
  now `ctx.signal`; `on_update` receives the live output of the nested tool
  (`ctx.executeTool(name, args, {"signal": ..., "onUpdate": ...})`). Nested
  calls now also emit their live output, such as an MCP server's progress
  or a `bash` command's output, as `tool_execution_update` events with
  `parentToolCallId` in JSON and RPC mode; it was dropped.
- A codemode script can run tool calls at the same time, as Pi's scripts do
  with `Promise.all`: `tools.<name>.submit(args)` starts a call and returns
  at once, and its `.result()` waits for it and returns or raises what
  `tools.<name>(args)` would. The calls start in the order the script
  submits them, so their ids, the tool budget, the hooks and the record
  follow that order; a call still running when the script ends is
  cancelled. At most 16 calls of a script wait for their result at once.
  The record of nested calls (`nestedCalls`) now lists every tool's calls in
  the order they began instead of the order they ended, and a record with
  an unfinished call says `complete: false`. The codemode guideline now
  names `submit()` for independent calls. See
  [calls at the same time](docs/codemode.md#calls-at-the-same-time).

### Fixed

- Closing an HTTP MCP server, a request timeout and a cancelled call end a
  request that is still connecting or in its TLS handshake at once. Before,
  its thread and socket stayed for up to 30 seconds.
- Signing in to an MCP server works when the authorization server answers a
  client registration or a token request with a 307 or 308 redirect: the
  request is sent again where it moved, as in Pi. Before, it failed with the
  redirect's status.
- Answers of an authorization server that are compressed with gzip or
  deflate are read. Before, such metadata, registration and token answers
  failed as invalid JSON.
- Signing in to an MCP server whose configured URL has a non-ASCII or
  otherwise unencoded path character (`https://example.com/café/mcp`) works
  when the server's protected resource metadata writes that path
  percent-encoded. Before, the sign-in stopped with `Protected resource ...
  does not match MCP server ...`.
- A codemode script may emit Pi's 16,777,216 characters of output, text and
  image data together, or 100,000 `text()`, `image()` and print calls; longer
  output is cut to the result's budget and saved whole to the full-output
  file. Before, a script whose text passed 1 MiB, or one line of output
  passed 1 MiB, failed. Past the limits the script fails with Pi's message.

- A tool whose structured result is `null` now gives a codemode script
  `None`, also for a failed result, as Pi does; before, the script received
  the tool's text or a `ToolError`. A result without structured content is
  marked with the new `NO_STRUCTURED_CONTENT` (from
  `pipy_harness.extensions`), which is now the default of
  `ToolResult.structured_content` and `ToolResultTransform.structured_content`
  and what a `tool_result` handler sees in `ToolResultEvent` when there is
  none; `None` there means `null`.

- Compaction and branch summaries retry eligible failures from every provider and
  show dedicated retry countdowns and bounded JSON/RPC status events. Private
  summary content, usage and provider failure text remain suppressed. Generic
  abort/Escape cancels them; abort_retry continues to target ordinary retries.

- The footer restores context usage from the active branch after resume or branch
  changes and estimates trailing message characters separately. Failed/aborted
  usage no longer anchors the meter, and model changes use the current window.
  Compacted contexts keep a conservative full estimate including system and
  summary state once; session token and cost totals stay unchanged.

- Ordinary retries preserve every failed partial assistant in durable history and
  expose per-attempt message/turn/agent boundaries. SDK results and RPC claims
  settle once per accepted prompt; cancelling backoff retains the last error
  without an extra aborted message or `Operation aborted` row. Active-attempt
  cancellation still preserves its aborted partial. Failed messages remain
  excluded from provider replay, and retry countdowns avoid duplicate error rows.

- Stopped partial tool calls now save recoverable JSON argument values, matching
  Pi's streaming parser for finite values. Ordinary call validation is unchanged;
  unrecoverable or non-finite stopped arguments become `{}`. Existing saved
  entries are never rewritten.
- Interrupted tool execution now settles all paired results and records an empty
  aborted assistant, without another provider request. Private compaction and
  branch summaries include labelled unfinished stopped text while excluding its
  thinking and unexecuted calls; ordinary requests still skip stopped messages.

- Live stopped provider answers now show their partial output followed by
  `Error: …` or `Operation aborted`. Responses without tool calls match resumed
  history. Stopped partial tool calls now appear as failed rows live and on
  resume, without executing them or adding synthetic tool results to history. Steering, local-command and provider cancellations show
  the abort marker too. Terminal provider errors replace the generic failure
  diagnostic; retry notices, saved entries and continuation are unchanged.

- Oversized compaction summary requests now recover through budget-admitted
  incomplete excerpts and bounded accepted summary text. A later fitting prompt
  can continue without `/new` after huge pasted text, tool arguments or aggregate
  history pressure. The notice and durable summary disclose omitted information;
  original transcript entries remain intact. Oversized new input still refuses,
  and cancellation, stale work and generation failure publish no compaction.

- Codemode preserves the original canonical pipeline exception when secondary
  parent evidence, completion events or release-status publication fail. Cleanup
  closes the nested service and releases its local reservation with child counters
  retained; evidence/status failures without an active exception still propagate.

- Provider errors saying "Selected model is at capacity" now use the existing
  bounded retry policy; quota and billing exhaustion remain non-retryable.
- Compaction records `tokensBefore` as Pi's context-token estimate instead of
  the byte size of the dropped history, so `Compacted from N tokens` matches
  the footer's context figure. Existing session entries keep their old value.
- The terminal shows Pi's `Compacting context... (escape to cancel)` loader
  during manual `/compact` and `Auto-compacting...` during automatic
  compaction; previously nothing was shown while the summary ran.

- SIGTERM and SIGHUP end pipy like an exit. Until now either signal ended
  the process at once: a terminal session left the terminal in raw mode, and
  an MCP server process that does not exit when its stdin closes was left
  running without a parent. The command is now unwound as for any other
  exit, so the terminal is restored and the servers get their close
  sequence, and pipy exits with status 143 or 129, in every mode and for
  `pipy mcp`. In RPC mode the signal aborts the run in flight instead of
  waiting for it. The exit hook for server processes that are still running
  when pipy exits now sends SIGKILL to a server that is not gone when its
  close time is over, and waits for it; it sent SIGTERM only. See
  [when pipy ends](docs/mcp.md#when-pipy-ends).
- The test suite no longer leaves fake MCP server processes behind. A test
  that failed, or a run that was killed, could leave a scripted server that
  ignores the end of its stdin running for good. The fake server now ends
  itself five minutes after its parent process ended, the PTY tests end the
  servers of a session they had to kill, and a module whose tests leave a
  fake server running fails with the command line of the process.
- Closing a stdio MCP server now also ends a child in the server's process
  group that keeps the server's stdout or stderr open and ignores SIGTERM.
  The close used to end as soon as the server exited, so such a child was
  sent one SIGTERM and kept running; now the group gets SIGTERM and then
  SIGKILL until the pipes close, as with Pi. See
  [when pipy ends](docs/mcp.md#when-pipy-ends).
- An extension tool, command or shortcut handler written as `async def` now
  runs, as Pi awaits them. A tool returned the text of the unstarted
  coroutine as its successful result, also when another tool called it, and
  a command or shortcut did nothing. See
  [tools](docs/extension-api.md#tools).

- Documentation builds and previews exclude local audit files and parity-loop
  run bundles before Zensical reads them. Strict builds validate references,
  links and anchors, preserve ordinary untracked documentation, and replace
  generated output so stale scratch artifacts cannot survive a successful build.

### Documentation

- The MCP parity plan, its acceptance note and the backlog record that
  the final goal check (plan section 7, Q17) found MCP and codemode tool
  reach at parity with Pi in all six areas, with no finding, and that the
  owner approved the merge to `main`. The row counts are unchanged; the
  live evidence and the follow-ons stay in the backlog. See the
  [acceptance note](docs/acceptance/2026-10-13-mcp-parity.md).
- The MCP parity plan, its acceptance note and the backlog now hold the
  goal-check tracks L and N and the owner decisions of 2026-10-14: no
  default OAuth Client ID Metadata Document (dynamic client registration
  stays the default, CIMD is opt-in through `oauth.clientMetadataBaseUrl`),
  stdio servers on Windows, the Radius offer after a login and codemode
  `models.*` outside the parity goal, inline terminal images in scope, and
  Pillow as the optional `images` extra.
- The [MCP page](docs/mcp.md) is in the order a user needs it: quick setup,
  configuration, migrating from another client, `/mcp` and the other ways
  to look after servers in a session, the `pipy mcp` commands, OAuth, tool
  exposure, tool search, resources, permissions, extensions and the SDK,
  then the reference sections. New in it: a table for taking a
  configuration over from Claude, Cursor, VS Code, Codex, OpenCode and Pi,
  a list for diagnosing a server that does not connect, an introduction to
  signing in with OAuth, a `toolExposure` example, and a list of what pipy
  does not support. The MCP commands in the [CLI reference](docs/usage.md)
  now list `login` and `logout`.

- The MCP feature has an
  [acceptance note](docs/acceptance/2026-10-13-mcp-parity.md): for each of
  the 292 behaviours of the
  [parity plan](docs/specs/2026-10-12-mcp-parity-plan.md) it names the tests
  that pin it and where pipy differs from Pi. All are closed, 127 of them
  with a recorded limit or deviation. The plan lists every recorded
  difference from Pi (section 3.1) and the result (section 3.2); the
  [backlog](docs/backlog.md) says what is open, which is live use with a
  real server and model.

- The MCP parity plan records the owner's decision on the second goal
  check (section 7, Q11): the findings its triage neither fixes nor
  refutes are accepted as the deviations it names, ten of them as the new
  rows DEV-69 to DEV-78.
  DEV-30 no longer says that Pi's SDK restores a resumed session's tools
  (Pi's `createAgentSession` passes the initial tool names, as its CLI
  does). The missing `before_agent_start` result fields (`systemPrompt`,
  `message`) are an extension-API item in the [backlog](docs/backlog.md).
  Since R2 and R5 were merged, the plan records the error text of a raising
  `prepare_arguments` as the proposal DEV-79 (EXT-01 is closed with that
  limit), and
  DEV-10, DEV-18, DEV-26, DEV-45, DEV-48 and DEV-59 say what R2 and R5
  changed, including that a resolver a `session_start` handler registers
  is added again in each new session of the run.

- The MCP parity plan records the decision on the third goal check
  (section 7, Q12): the findings its triage neither fixes nor refutes are
  accepted as the deviations it names, six of them as the new rows DEV-80 to
  DEV-85 (the codemode output cap, an `mcp.json` with a number JSON cannot
  write back, no proxy support, stored API keys without Pi's `${VAR}`
  templates, extensions kept across a session replacement, no
  `constrained_sampling`). DEV-36, DEV-48, DEV-54 and DEV-67 say more
  precisely where pipy differs, Q12 corrects Q11's refutation of the stdio
  child leak on macOS before Python 3.13 (a fix slice of that triage closes
  the leak), and 173 rows are closed with a
  test and 119 with a limit. [MCP](docs/mcp.md) lists the missing proxy
  support, and the [backlog](docs/backlog.md) has proxy support, Pi's
  `auth.json` config values and `constrained_sampling` as follow-ons.

- The MCP parity plan, its acceptance note and the backlog hold the fix
  slices T3, T4 and T6 of the third goal check: DEV-27, DEV-31, DEV-32,
  DEV-45 and DEV-68 say what they changed, the rows they touch name their
  new tests, and the backlog's READ-IMG item is left with what `read` does
  without Pillow. No row changes its state: 173 rows are closed with a test
  and 119 with a limit.

- The MCP parity plan records the decision on the fourth goal check
  (section 7, Q13): the findings its triage neither fixes nor refutes are
  accepted as the deviations it names, eight of them as the new rows DEV-86
  to DEV-93 (the text-only render context, one-at-a-time tool calls of a
  model response, text-only partial results, the text-only `input` event,
  ignored `send_user_message` options, trailing newlines of a print or JSON
  prompt, object-only `details`, silent `input` handler errors). DEV-33 now
  says that `get_all_tools()` lists `codemode` only once something
  registers it, DEV-48 says that its callable snapshot is the plan's rule
  that the owner accepted (Q6), and 170 rows are closed with a test and 122
  with a limit. The [backlog](docs/backlog.md) has the extension-API items
  as follow-ons and points the parallel tools deferral at DEV-87.

- The MCP parity plan, its acceptance note and the backlog hold the fix
  slices V1 to V3 of the fourth goal check: DEV-32, DEV-33, DEV-44, DEV-45
  and DEV-55 say what they changed, DEV-79 (the error text of a raising
  `prepare_arguments`) is closed, and the rows they touch name their new
  tests. EXT-01 keeps its limit through DEV-85, so no row changes its state:
  170 rows are closed with a test and 122 with a limit.

- The MCP parity plan records the decision on the fifth goal check
  (section 7, Q14): the findings its triage neither fixes nor refutes are
  accepted as the deviations it names. `pipy mcp add --local` with a trust
  store that cannot be read writes nothing, where Pi writes the entry and
  then stops with an error (DEV-57, which now also names CMDL-06);
  `pipy mcp logout` waits for a running token refresh, at most 25 s, so that
  a refresh that ends in that time cannot store the tokens again (DEV-55); `ctx.get_all_tools()`
  has no `sourceInfo` (DEV-25). The [backlog](docs/backlog.md) has tool
  source metadata as an extension-API follow-on. When the fix slice X2 was
  merged, Q14 also accepted the new DEV-94: pipy orders the MCP tools of
  different servers by the configuration, where Pi's order follows the
  order in which the servers connected. With it 168 rows are closed with a
  test and 124 with a limit.

- The MCP parity plan records the decision on the sixth goal check
  (section 7, Q15): its eight findings are fixed by the slices Y1 to Y3 and
  none is accepted. DEV-31 and DEV-52 say what Y2 and Y1 changed, DEV-66
  says that the nested calls start codemode's result part, and the new
  DEV-95, a proposal for the final judgement, records that a wrapped `edit`
  row has no diff preview under other arguments and that its call part
  shows the diff of the row's own result. The rows the slices touch name
  their new tests in the acceptance note; no row changes its state: 168
  rows are closed with a test and 124 with a limit.

- The MCP parity plan records the decision on the seventh goal check
  (section 7, Q16): the two findings its triage does not fix are accepted
  as deviations. The live codemode row keeps the latest 256 nested calls of
  a script, where Pi's renderer keeps every call (the new DEV-96, which
  names RND-04 and CMD-26); the diff preview of a wrapped `edit` row comes
  from the row's own arguments and result (DEV-95, a proposal until now).
  The [backlog](docs/backlog.md) has both as follow-ons. CMD-26 is closed
  with a limit, so 167 rows are closed with a test and 125 with a limit.

- The MCP parity plan records the decision on the eighth goal check
  (section 7, Q17): seven of its findings are accepted as deviations, and
  the goal checks end with one final check after the fix slices of that
  triage. That check asks for each area whether the goal is reached, with
  no Critical gap and no gap a user would notice in normal use; edge cases
  it still finds go to the [backlog](docs/backlog.md). The new DEV-97 to
  DEV-101 record that an SSE `retry` field of more than 18 digits is
  ignored, that an exposure change taking an excluded tool out of the
  selection drops the pending tool restorations, that a tool result whose
  blocks are all text reaches the hooks as its joined text, that an empty
  `prepare_loadout` description is ignored, and that `api.on()` works only
  while an extension loads. DEV-36 (a further hash suffix for a name that
  belongs to another tool) and DEV-37 (an exposure change while a server
  is not connected) are accepted in those parts. The backlog has the
  follow-ons. HTP-07 and EXP-09 are closed with a limit, so 165 rows are
  closed with a test and 127 with a limit.

- Added a reproducible synthetic context-elision evaluation comparing unchanged
  automatic compaction with an evaluation-only stale read-output projection;
  includes all provider requests, fact retention, rereads and cache counters.

- Added user-facing codemode installation, scripting, module capabilities,
  limits and authority documentation, with linked developer reproduction and
  A–K acceptance evidence. Retained the isolation spike and deferred scope.

- Audited Pi 1.0 selected surfaces, reran the catalog/session comparison gates,
  and refreshed the pinned upstream system-prompt documentation topics.

- Recorded the upstream parity scope: retain the established Pi coding agent
  as the daily-use reference, queue a drift audit, and track MCP/codemode and
  the experimental durable runtime as separate scope decisions.

- Added a provider API capability and evidence matrix distinguishing incremental
  text/thinking, buffered completion, final tool calls, cancellation proofs,
  request-shape fixtures and dated live observations. Local transport tests
  verify Codex callbacks before terminal data and ordinary buffered fallback.

- Corrected stale architecture and harness status: the repository is public,
  native product sessions are separate from the metadata archive, SDK/JSON/RPC
  surfaces are shipped, and documentation is hosted. Historical bootstrap
  rationale retains its existing headings and links to current contracts.

## [0.3.0] - 2026-09-30

### Highlights

A curated summary of the largest changes in this release; the full entries
follow.

- GPT-6.1 Sol (`gpt-6.1-sol`) on `openai`, `openai-codex` and
  `github-copilot`, and the new default `openai-codex` model.
- Sign in with ChatGPT for the `openai` provider: `/login openai` and
  `/logout openai`.
- Sessions survive trouble: provider retries follow Pi's auto-retry and are
  shown with a countdown, an aborted or failed turn keeps its partial answer,
  a tool result that overflows the context no longer wedges the session, and a
  resumed session redraws its conversation and gets its model and thinking
  level back.
- Every assistant message stores its usage and cost, so totals survive resume
  and model switches; `/session` prints Pi's `Session Info` with a `Cost`
  section.
- The built-in tools behave like Pi's: `read`, `ls`, `grep`, `find`, `write`,
  `edit` and `@file` resolve paths like Pi with no deny list, `bash`, `grep`,
  `find` and `ls` cut long output with Pi's limits and notices, `edit` takes
  Pi's `edits[]`, and `bash` reports a non-zero exit as an error.
- Tool calls are drawn as Pi's tool boxes with Pi's rows and colours, and the
  default `pi` theme uses Pi's `dark` palette.
- The `/model`, `/thinking` and `/resume` selectors and the slash menu use Pi's
  fuzzy search.
- The system prompt is Pi's (`<tools>`, `<rules>`, `<docs>` sections) and is
  recorded in the transcript as system messages; models that accept
  mid-conversation system messages get prompt and tool changes in place, so
  the prompt cache prefix survives them, and Anthropic models that support it
  get the thinking effort mid-conversation.
- Reasoning is stored and replayed like Pi: the same model gets its thinking
  back with provider signatures and encrypted reasoning, other models get it
  as plain text.
- Skills run as Pi's `/skill:<name> [args]` commands.

### Breaking

- `--read-root`, `PIPY_READ_ROOTS` and the automatic reference roots are
  removed; the read tools open any path, including `.git`, ignored files and
  paths outside the workspace.
- `/skill <name>` and the bare `/skill` listing are removed; use
  `/skill:<name>`. `enableSkillCommands: false` only hides the commands. A
  skill without a frontmatter `name` is named after its directory.
- `edit` takes `edits[]` of `{oldText, newText}`; `old_string`, `new_string`
  and `replace_all` are gone.
- `write` overwrites an existing file and creates parent directories.
- `bash` reports a non-zero exit as an error ending in `Command exited with
  code N`; the `exit code: N` / `[output]` framing is gone.
- `grep`, `find` and `ls` take Pi's parameters and return Pi's output format.
- `/model <ref>` switches only on an exact reference and only for the current
  session; other text opens the model selector.

### Added

- The default system prompt is now Pi's (`system-prompt.ts`, with pipy's name
  and docs): a short preamble, then Pi's `<tools>` (one line per active tool),
  `<rules>` (the rules the active tools add, then Pi's two defaults) and
  `<docs>` (where pipy's README, docs and examples live) sections, before the
  appended prompt, context files, skills and working directory. The tool,
  rule and skill sections are rebuilt for each prompt from the active tools,
  so an extension that changes them sends only the changed sections. A
  replaced prompt drops the three new sections, as in Pi.
- Skills run as Pi's `/skill:<name> [args]` commands: the skill file is read
  again and sent as Pi's `<skill>` block, followed by the text after the name,
  and the TUI draws it as Pi's collapsible `[skill] <name> (ctrl+o to expand)`
  box, live and on resume. The slash menu lists the skills with their
  descriptions.
- Skill frontmatter `disable-model-invocation: true` keeps a skill out of the
  system prompt; it still runs through its command. Without `read`, skills are
  advertised for loading with `bash`, as in Pi.

- Reasoning is stored and replayed like Pi (DF1-F2b, DF1-F6b, SYS1c): an
  assistant message keeps Pi's ordered content -- thinking blocks with their
  provider signatures, text with its Responses message id and phase, tool
  calls with Gemini thought signatures -- and the adapter API that answered.
  The same model gets it back exactly (Anthropic signed and redacted
  thinking, OpenAI Responses/Azure/Codex reasoning items with
  `encrypted_content`, message ids and `fc_` item ids, Gemini
  `thoughtSignature`s); any other model gets the thinking as plain text, as
  Pi's `transformMessages` does. Azure now asks for the encrypted reasoning
  (`include`, `summary: "auto"`). Gemini thought parts are no longer part of
  the answer text. An aborted or failed turn keeps the thinking and tool
  calls streamed so far and Anthropic's `providerThinkingLevel`. The TUI
  draws thinking in block order for answers that were not streamed and on
  resume, and Ctrl+T (or a new hidden-thinking label) re-renders every
  thinking row, like Pi; the pipy-only deferred-reasoning buffer is gone.
  JSON/RPC messages carry the thinking blocks and `api`, and partials stream
  `thinking_delta` events.

- Sign in with ChatGPT for the `openai` provider, as in Pi (Pi `02eed88fd`):
  `/login openai` opens the OpenAI sign-in page, takes the browser callback
  when you press Enter (or a pasted redirect URL) and stores the subscription
  login in the auth store; `/logout openai` removes it. A new global
  `deviceId` setting names the installation. The access token is refreshed
  before a request and the rotated refresh token is written back under the
  auth-file lock. Requests with a ChatGPT token omit `prompt_cache_retention`
  and `prompt_cache_options`, the footer cost shows `(sub)`, the
  `subscription_sharing_usage_limit_exceeded` error links to ChatGPT usage and
  is not retried, and `subscription_sharing_usage_unavailable` /
  `subscription_sharing_user_unavailable` are retried.

- GPT-6.1 Sol (`gpt-6.1-sol`) on the `openai`, `openai-codex` and
  `github-copilot` providers, with Pi's rows (MC6, Pi `12c416e1a`): $2 input,
  $0.10 cached input, $2.50 cache writes, $10 output, 272K context (1.05M on
  Copilot) and 128K output. It rejects `reasoning.effort: "none"`, so thinking
  cannot be switched off: `off` is not offered and no off-state `reasoning`
  field is sent. A carried `off` clamps to the lowest offered level: `minimal`
  on Codex (sent as `low`), `low` on OpenAI and Copilot. Levels run up to
  `max`.
- Models that accept mid-conversation system messages now receive them in
  place, as in Pi (SYS1b, Pi `9e05370b2`). The leading prompt stays fixed
  and later prompt or tool changes arrive where they happened, so the prompt
  cache prefix survives them. The catalog carries Pi's compat flags:
  `supportsMidConvoSystemMessages` (OpenAI and Codex GPT-5.4 and later,
  Anthropic Opus 4.8/5/5.5, Sonnet 5.5, Fable 5/5.1, and the GitHub Copilot
  rows that already carried Pi's flag, including through its per-request
  OAuth token refresh),
  `supportsAdditionalTools`, `supportsMidConvoToolChanges` and
  `supportsDeveloperRole`; `supportsMidConvoToolAdditions` is honoured from
  `models.json`. Per API:
  - OpenAI Responses, Azure and Codex send a later update as a `developer`
    message (`system` when the model is not a reasoning model or has
    `supportsDeveloperRole: false`), and load tools added later in place as
    `additional_tools` or, without that flag, as a client `tool_search` pair;
  - Chat Completions sends updates in the instruction role, and with
    `supportsMidConvoToolAdditions` a `{role: "system", tools}` message;
    Mistral sends `system` messages;
  - Anthropic sends `system`-role messages before the next assistant turn,
    and on models with tool changes adds and removes tools with
    `tool_addition`/`tool_removal` blocks, a deferred placeholder tool and
    the `mid-conversation-tool-changes-2026-07-01` beta;
  - Google and Bedrock keep sending the replayed prompt, like Pi.

  A `before_agent_start` suffix or a `before_provider_request` prompt change
  forces the prompt for that request, which then collapses as before. The
  request budget counts the updates and every declaration kept on the wire.
- Anthropic mid-conversation effort, as in Pi (SYS1c, Pi `4e69b0c28`): on
  Claude Opus 5, Opus 5.5, Sonnet 5.5 and Fable 5.1 (`supportsMidConvoEffort`)
  requests think adaptively with `block_binding`, send the effort as
  `output_config` system messages (the current one last, and each earlier
  answer's own before it) and add the `mid-conversation-output-config` and
  `thinking-binding-controls` betas. Each answer records
  `providerThinkingLevel`, stored in the session and emitted in JSON/RPC
  messages.
- `/tree` navigation restores the active tools the target branch declares,
  as Pi's `navigateTree` does (SYS1c). Resume keeps the configured tools.

### Changed

- `/skill <name>` and the bare `/skill` listing are gone; use
  `/skill:<name>`. `enableSkillCommands: false` now only hides the skill
  commands from the menu, as in Pi: the skills stay in the system prompt and a
  typed `/skill:<name>` still runs.
- A skill without a frontmatter `name` is named after its directory, a plain
  `.md` file included (Pi): an unnamed `.pipy/skills/lint.md` is now `skills`.
- Skills and prompt templates are searched in Pi's order: installed packages
  first, then the project and global directories, then `--skill` /
  `--prompt-template` paths, so a default resource wins a name clash.
- Prompt-template and custom-command stores follow symlinks like Pi.
- The leading prompt has Pi's shape (SYS1c). OpenAI Responses and Azure send
  it as the first `input` item in the instruction role instead of
  `instructions`, and a request without messages sends a user item instead of
  a bare string; Chat Completions sends it in the instruction role
  (`developer` for reasoning models that accept it); an empty prompt sends no
  message; Codex sends `"You are a helpful assistant."` when the prompt is
  empty. A session made before SYS1a sends its prompt as a later system
  message to models that accept one, as in Pi.
- Auth store writes (`/login`, `/logout`) re-read `auth.json` under a file lock
  and change only their provider's entry, as Pi's `AuthStorage` does, so a
  credential another process or a token refresh wrote meanwhile is kept.
  `/logout openai` and `/logout github-copilot` keep the selected model when an
  environment key still covers its provider.
- The system prompt is recorded as Pi's tagged sections (SYS1b): the untagged
  `preamble` (the default or custom prompt), then
  `<addendum>` (appended prompts, now wrapped in their tag), `<project_context>`,
  `<skills>`, a new `<cwd>` section with the working directory, and pipy's
  `<resume>` block. A later run patches only the sections that changed. A
  `before_agent_start` suffix is no longer recorded in the transcript; it
  forces that run's prompt, as a forced prompt does in Pi.
- Tools loaded mid-run are declared by the next turn's system message instead
  of a marker on the loader's tool result, as in Pi (which removed
  `addedToolNames` and `supportsToolReferences`). Models without the new
  compat flags now receive such tools as ordinary definitions; Anthropic
  `tool_reference` result blocks are gone. Stored sessions no longer write
  `added_tool_names`.
- Selectors and slash completion follow Pi (DF1-F7b). `/model <ref>` switches
  only on an exact reference among the scoped or available models, for this
  session only (`Model: <id>`); other text opens the model selector with it
  as the search. The `/model` selector is Pi's: a search box with Pi's fuzzy
  ranking, only models with configured auth, the current model and the saved
  default marked, Tab between scoped and all models, Enter for the session
  and Ctrl+S (`app.models.save`, rebindable) to also save the default. The
  `/thinking` selector gains the same search box. The slash menu ranks
  commands with Pi's fuzzy filter, accepting a command inserts `/<name> `,
  and `/model` and `/thinking` complete their arguments. The `/resume`
  search takes Pi's fuzzy tokens, `"phrases"` and `re:` patterns. Slash
  commands are no longer drawn as user messages; a prompt template run draws
  its expanded text. `/model` and `/thinking` failures are red `Error: …`
  lines. The plain REPL keeps its `/model <ref>` resolver.
- Tool calls in the terminal UI are drawn as Pi draws them (TOOLS3): one
  padded box per call, grey while it runs, then green or red, with each
  tool's own rows. A collapsed `read` shows `read <path>:<range>` (or
  `[skill] <name>`, `read resource AGENTS.md`, `read docs …`); `grep` shows
  15 lines and `find`/`ls` 20, with `[Truncated: …]` warnings; `write`
  previews 10 lines of the content; `edit` shows its numbered diff in its box,
  computed before the edit runs, with the changed words of a one-line edit
  highlighted; `bash` shows `$ command`, the last 5 lines, a `[Full output: …]`
  warning and `Took Ns`. The `$ ` prompt on non-bash rows, the
  `[error] tool reported a failure` line and `Took` on non-bash rows are
  gone. Ctrl+O now re-renders every tool row, extension-rendered ones
  included, live and after a resume, and clears the scrollback like Pi.
  Rows use Pi's `toolPendingBg`/`toolSuccessBg`/`toolErrorBg`/`toolOutput`
  colours, which theme files can set (`tool_pending_bg_*`,
  `tool_success_bg_*`, `tool_error_bg_*`, `tool_output_*`). There is no
  syntax highlighting yet.
- Tool results carry Pi's `details` and the session file stores them
  (TOOLS3): truncation facts and limits for `read`/`grep`/`find`/`ls`/`bash`
  (with `fullOutputPath`), `edit`'s `diff` and `firstChangedLine`, and an
  extension tool's `ToolResult.details` (its JSON copy). They are never sent
  to a provider, and restored rows draw from them.
- The default `openai-codex` model is now `gpt-6.1-sol` (was `gpt-5.5`), as in
  Pi (MC6, Pi `12c416e1a`). `--native-provider openai-codex` without
  `--native-model` starts on it; the other defaults are unchanged.
- `read`, `ls`, `grep`, `find`, `@file` and `@image:` resolve paths the way
  Pi's tools do (READ2, Pi `path-utils.ts`): relative to the working
  directory or absolute, with `~` expanded, a leading `@` stripped and unicode
  spaces normalized, and without any deny list. Files under `.git`,
  `.gitignore` matches, generated directories (`node_modules`, `build`,
  `dist`, `.venv`, `.pipy`, …), suffixes such as `.lock`, `.map`, `.min.js`
  and `.d.ts`, `..` and paths outside the workspace all read now, including
  the `bash` full-output temp file. `read`, `@file` and `@image:` also try the
  names macOS gives screenshots (a narrow no-break space before `AM`/`PM`,
  NFD, a curly apostrophe). `ls` lists every entry. `grep` and `find` leave
  out only what `rg --hidden` and `fd --hidden` leave out: `.git` is searched,
  and `.gitignore` (inside a repository, and for `find` also outside one),
  `.ignore`, `.rgignore`/`.fdignore` and `.git/info/exclude` apply. `grep`'s
  Python fallback and `find` follow those rules, including rg's `glob`
  override. `find` accepts patterns that start with `/` or contain `..` or
  `\`.
- `write` and `edit` follow Pi's tools (READ2b, Pi `write.ts`, `edit.ts`,
  `edit-diff.ts`). Both resolve paths like the read tools, with no deny list
  and no size cap. `write` creates parent directories and overwrites an
  existing file. `edit` takes `edits[]` of `{oldText, newText}` (the
  `old_string`/`new_string`/`replace_all` arguments are gone; a JSON-string
  `edits`, a single edit object and top-level `oldText`/`newText` are still
  accepted, as in Pi). Each `oldText` must match once, exactly or after Pi's
  fuzzy normalization (trailing spaces, smart quotes, dashes, special
  spaces), and the edits must not overlap. A UTF-8 BOM and CRLF line endings
  survive the edit. Results and errors use Pi's texts (`Successfully wrote
  to …`, `Successfully replaced N block(s) in ….`, `Could not find the exact
  text in …`, Node's `EACCES: permission denied, open '…'`), and the diff row
  shows Pi's numbered `+NN`/`-NN` lines. Mutations of one file are
  serialized, as in Pi.
- `bash` reports results like Pi (TOOLS2): the output alone on success; a
  non-zero exit is an error ending in `Command exited with code N` (a
  signal-killed shell reports `128 + n`), and a timeout or abort ends in
  `Command timed out after N seconds` or `Command aborted`. The `exit code:
  N` / `[output]` framing is gone.
- The `!`/`!!` shortcut keeps its output like Pi's `bash-executor.ts`:
  ANSI escapes and control characters are removed, the last 2000 lines or
  50 KB are kept (was 16 KB) and the full output goes to a temp file. The
  model sees Pi's `bash` message text (``Ran `cmd` `` with the output in a
  fenced block, `Command exited with code N`, `(command cancelled)`, `[Output
  truncated. Full output: …]`), and the rows show the last 20 lines with
  `(exit N)`, `(cancelled)` and the full-output path, following Ctrl+O. In
  the terminal UI a `!` command no longer stops after 600 seconds; Escape
  cancels it, as in Pi.
- Tool rows show Pi's headers: `grep /pattern/ in path (glob) limit N`,
  `find pattern in path (limit N)`, `ls path (limit N)`, and `write`/`edit`
  paths with the home directory shortened to `~`.
- `ls` sorts like Pi's `localeCompare` (ICU root collation): punctuation,
  then digits (`10` before `9`), then letters with accents and case as minor
  differences, other scripts after Latin. pipy uses a collation table
  measured on Node, so characters outside it can differ from Pi.
- `find` and `grep`'s Python fallback apply the global git excludes file
  (`core.excludesFile`, else `~/.config/git/ignore`) where rg and fd do.

- The terminal UI follows Pi's look more closely (DF1-F7, Pi `1b347794e`):
  - The default `pi` theme uses Pi's `dark` theme colours: accent, dim and
    muted text, `[Context]` headings, errors, warnings, the blue user message
    background, reasoning text and the tool boxes' 256-colour fallbacks. The
    editor border takes the thinking level's colour and turns green while the
    input starts with `!`; the ` ! bash ` label on the border is gone.
  - `/compact` and automatic compaction redraw the chat as Pi does: the kept
    messages, then the `[compaction]` row at once (a padded box in Pi's
    summary colours, also for `[branch]` rows), instead of a
    `compacted conversation context` notice.
  - A running `bash` row shows `Elapsed Ns`, updated every second, until
    `Took Ns` replaces it.
  - Status lines lose the `pipy  pipy:` prefix and are dim, like Pi's; the
    Ctrl+O, Ctrl+T, Shift+Tab and `/thinking` statuses use Pi's texts
    (`Tool output: expanded`, `Thinking level: high`, …).
  - Startup no longer leaves a stale editor frame above the header, and
    `quietStartup` hides the header and resource listing in the terminal UI
    too. The untrusted-project warning follows the restored session.
  - `--print`, `--mode json` and `--mode rpc` no longer write the startup
    chrome, prompt echo or footer to stderr; a successful run leaves stderr
    empty.

- The `bash`, `grep`, `find` and `ls` tools handle long output the way Pi's
  do (TOOLS1, Pi `4df157433`), and take Pi's parameters and descriptions:
  - `bash` keeps the last 2000 lines or 50 KB (was 16 KB), saves the full
    output to a temp file (`pipy-bash-<id>.log` in the system temp directory)
    and ends with `[Showing lines X-Y of N. Full output: <path>]`, which
    `read` can open (READ2).
  - `grep` takes a regex (or `literal`), `glob`, `ignoreCase`, `context` and
    `limit` (default 100). Rows are `path:N: text` relative to the search
    path, with `path-N- text` context lines; long lines are cut to 500
    characters. It uses `rg` when installed, else a Python search with the
    same rules. It no longer skips control-character or secret-shaped files.
  - `find` matches like `fd --glob`: a pattern without `/` matches names at
    any depth, smart case, directories end in `/`; `limit` defaults to 1000.
  - `ls` takes an optional `path` and `limit` (default 500) and lists names
    sorted case-insensitively, with `/` after directories.
  - Every cut ends with Pi's notice, such as `[100 matches limit reached. Use
    limit=200 for more, or refine pattern]` or `[50.0KB limit reached]`,
    instead of `... (truncated)`. Empty results read `No matches found`,
    `No files found matching pattern` or `(empty directory)`.

- The system prompt and tool declarations are now part of the transcript, as
  in Pi (`9e05370b2`, SYS1a). The first run of a session records a
  `role: "system"` message:
  - its only section, `preamble`, holds pipy's prompt;
  - `toolsAdded` lists every tool the model can call.
  Later runs record one only when the prompt or the tools change (section
  patches, `toolsAdded`/`toolsRemoved`), and so does a tool change inside a
  run. `--mode json` and `--mode rpc` emit it as
  `message_start`/`message_end` after `turn_start` and before the user
  message, and `agent_end.messages` starts with it. RPC `get_messages`
  returns it. The session file stores it as a `message` entry. A compaction
  entry stores the replayed state as `systemMessage` and replaces earlier
  system messages with it. The TUI draws nothing for it, and `/tree` shows
  `[system]`. Provider requests are unchanged: pipy sends the prompt out of
  band and never sends later system messages, which is what Pi does for
  models without mid-conversation system messages. `automation_pi_comparison.py`
  passes against Pi `4df157433` again. Per-model mid-conversation
  serialization and Anthropic mid-conversation effort are backlog SYS1b.

- Ctrl+O now expands and collapses tool results already on screen, not only
  the ones rendered afterwards.

### Removed

- `--read-root`, the `PIPY_READ_ROOTS` environment variable, the automatic
  reference roots found in `AGENTS.md` and docs, and the `Reference roots`
  block in the system prompt (READ2). They only widened what the read tools
  could open; those tools now open any path, as in Pi.

### Fixed

- Session cost and token totals are kept (USAGE1, Pi `1b347794e`). Every
  assistant message now stores its usage (uncached input, output, cache reads
  and writes, total tokens and the cost per part, priced from the model's
  catalog row) and the provider and model that answered. The footer, `/session`
  and `--mode rpc` `get_session_stats` sum the stored messages of the whole
  session, every branch included, so the totals no longer reset on `pipy -r` or
  a `/model` switch. The footer shows Pi's parts: `↑` is uncached input, each
  count appears only when non-zero, `CH` is the latest response's cache hit
  rate, and counts use Pi's format (`12k`). `/session` prints Pi's
  `Session Info` block with message counts, tokens (cached and uncached input)
  and a `Cost` section with a per-model breakdown and re-billed cache.
  `--mode json`/`--mode rpc` assistant messages carry Pi's `usage` object and
  `provider`/`model`. Sessions stored before this change load unchanged and
  count as zero.
- An aborted or failed turn is no longer lost (DF1-F6, Pi `4df157433`). The
  assistant message is kept with the text streamed so far and a stop reason
  (`aborted`, or `error` with its message), stored in the session and shown
  again on resume as the partial text followed by `Operation aborted` or
  `Error: <message>`. As in Pi, it is never sent to a provider again: every
  request (and the compaction summary request) skips it, so the next prompt
  continues from the last complete turn. `--mode json`/`--mode rpc` assistant
  messages now carry `stopReason` (`stop`, `toolUse`, `aborted`, `error`) and
  `errorMessage`; `/tree` shows a stopped turn without text as
  `assistant: (aborted)`. The fake `fake-tools` model streams a partial
  answer before waiting when a prompt starts with `STREAMBLOCK`.

- Provider retries are visible and follow Pi's agent-level auto-retry
  (DF1-F4, Pi `4df157433`):
  - The TUI shows the failed attempt as `Error: <message>` and replaces
    `Working...` with `Retrying (1/3) in 2s... (escape to cancel)`, counting
    down. Escape cancels the retry. Exhausted retries end with `Retry failed
    after 3 attempts: <error>`.
  - Every provider is retried, not only OpenAI-Codex. Pi's classifier decides
    (overloaded such as a 529 `overloaded_error`, rate limits, 5xx, network,
    timeouts, early stream ends, "you can retry your request"). Quota, billing
    and context-overflow errors are never retried.
  - A failure after partial output is retried too, and the new answer replaces
    the partial one. Backoff is exactly `retry.baseDelayMs * 2^(n-1)`, with no
    jitter.
  - A Codex stream `error` event now reads `Codex error: <message or code>`,
    and `response.failed` reads the server's message, so the classifier sees
    their text. A mid-stream `server_is_overloaded` is retried.
  - JSON and RPC emit `auto_retry_start` for each retry and one
    `auto_retry_end` per sequence (it was one per attempt). A cancelled retry
    reads `Retry cancelled`. The retried attempt's `message_update` text starts
    over.
  - After a failed turn the footer context no longer jumps (e.g. 0.6% to
    7.2%). It keeps the last successful response's usage, as in Pi.
- A turn whose tool result alone overflows the context window no longer wedges
  the session (DF1-F5). That turn is still refused, as in Pi, but the next
  prompt now compacts it away. Two changes follow Pi (`4df157433`):
  - Compaction and branch-summary requests cut each tool result to its first
    2000 characters plus `[... N more characters truncated]` (Pi
    `serializeConversation`), so the summary request no longer fails its own
    size check.
  - In a persistent session, an automatic cut that keeps only the new prompt
    writes a compaction entry whose `firstKeptEntryId` is its own id (Pi
    `appendCompaction(summary, null)`) instead of refusing with `retained
    history has no durable origin`. Reopen, fork and clone honor it.
  A removed range that is still too large after truncation (for example a huge
  paste) stays refused until `/new`, as in Pi.
- `--mode rpc` no longer stops running turns after a `follow_up` arrives
  before an idle `steer` has started. The session worker could exit, so later
  prompts were accepted but never ran, `compact` never answered, `get_state`
  kept reporting `isStreaming: true` and `new_session` or `cycle_model`
  answered `session is not idle`. It depended on timing and also affected
  0.2.0.

- A resumed conversation is shown again. Startup with `-r`, `--continue` or
  `--session`, `/resume`, `/tree` navigation, `/fork`, `/clone`, `/new` and
  `/import` redraw the transcript from the active branch, as Pi does: user and
  assistant messages, tool calls with their results, `!` shell rows,
  `[compaction]` and `[branch]` summaries (Ctrl+O expands them) and extension
  entries. Before, the screen stayed empty although the model had the context.
- A resumed session gets its model and thinking level back. pipy now records a
  `model_change` with a new session's first message and on every model switch
  (plus a `thinking_level_change` when the switch clamps the level), and
  opening a session restores its last model (unless `--native-provider` or
  `--native-model` pins one) and thinking level (unless `--thinking` is
  given), at startup and after `/resume`, `/fork` and `/clone`. A model that
  cannot be used prints `pipy: Could not restore model …` at startup and keeps
  the default. Before, a resumed session started at the default thinking
  level and model.

## [0.2.0] - 2026-09-29

### Highlights

A curated summary of the largest changes in this release; the full entries
follow.

- Current model rows from Pi's generator (`4df157433`): Claude Opus/Sonnet 5.5
  and 5, Fable 5/5.1, Opus 4.8, GPT-6 Sol/Luna/Astra, GPT-5.6 and Gemini 3.5
  Flash, with Pi's provider defaults and thinking maps.
- Context files and skills load the way Pi loads them: `AGENTS.md` or
  `CLAUDE.md` per directory, `.agents/skills/` roots, and symlinks followed.
- Prompt caching for OpenAI, OpenAI-Codex, Azure, Anthropic and Bedrock, with
  `PIPY_CACHE_RETENTION=long`.
- `/thinking` sets the session's thinking level, and the footer shows the live
  level.
- Session cost comes from each model's catalog row, priced like Pi's
  `calculateCost`.
- New `xai` and `github-copilot` providers, with `/login github-copilot`.
- `read` pages like Pi's `read`: `offset`/`limit`, 2000 lines or 50 KB per call,
  and a notice that says where to continue.
- `just catalog-drift` reports where the built-in catalog differs from Pi's.
- Fixes from live dogfooding on OpenAI-Codex: a model switch keeps the
  conversation, `edit`/`write` diffs no longer corrupt the TUI, Escape no longer
  freezes the frame on a WebSocket stream, and tool rows no longer show argument
  dumps.

### Added

- New `xai` and `github-copilot` providers, following Pi (`4df157433`).
  `xai` serves Pi's Grok 4.3 to 4.7 rows over the OpenAI Responses API with
  `XAI_API_KEY` (default `grok-4.7`). `github-copilot` serves Pi's 33 Copilot
  rows (Claude, GPT, Grok, Gemini, Kimi, MAI), each on the API family Copilot
  uses for it (default `gpt-5.4`). See `docs/providers.md` (GitHub Copilot).
  - `/login github-copilot` runs GitHub's device-code login, including GitHub
    Enterprise domains. It lists the account's models and enables the ones
    whose policy is unconfigured. `/logout github-copilot` removes the
    credential. `COPILOT_GITHUB_TOKEN` also works.
  - Requests go to the endpoint named in the Copilot token and refresh the
    token when it has less than five minutes left. They carry Copilot's editor
    headers plus `X-Initiator`, `Openai-Intent` and, when an image is sent,
    `Copilot-Vision-Request`. Claude models use Bearer auth. After a login,
    model lists show only the account's models.
  - Not ported: Pi's xAI OAuth login, and cost tiers. A refreshed Copilot
    token is kept in memory rather than written back to the auth store.
- `/thinking` sets the thinking level for the current session, as in Pi
  (`4df157433`). `/thinking <level>` takes any level the model offers, in any
  case. A bare `/thinking` opens a selector of the model's levels with Pi's
  descriptions, the current level marked `✓` and the default marked
  `· default`. Enter applies the level for this session. The new
  `app.thinking.save` binding (Ctrl+S, rebindable) also saves it as
  `defaultThinkingLevel`.
- `just catalog-drift` compares the built-in model catalog with Pi's
  generated catalog and reports rows or values that differ. This covers
  context window, max tokens, cost, thinking maps, API family, base URL and the
  compat flags pipy reads. Intentional differences are listed in
  `scripts/catalog_drift_allowlist.json`. It is a manual check, not part of
  `just check`. See `docs/provider-catalog.md` (Catalog drift check).
- Context files now load the way Pi loads them (Pi `4df157433`). In each
  directory pipy takes the first file that exists from `AGENTS.override.md`,
  `AGENTS.md`, `AGENTS.MD`, `CLAUDE.md`, then `CLAUDE.MD`. It stops ignoring
  `CLAUDE.md`, so projects whose instructions live only in `CLAUDE.md` now get
  them.
  - Inside a linked git worktree nested in its main checkout, the main
    checkout's same-named file is skipped, as in Pi.
  - The files reach the model as Pi's `<project_context>` section, with one
    `<project_instructions path="...">` block per file.
  - The startup `[Context]` list comes from the same loader.
- Skills now come from Pi's roots and in Pi's layout:
  - project `.agents/skills/` directories from the cwd up to the git root
    (trusted projects only), and `~/.agents/skills/`;
  - a `<name>/SKILL.md` directory is one skill, named after its directory;
  - `.gitignore`, `.ignore` and `.fdignore` files inside a skills directory
    apply.

  A `.agents/skills/` directory in the project or any parent directory now makes
  the project need trust. The skills advertisement is wrapped in Pi's `<skills>`
  section.

- The built-in catalog now carries Pi's current frontier rows. Costs, context
  windows, max tokens and thinking maps come from Pi's generator at
  `4df157433`. The new rows are:
  - Anthropic: Claude Opus/Sonnet 5.5, Opus/Sonnet 5, Fable 5/5.1, Opus 4.8 and
    Haiku 4.5.
  - OpenAI and Codex: GPT-6 Sol/Luna/Astra and GPT-5.6 Terra/Luna, plus
    GPT-5.6 Sol on OpenAI.
  - Google and Vertex: Gemini 3.5 Flash and 3.1 Flash Lite.
  - Bedrock: `us.` mirrors of Claude 5.x, Fable 5, Opus 4.8 and Haiku 4.5.

  Claude 5.x and Fable use adaptive thinking, and `max` reaches
  `output_config.effort`. OpenRouter Claude rows are not mirrored yet, because
  Pi routes them through an Anthropic-Messages transport that pipy lacks.

- OpenAI prompt caching now follows Pi (`4df157433`), so consecutive turns in
  one session can reuse the provider's prompt cache:
  - OpenAI-Codex sends the session id (cut to 64 characters) as the body
    `prompt_cache_key` and as the `session-id` and `x-client-request-id`
    headers, on SSE and WebSocket. Before, the WebSocket used a fresh id per
    attempt and SSE sent none.
  - OpenAI Responses sends the key plus Pi's affinity headers (`session_id` and
    `x-client-request-id`, or `x-session-id` for OpenRouter).
    `PIPY_CACHE_RETENTION=long` (Pi's `PI_CACHE_RETENTION`) asks for 24-hour
    retention, or a 30-minute `prompt_cache_options` TTL on GPT-5.6 and later.
  - Azure OpenAI sends the key.
  - Compaction and branch summaries send no cache key and use a fresh routing
    id, as in Pi. Azure is the exception: Pi sends it that routing id as the
    key.

- Anthropic prompt caching now follows Pi (`4df157433`). Before, pipy sent no
  `cache_control`, so Claude never reused a cached prefix.
  - Anthropic Messages marks the system prompt, the last tool, and the last
    block of a trailing user message with `cache_control: {type: "ephemeral"}`.
    The system prompt is now sent as a text block, and omitted when empty.
  - Bedrock Claude marks the system prompt and the last user block, but not
    tools. Only Pi's cache-capable Claude families get the markers:
    3.5 Haiku, 3.7 Sonnet, 4.x and 5.x. `AWS_BEDROCK_FORCE_CACHE=1` turns them
    on for ids that do not name Claude, such as application inference
    profiles.
  - `PIPY_CACHE_RETENTION=long` asks for the 1-hour TTL (`ttl: "1h"`).
    Compaction and branch summaries send no markers.
  - The 1-hour part of a cache write is read from the usage
    (`cache_creation.ephemeral_1h_input_tokens`) and priced at twice the input
    rate, as in Pi.
  - models.json `compat.supportsLongCacheRetention`,
    `compat.supportsCacheControlOnTools`, `compat.sendSessionAffinityHeaders`
    and `compat.sessionAffinityFormat` work as in Pi. An OpenRouter Anthropic
    endpoint gets the `x-session-id` header.

- Terminal `/import` now stages its permissive copy through the existing
  presentation path, then adopts that durable copy through the native transition
  owner. The owner validates the current terminal lease before the switch hook,
  claims the imported file before guarded state-first publication, retires the
  old claim before rebuilding history, and releases the adopted claim on normal
  or fatal teardown. Confirmations, recovery for missing recorded workspaces,
  controlled diagnostics, and partial-copy behavior remain unchanged.

- Terminal `/fork` and `/clone` now use the native transition owner. They
  snapshot the guarded active tree, claim and publish the child before releasing
  the source lease, and preserve terminal diagnostics, footers, and the detailed
  fork extension gate. Empty terminal sessions now refuse either command rather
  than creating an empty child; import and Python/RPC transition behavior are
  unchanged.

- Terminal `/new` now uses the native transition owner. Persistent terminal
  sessions create and claim a sibling before state-first replacement and release
  the adopted claim on teardown; in-memory terminal sessions remain file-free.
  Existing terminal diagnostics, footers, and extension-switch gating are
  preserved, while Python and RPC `new_session` semantics remain unchanged.

- Terminal `/resume` now adopts resolved native session targets through the
  canonical transition owner. Each stream lifetime claims its initial durable
  tree, strict-loads and workspace-validates a candidate before state-first
  replacement, and releases the current claim on normal or fatal teardown.
  Same-file aliases are no-ops; picker, listing and session-management
  presentation remain unchanged.

- Persistent Python product sessions can now fork or clone, create an empty
  sibling session, and strictly switch to one exact durable session in place.
  The native transition owner preserves the facade lifetime, canonical path
  claims, extension gates, and state-first history rebuild. RPC now adopts the
  same native owner for `new_session`, strict `switch_session`, `fork`, and
  `clone`, preserving correlated outcomes and the native idle-only control
  boundary.

- Direct RPC bash now emits bounded, line-gated and secret-redacted
  `bash_execution_update` records before its correlated terminal response.
  Per-operation relays isolate update correlation and let direct process cleanup
  proceed while a stdout consumer is blocked; terminal bash output remains the
  authoritative independently redacted result.

- Direct RPC bash operations can now be cancelled as an abort-all snapshot of
  their current exact operation identities. The restricted command sandbox
  starts each child in a separate process group, terminates and reaps that
  group on explicit abort or timeout, preserves bounded redacted output, and
  distinguishes explicit cancellation from timeout in the correlated result.

- Python embeddings can now reopen one exact durable native product-session
  JSONL file with `open_product_session(...)`. Reopen validates the workspace
  header, strictly rejects malformed or structurally invalid durable records,
  reconstructs the active native context with the explicitly supplied provider,
  and prevents simultaneous same-process public facades for canonical-path and
  symlink aliases. CLI recovery behavior and cross-process coordination are
  unchanged.

- RPC `compact` now runs the native semantic-compaction owner as one idle-only,
  cancellable worker operation, with correlated compaction lifecycle records and
  truthful state/result projection. `set_auto_compaction` now persists the real
  effective policy rather than an RPC-local flag; automatic preflight cuts emit
  threshold or overflow lifecycle reasons. Summary provider output, retry phases
  and usage remain private.

- RPC model and thinking controls now use the native provider-mutation owner.
  Catalog-backed sessions expose available tool-capable selections, switch the
  actual next provider request while idle, and refresh supported thinking levels
  with durable state-first entries. Model switches reset coding history and
  usage; thinking-only refreshes retain them. Static injected providers remain
  truthful singleton fallbacks. Live credentials and provider acceptance remain
  unverified dogfood work.

- RPC now adopts the native coding-session control atomically. Native queue
  state owns prompt/steer/follow-up admission, abort, exact claims and
  settlement; RPC retains LF JSONL framing, correlation, output projection and
  direct bash. The wake channel carries wake/EOF only, literal product content
  remains intact, and promoted runs publish `agent_end`, `queue_update`, then
  the next `agent_start`; protocol idle follows the later true-idle re-poll.

- Ordinary persistent Python product-session submits now use the native coding
  queue's atomic managed-operation claim for the complete submit-to-idle drive,
  including extension settled-hook continuations. The native lifetime settles
  the exact token on every exit, and a once-bound queue signal view replaces the
  facade-owned cancellation slot. Public submit, cancellation, result and close
  semantics are unchanged; no concurrent SDK controls or RPC serialization
  changes are introduced.

- The coding input owner now contains an internal, guarded external-admission
  mechanism. It reserves and claims one exact operation, queues steering before
  follow-ups, atomically promotes settlement, and signals a fresh accepted-abort
  latch outside the queue guard. Ordinary product-session submits now adopt its
  idle-only operation entry; RPC, selectors, extensions, and agent-loop delivery
  remain unchanged.

- Branch-summary tree selection now runs through canonical private provider
  execution with one frozen request and captured bounded retry policy. Retry
  events, deltas, and summary usage stay outside ordinary product projections;
  provider and backoff cancellation settle manual pending input. Selection
  rejects generated text when its original
  tree, leaf, coding history, provider binding, extension generation, or
  publication window changed. Successful selection publishes the tree and coding
  projection coherently, clears branch-bound extension inputs, and durably appends
  the exact accepted entry in order. Append failure preserves accepted live state;
  retained controls invoked by a provider worker do not require a new effect lease.

- Manual and automatic semantic compaction now use the canonical bounded retry
  mechanism for prepared providers. One captured policy and frozen private
  summary request span all logical attempts; retry admission and final acceptance
  validate the original compaction witness, and cancellation or exhaustion
  publishes no summary. Branch summaries keep their existing provider-owned
  behavior.

- Ordinary native product requests now capture retry settings once per request
  and use bounded caller-managed retries for the prepared OpenAI Codex
  capability. Retries reuse the prepared body and headers, preserve prior tool
  effects, stop on provider progress or reported usage, and revalidate the
  original coding context before reissue. Cancellation covers both backoff and
  provider phases; exhausted failures still leave the next prompt usable.
  Other providers, branch summaries, and the compatibility SDK runtime retain
  their existing behavior. RPC now persists exact-boolean `set_auto_retry`
  through the precedence-aware settings owner and can cancel only the exact
  active ordinary retry phase with `abort_retry`; it neither owns a retry latch
  nor exposes retry activity in state.

- Native compaction entries can now durably retain one exact user message plus a
  later tool-cycle suffix. Reopen and fork apply anchored and subsequent cuts in
  effective chronological order, reject invalid or synthetic anchors, strictly
  remap both references, and restore model/thinking settings from full ancestry.
  Known-limit automatic pressure now uses this form when the newest user group
  still does not fit after older whole groups are removed.

- Canonical history compaction now records exact retained and removed message
  objects and can purely select older settled tool cycles after the latest exact
  user while preserving the newest cycle. Guarded acceptance verifies a real,
  identity-preserving removal and permits a truthful zero whole-group count.
  Live known-limit automatic compaction can now activate the cycle selector;
  manual and unknown-limit compaction keep their existing whole-group behavior.

- Terminal native agent results now enumerate the canonical messages appended
  during the accepted run independently of retained provider context. This
  preserves earlier tool cycles for automation and completion consumers when
  within-run context cuts remove them from retained provider context.

- Native requests now use estimated declared/explicit context budgets, including
  system/messages/tools/images, safety and an output reserve. Optional pipy-only
  `compaction.contextWindow` limits the selected model. Known pressure permits one
  safe summary attempt, including older complete tool cycles when needed;
  ordinary hooks run once before final admission.
  Manual and automatic summaries preflight their own input. Invalid budgets and
  final overflow refuse recoverably; `keepRecentTokens` remains inactive and
  estimates do not guarantee provider fit. A persistent first-iteration cut to the
  new user can refuse its not-yet-durable origin; normal settlement then records
  that user once for subsequent operations. Manual `/compact` remains available.

- Native request preparation can now settle a typed recoverable refusal without
  calling the provider or creating an empty assistant message. Product snapshots
  retain the separate failure, earlier tools and usage survive, and the next
  prompt can continue. Bounded archive results expose only a safe classification;
  print mode suppresses stale earlier answers. Model-budget admission now uses this
  same refusal path.
- Python embeddings can now use `create_product_session` with an explicit
  tool-capable provider for persistent native coding: repeated literal prompts,
  real tools, fixed full-content events, immutable idle snapshots, cross-thread
  cancellation and once-only disposal. Workspace preparation retains existing
  instruction loading and fail-closed project trust. Sessions are ephemeral by
  default and create no workflow archive; the one-shot CLI runtime keeps its
  compatibility semantics. Optional diagnostic callbacks receive write fragments.
- RPC active-run `abort` now interrupts model-driven tools through the canonical
  cancellation worker, as well as provider and semantic-summary execution.
  Completed tool effects remain recorded and uncooperative tools retain bounded
  cleanup. The separate direct RPC bash cancellation boundary is unchanged.

- Native coding composition now has an internal persistent lifetime that yields
  at idle and continues with the same conversation and resources. The stream
  entrypoint shares its loop and once-only cleanup.

- Python extension tools can now activate additional registered tools during
  execution with `ctx.set_active_tools(...)`. Purely additive changes persist a
  provider-agnostic load-point marker on that tool result; supported first-party
  Anthropic Claude 4.5+ requests keep the new definitions out of the immediate
  cache prefix with `defer_loading: true` and load them at the result with
  `tool_reference`, while supported OpenAI Responses and OpenAI Codex Responses
  models now load them at the same durable result with completed client
  `tool_search_call`/`tool_search_output` items. Older/custom-disabled models
  and removals safely send the complete current tool list. Kimi Chat
  Completions deferred tools remain a separate follow-on.
- Python extensions can now register Pi-shaped durable entry renderers with
  `api.register_entry_renderer(...)`. `ctx.append_entry(...)` records receive a
  live product-TUI component with full stored-entry metadata plus current
  expanded/width/theme context; startup replay, `/resume`, expansion changes,
  and `/reload` use the same independent registry. Entry renderers stay inert
  in print/JSON/RPC modes, and their output never enters session JSONL,
  provider context, protocol stdout, or the summary-safe archive.
- Python extensions can now observe Pi's payload-free `agent_settled`
  lifecycle hook once a provider/tool run is truly idle. Automatic retry,
  compaction work, and queued steering/follow-up/extension prompts finish
  first; unexpected mid-run failures still settle, and a settled handler may
  schedule a new run without blocking on stdin. JSON/RPC protocol events remain
  mode-owned, so their streams still emit exactly one `agent_settled`.
- Python extensions can now register Pi-shaped `before_provider_headers`
  handlers. Each real HTTP provider request exposes a mutable header map after
  request-scoped assembly; handlers run serially, may add/override values or set
  one to `None` to delete it, and fail soft. Bedrock mutations occur before
  SigV4 signing, while OpenAI Codex retries and WebSocket-to-SSE fallback reuse
  one transformed snapshot without re-firing the hook. Header data remains
  live-only and never enters session archives or JSON/RPC protocol output.
- Project-trust extensions now ship end to end. Before an unresolved decision,
  pipy activates only global and explicit CLI extensions and runs their
  `project_trust` handlers serially; `undecided` continues, the first yes/no
  wins, failures warn and continue, and only exact `remember=True` persists the
  exact cwd. Headless UI choices are inert and notifications remain stderr-only.
  The same activation instances feed provider-catalog construction and the
  initial live session, so module top-level code runs once while project and
  project-package extensions remain gated. Normal extension contexts expose
  zero-argument `is_project_trusted()` and `isProjectTrusted()` run-local reads.
- RPC mode now implements Pi's read-only `get_entries` (including optional
  `since` slicing) and `get_tree` session-inspection commands, bringing pipy's
  green RPC baseline to all 31 Pi command types. Both return a coherent session
  entries/tree and leaf snapshot; deep linear trees use depth-safe iterative
  serialization.
- RPC mode now emits Pi's payload-free `agent_settled` event after the final
  `agent_end` when the session reaches true idle. Queued steer/follow-up runs do
  not emit a premature settled event between runs.
- `--mode json` now also emits Pi's payload-free `agent_settled` as the final
  event after the run's `agent_end`. The one-shot json driver settles into idle
  when the run returns, matching Pi's `_runAgentPrompt` `finally` that `--mode
  json` forwards. The extension-surface hook now ships independently without
  duplicating this protocol event.
- `openai-codex/gpt-5.6-sol` is now a built-in Codex model (372K context, image
  input) with a seventh thinking level, `max`. The thinking vocabulary is now
  `off|minimal|low|medium|high|xhigh|max` across the CLI `--thinking`/`:level`
  suffix, settings, extension controls, RPC, and `models.json`. Shift+Tab
  cycling is model-aware: every reasoning model cycles the ordinary tier and
  appends `xhigh`/`max` only when the active model maps them, so Sol cycles all
  seven levels. The Codex request now clamps an unsupported stored level to the
  nearest supported one and emits it as `reasoning.effort` (matching Pi's
  per-request `clampThinkingLevel`); e.g. a stored `max` sends `effort: "max"`
  on Sol and clamps to `xhigh` on GPT-5.5. Sol renders a `372k` status budget.
  GPT-5.5 remains the Codex default; no bare `gpt-5.6` alias is added.
- Extension UI editor text helpers now expose Pi-canonical camelCase aliases:
  `ctx.ui.getEditorText()`, `ctx.ui.setEditorText(text)`, and
  `ctx.ui.pasteToEditor(text)`. The existing snake_case helpers remain
  available as Python convenience aliases.
- Rich extension custom-message renderers now refresh their existing TUI block when the live tool-output expanded flag changes, matching Pi's `MessageRenderer(..., { expanded })` behavior without persisting rendered rows.
- Live custom editor components now receive Pi-shaped app-action delegation:
  keybinding specs, model/thinking/tool/follow-up handlers, external-editor
  (`app.editor.external`) handoff, Escape/Ctrl-D and paste-image callbacks,
  draft preservation, and Ctrl-C remains on the terminal interrupt path. The
  built-in editor now also opens `$VISUAL`/`$EDITOR` from the resolved
  `app.editor.external` binding, default Ctrl-G, as an undoable edit. The
  default Ctrl-G editor binding is reserved from extension shortcuts; extensions
  that still register Ctrl-G now fail activation as a reserved shortcut.
- Project-trust core now gates project `.pipy` settings and resources before
  startup loads them. Decisions live in owner-private `<config>/trust.json`
  with closest-ancestor lookup; global-only `defaultProjectTrust` accepts
  `ask|always|never`; and `--approve`/`-a` plus `--no-approve`/`-na` override
  one run without persistence (the last flag wins). Untrusted runs retain
  global resources/packages and explicit CLI sources, keep `AGENTS.md`/`pipy.md`
  context behavior, and exclude project settings, extensions, skills,
  templates, commands, system-prompt files, and project package declarations.
  Print/JSON/RPC/help/list-model paths fail closed without trust prompts or
  protocol output.
- Interactive project trust now ships end to end. An unresolved interactive TTY
  opens Pi's five-choice startup selector (current folder, parent, session-only,
  decline, or session-only decline); `/trust` shows saved/inherited and current
  state and persists a next-restart decision without hot-loading resources; and
  `/reload` narrowly saves trust when a previously resource-free trusted run
  explicitly loads a newly created protected input. `/settings` now controls the
  global `defaultProjectTrust` enum. `install`, `remove`/`uninstall`, `list`, and
  `config` accept command-local `--approve`/`--no-approve`; untrusted listings
  omit project entries, global operations remain usable, and local writes fail
  before mutation. Package `update` realignment remains separate.


### Removed

- `pipy.md` and `PIPY.md` are no longer context files. Pi has no equivalent;
  rename them to `AGENTS.md`. Skills without a frontmatter `description` no
  longer load, matching Pi's `loadSkillFromFile`.
- The pipy-only one-shot Python SDK facade has been removed outright. Python
  embedding now uses `create_product_session(...)` or `open_product_session(...)`
  with an explicit provider; `pipy run --agent pipy-native` retains its separate
  compatibility runtime and CLI/archive behavior.

- The pipy-only model-visible `edit_diff` tool and its unified-diff
  implementation have been removed outright, with no alias, compatibility
  dispatch, or deprecation shim. `edit` is now the sole edit tool, matching
  Pi's seven-tool product manifest while retaining its existing path and trust
  policy.
- The pipy-only model-visible `truncate` tool has been removed outright, with
  no alias, compatibility path, or deprecation shim. Read excerpts, `bash`
  output, provider-visible tool results, and rendered previews retain their
  independent automatic bounds.
- The no-tool REPL has been retired. There is now one product REPL — the
  model-visible tool-loop session. The `--repl-mode` flag and the no-tool
  commands `/read`, `/ask-file`, `/propose-file`, and `/apply-proposal` (and
  their archive-side observation/patch-proposal events) are gone; the model uses
  `read`/`edit`/`write`/`bash` directly.
- The `--native-output json` one-shot flag has been removed. Automation callers
  use `pipy repl --mode json` (the full Pi-shaped session event stream) or
  `--print`/`-p` for final-text output; the removed flag now prints guidance
  naming `--mode json`. `pipy run` keeps its default human/exit-code behavior.
- The `--archive-transcript` transcript sidecar has been removed, along with the
  `pipy-session export` `--export-transcript`/`--include-transcript` reader (the
  export schema is bumped v1→v2). The native session tree is the transcript; use
  `/export` (or `pipy --export`). The removed flag prints guidance.
- The pipy-only `/template` wrapper command has been removed. Prompt templates
  are now invokable as their own `/<template-name>` slash commands (matching Pi,
  which has no literal `/template`).
- The pipy-only `/clear`, `/status`, `/help`, and `/theme` slash commands have
  been removed outright, with no deprecation aliases or notices. Pi has none of
  them; use the Pi equivalents `/new`, `/session`, and `/hotkeys`, and select a
  theme from the `/settings` dialog. This follows pipy's no-deprecation policy
  (no users yet, private until Pi parity — see `AGENTS.md`); the brief
  `/clear`→`/new`/`/status`→`/session` deprecated aliases and the `/help`→
  `/hotkeys` alias introduced earlier in this cycle are gone. The
  `--theme`/`--no-themes` load flags and `PIPY_THEME` are unchanged.

### Changed

- The `read` tool now follows Pi's `read` (`4df157433`). It takes optional
  1-indexed `offset` and `limit` parameters, returns up to 2000 lines or
  50 KB, and ends a cut result with Pi's notice, such as
  `[Showing lines 1-512 of 901 (50.0KB limit). Use offset=513 to continue.]`
  or `[4 more lines in file. Use offset=7 to continue.]`. Before, it
  stopped at 200 lines / 8 KB without saying so: on a 901-line CSV the model
  took row 198 for the last row, and automatic compaction kept that wrong
  fact (DF1). A first line over 50 KB points at a `sed | head -c` command,
  and the tool-row header shows the requested range (`read a.csv:513`).
  - Files over 256 KB, non-UTF-8 and binary files, and files with
    secret-shaped content now read like any other file (invalid bytes become
    U+FFFD), as in Pi. `@file` references read through the same tool, so
    they change the same way.
  - Not ported: images (jpg, png, gif, webp, bmp) return an error rather
    than an attachment (READ-IMG), and paths still follow pipy's
    workspace/read-root policy with `.git`, `.gitignore` and generated-file
    refusals (READ2). `offset`/`limit` are integers (Pi: numbers).
- Switching the model (`/model`, Ctrl+P, RPC `set_model`, extension
  `setModel`) now keeps the conversation, as Pi `setModel` does. The next
  request replays the same history to the new model. Before, the switch
  cleared provider-visible history, so the new model answered without any
  earlier context while a later resume of the same session still had it.
  Usage totals still restart per model. Found by the DF1 live dogfooding run.
- OpenAI Responses requests with a thinking level now send Pi's
  `reasoning.summary: "auto"` and ask for the encrypted reasoning item
  (`include: ["reasoning.encrypted_content"]`). This applies to every
  `openai-responses` row (OpenAI, xAI, Copilot, custom); Azure and Codex are
  unchanged. Chat Completions rows whose `compat.supportsReasoningEffort`
  resolves false no longer send `reasoning_effort`, as in Pi.
- `just catalog-drift` also compares each row's static request headers.
- Built-in rows the first catalog drift check flagged now carry Pi's values
  (Pi `4df157433`):
  - Mistral Large, Devstral Medium and Mistral Small: cost, context window and
    max tokens;
  - Gemini 2.5 Pro on Google and Vertex: cost and context window;
  - OpenRouter GPT-5.1 Codex: cost and thinking map;
  - `openai-completions/gpt-4.1`: context window.

  Mistral Small is now a reasoning model. It offers `high`, and off sends
  `reasoning_effort: "none"`. Mistral `reasoning_effort` now follows Pi: a
  level is clamped to the model's levels before it is mapped. OpenRouter GPT-5.1
  Codex offers low, medium and high, and thinking cannot be switched off.
- Session cost now comes from the selected model's catalog row, priced with
  Pi's `calculateCost` (`4df157433`), so Claude and every other priced model
  show a cost. Before, only Codex `gpt-5*` had a hard-coded price. `models.json`
  `cost` overrides apply, and `cost.tiers` (Pi's request-wide long-context
  rates) is accepted on custom models and overrides; built-in rows get tier
  data with the catalog sync. Cached input is no longer charged at the full
  input rate, and reasoning tokens are no longer charged on top of output.
  Gemini thinking tokens now count as output, and cache reads are read from
  Gemini, Chat Completions (OpenAI-compatible, OpenRouter, Cloudflare), Mistral
  and Responses cache-write counters the way Pi reads them.
- The footer cost follows Pi: `$0.123` when there is a cost, nothing at zero,
  and `$0.123 (sub)` on a subscription login (OpenAI Codex, Anthropic or
  GitHub Copilot OAuth, or an extension OAuth provider declaring
  `is_subscription=True`). The `(api)` label is gone. RPC `get_session_stats`
  reports the same token totals and cost instead of zeros.
- Skills, prompt templates, custom commands, global extensions and
  `models.json` now use the same global config root as settings and context
  files: `PIPY_CONFIG_HOME`, `${XDG_CONFIG_HOME}/pipy`, then `~/.pipy` when it
  exists, then `~/.config/pipy`. Before, they skipped `~/.pipy`, so skills in
  `~/.pipy/skills/` never loaded.
- Skill roots follow symlinks like Pi. A skill directory or file symlinked into
  `~/.pipy/skills/`, `~/.agents/skills/`, a `--skill` path, or a trusted
  project's `.pipy/skills/` or `.agents/skills/` now loads from wherever it
  points, and a symlinked skills root is followed too. Template and command
  stores keep their containment guard.
- Context files follow symlinks like Pi, including a link to a file outside
  its directory. Context files load without project trust, so treat them as
  untrusted input (Pi `docs/security.md`).
- Provider defaults now mirror Pi's `defaultModelPerProvider`:

  | Provider | Default |
  |---|---|
  | anthropic | `claude-opus-4-8` |
  | google and google-vertex | `gemini-3.1-pro-preview` |
  | amazon-bedrock | `us.anthropic.claude-opus-4-6-v1` |
  | azure-openai | `gpt-5.4` |
  | openrouter | `moonshotai/kimi-k2.6` |
  | cloudflare | `@cf/moonshotai/kimi-k2.6` |
  | mistral | `devstral-medium-latest` |
  | openai-completions | `gpt-5.5` |

- Thinking now follows Pi's model rules:
  - A new session starts at the settings `defaultThinkingLevel`, or `medium`
    when that is unset, clamped to the startup model. Before, it started
    unset. A model switch clamps the carried level to the new model.
  - A level mapped to `null` is not offered. That includes `off` on models
    that cannot switch thinking off.
  - OpenAI Responses, Azure and Codex clamp an on-state level. An explicit
    `off` sends `reasoning.effort: "none"` (or the row's own off value).
  - Gemini clamps a level before mapping it.
  - Anthropic uses adaptive thinking only for rows with
    `compat.forceAdaptiveThinking: true`. Rows that cannot switch thinking off
    never get `thinking:{type:"disabled"}`.
- Existing catalog rows that Pi still ships now carry Pi's costs, context
  windows and thinking maps. The retired Codex GPT-5.4 row is removed.

- Custom commands, prompt templates, and extension commands can no longer be
  advertised in slash discovery or registered by an extension when their name
  collides with any built-in command. The reserved-name set now covers every
  built-in (`reload`, `tree`, `new`, `fork`, `session`, `compact`, `export`,
  `import`, `clone`, `resume`, `name`, `share`, `trust`, `scoped-models`,
  `hotkeys`, `changelog`, and the previously covered names) rather than a
  hand-maintained subset, closing an advertising gap where such a colliding
  name was still shown even though the built-in always ran instead. Runtime
  command dispatch is unchanged.
- Provider requests now carry an exact request-local advertised-tool snapshot.
  Serial `before_provider_request` tool transforms can only narrow the current
  detached definitions in their prior order, while `ctx.set_active_tools(...)`
  changes later provider iterations. Request construction and extension
  transforms route through a focused product adapter; public request formats
  and callback ordering are unchanged.
- Model-driven tool definition lookup, execution, and policy-error creation now
  flow through the synchronous canonical `AgentToolCapabilities` port. Product
  registry composition, CLI/run filters, active-tool changes, extension reload,
  workspace context, and executor construction live behind
  `NativeToolCapabilities`; scheduling remains sequential and public formats,
  extension ordering, persistence, and archive privacy are unchanged.
- Canonical agent-history compaction now lives in the dependency-neutral
  `native.agent.history` layer. The obsolete mixed
  `native.session_compaction` module and its unused no-tool compaction path are
  removed; product trigger policy, exact summary text, extension ordering, and
  durable session-tree writes remain owned by the native tool-loop session.
- Extension custom footers now receive live product-TUI `FooterData.onBranchChange(...)` callbacks that rebuild/repaint the footer on git branch changes; headless snapshots keep the safe no-op disposer.
- The native `google-generative-ai` provider now injects Pi's per-model
  `generationConfig.thinkingConfig`: a `thinkingLevel` enum (Gemini 3 Pro/Flash,
  Gemma 4) or a `thinkingBudget` token count (Gemini 2.5 family) with
  `includeThoughts: true` when thinking is on, and a per-model disabled config
  (no `includeThoughts`) when a reasoning-capable model runs with thinking
  off/unset — matching Pi's `google.ts` `streamSimpleGoogle`/`buildParams`.
  Non-reasoning models still omit `thinkingConfig` entirely.
- The native `google-vertex` provider now injects Pi's per-model
  `generationConfig.thinkingConfig` from `google-vertex.ts` (its
  `THINKING_LEVEL_MAP` variant) in both Express (api-key) and ADC (bearer) modes:
  a `thinkingLevel` enum for Gemini 3 Pro/Flash, a `thinkingBudget` token count
  otherwise (`includeThoughts: true` when on), and a per-model disabled config
  (no `includeThoughts`) when a reasoning-capable model runs with thinking
  off/unset. Catalog construction forwards the resolved
  `reasoning_effort`/`thinking_disabled`. It deliberately diverges from
  `google-generative-ai`: **no** `2.5-flash-lite` budget table (flash-lite falls
  into the `2.5-flash` branch → minimal `128`, not `512`) and **no** Gemma 4
  special-casing (Gemma is not a Vertex Gemini model).
- The native `azure-openai` provider now resolves its endpoint and deployment
  from Pi's config-source env vars: `AZURE_OPENAI_BASE_URL` (the base URL),
  `AZURE_OPENAI_RESOURCE_NAME` (used to build a default
  `https://{name}.openai.azure.com/openai/v1` base when no base URL is set),
  `AZURE_OPENAI_DEPLOYMENT_NAME_MAP` (a `modelId=deployment,...` map overriding
  the deployment name per model id), and the existing `AZURE_OPENAI_API_VERSION`
  — matching Pi's `resolveAzureConfig`/`resolveDeploymentName` precedence. The
  pipy-only `AZURE_OPENAI_ENDPOINT` env name was dropped for parity; provider
  availability now requires `AZURE_OPENAI_API_KEY` plus one of
  `AZURE_OPENAI_BASE_URL`/`AZURE_OPENAI_RESOURCE_NAME`.
- The native `anthropic-messages` provider now emits Pi's explicit
  `thinking: {type: "disabled"}` when a reasoning-capable Claude model runs with
  thinking off/unset, instead of omitting the `thinking` key — matching Pi's
  product path (`streamSimpleAnthropic` → `buildParams` `thinkingEnabled ===
  false`). Non-reasoning models still omit `thinking` entirely, and the
  `amazon-bedrock` adapter is unchanged (Pi omits thinking fields there rather
  than sending a disabled shape).
- Bare `pipy` and `pipy "<prompt>"` now launch the interactive product session
  like Pi (a bare positional prompt seeds the first message), while
  `auth`/`run`/`repl`/`config`/`install`/... stay reachable as subcommands. A
  bare token equal to a subcommand name dispatches that subcommand; quote it via
  `pipy repl "<word>"` or `pipy -p "<word>"` to send it as a prompt instead.

### Fixed

- An installed copy (`uv tool install .`) now ships `CHANGELOG.md` in the
  wheel, so `/changelog` and the startup "What's New" notice work outside a
  checkout. Before, both found no entries.
- Found by the DF1 live dogfooding run (`docs/acceptance/2026-09-29-df1-dogfooding.md`):
  - The `edit` and `write` diffs no longer corrupt the interactive screen. They
    were written raw to the terminal mid-turn, in raw mode, so the diff
    staircased across the editor and footer rows. They now render as a
    transcript row under the tool call.
  - Tool-call rows show `$ <command> (timeout Ns)` for `bash`,
    `$ edit <path>` and `$ write <path>` instead of an argument dump such as
    `$ bash(command="…", timeout=120)`. A `!` shell command no longer shows
    as `$ $ <command>`.
  - Escape and Ctrl-C on an OpenAI Codex WebSocket stream no longer freeze
    the frame for about three seconds. Cancellation now shuts the socket down
    before the WebSocket close handshake, so `Operation aborted` shows at once.
  - A refused `/model` switch (for example to the non-tool `fake` provider)
    no longer changes the footer's thinking level. It had published the
    refused target's level (`/model fake` showed `thinking off`) while
    requests kept the old one.
- `pipy repl --native-provider fake --native-model fake-native-bootstrap` no
  longer ends the first turn with `ProviderResult.model_id must match the
  request`. The REPL runs any `fake` selection on `fake-tools`, but it stamped
  requests with the raw `--native-model` flag. The REPL now takes the provider
  and model only from its resolved selection.
- The Pi comparison gates (`scripts/parity_checks/automation_pi_comparison.py`
  and `session_tree_pi_comparison.py`) run again. pi-mono dropped `tsx`, so
  both drivers now run from source with `node` and pi-mono's source resolver,
  as pi-mono's own scripts do (Node 22.19 or later). A missing Pi reference
  now reports `passed: false` and exits 2 instead of passing.
- Shift+Tab and the `/settings` "cycle thinking level" row now rebuild the
  provider with the new level. Before, the footer showed the new level while
  requests kept the effort the provider was built with.
- The footer's thinking segment follows Pi. A reasoning model shows its live
  level, or `thinking off`; a non-reasoning model shows none. The hard-coded
  `high` (Codex GPT-5) and `default` labels are gone.
- Thinking-level changes reach the session file in the order they took effect,
  including a level clamped by an RPC model switch.

- Seven conformance gates in `scripts/parity_checks/` had gone stale against
  refactors and crashed or failed before checking the product:
  `extension_package`, `project_trust`, `extension_chrome_widgets`,
  `session_tree`, `automation_rpc`, `settings_config` and `tui_workflow`.
  They run and pass again.
- Codex GPT-5.6 Sol now reports Pi's 272K context window, in the catalog
  and in the footer meter. It previously reported 372K. The footer meter now
  reads every built-in row's context window, formatted like Pi's footer.
  Claude Sonnet 4.5/5.x show `1.0M`; the old hardcoded value was `200k`.
- Shift+Tab no longer fails on a model that does not offer `off`.

- Cross-provider product-session reopens now project each durable tool-call and
  result pair to matching target-safe provider wire IDs while retaining raw
  canonical history and JSONL.

- The legacy score test now uses its installed interpreter directly under its
  isolated home, avoiding nested `uv run` calls that could change the shared
  development environment. Explicit `PIPY_PARITY_PYTHON` mode preflights Python,
  Pytest and both installed console scripts before running the real score checks.

- Compaction now generates a semantic summary of older whole-user groups and the
  prior summary using the current provider. Retained tool exchanges remain
  verbatim. Failed, cancelled or stale generation cannot publish a summary;
  automatic cancellation stops before the ordinary request, while stale automatic
  context closes the session safely. Summary output stays private and off the
  transcript. Even label/name/custom-message writes during automatic summary
  generation invalidate it and close the stale session. Summary tokens and cost
  are not yet included in session totals. Live-provider summary quality remains
  unverified. Branch summaries also retain one coherent provider binding when
  header callbacks change the active model. Manual `/compact` now settles queued
  input when summary work ends: steering and follow-up remain deliverable, while
  Escape/Ctrl-C restores pending text to the editor for explicit submission.
- Retained extension model controls can no longer let an older coding run restore
  discarded history, append messages to the replacement context, or charge its
  usage accumulator. A context change stops the stale session lifetime through
  existing exceptional cleanup. Accepted automatic compaction remains visible
  to the same request and subsequent tool-loop continuation.
- Repeated compaction now persists the exact retained boundary even after only
  one additional user group. Resume and branch replacement restore the destination
  summary separately from conversation groups, including duplicate custom/branch
  messages, while preserving current-run counters. Unmapped durable cuts refuse
  before mutation; accepted writes retain state-first failure behavior. Summary
  generation now uses the canonical semantic-summary path.
- `--mode rpc` `get_state` and `set_model` responses no longer report a
  fabricated `fake`/`fake-tools` provider selection when the session adapter is
  built without a provider. The underlying `ValueError` now reaches the RPC
  client as a real command error response, so a misconfigured session is no
  longer indistinguishable from a working one over the protocol.
- Package-manager git ref validation now rejects a malformed percent-escape
  outright instead of silently decoding it to replacement characters and
  validating the mangled result.

- Concurrent extension coding-session controls now serialize complete provider,
  durable session-tree, custom-render, and queued-input effects. Native tree
  id/parent selection, in-memory publication, labels/names, RPC snapshots, and
  JSONL append order now share one guard, as do all coding-input check/use paths.
  Terminal shutdown waits for effects accepted before close, then detaches the
  live generation and closes its outboxes/chrome exactly once; later completion,
  custom-entry, name, label, and custom-message calls raise
  `ExtensionCapabilityError` without changing provider/session/input state,
  while read-only final-tree views remain available.
- A live extension message can no longer be erased when
  `api.send_user_message(...)` or `api.send_message(...)` races the session's
  outbox drain. Live append and detach now serialize; rejected or retired queue
  handles silently keep the existing `None` return and accumulate nothing.
- Extension reload no longer clears live retained TUI chrome before activation
  and dynamic-flag validation succeed. Invalid flags or another rejected
  candidate now preserve the prior title, header/footer/widgets, indicator,
  terminal-input listeners, autocomplete providers, editor component, and
  hidden-thinking label; rejected candidate chrome never paints and no longer
  re-fires the retained generation's `session_start`, which previously appended
  duplicate registrations and rebuilt its editor. A reload now invokes exactly
  one replacement-generation `session_start` against the candidate before
  acceptance and before accepted staged custom messages become visible, then
  publishes one coherent flags/tools/renderers/hooks/providers/menu/chrome
  generation. The publication gate remains active through
  accepted staged delivery, two-phase route release, and gate drain—even while
  those paths invoke extension-visible sinks after the session commit unlocks—
  before chrome reconciliation. Retained active-tool and thinking controls now
  stay bound to their creating generation: stale, publication-pending, and
  post-run calls return `False` without changing tool visibility, thinking
  state, the session tree, persisted JSONL, or the footer. Thinking commits and
  durable entries also preserve one order under concurrent callers. Retained
  model controls now use the same generation, publication-gate, and terminal
  refusal boundary: provider/catalog construction is prepared outside the
  session mutex, the selection plus coding provider/history/usage commit is
  atomic under it, and footer/default persistence follows after unlock. A stale,
  gated, terminal, failed-construction, or superseded prepared model returns
  `False` without rebinding or persisting; a callable first released after
  teardown cannot construct a provider. If lifecycle, provider/catalog, or
  final chrome preparation then refuses the reload,
  non-staged, non-chrome lifecycle effects such as `notify` may already have
  occurred; candidate chrome is discarded and all candidate staged messages
  that the earlier reload path could expose are suppressed. This is part of the
  `session_start`-before-acceptance ordering change, not another behavior delta.
  Registrations it does not rebuild
  (including autocomplete, a custom editor, and the hidden-thinking label) are
  cleared, while custom-editor text returns to the built-in editor. Late
  retained writes to a rejected candidate's closed sink are ignored with their
  prior `None` return shape, while `on_terminal_input()` returns an inert
  disposer. Concurrent owner rotation during candidate-host publication now
  refuses before any generation/provider/tool/renderer write, preserving the
  newer live state while retiring the terminal candidate route and closing its
  chrome. Concurrent retained writes still cross accepted chrome reconciliation
  through a short effect-free ownership handoff; post-publication interrupts
  propagate after that reconciliation leaves the replacement explicitly live,
  without double close. Ordinary retained UI contexts now stay bound to their
  originating chrome handle, so writes through a retired handle are ignored
  instead of retargeting the replacement; terminal teardown invocation remains
  a separate follow-on.
- Extension activation APIs retained after successful activation or candidate
  rejection can no longer appear to register commands, shortcuts, hooks, tools,
  providers, flags, or renderers into dead candidate state. Late contribution
  registration now raises `ExtensionCapabilityError` at the call boundary,
  `(str, Enum)` names and hostile `str.__str__` overrides retain their exact
  underlying registration and `get_flag(...)` lookup value, and messages racing
  after the activation seal
  can no longer leak into (or mutate) the accepted frozen activation snapshot.
  Unexpected extension-controlled normalization/copy exceptions now fail closed
  to the registration family's bounded invalid reason and type-only diagnostic;
  the first failure remains recorded even when extension code catches the raised
  validation error, so this hostile case does not preserve exact pre-R1 reason
  behavior. Provider-only catalog scans now terminally finalize accepted hosts:
  staging, sends, and publication are inert, while guarded registration-time
  default flag reads stay available to detached provider factories that captured
  `api`; the catalog helper does not parse/apply CLI tokens, while live session
  activation still exposes parsed overrides. Rejected or abandoned activation
  disposal still clears flag values.
- Calling `unregister_provider(...)` with a non-string, empty/whitespace-only,
  or slash-containing provider name now records an `invalid_provider` activation
  failure and disables that extension, instead of silently staging or ignoring
  the malformed unregistration request.
- Escaped adapter exceptions no longer copy raw exception messages into the
  durable metadata archive or its Markdown summary. Those records retain only
  the bounded exception type and fixed lifecycle metadata, while the in-memory
  `RunResult` keeps its existing caller-facing failure detail.
- Registered tools omitted from the exact provider request can no longer reach
  extension tool hooks or execution when a provider returns them anyway. They
  now produce the normal balanced, budget-consuming `unknown tool` result with
  provider correlation and pipy request identities intact, without incrementing
  malformed-call or real-invocation counters or invoking extension custom
  renderers. A tool activated by an earlier call is not authorized later in the
  same provider response.
- Extension `deliverAs=nextTurn` custom context is now an identity-anchored,
  request-only overlay for exactly the next accepted run. It remains visible
  through every provider/tool iteration without entering canonical history or
  run results, hook prompt transforms cannot confuse it with equal text, and
  automatic compaction continues safely on durable history during the run.
- Valid model-selected tool executions that return error observations (for
  example read failures or timed-out shell commands) no longer count as
  malformed provider tool calls, so the product tool loop keeps feeding those
  observations back to the model instead of aborting after three such errors.
- Terminal color detection now uses truecolor RGB styling only when the
  terminal explicitly advertises it (`COLORTERM=truecolor`/`24bit` or a
  direct-color `TERM`), so ordinary `*-256color` sessions use Pi's 256-color
  fallback palette instead of displaying wrong chrome colors.
- Slash/local commands such as `/quit` now remain editable and submittable while
  a `!` shell shortcut or model-driven bash tool is streaming output, so
  long-running tests no longer trap the user in the product TUI.
- Raw terminal input now preserves UTF-8 prompt text in the product TUI and
  slash-menu editor, so non-ASCII characters such as `ö` no longer render as
  replacement characters or reach the provider corrupted.
- The interactive TUI now paints edge-to-edge at the true terminal width,
  matching Pi, removing the blank right-hand column. Full-row elements — the
  user-message and tool/bash background bands, the input-frame separators, and
  the bottom status line — now reach the final column instead of stopping one
  short. The input line keeps its one-column cursor-safety margin internally, so
  the hardware cursor still never lands in the last column.

### Changed

- Product TUI long editable prompts now soft-wrap inside the input frame instead
  of horizontally scrolling in one row. Cursor movement maps across wrapped
  rows, footer/status rows stay pinned, and long typed/pasted input plus resize
  are covered by real-PTY tests at 80x24 and 100x40.
- The pipy-only metadata-only `--resume RECORD` / `--branch LABEL` repl flags
  are retired: the native session tree is the product session source. The
  separate `pipy-session resume-info` archive utility is unchanged.

### Added

- Provider/model user documentation now covers listing models, provider/model
  selection, credentials, `models.json`, ds4, thinking/images metadata, and
  current provider follow-ons.
- Settings and keybindings user documentation now covers pipy's global/project
  settings files, reload workflow, common Pi-shaped fields and pipy
  divergences, key syntax, namespaced action ids, defaults, and customization
  examples.
- Session and compaction user documentation now covers the native product
  session tree, startup flags, `/session`/`/resume`/`/tree`/`/fork`/`/clone`,
  durable compaction, export/share pointers, and the separate `pipy-session`
  metadata/catalog utility.
- feat(extension-api): editor text helpers for command/shortcut contexts.
  Extensions can read the core prompt buffer, replace it, or paste literal text
  at the current cursor via `ctx.ui.get_editor_text()`,
  `ctx.ui.set_editor_text(text)`, and `ctx.ui.paste_to_editor(text)`. Headless
  reads return `""`; headless writes and pastes no-op deterministically.
- feat(extension-api): theme controls for command/shortcut contexts (rich-UI
  item E). Extensions can read and switch the chrome theme via `ctx.ui.theme`
  (the active `ChromePalette`), `ctx.ui.get_all_themes()` (`{"name", "path"}`
  per available theme, default first), `ctx.ui.get_theme(name)` (load a palette
  without switching; `None` when unknown), and
  `ctx.ui.set_theme(name_or_palette)` (`{"success", "error"}`), mirroring Pi's
  `theme`/`getAllThemes`/`getTheme`/`setTheme`. Reads are ambient (the global
  package theme registry plus `PIPY_THEME`/the chrome store) and work
  deterministically even headless; `set_theme` requires a live UI and returns
  `{"success": False, "error": "UI not available"}` headless without mutating
  process state, while a live call reuses the `/settings` `select_theme`
  mechanism so the next frame repaints. `get_all_themes()` keeps Pi's
  `{name, path}` shape but `path` is always `None` (the session theme registry
  retains only `name -> palette`; package theme file paths are not exposed to
  extension code).
- feat(extension-api): rich message renderers — slice C. A
  `register_message_renderer` renderer may now return a themed component via a
  required second `(data, ctx)` parameter (a `MessageRenderContext` with
  `custom_type`/`data`/`expanded`/`width`/`theme`); the component is committed
  SGR-preserving with no forced `[custom_type]` label, render-once at the
  append-time width, and fail-soft to the plain path. A 1-arg `renderer(data)`
  (including the capture-default idiom) keeps its existing plain-text behavior;
  the context parameter must be required (`def render(data, ctx)`, not
  `ctx=None`). The rendered body is live-only and never archived. Active-branch
  custom entries now replay into startup-opened TUI sessions, including
  `--session`/`--continue`/`--resume-session` opens, without mutating the
  session file. `ctx.send_message` / persisted `CustomMessageEntry` values
  now render through registered message renderers when present, receiving a
  Pi-shaped payload with `customType`, `content`, `display`, and `details`,
  while no-renderer cases continue to display stored content. Deferred:
  streaming `deliverAs`/`triggerTurn` follow-ons beyond the shipped idle and
  queued paths.
- feat(extension-api): persistent chrome widgets (set_widget/set_header/
  set_footer/set_title/set_working_indicator) — slice B. Extensions can pin an
  above/below-editor widget, an exclusive custom header and footer (with git
  branch via `FooterData`), the terminal title, and a custom working indicator;
  chrome re-renders width-reactively, falls back fail-soft, disposes components
  on replace/clear/reload, and renders live from `session_start` in an
  interactive TTY.
- Extensions can render their own tool call/result rows (`render_call`/
  `render_result`) with themed color.
- Discovered skills are now advertised in the tool-loop system prompt when the
  `read` tool is available, matching Pi's skill model: each skill contributes an
  `<available_skills>` entry (name, description, and absolute location), and the
  model loads a skill's body on demand via the `read` tool. Each skill's parent
  directory is added to the read-only reference roots so the model can read skill
  bodies (including global skills outside the workspace). The `/skill` command is
  kept, and the archive-safe skill metadata (path label, sha256, byte length,
  truncated, name) is unchanged.
- Theme selection now lives in the `/settings` dialog as a theme row + picker
  (matching Pi, which has no `/theme` command); the chosen theme persists through
  settings and re-colors the chrome on the next frame.
- Python extensions can now register custom session-entry renderers with
  `api.register_message_renderer(...)`; command and shortcut handlers can call
  `ctx.append_entry(...)` to persist JSON-safe `custom` entries in the native
  product session tree and render them in the product TUI or captured-stream
  diagnostics without starting a provider turn.
- Python extension command/shortcut contexts now expose simple Pi-shaped UI
  primitives: `ctx.ui.select`, `ctx.ui.input`, `ctx.ui.confirm`,
  `ctx.ui.set_status`, `ctx.ui.set_working_message`, and
  `ctx.ui.set_working_visible`. Interactive product-TUI runs use simple
  overlays, live status rows, and sticky provider-turn working controls;
  headless runs return cancel/default values without blocking.
- Python extensions can now register dynamic `pipy repl` tool-loop CLI flags
  with `ExtensionFlag`; parsed values are available to extension commands,
  shortcuts, hooks, and tools through `ctx.flags`.
- Python extensions can now participate in live product-session operations:
  `user_bash` hooks may block, rewrite, exclude, or synthesize `!`/`!!` shell
  shortcut results; `before_provider_request` hooks may transform bounded
  provider request fields and narrow model-visible tools for the current
  request; `session_before_switch`, `session_before_fork`,
  `session_before_compact`, and `session_before_tree` hooks may gate stateful
  session operations; and safe command/shortcut/pre-turn contexts expose
  `ctx.set_active_tools(...)`, `ctx.set_model(...)`, and
  `ctx.set_thinking_level(...)` through the native provider/session/tool
  boundaries. The new live-session parity gate is
  `scripts/parity_checks/extension_live_session_conformance.py --json`.
- Pi-shaped per-run source-loading flags for `pipy repl`: `--extension`/`-e`,
  `--no-extensions`/`-ne`, `--skill`, `--no-skills`/`-ns`,
  `--prompt-template`, `--no-prompt-templates`/`-np`, `--theme`, and
  `--no-themes`. Explicit CLI paths are temporary session sources that load
  before workspace/global/package defaults, survive matching `--no-*`
  discovery cutoffs, and override persisted `+/-pattern` resource filters while
  keeping `enable_skill_commands=false` as a hard skill-command disable.
- Native product export/import/share and self-update planning:
  - `/export` writes a self-contained HTML export of the full native session
    tree; `/export <path.jsonl>` writes the active branch as a linearly
    re-chained portable JSONL file.
  - `/import <path.jsonl>` copies a portable JSONL file into the native session
    store and resumes it after confirmation; `--yes` is accepted for
    noninteractive command scripts.
  - `pipy --export <session.jsonl> [output.html]` exports an existing native
    session file to HTML and exits.
  - `/share` uploads the HTML export as a secret GitHub gist through a stdlib
    GitHub API boundary using `GITHUB_TOKEN`/`GH_TOKEN` or `gh auth token`.
  - `pipy update self|pipy [--force] [--dry-run]` plans install-method-aware
    self-update commands for `uv tool`, `pipx`, `pip`, and user `pip`, while
    unknown/development installs and unconfigured package names fail safe with
    manual instructions.
  - New gate:
    `scripts/parity_checks/export_distribution_conformance.py --json`.
- Pi-style extension **package manager CLI** for local-path and managed git
  package sources ([docs/extension-api.md](docs/extension-api.md)): `pipy
  install <source> [-l]`, `pipy remove`/`pipy uninstall <source> [-l]`, and
  `pipy list` record and report package sources in a `packages` array in user
  `<config>/settings.json` or project `<cwd>/.pipy/settings.json` (with `-l`),
  preserving object-form `{source, ...}` entries. Supported git sources clone
  into pipy's managed package cache (`<config>/git` for user scope,
  `<cwd>/.pipy/git` for project scope), `pipy update --extensions`, `pipy
  update --extension <source>`, `pipy update <source>`, and bare `pipy update`
  refresh managed git packages through bounded fetch/reset, and local-path
  package updates are skipped as no-ops. `pipy config <enable|disable>
  <skill|prompt|theme|extension> <name>` writes Pi-shaped `+pattern`/`-pattern`
  resource filters without deleting discovered resources. PyPI/npm,
  `git+...`, credentialed URL userinfo, and ambiguous unsupported remote
  schemes fail closed; a missing path fails closed, removing an unconfigured
  source exits non-zero, a corrupt settings file is never overwritten, and no
  package lifecycle scripts run.
- Pi-style extension **package runtime composition**: installed local-path and
  managed git packages now contribute skills, prompts, themes, and Python
  extensions to a session through discovery
  ([docs/extension-api.md](docs/extension-api.md)). A package declares its
  resources in an optional `pipy-package.toml [resources]` table (mapping Pi's
  `pi.{extensions,skills,prompts,themes}`) or via convention subdirectories.
  Contributed resources are discovered at lowest precedence (a workspace/global
  resource wins a name collision), are name-deduped first-wins, and honor both
  the global `pipy config` `+pattern`/`-pattern` filters and a package's own
  object-form `{source, skills, prompts, themes}` filters. Runtime startup never
  clones or fetches git sources; it only reads already installed cache paths and
  preserves user/project cache scope when resolving configured git packages.
  This adds file-based chrome themes: a package theme `.toml` becomes
  selectable with `/theme <name>` and re-colors the chrome. `pipy config` lists
  package-contributed resources, and `/reload` re-discovers them. Package source
  paths and resource bodies never enter the default metadata archive. Remote
  PyPI/npm package installation remains deferred pending a broader supply-chain
  policy. See the example package `docs/examples/packages/demo-pack/`.
- Pi-style session startup flags and an interactive session picker for the
  native product session tree ([docs/session-tree.md](docs/session-tree.md)):
  - new startup flags `--session-id <id>` (open the native session with this
    exact id, or create one carrying it), `--session-dir <dir>` (native session
    store root override — the separate `$PIPY_SESSION_DIR` metadata-archive root
    is never reused for it), and `-n`/`--name <name>` (name the session at
    startup), alongside the existing `-c`/`-r`/`--session`/`--fork`/
    `--no-session`.
  - Pi mutual-exclusion errors: `--fork` and `--session-id` each conflict with
    `--session`/`--continue`/`--resume-session`/`--no-session`.
  - cross-project `--session <partial-id>`: a partial id that matches only a
    session in a different project prompts to fork it into the current
    workspace, aborting cleanly if declined.
  - `/resume` opens an interactive picker overlay on a TTY — type to search,
    `Tab` toggles current-project/all-projects scope, `Ctrl+P` the path column,
    `Ctrl+S` the sort, `Ctrl+N` named-only, `Ctrl+R` renames, `Ctrl+X` deletes
    after a `[y/N]` confirmation (the active session is protected), Enter opens,
    `Esc`/`Ctrl+C`/`Ctrl+D` cancel. It renders inline (no alternate screen),
    repaints on resize, runs no provider turn, and sanitizes user-controlled
    names/paths against terminal escape injection. `-r` opens the same picker at
    startup on a TTY; a non-TTY stream keeps the deterministic listing plus the
    `named`/`rename`/`delete --yes` subcommands and continues the most recent
    session.
  - a Pi comparison gate (`scripts/parity_checks/session_tree_pi_comparison.py
    --json`) runs the canonical tree workflow against Pi's real `SessionManager`
    and asserts matching name, branch/leaf chains, fork semantics, and durable
    reconstruction; the extended `session_tree_conformance.py` proves the new
    flags and picker rows/actions through the product paths.
- Pi-style headless automation surfaces for the product tool loop, through
  pipy-owned stdlib boundaries with no new runtime dependency
  ([docs/automation-rpc.md](docs/automation-rpc.md)):
  - `pipy repl --mode json "<prompt>"` runs one non-interactive turn and emits
    the native session header line followed by the full Pi-shaped session event
    stream (`agent_start`/`turn_start`/`message_start`/`message_update` with a
    `text_delta` `assistantMessageEvent`/`message_end`/`turn_end`/`agent_end`
    and `tool_execution_*`) as strict LF-only JSONL on stdout; diagnostics stay
    on stderr. Full assistant/tool/bash content is emitted like Pi; auth
    secrets/tokens are never emitted.
  - `pipy repl --print`/`-p "<prompt>"` prints only the final assistant text to
    stdout (Pi `-p`); failures go to stderr with a non-zero exit.
  - `pipy repl --mode rpc` starts a long-lived stdin/stdout JSONL protocol with
    Pi's command names: async `prompt` (correlated success then streamed
    events); `steer`/`follow_up` (queued during an active run and delivered as
    the next run after it settles, one message per turn boundary
    steering-then-follow-up, each observable via `queue_update` and counted in
    `pendingMessageCount`) and `abort` (cancels the active
    run; queued steering for that run is discarded) — a documented pipy boundary
    over Pi's in-turn injection; `bash` (on a worker thread; `abort_bash` errors
    while a sandboxed bash is in flight rather than falsely claiming a cancel);
    `get_state`/`get_messages`/`get_session_stats`/
    `get_last_assistant_text`, `set_session_name`, and queue-mode commands;
    model/thinking commands are accepted and reflected in `get_state`/events but
    do not yet switch the live provider or thread the thinking level into the
    running provider request (a documented follow-on); and well-formed error
    responses for unimplemented commands. All 29 Pi RPC command types are
    accepted; unknown commands and unparseable lines return well-formed error
    responses, never a crash. The native session
    tree is the introspection source; events derive from the real tool-loop
    run, not a parallel model.
  - The legacy metadata-only `--native-output json` on `pipy run` is deprecated
    in favor of `--mode json`; its `--help` now points there.
  - The session event grammar matches Pi's: after `turn_start` the user
    message emits its own `message_start`/`message_end` pair before the
    assistant message begins.
  - Gated by `scripts/parity_checks/automation_rpc_conformance.py --json` and
    `tests/test_native_automation_*.py`, plus a deterministic Pi-vs-pipy
    comparison (`scripts/parity_checks/automation_pi_comparison.py --json` with
    `scripts/parity_checks/pi_faux_event_driver.mts`) that drives the real local
    Pi and pipy with offline providers and asserts matching normalized event
    order/discriminators, assistant text + delta concatenation, `agent_end`
    semantics, and durable session-tree reconstruction.
- Pi-style interactive TUI/editor workflow depth for the product tool-loop
  terminal (`pipy repl --agent pipy-native --repl-mode tool-loop`), all through
  pipy-owned stdlib boundaries with no new runtime dependency and the inline
  (no-alternate-screen) contract preserved:
  - `@` file picker with Pi exact/prefix/substring ranking (not fuzzy) over a
    bounded, `.git`/ignored-aware workspace walk, and general Tab path
    completion (prefix-match, dirs-first, `~/` expansion, space-quoting) that is
    a no-op in prose.
  - Local `!`/`!!` shell shortcuts reusing the real bash execution boundary,
    with a bash-mode input affordance, context (`!`) vs no-context (`!!`)
    recording, and Escape cancellation of a running command.
  - `Shift+Tab` thinking-level cycling (off→minimal→low→medium→high, clamped to
    model reasoning support, recorded as a `thinking_level_change` native-tree
    entry) and `Ctrl+P`/`Shift+Ctrl+P` model cycling over the scoped/available
    set.
  - `Ctrl+O` tool-output expansion and `Ctrl+T` thinking-block fold as renderer
    view flags (the thinking fold persisted to `hideThinkingBlock`).
  - Queued steering / follow-up during active turns (`Alt+Enter` follow-up,
    `Alt+Up` restore-to-editor), a pending-messages region, steering-then-
    follow-up drain order, and steering interruption via the existing cancel
    token.
  - Clipboard image paste (`Ctrl+V`, owner-only temp file under an image
    reference root) and terminal drag-drop file references; image bytes never
    reach the metadata archive.
  - A `/scoped-models` multi-select overlay defining the Ctrl+P cycle set, new
    `/settings` actionable rows (tool-output/thinking folds, thinking-level
    cycle, scoped models), and startup hints + `/hotkeys` advertising every
    binding.
  - The terminal-native mouse-selection invariant: the renderer never enables
    xterm mouse tracking, so click-drag selection over scrollback keeps working.
  - New gate `scripts/parity_checks/tui_workflow_conformance.py --json` drives
    the real product PTY path and proves all of the above (plus non-TTY
    fallbacks and archive privacy) deterministically.
- User-facing terminal setup and tmux setup docs now cover pipy's inline TUI,
  modified-key expectations, bracketed paste, file/image drops, clipboard
  behavior, scrollback, and common platform caveats.

### Fixed

- A `/…` slash command or `!…` bash shortcut submitted with Enter mid-turn now
  runs locally (matching Pi's editor `onSubmit`): it interrupts the turn and
  dispatches through the normal local-command path instead of being steered to
  the model. Only ordinary prose becomes a steering message, so the queue lanes
  hold prompt text exclusively.
- Queued steering/follow-up messages that begin with `/` or `!` (including an
  `Alt+Enter` follow-up or an RPC `steer`/`follow_up`) now reach the model
  verbatim when the queue drains.
  Previously a queued line starting with a slash-command or `!`-shell prefix was
  re-interpreted as a local command on delivery and silently dropped from the
  conversation; drained messages are provider-visible prompt text and bypass
  local-command dispatch (they still resolve any `@file`/`@image` references).
- Moving the caret (`←`/`→`/`Home`/`End`) now dismisses the `@`/path completion
  popup. Previously the popup stayed anchored to the caret offset where it
  opened, so accepting after a move spliced the candidate at a stale offset and
  duplicated/corrupted the active token; it reopens on the next edit.
- Aborting (Escape/Ctrl-C) or restoring (`Alt+Up`) while a queued turn is
  draining now brings the remaining queued prompts back to the editor. Once a
  turn settled (or steering promoted), the queue moved into an internal drain
  that the restore path ignored, so the not-yet-delivered prompts stayed hidden
  and kept auto-submitting to the model after the cancellation; they are now
  restored along with the steering/follow-up lanes.
- `Ctrl+V` clipboard-image reads are bounded and isolated: the helper's stdin is
  `/dev/null` and the read enforces a wall-clock deadline, so a misbehaving
  clipboard tool (one that hangs or never closes its output) can no longer
  freeze the editor or consume terminal keystrokes.
- Tab path completion no longer offers ignored/generated entries (e.g.
  `node_modules/`) or symlinks escaping the workspace for workspace-relative
  directories, matching the `@` picker and the read policy; explicit
  absolute/`~/` navigation the user points Tab at is still listed as-is.
- OpenAI-Codex streams now use a configurable 300-second header/body idle
  timeout by default instead of the former hard-coded 60-second socket timeout.
  Recognized connection, timeout, reset, and truncated-stream failures become
  sanitized provider failures, while deliberate cancellation remains immediate
  and non-retryable.
- OpenAI-Codex now retries bounded transient HTTP and transport failures across
  the complete request-plus-stream attempt, with cancellation-aware exponential
  backoff and capped `Retry-After` support. Retries stop before replay once any
  provider event is parsed, preventing duplicate visible text, reasoning, tool
  calls, or tool effects.
- OpenAI-Codex now honors `transport: auto|sse|websocket` with a real Responses
  WebSocket path, Pi-shaped pre-event SSE fallback, WebSocket connect timeout
  settings, auto-mode fallback memory, and no post-event fallback or replay.
- The parity runner now records child-attempt start/finish events, distinguishes
  runner timeouts from signal exits, and narrowly retries a legacy raw
  `pipy: The read operation timed out` child tail only when no branch, HEAD,
  ref, or worktree progress occurred.

## [0.1.0] - 2026-06-03

### Added

- Pi-style settings/config/keybindings system for the native runtime:
  - Layered `settings.json` (global `<config>/settings.json` on the
    `PIPY_CONFIG_HOME` → `${XDG_CONFIG_HOME}/pipy` → `~/.config/pipy` chain, plus
    project `.pipy/settings.json`) with Pi migrations, one-level deep merge with
    project precedence, CLI/env overrides, parse-error isolation, and
    field-scoped lock-guarded writes that preserve unknown keys.
  - `keybindings.json` with the default editor/app binding table (single key
    spec or array of alternatives), legacy-name migration, malformed-file
    fallback to defaults, and `/hotkeys` rendered from the resolved manager.
  - Settings drive `defaultProvider`/`defaultModel`, `theme`, `quietStartup`,
    `promptHistory.enabled`, and `autocompleteMaxVisible` at startup; `/settings`
    reports the resolved configuration.
  - System-prompt inputs: `--system-prompt`, repeatable `--append-system-prompt`,
    `SYSTEM.md` / `APPEND_SYSTEM.md` auto-discovery, and `--no-context-files`/
    `-nc`.
  - `retry.*` feeds the provider HTTP retry policy and `compaction.enabled`
    gates auto-compaction.
  - Scoped models: `enabledModels` + `/scoped-models` (view/set/clear/cycle) and
    Ctrl+P forward cycling.
  - Resource enablement via `pipy config` (`-pattern`/`+pattern` over
    `skills`/`prompts`/`themes`/`extensions`) and `enableSkillCommands`.
  - `/reload` re-reads settings, keybindings, resources, and theme.
  - `/changelog` and the `--version` surface.
- Provider/model catalog closeout for the native runtime:
  - Catalog-backed provider construction now covers the OpenAI-compatible Chat
    Completions family, implemented catalog-constructed non-completions
    families, `pipy run` one-shot construction, and startup
    `--native-provider`/`--native-model` resolution through the shared resolver.
  - Extension-registered providers now contribute temporary per-run catalog
    rows: they appear in `--list-models`, resolve at startup when the extension
    is loaded, switch via `/model`, recompute on `/reload`, and construct
    through the extension `ProviderPort` factory without persisting package or
    catalog state.
  - The provider catalog conformance gate covers Verification-Plan items 1-25
    with deterministic fake HTTP/product-path checks and no network access.
- True active-turn provider-request cancellation for the native tool loop:
  Escape and Ctrl-C each thread a per-turn `CancelToken`
  (`pipy_harness.native.cancellation`) into `ProviderPort.complete(...)` that
  shuts the live `urllib`/SSE connection down — during the header wait or the
  body/stream read — so the worker's blocking read raises
  `ProviderCancelledError` instead of finishing the request; the worker is then
  best-effort joined and the loop renders Pi-style red `Operation aborted`
  without appending an assistant/tool observation. The socket-shutdown read
  path tolerates the `http.client` `_close_conn` shutdown race (a concurrent
  `fp = None` surfacing as `AttributeError`) by mapping it to cancellation only
  when the token is cancelled, so an aborted body read cannot leak a spurious
  provider error.
- Python SDK/headless embedding documentation for `pipy_harness.sdk`, including
  the current one-shot in-process surface, fake-provider default, current limits,
  and relationship to planned JSON/RPC automation.
