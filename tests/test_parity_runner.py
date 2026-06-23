"""Unit + integration tests for the parity runner (fake agent, tmp git repo)."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time as time_module
from pathlib import Path
from typing import Any

_MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "parity_runner.py"
_spec = importlib.util.spec_from_file_location("parity_runner", _MOD_PATH)
assert _spec is not None and _spec.loader is not None
pr: Any = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pr
_spec.loader.exec_module(pr)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
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


def test_valid_run_label() -> None:
    assert pr.valid_run_label("2026-06-22T120000Z") is True
    assert pr.valid_run_label("a.b_c-1") is True
    assert pr.valid_run_label("bad/label") is False
    assert pr.valid_run_label("..") is False
    assert pr.valid_run_label("") is False
    assert pr.valid_run_label("with space") is False


def test_per_run_path_safe(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    outside = pr.per_run_dir(tmp_path / "logs", "L1")
    assert pr.per_run_path_safe(repo, outside) is True
    (repo / ".gitignore").write_text("docs/parity-loop/runs/*\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore")
    ignored = pr.per_run_dir(repo / "docs/parity-loop/runs", "L1")
    assert pr.per_run_path_safe(repo, ignored) is True
    tracked = pr.per_run_dir(repo / "logs", "L1")
    assert pr.per_run_path_safe(repo, tracked) is False


def test_lock_is_per_repo_and_exclusive(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    assert pr.acquire_lock(repo) is True
    assert pr.acquire_lock(repo) is False
    pr.release_lock(repo)
    assert pr.acquire_lock(repo) is True
    pr.release_lock(repo)


def test_lock_reclaims_stale(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    lock = pr.lock_path(repo)
    lock.mkdir(parents=True)
    (lock / "pid").write_text("999999999", encoding="utf-8")
    assert pr.acquire_lock(repo) is True
    pr.release_lock(repo)


def test_lock_holds_fresh_incomplete_but_reclaims_aged(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    lock = pr.lock_path(repo)
    lock.mkdir(parents=True)
    assert pr.acquire_lock(repo) is False
    old = time_module.time() - (pr.INCOMPLETE_LOCK_GRACE + 10)
    os.utime(lock, (old, old))
    assert pr.acquire_lock(repo) is True
    pr.release_lock(repo)


def test_no_push_guard_blocks_and_restores(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _git(repo, "remote", "add", "origin", "https://example.invalid/x.git")
    _git(repo, "remote", "set-url", "--add", "--push", "origin", "https://a.invalid/x.git")
    before = _git(repo, "config", "--get-all", "remote.origin.pushurl")
    restore = pr.install_no_push_guards(repo)
    pushed = subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert pushed.returncode != 0
    assert (repo / ".git" / "hooks" / "pre-push").exists()
    restore()
    assert _git(repo, "config", "--get-all", "remote.origin.pushurl") == before
    assert not (repo / ".git" / "hooks" / "pre-push").exists()
    assert pr.tree_clean(repo) is True


def test_no_push_guard_restores_preexisting_hook(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    original = hook.read_text(encoding="utf-8")
    restore = pr.install_no_push_guards(repo)
    assert "parity-runner" in hook.read_text(encoding="utf-8")
    restore()
    assert hook.read_text(encoding="utf-8") == original


def test_no_push_guard_skips_worktree_hooks_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _git(repo, "remote", "add", "origin", "https://example.invalid/x.git")
    (repo / ".githooks").mkdir()
    _git(repo, "config", "core.hooksPath", ".githooks")
    restore = pr.install_no_push_guards(repo)
    assert not (repo / ".githooks" / "pre-push").exists()
    pushed = subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert pushed.returncode != 0
    restore()
    assert pr.tree_clean(repo) is True


def test_no_push_guard_cleans_own_residue_after_crash(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _git(repo, "remote", "add", "origin", "https://example.invalid/x.git")
    _git(repo, "config", "--add", "remote.origin.pushurl", pr.BLOCKED_PUSHURL)
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text(pr._PREPUSH_HOOK, encoding="utf-8")

    restore = pr.install_no_push_guards(repo)
    restore()

    pushurls = subprocess.run(
        ["git", "config", "--get-all", "remote.origin.pushurl"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert pushurls.stdout.strip() == ""
    assert not hook.exists()


def test_parse_sentinel() -> None:
    assert pr.parse_sentinel("noise\nPARITY_RESULT: NO_GAPS\n") == ("NO_GAPS", "")
    assert pr.parse_sentinel("PARITY_RESULT: COMMITTED abc123\n") == ("COMMITTED", "abc123")
    assert pr.parse_sentinel("PARITY_RESULT: BLOCKED reviewer down") == (
        "BLOCKED",
        "reviewer down",
    )
    assert pr.parse_sentinel("PARITY_RESULT: NO_GAPS\nPARITY_RESULT: COMMITTED z\n")[0] == (
        "COMMITTED"
    )
    assert pr.parse_sentinel("PARITY_RESULT: BLOCKED") == (None, "")
    assert pr.parse_sentinel("PARITY_RESULT: BLOCKEDxyz") == (None, "")
    assert pr.parse_sentinel("no sentinel here") == (None, "")


def test_verify_committed_ok_and_rejections(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    h0 = pr.head(repo)
    refs0 = pr.ref_snapshot(repo)
    sha = _commit(repo, "docs/x.md", "gap")
    ok, _ = pr.verify_committed(repo, h0, refs0, sha)
    assert ok is True
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
    assert ok is False


def test_verify_no_gaps(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    h0 = pr.head(repo)
    refs0 = pr.ref_snapshot(repo)
    ok, _ = pr.verify_no_gaps(repo, h0, refs0)
    assert ok is True
    _commit(repo, "docs/x.md", "stray")
    bad, _ = pr.verify_no_gaps(repo, h0, refs0)
    assert bad is False


def _events_recorder() -> tuple[list[tuple[str, dict[str, Any]]], Any]:
    events: list[tuple[str, dict[str, Any]]] = []

    def log(event_type: str, **fields: Any) -> None:
        events.append((event_type, fields))

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
    code = pr.lesson_gate(
        repo,
        "preflight",
        hooks,
        remaining_budget=1000.0,
        min_gap_slice=600.0,
        per_gap_timeout=2400.0,
        run_dir=tmp_path,
        log=log,
    )
    assert code is None


def test_lesson_gate_drains_then_exit3(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    events, log = _events_recorder()
    state = {"open": 2}

    def fake_improve(prompt: str, timeout: float, log_path: Path) -> int:
        assert pr.UNATTENDED_MARKER in prompt
        state["open"] = 1
        return 0

    hooks = pr.Hooks(
        run_gap=lambda *a: (0, ""),
        run_improve=fake_improve,
        ledger_validate=lambda _r: 0,
        ledger_open_count=lambda _r: state["open"],
    )
    code = pr.lesson_gate(
        repo,
        "postloop",
        hooks,
        remaining_budget=1000.0,
        min_gap_slice=600.0,
        per_gap_timeout=2400.0,
        run_dir=tmp_path,
        log=log,
    )
    assert code == 3
    assert any(event[0] == "needs_human_review" for event in events)


def test_lesson_gate_ledger_invalid(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _, log = _events_recorder()
    hooks = pr.Hooks(
        run_gap=lambda *a: (0, ""),
        run_improve=lambda *a: 0,
        ledger_validate=lambda _r: 1,
        ledger_open_count=lambda _r: 0,
    )
    code = pr.lesson_gate(
        repo,
        "preflight",
        hooks,
        remaining_budget=1000.0,
        min_gap_slice=600.0,
        per_gap_timeout=2400.0,
        run_dir=tmp_path,
        log=log,
    )
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
        ledger_open_count=lambda _r: 1,
    )
    code = pr.lesson_gate(
        repo,
        "postloop",
        hooks,
        remaining_budget=10.0,
        min_gap_slice=600.0,
        per_gap_timeout=2400.0,
        run_dir=tmp_path,
        log=log,
    )
    assert spawned["n"] == 0
    assert code == 3
    assert any(event[0] == "safety_net_skipped" for event in events)


def _opts(repo: Path, tmp_path: Path, **over: Any) -> Any:
    base: dict[str, Any] = {
        "repo": repo,
        "run_dir": tmp_path / "runs",
        "run_label": "L1",
        "agent": "opus",
        "max_gaps": 3,
        "time_budget": 7200.0,
        "per_gap_timeout": 2400.0,
        "min_gap_slice": 600.0,
        "dry_run": False,
    }
    base.update(over)
    return pr.Opts(**base)


def _clock_seq(values: list[float]) -> Any:
    iterator = iter(values)
    last = [0.0]

    def clock() -> float:
        try:
            last[0] = next(iterator)
        except StopIteration:
            pass
        return last[0]

    return clock


def _ok_ledger_hooks(**over: Any) -> Any:
    base: dict[str, Any] = {
        "run_gap": lambda *a: (0, ""),
        "run_improve": lambda *a: 0,
        "ledger_validate": lambda _r: 0,
        "ledger_open_count": lambda _r: 0,
    }
    base.update(over)
    return pr.Hooks(**base)


def test_run_stops_on_no_gaps(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    hooks = _ok_ledger_hooks(run_gap=lambda *a: (0, "PARITY_RESULT: NO_GAPS\n"))
    code = pr.run(_opts(repo, tmp_path), hooks, clock=_clock_seq([0.0, 1.0]))
    assert code == 0


def test_run_does_one_verified_gap_then_no_gaps(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    calls = {"n": 0}

    def run_gap(prompt: str, timeout: float, log_path: Path) -> tuple[int, str]:
        assert pr.SINGLE_GAP_MARKER in prompt
        calls["n"] += 1
        if calls["n"] == 1:
            sha = _commit(repo, f"docs/gap{calls['n']}.md", "gap 1")
            return 0, f"PARITY_RESULT: COMMITTED {sha}\n"
        return 0, "PARITY_RESULT: NO_GAPS\n"

    hooks = _ok_ledger_hooks(run_gap=run_gap)
    code = pr.run(_opts(repo, tmp_path), hooks, clock=_clock_seq([0.0, 1.0, 2.0, 3.0]))
    assert code == 0
    assert calls["n"] == 2
    runlog = (tmp_path / "runs" / "run-L1" / "run.jsonl").read_text(encoding="utf-8")
    assert "gap.completed" in runlog


def test_run_stops_on_unverified_committed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    hooks = _ok_ledger_hooks(run_gap=lambda *a: (0, "PARITY_RESULT: COMMITTED deadbeef\n"))
    code = pr.run(_opts(repo, tmp_path), hooks, clock=_clock_seq([0.0, 1.0]))
    assert code == 1


def test_run_blocks_duplicate_label(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".gitignore").write_text("runs/*\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore runs")
    opts = _opts(repo, tmp_path, run_dir=repo / "runs")
    (repo / "runs" / "run-L1").mkdir(parents=True)
    hooks = _ok_ledger_hooks(run_gap=lambda *a: (0, "PARITY_RESULT: NO_GAPS\n"))
    code = pr.run(opts, hooks, clock=_clock_seq([0.0]))
    assert code == 2


def test_run_preflight_backlog_exit3_zero_gaps(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    gap_calls = {"n": 0}

    def run_gap(*a: Any) -> tuple[int, str]:
        gap_calls["n"] += 1
        return 0, "PARITY_RESULT: NO_GAPS\n"

    hooks = _ok_ledger_hooks(
        run_gap=run_gap,
        run_improve=lambda *a: 0,
        ledger_open_count=lambda _r: 1,
    )
    code = pr.run(_opts(repo, tmp_path), hooks, clock=_clock_seq([0.0, 1.0]))
    assert code == 3
    assert gap_calls["n"] == 0


def test_run_stops_at_max_gaps_cap(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    calls = {"n": 0}

    def run_gap(prompt: str, timeout: float, log_path: Path) -> tuple[int, str]:
        calls["n"] += 1
        sha = _commit(repo, f"docs/gap{calls['n']}.md", f"gap {calls['n']}")
        return 0, f"PARITY_RESULT: COMMITTED {sha}\n"

    hooks = _ok_ledger_hooks(run_gap=run_gap)
    code = pr.run(
        _opts(repo, tmp_path, max_gaps=2),
        hooks,
        clock=_clock_seq([0.0, 1.0, 2.0, 3.0, 4.0]),
    )
    assert code == 0
    assert calls["n"] == 2
    runlog = (tmp_path / "runs" / "run-L1" / "run.jsonl").read_text(encoding="utf-8")
    assert '"stop_reason": "cap_reached"' in runlog


def test_run_stops_at_time_budget_cap(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    calls = {"n": 0}

    def run_gap(prompt: str, timeout: float, log_path: Path) -> tuple[int, str]:
        calls["n"] += 1
        sha = _commit(repo, "docs/gap1.md", "gap 1")
        return 0, f"PARITY_RESULT: COMMITTED {sha}\n"

    hooks = _ok_ledger_hooks(run_gap=run_gap)
    code = pr.run(
        _opts(repo, tmp_path, time_budget=1000.0, min_gap_slice=600.0),
        hooks,
        clock=_clock_seq([0.0, 1.0, 2.0, 500.0, 501.0]),
    )
    assert code == 0
    assert calls["n"] == 1
    runlog = (tmp_path / "runs" / "run-L1" / "run.jsonl").read_text(encoding="utf-8")
    assert '"stop_reason": "cap_reached"' in runlog


def test_run_blocked_records_human_cleanup(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    hooks = _ok_ledger_hooks(
        run_gap=lambda *a: (0, "PARITY_RESULT: BLOCKED reviewer unavailable\n")
    )
    code = pr.run(_opts(repo, tmp_path), hooks, clock=_clock_seq([0.0, 1.0]))
    assert code == 1
    runlog = (tmp_path / "runs" / "run-L1" / "run.jsonl").read_text(encoding="utf-8")
    assert '"needs_human_cleanup": true' in runlog


def test_run_postloop_backlog_exit3_after_improve(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    state = {"open": 0, "improve_calls": 0}

    def run_gap(prompt: str, timeout: float, log_path: Path) -> tuple[int, str]:
        state["open"] = 2
        return 0, "PARITY_RESULT: NO_GAPS\n"

    def run_improve(prompt: str, timeout: float, log_path: Path) -> int:
        assert pr.UNATTENDED_MARKER in prompt
        state["improve_calls"] += 1
        state["open"] = 1
        return 0

    hooks = _ok_ledger_hooks(
        run_gap=run_gap,
        run_improve=run_improve,
        ledger_open_count=lambda _r: state["open"],
    )
    code = pr.run(_opts(repo, tmp_path), hooks, clock=_clock_seq([0.0, 1.0, 2.0, 3.0]))
    assert code == 3
    assert state["improve_calls"] == 1
    runlog = (tmp_path / "runs" / "run-L1" / "run.jsonl").read_text(encoding="utf-8")
    assert "needs_human_review" in runlog


def test_cli_dry_run(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "docs" / "pi-mono-gap-audit.md").write_text("# gaps\n", encoding="utf-8")
    (repo / "docs" / "backlog.md").write_text("# backlog\n", encoding="utf-8")
    (repo / ".gitignore").write_text("docs/parity-loop/runs/*\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "docs/pi-mono-gap-audit.md", "docs/backlog.md")
    _git(repo, "commit", "-q", "-m", "ignore runs")
    result = subprocess.run(
        [
            "python3",
            str(_MOD_PATH),
            "--repo",
            str(repo),
            "--run-dir",
            str(repo / "docs/parity-loop/runs"),
            "--run-label",
            "DRY1",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_cli_dry_run_requires_gap_docs(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".gitignore").write_text("docs/parity-loop/runs/*\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore runs")
    result = subprocess.run(
        [
            "python3",
            str(_MOD_PATH),
            "--repo",
            str(repo),
            "--run-dir",
            str(repo / "docs/parity-loop/runs"),
            "--run-label",
            "DRY1",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


def test_cli_rejects_bad_label(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    result = subprocess.run(
        [
            "python3",
            str(_MOD_PATH),
            "--repo",
            str(repo),
            "--run-label",
            "bad/label",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


def test_agent_cmd_uses_codex_exec_adapter() -> None:
    assert pr._agent_cmd("codex") == [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
    ]
    assert pr._agent_cmd("claude") == ["claude", "-p", "--model", "opus"]
