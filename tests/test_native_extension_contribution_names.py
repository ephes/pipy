"""Unit tests for the contribution-name reservation records and checks.

The activation runtime threads a `_TakenContributionState` snapshot through
each activation pass and rejects an extension whose `_ContributionNames`
collide with a reserved built-in name or an already-committed extension.
These tests pin the value-level protocol in
`pipy_harness.native.extensions.contribution_names` directly; the
end-to-end activation paths that consume it are covered by
`test_native_extension_activation_sealing.py`.
"""

from __future__ import annotations

import pytest

from pipy_harness.native.extension_types import (
    REASON_DUPLICATE_COMMAND,
    REASON_DUPLICATE_ENTRY_RENDERER,
    REASON_DUPLICATE_FLAG,
    REASON_DUPLICATE_MESSAGE_RENDERER,
    REASON_DUPLICATE_PROVIDER,
    REASON_DUPLICATE_SHORTCUT,
    REASON_DUPLICATE_TOOL,
    REASON_RESERVED_COMMAND,
    REASON_RESERVED_TOOL,
)
from pipy_harness.native.extensions.contribution_names import (
    _ContributionNames,
    _normalize_contribution_names,
    _preloaded_collision_reason,
    _prepare_contribution_names_commit,
    _TakenContributionState,
)


def _names(**overrides: tuple[str, ...]) -> _ContributionNames:
    base: dict[str, tuple[str, ...]] = {
        "commands": (),
        "tools": (),
        "providers": (),
        "shortcuts": (),
        "flags": (),
        "message_renderers": (),
        "entry_renderers": (),
    }
    base.update(overrides)
    return _ContributionNames(**base)


def test_contribution_name_normalization_preserves_each_named_category() -> None:
    names = _ContributionNames(
        commands=("command",),
        tools=("tool",),
        providers=("provider",),
        shortcuts=("shortcut",),
        flags=("flag",),
        message_renderers=("message",),
        entry_renderers=("entry",),
    )

    assert _normalize_contribution_names(names) == names


def test_normalization_rejects_a_non_exact_string_name() -> None:
    class _TrickString(str):
        pass

    with pytest.raises(TypeError, match="not an exact string"):
        _normalize_contribution_names(_names(commands=(_TrickString("command"),)))


def test_normalization_rejects_a_duplicate_name_within_a_category() -> None:
    with pytest.raises(ValueError, match="duplicate extension contribution name"):
        _normalize_contribution_names(_names(tools=("tool", "tool")))


def test_reserved_names_collide_before_taken_names() -> None:
    taken = _TakenContributionState(
        commands=frozenset({"command"}), tools=frozenset({"tool"})
    )

    assert (
        _preloaded_collision_reason(
            _names(commands=("command",)),
            reserved=frozenset({"command"}),
            reserved_tools=frozenset(),
            taken=taken,
        )
        == REASON_RESERVED_COMMAND
    )
    assert (
        _preloaded_collision_reason(
            _names(tools=("tool",)),
            reserved=frozenset(),
            reserved_tools=frozenset({"tool"}),
            taken=taken,
        )
        == REASON_RESERVED_TOOL
    )


@pytest.mark.parametrize(
    ("category", "expected_reason"),
    [
        ("commands", REASON_DUPLICATE_COMMAND),
        ("tools", REASON_DUPLICATE_TOOL),
        ("providers", REASON_DUPLICATE_PROVIDER),
        ("shortcuts", REASON_DUPLICATE_SHORTCUT),
        ("flags", REASON_DUPLICATE_FLAG),
        ("message_renderers", REASON_DUPLICATE_MESSAGE_RENDERER),
        ("entry_renderers", REASON_DUPLICATE_ENTRY_RENDERER),
    ],
)
def test_each_category_reports_its_own_duplicate_reason(
    category: str, expected_reason: str
) -> None:
    reason = _preloaded_collision_reason(
        _names(**{category: ("name",)}),
        reserved=frozenset(),
        reserved_tools=frozenset(),
        taken=_TakenContributionState(**{category: frozenset({"name"})}),
    )

    assert reason == expected_reason


def test_non_colliding_names_report_no_reason() -> None:
    reason = _preloaded_collision_reason(
        _names(commands=("command",), providers=("provider",)),
        reserved=frozenset({"other"}),
        reserved_tools=frozenset({"other_tool"}),
        taken=_TakenContributionState(commands=frozenset({"taken"})),
    )

    assert reason is None


def test_prepare_commit_builds_the_successor_without_mutating_taken() -> None:
    taken = _TakenContributionState(
        commands=frozenset({"existing"}), flags=frozenset({"flag"})
    )

    prepared = _prepare_contribution_names_commit(
        _names(commands=("command",), shortcuts=("shortcut",)), taken
    )

    assert prepared.commands == frozenset({"existing", "command"})
    assert prepared.shortcuts == frozenset({"shortcut"})
    assert prepared.flags == frozenset({"flag"})
    assert taken.commands == frozenset({"existing"})
    assert taken.shortcuts == frozenset()
