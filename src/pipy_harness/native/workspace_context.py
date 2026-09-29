"""Workspace-context instruction discovery for the native pipy runtime.

A pure, dependency-free pipy-owned port of pi-mono's `loadProjectContextFiles`
in `packages/coding-agent/src/core/resource-loader.ts` (reference
`4df157433`), plus the `project_context` system-prompt section from
`core/system-prompt.ts`.

Discovery rules (pinned by `tests/test_native_workspace_context.py`):

- Per-directory candidate precedence (highest first) is Pi's
  `loadContextFileFromDir` list:
  `AGENTS.override.md > AGENTS.md > AGENTS.MD > CLAUDE.md > CLAUDE.MD`
  (`INSTRUCTION_CANDIDATE_FILENAMES`). The first existing, readable
  candidate file per directory is that directory's context file; a
  directory-shaped candidate or a read failure falls through to the next
  name, like Pi.
- The global root (pipy's counterpart of Pi's `agentDir`) is resolved
  through `PIPY_CONFIG_HOME`, then `${XDG_CONFIG_HOME}/pipy`, then `~/.pipy`
  (when present), then `~/.config/pipy`. Its context file comes first with a
  `<global>/<name>` path label.
- The absolute workspace path (not realpath, like Pi's `resolvePath`) and
  each parent directory up to the filesystem root are searched after the
  global root. The result lists the root-most ancestor first and the
  workspace itself last.
- Linked-worktree shadowing mirrors Pi's `findShadowedContextFile`: when the
  workspace sits in a linked git worktree nested inside its main worktree,
  the main worktree's context file with the same basename as the worktree
  root's own context file is skipped, so one logical repository scope is not
  applied twice.
- Dedup: a directory whose context file was already loaded (compared by
  canonical path) or is the shadowed file contributes nothing; like Pi, the
  loader does not fall through to another candidate name in that case.
- Missing files never raise. A leading UTF-8 BOM is stripped from the
  content, like Pi's `stripBom`.
- pipy-kept guard (not in Pi): a candidate symlink whose resolved real path
  is not inside the directory it was found in is treated as absent, so the
  loader falls through to the next candidate name for that directory.
- pipy-kept caps (not in Pi): each file loads at most `per_file_byte_cap`
  bytes (a longer file is truncated with a deterministic marker and
  `truncated=True`; `byte_length` and `sha256` always describe the file on
  disk). The total loaded bytes are bounded by `total_byte_cap`; once the
  next file would exceed it, loading stops and a synthetic
  `<workspace-context: total byte cap reached>` entry is appended.

No bodies leave the returned tuple; callers compose the in-memory content for
prompt construction. `pipy_session.recorder` only ever records `path_label`,
`sha256`, `byte_length`, and `truncated` per file plus
`total_byte_cap_reached`. The absolute path appears only in the provider
system prompt, as in Pi.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

INSTRUCTION_CANDIDATE_FILENAMES: tuple[str, ...] = (
    "AGENTS.override.md",
    "AGENTS.md",
    "AGENTS.MD",
    "CLAUDE.md",
    "CLAUDE.MD",
)

DEFAULT_PER_FILE_BYTE_CAP: int = 64 * 1024
DEFAULT_TOTAL_BYTE_CAP: int = 256 * 1024
_HASH_CHUNK_SIZE: int = 1024 * 1024

GLOBAL_PATH_LABEL_PREFIX: str = "<global>/"
TOTAL_BYTE_CAP_MARKER_PATH_LABEL: str = "<workspace-context: total byte cap reached>"
TOTAL_BYTE_CAP_NOTICE: str = (
    "[pipy: workspace-instruction loading stopped at the total byte cap]\n"
)
PER_FILE_TRUNCATION_MARKER_TEMPLATE: str = (
    "\n\n[pipy: workspace-instruction file truncated at {cap} bytes]\n"
)
PIPY_CONFIG_HOME_ENV: str = "PIPY_CONFIG_HOME"
XDG_CONFIG_HOME_ENV: str = "XDG_CONFIG_HOME"
PIPY_CONFIG_DIR_NAME: str = "pipy"

# Pi `renderProjectContext` + the section tag wrapper in `buildSystemPromptSections`.
PROJECT_CONTEXT_SECTION_TAG: str = "project_context"
PROJECT_CONTEXT_INTRO: str = "Project-specific instructions and guidelines:"

_UTF8_BOM: str = "﻿"


@dataclass(frozen=True, slots=True)
class WorkspaceInstructionFile:
    """One discovered context file (`AGENTS.md`, `CLAUDE.md`, ...).

    `path_label` is workspace-relative POSIX for files in or under the
    workspace (for example, `AGENTS.md`), `..`-prefixed relative POSIX for
    ancestor files (for example, `../CLAUDE.md`), and `<global>/<name>`
    for files under the global root. `sha256` and `byte_length` describe
    the file as it exists on disk; `truncated=True` means `content`
    contains only the first `per_file_byte_cap` bytes plus a marker.
    `content` is utf-8 text decoded with `errors="replace"` so a binary
    or partially invalid file does not crash the loader.

    `absolute_path` is the absolute (not symlink-resolved) path, matching
    Pi's context-file `path`. It is used only for the provider system
    prompt and must never enter the archive-safe metadata projection.
    """

    path_label: str
    sha256: str
    byte_length: int
    content: str
    truncated: bool
    absolute_path: str = ""


@dataclass(frozen=True, slots=True)
class WorkspaceInstructionDiscovery:
    """The result of one `discover_workspace_instructions(...)` call."""

    instructions: tuple[WorkspaceInstructionFile, ...]
    total_byte_cap_reached: bool


def resolve_global_instruction_root(
    *,
    env: dict[str, str] | os._Environ[str] | None = None,
    home_dir: Path | None = None,
) -> Path:
    """Return the global pipy instruction root.

    Resolution order:

    1. `PIPY_CONFIG_HOME` (taken verbatim, then `~` expanded).
    2. `${XDG_CONFIG_HOME}/pipy`.
    3. `~/.pipy` when present (chezmoi-managed pipy-owned home, mirrors
       what the startup chrome lists).
    4. `~/.config/pipy` (XDG default).
    """

    env_map = env if env is not None else os.environ
    explicit = env_map.get(PIPY_CONFIG_HOME_ENV)
    if explicit:
        return Path(explicit).expanduser()
    xdg = env_map.get(XDG_CONFIG_HOME_ENV)
    if xdg:
        return Path(xdg).expanduser() / PIPY_CONFIG_DIR_NAME
    home = (home_dir or Path.home()).expanduser()
    pipy_home = home / ".pipy"
    if pipy_home.is_dir():
        return pipy_home
    return home / ".config" / PIPY_CONFIG_DIR_NAME


def discover_workspace_instructions(
    workspace_root: Path,
    *,
    env: dict[str, str] | os._Environ[str] | None = None,
    home_dir: Path | None = None,
    per_file_byte_cap: int = DEFAULT_PER_FILE_BYTE_CAP,
    total_byte_cap: int = DEFAULT_TOTAL_BYTE_CAP,
) -> WorkspaceInstructionDiscovery:
    """Discover context files in the global root, workspace, and ancestors.

    See module docstring for the full set of pinned rules. The returned
    tuple is ordered: global context file first (if any), then ancestor
    files from the root-most ancestor down to the workspace's direct
    parent, then the workspace's own context file last. A deterministic
    `<workspace-context: total byte cap reached>` marker is appended when
    the total byte cap stops further inclusion.
    """

    if per_file_byte_cap < 1:
        raise ValueError(f"per_file_byte_cap must be >= 1; got {per_file_byte_cap}")
    if total_byte_cap < 1:
        raise ValueError(f"total_byte_cap must be >= 1; got {total_byte_cap}")

    workspace = Path(os.path.abspath(workspace_root.expanduser()))
    resolved_workspace = _canonical(workspace)
    seen_paths: set[Path] = set()
    discovered: list[WorkspaceInstructionFile] = []

    global_root = resolve_global_instruction_root(env=env, home_dir=home_dir)
    global_entry = _load_first_candidate(
        global_root,
        per_file_byte_cap=per_file_byte_cap,
        path_label_for=_global_path_label,
    )
    if global_entry is not None:
        entry, canonical_path = global_entry
        discovered.append(entry)
        seen_paths.add(canonical_path)

    discovered.extend(
        _ancestor_context_files(
            workspace,
            resolved_workspace=resolved_workspace,
            seen_paths=seen_paths,
            per_file_byte_cap=per_file_byte_cap,
        )
    )
    return _apply_total_byte_cap(discovered, total_byte_cap)


def _ancestor_context_files(
    workspace: Path,
    *,
    resolved_workspace: Path,
    seen_paths: set[Path],
    per_file_byte_cap: int,
) -> list[WorkspaceInstructionFile]:
    """Walk cwd up to the filesystem root; return files root-most first."""

    shadowed = _find_shadowed_context_file(workspace)
    ancestors_root_first: list[WorkspaceInstructionFile] = []
    current = workspace
    while True:
        loaded = _load_first_candidate(
            current,
            per_file_byte_cap=per_file_byte_cap,
            path_label_for=partial(
                _workspace_path_label,
                current,
                workspace=workspace,
                resolved_workspace=resolved_workspace,
            ),
        )
        if loaded is not None:
            entry, canonical_path = loaded
            if canonical_path != shadowed and canonical_path not in seen_paths:
                ancestors_root_first.insert(0, entry)
                seen_paths.add(canonical_path)
        parent = current.parent
        if parent == current:
            return ancestors_root_first
        current = parent


def _apply_total_byte_cap(
    discovered: list[WorkspaceInstructionFile],
    total_byte_cap: int,
) -> WorkspaceInstructionDiscovery:
    capped: list[WorkspaceInstructionFile] = []
    total_loaded = 0
    cap_reached = False
    for entry in discovered:
        content_bytes = len(entry.content.encode("utf-8"))
        if total_loaded + content_bytes > total_byte_cap:
            cap_reached = True
            break
        capped.append(entry)
        total_loaded += content_bytes

    if cap_reached:
        capped.append(
            WorkspaceInstructionFile(
                path_label=TOTAL_BYTE_CAP_MARKER_PATH_LABEL,
                sha256="",
                byte_length=0,
                content=TOTAL_BYTE_CAP_NOTICE,
                truncated=True,
                absolute_path=TOTAL_BYTE_CAP_MARKER_PATH_LABEL,
            )
        )

    return WorkspaceInstructionDiscovery(
        instructions=tuple(capped),
        total_byte_cap_reached=cap_reached,
    )


def _canonical(path: Path) -> Path:
    """Pi `canonicalizePath`: the realpath, or the path itself on failure."""

    try:
        return path.resolve()
    except OSError:
        return path


def _load_first_candidate(
    directory: Path,
    *,
    per_file_byte_cap: int,
    path_label_for: Callable[[str], str],
) -> tuple[WorkspaceInstructionFile, Path] | None:
    """Pi `loadContextFileFromDir` plus pipy's symlink-escape guard.

    Returns the directory's context file and its canonical path, or `None`.
    """

    resolved_dir = _resolve_candidate_directory(directory)
    if resolved_dir is None:
        return None
    for candidate_name in INSTRUCTION_CANDIDATE_FILENAMES:
        resolved_candidate = _validated_candidate_path(
            directory,
            resolved_dir,
            candidate_name,
        )
        if resolved_candidate is None:
            continue
        entry = _materialize_candidate(
            directory / candidate_name,
            resolved_candidate,
            candidate_name,
            per_file_byte_cap=per_file_byte_cap,
            path_label_for=path_label_for,
        )
        if entry is not None:
            return entry, resolved_candidate
    return None


def _resolve_candidate_directory(directory: Path) -> Path | None:
    try:
        if not directory.is_dir():
            return None
    except OSError:
        return None
    try:
        return directory.resolve()
    except OSError:
        return None


def _validated_candidate_path(
    directory: Path,
    resolved_dir: Path,
    candidate_name: str,
) -> Path | None:
    candidate = directory / candidate_name
    try:
        if not candidate.is_file():
            return None
    except OSError:
        return None
    try:
        resolved_candidate = candidate.resolve()
    except OSError:
        return None
    try:
        resolved_candidate.relative_to(resolved_dir)
    except ValueError:
        return None
    return resolved_candidate


def _materialize_candidate(
    candidate: Path,
    resolved_candidate: Path,
    candidate_name: str,
    *,
    per_file_byte_cap: int,
    path_label_for: Callable[[str], str],
) -> WorkspaceInstructionFile | None:
    try:
        head, byte_length, sha256 = _read_capped_bytes(
            resolved_candidate,
            per_file_byte_cap=per_file_byte_cap,
        )
    except OSError:
        return None
    truncated = byte_length > per_file_byte_cap
    content = head.decode("utf-8", errors="replace")
    if content.startswith(_UTF8_BOM):
        content = content[len(_UTF8_BOM) :]
    if truncated:
        content += PER_FILE_TRUNCATION_MARKER_TEMPLATE.format(cap=per_file_byte_cap)
    return WorkspaceInstructionFile(
        path_label=path_label_for(candidate_name),
        sha256=sha256,
        byte_length=byte_length,
        content=content,
        truncated=truncated,
        absolute_path=str(candidate),
    )


def _read_capped_bytes(
    path: Path,
    *,
    per_file_byte_cap: int,
) -> tuple[bytes, int, str]:
    hasher = hashlib.sha256()
    byte_length = 0
    with path.open("rb") as handle:
        first_chunk = handle.read(per_file_byte_cap + 1)
        byte_length += len(first_chunk)
        hasher.update(first_chunk)
        head = first_chunk[:per_file_byte_cap]
        while True:
            chunk = handle.read(_HASH_CHUNK_SIZE)
            if not chunk:
                break
            byte_length += len(chunk)
            hasher.update(chunk)
    return head, byte_length, hasher.hexdigest()


# -- linked-worktree shadowing (Pi `findShadowedContextFile`) ---------------


@dataclass(frozen=True, slots=True)
class _GitPaths:
    repo_dir: Path
    common_git_dir: Path


def _find_git_paths(cwd: Path) -> _GitPaths | None:
    """Port of Pi `findGitPaths` (`core/footer-data-provider.ts`).

    Walks up from `cwd` to the first `.git` entry. A `.git` file must hold a
    `gitdir: <path>` pointer (resolved against its directory) whose `HEAD`
    exists; its `commondir` (when present) names the common git dir. A `.git`
    directory must contain `HEAD`. Any other shape or error returns `None`.
    """

    directory = cwd
    while True:
        git_path = directory / ".git"
        if os.path.exists(git_path):
            try:
                if git_path.is_file():
                    decided, found = _git_paths_from_gitdir_file(directory, git_path)
                    if decided:
                        return found
                elif git_path.is_dir():
                    if not (git_path / "HEAD").exists():
                        return None
                    return _GitPaths(repo_dir=directory, common_git_dir=git_path)
            except (OSError, UnicodeDecodeError):
                return None
        parent = directory.parent
        if parent == directory:
            return None
        directory = parent


def _git_paths_from_gitdir_file(
    directory: Path, git_path: Path
) -> tuple[bool, _GitPaths | None]:
    """Parse a `.git` file as `(decided, paths)`.

    A file without a `gitdir: ` pointer is undecided, so the walk continues
    to the parent directory, like Pi.
    """

    content = git_path.read_text(encoding="utf-8").strip()
    if not content.startswith("gitdir: "):
        return False, None
    git_dir = Path(os.path.normpath(directory / content[len("gitdir: ") :].strip()))
    if not (git_dir / "HEAD").exists():
        return True, None
    common_dir_path = git_dir / "commondir"
    if common_dir_path.exists():
        common = common_dir_path.read_text(encoding="utf-8").strip()
        common_git_dir = Path(os.path.normpath(git_dir / common))
    else:
        common_git_dir = git_dir
    return True, _GitPaths(repo_dir=directory, common_git_dir=common_git_dir)


def _find_shadowed_context_file(cwd: Path) -> Path | None:
    """Return the canonical main-repo context file a nested worktree shadows.

    Port of Pi `findShadowedContextFile`: `None` for an ordinary repo, a
    sibling worktree (main repo not an ancestor), a bare layout, and a
    submodule; otherwise the main worktree's file with the same basename as
    the linked worktree root's own context file.
    """

    git_paths = _find_git_paths(cwd)
    if git_paths is None:
        return None
    common_git_dir = _canonical(git_paths.common_git_dir)
    worktree_root = _canonical(git_paths.repo_dir)
    main_repo_root = common_git_dir.parent
    if not str(worktree_root).startswith(f"{main_repo_root}{os.sep}"):
        return None
    if _canonical(main_repo_root / ".git") != common_git_dir:
        return None
    loaded = _load_first_candidate(
        worktree_root,
        per_file_byte_cap=1,
        path_label_for=lambda name: name,
    )
    if loaded is None:
        return None
    entry, _canonical_path = loaded
    return _canonical(main_repo_root / Path(entry.absolute_path).name)


def _global_path_label(filename: str) -> str:
    return f"{GLOBAL_PATH_LABEL_PREFIX}{filename}"


def _workspace_path_label(
    directory: Path,
    filename: str,
    workspace: Path,
    resolved_workspace: Path,
) -> str:
    if directory == workspace:
        return filename
    try:
        relative = directory.relative_to(workspace)
        return f"{relative.as_posix()}/{filename}"
    except ValueError:
        pass
    try:
        steps = workspace.relative_to(directory).parts
        prefix = "/".join([".."] * len(steps))
        return f"{prefix}/{filename}"
    except ValueError:
        pass
    try:
        steps = resolved_workspace.relative_to(directory).parts
        prefix = "/".join([".."] * len(steps))
        return f"{prefix}/{filename}"
    except ValueError:
        return f"{directory.as_posix()}/{filename}"


# -- adapter-facing helpers ------------------------------------------------

WorkspaceInstructionLoader = Callable[[Path], WorkspaceInstructionDiscovery]


def default_workspace_instruction_loader(
    workspace_root: Path,
) -> WorkspaceInstructionDiscovery:
    """Resolve workspace instructions using the current process environment.

    Production adapters pass this loader; tests pass
    `empty_workspace_instruction_loader` (or a custom loader scoped to
    `tmp_path`) for hermeticity.
    """

    return discover_workspace_instructions(workspace_root)


def empty_workspace_instruction_loader(
    workspace_root: Path,  # noqa: ARG001 - matches WorkspaceInstructionLoader signature
) -> WorkspaceInstructionDiscovery:
    """A deterministic no-op loader for tests that want an empty discovery."""

    return WorkspaceInstructionDiscovery(instructions=(), total_byte_cap_reached=False)


def render_project_context_section(discovery: WorkspaceInstructionDiscovery) -> str:
    """Render Pi's tagged `project_context` system-prompt section.

    Mirrors `renderProjectContext` plus the `<name>\\n...\\n</name>` wrapper
    from Pi's `buildSystemPromptSections`. Returns an empty string when no
    context file was discovered.
    """

    if not discovery.instructions:
        return ""
    blocks = [PROJECT_CONTEXT_INTRO]
    for entry in discovery.instructions:
        path = entry.absolute_path or entry.path_label
        blocks.append(
            f'<project_instructions path="{path}">\n{entry.content}\n</project_instructions>'
        )
    body = "\n\n".join(blocks)
    return f"<{PROJECT_CONTEXT_SECTION_TAG}>\n{body}\n</{PROJECT_CONTEXT_SECTION_TAG}>"


def compose_system_prompt(
    base_prompt: str,
    discovery: WorkspaceInstructionDiscovery,
) -> str:
    """Compose a system prompt from a base prompt and discovered context files.

    The base prompt is preserved verbatim when nothing was discovered.
    Otherwise Pi's `project_context` section is appended after the base,
    separated by a blank line (Pi joins system-prompt sections with
    `\\n\\n`).
    """

    section = render_project_context_section(discovery)
    if not section:
        return base_prompt
    return f"{base_prompt.rstrip()}\n\n{section}"


def workspace_instruction_safe_metadata(
    discovery: WorkspaceInstructionDiscovery,
) -> dict[str, object]:
    """Return the metadata-only summary for session safe context.

    The returned dict carries only `path_label`, `sha256`, `byte_length`,
    and `truncated` per discovered file plus a `total_byte_cap_reached`
    flag. Instruction bodies and absolute paths never leak into this
    surface.
    """

    files: list[dict[str, object]] = []
    for entry in discovery.instructions:
        files.append(
            {
                "path_label": entry.path_label,
                "sha256": entry.sha256,
                "byte_length": entry.byte_length,
                "truncated": entry.truncated,
            }
        )
    return {
        "workspace_instruction_files": files,
        "workspace_instruction_total_byte_cap_reached": discovery.total_byte_cap_reached,
    }
