# Parity Lessons Helper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `scripts/parity_lessons.py` — the tested, git-aware ledger helper that owns all deterministic operations on `docs/parity-loop/lessons/lessons.jsonl` (validate / append / list / mark).

**Architecture:** A single self-contained Python module (CLI + importable library, stdlib only) over a one-record-per-line JSONL ledger. Judgment stays with agents; this module enforces structure and *materialization* — an `applied` lesson must point at a real HEAD-ancestor commit that names the lesson and changed the right non-ledger artifact; a `rejected` lesson must carry a reason and sign-off. Git facts come from `subprocess` calls to `git` in the repo root.

**Tech Stack:** Python 3.11 stdlib (`json`, `argparse`, `subprocess`, `os`, `re`, `pathlib`), pytest, git, `uv run` / `just check` (ruff + mypy + pytest).

**Scope:** This is Plan 1 of 2 for the learning-loop spec (`docs/superpowers/specs/2026-06-22-parity-loop-learning-design.md`). Plan 2 (the reflect/improve skills + parity-loop body Phase 0/9 + AGENTS.md section) consumes this helper and is written separately. Do NOT implement the skills here.

**Constraints (read first):**
- Work directly on `main` (trunk-based; `AGENTS.md` forbids feature branches). No worktree/branch.
- The module lives at `scripts/parity_lessons.py`. ruff lints it (`uv run ruff check .`); mypy does NOT (`mypy src tests` only), but the **test file is mypy-checked**, so keep `tests/test_parity_lessons.py` type-clean.
- Tests import the script by file path via `importlib` (scripts/ is not a package).
- No reliance on `Date.now()`-style ambient state in tests: `append` takes injected `today` and `rand` parameters; the CLI fills them from `date.today()` and `os.urandom`.
- Run tests with `uv run pytest tests/test_parity_lessons.py -v`. Full gate: `just check`.

---

## File Structure

- Create: `docs/parity-loop/lessons/lessons.jsonl` — the ledger, starts empty, git-tracked.
- Create: `scripts/parity_lessons.py` — CLI + library. One module, these public functions:
  - `load_lessons(path) -> list[dict]`
  - `append_lesson(path, record, *, today, rand) -> str`
  - `list_lessons(path, status=None) -> list[dict]`
  - `mark_applied(path, lesson_id, sha, repo_root, signed_off_by=None) -> None`
  - `mark_rejected(path, lesson_id, reason, signed_off_by) -> None`
  - `validate(path, repo_root) -> list[str]` (returns error strings; empty = valid)
  - `main(argv=None) -> int` (argparse CLI)
  - plus private helpers `_norm`, `_matches_target_area`, `_is_ledger_or_scratch`, `_git`.
- Create: `tests/test_parity_lessons.py` — unit tests, including a tmp-git-repo fixture for materialization.

### Constants (defined in Task 1, referenced everywhere)

```python
STATUSES = ("open", "applied", "rejected")
TRIGGERS = ("recurring-review-finding", "gate-failure", "wrong-turn", "better-approach")
TARGET_AREAS = ("skill-body", "wrapper", "docs", "harness", "tests")
AGENTS = ("claude", "codex", "pi", "pipy")
INSTRUCTION_AREAS = ("skill-body", "wrapper")
REQUIRED_FIELDS = ("id", "date", "skill", "gap", "agent", "trigger", "lesson", "target_area", "status")
ID_RE = r"^\d{4}-\d{2}-\d{2}-[0-9a-f]{6}\Z"
SHA_RE = r"^[0-9a-f]{40}\Z"
INSTRUCTION_BODIES = ("docs/parity-loop/skill-body.md", "docs/parity-loop/improve-body.md")
LEDGER_PREFIXES = ("docs/parity-loop/lessons/", "docs/parity-loop/runs/")
```

---

## Task 1: Scaffold module, ledger, and test harness

**Files:**
- Create: `docs/parity-loop/lessons/lessons.jsonl`
- Create: `scripts/parity_lessons.py`
- Create: `tests/test_parity_lessons.py`

- [ ] **Step 1: Create the empty tracked ledger**

```bash
mkdir -p docs/parity-loop/lessons
: > docs/parity-loop/lessons/lessons.jsonl
git check-ignore docs/parity-loop/lessons/lessons.jsonl; echo "ignored? exit=$? (expect 1 = tracked)"
```

- [ ] **Step 2: Create `scripts/parity_lessons.py` with header, constants, and `load_lessons`**

