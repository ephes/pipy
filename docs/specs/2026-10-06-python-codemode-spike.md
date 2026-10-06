# Python codemode: CM1 isolation spike result

Status: spike result, 2026-10-06. This follows the
[CM1 light plan](2026-10-04-python-codemode-plan.md). [Backlog](../backlog.md)
still decides selection and order. This document picks a backend, defines the
first-slice contract and lists the first-slice tasks. Implementation status:
T1 (runtime provisioning and the availability self-test) and T2 (the worker
and the guest prelude) are implemented; see their notes in §5. Everything else
here is not yet implemented.

## 1. Status and scope

There were three backend spikes. Each one was red-teamed separately. In
addition, a seam map of pipy's agent loop and a reference read of Pi's
codemode were done by reading code only. No pipy code was changed and pipy's
test suite was not run. The disposable probes and red-team scripts lived in
session scratch and were not committed; this document is the durable record.

What was actually run:

- **macOS Seatbelt + CPython 3.14.7.** Run on macOS 27.0.1 arm64 only. This
  covered probes for filesystem, network, spawn, credentials, limits, orphans,
  startup and fail-closed behaviour, plus a red team (15 vectors).
- **Linux Landlock ABI 4 + seccomp + rlimits.** Run on Ubuntu 24.04.4 aarch64,
  kernel 6.8, inside the colima VM, both on the VM host and in Docker
  containers. bwrap 0.9.0 was run, but only in a privileged setuid container.
  nsjail was not run. A red team (13 vectors) followed.
- **CPython 3.14.7 on WASI under wasmtime 49.0.0.** Run on macOS 27 arm64 and
  on Linux aarch64 (a `python:3.12-slim` container in the colima VM), with the
  same probe outcomes on both. A red team (13+ vectors) followed.

Desk research only (UNVERIFIED):

- every x86_64 platform, Windows, Intel macOS and older macOS;
- the real Ubuntu deployment host, whose architecture is unknown;
- nsjail;
- a bwrap AppArmor profile;
- cgroup v2 memory limits;
- the seccomp ia32/x32 bypass on x86_64 (confirmed by reading the code, but
  not executed);
- wasmtime wheels for platforms other than arm64 macOS and aarch64 Linux;
- wasmtime under pipy's own interpreter (the WASI host used Python 3.12;
  T1 later verified it on macOS arm64, see §6 question 2).

## 2. Backend comparison

| | macOS Seatbelt + CPython | Linux Landlock+seccomp + CPython | CPython-on-WASI (wasmtime) |
|---|---|---|---|
| Platforms verified | macOS 27.0.1 arm64 | Ubuntu 24.04 aarch64, kernel 6.8 (VM host and containers) | macOS 27 arm64 and Linux aarch64 (container) |
| Filesystem | Reads and writes outside the interpreter home are denied. The root listing and ancestor-directory metadata leak. | Reads and writes are denied. `stat` leaks the existence, size, mode, owner and mtime of credential files. | Nothing exists outside the preopens; `/lib` is read-only. A mutable `/scratch` allowed unbounded disk writes and planted relative symlinks. |
| Network | Denied: TCP, UDP, AF_UNIX, DNS, https | TCP is denied by Landlock. UDP and unix sockets are denied by seccomp `socket()` only. | No sockets exist (errno 58), no `getaddrinfo`, no `ssl` |
| Process spawn | Denied: fork, spawn, exec, system, mach lookups. Exec of the interpreter itself is allowed and stays sandboxed. | Denied by a seccomp denylist, native architecture only | Not present in WASI (no fork, kill or ctypes; threads cannot start) |
| Credentials and env | The worker's env is scrubbed, but **the host env leaks via sysctl `KERN_PROCARGS2` on the parent** (fix verified, see below) | The env is scrubbed and `/proc/*/environ` is denied. Existence and metadata of credential files leak. | The guest env is set explicitly and the host env is invisible. Credential paths do not exist in the guest namespace. |
| Limits | Wall time: host `killpg`. CPU: `RLIMIT_CPU`. Memory: `RLIMIT_AS` only as the ~466 GiB floor plus a budget; the footprint watchdog overshoots about 2x. Output caps. | Wall time: host `killpg`. Memory: `RLIMIT_AS` 512 MiB. Output caps. | CPU: epoch deadline. Wall time: host `killpg`, which is required because the epoch does not interrupt blocking reads. Memory: `Store` limit of 256 MB. Stack overflow traps. Output caps. |
| Startup (median) | ~26 ms full round trip | ~26 ms on the VM host | 181 ms on macOS, 346 ms on Linux in the VM (fresh process, cached `.cwasm`). A resident engine would take 18.5 / 87 ms. |
| Dependencies | `/usr/bin/sandbox-exec` (deprecated) and an SBPL profile. Fails under an outer Seatbelt sandbox (rc 71). | Landlock ABI ≥ 4 (kernel 6.7+) and `libseccomp.so.2`. bwrap and nsjail are blocked by the Ubuntu 24.04 AppArmor userns restriction. | `wasmtime` wheel (26 MB) plus `python.wasm` and its stdlib (40 MB unpacked) from a personal GitHub release (pinned by sha256). No zlib, sqlite3, ssl, threading, subprocess or ctypes. |
| Red-team verdict | **broken** (4 escapes) | **broken** (3 escapes) | **holds-with-caveats** (2 host-side escapes) |

