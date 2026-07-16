# `before_provider_headers` Implementation Plan

Design: [`../specs/2026-07-16-before-provider-headers-design.md`](../specs/2026-07-16-before-provider-headers-design.md)

1. Add the public event and dispatcher.
   - Define/export `BeforeProviderHeadersEvent` with mutable `headers` and the
     forced `type` literal.
   - Add `dispatch_before_provider_headers_hooks` with serial load-order,
     awaitable, mutation-only, return-ignored, fail-soft semantics and normal
     mode-aware/session-manager context.
   - Acceptance: focused unit tests prove fields, add/override/delete, ordering,
     awaitables, ignored returns, exception isolation, and control-flow
     propagation.

2. Add the in-memory request/header seam.
   - Add an optional request-local callback to `ProviderRequest` plus a shared
     helper that copies assembled headers, invokes the callback once, and drops
     `None` entries before transport.
   - Keep the callback out of every serialization/archive surface and preserve
     exact behavior when it is absent.
   - Acceptance: helper tests prove copy isolation, deletion, and no-callback
     compatibility.

3. Wire activation, reload, and live product dispatch.
   - Collect `before_provider_headers` hooks in `_ExtensionRuntime`, refresh the
     tuple on `/reload`, and attach a request-local dispatcher closure to every
     provider request when hooks exist.
   - Supply the active session tree/context, mode/UI state, flags, trust state,
     and existing stderr-only notification sink.
   - Acceptance: product-path coverage proves active session-manager access,
     reload replacement, no protocol-stdout/session-archive leakage, and no
     behavior when no hook is registered.

4. Wire every real HTTP provider family at its owned seam.
   - Apply the helper in OpenAI Responses, OpenAI Chat Completions (and ds4 by
     delegation), OpenRouter, Anthropic, Google Generative AI, Google Vertex,
     Azure OpenAI, Mistral, Cloudflare Workers AI, Bedrock, and OpenAI Codex.
   - For Bedrock mutate before signing. For Codex mutate one shared snapshot
     before retry/transport loops and reuse it across every attempt/fallback.
   - Export/document the helper for extension-registered provider ports.
   - Acceptance: capturing-client tests prove a generic request mutation,
     Bedrock signed-header coverage, Codex once-only retry/fallback reuse, ds4
     once-only delegation, and an extension-provider opt-in path.

5. Add the objective conformance row and finish documentation.
   - Extend the extension conformance gate for the public/runtime contract.
   - Mark only `before_provider_headers` shipped in extension API docs, parity
     plan, gap audit, backlog, user-facing docs where applicable, and changelog;
     retain `agent_settled`, durable entry rendering, dynamic tool loading, and
     package realignment as separate gaps.
   - Acceptance: focused tests and conformance scripts pass, documentation no
     longer calls this hook missing, and unrelated gaps remain open.

6. Run the commit gate.
   - Run focused tests while iterating, then `just check` and `prek run
     --all-files` when configured.
   - Run a direct fresh-context Claude-family review over the complete code and
     documentation diff. Fix findings and repeat all gates until CLEAN.
   - Acceptance: the exact diff to commit is green and covered by the final
     CLEAN verdict.
