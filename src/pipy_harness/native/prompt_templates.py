"""Workspace prompt-template discovery for the native pipy runtime.

A `prompt template` is a Markdown file under
`<workspace>/.pipy/templates/` or `<global-root>/templates/` with
optional YAML frontmatter declaring `name` and `description`. The
body is the template content the integrator will inject when the
user invokes a `/template <name>` slash command.

This module is a pure, dependency-free pipy-owned helper. It mirrors
the discovery, byte-cap, and symlink-defense conventions pinned by
`pipy_harness.native.workspace_context`. No body content is intended
to reach the session JSONL, the Markdown summary, or the opt-in
`--archive-transcript` sidecar; use `safe_prompt_template_metadata`
to project the dataclass to archive-safe metadata.

Public API:

- `PromptTemplate` value object.
- `discover_workspace_prompt_templates(workspace_root, ...)` returns
  `(templates, total_byte_cap_reached)`.
- `find_template_by_name(templates, name)` returns the first
  case-sensitive match or `None`.
- `safe_prompt_template_metadata(templates)` returns the archive-safe
  per-file metadata projection.
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

PROMPT_TEMPLATES_WORKSPACE_SUBDIR: str = "templates"
PROMPT_TEMPLATES_GLOBAL_SUBDIR: str = "templates"


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """One discovered prompt-template Markdown file.

    `path_label` is workspace-relative POSIX for files inside the
    workspace (for example, `.pipy/templates/review.md`) and
    `<global>/templates/<name>.md` for files under the global root.
    `name` and `description` come from the optional YAML frontmatter
    (keys `name`, `description`). `body` is the post-frontmatter
    Markdown the integrator injects on `/template <name>`; it may be
    empty for a frontmatter-only file. `sha256` and `byte_length`
    always describe the file as it exists on disk; `truncated=True`
    means the body in this object only contains the first per-file-cap
    bytes plus a deterministic marker.
    """

    path_label: str
    name: str
    description: str
    body: str
    sha256: str
    byte_length: int
    truncated: bool


def discover_workspace_prompt_templates(
    workspace_root: Path,
    *,
    config_home_env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
    per_file_byte_cap: int = DEFAULT_PER_FILE_BYTE_CAP,
    total_byte_cap: int = DEFAULT_TOTAL_BYTE_CAP,
) -> tuple[list[PromptTemplate], bool]:
    """Discover prompt-template files in the workspace and global root.

    The workspace dir is `<workspace>/.pipy/templates/`. The global
    dir is resolved through `PIPY_CONFIG_HOME` then
    `${XDG_CONFIG_HOME}/pipy` then `~/.config/pipy`, and the
    `templates` subdir is appended. Files are deduplicated by
    canonical path. Missing dirs and files never raise. Resource
    directories must not be symlinks, and resource-file symlinks must
    stay inside the concrete `templates` directory they were found in.

    Returns `(templates, total_byte_cap_reached)`. Templates are
    listed workspace-first, then global, in sorted-name order within
    each source.
    """

    raw_files, cap_reached = discover_resource_files(
        workspace_root=workspace_root,
        workspace_subdir=PROMPT_TEMPLATES_WORKSPACE_SUBDIR,
        global_subdir=PROMPT_TEMPLATES_GLOBAL_SUBDIR,
        config_home_env=config_home_env,
        home_dir=home_dir,
        per_file_byte_cap=per_file_byte_cap,
        total_byte_cap=total_byte_cap,
    )
    templates = [
        PromptTemplate(
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
    return templates, cap_reached


def find_template_by_name(
    templates: Sequence[PromptTemplate],
    name: str,
) -> PromptTemplate | None:
    """Return the first template whose `name` matches `name`.

    The match is case-sensitive. Names come from the parsed
    frontmatter, with the file stem as a fallback when the
    frontmatter omits `name`. Returns `None` when no template
    matches.
    """

    for template in templates:
        if template.name == name:
            return template
    return None


def safe_prompt_template_metadata(
    templates: Sequence[PromptTemplate],
) -> list[dict[str, object]]:
    """Return the archive-safe per-file metadata for `templates`.

    The returned dicts contain only `path_label`, `sha256`,
    `byte_length`, and `truncated`. Names, descriptions, and bodies
    are excluded so the archive never receives template text.
    """

    return safe_resource_metadata(templates)
