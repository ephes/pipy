from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.coding import commands as commands_module
from pipy_harness.native.coding.command_registry import (
    _BUILTIN_COMMANDS,
    BuiltinArgumentContract,
    BuiltinCommandKind,
    BuiltinCommandSpec,
    builtin_command_description,
    builtin_command_names,
    classify_coding_command,
    project_command_completions,
    project_command_descriptions,
)
from pipy_harness.native.coding.commands import (
    CodingCommandAction,
    CodingCommandFooterPolicy,
    CodingCommandOutcome,
    CodingCommandOutcomeKind,
    CommandDispatchResolution,
    CommandDispatchResolutionKind,
    require_exact_coding_command_outcome,
)


@pytest.mark.parametrize("command", ["/exit", "/quit"])
def test_exit_commands_return_the_exact_terminal_outcome(command: str) -> None:
    outcome = classify_coding_command(ProductContent(command))

    assert type(outcome) is CodingCommandOutcome
    assert outcome == CodingCommandOutcome(CodingCommandOutcomeKind.EXIT)


@pytest.mark.parametrize(
    ("command", "action"),
    [
        ("/hotkeys", CodingCommandAction.SHOW_HOTKEYS),
        ("/changelog", CodingCommandAction.SHOW_CHANGELOG),
        ("/copy", CodingCommandAction.COPY_LAST_ANSWER),
        ("/session", CodingCommandAction.SHOW_SESSION_STATUS),
        ("/compact", CodingCommandAction.COMPACT),
        ("/new", CodingCommandAction.NEW_SESSION),
    ],
)
def test_action_commands_return_standard_continuing_outcomes(
    command: str,
    action: CodingCommandAction,
) -> None:
    outcome = classify_coding_command(ProductContent(command))

    assert outcome == CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.STANDARD,
    )


def test_blank_input_continues_with_no_action_and_standard_footer() -> None:
    assert classify_coding_command(ProductContent("")) == CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        footer_policy=CodingCommandFooterPolicy.STANDARD,
    )


@pytest.mark.parametrize(
    ("command", "expected_argument"),
    [
        ("/name", ""),
        ("/name session", "session"),
        ("/name   session name", "session name"),
        ("/name   session  name", "session  name"),
        ("/name \t session name", "session name"),
    ],
)
def test_name_command_returns_exact_stripped_product_argument(
    command: str,
    expected_argument: str,
) -> None:
    outcome = classify_coding_command(ProductContent(command))

    assert outcome == CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        CodingCommandAction.SESSION_NAME,
        CodingCommandFooterPolicy.STANDARD,
        ProductContent(expected_argument),
    )
    assert type(outcome.argument) is ProductContent
    assert outcome.argument.value == expected_argument


@pytest.mark.parametrize(
    ("command", "expected_argument"),
    [
        ("/tree", ""),
        ("/tree select 3", "select 3"),
        (
            "/tree   select  3 summarize:focus words",
            "select  3 summarize:focus words",
        ),
    ],
)
def test_session_tree_commands_return_exact_standard_product_arguments(
    command: str,
    expected_argument: str,
) -> None:
    first = classify_coding_command(ProductContent(command))
    second = classify_coding_command(ProductContent(command))

    action = CodingCommandAction["SESSION_TREE"]
    expected = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.STANDARD,
        ProductContent(expected_argument),
    )
    assert first == expected
    assert type(first.argument) is ProductContent
    assert first.argument.value == expected_argument
    assert second == first
    assert second is not first
    assert second.argument is not first.argument


@pytest.mark.parametrize(
    ("command", "expected_argument"),
    [
        ("/resume", ""),
        ("/resume named", "named"),
        ("/resume   rename  3  focused work", "rename  3  focused work"),
    ],
)
def test_session_resume_commands_return_exact_standard_product_arguments(
    command: str,
    expected_argument: str,
) -> None:
    first = classify_coding_command(ProductContent(command))
    second = classify_coding_command(ProductContent(command))

    action = CodingCommandAction["SESSION_RESUME"]
    expected = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.STANDARD,
        ProductContent(expected_argument),
    )
    assert first == expected
    assert type(first.argument) is ProductContent
    assert first.argument.value == expected_argument
    assert second == first
    assert second is not first
    assert second.argument is not first.argument


