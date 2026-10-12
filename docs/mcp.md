# MCP

pipy connects to the stdio MCP servers of your `mcp.json` when a session
starts and makes their tools available to the model and to codemode scripts.
The work follows the
[MCP parity plan](specs/2026-10-12-mcp-parity-plan.md) and lands in slices;
this page grows with them. [Quick setup](#quick-setup) shows what works today
and [what is missing](#what-is-missing) lists what does not yet: among other
things HTTP servers, OAuth, `/mcp` and resources.

## Quick setup

Add a server from the shell and check that it connects:

```bash
pipy mcp add filesystem --exposure direct -- npx -y @modelcontextprotocol/server-filesystem .
pipy mcp list
```

With `-l` the server goes into the project's `.pipy/mcp.json`; a value such
as `${TOOLS_KEY}` is kept as written and read from the environment when the
server starts:

```bash
pipy mcp add -l tools --env API_KEY='${TOOLS_KEY}' -- uvx tools-mcp
```

See [the `pipy mcp` commands](#the-pipy-mcp-commands). Or write the servers
yourself into `mcp.json` in the config home (`~/.pipy/mcp.json`
unless `PIPY_CONFIG_HOME` or `XDG_CONFIG_HOME` say otherwise), or into
`.pipy/mcp.json` of a trusted project:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
      "exposure": "direct"
    }
  }
}
```

Start pipy. Every enabled server starts connecting in the background, and
pipy waits for a server only when its tools are needed (see
[waiting for servers](#waiting-for-servers)). When a server connects, its
tools are registered as
`mcp__<server>__<tool>`, here for example `mcp__filesystem__read_file`. The
file is read once, when the session starts: restart pipy after changing it.
When the session ends, pipy closes every server and waits until its process
is gone, also for a server that was still starting.

How the tools are reached depends on the server's `exposure` and the
per-tool `toolExposure`, see [control tool exposure](#control-tool-exposure).

### Control tool exposure

| Exposure | Behaviour |
| --- | --- |
| `codemode` (default) | Not declared to the model. [Codemode](codemode.md) scripts call the tools (`tools.mcp__filesystem__read_file(path="a")`) and find them with `search_tools()`. |
| `deferred` | Not declared until [`tool_search`](#tool-search) loads a match; the model then calls the tool directly. |
| `direct` | Declared to the model like a built-in tool, and callable from scripts. The first prompt waits for the server, so its first request declares the tools; a server that connects later adds them to the next model request. |
| `hidden` | Registered, and cannot be called. |

`toolExposure` sets the exposure of single tools, see
[configuration rules](#configuration-rules).

pipy turns on the tool that reaches the undeclared tools. When an enabled
server has a tool with `codemode` exposure it activates `codemode`, and when
one has a tool with `deferred` exposure it activates `tool_search`. It decides
this from the configuration when the session starts, before any server
connects, so the first model request already declares the tool. Set
`"autoEnableCodemode": false` at the top level of `mcp.json` to keep
`codemode` off.

Either tool reaches both exposures: scripts can call `deferred` tools, and
`tool_search` can load `codemode` tools. So with `autoEnableCodemode` false
you can still reach the tools by enabling `tool_search` yourself
(`"defaultTools": ["+tool_search"]`).

When such tools exist and neither tool is active, pipy warns once in a
session:

```text
MCP tools are only reachable from the codemode or tool_search tool, but neither is active; they cannot be called.
```

With ` (autoEnableCodemode is false)` after `active` when that setting is the
cause. Other causes: `--tools`, `--exclude-tools` or `--no-tools` removed
both tools, or codemode's runtime is not installed. Codemode needs an
[installed runtime](codemode.md#installing); without it pipy also prints
`pipy: codemode unavailable: <reason>`, and the tools of a server with the
default exposure cannot be called until you install the runtime or choose
another exposure.

`--tools` and `--exclude-tools` apply to MCP tools as described under
[MCP tools](usage.md#mcp-tools).

The model is not given the tools of `codemode` and `deferred` servers, so it
is told which servers there are, see
[the servers in the system prompt](#the-servers-in-the-system-prompt).

Different from Pi:

- Pi always has `codemode` registered. pipy registers its built-in `codemode`
  when something asks for it, because registering it tests the runtime with a
  short self-test. A server with `codemode` exposure asks for it when the
  session starts, so such a session starts a little later than one without.
- With `autoEnableCodemode` false pipy does not test the runtime. The warning
  then names that setting whenever the tool filters would keep `codemode`,
  also when the runtime is not installed.

### The servers in the system prompt

Every enabled server that has `codemode` or `deferred` tools is listed in the
`mcp_servers` section of the system prompt, sorted by name:

```text
<mcp_servers>
MCP servers whose tools are not declared to you. Call the tools of `codemode` servers from codemode scripts. Load the tools of `tool_search` servers with `tool_search`.
- mcp__docs (codemode): Search the product docs
- mcp__tracker (tool_search): Issues and pull requests of the tracker.
</mcp_servers>
```

- The first line explains only the ways the listed servers use.
- A server is listed as `codemode` when its `exposure`, or any `toolExposure`
  value, is `codemode`, and as `tool_search` otherwise. This is read from the
  configuration, so a server is listed before it has connected. A server
  that has only `direct` tools is not listed, because the model is given
  its tools themselves. A server that has only `hidden` tools is not listed
  either: its tools cannot be called. Without a listed server there is no
  section.
- The text after the colon is the first line of the server's `description`
  in `mcp.json`. Without one it is the first line of the instructions the
  server sent when it connected, so it is missing until then. It is cut to
  250 characters, ending in `…`.
- The section is at most 4096 characters long. With many servers the
  summaries are cut to the same shorter length. When even the names do not
  fit, the last servers are left out and the section ends with
  `- … <N> more servers; find their tools with search_tools()`.

pipy writes the section when a prompt starts, after the
[first prompt's wait](#waiting-for-servers) for servers with `direct` tools.
When it differs from the section the conversation already has, for example
because a server connected and its summary is now known, the new section is
appended to the conversation as a change of the system prompt. The earlier
messages and the tool declarations stay as they are, so a provider's prompt
cache keeps them. The rest of a server's instructions is never put into the
prompt; a script reads them with `describe_namespace("mcp__docs")`.

An extension can read, change or remove the section before a run, see
[prompt sections](extension-api.md#prompt-sections).

Different from Pi: the closing line names `search_tools()`, the name of the
function in pipy's Python scripts (`searchTools()` in Pi). A summary is never
cut in the middle of a character; Pi can cut one outside the Basic
Multilingual Plane in half.

### Turning MCP off

- `pipy --no-mcp` turns MCP off for one run: no `mcp.json` is read, no server
  connects and there are no MCP tools.
- `"extensions": ["-builtin:mcp"]` in the [settings](settings.md) turns it off
  until you remove the entry. A `+builtin:mcp` or `-builtin:mcp` entry in the
  settings of a trusted project decides over the user-level entry.
  `pipy config disable builtin mcp` and `pipy config enable builtin mcp`
  write the entry (with `--scope project` into the project's settings), and
  `pipy config list` shows the state under `built-in`.
- `--no-extensions` turns MCP off as well. `--extension builtin:mcp` turns it
  on for the run, also against the settings entry and `--no-extensions`:
  `pipy -ne -e builtin:mcp` runs without extensions and with MCP.
  `--no-mcp` wins over it.

With MCP off, pipy does not turn on `codemode` or `tool_search` and reports
nothing about `mcp.json`. `mcp` is the only built-in entry; Pi also has
`builtin:codemode`, `builtin:tool-search` and `builtin:llama.cpp`, which in
pipy are selected as tools (`--tools`, `defaultTools`) or do not exist. An
unknown `builtin:` name after `--extension` is an error at startup.

### Waiting for servers

Servers connect while you type, so their tools are usually there when they
are needed. Where they might not be, pipy waits:

- **The first prompt** of a session waits for the servers that have `direct`
  tools (the server's `exposure`, or any `toolExposure` value, is `direct`)
  and are still connecting, so that the first model request declares those
  tools. It waits at most 10 seconds. After that the prompt is sent and pipy
  shows `MCP servers are still connecting; their tools become available once
  connected.`; the tools follow with the request after the server connected.
  Later prompts do not wait. Servers without `direct` tools never hold a
  prompt.
- **A codemode script** waits for the servers it names before it starts.
  pipy reads the script's source: `tools.mcp__docs__search(...)` and a name
  built as `"mcp__docs__" + kind` both wait for the server `docs` (a `-` in
  a server's name is a `_` in the script). A script that uses
  `search_tools`, `describe_namespace`, `describe_tool` or `ALL_TOOLS` waits
  for every enabled server, since it may call whatever it finds. A script
  that names no server waits for none.
- **`tool_search`** waits for every enabled server, so it searches all their
  tools.
- **A call to an MCP tool** waits for its own server while a new connection
  attempt for it is under way, for example after `/mcp reconnect`.

A wait for a tool call has no time limit of its own. It ends when the server
connected or its connection failed (the call then runs and reports what it
finds), and when you cancel the turn: Escape in the terminal, or an abort in
the JSON and RPC modes, ends the wait and cancels the call like a running
tool. Escape during the first prompt's wait only ends the wait: the prompt
is sent without waiting further.

An extension tool that reaches other tools gets the same wait by declaring
`reaches_tools`, see the
[extension API](extension-api.md#extension-tool-metadata).

Different from Pi:

- In Pi the first prompt's wait cannot be interrupted; it always runs to its
  10 seconds or to the server's answer.
- Pi lets an embedding program set the 10 seconds (`startupWaitMs`). pipy has
  no setting for it yet.
- A `reaches_tools` function that raises fails the call in Pi. In pipy the
  call waits for every server and then runs, as if the function had returned
  `"all"`; the same holds for a return value that is neither `"all"` nor a
  list of words.

### Permissions

A call to an MCP tool, from the model or from a script, runs through the same
tool pipeline as a call to a built-in tool. An extension's `tool_call`
handler can block it and a `tool_result` handler can change its result.
`ctx.get_all_tools()` reports each MCP tool with its exposure, its namespace
and the hints its server declares (`read_only_hint`, `destructive_hint`,
`idempotent_hint`, `open_world_hint`), so a permission extension can decide
by them. The hints are what the server says about itself; pipy does not
check them. A stdio server runs with your permissions, like `bash`.

### When tools change

A server can announce a new tool list while it runs. New tools are
registered, and a tool the server no longer offers is removed: the model is
not given it any more and a call to it is an unknown tool. A tool that comes
back is available again with its configured exposure. A call that was
already on its way fails with one of these messages:

| Message | When |
| --- | --- |
| `MCP tool "<server>/<tool>" is no longer available.` | The server withdrew the tool, or its exposure is `hidden`. |
| `MCP server "<server>" is disabled.` | The server is disabled, or the session has ended. |
| `MCP server "<server>" is still starting.` | The server has no connection yet. |

When a server process exits, its tools stay registered and the next call
starts it again.

### Startup report

When every server that started with the session has connected or failed,
pipy reports what needs you, once, as one warning:

```text
MCP servers need attention:
  config: /home/me/.pipy/mcp.json: server "old": legacy SSE transport is not supported; use the streamable HTTP URL
  search: failed: [Errno 2] No such file or directory: 'no-such-command'
