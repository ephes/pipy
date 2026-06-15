"""Python extension discovery and manifest inventory (slice 1).

This module is the inventory boundary for pipy's Python extension
platform. It discovers workspace and global Python extension
candidates, parses an optional `pipy-extension.toml` manifest, infers
safe defaults, validates names / API versions / source paths, and
returns archive-safe loadable/disabled descriptors with reason codes.

**It never imports or executes extension code.** Discovery only stats
candidates, reads entry-file bytes to compute an inventory hash, and
parses the manifest TOML. The entry module is not added to the import
system. Activation (`activate(api)`) is a later slice; until then a
descriptor is a pure inventory record.

Discovery locations mirror the resource stores
(`pipy_harness.native.skills`):

- Workspace directory extension: `.pipy/extensions/<name>/extension.py`
  (with an optional `.pipy/extensions/<name>/pipy-extension.toml`).
- Workspace single-file extension: `.pipy/extensions/<name>.py`.
- Global extensions under the resolved config root (`PIPY_CONFIG_HOME`
  then `${XDG_CONFIG_HOME}/pipy` then `~/.config/pipy`) in the same two
  shapes.

The module reuses the safety primitives pinned by
`pipy_harness.native._resource_files`: the global-root resolver, the
control-character / secret-name screen, and the bounded byte reader.
No manifest body, description text, or source code is intended to reach
the default session archive; project descriptors through
`safe_extension_metadata` for archive-safe records.

Public API:

- `ExtensionDescriptor` value object.
- `discover_extensions(workspace_root, ...)` returns the descriptor list.
- `safe_extension_metadata(descriptors)` projects to archive-safe dicts.
"""

from __future__ import annotations

import codecs
import hashlib
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from pipy_harness.capture import looks_sensitive
from pipy_harness.native._resource_files import (
    GLOBAL_PATH_LABEL_PREFIX,
    WORKSPACE_PIPY_DIR_NAME,
    _contains_control_character,
    _read_capped_bytes,
    _sanitize_label,
    resolve_global_resource_root,
)

EXTENSIONS_SUBDIR: str = "extensions"
MANIFEST_FILENAME: str = "pipy-extension.toml"

CURRENT_API_VERSION: str = "0.1"
SUPPORTED_API_MAJOR: int = 0
INFERRED_VERSION: str = "0.0.0-local"
DEFAULT_ENTRY_MODULE: str = "extension"
DEFAULT_ENTRY_FUNCTION: str = "activate"

PERMISSION_KEYS: tuple[str, ...] = (
    "workspace_read",
    "workspace_write",
    "shell",
    "network",
    "ui",
)

# Reason codes recorded on disabled descriptors. They are safe,
# enumerable labels (never free-form text from a manifest).
REASON_INVALID_NAME: str = "invalid_name"
REASON_INVALID_MANIFEST: str = "invalid_manifest"
REASON_UNSUPPORTED_API_VERSION: str = "unsupported_api_version"
REASON_DUPLICATE_NAME: str = "duplicate_name"
REASON_MISSING_ENTRY: str = "missing_entry"
REASON_UNSAFE_PATH: str = "unsafe_path"
REASON_BINARY_ENTRY: str = "binary_entry"
REASON_UNSAFE_NAME: str = "unsafe_name"

_PER_FILE_BYTE_CAP: int = 256 * 1024
_NUL_SCAN_CHUNK_SIZE: int = 1024 * 1024

SourceKind = Literal["workspace", "global"]
ExtensionKind = Literal["directory", "single_file"]
Status = Literal["loadable", "disabled"]


