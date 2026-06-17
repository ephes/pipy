"""Tests for the local-path package resource-resolution boundary.

`pipy_harness.native.package_resources.resolve_package_roots` turns the
configured local-path package sources (recorded by the package-manager
CLI) into per-kind resource roots that the existing skill / prompt /
extension / theme discovery boundaries consume at lowest precedence.

The boundary never imports or executes package code: it only stats
directories, reads an optional `pipy-package.toml` manifest as data, and
returns directory paths plus archive-safe per-package metadata. Remote /
missing / containment-escaping sources fail closed with a safe
diagnostic and contribute nothing.
"""

from __future__ import annotations

from pathlib import Path

from pipy_harness.native.package_resources import (
    REASON_INVALID_MANIFEST,
    REASON_MISSING_SOURCE,
    REASON_REMOTE_SOURCE,
    PackageInfo,
    PackageResourceRoots,
    resolve_package_roots,
)


def _paths(roots) -> tuple:
    """The directory paths of a tuple of PackageRoots."""

    return tuple(root.path for root in roots)


def _make_package(
    root: Path,
    name: str,
    *,
    manifest: str | None = None,
    subdirs: tuple[str, ...] = (),
) -> Path:
    pkg = root / name
    pkg.mkdir(parents=True)
    if manifest is not None:
        (pkg / "pipy-extension-package-skip.txt").write_text("noop", encoding="utf-8")
        (pkg / "pipy-package.toml").write_text(manifest, encoding="utf-8")
    for sub in subdirs:
        (pkg / sub).mkdir(parents=True, exist_ok=True)
    return pkg


def test_manifest_contributes_all_four_resource_roots(tmp_path: Path) -> None:
    pkg = _make_package(
        tmp_path / "src",
        "demo",
        manifest=(
            'name = "demo"\n'
            'version = "0.1.0"\n'
            "[resources]\n"
            'extensions = ["ext"]\n'
            'skills = ["skills"]\n'
            'prompts = ["prompts"]\n'
            'themes = ["themes"]\n'
        ),
        subdirs=("ext", "skills", "prompts", "themes"),
    )

    roots = resolve_package_roots([str(pkg)], tmp_path)

    assert isinstance(roots, PackageResourceRoots)
    assert _paths(roots.extensions) == (pkg / "ext",)
    assert _paths(roots.skills) == (pkg / "skills",)
    assert _paths(roots.prompts) == (pkg / "prompts",)
    assert _paths(roots.themes) == (pkg / "themes",)
    assert [p.name for p in roots.packages] == ["demo"]
    assert roots.packages[0].status == "loaded"
    assert roots.packages[0].reason == ""


def test_convention_fallback_without_manifest(tmp_path: Path) -> None:
    pkg = _make_package(
        tmp_path / "src",
        "conv",
        manifest=None,
        subdirs=("skills", "themes"),
    )

    roots = resolve_package_roots([str(pkg)], tmp_path)

    assert _paths(roots.skills) == (pkg / "skills",)
    assert _paths(roots.themes) == (pkg / "themes",)
    # Subdirs that do not exist contribute nothing, no error.
    assert roots.prompts == ()
    assert roots.extensions == ()
    assert roots.packages[0].status == "loaded"


def test_manifest_without_resources_table_falls_back_to_convention(tmp_path: Path) -> None:
    # A manifest may carry only name/version; the [resources] table is
    # optional, so convention subdirs are still discovered (Pi auto-discovery).
    pkg = _make_package(
        tmp_path / "src",
        "metaonly",
        manifest='name = "metaonly"\nversion = "1.0.0"\n',
        subdirs=("skills", "themes"),
    )

    roots = resolve_package_roots([str(pkg)], tmp_path)

    assert _paths(roots.skills) == (pkg / "skills",)
    assert _paths(roots.themes) == (pkg / "themes",)
    assert roots.packages[0].status == "loaded"


def test_empty_source_entry_is_rejected(tmp_path: Path) -> None:
    # Regression: an empty/whitespace source must not resolve to the
    # workspace root (which would fail-open and pull in a bare `skills/`
    # dir); such entries are dropped before resolution.
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "skills").mkdir()
    (workspace / "skills" / "x.md").write_text(
        "---\nname: x\ndescription: d\n---\nbody\n", encoding="utf-8"
    )

    roots = resolve_package_roots(["", "   ", {"source": ""}, {"source": "  "}], workspace)

    assert roots.skills == ()
    assert roots.packages == ()


def test_remote_source_is_skipped_with_diagnostic(tmp_path: Path) -> None:
    roots = resolve_package_roots(["git:example/repo"], tmp_path)

    assert roots.skills == ()
    assert roots.packages[0].status == "disabled"
    assert roots.packages[0].reason == REASON_REMOTE_SOURCE
    assert roots.diagnostics  # a safe diagnostic was recorded


