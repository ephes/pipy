# Parity Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `scripts/parity_runner.py` — the bounded, deterministic unattended driver that repeatedly spawns a fresh agent to run one parity-loop gap, with ref-aware verification, a per-repo lock, no-push guards, and a pre-flight/post-loop lesson gate.

**Architecture:** A single stdlib + `subprocess` Python module (CLI + library), tested with an injectable `Hooks` seam and an injected `clock` so every test runs against a tmp git repo with a fake agent — no real LLM, no network. All judgment stays in the spawned agent; the runner owns only the loop, git verification, caps, lock, guards, lesson gate, and run log.

**Tech Stack:** Python 3.11 stdlib (`subprocess`, `argparse`, `dataclasses`, `os`, `re`, `json`, `pathlib`, `shutil`, `signal`), pytest, git, `uv run` / `just check`.

**Scope:** Plan B of 2 for the spec (`docs/superpowers/specs/2026-06-22-parity-runner-design.md`). **Plan A (skill-clause prerequisites) must already be merged** — the runner passes the markers `runner single-gap mode` / `runner unattended mode` that Plan A's clauses key on. Consumes the shipped `scripts/parity_lessons.py`.

**Constraints (read first):**
- Work directly on `main` (trunk-based). No worktree/branch.
- Module lives at `scripts/parity_runner.py` (ruff-linted via `uv run ruff check .`; not mypy-checked, but the **test file is** — keep `tests/test_parity_runner.py` type-clean).
- Tests import the script by file path via `importlib` (scripts/ is not a package).
- Determinism: the module takes `clock` (a `() -> float`) and `run_label` as inputs; never call `time.monotonic()`/`uuid` except in CLI defaults. Tests inject both.
- All git facts via `subprocess` with `cwd=repo`; never assume process CWD.

---

## File Structure

- Create: `scripts/parity_runner.py` — CLI + library. Public surface:
  - dataclasses `Opts` (all config) and `Hooks` (injectable seam: `run_gap`, `run_improve`, `ledger_validate`, `ledger_open_count`).
  - git helpers `_git`, `_out`, `head`, `current_branch`, `tree_clean`, `ref_snapshot`, `is_ancestor`.
  - `valid_run_label`, `per_run_dir`, `per_run_path_safe`.
  - lock: `lock_path`, `acquire_lock`, `release_lock`, `_pid_alive`.
  - guards: `install_no_push_guards` (returns a restore callable).
  - verify: `only_main_advanced`, `verify_committed`, `verify_no_gaps`, `parse_sentinel`.
  - `lesson_gate`, `run`, `default_hooks`, `main`.
- Create: `tests/test_parity_runner.py` — unit + integration tests (tmp git repo + fake `Hooks`).
- Modify: `justfile` — add a `parity-run` recipe.
- Create: `docs/parity-loop/parity-runner.md` — operator doc with an example launchd plist.

### Constants (Task 1)

```python
RUN_LABEL_RE = r"^[A-Za-z0-9._-]+$"
SENTINEL_RE = r"^PARITY_RESULT: (COMMITTED \S+|NO_GAPS|BLOCKED.*)$"
SINGLE_GAP_MARKER = "runner single-gap mode"
UNATTENDED_MARKER = "runner unattended mode"
BLOCKED_PUSHURL = "blocked://parity-runner-no-push"
LEDGER_REL = "docs/parity-loop/lessons/lessons.jsonl"
DEFAULTS = {"max_gaps": 3, "time_budget": 7200, "per_gap_timeout": 2400, "min_gap_slice": 600}
INCOMPLETE_LOCK_GRACE = 30.0  # seconds; a pid-less lock older than this is a crash, reclaim it
```

---

## Task 1: Scaffold module, git helpers, dataclasses, test harness

**Files:**
- Create: `scripts/parity_runner.py`
- Create: `tests/test_parity_runner.py`

- [ ] **Step 1: Create `scripts/parity_runner.py` with header, constants, dataclasses, and git helpers**

```python
#!/usr/bin/env python3
"""Bounded, deterministic unattended driver for the pipy parity loop.

Spawns a fresh agent per gap (`runner single-gap mode`), verifies each result
against git reality, enforces caps, holds a per-repo lock, installs best-effort
no-push guards, and runs a pre-flight/post-loop lesson gate. See
docs/superpowers/specs/2026-06-22-parity-runner-design.md. All judgment lives in
the spawned agent; this module owns only the loop and safety.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

RUN_LABEL_RE = r"^[A-Za-z0-9._-]+$"
SENTINEL_RE = r"^PARITY_RESULT: (COMMITTED \S+|NO_GAPS|BLOCKED.*)$"
SINGLE_GAP_MARKER = "runner single-gap mode"
UNATTENDED_MARKER = "runner unattended mode"
BLOCKED_PUSHURL = "blocked://parity-runner-no-push"
LEDGER_REL = "docs/parity-loop/lessons/lessons.jsonl"
DEFAULTS = {"max_gaps": 3, "time_budget": 7200, "per_gap_timeout": 2400, "min_gap_slice": 600}
INCOMPLETE_LOCK_GRACE = 30.0  # seconds; a pid-less lock older than this is a crash, reclaim it


@dataclass
class Opts:
    repo: Path
    run_dir: Path
    run_label: str
    agent: str = "opus"
    max_gaps: int = 3
    time_budget: float = 7200.0
    per_gap_timeout: float = 2400.0
    min_gap_slice: float = 600.0
    dry_run: bool = False


@dataclass
class Hooks:
    # run_gap(prompt, timeout, log_path) -> (exit_code, stdout)
    run_gap: Callable[[str, float, Path], "tuple[int, str]"]
    # run_improve(prompt, timeout, log_path) -> exit_code
    run_improve: Callable[[str, float, Path], int]
    # ledger_validate(repo) -> exit_code (0 == valid)
    ledger_validate: Callable[[Path], int]
    # ledger_open_count(repo) -> number of open lessons
    ledger_open_count: Callable[[Path], int]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def _out(repo: Path, *args: str) -> str:
    return _git(repo, *args).stdout.strip()


def head(repo: Path) -> str:
    return _out(repo, "rev-parse", "HEAD")


def current_branch(repo: Path) -> str:
    return _out(repo, "rev-parse", "--abbrev-ref", "HEAD")


def tree_clean(repo: Path) -> bool:
    cp = _git(repo, "status", "--porcelain")
    return cp.returncode == 0 and cp.stdout.strip() == ""  # fail closed on git error


def ref_snapshot(repo: Path) -> dict:
    out = _out(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    snap = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        name, sha = line.split(" ", 1)
        snap[name] = sha.strip()
    return snap


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return _git(repo, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0
```