@dataclass(frozen=True, slots=True)
class ExtensionDescriptor:
    """One discovered Python extension candidate.

    A descriptor is an inventory record only; the entry module has not
    been imported. `path_label` is the workspace-relative POSIX path
    (single-file `.pipy/extensions/foo.py` or directory
    `.pipy/extensions/foo`) for workspace extensions, and
    `<global>/extensions/...` for global ones. `entry_path_label`
    labels the `.py` entry file specifically. `sha256` and
    `byte_length` describe the entry file on disk and are `""`/`0` when
    no entry file could be read (for example, a disabled
    `missing_entry` descriptor). `permissions` are descriptive manifest
    declarations only; they are not enforced in this slice. `status` is
    `"loadable"` when the candidate could be activated by a later slice,
    or `"disabled"` with a `reason` code otherwise.
    """

    name: str
    version: str
    api_version: str
    description: str
    source_kind: SourceKind
    kind: ExtensionKind
    path_label: str
    entry_module: str
    entry_function: str
    entry_path_label: str
    permissions: Mapping[str, bool]
    manifest_present: bool
    status: Status
    reason: str | None
    sha256: str
    byte_length: int
    # Absolute on-disk path of the entry file, for the activation
    # runtime to import. `None` for disabled descriptors. Intentionally
    # excluded from `safe_extension_metadata` (it is a local absolute
    # path, never archived).
    entry_path: str | None = None


@dataclass(frozen=True, slots=True)
class _Candidate:
    """A pre-validation extension candidate located on disk."""

    name: str
    source_kind: SourceKind
    kind: ExtensionKind
    base_dir: Path
    path_label: str
    containment_root: Path
    is_symlinked_dir: bool


def discover_extensions(
    workspace_root: Path,
    *,
    config_home_env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
) -> list[ExtensionDescriptor]:
    """Discover workspace and global Python extension candidates.

    The workspace dir is `<workspace>/.pipy/extensions`; the global dir
    is `<config-root>/extensions`. Candidates are workspace-first, then
    global, each sorted by name. Within a source, a directory and a
    single-file candidate of the same name both appear, but the second
    occurrence of a name (in iteration order) is disabled with
    `duplicate_name`. Missing directories never raise.

    No extension module is imported and no extension code runs. The
    returned descriptors are pure inventory records.
    """

    resolved_workspace = workspace_root.expanduser().resolve()
    workspace_dir = resolved_workspace / WORKSPACE_PIPY_DIR_NAME / EXTENSIONS_SUBDIR

    global_root = resolve_global_resource_root(env=config_home_env, home_dir=home_dir)
    global_dir = global_root / EXTENSIONS_SUBDIR

    sources: list[tuple[Path, SourceKind, Path]] = [
        (workspace_dir, "workspace", resolved_workspace),
        (global_dir, "global", global_root),
    ]

    descriptors: list[ExtensionDescriptor] = []
    seen_names: set[str] = set()
    for source_dir, source_kind, label_root in sources:
        for candidate in _iter_candidates(source_dir, source_kind, label_root):
            descriptor = _inventory_candidate(candidate)
            # Deduplicate on the RESOLVED descriptor name (which a
            # manifest `name` may override), not the filesystem
            # candidate name. The name is reserved even when the first
            # occurrence is disabled, so a later candidate with the same
            # name is reported as a duplicate rather than a second copy
            # of the same problem.
            if descriptor.name in seen_names:
                # Derive the duplicate record from the already-safe
                # computed descriptor (whose name/labels are redacted for
                # unsafe names), never from the raw candidate.
                descriptors.append(
                    replace(
                        descriptor,
                        status="disabled",
                        reason=REASON_DUPLICATE_NAME,
                        sha256="",
                        byte_length=0,
                    )
                )
                continue
            seen_names.add(descriptor.name)
            descriptors.append(descriptor)
    return descriptors


def safe_extension_metadata(
    descriptors: Sequence[ExtensionDescriptor],
) -> list[dict[str, object]]:
    """Project descriptors to archive-safe metadata.

    The returned dicts carry only safe labels: name, version,
    api_version, source/kind, path label, manifest-present flag, status,
    reason code, and the entry-file hash/length. Manifest descriptions,
    permission tables, and source code are excluded so the archive never
    receives extension text.
    """

    return [
        {
            "name": descriptor.name,
            "version": descriptor.version,
            "api_version": descriptor.api_version,
            "source_kind": descriptor.source_kind,
            "kind": descriptor.kind,
            "path_label": descriptor.path_label,
            "manifest_present": descriptor.manifest_present,
            "status": descriptor.status,
            "reason": descriptor.reason,
            "sha256": descriptor.sha256,
            "byte_length": descriptor.byte_length,
        }
        for descriptor in descriptors
    ]