@pytest.mark.parametrize(
    ("command", "expected_argument"),
    [
        ("/fork", ""),
        ("/fork 1", "1"),
        ("/fork   branch  3", "branch  3"),
    ],
)
def test_session_fork_commands_return_exact_standard_product_arguments(
    command: str,
    expected_argument: str,
) -> None:
    first = classify_coding_command(ProductContent(command))
    second = classify_coding_command(ProductContent(command))

    action = CodingCommandAction["SESSION_FORK"]
    expected = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.STANDARD,
        ProductContent(expected_argument),
    )
    assert first == expected
    assert type(first.argument) is ProductContent
    assert first.argument.value == expected_argument
    assert second == first
    assert second is not first
    assert second.argument is not first.argument


@pytest.mark.parametrize(
    ("command", "expected_argument"),
    [
        ("/export", ""),
        ("/export session.html", "session.html"),
        (
            '/export   "artifacts/full session.html" trailing tokens',
            '"artifacts/full session.html" trailing tokens',
        ),
    ],
)
def test_session_export_commands_return_exact_standard_product_arguments(
    command: str,
    expected_argument: str,
) -> None:
    first = classify_coding_command(ProductContent(command))
    second = classify_coding_command(ProductContent(command))

    action = CodingCommandAction["SESSION_EXPORT"]
    expected = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.STANDARD,
        ProductContent(expected_argument),
    )
    assert first == expected
    assert type(first.argument) is ProductContent
    assert first.argument.value == expected_argument
    assert second == first
    assert second is not first
    assert second.argument is not first.argument


@pytest.mark.parametrize(
    ("command", "expected_argument"),
    [
        ("/import", ""),
        ("/import source.jsonl", "source.jsonl"),
        ("/import source.jsonl --yes", "source.jsonl --yes"),
        (
            '/import   "artifacts/full session.jsonl" --yes',
            '"artifacts/full session.jsonl" --yes',
        ),
    ],
)
def test_session_import_commands_return_exact_standard_product_arguments(
    command: str,
    expected_argument: str,
) -> None:
    first = classify_coding_command(ProductContent(command))
    second = classify_coding_command(ProductContent(command))

    action = CodingCommandAction["SESSION_IMPORT"]
    expected = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.STANDARD,
        ProductContent(expected_argument),
    )
    assert first == expected
    assert type(first.argument) is ProductContent
    assert first.argument.value == expected_argument
    assert second == first
    assert second is not first
    assert second.argument is not first.argument


def test_session_clone_command_returns_exact_standard_payload_free_outcome() -> None:
    first = classify_coding_command(ProductContent("/clone"))
    second = classify_coding_command(ProductContent("/clone"))

    action = CodingCommandAction["SESSION_CLONE"]
    expected = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.STANDARD,
    )
    assert first == expected
    assert first.argument is None
    assert second == first
    assert second is not first


def test_trust_command_returns_exact_standard_payload_free_outcome() -> None:
    first = classify_coding_command(ProductContent("/trust"))
    second = classify_coding_command(ProductContent("/trust"))

    action = CodingCommandAction["TRUST_PROJECT"]
    expected = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.STANDARD,
    )
    assert first == expected
    assert first.argument is None
    assert second == first
    assert second is not first


def test_settings_command_returns_exact_standard_payload_free_outcome() -> None:
    first = classify_coding_command(ProductContent("/settings"))
    second = classify_coding_command(ProductContent("/settings"))

    action = CodingCommandAction["SETTINGS"]
    expected = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.STANDARD,
    )
    assert first == expected
    assert first.argument is None
    assert second == first
    assert second is not first


def test_share_command_returns_exact_standard_payload_free_outcome() -> None:
    first = classify_coding_command(ProductContent("/share"))
    second = classify_coding_command(ProductContent("/share"))

    action = CodingCommandAction["SESSION_SHARE"]
    expected = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.STANDARD,
    )
    assert first == expected
    assert first.argument is None
    assert second == first
    assert second is not first


def test_reload_command_returns_exact_standard_payload_free_outcome() -> None:
    first = classify_coding_command(ProductContent("/reload"))
    second = classify_coding_command(ProductContent("/reload"))

    action = CodingCommandAction["RELOAD"]
    expected = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.STANDARD,
    )
    assert first == expected
    assert first.argument is None
    assert second == first
    assert second is not first


