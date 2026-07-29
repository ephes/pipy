"""Shared discovery helpers for workspace `.pipy/skills`, `.pipy/templates`,
and `.pipy/commands` resource stores.

This module is an implementation detail of
`pipy_harness.native.skills`, `pipy_harness.native.prompt_templates`,
and `pipy_harness.native.custom_commands`. It owns the parts the three
loaders truly share: the global-root resolver, a tiny frontmatter
parser (no `yaml` import), the symlink-safe per-file reader with byte
cap and truncation marker, the per-candidate safety policy, and a
`*.md` directory glob that dedupes by canonical path. Each public
module wires these into its own dataclass plus its own composition /
lookup surface.

The helpers mirror the conventions pinned by
`pipy_harness.native.workspace_context`: stdlib only, no pydantic,
missing files never raise, resource directories must not be symlinks,
resource-file symlinks must resolve inside the containing directory,
per-file body loads are bounded with a deterministic marker, and the
global root resolves through `PIPY_CONFIG_HOME` then
`${XDG_CONFIG_HOME}/pipy` then `~/.config/pipy`.

Safety policy (in addition to byte caps and symlink containment):
candidate files are skipped silently when the filename looks secret
(`pipy_harness.capture.looks_sensitive`), when the loaded head bytes
contain a NUL byte (binary content), or when the bare filename is a
generated/ignored artifact (`read_only_tool._is_ignored_or_generated`,
applied to the filename only — the pipy-owned `.pipy/` parent is loaded
by design and is not treated as "ignored"). Oversized files are bounded
by the per-file and total byte caps.

No body content is intended to leave the in-process discovery layer.
Callers that want to record per-file metadata for the archive should
project the dataclass to `{path_label, sha256, byte_length,
truncated}` only.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pipy_harness.capture import looks_sensitive
from pipy_harness.native.read_only_tool import _is_ignored_or_generated

if TYPE_CHECKING:
    from pipy_harness.native.package_resources import PackageRoot

PIPY_CONFIG_HOME_ENV: str = "PIPY_CONFIG_HOME"
XDG_CONFIG_HOME_ENV: str = "XDG_CONFIG_HOME"
PIPY_CONFIG_DIR_NAME: str = "pipy"

WORKSPACE_PIPY_DIR_NAME: str = ".pipy"

GLOBAL_PATH_LABEL_PREFIX: str = "<global>/"
PACKAGE_PATH_LABEL_PREFIX: str = "<package>/"
CLI_PATH_LABEL_PREFIX: str = "<cli>/"

PER_FILE_TRUNCATION_MARKER_TEMPLATE: str = (
    "\n\n[pipy: resource file truncated at {cap} bytes]\n"
)

DEFAULT_PER_FILE_BYTE_CAP: int = 64 * 1024
DEFAULT_TOTAL_BYTE_CAP: int = 256 * 1024
_HASH_CHUNK_SIZE: int = 1024 * 1024


@dataclass(frozen=True, slots=True)
class _RawResourceFile:
    """Internal representation of a discovered resource file.

    Public modules wrap this in their own typed dataclass so the
    public API names (`SkillFile`, `PromptTemplate`,
    `CustomSlashCommand`) stay obvious and stable.

    `absolute_path` is the resolved on-disk path of the file. It is an
    in-process discovery detail that a public module may surface (skills
    need it for the system-prompt `<location>`), but it must never reach
    the archive-safe metadata projection (`safe_resource_metadata`).
    """

    path_label: str
    name: str
    description: str
    body: str
    sha256: str
    byte_length: int
    truncated: bool
    absolute_path: Path


@dataclass(frozen=True, slots=True)
class _ResourceSource:
    path: Path
    kind: str
    ignore_root: Path
    package_filters: tuple[str, ...]
    explicit_file: bool


@dataclass(frozen=True, slots=True)
class _LoadedResourceCandidate:
    candidate: Path
    resolved_path: Path
    name: str
    description: str
    body: str
    byte_length: int
    head: bytes
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
    package_roots: "Sequence[PackageRoot]" = (),
    explicit_paths: Sequence[Path] = (),
    include_defaults: bool = True,
    include_workspace_defaults: bool = False,
    include_global_defaults: bool = True,
    include_package_defaults: bool = True,
    dedupe_by_name: bool = False,
) -> tuple[list[_RawResourceFile], bool]:
    """Discover Markdown files in a workspace and global resource directory.

    Workspace-local discovery is fail-closed by default. Product callers must
    opt in only after resolving project trust. `workspace_subdir` is the path
    relative to `<workspace>/.pipy/` (for
    example, `skills` or `templates`). `global_subdir` is the path relative to
    the global resource root. `package_roots` lists concrete `PackageRoot`s
    contributed by installed local-path or managed git packages; they are
    searched after the workspace and global dirs. `explicit_paths` are per-run
    CLI paths (files or directories) searched before the defaults so an explicit
    CLI resource wins a name collision. When `include_defaults` is false,
    workspace/global/package discovery is skipped but explicit paths still load,
    matching Pi's
    `--no-skills`/`--no-prompt-templates` behavior. Each package root may carry
    per-package `+/-pattern` filters that scope that one package's resources by
    name. When `dedupe_by_name` is set, a later file whose resolved name was
    already seen is dropped (first wins), matching Pi's name-deduped
    skill/prompt loading.

    Discovery rules:

    - Workspace dir is `<workspace>/.pipy/<workspace_subdir>`.
    - Global dir is `<global-root>/<global_subdir>`.
    - Package dirs are the explicit `package_roots`, in order.
    - Both dirs are stat-globbed for `*.md` files one level deep; no
      recursion.
    - Workspace files come first in the returned list, then global
      files. Within each source the iteration order is sorted by file
      name so the result is deterministic.
    - Results are deduplicated by canonical (`Path.resolve()`) path.
      The first occurrence wins.
    - Each file loads at most `per_file_byte_cap` bytes into its body;
      a longer file is truncated with a deterministic marker and
      `truncated=True`. `byte_length` and `sha256` always describe the
      on-disk file, with hashing streamed in bounded chunks.
    - Symlinks must resolve inside the source directory they were found in.
      A symlink that escapes is skipped silently.
    - Candidate files are skipped silently when the filename looks
      secret, when the loaded head bytes are binary (contain a NUL
      byte), or when the bare filename is a generated/ignored artifact.
    - Total bytes loaded across all files is bounded by
      `total_byte_cap`. Once the next file would push the running
      total past the cap, the loader stops and returns
      `total_byte_cap_reached=True`. The partial-loaded file is not
      included.
    - Missing directories, missing files, and `OSError` on read are
      treated as "no resources here" and never raised.
    """

    _validate_resource_byte_caps(per_file_byte_cap, total_byte_cap)
    resolved_workspace = workspace_root.expanduser().resolve()
    global_root = resolve_global_resource_root(env=config_home_env, home_dir=home_dir)
    sources = _assemble_resource_sources(
        resolved_workspace=resolved_workspace,
        workspace_subdir=workspace_subdir,
        global_root=global_root,
        global_subdir=global_subdir,
        package_roots=package_roots,
        explicit_paths=explicit_paths,
        include_defaults=include_defaults,
        include_workspace_defaults=include_workspace_defaults,
        include_global_defaults=include_global_defaults,
        include_package_defaults=include_package_defaults,
    )

    seen_paths: set[Path] = set()
    seen_names: set[str] = set()
    raw_files: list[_RawResourceFile] = []
    total_loaded = 0
    cap_reached = False

    for source in sources:
        for candidate, resolved_candidate in _screen_resource_candidates(
            source, seen_paths
        ):
            loaded = _load_resource_candidate(
                candidate, resolved_candidate, per_file_byte_cap
            )
            if loaded is None or not _resource_name_is_selected(
                loaded.name,
                package_filters=source.package_filters,
                dedupe_by_name=dedupe_by_name,
                seen_names=seen_names,
            ):
                continue
            if total_loaded + loaded.byte_length > total_byte_cap:
                cap_reached = True
                break
            resource = _materialize_resource_candidate(
                loaded, source_kind=source.kind, workspace=resolved_workspace
            )
            if resource is None:
                continue
            raw_files.append(resource)
            seen_paths.add(loaded.resolved_path)
            if dedupe_by_name:
                seen_names.add(loaded.name)
            total_loaded += loaded.byte_length
        if cap_reached:
            break

    return raw_files, cap_reached


def _validate_resource_byte_caps(per_file_byte_cap: int, total_byte_cap: int) -> None:
    if per_file_byte_cap < 1:
        raise ValueError(f"per_file_byte_cap must be >= 1; got {per_file_byte_cap}")
    if total_byte_cap < 1:
        raise ValueError(f"total_byte_cap must be >= 1; got {total_byte_cap}")


def _assemble_resource_sources(
    *,
    resolved_workspace: Path,
    workspace_subdir: str,
    global_root: Path,
    global_subdir: str,
    package_roots: "Sequence[PackageRoot]",
    explicit_paths: Sequence[Path],
    include_defaults: bool,
    include_workspace_defaults: bool,
    include_global_defaults: bool,
    include_package_defaults: bool,
) -> list[_ResourceSource]:
    """Assemble CLI and default sources in their first-wins search order."""

    sources: list[_ResourceSource] = []
    for explicit in explicit_paths:
        path = explicit.expanduser()
        if not path.is_absolute():
            path = (resolved_workspace / path).resolve()
        explicit_file = path.suffix == ".md"
        sources.append(
            _ResourceSource(
                path=path,
                kind="cli",
                ignore_root=path.parent if explicit_file else path,
                package_filters=(),
                explicit_file=explicit_file,
            )
        )
    if not include_defaults:
        return sources

    if include_workspace_defaults:
        sources.append(
            _ResourceSource(
                path=(resolved_workspace / WORKSPACE_PIPY_DIR_NAME / workspace_subdir),
                kind="workspace",
                ignore_root=resolved_workspace,
                package_filters=(),
                explicit_file=False,
            )
        )
    if include_global_defaults:
        sources.append(
            _ResourceSource(
                path=global_root / global_subdir,
                kind="global",
                ignore_root=global_root,
                package_filters=(),
                explicit_file=False,
            )
        )
    if include_package_defaults:
        sources.extend(
            _ResourceSource(
                path=root.path,
                kind="package",
                ignore_root=root.path,
                package_filters=tuple(root.filters),
                explicit_file=False,
            )
            for root in package_roots
        )
    return sources


def _screen_resource_candidates(
    source: _ResourceSource,
    seen_paths: set[Path],
) -> Iterator[tuple[Path, Path]]:
    """Resolve candidates and enforce source containment and filename safety."""

    try:
        if source.path.is_symlink() and not source.explicit_file:
            return
        containment_root = (
            source.path.parent.expanduser().resolve()
            if source.explicit_file
            else source.path.expanduser().resolve()
        )
    except OSError:
        return

    candidates = [source.path] if source.explicit_file else _iter_md_files(source.path)
    for candidate in candidates:
        try:
            resolved_candidate = candidate.resolve()
            resolved_candidate.relative_to(containment_root)
        except (OSError, ValueError):
            continue
        if resolved_candidate in seen_paths:
            continue
        if not _candidate_name_is_safe(candidate.name, source.ignore_root):
            continue
        yield candidate, resolved_candidate


def _load_resource_candidate(
    candidate: Path,
    resolved_candidate: Path,
    per_file_byte_cap: int,
) -> _LoadedResourceCandidate | None:
    """Stat and bounded-read one screened candidate without hashing it."""

    try:
        byte_length = resolved_candidate.stat().st_size
        head = _read_head_bytes(resolved_candidate, per_file_byte_cap)
    except OSError:
        return None
    if b"\x00" in head:
        return None

    truncated = byte_length > per_file_byte_cap
    content = head.decode("utf-8", errors="replace")
    if truncated:
        content += PER_FILE_TRUNCATION_MARKER_TEMPLATE.format(cap=per_file_byte_cap)
    name, description, body = _parse_frontmatter(content, fallback_name=candidate.stem)
    return _LoadedResourceCandidate(
        candidate=candidate,
        resolved_path=resolved_candidate,
        name=name,
        description=description,
        body=body,
        byte_length=byte_length,
        head=head,
        truncated=truncated,
    )


def _resource_name_is_selected(
    name: str,
    *,
    package_filters: tuple[str, ...],
    dedupe_by_name: bool,
    seen_names: set[str],
) -> bool:
    """Apply package filtering and optional first-wins name deduplication."""

    if package_filters and not _name_passes_filter(name, package_filters):
        return False
    return not dedupe_by_name or name not in seen_names


def _materialize_resource_candidate(
    loaded: _LoadedResourceCandidate,
    *,
    source_kind: str,
    workspace: Path,
) -> _RawResourceFile | None:
    """Hash an accepted candidate and project it to the discovered record."""

    try:
        sha256 = (
            _hash_file(loaded.resolved_path)
            if loaded.truncated
            else hashlib.sha256(loaded.head).hexdigest()
        )
    except OSError:
        return None
    return _RawResourceFile(
        path_label=_path_label_for(
            candidate=loaded.candidate,
            source_kind=source_kind,
            workspace=workspace,
        ),
        name=loaded.name,
        description=loaded.description,
        body=loaded.body,
        sha256=sha256,
        byte_length=loaded.byte_length,
        truncated=loaded.truncated,
        absolute_path=loaded.resolved_path,
    )


def _name_passes_filter(name: str, filters: tuple[str, ...]) -> bool:
    """Apply a package's Pi-shaped `+/-pattern` filter to a resource name."""

    from pipy_harness.native.resource_enablement import is_resource_enabled

    return is_resource_enabled(name, list(filters))


