"""Slice 2 tests for the extension activation sandbox boundary.

Slice 2 imports an explicit, already-inventoried *loadable* extension
module, calls its `activate(api)`, and supports command registration
only. Every failure mode (import error, missing/!callable activate,
activation exception, invalid/duplicate/reserved command name) disables
that one extension with a safe reason code instead of crashing the
session. Disabled discovery descriptors are never imported.
"""

from __future__ import annotations

from pathlib import Path

from pipy_harness.extensions import PipyExtensionAPI
from pipy_harness.native.extension_runtime import (
    ActivatedExtension,
    activate_extensions,
    extension_flags,
    parse_extension_flag_tokens,
    safe_activation_metadata,
)
from pipy_harness.native.extensions import discover_extensions


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


def _ext_dir(workspace: Path) -> Path:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write_single_file(workspace: Path, name: str, body: str) -> None:
    (_ext_dir(workspace) / f"{name}.py").write_text(body, encoding="utf-8")


def _activate(workspace: Path, *, reserved: tuple[str, ...] = ()) -> list[ActivatedExtension]:
    descriptors = discover_extensions(
        workspace, config_home_env={}, home_dir=workspace
    )
    return activate_extensions(descriptors, reserved_command_names=reserved)


def _by_name(result: list[ActivatedExtension], name: str) -> ActivatedExtension:
    for activated in result:
        if activated.name == name:
            return activated
    raise AssertionError(f"no activated extension {name!r} in {[a.name for a in result]}")


def test_activate_registers_a_command(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "greeter",
        "def activate(api):\n"
        "    api.register_command('greet', 'Say hi', lambda ctx, args: None)\n",
    )

    activated = _by_name(_activate(workspace), "greeter")

    assert activated.status == "activated"
    assert activated.reason is None
    assert [c.name for c in activated.commands] == ["greet"]
    assert activated.commands[0].description == "Say hi"
    assert callable(activated.commands[0].handler)


def test_async_activate_is_awaited(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "asyncext",
        "async def activate(api):\n"
        "    api.register_command('ah', 'async hi', lambda ctx, args: None)\n",
    )

    activated = _by_name(_activate(workspace), "asyncext")

    assert activated.status == "activated"
    assert [c.name for c in activated.commands] == ["ah"]


def test_activate_registers_extension_flags_and_parses_tokens(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "flagger",
        "from pipy_harness.extensions import ExtensionFlag\n"
        "def activate(api):\n"
        "    api.register_flag(ExtensionFlag('plan', 'boolean', default=False))\n"
        "    api.register_flag(ExtensionFlag('ticket', 'string'))\n",
    )

    registered = extension_flags(_activate(workspace))
    values, error = parse_extension_flag_tokens(
        registered,
        ("--plan", "--ticket", "PIPY-123"),
    )

    assert error is None
    assert values == {"plan": True, "ticket": "PIPY-123"}


def test_invalid_extension_flag_disables_only_that_extension(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "badflag",
        "from pipy_harness.extensions import ExtensionFlag\n"
        "def activate(api):\n"
        "    api.register_flag(ExtensionFlag('bad/name', 'boolean'))\n",
    )

    activated = _by_name(_activate(workspace), "badflag")

    assert activated.status == "disabled"
    assert activated.reason == "invalid_flag"
    assert not activated.flags


def test_unknown_extension_flag_token_fails_closed(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "flagger",
        "from pipy_harness.extensions import ExtensionFlag\n"
        "def activate(api):\n"
        "    api.register_flag(ExtensionFlag('known', 'boolean'))\n",
    )

    values, error = parse_extension_flag_tokens(
        extension_flags(_activate(workspace)),
        ("--unknown",),
    )

    assert values == {}
    assert error == "unknown extension flag: --unknown"


def test_extension_flag_parser_accepts_inline_values(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "flagger",
        "from pipy_harness.extensions import ExtensionFlag\n"
        "def activate(api):\n"
        "    api.register_flag(ExtensionFlag('plan', 'boolean', default=True))\n"
        "    api.register_flag(ExtensionFlag('ticket', 'string'))\n",
    )

    values, error = parse_extension_flag_tokens(
        extension_flags(_activate(workspace)),
        ("--plan=false", "--ticket=PIPY-456"),
    )

    assert error is None
    assert values == {"plan": False, "ticket": "PIPY-456"}


