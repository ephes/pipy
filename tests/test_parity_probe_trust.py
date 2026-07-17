"""Regression checks for trust-aware standalone parity probes."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys

import pytest


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
