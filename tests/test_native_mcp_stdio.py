"""Tests for the MCP stdio transport, against the scripted fake server."""

from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.mcp import stdio
from pipy_harness.native.mcp.client import McpClient, McpClientOptions
from pipy_harness.native.mcp.protocol import (
    INVALID_REQUEST,
    JsonRpcMessage,
    McpConnectionClosedError,
    McpError,
    McpProtocolError,
)
from pipy_harness.native.mcp.stdio import (
    DEFAULT_CLOSE_TIMEOUT_MS,
    DEFAULT_MAX_STDERR_BYTES,
    STDIN_CLOSE_GRACE_MS,
    StdioTransport,
    StdioTransportOptions,
    _LineSplitter,
)
from pipy_harness.native.mcp.transport import DEFAULT_MAX_MESSAGE_BYTES, McpTransport

_FAKE_SERVER = Path(__file__).parent / "fixtures" / "mcp_fake_server.py"
_WAIT_SECONDS = 10.0
_INCOMPLETE = "MCP stdio server closed with an incomplete JSON-RPC message"


def _response(request_id: int, result: dict[str, Any] | None = None) -> str:
    message = {"jsonrpc": "2.0", "id": request_id, "result": result or {}}
    return json.dumps(message, separators=(",", ":"))


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.01)


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class _Server:
    """One fake server process behind a ``StdioTransport``."""

    def __init__(self, transport: StdioTransport, record_path: Path) -> None:
        self.transport = transport
        self.record_path = record_path
        # Everything the transport reported, in order.
        self.log: list[tuple[str, object]] = []
        self.messages: queue.Queue[JsonRpcMessage] = queue.Queue()
        self.errors: queue.Queue[Exception] = queue.Queue()
        self.closed = threading.Event()
        transport.on_message(self._on_message)
        transport.on_error(self._on_error)
        transport.on_close(self._on_close)

    def _on_message(self, message: JsonRpcMessage) -> None:
        self.log.append(("message", message))
        self.messages.put(message)

    def _on_error(self, error: Exception) -> None:
        self.log.append(("error", error))
        self.errors.put(error)

    def _on_close(self) -> None:
        self.log.append(("close", None))
        self.closed.set()

    def request(
        self, request_id: int, method: str, params: dict[str, Any] | None = None
    ) -> JsonRpcMessage:
        message: JsonRpcMessage = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self.transport.send(message)
        return self.messages.get(timeout=_WAIT_SECONDS)

    def records(self) -> list[dict[str, Any]]:
        if not self.record_path.exists():
            return []
        lines = self.record_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def events(self) -> list[str]:
        return [record["event"] for record in self.records() if "event" in record]

    def received_methods(self) -> list[str]:
        return [
            record["received"]["method"]
            for record in self.records()
            if "received" in record
        ]

    def child_pid(self) -> int:
        """The pid the server announced for its ``spawn`` step."""

        found: list[int] = []

        def announced() -> bool:
            match = re.search(r"child (\d+)", self.transport.stderr)
            if match:
                found.append(int(match.group(1)))
            return bool(match)

        _wait_until(announced)
        return found[0]


StartServer = Callable[..., _Server]


@pytest.fixture
def start_server(tmp_path: Path) -> Iterator[StartServer]:
    servers: list[_Server] = []

    def start(
        scenario: dict[str, Any] | None = None, *, start: bool = True, **options: Any
    ) -> _Server:
        index = len(servers)
        record_path = tmp_path / f"record-{index}.jsonl"
        scenario_path = tmp_path / f"scenario-{index}.json"
        scenario_path.write_text(
            json.dumps({"record": str(record_path), **(scenario or {})}),
            encoding="utf-8",
        )
        transport = StdioTransport(
            StdioTransportOptions(
                command=sys.executable,
                args=("-I", str(_FAKE_SERVER), "--scenario", str(scenario_path)),
                **options,
            )
        )
        server = _Server(transport, record_path)
        servers.append(server)
        if start:
            transport.start()
        return server

    yield start
    for server in servers:
        server.transport.close()
        for match in re.finditer(r"child (\d+)", server.transport.stderr):
            try:
                os.kill(int(match.group(1)), signal.SIGKILL)
            except ProcessLookupError:
                pass


