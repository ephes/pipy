"""Slice 12 tests for the local-path extension package manager."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pipy_harness.native.package_manager import (
    cached_git_source_path,
    git_cache_path,
    PackageSettingsError,
    PackageSourceError,
    canonical_local_source,
    configure_resource_filter,
    format_package_listing,
    install_package,
    install_package_source,
    is_local_path_source,
    list_packages,
    parse_git_source,
    remove_package,
    resource_filters,
)
from pipy_harness.native.package_resources import (
    REASON_MISSING_SOURCE,
    resolve_package_roots,
)


def _settings(tmp_path: Path, name: str) -> Path:
    return tmp_path / name / "settings.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _commit_all(repo: Path, message: str) -> None:
    _git("add", ".", cwd=repo)
    _git("-c", "user.name=pipy", "-c", "user.email=pipy@example.invalid", "commit", "-m", message, cwd=repo)


def _make_git_package(tmp_path: Path) -> Path:
    repo = tmp_path / "git-src" / "demo"
    (repo / "skills").mkdir(parents=True)
    (repo / "skills" / "one.md").write_text(
        "---\nname: one\ndescription: d\n---\none\n", encoding="utf-8"
    )
    _git("init", "-b", "main", cwd=repo)
    _commit_all(repo, "initial")
    return repo


def test_install_records_source(tmp_path: Path) -> None:
    user = _settings(tmp_path, "user")

    assert install_package("../pkg-a", user) == "Installed ../pkg-a"
    assert _read(user)["packages"] == ["../pkg-a"]


def test_install_deduplicates(tmp_path: Path) -> None:
    user = _settings(tmp_path, "user")
    install_package("../pkg-a", user)
    install_package("../pkg-a", user)
    install_package("../pkg-b", user)

    assert _read(user)["packages"] == ["../pkg-a", "../pkg-b"]


def test_install_preserves_other_settings(tmp_path: Path) -> None:
    user = _settings(tmp_path, "user")
    user.parent.mkdir(parents=True)
    user.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")

    install_package("../pkg", user)
    data = _read(user)
    assert data["theme"] == "dark"
    assert data["packages"] == ["../pkg"]


def test_remove_only_matching(tmp_path: Path) -> None:
    user = _settings(tmp_path, "user")
    install_package("../pkg-a", user)
    install_package("../pkg-b", user)

    assert remove_package("../pkg-a", user) == "Removed ../pkg-a"
    assert _read(user)["packages"] == ["../pkg-b"]
    # Removing a source that is not configured returns None (CLI exits != 0).
    assert remove_package("../pkg-missing", user) is None


def test_list_user_and_project_and_empty(tmp_path: Path) -> None:
    user = _settings(tmp_path, "user")
    project = _settings(tmp_path, "project")

    assert format_package_listing(list_packages(user_path=user, project_path=project)) == (
        "No packages installed."
    )

    install_package("../user-pkg", user)
    install_package("../project-pkg", project)
    listing = list_packages(user_path=user, project_path=project)
    assert listing.user == ("../user-pkg",)
    assert listing.project == ("../project-pkg",)
    rendered = format_package_listing(listing)
    assert "user:" in rendered and "../user-pkg" in rendered
    assert "project:" in rendered and "../project-pkg" in rendered


def test_config_writes_filters_and_toggles(tmp_path: Path) -> None:
    user = _settings(tmp_path, "user")

    configure_resource_filter(settings_path=user, kind="skills", pattern="lint", enable=False)
    assert resource_filters(user, "skills") == ("-lint",)

    # Toggling the same pattern flips its sign in place (no duplicate).
    configure_resource_filter(settings_path=user, kind="skills", pattern="lint", enable=True)
    assert resource_filters(user, "skills") == ("+lint",)

    configure_resource_filter(settings_path=user, kind="extensions", pattern="*", enable=False)
    assert resource_filters(user, "extensions") == ("-*",)


def test_local_source_resolution_and_existence(tmp_path: Path) -> None:
    pkg = tmp_path / "pkgs" / "mypkg"
    pkg.mkdir(parents=True)

    resolved = canonical_local_source("pkgs/mypkg", tmp_path)
    assert resolved == pkg.resolve()
    # A missing source fails closed.
    assert canonical_local_source("pkgs/missing", tmp_path) is None


def test_git_and_pypi_sources_are_not_local(tmp_path: Path) -> None:
    for source in (
        "git:foo",
        "git+https://x/y",
        "https://x/y.tgz",
        "npm:foo",
        "pypi:demo",
        "cargo:crate",
    ):
        assert is_local_path_source(source) is False
        assert canonical_local_source(source, tmp_path) is None
    assert is_local_path_source("../local-path") is True


def test_parse_git_source_and_cache_path_are_safe(tmp_path: Path) -> None:
    parsed = parse_git_source("git:github.com/org/repo@main")
    assert parsed is not None
    assert parsed.repo == "https://github.com/org/repo"
    assert parsed.host == "github.com"
    assert parsed.path == "org/repo"
    assert parsed.ref == "main"

    cache = git_cache_path(
        parsed,
        workspace_root=tmp_path / "ws",
        config_home=tmp_path / "cfg",
        local=True,
    )
    assert cache == (tmp_path / "ws" / ".pipy" / "git" / "github.com" / "org" / "repo").resolve()

    assert parse_git_source("https://user:token@example.com/org/repo") is None
    assert parse_git_source("git:example.com/org/../repo") is None


def test_writes_refuse_to_clobber_corrupt_settings(tmp_path: Path) -> None:
    # A present-but-unparseable settings file must not be silently overwritten
    # (matching SettingsManager's clobber-refusal); a missing file is fine.
    user = _settings(tmp_path, "user")
    user.parent.mkdir(parents=True)
    user.write_text("{ this is not json", encoding="utf-8")

    with pytest.raises(PackageSettingsError):
        install_package("../pkg", user)
    with pytest.raises(PackageSettingsError):
        remove_package("../pkg", user)
    with pytest.raises(PackageSettingsError):
        configure_resource_filter(
            settings_path=user, kind="skills", pattern="x", enable=False
        )
    # The corrupt content is preserved, not overwritten.
    assert user.read_text(encoding="utf-8") == "{ this is not json"


def test_remote_source_screen_is_case_and_scheme_robust(tmp_path: Path) -> None:
    # Uppercase / mixed-case, extra schemes, and leading whitespace must all be
    # classified as remote (non-local), matching Pi's package-source contract.
    for source in (
        "GIT:foo",
        "Git+https://x/y",
        "HTTPS://host/pkg",
        "ssh://git@host/x.git",
        "git://host/x.git",
        "file:///etc/passwd",
        "  https://host/pkg",
        "NPM:left-pad",
    ):
        assert is_local_path_source(source) is False, source
        assert canonical_local_source(source, tmp_path) is None, source


def test_object_form_package_entries_are_preserved(tmp_path: Path) -> None:
    # A `{source, skills: [...]}` PackageSource object stays intact when other
    # sources are installed/removed (the spec documents object-form entries).
    user = _settings(tmp_path, "user")
    user.parent.mkdir(parents=True)
    user.write_text(
        json.dumps({"packages": [{"source": "../obj-pkg", "skills": ["+only"]}]}),
        encoding="utf-8",
    )

    install_package("../str-pkg", user)
    packages = _read(user)["packages"]
    assert {"source": "../obj-pkg", "skills": ["+only"]} in packages
    assert "../str-pkg" in packages

    # list/format surface the object's source string.
    listing = list_packages(user_path=user, project_path=None)
    assert "../obj-pkg" in listing.user
    assert "../str-pkg" in listing.user

    # Removing the object-form source by its source string drops only it.
    assert remove_package("../obj-pkg", user) == "Removed ../obj-pkg"
    remaining = _read(user)["packages"]
    assert remaining == ["../str-pkg"]


# -- product path: the `pipy install/remove/list` CLI ---------------------


def test_cli_install_list_remove(tmp_path, monkeypatch, capsys) -> None:
    from pipy_harness.cli import main

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "cfg"))
    workspace = tmp_path / "ws"
    (workspace / ".pipy").mkdir(parents=True)
    pkg = tmp_path / "mypkg"
    pkg.mkdir()

    assert main(["install", str(pkg), "-l", "--cwd", str(workspace)]) == 0
    out = capsys.readouterr().out
    assert f"Installed {pkg}" in out

    assert main(["list", "--cwd", str(workspace)]) == 0
    assert str(pkg) in capsys.readouterr().out

    assert main(["remove", str(pkg), "-l", "--cwd", str(workspace)]) == 0
    assert f"Removed {pkg}" in capsys.readouterr().out

    # Removing again is non-zero (not configured).
    assert main(["remove", str(pkg), "-l", "--cwd", str(workspace)]) == 1


def test_cli_install_rejects_git_source(tmp_path, monkeypatch) -> None:
    from pipy_harness.cli import main

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "cfg"))
    assert main(["install", "npm:foo"]) == 2


def test_cli_git_install_composes_runtime_resources(tmp_path, monkeypatch, capsys) -> None:
    from pipy_harness.cli import main

    config_home = tmp_path / "cfg"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config_home))
    workspace = tmp_path / "ws"
    (workspace / ".pipy").mkdir(parents=True)
    repo = _make_git_package(tmp_path)
    source = repo.as_uri()

    assert main(["install", source, "-l", "--cwd", str(workspace)]) == 0
    assert f"Installed {source}" in capsys.readouterr().out

    cached = cached_git_source_path(
        source,
        workspace_root=workspace,
        config_home=config_home,
        local=True,
    )
    assert cached is not None
    assert (cached / "skills" / "one.md").exists()

    roots = resolve_package_roots([source], workspace)
    assert tuple(root.path for root in roots.skills) == (cached / "skills",)
    assert roots.packages[0].status == "loaded"


def test_cli_git_update_refreshes_managed_cache(tmp_path, monkeypatch, capsys) -> None:
    from pipy_harness.cli import main

    config_home = tmp_path / "cfg"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config_home))
    workspace = tmp_path / "ws"
    (workspace / ".pipy").mkdir(parents=True)
    repo = _make_git_package(tmp_path)
    source = repo.as_uri()

    assert main(["install", source, "-l", "--cwd", str(workspace)]) == 0
    capsys.readouterr()
    (repo / "skills" / "two.md").write_text(
        "---\nname: two\ndescription: d\n---\ntwo\n", encoding="utf-8"
    )
    _commit_all(repo, "second")

    assert main(["update", "--extensions", "--cwd", str(workspace)]) == 0
    assert f"Updated {source}" in capsys.readouterr().out
    roots = resolve_package_roots([source], workspace)
    assert roots.skills
    skill_names = {path.name for path in roots.skills[0].path.iterdir()}
    assert "two.md" in skill_names

    assert main(["update", "--dry-run", "git:example.com/no/match", "--cwd", str(workspace)]) == 1


def test_install_package_source_rejects_unsupported_remote_before_settings(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "user")
    # Even if POSIX can represent this as a directory name, `pypi:` is a
    # deferred package-source scheme and must not fall through as a local path.
    (tmp_path / "pypi:demo").mkdir()
    with pytest.raises(PackageSourceError, match="unsupported package source"):
        install_package_source(
            "pypi:demo",
            settings,
            workspace_root=tmp_path,
            config_home=tmp_path / "cfg",
            local=False,
        )
    assert not settings.exists()


def test_uninstalled_git_source_resolves_disabled_without_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.package_manager as package_manager

    def fail_git(*_args, **_kwargs):
        raise AssertionError("runtime package resolution must not run git")

    monkeypatch.setattr(package_manager, "_run_git", fail_git)

    roots = resolve_package_roots(["git:example.com/org/repo"], tmp_path)

    assert roots.skills == ()
    assert roots.packages[0].status == "disabled"
    assert roots.packages[0].reason == REASON_MISSING_SOURCE


def test_cli_update_rejects_conflicting_targets(tmp_path, monkeypatch, capsys) -> None:
    from pipy_harness.cli import main

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "cfg"))
    workspace = tmp_path / "ws"
    workspace.mkdir()

    assert main(["update", "self", "--extensions", "--cwd", str(workspace)]) == 2
    assert "accepts only one" in capsys.readouterr().err


def test_cli_list_empty(tmp_path, monkeypatch, capsys) -> None:
    from pipy_harness.cli import main

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "cfg"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    assert main(["list", "--cwd", str(workspace)]) == 0
    assert "No packages installed." in capsys.readouterr().out
