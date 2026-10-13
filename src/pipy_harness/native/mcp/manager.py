"""The MCP servers of one session.

``McpManager`` owns the servers of the loaded ``mcp.json`` files for the life
of a session: it connects every enabled server in the background, turns each
server's tools into tool ports, publishes them as the session's MCP tools,
and closes every connection when the session ends.

Threads. ``start`` returns at once; each enabled server gets a daemon thread
(``mcp-start``) that asks its connection for a client. A connection announces
changes on whatever thread made them, so its callbacks only put the server on
a queue. One daemon thread (``mcp-manager``) takes servers from that queue,
reads the connection's view and registers what it finds. It is the only
thread that builds tool ports and publishes them, so publications happen one
after another and the last one is built from the newest views.

Locks, in the order they may be taken: the publication lock, the manager
lock, a connection's lock, the capability state lock. The manager lock
guards the server list and is never held while the manager calls a
connection or publishes tools. ``close`` takes the publication lock, so it
must not be called by a thread that holds the capability state lock.

Nothing here calls into a user interface. What the user should see is kept
as notices, which the session thread collects with ``deliver_notices``.

Waiting. ``wait_for_servers`` is what the session's readiness waits are
built on (``readiness.py``). A connection attempt has a ``ready`` event; the
thread that made the attempt hands it to the registering thread, which sets
it once what the attempt ended with was registered and published. A change
announced by a connection never sets it. The server record holds the event
of its latest attempt. A new attempt gets a new event, assigned under the
manager lock, so a wait that reads the events again after waiting also
covers an attempt that was queued in the meantime.

The startup report. When every connection attempt that ``start`` made has
ended and was registered, one notice lists what needs the user: the errors of
the configuration files and the servers that failed or need a sign-in (Pi
``reportProblems``). Without an enabled server ``start`` leaves it at once.

Changes in a session. ``set_enabled``, ``set_exposure`` and ``reconnect``
block their caller until the change is made and may be called from any
thread. A change of a server's configuration is saved to its ``mcp.json``
first and holds the publication lock while the entry is replaced and the
tools are published, so two changes never interleave. Closing a connection
and connecting happen outside every lock. A server that is disabled and
enabled again gets a new connection object, and nothing is opened for it
until the old connection has finished closing. Work for a connection that
is no longer the server's finds a closed connection or reads the current
one, so a late result of an earlier attempt changes nothing (Pi
``isCurrent``).

Reload. A manager is not started twice: ``/reload`` closes this one and
starts another for the configuration it read again. While the reload decides
whether it is accepted it holds this manager's publications
(``publications_held``), so the session's tools do not change under it.

Resources. Once a server that declared the ``resources`` capability is
connected, the three resource tools are published with the session's MCP
tools (``resources.py``). They reach every enabled server with resources whose
exposure is not ``hidden`` and have the widest exposure among those servers.
They are built again whenever that exposure changes, in the same publication
as the server's tools; with no such server left they stay registered as
``hidden`` tools, as in Pi.

OAuth. Every connection is given the manager's credential store, so a
server that signs in with OAuth is sent its stored token. One that needs a
sign-in is in the state ``needs-auth``; the startup report names it.

Not built yet: servers registered by extensions, and the sign-in.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Final, cast

from pipy_harness.native.mcp.config import (
    DEFAULT_MCP_EXPOSURE,
    LoadedMcpConfig,
    McpExposure,
    McpServerConfig,
    McpServerEntry,
    configured_exposures,
    get_mcp_tool_exposure,
    update_mcp_server_config,
)
from pipy_harness.native.mcp.connection import (
    ConnectionView,
    McpServerConnection,
    McpTransportFactory,
    create_default_transport,
)
from pipy_harness.native.mcp.credentials import McpOAuthCredentialStore
from pipy_harness.native.mcp.log import McpServerLog
from pipy_harness.native.mcp.protocol import JsonObject
from pipy_harness.native.mcp.resources import create_mcp_resource_tools
from pipy_harness.native.mcp.servers_section import (
    McpServerListing,
    render_servers_section,
)
from pipy_harness.native.mcp.tools import (
    McpToolNames,
    McpToolPort,
    mcp_tool_namespace,
)
from pipy_harness.native.tools.base import ToolPort

# How long ``close`` waits for the registering thread to end.
_WORKER_JOIN_SECONDS: Final = 5.0
# How often a wait looks at its cancel event and its deadline.
_WAIT_POLL_SECONDS: Final = 0.05

# Replaces the session's MCP tools and returns the names that were left out
# because another tool has them (``NativeToolCapabilities.publish_mcp_tools``).
PublishMcpTools = Callable[[Mapping[str, ToolPort]], Collection[str]]
NoticeSink = Callable[[str, str], None]
# Chooses servers by their configuration entry.
ServerSelector = Callable[[McpServerEntry], bool]
# Saves a change of ``enabled`` or ``exposure`` to the file of the entry: its
# project ``override`` when set, else its ``source``.
UpdateMcpConfig = Callable[[McpServerEntry, Mapping[str, object]], None]
# Takes tools out of the session's active selection unless they are
# ``direct`` (``NativeToolCapabilities.deactivate_indirect_mcp_tools``).
DeactivateTools = Callable[[Collection[str]], None]


def update_config_file(entry: McpServerEntry, patch: Mapping[str, object]) -> None:
    """Save ``patch`` to the ``mcp.json`` that overrides or defines ``entry``."""

    update_mcp_server_config(
        entry.source if entry.override is None else entry.override,
        entry.name,
        enabled=cast("bool | None", patch.get("enabled")),
        exposure=cast("McpExposure | None", patch.get("exposure")),
        override=entry.override is not None,
    )


class McpToolUnavailableError(Exception):
    """A tool cannot be called now; the message is the call's error result."""


