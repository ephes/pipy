"""Regression tests for the bytecode guard plugin (tests/pytest_plugins)."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pipy_bytecode_guard
import pytest

PLUGIN_DIR = Path(__file__).parent / "pytest_plugins"


def test_suite_collection_leaves_bytecode_writing_as_started(
    request: pytest.FixtureRequest,
) -> None:
    assert request.config.pluginmanager.has_plugin("pipy_bytecode_guard")
    assert sys.dont_write_bytecode == pipy_bytecode_guard.INITIAL_DONT_WRITE_BYTECODE


def _write_project(root: Path) -> None:
    (root / "flag_setter.py").write_text(
        "import sys\nsys.dont_write_bytecode = True\n", encoding="utf-8"
    )
    (root / "test_a_imports_flag_setter.py").write_text(
        "import flag_setter\n\n\ndef test_a():\n    assert flag_setter\n",
        encoding="utf-8",
    )
    (root / "test_b_after.py").write_text(
        textwrap.dedent(
            """
            import sys


            def test_b():
                assert sys.dont_write_bytecode is False
            """
        ),
        encoding="utf-8",
    )
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")


def _run_pytest(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONDONTWRITEBYTECODE", "PYTEST_ADDOPTS"}
    }
    env["PYTHONPATH"] = str(PLUGIN_DIR)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _rewrite_caches(root: Path, stem: str) -> list[Path]:
    return sorted((root / "__pycache__").glob(f"{stem}.*pytest-*.pyc"))


@pytest.mark.parametrize("guarded", [True, False])
def test_guard_restores_bytecode_writing_after_a_collected_import(
    tmp_path: Path, guarded: bool
) -> None:
    _write_project(tmp_path)
    plugin_args = ("-p", "pipy_bytecode_guard") if guarded else ()

    result = _run_pytest(tmp_path, *plugin_args)

    if guarded:
        assert result.returncode == 0, result.stdout + result.stderr
        assert _rewrite_caches(tmp_path, "test_b_after")
    else:
        # Without the guard the bug reproduces: the module collected after the
        # flag was set gets no rewrite cache and sees bytecode writing disabled.
        assert result.returncode == 1, result.stdout + result.stderr
        assert not _rewrite_caches(tmp_path, "test_b_after")


def test_guard_keeps_bytecode_writing_disabled_under_dash_b(tmp_path: Path) -> None:
    _write_project(tmp_path)
    (tmp_path / "test_b_after.py").write_text(
        "import sys\n\n\ndef test_b():\n    assert sys.dont_write_bytecode is True\n",
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items() if key != "PYTEST_ADDOPTS"}
    env["PYTHONPATH"] = str(PLUGIN_DIR)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "pipy_bytecode_guard",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not _rewrite_caches(tmp_path, "test_b_after")
