"""Pi-shaped per-run source-loading flags.

The CLI flags are ephemeral runtime sources: explicit paths are additive
and are still honored when the matching ``--no-*`` flag disables default
workspace/global/package discovery.
"""

from __future__ import annotations

from pathlib import Path

from pipy_harness.cli import _resource_options_from_args, build_parser
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.package_resources import PackageRoot
from pipy_harness.native.resources import WorkspaceResources
from pipy_harness.native.theme_files import build_theme_registry
from pipy_harness.native.tool_loop_session import _activate_workspace_extensions


_SKILL = "---\nname: {name}\ndescription: {desc}\n---\nbody {name}\n"
_TEMPLATE = "---\nname: {name}\ndescription: {desc}\n---\ntemplate {name}\n"
_THEME = 'name = "{name}"\naccent_truecolor = "{accent}"\n'


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    (workspace / ".pipy" / "skills").mkdir(parents=True)
    (workspace / ".pipy" / "templates").mkdir(parents=True)
    (workspace / ".pipy" / "extensions").mkdir(parents=True)
    return workspace


def test_explicit_skill_file_loads_when_defaults_disabled(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace / ".pipy" / "skills" / "default.md").write_text(
        _SKILL.format(name="default", desc="workspace"), encoding="utf-8"
    )
    explicit = tmp_path / "outside" / "explicit.md"
    explicit.parent.mkdir()
    explicit.write_text(
        _SKILL.format(name="explicit", desc="cli"), encoding="utf-8"
    )

    resources = WorkspaceResources.discover(
        workspace,
        config_home_env={},
        home_dir=tmp_path,
        explicit_skill_paths=(explicit,),
        include_skills_defaults=False,
    )

    assert resources.skill_names() == ("explicit",)
    assert resources.skills[0].path_label.startswith("<cli>/")


def test_explicit_prompt_template_dir_loads_when_defaults_disabled(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    (workspace / ".pipy" / "templates" / "default.md").write_text(
        _TEMPLATE.format(name="default", desc="workspace"), encoding="utf-8"
    )
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "explicit.md").write_text(
        _TEMPLATE.format(name="explicit-template", desc="cli"), encoding="utf-8"
    )

    resources = WorkspaceResources.discover(
        workspace,
        config_home_env={},
        home_dir=tmp_path,
        explicit_prompt_template_paths=(template_dir,),
        include_prompt_template_defaults=False,
    )

    assert resources.template_names() == ("explicit-template",)
    assert resources.templates[0].path_label.startswith("<cli>/")


def test_explicit_skill_and_template_survive_persisted_disable_filters(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    explicit_skill = tmp_path / "skill.md"
    explicit_skill.write_text(
        _SKILL.format(name="explicit", desc="cli"), encoding="utf-8"
    )
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "explicit.md").write_text(
        _TEMPLATE.format(name="explicit-template", desc="cli"), encoding="utf-8"
    )

    resources = WorkspaceResources.discover(
        workspace,
        config_home_env={},
        home_dir=tmp_path,
        explicit_skill_paths=(explicit_skill,),
        explicit_prompt_template_paths=(template_dir,),
        include_skills_defaults=False,
        include_prompt_template_defaults=False,
    ).with_enablement(
        skills_patterns=["-explicit"],
        prompts_patterns=["-explicit-template"],
    )

    assert resources.skill_names() == ("explicit",)
    assert resources.template_names() == ("explicit-template",)


