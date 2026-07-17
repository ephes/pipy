"""Regression checks for trust-aware standalone parity probes."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_legacy_parity_score_opts_into_trusted_workspace_fixtures() -> None:
    completed = subprocess.run(
        ["bash", "scripts/parity_score.sh"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Score: 49 / 49" in completed.stdout
    assert "❌" not in completed.stdout


def test_provider_catalog_probe_opts_into_trusted_extension_fixture() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/parity_checks/provider_catalog_conformance.py",
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
