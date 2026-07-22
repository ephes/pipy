"""Declarative registry that drives built-in coding-command classification.

This module owns the single source of truth for how a stripped product input is
classified against the built-in ``/…`` commands. Every built-in is enumerated
once in the frozen :data:`_BUILTIN_COMMANDS` table, and
:func:`classify_coding_command` iterates that one table to produce the exact
:class:`~pipy_harness.native.coding.commands.CodingCommandOutcome`. The outcome
value objects and their validator stay in ``native.coding.commands`` (the pure
kernel); the registry depends on that kernel, never the reverse.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.coding.commands import (
    CodingCommandAction,
    CodingCommandFooterPolicy,
    CodingCommandOutcome,
    CodingCommandOutcomeKind,
    _require_exact_product_content,
)


class BuiltinCommandKind(StrEnum):
    """What outcome a built-in spec produces once its input matches.

    ``ACTION`` specs carry a bound :class:`CodingCommandAction` and classify to a
    ``CONTINUE`` outcome; ``EXIT`` specs classify to the terminal ``EXIT``
    outcome; ``BLANK`` is the single empty-input spec that classifies to an
    actionless ``CONTINUE``.
    """

    ACTION = "action"
    EXIT = "exit"
    BLANK = "blank"


class BuiltinArgumentContract(StrEnum):
    """How a built-in spec matches input and whether it carries an argument.

    ``NONE`` matches only the exact command literal and carries no argument.
    ``OPTIONAL_ARG`` and ``USAGE_AWARE`` match the bare literal or a
    ``"<command> "``-prefixed, already-outer-stripped line, carrying the stripped
    remainder as the argument; ``USAGE_AWARE`` additionally selects the
    usage-aware footer policy.
    """

    NONE = "none"
    OPTIONAL_ARG = "optional_arg"
    USAGE_AWARE = "usage_aware"


def _always_available() -> bool:
    """Availability predicate for a built-in that is always classifiable.

    Availability gating stays in the interpreter; every built-in is trivially
    classifiable, so the registry's predicate is uniformly true. The predicate is
    a structural seam for a later slice, not enforcement.
    """

    return True


@dataclass(frozen=True, slots=True)
class BuiltinCommandSpec:
    """One enumerated built-in coding command in the declarative registry."""

    name: str
    kind: BuiltinCommandKind
    argument_contract: BuiltinArgumentContract
    action: CodingCommandAction | None = None
    aliases: tuple[str, ...] = ()
    availability: Callable[[], bool] = field(default=_always_available)

    def __post_init__(self) -> None:
        if type(self.name) is not str:
            raise TypeError("spec.name must be an exact str")
        if type(self.kind) is not BuiltinCommandKind:
            raise TypeError("spec.kind must be an exact BuiltinCommandKind")
        if type(self.argument_contract) is not BuiltinArgumentContract:
            raise TypeError(
                "spec.argument_contract must be an exact BuiltinArgumentContract"
            )
        if type(self.aliases) is not tuple or any(
            type(alias) is not str for alias in self.aliases
        ):
            raise TypeError("spec.aliases must be an exact tuple of str")
        if not callable(self.availability):
            raise TypeError("spec.availability must be callable")
        if self.kind is BuiltinCommandKind.ACTION:
            if type(self.action) is not CodingCommandAction:
                raise ValueError("ACTION specs require an exact CodingCommandAction")
        else:
            if self.action is not None:
                raise ValueError("only ACTION specs may bind a CodingCommandAction")
            if self.argument_contract is not BuiltinArgumentContract.NONE:
                raise ValueError(
                    "EXIT and BLANK specs require the NONE argument contract"
                )
        if (
            self.argument_contract is BuiltinArgumentContract.USAGE_AWARE
            and self.kind is not BuiltinCommandKind.ACTION
        ):
            raise ValueError("USAGE_AWARE contracts require an ACTION spec")


_BUILTIN_COMMANDS: tuple[BuiltinCommandSpec, ...] = (
    BuiltinCommandSpec("", BuiltinCommandKind.BLANK, BuiltinArgumentContract.NONE),
    BuiltinCommandSpec("/exit", BuiltinCommandKind.EXIT, BuiltinArgumentContract.NONE),
    BuiltinCommandSpec("/quit", BuiltinCommandKind.EXIT, BuiltinArgumentContract.NONE),
    BuiltinCommandSpec(
        "/hotkeys",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.NONE,
        CodingCommandAction.SHOW_HOTKEYS,
    ),
    BuiltinCommandSpec(
        "/changelog",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.NONE,
        CodingCommandAction.SHOW_CHANGELOG,
    ),
    BuiltinCommandSpec(
        "/copy",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.NONE,
        CodingCommandAction.COPY_LAST_ANSWER,
    ),
    BuiltinCommandSpec(
        "/session",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.NONE,
        CodingCommandAction.SHOW_SESSION_STATUS,
    ),
    BuiltinCommandSpec(
        "/compact",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.NONE,
        CodingCommandAction.COMPACT,
    ),
    BuiltinCommandSpec(
        "/new",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.NONE,
        CodingCommandAction.NEW_SESSION,
    ),
    BuiltinCommandSpec(
        "/clone",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.NONE,
        CodingCommandAction.SESSION_CLONE,
    ),
    BuiltinCommandSpec(
        "/settings",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.NONE,
        CodingCommandAction.SETTINGS,
    ),
    BuiltinCommandSpec(
        "/trust",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.NONE,
        CodingCommandAction.TRUST_PROJECT,
    ),
    BuiltinCommandSpec(
        "/share",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.NONE,
        CodingCommandAction.SESSION_SHARE,
    ),
    BuiltinCommandSpec(
        "/reload",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.NONE,
        CodingCommandAction.RELOAD,
    ),
    BuiltinCommandSpec(
        "/tree",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.OPTIONAL_ARG,
        CodingCommandAction.SESSION_TREE,
    ),
    BuiltinCommandSpec(
        "/resume",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.OPTIONAL_ARG,
        CodingCommandAction.SESSION_RESUME,
    ),
    BuiltinCommandSpec(
        "/name",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.OPTIONAL_ARG,
        CodingCommandAction.SESSION_NAME,
    ),
    BuiltinCommandSpec(
        "/fork",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.OPTIONAL_ARG,
        CodingCommandAction.SESSION_FORK,
    ),
    BuiltinCommandSpec(
        "/export",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.OPTIONAL_ARG,
        CodingCommandAction.SESSION_EXPORT,
    ),
    BuiltinCommandSpec(
        "/import",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.OPTIONAL_ARG,
        CodingCommandAction.SESSION_IMPORT,
    ),
    BuiltinCommandSpec(
        "/model",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.USAGE_AWARE,
        CodingCommandAction.MODEL,
    ),
    BuiltinCommandSpec(
        "/scoped-models",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.USAGE_AWARE,
        CodingCommandAction.SCOPED_MODELS,
    ),
    BuiltinCommandSpec(
        "/login",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.USAGE_AWARE,
        CodingCommandAction.LOGIN,
    ),
    BuiltinCommandSpec(
        "/logout",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.USAGE_AWARE,
        CodingCommandAction.LOGOUT,
    ),
)


def _match_builtin(spec: BuiltinCommandSpec, value: str) -> str | None:
    """Return the stripped argument if ``value`` matches ``spec``, else ``None``.

    ``NONE`` contracts match only an exact literal (empty argument). The
    argument-bearing contracts match the bare literal (empty argument) or an
    already-outer-stripped ``"<name> …"`` line, returning the stripped remainder.
    """

    for name in (spec.name, *spec.aliases):
        if value == name:
            return ""
        if (
            spec.argument_contract is not BuiltinArgumentContract.NONE
            and value == value.strip()
            and value.startswith(f"{name} ")
        ):
            return value[len(name) :].strip()
    return None


def classify_coding_command(content: ProductContent) -> CodingCommandOutcome:
    """Classify one already-stripped product input without performing effects."""

    _require_exact_product_content(content)
    value = content.value
    for spec in _BUILTIN_COMMANDS:
        if not spec.availability():
            continue
        argument = _match_builtin(spec, value)
        if argument is None:
            continue
        if spec.kind is BuiltinCommandKind.EXIT:
            return CodingCommandOutcome(CodingCommandOutcomeKind.EXIT)
        if spec.kind is BuiltinCommandKind.BLANK:
            return _continue_outcome()
        if spec.argument_contract is BuiltinArgumentContract.NONE:
            return _continue_outcome(spec.action)
        footer_policy = (
            CodingCommandFooterPolicy.USAGE_AWARE
            if spec.argument_contract is BuiltinArgumentContract.USAGE_AWARE
            else CodingCommandFooterPolicy.STANDARD
        )
        return _continue_outcome(
            spec.action,
            ProductContent(argument),
            footer_policy=footer_policy,
        )
    return CodingCommandOutcome(CodingCommandOutcomeKind.UNHANDLED)


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