- [ ] **Step 2: Create `tests/test_parity_runner.py` with the import harness + a tmp-git-repo fixture**

```python
"""Unit + integration tests for the parity runner (fake agent, tmp git repo)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

_MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "parity_runner.py"
_spec = importlib.util.spec_from_file_location("parity_runner", _MOD_PATH)
assert _spec is not None and _spec.loader is not None
pr: Any = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pr   # register before exec so dataclass annotations resolve
_spec.loader.exec_module(pr)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    # Pin a repo-local hooks dir so no-push hook assertions are deterministic even
    # on machines with a global core.hooksPath.
    _git(repo, "config", "core.hooksPath", str(repo / ".git" / "hooks"))
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "seed.txt")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


def _commit(repo: Path, rel: str, message: str) -> str:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n", encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def test_git_helpers(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    assert pr.current_branch(repo) == "main"
    assert pr.tree_clean(repo) is True
    h0 = pr.head(repo)
    h1 = _commit(repo, "a.txt", "second")
    assert pr.is_ancestor(repo, h0, h1) is True
    assert pr.is_ancestor(repo, h1, h0) is False
    snap = pr.ref_snapshot(repo)
    assert snap["refs/heads/main"] == h1
```

- [ ] **Step 3: Run to verify it passes**

Run: `uv run pytest tests/test_parity_runner.py -v`
Expected: PASS (1 test).

- [ ] **Step 4: Commit**

```bash
git add scripts/parity_runner.py tests/test_parity_runner.py
git commit -m "feat(parity-runner): scaffold module + git helpers"
```

---

## Task 2: Run-label validation + per-run path safety

**Files:**
- Modify: `scripts/parity_runner.py`
- Modify: `tests/test_parity_runner.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parity_runner.py`:

```python
def test_valid_run_label() -> None:
    assert pr.valid_run_label("2026-06-22T120000Z") is True
    assert pr.valid_run_label("a.b_c-1") is True
    assert pr.valid_run_label("bad/label") is False
    assert pr.valid_run_label("..") is False
    assert pr.valid_run_label("") is False
    assert pr.valid_run_label("with space") is False


def test_per_run_path_safe(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    # Outside the worktree -> safe.
    outside = pr.per_run_dir(tmp_path / "logs", "L1")
    assert pr.per_run_path_safe(repo, outside) is True
    # In-worktree but ignored -> safe.
    (repo / ".gitignore").write_text("docs/parity-loop/runs/*\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore")
    ignored = pr.per_run_dir(repo / "docs/parity-loop/runs", "L1")
    assert pr.per_run_path_safe(repo, ignored) is True
    # In-worktree and NOT ignored -> unsafe.
    tracked = pr.per_run_dir(repo / "logs", "L1")
    assert pr.per_run_path_safe(repo, tracked) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_parity_runner.py -k 'run_label or per_run' -v`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Implement**

Append to `scripts/parity_runner.py`:

```python
def valid_run_label(label: str) -> bool:
    # Reject the reserved dot-names even though the charset would allow them.
    return bool(re.fullmatch(RUN_LABEL_RE, label)) and label not in (".", "..")


def per_run_dir(run_dir: Path, label: str) -> Path:
    return Path(run_dir) / f"run-{label}"


def per_run_path_safe(repo: Path, per_run_path: Path) -> bool:
    """True if the per-run path is outside the worktree, or git-ignored inside it."""
    top = Path(_out(repo, "rev-parse", "--show-toplevel")).resolve()
    target = per_run_path.resolve()
    try:
        target.relative_to(top)
    except ValueError:
        return True  # outside the worktree
    return _git(repo, "check-ignore", str(target)).returncode == 0
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_parity_runner.py -k 'run_label or per_run' -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/parity_runner.py tests/test_parity_runner.py
git commit -m "feat(parity-runner): run-label validation + per-run path safety"
```

---

## Task 3: Per-repo single-run lock

**Files:**
- Modify: `scripts/parity_runner.py`
- Modify: `tests/test_parity_runner.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parity_runner.py`:

```python
def test_lock_is_per_repo_and_exclusive(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    assert pr.acquire_lock(repo) is True
    # Second acquisition fails even though it's the same repo.
    assert pr.acquire_lock(repo) is False
    pr.release_lock(repo)
    # After release, acquirable again.
    assert pr.acquire_lock(repo) is True
    pr.release_lock(repo)


def test_lock_reclaims_stale(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    lock = pr.lock_path(repo)
    lock.mkdir(parents=True)
    (lock / "pid").write_text("999999999", encoding="utf-8")  # not a live PID
    assert pr.acquire_lock(repo) is True  # stale reclaimed
    pr.release_lock(repo)


def test_lock_holds_fresh_incomplete_but_reclaims_aged(tmp_path: Path) -> None:
    import os
    import time as _t
    repo = _init_repo(tmp_path)
    lock = pr.lock_path(repo)
    lock.mkdir(parents=True)  # incomplete: no pid file yet
    # Fresh incomplete (within grace) -> treated as live, not stolen.
    assert pr.acquire_lock(repo) is False
    # Aged past the grace window -> crashed acquirer, reclaim it.
    old = _t.time() - (pr.INCOMPLETE_LOCK_GRACE + 10)
    os.utime(lock, (old, old))
    assert pr.acquire_lock(repo) is True
    pr.release_lock(repo)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_parity_runner.py -k lock -v`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Implement**

Append to `scripts/parity_runner.py`:

```python
def lock_path(repo: Path) -> Path:
    common = Path(_out(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (repo / common).resolve()
    return common / "parity-runner.lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_lock(repo: Path) -> bool:
    """Atomic per-repo lock. Stale reclaim uses an atomic rename so exactly one of
    several racing reclaimers wins; the others see the new holder and fail."""
    lock = lock_path(repo)
    for _ in range(2):  # at most: see stale -> reclaim -> retry once
        try:
            lock.mkdir(parents=False)
            (lock / "pid").write_text(str(os.getpid()), encoding="utf-8")
            return True
        except FileExistsError:
            reclaim = False
            try:
                pid = int((lock / "pid").read_text(encoding="utf-8").strip())
            except (FileNotFoundError, ValueError):
                # Incomplete lock (pid not written yet, OR writer crashed mid-acquire).
                # Within a short grace window assume it's a live mid-acquire and do
                # NOT steal it (closes the mkdir-then-write race). Older than the
                # grace window, the writer crashed -> reclaim (recovers liveness).
                try:
                    age = time.time() - lock.stat().st_mtime
                except FileNotFoundError:
                    continue  # vanished; retry the mkdir
                if age < INCOMPLETE_LOCK_GRACE:
                    return False
                reclaim = True
            else:
                if _pid_alive(pid):
                    return False  # a live holder
                reclaim = True    # present pid that is dead -> stale
            if reclaim:
                # Claim the right to delete via an ATOMIC rename; one winner only.
                trash = lock.with_name(f"{lock.name}.stale.{os.getpid()}")
                try:
                    os.rename(lock, trash)
                except OSError:
                    continue  # someone else moved/recreated it; retry the mkdir
                shutil.rmtree(trash, ignore_errors=True)
                # loop retries mkdir on the now-free path
    return False


def release_lock(repo: Path) -> None:
    shutil.rmtree(lock_path(repo), ignore_errors=True)


def lock_is_held(repo: Path) -> bool:
    """Non-mutating peek (for --dry-run): True if a live or incomplete lock exists."""
    lock = lock_path(repo)
    if not lock.exists():
        return False
    try:
        pid = int((lock / "pid").read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return True  # incomplete -> treat as held
    return _pid_alive(pid)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_parity_runner.py -k lock -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/parity_runner.py tests/test_parity_runner.py
git commit -m "feat(parity-runner): per-repo single-run lock with stale reclaim"
```

---

## Task 4: No-push guards (pushurl block + conditional pre-push hook)

**Files:**
- Modify: `scripts/parity_runner.py`
- Modify: `tests/test_parity_runner.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parity_runner.py`:

```python
def test_no_push_guard_blocks_and_restores(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _git(repo, "remote", "add", "origin", "https://example.invalid/x.git")
    _git(repo, "remote", "set-url", "--add", "--push", "origin", "https://a.invalid/x.git")
    before = _git(repo, "config", "--get-all", "remote.origin.pushurl")
    restore = pr.install_no_push_guards(repo)
    # While active: a push is blocked, and a pre-push hook exists in .git/hooks.
    pushed = subprocess.run(["git", "push", "origin", "main"], cwd=repo,
                            capture_output=True, text=True)
    assert pushed.returncode != 0
    assert (repo / ".git" / "hooks" / "pre-push").exists()
    restore()
    # After restore: original pushurls back, hook removed (none existed before).
    assert _git(repo, "config", "--get-all", "remote.origin.pushurl") == before
    assert not (repo / ".git" / "hooks" / "pre-push").exists()
    assert pr.tree_clean(repo) is True


def test_no_push_guard_restores_preexisting_hook(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    original = hook.read_text(encoding="utf-8")
    restore = pr.install_no_push_guards(repo)
    assert "parity-runner" in hook.read_text(encoding="utf-8")  # replaced
    restore()
    assert hook.read_text(encoding="utf-8") == original  # put back exactly
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_parity_runner.py -k no_push -v`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Implement**

Append to `scripts/parity_runner.py`:

```python
_PREPUSH_HOOK = "#!/bin/sh\n# parity-runner: push disabled during unattended run\n" \
                'echo "parity-runner: push blocked" >&2\nexit 1\n'


def install_no_push_guards(repo: Path) -> Callable[[], None]:
    """Block naive child pushes; return a restore() callable. Best-effort (not a sandbox)."""
    # --- pushurl guard (transport-level) ---
    remotes = [r for r in _out(repo, "remote").splitlines() if r.strip()]
    saved: dict = {}
    for r in remotes:
        urls = _git(repo, "config", "--get-all", f"remote.{r}.pushurl").stdout.splitlines()
        saved[r] = [u for u in urls if u.strip()]
        _git(repo, "config", "--unset-all", f"remote.{r}.pushurl")
        _git(repo, "config", "--add", f"remote.{r}.pushurl", BLOCKED_PUSHURL)

    # --- pre-push hook, only if the EFFECTIVE hooks dir is inside the git dir ---
    common = Path(_out(repo, "rev-parse", "--git-common-dir"))
    common = (repo / common).resolve() if not common.is_absolute() else common.resolve()
    hooks_path_cfg = _out(repo, "config", "--get", "core.hooksPath")
    if hooks_path_cfg:
        top = Path(_out(repo, "rev-parse", "--show-toplevel"))
        hd = Path(hooks_path_cfg)
        hd = (hd if hd.is_absolute() else top / hd).resolve()
    else:
        hd = common / "hooks"
    inside_git = True
    try:
        hd.relative_to(common)   # hd is under the git dir (untracked scratch)
    except ValueError:
        inside_git = False       # worktree (tracked .githooks) or shared/global
    hook_file: Optional[Path] = None
    prev_hook: Optional[str] = None
    prev_mode: Optional[int] = None
    if inside_git:
        hook_file = hd / "pre-push"
        hook_file.parent.mkdir(parents=True, exist_ok=True)
        if hook_file.exists():
            prev_hook = hook_file.read_text(encoding="utf-8")
            prev_mode = hook_file.stat().st_mode  # remember exact mode to restore
        hook_file.write_text(_PREPUSH_HOOK, encoding="utf-8")
        hook_file.chmod(0o755)
    # else: skip the hook, rely on the pushurl guard.

    def restore() -> None:
        for r in remotes:
            _git(repo, "config", "--unset-all", f"remote.{r}.pushurl")
            for u in saved.get(r, []):
                _git(repo, "config", "--add", f"remote.{r}.pushurl", u)
        if hook_file is not None:
            if prev_hook is None:
                hook_file.unlink(missing_ok=True)
            else:
                hook_file.write_text(prev_hook, encoding="utf-8")
                if prev_mode is not None:
                    hook_file.chmod(prev_mode & 0o7777)  # restore exact mode

    return restore
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_parity_runner.py -k no_push -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/parity_runner.py tests/test_parity_runner.py
git commit -m "feat(parity-runner): best-effort no-push guards (pushurl + pre-push hook)"
```

