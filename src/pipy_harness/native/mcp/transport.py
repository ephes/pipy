"""The transport contract of the MCP client, and an in-memory pair for tests.

A transport carries whole JSON-RPC messages. It reports incoming messages,
errors and its own close to listeners, on whichever thread reads the
connection. Listeners are called without any transport lock held.
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable
from typing import Final, Protocol, TypeVar

from pipy_harness.native.mcp.protocol import (
    JsonRpcId,
    JsonRpcMessage,
    McpConnectionClosedError,
)

DEFAULT_MAX_MESSAGE_BYTES: Final = 16 * 1024 * 1024

MessageListener = Callable[[JsonRpcMessage], None]
ErrorListener = Callable[[Exception], None]
CloseListener = Callable[[], None]
RequestFailureListener = Callable[[JsonRpcId, Exception], None]
Disposer = Callable[[], None]
_Listener = TypeVar("_Listener")


class McpTransport(Protocol):
    """What ``McpClient`` needs from a connection to one server.

    ``send`` raises when the message cannot be handed to the server. A
    transport whose ``send`` returns before the server took a request reports
    a later failure to hand it over to the request-failure listeners, with the
    request's id; that request gets no response. Each ``on_*`` method returns
    a callable that removes the listener again.
    ``set_protocol_version`` tells the transport which version the handshake
    selected.
    """

    def start(self) -> None: ...

    def send(self, message: JsonRpcMessage) -> None: ...

    def close(self) -> None: ...

    def on_message(self, listener: MessageListener) -> Disposer: ...

    def on_error(self, listener: ErrorListener) -> Disposer: ...

    def on_close(self, listener: CloseListener) -> Disposer: ...

    def on_request_failure(self, listener: RequestFailureListener) -> Disposer: ...

    def set_protocol_version(self, version: str) -> None: ...


class TransportEvents:
    """Listener bookkeeping shared by transports.

    A listener registered twice is held once. ``emit_close`` reaches the
    listeners at most once per transport.
    """

    def __init__(self) -> None:
        self._listener_lock = threading.Lock()
        self._message_listeners: dict[MessageListener, None] = {}
        self._error_listeners: dict[ErrorListener, None] = {}
        self._close_listeners: dict[CloseListener, None] = {}
        self._request_failure_listeners: dict[RequestFailureListener, None] = {}
        self._close_emitted = False

    def on_message(self, listener: MessageListener) -> Disposer:
        with self._listener_lock:
            self._message_listeners[listener] = None
        return lambda: self._remove(self._message_listeners, listener)

    def on_error(self, listener: ErrorListener) -> Disposer:
        with self._listener_lock:
            self._error_listeners[listener] = None
        return lambda: self._remove(self._error_listeners, listener)

    def on_close(self, listener: CloseListener) -> Disposer:
        with self._listener_lock:
            self._close_listeners[listener] = None
        return lambda: self._remove(self._close_listeners, listener)

    def on_request_failure(self, listener: RequestFailureListener) -> Disposer:
        with self._listener_lock:
            self._request_failure_listeners[listener] = None
        return lambda: self._remove(self._request_failure_listeners, listener)

    def set_protocol_version(self, version: str) -> None:
        """Ignored by transports that do not put the version on the wire."""

    def emit_message(self, message: JsonRpcMessage) -> None:
        with self._listener_lock:
            listeners = list(self._message_listeners)
        for listener in listeners:
            listener(message)

    def emit_error(self, error: Exception) -> None:
        with self._listener_lock:
            listeners = list(self._error_listeners)
        for listener in listeners:
            listener(error)

    def emit_request_failure(self, request_id: JsonRpcId, error: Exception) -> None:
        with self._listener_lock:
            listeners = list(self._request_failure_listeners)
        for listener in listeners:
            listener(request_id, error)

    def emit_close(self) -> None:
        with self._listener_lock:
            if self._close_emitted:
                return
            self._close_emitted = True
            listeners = list(self._close_listeners)
        for listener in listeners:
            listener()

    def _remove(self, listeners: dict[_Listener, None], listener: _Listener) -> None:
        with self._listener_lock:
            listeners.pop(listener, None)


class InMemoryTransport(TransportEvents):
    """One end of an in-process connection.

    A message sent on one end is copied through strict JSON, as on a wire, and
    delivered to the other end's listeners on that end's delivery thread, in
    the order sent. Closing either end closes both.
    """

    def __init__(self) -> None:
        super().__init__()
        self._state_lock = threading.Lock()
        self._peer: InMemoryTransport | None = None
        self._started = False
        self._closed = False
        self._inbox: queue.SimpleQueue[JsonRpcMessage | None] = queue.SimpleQueue()

    def connect_peer(self, peer: InMemoryTransport) -> None:
        with self._state_lock:
            if self._peer is not None:
                raise RuntimeError("In-memory MCP transport already has a peer")
            self._peer = peer

    def start(self) -> None:
        with self._state_lock:
            if self._closed:
                raise McpConnectionClosedError()
            if self._started:
                return
            self._started = True
        threading.Thread(
            target=self._deliver_messages,
            name="mcp-in-memory-transport",
            daemon=True,
        ).start()

    def send(self, message: JsonRpcMessage) -> None:
        with self._state_lock:
            if not self._started or self._closed:
                raise McpConnectionClosedError()
            peer = self._peer
        copy: JsonRpcMessage = json.loads(json.dumps(message, allow_nan=False))
        if peer is None or not peer._accept(copy):
            raise McpConnectionClosedError("In-memory MCP peer is not connected")

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            peer = self._peer
            self._inbox.put(None)
        self.emit_close()
        if peer is not None:
            peer.close()

    def _accept(self, message: JsonRpcMessage) -> bool:
        with self._state_lock:
            if not self._started or self._closed:
                return False
            self._inbox.put(message)
            return True

    def _deliver_messages(self) -> None:
        while True:
            message = self._inbox.get()
            if message is None:
                return
            with self._state_lock:
                if self._closed:
                    return
            try:
                self.emit_message(message)
            except Exception as error:  # noqa: BLE001 - cross-thread capture: reported, delivery continues
                self.emit_error(error)


def create_in_memory_transport_pair() -> tuple[InMemoryTransport, InMemoryTransport]:
    """Return the ``(client, server)`` ends of one in-process connection."""

    client = InMemoryTransport()
    server = InMemoryTransport()
    client.connect_peer(server)
    server.connect_peer(client)
    return client, server
