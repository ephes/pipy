"""Focused contracts for repository-wide formatting gate ownership."""

from __future__ import annotations

from pathlib import Path
import tomllib


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


def test_architecture_assessment_navigation_and_reference_identity() -> None:
    assessment_path = "2026-07-29-architecture-quality-assessment.md"
    assessment = _read(f"docs/{assessment_path}")
    architecture = _read("docs/architecture.md")
    backlog = _read("docs/backlog.md")
    index = _read("docs/index.md")
    nav = _read("zensical.toml")
    readme = _read("README.md")

    for revision in (
        "e35a0d54898c160ac37acbdbdd35fff727569508",
        "edd4ccc6171420015fa0f04bec75d38fe32beb68",
        "7df73a00c6cf85c000bf1ce1594c9284067a92f0",
    ):
        assert revision in assessment
    assert "openai-codex/gpt-5.6-sol" in assessment
    assert assessment_path in architecture
    assert assessment_path in backlog
    assert assessment_path in index
    assert assessment_path in nav
    assert "Codex WebSocket transport uses" in readme
    assert "declared `websockets` dependency" in readme
    assert "independent review complete; landed Slice 16 commit pending" in (
        " ".join(assessment.split())
    )
