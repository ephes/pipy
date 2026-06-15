"""Hard conformance gate for the Python extension discovery slice (slice 1).

This script builds a temporary workspace and global config root populated
with a spread of Python extension candidates, runs the real
``pipy_harness.native.extensions.discover_extensions`` inventory, and
asserts the slice-1 invariants from ``docs/extension-api.md``:

1. a single-file workspace extension is discovered with safe inferred
   defaults (version ``0.0.0-local``, current api_version, all
   permissions ``false``);
2. a directory extension with ``pipy-extension.toml`` reports the
   declared name/version/description/permissions;
3. a directory extension with no entry module is disabled
   (``missing_entry``);
4. an invalid manifest is disabled (``invalid_manifest``);
5. a manifest targeting a newer major api_version is disabled
   (``unsupported_api_version``);
6. an entry symlink that escapes the extension directory is disabled
   (``unsafe_path``);
7. a duplicate name disables the later candidate (``duplicate_name``);
8. a global extension is discovered;
9. **no extension module is imported and no top-level extension code
   runs** — the inventory boundary is proven with an observable side
   effect that must never fire;
10. ``safe_extension_metadata`` carries only safe labels and never the
    manifest description text.

Exits 0 when every check passes, 1 otherwise. No network, no imports of
extension code.

Run:

    uv run python scripts/parity_checks/extension_discovery_conformance.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native.extensions import (
    CURRENT_API_VERSION,
    REASON_BINARY_ENTRY,
    REASON_DUPLICATE_NAME,
    REASON_INVALID_MANIFEST,
    REASON_MISSING_ENTRY,
    REASON_UNSAFE_NAME,
    REASON_UNSAFE_PATH,
    REASON_UNSUPPORTED_API_VERSION,
    ExtensionDescriptor,
    discover_extensions,
    safe_extension_metadata,
)


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def _by_name(
    descriptors: list[ExtensionDescriptor], name: str
) -> ExtensionDescriptor | None:
    matches = [d for d in descriptors if d.name == name]
    return matches[0] if len(matches) == 1 else None


def _populate(workspace: Path, config_home: Path) -> Path:
    ext = workspace / ".pipy" / "extensions"
    ext.mkdir(parents=True)

    # 1. single-file workspace extension
    (ext / "greet.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )

    # 2. directory extension with a full manifest
    protected = ext / "protected-paths"
    protected.mkdir()
    (protected / "extension.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (protected / "pipy-extension.toml").write_text(
        'name = "protected-paths"\n'
        'version = "0.3.1"\n'
        'api_version = "0.1"\n'
        'description = "guard secret words"\n'
        "[permissions]\n"
        "workspace_read = true\n"
        "ui = true\n",
        encoding="utf-8",
    )

    # 3. directory extension with no entry module
    (ext / "empty").mkdir()

    # 4. invalid manifest
    broken = ext / "broken"
    broken.mkdir()
    (broken / "extension.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (broken / "pipy-extension.toml").write_text("not = = valid [[[", encoding="utf-8")

    # 5. unsupported api major
    future = ext / "future"
    future.mkdir()
    (future / "extension.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (future / "pipy-extension.toml").write_text(
        'name = "future"\napi_version = "2.0"\n', encoding="utf-8"
    )

    # 5b. malformed api_version (only the major is numeric)
    weird = ext / "weird"
    weird.mkdir()
    (weird / "extension.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (weird / "pipy-extension.toml").write_text(
        'name = "weird"\napi_version = "0.not-a-version"\n', encoding="utf-8"
    )

    # 5c. unknown permission key
    perms = ext / "perms"
    perms.mkdir()
    (perms / "extension.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (perms / "pipy-extension.toml").write_text(
        'name = "perms"\n[permissions]\nworkspace_read = true\nbogus = true\n',
        encoding="utf-8",
    )

    # 5d. secret-shaped version string (safe name, sensitive version)
    versioned = ext / "versioned"
    versioned.mkdir()
    (versioned / "extension.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (versioned / "pipy-extension.toml").write_text(
        'name = "versioned"\nversion = "1.0-secret"\n', encoding="utf-8"
    )

    # 5e. present-but-empty fields fail closed (not defaulted)
    emptyver = ext / "emptyver"
    emptyver.mkdir()
    (emptyver / "extension.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (emptyver / "pipy-extension.toml").write_text(
        'name = "emptyver"\napi_version = ""\n', encoding="utf-8"
    )

    # 5h. secret-shaped entry.module (safe dir, sensitive module label)
    modtest = ext / "modtest"
    modtest.mkdir()
    (modtest / "my-api-key-helper.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (modtest / "pipy-extension.toml").write_text(
        'name = "modtest"\n[entry]\nmodule = "my-api-key-helper"\n', encoding="utf-8"
    )

    # 5g. secret-shaped filename (recorded disabled, but redacted)
    (ext / "my-api-key-helper.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )

    # 5f. binary (NUL-containing) entry file
    binext = ext / "binext"
    binext.mkdir()
    (binext / "extension.py").write_bytes(b"\x00\x01\x02 not python\x00")

    # 5i. non-UTF-8 binary entry without any NUL byte
    binext2 = ext / "binext2"
    binext2.mkdir()
    (binext2 / "extension.py").write_bytes(b"\xff\xfe\xfa not utf8 \xc3\x28")

    # 6. entry symlink that escapes the extension directory
    outside = workspace / "outside.py"
    outside.write_text("def activate(api):\n    pass\n", encoding="utf-8")
    escaper = ext / "escaper"
    escaper.mkdir()
    (escaper / "extension.py").symlink_to(outside)

    # 7. duplicate name (directory + single file)
    (ext / "dup").mkdir()
    (ext / "dup" / "extension.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (ext / "dup.py").write_text("def activate(api):\n    pass\n", encoding="utf-8")

    # 7b. entry symlink that stays in the store but escapes its own dir
    sibling = ext / "sibling.py"
    sibling.write_text("def activate(api):\n    pass\n", encoding="utf-8")
    sneaky = ext / "sneaky"
    sneaky.mkdir()
    (sneaky / "extension.py").symlink_to(sibling)

    # 7g. invalid-manifest extension still reserves its declared name
    decl_a = ext / "decl-a"
    decl_a.mkdir()
    (decl_a / "extension.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (decl_a / "pipy-extension.toml").write_text(
        'name = "declared"\n[permissions]\nbogus = true\n', encoding="utf-8"
    )
    decl_b = ext / "decl-b"
    decl_b.mkdir()
    (decl_b / "extension.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (decl_b / "pipy-extension.toml").write_text(
        'name = "declared"\n', encoding="utf-8"
    )

    # 7c. two directories declaring the same manifest name
    for dir_name in ("alpha", "bravo"):
        collide = ext / dir_name
        collide.mkdir()
        (collide / "extension.py").write_text(
            "def activate(api):\n    pass\n", encoding="utf-8"
        )
        (collide / "pipy-extension.toml").write_text(
            'name = "shared"\n', encoding="utf-8"
        )

    # 7d. symlinked extension directory escaping the store
    outside_dir = workspace / "outside_ext"
    outside_dir.mkdir()
    (outside_dir / "extension.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (ext / "linked").symlink_to(outside_dir, target_is_directory=True)

    # 7e. manifest symlink escaping the extension directory
    evil_manifest = workspace / "evil.toml"
    evil_manifest.write_text('name = "evil"\n', encoding="utf-8")
    hijack = ext / "hijack"
    hijack.mkdir()
    (hijack / "extension.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (hijack / "pipy-extension.toml").symlink_to(evil_manifest)

    # 7f. safe filename declaring a secret-shaped manifest name
    innocent = ext / "innocent"
    innocent.mkdir()
    (innocent / "extension.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (innocent / "pipy-extension.toml").write_text(
        'name = "secret-store"\n', encoding="utf-8"
    )

    # 8. global extension
    global_ext = config_home / "extensions"
    global_ext.mkdir(parents=True)
    (global_ext / "globby.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )

    # 9. non-execution sentinel: top-level code that must never run
    sideeffect = ext / "sideeffect"
    sideeffect.mkdir()
    sentinel = sideeffect / "EXECUTED"
    (sideeffect / "extension.py").write_text(
        "from pathlib import Path\n"
        "(Path(__file__).parent / 'EXECUTED').write_text('ran')\n"
        "raise RuntimeError('extension top-level code must not run')\n"
        "\n"
        "def activate(api):\n"
        "    pass\n",
        encoding="utf-8",
    )
    return sentinel


def run_checks(workspace: Path, config_home: Path) -> list[Check]:
    sentinel = _populate(workspace, config_home)
    descriptors = discover_extensions(
        workspace,
        config_home_env={"PIPY_CONFIG_HOME": str(config_home)},
        home_dir=workspace,
    )
    checks: list[Check] = []

    greet = _by_name(descriptors, "greet")
    checks.append(
        Check(
            "single_file_defaults",
            greet is not None
            and greet.status == "loadable"
            and greet.kind == "single_file"
            and greet.version == "0.0.0-local"
            and greet.api_version == CURRENT_API_VERSION
            and all(v is False for v in greet.permissions.values()),
            "single-file extension inferred safe defaults",
        )
    )

    protected = _by_name(descriptors, "protected-paths")
    checks.append(
        Check(
            "manifest_inventory",
            protected is not None
            and protected.status == "loadable"
            and protected.version == "0.3.1"
            and protected.manifest_present is True
            and protected.permissions.get("workspace_read") is True
            and protected.permissions.get("ui") is True
            and protected.permissions.get("network") is False,
            "directory manifest fields/permissions inventoried",
        )
    )

    empty = _by_name(descriptors, "empty")
    checks.append(
        Check(
            "missing_entry_disabled",
            empty is not None
            and empty.status == "disabled"
            and empty.reason == REASON_MISSING_ENTRY,
            "directory without entry module disabled",
        )
    )

    broken = _by_name(descriptors, "broken")
    checks.append(
        Check(
            "invalid_manifest_disabled",
            broken is not None
            and broken.status == "disabled"
            and broken.reason == REASON_INVALID_MANIFEST,
            "invalid manifest disabled",
        )
    )

    future = _by_name(descriptors, "future")
    checks.append(
        Check(
            "unsupported_api_disabled",
            future is not None
            and future.status == "disabled"
            and future.reason == REASON_UNSUPPORTED_API_VERSION,
            "newer major api_version disabled",
        )
    )

    all_metadata_json = json.dumps(safe_extension_metadata(descriptors), sort_keys=True)
    unsafe_named = [d for d in descriptors if d.reason == REASON_UNSAFE_NAME]
    checks.append(
        Check(
            "unsafe_name_redacted",
            len(unsafe_named) == 1
            and unsafe_named[0].status == "disabled"
            and "api-key" not in all_metadata_json
            and all("api-key" not in d.path_label for d in descriptors),
            "secret-shaped name recorded disabled but redacted, no leak",
        )
    )

    declared = [d for d in descriptors if d.name == "declared"]
    declared_reasons = {d.reason for d in declared}
    checks.append(
        Check(
            "invalid_manifest_reserves_name",
            len(declared) == 2
            and all(d.status == "disabled" for d in declared)
            and REASON_INVALID_MANIFEST in declared_reasons
            and REASON_DUPLICATE_NAME in declared_reasons,
            "invalid-manifest extension reserves its declared name",
        )
    )

    binext = _by_name(descriptors, "binext")
    checks.append(
        Check(
            "binary_entry_disabled",
            binext is not None
            and binext.status == "disabled"
            and binext.reason == REASON_BINARY_ENTRY,
            "binary entry file fails closed",
        )
    )

    modtest = _by_name(descriptors, "modtest")
    checks.append(
        Check(
            "secret_entry_module_disabled",
            modtest is not None
            and modtest.status == "disabled"
            and modtest.reason == REASON_INVALID_MANIFEST
            and "api-key" not in modtest.entry_module
            and "api-key" not in modtest.entry_path_label,
            "secret-shaped entry.module fails closed without leak",
        )
    )

    binext2 = _by_name(descriptors, "binext2")
    checks.append(
        Check(
            "non_utf8_binary_disabled",
            binext2 is not None
            and binext2.status == "disabled"
            and binext2.reason == REASON_BINARY_ENTRY,
            "non-UTF-8 entry without NUL fails closed",
        )
    )

    emptyver = _by_name(descriptors, "emptyver")
    checks.append(
        Check(
            "present_empty_field_disabled",
            emptyver is not None
            and emptyver.status == "disabled"
            and emptyver.reason == REASON_INVALID_MANIFEST,
            "present-but-empty api_version fails closed",
        )
    )

    weird = _by_name(descriptors, "weird")
    checks.append(
        Check(
            "malformed_api_version_disabled",
            weird is not None
            and weird.status == "disabled"
            and weird.reason == REASON_INVALID_MANIFEST,
            "non-version api_version disabled",
        )
    )

    perms = _by_name(descriptors, "perms")
    checks.append(
        Check(
            "unknown_permission_disabled",
            perms is not None
            and perms.status == "disabled"
            and perms.reason == REASON_INVALID_MANIFEST,
            "unknown permission key disabled",
        )
    )

    versioned = _by_name(descriptors, "versioned")
    no_version_leak = all(
        "secret" not in str(m.get("version", ""))
        for m in safe_extension_metadata(descriptors)
    )
    checks.append(
        Check(
            "version_secret_screened",
            versioned is not None
            and versioned.status == "disabled"
            and versioned.reason == REASON_INVALID_MANIFEST
            and no_version_leak,
            "secret-shaped version rejected, not emitted",
        )
    )

    escaper = _by_name(descriptors, "escaper")
    checks.append(
        Check(
            "unsafe_path_disabled",
            escaper is not None
            and escaper.status == "disabled"
            and escaper.reason == REASON_UNSAFE_PATH,
            "escaping entry symlink disabled",
        )
    )

    dups = [d for d in descriptors if d.name == "dup"]
    loadable = [d for d in dups if d.status == "loadable"]
    disabled = [d for d in dups if d.status == "disabled"]
    checks.append(
        Check(
            "duplicate_name_disabled",
            len(dups) == 2
            and len(loadable) == 1
            and len(disabled) == 1
            and disabled[0].reason == REASON_DUPLICATE_NAME,
            "duplicate name disables later candidate",
        )
    )

    sneaky = _by_name(descriptors, "sneaky")
    checks.append(
        Check(
            "own_dir_containment",
            sneaky is not None
            and sneaky.status == "disabled"
            and sneaky.reason == REASON_UNSAFE_PATH,
            "entry symlink escaping its own dir disabled",
        )
    )

    shared = [d for d in descriptors if d.name == "shared"]
    shared_loadable = [d for d in shared if d.status == "loadable"]
    shared_disabled = [d for d in shared if d.status == "disabled"]
    checks.append(
        Check(
            "manifest_name_dedup",
            len(shared) == 2
            and len(shared_loadable) == 1
            and len(shared_disabled) == 1
            and shared_disabled[0].reason == REASON_DUPLICATE_NAME,
            "duplicate manifest name across dirs deduplicated",
        )
    )

    linked = _by_name(descriptors, "linked")
    checks.append(
        Check(
            "symlinked_dir_disabled",
            linked is not None
            and linked.status == "disabled"
            and linked.reason == REASON_UNSAFE_PATH,
            "symlinked extension directory escaping store disabled",
        )
    )

    hijack = _by_name(descriptors, "hijack")
    checks.append(
        Check(
            "manifest_symlink_unsafe",
            hijack is not None
            and hijack.status == "disabled"
            and hijack.reason == REASON_UNSAFE_PATH,
            "manifest symlink escaping the dir disabled",
        )
    )

    secret_name_leaked = any(d.name == "secret-store" for d in descriptors)
    innocent = _by_name(descriptors, "innocent")
    checks.append(
        Check(
            "manifest_name_screened",
            not secret_name_leaked
            and innocent is not None
            and innocent.status == "disabled",
            "secret-shaped manifest name rejected, not emitted",
        )
    )

    globby = _by_name(descriptors, "globby")
    checks.append(
        Check(
            "global_discovered",
            globby is not None
            and globby.source_kind == "global"
            and globby.status == "loadable",
            "global extension discovered",
        )
    )

    sideeffect = _by_name(descriptors, "sideeffect")
    no_module = not any("sideeffect" in mod for mod in sys.modules)
    checks.append(
        Check(
            "no_execution",
            sideeffect is not None
            and sideeffect.status == "loadable"
            and not sentinel.exists()
            and no_module,
            "no top-level extension code ran and no module was imported",
        )
    )

    metadata = safe_extension_metadata(descriptors)
    protected_meta = next((m for m in metadata if m["name"] == "protected-paths"), None)
    allowed_keys = {
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
    no_leak = all(
        "guard secret words" not in json.dumps(m, sort_keys=True) for m in metadata
    )
    checks.append(
        Check(
            "safe_metadata",
            protected_meta is not None
            and set(protected_meta) == allowed_keys
            and no_leak,
            "archive-safe metadata excludes description/source",
        )
    )

    # Isolated workspace: a symlinked `.pipy` ancestor that escapes the
    # workspace must not surface outside code as a loadable extension.
    escape_ws = workspace.parent / "escape_ws"
    escape_ws.mkdir()
    outside_store = workspace.parent / "outside_store"
    (outside_store / "extensions").mkdir(parents=True)
    (outside_store / "extensions" / "evil.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    (escape_ws / ".pipy").symlink_to(outside_store, target_is_directory=True)
    escape_descriptors = discover_extensions(
        escape_ws,
        config_home_env={"PIPY_CONFIG_HOME": str(workspace.parent / "no_cfg")},
        home_dir=escape_ws,
    )
    checks.append(
        Check(
            "symlinked_ancestor_ignored",
            not any(d.name == "evil" for d in escape_descriptors),
            "symlinked .pipy ancestor escaping workspace not loadable",
        )
    )

    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        workspace = base / "work"
        workspace.mkdir()
        config_home = base / "cfg"
        checks = run_checks(workspace, config_home)

    passed = all(c.passed for c in checks)
    if args.json:
        report = {
            "passed": passed,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in checks
            ],
        }
        print(json.dumps(report, indent=2))
    else:
        for c in checks:
            status = "PASS" if c.passed else "FAIL"
            print(f"[{status}] {c.name}: {c.detail}")
        print("ALL PASS" if passed else "FAILURES PRESENT")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
