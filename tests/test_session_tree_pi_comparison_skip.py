"""The Pi comparison gates never read a missing Pi reference as a pass.

When Pi cannot be driven (checkout/deps absent), the comparison did not run, so
each script must report ``passed: false`` and exit 2 (distinct from a
comparison failure, 1). The session-tree script still runs its pipy
product-path leg, which must pass on its own.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_CHECKS = Path(__file__).resolve().parents[1] / "scripts" / "parity_checks"


def _run(script: str, tmp_path: Path) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "PI_MONO_DIR": str(tmp_path / "no-such-pi-checkout")}
    return subprocess.run(
        [sys.executable, str(_CHECKS / script), "--json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize(
    "script", ["session_tree_pi_comparison.py", "automation_pi_comparison.py"]
)
def test_pi_unavailable_is_not_a_pass(script: str, tmp_path: Path) -> None:
    proc = _run(script, tmp_path)
    assert proc.returncode == 2, proc.stderr.decode("utf-8")[:500]
    report = json.loads(proc.stdout.decode("utf-8"))
    assert report["passed"] is False
    assert report["skipped"] is True
    marker = [c for c in report["checks"] if c["name"] == "pi_reference_available"]
    assert len(marker) == 1
    assert marker[0]["passed"] is False
    assert "pi-mono not found" in marker[0]["detail"]


def _fake_pi_checkout(root: Path) -> Path:
    """A checkout that passes the availability check but whose Pi code throws."""

    (root / "node_modules").mkdir(parents=True)
    resolver = root / "packages/coding-agent/src/experimental/source-resolver.ts"
    resolver.parent.mkdir(parents=True)
    resolver.write_text("export {};\n", encoding="utf-8")
    for rel in (
        "packages/coding-agent/test/test-harness.ts",
        "packages/coding-agent/src/core/session-manager.ts",
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('throw new Error("broken pi");\n', encoding="utf-8")
    return root


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize(
    "script", ["session_tree_pi_comparison.py", "automation_pi_comparison.py"]
)
def test_pi_driver_failure_is_a_reported_failure(script: str, tmp_path: Path) -> None:
    _fake_pi_checkout(tmp_path / "pi")
    # A relative PI_MONO_DIR must resolve before the driver's cwd changes.
    env = {**os.environ, "PI_MONO_DIR": "pi"}
    proc = subprocess.run(
        [sys.executable, str(_CHECKS / script), "--json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=tmp_path,
        timeout=120,
        check=False,
    )
    report = json.loads(proc.stdout.decode("utf-8"))
    if report["skipped"]:
        pytest.skip("node older than pi-mono's minimum")
    assert proc.returncode == 1
    assert report["passed"] is False
    errors = [c for c in report["checks"] if c["name"] == "pi_driver_error"]
    assert len(errors) == 1
    assert "broken pi" in errors[0]["detail"]


def test_session_tree_pipy_leg_still_runs_without_pi(tmp_path: Path) -> None:
    report = json.loads(
        _run("session_tree_pi_comparison.py", tmp_path).stdout.decode("utf-8")
    )
    pipy_checks = [c for c in report["checks"] if c["name"].startswith("pipy_")]
    assert pipy_checks
    assert all(c["passed"] for c in pipy_checks)
