"""Package resource resolution (local paths plus managed git cache).

The package-manager CLI (`pipy_harness.native.package_manager`) records
local-path and git package sources in the layered settings system. This
module is the *read* side: it turns those configured sources into per-kind
resource roots that the existing discovery boundaries
(`pipy_harness.native._resource_files`, `.extensions`, and the theme
registry) consume at lowest precedence.

A package is a directory on disk. Its resources are declared by an
optional `pipy-package.toml` manifest `[resources]` table (mapping Pi's
`package.json` `pi.{extensions,skills,prompts,themes}` capability) or,
when that table is omitted or no manifest is present, by conventional
subdirectories `extensions/`, `skills/`, `prompts/`, and `themes/`.

A configured source may be a bare source string or an object-form entry
``{source, extensions, skills, prompts, themes}`` (Pi's `PackageSource`),
where each list is a Pi-shaped `+pattern`/`-pattern` filter scoping that
*one package's* resources of that kind. Those per-package filters are
carried on each resolved `PackageRoot` and applied by the discovery
boundary in addition to the global `pipy config` filters.

This boundary never imports or executes package code. It only stats
directories and reads the manifest as data with `tomllib`. Git sources
resolve only when an install/update command has already populated the
managed cache; startup never clones. Unsupported remote, missing, or
containment-escaping sources fail closed with a safe diagnostic and
contribute nothing. The only data intended to reach the metadata archive
is the `PackageInfo` projection: a safe package name, a `<package>/...`
path label that never embeds the absolute source path, a status, and a
reason code.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pipy_harness.native._resource_files import _sanitize_label
from pipy_harness.native.package_manager import (
    _is_remote_source,
    cached_git_source_path,
    canonical_local_source,
    parse_git_source,
)
from pipy_harness.native.settings import resolve_config_home
from pipy_harness.native.settings import (
    PACKAGE_ENTRY_SCOPE_KEY,
    SCOPE_GLOBAL,
    SCOPE_PROJECT,
)

#: Manifest filename a package may use to declare its resource dirs.
PACKAGE_MANIFEST_FILENAME = "pipy-package.toml"

#: Resource kinds, in the order they appear in the manifest `[resources]`
#: table and as convention subdirectory names.
RESOURCE_KINDS: tuple[str, ...] = ("extensions", "skills", "prompts", "themes")

# Per-package status reason codes (archive-safe labels).
REASON_REMOTE_SOURCE = "remote_source"
REASON_MISSING_SOURCE = "missing_source"
REASON_INVALID_MANIFEST = "invalid_manifest"

_PACKAGE_LABEL_PREFIX = "<package>/"


@dataclass(frozen=True, slots=True)
class PackageRoot:
    """A concrete package resource directory plus its per-package filters.

    `filters` are the Pi-shaped `+pattern`/`-pattern` entries scoping the
    *contributing package's* resources of this kind (from an object-form
    `PackageSource`). An empty tuple means the package contributes all of
    its resources of that kind (subject to the global filters applied
    later). The filter is applied by name at the discovery boundary.
    """

    path: Path
    filters: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PackageInfo:
    """Archive-safe metadata for one resolved package source.

    `name` is the manifest `name` or the source directory's basename.
    `path_label` is a `<package>/<name>` label that never embeds the
    absolute source path. `status` is ``"loaded"`` or ``"disabled"``;
    `reason` is a safe reason code (empty for a loaded package).
    """

    name: str
    path_label: str
    status: str
    reason: str


@dataclass(frozen=True, slots=True)
class PackageResourceRoots:
    """Per-kind resource roots contributed by configured packages.

    Each tuple lists `PackageRoot`s in the order they should be searched —
    package roots come *after* all workspace/global roots, and project
    packages come before user packages. `packages` carries safe
    per-package metadata; `diagnostics` carries safe human-readable
    notes about skipped sources or dirs.
    """

    extensions: tuple[PackageRoot, ...]
    skills: tuple[PackageRoot, ...]
    prompts: tuple[PackageRoot, ...]
    themes: tuple[PackageRoot, ...]
    packages: tuple[PackageInfo, ...]
    diagnostics: tuple[str, ...]

    @classmethod
    def empty(cls) -> "PackageResourceRoots":
        return cls((), (), (), (), (), ())

    def roots_for(self, kind: str) -> tuple[PackageRoot, ...]:
        """Return the contributed roots for one resource `kind`."""

        return {
            "extensions": self.extensions,
            "skills": self.skills,
            "prompts": self.prompts,
            "themes": self.themes,
        }[kind]


@dataclass(frozen=True, slots=True)
class _PackageSpec:
    """A normalized configured source: its string plus per-kind filters."""

    source: str
    filters: dict[str, tuple[str, ...]] = field(default_factory=dict)
    scope: str | None = None

    def filter_for(self, kind: str) -> tuple[str, ...]:
        return self.filters.get(kind, ())


def _normalize_entry(entry: object) -> _PackageSpec | None:
    """Normalize a configured `packages` entry to a `_PackageSpec`.

    A bare string source has no per-package filters. An object-form entry
    ``{source, extensions, skills, prompts, themes}`` carries a Pi-shaped
    filter list per kind. An entry without a usable source is dropped.
    """

    if isinstance(entry, str):
        # An empty/whitespace source would resolve to the workspace root
        # (`Path("")` -> `.`) and fail open — reject it before resolution.
        if not entry.strip():
            return None
        return _PackageSpec(source=entry)
    if isinstance(entry, Mapping):
        source = entry.get("source")
        if not isinstance(source, str) or not source.strip():
            return None
        raw_scope = entry.get(PACKAGE_ENTRY_SCOPE_KEY)
        scope = raw_scope if raw_scope in (SCOPE_PROJECT, SCOPE_GLOBAL) else None
        filters: dict[str, tuple[str, ...]] = {}
        for kind in RESOURCE_KINDS:
            value = entry.get(kind)
            if isinstance(value, str):
                filters[kind] = (value,)
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                filters[kind] = tuple(item for item in value if isinstance(item, str))
        return _PackageSpec(source=source, filters=filters, scope=scope)
    return None


def resolve_package_roots(
    sources: Sequence[object],
    workspace_root: Path,
) -> PackageResourceRoots:
    """Resolve configured package `sources` into resource roots.

    `sources` is the flattened, ordered list of configured package
    entries (project scope first, then user scope). Each entry is a bare
    source string or an object-form `PackageSource`
    ``{source, extensions, skills, prompts, themes}`` whose lists are
    per-package `+/-pattern` filters. A source is resolved once even when
    it appears more than once. Each resolved package contributes the
    resource dirs declared by its `pipy-package.toml` `[resources]` table,
    or — when that table is omitted or there is no manifest at all — the
    conventional subdirectories that exist; each contributed `PackageRoot`
    carries that package's per-kind filter.

    Git sources resolve from pipy's managed package cache when an install or
    update command has already populated it. Unsupported remote sources,
    missing sources/caches, and an invalid manifest fail closed: the package
    is recorded as ``disabled`` with a safe reason and contributes no roots.
    A declared/convention dir that does not exist, is not a directory, or
    escapes the package directory is silently skipped with a safe diagnostic.
    """

    per_kind: dict[str, list[PackageRoot]] = {kind: [] for kind in RESOURCE_KINDS}
    packages: list[PackageInfo] = []
    diagnostics: list[str] = []
    seen_sources: set[Path] = set()

    for entry in sources:
        spec = _normalize_entry(entry)
        if spec is None:
            continue
        source = spec.source

        git_source = parse_git_source(source)
        if git_source is not None:
            resolved = _cached_git_path(source, workspace_root, spec.scope)
            if resolved is None or not resolved.is_dir():
                name = _safe_name_from_source(f"{git_source.host}/{git_source.path}")
                packages.append(
                    PackageInfo(
                        name, _label_for(name), "disabled", REASON_MISSING_SOURCE
                    )
                )
                diagnostics.append(f"package git cache not found: {name}")
                continue
        elif _is_remote_source(source):
            name = _safe_name_from_source(source)
            packages.append(
                PackageInfo(name, _label_for(name), "disabled", REASON_REMOTE_SOURCE)
            )
            diagnostics.append(f"package source is not a local path: {name}")
            continue
        else:
            resolved = canonical_local_source(source, workspace_root)
            if resolved is None or not resolved.is_dir():
                name = _safe_name_from_source(source)
                packages.append(
                    PackageInfo(
                        name, _label_for(name), "disabled", REASON_MISSING_SOURCE
                    )
                )
                diagnostics.append(f"package source not found: {name}")
                continue

        if resolved in seen_sources:
            continue
        seen_sources.add(resolved)

        try:
            manifest = _load_manifest(resolved)
        except _InvalidManifest:
            name = _safe_name_from_source(source)
            packages.append(
                PackageInfo(name, _label_for(name), "disabled", REASON_INVALID_MANIFEST)
            )
            diagnostics.append(f"package manifest is invalid: {name}")
            continue

        name = _package_name(manifest, resolved)
        declared = _declared_dirs(manifest)
        for kind in RESOURCE_KINDS:
            for relative in declared[kind]:
                root = _safe_resource_dir(resolved, relative)
                if root is None:
                    diagnostics.append(
                        f"package {name}: skipped {kind} dir {relative!r}"
                    )
                    continue
                per_kind[kind].append(PackageRoot(root, spec.filter_for(kind)))
        packages.append(PackageInfo(name, _label_for(name), "loaded", ""))

    return PackageResourceRoots(
        extensions=tuple(per_kind["extensions"]),
        skills=tuple(per_kind["skills"]),
        prompts=tuple(per_kind["prompts"]),
        themes=tuple(per_kind["themes"]),
        packages=tuple(packages),
        diagnostics=tuple(diagnostics),
    )


class _InvalidManifest(Exception):
    """A `pipy-package.toml` is present but cannot be used (fail closed)."""


def _load_manifest(package_dir: Path) -> Mapping[str, object] | None:
    """Load `pipy-package.toml` as data.

    Returns ``None`` when no manifest exists (convention fallback) or the
    parsed mapping otherwise. Raises `_InvalidManifest` when a manifest is
    present but cannot be parsed as a TOML table. The manifest must be a
    regular file resolving inside the package dir; a symlink that escapes
    is treated as invalid.
    """

    manifest_path = package_dir / PACKAGE_MANIFEST_FILENAME
    try:
        present = manifest_path.is_symlink() or manifest_path.exists()
    except OSError:
        return None
    if not present:
        return None
    try:
        if not manifest_path.is_file():
            raise _InvalidManifest
        resolved = manifest_path.resolve()
        resolved.relative_to(package_dir.resolve())
    except (OSError, ValueError) as exc:
        raise _InvalidManifest from exc
    try:
        with resolved.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        raise _InvalidManifest from exc
    if not isinstance(data, Mapping):
        raise _InvalidManifest
    return data


def _declared_dirs(manifest: Mapping[str, object] | None) -> dict[str, list[str]]:
    """Return the relative resource dirs per kind.

    The `[resources]` table is optional. When it is absent — whether
    because there is no manifest at all or the manifest carries only
    metadata like `name`/`version` — every kind falls back to its
    convention subdir name (Pi-style auto-discovery). When a `[resources]`
    table *is* present, it is the explicit declaration: each kind uses the
    dirs it lists, and a kind absent from the table contributes nothing.
    """

    convention = {kind: [kind] for kind in RESOURCE_KINDS}
    if manifest is None:
        return convention
    resources = manifest.get("resources")
    if not isinstance(resources, Mapping):
        # Manifest present but no [resources] table → convention fallback.
        return convention
    declared: dict[str, list[str]] = {}
    for kind in RESOURCE_KINDS:
        value = resources.get(kind)
        if isinstance(value, str):
            declared[kind] = [value]
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            declared[kind] = [item for item in value if isinstance(item, str)]
        else:
            declared[kind] = []
    return declared


def _safe_resource_dir(package_dir: Path, relative: str) -> Path | None:
    """Resolve a declared resource dir, requiring containment.

    Returns the resolved directory when it exists, is a directory, and
    resolves inside `package_dir`. Returns ``None`` otherwise (escaping
    path, missing dir, not a directory, or OS error).
    """

    if not relative or Path(relative).is_absolute():
        return None
    candidate = package_dir / relative
    try:
        resolved = candidate.resolve()
        resolved.relative_to(package_dir.resolve())
    except (OSError, ValueError):
        return None
    try:
        if not resolved.is_dir():
            return None
    except OSError:
        return None
    return resolved


def _cached_git_path(source: str, workspace_root: Path, scope: str | None) -> Path | None:
    """Return an installed git cache path for `source`.

    Runtime settings entries carry their originating scope so a user/global git
    package cannot be shadowed by a same-source project cache. Direct callers
    that pass plain sources keep the historical project-then-user fallback.
    Runtime resolution never clones or fetches.
    """

    config_home = resolve_config_home()
    if scope == SCOPE_PROJECT:
        return cached_git_source_path(
            source,
            workspace_root=workspace_root,
            config_home=config_home,
            local=True,
        )
    if scope == SCOPE_GLOBAL:
        return cached_git_source_path(
            source,
            workspace_root=workspace_root,
            config_home=config_home,
            local=False,
        )
    project = cached_git_source_path(
        source,
        workspace_root=workspace_root,
        config_home=config_home,
        local=True,
    )
    if project is not None:
        return project
    return cached_git_source_path(
        source,
        workspace_root=workspace_root,
        config_home=config_home,
        local=False,
    )


def _safe_label_component(value: str) -> str | None:
    """A single archive-safe label component, or ``None`` if unusable.

    Strips control characters (via `_sanitize_label`) and additionally
    rejects path separators and parent/self references so a manifest
    `name` can never produce a misleading `<package>/../...` label.
    """

    sanitized = _sanitize_label(value)
    if not sanitized or "/" in sanitized or "\\" in sanitized:
        return None
    if sanitized in (".", ".."):
        return None
    return sanitized


def _package_name(manifest: Mapping[str, object] | None, package_dir: Path) -> str:
    if isinstance(manifest, Mapping):
        raw = manifest.get("name")
        if isinstance(raw, str):
            safe = _safe_label_component(raw)
            if safe is not None:
                return safe
    # A resolved directory's basename is a single path component, so it can
    # never contain a separator; it only needs control-character stripping.
    return _safe_label_component(package_dir.name) or "package"


def _safe_name_from_source(source: str) -> str:
    """A safe display name for a source that never resolved to a dir.

    Used only for rejected remote / missing sources. A manually configured
    remote source can carry secrets in its userinfo, query, or fragment
    (`https://user:token@host/pkg?token=...`), so those are stripped before
    a hint is derived — only a final, credential-free path segment can reach
    a name, label, or diagnostic. Falls back to a generic ``"package"``.
    """

    cleaned = source.strip().split("?", 1)[0].split("#", 1)[0]
    if "://" in cleaned:  # drop the scheme of a URL source
        cleaned = cleaned.split("://", 1)[1]
    if "@" in cleaned:  # drop userinfo (user:token@host)
        cleaned = cleaned.split("@", 1)[1]
    if ":" in cleaned:  # drop a leading scheme prefix (git:/npm:) or host:port
        cleaned = cleaned.split(":", 1)[1]
    basename = cleaned.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return _safe_label_component(basename) or "package"


def _label_for(name: str) -> str:
    return f"{_PACKAGE_LABEL_PREFIX}{name}"
