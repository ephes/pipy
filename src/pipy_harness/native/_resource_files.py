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
missing files never raise, per-file body loads are bounded with a
deterministic marker, and the global root resolves through the shared
config-home chain (`PIPY_CONFIG_HOME`, `${XDG_CONFIG_HOME}/pipy`, `~/.pipy`
when present, `~/.config/pipy`). Every store follows symlinks like Pi.

Safety policy (in addition to byte caps):
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
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pipy_harness.capture import looks_sensitive
from pipy_harness.native.ignore_rules import IgnoreMatcher, add_ignore_rules
from pipy_harness.native.read_only_tool import _is_ignored_or_generated
from pipy_harness.native.workspace_context import (
    PIPY_CONFIG_DIR_NAME as PIPY_CONFIG_DIR_NAME,
)
from pipy_harness.native.workspace_context import (
    PIPY_CONFIG_HOME_ENV as PIPY_CONFIG_HOME_ENV,
)
from pipy_harness.native.workspace_context import (
    XDG_CONFIG_HOME_ENV as XDG_CONFIG_HOME_ENV,
)
from pipy_harness.native.workspace_context import resolve_global_instruction_root

if TYPE_CHECKING:
    from pipy_harness.native.package_resources import PackageRoot

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
    disable_model_invocation: bool = False


@dataclass(frozen=True, slots=True)
class _ResourceSource:
    path: Path
    kind: str
    ignore_root: Path
    package_filters: tuple[str, ...]
    explicit_file: bool
    # "flat": `*.md` one level deep (templates, commands). "pi"/"agents": Pi's
    # skill layout walk (`collectSkillEntries` modes), used for skills only.
    layout: str = "flat"


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
    disable_model_invocation: bool = False


