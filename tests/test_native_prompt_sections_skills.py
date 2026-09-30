"""Pi's prompt sections per run and the "Skills and prompt" follow-on.

Pi references (pi-mono ``1b347794e``): ``system-prompt.ts``
``buildSystemPromptSections``, ``agent-session.ts``
(``_installAgentNextTurnRefresh``, ``_expandSkillCommand``,
``parseSkillBlock``), ``skills.ts`` (``loadSkillFromFile``,
``formatSkillsForPrompt``), ``resource-loader.ts`` (``mergePaths``),
``prompt-templates.ts`` (``loadTemplatesFromDir``) and
``components/skill-invocation-message.ts``.
"""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native._resource_files import PIPY_CONFIG_HOME_ENV
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.package_resources import PackageRoot
from pipy_harness.native.prompt_templates import discover_workspace_prompt_templates
from pipy_harness.native.skills import (
    SkillFile,
    discover_workspace_skills,
    parse_skill_block,
    skills_section_body,
)
from pipy_harness.native.system_prompt_sections import SystemPromptTemplate
from pipy_harness.native.tool_rows import (
    SkillInvocationBox,
    render_skill_invocation_box,
    row_theme,
)
from pipy_harness.native.tools.registry import production_tool_registry

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _skills(workspace: Path, tmp_path: Path, **kwargs: object) -> list[SkillFile]:
    skills, _ = discover_workspace_skills(
        workspace,
        config_home_env={PIPY_CONFIG_HOME_ENV: str(tmp_path / "config")},
        home_dir=tmp_path / "home",
        include_workspace_defaults=True,
        **kwargs,  # type: ignore[arg-type]
    )
    return skills


# -- skills discovery ------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "disabled"),
    [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("true  # only by command", True),
        ('"true"', False),
        ("false", False),
        ("yes", False),
    ],
)
def test_disable_model_invocation_is_a_yaml_true(
    tmp_path: Path, value: str, disabled: bool
) -> None:
    workspace = tmp_path / "ws"
    _write(
        workspace / ".pipy" / "skills" / "deploy" / "SKILL.md",
        f"---\ndescription: d\ndisable-model-invocation: {value}\n---\nbody\n",
    )
    (skill,) = _skills(workspace, tmp_path)
    assert skill.disable_model_invocation is disabled


def test_plain_md_skill_is_named_after_its_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write(workspace / ".pipy" / "skills" / "lint.md", "---\ndescription: d\n---\nb\n")
    _write(
        workspace / ".pipy" / "skills" / "named.md",
        "---\nname: named\ndescription: d\n---\nb\n",
    )
    # Pi `loadSkillFromFile`: `frontmatter.name || basename(dirname(filePath))`;
    # the unnamed file is `skills` and the named one keeps its name.
    assert [skill.name for skill in _skills(workspace, tmp_path)] == ["skills", "named"]