@pytest.mark.parametrize(
    ("command", "action", "expected_argument"),
    [
        ("/model", CodingCommandAction.MODEL, ""),
        ("/model openai/gpt-5", CodingCommandAction.MODEL, "openai/gpt-5"),
        (
            "/model   openai/model  label",
            CodingCommandAction.MODEL,
            "openai/model  label",
        ),
        ("/scoped-models", CodingCommandAction.SCOPED_MODELS, ""),
        (
            "/scoped-models openai/*  anthropic/*",
            CodingCommandAction.SCOPED_MODELS,
            "openai/*  anthropic/*",
        ),
        ("/login", CodingCommandAction.LOGIN, ""),
        ("/login openai-codex", CodingCommandAction.LOGIN, "openai-codex"),
        ("/logout", CodingCommandAction.LOGOUT, ""),
        ("/logout openai-codex", CodingCommandAction.LOGOUT, "openai-codex"),
    ],
)
def test_usage_aware_commands_return_exact_stripped_product_arguments(
    command: str,
    action: CodingCommandAction,
    expected_argument: str,
) -> None:
    outcome = classify_coding_command(ProductContent(command))

    assert outcome == CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.USAGE_AWARE,
        ProductContent(expected_argument),
    )
    assert type(outcome.argument) is ProductContent


@pytest.mark.parametrize(
    "command", ["/model x", "/scoped-models x", "/login x", "/logout x"]
)
def test_usage_aware_classification_returns_fresh_deterministic_outcomes(
    command: str,
) -> None:
    first = classify_coding_command(ProductContent(command))
    second = classify_coding_command(ProductContent(command))

    assert first == second
    assert first is not second
    assert first.argument is not second.argument


@pytest.mark.parametrize(
    "value",
    [
        " ",
        " /exit",
        "/exit ",
        "/EXIT",
        "/Compact",
        "/compact x",
        "/compact ",
        "/new ",
        "/new session",
        "/new\tsession",
        "/NEW",
        " /tree",
        "/tree ",
        "/tree select 1 ",
        "/tree\tselect 1",
        "/TREE",
        "/trees",
        "/tree/select 1",
        " /resume",
        "/resume ",
        "/resume named ",
        "/resume\tnamed",
        "/RESUME",
        "/resumes",
        "/resume/delete victim",
        " /fork",
        "/fork ",
        "/fork 1 ",
        "/fork\t1",
        "/FORK",
        "/forked",
        "/fork/1",
        " /export",
        "/export ",
        "/export session.html ",
        "/export\tsession.html",
        "/EXPORT",
        "/exports",
        "/export/session.html",
        " /import",
        "/import ",
        "/import source.jsonl ",
        "/import\tsource.jsonl",
        "/IMPORT",
        "/imports",
        "/import/source.jsonl",
        " /clone",
        "/clone ",
        "/clone anything",
        "/clone\tanything",
        "/CLONE",
        "/clones",
        "/clone/anything",
        " /trust",
        "/trust ",
        "/trust anything",
        "/trust\tanything",
        "/TRUST",
        "/trusted",
        "/trust/anything",
        " /settings",
        "/settings ",
        "/settings anything",
        "/settings\tanything",
        "/SETTINGS",
        "/settings-menu",
        "/settings/anything",
        " /share",
        "/share ",
        "/share foo",
        "/share\tfoo",
        "/SHARE",
        "/shared",
        "/share/anything",
        " /reload",
        "/reload ",
        "/reload anything",
        "/reload\tanything",
        "/RELOAD",
        "/reloads",
        "/reload/anything",
        "/NAME",
        "/name ",
        "/name    ",
        "/name session ",
        "/name session\t",
        "/name\tvalue",
        "/names",
        "/name:value",
        "/unknown",
        "/model ",
        "/model gpt ",
        "/model\tgpt",
        "/models gpt",
        "/MODEL gpt",
        "/scoped-models ",
        "/scoped-models next ",
        "/scoped-models\tnext",
        "/scoped-models-next",
        "/SCOPED-MODELS next",
        "/login ",
        "/login openai ",
        "/login\topenai",
        "/logins openai",
        "/LOGIN openai",
        "/logout ",
        "/logout openai ",
        "/logout\topenai",
        "/logouts openai",
        "/LOGOUT openai",
        "/extension:command",
        "!pwd",
        "ctrl-x",
        "ordinary prompt",
    ],
)
def test_all_other_input_falls_through_unchanged(value: str) -> None:
    assert classify_coding_command(ProductContent(value)) == CodingCommandOutcome(
        CodingCommandOutcomeKind.UNHANDLED
    )