```python
#!/usr/bin/env python3
"""Deterministic operations on the parity-loop lesson ledger.

CLI + importable library. The ledger is `docs/parity-loop/lessons/lessons.jsonl`:
one JSON object per line, exactly one line per lesson `id`. Judgment (what is a
lesson, what edit fixes it) stays with agents; this module enforces structure and
materialization. See docs/superpowers/specs/2026-06-22-parity-loop-learning-design.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import date
from pathlib import Path

STATUSES = ("open", "applied", "rejected")
TRIGGERS = ("recurring-review-finding", "gate-failure", "wrong-turn", "better-approach")
TARGET_AREAS = ("skill-body", "wrapper", "docs", "harness", "tests")
AGENTS = ("claude", "codex", "pi", "pipy")
INSTRUCTION_AREAS = ("skill-body", "wrapper")
REQUIRED_FIELDS = ("id", "date", "skill", "gap", "agent", "trigger", "lesson", "target_area", "status")
ID_RE = r"^\d{4}-\d{2}-\d{2}-[0-9a-f]{6}\Z"
SHA_RE = r"^[0-9a-f]{40}\Z"
INSTRUCTION_BODIES = ("docs/parity-loop/skill-body.md", "docs/parity-loop/improve-body.md")
LEDGER_PREFIXES = ("docs/parity-loop/lessons/", "docs/parity-loop/runs/")


def load_lessons(path):
    """Return the list of lesson records. Missing/empty file -> []."""
    p = Path(path)
    if not p.exists():
        return []
    records = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def _write_lessons(path, records):
    """Atomically rewrite the ledger as one compact JSON object per line."""
    p = Path(path)
    body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(p)
```

- [ ] **Step 3: Create `tests/test_parity_lessons.py` with the import harness + a load test**

```python
"""Unit tests for the parity-loop lesson ledger helper."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

# All imports live at the top from the start: later tasks add subprocess/json-using
# helpers, and ruff (run in `just check`, Task 8) rejects mid-file imports (E402).
# There is no per-commit ruff gate (no .pre-commit-config.yaml), so the unused
# imports in the early-task commits are harmless and become used by Task 5+.

_MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "parity_lessons.py"
_spec = importlib.util.spec_from_file_location("parity_lessons", _MOD_PATH)
assert _spec is not None and _spec.loader is not None
parity_lessons: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(parity_lessons)


def test_load_missing_and_empty(tmp_path: Path) -> None:
    missing = tmp_path / "nope.jsonl"
    assert parity_lessons.load_lessons(missing) == []
    empty = tmp_path / "lessons.jsonl"
    empty.write_text("", encoding="utf-8")
    assert parity_lessons.load_lessons(empty) == []
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_parity_lessons.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add docs/parity-loop/lessons/lessons.jsonl scripts/parity_lessons.py tests/test_parity_lessons.py
git commit -m "feat(parity-lessons): scaffold ledger helper module + empty ledger"
```

---

## Task 2: `append_lesson` (id assignment, status open, dedup)

**Files:**
- Modify: `scripts/parity_lessons.py`
- Modify: `tests/test_parity_lessons.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parity_lessons.py`:

```python
def _base_record(**over: Any) -> dict[str, Any]:
    rec = {
        "skill": "pipy-parity-loop",
        "gap": "demo-gap",
        "agent": "claude",
        "trigger": "gate-failure",
        "lesson": "Run just check before every review.",
        "target_area": "skill-body",
    }
    rec.update(over)
    return rec


def test_append_assigns_id_and_open_status(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    new_id = parity_lessons.append_lesson(
        ledger, _base_record(), today="2026-06-22", rand="a3f9c1"
    )
    assert new_id == "2026-06-22-a3f9c1"
    rows = parity_lessons.load_lessons(ledger)
    assert len(rows) == 1
    assert rows[0]["id"] == "2026-06-22-a3f9c1"
    assert rows[0]["status"] == "open"
    assert rows[0]["date"] == "2026-06-22"


def test_append_refuses_near_duplicate(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    parity_lessons.append_lesson(ledger, _base_record(), today="2026-06-22", rand="aaaaaa")
    # Same skill+target_area+normalized lesson (case/whitespace folded) -> duplicate.
    dup = _base_record(lesson="  RUN   just check  before every review.  ")
    try:
        parity_lessons.append_lesson(ledger, dup, today="2026-06-22", rand="bbbbbb")
        raise AssertionError("expected a duplicate to be refused")
    except ValueError as exc:
        assert "duplicate" in str(exc).lower()
    assert len(parity_lessons.load_lessons(ledger)) == 1


def test_append_rejects_id_or_status_in_input(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    for bad in (_base_record(id="x"), _base_record(status="open")):
        try:
            parity_lessons.append_lesson(ledger, bad, today="2026-06-22", rand="aaaaaa")
            raise AssertionError("must reject id/status in input")
        except ValueError:
            pass
    assert parity_lessons.load_lessons(ledger) == []


def test_append_rejects_missing_field_and_bad_enum(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    no_gap = _base_record()
    del no_gap["gap"]
    for bad in (no_gap, _base_record(trigger="nope"), _base_record(agent="bogus")):
        try:
            parity_lessons.append_lesson(ledger, bad, today="2026-06-22", rand="aaaaaa")
            raise AssertionError("must reject invalid record")
        except ValueError:
            pass


def test_append_rejects_id_collision(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    parity_lessons.append_lesson(ledger, _base_record(), today="2026-06-22", rand="aaaaaa")
    try:
        parity_lessons.append_lesson(
            ledger, _base_record(lesson="different"), today="2026-06-22", rand="aaaaaa"
        )
        raise AssertionError("same generated id must collide")
    except ValueError as exc:
        assert "collision" in str(exc).lower()


def test_append_rejects_malformed_generated_id(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    try:
        parity_lessons.append_lesson(ledger, _base_record(), today="2026/06/22", rand="zz")
        raise AssertionError("malformed today/rand must be refused")
    except ValueError as exc:
        assert "match" in str(exc).lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_parity_lessons.py -v`
