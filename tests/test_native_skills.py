"""Focused tests for the workspace skill discovery loader.

These tests pin the discovery rules listed in
`pipy_harness.native.skills`. They never wire the loader into a
provider, the REPL, or the session archive; the integrator wires
`compose_skills_system_block` into the system-prompt composition and
the `/skill <name>` slash command separately.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pipy_harness.native._resource_files import (
    PIPY_CONFIG_DIR_NAME,
    PIPY_CONFIG_HOME_ENV,
    XDG_CONFIG_HOME_ENV,
)
from pipy_harness.native.skills import (
    SkillFile,
    compose_skills_system_block,
    discover_workspace_skills,
    safe_skill_metadata,
)


def _empty_env() -> dict[str, str]:
    return {}


def _discover(
    workspace: Path,
    *,
    env: dict[str, str] | None = None,
    home_dir: Path | None = None,
    per_file_byte_cap: int = 64 * 1024,
    total_byte_cap: int = 256 * 1024,
) -> tuple[list[SkillFile], bool]:
    return discover_workspace_skills(
        workspace,
        config_home_env=env if env is not None else _empty_env(),
        home_dir=home_dir if home_dir is not None else workspace,
        per_file_byte_cap=per_file_byte_cap,
        total_byte_cap=total_byte_cap,
    )


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


def _write_skill(
    directory: Path,
    *,
    filename: str,
    name: str | None = None,
    description: str | None = None,
    body: str = "skill body\n",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    if name is not None or description is not None:
        parts.append("---")
        if name is not None:
            parts.append(f"name: {name}")
        if description is not None:
            parts.append(f"description: {description}")
        parts.append("---")
        parts.append("")
    parts.append(body)
    path = directory / filename
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def test_no_skills_dir_returns_empty_list(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    skills, cap_reached = _discover(workspace)
    assert skills == []
    assert cap_reached is False


def test_discovers_workspace_skills(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    skills_dir = workspace / ".pipy" / "skills"
    _write_skill(
        skills_dir,
        filename="lint.md",
        name="lint",
        description="Run linters",
        body="lint with ruff\n",
    )
    _write_skill(
        skills_dir,
        filename="test.md",
        name="test",
        description="Run the test suite",
        body="run pytest\n",
    )

    skills, cap_reached = _discover(workspace)

    assert cap_reached is False
    names = {skill.name for skill in skills}
    assert names == {"lint", "test"}
    lint = next(skill for skill in skills if skill.name == "lint")
    assert lint.description == "Run linters"
    assert "lint with ruff" in lint.body
    assert lint.path_label == ".pipy/skills/lint.md"
    assert lint.truncated is False


def test_discovers_global_skills_via_pipy_config_home(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    pipy_home = tmp_path / "pipy-home"
    global_skills_dir = pipy_home / "skills"
    _write_skill(
        global_skills_dir,
        filename="explain.md",
        name="explain",
        description="Explain code",
        body="explain in plain English\n",
    )

    env = {PIPY_CONFIG_HOME_ENV: str(pipy_home)}
    skills, cap_reached = _discover(workspace, env=env)

    assert cap_reached is False
    assert len(skills) == 1
    only = skills[0]
    assert only.name == "explain"
    assert only.path_label == "<global>/skills/explain.md"
    assert "explain in plain English" in only.body


def test_global_root_precedence_uses_pipy_config_home_first(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    pipy_home = tmp_path / "pipy-home"
    _write_skill(
        pipy_home / "skills",
        filename="lint.md",
        name="lint-from-pipy-home",
        description="winning",
    )
    xdg_root = tmp_path / "xdg"
    xdg_pipy = xdg_root / PIPY_CONFIG_DIR_NAME / "skills"
    _write_skill(
        xdg_pipy,
        filename="lint.md",
        name="lint-from-xdg",
        description="losing",
    )
    home = tmp_path / "home"
    home_pipy = home / ".config" / PIPY_CONFIG_DIR_NAME / "skills"
    _write_skill(
        home_pipy,
        filename="lint.md",
        name="lint-from-home",
        description="losing",
    )

    env = {
        PIPY_CONFIG_HOME_ENV: str(pipy_home),
        XDG_CONFIG_HOME_ENV: str(xdg_root),
    }
    skills, _ = _discover(workspace, env=env, home_dir=home)

    assert len(skills) == 1
    assert skills[0].name == "lint-from-pipy-home"
    assert skills[0].path_label == "<global>/skills/lint.md"


def test_dedupes_by_canonical_path(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    skills_dir = workspace / ".pipy" / "skills"
    shared = _write_skill(
        skills_dir,
        filename="shared.md",
        name="shared",
        description="shared",
    )
    (skills_dir / "alias.md").symlink_to(shared)

    skills, _ = _discover(workspace)

    assert len(skills) == 1
    assert skills[0].name == "shared"


def test_refuses_symlink_outside_workspace(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    secret = outside_dir / "secret.md"
    secret.write_text("never load me\n", encoding="utf-8")

    skills_dir = workspace / ".pipy" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "leak.md").symlink_to(secret)

    # A legitimate skill alongside the bad symlink still discovers.
    _write_skill(
        skills_dir,
        filename="legitimate.md",
        name="legitimate",
        description="real",
        body="real body\n",
    )

    skills, _ = _discover(workspace)

    assert all(skill.name != "leak" for skill in skills)
    assert all("never load me" not in skill.body for skill in skills)
    assert any(skill.name == "legitimate" for skill in skills)


def test_per_file_byte_cap_truncates_with_marker(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    skills_dir = workspace / ".pipy" / "skills"
    payload = "x" * 4096
    _write_skill(
        skills_dir,
        filename="big.md",
        name="big",
        description="big",
        body=payload,
    )

    skills, cap_reached = _discover(workspace, per_file_byte_cap=1024)
    assert cap_reached is False
    big = next(skill for skill in skills if skill.name == "big")
    assert big.truncated is True
    on_disk = (skills_dir / "big.md").read_bytes()
    assert big.byte_length == len(on_disk)
    assert big.sha256 == hashlib.sha256(on_disk).hexdigest()
    assert "[pipy: resource file truncated at 1024 bytes]" in big.body


def test_total_byte_cap_reached_flag_set(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    skills_dir = workspace / ".pipy" / "skills"
    payload = "a" * 800
    for index, letter in enumerate("abc"):
        _write_skill(
            skills_dir,
            filename=f"{letter}.md",
            name=letter,
            description=f"file {index}",
            body=payload,
        )

    skills, cap_reached = _discover(
        workspace,
        per_file_byte_cap=4096,
        total_byte_cap=900,
    )

    assert cap_reached is True
    # Only one file fits under the 900-byte total cap.
    assert len(skills) == 1


def test_frontmatter_parsed_with_name_and_description(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    skills_dir = workspace / ".pipy" / "skills"
    _write_skill(
        skills_dir,
        filename="explain.md",
        name="explain-code",
        description="Explain the highlighted code",
        body="please explain\n",
    )
    # A file without frontmatter falls back to the filename stem.
    plain = skills_dir / "plain.md"
    skills_dir.mkdir(parents=True, exist_ok=True)
    plain.write_text("just a body\n", encoding="utf-8")

    skills, _ = _discover(workspace)
    by_name = {skill.name: skill for skill in skills}
    assert "explain-code" in by_name
    explain = by_name["explain-code"]
    assert explain.description == "Explain the highlighted code"
    assert "please explain" in explain.body
    assert "---" not in explain.body
    assert "plain" in by_name
    plain_skill = by_name["plain"]
    assert plain_skill.description == ""
    assert "just a body" in plain_skill.body


def test_compose_skills_system_block_includes_name_and_description(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    skills_dir = workspace / ".pipy" / "skills"
    _write_skill(
        skills_dir,
        filename="lint.md",
        name="lint",
        description="Run linters",
        body="real body that must not appear in the block\n",
    )
    _write_skill(
        skills_dir,
        filename="bare.md",
        name="bare",
        description="",
        body="another body that must not appear\n",
    )

    skills, _ = _discover(workspace)
    block = compose_skills_system_block(skills)

    assert "Available skills" in block
    assert "/skill <name>" in block
    assert "- lint: Run linters" in block
    # Bare skill (empty description) still appears as a single name line.
    assert "- bare" in block
    # Bodies must NEVER leak into the block.
    assert "real body" not in block
    assert "another body" not in block


def test_compose_skills_system_block_empty_returns_empty_string() -> None:
    assert compose_skills_system_block([]) == ""


def test_no_skill_body_in_returned_metadata_archive_function(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    skills_dir = workspace / ".pipy" / "skills"
    _write_skill(
        skills_dir,
        filename="lint.md",
        name="lint",
        description="Run linters",
        body="sensitive instructions that must never reach the archive\n",
    )

    skills, _ = _discover(workspace)
    safe = safe_skill_metadata(skills)

    assert len(safe) == 1
    entry = safe[0]
    assert set(entry.keys()) == {"path_label", "sha256", "byte_length", "truncated"}
    assert entry["path_label"] == ".pipy/skills/lint.md"
    assert "sensitive instructions" not in str(entry)
    assert "lint" not in str(entry["sha256"])  # only the hash hex appears
    # No name, description, or body leak.
    assert "name" not in entry
    assert "description" not in entry
    assert "body" not in entry
