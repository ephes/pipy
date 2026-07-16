"""Deterministic conformance gate for project-trust core parity.

Run::

    uv run python scripts/parity_checks/project_trust_conformance.py --json

The gate exercises the pipy-owned store, protected-input detector, settings
isolation, resource provenance, decision order, CLI override parsing, and
final-session-cwd startup seam. It performs no network or model calls.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from unittest.mock import patch

from pipy_harness.cli import KNOWN_SUBCOMMANDS, build_parser, route_argv
from pipy_harness.native import FakeNativeProvider, NativeToolReplSession
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.package_runtime import compose_package_runtime
from pipy_harness.native.project_trust import (
    PROTECTED_PROJECT_ENTRIES,
    ProjectTrustStore,
    get_project_trust_options,
    has_trust_requiring_project_resources,
    resolve_project_trust,
    resolve_project_trusted,
)
from pipy_harness.native.resources import WorkspaceResources
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.system_prompt_inputs import resolve_system_prompt


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def _write_json(path: Path, body: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def _resource(path: Path, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {name}\n---\n{name}\n")


def _store_check(root: Path) -> Check:
    parent = root / "work"
    child = parent / "child"
    child.mkdir(parents=True)
    store = ProjectTrustStore(root / "config" / "trust.json")
    store.set(parent, True)
    store.set(child, False)
    closest = store.get(child) is False
    store.set(child, None)
    inherited = store.get(child) is True
    sorted_keys = list(json.loads(store.path.read_text())) == sorted(
        json.loads(store.path.read_text())
    )
    return Check(
        "store_ancestry_atomic",
        closest and inherited and sorted_keys,
        "closest/delete/sorted",
    )


def _detector_check(root: Path) -> Check:
    results: list[bool] = []
    for index, entry in enumerate(PROTECTED_PROJECT_ENTRIES):
        cwd = root / f"detector-{index}"
        candidate = cwd / ".pipy" / entry
        candidate.parent.mkdir(parents=True)
        if "." in Path(entry).name:
            candidate.write_text("x")
        else:
            candidate.mkdir()
        results.append(has_trust_requiring_project_resources(cwd))
    bare = root / "bare"
    (bare / ".pipy" / "themes").mkdir(parents=True)
    (bare / "AGENTS.md").write_text("context")
    return Check(
        "protected_detector",
        all(results) and not has_trust_requiring_project_resources(bare),
        "protected/exempt table",
    )


def _settings_check(root: Path) -> Check:
    global_path = root / "settings" / "global.json"
    project_path = root / "settings" / "project" / ".pipy" / "settings.json"
    global_package = root / "settings" / "global-package"
    project_package = root / "settings" / "project-package"
    (global_package / "themes").mkdir(parents=True)
    (project_package / "themes").mkdir(parents=True)
    _write_json(
        global_path,
        {
            "theme": "global",
            "defaultProjectTrust": "always",
            "packages": [str(global_package)],
        },
    )
    project_path.parent.mkdir(parents=True)
    _write_json(project_path, {"packages": [str(project_package)]})
    manager = SettingsManager(
        global_path=global_path,
        project_path=project_path,
        project_trusted=False,
    )
    unread = manager.get_theme() == "global" and "project" not in manager.load_errors()
    try:
        manager.set_theme("x", scope="project")
        refused = False
    except RuntimeError:
        refused = True
    global_only = manager.get_default_project_trust() == "always"
    roots = compose_package_runtime(
        manager, project_path.parents[1], install_theme_registry=False
    )
    themes_gated = [entry.path for entry in roots.themes] == [
        (global_package / "themes").resolve()
    ]
    return Check(
        "settings_gate",
        unread and refused and global_only and themes_gated,
        "unread/refused/global-only/project-package-theme-gated",
    )


def _resource_check(root: Path) -> Check:
    cwd = root / "resources" / "workspace"
    config = root / "resources" / "config"
    cwd.mkdir(parents=True)
    for kind in ("skills", "templates", "commands"):
        _resource(cwd / ".pipy" / kind / "project.md", f"project-{kind}")
        _resource(config / kind / "global.md", f"global-{kind}")
    explicit = root / "resources" / "explicit.md"
    _resource(explicit, "explicit-skill")
    resources = WorkspaceResources.discover(
        cwd,
        config_home_env={"PIPY_CONFIG_HOME": str(config)},
        explicit_skill_paths=(explicit,),
        include_workspace_defaults=False,
    )
    names = (
        *resources.skill_names(),
        *resources.template_names(),
        *resources.custom_command_slash_names(),
    )
    sources_ok = (
        "explicit-skill" in names
        and any("global" in name for name in names)
        and not any("project" in name for name in names)
    )

    (cwd / ".pipy" / "extensions").mkdir()
    (cwd / ".pipy" / "extensions" / "project.py").write_text(
        "def activate(api): pass\n"
    )
    (config / "extensions").mkdir()
    (config / "extensions" / "global.py").write_text("def activate(api): pass\n")
    ext_names = [
        item.name
        for item in discover_extensions(
            cwd,
            config_home_env={"PIPY_CONFIG_HOME": str(config)},
            include_workspace_defaults=False,
        )
    ]
    prompt = resolve_system_prompt(
        "default",
        cwd=cwd,
        config_home=config,
        include_project_defaults=False,
    )
    return Check(
        "resource_provenance",
        sources_ok
        and ext_names == ["global"]
        and "project" not in prompt.base_prompt.lower(),
        "workspace blocked; global/explicit retained",
    )


def _resolver_check(root: Path) -> Check:
    cwd = root / "resolver"
    (cwd / ".pipy" / "skills").mkdir(parents=True)
    store = ProjectTrustStore(root / "resolver-config" / "trust.json")
    ask_false = not resolve_project_trusted(cwd, trust_store=store)
    always_true = resolve_project_trusted(
        cwd, trust_store=store, default_project_trust="always"
    )
    store.set(cwd.parent, False)
    saved_wins = not resolve_project_trusted(
        cwd, trust_store=store, default_project_trust="always"
    )
    override_wins = resolve_project_trusted(cwd, trust_store=store, trust_override=True)
    return Check(
        "resolver_order",
        ask_false and always_true and saved_wins and override_wins,
        "override/no-resource/saved/default/headless",
    )


def _cli_check(_root: Path) -> Check:
    parser = build_parser()
    args = parser.parse_args(route_argv(["-a", "-na", "-a"], KNOWN_SUBCOMMANDS))
    ok = args.command == "repl" and args.trust_override is True
    return Check("cli_last_override", ok, "top-level routing + sequential flags")


def _interactive_management_check(root: Path) -> Check:
    cwd = root / "interactive" / "child"
    (cwd / ".pipy" / "skills").mkdir(parents=True)
    options = get_project_trust_options(cwd, include_session_only=True)
    labels_ok = [option.label for option in options] == [
        "Trust",
        f"Trust parent folder ({cwd.parent.resolve()})",
        "Trust (this session only)",
        "Do not trust",
        "Do not trust (this session only)",
    ]
    store = ProjectTrustStore(root / "interactive-config" / "trust.json")
    resolution = resolve_project_trust(
        cwd,
        trust_store=store,
        select=lambda _cwd: options[1],
    )
    parent_saved = (
        resolution.trusted
        and resolution.source == "selection"
        and store.get_entry(cwd) is not None
        and store.get_entry(cwd).path == cwd.parent.resolve()  # type: ignore[union-attr]
    )
    manager = SettingsManager(
        global_path=root / "interactive-settings" / "settings.json",
        project_path=cwd / ".pipy" / "settings.json",
        project_trusted=False,
    )
    manager.set_default_project_trust("never")
    global_enum = manager.get_default_project_trust() == "never"
    parser = build_parser()
    package_args = parser.parse_args(
        ["install", "./pkg", "-l", "--no-approve", "--approve"]
    )
    config_args = parser.parse_args(["config", "-l", "-a", "-na"])
    command_flags = (
        package_args.local
        and package_args.trust_override is True
        and config_args.scope == "project"
        and config_args.trust_override is False
    )
    return Check(
        "interactive_management",
        labels_ok and parent_saved and global_enum and command_flags,
        "five choices/parent atomic save/global enum/management overrides",
    )


def _reload_persistence_check(root: Path) -> Check:
    config = root / "reload-config"
    cwd = root / "reload" / "project"
    cwd.mkdir(parents=True)
    settings = SettingsManager.for_workspace(cwd, project_trusted=True)

    with patch.dict(os.environ, {"PIPY_CONFIG_HOME": str(config)}):
        session = NativeToolReplSession(
            provider=FakeNativeProvider(supports_tool_calls=True),
            tool_registry={},
            auto_trust_on_reload_cwd=cwd,
        )
        no_resource_guard = not session._maybe_save_implicit_trust_after_reload(
            cwd=cwd,
            settings=settings,
            terminal_ui=None,
            error_stream=io.StringIO(),
        )
        (cwd / ".pipy" / "skills").mkdir(parents=True)
        saved_new_resource = session._maybe_save_implicit_trust_after_reload(
            cwd=cwd,
            settings=settings,
            terminal_ui=None,
            error_stream=io.StringIO(),
        )
        store = ProjectTrustStore(config / "trust.json")
        exact_saved = store.get(cwd) is True
        candidate_consumed = session.auto_trust_on_reload_cwd is None

        inherited_cwd = root / "reload" / "inherited" / "project"
        (inherited_cwd / ".pipy" / "skills").mkdir(parents=True)
        store.set(inherited_cwd.parent, False)
        inherited_session = NativeToolReplSession(
            provider=FakeNativeProvider(supports_tool_calls=True),
            tool_registry={},
            auto_trust_on_reload_cwd=inherited_cwd,
        )
        saved_guard = not inherited_session._maybe_save_implicit_trust_after_reload(
            cwd=inherited_cwd,
            settings=SettingsManager.for_workspace(inherited_cwd, project_trusted=True),
            terminal_ui=None,
            error_stream=io.StringIO(),
        )
        inherited_preserved = store.get(inherited_cwd) is False

    return Check(
        "reload_persistence",
        no_resource_guard
        and saved_new_resource
        and exact_saved
        and candidate_consumed
        and saved_guard
        and inherited_preserved,
        "no-resource/new-resource/existing-decision guards",
    )


def _extension_product_check(root: Path) -> Check:
    cwd = root / "extension-product"
    config = root / "extension-config"
    proof = root / "extension-proof.txt"
    global_extension = config / "extensions" / "trust.py"
    global_extension.parent.mkdir(parents=True)
    global_extension.write_text(
        f"open({str(proof)!r}, 'a').write('g')\n"
        "def activate(api):\n"
        "    @api.on('project_trust')\n"
        "    def trust(event, ctx):\n"
        "        assert ctx.mode == 'json' and ctx.hasUI is False\n"
        "        assert ctx.ui.select('pick', ['yes']) is None\n"
        "        assert ctx.ui.confirm('confirm', 'message') is False\n"
        "        assert ctx.ui.input('input') is None\n"
        "        ctx.ui.notify('trust decided', 'info')\n"
        "        return {'trusted': 'yes'}\n"
        "    @api.on('session_start')\n"
        "    def started(event, ctx):\n"
        "        assert ctx.is_project_trusted() is True\n"
        "        assert ctx.isProjectTrusted() is True\n"
        f"        open({str(proof)!r}, 'a').write('G')\n",
        encoding="utf-8",
    )
    project_extension = cwd / ".pipy" / "extensions" / "project.py"
    project_extension.parent.mkdir(parents=True)
    project_extension.write_text(
        f"open({str(proof)!r}, 'a').write('p')\n"
        "def activate(api):\n"
        "    @api.on('session_start')\n"
        "    def started(event, ctx):\n"
        "        assert ctx.is_project_trusted() is True\n"
        "        assert ctx.isProjectTrusted() is True\n"
        f"        open({str(proof)!r}, 'a').write('P')\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.update(
        {
            "PIPY_CONFIG_HOME": str(config),
            "PIPY_NATIVE_DEFAULTS_PATH": str(root / "defaults.json"),
            "PIPY_AUTH_DIR": str(root / "auth"),
            "XDG_STATE_HOME": str(root / "state"),
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pipy_harness.cli",
            "repl",
            "--cwd",
            str(cwd),
            "--native-provider",
            "fake",
            "--native-model",
            "fake-tools",
            "--no-session",
            "--mode",
            "json",
            "ROOT",
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    try:
        records = [json.loads(line) for line in completed.stdout.splitlines()]
        protocol_only = bool(records) and records[0].get("type") == "session"
    except (json.JSONDecodeError, AttributeError):
        protocol_only = False
    return Check(
        "extension_decision_reuse",
        completed.returncode == 0
        and protocol_only
        and "trust decided" in completed.stderr
        and proof.read_text(encoding="utf-8") == "gpPG",
        "global decision/project gate/single activation/read aliases/protocol stdout",
    )


def run() -> list[Check]:
    with tempfile.TemporaryDirectory(prefix="pipy-trust-gate-") as raw:
        root = Path(raw)
        return [
            _store_check(root),
            _detector_check(root),
            _settings_check(root),
            _resource_check(root),
            _resolver_check(root),
            _cli_check(root),
            _interactive_management_check(root),
            _reload_persistence_check(root),
            _extension_product_check(root),
        ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    checks = run()
    passed = all(check.passed for check in checks)
    if args.json:
        print(
            json.dumps(
                {"passed": passed, "checks": [asdict(check) for check in checks]},
                indent=2,
            )
        )
    else:
        for check in checks:
            print(f"{'PASS' if check.passed else 'FAIL'} {check.name}: {check.detail}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
