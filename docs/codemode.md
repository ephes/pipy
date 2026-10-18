# Python codemode

Codemode lets the model run Python that calls existing tools, filters their text
results and emits a compact answer in one tool call. It is optional and disabled
by default. Every script starts a fresh isolated CPython 3.14.7 WASI worker;
ordinary Python variables do not survive between scripts. Use
[`store()` and `load()`](#keeping-values-between-scripts) for session-scoped values.
The host supports Python 3.11–3.15; upgrading it does not change the pinned WASI
guest version.

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

`codemode` is the built-in extension `builtin:codemode`, as in Pi:
`"extensions": ["-builtin:codemode"]` in the settings (or
`pipy config disable builtin codemode`) and `--no-extensions` remove the tool,
so neither `--tools` nor `defaultTools` nor MCP can select it, and
`--extension builtin:codemode` loads it again for one run (see
[turning MCP off](mcp.md#turning-mcp-off)).

A session made with the [Python SDK](sdk.md#codemode-tool-search-and-mcp) has
`codemode` only when it is created with `codemode=CodemodeOptions()`; the
setting then activates it as above. `CodemodeOptions(mode=...,
inline_budget=...)` replaces the `codemode.mode` and `codemode.inlineBudget`
settings for that session.

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

The script may start with Pi's options line, in Pi's `//` form although it is
not Python; it is replaced by an empty line before the script runs, so line
numbers stay the same:

```python
// @options: {"max_output_tokens": 2000, "timeout_ms": 60000}
text(tools.read({"path": "pyproject.toml"})[:400])
```

- `max_output_tokens` (default 10000) is the budget of the result's text
  (characters / 4). Longer output keeps its start and end, and the full text
  is written to a temp file the result names. pipy's result text holds at
  most 64 KiB, so a budget above 16000 acts as 16000, and as less when the
  path lines of the script's images need the room.
- `timeout_ms` is a deadline for the whole script, tool calls included. There
  is none by default.

Only these two fields are accepted, `max_output_tokens` as an integer from 0
and `timeout_ms` from 1 to 2147483647; anything else, an options line with
no script after it, or an empty script is an error result with Pi's message,
and the script does not run.

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

- An MCP tool returns its whole `CallToolResult` as a `dict`, as in Pi:
  `content` (the list of blocks), and `structuredContent` and `isError`
  when the server sends them. A result with `isError` is returned, not
  raised, so check it; see [what a script sees](mcp.md#what-a-script-sees).
- Any other tool that declares an output schema returns its structured content as a
  Python value (`dict`, `list`, `str`, number, `bool`, or `None` for a
  structured `null`), not as text. It
  returns it for a failure too, when the failed result carries structured
  content, so check the value instead of relying on `ToolError`. Of the
  built-in tools, `bash` and `read` declare one; an
  [extension tool](extension-api.md#tool-model) can.
- `bash` returns `{"output": ..., "truncated": ..., "full_output_path": ...,
  "exit_code": ..., "wall_time_seconds": ...}`, also for a non-zero exit code,
  which raises no `ToolError`: check `exit_code`. `output` is stdout and
  stderr together, not cut to the 2000 lines or 50 KB the model sees: it
  holds up to 1 MiB, and longer output keeps its first and last 512 KiB
  around an `[... N bytes omitted ...]` line, with `truncated` set and the
  full output in the file `full_output_path` names (present only then). A
  command that timed out or was aborted has no such value and raises
  `ToolError`.
- `read` returns the file's text, or for an image it attaches an image block
  `{"type": "image", "data": ..., "mimeType": ..., "note": ...}` that
  `image()` shows; `data` is the base64 image the model would see and `note`
  its text (`Read image file [image/png]`, then any hint lines such as
  `[Image converted from image/bmp to image/png.]` or the original size of
  a resized image). As in Pi, a BMP is converted to PNG and an image over
  2000 x 2000 pixels or 4.5 MB of base64 is resized (with
  `images.autoResize`, see [providers](providers.md#size-of-tool-result-images-and-images-settings)); this needs Pillow,
  the `images` extra. An image that cannot be converted or resized, and
  without Pillow a BMP or an image over those limits, is not attached:
  `read` returns its text with the reason (`[Image omitted: ...]`) instead.
- Every other call returns the tool's text as one `str`. That includes a tool
  that declares an output schema when a result carries no structured content.
- A call that fails without structured content raises `ToolError` with the
  tool's error text, or `Tool "<name>" failed` when the tool gave none. So do
  a call that a `tool_call` hook blocked and a call whose arguments do not fit
  the tool's schema.

A `tool_result` hook sees the result before the script does. A hook that
replaces the content decides the structured content with it: the script
receives the structured content the hook returned, or the replaced text when
the hook returned none.

A structured value is not cut to the 64 KiB a tool's text is limited to. One
tool reply may be up to 16 MiB as JSON in UTF-8 (a non-ASCII character counts
its UTF-8 bytes); a larger one raises `ToolError` in the script, after the
tool has run. A call's arguments may be as large: a script can hand
`tools.write` a file of several megabytes. Arguments over 16 MiB as JSON raise
`ValueError` in the script, and the call is not made. A script still has 256 MiB of memory for the
values it keeps and can emit 16,777,216 characters of output, so reduce a
large value before emitting it.

`text(value)` emits strings unchanged and other values as JSON (falling back to
`repr` for values JSON cannot encode). `print()` and writes to `sys.stdout`
and `sys.stderr` are the script's console output, Pi's `console.*`. Filter
before emitting: bulk raw results can exhaust output budgets and require
another model turn.

The result starts with `Script completed` or `Script failed`, the wall time
and `Output:`, then lays the output out as Pi does, so the model can tell
items apart:

- `text()` items and images appear in order, each on its own line. With more
  than one `text()` item, each starts with a `==> text N/M <==` line.
- The console output follows all of them in one block,
  `<console_output>` ... `</console_output>`, as the script wrote it: a
  `print()` ends its line, `print(..., end="")` and `sys.stdout.write()`
  continue it.
- A failed script keeps this partial output, followed by `Script error:`, the
  error and the tool calls made before the failure.

```text
Script completed
Wall time 0.4 seconds
Output:
==> text 1/2 <==
3 files
==> text 2/2 <==
{"largest": "src/app.py"}
<console_output>
checked src
</console_output>
```

### Calls at the same time

`tools.<name>(args)` waits for its call. `tools.<name>.submit(args)` (also
`tools["<name>"].submit(args)`) starts the call and returns at once, so calls
submitted one after another run at the same time, as `Promise.all` runs
them in Pi. Its `.result()` waits for the call and returns what
`tools.<name>(args)` would have returned, or raises its `ToolError`, every
time it is asked:

```python
calls = [tools.read.submit(path=path) for path in ("alpha.txt", "beta.txt")]
texts = []
for call in calls:
    try:
        texts.append(call.result())
    except ToolError as error:  # like Promise.allSettled: keep the others
        texts.append(f"failed: {error}")
```

The calls start in the order the script submits them: their ids, the
`tool_call` hooks and the nested-call record follow that order, whichever
call finishes first. A script's calls take no place in the per-turn tool
budget (`--tool-budget`), which counts only the calls the model makes; as in
Pi, a script may make any number of calls. Calls of a tool registered with
`execution_mode="sequential"` run one at a time, in that order, as in Pi;
such a call starts at once (its row and its start event) and waits for its
turn before its `tool_call` hooks, and one still
waiting when the script ends does not run and ends as `cancelled`
([running other tools](extension-api.md#running-other-tools)). A call still running when the
script ends, because the script did not ask for its result or because the
script failed, is cancelled and recorded as `cancelled`; a call submitted just
before the script ends has still started. At most 16 calls and discovery
requests can wait for their result at once; a script that submits more waits
for one of them first. Pi has no such limit.

### Keeping values between scripts

`store(key, value)` keeps a JSON value under a string key, and `load(key)`
returns it in a later script, or `None` for a key that is not there.
`store(key, None)` (or `store(key)`) deletes the key. Both work at once, with
no tool call:

```python
cursor = load("issues_cursor")
result = tools.mcp__tracker__list_issues(cursor=cursor) if cursor else tools.mcp__tracker__list_issues()
page = result["structuredContent"]
store("issues_cursor", page["next"])
text([issue["title"] for issue in page["issues"]])
```

- `store()` keeps a copy: changing the value afterwards, or the value
  `load()` returned, changes nothing stored. Tuples come back as lists and
  non-string dict keys as strings.
- What a script stores is kept only when it succeeds; an early `sys.exit()`
  (or `sys.exit(0)`) counts as success. A script that fails, also with
  `sys.exit(1)`, keeps nothing it stored, and `load()` in it still returned
  its own writes.
- A script that changed the store appends one `codemode-store` custom entry,
  `{"set": {...}, "delete": [...]}`, to the session. `load()` reads the
  entries on the current branch, so each branch sees the values written on
  its own path: after `/tree` to an earlier point, the values written after
  it are gone; resume and `/fork` keep what is on the path they continue.
- A value may have 262,144 characters of JSON (counted as written compactly,
  without escaping non-ASCII characters) and all keys and values together
  1,048,576. A value over the limit, or a write that would fill the store
  over it, raises `ValueError`; a key that is not a `str` and a value that is
  not JSON (`NaN` included) raise `TypeError`. The store is for small state
  such as ids and cursors, not for data.

Different from Pi: `None` deletes a key, where Pi deletes with `undefined`
and can store `null`; deleting a key the session's store does not have
writes no entry; sizes count Python characters, not UTF-16 units; and `NaN`
is refused, where Pi stores `null`.

### Images

`image(value)` adds an image to the result, where the model sees it as an
image. `value` is one of:

- an image block as MCP tools return it,
  `{"type": "image", "data": <base64>, "mimeType": ...}`;
- a base64 `data:` URL, `data:image/png;base64,...`;
- a dict with `image_url`, whose value is such a URL.

```python
shot = tools.mcp__browser__screenshot({})
for block in shot["content"]:
    if block["type"] == "image":
        image(block)
```

PNG, JPEG, GIF and WebP are accepted. The type is read from the first bytes
of the data; the declared one is ignored. Line breaks of wrapped base64 are
dropped. Everything else raises `TypeError` in the script, and nothing is
added to the result: a remote URL (`remote image URLs are not supported in
tool outputs. Pass a base64 data URI instead`), a string that is not a
base64 `data:` URL, data that is not complete base64, data of another type
(SVG, for example), and a value of another shape.

Images appear in the result in the order of the script's output, between the
text around them. Each image is also saved to a temp file that only you can
read (`pipy-codemode-<random>.png`, `.jpg`, `.gif` or `.webp`), and a line
`[Image saved to <path> (<type>, <size>)]` stands before it, so a later turn
can copy or move the file. An image shown twice is saved once. When saving
fails, the line is `[Image (<type>, <size>) could not be saved: <reason>]`
and the image is still attached. Images are not cut by the output budget:
when the text is over it, the text becomes one shortened block and the
images follow it. Like any tool result image, an attached image that is too
large is then scaled down (the saved file keeps the original), see
[providers](providers.md#size-of-tool-result-images-and-images-settings).
In the terminal the images are drawn after the codemode row when the
terminal can draw them, see
[images in tool results](tui-workflow.md#images-in-tool-results).

A tool's own images do not reach the model through a script: a tool without
an output schema returns its text, an MCP tool returns its `CallToolResult`
with the image blocks in `content`, and `read` returns an image block. Only what the
script passes to `image()` is attached:

```python
image(tools.read(path="screenshot.png"))
```

Different from Pi: Pi's scripts are JavaScript, where a string is a URL and
an object a block; here a `dict` is the object. Text and the images' base64
data share Pi's limit of 16,777,216 characters; past it the script fails, as
in Pi. `image()` checks the images alone against it: the image that would
pass it ends the run as a failure and then raises `ValueError`, so a script
that catches the error cannot go on to succeed, and neither the output after
it nor its `store()` writes are kept, as Pi's `output()` fails the script
before it throws.

The tool description includes a runnable small-file read/filter example.
Use returned strings directly: `tools.read()` is not a result object, and text
files do not need JSON decoding. Keep intermediate reads in Python and emit the
compact answer after processing. Format probes that print types, dump raw reads
or deliberately raise with their contents add work without completing the task.
Follow read's offset/limit notices when a file is incomplete; the small-file
example does not implement pagination. This is model guidance, not an enforced
single-script rule or a guarantee that a model follows it. See the
[adherence slice](specs/2026-10-09-codemode-adherence.md) for evidence and limits.

A script can call:

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
| `tools.<identifier>.submit(args)` | Start the call and return at once; `.result()` waits for it. See [calls at the same time](#calls-at-the-same-time). |
| `text(value)` | Emit a string unchanged, another value as JSON. `print()` output goes to the [console block](#writing-a-script) instead. |
| `image(value)` | Add an image to the output; see [images](#images). |
| `store(key, value)`, `load(key)` | Keep a JSON value for later scripts on this branch, and read it back; see [keeping values between scripts](#keeping-values-between-scripts). |
| `ToolError` | Raised by a tool call that failed. |
| `ALL_TOOLS` | A list with one `{"name": ..., "description": ...}` for every callable tool, in registration order. `name` is the identifier (a key that calls the tool: `tools[entry["name"]]`); `description` is the tool's description, its prompt guidelines as `- ` lines when it has some, and its declaration. |
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
`search_tools() limit must be a positive integer` (an `int` or a whole
`float` such as `8.0`, as JavaScript's `Number.isInteger` accepts; `None` means
the default), `search_tools() namespace must be a string`, `describe_tool() expects
a tool name` and `describe_namespace() expects a namespace name`. An argument
that is not JSON raises `TypeError`.

The callable tools are fixed when the script starts, after the `tool_call`
hooks of the `codemode` call have run: pipy takes one snapshot of the tools that
are registered and active at that moment. A change made while the script runs
(`ctx.set_active_tools(...)` from a nested tool, a `/reload`) applies from the
next request on and does not change the running script.

## What the model is told

The `codemode` tool description explains the call contract (a call returns a
string, or a structured value when the tool's declaration says so, also for a
failure that carries one; `submit()` starts calls that run at the same time;
calls still running when the script ends are cancelled) and names the globals, including the discovery
helpers as the way to find tools that are not listed. That part names no tool
and is the same in every session.

After it, under `Nested tools:`, the description lists tools a script can
call, each with a heading, its description, its prompt guidelines and its
declaration:

````text
Nested tools:

## records
Record store

### `lookup`
Look a record up.

codemode tool declaration:
```python
tools.lookup(args: {"key": str}) -> str
```
````

- Tools without a namespace come first, then one `## <namespace>` group per
  namespace, sorted by name, with the namespace's description. A namespace's
  `instructions` are never shown here; `describe_namespace` returns them.
- A heading names the tool by the identifier a script writes, and adds the
  tool's own name when that differs: `` ### `mcp__dev_radius__search`
  (`mcp__dev-radius__search`) ``.
- Tools with exposure `deferred` are never listed, and they change nothing
  else in the description. A script finds them with `search_tools`,
  `describe_tool`, `describe_namespace` or `ALL_TOOLS`. The description is
  therefore byte-identical before and after such tools register, and it does
  not change when one of them is activated.
- The listed declarations share a budget of 3000 estimated tokens (characters
  / 4), the [`codemode.inlineBudget`](settings.md#tools) setting. The groups
  take turns: in each round every group places its cheapest remaining tool,
  and a group whose next tool does not fit stops. A namespace that could not
  place all of its tools is marked `(some tools not listed)`, or `(tools not
  listed)` when it placed none. With `0` only the namespace headings remain.

The [`codemode.mode`](settings.md#tools) setting decides which tools are
listed and how the tools the model also sees directly are presented. It
applies only while the built-in `codemode` tool is declared; without it every
tool keeps its own description.

| `codemode.mode` | Declared tools that scripts can call | Listed under `Nested tools:` |
| --- | --- | --- |
| `on` (default) | Stay declared. Each description gets a last line such as ``Codemode: `tools.read(args)` resolves to a string.`` | Callable tools that are not `direct`: tools with exposure `codemode`. |
| `only` | Active `direct` tools are left out of requests. They stay active and callable from scripts. | Every callable tool except `deferred` ones. |

The appended line says what a script call returns: `a string`, the field
names of an object result (`` `{ content, isError? }` ``, nested one level),
or the declared type on one line. With `only`, the system prompt lists the
tools that requests declare, so the hidden tools and their guidelines leave
the prompt; the guidelines appear in the tools' sections of the `codemode`
description instead, and in `describe_tool` and `ALL_TOOLS`, which carry them
in either mode. A tool that is left out of requests is not a tool the model
can call directly: such a call is answered as an unknown tool. `model-only`
tools, `codemode` itself among them, are always declared.

Both settings are read when a session starts and on `/reload`.

A description longer than the 4 KiB that pipy's own tool schemas allow, which
a listing easily is, is declared without that limit.

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
runs its handler as it does for a direct call, and can itself run tools with
`ctx.execute_tool` (see
[running other tools](extension-api.md#running-other-tools)); those calls are
part of the script's `nestedCalls` record. Every nested call uses the same
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
| Wall deadline | none; `timeout_ms` in the [options line](#writing-a-script) sets one, including startup and time inside tools |
| Guest memory | 256 MiB; exhaustion raises catchable `MemoryError` |
| Source | 256 KiB UTF-8, checked before spawning |
| Protocol line | 1 MiB per line the script sends; violation fails the sandbox. Longer text is sent in pieces, an image message may be as long as the output limit allows, and a tool call with its arguments as long as a tool reply |
| Tool call | 16 MiB of arguments as JSON (UTF-8) per call; larger arguments raise `ValueError` and the call is not made. Pi has no limit |
| Tool reply | 16 MiB as JSON (UTF-8) per call; a larger result raises `ToolError` |
| Emitted output | 16,777,216 characters of text and base64 image data together, or 100,000 `text()`, `image()` and print calls; excess fails the script (Pi's limits) |
| Emitted images | within the output limit; the `image()` call whose images' base64 data would pass 16 MiB fails the script, also when the script catches its `ValueError` |
| Result display | 10,000 estimated tokens (characters / 4), head and tail; `max_output_tokens` sets it, up to 16,000 |
| Worker diagnostics | 64 KiB stderr retained |
| CPU backstop | wall deadline + 5 seconds; 25 days without a deadline |

Before a script starts, pipy waits for the MCP servers the script names that
are still connecting (see [waiting for servers](mcp.md#waiting-for-servers));
that wait is not part of the wall limit, and Escape or an RPC abort ends it
and cancels the script. Escape cancels computation or the active nested tool
through the normal input waiter. The builtin product runner retains one outer
raw input owner from before script launch through executable cleanup, across
nested-tool/computation handoffs; the same session-thread waiter reads Escape.
Later canonical result hooks are outside that scope. Intentional outer raw
transitions and foreign-I/O resumption retain their flushing policy.
RPC abort works during either phase. The host wall deadline includes tool
time and stops an active child; the CPU backstop is secondary. Worker groups are
killed and reaped; subsequent scripts start fresh. As with direct tools, a
noncooperative host tool can outlive its bounded cancellation join. Accepted
effects are not rolled back.

Display truncation spills exactly the untruncated output/error body to an
owner-only host temporary `pipy-codemode-*.log` file. The result names its path
and supplies `details.fullOutputPath` and character/UTF-8 byte metadata. Files
remain readable later through normal tools. Spilling cannot restore output beyond
the 16,777,216-character emission cap. Open/write/close failures report that full output could
not be saved, publish no path and preserve the primary outcome and nested records.

## Nested evidence and availability

Live children appear inside the parent row, as in Pi's codemode renderer:
collapsed, the latest eight calls after `... (N earlier calls, ctrl+o to
expand)`; Ctrl+O shows every call the row keeps. Pi keeps every call; the
row keeps the latest 256 while the script runs. Past that it lets the
oldest finished call go for each new one (a call still running stays) and
an expanded row starts with `... (N earlier calls, not kept)`. So the
latest calls and failures stay visible however many calls a script makes.
Below them the row shows the script's output as Pi's codemode renderer does:
without the `Script completed`, `Wall time` and `Output:` lines, nothing while
the script runs, and collapsed only the first five lines as wrapped at the
row's width (script output is often one long JSON line), followed by
`... (N more lines, ctrl+o to expand)` and, when the output was spilled,
`Full output: <path>`. Ctrl+O shows all of it, with the notice that ends a
spilled result. Unlike Pi, the call is still drawn as `codemode code="…"`
rather than as the highlighted script, and the row adds no `Model calls:`
cost line. The nested calls and the output are the result part of
codemode's rows, as in Pi, where `codemodeRenderers.renderResult` draws
both: renderers of an extension that replace codemode's draw no nested
calls of pipy's, and a resolver that wraps codemode's rows gets them from
the result part (see
[Tool rendering](extension-api.md#tool-rendering)).
JSON/RPC child lifecycle events, live output (`tool_execution_update`) included,
carry `parentToolCallId`. Only the parent result
enters provider and durable product history, with a bounded `nestedCalls` record:
256 calls, 8 KiB arguments per call, 32 KiB total arguments and 500 characters
per error. Omitted evidence marks the record incomplete; nested result bodies
are not stored there. For a call whose arguments the record leaves out, the
result's `details` keep the first 200 characters of their JSON in
`argumentPreviews` (by call id), as Pi's `details.calls` keep each call's
`args`, so the call's row still shows them after a resume and in the export.
Resume/tree reconstruct children from the parent. Host
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
`models.*` is deferred. A resident engine requires
an owner decision after T10; fresh workers remain the runtime model.
