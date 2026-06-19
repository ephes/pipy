"""Hard conformance gate for the extension activation slice (slice 2).

Builds a temporary workspace of extension fixtures, discovers them with
the real `discover_extensions`, then activates with the real
`activate_extensions`, and asserts the slice-2 invariants from
`docs/extension-api.md`:

1. a loadable extension's `activate(api)` runs and `register_command`
   records the command;
2. an async `activate` is awaited;
3. a missing/non-callable `activate` disables the extension
   (`no_activate`);
4. an exception in `activate` disables it (`activation_error`) without
   crashing the run;
5. an import-time error disables it (`import_error`);
6. an invalid command name disables it (`invalid_command_name`);
7. a reserved (built-in) command name disables it (`reserved_command`);
8. a command name already taken by another extension disables the later
   one (`duplicate_command`);
9. a discovery-disabled descriptor is NEVER imported (its top-level side
   effect must not run);
10. `safe_activation_metadata` carries only safe labels (no handlers).

Exits 0 when every check passes, 1 otherwise. No network.

Run:

    uv run python scripts/parity_checks/extension_activation_conformance.py --json
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native.extension_runtime import (
    ActivatedExtension,
    activate_extensions,
    safe_activation_metadata,
)
from pipy_harness.native.extensions import discover_extensions


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def _single(workspace: Path, name: str, body: str) -> None:
    ext = workspace / ".pipy" / "extensions"
    ext.mkdir(parents=True, exist_ok=True)
    (ext / f"{name}.py").write_text(body, encoding="utf-8")


def _by_name(result: list[ActivatedExtension], name: str) -> ActivatedExtension | None:
    matches = [a for a in result if a.name == name]
    return matches[0] if len(matches) == 1 else None


def _populate(workspace: Path) -> Path:
    ext = workspace / ".pipy" / "extensions"
    ext.mkdir(parents=True)

    _single(
        workspace,
        "greeter",
        "def activate(api):\n"
        "    api.register_command('greet', 'hi', lambda ctx, args: None)\n",
    )
    _single(
        workspace,
        "asyncext",
        "async def activate(api):\n"
        "    api.register_command('ah', 'async', lambda ctx, args: None)\n",
    )
    _single(workspace, "noact", "x = 1\n")
    _single(workspace, "boom", "def activate(api):\n    raise RuntimeError('x')\n")
    _single(workspace, "broken", "import nonexistent_pkg_xyz\n")
    _single(
        workspace,
        "badcmd",
        "def activate(api):\n"
        "    api.register_command('Bad Name', 'x', lambda ctx, args: None)\n",
    )
    _single(
        workspace,
        "shadow",
        "def activate(api):\n"
        "    api.register_command('model', 'x', lambda ctx, args: None)\n",
    )
    _single(
        workspace,
        "aaa",
        "def activate(api):\n"
        "    api.register_command('dup', 'first', lambda ctx, args: None)\n",
    )
    _single(
        workspace,
        "bbb",
        "def activate(api):\n"
        "    api.register_command('dup', 'second', lambda ctx, args: None)\n",
    )

    _single(
        workspace,
        "evilattr",
        "def __getattr__(name):\n    raise RuntimeError('boom')\n",
    )
    _single(
        workspace,
        "swallow",
        "def activate(api):\n"
        "    api.register_command('okcmd', 'first', lambda ctx, args: None)\n"
        "    try:\n"
        "        api.register_command('Bad Name', 'x', lambda ctx, args: None)\n"
        "    except Exception:\n"
        "        pass\n",
    )

    # directory extension using a relative import within its own dir
    pkgext = ext / "pkgext"
    pkgext.mkdir()
    (pkgext / "helper.py").write_text("GREETING = 'hi'\n", encoding="utf-8")
    (pkgext / "extension.py").write_text(
        "from .helper import GREETING\n"
        "def activate(api):\n"
        "    api.register_command('g', GREETING, lambda ctx, args: None)\n",
        encoding="utf-8",
    )

    # discovery-disabled extension with a side-effecting entry
    directory = ext / "disabledext"
    directory.mkdir()
    sentinel = directory / "EXECUTED"
    (directory / "extension.py").write_text(
        "from pathlib import Path\n"
        "(Path(__file__).parent / 'EXECUTED').write_text('ran')\n"
        "def activate(api):\n    pass\n",
        encoding="utf-8",
    )
    (directory / "pipy-extension.toml").write_text(
        'name = "disabledext"\n[entry]\nmodule = "missing_module"\n', encoding="utf-8"
    )
    return sentinel


def run_checks(workspace: Path) -> list[Check]:
    sentinel = _populate(workspace)
    descriptors = discover_extensions(
        workspace, config_home_env={}, home_dir=workspace
    )
    result = activate_extensions(descriptors, reserved_command_names=("model", "help"))
    checks: list[Check] = []

    def reason_is(name: str, status: str, reason: str | None) -> bool:
        item = _by_name(result, name)
        return item is not None and item.status == status and item.reason == reason

    greeter = _by_name(result, "greeter")
    checks.append(
        Check(
            "activate_registers_command",
            greeter is not None
            and greeter.status == "activated"
            and [c.name for c in greeter.commands] == ["greet"],
            "activate ran and registered a command",
        )
    )
    asyncext = _by_name(result, "asyncext")
    checks.append(
        Check(
            "async_activate_awaited",
            asyncext is not None
            and asyncext.status == "activated"
            and [c.name for c in asyncext.commands] == ["ah"],
            "async activate awaited",
        )
    )
    checks.append(
        Check(
            "missing_activate_disabled",
            reason_is("noact", "disabled", "no_activate"),
            "missing activate disabled",
        )
    )
    checks.append(
        Check(
            "activation_exception_disabled",
            reason_is("boom", "disabled", "activation_error"),
            "activation exception disabled, run survived",
        )
    )
    checks.append(
        Check(
            "import_error_disabled",
            reason_is("broken", "disabled", "import_error"),
            "import error disabled",
        )
    )
    checks.append(
        Check(
            "invalid_command_disabled",
            reason_is("badcmd", "disabled", "invalid_command_name"),
            "invalid command name disabled",
        )
    )
    checks.append(
        Check(
            "reserved_command_disabled",
            reason_is("shadow", "disabled", "reserved_command"),
            "reserved command name disabled",
        )
    )
    aaa = _by_name(result, "aaa")
    checks.append(
        Check(
            "duplicate_command_disabled",
            aaa is not None
            and aaa.status == "activated"
            and reason_is("bbb", "disabled", "duplicate_command"),
            "duplicate command disables the later extension",
        )
    )
    checks.append(
        Check(
            "disabled_descriptor_not_imported",
            reason_is("disabledext", "disabled", "missing_entry")
            and not sentinel.exists(),
            "discovery-disabled descriptor never imported",
        )
    )

    checks.append(
        Check(
            "module_getattr_raise_disabled",
            reason_is("evilattr", "disabled", "activation_error"),
            "module __getattr__ raising disabled, pass survived",
        )
    )
    swallow = _by_name(result, "swallow")
    checks.append(
        Check(
            "swallowed_registration_disabled",
            swallow is not None
            and swallow.status == "disabled"
            and swallow.reason == "invalid_command_name"
            and not swallow.commands,
            "swallowed bad registration disables, no partial commit",
        )
    )

    pkgext = _by_name(result, "pkgext")
    checks.append(
        Check(
            "relative_import_supported",
            pkgext is not None
            and pkgext.status == "activated"
            and len(pkgext.commands) == 1
            and pkgext.commands[0].description == "hi",
            "directory extension relative import works",
        )
    )

    metadata = safe_activation_metadata(result)
    greeter_meta = next((m for m in metadata if m["name"] == "greeter"), None)
    allowed = {
        "name",
        "version",
        "path_label",
        "status",
        "reason",
        "commands",
        "message_renderers",
    }
    checks.append(
        Check(
            "safe_metadata",
            greeter_meta is not None
            and set(greeter_meta) == allowed
            and greeter_meta["commands"] == ["greet"]
            and greeter_meta["message_renderers"] == []
            and "function" not in json.dumps(metadata).lower(),
            "activation metadata excludes handlers",
        )
    )
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "work"
        workspace.mkdir()
        checks = run_checks(workspace)

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
