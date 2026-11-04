export const meta = {
  name: 'durable-r15-resume',
  description: 'Resume the pi-durable 1.5 bridge: finish R15-3 (Storage batch commit, from the WIP commit) and R15-4 (shape renames), each reviewed CLEAN, committed and pushed',
  whenToUse: 'Continue the pipy durable campaign after the 2026-11-04 machine move. Pass args {repo, piMono}.',
  phases: [
    { title: 'Batch commit', detail: 'R15-3: Storage.commit(batches) on Memory and SQLite, continuing WIP 1821eac4' },
    { title: 'Shape renames', detail: 'R15-4: watch end, report and commit-observer shapes' },
  ],
}

// args: { repo: '/abs/path/to/pipy', piMono: '/abs/path/to/pi-mono' }
const REPO = (args && args.repo) || '/Users/jochen/projects/pipy'
const PI = (args && args.piMono) || '/Users/jochen/src/pi-mono'
const WIP_BRANCH = 'wip/durable-r15-3-20261104'
const WIP_CODE_COMMIT = '1821eac4'

const RESULT = {
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['delivered', 'blocked'] },
    commits: { type: 'array', items: { type: 'string' }, description: 'pushed commit SHAs, oldest first' },
    remote_sha_verified: { type: 'boolean' },
    review_rounds: { type: 'integer' },
    final_review_verdict: { type: 'string' },
    checks: { type: 'string', description: 'just check / docs-build / comparison results, short' },
    summary: { type: 'string', description: 'what changed, max ~200 words' },
    open_items: { type: 'array', items: { type: 'string' } },
    blocker: { type: 'string', description: 'only when blocked: exact cause and preserved state' },
  },
  required: ['status', 'commits', 'remote_sha_verified', 'summary', 'open_items'],
}

const COMMON = `
You are the single main writer for ONE slice of the pipy native durable-execution campaign in ${REPO} (branch main). Work directly on main; no other writer is active.

## Campaign context
- Plan: docs/specs/2026-10-19-durable-execution-plan.md (§4 P01–P43, §5 DE00–DE45, §7 protocol incl. appended owner decisions). Current target and slice list: docs/specs/2026-11-03-durable-release-1.5-bridge.md (READ IT FIRST). Reference guide: docs/durable-reference.md. Evidence ledger: docs/specs/2026-10-19-durable-execution-evidence.md. Backlog: docs/backlog.md. Resume note: docs/specs/2026-11-04-durable-r15-resume.md on branch ${WIP_BRANCH} (read with git show origin/${WIP_BRANCH}:docs/specs/2026-11-04-durable-r15-resume.md).
- Target: literal Pi release v1.5.0, commit f9a26365568127461c47a07a37dcb42c140a3ac5, read from the pi-mono checkout at ${PI} (read-only: never modify it; use git show/diff/archive). Reference runs: \`uv run python scripts/durable_pi_comparison.py reference --pi-source ${PI} ...\` and the phase comparison scripts; Node v26.13.0 + npm 11.20.0 are pinned (RUNTIME in scripts/durable_reference_contract.py).
- Delivered on main: DE00–DE14, DE15a, DE16a/b/c, and bridge slices R15-0 (b9e7d855), R15-1 (63cd965a, 31e1b48f), R15-2 (88e3c778, b5446172, c5ed6cf0, f267c4cf).
- pipy keeps the Session line held through storage (documented deviation from upstream group commit, revisit at DE45).

## Rules (owner decisions, binding)
- Small reviewable diffs; split a large slice into sub-slices, each with its own review and commit. Never drop an acceptance requirement; record splits in bridge spec/backlog.
- Gate per commit: focused tests, \`just check\`, \`just docs-build\` + rendered-link check when docs change, the real Node comparison chain for affected phases. GitHub CI is NOT a gate; never wait on or claim CI.
- Known unrelated just-check failures (do not fix inside a durable slice, just report): tests/test_native_fuzzy.py::test_matches_pi_under_node and tests/test_native_system_prompt_pi_pinned.py::test_fixture_is_current_with_pi (pi-mono checkout drift/missing deps); an MCP HTTP transport test can flake under load.
- Hash-pinned manifest trap: tests/fixtures/durable/*manifest.json pin sha256 of closure files (comparison scripts, .mts drivers, fixtures). Editing ANY closure file, even a docstring, needs the manifest cascade; \`just check\` does not catch a stale manifest. Run the actual comparison chain before review and commit and tell the reviewer which files are hash-pinned.
- Review: \`codex-review-loop\` skill (Codex gpt-6.1-sol, effort HIGH) with \`cross-agent-review-cycle\` value-driven rules; repair → delta re-review with --baseline-ref; up to 12 rounds; only CLEAN permits a commit. Fail closed: reviewer unavailable/reroute/crash/hang (exit 2 → retry once) → no substitute reviewer, no self-review; keep work uncommitted and return "blocked".
- Docs (docs/durable-*.md, CHANGELOG.md, backlog, evidence ledger, bridge spec) match the change in the same commit; record the previous slices' still-unrecorded receipts in the ledger as the ledger asks. All 43 P rows stay OPEN.
- Commit messages: plain imperative subject like existing history; no AI/assistant attribution, no Co-Authored-By. \`git push origin main\`, then verify \`git ls-remote origin main\` equals HEAD. If push fails with publickey on macOS, take SSH_AUTH_SOCK from \`launchctl print gui/$(id -u)\` for that single command.
- No questions mid-task: decide foreseeable cases yourself or return "blocked" with exact cause and preserved state. Owner-level scope decisions become open items.
- Return the structured result; "delivered" only if every commit is CLEAN-reviewed, pushed and ls-remote verified.
`

