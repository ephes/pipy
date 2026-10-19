"""Reproduce real subprocess crashes against disposable native-tool workspaces."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if not __debug__:
    raise SystemExit(
        "experiment verification requires assertions; run without -O/PYTHONOPTIMIZE"
    )

RUNTIME = Path(__file__).with_name("runtime.py")
CRASH_POINTS = (
    "admitted",
    "generation.0.stream",
    "generation.0.response",
    "generation.0.committed",
    "tool.0.admitted",
    "tool.0.before_execute",
    "tool.0.effect",
    "tool.0.committed",
    "tool.1.effect",
    "generation.2.committed",
)


def command(directory, action, scenario="safe", *extra):
    args = [sys.executable, str(RUNTIME), action, "--db", str(directory / "run.sqlite")]
    if action == "start":
        args += ["--workspace", str(directory), "--scenario", scenario]
    return [*args, *extra]


def invoke(directory, action, scenario="safe", *extra, label=None):
    completed = subprocess.run(
        command(directory, action, scenario, *extra),
        text=True,
        capture_output=True,
        timeout=20,
    )
    if label:
        (directory / (label + ".stdout")).write_text(completed.stdout, encoding="utf-8")
        (directory / (label + ".stderr")).write_text(completed.stderr, encoding="utf-8")
    return completed


def kill_at_barrier(directory, scenario, point):
    marker = directory / "barrier"
    with (
        open(directory / "initial.stdout", "w") as stdout,
        open(directory / "initial.stderr", "w") as stderr,
    ):
        process = subprocess.Popen(
            command(
                directory,
                "start",
                scenario,
                "--pause-at",
                point,
                "--marker",
                str(marker),
            ),
            stdout=stdout,
            stderr=stderr,
        )
        try:
            deadline = time.monotonic() + 10
            while not marker.exists():
                if process.poll() is not None:
                    raise AssertionError(f"barrier child exited {process.returncode}")
                if time.monotonic() > deadline:
                    raise AssertionError("barrier timeout")
                time.sleep(0.01)
            os.kill(process.pid, signal.SIGKILL)
            return process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def effect_lines(directory):
    path = directory / "effect.txt"
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def case(root, scenario, point=None, *, sigkill=False, fail_write_at=None):
    label = point or fail_write_at or "baseline"
    directory = Path(tempfile.mkdtemp(prefix=f"{scenario}-{label}-", dir=root))
    if sigkill:
        initial_code = kill_at_barrier(directory, scenario, point)
        assert initial_code == -signal.SIGKILL
    else:
        flags = (["--crash-at", point] if point else []) + (
            ["--fail-write-at", fail_write_at] if fail_write_at else []
        )
        initial = invoke(directory, "start", scenario, *flags, label="initial")
        initial_code = initial.returncode
        expected = 86 if point else (1 if fail_write_at else 0)
        assert initial_code == expected, initial.stderr
        if point or fail_write_at:
            assert not initial.stdout  # Nothing escapes before the represented commits.
    initial_effects = effect_lines(directory)
    effect_finished = point in (
        "tool.0.effect",
        "tool.0.committed",
        "tool.1.effect",
        "generation.2.committed",
    ) or fail_write_at in ("tool.result", "tool.result.after_commit")
    baseline = point is None and fail_write_at is None
    assert initial_effects == int(effect_finished or baseline)
    if fail_write_at == "admission":
        # No accepted input exists yet. Retrying start is the admission boundary.
        resumed = invoke(directory, "start", scenario, label="resume")
    else:
        resumed = invoke(directory, "resume", label="resume")
    blocked = scenario == "unsafe" and (
        point in ("tool.0.before_execute", "tool.0.effect")
        or fail_write_at == "tool.result"
    )
    assert resumed.returncode == (3 if blocked else 0), resumed.stderr
    summary = json.loads(resumed.stdout)
    assert summary["phase"] == ("blocked" if blocked else "done")
    repeated = invoke(directory, "resume", label="repeat")
    assert repeated.returncode == resumed.returncode
    assert json.loads(repeated.stdout) == summary
    assert effect_lines(directory) == (initial_effects if blocked else 1)
    with sqlite3.connect(directory / "run.sqlite") as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        rows = db.execute(
            "SELECT task_id,count(*) FROM entries GROUP BY task_id"
        ).fetchall()
        assert all(count == 1 for _, count in rows)
        assert summary["tool_results"] == (0 if blocked else 2)
        assert db.execute("SELECT count(*) FROM admission").fetchone()[0] == 1
    tasks = {task["id"]: task for task in summary["tasks"]}
    provider_attempts = sum(
        t["attempts"] for t in tasks.values() if t["kind"] == "generation"
    )
    generation_retry = point in ("generation.0.stream", "generation.0.response")
    tool_retry = scenario == "safe" and (
        point in ("tool.0.before_execute", "tool.0.effect")
        or fail_write_at == "tool.result"
    )
    expected_attempts = {
        "g0": 2 if generation_retry else 1,
        "t0": 2 if tool_retry else 1,
    }
    if not blocked:
        expected_attempts.update(
            {"g1": 1, "t1": 2 if point == "tool.1.effect" else 1, "g2": 1}
        )
    assert {key: task["attempts"] for key, task in tasks.items()} == expected_attempts
    assert provider_attempts == (1 if blocked else (4 if generation_retry else 3))
    if blocked:
        assert tasks["t0"]["phase"] == "uncertain"
    return {
        "scenario": scenario,
        "cut": label,
        "death": "SIGKILL" if sigkill else "exit",
        "initial_exit": initial_code,
        "initial_effect_lines": initial_effects,
        "final_effect_lines": effect_lines(directory),
        "phase": summary["phase"],
        "provider_attempts": provider_attempts,
        "tool_attempts": {
            key: task["attempts"]
            for key, task in tasks.items()
            if task["kind"] == "tool"
        },
        "committed_results": summary["tool_results"],
        "directory": str(directory),
    }


def concurrent_open(root):
    directory = Path(tempfile.mkdtemp(prefix="concurrent-", dir=root))
    marker = directory / "barrier"
    process = subprocess.Popen(
        command(
            directory,
            "start",
            "unsafe",
            "--pause-at",
            "tool.0.effect",
            "--marker",
            str(marker),
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        denied = invoke(directory, "resume", label="concurrent")
        assert denied.returncode == 1 and not denied.stdout
        assert "BlockingIOError" in denied.stderr
        assert effect_lines(directory) == 1
    finally:
        process.kill()
        process.communicate(timeout=5)
    resumed = invoke(directory, "resume", label="after-kill")
    assert resumed.returncode == 3 and effect_lines(directory) == 1
    return {
        "probe": "concurrent open rejected; SIGKILL released owner",
        "directory": str(directory),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Existing disposable evidence directory",
    )
    args = parser.parse_args()
    observations = []
    for scenario in ("safe", "unsafe"):
        observations.append(case(args.output, scenario))
        for point in CRASH_POINTS:
            observations.append(case(args.output, scenario, point))
        observations.append(case(args.output, scenario, "tool.0.effect", sigkill=True))
        for failure in (
            "admission",
            "tool.start",
            "tool.result",
            "tool.result.after_commit",
        ):
            observations.append(case(args.output, scenario, fail_write_at=failure))
    observations.append(concurrent_open(args.output))
    report = {"cases": len(observations), "observations": observations}
    (args.output / "observations.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
