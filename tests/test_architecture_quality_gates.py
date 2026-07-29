"""Focused contracts for repository-wide formatting gate ownership."""

from __future__ import annotations

from pathlib import Path
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[1]
FORMAT_CHECK_COMMAND = "uv run ruff format --check ."
FORMAT_WRITE_COMMAND = "uv run ruff format ."
SLICE_16_REVISION = "7deb8d8807f4e7eb52f7c9c8bd9e0ad30cb60727"
SLICE_16_SUBJECT = "docs: close architecture quality program"
LEDGER_FIX_REVISION = "ffeb86f"
LEDGER_FIX_SUBJECT = "docs: reconcile architecture program ledger"
PROGRAM_DOCUMENTS = (
    "docs/2026-07-29-architecture-quality-assessment.md",
    "docs/architecture.md",
    "docs/backlog.md",
    "docs/index.md",
    "docs/pi-mono-gap-audit.md",
    "docs/specs/2026-07-24-architecture-quality-improvement-plan.md",
)
RELATED_HISTORICAL_DOCUMENTS = ("docs/architecture-migration.md",)
STATUS_DOCUMENTS = PROGRAM_DOCUMENTS + RELATED_HISTORICAL_DOCUMENTS


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


def test_architecture_program_closeout_ledgers_stay_synchronized() -> None:
    stale_claims = (
        "landed Slice 16 commit pending",
        "Slice 16 commit hash remains pending",
        "No Slice 16 commit hash exists yet",
        "Status: active implementation program",
        "is being fixed",
        "being fixed by this ledger synchronization",
        "now being synchronized",
        "reason for the present documentation-only fix",
        "fix is under review",
    )
    required_claims = (
        "exhaustive partitions A–E are complete CLEAN",
        "valid, complete original Bundle F found one documentation-ledger Warning",
        "`openai-codex/gpt-5.6-sol` implementer fixed it",
        "valid, complete focused re-review was CLEAN",
        "fix landed as `ffeb86f`",
        "closing the Warning",
        "Final cross-cutting integration review remains pending",
        "no overall integration CLEAN is claimed",
        "bounded transactional-reload contract completion or formal reconciliation",
    )

    for path in STATUS_DOCUMENTS:
        document = " ".join(_read(path).replace("\n> ", "\n").split())
        assert SLICE_16_REVISION in document
        assert f"`{SLICE_16_SUBJECT}`" in document
        assert LEDGER_FIX_REVISION in document
        assert f"`{LEDGER_FIX_SUBJECT}`" in document
        for claim in required_claims:
            assert claim in document
        for stale_claim in stale_claims:
            assert stale_claim not in document

    plan = _read(PROGRAM_DOCUMENTS[-1])
    assert "Status: completed/reconciled historical plan." in plan
    assert "it is no longer an active implementation queue" in plan