The red-team escapes, by backend:

- **Seatbelt.**
  - `(allow sysctl-read)` lets the script read the host's argv and env through
    `KERN_PROCARGS2` on `getppid()`. The fix was verified: a named sysctl
    allowlist blocks this while CPython still boots.
  - The host runner crashes on malformed JSON from the worker, and the
    `killpg` after it never runs, so the worker survives as an orphan.
  - A flood of `tool_call` messages from a worker that never reads its stdin
    deadlocks the host's blocking `stdin.write`, so the wall limit never fires
    (the host hung for more than 20 s against a 2 s limit).
- **Landlock.**
  - The same `tool_call` flood deadlock, verified: the host hung 30 s against
    a 4 s deadline.
  - The seccomp denylist only covers the native architecture (no
    `seccomp_arch_add`). On x86_64, ia32/x32 syscalls would bypass the spawn
    and socket denials. This comes from reading the code and is UNVERIFIED
    by execution.
  - `stat` leaks credential-file metadata.
  - The reaper killed every process of the user's uid, a bystander included;
    a pipy host must reap only its own process group.
- **WASI.**
  - `tools.read` in the spike host had no path policy, so the guest could
    read any host file through it. This is a confused deputy, not a break of
    the wasm boundary.
  - The `/scratch` preopen allowed 300 MB of unbounded disk writes and planted
    symlinks.
  - Two robustness gaps did not escape: a `print()` flood exhausts guest
    memory and the outcome is lost, and a forged `{done}` line is accepted
    harmlessly.

Several defects belong to the host runner rather than to the backend: the
deadlock, the crash on malformed protocol input, cleanup that is skipped on a
host exception, and reaping across the whole uid. Any pipy host would have to
avoid them, whichever backend it uses. The tool-call flood that does not drain
stdin was not tested against the WASI spike host. Its call-spam probe drained
replies and was stopped by the output cap. For WASI this class is UNVERIFIED,
and it must be designed out (see §4.1).

## 3. Recommendation

**First slice: CPython-on-WASI under wasmtime as the only backend, on every
platform.**

Why:

- It is the only backend whose red-team verdict was not "broken".
- Its guest denials come from construction (cap-std preopens, no sockets, no
  processes, no FFI), not from a hand-written policy. Policy mistakes were
  exactly what broke the other two backends (the sysctl grant, the seccomp
  denylist).
- It behaves identically on macOS and Linux (the same errno values were
  verified on both), so pipy keeps one policy semantics instead of two.
- Pi's own boundary has the same shape: a language runtime in wasm, in a fresh
  worker. pipy adds a process boundary on top of that.
- It needs no root, no namespaces and no deprecated macOS tooling.

What the support statement must not overclaim:

- **Supported only where the startup self-test passes, and claimed for macOS
  arm64 and Linux aarch64 only.**
- x86_64 Linux, x86_64 macOS and Windows are UNVERIFIED. They get no support
  claim until the isolation probe suite has run there (task T2 adds a CI run).
  On those platforms, the probe suite must have run before support is
  claimed; until then the self-test still decides availability.
- The Ubuntu deployment host is UNVERIFIED, including its architecture.
- Running inside an outer sandbox (pipy started by Codex or Claude Code) is
  UNVERIFIED for wasmtime. Seatbelt is known to fail there; wasmtime's
  behaviour in that situation was not tested.

Not chosen for the first slice:

- **Seatbelt** is kept as a possible future "full CPython" backend (C
  extensions, the full stdlib). Before reuse it needs:
  - a named sysctl allowlist;
  - a fixed host runner;
  - acceptance that `sandbox-exec` is deprecated and that it cannot be nested.
- **Landlock+seccomp** is kept as the possible future Linux counterpart. Before
  reuse it needs:
  - a default-deny seccomp allowlist with explicit arch handling (x86_64,
    x32, ia32);
  - a decision on the `stat` metadata leak;
  - per-process reaping;
  - a fixed host runner.

  bwrap and nsjail are not viable defaults on stock Ubuntu 23.10+ because of
  the AppArmor userns restriction.

**Fail-closed rules.** Codemode is opt-in. When it is enabled:

