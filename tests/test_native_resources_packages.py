"""Package roots flowing through skill / prompt discovery at lowest precedence.

Installed local-path packages contribute skills and prompt templates
through the same `WorkspaceResources` discovery boundary used for
workspace and global resources, appended *after* them so local resources
always win. Pi-shaped `+/-pattern` enablement filters apply to
package-contributed resources via `with_enablement`.
"""

from __future__ import annotations

from pathlib import Path

from pipy_harness.native.package_resources import resolve_package_roots
from pipy_harness.native.resources import WorkspaceResources
from pipy_harness.native.skills import discover_workspace_skills

_SKILL = "---\nname: {name}\ndescription: {desc}\n---\nbody for {name}\n"
_TEMPLATE = "---\nname: {name}\ndescription: {desc}\n---\ntemplate {name}\n"


def _ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    (workspace / ".pipy" / "skills").mkdir(parents=True)
    (workspace / ".pipy" / "templates").mkdir(parents=True)
    return workspace


def _empty_env() -> dict[str, str]:
    return {}


def _discover(workspace: Path, package_roots, home_dir: Path) -> WorkspaceResources:
    return WorkspaceResources.discover(
        workspace,
        config_home_env=_empty_env(),
        home_dir=home_dir,
        package_roots=package_roots,
    )


def test_package_skill_discovered_after_workspace(tmp_path: Path) -> None:
    workspace = _ws(tmp_path)
    (workspace / ".pipy" / "skills" / "ws.md").write_text(
        _SKILL.format(name="ws-skill", desc="workspace"), encoding="utf-8"
    )
    pkg = tmp_path / "pkg"
    (pkg / "skills").mkdir(parents=True)
    (pkg / "skills" / "p.md").write_text(
        _SKILL.format(name="pkg-skill", desc="package"), encoding="utf-8"
    )

    roots = resolve_package_roots([str(pkg)], workspace)
    resources = _discover(workspace, roots, tmp_path)

    assert resources.skill_names() == ("ws-skill", "pkg-skill")


def test_package_prompt_discovered(tmp_path: Path) -> None:
    workspace = _ws(tmp_path)
    pkg = tmp_path / "pkg"
    (pkg / "prompts").mkdir(parents=True)
    (pkg / "prompts" / "greet.md").write_text(
        _TEMPLATE.format(name="pkg-prompt", desc="package"), encoding="utf-8"
    )

    roots = resolve_package_roots([str(pkg)], workspace)
    resources = _discover(workspace, roots, tmp_path)

    assert "pkg-prompt" in resources.template_names()


def test_disable_filter_excludes_package_skill(tmp_path: Path) -> None:
    workspace = _ws(tmp_path)
    pkg = tmp_path / "pkg"
    (pkg / "skills").mkdir(parents=True)
    (pkg / "skills" / "p.md").write_text(
        _SKILL.format(name="pkg-skill", desc="package"), encoding="utf-8"
    )

    roots = resolve_package_roots([str(pkg)], workspace)
    resources = _discover(workspace, roots, tmp_path).with_enablement(
        skills_patterns=["-pkg-skill"]
    )

    assert "pkg-skill" not in resources.skill_names()


def test_package_skill_name_collision_is_deduped(tmp_path: Path) -> None:
    workspace = _ws(tmp_path)
    (workspace / ".pipy" / "skills" / "dup.md").write_text(
        _SKILL.format(name="dup", desc="workspace"), encoding="utf-8"
    )
    pkg = tmp_path / "pkg"
    (pkg / "skills").mkdir(parents=True)
    (pkg / "skills" / "dup.md").write_text(
        _SKILL.format(name="dup", desc="package"), encoding="utf-8"
    )

    roots = resolve_package_roots([str(pkg)], workspace)
    resources = _discover(workspace, roots, tmp_path)

    # Name-deduped (first wins): exactly one "dup", and it is the workspace one.
    names = resources.skill_names()
    assert names.count("dup") == 1
    dup = next(s for s in resources.skills if s.name == "dup")
    assert not dup.path_label.startswith("<package>/")


def test_per_package_filter_scopes_to_that_package(tmp_path: Path) -> None:
    workspace = _ws(tmp_path)
    pkg = tmp_path / "pkg"
    (pkg / "skills").mkdir(parents=True)
    (pkg / "skills" / "keep.md").write_text(
        _SKILL.format(name="keep", desc="k"), encoding="utf-8"
    )
    (pkg / "skills" / "drop.md").write_text(
        _SKILL.format(name="drop", desc="d"), encoding="utf-8"
    )

    # Object-form entry: this package's own `-drop` filter.
    roots = resolve_package_roots(
        [{"source": str(pkg), "skills": ["-drop"]}], workspace
    )
    resources = _discover(workspace, roots, tmp_path)

    assert "keep" in resources.skill_names()
    assert "drop" not in resources.skill_names()


