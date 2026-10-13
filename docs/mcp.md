# MCP

pipy connects to the MCP servers of your `mcp.json` when a session starts,
stdio servers and servers reached over streamable HTTP, and makes their tools
and resources available to the model and to codemode scripts.
The work follows the
[MCP parity plan](specs/2026-10-12-mcp-parity-plan.md) and lands in slices;
this page grows with them. [Quick setup](#quick-setup) shows what works today
and [what is missing](#what-is-missing) lists what does not yet. An HTTP
server that wants an OAuth sign-in is signed in to with
[`/mcp login`](#the-mcp-command) in a session or
[`pipy mcp login`](#log-in-and-out) in a shell.

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

A server reached over HTTP is added with `--url`. A key it wants goes into a
header, here read from the environment variable `DOCS_TOKEN` at each connect:

```bash
pipy mcp add docs --url https://example.com/mcp --bearer-token-env-var DOCS_TOKEN
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
files are read when the session starts and again on
[`/reload`](#reloading): run it after changing them.
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
[configuration rules](#configuration-rules). In the terminal,
[the `/mcp` manager](#the-mcp-manager) changes a server's exposure and
enables or disables a server while the session runs, and saves the change
to `mcp.json`.

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

`/reload` reads the settings entry again, so MCP can be turned off and on
in a running session; the flags hold for the whole run.

With MCP off, pipy does not turn on `codemode` or `tool_search` and reports
nothing about `mcp.json`. `mcp` is the only built-in entry; Pi also has
`builtin:codemode`, `builtin:tool-search` and `builtin:llama.cpp`, which in
pipy are selected as tools (`--tools`, `defaultTools`) or do not exist. An
unknown `builtin:` name after `--extension` is an error at startup.

### Waiting for servers

Servers connect while you type, so their tools are usually there when they
are needed. Where they might not be, pipy waits:

- **The first prompt** of a session, and the first one after a `/reload`,
  waits for the servers that have `direct`
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
- **The [resource tools](#use-resources)** wait for every enabled server, so
  a listing covers all of them.
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

### Use resources

When a connected server offers
[resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources),
pipy adds the three resource tools that Codex and OpenCode use:

- `list_mcp_resources` lists resources as JSON: `{server?, resources:
  [{server, uri, name, ...}], nextCursor?}`. With `server` it lists one page
  of that server, and `cursor` (the `nextCursor` of the answer before)
  continues with the next page. Without `server` it lists every page of
  every server, the servers in name order; a server that could not be listed
  is named under `errors` (`[{server, error}]`) and the others are still
  listed. `cursor` without `server` fails with `cursor can only be used when
  a server is specified`.
- `list_mcp_resource_templates` lists URI templates the same way, under
  `resourceTemplates`. A server that does not implement the method has none.
- `read_mcp_resource` reads a resource by `server` and `uri`. Text reaches
  the model as text. Binary content of a text type is decoded; other binary
  content is saved to a file in the temp directory and the model gets the
  path, as for [tool results](#what-the-model-sees). When a read returns
  several contents (a directory, for example) each is preceded by a line
  `<uri>:`. A read without content gives `Resource <uri> is empty.`

The model receives a listing as one line of JSON. Listings and read text are
cut in the middle above 20 KB like [tool results](#what-the-model-sees),
with the full text in a file. A codemode script gets the object instead and
never a shortened one: the listing as above, and `{server, uri, contents}`
from a read, where `contents` are the server's entries (`uri`, `mimeType`,
and `text` or `blob`) without `_meta`.

```python
listed = tools.list_mcp_resources(server="docs")
page = tools.read_mcp_resource(server="docs", uri=listed["resources"][0]["uri"])
text(page["contents"][0]["text"])
```

Each listed item is what the server sent, without `_meta` and `icons`, plus a
`server` field. Resources for MCP Apps (a `ui://` URI, or a MIME type with
`profile=mcp-app`) are left out, because pipy does not render them.

The tools reach every enabled server that declared the `resources`
capability and whose `exposure` is not `hidden`. A name that is none of
these fails with `MCP server "<name>" has no resources. Servers with
resources: <names>`. A server counts from its first connect on, also while
it is disconnected: a listing or a read starts it again. The tools appear
when the first such server has connected. Their exposure is the widest among
those servers: `direct` if one of them is `direct`, else `codemode`, else
`deferred`; a `toolExposure` entry does not count. When they stop being
`direct` they are no longer declared to the model. When no such server is
left (disabled, or set to `hidden`) the tools stay registered but can no
longer be called. They are marked read-only (`read_only_hint`), and a
`tool_call` or `tool_result` handler sees their calls like any other.

For [`--tools` and `--exclude-tools`](usage.md#mcp-tools) the three tools
count as MCP tools, but their names do not start with `mcp__`: a pattern
`mcp__*` does not match them, so name them to declare or to exclude them.

A resource link in a tool result names the tool that reads it, when the
link's server is one the resource tools reach: `[Resource <uri> "<title>".
Read it with read_mcp_resource (server "<server>")]`.

pipy lists a server's resources and templates when it connects and again
when the server sends `notifications/resources/list_changed`, only to count
them: `pipy mcp list` shows `resources: <n>, URI templates: <m>`, and the
state of a connected server in the `/mcp reconnect` completion reads
`connected · 2 tools · 3 resources`. A server whose listing fails still
connects, with a count of 0.

Different from Pi:

- An image resource is shown to the model as `[image <mime> omitted]`,
  because a pipy tool result carries text only. A script gets the `blob`.
- A number in a listed item is printed as Python prints it (`2048.0` stays
  `2048.0`).
- An HTTP server's resource request that failed with a transient status is
  [made once more](#http-servers) after 250 ms, as in Pi, but a call that is
  cancelled meanwhile ends at once.

### Reloading

Run `/reload` after you added, removed or changed a server outside the
session. It closes every connection, reads the `mcp.json` files and the
[`builtin:mcp` setting](#turning-mcp-off) again and starts the servers they
name now, as at the start of a session:

- A server that is no longer configured is closed and its tools are gone. A
  new server connects. Every other server is started again too, with its
  entry as it is now; a stdio server gets a new process.
- `/reload` returns when the old connections are closed. The servers connect
  in the background, the [startup report](#startup-report) is made again, and
  the next prompt [waits](#waiting-for-servers) for the servers with `direct`
  tools like a first prompt.
- A tool that `tool_search` loaded, or that was declared for another
  reason, is declared again when its server has connected before the next
  prompt starts. A tool of a server that connects later is not: it is
  registered like a new tool, so a `direct` tool is declared anyway and a
  `codemode` or `deferred` tool has to be loaded again. The same holds after
  `/tree` to an entry that declares a tool of a server that is still
  connecting.
- When `/reload` keeps the previous extensions because the new ones were
  refused (`pipy: keeping the previous extensions.`), it also keeps the
  servers as they are and does not use the `mcp.json` it read.

Different from Pi:

- Pi closes the servers and reads `mcp.json` again for every new session in
  one process (`/new`, `/resume`, `/fork`) as well. pipy keeps the servers
  there; only `/reload` and a restart read the files again. The first-prompt
  wait and the startup report are not repeated for such a session.
- Pi's reload is not undone when an extension fails to load, so its servers
  restart in every case.
- While `/reload` decides whether the new extensions are accepted, a change
  of a running server's tools waits; it is registered afterwards, or dropped
  when the servers are replaced.

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
correcting `mcp.json`, run `/reload`.

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

In the terminal, `/mcp` opens [the manager](#the-mcp-manager), where you see
every server and sign in, reconnect, enable or disable one, look at its
tools and change its exposure.

Where pipy has no terminal UI, `/mcp` prints one line per configured server,
after the servers that started with the session have connected or failed:

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

`/mcp login [server]` signs in to a server that
[uses OAuth](#sign-in-with-oauth). It opens the manager's
[sign-in screen](#signing-in-from-the-manager), which shows the
authorization URL, opens the URL in your browser and waits until the browser
came back or you pasted the redirect URL. Then it connects the server,
closes the screen and answers `Signed in to MCP server "<name>" (<n>
tools).`; the server's tools are available from the next prompt on. When a
stored refresh token is enough, no browser is opened. Escape cancels the
sign-in at any moment (`Sign-in cancelled.`), as does the end of the
session. A failure is reported as `Sign-in failed: <error>`, and a connect
that fails after the sign-in as `Signed in, but <error>`. Login needs the
terminal UI; in a session without one it answers `Signing in to MCP server
"<name>" requires interactive mode.`

`/mcp logout [server]` deletes the server's stored credentials and closes
its connection: `Signed out of MCP server "<name>".`, or `No stored
credentials for MCP server "<name>".` The server then needs a sign-in.

Without a name, login and logout take the only enabled server that uses
OAuth; with several, the only one that needs a sign-in; otherwise they ask.
When no enabled server uses OAuth they answer `No enabled MCP server uses
OAuth. Only HTTP servers without an Authorization header do.` Anything else
prints
`Usage: /mcp, /mcp login [server], /mcp logout [server], /mcp reconnect
[server]`.

In the terminal, `/mcp ` completes the three actions and then the servers
the action can name, with their state: after `reconnect` the enabled servers,
after `login` and `logout` those that use OAuth.

The three subcommands, and `/mcp` where it prints the status, wait for the
servers that are still connecting from the start of the session; the manager
does not. Escape ends that wait: `/mcp` then prints the servers as they
are, and a subcommand is not run (`MCP servers are still connecting; /mcp
reconnect was not run.`). Escape during a reconnect or a logout stops the
waiting only; it goes on, and `/mcp` shows how it ended. Escape during a
login cancels the sign-in.

Different from Pi:

- pipy runs `/mcp` only for a line typed into a session. A prompt given on
  the command line (also with `--print` or `--mode json`) or sent by an RPC
  client is never run as a slash command; it goes to the model. Pi runs the
  command there too.
- A prompt template or custom command named `mcp` takes the place of the
  command, as does an extension command of that name. In Pi only an
  extension command does.
- Pi's command cannot be interrupted; the Escape rules above are pipy's.
- With MCP [turned off](#turning-mcp-off) there is no `/mcp` command.

### The `/mcp` manager

`/mcp` in the terminal opens at once, also while servers are still
connecting, and stands in place of the editor until you close it. It lists
every configured server, disabled ones too:

```text
 MCP servers

→ issues                          needs sign-in · direct · global
  search                          failed: [Errno 2] No such file or directory: 'no-such-command' · codemode · project
  docs                            connected · 2 tools · codemode · global, project override
  old                             disabled · codemode · global

 enter manage • escape/ctrl+c close
```

Each row is the server's state, its exposure and where it is configured:
`global` (the `mcp.json` in the config home), `project`, or `global, project
override` for a user-level server that the project's `mcp.json` overrides.
Servers that need you come first: those that need a sign-in, then failed,
disconnected, starting or connecting, connected and disabled ones, each
group by name. The errors of the `mcp.json` files are shown above the list
as `config: <error>`. Without any server the manager says where to add
them. The list follows the servers while it is open: a server that connects
or fails changes its row, and the selected server stays selected.

Up and Down move, Enter opens the selected server, and Escape or Ctrl+C
closes the manager; in a server's menu they go back to the list. Every
screen names both keys with what they do.

A server's menu shows its URL or command line, the file that configures it
(and the project file that overrides it), its state, what the last action
said if it failed, and the whole error of a server that is not connected,
with the end of a stdio server's stderr. Below are the actions its state
allows:

| Action | Offered for | What it does |
| --- | --- | --- |
| `Sign in` | a server that needs a sign-in | Opens the [sign-in screen](#signing-in-from-the-manager). |
| `Tools` | a connected server | Lists its tools with the first line of their description, and `[<exposure>]` in front of a tool whose `toolExposure` differs from the server's exposure. Above the list is what the server's exposure means. |
| `Reconnect` | a connected, failed or disconnected server and one that needs a sign-in | Closes the connection and connects again. |
| `Sign out` | a connected server that uses OAuth | Deletes its stored credentials; the server then needs a sign-in. |
| `Exposure` | every enabled server | Offers `codemode`, `deferred` and `direct` with what they mean, the current one marked. Enter saves the choice, and the server's tools are registered again with it at once. |
| `Disable`, `Enable` | an enabled, a disabled server | Saves `enabled` and closes or opens the connection. |
| `Disable in this project`, `Enable in this project` | a user-level server without a project override, in a trusted project | Saves the change as an override in the project's `.pipy/mcp.json`. From then on the server's changes go to that override. |

Each of the last three rows says where the change is saved: `saved to the
global mcp.json`, `saved to the project mcp.json`. A change that cannot be
saved is not made, and the menu shows why (`Could not update <file>:
<reason>`).

Enabling, disabling and reconnecting do not hold the manager: the menu comes
back at once and its `State:` line follows the server while it connects or
closes. You can go back to the list, open another server or close the
manager meanwhile. A server that finishes connecting after the manager was
closed gets `codemode` or `tool_search` activated, when its exposure needs
one, before the next prompt runs.

A screen that is taller than the terminal keeps its controls on the screen:
a long error or URL is cut short, with a line such as `… 16 more lines`, and
a long list shows fewer servers at a time. [`pipy mcp list`](#list) in a
shell prints a server's whole error.

#### Signing in from the manager

`Sign in`, and `/mcp login` in the terminal, show the sign-in screen. First
it says `Contacting the authorization server…`. Then it shows the
authorization URL, which pipy also opens in your browser, and an input:

```text
 Sign in to issues

 Approve access in your browser. If it did not open, visit:
 https://auth.example.com/authorize?response_type=code&client_id=…
 ctrl+x to copy

 If the browser runs on another machine, paste the URL it was redirected to:
>

 enter submit • escape/ctrl+c cancel
```

When the browser comes back to this machine the sign-in goes on by itself.
When it cannot reach this machine, over SSH for example, copy the URL from
the address bar of the page the browser ended on (it starts with
`http://127.0.0.1:`), paste it into the input and press Enter; an empty
input is not submitted. Ctrl+X copies the authorization URL to the
clipboard, for a URL too long to select in the terminal. While the server
is connected again the screen says `Connecting…`. Escape or Ctrl+C cancels
the sign-in at each of these steps and closes the screen at once.
`Connecting…` covers two things: the exchange of the browser's answer for
tokens, and then the new connection to the server. Cancelling during the
exchange stores nothing. Once the tokens are stored, cancelling only stops
the waiting: the server still connects, and its state shows how that ended.
In the manager the outcome is shown in
the server's menu (`Sign-in cancelled.`, `Sign-in failed: <error>`, or the
connected state).

Different from Pi in the manager:

- The authorization URL is plain text, not a terminal hyperlink, so there
  is no "click to open" hint. Most terminals still let you click a URL that
  fits on one line; use Ctrl+X for one that wraps.
- Ctrl+X is fixed. In Pi it is the key bound to `app.message.copy`, an
  action pipy does not have.
- The title of a screen is not bold, and the note after a copy has no colour
  of its own.
- The pasted URL is edited with the keys of pipy's selector input:
  Backspace, Left and Right, Home and End, Ctrl+U and Ctrl+K.
- Pi's screen scrolls with the terminal when it is taller than the window.
  pipy cuts the text above the controls short instead, as described above.
- In Pi the cancel key does nothing while the server is connected again
  after the tokens were stored; in pipy it ends the waiting.
- A `Sign out` that cannot delete the credentials (the credentials file
  cannot be read, or a token refresh holds the server's lock for 25 seconds)
  is shown in the server's menu as `Could not sign out of MCP server
  "<name>": <reason>`.
- The list has no `overridden:` lines, because pipy has no servers
  registered by extensions yet.

### What is missing

Not built yet, in the order the plan builds it: servers registered by
extensions, and images in tool results and resources.

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
tool, and `/tree` to a later entry declares it again. After a `/reload` a
loaded MCP tool is declared again once its server has connected, see
[Reloading](#reloading). A session that is resumed in a new pipy process
starts from the configured tools, as in Pi's CLI.

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
  `clientMetadataBaseUrl`, an `https` URL without query or fragment; a
  `callbackUrl` set with it must be on `localhost` or `127.0.0.1` with path
  `/callback`. What the settings do is described under
  [OAuth settings](#oauth-settings).
- `auth` needs a non-empty `provider` and a server URL that is `https`, or
  `http` on a loopback host. What it does is described under
  [a provider's token](#a-providers-token).
- An invalid entry is reported as `<path>: <reason>` and skipped. The other
  entries, and the other file, still load. A file that is not JSON, or whose
  top level or `mcpServers` is not an object, is reported as a whole.

The library can also change a file: set one server's `enabled` or `exposure`,
add a server, or remove one. An edit keeps all other content and the file's
indentation. [`pipy mcp add` and `pipy mcp remove`](#the-pipy-mcp-commands)
use the last two, and [the `/mcp` manager](#the-mcp-manager) sets `enabled`
and `exposure`.

Differences from Pi in the configuration:

- pipy checks URLs with a small strict parser and does not repair them. A URL
  without `//`, with a backslash, or with a shortened IPv4 address such as
  `127.1` is a configuration error here, where Pi's URL parser accepts it.
- `oauth.clientMetadataBaseUrl` exists only in pipy. Pi serves its Client ID
  Metadata Documents from its own site; pipy has no such document, so `cimd`
  needs the address where you host one, see
  [OAuth settings](#oauth-settings).
- A file that is not valid UTF-8 is an error, and JSON error messages are
  Python's.
- A whole-number `oauth.callbackPort` written as `8080.0` is accepted.

## The `pipy mcp` commands

```bash
pipy mcp add <server> [options] -- <command> [args...]
pipy mcp add <server> [options] --url <url>
pipy mcp remove <server> [-l]
pipy mcp list [--json]
pipy mcp login <server> [--timeout <seconds>]
pipy mcp logout <server>
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
server without an `Authorization` header the last line also names
[`pipy mcp login <server>`](#log-in-and-out), for the case that the server
wants a sign-in.

`remove` fails when the file does not have the entry and says where the
server is defined instead: `It is defined in <path>; use --local.` or `...;
omit --local.`

### List

`pipy mcp list` connects to every enabled server, all at the same time, reads
its tools and closes the connection again. For each server it prints the
state, the exposure and the scope, then the command or URL, and after that
what applies: the project file that overrides it, its tools, the number of
its resources and URI templates, and the error.

```text
filesystem: connected, 2 tools (direct, global)
  npx -y @modelcontextprotocol/server-filesystem .
  tools: read_file, write_file [hidden]
  resources: 12, URI templates: 1
broken: failed (codemode, global)
  no-such-server
  [Errno 2] No such file or directory: 'no-such-server'
parked: disabled (codemode, project)
  node server.js
config error: /home/me/.pipy/mcp.json: server "bad" needs either "command" (stdio) or "url" (streamable HTTP)
```

A tool is followed by its exposure in brackets when `toolExposure` gives it
another one than the server's. The `resources:` line appears for a server
that declares the `resources` capability and counts what it lists, without
resources for MCP Apps (a `ui://` URI, or a MIME type with
`profile=mcp-app`). The exit status is 1 when an entry of a file
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
      },
      "resources": 12,
      "resourceTemplates": 1
    }
  ],
  "errors": []
}
```

`override` (the project file that overrides the server), `toolExposure`,
`resources` with `resourceTemplates` (a server with the `resources`
capability) and `error` are present only when they apply; `note` is the line about an ignored
project file.

The project file is read only when a decision to trust the project is
already saved; the commands never ask, and the `defaultProjectTrust` setting
and `--approve` do not count. A project file that exists but is not read is
reported: `<path> is ignored because the project is not trusted. Start pipy
in the project to trust it.`

### Log in and out

`pipy mcp login <server>` signs in to a server that
[uses OAuth](#sign-in-with-oauth). It connects once to find out whether a
sign-in is needed:

- A server that connects needs none: `Already signed in to MCP server
  "<name>" (<n> tools).`
- A server that fails for another reason prints `MCP server "<name>" failed
  to connect: <error>` and status 1.
- Otherwise it prints `Sign in to MCP server "<name>" in your browser:` with
  the authorization URL on the next line, opens that URL in your browser and
  waits. When you approved, the browser is sent to a port on this machine,
  where pipy answers `Signed in to the MCP server. You may now close this
  page.` The command then connects with the new token and prints `Signed in
  to MCP server "<name>" (<n> tools).` When that connect fails it prints
  `Signed in, but <error>` and status 1; the credentials are stored anyway.

When the browser runs on another machine (over SSH, for example) it cannot
reach that port and shows an error page for an address that starts with
`http://127.0.0.1:`. In a terminal the command asks for that address: `If the
browser cannot reach this machine, paste the URL it was redirected to:`.
Paste the whole address from the browser's address bar. An empty line
cancels the sign-in. A pasted text is refused with a reason when it is not a
full URL, is not the address this sign-in redirects to, belongs to another
sign-in or carries no code. When standard input is no terminal, as when an
agent runs the command, only the browser can finish the sign-in.

`--timeout <seconds>` limits the whole sign-in, from the first request to
the authorization server to the stored token; the default is 300 and the
value must be a positive number. When the time is up, or you cancelled, the
command prints `Sign-in to MCP server "<name>" was cancelled or not completed
within <n> seconds.` and ends with status 1. Any other failure prints
`Sign-in to MCP server "<name>" failed: <error>`.

`pipy mcp logout <server>` deletes the stored credentials of the server and
prints `Signed out of MCP server "<name>".`, or `No stored credentials for
MCP server "<name>".` It does not tell the authorization server.

Both commands fail for a name that is in no file they read (`No MCP server
named "<name>". Configured: <names>.`, with the note about an ignored project
file when there is one) and for a server that does not use OAuth (`MCP server
"<name>" does not use OAuth. Only HTTP servers without an Authorization header
do.`).

A session that is running while you sign in, or while the agent runs the
command for you, notices it: when its next turn starts it connects the
server again, see [sign-in with OAuth](#sign-in-with-oauth).

### Differences from Pi

- `--oauth-callback-port` and `--timeout` take a decimal number. Pi also
  reads JavaScript's other spellings of a number, such as `0x50`.
- `logout` reports a credentials file it cannot read as `Could not update
  <path>: <reason>` with status 1.
- When the question for the redirect URL ends without an answer, pipy ends
  its line, so the next output starts on a new one.
- The usage text and the hint are plain text; Pi prints parts of them bold
  or dim.
- A trust store that cannot be read ends `list`, and `add` to a project file
  before it writes, with the store's error and status 1.
- Error texts of the operating system and of the JSON parser are Python's.

## Server connections

What pipy does with a configured server. An entry with a `command` is a
server process (stdio); an entry with a `url` is a server reached over
streamable HTTP, see [HTTP servers](#http-servers).

### Values resolved at connect time

- `timeout` is the time a request to the server may take, 60 seconds when not
  set. A progress notification for a request starts its time again.
- Each `env` value of a stdio server and each `headers` value of an HTTP
  server may refer to environment variables with `${NAME}` or
  `$NAME`; `$$` is a literal `$` and `$!` a literal `!`. A value that starts
  with `!` is a shell command, and its trimmed standard output is the value.
  The command runs on every connect, with a limit of 10 seconds. A variable
  that is unset or empty, or a command that fails or prints nothing, fails
  the connect with a message naming the server and the key or the header,
  for example `Failed to resolve MCP server "docs" env "API_KEY" from
  environment variable: DOCS_TOKEN` or `Failed to resolve MCP server "docs"
  header "Authorization" from environment variable: DOCS_TOKEN`. The server
  process gets pipy's environment plus the resolved `env`.
- A leading `~` or `~/` in `command`, in each of `args` and in `cwd` is the
  home directory. `--root=~/x` is passed as written.
- A relative `cwd` is relative to the session directory, and the session
  directory is the default.

`oauth.clientSecret` follows the same rules. It is resolved when a token is
refreshed, not when the server connects, so a secret that cannot be resolved
fails only the request that needed the refresh, with `Failed to resolve MCP
server "<name>" oauth.clientSecret from ...`.

### HTTP servers

A server with a `url` is reached over
[streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#streamable-http).
Every request carries the configured `headers`. A server without an
`Authorization` header is also sent the OAuth token stored for it, see
[sign-in with OAuth](#sign-in-with-oauth), or with `auth` the token of a
provider, see [a provider's token](#a-providers-token).

- A connect that fails because the server cannot be reached, or because it
  answers with status 408, 429 or 5xx other than 501, is made again after
  250 ms and, if that fails too, once more after one second. The server
  stays `connecting` meanwhile and is `failed` with the last error after the
  third attempt. Any other failure is final at once.
- A resource read or listing the server answers with one of these statuses is
  made once more after 250 ms. A tool call is not repeated for that reason,
  because the server may have run it; the call fails with the status, for
  example `MCP HTTP request failed with status 502`.
- When the server no longer knows pipy's session, typically because it
  restarted, it answers a request with status 404. The server did not run
  that request, so pipy connects again, which starts a new session and lists
  the tools again, and makes the request once more. This holds for tool
  calls too. Requests that were in flight on the old session get the same
  answer and move to the new session the same way. A request that finds the
  new session expired as well fails with `MCP session expired`.
- No request is made more than twice.
- Closing a server, or ending the session, ends the wait before another
  attempt and, when the server gave the session an id, tells it that the
  session is over (a `DELETE`, given one second).

Different from Pi:

- What counts as "cannot be reached" is any network failure Python reports
  (refused or reset connections, name lookup, TLS, a response that is not
  HTTP). Pi retries what `fetch` reports as a `TypeError`.
- Once the last request on a session the server forgot has ended, pipy
  closes that session's connection. Pi leaves it open.
- The wait before a resource request is made again ends when the call is
  cancelled or the server is closed. Pi waits the 250 ms out.
- pipy sends configured headers and tokens only to the server's own origin,
  so a redirect to another host is a failed request.

### Sign-in with OAuth

An HTTP server signs in with OAuth unless its entry has an `Authorization`
header (in any letter case) or `auth`. You sign in with
[`/mcp login <server>`](#the-mcp-command) in a session or with
[`pipy mcp login <server>`](#log-in-and-out) in a shell. Both open a browser
and store the credentials in [the credentials file](#the-credentials-file).
Nothing else opens a browser; connections use what is stored:

- Requests to such a server carry the access token stored for it in
  `mcp-auth.json` in the config home, and none when nothing is stored. The
  configured headers are sent as usual.
- A token within 30 seconds of its expiry is refreshed before a request, and
  a token the server rejects with status 401 is refreshed and the request
  made once more. Several sessions and `pipy mcp list` runs that use one
  server at the same time refresh its token once.
- When that is not possible, the server is in the state `needs-auth`: when
  nothing is stored, when no refresh token is stored or the authorization
  server refuses it or does not answer within 15 seconds, when the server
  asks for more scope than was granted (status 403 with
  `insufficient_scope`), and when it answers 401 again after a refresh.
  The connect or the call fails with `MCP server "<name>" requires sign-in.
  Run /mcp to sign in.`, the [startup report](#startup-report) and `/mcp`
  list the server as `needs sign-in`, and `pipy mcp list` adds `sign in
  with: pipy mcp login <name>`. The next call to the server connects again
  and finds the credentials stored by then.
- A session notices a sign-in made outside it. For every server that needs
  a sign-in it remembers what was stored when the need arose, and at the
  start of each turn it compares that with the credentials file. A server
  whose stored tokens changed is connected again before the turn's request
  is built, so the turn has its tools. That is how `pipy mcp login <name>`
  works when the agent runs it through `bash`: the model's next turn can
  use the server. Escape while such a server connects cancels the run; the
  server goes on connecting.
- A server with an `Authorization` header that answers 401 is a failed
  server with the error `MCP server requires authentication`, as is any
  server without `auth` asked by code that was given no credential store.
  A server with `auth` never signs in with OAuth; see
  [a provider's token](#a-providers-token).

What a sign-in does:

- It listens on `127.0.0.1` for the browser: on `oauth.callbackPort` when
  that is set, else on the port the server's stored client was registered
  with, else on a free port. A port that is taken is replaced by a free one,
  unless it is the configured one; then the sign-in fails.
- A client that was registered for another redirect address than the one
  now in use is dropped together with its tokens, and a new one is
  registered under the name `pipy` (or `oauth.clientName`). A client
  configured with `oauth.clientId` is never dropped.
- A stored refresh token is tried first, so signing in again often needs no
  browser. It is not tried when the server asked for more scope.
- The scope asked for is `oauth.scope` together with the scope the server
  named when it rejected a request. After a rejection for too little scope
  (`insufficient_scope`) the scopes granted so far are asked for as well, so
  the new token can do what the old one could.
- An authorization response from another issuer than the server's
  authorization server is refused.
- Each request to the authorization server is limited to 15 seconds.

### OAuth settings

The `oauth` object of an HTTP server entry changes how pipy signs in. All
of it is optional; without it pipy registers itself with the server's
authorization server and listens on a free port.

```json
{
  "mcpServers": {
    "issues": {
      "url": "https://issues.example.com/mcp",
      "oauth": {
        "clientId": "pipy-desktop",
        "callbackPort": 8123,
        "scope": "issues:read issues:write"
      }
    }
  }
}
```

- `clientId` names a client you registered with the authorization server
  yourself, for a server that does not let clients register. pipy then
  never registers and stores no client. `clientSecret` is that client's
  secret, [resolved](#values-resolved-at-connect-time) each time a token
  is requested.
- `callbackPort` is the port the sign-in listens on. The redirect address
  is then `http://127.0.0.1:<port>/callback`, which is what you register
  with a client of your own.
