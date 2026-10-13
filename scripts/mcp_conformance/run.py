#!/usr/bin/env python3
"""Run the official MCP client conformance suite against pipy.

Runs every client scenario of the pinned suite against ``client.py`` and
compares every check with the committed baseline (``baseline.json``). Fails
when a check that passed in the baseline no longer passes, or when a check
fails that the baseline does not list. Known failures stay visible in the
output. See ``README.md`` (Pi ``test/mcp-conformance/run.ts``).

Run:

    just mcp-conformance [--mode VERSION] [--scenario NAME] [--verbose]
                         [--keep-results] [--update-baseline]

Exit codes: 0 no regression, 1 regression or a run that could not be made,
2 unknown option or protocol version.
This is a manual check: it needs Node (``npx``) and, the first time, network
access to fetch the suite. It is not part of ``just check`` or CI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The pinned upstream suite. Bumping it requires reviewing and regenerating
# the baseline.
CONFORMANCE_PACKAGE = "@modelcontextprotocol/conformance@0.2.0-alpha.11"
# Protocol versions pipy negotiates. 2026-07-28 is a different (stateless)
# protocol pipy does not speak.
MODES = ("2025-03-26", "2025-06-18", "2025-11-25")
SCENARIO_TIMEOUT_MS = 30_000
# Checks that are part of the suite's HTTP trace, not assertions.
IGNORED_STATUSES = frozenset({"INFO", "SKIPPED"})
# Whether ``client.py`` completed the scenario, recorded as one more check.
CLIENT_CHECK = "pipy-client"

HERE = Path(__file__).resolve().parent
BASELINE_PATH = HERE / "baseline.json"
CLIENT_PATH = HERE / "client.py"

# mode -> scenario -> {"passing": [...], "failing": [...]}
BaselineModes = dict[str, dict[str, dict[str, list[str]]]]


@dataclass
class ScenarioResult:
    mode: str
    scenario: str
    # Check id to "pass" or "fail". A repeated id passes only when every
    # instance passes.
    checks: dict[str, str] = field(default_factory=dict)
    # Failure messages by check id, for the report.
    messages: dict[str, str] = field(default_factory=dict)

    def record(self, check: str, status: str, message: str = "") -> None:
        if self.checks.get(check) != "fail":
            self.checks[check] = status
        if status == "fail" and message and check not in self.messages:
            self.messages[check] = message


@dataclass(frozen=True)
class Completed:
    code: int
    stdout: str
    stderr: str


def run_command(
    args: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: float | None = None,
) -> Completed:
    """Run a command in a process group of its own, which is killed as a
    whole at the timeout: the suite starts the client and scenario servers."""

    process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        list(args),
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
    code = process.returncode
    return Completed(128 if code < 0 else code, stdout, stderr)


def conformance_env(environ: Mapping[str, str]) -> dict[str, str]:
    # The suite comes from npm with prebuilt files; nothing needs install
    # scripts.
    return {**environ, "npm_config_ignore_scripts": "true", "npm_config_yes": "true"}


def parse_scenario_list(output: str) -> list[str]:
    """The scenario names in the output of the suite's ``list`` command."""

    return re.findall(r"^\s+- (\S+)", output, flags=re.MULTILINE)


def list_scenarios(mode: str, work_dir: Path) -> list[str]:
    result = run_command(
        [
            "npx",
            "--yes",
            CONFORMANCE_PACKAGE,
            "list",
            "--client",
            "--spec-version",
            mode,
        ],
        cwd=work_dir,
        env=conformance_env(os.environ),
    )
    if result.code != 0:
        raise RuntimeError(
            f"Listing {mode} scenarios failed:\n{result.stderr or result.stdout}"
        )
    return parse_scenario_list(result.stdout)


def record_checks(result: ScenarioResult, checks: object) -> None:
    """Record the suite's ``checks.json`` content in ``result``."""

    if not isinstance(checks, list):
        result.record("runner", "fail", "checks.json is not a list")
        return
    for check in checks:
        if not isinstance(check, dict):
            result.record(
                "runner", "fail", "checks.json has an entry that is no object"
            )
            continue
        status = str(check.get("status") or "FAILURE").upper()
        if status in IGNORED_STATUSES:
            continue
        check_id = str(check.get("id") or check.get("name") or "unnamed")
        message = str(check.get("errorMessage") or check.get("description") or "")
        result.record(check_id, "pass" if status == "SUCCESS" else "fail", message)