---

## Task 5: Ref-aware verification + sentinel parsing

**Files:**
- Modify: `scripts/parity_runner.py`
- Modify: `tests/test_parity_runner.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parity_runner.py`:

```python
def test_parse_sentinel() -> None:
    assert pr.parse_sentinel("noise\nPARITY_RESULT: NO_GAPS\n") == ("NO_GAPS", "")
    assert pr.parse_sentinel("PARITY_RESULT: COMMITTED abc123\n") == ("COMMITTED", "abc123")
    assert pr.parse_sentinel("PARITY_RESULT: BLOCKED reviewer down") == ("BLOCKED", "reviewer down")
    # last match wins
    assert pr.parse_sentinel("PARITY_RESULT: NO_GAPS\nPARITY_RESULT: COMMITTED z\n")[0] == "COMMITTED"
    assert pr.parse_sentinel("no sentinel here") == (None, "")


def test_verify_committed_ok_and_rejections(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    h0 = pr.head(repo)
    refs0 = pr.ref_snapshot(repo)
    sha = _commit(repo, "docs/x.md", "gap")
    ok, _ = pr.verify_committed(repo, h0, refs0, sha)
    assert ok is True
    # Cited head_before -> reject (not strictly in new range)
    bad, _ = pr.verify_committed(repo, h0, refs0, h0)
    assert bad is False


def test_verify_committed_rejects_offbranch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    h0 = pr.head(repo)
    refs0 = pr.ref_snapshot(repo)
    _git(repo, "checkout", "-q", "-b", "side")
    sha = _commit(repo, "docs/x.md", "side")
    _git(repo, "checkout", "-q", "main")
    ok, _ = pr.verify_committed(repo, h0, refs0, sha)
    assert ok is False  # a new ref (refs/heads/side) was created


def test_verify_no_gaps(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    h0 = pr.head(repo)
    refs0 = pr.ref_snapshot(repo)
    ok, _ = pr.verify_no_gaps(repo, h0, refs0)
    assert ok is True
    _commit(repo, "docs/x.md", "stray")
    bad, _ = pr.verify_no_gaps(repo, h0, refs0)
    assert bad is False  # HEAD moved
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_parity_runner.py -k 'sentinel or verify' -v`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Implement**

Append to `scripts/parity_runner.py`:

```python
def parse_sentinel(text: str) -> "tuple[Optional[str], str]":
    found: tuple = (None, "")
    for line in text.splitlines():
        m = re.match(SENTINEL_RE, line.strip())
        if not m:
            continue
        body = m.group(1)
        if body == "NO_GAPS":
            found = ("NO_GAPS", "")
        elif body.startswith("COMMITTED "):
            found = ("COMMITTED", body[len("COMMITTED "):].strip())
        elif body.startswith("BLOCKED"):
            found = ("BLOCKED", body[len("BLOCKED"):].strip())
    return found


def only_main_advanced(repo: Path, refs_before: dict) -> bool:
    """True iff the ref set is unchanged except refs/heads/main may differ."""
    after = ref_snapshot(repo)
    if set(after) != set(refs_before):
        return False  # ref created or deleted
    for name, sha in refs_before.items():
        if name == "refs/heads/main":
            continue
        if after[name] != sha:
            return False  # a non-main ref moved
    return True


def _resolve(repo: Path, rev: str) -> Optional[str]:
    cp = _git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}")
    return cp.stdout.strip() if cp.returncode == 0 else None


def verify_committed(repo: Path, head_before: str, refs_before: dict, sha: str) -> "tuple[bool, str]":
    if current_branch(repo) != "main":
        return False, "not on main after gap"
    if not tree_clean(repo):
        return False, "dirty tree after COMMITTED"
    cur = head(repo)
    if cur == head_before or not is_ancestor(repo, head_before, cur):
        return False, "HEAD did not advance forward from head_before"
    if not only_main_advanced(repo, refs_before):
        return False, "a non-main ref changed (off-branch commit?)"
    resolved = _resolve(repo, sha)
    if resolved is None or resolved == head_before:
        return False, "cited sha does not resolve / equals head_before"
    if not (is_ancestor(repo, head_before, resolved) and is_ancestor(repo, resolved, cur)):
        return False, "cited sha is not within (head_before, HEAD]"
    return True, "ok"


def verify_no_gaps(repo: Path, head_before: str, refs_before: dict) -> "tuple[bool, str]":
    if current_branch(repo) != "main":
        return False, "not on main"
    if not tree_clean(repo):
        return False, "dirty tree on NO_GAPS"
    if head(repo) != head_before:
        return False, "HEAD moved on NO_GAPS"
    if ref_snapshot(repo) != refs_before:
        return False, "refs changed on NO_GAPS"
    return True, "ok"
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_parity_runner.py -k 'sentinel or verify' -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/parity_runner.py tests/test_parity_runner.py
git commit -m "feat(parity-runner): ref-aware commit verification + sentinel parsing"
```

---

## Task 6: Lesson gate (validate + unattended parity-improve + exit 3)

**Files:**
- Modify: `scripts/parity_runner.py`
- Modify: `tests/test_parity_runner.py`

The gate returns `None` when clear, or an exit code (`1` ledger_invalid, `3`
lessons_backlog). It records events via a small log callback.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parity_runner.py`:

```python
def _events_recorder() -> "tuple[list, Any]":
    events: list = []
    def log(ev_type: str, **fields: Any) -> None:
        events.append((ev_type, fields))
    return events, log


