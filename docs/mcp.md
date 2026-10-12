# MCP

pipy does not connect to MCP servers yet. The work is planned in the
[MCP parity plan](specs/2026-10-12-mcp-parity-plan.md) and lands in slices;
this page grows with them. What a session can use today is the tool model MCP
tools will use (exposure, namespaces, structured results), codemode scripts
that reach extension tools, and tool search.

The MCP client itself exists as library code that no session, command or
setting uses yet: the protocol client, the stdio and streamable HTTP
transports, the `mcp.json` reader and editor, the connection to a stdio
server, and OAuth discovery. See
[what exists as a library](#what-exists-as-a-library). The sections on
configuration below describe the file format that library accepts, so a file
written now is valid when sessions start to connect. Until then pipy never
reads an `mcp.json`, starts no server process and sends no request to a
server.

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

Not read by pipy yet: this section describes what the configuration library
loads and validates.

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
the codemode tool is activated when a server with `codemode` exposure
connects. A project value overrides the user-level one.

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
indentation. Nothing calls these functions yet; the `pipy mcp` commands and
`/mcp` will.

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

## Server connections

Not used by a session yet: this section describes what the connection library
does with a configured server. Only stdio servers can be connected; an entry
with a `url` fails with a message that says so.

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

## What exists as a library

The package `pipy_harness.native.mcp` is synchronous and uses only the
standard library. It is not part of the SDK surface, and nothing in a pipy
session imports it yet.

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
| `oauth` | OAuth document types and errors, a bounded HTTP fetch, parsing of `WWW-Authenticate`, and discovery of protected-resource and authorization-server metadata with the issuer check. |

Not built yet: connections to HTTP servers with their retries and session
renewal, registration of a server's tools, resources, readiness waits, the
`mcp_servers` prompt section, `--no-mcp`, the `pipy mcp` commands, `/mcp`,
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
