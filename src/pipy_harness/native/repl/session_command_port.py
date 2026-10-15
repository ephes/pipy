"""What an RPC client's commands need of the session's slash commands.

Pi's ``AgentSession`` checks queued input for extension commands
(``_throwIfExtensionCommand``), runs an extension command a ``prompt`` names
at once also during a run (``prompt``), runs the ``input`` hooks and then
expands skills and templates when it queues a message (``_queueUserInput``), and RPC ``get_commands`` lists the
extension commands, prompt templates and skills (``modes/rpc/rpc-mode.ts``).
The RPC server's command thread asks this port for those; everything it
reads is published for other threads (the extension generation, the
workspace resources, the MCP command view).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pipy_harness.native.coding.commands import ExtensionDispatchResolution
from pipy_harness.native.extensions.contracts import RegisteredCommand
from pipy_harness.native.resources import WorkspaceResources, dispatch_resource_command


class SessionCommandPort:
    """The session's slash commands as an RPC client meets them."""

    __slots__ = (
        "_extension_commands",
        "_has_input_hooks",
        "_input_hooks",
        "_report_error",
        "_resources",
        "_run_command",
        "_session_commands",
    )

    def __init__(
        self,
        *,
        extension_commands: Callable[[], Mapping[str, RegisteredCommand]],
        session_commands: Mapping[str, str],  # "/mcp" -> description
        resources: Callable[[], WorkspaceResources],
        run_command: Callable[[str], ExtensionDispatchResolution | None],
        report_error: Callable[[str, str, str], None],
        input_hooks: Callable[[str], str | None] = lambda text: text,
        has_input_hooks: Callable[[], bool] = lambda: False,
    ) -> None:
        self._extension_commands = extension_commands
        self._session_commands = session_commands
        self._resources = resources
        self._run_command = run_command
        self._report_error = report_error
        self._input_hooks = input_hooks
        self._has_input_hooks = has_input_hooks

    def has_input_hooks(self) -> bool:
        """Whether a loaded extension handles ``input``."""

        return self._has_input_hooks()

    def run_input_hooks(self, text: str) -> str | None:
        """The ``input`` hooks' text for ``text``, or ``None`` when one of
        them handled it (Pi ``_runInputHandlers``)."""

        return self._input_hooks(text)

    def command_name(self, text: str) -> str | None:
        """The name of the extension command ``text`` names (``/mcp`` is
        one while MCP is on), or ``None``."""

        if not text.startswith("/"):
            return None
        name = text[1:].partition(" ")[0]
        if name in self._extension_commands() or f"/{name}" in self._session_commands:
            return name
        return None

    def run(self, text: str) -> None:
        """Run the extension command ``text`` names; a failure is Pi's
        extension error (``command:<name>``)."""

        resolution = self._run_command(text)
        if resolution is None or resolution.ran:
            return
        self._report_error(
            f"command:{resolution.name}",
            "command",
            resolution.message
            if resolution.message is not None
            else resolution.error or "",
        )

    def expand(self, text: str) -> str:
        """``text`` with a skill command or prompt template expanded, as
        Pi expands a message it queues; otherwise ``text``."""

        if not text.startswith("/"):
            # Not trimmed: ` /review` is text, as in a prompt.
            return text
        dispatch = dispatch_resource_command(text, self._resources(), prompt=True)
        if dispatch is None or not dispatch.is_run or dispatch.provider_text is None:
            return text
        return dispatch.provider_text

    def commands(self) -> list[dict[str, Any]]:
        """Pi's ``get_commands``: extension commands, prompt templates (and
        pipy's custom commands, which expand the same way), skills."""

        listed: list[dict[str, Any]] = []
        extension_commands = self._extension_commands()
        for name, command in extension_commands.items():
            listed.append(
                _entry(name, command.description, "extension", command.extension)
            )
        for slash_name, description in self._session_commands.items():
            name = slash_name.removeprefix("/")
            if name not in extension_commands:
                listed.append(_entry(name, description, "extension", None))
        resources = self._resources()
        for template in resources.templates:
            listed.append(
                _entry(
                    template.name, template.description, "prompt", template.path_label
                )
            )
        for custom in resources.commands:
            listed.append(
                _entry(custom.name, custom.description, "prompt", custom.path_label)
            )
        for skill in resources.skills:
            listed.append(
                _entry(
                    f"skill:{skill.name}",
                    skill.description,
                    "skill",
                    skill.path_label,
                )
            )
        return listed


def _entry(
    name: str, description: str | None, source: str, path: str | None
) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name}
    if description:
        entry["description"] = description
    entry["source"] = source
    if path:
        entry["sourceInfo"] = {"path": path}
    return entry


def bind_session_command_port(observer: object, port: SessionCommandPort) -> None:
    """Hand the port to the automation transport behind ``observer`` when it
    takes one (RPC mode), as the transport's UI is found through it."""

    bind = getattr(observer, "bind_session_commands", None)
    if callable(bind):
        bind(port)


__all__ = ["SessionCommandPort", "bind_session_command_port"]
