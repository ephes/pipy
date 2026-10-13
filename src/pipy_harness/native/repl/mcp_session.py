"""The MCP servers of a session across ``/reload``.

An ``McpManager`` serves one loaded configuration and is not started twice.
``McpSession`` is what the session holds instead: it has the manager of the
configuration that is in use, with the objects that belong to it, and
replaces them when a reload is accepted (Pi closes its servers on
``session_shutdown`` and reads ``mcp.json`` again on ``session_start``).
Everything else in the session asks it for the current ones, so nothing
keeps a manager that was closed.

A sign-in made outside the session, by ``pipy mcp login`` that the agent
ran through ``bash`` for example, is picked up when the next turn starts:
``before_turn`` connects the servers again that were waiting for a sign-in
and have other credentials stored now (Pi's ``turn_start`` handler).

A server that was enabled or reconnected in the ``/mcp`` manager may finish
connecting after the manager was closed. ``before_turn`` then activates what
the server's exposure needs (``McpDiscovery.ensure_active``), as the manager
does while it is open.

A reload has two parts. Before the session decides whether the reload is
accepted, the configuration is read again and a manager is built for it that
connects nothing. Once the reload was accepted, the running manager is
closed, the session's MCP tools are unregistered and the new manager starts
like at the start of a session: the servers connect in the background, the
startup report is made again and the next prompt waits for servers with
``direct`` tools again. A reload that is refused keeps the running manager
and drops the other one, which never opened anything.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from typing import Protocol

from pipy_harness.native.agent import AgentCancellationReason
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolInterruptWaiter,
)
from pipy_harness.native.mcp.discovery import McpDiscovery
from pipy_harness.native.mcp.manager import McpManager
from pipy_harness.native.mcp.manager_flow import McpBackgroundActions
from pipy_harness.native.mcp.readiness import McpReadiness
from pipy_harness.native.mcp.servers_section import MCP_SERVERS_SECTION
from pipy_harness.native.repl.mcp_command import mcp_session_commands
from pipy_harness.native.repl.mcp_waits import run_readiness_wait


@dataclass(frozen=True, slots=True)
class McpServers:
    """One loaded MCP configuration and what the session uses with it."""

    manager: McpManager
    discovery: McpDiscovery
    readiness: McpReadiness
    # Whether MCP is on (``--no-mcp``, ``-builtin:mcp``). With it off the
    # manager has no server and there is no ``/mcp`` command.
    enabled: bool
    # The changes the ``/mcp`` manager view started and did not wait for.
    actions: McpBackgroundActions


class RestartableMcpTools(Protocol):
    """What a reload needs of the session's tool capabilities
    (``NativeToolCapabilities``)."""

    def restart_mcp_tools(self) -> None: ...


class _SessionCommands(Mapping[str, str]):
    """The slash commands MCP adds to the session, as they are when read."""

    def __init__(self, session: McpSession) -> None:
        self._session = session

    def _commands(self) -> Mapping[str, str]:
        return mcp_session_commands(enabled=self._session.servers.enabled)

    def __getitem__(self, name: str) -> str:
        return self._commands()[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._commands())

    def __len__(self) -> int:
        return len(self._commands())


class McpSession:
    """The session's MCP servers. Used by the session thread; ``servers`` may
    be read by any thread."""

    def __init__(
        self,
        load: Callable[[], McpServers],
        *,
        tools: RestartableMcpTools,
        notify: Callable[[str, str], None],
    ) -> None:
        """``load`` reads the configuration as it is now and returns a
        manager that was not started. It is called here and by every
        reload."""

        self._load = load
        self._tools = tools
        self._notify = notify
        self._lock = threading.Lock()
        self._servers = load()
        # A view; it follows a reload that turns MCP on or off.
        self.commands: Mapping[str, str] = _SessionCommands(self)

    @property
    def servers(self) -> McpServers:
        """What is in use now. Read it once per operation."""

        with self._lock:
            return self._servers

    def start(self) -> None:
        """Start connecting the servers in the background.

        ``codemode`` or ``tool_search`` is activated from the configuration,
        so it is active before the servers connect and the next prompt
        declares it.
        """

        servers = self.servers
        try:
            servers.manager.start()
            servers.discovery.ensure_active()
        except BaseException:
            # A start that failed has no shutdown bookend.
            servers.manager.close()
            raise

    def close(self) -> None:
        """Close every connection; returns when no transport is open."""

        self.servers.manager.close()

    def deliver_notices(self) -> None:
        """Show what the servers have to tell the user, on this thread."""

        self.servers.manager.deliver_notices(self._notify)

    def before_turn(
        self, waiter: ToolInterruptWaiter | None
    ) -> AgentCancellationReason | None:
        """Connect again the servers somebody signed in to since they began
        to need a sign-in, so that this turn has their tools.

        Runs on the session thread at the start of every turn. First it
        activates ``codemode`` or ``tool_search`` when a change made in the
        ``/mcp`` manager ended since the manager was closed. Then it returns
        at once when no server waits for a sign-in, and after a look at the
        credentials file when nobody signed in. ``waiter`` watches for an
        interruption while the servers connect: Escape or an abort then
        cancels the run, which is what is returned, and the reconnects go on
        in the background. Without a waiter the wait cannot be ended.
        """

        servers = self.servers
        manager = servers.manager
        if servers.actions.take_finished():
            servers.discovery.ensure_active()
        signed_in = manager.take_signed_in()
        if not signed_in:
            return None
        reconnect = partial(manager.reconnect_servers, signed_in)
        if waiter is None:
            reconnected = reconnect()
        else:
            try:
                reconnected, interruption = run_readiness_wait(reconnect, waiter)
            except KeyboardInterrupt:
                # As for a running tool: Ctrl-C while waiting aborts the run.
                return AgentCancellationReason.OPERATOR_ABORT
            if interruption is not ToolExecutionInterruption.SETTLED:
                return AgentCancellationReason.OPERATOR_ABORT
        if reconnected:
            servers.discovery.ensure_active()
        return None

    def prompt_sections(self) -> dict[str, str]:
        """The sections the session adds to a run's prompt: the list of MCP
        servers whose tools are not declared, as the servers are now."""

        section = self.servers.manager.servers_section()
        return {} if section is None else {MCP_SERVERS_SECTION: section}

    @contextmanager
    def reloading(self) -> Iterator[Callable[[], None]]:
        """Read the configuration again around the block that decides a
        reload; the block calls what it is given once the reload is accepted.

        While the block runs the running manager publishes nothing, so the
        tools the reload was prepared with are the tools it is accepted
        with. When the block accepted, the servers are replaced after it:
        this returns when the old connections are closed and the new
        servers have started to connect. When it did not, or raised, nothing
        changes.
        """

        candidate = self._load()
        accepted = False

        def accept() -> None:
            nonlocal accepted
            accepted = True

        with self.servers.manager.publications_held():
            yield accept
        if accepted:
            self._replace(candidate)

    def _replace(self, candidate: McpServers) -> None:
        retired = self.servers.manager
        try:
            retired.close()
        finally:
            # What the closed servers still had to say, the startup report
            # of a session that was reloaded at once for example.
            retired.deliver_notices(self._notify)
        # Nothing publishes now: the retired manager is closed and the next
        # one has not started. The tools that are declared are pending until
        # their servers have connected again.
        self._tools.restart_mcp_tools()
        with self._lock:
            self._servers = candidate
        self.start()
