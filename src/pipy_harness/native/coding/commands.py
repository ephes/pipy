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
    MODEL = "model"
    SCOPED_MODELS = "scoped_models"
    LOGIN = "login"
    LOGOUT = "logout"
    NEW_SESSION = "new_session"
    SESSION_TREE = "session_tree"
    SESSION_RESUME = "session_resume"
    SESSION_FORK = "session_fork"
    SESSION_CLONE = "session_clone"
    SESSION_EXPORT = "session_export"
    SESSION_IMPORT = "session_import"
    SESSION_SHARE = "session_share"
    SETTINGS = "settings"
    TRUST_PROJECT = "trust_project"


class CodingCommandFooterPolicy(StrEnum):
    """Closed footer policy applied after a handled continuing command."""

    STANDARD = "standard"
    USAGE_AWARE = "usage_aware"


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
    for command, action in (
        ("/hotkeys", CodingCommandAction.SHOW_HOTKEYS),
        ("/changelog", CodingCommandAction.SHOW_CHANGELOG),
        ("/copy", CodingCommandAction.COPY_LAST_ANSWER),
        ("/session", CodingCommandAction.SHOW_SESSION_STATUS),
        ("/compact", CodingCommandAction.COMPACT),
        ("/new", CodingCommandAction.NEW_SESSION),
        ("/clone", CodingCommandAction.SESSION_CLONE),
        ("/settings", CodingCommandAction.SETTINGS),
        ("/trust", CodingCommandAction.TRUST_PROJECT),
        ("/share", CodingCommandAction.SESSION_SHARE),
    ):
        if value == command:
            return _continue_outcome(action)
    for command, action in (
        ("/tree", CodingCommandAction.SESSION_TREE),
        ("/resume", CodingCommandAction.SESSION_RESUME),
        ("/name", CodingCommandAction.SESSION_NAME),
        ("/fork", CodingCommandAction.SESSION_FORK),
        ("/export", CodingCommandAction.SESSION_EXPORT),
        ("/import", CodingCommandAction.SESSION_IMPORT),
    ):
        if value == command or (
            value == value.strip() and value.startswith(f"{command} ")
        ):
            return _continue_outcome(
                action,
                ProductContent(value[len(command) :].strip()),
            )
    for command, action in (
        ("/model", CodingCommandAction.MODEL),
        ("/scoped-models", CodingCommandAction.SCOPED_MODELS),
        ("/login", CodingCommandAction.LOGIN),
        ("/logout", CodingCommandAction.LOGOUT),
    ):
        if value == command or (
            value == value.strip() and value.startswith(f"{command} ")
        ):
            return _continue_outcome(
                action,
                ProductContent(value[len(command) :].strip()),
                footer_policy=CodingCommandFooterPolicy.USAGE_AWARE,
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
        usage_aware_actions = {
            CodingCommandAction.MODEL,
            CodingCommandAction.SCOPED_MODELS,
            CodingCommandAction.LOGIN,
            CodingCommandAction.LOGOUT,
        }
        expected_footer_policy = (
            CodingCommandFooterPolicy.USAGE_AWARE
            if outcome.action in usage_aware_actions
            else CodingCommandFooterPolicy.STANDARD
        )
        if outcome.footer_policy is not expected_footer_policy:
            raise ValueError(
                f"{outcome.action or 'actionless'} CONTINUE outcomes require the "
                f"{expected_footer_policy.name} footer policy"
            )
        argument_actions = usage_aware_actions | {
            CodingCommandAction.SESSION_NAME,
            CodingCommandAction.SESSION_RESUME,
            CodingCommandAction.SESSION_TREE,
            CodingCommandAction.SESSION_FORK,
            CodingCommandAction.SESSION_EXPORT,
            CodingCommandAction.SESSION_IMPORT,
        }
        if outcome.action in argument_actions:
            if outcome.argument is None:
                raise ValueError(f"{outcome.action} outcomes require an argument")
        elif outcome.argument is not None:
            raise ValueError("only argument actions may carry an argument")
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
    *,
    footer_policy: CodingCommandFooterPolicy = CodingCommandFooterPolicy.STANDARD,
) -> CodingCommandOutcome:
    return CodingCommandOutcome(
        CodingCommandOutcomeKind.CONTINUE,
        action,
        footer_policy,
        argument,
    )


def _require_exact_product_content(content: object) -> None:
    if type(content) is not ProductContent:
        raise TypeError("content must be an exact ProductContent")
    if type(content.value) is not str:
        raise TypeError("content.value must be an exact string")
