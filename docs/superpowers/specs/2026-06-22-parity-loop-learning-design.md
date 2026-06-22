# Parity Loop Learning — Design

Status: draft for review, 2026-06-22.

## Problem

The parity-loop skill (shipped Phase 1) drives one pi-mono gap end to end, but it
does not get better over time. We want each iteration to **learn**: capture
reusable lessons from the session, and — crucially — feed those lessons back so
the workflow actually improves.

pipy already tried this once and it failed instructively. The 2026-05-26 audit
(commit `77766a8`) **deleted** `pipy-session reflect`,
`reflect_on_finalized_sessions()`, the `lesson.learned` event type, the
`ReflectionRepository`, and the `workflow.role` / `subagent.used` /
`review.outcome` / `workflow.evaluation` events — all as dead code: **"emit-only,
never consumed."** Lessons were written and never read back, so they earned their
removal.

The success criterion for this design is therefore singular: **close the loop.**
Whatever we capture must be consumed by future iterations, and there must be an
**automatic improvement process that actually changes the skill/docs** — not just
a store of advice nobody reads.

## Goals

- **Serialize lessons** durably in a git-tracked, machine-readable ledger.
- **Capture automatically** at the end of every parity-loop iteration (cheap,
  ungated).
- **Apply automatically but gated**: an improvement process consumes open lessons
  and edits the skill body / wrappers / docs (or harness), passing the existing
  different-family Pi review loop, with human/judge sign-off before any edit to
  the skill's own instructions.
- **Guarantee consumption by materialization**: a lesson is "consumed" only when
  it has been turned into a permanent, gated change to its target artifact — not
  when it is filed as advice. For instruction-area lessons that change is the
  skill body/wrappers every iteration reloads; for docs/tests/harness lessons it
  is a permanent repo change that embodies the fix (a regression test that now
  guards it, a harness bug that no longer exists, a corrected doc). Either way the
  lesson has been *acted on*, which is what stops the emit-only rot — there is no
  unread-advice store doing the work.
- **Reuse existing transcripts** (Claude/Pi/Codex/pipy-native already store full
  transcripts on disk) rather than building new capture storage.

## Non-goals (v1)

- No blind self-modifying harness: the *apply* step is always gated (Pi CLEAN +
  sign-off for instruction edits). Capture is automatic; change is deliberate.
- No new full-transcript archive. v1 reads the agent's own transcript in place and
  stores only a *pointer* plus the distilled lesson. (Optional snapshot-into-
  `runs/` is a later refinement.)
- No change to pipy's metadata-first *session archive* policy. This system is a
  separate, parity-loop-scoped ledger, not a revival of the audited
  `pipy-session reflect` surface.
- v1 targets the **pipy-parity-loop skill in this repo only**. The ledger schema
  carries a `skill` field so it can generalize, but cross-skill/cross-repo wiring
  is out of scope.

## Background (verified)

- **Transcripts already on disk, full content, per agent:**
  - Claude Code: `~/.claude/projects/-Users-jochen-projects-pipy/*.jsonl`
    (`user`/`assistant`/tool events, full text).
  - Pi: `~/.pi/agent/sessions/--Users-jochen-projects-pipy--/*.jsonl`
    (`message` events, full).
  - Codex: `~/.codex/history.jsonl` (full history, untyped).
  - pipy native: `~/.local/state/pipy/native-sessions/--<cwd>--/*.jsonl` (full).
- The pipy **metadata archive** (`~/.local/state/pipy/sessions/…`) is redacted by
  design and is NOT the learning substrate here.
- AGENTS.md already has a *manual* "Workflow Learning Capture" convention
  (`pipy-session append …`) and "Session Learning Checks" (inspect via
  `pipy-session search` before planning). Both are manual and unconsumed; this
  design supersedes them for parity-loop work with an automatic, consumed path.

## Architecture

