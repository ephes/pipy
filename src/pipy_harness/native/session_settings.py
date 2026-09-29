"""Which model and thinking level a session runs with, and how it records them.

Pi ``createAgentSession`` (``core/sdk.ts:194-263`` and ``:427-437``) decides
this every time a runtime is created -- at startup and at each ``/resume``,
``/fork``, ``/clone``, ``/new`` or import:

- A session that already has messages restores its model from the branch's
  latest ``model_change`` unless the CLI pinned one, falling back (with a
  ``Could not restore model`` message) when that model is unknown or has no
  auth. Its thinking level is the CLI ``--thinking``, else the branch's last
  ``thinking_level_change``, else the settings default or ``medium``. A
  branch with no thinking entry gets one recorded.
- A session with no messages records the model and level it starts with, so
  a later resume can restore them. Pi keeps those entries in memory and first
  writes them with the first message (its session file is created lazily);
  pipy writes its file eagerly, so it appends them just before the first
  message instead (:func:`record_session_start`). The file then reads the same
  as Pi's, and a session nobody used stays empty.

This module holds the decision and that one append. Applying the decision
(building the provider, clamping the level to the model) belongs to the
callers: the CLI at startup, the provider-mutation owner after a transition.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pipy_harness.native.repl_state import NativeModelSelection
from pipy_harness.native.session_tree import (
    MessageEntry,
    ModelChangeEntry,
    NativeSessionTree,
    SessionEntry,
    ThinkingLevelChangeEntry,
)


@dataclass(frozen=True, slots=True)
class SessionSettingsDecision:
    selection: NativeModelSelection
    # Before clamping to ``selection``; the caller clamps (Pi sdk.ts:258-263).
    thinking_level: str
    # Pi ``modelFallbackMessage``: shown at startup only.
    fallback_message: str | None
    # An existing branch without a thinking entry gets one (Pi sdk.ts:427-430).
    record_thinking: bool


def session_model(branch: Sequence[SessionEntry]) -> NativeModelSelection | None:
    """The branch's latest ``model_change`` (Pi ``getBranchSelection``).

    pipy assistant messages do not name the model that answered, so only
    ``model_change`` entries select one.
    """

    selection: NativeModelSelection | None = None
    for entry in branch:
        if isinstance(entry, ModelChangeEntry):
            selection = NativeModelSelection(entry.provider, entry.model_id)
    return selection


def session_thinking_level(branch: Sequence[SessionEntry]) -> str | None:
    level: str | None = None
    for entry in branch:
        if isinstance(entry, ThinkingLevelChangeEntry):
            level = entry.thinking_level
    return level


def resolve_session_settings(
    branch: Sequence[SessionEntry],
    *,
    has_messages: bool,
    cli_selection: NativeModelSelection | None,
    cli_thinking: str | None,
    fallback_selection: NativeModelSelection,
    current_thinking: str,
    default_thinking: str,
    usable: Callable[[NativeModelSelection], bool],
) -> SessionSettingsDecision:
    """Decide the opened session's model and thinking level.

    ``fallback_selection`` is the model pipy would use without a session: the
    startup selection at startup, the live model after a transition.
    ``current_thinking`` is the live level, kept for a session without
    messages; ``default_thinking`` is the settings default or ``medium``.
    """

    if not has_messages:
        return SessionSettingsDecision(
            selection=cli_selection or fallback_selection,
            thinking_level=cli_thinking or current_thinking,
            fallback_message=None,
            record_thinking=False,
        )
    recorded_level = session_thinking_level(branch)
    selection = cli_selection
    fallback_message: str | None = None
    if selection is None:
        restored = session_model(branch)
        if restored is not None and usable(restored):
            selection = restored
        else:
            selection = fallback_selection
            if restored is not None:
                fallback_message = (
                    f"Could not restore model {restored.reference}. "
                    f"Using {fallback_selection.reference}"
                )
    if cli_thinking is not None:
        level = cli_thinking
    elif recorded_level is not None:
        level = recorded_level
    else:
        level = default_thinking
    return SessionSettingsDecision(
        selection=selection,
        thinking_level=level,
        fallback_message=fallback_message,
        record_thinking=recorded_level is None,
    )


def record_session_start(
    tree: NativeSessionTree, selection: NativeModelSelection, thinking_level: str
) -> None:
    """Before a branch's first message, record its model and thinking level.

    Pi appends ``model_change`` and ``thinking_level_change`` when it creates
    a new session (``core/sdk.ts:431-437``); an entry already on the branch
    (say a ``/thinking`` before the first prompt) is kept rather than
    repeated. A branch that already has a message is left alone.
    """

    branch = tree.get_branch()
    if any(isinstance(entry, MessageEntry) for entry in branch):
        return
    if session_model(branch) is None:
        tree.append_model_change(selection.provider_name, selection.model_id)
    if session_thinking_level(branch) is None:
        tree.append_thinking_level_change(thinking_level)
