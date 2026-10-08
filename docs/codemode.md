# Python codemode sandbox (CM1, groundwork)

Status: **sandbox core and T4–T5 loop/policy groundwork, not yet a tool.** The model
cannot run scripts yet: there is no model-visible tool or codemode-specific
events or session records. Pure nested budget policy exists, without execution wiring.
Those are tasks T6–T11 of the
[spike result](specs/2026-10-06-python-codemode-spike.md) (§5); the
[light plan](specs/2026-10-04-python-codemode-plan.md) has the scope. This page
documents what exists today: installing the runtime, checking availability and
the `run_script` API that the loop integration will call.

T4 separates the agent loop's internal settle and record paths without changing
behaviour. Settlement owns admission, hooks, execution, result transformation
and validation, and existing tool events. Recording owns top-level results,
model history, skipped calls and terminal handling. Budget state is published
early; other state publication keeps its existing recording boundary. This is
groundwork for T6 reuse, not a nested-call service or a model-visible tool.

T5 adds pure policy transitions, using the existing admission, product-blocking
and execution-settlement functions with `AgentToolInvocationMode`. After core
admission and product preflight allow a composite parent, `reserve_composite_parent`
reserves one slot. Nested admission checks used slots plus that reservation before
authorization. Settlement with `PARENT` releases it exactly once in the caller's
state sequence; a second parent settlement or a nested call after release is
rejected. T6 must own that sequence and close the service on settlement.

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
malformed count. An exhausted transition is groundwork for T6 to translate to a
catchable guest `ToolError`; that integration does not exist yet. Direct calls,
callbacks and top-level interruption behavior are unchanged.

Python scripts are an intentional difference from Pi, whose codemode runs
JavaScript.

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
   uv run --extra codemode python -m pipy_harness.native.codemode install
   # as a tool
   "$(uv tool dir)/pipy/bin/python" -m pipy_harness.native.codemode install
   # from a local copy of the archive
   uv run --extra codemode python -m pipy_harness.native.codemode install \
       --from-file python-3.14.7-wasi_sdk-24.zip
   ```

   The commands below write `python -m pipy_harness.native.codemode` for
   short; prefix them the same way.

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
python -m pipy_harness.native.codemode status
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
