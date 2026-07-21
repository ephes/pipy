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
    RELOAD = "reload"


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
        ("/reload", CodingCommandAction.RELOAD),
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


class ResourceDispatchKind(StrEnum):
    """Closed classification of a workspace-resource command dispatch.

    The composition root maps a concrete resource dispatch onto exactly one of
    these before the headless controller interprets the precedence: ``LIST`` and
    ``REJECT`` are consumed locally (diagnostic + footer), while ``RUN`` records
    the invocation counter and carries the bounded provider-visible text.
    """

    LIST = "list"
    REJECT = "reject"
    RUN = "run"


@dataclass(frozen=True, slots=True)
class ResourceDispatchResolution:
    """Narrow, headless projection of a resolved workspace-resource command."""

    kind: ResourceDispatchKind
    message: str
    provider_text: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not ResourceDispatchKind:
            raise TypeError("kind must be an exact ResourceDispatchKind")
        if type(self.message) is not str:
            raise TypeError("message must be an exact str")
        if self.provider_text is not None and type(self.provider_text) is not str:
            raise TypeError("provider_text must be an exact str or None")


@dataclass(frozen=True, slots=True)
class ExtensionDispatchResolution:
    """Narrow, headless projection of a dispatched extension ``/command``."""

    name: str
    ran: bool
    error: str | None = None

    def __post_init__(self) -> None:
        if type(self.name) is not str:
            raise TypeError("name must be an exact str")
        if type(self.ran) is not bool:
            raise TypeError("ran must be an exact bool")
        if self.error is not None and type(self.error) is not str:
            raise TypeError("error must be an exact str or None")


class CommandDispatchResolutionKind(StrEnum):
    """Closed outcome of the built-in>resource>extension command precedence."""

    CONTINUE_LOOP = "continue_loop"
    PROCEED_TO_RUN = "proceed_to_run"
    EXIT_LOOP = "exit_loop"


@dataclass(frozen=True, slots=True)
class CommandDispatchResolution:
    """Result of resolving one command-dispatch step.

    ``EXIT_LOOP`` means a built-in ``/exit``/``/quit`` classified: the outer loop
    must break. A continuing built-in is no longer carried back as data: the
    controller invokes the composition-root :meth:`interpret_builtin` effect port
    directly and returns ``CONTINUE_LOOP``, so built-in interpretation runs
    through the same port as resource/extension dispatch. ``CONTINUE_LOOP`` means
    the input was consumed locally (a continuing built-in interpreted through the
    port, a resource list/reject, an extension command, or an unhandled ``/…``
    line) and the outer loop must continue without a provider turn.
    ``PROCEED_TO_RUN`` carries the exact values the run transition needs: the
    literal ``user_input``, the optional ``resource_provider_text`` from a
    resource run, and the optional ``selected_provider_content`` for
    queued/provider-visible content.
    """

    kind: CommandDispatchResolutionKind
    user_input: str = ""
    resource_provider_text: str | None = None
    selected_provider_content: ProductContent | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not CommandDispatchResolutionKind:
            raise TypeError("kind must be an exact CommandDispatchResolutionKind")
        if type(self.user_input) is not str:
            raise TypeError("user_input must be an exact str")
        if self.resource_provider_text is not None and (
            type(self.resource_provider_text) is not str
        ):
            raise TypeError("resource_provider_text must be an exact str or None")
        if self.selected_provider_content is not None and (
            type(self.selected_provider_content) is not ProductContent
        ):
            raise TypeError(
                "selected_provider_content must be an exact ProductContent or None"
            )
        if self.kind is CommandDispatchResolutionKind.CONTINUE_LOOP:
            if self.user_input != "":
                raise ValueError("a CONTINUE_LOOP resolution carries no user_input")
            if self.resource_provider_text is not None:
                raise ValueError(
                    "a CONTINUE_LOOP resolution carries no resource_provider_text"
                )
            if self.selected_provider_content is not None:
                raise ValueError(
                    "a CONTINUE_LOOP resolution carries no provider content"
                )
        if self.kind is CommandDispatchResolutionKind.EXIT_LOOP:
            if self.user_input != "":
                raise ValueError("an EXIT_LOOP resolution carries no user_input")
            if self.resource_provider_text is not None:
                raise ValueError(
                    "an EXIT_LOOP resolution carries no resource_provider_text"
                )
            if self.selected_provider_content is not None:
                raise ValueError("an EXIT_LOOP resolution carries no provider content")

    @classmethod
    def continue_loop(cls) -> CommandDispatchResolution:
        return cls(CommandDispatchResolutionKind.CONTINUE_LOOP)

    @classmethod
    def exit_loop(cls) -> CommandDispatchResolution:
        return cls(CommandDispatchResolutionKind.EXIT_LOOP)

    @classmethod
    def proceed_to_run(
        cls,
        *,
        user_input: str,
        resource_provider_text: str | None,
        selected_provider_content: ProductContent | None,
    ) -> CommandDispatchResolution:
        return cls(
            CommandDispatchResolutionKind.PROCEED_TO_RUN,
            user_input,
            resource_provider_text,
            selected_provider_content,
        )


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