def test_async_activate_within_running_event_loop(tmp_path: Path) -> None:
    # Activation may be driven from within a running event loop; an async
    # activate must still complete instead of failing with activation_error.
    import asyncio

    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "loopext",
        "async def activate(api):\n"
        "    api.register_command('lc', 'x', lambda ctx, args: None)\n",
    )

    async def driver() -> list[ActivatedExtension]:
        return _activate(workspace)

    result = asyncio.run(driver())
    activated = _by_name(result, "loopext")

    assert activated.status == "activated"
    assert [c.name for c in activated.commands] == ["lc"]


def test_diagnostic_excludes_raw_exception_message(tmp_path: Path) -> None:
    # A diagnostic must not echo raw exception text, which can carry
    # absolute paths or secrets.
    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "leakyboom",
        "def activate(api):\n"
        "    raise RuntimeError('/Users/private/leaked-abc123xyz')\n",
    )

    activated = _by_name(_activate(workspace), "leakyboom")

    assert activated.status == "disabled"
    assert activated.reason == "activation_error"
    diagnostic = activated.diagnostic or ""
    assert "/Users/private" not in diagnostic
    assert "leaked-abc123xyz" not in diagnostic


def test_directory_extension_supports_relative_import(tmp_path: Path) -> None:
    # A directory extension may use normal package semantics within its
    # own directory (relative import of a sibling helper module).
    workspace = _make_workspace(tmp_path)
    directory = _ext_dir(workspace) / "pkgext"
    directory.mkdir()
    (directory / "helper.py").write_text("GREETING = 'hi'\n", encoding="utf-8")
    (directory / "extension.py").write_text(
        "from .helper import GREETING\n"
        "def activate(api):\n"
        "    api.register_command('g', GREETING, lambda ctx, args: None)\n",
        encoding="utf-8",
    )

    activated = _by_name(_activate(workspace), "pkgext")

    assert activated.status == "activated"
    assert activated.commands[0].description == "hi"


def test_failed_import_purges_submodules(tmp_path: Path) -> None:
    # A submodule imported before an import-time failure must not leak in
    # sys.modules (per-extension isolation / fail-closed).
    import sys

    workspace = _make_workspace(tmp_path)
    directory = _ext_dir(workspace) / "leakpkg"
    directory.mkdir()
    (directory / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (directory / "extension.py").write_text(
        "from .helper import VALUE\n"
        "raise RuntimeError('fail after import')\n"
        "def activate(api):\n    pass\n",
        encoding="utf-8",
    )
    before = set(sys.modules)

    activated = _by_name(_activate(workspace), "leakpkg")

    assert activated.status == "disabled"
    assert activated.reason == "import_error"
    leaked = [m for m in set(sys.modules) - before if "leakpkg" in m]
    assert not leaked, f"leaked modules: {leaked}"


def test_entry_module_is_registered_in_sys_modules(tmp_path: Path) -> None:
    # `sys.modules[__name__]` must be valid during the entry module's
    # import (normal module semantics).
    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "selfref",
        "import sys\n"
        "assert __name__ in sys.modules\n"
        "def activate(api):\n"
        "    api.register_command('s', 'x', lambda ctx, args: None)\n",
    )

    activated = _by_name(_activate(workspace), "selfref")

    assert activated.status == "activated"


def test_keyboard_interrupt_during_activation_propagates(tmp_path: Path) -> None:
    # A user abort during activation must propagate, not be turned into a
    # disabled extension.
    import pytest

    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "intractivate",
        "def activate(api):\n    raise KeyboardInterrupt()\n",
    )
    descriptors = discover_extensions(
        workspace, config_home_env={}, home_dir=workspace
    )

    with pytest.raises(KeyboardInterrupt):
        activate_extensions(descriptors)


