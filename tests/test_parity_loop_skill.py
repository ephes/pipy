"""Structure/content invariants for the pipy-parity-loop skill.

The parity loop is a skill (instructions an agent follows), so these
tests pin the *shape* of the canonical body and its per-agent wrappers
rather than any runtime behavior.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pipy_harness.native.skills import (
    discover_workspace_skills,
    find_skill_by_name,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BODY = REPO_ROOT / "docs" / "parity-loop" / "skill-body.md"

# Tokens the canonical body MUST name so the gates/gap-sources stay explicit.
REQUIRED_BODY_TOKENS = (
    "just check",
    "prek",
    "docs/pi-mono-gap-audit.md",
    "docs/parity-plan.md",
    "docs/backlog.md",
    "different model family",
    "CLEAN",
    "Operator override",
    "Quota-constrained reviewer override",
    "REVIEWER_AGENT",
    "REVIEWER_MODEL",
    "Review directly; never delegate the review",
    "Agent`/Task-style delegation",
    "~/src/pi-mono",
    # Learning loop (Plan 2):
    "scripts/parity_lessons.py",
    "list --status open",
    "parity-improve",
    "Reflect",
    "transcript",
    "Run-end backstop",
    # Runner (Phase 2) single-gap mode marker:
    "runner single-gap mode",
    "Pi reference field list",
    "optionality",
    "derived identifiers",
)

PLACEHOLDER_TOKENS = ("TODO", "TBD", "FIXME", "XXX", "<placeholder>")


def test_canonical_body_exists() -> None:
    assert BODY.is_file(), f"missing canonical body: {BODY}"


def test_canonical_body_names_all_gates_and_gap_sources() -> None:
    text = BODY.read_text(encoding="utf-8")
    missing = [tok for tok in REQUIRED_BODY_TOKENS if tok not in text]
    assert not missing, f"canonical body is missing required tokens: {missing}"


def test_canonical_body_has_no_placeholders() -> None:
    text = BODY.read_text(encoding="utf-8")
    found = [tok for tok in PLACEHOLDER_TOKENS if tok in text]
    assert not found, f"canonical body contains placeholder tokens: {found}"


def test_planning_docs_use_neutral_repo_owned_paths() -> None:
    text = BODY.read_text(encoding="utf-8")
    assert "docs/specs/" in text
    assert "docs/superpowers" not in text

    legacy_root = REPO_ROOT / "docs" / "superpowers"
    legacy_files = (
        [
            path.relative_to(REPO_ROOT)
            for path in legacy_root.rglob("*")
            if path.is_file()
        ]
        if legacy_root.exists()
        else []
    )
    assert not legacy_files, f"legacy planning files remain: {legacy_files}"

    historical_roots = (
        REPO_ROOT / "docs" / "audit",
        REPO_ROOT / "docs" / "parity-loop" / "reports",
        REPO_ROOT / "docs" / "parity-loop" / "runs",
    )
    active_files = [
        path
        for path in (REPO_ROOT / "docs").rglob("*.md")
        if not any(path.is_relative_to(root) for root in historical_roots)
    ]
    active_files.extend((REPO_ROOT / "scripts").rglob("*.py"))
    active_files.extend((REPO_ROOT / "src").rglob("*.py"))
    active_files.extend(
        path
        for name in ("AGENTS.md", "CHANGELOG.md", "README.md")
        if (path := REPO_ROOT / name).is_file()
    )
    legacy_path_markers = (
        "docs/superpowers",
        "superpowers/plans",
        "superpowers/specs",
    )
    legacy_references = [
        path.relative_to(REPO_ROOT)
        for path in active_files
        if any(
            marker in path.read_text(encoding="utf-8") for marker in legacy_path_markers
        )
    ]
    assert not legacy_references, (
        f"active files still reference the legacy planning path: {legacy_references}"
    )

    plugin_directives = [
        path.relative_to(REPO_ROOT)
        for directory in (REPO_ROOT / "docs" / "plans", REPO_ROOT / "docs" / "specs")
        for path in directory.glob("*.md")
        if "superpowers:" in path.read_text(encoding="utf-8")
    ]
    assert not plugin_directives, (
        "neutral planning docs still require disabled Superpowers skills: "
        f"{plugin_directives}"
    )

    local_link_pattern = re.compile(r"]\((?!https?://|mailto:|#)([^)#\s]+)")
    broken_links = [
        (path.relative_to(REPO_ROOT), target)
        for directory in (REPO_ROOT / "docs" / "plans", REPO_ROOT / "docs" / "specs")
        for path in directory.glob("*.md")
        for target in local_link_pattern.findall(path.read_text(encoding="utf-8"))
        if not (path.parent / target).resolve().exists()
    ]
    assert not broken_links, (
        f"planning documents contain broken local links: {broken_links}"
    )


WRAPPERS = (
    REPO_ROOT / ".claude" / "skills" / "pipy-parity-loop" / "SKILL.md",
    REPO_ROOT / ".pipy" / "skills" / "pipy-parity-loop.md",
    REPO_ROOT / ".pi" / "skills" / "pipy-parity-loop.md",
)

# Wrappers are thin pointers; cap keeps the workflow body from being duplicated.
WRAPPER_MAX_BYTES = 1500
BODY_REFERENCE = "docs/parity-loop/skill-body.md"


@pytest.mark.parametrize(
    "wrapper", WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_wrapper_exists(wrapper: Path) -> None:
    assert wrapper.is_file(), f"missing wrapper: {wrapper}"


@pytest.mark.parametrize(
    "wrapper", WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_wrapper_references_canonical_body(wrapper: Path) -> None:
    text = wrapper.read_text(encoding="utf-8")
    assert BODY_REFERENCE in text, f"{wrapper} must point at {BODY_REFERENCE}"


@pytest.mark.parametrize(
    "wrapper", WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_wrapper_has_frontmatter_name(wrapper: Path) -> None:
    text = wrapper.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{wrapper} must start with YAML frontmatter"
    assert "name: pipy-parity-loop" in text, f"{wrapper} must declare its name"
    assert "description:" in text, f"{wrapper} must declare a description"


@pytest.mark.parametrize(
    "wrapper", WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_wrapper_does_not_duplicate_body(wrapper: Path) -> None:
    size = wrapper.stat().st_size
    assert size <= WRAPPER_MAX_BYTES, (
        f"{wrapper} is {size} bytes (> {WRAPPER_MAX_BYTES}); it likely duplicates "
        "the workflow body instead of pointing at it"
    )
    # The numbered phase list belongs only in the canonical body.
    text = wrapper.read_text(encoding="utf-8")
    assert "1. **Select the gap.**" not in text, (
        f"{wrapper} contains workflow body content; keep it a thin pointer"
    )


def test_claude_parity_wrapper_forbids_subagent_delegation() -> None:
    text = (
        REPO_ROOT / ".claude" / "skills" / "pipy-parity-loop" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "Do not delegate parity-loop" in text
    assert "You may delegate" not in text


def test_agents_md_has_parity_section() -> None:
    text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Parity loop" in text, "AGENTS.md must have a '## Parity loop' section"
    assert BODY_REFERENCE in text, "AGENTS.md parity section must point at the body"


def test_pipy_discovers_parity_loop_skill() -> None:
    skills, _cap_reached = discover_workspace_skills(
        REPO_ROOT,
        config_home_env={},  # don't read the real ~/.config/pipy
        home_dir=REPO_ROOT,
        per_file_byte_cap=64 * 1024,
        total_byte_cap=256 * 1024,
        include_workspace_defaults=True,
    )
    found = find_skill_by_name(skills, "pipy-parity-loop")
    assert found is not None, "pipy did not discover the pipy-parity-loop skill"
    assert found.path_label == ".pipy/skills/pipy-parity-loop.md", found.path_label


IMPROVE_BODY = REPO_ROOT / "docs" / "parity-loop" / "improve-body.md"

IMPROVE_REQUIRED_TOKENS = (
    "scripts/parity_lessons.py",
    "list --status open",
    "different",  # different model family review
    "CLEAN",
    "sign-off",
    "mark",  # mark applied/rejected
    "validate",
    "materializ",  # materialization language
    "runner unattended mode",
    "Never delegate the review",
)

FD_PRESSURE_GATE_TOKENS = (
    "Too many open files",
    "ulimit -n 4096; just check",
    "flaky PTY test",
)


def test_improve_body_exists() -> None:
    assert IMPROVE_BODY.is_file(), f"missing improve body: {IMPROVE_BODY}"


def test_improve_body_names_required_tokens() -> None:
    text = IMPROVE_BODY.read_text(encoding="utf-8")
    missing = [tok for tok in IMPROVE_REQUIRED_TOKENS if tok not in text]
    assert not missing, f"improve body is missing required tokens: {missing}"


def test_improve_body_has_no_placeholders() -> None:
    text = IMPROVE_BODY.read_text(encoding="utf-8")
    found = [tok for tok in PLACEHOLDER_TOKENS if tok in text]
    assert not found, f"improve body contains placeholder tokens: {found}"


@pytest.mark.parametrize(
    "body", (BODY, IMPROVE_BODY), ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_gate_steps_cover_fd_pressure_retry(body: Path) -> None:
    text = body.read_text(encoding="utf-8")
    missing = [tok for tok in FD_PRESSURE_GATE_TOKENS if tok not in text]
    assert not missing, (
        f"{body.relative_to(REPO_ROOT)} lacks gate retry tokens: {missing}"
    )


IMPROVE_WRAPPERS = (
    REPO_ROOT / ".claude" / "skills" / "parity-improve" / "SKILL.md",
    REPO_ROOT / ".pipy" / "skills" / "parity-improve.md",
    REPO_ROOT / ".pi" / "skills" / "parity-improve.md",
)
IMPROVE_BODY_REFERENCE = "docs/parity-loop/improve-body.md"


@pytest.mark.parametrize(
    "wrapper", IMPROVE_WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_improve_wrapper_exists(wrapper: Path) -> None:
    assert wrapper.is_file(), f"missing wrapper: {wrapper}"


@pytest.mark.parametrize(
    "wrapper", IMPROVE_WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_improve_wrapper_references_body(wrapper: Path) -> None:
    text = wrapper.read_text(encoding="utf-8")
    assert IMPROVE_BODY_REFERENCE in text, (
        f"{wrapper} must point at {IMPROVE_BODY_REFERENCE}"
    )


@pytest.mark.parametrize(
    "wrapper", IMPROVE_WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_improve_wrapper_has_frontmatter_name(wrapper: Path) -> None:
    text = wrapper.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{wrapper} must start with YAML frontmatter"
    assert "name: parity-improve" in text, f"{wrapper} must declare its name"
    assert "description:" in text, f"{wrapper} must declare a description"


@pytest.mark.parametrize(
    "wrapper", IMPROVE_WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_improve_wrapper_does_not_duplicate_body(wrapper: Path) -> None:
    assert wrapper.stat().st_size <= WRAPPER_MAX_BYTES, (
        f"{wrapper} is too large; it likely duplicates the improve body"
    )
    text = wrapper.read_text(encoding="utf-8")
    assert "1. **Read open lessons.**" not in text, (
        f"{wrapper} contains improve-body content; keep it a thin pointer"
    )


def test_claude_improve_wrapper_forbids_subagent_delegation() -> None:
    text = (REPO_ROOT / ".claude" / "skills" / "parity-improve" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "do not\ndelegate parity-improve" in text
    assert "You may delegate" not in text


def test_agents_md_has_parity_improve_section() -> None:
    text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Parity improve" in text, (
        "AGENTS.md must have a '## Parity improve' section"
    )
    assert IMPROVE_BODY_REFERENCE in text, (
        "AGENTS.md parity-improve section must point at the body"
    )


def test_pipy_discovers_parity_improve_skill() -> None:
    skills, _cap_reached = discover_workspace_skills(
        REPO_ROOT,
        config_home_env={},  # don't read the real ~/.config/pipy
        home_dir=REPO_ROOT,
        per_file_byte_cap=64 * 1024,
        total_byte_cap=256 * 1024,
        include_workspace_defaults=True,
    )
    found = find_skill_by_name(skills, "parity-improve")
    assert found is not None, "pipy did not discover the parity-improve skill"
    assert found.path_label == ".pipy/skills/parity-improve.md", found.path_label
