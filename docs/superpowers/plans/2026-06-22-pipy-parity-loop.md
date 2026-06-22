# Pipy Parity Loop Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Encode the repeated Pi-parity workflow as one canonical, in-repo skill body with thin per-agent wrappers (Claude Code, Codex, Pi, pipy), guarded by tests.

**Architecture:** A single canonical workflow body lives at `docs/parity-loop/skill-body.md`. Each agent gets a thin wrapper (frontmatter + a pointer to the body) in its own native discovery location. Three pytest tests pin the invariants: the body names every gate and gap-source, wrappers reference the body without duplicating it, and pipy's discovery + Codex's `AGENTS.md` surface the skill.

**Tech Stack:** Markdown skill files, pytest, `pipy_harness.native.skills.discover_workspace_skills`, `uv run` / `just check` (ruff + mypy + pytest), git (trunk-based on `main`).

**Important constraints (read first):**
- Work **directly on `main`** (trunk-based; `AGENTS.md` forbids feature branches for routine work). Do NOT create a worktree/branch.
- `.pipy/` is **entirely gitignored** (`.gitignore:1`). The pipy wrapper must be made trackable via a gitignore negation (Task 1).
- Tests run via `uv run pytest`; full gate is `just check` (= `lint typecheck test`).
- The deliverable is docs + tests, not runtime code. The skill is *instructions a human/agent follows*, so the tests assert structure/content invariants, not behavior of a function.

---

## File Structure

- Create: `docs/parity-loop/skill-body.md` — the canonical workflow (single source of truth).
- Create: `docs/parity-loop/runs/.gitkeep` — placeholder so the (gitignored) run-note dir exists.
- Create: `.claude/skills/pipy-parity-loop/SKILL.md` — Claude Code wrapper.
- Create: `.pipy/skills/pipy-parity-loop.md` — pipy native wrapper.
- Create: `.pi/skills/pipy-parity-loop.md` — Pi wrapper.
- Modify: `AGENTS.md` — add a "Parity loop" section pointing Codex (and all agents) at the body.
- Modify: `.gitignore` — un-ignore `.pipy/skills/`; ignore `docs/parity-loop/runs/`.
- Create: `tests/test_parity_loop_skill.py` — body-lint, wrapper-drift, discovery-smoke tests.

---

## Task 1: Scaffolding — directories and gitignore

**Files:**
- Create: `docs/parity-loop/runs/.gitkeep`
- Modify: `.gitignore`

- [ ] **Step 1: Create the docs dir and run-note placeholder**

```bash
mkdir -p docs/parity-loop/runs
printf '' > docs/parity-loop/runs/.gitkeep
```

- [ ] **Step 2: Edit `.gitignore` to track `.pipy/skills/` but keep ignoring run notes**