def _iter_candidates(
    source_dir: Path,
    source_kind: SourceKind,
    label_root: Path,
) -> list[_Candidate]:
    """Locate directory and single-file candidates under `source_dir`.

    A symlinked source directory is ignored entirely, and so is one whose
    *resolved* path escapes the owning root (for example, a symlinked
    `.pipy` ancestor pointing outside the workspace) — otherwise outside
    code could be discovered behind a safe-looking `.pipy/extensions/...`
    label. Within the directory, each subdirectory is a directory-extension
    candidate and each `*.py` file is a single-file candidate. Hidden
    entries (leading dot) are excluded by the glob.
    """

    try:
        if source_dir.is_symlink() or not source_dir.is_dir():
            return []
        containment_root = source_dir.resolve()
        # The whole resolved store must stay inside the owning root
        # (resolved workspace, or global config root). This catches a
        # symlinked ancestor that escapes even when the leaf component is
        # not itself a symlink.
        containment_root.relative_to(label_root.resolve())
    except (OSError, ValueError):
        return []

    try:
        entries = sorted(source_dir.iterdir(), key=lambda p: p.name)
    except OSError:
        return []

    candidates: list[_Candidate] = []
    for entry in entries:
        name = entry.name
        if name.startswith("."):
            continue
        try:
            is_dir = entry.is_dir()
            is_file = entry.is_file()
        except OSError:
            continue
        try:
            entry_is_symlink = entry.is_symlink()
        except OSError:
            continue
        is_symlinked_dir = False
        if is_dir:
            base_name = name
            kind: ExtensionKind = "directory"
            # A directory extension's entry must stay inside the
            # extension's OWN directory, not merely inside the shared
            # `.pipy/extensions` store.
            candidate_containment = _resolved_or_none(entry)
            # A symlinked extension directory must not be trusted: it can
            # point outside the allowed extension roots. It is rejected
            # up front (before any manifest is read through it).
            is_symlinked_dir = entry_is_symlink
        elif is_file and name.endswith(".py"):
            base_name = name[: -len(".py")]
            kind = "single_file"
            # A single-file extension lives directly in the store, so its
            # containment is the store directory; a `.py` symlink that
            # escapes the store fails the entry path check below.
            candidate_containment = containment_root
        else:
            continue
        if candidate_containment is None:
            continue
        # Name safety (control characters / secret-shaped names) is NOT
        # screened here: such candidates still produce a visible disabled
        # descriptor in `_inventory_candidate`, with a redacted label so
        # the sensitive name never reaches the inventory.
        candidates.append(
            _Candidate(
                name=base_name,
                source_kind=source_kind,
                kind=kind,
                base_dir=entry,
                path_label=_path_label_for(entry, source_kind, label_root),
                containment_root=candidate_containment,
                is_symlinked_dir=is_symlinked_dir,
            )
        )
    return candidates


def _resolved_or_none(path: Path) -> Path | None:
    try:
        return path.resolve()
    except OSError:
        return None