def _candidate_name_is_safe(filename: str, ignore_root: Path) -> bool:
    """Return True when `filename` may be loaded as a resource file.

    The screen runs on the bare filename so the pipy-owned `.pipy/`
    parent directory (which appears in
    `read_only_tool._GENERATED_PARTS`) is never treated as an "ignored"
    location — the resource stores live under `.pipy/` by design. A
    filename is rejected when it contains a control character (so a name
    derived from the filename stem, and the recorded `path_label`, can
    never carry a terminal-control sequence), when it looks
    secret-shaped, or when the bare filename matches a generated suffix
    / `.gitignore` pattern under `ignore_root`.
    """

    if _contains_control_character(filename):
        return False
    if looks_sensitive(filename):
        return False
    if _is_ignored_or_generated(filename, ignore_root):
        return False
    return True


def _contains_control_character(value: str) -> bool:
    """Return True when `value` holds a C0/C1 control char or DEL.

    Mirrors the character class stripped by `_sanitize_label`. Used to
    reject resource filenames outright, since the filename feeds both
    the frontmatter-fallback `name` and the recorded `path_label`,
    neither of which is sanitized downstream.
    """

    return any(
        ord(ch) < 0x20 or ord(ch) == 0x7F or 0x80 <= ord(ch) <= 0x9F for ch in value
    )


