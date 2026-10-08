# Python codemode sandbox (CM1, groundwork)

Status: **sandbox core and T4–T8 implemented; T9 deterministic delivery tests
and live tmux verification completed;
T10 live measurement and interactive acceptance are completed; T11 final delivery remains pending.**
The internal composite runner now retains bounded nested-call evidence on its
parent result and reconstructs child lines on resume and tree navigation.
T8a adds the stable builtin definition, runtime-management CLI and composite
output spilling. T8b adds public opt-in selection/settings/availability and production
registration. T9 now exercises public session/adapter and CLI delivery with
the installed WASI interpreter. Live tmux evidence is recorded in the
[acceptance note](specs/2026-10-09-python-codemode-acceptance.md); measurement and final delivery remain in
[spike result](specs/2026-10-06-python-codemode-spike.md) (§5). The
[light plan](specs/2026-10-04-python-codemode-plan.md) has the scope. This page
documents enabling codemode, installing the runtime, checking availability
and the internal API.

## Deterministic delivery evidence (T9)

`tests/test_native_codemode_delivery.py` runs the existing programmable
`FakeNativeProvider` through public `CodingSession`/`CodingSessionAdapter`,
the actual production registry and the real installed WASI runtime. The same
three-file read/filter task runs through CLI parsing and the print, JSON and
RPC mode controllers. It asserts the exact advertised public definition, a
compact structured summary, all read arguments/statuses in the parent record,
child `parentToolCallId` events and parent details, and parent-only provider
history/persistence. Product extension hooks block a nested write and transform
a nested read even after the active selection changes inside the parent hook;
ordinary direct calls keep their behavior. Malformed nested arguments and budget
exhaustion remain catchable `ToolError`s. RPC state queries and thread-owned
abort stay live during real bash and compute, reap workers and allow a fresh
successful turn. Existing T8 tests cover disabled/unavailable selection and
the single warning; their contracts are unchanged.

`tests/test_native_codemode_delivery_pty.py` drives the full session on a real
PTY, sends Escape through the normal input waiter, checks active/settled/cancelled
child markers, persisted results, worker-group cleanup, fresh execution and
startup replay through the actual `SessionHistoryRenderer`. These are automated
PTY assertions. Separate coordinator tmux captures and inspected active raw frames
now establish the terminal evidence in the [acceptance note](specs/2026-10-09-python-codemode-acceptance.md).

The Linux x86_64 `codemode-probes` CI job retains all existing isolation/core
probes and `PIPY_CODEMODE_REQUIRE_RUNTIME=1`. Its explicit list now also runs
the real composite integration, T8b public write-then-raise coverage (C), and
T9 delivery/PTY tests. The new tests fail if the runtime is required but missing;
ordinary local runs skip with the actual missing-dependency/runtime reason.
No test installs a runtime or selects a network model.

Run the new tests with:

```sh
PIPY_CODEMODE_REQUIRE_RUNTIME=1 uv run pytest -q tests/test_native_codemode_delivery.py tests/test_native_codemode_delivery_pty.py
```

### Coordinator terminal capture

Check the installed runtime with `uv run python -m pipy_harness.native.codemode
status`. On a real tty (or a coordinator-owned tmux pane), from the checkout:

```sh
uv run python tests/codemode_tui_evidence.py --workspace /tmp/pipy-cm1-t9-success --phase success
```

Use a fresh directory per run. Type `summarize fixtures` and Enter. The existing
fake issues one public codemode call: builtin bash sleeps three seconds, then
real reads of `alpha.txt`, `beta.txt`, `gamma.txt` filter `KEEP` lines and emit
`{"matches": [...], "count": 3}`. Capture `… bash` while active, then settled
`✓ read` lines with paths and durations. Wait for the editor to return after
the summary before typing another command; the active-turn watcher owns input
until then. Ctrl+O expands tool evidence. `/exit` settles the driver assertions.

For cancellation, use separate fresh roots:

