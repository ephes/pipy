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
import sys
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
REPORT_BEGIN = "<!-- BEGIN GENERATED:facts -->"
REPORT_END = "<!-- END GENERATED:facts -->"
REPORT_LABEL_PREFIX = "<!-- parity-run-label: "
CAVEAT_RE = re.compile(
    r"\b(caveat|warning|warn|incomplete|partial|skipped|blocked|remaining|"
    r"unable|failed|failure|not run|not complete|could not|couldn't|did not|didn't)\b",
    re.IGNORECASE,
)
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


class ReportError(RuntimeError):
    """Raised when a parity run cannot be rendered as a slice report."""


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


def remote_tracking_snapshot(repo: Path) -> dict[str, str]:
    return {
        name: sha
        for name, sha in ref_snapshot(repo).items()
        if name.startswith("refs/remotes/")
    }


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


def improve_log_caveats(log_path: Path, *, max_lines: int = 12, max_chars: int = 500) -> list[str]:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    caveats: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = re.sub(r"^\s*(?:[-*]\s+|>\s*)", "", raw).strip()
        if not line or line == "--- stderr ---" or not CAVEAT_RE.search(line):
            continue
        clipped = line[:max_chars]
        if clipped in seen:
            continue
        caveats.append(clipped)
        seen.add(clipped)
        if len(caveats) >= max_lines:
            break
    return caveats


def _read_run_events(run_log: Path) -> list[dict[str, object]]:
    try:
        lines = run_log.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReportError(f"could not read run log: {run_log}") from exc

    events: list[dict[str, object]] = []
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReportError(f"invalid JSON in {run_log}:{line_no}") from exc
        if not isinstance(parsed, dict):
            raise ReportError(f"run event is not an object in {run_log}:{line_no}")
        events.append(parsed)
    return events


def _latest_run_dir(run_dir: Path) -> Path:
    candidates = [
        path
        for path in run_dir.glob("run-*")
        if path.is_dir() and (path / "run.jsonl").is_file()
    ]
    if not candidates:
        raise ReportError(f"no run logs found under {run_dir}")
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.name))


def _run_dir_for_label(run_dir: Path, label: str | None) -> Path:
    if label:
        if not valid_run_label(label):
            raise ReportError(f"invalid run label: {label}")
        path = per_run_dir(run_dir, label)
        if not (path / "run.jsonl").is_file():
            raise ReportError(f"run log not found for label: {label}")
        return path
    return _latest_run_dir(run_dir)


def _run_label_from_dir(per_run: Path) -> str:
    name = per_run.name
    return name[len("run-") :] if name.startswith("run-") else name


def _event_type(event: dict[str, object]) -> str:
    value = event.get("type")
    return value if isinstance(value, str) else ""


def _require_event(events: list[dict[str, object]], event_type: str) -> dict[str, object]:
    for event in events:
        if _event_type(event) == event_type:
            return event
    raise ReportError(f"run log is missing {event_type}")


def _completed_gap_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return [event for event in events if _event_type(event) == "gap.completed"]


def _event_str(event: dict[str, object], key: str) -> str:
    value = event.get(key)
    return value if isinstance(value, str) else ""


def _event_int(event: dict[str, object], key: str, default: int = 0) -> int:
    value = event.get(key)
    return value if isinstance(value, int) else default


def _resolve_required(repo: Path, rev: str, label: str) -> str:
    resolved = _resolve(repo, rev)
    if resolved is None:
        raise ReportError(f"{label} does not resolve to a commit: {rev}")
    return resolved


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower()).strip("-._")
    return slug or "parity-slice"


def _find_report_for_label(report_dir: Path, label: str) -> Path | None:
    marker = f"{REPORT_LABEL_PREFIX}{label} -->"
    for path in sorted(report_dir.glob("*.md")):
        try:
            if marker in path.read_text(encoding="utf-8", errors="replace"):
                return path
        except OSError:
            continue
    return None