# -- start ----------------------------------------------------------------


def test_stdio_transport_satisfies_the_transport_protocol() -> None:
    transport: McpTransport = StdioTransport(StdioTransportOptions(command="server"))
    transport.set_protocol_version("2025-11-25")


def test_defaults_match_the_documented_limits() -> None:
    options = StdioTransportOptions(command="server")
    assert DEFAULT_MAX_MESSAGE_BYTES == 16 * 1024 * 1024
    assert options.max_message_bytes == DEFAULT_MAX_MESSAGE_BYTES
    assert options.max_stderr_bytes == DEFAULT_MAX_STDERR_BYTES == 64 * 1024
    assert options.close_timeout_ms == DEFAULT_CLOSE_TIMEOUT_MS == 2_000
    assert STDIN_CLOSE_GRACE_MS == 500


def test_connects_to_a_newline_delimited_server_and_captures_stderr(
    start_server: StartServer,
) -> None:
    # Pi: "connects to a newline-delimited MCP server and captures stderr"
    stderr: list[str] = []
    server = start_server(
        {
            "tools": [{"name": "echo", "inputSchema": {"type": "object"}}],
            "startup": [{"stderr": "stdio fixture ready\n"}],
        },
        start=False,
        on_stderr=stderr.append,
    )
    client = McpClient(McpClientOptions(name="stdio-test", version="1.0.0"))
    client.connect(server.transport)

    assert client.list_tools() == [{"name": "echo", "inputSchema": {"type": "object"}}]
    assert client.call_tool("echo", {"text": "hello"}) == {
        "content": [{"type": "text", "text": "hello"}]
    }
    assert isinstance(server.transport.pid, int)
    _wait_until(lambda: "stdio fixture ready" in "".join(stderr))
    assert "stdio fixture ready" in server.transport.stderr

    client.close()
    assert client.connection_state == "closed"
    assert server.closed.is_set()
    assert server.received_methods() == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
    ]
    assert server.events() == ["stdin-closed"]