const SLICES = [
  {
    phase: 'Batch commit',
    id: 'R15-3',
    task: `Finish R15-3 per the bridge spec: adopt the v1.5.0 Storage.commit(batches, receive, context) -> CommitResult {stored, refused?} contract (spec §10; CHANGELOG [1.5.0] first breaking-change block) in src/pipy_harness/native/durable/storage.py, MemoryStorage and SqliteStorage: each batch one atomic commit with its own seq, checked against prior batches; first refused batch (Durable's own checks only) not stored, nor anything after it; driver/I/O/COMMIT failure rejects the whole call storing nothing; receive(index, seq) once per stored batch, in order, synchronously, after durability; every batch has >=1 write; optional publishing hook; narrowed mintId contract. Keep the line-held Session (one batch per call is fine). Provide commit_writes (equivalent of pi-durable/testing commitWrites). Update backend conformance tests and Memory/SQLite comparisons against actual v1.5.0 runs. Remove the R15-3-owned declared divergences (U07/U08: 3 b1b scenarios, 2 b3 pairs) and restore the guard mutant commit-exempt or record its 1.5 replacement. Update docs/session-storage.md and related durable docs.

START by restoring the interrupted work: \`git fetch origin && git switch main && git pull --ff-only && git cherry-pick -n ${WIP_CODE_COMMIT}\` (the WIP code commit on origin/${WIP_BRANCH}; it touches only scripts/, src/ and tests/ — 34 files, about +966/-244). That WIP was written by a previous worker of this slice, is UNREVIEWED, no gate has run on it, its manifests are not cascaded, and it was cut off mid-edit (last action: switching scripts/durable_memory_comparison.py to commit_writes; it had just added 1.5 batch conformance cases to tests/durable/storage_contract_cases.py). Review it critically yourself, keep what is right, fix or drop the rest. Do not commit the resume note or .claude/workflows files from the WIP branch to main. After R15-3 is delivered, leave the WIP branch alone (the owner deletes it).`,
  },
  {
    phase: 'Shape renames',
    id: 'R15-4',
    task: `Implement R15-4 per the bridge spec: align already-delivered pipy surfaces with the v1.5.0 shape renames — watch end reasons (session_closed→ended, cancelled→aborted, tagged WatchEnd), tagged watch calls where pipy has watches, subscribe_commits→observe_commits(observer) with advance/end/detach and end once (follow the SOURCE, which ends commit observers failed "session closed" at close; spec §4 wording differs — bridge U22), onReport(Report) tagged by kind with session_failed→storage_failed and task_outdated reasons task_too_old/migration_failed, tagged SessionEnd and DocumentCommitChange (kind change/retire). Only surfaces pipy already has (e.g. native/durable/completed_projection.py, session_core subscribe_commits, report plumbing, product/headless consumers); record the rest against their DE rows. Remove the R15-4-owned declared divergences (U34 storage_failed in 14 b1b scenarios and 12+2 b3 pairs; U16 observer order in 2 b3 pairs). Pythonic naming per pipy conventions. Update comparisons/manifests with actual v1.5.0 runs, docs and CHANGELOG.`,
  },
]

const results = []
for (const s of SLICES) {
  phase(s.phase)
  const r = await agent(
    `${COMMON}\n\n## Your slice: ${s.id}\n${s.task}\n\nPrevious slices in this run:\n${JSON.stringify(results.map(x => ({ id: x.id, status: x.status, commits: x.commits, open_items: x.open_items })), null, 1)}`,
    { label: s.id, phase: s.phase, schema: RESULT, effort: 'max' },
  )
  if (!r) {
    log(`${s.id}: agent returned nothing; stopping`)
    results.push({ id: s.id, status: 'blocked', commits: [], open_items: [], summary: 'agent died or skipped', remote_sha_verified: false })
    break
  }
  results.push({ id: s.id, ...r })
  log(`${s.id}: ${r.status}, commits ${r.commits.join(', ') || 'none'}, review rounds ${r.review_rounds ?? '?'}`)
  if (r.status !== 'delivered' || !r.remote_sha_verified) {
    log(`${s.id} not delivered; stopping before dependent slices`)
    break
  }
}
return results
