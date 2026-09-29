"""Pi skill layout and `.agents/skills` roots (CTX1).

Pins pipy's port of Pi's auto skill discovery (pi-mono `4df157433`):
`package-manager.ts` `collectSkillEntries` / `collectAncestorAgentsSkillDirs`
and the auto root order, plus `skills.ts` `loadSkillFromFile` naming and the
description requirement.
"""

from __future__ import annotations

from pathlib import Path

from pipy_harness.native._resource_files import PIPY_CONFIG_HOME_ENV
from pipy_harness.native.ignore_rules import IgnoreMatcher, prefix_ignore_pattern
from pipy_harness.native.skills import SkillFile, discover_workspace_skills


def _skill(
    directory: Path, filename: str, *, name: str | None = None, description: str = "d"
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    if name is not None:
        lines.append(f"name: {name}")
    lines.append(f"description: {description}")
    lines += ["---", "body", ""]
    path = directory / filename
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _discover(
    workspace: Path,
    *,
    home: Path,
    config_home: Path,
    trusted: bool = True,
) -> list[SkillFile]:
    skills, _ = discover_workspace_skills(
        workspace,
        config_home_env={PIPY_CONFIG_HOME_ENV: str(config_home)},
        home_dir=home,
        include_workspace_defaults=trusted,
    )
    return skills


def _layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    home = tmp_path / "home"
    config_home = tmp_path / "config"
    workspace = tmp_path / "repo" / "pkg"
    workspace.mkdir(parents=True)
    (tmp_path / "repo" / ".git").mkdir()
    home.mkdir()
    config_home.mkdir()
    return workspace, home, config_home


def test_skill_md_directory_is_one_skill_named_after_its_directory(
    tmp_path: Path,
) -> None:
    workspace, home, config = _layout(tmp_path)
    root = workspace / ".pipy" / "skills"
    _skill(root / "commit-ready", "SKILL.md")
    # Files beside SKILL.md, and directories below it, are not skills.
    _skill(root / "commit-ready", "REFERENCE.md", name="reference")
    _skill(root / "commit-ready" / "nested", "SKILL.md", name="nested")

    skills = _discover(workspace, home=home, config_home=config)

    assert [(s.name, s.path_label) for s in skills] == [
        ("commit-ready", ".pipy/skills/commit-ready/SKILL.md")
    ]


def test_frontmatter_name_wins_over_directory_name(tmp_path: Path) -> None:
    workspace, home, config = _layout(tmp_path)
    _skill(workspace / ".pipy" / "skills" / "dir", "SKILL.md", name="named")

    skills = _discover(workspace, home=home, config_home=config)

    assert [s.name for s in skills] == ["named"]


def test_pi_layout_takes_plain_md_only_at_the_root(tmp_path: Path) -> None:
    workspace, home, config = _layout(tmp_path)
    root = workspace / ".pipy" / "skills"
    _skill(root, "top.md")
    _skill(root / "group", "deep.md")
    _skill(root / "group" / "inner", "SKILL.md")

    skills = _discover(workspace, home=home, config_home=config)

    assert sorted(s.name for s in skills) == ["inner", "top"]


def test_agents_layout_takes_plain_md_only_below_the_root(tmp_path: Path) -> None:
    workspace, home, config = _layout(tmp_path)
    root = workspace / ".agents" / "skills"
    _skill(root, "top.md")
    _skill(root / "group", "deep.md")
    _skill(root / "tool", "SKILL.md")

    skills = _discover(workspace, home=home, config_home=config)

    assert sorted(s.name for s in skills) == ["deep", "tool"]
    assert ".agents/skills/tool/SKILL.md" in {s.path_label for s in skills}


def test_dot_entries_and_node_modules_are_skipped(tmp_path: Path) -> None:
    workspace, home, config = _layout(tmp_path)
    root = workspace / ".pipy" / "skills"
    _skill(root / ".hidden", "SKILL.md")
    _skill(root / "node_modules" / "dep", "SKILL.md")
    _skill(root, ".dot.md")
    _skill(root / "real", "SKILL.md")

    skills = _discover(workspace, home=home, config_home=config)

    assert [s.name for s in skills] == ["real"]


def test_skill_without_description_is_dropped_and_does_not_shadow(
    tmp_path: Path,
) -> None:
    workspace, home, config = _layout(tmp_path)
    _skill(workspace / ".pipy" / "skills" / "dup", "SKILL.md", description="")
    _skill(config / "skills" / "dup", "SKILL.md", description="global")

    skills = _discover(workspace, home=home, config_home=config)

    assert [(s.name, s.description) for s in skills] == [("dup", "global")]


def test_root_order_and_first_wins_dedup(tmp_path: Path) -> None:
    workspace, home, config = _layout(tmp_path)
    repo = workspace.parent
    for label, directory in (
        ("pipy", workspace / ".pipy" / "skills" / "shared"),
        ("cwd-agents", workspace / ".agents" / "skills" / "shared"),
        ("repo-agents", repo / ".agents" / "skills" / "shared"),
        ("global", config / "skills" / "shared"),
        ("user-agents", home / ".agents" / "skills" / "shared"),
    ):
        _skill(directory, "SKILL.md", description=label)
    _skill(workspace / ".agents" / "skills" / "cwd-only", "SKILL.md")
    _skill(repo / ".agents" / "skills" / "repo-only", "SKILL.md")
    _skill(config / "skills" / "global-only", "SKILL.md")
    _skill(home / ".agents" / "skills" / "user-only", "SKILL.md")

    skills = _discover(workspace, home=home, config_home=config)

    by_name = {s.name: s for s in skills}
    assert by_name["shared"].description == "pipy"
    assert [s.name for s in skills] == [
        "shared",
        "cwd-only",
        "repo-only",
        "global-only",
        "user-only",
    ]
    assert by_name["repo-only"].path_label == "../.agents/skills/repo-only/SKILL.md"
    assert by_name["global-only"].path_label == "<global>/skills/global-only/SKILL.md"
    assert by_name["user-only"].path_label == "<user-agents>/skills/user-only/SKILL.md"
    assert all(str(tmp_path) not in s.path_label for s in skills)


def test_agents_ancestors_stop_at_the_git_root(tmp_path: Path) -> None:
    workspace, home, config = _layout(tmp_path)
    _skill(tmp_path / ".agents" / "skills" / "above-repo", "SKILL.md")
    _skill(workspace.parent / ".agents" / "skills" / "repo", "SKILL.md")

    skills = _discover(workspace, home=home, config_home=config)

    assert [s.name for s in skills] == ["repo"]


def test_untrusted_project_skips_project_roots_but_keeps_user_roots(
    tmp_path: Path,
) -> None:
    workspace, home, config = _layout(tmp_path)
    _skill(workspace / ".pipy" / "skills" / "pipy-project", "SKILL.md")
    _skill(workspace / ".agents" / "skills" / "agents-project", "SKILL.md")
    _skill(config / "skills" / "global", "SKILL.md")
    _skill(home / ".agents" / "skills" / "user", "SKILL.md")

    skills = _discover(workspace, home=home, config_home=config, trusted=False)

    assert [s.name for s in skills] == ["global", "user"]


def test_user_agents_skills_is_not_also_a_project_root(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = tmp_path / "config"
    config.mkdir()
    workspace = home / "project"
    workspace.mkdir(parents=True)
    _skill(home / ".agents" / "skills" / "mine", "SKILL.md")

    skills = _discover(workspace, home=home, config_home=config)

    assert [(s.name, s.path_label) for s in skills] == [
        ("mine", "<user-agents>/skills/mine/SKILL.md")
    ]


def test_ignore_files_apply_to_the_skill_walk(tmp_path: Path) -> None:
    workspace, home, config = _layout(tmp_path)
    root = workspace / ".pipy" / "skills"
    _skill(root / "kept", "SKILL.md")
    _skill(root / "dropped", "SKILL.md")
    _skill(root / "build", "SKILL.md")
    _skill(root / "group" / "keep-me", "SKILL.md")
    _skill(root / "group" / "hide-me", "SKILL.md")
    _skill(root, "draft.md")
    _skill(root, "draft-keep.md")
    (root / ".gitignore").write_text(
        "dropped/\ndraft*.md\n!draft-keep.md\n", encoding="utf-8"
    )
    (root / ".fdignore").write_text("build\n", encoding="utf-8")
    # A nested ignore file is prefixed with its directory.
    (root / "group" / ".ignore").write_text("hide-me/\n", encoding="utf-8")

    skills = _discover(workspace, home=home, config_home=config)

    assert sorted(s.name for s in skills) == ["draft-keep", "keep-me", "kept"]


def test_symlinked_skill_directory_escaping_the_root_is_not_loaded(
    tmp_path: Path,
) -> None:
    # pipy keeps its symlink-containment guard (documented deviation from Pi).
    workspace, home, config = _layout(tmp_path)
    outside = tmp_path / "outside" / "escaped"
    _skill(outside, "SKILL.md")
    root = workspace / ".pipy" / "skills"
    root.mkdir(parents=True)
    (root / "escaped").symlink_to(outside, target_is_directory=True)
    (root / "loop").symlink_to(root, target_is_directory=True)
    _skill(root / "inside", "SKILL.md")

    skills = _discover(workspace, home=home, config_home=config)

    assert [s.name for s in skills] == ["inside"]


def test_cli_skill_directory_uses_the_pi_layout(tmp_path: Path) -> None:
    workspace, home, config = _layout(tmp_path)
    cli_dir = tmp_path / "cli-skills"
    _skill(cli_dir / "from-cli", "SKILL.md")

    skills, _ = discover_workspace_skills(
        workspace,
        config_home_env={PIPY_CONFIG_HOME_ENV: str(config)},
        home_dir=home,
        explicit_paths=(cli_dir,),
    )

    assert [(s.name, s.path_label) for s in skills] == [
        ("from-cli", "<cli>/cli-skills/from-cli/SKILL.md")
    ]


# -- ignore matcher (node `ignore` semantics) ---------------------------------


def _matcher(*lines: str, prefix: str = "") -> IgnoreMatcher:
    matcher = IgnoreMatcher()
    matcher.add(
        [p for line in lines if (p := prefix_ignore_pattern(line, prefix)) is not None]
    )
    return matcher


def test_prefix_ignore_pattern_matches_pi() -> None:
    assert prefix_ignore_pattern("", "a/") is None
    assert prefix_ignore_pattern("   ", "a/") is None
    assert prefix_ignore_pattern("# comment", "a/") is None
    assert prefix_ignore_pattern("\\#literal", "a/") == "a/\\#literal"
    assert prefix_ignore_pattern("!keep.md", "a/") == "!a/keep.md"
    assert prefix_ignore_pattern("\\!bang", "") == "!bang"
    assert prefix_ignore_pattern("/rooted", "a/") == "a/rooted"
    assert prefix_ignore_pattern("plain", "") == "plain"


def test_unanchored_pattern_matches_at_any_depth() -> None:
    matcher = _matcher("*.log")
    assert matcher.ignores("x.log")
    assert matcher.ignores("a/b/x.log")
    assert not matcher.ignores("x.md")


def test_pattern_with_slash_is_anchored() -> None:
    matcher = _matcher("docs/*.md")
    assert matcher.ignores("docs/a.md")
    assert not matcher.ignores("x/docs/a.md")
    assert not matcher.ignores("docs/sub/a.md")


def test_double_star_forms() -> None:
    assert _matcher("**/tmp").ignores("a/b/tmp")
    assert _matcher("**/tmp").ignores("tmp")
    assert _matcher("a/**/z.md").ignores("a/z.md")
    assert _matcher("a/**/z.md").ignores("a/b/c/z.md")
    assert _matcher("a/**").ignores("a/b/c.md")
    assert not _matcher("a/**").ignores("a/")


def test_question_mark_and_bracket_classes() -> None:
    assert _matcher("?.md").ignores("x.md")
    assert not _matcher("?.md").ignores("xy.md")
    assert _matcher("[ab].md").ignores("a.md")
    assert not _matcher("[!ab].md").ignores("a.md")
    assert _matcher("[!ab].md").ignores("c.md")


def test_directory_only_rule_and_children_of_ignored_directories() -> None:
    matcher = _matcher("build/")
    assert matcher.ignores("build/")
    assert not matcher.ignores("build")
    assert matcher.ignores("build/SKILL.md")
    assert matcher.ignores("x/build/y/SKILL.md")


def test_last_matching_rule_wins_and_negation_needs_a_prior_match() -> None:
    matcher = _matcher("*.md", "!keep.md")
    assert matcher.ignores("drop.md")
    assert not matcher.ignores("keep.md")
    assert _matcher("!keep.md", "*.md").ignores("keep.md")


def test_negation_cannot_reinclude_a_file_under_an_ignored_directory() -> None:
    matcher = _matcher("dir/", "!dir/keep.md")
    assert matcher.ignores("dir/keep.md")


def test_prefixed_nested_rules_only_apply_below_their_directory() -> None:
    matcher = _matcher("hide-me/", prefix="group/")
    assert matcher.ignores("group/hide-me/")
    assert not matcher.ignores("hide-me/")


def test_block_and_multiline_descriptions_are_parsed(tmp_path: Path) -> None:
    workspace, home, config = _layout(tmp_path)
    root = workspace / ".pipy" / "skills"
    for name, frontmatter in (
        ("folded", "description: >-\n  Use when the task\n  needs folding.\n"),
        ("literal", "description: |\n  Line one.\n  Line two.\n"),
        ("plain", "description: Starts here\n  and continues.\n"),
        ("commented", "description: >- # summary\n  Folded after a comment.\n"),
        ("trailing", "description: Plain value # a comment\n"),
        ("quoted", "description: 'Keeps # inside quotes'\n"),
        ("nested", "metadata:\n  description: not top level\n"),
    ):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(
            f"---\n{frontmatter}---\nbody\n", encoding="utf-8"
        )

    skills = _discover(workspace, home=home, config_home=config)

    assert {s.name: s.description for s in skills} == {
        "folded": "Use when the task needs folding.",
        "literal": "Line one. Line two.",
        "plain": "Starts here and continues.",
        "commented": "Folded after a comment.",
        "trailing": "Plain value",
        "quoted": "Keeps # inside quotes",
    }