def test_remote_source_label_strips_credentials(tmp_path: Path) -> None:
    # A manually configured remote source can carry secrets in userinfo /
    # query / fragment; none of it may reach the name, label, or diagnostics.
    src = "https://user:s3cr3t@host/pkg?token=abc123#frag"
    roots = resolve_package_roots([src], tmp_path)

    info = roots.packages[0]
    assert info.status == "disabled"
    assert info.reason == REASON_REMOTE_SOURCE
    blob = info.name + " " + info.path_label + " " + " ".join(roots.diagnostics)
    for secret in ("s3cr3t", "abc123", "token", "user"):
        assert secret not in blob


def test_missing_source_is_skipped_with_diagnostic(tmp_path: Path) -> None:
    roots = resolve_package_roots([str(tmp_path / "nope")], tmp_path)

    assert roots.skills == ()
    assert roots.packages[0].status == "disabled"
    assert roots.packages[0].reason == REASON_MISSING_SOURCE


def test_invalid_manifest_disables_package(tmp_path: Path) -> None:
    pkg = _make_package(
        tmp_path / "src",
        "broken",
        manifest="this is = not valid toml [[[",
        subdirs=("skills",),
    )

    roots = resolve_package_roots([str(pkg)], tmp_path)

    assert roots.skills == ()
    assert roots.packages[0].status == "disabled"
    assert roots.packages[0].reason == REASON_INVALID_MANIFEST


def test_resource_dir_escaping_package_is_rejected(tmp_path: Path) -> None:
    src = tmp_path / "src"
    outside = src / "evil"
    outside.mkdir(parents=True)
    pkg = _make_package(
        src,
        "escaper",
        manifest=(
            'name = "escaper"\n'
            "[resources]\n"
            'skills = ["../evil"]\n'
        ),
    )

    roots = resolve_package_roots([str(pkg)], tmp_path)

    # The escaping dir is not contributed; the package still loads.
    assert roots.skills == ()
    assert roots.packages[0].status == "loaded"
    assert roots.diagnostics


def test_project_sources_precede_user_sources(tmp_path: Path) -> None:
    project = _make_package(tmp_path / "proj", "p", subdirs=("skills",))
    user = _make_package(tmp_path / "user", "u", subdirs=("skills",))

    # Caller passes sources in precedence order: project first, then user.
    roots = resolve_package_roots([str(project), str(user)], tmp_path)

    assert _paths(roots.skills) == (project / "skills", user / "skills")


def test_duplicate_source_is_resolved_once(tmp_path: Path) -> None:
    pkg = _make_package(tmp_path / "src", "dup", subdirs=("skills",))

    roots = resolve_package_roots([str(pkg), str(pkg)], tmp_path)

    assert _paths(roots.skills) == (pkg / "skills",)
    assert len(roots.packages) == 1


def test_relative_source_resolves_against_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _make_package(workspace, "rel", subdirs=("skills",))

    roots = resolve_package_roots(["rel"], workspace)

    assert _paths(roots.skills) == ((workspace / "rel" / "skills").resolve(),)


def test_object_form_entry_carries_per_package_filters(tmp_path: Path) -> None:
    pkg = _make_package(
        tmp_path / "src",
        "filtered",
        subdirs=("skills", "themes"),
    )

    entry = {"source": str(pkg), "skills": ["-secret"], "themes": ["+midnight"]}
    roots = resolve_package_roots([entry], tmp_path)

    assert roots.skills[0].filters == ("-secret",)
    assert roots.themes[0].filters == ("+midnight",)


def test_manifest_name_with_path_separators_is_made_safe(tmp_path: Path) -> None:
    # A manifest name with path separators / parent refs must not produce a
    # misleading `<package>/../...` label; it falls back to a safe component.
    pkg = _make_package(
        tmp_path / "src",
        "evilpkg",
        manifest='name = "../../etc/evil"\n',
        subdirs=("skills",),
    )

    roots = resolve_package_roots([str(pkg)], tmp_path)

    label = roots.packages[0].path_label
    assert label.startswith("<package>/")
    assert "/" not in label[len("<package>/") :]
    assert ".." not in label


def test_package_metadata_carries_no_absolute_path(tmp_path: Path) -> None:
    pkg = _make_package(tmp_path / "src", "labelled", subdirs=("skills",))

    roots = resolve_package_roots([str(pkg)], tmp_path)

    info = roots.packages[0]
    assert isinstance(info, PackageInfo)
    assert info.name == "labelled"
    # The safe label must not embed the absolute source path.
    assert str(tmp_path) not in info.path_label