def _inventory_candidate(candidate: _Candidate) -> ExtensionDescriptor:
    """Build a descriptor for one candidate without importing it."""

    if _contains_control_character(candidate.name) or looks_sensitive(candidate.name):
        # A control-character or secret-shaped name is recorded as a
        # disabled descriptor (deterministic inventory) but with a
        # redacted label, so the unsafe name never enters the inventory
        # or archive-safe metadata. This is screened FIRST so no other
        # disabling path (symlink, duplicate) can rebuild a descriptor
        # from the raw unsafe name.
        return _disabled_unsafe_name(candidate)

    if candidate.is_symlinked_dir:
        # Reject before reading anything through the symlink.
        return _disabled(candidate, REASON_UNSAFE_PATH)

    sanitized_name = _sanitize_label(candidate.name)
    if not _is_valid_extension_name(sanitized_name):
        return _disabled(candidate, REASON_INVALID_NAME)

    manifest_present = False
    manifest: dict[str, object] = {}
    if candidate.kind == "directory":
        location, resolved_manifest = _locate_manifest(
            candidate.base_dir, candidate.containment_root
        )
        if location == "unsafe":
            # A manifest symlink that escapes the extension directory is
            # never read or trusted.
            return _disabled(candidate, REASON_UNSAFE_PATH)
        if location == "present" and resolved_manifest is not None:
            manifest_present = True
            parsed = _parse_manifest(resolved_manifest)
            if parsed is None:
                return _disabled(
                    candidate, REASON_INVALID_MANIFEST, manifest_present=True
                )
            manifest = parsed

    name = candidate.name
    description = ""
    version = INFERRED_VERSION
    api_version = CURRENT_API_VERSION
    if manifest:
        raw_name = manifest.get("name")
        if raw_name is not None:
            if not isinstance(raw_name, str):
                return _disabled(
                    candidate, REASON_INVALID_MANIFEST, manifest_present=True
                )
            sanitized = _sanitize_label(raw_name)
            if not _is_valid_extension_name(sanitized) or looks_sensitive(sanitized):
                # Apply the same secret-name screen filesystem candidate
                # names get, so a manifest cannot smuggle a secret-shaped
                # name into archive-safe metadata.
                return _disabled(
                    candidate, REASON_INVALID_NAME, manifest_present=True
                )
            name = sanitized
        for key, target in (("version", "version"), ("api_version", "api_version")):
            if key not in manifest:
                # Absent field keeps the inferred default.
                continue
            value = manifest.get(key)
            sanitized = _sanitize_label(value) if isinstance(value, str) else ""
            if not isinstance(value, str) or not sanitized:
                # A present field that is non-string or empty after
                # sanitization is malformed: fail closed rather than
                # default. The already-parsed `name` is reserved so a
                # later same-named extension is a duplicate, not a load.
                return _disabled(
                    candidate,
                    REASON_INVALID_MANIFEST,
                    manifest_present=True,
                    name=name,
                )
            if target == "version":
                if looks_sensitive(sanitized):
                    # `version` is emitted into archive-safe metadata, so
                    # apply the same secret screen as `name`: a manifest
                    # must not smuggle sensitive text through it.
                    return _disabled(
                        candidate,
                        REASON_INVALID_MANIFEST,
                        manifest_present=True,
                        name=name,
                    )
                version = sanitized
            else:
                api_version = sanitized
        raw_description = manifest.get("description")
        if raw_description is not None:
            if not isinstance(raw_description, str):
                return _disabled(
                    candidate,
                    REASON_INVALID_MANIFEST,
                    manifest_present=True,
                    name=name,
                )
            description = _sanitize_label(raw_description)

    # Entry fields are extracted AFTER the manifest `name` so that an
    # invalid `[entry]` still reserves the declared name (a later
    # same-named extension is then a duplicate, not a silent load).
    entry_module = _manifest_str(manifest, ("entry", "module"), DEFAULT_ENTRY_MODULE)
    entry_function = _manifest_str(
        manifest, ("entry", "function"), DEFAULT_ENTRY_FUNCTION
    )
    if entry_module is None or entry_function is None:
        return _disabled(
            candidate, REASON_INVALID_MANIFEST, manifest_present=manifest_present, name=name
        )
    # `entry_module` becomes part of `entry_path_label`, and both entry
    # fields are emitted on the descriptor, so screen them for
    # secret-shaped text the same way names are.
    if looks_sensitive(entry_module) or looks_sensitive(entry_function):
        return _disabled(
            candidate, REASON_INVALID_MANIFEST, manifest_present=manifest_present, name=name
        )
    # entry.module / entry.function must be single Python identifiers
    # (no dotted package paths), so discovery and the activation loader
    # agree on the entry file and its module semantics.
    if not entry_module.isidentifier() or not entry_function.isidentifier():
        return _disabled(
            candidate, REASON_INVALID_MANIFEST, manifest_present=manifest_present, name=name
        )

    permissions = _parse_permissions(manifest)
    if permissions is None:
        return _disabled(
            candidate, REASON_INVALID_MANIFEST, manifest_present=manifest_present, name=name
        )

    major = _api_major(api_version)
    if major is None:
        return _disabled(
            candidate,
            REASON_INVALID_MANIFEST,
            manifest_present=manifest_present,
            name=name,
        )
    if major > SUPPORTED_API_MAJOR:
        return _disabled(
            candidate,
            REASON_UNSUPPORTED_API_VERSION,
            manifest_present=manifest_present,
            name=name,
            version=version,
            api_version=api_version,
            description=description,
            permissions=permissions,
        )

    entry_path, entry_path_label = _entry_file_for(candidate, entry_module)
    status, sha256, byte_length, resolved_entry = _classify_entry_file(
        entry_path, candidate.containment_root
    )
    if status != "ok":
        return _disabled(
            candidate,
            status,
            manifest_present=manifest_present,
            name=name,
            version=version,
            api_version=api_version,
            description=description,
            permissions=permissions,
        )

    return ExtensionDescriptor(
        name=name,
        version=version,
        api_version=api_version,
        description=description,
        source_kind=candidate.source_kind,
        kind=candidate.kind,
        path_label=candidate.path_label,
        entry_module=entry_module,
        entry_function=entry_function,
        entry_path_label=entry_path_label,
        permissions=permissions,
        manifest_present=manifest_present,
        status="loadable",
        reason=None,
        sha256=sha256,
        byte_length=byte_length,
        entry_path=resolved_entry,
    )