- `callbackUrl` is the redirect address, for a client that was registered
  with another host name or path, for example
  `http://localhost:8123/oauth/done`. With a port it is sent to the
  authorization server exactly as you wrote it. Without one, the port pipy
  listens on is added: `callbackPort`, or a free port. `localhost` is
  served on `127.0.0.1`.
- `scope` lists the scopes to ask for, separated by spaces.
- `clientName` is the name pipy registers under, instead of `pipy`. It is
  sent only when a client is registered. A server you already signed in to
  keeps its registered client, so after changing the name run `pipy mcp
  logout <server>` and sign in again.
- `authServerMetadataUrl` is the address of the authorization server's
  metadata document, for a server whose own discovery does not lead to it.
  The document is used as it is: its `issuer` is not compared with what
  discovery would expect. Nothing about it is stored with the credentials,
  so a changed address needs no new sign-in.
- `clientRegistration` is `dcr` (the default: dynamic client registration)
  or `cimd`, see below.

With `"clientRegistration": "cimd"` pipy registers no client. It identifies
with a Client ID Metadata Document: its client ID is the `https` address of
a JSON document that describes the client, which the authorization server
fetches. pipy has no document of its own, so you host the documents and
name their directory in `clientMetadataBaseUrl`:

```json
"oauth": {
  "clientRegistration": "cimd",
  "clientMetadataBaseUrl": "https://id.example.com/oauth"
}
```

