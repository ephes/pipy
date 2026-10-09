# Python codemode

Codemode lets the model run Python that calls existing tools, filters their text
results and emits a compact answer in one tool call. It is optional and disabled
by default. Every script starts a fresh isolated CPython 3.14.7 WASI worker;
variables do not survive between scripts.

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

```sh
pipy codemode status
pipy --tools +codemode
# In a checkout:
uv run --extra codemode pipy --tools +codemode
```

Or add this to global settings or trusted project `.pipy/settings.json`:

```json
{"defaultTools": ["+codemode"]}
```

This retains the seven default builtins and default extension visibility.
Settings modifiers merge with inherited entries in order; plain names replace
the inherited list. An empty list inherits a lower-layer list, or selects no
builtins if none exists. CLI modifiers apply to the resolved settings selection;
a plain CLI allowlist overrides it and filters extensions. For example,
`pipy --tools read,codemode` selects those two tools; `pipy --tools +codemode,-read`
modifies the defaults. CLI lists cannot mix plain names and modifiers.
See [selection rules](settings-config.md#default-tool-selection).

Disabled codemode does not probe. Selected but unavailable codemode is omitted
with one bounded warning (TUI transcript notice or headless stderr line); the
session continues with remaining tools. There is no automatic download or host
Python/exec fallback. `status` exits 0 when available and 1 with a reason otherwise.
Missing dependencies, runtime drift, corrupt cache or a failed self-test make it
unavailable. Runtime diagnostics name `install` or `install --force` where
appropriate. After installing a missing extra/runtime, start a new session.

## Writing a script

The tool accepts a JSON object with only a required `code` string. This example
reads two text files through the normal read tool and filters KEEP lines:

```json
{"code":"matches = []\nfor path in ('alpha.txt', 'beta.txt'):\n    content = tools.read({'path': path})\n    matches.extend({'path': path, 'line': line} for line in content.splitlines() if line.startswith('KEEP '))\ntext({'matches': matches, 'count': len(matches)})"}
```

`tools.read({...})` takes the same JSON arguments as a direct call. Calls return
model-facing text as `str`, after normal result hooks/validation, or raise
catchable `ToolError`:

```python
try:
    content = tools.read({"path": "alpha.txt"})
except ToolError as error:
    text({"error": str(error)})
```

`text(value)` emits strings unchanged and other values as JSON (falling back to
`repr` for values JSON cannot encode). `print()` and stdout/stderr writes are
included in order. Filter before emitting: bulk raw results can exhaust output
budgets and require another model turn.

Calls are sequential. Only advertised native builtins from the parent's frozen
request snapshot are eligible: `read`, `ls`, `grep`, `find`, `write`, `edit`,
`bash`. Hidden tools, extension tools/replacements, MCP and recursive codemode
calls are unavailable. Selection changes during a parent do not refresh its snapshot.

## Python capabilities and authority

Installed-runtime probes establish imports of `json`, `re`, `math`, `statistics`,
`itertools`, `functools`, `collections`, `datetime`, `pathlib`, `csv`, `random`,
`hashlib`, `decimal` and `fractions` for in-memory parsing and calculation.
`pathlib` grants no host file access. `subprocess`, `socket` and `threading`
import, but process creation, networking/DNS and thread creation are unavailable.
`ssl`, `ctypes`, `zlib`, `gzip`, `bz2`, `lzma` and `sqlite3` imports fail with
`ModuleNotFoundError` in this pin. Third-party native extensions are unavailable.

The guest has no direct host filesystem, network or process APIs or host
credentials/environment; its only filesystem preopen is the read-only stdlib.
Eligible tools retain exactly their normal direct-call authority: `read` resolves
any readable host path under the existing tool policy; `bash` may access files,
network and spawn host processes through its normal pipeline. Every nested call
uses the same validation, policy, extension hooks, budget, result transformation
and interruption path as a direct call. Scripts themselves gain no extra authority.

Completed tool effects persist after script failure or cancellation. The result
retains partial output and a call summary saying effects are **not undone**.
Scripts are not automatically retried after effects.

## Limits and cancellation

| Limit | Default |
| --- | --- |
| Wall deadline | 120 seconds, including startup and time inside tools |
| Guest memory | 256 MiB; exhaustion raises catchable `MemoryError` |
| Source | 256 KiB UTF-8, checked before spawning |
| Protocol line | 1 MiB; violation fails the sandbox |
| Emitted output | 1 MiB or 100,000 messages; excess fails the script |
| Result display | 10,000 estimated tokens (characters / 4), head and tail |
| Worker diagnostics | 64 KiB stderr retained |
| CPU backstop | wall deadline + 5 seconds |

Escape cancels computation or the active nested tool through the normal input
waiter. RPC abort works during either phase. The host wall deadline includes tool
time and stops an active child; the CPU backstop is secondary. Worker groups are
killed and reaped; subsequent scripts start fresh. As with direct tools, a
noncooperative host tool can outlive its bounded cancellation join. Accepted
effects are not rolled back.

Display truncation spills exactly the untruncated output/error body to an
owner-only host temporary `pipy-codemode-*.log` file. The result names its path
and supplies `details.fullOutputPath` and character/UTF-8 byte metadata. Files
remain readable later through normal tools. Spilling cannot restore output beyond
the 1 MiB emission cap. Open/write/close failures report that full output could
not be saved, publish no path and preserve the primary outcome and nested records.

## Nested evidence and availability

Live children appear inside the parent row; Ctrl+O expands retained evidence.
JSON/RPC child lifecycle events carry `parentToolCallId`. Only the parent result
enters provider and durable product history, with a bounded `nestedCalls` record:
256 calls, 8 KiB arguments per call, 32 KiB total arguments and 500 characters
per error. Omitted evidence marks the record incomplete; nested result bodies
are not stored there. Resume/tree reconstruct children from the parent. Host
crashes mid-script can lose parent evidence; per-call `CustomEntry` durability
remains a follow-on owner decision.

Canonical pipeline exceptions propagate as the original exception object, type
and message. Parent error evidence and completion/status publication are
best-effort while unwinding; the service still closes and the local reservation
is released with child counters retained. These exceptions do not guarantee saved
history. Without an active pipeline exception, evidence and status failures remain
visible. Returning script failures, timeouts and aborts retain bounded records.

Real-runtime isolation has evidence on macOS arm64, Linux aarch64 and
[Linux x86_64 CI](https://github.com/ephes/pipy/actions/runs/37858973937/job/113589899380)
(315 passed, runtime required). Intel macOS, Windows and the real Ubuntu deployment
host remain unverified. Workspace-write workers exercised the runtime here, but
transient EPERM signal faults qualify outer-sandbox compatibility; the self-test
decides availability in each environment.

[Acceptance evidence](specs/2026-10-09-python-codemode-acceptance.md) records A–K,
terminal captures and two fixed-order live pairs. The initial pair and interactive
run passed; the repeat returned correct facts but used two scripts/eight reads,
failing the compact execution pattern. Samples establish no general speed or
quality distribution.

See the [developer reference](specs/2026-10-09-python-codemode-developer.md) for
runtime provenance, API and reproduction commands, and the
[spike contract/open decisions](specs/2026-10-06-python-codemode-spike.md).
Extension tools, MCP, parallel calls, images, `models.*`, `store()`/`load()`,
options lines and structured bash results are deferred. A resident engine requires
an owner decision after T10; fresh workers remain the runtime model.
