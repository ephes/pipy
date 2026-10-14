"""The MCP servers of a session across ``/reload`` and session switches.

An ``McpManager`` serves one loaded configuration and is not started twice.
``McpSession`` is what the session holds instead: it has the manager of the
configuration that is in use, with the objects that belong to it, and
replaces them when a reload is accepted and when another session replaces
this one in the process, by ``/new``, ``/resume``, ``/fork``, ``/clone`` or
an import (Pi closes its servers on ``session_shutdown`` and reads
``mcp.json`` again on ``session_start``). Everything else in the session
asks it for the current ones, so nothing keeps a manager that was closed.

A sign-in made outside the session, by ``pipy mcp login`` that the agent
ran through ``bash`` for example, is picked up when the next turn starts:
``before_turn`` connects the servers again that were waiting for a sign-in
and have other credentials stored now (Pi's ``turn_start`` handler).

A server that was enabled or reconnected in the ``/mcp`` manager may finish
connecting after the manager was closed. ``before_turn`` then activates what
the server's exposure needs (``McpDiscovery.ensure_active``), as the manager
does while it is open.

Servers registered by extensions. The extensions that are loaded register
MCP servers in one registry (``native/mcp/registry.py``); a reload that is
accepted brings other extensions and with them another registry.
``follow_extensions`` says where the registry of the loaded extensions is
found. ``start`` takes it, lets the manager read it and listens for its
changes; a later ``start``, after a reload, closes the registry it followed
before, so that an extension of the replaced generation is refused. A change
is applied by the manager at once, on a thread of its own, whatever thread
the extension called from. What has to happen on the session thread, which
is activating ``codemode`` or ``tool_search`` for the servers as they are
then, is done where the session shows the notices and when a turn starts.
With MCP turned off no registered server connects.

The ``mcp_servers_change`` event. Every change of the registrations is an
event for the extensions' hooks, with the registrations after that change
(Pi emits it from the registry's listener). Hooks run on the session
thread, so the registry keeps what each change left, in the order of the
changes, and the session thread takes it from the registry it follows and
fires the events where it does its part of a change. That includes the
changes ``session_start`` handlers made before ``start`` followed the
registry, and it leaves out whatever a replaced registry still had.
``announce_through`` says what fires the event.

Nothing connects a registered server when MCP is turned off and no
extension handles that event. Each such registration is then reported once
as an error of its extension (Pi ``reportUnhandledMcpServers``): after
``session_start`` and after every change, also a registration that was
taken back before the session thread got to it.

An extension that replaces the built-in MCP support. An extension that
registers the ``/mcp`` command takes MCP over for the session (Pi leaves its
built-in MCP extension out when another extension has one of its names).
The servers are then loaded as with MCP turned off: ``mcp.json`` is not
read, nothing connects, and the session has no ``/mcp`` command of its own,
so the name reaches the extension. ``start`` warns that this happened, at
the start of the session and after every reload that keeps it so. Which
extension has the command is known when ``start`` runs, so a reload that
adds or removes such an extension loads the servers once more there, for
the extensions as they are then.

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
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from typing import Protocol

from pipy_harness.native.agent import AgentCancellationReason
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolInterruptWaiter,
)
from pipy_harness.native.mcp.commands import MCP_COMMAND_NAME
from pipy_harness.native.mcp.discovery import McpDiscovery
from pipy_harness.native.mcp.manager import McpManager, RegisteredServers
from pipy_harness.native.mcp.manager_flow import McpBackgroundActions
from pipy_harness.native.mcp.readiness import McpReadiness
from pipy_harness.native.mcp.registry import McpServerRegistry, RegisteredMcpServer
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
    # The extension whose ``/mcp`` command replaced MCP, when that is why
    # it is off; ``None`` also when it would be off anyway.
    replaced_by: str | None = None
    # What opens the authorization URL of a sign-in when the embedder chose
    # it (``McpOptions.open_url``); ``None`` leaves it to the session.
    open_url: Callable[[str], None] | None = None


# Loads the servers: with what returns the registrations of the extensions,
# and with the extension that replaced MCP, if one did.
McpServersLoader = Callable[[RegisteredServers, str | None], McpServers]


class _OwnedCommand(Protocol):
    @property
    def extension(self) -> str: ...


class LoadedExtensions(Protocol):
    """What ``McpSession`` reads of the extensions that are loaded now
    (``_ExtensionRuntime``)."""

    @property
    def mcp_servers(self) -> McpServerRegistry: ...

    @property
    def commands(self) -> Mapping[str, _OwnedCommand]: ...


def mcp_command_owner(commands: Mapping[str, _OwnedCommand]) -> str | None:
    """The extension that registered the ``/mcp`` command, if one did."""

    command = commands.get(MCP_COMMAND_NAME)
    return None if command is None else command.extension


def replaced_mcp_warning(extension: str) -> str:
    """Pi's warning for a built-in extension another extension replaced."""

    return (
        f"Extension {extension} registers command `/{MCP_COMMAND_NAME}`, so "
        "built-in extension `mcp` was not loaded. To use `mcp`, run "
        "`pipy config list` and make sure it is enabled under built-in, then "
        "disable or remove the existing extension. We recommend only having "
        "one or the other loaded at a time."
    )


