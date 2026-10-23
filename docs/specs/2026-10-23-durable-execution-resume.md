# Durable Execution continuation checkpoint — 2026-10-23

This is an owner-requested WIP preservation checkpoint, **not a delivered slice**.
The owner requested that current work be recorded and committed for later continuation.
The checkpoint branch is `wip/durable-de04-checkpoint-20261023`.
`main` remains at the last delivered commit
`33d39457ba73c583e2590e8d42f4f7e782a15ed6` (default document deltas).
Do not merge the WIP commit into main or count it as reviewed delivery.

## Current work and ownership

The campaign target remains DE00–DE45 and P01–P43. DE00–DE03 are delivered. Independent prerequisites DE06a and DE32a are delivered.
DE04b1b3a/J1 is delivered at the main SHA above. Remaining DE04b1b3b/B implements
checkpoint policies, callback execution authority, exceptional task handoff and
actual document unload. DE04 and its parent rows remain IN_PROGRESS; all 43 P
rows remain OPEN. DE05 waits for complete DE04. No new owner decision is pending.

Codex coordinates and owns shared metadata/manifests. Implementation worker
`/root/j1_astra_max_recovery` used Astra MAX and has stopped for this checkpoint.
Historical implementation/review attribution remains unchanged. Future work uses
one writer per checkout. The direct owner decision “Use the persisted value” is
binding: replayed persisted owned value is authoritative for loaded callback,
cache and publication; creation uses its actual final value; empty loaded ops
retain the committed incarnation/count. Do not reject valid signed-zero cases.
Work-app question 266 was withdrawn because the owner answered it in chat.

## Implementation snapshot

Product changes are confined to `documents.py`, `tx.py`, `session_core.py` and
`session.py` under `src/pipy_harness/native/durable/`.
New policy/authority/unload tests and the actual Pi/native comparison are present.
The new comparison phase is `session-checkpoints-j-v1`; the driver is
`scripts/durable_session_checkpoints_comparison.py`, with CLI
`run --acceptance --sqlite --output <directory>`.
The new topic is [document checkpoints](../durable-session-checkpoints.md).
The current product is frozen; this checkpoint does not assert code-review CLEAN.

Actual worker receipts inspected by the coordinator before checkpoint:

- Focused cumulative suite: **1260 passed, 18 skipped, 2 failed** (exit 1).
  Failures: stale current Session manifest closure and missing
  `session-checkpoints-v1.manifest.json`. These are genuine pending metadata
  gates, not a passing suite. This broad run preceded the final label-only
  comparator/driver/doc change; product and barrier test bytes stayed frozen. Logs: `focused-final/` in the worker archive.
- Full architecture selection: **348 passed**, exit 0.
- Source-aware mypy, Ruff and formatting: exit 0.
- Python 3.11.13: 20 runs, each **58 passed, 3 skipped** (unavailable eager factory).
- Python 3.12.15: 20 runs, each **61 passed**.
  All repetition receipts report reaped leaders and absent process groups.
- Standalone checkpoint comparison tests: **20 passed, 1 failed** (missing new
  manifest), exit 1; see `comparison-freeze/`.

Actual Pi fixture capture (final labels): 156 recipe rows across three backends,
20,738,601 raw bytes, SHA256
`56f77e4d93a581d0c269eb46d0941a8aa108d853eb5fc3eef6740042a24fada9`.
Compressed fixture: 1,057,940 bytes, SHA256
`615415d5ae30a5b52058cf6c4e25989dcb37842b6f3e45eb643c21311bcd327a`.
The coordinator inspected `native-freeze/process.json` (exit 0) and
`pair-freeze/process.json` (exit 0): the latter explicitly compares this final
capture with `native-freeze.json`; both receipts confirm reaping and absent
process groups. Earlier captures and passing intermediate pairs are retained
and must not be mislabeled as this final capture.
Pi working values are derived mirrors; callback/publication/storage observations
and computed replay have distinct provenance. Native backend consistency must
be checked unprojected. Keep historical fixtures byte exact.

## Durable local evidence

