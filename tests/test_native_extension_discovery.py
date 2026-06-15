"""Slice 1 tests for the Python extension discovery + manifest inventory.

These tests pin the discovery rules in
`pipy_harness.native.extensions`. Slice 1 is an inventory boundary only:
no extension module is ever imported and no extension code runs. The
discovery layer stats candidates, reads entry-file bytes (for the
inventory hash), and parses the optional `pipy-extension.toml` manifest.
It never imports the entry module. The non-execution proof below pins
that invariant with an observable side effect.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pipy_harness.native.extensions import (
    CURRENT_API_VERSION,
    REASON_BINARY_ENTRY,
    REASON_DUPLICATE_NAME,
    REASON_INVALID_MANIFEST,
    REASON_INVALID_NAME,
    REASON_MISSING_ENTRY,
    REASON_UNSAFE_NAME,
    REASON_UNSAFE_PATH,
    REASON_UNSUPPORTED_API_VERSION,
    ExtensionDescriptor,
    discover_extensions,
    safe_extension_metadata,
)


def _empty_env() -> dict[str, str]:
    return {}


def _discover(
    workspace: Path,
    *,
    env: dict[str, str] | None = None,
    home_dir: Path | None = None,
) -> list[ExtensionDescriptor]:
    return discover_extensions(
        workspace,
        config_home_env=env if env is not None else _empty_env(),
        home_dir=home_dir if home_dir is not None else workspace,
    )


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


def _ext_dir(workspace: Path) -> Path:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write_single_file(workspace: Path, name: str, body: str = "def activate(api):\n    pass\n") -> Path:
    path = _ext_dir(workspace) / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    return path


def _write_dir_extension(
    workspace: Path,
    name: str,
    *,
    manifest: str | None = None,
    entry_filename: str = "extension.py",
    entry_body: str = "def activate(api):\n    pass\n",
) -> Path:
    directory = _ext_dir(workspace) / name
    directory.mkdir(parents=True, exist_ok=True)
    if entry_filename is not None:
        (directory / entry_filename).write_text(entry_body, encoding="utf-8")
    if manifest is not None:
        (directory / "pipy-extension.toml").write_text(manifest, encoding="utf-8")
    return directory


def _by_name(descriptors: list[ExtensionDescriptor], name: str) -> ExtensionDescriptor:
    for descriptor in descriptors:
        if descriptor.name == name:
            return descriptor
    raise AssertionError(f"no descriptor named {name!r} in {[d.name for d in descriptors]}")


def test_single_file_extension_infers_safe_defaults(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write_single_file(workspace, "greet")

    descriptors = _discover(workspace)

    descriptor = _by_name(descriptors, "greet")
    assert descriptor.status == "loadable"
    assert descriptor.reason is None
    assert descriptor.kind == "single_file"
    assert descriptor.source_kind == "workspace"
    assert descriptor.version == "0.0.0-local"
    assert descriptor.api_version == CURRENT_API_VERSION
    assert descriptor.entry_module == "extension"
    assert descriptor.entry_function == "activate"
    assert descriptor.manifest_present is False
    assert descriptor.path_label == ".pipy/extensions/greet.py"
    assert descriptor.entry_path_label == ".pipy/extensions/greet.py"
    # All permissions default to False when no manifest declares them.
    assert dict(descriptor.permissions) == {
        "workspace_read": False,
        "workspace_write": False,
        "shell": False,
        "network": False,
        "ui": False,
    }


def test_directory_extension_reads_manifest(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    manifest = """
name = "protected-paths"
version = "0.2.0"
api_version = "0.1"
description = "Block writes to protected paths."

[entry]
module = "extension"
function = "activate"

