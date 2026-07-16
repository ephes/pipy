# Extension `agent_settled` Implementation Plan

Design: [`../specs/2026-07-16-extension-agent-settled-design.md`](../specs/2026-07-16-extension-agent-settled-design.md)

1. Extend the lifecycle contract and focused tests.
   - Add `agent_settled` to the extension runtime lifecycle vocabulary.
   - Update lifecycle unit/conformance fixtures to require the payload-free
     `LifecycleEvent(name="agent_settled", reason=None)` callback.
   - Acceptance: registration, async/serial fail-soft dispatch, and the
     ordinary sequence `agent_end -> agent_settled -> session_shutdown` are
     covered by focused tests.
2. Wire the true-idle product boundary.
   - Mark settlement pending immediately before an accepted run dispatches
     `agent_start`.
   - After normal queue resolution, fire only when no local command or queued
     provider prompt was selected; re-drain any prompt a settled handler
     schedules before reading input.
   - In the session `finally`, fire any still-pending event before
     `session_shutdown`, covering fatal returns and unexpected mid-run errors.
   - Keep hook dispatch extension-only; do not emit through
     `AutomationEmitter`.
   - Acceptance: one callback after all extension-enqueued continuations, one
     callback on a mid-run exception, none for an EOF-only session, and no
     duplicate JSON/RPC protocol event.
3. Refresh the extension conformance surface.
   - Add the hook to the golden conformance extension and lifecycle parity
     gate, preserving metadata-only proof and archive/protocol privacy.
   - Acceptance: focused extension gates and existing automation tests pass.
4. Close the parity documentation.
   - Mark only extension-surface `agent_settled` shipped in the extension spec,
     backlog, parity plan/status, gap audit, and changelog.
   - Keep durable entry renderers, dynamic tool loading, package realignment,
     and RPC extension UI explicitly deferred.
   - Acceptance: no current planning document still names this hook as an open
     gap.
5. Run the commit gate.
   - Run focused tests/gates, `just check`, and `prek run --all-files` when the
     repository has a pre-commit configuration.
   - Run a fresh direct Opus review over the complete code/docs diff; fix and
     repeat all checks/review until CLEAN.
   - Acceptance: the exact committed diff is green and CLEAN, then commit on
     `main` without pushing.