def _entry_file_for(
    candidate: _Candidate,
    entry_module: str,
) -> tuple[Path | None, str]:
    """Return the entry `.py` path and its label for a candidate."""

    if candidate.kind == "single_file":
        return candidate.base_dir, candidate.path_label
    entry_path = candidate.base_dir / f"{entry_module}.py"
    return entry_path, f"{candidate.path_label}/{entry_module}.py"


def _classify_entry_file(
    entry_path: Path | None,
    containment_root: Path,
) -> tuple[str, str, int, str | None]:
    """Classify the entry file for inventory without importing it.

    Reading bytes is not execution: the file is hashed for inventory
    only and never imported. Returns `("ok", sha256, byte_length,
    resolved_path)` for a usable text entry, or one of the disabled
    reason codes paired with `("", 0, None)`:

    - `REASON_MISSING_ENTRY` when no entry file exists;
    - `REASON_UNSAFE_PATH` when the entry exists but escapes
      `containment_root`;
    - `REASON_BINARY_ENTRY` when the entry is not NUL-free UTF-8 text
      (binary content that must never be imported as Python source).
    """

    if entry_path is None:
        return (REASON_MISSING_ENTRY, "", 0, None)
    try:
        is_file = entry_path.is_file()
    except OSError:
        return (REASON_MISSING_ENTRY, "", 0, None)
    if not is_file:
        return (REASON_MISSING_ENTRY, "", 0, None)
    try:
        resolved = entry_path.resolve()
        resolved.relative_to(containment_root)
    except (OSError, ValueError):
        return (REASON_UNSAFE_PATH, "", 0, None)
    try:
        _head, byte_length, sha256 = _read_capped_bytes(
            resolved, per_file_byte_cap=_PER_FILE_BYTE_CAP
        )
    except OSError:
        return (REASON_MISSING_ENTRY, "", 0, None)
    # Binary content fails closed across the WHOLE file (not just the
    # capped head): a NUL byte anywhere, or any non-UTF-8 sequence,
    # disqualifies the entry from being inventoried as loadable.
    if not _file_is_utf8_text(resolved):
        return (REASON_BINARY_ENTRY, "", 0, None)
    return ("ok", sha256, byte_length, str(resolved))


