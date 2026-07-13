# Plan: OpenAI-Codex WebSocket transport slice

Scope: Implement the reviewed Slice 3 only: make OpenAI-Codex `transport: auto|sse|websocket` real with Pi-shaped WS-first selection, pre-event SSE fallback, provider-lifetime auto fallback memory, and documentation/settings updates. Do not implement Pi's cached WebSocket continuation or runner defense-in-depth slice.

Pi/reference constraints pinned from the committed, reviewed design in `docs/superpowers/specs/2026-07-13-openai-codex-transport-reliability-design.md` (which cites the local pi-mono reference used during research): `sse` never constructs WS; `websocket` is a preference and falls back to SSE on recognized pre-event WS transport failures; `auto` starts with WS until a recognized pre-event WS transport failure activates provider-instance SSE fallback memory; protocol/auth/API errors and post-event failures do not fall back. Exact special API code `websocket_connection_limit_reached` gets one fresh WS retry before normal fallback. WebSocket sends a single `response.create` body to the Responses WS endpoint using `OpenAI-Beta: responses_websockets=2026-02-06`, bearer auth, account id, originator/user-agent, and fresh opaque request/session correlation headers; SSE keeps `responses=experimental`.

Implementation tasks/done-when:
1. Add `websockets>=16.0,<17` to runtime dependencies and lock it.
2. Add a narrow synchronous WebSocket client protocol plus a production adapter over `websockets.sync.client.connect` if available, with headers, URL conversion, open/idle timeout handling, send, text JSON receive, close, and cancellation checks. Keep fake-client tests as the primary hermetic coverage.
3. Refactor `OpenAICodexResponsesProvider.complete()` so one retry attempt can acquire either WS or SSE and parse events through the existing transport-neutral parser. Add `websocket_client`, request-id factory, and lock-protected auto fallback state fields.
4. Implement selection/fallback: `sse` SSE only; `websocket` WS first with pre-progress recognized transport fallback to SSE; `auto` same but remembers fallback for later calls; special pre-progress `websocket_connection_limit_reached` retries WS once then falls back. Fallback stays within the same retry attempt; later SSE failures use existing bounded retry policy. Cancellation remains non-retryable.
5. Keep error metadata sanitized and transport-specific. Mark parse progress before events as today so post-event errors never replay.
6. Add focused fake-client tests for mode selection, success, fallback, auto memory, special connection-limit retry, protocol/API non-fallback, post-event non-fallback, timeout forwarding, and cancellation. Update docs/backlog/parity docs to remove the no-op transport claim and mark the slice complete/next work deferred.

Acceptance: focused tests named in backlog pass, `just check` passes, and different-family review is CLEAN over the complete diff.