Run /mcp to fix.
```

There is one `config:` line per error in an `mcp.json` and one line per
server that failed (with the first line of its error) or needs a sign-in
(`<name>: needs sign-in`). Nothing is shown when there is nothing to report.
[`/mcp`](#the-mcp-command) shows every server with its whole error. After
correcting `mcp.json`, restart pipy.

The report and the other warnings on this page are shown before the next
prompt runs, not at the moment they arise: in the terminal you see them when
you send your next message, in a one-shot run on stderr at the end. Pi shows
them at once.

Two more cases are reported, each once:

- `MCP tool "<server>/<tool>" is not available: <reason>` for a tool pipy
  cannot declare, which is one whose input schema is not an object. The
  server's other tools are registered. Pi registers such a tool as it is.
- `MCP tool "<name>" is not available: another tool has that name` when a
  built-in or extension tool already has the name an MCP tool would get. The
  MCP tool never replaces the other tool.

Log messages a server sends are in `mcp.log`, see [server log](#server-log).

### The `/mcp` command

`/mcp` prints one line per configured server, after the servers that started
with the session have connected or failed:

```text
docs: connected, 2 tools (direct)
search: failed (codemode)
    [Errno 2] No such file or directory: 'no-such-command'
files: disconnected, reconnects on next call (codemode)
    Connection closed
