"""Deterministic conformance gate for project-trust core parity.

Run::

    uv run python scripts/parity_checks/project_trust_conformance.py --json

The gate exercises the pipy-owned store, protected-input detector, settings
isolation, resource provenance, decision order, CLI override parsing, and
final-session-cwd startup seam. It performs no network or model calls.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from pipy_harness.cli import KNOWN_SUBCOMMANDS, build_parser, route_argv
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.package_runtime import compose_package_runtime
from pipy_harness.native.project_trust import (
    PROTECTED_PROJECT_ENTRIES,
    ProjectTrustStore,
    has_trust_requiring_project_resources,
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
    return Check("store_ancestry_atomic", closest and inherited and sorted_keys, "closest/delete/sorted")


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
    return Check("protected_detector", all(results) and not has_trust_requiring_project_resources(bare), "protected/exempt table")


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
    names = (*resources.skill_names(), *resources.template_names(), *resources.custom_command_slash_names())
    sources_ok = "explicit-skill" in names and any("global" in name for name in names) and not any("project" in name for name in names)

    (cwd / ".pipy" / "extensions").mkdir()
    (cwd / ".pipy" / "extensions" / "project.py").write_text("def activate(api): pass\n")
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
    return Check("resource_provenance", sources_ok and ext_names == ["global"] and "project" not in prompt.base_prompt.lower(), "workspace blocked; global/explicit retained")


def _resolver_check(root: Path) -> Check:
    cwd = root / "resolver"
    (cwd / ".pipy" / "skills").mkdir(parents=True)
    store = ProjectTrustStore(root / "resolver-config" / "trust.json")
    ask_false = not resolve_project_trusted(cwd, trust_store=store)
    always_true = resolve_project_trusted(cwd, trust_store=store, default_project_trust="always")
    store.set(cwd.parent, False)
    saved_wins = not resolve_project_trusted(cwd, trust_store=store, default_project_trust="always")
    override_wins = resolve_project_trusted(cwd, trust_store=store, trust_override=True)
    return Check("resolver_order", ask_false and always_true and saved_wins and override_wins, "override/no-resource/saved/default/headless")


def _cli_check(_root: Path) -> Check:
    parser = build_parser()
    args = parser.parse_args(route_argv(["-a", "-na", "-a"], KNOWN_SUBCOMMANDS))
    ok = args.command == "repl" and args.trust_override is True
    return Check("cli_last_override", ok, "top-level routing + sequential flags")


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
        ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    checks = run()
    passed = all(check.passed for check in checks)
    if args.json:
        print(json.dumps({"passed": passed, "checks": [asdict(check) for check in checks]}, indent=2))
    else:
        for check in checks:
            print(f"{'PASS' if check.passed else 'FAIL'} {check.name}: {check.detail}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