- The authorization server must say in its metadata that it accepts such
  documents (`client_id_metadata_document_supported`) from clients without
  a secret (`none` among `token_endpoint_auth_methods_supported`).
  Otherwise the sign-in fails with `The authorization server does not
  support Client ID Metadata Documents for public clients; remove
  oauth.clientRegistration "cimd"`, and no client is registered instead.
- For an authorization server that names itself in its responses
  (`authorization_response_iss_parameter_supported`), the client ID is
  `<base>/client.json` and the redirect address is
  `http://127.0.0.1:<port>/callback`.
- For any other, the client ID is `<base>/<id>/client.json` and the
  redirect address ends in `/callback/<id>`. `<id>` identifies the MCP
  server: 12 characters, the URL-safe base64 of the first 9 bytes of the
  SHA-256 of the server's URL without its fragment. The response is
  accepted only at that address, so one from another authorization server
  cannot be taken for it. The URL is hashed in the form pipy reads it in
  (host in lower case, no default port, `/` for an empty path), so compute
  the `<id>` with pipy's own function, using the Python pipy is installed
  in (`uv run python` in a checkout):

  ```sh
  python -c 'import sys; from pipy_harness.native.mcp.credentials import callback_id; print(callback_id(sys.argv[1]))' https://issues.example.com/mcp
  ```