def record_client_report(result: ScenarioResult, report_path: Path) -> None:
    """Record whether pipy completed the scenario. Some scenarios expect the
    client to give up, so this is baselined like any other check."""

    report: object = {"error": "client.py wrote no report"}
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text())
        except ValueError:
            report = {"error": "client.py wrote a report that is not JSON"}
    if not isinstance(report, dict):
        report = {"error": "client.py wrote a report that is no object"}
    result.record(
        CLIENT_CHECK,
        "pass" if report.get("success") is True else "fail",
        str(report.get("error") or ""),
    )


def run_scenario(
    mode: str, scenario: str, launcher: Path, work_dir: Path, verbose: bool
) -> ScenarioResult:
    scenario_dir = work_dir / mode / re.sub(r"[^A-Za-z0-9_.-]+", "-", scenario)
    scenario_dir.mkdir(parents=True, exist_ok=True)
    report_path = scenario_dir / "pipy-client.json"
    result = ScenarioResult(mode, scenario)
    run = run_command(
        [
            "npx",
            "--yes",
            CONFORMANCE_PACKAGE,
            "client",
            "--command",
            str(launcher),
            "--scenario",
            scenario,
            "--spec-version",
            mode,
            "--timeout",
            str(SCENARIO_TIMEOUT_MS),
            "--output-dir",
            str(scenario_dir),
        ],
        cwd=work_dir,
        env={
            **conformance_env(os.environ),
            "PIPY_MCP_CONFORMANCE_REPORT": str(report_path),
        },
        timeout_seconds=(SCENARIO_TIMEOUT_MS + 30_000) / 1000,
    )
    if verbose:
        sys.stderr.write(run.stderr)
    checks_path = next(iter(sorted(scenario_dir.rglob("checks.json"))), None)
    if checks_path is None:
        result.record(
            "runner",
            "fail",
            f"No checks.json (runner exit {run.code}): {run.stderr.strip()[-2000:]}",
        )
        return result
    try:
        checks = json.loads(checks_path.read_text())
    except ValueError:
        result.record("runner", "fail", "checks.json is not JSON")
        return result
    record_checks(result, checks)
    record_client_report(result, report_path)
    return result


def to_baseline(results: Sequence[ScenarioResult]) -> dict[str, Any]:
    modes: BaselineModes = {}
    for result in results:
        ids = sorted(result.checks)
        modes.setdefault(result.mode, {})[result.scenario] = {
            "passing": [i for i in ids if result.checks[i] == "pass"],
            "failing": [i for i in ids if result.checks[i] == "fail"],
        }
    return {"conformance": CONFORMANCE_PACKAGE, "modes": modes}


def compare(
    results: Sequence[ScenarioResult], modes: BaselineModes
) -> tuple[list[str], list[str]]:
    """Compare with the baseline: the regressions and the checks that were
    fixed since.

    A regression is a failing check the baseline does not list as failing, a
    check that passes in the baseline and was not reported, and a baselined
    scenario that did not run.
    """

    regressions: list[str] = []
    fixed: list[str] = []
    for result in results:
        expected = modes.get(result.mode, {}).get(result.scenario, {})
        failing = expected.get("failing", [])
        label = f"{result.mode} {result.scenario}"
        for check, status in result.checks.items():
            message = result.messages.get(check)
            if status == "fail" and check not in failing:
                regressions.append(
                    f"{label}: {check} fails" + (f": {message}" if message else "")
                )
            if status == "pass" and check in failing:
                fixed.append(f"{label}: {check}")
        for check in expected.get("passing", []):
            if check not in result.checks:
                regressions.append(f"{label}: {check} was not reported")
    ran = {(result.mode, result.scenario) for result in results}
    for mode, scenarios in modes.items():
        for scenario in scenarios:
            if (mode, scenario) not in ran:
                regressions.append(f"{mode} {scenario}: scenario did not run")
    return regressions, fixed


def scoped_baseline(
    modes: BaselineModes, run_modes: Sequence[str], scenarios: Sequence[str] | None
) -> BaselineModes:
    """The part of the baseline a run of a subset is compared with."""

    return {
        mode: {
            scenario: checks
            for scenario, checks in modes.get(mode, {}).items()
            if scenarios is None or scenario in scenarios
        }
        for mode in run_modes
    }


