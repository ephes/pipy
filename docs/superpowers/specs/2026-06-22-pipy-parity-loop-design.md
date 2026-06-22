# Pipy Parity Loop — Design

Status: draft for review, 2026-06-22.

## Problem

Reaching Pi parity in pipy is a repeated, hand-driven workflow. For every gap
the operator types the same instructions into whichever agent they are in:
"look at `~/src/pi-mono`, pick the next gap, plan it, run a different-model
review loop on the plan, write an implementation plan, implement, run
code-review cycles until clean, then bring docs up to date," and then advance to
the next gap. This is high-friction, easy to do inconsistently, and impossible
to hand to an unattended run because the recipe lives only in the operator's
head.

The operator uses four agents and wants the workflow available from all of
them:

- `claude-yolo` (Opus, subagents also Opus)
- `codex-recorder` / Codex (GPT-5.5)
- `pi` (GPT-5.5)
- `pipy` (native; wanted for dogfooding)

This spec covers **Phase 1**: encode the repeated parity workflow as a single
canonical, portable skill that lives in the pipy repo and is usable from all
four agents. Phase 2 (an unattended outer-loop harness) and Phase 3 (learning
from recorded sessions) are sketched as follow-ons but are explicitly out of
scope for the initial implementation.

## Goals

- One canonical description of the parity workflow, stored in the pipy repo, so
  the recipe stops living in the operator's head.
- Invocable from Claude Code, Codex, and Pi, and (bonus) pipy — without four
  diverging copies. Each agent is reached through its own native
  project-discovery mechanism (a `SKILL.md`/skill file for Claude/Pi/pipy; the
  `AGENTS.md` parity section for Codex, which is Codex's native repo-instruction
  surface). "Skill" here means "discoverable and followable from that agent,"
  not "a dedicated skills-dir entry in every agent."
- Operationalize the gates `AGENTS.md` already mandates: `just check` green,
  pre-commit green, an independent **different-model-family** review pass before
  "done," all on trunk (`main`).
- Drive **one parity gap end to end** per invocation, with explicit done-when
  criteria and evidence requirements at each phase, so the result is auditable.
- Reuse existing machinery (`goal-handoff`, `handoff-impl`, `handoff-review`,
  `pi-review-loop` / `opus-review-loop`) rather than reinventing it.

## Non-goals (Phase 1)

- No unattended multi-hour autonomous runner (that is Phase 2).
- No automatic learning / reflection from recorded sessions (Phase 3).
- No new session-capture format and no change to the metadata-first capture
  policy in `AGENTS.md`.
- No change to how gaps are *discovered* — the skill consumes the existing
  parity docs as its gap source; it does not generate a fresh gap audit.

## Background: what already exists

- **Gap source.** `docs/parity-plan.md` is the index; `docs/pi-mono-gap-audit.md`
  is the ranked "what is biggest now?" snapshot for slice selection;
  `docs/backlog.md` names the next reviewable slice; `docs/parity-criterion.md`
  defines what "parity" means for a surface. The reference checkout is
  `/Users/jochen/src/pi-mono` (a.k.a. `~/src/pi-mono`).
- **Gates.** `AGENTS.md` mandates trunk-based work on `main` (no feature
  branches for routine work), `just check`, pre-commit hooks green, focused
  tests, docs updated in the same change, and a scaled independent review pass
  (implementation slices usually need a clean follow-up review after fixes).
- **Review machinery.** `agent-stuff` provides `pi-review-loop` (spawns Pi /
  GPT-5.5 as a fresh-context reviewer over the git diff, bounded loop, gates on
  a CLEAN verdict) and `opus-review-loop` (same shape, Claude Opus). These are
  the different-family review gate; the skill calls them rather than embedding
  review logic.
- **Handoff machinery.** `goal-handoff` (bounded objective), `handoff-impl`,
  `handoff-review` already encode how to frame each phase for a fresh context.