def _read_head_bytes(path: Path, per_file_byte_cap: int) -> bytes:
    """Read at most `per_file_byte_cap` head bytes (the body/name source).

    Bounded by design: enough to parse frontmatter and run the binary/
    filter/dedup screens without reading a large file in full.
    """

    with path.open("rb") as handle:
        return handle.read(per_file_byte_cap)


def _hash_file(path: Path) -> str:
    """Stream the whole file and return its sha256 hex digest.

    Used only for an *included* truncated resource, so an over-cap or
    skipped (filtered/duplicate) file is never hashed in full.
    """

    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


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
    if source_kind == "package":
        # Label as <package>/<subdir>/<filename> so a package resource is
        # never recorded with its absolute on-disk source path. The parent
        # dir name is a manifest-declared package directory, so it is
        # sanitized (control bytes stripped) before it enters the label —
        # the filename itself was already screened by `_candidate_name_is_safe`.
        parent_name = _sanitize_label(candidate.parent.name) or "package"
        return f"{PACKAGE_PATH_LABEL_PREFIX}{parent_name}/{candidate.name}"
    if source_kind == "cli":
        parent_name = _sanitize_label(candidate.parent.name) or "resource"
        return f"{CLI_PATH_LABEL_PREFIX}{parent_name}/{candidate.name}"
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

    fallback_name = _sanitize_label(fallback_name) or fallback_name
    lines = content.splitlines(keepends=False)
    if not lines or lines[0].rstrip("\r") != "---":
        return fallback_name, "", content
    end_index = _frontmatter_end_index(lines)
    if end_index is None:
        return fallback_name, "", content
    name, description = _parse_frontmatter_fields(
        lines[1:end_index], fallback_name=fallback_name
    )
    body = _reconstruct_frontmatter_body(content, lines[end_index + 1 :])
    return name, description, body


