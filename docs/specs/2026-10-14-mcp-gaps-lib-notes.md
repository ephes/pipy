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

## L2, commit 2: O4 (path encoding before the resource comparison)

Pi source read again: `packages/mcp/src/oauth/discovery.ts`,
`selectResource` (line 202): both URLs go through `new URL`, whose
`pathname` percent-encodes the path percent-encode set (`"`, `#`, `<`, `>`,
`?`, `^`, `` ` ``, `{`, `}`, space, C0 controls, DEL) and every non-ASCII
character as UTF-8, keeps existing escapes as written and leaves `|` alone
(checked with Node 26: `/café/mcp` against `/caf%C3%A9` matches, against
`/caf%c3%a9` does not, `/a|b` against `/a%7Cb` does not). The finding holds:
pipy compared `/café/mcp` with `/caf%C3%A9` as written and stopped the
authorization.

What changed, in `src/pipy_harness/native/mcp/oauth/discovery.py`:
`select_resource` encodes both paths that way (after removing dot segments,
as before) before the prefix comparison. The `resource` value sent is still
the one the metadata names, and the error text still names the server URL
as `parse_http_url` writes it.

Changelog line (section Unreleased):

- Signing in to an MCP server whose configured URL has a non-ASCII or
  otherwise unencoded path character (`https://example.com/café/mcp`) works
  when the server's protected resource metadata writes that path
  percent-encoded. Before, the sign-in stopped with `Protected resource ...
  does not match MCP server ...`.

Plan rows and texts:

- OAU-06 stays closed; its text does not change.
- The S27 notes that say no character of a path is percent-encoded before
  the comparison in `select_resource` are superseded by the module text.
- A difference that stays: the host is compared as `parse_http_url` reads
  it, without IDNA, so a server URL with `bücher.de` and a resource with
  `xn--bcher-kva.de` do not match, where Pi's `URL` writes both in punycode.
  Store keys and the CIMD callback id are computed over the URL as
  `parse_http_url` writes it, as recorded since S30 and S32.

## L2, commit 3: O7 (credentials keyed by the URL alone)

Pi source read again:
`packages/coding-agent/src/extensions/mcp/oauth.ts`, `storeKeys` (line 130,
`legacyKey = String(new URL(serverUrl))`), `forServer().load` (line 155:
when `states[key]` is falsy and `states[legacyKey]` truthy, the legacy entry
is moved to `key` under the file lock), `tokens` (line 199, `states[key] ??
states[legacyKey]`, nothing moved) and `remove` (line 206, deletes `key`, or
else `legacyKey`), with `test/mcp-oauth-store.test.ts` ("moves credentials
stored by SERVER_URL to the first server that loads them", "signs out of
credentials stored by SERVER_URL"). The finding holds: pipy looked only at
`<namespace>|<url>`, so an `mcp-auth.json` carried over from an older Pi had
a grant that could be neither used nor signed out of.

What changed, in `src/pipy_harness/native/mcp/credentials.py`:
`McpOAuthServerStore.load` takes a legacy entry over as Pi does (checked
without the lock, moved under the file's lock after checking again),
`McpOAuthCredentialStore.tokens` falls back to it, and `remove` deletes it
when the server has no entry of its own. Pi's JavaScript truthiness is kept
for the takeover check (an own entry that is `null` does not keep a legacy
one out; a legacy entry that is `null`, `""`, `0` or `false` is not taken).
The legacy key is the URL as `parse_http_url` writes it plus `#<fragment>`
when there is one.

Changelog line (section Unreleased):

- OAuth credentials that an older Pi stored in `mcp-auth.json` under the
  server URL alone are used: the first server with that URL takes them
  over, and `/mcp logout` deletes them. Before, they were ignored.

Plan rows and texts:

- OAU-18 stays closed; add to its text: an entry keyed by the URL alone
  (older Pi) is taken over by the first server that loads it, read by
  `tokens` and deleted by a sign-out (Pi `storeKeys`).
- DEV-08 is removed (closed): legacy keys are ported.
- The test inventory note in the plan (line 3473 as judged: "legacy-key
  cases fall under DEV-08") changes: the two legacy cases of
  `mcp-oauth-store.test.ts` are mirrored in
  `tests/test_native_mcp_credentials.py`.
- A difference that stays: the legacy key is not Pi's WHATWG `href` where
  the two differ (percent-encoding of the path, a URL that ends in an empty
  fragment `#`), so such an entry is not found.

## L2, commit 4: O5 (a counterpart of Pi's `adaptOAuthProvider`)

Pi source read again: `packages/mcp/src/oauth/flow.ts`,
`adaptOAuthProvider` (line 452) and `stepUpScope` (line 314), re-exported
from `oauth/index.ts`; used in `packages/mcp/README.md` (line 89) and in
`test/oauth.test.ts` ("discovers, registers, authorizes with PKCE, and
refreshes on 401", "shares one refresh between concurrent 401s when refresh
tokens rotate", "asks for authorization instead of refreshing when the
server needs more scope"). The finding holds: pipy's library had no way to
connect an `OAuthClientProvider` (for example `McpOAuthProvider` with a
`MemoryOAuthStateStore`) to the HTTP transport; `McpServerAuthProvider`
needs the file-backed server store.

What changed:

- `src/pipy_harness/native/mcp/oauth/flow.py`:
  `adapt_oauth_provider(provider, *, fetch=None)` returns an
  `OAuthTransportAuth`, an `McpAuthProvider` with `token()` (the provider's
  access token) and `on_unauthorized(context)`: as Pi, a 401 whose token
  was replaced already (and no run in flight) returns for a retry; else one
  shared run of `authorize_mcp` with the challenge's resource metadata URL
  and scope, or for `insufficient_scope` with `skip_refresh` and
  `step_up_scope(granted, challenged)`; `REDIRECT` raises
  `McpOAuthAuthorizationRequiredError`. Requests that arrive during a run
  wait for it and get its outcome (Pi's shared `inFlight` promise; here an
  event under a lock, as `McpServerAuthProvider` does).
- `step_up_scope` moved from `mcp/sign_in.py` to `mcp/oauth/flow.py`, where
  Pi has it; `sign_in.py` imports it from there.
- New test file `tests/test_native_mcp_oauth_adapter.py` (Pi's three cases,
  one through `StreamableHttpTransport` and `McpClient` against the fake
  OAuth MCP server).

A difference that stays: the adapter's flow requests go through the
library's OAuth fetch, or the one passed as `fetch=`; Pi passes
`context.fetch`, the transport's fetch. `UnauthorizedContext` still has no
fetch (decided in S30), and the transport's fetch has another interface
than the OAuth fetch.

Changelog line (section Unreleased):

- `pipy_harness.native.mcp.oauth.flow.adapt_oauth_provider(provider)`: a
  program that builds on pipy's MCP library can connect an HTTP MCP server
  that wants OAuth with any OAuth client provider and a store of its own,
  with a shared refresh after a 401 and a step-up authorization for more
  scope (Pi's `adaptOAuthProvider`).

Plan rows and texts:

- No checklist row covered the adapter (the goal check called it "not in
  the checklist"). Suggested new row in the OAU block: "`adaptOAuthProvider`
  connects any `OAuthClientProvider` to the HTTP transport: the provider's
  access token; after a 401 a shared run of the flow, a retry when the
  token was replaced meanwhile, a step-up authorization for
  `insufficient_scope`; `REDIRECT` fails the request with
  `McpOAuthAuthorizationRequiredError`. | `mcp/oauth/flow.ts`
  `adaptOAuthProvider` | L2", closed.
- S28 block (line 1255 as judged): remove "Pi's `adaptOAuthProvider` is not
  ported; S30 builds the connection's provider directly." New text: pipy's
  connections build their provider directly (S30);
  `adapt_oauth_provider` is the library's counterpart of Pi's adapter
  (L2).
- S31 block (line 1385 as judged): `step_up_scope` now lives in
  `mcp/oauth/flow.py`, as in Pi; `merge_scopes` stays in `sign_in.py`.

## L3, commit 1: K1 (URLs are read as Pi's `new URL` reads them)

Findings of the goal check of 2026-10-13, area "config and CLI", numbered
K1 to K7 there. Pi source read again: `core/mcp-servers.ts` lines 128-134
(`isLoopbackRedirectUri`: `URL.canParse`, `new URL(value).hostname`), 182,
196, 203, 273 and 285 (`new URL`), and `packages/mcp/src/transports/
streamable-http.ts` line 204 (`this.url = new URL(options.url)`, the URL every
request is sent to). Node 26 was used as the oracle for every URL in the new
tests. The finding holds: `http://127.1:8080/mcp`, `http://0x7f.0.0.1/` and
`http://2130706433/` were config errors in pipy and are `127.0.0.1` in Pi.

What changed:

- `config.parse_http_url` follows the WHATWG URL parser for `http` and
  `https`: any number of `/` and `\` after the scheme (`http:example.com`,
  `http:\\h\a`), `\` ends the authority and is `/` in the path, the host is
  percent-decoded (`%6cocalhost` is `localhost`; invalid UTF-8 there is an
  error), a host that ends in a number is parsed with the WHATWG IPv4 rules
  (one to four parts, decimal, `0x` hex or `0` octal, the last one filling
  the remaining bytes), the port follows the first `:` outside brackets, and
  an IPv6 host is written with hex pieces only (`[::ffff:102:304]`).
  `HttpUrl.has_user_info` is true only for a non-empty user or password, as
  undici's check (`http://@h/` is no user info).
- `is_loopback_redirect_uri` follows: `http://127.1/cb`,
  `http://2130706433/cb`, `http://%6cocalhost/cb` and `http://[0:0::1]/cb`
  are loopback URIs, as in Pi. The same holds for the loopback checks of
  `auth`, `oauth.authServerMetadataUrl` and the OAuth endpoints.
- `http_fetch.parse_http_target` reads the URL with `parse_http_url` instead
  of `urllib.parse.urlsplit`, so the transport connects to `127.0.0.1` for
  `http://127.1/mcp` (Host header `127.0.0.1:<port>`) and removes dot
  segments from the path, as Pi's `new URL` does. `HttpTarget.url` and the
  URL the transport hands its fetch and its auth provider are the URL as
  `parse_http_url` writes it (href plus fragment), Pi's `this.url`.

Remaining differences (in `config.py`'s module text and docs/mcp.md): no
IDNA processing (a non-ASCII host is kept as written, lower-cased, where
Pi writes punycode or rejects it), and `HttpUrl.path` keeps characters a
WHATWG parser percent-encodes (consumers encode them where it matters).

Changelog line (section Unreleased):

- MCP server URLs and OAuth URLs are read as Pi reads them: shortened and
  hex or decimal IPv4 hosts (`http://127.1:8080/mcp`), percent-escapes in
  the host, and missing or extra slashes and backslashes after the scheme
  are accepted, where they were configuration errors before. `127.1` and
  the other spellings of `127.0.0.1` count as loopback hosts.

Plan rows and texts:

- CFG-13, CFG-18, CFG-20, CFG-21 stay closed; the deviation "pipy checks
  URLs with a small strict parser and does not repair them" is removed,
  replaced by "no IDNA processing".
- Section 4.1 (line 621 as judged): "`config.py` owns the URL parser
  (`parse_http_url`, `HttpUrl`) that config validation and OAuth discovery
  share. It is strict and repairs nothing." becomes: "... that config
  validation, OAuth and the HTTP transport's default fetch share. It
  follows the WHATWG URL parser for `http` and `https`, without IDNA."

Addendum to commit 1 (from its review): `parse_http_url` reads its input
as a USVString, so a lone surrogate (which JSON can spell, `"\ud800"`) is
U+FFFD as in Node, and a host with U+FFFD (from a surrogate or from
percent-escaped bytes that are no UTF-8) is not a URL. A surrogate in the
path is requested as `%EF%BF%BD`, as Node does; it no longer raises
`UnicodeEncodeError` out of `load_mcp_config` or the transport.

## L3, commit 2: K2 (non-UTF-8 config files), K3 (option numbers), K7 (config home in the quick setup); K4 checked

Pi source read again: `extensions/mcp/config.ts` line 96 (`readConfigFile`:
`JSON.parse(readFileSync(path, "utf8"))`) and line 232 (`editMcpServers`,
the same read, then `writeFileSync` of `JSON.stringify`); `extensions/mcp/
cli.ts` line 245 (`Number(parsed.values.get("timeout") ?? 300)`, then
`Number.isFinite(timeout) && timeout > 0`) and line 344
(`callbackPort: Number(port)`), the only two numeric options of `pi mcp`.
Node 26 was the oracle for the decoding and for every `Number()` value in
the new tests.

What changed:

- K2: `config._read_text` decodes with `errors="replace"`. Node's `utf8`
  decoding and Python's substitute U+FFFD for the same maximal invalid
  sequences (checked with `a\xff\xed\xa0\x80b\xf0\x9f\x98c`, four and one
  replacement characters in both). A configuration with such bytes in a
  string loads; a server name with them is an invalid name for that entry
  only; editing such a file (`mcp add`, `mcp remove`, the `/mcp` manager)
  writes the replacement characters back, as Pi does. A UTF-8 BOM is still
  a JSON error in both.
- K3: `shell_cli._port_number` became `_js_number`, JavaScript's
  `Number(text)`: decimal and exponent spellings, `Infinity` with a sign,
  `0x`/`0o`/`0b` integers in either letter case and without a sign, `0` for
  blank text, NaN otherwise (no `_` separators, no `inf`/`nan`). It reads
  `--oauth-callback-port` of `mcp add` (`0x50` writes `callbackPort: 80`)
  and `--timeout` of `mcp login` (`0b1` is one second).
- K7: docs/mcp.md "Quick setup" names the real config home chain:
  `$PIPY_CONFIG_HOME`, else `$XDG_CONFIG_HOME/pipy`, else `~/.pipy` when
  that directory exists, else `~/.config/pipy` (`resolve_config_home` via
  `workspace_context.resolve_global_instruction_root`), and that `pipy mcp
  add` prints the file it wrote. "Configure servers" now says "`~/.pipy`
  when it exists" too.
- K4 (refuted by the verification, confirmed here): Pi's
  `McpCommandOptions.credentials` (`cli.ts` line 82) is a test seam. Pi's
  only caller is `main.ts` line 618, `runMcpCommand(args.slice(1), { cwd,
  agentDir })`; `src/index.ts` exports no `runMcpCommand` or
  `McpCommandOptions` (only config types and `createMcpExtension`, lines
  220 and 411-412), and `package.json` `exports` has no path to `cli.ts`.
  No user or SDK caller can pass another store, so pipy's commands always
  using `mcp-auth.json` of the config home is no gap. Nothing changed; the
  deviation line in `shell_cli.py` ("there is no option for another
  store") stays as a description.

Removed deviations (module texts and docs/mcp.md): "A file that is not
valid UTF-8 is an error" (config); "`--oauth-callback-port` and `--timeout`
take a decimal number" (`pipy mcp` commands).

Changelog lines (section Unreleased):

- An `mcp.json` with bytes that are not UTF-8 inside a string loads, with
  replacement characters there, as in Pi; the whole file was rejected
  before.
- `pipy mcp add --oauth-callback-port` and `pipy mcp login --timeout` read
  their number as Pi does with JavaScript's `Number()`, so `0x50`, `0o17`,
  `0b1` and `1e3` are numbers.

Plan rows and texts:

- CFG-02, CMDL-04, CMDL-13 and DOC-01 stay closed. Remove the deviations
  about non-UTF-8 files and decimal-only option numbers wherever the plan
  lists them (line 1409 as judged: "`--oauth-callback-port`, as a decimal
  number" becomes "as JavaScript's `Number()` reads it").
- K4: no row; the goal check's verification refuted it, and this item
  confirms that.

## L4: K5 and O1 (`auth.provider` tokens from every source a model request uses)

Pi source read again: `extensions/mcp/index.ts` line 575 (`providerToken:
async (provider) => modelRegistry?.getApiKeyForProvider(provider)`),
`core/model-registry.ts` lines 197-203 (`getApiKeyForProvider` returns
`(await this.runtime.getAuth(provider))?.auth.apiKey` and catches),
`packages/ai/src/auth/resolve.ts` (`resolveProviderAuth`: a runtime key
override, then the stored credential, which owns the provider, then
ambient/env), `core/runtime-credentials.ts` (`--api-key` is a credential
overlay for one provider) and `main.ts` line 837 (`setRuntimeApiKey(
sessionOptions.model.provider, ...)`). Both findings hold.

What changed:

- `provider_login_token` answers with the key a model request to the
  provider would carry, from the same sources in pipy's model-request
  order: for `openai-codex` the Codex login file when there is a login
  (what a model request to a Codex model uses; refreshed and saved through
  `OpenAICodexAuthManager.get_credentials`; without a login the general
  sources below, which a `models.json` model of that provider with another
  `api` uses; a login that cannot be read or refreshed is no token); then the session's `--api-key`; then `auth.json`
  (read at every call; a stored entry owns the provider, refresh through the
  session's `OAuthCredentialCache` for `PER_REQUEST_OAUTH_PROVIDERS`); then
  the API-key environment variable; then the `models.json` `apiKey`,
  resolved like a stored key. A provider an extension registered is
  answered from `auth.json` and the environment only (login sent as stored),
  as before.
- New signature: `refreshes` is gone; the function takes `replaced`
  (an extension registered the provider), `runtime_api_key`,
  `models_json_key` and `codex_login`, each a function asked at the call.
  `repl/mcp_loading.provider_token` binds them to the live catalog
  (`extension_provider_for`, `runtime_api_key`,
  `catalog.provider_request_configs`) and an `OpenAICodexAuthManager` for
  the catalog's Codex login file.
- Unchanged rules from S33: request threads never touch the session's
  `AuthStore`; a damaged `auth.json` is no token and no later source
  (environment, `models.json`) is used in its place; `close`/`settled` of
  the auth provider; any failure is no token.
- `native/openai_codex_provider.py` (outside the track's files, needed
  because model requests and MCP requests now both refresh the Codex
  login): `OpenAICodexAuthManager.get_credentials` loads, refreshes and
  saves under one process-wide lock (`_CREDENTIALS_LOCK`), so a refresh
  token that rotates is never sent twice by two threads.
- `native/models_json.py` (outside the track's files, needed for the
  thread rule): `ModelCatalog.refresh` no longer empties
  `provider_request_configs` before the merge refills it; the map is
  replaced by one assignment, so an MCP request thread never sees it empty
  while the session thread refreshes the catalog after a reload. Same
  result as before.

Removed deviations (module text, docs/mcp.md "A provider's token"): a
`models.json` key, `--api-key` and the `openai-codex` login are no longer
"no token for an MCP server". Still different, documented: ambient cloud
credentials are no token; `--api-key` applies to every provider (Pi: the
starting model's provider only), as for pipy's model requests; an OAuth
login pipy does not refresh for its own requests is sent as stored; the
token is per provider, so with a Codex login an `openai-codex` server gets
the login even where a `models.json` model of that provider with another
`api` uses a stored key.

Changelog lines (section Unreleased; replace the end of the `auth` entry's
first sentence, "the token that `/login <provider>` stored for that model
provider, or the provider's API-key environment variable", with "the key a
model request to that provider would use"), plus:

- An MCP server with `"auth": {"provider": "<provider>"}` is also sent the
  `openai-codex` login, the key given with `--api-key` and the `apiKey`
  that `models.json` configures for the provider, as a model request to
  that provider would use them; these sources sent no token before.

Plan rows and texts:

- OAU-36 stays closed. In "Decided in S33", replace the bullet "Pi's
  function is `modelRegistry.getApiKeyForProvider` ..." with the order
  above, replace "Whether a login is refreshed is asked at each call
  (`refreshes`) ..." by "Which providers an extension registered, the
  `--api-key` and the `models.json` request settings are read from the
  live catalog at each call (attributes replaced by assignment)", and
  shorten "Narrower than Pi" to: ambient cloud credentials, `--api-key`
  for every provider, no refresh of a login pipy does not refresh for its
  own requests, a `!command` key run for every MCP request.

## G7: T4 (`images.blockImages` for tool result images) and T5/M22 (tool result images normalized and resized)

Pi source read: `coding-agent/src/utils/tool-result-images.ts`
(`normalizeToolResultImages`), `utils/image-process.ts` (`processImage`),
`utils/image-resize.ts` (`formatDimensionNote`), `utils/image-resize-core.ts`
(`resizeImageInProcess`, defaults 2000x2000, 4.5 MB of base64, JPEG quality
80 then 85/70/55/40, shrink by 0.75), `utils/image-convert.ts`,
`utils/exif-orientation.ts` (JPEG and WebP only), `core/agent-session.ts`
`_afterToolCall` (lines 684-723: hooks, then normalization with
`getImageAutoResize()` and the model's `inputLimits.images.resize`, also for
nested calls) and `core/sdk.ts` `convertToLlmWithBlockImages` (line 297 ff.).
`packages/ai/src/providers/data/*.json`: every `inputLimits.images.resize`
equals the defaults. All three findings hold.

What changed:

- New module `native/image_process.py` (`process_image`, `resize_image`,
  `format_dimension_note`, `decode_base64`, `ImageResizeOptions`,
  `ResizedImage`, `ProcessedImage`): Pi's `processImage` with Pillow in place
  of Photon. Pillow is the new optional extra `images` (`pillow>=12,<13`) and
  is in the dev group; without it every call fails and the image is kept,
  as Pi does when Photon cannot load. Only Photon's decoder formats are
  opened (PNG, JPEG, GIF, WebP, BMP, ICO, TIFF, TGA, PNM, DDS); over
  `PIL.Image.MAX_IMAGE_PIXELS` an image is not decoded.
- New module `native/tool_result_images.py` (`normalize_tool_result_images`,
  `normalize_tool_result`, `tool_result_normalizer`).
- `AgentLoop` and `CodingAgentRunCoordinator` take `normalize_result`; the
  loop calls it in `_after_tool_call` after `_transform_tool_result` (the
  `tool_result` hooks) for direct, nested and composite-parent results,
  before `ToolCallCompleted` is emitted and the result is stored.
  `repl/loop_step.py` passes `tool_result_normalizer(scope.settings)`.
- `SettingsManager.get_image_auto_resize()` and `get_block_images()`.
- `agent/messages.py`: `BLOCKED_IMAGE_PLACEHOLDER` ("Image reading is
  disabled.") and `without_blocked_images` (Pi's map and filter: consecutive
  placeholder texts, also ones the tool wrote, collapse to one).
  `ProviderMutationEffects.request_history` applies it first when
  `images.blockImages` is set, read per request, then the non-vision
  downgrade.

Changelog lines (section Unreleased):

- Images in tool results (MCP tools and resources, codemode `image()`,
  extension tools) are processed as Pi processes them before they are
  stored and sent: an image larger than 2000x2000 pixels or 4.5 MB of
  base64 is scaled down, with a text that gives the original size and the
  coordinate scale, and an image of another type than PNG, JPEG, GIF or
  WebP is converted to PNG. `images.autoResize: false` turns the resizing
  off. This needs the new optional extra `images` (Pillow); without it the
  images are sent as before.
- `images.blockImages: true` keeps tool result images from every provider
  request; each becomes the text `Image reading is disabled.`.

Plan rows and texts:

- No row of the plan covered either behaviour (the findings were "not in
  checklist" next to TLS-11, RES-09 and CMD-29). Suggested new rows next
  to TLS-11: "Tool result images, nested ones included, are processed after
  the `tool_result` hooks: other types become PNG, oversized images are
  resized to the model's limits with a dimension note, failures keep the
  image (`utils/tool-result-images.ts` `normalizeToolResultImages`,
  `core/agent-session.ts` `_afterToolCall`)" and "With `images.blockImages`
  every tool result image is replaced by `Image reading is disabled.`,
  consecutive ones once (`core/sdk.ts` `convertToLlmWithBlockImages`)".
- The S39 decision text and notes-A4 limit "TLS-11: like Pi, a server's
  image is not checked" are superseded: oversized images are resized; type
  and base64 are still not validated, a failed decode keeps the image.
- New deviations for section 6 (all documented in docs/providers.md "Size
  of tool result images and `images` settings"): Pillow instead of Photon
  and only with the `images` extra (without it no resizing); resized bytes
  differ; over about 89 million pixels no decode; limits are Pi's defaults,
  not per catalog row (pipy's catalog has no `inputLimits`; every Pi row
  repeats the defaults); `images.blockImages` does not touch `@image`
  attachments (Pi replaces user message images too); a note that would
  take a result's text past 64 KiB is dropped. Pi resizes in a worker
  thread; pipy resizes on the thread that runs the tool loop.
- Module table (section 4.1): `native/image_process.py`,
  `native/tool_result_images.py`.

Audit test references: `tests/test_native_tool_result_image_normalize.py`
(unit tests of processing, the loop hook order with a nested call, and two
session tests: the resized image and note in the next request, and
`images.blockImages`).

## G8, commit 1: T1 (`--tools ''` selects no tools) and T2 (the no-tools flags with `--tools`)

Pi source read: `coding-agent/src/cli/args.ts` (`--tools ""` gives `[]`),
`src/main.ts` lines 540-550 (`noTools: "all" | "builtin"`, `tools` copied
when given, also empty), `src/core/sdk.ts` lines 280-293 (the default
selection is empty under either `noTools`; a plain `tools` list is the
allowlist whatever `noTools` says; modifiers start from the empty selection
and are the allowlist only under `"all"`) and `src/core/agent-session.ts`
(constructor: an empty allowlist filters MCP tools; `_isAllowedTool`;
`_refreshToolRegistry`: under `"builtin"` the built-in tools stay
registered and only the initial selection activates them). Both findings
hold.

What changed:

- `cli._tool_filter_options_from_args` folds the flags as Pi does: a given
  `--tools` list, also an empty one, is the allowlist (`--tools ''` is
  `no_tools`); `--no-tools` with modifiers makes what they add from nothing
  the allowlist; `--no-builtin-tools` with modifiers keeps them as
  modifiers of an empty default selection; `--no-tools` wins over
  `--no-builtin-tools`.
- `ToolFilterOptions`: `no_tools` is Pi's empty allowlist and
  `no_builtin_tools` Pi's `noTools: "builtin"`. The value refuses
  `no_tools` with names, modifiers or `no_builtin_tools`, and
  `no_builtin_tools` with an allowlist. `provider_visible_names` lost its
  `builtin_names` parameter: `no_builtin_tools` no longer unregisters
  built-in tools. `ToolCapabilityState.filter_configured` ignores
  `no_builtin_tools`, like modifiers, so a selection is carried across a
  reload as without flags. `build` and `prepare_extensions` start the
  default selection empty under `no_builtin_tools` (modifiers apply to it,
  the `defaultTools` setting is not read; neither is it under `no_tools`).
  `codemode` and `tool_search` are no longer removed by
  `no_builtin_tools` (they are extension tools in Pi), so MCP can still
  activate them.
- The adapter's first system prompt, made before extensions load, lists
  under `--no-builtin-tools` only the built-in tools modifiers add.

Changelog lines (section Unreleased):

- `--tools ''` disables every tool, MCP tools included, as `--no-tools`
  does; before, an empty list was the same as no list.
- `--no-tools` and `--no-builtin-tools` only empty the default selection,
  as in Pi: `pipy --no-tools --tools tool_search` declares `tool_search`,
  `--no-builtin-tools --tools read` declares `read`, and
  `--no-builtin-tools --tools +read` activates `read` and keeps the
  extension tools. Under `--no-builtin-tools` the built-in tools stay
  registered (an extension can select one), and `codemode` and
  `tool_search` stay available for MCP.

Plan rows and texts:

- EXP-06 and EXP-09: the empty `--tools` list and the combination with
  `--no-tools`/`--no-builtin-tools` now match Pi; the audit can cite the
  tests below. The capability docstring note "Pi's empty allowlist is
  `no_tools` here" now also holds for `--tools ''`.

Audit test references: `tests/test_harness_native_cli.py`
(`test_tool_selection_flags_combine_as_in_pi`,
`test_tool_filter_flags_parse_pi_aliases`),
`tests/test_native_tool_capabilities_mcp.py`
(`test_an_allowlist_after_no_tools_declares_tool_search`,
`test_no_builtin_tools_keeps_builtins_registered_and_inactive`,
`test_modifiers_after_no_builtin_tools_start_from_nothing`),
`tests/test_native_tool_capabilities.py`
(`test_filter_precedence_is_selection_then_exclude`, the validation test).