Three pieces: a ledger, a reflect step (per iteration), and an improve sub-skill
(the engine that changes things). A small tested helper (`scripts/parity_lessons.py`)
owns the deterministic ledger operations; all judgment (what is a lesson, what
edit to make) stays agent-driven.

```
docs/parity-loop/lessons/lessons.jsonl   # serialized JSONL, one record per lesson (updated in place), git-tracked
scripts/parity_lessons.py                # tested CLI/lib: validate, list, append, mark
docs/parity-loop/skill-body.md           # gains Phase 0 (load) + Phase 9 (reflect)
docs/parity-loop/improve-body.md          # canonical body of the "improve" sub-skill
.claude/skills/parity-improve/SKILL.md    # improve wrappers (one per agent), each a
.pipy/skills/parity-improve.md            #   thin pointer to improve-body.md, mirroring
.pi/skills/parity-improve.md              #   the parity-loop skill's per-agent pattern
AGENTS.md                                # "## Parity improve" section = Codex's surface
```

### Lesson record schema (one JSON object per line in `lessons.jsonl`)

```json
{
  "id": "2026-06-22-a3f9c1",
  "date": "2026-06-22",
  "skill": "pipy-parity-loop",
  "gap": "short gap name / slice id",
  "agent": "claude|codex|pi|pipy",
  "trigger": "recurring-review-finding|gate-failure|wrong-turn|better-approach",
  "lesson": "Distilled, summary-safe, actionable statement (no secrets, no raw transcript).",
  "target_area": "skill-body|wrapper|docs|harness|tests",
  "status": "open|applied|rejected",
  "resolution": {"sha": "<commit, applied>", "reason": "<why, rejected>", "signed_off_by": "<who/what; required for rejected + instruction-area applied>"},
  "transcript_ref": {"agent": "claude", "session_id": "…", "path": "~/.claude/…/<id>.jsonl"}
}
```

- `id` is `YYYY-MM-DD-<6 hex>` (date + 6 random hex chars from `os.urandom`),
  assigned by the helper. The random suffix makes ids **collision-resistant by
  construction** across concurrent runs, worktrees, and branches that each append
  before the other's ledger change lands — no shared counter to coordinate.
  (`parity_lessons.py` is ordinary Python, not a Workflow script, so real
  randomness/time is available here.) `validate`'s unique-`id` check remains as a
  cheap backstop. Two branches appending distinct-id lines can still produce a
  git merge on `lessons.jsonl`, but with distinct ids it resolves by keeping both
  lines.
- `lesson` is **summary-safe**: distilled advice, never raw transcript content,
  never secrets/credentials/tokens. `transcript_ref.path` points at the local
  (un-committed) transcript for human follow-up.
- `resolution` is populated only on `applied` (with `sha`) or `rejected` (with
  `reason` + `signed_off_by`). `rejected` rows stay in the ledger so the same dud
  isn't re-proposed.

### `scripts/parity_lessons.py` (deterministic, tested)

A small CLI + importable library.

The ledger is **not append-only**: there is exactly one line per lesson `id`.
`append` adds a new line; `mark` rewrites that lesson's line in place (changing
`status`/`resolution`). This is consistent with unique-`id` validation. Commands:

- `validate` — every line parses, required fields present, `status`/`trigger`/
  `target_area` are in their enums, `id`s unique. **Plus materialization checks
  that give the ledger teeth:** every `applied` record's `resolution.sha` must
  resolve **and be an ancestor of `HEAD`** (`git merge-base --is-ancestor <sha>
  HEAD`), so it is a permanent change on this branch — not an orphan, stash, or
  other-branch/local-only commit — **and that commit must modify at least one file
  matching the lesson's `target_area`** (map:
  `skill-body`→`docs/parity-loop/{skill,improve}-body.md`; `wrapper`→the
  `.claude`/`.pipy`/`.pi` skill files or `AGENTS.md`;
  `docs`→`docs/**` **excluding the instruction bodies and the learning machinery's
  own files** (`docs/parity-loop/skill-body.md`, `docs/parity-loop/improve-body.md`,
  `docs/parity-loop/lessons/**`, `docs/parity-loop/runs/**`) so `docs` means actual
  documentation, disjoint from `skill-body`; `tests`→`tests/**`;
  `harness`→`src/**` or `scripts/**` (the helper itself lives at
  `scripts/parity_lessons.py`)). **Crucially, changes to the ledger
  itself (`docs/parity-loop/lessons/**`) never count as materialization for any
  `target_area`** — the matched file must be ≥1 file other than the ledger/scratch.
  This stops the circular loophole where the step-7 bookkeeping commit (which edits
  `lessons.jsonl`, under `docs/`) could "materialize" a `docs` lesson by itself;
  the qualifying SHA must be the step-5 *edit* commit, not the bookkeeping commit.
  **The applied SHA must be tied to *this* lesson, not just to the right area:**
  the edit commit's message must reference the lesson `id`(s) it materializes
  (step 5 requires this), and `validate` checks `git log -1 --format=%B <sha>`
  contains the lesson's `id`. This stops citing one unrelated same-area commit to
  drain several lessons. **Instruction-area applied records** (`target_area` ∈
  {`skill-body`,`wrapper`}) must additionally carry `resolution.signed_off_by`, and
  `validate` requires its presence — so the sign-off that process demands leaves a
  durable, checkable trace. **Every `rejected` record must carry a non-empty
  `resolution.reason` AND a `resolution.signed_off_by`** (rejection gate below) — a
  rejection is a gated decision, not a free drain. Exit non-zero on any violation.
  This makes "marked `applied`" mechanically imply "a real, HEAD-ancestor edit
  commit that names this lesson and changed the right non-ledger artifact (with a
  recorded sign-off for instruction edits)," and "marked `rejected`" imply "a
  recorded, signed-off decision."
- `append --json '<record-without-id-or-status>'` — assigns `id`, sets
  `status:open`, appends; refuses near-duplicates (same `skill`+`target_area`+
  normalized `lesson`).
