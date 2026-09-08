"""Regression checks for trust-aware standalone parity probes."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _environment_snapshot() -> dict[str, object]:
    """Capture links, target metadata and configuration without repairing them."""

    interpreter = Path(sys.executable)
    paths = sorted(interpreter.parent.glob("python*"))
    paths.append(interpreter.parent.parent / "pyvenv.cfg")
    snapshot: dict[str, object] = {}
    for path in paths:
        stat = path.lstat()
        resolved = path.resolve(strict=True)
        target_stat = resolved.stat()
        snapshot[str(path)] = (
            path.readlink().as_posix() if path.is_symlink() else None,
            str(resolved),
            stat.st_ino,
            stat.st_mode,
            stat.st_size,
            stat.st_mtime_ns,
            target_stat.st_ino,
            target_stat.st_size,
            target_stat.st_mtime_ns,
            path.read_bytes() if path.name == "pyvenv.cfg" else None,
        )
    return snapshot


def _recording_executable(path: Path, body: str) -> None:
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['DISPATCH_LOG'], 'a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n" + body,
        encoding="utf-8",
    )
    path.chmod(0o755)


def _score_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    bin_dir = tmp_path / "trap bin"
    bin_dir.mkdir()
    log = tmp_path / "uv-calls.jsonl"
    _recording_executable(bin_dir / "uv", "raise SystemExit(97)\n")
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "DISPATCH_LOG": str(log),
    }
    env.pop("PIPY_PARITY_PYTHON", None)
    return env, log


def _run_score(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/parity_score.sh"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_legacy_parity_score_opts_into_trusted_workspace_fixtures(
    tmp_path: Path,
) -> None:
    env, uv_log = _score_environment(tmp_path)
    env["PIPY_PARITY_PYTHON"] = sys.executable
    before = _environment_snapshot()

    completed = _run_score(env)

    assert _environment_snapshot() == before
    assert not uv_log.exists(), "explicit score mode must never invoke uv"
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Score: 49 / 49" in completed.stdout
    assert "❌" not in completed.stdout


@pytest.mark.parametrize("path_kind", ["empty", "relative", "missing", "unrunnable"])
def test_explicit_score_invalid_interpreter_refuses_before_rows(
    tmp_path: Path, path_kind: str
) -> None:
    env, uv_log = _score_environment(tmp_path)
    interpreter = tmp_path / "unrunnable python"
    interpreter.write_text("#!/bin/sh\nexit 19\n", encoding="utf-8")
    interpreter.chmod(0o755)
    env["PIPY_PARITY_PYTHON"] = {
        "empty": "",
        "relative": ".venv/bin/python",
        "missing": str(tmp_path / "missing python"),
        "unrunnable": str(interpreter),
    }[path_kind]

    completed = _run_score(env)

    assert completed.returncode == 2
    assert "configuration error" in completed.stderr
    assert completed.stdout == ""
    assert not uv_log.exists()


@pytest.mark.parametrize(
    ("target", "missing"),
    [
        ("pytest", False),
        ("pipy", True),
        ("pipy-session", True),
        ("pipy", False),
        ("pipy-session", False),
    ],
)
def test_explicit_score_preflights_every_dispatch_before_rows(
    tmp_path: Path, target: str, missing: bool
) -> None:
    env, uv_log = _score_environment(tmp_path)
    bin_dir = tmp_path / "installed environment" / "bin"
    bin_dir.mkdir(parents=True)
    interpreter = bin_dir / "python"
    dispatch_log = tmp_path / "preflight.jsonl"
    # This test-owned interpreter records all admission attempts and simulates
    # only the unavailable dispatch; no real installation is removed or changed.
    _recording_executable(
        interpreter,
        "target = 'pytest' if sys.argv[1:3] == ['-m', 'pytest'] else "
        "os.path.basename(sys.argv[1])\n"
        "raise SystemExit(31 if target == os.environ['FAILED_TARGET'] else 0)\n",
    )
    for name in ("pipy", "pipy-session"):
        if not (missing and target == name):
            (bin_dir / name).write_text("# installed console script\n")
    # Keep the uv trap's log separate even though the recording interpreter
    # requires its own log destination.
    trap = Path(env["PATH"].split(os.pathsep)[0]) / "uv"
    trap.write_text(f"#!/bin/sh\nprintf called > '{uv_log}'\nexit 97\n")
    env.update(
        PIPY_PARITY_PYTHON=str(interpreter),
        DISPATCH_LOG=str(dispatch_log),
        FAILED_TARGET=target if not missing else "",
    )

    completed = _run_score(env)

    assert completed.returncode == 2
    assert "configuration error" in completed.stderr and target in completed.stderr
    assert completed.stdout == ""
    assert not uv_log.exists()
    calls = [json.loads(line) for line in dispatch_log.read_text().splitlines()]
    assert calls[0][0] == "-c"
    assert calls[1] == ["-m", "pytest", "--version"]
    expected_console = ["pipy", "pipy-session"][: (1 if target == "pipy" else 2)]
    if target == "pytest":
        expected_console = []
    if missing:
        expected_console.remove(target)
    assert calls[2:] == [[str(bin_dir / name), "--help"] for name in expected_console]


@pytest.mark.parametrize("exit_code", [0, 73])
def test_default_score_dispatch_uses_uv_and_propagates_failures(
    tmp_path: Path, exit_code: int
) -> None:
    env, uv_log = _score_environment(tmp_path)
    uv = Path(env["PATH"].split(os.pathsep)[0]) / "uv"
    _recording_executable(
        uv,
        f"print('pipy goal repl list search --mode') if {exit_code} == 0 else None\n"
        f"raise SystemExit({exit_code})\n",
    )
    before = _environment_snapshot()

    completed = _run_score(env)

    assert _environment_snapshot() == before
    calls = [json.loads(line) for line in uv_log.read_text().splitlines()]
    assert calls[0] == ["run", "python", "scripts/parity_checks/bash_behavior.py"]
    assert ["run", "pipy", "run", "--help"] in calls
    assert ["run", "pipy-session", "search", "--help"] in calls
    assert any(call[:3] == ["run", "pytest", "-q"] for call in calls)
    assert all(call[0] == "run" for call in calls)
    assert completed.returncode == (0 if exit_code == 0 else 1)
    assert ("❌  B7" in completed.stdout) == (exit_code != 0)


def test_explicit_score_dispatches_dormant_export_fallback(tmp_path: Path) -> None:
    env, uv_log = _score_environment(tmp_path)
    env["PIPY_PARITY_PYTHON"] = sys.executable
    script = (REPO_ROOT / "scripts/parity_score.sh").read_text()
    preflight = script.split("PASS=0\n", 1)[0]
    row = next(line for line in script.splitlines() if line.startswith("check E4 "))
    command = row.split('small  "', 1)[1][:-1]
    # The real E4 command sees no source file in this empty working directory,
    # so it must execute its installed-console fallback in a bash subshell.
    completed = subprocess.run(
        [
            "bash",
            "-c",
            preflight + '\ncd "$1"\nbash -c "$2"',
            str(REPO_ROOT / "scripts/parity_score.sh"),
            str(tmp_path),
            command,
        ],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not uv_log.exists()


@pytest.mark.parametrize(
    "probe",
    [
        "extension_activation_conformance.py",
        "extension_discovery_conformance.py",
        "extension_dispatch_conformance.py",
        "extension_providers_conformance.py",
        "extension_tool_call_conformance.py",
        "extension_tools_conformance.py",
        "provider_catalog_conformance.py",
    ],
)
def test_extension_probe_opts_into_trusted_workspace_fixtures(probe: str) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            f"scripts/parity_checks/{probe}",
            "--json",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["passed"] is True
    assert all(check["passed"] for check in payload["checks"])


def test_direct_workspace_extension_probes_make_trust_explicit() -> None:
    failures: list[str] = []
    for path in sorted((REPO_ROOT / "scripts" / "parity_checks").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        direct_names = {"discover_extensions"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                if alias.name == "discover_extensions":
                    direct_names.add(alias.asname or alias.name)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            direct_call = (
                isinstance(node.func, ast.Name) and node.func.id in direct_names
            )
            attribute_call = (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "discover_extensions"
            )
            if not direct_call and not attribute_call:
                continue
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            # ``home_dir`` is keyword-only in the production signature, so every
            # valid isolated-workspace call exposes it here.
            if "home_dir" not in keywords:
                continue
            trusted = keywords.get("include_workspace_defaults")
            if not isinstance(trusted, ast.Constant) or trusted.value is not True:
                failures.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert not failures, (
        "workspace extension probes must explicitly opt into trusted defaults: "
        + ", ".join(failures)
    )
