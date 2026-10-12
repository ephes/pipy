"""One configured MCP server: connect, list its tools, reconnect, close.

``McpServerConnection`` owns the clients of one server. ``get_client``
returns the connected client or connects; every caller that arrives while a
connect is in progress waits for that one attempt. A connection that dropped
is reported as ``disconnected`` and the next call connects again.

Threads. An attempt runs on a daemon thread of its own (``mcp-connect``), so
the callers that wait for it can give up when their cancel event is set. A
``notifications/tools/list_changed`` is handled on a second daemon thread
(``mcp-refresh``) that lives only while a refresh is wanted, because a
notification listener runs on the client's reading thread and must not send a
request. One lock guards the connection's state; it is never held while the
connection calls a client, a transport or a callback. The connection takes a
client's lock while it holds its own, never the other way round.

Callbacks. ``on_tools`` and ``on_change`` are called on whichever thread made
the change: the attempt thread, the refresh thread, a transport thread or the
caller of ``get_client`` or ``close``. They say that something changed, not
what; the receiver reads ``view()``. Two changes made at nearly the same
time may be announced in either order, and a change that was being announced
when ``close()`` returned may still arrive afterwards. An exception a
callback raises is dropped. A callback may call ``close()``: that call shuts
the connection down but waits for nothing, because the thread it runs on may
be the one a transport needs back before it can finish closing. A callback
must not call ``get_client`` for the attempt it is announcing.

Not built yet: HTTP servers, the retries of a failed HTTP connect, a new
session after the old one expired, OAuth and the ``needs-auth`` state, and
resources.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pipy_harness.native.fuzzy import js_trim
from pipy_harness.native.mcp.client import (
    McpClient,
    McpClientOptions,
    McpRequestOptions,
)
from pipy_harness.native.mcp.config import McpServerEntry
from pipy_harness.native.mcp.config_values import resolve_config_value
from pipy_harness.native.mcp.http import McpAuthProvider
from pipy_harness.native.mcp.log import McpServerLog
from pipy_harness.native.mcp.protocol import (
    JsonObject,
    JsonRpcMessage,
    McpAbortError,
    is_number,
)
from pipy_harness.native.mcp.stdio import StdioTransport, StdioTransportOptions
from pipy_harness.native.mcp.transport import (
    CloseListener,
    Disposer,
    ErrorListener,
    McpTransport,
    MessageListener,
    RequestFailureListener,
)

DEFAULT_TIMEOUT_SECONDS: Final = 60
STDERR_TAIL_CHARS: Final = 2_000
CLIENT_NAME: Final = "pipy"
# How often a caller waiting for a connect looks at its cancel event.
_CANCEL_POLL_SECONDS: Final = 0.05

# ``disconnected``: the connection dropped (for example the stdio server
# exited); the next call reconnects. ``needs-auth`` is entered once sign-in
# exists.
ServerState = Literal[
    "connecting", "connected", "disconnected", "needs-auth", "failed", "closed"
]
# The transport a factory returns must report its close exactly once after a
# ``start`` that succeeded, whoever closes it; ``close()`` relies on that.
McpTransportFactory = Callable[
    [McpServerEntry, str, McpAuthProvider | None], McpTransport
]


class McpServerConnectionError(Exception):
    """A server could not be connected, or its connection is shut down."""


@dataclass(frozen=True, slots=True)
class ConnectionView:
    """What a connection looked like at one moment.

    ``tools`` holds the definitions as the server sent them; treat them as
    read-only. They are kept while the server is disconnected.
    """

    name: str
    state: ServerState
    error: str | None
    tools: tuple[JsonObject, ...]
    instructions: str | None


def expand_home(value: str) -> str:
    """``~`` and ``~/...`` name the home directory, as in a shell."""

    if value == "~":
        return os.path.expanduser("~")
    if not value.startswith("~/"):
        return value
    rest = value[2:]
    # Joined as text, so a second slash (``~//bin``) does not make the rest
    # an absolute path. A trailing slash is kept, as Node's ``path.join`` does.
    expanded = os.path.normpath(f"{os.path.expanduser('~')}/{rest}")
    return expanded + "/" if rest.endswith("/") and expanded != "/" else expanded


def create_default_transport(
    entry: McpServerEntry, cwd: str, auth_provider: McpAuthProvider | None = None
) -> McpTransport:
    """The transport for ``entry``. Only stdio servers can be connected yet.

    ``env`` values are resolved here, so a value that cannot be resolved
    fails the connect with a message naming the server and the key.
    """

    config = entry.config
    if "url" in config:
        raise McpServerConnectionError(
            f'MCP server "{entry.name}" is an HTTP server; '
            "pipy connects only to stdio servers yet"
        )
    env = {
        key: resolve_config_value(value, f'MCP server "{entry.name}" env "{key}"')
        for key, value in (config.get("env") or {}).items()
    }
    return StdioTransport(
        StdioTransportOptions(
            command=expand_home(config["command"]),
            args=tuple(expand_home(argument) for argument in config.get("args") or ()),
            cwd=os.path.normpath(
                os.path.join(
                    os.path.abspath(cwd), expand_home(config.get("cwd") or ".")
                )
            ),
            env=env,
        )
    )


def _error_message(error: BaseException) -> str:
    return str(error) or type(error).__name__


def _is_truthy(value: object) -> bool:
    """JavaScript truthiness of a JSON value: ``{}`` and ``[]`` count."""

    if value is None or value is False or value == "":
        return False
    return not (is_number(value) and (value == 0 or value != value))


def _stderr_tail(transport: StdioTransport) -> str:
    return js_trim(transport.stderr)[-STDERR_TAIL_CHARS:]


def _close_quietly(target: McpClient | McpTransport) -> None:
    try:
        target.close()
    except Exception:  # noqa: BLE001 - closing is best effort; the caller reports its own error
        pass


class _StartReporting:
    """A transport that says when its ``start`` succeeded.

    A transport that started reports its close exactly once, however and by
    whomever it is closed; one that did not start (the executable is
    missing, it was closed before it was handed over) has nothing open and
    may never report a close. ``McpServerConnection.close`` waits for the
    first kind only. Everything else is passed through.
    """

    def __init__(self, transport: McpTransport, on_started: Callable[[], None]) -> None:
        self._transport = transport
        self._on_started = on_started

    def start(self) -> None:
        self._transport.start()
        self._on_started()

    def send(self, message: JsonRpcMessage) -> None:
        self._transport.send(message)

    def close(self) -> None:
        self._transport.close()

    def on_message(self, listener: MessageListener) -> Disposer:
        return self._transport.on_message(listener)

    def on_error(self, listener: ErrorListener) -> Disposer:
        return self._transport.on_error(listener)

    def on_close(self, listener: CloseListener) -> Disposer:
        return self._transport.on_close(listener)

    def on_request_failure(self, listener: RequestFailureListener) -> Disposer:
        return self._transport.on_request_failure(listener)

    def set_protocol_version(self, version: str) -> None:
        self._transport.set_protocol_version(version)


class _Link:
    """One client and what belongs to it. Fields are guarded by the connection lock."""

    __slots__ = (
        "client",
        "refresh_wanted",
        "refreshing",
        "stdio",
        "transport_closed",
    )

    def __init__(self, client: McpClient) -> None:
        self.client = client
        self.stdio: StdioTransport | None = None
        # Set by the transport when it has finished closing.
        self.transport_closed = threading.Event()
        self.refresh_wanted = False
        self.refreshing = False


class _Attempt:
    """One connect that every caller arriving meanwhile waits for."""

    __slots__ = ("cause", "client", "done", "failure", "thread")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.client: McpClient | None = None
        self.failure: str | None = None
        self.cause: BaseException | None = None
        self.thread: threading.Thread | None = None


class McpServerConnection:
    """One configured server. Reconnects when a call finds the connection gone."""

    def __init__(
        self,
        *,
        entry: McpServerEntry,
        cwd: Path | str,
        client_version: str,
        on_tools: Callable[[McpServerConnection], None],
        on_change: Callable[[McpServerConnection], None] | None = None,
        create_transport: McpTransportFactory = create_default_transport,
        log: McpServerLog | None = None,
    ) -> None:
        """``on_tools`` is called when the tool list was read, ``on_change``
        when the state, the error or the tools changed. ``log`` receives the
        server's log messages. ``cwd`` is the session directory."""

        self.entry = entry
        self._cwd = os.path.abspath(cwd)
        self._client_version = client_version
        self._on_tools = on_tools
        self._on_change = on_change
        self._create_transport = create_transport
        self._log = log
        self._lock = threading.Lock()
        self._state: ServerState = "connecting"
        self._error: str | None = None
        self._tools: tuple[JsonObject, ...] = ()
        self._instructions: str | None = None
        self._link: _Link | None = None  # the connected client
        self._pending_link: _Link | None = None  # the client of the running attempt
        # Every link whose transport started and has not reported its close,
        # whether the link is connecting, connected or dropped.
        self._open_links: list[_Link] = []
        # How many callbacks of this connection the current thread is inside.
        self._callback_depth = threading.local()
        self._opening: _Attempt | None = None
        # Stderr of the last stdio server that failed to connect.
        self._failed_stderr: str | None = None
        self._closed = False

    @property
    def name(self) -> str:
        return self.entry.name

    @property
    def timeout_ms(self) -> float:
        """The request timeout: ``timeout`` seconds from the config, default 60."""

        timeout = self.entry.config.get("timeout")
        seconds: float = DEFAULT_TIMEOUT_SECONDS if timeout is None else timeout
        return seconds * 1000

    def view(self) -> ConnectionView:
        with self._lock:
            return ConnectionView(
                name=self.entry.name,
                state=self._state,
                error=self._error,
                tools=self._tools,
                instructions=self._instructions,
            )

    @property
    def state(self) -> ServerState:
        with self._lock:
            return self._state

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def tools(self) -> tuple[JsonObject, ...]:
        with self._lock:
            return self._tools

    @property
    def instructions(self) -> str | None:
        """The server's instructions from ``initialize``, trimmed; ``None`` when empty."""

        with self._lock:
            return self._instructions

    def get_client(self, cancel_event: threading.Event | None = None) -> McpClient:
        """Return the connected client, connecting first when there is none.

        Blocks until the connect ends. Raises ``McpServerConnectionError``
        when it fails or the connection is shut down, and ``McpAbortError``
        when ``cancel_event`` is set first; the attempt then goes on for the
        other callers and the next one.
        """

        with self._lock:
            if self._closed:
                raise McpServerConnectionError(
                    f'MCP server "{self.entry.name}" is shut down'
                )
            link = self._link
            if link is not None:
                if link.client.connection_state == "connected":
                    return link.client
                # It dropped and its close listener has not run yet. That
                # listener finds the link replaced and reports nothing.
                self._link = None
            attempt = self._opening
            started = attempt is None
            if attempt is None:
                attempt = _Attempt()
                attempt.thread = threading.Thread(
                    target=self._run_attempt,
                    args=(attempt,),
                    name="mcp-connect",
                    daemon=True,
                )
                attempt.thread.start()
                self._opening = attempt
                self._state = "connecting"
        if started:
            self._changed()
        if attempt.thread is threading.current_thread():
            # A callback announcing this attempt's failure asked for the
            # client: waiting would be waiting for itself.
            raise McpServerConnectionError(
                f'MCP server "{self.entry.name}" is still connecting'
            )
        while not attempt.done.wait(
            None if cancel_event is None else _CANCEL_POLL_SECONDS
        ):
            if cancel_event is not None and cancel_event.is_set():
                raise McpAbortError()
        if attempt.client is None:
            raise McpServerConnectionError(attempt.failure) from attempt.cause
        return attempt.client

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, object] | None = None,
        options: McpRequestOptions | None = None,
    ) -> JsonObject:
        """Call a tool of the server, connecting first when needed."""

        client = self.get_client(None if options is None else options.cancel_event)
        return client.call_tool(name, arguments, options)

    def close(self) -> None:
        """Shut the connection down for good.

        Stops a connect in progress and returns only when no transport of
        this server is open, including one that was still connecting and one
        that somebody else is closing. Afterwards every call fails with
        ``MCP server "<name>" is shut down``. Every caller waits, also when
        another thread closed first; only a call made from inside a callback
        of this connection returns without waiting.
        """

        with self._lock:
            first = not self._closed
            self._closed = True
            self._state = "closed"
            link = self._link
            self._link = None
            pending = self._pending_link
            attempt = self._opening
        if first:
            self._changed()
        if link is not None:
            _close_quietly(link.client)
        if pending is not None:
            # Fails its pending initialize or tools/list and closes its
            # transport; the attempt then ends.
            _close_quietly(pending.client)
        if getattr(self._callback_depth, "value", 0):
            return
        # Every caller waits for the facts, not for the first caller. A
        # ``close()`` of a client or a transport returns at once when another
        # thread is already closing it, so the return of such a call says
        # nothing; a transport that started says itself when it has closed.
        if attempt is not None and attempt.thread is not threading.current_thread():
            attempt.done.wait()
        # No attempt runs now and none can start, so no transport starts any
        # more and the list only shrinks.
        with self._lock:
            still_open = list(self._open_links)
        for open_link in still_open:
            open_link.transport_closed.wait()

    # -- connecting -------------------------------------------------------

    def _run_attempt(self, attempt: _Attempt) -> None:
        try:
            attempt.client = self._connect_once()
        except Exception as error:  # noqa: BLE001 - any failure is this attempt's result
            attempt.cause = error
            attempt.failure = self._connect_failed(error)
        finally:
            if attempt.client is None and attempt.failure is None:
                attempt.failure = (
                    f'MCP server "{self.entry.name}" failed to connect: '
                    "the attempt ended unexpectedly"
                )
            with self._lock:
                if self._opening is attempt:
                    self._opening = None
            attempt.done.set()

    def _connect_once(self) -> McpClient:
        link = _Link(self._new_client())
        client = link.client
        transport: McpTransport | None = None
        with self._lock:
            self._failed_stderr = None
            if self._closed:
                raise McpServerConnectionError("shut down while connecting")
            # From here ``close()`` closes this client, which ends the steps
            # below.
            self._pending_link = link
        try:
            transport = self._create_transport(self.entry, self._cwd, None)
            # Registered before the client starts the transport, so the close
            # of a transport that started is always heard.
            transport.on_close(lambda: self._transport_closed(link))
            refresh = self._set_up(link, transport)
        except BaseException:
            with self._lock:
                if self._pending_link is link:
                    self._pending_link = None
            _close_quietly(client)
            if transport is not None:
                # A transport the client never started is still to be closed.
                _close_quietly(transport)
            if isinstance(transport, StdioTransport):
                with self._lock:
                    self._failed_stderr = _stderr_tail(transport) or None
            raise
        self._changed(tools=True)
        if refresh:
            self._start_refresher(link)
        return client

    def _new_client(self) -> McpClient:
        name = self.entry.name
        client = McpClient(
            McpClientOptions(
                name=CLIENT_NAME,
                version=self._client_version,
                request_timeout_ms=self.timeout_ms,
                roots=(
                    {
                        "uri": Path(self._cwd).as_uri(),
                        "name": os.path.basename(self._cwd),
                    },
                ),
            )
        )
        log = self._log
        if log is not None:
            client.on_notification(
                "notifications/message", lambda params: log.write(name, params)
            )
        return client

    def _set_up(self, link: _Link, transport: McpTransport) -> bool:
        """Connect ``link``'s client, list the tools and publish the link.

        Returns whether the tools changed while they were being listed, in
        which case the caller starts a refresh.
        """

        client = link.client
        client.connect(
            _StartReporting(transport, lambda: self._transport_started(link))
        )
        client.on_notification(
            "notifications/tools/list_changed",
            lambda params: self._schedule_refresh(link),
        )
        if isinstance(transport, StdioTransport):
            link.stdio = transport
        client.on_close(lambda: self._handle_client_close(link))
        # A server without the tools capability (prompts or resources only)
        # does not answer tools/list.
        capabilities = client.server_capabilities or {}
        tools = client.list_tools() if _is_truthy(capabilities.get("tools")) else []
        instructions = js_trim(client.instructions or "") or None
        with self._lock:
            if self._closed:
                raise McpServerConnectionError("shut down while connecting")
            # Checked under the lock the close listener takes: a client that
            # closes after this is reported by that listener.
            if client.connection_state != "connected":
                raise McpServerConnectionError("connection closed during setup")
            self._pending_link = None
            self._link = link
            self._tools = tuple(tools)
            self._instructions = instructions
            self._state = "connected"
            self._error = None
            link.refreshing = link.refresh_wanted
            return link.refreshing

    def _connect_failed(self, error: Exception) -> str:
        """Record a failed connect; return the text its callers are given."""

        detail = _error_message(error)
        with self._lock:
            self._state = "closed" if self._closed else "failed"
            self._error = (
                f"{detail}\n{self._failed_stderr}" if self._failed_stderr else detail
            )
            recorded = self._error
        self._changed()
        return f'MCP server "{self.entry.name}" failed to connect: {recorded}'

    def _handle_client_close(self, link: _Link) -> None:
        """The transport dropped. The next call reconnects; the status says why."""

        with self._lock:
            if self._link is not link or self._closed:
                return
            self._link = None
            self._state = "disconnected"
            stderr = "" if link.stdio is None else _stderr_tail(link.stdio)
            self._error = (
                f"Connection closed\n{stderr}" if stderr else "Connection closed"
            )
        self._changed()

    def _transport_started(self, link: _Link) -> None:
        with self._lock:
            # It may have closed again already, on another thread.
            if not link.transport_closed.is_set():
                self._open_links.append(link)

    def _transport_closed(self, link: _Link) -> None:
        """``link``'s transport finished closing. Runs on a transport thread."""

        link.transport_closed.set()
        with self._lock:
            if link in self._open_links:
                self._open_links.remove(link)

    # -- tool list changes ------------------------------------------------

    def _schedule_refresh(self, link: _Link) -> None:
        """Have ``link``'s tools listed again. Runs on a client's reading thread."""

        with self._lock:
            if self._closed or link not in (self._link, self._pending_link):
                return
            link.refresh_wanted = True
            if link is not self._link or link.refreshing:
                # A link that is still connecting is refreshed once it is
                # published; a running refresher lists again.
                return
            link.refreshing = True
        self._start_refresher(link)

    def _start_refresher(self, link: _Link) -> None:
        try:
            threading.Thread(
                target=self._refresh_tools,
                args=(link,),
                name="mcp-refresh",
                daemon=True,
            ).start()
        except RuntimeError as error:
            with self._lock:
                link.refreshing = False
            self._refresh_failed(link, error)

    def _refresh_tools(self, link: _Link) -> None:
        while True:
            with self._lock:
                if self._link is not link or self._closed or not link.refresh_wanted:
                    link.refreshing = False
                    return
                link.refresh_wanted = False
            try:
                tools = link.client.list_tools()
            except Exception as error:  # noqa: BLE001 - recorded as the connection's error
                self._refresh_failed(link, error)
                continue
            with self._lock:
                if self._link is not link or self._closed:
                    continue
                self._tools = tuple(tools)
            self._changed(tools=True)

    def _refresh_failed(self, link: _Link, error: Exception) -> None:
        with self._lock:
            if self._link is not link or self._closed:
                return
            self._error = f"Failed to refresh tools: {_error_message(error)}"
        self._changed()

    def _changed(self, *, tools: bool = False) -> None:
        """Announce a change. An exception from a callback is dropped: it
        must not end the thread that connects, refreshes or reads."""

        callbacks = (self._on_tools if tools else None, self._on_change)
        depth = getattr(self._callback_depth, "value", 0)
        self._callback_depth.value = depth + 1
        try:
            for callback in callbacks:
                if callback is None:
                    continue
                try:
                    callback(self)
                except Exception:  # noqa: BLE001 - see above; there is nowhere to report it
                    pass
        finally:
            self._callback_depth.value = depth
