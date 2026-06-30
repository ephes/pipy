"""Unit + integration tests for the parity runner (fake agent, tmp git repo)."""

from __future__ import annotations

import importlib.util
import json
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
        assert "--signed-off-by standing-human" in prompt
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


def _run_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_run_events(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )


def _ok_ledger_hooks(**over: Any) -> Any:
    base: dict[str, Any] = {
        "run_gap": lambda *a: (0, ""),
        "run_improve": lambda *a: 0,
        "ledger_validate": lambda _r: 0,
        "ledger_open_count": lambda _r: 0,
    }
    base.update(over)
    return pr.Hooks(**base)


def test_spawn_capture_closes_child_stdin(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class FakeProc:
        pid = 999999
        returncode = 0

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            return ("ok\n", "")

    def fake_popen(cmd: list[str], **kwargs: Any) -> FakeProc:
        captured["cmd"] = cmd
        captured.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(pr.subprocess, "Popen", fake_popen)

    rc, out = pr._spawn_capture(["fake-agent"], tmp_path, 10.0, tmp_path / "agent.log")

    assert rc == 0
    assert out == "ok\n"
    assert captured["stdin"] is pr.subprocess.DEVNULL


def test_real_agent_prompt_is_delimited(monkeypatch: Any, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_spawn(cmd: list[str], cwd: Path, timeout: float, log_path: Path) -> tuple[int, str]:
        calls.append(cmd)
        return 0, ""

    monkeypatch.setattr(pr, "_spawn_capture", fake_spawn)

    pr._real_run_gap(tmp_path, "claude")("gap prompt", 10.0, tmp_path / "gap.log")
    rc = pr._real_run_improve(tmp_path, "claude")("improve prompt", 10.0, tmp_path / "improve.log")
    pr._real_run_gap(tmp_path, "opus")("opus prompt", 10.0, tmp_path / "opus.log")
    pr._real_run_gap(tmp_path, "pipy")("pipy prompt", 10.0, tmp_path / "pipy.log")

    assert rc == 0
    assert calls[0][-2:] == ["--", "gap prompt"]
    assert calls[1][-2:] == ["--", "improve prompt"]
    assert "--dangerously-skip-permissions" in calls[0]
    assert calls[2] == [
        "fish",
        "-lc",
        (
            'set args $argv; if test (count $args) -gt 0; and test "$args[1]" = "--"; '
            "set args $args[2..-1]; end; claude-yolo -p --model opus -- $args"
        ),
        "--",
        "opus prompt",
    ]
    assert calls[3] == [
        "uv",
        "run",
        "pipy",
        "--tool-budget",
        "200",
        "-p",
        "--",
        "pipy prompt",
    ]


def test_run_stops_on_no_gaps(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    hooks = _ok_ledger_hooks(run_gap=lambda *a: (0, "PARITY_RESULT: NO_GAPS\n"))
    code = pr.run(_opts(repo, tmp_path), hooks, clock=_clock_seq([0.0, 1.0]))
    assert code == 0


def test_run_records_remote_tracking_ref_audit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    remote_main = pr.head(repo)
    _git(repo, "update-ref", "refs/remotes/origin/main", remote_main)
    hooks = _ok_ledger_hooks(run_gap=lambda *a: (0, "PARITY_RESULT: NO_GAPS\n"))

    code = pr.run(_opts(repo, tmp_path), hooks, clock=_clock_seq([0.0, 1.0]))

    assert code == 0
    events = _run_events(tmp_path / "runs" / "run-L1" / "run.jsonl")
    started = next(event for event in events if event["type"] == "run.started")
    finished = next(event for event in events if event["type"] == "run.finished")
    assert started["remote_tracking_before"] == {"refs/remotes/origin/main": remote_main}
    assert finished["remote_tracking_before"] == {"refs/remotes/origin/main": remote_main}
    assert finished["remote_tracking_after"] == {"refs/remotes/origin/main": remote_main}
    assert finished["remote_tracking_changed"] is False


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
    events = _run_events(tmp_path / "runs" / "run-L1" / "run.jsonl")
    completed = next(event for event in events if event["type"] == "gap.completed")
    assert completed["head_before"]
    assert completed["head_after"] == _git(repo, "rev-parse", "HEAD")


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


def test_run_classifies_pipy_provider_failure_as_blocked(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    def run_gap(_prompt: str, _timeout: float, log_path: Path) -> tuple[int, str]:
        log_path.write_text(
            "pipy: provider failure during turn: "
            "OpenAICodexResponseParseError: OpenAI Codex stream returned an error event. "
            "(response_status=unknown)\n",
            encoding="utf-8",
        )
        return 1, ""

    hooks = _ok_ledger_hooks(run_gap=run_gap)

    code = pr.run(_opts(repo, tmp_path), hooks, clock=_clock_seq([0.0, 1.0]))

    assert code == 1
    events = _run_events(tmp_path / "runs" / "run-L1" / "run.jsonl")
    failed = next(event for event in events if event["type"] == "gap.failed")
    assert failed["reason"] == "blocked:provider_failure"
    finished = next(event for event in events if event["type"] == "run.finished")
    assert finished["stop_reason"] == "blocked:provider_failure"


def test_run_does_not_classify_earlier_recovered_provider_failure(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    def run_gap(_prompt: str, _timeout: float, log_path: Path) -> tuple[int, str]:
        log_path.write_text(
            "pipy: provider failure during turn: transient\n"
            + "\n".join(f"later unrelated failure context {idx}" for idx in range(25))
            + "\n",
            encoding="utf-8",
        )
        return 1, ""

    hooks = _ok_ledger_hooks(run_gap=run_gap)

    code = pr.run(_opts(repo, tmp_path), hooks, clock=_clock_seq([0.0, 1.0]))

    assert code == 1
    events = _run_events(tmp_path / "runs" / "run-L1" / "run.jsonl")
    failed = next(event for event in events if event["type"] == "gap.failed")
    assert failed["reason"] == "failure"


def test_run_postloop_backlog_exit3_after_improve(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    state = {"open": 0, "improve_calls": 0}

    def run_gap(prompt: str, timeout: float, log_path: Path) -> tuple[int, str]:
        state["open"] = 2
        return 0, "PARITY_RESULT: NO_GAPS\n"

    def run_improve(prompt: str, timeout: float, log_path: Path) -> int:
        assert pr.UNATTENDED_MARKER in prompt
        assert "--signed-off-by standing-human" in prompt
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


def test_run_postloop_surfaces_improve_child_caveats(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    state = {"open": 0}

    def run_gap(prompt: str, timeout: float, log_path: Path) -> tuple[int, str]:
        state["open"] = 1
        return 0, "PARITY_RESULT: NO_GAPS\n"

    def run_improve(prompt: str, timeout: float, log_path: Path) -> int:
        state["open"] = 0
        log_path.write_text(
            "Applied harness lesson.\n"
            "Remaining open lessons: none.\n"
            "[error] tool reported a failure\n"
            "Verification incomplete: full gate did not run before timeout.\n",
            encoding="utf-8",
        )
        return 0

    hooks = _ok_ledger_hooks(
        run_gap=run_gap,
        run_improve=run_improve,
        ledger_open_count=lambda _r: state["open"],
    )

    code = pr.run(_opts(repo, tmp_path), hooks, clock=_clock_seq([0.0, 1.0, 2.0, 3.0]))

    assert code == 0
    events = _run_events(tmp_path / "runs" / "run-L1" / "run.jsonl")
    caveat = next(event for event in events if event["type"] == "safety_net_child_caveats")
    assert caveat["phase"] == "postloop"
    assert caveat["log_path"] == "improve-postloop.log"
    assert caveat["caveats"] == ["Verification incomplete: full gate did not run before timeout."]
    completed = next(event for event in events if event["type"] == "safety_net_completed")
    assert completed["phase"] == "postloop"
    assert completed["open_before"] == 1
    assert completed["open_after"] == 0
    assert completed["commits"] == []


def test_improve_log_caveats_uses_explicit_prefix_contract(tmp_path: Path) -> None:
    log_path = tmp_path / "improve.log"
    log_path.write_text(
        "Remaining open lessons: none.\n"
        "[error] tool reported a failure\n"
        "Caveat: review was narrower than usual.\n"
        "- Blocked: human sign-off required.\n"
        "> Failed: targeted gate failed.\n"
        "Incomplete: full gate did not run.\n",
        encoding="utf-8",
    )

    assert pr.improve_log_caveats(log_path) == [
        "Caveat: review was narrower than usual.",
        "Blocked: human sign-off required.",
        "Failed: targeted gate failed.",
        "Incomplete: full gate did not run.",
    ]


def test_run_postloop_records_safety_net_commits(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    state = {"open": 0}

    def run_gap(prompt: str, timeout: float, log_path: Path) -> tuple[int, str]:
        state["open"] = 1
        return 0, "PARITY_RESULT: NO_GAPS\n"

    def run_improve(prompt: str, timeout: float, log_path: Path) -> int:
        state["open"] = 0
        _commit(repo, "tests/regression.txt", "test(native): cover regression")
        log_path.write_text("Applied harness lesson.\n", encoding="utf-8")
        return 0

    hooks = _ok_ledger_hooks(
        run_gap=run_gap,
        run_improve=run_improve,
        ledger_open_count=lambda _r: state["open"],
    )

    code = pr.run(_opts(repo, tmp_path), hooks, clock=_clock_seq([0.0, 1.0, 2.0, 3.0]))

    assert code == 0
    events = _run_events(tmp_path / "runs" / "run-L1" / "run.jsonl")
    completed = next(event for event in events if event["type"] == "safety_net_completed")
    assert completed["phase"] == "postloop"
    assert completed["log_path"] == "improve-postloop.log"
    assert completed["open_before"] == 1
    assert completed["open_after"] == 0
    assert completed["head_before"] != completed["head_after"]
    assert completed["commits"] == [
        {"sha": completed["head_after"][:7], "subject": "test(native): cover regression"}
    ]


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
    assert "required gap docs are missing" in result.stderr


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
    assert "invalid run label" in result.stderr


def test_cli_preflight_reports_dirty_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "docs" / "pi-mono-gap-audit.md").write_text("# gaps\n", encoding="utf-8")
    (repo / "docs" / "backlog.md").write_text("# backlog\n", encoding="utf-8")
    (repo / ".gitignore").write_text("docs/parity-loop/runs/*\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "docs/pi-mono-gap-audit.md", "docs/backlog.md")
    _git(repo, "commit", "-q", "-m", "ignore runs")
    (repo / "scratch.md").write_text("dirty\n", encoding="utf-8")

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
    assert "worktree is not clean" in result.stderr


def test_agent_cmd_uses_codex_exec_adapter() -> None:
    assert pr._agent_cmd("opus") == [
        "fish",
        "-lc",
        (
            'set args $argv; if test (count $args) -gt 0; and test "$args[1]" = "--"; '
            "set args $args[2..-1]; end; claude-yolo -p --model opus -- $args"
        ),
    ]
    assert pr._agent_cmd("codex") == [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
    ]
    assert pr._agent_cmd("claude") == [
        "claude",
        "-p",
        "--model",
        "opus",
        "--dangerously-skip-permissions",
    ]
    assert pr._agent_cmd("pipy") == [
        "uv",
        "run",
        "pipy",
        "--tool-budget",
        "200",
        "-p",
    ]


def test_generate_slice_report_pins_recorded_sha_not_live_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    start = pr.head(repo)
    gap_sha = _commit(repo, "docs/gap.md", "feat(demo): add gap")
    short_gap = gap_sha[:7]
    _commit(repo, "docs/later.md", "feat(demo): later unrelated")
    run_dir = tmp_path / "runs"
    report_dir = tmp_path / "reports"
    _write_run_events(
        run_dir / "run-L1" / "run.jsonl",
        [
            {
                "type": "run.started",
                "agent": "codex",
                "head_before": start,
                "max_gaps": 1,
            },
            {"type": "gap.completed", "index": 1, "sha": short_gap},
            {
                "type": "run.finished",
                "exit_code": 0,
                "gaps_done": 1,
                "stop_reason": "cap_reached",
            },
        ],
    )

    report = pr.generate_slice_report(repo, run_dir, report_dir, label="L1")
    text = report.read_text(encoding="utf-8")

    assert "feat(demo): add gap" in text
    assert "feat(demo): later unrelated" not in text
    assert f"`{gap_sha[:12]}`" in text


def test_generate_slice_report_uses_head_after_for_multi_commit_gap(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    start = pr.head(repo)
    cited_sha = _commit(repo, "docs/first.md", "feat(demo): first commit")
    head_after = _commit(repo, "docs/second.md", "test(demo): second commit")
    run_dir = tmp_path / "runs"
    report_dir = tmp_path / "reports"
    _write_run_events(
        run_dir / "run-L1" / "run.jsonl",
        [
            {"type": "run.started", "agent": "codex", "head_before": start},
            {
                "type": "gap.completed",
                "index": 1,
                "sha": cited_sha[:7],
                "head_after": head_after,
            },
            {
                "type": "run.finished",
                "exit_code": 0,
                "gaps_done": 1,
                "stop_reason": "cap_reached",
            },
        ],
    )

    report = pr.generate_slice_report(repo, run_dir, report_dir, label="L1")
    text = report.read_text(encoding="utf-8")

    assert "feat(demo): first commit" in text
    assert "test(demo): second commit" in text
    assert "docs/second.md" in text
    assert f"`{head_after[:12]}`" in text


def test_generate_slice_report_refuses_incomplete_or_failed_runs(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    start = pr.head(repo)
    run_dir = tmp_path / "runs"
    report_dir = tmp_path / "reports"
    _write_run_events(
        run_dir / "run-incomplete" / "run.jsonl",
        [{"type": "run.started", "agent": "codex", "head_before": start}],
    )
    _write_run_events(
        run_dir / "run-failed" / "run.jsonl",
        [
            {"type": "run.started", "agent": "codex", "head_before": start},
            {"type": "run.finished", "exit_code": 1, "gaps_done": 0, "stop_reason": "failure"},
        ],
    )

    for label in ("incomplete", "failed"):
        try:
            pr.generate_slice_report(repo, run_dir, report_dir, label=label)
        except pr.ReportError:
            pass
        else:
            raise AssertionError(f"{label} run should not produce a report")

    assert not report_dir.exists()


def test_generate_slice_report_refuses_incomplete_sentinel_block(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    start = pr.head(repo)
    gap_sha = _commit(repo, "docs/gap.md", "feat(demo): add gap")
    run_dir = tmp_path / "runs"
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    existing = report_dir / "custom-name.md"
    original = (
        "# Hand Report\n\n"
        "<!-- parity-run-label: L1 -->\n\n"
        "<!-- BEGIN GENERATED:facts -->\n"
        "old facts\n"
    )
    existing.write_text(original, encoding="utf-8")
    _write_run_events(
        run_dir / "run-L1" / "run.jsonl",
        [
            {"type": "run.started", "agent": "codex", "head_before": start},
            {"type": "gap.completed", "index": 1, "sha": gap_sha},
            {
                "type": "run.finished",
                "exit_code": 0,
                "gaps_done": 1,
                "stop_reason": "cap_reached",
            },
        ],
    )

    try:
        pr.generate_slice_report(repo, run_dir, report_dir, label="L1")
    except pr.ReportError:
        pass
    else:
        raise AssertionError("incomplete generated block should be refused")

    assert existing.read_text(encoding="utf-8") == original


def test_generate_slice_report_appends_facts_when_existing_report_has_no_sentinel(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    start = pr.head(repo)
    gap_sha = _commit(repo, "docs/gap.md", "feat(demo): add gap")
    run_dir = tmp_path / "runs"
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    existing = report_dir / "custom-name.md"
    existing.write_text(
        "# Hand Report\n\n"
        "<!-- parity-run-label: L1 -->\n\n"
        "Human-written body with no generated block.\n",
        encoding="utf-8",
    )
    _write_run_events(
        run_dir / "run-L1" / "run.jsonl",
        [
            {"type": "run.started", "agent": "codex", "head_before": start},
            {"type": "gap.completed", "index": 1, "sha": gap_sha},
            {
                "type": "run.finished",
                "exit_code": 0,
                "gaps_done": 1,
                "stop_reason": "cap_reached",
            },
        ],
    )

    report = pr.generate_slice_report(repo, run_dir, report_dir, label="L1")
    text = report.read_text(encoding="utf-8")

    assert report == existing
    assert "Human-written body with no generated block." in text
    assert "<!-- BEGIN GENERATED:facts -->" in text


def test_generate_slice_report_regenerates_only_sentinel_block(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    start = pr.head(repo)
    gap_sha = _commit(repo, "src/demo.py", "feat(demo): add source")
    run_dir = tmp_path / "runs"
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    existing = report_dir / "custom-name.md"
    existing.write_text(
        "# Hand Report\n\n"
        "<!-- parity-run-label: L1 -->\n\n"
        "<!-- BEGIN GENERATED:facts -->\n"
        "old facts\n"
        "<!-- END GENERATED:facts -->\n\n"
        "## Visualization\n\n"
        "```mermaid\n"
        "flowchart TD\n"
        "A --> B\n"
        "```\n\n"
        "<!-- harmless comment outside generated block -->\n",
        encoding="utf-8",
    )
    _write_run_events(
        run_dir / "run-L1" / "run.jsonl",
        [
            {"type": "run.started", "agent": "codex", "head_before": start},
            {"type": "gap.completed", "index": 1, "sha": gap_sha[:8]},
            {
                "type": "run.finished",
                "exit_code": 0,
                "gaps_done": 1,
                "stop_reason": "cap_reached",
            },
        ],
    )

    first = pr.generate_slice_report(repo, run_dir, report_dir, label="L1")
    first_text = first.read_text(encoding="utf-8")
    second = pr.generate_slice_report(repo, run_dir, report_dir, label="L1")
    second_text = second.read_text(encoding="utf-8")

    assert first == existing
    assert second == existing
    assert first_text == second_text
    assert "old facts" not in second_text
    assert "```mermaid\nflowchart TD\nA --> B\n```" in second_text
    assert "<!-- harmless comment outside generated block -->" in second_text


def test_generate_slice_report_handles_zero_gap_run(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    start = pr.head(repo)
    run_dir = tmp_path / "runs"
    report_dir = tmp_path / "reports"
    _write_run_events(
        run_dir / "run-L1" / "run.jsonl",
        [
            {"type": "run.started", "agent": "codex", "head_before": start},
            {
                "type": "run.finished",
                "exit_code": 0,
                "gaps_done": 0,
                "stop_reason": "no_gaps",
            },
        ],
    )

    report = pr.generate_slice_report(repo, run_dir, report_dir, label="L1")
    text = report.read_text(encoding="utf-8")

    assert "No commits were recorded for this run." in text
    assert "No changed files were recorded for this run." in text
    assert pr.REPORT_FACTS_ONLY_MARKER in text


def test_curate_slice_report_invokes_agent_and_preserves_facts(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    report = repo / "docs" / "report.md"
    report.parent.mkdir()
    original = (
        "# Report\n\n"
        "<!-- parity-run-label: L1 -->\n\n"
        "<!-- BEGIN GENERATED:facts -->\n"
        "facts\n"
        "<!-- END GENERATED:facts -->\n\n"
        "## What Changed\n\n"
        f"{pr.REPORT_FACTS_ONLY_MARKER} Replace this.\n\n"
    )
    report.write_text(original, encoding="utf-8")
    calls: list[dict[str, Any]] = []

    def fake_spawn(cmd: list[str], cwd: Path, timeout: float, log_path: Path) -> tuple[int, str]:
        calls.append({"cmd": cmd, "cwd": cwd, "timeout": timeout, "log_path": log_path})
        assert "report.md" in cmd[-1]
        log_path.write_text("curation log\n", encoding="utf-8")
        report.write_text(
            original.replace(
                f"{pr.REPORT_FACTS_ONLY_MARKER} Replace this.",
                "This slice exposes the behavior in plain language.",
            ),
            encoding="utf-8",
        )
        return (0, "REPORT_CURATION: OK\n")

    monkeypatch.setattr(pr, "_spawn_capture", fake_spawn)

    pr.curate_slice_report(repo, tmp_path / "runs", report, agent="fake-agent", timeout=12.0)

    curated = report.read_text(encoding="utf-8")
    assert pr.REPORT_FACTS_ONLY_MARKER not in curated
    assert "<!-- BEGIN GENERATED:facts -->\nfacts\n<!-- END GENERATED:facts -->" in curated
    assert calls == [
        {
            "cmd": ["fake-agent", "-p", "--", pr._report_curation_prompt(repo, report)],
            "cwd": repo,
            "timeout": 12.0,
            "log_path": report.with_suffix(".curation.log"),
        }
    ]


def test_curate_slice_report_fails_when_placeholder_remains(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    report = tmp_path / "report.md"
    report.write_text(
        "<!-- BEGIN GENERATED:facts -->\nfacts\n<!-- END GENERATED:facts -->\n\n"
        f"{pr.REPORT_FACTS_ONLY_MARKER}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(pr, "_spawn_capture", lambda *a: (0, "REPORT_CURATION: OK\n"))

    try:
        pr.curate_slice_report(repo, tmp_path / "runs", report, agent="fake-agent", timeout=12.0)
    except pr.ReportError as exc:
        assert "left the generated-facts-only placeholder" in str(exc)
    else:
        raise AssertionError("curation should fail when the placeholder remains")


def test_curate_slice_report_fails_when_agent_changes_generated_facts(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    report = tmp_path / "report.md"
    report.write_text(
        "<!-- BEGIN GENERATED:facts -->\nfacts\n<!-- END GENERATED:facts -->\n\n"
        f"{pr.REPORT_FACTS_ONLY_MARKER}\n",
        encoding="utf-8",
    )

    def fake_spawn(*_args: Any) -> tuple[int, str]:
        report.write_text(
            "<!-- BEGIN GENERATED:facts -->\nchanged\n<!-- END GENERATED:facts -->\n\n"
            "Curated.\n",
            encoding="utf-8",
        )
        return (0, "REPORT_CURATION: OK\n")

    monkeypatch.setattr(pr, "_spawn_capture", fake_spawn)

    try:
        pr.curate_slice_report(repo, tmp_path / "runs", report, agent="fake-agent", timeout=12.0)
    except pr.ReportError as exc:
        assert "changed the generated facts block" in str(exc)
    else:
        raise AssertionError("curation should fail when generated facts change")


def test_curate_slice_report_fails_when_agent_exits_nonzero(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    report = tmp_path / "report.md"
    report.write_text(
        "<!-- BEGIN GENERATED:facts -->\nfacts\n<!-- END GENERATED:facts -->\n\n"
        f"{pr.REPORT_FACTS_ONLY_MARKER}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(pr, "_spawn_capture", lambda *a: (7, "failed\n"))

    try:
        pr.curate_slice_report(repo, tmp_path / "runs", report, agent="fake-agent", timeout=12.0)
    except pr.ReportError as exc:
        assert "failed with exit code 7" in str(exc)
    else:
        raise AssertionError("curation should fail when the agent exits nonzero")


def test_curate_slice_report_fails_when_agent_changes_other_files(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    report = repo / "docs" / "report.md"
    report.parent.mkdir()
    original = (
        "<!-- BEGIN GENERATED:facts -->\nfacts\n<!-- END GENERATED:facts -->\n\n"
        f"{pr.REPORT_FACTS_ONLY_MARKER}\n"
    )
    report.write_text(original, encoding="utf-8")

    def fake_spawn(_cmd: list[str], _cwd: Path, _timeout: float, log_path: Path) -> tuple[int, str]:
        log_path.write_text("curation log\n", encoding="utf-8")
        report.write_text(original.replace(pr.REPORT_FACTS_ONLY_MARKER, "Curated."), encoding="utf-8")
        (repo / "other.txt").write_text("unexpected\n", encoding="utf-8")
        return (0, "REPORT_CURATION: OK\n")

    monkeypatch.setattr(pr, "_spawn_capture", fake_spawn)

    try:
        pr.curate_slice_report(repo, tmp_path / "runs", report, agent="fake-agent", timeout=12.0)
    except pr.ReportError as exc:
        assert "changed files other than the report" in str(exc)
    else:
        raise AssertionError("curation should fail when another file changes")


def test_curate_slice_report_fails_when_agent_moves_head(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    report = repo / "docs" / "report.md"
    report.parent.mkdir()
    original = (
        "<!-- BEGIN GENERATED:facts -->\nfacts\n<!-- END GENERATED:facts -->\n\n"
        f"{pr.REPORT_FACTS_ONLY_MARKER}\n"
    )
    report.write_text(original, encoding="utf-8")

    def fake_spawn(_cmd: list[str], _cwd: Path, _timeout: float, log_path: Path) -> tuple[int, str]:
        log_path.write_text("curation log\n", encoding="utf-8")
        report.write_text(original.replace(pr.REPORT_FACTS_ONLY_MARKER, "Curated."), encoding="utf-8")
        _git(repo, "add", str(report.relative_to(repo)))
        _git(repo, "commit", "-q", "-m", "unexpected curation commit")
        return (0, "REPORT_CURATION: OK\n")

    monkeypatch.setattr(pr, "_spawn_capture", fake_spawn)

    try:
        pr.curate_slice_report(repo, tmp_path / "runs", report, agent="fake-agent", timeout=12.0)
    except pr.ReportError as exc:
        assert "moved HEAD" in str(exc)
    else:
        raise AssertionError("curation should fail when HEAD moves")


def test_curate_slice_report_fails_without_complete_generated_block(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    report = tmp_path / "report.md"
    report.write_text(f"{pr.REPORT_FACTS_ONLY_MARKER}\n", encoding="utf-8")

    try:
        pr.curate_slice_report(repo, tmp_path / "runs", report, agent="fake-agent", timeout=12.0)
    except pr.ReportError as exc:
        assert "no complete generated facts block" in str(exc)
    else:
        raise AssertionError("curation should fail without a generated facts block")


def test_curate_slice_report_skips_already_curated_report(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    report = tmp_path / "report.md"
    report.write_text(
        "<!-- BEGIN GENERATED:facts -->\nfacts\n<!-- END GENERATED:facts -->\n\n"
        "Curated.\n",
        encoding="utf-8",
    )
    spawned = {"value": False}

    def fake_spawn(*_args: Any) -> tuple[int, str]:
        spawned["value"] = True
        return (0, "")

    monkeypatch.setattr(pr, "_spawn_capture", fake_spawn)

    pr.curate_slice_report(repo, tmp_path / "runs", report, agent="fake-agent", timeout=12.0)

    assert spawned["value"] is False


def test_generate_slice_report_includes_safety_net_commit_table(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    start = pr.head(repo)
    gap_sha = _commit(repo, "src/gap.py", "feat(demo): add gap")
    safety_start = gap_sha
    safety_sha = _commit(repo, "tests/gap_test.py", "test(demo): cover lesson")
    run_dir = tmp_path / "runs"
    report_dir = tmp_path / "reports"
    _write_run_events(
        run_dir / "run-L1" / "run.jsonl",
        [
            {"type": "run.started", "agent": "codex", "head_before": start},
            {
                "type": "gap.completed",
                "index": 1,
                "sha": gap_sha,
                "head_before": start,
                "head_after": gap_sha,
            },
            {
                "type": "safety_net_completed",
                "phase": "postloop",
                "log_path": "improve-postloop.log",
                "exit_code": 0,
                "head_before": safety_start,
                "head_after": safety_sha,
                "open_before": 1,
                "open_after": 0,
                "commits": [
                    {
                        "sha": safety_sha[:7],
                        "subject": "test(demo): cover lesson",
                    }
                ],
            },
            {
                "type": "run.finished",
                "exit_code": 0,
                "gaps_done": 1,
                "stop_reason": "cap_reached",
            },
        ],
    )

    report = pr.generate_slice_report(repo, run_dir, report_dir, label="L1")
    text = report.read_text(encoding="utf-8")

    assert "| `feat(demo): add gap`" not in text
    assert "### Lesson Safety Net" in text
    assert "postloop" in text
    assert "improve-postloop.log" in text
    assert "`" + safety_sha[:7] + "` test(demo): cover lesson" in text


def test_generate_slice_report_separates_preflight_safety_net_from_gap_range(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    start = pr.head(repo)
    preflight_sha = _commit(repo, "tests/preflight.txt", "test(demo): preflight lesson")
    gap_sha = _commit(repo, "src/gap.py", "feat(demo): add gap")
    run_dir = tmp_path / "runs"
    report_dir = tmp_path / "reports"
    _write_run_events(
        run_dir / "run-L1" / "run.jsonl",
        [
            {"type": "run.started", "agent": "codex", "head_before": start},
            {
                "type": "safety_net_completed",
                "phase": "preflight",
                "log_path": "improve-preflight.log",
                "exit_code": 0,
                "head_before": start,
                "head_after": preflight_sha,
                "open_before": 1,
                "open_after": 0,
                "commits": [
                    {
                        "sha": preflight_sha[:7],
                        "subject": "test(demo): preflight lesson",
                    }
                ],
            },
            {
                "type": "gap.completed",
                "index": 1,
                "sha": gap_sha,
                "head_before": preflight_sha,
                "head_after": gap_sha,
            },
            {
                "type": "run.finished",
                "exit_code": 0,
                "gaps_done": 1,
                "stop_reason": "cap_reached",
            },
        ],
    )

    report = pr.generate_slice_report(repo, run_dir, report_dir, label="L1")
    text = report.read_text(encoding="utf-8")
    range_section = text.split("### Recorded Range Commits", 1)[1].split(
        "### Change Shape", 1
    )[0]
    files_section = text.split("### Changed Files", 1)[1].split(
        "### Lesson Safety Net", 1
    )[0]

    assert "feat(demo): add gap" in range_section
    assert "test(demo): preflight lesson" not in range_section
    assert "src/gap.py" in files_section
    assert "tests/preflight.txt" not in files_section
    assert "preflight" in text
    assert "`" + preflight_sha[:7] + "` test(demo): preflight lesson" in text


def test_generate_slice_report_handles_legacy_safety_net_event_without_heads(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    start = pr.head(repo)
    run_dir = tmp_path / "runs"
    report_dir = tmp_path / "reports"
    _write_run_events(
        run_dir / "run-L1" / "run.jsonl",
        [
            {"type": "run.started", "agent": "codex", "head_before": start},
            {
                "type": "safety_net_completed",
                "phase": "postloop",
                "log_path": "improve-postloop.log",
                "exit_code": 0,
                "open_before": 1,
                "open_after": 0,
                "commits": [],
            },
            {
                "type": "run.finished",
                "exit_code": 0,
                "gaps_done": 0,
                "stop_reason": "no_gaps",
            },
        ],
    )

    report = pr.generate_slice_report(repo, run_dir, report_dir, label="L1")
    text = report.read_text(encoding="utf-8")

    assert "| postloop | improve-postloop.log | `-` | `-` | 0 | 1 | 0 | No commits. |" in text


def test_report_slice_cli_writes_latest_report(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    start = pr.head(repo)
    gap_sha = _commit(repo, "docs/gap.md", "feat(demo): cli report")
    run_dir = tmp_path / "runs"
    report_dir = tmp_path / "reports"
    _write_run_events(
        run_dir / "run-L1" / "run.jsonl",
        [
            {"type": "run.started", "agent": "codex", "head_before": start},
            {"type": "gap.completed", "index": 1, "sha": gap_sha},
            {
                "type": "run.finished",
                "exit_code": 0,
                "gaps_done": 1,
                "stop_reason": "cap_reached",
            },
        ],
    )

    result = subprocess.run(
        [
            "python3",
            str(_MOD_PATH),
            "--repo",
            str(repo),
            "--run-dir",
            str(run_dir),
            "--report-dir",
            str(report_dir),
            "--report-slice",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report_path = Path(result.stdout.strip())
    assert report_path.is_file()
    assert "<!-- parity-run-label: L1 -->" in report_path.read_text(encoding="utf-8")
