from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.coding.commands import (
    CodingCommandAction,
    CodingCommandFooterPolicy,
    CodingCommandOutcome,
    CodingCommandOutcomeKind,
    classify_coding_command,
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
    "value",
    [
        " ",
        " /exit",
        "/exit ",
        "/EXIT",
        "/Compact",
        "/compact x",
        "/compact ",
        "/NAME",
        "/name ",
        "/name    ",
        "/name session ",
        "/name session\t",
        "/name\tvalue",
        "/names",
        "/name:value",
        "/unknown",
        "/model gpt",
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


def test_validator_rejects_continue_without_standard_footer() -> None:
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
        None,
        CodingCommandAction.SHOW_HOTKEYS,
        CodingCommandAction.SHOW_CHANGELOG,
        CodingCommandAction.COPY_LAST_ANSWER,
        CodingCommandAction.SHOW_SESSION_STATUS,
        CodingCommandAction.COMPACT,
    ],
)
def test_only_session_name_action_may_carry_an_argument(
    action: CodingCommandAction | None,
) -> None:
    with pytest.raises(ValueError, match="only SESSION_NAME"):
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