old: disabled (codemode)
config error: /home/me/.pipy/mcp.json: server "sse": legacy SSE transport is not supported; use the streamable HTTP URL
```

Each line is `<name>: <state> (<exposure>)`. A connected server also shows
how many tools it offers. The whole error of a server that is not connected
follows its line, indented. The states are `connected`, `connecting`,
`failed`, `disconnected, reconnects on next call`, `disabled`, `starting`
(enabled, and its previous connection is still closing) and `closed`. After
the servers come the errors of the `mcp.json` files. Without any server pipy
prints `No MCP servers configured. Add them to <config home>/mcp.json or
.pipy/mcp.json.`

`/mcp reconnect [server]` closes a server's connection and connects again,
which also starts a stdio server's process again. It answers `Reconnected to
MCP server "<name>" (<state>).` or with the error of the failed connect.
Without a name pipy takes the only enabled server; with several, the only one
that is failed or disconnected; otherwise it asks you to choose one in the
terminal, and does nothing where it cannot ask. A disabled server cannot be
reconnected (`No enabled MCP server to reconnect.`), and an unknown name
gives `No MCP server named "<name>".`

`/mcp login [server]` and `/mcp logout [server]` exist as names only: no
server uses OAuth yet, so both answer `No enabled MCP server uses OAuth. Only
HTTP servers without an Authorization header do.` Anything else prints
`Usage: /mcp, /mcp login [server], /mcp logout [server], /mcp reconnect
[server]`.

In the terminal, `/mcp ` completes the three actions and, after `reconnect`,
the servers that can be reconnected with their state.

Every form waits for the servers that are still connecting from the start of
the session. Escape ends that wait: `/mcp` then prints the servers as they
are, and a subcommand is not run (`MCP servers are still connecting; /mcp
reconnect was not run.`). Escape during a reconnect stops the waiting only;
the reconnect goes on, and `/mcp` shows how it ended.

Different from Pi:

- In Pi's terminal, `/mcp` without arguments opens a manager where servers
  are enabled, disabled and given another exposure. pipy has no such manager
  yet and prints the status there too, so those three changes cannot be made
  in a session; edit `mcp.json` and restart pipy.
- pipy runs `/mcp` only for a line typed into a session. A prompt given on
  the command line (also with `--print` or `--mode json`) or sent by an RPC
  client is never run as a slash command; it goes to the model. Pi runs the
  command there too.
- A prompt template or custom command named `mcp` takes the place of the
  command, as does an extension command of that name. In Pi only an
  extension command does.
- Pi's command cannot be interrupted; the Escape rules above are pipy's.
- With MCP [turned off](#turning-mcp-off) there is no `/mcp` command.

### What is missing

Not built yet, in the order the plan builds it: enabling and disabling a
server and changing its exposure in a session (the `/mcp` manager), reading
`mcp.json` again on `/reload`, resources, HTTP servers, OAuth, servers
registered by extensions, and images in tool results.

Until `/reload` handles MCP: `/reload` does not read `mcp.json` or the
`-builtin:mcp` entry again; restart pipy. When a server connects or changes its tools
while a `/reload` is being prepared, that reload is refused with `prepared
reload owner state changed`; run it again. A new session in the same pipy
process (`/new`, `/resume`) keeps the servers of the first one, where Pi
closes them and reads `mcp.json` again.

## Tool search

`tool_search` is a built-in tool that finds tools the model has not been
given and loads them. It is off by default; enable it with
`"defaultTools": ["+tool_search"]` or `--tools +tool_search`. An extension
can activate it with `ctx.set_active_tools(...)`.

A tool with exposure `codemode` or `deferred` is registered without being
declared to the model. Extension tools can have these exposures today, see
[tool metadata](extension-api.md#extension-tool-metadata); MCP tools will have them by
default. `tool_search` takes a `query` and an optional `limit` (default 8),
ranks the registered `codemode` and `deferred` tools that are not declared
yet, and activates the best matches. The next model request declares them,
and the model calls them like any other tool. It uses the same ranking as
[`search_tools()`](codemode.md#globals) in codemode scripts: the query
is matched against a tool's name, its description, the descriptions and
property names of its input schema, and its namespace.

The result names what was loaded, one line per tool with the first line of
its description:

```text
Loaded 2 tools. They are available from your next call:
- docs_search: Search the docs.
- docs_fetch: Fetch one page of the docs.
```

When nothing matches, or every matching tool is already declared, the result
is `No matching tools found.` An empty query fails with `query must not be
empty`, and a limit that is not a positive integer fails with `limit must be
a positive integer`.

Loaded tools are recorded in the session like other tool changes, so they
stay declared on that branch: `/tree` to an entry before the load drops the
tool, and `/tree` to a later entry declares it again.

`tool_search` never loads `direct`, `model-only` or `hidden` tools, and it
does not load a tool that `--tools` or `--exclude-tools` filters out. With
`--tools read,tool_search` it can still load the MCP tools the list does not
name, because a list of plain names keeps them registered (see
[MCP tools](usage.md#mcp-tools)). Scripts
cannot call it; they use `search_tools()` and call the tool they found. Its
description is fixed text that names no tool, so it does not change when
tools register.

Differences from Pi:

- When the listing would be longer than one tool result (64 KiB), the lines
  that do not fit are replaced by `(and <n> more)`. Every matched tool is
  loaded all the same.
- `limit` is declared as a JSON number, as in Pi. A value of another type
  (`"3"`) is refused by argument validation; Pi converts it first.
- `tool_search` is registered with pipy's own built-in tools. An SDK caller
  that passes its own tool registry does not get it.

## Configure servers

User-level servers are in `mcp.json` in the config home (`PIPY_CONFIG_HOME`,
then `${XDG_CONFIG_HOME}/pipy`, then `~/.pipy`, then `~/.config/pipy`), project
servers in `.pipy/mcp.json`. The project file is loaded only for a
[trusted project](settings-config.md#project-trust-complete-runtime-track-shipped).
A missing file is not an error. A project entry replaces a user-level entry
with the same name.

A project entry without `command`, `url` and `type` overrides only `enabled`,
`exposure` and `toolExposure` of the user-level server with the same name and
keeps the rest, including `env`, `headers`, `oauth` and `auth`. For example,
this turns off a user-level server in one project:

```json
{
  "mcpServers": {
    "internal-tools": { "enabled": false }
  }
}
```

The format matches other MCP clients:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
    },
    "docs": {
      "url": "https://example.com/mcp",
      "headers": { "Authorization": "Bearer ${DOCS_TOKEN}" },
      "description": "Search and read the product documentation"
    }
  }
}
```