def print_results(results: Sequence[ScenarioResult]) -> None:
    for mode in MODES:
        of_mode = [result for result in results if result.mode == mode]
        if not of_mode:
            continue
        print(f"\n{mode}")
        for result in of_mode:
            failed = [c for c, status in result.checks.items() if status == "fail"]
            passed = len(result.checks) - len(failed)
            verdict = "FAIL" if failed else "pass"
            print(
                f"  {verdict} {result.scenario} ({passed} passed, {len(failed)} failed)"
            )
            for check in failed:
                message = " ".join(result.messages.get(check, "").split())[:300]
                print(f"         {check}" + (f": {message}" if message else ""))


def write_launcher(work_dir: Path) -> Path:
    # The upstream runner splits --command on spaces, so use a launcher path
    # without any.
    launcher = work_dir / "pipy-client"
    launcher.write_text(
        "#!/bin/sh\n"
        f'exec {shlex.quote(sys.executable)} {shlex.quote(str(CLIENT_PATH))} "$@"\n'
    )
    launcher.chmod(0o755)
    return launcher


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="just mcp-conformance",
        description="Run the official MCP client conformance suite against pipy.",
    )
    parser.add_argument(
        "--mode",
        action="append",
        choices=MODES,
        help="only run this protocol version (repeatable)",
    )
    parser.add_argument(
        "--scenario", action="append", help="only run this scenario (repeatable)"
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="write the results to baseline.json instead of comparing",
    )
    parser.add_argument(
        "--keep-results",
        action="store_true",
        help="keep the upstream checks.json and client output",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="print the upstream runner output"
    )
    return parser.parse_args(argv)


def usage_error(args: argparse.Namespace) -> str | None:
    # What is wrong with the invocation comes before what is missing here.
    if args.update_baseline and is_partial(args):
        return "--update-baseline needs a full run, without --mode or --scenario."
    if os.name != "posix":
        return "The conformance runner needs a POSIX shell."
    if shutil.which("npx") is None:
        return "The conformance runner needs Node's npx on PATH."
    return None


def is_partial(args: argparse.Namespace) -> bool:
    return args.mode is not None or args.scenario is not None


def run_scenarios(args: argparse.Namespace, work_dir: Path) -> list[ScenarioResult]:
    launcher = write_launcher(work_dir)
    results: list[ScenarioResult] = []
    for mode in args.mode or MODES:
        for scenario in list_scenarios(mode, work_dir):
            if args.scenario is not None and scenario not in args.scenario:
                continue
            print(f"{mode} {scenario}", file=sys.stderr)
            results.append(
                run_scenario(mode, scenario, launcher, work_dir, args.verbose)
            )
    return results


def load_baseline(path: Path) -> tuple[BaselineModes | None, str | None]:
    """The baseline's modes, or the reason it cannot be used."""

    if not path.exists():
        return None, "No baseline.json. Run with --update-baseline and review it."
    baseline = json.loads(path.read_text())
    if baseline.get("conformance") != CONFORMANCE_PACKAGE:
        return None, (
            f"baseline.json is for {baseline.get('conformance')}, "
            f"not {CONFORMANCE_PACKAGE}. Regenerate it."
        )
    return baseline["modes"], None


def judge(results: Sequence[ScenarioResult], args: argparse.Namespace) -> int:
    expected, problem = load_baseline(BASELINE_PATH)
    if expected is None:
        print(f"\n{problem}", file=sys.stderr)
        return 1
    if is_partial(args):
        expected = scoped_baseline(expected, args.mode or MODES, args.scenario)
    regressions, fixed = compare(results, expected)
    if fixed:
        print("\nFixed since the baseline (run with --update-baseline):")
        for line in fixed:
            print(f"  {line}")
    if regressions:
        print("\nRegressions:")
        for line in regressions:
            print(f"  {line}")
        return 1
    print("\nNo regressions against the baseline.")
    return 0


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    problem = usage_error(args)
    if problem is not None:
        print(problem, file=sys.stderr)
        return 1
    work_dir = Path(tempfile.mkdtemp(prefix="pipy-mcp-conformance-"))
    try:
        if " " in str(work_dir):
            print(
                f"The temporary directory {work_dir} has a space in its path; "
                "set TMPDIR to one without.",
                file=sys.stderr,
            )
            return 1
        results = run_scenarios(args, work_dir)
        if not results:
            print("No scenarios matched.", file=sys.stderr)
            return 1
        print_results(results)
        if args.update_baseline:
            BASELINE_PATH.write_text(json.dumps(to_baseline(results), indent=2) + "\n")
            print(f"\nWrote {BASELINE_PATH}")
            return 0
        return judge(results, args)
    finally:
        if args.keep_results:
            print(f"\nResults kept in {work_dir}")
        else:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
