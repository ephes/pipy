# Extension `send_message` Delivery Implementation Plan

1. Add focused loop coverage.
   - Acceptance: a command that calls `ctx.send_message(..., {"triggerTurn": True})`
     appends/renders the custom message and produces a provider turn whose prompt
     is the custom message content.
   - Acceptance: a command that calls `ctx.send_message(..., {"deliverAs":
     "nextTurn"})` does not produce an immediate provider turn, but the next user
     prompt's provider request includes the custom message content after the real
     user prompt.

2. Thread in-memory delivery queues through `ToolLoopNativeSession.run`.
   - Acceptance: `extension_send_message` handles `triggerTurn` and `nextTurn`
     after the existing append/render path, with invalid or unsupported options
     treated as no-op delivery.
   - Acceptance: queued next-turn content is injected into the live provider
     message list exactly once for the next accepted provider turn.

3. Extend the extension/package conformance gate and docs.
   - Acceptance: `uv run python scripts/parity_checks/extension_package_conformance.py --json`
     exercises the new delivery semantics.
   - Acceptance: `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, and
     `docs/backlog.md` mark idle `triggerTurn` / `nextTurn` as shipped and keep
     streaming `steer` / `followUp` deferred.

4. Verify and review.
   - Acceptance: `just check` passes, prek is run if configured, and the
     different-family review returns CLEAN on the full code+docs diff before
     committing.
