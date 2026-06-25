"""Workspace-context instruction discovery for the native pipy runtime.

Slice 2 of the Workspace Context Loading Parity Track. This module is a
pure, dependency-free pipy-owned slopfork of pi-mono's
`loadProjectContextFiles` in
`packages/coding-agent/src/core/resource-loader.ts`. It is not wired into
provider system prompts or session metadata in this slice; slice 3 wires
the output into the existing native adapters.

Discovery rules (pinned by `tests/test_native_workspace_context.py`):

- Per-directory candidate precedence (highest first):
  `AGENTS.md > AGENTS.MD > pipy.md > PIPY.md`
  (sourced from `INSTRUCTION_CANDIDATE_FILENAMES`; the chrome listing
  imports the same tuple so the two never drift). The first existing
  candidate per directory wins; the others are not considered for
  that directory. Neighbour-tool config (e.g. Claude Code's
  `CLAUDE.md`, Codex's `.codex/...`) is intentionally not in this
  list — pipy is a separate product and must not silently compose
  another agent's prompts into its own system prompt.
- The global root is resolved through `PIPY_CONFIG_HOME`, then
  `${XDG_CONFIG_HOME}/pipy`, then `~/.pipy` (when present, mirroring
  the chezmoi-managed pipy-owned home the startup chrome lists),
  then `~/.config/pipy` as the XDG default. The first existing
  candidate file in the global root is returned with a
  `<global>/<name>` path label.
- The workspace and each parent directory is searched after the global
  root. The returned tuple lists the root-most ancestor first and the
  workspace itself last, so more-specific instructions appear later in
  the composed system prompt and override earlier ones.
- Results are deduplicated by canonical (`Path.resolve()`) absolute
  path. The first occurrence wins; later occurrences are dropped
  silently (the loader does not fall back to other candidates for that
  directory once the first existing candidate is matched).
- Missing files never raise. `PermissionError` / `OSError` on a
  candidate is treated as "not present" and the search continues.
- A candidate that is a symlink whose resolved real path is not inside
  the directory it was found in is skipped, and the loader falls
  through to the next candidate name for the same directory. This
  closes the `AGENTS.md -> /etc/secrets`-style escape vector without
  blocking a legitimate `pipy.md` from the same directory.
- Each file loads at most `per_file_byte_cap` bytes into the prompt. If
  the file is longer, the loader returns the truncated bytes with a
  deterministic marker appended and `truncated=True`. `byte_length` and
  `sha256` always describe the file as it exists on disk, with hashing
  streamed in bounded chunks so callers can detect changes between runs.
- The total bytes loaded across all included files is bounded by
  `total_byte_cap`. Once including the next file would exceed the cap,
  the loader stops and appends a deterministic synthetic
  `<workspace-context: total byte cap reached>` entry. The
  `WorkspaceInstructionDiscovery.total_byte_cap_reached` flag mirrors
  the same fact for session metadata.

No bodies leave the returned tuple; callers compose the in-memory
content for prompt construction. `pipy_session.recorder` only ever
records `path_label`, `sha256`, `byte_length`, and `truncated` per
file plus `total_byte_cap_reached`.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

INSTRUCTION_CANDIDATE_FILENAMES: tuple[str, ...] = (
    "AGENTS.md",
    "AGENTS.MD",
    "pipy.md",
    "PIPY.md",
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

WORKSPACE_INSTRUCTIONS_PROMPT_HEADER: str = (
    "## Workspace Instructions\n"
    "The files below were discovered as workspace context. They are listed\n"
    "global-first, then ancestor directories from the root-most ancestor down\n"
    "to the workspace itself last. Treat later files as overriding earlier\n"
    "ones when guidance conflicts.\n"
)
WORKSPACE_INSTRUCTIONS_FILE_HEADER_TEMPLATE: str = (
    "\n### {path_label} (sha256={sha256_short}, bytes={byte_length}{trunc_suffix})\n\n"
)
WORKSPACE_INSTRUCTIONS_PROMPT_FOOTER: str = (
    "\n## End Workspace Instructions\n"
)


@dataclass(frozen=True, slots=True)
class WorkspaceInstructionFile:
    """One discovered AGENTS.md / pipy.md instruction file.

    `path_label` is workspace-relative POSIX for files in or under the
    workspace (for example, `AGENTS.md`), `..`-prefixed relative POSIX for
    ancestor files (for example, `../AGENTS.md`), and `<global>/<name>`
    for files under the global root. `sha256` and `byte_length` describe
    the file as it exists on disk; `truncated=True` means `content`
    contains only the first `per_file_byte_cap` bytes plus a marker.
    `content` is utf-8 text decoded with `errors="replace"` so a binary
    or partially invalid file does not crash the loader.
    """

    path_label: str
    sha256: str
    byte_length: int
    content: str
    truncated: bool


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
    """Discover instruction files in the global root, workspace, and ancestors.

    See module docstring for the full set of pinned rules. The returned
    tuple is ordered: global instruction file first (if any), then
    ancestor files from the root-most ancestor down to the workspace's
    direct parent, then the workspace's own instruction file last. A
    deterministic `<workspace-context: total byte cap reached>` marker
    is appended when the total byte cap stops further inclusion.
    """

    if per_file_byte_cap < 1:
        raise ValueError(
            "per_file_byte_cap must be >= 1; "
            f"got {per_file_byte_cap}"
        )
    if total_byte_cap < 1:
        raise ValueError(
            "total_byte_cap must be >= 1; "
            f"got {total_byte_cap}"
        )

    resolved_workspace = workspace_root.expanduser().resolve()
    seen_paths: set[Path] = set()
    discovered: list[WorkspaceInstructionFile] = []

    global_root = resolve_global_instruction_root(env=env, home_dir=home_dir)
    global_entry = _load_first_candidate(
        global_root,
        seen_paths=seen_paths,
        per_file_byte_cap=per_file_byte_cap,
        path_label_for=_global_path_label,
    )
    if global_entry is not None:
        discovered.append(global_entry)

    ancestors_root_first: list[WorkspaceInstructionFile] = []
    current = resolved_workspace
    while True:
        entry = _load_first_candidate(
            current,
            seen_paths=seen_paths,
            per_file_byte_cap=per_file_byte_cap,
            path_label_for=lambda filename, directory=current: _workspace_path_label(
                directory, filename, resolved_workspace
            ),
        )
        if entry is not None:
            ancestors_root_first.insert(0, entry)
        parent = current.parent
        if parent == current:
            break
        current = parent

    discovered.extend(ancestors_root_first)

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
        notice = TOTAL_BYTE_CAP_NOTICE
        capped.append(
            WorkspaceInstructionFile(
                path_label=TOTAL_BYTE_CAP_MARKER_PATH_LABEL,
                sha256="",
                byte_length=0,
                content=notice,
                truncated=True,
            )
        )

    return WorkspaceInstructionDiscovery(
        instructions=tuple(capped),
        total_byte_cap_reached=cap_reached,
    )


def _load_first_candidate(
    directory: Path,
    *,
    seen_paths: set[Path],
    per_file_byte_cap: int,
    path_label_for,
) -> WorkspaceInstructionFile | None:
    try:
        if not directory.is_dir():
            return None
    except OSError:
        return None
    try:
        resolved_dir = directory.resolve()
    except OSError:
        return None
    for candidate_name in INSTRUCTION_CANDIDATE_FILENAMES:
        candidate = directory / candidate_name
        try:
            is_file = candidate.is_file()
        except OSError:
            continue
        if not is_file:
            continue
        try:
            resolved_candidate = candidate.resolve()
        except OSError:
            continue
        try:
            resolved_candidate.relative_to(resolved_dir)
        except ValueError:
            continue
        if resolved_candidate in seen_paths:
            continue
        try:
            head, byte_length, sha256 = _read_capped_bytes(
                resolved_candidate,
                per_file_byte_cap=per_file_byte_cap,
            )
        except OSError:
            continue
        seen_paths.add(resolved_candidate)
        truncated = byte_length > per_file_byte_cap
        if truncated:
            content = head.decode("utf-8", errors="replace") + (
                PER_FILE_TRUNCATION_MARKER_TEMPLATE.format(cap=per_file_byte_cap)
            )
        else:
            content = head.decode("utf-8", errors="replace")
        path_label = path_label_for(candidate_name)
        return WorkspaceInstructionFile(
            path_label=path_label,
            sha256=sha256,
            byte_length=byte_length,
            content=content,
            truncated=truncated,
        )
    return None


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


def _global_path_label(filename: str) -> str:
    return f"{GLOBAL_PATH_LABEL_PREFIX}{filename}"


def _workspace_path_label(
    directory: Path,
    filename: str,
    resolved_workspace: Path,
) -> str:
    if directory == resolved_workspace:
        return filename
    try:
        relative = directory.relative_to(resolved_workspace)
        return f"{relative.as_posix()}/{filename}"
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

    Reads `PIPY_CONFIG_HOME`, then `${XDG_CONFIG_HOME}/pipy`, then
    `~/.config/pipy` to find the global root. Production adapters pass this
    loader; tests pass `empty_workspace_instruction_loader` (or a custom
    loader scoped to `tmp_path`) for hermeticity.
    """

    return discover_workspace_instructions(workspace_root)


