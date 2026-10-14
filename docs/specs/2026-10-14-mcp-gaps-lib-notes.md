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

## L1, commit 3: C5 (tool and resource refreshes run independently)

Pi source read again:
`packages/coding-agent/src/extensions/mcp/runtime.ts`, `connectOnce` (lines
399 to 404: each of the two notification listeners starts
`void this.refreshTools(client)` or `void this.refreshResources(client)`)
and the two functions (lines 458 to 477). The finding holds: in pipy one
thread listed one kind after the other, so `tools/list` waited for a
resource listing that the server was slow to answer, for up to the request
timeout.

What changed, in `src/pipy_harness/native/mcp/connection.py`:

- A link keeps one refresh state per kind (`tool_refresh`,
  `resource_refresh`), and each kind is listed again on a thread of its own:
  `mcp-refresh` for the tools as before, `mcp-refresh-resources` for the
  resources. A change of one kind never waits for a listing of the other.
- Unchanged: one listing of a kind runs at a time, and a change that arrives
  during it is read by one more listing; changes announced while the server
  connects are read after the connection is published, now by up to two
  threads.

Changelog line (section Unreleased):

- A server's changed tool list is read again at once, also while the server
  is slow to answer a listing of its resources (and the other way round).
  Before, the two were read one after the other.

Plan rows and texts:

- CON-05 and RES-10 stay closed.
- Section 4.1, in the S15 block: "A tool list change is handled by a thread
  (`mcp-refresh`) that lives only while a refresh is wanted" still holds for
  the tools; add that resource changes have a thread of their own,
  `mcp-refresh-resources`.
- Section 4.1, in the S23 block: remove "`notifications/resources/list_changed`
  uses the refresh thread of the tool list; it lists one kind per round, so a
  slow resource listing delays a tool refresh (Pi runs them independently)".
  New text: `notifications/resources/list_changed` is handled like a tool
  list change, on a thread of its own, as Pi runs the two independently.
- A difference from Pi that stays, as before this change: Pi starts one
  listing per notification, so several of one kind may overlap and the last
  answer wins; pipy runs one listing of a kind at a time and lists once more
  when a change arrived meanwhile.

## L2, commit 1: O2 (OAuth requests with a body follow redirects) and O3 (compressed OAuth answers)

The findings are those of the goal check's area "oauth", O2 and O3. Pi
source read again: `packages/mcp/src/oauth/flow.ts` (`tokenRequest` line
223, `registerClient` line 259: `(options.fetch ?? globalThis.fetch)(url,
{method: "POST", headers, body, signal})`, no `redirect` option) and
`packages/mcp/src/oauth/discovery.ts` (`fetchMetadata` line 58, the same).
What the default `follow` and the transparent decoding do was read in
undici 8.10.2 under pi-mono's `node_modules`, `lib/web/fetch/index.js`:
`httpRedirectFetch` (line 1254; step 11 fails only for a body without a
source, which a string and a `URLSearchParams` have; step 12 method change;
step 13 header removal), `httpNetworkOrCacheFetch` (line 1571,
`Accept-Encoding`) and the response handler (lines 2262 to 2335, the content
codings), and `lib/web/fetch/util.js` `createInflate` (line 1243). Both
findings hold.

What changed, all in the default fetch of the OAuth library
(`mcp/oauth/fetch.py`):

- O2. Redirects are followed by the rules the HTTP transport's default
  fetch got in L1: 301, 302, 303, 307, 308 with a `Location`, to any origin,
  at most 20. A 307 and a 308 send method and body again. A 303 for a method
  other than GET and HEAD, and a 301 or 302 for a POST, change the method to
  GET and drop the body and `Content-Encoding`, `Content-Language`,
  `Content-Location`, `Content-Type`. A change of origin drops
  `Authorization`, `Proxy-Authorization`, `Cookie` and now also `Host`. The
  body goes along to another origin, as with `fetch`: the form of a token
  request (authorization code, refresh token, `client_secret` with
  `client_secret_post`) is sent where the token endpoint redirects with 307
  or 308. The owner may want to see this, as with the credentials row of L1.
- O3. A request sends `Accept-Encoding: gzip, deflate` unless the caller set
  the header. `Content-Encoding` is undone from the last coding to the
  first: `gzip`, `x-gzip`, `deflate` (zlib or raw, told apart by the first
  byte as undici does). A compressed body that ends early gives what it
  held (undici decodes with `Z_SYNC_FLUSH`). After a gzip member comes
  another member or zero bytes (padding), as Node's zlib reads it; other
  data there is a decoding failure. Data after a deflate stream is ignored. A list that names another
  coding leaves the body as it arrived. No decoding for HEAD, for 101, 204,
  205, 304 and for a redirect that is followed. More than five codings and
  data that is not in the coding are network failures
  (`http.client.HTTPException`), as the fetch rejects or its body errors
  with a `TypeError` in Pi. The 1 MiB limit holds for the body as it arrived
  and after each decoding.

Changelog lines (section Unreleased):

- Signing in to an MCP server works when the authorization server answers a
  client registration or a token request with a 307 or 308 redirect: the
  request is sent again where it moved, as in Pi. Before, it failed with the
  redirect's status.
- Answers of an authorization server that are compressed with gzip or
  deflate are read. Before, such metadata, registration and token answers
  failed as invalid JSON.

Plan rows and texts:

- OAU-02, OAU-03, OAU-08, OAU-11 stay closed; their text does not change.
- Section 4.1, the bullet on redirects (line 615 in the plan as judged):
  "the OAuth fetch follows one only for a request without a body and drops
  credentials headers across origins" becomes: the OAuth fetch follows
  redirects by the same rules as the HTTP transport's default fetch, with
  the body for 307 and 308.
- The S28 block: remove "The default fetch does not follow the redirect of
  a POST."
- The S27 notes on `oauth/fetch.py`, where they say "no transparent
  decompression" or list redirects for requests without a body only, are
  superseded by the module text.
- A deviation to record for the OAuth fetch: `br` and `zstd` are neither
  asked for nor decoded (Node asks for both over `https`); user info in a
  `Location` is refused; a `Location` is resolved with `urljoin`.
