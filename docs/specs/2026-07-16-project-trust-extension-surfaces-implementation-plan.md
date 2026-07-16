# Project-trust extension surfaces — implementation plan

Reviewed design:
[`2026-07-16-project-trust-extension-surfaces-plan.md`](2026-07-16-project-trust-extension-surfaces-plan.md)
(direct Claude Opus plan review `CLEAN`, 2026-07-16, round 2).

Status: implemented 2026-07-16.

Implement exactly project-trust slice 3 in this order.

## 1. Add focused failing tests for event and read contracts

- Add immutable event/result/context shapes and tests for serial handler order,
  first yes/no ownership, undecided continuation, awaitables, safe error
  continuation, malformed returns, and exact-`True` remember behavior.
- Cover inert headless select/confirm/input and stderr-only notify.
- Add trust-read assertions for every `_CommandContext`-based dispatcher and
  both zero-argument aliases.

Acceptance: focused tests fail only because the new event dispatcher, resolver
rung, UI context, and read callbacks do not exist yet.

## 2. Implement reusable activation batches

- Add an internal activation batch that retains ordered descriptor identity,
  outcomes, staged activation state, and shared outboxes.
- Support pending pre-trust activation and one final merge by canonical entry
  path, without retrying preload failures or re-running successful activation.
- Revalidate the final ordered reserved/taken sets before committing any staged
  activation messages or contributions.

Acceptance: global/CLI sentinels execute once, project code is absent from the
pending batch, final order/collisions match ordinary one-pass activation, and
failed preloads are not retried.

## 3. Wire the extension decision into trust resolution

- Add the generic extension-decision callback rung after no-protected-input and
  before saved/default/UI resolution.
- Build the pending batch lazily only when that rung is reachable.
- Construct `tui|print|json|rpc` startup context with live startup dialogs only
  for an interactive TTY and safe stderr diagnostics in every mode.
- Persist only exact `remember=True` yes/no decisions at the exact cwd.

Acceptance: override/no-input suppress the event; extension decisions beat
saved/default/UI; handler failures warn and continue; JSON/RPC stdout remains
protocol-only.

## 4. Reuse the finalized batch across catalog and session

- Finalize discovery after trust with resolved settings/package roots.
- Feed one finalized batch to provider-catalog construction and the initial
  `NativeToolReplSession` extension runtime.
- Preserve fresh activation on explicit `/reload` and direct-library fallbacks.

Acceptance: one product startup imports each global/CLI/project extension at
most once, extension providers are available during model selection, and live
commands/hooks use those same instances.

## 5. Thread the read callback through every normal context

- Capture the active run-local `project_trusted` boolean in context factories.
- Expose `is_project_trusted()` and `isProjectTrusted()` with no parameters and
  no storage/resolution side effects.
- Keep reload contexts bound to the active settings manager state.

Acceptance: command, shortcut, lifecycle, input, tool, provider, and
session-gate contexts return identical values in TUI/print/JSON/RPC tests.

## 6. Close docs, objective gate, and review gate

- Extend `project_trust_conformance.py --json` with product extension coverage.
- Update extension/security/parity docs, audit/backlog/roadmap, implementation
  status, and `CHANGELOG.md` to mark the trust track shipped.
- Run focused tests, the objective gate, `just check`, and `prek run --all-files`
  only if configured; then run the direct Claude Opus review loop over the exact
  full diff until `CLEAN`, bounded by the active runner/review-loop outer budget
  and its progress-sensitive stopping conditions.

Acceptance: all gates are green, the final `CLEAN` covers the exact committed
diff, and the single gap is committed on `main` without pushing.
