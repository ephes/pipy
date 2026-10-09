# DF1-F5b — oversized auxiliary-summary recovery

Prepared on main at `ca608ce045406f3784d913f7b501d4b141297471`.
Scope: compaction summary preflight overflow after CM1, with one implementation
writer (Codex), without a branch or product commit during implementation/review.
Independent review closed as advisory
with no outstanding Critical or Warning findings; this is not a CLEAN verdict.

## Reproduction

The existing oversized-paste test described the wedge. Changed its expected
later fitting submission to succeed and ran it against unchanged production:
`uv run pytest -q tests/test_native_compaction_oversized_turn.py::test_oversized_user_message_is_still_refused_like_pi`
exited 1 (one failed), because the later prompt returned `request_budget`.

The new product-session regression has three independent cases: a multibyte huge
paste, a large direct `write` argument and eighty complete tool cycles whose
results are individually below the existing 2000-character truncation threshold.
With only baseline `provider_selection.py` restored temporarily, all three failed
at continuation with `request_budget`; the baseline summary preflight admits no
summary provider call. The implementation file was restored in a `finally`
block before any further edits or checks. No branches or worktrees were created.

## Contract and design

See [the implemented recovery contract](../compaction.md#oversized-summary-recovery-df1-f5b)
for exact bounds and information handling. Keep the existing safe cut, immutable
work snapshot, private executor, retry policy, state acceptance and durable writer.
After normal preflight fails, deterministically reduce private textual evidence,
with a finite ten-candidate allowance schedule and full ordinary budget accounting.
Calls and their consecutive results stay in one unit or are omitted together.
Requests have no tools, attachments or request-only overlays. Earlier details and
thinking may be absent; task orientation and file metadata are also bounded.

One successful result replaces continuity, with bounded accepted output and a
fixed warning in live and durable summary. The output bound matters even with a
provider that echoes its input: otherwise recovery could simply replace one
oversized context with another. Both provider-input and accepted-output reductions
are disclosed. A warning from previous recovery survives later compaction. This
is an intentional difference from Pi, not token-based retention parity.

The original full-work witness is checked after detached excerpt work and at all
existing generation/reissue/acceptance boundaries. Cancellation, generation failure
and stale work publish nothing. Live acceptance still precedes persistence;
accepted persistence failure escapes without rollback or retry. Reopen equivalence
is required after successful persistence only. No transcript entries are rewritten
and no session format or public compaction command changes.

## Evidence

- New and existing focused contracts, including TUI notice projection: 312 passed.
- Initial Ruff/static failures were corrected: Python 3.11-compatible type-variable
  syntax, sorted imports and explicit fixture annotations. Existing summary-refusal
  tests now use allowances below recovery framing, keeping their hook/cancellation
  assertions meaningful.
- Initial full `just check`: 7,935 passed, 2 expected skips; process exited 0.
- Runtime-required CM1 composite/delivery: 48 passed. Docs build and all eight PTY
  smoke tests passed; no pre-commit configuration is present.
- First independent review: Claude Code `claude-opus-5-5`, high effort, ISSUES;
  two Warnings and two Suggestions accepted. No skipped files, truncations or
  redactions; the private review copy was removed, with no forbidden tool use.
  Reviewed snapshot: `fc535654e16a3d82994ebecf36bf0689946691f4`.
- Repair scope: explicit current-recovery outcome flag for truthful TUI/plain
  notices; actual-field excerpt allocation and prior-warning removal to preserve
  more continuity; leading warning deduplication; corrected verification status.
  Repaired focused group: 317 passed; Ruff, format and mypy passed.
  Repaired full `just check`: 7,940 passed, 2 expected skips; process exited 0.
  Runtime-required CM1 (48 passed), docs build and PTY (8 passed) passed again.
  Fresh scoped independent review completed: `claude-opus-5-5`, high effort,
  ISSUES with one low-impact Suggestion and no Critical/Warning findings.
  Repair snapshot: `98c4db4f7daa725945c2ecd349a1750294311185`.
  No skipped files, truncations, redactions, denied or forbidden tool use;
  the private copy was removed. Both harness processes completed validly.
- Advisory disposition: defer redistribution of unused short-field allowance to
  longer sibling fields. Equal sharing deliberately keeps this fallback simple;
  the single-field prior-summary repair is unaffected, estimates stay conservative
  and all information reduction is disclosed. This is a continuity-quality
  improvement, not a correctness defect. Another repair/review round would add
  an allocation heuristic without addressing a demonstrated blocking risk.
  The harness ledger reports convergence. No earlier accepted finding remains
  unresolved: both Warnings and both first-round Suggestions were repaired.
- Closure is **advisory, not CLEAN**. No owner acceptance or override is needed
  for the deferred Suggestion; there are no unaccepted blocking findings.
  Post-review edits only finalize evidence and backlog status; source/tests remain
  the reviewed snapshot. Documentation build and whitespace validation are rerun
  for that status-only delta.

Tests establish deterministic recovery, honest oversized-input refusal, one
summary operation per iteration, repeated-pressure termination, preserved original
JSONL bytes, strict reopen, resumed continuation, old-branch navigation and fork,
complete textual pairing, private no-tool payloads, attempted nested-file evidence,
failure/cancellation/stale refusal, post-preparation revalidation, state-first
persistence failure and truthful live terminal warning. Live provider summary
quality and below-estimate provider rejection remain unverified.

## Verification commands

Run from `/Users/jochen/projects/pipy`; every result is recorded only after its
process exits. All test/manual configuration uses isolated fixtures. Theme
settings in both local stores remained `pi`.

```sh
uv run pytest -q \
  tests/test_native_compaction_summary_recovery.py \
  tests/test_native_compaction_oversized_turn.py \
  tests/test_native_semantic_compaction.py \
  tests/test_native_semantic_compaction_retry.py \
  tests/test_native_request_budget.py \
  tests/test_native_request_budget_admission.py \
  tests/test_native_session_tree_anchored_compaction.py \
  tests/test_native_session_tree_self_compaction.py \
  tests/test_native_branch_summary_retry.py \
  tests/test_native_nested_record.py \
  tests/test_native_tui_polish.py
PIPY_CODEMODE_REQUIRE_RUNTIME=1 uv run --extra codemode pytest -q \
  tests/test_native_codemode_composite.py tests/test_native_codemode_delivery.py
uv run ruff check .
uv run ruff format --check .
just typecheck
just check
just docs-build
just test-pty-smoke
git diff --check
```

## Changed files and ownership

- `native/coding/summary_recovery.py`: pure bounded excerpt preparation and
  accepted-output reduction, warning preservation and deduplication.
- `native/coding/compaction.py`: explicit current-recovery flag on the result.
- `native/repl/provider_selection.py`: preflight fallback, full-work revalidation,
  bounded acceptance and truthful projection through existing persistence.
- `native/repl/wiring.py`: fixed terminal notice after a new recovery redraw.
- Four test modules: new recovery regressions, the repaired oversized-paste
  expectation, still-unfittable auxiliary/hook contracts and TUI notice accuracy.
- `docs/compaction.md`, `docs/harness-spec.md`, `docs/backlog.md`, this note and
  Unreleased `CHANGELOG.md`: implemented behavior, limits and evidence.
  No usage/settings surface changed.

## Workflow capture

Summary-safe archive searches and prior Markdown summaries were inspected for
compaction/currentness and state-first persistence lessons. No active DF1-F5b
record was found, so this note supplies reproduction, implementer (Codex session;
exact model identifier is not exposed), verification and review facts for the
driver. Harness ledger entries record the actual Claude model, effort, snapshot
and severity counts without raw content. No unrelated repository or external
work app was written. Implementation/review created no product commit, push,
branch, PR or publication. The owner subsequently authorized commit and push.

## Deferred boundaries

No general `keepRecentTokens` policy, new provider transport/output cap, branch
summary contract expansion, codemode expansion, schema migration or durability
transaction. Tiny allowances and oversized protected new content can still refuse.
The bounded warning identifies incomplete context; users may need to restate facts
from the preserved transcript when an omitted detail matters.

## Acceptance and remaining limits

All ten requested acceptance criteria are satisfied for this prepared diff:
production-path baseline failure, later fitting continuation, bounded admission
and termination, honest oversized-new-input refusal, preserved durable transcript
and reconstructed context, pairing/anchor/CM1 preservation, nonpublication on
cancel/failure/staleness, truthful reduction notice, complete checks/docs, and a
valid independent advisory closure with no unresolved blocking findings.

No live-provider summary-quality or exact-tokenizer guarantee is claimed. The
one deferred review Suggestion concerns better use of spare excerpt capacity.
The normal final admission can still refuse when protected input, system/tools,
attachments or reserve leave insufficient room. After advisory review closure,
the owner authorized committing and pushing this slice on main. The harness's
unreferenced local review snapshots are evidence objects,
not product commits or branch changes.
