"""Tests for the MCP transport listener bookkeeping and the in-memory pair."""

from __future__ import annotations

import math
import queue
import threading

import pytest

from pipy_harness.native.mcp.protocol import JsonRpcMessage, McpConnectionClosedError
from pipy_harness.native.mcp.transport import (
    InMemoryTransport,
    McpTransport,
    TransportEvents,
    create_in_memory_transport_pair,
)

_WAIT_SECONDS = 5.0


def _started_pair() -> tuple[InMemoryTransport, InMemoryTransport]:
    client, server = create_in_memory_transport_pair()
    client.start()
    server.start()
    return client, server


def _inbox(transport: InMemoryTransport) -> queue.Queue[JsonRpcMessage]:
    received: queue.Queue[JsonRpcMessage] = queue.Queue()
    transport.on_message(received.put)
    return received


def test_in_memory_transport_satisfies_the_transport_protocol() -> None:
    transport: McpTransport = InMemoryTransport()
    transport.set_protocol_version("2025-11-25")


def test_listeners_are_held_once_and_removed_by_their_disposer() -> None:
    events = TransportEvents()
    seen: list[JsonRpcMessage] = []
    events.on_message(seen.append)
    dispose = events.on_message(seen.append)

    events.emit_message({"jsonrpc": "2.0", "method": "a"})
    assert seen == [{"jsonrpc": "2.0", "method": "a"}]

    dispose()
    dispose()
    events.emit_message({"jsonrpc": "2.0", "method": "b"})
    assert len(seen) == 1


def test_error_listeners_receive_errors_until_removed() -> None:
    events = TransportEvents()
    errors: list[Exception] = []
    dispose = events.on_error(errors.append)
    failure = OSError("broken pipe")

    events.emit_error(failure)
    dispose()
    events.emit_error(OSError("again"))

    assert errors == [failure]


def test_close_reaches_listeners_at_most_once() -> None:
    events = TransportEvents()
    closed: list[str] = []
    events.on_close(lambda: closed.append("first"))
    removed = events.on_close(lambda: closed.append("removed"))
    removed()

    events.emit_close()
    events.emit_close()

    assert closed == ["first"]


def test_messages_arrive_in_order_on_another_thread_as_copies() -> None:
    client, server = _started_pair()
    threads: list[int] = []
    received: queue.Queue[JsonRpcMessage] = queue.Queue()

    def listener(message: JsonRpcMessage) -> None:
        threads.append(threading.get_ident())
        received.put(message)

    server.on_message(listener)
    params = {"values": (1, 2)}
    first: JsonRpcMessage = {"jsonrpc": "2.0", "method": "first", "params": params}
    client.send(first)
    client.send({"jsonrpc": "2.0", "method": "second"})

    delivered = received.get(timeout=_WAIT_SECONDS)
    # The copy went through JSON, so a tuple arrives as a list.
    assert delivered == {
        "jsonrpc": "2.0",
        "method": "first",
        "params": {"values": [1, 2]},
    }
    assert delivered is not first
    assert received.get(timeout=_WAIT_SECONDS)["method"] == "second"
    assert threads[0] != threading.get_ident()
    client.close()


def test_both_directions_are_connected() -> None:
    client, server = _started_pair()
    at_client = _inbox(client)
    at_server = _inbox(server)

    client.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert at_server.get(timeout=_WAIT_SECONDS)["method"] == "ping"
    server.send({"jsonrpc": "2.0", "id": 1, "result": {}})
    assert at_client.get(timeout=_WAIT_SECONDS) == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {},
    }
    client.close()


def test_send_needs_both_ends_started() -> None:
    client, server = create_in_memory_transport_pair()
    message: JsonRpcMessage = {"jsonrpc": "2.0", "method": "early"}
    with pytest.raises(McpConnectionClosedError, match="^MCP connection closed$"):
        client.send(message)

    client.start()
    with pytest.raises(
        McpConnectionClosedError, match="^In-memory MCP peer is not connected$"
    ):
        client.send(message)

    server.start()
    client.send(message)
    client.close()


def test_send_rejects_a_message_that_is_not_json() -> None:
    client, _server = _started_pair()
    with pytest.raises(TypeError):
        client.send({"jsonrpc": "2.0", "method": "bad", "params": {"value": object()}})
    for number in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            client.send(
                {"jsonrpc": "2.0", "method": "bad", "params": {"value": [number]}}
            )
    client.close()


def test_closing_one_end_closes_both_once() -> None:
    client, server = _started_pair()
    closed: list[str] = []
    client.on_close(lambda: closed.append("client"))
    server.on_close(lambda: closed.append("server"))

    server.close()
    server.close()
    client.close()

    assert closed == ["server", "client"]
    with pytest.raises(McpConnectionClosedError):
        client.send({"jsonrpc": "2.0", "method": "late"})
    with pytest.raises(McpConnectionClosedError):
        server.start()


def test_messages_still_queued_at_close_are_dropped() -> None:
    client, server = _started_pair()
    entered = threading.Event()
    release = threading.Event()
    methods: list[object] = []

    def listener(message: JsonRpcMessage) -> None:
        methods.append(message["method"])
        entered.set()
        release.wait(_WAIT_SECONDS)

    server.on_message(listener)
    client.send({"jsonrpc": "2.0", "method": "first"})
    assert entered.wait(_WAIT_SECONDS)
    client.send({"jsonrpc": "2.0", "method": "second"})
    drained = threading.Event()
    server.on_close(drained.set)

    server.close()
    release.set()

    assert drained.wait(_WAIT_SECONDS)
    assert methods == ["first"]


def test_a_raising_message_listener_is_reported_and_delivery_continues() -> None:
    client, server = _started_pair()
    errors: queue.Queue[Exception] = queue.Queue()
    received: queue.Queue[JsonRpcMessage] = queue.Queue()

    def listener(message: JsonRpcMessage) -> None:
        if message["method"] == "bad":
            raise ValueError("listener failed")
        received.put(message)

    server.on_message(listener)
    server.on_error(errors.put)
    client.send({"jsonrpc": "2.0", "method": "bad"})
    client.send({"jsonrpc": "2.0", "method": "good"})

    assert str(errors.get(timeout=_WAIT_SECONDS)) == "listener failed"
    assert received.get(timeout=_WAIT_SECONDS)["method"] == "good"
    client.close()


def test_a_transport_has_one_peer() -> None:
    client, _server = create_in_memory_transport_pair()
    with pytest.raises(RuntimeError, match="already has a peer"):
        client.connect_peer(InMemoryTransport())
