"""Headless outcome policy for built-in coding-session commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pipy_harness.native.agent.content import ProductContent


class CodingCommandOutcomeKind(StrEnum):
    """Closed control-flow outcomes understood by the outer composition."""

    UNHANDLED = "unhandled"
    EXIT = "exit"
    CONTINUE = "continue"


class CodingCommandAction(StrEnum):
    """Closed composition actions for commands handled by this kernel."""

    SHOW_HOTKEYS = "show_hotkeys"
    SHOW_CHANGELOG = "show_changelog"
    COPY_LAST_ANSWER = "copy_last_answer"
    SHOW_SESSION_STATUS = "show_session_status"
    COMPACT = "compact"
    SESSION_NAME = "session_name"


class CodingCommandFooterPolicy(StrEnum):
    """Closed footer policy applied after a handled continuing command."""

    STANDARD = "standard"


@dataclass(frozen=True, slots=True)
class CodingCommandOutcome:
    """One validated headless command classification."""

    kind: CodingCommandOutcomeKind
    action: CodingCommandAction | None = None
    footer_policy: CodingCommandFooterPolicy | None = None
    argument: ProductContent | None = None

    def __post_init__(self) -> None:
        require_exact_coding_command_outcome(self)


def classify_coding_command(content: ProductContent) -> CodingCommandOutcome:
    """Classify one already-stripped product input without performing effects."""

    _require_exact_product_content(content)
    value = content.value
    if value == "":
        return _continue_outcome()
    if value == "/exit":
        return CodingCommandOutcome(CodingCommandOutcomeKind.EXIT)
    if value == "/quit":
        return CodingCommandOutcome(CodingCommandOutcomeKind.EXIT)
    if value == "/hotkeys":
        return _continue_outcome(CodingCommandAction.SHOW_HOTKEYS)
    if value == "/changelog":
        return _continue_outcome(CodingCommandAction.SHOW_CHANGELOG)
    if value == "/copy":
        return _continue_outcome(CodingCommandAction.COPY_LAST_ANSWER)
    if value == "/session":
        return _continue_outcome(CodingCommandAction.SHOW_SESSION_STATUS)
    if value == "/compact":
        return _continue_outcome(CodingCommandAction.COMPACT)
    if value == "/name" or (value == value.strip() and value.startswith("/name ")):
        return _continue_outcome(
            CodingCommandAction.SESSION_NAME,
            ProductContent(value[len("/name") :].strip()),
        )
    return CodingCommandOutcome(CodingCommandOutcomeKind.UNHANDLED)


def require_exact_coding_command_outcome(outcome: object) -> None:
    """Reject non-canonical, corrupted, or internally inconsistent outcomes."""

    if type(outcome) is not CodingCommandOutcome:
        raise TypeError("outcome must be an exact CodingCommandOutcome")
    if type(outcome.kind) is not CodingCommandOutcomeKind:
        raise TypeError("outcome.kind must be an exact CodingCommandOutcomeKind")
    if outcome.action is not None and type(outcome.action) is not CodingCommandAction:
        raise TypeError("outcome.action must be an exact CodingCommandAction or None")
    if (
        outcome.footer_policy is not None
        and type(outcome.footer_policy) is not CodingCommandFooterPolicy
    ):
        raise TypeError(
            "outcome.footer_policy must be an exact CodingCommandFooterPolicy or None"
        )
    if outcome.argument is not None:
        try:
            _require_exact_product_content(outcome.argument)
        except TypeError as exc:
            raise TypeError(
                "outcome.argument must be an exact ProductContent or None"
            ) from exc
    if outcome.kind is CodingCommandOutcomeKind.CONTINUE:
        if outcome.footer_policy is not CodingCommandFooterPolicy.STANDARD:
            raise ValueError("CONTINUE outcomes require the STANDARD footer policy")
        if outcome.action is CodingCommandAction.SESSION_NAME:
            if outcome.argument is None:
                raise ValueError("SESSION_NAME outcomes require an argument")
        elif outcome.argument is not None:
            raise ValueError("only SESSION_NAME outcomes may carry an argument")
        return
    if outcome.action is not None:
        raise ValueError("only CONTINUE outcomes may carry an action")
    if outcome.footer_policy is not None:
        raise ValueError("only CONTINUE outcomes may carry a footer policy")
    if outcome.argument is not None:
        raise ValueError("only CONTINUE outcomes may carry an argument")


def _continue_outcome(
    action: CodingCommandAction | None = None,
    argument: ProductContent | None = None,
) -> CodingCommandOutcome:
    return CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        CodingCommandFooterPolicy.STANDARD,
        argument,
    )


def _require_exact_product_content(content: object) -> None:
    if type(content) is not ProductContent:
        raise TypeError("content must be an exact ProductContent")
    if type(content.value) is not str:
        raise TypeError("content.value must be an exact string")