@dataclass(frozen=True, slots=True)
class McpNotice:
    """Something the user should be told, for the session thread to show."""

    level: str
    message: str


@dataclass(frozen=True, slots=True)
class McpServerStatus:
    """One configured server at one moment. A disabled server has no connection."""

    entry: McpServerEntry
    enabled: bool
    connection: ConnectionView | None


def _is_enabled(entry: McpServerEntry) -> bool:
    return entry.config.get("enabled") is not False


def _exposure_of(entry: McpServerEntry) -> str:
    exposure = entry.config.get("exposure")
    return DEFAULT_MCP_EXPOSURE if exposure is None else str(exposure)


def _no_server(name: str) -> str:
    return f'No MCP server named "{name}".'


def _error_message(error: BaseException) -> str:
    return str(error) or type(error).__name__


def describe_state(status: McpServerStatus, *, with_error: bool = True) -> str:
    """Short state of a server for lists and the startup report (Pi
    ``describeState``). ``with_error`` appends the first line of a failure.
    A connected server's resources are counted without MCP App resources."""

    if not status.enabled:
        return "disabled"
    view = status.connection
    if view is None:
        return "starting"
    if view.state == "needs-auth":
        return "needs sign-in"
    if view.state == "failed":
        if not with_error:
            return "failed"
        error = "unknown error" if view.error is None else view.error
        return f"failed: {error.split(chr(10), 1)[0]}"
    if view.state == "connected":
        count = len(view.tools)
        resources = len(view.resources)
        resource_count = (
            f" · {resources} resource{'' if resources == 1 else 's'}"
            if resources
            else ""
        )
        return f"connected · {count} tool{'' if count == 1 else 's'}{resource_count}"
    if view.state == "connecting":
        return "connecting…"
    return view.state


def _wait_for_event(
    event: threading.Event,
    deadline: float | None,
    cancel_event: threading.Event | None,
) -> bool:
    """Whether ``event`` was set before the deadline and the cancellation."""

    while not event.is_set():
        if cancel_event is not None and cancel_event.is_set():
            return False
        wait: float | None = None
        if deadline is not None:
            wait = deadline - time.monotonic()
            if wait <= 0:
                return False
        if cancel_event is not None:
            wait = _WAIT_POLL_SECONDS if wait is None else min(wait, _WAIT_POLL_SECONDS)
        event.wait(wait)
    return True


