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

What a call returns depends on the tool:

- A tool that declares an output schema returns its structured content as a
  Python value (`dict`, `list`, `str`, number, `bool`), not as text. It
  returns it for a failure too, when the failed result carries structured
  content, so check the value instead of relying on `ToolError`. None of the
  built-in tools declares an output schema; an
  [extension tool](extension-api.md#tool-model) can.
- Every other call returns the tool's text as one `str`. That includes a tool
  that declares an output schema when a result carries no structured content.
- A call that fails without structured content raises `ToolError` with the
  tool's error text, or `Tool "<name>" failed` when the tool gave none. So do
  a call that a `tool_call` hook blocked, a call whose arguments do not fit
  the tool's schema and a call that the tool budget refused.

A `tool_result` hook sees the result before the script does. A hook that
replaces the content decides the structured content with it: the script
receives the structured content the hook returned, or the replaced text when
the hook returned none.

A structured value is not cut to the 64 KiB a tool's text is limited to. One
tool reply may be up to 16 MiB as JSON; a larger one raises `ToolError` in the
script, after the tool has run. A script still has 256 MiB of memory for the
values it keeps and can emit 1 MiB of output, so reduce a large value before
emitting it.

`text(value)` emits strings unchanged and other values as JSON (falling back to
`repr` for values JSON cannot encode). `print()` and stdout/stderr writes are
included in order. Filter before emitting: bulk raw results can exhaust output
budgets and require another model turn.

The advertised builtin now includes a runnable small-file read/filter example.
Use returned strings directly: `tools.read()` is not a result object, and text
files do not need JSON decoding. Keep intermediate reads in Python and emit the
compact answer after processing. Format probes that print types, dump raw reads
or deliberately raise with their contents add work without completing the task.
Follow read's offset/limit notices when a file is incomplete; the small-file
example does not implement pagination. This is model guidance, not an enforced
single-script rule or a guarantee that a model follows it. See the
[adherence slice](specs/2026-10-09-codemode-adherence.md) for evidence and limits.

Calls are sequential. A script can call:

- every active tool with exposure `direct`: the built-ins `read`, `ls`, `grep`,
  `find`, `write`, `edit` and `bash` while they are active, and extension tools,
  including one that replaces a built-in;
- every registered extension tool with exposure `codemode` or `deferred`,
  whether or not it is active. Registering such a tool does not declare it to
  the model, so a script is the way to reach it unless something activates it
  by name (`ctx.set_active_tools(...)`, a restored transcript).

A script cannot call `codemode` itself, a `model-only` or `hidden` tool, a
`direct` tool that is not active, a tool that `--tools`, `--exclude-tools` or
`--no-tools` removed, or a tool that a `before_provider_request` hook withheld
from the request that started the script. See
[tool metadata](extension-api.md#tool-model) for the exposures.

Every callable tool is a method of `tools`, named by its identifier: the
tool's name with each character that an ASCII Python identifier cannot hold
replaced by `_`, and a trailing `_` when the result is a Python keyword. A
tool named `mcp__dev-radius__search` is `tools.mcp__dev_radius__search`, and a
tool named `class` is `tools.class_`. The tool's own name works as a key:
`tools["mcp__dev-radius__search"]`. So does the identifier, which is what
`ALL_TOOLS` and `search_tools` return: `tools[match["name"]]`. When two names
give the same identifier, the tool registered first has it; the other is
reached by its own name, and `ALL_TOOLS`, the helpers and its declaration name
it that way (`tools["read-file"](args: ...)`). A tool whose own name is also
taken, because it is an earlier tool's identifier, cannot be called from a
script and is not listed. `"<name>" in tools` tests for a tool, and iterating
over `tools` yields the names `ALL_TOOLS` lists. A name of the form `__x__` is
reached only as a key, and its declaration says so (`tools["__x__"](args: ...)`).

Reading a tool that does not exist raises `AttributeError` (`tools.<name>`) or
`KeyError` (`tools["<name>"]`). The message names close matches: tools whose
name is the same apart from case and punctuation, otherwise tools whose name
contains the wanted name or is part of it, otherwise the most similar name.
With no match it lists the tools when there are at most 20.

## Globals

| Global | Purpose |
| --- | --- |
| `tools.<identifier>(args)`, `tools["<name>"](args)` | Call a tool with one `dict` or with keyword arguments. |
| `text(value)` | Emit a string unchanged, another value as JSON. |
| `ToolError` | Raised by a tool call that failed. |
| `ALL_TOOLS` | A list with one `{"name": ..., "description": ...}` for every callable tool, in registration order. `name` is the identifier (a key that calls the tool: `tools[entry["name"]]`); `description` is the tool's description followed by its declaration. |
| `search_tools(query, limit=8, namespace=None)` | Rank the callable tools by relevance to `query` and return up to `limit` entries shaped like those of `ALL_TOOLS`, best first. `namespace` restricts the search to the tools of one namespace. |
| `describe_tool(name)` | The `description` of the `ALL_TOOLS` entry for a tool, given its name or its identifier, or `None`. |
| `describe_namespace(name)` | `{"name": ..., "description": ..., "instructions": ..., "tools": [...]}` for a namespace, or `None` when no callable tool belongs to it. `description` and `instructions` are present only when the namespace has them; `tools` lists the identifiers of its callable tools. |

A declaration is the call signature as Python type text, for example:

```python
tools.mcp__dev_radius__search(args: {
  # What to look for.
  "query": str,
}) -> {"hits"?: list[str]}
```

`"hits"?` marks an optional key. A tool without an output schema is declared
`-> str`. The notation is for reading; it is not Python that a script can
evaluate.

The helpers see exactly the tools the script can call. They return metadata,
run no tool and do not appear among the nested calls of the result.
`search_tools` uses BM25 over each tool's name, description, the property
names and descriptions of its input schema, and its namespace's name,
description and instructions. Words are split at punctuation and at camelCase
boundaries and are ASCII only, so a query for `github` does not find a
description that says `GitHub` (that is the words `git` and `hub`).

A namespace groups related tools, as an MCP server will; an
[extension tool](extension-api.md#tool-model) can declare one. Its name and
its aliases refer to it, each as written or as the same identifier:
`mcp__dev-radius`, `mcp__dev_radius`, and with an alias `dev-radius` also
`dev-radius` and `dev_radius`. A namespace's `instructions` are returned only
by `describe_namespace`; they are part of no tool description.

Wrong arguments raise `ValueError`: `search_tools() expects a query string`,
`search_tools() limit must be a positive integer` (an `int`; `None` means the
default), `search_tools() namespace must be a string`, `describe_tool() expects
a tool name` and `describe_namespace() expects a namespace name`. An argument
that is not JSON raises `TypeError`.

The callable tools are fixed when the script starts, after the `tool_call`
hooks of the `codemode` call have run: pipy takes one snapshot of the tools that
are registered and active at that moment. A change made while the script runs
(`ctx.set_active_tools(...)` from a nested tool, a `/reload`) applies from the
next request on and does not change the running script.

The tool description the model sees still names only the seven built-ins and
says that tool results are strings; extension tools, structured results and
the discovery helpers are not described in it yet.

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
Callable tools retain exactly their normal direct-call authority: `read` resolves
any readable host path under the existing tool policy; `bash` may access files,
network and spawn host processes through its normal pipeline; an extension tool
runs its handler as it does for a direct call. Every nested call uses the same
validation, policy, extension hooks, budget, result transformation and
interruption path as a direct call: a `tool_call` hook can block a nested call
and a `tool_result` hook can change its result. Scripts themselves gain no extra
authority.

Completed tool effects persist after script failure or cancellation. The result
retains partial output and a call summary saying effects are **not undone**.
Scripts are not automatically retried after effects.

## Limits and cancellation

| Limit | Default |
| --- | --- |
| Wall deadline | 120 seconds, including startup and time inside tools |
| Guest memory | 256 MiB; exhaustion raises catchable `MemoryError` |
| Source | 256 KiB UTF-8, checked before spawning |
| Protocol line | 1 MiB per line the script sends (a tool call with its arguments, an output message); violation fails the sandbox |
| Tool reply | 16 MiB as JSON per call; a larger result raises `ToolError` |
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
MCP tools, parallel calls, images, `models.*`,
`store()`/`load()`, options lines and structured bash results are deferred. A resident engine requires
an owner decision after T10; fresh workers remain the runtime model.