def resolve_global_resource_root(
    *,
    env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> Path:
    """Return the global pipy resource root (pipy's Pi `agentDir`).

    Delegates to `workspace_context.resolve_global_instruction_root`, the one
    config-home chain settings, context files, trust and keybindings use:

    1. `PIPY_CONFIG_HOME` (taken verbatim, then `~` expanded).
    2. `${XDG_CONFIG_HOME}/pipy`.
    3. `~/.pipy` when that directory exists.
    4. `~/.config/pipy`.
    """

    return resolve_global_instruction_root(env=env, home_dir=home_dir)


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
    skill_discovery: bool = False,
) -> tuple[list[_RawResourceFile], bool]:
    """Discover Markdown files in a workspace and global resource directory.

    `skill_discovery=True` switches to Pi's skill rules (see
    `pipy_harness.native.skills`): the `SKILL.md` directory layout with
    `.gitignore`/`.ignore`/`.fdignore` handling, the extra `.agents/skills`
    roots, the `SKILL.md` parent-directory name fallback, and dropping
    skills without a description.

    Workspace-local discovery is fail-closed by default. Product callers must
    opt in only after resolving project trust. `workspace_subdir` is the path
    relative to `<workspace>/.pipy/` (for
    example, `skills` or `templates`). `global_subdir` is the path relative to
    the global resource root. `package_roots` lists concrete `PackageRoot`s
    contributed by installed local-path or managed git packages; they are
    searched first, before the workspace and global dirs. `explicit_paths` are
    per-run CLI paths (files or directories) searched last, as Pi merges
    `--skill`/`--prompt-template` paths after its resolved resources, so a
    default resource wins a name collision. When `include_defaults` is false,
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
    - Package files come first in the returned list, then workspace,
      global and CLI files. Within each source the iteration order is
      sorted by file name so the result is deterministic.
    - Results are deduplicated by canonical (`Path.resolve()`) path.
      The first occurrence wins.
    - Each file loads at most `per_file_byte_cap` bytes into its body;
      a longer file is truncated with a deterministic marker and
      `truncated=True`. `byte_length` and `sha256` always describe the
      on-disk file, with hashing streamed in bounded chunks.
    - Every store follows symlinks like Pi.
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
    if skill_discovery:
        sources = _with_skill_layout_sources(
            sources,
            resolved_workspace=resolved_workspace,
            home=(home_dir or Path.home()).expanduser(),
            include_workspace_defaults=include_workspace_defaults,
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
                candidate,
                resolved_candidate,
                per_file_byte_cap,
                skill_discovery=skill_discovery,
            )
            if loaded is None or not _resource_name_is_selected(
                loaded.name,
                package_filters=source.package_filters,
                dedupe_by_name=dedupe_by_name,
                seen_names=seen_names,
            ):
                continue
            if skill_discovery and not loaded.description.strip():
                # Pi `loadSkillFromFile`: a skill without a description is not
                # loaded, so it neither counts nor shadows a later same name.
                continue
            if total_loaded + loaded.byte_length > total_byte_cap:
                cap_reached = True
                break
            resource = _materialize_resource_candidate(
                loaded, source=source, workspace=resolved_workspace
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


def read_resource_body(path: Path) -> str | None:
    """A resource file's text without its frontmatter, or ``None`` if unreadable.

    Pi's `_expandSkillCommand` reads the skill file again at invocation
    (`stripFrontmatter(readFileSync(...))`), not the body loaded at discovery.
    """

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return _parse_frontmatter(content, fallback_name=path.stem).body


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
    """Assemble the sources in Pi's first-wins search order.

    Pi `resource-loader.ts` merges the package manager's resources (packages,
    then the auto roots, project before user) before the `--skill` /
    `--prompt-template` paths (`additionalSkillPaths`, merged last), and the
    loaders keep the first resource of a name.
    """

    sources: list[_ResourceSource] = []
    if include_defaults and include_package_defaults:
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
    if include_defaults and include_workspace_defaults:
        sources.append(
            _ResourceSource(
                path=(resolved_workspace / WORKSPACE_PIPY_DIR_NAME / workspace_subdir),
                kind="workspace",
                ignore_root=resolved_workspace,
                package_filters=(),
                explicit_file=False,
            )
        )
    if include_defaults and include_global_defaults:
        sources.append(
            _ResourceSource(
                path=global_root / global_subdir,
                kind="global",
                ignore_root=global_root,
                package_filters=(),
                explicit_file=False,
            )
        )
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
    return sources


AGENTS_DIR_NAME: str = ".agents"
AGENTS_SKILLS_SUBDIR: str = "skills"


def _with_skill_layout_sources(
    sources: list[_ResourceSource],
    *,
    resolved_workspace: Path,
    home: Path,
    include_workspace_defaults: bool,
) -> list[_ResourceSource]:
    """Apply Pi's skill layout modes and add the `.agents/skills` roots.

    Pi order (`package-manager.ts` auto resources): project `.pi/skills`
    (pipy `.pipy/skills`, mode "pi", trusted), project `.agents/skills` from
    cwd up to the git root (mode "agents", trusted, excluding
    `~/.agents/skills`), user `<agentDir>/skills` (mode "pi"), then
    `~/.agents/skills` (mode "agents"). CLI and package directories load in
    mode "pi" (`loadSkillsFromDirInternal(dir, ..., true)`).
    """

    user_agents_skills = home / AGENTS_DIR_NAME / AGENTS_SKILLS_SUBDIR
    result: list[_ResourceSource] = []
    for source in sources:
        result.append(_replace_layout(source, "pi"))
        if source.kind == "workspace" and include_workspace_defaults:
            result.extend(
                _ResourceSource(
                    path=agents_dir,
                    kind="workspace",
                    ignore_root=resolved_workspace,
                    package_filters=(),
                    explicit_file=False,
                    layout="agents",
                )
                for agents_dir in _ancestor_agents_skill_dirs(
                    resolved_workspace, exclude=user_agents_skills
                )
            )
        if source.kind == "global":
            result.append(
                _ResourceSource(
                    path=user_agents_skills,
                    kind="user-agents",
                    ignore_root=home / AGENTS_DIR_NAME,
                    package_filters=(),
                    explicit_file=False,
                    layout="agents",
                )
            )
    return result


def _replace_layout(source: _ResourceSource, layout: str) -> _ResourceSource:
    return _ResourceSource(
        path=source.path,
        kind=source.kind,
        ignore_root=source.ignore_root,
        package_filters=source.package_filters,
        explicit_file=source.explicit_file,
        layout=layout,
    )


def _find_git_repo_root(start: Path) -> Path | None:
    """Pi `findGitRepoRoot`: the first ancestor holding a `.git` entry."""

    directory = start
    while True:
        if os.path.exists(directory / ".git"):
            return directory
        parent = directory.parent
        if parent == directory:
            return None
        directory = parent


def _ancestor_agents_skill_dirs(start: Path, *, exclude: Path) -> list[Path]:
    """Pi `collectAncestorAgentsSkillDirs`, minus the user `~/.agents/skills`.

    Lists `<dir>/.agents/skills` from `start` upwards, cwd first, stopping at
    the git repo root (or the filesystem root outside a repository).
    """

    excluded = _canonical_or_self(exclude)
    git_root = _find_git_repo_root(start)
    dirs: list[Path] = []
    directory = start
    while True:
        candidate = directory / AGENTS_DIR_NAME / AGENTS_SKILLS_SUBDIR
        if candidate != exclude and _canonical_or_self(candidate) != excluded:
            dirs.append(candidate)
        if git_root is not None and directory == git_root:
            return dirs
        parent = directory.parent
        if parent == directory:
            return dirs
        directory = parent


def _canonical_or_self(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _screen_resource_candidates(
    source: _ResourceSource,
    seen_paths: set[Path],
) -> Iterator[tuple[Path, Path]]:
    """Resolve candidates and enforce filename safety.

    Every store follows symlinks like Pi: skill layouts ("pi"/"agents") like
    `collectSkillEntries`/`loadSkills`, and the flat template/command stores
    like `loadTemplatesFromDir`, which stats a symlinked file wherever it
    points.
    """

    if source.explicit_file:
        candidates = [source.path]
    elif source.layout == "flat":
        candidates = _iter_md_files(source.path)
    else:
        candidates = _iter_skill_layout_files(source.path, source.layout)
    for candidate in candidates:
        try:
            resolved_candidate = candidate.resolve()
        except OSError:
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
    *,
    skill_discovery: bool = False,
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
    # Pi `loadSkillFromFile` names a skill without a frontmatter `name` after
    # its directory, a `SKILL.md` and a plain `.md` file alike.
    fallback_name = candidate.parent.name if skill_discovery else candidate.stem
    frontmatter = _parse_frontmatter(content, fallback_name=fallback_name)
    name, description, body = (
        frontmatter.name,
        frontmatter.description,
        frontmatter.body,
    )
    return _LoadedResourceCandidate(
        candidate=candidate,
        resolved_path=resolved_candidate,
        name=name,
        description=description,
        body=body,
        byte_length=byte_length,
        head=head,
        truncated=truncated,
        disable_model_invocation=frontmatter.disable_model_invocation,
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
    source: _ResourceSource,
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
            source=source,
            workspace=workspace,
        ),
        name=loaded.name,
        description=loaded.description,
        body=loaded.body,
        sha256=sha256,
        byte_length=loaded.byte_length,
        truncated=loaded.truncated,
        absolute_path=loaded.resolved_path,
        disable_model_invocation=loaded.disable_model_invocation,
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


SKILL_FILE_NAME: str = "SKILL.md"
_SKILL_WALK_SKIPPED_DIR_NAMES: frozenset[str] = frozenset({"node_modules"})


def _iter_skill_layout_files(root: Path, layout: str) -> list[Path]:
    """Port of Pi `collectSkillEntries` for one skills root.

    A directory holding a `SKILL.md` file yields only that file (recursion
    stops). Otherwise names starting with `.` and `node_modules` are skipped,
    subdirectories are walked, and plain `*.md` files are included only at
    the root in "pi" layout or only below the root in "agents" layout.
    `.gitignore`/`.ignore`/`.fdignore` rules found along the way apply
    (`ignore_rules`). Entries are visited in sorted-name order.

    Symlinked subdirectories and files are followed, like Pi, and a directory
    reached through two names is walked under each (its ignore rules and skill
    name depend on the path it was found at); duplicate files are dropped by
    real path later. A directory whose real path is already on the current
    walk path is not entered again, which stops symlink cycles where Pi would
    recurse until the OS refuses the path.
    """

    try:
        if not root.is_dir():
            return []
    except OSError:
        return []
    matcher = IgnoreMatcher()
    files: list[Path] = []
    _collect_skill_entries(
        root,
        root=root,
        layout=layout,
        matcher=matcher,
        ancestors=set(),
        files=files,
    )
    return files


def _collect_skill_entries(
    directory: Path,
    *,
    root: Path,
    layout: str,
    matcher: IgnoreMatcher,
    ancestors: set[Path],
    files: list[Path],
) -> None:

    try:
        real_directory = directory.resolve()
    except OSError:
        return
    if real_directory in ancestors:
        return
    ancestors.add(real_directory)
    try:
        _collect_skill_directory(
            directory,
            root=root,
            layout=layout,
            matcher=matcher,
            ancestors=ancestors,
            files=files,
        )
    finally:
        ancestors.discard(real_directory)


def _collect_skill_directory(
    directory: Path,
    *,
    root: Path,
    layout: str,
    matcher: IgnoreMatcher,
    ancestors: set[Path],
    files: list[Path],
) -> None:
    add_ignore_rules(matcher, directory, root)
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return

    skill_file = directory / SKILL_FILE_NAME
    if any(entry.name == SKILL_FILE_NAME for entry in entries) and _is_file(skill_file):
        if not matcher.ignores(_relative_posix(skill_file, root)):
            files.append(skill_file)
            return

    for entry in entries:
        if entry.name.startswith(".") or entry.name in _SKILL_WALK_SKIPPED_DIR_NAMES:
            continue
        relative = _relative_posix(entry, root)
        is_file = _is_file(entry)
        at_root = directory == root
        if (
            is_file
            and entry.name.endswith(".md")
            and not matcher.ignores(relative)
            and ((layout == "pi" and at_root) or (layout == "agents" and not at_root))
        ):
            files.append(entry)
            continue
        if not _is_dir(entry) or matcher.ignores(f"{relative}/"):
            continue
        _collect_skill_entries(
            entry,
            root=root,
            layout=layout,
            matcher=matcher,
            ancestors=ancestors,
            files=files,
        )


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _path_label_for(
    *,
    candidate: Path,
    source: _ResourceSource,
    workspace: Path,
) -> str:
    """Compute the workspace-relative or `<prefix>`-labelled POSIX label.

    Labels never carry an absolute on-disk path. Non-workspace sources are
    labelled `<prefix>/<source dir name>/<path within the source>`, which is
    `<prefix>/<subdir>/<filename>` for flat stores. Directory names are
    sanitized (control bytes stripped) before they enter the label; the
    filename itself was already screened by `_candidate_name_is_safe`.
    """

    prefix = _LABEL_PREFIXES.get(source.kind)
    if prefix is not None:
        root = source.path.parent if source.explicit_file else source.path
        try:
            inner_parts = candidate.relative_to(root).parts
        except ValueError:
            inner_parts = (candidate.name,)
        fallback = "package" if source.kind == "package" else "resource"
        parts = [_sanitize_label(root.name) or fallback]
        parts.extend(_sanitize_label(part) or "_" for part in inner_parts[:-1])
        parts.append(candidate.name)
        return prefix + "/".join(parts)
    # Label the path the file was found at, not its symlink target, so a
    # project skill symlinked out of the workspace keeps its in-tree label.
    try:
        relative = candidate.relative_to(workspace)
        return relative.as_posix()
    except ValueError:
        pass
    try:
        return Path(os.path.relpath(candidate, workspace)).as_posix()
    except ValueError:
        return candidate.name


USER_AGENTS_PATH_LABEL_PREFIX: str = "<user-agents>/"
_LABEL_PREFIXES: dict[str, str] = {
    "global": GLOBAL_PATH_LABEL_PREFIX,
    "package": PACKAGE_PATH_LABEL_PREFIX,
    "cli": CLI_PATH_LABEL_PREFIX,
    "user-agents": USER_AGENTS_PATH_LABEL_PREFIX,
}


@dataclass(frozen=True, slots=True)
class _Frontmatter:
    name: str
    description: str
    body: str
    disable_model_invocation: bool = False


# YAML 1.2 core-schema booleans (Pi parses frontmatter with `yaml`); a quoted
# "true" is a string and does not disable model invocation.
_YAML_TRUE: frozenset[str] = frozenset({"true", "True", "TRUE"})


def _parse_frontmatter(
    content: str,
    *,
    fallback_name: str,
) -> _Frontmatter:
    """Return the name, description, body and flags extracted from `content`.

    The frontmatter is the block delimited by a leading line equal to
    `---` and a trailing line equal to `---`. Only `key: value` lines
    are honored; everything else inside the block is ignored. The
    parser recognizes `name`, `description` and Pi's boolean
    `disable-model-invocation`. When no
    frontmatter is present, the body is the full content,
    `name` falls back to `fallback_name`, and `description` is empty.

    The parser is intentionally small and stdlib-only: a real YAML
    parser is out of scope for this slice and would require a runtime
    dependency.
    """

    fallback_name = _sanitize_label(fallback_name) or fallback_name
    lines = content.splitlines(keepends=False)
    if not lines or lines[0].rstrip("\r") != "---":
        return _Frontmatter(fallback_name, "", content)
    end_index = _frontmatter_end_index(lines)
    if end_index is None:
        return _Frontmatter(fallback_name, "", content)
    fields = _frontmatter_top_level_fields(lines[1:end_index])
    name, description = _parse_frontmatter_fields(fields, fallback_name=fallback_name)
    body = _reconstruct_frontmatter_body(content, lines[end_index + 1 :])
    disabled = _yaml_flag_is_true(lines[1:end_index], "disable-model-invocation")
    return _Frontmatter(name, description, body, disabled)


def _yaml_flag_is_true(lines: list[str], key: str) -> bool:
    """Whether top-level ``key`` is the YAML boolean ``true`` (the last one wins).

    A plain scalar may continue on indented lines; a block scalar (``|``/``>``)
    or a quoted value is a string, so it never counts.
    """

    value: bool = False
    for index, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r")
        if line[:1] in (" ", "\t") or ":" not in line:
            continue
        name, _, rest = line.partition(":")
        if name.strip().lower() != key:
            continue
        rest = _TRAILING_COMMENT.sub("", rest.strip())
        parts = [rest] if rest else []
        # The plain scalar continues on indented lines, across blank and
        # comment-only ones.
        for follow in lines[index + 1 :]:
            follow = follow.rstrip("\r")
            if not follow.strip() or follow.lstrip().startswith("#"):
                continue
            if follow[:1] not in (" ", "\t"):
                break
            parts.append(_TRAILING_COMMENT.sub("", follow.strip()))
        value = " ".join(parts) in _YAML_TRUE
    return value


def _frontmatter_end_index(lines: list[str]) -> int | None:
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r") == "---":
            return index
    return None


def _parse_frontmatter_fields(
    top_level_fields: list[tuple[str, str]],
    *,
    fallback_name: str,
) -> tuple[str, str]:
    """Parse the supported frontmatter label fields (`name`, `description`).

    Top-level `key: value` lines are honoured. Indented lines that follow a
    key continue its value, so YAML block scalars (`description: >-` /
    `|`) and multi-line plain scalars keep their text. Labels are
    single-line, so continuation lines are joined with spaces (and then
    whitespace-collapsed by `_sanitize_label`).
    """

    fields: dict[str, str] = {}
    for key, value in top_level_fields:
        if key in ("name", "description"):
            fields[key] = _sanitize_label(_unquote(value))
    name = fields.get("name") or fallback_name
    return name, fields.get("description", "")


_BLOCK_SCALAR_INDICATOR = re.compile(r"^[|>][+-]?[0-9]?[+-]?(?:\s+#.*)?$")
# A YAML comment on an unquoted value starts at whitespace followed by `#`.
_TRAILING_COMMENT = re.compile(r"\s+#.*$")


def _frontmatter_top_level_fields(lines: list[str]) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    current_key: str | None = None
    parts: list[str] = []

    def flush() -> None:
        if current_key is not None:
            fields.append((current_key, " ".join(part for part in parts if part)))

    for raw_line in lines:
        line = raw_line.rstrip("\r")
        if line[:1] in (" ", "\t"):
            if current_key is not None:
                parts.append(line.strip())
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        flush()
        if ":" not in line:
            current_key, parts = None, []
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        current_key = key.strip().lower()
        if not value.startswith(("'", '"')):
            value = _TRAILING_COMMENT.sub("", value)
        parts = [] if _BLOCK_SCALAR_INDICATOR.match(value) else [value]
    flush()
    return fields


def _unquote(value: str) -> str:
    if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
        return value[1:-1]
    return value


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