Stdio servers use `command`, `args` (an array of strings), `env` (names to
strings) and `cwd`. HTTP servers use `url`, `headers` (names to strings) and
`oauth`. The legacy SSE transport is not supported.

Both server types accept:

- `timeout`: a positive number of seconds.
- `enabled: false`: keep the entry without connecting to it. A disabled
  server stays in the loaded list.
- `exposure` and `toolExposure`: how the server's tools reach the model, see
  the rules below.
- `description`: what the server offers, in a sentence.

The top-level key `autoEnableCodemode` (a boolean, default true) says whether
the codemode tool is activated for a server with `codemode` exposure, see
[control tool exposure](#control-tool-exposure). A project value overrides
the user-level one.

The loader checks types and keeps the values as written. What the fields do
when a server is connected is described under
[server connections](#server-connections).

### Configuration rules

- Server names may contain only letters, digits, `_` and `-`. The namespace
  of a server's tools is `mcp__<server>` with `-` written as `_`, so two names
  that differ only in `-` and `_` conflict and the later one is rejected:
  `server "<a>" conflicts with "<b>"`.
- `type` is optional. A `command` selects stdio and a `url` selects streamable
  HTTP. When present, `type` must be `stdio` (with `command`), or `http` or
  `streamable-http` (with `url`).
- `sse` is rejected. Servers that document an SSE endpoint often also provide
  streamable HTTP, commonly at `/mcp` instead of `/sse`.
- `command` is one executable and `args` contains its arguments. It is not a
  shell command string.
- `url` must be an `http` or `https` URL.
- `exposure` is `codemode` (the default), `deferred`, `direct` or `hidden`.
  `codemode-deferred` is accepted as another name for `codemode`.
- `toolExposure` maps tool names to exposures. An exact name wins; otherwise
  the first key containing `*` that matches, in the order written; otherwise
  the server's `exposure`. `*` matches any characters and every other
  character is literal.
- A project entry cannot set `auth`, and a project override can set only
  `enabled`, `exposure` and `toolExposure`. An override needs a user-level
  server of that name.
- `oauth` is checked as far as it can be without a server: `clientId`,
  `clientSecret` and `scope` are strings, `clientName` is a non-empty string,
  `callbackPort` is 1 to 65535, `callbackUrl` is an `http` URL on `localhost`,
  `127.0.0.1` or `[::1]` without query or fragment whose port, when it has
  one, equals `callbackPort`, `authServerMetadataUrl` is `https` or `http` on
  a loopback host, and `clientRegistration` is `dcr` or `cimd`. `cimd` cannot
  be combined with `clientId` or `clientName` and requires
  `clientMetadataBaseUrl`, an `https` URL; a `callbackUrl` set with it must be
  on `localhost` or `127.0.0.1` with path `/callback`.
- `auth` needs a non-empty `provider` and a server URL that is `https`, or
  `http` on a loopback host.
- An invalid entry is reported as `<path>: <reason>` and skipped. The other
  entries, and the other file, still load. A file that is not JSON, or whose
  top level or `mcpServers` is not an object, is reported as a whole.

The library can also change a file: set one server's `enabled` or `exposure`,
add a server, or remove one. An edit keeps all other content and the file's
indentation. [`pipy mcp add` and `pipy mcp remove`](#the-pipy-mcp-commands)
use the last two; nothing sets `enabled` or `exposure` yet, `/mcp` will.

Differences from Pi in the configuration:

- pipy checks URLs with a small strict parser and does not repair them. A URL
  without `//`, with a backslash, or with a shortened IPv4 address such as
  `127.1` is a configuration error here, where Pi's URL parser accepts it.
- `oauth.clientMetadataBaseUrl` exists only in pipy. Pi serves its Client ID
  Metadata Documents from its own site; pipy has no such document, so `cimd`
  needs the address where you host one.
- A file that is not valid UTF-8 is an error, and JSON error messages are
  Python's.
- A whole-number `oauth.callbackPort` written as `8080.0` is accepted.

## The `pipy mcp` commands

```bash
pipy mcp add <server> [options] -- <command> [args...]
pipy mcp add <server> [options] --url <url>
pipy mcp remove <server> [-l]
pipy mcp list [--json]
```

The commands work without a session, in the current directory. They load no
extension and read no settings, so they see only the servers of the two
`mcp.json` files, and [turning MCP off](#turning-mcp-off) for sessions does
not turn them off. `pipy mcp`, `pipy mcp help` and `--help` or `-h` anywhere
after `mcp` print the usage text.

### Add and remove

`add` writes one entry and `remove` deletes one, in the user-level `mcp.json`
or, with `-l` or `--local`, in `.pipy/mcp.json` of the current directory. Both
keep the rest of the file and its indentation. `add` replaces an entry of the
same name, checks the entry with the
[configuration rules](#configuration-rules) first and writes nothing when it
is invalid. It does not connect to the server.

After the server name and the first word of the command, everything belongs
to the command, so the command's own options need no `--`:

```bash
pipy mcp add files node server.js --port 1
```

Options of `pipy mcp add` go before the command, or before `--`:

| Option | Applies to | Writes |
| --- | --- | --- |
| `--env KEY=VALUE` (repeatable) | stdio | `env` |
| `--cwd <dir>` | stdio | `cwd` |
| `--url <url>` | HTTP | `url`, in place of a command |
| `--header KEY=VALUE` (repeatable) | HTTP | `headers` |
| `--bearer-token-env-var <NAME>` | HTTP | the header `Authorization: Bearer ${NAME}` |
| `--oauth-client-id`, `--oauth-client-secret`, `--oauth-callback-port`, `--oauth-client-name` | HTTP | `oauth.clientId`, `oauth.clientSecret`, `oauth.callbackPort`, `oauth.clientName` |
| `--exposure <mode>` | both | `exposure` |
| `--description <text>` | both | `description` |

An option for the other kind of server is an error (`--env only applies to
stdio servers.`), as is a pair without `=` or without a key. A value is the
next argument, whatever it looks like.

`add` prints what it did and how to check it:

```text
Added global MCP server "files" in /home/me/.pipy/mcp.json.
Check it with: pipy mcp list
```

For a project server in a project that is not trusted it adds that the file
is ignored until you start pipy there and trust the project. For an HTTP
server without an `Authorization` header the last line also names `pipy mcp
login <server>`; that command, like the rest of OAuth, does not exist yet.

`remove` fails when the file does not have the entry and says where the
server is defined instead: `It is defined in <path>; use --local.` or `...;
omit --local.`

### List

`pipy mcp list` connects to every enabled server, all at the same time, reads
its tools and closes the connection again. For each server it prints the
state, the exposure and the scope, then the command or URL, and after that
what applies: the project file that overrides it, its tools, and the error.

```text
filesystem: connected, 2 tools (direct, global)
  npx -y @modelcontextprotocol/server-filesystem .
  tools: read_file, write_file [hidden]
broken: failed (codemode, global)
  no-such-server
  [Errno 2] No such file or directory: 'no-such-server'
parked: disabled (codemode, project)
  node server.js
config error: /home/me/.pipy/mcp.json: server "bad" needs either "command" (stdio) or "url" (streamable HTTP)
```

A tool is followed by its exposure in brackets when `toolExposure` gives it
another one than the server's. The exit status is 1 when an entry of a file
is invalid or an enabled server is not connected, else 0, so a script or an
agent can use the command to check an `mcp.json` it wrote. A server that
does not answer is given up after its `timeout` (60 seconds by default).

`--json` prints one object instead:

```json
{
  "servers": [
    {
      "name": "filesystem",
      "scope": "global",
      "source": "/home/me/.pipy/mcp.json",
      "enabled": true,
      "exposure": "direct",
      "transport": "npx -y @modelcontextprotocol/server-filesystem .",
      "state": "connected",
      "tools": [
        "read_file",
        "write_file"
      ],
      "toolExposure": {
        "write_file": "hidden"
      }
    }
  ],
  "errors": []
}
```

`override` (the project file that overrides the server), `toolExposure` and
`error` are present only when they apply; `note` is the line about an ignored
project file.

The project file is read only when a decision to trust the project is
already saved; the commands never ask, and the `defaultProjectTrust` setting
and `--approve` do not count. A project file that exists but is not read is
reported: `<path> is ignored because the project is not trusted. Start pipy
in the project to trust it.`

### Differences from Pi

- `pipy mcp login` and `pipy mcp logout` do not exist yet and say so; the
  usage text does not list them.
- A server with a `url` is listed as `failed`, because pipy cannot connect to
  HTTP servers yet.
- The list has no `resources:` line and no `resources` and
  `resourceTemplates` fields yet.
- `--oauth-callback-port` takes a decimal number. Pi also reads JavaScript's
  other spellings of a number, such as `0x50`.
- The usage text and the hint are plain text; Pi prints parts of them bold
  or dim.
- A trust store that cannot be read ends `list`, and `add` to a project file
  before it writes, with the store's error and status 1.
- Error texts of the operating system and of the JSON parser are Python's.

## Server connections

What pipy does with a configured server. Only stdio servers can be connected
so far; an entry with a `url` fails to connect with a message that says so.

### Values resolved at connect time

- `timeout` is the time a request to the server may take, 60 seconds when not
  set. A progress notification for a request starts its time again.
- Each `env` value may refer to environment variables with `${NAME}` or
  `$NAME`; `$$` is a literal `$` and `$!` a literal `!`. A value that starts
  with `!` is a shell command, and its trimmed standard output is the value.
  The command runs on every connect, with a limit of 10 seconds. A variable
  that is unset or empty, or a command that fails or prints nothing, fails
  the connect with a message naming the server and the key, for example
  `Failed to resolve MCP server "docs" env "API_KEY" from environment
  variable: DOCS_TOKEN`. The server process gets pipy's environment plus the
  resolved `env`.
- A leading `~` or `~/` in `command`, in each of `args` and in `cwd` is the
  home directory. `--root=~/x` is passed as written.
- A relative `cwd` is relative to the session directory, and the session
  directory is the default.

The same rules will apply to `headers` and `oauth.clientSecret` of HTTP
servers once those can be connected.

### States

A server is `connecting`, `connected`, `disconnected`, `failed` or `closed`
(`needs-auth` comes with sign-in).

- The first call connects. Calls that arrive while a connect is in progress
  wait for that one attempt. A stdio connect is not retried.
- The tools are listed only when the server declares the `tools` capability.
  When the server sends `notifications/tools/list_changed` they are listed
  again; if that fails, the error is `Failed to refresh tools: <reason>` and
  the earlier tools stay.
- The server's `instructions` are kept with surrounding whitespace removed.
- A connect that fails leaves the server `failed` with the reason, followed
  by the last 2000 characters of the server's stderr, and the call fails with
  `MCP server "<name>" failed to connect: <reason>`. The next call tries
  again.
- When the server exits or the connection drops, the server is
  `disconnected` with the error `Connection closed` and the end of its
  stderr. The next call starts it again.
- Closing a server stops a connect in progress and returns when the server
  process is gone. Calls then fail with `MCP server "<name>" is shut down`.

pipy tells the server its name (`pipy`) and version, and announces one root:
the session directory as a `file://` URI, named by its last path component.

### Server log

Log messages a server sends with `notifications/message` are appended to
`mcp.log` in the config home, one per line:

```text
2026-10-12T08:30:05.120Z [docs] warning db: slow query
```

The level is `info` when the message has none, the logger name is left out
when there is none, data that is not text is written as JSON, and further
lines of a message are indented by four spaces. When the file is larger than
5 MB it is renamed to `mcp.log.1`, replacing an earlier one, before the next
line is written. A line that cannot be written is dropped. The text comes
from the server; pipy creates the file readable by its owner only.

Differences from Pi in connections:

- Config values with `${NAME}` follow Pi's current rules. pipy's API key
  settings still use the older rule (a whole value that is the name of a
  variable); the two are separate.
- A failed refresh of the tool list is recorded only for the connection that
  is current. In Pi a refresh that fails after the connection was replaced
  still sets the error.
- A caller waiting for a connect stops waiting when its call is cancelled;
  the connect goes on for the other callers.
- Messages of failures come from Python: a missing executable is reported as
  `[Errno 2] No such file or directory: '<command>'`.
- The stderr tail is 2000 characters, not 2000 UTF-16 units, and a JSON
  number such as `1.0` in log data is written `1.0`, not `1`.
- `mcp.log` is created with mode 0600.

## Server tools

How a server's tools become pipy tools, and what the model and a script see
of a tool's result.

### Names

A tool is named `mcp__<server>__<tool>`, with every character other than
letters, digits and `_` replaced by `_`: the tool `get.item/v2` of the server
`my-server` is `mcp__my_server__get_item_v2`. The name is also what a
codemode script calls, `tools.mcp__my_server__get_item_v2(...)`.

- A name longer than 64 characters is cut and ends in `_` and eight
  hexadecimal digits computed from the server and tool name.
- When two tools of a server would get the same name (`a-b` and `a_b`), both
  get such a suffix, whatever their order in the server's list. When one of
  them disappears from a later list, the other has its plain name again.
- A name that one tool had in a session is never given to a different tool,
  of this or another server; the other tool gets the suffix instead.

The tools of a server form the namespace `mcp__<server>` (with `-` written
as `_`), which also answers to the server's name. It carries the configured
`description` and the server's instructions.

### What a tool declares

- Description: the server's description without surrounding whitespace, else
  the tool's title, else the title in its annotations, else
  `MCP tool <tool> from server <server>`.
- Input schema: the server's, with `type: "object"` and `properties: {}`
  added when they are missing. Everything else is passed on unchanged.
- Annotations: `readOnlyHint`, `destructiveHint`, `idempotentHint` and
  `openWorldHint` where the server gives a boolean.
- Exposure: `direct` and `hidden` as configured. `codemode` and `deferred`
  both give a tool that scripts can call and that the `codemode` description
  does not list.
- Result type: every tool declares the result a script receives,
  `{content, structuredContent?, isError?}`. `content` is a list of blocks
  with `type` and, depending on the type, `text`, `data` and `mimeType`;
  `structuredContent` has the tool's own output schema when it declares one.
  The type is written out in the tool's declaration.

### What the model sees

- Text blocks are shown as text. A result without content blocks shows its
  structured content as JSON.
- A resource link is shown as
  `[Resource <uri> "<title or name>" (<mime>, <size>): <description>]`; the
  parenthesis and the description appear only when the server gives them.
- An embedded resource with text is shown as that text. One with binary data
  of a text type (`text/*`, `application/json`, `+json`, `+xml`) is decoded
  as UTF-8. Other binary data is saved to a file in the temp directory, named
  with the extension of the resource's URI or `.bin`, and shown as
  `[Binary resource <uri> (<mime>, <size>) saved to <path>]`, or
  `... could not be saved: <reason>]`.
- Audio is shown as `[audio <mime> omitted]`.
- A result the server marks with `isError` is an error. Without any text it
  reads `MCP tool <server>/<tool> returned an error`.
- A call that fails before there is a result (the server cannot be
  connected, the request times out, is cancelled or is answered with an
  error) is an error with that message.
- A progress notification of a running call is shown as a line of live
  output: its message, else `Progress <p>` or `Progress <p>/<total>`.

Text of more than 20 KB (all text of the result, joined by newlines, in
UTF-8) is replaced by its first and last 10 KB, cut between characters, with
the number of characters left out in between:

```text
Warning: truncated output (original token count: 12345)
Total output lines: 3000

line 1
line 2
<the rest of the first 10 KB>…29012 chars truncated…<the last 10 KB>
line 3000

[Full output: /tmp/pipy-mcp-0123456789abcdef.txt (read it with offset/limit)]
```

The full text is in the named file, which pipy creates readable by its owner
only. The token count is the size in bytes divided by four. When the file
cannot be written the last line is `[Could not save the full output:
<reason>]`. The result's details are `{server, tool}`, plus `fullOutputPath`
when the file was written.

### What a script sees

A script gets the whole result as the server sent it, without `_meta`:
`content` with every block unchanged, `structuredContent` and `isError`. It
is never shortened, and a result with `isError` is returned, not raised. A
call that fails before there is a result raises `ToolError`.

Known gap: pipy tool results carry text only, so an image in a result is
shown to the model as `[image <mime> omitted]`. A script still gets the image
block.

Differences from Pi in tools:

- A tool whose input schema declares a type other than `object` cannot be
  declared by pipy. Pi registers it as it is.
- Arguments are checked against the server's schema but not converted
  (`"3"` stays a string).
- Progress is added to the call's live output line by line; Pi replaces the
  call's preliminary result.
- A resource link without a `uri` or `name` is shown as
  `[unsupported MCP content resource_link]`.
- The message of a failed call is cut to 20 KB like a result; nothing is
  saved for it. Reasons for a failed save are Python's.
- A lone surrogate in text from a server is shown to the model as U+FFFD.
- A name with a suffix that is already another tool's name, or a tool a
  server lists twice, gets a different suffix. In Pi the name changes hands.

## What exists as a library

The package `pipy_harness.native.mcp` is synchronous and uses only the
standard library. It is not part of the SDK surface.

| Module | What it does |
| --- | --- |
| `protocol`, `transport` | JSON-RPC message checks, error types, the protocol versions pipy accepts (`2025-11-25`, `2025-06-18`, `2025-03-26`, `2024-11-05`), conversion of MCP content blocks to text and images, and an in-memory transport pair for tests. |
| `client` | `McpClient`: the `initialize` handshake, requests with timeouts, progress and cancellation, answers to `ping` and `roots/list`, notifications, and paginated `tools/list`, `resources/list`, `resources/templates/list`, `tools/call` and `resources/read`. |
| `stdio` | Starts a server process in its own process group and exchanges newline-delimited JSON with it. It keeps the last 64 KiB of stderr and, on close, closes stdin, then sends SIGTERM and SIGKILL to the group. POSIX only. |
| `sse`, `http` | The streamable HTTP transport: POST per message with JSON or SSE answers, the session id, the server-to-client GET stream, and resumption of a broken response stream with `Last-Event-ID`. |
| `config` | Validation, loading and editing of `mcp.json`, as described above. |
| `config_values` | `${NAME}` and `!command` in config values. |
| `connection` | `McpServerConnection`: one configured stdio server with its state, tools and instructions, as described under [server connections](#server-connections). |
| `log` | The `mcp.log` writer. |
| `tools` | Names, definitions and tool ports for a server's tools, and the conversion of results, as described under [server tools](#server-tools). |
| `manager` | `McpManager`: the servers of one session. It starts the connections, registers their tools as the session's MCP tools after every change, resolves a tool's server when a call runs, waits for chosen servers (`wait_for_servers`), writes the startup report, and closes everything at the end. `reconnect`, `set_enabled` and `set_exposure` change a server while the session runs and save the last two to `mcp.json`. `describe_state` gives a server's short state. |
| `commands` | `McpCommand`: [the `/mcp` command](#the-mcp-command), with `format_status` and `complete_mcp_arguments`. |
| `shell_cli` | `run_mcp_command`: [the `pipy mcp` commands](#the-pipy-mcp-commands). |
| `readiness` | `McpReadiness`: which servers the first prompt and a tool call wait for, as described under [waiting for servers](#waiting-for-servers). |
| `discovery` | `McpDiscovery`: activates `codemode` and `tool_search` for the configured exposures and warns when neither is active, as described under [control tool exposure](#control-tool-exposure). |
| `oauth` | OAuth document types and errors, a bounded HTTP fetch, parsing of `WWW-Authenticate`, and discovery of protected-resource and authorization-server metadata with the issuer check. |

Not built yet: connections to HTTP servers with their retries and session
renewal, images in tool results, resources, the `/mcp` manager,
and everything of OAuth after discovery (client registration, the
authorization and token requests, the callback server, stored credentials,
sign-in).

Where the library differs from Pi's `@earendil-works/pi-mcp`, for those who
build on it:

- The HTTP transport follows a redirect only within the configured server's
  origin, so configured headers and tokens go to no other host. It has no
  proxy support and does not decompress responses.
- The OAuth fetch follows redirects only for requests without a body, drops
  `Authorization`, `Proxy-Authorization` and `Cookie` on a redirect to
  another origin, and limits a response body to 1 MiB.
- A cancelled or timed-out HTTP request closes its connection.
- A line from a stdio server that is longer than the message limit (16 MiB) is
  skipped to its end and reported once.
- Windows process handling is not ported.
