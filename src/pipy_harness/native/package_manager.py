"""Local-path extension package manager (slice 12).

Pipy's package manager records Pi-shaped package sources and resource
enable/disable filters in the layered settings system
(`pipy_harness.native.settings`). This slice supports **local-path
package sources** only: a directory or file on disk recorded in the
chosen settings scope. Git / PyPI sources stay out until a supply-chain
policy is written; no package lifecycle scripts ever run.

Settings representation (matching `docs/extension-api.md`):

- a top-level `packages` array of source strings per settings scope
  (user `<config>/settings.json`, project `<cwd>/.pipy/settings.json`);
- resource enablement uses Pi-shaped `+pattern` / `-pattern` entries in
  the `extensions` / `skills` / `prompts` / `themes` arrays — enable and
  disable are *filters*, never deletions of discovered resources.

The functions here are pure-ish settings mutations (read → modify →
atomic write) reused by the `pipy install/remove/uninstall/list/config`
CLI surface. No source path, command output, or resource body crosses
into the metadata archive.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native.settings import _atomic_write_json

PACKAGES_KEY = "packages"
RESOURCE_KINDS: tuple[str, ...] = ("extensions", "skills", "prompts", "themes")

# Remote / non-local source classification. A bare scheme prefix (`git:`,
# `git+`, `npm:`) or any `<scheme>://` URL (http(s), ssh, git, file, ...) is a
# remote source kind that stays out until a supply-chain policy is written. The
# screen is case-insensitive and ignores surrounding whitespace so trivially
# disguised remote sources (`GIT:foo`, `  https://...`) cannot slip through as
# local paths.
_SCHEME_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://")
_REMOTE_SCHEME_PREFIXES: tuple[str, ...] = ("git:", "git+", "npm:")


def _is_remote_source(source: str) -> bool:
    normalized = source.strip().lower()
    if _SCHEME_URL_RE.match(normalized):
        return True
    return any(normalized.startswith(prefix) for prefix in _REMOTE_SCHEME_PREFIXES)


def _source_of(entry: object) -> str | None:
    """The source string of a package entry (string or `{source, ...}` object)."""

    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        source = entry.get("source")
        return source if isinstance(source, str) else None
    return None


@dataclass(frozen=True, slots=True)
class PackageList:
    """The configured package sources per settings scope."""

    user: tuple[str, ...]
    project: tuple[str, ...]


class PackageSettingsError(RuntimeError):
    """A settings file exists but could not be read for a package write.

    Raised by the mutating helpers so a corrupt settings file is never silently
    overwritten (mirroring `SettingsManager`'s clobber-refusal). The CLI turns
    this into a non-zero exit with a diagnostic.
    """


def _read_settings(path: Path) -> dict:
    """Lenient read for display paths: missing or invalid file → ``{}``."""

    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_settings_for_write(path: Path) -> dict:
    """Strict read for mutating paths.

    A missing file yields an empty document. A file that exists but does not
    parse as a JSON object raises `PackageSettingsError` so the caller refuses
    to clobber unreadable user data.
    """

    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise PackageSettingsError(f"cannot read {path}: {exc}") from exc
    except ValueError as exc:
        raise PackageSettingsError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PackageSettingsError(f"{path} is not a JSON object")
    return data


def _raw_packages(data: dict) -> list:
    """The raw `packages` entries (string sources and `{source, ...}` objects).

    Entries without a resolvable source string are dropped, but valid
    object-form `PackageSource` entries are preserved verbatim so per-package
    resource filters survive a string-source install/remove.
    """

    raw = data.get(PACKAGES_KEY)
    if not isinstance(raw, list):
        return []
    return [item for item in raw if _source_of(item) is not None]


def _package_sources(data: dict) -> list[str]:
    sources: list[str] = []
    for item in _raw_packages(data):
        source = _source_of(item)
        if source is not None:
            sources.append(source)
    return sources


def install_package(source: str, settings_path: Path) -> str:
    """Record `source` in the `packages` array of `settings_path`.

    The source string is stored verbatim (deduplicated by source). Existing
    object-form entries are preserved. Returns the Pi-shaped `Installed
    <source>` message.
    """

    data = _read_settings_for_write(settings_path)
    packages = _raw_packages(data)
    if source not in (_source_of(item) for item in packages):
        packages.append(source)
    data[PACKAGES_KEY] = packages
    _atomic_write_json(settings_path, data)
    return f"Installed {source}"


def remove_package(source: str, settings_path: Path) -> str | None:
    """Remove the entry whose source is `source` from `settings_path`.

    Matches both string sources and `{source, ...}` objects by their source
    string. Returns the Pi-shaped `Removed <source>` message, or `None` when
    the source is not configured in this scope (the caller exits non-zero).
    """

    data = _read_settings_for_write(settings_path)
    packages = _raw_packages(data)
    if source not in (_source_of(item) for item in packages):
        return None
    data[PACKAGES_KEY] = [item for item in packages if _source_of(item) != source]
    _atomic_write_json(settings_path, data)
    return f"Removed {source}"


def list_packages(*, user_path: Path, project_path: Path | None) -> PackageList:
    """Return the configured user and project package sources."""

    user = tuple(_package_sources(_read_settings(user_path)))
    project = (
        tuple(_package_sources(_read_settings(project_path)))
        if project_path is not None
        else ()
    )
    return PackageList(user=user, project=project)


def format_package_listing(packages: PackageList) -> str:
    """Format `list_packages` output. Empty config prints Pi's dim message."""

    if not packages.user and not packages.project:
        return "No packages installed."
    lines: list[str] = []
    if packages.user:
        lines.append("user:")
        lines.extend(f"  {source}" for source in packages.user)
    if packages.project:
        lines.append("project:")
        lines.extend(f"  {source}" for source in packages.project)
    return "\n".join(lines)


def configure_resource_filter(
    *,
    settings_path: Path,
    kind: str,
    pattern: str,
    enable: bool,
) -> None:
    """Write a Pi-shaped `+pattern` / `-pattern` filter to a resource array.

    `kind` is one of `extensions` / `skills` / `prompts` / `themes`. The
    opposite token and any duplicate of the new token are removed first,
    so toggling a pattern flips its sign in place. This edits *filters*,
    never the discovered resource files.
    """

    if kind not in RESOURCE_KINDS:
        raise ValueError(f"unknown resource kind: {kind!r}")
    # Reuse the canonical Pi-shaped pattern logic so package config and
    # `pipy config` write filters identically.
    from pipy_harness.native.resource_enablement import disable_entry, enable_entry

    data = _read_settings_for_write(settings_path)
    raw = data.get(kind)
    entries = [item for item in raw if isinstance(item, str)] if isinstance(raw, list) else []
    entries = enable_entry(entries, pattern) if enable else disable_entry(entries, pattern)
    data[kind] = entries
    _atomic_write_json(settings_path, data)


def resource_filters(settings_path: Path, kind: str) -> tuple[str, ...]:
    """Return the configured `+pattern` / `-pattern` filters for `kind`."""

    data = _read_settings(settings_path)
    raw = data.get(kind)
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in raw if isinstance(item, str))


def canonical_local_source(source: str, workspace_root: Path | None) -> Path | None:
    """Resolve a local-path source, requiring it to exist.

    A relative source resolves against `workspace_root` (project ops) or
    the current directory. Returns the resolved path, or `None` when the
    source does not exist (the caller fails closed). Git / PyPI sources
    (`git:` / `git+` / URLs) are not local paths and return `None`.
    """

    if _is_remote_source(source):
        return None
    candidate = Path(source).expanduser()
    if not candidate.is_absolute() and workspace_root is not None:
        candidate = workspace_root / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    return resolved if resolved.exists() else None


def is_local_path_source(source: str) -> bool:
    """Whether `source` is a (supported) local-path source, not git/PyPI/URL."""

    return not _is_remote_source(source)


def configured_packages(paths: Sequence[Path]) -> list[str]:
    """Flatten the configured package sources across the given settings paths."""

    out: list[str] = []
    for path in paths:
        out.extend(_package_sources(_read_settings(path)))
    return out