def test_explicit_skill_does_not_override_skill_command_disable(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    explicit_skill = tmp_path / "skill.md"
    explicit_skill.write_text(
        _SKILL.format(name="explicit", desc="cli"), encoding="utf-8"
    )

    resources = WorkspaceResources.discover(
        workspace,
        config_home_env={},
        home_dir=tmp_path,
        explicit_skill_paths=(explicit_skill,),
        include_skills_defaults=False,
    ).with_enablement(enable_skill_commands=False)

    assert resources.skill_names() == ()


def test_explicit_extension_file_loads_when_defaults_disabled(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    (workspace / ".pipy" / "extensions" / "default.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )
    explicit = tmp_path / "cli_ext.py"
    explicit.write_text("def activate(api):\n    pass\n", encoding="utf-8")

    descriptors = discover_extensions(
        workspace,
        config_home_env={},
        home_dir=tmp_path,
        explicit_paths=(explicit,),
        include_defaults=False,
    )

    assert [descriptor.name for descriptor in descriptors] == ["cli_ext"]
    assert descriptors[0].source_kind == "cli"
    assert descriptors[0].path_label == "<cli>/cli_ext.py"
    assert descriptors[0].status == "loadable"


def test_explicit_extension_directory_loads_direct_extension(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    explicit = tmp_path / "my_extension"
    explicit.mkdir()
    (explicit / "extension.py").write_text(
        "def activate(api):\n    pass\n", encoding="utf-8"
    )

    descriptors = discover_extensions(
        workspace,
        config_home_env={},
        home_dir=tmp_path,
        explicit_paths=(explicit,),
        include_defaults=False,
    )

    assert [descriptor.name for descriptor in descriptors] == ["my_extension"]
    assert descriptors[0].source_kind == "cli"
    assert descriptors[0].path_label == "<cli>/my_extension"
    assert descriptors[0].status == "loadable"


def test_explicit_extension_survives_persisted_disable_filter(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    explicit = tmp_path / "cli_ext.py"
    explicit.write_text(
        "def activate(api):\n"
        "    api.register_command('runtime', 'Runtime', lambda ctx, args: None)\n",
        encoding="utf-8",
    )

    runtime = _activate_workspace_extensions(
        workspace,
        WorkspaceResources.discover(workspace, config_home_env={}, home_dir=tmp_path),
        explicit_extension_paths=(explicit,),
        include_default_extensions=False,
        extension_patterns=["-cli_ext"],
    )

    assert runtime.menu_names == ("/runtime",)


def test_explicit_theme_file_overrides_package_theme(tmp_path: Path) -> None:
    package_theme_dir = tmp_path / "pkg" / "themes"
    package_theme_dir.mkdir(parents=True)
    (package_theme_dir / "shared.toml").write_text(
        _THEME.format(name="shared", accent="38;2;1;1;1"), encoding="utf-8"
    )
    explicit = tmp_path / "shared.toml"
    explicit.write_text(
        _THEME.format(name="shared", accent="38;2;9;9;9"), encoding="utf-8"
    )

    registry = build_theme_registry(
        (PackageRoot(package_theme_dir),),
        explicit_theme_paths=(explicit,),
    )

    assert registry.is_known("shared")
    assert registry.resolve("shared").accent_truecolor == "38;2;9;9;9"


def test_explicit_theme_survives_persisted_disable_filter(tmp_path: Path) -> None:
    explicit = tmp_path / "shared.toml"
    explicit.write_text(
        _THEME.format(name="shared", accent="38;2;9;9;9"), encoding="utf-8"
    )

    registry = build_theme_registry(
        (),
        filters=("-shared",),
        explicit_theme_paths=(explicit,),
    )

    assert registry.is_known("shared")


def test_repl_source_loading_flags_parse_to_runtime_options(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    parser = build_parser()

    args = parser.parse_args(
        [
            "repl",
            "--cwd",
            str(workspace),
            "--extension",
            "ext.py",
            "--no-extensions",
            "--skill",
            "s.md",
            "--no-skills",
            "--prompt-template",
            "p.md",
            "--no-prompt-templates",
            "--theme",
            "theme.toml",
            "--no-themes",
        ]
    )
    options = _resource_options_from_args(args)

    assert options.extension_paths == (workspace / "ext.py",)
    assert options.skill_paths == (workspace / "s.md",)
    assert options.prompt_template_paths == (workspace / "p.md",)
    assert options.theme_paths == (workspace / "theme.toml",)
    assert options.no_extensions is True
    assert options.no_skills is True
    assert options.no_prompt_templates is True
    assert options.no_themes is True
