"""Slice 12 tests for the local-path extension package manager."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipy_harness.native.package_manager import (
    PackageSettingsError,
    canonical_local_source,
    configure_resource_filter,
    format_package_listing,
    install_package,
    is_local_path_source,
    list_packages,
    remove_package,
    resource_filters,
)


def _settings(tmp_path: Path, name: str) -> Path:
    return tmp_path / name / "settings.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
    for source in ("git:foo", "git+https://x/y", "https://x/y.tgz", "npm:foo"):
        assert is_local_path_source(source) is False
        assert canonical_local_source(source, tmp_path) is None
    assert is_local_path_source("../local-path") is True


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
    assert main(["install", "git:foo"]) == 2


def test_cli_list_empty(tmp_path, monkeypatch, capsys) -> None:
    from pipy_harness.cli import main

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "cfg"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    assert main(["list", "--cwd", str(workspace)]) == 0
    assert "No packages installed." in capsys.readouterr().out
