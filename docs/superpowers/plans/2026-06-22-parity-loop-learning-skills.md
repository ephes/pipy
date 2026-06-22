# Parity Loop Learning Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the learning loop's *skill* layer — make the parity loop reflect (capture lessons) and enforce a drain, and add a gated `parity-improve` sub-skill that materializes lessons — all consuming the shipped `scripts/parity_lessons.py` helper.

**Architecture:** Pure skill/doc wiring + structure tests (no new runtime code). Extend the canonical `docs/parity-loop/skill-body.md` with Phase 0 (load + drain backlog) and Phase 9 (reflect → append lessons), add a canonical `docs/parity-loop/improve-body.md` with thin per-agent wrappers and an `AGENTS.md` section, and pin every invariant with tests in `tests/test_parity_loop_skill.py`.

**Tech Stack:** Markdown skill files, pytest, `pipy_harness.native.skills.discover_workspace_skills`, `uv run` / `just check`, git (trunk-based on `main`).

**Scope:** This is Plan 2 of 2 for the learning-loop spec (`docs/superpowers/specs/2026-06-22-parity-loop-learning-design.md`). Plan 1 (the `scripts/parity_lessons.py` helper + ledger) already shipped (commit `0651ad1`). This plan consumes that helper; do not modify it here except if a test reveals a genuine bug (flag it, don't silently change).

**Constraints (read first):**
- Work directly on `main` (trunk-based; `AGENTS.md` forbids feature branches). No worktree/branch.
- Tests live in the existing `tests/test_parity_loop_skill.py` (extend it). Run `uv run pytest tests/test_parity_loop_skill.py -v`; full gate `just check`.
- These are *skills* (instructions agents follow), so tests pin **structure/content invariants**, not runtime behavior — exactly like the Phase 1 skill tests already in that file.
- The helper CLI (shipped) is `python3 scripts/parity_lessons.py <validate|append|list|mark> [--ledger P] [--repo P]`; `list --status open --json`, `append --json '<record-without-id/status>'`, `mark <id> applied --sha <40hex> [--signed-off-by W]`, `mark <id> rejected --reason "<why>" --signed-off-by W`.

---

## File Structure

- Modify: `docs/parity-loop/skill-body.md` — add Phase 0 (load + drain), Phase 9 (reflect), and a run-end backstop. Existing Phases 1–8 and hard rules are unchanged.
- Create: `docs/parity-loop/improve-body.md` — canonical body of the `parity-improve` sub-skill (consume open lessons → gated materialization → mark).
- Create: `.claude/skills/parity-improve/SKILL.md` — Claude wrapper (thin pointer).
- Create: `.pipy/skills/parity-improve.md` — pipy wrapper.
- Create: `.pi/skills/parity-improve.md` — Pi wrapper.
- Modify: `AGENTS.md` — add a `## Parity improve` section (Codex's surface).
- Modify: `tests/test_parity_loop_skill.py` — add body tokens for Phase 0/9, improve-body lint, improve-wrapper drift, AGENTS section, and pipy discovery of `parity-improve`.

---

## Task 1: Parity-loop body gains Phase 0 (drain) + Phase 9 (reflect)

**Files:**
- Test: `tests/test_parity_loop_skill.py`
- Modify: `docs/parity-loop/skill-body.md`

- [ ] **Step 1: Extend the body-token test to require the new content**

In `tests/test_parity_lessons` — no. In `tests/test_parity_loop_skill.py`, find the `REQUIRED_BODY_TOKENS` tuple (currently ends with `"~/src/pi-mono",`) and add the learning tokens so it reads:

```python
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
    # Learning loop (Plan 2):
    "scripts/parity_lessons.py",
    "list --status open",
    "parity-improve",
    "Reflect",
    "transcript",
    "Run-end backstop",
)
```

- [ ] **Step 2: Run the body-token test to verify it fails**

Run: `uv run pytest tests/test_parity_loop_skill.py::test_canonical_body_names_all_gates_and_gap_sources -v`
Expected: FAIL — the body is missing the new tokens.

- [ ] **Step 3: Add Phase 0 to `docs/parity-loop/skill-body.md`**

Insert this block immediately BEFORE the line `1. **Select the gap.**` (so it becomes the first numbered phase):

```markdown
0. **Load context & drain the lesson backlog.** Read this body (it now carries
   applied improvements) and run
   `python3 scripts/parity_lessons.py list --status open`. **If the open-lesson
   count is ≥ the threshold (default 5), you MUST run the `parity-improve` skill
   to drain ALL open lessons to zero before selecting a new gap** — each open
   lesson must end `applied` or `rejected`. If the count is below threshold, note
   the open lessons and proceed; the run-end backstop drains the rest.
   *Done-when:* either the count was below threshold (noted, proceeding), or it
   met the threshold and `parity-improve` drove the open count to zero.
```

- [ ] **Step 4: Add Phase 9 + the run-end backstop to `docs/parity-loop/skill-body.md`**

Insert this block immediately AFTER Phase 8 (after the line ending `*Done-when:* committed, gap marked complete.`) and BEFORE the `## Reuse` heading (the block contains a fenced `bash` example, shown here inside a 4-backtick fence):

````markdown
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
       "target_area":"skill-body"}'
   ```

   Set `agent` to the host agent actually running this loop (not a literal); the
   helper assigns the `id` and `status: open` and refuses near-duplicates.
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
````

- [ ] **Step 5: Run the body tests to verify they pass**

Run: `uv run pytest tests/test_parity_loop_skill.py -v`
Expected: PASS — `test_canonical_body_names_all_gates_and_gap_sources`, `test_canonical_body_has_no_placeholders`, and all existing parity-loop tests still pass (Phases 1–8 and `1. **Select the gap.**` are unchanged).

- [ ] **Step 6: Commit**

```bash
git add tests/test_parity_loop_skill.py docs/parity-loop/skill-body.md
git commit -m "feat(parity-loop): body Phase 0 (drain) + Phase 9 (reflect) + run-end backstop"
```

---

## Task 2: Canonical `improve-body.md` + body-lint test

**Files:**
- Test: `tests/test_parity_loop_skill.py`
- Create: `docs/parity-loop/improve-body.md`

- [ ] **Step 1: Write the failing improve-body lint test**

Append to `tests/test_parity_loop_skill.py`:

```python
IMPROVE_BODY = REPO_ROOT / "docs" / "parity-loop" / "improve-body.md"

IMPROVE_REQUIRED_TOKENS = (
    "scripts/parity_lessons.py",
    "list --status open",
    "different",          # different model family review
    "CLEAN",
    "sign-off",
    "mark",               # mark applied/rejected
    "validate",
    "materializ",         # materialization language
)


def test_improve_body_exists() -> None:
    assert IMPROVE_BODY.is_file(), f"missing improve body: {IMPROVE_BODY}"


def test_improve_body_names_required_tokens() -> None:
    text = IMPROVE_BODY.read_text(encoding="utf-8")
    missing = [tok for tok in IMPROVE_REQUIRED_TOKENS if tok not in text]
    assert not missing, f"improve body is missing required tokens: {missing}"


def test_improve_body_has_no_placeholders() -> None:
    text = IMPROVE_BODY.read_text(encoding="utf-8")
    found = [tok for tok in PLACEHOLDER_TOKENS if tok in text]
    assert not found, f"improve body contains placeholder tokens: {found}"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_parity_loop_skill.py -k improve_body -v`
Expected: FAIL — `docs/parity-loop/improve-body.md` does not exist.

- [ ] **Step 3: Create `docs/parity-loop/improve-body.md`**

Create the file with exactly this content:

````markdown
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
3. **Gate the edits (different model family).** Run `just check` + pre-commit;
   only when both are green, run the **different-family** review over the edit
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
````

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_parity_loop_skill.py -k improve_body -v`
Expected: PASS (3 improve-body tests).

- [ ] **Step 5: Commit**

```bash
git add tests/test_parity_loop_skill.py docs/parity-loop/improve-body.md
git commit -m "feat(parity-loop): canonical improve-body + body-lint tests"
```

---

## Task 3: `parity-improve` wrappers + AGENTS.md section

**Files:**
- Test: `tests/test_parity_loop_skill.py`
- Create: `.claude/skills/parity-improve/SKILL.md`
- Create: `.pipy/skills/parity-improve.md`
- Create: `.pi/skills/parity-improve.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Write the failing wrapper-drift + AGENTS tests**

Append to `tests/test_parity_loop_skill.py`:

```python
IMPROVE_WRAPPERS = (
    REPO_ROOT / ".claude" / "skills" / "parity-improve" / "SKILL.md",
    REPO_ROOT / ".pipy" / "skills" / "parity-improve.md",
    REPO_ROOT / ".pi" / "skills" / "parity-improve.md",
)
IMPROVE_BODY_REFERENCE = "docs/parity-loop/improve-body.md"


@pytest.mark.parametrize("wrapper", IMPROVE_WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_improve_wrapper_exists(wrapper: Path) -> None:
    assert wrapper.is_file(), f"missing wrapper: {wrapper}"


@pytest.mark.parametrize("wrapper", IMPROVE_WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_improve_wrapper_references_body(wrapper: Path) -> None:
    text = wrapper.read_text(encoding="utf-8")
    assert IMPROVE_BODY_REFERENCE in text, f"{wrapper} must point at {IMPROVE_BODY_REFERENCE}"


@pytest.mark.parametrize("wrapper", IMPROVE_WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_improve_wrapper_has_frontmatter_name(wrapper: Path) -> None:
    text = wrapper.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{wrapper} must start with YAML frontmatter"
    assert "name: parity-improve" in text, f"{wrapper} must declare its name"
    assert "description:" in text, f"{wrapper} must declare a description"


@pytest.mark.parametrize("wrapper", IMPROVE_WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_improve_wrapper_does_not_duplicate_body(wrapper: Path) -> None:
    assert wrapper.stat().st_size <= WRAPPER_MAX_BYTES, (
        f"{wrapper} is too large; it likely duplicates the improve body"
    )
    text = wrapper.read_text(encoding="utf-8")
    assert "1. **Read open lessons.**" not in text, (
        f"{wrapper} contains improve-body content; keep it a thin pointer"
    )


def test_agents_md_has_parity_improve_section() -> None:
    text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Parity improve" in text, "AGENTS.md must have a '## Parity improve' section"
    assert IMPROVE_BODY_REFERENCE in text, "AGENTS.md parity-improve section must point at the body"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_parity_loop_skill.py -k "improve_wrapper or agents_md_has_parity_improve" -v`
Expected: FAIL — the wrappers do not exist and the `## Parity improve` AGENTS section is missing.

- [ ] **Step 3: Create the Claude wrapper `.claude/skills/parity-improve/SKILL.md`**

```markdown
---
name: parity-improve
description: Use to consume captured parity-loop lessons and materialize them into gated edits to the skills/docs/tests/harness in this repo. Triggers: "parity improve", "drain lessons", "apply parity lessons".
---

# Parity Improve (Claude Code)

Follow the canonical workflow in `docs/parity-loop/improve-body.md` (resolve it
against the repo root and read it now). Consume the open lessons and materialize
each into a gated change. You may delegate edits to subagents, but keep the
different-family review (`pi-review-loop`) as a separate fresh context, and honor
the sign-off gate for instruction edits.
```

- [ ] **Step 4: Create the pipy wrapper `.pipy/skills/parity-improve.md`**

```markdown
---
name: parity-improve
description: Consume captured parity-loop lessons and materialize each into a gated edit (skills/docs/tests/harness) in this repo.
---

# Parity Improve (pipy)

Follow the canonical workflow in `docs/parity-loop/improve-body.md` (resolve the
relative path against the repo root and read it now). Run the steps inline. Honor
the body's hard rules, especially the different-family review (use
`opus-review-loop` when this run is GPT) and the sign-off gate for instruction
edits.
```

- [ ] **Step 5: Create the Pi wrapper `.pi/skills/parity-improve.md`**

```markdown
---
name: parity-improve
description: Consume captured parity-loop lessons and materialize each into a gated edit (skills/docs/tests/harness) in this repo.
---

# Parity Improve (Pi)

Follow the canonical workflow in `docs/parity-loop/improve-body.md` (resolve the
relative path against the repo root and read it now). Run the steps inline. Honor
the body's hard rules, especially the different-family review (use
`opus-review-loop` to review with Opus) and the sign-off gate for instruction
edits.
```

- [ ] **Step 6: Add the `## Parity improve` section to `AGENTS.md`**

Append this section to the end of `AGENTS.md`:

```markdown
## Parity improve

To consume captured parity-loop lessons and materialize them into gated edits
(skills/docs/tests/harness), follow the canonical workflow in
`docs/parity-loop/improve-body.md`. This is the `parity-improve` skill; the same
body is wrapped per-agent under `.claude/skills/`, `.pipy/skills/`, and
`.pi/skills/`. Application is gated: `just check` + a different-family review
CLEAN + human/judge sign-off for instruction edits. The ledger helper
`scripts/parity_lessons.py` refuses to mark a lesson `applied` without a real
materializing commit, so lessons cannot be closed without being acted on.
```

- [ ] **Step 7: Run to verify they pass**

Run: `uv run pytest tests/test_parity_loop_skill.py -v`
Expected: PASS — all improve-wrapper tests and the AGENTS-improve test, plus every earlier test.

- [ ] **Step 8: Commit**

```bash
git add tests/test_parity_loop_skill.py \
  .claude/skills/parity-improve/SKILL.md \
  .pipy/skills/parity-improve.md \
  .pi/skills/parity-improve.md \
  AGENTS.md
git commit -m "feat(parity-loop): parity-improve wrappers + AGENTS section + drift tests"
```

---

## Task 4: pipy discovers the `parity-improve` skill

**Files:**
- Test: `tests/test_parity_loop_skill.py`

- [ ] **Step 1: Write the failing discovery test**

Append to `tests/test_parity_loop_skill.py`:

```python
def test_pipy_discovers_parity_improve_skill() -> None:
    skills, _cap_reached = discover_workspace_skills(
        REPO_ROOT,
        config_home_env={},        # don't read the real ~/.config/pipy
        home_dir=REPO_ROOT,
        per_file_byte_cap=64 * 1024,
        total_byte_cap=256 * 1024,
    )
    found = find_skill_by_name(skills, "parity-improve")
    assert found is not None, "pipy did not discover the parity-improve skill"
    assert found.path_label == ".pipy/skills/parity-improve.md", found.path_label
```

- [ ] **Step 2: Run it (the wrapper exists from Task 3, so it should pass)**

Run: `uv run pytest tests/test_parity_loop_skill.py::test_pipy_discovers_parity_improve_skill -v`
Expected: PASS. If it FAILS on `path_label`, print the discovered skills to read the loader's exact label and correct the assertion to match the loader (the loader is the source of truth):

```bash
uv run python -c "from pathlib import Path; from pipy_harness.native.skills import discover_workspace_skills; s,_=discover_workspace_skills(Path('.'), config_home_env={}, home_dir=Path('.')); print([(x.name, x.path_label) for x in s])"
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_parity_loop_skill.py
git commit -m "test(parity-loop): pipy discovery smoke for parity-improve"
```

---

## Task 5: Full gate + finalize

**Files:** none (verification only).

- [ ] **Step 1: Run the full project gate**

Run: `just check`
Expected: ruff clean, mypy clean, all pytest tests pass — including the extended
`tests/test_parity_loop_skill.py` and the existing `tests/test_parity_lessons.py`.

- [ ] **Step 2: Fix any lint/type issues inline, then re-run `just check`**

If ruff/mypy flags the test file (e.g. import ordering), fix in place and re-run
`just check` until green. Do not weaken any assertion.

- [ ] **Step 3: Final commit if Step 2 changed anything**

```bash
git add -A
git commit -m "chore(parity-loop): satisfy lint/type gate for learning skills"
```

(If Step 2 changed nothing, skip — the work is already committed.)

---

## Notes for the executor

- This is the *skill* layer only; the `scripts/parity_lessons.py` helper already
  shipped (commit `0651ad1`) and must not be modified here.
- The parity-loop body keeps Phases 1–8 and the hard rules verbatim; you only
  ADD Phase 0, Phase 9, and the run-end backstop. Do not renumber 1–8.
- After implementation, run the different-family Pi review loop over the diff
  before the final commit, per the workflow's own gate.