1. Before the tool is advertised, availability is checked:
   - `import wasmtime` in the worker interpreter works;
   - the runtime artifact is present and matches the pinned sha256 (the spike
     recorded `2e064d3f…584b` for the 3.14.7 zip);
   - the `.cwasm` cache deserializes or recompiles under the exact engine
     config;
   - the worker's `ready` status reports the expected backend and runtime
     hash.
2. If any check fails, the tool is not advertised and one warning line names
   the reason. If the backend fails during a session, the call returns a
   `Script sandbox failed: …` error result.
3. No code path ever runs a script with host `exec()`. No backend is
   "degraded" or "none".

## 4. Execution contract for the first slice

### 4.1 Processes and worker protocol

The process chain is:

- **pipy (session thread plus one I/O thread)** → pipes,
  `start_new_session=True`, env `{PATH}` →
- **worker process:** pipy's interpreter running
  `python -I -m pipy_harness.native.codemode.worker`. It imports `wasmtime`,
  builds the engine with `epoch_interruption=True`, sets
  `Store.set_limits(memory_size=…)` and the epoch deadline, uses an explicit
  `WasiConfig.env`, and preopens the stdlib only, read-only at `/lib` →
- **wasm guest:** `python -I -S -c PRELUDE`.

The first slice has **no mutable preopen**: no `/scratch`, so all effects go
through `tools.*`.

Channels:

- The guest's stdin and stdout carry script traffic.
- The worker reports its own status on a private fd (passed with `pass_fds`).
  WASI does not expose that fd to the guest; the fd enumeration probe saw only
  fds 0–4.
- Guest and worker stderr are captured with a cap and used only as
  diagnostics. They are never parsed.

Messages are JSON, one object per line. Every guest message is untrusted.

| Direction | Message |
|---|---|
| host → guest | `{"type":"run","code":str,"tools":[str]}` (first line; `tools` names the callable tools, for the `tools.<name>` proxy and "did you mean") |
| guest → host | `{"type":"call","id":int,"name":str,"args":object}` |
| host → guest | `{"type":"result","id":int,"ok":true,"value":str}` or `{"type":"result","id":int,"ok":false,"error":str}` |
| guest → host | `{"type":"text","value":str}` (from `text()` and from `print()`, in order) |
| guest → host | `{"type":"done","ok":true}` or `{"type":"done","ok":false,"error":{"type":str,"message":str,"traceback":str}}` |
| worker → host (status fd) | `{"type":"ready","backend":"wasi","wasmtime":str,"runtime_sha256":str}`, then `{"type":"exit","reason":"ok"\|"trap-interrupt"\|"trap-stack"\|"error","code"?:int,"detail"?:str}`; a setup failure sends `exit` without `ready` |

Host rules. Each one closes a red-team finding:

- All pipe I/O is non-blocking and owned by the I/O thread. The deadline and
  the cancel state are checked on every loop iteration. A blocking write
  inside the loop is never allowed (fixes the flood deadlock).
- At most one `call` may be outstanding. A second `call` before its `result`,
  or an unknown `type`, a wrong `id`, malformed JSON or a non-object line,
  counts as a protocol violation. The host kills the worker and returns a
  sandbox error, and the host itself never raises (fixes the JSON crash).
- The line length cap is checked while bytes are being buffered.
- `done` is terminal. After it, the host stops reading and kills the worker.
- Kill and reap always run in `finally`, in this order:
  1. `os.killpg(pgid, SIGKILL)`, tolerating ESRCH and EPERM;
  2. a bounded `wait()`;
  3. closing the pipes.

  The host never waits for EOF after the worker has died, and it never sweeps
  other processes by uid. Only its own process group is reaped. Nothing in
  the guest can fork, so the group contains only the worker.
- The guest prelude does not buffer `print()`. Every write becomes a `text`
  message (fixes the in-guest OOM that lost the outcome). The host enforces
  the output caps.

### 4.2 Nested-call service and where it lives

These steps follow the seam map. Line references are to
`src/pipy_harness/native/` at `a992b43d`.

1. **Extract** a pure settle step from `AgentLoop._handle_tool_call`,
   `_execute_admitted_tool` and `_execute_tool` (`agent/loop.py:734-832`):
   - The settle step covers admission, `before_execute` and extension hooks,
     execution, counter settlement, `transform_result` and events.
   - The record step covers the history and `results` append and the
     skipped/cancel handling, and stays at top level only.
   - The refactor itself must not change behaviour.