```sh
uv run python tests/codemode_tui_evidence.py --workspace /tmp/pipy-cm1-t9-bash --phase bash
uv run python tests/codemode_tui_evidence.py --workspace /tmp/pipy-cm1-t9-compute --phase compute
```

Type the same initial prompt. In `bash`, wait for `… bash` and the fixture
`bash-active` file (created by the actual builtin command), then send Escape
while its 30-second sleep is active. Capture `⊘ bash` and `Script aborted`.
In `compute`, the first real read settles (`phase.txt` becomes `settled-read`),
then the guest loops indefinitely; send Escape after that phase. After the
aborted result and editor return, type `fresh summary` and Enter. It must
produce the successful three-read summary. Then `/exit` verifies both parent
records and that every observed worker process group is gone. The driver only
observes the standard subprocess launch boundary; argv, runtime, tools,
cancellation and UI behavior are unchanged.

Each root contains the three fixture text files, `phase.txt`, `parents.json`,
`worker-pids.json`, `runtime-check.json`, `session-path.txt` and, after a successful
exit, `verified.json`. Full product JSONL sessions live in `sessions/`; only
parent results persist. No credentials are written. `verified.json` is assertion
evidence, not a screenshot or live-provider quality claim.

Replay the exact recorded tree in a second isolated tty root:

```sh
uv run python tests/codemode_tui_evidence.py --workspace /tmp/pipy-cm1-t9-replay --replay "$(cat /tmp/pipy-cm1-t9-success/session-path.txt)"
```

Startup uses product history reconstruction, with no provider call required.
Inspect the settled nested reads (and cancelled bash when replaying its session),
then use the normal `/tree` command to inspect/navigation-select the existing
branch. `/resume` remains available through the product command path. The driver
never manually renders children. Exit with `/exit`.

The driver preserves `HOME`: runtime lookup is
`Path.home()/.local/state/pipy/codemode`, independent of `XDG_STATE_HOME` and
`PIPY_CONFIG_HOME`. It isolates config, native theme/defaults, prompt history,
auth and product-session paths under the controlled workspace, selects `pi`,
and uses no global settings/theme writes. T10 live-provider comparison and T11
cumulative checks/review are intentionally separate. The coordinator captured
and inspected active, settled, cancelled and replay/tree frames on 2026-10-09;
see the [acceptance note](specs/2026-10-09-python-codemode-acceptance.md) for retained artifacts and limitations.

T4 separates the agent loop's internal settle and record paths without changing
behaviour. Settlement owns admission, hooks, execution, result transformation
and validation, and existing tool events. Recording owns top-level results,
model history, skipped calls and terminal handling. For direct calls, budget state is published
early and other state publication keeps its existing recording boundary. This is
the settlement seam reused by T6a; it remains internal.

T5 adds pure policy transitions, using the existing admission, product-blocking
and execution-settlement functions with `AgentToolInvocationMode`. After core
admission and product preflight allow a composite parent, `reserve_composite_parent`
reserves one slot. Nested admission checks used slots plus that reservation before
authorization. Settlement with `PARENT` releases it exactly once in the caller's
state sequence; a second parent settlement or a nested call after release is
rejected. T6a owns that sequence and closes the service before parent settlement.

| Outcome | Turn slots | Execution count | Malformed accounting |
| --- | --- | --- | --- |
| Nested success or tool error | +1 | +1 | direct streak unchanged |
| Nested unauthorized or blocked | +1 | unchanged | unchanged |
| Nested malformed | +1 | unchanged | run-owned `nested_malformed_count` +1; no fatal failure, direct streak unchanged |
| Nested interrupted | unchanged | unchanged | unchanged; parent reservation retained |
| Nested exhausted | unchanged | unchanged | unchanged; exhaustion count +1, no fatal failure |
| Parent success or tool error | reserved slot consumed | +1 | direct streak reset |
| Parent malformed | reservation released, no slot consumed | unchanged | existing direct count/streak +1, fatal at three |
| Parent interrupted | reservation released, no slot consumed | unchanged | unchanged |