def test_classification_returns_fresh_outcomes() -> None:
    first = classify_coding_command(ProductContent("/copy"))
    second = classify_coding_command(ProductContent("/copy"))

    assert first == second
    assert first is not second


def test_outcomes_are_frozen_and_slotted() -> None:
    outcome = CodingCommandOutcome(CodingCommandOutcomeKind.EXIT)

    assert not hasattr(outcome, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(outcome, "kind", CodingCommandOutcomeKind.UNHANDLED)


class _OutcomeSubclass(CodingCommandOutcome):
    pass


def test_validator_rejects_outcome_subclasses() -> None:
    with pytest.raises(TypeError, match="exact CodingCommandOutcome"):
        _OutcomeSubclass(CodingCommandOutcomeKind.EXIT)


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "message"),
    [
        ("kind", "continue", "outcome.kind"),
        ("action", "show_hotkeys", "outcome.action"),
        ("action", [], "outcome.action"),
        ("footer_policy", "standard", "outcome.footer_policy"),
        ("footer_policy", [], "outcome.footer_policy"),
        ("argument", "session", "outcome.argument"),
        ("argument", [], "outcome.argument"),
    ],
)
def test_validator_rejects_corrupted_exact_field_values(
    field_name: str,
    invalid_value: object,
    message: str,
) -> None:
    outcome = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        CodingCommandAction.SHOW_HOTKEYS,
        CodingCommandFooterPolicy.STANDARD,
    )
    object.__setattr__(outcome, field_name, invalid_value)

    with pytest.raises(TypeError, match=message):
        require_exact_coding_command_outcome(outcome)


def test_outcome_type_validation_preserves_first_failure_order() -> None:
    outcome = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        CodingCommandAction.SHOW_HOTKEYS,
        CodingCommandFooterPolicy.STANDARD,
    )
    object.__setattr__(outcome, "action", "show_hotkeys")
    object.__setattr__(outcome, "footer_policy", "standard")

    with pytest.raises(TypeError, match="outcome.action"):
        require_exact_coding_command_outcome(outcome)


def test_dispatch_payload_validation_preserves_first_failure_order() -> None:
    with pytest.raises(
        ValueError, match="CONTINUE_LOOP resolution carries no user_input"
    ):
        CommandDispatchResolution(
            CommandDispatchResolutionKind.CONTINUE_LOOP,
            user_input="prompt",
            resource_provider_text="resource",
            selected_provider_content=ProductContent("queued"),
        )


@pytest.mark.parametrize(
    "kind",
    [
        CodingCommandOutcomeKind.EXIT,
        CodingCommandOutcomeKind.UNHANDLED,
    ],
)
@pytest.mark.parametrize(
    ("field_name", "invalid_value", "message"),
    [
        ("action", CodingCommandAction.SHOW_HOTKEYS, "only CONTINUE"),
        ("footer_policy", CodingCommandFooterPolicy.STANDARD, "only CONTINUE"),
        ("argument", ProductContent("session"), "only CONTINUE"),
    ],
)
def test_validator_rejects_corrupted_non_continuing_outcomes(
    kind: CodingCommandOutcomeKind,
    field_name: str,
    invalid_value: object,
    message: str,
) -> None:
    outcome = CodingCommandOutcome(kind)
    object.__setattr__(outcome, field_name, invalid_value)

    with pytest.raises(ValueError, match=message):
        require_exact_coding_command_outcome(outcome)


def test_validator_rejects_standard_continue_without_standard_footer() -> None:
    outcome = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        footer_policy=CodingCommandFooterPolicy.STANDARD,
    )
    object.__setattr__(outcome, "footer_policy", None)

    with pytest.raises(ValueError, match="require the STANDARD footer"):
        require_exact_coding_command_outcome(outcome)


@pytest.mark.parametrize(
    "action",
    [
        CodingCommandAction.MODEL,
        CodingCommandAction.SCOPED_MODELS,
        CodingCommandAction.LOGIN,
        CodingCommandAction.LOGOUT,
    ],
)
def test_usage_aware_actions_require_the_usage_aware_footer(
    action: CodingCommandAction,
) -> None:
    with pytest.raises(ValueError, match="require the USAGE_AWARE footer"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.STANDARD,
            ProductContent("value"),
        )