def test_server_runs_with_the_parent_environment_plus_env_in_cwd_and_own_group(
    start_server: StartServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TEST_PARENT", "parent")
    monkeypatch.setenv("MCP_TEST_OVERRIDDEN", "parent")
    work = tmp_path / "work"
    work.mkdir()
    server = start_server(
        cwd=str(work),
        env={"MCP_TEST_EXTRA": "extra", "MCP_TEST_OVERRIDDEN": "configured"},
    )

    info = server.request(1, "fixture/process")["result"]
    assert isinstance(info, dict)
    assert info["env"]["MCP_TEST_PARENT"] == "parent"
    assert info["env"]["MCP_TEST_EXTRA"] == "extra"
    assert info["env"]["MCP_TEST_OVERRIDDEN"] == "configured"
    assert os.path.realpath(info["cwd"]) == os.path.realpath(work)
    assert info["pid"] == server.transport.pid
    assert info["pgid"] == info["pid"] == info["sid"]
    assert info["pgid"] != os.getpgrp()


def test_inherit_env_false_passes_only_the_configured_environment(
    start_server: StartServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TEST_PARENT", "parent")
    server = start_server(inherit_env=False, env={"MCP_TEST_EXTRA": "extra"})

    info = server.request(1, "fixture/process")["result"]
    assert isinstance(info, dict)
    assert info["env"]["MCP_TEST_EXTRA"] == "extra"
    assert "MCP_TEST_PARENT" not in info["env"]
    assert "PATH" not in info["env"]


def test_missing_executable_fails_the_start(tmp_path: Path) -> None:
    transport = StdioTransport(
        StdioTransportOptions(command=str(tmp_path / "no-such-server"))
    )
    closed = threading.Event()
    transport.on_close(closed.set)

    with pytest.raises(FileNotFoundError):
        transport.start()
    assert transport.pid is None
    with pytest.raises(McpConnectionClosedError):
        transport.send({"jsonrpc": "2.0", "method": "ping"})
    transport.close()
    assert closed.is_set()


def test_missing_executable_fails_the_client_connection(tmp_path: Path) -> None:
    transport = StdioTransport(
        StdioTransportOptions(command=str(tmp_path / "no-such-server"))
    )
    client = McpClient(McpClientOptions(name="stdio-test", version="1.0.0"))

    with pytest.raises(FileNotFoundError):
        client.connect(transport)
    assert client.connection_state == "closed"


def test_start_is_allowed_once_and_not_after_close(start_server: StartServer) -> None:
    server = start_server()
    with pytest.raises(RuntimeError, match="MCP stdio transport already started"):
        server.transport.start()

    unstarted = start_server(start=False)
    with pytest.raises(McpConnectionClosedError):
        unstarted.transport.send({"jsonrpc": "2.0", "method": "ping"})
    unstarted.transport.close()
    assert unstarted.closed.is_set()
    with pytest.raises(McpConnectionClosedError):
        unstarted.transport.start()
    unstarted.transport.close()
    assert unstarted.log == [("close", None)]


# -- messages -------------------------------------------------------------


def test_strips_carriage_returns_and_skips_blank_lines(
    start_server: StartServer,
) -> None:
    server = start_server(
        {
            "methods": {
                "ping": [
                    {"stdout": "\r\n\n   \n"},
                    {"stdout": _response(1, {"crlf": True}) + "\r\n"},
                    {"stdout": "\n" + _response(2) + "\n"},
                ]
            }
        }
    )

    assert server.request(1, "ping") == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"crlf": True},
    }
    assert server.messages.get(timeout=_WAIT_SECONDS)["id"] == 2
    assert server.log == [("message", message) for _, message in server.log]


def test_sends_newline_delimited_json(start_server: StartServer) -> None:
    server = start_server()
    server.request(1, "ping", {"text": "line\nbreak ä€", "n": 1})

    lines = server.record_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["received"] == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "ping",
        "params": {"text": "line\nbreak ä€", "n": 1},
    }
    with pytest.raises(ValueError):
        server.transport.send({"jsonrpc": "2.0", "method": "x", "params": float("nan")})
    assert server.request(2, "ping")["id"] == 2


def test_invalid_lines_are_reported_without_closing(start_server: StartServer) -> None:
    server = start_server(
        {
            "methods": {
                "ping": [
                    {"stdout": "not json\n"},
                    {"stdout": '{"jsonrpc":"2.0"}\n'},
                    {"stdout": '{"jsonrpc":"2.0","id":1,"result":NaN}\n'},
                    {"stdout": "[1]\n"},
                    {"default": True},
                ]
            }
        }
    )

    assert server.request(1, "ping") == {"jsonrpc": "2.0", "id": 1, "result": {}}
    kinds = [kind for kind, _ in server.log]
    assert kinds == ["error", "error", "error", "error", "message"]
    errors = [value for kind, value in server.log if kind == "error"]
    assert isinstance(errors[0], json.JSONDecodeError)
    assert isinstance(errors[1], McpError) and errors[1].code == INVALID_REQUEST
    assert isinstance(errors[2], ValueError)
    assert isinstance(errors[3], McpError) and errors[3].code == INVALID_REQUEST
    assert not server.closed.is_set()
    assert server.request(2, "ping")["id"] == 2


