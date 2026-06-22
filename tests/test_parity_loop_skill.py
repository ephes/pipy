"""Structure/content invariants for the pipy-parity-loop skill.

The parity loop is a skill (instructions an agent follows), so these
tests pin the *shape* of the canonical body and its per-agent wrappers
rather than any runtime behavior.
"""

from __future__ import annotations

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
    "pre-commit",
    "docs/pi-mono-gap-audit.md",
    "docs/parity-plan.md",
    "docs/backlog.md",
    "different model family",
    "CLEAN",
    "Operator override",
    "~/src/pi-mono",
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


WRAPPERS = (
    REPO_ROOT / ".claude" / "skills" / "pipy-parity-loop" / "SKILL.md",
    REPO_ROOT / ".pipy" / "skills" / "pipy-parity-loop.md",
    REPO_ROOT / ".pi" / "skills" / "pipy-parity-loop.md",
)

# Wrappers are thin pointers; cap keeps the workflow body from being duplicated.
WRAPPER_MAX_BYTES = 1500
BODY_REFERENCE = "docs/parity-loop/skill-body.md"


@pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_wrapper_exists(wrapper: Path) -> None:
    assert wrapper.is_file(), f"missing wrapper: {wrapper}"


@pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_wrapper_references_canonical_body(wrapper: Path) -> None:
    text = wrapper.read_text(encoding="utf-8")
    assert BODY_REFERENCE in text, f"{wrapper} must point at {BODY_REFERENCE}"


@pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_wrapper_has_frontmatter_name(wrapper: Path) -> None:
    text = wrapper.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{wrapper} must start with YAML frontmatter"
    assert "name: pipy-parity-loop" in text, f"{wrapper} must declare its name"
    assert "description:" in text, f"{wrapper} must declare a description"


@pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
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


def test_agents_md_has_parity_section() -> None:
    text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Parity loop" in text, "AGENTS.md must have a '## Parity loop' section"
    assert BODY_REFERENCE in text, "AGENTS.md parity section must point at the body"


def test_pipy_discovers_parity_loop_skill() -> None:
    skills, _cap_reached = discover_workspace_skills(
        REPO_ROOT,
        config_home_env={},        # don't read the real ~/.config/pipy
        home_dir=REPO_ROOT,
        per_file_byte_cap=64 * 1024,
        total_byte_cap=256 * 1024,
    )
    found = find_skill_by_name(skills, "pipy-parity-loop")
    assert found is not None, "pipy did not discover the pipy-parity-loop skill"
    assert found.path_label == ".pipy/skills/pipy-parity-loop.md", found.path_label