def test_packages_come_first_and_cli_paths_last(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    package = tmp_path / "pkg" / "skills"
    cli = tmp_path / "cli"
    for root, label in (
        (package, "package"),
        (workspace / ".pipy" / "skills", "workspace"),
        (cli, "cli"),
    ):
        _write(root / "same" / "SKILL.md", f"---\ndescription: {label}\n---\nb\n")
    _write(cli / "only-cli" / "SKILL.md", "---\ndescription: cli-only\n---\nb\n")
    skills = _skills(
        workspace,
        tmp_path,
        package_roots=(PackageRoot(path=package),),
        explicit_paths=(cli,),
    )
    # Pi `mergePaths([...cliEnabledSkills, ...enabledSkills], additionalSkillPaths)`:
    # package resources before the auto roots, `--skill` paths last; the
    # first skill of a name wins.
    assert [(skill.name, skill.description) for skill in skills] == [
        ("same", "package"),
        ("only-cli", "cli-only"),
    ]


def test_prompt_templates_follow_the_same_order(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    package = tmp_path / "pkg" / "prompts"
    cli = tmp_path / "cli.md"
    _write(package / "review.md", "---\ndescription: package\n---\nb\n")
    _write(
        workspace / ".pipy" / "templates" / "review.md",
        "---\ndescription: ws\n---\nb\n",
    )
    _write(cli, "---\ndescription: cli\n---\nb\n")
    templates, _ = discover_workspace_prompt_templates(
        workspace,
        config_home_env={PIPY_CONFIG_HOME_ENV: str(tmp_path / "config")},
        home_dir=tmp_path / "home",
        include_workspace_defaults=True,
        package_roots=(PackageRoot(path=package),),
        explicit_paths=(cli,),
    )
    assert [(t.name, t.description) for t in templates] == [
        ("review", "package"),
        ("cli", "cli"),
    ]


def test_template_store_follows_symlinks_like_pi(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    outside = _write(
        tmp_path / "elsewhere" / "shared.md", "---\ndescription: s\n---\nb\n"
    )
    store = workspace / ".pipy" / "templates"
    store.mkdir(parents=True)
    (store / "shared.md").symlink_to(outside)
    linked_store = tmp_path / "linked-store"
    _write(linked_store / "other.md", "---\ndescription: o\n---\nb\n")
    (workspace / ".pipy" / "commands").symlink_to(linked_store)
    templates, _ = discover_workspace_prompt_templates(
        workspace,
        config_home_env={PIPY_CONFIG_HOME_ENV: str(tmp_path / "config")},
        home_dir=tmp_path / "home",
        include_workspace_defaults=True,
    )
    assert [t.name for t in templates] == ["shared"]
    from pipy_harness.native.custom_commands import discover_workspace_custom_commands

    commands, _ = discover_workspace_custom_commands(
        workspace,
        config_home_env={PIPY_CONFIG_HOME_ENV: str(tmp_path / "config")},
        home_dir=tmp_path / "home",
        include_workspace_defaults=True,
    )
    assert [c.name for c in commands] == ["other"]


# -- skills section --------------------------------------------------------


def _skill(name: str, *, hidden: bool = False) -> SkillFile:
    return SkillFile(
        path_label=name,
        name=name,
        description=f"{name} skill",
        body="",
        sha256="0" * 64,
        byte_length=0,
        truncated=False,
        absolute_path=Path(f"/skills/{name}/SKILL.md"),
        disable_model_invocation=hidden,
    )


def test_skills_section_uses_bash_without_read_and_drops_hidden_skills() -> None:
    skills = (_skill("a"), _skill("b", hidden=True))
    template = SystemPromptTemplate(cwd="/w", skills=skills)

    by_read = dict(template.sections(("read", "bash")))["skills"]
    by_bash = dict(template.sections(("bash",)))["skills"]
    assert "Use the read tool to load a skill's file" in by_read
    assert "Use bash to load a skill's file" in by_bash
    assert "<name>a</name>" in by_bash and "<name>b</name>" not in by_bash
    assert "skills" not in dict(template.sections(("edit", "write")))
    assert skills_section_body((_skill("b", hidden=True),)) == ""


def test_tools_and_rules_follow_the_selected_tools() -> None:
    template = SystemPromptTemplate(cwd="/w")
    full = dict(
        template.sections(("read", "ls", "grep", "find", "write", "edit", "bash"))
    )
    narrowed = dict(template.sections(("bash", "custom")))

    assert "- read: Read file contents" in full["tools"]
    assert narrowed["tools"] == (
        "<tools>\n- bash: Execute bash commands (ls, grep, find, etc.)\n\n"
        "In addition to the tools above, you may have access to other custom tools "
        "depending on the project.\n</tools>"
    )
    assert narrowed["rules"] == (
        "<rules>\n- Use bash for file operations like ls, rg, find\n"
        "- Be concise in your responses\n"
        "- Show file paths clearly when working with files\n</rules>"
    )


def test_a_custom_prompt_replaces_tools_rules_and_docs() -> None:
    template = SystemPromptTemplate(cwd="/w", custom_preamble="CUSTOM", addendum="A")
    assert [name for name, _ in template.sections(("read",))] == [
        "preamble",
        "addendum",
        "cwd",
    ]
    empty = SystemPromptTemplate(cwd="/w", custom_preamble="")
    assert [name for name, _ in empty.sections(("read",))][:4] == [
        "preamble",
        "tools",
        "rules",
        "docs",
    ]


# -- sections rebuilt per run ------------------------------------------------


class _StubProvider:
    name = "stub"
    model_id = "stub-model"
    supports_mid_convo_system_messages = True
    supports_tool_calls = True

    def __init__(self, count: int) -> None:
        self.requests: list[ProviderRequest] = []
        self._count = count

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        now = datetime(2026, 9, 30, tzinfo=UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name="stub",
            model_id="stub-model",
            started_at=now,
            ended_at=now,
            final_text="ok",
        )


def test_active_tool_change_patches_tools_and_rules_at_the_next_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write(
        tmp_path / ".pipy" / "extensions" / "narrow.py",
        "def activate(api):\n"
        "    def narrow(ctx, _args):\n"
        "        assert ctx.set_active_tools(['bash'])\n"
        "    api.register_command('narrow', 'narrow the tools', narrow)\n",
    )
    # The session renders its loaded skills (the template's own are replaced).
    _write(
        tmp_path / ".pipy" / "skills" / "a" / "SKILL.md",
        "---\ndescription: d\n---\nb\n",
    )
    template = SystemPromptTemplate(cwd=str(tmp_path))
    provider = _StubProvider(2)
    session = CodingSession(
        provider=provider,
        tool_registry=production_tool_registry(),
        tool_budget=5,
        system_prompt_sections=template,
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("first\n/narrow\nsecond\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    first, second = provider.requests
    leading = dict(first.system_messages[0].message.sections)
    assert "- read: Read file contents" in (leading["tools"] or "")
    patch = dict(second.system_messages[-1].message.sections)
    # Pi `diffSystemPromptSections`: only the changed sections are patched.
    assert set(patch) == {"tools", "rules", "skills"}
    assert patch["tools"] == dict(template.sections(("bash",)))["tools"]
    assert "Use bash to load a skill's file" in (patch["skills"] or "")


# -- skill invocation block --------------------------------------------------


def test_parse_skill_block_matches_pi() -> None:
    block = '<skill name="lint" location="/s/lint/SKILL.md">\nbody\nmore\n</skill>'
    parsed = parse_skill_block(block)
    assert parsed is not None
    assert (parsed.name, parsed.location, parsed.content) == (
        "lint",
        "/s/lint/SKILL.md",
        "body\nmore",
    )
    assert parsed.user_message is None
    with_args = parse_skill_block(block + "\n\n  fix it \n")
    assert with_args is not None and with_args.user_message == "fix it"
    assert parse_skill_block(block + "\n") is None
    assert parse_skill_block("x" + block) is None
    blank_args = parse_skill_block(block + "\n\n   ")
    assert blank_args is not None and blank_args.user_message is None


def test_skill_box_rows_collapsed_and_expanded() -> None:
    box = SkillInvocationBox("lint", "line one\nline two")
    theme = row_theme_disabled()

    collapsed = render_skill_invocation_box(box, theme, expanded=False)
    expanded = render_skill_invocation_box(box, theme, expanded=True)

    assert [row.text for row in collapsed] == [
        "",
        "[skill] lint (ctrl+o to expand)",
        "",
        "",
    ]
    assert [row.bg for row in collapsed] == ["custom"] * 3 + ["none"]
    assert [row.text for row in expanded] == [
        "",
        "[skill]",
        "lint",
        "",
        "line one",
        "line two",
        "",
        "",
    ]


def row_theme_disabled():  # noqa: ANN201 - the row theme type is private
    from pipy_harness.native.chrome import chrome_style_for

    return row_theme(chrome_style_for(io.StringIO()))


def test_skill_box_styles_like_pi(monkeypatch: pytest.MonkeyPatch) -> None:
    from pipy_harness.native.chrome import chrome_style_for

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    theme = row_theme(chrome_style_for(_Tty()))
    (_, line, _, _) = render_skill_invocation_box(
        SkillInvocationBox("lint", "x"), theme, expanded=False
    )
    assert _ANSI.sub("", line.text) == "[skill] lint (ctrl+o to expand)"
    assert "\x1b[1m[skill]\x1b[22m " in line.text


def test_reload_rebuilds_the_skills_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    skill = _write(
        tmp_path / ".pipy" / "skills" / "lint" / "SKILL.md",
        "---\ndescription: Run linters\n---\nbody\n",
    )
    _write(
        tmp_path / ".pipy" / "extensions" / "hide.py",
        "from pathlib import Path\n"
        "def activate(api):\n"
        "    def hide(ctx, _args):\n"
        f"        Path({str(skill)!r}).write_text(\n"
        "            '---\\ndescription: Run linters\\n'\n"
        "            'disable-model-invocation: true\\n---\\nbody\\n')\n"
        "    api.register_command('hide', 'hide the skill', hide)\n",
    )
    provider = _StubProvider(2)
    session = CodingSession(
        provider=provider,
        tool_registry=production_tool_registry(),
        tool_budget=5,
        system_prompt_sections=SystemPromptTemplate(cwd=str(tmp_path)),
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("first\n/hide\n/reload\nsecond\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    first, second = provider.requests
    leading = dict(first.system_messages[0].message.sections)
    assert "<name>lint</name>" in (leading["skills"] or "")
    # Pi `_rebuildSystemPrompt` on reload: the hidden skill leaves the prompt.
    patch = dict(second.system_messages[-1].message.sections)
    assert patch == {"skills": None}


def test_input_hook_tool_change_reaches_a_forced_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write(
        tmp_path / ".pipy" / "extensions" / "narrow.py",
        "from pipy_harness.extensions import BeforeAgentStartResult\n"
        "def activate(api):\n"
        "    @api.on('input')\n"
        "    def narrow(event, ctx):\n"
        "        ctx.set_active_tools(['bash'])\n"
        "        return None\n"
        "    @api.on('before_agent_start')\n"
        "    def extra(event, ctx):\n"
        "        return BeforeAgentStartResult(append_system_prompt='EXTRA')\n",
    )
    provider = _StubProvider(1)
    session = CodingSession(
        provider=provider,
        tool_registry=production_tool_registry(),
        tool_budget=5,
        system_prompt_sections=SystemPromptTemplate(cwd=str(tmp_path)),
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    (request,) = provider.requests
    assert [tool.name for tool in request.available_tools] == ["bash"]
    # The forced prompt is rendered after the input hooks (Pi builds the
    # prompt after `input` handlers), so it lists only the active tool.
    assert request.system_prompt.endswith("\nEXTRA")
    assert "- bash: Execute bash commands" in request.system_prompt
    assert "- read: Read file contents" not in request.system_prompt


@pytest.mark.parametrize(
    ("frontmatter", "disabled"),
    [
        ("disable-model-invocation:\n  true\n", True),
        ("disable-model-invocation:\n\n  true\n", True),
        ("disable-model-invocation: true\n  extra\n", False),
        ("disable-model-invocation: true\nname: deploy\n", True),
        ("disable-model-invocation: true\n  # hidden on purpose\nname: deploy\n", True),
        ("disable-model-invocation:\n  # comment\n  true\n", True),
        ("disable-model-invocation: |\n  true\n", False),
        ("disable-model-invocation: >-\n  true\n", False),
        ("disable-model-invocation: 'true'\n", False),
    ],
)
def test_disable_model_invocation_scalar_forms(
    tmp_path: Path, frontmatter: str, disabled: bool
) -> None:
    workspace = tmp_path / "ws"
    _write(
        workspace / ".pipy" / "skills" / "deploy" / "SKILL.md",
        f"---\ndescription: d\n{frontmatter}---\nbody\n",
    )
    (skill,) = _skills(workspace, tmp_path)
    assert skill.disable_model_invocation is disabled
