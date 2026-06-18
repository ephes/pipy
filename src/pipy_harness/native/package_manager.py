"""Extension package manager (local paths plus managed git sources).

Pipy's package manager records Pi-shaped package sources and resource
enable/disable filters in the layered settings system
(`pipy_harness.native.settings`). Local-path sources are recorded directly.
Git sources are cloned into a pipy-owned package cache and then resolved
from that cache. PyPI / npm-style sources stay out until a broader
supply-chain policy is written; no package lifecycle scripts ever run.

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
import os
import shutil
import subprocess
import urllib.parse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native.settings import _atomic_write_json

PACKAGES_KEY = "packages"
RESOURCE_KINDS: tuple[str, ...] = ("extensions", "skills", "prompts", "themes")
GIT_COMMAND_TIMEOUT_SECONDS = 10

# Remote / non-local source classification. Git sources are supported through a
# managed cache; npm / PyPI / arbitrary URL schemes are still rejected. On
# POSIX, colon-leading relative paths must be written with `./` to avoid looking
# like deferred package-source schemes.
_SCHEME_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://")
_SCHEME_TOKEN_RE = re.compile(r"^[a-z][a-z0-9+.\-]*:")
_REMOTE_SCHEME_PREFIXES: tuple[str, ...] = ("git:", "git+", "npm:")
_SUPPORTED_GIT_URL_SCHEMES = {"http", "https", "ssh", "git", "file"}
_UNSUPPORTED_REMOTE_PREFIXES: tuple[str, ...] = ("git+", "npm:")
_UNSUPPORTED_REMOTE_SCHEMES = {"npm", "pypi"}


def _is_remote_source(source: str) -> bool:
    normalized = source.strip().lower()
    if _SCHEME_TOKEN_RE.match(normalized):
        return True
    return any(normalized.startswith(prefix) for prefix in _REMOTE_SCHEME_PREFIXES)


def _is_unsupported_remote_source(source: str) -> bool:
    normalized = source.strip().lower()
    if any(normalized.startswith(prefix) for prefix in _UNSUPPORTED_REMOTE_PREFIXES):
        return True
    if normalized.startswith("pypi:"):
        return True
    match = _SCHEME_URL_RE.match(normalized)
    if match:
        scheme = normalized.split(":", 1)[0]
        return scheme not in _SUPPORTED_GIT_URL_SCHEMES
    token_match = _SCHEME_TOKEN_RE.match(normalized)
    if token_match:
        scheme = normalized.split(":", 1)[0]
        return scheme not in {"git"}
    return False


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


@dataclass(frozen=True, slots=True)
class GitPackageSource:
    """A supported git package source.

    `repo` is the clone URL without the optional ref suffix. `host` and `path`
    are safe cache path components. `ref` is an optional branch/tag/commit.
    """

    repo: str
    host: str
    path: str
    ref: str | None = None

    @property
    def pinned(self) -> bool:
        return self.ref is not None


class PackageSettingsError(RuntimeError):
    """A settings file exists but could not be read for a package write.

    Raised by the mutating helpers so a corrupt settings file is never silently
    overwritten (mirroring `SettingsManager`'s clobber-refusal). The CLI turns
    this into a non-zero exit with a diagnostic.
    """


class PackageSourceError(ValueError):
    """The requested package source cannot be installed or updated safely."""


class PackageCommandError(RuntimeError):
    """A package install/update/remove subprocess failed."""


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


def install_package_source(
    source: str,
    settings_path: Path,
    *,
    workspace_root: Path | None,
    config_home: Path,
    local: bool,
) -> str:
    """Install/verify `source`, then persist it in settings.

    Local paths must exist. Git sources are cloned or reconciled in the
    managed cache for the requested scope. Unsupported remote sources fail
    closed before settings are written.
    """

    git_source = parse_git_source(source)
    if git_source is not None:
        install_git_package(
            git_source,
            workspace_root=workspace_root,
            config_home=config_home,
            local=local,
        )
    elif _is_unsupported_remote_source(source):
        raise PackageSourceError(f"unsupported package source: {source}")
    elif is_local_path_source(source):
        if canonical_local_source(source, workspace_root if local else None) is None:
            raise PackageSourceError(f"package source not found: {source}")
    else:
        raise PackageSourceError(f"unsupported package source: {source}")
    return install_package(source, settings_path)


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


def remove_package_source(
    source: str,
    settings_path: Path,
    *,
    workspace_root: Path | None,
    config_home: Path,
    local: bool,
) -> str | None:
    """Remove a configured package source and any managed git cache."""

    message = remove_package(source, settings_path)
    if message is None:
        return None
    git_source = parse_git_source(source)
    if git_source is not None:
        remove_git_package(
            git_source,
            workspace_root=workspace_root,
            config_home=config_home,
            local=local,
        )
    return message


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
    source does not exist (the caller fails closed). Git / PyPI sources are
    not local paths and return `None`.
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


def parse_git_source(source: str) -> GitPackageSource | None:
    """Parse a supported git package source, or return ``None``.

    Supported inputs mirror the safe subset of Pi's package sources:
    `git:<host>/<owner>/<repo>`, `git:<url>`, explicit `https://`, `ssh://`,
    `git://`, `file://`, and `git@host:owner/repo` when carried behind the
    `git:` prefix. A ref may be expressed as `@ref` after the repo path.
    """

    raw = source.strip()
    if not raw:
        return None
    lower = raw.lower()
    has_git_prefix = lower.startswith("git:")
    candidate = raw[4:].strip() if has_git_prefix else raw
    if not has_git_prefix and not _explicit_git_url(candidate):
        return None
    repo, ref = _split_git_ref(candidate)
    parsed = _parse_git_repo(repo, allow_shorthand=has_git_prefix)
    if parsed is None:
        return None
    clone_url, host, path = parsed
    if not _safe_git_component(host, allow_slash=False):
        return None
    if not _safe_git_component(path, allow_slash=True):
        return None
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return None
    normalized_path = "/".join(parts)
    if normalized_path.endswith(".git"):
        normalized_path = normalized_path[:-4]
    return GitPackageSource(
        repo=clone_url,
        host=host.lower(),
        path=normalized_path,
        ref=ref or None,
    )


def is_supported_package_source(source: str) -> bool:
    """Whether `source` is currently installable by pipy."""

    return is_local_path_source(source) or parse_git_source(source) is not None


def git_cache_path(
    git_source: GitPackageSource,
    *,
    workspace_root: Path | None,
    config_home: Path,
    local: bool,
) -> Path:
    """Return the contained managed cache path for a git package."""

    root = _git_cache_root(workspace_root=workspace_root, config_home=config_home, local=local)
    return _contained_path(root, (git_source.host, *git_source.path.split("/")))


def install_git_package(
    git_source: GitPackageSource,
    *,
    workspace_root: Path | None,
    config_home: Path,
    local: bool,
) -> Path:
    """Clone or reconcile a git package into the managed cache."""

    target = git_cache_path(
        git_source,
        workspace_root=workspace_root,
        config_home=config_home,
        local=local,
    )
    root = _git_cache_root(workspace_root=workspace_root, config_home=config_home, local=local)
    _ensure_cache_root(root)
    if target.exists():
        update_git_package(
            git_source,
            workspace_root=workspace_root,
            config_home=config_home,
            local=local,
        )
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    _run_git(["clone", git_source.repo, str(target)], cwd=None)
    if git_source.ref:
        _run_git(["checkout", git_source.ref], cwd=target)
    return target


def update_git_package(
    git_source: GitPackageSource,
    *,
    workspace_root: Path | None,
    config_home: Path,
    local: bool,
) -> Path:
    """Fetch/reset an existing managed git package, cloning if missing."""

    target = git_cache_path(
        git_source,
        workspace_root=workspace_root,
        config_home=config_home,
        local=local,
    )
    if not target.exists():
        return install_git_package(
            git_source,
            workspace_root=workspace_root,
            config_home=config_home,
            local=local,
        )
    if not (target / ".git").exists():
        raise PackageCommandError(f"managed git cache is not a git checkout: {target}")
    if git_source.ref:
        _run_git(["fetch", "--prune", "--no-tags", "origin", git_source.ref], cwd=target)
        target_ref = "FETCH_HEAD"
    else:
        _run_git(["fetch", "--prune", "--no-tags", "origin"], cwd=target)
        target_ref = _current_upstream_ref(target) or "origin/HEAD"
    _run_git(["reset", "--hard", f"{target_ref}^{{commit}}"], cwd=target)
    _run_git(["clean", "-fdx"], cwd=target)
    return target


def remove_git_package(
    git_source: GitPackageSource,
    *,
    workspace_root: Path | None,
    config_home: Path,
    local: bool,
) -> None:
    """Remove a managed git cache path and prune empty parent dirs."""

    target = git_cache_path(
        git_source,
        workspace_root=workspace_root,
        config_home=config_home,
        local=local,
    )
    root = _git_cache_root(workspace_root=workspace_root, config_home=config_home, local=local)
    if target.exists():
        shutil.rmtree(target)
    _prune_empty_parents(target.parent, root)


def cached_git_source_path(
    source: str,
    *,
    workspace_root: Path,
    config_home: Path,
    local: bool,
) -> Path | None:
    """Resolve a configured git source to its installed cache path if present."""

    git_source = parse_git_source(source)
    if git_source is None:
        return None
    path = git_cache_path(
        git_source,
        workspace_root=workspace_root,
        config_home=config_home,
        local=local,
    )
    return path if path.exists() else None


@dataclass(frozen=True, slots=True)
class PackageUpdateResult:
    source: str
    scope: str
    status: str
    detail: str = ""


def update_configured_packages(
    *,
    sources: Iterable[tuple[str, str]],
    workspace_root: Path,
    config_home: Path,
    target: str | None = None,
) -> list[PackageUpdateResult]:
    """Update configured package sources.

    `sources` is `(source, scope)` where scope is `"user"` or `"project"`.
    Local-path sources are no-ops. Git sources are fetched/reset. Unsupported
    configured remote sources fail closed with a per-source error result.
    """

    results: list[PackageUpdateResult] = []
    selected = select_update_sources(
        sources=sources, workspace_root=workspace_root, target=target
    )
    if target is not None and not selected:
        raise PackageSourceError(f"No matching package found for {target}")
    for source, scope in selected:
        local = scope == "project"
        git_source = parse_git_source(source)
        if git_source is not None:
            try:
                path = update_git_package(
                    git_source,
                    workspace_root=workspace_root,
                    config_home=config_home,
                    local=local,
                )
            except (PackageCommandError, PackageSourceError) as exc:
                results.append(PackageUpdateResult(source, scope, "failed", str(exc)))
            else:
                results.append(
                    PackageUpdateResult(source, scope, "updated", _safe_display_path(path))
                )
            continue
        if is_local_path_source(source):
            results.append(PackageUpdateResult(source, scope, "skipped", "local path"))
            continue
        results.append(PackageUpdateResult(source, scope, "failed", "unsupported source"))
    return results


def select_update_sources(
    *,
    sources: Iterable[tuple[str, str]],
    workspace_root: Path,
    target: str | None = None,
) -> list[tuple[str, str]]:
    """Select configured package update targets without running commands."""

    entries = list(sources)
    if target is None:
        return entries
    target_identity = _package_identity(target, workspace_root)
    return [
        (source, scope)
        for source, scope in entries
        if _package_identity(source, workspace_root) == target_identity
    ]


def _explicit_git_url(value: str) -> bool:
    lowered = value.lower()
    return any(
        lowered.startswith(prefix)
        for prefix in ("http://", "https://", "ssh://", "git://", "file://")
    )


def _split_git_ref(value: str) -> tuple[str, str | None]:
    if re.match(r"^git@[^:]+:", value):
        host, rest = value.split(":", 1)
        repo_path, sep, ref = rest.rpartition("@")
        if sep and repo_path and ref:
            return f"{host}:{repo_path}", ref
        return value, None
    if "://" in value:
        try:
            parsed = urllib.parse.urlsplit(value)
        except ValueError:
            return value, None
        path = parsed.path.lstrip("/")
        repo_path, sep, ref = path.rpartition("@")
        if sep and repo_path and ref:
            rebuilt = parsed._replace(path="/" + repo_path, fragment="")
            return urllib.parse.urlunsplit(rebuilt).rstrip("/"), ref
        if parsed.fragment:
            rebuilt = parsed._replace(fragment="")
            return urllib.parse.urlunsplit(rebuilt).rstrip("/"), parsed.fragment
        return value, None
    host_path, sep, ref = value.rpartition("@")
    if sep and "/" in host_path and ref:
        return host_path, ref
    return value, None


def _parse_git_repo(value: str, *, allow_shorthand: bool) -> tuple[str, str, str] | None:
    scp_match = re.match(r"^git@([^:]+):(.+)$", value)
    if scp_match:
        return value, scp_match.group(1), scp_match.group(2).lstrip("/")
    if "://" in value:
        try:
            parsed = urllib.parse.urlsplit(value)
        except ValueError:
            return None
        if parsed.scheme.lower() not in _SUPPORTED_GIT_URL_SCHEMES:
            return None
        host = parsed.hostname or ("localhost" if parsed.scheme.lower() == "file" else "")
        path = parsed.path.lstrip("/")
        if parsed.username or parsed.password:
            # Credentials in configured package URLs are a supply-chain footgun
            # and would be hard to keep out of command displays. Refuse them.
            return None
        return value, host, path
    if allow_shorthand:
        slash = value.find("/")
        if slash > 0:
            host = value[:slash]
            path = value[slash + 1 :]
            if "." in host or host == "localhost":
                return f"https://{value}", host, path
    return None


def _safe_git_component(value: str, *, allow_slash: bool) -> bool:
    try:
        decoded = urllib.parse.unquote(value)
    except Exception:
        return False
    for candidate in (value, decoded):
        if "\x00" in candidate or "\\" in candidate or candidate.startswith("/"):
            return False
        if not allow_slash and "/" in candidate:
            return False
        if any(part in ("", ".", "..") for part in candidate.split("/")):
            return False
    return True


def _git_cache_root(*, workspace_root: Path | None, config_home: Path, local: bool) -> Path:
    if local:
        if workspace_root is None:
            raise PackageSourceError("project package cache requires a workspace root")
        return workspace_root / ".pipy" / "git"
    return config_home / "git"


def _contained_path(root: Path, parts: Sequence[str]) -> Path:
    resolved_root = root.expanduser().resolve()
    candidate = resolved_root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise PackageSourceError(f"refusing package cache path outside {resolved_root}") from exc
    return candidate


def _ensure_cache_root(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    ignore = root / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n!.gitignore\n", encoding="utf-8")


def _run_git(args: Sequence[str], *, cwd: Path | None) -> str:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PackageCommandError("git executable not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise PackageCommandError("git command timed out") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        message = detail[-1] if detail else f"exit {completed.returncode}"
        raise PackageCommandError(f"git {' '.join(args[:2])} failed: {message}")
    return completed.stdout


def _current_upstream_ref(target: Path) -> str | None:
    try:
        upstream = _run_git(["rev-parse", "--abbrev-ref", "@{upstream}"], cwd=target)
    except PackageCommandError:
        return None
    trimmed = upstream.strip()
    return trimmed if trimmed.startswith("origin/") else None


def _prune_empty_parents(start: Path, root: Path) -> None:
    try:
        resolved_root = root.resolve()
        current = start.resolve()
    except OSError:
        return
    while current != resolved_root:
        try:
            current.relative_to(resolved_root)
        except ValueError:
            return
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _package_identity(source: str | None, workspace_root: Path) -> str | None:
    if source is None:
        return None
    git_source = parse_git_source(source)
    if git_source is not None:
        return f"git:{git_source.host}/{git_source.path}"
    if is_local_path_source(source):
        resolved = canonical_local_source(source, workspace_root)
        return f"local:{resolved}" if resolved is not None else f"local:{source}"
    return source.strip()


def _safe_display_path(path: Path) -> str:
    try:
        return str(path)
    except OSError:
        return "<package-cache>"