[permissions]
workspace_read = true
workspace_write = false
shell = false
network = false
ui = true
"""
    _write_dir_extension(workspace, "protected-paths", manifest=manifest)

    descriptor = _by_name(_discover(workspace), "protected-paths")

    assert descriptor.status == "loadable"
    assert descriptor.kind == "directory"
    assert descriptor.version == "0.2.0"
    assert descriptor.description == "Block writes to protected paths."
    assert descriptor.manifest_present is True
    assert descriptor.path_label == ".pipy/extensions/protected-paths"
    assert descriptor.entry_path_label == ".pipy/extensions/protected-paths/extension.py"
    assert dict(descriptor.permissions) == {
        "workspace_read": True,
        "workspace_write": False,
        "shell": False,
        "network": False,
        "ui": True,
    }


def test_directory_extension_without_manifest_infers_defaults(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write_dir_extension(workspace, "plain")

    descriptor = _by_name(_discover(workspace), "plain")

    assert descriptor.status == "loadable"
    assert descriptor.kind == "directory"
    assert descriptor.manifest_present is False
    assert descriptor.version == "0.0.0-local"


def test_directory_extension_missing_entry_is_disabled(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    # Directory present but no extension.py and no manifest pointing elsewhere.
    (_ext_dir(workspace) / "empty").mkdir()

    descriptor = _by_name(_discover(workspace), "empty")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_MISSING_ENTRY


def test_invalid_manifest_is_disabled(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write_dir_extension(workspace, "broken", manifest="this is = = not valid toml [[[")

    descriptor = _by_name(_discover(workspace), "broken")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_INVALID_MANIFEST


def test_unsupported_api_major_is_disabled(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    manifest = 'name = "future"\napi_version = "1.0"\n'
    _write_dir_extension(workspace, "future", manifest=manifest)

    descriptor = _by_name(_discover(workspace), "future")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_UNSUPPORTED_API_VERSION


def test_malformed_api_version_is_invalid(tmp_path: Path) -> None:
    # A non-version api_version (only the major component is digits) must
    # not be treated as supported nor emitted verbatim into metadata.
    workspace = _make_workspace(tmp_path)
    _write_dir_extension(
        workspace,
        "weird",
        manifest='name = "weird"\napi_version = "0.not-a-version"\n',
    )

    descriptor = _by_name(_discover(workspace), "weird")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_INVALID_MANIFEST


def test_present_empty_api_version_is_invalid(tmp_path: Path) -> None:
    # A present-but-empty api_version is malformed and must fail closed,
    # not silently fall back to the current api version.
    workspace = _make_workspace(tmp_path)
    _write_dir_extension(
        workspace, "emptyver", manifest='name = "emptyver"\napi_version = ""\n'
    )

    descriptor = _by_name(_discover(workspace), "emptyver")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_INVALID_MANIFEST


def test_secret_shaped_entry_module_is_invalid(tmp_path: Path) -> None:
    # A secret-shaped entry module name must fail closed rather than be
    # emitted into entry_module / entry_path_label.
    workspace = _make_workspace(tmp_path)
    directory = _ext_dir(workspace) / "modtest"
    directory.mkdir(parents=True)
    (directory / "my-api-key-helper.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (directory / "pipy-extension.toml").write_text(
        'name = "modtest"\n[entry]\nmodule = "my-api-key-helper"\n', encoding="utf-8"
    )

    descriptor = _by_name(_discover(workspace), "modtest")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_INVALID_MANIFEST
    assert "api-key" not in descriptor.entry_module
    assert "api-key" not in descriptor.entry_path_label


def test_invalid_entry_reserves_declared_name(tmp_path: Path) -> None:
    # Declared name must be reserved even when `[entry]` validation fails
    # (entry is validated before name was extracted previously).
    workspace = _make_workspace(tmp_path)
    _write_dir_extension(
        workspace, "alpha", manifest='name = "shared"\n[entry]\nmodule = ""\n'
    )
    _write_dir_extension(workspace, "bravo", manifest='name = "shared"\n')

    descriptors = _discover(workspace)
    shared = [d for d in descriptors if d.name == "shared"]

    assert len(shared) == 2
    assert all(d.status == "disabled" for d in shared)
    reasons = {d.reason for d in shared}
    assert REASON_INVALID_MANIFEST in reasons
    assert REASON_DUPLICATE_NAME in reasons


def test_non_utf8_binary_entry_without_nul_is_disabled(tmp_path: Path) -> None:
    # Non-text content that lacks a NUL byte must still fail closed.
    workspace = _make_workspace(tmp_path)
    directory = _ext_dir(workspace) / "binext2"
    directory.mkdir(parents=True)
    (directory / "extension.py").write_bytes(b"\xff\xfe\xfa not utf8 \xc3\x28")

    descriptor = _by_name(_discover(workspace), "binext2")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_BINARY_ENTRY


def test_large_entry_invalid_utf8_after_cap_is_disabled(tmp_path: Path) -> None:
    # Valid UTF-8 in the first 256 KiB but invalid bytes afterwards (no
    # NUL) must still fail closed: text validation spans the whole file.
    workspace = _make_workspace(tmp_path)
    directory = _ext_dir(workspace) / "bigbin"
    directory.mkdir(parents=True)
    content = b"a" * (300 * 1024) + b"\xff\xfe\xc3\x28"
    (directory / "extension.py").write_bytes(content)

    descriptor = _by_name(_discover(workspace), "bigbin")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_BINARY_ENTRY


def test_dotted_entry_module_is_invalid(tmp_path: Path) -> None:
    # entry.module must be a single module identifier; a dotted value is
    # not supported and fails closed (keeps discovery and the activation
    # loader consistent).
    workspace = _make_workspace(tmp_path)
    _write_dir_extension(
        workspace, "dotted", manifest='name = "dotted"\n[entry]\nmodule = "pkg.main"\n'
    )

    descriptor = _by_name(_discover(workspace), "dotted")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_INVALID_MANIFEST


def test_present_empty_entry_module_is_invalid(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write_dir_extension(
        workspace, "emptyentry", manifest='name = "emptyentry"\n[entry]\nmodule = ""\n'
    )

    descriptor = _by_name(_discover(workspace), "emptyentry")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_INVALID_MANIFEST


def test_unknown_permission_key_is_invalid(tmp_path: Path) -> None:
    # Unknown permission keys (typos or forward permissions) make the
    # inventory misleading and must fail closed.
    workspace = _make_workspace(tmp_path)
    _write_dir_extension(
        workspace,
        "perms",
        manifest=(
            'name = "perms"\n[permissions]\nworkspace_read = true\nbogus = true\n'
        ),
    )

    descriptor = _by_name(_discover(workspace), "perms")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_INVALID_MANIFEST


def test_duplicate_name_disables_the_later_candidate(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    # A directory extension and a single-file extension that resolve to
    # the same name. The directory (sorted first) wins; the file is a
    # duplicate.
    _write_dir_extension(workspace, "dup")
    _write_single_file(workspace, "dup")

    descriptors = _discover(workspace)
    dups = [d for d in descriptors if d.name == "dup"]

    assert len(dups) == 2
    assert sum(1 for d in dups if d.status == "loadable") == 1
    disabled = [d for d in dups if d.status == "disabled"]
    assert len(disabled) == 1
    assert disabled[0].reason == REASON_DUPLICATE_NAME


def test_global_extension_is_discovered(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    config_home = tmp_path / "cfg"
    global_ext_dir = config_home / "extensions"
    global_ext_dir.mkdir(parents=True)
    (global_ext_dir / "globby.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )

    descriptors = discover_extensions(
        workspace,
        config_home_env={"PIPY_CONFIG_HOME": str(config_home)},
        home_dir=workspace,
    )

    descriptor = _by_name(descriptors, "globby")
    assert descriptor.source_kind == "global"
    assert descriptor.status == "loadable"
    assert descriptor.path_label == "<global>/extensions/globby.py"


def test_symlinked_entry_escape_is_unsafe(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("def activate(api):\n    pass\n", encoding="utf-8")
    directory = _ext_dir(workspace) / "escaper"
    directory.mkdir(parents=True)
    (directory / "extension.py").symlink_to(outside)

    descriptor = _by_name(_discover(workspace), "escaper")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_UNSAFE_PATH


def test_entry_symlink_escaping_own_dir_is_unsafe(tmp_path: Path) -> None:
    # The entry file must stay inside the extension's OWN directory, not
    # merely inside the shared `.pipy/extensions` store. A symlink to a
    # sibling file inside the store still escapes the extension dir.
    workspace = _make_workspace(tmp_path)
    ext = _ext_dir(workspace)
    sibling = ext / "sibling.py"
    sibling.write_text("def activate(api):\n    pass\n", encoding="utf-8")
    directory = ext / "sneaky"
    directory.mkdir()
    (directory / "extension.py").symlink_to(sibling)

    descriptor = _by_name(_discover(workspace), "sneaky")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_UNSAFE_PATH


def test_symlinked_pipy_ancestor_escaping_workspace_is_ignored(
    tmp_path: Path,
) -> None:
    # If an ancestor of the store (e.g. `.pipy`) is a symlink that
    # escapes the workspace, outside code must not be discovered as a
    # loadable workspace extension behind a safe-looking label.
    workspace = _make_workspace(tmp_path)
    outside_pipy = tmp_path / "outside_pipy"
    (outside_pipy / "extensions").mkdir(parents=True)
    (outside_pipy / "extensions" / "evil.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (workspace / ".pipy").symlink_to(outside_pipy, target_is_directory=True)

    descriptors = _discover(workspace)

    assert not any(d.name == "evil" for d in descriptors)


def test_symlinked_dir_with_secret_name_is_redacted(tmp_path: Path) -> None:
    # A candidate that is BOTH a symlinked dir AND secret-named must not
    # leak the secret name through the disabled descriptor.
    workspace = _make_workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "extension.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (_ext_dir(workspace) / "my-api-key-helper").symlink_to(
        outside, target_is_directory=True
    )

    descriptors = _discover(workspace)

    for descriptor in descriptors:
        assert "api-key" not in descriptor.name
        assert "api-key" not in descriptor.path_label
    for entry in safe_extension_metadata(descriptors):
        assert "api-key" not in json.dumps(entry)


def test_invalid_manifest_reserves_declared_name(tmp_path: Path) -> None:
    # An extension that declares a manifest name but fails later manifest
    # validation must still reserve that declared name, so a later
    # extension with the same name is a duplicate, not a silent load.
    workspace = _make_workspace(tmp_path)
    _write_dir_extension(
        workspace,
        "alpha",
        manifest='name = "shared"\n[permissions]\nbogus = true\n',
    )
    _write_dir_extension(workspace, "bravo", manifest='name = "shared"\n')

    descriptors = _discover(workspace)
    shared = [d for d in descriptors if d.name == "shared"]

    assert len(shared) == 2
    assert all(d.status == "disabled" for d in shared)
    reasons = {d.reason for d in shared}
    assert REASON_INVALID_MANIFEST in reasons
    assert REASON_DUPLICATE_NAME in reasons


def test_symlinked_extension_directory_escaping_store_is_unsafe(
    tmp_path: Path,
) -> None:
    # A symlinked extension directory that points outside the store must
    # not become loadable (otherwise activation could run code outside
    # the allowed extension roots).
    workspace = _make_workspace(tmp_path)
    outside_dir = tmp_path / "outside_ext"
    outside_dir.mkdir()
    (outside_dir / "extension.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (_ext_dir(workspace) / "linked").symlink_to(outside_dir, target_is_directory=True)

    descriptor = _by_name(_discover(workspace), "linked")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_UNSAFE_PATH


def test_manifest_name_collision_across_dirs_is_duplicate(tmp_path: Path) -> None:
    # Two different directories that declare the same manifest `name`
    # must be deduplicated on the resolved name, not the directory name.
    workspace = _make_workspace(tmp_path)
    _write_dir_extension(workspace, "alpha", manifest='name = "shared"\n')
    _write_dir_extension(workspace, "bravo", manifest='name = "shared"\n')

    descriptors = _discover(workspace)
    shared = [d for d in descriptors if d.name == "shared"]

    assert len(shared) == 2
    assert sum(1 for d in shared if d.status == "loadable") == 1
    disabled = [d for d in shared if d.status == "disabled"]
    assert len(disabled) == 1
    assert disabled[0].reason == REASON_DUPLICATE_NAME


def test_manifest_symlink_escaping_is_unsafe(tmp_path: Path) -> None:
    # A manifest symlink pointing outside the extension directory must
    # never be read or trusted.
    workspace = _make_workspace(tmp_path)
    outside_manifest = tmp_path / "evil.toml"
    outside_manifest.write_text('name = "evil"\n', encoding="utf-8")
    directory = _write_dir_extension(workspace, "host")
    (directory / "pipy-extension.toml").symlink_to(outside_manifest)

    descriptor = _by_name(_discover(workspace), "host")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_UNSAFE_PATH


def test_disabled_symlink_label_is_literal_store_path(tmp_path: Path) -> None:
    # A symlinked candidate is labeled by its literal store path, never
    # its (outside) target path.
    workspace = _make_workspace(tmp_path)
    outside_dir = tmp_path / "outside_target"
    outside_dir.mkdir()
    (outside_dir / "extension.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (_ext_dir(workspace) / "linked").symlink_to(outside_dir, target_is_directory=True)

    descriptor = _by_name(_discover(workspace), "linked")

    assert descriptor.path_label == ".pipy/extensions/linked"
    assert "outside_target" not in descriptor.path_label


def test_manifest_version_secret_shaped_is_invalid(tmp_path: Path) -> None:
    # `version` is emitted into archive-safe metadata, so secret-shaped
    # version text must fail closed rather than leak.
    workspace = _make_workspace(tmp_path)
    _write_dir_extension(
        workspace, "vers", manifest='name = "vers"\nversion = "1.0-secret"\n'
    )

    descriptor = _by_name(_discover(workspace), "vers")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_INVALID_MANIFEST


def test_manifest_name_secret_shaped_is_invalid(tmp_path: Path) -> None:
    # A safe filename must not be able to declare a secret-shaped
    # manifest name that then reaches archive-safe metadata.
    workspace = _make_workspace(tmp_path)
    _write_dir_extension(workspace, "innocent", manifest='name = "secret-store"\n')

    descriptors = _discover(workspace)

    assert not any(d.name == "secret-store" for d in descriptors)
    descriptor = _by_name(descriptors, "innocent")
    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_INVALID_NAME


def test_directory_at_manifest_path_is_unsafe(tmp_path: Path) -> None:
    # A non-regular-file occupying the reserved manifest path must fail
    # closed, not be treated as "no manifest" and loaded with defaults.
    workspace = _make_workspace(tmp_path)
    directory = _write_dir_extension(workspace, "weirdmanifest")
    (directory / "pipy-extension.toml").mkdir()

    descriptor = _by_name(_discover(workspace), "weirdmanifest")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_UNSAFE_PATH


def test_broken_manifest_symlink_is_unsafe(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    directory = _write_dir_extension(workspace, "brokenlink")
    (directory / "pipy-extension.toml").symlink_to(tmp_path / "nonexistent.toml")

    descriptor = _by_name(_discover(workspace), "brokenlink")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_UNSAFE_PATH


def test_secret_named_extension_is_disabled_and_redacted(tmp_path: Path) -> None:
    # A secret-shaped filename must produce a visible disabled record
    # (deterministic inventory) WITHOUT leaking the sensitive name into
    # any descriptor field or archive-safe metadata.
    workspace = _make_workspace(tmp_path)
    _write_single_file(workspace, "my-api-key-helper")

    descriptors = _discover(workspace)

    # The sensitive name never appears anywhere in the inventory.
    for descriptor in descriptors:
        assert "api-key" not in descriptor.name
        assert "api-key" not in descriptor.path_label
    for entry in safe_extension_metadata(descriptors):
        assert "api-key" not in json.dumps(entry)
    # But a disabled record with a safe reason code exists.
    unsafe = [d for d in descriptors if d.reason == REASON_UNSAFE_NAME]
    assert len(unsafe) == 1
    assert unsafe[0].status == "disabled"


def test_binary_entry_file_is_disabled(tmp_path: Path) -> None:
    # A binary (NUL-containing) entry file must fail closed during
    # discovery, not be inventoried as loadable.
    workspace = _make_workspace(tmp_path)
    directory = _ext_dir(workspace) / "binext"
    directory.mkdir()
    (directory / "extension.py").write_bytes(b"\x00\x01\x02 not python\x00")

    descriptor = _by_name(_discover(workspace), "binext")

    assert descriptor.status == "disabled"
    assert descriptor.reason == REASON_BINARY_ENTRY


def test_discovery_never_imports_or_executes_extension_code(tmp_path: Path) -> None:
    """The inventory boundary must not run any top-level extension code."""

    workspace = _make_workspace(tmp_path)
    directory = _write_dir_extension(
        workspace,
        "sideeffect",
        entry_body=(
            "from pathlib import Path\n"
            "(Path(__file__).parent / 'EXECUTED').write_text('ran')\n"
            "raise RuntimeError('top-level extension code must not run')\n"
            "\n"
            "def activate(api):\n"
            "    pass\n"
        ),
    )
    modules_before = set(sys.modules)

    descriptors = _discover(workspace)

    # The descriptor was inventoried...
    descriptor = _by_name(descriptors, "sideeffect")
    assert descriptor.status == "loadable"
    # ...but the entry module's top-level side effect never ran.
    assert not (directory / "EXECUTED").exists()
    # ...and no extension module entered the import system.
    new_modules = set(sys.modules) - modules_before
    assert not any("sideeffect" in mod for mod in new_modules)


def test_safe_metadata_excludes_source_and_manifest_body(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    manifest = 'name = "labelled"\nversion = "1.2.3-local"\ndescription = "secret words here"\n'
    _write_dir_extension(workspace, "labelled", manifest=manifest)

    descriptors = _discover(workspace)
    metadata = safe_extension_metadata(descriptors)

    entry = next(m for m in metadata if m["name"] == "labelled")
    assert set(entry) == {
        "name",
        "version",
        "api_version",
        "source_kind",
        "kind",
        "path_label",
        "manifest_present",
        "status",
        "reason",
        "sha256",
        "byte_length",
    }
    # The description text must never reach archive-safe metadata.
    assert "secret words here" not in str(entry)
