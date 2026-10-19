"""Hard-crash matrix for the real AgentLoop receipt-adapter spike."""

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
        "experiment verification requires assertions; run without optimization"
    )

RUNTIME = Path(__file__).with_name("loop_integration.py")
POINTS = (
    "admitted",
    "g0.stream",
    "g0.response",
    "g0.receipt",
    "g0.message",
    "t0.intent",
    "t0.effect",
    "t0.receipt",
    "t0.message",
    "t0.published",
    "t1.effect",
    "t1.receipt",
    "t1.message",
    "g1.receipt",
    "g1.message",
    "settled.committed",
)
FAILURES = (
    "g0.receipt",
    "g0.message",
    "t0.receipt",
    "t0.receipt.after_commit",
    "loop.settled.after_commit",
)


def command(directory, action, scenario="safe", *extra):
    args = [sys.executable, str(RUNTIME), action, "--db", str(directory / "run.sqlite")]
    if action == "start":
        args += ["--workspace", str(directory), "--scenario", scenario]
    return [*args, *extra]


def invoke(directory, action, scenario="safe", *extra, label=None):
    result = subprocess.run(
        command(directory, action, scenario, *extra),
        capture_output=True,
        text=True,
        timeout=20,
    )
    if label:
        (directory / (label + ".stdout")).write_text(result.stdout, encoding="utf-8")
        (directory / (label + ".stderr")).write_text(result.stderr, encoding="utf-8")
    return result


def kill_at(directory, scenario, point):
    marker = directory / "barrier"
    with (
        open(directory / "initial.stdout", "w") as out,
        open(directory / "initial.stderr", "w") as err,
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
            stdout=out,
            stderr=err,
        )
        try:
            deadline = time.monotonic() + 10
            while not marker.exists():
                assert process.poll() is None
                assert time.monotonic() < deadline
                time.sleep(0.01)
            os.kill(process.pid, signal.SIGKILL)
            assert process.wait(timeout=5) == -signal.SIGKILL
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def lines(directory):
    path = directory / "effect.txt"
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def case(root, scenario, point=None, *, sigkill=False, failure=None):
    directory = Path(
        tempfile.mkdtemp(
            prefix=f"{scenario}-{point or failure or 'baseline'}-", dir=root
        )
    )
    if sigkill:
        kill_at(directory, scenario, point)
    else:
        flags = (["--crash-at", point] if point else []) + (
            ["--fail-write-at", failure] if failure else []
        )
        result = invoke(directory, "start", scenario, *flags, label="initial")
        assert result.returncode == (86 if point else (1 if failure else 0)), (
            result.stderr
        )
        if point or failure:
            assert not result.stdout
    initial_lines = lines(directory)
    effect_finished = point in (
        "t0.effect",
        "t0.receipt",
        "t0.message",
        "t0.published",
        "t1.effect",
        "t1.receipt",
        "t1.message",
        "g1.receipt",
        "g1.message",
        "settled.committed",
    ) or failure in (
        "t0.receipt",
        "t0.receipt.after_commit",
        "loop.settled.after_commit",
    )
    assert initial_lines == int(effect_finished or (not point and not failure))
    resumed = invoke(directory, "resume", label="resume")
    blocked = scenario == "unsafe" and (
        point in ("t0.intent", "t0.effect") or failure == "t0.receipt"
    )
    assert resumed.returncode == (3 if blocked else 0), resumed.stderr
    summary = json.loads(resumed.stdout)
    assert summary["phase"] == ("blocked" if blocked else "done")
    repeated = invoke(directory, "resume", label="repeat")
    assert (
        repeated.returncode == resumed.returncode and repeated.stdout == resumed.stdout
    )
    assert lines(directory) == (initial_lines if blocked else 1)
    retry_generation = point in ("g0.stream", "g0.response") or failure == "g0.receipt"
    retry_tool = scenario == "safe" and (
        point in ("t0.intent", "t0.effect") or failure == "t0.receipt"
    )
    attempts = {"g0": 2 if retry_generation else 1, "t0": 2 if retry_tool else 1}
    if blocked:
        attempts["t1"] = 0  # Admitted atomically with the assistant, never invoked.
    else:
        attempts.update({"t1": 2 if point == "t1.effect" else 1, "g1": 1})
        assert summary["run"]["usage"] == summary["run"]["session_usage"]
        assert summary["run"]["usage"] == {
            "input_tokens": 200,
            "output_tokens": 40,
            "reasoning_tokens": 14,
            "cache_read_tokens": 60,
            "cache_write_tokens": 20,
            "cost_usd": 0.000261,
        }
    assert {row["id"]: row["attempts"] for row in summary["tasks"]} == attempts
    assert summary["entries"] == (2 if blocked else 5)
    assert summary["tool_results"] == (0 if blocked else 2)
    assert summary["observations"] == (2 if blocked else 6)
    with sqlite3.connect(directory / "run.sqlite") as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT count(*) FROM entries").fetchone()[0] == len(
            db.execute("SELECT DISTINCT task_id FROM entries").fetchall()
        )
    return {
        "scenario": scenario,
        "cut": point or failure or "baseline",
        "death": "SIGKILL" if sigkill else "exit",
        "phase": summary["phase"],
        "attempts": attempts,
        "initial_effect_lines": initial_lines,
        "final_effect_lines": lines(directory),
        "observations": summary["observations"],
        "run": summary["run"],
        "directory": str(directory),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = []
    for scenario in ("safe", "unsafe"):
        results.append(case(args.output, scenario))
        for point in POINTS:
            results.append(case(args.output, scenario, point))
        results.append(case(args.output, scenario, "t0.effect", sigkill=True))
        for failure in FAILURES:
            results.append(case(args.output, scenario, failure=failure))
    report = {"cases": len(results), "observations": results}
    (args.output / "observations.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