@pytest.mark.parametrize(
    "action",
    [
        None,
        CodingCommandAction.SHOW_HOTKEYS,
        CodingCommandAction.SHOW_CHANGELOG,
        CodingCommandAction.COPY_LAST_ANSWER,
        CodingCommandAction.SHOW_SESSION_STATUS,
        CodingCommandAction.COMPACT,
        CodingCommandAction.NEW_SESSION,
        CodingCommandAction.SESSION_NAME,
    ],
)
def test_standard_actions_reject_the_usage_aware_footer(
    action: CodingCommandAction | None,
) -> None:
    argument = (
        ProductContent("session")
        if action is CodingCommandAction.SESSION_NAME
        else None
    )
    with pytest.raises(ValueError, match="require the STANDARD footer"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.USAGE_AWARE,
            argument,
        )


@pytest.mark.parametrize(
    "action",
    [
        None,
        CodingCommandAction.SHOW_HOTKEYS,
        CodingCommandAction.SHOW_CHANGELOG,
        CodingCommandAction.COPY_LAST_ANSWER,
        CodingCommandAction.SHOW_SESSION_STATUS,
        CodingCommandAction.COMPACT,
        CodingCommandAction.NEW_SESSION,
    ],
)
def test_only_argument_actions_may_carry_an_argument(
    action: CodingCommandAction | None,
) -> None:
    with pytest.raises(ValueError, match="only argument actions"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.STANDARD,
            ProductContent("session"),
        )


def test_session_name_action_requires_an_argument_even_for_status_read() -> None:
    with pytest.raises(ValueError, match="require an argument"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            CodingCommandAction.SESSION_NAME,
            CodingCommandFooterPolicy.STANDARD,
        )


def test_session_tree_action_requires_an_exact_product_argument() -> None:
    action = CodingCommandAction["SESSION_TREE"]
    with pytest.raises(ValueError, match="require an argument"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.STANDARD,
        )
    with pytest.raises(ValueError, match="require the STANDARD footer"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.USAGE_AWARE,
            ProductContent("select 1"),
        )

    outcome = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.STANDARD,
        ProductContent("select 1"),
    )
    object.__setattr__(outcome, "argument", _ProductContentSubclass("select 1"))
    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)

    object.__setattr__(outcome, "argument", ProductContent("select 1"))
    object.__setattr__(outcome.argument, "value", cast(str, []))
    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)


def test_session_resume_action_requires_an_exact_product_argument() -> None:
    action = CodingCommandAction["SESSION_RESUME"]
    with pytest.raises(ValueError, match="require an argument"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.STANDARD,
        )
    with pytest.raises(ValueError, match="require the STANDARD footer"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.USAGE_AWARE,
            ProductContent("named"),
        )

    outcome = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.STANDARD,
        ProductContent("named"),
    )
    object.__setattr__(outcome, "argument", _ProductContentSubclass("named"))
    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)

    object.__setattr__(outcome, "argument", ProductContent("named"))
    object.__setattr__(outcome.argument, "value", cast(str, []))
    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)


def test_session_fork_action_requires_an_exact_product_argument() -> None:
    action = CodingCommandAction["SESSION_FORK"]
    with pytest.raises(ValueError, match="require an argument"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.STANDARD,
        )
    with pytest.raises(ValueError, match="require the STANDARD footer"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.USAGE_AWARE,
            ProductContent("1"),
        )

    outcome = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.STANDARD,
        ProductContent("1"),
    )
    object.__setattr__(outcome, "argument", "1")
    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)

    object.__setattr__(outcome, "argument", _ProductContentSubclass("1"))
    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)

    object.__setattr__(outcome, "argument", ProductContent("1"))
    object.__setattr__(outcome.argument, "value", cast(str, []))
    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)


def test_session_export_action_requires_an_exact_product_argument() -> None:
    action = CodingCommandAction["SESSION_EXPORT"]
    with pytest.raises(ValueError, match="require an argument"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.STANDARD,
        )
    with pytest.raises(ValueError, match="require the STANDARD footer"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.USAGE_AWARE,
            ProductContent("session.html"),
        )

    outcome = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.STANDARD,
        ProductContent(""),
    )
    object.__setattr__(outcome, "argument", "session.html")
    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)

    object.__setattr__(outcome, "argument", _ProductContentSubclass("session.html"))
    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)

    object.__setattr__(outcome, "argument", ProductContent("session.html"))
    object.__setattr__(outcome.argument, "value", cast(str, []))
    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)