def test_missing_activate_is_disabled(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write_single_file(workspace, "noact", "x = 1\n")

    activated = _by_name(_activate(workspace), "noact")

    assert activated.status == "disabled"
    assert activated.reason == "no_activate"
    assert not activated.commands


def test_activation_exception_is_disabled(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "boom",
        "def activate(api):\n    raise RuntimeError('kaboom')\n",
    )

    activated = _by_name(_activate(workspace), "boom")

    assert activated.status == "disabled"
    assert activated.reason == "activation_error"
    # The diagnostic must not echo the raw exception message verbatim as
    # the only content; it stays a safe, bounded label.
    assert activated.diagnostic is not None


def test_import_error_is_disabled(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    # Valid UTF-8 text (passes discovery) but raises at import time.
    _write_single_file(workspace, "broken", "import nonexistent_pkg_xyz\n")

    activated = _by_name(_activate(workspace), "broken")

    assert activated.status == "disabled"
    assert activated.reason == "import_error"


def test_module_getattr_raising_is_disabled(tmp_path: Path) -> None:
    # Resolving the entry function must be inside the fail-closed
    # boundary: a module-level __getattr__ that raises disables the
    # extension instead of crashing the whole activation pass.
    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "evilattr",
        "def __getattr__(name):\n    raise RuntimeError('boom')\n",
    )

    activated = _by_name(_activate(workspace), "evilattr")

    assert activated.status == "disabled"
    assert activated.reason == "activation_error"


def test_swallowed_invalid_registration_still_disables(tmp_path: Path) -> None:
    # If the extension swallows the registration error, the extension is
    # still disabled and NO earlier-staged command is committed.
    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "swallow",
        "def activate(api):\n"
        "    api.register_command('okcmd', 'first', lambda ctx, args: None)\n"
        "    try:\n"
        "        api.register_command('Bad Name', 'x', lambda ctx, args: None)\n"
        "    except Exception:\n"
        "        pass\n",
    )

    activated = _by_name(_activate(workspace), "swallow")

    assert activated.status == "disabled"
    assert activated.reason == "invalid_command_name"
    assert not activated.commands


def test_invalid_command_name_disables_extension(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "badcmd",
        "def activate(api):\n"
        "    api.register_command('Bad Name', 'x', lambda ctx, args: None)\n",
    )

    activated = _by_name(_activate(workspace), "badcmd")

    assert activated.status == "disabled"
    assert activated.reason == "invalid_command_name"
    assert not activated.commands


def test_reserved_command_name_disables_extension(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "shadow",
        "def activate(api):\n"
        "    api.register_command('model', 'shadow', lambda ctx, args: None)\n",
    )

    activated = _by_name(_activate(workspace, reserved=("model", "help")), "shadow")

    assert activated.status == "disabled"
    assert activated.reason == "reserved_command"


def test_duplicate_command_across_extensions_disables_second(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "aaa",
        "def activate(api):\n"
        "    api.register_command('dup', 'first', lambda ctx, args: None)\n",
    )
    _write_single_file(
        workspace,
        "bbb",
        "def activate(api):\n"
        "    api.register_command('dup', 'second', lambda ctx, args: None)\n",
    )

    result = _activate(workspace)
    aaa = _by_name(result, "aaa")
    bbb = _by_name(result, "bbb")

    assert aaa.status == "activated"
    assert bbb.status == "disabled"
    assert bbb.reason == "duplicate_command"


def test_disabled_discovery_descriptor_is_not_imported(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    # A directory extension with a side-effecting entry but a missing
    # manifest-declared module -> discovery disables it (missing_entry),
    # so activation must never import the side-effecting file.
    directory = _ext_dir(workspace) / "disabledext"
    directory.mkdir()
    sentinel = directory / "EXECUTED"
    (directory / "extension.py").write_text(
        "from pathlib import Path\n"
        "(Path(__file__).parent / 'EXECUTED').write_text('ran')\n"
        "def activate(api):\n    pass\n",
        encoding="utf-8",
    )
    (directory / "pipy-extension.toml").write_text(
        'name = "disabledext"\n[entry]\nmodule = "does_not_exist"\n', encoding="utf-8"
    )

    result = _activate(workspace)
    activated = _by_name(result, "disabledext")

    assert activated.status == "disabled"
    assert activated.reason == "missing_entry"
    assert not sentinel.exists()


def test_safe_activation_metadata_excludes_handlers(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "metaext",
        "def activate(api):\n"
        "    api.register_command('mc', 'desc', lambda ctx, args: None)\n",
    )

    result = _activate(workspace)
    metadata = safe_activation_metadata(result)

    entry = next(m for m in metadata if m["name"] == "metaext")
    assert set(entry) == {"name", "version", "path_label", "status", "reason", "commands"}
    assert entry["commands"] == ["mc"]
    # No handler objects or callables leak into metadata.
    assert "lambda" not in str(entry) and "function" not in str(entry).lower()


def test_public_api_protocol_is_importable() -> None:
    # The public extension surface the spec examples import from.
    assert hasattr(PipyExtensionAPI, "register_command")