Replace the first line `.pipy/` with the block below (un-ignoring a path inside an ignored dir requires re-including the dir's entries, then negating the subdir):

```gitignore
.pipy/*
!.pipy/skills/
docs/parity-loop/runs/
!docs/parity-loop/runs/.gitkeep
```

(Keep every other existing `.gitignore` line unchanged. The `.pipy/*` form still ignores `smoke-status*` capture dirs while letting `.pipy/skills/` be tracked.)

- [ ] **Step 3: Verify the gitignore behaves as intended**

Run:
```bash
mkdir -p .pipy/skills && touch .pipy/skills/.probe
git check-ignore -v .pipy/skills/.probe; echo "skills ignored? exit=$?"
git check-ignore -v .pipy/smoke-status 2>/dev/null; echo "smoke-status ignored? exit=$?"
git check-ignore -v docs/parity-loop/runs/scratch.md; echo "runs ignored? exit=$?"
rm .pipy/skills/.probe
```
Expected: `.pipy/skills/.probe` is **NOT** ignored (first `git check-ignore` prints nothing, `exit=1`); `.pipy/smoke-status` **IS** ignored (`exit=0`); `docs/parity-loop/runs/scratch.md` **IS** ignored (`exit=0`).

- [ ] **Step 4: Commit**

```bash
git add .gitignore docs/parity-loop/runs/.gitkeep
git commit -m "chore(parity-loop): scaffold dirs; track .pipy/skills, ignore run notes"
```

---

## Task 2: Canonical workflow body + body-lint test

**Files:**
- Test: `tests/test_parity_loop_skill.py`
- Create: `docs/parity-loop/skill-body.md`

- [ ] **Step 1: Write the failing body-lint tests**

Create `tests/test_parity_loop_skill.py`:

```python
"""Structure/content invariants for the pipy-parity-loop skill.

The parity loop is a skill (instructions an agent follows), so these
tests pin the *shape* of the canonical body and its per-agent wrappers
rather than any runtime behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BODY = REPO_ROOT / "docs" / "parity-loop" / "skill-body.md"

# Tokens the canonical body MUST name so the gates/gap-sources stay explicit.
REQUIRED_BODY_TOKENS = (
    "just check",
    "pre-commit",
    "docs/pi-mono-gap-audit.md",
    "docs/parity-plan.md",
    "docs/backlog.md",
    "different model family",
    "CLEAN",
    "Operator override",
    "~/src/pi-mono",
)

PLACEHOLDER_TOKENS = ("TODO", "TBD", "FIXME", "XXX", "<placeholder>")


def test_canonical_body_exists() -> None:
    assert BODY.is_file(), f"missing canonical body: {BODY}"


def test_canonical_body_names_all_gates_and_gap_sources() -> None:
    text = BODY.read_text(encoding="utf-8")
    missing = [tok for tok in REQUIRED_BODY_TOKENS if tok not in text]
    assert not missing, f"canonical body is missing required tokens: {missing}"


def test_canonical_body_has_no_placeholders() -> None:
    text = BODY.read_text(encoding="utf-8")
    found = [tok for tok in PLACEHOLDER_TOKENS if tok in text]
    assert not found, f"canonical body contains placeholder tokens: {found}"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_parity_loop_skill.py -v`
Expected: FAIL — `test_canonical_body_exists` (and the others) fail because `docs/parity-loop/skill-body.md` does not exist yet.

- [ ] **Step 3: Write the canonical body**

Create `docs/parity-loop/skill-body.md` with exactly this content:

````markdown
# Pipy Parity Loop — Workflow

This is the canonical body of the `pipy-parity-loop` skill. Each agent's
wrapper points here; do not duplicate this content into the wrappers.

Drive **one parity gap end to end**. Do not advance to another gap in a single
invocation — that outer loop is deferred (Phase 2). Reference checkout:
`~/src/pi-mono`. Work on trunk (`main`); do not create feature branches.

## Hard rules (read before starting)

- **Never self-grade.** Every review is a fresh, *different model family* context
  (implementer Opus → review with Pi/GPT; implementer GPT → review with Opus).
- **Never weaken or delete tests** to pass a gate.
- **The commit gate requires the last CLEAN review to cover the exact diff being
  committed.** If any fix (including docs) changes files after a CLEAN verdict,
  re-run `just check`, pre-commit, and the review gate before committing.
- **Operator override is an escalation, not a pass.** A CLEAN different-family
  review is mandatory; nothing marks a gap "done" without one. The only override
  is when the reviewer CLI is genuinely *unavailable* after retries — and then
  you **stop**, record it in the run note, and surface to the operator. An
  override may never bypass an ISSUES verdict and may never mark a gap complete
  on its own.

## Phases

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
     `handoff-review` prompt pointing the reviewer at the plan file path.
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
   `just check` + pre-commit, and only when both are green run the
   different-family review over the **full diff — code and docs together**. On an
   ISSUES verdict, fix and **return to the top of this iteration** (re-run `just
   check` + pre-commit before the next review), so every review — including the
   final CLEAN one — is taken over a diff whose gates currently pass.
   *Done-when:* `just check` green, pre-commit green, and review CLEAN over the
   complete diff in the *same* iteration.
8. **Mark done & report.** Commit (trunk; clean message, no self-reference).
   Record an evidence summary: what changed, gates passed, review verdict.
   *Done-when:* committed, gap marked complete.

## Reuse

- Plan/review/impl framing: the `goal-handoff`, `handoff-impl`, `handoff-review`
  skills.
- The different-family review gate: `pi-review-loop` (review with Pi/GPT) or
  `opus-review-loop` (review with Opus).
````

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_parity_loop_skill.py -v`
Expected: PASS (3 tests: exists, names-tokens, no-placeholders).

- [ ] **Step 5: Commit**

```bash
git add tests/test_parity_loop_skill.py docs/parity-loop/skill-body.md
git commit -m "feat(parity-loop): canonical workflow body + body-lint tests"
```

---

## Task 3: Per-agent wrappers + AGENTS.md + wrapper-drift test

**Files:**
- Test: `tests/test_parity_loop_skill.py` (append)
- Create: `.claude/skills/pipy-parity-loop/SKILL.md`
- Create: `.pipy/skills/pipy-parity-loop.md`
- Create: `.pi/skills/pipy-parity-loop.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Write the failing wrapper-drift test**

Append to `tests/test_parity_loop_skill.py`:

```python
WRAPPERS = (
    REPO_ROOT / ".claude" / "skills" / "pipy-parity-loop" / "SKILL.md",
    REPO_ROOT / ".pipy" / "skills" / "pipy-parity-loop.md",
    REPO_ROOT / ".pi" / "skills" / "pipy-parity-loop.md",
)

# Wrappers are thin pointers; cap keeps the workflow body from being duplicated.
WRAPPER_MAX_BYTES = 1500
BODY_REFERENCE = "docs/parity-loop/skill-body.md"


@pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_wrapper_exists(wrapper: Path) -> None:
    assert wrapper.is_file(), f"missing wrapper: {wrapper}"


@pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_wrapper_references_canonical_body(wrapper: Path) -> None:
    text = wrapper.read_text(encoding="utf-8")
    assert BODY_REFERENCE in text, f"{wrapper} must point at {BODY_REFERENCE}"


@pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_wrapper_has_frontmatter_name(wrapper: Path) -> None:
    text = wrapper.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{wrapper} must start with YAML frontmatter"
    assert "name: pipy-parity-loop" in text, f"{wrapper} must declare its name"
    assert "description:" in text, f"{wrapper} must declare a description"


@pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_wrapper_does_not_duplicate_body(wrapper: Path) -> None:
    size = wrapper.stat().st_size
    assert size <= WRAPPER_MAX_BYTES, (
        f"{wrapper} is {size} bytes (> {WRAPPER_MAX_BYTES}); it likely duplicates "
        "the workflow body instead of pointing at it"
    )
    # The numbered phase list belongs only in the canonical body.
    text = wrapper.read_text(encoding="utf-8")
    assert "1. **Select the gap.**" not in text, (
        f"{wrapper} contains workflow body content; keep it a thin pointer"
    )


def test_agents_md_has_parity_section() -> None:
    text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Parity loop" in text, "AGENTS.md must have a '## Parity loop' section"
    assert BODY_REFERENCE in text, "AGENTS.md parity section must point at the body"
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_parity_loop_skill.py -v`
Expected: FAIL — the four `test_wrapper_*` parametrizations fail (wrappers missing) and `test_agents_md_has_parity_section` fails (no section yet).

- [ ] **Step 3: Create the Claude Code wrapper**

Create `.claude/skills/pipy-parity-loop/SKILL.md`:

```markdown
---
name: pipy-parity-loop
description: Use when driving one pi-mono parity gap in this repo end to end (select gap → plan → different-family plan review → impl plan → implement → docs → code-review loop until CLEAN → commit). Triggers: "parity loop", "next parity gap", "close a pi-mono gap".
---

# Pipy Parity Loop (Claude Code)

Follow the canonical workflow in `docs/parity-loop/skill-body.md` (resolve it
against the repo root and read it now). Drive exactly one parity gap end to end.

Claude-specific notes:
- You may delegate phases (plan, implement, docs) to subagents; keep the
  different-family review gate (`pi-review-loop`) as a separate fresh context.
- Honor the hard rules in the body: never self-grade, gates re-run after every
  fix, operator override is a stop — not a pass.
```

- [ ] **Step 4: Create the pipy native wrapper**

Create `.pipy/skills/pipy-parity-loop.md`:

```markdown
---
name: pipy-parity-loop
description: Drive one pi-mono parity gap end to end in this repo (select → plan → different-family review → implement → docs → review until CLEAN → commit).
---

# Pipy Parity Loop (pipy)

Follow the canonical workflow in `docs/parity-loop/skill-body.md` (resolve the
relative path against the repo root and read it now). Drive exactly one parity
gap end to end. Run phases inline. Honor the body's hard rules, especially the
mandatory different-family review (use `opus-review-loop` when this run is GPT).
```

- [ ] **Step 5: Create the Pi wrapper**

Create `.pi/skills/pipy-parity-loop.md`:

```markdown
---
name: pipy-parity-loop
description: Drive one pi-mono parity gap end to end in this repo (select → plan → different-family review → implement → docs → review until CLEAN → commit).
---

# Pipy Parity Loop (Pi)

Follow the canonical workflow in `docs/parity-loop/skill-body.md` (resolve the
relative path against the repo root and read it now). Drive exactly one parity
gap end to end. Run phases inline. Honor the body's hard rules, especially the
mandatory different-family review (use `opus-review-loop` to review with Opus).
```

- [ ] **Step 6: Add the Codex-facing section to `AGENTS.md`**

Append this section to the end of `AGENTS.md`:

```markdown
## Parity loop

To drive one pi-mono parity gap end to end (select gap → plan → different-family
plan review → implementation plan → implement → docs → code-review loop until
CLEAN → commit), follow the canonical workflow in
`docs/parity-loop/skill-body.md`. This is the `pipy-parity-loop` skill; the same
body is wrapped per-agent under `.claude/skills/`, `.pipy/skills/`, and
`.pi/skills/`. Drive exactly one gap per invocation (the unattended outer loop is
deferred). Honor the body's hard rules: never self-grade, the review gate is a
mandatory different-family CLEAN, and an operator override is an escalation/stop —
never a pass.
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_parity_loop_skill.py -v`
Expected: PASS (body-lint tests from Task 2 plus all wrapper/AGENTS.md tests).

- [ ] **Step 8: Commit**

```bash
git add tests/test_parity_loop_skill.py \
  .claude/skills/pipy-parity-loop/SKILL.md \
  .pipy/skills/pipy-parity-loop.md \
  .pi/skills/pipy-parity-loop.md \
  AGENTS.md
git commit -m "feat(parity-loop): per-agent wrappers + AGENTS.md section + drift tests"
```

---

## Task 4: pipy discovery smoke test

**Files:**
- Test: `tests/test_parity_loop_skill.py` (append)

- [ ] **Step 1: Write the failing discovery test**

Append to `tests/test_parity_loop_skill.py`:

```python
from pipy_harness.native.skills import (  # noqa: E402  (grouped with skill tests)
    discover_workspace_skills,
    find_skill_by_name,
)


def test_pipy_discovers_parity_loop_skill() -> None:
    skills, _cap_reached = discover_workspace_skills(
        REPO_ROOT,
        config_home_env={},        # don't read the real ~/.config/pipy
        home_dir=REPO_ROOT,
        per_file_byte_cap=64 * 1024,
        total_byte_cap=256 * 1024,
    )
    found = find_skill_by_name(skills, "pipy-parity-loop")
    assert found is not None, "pipy did not discover the pipy-parity-loop skill"
    assert found.path_label == ".pipy/skills/pipy-parity-loop.md", found.path_label
```

- [ ] **Step 2: Run it to verify it passes (the wrapper already exists from Task 3)**

Run: `uv run pytest tests/test_parity_loop_skill.py::test_pipy_discovers_parity_loop_skill -v`
Expected: PASS. If it FAILS on `path_label`, print the discovered skills to read the exact label the loader produces:

```bash
uv run python -c "from pathlib import Path; from pipy_harness.native.skills import discover_workspace_skills; s,_=discover_workspace_skills(Path('.'), config_home_env={}, home_dir=Path('.')); print([ (x.name, x.path_label) for x in s ])"
```

Then correct the asserted `path_label` to match the loader's actual output (the loader is the source of truth) and re-run.

- [ ] **Step 3: Commit**

```bash
git add tests/test_parity_loop_skill.py
git commit -m "test(parity-loop): pipy discovery smoke for the parity-loop skill"
```

---

## Task 5: Full gate + finalize

**Files:** none (verification only).

- [ ] **Step 1: Run the full project gate**

Run: `just check`
Expected: ruff clean, mypy clean, all pytest tests pass (including the new
`tests/test_parity_loop_skill.py`).

- [ ] **Step 2: Fix any lint/type issues inline**

If ruff/mypy flag the new test file, fix in place (e.g. import ordering). Re-run
`just check` until green. Do not weaken assertions to pass.

- [ ] **Step 3: Final commit if Step 2 changed anything**

```bash
git add -A
git commit -m "chore(parity-loop): satisfy lint/type gate for parity-loop skill"
```

(If Step 2 changed nothing, skip — the work is already committed.)

---

## Notes for the executor

- This plan ships the **skill** (Phase 1 of the spec). The unattended outer-loop
  harness (Phase 2) and session-learning/reflection (Phase 3) are out of scope
  and described in `docs/superpowers/specs/2026-06-22-pipy-parity-loop-design.md`.
- The two open questions in the spec do not block this plan: the canonical-body
  location is decided here (`docs/parity-loop/skill-body.md`), and reviewer
  selection is left to the operator/agent at run time (the body names both
  `pi-review-loop` and `opus-review-loop` and the family rule).
- After implementation, this very change is a good first exercise of the skill's
  own review gate: run the different-family review (`pi-review-loop`) over the
  diff before the final commit, per the workflow body.