Reservation and nested malformed count are transient run-owned state, validated
at policy and publication boundaries. Session publication retains the existing
cumulative counters only; a new run starts with no reservation and zero nested
malformed count. T6 returns exhausted and malformed child statuses internally and translates
them to catchable guest `ToolError` values. Direct calls,
callbacks and top-level interruption behavior are unchanged.

T6a adds `agent/nested_calls.py` and optional `AgentCompositeToolRunner` dispatch
in the canonical loop. Dispatch happens only for `codemode` after core admission
and product preflight, and is absent by default. Existing direct callers,
including an ordinary extension named `codemode`, retain their behavior.
The coding coordinator forwards the optional ports. T6b wires the real runner at
the REPL composition seam for both interactive and headless/RPC waiters. A frozen
product identity guard requires an advertised native builtin `codemode`, excluding
extension overrides. T8b registers the public definition only when selected and available. Existing internal test ports can omit the optional guard.

Each admitted parent owns a fresh sequential service on the session thread.
Child calls reuse shared admission/settlement policy, product hooks, the normal
executor and waiter, and result transformation/validation. Eligibility is a
separate injectable port, read once from the frozen projection: only builtins
`read`, `ls`, `grep`, `find`, `write`, `edit`, `bash` can execute, and extension
replacements, extension names and recursion are refused. Hidden builtin names
remain unauthorized against the parent's request snapshot. Definitions are never
refreshed inside a parent, preserving tool and hook generations.

Every validated child settlement publishes policy state through the existing
status port on the session thread; exhaustion publishes before error construction.
Session state mirrors cumulative counters, so a later canonical hook failure
cannot discard accepted child or exhaustion counts. Reservation acquisition and
validation are inside the cleanup guard. Parent settlement publishes released
state before parent result transformation; exceptional cleanup releases and
publishes any acquired reservation without inventing a settlement. Direct-call
publication ordering is unchanged. Parent settlement reads the latest child state
and preserves the first operator/local-command child interruption when a runner
returns `SETTLED`. This sticky, read-only, session-thread-owned interruption is
independent of retained evidence limits. Runner exceptions and invalid runner results
become parent errors; canonical child hook/executor/validation exceptions still
propagate. Cleanup also releases reservations on propagated exceptions.
Wrong-thread calls are refused without touching mutable session state; records, interruption access
and close are session-thread-only. Reentrant calls and calls after close are refused.

Child results never enter model history, top-level results or top-level tool events.
The session-thread service retains only bounded argument snapshots and errors,
never nested result output or details. Further exhausted calls may update the
exhaustion counter but cannot grow retained evidence indefinitely.

T7b stores `details["nestedCalls"]` on the parent result:
`{"calls": [{"id", "name", "status", "arguments" | "argumentsBytes",
"durationMs", "error"?}], "complete": bool}`. Status is `ok`, `error`,
`cancelled` or `unfinished`; duration is finite and nonnegative, shared with
live completion evidence. Limits are 256 calls, 8 KiB arguments per call,
32 KiB total arguments and 500 characters per error. Oversized arguments become
a byte count while the call remains; any dropped arguments, errors or calls set
`complete=false`. Snapshots are independent of live objects.

Script errors, timeouts and operator aborts return records for persistence,
retaining completed effects and partial child evidence. Canonical pipeline
exceptions publish unfinished live/parent-completion event evidence and propagate
after reservation cleanup; they do not guarantee saved session history. Secondary
evidence failures cannot replace the original pipeline exception or reopen the
service. Result hooks own parent content and other metadata; the loop merges a
fresh canonical record at the terminal boundary. JSON/RPC exposes all details
only for codemode parent results carrying that record. Ordinary direct tools and
extensions retain their prior wire shape. Product session-tree persistence stores
parent details; no child message or per-call custom entry is added. Provider HTTP
serializers continue to omit details and child results.

