# Parity Runner (Phase 2) — Design

Status: draft for review, 2026-06-22.

## Problem

The parity-loop skill (Phase 1) drives **one** gap end to end, and the learning
loop (Phase 3) lets it improve over time — but a human still has to launch each
gap by hand. Phase 2 is the **unattended driver**: a bounded, scheduled harness
that runs the existing gated unit repeatedly without babysitting, so parity work
makes progress while the operator is away.

The research is explicit that unattended loops over a **brownfield** codebase
fail without strong structure, and that continuous multi-hour runs "overbake."
Phase 2 therefore is **not** a clever autonomous agent. It is a thin,
deterministic driver that repeats a unit which already carries all the hard gates
(plan review, `just check`, different-family code review, commit), adding only:
bounded iteration, hard caps, stop-on-failure, checkpoint/logging, and a
lessons safety net.

## Goals

- Run N parity-loop gaps unattended in one bounded invocation, **fresh context
  per gap**, stopping cleanly at caps or on the first failure.
- Reuse the shipped pieces unchanged: the `pipy-parity-loop` skill is the unit;
  `parity-improve` + `scripts/parity_lessons.py` handle lessons; the
  different-family review gate lives inside the skill.
- Be deterministic and testable with a **fake agent** (no real LLM calls in
  tests).
- Be safe by construction: the runner issues no push and installs best-effort
  no-push guards for the child run (commits stay local); never start a new gap on
  a dirty tree or after a failure; hard time/gap caps; single-run lock.
- Stay scheduler-agnostic: a plain CLI the operator can `launchd`/cron or run
  manually.

## Non-goals (v1)

- No continuous/forever loop; runs are bounded and meant to be scheduled in small
  nightly batches (avoids overbaking + merge cascades).
- The runner performs no pushing, PR creation, or any remote/outward action, and
  installs best-effort guards to block a *naive* child push (see Safety rails);
  commits stay local on `main` for human review. A deliberately adversarial agent
  is not sandboxed — see "What the runner can and cannot guarantee".
- No in-harness scheduler/daemon — an example launchd plist + a `just` recipe are
  provided, but cron/launchd ownership is the operator's.
- No bespoke orchestration of the phases — the runner spawns the agent and lets
  the skill orchestrate; the runner owns only the outer loop and safety.
- Default driver is `claude-yolo --model opus`; other agents are a later
  `--agent` extension, out of scope here.

## Background (shipped pieces it builds on)

- **The unit:** `docs/parity-loop/skill-body.md` (`pipy-parity-loop`) drives one
  gap with hard gates: plan → different-family plan review CLEAN → impl → docs →
  `just check` + prek (if configured) + different-family code review CLEAN →
  commit → Phase 9 reflect. Phase 0 drains the lesson backlog; a run-end backstop
  drains the rest.
- **Driver/review pairing:** default `claude-yolo -p --model opus` implements;
  the skill's review gate uses `pi-review-loop` (GPT, different family).
- **Lessons:** `scripts/parity_lessons.py` (validate/append/list/mark) over
  `docs/parity-loop/lessons/lessons.jsonl`; `parity-improve` materializes them.
- **Run scratch dir:** `docs/parity-loop/runs/` already exists and is gitignored
  (Phase 1 scaffolding) — the run log lives here.
- **Gate command:** pipy's real gate is `just check` (ruff + mypy + pytest); prek
  runs only if a `.pre-commit-config.yaml` exists; `pre-commit` is not installed.

## Architecture

A single deterministic module `scripts/parity_runner.py` (stdlib + `subprocess`,
no LLM dependency), CLI + importable library, tested like `parity_lessons.py`.
All judgment stays inside the spawned agent + skill; the runner owns the loop,
verification, caps, checkpointing, and stop conditions.

### Per-gap spawn contract

For each iteration the runner spawns a **fresh** agent process:

```
claude-yolo -p --model opus  <prompt>
```

where `<prompt>` instructs: *"Run the `pipy-parity-loop` skill for exactly ONE
gap, in this repo, on `main`, in **runner single-gap mode** (see below). Do not
push. When finished, print exactly one final line: `PARITY_RESULT: COMMITTED
<sha>` or `PARITY_RESULT: NO_GAPS` or `PARITY_RESULT: BLOCKED <reason>`."*