2. **Add the service** in a new module, `agent/nested_calls.py`:

   ```python
   class NestedCallStatus(StrEnum):
       SETTLED; BLOCKED; UNAUTHORIZED; BUDGET_EXHAUSTED; MALFORMED; INTERRUPTED; REFUSED

   @dataclass(frozen=True, slots=True)
   class NestedToolCallOutcome:
       nested_call: AgentToolCall        # id "<parent_id>/<n>", never sent to a provider
       result: AgentToolResultMessage    # post-transform, validated
       status: NestedCallStatus
       interruption: ToolExecutionInterruption | None = None

   class NestedToolCallService(Protocol):
       """Bound to one admitted parent call; session-thread only; closed when the parent settles."""
       def call(self, tool_name: str, arguments_json: str, /) -> NestedToolCallOutcome: ...
       def records(self) -> tuple[NestedToolCallOutcome, ...]: ...
   ```

   The loop-side implementation does the following:
   - **Authorization:** checks the name with `snapshot.authorizes(name)`
     against the parent turn's frozen snapshot (`agent/request.py:52-55`) and
     the projection pinned for that turn (`repl/execution_projections.py:121`),
     minus `codemode`.
   - **Execution:** calls `self._tools.execute(...)` with a waiter that
     combines the operator interrupt and the script deadline. Validation and
     interruption are therefore the same as for a top-level call.
   - **Ids:** `<parent>/<n>` contains `/`, which is fine because the
     provider-id rule in `tool_call_ids.py:9` applies only to provider wires.
3. **Add the composite runner port.** An optional `AgentCompositeToolRunner`
   is dispatched from `_execute_admitted_tool` for `codemode` after the
   parent's `before_execute` passes. It receives the service and the
   `tool_waiter` and returns a `ToolExecutionOutcome`. The runner, not
   `ToolExecutor`, owns the parent result. It must never raise; failures
   become results. The runner is wired in `coding/agent_run.py:256-266` and
   `repl/loop_step.py:717-729`.
4. **Session-thread pump.** The I/O thread hands each guest message to the
   session thread through a queue plus an event, and the session thread waits
   on that event with the same `tool_waiter`. Nested calls therefore run on
   the session thread, where hooks, UI, events and persistence behave as they
   do at top level. There is never a second stdin reader.

   On `OPERATOR_ABORT` or `LOCAL_COMMAND`, the runner:
   1. closes the service, so later calls get `REFUSED`;
   2. kills and reaps the worker;
   3. returns an interrupted outcome.

   The existing interrupted path (`loop.py:813-819`) then records the parent
   and the skipped calls.

### 4.3 Events and persistence

- **Events.** New `NestedToolCallStarted` and
  `NestedToolCallCompleted(turn_index, parent_correlation_id, call, result,
  status, duration)` are added to the `AgentEvent` union
  (`agent/events.py:293`). These are not `ToolCallStarted` or
  `ToolCallCompleted`, so existing consumers neither persist them as results
  nor show them as top-level rows.
  - Nested results are never added to the loop's `results`, which would
    persist them through `agent_adapters.py:117-120`, and never added to
    history either.
- **Projections.** Each existing projection gets an explicit branch:
  - **json/rpc:** Pi-shaped `tool_execution_start` / `tool_execution_end`
    events with `parentToolCallId`. The `TypeError` on unknown events
    (`automation/agent_events.py:97`) is kept for everything else.
  - **UI reducer:** child lines inside the codemode row, with Pi's status
    icon, name, args truncated to 80 characters and duration. They are never
    separate rows (`ui/components/transcript.py:186`).
  - **Workflow archive adapter:** counts nested calls separately.
  - **Persistence:** no per-call entry (see below).
- **Durable record, following Pi.** The parent `toolResult.details` carries
  `nestedCalls: {calls: [{id, name, status: ok|error|cancelled|unfinished,
  arguments | argumentsBytes, durationMs, error?}], complete}` with Pi's
  bounds: 256 calls, 8 KiB of arguments per call, 32 KiB total, 500-character
  errors, and `complete=false` when anything was dropped.
  - Because the runner owns the parent result, the record survives a script
    error, a timeout and an operator abort. A test pins this.
  - `details` is stored and rendered on resume but never sent to the provider
    (`agent/messages.py:290-292`).
  - Nested results are not persisted.
  - Compaction's file-operation extraction reads `nestedCalls`.
  - Per-call `CustomEntry` records (seam map option C) are deferred; see
    open question 6.

### 4.4 Budgets

- Each nested call consumes the per-turn tool budget (`tool_budget`, default
  50, `coding/session.py:127`) through the same `decide_tool_admission` and
  `settle_tool_execution`.
- One slot is reserved for the parent, so settling the parent cannot raise
  the error at `loop_policy.py:325-326`.
- When the budget runs out, the script gets `ToolError("tool budget
  exhausted")`.
- Malformed nested calls are counted separately and are never fatal. They
  must not trigger `terminate_session` (`loop.py:725-728`). The script sees
  them as a catchable `ToolError`.

