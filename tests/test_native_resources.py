"""Unit tests for the runtime resource registry and dispatcher.

These pin the pure dispatch contract used by both REPL product paths:
list / run / reject / pass-through, fail-closed behaviour for unknown
or unsafe resources, reserved-name collision handling, and the
archive-safe metadata projection (no body, description, or expanded
text).
"""

from __future__ import annotations

from pathlib import Path

from pipy_harness.native.resources import (
    DISPATCH_COMMAND_RUN,
    DISPATCH_LIST,
    DISPATCH_REJECT,
    DISPATCH_SKILL_RUN,
    DISPATCH_TEMPLATE_RUN,
    WorkspaceResources,
    dispatch_resource_command,
)


def _write(directory: Path, filename: str, *, name: str, description: str, body: str) -> None:
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
        "help.md",
        name="help",
        description="should be ignored",
        body="should never run\n",
    )
    workspace.mkdir(exist_ok=True)
    return WorkspaceResources.discover(
        workspace, config_home_env={}, home_dir=workspace
    )


def test_discovery_collects_all_three_kinds(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    assert set(resources.skill_names()) == {"lint", "empty"}
    assert set(resources.template_names()) == {"review"}
    assert {c.name for c in resources.commands} == {"deploy", "help"}


def test_custom_command_slash_names_excludes_reserved(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    names = resources.custom_command_slash_names()
    assert "/deploy" in names
    assert "/help" not in names  # reserved built-in collision dropped


def test_dispatch_passthrough_for_non_resource(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    assert dispatch_resource_command("hello there", resources) is None
    assert dispatch_resource_command("/unknown-thing", resources) is None
    # A custom command that collides with a built-in is never claimed here.
    assert dispatch_resource_command("/help", resources) is None


def test_skill_bare_lists(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    result = dispatch_resource_command("/skill", resources)
    assert result is not None and result.kind == DISPATCH_LIST
    assert "lint" in result.message
    assert "Apply the project" not in result.message  # body never leaks


def test_skill_run_returns_body_and_safe_metadata(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    result = dispatch_resource_command("/skill lint", resources)
    assert result is not None and result.kind == DISPATCH_SKILL_RUN
    assert result.provider_text is not None
    assert "Apply the project's lint rules." in result.provider_text
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


def test_skill_unknown_rejects(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    result = dispatch_resource_command("/skill nope", resources)
    assert result is not None and result.kind == DISPATCH_REJECT
    assert result.provider_text is None


def test_skill_empty_body_rejects(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    result = dispatch_resource_command("/skill empty", resources)
    assert result is not None and result.kind == DISPATCH_REJECT
    assert result.provider_text is None


def test_template_run_expands_arguments(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    result = dispatch_resource_command("/template review the auth module", resources)
    assert result is not None and result.kind == DISPATCH_TEMPLATE_RUN
    assert result.provider_text is not None
    assert result.provider_text.strip() == "Please review the auth module carefully."
    assert result.safe_metadata is not None
    assert result.safe_metadata["name"] == "review"


def test_template_unknown_rejects(tmp_path: Path) -> None:
    resources = _resources(tmp_path)
    result = dispatch_resource_command("/template missing", resources)
    assert result is not None and result.kind == DISPATCH_REJECT


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
        workspace, config_home_env={}, home_dir=workspace
    )
    assert resources.has_any() is False
    # /skill and /template still respond locally (empty listing), never None.
    skill_result = dispatch_resource_command("/skill", resources)
    assert skill_result is not None and skill_result.kind == DISPATCH_LIST
    template_result = dispatch_resource_command("/template", resources)
    assert template_result is not None and template_result.kind == DISPATCH_LIST
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
        workspace, config_home_env={}, home_dir=workspace
    )
    skill = resources.skills[0]
    assert "\x1b" not in skill.name and "\x1b" not in skill.description
    assert "\x07" not in skill.description
    listing = dispatch_resource_command("/skill", resources)
    assert listing is not None
    assert "\x1b" not in listing.message


def test_control_bytes_in_custom_command_description_are_stripped(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_raw(
        workspace / ".pipy" / "commands",
        "dep.md",
        "---\nname: dep\ndescription: do\x1b[2Jthing\n---\nBODY\n",
    )
    resources = WorkspaceResources.discover(
        workspace, config_home_env={}, home_dir=workspace
    )
    descriptions = resources.custom_command_descriptions()
    assert "\x1b" not in descriptions["/dep"]


def test_whitespace_named_command_is_not_advertised_or_dispatched(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_raw(
        workspace / ".pipy" / "commands",
        "spaced.md",
        "---\nname: deploy now\ndescription: spaced\n---\nBODY\n",
    )
    resources = WorkspaceResources.discover(
        workspace, config_home_env={}, home_dir=workspace
    )
    # Not advertised in slash discovery (could never be invoked).
    assert resources.custom_command_slash_names() == ()
    assert resources.custom_command_descriptions() == {}
    # And not claimed by the dispatcher: it falls through to the caller's
    # unknown-command (fail-closed) handling.
    assert dispatch_resource_command("/deploy now", resources) is None


def test_multi_word_skill_name_loads_from_full_argument(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _write_raw(
        workspace / ".pipy" / "skills",
        "multi.md",
        "---\nname: code review\ndescription: review\n---\nREVIEWBODY\n",
    )
    resources = WorkspaceResources.discover(
        workspace, config_home_env={}, home_dir=workspace
    )
    result = dispatch_resource_command("/skill code review", resources)
    assert result is not None and result.kind == DISPATCH_SKILL_RUN
    assert result.provider_text is not None and "REVIEWBODY" in result.provider_text