def test_large_name_duplicate_does_not_halt_discovery(tmp_path: Path) -> None:
    # Regression: a large later package resource whose NAME duplicates an
    # earlier one must be name-deduped (skipped) WITHOUT tripping the total
    # byte cap and halting discovery of subsequent distinct resources.
    workspace = _ws(tmp_path)
    (workspace / ".pipy" / "skills" / "dup.md").write_text(
        _SKILL.format(name="dup", desc="workspace"), encoding="utf-8"
    )
    pkg = tmp_path / "pkg"
    (pkg / "skills").mkdir(parents=True)
    # A huge duplicate-named package skill (would exceed the total cap if
    # it were counted) sorts before "keep.md".
    (pkg / "skills" / "dup.md").write_text(
        "---\nname: dup\ndescription: d\n---\n" + ("x" * 5000) + "\n",
        encoding="utf-8",
    )
    (pkg / "skills" / "keep.md").write_text(
        _SKILL.format(name="keep", desc="package"), encoding="utf-8"
    )

    roots = resolve_package_roots([str(pkg)], workspace)
    skills, cap_reached = discover_workspace_skills(
        workspace,
        config_home_env=_empty_env(),
        home_dir=tmp_path,
        package_roots=roots.skills,
        total_byte_cap=2000,
    )

    names = [s.name for s in skills]
    assert "keep" in names
    assert names.count("dup") == 1
    assert cap_reached is False


def test_over_cap_unique_file_is_not_fully_hashed(tmp_path, monkeypatch) -> None:
    # Regression: a unique resource that will be rejected for exceeding the
    # total byte cap must not be fully read/hashed first. The full-file hash
    # (`_hash_file`) must never run for a file that never gets included.
    from pipy_harness.native import _resource_files

    calls = {"n": 0}
    real_hash = _resource_files._hash_file

    def _counting_hash(path):
        calls["n"] += 1
        return real_hash(path)

    monkeypatch.setattr(_resource_files, "_hash_file", _counting_hash)

    workspace = _ws(tmp_path)
    big = workspace / ".pipy" / "skills" / "big.md"
    big.write_text(
        "---\nname: big\ndescription: d\n---\n" + ("x" * 4000) + "\n",
        encoding="utf-8",
    )

    skills, cap_reached = discover_workspace_skills(
        workspace,
        config_home_env=_empty_env(),
        home_dir=tmp_path,
        per_file_byte_cap=200,
        total_byte_cap=500,
    )

    # The over-cap file is rejected and was never fully hashed.
    assert cap_reached is True
    assert "big" not in [s.name for s in skills]
    assert calls["n"] == 0


def test_package_label_strips_control_bytes_from_dir_name(tmp_path: Path) -> None:
    # Regression: a package may declare a resource dir whose name contains a
    # terminal control byte; the recorded path_label must never carry it.
    workspace = _ws(tmp_path)
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    bad_dir = "bad\x1bdir"
    (pkg / bad_dir).mkdir()
    (pkg / bad_dir / "s.md").write_text(
        _SKILL.format(name="ctrl", desc="d"), encoding="utf-8"
    )
    (pkg / "pipy-package.toml").write_text(
        'name = "ctrlpkg"\n[resources]\nskills = ["bad\\u001bdir"]\n',
        encoding="utf-8",
    )

    roots = resolve_package_roots([str(pkg)], workspace)
    skills, _ = discover_workspace_skills(
        workspace,
        config_home_env=_empty_env(),
        home_dir=tmp_path,
        package_roots=roots.skills,
    )

    ctrl = next(s for s in skills if s.name == "ctrl")
    assert "\x1b" not in ctrl.path_label
    assert ctrl.path_label.startswith("<package>/")


def test_no_package_roots_is_unchanged(tmp_path: Path) -> None:
    workspace = _ws(tmp_path)
    (workspace / ".pipy" / "skills" / "ws.md").write_text(
        _SKILL.format(name="ws-skill", desc="workspace"), encoding="utf-8"
    )

    resources = _discover(workspace, None, tmp_path)

    assert resources.skill_names() == ("ws-skill",)