**Runner single-gap mode — defer lesson application to the runner.** The skill's
Phase 0 drain-enforcement and the run-end backstop both require draining the open
lesson backlog to zero, which an unattended child *cannot* do for instruction-area
lessons (they need human/judge sign-off). If the child ran them, a single captured
sign-off-needing lesson would make it block or time out, stopping the batch and
starving the runner's batch-level `lessons_backlog` (exit 3) path. So the spawn
prompt tells the child to run Phases 1–8 (the gap) **and Phase 9 capture** as
usual, but to **skip Phase 0's drain-enforcement, the `parity-improve` step, and
the run-end backstop** — lesson *application* and the batch-level drain are owned
by the **runner** (its safety net + exit-3 handling). The child still *captures*
lessons (Phase 9 append), which is exactly what feeds the runner's safety net.
This is the only behavioral divergence from a hand-run gap.

**Implementation prerequisite (hard, not an assumption).** The canonical
`docs/parity-loop/skill-body.md` currently mandates the Phase 0 drain and run-end
backstop *unconditionally*, so the runner is **incorrect until** the skill body
gains the keyed exception. Therefore the **first implementation task** of this
design is to add to `skill-body.md`: *"When the invocation prompt contains the
marker `runner single-gap mode`, run only the single gap (Phases 1–8) plus Phase 9
capture, and defer Phase 0's drain-enforcement, the `parity-improve` step, and the
run-end backstop to the caller (the parity-runner)."* The runner activates it by
including that exact marker phrase in the spawn prompt (it already does). Until
this clause lands, a child spawned while open lessons exist would follow the
unconditional skill and block in `parity-improve` instead of returning through the
runner's `lessons_backlog` (exit 3) path — so the clause and the runner ship
together.

The spawn command itself is a small per-agent adapter (v1: only the `opus`
adapter, which shells `claude-yolo -p --model opus`). The runner captures the
child's stdout/stderr to the run dir and parses the **last** line matching
`^PARITY_RESULT: (COMMITTED \S+|NO_GAPS|BLOCKED .*)$`.

### Independent verification (do not trust the sentinel alone)

The sentinel is a *claim*; the runner verifies it against git reality before
accepting it:

- **Before spawning:** record `head_before = git rev-parse HEAD`; assert the tree
  is clean (`git status --porcelain` empty) **and the current branch is `main`**
  (`git rev-parse --abbrev-ref HEAD` == `main`); and snapshot the full ref set
  `refs_before = git for-each-ref --format='%(refname) %(objectname)'`.
- **After the child exits**, first assert **all** of: the current branch is
  `main`; **and the ref set changed only by `refs/heads/main` advancing forward**
  — recompute `git for-each-ref` and require that no branch/ref was created or
  deleted and no non-`main` ref tip moved versus `refs_before`. This catches a
  child that committed on another branch even if it switched back to `main` (an
  off-`main` branch commit creates or moves a ref). Any violation → **failure**,
  regardless of the sentinel. (A detached-HEAD commit that moves no ref is
  unreachable, never pushed, and harmless — out of scope.) Then:
  - **`COMMITTED <sha>`** is accepted only if **all** hold: exit code 0; branch is
    `main`; tree clean; **forward-only progress** — `head_before` is a *strict*
    ancestor of the new `HEAD` (`git merge-base --is-ancestor head_before HEAD`
    succeeds **and** `HEAD != head_before`), which rejects an unchanged HEAD and
    any history rewrite; and the reported `<sha>` lies **strictly in the new
    range** `(head_before, HEAD]` — i.e. `git merge-base --is-ancestor head_before
    <sha>` and `git merge-base --is-ancestor <sha> HEAD` both succeed and `<sha> !=
    head_before`. Any mismatch → **failure**. (Reporting `HEAD` is the common case
    and always satisfies this.)
  - **`NO_GAPS`** is accepted only if exit 0, branch `main`, tree clean, and `HEAD`
    **unchanged** (`HEAD == head_before`, no stray commit). → stop, success,
    "nothing left."
  - **`BLOCKED <reason>`** → stop (exit 1) and report the reason; this is an
    **unclean stop** (no safety net). Also verify state: if `HEAD` moved, the ref
    set changed, or the tree is dirty under a BLOCKED claim, additionally record
    `unexpected_progress` and set `needs_human_cleanup` — the child left un-vetted
    changes that were never accepted/gated. (A clean BLOCKED — `HEAD` unchanged,
    tree clean, on `main` — just reports the blocker.)
  - **No parseable sentinel, non-zero exit, or per-gap timeout** → failure.
