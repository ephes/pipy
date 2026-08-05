"""Ownership contracts for the root-level startup selector adapters."""

from __future__ import annotations

import ast
from pathlib import Path

import pipy_harness.native.startup_selectors as startup_selectors
import pipy_harness.native.tui as tui

REPO_ROOT = Path(__file__).resolve().parents[1]
NEW_MODULE = "pipy_harness.native.startup_selectors"
OLD_MODULE = "pipy_harness.native.tui"
MOVED_NAMES = frozenset(
    {
        "run_project_trust_selector",
        "run_startup_project_trust_selector",
        "run_startup_session_picker",
    }
)


def _repository_python_paths() -> list[Path]:
    return [
        path
        for root_name in ("src", "tests", "scripts")
        for path in (REPO_ROOT / root_name).rglob("*.py")
    ]


def test_startup_selectors_have_one_definition_site_and_no_old_attributes() -> None:
    definition_sites: dict[str, set[str]] = {name: set() for name in MOVED_NAMES}
    for path in (REPO_ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                if node.name in definition_sites:
                    definition_sites[node.name].add(str(path.relative_to(REPO_ROOT)))

    expected_path = "src/pipy_harness/native/startup_selectors.py"
    assert definition_sites == {name: {expected_path} for name in MOVED_NAMES}
    for name in MOVED_NAMES:
        assert getattr(startup_selectors, name).__module__ == NEW_MODULE
        assert not hasattr(tui, name)


def test_direct_importers_and_embedded_scripts_use_the_definition_site() -> None:
    wrong_imports: list[tuple[str, int, str | None, str]] = []
    stale_embedded_references: list[tuple[str, int]] = []
    for path in _repository_python_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for imported in node.names:
                    if imported.name in MOVED_NAMES and node.module != NEW_MODULE:
                        wrong_imports.append(
                            (
                                str(path.relative_to(REPO_ROOT)),
                                node.lineno,
                                node.module,
                                imported.name,
                            )
                        )
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and OLD_MODULE in node.value
                and any(name in node.value for name in MOVED_NAMES)
            ):
                stale_embedded_references.append(
                    (str(path.relative_to(REPO_ROOT)), node.lineno)
                )

    assert wrong_imports == []
    assert stale_embedded_references == []