def _frontmatter_end_index(lines: list[str]) -> int | None:
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r") == "---":
            return index
    return None


def _parse_frontmatter_fields(
    lines: list[str],
    *,
    fallback_name: str,
) -> tuple[str, str]:
    """Parse the supported, single-line frontmatter label fields."""

    name = fallback_name
    description = ""
    for raw_line in lines:
        stripped = raw_line.rstrip("\r")
        if not stripped or stripped.lstrip().startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if (
            value.startswith(("'", '"'))
            and value.endswith(("'", '"'))
            and len(value) >= 2
        ):
            value = value[1:-1]
        if key == "name":
            sanitized = _sanitize_label(value)
            if sanitized:
                name = sanitized
        elif key == "description":
            description = _sanitize_label(value)
    return name, description


def _reconstruct_frontmatter_body(content: str, body_lines: list[str]) -> str:
    """Rebuild body newlines exactly as the legacy frontmatter parser did."""

    body = "\n".join(body_lines)
    if content.endswith("\n") and not body.endswith("\n"):
        body += "\n"
    if body.startswith("\n"):
        body = body.lstrip("\n")
    return body


_LABEL_MAX_LENGTH: int = 256


def _sanitize_label(value: str) -> str:
    """Return a safe single-line label from frontmatter `value`.

    Frontmatter `name`/`description` values are rendered into local UI
    surfaces (REPL listings, the `[Skills]` startup chrome, and the
    tool-loop TUI slash menu), so a hostile resource file must not be
    able to inject terminal control sequences. This strips C0/C1
    control characters (including ESC) and DEL, collapses any remaining
    whitespace runs to single spaces, and caps the length so a label
    can neither move the cursor, clear the screen, nor blow out a row.
    """

    cleaned_chars = [
        ch
        for ch in value
        if not (ord(ch) < 0x20 or ord(ch) == 0x7F or 0x80 <= ord(ch) <= 0x9F)
    ]
    collapsed = " ".join("".join(cleaned_chars).split())
    if len(collapsed) > _LABEL_MAX_LENGTH:
        return collapsed[:_LABEL_MAX_LENGTH]
    return collapsed


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
