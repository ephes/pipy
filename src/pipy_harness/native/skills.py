"""Workspace skill discovery for the native pipy runtime.

A `skill` is a Markdown file with YAML frontmatter declaring `name` and a
required `description` (Pi `loadSkillFromFile` drops a skill without one).
Discovery mirrors Pi (`package-manager.ts` auto skill roots and
`collectSkillEntries`, reference `4df157433`), in first-wins order:

1. per-run `--skill` paths;
2. `<workspace>/.pipy/skills/` (Pi `.pi/skills`), trusted projects only;
3. `.agents/skills/` in the workspace and each ancestor up to the git
   repository root, trusted projects only, excluding `~/.agents/skills`;
4. `<global-root>/skills/` (Pi `~/.pi/agent/skills`);
5. `~/.agents/skills/`;
6. installed package skill directories.

Every root uses Pi's layout: a directory holding `SKILL.md` is one skill
(named after the directory unless the frontmatter names it) and is not
descended further; `.`-prefixed entries and `node_modules` are skipped;
plain `*.md` files count at the root of `.pipy/skills`/global/CLI/package
roots and below the root of `.agents/skills` roots; `.gitignore`,
`.ignore` and `.fdignore` rules apply. The body is the skill instruction
text that the runtime injects as a bounded provider-visible message when
the user loads the skill through the `/skill <name>` slash command.

Symlinks are followed in every skill root, like Pi (`collectSkillEntries`
stats through a symlinked file or directory); the project roots only load for
trusted projects. This module is a pure, dependency-free pipy-owned helper
that shares the byte-cap and filename-safety conventions of
`pipy_harness.native.workspace_context`. No body content is
intended to reach the session JSONL or the Markdown summary; use
`safe_skill_metadata` to project the dataclass to archive-safe metadata.

Public API:

- `SkillFile` value object.
- `discover_workspace_skills(workspace_root, ...)` returns
  `(skills, total_byte_cap_reached)`.
- `find_skill_by_name(skills, name)` returns the first case-sensitive
  match or `None`.
- `compose_skills_system_block(skills)` formats the Pi-shaped
  `<available_skills>` section (name/description/location) suitable for
  system-prompt injection. Bodies do not appear in this block; the model
  loads them on demand with the read tool.
- `safe_skill_metadata(skills)` returns the archive-safe per-file
  metadata projection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipy_harness.native.package_resources import PackageRoot

from pipy_harness.native._resource_files import (
    DEFAULT_PER_FILE_BYTE_CAP,
    DEFAULT_TOTAL_BYTE_CAP,
    discover_resource_files,
    safe_resource_metadata,
)

SKILLS_WORKSPACE_SUBDIR: str = "skills"
SKILLS_GLOBAL_SUBDIR: str = "skills"

# Header lines for the Pi-shaped skill-advertisement block. Mirrors
# `formatSkillsForPrompt` in pi-mono `packages/coding-agent/src/core/skills.ts`
# so the model loads a skill's body on demand with the read tool.
SKILLS_SYSTEM_BLOCK_HEADER_LINES: tuple[str, ...] = (
    "The following skills provide specialized instructions for specific tasks.",
    "Use the read tool to load a skill's file when the task matches its description.",
    "When a skill file references a relative path, resolve it against the "
    "skill directory (parent of SKILL.md / dirname of the path) and use "
    "that absolute path in tool commands.",
)
# Pi wraps the block in its `skills` system-prompt section tag.
SKILLS_SECTION_TAG: str = "skills"


@dataclass(frozen=True, slots=True)
class SkillFile:
    """One discovered skill Markdown file.

    `path_label` is workspace-relative POSIX for files inside the
    workspace (for example, `.pipy/skills/lint.md`) and
    `<global>/skills/<name>.md` for files under the global root.
    `name` and `description` come from the optional YAML frontmatter
    (keys `name`, `description`). `body` is the post-frontmatter
    Markdown that the runtime injects when the user loads the skill; it
    may be empty for a frontmatter-only file. `sha256` and
    `byte_length` always describe the file as it exists on disk;
    `truncated=True` means the body in this object only contains the
    first per-file-cap bytes plus a deterministic marker.

    `absolute_path` is the resolved on-disk path of the skill file. It is
    used for the system-prompt `<location>` so the model can load the
    skill body with the read tool, including for global skills outside
    the workspace. It must never enter `safe_skill_metadata` or any other
    archive/JSONL/Markdown surface.
    """

    path_label: str
    name: str
    description: str
    body: str
    sha256: str
    byte_length: int
    truncated: bool
    absolute_path: Path


def discover_workspace_skills(
    workspace_root: Path,
    *,
    config_home_env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
    per_file_byte_cap: int = DEFAULT_PER_FILE_BYTE_CAP,
    total_byte_cap: int = DEFAULT_TOTAL_BYTE_CAP,
    package_roots: "Sequence[PackageRoot]" = (),
    explicit_paths: Sequence[Path] = (),
    include_defaults: bool = True,
    include_workspace_defaults: bool = False,
    include_global_defaults: bool = True,
    include_package_defaults: bool = True,
) -> tuple[list[SkillFile], bool]:
    """Discover skill files in the workspace and global root.

    Workspace-local discovery is fail-closed by default. Product callers must
    opt in only after resolving project trust; it covers both
    `<workspace>/.pipy/skills/` and the project `.agents/skills/` roots. The
    global dir is resolved through `PIPY_CONFIG_HOME` then
    `${XDG_CONFIG_HOME}/pipy` then `~/.pipy` (when present) then
    `~/.config/pipy`, and the `skills` subdir is appended; `~/.agents/skills/`
    (from `home_dir`) follows it. Files are deduplicated by canonical path and
    skills by name (first wins). Missing dirs and files never raise. Symlinked
    roots, skill directories and skill files are followed wherever they
    point, like Pi; a symlink cycle is walked once.

    Returns `(skills, total_byte_cap_reached)` in the source order listed in
    the module docstring, sorted by path within each source.
    """

    raw_files, cap_reached = discover_resource_files(
        workspace_root=workspace_root,
        workspace_subdir=SKILLS_WORKSPACE_SUBDIR,
        global_subdir=SKILLS_GLOBAL_SUBDIR,
        config_home_env=config_home_env,
        home_dir=home_dir,
        per_file_byte_cap=per_file_byte_cap,
        total_byte_cap=total_byte_cap,
        package_roots=package_roots,
        explicit_paths=explicit_paths,
        include_defaults=include_defaults,
        include_workspace_defaults=include_workspace_defaults,
        include_global_defaults=include_global_defaults,
        include_package_defaults=include_package_defaults,
        dedupe_by_name=True,
        skill_discovery=True,
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
            absolute_path=raw.absolute_path,
        )
        for raw in raw_files
    ]
    return skills, cap_reached


def find_skill_by_name(
    skills: Sequence[SkillFile],
    name: str,
) -> SkillFile | None:
    """Return the first skill whose `name` matches `name`.

    The match is case-sensitive. Names come from the parsed
    frontmatter, with the file stem as a fallback when the frontmatter
    omits `name`. Returns `None` when no skill matches.
    """

    for skill in skills:
        if skill.name == name:
            return skill
    return None


def _escape_xml(value: str) -> str:
    """XML-escape `value` for the skill-advertisement block.

    Mirrors `escapeXml` in pi-mono `skills.ts`: escapes `&`, `<`, `>`,
    `"`, and `'` (ampersand first so already-escaped entities are not
    double-escaped beyond the intended single pass).
    """

    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def compose_skills_system_block(skills: Sequence[SkillFile]) -> str:
    """Compose the system-prompt section that advertises skills.

    Mirrors Pi's `skills` system-prompt section (`formatSkillsForPrompt`
    wrapped in `<skills>...</skills>` and joined with a blank line): a short
    header instructing the model to load a skill's file with the read tool
    when the task matches its description, followed by an
    `<available_skills>` block carrying a per-skill `<name>`,
    `<description>`, and `<location>` (the skill file's absolute path), all
    XML-escaped. Bodies never appear in the block; the model loads them on
    demand with the read tool. When `skills` is empty the function returns
    an empty string so the caller can safely concatenate it onto the prompt.
    """

    body = skills_section_body(skills)
    if not body:
        return ""
    return f"\n\n<{SKILLS_SECTION_TAG}>\n{body}\n</{SKILLS_SECTION_TAG}>"


def skills_section_body(skills: Sequence[SkillFile]) -> str:
    """Pi ``formatSkillsForPrompt``: the ``skills`` section body, or ``""``."""

    if not skills:
        return ""
    lines: list[str] = list(SKILLS_SYSTEM_BLOCK_HEADER_LINES)
    lines.append("")
    lines.append("<available_skills>")
    for skill in skills:
        lines.append("  <skill>")
        lines.append(f"    <name>{_escape_xml(skill.name)}</name>")
        lines.append(f"    <description>{_escape_xml(skill.description)}</description>")
        lines.append(
            f"    <location>{_escape_xml(str(skill.absolute_path))}</location>"
        )
        lines.append("  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)


def safe_skill_metadata(skills: Sequence[SkillFile]) -> list[dict[str, object]]:
    """Return the archive-safe per-file metadata for `skills`.

    The returned dicts contain only `path_label`, `sha256`,
    `byte_length`, and `truncated`. Names, descriptions, and bodies
    are excluded so the archive never receives skill text.
    """

    return safe_resource_metadata(skills)
