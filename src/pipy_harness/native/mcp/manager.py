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

Not built yet: servers registered by extensions, enabling and disabling a
server in a session, and ``/reload``.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Final

from pipy_harness.native.mcp.config import (
    LoadedMcpConfig,
    McpServerEntry,
    configured_exposures,
    get_mcp_tool_exposure,
)
from pipy_harness.native.mcp.connection import (
    ConnectionView,
    McpServerConnection,
    McpTransportFactory,
    create_default_transport,
)
from pipy_harness.native.mcp.log import McpServerLog
from pipy_harness.native.mcp.protocol import JsonObject
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


def describe_state(status: McpServerStatus, *, with_error: bool = True) -> str:
    """Short state of a server for lists and the startup report (Pi
    ``describeState``). ``with_error`` appends the first line of a failure."""

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
        return f"connected · {count} tool{'' if count == 1 else 's'}"
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
    server's current one is read and changed under it.
    """

    __slots__ = ("connection", "entry", "ports", "ready", "source")

    def __init__(self, entry: McpServerEntry) -> None:
        self.entry = entry
        self.connection: McpServerConnection | None = None
        # The event of the latest connection attempt: set once what that
        # attempt ended with was registered and published.
        self.ready = threading.Event()
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
    ) -> None:
        """``publish_tools`` receives the tools of all servers after every
        change. ``config`` is read once; ``cwd`` is the session directory."""

        self.config_errors: tuple[str, ...] = tuple(config.errors)
        # ``autoEnableCodemode``; true unless a file says false.
        self.auto_enable_codemode: bool = config.auto_enable_codemode is not False
        self._cwd = cwd
        self._client_version = client_version
        self._publish_tools = publish_tools
        self._log = log
        self._create_transport = create_transport
        self._publication = threading.Lock()
        self._lock = threading.Lock()
        self._servers = [_Server(entry) for entry in config.servers]
        self._retired: list[McpServerConnection] = []
        self._worker: threading.Thread | None = None
        self._started = False
        self._closed = False
        self._names = McpToolNames()
        # A server whose connection changed, and the ``ready`` event of the
        # connection attempt that ended with this change, if one did.
        self._events: queue.SimpleQueue[
            tuple[_Server, threading.Event | None] | None
        ] = queue.SimpleQueue()
        self._notices: deque[McpNotice] = deque()
        # The ``ready`` events of the attempts ``start`` made that have not
        # been registered yet; the startup report waits for them.
        self._startup: set[threading.Event] = set()
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
                    connecting.append((server, server.connection, server.ready))
            self._startup = {ready for _, _, ready in connecting}
            if connecting:
                self._worker = threading.Thread(
                    target=self._run, name="mcp-manager", daemon=True
                )
                self._worker.start()
        if not connecting:
            self._report_problems()
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
                servers, self._servers = self._servers, []
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
        for server in servers:
            server.ready.set()
        if worker is not None:
            self._events.put(None)
            worker.join(_WORKER_JOIN_SECONDS)

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

        A server is waited for while it is enabled and was started; one that
        is disabled or removed during the wait no longer counts. The servers
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
                    and server.connection is not None
                    and _is_enabled(server.entry)
                    and not server.ready.is_set()
                ]
            if not pending:
                return True
            for ready in pending:
                if not _wait_for_event(ready, deadline, cancel_event):
                    return False

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
                        ready.set()

    def _attempt_registered(self, ready: threading.Event) -> None:
        """Report the startup problems once the last attempt ``start`` made
        was registered. A later attempt of a server has another event."""

        with self._lock:
            if ready not in self._startup:
                return
            self._startup.discard(ready)
            if self._startup:
                return
        self._report_problems()

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
            if (
                source is None
                or source[0] is not view.tools
                or source[1] != view.instructions
            ):
                ports = self._create_ports(entry, connection, view)
                with self._lock:
                    server.ports = ports
                    server.source = (view.tools, view.instructions)
                    merged: dict[str, ToolPort] = {
                        name: port
                        for other in self._servers
                        for name, port in other.ports.items()
                    }
                if ports or registered:
                    for name in self._publish_tools(merged):
                        self._notify(
                            "warning",
                            f'MCP tool "{name}" is not available: '
                            "another tool has that name",
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
