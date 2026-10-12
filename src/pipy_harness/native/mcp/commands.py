"""The ``/mcp`` command of a session (Pi ``registerCommand("mcp")``).

``/mcp`` prints one line per configured server. ``/mcp reconnect [server]``
connects a server again. ``/mcp login`` and ``/mcp logout`` choose a server
as Pi does; no server uses OAuth yet, so they end with the message that says
so, and the sign-in itself is not built.

Nothing here touches a user interface. The command reports through
``notify(level, message)``, asks through ``select(title, options)`` and is run
on the session thread. A step that can take long (waiting for the startup
connections, a reconnect) is handed to ``run_step``, which the session
supplies so that Escape or an abort ends the waiting.

Different from Pi: the usage text for an unknown action is printed without
waiting for the startup connections first. Pi's command cannot be
interrupted. Here a wait can be: ``/mcp`` then prints the servers as they
are, a subcommand is not run when the wait for the startup connections was
ended, and a reconnect the user stops waiting for goes on in the background.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pipy_harness.native.mcp.config import (
    DEFAULT_MCP_EXPOSURE,
    McpServerEntry,
    global_mcp_config_path,
)
from pipy_harness.native.mcp.manager import (
    McpManager,
    McpServerStatus,
    describe_state,
)

MCP_COMMAND_NAME: Final = "mcp"
MCP_COMMAND_DESCRIPTION: Final = (
    "Manage MCP servers: sign in, reconnect, enable or disable, and change exposure"
)
MCP_USAGE: Final = (
    "Usage: /mcp, /mcp login [server], /mcp logout [server], /mcp reconnect [server]"
)
NO_OAUTH_SERVER: Final = (
    "No enabled MCP server uses OAuth. "
    "Only HTTP servers without an Authorization header do."
)
NO_SERVER_TO_RECONNECT: Final = "No enabled MCP server to reconnect."
_ACTIONS: Final = ("login", "logout", "reconnect")
_WHITESPACE: Final = re.compile(r"\s+")

Notify = Callable[[str, str], None]
Select = Callable[[str, Sequence[str]], str | None]
# Runs ``step(cancel_event)`` and returns whether it finished. ``False``
# means the user stopped waiting: the cancel event is set, and a step that
# does not look at it goes on in the background.
RunStep = Callable[[Callable[[threading.Event], object]], bool]


def run_step_inline(step: Callable[[threading.Event], object]) -> bool:
    """Run a step on the caller's thread; nothing can interrupt it."""

    step(threading.Event())
    return True


@dataclass(frozen=True, slots=True)
class McpArgumentCompletion:
    """One completion of the command's argument text (Pi ``AutocompleteItem``)."""

    value: str
    label: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class _Pick:
    """Which servers a subcommand can act on (Pi ``pickServer`` options)."""

    eligible: Callable[[McpServerStatus], bool]
    preferred: Callable[[McpServerStatus], bool]
    none: str


def _exposure(entry: McpServerEntry) -> str:
    exposure = entry.config.get("exposure")
    return DEFAULT_MCP_EXPOSURE if exposure is None else str(exposure)


def _uses_oauth(status: McpServerStatus) -> bool:
    """Whether the server signs in with OAuth. None does before HTTP servers
    connect and their connections carry an OAuth URL."""

    return False


def _has_connection(status: McpServerStatus) -> bool:
    return status.connection is not None


def _state(status: McpServerStatus) -> str | None:
    return None if status.connection is None else status.connection.state


_OAUTH_PICK: Final = _Pick(
    eligible=_uses_oauth,
    preferred=lambda status: _state(status) == "needs-auth",
    none=NO_OAUTH_SERVER,
)
_RECONNECT_PICK: Final = _Pick(
    eligible=_has_connection,
    preferred=lambda status: _state(status) in ("failed", "disconnected"),
    none=NO_SERVER_TO_RECONNECT,
)


def format_status(
    servers: Sequence[McpServerStatus],
    config_errors: Sequence[str],
    config_home: Path | str,
) -> str:
    """The text ``/mcp`` prints (Pi ``formatStatus``)."""

    if not servers and not config_errors:
        return (
            "No MCP servers configured. Add them to "
            f"{global_mcp_config_path(config_home)} or .pipy/mcp.json."
        )
    lines = [_status_line(status) for status in servers]
    lines.extend(f"config error: {error}" for error in config_errors)
    return "\n".join(lines)


def _status_line(status: McpServerStatus) -> str:
    name = status.entry.name
    exposure = _exposure(status.entry)
    view = status.connection
    if view is not None and view.state == "needs-auth":
        return f"{name}: needs sign-in, run /mcp login {name} ({exposure})"
    tools = (
        f", {len(view.tools)} tools"
        if view is not None and view.state == "connected"
        else ""
    )
    if not status.enabled:
        state = "disabled"
    elif view is None:
        state = "starting"
    elif view.state == "disconnected":
        state = "disconnected, reconnects on next call"
    else:
        state = view.state
    error = ""
    if view is not None and view.error and view.state != "connected":
        error = "\n    " + "\n    ".join(view.error.split("\n"))
    return f"{name}: {state}{tools} ({exposure}){error}"


