# `before_provider_headers` Extension Hook Design

Status: parity-loop design for the next bounded extension slice

## Scope

Add Pi's `before_provider_headers` event to pipy's Python extension runtime and
wire it to every pipy-owned real HTTP provider path. Keep the event separate
from the extension-surface `agent_settled` event, durable entry renderers,
dynamic tool loading, and package command realignment. The deterministic fake
provider has no outgoing HTTP request and therefore does not emit this event.

Pi reference (local checkout `/Users/jochen/src/pi-mono` at `b084d2fb`):

- `packages/coding-agent/src/core/extensions/types.ts:665-675` defines the
  event payload.
- `packages/coding-agent/src/core/extensions/runner.ts:999-1028` dispatches
  handlers serially in extension/load order, awaits each handler, ignores its
  return value, preserves in-place mutations, and isolates handler failures.
- `packages/coding-agent/src/core/sdk.ts:306-332` assembles request-scoped
  headers, fires the hook once, and passes the result into `streamSimple`.
- `packages/coding-agent/docs/extensions.md:653-671` specifies add/override,
  `null` deletion, once-per-provider-request dispatch, and retry reuse.
- `packages/ai/src/types.ts:102` defines `ProviderHeaders` as
  `Record<string, string | null>`; `packages/ai/src/utils/headers.ts:11-19`
  removes the `null` entries at the concrete HTTP boundary.

## Pinned public contract

`BeforeProviderHeadersEvent` has exactly these public fields:

- `headers`: a mutable mapping whose keys are header names and whose values are
  `str | None`. Existing assembled values are present. Setting a string adds or
  overrides that exact key; setting `None` deletes it before transport.
- `type`: the forced literal `"before_provider_headers"`.

The handler arity is `(event, ctx)`. Synchronous and awaitable handlers are
supported. Handlers run serially in activated-extension/load order against the
same mutable mapping, so later handlers observe earlier mutations. Return values
are ignored. A handler exception is fail-soft: mutations completed before the
exception remain, later handlers still run, and the provider request continues.
`KeyboardInterrupt` and `SystemExit` remain control flow and propagate, matching
the other extension dispatchers.

Header keys remain case-preserving and are not normalized by the extension
dispatcher. Concrete HTTP clients retain their existing case-insensitive wire
behavior. After dispatch, only string values are forwarded; `None` entries are
removed. The extension event and header contents are live request data: they are
not appended to the native session tree, metadata archive, automation JSON/RPC
events, or diagnostics.

The context is the normal mode-aware extension context for the active product
session, including the read-only session manager (so a translated Pi extension
can obtain the current session id). In TUI mode existing live UI behavior is
available. In print/JSON/RPC modes `has_ui` is false: selection/input/editor
methods return immediately without stdin reads, and notifications use the
existing stderr-only diagnostic path; the hook never writes protocol stdout.

## Ownership and provider seams

Pipy owns header construction inside its provider adapters rather than in one
SDK `streamFn`. Preserve that boundary instead of moving provider auth or body
construction into the extension runtime:

1. Add a request-local header dispatcher callback to `ProviderRequest`. It is
   in-memory-only, excluded from serialization/archival, and defaults to `None`
   for every existing caller.
2. Add one provider helper that copies an adapter's assembled mutable header
   map, invokes the request callback once, and filters `None` before the wire.
   An adapter without a callback receives the same headers it does today.
3. The product tool loop installs the callback only when activated extensions
   registered `before_provider_headers`. The callback creates the event/context
   and dispatches all handlers. `/reload` replaces the active hook tuple for
   subsequent requests just as it does for `before_provider_request`.
4. Invoke the helper at the last pipy-owned seam before HTTP for the built-in
   OpenAI Responses, OpenAI Chat Completions (including ds4 and catalog aliases),
   OpenRouter, Anthropic, Google Generative AI, Google Vertex, Azure OpenAI,
   Mistral, Cloudflare Workers AI, Amazon Bedrock, and OpenAI Codex paths.
   Extension-registered provider ports receive the request-local callback on
   `ProviderRequest` and use the same public helper at their HTTP seam; document
   this requirement alongside `ExtensionProvider.factory`.

Provider-specific constraints:

- Amazon Bedrock dispatches against its base headers before SigV4 signing so
  added/overridden headers are signed and `None` deletions cannot invalidate a
  completed signature. Signer-owned `authorization`, `host`, and `x-amz-*`
  fields remain provider-owned, consistent with the existing reserved-header
  filter and with Pi applying extension option headers before provider-local
  signing/auth.
- OpenAI Codex creates one mutated request-header snapshot before its retry and
  transport-attempt loop. SSE retries, WebSocket retries, and pre-event
  WebSocket-to-SSE fallback reuse that snapshot without re-firing handlers.
  Transport-required fields (SSE `Accept`/`Content-Type`/beta versus WebSocket
  beta/session/request ids) remain provider-owned and are derived after the
  shared extension snapshot, matching Pi's `buildBaseCodexHeaders` ownership.
- Delegating adapters such as ds4 do not invoke the helper themselves; only the
  delegated OpenAI Chat Completions adapter fires it, exactly once.

## Tests and objective gate

Focused extension-runtime tests will prove the exact event fields, in-place
add/override/delete behavior, load order, ignored returns, awaitable support,
and exception isolation. Product-path tests will use a capturing HTTP client to
prove the hook sees existing headers, can add/override/delete them, receives the
active session id through `ctx.session_manager`, and leaks nothing to JSON/RPC
stdout or the session archive.

Provider-boundary tests will cover at least:

- a generic JSON provider request, including removal of an existing header;
- Bedrock mutation before signing (the added header is in the signed-header
  set and a removed base header is absent);
- OpenAI Codex retry/fallback reuse, asserting the handler runs once while the
  transformed header reaches every attempt;
- ds4 delegation, asserting one event rather than two;
- an extension-registered provider using the public helper.

Extend the extension conformance gate with a deterministic
`before_provider_headers` row so the shipped parity claim has a durable
objective check.

## Done when

- The pinned event/dispatch contract is public and documented.
- All pipy-owned real HTTP provider families invoke the request-local hook at
  the correct signing/transport seam exactly once per provider request.
- Retries reuse transformed headers without re-dispatch.
- Focused tests and the extension conformance row pass.
- Extension docs, parity plan, gap audit, backlog, user docs where relevant,
  and release notes mark only this slice shipped and retain the named
  follow-ons.
- `just check` and repository hooks pass, and a fresh direct Claude-family
  review returns CLEAN over the exact complete diff committed on `main`.