def test_oversize_messages_are_dropped_and_the_stream_continues(
    start_server: StartServer,
) -> None:
    at_limit = _response(1, {"pad": ""})
    at_limit = _response(1, {"pad": "p" * (64 - len(at_limit))})
    assert len(at_limit) == 64
    server = start_server(
        {
            "methods": {
                "ping": [
                    {"stdout": "x" * 65 + "\n"},
                    {"stdout": at_limit + "\n"},
                    # Arrives in pieces: the limit is passed before the newline.
                    {"stdout": "y" * 200},
                    {"sleep": 0.2},
                    {"stdout": "y" * 200},
                    {"sleep": 0.2},
                    {"stdout": 'tail"}\n' + _response(2) + "\n"},
                ]
            }
        },
        max_message_bytes=64,
    )

    assert server.request(1, "ping")["id"] == 1
    assert server.messages.get(timeout=_WAIT_SECONDS)["id"] == 2
    assert [kind for kind, _ in server.log] == ["error", "message", "error", "message"]
    for error in (value for kind, value in server.log if kind == "error"):
        assert isinstance(error, McpProtocolError)
        assert str(error) == "MCP stdio message exceeds 64 bytes"
    assert not server.closed.is_set()


def test_a_message_over_16_mib_is_dropped_with_an_error(
    start_server: StartServer,
) -> None:
    server = start_server(
        {
            "methods": {
                "ping": [
                    {"stdout": "x", "times": DEFAULT_MAX_MESSAGE_BYTES + 1},
                    {"stdout": "\n"},
                    {"default": True},
                ]
            }
        }
    )

    assert server.request(1, "ping")["id"] == 1
    assert [kind for kind, _ in server.log] == ["error", "message"]
    assert str(server.errors.get_nowait()) == "MCP stdio message exceeds 16777216 bytes"


def test_line_splitter_gives_the_same_lines_for_any_chunking() -> None:
    data = b"one\n\ntoo long line\r\nexactly8\nlast\npartial"
    expected = [b"one", b"", None, b"exactly8", b"last"]

    whole = _LineSplitter(8)
    assert list(whole.feed(data)) == expected
    assert whole.has_partial_line

    single = _LineSplitter(8)
    lines = [line for byte in data for line in single.feed(bytes([byte]))]
    assert lines == expected
    assert single.has_partial_line


def test_line_splitter_reports_a_partial_line_only_for_content() -> None:
    splitter = _LineSplitter(8)
    assert not splitter.has_partial_line
    assert list(splitter.feed(b"done\n \r\t")) == [b"done"]
    assert not splitter.has_partial_line
    # A dropped line that never ended is still an incomplete message.
    assert list(splitter.feed(b"123456789")) == [None]
    assert list(splitter.feed(b"more")) == []
    assert splitter.has_partial_line
    assert list(splitter.feed(b"\nnext\n")) == [b"next"]
    assert not splitter.has_partial_line


def test_a_raising_message_listener_is_reported_and_delivery_continues(
    start_server: StartServer,
) -> None:
    server = start_server(start=False)
    failure = RuntimeError("listener failed")
    calls: list[JsonRpcMessage] = []

    def listener(message: JsonRpcMessage) -> None:
        calls.append(message)
        if len(calls) == 1:
            raise failure

    def raising_error_listener(error: Exception) -> None:
        raise RuntimeError("error listener failed")

    server.transport.on_message(listener)
    server.transport.on_error(raising_error_listener)
    server.transport.start()
    server.transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    server.transport.send({"jsonrpc": "2.0", "id": 2, "method": "ping"})

    _wait_until(lambda: len(calls) == 2)
    assert server.errors.get(timeout=_WAIT_SECONDS) is failure


