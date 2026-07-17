# Anthropic Dynamic Tool Loading Implementation Plan

Design: [`../specs/2026-07-17-anthropic-dynamic-tool-loading-design.md`](../specs/2026-07-17-anthropic-dynamic-tool-loading-design.md)

1. Add and persist the provider-agnostic load-point marker.
   - Extend `ToolResultMessage` with ordered `added_tool_names` and validation.
   - Round-trip the optional field through native session JSONL while accepting
     old records that omit it.
   - Acceptance: focused message/session-tree tests prove ordered round-trip and
     backward compatibility.

2. Capture purely additive extension-tool activation.
   - Wire the existing active-tool setter into extension-tool handler contexts.
   - Snapshot active definitions immediately around successful extension-tool
     execution and attach only unique additions when the old set is a subset of
     the new set.
   - Preserve replacement/removal behavior without a marker; keep hook changes
     outside the snapshot.
   - Acceptance: product-loop tests prove additive marking, replacement and
     exception fallback, and complete current tools on the next request.

3. Resolve Anthropic tool-reference compatibility.
   - Add the construction-owned Boolean with explicit compat precedence and
     Pi's exact first-party/model-version default predicate.
   - Pass the resolved value only to the Anthropic Messages adapter.
   - Acceptance: construction tests cover supported 4.5+, Haiku, old/date-like
     versions, non-first-party defaults, and explicit true/false overrides.

4. Build the Anthropic deferred request shape.
   - Split current definitions from message history, retaining prior-used and
     stale-marker tools as immediate and keeping at least one immediate tool.
   - Mark deferred definitions with `defer_loading: true`.
   - Emit deduplicated `tool_reference` blocks at their result marker, group
     consecutive results, and preserve displaced ordinary output as sibling
     text after every result block.
   - Acceptance: focused adapter tests cover the supported request, output
     preservation, prior use, missing/duplicate/all-deferred cases,
     unsupported fallback, and consecutive-result order.

5. Update objective gates and documentation.
   - Extend the extension conformance surface with a deterministic handler-to-
     request dynamic activation proof.
   - Mark only the Anthropic slice shipped in extension/parity/backlog/audit
     docs and changelog; retain OpenAI Responses and Kimi follow-ons.
   - Acceptance: extension conformance and live-session/package gates stay
     green, and no broader provider parity is claimed.

6. Run the commit gate.
   - Run focused tests while iterating, then `just check` (and `prek run
     --all-files` only if configured).
   - Run a direct fresh-context Claude-family review over the complete code and
     documentation diff. Fix findings and repeat every gate until CLEAN.
   - Acceptance: the exact diff to commit is green and covered by the final
     CLEAN verdict.