def complete_mcp_arguments(
    servers: Sequence[McpServerStatus], prefix: str
) -> tuple[McpArgumentCompletion, ...] | None:
    """Completions for the text after ``/mcp `` (Pi
    ``getArgumentCompletions``): the actions, then the servers an action can
    name, each with its state."""

    action, *rest = _WHITESPACE.split(prefix.lstrip())
    if len(rest) > 1:
        return None
    if not rest:
        return (
            tuple(
                McpArgumentCompletion(value=f"{item} ", label=item)
                for item in _ACTIONS
                if item.startswith(action)
            )
            or None
        )
    if action not in _ACTIONS:
        return None
    eligible = _has_connection if action == "reconnect" else _uses_oauth
    return (
        tuple(
            McpArgumentCompletion(
                value=f"{action} {status.entry.name}",
                label=status.entry.name,
                description=describe_state(status),
            )
            for status in servers
            if eligible(status) and status.entry.name.startswith(rest[0])
        )
        or None
    )


class McpCommand:
    """``/mcp`` for one session."""

    def __init__(
        self,
        manager: McpManager,
        *,
        config_home: Path | str,
        notify: Notify,
        select: Select,
        ensure_discovery: Callable[[], None],
        run_step: RunStep = run_step_inline,
    ) -> None:
        """``ensure_discovery`` is ``McpDiscovery.ensure_active``, called
        after a reconnect. ``select`` returns ``None`` when nothing was
        chosen or nothing can be asked."""

        self._manager = manager
        self._config_home = config_home
        self._notify = notify
        self._select = select
        self._ensure_discovery = ensure_discovery
        self._run_step = run_step

    def complete(self, prefix: str) -> tuple[McpArgumentCompletion, ...] | None:
        return complete_mcp_arguments(self._manager.servers(), prefix)

    def run(self, args: str) -> None:
        """Run ``/mcp <args>`` on the session thread."""

        words = args.split()
        if not words:
            self._wait_for_startup()
            self._notify(
                "info",
                format_status(
                    self._manager.servers(),
                    self._manager.config_errors,
                    self._config_home,
                ),
            )
            return
        action = words[0]
        if len(words) > 2 or action not in _ACTIONS:
            self._notify("warning", MCP_USAGE)
            return
        name = words[1] if len(words) == 2 else None
        if not self._wait_for_startup():
            self._notify(
                "info",
                f"MCP servers are still connecting; /mcp {action} was not run.",
            )
            return
        if action == "reconnect":
            server = self._pick(name, _RECONNECT_PICK)
            if server is not None:
                self._reconnect(server)
            return
        # login, logout: no server is eligible yet, so the pick reports it.
        self._pick(name, _OAUTH_PICK)

    def _wait_for_startup(self) -> bool:
        """Pi ``await pending``; ``False`` when the user stopped waiting.

        The wait's own answer decides: a wait that was cancelled may have
        returned before the runner looked, and then counts as finished.
        """

        manager = self._manager
        settled: list[bool] = []
        self._run_step(
            lambda cancel: settled.append(manager.wait_for_startup(cancel_event=cancel))
        )
        return settled == [True]

    def _pick(self, name: str | None, pick: _Pick) -> str | None:
        """The server a subcommand acts on (Pi ``pickServer``): the named
        one, the only eligible one, the only preferred one, or the one the
        user selects."""

        servers = self._manager.servers()
        if name is not None:
            named = next((s for s in servers if s.entry.name == name), None)
            if named is None:
                self._notify("error", f'No MCP server named "{name}".')
                return None
            if not pick.eligible(named):
                self._notify("error", pick.none)
                return None
            return name
        candidates = [s.entry.name for s in servers if pick.eligible(s)]
        if not candidates:
            self._notify("info", pick.none)
            return None
        if len(candidates) == 1:
            return candidates[0]
        preferred = [
            s.entry.name for s in servers if pick.eligible(s) and pick.preferred(s)
        ]
        if len(preferred) == 1:
            return preferred[0]
        choice = self._select("MCP server", candidates)
        return choice if choice in candidates else None

    def _reconnect(self, name: str) -> None:
        manager = self._manager
        failures: list[str | None] = []
        if not self._run_step(lambda cancel: failures.append(manager.reconnect(name))):
            self._notify(
                "info",
                f'Still reconnecting MCP server "{name}". Run /mcp to see its state.',
            )
            return
        failure = failures[0]
        if failure is not None:
            self._notify("error", failure)
            return
        self._ensure_discovery()
        status = next((s for s in manager.servers() if s.entry.name == name), None)
        state = "disabled" if status is None else describe_state(status)
        self._notify("info", f'Reconnected to MCP server "{name}" ({state}).')