- **Dirty tree, or a non-`main` branch, after any gap** (e.g. a killed/half-done
  gap) → **stop immediately**, do not start another gap; surface for human
  cleanup. The runner never auto-commits, auto-reverts, or switches branches.

### Bounded loop + stop conditions

```
# Startup order matters: reserve the run label LAST, only after lock + preconditions pass,
# so a failed/parallel start never burns a label or leaves a stray run dir.
assert per-run path <run-dir>/run-<label>/ is gitignored-or-outside-worktree (else exit 2)
assert clean tree and branch==main (else exit 2)
acquire per-repo single-run lock (else exit 2)
create per-run dir EXCLUSIVELY (else exit 2: duplicate_run_label)   # AFTER lock + preconditions
install no-push guards (pre-push hook + blocked pushurl); ensure restore on exit
start = <injected clock>
lesson_gate("preflight")   # never start new gaps over a prior run's undrained backlog (below)
head = HEAD; gaps_done = 0; stop = None
while gaps_done < max_gaps:
    remaining = time_budget - (now - start)
    if remaining < min_gap_slice:                      # not enough time for a useful gap
        stop = "cap_reached"; break
    # Clamp so the run never exceeds time_budget by more than scheduling overhead.
    result = spawn_one_gap(timeout=min(per_gap_timeout, remaining))   # fresh process
    log(result)
    if result is COMMITTED-and-verified:
        gaps_done += 1; head = HEAD; continue
    else:                                              # NO_GAPS / BLOCKED / failure / dirty / non-main
        stop = result; break
else:
    stop = "cap_reached"                               # max_gaps reached
# Lesson gate ONLY on a clean stop, clean tree on main (same routine as pre-flight).
clean_stop = stop in ("no_gaps", "cap_reached")
if clean_stop and tree_clean() and branch_is_main():
    lesson_gate("postloop")
else:
    record needs_human_cleanup=true                    # do NOT spawn anything over partial work
write run summary; restore no-push guards; release lock

# Shared lesson gate — run at pre-flight (before the first gap) AND post-loop:
def lesson_gate(phase):
    if parity_lessons.validate() != 0:                 # hard, cheap, always
        stop="ledger_invalid"; exit 1
    if open_lessons_remain():
        if remaining_budget() >= min_gap_slice:        # drain what's gateable unattended
            spawn parity-improve (ref-aware postconditions; may set exit 1)
        else:
            record safety_net_skipped="budget"
        if parity_lessons.validate() != 0: stop="ledger_invalid"; exit 1
        if open_lessons_remain():                      # sign-off-needing lessons remain
            record needs_human_review; stop="lessons_backlog"; exit 3
    # preflight: exit 3 here means ZERO gaps ran (refused to pile on an undrained backlog)
```

A gap's **effective timeout** is `min(per_gap_timeout, remaining_budget)`, so
`--time-budget` is a true wall-clock ceiling that **may terminate an in-flight
gap** (its child **process group** is killed, like `pi-review-loop`). That is
acceptable because a killed gap is treated as a **failure** and the subsequent
**stop-on-dirty-tree** check halts the run and surfaces the half-done state for
human cleanup — the runner never starts another gap over it and never
auto-commits/reverts it. (Choosing a hard cap over "let the current gap finish" is
deliberate: bounded cost beats avoiding the occasional human-cleanup stop, and
every gap is independently gated/committed so at most the *current* gap is partial.)

### Fresh context per gap

Each gap is a new `claude-yolo -p` process — no shared/long-lived context. Durable
state is git (commits), the gap docs (`pi-mono-gap-audit.md` / `backlog.md`), and
the lesson ledger. This is the Ralph principle that prevents drift on long runs.

### Lessons safety net

Because the child runs in **runner single-gap mode** (it defers Phase 0 drain,
`parity-improve`, and the run-end backstop), the **runner is the sole owner of
draining lessons** — this is the runner-level equivalent of the skill's Phase 0 +
run-end backstop. The runner runs one shared **lesson gate** routine at **two**
points:

- **Pre-flight (before the first gap):** so a scheduled retry never piles new gaps
  on a backlog a prior `exit 3` left for human drainage. If open lessons survive the
  gate here, the run **exits 3 having started zero gaps**.
- **Post-loop (after a clean stop):** to drain lessons captured *this* run.

