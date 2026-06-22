# Pipy Parity Improve — Workflow

Canonical body of the `parity-improve` skill. Each agent's wrapper points here;
do not duplicate this content. This skill **consumes** the lessons captured by
the parity loop (its Phase 9) and materializes them into real, gated changes, so
the workflow improves over time. Work on trunk (`main`); never self-grade.

## When to run

- When `python3 scripts/parity_lessons.py list --status open` shows ≥ the
  threshold (default 5) open lessons — the parity loop's Phase 0 forces this
  before a new gap.
- At a run's end, to drain all remaining open lessons (the run-end backstop).
- On demand.

## Steps

1. **Read open lessons.** `python3 scripts/parity_lessons.py list --status open
   --json`. Group them by `target_area` (`skill-body`, `wrapper`, `docs`,
   `tests`, `harness`).
2. **Propose concrete edits.** For each group, make the smallest real change that
   materializes the lesson — most often editing `docs/parity-loop/skill-body.md`
   or `docs/parity-loop/improve-body.md`, but also wrappers, docs, tests, or
   harness/helper code under `src/` or `scripts/`.
3. **Gate the edits (different model family).** Run `just check` (and `prek run
   --all-files` only if a `.pre-commit-config.yaml` is present — `just check` is
   pipy's real gate; `pre-commit` is not installed); only when green, run the
   **different-family** review over the edit
   diff (`pi-review-loop` if you are Opus, `opus-review-loop` if you are GPT)
   until the verdict is CLEAN. On ISSUES, fix and re-run the gates.
4. **Sign-off gate.** Any edit to `skill-body.md`, `improve-body.md`, or a
   wrapper (the workflow's own instructions) additionally requires human sign-off
   OR a judge-agent majority vote before commit. **Rejection is also gated:**
   deciding a lesson is not worth applying requires the same sign-off — draining
   via rejection is never free.
5. **Commit the edits, naming the lesson ids.** Commit with a message that names
   the lesson id(s) it materializes (e.g. `Closes lessons: 2026-06-22-a3f9c1`),
   then capture the full commit SHA with `git rev-parse HEAD`.
6. **Record resolutions.** For each consumed lesson:
   `python3 scripts/parity_lessons.py mark <id> applied --sha <SHA> --repo .`
   (add `--signed-off-by <human|judge>` for instruction-area lessons —
   `target_area: skill-body` or `wrapper`; note that edits to BOTH instruction
   bodies, `skill-body.md` AND `improve-body.md`, are recorded under
   `target_area: skill-body`, so they require sign-off); for duds
   `python3 scripts/parity_lessons.py mark <id> rejected --reason "<why>"
   --signed-off-by <human|judge>`. The helper refuses to mark `applied` unless the
   SHA is a HEAD-ancestor commit that names the lesson and touches the right
   non-ledger artifact, so a lesson cannot be closed without real materialization.
7. **Commit the ledger update** as a separate `chore(lessons): mark <ids>
   applied/rejected` commit. `python3 scripts/parity_lessons.py validate` (wired
   into `just check`) gates it, so a malformed or unmaterialized ledger fails the
   gate.

## Hard rules

- **Never self-grade** — the review is always a fresh, different-family context.
- **Never mark `applied` without a real materializing commit** — the helper
  enforces this; do not fabricate a SHA.
- Capture (the loop's Phase 9) is ungated; **application here is gated**. A bad
  candidate lesson is harmless because applying *or* rejecting it both cost the
  sign-off gate.
