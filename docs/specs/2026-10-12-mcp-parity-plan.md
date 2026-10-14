# MCP and codemode tool reach: Pi parity plan

Status: plan, 2026-10-12. Work item `pipy-mcp-codemode-parity`, backlog ID MCP1.
No product code is changed by this document.

Audited 2026-10-14 (slice S40): the slices S01 to S40 and X1 are on
`feat/mcp-parity`, not merged. Of the 287 rows of section 2, 285 are closed
(106 of them with a recorded limit or deviation), MGR-09 is closed in part
and SLH-13 is open; see section 3.2 and the
[acceptance note](../acceptance/2026-10-13-mcp-parity.md).

## 1. Goal, reference and how to read this plan

pipy has no MCP support, and its Python codemode tool reaches only the seven
native built-in tools. This plan brings the whole MCP feature to functionality
parity with Pi: codemode reaching extension and MCP tools with discovery, the
MCP client and its transports, configuration, exposure modes, `tool_search`,
the `mcp_servers` prompt section, resources, OAuth, result truncation, the
`mcp` shell commands, the `/mcp` manager, the CLI flags, reload behaviour and
the extension and SDK hooks.

**Pi reference:** `~/src/pi-mono` at
`a447ba11f3e7760fcff91e924bda8c20033953ea` (2026-10-12), `packages/coding-agent`
after 0.99.0 with the Unreleased section. Commits `24382bedd` (MCP decoupled
from codemode: `reachesTools`, namespace `aliases`, self-describing MCP result
schema) and `8a9c051f8` (schema constraints in declarations) are included.

Path abbreviations used in the checklist:

| Prefix | Pi path |
| --- | --- |
| `mcp/` | `packages/mcp/src/` |
| `ext/` | `packages/coding-agent/src/extensions/mcp/` |
| `core/` | `packages/coding-agent/src/core/` |
| `cm/` | `packages/coding-agent/src/extensions/codemode/` |
| `ts/` | `packages/coding-agent/src/extensions/tool-search/` |
| `pcm/` | `packages/codemode/src/` |
| `docs/` | `packages/coding-agent/docs/` |

Section 2 is the acceptance list for the final parity judgement. Every row is
one checkable behaviour with the Pi location that defines it and the slice
that closes it. Section 3 lists what this plan proposes not to port, with the
reason, the differences the slices recorded (3.1) and the result of the
closing audit (3.2). Section 4 is the pipy design, section 5 the ordered slices, section 6
the test strategy and section 7 the owner's decisions on the questions the plan raised.

Decisions already made by the owner and not reopened here:

- pipy keeps its synchronous native core. There is no new async runtime and no
  dependency on the async `mcp` Python SDK. The JSON-RPC client, both
  transports and OAuth are written in pipy, as Pi wrote its own.
- Codemode stays Python on WASI. Python names (`search_tools`) replace Pi's
  JavaScript names (`searchTools`); the capability must exist.
- Configuration follows Pi's `mcp.json` format at pipy's locations, with
  pipy's existing project-trust rules.
- Privacy is not a reason to diverge from Pi. No deprecation shims.

Names in this plan: "config home" is `resolve_config_home()` in
`native/settings.py` (`PIPY_CONFIG_HOME`, then `${XDG_CONFIG_HOME}/pipy`, then
`~/.pipy`, then `~/.config/pipy`). Where Pi writes `~/.pi/agent/<file>`, pipy
writes `<config home>/<file>`; where Pi reads `.pi/mcp.json`, pipy reads
`.pipy/mcp.json`. Where Pi prints `pi`, pipy prints `pipy`. These three
substitutions apply to every row and are not repeated.

## 2. Parity checklist

A row is closed when the named slice has landed with a test that pins the
behaviour. "Text" in a row means the exact user-visible string is part of the
behaviour (with the `pi`/`pipy` substitution). Section 3.2 says which rows
are closed.

### 2.1 Configuration (CFG)

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| CFG-01 | User-level servers are read from `<config home>/mcp.json`; project servers from `<cwd>/.pipy/mcp.json` only when the project is trusted. `projectConfig` is reported only for a trusted project. | `ext/config.ts` `loadMcpConfig` | S14 |
| CFG-02 | A missing file is not an error. Invalid JSON gives `<path>: <parser message>`; a top level that is not an object, or an `mcpServers` that is not an object, gives `<path>: expected an object with an "mcpServers" object`. The other file still loads. | `ext/config.ts` `readConfigFile` | S14 |
| CFG-03 | An invalid entry is reported as `<path>: <reason>` and skipped; the remaining entries still load. | `ext/config.ts` `readConfigFile` | S14 |
| CFG-04 | A project entry that defines a server (`command`, `url` or `type` present) replaces the user-level entry of the same name as a whole. | `ext/config.ts` `readConfigFile` | S14 |
| CFG-05 | A project entry without `command`, `url` and `type` is an override: only `enabled`, `exposure` and `toolExposure` are merged over the user-level entry, everything else (including `env`, `headers`, `oauth`, `auth`) is kept, and the entry records the override path. | `ext/config.ts` `isOverride`, `readConfigFile` | S14 |
| CFG-06 | Override errors: no user-level server gives `server "<n>" needs "command" or "url", or a global server to override`; other keys give `server "<n>": an override can only set enabled, exposure, toolExposure`. The merged entry is validated again. | `ext/config.ts` `readConfigFile` | S14 |
| CFG-07 | Two server names that differ only in `-` and `_` conflict: the later one is rejected with `server "<a>" conflicts with "<b>"`. | `ext/config.ts` `readConfigFile`; `core/mcp-servers.ts` `mcpNamespace` | S14 |
| CFG-08 | A project entry with `auth` is rejected: `server "<n>": auth is only allowed in the global mcp.json`. | `ext/config.ts` `readConfigFile` | S14 |
| CFG-09 | Top-level `autoEnableCodemode` (boolean, default true); a project value overrides the user-level value; another type gives `<path>: autoEnableCodemode must be a boolean`. | `ext/config.ts` `readConfigFile`, `loadMcpConfig` | S14 |
| CFG-10 | Server names match `^[A-Za-z0-9_-]+$`; otherwise `invalid server name "<n>" (use letters, digits, "_" and "-")`. An entry that is not an object gives `server "<n>" must be an object`. | `core/mcp-servers.ts` `validateMcpServerConfig` | S14 |
| CFG-11 | `type` is optional. `command` (string) selects stdio, `url` (string) selects streamable HTTP. Accepted `type` values are `stdio` with `command`, and `http` or `streamable-http` with `url`. `sse` gives `server "<n>": legacy SSE transport is not supported; use the streamable HTTP URL`. Neither gives `server "<n>" needs either "command" (stdio) or "url" (streamable HTTP)`. | `core/mcp-servers.ts` `validateMcpServerConfig` | S14 |
| CFG-12 | Stdio fields: `args` must be an array of strings, `env` a string-to-string object, `cwd` a string; each has its own error text. `command` is one executable, not a shell string. | `core/mcp-servers.ts` `validateMcpServerConfig` | S14 |
| CFG-13 | HTTP fields: `url` must parse and use `http` or `https` (`url must be an http or https URL`); `headers` must map names to strings. | `core/mcp-servers.ts` `validateMcpServerConfig` | S14 |
| CFG-14 | `exposure` is one of `codemode`, `deferred`, `direct`, `hidden`; default `codemode`. `codemode-deferred` is accepted as an alias for `codemode`, on the server and in `toolExposure` values, and is replaced by `codemode` in the validated config. | `core/mcp-servers.ts` `MCP_EXPOSURE_ALIASES`, `resolveExposureAliases` | S14 |
| CFG-15 | `toolExposure` must map tool names to exposures; errors name the offending key. `enabled` must be a boolean, `description` a string, `timeout` a positive number of seconds. | `core/mcp-servers.ts` `validateMcpServerConfig` | S14 |
| CFG-16 | Tool exposure resolution: an exact `toolExposure` key wins; otherwise the first key containing `*` that matches, in object order; otherwise the server `exposure`. `*` matches any characters and every other character is literal. | `core/mcp-servers.ts` `getMcpToolExposure`, `toolPatternRegExp` | S14 |
| CFG-17 | `oauth` validation: must be an object; `clientId`, `clientSecret`, `scope` strings; `callbackPort` an integer 1 to 65535; `clientName` a non-empty string. | `core/mcp-servers.ts` `validateOAuth` | S14 |
| CFG-18 | `oauth.callbackUrl` must be an `http` URI on `localhost`, `127.0.0.1` or `[::1]` without query or fragment; a port in it that differs from `callbackPort` is an error. | `core/mcp-servers.ts` `validateOAuth`, `isLoopbackRedirectUri` | S14 |
| CFG-19 | `oauth.clientRegistration` is `dcr` or `cimd`. `cimd` cannot be combined with `clientId` or `clientName`, and a `callbackUrl` must then use `localhost` or `127.0.0.1` with path `/callback`. pipy also requires `oauth.clientMetadataBaseUrl` with `cimd` (DEV-06). | `core/mcp-servers.ts` `validateOAuth` | S14 |
| CFG-20 | `oauth.authServerMetadataUrl` must be `https`, or `http` on a loopback host. | `core/mcp-servers.ts` `validateOAuth` | S14 |
| CFG-21 | `auth.provider` must be a non-empty provider name, and `auth` requires an `https` URL or `http` on a loopback host. | `core/mcp-servers.ts` `validateMcpServerConfig` | S14 |
| CFG-22 | Disabled servers (`enabled: false`) stay in the loaded list so they can be enabled again. | `ext/config.ts` `loadMcpConfig` | S14 |
| CFG-23 | Changing one server's `enabled` or `exposure` in a file keeps all other content and the file's indentation and ends the file with a newline. In a defining entry the defaults (`enabled: true`, `exposure: "codemode"`) remove the key; in an override entry they are written. With `override`, a missing entry is created as an override. A file that does not define the server gives `<path> does not define MCP server "<n>"`. | `ext/config.ts` `updateMcpServerConfig`, `editMcpServers` | S14 |
| CFG-24 | Adding a server creates the file and its directory when missing, replaces an entry of the same name and reports whether it replaced one. Removing reports false when the file or the entry is missing. | `ext/config.ts` `addMcpServerConfig`, `removeMcpServerConfig` | S14 |
| CFG-25 | `env` and `headers` values and `oauth.clientSecret` resolve `${NAME}` environment references and whole-value `!command` commands; a value that cannot be resolved fails that server's connection with a message naming the server and the key. | `ext/runtime.ts` `createDefaultTransport`, `oauthSettings`; `core/resolve-config-value.ts` | S15 |
| CFG-26 | A leading `~` or `~/` in `command`, in each argument and in `cwd` names the home directory. A relative `cwd` resolves against the session directory; the default is the session directory. | `ext/runtime.ts` `expandHome`, `createDefaultTransport` | S15 |

### 2.2 Protocol client (CLT)

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| CLT-01 | `initialize` sends protocol version `2025-11-25`, the client capabilities (`roots: {}` when roots are configured, nothing for sampling or elicitation) and `clientInfo` name and version; then `notifications/initialized`. The client is usable only after both. | `mcp/client.ts` `connect` | S11 |
| CLT-02 | A server answer with protocol version `2025-11-25`, `2025-06-18`, `2025-03-26` or `2024-11-05` is accepted; another version fails the connection with `MCP server selected unsupported protocol version <v>`. | `mcp/protocol/types.ts` `SUPPORTED_PROTOCOL_VERSIONS`; `mcp/client.ts` `connect` | S11 |
| CLT-03 | An `initialize` result without string `protocolVersion`, object `capabilities`, or `serverInfo.name`/`version`, or with non-string `instructions`, is rejected. | `mcp/client.ts` `validateInitializeResult` | S11 |
| CLT-04 | Server info, capabilities, instructions and the negotiated version are available after connecting; a failed connect closes the transport. | `mcp/client.ts` `connect` | S11 |
| CLT-05 | Requests carry increasing integer ids. A request times out after its own timeout, else the client default (30 s); the caller gets a timeout error naming the milliseconds and the server gets `notifications/cancelled` with reason `Request timed out`. | `mcp/client.ts` `requestInternal`, `armTimeout`, `cancelPending` | S11 |
| CLT-06 | Cancelling a request (caller signal) rejects it with an abort error and sends `notifications/cancelled` with the request id. `initialize` is never cancelled on the wire. | `mcp/client.ts` `requestInternal`, `cancelPending` | S11 |
| CLT-07 | A request with a progress handler sends `_meta.progressToken`; each matching `notifications/progress` restarts the request's timeout and calls the handler. Progress for unknown tokens is ignored. | `mcp/client.ts` `requestInternal`, `handleProgress` | S11 |
| CLT-08 | A JSON-RPC error response rejects the request with code, message and data. A response for an unknown id is reported as an error and changes nothing else. | `mcp/client.ts` `handleResponse` | S11 |
| CLT-09 | Server requests: `ping` is answered `{}`; `roots/list` with the configured roots; an unknown method with error `-32601 Method not found: <m>`; a failing handler with `-32603`. `notifications/cancelled` from the server aborts the matching handler. | `mcp/client.ts` `handleRequest`, `handleCancelled` | S11 |
| CLT-10 | Notifications reach every listener registered for their method; a listener that raises does not affect other listeners or the connection. | `mcp/client.ts` `handleNotification` | S11 |
| CLT-11 | A transport error is reported and does not fail pending requests. A transport close rejects every pending request with a connection-closed error, aborts server requests in progress and calls each close listener exactly once. `close()` is idempotent. | `mcp/client.ts` `handleTransportClose`, `markClosed`, `close` | S11 |
| CLT-12 | Lists follow `nextCursor` through all pages; `null` and `""` end pagination; a repeated cursor fails with `MCP <method> returned duplicate cursor: <c>`; more than 1000 pages fails. | `mcp/client.ts` `listAll`, `validateListPage` | S12 |
| CLT-13 | A tool entry needs a string `name` and an object `inputSchema`; a resource a string `uri`; a template a string `uriTemplate`. A resource or template without `name` uses its URI. Invalid pages are rejected. | `mcp/client.ts` `isTool`, `isResource`, `toResource`, `toResourceTemplate` | S12 |
| CLT-14 | `tools/call` results: missing `content` becomes `[]`; non-array `content` or non-object `structuredContent` is rejected. `resources/read` results need `contents` entries with `uri` and `text` or `blob`. | `mcp/client.ts` `validateCallToolResult`, `validateReadResourceResult` | S12 |
| CLT-15 | Content conversion: text and image blocks pass through; audio becomes `[audio <mime> omitted]`; a resource link `<name>: <uri>`; an embedded text resource its text; an embedded image blob an image; another blob `[binary resource <uri> (<mime>) omitted]`; an unknown block `[unsupported MCP content <type>]`. Without content blocks, `structuredContent` becomes its JSON with two-space indentation. | `mcp/protocol/content.ts` `toLlmContent` | S12 |
| CLT-16 | The roots the coding agent announces are one root: the session directory as a `file://` URI, named by its base name. | `ext/runtime.ts` `connectOnce` | S15 |

### 2.3 Stdio transport (STD)

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| STD-01 | The server process is started with the parent environment plus the configured `env`, in the configured directory, with piped stdin/stdout and captured stderr. A start failure (missing executable) fails the connection. | `mcp/transports/stdio.ts` `start` | S13 |
| STD-02 | Messages are newline-delimited JSON in both directions. A trailing `\r` is stripped, blank lines are skipped, and a line that is not a valid JSON-RPC message is reported without closing the connection. | `mcp/transports/stdio.ts` `handleStdout`, `send` | S13 |
| STD-03 | A message over 16 MiB is dropped with an error (`MCP stdio message exceeds <n> bytes`) and the stream continues. | `mcp/transports/stdio.ts` `handleStdout`; `mcp/transports/transport.ts` `DEFAULT_MAX_MESSAGE_BYTES` | S13 |
| STD-04 | The last 64 KiB of stderr are kept and readable after the process failed. | `mcp/transports/stdio.ts` `handleStderr` | S13 |
| STD-05 | The server runs in its own process group (POSIX). Closing closes stdin, sends SIGTERM to the group after 500 ms, SIGKILL after a further 2 s, and after the server exits SIGTERMs the group once more so children of wrappers such as `npx` or `uvx` do not survive. | `mcp/transports/stdio.ts` `close`, `killProcessTree` | S13 |
| STD-06 | Process groups still alive when pipy exits are sent SIGTERM. | `mcp/transports/stdio.ts` `installExitHook` | S13 |
| STD-07 | When the process exits, the transport closes; a partial message left in the buffer is reported. Sending on a closed transport fails with a connection-closed error. | `mcp/transports/stdio.ts` `start` (close handler), `send` | S13 |

### 2.4 Streamable HTTP transport (HTP)

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| HTP-01 | Every message is a POST with `Accept: application/json, text/event-stream` and JSON body, plus the configured headers. | `mcp/transports/streamable-http.ts` `send` | S24 |
| HTP-02 | An `Mcp-Session-Id` response header is remembered and sent on later requests; `MCP-Protocol-Version` is sent after negotiation; a token from the auth provider is sent as `Authorization: Bearer` and replaces a configured header of that name. | `mcp/transports/streamable-http.ts` `buildHeaders`, `captureSession` | S24 |
| HTP-03 | A JSON response body (one message or an array) is delivered; an SSE response is read until the stream ends, delivering every message. Events without data or with an event type other than `message` are ignored; event data that is not a JSON-RPC message is reported. | `mcp/transports/streamable-http.ts` `send`, `consumeSse` | S24 |
| HTP-04 | A request answered with 202 or 204 fails with `MCP server accepted request <method> without a response`. Notifications and responses ignore the response body. Another content type fails with `Unsupported MCP response content type: <t>`. | `mcp/transports/streamable-http.ts` `send` | S24 |
| HTP-05 | Failure statuses: 401 is an authentication-required error carrying `WWW-Authenticate`; 404 while a session id is held is a session-expired error; any other gives `MCP HTTP request failed with status <n>: <body>`, the body cut to 500 characters (at most 8 KiB are read). | `mcp/transports/streamable-http.ts` `checkResponse`, `describeHttpFailure` | S24 |
| HTP-06 | A 401, or a 403 whose `WWW-Authenticate` reports `insufficient_scope`, is handed to the auth provider once with the rejected token; the request is then sent once more. | `mcp/transports/streamable-http.ts` `authorizedFetch`, `needsAuthorization` | S30 |
| HTP-07 | SSE parsing: CRLF and LF line ends, `:` comments, multi-line `data`, `event`, `id` (ignored when it contains NUL), numeric `retry`; an event over 16 MiB fails the stream. | `mcp/transports/streamable-http.ts` `consumeSseStream` | S24 |
| HTP-08 | After `notifications/initialized` the client opens the server-to-client GET stream. A 405 means the server has none. A dropped stream is reopened with `Last-Event-ID` (only when an id was seen), waiting the server's `retry` value or 1 s doubling to 30 s, and gives up after 5 consecutive failures. | `mcp/transports/streamable-http.ts` `startGetStream`, `runGetStream`, `openSseStream`, `reconnectDelay` | S25 |
| HTP-09 | A response stream that ends or breaks before its answer is resumed with GET and `Last-Event-ID` when the server assigned event ids. Otherwise only that request fails, with `MCP response stream failed: <reason>` (`stream ended without a response` when the stream just ended). | `mcp/transports/streamable-http.ts` `consumeResponseStream` | S25 |
| HTP-10 | Only network failures and statuses 408, 429 and 5xx are retried for streams; authentication, session and protocol errors are not. | `mcp/transports/streamable-http.ts` `isRetryable`, `isTransientStatus` | S25 |
| HTP-11 | Closing aborts requests in flight and, when a session id is held, sends `DELETE` with the session id and the last request's token, waiting at most 1 s and ignoring failures. Closing never asks the auth provider for a token. | `mcp/transports/streamable-http.ts` `close` | S24 |

### 2.5 Server connection (CON)

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| CON-01 | A server is in one of the states `connecting`, `connected`, `disconnected`, `needs-auth`, `failed`, `closed`; state, error and tool changes are observable by the manager and the `/mcp` view. | `ext/runtime.ts` `McpServerConnection` | S15 |
| CON-02 | Requests time out after `timeout` seconds from the config, default 60; progress notifications reset the timer. | `ext/runtime.ts` `timeoutMs`; `ext/tools.ts` `createMcpToolDefinition` | S15 |
| CON-03 | On connect the tools are listed only when the server declares the `tools` capability; a server without it connects with no tools. | `ext/runtime.ts` `connectOnce` | S15 |
| CON-04 | The server's `instructions` are kept, trimmed; empty instructions count as none. | `ext/runtime.ts` `connectOnce` | S15 |
| CON-05 | `notifications/tools/list_changed` lists the tools again and updates the registered tools; a failed refresh records `Failed to refresh tools: <reason>`. | `ext/runtime.ts` `refreshTools` | S15 |
| CON-06 | A dropped connection sets `disconnected` with error `Connection closed`, followed by the last 2000 characters of stderr for stdio servers. The next call reconnects. | `ext/runtime.ts` `handleClientClose`, `getClient` | S15 |
| CON-07 | A failed connect sets `failed` with the error and the stderr tail and reports `MCP server "<n>" failed to connect: <error>`. | `ext/runtime.ts` `connectFailed` | S15 |
| CON-08 | Concurrent callers share one connection attempt. | `ext/runtime.ts` `getClient` | S15 |
| CON-09 | Closing a server stops a connect in progress, including the wait between retries, and returns only when no transport of that server is open. After closing, calls fail with `MCP server "<n>" is shut down`. | `ext/runtime.ts` `close`, `connectOnce` | S15 |
| CON-10 | HTTP connects that fail with a network error or status 408, 429 or 5xx except 501 are retried twice, after 250 ms and 1 s. Stdio connects are not retried. | `ext/runtime.ts` `open`, `isTransientError`, `CONNECT_RETRY_DELAYS_MS` | S26 |
| CON-11 | Resource reads and listings are retried once, after 250 ms, after a transient HTTP status. Tool calls are never retried for that reason. | `ext/runtime.ts` `withClient` | S26 |
| CON-12 | A session-expired answer makes the request run once more on a new session, for tool calls too; other calls in flight on the old session retry the same way. | `ext/runtime.ts` `withClient` | S26 |
| CON-13 | An HTTP server uses OAuth unless its config has an `Authorization` header (any letter case) or `auth`. | `ext/runtime.ts` `usesOAuth` | S30 |
| CON-14 | A server that needs a sign-in enters `needs-auth` and calls fail with `MCP server "<n>" requires sign-in. Run /mcp to sign in.`; with `auth.provider` the text says `Run /login <provider> to sign in.` | `ext/runtime.ts` `signInRequiredMessage`, `markNeedsAuth`, `needsSignIn` | S30, S33 |
| CON-15 | Server log messages (`notifications/message`) are appended to `<config home>/mcp.log` as `<ISO time> [<server>] <level> <logger>: <message>`; level defaults to `info`, the logger part is omitted when absent, non-string data is JSON, continuation lines are indented four spaces. Write failures are ignored. | `ext/log.ts` `formatMcpLogMessage`, `McpServerLog.write` | S15 |
| CON-16 | When `mcp.log` is larger than 5 MB it is renamed to `mcp.log.1` before the next line is written. | `ext/log.ts` `McpServerLog.write` | S15 |

### 2.6 Tool adaptation and results (TLS)

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| TLS-01 | A server tool is registered as `mcp__<server>__<tool>` with every character outside `[A-Za-z0-9_]` replaced by `_`. | `ext/tools.ts` `createMcpToolName` | S16 |
| TLS-02 | A name longer than 64 characters is cut and ends in `_` plus the first 8 hex digits of SHA-256 over `<server>\0<tool>`. | `ext/tools.ts` `createMcpToolName` | S16 |
| TLS-03 | Names are computed from each tool list the server sends. When two tools in the current list sanitize to the same name, all of them get the hash suffix, independent of list order; when the collision disappears from a later list, the remaining tool gets its plain name back. A name that was ever assigned to a different tool (of any server) in this session is not given to another tool; that tool gets the hash suffix instead. | `ext/index.ts` `registerTools` (`assignName`, `toolOwners`, `plain`) | S16 |
| TLS-04 | The description is the tool's trimmed description, else its title, else its annotation title, else `MCP tool <tool> from server <server>`. | `ext/tools.ts` `createMcpToolDefinition` | S16 |
| TLS-05 | The declared input schema is the server's schema with `type: "object"` added when missing and `properties: {}` added when missing. Other keywords pass through unchanged. | `ext/tools.ts` `toParameters` | S16 |
| TLS-06 | Every MCP tool declares an output schema: an object with `content` (array of blocks with `type`, `text`, `data`, `mimeType`), `structuredContent` (the tool's own output schema, or any object) and `isError`, `content` required, further fields allowed. Local `$ref`s in the server's output schema are rewritten so they still resolve below `#/properties/structuredContent`; `$ref` keys inside `const`, `enum`, `default`, `examples` are data and are left alone. | `ext/tools.ts` `createMcpResultSchema`, `relocateSchema` | S16 |
| TLS-07 | The tool carries the boolean annotations `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint` its server declares, and no others. | `ext/tools.ts` `toToolAnnotations` | S16 |
| TLS-08 | The tool belongs to the namespace `mcp__<server>` (with `-` as `_`), with alias `<server>`, the configured `description` and the server's instructions. | `ext/index.ts` `registerTools` | S16 |
| TLS-09 | MCP exposure maps to tool exposure: `codemode` and `deferred` both become tools that are callable from scripts but not listed in the codemode description; `direct` and `hidden` keep their meaning. | `ext/tools.ts` `toToolExposure` | S16 |
| TLS-10 | A call sends the validated arguments, the configured timeout and the cancellation signal. Progress notifications become live tool updates: the progress `message`, else `Progress <p>` or `Progress <p>/<total>`. | `ext/tools.ts` `createMcpToolDefinition` `execute` | S16 |
| TLS-11 | Model-facing content: text blocks as text, image blocks as images. With no content blocks the structured content is shown as JSON. An error result without text gets `MCP tool <server>/<tool> returned an error`. | `ext/tools.ts` `convertMcpResult` | S16, S39 |
| TLS-12 | A resource link becomes `[Resource <uri> "<title or name>" (<mime>, <size>): <description>. Read it with read_mcp_resource (server "<server>")]`; the parenthesis, the description and the read hint appear only when present, and the read hint only when the server's resources are reachable. | `ext/tools.ts` `blockToContent` | S16, S23 |
| TLS-13 | An embedded blob that is not an image: with a text MIME type (`text/*`, `application/json`, `+json`, `+xml`) it is decoded as UTF-8 text; otherwise it is saved to a temp file whose extension is the URI's (else `.bin`) and shown as `[Binary resource <uri> (<mime>, <size>) saved to <path>]`, or `... could not be saved: <reason>]`. | `ext/tools.ts` `blockToContent`, `extensionOf`, `isTextMimeType` | S16 |
| TLS-14 | `isError` results are error results for a direct model call. | `ext/tools.ts` `convertMcpResult` | S16 |
| TLS-15 | Model-facing text over 20 KB (UTF-8 bytes of all text blocks joined by newlines) is replaced by one text block: `Warning: truncated output (original token count: <bytes/4 rounded up>)`, `Total output lines: <n>`, a blank line, the first and last 10 KB around `…<N> chars truncated…` cut only at character boundaries, a blank line and `[Full output: <path> (read it with offset/limit)]`. Images follow that block. | `ext/tools.ts` `limitMcpContent`; `core/tools/truncate.ts` `truncateMiddle` | S16 |
| TLS-16 | The full text is saved to a new temp file readable only by the user (`pipy-mcp-<random>.txt`); the result details name it as `fullOutputPath`. When saving fails the block ends with `[Could not save the full output: <reason>]` and no path is published. | `ext/tools.ts` `limitMcpContent`, `saveToTempFile`; `utils/output-files.ts` `writeOutputFile` | S16 |
| TLS-17 | Result details are `{server, tool, fullOutputPath?}`. | `ext/tools.ts` `McpToolDetails`, `convertMcpResult` | S16 |
| TLS-18 | Scripts receive the whole `CallToolResult` without `_meta`: `content` blocks as the server sent them, `structuredContent`, `isError`, never truncated. | `ext/tools.ts` `convertMcpResult` | S16 |
| TLS-19 | A call to a tool of a disabled server fails with `MCP server "<n>" is disabled.`; of a server without a connection yet with `MCP server "<n>" is still starting.`; of a tool that is now hidden or no longer offered with `MCP tool "<server>/<tool>" is no longer available.` The server is resolved when the call runs, so a call prepared before a disable and enable uses the new connection. | `ext/index.ts` `registerTools` (`getClient`) | S17 |

### 2.7 Session integration (SES)

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| SES-01 | When a session starts, the servers are the valid `mcp.json` entries followed by extension-registered servers whose name (compared as namespace) no `mcp.json` entry uses. Every enabled server starts connecting in the background; startup does not wait. | `ext/index.ts` `session_start` handler, `registeredServers` | S17 |
| SES-02 | A server's tools become available when it connects. Tools of a server are registered with the exposure `toolExposure` resolves for each. | `ext/index.ts` `registerTools` | S17 |
| SES-03 | When a server announces a changed list, new tools are added, and withdrawn tools become unreachable; a tool that comes back is reachable again with its configured exposure. | `ext/index.ts` `registerTools` (previous/current) | S17 |
| SES-04 | Problems found at startup are reported once, as one warning: `MCP servers need attention:` then one indented line per config error (`config: <error>`) and per server that needs sign-in or failed (`<name>: needs sign-in` or `<name>: failed: <first error line>`), then `Run /mcp to fix.` Nothing is reported when there is nothing to report. | `ext/index.ts` `reportProblems`, `describeState` | S19 |
| SES-05 | The first prompt of a session waits for servers that have `direct` tools (server exposure or any `toolExposure` value) and are still connecting, at most 10 s. After the limit it shows `MCP servers are still connecting; their tools become available once connected.` and proceeds. Later prompts do not wait. | `ext/index.ts` `waitForDirectServers`, `hasDirectTools`, `DEFAULT_STARTUP_WAIT_MS` | S18 |
| SES-06 | Servers without `direct` tools never hold a prompt. | `ext/index.ts` `waitForDirectServers` | S18 |
| SES-07 | Before a tool that reaches other tools runs, pipy waits for the servers the call may reach: every enabled server when the reach is "all", otherwise each server whose namespace (`mcp__<server>` with `-` as `_`) is contained in one of the reach words. | `ext/index.ts` `tool_call` handler, `reachesServer` | S18 |
| SES-08 | A codemode script reaches "all" when its source uses a discovery global (`search_tools`, `describe_namespace`, `describe_tool`, `ALL_TOOLS`); otherwise its reach words are the identifiers in its source, so both `tools.mcp__docs__search` and a name built as `"mcp__docs__" + x` wait for `docs`. A script that names no server waits for none. | `cm/tool.ts` `getScriptReach`, `DISCOVERY_GLOBALS` | S18 |
| SES-09 | `tool_search` and the three resource tools wait for every enabled server. A direct call to an MCP tool waits for its own server only, including a reconnect in progress. | `ext/index.ts` `tool_call` handler; `ts/tool.ts` `reachesTools` | S18 |
| SES-10 | Every readiness wait ends when the turn is cancelled. A wait also covers a connection attempt that was queued while waiting (disable and enable, reconnect). | `ext/index.ts` `waitForServers` | S18 |
| SES-11 | pipy activates `codemode` when an enabled server has a tool with `codemode` exposure, unless `autoEnableCodemode` is false, and activates `tool_search` when an enabled server has a tool with `deferred` exposure. This is decided from the config, before any server connects. | `ext/index.ts` `ensureDiscoveryActive`, `configuredExposures` | S19 |
| SES-12 | Either mechanism reaches both exposures: scripts can call `deferred` tools, and `tool_search` can load `codemode` tools. | `core/agent-session.ts` `_getCallableTools`; `ts/tool.ts` `isSearchable` | S19 |
| SES-13 | When non-direct tools exist and neither `codemode` nor `tool_search` is active, pipy warns once per session: `MCP tools are only reachable from the codemode or tool_search tool, but neither is active; they cannot be called.`, with ` (autoEnableCodemode is false)` before the semicolon when that is the cause. | `ext/index.ts` `ensureDiscoveryActive` | S19 |
| SES-14 | Disabling a server in a session closes its connection (also one that is still connecting) and makes its tools unreachable; enabling connects it after the previous connection finished closing. | `ext/index.ts` `setEnabled`, `hideTools` | S21 |
| SES-15 | Changing a server's exposure in a session re-registers its tools with the new exposure, and tools that are no longer `direct` leave the declared set. | `ext/index.ts` `setExposure` | S21 |
| SES-16 | A config change from the session is saved to the override file when the server has one, else to the file that defines it. Servers registered by extensions change for the session only. A failed save reports `Could not update <path>: <reason>` and changes nothing. | `ext/index.ts` `saveConfig` | S21 |
| SES-17 | When the session ends, every connection is closed and running sign-ins are cancelled before shutdown completes; a server that is still connecting is closed too. | `ext/index.ts` `session_shutdown` handler | S17 |
| SES-18 | A sign-in done by another process (for example `pipy mcp login` run through `bash`) is picked up at the next turn: servers in `needs-auth` whose stored tokens changed reconnect. | `ext/index.ts` `turn_start` handler, `reconnectSignedIn`, `tokensAtSignIn` | S31 |
| SES-19 | Every MCP call, direct or nested, passes the normal tool pipeline: `tool_call` hooks can block it and `tool_result` hooks can change its result, for built-in resource tools as well. | `docs/mcp.md` "Permissions"; `core/agent-session.ts` tool hooks | S17 |
| SES-20 | `/reload` reads `mcp.json` again: removed servers disconnect and their tools become unreachable, new servers connect, changed entries reconnect with the new config, and the startup report and the first-prompt wait apply again. | `ext/index.ts` `session_shutdown` and `session_start` handlers (reload) ; `docs/mcp.md` "Quick setup" | S22 |

### 2.8 `mcp_servers` prompt section (SEC)

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| SEC-01 | The system prompt has a section named `mcp_servers` when at least one enabled server has `codemode` or `deferred` tools (from the config); otherwise the section is absent. | `ext/index.ts` `renderServersSection`, `before_agent_start` handler | S20 |
| SEC-02 | Line 1 is `MCP servers whose tools are not declared to you.` plus ` Call the tools of \`codemode\` servers from codemode scripts.` when a listed server is reached by codemode and ` Load the tools of \`tool_search\` servers with \`tool_search\`.` when one is reached by tool search. | `ext/index.ts` `serversSectionIntro` | S20 |
| SEC-03 | One line per server, sorted by name: `- mcp__<namespace> (<reach>)` and, when a summary exists, `: <summary>`. Reach is `codemode` when any configured exposure of the server is `codemode`, else `tool_search`. | `ext/index.ts` `renderServersSection` | S20 |
| SEC-04 | The summary is the first line of the configured `description`, else of the server instructions once connected, cut to 250 characters with a trailing `…`. | `ext/index.ts` `serverSummary`, `truncate` | S20 |
| SEC-05 | The section is at most 4096 characters. Descriptions shrink equally to fit; when the server lines alone do not fit, the last servers are dropped and the section ends with `- … <N> more server(s); find their tools with search_tools()` (singular for one). | `ext/index.ts` `renderServersSection`, `MAX_SERVERS_SECTION_CHARS` | S20 |
| SEC-06 | The section is rendered when a prompt starts, after the first-prompt wait. When it differs from the section the conversation already has, the new section is appended to the conversation as a system-prompt change; tool declarations do not change because of it. | `ext/index.ts` `before_agent_start` handler; `core/agent-session.ts` `_preparePromptAndToolLoadout` | S20 |
| SEC-07 | A server with only `direct` tools is not listed. A server is listed before it connects, without a summary unless it has a configured description. | `ext/index.ts` `hasIndirectTools` | S20 |

### 2.9 Exposure semantics and tool selection (EXP)

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| EXP-01 | Tools have an exposure: `direct` (declared while active, callable from scripts while active), `model-only` (declared while active, never callable from scripts), `codemode` (callable whenever registered, listed in the codemode description, not declared unless activated), `deferred` (as `codemode` but not listed), `hidden` (unreachable). | `docs/extensions.md` "Tool exposure"; `core/agent-session.ts` `_getCallableTools`, `_getToolExposure` | S01, S03 |
| EXP-02 | Registering a `direct` or `model-only` tool activates it; other exposures are not activated on registration. | `docs/extensions.md` "Tool exposure" | S03 |
| EXP-03 | The model cannot call a tool that is not declared to it: a direct call to a `codemode`-only tool is rejected as an unknown tool. | `test/suite/agent-session-mcp.test.ts` "rejects direct model calls to codemode-only MCP tools" | S03 |
| EXP-04 | Tools callable from scripts are the active `direct` tools and every registered `codemode` or `deferred` tool. This does not depend on the active set for the latter two, so they stay callable after `/tree`, resume and fork. | `core/agent-session.ts` `_getCallableTools` | S03 |
| EXP-05 | A `direct` MCP tool is declared to the model like a built-in tool once its server is connected, and is also callable from scripts. | `test/suite/agent-session-mcp.test.ts` "declares directly exposed MCP tools to the model" | S17 |
| EXP-06 | `--tools` with plain names keeps MCP tools registered unless one entry starts with `mcp__` (or the list is empty). An MCP tool the list keeps without naming or matching it is never declared directly, whatever its exposure, also when restored from a transcript or activated by an extension; it may be declared only when its exposure is not `direct` and `tool_search` is registered. | `core/agent-session.ts` `_isAllowedTool`, `_isActivatable`, `_allowlistFiltersMcp` | S10 |
| EXP-07 | Once a `--tools` entry starts with `mcp__`, the list filters MCP tools like other tools: `--tools read,bash,codemode,'mcp__radius__*'` keeps only the tools of `radius`. | `core/agent-session.ts` `_allowlistFiltersMcp`; `docs/cli.md` "MCP tools" | S10 |
| EXP-08 | `--tools` and `--exclude-tools` entries may be patterns where `*` matches any characters. `+name` and `-name` entries take exact names only. | `core/mcp-servers.ts` `createToolNameMatcher`; `docs/cli.md` "Tools" | S10 |
| EXP-09 | `--exclude-tools` removes matching MCP tools; `--exclude-tools 'mcp__*'` removes all server tools. `--no-tools` starts with MCP tools disabled as well. | `core/agent-session.ts` `_isAllowedTool`; `docs/cli.md` "Tools" | S10 |
| EXP-10 | The three resource tools count as MCP tools for these rules. | `core/mcp-servers.ts` `isMcpToolName` | S23 |
| EXP-11 | `--no-mcp` turns MCP off for one run: no `mcp.json` is read, no server connects, there are no MCP tools and no `/mcp` command. | `cli/args.ts` (`--no-mcp`); `main.ts` (`disabledBuiltinExtensions`) | S19 |
| EXP-12 | MCP can be turned off in settings with `"extensions": ["-builtin:mcp"]` (project value on top of the user value), shown and toggled in the config command's Built-in list. `--no-extensions` turns it off too, and `-e builtin:mcp` turns it on again for that run. | `docs/mcp.md` "Replace the built-in MCP support"; coding-agent `CHANGELOG.md` 0.99.0 "Built-in section in pi config" | S19 |

### 2.10 `tool_search` (TSR)

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| TSR-01 | `tool_search` is a tool with parameters `query` (string, `Search query for deferred tools.`) and optional `limit` (number, default 8), registered inactive; it is enabled with `defaultTools`, `--tools` or by MCP (SES-11). Scripts cannot call it. | `ts/tool.ts` `createToolSearchToolDefinition`, `toolSearchSchema`; `ts/index.ts` | S09 |
| TSR-02 | Its description is the fixed text of `TOOL_SEARCH_DESCRIPTION` and its prompt snippet `Search for tools that are not loaded yet and load the matches`. Neither lists servers or tools, so they do not change while servers connect. | `ts/tool.ts` `TOOL_SEARCH_DESCRIPTION` | S09 |
| TSR-03 | It ranks the registered tools with `codemode` or `deferred` exposure that are not active yet, and activates the matches so the next model request declares them. | `ts/tool.ts` `searchAndLoad`, `isSearchable` | S09 |
| TSR-04 | Result text: `Loaded <n> tool(s). They are available from your next call:` and one `- <name>: <first description line>` per tool, or `No matching tools found.` Details carry `loaded` names. | `ts/tool.ts` `createToolSearchToolDefinition` `execute` | S09 |
| TSR-05 | An empty query fails with `query must not be empty`; a limit that is not a positive integer fails with `limit must be a positive integer`. | `ts/tool.ts` `execute` | S09 |
| TSR-06 | Tokenization: split at camelCase boundaries and non-alphanumerics, lower-case, drop the 21 stop words, fold plurals (`ies` to `y`, `ches/shes/sses/xes/zes` lose `es`, a final `s` unless `ss`). | `ts/tool.ts` `tokenize`, `stem`, `STOP_WORDS` | S06 |
| TSR-07 | Ranking is Okapi BM25 with k1 1.2 and b 0.75 over unique query terms; only tools with a positive score are returned, best first, ties in registration order, at most `limit`. | `ts/tool.ts` `Bm25Ranker.rank` | S06 |
| TSR-08 | A tool's search text is its name, its name with `_` as spaces, its description, the descriptions and property names of its input schema (through `properties`, `items`, `anyOf`, `oneOf`, `allOf`) and its namespace name, description and instructions. A server `description` therefore ranks that server's tools. | `ts/tool.ts` `createToolSearchDocument`, `schemaText` | S06 |
| TSR-09 | Loaded tools are recorded in the transcript as a tool change and stay declared on that branch through `/tree`, resume and fork. | `ts/tool.ts` header; `docs/cli.md` "Tool search" | S09 |
| TSR-10 | After resume and after `/reload`, tools that `tool_search` loaded are declared again once their server has reconnected before the next prompt starts; a tool that registers after the next prompt started is not activated by the restore. | `core/agent-session.ts` `_pendingToolNames`; `test/suite/agent-session-mcp.test.ts` "MCP tools after resume and reload" | S22 |
| TSR-11 | When every matching tool is already declared, nothing is loaded and the result is `No matching tools found.` | `test/suite/agent-session-mcp.test.ts` "finds nothing to load when every matching tool is already declared" | S09 |

### 2.11 Codemode reaching other tools (CMD)

Python names replace Pi's JavaScript names (decision). `ALL_TOOLS` keeps its
name.

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| CMD-01 | Scripts can call every tool of EXP-04, including extension tools and MCP tools, except `codemode` itself and `model-only` tools. An inactive `direct` tool is not callable. | `cm/tool.ts` `getCodemodeCallableTools`; `core/agent-session.ts` `_getCallableTools`; test "does not let codemode call itself or inactive direct tools" | S03 |
| CMD-02 | A tool is a method of `tools` named by its identifier: characters that are not valid in an identifier become `_` (`mcp__dev-radius__search` is `tools.mcp__dev_radius__search`). The raw name is accepted as a key too. | `pcm/identifier.ts` `toCodemodeIdentifier`; `pcm/runtime/prelude-source.ts` | S07 |
| CMD-03 | A tool that declares an output schema resolves to its structured content, also when the result is an error that carries structured content. | `cm/execute.ts` `toScriptValue` | S04 |
| CMD-04 | Any other tool resolves to its text content as one string. A failed, blocked or invalid call raises `ToolError` with the tool's error text (`Tool "<name>" failed` when there is none). | `cm/execute.ts` `toScriptValue` | S04 |
| CMD-05 | An MCP tool call resolves to `{content, structuredContent?, isError?}`; a result with `isError` resolves and does not raise. | `ext/tools.ts` `convertMcpResult`; `docs/mcp.md` "Control tool exposure" | S16 |
| CMD-06 | `ALL_TOOLS` lists every callable tool as `{name, description}`, where `name` is the identifier and `description` is the tool's description, its prompt guidelines as bullets and its declaration. | `cm/execute.ts` (`samples`, `sandboxTools`); `cm/tool.ts` `toCodemodeDeclaration`; `pcm/declarations.ts` `renderToolSample` | S07 |
| CMD-07 | `search_tools(query, limit=8, namespace=None)` ranks the callable tools like TSR-06 to TSR-08 and returns `{name, description}` entries as in CMD-06. A non-string query, a limit that is not a positive integer and a non-string namespace raise with Pi's messages. | `cm/execute.ts` `createDiscoveryGlobals` (`searchTools`) | S07 |
| CMD-08 | `namespace` restricts the search to one namespace, matched by its name or an alias, as written or as the same identifier: `mcp__dev-radius`, `mcp__dev_radius`, `dev-radius` and `dev_radius` all match. | `cm/execute.ts` `isNamespaceName` | S07 |
| CMD-09 | `describe_tool(name)` returns the entry description of CMD-06 for a raw name or an identifier, or `None`. | `cm/execute.ts` `createDiscoveryGlobals` (`describeTool`) | S07 |
| CMD-10 | `describe_namespace(name)` returns `{name, description?, instructions?, tools}` with the identifiers of the namespace's callable tools, or `None`. Server instructions are available only this way and are part of no tool description. | `cm/execute.ts` `createDiscoveryGlobals` (`describeNamespace`) | S07 |
| CMD-11 | A declaration shows the call signature with the input type and the result type. Object properties are sorted by name and optional ones are marked; property descriptions appear as comment lines above the property. | `pcm/declarations.ts` `renderToolSignature`, `objectType` | S05 |
| CMD-12 | Constraints the type cannot express appear as tags on the property's comment line: `@integer`, `@minimum`, `@exclusiveMinimum`, `@maximum`, `@exclusiveMaximum`, `@multipleOf`, `@minLength`, `@maxLength`, `@pattern`, `@format`, `@minItems`, `@maxItems`, `@uniqueItems`, `@default <json>`, `@deprecated`. | `pcm/declarations.ts` `constraintTags`, `TAG_KEYWORDS` | S05 |
| CMD-13 | Constraints of an optional field written as a union with `null` are read from the non-null branch together with the property's own; for bounds the tightest value is shown. Draft-04 boolean `exclusiveMinimum`/`exclusiveMaximum` are read as exclusive bounds. A tag longer than 80 characters is left out, and line terminators in tags are escaped. | `pcm/declarations.ts` `constraintTags` | S05 |
| CMD-14 | Type rendering covers `const`, `enum`, `anyOf`/`oneOf` (union), `allOf` (intersection), type arrays, arrays, tuples, additional properties, and local `$ref`s (`#`, `#/$defs/...`, `#/definitions/...`); recursive, remote and more than 32 references render as unknown. | `pcm/declarations.ts` `toType`, `resolveRef`, `arrayType` | S05 |
| CMD-15 | An input type longer than 16000 characters is rendered again without constraint tags, and as unknown when still too long. | `pcm/declarations.ts` `schemaToType`, `DEFAULT_INPUT_SCHEMA_MAX_CHARS` | S05 |
| CMD-16 | The MCP result type is spelled out in the declaration (`content`, `structuredContent`, `isError`) with the server's output schema inline; no shared type is referenced that the script cannot see. | `ext/tools.ts` `createMcpResultSchema`; coding-agent `CHANGELOG.md` Unreleased | S16 |
| CMD-17 | Reading a tool that does not exist raises an error that names close matches. | `pcm/runtime/prelude-source.ts`; coding-agent `CHANGELOG.md` 0.99.x "Codemode errors now say how to recover" | S07 |
| CMD-18 | The codemode description explains the call contract (a call returns a string, or a structured value when the tool's declaration says so; a tool that declares a structured value returns it on failure too) and names the discovery helpers as the way to find unlisted tools such as MCP tools. | `cm/tool.ts` `DESCRIPTION_INTRO`, `describeGlobals` | S08 |
| CMD-19 | The description lists callable tools that are not `deferred` under `Nested tools:`, grouped by namespace (tools without one first, then namespaces by name) with a heading and the namespace description, each tool with its description, guidelines and declaration. | `cm/tool.ts` `createCodemodeDescription`, `renderToolSection` | S08 |
| CMD-20 | Listed declarations share a budget of estimated tokens (characters / 4), default 3000, setting `codemode.inlineBudget`. Groups take turns placing their cheapest remaining tool; a namespace with unlisted tools is marked `(some tools not listed)` or `(tools not listed)`. | `cm/tool.ts` `selectCatalog`, `DEFAULT_CODEMODE_INLINE_BUDGET` | S08 |
| CMD-21 | `deferred` tools, which include MCP tools with the default exposure, and namespace instructions never appear in the description, so it is byte-identical before and after MCP servers connect and after `tool_search` loads a tool. | `cm/tool.ts` `createCodemodeDescription`, `prepareCodemodeLoadout`; test "keeps the codemode description unchanged when the server connects" | S08 |
| CMD-22 | Setting `codemode.mode`: with `on` (default) declared tools that are also callable get a line `Codemode: \`tools.<id>(args)\` resolves to <a string or the field names of the result>.` appended to their description, and the codemode description lists only tools that are not `direct`. With `only` the codemode description lists every callable tool and requests leave out the declarations of active `direct` tools, which stay callable. | `cm/tool.ts` `prepareCodemodeLoadout`, `describeScriptCall`, `describeOutput` | S08 |
| CMD-23 | For a `direct` MCP tool the appended line names the result fields, for example `{ content, structuredContent?: { results }, isError? }`. | `cm/tool.ts` `describeFields`; test "tells scripts what directly exposed MCP tools resolve to" | S16 |
| CMD-24 | Nested calls pass argument validation and the `tool_call` and `tool_result` hooks exactly like direct calls; their events carry the parent tool call id, and their ids are `<parent id>/<n>`. | `docs/extensions.md` (`ctx.executeTool`); `docs/mcp.md` "Permissions" | S03 |
| CMD-25 | A `tool_result` hook that replaces a nested result's content also decides its structured content: the script receives the replaced structured content. | test "keeps structured content that tool_result handlers replace along with the content" (`agent-session-codemode.test.ts`) | S04 |
| CMD-26 | The parent result keeps the bounded nested-call record (name, arguments, status, duration, error; never results) for MCP and extension tools as for built-ins. | `docs/extensions.md` (`nestedCalls`) | S03 |
| CMD-27 | The codemode system prompt contribution stays one snippet and one guideline line; MCP adds nothing to it. | `cm/tool.ts` `codemodeToolSystemPromptContribution` | S08 |
| CMD-28 | `image(value)` adds an image to the script's output. It accepts an image block `{type: "image", data, mimeType}` as MCP tools return it, a base64 `data:` URL, and an `{image_url}` object. Remote URLs, malformed base64 and types other than PNG, JPEG, GIF and WebP are rejected with an error in the script, and no invalid image reaches the result. | `docs/codemode.md` "Globals"; `pcm/runtime/prelude-source.ts` (`image`) | S39 |
| CMD-29 | Images reach the model as images, in output order after the text rules of the result. Each image is also saved to a temp file readable only by the user, and a text item `[Image saved to <path> (<mime>, <size>)]` precedes it; an image shown more than once is saved once; a failed save gives `[Image (<mime>, <size>) could not be saved: <reason>]` and keeps the image. | `cm/execute.ts` `saveImages`, `formatOutput`, `IMAGE_EXTENSIONS` | S39 |
| CMD-30 | When the text output is truncated, images follow the truncated text block and are never cut. | `cm/execute.ts` `truncateOutput` | S39 |

### 2.12 Resources (RES)

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| RES-01 | Once an enabled server whose exposure is not `hidden` has connected and declared the `resources` capability, the tools `list_mcp_resources`, `list_mcp_resource_templates` and `read_mcp_resource` exist. The server keeps counting while it is disconnected (a resource call reconnects it, CON-06); the tools become unreachable only when no enabled, non-hidden server with the capability remains (disabled, removed, exposure set to `hidden`). | `ext/index.ts` `syncResourceTools`, `serversWithResources`; `ext/runtime.ts` `handleClientClose` (keeps `hasResources`) | S23 |
| RES-02 | Their exposure is the widest server exposure among those servers: `direct`, else `codemode`, else `deferred`. When it stops being `direct` the tools leave the declared set. | `ext/index.ts` `syncResourceTools` | S23 |
| RES-03 | Their names, descriptions, parameter descriptions and output schemas are Pi's (the Codex and OpenCode tool shapes), and they are annotated read-only. | `ext/resources.ts` `createMcpResourceToolDefinitions`, `LIST_PARAMETERS`, `READ_PARAMETERS` | S23 |
| RES-04 | `list_mcp_resources` with `server` lists one page and returns `{server, resources, nextCursor?}`; `cursor` continues. Without `server` it lists every page of every server, servers in name order, and returns `{resources, errors?}`, where `errors` names servers that could not be listed. `cursor` without `server` fails with `cursor can only be used when a server is specified`. | `ext/resources.ts` `list` | S23 |
| RES-05 | Each listed item is the server's item without `_meta` and `icons`, with a `server` field. Items for MCP Apps (`ui://` URIs or a `profile=mcp-app` MIME parameter) are left out. | `ext/resources.ts` `listed`, `isMcpAppResource` | S23 |
| RES-06 | `list_mcp_resource_templates` behaves the same with `resourceTemplates`; a server that does not implement the method has none. | `ext/resources.ts`; `ext/runtime.ts` `withoutTemplates` | S23 |
| RES-07 | The model receives a listing as one JSON text, under the 20 KB rule of TLS-15; scripts receive the object. | `ext/resources.ts` `jsonResult` | S23 |
| RES-08 | `read_mcp_resource` needs `server` and `uri` (`server must be provided`, `uri must be provided`; non-strings give `<key> must be a string`). An unknown server fails with `MCP server "<n>" has no resources. Servers with resources: <list>`. | `ext/resources.ts` `readResource`, `findServer`, `stringArgument` | S23 |
| RES-09 | Read content reaches the model as in TLS-11 and TLS-13: text as text, images as images, other binary content saved to a temp file with its path. Several contents are each preceded by `<uri>:`. An empty result gives `Resource <uri> is empty.` Scripts receive `{server, uri, contents}` without `_meta`. | `ext/resources.ts` `readResource` | S23, S39 |
| RES-10 | At connect, the resource and template counts are fetched for display; a server whose listing fails still connects. `notifications/resources/list_changed` refreshes them. | `ext/runtime.ts` `fetchResources`, `refreshResources` | S23 |

### 2.13 OAuth (OAU)

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| OAU-01 | A connection never opens a browser. It sends the stored access token; after a 401 it tries the stored refresh token; when that is not possible the server needs sign-in. | `ext/oauth.ts` `createMcpAuthProvider` | S30 |
| OAU-02 | Protected resource metadata (RFC 9728) is fetched from the challenge's `resource_metadata` URL, else from `/.well-known/oauth-protected-resource<path>` and then the root form. Requests carry `MCP-Protocol-Version`. A network failure fails the flow; any other failure means no metadata. | `mcp/oauth/discovery.ts` `discoverProtectedResourceMetadata`, `discoverOAuthServerInfo` | S27 |
| OAU-03 | The authorization server is the first `authorization_servers` entry, else the server's origin. Its metadata is looked up at `/.well-known/oauth-authorization-server<path>`, `/.well-known/openid-configuration<path>`, then `<path>/.well-known/openid-configuration`; 4xx and 502 try the next URL; another failure status is an error; none found means no metadata (default endpoints `/authorize`, `/token`, `/register`). | `mcp/oauth/discovery.ts` `buildAuthorizationServerDiscoveryUrls`, `discoverAuthorizationServerMetadata` | S27 |
| OAU-04 | Discovered metadata must name the expected issuer (ignoring one trailing slash); otherwise the flow fails with `OAuth issuer mismatch: expected "<e>", received "<r>"`. | `mcp/oauth/discovery.ts` `discoverAuthorizationServerMetadata`; `mcp/oauth/errors.ts` `OAuthIssuerMismatchError` | S27 |
| OAU-05 | `WWW-Authenticate` is parsed for the `Bearer` and `DPoP` schemes: `resource_metadata`, `scope`, `error`, `error_description`; empty values count as absent. | `mcp/oauth/discovery.ts` `parseWwwAuthenticate` | S27 |
| OAU-06 | When protected resource metadata exists, its `resource` must have the server's origin and be a path prefix of the server URL, else the flow fails; the value is sent as `resource` in the authorization and token requests. | `mcp/oauth/discovery.ts` `selectResource`; `mcp/oauth/flow.ts` `startAuthorization`, `tokenRequest` | S27, S28 |
| OAU-07 | Requested scope: the explicit scope (configured `oauth.scope` plus the challenge's), else the resource metadata's `scopes_supported`, else none. An empty value falls through to the next source. | `mcp/oauth/flow.ts` `runFlow`; `ext/oauth.ts` `signInMcpServer`, `mergeScopes` | S28, S31 |
| OAU-08 | Dynamic client registration posts `client_name` (default `pipy`), `redirect_uris`, `grant_types` `authorization_code` and `refresh_token`, `response_types` `code`, `token_endpoint_auth_method` (`none`, or `client_secret_post` with a secret), `application_type` `native` for loopback redirect URIs, and the scope. A server whose metadata has no registration endpoint fails with `Authorization server does not support dynamic client registration`; a failed registration reports status and body. | `mcp/oauth/flow.ts` `registerClient`, `applicationType`; `mcp/oauth/provider.ts` `McpOAuthProvider` | S28 |
| OAU-09 | The authorization URL carries `response_type=code`, `client_id`, a PKCE S256 challenge, `redirect_uri`, `state`, `scope`, `resource`, and `prompt=consent` when the scope includes `offline_access`. A server that does not support the code response type or S256 fails with a clear error. | `mcp/oauth/flow.ts` `startAuthorization`, `pkce` | S28 |
| OAU-10 | Tokens are requested only from an `https` or loopback endpoint (`Refusing to send OAuth credentials to non-HTTPS endpoint <url>`). Client authentication follows the registered method, else the server's supported methods: basic, post, or none. | `mcp/oauth/flow.ts` `secureEndpoint`, `selectClientAuthMethod`, `applyClientAuthentication` | S28 |
| OAU-11 | A token response with an `error` field is an OAuth error whatever the status; another non-success status is `server_error`. `expires_in`, `scope`, `refresh_token` that are empty or `null` count as absent. | `mcp/oauth/flow.ts` `tokenRequest`; `mcp/oauth/types.ts` `parseOAuthTokens` | S28 |
| OAU-12 | A code is exchanged only when the response's `iss` matches the authorization server's issuer: when `iss` is present, or the server advertises `authorization_response_iss_parameter_supported`, a mismatch (or missing `iss`, or missing metadata) rejects the code before any exchange (RFC 9207). | `mcp/oauth/flow.ts` `runFlow` | S28 |
| OAU-13 | A token response without `scope` records the requested scope; a refresh without `scope` keeps the grant's scope, and a refresh without a new refresh token keeps the old one. | `mcp/oauth/flow.ts` `withScope`, `refreshAuthorization` | S28 |
| OAU-14 | A refresh that fails with `invalid_grant` drops the tokens, and with `invalid_client` or `unauthorized_client` drops client and tokens; the flow then runs once more. `server_error` falls through to a new authorization; other OAuth errors and a cancelled flow are reported. | `mcp/oauth/flow.ts` `authorizeMcp`, `runFlow` | S28 |
| OAU-15 | The `state` parameter is 32 random bytes in hex, new for every sign-in. Stored state for another server URL is never used. | `mcp/oauth/provider.ts` `state`, `own`; `ext/oauth.ts` `signInMcpServer` | S28, S31 |
| OAU-16 | The callback server listens on `127.0.0.1` at a free or configured port, path `/callback`. Another path gives 404; an unknown or missing `state` 400 `Invalid or expired OAuth state`; an `error` parameter fails the sign-in with its description; a missing code gives 400; a response on a path other than the expected one fails with `The authorization response arrived on another redirect URI`. It gives up after 5 minutes. | `mcp/oauth/callback.ts` `OAuthCallbackServer` | S29 |
| OAU-17 | The browser page after the redirect is HTML: success says `Signed in to the MCP server. You may now close this page.`; failure shows the message and details. | `ext/oauth.ts` `listenForCallback` (`renderPage`) | S29 |
| OAU-18 | Credentials are stored in `<config home>/mcp-auth.json`, readable only by the user, keyed by server namespace and URL. Servers with one URL under different names sign in separately; servers with the same name and URL in different `mcp.json` files share a sign-in. Signing out deletes the entry. | `ext/oauth.ts` `McpOAuthCredentialStore`, `storeKeys` | S30 |
| OAU-19 | An access token within 30 s of expiry is refreshed before a request when a refresh token exists; a failed refresh sends the old token and lets the server decide. | `ext/oauth.ts` `createMcpAuthProvider` (`token`), `REFRESH_SKEW_MS` | S30 |
| OAU-20 | Requests in one process share one refresh. Other processes are kept out by a lock per server held from reading the tokens to saving the new ones; a lock whose holder died is taken over after 20 s, and a waiter gives up after 25 s. Tokens that changed while waiting are used without refreshing. | `ext/oauth.ts` `withRefreshLock`, `createMcpAuthProvider` (`refresh`) | S30 |
| OAU-21 | A challenge with `insufficient_scope` never refreshes; it needs a new sign-in, which requests the configured scope, the scopes granted so far and the challenged scopes together, so the new token keeps earlier access. | `ext/oauth.ts` `onUnauthorized`, `signInMcpServer`; `mcp/oauth/flow.ts` `stepUpScope` | S31 |
| OAU-22 | Every request to the authorization server is limited to 15 s. | `ext/oauth.ts` `timedFetch`, `OAUTH_REQUEST_TIMEOUT_MS` | S30 |
| OAU-23 | Closing a connection waits for a running refresh to save its tokens, and does not start a refresh for a token that is about to expire. | `ext/runtime.ts` `close`; `ext/oauth.ts` `settled`; `mcp/transports/streamable-http.ts` `close` | S30 |
| OAU-24 | Sign-in first tries the stored refresh token (no browser) unless the server asked for more scope. | `ext/oauth.ts` `signInMcpServer` (`skipRefresh`) | S31 |
| OAU-25 | Sign-in listens on the configured port, else the port of the registered redirect URI, else a free port; when that port is taken and was not configured, a free port is used. A stored client registered for another redirect URI is discarded with its tokens (unless `clientId` is configured). | `ext/oauth.ts` `signInMcpServer`, `listenForCallback` | S31 |
| OAU-26 | The authorization URL is shown and opened in the browser. The sign-in completes on the browser callback or on a pasted redirect URL, whichever comes first. | `ext/oauth.ts` `waitForAuthorizationResponse` | S31 |
| OAU-27 | A pasted URL must be a full URL (`Expected the full redirect URL from the browser address bar`), match the redirect URI's origin and path (`The redirect URL does not match this sign-in's redirect URI`), carry this sign-in's `state` (`The redirect URL belongs to a different sign-in`) and a code (`The redirect URL does not contain an authorization code`); an `error` parameter fails with its description. An empty paste cancels. | `ext/oauth.ts` `responseFromRedirectUrl` | S31 |
| OAU-28 | A sign-in can be cancelled at every step, including while a request to the authorization server is pending, and ends as `Sign-in cancelled.`; ending the session cancels it. | `ext/oauth.ts` `signInMcpServer`; `ext/index.ts` `signIn` | S31 |
| OAU-29 | After a successful sign-in the server reconnects; a reconnect failure reports `Signed in, but <error>`. A sign-in failure reports `Sign-in failed: <error>`. A server that does not use OAuth reports `MCP server "<n>" does not use OAuth.` | `ext/index.ts` `signIn` | S31 |
| OAU-30 | `oauth.clientId` (and `clientSecret`) use a pre-registered client instead of registering. The secret is resolved only when a token request needs it, so a secret that fails to resolve fails the refresh, not the connection. | `ext/runtime.ts` `oauthSettings`; `ext/oauth.ts` `createProvider`; test "resolves the OAuth client secret lazily" | S32 |
| OAU-31 | `oauth.callbackPort` fixes the port and makes the redirect URI `http://127.0.0.1:<port>/callback`. `oauth.callbackUrl` is sent exactly as written when it has a port; without one the listening port (`callbackPort` or a free port) is added. `localhost` is served on `127.0.0.1`. | `ext/oauth.ts` `callbackSettings` | S32 |
| OAU-32 | `oauth.clientName` is the `client_name` sent at registration; it applies only when a client is registered, so changing it needs a sign-out first. | `ext/oauth.ts` `createProvider`; `docs/mcp.md` | S32 |
| OAU-33 | `oauth.clientRegistration: "cimd"` identifies with a Client ID Metadata Document URL instead of registering. The authorization server must advertise `client_id_metadata_document_supported` and the `none` token auth method, else sign-in fails with `The authorization server does not support Client ID Metadata Documents for public clients; remove oauth.clientRegistration "cimd"`. | `ext/oauth.ts` `clientMetadataDocument` | S32 |
| OAU-34 | With CIMD, when the server sends `iss` in responses the client ID is `<base>/client.json` with redirect `http://127.0.0.1:<port>/callback`; otherwise it is `<base>/<id>/client.json` with redirect path `/callback/<id>`, where `<id>` is the URL-safe base64 of the first 9 bytes of SHA-256 over the server URL without fragment, and the response is accepted only on that path. Switching to CIMD replaces a stored registered client. | `ext/oauth.ts` `clientMetadataDocument`, `callbackId`, `signInMcpServer` | S32 |
| OAU-35 | `oauth.authServerMetadataUrl` replaces discovery of the authorization server: the document is used as configured, its issuer is not checked against discovery, and nothing about it is cached, so a changed URL applies at once. | `mcp/oauth/discovery.ts` `discoverOAuthServerInfo`; `mcp/oauth/flow.ts` `runFlow` | S32 |
| OAU-36 | `"auth": {"provider": "<p>"}` sends the current `/login` token of that provider as the bearer token, read on every request so provider refreshes apply; MCP stores no copy and never runs MCP OAuth for that server. | `ext/runtime.ts` `McpServerConnection` constructor (`providerToken`) | S33 |

### 2.14 `pipy mcp` shell commands (CMDL)

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| CMDL-01 | `pipy mcp`, `pipy mcp help`, and any `pipy mcp` invocation with `--help` or `-h` print the usage text of `HELP` and exit 0. An unknown subcommand prints `Unknown mcp command "<c>".` with a hint and exits 1. | `ext/cli.ts` `runMcpCommand`, `HELP` | S20a |
| CMDL-02 | The commands work without a session and do not load extensions; they see only file-configured servers. | `ext/cli.ts` header; `docs/mcp.md` | S20a |
| CMDL-03 | `mcp add <server> [options] -- <command> [args...]` and `mcp add <server> [options] --url <url>` write the entry. After the server name and the first command word, all remaining arguments belong to the command, so the command's own options pass through without `--`. Exactly one of a command and `--url` is required. | `ext/cli.ts` `add`, `parseOptions` (`maxPositionals`) | S20a |
| CMDL-04 | `add` options: `--env KEY=VALUE` (repeatable) and `--cwd` for stdio; `--header KEY=VALUE` (repeatable), `--bearer-token-env-var NAME` (writes `Authorization: Bearer ${NAME}`), `--oauth-client-id`, `--oauth-client-secret`, `--oauth-callback-port`, `--oauth-client-name` for HTTP; `--exposure`, `--description` for both. An option for the other transport fails with `--<opt> only applies to ...`; a malformed pair with `--<opt> expects KEY=VALUE, got "<pair>".`; unknown options and missing values fail. | `ext/cli.ts` `add`, `parsePairs`, `parseOptions` | S20a |
| CMDL-05 | `add` validates the entry with the config rules before writing and writes nothing when it is invalid. | `ext/cli.ts` `add` | S20a |
| CMDL-06 | `add` and `remove` change the user-level file by default and `.pipy/mcp.json` in the current directory with `--local` or `-l`. `add` prints `Added` or `Replaced <scope> MCP server "<n>" in <path>.`, a note when the project is not trusted, and `Check it with: pipy mcp list`, extended by the login hint for HTTP servers without an `Authorization` header. `add` does not connect. | `ext/cli.ts` `add` | S20a |
| CMDL-07 | `mcp remove <server> [-l]` prints `Removed <scope> MCP server "<n>" from <path>.`; when the entry is not in that file it fails and says in which file the server is defined and whether to add or omit `--local`. Stored OAuth credentials are kept. | `ext/cli.ts` `remove` | S20a |
| CMDL-08 | `mcp list` connects to every enabled server and prints, per server, `<name>: <state> (<exposure>, <scope>)` (`connected, <n> tool(s)`, `needs sign-in`, or the state), the transport line, `project override: <path>`, the sign-in hint, `tools: ...` with `[<exposure>]` after tools whose exposure differs from the server's, `resources: <n>, URI templates: <m>` and the error; then `config error:` lines and the note about an ignored project file. | `ext/cli.ts` `list` | S20a (stdio, without the resources line), S23 (resources line), S26 (HTTP entries) |
| CMDL-09 | `mcp list --json` prints `{servers, errors, note?}` with, per server, `name`, `scope`, `source`, `override?`, `enabled`, `exposure`, `transport`, `state`, `tools`, `toolExposure?`, `resources?`, `resourceTemplates?`, `error?`. | `ext/cli.ts` `list`, `ServerReport` | S20a (without the two resource fields), S23 (resource fields), S26 (HTTP entries) |
| CMDL-10 | `mcp list` exits 1 when a config entry is invalid or an enabled server is not connected, else 0. With no servers and no errors it prints `No MCP servers configured. Add them to <path> or .pipy/mcp.json.` | `ext/cli.ts` `list` | S20a |
| CMDL-11 | `list`, `login` and `logout` read the project file only when the project is already trusted; otherwise a project file that exists is reported as ignored. | `ext/cli.ts` `runMcpCommand` (`untrustedNote`) | S20a |
| CMDL-12 | `mcp login <server> [--timeout <seconds>]`: an unknown server and a server without OAuth fail with Pi's messages; a server that already connects prints `Already signed in ...`; a server that fails for another reason prints the connection error. | `ext/cli.ts` `runMcpCommand`, `login` | S31 |
| CMDL-13 | `mcp login` prints the authorization URL, opens the browser and waits; on a terminal it also accepts the pasted redirect URL. `--timeout` (default 300, must be positive) limits the whole sign-in, including requests to the authorization server; on expiry it reports that the sign-in was cancelled or not completed within that time. Success prints `Signed in to MCP server "<n>" (<k> tools).` | `ext/cli.ts` `login`, `waitForRedirectUrl` | S31 |
| CMDL-14 | `mcp logout <server>` deletes the stored credentials and prints `Signed out of MCP server "<n>".` or `No stored credentials for MCP server "<n>".` | `ext/cli.ts` `runMcpCommand` | S31 |

### 2.15 `/mcp` in a session (SLH)

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| SLH-01 | `/mcp` is a built-in command described as `Manage MCP servers: sign in, reconnect, enable or disable, and change exposure`. Arguments complete `login`, `logout`, `reconnect`, and then the names of eligible servers with their state. | `ext/index.ts` `registerCommand("mcp")`, `getArgumentCompletions` | S21 |
| SLH-02 | Outside the interactive TUI, `/mcp` waits for the startup connections and prints one line per server: `<name>: <state>[, <n> tools] (<exposure>)`, with `needs sign-in, run /mcp login <name>`, `disconnected, reconnects on next call`, `disabled`, `starting`, an indented error, then `config error:` and `overridden:` lines; or `No MCP servers configured. ...` | `ext/index.ts` `formatStatus` | S21 |
| SLH-03 | `/mcp reconnect [server]` reconnects and reports `Reconnected to MCP server "<n>" (<state>).` or the error. | `ext/index.ts` handler (`reconnect`) | S21 |
| SLH-04 | `/mcp login [server]` and `/mcp logout [server]` sign in and out; login needs a UI (`Signing in to MCP server "<n>" requires interactive mode.`). Without the TUI, login shows the URL as a notice and asks for the pasted redirect URL through the input prompt. | `ext/index.ts` `loginCommand`, handler | S31 |
| SLH-05 | When the server name is omitted: one eligible server is used; else the one preferred server (needing sign-in for login and logout, failed or disconnected for reconnect); else a selection is shown. An unknown name gives `No MCP server named "<n>".`; none eligible gives the specific message. | `ext/index.ts` `pickServer`, `oauthPick` | S21 |
| SLH-06 | More than two arguments or an unknown action prints `Usage: /mcp, /mcp login [server], /mcp logout [server], /mcp reconnect [server]`. | `ext/index.ts` `MCP_USAGE` | S21 |
| SLH-07 | A prompt of print, JSON or RPC mode (`session.prompt`) whose text starts with `/` and whose name, the text between the slash and the first space, is a command an extension registered (`/mcp` is one) runs that command with the rest as its arguments. No run starts and no session event is emitted. A handler that throws is reported as an extension error (`Extension error (command:<name>): <message>` on stderr in print and JSON mode, an `extension_error` object in RPC mode) and the prompt still counts as handled. The text is not trimmed first. | `core/agent-session.ts` `prompt`, `_tryExecuteExtensionCommand`; `modes/print-mode.ts`; `modes/rpc/rpc-mode.ts` | X1 |
| SLH-08 | Nothing else is a command there. The built-in commands of the interactive mode (`/model`, `/compact`, `/quit` and the rest), an unknown `/name` and a `!` line are handled only by the interactive editor; as a prompt their text goes to the model unchanged. | `core/agent-session.ts` `prompt`; `modes/interactive/interactive-mode.ts` | X1 |
| SLH-09 | After the extension commands the `input` handlers run on the text, then `/skill:<name> [args]` becomes the skill block and `/<template> [args]` the expanded prompt template. An unknown skill or template name leaves the text unchanged. | `core/agent-session.ts` `prompt`, `_expandSkillCommand`; `core/prompt-templates.ts` `expandPromptTemplate` | X1 |
| SLH-10 | What a handled prompt looks like from outside. Print mode prints nothing and exits 0. JSON mode writes the session header and no event, also no `agent_settled`. The RPC `prompt` response carries `data.disposition`: `handled` (sent when the handler returned), `started` or `queued`; `steer` and `follow_up` answer `queued` or `handled`. | `modes/print-mode.ts`; `modes/rpc/rpc-mode.ts` `prompt`; `modes/rpc/rpc-types.ts` | X1 |
| SLH-11 | The UI a command has depends on the mode. Print and JSON mode have none: `ctx.hasUI` is false and `ctx.ui.notify` does nothing, so `/mcp` and its subcommands run there and show nothing. In RPC mode `ctx.hasUI` is true and `notify`, `select` and `input` are `extension_ui_request` objects on stdout (`method`, `id`, and `message` with `notifyType`, or `title` with `options` or `placeholder`); the client answers `select` and `input` with an `extension_ui_response` of the same `id`. `/mcp` there sends its status as a `notify` of type `info`. | `core/extensions/runner.ts` `noOpUIContext`, `hasUI`; `modes/rpc/rpc-mode.ts` `createExtensionUIContext` | X1 |
| SLH-12 | `/mcp login` in RPC mode sends the authorization URL as a `notify`, opens the browser, and asks for the pasted redirect URL with an `input` request (`Waiting for sign-in to "<n>". If the browser cannot reach this machine, paste the URL it was redirected to.`, placeholder `http://127.0.0.1:.../callback?code=...`) that ends unanswered when the browser arrives first. | `ext/index.ts` `loginCommand`; `modes/rpc/rpc-mode.ts` `createDialogPromise` | X1 |
| SLH-13 | Queued input. `steer` and `follow_up` whose text names an extension command fail with `Extension command "/<name>" cannot be queued. Use prompt() or execute the command when not streaming.`; they expand skills and templates when the message is queued. A `prompt` sent during a run runs an extension command at once; any other text is queued by its `streamingBehavior`, expanded. A message an extension sends (`sendUserMessage`) is never dispatched or expanded. | `core/agent-session.ts` `_queueUserInput`, `_throwIfExtensionCommand`, `prompt`, `sendUserMessage` | X1 |

### 2.16 Interactive `/mcp` manager (MGR)

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| MGR-01 | In the TUI, `/mcp` opens a manager that lists every configured server, including disabled ones, with `<state> · <exposure> · <source>` (`global`, `project`, `extension`, or `global, project override`). Servers needing attention come first: needs sign-in, failed, disconnected, starting, connected, disabled; then by name. | `ext/index.ts` `serversMenu`, `attentionRank`, `describeState` | S34 |
| MGR-02 | The manager opens at once, without waiting for servers, and updates while servers connect, keeping the selected item. Config errors and overridden registrations are shown above the list. With no servers it says where to add them. | `ext/index.ts` `manage`, `subscribe`; `ext/ui.ts` `McpManagerView.menu` | S34 |
| MGR-03 | Selecting a server shows its transport (URL or command line), its source, a project override path, its state, the last action's error, and for a server that is not connected its full error with the stderr tail. | `ext/index.ts` `serverMenu` | S34 |
| MGR-04 | The actions offered depend on the state: `Sign in` (needs sign-in), `Tools` (connected), `Reconnect` (failed, disconnected, connected, needs sign-in), `Sign out` (connected OAuth server), `Exposure`, `Disable`; a disabled server offers `Enable`. Each says where the change is saved. | `ext/index.ts` `serverMenu` | S34 |
| MGR-05 | For a user-level server without an override in a trusted project, `Enable in this project` and `Disable in this project` write a project override; later changes to that server go to the override. | `ext/index.ts` `serverMenu`, `setEnabled` (`inProject`), `saveConfig` | S34 |
| MGR-06 | `Tools` lists the server's tools with the first line of their description and `[<exposure>]` for tools whose exposure differs from the server's, and explains the server's exposure. | `ext/index.ts` `showTools`, `EXPOSURE_DESCRIPTIONS` | S34 |
| MGR-07 | `Exposure` offers `codemode`, `deferred` and `direct` with their explanations, marks the current one and saves the choice (SES-15, SES-16). | `ext/index.ts` `chooseExposure` | S34 |
| MGR-08 | Enabling, disabling and reconnecting run in the background: the manager stays usable and shows the changing state. | `ext/index.ts` `runInBackground` | S34 |
| MGR-09 | The sign-in screen shows a status line while contacting the authorization server, then the authorization URL as a clickable link with an input for the pasted redirect URL. Confirm submits a non-empty paste, the copy key copies the URL, and cancel stops the sign-in at every step. | `ext/index.ts` `signInWithUi`; `ext/ui.ts` `McpManagerView.status`, `redirectUrl` | S34 |
| MGR-10 | Each screen shows key hints for confirm and cancel with the action they perform. | `ext/ui.ts` `McpManagerView.menu`, `frame` | S34 |

### 2.17 Rendering (RND)

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| RND-01 | An MCP tool call is titled `<server>/<tool>`, with its arguments as `key=value` pairs on the title line when collapsed and one `key: value` line per argument when expanded. | `ext/tools.ts` `createMcpToolRenderers` (`renderCall`); `core/tools/render-utils.ts` `formatToolCallWithArgs` | S35 |
| RND-02 | A collapsed result shows at most 5 wrapped lines, counted after wrapping to the terminal width, with a hint for the rest and `Full output: <path>` when the output was truncated; expanded shows everything. Errors use the error colour. | `ext/tools.ts` `createMcpToolRenderers` (`renderResult`) | S35 |
| RND-03 | Calls to `mcp__<server>__<tool>` in a resumed session render the same way before their server connected, and when it never does; the HTML export renders them the same way. | `ext/index.ts` `registerToolRenderer` call; coding-agent `CHANGELOG.md` 0.99.x | S35 |
| RND-04 | Nested MCP calls appear as rows inside the codemode parent row like built-in nested calls. | `cm/renderer.ts` | S35 |

### 2.18 Extension API (EXT)

Python names follow pipy's extension API conventions (`snake_case`, with the
Pi-shaped camelCase alias where the API already offers both).

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| EXT-01 | An extension tool can declare `exposure`, `namespace` (`name`, `aliases`, `description`, `instructions`), `annotations` and `output_schema`. | `core/extensions/types.ts` `ToolDefinition`; `docs/extensions.md` "Tool exposure" | S02 |
| EXT-02 | An extension tool result can carry `structured_content` and `is_error`: the model sees `content` (as an error when `is_error`), scripts receive the structured content when the tool declares an output schema. | `docs/extensions.md` (outputSchema paragraph) | S02, S04 |
| EXT-03 | `get_all_tools()` reports each tool's `exposure`, `namespace`, `annotations` and whether it declares `reaches_tools`, for MCP tools as well. A permission extension can decide by the annotations. | `docs/extensions.md` "Tool exposure"; `docs/mcp.md` "Permissions" | S02, S17 |
| EXT-04 | Re-registering a tool with exposure `hidden` withdraws it. | `docs/extensions.md` "Tool exposure" | S02 |
| EXT-05 | A tool can declare `reaches_tools(input)` returning `"all"` or the words that may name the tools it reaches. `codemode` and `tool_search` declare it. | `core/extensions/types.ts` `reachesTools`; `cm/tool.ts`; `ts/tool.ts` | S02, S18 |
| EXT-06 | `register_mcp_server(name, config)` adds a server for the session with the `mcpServers` entry shape. Servers registered while the extension loads connect at session start; servers registered later connect at once. | `core/extensions/loader.ts` `registerMcpServer`; `ext/index.ts` `mcp_servers_change` handler | S36 |
| EXT-07 | Registration errors raise: an invalid name or config (`Invalid MCP server registered by extension "<path>": <reason>`), a name another extension registered, and a name that conflicts in `-`/`_` with a registered server. The same extension registering a name again replaces its registration and reconnects when the config changed. | `core/extensions/loader.ts` `registerMcpServer` | S36 |
| EXT-08 | `unregister_mcp_server(name)` closes the connection and makes the tools unreachable; it only affects the caller's own registration. `get_mcp_servers()` returns copies of the registrations in order. | `core/extensions/loader.ts`; `core/mcp-servers.ts` `McpServerRegistry` | S36 |
| EXT-09 | An `mcp.json` server takes precedence over a registered server of the same namespace; `/mcp` and the status output list the overridden registration. Registered servers appear with the extension as their source, and enable/exposure changes to them last for the session. | `ext/index.ts` `registeredServers`, `saveConfig` | S36 |
| EXT-10 | Extensions receive an `mcp_servers_change` event with the registrations after every change. When no handler exists and built-in MCP is off, each registration is reported once as an extension error: `MCP server "<n>" is registered, but no loaded extension connects MCP servers; ...`. | `core/extensions/runner.ts` `reportUnhandledMcpServers`; `core/extensions/types.ts` `McpServersChangeEvent` | S36 |
| EXT-11 | An extension tool handler can run other tools with `ctx.execute_tool(name, args)` and list them with `ctx.tools`; the nested calls behave as in CMD-24 and CMD-26. | `docs/extensions.md` (`ctx.executeTool`) | S37 |
| EXT-12 | A tool can adjust what the model sees while it is active with `prepare_loadout(loadout)`: replacement descriptions for declared tools and declarations to hide. | `docs/extensions.md` (`prepareLoadout`); `core/extensions/types.ts` `ToolLoadout` | S37 |
| EXT-13 | An extension that registers a tool named `codemode` or `tool_search` replaces the built-in tool of that name. MCP activates the replacement, and waits for servers before its calls, only when it declares `reaches_tools`. | `docs/mcp.md` "Replace the built-in MCP support"; `ext/index.ts` `ensureDiscoveryActive` (`hasReachingTool`) | S37 |
| EXT-14 | An extension that registers the `/mcp` command replaces built-in MCP for sessions: `mcp.json` is not read, no server connects, and `/mcp` belongs to the extension. The `pipy mcp` shell commands keep using the built-in implementation. | `docs/mcp.md` "Replace the built-in MCP support"; `extensions/index.ts` (`replaceable`) | S37 |
| EXT-15 | `register_tool_renderer(resolver)` chooses renderers for calls to any tool name, including tools that are not registered. | `docs/extensions.md` "Tool rendering" | S35 |
| EXT-16 | A `before_agent_start` handler can read and change the named system prompt sections of the coming request. | `core/extensions/types.ts` `BeforeAgentStartEvent.systemPromptOptions` | S20 |

### 2.19 SDK (SDK)

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| SDK-01 | An SDK session gets `codemode`, `tool_search` and MCP only when the embedder asks for them; nothing connects otherwise. | `docs/sdk.md` "Codemode and MCP"; `examples/sdk/14-codemode-mcp.ts` | S38 |
| SDK-02 | The MCP option accepts: a config loader, a transport factory, a credential store, the log path, the URL opener, the config updater and the first-prompt wait. Defaults are the CLI's. | `ext/index.ts` `McpExtensionOptions` | S38 |
| SDK-03 | The codemode option accepts the mode and the inline budget, overriding the settings. | `cm/index.ts` `CodemodeExtensionOptions` | S38 |
| SDK-04 | A documented SDK example enables all three and runs a prompt. | `examples/sdk/14-codemode-mcp.ts` | S38 |

### 2.20 Documentation (DOC)

| ID | Behaviour | Pi source | Slice |
| --- | --- | --- | --- |
| DOC-01 | A user page covers quick setup, configuration and its rules, inspecting and diagnosing servers, migrating from other clients, OAuth with every option, exposure, resources, permissions, and extensions and SDK. | `docs/mcp.md` | each slice; S40 |
| DOC-02 | The CLI reference documents `--no-mcp`, tool patterns, the MCP rules of `--tools`, `tool_search`, and the `mcp` commands. | `docs/cli.md` | S10, S19, S20a |
| DOC-03 | The codemode page documents the discovery helpers, result shapes, listing rules and settings. | `docs/codemode.md` | S04, S07, S08 |
| DOC-04 | The extension API reference documents exposure, namespaces, annotations, output schemas, `reaches_tools`, nested execution and MCP server registration. | `docs/extensions.md` | S02, S36, S37 |
| DOC-05 | The changelog records each user-visible change. | coding-agent `CHANGELOG.md` | each slice |

Row count: 287 (CFG 26, CLT 16, STD 7, HTP 11, CON 16, TLS 19, SES 20, SEC 7,
EXP 12, TSR 11, CMD 30, RES 10, OAU 36, CMDL 14, SLH 13, MGR 10, RND 4, EXT 16,
SDK 4, DOC 5; the abbreviation CLT stands for the client library and CMDL for
the shell commands). The plan had 280 rows when it was written; item X1
added SLH-07 to SLH-13.

## 3. Proposed deviations

Each item is a Pi behaviour this plan proposes not to port, or to port in a
different form. Items marked "decided" restate an owner decision. The others
are proposals the final judgement should weigh; section 7 records what the
owner has decided.

| ID | Pi behaviour | Proposal | Reason |
| --- | --- | --- | --- |
| DEV-01 (decided) | JavaScript script API: `searchTools`, `describeTool`, `describeNamespace`; TypeScript declarations. | Python names `search_tools`, `describe_tool`, `describe_namespace`; declarations rendered as Python-style types with `#` comment lines. Same information (CMD-11 to CMD-15). | Codemode is Python by decision. |
| DEV-02 (decided) | Config and state under `~/.pi/agent/`, project `.pi/`. | `<config home>/mcp.json`, `mcp-auth.json`, `mcp.log`; project `.pipy/mcp.json`. | Decision. |
| DEV-03 | Scripts run tool calls in parallel (`Promise.all`). | Ported in G3, in a Python form: `tools.<name>.submit(args)` starts a call and returns at once, its `.result()` waits for it; a plain `tools.<name>(args)` waits for its call. At most 16 requests of one script wait for their reply at once; a script that submits more first waits for one. Until G3 nested calls were sequential, as CM1 specified. | The guest has no promises or event loop; the bound keeps the host's buffer of unread replies finite, where Pi has no bound. |
| DEV-04 | Codemode features that are not about reaching tools: raw-source input with a grammar, `return`, `console.*`, `exit()`, the `// @options:` line, `store()`/`load()`, `models.*`, structured `bash` results, image results from `read`. | Not in this campaign, except `image()` (S39), `store()`/`load()` (G4), structured `bash` results, image results from `read` and the `// @options:` line (G5). The others remain CM1 follow-ons in the backlog. `store()`/`load()` has Pi's semantics in a Python form: `store(key, None)` deletes (Pi: `undefined`), so no top-level `null` is stored; deleting a key the session's store lacks writes no entry; sizes count Python characters; `NaN` is refused (Pi stores `null`); an early exit is `sys.exit()` or `sys.exit(0)`. | They do not change whether or how MCP and extension tools are reached. `image()` and `store()`/`load()` were ported after the goal check (finding M4 for the store). |
| DEV-05 | The client identifies as `pi` in `initialize` and in dynamic client registration. | `pipy` and pipy's version. `oauth.clientName` covers servers that accept only known client names, as in Pi. | Product name. |
| DEV-06 | CIMD documents are served from `https://pi.dev/oauth/`, built in. | The mechanism is ported in full (OAU-33, OAU-34), but there is no built-in document. `<base>` comes from a pipy-only field `oauth.clientMetadataBaseUrl` (an `https` URL) next to `clientRegistration: "cimd"`; the user hosts `client.json` and, for servers without `iss`, `<id>/client.json` there. `cimd` without that field is a config error: `server "<n>": oauth.clientRegistration "cimd" requires oauth.clientMetadataBaseUrl`. If the owner later names a default (section 7, Q1), the field becomes optional. | pipy has no hosted document, and identifying as Pi's document is an identity claim only the owner can make. |
| DEV-07 | `/login` offers to configure the Radius MCP server after a Radius sign-in. | Not ported. | pipy has no Radius provider. `auth.provider` itself is ported (OAU-36). |
| DEV-08 | Credentials stored by older Pi versions under the URL alone are taken over by the first server that loads them. | Not ported. | No pipy version ever wrote that key; the no-shim rule applies. |
| DEV-09 | Windows process handling (`taskkill`, `~\` paths, `.cmd` shims). | POSIX behaviour is ported and tested; Windows is not verified. | pipy's platform evidence is macOS and Linux (see `docs/codemode.md`). |
| DEV-10 | Tool arguments are validated against the full JSON Schema of the tool before the call. | MCP and other foreign-schema tools are validated with a standard-library validator for a named keyword set (S01); keywords outside it are not enforced by pipy and the server remains the authority. Native and extension tools keep pipy's strict subset. | No JSON Schema dependency in the runtime (`pyproject.toml` keeps two dependencies). The named set is pinned by tests. |
| DEV-11 | MCP, codemode and tool search are built-in extensions built only on the public extension API. | They are native modules. The public extension API still gains every hook Pi documents for them (section 2.18). | pipy's codemode is a native composite tool with a WASI host; rebuilding it on the extension API is an architecture change the backlog rules out. |
| DEV-12 | The official MCP conformance suite runs in CI through `npx`. | A manual `just` recipe runs the same suite against pipy's client with a committed baseline (S40); CI runs pipy's own mirrored cases (section 6). | The suite needs Node and network access; pipy's CI has neither as a requirement. |
| DEV-13 | The token usage and cost of nested results, at every depth, is added to the calling tool's result usage and counts toward the session cost. | Not ported in this campaign. pipy tool results carry no usage field. | Nothing in scope produces tool usage: MCP tools and pipy's built-in and extension tools report none, and the only Pi producers are `models.classify()` and `models.generateImages()` (DEV-04). The field and its propagation belong to the slice that adds a usage-producing tool. |
| DEV-14 | The refresh lock of a server's credentials is a lock directory (`proper-lockfile`): a lock its holder stopped renewing is taken over after 20 s (OAU-20). | The lock is a `flock` on a lock file. The kernel frees the lock of a holder that died at once, and no lock is ever taken from a process that is alive. The 25 s a waiter waits are kept. | pipy already locks `auth.json` with `flock`; it needs no renewing thread and cannot be lost while held, which a lock taken over by age can (Pi `onCompromised`). Added by S30. |

Not listed as deviations because Pi does not implement them either: the legacy
SSE transport, elicitation, sampling, prompts, resource subscriptions, MCP App
rendering, the stateless `2026-07-28` protocol.

### 3.1 Differences the slices recorded

DEV-01 to DEV-14 were proposed before or while the slices were built. The
slices recorded further differences from Pi in their "Decided in" blocks
(sections 4.1 to 4.9) and in the "Different from Pi" paragraphs of the user
documents. The closeout (S40) collected them here so that the final judgement
has one list. None of them is an owner decision yet: each is a proposal.

"Rows" names the checklist rows whose behaviour a difference changes, or
`none` when it lies beside every row. "Kind" says how pipy differs:
*narrower* (Pi does something pipy does not), *other form* (the same
purpose, reached another way or at another time) or *stricter* (pipy refuses
or guards something Pi lets through). The acceptance note marks every row
that is named here as `closed, with limits` and cites the DEV row.

| ID | Rows | pipy differs from Pi in | Kind | Recorded in |
| --- | --- | --- | --- | --- |
| DEV-15 | SES-04, EXT-06, EXT-10 | A notice is shown where the session drains its notices, before the next prompt runs and when a turn starts, not at the moment it arises. That holds for the startup report, the report for a server an extension registered later, and the `mcp_servers_change` hooks; `codemode` or `tool_search` is activated for a server registered later at the same points. A session whose first action is `/mcp` therefore sees the startup report after the manager was closed. | other form | Decided in S19, S34, S36 |
| DEV-16 | SES-20 | `/new`, `/resume` and `/fork` in one process keep the servers: `mcp.json` is not read again and the startup report and the first-prompt wait are not repeated. Pi closes the servers and reads the files again there. `/reload` is transactional: a reload that keeps the previous extensions keeps the running servers, where Pi restarts them in every case. | narrower | Decided in S17, S22; `docs/mcp.md` "Reloading" |
| DEV-17 | SES-05, SES-10, SLH-02, SLH-03, CON-11 | pipy's waits can be interrupted where Pi's cannot: the first prompt's wait (Escape or an abort ends the wait and the prompt goes on), the waits of `/mcp` and its subcommands, the reconnects at a turn's start, and the 250 ms before a resource request is made again. | other form | Decided in S18, S21, S26, S31 |
| DEV-18 | TLS-10, RND-02 | Progress of a running call is appended to the call's live output line by line, and the row shows the last line. Pi replaces the preliminary result, which also carries `{server, tool}` details. | other form | Decided in S16, S35 |
| DEV-19 | TLS-11, RES-09 | An image is never drawn in the terminal: the row shows `[Image: [<mime>] <w>x<h>]`, which is what Pi shows without an image protocol. An image block without data or MIME type becomes the text `[image <mime> without data omitted]`, where Pi passes it on. | narrower | Decided in S39 (parts 1, 3, 4) |
| DEV-20 | CMD-29, CMD-30 | Text and images share Pi's limit of 16 Mi characters (since G5; S39 had a byte cap of 16 MiB for images beside 1 MiB of text), and `image()` also raises `ValueError` once the images alone would pass it. There are no `==> text N/M <==` separators (DEV-04). A result's text is at most 64 KiB, so a script that shows some hundreds of images gets a failure result instead of its output. | other form | Decided in S39 (part 5) |
| DEV-21 | TLS-11 | How a tool result's images are sent to a provider differs in three points: a text-only result is sent as before (no `(no tool output)` for an empty one); the Mistral adapter sends images as the Chat Completions family does; there is no `requiresAssistantAfterToolResult`. Images are withheld from a model without image input only when the model has a catalog row of its own, so a model without a row and a provider object passed in by an embedder are sent the images. | narrower | Decided in S39 (part 2); `docs/providers.md` |
| DEV-22 | CMD-25 | A `tool_result` transform always replaces the content and decides the structured content with it. It cannot replace `details`, `isError` or usage, it is not shown a result's images, and an extension tool cannot return an image (`ToolResult.content` is a string). | narrower | Decided in S04, S39 (part 1) |
| DEV-23 | CMD-22 | With `codemode.mode: "only"` a direct model call to a tool whose declaration is left out is an unknown tool; Pi would run it. `codemode.mode` and `codemode.inlineBudget` are read at session start and on `/reload`, not before every prompt. | other form | Decided in S08 |
| DEV-24 | CMD-27 | The codemode system prompt contribution has two guideline lines. The second comes from the codemode adherence slice and predates this plan. | other form | Decided in S08 |
| DEV-25 | CMD-06, CMD-19, EXT-03 | Only the built-in tools have prompt guidelines; extension tools have none, so nothing is added for them in `ALL_TOOLS` and the catalog. `get_all_tools()` is a method of the handler context (`ctx.get_all_tools()`), not of the activation API, and reports no `promptGuidelines` or `sourceInfo`. | narrower | Decided in S02, S07, S08 |
| DEV-26 | EXT-04 | Tools are registered while an extension loads and not afterwards. A tool whose last registration is `hidden` is registered and never declared; a session sees a withdrawal after the next `/reload`. A tool name another extension registered disables the second extension, where Pi lets the first one win silently. | other form | Decided in S02 |
| DEV-27 | CMD-02, CMD-06, CMD-07, CMD-09, CMD-10, CMD-17 | Identifiers and discovery in Python terms beyond DEV-01: a Python keyword gets a trailing `_` (`tools.class_`) and `$` is no identifier character. A tool is named everywhere by the key that reaches it, the identifier or else its own name, and a tool no key reaches is left out; Pi names every tool by its identifier, which for a tool that lost its identifier calls the winner. `limit` must be an `int` (`8.0` is refused). When Pi's two rules for close matches find none, pipy still offers its nearest-name match. | other form | Decided in S05, S07 |
| DEV-28 | CMD-13, CMD-15, CMD-19, SEC-04 | Counting and ordering where JavaScript and Python differ: the 80 characters of a tag and the 16000 of an input type count Python characters (Pi: UTF-16 units); namespaces in the codemode description are ordered without regard to case and then by code point (Pi: `localeCompare`); a server summary is never cut inside a character. A schema nested deeper than the interpreter follows renders as `Any`. `renderDeclarations` is not ported; nothing in the coding agent uses it. | other form | Decided in S05, S08, S20 |
| DEV-29 | TSR-03, TSR-04, TSR-08 | `tool_search` adds the loaded tools to the selection that is live at that moment, where Pi replaces the list with a copy it read before ranking. A listing longer than one tool result (64 KiB) ends with `(and <n> more)`; every match is loaded all the same. A string or boolean `limit` is refused, where Pi converts it (DEV-10). Property names that look like array indexes are in schema order in the search text; JavaScript lists them first. | other form | Decided in S06, S09 |
| DEV-30 | TSR-09, TSR-10 | A session resumed in a new process starts from the configured tools and restores nothing from the transcript. Pi's CLI does the same (it always passes the initial tool names); Pi's SDK resume, which the mirrored Pi test uses, restores. In pipy `/tree` and `/reload` restore. | narrower | Decided in S09, S22 |
| DEV-31 | none | An unknown name in `--tools`, `--exclude-tools` or `defaultTools` is refused at startup; Pi accepts it. An active name the filters remove is kept in the selection and narrowed when the selection is read; Pi drops it. | stricter | Decided in S10 |
| DEV-32 | EXP-12 | pipy's config command has no interactive list: the Built-in list is `pipy config list`, and the toggle is `pipy config enable\|disable builtin mcp`. The `builtin:mcp` directive follows pipy's pattern rules for the `extensions` array (last matching directive wins, `fnmatch`). | other form | Decided in S19 |
| DEV-33 | SES-11, SES-13 | pipy registers its built-in `codemode` on demand, so a session with a server of `codemode` exposure runs one runtime self-test when it starts. With `autoEnableCodemode` false nothing is tested, and the SES-13 warning names that setting whenever the filters would keep `codemode`. A `hidden` tool named `codemode` or `tool_search` does not count as a tool that reaches other tools. | other form | Decided in S19; `docs/mcp.md` "Control tool exposure" |
| DEV-34 | SES-07, EXT-05 | A `reaches_tools` function that raises, or returns neither `"all"` nor a list of words, counts as `"all"`; Pi fails the call. The readiness wait runs after every `tool_call` handler admitted the call; in Pi it is one handler among them. | other form | Decided in S18 |
| DEV-35 | SEC-06, EXT-16 | The `mcp_servers` section is rendered once per run; a queued follow-up or steering message inside a run does not render it again. `before_agent_start` has `system_prompt_options.sections` only; Pi's other option fields and the event's `messageType`, `prompt` and `images` are not built. `event.system_prompt` is a string taken when the handler is called, and an invalid section entry is reported and left out, where Pi fails the prompt. | narrower | Decided in S20; `docs/extension-api.md` |
| DEV-36 | SES-02, TLS-03, TLS-05 | An MCP tool never replaces a built-in or extension tool of its name: it is left out and reported once. A tool whose input schema declares a type other than `object` is left out and reported; Pi registers it as it is. A name with a hash suffix that already belongs to another tool gets another suffix, so no name ever changes its owner. | stricter | Decided in S16, S17 |
| DEV-37 | SES-14, SES-15 | Enabling a server that is enabled saves the change and opens nothing; Pi would open a second connection. An exposure change registers the server's tools again whenever the server has a connection, in any state; Pi does so only while it is connected. Only names an MCP tool has are taken out of the selection. | stricter | Decided in S21 |
| DEV-38 | SLH-01, SLH-02, SLH-06 | For a line typed into a session the order is built-in command, prompt template or custom command, extension command, `/mcp`, so a template or custom command named `mcp` takes the command's place; in Pi only an extension command does. For a prompt of print, JSON and RPC mode the order is Pi's. An unknown action prints the usage without waiting for the startup connections. | other form | Decided in S21, X1 |
| DEV-39 | SLH-07 | A command that fails is the line `pipy: extension command /<name> failed (<error>)` on stderr in print, JSON and RPC mode. Pi prints `Extension error (command:<name>): <message>` and, in RPC mode, sends an `extension_error` object. | other form | Decided in X1 |
| DEV-40 | SLH-09 | pipy's rules for skills and templates stay: a template named like a built-in command is not expanded, custom commands (`.pipy/commands`) expand like templates, a skill or template turn skips the `input` hooks (Pi runs them first), and an unknown or unreadable skill is reported on stderr before the text goes to the model. | other form | Decided in X1 |
| DEV-41 | SLH-11 | In print and JSON mode a command's notices go to stderr; Pi shows nothing there. In RPC mode only `/mcp` has the UI channel: `ctx.has_ui` stays false for extensions, their own `select`, `confirm`, `input` and `editor` return defaults without a request, and there are no `confirm`, `editor`, `setStatus`, `setWidget`, `setTitle` or `set_editor_text` requests and no `timeout` option. | narrower | Decided in X1 |
| DEV-42 | none | The RPC command `get_commands` answers an empty list; Pi lists the extension commands, prompt templates and skills. | narrower | Decided in X1 |
| DEV-43 | MGR-09 | The manager view beside the open part of MGR-09: the copy key is fixed to Ctrl+X (pipy has no `app.message.copy` action); titles are not bold and the copy note has no colour of its own; the pasted URL is edited with the selector input's keys; a screen taller than the terminal is cut above its controls, where Pi's scrolls; the cancel key ends the waiting also during the reconnect after a sign-in; a sign-out that fails is a message in the server's menu. | other form | Decided in S34; `docs/mcp.md` "The `/mcp` manager" |
| DEV-44 | RND-01, RND-03 | MCP rows are drawn by pipy itself, by the name rule, also with MCP turned off; Pi has the rule only while its MCP extension is loaded. A title is made printable. pipy's export page is its own page with Pi's `renderedTools` added: inputs are redacted before they are drawn, the rows sit in a dark box, a result's images are shown for every tool (Pi: for `read`), and `pipy --export` and `/share` run no extension code. | other form | Decided in S35, S39 (part 3) |
| DEV-45 | EXT-15 | A resolver's `next()` gives a marker for pipy's own rows and `None` for an extension tool without renderers, so a resolver cannot call and wrap pipy's rows as it can wrap Pi's renderers. `renderShell` is not ported. A resolver that raises, or returns something that is not renderers, is left out; Pi lets the exception through. | narrower | Decided in S35 |
| DEV-46 | EXT-07, EXT-09 | The owner of a registration is the extension's path label, not its absolute path. Extensions that load before the project is trusted do not see each other's registrations: a shared name disables the later one when they are finalized (`duplicate_mcp_server`), and a `project_trust` handler cannot register or list servers in between. One extension that registers `my-server` and `my_server` while it loads is refused the second. | other form | Decided in S36 |
| DEV-47 | EXT-10 | The report of unhandled registrations appears when MCP is turned off and no extension has a hook for `mcp_servers_change`, because pipy's MCP support is not an extension that handles the event. The text is Pi's. | other form | Decided in S36 |
| DEV-48 | EXT-11 | The callable tools are fixed, for every depth, when the tool the model called starts. Nested calls always run concurrently (no sequential mode, no `executionMode`), and those still running when their calling tool returns are cancelled (G2, G3). Its outcome's `result.content` is the text, with the blocks in `result.blocks` when there is an image, and a partial result is a text chunk (G2). No event is emitted for a name that is not callable. Usage is DEV-13. | narrower | Decided in S37 |
| DEV-49 | EXT-12 | The loadout has no `getExposure`, `getNamespace` or `getPromptGuidelines`. The function is called at every read of the declarations and must be pure; Pi calls it when the active tools change. A failing function is one warning per session, tool and error type, not an error of its extension. | narrower | Decided in S37 |
| DEV-50 | EXT-13, EXT-14 | No notice is shown when an extension replaces `codemode` or `tool_search`. Only an extension's `/mcp` command replaces built-in MCP, not a tool or flag name of Pi's MCP extension. The warning names the extension by its name and points to `pipy config list`. A `/reload` reads `mcp.json` once before it knows the new extensions and drops what it read when one of them has `/mcp`. | narrower | Decided in S37 |
| DEV-51 | SDK-02 to SDK-04 | The three are arguments of the session factories, not extension factories. A product session runs no slash command (`submit()` sends its text to the model), so `open_url` and `update_config` reach the command and the manager and nothing in a product session uses them. `load_config` gets three values instead of an extension context. `CodemodeOptions` has no `models` field (DEV-04). The options are checked when they are made. An extension with `/mcp` replaces MCP in an embedded session too. Servers start when the session is created. The example runs offline, with a scripted provider and a server in the same process. | narrower | Decided in S38; `docs/sdk.md` |
| DEV-52 | OAU-36 | `auth.provider` reads `auth.json` and the environment only: a `models.json` key, `--api-key`, the `openai-codex` login and ambient cloud credentials give no token, and a login pipy does not refresh for its own requests is sent as stored. A stored `!command` key runs for every MCP request. | narrower | Decided in S33 |
| DEV-53 | OAU-17 | The sign-in pages have Pi's layout and texts without Pi's logo. | other form | `oauth/page.py`; Decided in S28 and S29 |
| DEV-54 | CFG-02, CFG-13, CFG-17, CFG-18, CFG-20, OAU-34 | URLs are read by a strict parser that repairs nothing: a URL without `//`, with a backslash or with a shortened IPv4 address is an error, where Pi's parser accepts it. The `<id>` of a server-specific Client ID Metadata Document hashes the URL as that parser writes it, which for an unusual URL is not Pi's text. A file that is not UTF-8 is an error. A whole-number `oauth.callbackPort` written `8080.0` is accepted. | stricter | Decided in S11 to S14, S32; `docs/mcp.md` "Configuration rules" |
| DEV-55 | OAU-18, OAU-20, OAU-23, CON-14, CMDL-14 | Reading the credentials takes no lock and creates no file; Pi locks and creates it. Deleting credentials holds the server's refresh lock, so a refresh that is answered after a sign-out cannot store tokens again; a lock held for 25 s, and a file that cannot be read, are reported (`Could not sign out ...`, `Could not update <path>: ...`). A late 401 on a client that was already replaced does not put a working connection into `needs-auth`. After `close()` a connection's auth provider starts no refresh. | stricter | Decided in S30, S31 |
| DEV-56 | CON-05, CON-06, CON-10, CON-12, CON-15 | Connections: a failed refresh of the tool list is recorded only for the current connection. The client of a session the server forgot is closed once its last request ended; Pi leaves it open. A network failure is whatever Python reports as one (Pi: a `TypeError` of `fetch`). Configured headers and tokens are sent only to the server's own origin, so a redirect to another host fails, and the OAuth fetch follows a redirect only for a request without a body. `mcp.log` is created with mode 0600. The stderr tail is 2000 characters, not UTF-16 units. | stricter | Decided in S11 to S14, S15, S26; `docs/mcp.md` "Server connections" |
| DEV-57 | CMDL-01, CMDL-04, CMDL-11, CMDL-13 | The shell commands print plain text where Pi prints parts bold or dim. `--oauth-callback-port` and `--timeout` take a decimal number; Pi also reads JavaScript's other spellings. A trust store that cannot be read ends `list`, and `add` to a project file before it writes, with the store's error. When the question for the redirect URL ends without an answer, pipy ends its line. | other form | Decided in S20a, S31; `docs/mcp.md` "Differences from Pi" |
| DEV-58 | RES-04, RES-07, RES-08, RES-10 | Whatever a resource tool raises is an error result, so a script gets a `ToolError`; Pi throws. A listing is compact JSON and prints a number as Python prints it. A resource list change is refreshed on the thread that refreshes the tool list, one kind per round. | other form | Decided in S23 |
| DEV-59 | CMD-03, CMD-04, EXT-02 | A tool cannot hand a script JSON `null` as its structured content; the script gets the text. A reply to a script over 16 MiB, or one that is not JSON, is a `ToolError` that names the tool and the size. The JSON and RPC event `tool_execution_end` carries no structured content. | narrower | Decided in S04 |
| DEV-60 | TLS-12, TLS-15 | Results of MCP tools: a resource link without `uri` or `name` is shown as `[unsupported MCP content resource_link]`; the message of a failed call is cut to 20 KB like a result and nothing is saved for it; a lone surrogate in a server's text reaches the model as U+FFFD. | other form | `docs/mcp.md` "Differences from Pi in tools" |
| DEV-61 | none | Texts that come from the platform are Python's, not Node's: errors of the operating system (a missing executable is `[Errno 2] No such file or directory: '<command>'`), messages of the JSON parser, and the reason of a failed save. A JSON number such as `1.0` in log data or a listing is written `1.0`. | other form | `docs/mcp.md` "Differences from Pi" paragraphs |
| DEV-62 | none (DEV-12) | The conformance runner checks `--mode` in its argument parser (exit status 2), reports a missing `npx` and a temporary directory with a space in its path before anything runs, and fails the `runner` check for a `checks.json` it cannot read. | other form | Decided in S40 (part 1) |
| DEV-63 | none | Ended by SIGTERM or SIGHUP, pipy exits with status 143 or 129 in every mode; Pi's interactive mode exits with 0. A second signal during the shutdown is ignored in every mode; Pi's RPC mode exits at once on it. The exit hook for server processes that are still running sends SIGKILL to a group that is not gone when its close timeout is over and waits until the servers are reaped (at most a second after the last SIGKILL); Pi's hook sends SIGTERM and returns. | other form | Decided in X2 |
| DEV-64 | none | A `tool_call` handler's change to the arguments is validated again before the tool runs: a change the schema refuses is the validation error (not a malformed call), and a value that is not JSON blocks the call. Pi runs the changed arguments as they are. | stricter | Decided in G6 |

Two rows are open and are not deviations until the owner decides to accept
them as such: SLH-13 (commands and expansion in queued RPC input) and the
hyperlink part of MGR-09. Section 3.2 has the audit result.

### 3.2 Audit result (S40, 2026-10-14)

The closeout walked every row of section 2 against the tests of
`feat/mcp-parity`. The table of rows, tests and limits is the
[acceptance note](../acceptance/2026-10-13-mcp-parity.md);
`tests/test_mcp_parity_audit.py` keeps it in step with this plan and the
test suite.

| State | Rows | Which |
| --- | --- | --- |
| Closed with a test | 179 | see the note |
| Closed with a limit or deviation | 106 | each names its DEV rows or its limit in the note |
| Closed in part | 1 | MGR-09: the authorization URL is plain text, not a terminal hyperlink, and there is no "click to open" hint |
| Open | 1 | SLH-13: `steer`, `follow_up` and a `prompt` sent during a run are queued without a look at the text |

The 106 rows count as closed only if the final judgement accepts the
deviation or limit each one names. Not rows, and still outstanding: the live evidence
of section 6.5, which needs the owner's provider login and an account at an
OAuth MCP server, and question Q1 of section 7.

## 4. pipy design

### 4.1 Module layout

New package `src/pipy_harness/native/mcp/`. Nothing in it imports the TUI.

| Module | Contents | Pi counterpart |
| --- | --- | --- |
| `protocol.py` | JSON-RPC message predicates and parser, error types (`McpError`, connection closed, timeout, abort), protocol versions, content block conversion (`to_llm_content`) | `mcp/protocol/*` |
| `transport.py` | `McpTransport` protocol (start, send, close, three listener sets, `set_protocol_version`), listener bookkeeping, in-memory transport pair for tests | `mcp/transports/transport.ts`, `in-memory.ts` |
| `client.py` | `McpClient`: handshake, request/notify, pending map, timeouts, progress, cancellation, server requests, pagination, typed list/call/read helpers | `mcp/client.ts` |
| `stdio.py` | `StdioTransport` | `mcp/transports/stdio.ts` |
| `sse.py`, `http.py` | SSE parser; `StreamableHttpTransport`, HTTP error types | `mcp/transports/streamable-http.ts` |
| `oauth/` (`types.py`, `errors.py`, `fetch.py`, `discovery.py`, `flow.py`, `provider.py`, `callback.py`, `page.py`) | The OAuth client library; `fetch.py` is the HTTP fetch Pi gets from JavaScript, `page.py` the sign-in pages of Pi's `oauth-page.ts` | `mcp/oauth/*` |
| `config.py` | Entry validation, exposure resolution, load, edit, the strict URL parser (the name matcher is in `native/tools/name_patterns.py`) | `core/mcp-servers.ts`, `ext/config.ts` |
| `config_values.py` | `${NAME}` and `!command` resolution of config values | `core/resolve-config-value.ts` |
| `log.py` | Server log file | `ext/log.ts` |
| `connection.py` | `McpServerConnection`, default transport factory | `ext/runtime.ts` |
| `credentials.py` | `McpOAuthCredentialStore`, the connection auth provider, `sign_in_mcp_server` | `ext/oauth.ts` |
| `tools.py` | Naming and the session's name table, definitions, tool ports, result conversion, truncation and spill, result schema | `ext/tools.ts`, `ext/index.ts` `assignName` |
| `resources.py` | The three resource tools | `ext/resources.ts` |
| `manager.py` | `McpManager`: the per-session owner of servers, readiness, registration, the prompt section and the startup report | `ext/index.ts` |
| `servers_section.py` | `render_servers_section`: the text of the `mcp_servers` prompt section | `ext/index.ts` `renderServersSection` |
| `discovery.py` | `McpDiscovery`: activates `codemode` and `tool_search` from the configured exposures, and the warning | `ext/index.ts` `ensureDiscoveryActive` |
| `readiness.py` | `McpReadiness`: which servers the first prompt and a tool call wait for | `ext/index.ts` `waitForDirectServers`, `tool_call` handler, `reachesServer` |
| `registry.py` | Extension-registered servers | `core/mcp-servers.ts` `McpServerRegistry` |
| `shell_cli.py` | `pipy mcp ...` | `ext/cli.ts` |
| `commands.py` | `/mcp` command (the session adapter is `repl/mcp_command.py`) | `ext/index.ts` command |
| `repl/mcp_session.py` (outside `native/mcp/`) | `McpSession`: the servers in use, replaced on `/reload` | `ext/index.ts` `session_start`, `session_shutdown` |
| `options.py` | `McpOptions`: what an embedder may replace | `ext/index.ts` `McpExtensionOptions` |
| `repl/mcp_loading.py` (outside `native/mcp/`) | `load_mcp_servers`: builds the manager for the configuration as it is now, from the settings, the flags and the embedder's options | `ext/index.ts` `createMcpExtension`, `defaultLoadConfig` |
| `session_builtins.py` (in `native/`) | `SessionBuiltins`, `CodemodeOptions`: which of `codemode`, `tool_search` and MCP an embedded session has | `cm/index.ts` `CodemodeExtensionOptions`; `examples/sdk/14-codemode-mcp.ts` |
| `manager_menus.py`, `manager_flow.py` | The manager without its drawing: the menus as values, and the flow that decides what a choice does | `ext/index.ts` `serversMenu`, `serverMenu`, `showTools`, `chooseExposure`, `manage`, `runAction`, `signInWithUi`, `runInBackground` |
| `ui/components/mcp_manager_view.py` (outside `native/mcp/`, which may not import the UI) | The manager view | `ext/ui.ts` |

Other new modules: `native/tool_search.py` (tokenizer, BM25, document
builder, the `tool_search` tool), `native/codemode/declarations.py`
(schema to Python type text, tool samples) and
`native/coding/tool_parent_runner.py` (the runner and the thread bridge of
a tool that runs other tools, see "Decided in S37").

Decided in S06 (search core, `native/tool_search.py`):

- The API is `tokenize`, `stem`, `create_tool_search_document(tool,
  namespace=None)`, `Bm25Ranker(k1=1.2, b=0.75).rank(query, documents, limit)`
  and the values `ToolSearchDocument` and `ToolSearchMatch`. A `ToolInfo` and a
  `ToolDefinition` both serve as `tool`; the namespace is passed separately,
  as in Pi.
- Terms are ASCII, as in Pi: every other character separates terms. Text is
  split at camelCase boundaries like names, so `GitHub` in a description is
  the terms `git` and `hub` and the query `github` does not find it.
- The schema walk reads `description`, `properties`, `items` (a single
  schema only) and `anyOf`/`oneOf`/`allOf`, and nothing else (no `$defs`,
  `additionalProperties`, `prefixItems`, `title` or `enum`). It uses its own
  stack, so a deeply nested schema is read to the bottom where Pi's recursion
  would overflow.
- A text part is left out when JavaScript's `trim` would empty it, so the
  search text equals Pi's except for the order of property names that look
  like array indexes (JavaScript lists those first).
- The ranker was compared with Pi's on generated tools and queries (same
  token lists, search texts, orders and scores).

Decided in S09 (the `tool_search` tool, `native/tool_search.py`):

- `ToolDefinition` gains `default_active` (Pi `defaultActive`): a `direct` or
  `model-only` tool with `False` is registered without being activated.
  `tool_search` is the one tool that uses it. `ExtensionTool` does not take
  it yet; S37 needs it for an extension that replaces `tool_search` and
  wants the replacement inactive. Like the other metadata it is dropped from
  the frozen request definition.
- `NativeToolCapabilities.prepare_extensions` registers `tool_search` with
  the production built-ins on every preparation; it needs no runtime, so
  there is no probe as for `codemode`. A caller-injected plain registry does
  not get it. `codemode` is still registered only when it is named.
- An allowlist (`--tools a,b`) activates every declarable tool it names,
  also one that is inactive by default (Pi `_refreshToolRegistry`); a filter
  that names nothing (`--exclude-tools`, `--no-builtin-tools`) leaves it
  inactive. `+tool_search` in `--tools` or `defaultTools` and
  `set_active_tools` activate it as they activate any name.
- Loading is `NativeToolCapabilities.loadable_tools()` (registered, kept by
  the filters, `codemode` or `deferred`, not active) and
  `load_tools(names)`, which adds the names to the selection that is live at
  that moment, inside the state lock, and returns what it added. Pi replaces
  the active list with a copy it read before ranking; pipy adds, because an
  extension handler on another thread may change the selection in between.
  The tool reads the live capability value, not the value of the provider
  turn that called it. A load is not marked as a selection an extension
  customized, so a later `/reload` still activates new extension tools.
- The schema is Pi's, with `limit` as a JSON number, so the definition is a
  foreign schema. `1.0` is an integer as in JavaScript; a string or boolean
  is refused by argument validation (DEV-10: Pi would convert `"3"`).
- A listing longer than one tool result (64 KiB) ends with `(and <n> more)`;
  all matches are loaded and named in `details.loaded`. Pi has no cap.
- TSR-09: a load is recorded by the system message of the next request
  (`tools_added` holds only the loaded tool), `/tree` restores it through
  `restore_active_tools`, and `/clone` keeps it because the capability value
  does not change. A resumed session starts from the configured tools, as
  Pi's CLI does (it always passes the initial tool names); declaring loaded
  tools again after resume and reload is TSR-10 (S22).

Decided in S11 to S14, S24, S25 and S27 (the client track, merged in INT1):

- Requests and their `notifications/cancelled` are written by one writer
  thread per client from an outbox, so a blocked write cannot defeat a
  timeout or a cancel. A request that ends while it is still queued is never
  written, and a cancellation is sent only for a request that was written.
  `notify()` and answers to server requests are written by their caller.
  This replaces "sends the message and waits" in 4.5.
- `McpTransport.send(message)` takes no deadline or cancel argument. The
  transport contract has a fourth listener set, `on_request_failure`, through
  which a transport ends one pending request with a typed error.
- HTTP: `send` waits for the POST to be accepted only for notifications and
  responses (at most `send_timeout_ms`, default 30 s). For a request it
  starts the I/O thread and returns, and a failure reaches the caller through
  `on_request_failure`; with one writer thread, waiting for response headers
  would put every request of a client behind a slow tool call. A request's
  I/O is cancelled when its `notifications/cancelled` passes through `send`,
  and by `close()`, not by the waiting side. This replaces the HTTP
  paragraph of 4.5 and the `CancelToken` sentence of 4.6 where they differ.
- A failure of the GET stream that is not retried, or five failures in a row,
  is reported to the error listeners and the stream is not reopened. What a
  session-expired error from the GET stream means for the connection is
  S26's decision.
- Redirects, which have no checklist row: the HTTP transport follows one only
  within the configured origin; the OAuth fetch follows one only for a
  request without a body and drops credentials headers across origins.
- `oauth/fetch.py` is the one HTTP client of the OAuth library:
  `create_oauth_fetch(timeout_ms=..., max_response_bytes=1 MiB)`. The 15 s
  limit of OAU-22 is a fetch made with `timeout_ms=15000`.
- `config.py` owns the URL parser (`parse_http_url`, `HttpUrl`) that config
  validation and OAuth discovery share. It is strict and repairs nothing.
- A server config is a plain dict with Pi's JSON key names; there are no
  typed config classes. `load_mcp_config(config_home=, cwd=,
  project_trusted=)` takes the trust decision from its caller.
- Pi's plain `Error` is `McpProtocolError` in the client and transports and
  `OAuthFlowError` in the OAuth package. The error types have no common base.
- Tool name patterns have one implementation,
  `native/tools/name_patterns.py` (S10); `config.py` uses it for
  `toolExposure` keys and takes the `mcp__` prefix from it. With
  `native/cancellation.py` in `http.py` and `oauth/fetch.py` it is the
  package's only import from outside `native/mcp/`.
- An architecture rule keeps `native/mcp/` free of imports from `native/ui`
  and `native/tui`. S34 has to place the manager view accordingly or change
  the rule on purpose.

Decided in S15 (`connection.py`, `config_values.py`, `log.py`):

- `auth_store.resolve_config_value` implements Pi's older rule (a whole
  value that names a variable), which API keys keep. MCP values follow Pi's
  current template rules in `mcp/config_values.py`:
  `resolve_config_value(value, description)` and
  `resolve_config_headers(headers, description)` raise `McpConfigValueError`
  with Pi's texts. Only the command runner of `auth_store` is reused.
- `McpServerConnection(entry=, cwd=, client_version=, on_tools=, on_change=,
  create_transport=, log=)`. The version is passed in, because the function
  that knows it lives in `native/chrome.py`. `get_client(cancel_event=None)`
  and `call_tool(name, arguments, options)` block; `view()` returns the
  `ConnectionView` of 4.5 (`name`, `state`, `error`, `tools`,
  `instructions`). Failures are `McpServerConnectionError`.
- A connect runs on its own daemon thread (`mcp-connect`) and every caller,
  the first included, waits for it and can stop waiting through its cancel
  event. A tool list change is handled by a thread (`mcp-refresh`) that lives
  only while a refresh is wanted and lists again when changes arrived in the
  meantime; a change announced while the first list is read is refreshed
  after the connection is published. This is the "connection's own worker
  thread" of 4.5.
- `on_tools` and `on_change` say that something changed and are called on
  the thread that made the change, without a lock. The receiver reads
  `view()`; two changes may be announced in either order, and one may arrive
  just after `close()` returned. The manager's queue (4.5) has to take that
  into account. An exception from a callback is dropped.
- `close()` closes the connected client and the client of a running attempt,
  then waits for the attempt and for every transport that started and has
  not reported its close, including one another thread is closing. The
  contract a transport has to keep for this: after a `start` that succeeded
  it reports its close exactly once, whoever closes it (S26 checks this for
  the HTTP transport). A transport that never started is not waited for.
  Every caller waits for those facts, not for the first caller. A `close()` made
  from inside a callback of the connection shuts it down and waits for
  nothing, because a transport may need that thread back before it can
  finish closing; the manager must not rely on such a call having ended the
  server process.
- The default transport factory raised for a `url` entry until S26. The
  factory signature already carries the auth provider (always `None` until
  S30). `resolve_config_headers` exists for S26; `oauth.clientSecret` is
  resolved by S30's `oauth_settings` with `resolve_config_value`.
- Left to their slices: the retry loop and its wait (S26, with the
  retry-wait half of CON-09), `withClient`'s retries and session renewal
  (S26), `needs-auth` and `sign_out` (S30, S31), resources at connect time
  (S23). `reconnect` came with S21.

Decided in S16 (`tools.py`, and `truncate_middle` in
`native/tools/truncate.py`):

- `ToolDefinition`'s name rule is not relaxed. `create_mcp_tool_name` yields
  only letters, digits and `_`, as Pi's does, and at most 64 characters.
- `McpToolNames` is the session's name table (Pi `toolOwners`, 4.4):
  `assign(server, tool_names)` returns the names for one tool list in list
  order and `owner(name)` the `(server, tool)` a name belongs to. The manager
  owns one instance; it has its own lock. Stricter than Pi: a suffixed name
  that already belongs to another tool, or is already used in the list, is
  replaced by one with another suffix, so the names of a list are all
  different and no name ever changes its owner.
- `McpToolPort(server=, tool=, name=, exposure=, namespace=, timeout_ms=,
  get_caller=, readable_resources=None, save_output=save_to_temp_file)` is
  the tool port of 4.4. `tool` is the server's definition, `exposure` the
  MCP exposure of the config. `get_caller()` is asked on every call and
  returns something with the connection's `call_tool`; what it raises (the
  TLS-19 messages of S17) is the call's error result, like every other
  failure of a call. `readable_resources()` is asked on every call too
  (TLS-12; `False` until S23). The timeout is the one of the connection at
  registration, as in Pi.
- The constructor raises `ValueError` for a tool that cannot be a pipy
  definition: an input schema whose `type` is not `object`, or an unknown
  exposure. Pi registers such a schema as it is. S17 has to leave that tool
  out and report it instead of failing the server's registration.
- `convert_mcp_result` returns model-facing content blocks (text and image),
  the details, the script result and the error flag, as Pi's function does.
  `ToolExecutionResult` has text only, so the port joins the blocks with
  newlines and writes `[image <mime> omitted]` for an image
  (`render_model_text`). S39 replaces that one step.
- A script result is delivered through `structured_content` and the
  declared output schema (S04), so CMD-05 needs nothing in the codemode
  host. A value over the reply limit of 16 MiB is an error in the script.
- Progress goes to `ToolContext.output_sink` as one line per notification.
  Pi's update replaces the preliminary result and carries details; pipy's
  sink takes text only.
- Spill files are created by `save_to_temp_file` in the temp directory as
  `pipy-mcp-<16 hex><extension>`, exclusively and with mode 0600. Nothing
  removes them, as in Pi.
- Base64 of an embedded blob is decoded as leniently as Node decodes it
  (either alphabet, other characters skipped, missing padding accepted).
- `truncate_middle` is Pi's `truncateMiddle`, in `native/tools/truncate.py`
  next to the other truncation helpers; `format_size` takes a float as well.

Decided in S17 (`manager.py` and the session wiring; the capability value
is under 4.4):

- `McpManager(config=, cwd=, client_version=, publish_tools=, log=,
  create_transport=)` takes a loaded config and a function that replaces the
  session's MCP tools (`NativeToolCapabilities.publish_mcp_tools`), so the
  package does not import the capability owner. `start()` returns at once,
  `close()` returns when no transport is open, `servers()` gives
  `McpServerStatus(entry, enabled, connection view)` values,
  `wait_until_settled(timeout)` waits until every started server connected
  or failed and its tools were published, and `config_errors` keeps the
  loader's errors for the startup report (S19). `wait_until_settled` is what
  S18 builds its waits on; it has no cancel event yet. (S18: it is now
  `wait_for_servers` without a selection; see "Decided in S18".)
- Threads: one `mcp-start` thread per enabled server asks its connection for
  a client. A connection's callbacks only put the server on a queue. One
  `mcp-manager` thread takes servers from the queue, reads the connection's
  view, builds the tool ports and publishes the tools of all servers. It is
  the only publisher, so two changes announced in either order end with the
  newest views published. A session without an enabled server starts no
  thread.
- A server's ports are built again only when its tool list or its
  instructions changed; a state change alone publishes nothing. Tools stay
  registered while their server is disconnected or its reconnect failed, as
  in Pi.
- Locks, in the order they may be taken: the manager's publication lock, the
  manager lock, a connection's lock, the capability state lock. The
  publication lock is held from the check that the manager is open to the
  end of the publication, and `close()` takes it to mark the manager closed,
  so nothing is published after `close()` returned. `close()` must therefore
  not be called by a thread that holds the capability state lock. A test
  wraps the four kinds of lock and fails on an acquisition out of order and
  on any lock taken while the manager lock is held.
- The queue of 4.5 is `deliver_notices(sink)`: notices are kept by the
  manager and handed to the sink on the caller's thread. The session calls it
  where it drains the extension outboxes, which is before each step is
  selected: before a prompt starts and when the session becomes idle. It is
  not called at the start of each provider turn yet. Two notices exist: a
  tool pipy cannot declare, and a tool whose name another tool has. Each text
  is reported once in a session.
- TLS-19: a port's `get_caller` asks the manager, which resolves the server
  by name when the call runs. After `close()` there are no servers, so the
  text is `MCP server "<n>" is disabled.`, as after Pi's shutdown. The state
  "enabled and no connection yet" cannot be reached before S21 adds
  enabling in a session; its text is tested by setting that state on the
  manager's record.
- Session wiring (`repl/wiring.py`): the manager is built with the session's
  tool capabilities from `load_mcp_config(config_home=<directory of the
  user-level settings file>, cwd=, project_trusted=settings.project_trusted)`,
  started after `session_start` fired, and closed in the cleanup every exit
  of a started session runs (with `close_extension_session`), so also when a
  lifecycle hook ends the shutdown before `session_shutdown` fired.
  `.pipy/mcp.json` is a protected project entry
  (`project_trust.PROTECTED_PROJECT_ENTRIES`), as in Pi's trust manager, so a
  project that has only that file asks for a trust decision. This is the
  trust wiring of 4.7.
- pipy fires `session_start` at startup and on `/reload` only, not when a
  session is replaced in the same process (`/new`, `/resume`, `/fork`). The
  manager therefore lives as long as the process serves sessions and keeps
  its servers across such a switch. Pi closes the servers and reads
  `mcp.json` again there. S22, which owns the reload path, decides whether a
  switch restarts the manager.
- Not in S17, although the session now connects servers: no problem is
  reported at startup (S19), nothing turns on `codemode` or `tool_search`
  (S19), and no prompt or script waits for a server (S18, done).

Decided in S18 (`readiness.py`, `McpManager.wait_for_servers`, the loop's
readiness port and `repl/mcp_waits.py`):

- `McpManager.wait_for_servers(select=None, *, timeout=None,
  cancel_event=None)` is Pi `waitForServers`. `select` chooses servers by
  their config entry, once, when the wait begins. The wait counts a chosen
  server while it is enabled, was started and is still one of the manager's
  servers. After waiting it reads the servers' `ready` events again, so an
  attempt that replaced the one it waited for is waited for as well. It polls
  the cancel event and the deadline every 50 ms, as the client does.
  `timeout=0` only looks. `close()` ends every wait. `tool_owner(name)` gives
  the server a pipy tool name was assigned to.
- A `ready` event belongs to one connection attempt. The thread that made
  the attempt (`mcp-start`) hands the event to the registering thread with
  its queue entry, and that thread sets it after the registration, whatever
  the registration did. A change a connection announces never sets it. In
  S17 `_register` set the server's current event whenever the state it read
  was not `connecting`; with a second attempt that would have settled the
  new attempt on a late change of the old one, and Pi's handler says that
  readiness can be pending while the state still reads `connected`.
  **S21** (enable, disable, reconnect) has to give every new attempt a new
  event, assign it to the server record under the manager lock, and pass it
  along as `start()` does. The tests simulate a second attempt by assigning
  the event themselves, since nothing queues one yet.
- `McpReadiness(manager, all_tools, startup_wait_seconds=None)` decides what
  to wait for and returns a function that waits, or `None` when nothing is
  connecting. The usual case, every server settled, costs one look at the
  servers per tool call and does not list the tools. `first_prompt_wait()`
  is Pi `waitForDirectServers` (once per `McpReadiness`, so once per process
  run while the manager lives that long; S22 resets it if a reload restarts
  the servers). `call_wait(tool_name, arguments_json)` is Pi's `tool_call`
  handler: the reach of a tool that declares `reaches_tools`, every server
  for the three resource tool names, the owner for an MCP tool, nothing
  otherwise. The tool is looked up in `NativeToolCapabilities.all_tools()`,
  as Pi looks it up in `getAllTools()`.
- Where the tool wait runs: the agent loop has an optional port,
  `AgentLoop(tool_readiness=)` of type `ToolReadinessWaiter`
  (`agent/tools.py`), called in `_execute_admitted_tool` after the
  `tool_call` policy admitted the call and before a composite parent takes
  its callable snapshot (4.3) or a tool runs. It serves direct calls,
  composite parents and nested calls with one call site, and it gets the
  waiter the call itself would get. A result other than `SETTLED` cancels the
  call with the outcome `ToolExecutor` gives a tool that was interrupted
  while running (`cancelled_tool_outcome`, now a function of
  `agent/tools.py`), so the turn ends the same way. A blocked call does not
  wait. In Pi the wait is one `tool_call` handler among the extensions';
  pipy runs it after all of them.
- `repl/mcp_waits.py` runs a wait: the wait itself on a short-lived thread
  `mcp-wait`, the session thread in the tool interrupt waiter, as 4.5 says.
  Without a waiter (a plain non-interactive run without an abort signal) the
  session thread just waits. The scope carries one `McpSessionWaits`.
- The first prompt's wait is made where the run's system prompt is rendered
  (`loop_step._base_system_prompt`), after the `input` hooks and before
  `before_agent_start`, so the prompt, the transcript's sections (S20's
  `mcp_servers` section too) and the first request see the tools. That point
  is before the run exists, so there is nothing to cancel: an interruption
  (Escape through `turn_leaves.wait_for_escape`, or the abort signal of a
  headless run) ends the wait and the prompt goes on, with the notice. This
  is how SES-10 holds for the first prompt; Pi does not interrupt that wait
  at all.
- The startup limit is `readiness.STARTUP_WAIT_SECONDS` (10 s), read when the
  session is wired. Pi's `startupWaitMs` option belongs to the SDK slice
  (S38), which can pass `startup_wait_seconds`.
- A `reaches_tools` function that raises, or returns neither `"all"` nor a
  list of strings, counts as `"all"`. Pi fails the call. Arguments that are
  not a JSON object reach the function as `{}`.
- Not reachable through a public path yet, tested with a simulated second
  attempt: SES-09 "including a reconnect in progress" and the second
  sentence of SES-10 (S21 makes them reachable). The resource tools do not
  exist before S23; the rule for their names is tested on `McpReadiness`.

Decided in S19 (`discovery.py`, the startup report, the switches):

- `McpDiscovery(configured_exposures=, auto_enable_codemode=, tools=,
  notify=)` is Pi `ensureDiscoveryActive`; `ensure_active()` is called on the
  session thread. The session calls it once, in `_start_session` after
  `McpManager.start()`. Every later caller is another slice's: S21 after
  enable, disable, an exposure change and reconnect, S22 after a reload (with
  a new object, which also resets "warned once"), S31 after a sign-in, S36
  after a change of the registered servers. The object is on
  `_ExtensionPhase.mcp_discovery`.
- The capability value has what the activation needs
  (`NativeToolCapabilities`): `register_codemode()` registers the optional
  built-in for the run when the filters keep it and the runtime probe
  passes, keeping the selection and the extension generation;
  `activate_tools(names)` adds registered tools to the live selection without
  marking it as customized; `declared_tool_names()` is Pi `getActiveTools`;
  `codemode_selectable()` says whether `codemode` is or could be registered,
  without probing.
- pipy registers `codemode` on demand, so SES-11 costs one runtime probe (a
  self-test in a child process) at the start of every session that has an
  enabled server with `codemode` exposure and `autoEnableCodemode` not
  false. With the setting false nothing is probed, and the SES-13 warning
  names the setting whenever the filters would keep `codemode`. When the
  probe fails the warning has no reason and the probe's own
  `codemode unavailable` line says why.
- `publish` (reload) activated new extension tools on a selection that was
  only added to (not customized) only when default tools were configured.
  That condition is gone: discovery activation makes "added to, nothing
  configured" the usual state of a session with MCP servers.
- The startup report is built by the manager. `start()` keeps the `ready`
  events of the attempts it made; the registering thread removes an event
  after it registered the attempt's outcome and, when none is left, posts
  the report as a notice before it sets the event, so a wait for the servers
  returns with the report already queued. Without an enabled server
  `start()` posts it. A closed manager reports nothing. Later attempts (S21)
  have other events and never cause a report; Pi's second form of
  `reportProblems` (`only`, for servers registered later) is S36's.
  `describe_state(status, with_error=)` is Pi `describeState` without the
  resource count (S23).
- The report is a notice like the others, so it is shown where the session
  drains them: before the next step is selected. In a terminal that is when
  the next prompt is sent; Pi shows it when it arises. Showing notices while
  the session is idle needs a wake-up of the input loop, which no slice owns
  yet (S34's view refresh is the nearest).
- `McpManager.post_notice(level, message)` is the public way to queue a
  notice (each text once per session); `configured_exposures()` and
  `auto_enable_codemode` give discovery its input. `configured_exposures(entry)`
  is in `config.py` and `has_direct_tools` uses it.
- MCP on or off is decided once, when the session is wired:
  `RuntimeResourceOptions.mcp_enabled(configured=
  settings.builtin_extension_enabled("mcp"))`. Off means the manager gets an
  empty configuration: `load_mcp_config` is not called, nothing starts and
  nothing is reported. S21 must not register `/mcp` then (EXP-11), and S22
  has to evaluate the switch again on reload. Whether the `pipy mcp` shell
  commands look at the switch is S20a's to decide from Pi's `cli.ts`.
- `builtin:<name>` is a name in the `extensions` array and after
  `--extension`, not an extension pipy loads (DEV-11). Only `mcp` exists
  (`resource_enablement.BUILTIN_EXTENSIONS`); an unknown name after
  `--extension` is a startup error (`Unknown built-in extension: <value>`,
  Pi's text, which Pi reports as a load error). The settings rule is
  `is_builtin_extension_enabled`: the user-level array decides unless the
  project array has a `+`/`-` directive that matches, with pipy's pattern
  rules (last matching directive wins, `fnmatch`) in place of Pi's
  (`!pattern`, exact `+`/`-`).
- The config command has no interactive list in pipy. `pipy config list`
  shows `built-in:` with `builtin:mcp` (JSON key `builtin`), and
  `pipy config enable|disable builtin mcp` writes `+builtin:mcp` or
  `-builtin:mcp` into the `extensions` array of the chosen scope, read from
  that scope alone.

Decided in S20 (`servers_section.py`, the run's prompt sections):

- `render_servers_section(listings)` and its constants are in
  `mcp/servers_section.py`, a module without state;
  `McpManager.servers_section()` renders it for the servers as they are.
  Sizes are UTF-16 code units as in Pi, a summary is never cut inside a
  character, names are sorted like `localeCompare` (case-insensitive, lower
  case first on a tie), and the closing line says `search_tools()`.
- The section does not join `SystemPromptTemplate`. A run has *custom
  sections* (Pi's `sections` option): a `dict` of name to untagged text that
  starts with what the session lists (`ReplLoopScope.session_prompt_sections`,
  today only `mcp_servers`) and that `before_agent_start` hooks change in
  place. `system_prompt_sections.with_custom_sections(sections, custom)`
  applies it to the rendered sections with Pi's rule: a known name replaces
  that section in place, a new name follows the last section (so after
  pipy's `resume`), an empty text changes nothing. It works on fixed
  sections too (a session that was given only a prompt string).
- One object per run (`loop_step._RunSystemPrompt`) renders the prompt for
  the first hook (after the first-prompt wait), again for each later hook,
  and once after the hooks. The last rendering is the run's base
  prompt and its sections go to `system_prompt_input`, so the prompt text and
  the recorded sections come from one rendering. Before S20 the base prompt
  was rendered before the hooks and the sections again after them.
  `CodingAcceptedInputPreparer` has an optional port
  `system_prompt_after_hooks` for this; a hook that changed only sections
  does not force a prompt, and a suffix is appended to the changed base.
- EXT-16 is `event.system_prompt_options.sections` only
  (`SystemPromptOptions` in `extension_types.py`, exported from
  `pipy_harness.extensions`). Pi's other option fields are not there.
  Different from Pi: `event.system_prompt` is a string taken when the hook
  is called; an entry with an invalid name or a text that is not a string is
  left out with a diagnostic where Pi fails the prompt; the sections are not
  bounded in size (as in Pi), unlike the `append_system_prompt` suffix.
- The section is rendered once per run, when the prompt starts. A queued
  follow-up or a steering message inside a run does not render it again
  (pipy rebuilds sections per run, see `docs/backlog.md`).
- S22: nothing here is cached, so a reload that replaces the manager only
  has to pass the new manager's `servers_section`. S23, S36: a resource-only
  server and a registered server are listed by the same configuration rule.

Decided in S20a (`shell_cli.py`, `pipy mcp`):

- `run_mcp_command(args, McpCommandOptions(cwd, config_home, client_version,
  log, error, trust_store, create_transport)) -> int` is the port of Pi
  `runMcpCommand`, with Pi's own option parser (`--` and the second
  positional end the options). `cli.py` `main` hands `pipy mcp ...` to it
  before argparse runs; the `mcp` subparser exists only for `pipy --help`
  and the router's list of subcommands. The commands use the current
  directory and `resolve_config_home()` and take no `--cwd`: in `add` that
  option is the server's working directory.
- The commands read no settings, so `--no-mcp` and `-builtin:mcp` do not
  apply, as in Pi.
- `list` probes each enabled server on its own thread (`mcp-list`) with one
  `McpServerConnection` and closes it; an interrupted wait closes all.
- An HTTP entry gets no placeholder of its own in `list`: it fails with the
  error of the default transport factory (S15), `MCP server "<n>" is an HTTP
  server; pipy connects only to stdio servers yet`, the text a session
  reports too. S26 has nothing to remove in `shell_cli.py`.
- `login` and `logout` answer `pipy mcp <command> is not available yet.`
  and are not in the usage text; S31 adds both and the `--timeout` line. The
  login hint of `add` (CMDL-06) is printed already.
- A trust store that cannot be read fails `list`, and `add -l` before it
  writes, with the store's error.

Decided in S21 (changes in a session: `manager.py`, `connection.py`):

- `McpServerConnection.reconnect()` is Pi `reconnect`: it waits for a connect
  in progress, closes the connected client, waits until that client's
  transport reported its close, and calls `get_client()`. The dropped client
  is not reported as `disconnected`.
- `McpManager.reconnect(name)`, `set_enabled(name, enabled, in_project=False)`
  and `set_exposure(name, exposure)` return `None` or the error text, block
  their caller until the change is made, and may be called from any thread.
  They are complete operations; a caller that must stay responsive (the TUI
  manager of S34) runs them on a thread of its own. An unknown name gives
  `No MCP server named "<n>".`
- One writer of a server's entry at a time: every configuration change holds
  the publication lock from the save to the publication. `save_config` is
  the manager's `_save_config`; the file is written by an injected
  `update_config(entry, patch)` (default `update_config_file`, which calls
  `update_mcp_server_config`). The manager keeps `project_config` for
  `in_project`. File I/O therefore happens under the publication lock, never
  under the manager lock.
- Attempts. A server record's `ready` is `None` until its first attempt and
  a new event for every attempt (start, enable, reconnect), assigned under
  the manager lock. `wait_for_servers` counts an enabled server whose
  current event is not set, also while it has no connection yet. A manager
  that had no enabled server at startup starts its registering thread with
  the first attempt.
- Disable (SES-14): under the publication lock the connection is detached,
  the server's ports are dropped and the registry is published; then the
  connection is closed outside every lock, and a `closing` event of the
  server is set when that returned. Enable assigns the `ready` event at
  once and leaves the server without a connection (state `starting`, calls
  answer `is still starting.`) until `closing` is set; then it checks that
  its event is still the server's, that the server is still enabled and has
  no connection, creates the connection and connects on the caller's thread.
  This check is Pi's `isCurrent()`; there is no separate attempt token. A
  late announcement of a detached connection reaches `_register`, which
  reads the server's current connection, so it changes nothing. `close()`
  ends every such wait through a shutdown event and also closes a
  connection whose disable is still running.
- Stricter than Pi: enabling a server that is enabled saves the change and
  opens nothing (Pi would create a second connection; its manager view
  offers Enable only for a disabled server).
- Exposure (SES-15): `set_exposure` builds the server's ports again whenever
  the server has a connection, in any state. Pi registers again only while
  the server is `connected`, so there a change made while the server is
  disconnected applies at its next connect. Then the injected
  `deactivate_tools` (`NativeToolCapabilities.deactivate_indirect_mcp_tools`)
  takes the server's tools that are not `direct` out of the active
  selection, also tools `tool_search` had loaded, as Pi's `setActiveTools`
  filter does. Only names that an MCP tool has in the merged registry are
  removed: a built-in or extension tool that has the name an MCP tool would
  get stays active (Pi's filter goes by name). Without a selection nothing is
  removed, since only tools that registration activates are active then.
- `wait_for_startup(timeout=, cancel_event=)` is Pi's `pending`: it ends when
  every attempt `start()` made was registered and the report was posted.
- The callers of `ensure_active` after a change are outside the manager:
  `/mcp reconnect` in this slice, the manager view in S34.
- `set_enabled` and `set_exposure` have no caller in a session before S34;
  they are tested at the manager with in-memory servers, real `mcp.json`
  files and the stdio fake.

Decided in S21 (`commands.py`, `repl/mcp_command.py`, `/mcp`):

- `/mcp` is not a row of the built-in command registry
  (`coding/command_registry.py`). Pi registers it as a command of its
  built-in MCP extension, and pipy dispatches it at the port that
  dispatches extension commands (`SessionCollaborators.
  dispatch_extension_effect`), after them. So an extension command named
  `mcp` takes its place (the base of EXT-14, S37), with MCP turned off the
  name is not a command at all (EXP-11) and the unhandled-command notice
  answers, and the slash menu gets the name and Pi's description through
  `session_commands` of `published_command_surface`, at startup and on
  `/reload`. pipy's order is built-in, prompt template or custom command,
  extension, `/mcp`; in Pi extension commands come before templates.
- `native/mcp/commands.py` has no UI import: `McpCommand(manager,
  config_home=, notify=, select=, ensure_discovery=, run_step=)`,
  `format_status`, `complete_mcp_arguments`. `repl/mcp_command.py` adapts it
  to the session: `McpSessionCommand.dispatch(command_text)`,
  `.completer(fallback)` for the autocomplete owner's argument completer, and
  `interruptible_steps(waiter)`.
- Waits. `run_step(step)` runs a step that can take long. In a terminal and
  in a headless run with an abort signal it runs on a thread (`mcp-command`)
  while the session thread watches for Escape or the abort, as the readiness
  waits do. `/mcp` prints the status also when the wait for the startup
  connections was ended; a subcommand is then not run and says so; a
  reconnect the user stops waiting for goes on on its thread and its result
  is dropped (no `ensure_active` then). Pi's command cannot be interrupted.
- An unknown action prints the usage without waiting for the startup
  connections; Pi waits first.
- `login` and `logout` go through the server choice with an eligibility that
  is false for every server (`_uses_oauth`), so they answer with Pi's
  messages for "unknown name" and "no server uses OAuth". S30 gives the
  connection view its OAuth URL and S31 the sign-in.
- In the terminal `/mcp` without arguments prints the status as everywhere
  else until S34 builds the manager view, which is also the first caller of
  `set_enabled` and `set_exposure`.
- Not done in S21: "RPC mode prints the status". An RPC client's prompt
  went to the model without command dispatch
  (`loop_step._phase_b_intake`: `command_text` is empty for selected
  provider content), for extension commands as well, while Pi's `prompt`
  runs extension commands. X1 closed that gap of the automation modes; see
  "Decided in X1". (S21 also recorded that the prompt of `--print` and
  `--mode json` ran no command. That was not so: it went through the whole
  dispatch of a typed line, built-in commands included.)
- The status has no `overridden:` lines yet (S36) and no resource counts in
  the state of the completion items (S23).

Decided in S22 (`repl/mcp_session.py`, `/reload`, pending tool names):

- A manager still serves one loaded configuration and is not started twice.
  The session holds an `McpSession` (`repl/mcp_session.py`), which has the
  `McpServers` in use (manager, `McpDiscovery`, `McpReadiness`, and whether
  MCP is on) and replaces them on reload. The waits, `/mcp`, the prompt
  section, the notices and the session's command list ask it for the current
  ones: `McpSessionWaits` and `McpSessionCommand` take a function instead of
  an object, and `McpSession.commands` is a mapping that follows the switch.
  New objects give "warned once" and the first-prompt wait their reset.
- `McpSession.reloading()` is the reload's part (4.10). It loads the new
  `McpServers` (reads `mcp.json` and the `builtin:mcp` switch; connects
  nothing), runs the block that activates and attaches the extensions, and
  replaces the servers after it when the block accepted: close the old
  manager, show its remaining notices, `restart_mcp_tools`, then start the
  new manager and call `ensure_active`. A reload that keeps the previous
  extensions keeps the old manager; the loaded one never opened anything.
  Pi restarts its servers on every reload because its reload is not
  transactional.
- 4.4 said that S22 closes the manager before the reload is prepared. That
  would have left a refused reload without servers. Instead the running
  manager's publication lock is held while the block runs
  (`McpManager.publications_held`), so the capability value the reload was
  prepared with is the one it is accepted with. The lock order stays
  publication lock, then the capability state lock.
- A new session in the same process (`/new`, `/resume`, `/fork`) keeps the
  manager; only `/reload` replaces it. Pi closes and reloads there. pipy
  fires no `session_start` for such a switch, and the servers are not bound
  to a conversation. Documented as a difference.
- TSR-10: `ToolCapabilityState.pending_tool_names` (Pi `_pendingToolNames`).
  Names enter it in three places: `publish` on reload keeps the declared
  MCP tools of the live value (a configured filter derives the selection
  again, so they are read in the critical section of the swap),
  `restart_mcp_tools` moves the MCP tools declared at that moment out of the
  selection in the critical section that unregisters them (so a publication
  of the old servers between the two cannot lose a name), and
  `restore_active_tools` does it for `/tree`. Only names of MCP tools that no tool
  has and that the filters keep are pending. `with_mcp_registry` activates a
  pending name when its tool is offered; `set_active_tools` and
  `deactivate_indirect_mcp_tools` drop the pending names when they deactivate
  a tool (one that was declared); `drop_pending_tools` clears
  them when a run's prompt is settled (`ReplLoopScope.run_prepared`, called
  from `_RunSystemPrompt.after_hooks`, so after the first-prompt wait and
  the `before_agent_start` hooks, as in Pi `_startRun`).
- On reload the names of MCP tools leave the active selection, so a tool
  that registers after the next run started is a new tool: a `direct` one is
  activated by registration, another one is not active. A tool list change
  of a running server keeps the S17 rule (the name stays in the selection).
- "After resume": a resumed pipy session starts from the configured tools
  and restores nothing from the transcript, as Pi's CLI does (it always
  passes the initial tool names; Pi's resume case in
  `agent-session-mcp.test.ts` runs through the SDK without that option).
  The transcript restore pipy has is `/tree`, which got the pending names.

Decided in S23 (`resources.py`, resources in `connection.py` and
`manager.py`):

- `resources.py` is a port of `ext/resources.ts`:
  `create_mcp_resource_tools(exposure=, servers=, save_output=)` returns the
  three `McpResourceToolPort`s. `servers` is asked at every call and returns
  objects with `name`, `timeout_ms`, `resources_page`,
  `resource_templates_page`, `all_resources`, `all_resource_templates` and
  `read_resource` (`McpResourceServer`; `McpServerConnection` fits). The
  module also has `is_mcp_app_resource` and `settle_all` (calls run at the
  same time on daemon threads `mcp-request`, each result or exception
  returned in order, Pi `Promise.allSettled`), which `connection.py` uses
  too.
- Whatever a resource tool raises is an error result without structured
  content, so a script call raises `ToolError` with the message (Pi throws).
  A listing is one compact JSON text (`json.dumps` with `,` and `:`), under
  the 20 KB rule.
- The connection keeps `has_resources`, `resources` and `resource_templates`
  (in `ConnectionView` too). They are read at connect on a thread
  `mcp-resources` next to the tool listing, each list failing to an empty
  one; `has_resources` is the presence of the `resources` key in the
  server's capabilities, as in Pi (`!== undefined`). They are kept while the
  server is disconnected and when a later connect fails.
  `notifications/resources/list_changed` uses the refresh thread of the tool
  list; it lists one kind per round, so a slow resource listing delays a
  tool refresh (Pi runs them independently).
- The connection has the requests the resource tools make:
  `read_resource`, `resources_page`, `resource_templates_page`,
  `all_resources`, `all_resource_templates`. Each connects first when
  needed. The two for templates answer with none when the server has no
  `resources/templates/list` (Pi `withoutTemplates`). The lists are the
  server's; leaving out MCP App entries is the caller's.
- The manager owns the resource ports (`_resource_ports`, built with
  `_resource_exposure`) and publishes them in the same registry as the
  servers' tools. `_publish_changes` is the one place that brings them up to
  date and publishes; it runs after a server's registration, after a
  disable and after an exposure change (Pi `syncResourceTools`). With no
  server left the ports are published with exposure `hidden` and stay
  registered, as in Pi; S17's rule that a withdrawn tool is absent from the
  registry holds for server tools only. When the exposure stops being
  `direct` the three names leave the active selection through
  `deactivate_tools`.
- `McpToolPort.readable_resources` is `McpManager._reads_resources(server)`,
  asked at each call (TLS-12).
- `describe_state` counts the resources of a connected server. `/mcp`'s
  status lines have no count, as in Pi's `formatStatus`.
- Not built here: the one retry of a resource request after a transient HTTP
  error (Pi `withClient(..., readOnly)`), which S26 adds with the retry
  loop; image resources as images (S39).

Decided in S26 (HTTP servers in `connection.py`):

- `create_default_transport` returns a `StreamableHttpTransport` for a `url`
  entry, with the headers resolved by `resolve_config_headers` and the auth
  provider it is given (still `None` until S30). `send_timeout_ms` keeps its
  default; the config's `timeout` is the client's request timeout only.
- CON-10: `_open` runs `_connect_once` up to three times for a `url` entry,
  waiting `CONNECT_RETRY_DELAYS_MS` (250 ms, 1 s) in between; the retries
  are part of the one attempt every caller waits for, and the state stays
  `connecting`. Transient is `McpHttpError` with status 408, 429 or 5xx
  other than 501, or a network failure, which in pipy is `OSError` or
  `http.client.HTTPException` (Pi: `TypeError` from `fetch`). `close()` sets
  an event that ends the wait; the attempt then fails with the error of the
  try before the wait (the retry-wait half of CON-09).
- CON-11 and CON-12: `_with_client` (Pi `withClient`) runs `call_tool` and
  the five resource requests. A request is made at most twice: again on a
  new connection after `McpSessionExpiredError`, and, for the resource
  requests, again after 250 ms after an `McpHttpError` with a transient
  status. The wait also ends with `McpAbortError` when the caller's cancel
  event is set (Pi waits it out).
- A link whose session expired is detached at once, so the next request
  connects again, and its client is closed when no request of the
  connection's callers is in flight on it any more (`_Link.calls`,
  `_Link.expired`). Pi never closes that client. `close()` also closes the
  clients of detached links that still carry requests, because it waits for
  every started transport to report its close.
- The question S25 left: a session-expired error from the server-to-client
  GET stream changes nothing in the connection, as in Pi, where the
  connection has no error listener. The stream ends, and the next request
  gets the 404 and starts a new session.
- An HTTP transport reports no close of its own, so an HTTP server is never
  `disconnected`. The close contract of S15 holds: `close()` emits the
  close once, after the DELETE of the session (given one second).
- Until S30 a 401 is a failed connect with the error `MCP server requires
  authentication`, and an entry with `auth` is connected without a token.
- Fixture: the HTTP fake gained `postSequence` (step lists for the first
  POSTs of a method) and `sessions` (a session per `initialize`, 404 for a
  request that names no known session, `expire_sessions()`).

Decided in S28 and S29 (the rest of the client track, merged in INT2;
`oauth/flow.py`, `provider.py`, `callback.py`, `page.py`). Library only:
nothing in the product imports these modules before S30.

- `authorize_mcp(provider, OAuthFlowOptions(server_url, authorization_code,
  iss, scope, resource_metadata_url, authorization_server_metadata_url,
  fetch, cancel_event, skip_issuer_validation, skip_refresh))` returns
  `"AUTHORIZED"` or `"REDIRECT"`. The flow takes one `scope`; merging the
  configured scope with a challenge's (OAU-07) and `stepUpScope` (OAU-21)
  are S31's. Pi's `adaptOAuthProvider` is not ported; S30 builds the
  connection's provider directly.
- `OAuthClientProvider` is a Protocol of the required members; the optional
  ones (`state`, `save_client_information`, `client_metadata_document`,
  `add_client_authentication`, `invalidate_credentials`, `discovery_state`,
  `save_discovery_state`) are looked up on the object. `OAuthDiscoveryState`
  and the state document `McpOAuthState` are dicts with Pi's keys.
- Endpoints are read with `config.parse_http_url`; one it rejects is refused
  as insecure. The default fetch does not follow the redirect of a POST. A
  cancelled request always ends the flow. As in Pi, a refresh that fails
  with a time limit, a network error, an answer that is no token document or
  a failing `save_tokens` falls through to a new authorization, which S30
  has to know when it sets the 15 s limit.
- `McpOAuthProvider` keeps its `state` value until it is forgotten, so the
  "new for every sign-in" half of OAU-15 is S31's (delete `oauthState`
  before a sign-in starts). The default `client_name` of OAU-08 is set where
  the provider is created (S30, S32). The provider locks only itself; the
  lock between processes is S30's (OAU-20). The only store so far is
  `MemoryOAuthStateStore`.
- `OAuthCallbackServer(OAuthCallbackServerOptions(...))` binds in its
  constructor and raises the bind's `OSError`, which S31's fall back to a
  free port catches. `wait_for_callback(state, path=None)` does not block;
  `PendingOAuthCallback.result(cancel_event)` does, and `done` is an event
  for the race with a pasted URL. `close()` gives pages in flight two
  seconds and then drops connections.
- `oauth/page.py` is not in the module list this plan started with: it is
  the `renderPage` of OAU-17. S31 passes `render_sign_in_page` as the
  callback server's `render_page`; the default is plain text, as in Pi.

Decided in S30 (`credentials.py`, the 401 handoff in `http.py`, and their
use in `connection.py`, `manager.py`, `shell_cli.py` and `repl/wiring.py`),
in three commits:

- `McpOAuthCredentialStore(path, lock_dir=None)` is the file
  `mcp_auth_path(config_home)`. There is no storage backend to swap: tests
  use a file in a temporary directory, and stores on one file stand in for
  processes. `for_server(name, url)` is the `McpOAuthStateStore` of a
  provider, with `refresh_lock()` as a context manager (Pi
  `withRefreshLock`); `tokens` and `remove` are Pi's.
- The key is `mcp_namespace(name) + "|" + parse_http_url(url).href`, so
  without the fragment, as the provider compares server URLs.
- A write takes `auth_file_lock` of the file, reads it again and replaces
  it as a whole, mode 0600. A read takes no lock (it sees the file before
  or after a replace) and creates nothing, so `pipy mcp list` and a session
  leave no file and no changed directory mode behind for a server nobody
  signed in to; Pi locks for a read and creates the file. A file that is
  not JSON raises and is never replaced, as in Pi.
- OAU-20, deviation (DEV-14): the refresh lock is a `flock` on
  `mcp-auth-refresh-<hash>` in the lock directory, tried every 100 ms for
  25 s (`McpRefreshLockTimeoutError`). The kernel frees the lock of a
  holder that died, so there is no takeover after 20 s and no lock is taken
  from a live process (Pi's `onCompromised` case cannot happen). The file
  is removed by the holder before it lets go, and a waiter that locked a
  file no longer at the path tries again.
- HTP-06: `McpAuthProvider` keeps `token()` as its only required member;
  `on_unauthorized(context)` is looked up on the object, as Pi's is
  optional. `UnauthorizedContext` has `status`, `www_authenticate`,
  `server_url` and `token` (`None` for a request without one). Pi's context
  has the response and the transport's `fetch`; pipy's transport has no
  fetch a provider could use, so a provider brings its own. The handoff is
  in `_authorized_fetch`, used by POST and GET. A request whose cancel token
  was cancelled while the provider worked is not sent again.
- `McpServerAuthProvider(server_url, store, settings, on_challenge, fetch)`
  is Pi's `createMcpAuthProvider` with `token`, `on_unauthorized` and
  `settled`. A refresh runs on the thread of the request that started it
  (an I/O thread of the transport); the others wait on its event and get
  its exception. The default fetch is
  `create_oauth_fetch(timeout_ms=OAUTH_REQUEST_TIMEOUT_MS)` (OAU-22).
- `McpOAuthSettings` is Pi's, with snake case fields.
  `create_oauth_provider` (Pi `createProvider`) sets the default
  `client_name` `pipy` (OAU-08) and passes no document chooser yet: S32 adds
  `clientRegistration: "cimd"` there, which a refresh needs too, because a
  document client is not stored. `fixed_redirect_url` is the
  `fixedRedirectUrl` of Pi's `callbackSettings`; S31 needs the rest of that
  function for the callback server.
- Fixture: `FakeOAuthMcpServer` in `mcp_fake_oauth.py` is Pi's
  `startOAuthMcpServer` without CIMD and without `stall` (a route can be
  set to `Reply(hang=True)`): the MCP endpoint `/mcp` accepts the tokens
  its authorization server issued, answers 401 with a challenge, has
  `expire_access_tokens()`, `insufficient_scope`, `log` and `deletes`. The
  tests sign in with the library's flow and `authorization_response`
  (`_sign_in` in `tests/test_native_mcp_auth_provider.py`), which S31 can
  replace by the real sign-in.
- `McpServerConnection(credentials=None)` and `McpManager(credentials=None)`:
  the store is optional, and without one no server gets an auth provider, so
  a 401 is a failed connect as before. `repl/wiring.py` and `shell_cli.py`
  pass `McpOAuthCredentialStore(mcp_auth_path(config_home))`; S38 makes it an
  SDK option. A server URL the store refuses (user info) gets no provider
  either; its connect fails with the transport's error.
- CON-13 is `uses_oauth(entry)`; `oauth_url` is a property of the connection
  and a field of `ConnectionView`, so S31 can make `_uses_oauth` in
  `commands.py` true (it stays false: `/mcp login` has nothing to run yet).
  `challenge` is a property of the connection, set by the provider.
  `oauth_settings()` resolves `oauth.clientSecret` with
  `resolve_config_value` at each call.
- CON-14: `_needs_sign_in` is Pi's `needsSignIn`. In `_connect_failed` and
  in `_with_client` it is checked before everything else; the connection
  then detaches the client and enters `needs-auth` in one step under its
  lock, closes the client (`_drop_for_sign_in`) and raises
  `McpServerConnectionError` with `sign_in_required_message(entry)`, the
  cause chained. The state is left alone when the connection is shut down
  or has something newer than the client that was rejected, another
  connected client or a connect that started after the one that published
  it (`_enter_needs_auth`, `_Link.attempt`): a late 401
  of a request on a replaced client must not hide a working connection.
  Pi marks without that check. `sign_out` follows the same rule. The text for
  `auth.provider` (`Run /login <provider>`) came with S33 and the provider
  token.
- `sign_out()` is Pi's `signOut`, built for S31's logout: nothing calls it
  yet.
- OAU-23: `close()` first calls the provider's `close()`, after which
  the provider starts no refresh (a request thread that was about to gets
  `McpConnectionClosedError`, or sends the old token), and calls its
  `settled()` after every transport has closed, except when called from
  inside a callback of the connection. Pi has only `settled`; with threads
  a request could start a refresh after `settled()` had returned.
- User-visible change before S31: an HTTP server without a configured key
  that answers 401 is `needs sign-in` in the startup report, `/mcp` and
  `pipy mcp list`, and its texts name `/mcp` and `pipy mcp login`, which
  cannot sign in yet. `docs/mcp.md` says so under "Sign-in with OAuth"; S31
  has to rewrite that section, "What is missing" and the `/mcp login`
  paragraph.

Decided in S31 (`sign_in.py`, `pipy mcp login` and `logout` in
`shell_cli.py`; then, in a second commit, the session's part in
`manager.py`, `commands.py`, `repl/mcp_session.py` and the agent loop):

- `sign_in_mcp_server(server_url=, store=, settings=, prompt=, challenge=,
  cancel_event=, fetch=)` is Pi's `signInMcpServer`, with
  `callback_settings` (the whole of Pi's `callbackSettings`),
  `merge_scopes`, `step_up_scope` (Pi has it in `mcp/oauth/flow.ts`),
  `response_from_redirect_url` and `McpSignInCancelledError`. It blocks its
  caller; cancelling is a `threading.Event`.
- `McpSignInPrompt` has `show_authorization_url(url)`, called on the
  caller's thread, and `prompt_for_redirect_url(cancel_event)`, called on a
  daemon thread `mcp-sign-in-prompt`; it must return once the event is set
  (`wait_for_cancel` for a prompt that cannot ask). The sign-in gives it one
  second to return and goes on without it.
- The wait for the browser is registered before the URL is shown. In Pi
  both happen in one turn of the event loop; with threads a browser could
  answer before the wait existed.
- As in Pi, the refresh a sign-in tries first (OAU-24) does not take the
  store's refresh lock, and the sign-in's changes of the stored state are
  not serialized against a connection's provider.
- `oauth.clientRegistration: "cimd"` is still not acted on. S32 adds to
  the sign-in: the extra callback path of a server-specific document, and
  the rule that a stored client is replaced when the document is used.
- `McpServerConnection.clear_challenge()` is Pi's `connection.challenge =
  undefined` after a sign-in.
- `pipy mcp`: `McpCommandOptions.open_url` is Pi's `openUrl` (default
  `webbrowser.open`, failures ignored); with one, standard input is never
  asked. There is no `credentials` option: the store is always the file in
  `config_home`. `login` needs no connection for a server without OAuth and
  `logout` creates none at all. `--timeout` is parsed like
  `--oauth-callback-port`, as a decimal number.
- Fixture: `FakeOAuthMcpServer` logs `register` and has `response_iss` (the
  `iss` option of Pi's `startOAuthMcpServer`).
- `McpManager.sign_in(name, prompt, cancel_event=None)` is Pi's `signIn`: it
  blocks its caller and returns `None` or the message (`Sign-in cancelled.`,
  `Sign-in failed: <error>`, `Signed in, but <error>`, `MCP server "<n>"
  does not use OAuth.`). `sign_out(name)` is Pi's `signOut`. `close()` sets
  the cancel event of every running sign-in (the caller's own event, so no
  second event is needed) and returns when they have ended (SES-17,
  OAU-28).
- SES-18. The manager keeps Pi's `tokensAtSignIn`: a connection's callback
  notes the server's stored tokens when the connection is in `needs-auth`
  and forgets them otherwise; it reads the connection's state itself, so the
  order in which two changes are announced does not matter. `take_signed_in()`
  names the servers whose stored tokens differ now and `reconnect_servers`
  connects them at the same time (Pi `reconnectSignedIn`); a note that went
  stale is dropped there. A credentials file that cannot be read counts as
  a value of its own.
- The turn-start hook. Pi's `turn_start` handler has no place in pipy's
  extension events for built-in MCP, so the agent loop gained
  `before_turn: Callable[[], AgentCancellationReason | None]`, called at the
  top of every iteration before the turn's tools are read; a reason it
  returns ends the run as a request source does that found it cancelled.
  The session passes `McpSession.before_turn`, which looks at the
  credentials file only while a server needs a sign-in, runs the reconnects
  under the session's interrupt waiter and returns `OPERATOR_ABORT` for
  Escape, Ctrl-C or an abort; then `ensure_active` (S19) is called. Pi's
  handler cannot be interrupted.
- `/mcp login`. `McpCommand(open_url=)` is the UI a sign-in needs: `None`
  (a session without a terminal UI) answers `Signing in to MCP server "<n>"
  requires interactive mode.` With one, the sign-in runs on a thread
  `mcp-sign-in` while the session thread waits twice through `run_step`,
  for the authorization URL and for the end; in between it shows the URL as
  a notice and opens the browser, so every UI call stays on the session
  thread. Ending either wait cancels the sign-in.
- Not done in S31, left to S34 (MGR-09): the pasted redirect URL in a
  session. Pi asks through `ctx.ui.input` where it has a UI without the TUI
  (RPC mode, which in pipy runs no slash command, see S21) and through the
  manager's sign-in screen in the TUI. pipy's input dialog can neither be
  closed when the browser arrives first nor take a bracketed paste, so
  `/mcp login` in the terminal waits for the browser only and its prompt is
  `wait_for_cancel`. S34 replaces that prompt with the sign-in screen;
  `prompt_for_redirect_url` is where the pasted URL arrives. Until then a
  user whose browser cannot reach the machine uses `pipy mcp login` in a
  terminal, and the session picks the sign-in up. (The RPC side was built
  in X1, see "Decided in X1 (RPC UI requests)".)
- `/mcp logout` runs `sign_out` through `run_step`; a credentials file that
  cannot be read is reported as `Could not sign out of MCP server "<n>":
  <reason>` (Pi's command would fail).
- Stricter than Pi, after the review: `McpOAuthCredentialStore.remove` holds
  the server's refresh lock, for `/mcp logout` and `pipy mcp logout`. In Pi
  a refresh that is answered after a sign-out saves its tokens, the stored
  tokens then differ from the ones noted for the `needs-auth` server, and
  the next turn connects it again. A lock that is held for the whole wait
  (25 s) is `McpRefreshLockTimeoutError`, reported like an unreadable file.
- The sign-in watch has a lock of its own (`McpManager._watch`), taken with
  neither the publication lock nor the manager lock held, so the rule that
  the manager lock is never held while a connection is called stands (S17's
  lock-order test). Its updates (`_note_sign_in_state`, `take_signed_in`)
  read `connection.state` under it: an update that read the state before
  taking the lock could delete what a later change had noted.

Decided in S32 (the `oauth` options; `credentials.py`, `sign_in.py`,
`connection.py`, `config.py`):

- S30 and S31 had already wired `clientId`, `clientSecret`, `callbackPort`,
  `callbackUrl`, `clientName`, `scope` and `authServerMetadataUrl` through
  `oauth_settings()`, `callback_settings` and the flow. S32 added the tests
  that mirror Pi's for them (`tests/test_native_mcp_oauth_options.py`) and
  the user documentation, and built `clientRegistration: "cimd"`.
- `McpOAuthSettings.client_metadata_base_url` is DEV-06's
  `oauth.clientMetadataBaseUrl`. `credentials.callback_id(server_url)` and
  `credentials.client_metadata_document(server_url, redirect_url, base_url,
  metadata)` are Pi's `callbackId` and `clientMetadataDocument`;
  `create_oauth_provider` passes the chooser when the setting is `cimd`, so
  a sign-in and a connection's refresh name the same client. The document
  URL is the base URL's origin and path without a trailing slash, then
  `/client.json` or `/<id>/client.json`.
- `callback_id` hashes the `href` of `config.parse_http_url`, as the
  credential store's key does; for a URL that parser writes differently
  from a WHATWG parser the identifier differs from Pi's. The user hosts the
  documents, so `docs/mcp.md` gives a command that computes the identifier.
- Config, changed after S14: `oauth.clientMetadataBaseUrl` must be an
  `https` URL without user info, query or fragment (`oauth.clientMetadataBaseUrl
  must be an https URL without query or fragment`), because document URLs
  are built under it and a client ID has none of these.
- Settings made by hand with `cimd` and no base URL fail the flow with
  `oauth.clientRegistration "cimd" requires oauth.clientMetadataBaseUrl`
  (the config's text), after the check of the server's metadata.
- Sign-in: with `cimd` the callback server also serves
  `/callback/<id>`, and a stored client is dropped with its tokens (a
  document client is never stored, so a stored one was registered before).
- Fixture: a `FakeAuthorizationServer` whose metadata has
  `client_id_metadata_document_supported` knows every client whose ID is an
  `https` URL; `redirect_path` is the `redirectPath` option of Pi's
  `startOAuthMcpServer`, and `authorizations` lists the authorization
  requests.

Decided in S33 (`auth.provider`; `connection.py`, `manager.py`,
`native/provider_login_token.py`, `repl/wiring.py`):

- `McpServerConnection` and `McpManager` take `provider_token`, a function
  from a provider name to its current token or `None` (Pi `providerToken`).
  A server with `auth.provider` gets an auth provider that asks it before
  every request and has no `on_unauthorized`, so a 401 is not retried and
  the connection enters `needs-auth` (CON-14 with
  `Run /login <provider> to sign in.`). This does not depend on
  `credentials`: without a credential store the server still gets it.
  Without `provider_token` no token is sent, which is what `pipy mcp` does,
  as Pi's command passes none.
- Pi's function is `modelRegistry.getApiKeyForProvider`. pipy has no single
  place that resolves a provider's auth, and what it has (the catalog, its
  `AuthStore`) belongs to the session thread, while the token is asked for
  on request threads. `provider_login_token` therefore reads `auth.json`
  itself at every call: a stored API key (resolved with
  `resolve_config_value`), a stored OAuth login's `access` (refreshed through
  the session's `OAuthCredentialCache` for `PER_REQUEST_OAUTH_PROVIDERS`,
  sent as stored otherwise), and with nothing stored the provider's API-key
  environment variable. A stored entry owns the provider, as in Pi's
  `resolveProviderAuth`. Any failure is no token, as Pi's function catches.
  It reads the file with its own few lines and not with `AuthStore`, which
  reads a damaged file as empty: the environment's key would then be sent
  in place of a stored login.
- Whether a login is refreshed is asked at each call (`refreshes`): a
  per-request provider that no extension registered, the rule of
  `ProviderCatalogState.per_request_oauth_credential`. This is the one
  thing a request thread reads from the catalog: `extension_provider_for`,
  one lookup in an immutable map that a reload replaces by assignment. A
  snapshot on the session thread would be stale, because a reload loads the
  MCP servers before it activates the new extensions.
- The auth provider follows OAU-23: after `close()` it asks for no token
  (`McpConnectionClosedError`), and `settled()`, which `close()` calls
  last, waits for the calls that are running, since one may be refreshing
  a login whose rotated credentials still have to be saved.
- Narrower than Pi, documented in `docs/mcp.md`: a `models.json` key,
  `--api-key`, the `openai-codex` login (its own file), ambient cloud
  credentials, and the refresh of an OAuth login pipy does not refresh for
  its own requests (extension providers: pipy never calls their
  `refresh_token`/`get_api_key`). A stored `!command` key runs for every
  MCP request; Pi caches command results per process, pipy's
  `resolve_config_value` does not.
- A function `provider_token` that raises fails the request with that
  error, as a rejected `providerToken` promise does in Pi.
- No reconnect is triggered by `/login <provider>`: as in Pi, the server
  connects at the next call or with `/mcp reconnect`. The manager's sign-in
  watch never fires for such a server (it has no stored MCP tokens).
- Fixture: `FakeOAuthMcpServer.accept(token)` makes the protected MCP
  server accept a token it did not issue.

Decided in S34 (the `/mcp` manager; `manager_menus.py`, `manager_flow.py`,
`ui/components/mcp_manager_view.py`, `ui/modal_driver.py`, `ui/screen.py`,
`commands.py`, `repl/mcp_session.py`):

- Three parts, as S21 split the command. `manager_menus.py` builds a menu
  as a value (`McpMenu`) from `McpManager.servers()`; `manager_flow.py`
  (`McpManagerFlow`) is Pi's `manage`, `runAction`, `signInWithUi` and
  `runInBackground` over the protocol `McpUi`; both are in `native/mcp/`
  and import no UI, so the architecture rule that keeps `native/mcp/` free
  of UI imports stands unchanged. The
  view is `native/ui/components/mcp_manager_view.py` (`McpManagerView`).
- No async. Pi's screens are promises; here a screen is shown with the
  function that takes its outcome, and the whole flow runs on the session
  thread: the view calls those functions from its key handling, and the
  modal key loop calls `McpManagerFlow.poll()` on every pass of its 100 ms
  input wait. For that `DriveOwner` gained `poll` and
  `Screen.read_key_polling_resize` an optional second argument; an owner
  without `poll` is driven exactly as before. `poll` shows a sign-in's next
  step, and has the view build its live menus again (Pi `subscribe`); the
  view repaints only when the built menu differs.
- The view stands in place of the editor through the existing
  `search_selector` overlay, which now takes any component with
  `render(width, style)`; no overlay kind was added. A component with
  `render_within(width, height, style)` is asked to fit the rows that are
  free, because pipy's live region drops what does not fit from the bottom
  where Pi's output scrolls: the view cuts the text above its controls
  short and shows fewer list items.
- Locks. The view has a lock of its own for its state, taken by `render`
  on any painting thread; it is never held while a callback runs or a menu
  is built. `TerminalModalDriver.run_mcp_manager` does not hold the paint
  lock around the view's key handling or the flow's poll, because their
  callbacks take the manager's publication lock (`set_exposure`,
  `sign_out`), which publishing threads hold.
- Background changes (MGR-08). `set_enabled` and `reconnect` block, so
  `McpBackgroundActions.run` gives each a thread (`mcp-manager-action`).
  There is one `McpBackgroundActions` per loaded configuration
  (`McpServers.actions`), not per view, because a change may end after the
  view was closed. Its message is recorded by the thread itself
  (`McpManager.set_message`, the per-server `message` of
  `McpServerStatus`, Pi `server.message`) unless a later change of the
  same server was started; Pi compares the connection attempt.
  `ensure_active` must run on the session thread, so the thread only notes
  that a change ended (`take_finished`): the flow's `poll` asks while the
  view is open, `McpSession.before_turn` afterwards.
- Sign-in (MGR-09). The flow's `_SignIn` runs `McpManager.sign_in` on the
  thread `mcp-sign-in` and is its prompt: the URL, the request for a pasted
  redirect URL and the end travel through a queue that `poll` empties, so
  the browser is opened and every screen is shown on the session thread.
  The view's answer sets an event the prompt waits for. Closing the view
  (end of input) cancels the sign-in through `McpManagerFlow.stop()`.
  The cancel key sets the sign-in's cancel event and reports `Sign-in
  cancelled.` at once, without waiting for the thread: `McpManager.sign_in`
  does not look at the event during the reconnect that follows a sign-in
  (nor does Pi's `signIn`), and S31's `/mcp login` ended that wait with
  Escape too. Before the tokens are stored the event cancels the sign-in
  and nothing is stored; afterwards the reconnect goes on and shows in the
  server's state.
  `/mcp login` in a terminal runs the same flow with only the sign-in
  screen (`McpCommand(show_manager=)`); S31's wait through `run_step`
  remains for a session that can open a browser but has no manager view,
  which no session is today.
- `/mcp` in a terminal no longer waits for the startup connections
  (MGR-02); the subcommands still do.
- Narrower than Pi, documented in `docs/mcp.md`: the authorization URL is
  plain text, not an OSC 8 hyperlink (the frame carries only SGR
  sequences), with no click hint; the copy key is fixed to `ctrl+x`
  because pipy has no `app.message.copy` action; the title is not bold and
  the copy note has no colour of its own; the input is the selectors'
  `SearchInput`. No `overridden:` lines until S36 registers servers from
  extensions; `servers_menu` then takes them with the config errors.
- Different from Pi: a sign-out that fails is the server's message instead
  of an exception.
- Still open, not owned by a slice: notices are shown when the session
  drains them, so the startup report of a session whose first action is
  `/mcp` appears after the manager was closed (see "Decided in S19"). The
  poll hook of the key loop could show them while the session is idle.

Decided in S35 (rendering; `tool_rows.py`, `tool_renderer_lookup.py`,
`tool_renderers.py`, `ui/components/tool_loop_renderer.py`,
`repl/execution_projections.py`, `export_tool_rows.py`):

- MCP rows are drawn by `tool_rows` like the built-in tools' rows, not
  through `ExtensionTool.render_call`/`render_result`: a row's state has
  `mcp_label` (`<server>/<tool>`), the call is Pi's
  `formatToolCallWithArgs` with that title and the result is
  `_mcp_result_rows`, a port of `createMcpToolRenderers` `renderResult`
  (the first 5 visual lines at the box's content width, the hint, then
  `Full output: <path>` from `details.fullOutputPath`). The row theme has
  Pi's colour keys (`toolTitle`, `muted`, `toolOutput`); the theme handed
  to extension renderers has six colours and none of these.
- `tool_renderer_lookup.ToolRendererLookup.resolve(name)` is Pi's
  `resolveToolRenderers`: the resolvers of `register_tool_renderer` in load
  order, then what the tool itself has: `BuiltinToolRows()` for a built-in
  tool, the extension tool of that name that has a renderer, else
  `McpToolRows(label)`. The label of a registered
  server tool is `McpToolPort.label`, read from the capability value the
  provider turn pinned (`NativeToolCapabilitySnapshot.mcp_tool_labels`);
  for any other name it is `mcp_tool_label_from_name`, Pi's
  `/^mcp__(.+?)__(.+)$/`. Both renderers take a lookup or, as before, a
  mapping of extension tools; a renderer that was never refreshed (a
  session before its first provider turn, which is when a resumed session
  is drawn) has the name rule, which is RND-03.
- A title is made printable where the lookup answers (`_printable`: a
  space for every character that is not printable). A server chooses its
  tools' names, and the line renderer writes the title to the terminal as
  it is. Pi prints the label unchanged.
- EXT-15. `register_tool_renderer` is staged like a hook (a list per
  activation, frozen at the seal) and travels as
  `ActivatedExtension.tool_renderers`, `_ExtensionRuntime.tool_renderers`
  and `ExtensionRendererProjection.tool_resolvers` to the lookup of a
  provider turn. Pi loads its built-in extensions after the others, so its
  MCP resolver is the last one and a user resolver's `next()` has the MCP
  renderers; here the name rule is part of what the tool itself has, with
  the same effect. `next()` is computed once per resolver. A resolver that
  raises or returns something that is not renderers is left out (Pi lets
  the exception through). `next()` gives a marker for pipy's own rows and
  `None` for an extension tool without renderers, where Pi gives real
  renderers and the tool's definition; `renderShell` is not ported.
  Recorded in `docs/extension-api.md` "Tool rendering".
- A transcript that is drawn again takes its lookup from the published
  generation (`CustomEntryRenderer.replay_tool_renderers`: every extension
  tool with a renderer, and the resolvers), with the MCP titles of the
  provider turn that last started. Before S35 it used the live renderer's
  map, which is empty until the first provider turn and holds only that
  turn's declared tools afterwards.
- The export (RND-03). pipy's export page is not Pi's: it lists the
  entries and shows a tool call as its JSON. S35 adds Pi's `renderedTools`
  to the embedded data and the page shows them; the rest of the page is as
  it was. `export_tool_rows.render_export_tools` draws, at Pi's width of
  100, the calls that the lookup answers with MCP rows or with an
  extension's renderers (`tool_rows.render_tool_row_parts`), and
  `ansi_lines_to_html` is Pi's `ansiLinesToHtml` for what pipy's rows emit
  (foreground, bold, italic, underline; the rest is dropped). Built-in
  tools are never drawn ahead, as Pi's `TEMPLATE_RENDERED_TOOLS`. Different
  from Pi: the part of a row without a renderer is drawn as the generic
  one, where Pi leaves that part to its page; inputs are redacted before
  they are drawn (pipy's export withholds credentials, Pi's does not); the
  rows sit in a dark box whatever the page's colour scheme. `/export`
  passes `SessionExecutionProjections.published_tool_renderer_lookup()`;
  `pipy --export` and `/share` (which runs on a worker thread) use an empty
  lookup, so no extension code runs and titles come from the name rule.
- The name rule is always on. Pi has it only while its MCP extension is
  loaded; with `--no-mcp` a resumed `mcp__` call has Pi's generic row.
  Recorded in `docs/mcp.md`.
- Progress: pipy's tool output sink is append-only text (see "Decided in
  S16"), Pi replaces the partial result. The running row therefore shows
  the last line of the partial output.
- RND-04 needed no code: nested calls are rows of the `codemode` row under
  the tool's pipy name since CM1, and Pi's `CodemodeNestedCall.name` is the
  tool's name too. Tests pin it for an MCP tool, live, in the line renderer
  and in a session with the fake server.
- The line renderer (no terminal UI) titles the call the same way; its
  result output is unchanged.

Decided in S36 (servers registered by extensions; `registry.py`,
`manager.py`, `commands.py`, `manager_menus.py`):

- `registry.py` has `McpServerRegistry` (Pi `McpServerRegistry`),
  `RegisteredMcpServer(name, config, extension_path)`,
  `validated_registration(name, config, extension_path)` and
  `registration_conflict(server, registered)`. One registry holds the
  registrations of one extension generation. Different from Pi, where the
  loader checks the owner and then registers on one thread:
  `McpServerRegistry.register` does both under the registry's lock and
  raises `McpServerRegistrationError`, because pipy's extensions may call
  from any thread. `close()` makes a registry refuse every change; it is for
  the registry of the extensions a reload replaced.
- The manager takes `registered`, a function that returns the registrations
  as they are now. It is the registry's `list`, so the manager does not know
  the extension API. `start()` reads it, and `sync_registered()` is Pi's
  `mcp_servers_change` handler: under the publication lock it drops the
  registered servers that are gone or have another configuration (their
  tools are unpublished in the same step), adds the new ones after the
  servers it has, and starts their attempts. Closing and connecting happen
  on threads (`mcp-close`, `mcp-start`); a server that takes the place of
  one of the same name waits until that one's connection has closed, as an
  enable waits for a disable (S21); the manager remembers by name what is
  still closing, so this also holds when the name is registered again by a
  later change. `wait_for_servers` no longer blocks on one `ready` event
  until it is set: it looks at its servers again every 50 ms, so a wait
  ends when the last server it counted was removed or disabled, also while
  an earlier attempt of that server still waits for a connection to close
  (this changes the S18 wait for disabled servers too).
  `registered_changed()` runs
  `sync_registered` on a thread (`mcp-registered`), for a caller that must
  not block or may hold this manager's publications.
- A registered server is told from a server of `mcp.json` by its
  `registered_config`, the configuration as registered, as JSON text with
  the keys in their order (Pi `registeredConfig`): the order of
  `toolExposure` patterns decides which one matches, so another order is
  another configuration and reconnects.
- A change of `enabled` or `exposure` of a registered server lasts while
  the server stays registered with the same configuration; registering
  another configuration makes a new server from it, as in Pi.
- When the servers one change added have connected or failed, one notice
  names those that need the user, without the configuration errors (Pi
  `reportProblems` with `only`). Like every notice a text is shown once in a
  session.
- `McpManager.overridden()` has Pi's lines for the registered servers a
  server of `mcp.json` overrides, compared by namespace against the servers
  as they were loaded. `format_status` and `servers_menu` take them and show
  them after the configuration errors (SLH-02, MGR-02).
- Lock order: the registry's lock is a leaf. `registered` is called under
  the publication lock and never under the manager lock, and the registry
  calls its listener without its lock.

Decided in S36 (the extension API; `extensions/activation.py`,
`extensions/contracts.py`, `extension_hooks.py`, `repl/mcp_session.py`):

- `api.register_mcp_server(name, config)`, `api.unregister_mcp_server(name)`
  and `api.get_mcp_servers()` are methods of the activation host. The
  facade exports `RegisteredMcpServer` and `McpServerRegistrationError`.
  The owner of a registration is the extension's path label
  (`ExtensionDescriptor.path_label`), which is also what `/mcp` shows; Pi
  uses the extension's path.
- One `McpServerRegistry` per activation batch: `activate_extension_batch`
  makes it (or takes the pending batch's), every host of the batch has it,
  and it is `ExtensionActivationBatch.mcp_servers` and
  `_ExtensionRuntime.mcp_servers`. It is activation-owned mutable state
  like the hosts, so it is not part of the detached projection.
- While an extension loads (host state `open`) its registrations are staged
  on the host, as Pi queues them (`applyRuntimeChange`), and checked against
  the registry and what is staged. They enter the registry when the
  activation is committed (`McpServerRegistry.load`, all or none, no
  listener call), so an extension that fails registers nothing and a later
  extension is refused the names of an earlier one. In the states
  `committed` and `published` the methods change the registry directly;
  `committed` is included because `/reload` fires the candidate's
  `session_start` before it publishes the hosts. In `sealed` (a pending
  extension before its project is trusted) and after disposal they raise
  `ExtensionCapabilityError`.
- The errors are plain exceptions that the extension may catch, as in Pi;
  they do not record an activation failure. That differs from pipy's other
  registrations, where an invalid or duplicate registration disables the
  extension also when it catches the error.
- Pending extensions are activated one by one against an empty registry, so
  a shared name shows only when they are finalized. The later extension is
  then disabled with the new reason `duplicate_mcp_server`, the outcome a
  taken command name has on that path. Both commit paths ask
  `McpServerRegistry.load_conflict` first, so the commit itself cannot be
  refused.
- `McpSession` takes a `load` function with one argument, the function that
  returns the registrations for the manager, and `follow_extensions(source)`
  tells it where the registry of the loaded extensions is found
  (`generation_ref.current.runtime.mcp_servers`; one line in
  `repl/wiring.py`). `start()` follows that registry: it closes the one it
  followed before, when that is another one, and sets the change listener.
  A reload therefore needs nothing new: `_replace` calls `start()` after the
  new generation was published. The manager of a session with MCP turned
  off gets no `registered` function.
- The listener may run on any thread. It has the manager apply the change
  on a thread (`registered_changed`) and sets a flag; the session thread
  calls `sync_registered` again and then `ensure_active` where it shows the
  notices and when a turn starts (`McpSession._apply_registrations`). Pi
  calls `ensureDiscoveryActive` in the handler.
- The listener is set after the `session_start` handlers ran, so what they
  register is read by `start()` like a registration made while loading.

Decided in S36 (`mcp_servers_change` and the report; `registry.py`,
`extension_types.py`, `extension_hooks.py`, `repl/mcp_session.py`):

- `McpServersChangeEvent(servers)` with `name == "mcp_servers_change"`. The
  event name is one of `LIFECYCLE_EVENTS`, so its hooks travel in the
  lifecycle map of the runtime and the projection and no record gained a
  field. The lifecycle adapter got `fire_mcp_servers_change(servers)` and
  `mcp_servers_change_handled()`; `McpSession.announce_through` takes it
  (one line in `repl/wiring.py`).
- Hooks run on the session thread, and changes come from any thread. The
  registry itself keeps the registrations after each change, taken under
  its lock in the step that makes the change
  (`McpServerRegistry.take_changes`), so the events are in the order of
  the changes and belong to one registry. What is kept under the lock are
  the stored registrations themselves, which are only ever replaced; they
  are copied when they are taken, outside the lock, because copying a
  configuration can run code of its extension (a dict subclass). A queue filled by the listener
  was tried first and reviewed out: two listeners could enqueue in the
  wrong order, and one of a replaced registry could enqueue after the
  reload. `McpSession._apply_registrations` takes the changes of the
  registry it follows where the session shows the notices and when a turn
  starts, calls `sync_registered` and `ensure_active` when there are any,
  and fires one event per change. It takes once per call, so a hook that
  registers a server cannot keep it busy. Pi emits inside the registering
  call. The listener is left with one job: having the manager apply the
  change at once.
- The registry records a change whether or not anybody listens. That is
  how a change made in a `session_start` handler, before `start()` follows
  the registry, becomes an event, at startup and after a reload alike (in
  Pi the listener is set before `session_start`). `load` is not a change
  (Pi: servers registered while loading are read with `getMcpServers()`),
  and `close()` forgets what nobody took.
- The report (Pi `reportUnhandledMcpServers`) is
  `McpSession._report_unhandled`: when MCP is turned off and no extension
  has a hook for the event, each registration is shown once per followed
  registry as `Extension "<path>" error: <Pi's text>` through the session's
  notify function, in `start()` (after `session_start`, as in Pi) and after
  every change. It goes through the registrations of every change that was
  taken, so a server that was unregistered before the session thread
  looked is reported as well. pipy's MCP support is not an extension that handles the
  event, so "MCP is turned off" stands for Pi's "no handler".

Decided in S37 (design note: a tool that runs other tools;
`coding/tool_parent_runner.py`, `agent/loop.py`, `agent/nested_calls.py`,
`tools/base.py`, `extensions/command_context.py`):

- Pi gives every tool `ctx.executeTool`, so pipy cannot know before a call
  whether a tool orchestrates. Every call the model makes to an extension
  tool therefore runs as a composite parent, through what `codemode` uses:
  admission and `tool_call` hooks of the parent, the readiness wait, the
  callable snapshot (4.3), the reserved parent slot in the tool budget, and
  one `NestedToolCallService` on the session thread. A parent that calls
  nothing settles exactly like a plain call (`PARENT` settlement without
  children moves the same counters as `DIRECT`), and its result carries no
  `nestedCalls`. Built-in and MCP tools stay plain calls.
- The loop got one more port, `AgentLoop(parent_runner=)`: a function from
  the admitted call to the `AgentCompositeToolRunner` that runs it, or
  `None`. The built-in `codemode` keeps `composite_runner` and its name
  guard and is asked first. `_execute_composite` and `_run_composite` take
  the runner they are to use. A runner says with `records_always` whether
  the parent's result has the record also without calls: `codemode` yes
  (unchanged), `ToolParentRunner` no (Pi leaves `nestedCalls` off).
- The session's function is `parent_runner_for(runs_as_parent,
  execute_parent)` with two new methods of `SessionExecutionProjections`:
  `runs_as_parent(name)` (the tool of this provider turn's value is an
  extension's, `NativeToolCapabilitySnapshot.is_extension_tool`) and
  `execute_parent(call, wait_for_interrupt=, nested_tools=)`, which runs the
  parent on the turn's pinned value like `execute`. An extension's tool
  named `codemode` is such a parent too.
- Threads. The service belongs to the session thread; a handler runs on the
  executor's worker thread whenever the session has a waiter.
  `ToolParentRunner` does not run the handler itself: it calls the ordinary
  executor, so validation, the cancel event, the join timeout and the
  completion-before-cancellation rule are the executor's, and gives it
  `NestedToolBridge.serve` as the waiter. The executor calls the waiter on
  the session thread, which is where `serve` waits, with the session's own
  waiter, for a wake event that a request or the end of the parent sets,
  and runs one request at a time through `service.call`. A watcher thread
  turns the executor's `done_event` into the wake event. Without a waiter
  the handler runs on the session thread and `execute` reaches the service
  directly; a call from another thread is then refused at once, because
  nothing can serve it while the handler has the session thread (found in
  review: it blocked a helper thread the handler joined). Running the
  handler on a worker thread there as well was not done: the executor
  turns Ctrl-C during its wait into a cancelled call, where such a session
  ends today. The bridge's lock guards its queue, the wake event and
  `closed`; nothing but the session thread touches the service.
- The bridge is closed when `serve` returns (the parent ended, an
  interruption, an exception of the pipeline) and again when the runner
  returns. Closing answers every waiting and later request with `Nested
  tool calls are not available in this context` (Pi's text for a context
  without nested calls), so a handler the session stopped waiting for is
  not left blocked. An interruption of a nested call is returned by `serve`
  as the parent's interruption.
- `ToolContext.nested_tools` (`NestedToolCalls`: `tools()`, `execute(name,
  arguments) -> NestedToolResult`) carries the bridge to the tool port;
  `ExtensionModelRuntimeControl.nested_tools` carries it into the context,
  which has `tools`, `execute_tool` and `executeTool`. The public protocol
  is `ExtensionToolContext` (inventory pin 108).
- `NestedToolCallService.call(unknown_tool_error=)`: with it a name that is
  not callable is refused with that text and kept in the record, as Pi
  records every call `executeTool` was asked for. `codemode` does not pass
  it and is unchanged. `has_record()` says whether a call was kept or
  dropped.
- Narrower than Pi, in `docs/extension-api.md` "Running other tools": one
  level of nesting (a nested extension tool gets no `nested_tools`, because
  the reserved parent slot and the service are for one parent); no
  self-call; the callable tools are the snapshot of the parent's admission;
  `ToolResult` instead of Pi's outcome object, no `signal` or `onUpdate`;
  no usage is added up; no event for a name that is not callable.

Decided in S37 (the loadout hook; `tools/loadout.py`, `tools/base.py`,
`tool_capabilities.py`, `extension_types.py`):

- `ToolDefinition` gained `prepare_loadout`, and `ExtensionTool` gained
  `prepare_loadout` and `default_active` (the field S09 left for this
  slice). Both are checked at registration (`invalid_tool`) and copied to
  the port's definition.
- `NativeToolCapabilitySnapshot._loadout_changes` is now the merge of every
  declared presenter, in declared order (Pi `_applyToolLoadout`): the
  built-in `codemode` with the value's `codemode_loadout` as before, and
  every declared tool whose definition has `prepare_loadout`. S08's rule
  stays: an extension's own `codemode` does not get the built-in presenter.
  Still nothing is cached, so a function runs for each read of the
  declarations (request, hidden set, system prompt).
- `ToolLoadout` gained `registered_tools` (the tools the filters keep).
  `checked_loadout_changes` turns what a function returned into changes or
  raises; a failure is reported once per tool and error type through the
  capabilities' diagnostic function (`report_loadout_error`), and the other
  presenters' changes still apply. The report has the type of the error
  only, as `_safe_diagnostic` has (review: the text is the extension's and
  reading it runs its code). The set of reported failures lasts for the
  session.
- Public names: `ToolLoadout`, `ToolLoadoutChanges` (inventory pin 110).
- Narrower than Pi, in `docs/extension-api.md` "Presenting the loadout": no
  `getExposure`, `getNamespace` or `getPromptGuidelines` on the loadout (the
  first two are fields of the definitions; extension tools have no prompt
  guidelines); the function is called per read instead of per change of the
  active tools; a failure is a warning that names the tool, not an error of
  the extension's path.

Decided in S37 (replaceable built-ins; `repl/mcp_session.py`,
`repl/wiring.py`):

- EXT-13 needed no new mechanism. Extension tools already go over the
  built-in registry (`_merged_registry`), `codemode` and `tool_search` are
  not reserved names, `register_codemode` leaves an extension's `codemode`
  alone, `McpDiscovery` activates only a tool that declares
  `reaches_tools`, and `McpReadiness.call_wait` waits by the declared
  reach. The slice added `default_active` (previous block) and the session
  tests that mirror Pi's two cases, for both names, plus the wait.
- EXT-14: Pi leaves a replaceable built-in extension out when another
  extension has one of its tool, command or flag names
  (`omitReplacedExtensions`). pipy's MCP support is not an extension, so
  the rule is applied where the session loads its servers: the loader got a
  second argument, `replaced_by`, the name of the extension that registered
  the command `mcp`, and loads as with MCP off when it is set
  (`McpServers.replaced_by` is set only when MCP would have been on).
  `McpServersLoader` is the type; S38 (SDK) passes it.
- Which extension has the command is read from the loaded extensions:
  `McpSession(extensions=)` for those of the start, and
  `follow_extensions`, which now takes the function that returns the
  loaded extensions (`LoadedExtensions`: `mcp_servers` and `commands`; the
  `_ExtensionRuntime` of the published generation) instead of the one that
  returns their registry. `start()` calls `_load_for_extensions`: when the
  owner differs from the one the servers in use were loaded for, they are
  loaded again and the others, which were never started, are dropped. That
  is how a reload that adds or removes such an extension is handled: the
  reload's candidate is loaded before the new extensions exist, for the
  owner of the old ones. The price is one read of `mcp.json` that is
  thrown away in that case.
- The warning is Pi's text with `pipy config list` and the extension's
  name (`RegisteredCommand.extension`; a command does not keep the path
  label). It is a `warning` notice from `start()`, so it comes at startup
  and after every reload that leaves MCP replaced.
- With MCP replaced the S36 report of unhandled registrations applies as
  written: it keys on "MCP is off and no hook for `mcp_servers_change`".
- Not built: a notice for a replaced `codemode` or `tool_search` (pipy's
  built-in `codemode` is registered on demand, so "was not loaded" would be
  shown to users who never asked for it), and replacement through a flag
  name (pipy's MCP flags are not extension flags).
- `repl/wiring.py` is at 2447 of 2452 lines.

Decided in S38 (SDK; `session_builtins.py`, `mcp/options.py`,
`repl/mcp_loading.py`, `product_api.py`):

- Pi's SDK session has none of the three because they are extensions the
  CLI loads and an SDK caller adds as factories. pipy's three are not
  extensions, so the choice is a value: `SessionBuiltins(codemode=,
  tool_search=, mcp=)`, carried as `RuntimeResourceOptions.builtins`
  beside `no_mcp` and `builtin_extensions`, so it reaches the wiring and the
  loader without new session plumbing (`coding/session.py` is at its size
  bound). `None` there is the `pipy` command's (all three, by its settings
  and flags), which is what every caller but the product API has; the
  product API replaces whatever the embedder's `resources` had there. `create_product_session` and
  `open_product_session` always pass one, built from their new arguments
  `codemode`, `tool_search` and `mcp`, so a product session has none by
  default. Before this slice a product session had all three like the
  command, including the servers of the user's `mcp.json`.
- The tools: `NativeToolCapabilities(optional_builtins=)` names the optional
  built-in tools the session may register. `None` keeps the rule "a session
  of the production registry may register both". With a value the registry
  does not matter, so an embedder's own `tools` can be combined with
  `codemode` (then only MCP activates it, because `defaultTools` is read for
  the production registry only). `codemode` and `tool_search` are both
  names the settings may select without anything having them: a
  `defaultTools` entry of the user must not fail a product session that was
  not given the tool.
- MCP: `mcp_wanted` is `resource_options.mcp_enabled(...)` for the command
  and "`mcp` was passed and not `no_mcp`" for a session with builtins
  (`builtin_extensions` does not turn it on there). The
  `extensions` settings do not decide for the second (Pi: an inline factory
  is not a built-in extension the settings turn off). A replacing extension
  (`replaced_by`) turns MCP off in both; Pi's inline factory is not
  replaceable, which is recorded as a difference.
- `McpOptions` (frozen, every field `None` by default): `load_config(*,
  config_home, cwd, project_trusted)`, `create_transport`, `credentials`,
  `log_path`, `open_url`, `update_config`, `startup_wait_seconds`. The
  loader applies them where it built the defaults before;
  `McpManager` and `McpReadiness` already had the parameters. `open_url`
  travels on `McpServers.open_url` to where the `/mcp` command is built and
  comes before the rule "the browser, in a terminal only".
- The loader and `provider_token` moved from `repl/wiring.py` to
  `repl/mcp_loading.py` (`load_mcp_servers`, `mcp_wanted`). `repl/wiring.py`
  is at 2342 of 2452 lines.
- `CodemodeOptions(mode=, inline_budget=)`; `codemode_loadout(settings,
  builtins)` is what the capabilities call for the loadout, at the start and
  on reload as before.
- Narrower than Pi, in `docs/sdk.md`: `open_url` and `update_config` are
  accepted and reach the command and the manager, but a product session
  runs no slash command, so nothing uses them there today (tested through a
  session without a terminal that runs `/mcp`, and at the manager). No
  `models` option for codemode. `load_config` gets three values instead of
  an extension context. The options are validated when made.
- The SDK surface gained two names, `CodemodeOptions` and `McpOptions`; the
  types their fields take are imported from `native/mcp`.
- The example is `docs/examples/sdk_codemode_mcp.py`, with a server in the
  same process over the in-memory transport pair so that it runs offline;
  `tests/test_sdk_codemode_mcp_example.py` runs it.

Decided in X1 (prompts of the automation modes;
`coding/session_controller.py`, `repl/loop_step.py`, `automation/rpc.py`,
`automation/run_modes.py`):

- What pipy did before. The prompt of `--print` and `--mode json` was read
  as a typed line: `/mcp` and extension commands ran, but so did the
  built-in commands and the `!` shortcut, and an unknown `/name` got the
  "not handled" notice and no run. An RPC `prompt` went to the model
  without any dispatch. Pi does neither (SLH-07 to SLH-09).
- `CodingSessionController.dispatch_prompt(user_input=,
  selected_provider_content=, effects=)` is Pi's `AgentSession.prompt` up
  to the start of the run: extension command (through the existing port,
  so `/mcp` after the extensions' commands), then the resource port for a
  skill or template, then the text as it stands. It never asks the
  built-in commands. The loop step calls it instead of `dispatch_command`
  for a prompt. A prompt is a step with `CodingLoopStep.external_prompt`
  (the queue source `EXTERNAL_PROMPT`, an RPC `prompt` that is next to
  run), or a fresh line of a session with `ReplLoopScope.prompt_mode`,
  which is true when the input stream says `is_prompt` (the one-prompt
  stream of print and JSON mode). The automation observer does not decide:
  tests attach one to sessions that read typed lines. Such a turn has
  an empty `command_text`, which is what skips the hotkeys and the `!`
  shortcut. Seeds, messages an extension sends and queued steering or
  follow-up messages are not prompts in this sense, as in Pi.
- RPC settlement. A selected RPC prompt holds the worker's claim, which
  only the end of a run consumed. `dispatch_prompt` calls
  `_NativeSessionControlBridge.settle_handled_prompt()` after a command
  ran; the server's handler (`bind_handled_prompt_handler`) consumes the
  claim and publishes under the control gate, as the end of a run does. No
  `agent_settled` follows, because no run armed it.
- `data.disposition`. The reader thread answers `queued` at once for a
  prompt that went behind the run in flight. The response of a prompt that
  is next to run is owed to the worker (`_owed_prompts`, by the
  identity of the content): `handled` when its command returned, `started`
  just before `agent_start` (or at settlement for a run that ended before
  one). Which of the two a prompt is, is decided from the admission's own
  snapshot, and the reader holds `_prompt_lock` from the admission until
  the prompt is listed, so the worker neither misses a listed prompt nor
  answers a queued one. Lock order: `_prompt_lock`, then the control gate;
  the worker takes the prompt lock only outside the gate (a handled
  prompt's entry is taken before its publication under the gate). The
  worker's end, with or without an exception, answers what it still owes
  with an error (`the session has ended`), and the reader refuses a prompt
  from then on; before, such a prompt was accepted and never ran.
  `steer` and `follow_up` answer `queued`. This also closes the backlog's
  "prompt disposition" entry.
- JSON mode writes `agent_settled` only when an `agent_start` was written.
- Different from Pi, in `docs/automation-rpc.md`:
  - A notice of a command (`ctx.ui.notify`, the `/mcp` status) goes to
    stderr in print and JSON mode, where pipy has always written an
    extension's notices; Pi drops it there (SLH-11). For RPC mode see the
    next block.
  - A failed command is `pipy: extension command /<name> failed (<error>)`
    on stderr in all three modes; RPC has no `extension_error` object.
  - Not built (SLH-13): `steer`, `follow_up` and a `prompt` sent during a
    run are queued without a look at the text. One that names an extension
    command reaches the model as text (Pi refuses the first two and runs
    the third at once), and a queued skill or template name is not
    expanded. The reader thread would need the session's commands and
    resources, which belong to the session thread.
  - pipy's resource rules stay: a template named like a built-in command
    is not expanded, custom commands (`.pipy/commands`) expand like
    templates, a resource turn skips the `input` hooks (Pi runs them
    first, on the unexpanded text), and a skill that is unknown or cannot
    be read says so on stderr before the text goes to the model.
- Not changed: the terminal UI, the plain REPL and piped standard input
  without `--print` (all typed lines), the product API (`submit()` still
  sends its text to the model, see "Decided in S38"), and `get_commands`,
  which still answers an empty list.

Decided in X1 (RPC UI requests; `automation/rpc_ui.py`,
`automation/events.py`, `mcp/commands.py`, `repl/wiring.py`):

- pipy's RPC server had no `extension_ui_request` at all: it ignored
  `extension_ui_response` lines, and every notice went to stderr, although
  `docs/automation-rpc.md` described the whole channel. X1 builds what
  `/mcp` needs (SLH-11, SLH-12), not the channel for extensions.
- `RpcUi(writer)` writes the requests and waits for the answers: `notify`
  (no answer), `select` and `input` (block the caller until a response with
  the request's `id`, a set cancel event, or `close()` at the end of the
  input; a wait polls every 50 ms). `NativeRpcServer.ui` is one; the reader
  thread passes `extension_ui_response` lines to `respond`. The session
  finds it with `automation_ui(observer)` (`AutomationUi` protocol), read
  off the automation observer like the control bridge is read off the abort
  carrier, so no session field was added.
- Notices. The session's one notice sink (`_extension_notify` in the
  wiring) sends to the transport's UI when there is one. That covers an
  extension's `ctx.ui.notify` and every notice of MCP (the `/mcp` status
  and messages, the startup report, registered-server changes), all of
  which are `ctx.ui.notify` calls in Pi. The text is sent as it is; the
  terminal's label sanitizing does not apply to a JSON value. pipy's own
  diagnostics stay on stderr in every mode: the `pipy:` lines and the tool
  warnings of the capability value (`_local_notify`), whose one warning for
  an unavailable `codemode` is pinned on stderr for all three modes.
- `/mcp` with an RPC client: `select` is the client's (`_mcp_select`),
  `open_url` is the browser as in a terminal (Pi's `openUrl` runs in RPC
  mode too), and `McpCommand(ask_input=)` asks for the pasted redirect URL.
  The sign-in without the manager view (S31's `_RunningSignIn`) is thereby
  the RPC path: its prompt asks only after the session thread has sent the
  URL notice and asked for the browser (`url_announced`), so the client
  sees notice, then `input`, as in Pi. The answer `None` cancels the
  sign-in, as the sign-in function already had it.
- This closes what S31 left for a UI mode without the TUI: the pasted
  redirect URL in a session (SLH-04 second sentence, OAU-26 and OAU-27
  there). S34 had closed the terminal side with the sign-in screen.
- Different from Pi, in `docs/automation-rpc.md`:
  - `ctx.has_ui` stays false for extensions in RPC mode, and an
    extension's own `select`, `confirm`, `input` and `editor` return their
    defaults without a request. Building that means an `ExtensionUiDriver`
    over `RpcUi` and deciding which thread may block; nothing in the MCP
    rows needs it.
  - No `confirm`, `editor`, `setStatus`, `setWidget`, `setTitle`,
    `set_editor_text` requests and no `timeout` option.
  - A `select` or `input` that the client never answers holds the session
    until the input ends; an RPC `abort` ends the waits of `/mcp` that go
    through `run_step` (the startup wait, a reconnect, a sign-in), but not
    a `select`. Pi's dialog also waits without a limit.
  - A waiting request is polled, so it ends up to 50 ms after its cancel
    event.

Decided in S39 (images, part 1: content model, session file, replay;
`agent/content.py`, `tools/base.py`, `agent/messages.py`,
`assistant_blocks_json.py`, `session_tree.py`):

- `ImageContent(data, mime_type)` is Pi's image block: base64 data and a
  MIME type, checked for type only (Pi checks nothing; a producer that takes
  data from a script checks it first, see part 4).
- A tool result keeps its text where it was (`ToolExecutionResult.output_text`,
  `AgentToolResultMessage.content`) and gains `blocks`: Pi's ordered `content`
  of `TextContent` and `ImageContent`, present only when the result has an
  image. The text is then the text blocks joined by newlines, which is how
  Pi's adapters and rows join them, and the constructors refuse blocks that
  say otherwise. Blocks without an image are dropped, so such a result equals
  one built from its text. Every reader that shows or measures text is
  unchanged. `blocks` takes part in history equality, since images are sent.
- Kept in order rather than as "text plus images" because Pi's Anthropic
  and Bedrock adapters send the blocks in order; the other adapters join the
  text and append the images (part 2).
- Session file: a `tool` message with an image has `blocks` in Pi's shape
  (`{"type":"text","text"}`, `{"type":"image","data","mimeType"}`) next to
  `output_text`; a result stored without its call keeps them when the file
  is written again. JSON and RPC `toolResult` messages carry the same list
  as `content`; `tool_execution_end.result` stays the text.
- Estimates: an image counts 4800 characters in `estimate_message_tokens`
  (Pi `ESTIMATED_IMAGE_CHARS`) and as one image (4096 tokens) in
  `estimate_request`, where `image_count` was the attachments only.
- A `tool_result` hook that replaces the content replaces the images too
  (Pi: the handler's `content` is the whole content); one that returns the
  text unchanged keeps them. The hook is not shown the images, and an
  extension tool cannot return one: `ToolResult.content` stays a string.
- A compaction summary request quotes a result's text only (Pi
  `serializeConversation`); the bounded nested-call record keeps no image.

Decided in S39 (images, part 2: provider adapters;
`providers/*_wire.py`, `_provider_helpers.py`, `providers/replay_content.py`,
`agent/messages.py`, `repl/loop_step.py`):

- The four wire families send a tool result's images as Pi's adapters do
  (table in `docs/providers.md`): Anthropic and Bedrock the ordered blocks,
  Responses a list of `input_text` and `input_image`, Gemini inside the
  function response or, before Gemini 3, in a following user turn, Chat
  Completions in one user message after a run of `tool` messages. A later
  system message between two results does not split that run.
- A text-only result is sent exactly as before, including an empty text;
  Pi's `(no tool output)` for an empty result is not adopted here.
- Pi `downgradeUnsupportedImages` is `without_tool_result_images`, applied
  where the session builds a request, not in each adapter: adapters do not
  know the catalog. `ProviderMutationEffects.request_history(binding,
  messages)` is the one projection (`ReplLoopScope.request_history`), used
  for the turn's request (`_request_values`) and for the retained history an
  automatic compaction measures (`_select_automatic_cut`), so both count
  what is sent. `model_takes_images` reads the `input` of the model's own
  catalog row: a model without a row (also one that borrows its provider's
  base row in `resolve_spec`), and a session whose provider was passed in,
  is sent the images. The stored history is never changed.
- The `fake` catalog row takes images, like Pi's faux provider.
- `copilot_dynamic_headers` sets `Copilot-Vision-Request` when a tool result
  in the request holds an image, for all three Copilot adapters.
- Not built: the Mistral adapter's own shape (images inside the `tool`
  message, `[tool error]` prefix) and `requiresAssistantAfterToolResult`;
  attachments of the user's turn are still sent whatever the row says.

Decided in S39 (images, part 3: rows and export;
`tool_result_display.py`, `ui/state.py`, `ui/components/session_history.py`,
`export_tool_rows.py`, `export_distribution.py`):

- pipy's frames carry no inline images (the frame pipeline keeps SGR only,
  see "Decided in S34"), so a row shows what Pi shows in a terminal without
  an image protocol: after the result's text one line per image,
  `[Image: [<mime>] <w>x<h>]` (Pi `getTextOutput`, pi-tui `imageFallback`
  and `getImageDimensions`, ported for PNG, JPEG, GIF and WebP).
  `tool_result_display_text(result)` builds that text; the three places
  that hand a result's text to a row use it: the live result (`ui/state.py`,
  which also feeds the captured renderer), the redraw of a stored result
  (`session_history.py`) and the export rows. An extension's `render_result`
  therefore sees the lines in its content.
- Export: the entries keep `blocks`; a drawn row has the image line (Pi
  draws export rows with `showImages: false`); the page adds a "Tool result
  images" entry with `<img>` elements after every result that has images,
  where Pi's page shows images for `read` only. The base64 data of an image
  block is not passed through the text redaction.
- Not built: inline images in the terminal (Pi's `Image` component with the
  Kitty and iTerm2 protocols, `terminal.showImages`, `imageWidthCells`).

Decided in S39 (images, part 4: MCP image blocks and image resources;
`mcp/tools.py`, `mcp/resources.py`):

- `model_content(content)` replaces `render_model_text`: the converted
  blocks of a result become the tool result's text and `blocks`, in the
  server's order (TLS-11, RES-09). `convert_mcp_result`, `to_model_content`
  and `limit_mcp_content` already produced the image blocks and put them
  after a shortened text (TLS-15); nothing there changed.
- Like Pi, pipy does not check a server's image (type, base64, size). An
  image block with empty `data` or `mimeType` cannot be an `ImageContent`
  and becomes the text `[image <mime> without data omitted]`; Pi passes it
  on and the provider rejects it.
- Tests: `test_native_mcp_session_images.py` runs a session against the
  fake stdio server (a tool with an image block, an image resource), checks
  the request in all four adapter families and a resumed session's request.
- Codemode `image()` (CMD-28 to CMD-30) is the next commit of the slice.

Decided in S39 (images, part 5: codemode `image()`; `codemode/prelude.py`,
`codemode/host.py`, `codemode/outcome.py`, `codemode/result.py`,
`coding/codemode_runner.py`, `tools/codemode.py`):

- Guest: `image(value)` is Pi's prelude `image` in Python
  (`prelude.checked_image`): a `data:` URL string, a dict with `image_url`,
  or an image block dict; the same checks and error texts (CMD-28). The
  type is detected from the base64 prefix (`image_type`), as in Pi. A new
  guest message `{"type":"image","data","mimeType"}` carries it.
- Host: the message is checked again with the prelude's own functions
  (`is_base64`, `image_type`); one that fails is a protocol violation,
  since the prelude never sends it. `ScriptOutcome.output` holds
  `str | ScriptImage` in order (`texts` is the text view).
- Limits, different from what this plan's S39 entry said ("counted against
  the existing emission cap"): images count against `output_messages`, but
  their bytes against a cap of their own, `ScriptLimits.image_bytes` =
  16 MiB (Pi's 16,777,216 characters), not the 1 MiB of text. With one cap
  of 1 MiB a script could not show a screenshot that a tool reply (up to
  16 MiB) had just handed it. An image line may be longer than
  `line_bytes`, up to the image cap; the host tells it by the line's first
  bytes while it buffers and refuses it at parse time when it is not an
  image. The guest raises `ValueError` at the same total
  (`prelude.MAX_IMAGE_CHARS`; a test pins that the two match).
- Result (CMD-29, CMD-30): `format_result` keeps images between the text
  items (`ResultText.header`, `.items`); the budget is for the text items
  joined by newlines, and a truncated text is followed by all images.
  `result_content` (runner) saves each image once to an owner-only temp
  file `pipy-codemode-*.<ext>` and puts `[Image saved to <path> (<mime>,
  <size>)]` before each occurrence, or `[Image (<mime>, <size>) could not
  be saved: <reason>]`. The text of a result without images is byte for
  byte what it was.
- pipy has no `==> text N/M <==` separators and no console block (DEV-04),
  so the text around images is joined as pipy joins output items.
- A result's text is at most 64 KiB (`AgentToolResultMessage`): a script
  that shows some hundreds of images makes more label text than that and
  gets a sandbox-failure result instead of its output. Not handled further.
- Tests: `test_native_codemode_images.py`, including the real guest and a
  session in which a script shows an MCP tool's image block (the mirror of
  Pi's "attaches only the images the script passes to image()").

Decided in S40 (closeout, part 1: the conformance recipe;
`scripts/mcp_conformance/`):

- `run.py` and `client.py` are ports of Pi's `test/mcp-conformance/run.ts`
  and `client.ts`; `just mcp-conformance [options]` runs `run.py`. The
  client under test uses `McpServerConnection` with the default transport
  factory and `sign_in_mcp_server`, as the plan asked. pipy has no
  in-memory credential backend, so the driver's `mcp-auth.json` is in a
  temporary directory that is removed at the end; nothing under the config
  home is read or written.
- The extra check is named `pipy-client` (Pi `pi-client`), the report file
  variable `PIPY_MCP_CONFORMANCE_REPORT`.
- Result of the first full run (2026-10-13, suite `0.2.0-alpha.11`, 26
  scenario runs in the three modes): pipy's `baseline.json` equals Pi's at
  the plan's Pi commit, check for check, with `pipy-client` for `pi-client`.
  The bar of section 6.4 (no failing check that passes in Pi's baseline) is
  met; no product code was changed for it.
- `tests/test_mcp_conformance_recipe.py` runs in `just check` without Node:
  the comparison rules, the reading of `checks.json` and of the client's
  report, the committed baseline (pinned suite, three modes, and exactly
  the known failing checks, so a regenerated baseline with a new failure
  does not pass), and the client under test against the repository's fake
  OAuth MCP server (a sign-in through the simulated browser, and the three
  sign-ins after which the simulated user gives up).
- Different from Pi's runner: `--mode` is checked by the argument parser
  (exit status 2 for an unknown version, Pi prints a message and exits 1);
  a missing `npx`, and a temporary directory whose path has a space (the
  suite splits `--command` on spaces), are reported before anything runs;
  a `checks.json` that is not JSON or not a list fails the `runner` check
  instead of ending the run with an exception.
- Not in CI, as DEV-12 says.

Decided in S40 (closeout, part 2: the user docs pass; `docs/mcp.md`,
`docs/usage.md`, `docs/index.md`, `CHANGELOG.md`):

- `docs/mcp.md` grew slice by slice, with most sections as subsections of
  "Quick setup". It is now in the order of DOC-01 and of Pi's page: quick
  setup, configuration, migration, the servers in a session (`/mcp`, the
  manager, the startup report, reloading, turning MCP off, diagnosing), the
  `pipy mcp` commands, OAuth, exposure, tool search, resources,
  permissions, extensions and SDK, then the reference sections
  (connections, tools, conformance, what is not supported, the library).
  Sections were moved whole. Heading texts did not change, so every anchor
  other documents link to is the same; only heading levels did ("Control
  tool exposure", "Use resources" and "Permissions" are second-level now),
  and the three OAuth sections moved from "Server connections" under a new
  "Authenticate with OAuth".
- New text, all of it describing behaviour that earlier slices built and
  tested: the introduction, "Migrate configuration from another client"
  (Pi's table, plus a row for Pi itself), "Manage servers in a session",
  "Diagnose connection problems", the introduction of "Authenticate with
  OAuth", a `toolExposure` example, `defaultTools: ["+codemode"]` without
  servers, the parent of a script's call under "Permissions", "Extensions
  and SDK", and "What pipy does not support".
- "What pipy does not support" lists, with the six things Pi does not
  implement either (legacy SSE, elicitation and sampling, prompts, resource
  subscriptions, MCP Apps, the stateless protocol), what only pipy lacks:
  Windows, a built-in Client ID Metadata Document, images drawn in the
  terminal, parallel calls of one script, the clickable sign-in link, and
  commands in queued RPC input.
- Pi's page says that calls made from codemode scripts carry the codemode
  call id as `parentToolCallId`. In pipy that id is in the JSON and RPC
  events; a `tool_call` handler is given the tool name and the arguments
  only. The page says so.
- `docs/usage.md` "MCP commands" listed four of the six commands in its
  synopsis; `login` and `logout` are added (DOC-02).
- `CHANGELOG.md`: entries written while the feature was built in parts said
  what was "not yet" there ("no session uses yet", "OAuth is not built
  yet", "`pipy mcp login` and `logout` do not exist yet", "which you still
  have to enable yourself", images "omitted for now"). Those clauses are
  removed, since later entries of the same release add the parts; nothing
  else in the entries changed.

Decided in S40 (closeout, part 3: the row audit;
`docs/acceptance/2026-10-13-mcp-parity.md`, `tests/test_mcp_parity_audit.py`,
sections 3.1 and 3.2 of this plan):

- The acceptance note has one line per row of section 2: the slice, the
  state (`closed`, `closed, with limits`, `part open`, `open`), tests that
  pin the row, and the limit or the DEV rows that apply. It also has the
  count of mirrored Pi cases per test file (section 6.3), the result of the
  conformance run and what the note does not show.
- How a row was read: many tests name their row in a comment; the others
  were matched by the test names of the module that implements the row and,
  where a name left doubt, the test's body. A second pass checked three
  things by program: every test the note names exists; the texts the rows
  quote are in the tests (of 174 texts, 127 in a test the row names; the
  other 47 were read one by one and are notation, asserted elsewhere, or
  one of two Pi texts pipy does not print, see DEV-39 and SLH-13); and the
  count of mirrored Pi cases, taken again from the Pi files at the reference
  commit. Parts of rows with several clauses were then read against the
  tests by sample. The audit did not run tests against a changed
  implementation and did not compare with a running Pi.
- `tests/test_mcp_parity_audit.py` runs in `just check`. It fails when a
  row of section 2 is missing from the note or listed twice, when a named
  test does not exist, when a row without a test is not marked open, when
  the stated row counts are wrong, when a DEV row the note names is not in
  section 3, and when a row that table 3.1 names does not cite that DEV row
  or is listed as plainly `closed`. It does not prove that a test covers its row. Its last five
  tests are the pins of DOC-01 to DOC-05: the headings and terms those rows
  ask for are in the user documents and the changelog.
- Five Pi cases had a pipy test without the name comment; the comment was
  added. For one, "routes tools whose names differ only in - and _", a test
  was added at the manager
  (`test_calls_reach_tools_whose_names_differ_only_in_dash_and_underscore`).
  The count of section 6.3 was corrected: `agent-session-mcp.test.ts` has 48
  cases at the reference commit, not 44.
- Section 3 gained 3.1: the differences from Pi that the slices recorded in
  their "Decided in" blocks and in the user documents, as DEV-15 to DEV-62,
  each a proposal for the final judgement. The table of DEV-01 to DEV-14
  was broken by a blank line before DEV-13; that line is gone.
- Result: section 3.2. Two rows are not closed (SLH-13, and MGR-09 in
  part); no product code was changed to close them in this slice.
- Docs pass: `docs/backlog.md` said that no slice had landed and counted
  280 rows; `docs/extension-api.md` "Tools" said that no tool name may
  shadow a built-in, which "Replacing built-in tools" on the same page
  contradicts for `codemode` and `tool_search`. Both are corrected. The
  other pages the slice names (`docs/mcp.md`, `docs/codemode.md`,
  `docs/sdk.md`, `docs/automation-rpc.md`, `docs/usage.md`,
  `docs/index.md`) were read for statements that a built part is missing or
  a missing part is built; none was found after part 2.
- Not done, and not a row: the live evidence of section 6.5.

Open after the merge, owned by later slices:

- Fixtures: the stdio fake is single-threaded, so a `sleep` step blocks it.

Changed modules, by seam:

| Seam | Module | Change |
| --- | --- | --- |
| Tool definition | `tools/base.py` | `ToolDefinition` gains `exposure`, `namespace`, `annotations`, `output_schema`, `reaches_tools`, and a foreign-schema mode. `ToolExecutionResult` gains `structured_content`. |
| Capability value | `tool_capabilities.py` | A third registry (`mcp_registry`); declared set and callable set derived from exposure; pattern matching for filters; the MCP allowlist rules. |
| Request snapshot | `agent/request.py`, `agent/loop.py`, `agent/nested_calls.py` | The frozen snapshot keeps the declared names. Composite admission captures a callable snapshot (4.3). The built-in name guard in `NestedToolCallService` is replaced by that snapshot. |
| Codemode | `codemode/prelude.py`, `codemode/host.py`, `coding/codemode_runner.py`, `tools/codemode.py` | Structured values in replies, discovery messages, `ALL_TOOLS`, dynamic description. |
| Prompt | `system_prompt_sections.py`, `agent/system_messages.py`, `repl/loop_step.py` | The `mcp_servers` section is one of a run's custom sections (`with_custom_sections`, see "Decided in S20"); the existing section diff appends changes. |
| Extensions | `extension_types.py`, `extensions/activation.py`, `extension_hooks.py` | Tool metadata, `reaches_tools`, MCP server registration, `mcp_servers_change`, nested execution, loadout hook. |
| Settings and CLI | `settings.py`, `cli.py`, `resource_enablement.py` | `codemode.mode`, `codemode.inlineBudget`, `-builtin:mcp`, `--no-mcp`, patterns, the `mcp` subcommand. |
| Session lifecycle | `repl/wiring.py`, `repl/reload.py`, `coding/session_controller.py`, `automation/rpc.py` | Create, start, reload and shut down the manager; first-prompt wait; turn-start hook. |
| OAuth plumbing | `auth_store.py` | `resolve_config_value` and `auth_file_lock` are reused, not changed. |
| Rendering | `tool_rows.py`, `tool_renderer_lookup.py`, `tool_renderers.py`, `ui/components/tool_loop_renderer.py`, `export_tool_rows.py`, `export_distribution.py` | MCP rows, the lookup that says which rows a call gets, and the rows of an HTML export (see "Decided in S35"). |

Decided in X2 (leaked server processes; `pipy_harness/exit_signals.py`,
`tests/mcp_fake_server_guard.py`, `tests/fixtures/mcp_fake_server.py`):

- What was found. Orphaned fake servers (parent pid 1) were alive hours
  after test runs. One was left to look at: its scenario was the one of the
  TUI driver (`initialize` sleeps 600 s, the end of stdin is ignored), and
  it was started by a developer script that killed the session with
  SIGKILL, as the PTY tests' `_finish` does after a failed assertion.
- What was tried, with a server that never answers `initialize` and ignores
  the end of its stdin. A run of all MCP test modules, when they pass,
  leaves no process. A session ended by `/exit`, Ctrl+D, SIGINT or a closed
  terminal closes the server. A session ended by SIGTERM or SIGHUP did not:
  Python's default action ends the process without `finally` blocks and
  without the `atexit` hook of STD-06. The same held for print mode, RPC
  mode (whose page already promised status 143 and 129) and `pipy mcp
  list`. No leaking path was found inside a living pipy (reconnect while
  connecting, disable, exposure change, reload, failed or timed-out
  connect): the tests of those paths leave nothing behind.
- The fix in pipy: `exit_on_termination_signals()` around `cli.main`. The
  first SIGTERM or SIGHUP raises `SystemExit(128 + signal)` on the main
  thread, so the command unwinds as for any other exit: the session's
  `_close_session` runs `McpManager.close()` (STD-05) and the `atexit` hook
  (STD-06) still runs. Pi reaches the same through its per-mode handlers
  (`interactive-mode.ts`, `print-mode.ts`, `rpc-mode.ts`
  `registerSignalHandlers`) that dispose the runtime and call
  `process.exit`. Later signals are ignored. A signal that is ignored or
  has a handler when pipy starts is left alone; off the main thread nothing
  is installed, so an embedded session is unchanged. DEV-63 has the
  differences from Pi.
- A signal can arrive while a finished run is already closing its servers.
  The exit then interrupts that close on the main thread. Therefore the
  exit hook of STD-06 (`stdio._terminate_live_process_groups`) finishes the
  job: SIGTERM to every tracked group, SIGKILL to a group that is still
  tracked when its transport's `close_timeout_ms` is over, and it returns
  when the watcher threads stopped tracking the servers (they do so before
  they reap, under the hook's lock, so the hook cannot signal a reused id),
  or one second after the last SIGKILL. Once the hook runs, a watcher sends
  SIGKILL to the group of a server that exited, before it reaps it, so
  children that ignored SIGTERM do not outlive it. The hook is the backstop
  for every exit that did not close a server, also a worker thread that did
  not end in time.
- RPC mode runs the session on a worker thread; its `run()` waited up to 120
  seconds for the run in flight and the queue before it let the session
  end. On the `SystemExit` of a signal it now aborts the run
  (`request_abort(discard_follow_ups=True)`: the `abort` command clears only
  queued steering, this also clears the queued follow-ups) and the running
  direct-bash commands, skips that wait, and then ends the session as before
  (Pi's `shutdown` disposes the runtime at once). The end of input still
  drains. Follow-ups queued by extensions are not cleared there.
- Not solved, and not solvable in pipy: SIGKILL. A server that ignores the
  end of its stdin then stays, as with Pi.
- The test side is in section 6.1: the fake server ends itself after its
  parent ended, the PTY teardown ends the servers of a killed session, and
  a suite guard fails a module that leaves a fake server behind.

### 4.2 Tool model

Pi's MCP support rests on five properties of a tool that pipy does not have
yet. They are added to `ToolDefinition` once and used by native, extension and
MCP tools alike:

- **exposure** (`direct`, `model-only`, `codemode`, `deferred`, `hidden`),
  default `direct`. Existing tools are unchanged.
- **namespace** (`name`, `aliases`, `description`, `instructions`), optional.
- **annotations**, the four boolean hints, optional.
- **output schema**, optional. A tool with one returns `structured_content`
  next to its text.
- **reaches_tools**, optional, a function from the call input to `"all"` or
  a tuple of words.

`ToolDefinition` validates its input schema against pipy's strict subset
today, caps descriptions at 4 KiB and is constructed in many places. MCP
schemas are arbitrary JSON Schema and descriptions are not bounded by Pi. The
definition therefore gets an explicit mode: `schema_mode="strict"` (default,
today's behaviour) or `"foreign"`. A foreign definition requires only a JSON
object with `type: "object"`, has no description cap, and is validated at call
time by a separate standard-library validator (DEV-10) for this keyword set:
`type` (including arrays of types and `number`, `null`), `properties`,
`required`, `additionalProperties`, `items`, `prefixItems`, `enum`, `const`,
`anyOf`, `oneOf`, `allOf`, local `$ref`, numeric and length bounds and
`pattern`. Unknown keywords are ignored. Provider encoders already copy
schemas through `materialize_tool_input_schema`, which accepts any JSON value;
S01 adds a test per adapter family that a foreign schema reaches the request
unchanged.

`ToolExecutionResult.structured_content` and the same field on
`AgentToolResultMessage` are never sent to a provider. S01 decides, by reading
Pi's `core/agent-session.ts` tool-result path (lines 698 and 720 at the
reference commit), whether Pi persists the field in the session file, and
follows it.

Decided in S01: not persisted. Pi keeps `structuredContent` on the in-memory
`AgentToolResult` (hooks, `tool_execution_end`, nested callers), and
`createToolResultMessage` in `packages/agent/src/agent-loop.ts` builds the
stored `ToolResultMessage` from `content`, `details`, `usage` and `isError`
only. pipy's session file therefore omits the field, and a resumed result has
none.

Also decided in S01: the foreign validator does not convert argument types.
Pi's `validateToolArguments` coerces (`"3"` to `3`, optional `null` dropped)
before it validates; pipy passes foreign arguments through unchanged and
reports a mismatch to the model, under DEV-10.

Decided in S02:

- pipy had no `get_all_tools()`. It is added on the handler context
  (`ctx.get_all_tools()`), next to `ctx.set_active_tools(...)`, not on the
  activation API as Pi's `pi.getAllTools()`: pipy's activation API is closed
  after `activate(api)` returns. An entry (`ToolInfo`) carries name,
  description, input schema, exposure, namespace, annotations and the reach
  function. Pi's `promptGuidelines` and `sourceInfo` are not reported; pipy
  tools have neither yet.
- Registration is open only during activation, so EXT-04 reads: an extension
  that registers one of its own tool names again replaces the earlier
  registration (Pi `extension.tools.set`), and a tool whose last registration
  is `hidden` is registered but never declared. A session sees a withdrawal
  after the next activation (`/reload`). A name another extension registered
  stays a duplicate that disables the second extension; Pi lets the first
  extension win silently.
- `hidden` is the one exposure S02 enforces (the definitions a capability
  value projects leave hidden tools out). The other exposures are stored and
  reported; S03 makes them decide the declared and callable sets, and sets
  the `codemode` tool's own exposure to `model-only`.
- `codemode` declares `reaches_tools` with the Python discovery names of
  DEV-01, ahead of the helpers themselves (S07).

`ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH` (64 KiB) stays. MCP text is
already cut to 20 KB plus a short frame before it becomes a result.

### 4.3 Declared and callable sets, and the frozen snapshot

Today one set, `active_tool_names`, is both what a request declares and what
codemode may call (intersected with the seven built-in names). Pi separates
them (EXP-01, EXP-04). `ToolCapabilityState` computes both from one immutable
value:

- **declared** = active names whose tool is registered, not `hidden`, allowed
  by the filters (EXP-06 to EXP-09), minus declarations hidden by
  `codemode.mode: "only"`.
- **callable** = active `direct` tools, plus every registered `codemode` and
  `deferred` tool that the filters keep registered, minus `codemode`,
  `model-only` and `hidden` tools.

The provider request snapshot stays frozen per request and keeps the declared
set; a direct model call is authorized against it, which gives EXP-03.

Codemode needs one change to the CM1 rule "only tools advertised in the
parent's frozen request snapshot are eligible". MCP tools with the default
exposure are never advertised, and a server may finish connecting between the
request and the script (SES-07 makes the script wait for exactly that). The
rule becomes:

> When a composite parent is admitted, after the readiness wait and the
> `tool_call` hooks, the loop takes one **callable snapshot** from the live
> capability value: the callable names bound to the registry and executor of
> that one `ToolCapabilityState`. The snapshot is immutable for the life of
> the parent. Every nested call is authorized against it and executed by its
> executor. A capability value published later (reload, a server connecting,
> `set_active_tools`) does not change a running script.

This keeps the property CM1 protected (a script never sees a half-published
generation and its eligibility cannot change under it) while allowing tools
that were registered after the request was built. `NestedToolCallService`
keeps its session-thread ownership, its bounded records and its sequential
rule (G2 and G3 replaced the sequential rule: calls run at the same time); `BUILTIN_NESTED_NAMES` is deleted and `eligible_names()` returns the
snapshot's names. The CM1 spike spec section 4.7 and `docs/codemode.md` are
updated in S03.

Decided in S03:

- "No selection" (`active_tool_names` is `None`) means the tools that
  registration activated, the `direct` and `model-only` ones, not every
  registered tool. A filter (`--tools`, `--exclude-tools`) derives the active
  set the same way: it keeps a tool registered, and only a declarable tool is
  activated by being kept. A reload activates new extension tools, and tools
  whose exposure became declarable, only when they are declarable.
- A tool that a `before_provider_request` hook withheld from the parent's
  request (`ProviderRequest.hidden_tool_names`) is not in that parent's
  callable names. pipy's request hooks narrow monotonically and a direct call
  to a withheld tool is an unknown tool; a script must not get around that.
  S08 (`codemode.mode: "only"`) hides declarations by another mechanism and
  must not put those names into `hidden_tool_names` if they are to stay
  callable. (S08: they are in a field of their own,
  `ProviderRequest.undeclared_tool_names`. See "Decided in S08" in 4.9.)
- The session takes the callable snapshot as it takes a provider turn's view:
  under the generation lock, the live capability value bound to the
  generation id of the published extension generation
  (`SessionExecutionProjections.callable_snapshot`). The `tool_call` and
  `tool_result` hooks of nested calls stay those of the provider turn.
- A name outside the callable names is refused by the nested service before
  admission (no budget, no events), so a nested call is never settled as
  `unauthorized`. The admission policy still checks the snapshot's names.
- A composite never reaches itself: the service removes the parent's own tool
  name, in addition to `codemode` being `model-only`.
- The codemode tool description still names the seven built-ins. S08 makes it
  dynamic; until then extension tools are callable but not listed. (Done in
  S08.)
- Declared tools are not checked against the filters again when an extension
  activates a filtered-out name with `set_active_tools`; the callable set is.
  S10 owns the filter rules (EXP-06 to EXP-09). (Done in S10.)

Decided in S10 (patterns and the MCP rules of the filters):

- `create_tool_name_matcher` and `is_mcp_tool_name` live in the
  standard-library leaf `native/tools/name_patterns.py`, not in
  `mcp/config.py`, because the capability value needs them before the MCP
  package exists; S13 imports them for `toolExposure` patterns. A `*` does
  not match a line terminator, as in Pi's regular expression. The three
  resource tool names already count as MCP names (EXP-10 closes when S23
  registers the tools).
- `ToolFilterOptions.provider_visible_names` is Pi `_isAllowedTool`: the
  names the filters keep registered. `allow=()` still means "no allowlist";
  Pi's empty allowlist is `no_tools`.
- The active set is stored as it was given and narrowed when it is read
  (`_declared_names` in `tool_capabilities.py`): an active name is declared
  only when the filters keep it and it is activatable (Pi `_isActivatable`).
  Pi drops such names when it applies a loadout. Declarations, the callable
  names (an undeclared `direct` tool is not callable) and the loadable tools
  all read the narrowed set. `set_active_tools` still returns `True` for a
  registered name the filters removed. Explicit `definitions(allowed_names)`
  still selects from the whole registry.
- "`tool_search` is registered" in `_isActivatable` means the filters keep
  it, since pipy registers it in every production session.
- An allowlist activates only the declarable tools it names or matches; an
  MCP tool it keeps without naming it stays inactive (Pi
  `_refreshToolRegistry`).
- `all_tools()` (`ctx.get_all_tools()`) leaves out the tools the filters
  removed, as Pi's registry does. `registered_names` and the "Known tools"
  list of the unknown-name error are unchanged.
- pipy rejects unknown names in `--tools`, `--exclude-tools` and
  `defaultTools` at startup (Pi does not). A pattern is never unknown, and
  neither is an MCP tool name, because a server's tools are registered when
  it connects.
- A pattern in `--tools` that matches `codemode` selects the optional
  built-in like its name does.

### 4.4 Registration of MCP tools

`McpManager` owns a mapping of tool name to tool port for all servers. When it
changes (a server connected, announced a new list, was disabled, changed
exposure) the manager builds the complete new MCP registry and asks
`NativeToolCapabilities` to publish a capability value that differs only in
`mcp_registry`. Publication uses the same lock and the same
prepare-then-assign pattern as extension reload, so a reader sees either the
old or the new value.

Pi cannot unregister tools and re-registers withdrawn ones as `hidden`. pipy
publishes whole registries, so a withdrawn tool is simply absent from the next
value; a call that was prepared against it fails with the TLS-19 messages. The
name-ownership map (TLS-03) lives in the manager for the session: it records
which tool a name was assigned to, so a name is never handed to a different
tool, while collision suffixes are recomputed from every new list as in Pi.

A tool port holds the server name and tool name, not a connection: it asks the
manager for the current connection when it runs (TLS-19).

Decided in S17 (the capability value, `tool_capabilities.py`):

- `ToolCapabilityState.mcp_registry` is the third registry. The merged
  registry is the built-in tools, the extension tools over them, then the MCP
  tools. An MCP tool never replaces a built-in or extension tool of its
  name; `NativeToolCapabilities.publish_mcp_tools(registry)` returns the
  names it left out for that reason. Pi registers MCP tools as extension
  tools, where the first registration of a name wins.
- `publish_mcp_tools` builds the new value from the live one and assigns it
  inside the state lock, from any thread. The value has a new registry and a
  new executor and keeps the selection, the filters and the extension
  registry.
- A capability value carries `extension_generation`, an identity that
  `build` creates and every derived value keeps (a selection, other MCP
  tools). `snapshot_for_projection` compares it where it compared the
  executor, so a provider turn and a composite parent bind to the live value
  also after a server's tools changed. This is the loosening the S03 note
  asked for.
- Activation follows Pi's `_refreshToolRegistry`. Without a selection
  (`active_tool_names` is `None`) the `direct` MCP tools are active because
  registration activates them. With a selection, a published MCP tool joins
  it when registration activates it and it was not registered that way in
  the value before, so a tool that was deactivated is not activated again by
  the next list, and a tool whose exposure becomes `direct` is. With an
  allowlist, every declarable MCP tool it names or matches is active after
  each publication, as after each refresh in Pi. The narrowing of S10
  (`_declared_names`) applies as to any name.
- The name of a tool that is gone stays in the selection. Requests declare
  only registered tools, so nothing declares it, and the tool is active
  again when its server offers it again (SES-03). Pi keeps such a tool
  registered as `hidden`, with the same effect. A reload drops names that
  are not registered, as before.
- A reload carries the MCP tools: `prepare_extensions` builds its candidate
  with the live `mcp_registry`, and `publish` replaces a candidate's MCP
  tools with the live ones when they differ at the swap. The reload gate
  (`accept_prepared_reload`) still refuses a reload when the live value
  changed during its preparation. A server that connected in that window
  made `/reload` report `prepared reload owner state changed` until S22,
  which holds the manager's publications while the reload is decided (see
  "Decided in S22" in 4.1).

### 4.5 Threading

pipy's core is synchronous and stays so. MCP adds threads only inside
`native/mcp/`, all daemon threads, following the pattern of the codemode host
channel and the provider transports:

- **`McpClient.request`** is a blocking call. It registers a pending entry
  (a `threading.Event` plus result slot), sends the message and waits in a
  loop on the event with the request's deadline and the caller's cancel event.
  Responses are delivered by whichever thread reads the transport.
- **Stdio**: one reader thread for stdout parses lines and dispatches
  responses, notifications and server requests; one reader thread drains
  stderr into the bounded tail. Writes take a per-transport lock.
- **HTTP**: no network read happens on the thread that waits for a request's
  result. `send` hands the POST to a per-message daemon I/O thread and, for
  every message kind, returns only when that thread reports the POST
  **accepted** (status and headers received and checked, session id captured)
  or failed; the caller waits for that report on an event with the message's
  deadline and cancel event, and a failure (status, content type, network) is
  raised from `send`. So `notifications/initialized` is accepted by the server
  before `connect` returns, before `tools/list` is sent and before the GET
  stream opens (CLT-01, HTP-08), and a failed notification or response POST is
  an error at its call site, as in Pi. After acceptance the I/O thread alone
  reads the body: it dispatches a JSON body, or consumes an SSE body to its
  end (and resumes it, HTP-09), delivering every message through the client's
  dispatch path; a notification's body is discarded. A request's caller then
  waits only on its pending entry, with its deadline and cancel event, exactly
  as for stdio; it returns as soon as its response is dispatched, also when
  the server leaves the stream open, and on timeout or cancel when a read
  stalls. Each I/O thread
  registers its connection with a per-request `CancelToken`; the waiting side
  cancels that token when the request times out, when the caller's cancel
  event is set, and when the transport closes, which closes the socket and
  ends the thread. The GET stream runs on one background thread per
  connection, including its reconnect waits, and ends the same way on close.
- **Connection attempts**: `McpManager` starts one thread per enabled server
  at session start. Each attempt has a `ready` event. Readiness waits (SES-05,
  SES-07) wait on those events on the session thread through the existing
  tool interrupt waiter, so Escape and RPC abort end the wait (SES-10).
- **Notification handlers** run on a reader thread and only do bounded work:
  they enqueue a refresh job for the connection's own worker thread
  (`tools/list` on `list_changed`) or append a log line.
- **OAuth callback server**: `ThreadingHTTPServer` on a daemon thread, as the
  existing `_ChatGPTCallbackServer` in `oauth_providers.py`. The sign-in call
  itself blocks its caller and polls a cancel event.

Shared state and its guards:

| State | Owner and guard |
| --- | --- |
| A connection's `state`, `error`, `tools`, `resources`, `instructions`, current client | One lock per connection; readers get immutable snapshots (`ConnectionView`). |
| The manager's server list, attempts, name-ownership map | One manager lock. Never held while calling into a connection or the capability owner. |
| The published tool registry | `NativeToolCapabilities` state lock (existing). |
| Client pending map and listeners | One lock per client. |
| `mcp-auth.json` | `auth_file_lock` for every read-modify-write, plus the per-server refresh lock file. |

The manager never touches session state from a background thread. Anything
the user must see (a state change for the manager view, the startup report,
the "still connecting" notice) is posted to a thread-safe queue that the
session thread drains at defined points: prompt start, turn start, and the
`/mcp` view's refresh. S17 pins the queue contract with a test that a
background state change never calls a UI function off the session thread.

Lock order is manager lock, then connection lock, then capability state lock;
no code acquires them in another order, and S17 adds a test that fails on an
inversion (a lock wrapper that records order in tests).

### 4.6 Cancellation and shutdown

- A tool call's `ToolContext.cancel_event` is passed to `McpClient.request`.
  The waiting loop in `request` observes it (it is an event, not a callback):
  when it is set the request returns an abort error and the client sends
  `notifications/cancelled` (CLT-06). For HTTP the waiting side then cancels
  the request's `CancelToken` (`native/cancellation.py`, the mechanism
  provider requests already use), which closes the socket its I/O thread is
  reading (4.5). The cancellation notice itself is a separate short POST with
  its own 1 s bound, so a dead server cannot hold the caller.
- A nested MCP call inside codemode is cancelled through the same event by the
  existing composite waiter.
- `McpManager.close()` runs on session shutdown and on reload: it sets the
  session's shutdown flag, closes every connection (which stops connects in
  progress and retry waits, CON-09), cancels running sign-ins and joins the
  worker threads with a bound. Stdio servers get the close sequence of STD-05.
  An `atexit` hook SIGTERMs process groups that are still alive (STD-06).
  SIGTERM and SIGHUP are turned into an exit, so both also run when pipy is
  signalled (X2).
- A connection attempt is identified by a token object. Late results of an
  attempt that is no longer current (disabled, replaced, session ended) are
  dropped, as Pi's `isCurrent()`.

### 4.7 Settings, trust and storage

- `mcp.json` is its own file, not part of `settings.json`. The project file
  is read only when `resolve_project_trusted()` (`native/project_trust.py`) is
  true for the workspace; `pipy mcp` uses `ProjectTrustStore.get(cwd) is True`
  and never prompts (CMDL-11). `.pipy/mcp.json` is added to the list of
  project resources that require a trust decision
  (`has_trust_requiring_project_resources`), so a project that only has an
  `mcp.json` still asks.
- `auth` in a project file is rejected (CFG-08), and project overrides can set
  only three keys (CFG-05), so a repository cannot choose where a credential
  is sent or change a user-level server's command or URL.
- New settings: `codemode.mode`, `codemode.inlineBudget`, and the
  `-builtin:mcp` entry of `extensions`. They follow the documented
  parse, migrate, cast pipeline in `docs/settings-config.md`.
- `mcp-auth.json` is written with mode 0600 in a 0700 directory through the
  same write helper pattern as `auth_store.py`. Tokens never enter the
  metadata-only archive; MCP results are product content like any tool result.
- Output files (full text, binary resources) are created with `O_EXCL` and
  mode 0600 in the system temp directory, like `OutputAccumulator`.

### 4.8 Security boundaries

| Boundary | Rule |
| --- | --- |
| Project config | Trust-gated; cannot set `auth`; overrides limited to `enabled`, `exposure`, `toolExposure`. |
| Stdio servers | Run with the user's permissions, as `bash` does. Only configured servers start; `command` is executed without a shell. `!command` config values run a shell and come only from files the user trusts. |
| Credentials | Sent only to the configured server URL and, for OAuth, to `https` or loopback token endpoints (OAU-10). Stored state is bound to the exact server URL (OAU-15). `iss` is checked (OAU-12). Redirects are loopback only (CFG-18). |
| `auth.provider` | User-level file and extensions only; `https` or loopback URL only (CFG-21). |
| Server output | Tool results, instructions, descriptions and log messages are untrusted text. They reach the model only through the tool result path and the bounded `mcp_servers` summary (first line, 250 characters). Instructions are never placed in a tool description (CMD-10, CMD-21). |
| Tool pipeline | Every MCP call, direct or nested, passes validation and the `tool_call`/`tool_result` hooks (SES-19). Annotations are reported, not trusted. |
| Codemode | The guest gains no authority: discovery helpers return metadata only, and calls go through the nested service. |
| Limits | 16 MiB per message, 64 KiB stderr, 8 KiB error body, 1000 list pages, 20 KB model-facing text, 4096-character prompt section. |

### 4.9 Codemode protocol changes

The guest and host exchange JSON lines (`codemode/prelude.py`,
`codemode/host.py`). Three additions:

1. The run request carries the callable tools as `{name, id, description}`
   records (the ALL_TOOLS entries), replacing the list of names.
2. A tool reply carries either `text` or `value` (JSON). `value` is used when
   the tool declares an output schema and the result has structured content
   (CMD-03).
3. A new guest message kind for discovery (`search`, `describe_tool`,
   `describe_namespace`) answered by the host from the callable snapshot.
   Discovery never executes a tool and is not a nested call record.

The 1 MiB protocol line limit applies to replies. An untruncated MCP result
larger than that cannot cross it; S04 raises the limit for reply lines to the
transport message limit or streams large replies in parts, and pins the choice
with a test of a 5 MB structured result.

Decided in S04:

- Replies are not streamed in parts. The 1 MiB limit (`ScriptLimits.line_bytes`)
  was only ever checked on the lines a guest sends, and stays there. Reply
  lines get their own limit, `ScriptLimits.reply_bytes`, 16 MiB (the
  transport message limit of 4.8), measured on the encoded JSON line. A larger
  result, or a structured value that is not JSON, becomes an error reply: the
  tool has run, the nested record keeps the tool's own status, and the script
  gets a `ToolError` that names the tool and the size. Replies are encoded on
  the session thread, so the I/O thread only copies bytes.
- The text reply key is `text` and the structured one `value`, as item 2
  says; the earlier `{"ok": true, "value": str}` form is gone. A reply with
  `value` is sent with `ok: true` also for an error result (CMD-03); the
  host's call record and the parent's nested record follow the tool's outcome.
- `None` stays the marker for "no structured content" (S01), so a tool cannot
  hand a script JSON `null` as its structured content; it gets the text.
- Which tools declare an output schema is part of the parent's callable
  snapshot (`NestedCallableTools.output_schema_names()`), read through
  `NestedToolCallService.declares_output_schema(name)`. The decision uses the
  registered definition, not the result: structured content of a tool without
  an output schema stays with hooks and events and never reaches a script,
  also when a `tool_result` hook added it.
- CMD-25: `ToolResultEvent` carries `structured_content`, and
  `ToolResultTransform` gained an optional `structured_content`. pipy's
  transform always replaces the content, so Pi's rule "content without
  structured content drops it" means here: a transform decides both, and one
  without `structured_content` drops it. To change only the structured content
  a hook returns the event's content with it. Pi's transform can also replace
  `details`, `isError` and `usage`; pipy's cannot, and that is not part of
  this plan. The event hands the value over by reference, as Pi does.
- The tool policy port (`AgentToolPolicy.transform_result`) may return an
  `AgentToolResultTransform(content, structured_content)` in place of a bare
  `ProductContent`. A bare content that differs from the result's drops the
  structured content, the same rule one level down.
  `dispatch_tool_result_hooks` now returns the final `ToolResultTransform`
  instead of a string.
- `Tool "<name>" failed` is used for a failed result without text, for the
  script's `ToolError` and for the `error` field of the nested record.
- The retained nested records drop `structured_content` together with content
  and details (CMD-26: never results).
- Not changed: the `codemode` tool description still says that tool results
  are strings (byte-pinned until S08, CMD-18), and RPC/JSON
  `tool_execution_end` does not carry structured content (no row of this plan
  asks for it; Pi's event carries the whole in-memory result).

Decided in S05 (declaration syntax, `codemode/declarations.py`):

- A declaration is notation for the model, not evaluated code. Signature:
  `name(args: T) -> R` with the tool's identifier; the sample is the
  description, a blank line, `codemode tool declaration:` and a `python`
  fence holding `tools.<signature>`. A tool without output schema returns
  `Any`.
- Types: `str`, `float` (number and integer), `bool`, `None`, `Any` (Pi
  `unknown`), `Never` (Pi `never`: the `false` schema, an empty `enum` or
  union), `list[T]`, `[A, B]` for positional arrays (the value is a list),
  `A | B`, `A & B` for `allOf`.
- Objects: `{"a": str, "b"?: float}` with every property name quoted, sorted
  as Pi sorts them (by UTF-16 code unit) and separated by commas. Pi's index
  signature `[key: string]: T` is a last member `str: T`; an object without
  declared properties is `dict[str, T]` (`dict[str, Any]` for a bare
  `{"type": "object"}`), and `{}` when it allows no properties at all. The
  multi-line form puts one member per line with a trailing comma and the
  description and tag lines as `#` comments above it.
- `const` and `enum` values are Python literals (`None`, `True`, `False`,
  compact dicts and lists). Constraint tags keep the JSON Schema keyword names
  and JSON values (`@default true`), as CMD-12 says. Numbers are printed as
  JavaScript prints them (`1.0` is `1`), so tags match Pi's.
- Line terminators (`\r`, `\n`, U+2028, U+2029) are escaped in literals and
  property names as well as in tags, so no value can end its line.
- Lengths (the 80 characters of a tag, the 16000 of an input type) count
  Python characters, where Pi counts UTF-16 units.
- A schema nested deeper than the interpreter can follow renders as `Any`
  instead of raising.
- `to_codemode_identifier` lives in the same module: the ASCII rule of Pi
  without `$`, which Python cannot spell. It does not treat Python keywords:
  a tool named `class` keeps the identifier `class`, which a script cannot
  write as `tools.class`. S07 decides that together with the raw-name key
  (CMD-02).
- Not ported: `renderDeclarations` (a whole `tools` object and script
  globals). Nothing in the coding agent uses it; `ALL_TOOLS` and the tool
  description use the per-tool sample.

Decided in S07 (discovery, `codemode/prelude.py`, `codemode/host.py`,
`coding/codemode_discovery.py`):

- Identifier (CMD-02): Pi's rule, and a Python keyword gets a trailing `_`
  (`class` is `tools.class_`), so every identifier can be written after
  `tools.`. `to_codemode_identifier` is the one mapping; declarations,
  `ALL_TOOLS`, namespace matching and the guest use it. The raw-name key is
  subscript access: `tools["<name>"]` takes the tool's own name or its
  identifier (`getattr(tools, "<name>")` works too). As in Pi's prelude the
  first tool wins an identifier that two names share, and a raw name never
  replaces an earlier tool's identifier. Different from Pi (review finding):
  Pi names every discovery result by the identifier, which for a tool that
  lost its identifier calls the winner. pipy names a tool everywhere
  (`ALL_TOOLS`, `search_tools`, `describe_namespace`, its declaration) by the
  key that reaches it: the identifier, else its own name. A tool that neither
  key reaches is left out of the run request and of discovery. An identifier
  that starts with `_` is a tool; no tool name is shadowed by the `tools`
  object's own state. Only `__dunder__` names stay the interpreter's and are
  reached as keys.
- The run request carries `{name, id, description}` records in registration
  order (item 1). The host computes the identifiers; the guest holds no copy
  of the mapping. `ALL_TOOLS` is a list of `{"name": id, "description": ...}`
  dicts. A script can change its own copy; that changes nothing on the host.
- Discovery is one guest message kind, `discover`, with the helper's name
  (`search_tools`, `describe_tool`, `describe_namespace`) and its arguments by
  parameter name (item 3). It shares the id sequence and the one-outstanding
  rule of `call`, is answered on the session thread from the parent's callable
  snapshot, and its reply is bounded by `ScriptLimits.reply_bytes` like a tool
  reply. It does not reach the nested service: no record, no event, no tool
  budget. A helper name other than the three is a protocol violation.
- Arguments are checked on the host only, as in Pi, because the guest is
  untrusted: the answers are Pi's messages with the Python names, raised in
  the script as `ValueError`. `limit` must be an `int` (not `bool`, not a
  float such as `8.0`, which JavaScript cannot tell from `8`); `None` means
  the default 8 (Pi `??`). An empty `namespace` is no filter (Pi `&&`). An
  argument that is not JSON raises `TypeError` in the guest before anything
  is sent.
- The callable snapshot hands out definitions: `NestedCallableTools
  .callable_tools()` replaces `output_schema_names()` (S04), and
  `NestedToolCallService.callable_tools()` returns the ones the parent may
  call, in registration order. The codemode sandbox package still imports no
  tool types; the product builds the records and the discovery callback
  (`CodemodeDiscovery`) and injects them into `run_script(tools=, discover=)`.
- The `ALL_TOOLS` description is `render_tool_sample` of the tool's
  description and schemas. A tool without an output schema is declared
  `-> str` (Pi `TEXT_OUTPUT_SCHEMA`). Prompt guidelines are not added: pipy
  tools have none (S02).
- CMD-17: the message for a missing tool is Pi's in shape (`tools.<name> does
  not exist.`, up to five close matches, else the names when there are at most
  20, then the pointer to `ALL_TOOLS` and `search_tools`). Close matches are
  Pi's two rules; when neither finds one, pipy's earlier nearest-name match
  (`difflib`) still catches a misspelling such as `reed`. Subscript access
  raises `KeyError` with the same text, attribute access `AttributeError`.
  Names are listed in registration order, no longer sorted.
- Not changed: the `codemode` tool description (byte-pinned until S08) does
  not mention the helpers. Tool names are still limited to letters, digits
  and `_` by `ToolDefinition`, so a name with `-` cannot be registered; the
  mapping and both key forms are tested below the registry with such names,
  and in a session with the names `class` and `9lives`. (S16: the rule
  stays. Pi gives an MCP tool a name of letters, digits and `_` only, so no
  MCP tool needs another character.)

Decided in S08 (description, catalog and `codemode.mode`,
`coding/codemode_description.py`, `tools/loadout.py`, `tool_capabilities.py`):

- The loadout is Pi's `prepareLoadout` in pipy's terms. A capability value
  (`ToolCapabilityState`) carries one presenter, `codemode_loadout`: a
  function from a `ToolLoadout` (the declared and the callable definitions)
  to `ToolLoadoutChanges` (replaced descriptions by tool name, and the
  declarations requests leave out). It is applied when the definitions of a
  value are projected, and only while the built-in `codemode` is declared;
  an extension's own `codemode` tool and a request that does not declare
  `codemode` get no changes. Nothing is cached: a value's definitions are a
  function of the value. S37 (`prepare_loadout` for extensions) can widen the
  one presenter to one per tool.
- Settings are read when a capability value is built, at session start and on
  `/reload`, with `defaultTools`; they are part of the value, so the
  declarations, the hidden set and the system prompt of one value always
  agree. Pi reads `codemode.mode` and `codemode.inlineBudget` before every
  prompt. pipy's getters are as tolerant as Pi's readers: a mode other than
  `only` is `on`; a budget that is not a finite, nonnegative number is 3000.
- The transcript declares the tools `only` hides, as in Pi (`definitions()`
  keeps them, with their plain descriptions), so `/tree` navigation restores
  them as active. The request leaves them out: the product keeps them out of
  `available_tools` and names them in the new
  `ProviderRequest.undeclared_tool_names`, and the system prompt's tool list
  and rules leave them out too (Pi `hiddenTools`). The mid-conversation
  projection of `providers/transcript.py` removes their declarations from
  the system messages it sends, as it does for `hidden_tool_names`.
- The two fields stay apart, as the S03 note in 4.3 asked:
  `hidden_tool_names` are tools a `before_provider_request` hook withheld,
  which a script must not reach, and `undeclared_tool_names` are
  declarations the product left out, which a script is meant to reach. A
  parent's callable names subtract only the first. Both survive a request
  being frozen or transformed again.
- Different from Pi: a direct model call to a tool that `only` hides is an
  unknown tool in pipy, because a direct call is authorized against the
  frozen request (4.3). In Pi the tool is still among the agent's tools and
  the call would run. The model is not told about the tool either way.
- Description text (CMD-18). The fixed part is Pi's `DESCRIPTION_INTRO` and
  `describeGlobals` for Python scripts: where a script runs, the call
  contract, the limits, and the globals with the helpers named as the way to
  find unlisted tools, such as MCP tools. pipy keeps the read/filter guidance
  and the runnable example from the codemode adherence slice after it. The
  sentence that listed the seven built-ins is gone: with `on` each declared
  tool says in its own description that scripts can call it.
- Catalog (CMD-19, CMD-20): a section is ``### `<key>` `` (and the tool's own
  name in parentheses when it differs), then the tool's `ALL_TOOLS`
  description from `CodemodeDiscovery`, so a tool that lost its identifier is
  declared by key and a tool no key reaches is not listed (S07). The keys
  come from the whole callable set, not from the listed tools. The cost of a
  section is its length in UTF-16 code units / 4, rounded up, as in Pi.
  Namespaces are ordered by name without regard to case, then by code point;
  Pi uses `localeCompare`, which can order punctuation differently.
- Guidelines (CMD-06, CMD-19): pipy's only prompt guidelines are those of
  the built-ins (`BUILTIN_TOOL_PROMPTS`); extension tools have none (S02).
  They are added as bullets to a tool's `ALL_TOOLS` description, which
  `describe_tool` and the catalog sections use. This closes the part of
  CMD-06 that S07 left open.
- The `Codemode:` line (CMD-22) names the call as scripts write it,
  `tools.<identifier>` or `tools["<key>"]`, and what it resolves to: `a
  string`, the field names of an object schema as Pi writes them
  (`{ content, isError? }`, one nested level), or the declared type in
  pipy's notation on one line. A declared tool that no key reaches gets no
  line and is not hidden by `only`.
- Description length: pipy's strict definitions cap a description at 4 KiB.
  The declared copy of a tool whose replaced description is longer is
  declared with `schema_mode="foreign"`, which has no cap and accepts every
  strict schema. The registered definition, which validates arguments, is
  unchanged. Pi has no cap.
- CMD-27: pipy's contribution is one snippet and two guideline lines (Pi's
  guideline in Python terms, and one from the adherence slice that predates
  this plan). S08 leaves it as it is and pins that tools add nothing to it.
- Cost, for S16 and S17: every projection of a value's definitions computes
  the keys of all callable tools and renders the sections of the listed
  ones. `deferred` tools, which MCP tools are by default, are not rendered.

### 4.10 Reload and resume

`/reload` runs `McpManager.close()`, loads the config again and starts the
manager like a session start (SES-20). It fits the transactional reload
contract as an additional candidate: the new manager is constructed (config
loaded and validated, no connections) before the swap, and connections start
after the new generation is published. A reload that fails before the swap
leaves the old manager running.

Tools that `tool_search` loaded are active names in the transcript. After
`/tree` the names are restored through
`NativeToolCapabilities.restore_active_tools`, which drops unknown names; a
reload unregisters the MCP tools. For TSR-10 the names that look like MCP
tools are kept as pending names in the capability value and activated when
their tool registers before the next prompt starts; the pending set is
cleared at prompt start. How S22 built this is in 4.1, "Decided in S22".

## 5. Ordered slices

Size: S is up to about 250 lines of product code, M about 250 to 500, L about
500 to 800 (to be split by the implementer when it grows). Each slice lands as
one reviewed commit with its tests, its docs rows and a changelog line.

### 5.1 Tracks

- **Track A** (S01 to S10) changes shared files: `tools/base.py`,
  `tool_capabilities.py`, the agent loop, codemode, extension types.
- **Track B** (S11 to S14, S24, S25, S27 to S29) adds new files under
  `native/mcp/` and `tests/` only, with no import from Track A files beyond
  the standard library and `native/cancellation.py`, `native/http.py`,
  `native/auth_store.py` (read-only use). It can be built in parallel on a
  second branch and merged before S15.

Slices outside these two lists are serial.

### 5.2 Slice table

| ID | Title | Closes | Size | Depends on | Parallel |
| --- | --- | --- | --- | --- | --- |
| S01 | Tool metadata on `ToolDefinition` and results; foreign-schema mode and validator | EXP-01 (model) | M | none | no |
| S02 | Extension tool metadata, `get_all_tools` reporting, hidden re-registration, `reaches_tools` declaration | EXT-01 to EXT-05 (declaration part) | M | S01 | no |
| S03 | Declared and callable sets; callable snapshot; codemode reaches extension tools | EXP-01 to EXP-04, CMD-01, CMD-24, CMD-26 | M | S02 | no |
| S04 | Codemode result shapes: structured values, error rules, large replies | CMD-03, CMD-04, CMD-25, EXT-02 | M | S03 | no |
| S05 | Declaration renderer | CMD-11 to CMD-15 | M | none | yes (A or B) |
| S06 | Tool search core: tokenizer, BM25, documents | TSR-06 to TSR-08 | S | none | yes (A or B) |
| S07 | Codemode discovery: identifiers, `ALL_TOOLS`, `search_tools`, `describe_tool`, `describe_namespace` | CMD-02, CMD-06 to CMD-10, CMD-17 | M | S04, S05, S06 | no |
| S08 | Dynamic codemode description, catalog budget, `codemode.mode`, settings | CMD-18 to CMD-22, CMD-27 | M | S07 | no |
| S09 | `tool_search` tool | TSR-01 to TSR-05, TSR-09, TSR-11 | S | S03, S06 | no |
| S10 | Tool patterns and MCP allowlist rules in `--tools`/`--exclude-tools` | EXP-06 to EXP-09 | S | S03 | no |
| S11 | Protocol types, in-memory transport, client core | CLT-01 to CLT-11 | L | none | yes (B) |
| S12 | Client lists, calls, resources, content conversion | CLT-12 to CLT-15 | S | S11 | yes (B) |
| S13 | Stdio transport and the fake stdio server | STD-01 to STD-07 | M | S11 | yes (B) |
| S14 | Config: validation, load, trust, overrides, edit | CFG-01 to CFG-24 | M | none | yes (B) |
| S15 | Server connection, config value resolution, log file | CFG-25, CFG-26, CLT-16, CON-01 to CON-09, CON-15, CON-16 | M | S12, S13, S14 | no |
| S16 | Tool adaptation and result conversion (images as placeholders until S39) | TLS-01 to TLS-18, CMD-05, CMD-16, CMD-23 | L | S15, S04, S05 | no |
| S17 | Session manager: start, registration, call-time checks, shutdown; stdio server end to end | SES-01 to SES-03, SES-17, SES-19, TLS-19, EXP-05, EXT-03 (MCP part) | L | S16, S03 | no |
| S18 | Readiness waits: first prompt, reach-based waits | SES-05 to SES-10, EXT-05 (use) | M | S17, S08, S09 | no |
| S19 | Discovery activation, warnings, startup report, `--no-mcp`, `-builtin:mcp` | SES-04, SES-11 to SES-13, EXP-11, EXP-12 | M | S18 | no |
| S20 | `mcp_servers` prompt section and section access in `before_agent_start` | SEC-01 to SEC-07, EXT-16 | M | S19 | no |
| S20a | `pipy mcp add`, `remove`, `list` for stdio servers | CMDL-01 to CMDL-07, CMDL-10, CMDL-11; CMDL-08 and CMDL-09 in part | M | S15 | yes (after S15) |
| S21 | `/mcp` status, `reconnect`, completion; runtime enable/disable/exposure | SLH-01 to SLH-03, SLH-05, SLH-06, SES-14 to SES-16 | M | S19 | no |
| S22 | `/reload` and resume | SES-20, TSR-10 | M | S21, S09 | no |
| S23 | Resources | RES-01 to RES-10, EXP-10, TLS-12 (read hint), CMDL-08 and CMDL-09 (resource counts) | M | S19, S20a | no |
| S24 | SSE parser and streamable HTTP POST path; the fake HTTP server | HTP-01 to HTP-05, HTP-07, HTP-11 | M | S11 | yes (B) |
| S25 | GET stream and stream resumption | HTP-08 to HTP-10 | M | S24 | yes (B) |
| S26 | HTTP connections: transport factory for `url` entries, retries, session expiry | CON-10 to CON-12, CMDL-08 and CMDL-09 (HTTP entries) | S | S25, S17, S20a | no |
| S27 | OAuth types, errors, discovery | OAU-02 to OAU-06 (discovery part) | M | none | yes (B) |
| S28 | OAuth flow: registration, PKCE, token requests, refresh, recovery | OAU-06 to OAU-15 | L | S27 | yes (B) |
| S29 | Callback server | OAU-16, OAU-17 | S | none | yes (B) |
| S30 | Credential store, connection auth provider, 401 handoff, `needs-auth` | OAU-01, OAU-18 to OAU-20, OAU-22, OAU-23, HTP-06, CON-13, CON-14 | M | S26, S28 | no |
| S31 | Sign-in; `/mcp login`/`logout`; `pipy mcp login`/`logout`; cross-process pickup | OAU-07, OAU-15, OAU-21, OAU-24 to OAU-29, CMDL-12 to CMDL-14, SLH-04, SES-18 | L | S30, S29, S21, S20a | no |
| S32 | OAuth options: pre-registered client, callback URL and port, client name, CIMD, metadata URL | OAU-30 to OAU-35 | M | S31 | no |
| S33 | `auth.provider` | OAU-36, CON-14 (provider text) | S | S30 | no |
| S34 | Interactive `/mcp` manager | MGR-01 to MGR-10 | L | S31 | no |
| S35 | Rendering: MCP rows, resumed sessions, export, tool renderer resolver | RND-01 to RND-04, EXT-15 | M | S17 | yes (after S17) |
| S36 | Extension API: server registration and `mcp_servers_change` | EXT-06 to EXT-10 | M | S21 | no |
| S37 | Extension API: nested execution, loadout hook, replaceable built-ins | EXT-11 to EXT-14 | L | S36, S08 | no |
| S38 | SDK options and example | SDK-01 to SDK-04 | M | S36 | no |
| S39 | Images: tool-result images, MCP images and image resources, codemode `image()` | TLS-11, RES-09 (image parts), CMD-28 to CMD-30 | L, several commits | S23 | no |
| X1 | Prompts of print, JSON and RPC mode run extension commands and `/mcp`; RPC UI requests for `/mcp` | SLH-02 (remainder), SLH-07 to SLH-13 | M | S38 | no |
| S40 | Conformance recipe, user docs pass, changelog, parity audit against section 2 | DOC-01 to DOC-05 | M | all | no |
| X2 | Leaked stdio server processes: the fake server and the suite guard; SIGTERM and SIGHUP end pipy like an exit | none (STD-05 and STD-06 on a signalled pipy) | S, two commits | S40 | no |
| G1 | Stdio close keeps signalling the group while a child holds the server's pipes (goal-check finding C1) | STD-05 | S | X2 | no |
| G2 | Nested tool calls: async handlers, every depth, concurrent calls, `signal` and `on_update`, update events, the full outcome (goal-check findings M1, M2, M12, E2 to E5) | EXT-11, CMD-24, CMD-26 | M, several commits | G1 | no |
| G3 | A codemode script's calls at the same time (goal-check finding M3, DEV-03) | CMD-24, CMD-26 | M | G2 | no |
| G4 | Codemode `store()`/`load()` per session branch (goal-check finding M4, DEV-04) | none (DEV-04) | M | G3 | no |
| G5 | What a codemode script gets and may emit: a `null` structured result, structured `bash` results, images from `read`, the `// @options:` line, output over the limits truncated (goal-check findings M6, M15 to M18, DEV-04) | CMD-03 | M, several commits | G4 | no |
| G6 | Tool hooks and argument validation as Pi has them: validation before the `tool_call` hooks, their argument changes run, call ids on the hook events, `tool_result` hooks that change `is_error`, details and images, foreign arguments validated and coerced, a record with an unfinished call is not complete (goal-check findings M8 to M11, M13, M14, E8, T6, T8) | CMD-24, CMD-25, CMD-26, SES-19, TLS-10 | M, several commits | G5 | no |

Usable milestones: after S03 a script calls an extension tool; after S09
scripts discover tools and `tool_search` works for extension tools; after S19
a stdio MCP server works end to end (config by hand, `--no-mcp`, startup
report); after S26 HTTP servers with static headers; after S31 OAuth servers.

### 5.3 Slice details

Each entry names what the slice builds, its acceptance tests and its docs. All
tests use the fixtures of section 6 and isolate `HOME` and `PIPY_CONFIG_HOME`.

**S01 Tool metadata and foreign schemas.**
Build: the five properties of 4.2 on `ToolDefinition`; `schema_mode`;
`validate_foreign_arguments`; `structured_content` on both result types;
decide persistence from Pi.
Tests: defaults leave every existing definition unchanged (the existing
definition tests pass untouched); a foreign definition accepts a schema with
`anyOf`, `$ref`, `number`, nested arrays and a 10 KiB description; the
validator accepts and rejects one case per keyword in the named set and
ignores an unknown keyword; one provider request per adapter family carries a
foreign schema unchanged.
Docs: `docs/extension-api.md` Tools section (model only), architecture note.

**S02 Extension tool metadata.**
Build: `ExtensionTool` fields; `ToolResult.structured_content` and `is_error`;
`get_all_tools()` entries report exposure, namespace, annotations and
`reaches_tools`; registering an existing name again with `hidden` withdraws.
Tests: activation accepts and rejects each field shape; a tool that returns
`is_error` reaches the model as an error; `get_all_tools()` output; hidden
re-registration removes the tool from the next request.
Docs: `docs/extension-api.md`.

**S03 Declared and callable sets.**
Build: 4.3 in full; registration activates only `direct` and `model-only`;
remove `BUILTIN_NESTED_NAMES`; update the CM1 spec text and
`docs/codemode.md`.
Tests: mirrors of "rejects direct model calls to codemode-only MCP tools",
"does not let codemode call itself or inactive direct tools", "keeps
codemode-only MCP tools callable across tree navigation" using extension
tools; a script calls an extension tool with `codemode` exposure and a
`tool_call` hook blocks it; a capability value published while a script runs
does not change the script's callable names; the existing composite tests
pass with built-ins.
Docs: `docs/codemode.md` "Writing a script" and eligibility paragraph.

**S04 Result shapes.**
Build: 4.9 items 2 and the large-reply rule; `ToolError` text rules.
Tests: a tool with an output schema resolves to a dict, also for an error
result with structured content; a tool without resolves to `str`; a blocked
call raises `ToolError`; a `tool_result` hook that replaces content decides
the structured content (CMD-25); a 5 MB structured result reaches the script
intact.
Docs: `docs/codemode.md` result rules.

**S05 Declaration renderer.**
Build: `codemode/declarations.py`: `schema_to_type`, `render_tool_signature`,
`render_tool_sample`, constraint tags. Python syntax: `str`, `float` for
number and integer (with `@integer`), `bool`, `None`, `list[T]`, tuples,
unions with `|`, object literals `{"a": str, "b"?: float}`, `Any` for
unknown, `#` comment lines.
Tests: one case per case of Pi's `declarations.test.ts` `schemaToType` and
"tool declarations" groups, with the expected text in Python syntax.
Docs: none (internal until S07).

**S06 Tool search core.**
Build: `tokenize`, `stem`, `Bm25Ranker`, `create_tool_search_document`.
Tests: the three `tokenize`/`Bm25Ranker` groups of Pi's `tool-search.test.ts`
with the same inputs and expected orders.
Docs: none.

**S07 Codemode discovery.**
Build: 4.9 items 1 and 3; guest `ALL_TOOLS`, `search_tools`, `describe_tool`,
`describe_namespace`; identifier mapping; unknown-tool message keeps working
with identifiers.
Tests: mirror of "finds tools from scripts with searchTools() and
describeTool()"; namespace filter by all four name forms; argument errors;
`describe_namespace` returns instructions and tool ids; discovery adds no
nested call record.
Docs: `docs/codemode.md` globals table.

**S08 Codemode description and settings.**
Build: `create_codemode_description` with catalog selection; `codemode.mode`
and `codemode.inlineBudget`; the per-tool `Codemode:` line; description
computed per request from the declared and callable sets and pinned by a
stability test.
Tests: mirrors of the five "codemode description catalog" cases and of
"presents callable tools per codemode.mode"; the description is byte-identical
when a `deferred` tool is added to the registry.
Docs: `docs/codemode.md`, `docs/settings-config.md`, `docs/settings.md`.

**S09 `tool_search`.**
Build: the built-in tool, registered inactive, `model-only`,
`reaches_tools` "all"; loading goes through `set_active_tools` so the
transcript records the tool change.
Tests: mirrors of "activates tool_search for deferred MCP tools and keeps
loaded tools declared on the branch" (with extension tools), "finds nothing to
load when every matching tool is already declared", "registers codemode and
tool_search inactive until they are named"; argument errors.
Docs: `docs/usage.md` tools list, new `docs/mcp.md` stub section "Tool
search".

**S10 Patterns and allowlist rules.**
Build: `create_tool_name_matcher`; `ToolFilterOptions` uses it for `allow`
and `exclude`; `is_mcp_tool_name`; EXP-06 rules in declared-set computation.
Tests: mirrors of the six `--tools` cases of `agent-session-mcp.test.ts`
(lines 231 to 315), using registry entries named `mcp__x__y`.
Docs: `docs/usage.md` CLI flags.

**S11 Client core.** Build `protocol.py`, `transport.py`, `client.py`
(without the typed helpers). Tests: mirrors of Pi's `client.test.ts` cases
"initializes the connection before exposing server information", "renews the
timeout on progress", "cancels aborted and timed-out requests", "reports
transport errors without failing pending requests", "accepts servers that
answer with an older protocol version", "does not send notifications/cancelled
for a timed-out initialize", "notifies close listeners once when the transport
drops", "answers roots/list and dispatches notifications". Docs: none.

**S12 Client helpers.** Build list, call and read helpers and
`to_llm_content`. Tests: mirrors of "paginates tools and preserves protocol
tool definitions", "lists and reads resources", "returns structured tool
content and surfaces JSON-RPC errors", "defaults missing tool result content
to an empty list", and both `content.test.ts` cases; duplicate cursor and page
limit.

**S13 Stdio.** Build `stdio.py` and `tests/fixtures/mcp_fake_server.py`
(section 6). Tests: mirror of "connects to a newline-delimited MCP server and
captures stderr"; oversize line; CRLF; invalid line; missing executable; a
server that ignores stdin close is killed and its child process is gone;
partial message at exit.

**S14 Config.** Build `config.py`. Tests: mirrors of the seven "MCP config"
cases of `mcp-extension.test.ts`; one test per error text of CFG-02 to
CFG-21; edit functions keep unrelated content, tabs and four-space
indentation. Docs: `docs/mcp.md` "Configure servers" and "Configuration
rules".

**S15 Connection.** Build `connection.py`, `log.py`, default transport
factory for stdio, `resolve_config_value` use. Tests: mirrors of "connects to
servers without the tools capability without listing tools", "marks a dropped
connection and reconnects on the next call", "closes a connection that is
still initializing before close returns", "appends server log messages to the
log file"; `~` and relative `cwd`; unresolved `${VAR}` in `env`; log rotation.

**S16 Tool adaptation.** Build `tools.py`. Tests: mirrors of the six "MCP
tools" cases of `mcp-extension.test.ts` ("creates provider-safe tool names",
"declares the CallToolResult scripts receive, keeping references in the
server's output schema", "converts results, passing the CallToolResult to
scripts and flagging errors", "points resource links to read_mcp_resource and
saves binary resources", "cuts the middle of model-facing text over 20KB and
keeps the full result for scripts", "cuts multi-byte text only at character
boundaries"); collision naming independent of order, a refresh that adds a colliding tool renames both and one that removes it restores the plain name, and a name once owned by another tool is not reused; progress updates; file
mode of the spill file. Images render as `[image <mime> omitted]` until S39;
the slice records this as a known gap in `docs/mcp.md`.

**S17 Session manager.** Build `manager.py` (start, registration,
publication, call-time checks, close), the session wiring and the queue of
4.5. Tests: an end-to-end run with the fake stdio server and a fake provider
that (a) calls a `direct` MCP tool and (b) runs a codemode script calling a
`codemode` MCP tool; mirrors of "exposes codemode-only MCP tools through
codemode and hides them from the model", "applies per-tool exposure
overrides"; list change adds and withdraws tools; `tool_call` hook blocks an
MCP call; shutdown while a server is connecting returns with no child process
left; the lock-order and off-thread UI tests of 4.5. Docs: `docs/mcp.md`
"Quick setup" with a hand-written `mcp.json`.

**S18 Readiness.** Build SES-05 to SES-10 and `reaches_tools` for codemode.
Tests: mirrors of the cases at lines 769 to 925 of `agent-session-mcp.test.ts`
("does not hold the first prompt for codemode servers that are still
connecting" through "holds the first prompt for servers with direct tools only
up to startupWaitMs"), using the fake server's `--delay-initialize` option;
Escape ends a wait.

**S19 Activation and switches.** Build `ensure_discovery_active`, the
warning, the startup report, `--no-mcp`, `-builtin:mcp`, the config command
entry. Tests: mirrors of "does not activate codemode when autoEnableCodemode
is false", "treats codemode MCP tools as reachable through an active
tool_search", "reaches deferred MCP tools through codemode when tool_search is
not available", "warns when deferred MCP tools have neither tool_search nor
codemode"; codemode selected but its runtime unavailable gives the SES-13
warning; `--no-mcp` reads no file and starts no process. Docs: `docs/mcp.md`
"Control tool exposure", `docs/usage.md`.

**S20 Prompt section.** Build `render_servers_section`, template integration,
section patch through `diff_sections`, `system_prompt_options.sections` for
`before_agent_start`. Tests: the three "MCP servers section" cases of
`mcp-extension.test.ts`; mirrors of "lists a server before it connects and
appends its summary with the next prompt", "waits for servers with direct
tools before listing the servers", "leaves servers with only direct tools out
of the servers section", "describes the server with its configured description
and returns its instructions to scripts", "lists servers by the first line of
their instructions without a configured description".

**S20a Shell commands.** Build `shell_cli.py` and the `mcp` subparser. `add`
and `remove` handle both transports (they only write config). `list` connects
stdio entries; until S26 an HTTP entry is reported in state `failed` with the
error of the default transport factory (see "Decided in S20a"); S23 added the
resource counts. The gap is named in the slice's changelog line and closed
by the row's later slice. Tests: mirrors of the eight `mcp-command.test.ts`
cases except login, with stdio servers. Docs:
`docs/mcp.md`, `docs/usage.md` "MCP commands".

**S21 `/mcp` without the TUI.** Build the command, status text, `reconnect`,
completion, `set_enabled`, `set_exposure`, `save_config`. Tests: status text
for each state; reconnect after the fake server was killed; disable closes the
process; exposure change moves a tool between declared and callable; a failed
save changes nothing; RPC mode prints the status (done in X1, see "Decided in
X1").

**X1 Prompts of the automation modes.** Added after S38 for the remainder of
SLH-02: Pi's `prompt` dispatch in print, JSON and RPC mode (SLH-07 to SLH-10)
and the RPC UI requests `/mcp` needs (SLH-11, SLH-12). Tests: `/mcp`, an
extension command, a failing one, the built-in names, an unknown name, a
template and a skill in each mode; the RPC dispositions; a prompt after a
handled one.

**S22 Reload and resume.** Build 4.10. Tests: mirrors of the "MCP tools
after resume and reload" cases (the two reload cases in a session, the
resume and the loadout cases at the capability value, since pipy restores a
transcript's tools only on `/tree`); reload with a removed, an added and a
changed server; a reload that is refused keeps the running manager; the
`builtin:mcp` switch is read again.

**S23 Resources.** Build `resources.py`, connection resource calls, exposure
sync. Tests: mirrors of "lists and reads resources with Codex's resource
tools", "makes the resource tools callable from codemode for codemode
servers"; each RES row; the resource tools stay registered after the only resource server's process is killed, and the next `read_mcp_resource` reconnects it.

**S24, S25 HTTP transport.** Build `sse.py`, `http.py` and
`tests/fixtures/mcp_fake_http_server.py`. Tests: mirrors of all
`streamable-http.test.ts` cases except "calls fetch without a receiver"; a
server that answers on SSE and keeps the stream open (the call returns at
once); a server that stalls mid-body (the call times out at its deadline and
the I/O thread ends); cancel during a stalled read returns within the cancel
bound and leaves no thread or socket behind; closing the transport ends every
I/O thread; a server that delays its answer to the `notifications/initialized`
POST receives no `tools/list` and no GET before it answered, and one that
rejects that POST fails the connect.

**S26 HTTP connections.** Build: the default transport factory creates the
HTTP transport for `url` entries (S15 built it for stdio only), which also
ends the failure `pipy mcp list` reports for them. Tests: `pipy mcp list` and a session against the fake
HTTP server; mirrors of "starts a new session and retries
once when the session expired", "retries HTTP connections that fail with a
transient error", "stops waiting to retry a connection when closed", "retries
resource reads, but not tool calls, after a transient HTTP error".

**S27, S28, S29 OAuth library.** Tests: mirrors of all `oauth.test.ts` cases
against the fake authorization server of section 6.

**S30 Credentials and auth provider.** Tests: mirrors of
`mcp-oauth-store.test.ts` "keeps separate credentials for servers sharing a
SERVER_URL", both `mcp-oauth-refresh.test.ts` "MCP OAuth refresh" cases (two
processes), "asks OAuth servers that keep rejecting requests for a new
sign-in", "closes the session without refreshing an expiring token".

**S31 Sign-in.** Tests: mirrors of `agent-session-mcp-oauth.test.ts` "signs in
through the browser, refreshes expired tokens, and signs out", "accepts a
pasted redirect URL when the browser cannot reach the callback", "cancels a
sign-in waiting on <path> when the session shuts down", "gives up on pi mcp
login after --timeout, also while the authorization server hangs", "uses
credentials from pi mcp login on the next turn"; `mcp-oauth-refresh.test.ts`
"rejects an authorization response from another issuer", "keeps the granted
scope when the server asks for more"; `mcp-command.test.ts` "rejects unknown
servers and servers without OAuth for login and logout". The browser is a test
double that fetches the authorization URL and follows the redirect.

**S32 OAuth options.** Tests: mirrors of "uses the configured callback URL and
scope", "registers with the configured client name", "adds the listening port
to a callback URL without one", "uses the configured authorization server
metadata URL", "resolves the OAuth client secret lazily", and the six "MCP
OAuth client ID metadata documents" cases, with the document base URL taken
from `oauth.clientMetadataBaseUrl` (DEV-06).

**S33 Provider auth.** Tests: mirror of "sends the provider token and asks
for the provider login when the server rejects it".

**S34 Manager view.** Build `manager_view.py` on pipy's existing selector and
overlay components (`ui/components/select_list.py`, `ui/modal_driver.py`).
Tests: unit tests of the menu models for each state (MGR-01, MGR-04, MGR-05);
mirrors of both `mcp-manager-view.test.ts` cases; one PTY test that opens
`/mcp`, disables a server and sees the list update; a tmux capture per the
`tmux-transient-ui-verify` skill for the sign-in screen.

**S35 Rendering.** Tests: collapsed and expanded rows; a resumed session with
MCP calls and no server; export output.

**S36, S37 Extension API.** Tests: mirrors of the four "AgentSession MCP
servers registered by extensions" cases; "activates another extension's
codemode tool when it reaches other tools", "does not activate another
extension's tool named codemode"; "supports tools that call other tools under
any name through the extension API". S37 needs a design note before code: an
extension tool that orchestrates runs as a composite parent on the session
thread through the same admission, reservation and `NestedToolCallService` as
codemode; that note is reviewed as part of S37.

**S38 SDK.** Tests: an SDK session with the fake server and the three options;
defaults connect nothing.

**S39 Images.** A group of commits: (1) image blocks in tool results in the
content model, session file and replay; (2) provider adapters; (3) rows and
export; (4) MCP image blocks, image resources and codemode `image()` (a new guest
output message kind carrying base64 data and MIME type, counted against the
existing emission cap; as built the images have a byte cap of their own, see
"Decided in S39 (images, part 5 ...)"). This also closes the backlog follow-on READ-IMG for
the shared part (decided in scope, Q2).
Tests: a direct MCP call returning an image block reaches each provider
adapter family's request as an image and survives resume; an image resource
read (RES-09); mirror of "attaches only the images the script passes to
image(), in output order, each after its saved path" from
`agent-session-codemode.test.ts`; `image()` with each accepted input form and
each rejected one (CMD-28); the same image shown twice is saved once; images
follow a truncated text block (CMD-30).

**X2 Leaked server processes.** Added after S40, when orphaned fake servers
were found alive hours after test runs. Tests: the fake server ends itself
after its parent ended; a module that leaves one behind fails; the real
command with a server that ignores its stdin, sent SIGTERM and SIGHUP in RPC
mode, SIGTERM in print mode, in the terminal UI and in `pipy mcp list`, ends
the server and exits with 143 or 129. See "Decided in X2".

**G1 Stdio close and a child that holds the pipes.** Added after the goal
check (finding C1): `StdioTransport` reported the close as soon as the
server exited, so `close()` sent no SIGKILL to a child in the server's group
that kept stdout or stderr open and ignored SIGTERM. Pi's `close()` resolves
on the child's `close` event (exit and pipes closed) and its SIGTERM and
SIGKILL timers for the group run until then. Now the watcher leaves an
exited server unreaped (`os.waitid` with `WNOWAIT`) until its pipes closed,
so `close()` can go on signalling the group without reaching a reused id;
it also does so when the server had exited before `close()` (Pi's `close()`
then returns at once without signals). Limits: after its SIGKILL `close()`
no longer waits for a holder that left the group (Pi waits without end);
`close()` from a reader's listener does not wait for that reader's pipe.
Without `os.waitid` (and when the system reaps the server itself because
SIGCHLD is ignored) the server is reaped in the same locked step that
untracks it, and its group is not signalled after that, also not with the
one SIGTERM after the exit that the earlier code sent; children that outlive
the server are then not ended. Tests in `test_native_mcp_stdio.py`:
`test_a_reaped_server_is_untracked_and_its_group_not_signalled`,
`test_close_kills_a_child_that_holds_the_server_output_and_ignores_sigterm`,
`test_close_after_the_server_exited_kills_a_child_that_holds_its_output`,
`test_close_does_not_wait_for_a_child_that_left_the_process_group`,
`test_close_without_waitid_does_not_wait_for_a_child_that_holds_the_output`,
`test_without_waitid_a_reaped_server_is_not_tracked_while_a_child_holds_its_output`,
`test_close_from_a_message_listener_does_not_wait_for_its_own_stream`,
`test_the_exit_hook_kills_a_child_that_holds_the_output_of_an_exited_server`.

**G2 Nested tool calls.** Added after the goal check (findings M1, M2,
M12, E2 to E5), in several commits. Async handlers (M1): an extension
tool's handler that returns an awaitable (an `async def` handler) is run to
its end with the driver the hooks use (`_drive_awaitable`) on the thread
that called it, also as a nested call; command and shortcut handlers too,
as Pi awaits all three. Before, a tool returned the text of the unstarted
coroutine as its successful result. Renderers, `prepare_loadout` and
`reaches_tools` stay synchronous, as in Pi. Test:
`test_an_async_tool_handler_is_awaited`,
`test_dispatch_awaits_an_async_command_handler`,
`test_dispatch_awaits_an_async_shortcut_handler`.
The outcome (E5): `ctx.execute_tool` returns `ToolCallOutcome` (`tool_call`
with id, name and arguments, `result`, `is_error`, `duration_ms`), and
`ToolResult` gained `blocks`, so a nested result keeps its images and a tool
can return them. `NestedToolResult` carries the blocks, the call id, the
arguments and the duration from the service's outcome. `duration_ms` is the
time of the whole nested pipeline (hooks included), `None` only for a name
that is not callable; Pi measures `execute()` alone and has none for a call
that did not run. Tests: `test_a_nested_result_keeps_its_images`, the
`calls` rows of `test_a_tool_runs_other_tools_through_the_extension_api`.
Depth and concurrency (M2, E2, E3): `NestedToolCallService` now serves the
nested calls of one parent at every depth. Every nested call gets
`nested_tools` for its own id (`<caller>/<n>` with a counter per caller), and
the record is shared, as Pi's recorder is. A tool may call itself, except
`codemode` (runner attribute `reaches_itself`). The pipeline is split: the
loop's `_NestedPipeline.settle` runs admission, `tool_call` hooks and the
readiness wait on the session thread and returns a `PendingNestedCall`,
whose `execute` runs the tool on any thread and whose `finish` settles it on
the session thread from the then current policy state. A request from another
thread waits in the service's queue; `serve`, the waiter of whatever the
session thread waits for (the parent's executor, or the executor of a call a
script made), takes it, begins it and runs its tool on a thread of its own,
so calls run concurrently. `decide_tool_admission(pending=)` counts the
calls admitted and not yet settled, so concurrent calls cannot settle past
the budget. A call the pipeline is running (from a hook) is still refused.
When `serve` returns, the calls it started have ended: an interruption ends
them as interrupted, the end of the waited-for tool cancels them with the
new `ToolExecutionInterruption.CALLER_STOP` (`tool cancelled by its
caller`, an ordinary cancelled error like `SCRIPT_STOP`). A requested call's
readiness wait ends like `serve`'s own wait and also when the waited-for
tool or the call's own caller ended; a call admitted after its caller ended
is settled as cancelled without running. A session without
a waiter runs the parent on a worker too and waits for it alone; Ctrl-C
there is raised again after the parent was cancelled. Tests:
`test_a_nested_tool_runs_tools_itself`, `test_a_tool_calls_itself`,
`test_calls_from_several_threads_run_at_the_same_time`,
`test_a_running_nested_call_is_cancelled_when_its_parent_ends`,
`test_without_a_waiter_the_handler_runs_on_a_worker_and_is_served`,
`test_a_nested_call_runs_tools_through_the_same_service`,
`test_concurrent_nested_calls_take_their_place_in_the_budget`,
`test_a_script_child_serves_the_calls_of_its_own_threads`,
`test_a_call_waiting_for_readiness_ends_with_its_parent`,
`test_a_call_waiting_for_readiness_ends_with_its_own_caller`,
`test_calls_of_nested_calls_are_rows_of_the_parent_row` (live rows of every
depth, `tool_rows.is_nested_under`).
Signal and updates (E4, M12): `ctx.execute_tool(..., signal=, on_update=)`
(and `executeTool(name, args, {signal, onUpdate})`); `ctx.signal` is the
tool's own cancel event and the default signal of its calls. A set signal
ends the call's readiness wait and its executor wait with `CALLER_STOP`. Each
nested call's tool gets an output sink: it calls `on_update` on the writing
thread and hands the chunk to the session thread, which emits the new
`NestedToolCallUpdated` (JSON/RPC `tool_execution_update` with
`parentToolCallId`, chunk text as `partialResult` like pipy's direct
updates) while the call is live; chunks after its end are dropped. A call
made on the session thread (`service.call`) always runs its tool under
`serve`, also without a waiter, so output written on other threads is
published. Tests: `test_a_nested_call_has_a_signal_and_updates`,
`test_a_signal_ends_the_readiness_wait_of_a_call`,
`test_live_output_of_another_thread_is_published_on_the_session_thread`, the
update rows of `test_loop_to_live_transcript_and_all_projections`.

**G3 A script's calls at the same time.** Added after the goal check
(finding M3; DEV-03 is now ported). The Python form of Pi's `Promise.all`:
`tools.<name>.submit(args)` (and `tools["<name>"].submit(args)`) sends the
call and returns a pending call at once; its `.result()` waits for the reply
and returns the value or raises the `ToolError` that `tools.<name>(args)`
would, each time it is asked. A plain call waits for its own reply as before.
Protocol: the guest may send further messages before a reply, and replies
come in the order the calls settle; a request stays outstanding until its
reply is written in full, at most `MAX_PENDING_REQUESTS` (16, prelude and
host share it) at once. The prelude waits for a reply before it sends a 17th;
the host treats a 17th as a protocol violation, so the buffer of unread
replies stays bounded. Host: `run_script(start_tool=)` starts each call on
the session thread in the order the guest made it and settles it later
(`call_tool` remains for callers that run a call to its end); the outcome's
calls keep that order and a call that did not settle before the run ended is
`cancelled`, as Pi's records start out. A `done` hands the calls of its chunk
to the session thread before the end, and `tool_stop` is then set only after
they were started, so a call made just before the script ends always starts;
after a violation, a cap, the deadline or an abort they do not start.
Service: `NestedToolCallService.batch(waiter, stop)` returns a
`NestedCallBatch`; `start` begins a call (admission, `tool_call` hooks,
readiness, budget, id) and runs its tool on a thread of its own, `pump` waits
for guest activity while it settles finished calls and serves the calls
their tools make, `end` ends and settles the calls still running. `stop` ends
a call's readiness wait and its tool with `SCRIPT_STOP`; an operator
interruption ends every running call. The record now keeps calls in the
order they began (a place is taken when a call begins, Pi's recorder), for
every nested caller, and a record with an unfinished call is not complete
(Pi's `snapshot`). A failure of the session's wait while calls run is the
parent's failure; while only the script computes it stays a parent error.
A nested call still running when the nested call that made it returned is
cancelled then (for every nested caller, as `serve` does for the tool it
waits for), not only at the end of the parent. After a `done` the deadline
still sets `tool_stop` while the calls made before it start, and a call the
end of the run cancelled does not change the script's outcome; a starter
that fails while those calls start is the outcome's sandbox error.
The codemode guideline names `submit()` where Pi names
`Promise.allSettled`. Not ported: Pi's exclusive queue (DEV-48). Tests:
`test_submitted_calls_run_at_the_same_time_and_the_record_keeps_their_order`
(Pi "runs nested calls in parallel and returns only the script result"),
`test_submitted_calls_take_the_budget_in_the_order_they_were_made`,
`test_a_call_still_running_when_the_script_ends_is_cancelled`,
`test_an_interruption_cancels_every_running_call`,
`test_a_call_a_script_call_made_ends_when_that_call_returns` in
`test_native_codemode_composite.py`;
`test_calls_run_at_the_same_time_and_settle_in_any_order`,
`test_a_call_made_just_before_done_always_starts`,
`test_requests_up_to_the_limit_run_and_get_their_replies`,
`test_a_starter_failure_before_done_is_the_outcome`,
`test_the_deadline_ends_a_start_that_blocks_after_done`,
`test_more_outstanding_requests_than_the_limit_are_a_violation` in
`test_native_codemode_host.py`;
`test_a_real_script_submits_calls_that_settle_in_any_order` in
`test_native_codemode_host_runtime.py`; the prelude units
`test_submitted_calls_are_sent_at_once_and_take_their_own_replies` and
`test_a_script_past_the_pending_limit_waits_for_a_reply`.

**G4 A script's store.** Added after the goal check (finding M4; the
`store()`/`load()` part of DEV-04 is now ported). Prelude: `store(key,
value)` and `load(key)` are synchronous globals over a snapshot of JSON
text (`ScriptStore`); `store` keeps the compact JSON text
(`store_json`: no ASCII escaping, `NaN` refused), `load` parses a fresh
copy, `store(key, None)` or `store(key)` deletes. Pi's limits and texts:
`MAX_STORE_VALUE_CHARS` (256 Ki) per value and `MAX_STORE_TOTAL_CHARS`
(1 Mi) for keys and values together, counted in Python characters; a key
that is not a `str` or a value that is not JSON raises `TypeError`, a
limit `ValueError` (Pi `RangeError`) with Pi's hint. A store over the
limit (from the branch) can only shrink. A delete of a key the snapshot
lacks is no write, so the writes are bounded by the snapshot and the limit.
Protocol: the run line carries `"store": {key: json}`; a successful `done`
that changed something carries `"writes": [[key, json], [key]]`. The host
parses each value strictly (surrogates scrubbed as on every guest line),
checks the per-value limit and, when the script set a value, the total of
the resulting store, so a forged `done` cannot pass the limits; a `done`
line may exceed the line cap by 20 bytes per character of the limit and
of the snapshot (`_Launch.done_line_bytes`), the most the prelude's
escaping can need. `run_script(store=)` takes JSON values and leaves out
what does not serialize (Pi `serializeStore`); `ScriptOutcome.store_writes`
(`StoreWrites(values, deleted)`) is empty unless the run succeeded,
including a success that a stop on the calling thread turned into an
error. Session: `native/coding/codemode_store.py` has the
`CodemodeStore` port, `CODEMODE_STORE_ENTRY_TYPE` (`codemode-store`) and
`read_codemode_store` (Pi's fold from the root, skipping malformed data);
`native/repl/codemode_session_store.py` has `SessionCodemodeStore` (the
headless coding package may not import the session tree), which reads
`get_branch()` and appends with
`append_custom` under the session's tree lock, on whatever tree is live
then. `CodemodeCompositeRunner(store=)` reads it before the script and
appends `{"set", "delete"}` after a successful script with writes, before
the result is recorded; without a store a script starts empty and its
writes are dropped (Pi without a session). The entry is not the extension
`append_entry` path, which truncates data over 64 KiB. Description: the
`Globals:` list gains Pi's `store`/`load` line. Not ported: Pi's
`entry_appended` event (pipy has none). Tests in
`test_native_codemode_store.py`: the prelude units (Pi "reads the snapshot
and reports writes", "returns copies", "rejects invalid keys, values, and
oversized writes", "explains oversized writes"); host checks against a
scripted guest (writes reported, failure drops them, malformed and
over-limit writes are violations, the done line allowance); a real guest
(store and load, an early exit keeps writes, Pi "ends the script
successfully on exit(), keeping output and store writes", failures drop
them, no store starts empty); the fold (Pi "folds store entries from the
root, ignoring malformed data"); sessions (Pi "persists store() writes as
custom entries for later calls", "appends nothing for failed scripts or
scripts without writes", "loads the values written on the current
branch", extended to resume, `/tree select` and `/fork`).

**G5 What a script gets and may emit.** Added after the goal check,
in several commits. Part 1 (finding M6, CMD-03): a structured result of
`null` is a value. `None` is the JSON `null` everywhere a result's
structured content travels; a result without one carries
`NO_STRUCTURED_CONTENT` (`agent/content.py`, exported by
`pipy_harness.extensions`), the default of `ToolExecutionResult`,
`AgentToolResultMessage`, `NestedToolResult`, `AgentToolResultTransform`,
`ToolResult`, `ToolResultEvent` and `ToolResultTransform` (Pi
`structuredContent: undefined`). A value that is not JSON becomes
`NO_STRUCTURED_CONTENT` (`json_safe_value`). The codemode package, which may
not import the agent, has its own `NO_VALUE` for `CallResult.value`; the
runner maps one to the other, so a tool with an output schema that returns
`null` gives the script `None`, also for an error result. Tests:
`test_a_reply_carries_text_a_structured_value_or_an_error` (host),
`test_script_receives_structured_content_text_or_a_tool_error` (real guest),
`test_null_structured_content_is_a_value_hooks_see_and_keep`,
`test_structured_content_is_any_json_value` (`None` added).
Part 2 (finding M15): `bash` declares Pi's `bashOutputSchema`
(`BASH_OUTPUT_SCHEMA`) and every command that ran to its exit carries
`{output, truncated, full_output_path?, exit_code, wall_time_seconds}` as
structured content, also with a non-zero exit (an error result for the
model, a value for a script, which does not raise). `output` is
`OutputAccumulator.read_full_output(1 MiB)` (Pi `readFullOutput`): the raw
output decoded with replacement characters, or past 1 MiB its first and last
512 KiB at character boundaries around `[... N bytes omitted ...]`;
`full_output_path` only then. If the temp file could not be kept, the tail
the model sees is the output. A timeout or an abort has no structured
content (Pi throws). The wall time is Pi's tenths of a second. Tests in
`test_bash_tool.py` (`test_the_output_schema_is_pis`,
`test_a_nonzero_exit_is_an_error_that_keeps_its_structured_result`,
`test_structured_output_over_one_mebibyte_keeps_head_and_tail`, ...) and
`test_a_script_gets_bashs_structured_result_also_for_a_failed_command`.
Part 3 (finding M16, the rest of READ-IMG): `read` attaches an image as Pi's
image branch does: `Read image file [<type>]` and the image as `blocks`,
with Pi's description. It declares Pi's `readOutputSchema`
(`READ_OUTPUT_SCHEMA`): the structured content is the text, or for an
attached image `{type: "image", data, mimeType, note}`, which `image()`
accepts. Pi's `processImage` needs Photon; pipy has no image codec, so
`tools/read_image.py` keeps what Pi does without changing pixels: an image
within 2000 x 2000 pixels and under 4.5 MB of base64 is sent as it is. As
nothing is decoded, the file's structure stands in for Photon's decode
(PNG chunks with their CRCs up to `IEND` with image data, JPEG segments up
to the scan with entropy data and an end of image, GIF blocks up to the
trailer with image data of a valid code size, the WebP RIFF chunks with a
frame, also inside `ANMF`) before the size is read, and a PNG within the
limits is inflated: its header must be valid and its data exactly the
rows (Adam7 passes too) it declares, each with a valid filter type. The
pixels of a JPEG, GIF or WebP are not decoded, so a file whose structure
is complete but whose coded data is corrupt is still attached, as MCP
images and `image()` images are. A larger image, one that is cut short or
malformed, and a BMP get Pi's `[Image omitted: could not be resized ...]` /
`[... could not be converted ...]` text as a result that is not an error.
EXIF orientation is not applied (it cannot change the outcome with equal
limits), and the non-vision note is not added (the tool does not know the
model; the provider request leaves the image out). An `@file` reference
reads with `ReadTool(attach_images=False)`: its excerpt is text, so an
image is refused there with a pointer to `@image:` as before. Tests:
`test_read_tool_returns_an_image_as_an_attachment`,
`test_a_text_only_reader_refuses_an_image`,
`test_resolve_image_reference_fails_closed_with_the_image_hint`,
`test_read_tool_omits_an_image_it_would_have_to_resize`,
`test_read_tool_omits_a_bmp_it_would_have_to_convert`,
`test_read_tool_structured_result_of_a_text_file_is_its_text` and
`test_a_script_reads_an_image_and_shows_it` (real guest, Pi docs "tools.read()
returns an image file as an image block").
Part 4 (finding M17): Pi's options line. `codemode/source.py` ports
`parseCodemodeSource`: an optional first line `// @options: {...}` (Pi's
`//` form, not Python) with `max_output_tokens` (a safe integer from 0) and
`timeout_ms` (1 to 2^31 - 1), replaced by an empty line so line numbers
stay; an empty script, an options line without a script after it and any
other field or value are an error result with Pi's message (naming Python
for JavaScript), before a worker starts. `ScriptLimits.wall_seconds` is
`None` by default: no deadline, as in Pi; then the worker's epoch backstop
is `CPU_SECONDS_MAX`, now 25 days (above Pi's longest `timeout_ms` plus the
margin), and after a `done` only the runner sets `tool_stop`. The runner
takes `timeout_ms` as the deadline and `max_output_tokens` as the result
budget, lowered by `fitting_budget` so the result's text stays within
pipy's 64 KiB: at most `MAX_RESULT_OUTPUT_TOKENS` (16000) and less what the
images' path lines take (measured with `SaveOnce`, which saves each image
once for the measurement and the result); `truncate_output` takes 0. The description's
intro gains Pi's `Optional first line` bullet and names no wall time.
Tests: `test_native_codemode_source.py`,
`test_the_options_line_sets_the_deadline_and_the_output_budget`,
`test_the_options_line_keeps_the_line_numbers`,
`test_invalid_options_are_an_error_before_any_script_runs`,
`test_a_script_has_no_deadline_unless_it_sets_one`,
`test_the_budget_leaves_room_for_the_images_path_lines`,
`test_a_call_made_just_before_done_always_starts` (also without a deadline).
Part 5 (finding M18): Pi's output limits. `ScriptLimits.output_chars`
(16 Mi characters, Pi `MAX_OUTPUT_CHARS`) replaces `output_bytes` (1 MiB
of text) and `image_bytes` and counts text and base64 image data together;
`output_messages` stays 100,000 (Pi `MAX_OUTPUT_ITEMS`) and counts calls,
not pieces. The line limit stays 1 MiB: the prelude sends a text value in
pieces of `TEXT_PIECE_CHARS` (64 Ki characters, at most 12 bytes each
escaped; a surrogate pair is never split, so JSON still joins it), each
but the last with `"more": true`, one after another; the host holds the
pieces apart and adds them as one item with the last, so an item that
passes the cap leaves nothing of itself (Pi checks a call's output whole);
a continued piece shorter than half a piece, and any other message while a
text is open, is a violation, so the pieces held stay few. Past a limit the script fails with Pi's message (`script
output exceeded the limit of ... characters or ... text(), image() and
print calls. Print a summary instead, or write large data to a file with a
tool.`). The prelude's own `image()` check (`MAX_IMAGE_CHARS`) stays. So a
script emitting 1,100,000 characters completes, and its output is cut to
the budget and spilled whole. Tests: `test_text_in_pieces_is_one_item`,
`test_text_in_pieces_must_be_continued`,
`test_a_text_message_with_a_bad_continuation_flag_is_a_violation`,
`test_text_in_pieces_over_the_cap_leaves_nothing_of_itself`,
`test_a_short_text_piece_is_a_violation`,
`test_wire_text_sends_long_text_in_pieces_keeping_surrogate_pairs`,
`test_a_surrogate_pair_across_a_piece_boundary_stays_one_character`,
`test_long_text_reaches_the_host_whole` (real guest),
`test_images_and_text_share_the_output_cap_and_the_message_cap`,
`test_output_over_the_line_limit_is_truncated_and_spilled` (real guest).

**G6 Tool hooks and argument validation.** Added after the goal check, in
several commits. Part 1 (findings M8 and E8 for `tool_call`, M9, M10): the
agent loop runs Pi's `prepareToolCall` order for direct and nested calls
alike. After admission it asks the tools for `validated_arguments(call)`
(`ToolExecutor.validated_arguments`, also on the capability snapshots and
`SessionExecutionProjections`; part of `AgentToolCapabilities` and
`NestedCallableTools`): the parsed, prepared and validated arguments, or the
malformed outcome, which settles without the `tool_call` and `tool_result`
policy. The policy port is `before_execute(call, arguments, parent_call_id)`;
`apply_extension_tool_policy` hands `arguments` itself to
`dispatch_tool_call_hooks` as `ToolCallEvent.input`, so a handler changes the
arguments in place (Pi "mutate `event.input` in place"). `ToolCallEvent`
gains `tool_call_id` (the call's id, `<parent id>/<n>` for a nested call)
and `parent_tool_call_id` (`None` for a call the model made). The tool
runs the arguments the hooks left: `execute(..., arguments=...)` (on
`ToolExecutor` and every capability surface) validates them again but does
not run `prepare_arguments` again, so a tool's preparation runs once per
call, as in Pi. A composite parent reads its arguments from the call, so
when the hooks changed them (compared as sorted JSON) it runs a copy of the
call whose `arguments_json` is the changed arguments; the transcript, the
events, the result policy and the nested-call record keep the call as it
was asked for. The validation of changed arguments is pipy's (DEV-64): an
error there is an error result that does not count as a malformed call, and
arguments that are not JSON block the call with `tool_call hooks left
arguments that are not JSON`. Tests:
`test_native_tool_call_hook_arguments.py` (session level: a hook changes the
arguments, sees the call id and the calling tool's id, invalid arguments
reach no hook), `test_arguments_that_fail_validation_reach_no_tool_policy`,
`test_the_arguments_a_tool_policy_leaves_are_what_runs`,
`test_arguments_a_tool_policy_made_invalid_are_not_malformed`,
`test_arguments_a_tool_policy_left_that_are_not_json_block_the_call`,
`test_nested_calls_are_validated_and_the_policy_gets_their_parent_id`,
`test_a_nested_call_runs_with_the_arguments_the_policy_left`,
`test_given_arguments_are_validated_but_not_prepared_again`.

**S40 Closeout.** Build the `just mcp-conformance` recipe that runs the
official suite's client scenarios against a small driver using
`McpServerConnection` and `sign_in_mcp_server`, with a committed baseline;
complete `docs/mcp.md`; audit every row of section 2 against a test name and
record the table in the acceptance note.

## 6. Test strategy

### 6.1 Fixtures

`tests/fixtures/mcp_fake_server.py` is one scripted MCP server usable over
stdio and over HTTP, written with the standard library only and sharing no
code with `native/mcp/` (it must not validate the client with the client's own
parser).

- **Stdio**: started as `python -I tests/fixtures/mcp_fake_server.py
  --scenario <file>`. Reads and writes newline-delimited JSON.
- **HTTP**: `FakeMcpHttpServer` wraps the same scenario engine in a
  `ThreadingHTTPServer` on `127.0.0.1` port 0, speaking streamable HTTP: JSON
  or SSE answers per request, session ids, a GET stream, `DELETE`.
- **Scenario**: a JSON description of capabilities, instructions, tools
  (schemas, annotations, output schemas), resources and templates, and per
  method scripted behaviour: delay, progress notifications, error, status code
  sequence (for retries), drop connection, close the SSE stream after N
  events, emit `list_changed`, write to stderr, ignore stdin close, spawn a
  child, send log messages, require a bearer token, answer 401 or 403 with a
  `WWW-Authenticate` value, expire the session.
- **Recording**: every received request, its headers and timing are available
  to the test (in memory for HTTP, a side file for stdio).

`tests/fixtures/mcp_fake_oauth.py` is a fake authorization server and
protected-resource metadata endpoint: discovery documents at configurable
paths, dynamic registration, authorize (returns the redirect a browser would
follow), token with rotating refresh tokens, configurable failures, a hanging
endpoint, optional `iss`, CIMD support flags. `FakeBrowser` is the test double
that follows the authorization URL to the loopback callback.

An in-memory transport pair (`native/mcp/transport.py`) serves the client unit
tests, as in Pi.

Tests that start processes use the fake server only; nothing in the default
suite needs Node, `npx`, `uvx` or the network.

No fake server outlives a test run (added in X2, after orphaned servers of
failed and interrupted runs were found alive hours later):

- A scenario can make the server ignore the end of its stdin and SIGTERM, and
  a step can sleep for minutes. Nothing ends such a server when the process
  that started it is killed. So the stdio server, and a `spawn` child, ends
  itself `orphanGraceSeconds` (default 300) after its parent process ended and
  records the event `orphaned`; the server also counts from the end of its
  stdin, which shows the end of a parent it did not see. The time is far
  longer than any test waits for a server to end, so it does not hide a
  client that fails to end one.
- `tests/mcp_fake_server_guard.py` is a pytest plugin loaded by
  `tests/conftest.py`. After the last test of each module it looks, with
  `ps`, for fake server processes (a `spawn` child is the fixture run with
  `--sleeper`, so it is found too) whose scenario file is below the base
  temporary directory of this run. A process that is still there after 2 s is
  killed and the module fails with the command lines; the directory in a
  command line is named after the test. Another test run on the same machine
  is never touched. The check is per module, not per test, because a `ps`
  per test would add minutes to the suite.
- The helper that ends a killed TUI session in the PTY tests
  (`test_native_mcp_readiness_pty._finish`) ends that session's servers.

### 6.2 Layers

| Layer | What it proves | Where |
| --- | --- | --- |
| Unit | Protocol, client, SSE, config, naming, truncation, BM25, declarations, section rendering, OAuth flow steps | `tests/test_native_mcp_*.py`, one file per module |
| Connection | State machine, retries, reconnect, close, with the fake server over both transports | `tests/test_native_mcp_connection*.py` |
| Session | Fake provider plus fake server: declared tools, codemode calls, readiness, activation, section, reload, resume | `tests/test_native_mcp_session*.py` |
| Process | Two-process refresh lock, `pipy mcp` exit codes, process-group cleanup | `tests/test_native_mcp_process*.py` |
| Terminal | `/mcp` manager | PTY test plus tmux capture |
| Shared state | Lock order, publication atomicity, off-thread UI, attempt currency after disable and enable | part of S17, S21 |

Codemode tests that need the real WASI runtime follow the existing
`runtime required` marking; discovery and result-shape logic is also tested
against `tests/codemode_fake_worker.py` so it runs everywhere.

### 6.3 Pi cases to mirror

The slice details name them. In total the plan mirrors:

- `packages/mcp/test/`: `client.test.ts` (12 cases), `content.test.ts` (2),
  `stdio.test.ts` (1), `streamable-http.test.ts` (14 of 15), `oauth.test.ts`
  (12).
- `packages/coding-agent/test/`: `mcp-extension.test.ts` (all 27),
  `mcp-command.test.ts` (8), `mcp-manager-view.test.ts` (2),
  `mcp-oauth-refresh.test.ts` (11), `mcp-oauth-store.test.ts` (1 of 3; the two
  legacy-key cases fall under DEV-08), `tool-search.test.ts` (9),
  `suite/agent-session-mcp.test.ts` (all 48), `suite/agent-session-mcp-oauth.test.ts`
  (9), `suite/agent-session-tool-orchestration.test.ts` (3), and from
  `suite/agent-session-codemode.test.ts` the cases about reaching tools
  ("presents callable tools per codemode.mode", "shows the guidelines of tools
  that do not fit the inline budget through describeTool()", "routes nested
  calls through extension hooks", "keeps structured content that tool_result
  handlers replace along with the content").
- `packages/codemode/test/declarations.test.ts`: the `schemaToType` and "tool
  declarations" groups (14 cases), with Python syntax.

A mirrored test keeps Pi's case name in a comment so the final audit can match
them.

### 6.4 Official conformance suite

Pi runs `@modelcontextprotocol/conformance@0.2.0-alpha.11` client scenarios
for protocol versions `2025-03-26`, `2025-06-18` and `2025-11-25` and compares
with a baseline whose known failures are `elicitation-sep1034-client-defaults`,
`auth/basic-cimd` and `auth/scope-retry-limit`. S40 adds the same run as a
manual recipe (DEV-12). The acceptance bar is that pipy's baseline has no
failing check that passes in Pi's `test/mcp-conformance/baseline.json`.

### 6.5 Live evidence

Automated tests do not prove daily use. Before closing the campaign: one
stdio server through `uvx` or `npx` and one OAuth HTTP server, driven through
`pipy mcp add`, `pipy mcp list`, `/mcp`, a codemode script and a `direct`
call, recorded in an acceptance note under `docs/acceptance/`.

## 7. Owner decisions

Recorded 2026-10-12 through the campaign coordinator.

| ID | Question | Decision |
| --- | --- | --- |
| Q1 | CIMD identity (DEV-06): which Client ID Metadata Documents does pipy identify with? | **Open with the owner.** Until decided, the plan ships no default document: `cimd` works only with a configured base URL (DEV-06). This keeps OAU-33 and OAU-34 closable in S32 and blocks no other slice. |
| Q2 | Images in tool results (S39). | **In scope.** The owner asked for the complete feature at functionality parity. S39 stays a group of several commits. |
| Q3 | Extension orchestration hooks (S37: `ctx.execute_tool`, `prepare_loadout`, replacing `codemode`, `tool_search` and `/mcp` from an extension). | **In scope**, for the same reason. |
| Q4 | Client name sent to servers (DEV-05). | **`pipy`**, with `oauth.clientName` as the documented override for servers that accept only known client names. |
| Q5 | Where slices land, given the trunk rule in `AGENTS.md`. | **On `feat/mcp-parity`, pushed per slice.** The merge to `main` is an owner approval at the end of the campaign. This is a campaign exception to the trunk rule; `AGENTS.md` is not changed. |
| Q6 | The callable snapshot of 4.3 revises the CM1 rule that only tools advertised in the parent's frozen request snapshot are callable. | **Accepted.** S03 changes the rule in code and updates `docs/codemode.md` and the CM1 spike spec (section 4.7) in the same commit. |
