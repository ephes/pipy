# Parity Runner Skill Prerequisites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the two marker-keyed clauses the unattended runner depends on — a `skill-body.md` "runner single-gap mode" and an `improve-body.md` "runner unattended mode" — so the runner is correct from its first task.

**Architecture:** Pure skill-doc edits plus body-lint tokens. No runtime code. Each clause is keyed on an exact marker phrase the runner passes in its spawn prompt; tests pin that the markers and the deferral/scoping language are present.

**Tech Stack:** Markdown skill files, pytest (extend `tests/test_parity_loop_skill.py`), `uv run` / `just check`, git (trunk-based on `main`).

**Scope:** This is Plan A of 2 for the Phase-2 runner spec (`docs/superpowers/specs/2026-06-22-parity-runner-design.md`). Plan B (`scripts/parity_runner.py`) consumes these markers. Land this plan first.

**Constraints (read first):**
- Work directly on `main` (trunk-based; `AGENTS.md` forbids feature branches). No worktree/branch.
- These are skill instructions; tests pin structure/content invariants only.
- Run `uv run pytest tests/test_parity_loop_skill.py -v`; full gate `just check`.

---

## File Structure

- Modify: `docs/parity-loop/skill-body.md` — add a "Runner single-gap mode" clause.
- Modify: `docs/parity-loop/improve-body.md` — add a "Runner unattended mode" clause.
- Modify: `tests/test_parity_loop_skill.py` — add body-lint tokens for both markers.

---

## Task 1: `skill-body.md` runner single-gap-mode clause

**Files:**
- Test: `tests/test_parity_loop_skill.py`
- Modify: `docs/parity-loop/skill-body.md`

- [ ] **Step 1: Add the required token to the body-lint test**

In `tests/test_parity_loop_skill.py`, find the `REQUIRED_BODY_TOKENS` tuple and add the marker so it includes:

```python
    # Runner (Phase 2) single-gap mode marker:
    "runner single-gap mode",
```

(Add that line inside the existing `REQUIRED_BODY_TOKENS = ( ... )` tuple, after the learning-loop tokens.)

- [ ] **Step 2: Run the body-token test to verify it fails**

Run: `uv run pytest tests/test_parity_loop_skill.py::test_canonical_body_names_all_gates_and_gap_sources -v`
Expected: FAIL — the body does not yet contain `runner single-gap mode`.

- [ ] **Step 3: Add the clause to `docs/parity-loop/skill-body.md`**

Insert this section immediately BEFORE the `## Reuse` heading (after the run-end backstop paragraph):

```markdown
## Runner single-gap mode

When the invocation prompt contains the marker `runner single-gap mode` (the
parity-runner sets it), run only the single gap — Phases 1–8 plus the **Phase 9
capture** — and **defer Phase 0's drain-enforcement, the `parity-improve` step,
and the run-end backstop to the caller** (the parity-runner owns batch-level
lesson draining). Still capture lessons in Phase 9 as usual; do **not** apply,
drain, or reject them in this mode. Everything else (gates, different-family
review, commit) is unchanged. This exists so an unattended single-gap run never
blocks on a sign-off-needing lesson; lesson application is the runner's job.
```

- [ ] **Step 4: Run the body tests to verify they pass**

Run: `uv run pytest tests/test_parity_loop_skill.py -v`
Expected: PASS — the new token is present and all existing parity-loop tests still pass (Phases 1–8 and the existing tokens are untouched).

- [ ] **Step 5: Commit**

```bash
git add tests/test_parity_loop_skill.py docs/parity-loop/skill-body.md
git commit -m "feat(parity-loop): skill-body runner single-gap-mode clause"
```

---

## Task 2: `improve-body.md` runner unattended-mode clause

**Files:**
- Test: `tests/test_parity_loop_skill.py`
- Modify: `docs/parity-loop/improve-body.md`

- [ ] **Step 1: Add the required token to the improve-body lint test**

In `tests/test_parity_loop_skill.py`, find the `IMPROVE_REQUIRED_TOKENS` tuple and add:

```python
    "runner unattended mode",
```

(Add that line inside the existing `IMPROVE_REQUIRED_TOKENS = ( ... )` tuple.)

- [ ] **Step 2: Run the improve-body test to verify it fails**

Run: `uv run pytest tests/test_parity_loop_skill.py::test_improve_body_names_required_tokens -v`
Expected: FAIL — `improve-body.md` does not yet contain `runner unattended mode`.

- [ ] **Step 3: Add the clause to `docs/parity-loop/improve-body.md`**

Insert this section immediately BEFORE the `## Hard rules` heading:

```markdown
## Runner unattended mode

When the invocation prompt contains the marker `runner unattended mode` (the
parity-runner sets it), apply **only** lessons that are gateable without sign-off —
`target_area` ∈ {`docs`, `tests`, `harness`}, gated by a Pi-CLEAN review as usual —
and **leave every instruction-area lesson (`skill-body`/`wrapper`) and every
rejection candidate untouched and `open`**. Never block on, wait for, or fake
human/judge sign-off in this mode. The runner surfaces the remaining open lessons
to the operator afterward; do not attempt to drain them here.
```

- [ ] **Step 4: Run the improve-body tests to verify they pass**

Run: `uv run pytest tests/test_parity_loop_skill.py -v`
Expected: PASS — the new token is present and all existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_parity_loop_skill.py docs/parity-loop/improve-body.md
git commit -m "feat(parity-loop): improve-body runner unattended-mode clause"
```

---

## Task 3: Full gate

**Files:** none (verification only).

- [ ] **Step 1: Run the full project gate**

Run: `just check`
Expected: ruff clean, mypy clean, all tests pass (including the extended `tests/test_parity_loop_skill.py`).

- [ ] **Step 2: Fix any issues inline and re-run `just check` until green.**

If anything fails, fix in place; do not weaken assertions.

- [ ] **Step 3: Commit if Step 2 changed anything**

```bash
git add -A
git commit -m "chore(parity-loop): satisfy gate for runner skill-mode clauses"
```

(If Step 2 changed nothing, skip.)

---

## Notes for the executor

- Markers are exact phrases (`runner single-gap mode`, `runner unattended mode`); the runner in Plan B passes these verbatim in its spawn prompts, so do not reword them.
- These clauses only take effect when the marker is present; a hand-run gap (no marker) behaves exactly as before. Do not change any existing phase.
- After implementation, run the different-family review over the diff before the final commit, per the workflow gate.
