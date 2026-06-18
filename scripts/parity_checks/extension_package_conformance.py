"""Conformance gate for the extension package-management CLI (slice 12).

Exercises the real `pipy install/remove/list` CLI and the package-manager
settings mutations in temporary user/project settings files, and asserts the
settings/CLI invariants of slice 12 from `docs/extension-api.md` (local-path
scope):

1. `install` persists a local-path source to user and (with -l) project
   settings;
2. `list` reports user/project packages and an empty state correctly;
3. `config` writes Pi-shaped `+pattern` / `-pattern` resource filters
   (not deletions);
4. `remove`/`uninstall` removes only the selected source/scope; a missing
   source fails non-zero;
5. unsupported PyPI/npm/ambiguous URL sources are rejected.

It also covers **package runtime composition** (the slice-12 closeout), proving
the spec "Package conformance gate" items 2, 4, and 8:

6. (item 2) a package manifest contributes an extension, a skill, a
   prompt/template, and a theme, and resource precedence is deterministic (a
   workspace resource wins a name collision with a package resource);
7. (item 4) Pi-shaped `+pattern` / `-pattern` filters affect runtime discovery
   of package skills, prompts, and themes;
8. (item 8) no package source path or resource body (skill/prompt body,
   extension source, theme palette) leaks into the archive-safe metadata
   projections.
9. managed git package sources clone into the isolated cache, update from a
   local file-backed remote, and expose scriptable update target selection;
10. Pi-shaped source-loading flags parse and compose with runtime discovery:
   explicit ``--extension``/``--skill``/``--prompt-template``/``--theme``
   sources load even when matching default discovery is disabled.

Exits 0 when every check passes, 1 otherwise. No network.

Run:

    uv run python scripts/parity_checks/extension_package_conformance.py --json
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.cli import (
    _resource_options_from_args,
    build_parser,
    main as cli_main,
)
from pipy_harness.native import themes
from pipy_harness.native.extensions import discover_extensions, safe_extension_metadata
from pipy_harness.native.package_manager import (
    configure_resource_filter,
    install_package,
    resource_filters,
)
from pipy_harness.native.package_runtime import compose_package_runtime
from pipy_harness.native.resources import WorkspaceResources
from pipy_harness.native.settings import (
    SettingsManager,
    global_settings_path,
    project_settings_path,
)
from pipy_harness.native.theme_files import build_theme_registry


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli_main(argv)
    return code, out.getvalue(), err.getvalue()


def run_checks(base: Path) -> list[Check]:
    workspace = base / "ws"
    (workspace / ".pipy").mkdir(parents=True)
    pkg_user = base / "pkg-user"
    pkg_user.mkdir()
    pkg_project = base / "pkg-project"
    pkg_project.mkdir()
    checks: list[Check] = []

    code_e, out_e, _ = _run_cli(["list", "--cwd", str(workspace)])
    checks.append(
        Check(
            "list_empty",
            code_e == 0 and "No packages installed." in out_e,
            "list reports the empty state",
        )
    )

    code_u, out_u, _ = _run_cli(["install", str(pkg_user)])
    code_p, out_p, _ = _run_cli(["install", str(pkg_project), "-l", "--cwd", str(workspace)])
    user_settings = json.loads(global_settings_path().read_text(encoding="utf-8"))
    project_settings = json.loads(
        project_settings_path(workspace).read_text(encoding="utf-8")
    )
    checks.append(
        Check(
            "install_persists_scopes",
            code_u == 0
            and code_p == 0
            and str(pkg_user) in user_settings.get("packages", [])
            and str(pkg_project) in project_settings.get("packages", []),
            "install persists to user and project settings",
        )
    )

    code_l, out_l, _ = _run_cli(["list", "--cwd", str(workspace)])
    checks.append(
        Check(
            "list_reports_scopes",
            code_l == 0 and str(pkg_user) in out_l and str(pkg_project) in out_l,
            "list reports user + project packages",
        )
    )

    configure_resource_filter(
        settings_path=global_settings_path(), kind="skills", pattern="lint", enable=False
    )
    configure_resource_filter(
        settings_path=global_settings_path(),
        kind="extensions",
        pattern="protected",
        enable=True,
    )
    checks.append(
        Check(
            "config_writes_filters",
            resource_filters(global_settings_path(), "skills") == ("-lint",)
            and resource_filters(global_settings_path(), "extensions") == ("+protected",)
            # The package source is NOT deleted by writing a filter.
            and str(pkg_user)
            in json.loads(global_settings_path().read_text(encoding="utf-8")).get(
                "packages", []
            ),
            "config writes +/- filters without deleting packages",
        )
    )

    code_r, out_r, _ = _run_cli(["remove", str(pkg_user)])
    code_miss, _, err_miss = _run_cli(["uninstall", str(pkg_user)])
    user_after = json.loads(global_settings_path().read_text(encoding="utf-8"))
    project_after = json.loads(
        project_settings_path(workspace).read_text(encoding="utf-8")
    )
    checks.append(
        Check(
            "remove_only_selected_scope",
            code_r == 0
            and f"Removed {pkg_user}" in out_r
            and str(pkg_user) not in user_after.get("packages", [])
            and str(pkg_project) in project_after.get("packages", [])
            and code_miss == 1,
            "remove removes only the selected source/scope; missing -> non-zero",
        )
    )

    # Prove `pypi:` is rejected by source policy, not merely because no local
    # POSIX path with a colon happens to exist.
    (workspace / "pypi:demo").mkdir()
    unsupported_sources = (
        "git+https://example/x.git",
        "https://user:token@example.com/x.git",  # credentials are refused
        "GIT:example/repo",  # case-insensitive
        "ssh://git@host/x.git",  # credential-like URL userinfo is refused
        "  https://example/x.tgz",  # leading whitespace
        "npm:left-pad",
        "pypi:demo",
    )
    remote_results = [_run_cli(["install", src]) for src in unsupported_sources]
    checks.append(
        Check(
            "unsupported_remote_rejected",
            all(code == 2 for code, _out, _err in remote_results)
            and any(
                src == "pypi:demo" and "unsupported package source" in err
                for src, (_code, _out, err) in zip(unsupported_sources, remote_results)
            ),
            "unsupported PyPI/npm/credentialed/ambiguous remote sources are rejected",
        )
    )

    checks.extend(_git_package_update_checks(base, workspace))
    checks.extend(_runtime_composition_checks(base))
    checks.extend(_source_loading_flag_checks(base))
    return checks


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _commit_all(repo: Path, message: str) -> None:
    _git("add", ".", cwd=repo)
    _git(
        "-c",
        "user.name=pipy",
        "-c",
        "user.email=pipy@example.invalid",
        "commit",
        "-m",
        message,
        cwd=repo,
    )


def _make_git_package(base: Path) -> Path:
    repo = base / "git-package-src"
    (repo / "skills").mkdir(parents=True)
    (repo / "skills" / "git-one.md").write_text(
        "---\nname: git-one\ndescription: d\n---\none\n",
        encoding="utf-8",
    )
    _git("init", "-b", "main", cwd=repo)
    _commit_all(repo, "initial")
    return repo


def _git_package_update_checks(base: Path, workspace: Path) -> list[Check]:
    checks: list[Check] = []
    repo = _make_git_package(base)
    source = repo.as_uri()

    code_install, out_install, err_install = _run_cli(
        ["install", source, "-l", "--cwd", str(workspace)]
    )
    checks.append(
        Check(
            "git_install_persists_and_caches",
            code_install == 0 and f"Installed {source}" in out_install,
            f"git package install clones into a managed project cache ({err_install.strip()})",
        )
    )

    code_dry, out_dry, _ = _run_cli(
        ["update", "--extensions", "--dry-run", "--cwd", str(workspace)]
    )
    checks.append(
        Check(
            "update_extensions_dry_run_targets_packages",
            code_dry == 0 and source in out_dry and "project" in out_dry,
            "package update dry-run reports configured package targets without network",
        )
    )

    (repo / "skills" / "git-two.md").write_text(
        "---\nname: git-two\ndescription: d\n---\ntwo\n",
        encoding="utf-8",
    )
    _commit_all(repo, "second")
    code_update, out_update, err_update = _run_cli(
        ["update", "--extension", source, "--cwd", str(workspace)]
    )
    from pipy_harness.native.package_resources import resolve_package_roots

    roots = resolve_package_roots([source], workspace)
    skill_files = {
        path.name
        for root in roots.skills
        for path in root.path.iterdir()
        if path.is_file()
    }
    checks.append(
        Check(
            "git_update_refreshes_cache",
            code_update == 0
            and f"Updated {source}" in out_update
            and "git-two.md" in skill_files,
            f"git package update refreshes the managed cache ({err_update.strip()})",
        )
    )
    return checks


def _make_demo_package(base: Path) -> Path:
    """Build a package contributing all four resource kinds via a manifest."""

    pkg = base / "demo-pack"
    (pkg / "skills").mkdir(parents=True)
    (pkg / "prompts").mkdir(parents=True)
    (pkg / "themes").mkdir(parents=True)
    (pkg / "extensions").mkdir(parents=True)
    (pkg / "pipy-package.toml").write_text(
        'name = "demo-pack"\n'
        "[resources]\n"
        'skills = ["skills"]\n'
        'prompts = ["prompts"]\n'
        'themes = ["themes"]\n'
        'extensions = ["extensions"]\n',
        encoding="utf-8",
    )
    (pkg / "skills" / "pkgskill.md").write_text(
        "---\nname: pkgskill\ndescription: d\n---\nSECRET-SKILL-BODY\n",
        encoding="utf-8",
    )
    # A skill whose name collides with a workspace skill, to prove precedence.
    (pkg / "skills" / "dup.md").write_text(
        "---\nname: dup\ndescription: d\n---\nPACKAGE-DUP-BODY\n", encoding="utf-8"
    )
    (pkg / "prompts" / "pkgprompt.md").write_text(
        "---\nname: pkgprompt\ndescription: d\n---\nSECRET-PROMPT-BODY\n",
        encoding="utf-8",
    )
    (pkg / "themes" / "pkgtheme.toml").write_text(
        'name = "pkgtheme"\naccent_truecolor = "38;2;5;6;7"\n', encoding="utf-8"
    )
    (pkg / "extensions" / "pkgext.py").write_text(
        "def activate(api):\n    pass  # SECRET-EXTENSION-SOURCE\n", encoding="utf-8"
    )
    return pkg


def _runtime_composition_checks(base: Path) -> list[Check]:
    """Items 2/4/8: package manifest composition, filters, archive privacy."""

    checks: list[Check] = []
    ws = base / "composition-ws"
    (ws / ".pipy" / "skills").mkdir(parents=True)
    # A workspace skill named `dup` must win the collision with the package's.
    (ws / ".pipy" / "skills" / "dup.md").write_text(
        "---\nname: dup\ndescription: d\n---\nWORKSPACE-DUP-BODY\n", encoding="utf-8"
    )
    pkg = _make_demo_package(base)
    install_package(str(pkg), project_settings_path(ws))

    settings = SettingsManager.for_workspace(ws)
    try:
        roots = compose_package_runtime(settings, ws)
        resources = WorkspaceResources.discover(ws, package_roots=roots)
        descriptors = discover_extensions(ws, package_roots=roots.extensions)
        ext_names = {d.name for d in descriptors}

        checks.append(
            Check(
                "manifest_contributes_all_kinds",
                "pkgskill" in resources.skill_names()
                and "pkgprompt" in resources.template_names()
                and themes.is_known_theme("pkgtheme")
                and "pkgext" in ext_names,
                "manifest contributes an extension, skill, prompt, and theme",
            )
        )

        # Deterministic precedence: workspace `dup` wins over the package `dup`.
        dup_skills = [s for s in resources.skills if s.name == "dup"]
        first_dup = dup_skills[0] if dup_skills else None
        checks.append(
            Check(
                "deterministic_precedence",
                first_dup is not None
                and "WORKSPACE-DUP-BODY" in first_dup.body
                and not first_dup.path_label.startswith("<package>/"),
                "a workspace resource wins a name collision with a package one",
            )
        )

        # Item 4: filters affect runtime discovery.
        configure_resource_filter(
            settings_path=project_settings_path(ws),
            kind="skills",
            pattern="pkgskill",
            enable=False,
        )
        configure_resource_filter(
            settings_path=project_settings_path(ws),
            kind="prompts",
            pattern="pkgprompt",
            enable=False,
        )
        configure_resource_filter(
            settings_path=project_settings_path(ws),
            kind="themes",
            pattern="pkgtheme",
            enable=False,
        )
        settings.reload()
        roots2 = compose_package_runtime(settings, ws)
        filtered = WorkspaceResources.discover(ws, package_roots=roots2).with_enablement(
            skills_patterns=settings.get_skills_patterns(),
            prompts_patterns=settings.get_prompts_patterns(),
        )
        checks.append(
            Check(
                "filters_affect_discovery",
                "pkgskill" not in filtered.skill_names()
                and "pkgprompt" not in filtered.template_names()
                and not themes.is_known_theme("pkgtheme"),
                "+/- filters drop package skills, prompts, and themes",
            )
        )

        # Item 4 (per-package filters): an object-form package entry's own
        # `+/-pattern` filter scopes only that package's resources by name.
        from pipy_harness.native.package_resources import resolve_package_roots

        pp_roots = resolve_package_roots(
            [{"source": str(pkg), "skills": ["-pkgskill"]}], ws
        )
        pp_resources = WorkspaceResources.discover(ws, package_roots=pp_roots)
        checks.append(
            Check(
                "per_package_filter_scopes",
                "pkgskill" not in pp_resources.skill_names()
                and "pkgprompt" in pp_resources.template_names(),
                "an object-form package entry's own filter scopes its resources",
            )
        )

        # Item 8: no source path or resource body leaks into safe metadata.
        skill_meta = resources.safe_skill_metadata_all()
        template_meta = resources.safe_template_metadata_all()
        ext_meta = safe_extension_metadata(descriptors)
        allowed_resource_keys = {"path_label", "sha256", "byte_length", "truncated"}
        meta_blob = json.dumps(
            {
                "skills": skill_meta,
                "templates": template_meta,
                "extensions": ext_meta,
                "packages": [
                    {"name": p.name, "path_label": p.path_label, "status": p.status}
                    for p in roots.packages
                ],
            }
        )
        no_body_leak = not any(
            secret in meta_blob
            for secret in (
                "SECRET-SKILL-BODY",
                "SECRET-PROMPT-BODY",
                "SECRET-EXTENSION-SOURCE",
                "38;2;5;6;7",
            )
        )
        no_path_leak = str(pkg) not in meta_blob
        keys_safe = all(
            set(entry).issubset(allowed_resource_keys)
            for entry in (*skill_meta, *template_meta)
        )
        checks.append(
            Check(
                "no_archive_leak",
                no_body_leak and no_path_leak and keys_safe,
                "no source path or resource body leaks into safe metadata",
            )
        )
    finally:
        themes.set_active_theme_registry(None)
    return checks


def _source_loading_flag_checks(base: Path) -> list[Check]:
    """Per-run source flags: explicit paths survive matching ``--no-*``."""

    from pipy_harness.native.package_resources import PackageRoot

    checks: list[Check] = []
    ws = base / "source-flags-ws"
    (ws / ".pipy" / "skills").mkdir(parents=True)
    (ws / ".pipy" / "templates").mkdir(parents=True)
    (ws / ".pipy" / "extensions").mkdir(parents=True)
    (ws / ".pipy" / "skills" / "default.md").write_text(
        "---\nname: default-skill\n---\ndefault\n", encoding="utf-8"
    )
    (ws / ".pipy" / "templates" / "default.md").write_text(
        "---\nname: default-template\n---\ndefault\n", encoding="utf-8"
    )
    (ws / ".pipy" / "extensions" / "default.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )

    explicit_dir = base / "explicit-sources"
    explicit_dir.mkdir()
    skill_path = explicit_dir / "skill.md"
    prompt_dir = explicit_dir / "prompts"
    prompt_dir.mkdir()
    extension_path = explicit_dir / "runtime_ext.py"
    theme_path = explicit_dir / "runtime_theme.toml"
    skill_path.write_text("---\nname: runtime-skill\n---\nbody\n", encoding="utf-8")
    (prompt_dir / "template.md").write_text(
        "---\nname: runtime-template\n---\nbody\n", encoding="utf-8"
    )
    extension_path.write_text("def activate(api):\n    pass\n", encoding="utf-8")
    theme_path.write_text(
        'name = "runtime-theme"\naccent_truecolor = "38;2;9;9;9"\n',
        encoding="utf-8",
    )

    parser = build_parser()
    parsed = parser.parse_args(
        [
            "repl",
            "--cwd",
            str(ws),
            "--extension",
            str(extension_path),
            "--no-extensions",
            "--skill",
            str(skill_path),
            "--no-skills",
            "--prompt-template",
            str(prompt_dir),
            "--no-prompt-templates",
            "--theme",
            str(theme_path),
            "--no-themes",
        ]
    )
    options = _resource_options_from_args(parsed)
    resources = WorkspaceResources.discover(
        ws,
        explicit_skill_paths=options.skill_paths,
        explicit_prompt_template_paths=options.prompt_template_paths,
        include_skills_defaults=not options.no_skills,
        include_prompt_template_defaults=not options.no_prompt_templates,
    ).with_enablement(
        skills_patterns=["-runtime-skill"],
        prompts_patterns=["-runtime-template"],
    )
    descriptors = discover_extensions(
        ws,
        explicit_paths=options.extension_paths,
        include_defaults=not options.no_extensions,
    )
    package_theme_dir = base / "source-theme-package"
    package_theme_dir.mkdir()
    (package_theme_dir / "package_theme.toml").write_text(
        'name = "package-theme"\naccent_truecolor = "38;2;1;1;1"\n',
        encoding="utf-8",
    )
    registry = build_theme_registry(
        () if options.no_themes else (PackageRoot(package_theme_dir),),
        filters=["-runtime-theme"],
        explicit_theme_paths=options.theme_paths,
    )

    checks.append(
        Check(
            "source_loading_flags",
            resources.skill_names() == ("runtime-skill",)
            and resources.template_names() == ("runtime-template",)
            and [d.name for d in descriptors] == ["runtime_ext"]
            and descriptors[0].source_kind == "cli"
            and registry.is_known("runtime-theme")
            and not registry.is_known("package-theme"),
            "explicit CLI sources load while matching default discovery and persisted filters are disabled",
        )
    )
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        os.environ["PIPY_CONFIG_HOME"] = str(base / "cfg")
        checks = run_checks(base)

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
