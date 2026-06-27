# Pipy Parity Loop — Workflow

This is the canonical body of the `pipy-parity-loop` skill. Each agent's
wrapper points here; do not duplicate this content into the wrappers.

Drive **one parity gap end to end**. Do not advance to another gap in a single
invocation — that outer loop is deferred (Phase 2). Reference checkout:
`~/src/pi-mono`. Work on trunk (`main`); do not create feature branches.

## Hard rules (read before starting)

- **Never self-grade.** Every review is a fresh, *different model family* context
  (implementer Opus → review with Pi/GPT; implementer GPT → review with Opus).
- **Review directly; never delegate the review.** The different-family reviewer
  must evaluate the supplied plan/diff in its own context. It must not spawn
  subagents, use Claude Code `Agent`/Task-style delegation, or fan out parallel
  reviewers. If the reviewer cannot complete the review directly within the
  provided bundle/context, treat that as `BLOCKED`, not CLEAN.
- **Never weaken or delete tests** to pass a gate.
- **The commit gate requires the last CLEAN review to cover the exact diff being
  committed.** If any fix (including docs) changes files after a CLEAN verdict,
  re-run `just check`, prek (only if a `.pre-commit-config.yaml` is present), and
  the review gate before committing.
- **Operator override is an escalation, not a pass.** A CLEAN different-family
  review is mandatory; nothing marks a gap "done" without one. The only override
  is when the reviewer CLI is genuinely *unavailable* after retries — and then
  you **stop**, record it in the run note, and surface to the operator. An
  override may never bypass an ISSUES verdict and may never mark a gap complete
  on its own.

## Reviewer selection

The review gate is configurable but must still satisfy the hard different-family
rule.

- `REVIEWER_AGENT` may be `auto`, `pi`, or `opus`; unset is `auto`.
- `REVIEWER_MODEL` may override the selected review harness model when that
  harness supports `--model`.
- `auto` selects by implementer family:
  - Claude/Opus-family implementer -> `pi-review-loop`.
  - GPT/Codex/Pi-family implementer -> `opus-review-loop`.
  - `pipy` implementer -> inspect the active native model/provider; if it is
    GPT/OpenAI-Codex-family, use `opus-review-loop`; if it is Claude/Opus-family,
    use `pi-review-loop`; if the family cannot be determined, stop as `BLOCKED`
    rather than guessing.
- Explicit `REVIEWER_AGENT=pi` forces the Pi review harness; explicit
  `REVIEWER_AGENT=opus` forces the Opus review harness. Before accepting an
  explicit override, verify it is still a different model family from the
  implementer. If it is not different-family, stop as `BLOCKED`; do not treat
  an operator-specified same-family reviewer as a valid CLEAN gate.

Use these commands for the selected gate, adding `--model "$REVIEWER_MODEL"`
when `REVIEWER_MODEL` is set:

```bash
# Pi/GPT-family reviewer
python3 ~/projects/agent-stuff/claude/skills/pi-review-loop/bin/pi-review-loop \
  --repo "$PWD" --run-dir "$(mktemp -d)/pi-review"

# Opus/Claude-family reviewer
python3 ~/projects/agent-stuff/codex/skills/opus-review-loop/bin/opus-review-loop \
  --repo "$PWD" --run-dir "$(mktemp -d)/opus-review"
```

## Phases

0. **Load context & drain the lesson backlog.** Read this body (it now carries
   applied improvements) and run
   `python3 scripts/parity_lessons.py list --status open`. **If the open-lesson
   count is ≥ the threshold (default 5), you MUST run the `parity-improve` skill
   to drain ALL open lessons to zero before selecting a new gap** — each open
   lesson must end `applied` or `rejected`. If the count is below threshold, note
   the open lessons and proceed; the run-end backstop drains the rest.
   *Done-when:* either the count was below threshold (noted, proceeding), or it
   met the threshold and `parity-improve` drove the open count to zero.
1. **Select the gap.** Read `docs/pi-mono-gap-audit.md` (ranked) and
   `docs/backlog.md`; pick the highest-value incomplete slice, or accept an
   operator-supplied gap. Confirm it is a single reviewable slice (decompose if
   not). *Done-when:* one named gap with a one-paragraph scope and the relevant
   `~/src/pi-mono` reference path(s).
2. **Plan.** Read the pi-mono reference; write a short design/plan (what Pi does,
   how pipy matches it through pipy-owned Python boundaries, constraints from
   `AGENTS.md`). **Write the plan to a file** so it is reviewable. *Done-when:* a
   written plan file with done-when criteria.
3. **Review the plan (different family).** Use one explicit path:
   - **Diff-based:** the plan must be a **tracked or staged** file (e.g. a spec
     under `docs/superpowers/specs/`). `git add` it, then run the different-family
     review over the diff. The harness bundles staged/untracked content but
     **not gitignored** files, so a plan kept only under the gitignored
     `docs/parity-loop/runs/` must not use this path.
   - **Direct handoff:** for an untracked/gitignored plan note, use a
     `handoff-review` prompt that includes the plan content inline, or run a
     review mode whose tools can read the path. A tools-disabled reviewer given
     only a file path cannot inspect that file and must not be expected to return
     a verdict.
   *Done-when:* CLEAN verdict (or the Operator-override stop above).
4. **Write the implementation plan.** Turn the reviewed design into an ordered,
   testable task breakdown, written to a file. *Done-when:* numbered plan with
   acceptance criteria per task.
5. **Implement.** Execute on `main`, TDD where it applies, matching Pi behavior;
   remove pipy-only accretions per the no-deprecation policy. *Done-when:* code
   complete, focused tests written.
