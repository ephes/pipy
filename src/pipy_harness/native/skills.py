"""Workspace skill discovery for the native pipy runtime.

A `skill` is a Markdown file under `<workspace>/.pipy/skills/` or
`<global-root>/skills/` with optional YAML frontmatter declaring
`name` and `description`. The body is the skill instruction text
that the integrator will inject lazily when the user loads the skill
through a slash command (for example, `/skill <name>`).

This module is a pure, dependency-free pipy-owned helper. It mirrors
the discovery, byte-cap, and symlink-defense conventions pinned by
`pipy_harness.native.workspace_context`. No body content is intended
to reach the session JSONL, the Markdown summary, or the opt-in
`--archive-transcript` sidecar; use `safe_skill_metadata` to project
the dataclass to archive-safe metadata.

Public API:

- `SkillFile` value object.
- `discover_workspace_skills(workspace_root, ...)` returns
  `(skills, total_byte_cap_reached)`.
- `compose_skills_system_block(skills)` formats a name/description
  section suitable for system-prompt injection. Bodies do not appear
  in this block.
- `safe_skill_metadata(skills)` returns the archive-safe per-file
  metadata projection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native._resource_files import (
    DEFAULT_PER_FILE_BYTE_CAP,
    DEFAULT_TOTAL_BYTE_CAP,
    discover_resource_files,
    safe_resource_metadata,
)

SKILLS_WORKSPACE_SUBDIR: str = "skills"
SKILLS_GLOBAL_SUBDIR: str = "skills"

SKILLS_SYSTEM_BLOCK_HEADER: str = (
    "Available skills (load with /skill <name>):\n"
)
SKILLS_SYSTEM_BLOCK_LINE_TEMPLATE: str = "- {name}: {description}\n"
SKILLS_SYSTEM_BLOCK_LINE_NO_DESCRIPTION_TEMPLATE: str = "- {name}\n"


@dataclass(frozen=True, slots=True)
class SkillFile:
    """One discovered skill Markdown file.

    `path_label` is workspace-relative POSIX for files inside the
    workspace (for example, `.pipy/skills/lint.md`) and
    `<global>/skills/<name>.md` for files under the global root.
    `name` and `description` come from the optional YAML frontmatter
    (keys `name`, `description`). `body` is the post-frontmatter
    Markdown that callers may inject lazily; it may be empty for a
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


def discover_workspace_skills(
    workspace_root: Path,
    *,
    config_home_env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
    per_file_byte_cap: int = DEFAULT_PER_FILE_BYTE_CAP,
    total_byte_cap: int = DEFAULT_TOTAL_BYTE_CAP,
) -> tuple[list[SkillFile], bool]:
    """Discover skill files in the workspace and global root.

    The workspace dir is `<workspace>/.pipy/skills/`. The global dir is
    resolved through `PIPY_CONFIG_HOME` then `${XDG_CONFIG_HOME}/pipy`
    then `~/.config/pipy`, and the `skills` subdir is appended. Files
    are deduplicated by canonical path. Missing dirs and files never
    raise. Symlinks must stay inside the workspace root for workspace skills
    and inside the global resource root for global skills.

    Returns `(skills, total_byte_cap_reached)`. Skills are listed
    workspace-first, then global, in sorted-name order within each
    source.
    """

    raw_files, cap_reached = discover_resource_files(
        workspace_root=workspace_root,
        workspace_subdir=SKILLS_WORKSPACE_SUBDIR,
        global_subdir=SKILLS_GLOBAL_SUBDIR,
        config_home_env=config_home_env,
        home_dir=home_dir,
        per_file_byte_cap=per_file_byte_cap,
        total_byte_cap=total_byte_cap,
    )
    skills = [
        SkillFile(
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
    return skills, cap_reached


def compose_skills_system_block(skills: Sequence[SkillFile]) -> str:
    """Compose the system-prompt section that advertises skills.

    Only the name and description appear in the block. Bodies are
    loaded lazily by the integrator when the user invokes a
    `/skill <name>` command. When `skills` is empty the function
    returns an empty string so the caller can safely concatenate it
    onto the base prompt.
    """

    if not skills:
        return ""
    parts: list[str] = [SKILLS_SYSTEM_BLOCK_HEADER]
    for skill in skills:
        if skill.description:
            parts.append(
                SKILLS_SYSTEM_BLOCK_LINE_TEMPLATE.format(
                    name=skill.name,
                    description=skill.description,
                )
            )
        else:
            parts.append(
                SKILLS_SYSTEM_BLOCK_LINE_NO_DESCRIPTION_TEMPLATE.format(name=skill.name)
            )
    return "".join(parts)


def safe_skill_metadata(skills: Sequence[SkillFile]) -> list[dict[str, object]]:
    """Return the archive-safe per-file metadata for `skills`.

    The returned dicts contain only `path_label`, `sha256`,
    `byte_length`, and `truncated`. Names, descriptions, and bodies
    are excluded so the archive never receives skill text.
    """

    return safe_resource_metadata(skills)
