# Python codemode sandbox (CM1, groundwork)

Status: **sandbox core, T4–T7 internal integration and T8a implemented.**
The internal composite runner now retains bounded nested-call evidence on its
parent result and reconstructs child lines on resume and tree navigation.
T8a adds the stable builtin definition, runtime-management CLI and composite
output spilling. T8b opt-in/settings/availability and production registration
remain pending; end-to-end/tmux evidence,
measurement and final delivery remain T9–T11 of the
[spike result](specs/2026-10-06-python-codemode-spike.md) (§5). The
[light plan](specs/2026-10-04-python-codemode-plan.md) has the scope. This page
documents installing the runtime, checking availability and the internal API.

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
extension overrides. The T8a definition is not registered in production; T8b owns registration, so public behavior
remains unchanged. Existing internal test ports can omit the optional guard.

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
evidence remains T9. T7a implements nested events and live projections; T7b implements durable parent
details, resume/tree and private summary file-operation input; T8a implements definition/runtime CLI/truncation spilling; T8b owns opt-in,
availability selection and production registration.
T9–T11 remain outstanding. There is no model opt-in yet.

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
branch summaries. No nested results or per-call CustomEntry records persist. T8b owns opt-in/settings/availability and production registration, T9 end-to-end evidence, T10 measurement and T11
final documentation/review.

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
schema, shared with composite validation. It is available for internal fixture
injection only; direct invocation fails closed because execution requires the
canonical composite service. Production registries and model settings are
unchanged. Installing the runtime does not enable a model tool.

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
write-then-raise with retained effects and records; T9 live/PTY evidence is pending.