6. **Update docs (part of the change).** Bring docs + release notes + the parity
   docs (`docs/parity-plan.md`, `docs/pi-mono-gap-audit.md`, `docs/backlog.md`)
   in line with the change, *before* the review gate, so the reviewed diff is
   complete. *Done-when:* docs reflect behavior; the gap is struck from the gap
   source.
7. **Code-review loop until CLEAN (over the complete diff).** Each iteration: run
   `just check` (and `prek run --all-files` only if a `.pre-commit-config.yaml`
   is present — `just check` is pipy's real gate; `pre-commit` is not installed),
   and only when green run the different-family review over the **full diff —
   code and docs together**. The reviewer must be a single direct fresh context:
   no subagents, `Agent` tool, Task-style delegation, or parallel reviewer fanout.
   On an ISSUES verdict, fix and **return to the top of
   this iteration** (re-run `just check` and prek before the next review), so
   every review — including the final CLEAN one — is taken over a diff whose
   gates currently pass.
   *Done-when:* `just check` green, prek green (or absent), and review CLEAN over
   the complete diff in the *same* iteration.
8. **Mark done & report.** Commit (trunk; clean message, no self-reference).
   Record an evidence summary: what changed, gates passed, review verdict.
   *Done-when:* committed, gap marked complete.
9. **Reflect (capture lessons).** Locate this session's transcript with the
   per-agent locator below, then read it with the gap's diff, the review
   verdicts, and how many review rounds it took. Distill 0–N reusable,
   summary-safe lessons (a recurring review finding, a gate failure, a wrong turn
   that cost time, or a better approach — never raw transcript or secrets) and
   append each (after checking `list` to avoid duplicates):

   ```bash
   python3 scripts/parity_lessons.py append --json \
     '{"skill":"pipy-parity-loop","gap":"<gap>",
       "agent":"<host agent: claude|codex|pi|pipy>",
       "trigger":"recurring-review-finding","lesson":"<distilled, summary-safe>",
       "target_area":"<skill-body|wrapper|docs|tests|harness>"}'
   ```

   Set `agent` to the host agent actually running this loop (not a literal), and
   set `target_area` to the artifact the lesson is about — it decides the sign-off
   path and which file a later `applied` commit must touch, so do not leave it as
   `skill-body` by default. The helper assigns the `id` and `status: open` and
   refuses near-duplicates.
   Commit the ledger change as a small `chore(lessons): …` commit. *Done-when:*
   lessons appended (or none worth keeping) and committed.

   **Per-agent transcript locator** (the host agent is known — it is the one
   running this loop):
   - claude: newest `*.jsonl` in `~/.claude/projects/-Users-jochen-projects-pipy/`.
   - pi: newest `*.jsonl` in `~/.pi/agent/sessions/--Users-jochen-projects-pipy--/`.
   - pipy: newest `*.jsonl` in `~/.local/state/pipy/native-sessions/--<cwd>--/`.
   - codex: `~/.codex/history.jsonl` is a single global, untyped log — extract the
     contiguous tail of records sharing the last record's `session_id` (coarser;
     documented limitation).

**Run-end backstop.** A parity-loop run must not conclude with any `open`
lessons. Before finishing, run `parity-improve` until
`python3 scripts/parity_lessons.py list --status open` is empty, so every
captured lesson is consumed (materialized or, with sign-off, rejected).

## Runner single-gap mode

When the invocation prompt contains the marker `runner single-gap mode` (the
parity-runner sets it), run only the single gap — Phases 1–8 plus the **Phase 9
capture** — and **defer Phase 0's drain-enforcement, the `parity-improve` step,
and the run-end backstop to the caller** (the parity-runner owns batch-level
lesson draining). Still capture lessons in Phase 9 as usual; do **not** apply,
drain, or reject them in this mode. Everything else (gates, different-family
review, commit) is unchanged. This exists so an unattended single-gap run never
blocks on a sign-off-needing lesson; lesson application is the runner's job.

## Reuse

- Plan/review/impl framing: the `goal-handoff`, `handoff-impl`, `handoff-review`
  skills.
- The different-family review gate: `pi-review-loop` (review with Pi/GPT) or
  `opus-review-loop` (review with Opus), selected by the reviewer-selection
  rules above.

## Claude Code CLI Hygiene

When invoking Claude Code noninteractively, be explicit about the prompt channel:

- If the prompt is a positional argument, put it after `--` and close child stdin
  with `/dev/null` (for example:
  `claude -p --model opus --no-session-persistence --tools "" --disable-slash-commands -- "$PROMPT" </dev/null`).
- If the prompt is piped or supplied as a prompt file on stdin, do not also pass a
  positional prompt; the pipe/file must be the only prompt source.
- Never place a positional prompt immediately after variadic flags such as
  `--tools ""`; without `--`, Claude may treat the prompt as another tool value.
- For read-only Opus reviews, prefer the `opus-review-loop` harness or direct
  `claude -p` with tools disabled. Do not use `claude-yolo` solely for a
  read-only review. A read-only review must be a direct single-context review:
  no `Agent` tool, Task-style delegation, subagents, or parallel reviewer fanout.
- When tools are disabled, the prompt or review bundle must contain the actual
  plan/diff content being reviewed. Do not ask Claude to review only a path,
  commit name, or unstaged local file reference that it cannot read.
- For write-capable unattended implementation runners, do not replace permission
  bypass with a weaker mode such as `default`, `plan`, or `acceptEdits` unless
  that exact runner has been verified to perform edits, shell commands, and
  commits without prompting.
- Bound availability checks: after a small fixed number of smoke-test/retry
  attempts, stop and report the reviewer CLI as unavailable instead of looping.