def test_concurrent_sends_do_not_mix_messages(start_server: StartServer) -> None:
    server = start_server({"methods": {"note": []}})
    padding = "p" * 20_000

    def send_many(sender: int) -> None:
        for index in range(20):
            server.transport.send(
                {
                    "jsonrpc": "2.0",
                    "method": "note",
                    "params": {"sender": sender, "index": index, "padding": padding},
                }
            )

    threads = [threading.Thread(target=send_many, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(_WAIT_SECONDS)
    server.request(1, "ping")

    records = server.records()
    assert all("received" in record for record in records)
    notes = [r["received"]["params"] for r in records if "params" in r["received"]]
    assert len(notes) == 120
    for sender in range(6):
        assert [n["index"] for n in notes if n["sender"] == sender] == list(range(20))


# -- stderr ---------------------------------------------------------------


def test_keeps_the_last_64_kib_of_stderr_readable_after_the_server_failed(
    start_server: StartServer,
) -> None:
    server = start_server(
        {
            "startup": [
                {"stderr": "0123456789", "times": 10_000},
                {"stderr": "fatal: giving up\n"},
                {"exit": 3},
            ]
        }
    )

    assert server.closed.wait(_WAIT_SECONDS)
    tail = server.transport.stderr
    assert len(tail) == 64 * 1024
    assert tail.endswith("89fatal: giving up\n")
    assert server.transport.pid is None


def test_stderr_limit_is_configurable_and_a_raising_callback_is_reported(
    start_server: StartServer,
) -> None:
    def on_stderr(text: str) -> None:
        raise RuntimeError("stderr callback failed")

    server = start_server(
        {"startup": [{"stderr": "abcdefghijklmnopqrstuvwxyz"}]},
        max_stderr_bytes=10,
        on_stderr=on_stderr,
    )

    assert str(server.errors.get(timeout=_WAIT_SECONDS)) == "stderr callback failed"
    assert server.transport.stderr == "qrstuvwxyz"
    assert server.request(1, "ping")["id"] == 1


def test_on_stderr_gets_a_character_cut_off_by_the_end_of_the_stream(
    tmp_path: Path,
) -> None:
    chunks: list[str] = []
    closed = threading.Event()
    script = "import sys; sys.stderr.buffer.write(b'error: \\xe2\\x82')"
    transport = StdioTransport(
        StdioTransportOptions(
            command=sys.executable, args=("-I", "-c", script), on_stderr=chunks.append
        )
    )
    transport.on_close(closed.set)
    transport.start()

    assert closed.wait(_WAIT_SECONDS)
    assert "".join(chunks) == "error: \ufffd"
    assert transport.stderr == "error: \ufffd"


# -- the server exits -----------------------------------------------------


def test_server_exit_closes_the_transport_and_reports_a_partial_message(
    start_server: StartServer,
) -> None:
    server = start_server(
        {
            "methods": {
                "ping": [
                    {"stdout": _response(1) + "\n" + '{"jsonrpc":"2.0","id":2,'},
                    {"exit": 0},
                ]
            }
        }
    )

    assert server.request(1, "ping")["id"] == 1
    assert server.closed.wait(_WAIT_SECONDS)
    assert [kind for kind, _ in server.log] == ["message", "error", "close"]
    error = server.errors.get_nowait()
    assert isinstance(error, McpProtocolError)
    assert str(error) == _INCOMPLETE
    with pytest.raises(McpConnectionClosedError):
        server.transport.send({"jsonrpc": "2.0", "id": 3, "method": "ping"})
    assert server.transport.pid is None
    server.transport.close()
    assert [kind for kind, _ in server.log].count("close") == 1


def test_server_exit_without_leftover_output_reports_only_the_close(
    start_server: StartServer,
) -> None:
    server = start_server({"methods": {"ping": [{"stdout": " \r\n  "}, {"exit": 1}]}})
    server.transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})

    assert server.closed.wait(_WAIT_SECONDS)
    assert server.log == [("close", None)]


def test_server_exit_fails_a_pending_client_request(start_server: StartServer) -> None:
    server = start_server({"methods": {"tools/list": [{"exit": 0}]}}, start=False)
    client = McpClient(McpClientOptions(name="stdio-test", version="1.0.0"))
    client.connect(server.transport)

    with pytest.raises(McpConnectionClosedError):
        client.list_tools()
    assert client.connection_state == "closed"
    with pytest.raises(McpConnectionClosedError):
        server.transport.send({"jsonrpc": "2.0", "method": "ping"})


def test_close_listener_may_call_close_after_the_server_exited(
    start_server: StartServer,
) -> None:
    server = start_server({"methods": {"ping": [{"exit": 0}]}}, start=False)
    returned = threading.Event()

    def close_again() -> None:
        server.transport.close()
        returned.set()

    server.transport.on_close(close_again)
    server.transport.start()
    server.transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})

    assert returned.wait(_WAIT_SECONDS)
    server.transport.close()
    assert [kind for kind, _ in server.log] == ["close"]