def _file_is_utf8_text(path: Path) -> bool:
    """Stream the whole file, returning True only for NUL-free UTF-8 text.

    A single bounded-chunk pass feeds an incremental UTF-8 decoder (so a
    multibyte character split across a chunk boundary is not mistaken for
    invalid bytes) and rejects any NUL byte. An unreadable file fails
    closed (treated as non-text).
    """

    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(_NUL_SCAN_CHUNK_SIZE)
                if not chunk:
                    break
                if b"\x00" in chunk:
                    return False
                try:
                    decoder.decode(chunk, final=False)
                except UnicodeDecodeError:
                    return False
            try:
                decoder.decode(b"", final=True)
            except UnicodeDecodeError:
                return False
    except OSError:
        return False
    return True


def _disabled(
    candidate: _Candidate,
    reason: str,
    *,
    manifest_present: bool = False,
    name: str | None = None,
    version: str = INFERRED_VERSION,
    api_version: str = CURRENT_API_VERSION,
    description: str = "",
    permissions: Mapping[str, bool] | None = None,
) -> ExtensionDescriptor:
    """Build a disabled descriptor with a safe reason code."""

    resolved_name = name if name is not None else _sanitize_label(candidate.name)
    return ExtensionDescriptor(
        name=resolved_name or candidate.name,
        version=version,
        api_version=api_version,
        description=description,
        source_kind=candidate.source_kind,
        kind=candidate.kind,
        path_label=candidate.path_label,
        entry_module=DEFAULT_ENTRY_MODULE,
        entry_function=DEFAULT_ENTRY_FUNCTION,
        entry_path_label=candidate.path_label,
        permissions=permissions if permissions is not None else _default_permissions(),
        manifest_present=manifest_present,
        status="disabled",
        reason=reason,
        sha256="",
        byte_length=0,
    )


def _disabled_unsafe_name(candidate: _Candidate) -> ExtensionDescriptor:
    """Disabled descriptor for an unsafe (control/secret) candidate name.

    The raw name and its derived `path_label` may themselves be sensitive
    or terminal-hostile, so they are replaced with a deterministic,
    one-way redaction (`<redacted-extension:<hash>>`). The record stays in
    the inventory with `REASON_UNSAFE_NAME` so the skip is visible, but no
    sensitive name text reaches any descriptor field or archive metadata.
    """

    digest = hashlib.sha256(
        candidate.name.encode("utf-8", "surrogatepass")
    ).hexdigest()[:12]
    redacted = f"<redacted-extension:{digest}>"
    return ExtensionDescriptor(
        name=redacted,
        version=INFERRED_VERSION,
        api_version=CURRENT_API_VERSION,
        description="",
        source_kind=candidate.source_kind,
        kind=candidate.kind,
        path_label=redacted,
        entry_module=DEFAULT_ENTRY_MODULE,
        entry_function=DEFAULT_ENTRY_FUNCTION,
        entry_path_label=redacted,
        permissions=_default_permissions(),
        manifest_present=False,
        status="disabled",
        reason=REASON_UNSAFE_NAME,
        sha256="",
        byte_length=0,
    )


def _locate_manifest(
    base_dir: Path,
    containment_root: Path,
) -> tuple[Literal["absent", "present", "unsafe"], Path | None]:
    """Locate a containment-safe `pipy-extension.toml` for a directory.

    Returns ``("absent", None)`` when no manifest file is present,
    ``("present", resolved)`` when the manifest is a regular file that
    resolves inside ``containment_root``, and ``("unsafe", None)`` when a
    manifest exists but escapes containment (for example, a symlink to a
    file outside the extension directory). The manifest is never opened
    here.
    """

    manifest_path = base_dir / MANIFEST_FILENAME
    try:
        # `lexists`-style check: True for a broken symlink too, so
        # *something* occupying the reserved path is never silently
        # treated as "no manifest".
        present_any = manifest_path.is_symlink() or manifest_path.exists()
    except OSError:
        return ("absent", None)
    if not present_any:
        return ("absent", None)
    # Something occupies the reserved manifest path. It must be a regular
    # file that resolves inside containment; a directory, broken symlink,
    # symlink-to-directory, or escaping symlink fails closed.
    try:
        if not manifest_path.is_file():
            return ("unsafe", None)
        resolved = manifest_path.resolve()
        resolved.relative_to(containment_root)
    except (OSError, ValueError):
        return ("unsafe", None)
    return ("present", resolved)