Expected: FAIL with `AttributeError: module 'parity_lessons' has no attribute 'append_lesson'`.

- [ ] **Step 3: Implement `_norm` and `append_lesson`**

Append to `scripts/parity_lessons.py`:

```python
def _norm(text):
    """Lowercase + collapse whitespace, for duplicate detection."""
    return " ".join(text.lower().split())


CALLER_REQUIRED = ("skill", "gap", "agent", "trigger", "lesson", "target_area")


def append_lesson(path, record, *, today, rand):
    """Append a new lesson; assign id = f"{today}-{rand}", date, and status 'open'.

    `record` must NOT contain id/status, must include every field in
    CALLER_REQUIRED (non-empty) with valid enum values. Raises ValueError on a
    precondition violation, missing field, bad enum, generated-id collision, or a
    near-duplicate (same skill + target_area + normalized lesson). This guarantees
    a freshly appended ledger still passes validate().
    """
    if "id" in record or "status" in record:
        raise ValueError("record must not contain 'id' or 'status'")
    for field in CALLER_REQUIRED:
        if not record.get(field):
            raise ValueError(f"record missing required field '{field}'")
    if record["agent"] not in AGENTS:
        raise ValueError(f"agent not in {AGENTS}")
    if record["trigger"] not in TRIGGERS:
        raise ValueError(f"trigger not in {TRIGGERS}")
    if record["target_area"] not in TARGET_AREAS:
        raise ValueError(f"target_area not in {TARGET_AREAS}")
    existing = load_lessons(path)
    new_id = f"{today}-{rand}"
    if not re.match(ID_RE, new_id):
        raise ValueError(f"generated id {new_id!r} does not match {ID_RE}")
    if any(r.get("id") == new_id for r in existing):
        raise ValueError(f"generated id collision: {new_id}")
    key = (record["skill"], record["target_area"], _norm(record["lesson"]))
    for r in existing:
        if (r.get("skill"), r.get("target_area"), _norm(r.get("lesson", ""))) == key:
            raise ValueError(f"duplicate lesson for {key[0]}/{key[1]}")
    new = dict(record)
    new["id"] = new_id
    new["date"] = today
    new["status"] = "open"
    existing.append(new)
    _write_lessons(path, existing)
    return new["id"]
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_parity_lessons.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add scripts/parity_lessons.py tests/test_parity_lessons.py
git commit -m "feat(parity-lessons): append with id assignment and dedup"
```

---

## Task 3: `list_lessons`

**Files:**
- Modify: `scripts/parity_lessons.py`
- Modify: `tests/test_parity_lessons.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_parity_lessons.py`:

```python
def test_list_filters_by_status(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    parity_lessons.append_lesson(ledger, _base_record(), today="2026-06-22", rand="aaaaaa")
    parity_lessons.append_lesson(
        ledger, _base_record(lesson="Second lesson."), today="2026-06-22", rand="bbbbbb"
    )
    assert len(parity_lessons.list_lessons(ledger)) == 2
    assert len(parity_lessons.list_lessons(ledger, status="open")) == 2
    assert parity_lessons.list_lessons(ledger, status="applied") == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_parity_lessons.py -v`
Expected: FAIL with `AttributeError: ... 'list_lessons'`.

- [ ] **Step 3: Implement `list_lessons`**

Append to `scripts/parity_lessons.py`:

```python
def list_lessons(path, status=None):
    """Return records, optionally filtered to a single status."""
    rows = load_lessons(path)
    if status is None:
        return rows
    return [r for r in rows if r.get("status") == status]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_parity_lessons.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add scripts/parity_lessons.py tests/test_parity_lessons.py
git commit -m "feat(parity-lessons): list_lessons with status filter"
```

---

## Task 4: `validate` — schema checks

**Files:**
- Modify: `scripts/parity_lessons.py`
- Modify: `tests/test_parity_lessons.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parity_lessons.py`:

```python
def _write_raw(ledger: Path, records: list[dict[str, Any]]) -> None:
    ledger.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8"
    )


def _valid_open(**over: Any) -> dict[str, Any]:
    rec = {
        "id": "2026-06-22-a3f9c1",
        "date": "2026-06-22",
        "skill": "pipy-parity-loop",
        "gap": "demo",
        "agent": "claude",
        "trigger": "gate-failure",
        "lesson": "x",
        "target_area": "docs",
        "status": "open",
    }
    rec.update(over)
    return rec


def test_validate_passes_empty(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    ledger.write_text("", encoding="utf-8")
    assert parity_lessons.validate(ledger, repo_root=tmp_path) == []


def test_validate_open_record_ok(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    _write_raw(ledger, [_valid_open()])
    assert parity_lessons.validate(ledger, repo_root=tmp_path) == []


def test_validate_catches_bad_enum_and_missing_field(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    _write_raw(ledger, [_valid_open(trigger="nonsense")])
    errs = parity_lessons.validate(ledger, repo_root=tmp_path)
    assert any("trigger" in e for e in errs)
    missing = _valid_open()
    del missing["gap"]
    _write_raw(ledger, [missing])
    errs = parity_lessons.validate(ledger, repo_root=tmp_path)
    assert any("gap" in e for e in errs)


def test_validate_catches_bad_id_and_duplicate(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    _write_raw(ledger, [_valid_open(id="BADID")])
    assert any("id" in e for e in parity_lessons.validate(ledger, repo_root=tmp_path))
    _write_raw(ledger, [_valid_open(), _valid_open(lesson="y")])  # same id twice
    assert any("duplicate" in e.lower() for e in parity_lessons.validate(ledger, repo_root=tmp_path))


def test_validate_catches_malformed_json(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    ledger.write_text("{not json}\n", encoding="utf-8")
    assert any("parse" in e.lower() or "json" in e.lower()
               for e in parity_lessons.validate(ledger, repo_root=tmp_path))


def test_validate_catches_non_object_line(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    # Valid JSON, but not an object — must be reported, not crash.
    ledger.write_text('[]\n"x"\n', encoding="utf-8")
    errs = parity_lessons.validate(ledger, repo_root=tmp_path)
    assert sum("must be a JSON object" in e for e in errs) == 2


def test_validate_handles_unhashable_id_without_crash(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    # An object whose id is an unhashable JSON value must not crash validate().
    ledger.write_text('{"id": [], "status": "open"}\n', encoding="utf-8")
    errs = parity_lessons.validate(ledger, repo_root=tmp_path)
    assert errs  # reports bad id + missing fields, does not raise
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_parity_lessons.py -v`
Expected: FAIL with `AttributeError: ... 'validate'`.

- [ ] **Step 3: Implement `validate` (schema portion only; git checks come in Task 5)**

Append to `scripts/parity_lessons.py`:

```python
def _schema_errors(records):
    """Structural errors: parse handled by caller; here check fields/enums/ids."""
    errors = []
    seen_ids = set()
    for i, rec in enumerate(records):
        rid = str(rec.get("id", f"<line {i + 1}>"))
        for field in REQUIRED_FIELDS:
            if field not in rec or rec[field] in (None, ""):
                errors.append(f"{rid}: missing required field '{field}'")
        if "id" in rec and not re.match(ID_RE, str(rec["id"])):
            errors.append(f"{rid}: id must match {ID_RE}")
        if rec.get("status") not in STATUSES:
            errors.append(f"{rid}: status not in {STATUSES}")
        if rec.get("trigger") not in TRIGGERS:
            errors.append(f"{rid}: trigger not in {TRIGGERS}")
        if rec.get("target_area") not in TARGET_AREAS:
            errors.append(f"{rid}: target_area not in {TARGET_AREAS}")
        if rec.get("agent") not in AGENTS:
            errors.append(f"{rid}: agent not in {AGENTS}")
        if "id" in rec:
            key = str(rec["id"])  # str() guards unhashable JSON ids (e.g. [])
            if key in seen_ids:
                errors.append(f"{key}: duplicate id")
            seen_ids.add(key)
    return errors


def validate(path, repo_root):
    """Return a list of human-readable error strings; empty means valid."""
    p = Path(path)
    errors = []
    records = []
    if p.exists():
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"<line {n}>: JSON parse error: {exc}")
                continue
            if not isinstance(obj, dict):
                errors.append(f"<line {n}>: each line must be a JSON object")
                continue
            records.append(obj)
    errors.extend(_schema_errors(records))
    errors.extend(_materialization_errors(records, Path(repo_root)))
    return errors


def _materialization_errors(records, repo_root):
    """Filled in Task 5. Returns [] until git-aware checks are added."""
    return []
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_parity_lessons.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add scripts/parity_lessons.py tests/test_parity_lessons.py
git commit -m "feat(parity-lessons): validate schema (fields, enums, ids, json)"
```

---

## Task 5: `validate` — materialization (git-aware) checks

**Files:**
- Modify: `scripts/parity_lessons.py`
- Modify: `tests/test_parity_lessons.py`

- [ ] **Step 1: Write the failing tests (with a tmp-git-repo fixture)**

Append to `tests/test_parity_lessons.py` (`subprocess` is already imported at the top from Task 1):