- `list [--status open] [--json]` — read/filter.
- `mark <id> applied --sha <sha> [--signed-off-by <who>]` (sign-off **required**
  when the lesson's `target_area` ∈ {`skill-body`,`wrapper`}) / `mark <id> rejected
  --reason <text> --signed-off-by <who>` — transition status, write `resolution`.
  `validate` (above) enforces that `applied` carries a HEAD-ancestor lesson-id-
  naming materializing `sha` (plus `signed_off_by` for instruction areas) and that
  `rejected` carries both `reason` and `signed_off_by`.

This is genuinely *consumed* (the improve sub-skill calls `list --status open` and
`mark`), so it is not the audited emit-only dead code — consumption is enforced by
the workflow and pinned by tests.

### Reflect — new Phase 9 of the parity-loop body (every iteration, ungated)

After the gap is committed (end of the existing workflow), the agent:

1. Locates the just-finished session transcript using the **per-agent locator**
   below (the sources differ in shape, so one "newest jsonl" rule does not fit
   all).
2. Reads transcript + the gap's diff + the review verdicts + the number of review
   rounds it took.
3. Distills 0–N candidate lessons (skip if nothing reusable) and appends each via
   `parity_lessons.py append`, first checking `list` to avoid duplicating an
   existing open/applied lesson.
4. Commits the ledger change as a small `chore(lessons): …` commit (summary-safe).

Capture is cheap and ungated; a bad candidate lesson is harmless because *apply*
is gated.

#### Per-agent transcript locator

The four agents store transcripts in different shapes, so Phase 9 step 1 uses an
agent-specific rule (the agent is known — it is the host running the loop):

- **Claude**: newest `*.jsonl` (by mtime) in
  `~/.claude/projects/-Users-jochen-projects-pipy/`. Per-file = per-session.
- **Pi**: newest `*.jsonl` in
  `~/.pi/agent/sessions/--Users-jochen-projects-pipy--/`. Per-file = per-session.
- **pipy native**: newest `*.jsonl` in
  `~/.local/state/pipy/native-sessions/--<cwd-encoded>--/`. Per-file = per-session.
- **Codex**: there is **no per-session file** — `~/.codex/history.jsonl` is a
  single global, untyped, append-only log whose records carry `session_id` and
  `ts`. The locator must therefore *extract*, not pick a file: read the tail,
  take the `session_id` of the last record, and select the contiguous run of
  records sharing that `session_id` as the current session. This is coarser than
  the file-per-session agents and is the documented v1 limitation (see open
  question 3); a future refinement is a session-id handshake at loop start.

Per-file agents still face the concurrent-session ambiguity noted in open
question 3; the Codex extraction additionally assumes the loop's session is the
most recent activity in the global log.

### Improve — the `parity-improve` sub-skill (threshold or on-demand, gated)

Canonical body `docs/parity-loop/improve-body.md`, thin per-agent wrappers like the
parity-loop skill.

**What invokes it (the trigger is enforced, not aspirational).** Two enforced
checkpoints guarantee every captured lesson is eventually consumed:

- **Mid-run batch trigger (efficiency).** The open-lesson count is checked at
  **Phase 0 of every parity-loop iteration** and, in Phase 2's unattended harness,
  between gaps. When the count is ≥ the threshold (default 5), running
  `parity-improve` is a **hard precondition** for starting a new gap. The threshold
  exists only to *batch* improvement so we don't re-edit the skill after every
  single lesson — it is not a leak.
- **Run-end backstop (the consumption guarantee).** A parity-loop run/session may
  **not conclude with any `open` lessons**, regardless of count. The wrap-up step
  (Phase 2 harness end, or the attended operator's stop / finishing-a-branch) runs
  `parity-improve` until `list --status open` is empty (each lesson ends `applied`
  or `rejected`). So "below threshold" is only ever a *within-run transient*; no
  lesson survives a run unconsumed.

`parity-improve` may also be run on demand at any time. Together these put
application *on the critical path* — the mechanism that prevents the emit-only
failure mode rather than leaving it to a cron nobody runs.

Steps:

1. `parity_lessons.py list --status open` → group by `target_area`.
2. For each group, propose **concrete edits** to the target file(s) — most often
   `skill-body.md`, but also wrappers, docs, tests, or harness code.
3. Run `just check` + pre-commit, then the **different-family Pi review loop until
   CLEAN** over the edit diff (the exact gate the parity-loop already uses).
4. **Sign-off gate for instruction edits:** any edit to `skill-body.md` /
   `improve-body.md` / wrappers (the workflow's own instructions) additionally
   requires human sign-off OR a judge-agent majority vote before commit. Edits to
   docs/tests/harness need only the Pi-CLEAN gate.
   **Rejection gate:** marking a lesson `rejected` instead of applying it is *also*
   a gated decision — it requires the same human sign-off OR judge-agent majority
   vote, recorded as `resolution.signed_off_by` with `resolution.reason`. This
   closes the escape hatch where a run could satisfy the run-end backstop by
   mass-rejecting every open lesson for free; draining via rejection costs the same
   gate as applying.
5. **Commit the edits** (the skill/doc/harness changes), and **name the lesson
   `id`(s) the commit materializes in its commit message** (e.g. `Closes lessons:
   2026-06-22-a3f9c1`) → capture the resulting commit SHA. `validate` later checks
   this message references the lesson, tying the SHA to *this* lesson.
6. **Record resolutions in the ledger:** for each consumed lesson
   `parity_lessons.py mark <id> applied --sha <SHA-from-step-5>` — adding
   `--signed-off-by <human|judge>` when the lesson is instruction-area
   (`skill-body`/`wrapper`); for duds `mark <id> rejected --reason …
   --signed-off-by <human|judge>` (a rejection is gated per step 4). This rewrites
   `lessons.jsonl`.
7. **Commit the ledger update** as a separate `chore(lessons): mark <ids>
   applied/rejected` commit. This bookkeeping commit changes only `lessons.jsonl`
   (status + resolution) and contains no instruction change. It skips a *fresh* Pi
   review round because (a) the edit it references was already gated at apply-time
   by the step-3 Pi-CLEAN review and the step-4 sign-off — that is where edit
   *quality/correctness* is judged — and (b) `parity_lessons.py validate`
   (pre-commit/`just check`) mechanically confirms *materialization*: the `applied`
   SHA exists and that commit touched a file matching the lesson's `target_area`.
   So a lesson can reach `applied` only when a real, gated, materializing edit
   exists; the bookkeeping commit merely records it.

This deliberate two-commit ordering resolves the chicken-and-egg: the `applied`
SHA can only be known *after* the edit commit, and the ledger transition is itself
committed (step 7) so consumed lessons never remain `open` for a future run to
re-process.

### Consumption guarantee — new Phase 0 of the parity-loop body

At the *start* of every iteration the body instructs:

1. Read this skill body (which now contains applied improvements).
2. Run `parity_lessons.py list --status open`. **If the open count ≥ threshold
   (default 5), you MUST run `parity-improve` to drain the backlog before
   selecting a new gap** (the enforced trigger above). Below threshold, surface
   the open lessons as a heads-up and proceed.

The durable consumption path is **materialization** (see Goals): instruction-area
lessons are folded into the body/wrappers that every iteration reloads, while
docs/tests/harness lessons become permanent repo changes that embody the fix.
Phase 0's open-lesson check is what *forces* this materialization to happen on the
critical path rather than being deferred indefinitely; it does not assume every
applied lesson is re-loaded into context.

## Data flow

```
iteration ──> Phase 0: load body; list --status open
                 │
   open ≥5 ?     ├── yes ──> parity-improve (REQUIRED before new gap) ─┐
                 │                                                      │
                 └── no ──> Phases 1-8: drive gap, gated, commit       │
                       ──> Phase 9 (reflect): transcript+diff          │
                            -> append open lessons -> commit           │
                                                                       │
parity-improve ──> read open lessons -> edit skill/docs/harness <──────┘
              ──> just check + Pi review loop until CLEAN
              ──> (instruction edits) human/judge sign-off
              ──> commit edits (SHA) -> mark applied/rejected -> commit ledger
                 │
                 ▼
 lesson materialized: improved body (reloaded) OR permanent repo fix ── loop closed
```
(`parity-improve` also runnable on demand, independent of the Phase 0 gate.)

## Privacy

The committed artifacts (`lessons.jsonl`, skill/doc edits) are **summary-safe** by
construction: distilled lessons only, no raw transcript, no secrets. Raw
transcripts stay where each agent already keeps them (un-committed, local);
`transcript_ref` is a pointer for human follow-up. This keeps the git-tracked
surface metadata-clean while still enabling learning, consistent with the
parity-plan's "match Pi, don't diverge for privacy" stance applied to the *local*
transcripts and a clean *committed* ledger.

## Testing strategy

- **Ledger helper unit tests** (`tests/test_parity_lessons.py`): `validate`
  catches malformed lines / bad enums / duplicate ids; `append` assigns a unique
  date+random `id`, sets `status:open`, and rejects near-duplicates; `mark applied/rejected`
  performs the right status+resolution transition and is idempotent-safe; `list
  --status` filters correctly. **Materialization checks (one test each):**
  `validate` fails an `applied` record whose `resolution.sha` does not resolve;
  fails one whose SHA resolves but is **not a HEAD-ancestor** (orphan/other
  branch); fails one whose commit **touches no file matching `target_area`**; fails
  one whose commit touches **only the ledger** (`docs/parity-loop/lessons/**`);
  fails one whose **commit message does not name the lesson `id`**; fails an
  instruction-area (`skill-body`/`wrapper`) `applied` record **missing
  `signed_off_by`**; fails a `rejected` record with an empty `reason` **or** missing
  `signed_off_by`; and **passes** a well-formed `applied` (real HEAD-ancestor
  commit naming the lesson, touching the right non-ledger artifact, signed off if
  instruction-area) and a well-formed `rejected`. Use a tmp git repo + tmp ledger
  fixture; inject the date (no `Date.now()` reliance).
- **Schema lint test**: the checked-in `lessons.jsonl` (even if empty) passes
  `validate`. `parity_lessons.py validate` is wired into `just check` (a test that
  shells out to it, or a pre-commit hook) so a malformed or tampered ledger fails
  the gate — this is what lets the step-7 bookkeeping commit skip a Pi review
  round safely.
- **Body-lint test** (extend `tests/test_parity_loop_skill.py`): the parity-loop
  body names Phase 0 (load lessons) and Phase 9 (reflect); the improve body names
  the Pi review gate, the sign-off gate for instruction edits, and the `mark
  applied` step; no placeholders.
- **Wrapper-drift test**: the new `parity-improve` wrappers point at
  `docs/parity-loop/improve-body.md` and don't duplicate it (same cap as before).
- **Manual dry-run**: after one real gap, confirm Phase 9 appends a well-formed
  lesson and a subsequent `parity-improve` run produces a Pi-CLEAN, signed-off
  edit and flips the lesson to `applied`.

## Open questions

1. **Judge-agent vs human for instruction-edit sign-off.** Default: human
   sign-off, with a judge-agent majority vote as an opt-in alternative for
   unattended runs. Confirm whether unattended runs may rely on the judge alone.
2. **Improve batch threshold.** Default ≥5 open lessons triggers a mid-run drain
   (the run-end backstop drains the rest regardless); confirm the
   number.
3. **Reflect transcript-location heuristic.** "Newest jsonl for this agent+cwd" is
   simple but could mis-pick under concurrent sessions. v1 accepts the heuristic;
   a session-id handshake is a later refinement.

## What `validate` can and cannot prove (design stance)

`validate` is a **mechanical backstop, not a theorem prover.** It raises the cost
of faking consumption to the point where faking is harder than doing the work:
an `applied` lesson must point at a real, HEAD-ancestor commit that names the
lesson id and changed the correct non-ledger artifact, with a recorded sign-off
for instruction edits; a `rejected` lesson must carry a reason and sign-off. What
it *cannot* mechanically prove is that the edit is the *semantically correct* fix
for the lesson, or that the named human/judge truly approved. Those are
guaranteed by the **apply-time gates** — the step-3 different-family Pi review over
the actual diff, the step-4 human/judge sign-off, and the fact that every commit
is a reviewable git diff. The ledger checks exist to kill the *cheap* fakes
(bogus SHA, ledger-only commit, area-mismatch, unrelated-commit reuse, unsigned
rejection); the *expensive* judgment stays with the reviewer and signer. This is
deliberate: we are not trying to make a mechanical check sufficient on its own —
that pursuit is endless — only to make the gates' evidence durable and the
shortcuts fail.

## Risks

- **Re-creating emit-only dead code.** Mitigation: the helper is consumed by the
  improve skill and pinned by tests; applied lessons land in the always-loaded
  body. If the improve step is never run, Phase 0's open-lesson surfacing still
  forces visibility.
- **Self-modification drift.** Mitigation: instruction edits are double-gated (Pi
  CLEAN + sign-off); capture stays ungated and harmless.
- **Lesson-ledger rot/noise.** Mitigation: `append` de-dups; `rejected` rows are
  retained with reasons so duds aren't re-proposed; lessons are summary-safe and
  reviewable in git.
- **Transcript mis-pick** under concurrent sessions (see open question 3).