Resume and active-branch/tree replacement use the same transcript verbs and
PaintLock as live rendering. Bounded, validated child lines show sanitized
80-character arguments (or omitted byte counts), status and duration inside one
parent row; live settlement does not duplicate existing children. Malformed
imported metadata is ignored safely. Compaction and branch summaries derive
sorted, deduplicated read/modified path data before private summary details are
stripped. Writes/edits take precedence over reads, failed attempts are included,
and omitted arguments cannot supply paths. Paths are bounded JSON string data;
private summary requests have no tools. Derived data is prefixed before history,
preserving tool call/result adjacency and final compaction instructions. Direct
argument JSON is parsed up to 1 MiB of UTF-8 bytes; larger or malformed inputs
omit paths. Nested argument storage keeps its 8 KiB / 32 KiB bounds. This records
attempts, not proof that file effects succeeded.

T6b adds `coding/codemode_runner.py` outside the standalone sandbox core. It
validates the parent's `code` object with normal schema semantics and returns
`format_result` text under the parent identity. Only the seven eligible builtins
also advertised in the frozen parent request are offered to the guest. Results
are validated post-transform model text; malformed, budget and policy errors
become catchable `ToolError` values. Completed effects remain after failure.

The host's optional `activity_waiter` pumps the existing waiter on the session
thread while the guest computes. The I/O thread alone owns pipes and validates
protocol. Queue publication and empty-queue/event acknowledgement use one lock,
so a producer cannot lose a wake between the empty check and event clear.
Every I/O-thread exit publishes completion and wakes activity in a narrow final
path, including failures during kill, diagnostics or finish signalling. A dead
I/O thread without completion fails closed as a sandbox error. Cleanup retries
signal faults through the same owned process group before the bounded reap.
During a child tool, a monitor signals events for completion or the fresh
script stop event; it never reads or writes canonical or service state. The
session thread combines that signal with the existing operator waiter. The
explicit `SCRIPT_STOP` waiter value makes the executor cancel and bound its
join, then return an ordinary error observation with typed cancellation evidence rather
than operator interruption. T7a projects the in-flight child as cancelled.
Ctrl-C while computing becomes operator abort even when no tool waiter is
installed. Actual operator/local-command interruption closes the service,
stops the loop and reaps the worker. Backend/deadline failures remain parent errors. There is
no continuation after close, and every parent starts a fresh worker and control.
Noncooperative tools can outlive the bounded executor join; their already accepted
effects are not rolled back, as with direct tools.

Real-runtime tests exercise AgentLoop, ToolExecutor and host together: multiple
reads, text transformation, catchable malformed/budget errors, retained writes,
operator/local-command cancellation during cooperative and noncooperative tools,
headless abort during guest compute, deadline/backend failure during a tool,
fresh execution after cancellation and worker process-group cleanup. They
establish T6 acceptance D at the internal integration seam. Actual tmux Escape
evidence is recorded in the T9 acceptance note. T7a implements nested events and live projections; T7b implements durable parent
details, resume/tree and private summary file-operation input; T8a implements definition/runtime CLI/truncation spilling; T8b implements opt-in, availability selection and production registration.
T9 now pins the public production path with deterministic real-WASI and PTY
tests. Selected-frame tmux inspection and T10 live comparison are completed; T11 final
docs/cumulative review remain outstanding.

Python scripts are an intentional difference from Pi, whose codemode runs
JavaScript.

## Internal nested lifecycle (T7a)

The session thread emits parent `ToolCallStarted`, then accepted child
`NestedToolCallStarted` / `NestedToolCallCompleted` pairs, then parent
`ToolCallCompleted`. Children use local `<parent>/<n>` identities; they never
become provider tool calls. Completion carries validated post-transform text,
policy status and finite nonnegative `duration_seconds`. Refused recursive,
reentrant, closed or wrong-thread requests cannot execute and have no lifecycle.
Canonical callback failures still propagate and release the parent reservation.