```python
def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "seed.txt")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


def _commit_file(repo: Path, rel: str, message: str) -> str:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("change\n", encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _applied(rid: str, sha: str, target_area: str, **over: Any) -> dict[str, Any]:
    rec = _valid_open(id=rid, target_area=target_area, status="applied")
    rec["resolution"] = {"sha": sha}
    rec.update(over)
    return rec


def test_validate_applied_ok(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rid = "2026-06-22-aaaaaa"
    sha = _commit_file(repo, "docs/guide.md", f"docs fix\n\nCloses lessons: {rid}")
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_applied(rid, sha, "docs")])
    assert parity_lessons.validate(ledger, repo_root=repo) == []


def test_validate_applied_bad_sha(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_applied("2026-06-22-aaaaaa", "0123456789abcdef0123456789abcdef01234567", "docs")])
    assert any("resolve" in e.lower() or "ancestor" in e.lower()
               for e in parity_lessons.validate(ledger, repo_root=repo))


def test_validate_applied_rejects_ref_sha(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rid = "2026-06-22-aaaaaa"
    # A real commit exists, but the ledger stores a mutable ref instead of a SHA.
    _commit_file(repo, "docs/guide.md", f"docs\n\nCloses lessons: {rid}")
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_applied(rid, "HEAD", "docs")])
    errs = parity_lessons.validate(ledger, repo_root=repo)
    assert any("hex" in e.lower() for e in errs)


def test_validate_applied_nonstring_sha_no_crash(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rec = _applied("2026-06-22-aaaaaa", "x", "docs")
    rec["resolution"]["sha"] = []  # not a string
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [rec])
    errs = parity_lessons.validate(ledger, repo_root=repo)  # must not raise
    assert any("string resolution.sha" in e for e in errs)


def test_validate_applied_not_ancestor(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rid = "2026-06-22-aaaaaa"
    # Commit on a side branch, then return to main so the sha is not a HEAD ancestor.
    _git(repo, "checkout", "-q", "-b", "side")
    sha = _commit_file(repo, "docs/guide.md", f"side\n\nCloses lessons: {rid}")
    _git(repo, "checkout", "-q", "-")
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_applied(rid, sha, "docs")])
    assert any("ancestor" in e.lower() for e in parity_lessons.validate(ledger, repo_root=repo))


def test_validate_applied_wrong_area(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rid = "2026-06-22-aaaaaa"
    sha = _commit_file(repo, "tests/test_x.py", f"t\n\nCloses lessons: {rid}")
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_applied(rid, sha, "docs")])  # claims docs, touched tests/
    assert any("target_area" in e or "materializ" in e.lower()
               for e in parity_lessons.validate(ledger, repo_root=repo))


def test_validate_applied_ledger_only(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rid = "2026-06-22-aaaaaa"
    sha = _commit_file(repo, "docs/parity-loop/lessons/lessons.jsonl", f"x\n\nCloses lessons: {rid}")
    ledger = repo / "other.jsonl"
    _write_raw(ledger, [_applied(rid, sha, "docs")])
    assert any("materializ" in e.lower() or "ledger" in e.lower()
               for e in parity_lessons.validate(ledger, repo_root=repo))


def test_validate_applied_message_missing_id(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rid = "2026-06-22-aaaaaa"
    sha = _commit_file(repo, "docs/guide.md", "docs fix without id reference")
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_applied(rid, sha, "docs")])
    assert any("message" in e.lower() for e in parity_lessons.validate(ledger, repo_root=repo))


def test_validate_applied_instruction_needs_signoff(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rid = "2026-06-22-aaaaaa"
    sha = _commit_file(repo, "docs/parity-loop/skill-body.md", f"edit\n\nCloses lessons: {rid}")
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_applied(rid, sha, "skill-body")])  # no signed_off_by
    assert any("sign" in e.lower() for e in parity_lessons.validate(ledger, repo_root=repo))
    signed = _applied(rid, sha, "skill-body")
    signed["resolution"]["signed_off_by"] = "jochen"
    _write_raw(ledger, [signed])
    assert parity_lessons.validate(ledger, repo_root=repo) == []


def test_validate_resolution_not_object(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rec = _valid_open(id="2026-06-22-aaaaaa", status="applied")
    rec["resolution"] = "bad"  # not a dict
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [rec])
    errs = parity_lessons.validate(ledger, repo_root=repo)  # must not crash
    assert any("resolution must be an object" in e for e in errs)


def test_validate_rejected_requires_reason_and_signoff(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    rec = _valid_open(id="2026-06-22-aaaaaa", status="rejected")
    rec["resolution"] = {"reason": "", "signed_off_by": ""}
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [rec])
    errs = parity_lessons.validate(ledger, repo_root=repo)
    assert any("reason" in e.lower() for e in errs)
    assert any("sign" in e.lower() for e in errs)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_parity_lessons.py -v`
Expected: FAIL — the materialization tests fail because `_materialization_errors` currently returns `[]`.

- [ ] **Step 3: Implement the git-aware materialization checks**

In `scripts/parity_lessons.py`, REPLACE the placeholder `_materialization_errors` (the stub from Task 4) with the real implementation, and add the helpers:

```python
def _git(repo_root, *args):
    """Run git; return CompletedProcess (never raises on non-zero)."""
    return subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True
    )


def _is_ledger_or_scratch(rel):
    return any(rel.startswith(prefix) for prefix in LEDGER_PREFIXES)


def _matches_target_area(target_area, rel):
    if _is_ledger_or_scratch(rel):
        return False
    if target_area == "skill-body":
        return rel in INSTRUCTION_BODIES
    if target_area == "wrapper":
        return (
            rel == "AGENTS.md"
            or rel.startswith(".claude/skills/")
            or rel.startswith(".pipy/skills/")
            or rel.startswith(".pi/skills/")
        )
    if target_area == "docs":
        return rel.startswith("docs/") and rel not in INSTRUCTION_BODIES
    if target_area == "tests":
        return rel.startswith("tests/")
    if target_area == "harness":
        return rel.startswith("src/") or rel.startswith("scripts/")
    return False


def _record_materialization_errors(rec, repo_root):
    """Materialization errors for ONE record (empty if valid or not applied/rejected).

    Shared by `_materialization_errors` (whole-ledger validate) and the CLI
    `mark applied` dry-run, so marking and validating enforce identical rules.
    """
    rid = str(rec.get("id", "<unknown>"))  # str() guards non-string ids
    status = rec.get("status")
    if status not in ("applied", "rejected"):
        return []
    resolution = rec.get("resolution")
    if not isinstance(resolution, dict):
        return [f"{rid}: resolution must be an object"]
    if status == "rejected":
        errors = []
        if not resolution.get("reason"):
            errors.append(f"{rid}: rejected requires non-empty resolution.reason")
        if not resolution.get("signed_off_by"):
            errors.append(f"{rid}: rejected requires resolution.signed_off_by")
        return errors
    # status == "applied"
    sha = resolution.get("sha")
    if not isinstance(sha, str) or not sha:
        return [f"{rid}: applied requires a string resolution.sha"]
    if not re.match(SHA_RE, sha):
        return [f"{rid}: resolution.sha must be a full 40-char hex commit id, not a ref"]
    if _git(repo_root, "rev-parse", "--verify", f"{sha}^{{commit}}").returncode != 0:
        return [f"{rid}: resolution.sha {sha} does not resolve to a commit"]
    if _git(repo_root, "merge-base", "--is-ancestor", sha, "HEAD").returncode != 0:
        return [f"{rid}: resolution.sha {sha} is not an ancestor of HEAD"]
    errors = []
    message = _git(repo_root, "log", "-1", "--format=%B", sha).stdout
    if rid not in message:
        errors.append(f"{rid}: commit message of {sha} does not name the lesson id")
    changed = _git(
        repo_root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha
    ).stdout.split()
    qualifying = [f for f in changed if _matches_target_area(rec.get("target_area"), f)]
    if not qualifying:
        errors.append(
            f"{rid}: commit {sha} materializes no non-ledger file for "
            f"target_area '{rec.get('target_area')}'"
        )
    if rec.get("target_area") in INSTRUCTION_AREAS and not resolution.get("signed_off_by"):
        errors.append(f"{rid}: instruction-area applied requires resolution.signed_off_by")
    return errors


def _materialization_errors(records, repo_root):
    errors = []
    for rec in records:
        errors.extend(_record_materialization_errors(rec, Path(repo_root)))
    return errors
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_parity_lessons.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add scripts/parity_lessons.py tests/test_parity_lessons.py
git commit -m "feat(parity-lessons): git-aware materialization checks in validate"
```

---

## Task 6: `mark_applied` (materialization-enforcing) + `mark_rejected`

**Files:**
- Modify: `scripts/parity_lessons.py`
- Modify: `tests/test_parity_lessons.py`

This task comes AFTER the materialization machinery (Task 5) on purpose:
`mark_applied` enforces the SAME materialization rules as `validate` (via the
shared `_record_materialization_errors`) against a `repo_root`, so the library
never writes an invalid `applied` record. The tests reuse the git fixtures
(`_init_repo`, `_commit_file`, `_applied`) introduced in Task 5.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parity_lessons.py`:

```python
def test_mark_applied_materializing_commit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    lid = parity_lessons.append_lesson(
        ledger, _base_record(target_area="docs"), today="2026-06-22", rand="cccccc"
    )
    sha = _commit_file(repo, "docs/guide.md", f"docs fix\n\nCloses lessons: {lid}")
    parity_lessons.mark_applied(ledger, lid, sha=sha, repo_root=repo)
    row = parity_lessons.list_lessons(ledger)[0]
    assert row["status"] == "applied"
    assert row["resolution"]["sha"] == sha


