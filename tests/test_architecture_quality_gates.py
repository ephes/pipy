"""Focused contracts for repository-wide formatting gate ownership."""

from __future__ import annotations

from pathlib import Path
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[1]
FORMAT_CHECK_COMMAND = "uv run ruff format --check ."
FORMAT_WRITE_COMMAND = "uv run ruff format ."
REVIEWED_ENDPOINT = "87c6f887f4afb719da89e68074551e9b8786ac1d"
COMMIT_FACTS = (
    (
        "7deb8d8807f4e7eb52f7c9c8bd9e0ad30cb60727",
        "docs: close architecture quality program",
    ),
    (
        "ffeb86f0319efd28f6f360174ae640fa358761d0",
        "docs: reconcile architecture program ledger",
    ),
    (
        "aea52b438713ce04fcad93ae32927ff156574aac",
        "docs: record integration warning closure",
    ),
    ("b64ceb7db9581bf3ebfab51f5803c513c1fdb549", "docs: align provider catalog status"),
    (REVIEWED_ENDPOINT, "docs: sync final integration ledger"),
)
PARTITION_FACTS = (
    "A: 29/29, 220,750 bytes/5,384 lines, valid complete CLEAN",
    "B: 22/22, 359,459 bytes/8,776 lines, valid complete CLEAN",
    "C: 14/14, 111,705 bytes/2,418 lines, valid complete CLEAN",
    "D: 103/103, 410,314 bytes/9,494 lines, valid complete CLEAN",
    "E: 150/150, 406,331 bytes/9,333 lines, valid complete CLEAN",
    "Refreshed F: 19/19, 139,365 bytes/1,892 lines, valid complete CLEAN",
    "G: 8/8, 36,717 bytes, valid complete CLEAN",
)
STABLE_VERIFICATION_FACTS = (
    "Latest stable verification for reviewed endpoint `87c6f88`",
    "strict Mypy across 169 source files",
    "combined Mypy across 438 source/test files",
    "`just check` at 4,829 passed / 2 skipped",
    "Ruff formatting covers 480 files",
    "34 / 18 repository/source C901 findings",
    "81,738 / 121,175 source/test physical lines",
    "43 `ToolLoopTerminalUi` fields",
    "one source ignore",
    "5,433 / 6,329 lines in `tool_loop_session.py` / `tui.py`",
    "Docs are clean",
    "both theme sources are `pi`",
    "pre-commit is absent",
)
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
    required_claims = (
        "13 program/integration commits",
        "final integration ledger is closed/reconciled at reviewed endpoint `87c6f887f4afb719da89e68074551e9b8786ac1d`",
        "A-G manifest union exactly covers all 298 changed paths",
        "covered A-G manifests/reports, prior cross-cutting evidence, final ledger files, and unchanged cross-contracts",
        "`STATE: CLEAN`, `COVERAGE_COMPLETE: yes`, `PARTITION_UNION_COMPLETE: yes`, and `VERDICT: CLEAN`",
        "zero Critical, Warning, or Suggestion findings",
        "`SCOPED_OMISSIONS: none`, `FORBIDDEN_TOOL_USES: 0`, `SKIPPED_FILES: none`, `TRUNCATIONS: none`, and `REDACTIONS: none`",
        "architecture-quality program and final integration review are closed/reconciled",
        "further review would add no material value unless scope changes",
        "bounded transactional-reload contract completion or formal reconciliation",
    )
    stale_claims = (
        "The integration ledger remains open",
        "its fresh cross-cutting re-review remains pending",
        "no overall integration CLEAN is claimed",
        "Current-worktree verification after this ledger/test fix",
        "final integration review is in progress",
    )

    for path in STATUS_DOCUMENTS:
        raw_document = _read(path)
        document = " ".join(raw_document.replace("\n> ", "\n").split())
        for revision, subject in COMMIT_FACTS:
            assert revision in document
            assert f"`{subject}`" in document
        for claim in PARTITION_FACTS + STABLE_VERIFICATION_FACTS + required_claims:
            assert claim in document
        for stale_claim in stale_claims:
            assert stale_claim not in document

    plan = _read(PROGRAM_DOCUMENTS[-1])
    assert "Status: completed/reconciled historical plan." in plan
    assert "it is no longer an active implementation queue" in plan