# -- close ----------------------------------------------------------------


def test_close_lets_a_server_exit_when_its_stdin_closes(
    start_server: StartServer,
) -> None:
    server = start_server({"onSigterm": [{"exit": 0}]})
    assert server.request(1, "ping")["id"] == 1
    pid = server.transport.pid
    assert pid is not None

    server.transport.close()

    assert server.closed.is_set()
    assert server.events() == ["stdin-closed"]
    assert not _is_alive(pid)
    with pytest.raises(McpConnectionClosedError):
        server.transport.send({"jsonrpc": "2.0", "id": 2, "method": "ping"})


def test_close_from_a_message_listener_returns(start_server: StartServer) -> None:
    server = start_server(start=False)
    returned = threading.Event()

    def close_on_message(message: JsonRpcMessage) -> None:
        server.transport.close()
        returned.set()

    server.transport.on_message(close_on_message)
    server.transport.start()
    server.transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})

    assert returned.wait(_WAIT_SECONDS)
    assert server.closed.is_set()


def test_close_from_a_message_listener_still_reports_a_partial_message(
    start_server: StartServer,
) -> None:
    server = start_server(
        {"methods": {"ping": [{"stdout": _response(1) + '\n{"jsonrpc":'}]}},
        start=False,
    )
    server.transport.on_message(lambda message: server.transport.close())
    server.transport.start()
    server.transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})

    error = server.errors.get(timeout=_WAIT_SECONDS)
    assert isinstance(error, McpProtocolError)
    assert str(error) == _INCOMPLETE
    assert server.closed.is_set()


def test_close_sends_sigterm_after_the_grace_period(start_server: StartServer) -> None:
    server = start_server(
        {"onStdinClose": [], "onSigterm": [{"exit": 0}]}, close_timeout_ms=60_000
    )
    assert server.request(1, "ping")["id"] == 1

    server.transport.close()

    assert server.closed.is_set()
    assert server.events() == ["stdin-closed", "sigterm"]
    stdin_closed, sigterm = (r["t"] for r in server.records() if "event" in r)
    assert 0.4 < sigterm - stdin_closed < 30


def test_close_kills_a_server_that_ignores_shutdown_including_its_children(
    start_server: StartServer,
) -> None:
    # Pi: "kills a server that ignores shutdown, including its children"
    server = start_server(
        {
            "startup": [{"spawn": {"ignoreSigterm": True}}],
            "onStdinClose": [],
            "onSigterm": [],
        },
        start=False,
        close_timeout_ms=100,
    )
    client = McpClient(McpClientOptions(name="stdio-test", version="1.0.0"))
    client.connect(server.transport)
    child = server.child_pid()
    pid = server.transport.pid
    assert pid is not None and _is_alive(child)

    started_at = time.monotonic()
    client.close()

    assert time.monotonic() - started_at < 5
    assert server.closed.is_set()
    assert not _is_alive(pid)
    _wait_until(lambda: not _is_alive(child))
    # SIGTERM came first, after the shortened grace period.
    assert server.events() == ["stdin-closed", "sigterm"]


@pytest.mark.parametrize("wait_without_reaping", [True, False])
def test_close_terminates_children_that_outlive_a_server_that_exited(
    start_server: StartServer,
    monkeypatch: pytest.MonkeyPatch,
    wait_without_reaping: bool,
) -> None:
    if not wait_without_reaping:
        # Platforms without os.waitid reap the server before the signal.
        monkeypatch.delattr(os, "waitid", raising=False)
    server = start_server({"startup": [{"spawn": {}}]})
    assert server.request(1, "ping")["id"] == 1
    child = server.child_pid()
    assert _is_alive(child)

    server.transport.close()

    assert server.events() == ["stdin-closed"]
    _wait_until(lambda: not _is_alive(child))