def test_mark_applied_rejects_nonmaterializing(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    lid = parity_lessons.append_lesson(
        ledger, _base_record(target_area="docs"), today="2026-06-22", rand="dddddd"
    )
    # 'HEAD' is a ref, not a 40-char hex sha -> refused; ledger stays open.
    try:
        parity_lessons.mark_applied(ledger, lid, sha="HEAD", repo_root=repo)
        raise AssertionError("non-materializing sha must be refused")
    except ValueError:
        pass
    assert parity_lessons.list_lessons(ledger)[0]["status"] == "open"


def test_mark_applied_instruction_area_requires_signoff(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    lid = parity_lessons.append_lesson(
        ledger, _base_record(target_area="skill-body"), today="2026-06-22", rand="eeeeee"
    )
    sha = _commit_file(
        repo, "docs/parity-loop/skill-body.md", f"edit\n\nCloses lessons: {lid}"
    )
    try:
        parity_lessons.mark_applied(ledger, lid, sha=sha, repo_root=repo)
        raise AssertionError("instruction-area apply must require sign-off")
    except ValueError as exc:
        assert "sign" in str(exc).lower()
    parity_lessons.mark_applied(ledger, lid, sha=sha, repo_root=repo, signed_off_by="jochen")
    assert parity_lessons.list_lessons(ledger)[0]["resolution"]["signed_off_by"] == "jochen"


def test_mark_unknown_id_raises(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    parity_lessons.append_lesson(ledger, _base_record(), today="2026-06-22", rand="ffffff")
    try:
        parity_lessons.mark_applied(ledger, "2026-06-22-000000", sha="0" * 40, repo_root=repo)
        raise AssertionError("unknown id must raise")
    except KeyError:
        pass


def test_mark_rejected_requires_reason_and_signoff(tmp_path: Path) -> None:
    ledger = tmp_path / "lessons.jsonl"
    lid = parity_lessons.append_lesson(ledger, _base_record(), today="2026-06-22", rand="ababab")
    try:
        parity_lessons.mark_rejected(ledger, lid, reason="", signed_off_by="judge")
        raise AssertionError("empty reason must be refused")
    except ValueError:
        pass
    parity_lessons.mark_rejected(ledger, lid, reason="Not reusable.", signed_off_by="judge")
    row = parity_lessons.list_lessons(ledger)[0]
    assert row["status"] == "rejected"
    assert row["resolution"] == {"reason": "Not reusable.", "signed_off_by": "judge"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_parity_lessons.py -v`
Expected: FAIL with `AttributeError` for `mark_applied`/`mark_rejected`/`_find`.

- [ ] **Step 3: Implement the functions**

Append to `scripts/parity_lessons.py`:

```python
def _find(records, lesson_id):
    for r in records:
        if r.get("id") == lesson_id:
            return r
    raise KeyError(lesson_id)


def mark_applied(path, lesson_id, sha, repo_root, signed_off_by=None):
    """Transition a lesson to 'applied', enforcing materialization.

    Builds the prospective record and runs the SAME check `validate` uses
    (`_record_materialization_errors`) against `repo_root`; raises ValueError if
    the result would be invalid, so the library never writes a bad applied record.
    """
    records = load_lessons(path)
    rec = _find(records, lesson_id)  # raises KeyError for unknown id
    resolution = {"sha": sha}
    if signed_off_by:
        resolution["signed_off_by"] = signed_off_by
    candidate = dict(rec)
    candidate["status"] = "applied"
    candidate["resolution"] = resolution
    errs = _record_materialization_errors(candidate, Path(repo_root))
    if errs:
        raise ValueError("; ".join(errs))
    rec["status"] = "applied"
    rec["resolution"] = resolution
    _write_lessons(path, records)


def mark_rejected(path, lesson_id, reason, signed_off_by):
    """Transition a lesson to 'rejected'. Requires reason AND sign-off (no git)."""
    if not reason:
        raise ValueError(f"{lesson_id}: rejection requires a non-empty reason")
    if not signed_off_by:
        raise ValueError(f"{lesson_id}: rejection requires signed_off_by")
    records = load_lessons(path)
    rec = _find(records, lesson_id)
    rec["status"] = "rejected"
    rec["resolution"] = {"reason": reason, "signed_off_by": signed_off_by}
    _write_lessons(path, records)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_parity_lessons.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add scripts/parity_lessons.py tests/test_parity_lessons.py
git commit -m "feat(parity-lessons): materialization-enforcing mark applied/rejected"
```

---

## Task 7: CLI entrypoint

**Files:**
- Modify: `scripts/parity_lessons.py`
- Modify: `tests/test_parity_lessons.py`

- [ ] **Step 1: Write the failing CLI smoke tests**

Append to `tests/test_parity_lessons.py`:

```python
def _run_cli(repo: Path, ledger: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(_MOD_PATH), "--ledger", str(ledger), "--repo", str(repo), *args],
        capture_output=True, text=True,
    )


def test_cli_validate_empty_exit_zero(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    ledger.write_text("", encoding="utf-8")
    result = _run_cli(repo, ledger, "validate")
    assert result.returncode == 0, result.stderr


def test_cli_validate_reports_errors_exit_one(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    ledger.write_text('{"id":"BADID","status":"open"}\n', encoding="utf-8")
    result = _run_cli(repo, ledger, "validate")
    assert result.returncode == 1
    assert "id" in (result.stdout + result.stderr)


def test_cli_list_open_json(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_valid_open()])
    result = _run_cli(repo, ledger, "list", "--status", "open", "--json")
    assert result.returncode == 0
    assert "2026-06-22-a3f9c1" in result.stdout


def test_cli_mark_applied_without_sha_exits_one(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_valid_open(target_area="docs")])
    result = _run_cli(repo, ledger, "mark", "2026-06-22-a3f9c1", "applied")
    assert result.returncode == 1
    assert "sha" in (result.stdout + result.stderr).lower()
    # The ledger must be untouched (still open), not written with a null sha.
    assert parity_lessons.list_lessons(ledger)[0]["status"] == "open"


def test_cli_mark_applied_nonmaterializing_sha_exits_one(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "lessons.jsonl"
    _write_raw(ledger, [_valid_open(target_area="docs")])
    # 'HEAD' is a ref, not a 40-char hex sha — the dry-run materialization check
    # must reject it and leave the ledger untouched.
    result = _run_cli(repo, ledger, "mark", "2026-06-22-a3f9c1", "applied", "--sha", "HEAD")
    assert result.returncode == 1
    assert parity_lessons.list_lessons(ledger)[0]["status"] == "open"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_parity_lessons.py -k cli -v`
Expected: FAIL (no CLI yet; non-zero exit / empty output).

- [ ] **Step 3: Implement `main` and the `__main__` guard**

Append to `scripts/parity_lessons.py`:

```python
DEFAULT_LEDGER = "docs/parity-loop/lessons/lessons.jsonl"


def main(argv=None):
    parser = argparse.ArgumentParser(description="parity-loop lesson ledger helper")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    parser.add_argument("--repo", default=".")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate")

    p_append = sub.add_parser("append")
    p_append.add_argument("--json", dest="record_json", required=True,
                          help="record without id/status")

    p_list = sub.add_parser("list")
    p_list.add_argument("--status", choices=STATUSES, default=None)
    p_list.add_argument("--json", action="store_true")

    p_mark = sub.add_parser("mark")
    p_mark.add_argument("id")
    p_mark.add_argument("new_status", choices=("applied", "rejected"))
    p_mark.add_argument("--sha")
    p_mark.add_argument("--reason")
    p_mark.add_argument("--signed-off-by", dest="signed_off_by")

    args = parser.parse_args(argv)
    ledger = args.ledger

    if args.command == "validate":
        errors = validate(ledger, repo_root=args.repo)
        for e in errors:
            print(e)
        return 1 if errors else 0

    if args.command == "append":
        record = json.loads(args.record_json)
        new_id = append_lesson(
            ledger, record, today=date.today().isoformat(), rand=os.urandom(3).hex()
        )
        print(new_id)
        return 0

    if args.command == "list":
        rows = list_lessons(ledger, status=args.status)
        if args.json:
            print(json.dumps(rows))
        else:
            for r in rows:
                print(f"{r.get('id')}  {r.get('status')}  {r.get('target_area')}  {r.get('lesson')}")
        return 0

    if args.command == "mark":
        if args.new_status == "applied":
            if not args.sha:
                print("mark applied requires --sha")
                return 1
            # Dry-run the materialization check against --repo BEFORE mutating the
            # ledger, so the CLI never writes a knowingly-invalid applied record.
            records = load_lessons(ledger)
            try:
                rec = _find(records, args.id)
            except KeyError:
                print(f"unknown lesson id: {args.id}")
                return 1
            candidate = dict(rec)
            candidate["status"] = "applied"
            resolution = {"sha": args.sha}
            if args.signed_off_by:
                resolution["signed_off_by"] = args.signed_off_by
            candidate["resolution"] = resolution
            errs = _record_materialization_errors(candidate, Path(args.repo))
            if errs:
                for e in errs:
                    print(e)
                return 1
            mark_applied(ledger, args.id, sha=args.sha, repo_root=args.repo,
                         signed_off_by=args.signed_off_by)
        else:
            if not args.reason or not args.signed_off_by:
                print("mark rejected requires --reason and --signed-off-by")
                return 1
            try:
                mark_rejected(ledger, args.id, reason=args.reason,
                              signed_off_by=args.signed_off_by)
            except KeyError:
                print(f"unknown lesson id: {args.id}")
                return 1
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_parity_lessons.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add scripts/parity_lessons.py tests/test_parity_lessons.py
git commit -m "feat(parity-lessons): argparse CLI (validate/append/list/mark)"
```

---

## Task 8: Wire validate into the gate + finalize

**Files:**
- Modify: `tests/test_parity_lessons.py`

- [ ] **Step 1: Write a test that validates the REAL repo ledger**

Append to `tests/test_parity_lessons.py` (this is the wiring that makes a malformed checked-in ledger fail `just check`):

```python
def test_real_repo_ledger_is_valid() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    ledger = repo_root / "docs" / "parity-loop" / "lessons" / "lessons.jsonl"
    assert ledger.exists(), "the tracked ledger must exist"
    errors = parity_lessons.validate(ledger, repo_root=repo_root)
    assert errors == [], f"checked-in ledger is invalid: {errors}"
```

- [ ] **Step 2: Run it (the ledger is empty, so it passes)**

Run: `uv run pytest tests/test_parity_lessons.py::test_real_repo_ledger_is_valid -v`
Expected: PASS.

- [ ] **Step 3: Run the full project gate**

Run: `just check`
Expected: ruff clean, mypy clean (recall mypy checks `tests/` — the test file uses `Any` for the dynamically-imported module so attribute access type-checks), all tests pass.

- [ ] **Step 4: Fix any lint/type issues inline, then re-run `just check`**

If ruff flags `scripts/parity_lessons.py` (e.g. unused import) or the test file, fix in place and re-run `just check` until green. Do not weaken assertions.

- [ ] **Step 5: Commit**

```bash
# Stage the script too, in case Step 4 fixed lint/type issues in it.
git add scripts/parity_lessons.py tests/test_parity_lessons.py
git commit -m "test(parity-lessons): gate just check on a valid checked-in ledger"
```

---

## Notes for the executor

- This is the helper only. The reflect/improve **skills** that call this CLI are Plan 2 — do not build them here.
- Every git fact comes from `subprocess` calls with `cwd=repo_root`; never assume the process CWD. Tests build a throwaway repo per case.
- `validate` returns error strings (does not raise) so it composes; the CLI maps a non-empty list to exit 1.
- The ID's random suffix uses `os.urandom` in the CLI only; tests inject `rand` for determinism.
- After implementation, run the Pi review loop over the diff before the final commit per the parity-loop workflow (different-family review gate).
