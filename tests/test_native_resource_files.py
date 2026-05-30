"""Safety-policy tests for the shared resource-file discovery loader.

These pin the per-candidate safety screen added on top of the byte-cap
and symlink-containment rules: secret-shaped filenames, binary
content, and generated/ignored filenames are skipped silently, while
legitimate neighbours still load. The skills loader is used as the
concrete entry point; the policy lives in `_resource_files` and is
shared by all three resource kinds.
"""

from __future__ import annotations

from pathlib import Path

from pipy_harness.native._resource_files import PIPY_CONFIG_HOME_ENV
from pipy_harness.native.skills import discover_workspace_skills


def _discover(
    workspace: Path,
    *,
    env: dict[str, str] | None = None,
):
    return discover_workspace_skills(
        workspace,
        config_home_env=env if env is not None else {},
        home_dir=workspace,
    )


def _skills_dir(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    skills_dir = workspace / ".pipy" / "skills"
    skills_dir.mkdir(parents=True)
    return skills_dir


def _write(directory: Path, filename: str, body: str = "real body\n") -> None:
    (directory / filename).write_text(body, encoding="utf-8")


def test_secret_shaped_filename_is_skipped(tmp_path: Path) -> None:
    skills_dir = _skills_dir(tmp_path)
    _write(skills_dir, "api_key.md", "leak me\n")
    _write(skills_dir, "secret-notes.md", "leak me too\n")
    _write(skills_dir, "lint.md", "real body\n")

    skills, _ = _discover(skills_dir.parent.parent)

    names = {skill.name for skill in skills}
    assert names == {"lint"}
    assert all("leak me" not in skill.body for skill in skills)


def test_binary_content_is_skipped(tmp_path: Path) -> None:
    skills_dir = _skills_dir(tmp_path)
    (skills_dir / "binary.md").write_bytes(b"text\x00\x01binary payload\n")
    _write(skills_dir, "lint.md", "real body\n")

    skills, _ = _discover(skills_dir.parent.parent)

    names = {skill.name for skill in skills}
    assert names == {"lint"}


def test_gitignored_filename_is_skipped(tmp_path: Path) -> None:
    skills_dir = _skills_dir(tmp_path)
    workspace = skills_dir.parent.parent
    (workspace / ".gitignore").write_text("ignored.md\n", encoding="utf-8")
    _write(skills_dir, "ignored.md", "should not load\n")
    _write(skills_dir, "kept.md", "real body\n")

    skills, _ = _discover(workspace)

    names = {skill.name for skill in skills}
    assert names == {"kept"}


def test_pipy_parent_dir_does_not_block_loading(tmp_path: Path) -> None:
    # `.pipy` is in read_only_tool._GENERATED_PARTS, but the resource
    # stores live under it by design; the safety screen runs on the bare
    # filename so a normal skill under `.pipy/skills` still loads.
    skills_dir = _skills_dir(tmp_path)
    _write(skills_dir, "lint.md", "real body\n")

    skills, _ = _discover(skills_dir.parent.parent)

    assert [skill.name for skill in skills] == ["lint"]


def test_secret_shaped_global_filename_is_skipped(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    pipy_home = tmp_path / "pipy-home"
    global_skills = pipy_home / "skills"
    global_skills.mkdir(parents=True)
    _write(global_skills, "token.md", "leak\n")
    _write(global_skills, "explain.md", "real body\n")

    skills, _ = discover_workspace_skills(
        workspace,
        config_home_env={PIPY_CONFIG_HOME_ENV: str(pipy_home)},
        home_dir=tmp_path,
    )

    names = {skill.name for skill in skills}
    assert names == {"explain"}


def test_control_character_filename_without_frontmatter_is_skipped(tmp_path: Path) -> None:
    skills_dir = _skills_dir(tmp_path)
    # No frontmatter, so the name would otherwise fall back to the raw stem.
    (skills_dir / "\x1b.md").write_text("just a body\n", encoding="utf-8")
    (skills_dir / "lint.md").write_text("real body\n", encoding="utf-8")

    skills, _ = _discover(skills_dir.parent.parent)

    names = {skill.name for skill in skills}
    assert names == {"lint"}
    # No label (name or path_label) carries a control byte.
    assert all("\x1b" not in skill.name for skill in skills)
    assert all("\x1b" not in skill.path_label for skill in skills)


def test_control_character_command_filename_is_not_advertised(tmp_path: Path) -> None:
    from pipy_harness.native.resources import (
        WorkspaceResources,
        dispatch_resource_command,
    )

    workspace = tmp_path / "ws"
    commands = workspace / ".pipy" / "commands"
    commands.mkdir(parents=True)
    (commands / "\x1b.md").write_text("body\n", encoding="utf-8")

    resources = WorkspaceResources.discover(
        workspace, config_home_env={}, home_dir=workspace
    )
    assert resources.commands == ()
    assert resources.custom_command_slash_names() == ()
    listing = dispatch_resource_command("/skill", resources)
    assert listing is not None and "\x1b" not in listing.message
