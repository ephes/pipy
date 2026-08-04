"""Package extension roots flowing through extension discovery.

Installed local-path packages contribute Python extension candidates
through the same inventory boundary as workspace/global extensions,
appended at lowest precedence with a ``"package"`` source kind. Discovery
still never imports extension code, and a workspace extension wins a name
collision with a package extension.
"""

from __future__ import annotations

from pathlib import Path

from pipy_harness.native.extensions.packages import (
    REASON_DUPLICATE_NAME,
    discover_extensions,
)
from pipy_harness.native.package_resources import PackageRoot


def _empty_env() -> dict[str, str]:
    return {}


def _discover(workspace: Path, package_roots, home_dir: Path):
    return discover_extensions(
        workspace,
        config_home_env=_empty_env(),
        home_dir=home_dir,
        package_roots=package_roots,
        include_workspace_defaults=True,
    )


def _ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    (workspace / ".pipy" / "extensions").mkdir(parents=True)
    return workspace


def test_package_extension_is_discovered(tmp_path: Path) -> None:
    workspace = _ws(tmp_path)
    pkg_ext = tmp_path / "pkg" / "extensions"
    pkg_ext.mkdir(parents=True)
    (pkg_ext / "demo.py").write_text("def activate(api):\n    pass\n", encoding="utf-8")

    descriptors = _discover(workspace, [PackageRoot(pkg_ext)], tmp_path)

    by_name = {d.name: d for d in descriptors}
    assert "demo" in by_name
    assert by_name["demo"].source_kind == "package"
    assert by_name["demo"].status == "loadable"
    assert by_name["demo"].path_label.startswith("<package>/")


def test_workspace_extension_wins_name_collision(tmp_path: Path) -> None:
    workspace = _ws(tmp_path)
    (workspace / ".pipy" / "extensions" / "shared.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    pkg_ext = tmp_path / "pkg" / "extensions"
    pkg_ext.mkdir(parents=True)
    (pkg_ext / "shared.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )

    descriptors = _discover(workspace, [PackageRoot(pkg_ext)], tmp_path)

    shared = [d for d in descriptors if d.name == "shared"]
    assert len(shared) == 2
    # Workspace candidate comes first and stays loadable; the package
    # duplicate is disabled.
    assert shared[0].source_kind == "workspace"
    assert shared[0].status == "loadable"
    assert shared[1].source_kind == "package"
    assert shared[1].status == "disabled"
    assert shared[1].reason == REASON_DUPLICATE_NAME


def test_source_tiers_use_explicit_first_wins_precedence(tmp_path: Path) -> None:
    workspace = _ws(tmp_path)
    config_home = tmp_path / "config"
    global_ext = config_home / "extensions"
    global_ext.mkdir(parents=True)
    package_ext = tmp_path / "package" / "extensions"
    package_ext.mkdir(parents=True)
    explicit = tmp_path / "explicit" / "shared.py"
    explicit.parent.mkdir()
    body = "def activate(api):\n    pass\n"
    explicit.write_text(body, encoding="utf-8")
    (workspace / ".pipy" / "extensions" / "shared.py").write_text(
        body, encoding="utf-8"
    )
    (global_ext / "shared.py").write_text(body, encoding="utf-8")
    (package_ext / "shared.py").write_text(body, encoding="utf-8")

    descriptors = discover_extensions(
        workspace,
        config_home_env={"PIPY_CONFIG_HOME": str(config_home)},
        package_roots=(PackageRoot(package_ext),),
        explicit_paths=(explicit,),
        include_workspace_defaults=True,
    )

    shared = [descriptor for descriptor in descriptors if descriptor.name == "shared"]
    assert [descriptor.source_kind for descriptor in shared] == [
        "cli",
        "workspace",
        "global",
        "package",
    ]
    assert shared[0].status == "loadable"
    assert [descriptor.reason for descriptor in shared[1:]] == [
        REASON_DUPLICATE_NAME,
        REASON_DUPLICATE_NAME,
        REASON_DUPLICATE_NAME,
    ]


def test_no_package_roots_unchanged(tmp_path: Path) -> None:
    workspace = _ws(tmp_path)
    (workspace / ".pipy" / "extensions" / "only.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )

    descriptors = _discover(workspace, (), tmp_path)

    assert [d.name for d in descriptors] == ["only"]
