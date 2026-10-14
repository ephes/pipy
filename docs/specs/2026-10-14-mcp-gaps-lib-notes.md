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

## L1, commit 2: C2 (close interrupts a connection attempt) and C4 (redirects as fetch follows them)

Pi source read again: `packages/mcp/src/transports/streamable-http.ts`,
`close` (line 261, `this.controller.abort()`) and `authorizedFetch` (line
288 to 293: every fetch gets `signal: this.controller.signal` and no
`redirect` option, so the default `follow` applies). What `follow` does was
read in the fetch implementation Pi runs on, undici (Node's global `fetch`;
read in the copy under pi-mono's `node_modules/undici`, version 8.10.2,
`lib/web/fetch/index.js`, `httpRedirectFetch`), which
follows the fetch standard's "HTTP-redirect fetch" step by step and adds
three header names in step 13. Both findings hold.

What changed, all in the default fetch (`mcp/http_fetch.py`):

- C2. The TCP connect and the TLS handshake are made on a non-blocking
  socket and wait for the socket together with a wake-up that the request's
  `CancelToken` triggers. A request that is given up (timeout,
  cancellation) and `close()` end the attempt at once and close the socket.
  A name lookup still runs to its end; its thread opens no socket then.
  `send_timeout_ms` bounds the connect (per address) and the handshake as
  before.
- C4. Redirects are followed to any origin, by these rules of
  `httpRedirectFetch`: statuses 301, 302, 303, 307, 308 with a `Location`;
  at most 20, the twenty-first is a network failure
  (`http.client.HTTPException: Too many redirects requesting <url>`; before,
  the redirect answer was returned); a `Location` that is no http(s) URL or
  has user info is a network failure (`http.client.InvalidURL: Cannot follow
  the redirect of <url> to <location>`; before, the redirect answer was
  returned); 303 for a method other than GET and HEAD, and 301 or 302 for a
  POST, change the method to GET and drop the body and the headers
  `Content-Encoding`, `Content-Language`, `Content-Location` and
  `Content-Type` (before, only `Content-Type`); a change of origin drops
  `Authorization`, `Proxy-Authorization`, `Cookie` and `Host` from the
  request for all later hops; everything else is sent on, so configured
  headers such as an API key header and `Mcp-Session-Id` reach the other
  origin, as in Pi.

Changelog lines (section Unreleased):

- An HTTP MCP server that redirects to another host or port is followed, as
  in Pi. The OAuth or provider token and a configured `Authorization`,
  `Proxy-Authorization`, `Cookie` or `Host` header are not sent to the other
  origin; other configured headers are. Before, such a redirect failed with
  the redirect's status.
- Closing an HTTP MCP server, a request timeout and a cancelled call end a
  request that is still connecting or in its TLS handshake at once. Before,
  its thread and socket stayed for up to 30 seconds.

Plan rows and texts:

- HTP-11 and CON-09 stay closed; their "aborts requests in flight" and
  "stops a connect in progress" now hold for a connection attempt too. The
  texts that say otherwise are superseded: the S24 block ("Connection
  attempts ... cannot be interrupted by cancel or close").
- Section 4.1, the bullet "Redirects, which have no checklist row: the HTTP
  transport follows one only within the configured origin" becomes: the HTTP
  transport's default fetch follows redirects as Node's fetch does (rules
  above). The OAuth fetch half of the bullet is unchanged (finding O2,
  another track).
- Section 4.8, row "Credentials": "Sent only to the configured server URL"
  is no longer true as written. New text: the bearer token and a configured
  `Authorization`, `Proxy-Authorization`, `Cookie` or `Host` header are sent
  only to the origin of the configured server URL and to origins it
  redirects to without a change of origin; other configured headers and the
  session id follow a redirect to another origin, as with Pi's fetch.
- docs/mcp.md no longer lists redirects under "Different from Pi" for HTTP
  servers. Remaining differences, recorded under "Where the library
  differs": a `Location` is resolved with `urllib.parse.urljoin` and the
  transport's strict URL split, not a WHATWG URL parser; a name lookup
  cannot be interrupted (in Pi the fetch rejects at once and the lookup ends
  on a libuv thread).
