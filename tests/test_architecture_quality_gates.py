"""Focused contracts for repository-wide formatting gate ownership."""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FORMAT_CHECK_COMMAND = "uv run ruff format --check ."
FORMAT_WRITE_COMMAND = "uv run ruff format ."


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_local_and_ci_gates_share_the_format_check_recipe() -> None:
    justfile = _read("justfile")
    workflow = _read(".github/workflows/ci.yml")

    assert (
        "# Check repository-wide Python formatting without changing files.\n"
        "format-check:\n"
        f"    {FORMAT_CHECK_COMMAND}\n"
    ) in justfile
    assert "check: lint format-check typecheck test" in justfile
    assert justfile.count(FORMAT_CHECK_COMMAND) == 1

    assert workflow.count("run: just format-check") == 1
    assert FORMAT_CHECK_COMMAND not in workflow


def test_contributor_docs_name_format_check_and_write_commands() -> None:
    readme = _read("README.md")

    assert "just format-check  # ruff format --check across the repository" in readme
    assert f"`{FORMAT_CHECK_COMMAND}`" in readme
    assert f"`{FORMAT_WRITE_COMMAND}`" in readme
    assert "(or `just format`)" in readme


def test_formatter_gate_has_no_custom_repository_exclusions() -> None:
    config = tomllib.loads(_read("pyproject.toml"))
    ruff = config["tool"]["ruff"]

    assert "exclude" not in ruff
    assert "extend-exclude" not in ruff
    assert not (REPO_ROOT / ".ruff.toml").exists()
    assert not (REPO_ROOT / "ruff.toml").exists()


def test_ruff_import_order_gate_selects_exact_i001() -> None:
    config = tomllib.loads(_read("pyproject.toml"))
    lint = config["tool"]["ruff"]["lint"]
    extend_select = lint["extend-select"]
    configured_selectors = lint.get("select", []) + extend_select
    ignored_selectors = lint.get("ignore", []) + lint.get("extend-ignore", [])
    for selectors in lint.get("per-file-ignores", {}).values():
        ignored_selectors.extend(selectors)

    assert "I001" in extend_select
    assert {
        selector
        for selector in configured_selectors
        if selector == "ALL" or selector.startswith("I")
    } == {"I001"}
    assert all(
        selector != "ALL" and not "I001".startswith(selector)
        for selector in ignored_selectors
    )


def test_readme_names_the_codex_websocket_dependency() -> None:
    readme = _read("README.md")

    assert "Codex WebSocket transport uses" in readme
    assert "declared `websockets` dependency" in readme