- **Skill discovery per agent (verified):**
  | Agent | Project-local skill location |
  |---|---|
  | pipy (native) | `<repo>/.pipy/skills/*.md` (single markdown + frontmatter; dir does not exist yet) |
  | Claude Code | `<repo>/.claude/skills/<name>/SKILL.md` |
  | Pi | `<repo>/.pi/skills/*.md` (Pi convention pipy mirrors) |
  | Codex | discovered via `<repo>/AGENTS.md` (+ its skills dir) |

## Architecture

### Canonical body + thin per-agent wrappers

The workflow is written **once** as a canonical body, with a thin entry point
per agent that points at the canonical body. This satisfies "lives in the pipy
repo" and "works from all four agents" without four copies drifting.

```
docs/parity-loop/skill-body.md        # canonical workflow (single source of truth)
.claude/skills/pipy-parity-loop/SKILL.md   # frontmatter + "follow docs/parity-loop/skill-body.md"
.pipy/skills/pipy-parity-loop.md           # frontmatter + same pointer
.pi/skills/pipy-parity-loop.md             # frontmatter + same pointer
AGENTS.md  (new "Parity loop" section)     # pointer for Codex
```

- Each wrapper carries only the agent-appropriate frontmatter (name +
  trigger/description) and one instruction: read and follow
  `docs/parity-loop/skill-body.md` (relative-path resolution against the repo
  root is already part of every agent's skill contract). Wrappers may add a
  handful of agent-specific notes (e.g. Claude can use subagents for phases;
  Codex/Pi run phases inline) but never a second copy of the workflow.
- A wrapper-drift guard: a tiny test (see Testing) asserts every wrapper
  references the canonical body and contains no duplicated phase content beyond a
  size cap.

Rationale: this is the same per-agent-wrapper pattern `agent-stuff` already
uses, relocated *inside* pipy because the workflow is pipy-specific (it points
at `~/src/pi-mono`, uses `just check`, and reads pipy's own gap docs).

### Control flow (Phase 1)

The **host agent drives the phases itself** (the agent the operator launched).
The only thing it spawns *out of family* is the review gate, via the existing
`pi-review-loop` / `opus-review-loop`. On Claude Code the host may delegate
phases to subagents; on Codex/Pi/pipy the host runs phases inline. The outer
"repeat across many gaps" loop is **not** part of Phase 1 — one invocation
drives one gap. (Phase 2 adds a deterministic outer loop; see below.)

This keeps Phase 1 small and identical-in-spirit across agents, and avoids
automating the recipe before it is proven.

### Different-family review rule

The review gate must run a **different model family** than the implementer:

- Implementer is Claude/Opus (claude-yolo, pipy-on-Claude) → review with
  `pi-review-loop` (GPT-5.5).
- Implementer is GPT-5.5 (Codex, Pi, pipy-on-GPT) → review with
  `opus-review-loop` (Opus).

The canonical body states this rule and how to pick the reviewer from the
current host; it does not hard-code a single reviewer.

## The workflow (canonical body contents)

The skill drives one gap through these phases. Each phase has an explicit
done-when gate; the skill does not advance until the gate is met.

1. **Select the gap.** Read `docs/pi-mono-gap-audit.md` (ranked) and
   `docs/backlog.md`; pick the next highest-value incomplete slice, or accept an
   operator-supplied gap argument. Confirm scope is a single reviewable slice
   (decompose if not). *Done-when:* one named gap with a one-paragraph scope and
   a pointer to the relevant pi-mono reference path(s).
2. **Plan.** Read the pi-mono reference for the gap; produce a short design/plan
   (what Pi does, how pipy will match it through pipy-owned Python boundaries,
   constraints from `AGENTS.md`). **The plan is written to a file** (a design
   spec under `docs/superpowers/specs/` and/or a run note under
   `docs/parity-loop/runs/`) so it is reviewable as working-tree content.
   *Done-when:* a written plan file with done-when criteria for the gap itself.
3. **Review the plan (different family).** Review the *plan file* with a
   different model family, using one of two explicit paths (never assume the
   harness "just picks it up"):
   - **Diff-based path:** the plan must be a **tracked or staged** file (e.g. a
     design spec under `docs/superpowers/specs/`). `git add` it first; the
     review harness bundles staged/untracked content but **not gitignored**
     files, so a plan kept only under the gitignored `docs/parity-loop/runs/`
     would be silently excluded and must not use this path.
     Then run `pi-review-loop` / `opus-review-loop` over the diff.
   - **Direct handoff path:** for any untracked or gitignored plan note, use a
     `handoff-review` prompt that points the reviewer explicitly at the plan
     file path. Use this whenever the plan is not staged/tracked.
   *Done-when:* CLEAN verdict from one of these paths. (For the narrow case
   where the reviewer is *unavailable*, see "Operator override" below — it is an
   escalation/stop, not a way to pass.)
4. **Write the implementation plan.** Turn the reviewed design into an ordered,
   testable task breakdown (this is where `handoff-impl` / the superpowers
   writing-plans discipline applies), also written to a file. *Done-when:* a
   numbered plan with acceptance criteria per task.
5. **Implement.** Execute the plan on `main` (trunk), TDD where it applies,
   matching Pi behavior; remove pipy-only accretions per the no-deprecation
   policy. *Done-when:* code complete, focused tests written.
6. **Update docs (part of the change).** Bring docs + release notes + the parity
   docs (`parity-plan.md` / `pi-mono-gap-audit.md` / `backlog.md`) in line with
   the change, per the global "docs are part of the change" rule. Docs are
   updated *before* the review gate so the reviewed diff is the complete change.
   *Done-when:* docs reflect behavior; the gap is struck from the gap source.
7. **Code-review loop until CLEAN (over the complete diff).** Each iteration:
   run `just check` + pre-commit, and only when both are green run the
   different-family review gate over the **full diff — code and docs together**.
   On an ISSUES verdict, fix findings and **return to the top of this iteration**
   — re-run `just check` + pre-commit *before* the next review, so every review
   (and the final CLEAN one) is taken over a diff whose gates currently pass.
   Bounded to the cap the review-loop harness already enforces. *Done-when:*
   `just check` green, pre-commit green, and review verdict CLEAN over the
   complete diff in the *same* iteration (scaled per `AGENTS.md`: implementation
   slices need a clean follow-up after fixes).
8. **Mark done & report.** Commit (trunk, clean message per `cmsg`
   conventions), record an evidence summary (what changed, gates passed, review
   verdict). *Done-when:* committed, gap marked complete.

The body also states the hard rules: never self-grade (review is always a fresh,
different-family context); never weaken or delete tests to pass a gate; stop and
surface to the operator on a blocked gate rather than faking completion (the
hard-gate discipline from `goal-handoff`); and **the commit gate requires the
last CLEAN review to cover the exact diff being committed** — if any fix (incl.
docs) changes files after a CLEAN verdict, re-run `just check`, pre-commit, and
the review gate before committing, so nothing is committed unreviewed.

**Operator override (escalation, not a pass).** A CLEAN different-family review
is a mandatory gate; there is no path that marks a gap "done" without one. The
*only* override is for the case where the reviewer is genuinely **unavailable**
(the other-family CLI is missing or keeps erroring after retries) — and even
then it is a **stop**: the skill halts, records the unavailability in the run
note, and surfaces to the operator. An override may **never** be used to bypass
an ISSUES verdict, and an override on its own **cannot mark the gap complete** —
only a human can decide how to proceed (wait for the reviewer, switch reviewer,
or explicitly accept the risk in writing). Autonomous progression to the next
gap is forbidden while an override is in effect.

## State on disk

Phase 1 keeps state minimal and human-visible, so Phase 2 can later consume it:

- Gap status lives in the existing parity docs (source of truth), not a new
  ledger.
- A per-run scratch note under `docs/parity-loop/runs/` (gitignored) capturing
  the selected gap, phase progress, and review verdicts — optional in Phase 1,
  but the directory and format are defined here so Phase 2's checkpointing
  reuses it.

## Privacy / capture

No change to capture policy. The skill runs under whatever capture the host
already applies; it does not store transcripts or tool payloads itself. Any
run-scratch notes are metadata-level (gap name, phase, verdict, gate results),
consistent with the metadata-first posture in `AGENTS.md`.

## Phase 2 (sketch — follow-on, not implemented now)

A thin deterministic outer-loop harness (same family as `pi-review-loop`) that
*repeats the Phase 1 skill* across many gaps for unattended runs. It owns only
the things a model-driven loop does badly over hours:

- A checkable feature-list derived from the gap audit (done-conditions per
  gap).
- Fresh context per gap (re-invoke the agent CLI rather than one long session) —
  the research consensus for avoiding context-rot/drift on brownfield work.
- Hard gates between gaps (`just check`, pre-commit, review CLEAN) before any
  commit.
- Budget + wall-clock caps and checkpoint/resume to `docs/parity-loop/runs/`.
- Bounded, schedulable runs (e.g. nightly cron) rather than one unbroken
  multi-hour loop, to avoid "overbaking."

Phase 2 is deferred until the Phase 1 recipe is proven stable across at least
two agents.

## Phase 3 (sketch — follow-on, not implemented now)

Close the learning loop. Today's recorded sessions are lifecycle *metadata*
only (no transcripts), so there is nothing to learn from yet. Phase 3 would add
a Reflexion-style post-run reflection pass that distills a few terse lessons
into a curated `LESSONS.md` (+ per-skill notes) that the next run loads —
explicitly *not* autonomous self-modification of the harness. Requires first
deciding whether to capture richer (non-metadata) transcripts for the reflection
input, which is a policy change and out of scope here.

## Testing strategy (Phase 1)

- **Wrapper-drift test:** assert each per-agent wrapper exists, has valid
  frontmatter, references `docs/parity-loop/skill-body.md`, and stays under a
  size cap (no duplicated workflow body).
- **Discovery smoke:** assert pipy's skill discovery finds
  `.pipy/skills/pipy-parity-loop.md` and that `AGENTS.md` contains the parity
  section pointer.
- **Body lint:** the canonical body names every gate (`just check`, pre-commit,
  different-family review CLEAN) and the gap-source docs, and contains no
  unresolved placeholders.
- **Manual dry-run:** drive one real, small gap end to end from at least one
  agent (Claude Code) and confirm the gates fire and a CLEAN verdict is required
  before commit. (No automated end-to-end test in Phase 1.)

## Open questions

1. **Canonical body location.** `docs/parity-loop/skill-body.md` (neutral,
   proposed) vs making the Claude `SKILL.md` the source and others point to it.
   Proposed: neutral file, so no single agent's dir is privileged.
2. **Reviewer auto-selection vs explicit.** Should the body auto-detect host
   family and pick the reviewer, or require the operator to pass it? Proposed:
   auto-detect with an override argument.

## Risks

- **Brownfield loop risk.** Parity work is brownfield; the research is explicit
  that free-running loops fail here without strong gates. Mitigation: Phase 1 is
  one-gap-per-invocation with hard gates; the loop (Phase 2) is deliberately
  deferred until the recipe is proven.
- **Wrapper drift.** Four entry points could diverge. Mitigation: canonical body
  + drift test.
- **Reviewer availability.** The different-family review depends on the other
  CLI being installed/working. Mitigation: when the reviewer is unavailable the
  body triggers the "Operator override" escalation — halt, record, surface to
  the operator — rather than silently skipping review or auto-completing the
  gap.