- Each document has to list the redirect address its sign-ins use, with
  the port left to the authorization server's rules for loopback addresses
  or fixed with `callbackPort`. A `callbackUrl` set with `cimd` keeps the
  path `/callback`; it can change the host name to `localhost` and name the
  port.
- The document is not stored with the credentials. A client that was
  registered before you set `cimd` is dropped with its tokens at the next
  sign-in.

Differences from Pi: Pi identifies with documents on its own site and has
no `clientMetadataBaseUrl`. The `<id>` is computed over the URL as pipy's
URL parser reads it, which for a URL with unusual characters may not be the
text Pi's parser produces.

### A provider's token

Some MCP servers accept the token of a model provider you are logged in to.
For such a server, name the provider in the user-level `mcp.json`:

```json
{
  "mcpServers": {
    "radius": {
      "url": "https://mcp.radius.example/mcp",
      "auth": { "provider": "radius" }
    }
  }
}
```

- Every request to the server carries `Authorization: Bearer <token>` with
  the token the provider has at that moment. pipy asks for it before each
  request and keeps no copy, so a login, a logout or a refreshed token
  applies to the next request. Nothing is written to `mcp-auth.json`.
- The token is what is stored for the provider in
  [the auth store](providers.md#credentials-and-auth-sources), `auth.json`,
  where `/login <provider>` puts a login: an API key entry (the name of a set
  environment variable is that variable's value, `!command` is the output
  of the command, which then runs for every request), or the access token
  of a stored OAuth login. A `github-copilot` or `openai` login is
  refreshed first when it expires within five minutes, as before a model
  request (not when an extension registered a provider of that name). When nothing is stored for the provider, its API-key environment
  variable is used. A provider without any of these has no token, and the
  request carries none. The same holds when `auth.json` exists and cannot
  be read: the environment variable is not used in its place.
- The server never signs in with OAuth: `/mcp login` and `pipy mcp login`
  answer that it does not use OAuth, and a 401 is not answered with a
  refresh.
- When the server answers 401, it is in the state `needs-auth`, and the
  connect or the call fails with `MCP server "<name>" requires sign-in. Run
  /login <provider> to sign in.` The startup report and `/mcp` list it as
  `needs sign-in`. After `/login <provider>`, the next call to the server
  connects it again; `/mcp reconnect <name>` does so at once.
- `auth` is refused in a project file, and needs an `https` URL or a
  loopback host, so a project cannot send your token anywhere
  ([configuration rules](#configuration-rules)).

Differences from Pi:

- Pi answers with whatever its model registry resolves for the provider.
  pipy reads `auth.json` and the environment only: a key that `models.json`
  configures for the provider, `--api-key`, the `openai-codex` login (kept
  in its own file) and ambient cloud credentials are no token for an MCP
  server.
- An OAuth login that pipy does not refresh before its own model requests
  (a provider an extension registered, for example) is sent as stored.
- `pipy mcp list` knows no provider logins, as `pi mcp list` does not: it
  sends such a server no token, reports `needs sign-in` when the server
  wants one, and then prints `sign in with: pipy mcp login <name>`, which
  does not apply to this server. Log in to the provider in a session.

### States

A server is `connecting`, `connected`, `disconnected`, `needs-auth`,
`failed` or `closed`. `needs-auth` is described under
[sign-in with OAuth](#sign-in-with-oauth) and
[a provider's token](#a-providers-token).

- The first call connects. Calls that arrive while a connect is in progress
  wait for that one attempt. A stdio connect is not retried; an
  [HTTP connect](#http-servers) is, after some failures.
- The tools are listed only when the server declares the `tools` capability.
  When the server sends `notifications/tools/list_changed` they are listed
  again; if that fails, the error is `Failed to refresh tools: <reason>` and
  the earlier tools stay.
- The resources and URI templates are listed, next to the tools, only when
  the server declares the `resources` capability, and again when it sends
  `notifications/resources/list_changed`. They are only counted: by
  `pipy mcp list`, and in the state of a connected server that the
  `/mcp reconnect` completion shows (`connected · 2 tools · 3 resources`). A
  listing that fails counts as empty and is no error of the server. What is
  known of a server's resources is kept while it is disconnected. The
  [resource tools](#use-resources) ask the server again at every call.
- The server's `instructions` are kept with surrounding whitespace removed.
- A connect that fails leaves the server `failed` with the reason, followed
  by the last 2000 characters of the server's stderr, and the call fails with
  `MCP server "<name>" failed to connect: <reason>`. The next call tries
  again.
- When the server exits or the connection drops, the server is
  `disconnected` with the error `Connection closed` and the end of its
  stderr. The next call starts it again. An HTTP server has no connection
  that stays open, so it is never `disconnected`: a server that went away
  shows in the next request.
- Closing a server stops a connect in progress and returns when the server
  process is gone, or the HTTP session ended. Calls then fail with `MCP server "<name>" is shut down`.
  From the moment a server is closed no token refresh starts for it, and
  closing waits for one that is running, so that its new tokens are saved.
  The same holds for asking for [a provider's token](#a-providers-token).

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
  `[Resource <uri> "<title or name>" (<mime>, <size>): <description>. Read
  it with read_mcp_resource (server "<server>")]`; the parenthesis and the
  description appear only when the server gives them, and the last sentence
  only when the [resource tools](#use-resources) reach the server.
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

### How a call is shown

In the terminal a call to a server tool is a row titled `<server>/<tool>`,
with the names as the server and its configuration give them
(`my-server/get.item/v2`, not the pipy name). A character of a name that
is not printable, such as a control character, is shown as a space.

- Collapsed, the arguments follow the title as `key=value` pairs with JSON
  values, cut to 100 characters. Expanded with Ctrl+O, each argument is a
  `key: value` line below the title; a string is shown as it is, anything
  else as indented JSON.
- A collapsed result shows its first 5 lines, counted after wrapping to the
  width of the row, because a result is often one long line of JSON. Below
  them is `... (<n> more lines, ctrl+o to expand)`, and
  `Full output: <path>` when the text was shortened and saved to a file.
  Expanded, the row shows the whole text. A result without text adds
  nothing to the row.
- An error is shown in the error colour.
- While a call runs, the row shows the last progress line.

A call whose tool is not registered is shown the same way: in a resumed
session before the server connected, when it never connects, and for a tool
the server no longer has. Its title then comes from the pipy name, split at
the first `__` after `mcp__`: `mcp__my_server__get_item_v2` is shown as
`my_server/get_item_v2`. The resource tools have rows like any other tool
without a renderer of its own.

A script's calls to server tools are lines inside the `codemode` row, like
its calls to built-in tools, under the tool's pipy name.

Without the terminal UI, the call's line is the same title with the
collapsed arguments; the result is printed like any other tool's.

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
  call's preliminary result. The call's row shows the last line, so a
  progress message of several lines shows only its last one.
- The row of a call is drawn by pipy itself, and a call to
  `mcp__<server>__<tool>` gets it also with MCP turned off. In Pi the MCP
  extension's renderers do it, so with `--no-mcp` such a call in a resumed
  session has the generic row. An extension can replace the row with
  `api.register_tool_renderer` (see
  [tool rendering](extension-api.md#tool-rendering)), but cannot call and
  wrap pipy's row as it can wrap Pi's renderers.
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
| `sse`, `http` | The streamable HTTP transport: POST per message with JSON or SSE answers, the session id, the server-to-client GET stream, and resumption of a broken response stream with `Last-Event-ID`. A transport that is given an auth provider sends its token, and hands it a request the server rejected, as described under [the credentials file](#the-credentials-file). |
| `config` | Validation, loading and editing of `mcp.json`, as described above. |
| `config_values` | `${NAME}` and `!command` in config values. |
| `connection` | `McpServerConnection`: one configured server with its state, tools, resources and instructions, as described under [server connections](#server-connections), its `provider_token` function for a server with [`auth`](#a-providers-token), and the resource requests (`read_resource`, `resources_page`, `all_resources` and the two for templates). |
| `log` | The `mcp.log` writer. |
| `tools` | Names, definitions and tool ports for a server's tools, and the conversion of results, as described under [server tools](#server-tools). |
| `resources` | `create_mcp_resource_tools`: the three [resource tools](#use-resources) over the servers a function returns at call time; `is_mcp_app_resource`. |
| `manager` | `McpManager`: the servers of one session. It starts the connections, registers their tools and the resource tools as the session's MCP tools after every change, resolves a tool's server when a call runs, waits for chosen servers (`wait_for_servers`), writes the startup report, and closes everything at the end. `reconnect`, `set_enabled` and `set_exposure` change a server while the session runs and save the last two to `mcp.json`. `sign_in` signs in to a server and connects it again, `sign_out` deletes its credentials, and `take_signed_in` with `reconnect_servers` picks up a sign-in made elsewhere. `close` cancels running sign-ins. `describe_state` gives a server's short state. |
| `commands` | `McpCommand`: [the `/mcp` command](#the-mcp-command), with `format_status` and `complete_mcp_arguments`. |
| `manager_menus`, `manager_flow` | [The `/mcp` manager](#the-mcp-manager) without its drawing: the menus as values built from the servers' status (`servers_menu`, `server_menu`, `tools_menu`, `exposure_menu`), and `McpManagerFlow`, which decides what a choice does and shows the screens through an `McpUi` object. `McpBackgroundActions` runs the changes the manager does not wait for. The terminal view is `pipy_harness.native.ui.components.mcp_manager_view`. |
| `shell_cli` | `run_mcp_command`: [the `pipy mcp` commands](#the-pipy-mcp-commands). |
| `readiness` | `McpReadiness`: which servers the first prompt and a tool call wait for, as described under [waiting for servers](#waiting-for-servers). |
| `discovery` | `McpDiscovery`: activates `codemode` and `tool_search` for the configured exposures and warns when neither is active, as described under [control tool exposure](#control-tool-exposure). |
| `oauth` | The parts of an OAuth sign-in, described under [the OAuth library](#the-oauth-library). A connection uses the flow to refresh tokens, and the sign-in runs it. |
| `credentials` | `McpOAuthCredentialStore`: the file that keeps the OAuth state of servers, and `McpServerAuthProvider`: the auth provider that sends a server's stored token and refreshes it. Both are described under [the credentials file](#the-credentials-file). Sessions and the `pipy mcp` commands create them for the config home. |
| `sign_in` | `sign_in_mcp_server`: [the sign-in](#sign-in-with-oauth) to one server, with the loopback callback server, a prompt object that shows the authorization URL and asks for a pasted redirect URL, and a cancel event. `/mcp login` (through the manager) and `pipy mcp login` run it. |

Not built yet: images in tool results and resources.

### The OAuth library

`pipy_harness.native.mcp.oauth` holds the parts of a sign-in to an HTTP MCP
server. A connection uses the flow to refresh a stored token
([sign-in with OAuth](#sign-in-with-oauth)), and `/mcp login` and
[`pipy mcp login`](#log-in-and-out) sign in with it. A server with `auth`
uses none of it ([a provider's token](#a-providers-token)). This section
describes what the modules do for those who read or build on the code.

| Module | What it does |
| --- | --- |
| `types`, `errors` | The OAuth documents (server metadata, client information, tokens) with their checks, and the error types. |
| `fetch` | The HTTP client of the library, with a 1 MiB limit on a response body. The fetch the other modules use by default has no time limit; `create_oauth_fetch(timeout_ms=...)` makes one that has. |
| `discovery` | Parsing of `WWW-Authenticate`, and discovery of protected-resource and authorization-server metadata with the issuer check. |
| `flow` | `authorize_mcp`: one pass of the authorization code flow for a provider. Also the single steps: `register_client`, `start_authorization`, `exchange_authorization_code`, `refresh_authorization`. |
| `provider` | `McpOAuthProvider`: the provider the flow is run with. It keeps one server's client registration, tokens, PKCE verifier, `state` value and discovery result as one document in a store its caller supplies. |
| `callback` | `OAuthCallbackServer`: the local HTTP server the browser is redirected to at the end of a sign-in. |
| `page` | The HTML pages that server shows. |

The flow (`authorize_mcp`) returns `AUTHORIZED` when the provider holds
usable tokens and `REDIRECT` when it handed the provider an authorization
URL for the user to open; the caller then calls it again with the code of
the authorization response.

- It uses the provider's stored discovery result or runs discovery and
  stores the result. When the caller passes the URL of the authorization
  server's metadata, that document is used and nothing is stored.
- The client is the stored one, else a Client ID Metadata Document the
  provider offers, else one from dynamic client registration. A server
  without a registration endpoint fails with `Authorization server does not
  support dynamic client registration`.
- The requested scope is the one the caller passes, else the
  `scopes_supported` of the protected-resource metadata, else the scope of
  the provider's client metadata, else none.
- The authorization URL carries `response_type=code`, `client_id`, a PKCE
  S256 challenge, `redirect_uri`, `state`, `scope`, `resource`, and
  `prompt=consent` when the scope includes `offline_access`. A server whose
  metadata rules out the code response type or S256 is refused.
- A token request goes only to an `https` endpoint or to one on a loopback
  host; otherwise the flow fails with `Refusing to send OAuth credentials to
  non-HTTPS endpoint <url>`. The client authenticates with the method it was
  registered with when the server supports it, else with the first of basic,
  post and none that fits the client and the server.
- A code is exchanged only when the `iss` of the authorization response
  matches the server's issuer, if the response has one or the server's
  metadata says it sends one (RFC 9207).
- With a stored refresh token and no code, the tokens are refreshed. A
  refresh the server answers with `server_error`, or that fails without an
  OAuth error from the server (a network failure, a time limit, an answer
  that is no token document), leads to a new authorization. A cancelled
  flow, an insecure token endpoint and other OAuth errors are raised.
- When the server answers a token request with `invalid_grant`, the provider
  is told to forget its tokens, and with `invalid_client` or
  `unauthorized_client` everything it stored; the flow then runs once more.
- A token answer without `scope` is stored with the requested scope. A
  refresh answer without `scope` or without a refresh token keeps the old
  one.

The provider (`McpOAuthProvider`) binds its document to one server URL: a
document stored for another URL counts as empty and is replaced by the first
write. Its `state` value is 32 random bytes in hex. The store in the
library, `MemoryOAuthStateStore`, forgets everything with the process; the
one that keeps a document is [the credentials file](#the-credentials-file).
The provider locks its own reads and writes; it does not lock a store shared
with another provider or another process.

The callback server (`OAuthCallbackServer`) listens on `127.0.0.1`, on a
free port or a given one, at the path `/callback`, and can be given more
paths. A caller registers the `state` value of a sign-in and waits for the
request that carries it.

- A request to another path is answered 404, and one without a registered
  `state` 400 `Invalid or expired OAuth state`.
- A request with an `error` parameter fails the wait with the error's
  description, or its code when there is none. One without a code is
  answered 400 and fails the wait, and a
  response on a path other than the one the wait named fails it with `The
  authorization response arrived on another redirect URI`.
- A wait ends with `OAuth callback timed out` after five minutes, or the
  limit the server was made with.
- The page is plain text unless the caller passes a function that renders
  it. `page.render_sign_in_page` gives the HTML pages of a sign-in: `Signed
  in to the MCP server. You may now close this page.`, or the failure's
  message and details.

### The credentials file

`pipy_harness.native.mcp.credentials` has the store that keeps OAuth state
on disk, `McpOAuthCredentialStore`, and the auth provider that uses it for
the requests to a server, `McpServerAuthProvider`. A session and `pipy mcp
list` create both for the config home and give them to the connections of
servers that [sign in with OAuth](#sign-in-with-oauth). The first sign-in
creates the file.

The file is `mcp-auth.json` in the directory of the user-level `mcp.json`,
readable and writable only by you. It is one JSON object with one entry per
server, the state document of that server's provider. The key of an entry
is the namespace of the server's tools and its URL,
`mcp__<name>|<url>`, so:

- two servers with the same URL and different names have separate entries
  and sign in separately;
- a server that has the same name and URL in the user-level and a project
  `mcp.json` has one entry;
- names that differ only in `-` and `_` are the same server.

`for_server(name, url)` gives the store of one server, which a provider is
created with; `tokens(name, url)` reads a server's tokens and
`remove(name, url)` deletes its entry and says whether there was one. It
holds the server's refresh lock (below) meanwhile, so a token refresh that
is running does not store the tokens again after a sign-out. A
write locks the file, reads it again and replaces it as a whole, so entries
written by another process are kept. A read takes no lock and creates
nothing: looking for the token of a server nobody signed in to leaves no
file behind. A file that is not JSON is an error and is left as it is.

`refresh_lock()` of a server's store is the lock a token refresh holds from
reading the tokens to saving the new ones, so that two processes never use
one refresh token twice. It is a file `mcp-auth-refresh-<hash>` next to the
credentials file that exists only while the lock is held or waited for. A
waiter tries every 100 ms and gives up after 25 seconds.

The auth provider (`McpServerAuthProvider`) is what the HTTP transport of
an OAuth server is given. It never opens a browser:

- Every request carries the stored access token; without one it carries
  none.
- An access token within 30 seconds of its expiry is refreshed before the
  request when a refresh token is stored. When that refresh fails, the old
  token is sent and the server decides.
- The transport hands the provider the first answer to a request with
  status 401, or with status 403 and a challenge that reports
  `insufficient_scope`, and then sends the request once more. For a 401 the
  provider refreshes the tokens. It raises `MCP OAuth authorization requires
  user interaction`, which fails the request, when the challenge asks for
  more scope (a refresh keeps the granted scope), when no refresh token is
  stored, and when the refresh ends in a new authorization.
- Requests of one process share one refresh. Processes are kept apart by
  the refresh lock, and a process that finds other tokens stored than the
  ones that were rejected uses those without refreshing.
- Each request to the authorization server is limited to 15 seconds. A
  refresh is not cancelled, since its answer may carry a new refresh token
  that must be saved; `settled()` returns when no refresh is running. After
  `close()` the provider starts no refresh any more, so a connection that
  shuts down calls `close()` and then `settled()`.

Where the library differs from Pi's `@earendil-works/pi-mcp`, for those who
build on it:

- The HTTP transport follows a redirect only within the configured server's
  origin, so configured headers and tokens go to no other host. It has no
  proxy support and does not decompress responses.
- The OAuth fetch follows redirects only for requests without a body, drops
  `Authorization`, `Proxy-Authorization` and `Cookie` on a redirect to
  another origin, and limits a response body to 1 MiB. So a registration or
  token request that the server redirects fails with the redirect's status.
- The OAuth flow reads URLs with the strict parser of the
  [configuration](#configuration-rules). A token endpoint that parser does
  not accept is refused like an insecure one. A registration answer that is
  not JSON, or has no usable `client_id`, is an error.
- A request of the OAuth flow that was cancelled ends the flow, also during
  a refresh.
- The OAuth provider compares server URLs without their fragment, and
  refuses a server URL with user info.
- An auth provider sends the requests of a refresh itself; Pi sends them
  with the fetch of the transport whose request was rejected. A request
  that was given up while the provider worked is not sent again.
- The refresh lock of the credentials file is a `flock`. The lock of a
  process that died is free at once; Pi takes such a lock over after 20
  seconds. Deleting a server's credentials holds that lock too; in Pi a
  refresh that ends after a sign-out stores the tokens again. Pi also reads credentials an older version stored under the URL
  alone, which no pipy version wrote.
- The callback server does not wait for open browser connections when it is
  closed: it gives a page that is being sent two seconds and then drops the
  connections. Registering a wait on a closed server fails at once. A
  page-rendering function that raises gets the plain-text page sent instead.
- The sign-in pages have Pi's layout without its logo.
- The sign-in registers its wait for the browser before it shows the
  authorization URL. Its prompt for a pasted redirect URL runs on a thread
  of its own and is given an event instead of an abort signal. A pasted URL
  of a scheme other than `http` and `https` is refused as not matching the
  redirect URI.
- A cancelled or timed-out HTTP request closes its connection.
- A line from a stdio server that is longer than the message limit (16 MiB) is
  skipped to its end and reported once.
- Windows process handling is not ported.