class _Server:
    """A configured server, guarded by the manager lock.

    The ``ready`` event itself is set without the lock; which event is the
    server's current one is read and changed under it. ``entry`` is replaced
    only by a thread that holds the publication lock.
    """

    __slots__ = ("closing", "connection", "entry", "ports", "ready", "source")

    def __init__(self, entry: McpServerEntry) -> None:
        self.entry = entry
        # ``None`` while the server is disabled, and while an enabled server
        # waits for its previous connection to finish closing.
        self.connection: McpServerConnection | None = None
        # The event of the latest connection attempt: set once what that
        # attempt ended with was registered and published. ``None`` until
        # the server is started.
        self.ready: threading.Event | None = None
        # Set when the connection a disable detached has finished closing;
        # ``None`` when nothing is closing.
        self.closing: threading.Event | None = None
        # The registered tools by pipy name, and the tool list and
        # instructions they were built from.
        self.ports: dict[str, McpToolPort] = {}
        self.source: tuple[tuple[JsonObject, ...], str | None] | None = None


class McpManager:
    """The servers of one session. Start once, close once."""

    def __init__(
        self,
        *,
        config: LoadedMcpConfig,
        cwd: Path | str,
        client_version: str,
        publish_tools: PublishMcpTools,
        log: McpServerLog | None = None,
        create_transport: McpTransportFactory = create_default_transport,
        update_config: UpdateMcpConfig = update_config_file,
        deactivate_tools: DeactivateTools | None = None,
        credentials: McpOAuthCredentialStore | None = None,
    ) -> None:
        """``publish_tools`` receives the tools of all servers after every
        change. ``config`` is read once; ``cwd`` is the session directory.
        ``credentials`` holds the OAuth credentials of the servers; without
        it no server is sent a token.
        ``update_config`` saves a change made in the session and
        ``deactivate_tools`` takes a server's tools that are no longer
        ``direct`` out of the session's selection."""

        self.config_errors: tuple[str, ...] = tuple(config.errors)
        # ``autoEnableCodemode``; true unless a file says false.
        self.auto_enable_codemode: bool = config.auto_enable_codemode is not False
        # The trusted project's ``mcp.json``, where project overrides of
        # user-level servers are saved.
        self.project_config: str | None = config.project_config
        self._update_config = update_config
        self._deactivate_tools = deactivate_tools
        # Set by ``close``; ends the waits of changes made in the session.
        self._shutdown = threading.Event()
        # Set once every attempt ``start`` made was registered.
        self._startup_done = threading.Event()
        self._cwd = cwd
        self._client_version = client_version
        self._publish_tools = publish_tools
        self._log = log
        self._create_transport = create_transport
        self._credentials = credentials
        self._publication = threading.Lock()
        self._lock = threading.Lock()
        self._servers = [_Server(entry) for entry in config.servers]
        self._retired: list[McpServerConnection] = []
        self._worker: threading.Thread | None = None
        self._started = False
        self._closed = False
        self._names = McpToolNames()
        # The three resource tools and the MCP exposure they were built
        # with; ``None`` until a server has resources. Guarded by the manager
        # lock and changed only by a thread that holds the publication lock.
        self._resource_exposure: str | None = None
        self._resource_ports: dict[str, ToolPort] = {}
        # A server whose connection changed, and the ``ready`` event of the
        # connection attempt that ended with this change, if one did.
        self._events: queue.SimpleQueue[
            tuple[_Server, threading.Event | None] | None
        ] = queue.SimpleQueue()
        self._notices: deque[McpNotice] = deque()
        # The ``ready`` events of the attempts ``start`` made that have not
        # been registered yet; the startup report waits for them.
        self._startup: set[threading.Event] = set()
        # The ``ready`` events that are not set yet, also those a later
        # attempt replaced on their server; ``close`` sets them all.
        self._unsettled: set[threading.Event] = set()
        self._reported: set[str] = set()

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        """Start connecting every enabled server; does not wait for any."""

        connecting: list[tuple[_Server, McpServerConnection, threading.Event]] = []
        with self._lock:
            if self._started or self._closed:
                return
            self._started = True
            for server in self._servers:
                if _is_enabled(server.entry):
                    server.connection = self._new_connection(server)
                    connecting.append(
                        (server, server.connection, self._new_attempt(server))
                    )
            self._startup = {ready for _, _, ready in connecting}
        if not connecting:
            self._report_problems()
            self._startup_done.set()
        for attempt in connecting:
            threading.Thread(
                target=self._connect,
                args=attempt,
                name="mcp-start",
                daemon=True,
            ).start()

    def close(self) -> None:
        """Close every connection, also one that is still connecting.

        Returns when no transport of any server is open. Afterwards nothing
        is published any more and a call to a tool of these servers fails.
        Every caller waits, also when another thread closed first.
        """

        with self._publication:
            with self._lock:
                self._closed = True
                self._shutdown.set()
                servers, self._servers = self._servers, []
                unsettled, self._unsettled = self._unsettled, set()
                self._retired.extend(
                    server.connection
                    for server in servers
                    if server.connection is not None
                )
                retired = list(self._retired)
                worker = self._worker
        closers = [
            threading.Thread(target=connection.close, name="mcp-close", daemon=True)
            for connection in retired[1:]
        ]
        for closer in closers:
            closer.start()
        if retired:
            retired[0].close()
        for closer in closers:
            closer.join()
        for ready in unsettled:
            ready.set()
        self._startup_done.set()
        if worker is not None:
            self._events.put(None)
            worker.join(_WORKER_JOIN_SECONDS)

    @contextmanager
    def publications_held(self) -> Iterator[None]:
        """Publish nothing while the block runs.

        A change a server announces meanwhile is registered when the block
        ends, and a change made in the session (``set_enabled``,
        ``set_exposure``) waits for it. The block must not call those or
        ``close``, and must not be entered by a thread that holds the
        capability state lock.
        """

        with self._publication:
            yield

    # -- reading ----------------------------------------------------------

    def servers(self) -> tuple[McpServerStatus, ...]:
        """Every configured server, in configuration order."""

        with self._lock:
            servers = [(server.entry, server.connection) for server in self._servers]
        return tuple(
            McpServerStatus(
                entry=entry,
                enabled=_is_enabled(entry),
                connection=None if connection is None else connection.view(),
            )
            for entry, connection in servers
        )

    def wait_until_settled(self, timeout: float | None = None) -> bool:
        """Wait until every server that was started connected or failed and
        its tools were published. Returns whether that happened in time."""

        return self.wait_for_servers(timeout=timeout)

    def wait_for_servers(
        self,
        select: ServerSelector | None = None,
        *,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> bool:
        """Wait for the latest connection attempts of the chosen servers.

        A server is waited for while it is enabled and has an attempt, also
        one that still waits for the server's previous connection to close;
        one that is disabled or removed during the wait no longer counts. The servers
        are chosen once, when the wait begins. An attempt that was queued for
        one of them while waiting is waited for as well (Pi
        ``waitForServers``). Returns ``True`` when every chosen server
        connected or failed and its tools were published, ``False`` when
        ``timeout`` seconds passed or ``cancel_event`` was set first. With
        ``timeout=0`` it only looks. ``close`` ends every wait.
        """

        with self._lock:
            candidates = [(server, server.entry) for server in self._servers]
        chosen = [
            server for server, entry in candidates if select is None or select(entry)
        ]
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                current = {id(server) for server in self._servers}
                pending = [
                    server.ready
                    for server in chosen
                    if id(server) in current
                    and server.ready is not None
                    and _is_enabled(server.entry)
                    and not server.ready.is_set()
                ]
            if not pending:
                return True
            for ready in pending:
                if not _wait_for_event(ready, deadline, cancel_event):
                    return False

    def wait_for_startup(
        self,
        *,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> bool:
        """Wait until every attempt ``start`` made was registered and the
        startup report was posted (Pi ``pending``). Attempts made later do
        not count. Returns at once for a manager that was not started or is
        closed; ``False`` when the time or the cancellation came first."""

        with self._lock:
            if not self._started:
                return True
        deadline = None if timeout is None else time.monotonic() + timeout
        return _wait_for_event(self._startup_done, deadline, cancel_event)

    def configured_exposures(self) -> frozenset[str]:
        """Exposures the tools of the enabled servers can have, known from
        the configuration before any server connects."""

        with self._lock:
            entries = [server.entry for server in self._servers]
        return frozenset(
            exposure
            for entry in entries
            if _is_enabled(entry)
            for exposure in configured_exposures(entry)
        )

    def servers_section(self) -> str | None:
        """The text of the system prompt's ``mcp_servers`` section for the
        servers as they are now, or ``None`` when no server is listed.

        A server is listed from its configuration, so also before it
        connected; its instructions can only be its summary afterwards.
        """

        return render_servers_section(
            McpServerListing(
                status.entry,
                None if status.connection is None else status.connection.instructions,
            )
            for status in self.servers()
        )

    def tool_owner(self, name: str) -> str | None:
        """The server whose tool was registered under ``name``, if any."""

        owner = self._names.owner(name)
        return None if owner is None else owner[0]

    def deliver_notices(self, sink: NoticeSink) -> None:
        """Hand the pending notices to ``sink(level, message)``, on the
        caller's thread. The session thread calls this; nothing else shows
        a notice."""

        while True:
            try:
                notice = self._notices.popleft()
            except IndexError:
                return
            sink(notice.level, notice.message)

    # -- changes in a session ---------------------------------------------

    def reconnect(self, name: str) -> str | None:
        """Connect the server again; returns the error when that failed.

        Waits for the server's running attempt, drops the connected client
        and connects. Returns when the outcome was registered and published.
        The new attempt has its own ``ready`` event, so a call that arrives
        meanwhile waits for it (Pi ``reconnect``).
        """

        with self._lock:
            server = self._find(name)
            if server is None:
                return _no_server(name)
            connection = server.connection
            if connection is None:
                return f'MCP server "{name}" is disabled.'
            previous = server.ready
            ready = self._new_attempt(server)
        if previous is not None:
            self._wait(previous)
        failure: str | None = None
        try:
            connection.reconnect()
        except Exception as error:  # noqa: BLE001 - the message is the result
            failure = _error_message(error)
        self._events.put((server, ready))
        self._wait(ready)
        return failure

    def set_enabled(
        self, name: str, enabled: bool, *, in_project: bool = False
    ) -> str | None:
        """Enable or disable a server; returns the error when the change
        could not be saved (Pi ``setEnabled``).

        Disabling takes the server's tools away at once and returns when its
        connection has closed, also one that was still connecting. Enabling
        waits for a connection that is still closing, connects, and returns
        when the outcome was registered; a failed connect shows in the
        server's state. ``in_project`` saves the change as an override in the
        trusted project's file. Enabling a server that is enabled only saves
        the change.
        """

        detached: tuple[McpServerConnection | None, threading.Event | None]
        detached = (None, None)
        attempt: tuple[threading.Event | None, threading.Event] | None = None
        with self._publication:
            with self._lock:
                server = self._find(name)
                was_enabled = server is not None and _is_enabled(server.entry)
            if server is None:
                return _no_server(name)
            failed = self._save_config(server, {"enabled": enabled}, in_project)
            if failed is not None:
                return failed
            if not enabled:
                detached = self._detach(server)
            elif not was_enabled:
                attempt = self._queue_attempt(server)
        if not enabled:
            self._finish_closing(server, *detached)
        elif attempt is not None:
            self._run_attempt(server, *attempt)
        return None

    def _detach(
        self, server: _Server
    ) -> tuple[McpServerConnection | None, threading.Event | None]:
        """Take a disabled server's connection and tools away (Pi
        ``hideTools``). The publication lock is held. Returns the connection
        the caller has to close, and the event of whatever is closing."""

        with self._lock:
            connection = server.connection
            server.connection = None
            if connection is not None:
                server.closing = threading.Event()
                # ``close`` waits for it too.
                self._retired.append(connection)
            closing = server.closing
            registered = bool(server.ports)
            server.ports = {}
            server.source = None
        self._publish_changes(registered)
        return connection, closing

    def _finish_closing(
        self,
        server: _Server,
        connection: McpServerConnection | None,
        closing: threading.Event | None,
    ) -> None:
        """Close what ``_detach`` returned, or wait for the call that does."""

        if closing is None:
            return
        if connection is None:
            self._wait(closing)
            return
        try:
            connection.close()
        finally:
            with self._lock:
                if server.closing is closing:
                    server.closing = None
                if connection in self._retired:
                    self._retired.remove(connection)
            closing.set()

    def _queue_attempt(
        self, server: _Server
    ) -> tuple[threading.Event | None, threading.Event] | None:
        """Give an enabled server a new attempt. The publication lock is
        held. Returns the event of a connection that is still closing and
        the attempt's ``ready`` event; ``None`` when the manager does not
        run."""

        with self._lock:
            if not self._started or self._closed or server not in self._servers:
                return None
            return server.closing, self._new_attempt(server)

    def _run_attempt(
        self,
        server: _Server,
        closing: threading.Event | None,
        ready: threading.Event,
    ) -> None:
        """Connect an enabled server once its previous connection closed,
        and wait until the outcome was registered."""

        if closing is not None:
            self._wait(closing)
        connection: McpServerConnection | None = None
        with self._lock:
            # Disabled again, enabled again by another call, or closed: this
            # attempt is not the server's any more.
            if (
                not self._closed
                and server in self._servers
                and server.ready is ready
                and server.connection is None
                and _is_enabled(server.entry)
            ):
                connection = server.connection = self._new_connection(server)
        if connection is None:
            self._settle(ready)
            return
        self._connect(server, connection, ready)
        self._wait(ready)

    def set_exposure(self, name: str, exposure: McpExposure) -> str | None:
        """Change a server's exposure; returns the error when the change
        could not be saved (Pi ``setExposure``).

        The server's tools are registered again with the new exposure, and
        those that are not ``direct`` afterwards leave the session's active
        selection. A ``toolExposure`` entry still decides for its tools.
        """

        with self._publication:
            with self._lock:
                server = self._find(name)
            if server is None:
                return _no_server(name)
            failed = self._save_config(server, {"exposure": exposure})
            if failed is not None:
                return failed
            self._register_held(server, force=True)
            with self._lock:
                names = tuple(server.ports)
            if names and self._deactivate_tools is not None:
                self._deactivate_tools(names)
        return None

    def _find(self, name: str) -> _Server | None:
        """The server called ``name``. The manager lock is held."""

        return next(
            (server for server in self._servers if server.entry.name == name), None
        )

    def _save_config(
        self, server: _Server, patch: Mapping[str, object], in_project: bool = False
    ) -> str | None:
        """Save a change and apply it to the server's entry (Pi
        ``saveConfig``). The publication lock is held, so no other change of
        an entry runs. A server an extension registered changes for the
        session only. Returns the error when the file could not be updated;
        the entry is unchanged then."""

        with self._lock:
            entry = server.entry
        override = self.project_config if in_project else entry.override
        if override:
            entry = replace(entry, override=override)
        if entry.scope != "extension":
            try:
                self._update_config(entry, patch)
            except Exception as error:  # noqa: BLE001 - the message is the result
                path = entry.source if entry.override is None else entry.override
                return f"Could not update {path}: {_error_message(error)}"
        config = cast("McpServerConfig", {**entry.config, **patch})
        with self._lock:
            server.entry = replace(entry, config=config)
        return None

    def _wait(self, event: threading.Event) -> None:
        """Wait for ``event``, or for the manager to close."""

        _wait_for_event(event, None, self._shutdown)

    def _new_attempt(self, server: _Server) -> threading.Event:
        """Give ``server`` the ``ready`` event of a new attempt and make sure
        the registering thread runs. The manager lock is held and the
        manager is not closed."""

        ready = server.ready = threading.Event()
        self._unsettled.add(ready)
        if self._worker is None:
            self._worker = threading.Thread(
                target=self._run, name="mcp-manager", daemon=True
            )
            self._worker.start()
        return ready

    def _settle(self, ready: threading.Event) -> None:
        """An attempt ended and what it ended with was registered."""

        with self._lock:
            self._unsettled.discard(ready)
        ready.set()

    # -- connecting and registering ---------------------------------------

    def _new_connection(self, server: _Server) -> McpServerConnection:
        changed = partial(self._changed, server)
        return McpServerConnection(
            entry=server.entry,
            cwd=self._cwd,
            client_version=self._client_version,
            on_tools=changed,
            on_change=changed,
            create_transport=self._create_transport,
            log=self._log,
            credentials=self._credentials,
        )

    def _connect(
        self,
        server: _Server,
        connection: McpServerConnection,
        ready: threading.Event,
    ) -> None:
        """One connection attempt. ``ready`` is its event: it is set when
        what the attempt ended with was registered, by nothing else."""

        try:
            connection.get_client()
        except Exception:  # noqa: BLE001 - a failed connect is the connection's state
            pass
        self._events.put((server, ready))

    def _changed(self, server: _Server, connection: McpServerConnection) -> None:
        """A connection's callback: any thread, so it only leaves a note."""

        self._events.put((server, None))

    def _run(self) -> None:
        while True:
            event = self._events.get()
            if event is None:
                return
            server, ready = event
            try:
                self._register(server)
            except Exception as error:  # noqa: BLE001 - one server's tools must not end the thread
                self._notify(
                    "warning",
                    f'MCP server "{server.entry.name}": '
                    f"its tools could not be registered: {error}",
                )
            finally:
                if ready is not None:
                    try:
                        self._attempt_registered(ready)
                    finally:
                        self._settle(ready)

    def _attempt_registered(self, ready: threading.Event) -> None:
        """Report the startup problems once the last attempt ``start`` made
        was registered. A later attempt of a server has another event."""

        with self._lock:
            if ready not in self._startup:
                return
            self._startup.discard(ready)
            if self._startup:
                return
        try:
            self._report_problems()
        finally:
            self._startup_done.set()

    def _report_problems(self) -> None:
        """One notice for everything that needs the user after startup."""

        with self._lock:
            if self._closed:
                return
            servers = [(server.entry, server.connection) for server in self._servers]
        lines = [f"config: {error}" for error in self.config_errors]
        for entry, connection in servers:
            if connection is None:
                continue
            view = connection.view()
            if view.state in ("needs-auth", "failed"):
                status = McpServerStatus(entry, _is_enabled(entry), view)
                lines.append(f"{entry.name}: {describe_state(status)}")
        if lines:
            listed = "\n".join(f"  {line}" for line in lines)
            self._notify(
                "warning", f"MCP servers need attention:\n{listed}\nRun /mcp to fix."
            )

    def _register(self, server: _Server) -> None:
        """Publish the tools of ``server`` as its connection has them now."""

        with self._publication:
            self._register_held(server, force=False)

    def _register_held(self, server: _Server, *, force: bool) -> None:
        """Build the server's tool ports again when its tool list or its
        instructions changed, or always with ``force`` (its configuration
        changed), and publish. The publication lock is held."""

        with self._lock:
            if self._closed or server not in self._servers:
                return
            connection = server.connection
            entry = server.entry
            source = server.source
            registered = bool(server.ports)
        if connection is None:
            return
        view = connection.view()
        changed = False
        if (
            force
            or source is None
            or source[0] is not view.tools
            or source[1] != view.instructions
        ):
            ports = self._create_ports(entry, connection, view)
            with self._lock:
                server.ports = ports
                server.source = (view.tools, view.instructions)
            changed = bool(ports) or registered
        self._publish_changes(changed)

    def _publish_changes(self, server_tools_changed: bool) -> None:
        """Bring the resource tools up to date and publish, when they or a
        server's tools changed. The publication lock is held."""

        was_direct = self._sync_resource_tools()
        if not server_tools_changed and was_direct is None:
            return
        with self._lock:
            merged = self._merged_ports()
            names = tuple(self._resource_ports)
        self._publish(merged)
        if was_direct and self._deactivate_tools is not None:
            # No longer declared to the model: out of the active selection.
            self._deactivate_tools(names)

    def _sync_resource_tools(self) -> bool | None:
        """Build the resource tools with the widest exposure of the servers
        they reach: ``direct`` when one of them is direct, and so on; hidden
        when no server has resources (Pi ``syncResourceTools``).

        The publication lock is held. Returns ``None`` when nothing changed,
        else whether the tools were ``direct`` before.
        """

        exposures = {_exposure_of(entry) for entry, _ in self._servers_with_resources()}
        exposure = next(
            (
                candidate
                for candidate in ("direct", "codemode", "deferred")
                if candidate in exposures
            ),
            "hidden",
        )
        with self._lock:
            previous = self._resource_exposure
            if exposure == previous or (previous is None and exposure == "hidden"):
                return None
            self._resource_exposure = exposure
            self._resource_ports = {
                port.definition.name: port
                for port in create_mcp_resource_tools(
                    exposure=exposure, servers=self._resource_servers
                )
            }
        return previous == "direct"

    def _servers_with_resources(
        self,
    ) -> list[tuple[McpServerEntry, McpServerConnection]]:
        """The enabled servers with resources whose exposure is not
        ``hidden``, which the resource tools reach. A server counts from its
        first connect on, also while it is disconnected."""

        with self._lock:
            servers = [(server.entry, server.connection) for server in self._servers]
        return [
            (entry, connection)
            for entry, connection in servers
            if connection is not None
            and _is_enabled(entry)
            and _exposure_of(entry) != "hidden"
            and connection.has_resources
        ]

    def _resource_servers(self) -> list[McpServerConnection]:
        return [connection for _, connection in self._servers_with_resources()]

    def _reads_resources(self, server_name: str) -> bool:
        """Whether ``read_mcp_resource`` reaches the server's resources now."""

        return any(
            entry.name == server_name for entry, _ in self._servers_with_resources()
        )

    def _merged_ports(self) -> dict[str, ToolPort]:
        """The tools of all servers and the resource tools. The manager lock
        is held."""

        merged: dict[str, ToolPort] = {
            name: port
            for server in self._servers
            for name, port in server.ports.items()
        }
        merged.update(self._resource_ports)
        return merged

    def _publish(self, merged: Mapping[str, ToolPort]) -> None:
        """Publish the session's MCP tools. The publication lock is held."""

        for name in self._publish_tools(merged):
            self._notify(
                "warning",
                f'MCP tool "{name}" is not available: another tool has that name',
            )

    def _create_ports(
        self,
        entry: McpServerEntry,
        connection: McpServerConnection,
        view: ConnectionView,
    ) -> dict[str, McpToolPort]:
        tools = [str(tool["name"]) for tool in view.tools]
        namespace = mcp_tool_namespace(
            entry.name,
            description=entry.config.get("description"),
            instructions=view.instructions,
        )
        ports: dict[str, McpToolPort] = {}
        for tool, tool_name, name in zip(
            view.tools, tools, self._names.assign(entry.name, tools), strict=True
        ):
            try:
                ports[name] = McpToolPort(
                    server=entry.name,
                    tool=tool,
                    name=name,
                    exposure=get_mcp_tool_exposure(entry.config, tool_name),
                    namespace=namespace,
                    timeout_ms=connection.timeout_ms,
                    get_caller=partial(self._caller, entry.name, tool_name),
                    readable_resources=partial(self._reads_resources, entry.name),
                )
            except (TypeError, ValueError) as error:
                # Pi registers such a tool as it is; pipy cannot declare it.
                self._notify(
                    "warning",
                    f'MCP tool "{entry.name}/{tool_name}" is not available: {error}',
                )
        return ports

    def _caller(self, server_name: str, tool_name: str) -> McpServerConnection:
        """The connection a call to ``tool_name`` runs on, resolved now.

        A call that was prepared against an earlier tool list, or before the
        server was disabled and enabled again, uses what is current.
        """

        with self._lock:
            server = next(
                (
                    candidate
                    for candidate in self._servers
                    if candidate.entry.name == server_name
                ),
                None,
            )
            if server is None or not _is_enabled(server.entry):
                raise McpToolUnavailableError(
                    f'MCP server "{server_name}" is disabled.'
                )
            connection = server.connection
            entry = server.entry
        if connection is None:
            raise McpToolUnavailableError(
                f'MCP server "{server_name}" is still starting.'
            )
        view = connection.view()
        if get_mcp_tool_exposure(entry.config, tool_name) == "hidden" or (
            view.state == "connected"
            and not any(offered.get("name") == tool_name for offered in view.tools)
        ):
            raise McpToolUnavailableError(
                f'MCP tool "{server_name}/{tool_name}" is no longer available.'
            )
        return connection

    def post_notice(self, level: str, message: str) -> None:
        """Keep a notice for the session thread; each text once per session."""

        self._notify(level, message)

    def _notify(self, level: str, message: str) -> None:

        with self._lock:
            if message in self._reported:
                return
            self._reported.add(message)
        self._notices.append(McpNotice(level, message))