class RestartableMcpTools(Protocol):
    """What a reload needs of the session's tool capabilities
    (``NativeToolCapabilities``)."""

    def restart_mcp_tools(self, *, keep_declared: bool = True) -> None: ...


class McpServersChangeEvents(Protocol):
    """What tells the loaded extensions about a change of the registered
    servers (``_ExtensionLifecycleAgentEventAdapter``)."""

    def mcp_servers_change_handled(self) -> bool: ...

    def fire_mcp_servers_change(
        self, servers: Sequence[RegisteredMcpServer]
    ) -> None: ...


def unhandled_mcp_server_error(server: RegisteredMcpServer) -> str:
    """The error of an extension whose registered server nothing connects."""

    return (
        f'Extension "{server.extension_path}" error: MCP server "{server.name}" is '
        "registered, but no loaded extension connects MCP servers; another "
        "extension may have replaced the built-in MCP support"
    )


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
        load: McpServersLoader,
        *,
        tools: RestartableMcpTools,
        notify: Callable[[str, str], None],
        extensions: LoadedExtensions | None = None,
    ) -> None:
        """``load`` reads the configuration as it is now and returns a
        manager that was not started. It is called here and by every
        reload, with the function that returns what the extensions have
        registered, for the manager, and with the extension that replaced
        MCP. ``extensions`` are those loaded at the start of the session;
        ``follow_extensions`` says where they are found afterwards."""

        self._load = load
        self._tools = tools
        self._notify = notify
        self._lock = threading.Lock()
        # Where the registry of the loaded extensions is found, and the one
        # that is followed since the last start. Guarded by the lock.
        self._extensions: Callable[[], LoadedExtensions] | None = None
        self._registry: McpServerRegistry | None = None
        self._events: McpServersChangeEvents | None = None
        # The servers of the followed registry that were reported because
        # nothing connects them. Used by the session thread.
        self._reported_unhandled: set[str] = set()
        # The extension the servers in use were loaded for (``replaced_by``
        # of ``load``). Used by the session thread.
        self._loaded_for = (
            None if extensions is None else mcp_command_owner(extensions.commands)
        )
        self._servers = load(self._registered, self._loaded_for)
        # A view; it follows a reload that turns MCP on or off.
        self.commands: Mapping[str, str] = _SessionCommands(self)

    @property
    def servers(self) -> McpServers:
        """What is in use now. Read it once per operation."""

        with self._lock:
            return self._servers

    def follow_extensions(self, extensions: Callable[[], LoadedExtensions]) -> None:
        """``extensions`` returns the extensions that are loaded now: their
        registry of MCP servers and their commands. It is asked by every
        ``start``."""

        with self._lock:
            self._extensions = extensions

    def announce_through(self, events: McpServersChangeEvents) -> None:
        """``events`` fires ``mcp_servers_change`` for the loaded extensions
        and says whether one of them handles it."""

        self._events = events

    def _registered(self) -> Sequence[RegisteredMcpServer]:
        """What the extensions have registered, for the manager."""

        with self._lock:
            registry = self._registry
        return () if registry is None else registry.list()

    def _follow_registry(self) -> None:
        """Follow the registry of the extensions that are loaded now, and
        close the one of the extensions they replaced."""

        with self._lock:
            source = self._extensions
        registry = None if source is None else source().mcp_servers
        with self._lock:
            previous, self._registry = self._registry, registry
        if previous is not registry:
            self._reported_unhandled = set()
            if previous is not None:
                # With it go the changes nobody took: events of the
                # replaced extensions are not for the new ones.
                previous.close()
        if registry is not None:
            registry.set_change_listener(self._on_registrations_changed)

    def _load_for_extensions(self) -> McpServers:
        """The servers for the extensions that are loaded now. They are
        loaded again when another extension, or none, has the ``/mcp``
        command than the servers in use were loaded for; those were not
        started and are dropped."""

        with self._lock:
            source = self._extensions
        owner = (
            self._loaded_for if source is None else mcp_command_owner(source().commands)
        )
        if owner != self._loaded_for:
            servers = self._load(self._registered, owner)
            self._loaded_for = owner
            with self._lock:
                self._servers = servers
        servers = self.servers
        if servers.replaced_by is not None:
            self._notify("warning", replaced_mcp_warning(servers.replaced_by))
        return servers

    def _on_registrations_changed(self) -> None:
        """The registry's listener: any thread, so it only has the manager
        apply the change on a thread of its own. The session thread finds
        the change in the registry."""

        self.servers.manager.registered_changed()

    def _apply_registrations(self, servers: McpServers) -> None:
        """The session thread's part of the changes of the registrations
        since it last looked: with the servers as they are registered now,
        activate what their exposures need; fire one ``mcp_servers_change``
        event per change; report the servers nothing connects."""

        with self._lock:
            registry = self._registry
        # Only what changed until now: a hook that registers a server
        # causes another event, which is fired the next time.
        changes = () if registry is None else registry.take_changes()
        if not changes:
            return
        servers.manager.sync_registered()
        servers.discovery.ensure_active()
        events = self._events
        if events is not None:
            for change in changes:
                events.fire_mcp_servers_change(change)
        self._report_unhandled(servers, changes)

    def _report_unhandled(
        self,
        servers: McpServers,
        changes: Sequence[Sequence[RegisteredMcpServer]] = (),
    ) -> None:
        """Report, once each, the registered servers that nothing connects:
        MCP is turned off and no extension handles ``mcp_servers_change``.
        ``changes`` has the registrations after each change that was just
        taken, so that one that was taken back meanwhile is reported too."""

        events = self._events
        if servers.enabled or (
            events is not None and events.mcp_servers_change_handled()
        ):
            return
        for registered in (*changes, self._registered()):
            for server in registered:
                if server.name not in self._reported_unhandled:
                    self._reported_unhandled.add(server.name)
                    self._notify("error", unhandled_mcp_server_error(server))

    def start(self) -> None:
        """Start connecting the servers in the background, also those the
        extensions registered while they loaded and in their
        ``session_start`` handlers.

        ``codemode`` or ``tool_search`` is activated from the configuration,
        so it is active before the servers connect and the next prompt
        declares it.
        """

        servers = self._load_for_extensions()
        self._follow_registry()
        self._report_unhandled(servers)
        try:
            servers.manager.start()
            servers.discovery.ensure_active()
        except BaseException:
            # A start that failed has no shutdown bookend.
            servers.manager.close()
            raise

    def close(self) -> None:
        """Close every connection; returns when no transport is open."""

        with self._lock:
            registry = self._registry
        if registry is not None:
            registry.set_change_listener(None)
        self.servers.manager.close()

    def deliver_notices(self) -> None:
        """Show what the servers have to tell the user, on this thread,
        after this thread's part of a change of the registered servers."""

        servers = self.servers
        self._apply_registrations(servers)
        servers.manager.deliver_notices(self._notify)

    def before_turn(
        self, waiter: ToolInterruptWaiter | None
    ) -> AgentCancellationReason | None:
        """Connect again the servers somebody signed in to since they began
        to need a sign-in, so that this turn has their tools.

        Runs on the session thread at the start of every turn. First it
        does its part of a change of the registered servers, and activates ``codemode`` or ``tool_search`` when a change made in the
        ``/mcp`` manager ended since the manager was closed. Then it returns
        at once when no server waits for a sign-in, and after a look at the
        credentials file when nobody signed in. ``waiter`` watches for an
        interruption while the servers connect: Escape or an abort then
        cancels the run, which is what is returned, and the reconnects go on
        in the background. Without a waiter the wait cannot be ended.
        """

        servers = self.servers
        manager = servers.manager
        self._apply_registrations(servers)
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

        # For the extensions before the reload; ``start`` loads once more
        # when the reload changed which extension has ``/mcp``.
        candidate = self._load(self._registered, self._loaded_for)
        accepted = False

        def accept() -> None:
            nonlocal accepted
            accepted = True

        with self.servers.manager.publications_held():
            yield accept
        if accepted:
            self._replace(candidate, keep_declared=True)

    def replace_session(self) -> None:
        """Another session replaced this one in the process: ``/new``,
        ``/resume``, ``/fork``, ``/clone`` or an import (Pi's
        ``session_shutdown`` and ``session_start`` handlers).

        Like an accepted reload, but the tools ``tool_search`` loaded and
        the other declared MCP tools are not declared again: the new session
        starts from the configured tools, as a new Pi session does. Changes
        made in ``/mcp`` that apply only to the session, to a server an
        extension registered, are gone with the old manager, and a
        registered server nothing connects is reported again. Returns when
        the old connections are closed and the new servers have started to
        connect.
        """

        candidate = self._load(self._registered, self._loaded_for)
        self._reported_unhandled = set()
        self._replace(candidate, keep_declared=False)

    def _replace(self, candidate: McpServers, *, keep_declared: bool) -> None:
        retired = self.servers.manager
        try:
            retired.close()
        finally:
            # What the closed servers still had to say, the startup report
            # of a session that was reloaded at once for example.
            retired.deliver_notices(self._notify)
        # Nothing publishes now: the retired manager is closed and the next
        # one has not started. On a reload the tools that are declared are
        # pending until their servers have connected again.
        self._tools.restart_mcp_tools(keep_declared=keep_declared)
        with self._lock:
            self._servers = candidate
        self.start()