The gate (see pseudocode) is: cheap `validate` (hard, `ledger_invalid` exit 1);
if open lessons remain, spawn `parity-improve` once when budget allows
(`remaining_budget ≥ min_gap_slice`, fresh process, timeout
`min(per_gap_timeout, remaining_budget)`) to drain what's gateable unattended
(docs/tests/harness lessons need only a Pi-CLEAN review); then if open lessons
*still* remain (instruction-area / rejections needing human sign-off), record
`needs_human_review` and **exit 3**.

**Unattended improve mode (hard prerequisite, like single-gap mode).** The
canonical `parity-improve` processes *every* open lesson and requires human/judge
sign-off for instruction-area applies and for **any** rejection — which the runner
does not have. So the runner spawns `parity-improve` with the marker `runner
unattended mode`, under which it must: **apply only lessons gateable without
sign-off** (`target_area` ∈ {`docs`,`tests`,`harness`}, gated by a Pi-CLEAN
review), and **leave every instruction-area lesson (`skill-body`/`wrapper`) and
every rejection candidate untouched and `open`** — never blocking on, waiting for,
or faking sign-off. This requires a one-clause addition to
`docs/parity-loop/improve-body.md` keyed on that marker, **shipped with the runner**
(an implementation prerequisite alongside the skill-body single-gap clause). With
it, the unattended `parity-improve` returns promptly having drained the
auto-gateable lessons, leaving the sign-off-needing ones open so the gate cleanly
reaches `exit 3` instead of blocking or timing out.

When the post-loop spawn runs, it has explicit **postconditions**: If the budget is
exhausted (`remaining_budget < min_gap_slice`), the safety net is **skipped** and
recorded as `safety_net_skipped: budget` — the runner never overruns
`--time-budget` to drain lessons. Because open lessons then remain, the gate still
exits 3 (work-complete, lessons-pending), and the **next scheduled run's pre-flight
gate** refuses to start new gaps until they are drained. **The cheap local ledger checks below (`validate` + `list`) always run on a clean
stop — even when the `parity-improve` spawn was skipped for budget** — because they
are sub-second and excluded from the time ceiling; only the *spawn* is
budget-gated. When the spawn does run, it has explicit **postconditions**:
- **Process result:** if `parity-improve` times out or exits non-zero, record a
  `safety_net_failed` warning; it does not by itself fail the run, but the state
  rechecks below still apply.
- **State recheck (ref-aware):** snapshot the ref set immediately *before* the
  safety-net spawn; *after*, require branch `main`, a clean tree, and that **the
  ref set changed only by `refs/heads/main` advancing forward**. `parity-improve`
  legitimately commits (its materialization edit + ledger bookkeeping), so `main`
  may advance — but a history rewrite, a non-forward move, or any non-`main` ref
  created/moved/deleted, or a dirty tree / off-`main` branch, → record
  `needs_human_cleanup` + `safety_net_dirtied` and **exit 1** (do not pretend the
  run is clean). This is the same ref contract used to verify a gap.

Then it checks the ledger two ways:
- **Hard (`ledger_invalid`, exit 1):** `python3 scripts/parity_lessons.py --repo .
  validate` must exit 0. A corrupt or unmaterialized ledger is a real defect → set
  `stop_reason = ledger_invalid` and exit 1.