JSON/RPC projects child start/end to `tool_execution_start` / `tool_execution_end`
with `parentToolCallId`, `toolCallId`, `toolName`, `args`, and completion
`result` / `isError`. The SDK deliberately ignores children. Product persistence
appends only the parent result. The workflow archive counts child starts and
completions separately using numeric metadata without names, arguments or text.

Live TUI children stay inside the pending codemode row: `…` unfinished, `✓` ok,
`✗` error, `⊘` cancelled, with name, arguments and settled duration. Collapsed
arguments show at most 80 characters and the last eight calls; Ctrl+O expands
retained evidence, with child errors on separate indented lines. PaintLock protects child updates and rendering, and parent
correlation rejects late or mismatched events. A completed parent commits one
row with its children; resize and expansion redraw that retained row. The plain
renderer emits indented child lifecycle lines under the parent. Retained live
state is capped at 256 children, 8 KiB arguments per child / 32 KiB total,
80-byte names and 500-byte error evidence; terminal controls are removed.
Backend/deadline stops mark an in-flight child cancelled through typed executor
evidence while the parent remains a timeout/backend error. Operator/local-command
interruption keeps existing turn-stop and budget semantics.

T7b reconstructs these child lines on resume/tree from the bounded parent
`nestedCalls` record and supplies derived attempted file paths to compaction and
branch summaries. No nested results or per-call CustomEntry records persist.
T8b implements public opt-in/settings/availability and production registration.
T9 automated delivery/replay/tree evidence is implemented; selected-frame tmux
inspection and T10 live measurement are completed; T11 final documentation/review remains pending.

## What it is

`pipy_harness.native.codemode` runs one Python script in CPython 3.14.7 compiled
to WASI, under wasmtime, in a fresh worker process per script. The guest has:

- an empty environment and a fixed argv (`python -I -S -B -c <prelude>`);
- the runtime's stdlib read-only at `/lib`, and no writable directory;
- no sockets, subprocesses, signals or native extensions (WASI has none);
- a memory cap and a CPU backstop.

A script calls `tools.<name>(dict)` or `tools.<name>(**kwargs)` to run a nested
tool through the host, emits its result with `text(value)` or `print(...)`, and
sees a failed call as `ToolError`. Nested calls are sequential.

## Installing

Codemode needs two optional pieces. Without either one pipy works as before and
codemode reports itself unavailable.

1. The `codemode` extra, which adds `wasmtime` 49:

   ```bash
   uv sync --extra codemode          # in a checkout
   uv tool install '.[codemode]'     # as a tool
   ```

2. The pinned runtime. Only this command downloads it; nothing fetches it
   during a turn. Run it with the Python environment pipy is installed in,
   which neither `uv sync` nor `uv tool install` activates:

   ```bash
   # in a checkout
   uv run --extra codemode pipy codemode install
   # as a tool
   pipy codemode install
   # from a local copy of the archive
   uv run --extra codemode pipy codemode install \
       --from-file python-3.14.7-wasi_sdk-24.zip
   ```

   In a checkout, prefix `pipy codemode` with `uv run --extra codemode`.
   The developer `python -m pipy_harness.native.codemode install|status`
   entrypoint remains available in the same Python environment.

   The archive is pinned in code (`RUNTIME_PIN` in `runtime.py`): the
   `python-3.14.7-wasi_sdk-24.zip` asset of the
   `brettcannon/cpython-wasi-build` v3.14.7 release, 14,291,017 bytes, sha256
   `2e064d3fb8172471d39d741348efa722349c40b96301f69968dff714999c584b`.
   `--from-file` installs from a local copy, which must match the same size and
   sha256. `--force` reinstalls over a valid runtime. The installer then runs
   the self-test, which also builds the compiled-module cache.

Files live under `~/.local/state/pipy/codemode/`:

| Path | Contents |
| --- | --- |
| `runtime/<version>/` | the unpacked runtime: `python.wasm`, `lib/`, `manifest.json` (sha256 of every file) |
| `cache/` | the `.cwasm` compiled-module cache, keyed on wasmtime version, platform and engine configuration |

