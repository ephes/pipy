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
SENTINEL_RE = r"^PARITY_RESULT: (COMMITTED \S+|NO_GAPS|BLOCKED .*)$"
SINGLE_GAP_MARKER = "runner single-gap mode"
UNATTENDED_MARKER = "runner unattended mode"
BLOCKED_PUSHURL = "blocked://parity-runner-no-push"
LEDGER_REL = "docs/parity-loop/lessons/lessons.jsonl"
DEFAULTS = {
    "max_gaps": 3,
    "time_budget": 7200,
    "per_gap_timeout": 2400,
    "min_gap_slice": 600,
}
INCOMPLETE_LOCK_GRACE = 30.0


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
    run_gap: Callable[[str, float, Path], tuple[int, str]]
    run_improve: Callable[[str, float, Path], int]
    ledger_validate: Callable[[Path], int]
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
    return cp.returncode == 0 and cp.stdout.strip() == ""


def ref_snapshot(repo: Path) -> dict[str, str]:
    out = _out(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    snap: dict[str, str] = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        name, sha = line.split(" ", 1)
        snap[name] = sha.strip()
    return snap


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return _git(repo, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0


def valid_run_label(label: str) -> bool:
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
        return True
    return _git(repo, "check-ignore", str(target)).returncode == 0


def gap_docs_present(repo: Path) -> bool:
    return (repo / "docs" / "pi-mono-gap-audit.md").is_file() and (
        repo / "docs" / "backlog.md"
    ).is_file()


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
    """Acquire the atomic per-repo lock, reclaiming stale holders."""
    lock = lock_path(repo)
    for _ in range(2):
        try:
            lock.mkdir(parents=False)
            (lock / "pid").write_text(str(os.getpid()), encoding="utf-8")
            return True
        except FileExistsError:
            reclaim = False
            try:
                pid = int((lock / "pid").read_text(encoding="utf-8").strip())
            except (FileNotFoundError, ValueError):
                try:
                    age = time.time() - lock.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age < INCOMPLETE_LOCK_GRACE:
                    return False
                reclaim = True
            else:
                if _pid_alive(pid):
                    return False
                reclaim = True
            if reclaim:
                trash = lock.with_name(f"{lock.name}.stale.{os.getpid()}")
                try:
                    os.rename(lock, trash)
                except OSError:
                    continue
                shutil.rmtree(trash, ignore_errors=True)
    return False


def release_lock(repo: Path) -> None:
    shutil.rmtree(lock_path(repo), ignore_errors=True)


def lock_is_held(repo: Path) -> bool:
    lock = lock_path(repo)
    if not lock.exists():
        return False
    try:
        pid = int((lock / "pid").read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return True
    return _pid_alive(pid)


_PREPUSH_HOOK = (
    "#!/bin/sh\n"
    "# parity-runner: push disabled during unattended run\n"
    'echo "parity-runner: push blocked" >&2\n'
    "exit 1\n"
)


def install_no_push_guards(repo: Path) -> Callable[[], None]:
    """Block naive child pushes; return a restore callable."""
    remotes = [r for r in _out(repo, "remote").splitlines() if r.strip()]
    saved: dict[str, list[str]] = {}
    for remote in remotes:
        urls = _git(repo, "config", "--get-all", f"remote.{remote}.pushurl").stdout.splitlines()
        saved[remote] = [u for u in urls if u.strip() and u.strip() != BLOCKED_PUSHURL]
        _git(repo, "config", "--unset-all", f"remote.{remote}.pushurl")
        _git(repo, "config", "--add", f"remote.{remote}.pushurl", BLOCKED_PUSHURL)

    common = Path(_out(repo, "rev-parse", "--git-common-dir"))
    common = (repo / common).resolve() if not common.is_absolute() else common.resolve()
    hooks_path_cfg = _out(repo, "config", "--get", "core.hooksPath")
    if hooks_path_cfg:
        top = Path(_out(repo, "rev-parse", "--show-toplevel"))
        hooks_dir = Path(hooks_path_cfg)
        hooks_dir = (hooks_dir if hooks_dir.is_absolute() else top / hooks_dir).resolve()
    else:
        hooks_dir = common / "hooks"

    hook_file: Optional[Path] = None
    prev_hook: Optional[str] = None
    prev_mode: Optional[int] = None
    try:
        hooks_dir.relative_to(common)
    except ValueError:
        pass
    else:
        hook_file = hooks_dir / "pre-push"
        hook_file.parent.mkdir(parents=True, exist_ok=True)
        if hook_file.exists():
            existing_hook = hook_file.read_text(encoding="utf-8")
            if existing_hook != _PREPUSH_HOOK:
                prev_hook = existing_hook
                prev_mode = hook_file.stat().st_mode
        hook_file.write_text(_PREPUSH_HOOK, encoding="utf-8")
        hook_file.chmod(0o755)

    def restore() -> None:
        for remote in remotes:
            _git(repo, "config", "--unset-all", f"remote.{remote}.pushurl")
            for url in saved.get(remote, []):
                _git(repo, "config", "--add", f"remote.{remote}.pushurl", url)
        if hook_file is not None:
            if prev_hook is None:
                hook_file.unlink(missing_ok=True)
            else:
                hook_file.write_text(prev_hook, encoding="utf-8")
                if prev_mode is not None:
                    hook_file.chmod(prev_mode & 0o7777)

    return restore


def parse_sentinel(text: str) -> tuple[Optional[str], str]:
    found: tuple[Optional[str], str] = (None, "")
    for line in text.splitlines():
        match = re.match(SENTINEL_RE, line.strip())
        if not match:
            continue
        body = match.group(1)
        if body == "NO_GAPS":
            found = ("NO_GAPS", "")
        elif body.startswith("COMMITTED "):
            found = ("COMMITTED", body[len("COMMITTED ") :].strip())
        elif body.startswith("BLOCKED"):
            found = ("BLOCKED", body[len("BLOCKED") :].strip())
    return found


def only_main_advanced(repo: Path, refs_before: dict[str, str]) -> bool:
    after = ref_snapshot(repo)
    if set(after) != set(refs_before):
        return False
    for name, sha in refs_before.items():
        if name == "refs/heads/main":
            continue
        if after[name] != sha:
            return False
    return True


def _resolve(repo: Path, rev: str) -> Optional[str]:
    cp = _git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}")
    return cp.stdout.strip() if cp.returncode == 0 else None


def verify_committed(
    repo: Path,
    head_before: str,
    refs_before: dict[str, str],
    sha: str,
) -> tuple[bool, str]:
    if current_branch(repo) != "main":
        return False, "not on main after gap"
    if not tree_clean(repo):
        return False, "dirty tree after COMMITTED"
    cur = head(repo)
    if cur == head_before or not is_ancestor(repo, head_before, cur):
        return False, "HEAD did not advance forward from head_before"
    if not only_main_advanced(repo, refs_before):
        return False, "a non-main ref changed"
    resolved = _resolve(repo, sha)
    if resolved is None or resolved == head_before:
        return False, "cited sha does not resolve or equals head_before"
    if not (is_ancestor(repo, head_before, resolved) and is_ancestor(repo, resolved, cur)):
        return False, "cited sha is not within (head_before, HEAD]"
    return True, "ok"


def verify_no_gaps(repo: Path, head_before: str, refs_before: dict[str, str]) -> tuple[bool, str]:
    if current_branch(repo) != "main":
        return False, "not on main"
    if not tree_clean(repo):
        return False, "dirty tree on NO_GAPS"
    if head(repo) != head_before:
        return False, "HEAD moved on NO_GAPS"
    if ref_snapshot(repo) != refs_before:
        return False, "refs changed on NO_GAPS"
    return True, "ok"


def _improve_prompt() -> str:
    return (
        f"Run the `parity-improve` skill in {UNATTENDED_MARKER}, in this repo, on "
        "`main`. Do not push. Apply only lessons gateable without sign-off "
        "(docs/tests/harness); leave instruction-area lessons and rejections open."
    )


def lesson_gate(
    repo: Path,
    phase: str,
    hooks: Hooks,
    *,
    remaining_budget: float,
    min_gap_slice: float,
    per_gap_timeout: float,
    run_dir: Path,
    log: Callable[..., None],
) -> Optional[int]:
    """Return None if clear, else exit code 1 or 3."""
    if hooks.ledger_validate(repo) != 0:
        log("ledger_invalid", phase=phase)
        return 1
    open_before = hooks.ledger_open_count(repo)
    if open_before < 0:
        log("ledger_count_failed", phase=phase)
        return 1
    if open_before == 0:
        return None
    if remaining_budget >= min_gap_slice:
        head_before = head(repo)
        refs_before = ref_snapshot(repo)
        timeout = min(per_gap_timeout, remaining_budget)
        rc = hooks.run_improve(_improve_prompt(), timeout, Path(run_dir) / f"improve-{phase}.log")
        if rc != 0:
            log("safety_net_failed", phase=phase, exit_code=rc)
        if (
            current_branch(repo) != "main"
            or not tree_clean(repo)
            or not only_main_advanced(repo, refs_before)
            or not is_ancestor(repo, head_before, head(repo))
        ):
            log("safety_net_dirtied", phase=phase)
            return 1
        if hooks.ledger_validate(repo) != 0:
            log("ledger_invalid", phase=phase)
            return 1
    else:
        log("safety_net_skipped", phase=phase, reason="budget")
    open_after = hooks.ledger_open_count(repo)
    if open_after < 0:
        log("ledger_count_failed", phase=phase)
        return 1
    if open_after > 0:
        log("needs_human_review", phase=phase, open=open_after)
        return 3
    return None


class _RunLog:
    def __init__(self, per_run: Path) -> None:
        self.path = per_run / "run.jsonl"
        self.per_run = per_run

    def event(self, event_type: str, **fields: object) -> None:
        rec = {"type": event_type, **fields}
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
    if not valid_run_label(opts.run_label):
        return 2
    if not per_run_path_safe(repo, per_run):
        return 2
    if not tree_clean(repo) or current_branch(repo) != "main":
        return 2
    if opts.dry_run:
        if lock_is_held(repo) or per_run.exists() or not gap_docs_present(repo):
            return 2
        return 0
    if not acquire_lock(repo):
        return 2
    try:
        try:
            per_run.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            return 2
        log = _RunLog(per_run)
        restore_guards = install_no_push_guards(repo)
        try:
            start = clock()

            def remaining() -> float:
                return opts.time_budget - (clock() - start)

            log.event(
                "run.started",
                agent=opts.agent,
                head_before=head(repo),
                max_gaps=opts.max_gaps,
            )
            code = lesson_gate(
                repo,
                "preflight",
                hooks,
                remaining_budget=remaining(),
                min_gap_slice=opts.min_gap_slice,
                per_gap_timeout=opts.per_gap_timeout,
                run_dir=per_run,
                log=log.event,
            )
            if code is not None:
                log.event("run.finished", gaps_done=0, stop_reason="preflight", exit_code=code)
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
                    _gap_prompt(),
                    min(opts.per_gap_timeout, rem),
                    log.gap_log(gaps_done + 1),
                )
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
                if exit_code == 0 and kind == "NO_GAPS":
                    ok, reason = verify_no_gaps(repo, head_before, refs_before)
                    if ok:
                        stop = "no_gaps"
                        log.event("gap.no_gaps")
                        break
                    stop = f"verify_failed:{reason}"
                    log.event("gap.failed", reason=stop)
                    break
                if kind == "BLOCKED" and (
                    current_branch(repo) != "main"
                    or not tree_clean(repo)
                    or head(repo) != head_before
                    or ref_snapshot(repo) != refs_before
                ):
                    log.event("unexpected_progress", reason=arg)
                stop = f"blocked:{arg}" if kind == "BLOCKED" else "failure"
                log.event("gap.failed", reason=stop)
                break

            clean_stop = stop in ("no_gaps", "cap_reached")
            if clean_stop and tree_clean(repo) and current_branch(repo) == "main":
                code = lesson_gate(
                    repo,
                    "postloop",
                    hooks,
                    remaining_budget=remaining(),
                    min_gap_slice=opts.min_gap_slice,
                    per_gap_timeout=opts.per_gap_timeout,
                    run_dir=per_run,
                    log=log.event,
                )
                if code is not None:
                    log.event(
                        "run.finished",
                        gaps_done=gaps_done,
                        stop_reason=stop,
                        exit_code=code,
                    )
                    return code
                log.event("run.finished", gaps_done=gaps_done, stop_reason=stop, exit_code=0)
                return 0
            log.event(
                "run.finished",
                gaps_done=gaps_done,
                stop_reason=stop,
                exit_code=1,
                needs_human_cleanup=True,
            )
            return 1
        finally:
            restore_guards()
    finally:
        release_lock(repo)


def _agent_cmd(agent: str) -> list[str]:
    return ["claude-yolo", "-p", "--model", "opus"] if agent == "opus" else [agent, "-p"]


def _spawn_capture(cmd: list[str], cwd: Path, timeout: float, log_path: Path) -> tuple[int, str]:
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        out, err = proc.communicate()
        rc = -1
    log_path.write_text((out or "") + "\n--- stderr ---\n" + (err or ""), encoding="utf-8")
    return rc, out or ""


def _real_run_gap(repo: Path, agent: str) -> Callable[[str, float, Path], tuple[int, str]]:
    def run_gap(prompt: str, timeout: float, log_path: Path) -> tuple[int, str]:
        return _spawn_capture([*_agent_cmd(agent), prompt], repo, timeout, log_path)

    return run_gap


def _real_run_improve(repo: Path, agent: str) -> Callable[[str, float, Path], int]:
    def run_improve(prompt: str, timeout: float, log_path: Path) -> int:
        rc, _ = _spawn_capture([*_agent_cmd(agent), prompt], repo, timeout, log_path)
        return rc

    return run_improve


def _ledger_cmd(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "python3",
            str(repo / "scripts" / "parity_lessons.py"),
            "--ledger",
            str(repo / LEDGER_REL),
            "--repo",
            str(repo),
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def default_hooks(opts: Opts) -> Hooks:
    def ledger_validate(repo: Path) -> int:
        return _ledger_cmd(repo, "validate").returncode

    def ledger_open_count(repo: Path) -> int:
        cp = _ledger_cmd(repo, "list", "--status", "open", "--json")
        if cp.returncode != 0:
            return -1
        try:
            parsed = json.loads(cp.stdout or "[]")
        except (json.JSONDecodeError, TypeError):
            return -1
        return len(parsed) if isinstance(parsed, list) else -1

    return Hooks(
        run_gap=_real_run_gap(opts.repo, opts.agent),
        run_improve=_real_run_improve(opts.repo, opts.agent),
        ledger_validate=ledger_validate,
        ledger_open_count=ledger_open_count,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="bounded unattended parity-loop runner")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--run-dir", default="docs/parity-loop/runs")
    parser.add_argument("--run-label", default=None)
    parser.add_argument("--agent", default="opus")
    parser.add_argument("--max-gaps", type=int, default=DEFAULTS["max_gaps"])
    parser.add_argument("--time-budget", type=float, default=DEFAULTS["time_budget"])
    parser.add_argument("--per-gap-timeout", type=float, default=DEFAULTS["per_gap_timeout"])
    parser.add_argument("--min-gap-slice", type=float, default=DEFAULTS["min_gap_slice"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    label = args.run_label or time.strftime("%Y-%m-%dT%H%M%SZ", time.gmtime())
    repo_path = Path(args.repo).resolve()
    run_dir = Path(args.run_dir)
    if not run_dir.is_absolute():
        run_dir = repo_path / run_dir
    opts = Opts(
        repo=repo_path,
        run_dir=run_dir,
        run_label=label,
        agent=args.agent,
        max_gaps=args.max_gaps,
        time_budget=args.time_budget,
        per_gap_timeout=args.per_gap_timeout,
        min_gap_slice=args.min_gap_slice,
        dry_run=args.dry_run,
    )
    return run(opts, default_hooks(opts), clock=time.monotonic)


if __name__ == "__main__":
    raise SystemExit(main())