def test_lesson_gate_clean_when_no_open(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _, log = _events_recorder()
    hooks = pr.Hooks(
        run_gap=lambda *a: (0, ""),
        run_improve=lambda *a: 0,
        ledger_validate=lambda _r: 0,
        ledger_open_count=lambda _r: 0,
    )
    code = pr.lesson_gate(repo, "preflight", hooks, remaining_budget=1000.0,
                          min_gap_slice=600.0, per_gap_timeout=2400.0,
                          run_dir=tmp_path, log=log)
    assert code is None


def test_lesson_gate_drains_then_exit3(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    events, log = _events_recorder()
    state = {"open": 2}

    def fake_improve(prompt: str, timeout: float, log_path: Path) -> int:
        assert pr.UNATTENDED_MARKER in prompt  # must request unattended mode
        state["open"] = 1   # drained the auto-gateable one; one sign-off lesson remains
        return 0

    hooks = pr.Hooks(
        run_gap=lambda *a: (0, ""),
        run_improve=fake_improve,
        ledger_validate=lambda _r: 0,
        ledger_open_count=lambda _r: state["open"],
    )
    code = pr.lesson_gate(repo, "postloop", hooks, remaining_budget=1000.0,
                          min_gap_slice=600.0, per_gap_timeout=2400.0,
                          run_dir=tmp_path, log=log)
    assert code == 3
    assert any(e[0] == "needs_human_review" for e in events)


def test_lesson_gate_ledger_invalid(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _, log = _events_recorder()
    hooks = pr.Hooks(
        run_gap=lambda *a: (0, ""),
        run_improve=lambda *a: 0,
        ledger_validate=lambda _r: 1,   # corrupt ledger
        ledger_open_count=lambda _r: 0,
    )
    code = pr.lesson_gate(repo, "preflight", hooks, remaining_budget=1000.0,
                          min_gap_slice=600.0, per_gap_timeout=2400.0,
                          run_dir=tmp_path, log=log)
    assert code == 1


def test_lesson_gate_skips_improve_when_no_budget(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    events, log = _events_recorder()
    spawned = {"n": 0}

    def fake_improve(*a: Any) -> int:
        spawned["n"] += 1
        return 0

    hooks = pr.Hooks(
        run_gap=lambda *a: (0, ""),
        run_improve=fake_improve,
        ledger_validate=lambda _r: 0,
        ledger_open_count=lambda _r: 1,   # one open lesson, no budget to drain it
    )
    code = pr.lesson_gate(repo, "postloop", hooks, remaining_budget=10.0,
                          min_gap_slice=600.0, per_gap_timeout=2400.0,
                          run_dir=tmp_path, log=log)
    assert spawned["n"] == 0          # no spawn (budget too low)
    assert code == 3                  # lessons still open
    assert any(e[0] == "safety_net_skipped" for e in events)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_parity_runner.py -k lesson_gate -v`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Implement**

Append to `scripts/parity_runner.py`:

```python
def _improve_prompt() -> str:
    return (
        f"Run the `parity-improve` skill in {UNATTENDED_MARKER}, in this repo, on "
        "`main`. Do not push. Apply only lessons gateable without sign-off "
        "(docs/tests/harness); leave instruction-area lessons and rejections open."
    )


def lesson_gate(repo: Path, phase: str, hooks: Hooks, *, remaining_budget: float,
                min_gap_slice: float, per_gap_timeout: float, run_dir: Path,
                log: Callable[..., None]) -> Optional[int]:
    """Return None if clear, else an exit code (1 ledger_invalid, 3 lessons_backlog)."""
    if hooks.ledger_validate(repo) != 0:
        log("ledger_invalid", phase=phase)
        return 1
    n = hooks.ledger_open_count(repo)
    if n < 0:                       # fail-closed: count unavailable
        log("ledger_count_failed", phase=phase)
        return 1
    if n == 0:
        return None
    # Open lessons exist: drain what we can if budget allows.
    if remaining_budget >= min_gap_slice:
        head_before = head(repo)
        refs_before = ref_snapshot(repo)
        log_path = Path(run_dir) / f"improve-{phase}.log"   # distinct per gate
        timeout = min(per_gap_timeout, remaining_budget)
        rc = hooks.run_improve(_improve_prompt(), timeout, log_path)
        if rc != 0:
            # Per spec: record a warning; the state + ledger rechecks below decide
            # the exit (a nonzero exit that still left a clean tree and drained the
            # auto-gateable lessons must not be mis-reported as a hard failure).
            log("safety_net_failed", phase=phase, exit_code=rc)
        # main may advance (improve commits) but only FORWARD — reject rewind/jump.
        if (current_branch(repo) != "main" or not tree_clean(repo)
                or not only_main_advanced(repo, refs_before)
                or not is_ancestor(repo, head_before, head(repo))):
            log("safety_net_dirtied", phase=phase)
            return 1
        if hooks.ledger_validate(repo) != 0:
            log("ledger_invalid", phase=phase)
            return 1
    else:
        log("safety_net_skipped", phase=phase, reason="budget")
    m = hooks.ledger_open_count(repo)
    if m < 0:
        log("ledger_count_failed", phase=phase)
        return 1
    if m > 0:
        log("needs_human_review", phase=phase, open=m)
        return 3
    return None
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_parity_runner.py -k lesson_gate -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/parity_runner.py tests/test_parity_runner.py
git commit -m "feat(parity-runner): pre-flight/post-loop lesson gate"
```

---

## Task 7: The orchestrator `run()` (startup, loop, caps, run log)

**Files:**
- Modify: `scripts/parity_runner.py`
- Modify: `tests/test_parity_runner.py`

`run(opts, hooks, clock)` returns the exit code. `clock()` is an injected
monotonic seconds source so time-budget tests are deterministic.

- [ ] **Step 1: Write the failing integration tests**

Append to `tests/test_parity_runner.py`:

```python
def _opts(repo: Path, tmp_path: Path, **over: Any) -> Any:
    base: "dict[str, Any]" = dict(repo=repo, run_dir=tmp_path / "runs", run_label="L1", agent="opus",
                max_gaps=3, time_budget=7200.0, per_gap_timeout=2400.0,
                min_gap_slice=600.0, dry_run=False)
    base.update(over)
    return pr.Opts(**base)


def _clock_seq(values: list) -> Any:
    it = iter(values)
    last = [0.0]
    def clock() -> float:
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]
    return clock


def _ok_ledger_hooks(repo: Path, **over: Any) -> Any:
    base: "dict[str, Any]" = dict(run_gap=lambda *a: (0, ""), run_improve=lambda *a: 0,
                                  ledger_validate=lambda _r: 0, ledger_open_count=lambda _r: 0)
    base.update(over)
    return pr.Hooks(**base)


def test_run_stops_on_no_gaps(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    hooks = _ok_ledger_hooks(repo, run_gap=lambda *a: (0, "PARITY_RESULT: NO_GAPS\n"))
    code = pr.run(_opts(repo, tmp_path), hooks, clock=_clock_seq([0.0, 1.0]))
    assert code == 0


def test_run_does_one_verified_gap_then_no_gaps(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    calls = {"n": 0}

    def run_gap(prompt: str, timeout: float, log_path: Path) -> "tuple[int, str]":
        assert pr.SINGLE_GAP_MARKER in prompt
        calls["n"] += 1
        if calls["n"] == 1:
            sha = _commit(repo, f"docs/gap{calls['n']}.md", "gap 1")
            return 0, f"PARITY_RESULT: COMMITTED {sha}\n"
        return 0, "PARITY_RESULT: NO_GAPS\n"

    hooks = _ok_ledger_hooks(repo, run_gap=run_gap)
    code = pr.run(_opts(repo, tmp_path), hooks, clock=_clock_seq([0.0, 1.0, 2.0, 3.0]))
    assert code == 0
    assert calls["n"] == 2
    runlog = (tmp_path / "runs" / "run-L1" / "run.jsonl").read_text(encoding="utf-8")
    assert "gap.completed" in runlog


def test_run_stops_on_unverified_committed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    # Claims COMMITTED but makes no commit -> verification fails -> stop, exit 1.
    hooks = _ok_ledger_hooks(repo, run_gap=lambda *a: (0, "PARITY_RESULT: COMMITTED deadbeef\n"))
    code = pr.run(_opts(repo, tmp_path), hooks, clock=_clock_seq([0.0, 1.0]))
    assert code == 1


def test_run_blocks_duplicate_label(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".gitignore").write_text("runs/*\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ig")
    opts = _opts(repo, run_dir=repo / "runs", tmp_path=tmp_path)
    (repo / "runs" / "run-L1").mkdir(parents=True)
    hooks = _ok_ledger_hooks(repo, run_gap=lambda *a: (0, "PARITY_RESULT: NO_GAPS\n"))
    code = pr.run(opts, hooks, clock=_clock_seq([0.0]))
    assert code == 2  # duplicate_run_label


def test_run_preflight_backlog_exit3_zero_gaps(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    gap_calls = {"n": 0}

    def run_gap(*a: Any) -> "tuple[int, str]":
        gap_calls["n"] += 1
        return 0, "PARITY_RESULT: NO_GAPS\n"

    hooks = _ok_ledger_hooks(repo, run_gap=run_gap, ledger_open_count=lambda _r: 1,
                             run_improve=lambda *a: 0)
    code = pr.run(_opts(repo, tmp_path), hooks, clock=_clock_seq([0.0, 1.0]))
    assert code == 3
    assert gap_calls["n"] == 0  # never started a gap
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_parity_runner.py -k run_ -v`
Expected: FAIL (`AttributeError` for `run`).

- [ ] **Step 3: Implement**

Append to `scripts/parity_runner.py`:

```python
class _RunLog:
    def __init__(self, per_run: Path) -> None:
        self.path = per_run / "run.jsonl"
        self.per_run = per_run

    def event(self, ev_type: str, **fields: object) -> None:
        rec = {"type": ev_type, **fields}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")

    def gap_log(self, idx: int) -> Path:
        return self.per_run / f"gap-{idx}.log"


def _gap_prompt() -> str:
    return (
        f"Run the `pipy-parity-loop` skill for exactly ONE gap, in this repo, on "
        f"`main`, in {SINGLE_GAP_MARKER}. Do not push. When finished, print exactly "
        "one final line: `PARITY_RESULT: COMMITTED <sha>` or `PARITY_RESULT: "
        "NO_GAPS` or `PARITY_RESULT: BLOCKED <reason>`."
    )


def run(opts: Opts, hooks: Hooks, *, clock: Callable[[], float]) -> int:
    repo = opts.repo
    per_run = per_run_dir(opts.run_dir, opts.run_label)
    # --- startup preconditions ---
    if not valid_run_label(opts.run_label):
        return 2
    if not per_run_path_safe(repo, per_run):
        return 2
    if not tree_clean(repo) or current_branch(repo) != "main":
        return 2
    # Dry-run validates and reports WITHOUT reserving any state (no lock, no dir,
    # no guards) — handled before anything is created.
    if opts.dry_run:
        if lock_is_held(repo):
            return 2
        if per_run.exists():
            return 2  # would be duplicate_run_label
        return 0
    # Reserve label LAST: only after the lock is held and preconditions pass.
    if not acquire_lock(repo):
        return 2
    try:
        try:
            per_run.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            return 2  # duplicate_run_label
        log = _RunLog(per_run)
        restore_guards = install_no_push_guards(repo)
        try:
            start = clock()

            def remaining() -> float:
                return opts.time_budget - (clock() - start)

            log.event("run.started", agent=opts.agent, head_before=head(repo),
                      max_gaps=opts.max_gaps)
            # pre-flight lesson gate (never pile gaps on an undrained backlog)
            code = lesson_gate(repo, "preflight", hooks, remaining_budget=remaining(),
                               min_gap_slice=opts.min_gap_slice,
                               per_gap_timeout=opts.per_gap_timeout,
                               run_dir=per_run, log=log.event)
            if code is not None:
                log.event("run.finished", gaps_done=0, stop_reason="preflight",
                          exit_code=code)
                return code

            gaps_done = 0
            stop = "cap_reached"
            while gaps_done < opts.max_gaps:
                rem = remaining()
                if rem < opts.min_gap_slice:
                    stop = "cap_reached"
                    break
                head_before = head(repo)
                refs_before = ref_snapshot(repo)
                exit_code, stdout = hooks.run_gap(
                    _gap_prompt(), min(opts.per_gap_timeout, rem), log.gap_log(gaps_done + 1))
                kind, arg = parse_sentinel(stdout)
                if exit_code == 0 and kind == "COMMITTED":
                    ok, reason = verify_committed(repo, head_before, refs_before, arg)
                    if ok:
                        gaps_done += 1
                        log.event("gap.completed", index=gaps_done, sha=arg)
                        continue
                    stop = f"verify_failed:{reason}"
                    log.event("gap.failed", reason=stop)
                    break
                if exit_code == 0 and kind == "NO_GAPS" \
                        and verify_no_gaps(repo, head_before, refs_before)[0]:
                    stop = "no_gaps"
                    log.event("gap.no_gaps")   # clean stop, not a failure
                    break
                stop = "blocked" if kind == "BLOCKED" else "failure"
                log.event("gap.failed", reason=stop)
                break

            clean_stop = stop in ("no_gaps", "cap_reached")
            if clean_stop and tree_clean(repo) and current_branch(repo) == "main":
                code = lesson_gate(repo, "postloop", hooks, remaining_budget=remaining(),
                                   min_gap_slice=opts.min_gap_slice,
                                   per_gap_timeout=opts.per_gap_timeout,
                                   run_dir=per_run, log=log.event)
                if code is not None:
                    log.event("run.finished", gaps_done=gaps_done, stop_reason=stop,
                              exit_code=code)
                    return code
                log.event("run.finished", gaps_done=gaps_done, stop_reason=stop,
                          exit_code=0)
                return 0
            log.event("run.finished", gaps_done=gaps_done, stop_reason=stop,
                      exit_code=1, needs_human_cleanup=True)
            return 1
        finally:
            restore_guards()
    finally:
        release_lock(repo)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_parity_runner.py -k run_ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/parity_runner.py tests/test_parity_runner.py
git commit -m "feat(parity-runner): orchestrator run() with startup, loop, gates, run log"
```

---

## Task 8: Default real hooks + CLI

**Files:**
- Modify: `scripts/parity_runner.py`
- Modify: `tests/test_parity_runner.py`

- [ ] **Step 1: Write the failing CLI test**

Append to `tests/test_parity_runner.py`:

```python
def test_cli_dry_run(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".gitignore").write_text("docs/parity-loop/runs/*\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ig")
    result = subprocess.run(
        ["python3", str(_MOD_PATH), "--repo", str(repo),
         "--run-dir", str(repo / "docs/parity-loop/runs"),
         "--run-label", "DRY1", "--dry-run"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_cli_rejects_bad_label(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    result = subprocess.run(
        ["python3", str(_MOD_PATH), "--repo", str(repo), "--run-label", "bad/label",
         "--dry-run"],
        capture_output=True, text=True)
    assert result.returncode == 2
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_parity_runner.py -k cli -v`
Expected: FAIL (no `main`).

- [ ] **Step 3: Implement default hooks + `main`**

Append to `scripts/parity_runner.py`:

```python
def _agent_cmd(agent: str) -> "list[str]":
    return ["claude-yolo", "-p", "--model", "opus"] if agent == "opus" else [agent, "-p"]


def _spawn_capture(cmd: "list[str]", cwd: Path, timeout: float,
                   log_path: Path) -> "tuple[int, str]":
    """Spawn in its own process group; on timeout kill the whole group (not just the
    direct child, which may have spawned the model CLI). Returns (exit_code, stdout)."""
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        out, err = proc.communicate()
        rc = -1  # treated as a failure by the loop
    log_path.write_text((out or "") + "\n--- stderr ---\n" + (err or ""), encoding="utf-8")
    return rc, (out or "")


def _real_run_gap(repo: Path, agent: str) -> Callable[[str, float, Path], "tuple[int, str]"]:
    def run_gap(prompt: str, timeout: float, log_path: Path) -> "tuple[int, str]":
        return _spawn_capture([*_agent_cmd(agent), prompt], repo, timeout, log_path)
    return run_gap


def _real_run_improve(repo: Path, agent: str) -> Callable[[str, float, Path], int]:
    def run_improve(prompt: str, timeout: float, log_path: Path) -> int:
        rc, _ = _spawn_capture([*_agent_cmd(agent), prompt], repo, timeout, log_path)
        return rc
    return run_improve


def _ledger_cmd(repo: Path, *args: str) -> subprocess.CompletedProcess:
    script = repo / "scripts" / "parity_lessons.py"
    ledger = repo / LEDGER_REL
    return subprocess.run(
        ["python3", str(script), "--ledger", str(ledger), "--repo", str(repo), *args],
        cwd=repo, capture_output=True, text=True)


def default_hooks(opts: Opts) -> Hooks:
    def ledger_validate(repo: Path) -> int:
        return _ledger_cmd(repo, "validate").returncode

    def ledger_open_count(repo: Path) -> int:
        # Fail CLOSED: a failed/garbled count returns -1 so the gate stops (exit 1),
        # never proceeds as if the backlog were empty.
        cp = _ledger_cmd(repo, "list", "--status", "open", "--json")
        if cp.returncode != 0:
            return -1
        try:
            return len(json.loads(cp.stdout or "[]"))
        except (json.JSONDecodeError, TypeError):
            return -1

    return Hooks(
        run_gap=_real_run_gap(opts.repo, opts.agent),
        run_improve=_real_run_improve(opts.repo, opts.agent),
        ledger_validate=ledger_validate,
        ledger_open_count=ledger_open_count,
    )


def main(argv: Optional[list] = None) -> int:
    import time
    p = argparse.ArgumentParser(description="bounded unattended parity-loop runner")
    p.add_argument("--repo", default=".")
    p.add_argument("--run-dir", default="docs/parity-loop/runs")
    p.add_argument("--run-label", default=None)
    p.add_argument("--agent", default="opus")
    p.add_argument("--max-gaps", type=int, default=DEFAULTS["max_gaps"])
    p.add_argument("--time-budget", type=float, default=DEFAULTS["time_budget"])
    p.add_argument("--per-gap-timeout", type=float, default=DEFAULTS["per_gap_timeout"])
    p.add_argument("--min-gap-slice", type=float, default=DEFAULTS["min_gap_slice"])
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    label = a.run_label or time.strftime("%Y-%m-%dT%H%M%SZ", time.gmtime())
    repo_path = Path(a.repo).resolve()
    run_dir = Path(a.run_dir)
    if not run_dir.is_absolute():
        run_dir = repo_path / run_dir   # resolve against --repo, never process CWD
    opts = Opts(repo=repo_path, run_dir=run_dir, run_label=label,
                agent=a.agent, max_gaps=a.max_gaps, time_budget=a.time_budget,
                per_gap_timeout=a.per_gap_timeout, min_gap_slice=a.min_gap_slice,
                dry_run=a.dry_run)
    return run(opts, default_hooks(opts), clock=time.monotonic)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_parity_runner.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/parity_runner.py tests/test_parity_runner.py
git commit -m "feat(parity-runner): default real hooks + argparse CLI"
```

---

## Task 9: `just parity-run` recipe + operator doc (launchd example)

**Files:**
- Modify: `justfile`
- Create: `docs/parity-loop/parity-runner.md`

- [ ] **Step 1: Add the `parity-run` recipe to `justfile`**

Append to `justfile`:

```make
# Run one bounded unattended parity-loop batch (claude-yolo opus driver).
# Defaults the run label to a UTC timestamp when none is given (never passes empty).
parity-run label="":
    label="{{label}}"; [ -n "$label" ] || label="$(date -u +%Y-%m-%dT%H%M%SZ)"; uv run python scripts/parity_runner.py --run-label "$label"
```

- [ ] **Step 2: Create the operator doc `docs/parity-loop/parity-runner.md`**

```markdown
# Parity Runner (unattended)

`scripts/parity_runner.py` runs a **bounded** batch of parity-loop gaps unattended,
fresh context per gap, with hard caps and a lesson gate. It **never pushes**;
commits stay local on `main` for review. See the design at
`docs/superpowers/specs/2026-06-22-parity-runner-design.md`.

## Run it

    just parity-run                 # default label from UTC time
    just parity-run my-label        # explicit run label
    uv run python scripts/parity_runner.py --max-gaps 2 --time-budget 3600

## Exit codes

- `0` — clean stop, no open lessons remaining.
- `1` — something failed **mid-run**: a blocked/unverified gap, or a dirty tree /
  off-`main` / invalid-ledger detected after work started (needs human cleanup).
- `2` — a **startup precondition** was not met, so **nothing ran**: dirty tree or
  off `main` at start, unsafe `--run-dir`, busy lock, duplicate or invalid run label.
- `3` — work complete but open lessons need your sign-off; drain them with
  `parity-improve`, then the next run proceeds.

## Schedule it (example launchd plist — not auto-installed)

Save as `~/Library/LaunchAgents/de.wersdoerfer.parity-run.plist` and
`launchctl load` it to run nightly at 02:30. Adjust paths.

    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
      "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0"><dict>
      <key>Label</key><string>de.wersdoerfer.parity-run</string>
      <key>WorkingDirectory</key><string>/Users/jochen/projects/pipy</string>
      <key>ProgramArguments</key><array>
        <string>/bin/sh</string><string>-lc</string>
        <string>just parity-run "$(date -u +%Y-%m-%dT%H%M%SZ)"</string>
      </array>
      <key>StartCalendarInterval</key><dict>
        <key>Hour</key><integer>2</integer><key>Minute</key><integer>30</integer>
      </dict>
      <key>StandardOutPath</key><string>/tmp/parity-run.out.log</string>
      <key>StandardErrorPath</key><string>/tmp/parity-run.err.log</string>
    </dict></plist>

Prefer small nightly batches (low `--max-gaps`) over long continuous runs.
```

- [ ] **Step 3: Verify the recipe parses and the doc exists**

Run: `just --list | grep parity-run && test -f docs/parity-loop/parity-runner.md && echo OK`
Expected: shows the `parity-run` recipe and prints `OK`.

- [ ] **Step 4: Commit**

```bash
git add justfile docs/parity-loop/parity-runner.md
git commit -m "docs(parity-runner): just parity-run recipe + operator/launchd doc"
```

---

## Task 10: Full gate + finalize

**Files:** none (verification only).

- [ ] **Step 1: Run the full project gate**

Run: `just check`
Expected: ruff clean, mypy clean (mypy checks `tests/` — the test file uses `Any`
for the dynamically-imported module and the fake hooks, so it type-checks), all
tests pass (including `tests/test_parity_runner.py` and the existing suites).

- [ ] **Step 2: Fix any lint/type issues inline, then re-run `just check`**

If ruff flags `scripts/parity_runner.py` (e.g. an unused import) or the test file,
fix in place and re-run `just check` until green. Do not weaken assertions.

- [ ] **Step 3: Final commit if Step 2 changed anything**

```bash
git add -A
git commit -m "chore(parity-runner): satisfy lint/type gate"
```

(If Step 2 changed nothing, skip.)

---

## Notes for the executor

- **Plan A must be merged first** — the runner passes `runner single-gap mode` /
  `runner unattended mode`, which only have effect once Plan A's skill clauses exist.
- The runner is a deterministic safety harness, not a sandbox: it issues no push
  and installs best-effort guards, but does not defeat a deliberately adversarial
  agent. Safety = bounded local increments + the in-unit gates + human review.
- Every git fact comes from `subprocess` with `cwd=repo`; the injected `clock` and
  `Hooks` seam keep tests free of real LLM calls and wall-clock flakiness.
- After implementation, run the different-family Pi review loop over the diff
  before the final commit, per the parity-loop workflow.

