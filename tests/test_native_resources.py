"""Unit tests for the runtime resource registry and dispatcher.

These pin the pure dispatch contract used by the product tool-loop session:
run / reject / pass-through (Pi's `/skill:<name> [args]` skill block), fail-closed behaviour for unknown
or unsafe resources, reserved-name collision handling, and the
archive-safe metadata projection (no body, description, or expanded
text).
"""

from __future__ import annotations

from pathlib import Path

from pipy_harness.native.coding.command_registry import builtin_command_names
from pipy_harness.native.resources import (
    DISPATCH_COMMAND_RUN,
    DISPATCH_REJECT,
    DISPATCH_SKILL_RUN,
    DISPATCH_TEMPLATE_RUN,
    RESERVED_COMMAND_NAMES,
    WorkspaceResources,
    dispatch_resource_command,
)


def _write(
    directory: Path, filename: str, *, name: str, description: str, body: str
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    text = f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"
    (directory / filename).write_text(text, encoding="utf-8")


def _resources(tmp_path: Path) -> WorkspaceResources:
    workspace = tmp_path / "ws"
    pipy = workspace / ".pipy"
    _write(
        pipy / "skills",
        "lint.md",
        name="lint",
        description="Run linters",
        body="Apply the project's lint rules.\n",
    )
    _write(
        pipy / "skills",
        "empty.md",
        name="empty",
        description="frontmatter only",
        body="",
    )
    _write(
        pipy / "templates",
        "review.md",
        name="review",
        description="Review the diff",
        body="Please review $ARGUMENTS carefully.\n",
    )
    _write(
        pipy / "commands",
        "deploy.md",
        name="deploy",
        description="Deploy summary",
        body="Summarize the deploy for $ARGUMENTS.\n",
    )
    # A custom command that tries to shadow a built-in is dropped.
    _write(
        pipy / "commands",
        "model.md",
        name="model",
        description="should be ignored",
        body="should never run\n",
    )
    workspace.mkdir(exist_ok=True)
    return WorkspaceResources.discover(
        workspace,
        config_home_env={},
        home_dir=workspace,
        include_workspace_defaults=True,
    )


def test_discovery_collects_all_three_kinds(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    assert set(resources.skill_names()) == {"lint", "empty"}
    assert set(resources.template_names()) == {"review"}
    assert {c.name for c in resources.commands} == {"deploy", "model"}


def test_custom_command_slash_names_excludes_reserved(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    names = resources.custom_command_slash_names()
    assert "/deploy" in names
    assert "/model" not in names  # reserved built-in collision dropped


def test_reserved_command_names_covers_full_registry_plus_adjuncts() -> None:
    # RESERVED_COMMAND_NAMES is derived from the single declarative-registry
    # source (every built-in ``/…`` name + alias, without the slash) unioned with
    # the ``theme`` resource adjunct (skills are ``/skill:<name>``). This is the widened Phase 3.2
    # advertising-completeness set: every built-in the kernel can classify is
    # reserved, not just the subset advertised in the completion menus.
    registry_names = {name.lstrip("/") for name in builtin_command_names()}
    assert registry_names <= RESERVED_COMMAND_NAMES
    assert RESERVED_COMMAND_NAMES == registry_names | {"theme"}
    # Built-ins that were absent from the pre-3.2 hardcoded reserved set are now
    # reserved (they can never be shadowed / advertised by a colliding resource).
    for widened in ("reload", "tree", "new", "fork", "session", "compact", "export"):
        assert widened in RESERVED_COMMAND_NAMES
    # ``template`` remains unreserved: there is no ``/template`` built-in.
    assert "template" not in RESERVED_COMMAND_NAMES


def test_custom_command_colliding_with_widened_builtin_is_dropped(
    tmp_path: Path,
) -> None:
    # A custom command named after a built-in that was NOT in the pre-3.2
    # reserved set (e.g. ``reload``) is now dropped from slash discovery and is
    # never claimed by resource dispatch.
    workspace = tmp_path / "ws"
    _write(
        workspace / ".pipy" / "commands",
        "reload.md",
        name="reload",
        description="should be ignored",
        body="never runs\n",
    )
    workspace.mkdir(exist_ok=True)
    resources = WorkspaceResources.discover(
        workspace,
        config_home_env={},
        home_dir=workspace,
        include_workspace_defaults=True,
    )
    # Discovered on disk, but not advertised (reserved built-in collision).
    assert {c.name for c in resources.commands} == {"reload"}
    assert "/reload" not in resources.custom_command_slash_names()
    assert "/reload" not in resources.custom_command_descriptions()
    # And never claimed by resource dispatch: it falls through to ``None`` so the
    # caller's fail-closed unknown-command path handles it.
    assert dispatch_resource_command("/reload", resources) is None


def test_template_colliding_with_widened_builtin_is_dropped(tmp_path: Path) -> None:
    # A prompt template named after a widened built-in (e.g. ``tree``) is not
    # advertised and never dispatched as a template run.
    workspace = tmp_path / "ws"
    _write(
        workspace / ".pipy" / "templates",
        "tree.md",
        name="tree",
        description="should be ignored",
        body="Please expand $ARGUMENTS.\n",
    )
    workspace.mkdir(exist_ok=True)
    resources = WorkspaceResources.discover(
        workspace,
        config_home_env={},
        home_dir=workspace,
        include_workspace_defaults=True,
    )
    assert "/tree" not in resources.template_slash_names()
    assert dispatch_resource_command("/tree the repo", resources) is None


def test_dispatch_passthrough_for_non_resource(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    assert dispatch_resource_command("hello there", resources) is None
    assert dispatch_resource_command("/unknown-thing", resources) is None
    # A custom command that collides with a built-in is never claimed here.
    assert dispatch_resource_command("/model", resources) is None


def test_bare_skill_is_not_a_command(tmp_path: Path) -> None:
    # Pi has no `/skill` command; pipy's `/skill` listing is gone.
    resources = _resources(tmp_path)
    assert dispatch_resource_command("/skill", resources) is None
    assert dispatch_resource_command("/skill lint", resources) is None


def test_skill_run_sends_pis_skill_block_and_safe_metadata(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    skill = next(s for s in resources.skills if s.name == "lint")
    location = skill.absolute_path
    result = dispatch_resource_command("/skill:lint", resources)
    assert result is not None and result.kind == DISPATCH_SKILL_RUN
    # Pi `_expandSkillCommand`: frontmatter stripped, body trimmed.
    assert result.provider_text == (
        f'<skill name="lint" location="{location}">\n'
        f"References are relative to {location.parent}.\n\n"
        "Apply the project's lint rules.\n</skill>"
    )
    assert result.message == ""
    meta = result.safe_metadata
    assert meta is not None
    assert meta["name"] == "lint"
    assert meta["resource_kind"] == "skill"
    assert set(meta.keys()) == {
        "resource_kind",
        "name",
        "path_label",
        "sha256",
        "byte_length",
        "truncated",
    }
    # The instruction body must not appear in the recorded metadata.
    assert "Apply the project" not in str(meta)


def test_skill_arguments_follow_the_block(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    result = dispatch_resource_command("/skill:lint  fix src/app.py ", resources)
    assert result is not None and result.provider_text is not None
    assert result.provider_text.endswith("\n</skill>\n\nfix src/app.py")


def test_skill_is_read_again_at_invocation(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    skill = next(s for s in resources.skills if s.name == "lint")
    skill.absolute_path.write_text(
        "---\nname: lint\ndescription: Run linters\n---\nEDITED\n", encoding="utf-8"
    )
    result = dispatch_resource_command("/skill:lint", resources)
    assert result is not None and result.provider_text is not None
    assert "\n\nEDITED\n</skill>" in result.provider_text


def test_skill_unknown_or_unreadable_rejects(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    result = dispatch_resource_command("/skill:nope", resources)
    assert result is not None and result.kind == DISPATCH_REJECT
    assert result.provider_text is None
    next(s for s in resources.skills if s.name == "lint").absolute_path.unlink()
    result = dispatch_resource_command("/skill:lint", resources)
    assert result is not None and result.kind == DISPATCH_REJECT


def test_skill_empty_body_sends_an_empty_block(tmp_path: Path) -> None:
    # Pi sends the block even when the skill has no body.
    resources = _resources(tmp_path)
    result = dispatch_resource_command("/skill:empty", resources)
    assert result is not None and result.kind == DISPATCH_SKILL_RUN
    assert result.provider_text is not None
    assert result.provider_text.endswith(".\n\n\n</skill>")


def test_skill_commands_setting_hides_menu_entries_only(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    assert resources.skill_slash_names() == ("/skill:empty", "/skill:lint")
    assert resources.skill_descriptions()["/skill:lint"] == "Run linters"
    hidden = resources.with_enablement(enable_skill_commands=False)
    assert hidden.skill_slash_names() == ()
    assert hidden.skill_descriptions() == {}
    # Pi `enableSkillCommands` only hides the commands: the skills stay
    # (system prompt) and a typed `/skill:<name>` still expands.
    assert hidden.skills == resources.skills
    result = dispatch_resource_command("/skill:lint", hidden)
    assert result is not None and result.kind == DISPATCH_SKILL_RUN


def test_template_run_expands_arguments(tmp_path: Path) -> None:
    # Templates are invoked directly by their own name (Pi shape); there is no
    # ``/template`` wrapper command.
    resources = _resources(tmp_path)
    result = dispatch_resource_command("/review the auth module", resources)
    assert result is not None and result.kind == DISPATCH_TEMPLATE_RUN
    assert result.provider_text is not None
    assert result.provider_text.strip() == "Please review the auth module carefully."
    assert result.safe_metadata is not None
    assert result.safe_metadata["name"] == "review"


def test_template_wrapper_command_is_gone(tmp_path: Path) -> None:
    # ``/template`` is no longer a built-in resource command; with no template
    # named "template" it passes through to the caller's unknown-command path.
    resources = _resources(tmp_path)
    assert dispatch_resource_command("/template", resources) is None
    assert dispatch_resource_command("/template review", resources) is None


def test_custom_command_run_expands(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    result = dispatch_resource_command("/deploy staging", resources)
    assert result is not None and result.kind == DISPATCH_COMMAND_RUN
    assert result.provider_text is not None
    assert result.provider_text.strip() == "Summarize the deploy for staging."
    meta = result.safe_metadata
    assert meta is not None
    assert meta["name"] == "deploy"
    assert meta["resource_kind"] == "custom_command"
    assert "Summarize the deploy" not in str(meta)


def test_no_resources_dispatch_is_inert(tmp_path: Path) -> None:
    workspace = tmp_path / "bare"
    workspace.mkdir()
    resources = WorkspaceResources.discover(
        workspace,
        config_home_env={},
        home_dir=workspace,
        include_workspace_defaults=True,
    )
    assert resources.has_any() is False
    assert dispatch_resource_command("/skill", resources) is None
    # /template is no longer a built-in; it passes through like any unknown
    # command (templates are invoked as /<name>).
    assert dispatch_resource_command("/template", resources) is None
    # An unknown custom command passes through.
    assert dispatch_resource_command("/whatever", resources) is None


# --- review follow-up: label safety + executable-token honesty -------------


def _write_raw(directory: Path, filename: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(text, encoding="utf-8")


def test_control_bytes_in_name_and_description_are_stripped(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_raw(
        workspace / ".pipy" / "skills",
        "evil.md",
        "---\nname: clear\x1b[2Jname\ndescription: wipe\x1b[2Jscreen\x07\n---\nBODY\n",
    )
    resources = WorkspaceResources.discover(
        workspace,
        config_home_env={},
        home_dir=workspace,
        include_workspace_defaults=True,
    )
    skill = resources.skills[0]
    assert "\x1b" not in skill.name and "\x1b" not in skill.description
    assert "\x07" not in skill.description
    assert all(
        "\x1b" not in key + value
        for key, value in resources.skill_descriptions().items()
    )


def test_control_bytes_in_custom_command_description_are_stripped(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    _write_raw(
        workspace / ".pipy" / "commands",
        "dep.md",
        "---\nname: dep\ndescription: do\x1b[2Jthing\n---\nBODY\n",
    )
    resources = WorkspaceResources.discover(
        workspace,
        config_home_env={},
        home_dir=workspace,
        include_workspace_defaults=True,
    )
    descriptions = resources.custom_command_descriptions()
    assert "\x1b" not in descriptions["/dep"]


def test_whitespace_named_command_is_not_advertised_or_dispatched(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    _write_raw(
        workspace / ".pipy" / "commands",
        "spaced.md",
        "---\nname: deploy now\ndescription: spaced\n---\nBODY\n",
    )
    resources = WorkspaceResources.discover(
        workspace,
        config_home_env={},
        home_dir=workspace,
        include_workspace_defaults=True,
    )
    # Not advertised in slash discovery (could never be invoked).
    assert resources.custom_command_slash_names() == ()
    assert resources.custom_command_descriptions() == {}
    # And not claimed by the dispatcher: it falls through to the caller's
    # unknown-command (fail-closed) handling.
    assert dispatch_resource_command("/deploy now", resources) is None


def test_multi_word_skill_name_is_not_a_command(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_raw(
        workspace / ".pipy" / "skills",
        "multi.md",
        "---\nname: code review\ndescription: review\n---\nREVIEWBODY\n",
    )
    resources = WorkspaceResources.discover(
        workspace,
        config_home_env={},
        home_dir=workspace,
        include_workspace_defaults=True,
    )
    # Pi splits `/skill:<name>` at the first space, so `code review` cannot
    # run: `/skill:code review` looks up `code` with the argument `review`.
    result = dispatch_resource_command("/skill:code review", resources)
    assert result is not None and result.kind == DISPATCH_REJECT