def test_session_import_action_requires_an_exact_product_argument() -> None:
    action = CodingCommandAction["SESSION_IMPORT"]
    with pytest.raises(ValueError, match="require an argument"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.STANDARD,
        )
    with pytest.raises(ValueError, match="require the STANDARD footer"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.USAGE_AWARE,
            ProductContent("source.jsonl --yes"),
        )

    outcome = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.STANDARD,
        ProductContent(""),
    )
    object.__setattr__(outcome, "argument", "source.jsonl --yes")
    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)

    object.__setattr__(
        outcome,
        "argument",
        _ProductContentSubclass("source.jsonl --yes"),
    )
    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)

    object.__setattr__(outcome, "argument", ProductContent("source.jsonl --yes"))
    object.__setattr__(outcome.argument, "value", cast(str, []))
    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)


def test_session_clone_action_requires_standard_footer_and_no_argument() -> None:
    action = CodingCommandAction["SESSION_CLONE"]
    with pytest.raises(ValueError, match="require the STANDARD footer"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.USAGE_AWARE,
        )
    with pytest.raises(ValueError, match="only argument actions"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.STANDARD,
            ProductContent("anything"),
        )

    outcome = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.STANDARD,
    )
    object.__setattr__(outcome, "argument", "anything")
    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)

    object.__setattr__(outcome, "argument", _ProductContentSubclass("anything"))
    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)

    object.__setattr__(outcome, "argument", ProductContent("anything"))
    object.__setattr__(outcome.argument, "value", cast(str, []))
    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)


def test_trust_action_requires_standard_footer_and_no_argument() -> None:
    action = CodingCommandAction["TRUST_PROJECT"]
    with pytest.raises(ValueError, match="require the STANDARD footer"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.USAGE_AWARE,
        )
    with pytest.raises(ValueError, match="only argument actions"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.STANDARD,
            ProductContent("anything"),
        )


def test_settings_action_requires_standard_footer_and_no_argument() -> None:
    action = CodingCommandAction["SETTINGS"]
    with pytest.raises(ValueError, match="require the STANDARD footer"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.USAGE_AWARE,
        )
    with pytest.raises(ValueError, match="only argument actions"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.STANDARD,
            ProductContent("anything"),
        )


def test_share_action_requires_standard_footer_and_no_argument() -> None:
    action = CodingCommandAction["SESSION_SHARE"]
    with pytest.raises(ValueError, match="require the STANDARD footer"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.USAGE_AWARE,
        )
    with pytest.raises(ValueError, match="only argument actions"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.STANDARD,
            ProductContent("anything"),
        )


def test_reload_action_requires_standard_footer_and_no_argument() -> None:
    action = CodingCommandAction["RELOAD"]
    with pytest.raises(ValueError, match="require the STANDARD footer"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.USAGE_AWARE,
        )
    with pytest.raises(ValueError, match="only argument actions"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.STANDARD,
            ProductContent("anything"),
        )


@pytest.mark.parametrize(
    "action",
    [
        CodingCommandAction.MODEL,
        CodingCommandAction.SCOPED_MODELS,
        CodingCommandAction.LOGIN,
        CodingCommandAction.LOGOUT,
    ],
)
def test_usage_aware_argument_actions_require_an_argument_even_for_status_read(
    action: CodingCommandAction,
) -> None:
    with pytest.raises(ValueError, match="require an argument"):
        CodingCommandOutcome(
            CodingCommandOutcomeKind.CONTINUE,
            action,
            CodingCommandFooterPolicy.USAGE_AWARE,
        )


def test_validator_rejects_deep_mutation_of_usage_aware_argument() -> None:
    outcome = classify_coding_command(ProductContent("/model openai/model"))
    assert outcome.argument is not None
    object.__setattr__(outcome.argument, "value", cast(str, []))

    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)


def test_validator_rejects_mutated_or_subclassed_argument_content() -> None:
    outcome = CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        CodingCommandAction.SESSION_NAME,
        CodingCommandFooterPolicy.STANDARD,
        ProductContent("session"),
    )
    object.__setattr__(outcome, "argument", _ProductContentSubclass("session"))

    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)

    object.__setattr__(outcome, "argument", ProductContent("session"))
    object.__setattr__(outcome.argument, "value", cast(str, []))

    with pytest.raises(TypeError, match="outcome.argument"):
        require_exact_coding_command_outcome(outcome)


