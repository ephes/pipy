"""Composing installed packages into a session's resource discovery.

`compose_package_runtime` reads the configured package sources from the
settings system, resolves them into per-kind roots, and installs the
package theme registry. Project-scope sources precede user-scope sources.
The composed roots flow through `WorkspaceResources.discover` so package
skills/prompts appear at lowest precedence, and the installed theme
registry makes a package theme selectable.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path

import pytest

from pipy_harness.native import themes
from pipy_harness.native.package_manager import git_cache_path, install_package, parse_git_source
from pipy_harness.native.package_runtime import compose_package_runtime
from pipy_harness.native.resources import WorkspaceResources
from pipy_harness.native.settings import (
    SettingsManager,
    global_settings_path,
    project_settings_path,
)

_SKILL = "---\nname: {name}\ndescription: d\n---\nbody\n"
_TEMPLATE = "---\nname: {name}\ndescription: d\n---\ntpl\n"


@pytest.fixture(autouse=True)
def _reset_registry() -> Iterator[None]:
    try:
        yield
    finally:
        themes.set_active_theme_registry(None)


def _make_package(root: Path) -> Path:
    pkg = root / "pkg"
    (pkg / "skills").mkdir(parents=True)
    (pkg / "skills" / "s.md").write_text(_SKILL.format(name="pkg-skill"), encoding="utf-8")
    (pkg / "prompts").mkdir(parents=True)
    (pkg / "prompts" / "p.md").write_text(
        _TEMPLATE.format(name="pkg-prompt"), encoding="utf-8"
    )
    (pkg / "themes").mkdir(parents=True)
    (pkg / "themes" / "midnight.toml").write_text(
        'name = "midnight"\naccent_truecolor = "38;2;3;3;3"\n', encoding="utf-8"
    )
    return pkg


def _write_skill(root: Path, name: str) -> None:
    (root / "skills").mkdir(parents=True)
    (root / "skills" / f"{name}.md").write_text(
        _SKILL.format(name=name), encoding="utf-8"
    )


def _settings(cwd: Path, env: dict[str, str]) -> SettingsManager:
    return SettingsManager.for_workspace(cwd, env=env, home_dir=cwd)


def test_get_packages_returns_project_then_user(tmp_path: Path) -> None:
    cwd = tmp_path / "ws"
    (cwd / ".pipy").mkdir(parents=True)
    env = {"PIPY_CONFIG_HOME": str(tmp_path / "cfg")}
    user_pkg = tmp_path / "user-pkg"
    user_pkg.mkdir()
    project_pkg = tmp_path / "proj-pkg"
    project_pkg.mkdir()

    install_package(str(user_pkg), global_settings_path(env=env, home_dir=cwd))
    install_package(str(project_pkg), project_settings_path(cwd))

    settings = _settings(cwd, env)
    assert settings.get_packages() == [str(project_pkg), str(user_pkg)]


def test_compose_installs_package_resources_and_theme(tmp_path: Path) -> None:
    cwd = tmp_path / "ws"
    (cwd / ".pipy").mkdir(parents=True)
    env = {"PIPY_CONFIG_HOME": str(tmp_path / "cfg")}
    pkg = _make_package(tmp_path)
    install_package(str(pkg), project_settings_path(cwd))

    settings = _settings(cwd, env)
    roots = compose_package_runtime(settings, cwd)

    resources = WorkspaceResources.discover(
        cwd, config_home_env=env, home_dir=cwd, package_roots=roots
    )
    assert "pkg-skill" in resources.skill_names()
    assert "pkg-prompt" in resources.template_names()
    # The package theme is now selectable through the active registry.
    assert themes.is_known_theme("midnight")
    assert themes.resolve_palette("midnight").accent_truecolor == "38;2;3;3;3"


def test_user_scoped_git_package_does_not_resolve_project_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "ws"
    (cwd / ".pipy").mkdir(parents=True)
    config_home = tmp_path / "cfg"
    env = {"PIPY_CONFIG_HOME": str(config_home)}
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config_home))
    source = "git:example.com/org/repo"
    parsed = parse_git_source(source)
    assert parsed is not None

    user_cache = git_cache_path(
        parsed, workspace_root=cwd, config_home=config_home, local=False
    )
    project_cache = git_cache_path(
        parsed, workspace_root=cwd, config_home=config_home, local=True
    )
    _write_skill(user_cache, "user-skill")
    _write_skill(project_cache, "project-skill")
    settings_path = global_settings_path(env=env, home_dir=cwd)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps({"packages": [source]}), encoding="utf-8")

    roots = compose_package_runtime(_settings(cwd, env), cwd, install_theme_registry=False)

    assert tuple(root.path for root in roots.skills) == (user_cache / "skills",)


def test_compose_with_no_packages_is_empty(tmp_path: Path) -> None:
    cwd = tmp_path / "ws"
    (cwd / ".pipy").mkdir(parents=True)
    env = {"PIPY_CONFIG_HOME": str(tmp_path / "cfg")}
    settings = _settings(cwd, env)

    roots = compose_package_runtime(settings, cwd)

    assert roots.skills == ()
    assert not themes.is_known_theme("midnight")