def test_close_does_not_wait_for_a_child_that_holds_the_server_output(
    start_server: StartServer,
) -> None:
    server = start_server(
        {"startup": [{"spawn": {"ignoreSigterm": True, "inheritStdio": True}}]}
    )
    assert server.request(1, "ping")["id"] == 1
    child = server.child_pid()

    started_at = time.monotonic()
    server.transport.close()

    assert time.monotonic() - started_at < 5
    assert server.closed.is_set()
    assert _is_alive(child)


def test_transport_stays_open_while_a_child_holds_the_output_of_an_exited_server(
    start_server: StartServer,
) -> None:
    server = start_server(
        {
            "startup": [{"spawn": {"ignoreSigterm": True, "inheritStdio": True}}],
            "methods": {"ping": [{"default": True}, {"exit": 0}]},
        }
    )
    assert server.request(1, "ping")["id"] == 1
    server.child_pid()
    pid = server.transport.pid
    assert pid is not None
    _wait_until(lambda: not _is_alive(pid))

    assert not server.closed.wait(0.3)
    server.transport.close()
    assert server.closed.is_set()


def test_close_ends_a_send_blocked_on_a_server_that_does_not_read(
    start_server: StartServer,
) -> None:
    server = start_server({"methods": {"ping": [{"default": True}, {"sleep": 600}]}})
    assert server.request(1, "ping")["id"] == 1
    outcome: queue.Queue[BaseException | None] = queue.Queue()

    def send_large() -> None:
        try:
            server.transport.send(
                {"jsonrpc": "2.0", "method": "note", "params": "p" * 4_000_000}
            )
        except BaseException as error:  # noqa: BLE001 - handed to the test thread
            outcome.put(error)
        else:
            outcome.put(None)

    sender = threading.Thread(target=send_large, daemon=True)
    sender.start()
    time.sleep(0.2)
    assert outcome.empty()

    server.transport.close()

    assert isinstance(outcome.get(timeout=_WAIT_SECONDS), McpConnectionClosedError)
    assert server.closed.is_set()


# -- exit hook ------------------------------------------------------------


def test_running_servers_are_tracked_until_they_exit(start_server: StartServer) -> None:
    server = start_server({"onStdinClose": [], "onSigterm": [{"exit": 0}]})
    assert server.request(1, "ping")["id"] == 1
    pid = server.transport.pid
    assert pid in stdio._live_process_groups

    stdio._terminate_live_process_groups()

    assert server.closed.wait(_WAIT_SECONDS)
    assert server.events() == ["sigterm"]
    assert pid not in stdio._live_process_groups


_EXIT_WITHOUT_CLOSING = """
import sys, threading
from pipy_harness.native.mcp.stdio import StdioTransport, StdioTransportOptions

transport = StdioTransport(StdioTransportOptions(command=sys.executable, args=sys.argv[1:]))
answered = threading.Event()
transport.on_message(lambda message: answered.set())
transport.start()
transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})
assert answered.wait(30)
print(transport.pid, flush=True)
"""


def test_servers_still_alive_at_interpreter_exit_are_sent_sigterm(
    tmp_path: Path,
) -> None:
    record_path = tmp_path / "record.jsonl"
    scenario_path = tmp_path / "scenario.json"
    scenario = {
        "record": str(record_path),
        "onStdinClose": [],
        "onSigterm": [{"exit": 0}],
    }
    scenario_path.write_text(json.dumps(scenario), encoding="utf-8")

    host = subprocess.run(
        [
            sys.executable,
            "-c",
            _EXIT_WITHOUT_CLOSING,
            "-I",
            str(_FAKE_SERVER),
            "--scenario",
            str(scenario_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert host.returncode == 0, host.stderr
    server_pid = int(host.stdout)
    try:
        _wait_until(lambda: not _is_alive(server_pid))
    finally:
        if _is_alive(server_pid):
            os.kill(server_pid, signal.SIGKILL)
    events = [
        json.loads(line).get("event") for line in record_path.read_text().splitlines()
    ]
    assert "sigterm" in events