@pytest.mark.parametrize(
    "invalid_kind",
    [
        cast(CodingCommandOutcomeKind, "exit"),
        cast(CodingCommandOutcomeKind, []),
    ],
)
def test_constructor_rejects_wrong_or_mutable_kind(invalid_kind: object) -> None:
    with pytest.raises(TypeError, match="outcome.kind"):
        CodingCommandOutcome(cast(CodingCommandOutcomeKind, invalid_kind))


class _ProductContentSubclass(ProductContent):
    pass


def test_classifier_rejects_content_subclasses() -> None:
    with pytest.raises(TypeError, match="exact ProductContent"):
        classify_coding_command(_ProductContentSubclass("/exit"))


def test_classifier_rejects_corrupted_mutable_content() -> None:
    content = ProductContent("/exit")
    object.__setattr__(content, "value", cast(str, []))

    with pytest.raises(TypeError, match="content.value must be an exact string"):
        classify_coding_command(content)


def test_kernel_has_only_its_declared_headless_dependencies() -> None:
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "pipy_harness"
        / "native"
        / "coding"
        / "commands.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert imported_modules == {
        "__future__",
        "dataclasses",
        "enum",
        "pipy_harness.native.agent.content",
    }


def test_superseded_classifier_left_the_outcome_kernel() -> None:
    assert not hasattr(commands_module, "classify_coding_command")
    assert not hasattr(commands_module, "_continue_outcome")


def test_registry_table_enumerates_every_builtin_exactly_once() -> None:
    names = [spec.name for spec in _BUILTIN_COMMANDS]

    assert names == [
        "",
        "/exit",
        "/quit",
        "/hotkeys",
        "/changelog",
        "/copy",
        "/session",
        "/compact",
        "/new",
        "/clone",
        "/settings",
        "/trust",
        "/share",
        "/reload",
        "/tree",
        "/resume",
        "/name",
        "/fork",
        "/export",
        "/import",
        "/model",
        "/scoped-models",
        "/login",
        "/logout",
    ]
    assert len(names) == len(set(names))


def test_registry_binds_every_coding_command_action_exactly_once() -> None:
    bound_actions = [
        spec.action
        for spec in _BUILTIN_COMMANDS
        if spec.kind is BuiltinCommandKind.ACTION
    ]

    assert None not in bound_actions
    assert set(bound_actions) == set(CodingCommandAction)
    assert len(bound_actions) == len(set(bound_actions))


def test_registry_has_exactly_two_exit_specs_and_one_blank_spec() -> None:
    exit_specs = [
        spec for spec in _BUILTIN_COMMANDS if spec.kind is BuiltinCommandKind.EXIT
    ]
    blank_specs = [
        spec for spec in _BUILTIN_COMMANDS if spec.kind is BuiltinCommandKind.BLANK
    ]

    assert {spec.name for spec in exit_specs} == {"/exit", "/quit"}
    assert [spec.name for spec in blank_specs] == [""]
    assert all(
        spec.argument_contract is BuiltinArgumentContract.NONE
        for spec in (*exit_specs, *blank_specs)
    )


def test_registry_usage_aware_contract_only_covers_provider_control_actions() -> None:
    usage_aware = {
        spec.action
        for spec in _BUILTIN_COMMANDS
        if spec.argument_contract is BuiltinArgumentContract.USAGE_AWARE
    }

    assert usage_aware == {
        CodingCommandAction.MODEL,
        CodingCommandAction.SCOPED_MODELS,
        CodingCommandAction.LOGIN,
        CodingCommandAction.LOGOUT,
    }


