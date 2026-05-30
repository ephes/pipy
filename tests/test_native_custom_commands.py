"""Focused tests for the custom slash-command discovery loader.

These tests pin the discovery rules listed in
`pipy_harness.native.custom_commands`. They never wire the loader into
a REPL dispatcher or the session archive; the integrator wires
`find_custom_command_by_name` into the slash-command dispatcher and
`compose_custom_commands_help_block` into `/help` separately.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pipy_harness.native._resource_files import PIPY_CONFIG_HOME_ENV
from pipy_harness.native.custom_commands import (
    CustomSlashCommand,
    compose_custom_commands_help_block,
    discover_workspace_custom_commands,
    find_custom_command_by_name,
    safe_custom_command_metadata,
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
) -> tuple[list[CustomSlashCommand], bool]:
    return discover_workspace_custom_commands(
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


def _write_command(
    directory: Path,
    *,
    filename: str,
    name: str | None = None,
    description: str | None = None,
    body: str = "command body\n",
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


def test_no_commands_dir_returns_empty_list(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    commands, cap_reached = _discover(workspace)
    assert commands == []
    assert cap_reached is False


def test_discovers_workspace_commands(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    commands_dir = workspace / ".pipy" / "commands"
    _write_command(
        commands_dir,
        filename="review.md",
        name="review",
        description="Review the diff",
        body="please review the staged diff\n",
    )
    _write_command(
        commands_dir,
        filename="plan.md",
        name="plan",
        description="Plan the next step",
        body="propose a plan\n",
    )

    commands, cap_reached = _discover(workspace)

    assert cap_reached is False
    names = {command.name for command in commands}
    assert names == {"review", "plan"}
    review = next(command for command in commands if command.name == "review")
    assert review.description == "Review the diff"
    assert "please review the staged diff" in review.body
    assert review.path_label == ".pipy/commands/review.md"
    assert review.truncated is False


def test_discovers_global_commands_via_pipy_config_home(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    pipy_home = tmp_path / "pipy-home"
    global_commands_dir = pipy_home / "commands"
    _write_command(
        global_commands_dir,
        filename="explain.md",
        name="explain",
        description="Explain code",
        body="explain the highlighted snippet\n",
    )

    env = {PIPY_CONFIG_HOME_ENV: str(pipy_home)}
    commands, cap_reached = _discover(workspace, env=env)

    assert cap_reached is False
    assert len(commands) == 1
    only = commands[0]
    assert only.name == "explain"
    assert only.path_label == "<global>/commands/explain.md"
    assert "explain the highlighted snippet" in only.body


def test_dedupes_by_canonical_path(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    commands_dir = workspace / ".pipy" / "commands"
    shared = _write_command(
        commands_dir,
        filename="shared.md",
        name="shared",
        description="shared",
    )
    (commands_dir / "alias.md").symlink_to(shared)

    commands, _ = _discover(workspace)

    assert len(commands) == 1
    assert commands[0].name == "shared"


def test_refuses_symlinked_workspace_commands_dir_outside_workspace(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    outside_dir = tmp_path / "outside-commands"
    _write_command(
        outside_dir,
        filename="leak.md",
        name="leak",
        description="outside",
        body="outside body must not load\n",
    )
    pipy_dir = workspace / ".pipy"
    pipy_dir.mkdir()
    (pipy_dir / "commands").symlink_to(outside_dir, target_is_directory=True)

    commands, _ = _discover(workspace)

    assert commands == []


def test_refuses_symlink_outside_workspace(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    secret = outside_dir / "secret.md"
    secret.write_text("never load me\n", encoding="utf-8")

    commands_dir = workspace / ".pipy" / "commands"
    commands_dir.mkdir(parents=True)
    (commands_dir / "leak.md").symlink_to(secret)

    _write_command(
        commands_dir,
        filename="legitimate.md",
        name="legitimate",
        description="real",
        body="real body\n",
    )

    commands, _ = _discover(workspace)

    assert all(command.name != "leak" for command in commands)
    assert all("never load me" not in command.body for command in commands)
    assert any(command.name == "legitimate" for command in commands)


def test_per_file_byte_cap_truncates_with_marker(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    commands_dir = workspace / ".pipy" / "commands"
    payload = "x" * 4096
    _write_command(
        commands_dir,
        filename="big.md",
        name="big",
        description="big",
        body=payload,
    )

    commands, cap_reached = _discover(workspace, per_file_byte_cap=1024)
    assert cap_reached is False
    big = next(command for command in commands if command.name == "big")
    assert big.truncated is True
    on_disk = (commands_dir / "big.md").read_bytes()
    assert big.byte_length == len(on_disk)
    assert big.sha256 == hashlib.sha256(on_disk).hexdigest()
    assert "[pipy: resource file truncated at 1024 bytes]" in big.body


def test_total_byte_cap_reached_flag_set(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    commands_dir = workspace / ".pipy" / "commands"
    payload = "a" * 800
    for index, letter in enumerate("abc"):
        _write_command(
            commands_dir,
            filename=f"{letter}.md",
            name=letter,
            description=f"file {index}",
            body=payload,
        )

    commands, cap_reached = _discover(
        workspace,
        per_file_byte_cap=4096,
        total_byte_cap=900,
    )

    assert cap_reached is True
    # Only one file fits under the 900-byte total cap.
    assert len(commands) == 1


def test_frontmatter_name_overrides_filename_stem(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    commands_dir = workspace / ".pipy" / "commands"
    # The filename stem is `plain-stem` but the frontmatter overrides
    # the command name to `fancy`.
    _write_command(
        commands_dir,
        filename="plain-stem.md",
        name="fancy",
        description="renamed via frontmatter",
        body="hello\n",
    )
    # A file without frontmatter falls back to the filename stem.
    commands_dir.mkdir(parents=True, exist_ok=True)
    bare = commands_dir / "no-frontmatter.md"
    bare.write_text("just a body\n", encoding="utf-8")

    commands, _ = _discover(workspace)
    by_name = {command.name: command for command in commands}

    assert "fancy" in by_name
    assert "plain-stem" not in by_name
    fancy = by_name["fancy"]
    assert fancy.description == "renamed via frontmatter"
    assert "hello" in fancy.body
    assert "---" not in fancy.body

    assert "no-frontmatter" in by_name
    bare_cmd = by_name["no-frontmatter"]
    assert bare_cmd.description == ""
    assert "just a body" in bare_cmd.body


def test_find_by_name_case_sensitive(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    commands_dir = workspace / ".pipy" / "commands"
    _write_command(
        commands_dir,
        filename="review.md",
        name="Review",
        description="capitalized",
        body="body\n",
    )

    commands, _ = _discover(workspace)
    assert find_custom_command_by_name(commands, "Review") is not None
    assert find_custom_command_by_name(commands, "review") is None
    assert find_custom_command_by_name(commands, "missing") is None


def test_compose_help_block_contains_name_and_description(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    commands_dir = workspace / ".pipy" / "commands"
    _write_command(
        commands_dir,
        filename="review.md",
        name="review",
        description="Review the diff",
        body="real body that must not appear in the help block\n",
    )
    _write_command(
        commands_dir,
        filename="bare.md",
        name="bare",
        description="",
        body="another body that must not appear\n",
    )

    commands, _ = _discover(workspace)
    block = compose_custom_commands_help_block(commands)

    assert "Custom slash commands" in block
    assert "/review: Review the diff" in block
    # Bare command (empty description) still appears as a single name line.
    assert "/bare" in block
    # Bodies must NEVER leak into the help block.
    assert "real body" not in block
    assert "another body" not in block

    # Empty input returns the empty string so callers can concat safely.
    assert compose_custom_commands_help_block([]) == ""


def test_safe_metadata_excludes_body(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    commands_dir = workspace / ".pipy" / "commands"
    _write_command(
        commands_dir,
        filename="review.md",
        name="review",
        description="Review the diff",
        body="sensitive instructions that must never reach the archive\n",
    )

    commands, _ = _discover(workspace)
    assert len(commands) == 1
    safe = safe_custom_command_metadata(commands[0])

    assert set(safe.keys()) == {
        "path_label",
        "name",
        "sha256",
        "byte_length",
        "truncated",
    }
    assert safe["path_label"] == ".pipy/commands/review.md"
    assert safe["name"] == "review"
    assert "sensitive instructions" not in str(safe)
    # No description or body leak.
    assert "description" not in safe
    assert "body" not in safe
    assert "Review the diff" not in str(safe)
