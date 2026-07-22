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
    description: str = ""

    def __post_init__(self) -> None:
        if type(self.name) is not str:
            raise TypeError("spec.name must be an exact str")
        if type(self.description) is not str:
            raise TypeError("spec.description must be an exact str")
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
    BuiltinCommandSpec(
        "/exit",
        BuiltinCommandKind.EXIT,
        BuiltinArgumentContract.NONE,
        description="Exit the REPL",
    ),
    BuiltinCommandSpec(
        "/quit",
        BuiltinCommandKind.EXIT,
        BuiltinArgumentContract.NONE,
        description="Exit the REPL (alias)",
    ),
    BuiltinCommandSpec(
        "/hotkeys",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.NONE,
        CodingCommandAction.SHOW_HOTKEYS,
        description="Show keyboard shortcuts",
    ),
    BuiltinCommandSpec(
        "/changelog",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.NONE,
        CodingCommandAction.SHOW_CHANGELOG,
        description="Show the changelog (What's New)",
    ),
    BuiltinCommandSpec(
        "/copy",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.NONE,
        CodingCommandAction.COPY_LAST_ANSWER,
        description="Copy the last answer to the clipboard (local)",
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
        description="Compact context, keep a safe summary",
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
        description="Settings and status",
    ),
    BuiltinCommandSpec(
        "/trust",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.NONE,
        CodingCommandAction.TRUST_PROJECT,
        description="Save project trust for the next restart",
    ),
    BuiltinCommandSpec(
        "/share",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.NONE,
        CodingCommandAction.SESSION_SHARE,
        description="Upload the native session as a secret GitHub gist",
    ),
    BuiltinCommandSpec(
        "/reload",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.NONE,
        CodingCommandAction.RELOAD,
        description="Reload settings, keybindings, and resources",
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
        description="Export the native session to HTML or active-branch JSONL",
    ),
    BuiltinCommandSpec(
        "/import",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.OPTIONAL_ARG,
        CodingCommandAction.SESSION_IMPORT,
        description="Import a native session JSONL file",
    ),
    BuiltinCommandSpec(
        "/model",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.USAGE_AWARE,
        CodingCommandAction.MODEL,
        description="Select provider/model",
    ),
    BuiltinCommandSpec(
        "/scoped-models",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.USAGE_AWARE,
        CodingCommandAction.SCOPED_MODELS,
        description="View/set the Ctrl+P model cycle set",
    ),
    BuiltinCommandSpec(
        "/login",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.USAGE_AWARE,
        CodingCommandAction.LOGIN,
        description="Log in (openai-codex OAuth)",
    ),
    BuiltinCommandSpec(
        "/logout",
        BuiltinCommandKind.ACTION,
        BuiltinArgumentContract.USAGE_AWARE,
        CodingCommandAction.LOGOUT,
        description="Log out (openai-codex OAuth)",
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


def builtin_command_names() -> frozenset[str]:
    """Every advertisable built-in ``/…`` name (spec names + their aliases).

    Excludes the blank-input spec, which has no advertisable name. Consumers use
    this to validate their curated completion/description name lists against the
    single registry source rather than re-listing command strings.
    """

    names: set[str] = set()
    for spec in _BUILTIN_COMMANDS:
        if spec.kind is BuiltinCommandKind.BLANK:
            continue
        names.add(spec.name)
        names.update(spec.aliases)
    return frozenset(names)


def builtin_command_description(name: str) -> str:
    """Return the advertised description for a built-in ``/…`` name.

    ``name`` matches a spec's ``name`` or one of its aliases. Raises
    :class:`KeyError` for an unknown name so a stale projection fails loudly at
    import instead of silently advertising nothing.
    """

    for spec in _BUILTIN_COMMANDS:
        if spec.name == name or name in spec.aliases:
            return spec.description
    raise KeyError(name)


def project_command_completions(
    names: tuple[str, ...],
    *,
    adjunct_names: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Return ``names`` unchanged after validating each against the registry.

    Every entry must be a registry built-in name or an explicitly listed
    ``adjunct_names`` (resource-owned commands such as ``/skill`` and ``/theme``
    that are advertised but are not registry built-ins). The order and membership
    of ``names`` are preserved exactly; this is a curated projection, not a
    derivation of the full built-in set.
    """

    valid = builtin_command_names() | adjunct_names
    unknown = tuple(name for name in names if name not in valid)
    if unknown:
        raise ValueError(
            "advertised command names are not registry built-ins or adjuncts: "
            f"{unknown}"
        )
    return names


def project_command_descriptions(
    names: tuple[str, ...],
    *,
    adjunct_descriptions: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build an ordered description map for ``names`` from the registry.

    Registry built-in descriptions are read from the single registry source; an
    ``adjunct_descriptions`` entry supplies the advertised text for a resource-
    owned adjunct (``/skill``/``/theme``) that is not a registry built-in. Order
    follows ``names``. A registry built-in with no advertised description, or an
    unknown non-adjunct name, is a hard error so a stale projection fails at
    import.
    """

    adjuncts = adjunct_descriptions or {}
    descriptions: dict[str, str] = {}
    for name in names:
        if name in adjuncts:
            descriptions[name] = adjuncts[name]
            continue
        description = builtin_command_description(name)
        if not description:
            raise ValueError(
                f"registry built-in {name!r} has no advertised description"
            )
        descriptions[name] = description
    return descriptions