def _report_path_for_label(report_dir: Path, label: str) -> Path:
    existing = _find_report_for_label(report_dir, label)
    if existing is not None:
        return existing
    return report_dir / f"{_slugify(label)}.md"


def _git_stdout(repo: Path, *args: str) -> str:
    cp = _git(repo, *args)
    if cp.returncode != 0:
        raise ReportError(f"git {' '.join(args)} failed: {cp.stderr.strip()}")
    return cp.stdout


def _range_commits(repo: Path, start_sha: str, end_sha: str) -> list[tuple[str, str]]:
    if start_sha == end_sha:
        return []
    out = _git_stdout(repo, "log", "--reverse", "--format=%h%x09%s", f"{start_sha}..{end_sha}")
    commits: list[tuple[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        short, _, subject = line.partition("\t")
        commits.append((short, subject))
    return commits


def _diff_numstat(repo: Path, start_sha: str, end_sha: str) -> list[tuple[str, int, int]]:
    if start_sha == end_sha:
        return []
    out = _git_stdout(repo, "diff", "--numstat", f"{start_sha}..{end_sha}")
    stats: list[tuple[str, int, int]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added = 0 if parts[0] == "-" else int(parts[0])
        deleted = 0 if parts[1] == "-" else int(parts[1])
        stats.append((parts[2], added, deleted))
    return stats


def _area_for_path(path: str) -> str:
    parts = path.split("/")
    if not parts:
        return "."
    if parts[0] == "docs" and len(parts) > 2:
        return f"docs/{parts[1]}"
    return parts[0]


def _changed_area_rows(stats: list[tuple[str, int, int]]) -> list[tuple[str, int, int, int]]:
    areas: dict[str, tuple[int, int, int]] = {}
    for path, added, deleted in stats:
        area = _area_for_path(path)
        files, adds, dels = areas.get(area, (0, 0, 0))
        areas[area] = (files + 1, adds + added, dels + deleted)
    return [(area, *values) for area, values in sorted(areas.items())]


def _format_markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _caveat_rows(events: list[dict[str, object]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for event in events:
        if _event_type(event) == "safety_net_child_caveats":
            caveats = event.get("caveats")
            if isinstance(caveats, list):
                for caveat in caveats:
                    if isinstance(caveat, str):
                        rows.append([
                            _event_str(event, "phase") or "-",
                            _event_str(event, "log_path") or "-",
                            caveat.replace("|", "\\|"),
                        ])
    return rows


def _render_generated_facts(
    *,
    label: str,
    started: dict[str, object],
    finished: dict[str, object],
    start_sha: str,
    end_sha: str,
    commits: list[tuple[str, str]],
    stats: list[tuple[str, int, int]],
    caveats: list[list[str]],
) -> str:
    fact_rows = [
        ["Run label", f"`{label}`"],
        ["Agent", f"`{_event_str(started, 'agent') or '-'}`"],
        ["Recorded start", f"`{start_sha[:12]}`"],
        ["Recorded end", f"`{end_sha[:12]}`"],
        ["Gaps done", str(_event_int(finished, "gaps_done"))],
        ["Stop reason", f"`{_event_str(finished, 'stop_reason') or '-'}`"],
        ["Exit code", str(_event_int(finished, "exit_code", -1))],
        ["Range note", "`head_before..recorded_end`; this is factual, not curated semantic membership."],
    ]

    commit_rows = [[f"`{short}`", subject.replace("|", "\\|")] for short, subject in commits]
    area_rows = [
        [area, str(files), str(added), str(deleted)]
        for area, files, added, deleted in _changed_area_rows(stats)
    ]
    file_rows = [
        [path.replace("|", "\\|"), str(added), str(deleted)]
        for path, added, deleted in stats
    ]

    chunks = [
        REPORT_BEGIN,
        "## Generated Facts",
        "",
        _format_markdown_table(["Field", "Value"], fact_rows),
        "",
        "### Recorded Range Commits",
        "",
        _format_markdown_table(["Commit", "Subject"], commit_rows)
        if commit_rows
        else "No commits were recorded for this run.",
        "",
        "### Change Shape",
        "",
        _format_markdown_table(["Area", "Files", "Added", "Deleted"], area_rows)
        if area_rows
        else "No changed files were recorded for this run.",
        "",
        "### Changed Files",
        "",
        _format_markdown_table(["File", "Added", "Deleted"], file_rows)
        if file_rows
        else "No changed files were recorded for this run.",
        "",
        "### Recorded Caveats",
        "",
        _format_markdown_table(["Phase", "Log", "Caveat"], caveats)
        if caveats
        else "None recorded in `run.jsonl`.",
        "",
        REPORT_END,
    ]
    return "\n".join(chunks)


def _replace_generated_facts(existing: str, generated: str) -> str:
    begin = existing.find(REPORT_BEGIN)
    end = existing.find(REPORT_END)
    if begin == -1 and end == -1:
        return existing.rstrip() + "\n\n" + generated + "\n"
    if begin == -1 or end == -1 or end < begin:
        raise ReportError("report has an incomplete generated facts sentinel block")
    end += len(REPORT_END)
    return existing[:begin].rstrip() + "\n\n" + generated + "\n" + existing[end:].lstrip()


def _new_report_template(label: str, generated: str) -> str:
    return (
        f"# Parity Slice Report: {label}\n\n"
        f"{REPORT_LABEL_PREFIX}{label} -->\n\n"
        f"{generated}\n\n"
        "## What Changed\n\n"
        "Fill this in after reading the generated facts and the relevant diffs. "
        "Name the user-visible behavior that changed, not just the files touched.\n\n"
        "## Visualization\n\n"
        "Add a slice-specific Mermaid diagram only when it clarifies the behavior or "
        "workflow. Avoid generic runner diagrams that repeat across every slice.\n\n"
        "## Boundaries\n\n"
        "List what this slice deliberately did not ship, especially nearby parity "
        "gaps that remain deferred.\n\n"
        "## Comprehension Check\n\n"
        "Add two or three semantic questions with collapsible answers when a reader "
        "would benefit from testing their understanding of the slice.\n"
    )


def generate_slice_report(
    repo: Path,
    run_dir: Path,
    report_dir: Path,
    *,
    label: str | None = None,
) -> Path:
    per_run = _run_dir_for_label(run_dir, label)
    actual_label = _run_label_from_dir(per_run)
    events = _read_run_events(per_run / "run.jsonl")
    started = _require_event(events, "run.started")
    finished = _require_event(events, "run.finished")
    if _event_int(finished, "exit_code", -1) != 0:
        raise ReportError(f"run did not finish cleanly: {actual_label}")

    start_sha = _resolve_required(repo, _event_str(started, "head_before"), "head_before")
    gaps = _completed_gap_events(events)
    if gaps:
        end_rev = _event_str(gaps[-1], "head_after") or _event_str(gaps[-1], "sha")
        end_sha = _resolve_required(repo, end_rev, "last completed gap")
    else:
        end_sha = start_sha

    commits = _range_commits(repo, start_sha, end_sha)
    stats = _diff_numstat(repo, start_sha, end_sha)
    generated = _render_generated_facts(
        label=actual_label,
        started=started,
        finished=finished,
        start_sha=start_sha,
        end_sha=end_sha,
        commits=commits,
        stats=stats,
        caveats=_caveat_rows(events),
    )

    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = _report_path_for_label(report_dir, actual_label)
    if report_path.exists():
        existing = report_path.read_text(encoding="utf-8")
        updated = _replace_generated_facts(existing, generated)
    else:
        updated = _new_report_template(actual_label, generated)
    report_path.write_text(updated, encoding="utf-8")
    return report_path


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
        improve_log = Path(run_dir) / f"improve-{phase}.log"
        rc = hooks.run_improve(_improve_prompt(), timeout, improve_log)
        caveats = improve_log_caveats(improve_log)
        if caveats:
            log("safety_net_child_caveats", phase=phase, log_path=improve_log.name, caveats=caveats)
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


def child_block_reason(log_path: Path) -> str | None:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # In pipy print-mode, a fatal provider failure is emitted at the tail of the
    # child log. Do not match the entire log: interactive/product paths can log
    # recoverable provider failures and continue, and a later unrelated failure
    # should not be hidden as a provider outage.
    tail = [line for line in text.splitlines()[-20:] if line.strip()]
    if any("pipy: provider failure during turn:" in line for line in tail):
        return "provider_failure"
    return None


def run(opts: Opts, hooks: Hooks, *, clock: Callable[[], float]) -> int:
    repo = opts.repo
    per_run = per_run_dir(opts.run_dir, opts.run_label)
    if not valid_run_label(opts.run_label):
        print(f"parity-runner: invalid run label: {opts.run_label!r}", file=sys.stderr)
        return 2
    if not per_run_path_safe(repo, per_run):
        print(f"parity-runner: run directory is not git-ignored: {per_run}", file=sys.stderr)
        return 2
    branch = current_branch(repo)
    if branch != "main":
        print(f"parity-runner: expected branch main, got {branch}", file=sys.stderr)
        return 2
    if not tree_clean(repo):
        print(
            "parity-runner: worktree is not clean; commit, stash, or remove changes before starting a run",
            file=sys.stderr,
        )
        return 2
    if opts.dry_run:
        if lock_is_held(repo):
            print("parity-runner: another parity run holds the lock", file=sys.stderr)
            return 2
        if per_run.exists():
            print(f"parity-runner: run directory already exists: {per_run}", file=sys.stderr)
            return 2
        if not gap_docs_present(repo):
            print("parity-runner: required gap docs are missing", file=sys.stderr)
            return 2
        return 0
    if not acquire_lock(repo):
        print("parity-runner: another parity run holds the lock", file=sys.stderr)
        return 2
    try:
        try:
            per_run.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            print(f"parity-runner: run directory already exists: {per_run}", file=sys.stderr)
            return 2
        log = _RunLog(per_run)
        restore_guards = install_no_push_guards(repo)
        try:
            start = clock()
            remote_refs_before = remote_tracking_snapshot(repo)

            def remaining() -> float:
                return opts.time_budget - (clock() - start)

            def finish(gaps_done: int, stop_reason: str, exit_code: int, **fields: object) -> int:
                remote_refs_after = remote_tracking_snapshot(repo)
                log.event(
                    "run.finished",
                    gaps_done=gaps_done,
                    stop_reason=stop_reason,
                    exit_code=exit_code,
                    remote_tracking_before=remote_refs_before,
                    remote_tracking_after=remote_refs_after,
                    remote_tracking_changed=remote_refs_after != remote_refs_before,
                    **fields,
                )
                return exit_code

            log.event(
                "run.started",
                agent=opts.agent,
                head_before=head(repo),
                max_gaps=opts.max_gaps,
                remote_tracking_before=remote_refs_before,
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
                return finish(0, "preflight", code)

            gaps_done = 0
            stop = "cap_reached"
            while gaps_done < opts.max_gaps:
                rem = remaining()
                if rem < opts.min_gap_slice:
                    stop = "cap_reached"
                    break
                head_before = head(repo)
                refs_before = ref_snapshot(repo)
                gap_log_path = log.gap_log(gaps_done + 1)
                exit_code, stdout = hooks.run_gap(
                    _gap_prompt(),
                    min(opts.per_gap_timeout, rem),
                    gap_log_path,
                )
                kind, arg = parse_sentinel(stdout)
                if exit_code == 0 and kind == "COMMITTED":
                    ok, reason = verify_committed(repo, head_before, refs_before, arg)
                    if ok:
                        head_after = head(repo)
                        gaps_done += 1
                        log.event(
                            "gap.completed",
                            index=gaps_done,
                            sha=arg,
                            head_before=head_before,
                            head_after=head_after,
                        )
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
                if kind == "BLOCKED":
                    stop = f"blocked:{arg}"
                else:
                    detected_block = child_block_reason(gap_log_path)
                    stop = f"blocked:{detected_block}" if detected_block else "failure"
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
                    return finish(gaps_done, stop, code)
                return finish(gaps_done, stop, 0)
            return finish(gaps_done, stop, 1, needs_human_cleanup=True)
        finally:
            restore_guards()
    finally:
        release_lock(repo)


def _agent_cmd(agent: str) -> list[str]:
    if agent == "opus":
        return [
            "fish",
            "-lc",
            (
                'set args $argv; if test (count $args) -gt 0; and test "$args[1]" = "--"; '
                "set args $args[2..-1]; end; claude-yolo -p --model opus -- $args"
            ),
        ]
    if agent == "claude":
        return ["claude", "-p", "--model", "opus", "--dangerously-skip-permissions"]
    if agent == "codex":
        return ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox"]
    if agent == "pipy":
        return ["uv", "run", "pipy", "--tool-budget", "200", "-p"]
    return [agent, "-p"]


def _spawn_capture(cmd: list[str], cwd: Path, timeout: float, log_path: Path) -> tuple[int, str]:
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
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
        return _spawn_capture([*_agent_cmd(agent), "--", prompt], repo, timeout, log_path)

    return run_gap


def _real_run_improve(repo: Path, agent: str) -> Callable[[str, float, Path], int]:
    def run_improve(prompt: str, timeout: float, log_path: Path) -> int:
        rc, _ = _spawn_capture([*_agent_cmd(agent), "--", prompt], repo, timeout, log_path)
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
    parser.add_argument("--report-dir", default="docs/parity-loop/reports")
    parser.add_argument("--run-label", default=None)
    parser.add_argument("--agent", default="opus")
    parser.add_argument("--max-gaps", type=int, default=DEFAULTS["max_gaps"])
    parser.add_argument("--time-budget", type=float, default=DEFAULTS["time_budget"])
    parser.add_argument("--per-gap-timeout", type=float, default=DEFAULTS["per_gap_timeout"])
    parser.add_argument("--min-gap-slice", type=float, default=DEFAULTS["min_gap_slice"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--report-slice",
        nargs="?",
        const="",
        default=None,
        metavar="LABEL",
        help="generate a slice report for LABEL, or for the latest run when omitted",
    )
    parser.add_argument(
        "--write-report",
        action="store_true",
        help="generate a slice report after a clean non-dry run",
    )
    args = parser.parse_args(argv)

    label = args.run_label or time.strftime("%Y-%m-%dT%H%M%SZ", time.gmtime())
    repo_path = Path(args.repo).resolve()
    run_dir = Path(args.run_dir)
    if not run_dir.is_absolute():
        run_dir = repo_path / run_dir
    report_dir = Path(args.report_dir)
    if not report_dir.is_absolute():
        report_dir = repo_path / report_dir
    if args.report_slice is not None:
        try:
            report_path = generate_slice_report(
                repo_path,
                run_dir,
                report_dir,
                label=args.report_slice or None,
            )
        except ReportError as exc:
            print(f"parity-runner: {exc}", file=sys.stderr)
            return 1
        print(report_path)
        return 0
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
    exit_code = run(opts, default_hooks(opts), clock=time.monotonic)
    if args.write_report and exit_code == 0 and not args.dry_run:
        try:
            report_path = generate_slice_report(repo_path, run_dir, report_dir, label=label)
        except ReportError as exc:
            print(f"parity-runner: report generation failed: {exc}", file=sys.stderr)
            return exit_code
        print(report_path)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
