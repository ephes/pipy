# MCP parity gaps in the client library: notes for the merge

Track L of the MCP parity campaign works on `feat/mcp-parity-gaps-lib` and
does not edit `CHANGELOG.md`, `docs/backlog.md` or
`docs/specs/2026-10-12-mcp-parity-plan.md`. This file holds what belongs
there, one section per item; the merge into `feat/mcp-parity` folds it in.

The findings are those of the goal check of 2026-10-13 (gpt-6.1-sol, area
"client"), numbered C1 to C7 there. Pi reference: `~/src/pi-mono` at
a447ba11.

## L1, commit 1: C3 (JSON answers have no size limit) and C6 (`fetch` option)

Pi source read again: `packages/mcp/src/transports/streamable-http.ts`
(`send` line 246, `await response.json()` with no size check; `consumeSse`
line 337, where `maxMessageBytes` becomes `maxEventBytes`; the constructor
line 205, `options.fetch ?? globalThis.fetch`; `close` line 266 and
`authorizedFetch` line 288, which both send through `this.fetch`) and
`packages/mcp/README.md` line 28 ("Fetch can be injected for proxying or
custom networking"). Both findings hold.

What changed:

- `StreamableHttpTransport` reads an `application/json` answer as a whole.
  `max_message_bytes` limits one event of an event stream only. The error
  `MCP HTTP message exceeds <n> bytes` no longer exists.
- New module `src/pipy_harness/native/mcp/http_fetch.py` with
  `McpHttpRequest`, `McpHttpResponse`, `McpFetch` and
  `create_default_fetch()`. `StreamableHttpTransportOptions` has a new field
  `fetch: McpFetch | None`. Every request of the transport (POST, the GET of
  a stream, the DELETE of a session) goes through it. The code that
  connects and follows redirects moved from `http.py` into the default
  fetch without a change of behaviour.

Changelog lines (section Unreleased):

- An HTTP MCP server may answer with a JSON body of any size. The 16 MiB
  limit now applies to one event of an event stream, as in Pi; a larger
  JSON answer failed with `MCP HTTP message exceeds <n> bytes` before.
- `StreamableHttpTransportOptions(fetch=...)`: a program that builds on
  `pipy_harness.native.mcp` can pass the function that sends the HTTP
  requests of a transport, for a proxy or a network of its own (Pi's
  `fetch` option). See `pipy_harness.native.mcp.http_fetch`.

Plan rows and texts:

- HTP-03 stays closed; no row said that a JSON body has a limit. The limit
  was an addition of S24.
- Section 4.8, row "Limits": "16 MiB per message" holds for a stdio line
  and for one event of an HTTP event stream, not for an HTTP JSON answer.
- The S24 notes ("A JSON answer body is limited to max_message_bytes")
  are superseded.
- New fact for section 4.1 (module layout): `mcp/http_fetch.py`.
- A deviation to record next to the `fetch` option: the function is not
  JavaScript's `fetch`. It takes one request object and returns a response
  object (`status`, `header`, `read`, `chunks`, `close`), and it gets a
  `CancelToken` where Pi passes an `AbortSignal`. An auth provider is still
  not handed the transport's fetch (`UnauthorizedContext` has none, as
  decided in S30).