### 4.5 Limits (all PROVISIONAL)

| Limit | Default | Mechanism |
|---|---|---|
| Wall time per script, including nested tool time | 120 s | Host deadline on the I/O thread, then kill. During a nested call, the deadline also cancels the active tool through the executor's `cancel_event` path. |
| Guest CPU backstop | wall + 5 s | Epoch deadline (10 ms ticks). The ticks count wall time, including time blocked on host replies (verified in T2), so the host wall timer is primary. |
| Guest memory | 256 MiB | `Store.set_limits(memory_size=…)`. Verified to give `MemoryError` and let the script continue. |
| Code size | 256 KiB | Host check before spawn |
| Protocol line | 1 MiB | Checked while buffering |
| Total output | 1 MiB or 100 000 `text` messages | Host kills the worker and returns a script error with guidance to write large data through a tool |
| Model-visible output | 10 000 tokens (chars/4) | Head and tail truncation, with the full output spilled to a user-only temp file whose path is in the result. Reuse `tools/truncate.py` / `output_accumulator.py` where they fit. |
| Diagnostic stderr | 64 KiB | Captured, never parsed |
| Nested calls | Turn tool budget | §4.4 |

Pi's default wall limit is effectively unlimited. pipy deliberately sets a
finite one, because the plan requires it. Per-script overrides (Pi's
`// @options:` line) are deferred.

### 4.6 Error result shape (Pi-adopted)

```
Script completed|Script failed
Wall time 1.2 seconds
Output:
<text/print output, in order, truncated per budget>
Script error:
<traceback trimmed to <codemode> frames | Script timed out: … | Script aborted: … | Script sandbox failed: …>
Tool calls made before the failure (they are not undone): read (ok), bash (error)
```

- Output produced before a failure is kept, and a failure sets `isError`.
- The four error kinds are script, timeout, aborted and sandbox. A protocol
  violation is reported as sandbox.
- If no tool calls were made, the last line says "No tool calls were made."
- Reading an unknown `tools.<name>` raises `AttributeError` with "Did you mean
  tools.read?" or, when there are 20 or fewer, the list of available names.

### 4.7 Eligible tools

- **Callable:** the built-in tools advertised in the parent turn's snapshot
  (`read`, `ls`, `grep`, `find`, `write`, `edit`, `bash`), minus `codemode`.
- **Not callable in the first slice:** extension tools (that generalization is
  deferred) and `codemode` itself.
- **Authority:** nested calls run with the same authority as a direct call,
  including all of pipy's path policy and extension `tool_call` hooks, and
  they are attributed to the parent. As in Pi, the boundary limits what a
  script can do on its own. It does not limit what tools do. The WASI
  confused-deputy finding is therefore accepted by design, provided every
  nested call goes through the full policy path (§4.2). The spike's
  unprotected `read` stand-in is not representative of pipy's read tool.
- **Results:** a call returns the tool's model-facing text content as `str`.
  A tool error raises `ToolError(message)`. Structured results (Pi's `bash`
  object) are deferred.

### 4.8 What the model sees (draft)

Schema: `{"code": {"type": "string", "description": "Python source run as a script."}}`.

Description, drafted to stay byte-stable within a session:

> Run a Python script that calls other tools. The script runs in an isolated
> Python 3.14 interpreter with no file system, network or process access of
> its own. All effects go through `tools.<name>(args)`, which runs the named
> tool exactly as a direct call would, with the same JSON arguments. It
> returns the tool's text output or raises `ToolError`. Calls run one at a
> time. `text(value)` emits output: strings as-is, anything else as JSON.
> `print()` output is included as well. Unavailable modules: subprocess,
> socket networking, ssl, threading, ctypes, zlib/gzip/bz2/lzma, sqlite3.
> Limits: 120 s wall time, 256 MiB memory, about 10k tokens of output. Tool
> calls already made are not undone when the script fails. Callable tools:
> read, ls, grep, find, write, edit, bash.

Prompt snippet: "Run Python that calls other tools". Guideline: "Use codemode
to chain several tool calls or filter large output in one step instead of many
separate calls."

### 4.9 Pi behaviours

**Adopted:**

- one shared pipeline for nested calls;
- `<parent>/<n>` ids and `parentToolCallId` events;
- nested rows rendered inside the parent row;
- a bounded `nestedCalls` record on the parent result, with no persisted
  nested results;
- the result header and the error kinds;
- "not undone" call summaries;
- "did you mean" errors;
- a fresh worker per script that is always reaped;
- marking in-flight calls as cancelled;
- no recursion;
- credentials unreachable from the script;
- failing closed to a sandbox error;
- a lean, stable description;
- off by default.

**Deliberately different:**