def empty_workspace_instruction_loader(
    workspace_root: Path,  # noqa: ARG001 - matches WorkspaceInstructionLoader signature
) -> WorkspaceInstructionDiscovery:
    """A deterministic no-op loader for tests that want an empty discovery."""

    return WorkspaceInstructionDiscovery(instructions=(), total_byte_cap_reached=False)


def compose_system_prompt(
    base_prompt: str,
    discovery: WorkspaceInstructionDiscovery,
) -> str:
    """Compose a system prompt from a base bootstrap and workspace instructions.

    The base prompt is preserved verbatim. If `discovery.instructions` is
    empty, the function returns the base unchanged. Otherwise the discovered
    files are appended in their existing order with a deterministic
    `## Workspace Instructions` section header and per-file headers carrying
    the path label, short sha256, and byte length. The composed string is
    safe to send as a `ProviderRequest.system_prompt`.
    """

    if not discovery.instructions:
        return base_prompt

    parts: list[str] = [base_prompt.rstrip(), "\n", WORKSPACE_INSTRUCTIONS_PROMPT_HEADER]
    for entry in discovery.instructions:
        trunc_suffix = "+truncated" if entry.truncated else ""
        header = WORKSPACE_INSTRUCTIONS_FILE_HEADER_TEMPLATE.format(
            path_label=entry.path_label,
            sha256_short=entry.sha256[:12] if entry.sha256 else "",
            byte_length=entry.byte_length,
            trunc_suffix=trunc_suffix,
        )
        parts.append(header)
        parts.append(entry.content.rstrip())
        parts.append("\n")
    parts.append(WORKSPACE_INSTRUCTIONS_PROMPT_FOOTER)
    return "".join(parts)


def workspace_instruction_safe_metadata(
    discovery: WorkspaceInstructionDiscovery,
) -> dict[str, object]:
    """Return the metadata-only summary for session safe context.

    The returned dict carries only `path_label`, `sha256`, `byte_length`,
    and `truncated` per discovered file plus a `total_byte_cap_reached`
    flag. Instruction bodies never leak into this surface. Pinned by the
    privacy tests in slice 3.
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
