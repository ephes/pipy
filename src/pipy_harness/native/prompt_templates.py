"""Workspace prompt-template discovery for the native pipy runtime.

A `prompt template` is a Markdown file under
`<workspace>/.pipy/templates/` or `<global-root>/templates/` with
optional YAML frontmatter declaring `name` and `description`. The
body is the template content the runtime expands (with `$ARGUMENTS`
/ `$1..$9` substitution) and sends as a bounded provider-visible
message when the user invokes the template by its own `/<name> [args]`
slash command (Pi shape — there is no `/template` wrapper command).

This module is a pure, dependency-free pipy-owned helper. It mirrors
the discovery, byte-cap, safety, and symlink-defense conventions
pinned by `pipy_harness.native.workspace_context`. No body content is
intended to reach the session JSONL, the Markdown summary, or the
opt-in `--archive-transcript` sidecar; use
`safe_prompt_template_metadata` to project the dataclass to
archive-safe metadata.

Public API:

- `PromptTemplate` value object.
- `discover_workspace_prompt_templates(workspace_root, ...)` returns
  `(templates, total_byte_cap_reached)`.
- `find_template_by_name(templates, name)` returns the first
  case-sensitive match or `None`.
- `expand_template_body(body, arguments)` returns the expanded prompt
  text (shared with custom slash commands).
- `safe_prompt_template_metadata(templates)` returns the archive-safe
  per-file metadata projection.
"""

from __future__ import annotations

import re
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

PROMPT_TEMPLATES_WORKSPACE_SUBDIR: str = "templates"
PROMPT_TEMPLATES_GLOBAL_SUBDIR: str = "templates"

# Recognized substitution tokens: ``$ARGUMENTS`` / ``${ARGUMENTS}`` for the
# whole argument string, and ``$1``..``$9`` / ``${1}``..``${9}`` for
# whitespace-split positional arguments.
_PLACEHOLDER_RE = re.compile(r"\$(?:\{(ARGUMENTS|[1-9])\}|(ARGUMENTS|[1-9]))")


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """One discovered prompt-template Markdown file.

    `path_label` is workspace-relative POSIX for files inside the
    workspace (for example, `.pipy/templates/review.md`) and
    `<global>/templates/<name>.md` for files under the global root.
    `name` and `description` come from the optional YAML frontmatter
    (keys `name`, `description`). `body` is the post-frontmatter
    Markdown the runtime expands on `/<name>`; it may be
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
    package_roots: "Sequence[PackageRoot]" = (),
    explicit_paths: Sequence[Path] = (),
    include_defaults: bool = True,
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
        package_roots=package_roots,
        explicit_paths=explicit_paths,
        include_defaults=include_defaults,
        dedupe_by_name=True,
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


def expand_template_body(body: str, arguments: str) -> str:
    """Expand `body` with the user-supplied `arguments` string.

    Substitution rules:

    - ``$ARGUMENTS`` / ``${ARGUMENTS}`` expand to the full (stripped)
      `arguments` string.
    - ``$1``..``$9`` / ``${1}``..``${9}`` expand to whitespace-split
      positional arguments; an out-of-range index expands to the empty
      string.
    - When the body contains **no** recognized placeholder and
      `arguments` is non-empty, the arguments are appended as inserted
      prompt text after a blank line so a placeholder-free template can
      still take a free-form argument. A body that already references
      a placeholder controls its own argument placement and is not
      appended to.

    The expansion is purely textual; it never executes the body and
    never resolves shell metacharacters.
    """

    stripped_arguments = arguments.strip()
    positional = stripped_arguments.split()

    def _replace(match: "re.Match[str]") -> str:
        token = match.group(1) or match.group(2)
        if token == "ARGUMENTS":
            return stripped_arguments
        index = int(token)
        if 1 <= index <= len(positional):
            return positional[index - 1]
        return ""

    expanded, substitutions = _PLACEHOLDER_RE.subn(_replace, body)
    if substitutions == 0 and stripped_arguments:
        if expanded.strip():
            base = expanded.rstrip("\n")
            return f"{base}\n\n{stripped_arguments}"
        return stripped_arguments
    return expanded


def safe_prompt_template_metadata(
    templates: Sequence[PromptTemplate],
) -> list[dict[str, object]]:
    """Return the archive-safe per-file metadata for `templates`.

    The returned dicts contain only `path_label`, `sha256`,
    `byte_length`, and `truncated`. Names, descriptions, and bodies
    are excluded so the archive never receives template text.
    """

    return safe_resource_metadata(templates)
