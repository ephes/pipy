"""The mode-aware context handed to extension command/shortcut handlers.

`CommandContext` is the protocol an extension handler receives: the
workspace root, whether interactive UI is available, the `ui` capability,
read-only `conversation` and `session_manager` views
(`pipy_harness.native.extensions.session_views`), parsed flags, and the
capability-gated verbs (`complete`, `set_model`, `append_entry`, ...).
`_CommandContext` is the concrete per-invocation implementation; every
capability it lacks degrades to `ExtensionCapabilityError` instead of a
crash, so handlers behave predictably in deterministic / non-interactive
dispatches.

Contexts are built by `make_extension_context`
(`pipy_harness.native.extension_runtime`) and directly by the hook
dispatchers (`pipy_harness.native.extension_hooks`); the public names are
re-exported from `pipy_harness.extensions`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from pipy_harness.native.extension_types import (
    ExtensionCodingSessionControl,
    ExtensionModelRuntimeControl,
    ExtensionUi,
    is_valid_custom_entry_type,
)
from pipy_harness.native.extension_ui import _CollectingUi
from pipy_harness.native.extensions.custom_payloads import coerce_custom_message
from pipy_harness.native.extensions.session_views import (
    ConversationView,
    SessionManagerView,
    _ConversationView,
    _ReadOnlySessionManagerView,
)


class ExtensionCapabilityError(RuntimeError):
    """A capability a handler asked for is not available in this context.

    Raised by e.g. `ctx.complete(...)` when no completion backend is wired
    (a deterministic / non-interactive dispatch), so a handler degrades
    predictably instead of crashing on a missing attribute.
    """


@runtime_checkable
class CommandContext(Protocol):
    """Context passed to an extension command handler.

    Carries the workspace root, whether interactive UI is available, the `ui`
    capability, and a read-only `conversation` view (the last assistant
    message). It grows (model info, cancellation, system-prompt access) in
    later slices.
    """

    cwd: str
    has_ui: bool
    ui: ExtensionUi
    conversation: ConversationView
    session_manager: SessionManagerView
    sessionManager: SessionManagerView
    flags: Mapping[str, object]

    def is_project_trusted(self) -> bool: ...
    def isProjectTrusted(self) -> bool: ...

    def complete(self, system_prompt: str, user_text: str) -> str:
        """Run one bounded provider completion and return its text.

        Raises `ExtensionCapabilityError` when no completion backend is wired
        (a non-interactive / deterministic dispatch).
        """
        ...

    def set_active_tools(self, tool_names: Sequence[str]) -> bool:
        """Restrict the active model-visible tools for later provider turns."""
        ...

    def set_model(self, reference: str) -> bool:
        """Switch the active model/provider selection by reference."""
        ...

    def set_thinking_level(self, level: str) -> bool:
        """Set the active thinking level for later provider turns."""
        ...

    def append_entry(self, custom_type: str, data: object | None = None) -> object:
        """Append a custom entry to the active product session tree."""
        ...

    def set_session_name(self, name: str | None) -> object: ...
    def setSessionName(self, name: str | None) -> object: ...
    def get_session_name(self) -> str | None: ...
    def getSessionName(self) -> str | None: ...
    def set_label(self, entry_id: str, label: str | None) -> object: ...
    def setLabel(self, entry_id: str, label: str | None) -> object: ...
    def send_message(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> object: ...
    def sendMessage(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> object: ...


class _CommandContext:
    """Concrete `CommandContext` for one command invocation."""

    def __init__(
        self,
        cwd: str,
        ui: _CollectingUi,
        coding_session: "ExtensionCodingSessionControl | None" = None,
        *,
        model_runtime: "ExtensionModelRuntimeControl | None" = None,
        flags: Mapping[str, object] | None = None,
        project_trusted: bool = False,
    ) -> None:
        self.cwd = cwd
        self.has_ui = ui.has_ui
        self.ui: ExtensionUi = ui
        session = coding_session or ExtensionCodingSessionControl()
        self.conversation: ConversationView = _ConversationView(session.messages)
        self.session_manager: SessionManagerView = _ReadOnlySessionManagerView(
            session.session_tree
        )
        self.sessionManager: SessionManagerView = self.session_manager
        self.flags: Mapping[str, object] = dict(flags or {})
        self._project_trusted = bool(project_trusted)
        self._coding_session = session
        self._model_runtime = model_runtime or ExtensionModelRuntimeControl()

    def is_project_trusted(self) -> bool:
        return self._project_trusted

    def isProjectTrusted(self) -> bool:  # noqa: N802 - Pi-shaped alias
        return self.is_project_trusted()

    def complete(self, system_prompt: str, user_text: str) -> str:
        if self._coding_session.complete_fn is None:
            raise ExtensionCapabilityError(
                "completion is not available in this context"
            )
        return self._coding_session.complete_fn(str(system_prompt), str(user_text))

    def set_active_tools(self, tool_names: Sequence[str]) -> bool:
        if self._model_runtime.set_active_tools_fn is None:
            raise ExtensionCapabilityError(
                "active-tool control is not available in this context"
            )
        return self._model_runtime.set_active_tools_fn(
            tuple(str(name) for name in tool_names)
        )

    def set_model(self, reference: str) -> bool:
        if self._model_runtime.set_model_fn is None:
            raise ExtensionCapabilityError(
                "model control is not available in this context"
            )
        return self._model_runtime.set_model_fn(str(reference))

    def set_thinking_level(self, level: str) -> bool:
        if self._model_runtime.set_thinking_level_fn is None:
            raise ExtensionCapabilityError(
                "thinking-level control is not available in this context"
            )
        return self._model_runtime.set_thinking_level_fn(str(level))

    def append_entry(self, custom_type: str, data: object | None = None) -> object:
        if self._coding_session.append_entry_fn is None:
            raise ExtensionCapabilityError(
                "custom session entries are not available in this context"
            )
        name = str(custom_type).strip()
        if not is_valid_custom_entry_type(name):
            raise ValueError("invalid custom entry type")
        return self._coding_session.append_entry_fn(name, data)

    def set_session_name(self, name: str | None) -> object:
        if self._coding_session.set_session_name_fn is None:
            raise ExtensionCapabilityError(
                "session-name mutation is not available in this context"
            )
        return self._coding_session.set_session_name_fn(
            None if name is None else str(name)
        )

    def setSessionName(self, name: str | None) -> object:
        return self.set_session_name(name)

    def get_session_name(self) -> str | None:
        if self._coding_session.get_session_name_fn is None:
            return None
        return self._coding_session.get_session_name_fn()

    def getSessionName(self) -> str | None:
        return self.get_session_name()

    def set_label(self, entry_id: str, label: str | None) -> object:
        if self._coding_session.set_label_fn is None:
            raise ExtensionCapabilityError(
                "session-label mutation is not available in this context"
            )
        return self._coding_session.set_label_fn(
            str(entry_id), None if label is None else str(label)
        )

    def setLabel(self, entry_id: str, label: str | None) -> object:
        return self.set_label(entry_id, label)

    def send_message(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> object:
        if self._coding_session.send_message_fn is None:
            raise ExtensionCapabilityError(
                "custom messages are not available in this context"
            )
        queued = coerce_custom_message(message, options)
        return self._coding_session.send_message_fn(
            queued.custom_type,
            queued.content,
            queued.display,
            queued.options,
            queued.details,
        )

    def sendMessage(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> object:
        return self.send_message(message, options)