- **Open backlog (`lessons_backlog`, needs human, exit 3):** the safety-net
  `parity-improve` *does* drain the lessons it can gate unattended — docs/tests/
  harness lessons need only a Pi-CLEAN review, no sign-off. But instruction-area
  lessons (`skill-body`/`wrapper`) and rejections require human/judge sign-off,
  which an unattended run does not have, so they legitimately remain `open`. The
  canonical parity-loop run-end backstop requires draining open lessons to **zero**,
  and an unattended run cannot silently violate that. Therefore a non-empty
  `list --status open` after the safety net is **not** an exit-0 warning: record
  `needs_human_review` + `lessons_backlog` and **exit 3** ("work complete, lessons
  pending"). The gated commits already made are valid; exit 3 tells the operator
  the learning loop is not fully closed and they must drain the remainder (apply or
  reject the open lessons with sign-off). This keeps the canonical
  drain-to-zero/consumption guarantee intact rather than letting below-threshold
  lessons linger.

**On any unclean stop** (BLOCKED,
failure, dirty tree, or non-`main` branch) the safety net is **skipped entirely**
— the runner must not spawn anything that could commit over partial/abandoned
work; it records `needs_human_cleanup` and surfaces it.

### Checkpoint / run log

A **per-run directory** `docs/parity-loop/runs/run-<runlabel>/` (gitignored)
holding `run.jsonl` (the append-only event log) plus the raw child logs, so
sequential runs sharing a `--run-dir` never overwrite each other's audit trail.
The runner creates this directory **exclusively** (atomic `os.mkdir` that fails if
it already exists), and — critically — **only after the per-repo lock is held and
all preconditions (clean tree, branch `main`, per-run path ignored) have passed**,
so a failed or parallel start never reserves a label or leaves a stray empty run
dir. A **reused `<runlabel>`** that survives to this point (manual retry or a
timestamp collision) **exits 2** (`duplicate_run_label`) before writing anything,
rather than overwriting or mingling a prior run's `run.jsonl` / `gap-<n>.log` /
`improve.log`.
**Precondition:** the runner writes only under the concrete per-run path
`<run-dir>/run-<runlabel>/`, and **that path** must be git-ignored or outside the
worktree, so its writes never dirty `git status --porcelain` (which would break
the clean-tree gap verification). The check must target the **per-run path, not
the base `--run-dir`**: the default ignore rule is `docs/parity-loop/runs/*`, so
`git check-ignore docs/parity-loop/runs` (the base dir) returns *not-ignored*
while `git check-ignore docs/parity-loop/runs/run-<label>` returns *ignored* — so
checking the base dir would wrongly fail the default run. The runner runs
`git check-ignore` on the per-run path (for in-worktree run dirs) at startup and
**exits 2** if it is tracked/not ignored, before writing anything.
`run.jsonl` has one object per event: `run.started` (caps, head_before, agent),
`gap.completed` (index, sentinel, sha, elapsed_s, verified), `gap.failed`
(reason), `lessons.safety_net` (open_before/after), `run.finished`
(gaps_done, stop_reason, elapsed_s). `<runlabel>` is supplied by the caller (e.g.
the launchd job passes a timestamp) — the module does **not** call
`Date.now()`/`uuid` internally so it stays deterministic and testable; the CLI
fills a default label from the injected clock. The label is **validated to a safe
basename** (`^[A-Za-z0-9._-]+$`, no `/` or `..`); an invalid label is rejected
(exit 2) so a log can never be written outside `--run-dir`. Raw child stdout/stderr
are stored inside the same per-run directory as `gap-<n>.log` (and `improve.log`
for the safety-net spawn) — never overwritten across runs because the directory is
keyed on the validated `<runlabel>`. The "real" resumable state is git + gap docs,
so a later run just continues from there.

### Safety rails

- **No push (runner + best-effort child guard).** The runner issues no
  `git push`; the skill body has no push step; the spawn prompt says "Do not
  push." For the duration of the run it adds two **best-effort mechanical
  guards**, restored exactly on exit:
  - **Primary — blocked `pushurl` (transport-level, hook-independent):** for every
    remote, **snapshot all existing `pushurl` values** (`git config --get-all
    remote.<name>.pushurl` — a remote may have several and Git pushes to *all* of
    them), **unset them all**, then set a single blocked `pushurl`. A push then
    fails regardless of hooks, `--no-verify`, or pre-existing push URLs — including
    a remote with **no** `pushurl` (where push would otherwise fall back to the
    fetch URL, which the blocked `pushurl` now overrides). On exit, restore exactly:
    unset the blocked value and re-add each snapshotted `pushurl` in order (leaving
    none if the remote originally had none). (An adversarial agent could reset the
    config; that is out of scope.)
  - **Secondary — `pre-push` hook (only when safe):** resolve the effective hooks
    directory — if `git config --get core.hooksPath` is set use that (note
    `git rev-parse --git-path hooks/...` does **not** honor `core.hooksPath`, so it
    must be checked explicitly), otherwise `git rev-parse --git-path hooks`. Install
    the `pre-push` hook **only if that directory is inside the git dir** (`.git/…`,
    i.e. untracked scratch), preserving/restoring any prior hook. If the effective
    hooks dir is anywhere in the **worktree** (e.g. a tracked `.githooks/`) or
    **outside** the repo (shared global), the runner **skips** the hook —
    installing it would dirty tracked files or clobber shared config — and relies
    on the `pushurl` guard, recording `prepush_hook_skipped: <tracked|shared>`.

  Together these stop accidental/naive pushes but do **not** defeat a deliberately
  adversarial agent (config reset + `--no-verify`) — out of scope (see guarantees).
- **Single-run lock (per-repo, not per-run-dir):** an atomic mkdir lock keyed on
  the repository, located at `<git-common-dir>/parity-runner.lock` (resolved via
  `git rev-parse --git-common-dir`, so it is the *same* path regardless of
  `--run-dir`, worktree, or CWD). PID recorded; stale lock reclaimed by liveness
  check (like `pi-review-loop`). This prevents two concurrent runners — even ones
  pointed at different `--run-dir`s — from racing on the same `main`.
- **Hard caps** with conservative defaults: `--max-gaps 3`, `--time-budget 7200`
  (2h), `--per-gap-timeout 2400` (40m), `--min-gap-slice 600` (10m). `--time-budget`
  is a genuine wall-clock ceiling on **agent-spawning work** (each gap, and the
  safety-net `parity-improve`): a spawn's effective timeout is
  `min(per_gap_timeout, remaining_budget)`, and neither a new gap nor the safety
  net starts with less than `--min-gap-slice` remaining — so a run never exceeds
  `--time-budget` by more than process-teardown overhead. The runner's own
  deterministic bookkeeping (git checks, `parity_lessons.py validate`/`list`, log
  writes) is local and sub-second and is excluded from the ceiling. Optional
  `--token-budget` (see open questions).
- **Stop-on-failure**, **stop-on-dirty-tree**, **stop-on-NO_GAPS** — the loop
  never continues through an ambiguous or broken state.

### Scheduling (outside the harness)

Shipped as a plain CLI plus:
- a `just parity-run` recipe wrapping `uv run python scripts/parity_runner.py …`
  with the defaults;
- an **example** launchd plist (documented, not auto-installed) that runs
  `just parity-run` nightly and passes a timestamp run label.

The operator owns enabling/scheduling; the harness has no daemon.

## CLI interface

```
uv run python scripts/parity_runner.py \
  [--agent opus] [--max-gaps 3] [--time-budget 7200] [--per-gap-timeout 2400] \
  [--min-gap-slice 600] [--token-budget N] [--run-dir docs/parity-loop/runs] \
  [--run-label <label>] [--repo .] [--dry-run]
```

`--dry-run` spawns nothing; it validates preconditions (clean tree, branch
`main`, the per-run path gitignored-or-outside-worktree, the per-run dir does not
already exist, lock available, gap docs present) and prints what it would do. Exit codes:
`0` fully clean stop (gaps done, cap reached, or NO_GAPS, **with no open lessons
remaining**), `1` stopped on failure/blocked/dirty/non-main/`ledger_invalid`/
`safety_net_dirtied`, `2` precondition/lock error, `3` work complete but
`lessons_backlog` remains (open lessons need human sign-off to drain).

## Testing strategy

Deterministic unit tests with a **fake agent** — a tiny stub script the runner is
pointed at via a seam (`--agent-cmd`/injected spawn function) that, per scripted
scenario, prints a chosen `PARITY_RESULT` line and optionally makes/doesn't-make a
commit in a tmp git repo. No real LLM calls. Cover:

- **Sentinel parsing:** last-match wins; unparseable → failure.
- **Verification:** `COMMITTED` accepted only when branch is `main`, tree clean,
  `head_before` is a strict ancestor of the advanced `HEAD`, and the cited sha is
  strictly within `(head_before, HEAD]`. Rejected when the fake: makes no commit
  (HEAD unchanged); leaves the tree dirty; cites a bogus sha; cites `head_before`
  itself; cites a sha outside the new range; rewrites history so `head_before` is
  no longer an ancestor; switches off `main` before committing; or commits on
  another branch (creating/advancing a non-`main` ref) even if it returns to
  `main` before exit.
- **Stop conditions:** `NO_GAPS` stops with success; `BLOCKED` stops with reason;
  non-zero exit stops; dirty tree / non-`main` branch after a gap stops;
  `--max-gaps` and `--time-budget` (injected clock) each stop the loop.
- **Safety net gating:** after a clean stop (`NO_GAPS` / cap) **with budget
  remaining** the fake `parity-improve` IS spawned; after a clean stop with the
  **budget exhausted** (`remaining < min_gap_slice`, injected clock) it is
  **skipped** with `safety_net_skipped: budget`; after an unclean stop (BLOCKED /
  failure / dirty / non-`main`) it is **not** spawned and `needs_human_cleanup` is
  recorded.
- **Lock (per-repo):** a second runner against the same repo fails with exit 2
  **even when pointed at a different `--run-dir`**; a stale lock (dead PID) is
  reclaimed.
- **No-push guard:** on start the runner sets a blocked `pushurl` and, **only when
  the effective hooks dir is inside `.git/`**, installs a `pre-push` hook (resolve
  `core.hooksPath` first, else `git rev-parse --git-path hooks`); a child
  `git push` attempt fails while active. On exit the prior state is restored
  exactly — `pushurl` reverted, and a pre-existing `pre-push` hook put back (or
  removed if none existed). When the hooks dir is in the worktree (tracked
  `.githooks/`) or shared/global, the hook is **skipped** (no tracked file is
  touched) and only the `pushurl` guard is asserted — verify the skip leaves the
  tree clean.
- **Lessons backlog exit code:** after a clean stop, if the (fake) ledger still
  has `open` lessons, the run records `needs_human_review`/`lessons_backlog` and
  exits **3**; with no open lessons it exits **0**. A failing `validate` exits 1
  (`ledger_invalid`).
- **Pre-flight backlog gate:** starting a run while the ledger already has `open`
  lessons drains what the fake `parity-improve` can, and if any remain, exits **3**
  with **zero gaps started** (the gap-spawn seam is never called) — a scheduled
  retry refuses to pile new gaps on an undrained backlog.
- **Duplicate run label:** a run whose `<run-dir>/run-<label>/` already exists
  exits **2** (`duplicate_run_label`) at startup and overwrites no existing file.
- **Run-dir precondition (per-run path):** an in-worktree `--run-dir` whose per-run
  path `<run-dir>/run-<label>/` is **not** ignored → exit 2; the default
  `docs/parity-loop/runs/` passes because the per-run path matches the
  `docs/parity-loop/runs/*` ignore rule (the base dir alone does not — assert the
  check targets the per-run path, not the base).
- **Run log:** events written in order with the expected shape; the log JSONL
  parses.
- **No real LLM / no network** asserted by construction (the fake-agent seam).

## What the runner can and cannot guarantee

Like `parity_lessons.py validate`, the runner is a deterministic safety harness,
not a sandbox. It **guarantees**: bounded iteration; that every accepted gap
corresponds to a real clean-tree commit on `main` (forward-only, cited sha in the
new range); stop-on-failure / dirty-tree / non-`main`; no pushes *issued by the
runner*; best-effort blocking of a *naive* child push (pre-push hook + blocked
pushurl); and single-run mutual exclusion. It **cannot** guarantee a *deliberately
adversarial* agent behaves — one could defeat the no-push guards (`--no-verify` +
config reset) or make a bad-but-committed change. Those are caught by human review
of the local commits (nothing reaches the remote without a deliberate,
guard-defeating push) and by the different-family review gate inside the unit.
This is deliberate: the safety comes from bounded local increments + gates + human
review, not from sandboxing the agent.

## Open questions

1. **Token budget source.** `claude -p --output-format json` reports usage/cost;
   v1 can parse it to enforce `--token-budget` between gaps. If the format is
   unstable, ship with time+gap caps only and add token caps later. Proposed:
   implement `--token-budget` as optional, off by default.
2. **`--agent` generalization.** v1 ships only the `opus` adapter; the seam is
   designed so `codex`/`pi`/`pipy` adapters slot in later. Confirm that's the
   right v1 boundary.
3. **Run label / clock injection.** The module takes the clock and run label as
   inputs (no ambient `Date.now()`); the CLI fills defaults. Confirm this is
   acceptable ergonomically.

## Risks

- **Brownfield drift.** Mitigated by fresh-context-per-gap + the unit's hard
  gates + bounded runs (the whole reason this is safe now and wasn't before the
  unit existed).
- **Half-done state from a killed gap.** Mitigated by stop-on-dirty-tree and never
  auto-committing partial work.
- **Runaway cost/time.** Mitigated by `--max-gaps`/`--time-budget`/
  `--per-gap-timeout`; optional token cap.
- **Agent misbehavior (push / bad commit).** Mitigated by never-push policy +
  local-only commits + human review + the in-unit review gate (see guarantees
  above); not sandboxed.
- **Concurrent runs racing `main`.** Mitigated by the single-run lock.
