# MCP and codemode tool reach: Pi parity plan

Status: plan, 2026-10-12. Work item `pipy-mcp-codemode-parity`, backlog ID MCP1.
No product code is changed by this document.

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
reason. Section 4 is the pipy design, section 5 the ordered slices, section 6
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
behaviour (with the `pi`/`pipy` substitution).

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

Row count: 280 (CFG 26, CLT 16, STD 7, HTP 11, CON 16, TLS 19, SES 20, SEC 7,
EXP 12, TSR 11, CMD 30, RES 10, OAU 36, CMDL 14, SLH 6, MGR 10, RND 4, EXT 16,
SDK 4, DOC 5; the abbreviation CLT stands for the client library and CMDL for
the shell commands).

## 3. Proposed deviations

Each item is a Pi behaviour this plan proposes not to port, or to port in a
different form. Items marked "decided" restate an owner decision. The others
are proposals the final judgement should weigh; section 7 records what the
owner has decided.

| ID | Pi behaviour | Proposal | Reason |
| --- | --- | --- | --- |
| DEV-01 (decided) | JavaScript script API: `searchTools`, `describeTool`, `describeNamespace`; TypeScript declarations. | Python names `search_tools`, `describe_tool`, `describe_namespace`; declarations rendered as Python-style types with `#` comment lines. Same information (CMD-11 to CMD-15). | Codemode is Python by decision. |
| DEV-02 (decided) | Config and state under `~/.pi/agent/`, project `.pi/`. | `<config home>/mcp.json`, `mcp-auth.json`, `mcp.log`; project `.pipy/mcp.json`. | Decision. |
| DEV-03 | Scripts run tool calls in parallel (`Promise.all`). | Nested calls stay sequential, as CM1 specified. | Parallel tools are a standing backlog deferral with its own evidence gate; MCP calls run through the same nested service. Not an MCP rule. |
| DEV-04 | Codemode features that are not about reaching tools: raw-source input with a grammar, `return`, `console.*`, `exit()`, the `// @options:` line, `store()`/`load()`, `models.*`, structured `bash` results, image results from `read`. | Not in this campaign. They remain CM1 follow-ons in the backlog. | They do not change whether or how MCP and extension tools are reached. `image()` is the exception and is in slice S39. |
| DEV-05 | The client identifies as `pi` in `initialize` and in dynamic client registration. | `pipy` and pipy's version. `oauth.clientName` covers servers that accept only known client names, as in Pi. | Product name. |
| DEV-06 | CIMD documents are served from `https://pi.dev/oauth/`, built in. | The mechanism is ported in full (OAU-33, OAU-34), but there is no built-in document. `<base>` comes from a pipy-only field `oauth.clientMetadataBaseUrl` (an `https` URL) next to `clientRegistration: "cimd"`; the user hosts `client.json` and, for servers without `iss`, `<id>/client.json` there. `cimd` without that field is a config error: `server "<n>": oauth.clientRegistration "cimd" requires oauth.clientMetadataBaseUrl`. If the owner later names a default (section 7, Q1), the field becomes optional. | pipy has no hosted document, and identifying as Pi's document is an identity claim only the owner can make. |
| DEV-07 | `/login` offers to configure the Radius MCP server after a Radius sign-in. | Not ported. | pipy has no Radius provider. `auth.provider` itself is ported (OAU-36). |
| DEV-08 | Credentials stored by older Pi versions under the URL alone are taken over by the first server that loads them. | Not ported. | No pipy version ever wrote that key; the no-shim rule applies. |
| DEV-09 | Windows process handling (`taskkill`, `~\` paths, `.cmd` shims). | POSIX behaviour is ported and tested; Windows is not verified. | pipy's platform evidence is macOS and Linux (see `docs/codemode.md`). |
| DEV-10 | Tool arguments are validated against the full JSON Schema of the tool before the call. | MCP and other foreign-schema tools are validated with a standard-library validator for a named keyword set (S01); keywords outside it are not enforced by pipy and the server remains the authority. Native and extension tools keep pipy's strict subset. | No JSON Schema dependency in the runtime (`pyproject.toml` keeps two dependencies). The named set is pinned by tests. |
| DEV-11 | MCP, codemode and tool search are built-in extensions built only on the public extension API. | They are native modules. The public extension API still gains every hook Pi documents for them (section 2.18). | pipy's codemode is a native composite tool with a WASI host; rebuilding it on the extension API is an architecture change the backlog rules out. |
| DEV-12 | The official MCP conformance suite runs in CI through `npx`. | A manual `just` recipe runs the same suite against pipy's client with a committed baseline (S40); CI runs pipy's own mirrored cases (section 6). | The suite needs Node and network access; pipy's CI has neither as a requirement. |

| DEV-13 | The token usage and cost of nested results, at every depth, is added to the calling tool's result usage and counts toward the session cost. | Not ported in this campaign. pipy tool results carry no usage field. | Nothing in scope produces tool usage: MCP tools and pipy's built-in and extension tools report none, and the only Pi producers are `models.classify()` and `models.generateImages()` (DEV-04). The field and its propagation belong to the slice that adds a usage-producing tool. |

Not listed as deviations because Pi does not implement them either: the legacy
SSE transport, elicitation, sampling, prompts, resource subscriptions, MCP App
rendering, the stateless `2026-07-28` protocol.

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
| `oauth/` (`types.py`, `errors.py`, `fetch.py`, `discovery.py`, `flow.py`, `provider.py`, `callback.py`) | The OAuth client library; `fetch.py` is the HTTP fetch Pi gets from JavaScript | `mcp/oauth/*` |
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
| `commands.py`, `manager_view.py` | `/mcp` command; TUI manager | `ext/index.ts` command, `ext/ui.ts` |

Other new modules: `native/tool_search.py` (tokenizer, BM25, document
builder, the `tool_search` tool) and `native/codemode/declarations.py`
(schema to Python type text, tool samples).

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
- The default transport factory raises for a `url` entry until S26. The
  factory signature already carries the auth provider (always `None` until
  S30). `resolve_config_headers` exists for S26; `oauth.clientSecret` is
  resolved by S30's `oauth_settings` with `resolve_config_value`.
- Left to their slices: the retry loop and its wait (S26, with the
  retry-wait half of CON-09), `withClient`'s retries and session renewal
  (S26), `needs-auth`, `reconnect` and `sign_out` (S21, S30, S31), resources
  at connect time (S23).

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

Open after the merge, owned by later slices:

- Fixtures: the stdio fake is single-threaded, so a `sleep` step blocks it.
  The HTTP fake has no POST status sequences, no session expiry or
  session-id enforcement and no 403 with a challenge (S26, S30). The OAuth
  fake serves scripted routes only: registration, authorize and token
  endpoints and `FakeBrowser` come with S28, S29 and S31.
- `McpAuthProvider` has `token()` only. S30 adds the 401 handoff, which has
  to cover the GET stream too.

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
rule; `BUILTIN_NESTED_NAMES` is deleted and `eligible_names()` returns the
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
  changed during its preparation, so a server that connects in that window
  makes `/reload` report `prepared reload owner state changed`. S22 closes
  the manager before it prepares the reload, which removes the window.

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
resume and reload the names are restored through
`NativeToolCapabilities.restore_active_tools`, which today drops unknown
names. For TSR-10 the dropped names that look like MCP tools are kept as
pending names in the capability value and activated when their tool registers
before the next prompt starts; the pending set is cleared at prompt start.

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
| S40 | Conformance recipe, user docs pass, changelog, parity audit against section 2 | DOC-01 to DOC-05 | M | all | no |

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
error `streamable HTTP is not available yet`, and until S23 the report has no
resource counts. Both gaps are named in the slice's changelog line and closed
by the rows' later slices. Tests: mirrors of the eight `mcp-command.test.ts`
cases except login, with stdio servers. Docs:
`docs/mcp.md`, `docs/usage.md` "MCP commands".

**S21 `/mcp` without the TUI.** Build the command, status text, `reconnect`,
completion, `set_enabled`, `set_exposure`, `save_config`. Tests: status text
for each state; reconnect after the fake server was killed; disable closes the
process; exposure change moves a tool between declared and callable; a failed
save changes nothing; RPC mode prints the status.

**S22 Reload and resume.** Build 4.10. Tests: mirrors of the three "MCP tools
after resume and reload" cases; reload with a removed, an added and a changed
server; a reload that fails validation keeps the running manager.

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
HTTP transport for `url` entries (S15 built it for stdio only); remove the
S20a placeholder error. Tests: `pipy mcp list` and a session against the fake
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
existing emission cap). This also closes the backlog follow-on READ-IMG for
the shared part (decided in scope, Q2).
Tests: a direct MCP call returning an image block reaches each provider
adapter family's request as an image and survives resume; an image resource
read (RES-09); mirror of "attaches only the images the script passes to
image(), in output order, each after its saved path" from
`agent-session-codemode.test.ts`; `image()` with each accepted input form and
each rejected one (CMD-28); the same image shown twice is saved once; images
follow a truncated text block (CMD-30).

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
  `suite/agent-session-mcp.test.ts` (all 44), `suite/agent-session-mcp-oauth.test.ts`
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