Evidence is preserved outside temporary storage at:
`/Users/jochen/.local/state/pipy/durable-checkpoints/de04b1b3b-20261023/`.
Read `ARCHIVE.json`, `worker/WIP-HANDOFF/HANDOFF.md` and `worker/WIP-HANDOFF/FREEZE.json` there first.
The original worker scratch was `/private/tmp/pipy-j2-implementation.29zohlel`.
Logs contain old absolute paths; use ARCHIVE.json to map to preserved copies.
Archived environments/caches are historical; recreate scratch environments if
original interpreter links no longer resolve. Do not install anything to resume.
The archive also preserves coordinator chronology, pre-code metadata admission,
execution brief, observer preparation and DE05 source-only preparation.
The tracked source, tests and expected/input fixtures are in this WIP commit.
Raw process logs stay outside git.

## Exact next actions

1. Inspect git status, this note, the preserved worker HANDOFF/FREEZE and latest
   coordinator log. Verify the checkpoint branch and main SHA before editing.
   To restore the full slice as an uncommitted reviewable delta on main, with a
   clean checkpoint checkout, run `git switch main` followed by
   `git merge --squash wip/durable-de04-checkpoint-20261023`.
   This stages the WIP without creating a delivery commit. The review must see
   the entire delta against delivered main, not just changes after this WIP.
2. Re-read mandatory AGENTS/spec/audit/evidence/backlog/storage/reference docs and
   the five coordination/ledger/review/commit skills. Follow original owner
   instructions; generated handoffs do not supersede them.
3. Create the new checkpoint manifest using archived
   `worker/NEW-MANIFEST-PROPOSAL.json` (79 closure files, 51 pinned sources,
   4 upstream tests; actual loaded source inventory 46). Inspect actual current
   comparator constants/schema. Root owns all manifest JSON. Register the new
   bounded acceptance case and phase, synchronize shared docs/CHANGELOG,
   coverage, plan and evidence. Retain every cumulative DE04 acceptance cell.
4. Cascade all manifest hashes LAST, existing 11 in dependency order then new
   checkpoint manifest; synchronize semantic scope as well as hashes. Verify
   actual validators, local closure and pinned upstream git-show hashes.
   `just check` alone does not catch stale manifest hashes.
5. Run relevant focused/new comparator and closure tests; two actual fresh
   cumulative reference chains with Node 26.12.0/npm 11.20.0, extending the
   delivered chain with the new checkpoint phase. Verify parent bindings,
   source/dependency/artifact hashes, mutants and actual CLI 0/1/2 outcomes.
   Re-check final native Memory/SQLite/reopen strict equality. Do not treat the
   archived single capture as these fresh final chains.
6. Run exact independent review only through installed `claude-review-loop`,
   `--model claude-opus-5-5 --effort high --slice-id DE04b1b3b
   --record-baseline`, with fresh empty run directory. Include
   `--max-file-size 2097152 --max-diff-bytes-per-file 2097152
   --max-bundle-bytes 8388608` so the large actual fixture is not skipped.
   Inspect all exclusions, truncations, redactions and review-copy cleanup.
   First review covers the
   whole delta against delivered main; subsequent repairs use the actual prior
   `--baseline-ref`. Owner permits up to 12 value-driven rounds; never call
   ISSUES/advisory CLEAN. If reviewer unavailable, preserve work and report.
7. Before delivery require local `just check`, `just docs-build`, rendered link
   checks, supported Python checks appropriate to any repairs, and CLEAN on the
   exact committed tree. No full check/docs/final review has passed for B yet.
   GitHub CI is not a gate. No new Linux evidence is claimed.
8. Stage only this slice, commit on main after all gates, run
   `python3 ~/projects/agent-stuff/tools/guard-projects/bin/check-push-remote`,
   normal push and confirm exact main SHA using `git ls-remote`.
   Update work ledger and coordinator chronology. Only then close DE04 parents
   if all original criteria are proven; continue with DE05. Remove the WIP
   branch only once its work is safely delivered.

SSH requires per-command SSH_AUTH_SOCK obtained dynamically from
`launchctl print gui/$(id -u)/com.openssh.ssh-agent`; never print credentials.
Upstream is read only through `git show` at
`2ec55d808d455b1a64a2bf7888198207a096120a` in `/Users/jochen/src/pi-mono`.
The store inode/lock, typed SQLite reads, foreign-object ingress and all existing
contract boundaries remain binding. Preserve global `pi` theme state.