def _parse_manifest(manifest_path: Path) -> dict[str, object] | None:
    """Parse the manifest TOML, returning `None` on any error.

    Parsing never imports or executes anything; `tomllib` reads the
    file as data.
    """

    try:
        with manifest_path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return None


def _manifest_str(
    manifest: Mapping[str, object],
    path: tuple[str, ...],
    default: str,
) -> str | None:
    """Read a nested string from the manifest, defaulting when absent.

    Returns `None` when the addressed value exists but is not a string,
    is empty after sanitization, or when an intermediate key is not a
    table (all malformed-manifest cases the caller fails closed on). A
    fully absent key returns `default`.
    """

    cursor: object = manifest
    for index, key in enumerate(path):
        if not isinstance(cursor, Mapping):
            return None
        if key not in cursor:
            return default
        cursor = cursor[key]
        if index < len(path) - 1 and not isinstance(cursor, Mapping):
            return None
    if not isinstance(cursor, str):
        return None
    sanitized = _sanitize_label(cursor)
    # Present but empty after sanitization is malformed, not a default.
    return sanitized or None


def _parse_permissions(
    manifest: Mapping[str, object],
) -> dict[str, bool] | None:
    """Parse the `[permissions]` table into the known boolean keys.

    Unknown keys are ignored. A non-boolean value for a known key makes
    the manifest invalid (`None`). Absent table or keys default to
    `False`.
    """

    permissions = _default_permissions()
    table = manifest.get("permissions")
    if table is None:
        return permissions
    if not isinstance(table, Mapping):
        return None
    for key, value in table.items():
        if key not in PERMISSION_KEYS:
            # Unknown permission key (typo or forward permission): fail
            # closed rather than silently dropping it.
            return None
        if not isinstance(value, bool):
            return None
        permissions[key] = value
    return permissions


def _default_permissions() -> dict[str, bool]:
    return {key: False for key in PERMISSION_KEYS}


def _api_major(api_version: str) -> int | None:
    """Return the major component of a well-formed `api_version`.

    A valid version is dot-separated numeric components (for example
    `0`, `0.1`, or `0.1.0`). Any non-numeric component makes the whole
    version invalid (`None`) so junk like `0.not-a-version` is rejected
    before a descriptor is created, rather than passing the major check
    and leaking arbitrary manifest text into metadata.
    """

    parts = api_version.split(".")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    return int(parts[0])


def _is_valid_extension_name(name: str) -> bool:
    """Return True for a lowercase ASCII identifier with optional `-`.

    Extension names appear in local UI listings and (later) command /
    tool registration, so they share the slash-command name rules:
    lowercase ASCII letters, digits, underscore, and hyphen, starting
    with a letter or digit.
    """

    if not name:
        return False
    if name[0] not in _NAME_START_CHARS:
        return False
    return all(ch in _NAME_BODY_CHARS for ch in name)


_NAME_START_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")
_NAME_BODY_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")


def _path_label_for(
    entry: Path,
    source_kind: SourceKind,
    label_root: Path,
) -> str:
    """Compute the workspace-relative or `<global>`-prefixed label."""

    if source_kind == "global":
        return f"{GLOBAL_PATH_LABEL_PREFIX}{EXTENSIONS_SUBDIR}/{entry.name}"
    try:
        # Label from the LITERAL path, not the symlink target: a
        # symlinked candidate must still read as `.pipy/extensions/<name>`
        # and never leak its target path into safe metadata. `entry` is
        # already built under the resolved workspace root, so a literal
        # `relative_to` is correct without re-resolving.
        relative = entry.relative_to(label_root)
        return relative.as_posix()
    except (OSError, ValueError):
        return entry.name