def test_registry_specs_are_frozen_and_slotted() -> None:
    spec = _BUILTIN_COMMANDS[0]

    assert not hasattr(spec, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(spec, "name", "/mutated")


def test_registry_spec_validation_rejects_action_spec_without_action() -> None:
    with pytest.raises(ValueError, match="ACTION specs require"):
        BuiltinCommandSpec(
            "/x",
            BuiltinCommandKind.ACTION,
            BuiltinArgumentContract.NONE,
        )


def test_registry_spec_validation_rejects_non_action_spec_with_action() -> None:
    with pytest.raises(ValueError, match="only ACTION specs"):
        BuiltinCommandSpec(
            "/x",
            BuiltinCommandKind.EXIT,
            BuiltinArgumentContract.NONE,
            CodingCommandAction.RELOAD,
        )


@pytest.mark.parametrize("kind", [BuiltinCommandKind.EXIT, BuiltinCommandKind.BLANK])
def test_registry_spec_validation_forces_none_contract_for_exit_and_blank(
    kind: BuiltinCommandKind,
) -> None:
    with pytest.raises(ValueError, match="EXIT and BLANK specs require the NONE"):
        BuiltinCommandSpec("/x", kind, BuiltinArgumentContract.OPTIONAL_ARG)


def test_registry_spec_validation_rejects_non_exact_field_types() -> None:
    with pytest.raises(TypeError, match="spec.name"):
        BuiltinCommandSpec(
            cast(str, []),
            BuiltinCommandKind.EXIT,
            BuiltinArgumentContract.NONE,
        )


def test_registry_spec_validation_rejects_non_str_description() -> None:
    with pytest.raises(TypeError, match="spec.description"):
        BuiltinCommandSpec(
            "/exit",
            BuiltinCommandKind.EXIT,
            BuiltinArgumentContract.NONE,
            description=cast(str, 0),
        )


def test_builtin_command_names_projects_every_advertisable_name() -> None:
    names = builtin_command_names()

    assert "" not in names
    assert names == {
        spec.name
        for spec in _BUILTIN_COMMANDS
        if spec.kind is not BuiltinCommandKind.BLANK
    }


def test_registry_carries_the_exact_advertised_descriptions() -> None:
    assert builtin_command_description("/hotkeys") == "Show keyboard shortcuts"
    assert builtin_command_description("/reload") == (
        "Reload settings, keybindings, and resources"
    )
    assert builtin_command_description("/changelog") == (
        "Show the changelog (What's New)"
    )
    assert builtin_command_description("/compact") == (
        "Compact context, keep a safe summary"
    )
    assert builtin_command_description("/export") == (
        "Export the native session to HTML or active-branch JSONL"
    )
    assert builtin_command_description("/import") == "Import a native session JSONL file"
    assert builtin_command_description("/share") == (
        "Upload the native session as a secret GitHub gist"
    )
    assert builtin_command_description("/settings") == "Settings and status"
    assert builtin_command_description("/trust") == (
        "Save project trust for the next restart"
    )
    assert builtin_command_description("/copy") == (
        "Copy the last answer to the clipboard (local)"
    )
    assert builtin_command_description("/login") == "Log in (openai-codex OAuth)"
    assert builtin_command_description("/logout") == "Log out (openai-codex OAuth)"
    assert builtin_command_description("/model") == "Select provider/model"
    assert builtin_command_description("/scoped-models") == (
        "View/set the Ctrl+P model cycle set"
    )
    assert builtin_command_description("/exit") == "Exit the REPL"
    assert builtin_command_description("/quit") == "Exit the REPL (alias)"


def test_builtin_command_description_rejects_unknown_name() -> None:
    with pytest.raises(KeyError):
        builtin_command_description("/nonexistent")


def test_project_command_completions_preserves_order_and_membership() -> None:
    names = ("/model", "/settings", "/exit")

    assert project_command_completions(names) == names


def test_project_command_completions_allows_declared_adjuncts() -> None:
    names = ("/model", "/skill", "/exit")

    assert (
        project_command_completions(names, adjunct_names=frozenset({"/skill"})) == names
    )


def test_project_command_completions_rejects_unknown_names() -> None:
    with pytest.raises(ValueError, match="not registry built-ins or adjuncts"):
        project_command_completions(("/model", "/skill"))


def test_project_command_descriptions_reads_from_the_registry() -> None:
    descriptions = project_command_descriptions(("/model", "/exit"))

    assert descriptions == {
        "/model": "Select provider/model",
        "/exit": "Exit the REPL",
    }
    assert list(descriptions) == ["/model", "/exit"]


def test_project_command_descriptions_supplies_adjunct_text() -> None:
    descriptions = project_command_descriptions(
        ("/model", "/skill"),
        adjunct_descriptions={"/skill": "List or load a workspace/global skill"},
    )

    assert descriptions == {
        "/model": "Select provider/model",
        "/skill": "List or load a workspace/global skill",
    }


def test_project_command_descriptions_rejects_builtins_without_description() -> None:
    with pytest.raises(ValueError, match="no advertised description"):
        project_command_descriptions(("/session",))