- Python instead of JavaScript.
- A process plus a wasm runtime instead of a worker thread.
- A finite default wall limit.
- Sequential calls only.
- A JSON `code` string instead of raw-source grammar.
- No `return`, `console.*` or `exit()`; print is equivalent to text.
- Deferred: `store()`/`load()`, `image()`, `models.*`, `searchTools()` and
  related helpers, MCP exposure tiers, structured tool results and the
  options line.

## 5. First-slice task list

Each task is small and lands with its own tests. The plan's acceptance items
(A–K) map to tasks as follows:

- A: a multi-file read, filter and compact result using real tools;
- B: validation and policy failures;
- C: script failure after a completed effect;
- D: cancellation during a tool;
- E: infinite-loop termination;
- F: memory and output limits;
- G: worker cleanup;
- H: negative isolation probes;
- I: headless and TUI lifecycle, plus resume and tree evidence;
- J: comparison against direct tools;
- K: docs, release notes, checks and review.

1. **T1: runtime provisioning and the availability self-test.**
   - Optional `wasmtime` dependency; pinned runtime URL and sha256; an
     explicit install step into pipy's data dir (no download during a turn);
     a `.cwasm` cache keyed by wasmtime version, CPU and config.
   - Verify that `wasmtime` 49 imports under pipy's 3.14 interpreter.
   - Tests: missing wheel, missing runtime, hash mismatch and a stale `.cwasm`
     each make codemode unavailable with a reason and never run the script on
     the host.
   - **Implemented** in `src/pipy_harness/native/codemode/`:
     - `runtime.py` pins the release asset
       (`brettcannon/cpython-wasi-build` v3.14.7,
       `python-3.14.7-wasi_sdk-24.zip`, sha256 `2e064d3f…584b`, 14 291 017
       bytes). `install_runtime` is the only code that downloads or unpacks it.
       It checks size and sha256 before unpacking, refuses unsafe members
       (absolute, `..`, symlinks, unknown top-level names), and writes a
       per-file `manifest.json`. `verify_runtime` checks the whole tree against
       that manifest, so a partial install or a modified, missing or extra
       file is reported. Layout: `~/.local/state/pipy/codemode/{runtime,cache}`.
     - The install command is `python -m pipy_harness.native.codemode install
       [--from-file ZIP] [--force]`; `status` runs only the self-test. A
       Pi-aligned `pipy` CLI surface belongs to T8.
     - `selftest.py` holds `availability() -> Availability(available, reason,
       …)`. It never raises, never imports `wasmtime` into the host and never
       runs a script. It checks, in order: the wheel is importable
       (`find_spec`), the runtime verifies, then a fresh worker (pipy's
       interpreter, `-I`, own session, env `{PATH}`) reports `backend=wasi`
       and the pinned runtime hash. The worker is killed and reaped by its own
       process group on timeout.
     - `worker.py` is the only module that imports `wasmtime`. It hashes the
       `python.wasm` bytes it compiles against the manifest, keys the
       `.cwasm` cache on wasmtime version, `sys.platform`, machine, the
       engine settings and the `python.wasm` hash, and deserializes a cached
       module only when its bytes match the sidecar hash recorded when it
       was compiled. A stale, corrupt or foreign-engine cache is recompiled
       (state `recompiled`); if the fresh module cannot be cached, codemode
       is unavailable. The self-test instantiates the module without
       calling `_start`.
     - `wasmtime` 49.0.0 imports and compiles under pipy's CPython 3.14.7 on
       macOS arm64 (closes open question 2 for that platform). Compiling
       `python.wasm` took ~0.33 s; a cache hit plus the whole self-test
       takes about 0.2 s.
   - **Deviation:** the worker runs by file path
     (`python -I …/codemode/worker.py`), not `-m
     pipy_harness.native.codemode.worker`. Importing the `pipy_harness`
     package costs 0.23–0.58 s, more than the whole worker budget, so the
     worker uses only the standard library and `wasmtime`; a test pins that.
   - `wasmtime` is in the `codemode` extra and also in the dev group, so the
     type check and real-runtime tests see it. Real-runtime tests copy the
     installed runtime into a temp root and skip with a reason when the
     wheel or the runtime is absent.