Deleting the directory uninstalls the runtime.

## Enabling

Codemode is off by default. Once installed, enable it for a run:

```bash
pipy --tools +codemode
# in a checkout
uv run --extra codemode pipy --tools +codemode
```

Or add `"defaultTools": ["+codemode"]` to global settings or trusted project
`.pipy/settings.json`. The seven existing builtins and default extension
visibility remain unchanged. A plain `defaultTools` list sets the initial
builtin selection; `[]` inherits a lower-layer list, or selects no builtins when
there is no inherited list. Exact `+name`/`-name` entries
modify the inherited selection in order. See [settings semantics](settings-config.md#default-tool-selection).
A CLI plain allowlist overrides settings and filters extensions too;
`--no-tools`, `--no-builtin-tools`, and `--exclude-tools` retain their filters.

Only selected builtin codemode runs the availability check before advertisement.
The slow probe and concurrent probe waits run outside the shared session lock;
its run-owned cache and one-warning ownership remain synchronized. Candidate
preparation leaves live state unchanged, and publication carries accepted live
selections. If the extra, runtime or self-test is unavailable, startup or reload
emits one bounded diagnostic outside the session and probe locks: a transcript
notice when a terminal UI exists, otherwise one stderr line. The session
continues with the remaining tools. There is no
automatic download or interpreter fallback. After installing a missing runtime
or extra, start a new session. A custom extension named `codemode` remains an
ordinary extension and does not trigger the builtin probe or composite dispatch.
The stable public description, prompt snippet and guideline appear only for the
advertised builtin. Completed effects survive script errors; scripts are not
retried automatically after effects.

## Availability

```bash
pipy codemode status
```

prints `codemode available: ...` (exit 0) or `codemode unavailable: <reason>`
(exit 1). The check is fail-closed and has no fallback interpreter. Reasons
include:

- the `wasmtime` package is not installed (install the `codemode` extra);
- the runtime is not installed, or does not match the pinned sha256;
- the installed tree differs from its manifest (a missing, changed or extra
  file, or a symlink), or the manifest is missing or malformed;
- the self-test worker failed: it could not import wasmtime, load or rebuild
  the cache, instantiate the module or report the pinned runtime hash.

Runtime problems name their fix (`install` or `install --force`). Other
self-test failures report the underlying error as
`self-test failed: <detail>`, for example an unwritable cache directory; fix
the cause (permissions or free space in `~/.local/state/pipy/codemode/`), or
delete `cache/` and run `status` again to rebuild it.

The self-test detects corruption and drift, not tampering by someone who can
write the state directory: the `.cwasm` cache is native code whose sidecar hash
only detects corruption, and stdlib files changed after the self-test are used
as they are. A run re-checks `python.wasm` and refuses a symlinked `lib`.

Support is claimed for **macOS arm64 and Linux aarch64** only, where the
isolation probes were run. Other platforms (x86_64 Linux and macOS, Windows)
are unverified; there the self-test still decides availability. Running pipy
inside another agent's sandbox is unverified as well.

## Running a script (API)

```python
from pipy_harness.native.codemode import CallResult, ScriptLimits, run_script

def call_tool(name: str, args_json: str) -> CallResult:
    return CallResult(ok=True, text="...")

outcome = run_script(
    "text(tools.read(path='README.md')[:100])",
    call_tool=call_tool,
    tool_names=("read",),
    limits=ScriptLimits(),
    cancel=cancel_event,      # optional threading.Event; set = aborted
    tool_stop=stop_event,     # optional, fresh per run; set when the run ends
    activity_waiter=waiter,   # optional (activity_event, cancel_event) pump
)
```

`run_script` never raises. It returns a `ScriptOutcome` with the output, the
nested calls made (`ToolCallRecord`, status `ok`/`error`/`cancelled`), wall
time and, on failure, a `ScriptError` whose kind is `script`, `timeout`,
`aborted` or `sandbox` (a protocol violation or host-side failure).
`format_result` renders Pi's result text: a completed/failed header with wall
time, the output truncated to a token budget, and on failure the error and the
list of tool calls that are not undone.

`call_tool` runs on the calling thread, one call at a time, and only for names
in `tool_names`. It must honour `cancel` and `tool_stop` itself. Checking
availability first is the caller's job, but the runner fails closed regardless.
The optional `activity_waiter(activity_event, cancel_event)` runs on that same
thread while waiting for a queued call or completion. It returns when activity
or cancellation is signalled; ordinary exceptions become sandbox errors and
still clean up the worker. `KeyboardInterrupt` during activity waiting or
standalone polling sets cancellation and returns an `ABORTED` script outcome.
The canonical composite runner additionally records operator interruption, so
AgentLoop returns `CANCELLED`, records skipped calls and makes no provider
continuation. Standalone `run_script` has no parent-turn state. Omitting the
waiter preserves standalone polling behavior.

## Limits

`ScriptLimits` defaults (provisional, spike §4.5):

| Limit | Default | When exceeded |
| --- | --- | --- |
| `wall_seconds` | 120 s, nested tool time included | `timeout` |
| `memory_bytes` | 256 MiB guest memory (max 4 GiB) | `MemoryError` in the script (catchable; uncaught it is a `script` error) |
| `code_bytes` | 256 KiB of script source | `script`, before a worker starts |
| `line_bytes` | 1 MiB per protocol line | `sandbox` |
| `output_bytes` / `output_messages` | 1 MiB / 100,000 | `script`, with guidance to filter output |
| `stderr_bytes` | 64 KiB kept for diagnostics | truncated |
| `cpu_margin_seconds` | 5 s | guest CPU backstop at `wall_seconds` + margin |

The worker runs in its own process group, which the host always kills and
reaps. If the host itself dies, the worker notices within one 10 ms tick and
exits.

## Tests

Tests that need the real runtime skip with a reason when wasmtime or the
runtime is missing. Set `PIPY_CODEMODE_REQUIRE_RUNTIME=1` to make a missing
runtime fail instead (the Linux CI `codemode-probes` job does this after
installing it).

## T8a definition and result delivery

`CodemodeTool` defines the stable §4.8 description and exact `code` object
schema, shared with composite validation. T8b composes it into public production
capabilities only when selected and available; direct invocation fails closed
because execution requires the canonical composite service. Installing the
runtime alone does not enable a model tool.

The composite runner keeps ordered stdout/text, the completion header, script
errors, partial output and the “not undone” summary. Above about 10k tokens
(characters/4), it keeps the head and tail and spills exactly the untruncated
output/error body to an owner-only host temporary `pipy-codemode-*.log` file.
The result names the readable path and stores `details.fullOutputPath` alongside
honest character/UTF-8 byte truncation metadata and `nestedCalls`. These host
files remain for later reading; the guest gains no filesystem authority.
Open/write/close failures publish no full-output path, add a bounded diagnostic,
and preserve script success/error, interruption and nested records. The existing
1 MiB host output cap remains unchanged. Internal real-WASI acceptance C covers
write-then-raise with retained effects and records; public C is exercised by T8b
and retained in runtime-required CI. T9 automated PTY evidence is implemented;
selected-frame tmux inspection is recorded in the acceptance note.

## CM1 T10 measurement driver

The coordinator runs one paired live experiment first:

```sh
uv run --extra codemode python scripts/codemode_measure.py \
  --workspace /tmp/pipy-cm1-paired-1 --run-label paired-1 \
  --report /tmp/pipy-cm1-paired-1.json
```

Use a fresh empty directory outside the checkout. The approved provider is
`openai-codex`, model `gpt-6.1-sol`, effort `low`, explicit SSE for both runs.
There is no provider option, fallback or dollar cap. Existing normal credentials
are used by the provider's default auth manager; the script never reads, copies
or reports auth contents. HOME and PIPY_AUTH_DIR survive. Settings, theme,
defaults, history, state and sessions are isolated; the theme is `pi`.
A missing runtime, unavailable credentials or execution error fails closed with
exit 1 and a generic report failure. No runtime is downloaded.

Four deterministic 400-line files contain eight known KEEP records amid DROP
noise. Both runs share one fixture workspace (and therefore the same prompt cwd),
with fresh independent sessions. They use the same goal and fixtures, differing
only in the required
direct-read versus Python read/filter/compact instruction and selected tool
surface (`read` versus `read,+codemode`). The production CodingSession registry,
normal hooks, budget 50 and product session-tree persistence apply. A normal
fixture-local tool_call hook confines direct and nested reads to the four owned
relative paths. No write, edit, bash, discovery or extension tools are advertised.
The model cannot write repository files. Product trees retain normal owned task
content in the temporary workspace; the aggregate report contains no raw prompts,
transcripts, headers, tokens or provider bodies.

The report defines every metric. HTTP bytes match the actual default urllib JSON
serialization, counting every SSE attempt, including retries. It also reports
serialized `tools` and `input` bytes, provider turns, available cumulative usage,
parent/nested call counts, UTF-8 parent result sizes and codemode output-body size.
Task wall time covers session construction through return, including selected
availability/self-test, provider turns, fresh WASI startup, tools and persistence.
Fixture creation and the separately timed preflight/minimal `text(1)` worker run
are excluded. Parent durations are canonical completion-event durations and
include startup for codemode. The standalone worker number includes a fresh
process, engine/cache load, guest execution and cleanup, with no tool work.
It supplements rather than replaces the prior platform-specific 181/346 ms data.

Quality passes only when the final JSON has exactly the eight expected facts and
order, with four actual direct reads or one actual codemode parent/four nested
reads, no errors and a successful run. Codemode must also emit exactly that JSON
as its compact parent output. No speed or byte target is set; a single fixed-order
pair is descriptive, not a variance estimate. Repeat with fresh roots/labels if
needed. The coordinator completed the initial paired live run and separate interactive acceptance.
A repeat returned correct final facts but used two scripts/eight reads and failed
the prescribed one-parent pattern; its higher traffic is retained as a limitation.
Measured results and limitations are in the
[acceptance note](specs/2026-10-09-python-codemode-acceptance.md).

For separate live interactive acceptance, reuse the retained `codemode` directory
(or create fixtures offline with `--prepare-only --workspace /tmp/pipy-cm1-ui`).
Set isolated paths before launching the actual CLI; do not override HOME or auth.
Create `$ui_state/config/settings.json` containing
`{"theme":"pi","transport":"sse","quietStartup":true}` in a fresh temporary `$ui_state`, then:

```sh
PIPY_CONFIG_HOME="$ui_state/config" XDG_STATE_HOME="$ui_state/state" \
PIPY_NATIVE_THEME_PATH="$ui_state/theme.json" \
PIPY_NATIVE_DEFAULTS_PATH="$ui_state/defaults.json" \
PIPY_PROMPT_HISTORY_PATH="$ui_state/history.json" \
PIPY_NATIVE_SESSIONS_ROOT="$ui_state/sessions" \
uv run --extra codemode pipy repl --cwd /tmp/pipy-cm1-paired-1/codemode \
  --root "$ui_state/archive" \
  --native-provider openai-codex --native-model gpt-6.1-sol --thinking low \
  --tools read,codemode --approve --no-skills --no-prompt-templates --no-themes
```

`--approve` trusts only these owned resources for this run, without writing
trust.json. Submit the exact `prompt('codemode')` from the measurement script
(the owned task instruction, not an arbitrary repository prompt). Inspect actual
nested read rows, the compact count-8 output and final exact facts. Label this
interactive quality verification separately; it is not part of the paired timing
or byte totals. T11 owns cumulative checks, final module/authority/limit docs and
independent review. A resident engine remains an owner decision after data.
