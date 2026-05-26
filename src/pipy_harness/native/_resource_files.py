"""Shared discovery helpers for workspace `.pipy/skills` and `.pipy/templates`.

This module is an implementation detail of
`pipy_harness.native.skills` and
`pipy_harness.native.prompt_templates`. It owns the parts the two
loaders truly share: the global-root resolver, a tiny frontmatter
parser (no `yaml` import), the symlink-safe per-file reader with byte
cap and truncation marker, and a `*.md` directory glob that dedupes by
canonical path. Each public module wires these into its own dataclass
plus its own composition / lookup surface.

The helpers mirror the conventions pinned by
`pipy_harness.native.workspace_context`: stdlib only, no pydantic,
missing files never raise, symlinks must resolve inside the
containing directory, per-file reads are bounded with a deterministic
marker, and the global root resolves through `PIPY_CONFIG_HOME` then
`${XDG_CONFIG_HOME}/pipy` then `~/.config/pipy`.

No body content is intended to leave the in-process discovery layer.
Callers that want to record per-file metadata for the archive should
project the dataclass to `{path_label, sha256, byte_length,
truncated}` only.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

PIPY_CONFIG_HOME_ENV: str = "PIPY_CONFIG_HOME"
XDG_CONFIG_HOME_ENV: str = "XDG_CONFIG_HOME"
PIPY_CONFIG_DIR_NAME: str = "pipy"

WORKSPACE_PIPY_DIR_NAME: str = ".pipy"

GLOBAL_PATH_LABEL_PREFIX: str = "<global>/"

PER_FILE_TRUNCATION_MARKER_TEMPLATE: str = (
    "\n\n[pipy: resource file truncated at {cap} bytes]\n"
)

DEFAULT_PER_FILE_BYTE_CAP: int = 64 * 1024
DEFAULT_TOTAL_BYTE_CAP: int = 256 * 1024


@dataclass(frozen=True, slots=True)
class _RawResourceFile:
    """Internal representation of a discovered resource file.

    Public modules wrap this in their own typed dataclass so the
    public API names (`SkillFile`, `PromptTemplate`) stay obvious and
    stable.
    """

    path_label: str
    name: str
    description: str
    body: str
    sha256: str
    byte_length: int
    truncated: bool


def resolve_global_resource_root(
    *,
    env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> Path:
    """Return the global pipy resource root.

    Resolution order matches `workspace_context.resolve_global_instruction_root`:

    1. `PIPY_CONFIG_HOME` (taken verbatim, then `~` expanded).
    2. `${XDG_CONFIG_HOME}/pipy`.
    3. `~/.config/pipy`.
    """

    env_map: Mapping[str, str] = env if env is not None else os.environ
    explicit = env_map.get(PIPY_CONFIG_HOME_ENV)
    if explicit:
        return Path(explicit).expanduser()
    xdg = env_map.get(XDG_CONFIG_HOME_ENV)
    if xdg:
        return Path(xdg).expanduser() / PIPY_CONFIG_DIR_NAME
    home = (home_dir or Path.home()).expanduser()
    return home / ".config" / PIPY_CONFIG_DIR_NAME


def discover_resource_files(
    *,
    workspace_root: Path,
    workspace_subdir: str,
    global_subdir: str,
    config_home_env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
    per_file_byte_cap: int = DEFAULT_PER_FILE_BYTE_CAP,
    total_byte_cap: int = DEFAULT_TOTAL_BYTE_CAP,
) -> tuple[list[_RawResourceFile], bool]:
    """Discover Markdown files in a workspace and global resource directory.

    `workspace_subdir` is the path relative to `<workspace>/.pipy/` (for
    example, `skills` or `templates`). `global_subdir` is the path
    relative to the global resource root.

    Discovery rules:

    - Workspace dir is `<workspace>/.pipy/<workspace_subdir>`.
    - Global dir is `<global-root>/<global_subdir>`.
    - Both dirs are stat-globbed for `*.md` files one level deep; no
      recursion.
    - Workspace files come first in the returned list, then global
      files. Within each source the iteration order is sorted by file
      name so the result is deterministic.
    - Results are deduplicated by canonical (`Path.resolve()`) path.
      The first occurrence wins.
    - Each file is read at most `per_file_byte_cap` bytes; a longer
      file is truncated with a deterministic marker and
      `truncated=True`. `byte_length` and `sha256` always describe the
      on-disk file.
    - Symlinks must resolve inside their authority root: workspace files under
      the resolved workspace, and global files under the resolved global
      resource root. A symlink that escapes is skipped silently.
    - Total bytes loaded across all files is bounded by
      `total_byte_cap`. Once the next file would push the running
      total past the cap, the loader stops and returns
      `total_byte_cap_reached=True`. The partial-loaded file is not
      included.
    - Missing directories, missing files, and `OSError` on read are
      treated as "no resources here" and never raised.
    """

    if per_file_byte_cap < 1:
        raise ValueError(
            f"per_file_byte_cap must be >= 1; got {per_file_byte_cap}"
        )
    if total_byte_cap < 1:
        raise ValueError(
            f"total_byte_cap must be >= 1; got {total_byte_cap}"
        )

    resolved_workspace = workspace_root.expanduser().resolve()
    workspace_dir = resolved_workspace / WORKSPACE_PIPY_DIR_NAME / workspace_subdir

    global_root = resolve_global_resource_root(env=config_home_env, home_dir=home_dir)
    global_dir = global_root / global_subdir

    seen_paths: set[Path] = set()
    raw_files: list[_RawResourceFile] = []
    total_loaded = 0
    cap_reached = False

    sources: list[tuple[Path, str, Path]] = [
        (workspace_dir, "workspace", resolved_workspace),
        (global_dir, "global", global_root.expanduser().resolve()),
    ]

    for source_dir, source_kind, containment_root in sources:
        if cap_reached:
            break
        for candidate in _iter_md_files(source_dir):
            try:
                resolved_candidate = candidate.resolve()
            except OSError:
                continue
            try:
                resolved_candidate.relative_to(containment_root)
            except (OSError, ValueError):
                continue
            if resolved_candidate in seen_paths:
                continue
            try:
                raw = candidate.read_bytes()
            except OSError:
                continue
            byte_length = len(raw)
            if total_loaded + byte_length > total_byte_cap and raw_files:
                cap_reached = True
                break
            if total_loaded + byte_length > total_byte_cap:
                cap_reached = True
                break
            seen_paths.add(resolved_candidate)
            truncated = byte_length > per_file_byte_cap
            if truncated:
                head = raw[:per_file_byte_cap]
                content = head.decode("utf-8", errors="replace") + (
                    PER_FILE_TRUNCATION_MARKER_TEMPLATE.format(cap=per_file_byte_cap)
                )
            else:
                content = raw.decode("utf-8", errors="replace")
            sha256 = hashlib.sha256(raw).hexdigest()
            name, description, body = _parse_frontmatter(content, fallback_name=candidate.stem)
            path_label = _path_label_for(
                candidate=candidate,
                source_kind=source_kind,
                workspace=resolved_workspace,
            )
            raw_files.append(
                _RawResourceFile(
                    path_label=path_label,
                    name=name,
                    description=description,
                    body=body,
                    sha256=sha256,
                    byte_length=byte_length,
                    truncated=truncated,
                )
            )
            total_loaded += byte_length

    return raw_files, cap_reached


def _iter_md_files(directory: Path) -> list[Path]:
    """Return the `*.md` files directly under `directory`, sorted by name.

    Missing directories return an empty list. Errors stat'ing the
    directory are swallowed: a permission failure on the resource dir
    is equivalent to "no resources here".
    """

    try:
        if not directory.is_dir():
            return []
    except OSError:
        return []
    try:
        entries = sorted(directory.glob("*.md"), key=lambda p: p.name)
    except OSError:
        return []
    files: list[Path] = []
    for entry in entries:
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        files.append(entry)
    return files


def _path_label_for(
    *,
    candidate: Path,
    source_kind: str,
    workspace: Path,
) -> str:
    """Compute the workspace-relative or `<global>`-prefixed POSIX label."""

    if source_kind == "global":
        # Label as <global>/<subdir>/<filename>, joining the parent
        # dir name with the file name so the audit trail keeps the
        # category (`skills` vs `templates`).
        parent_name = candidate.parent.name
        return f"{GLOBAL_PATH_LABEL_PREFIX}{parent_name}/{candidate.name}"
    try:
        relative = candidate.resolve().relative_to(workspace)
        return relative.as_posix()
    except (OSError, ValueError):
        return candidate.name


def _parse_frontmatter(
    content: str,
    *,
    fallback_name: str,
) -> tuple[str, str, str]:
    """Return `(name, description, body)` extracted from `content`.

    The frontmatter is the block delimited by a leading line equal to
    `---` and a trailing line equal to `---`. Only `key: value` lines
    are honored; everything else inside the block is ignored. The
    parser recognizes only `name` and `description`. When no
    frontmatter is present, the body is the full content,
    `name` falls back to `fallback_name`, and `description` is empty.

    The parser is intentionally small and stdlib-only: a real YAML
    parser is out of scope for this slice and would require a runtime
    dependency.
    """

    lines = content.splitlines(keepends=False)
    if not lines or lines[0].rstrip("\r") != "---":
        return fallback_name, "", content
    end_index = -1
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r") == "---":
            end_index = index
            break
    if end_index == -1:
        return fallback_name, "", content
    name = fallback_name
    description = ""
    for raw_line in lines[1:end_index]:
        stripped = raw_line.rstrip("\r")
        if not stripped or stripped.lstrip().startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        if key == "name" and value:
            name = value
        elif key == "description":
            description = value
    body_lines = lines[end_index + 1 :]
    body = "\n".join(body_lines)
    if content.endswith("\n") and not body.endswith("\n"):
        body = body + "\n"
    if body.startswith("\n"):
        body = body.lstrip("\n")
    return name, description, body


def safe_resource_metadata(
    files: Sequence[object],
) -> list[dict[str, object]]:
    """Project resource files to archive-safe per-file metadata.

    The returned list contains only `path_label`, `sha256`,
    `byte_length`, and `truncated`. The body, name, and description
    are intentionally excluded so the archive never receives the
    instruction text. Callers pass either `Sequence[SkillFile]` or
    `Sequence[PromptTemplate]`.
    """

    out: list[dict[str, object]] = []
    for entry in files:
        out.append(
            {
                "path_label": getattr(entry, "path_label"),
                "sha256": getattr(entry, "sha256"),
                "byte_length": getattr(entry, "byte_length"),
                "truncated": getattr(entry, "truncated"),
            }
        )
    return out