2. **T2: worker and guest prelude.**
   - `tools` proxy, `text`, print routed to text, `ToolError`, "did you
     mean", traceback trimming, the private status fd, and no mutable
     preopen.
   - Port the red-team probes as tests: `/proc` and `/dev`, inherited fds,
     symlinks, ctypes/dlopen imports, signals, credentials and env, sockets,
     DNS, kill survival.
   - Run the probe suite in CI on x86_64 Linux.
   - Covers H.
   - **Implemented:**
     - `worker.py run STATUS_FD REQUEST_JSON` (request: `runtime_dir`,
       `cache_dir`, `memory_bytes`, `cpu_seconds`). It reuses T1's engine and
       verified module cache, sets `Store.set_limits(memory_size=…)`, runs a
       10 ms epoch ticker with the deadline set just before `_start`, gives
       the guest the argv `python -I -S -B -c <prelude>`, an empty
       `WasiConfig.env`, inherited stdio and only `/lib` (the runtime's
       stdlib, read-only). It writes `ready` and `exit` to the status fd and
       never touches stdio itself. Like T1, it runs by file path, not `-m`.
     - `prelude.py` is the guest prelude, passed as `-c` source and
       importable on the host for unit tests. `tools.<name>(dict)` or
       `tools.<name>(**kwargs)` sends one `call` and returns the result's
       `str` or raises `ToolError`; arguments that are not a dict or not JSON
       never reach the host. Unknown names raise `AttributeError` with "Did
       you mean tools.read?", otherwise the list of up to 20 names. Each
       `text()` call, `print()` call (one message per call, never buffered)
       and write to `sys.stdout`/`sys.stderr` is one `text` message;
       `text()` sends non-strings as JSON (or `repr`). `sys.stdin` is empty.
       Failures carry `{type, message, traceback}` with only `<codemode>`
       frames, across `__cause__`/`__context__`/exception groups; the script
       namespace is cleared first, so an out-of-memory script still reports
       its outcome. `sys.exit(0)` counts as success. A malformed host message
       ends the guest with exit status 3, never a script outcome.
     - Probe results (macOS arm64, wasmtime 49.0.0): no `/proc`, `/dev`,
       `/tmp`, `/etc` or host path exists; `listdir('/')` fails; `/lib` is
       read-only (create, append, truncate, rename, unlink, link, symlink,
       utime fail; `chmod` is a no-op stub and the test pins that the host
       mode is unchanged); host-planted absolute, relative and directory
       symlinks inside `/lib` cannot be followed out; the guest sees fds
       0–3 only, not the status fd or other inherited fds; `_ctypes`,
       `_posixsubprocess`, `multiprocessing`, `mmap`, `fcntl`, `ssl` are
       missing; `subprocess` imports but fails (errno 58), `os.fork`,
       `os.system` and friends do not exist and threads cannot start;
       sockets fail (errno 58), `getaddrinfo` and `AF_UNIX` do not exist and
       a host listener sees no connection; `os.environ` is empty even with a
       secret in the worker's environment, `sys.argv` is `['-c']`; `os.kill`
       and `os.killpg` do not exist, and `signal.raise_signal` aborts only
       the guest (an `error` exit).
     - Limits: memory beyond the cap raises `MemoryError` and the script
       continues; a busy loop ends as `trap-interrupt`; unbounded recursion
       with a raised recursion limit ends as `trap-stack`. The epoch
       deadline counts wall time, including time the guest spends blocked on
       a host reply (open question 7 is answered: it does), so the host's
       wall deadline stays primary. A guest blocked reading its stdin is
       ended only by the driver's `killpg`, after which the group is gone.
     - Tests: `tests/test_native_codemode_worker.py` (prelude units and the
       protocol, limits) and `tests/test_native_codemode_isolation.py`
       (probes) drive real workers through a minimal test driver
       (`tests/codemode_driver.py`, not the T3 host runner) and skip
       without the wheel or the runtime. The CI job `codemode-probes`
       installs the runtime on Linux x86_64 and fails instead of skipping.
3. **T3: host runner.**
   - I/O thread, non-blocking pipes, the caps from §4.5, protocol-violation
     handling, deadline, `killpg` and reap in `finally`, reaping by process
     group only.
   - Regression tests for the red-team vectors: malformed JSON, a call flood
     without reading stdin, a second outstanding call, a forged `done`, an
     oversized line, a print flood, and a guest blocked in a read until the
     wall limit.
   - Covers E, F and G.
4. **T4: settle/record extraction** in `agent/loop.py`, with no behaviour
   change. The existing loop tests stay green.
5. **T5: nested policy transitions** in `loop_policy.py`: a reserved parent
   slot, non-fatal nested malformed calls, and budget exhaustion. Covers B,
   plus a budget test.
6. **T6: `NestedToolCallService`, `AgentCompositeToolRunner` and the
   session-thread pump**, including the combined interrupt and deadline
   waiter.
   - Tests: cancellation during a nested tool (D), cancellation during script
     compute, the deadline firing during a nested tool, refusal after close,
     and recursion refused.
7. **T7: events and projections.**
   - Add `NestedToolCallStarted` / `NestedToolCallCompleted`.
   - Add the `nestedCalls` record on the parent details, and survive abort and
     timeout.
   - Project to json/rpc with `parentToolCallId`; add the UI reducer child
     lines, the workflow archive branch and the compaction file-ops input.
   - Tests pin the ordering parent Started → nested* → parent Completed, and
     pin that the persisted record survives resume and tree rendering.
     Covers I (durable part).
