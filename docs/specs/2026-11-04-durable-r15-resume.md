# Durable R15 resume note (2026-11-04)

This note lives only on branch `wip/durable-r15-3-20261104`. It is not part of
`main`. It records where the pi-durable 1.5 bridge stopped for a machine and
account move and how to continue.

## State

- `main` = `origin/main` = `f267c4cf2a791f72cd77181f9dc344cc371c534a`. The
  following slices are delivered there, each one CLEAN-reviewed by Codex
  gpt-6.1-sol HIGH, pushed and checked with `ls-remote`:
  - R15-0 bridge spec `b9e7d855`
  - R15-1 reference re-pin `63cd965a`, `31e1b48f`
  - R15-2 parking/retry removal `88e3c778`, `b5446172`, `c5ed6cf0`, `f267c4cf`
- Target: literal Pi release v1.5.0, `f9a26365568127461c47a07a37dcb42c140a3ac5`.
  See [the bridge spec](2026-11-03-durable-release-1.5-bridge.md).
- **R15-3 (Storage batch commit) was interrupted mid-implementation.** Its
  work is commit `1821eac4` on this branch: 34 files under `scripts/`, `src/`
  and `tests/`, about +966/−244. The work is unreviewed and no gate has run on
  it. Its hash-pinned manifests are not cascaded, and the last edit was in
  progress. It is not a delivery.
- R15-4 (shape renames) has not started. After R15-4 comes the remainder of
  DE16, which needs a new workflow.

## Prerequisites on the new machine

1. A clone of pipy with `uv sync` (or the project venv) and a working `just check`.
2. A pi-mono checkout that contains commit `f9a26365` (tag `v1.5.0`). The
   reference runner archives the commit itself, so the working tree may be
   anywhere. Its path is passed as `--pi-source` / workflow arg `piMono`.
3. Node **v26.13.0** and npm **11.20.0** on PATH. Other versions stop the
   chain with "Runtime version drift".
4. Codex CLI logged in with access to `gpt-6.1-sol`. The `codex-review-loop`
   and `cross-agent-review-cycle` skills must be installed for the Claude
   account. The review gate fails closed without them.
5. Push access to `origin` over SSH.

Two `just check` failures are known and unrelated to the slices:
`test_native_fuzzy::test_matches_pi_under_node` and
`test_native_system_prompt_pi_pinned::test_fixture_is_current_with_pi`. Both
come from pi-mono checkout drift or missing node_modules. An owner decision on
porting upstream fuzzy change `8c03fef4d` is open.

## How to resume

```sh
git fetch origin
git switch main && git pull --ff-only
git show origin/wip/durable-r15-3-20261104:.claude/workflows/durable-r15-resume.js > /tmp/durable-r15-resume.js
```

Then, in Claude Code, start the workflow with
`Workflow({scriptPath: "/tmp/durable-r15-resume.js", args: {repo: "<pipy path>", piMono: "<pi-mono path>"}})`.
Its R15-3 worker restores the WIP with `git cherry-pick -n 1821eac4`, reviews
and finishes it, and then delivers R15-3 and R15-4 slice by slice.

Delete the branch only after R15-3 has landed on `main`:
`git push origin --delete wip/durable-r15-3-20261104`.

## Open items carried from R15-0..R15-2

- The R15-2d review, its confirming chain (execution `463acc7c`, acceptance
  `e07cbd71…`) and its push are not yet recorded in the evidence ledger. R15-3
  records them.
- Declared divergences with owners: R15-3 U07/U08 and the `commit-exempt`
  guard mutant; R15-4 U34 `storage_failed` and U16 observer order; DE27/DE28/DE45
  U18; DE25 U46; DE17 U04.
- Deferred to their rows: DE17 in-flight request charge (`interrupted`);
  DE19 runFailure for generation restart_limit; DE29 compactionFailure;
  DE39 task_interrupted/task_faulted reports; DE16 restart abandonment.
- Upstream inconsistency: spec §4 says commit observers end `ended` at close,
  but the source says `failed` "session closed". pipy follows the source.
- All 43 P rows remain OPEN.
