"""Workspace custom slash-command discovery for the native pipy runtime.

A `custom slash command` (sometimes called a `user command`) is a
Markdown file under `<workspace>/.pipy/commands/` or
`<global-root>/commands/` with optional YAML frontmatter declaring
`name` and `description`. The body of the file is the bounded message
text the dispatcher expands (with `$ARGUMENTS` / `$1..$9`
substitution) and sends as the user message when the user invokes
`/<name>` at the REPL prompt.

This module is a pure, dependency-free pipy-owned helper. It mirrors
the discovery, byte-cap, safety, and symlink-defense conventions
pinned by `pipy_harness.native.workspace_context` and reuses the
shared resource loader in `pipy_harness.native._resource_files`. No
body content is intended to reach the session JSONL, the Markdown
summary, or the opt-in `--archive-transcript` sidecar; use
`safe_custom_command_metadata` to project a dataclass to archive-safe
metadata.

Public API:

- `CustomSlashCommand` value object.
- `discover_workspace_custom_commands(workspace_root, ...)` returns
  `(commands, total_byte_cap_reached)`.
- `find_custom_command_by_name(commands, name)` returns the first
  case-sensitive match or `None`.
- `compose_custom_commands_help_block(commands)` formats a name +
  description help section suitable for `/help` output. Bodies do not
  appear in this block.
- `safe_custom_command_metadata(command)` returns the archive-safe
  per-file metadata projection for one command.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native._resource_files import (
    DEFAULT_PER_FILE_BYTE_CAP,
    DEFAULT_TOTAL_BYTE_CAP,
    discover_resource_files,
)

CUSTOM_COMMANDS_WORKSPACE_SUBDIR: str = "commands"
CUSTOM_COMMANDS_GLOBAL_SUBDIR: str = "commands"

CUSTOM_COMMANDS_HELP_BLOCK_HEADER: str = "Custom slash commands:\n"
CUSTOM_COMMANDS_HELP_BLOCK_LINE_TEMPLATE: str = "/{name}: {description}\n"
CUSTOM_COMMANDS_HELP_BLOCK_LINE_NO_DESCRIPTION_TEMPLATE: str = "/{name}\n"


@dataclass(frozen=True, slots=True)
class CustomSlashCommand:
    """One discovered custom slash-command Markdown file.

    `path_label` is workspace-relative POSIX for files inside the
    workspace (for example, `.pipy/commands/review.md`) and
    `<global>/commands/<filename>.md` for files under the global root.
    `name` is the leading-slash command name **without** the slash; it
    comes from the optional YAML frontmatter `name` key, with the file
    stem as the fallback. `description` comes from the optional
    frontmatter `description` key. `body` is the post-frontmatter
    Markdown text the dispatcher expands and sends as the user message
    when the command is invoked; it may be empty for a
    frontmatter-only file. `sha256` and `byte_length` always describe
    the file as it exists on disk; `truncated=True` means the body in
    this object only contains the first per-file-cap bytes plus a
    deterministic marker.
    """

    path_label: str
    name: str
    description: str
    body: str
    sha256: str
    byte_length: int
    truncated: bool


def discover_workspace_custom_commands(
    workspace_root: Path,
    *,
    config_home_env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
    per_file_byte_cap: int = DEFAULT_PER_FILE_BYTE_CAP,
    total_byte_cap: int = DEFAULT_TOTAL_BYTE_CAP,
) -> tuple[list[CustomSlashCommand], bool]:
    """Discover custom slash-command files in the workspace and global root.

    The workspace dir is `<workspace>/.pipy/commands/`. The global dir
    is resolved through `PIPY_CONFIG_HOME` then
    `${XDG_CONFIG_HOME}/pipy` then `~/.config/pipy`, and the
    `commands` subdir is appended. Files are deduplicated by canonical
    path. Missing dirs and files never raise. Resource directories must not
    be symlinks, and resource-file symlinks must stay inside the concrete
    `commands` directory they were found in.

    Returns `(commands, total_byte_cap_reached)`. Commands are listed
    workspace-first, then global, in sorted-name order within each
    source.
    """

    raw_files, cap_reached = discover_resource_files(
        workspace_root=workspace_root,
        workspace_subdir=CUSTOM_COMMANDS_WORKSPACE_SUBDIR,
        global_subdir=CUSTOM_COMMANDS_GLOBAL_SUBDIR,
        config_home_env=config_home_env,
        home_dir=home_dir,
        per_file_byte_cap=per_file_byte_cap,
        total_byte_cap=total_byte_cap,
    )
    commands = [
        CustomSlashCommand(
            path_label=raw.path_label,
            name=raw.name,
            description=raw.description,
            body=raw.body,
            sha256=raw.sha256,
            byte_length=raw.byte_length,
            truncated=raw.truncated,
        )
        for raw in raw_files
    ]
    return commands, cap_reached


def find_custom_command_by_name(
    commands: Sequence[CustomSlashCommand],
    name: str,
) -> CustomSlashCommand | None:
    """Return the first command whose `name` matches `name`.

    The match is case-sensitive. Names come from the parsed
    frontmatter, with the file stem as a fallback when the frontmatter
    omits `name`. Returns `None` when no command matches.
    """

    for command in commands:
        if command.name == name:
            return command
    return None


def compose_custom_commands_help_block(
    commands: Sequence[CustomSlashCommand],
) -> str:
    """Compose a help-text section that lists `/<name>: <description>`.

    Only the name and description appear in the block. Bodies are sent
    by the slash-command dispatcher when the user invokes the command
    and never leak into help output. When `commands` is empty the
    function returns an empty string so the caller can safely
    concatenate it onto the base help text.
    """

    if not commands:
        return ""
    parts: list[str] = [CUSTOM_COMMANDS_HELP_BLOCK_HEADER]
    for command in commands:
        if command.description:
            parts.append(
                CUSTOM_COMMANDS_HELP_BLOCK_LINE_TEMPLATE.format(
                    name=command.name,
                    description=command.description,
                )
            )
        else:
            parts.append(
                CUSTOM_COMMANDS_HELP_BLOCK_LINE_NO_DESCRIPTION_TEMPLATE.format(
                    name=command.name,
                )
            )
    return "".join(parts)


def safe_custom_command_metadata(
    command: CustomSlashCommand,
) -> dict[str, object]:
    """Return the archive-safe metadata projection for one command.

    The returned dict contains only `path_label`, `name`, `sha256`,
    `byte_length`, and `truncated`. The `description` and `body` are
    intentionally excluded so the archive never receives the command's
    instruction text. The `name` is included so the audit trail can
    record which slash command was invoked without leaking what the
    command actually expanded to.
    """

    return {
        "path_label": command.path_label,
        "name": command.name,
        "sha256": command.sha256,
        "byte_length": command.byte_length,
        "truncated": command.truncated,
    }