8. **T8: the codemode tool.**
   - Opt-in switch, with the name aligned to Pi's setting and CLI surface
     (`CA/docs/settings.md:41-42`, `cli.md:155-178`).
   - Schema and prompt text, result shaping, truncation and spill.
   - Test: a script that writes a file and then raises; the file stays
     written, the result is an error, and the summary says
     `write (ok)` "not undone". Covers C.
9. **T9: end-to-end.**
   - A script that reads several real files, filters them and emits a compact
     result (A).
   - Headless and json/rpc runs, plus a TUI run verified with tmux (the
     `tmux-transient-ui-verify` skill) for live nested rows.
   - Resume shows the evidence. Covers I.
10. **T10: measurement.** The same task with direct tools and with codemode,
    comparing request size, wall time (including the measured 181 / 346 ms
    startup) and output quality. No target is set beforehand. Covers J.
11. **T11: docs and review.**
    - A user doc listing the available modules, the limits and the
      authority model; a `CHANGELOG.md` entry.
    - Repository checks and an independent different-family review before
      landing. Covers K.

## 6. Open questions and unresolved findings

1. **x86_64 and the deployment host.** WASI is verified only on arm64 macOS
   and aarch64 Linux. x86_64 Linux and macOS, Windows, and the real Ubuntu
   host (architecture unknown) are UNVERIFIED. The CI probe run in T2 decides
   x86_64 Linux.
2. **wasmtime under pipy's interpreter.** The spike host ran Python 3.12.
   pipy runs 3.14 and declares `>=3.11`. T1 verified that wasmtime 49.0.0
   imports, compiles and instantiates `python.wasm` under CPython 3.14.7 on
   macOS arm64. Other platforms and interpreter versions remain UNVERIFIED;
   the self-test still decides availability there.
3. **Runtime provenance and size.** About 55 MB comes from Brett Cannon's
   personal release. Options: pin and mirror, build our own, or ship a
   separate wheel. The 3.15.0rc2 build was seen but not evaluated, and the
   VMware Wasm Labs builds were not evaluated.
4. **Outer sandbox.** Whether wasmtime (JIT and mmap) works when pipy itself
   runs inside Codex's or Claude Code's Seatbelt is UNVERIFIED. If it fails,
   the self-test must make codemode unavailable.
5. **Startup cost.** A fresh process takes 181 ms on macOS and 346 ms on
   Linux in the VM. A resident engine with a fresh `Store` per script would
   take 18.5 / 87 ms but weakens the "fresh worker" kill story. Decide after
   T10.
6. **Crash durability.** A record on the parent result is lost if pipy itself
   dies in the middle of a script that has effects. Pi accepts this. Per-call
   `CustomEntry` records (seam map option C) would avoid it, at the cost of a
   new `append_custom` sink action and a renderer. This needs an owner
   decision.
7. **Epoch versus blocking.** The epoch deadline does not interrupt blocking
   WASI reads (verified). Time spent waiting on host tools does use up the
   epoch budget: the guest traps on its first check after the reply (T2
   verified on macOS arm64). The design keeps the host wall timer as the
   primary limit and sets the epoch backstop above it.
8. **Flood deadlock on WASI.** The deadlock was verified on the Seatbelt and
   Landlock hosts. The WASI host was not tested with that exact vector. T3's
   design and regression test are mandatory either way.
9. **Missing stdlib modules** (zlib, sqlite3, ssl, threading). Is that
   acceptable for model-written scripts? There is no third-party code with C
   extensions.
10. **Defense in depth.** Should the wasmtime worker also be wrapped in
    Seatbelt or Landlock? It is not needed for the verified guest denials, and
    nesting limits apply. Deferred.
11. **Future full-CPython backends.** These Seatbelt and Landlock findings are
    still open: the sysctl allowlist (fix verified), the seccomp default-deny
    and arch handling (x86_64 bypass UNVERIFIED by execution), the Landlock
    `stat` metadata leak, the Ubuntu AppArmor userns restriction on bwrap and
    nsjail, the `RLIMIT_AS` floor (~466 GiB) on macOS, and Seatbelt's
    deprecation.
12. **Authority narrowing.** Nested calls have full direct-call authority by
    design. Any narrowing, such as a read-only codemode, is a separate policy
    decision.
13. **Deferred Pi surface.** Options line or timeout override, structured
    `bash` results, exposure tiers (direct, model-only, codemode, deferred),
    `store()`/`load()`, `image()`, `models.*`, parallel calls with Pi's
    exclusive-queue rule.
