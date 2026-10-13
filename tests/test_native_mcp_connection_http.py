"""Tests for connections to HTTP MCP servers (MCP parity plan S26).

Rows CON-10 to CON-12 and the retry-wait half of CON-09: the retries of a
connect that failed with a transient error, the one retry of a resource
request, and the new session after the server forgot the old one. The rules
are tested against an in-memory server whose transport fails the way the
HTTP transport does, as in Pi's ``mcp-extension.test.ts`` (a mirrored case
names it in a ``Pi:`` comment), and then with the default transport factory
against the scripted fake HTTP server.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from test_native_mcp_connection import (
    _NO_ANSWER,
    _OK,
    _WAIT_SECONDS,
    _Harness,
    _tool,
    _wait_until,
)
from test_native_mcp_connection_resources import _ResourceServer

from pipy_harness.native.mcp import connection as connection_module
from pipy_harness.native.mcp.client import McpRequestOptions
from pipy_harness.native.mcp.config import McpServerEntry
from pipy_harness.native.mcp.config_values import McpConfigValueError
from pipy_harness.native.mcp.connection import (
    CONNECT_RETRY_DELAYS_MS,
    McpServerConnection,
    McpServerConnectionError,
    create_default_transport,
)
from pipy_harness.native.mcp.http import (
    McpAuthRequiredError,
    McpHttpError,
    McpSessionExpiredError,
    StreamableHttpTransport,
)
from pipy_harness.native.mcp.protocol import (
    JsonRpcMessage,
    McpAbortError,
    McpConnectionClosedError,
    McpTimeoutError,
)
from pipy_harness.native.mcp.transport import (
    CloseListener,
    Disposer,
    ErrorListener,
    InMemoryTransport,
    McpTransport,
    MessageListener,
    RequestFailureListener,
)

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_http_server import FakeMcpHttpServer  # noqa: E402

_HTTP = {"url": "http://unused.invalid", "headers": {"Authorization": "x"}}
_READ = {"contents": [{"uri": "docs://a", "text": "ok"}]}

Failure = Callable[[JsonRpcMessage], Exception | None]


class _Failing:
    """A transport whose ``send`` raises what ``fail`` returns for a message.

    The HTTP transport reports the failure of a request through its
    request-failure listeners; a ``send`` that raises ends the request with
    the same error.
    """

    def __init__(self, transport: InMemoryTransport, fail: Failure) -> None:
        self._transport = transport
        self._fail = fail
        self.closes = 0
        transport.on_close(self._count_close)

    def _count_close(self) -> None:
        self.closes += 1

    def start(self) -> None:
        self._transport.start()

    def send(self, message: JsonRpcMessage) -> None:
        error = self._fail(message)
        if error is not None:
            raise error
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


def _status(status: int, detail: str = "") -> McpHttpError:
    return McpHttpError(status, f"MCP HTTP request failed with status {status}{detail}")


def _always(error: Exception) -> Failure:
    return lambda message: error


def _once_per_method(error: Exception, *methods: str) -> Failure:
    """Fail the first request of each of ``methods``."""

    failed: set[str] = set()

    def fail(message: JsonRpcMessage) -> Exception | None:
        method = str(message.get("method", ""))
        if method in methods and method not in failed:
            failed.add(method)
            return error
        return None

    return fail


def _server(**kwargs: Any) -> _ResourceServer:
    return _ResourceServer(
        tools=[_tool("echo")],
        handlers={"resources/read": lambda message: _READ},
        **kwargs,
    )


def _failing(fail: Failure, server: _ResourceServer | None = None) -> _Failing:
    return _Failing((server or _server()).client_end, fail)


@pytest.fixture
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connection_module, "CONNECT_RETRY_DELAYS_MS", (1, 2))


# -- CON-10: a connect that fails with a transient error ----------------------


def test_the_retry_delays_are_pis() -> None:
    assert CONNECT_RETRY_DELAYS_MS == (250, 1_000)


def test_retries_http_connections_that_fail_with_a_transient_error(
    fast_retries: None,
) -> None:
    # Pi: retries HTTP connections that fail with a transient error
    server = _server()
    harness = _Harness(
        [lambda: _failing(_always(_status(503))), lambda: server.client_end],
        config=_HTTP,
    )
    harness.connection.get_client()
    assert harness.connection.state == "connected"
    assert len(harness.opened) == 2
    # The retry is part of the one attempt: no state was announced between.
    assert "failed" not in harness.states
    harness.connection.close()

    failing = _Harness([lambda: _failing(_always(_status(400, ": bad")))], config=_HTTP)
    with pytest.raises(McpServerConnectionError, match="status 400: bad"):
        failing.connection.get_client()
    assert failing.connection.state == "failed"
    assert len(failing.opened) == 1
    failing.connection.close()


def test_a_connect_is_tried_three_times_with_the_two_delays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connection_module, "CONNECT_RETRY_DELAYS_MS", (60, 120))
    opened: list[float] = []

    def transport() -> McpTransport:
        opened.append(time.monotonic())
        return _failing(_always(_status(503)))

    harness = _Harness([transport] * 4, config=_HTTP)
    with pytest.raises(McpServerConnectionError, match="status 503"):
        harness.connection.get_client()
    assert harness.connection.state == "failed"
    assert harness.connection.error == "MCP HTTP request failed with status 503"
    assert len(opened) == 3
    assert opened[1] - opened[0] >= 0.06
    assert opened[2] - opened[1] >= 0.12
    harness.connection.close()


@pytest.mark.parametrize(
    ("error", "attempts"),
    [
        (_status(408), 3),
        (_status(429), 3),
        (_status(500), 3),
        (_status(502), 3),
        (_status(599), 3),
        (_status(501), 1),
        (_status(404), 1),
        (McpAuthRequiredError(None), 1),
        (ConnectionRefusedError("refused"), 3),
        (McpTimeoutError(5), 1),
        (McpConfigValueError("no value"), 1),
    ],
)
def test_only_network_errors_and_some_statuses_are_tried_again(
    fast_retries: None, error: Exception, attempts: int
) -> None:
    harness = _Harness([lambda: _failing(_always(error))] * 3, config=_HTTP)
    with pytest.raises(McpServerConnectionError):
        harness.connection.get_client()
    assert len(harness.opened) == attempts
    harness.connection.close()


def test_a_failure_while_the_tools_are_listed_is_tried_again(
    fast_retries: None,
) -> None:
    server = _server()
    harness = _Harness(
        [
            lambda: _failing(_once_per_method(_status(503), "tools/list")),
            lambda: server.client_end,
        ],
        config=_HTTP,
    )
    harness.connection.get_client()
    assert harness.connection.state == "connected"
    assert len(harness.opened) == 2
    harness.connection.close()


def test_stdio_connects_are_not_retried(fast_retries: None) -> None:
    harness = _Harness([lambda: _failing(_always(_status(503)))] * 3)
    with pytest.raises(McpServerConnectionError, match="status 503"):
        harness.connection.get_client()
    assert len(harness.opened) == 1
    harness.connection.close()


def test_stops_waiting_to_retry_a_connection_when_closed() -> None:
    # Pi: stops waiting to retry a connection when closed (#10249)
    harness = _Harness([lambda: _failing(_always(_status(503)))] * 3, config=_HTTP)
    failures: list[BaseException] = []

    def connect() -> None:
        try:
            harness.connection.get_client()
        except McpServerConnectionError as error:
            failures.append(error)

    caller = threading.Thread(target=connect)
    caller.start()
    # The first attempt failed; the retry is 250 ms away.
    _wait_until(lambda: len(harness.opened) == 1)
    time.sleep(0.01)
    started = time.monotonic()
    harness.connection.close()
    caller.join(_WAIT_SECONDS)
    assert time.monotonic() - started < 0.2
    assert "status 503" in str(failures[0])
    assert len(harness.opened) == 1
    assert harness.connection.state == "closed"
    assert harness.connection.error == "MCP HTTP request failed with status 503"


# -- CON-12: the session expired ---------------------------------------------


def _expired_calls(*methods: str) -> Failure:
    """Every request of ``methods`` is answered "session expired"."""

    return lambda message: (
        McpSessionExpiredError("gone") if message.get("method") in methods else None
    )


def test_starts_a_new_session_and_retries_once_when_the_session_expired() -> None:
    # Pi: starts a new session and retries once when the session expired
    first = _failing(_expired_calls("tools/call"))
    second = _server()
    harness = _Harness([lambda: first, lambda: second.client_end])
    results: list[object] = []

    def call() -> None:
        results.append(harness.connection.call_tool("echo", {}))

    callers = [threading.Thread(target=call) for _ in range(2)]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(_WAIT_SECONDS)
    assert results == [_OK, _OK]
    assert len(harness.opened) == 2
    assert harness.connection.state == "connected"
    # The client of the old session was closed with its last call.
    assert first.closes == 1
    harness.connection.close()


def test_a_session_that_expires_again_fails_the_call() -> None:
    expired = _expired_calls("tools/call")
    harness = _Harness([lambda: _failing(expired), lambda: _failing(expired)])
    with pytest.raises(McpSessionExpiredError):
        harness.connection.call_tool("echo", {})
    assert len(harness.opened) == 2
    # The second client stays the connection's; the next call starts again.
    assert harness.connection.state == "connected"
    harness.connection.close()


def test_resource_requests_start_a_new_session_too() -> None:
    second = _server()
    harness = _Harness(
        [
            lambda: _failing(_expired_calls("resources/read", "resources/list")),
            lambda: second.client_end,
        ]
    )
    assert harness.connection.read_resource("docs://a") == _READ
    assert len(harness.opened) == 2
    harness.connection.close()


def _tool_name(message: JsonRpcMessage) -> str:
    params = message.get("params")
    assert isinstance(params, dict)
    return str(params["name"])


def _expired_except(tool: str) -> Failure:
    """Every tool call but the one of ``tool`` finds the session expired."""

    return lambda message: (
        McpSessionExpiredError("gone")
        if message.get("method") == "tools/call" and _tool_name(message) != tool
        else None
    )


def test_the_old_session_stays_open_for_its_other_calls() -> None:
    received = threading.Event()
    answer = threading.Event()
    slow_result = {"content": [{"type": "text", "text": "slow"}]}

    def slow(message: JsonRpcMessage) -> object:
        received.set()
        assert answer.wait(_WAIT_SECONDS)
        return slow_result

    old = _server()
    old.handlers["tools/call"] = slow
    first = _Failing(old.client_end, _expired_except("slow"))
    second = _server()
    harness = _Harness([lambda: first, lambda: second.client_end])
    results: list[object] = []
    slow_call = threading.Thread(
        target=lambda: results.append(harness.connection.call_tool("slow", {}))
    )
    slow_call.start()
    assert received.wait(_WAIT_SECONDS)

    # This call finds the session expired and moves to a new one.
    assert harness.connection.call_tool("echo", {}) == _OK
    assert len(harness.opened) == 2
    assert first.closes == 0

    answer.set()
    slow_call.join(_WAIT_SECONDS)
    assert results == [slow_result]
    # The old session's last call ended, so its client was closed.
    assert first.closes == 1
    assert harness.connection.state == "connected"
    harness.connection.close()


def test_close_ends_an_expired_session_that_still_has_a_call() -> None:
    received = threading.Event()

    def never(message: JsonRpcMessage) -> object:
        received.set()
        return _NO_ANSWER

    old = _server()
    old.handlers["tools/call"] = never
    first = _Failing(old.client_end, _expired_except("hang"))
    second = _server()
    harness = _Harness([lambda: first, lambda: second.client_end])
    failures: list[BaseException] = []

    def hang() -> None:
        try:
            harness.connection.call_tool("hang", {})
        except McpConnectionClosedError as error:
            failures.append(error)

    hanging = threading.Thread(target=hang)
    hanging.start()
    assert received.wait(_WAIT_SECONDS)
    assert harness.connection.call_tool("echo", {}) == _OK
    assert first.closes == 0

    harness.connection.close()

    assert first.closes == 1
    hanging.join(_WAIT_SECONDS)
    assert len(failures) == 1


# -- CON-11: a resource request after a transient status ----------------------


def test_retries_resource_reads_but_not_tool_calls_after_a_transient_http_error(
    fast_retries: None,
) -> None:
    # Pi: retries resource reads, but not tool calls, after a transient HTTP error
    transport = _failing(_once_per_method(_status(502), "resources/read", "tools/call"))
    harness = _Harness([lambda: transport])
    assert harness.connection.read_resource("docs://a") == _READ
    with pytest.raises(McpHttpError, match="status 502"):
        harness.connection.call_tool("echo", {})
    assert len(harness.opened) == 1
    harness.connection.close()


@pytest.mark.parametrize(
    ("method", "ask"),
    [
        ("resources/read", lambda c: c.read_resource("docs://a")),
        ("resources/list", lambda c: c.resources_page()),
        ("resources/list", lambda c: c.all_resources()),
        ("resources/templates/list", lambda c: c.resource_templates_page()),
        ("resources/templates/list", lambda c: c.all_resource_templates()),
    ],
)
def test_every_resource_request_is_made_once_more(
    fast_retries: None,
    method: str,
    ask: Callable[[McpServerConnection], object],
) -> None:
    server = _server(capabilities={"tools": {}})
    failed: list[JsonRpcMessage] = []

    def fail(message: JsonRpcMessage) -> Exception | None:
        if message.get("method") != method or len(failed) == 2:
            return None
        failed.append(message)
        return _status(503)

    harness = _Harness([lambda: _Failing(server.client_end, fail)])
    with pytest.raises(McpHttpError, match="status 503"):
        ask(harness.connection)
    assert len(failed) == 2
    # The next call starts again, with a retry of its own.
    assert ask(harness.connection)
    harness.connection.close()


def test_a_resource_request_waits_before_it_is_made_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connection_module, "CONNECT_RETRY_DELAYS_MS", (80, 1_000))
    harness = _Harness(
        [lambda: _failing(_once_per_method(_status(429), "resources/read"))]
    )
    harness.connection.get_client()
    started = time.monotonic()
    assert harness.connection.read_resource("docs://a") == _READ
    assert time.monotonic() - started >= 0.08
    harness.connection.close()


@pytest.mark.parametrize("status", [400, 404, 501])
def test_a_resource_request_is_not_repeated_after_another_status(
    fast_retries: None, status: int
) -> None:
    sent: list[JsonRpcMessage] = []

    def fail(message: JsonRpcMessage) -> Exception | None:
        if message.get("method") != "resources/read":
            return None
        sent.append(message)
        return _status(status)

    harness = _Harness([lambda: _failing(fail)])
    with pytest.raises(McpHttpError, match=f"status {status}"):
        harness.connection.read_resource("docs://a")
    assert len(sent) == 1
    harness.connection.close()


def test_a_request_is_made_twice_at_most(fast_retries: None) -> None:
    # A transient status and then an expired session: no third request.
    answers = iter([_status(503), McpSessionExpiredError("gone")])

    def fail(message: JsonRpcMessage) -> Exception | None:
        if message.get("method") != "resources/read":
            return None
        return next(answers)

    harness = _Harness([lambda: _failing(fail)])
    with pytest.raises(McpSessionExpiredError):
        harness.connection.read_resource("docs://a")
    assert len(harness.opened) == 1
    harness.connection.close()


def test_a_cancelled_caller_stops_waiting_to_repeat_a_resource_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connection_module, "CONNECT_RETRY_DELAYS_MS", (30_000, 1))
    cancel = threading.Event()
    sent = threading.Event()

    def fail(message: JsonRpcMessage) -> Exception | None:
        if message.get("method") != "resources/read":
            return None
        sent.set()
        return _status(503)

    harness = _Harness([lambda: _failing(fail)])
    failures: list[BaseException] = []

    def read() -> None:
        try:
            harness.connection.read_resource(
                "docs://a", McpRequestOptions(cancel_event=cancel)
            )
        except McpAbortError as error:
            failures.append(error)

    reader = threading.Thread(target=read)
    reader.start()
    assert sent.wait(_WAIT_SECONDS)
    cancel.set()
    reader.join(_WAIT_SECONDS)
    assert len(failures) == 1
    harness.connection.close()


def test_close_ends_the_wait_before_a_resource_request_is_repeated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connection_module, "CONNECT_RETRY_DELAYS_MS", (30_000, 1))
    sent = threading.Event()

    def fail(message: JsonRpcMessage) -> Exception | None:
        if message.get("method") != "resources/read":
            return None
        sent.set()
        return _status(503)

    harness = _Harness([lambda: _failing(fail)])
    failures: list[BaseException] = []

    def read() -> None:
        try:
            harness.connection.read_resource("docs://a")
        except McpServerConnectionError as error:
            failures.append(error)

    reader = threading.Thread(target=read)
    reader.start()
    assert sent.wait(_WAIT_SECONDS)
    harness.connection.close()
    reader.join(_WAIT_SECONDS)
    assert [str(failure) for failure in failures] == ['MCP server "fake" is shut down']


# -- the default transport, against an HTTP server ----------------------------

_ECHO = {"name": "echo", "inputSchema": {"type": "object"}}
_INTRO = {"uri": "docs://intro", "name": "intro"}
_SCENARIO: dict[str, Any] = {
    "tools": [_ECHO],
    "capabilities": {"tools": {}, "resources": {}},
    "resources": [_INTRO],
    "resourceContents": {"docs://intro": [{"uri": "docs://intro", "text": "hello"}]},
}


def _entry(config: dict[str, Any]) -> McpServerEntry:
    return McpServerEntry(name="docs", config=config, source="test")


def _connect(
    server: FakeMcpHttpServer, tmp_path: Path, **config: Any
) -> McpServerConnection:
    return McpServerConnection(
        entry=_entry({"url": server.url, **config}),
        cwd=tmp_path,
        client_version="1.2.3",
        on_tools=lambda connection: None,
    )


def _http_threads(before: set[threading.Thread]) -> list[str]:
    return [
        thread.name
        for thread in threading.enumerate()
        if thread not in before and thread.name.startswith("mcp-http")
    ]


def _text(result: Any) -> str:
    return str(result["content"][0]["text"])


def test_the_default_transport_of_a_url_entry_is_streamable_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPY_TEST_MCP_TOKEN", "secret")
    provider = object()
    transport = create_default_transport(
        _entry(
            {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer ${PIPY_TEST_MCP_TOKEN}"},
            }
        ),
        str(tmp_path),
        provider,  # type: ignore[arg-type]
    )
    assert isinstance(transport, StreamableHttpTransport)
    assert transport.options.url == "https://example.com/mcp"
    assert transport.options.headers == {"Authorization": "Bearer secret"}
    assert transport.options.auth_provider is provider

    plain = create_default_transport(_entry({"url": "http://localhost:1/mcp"}), "/")
    assert isinstance(plain, StreamableHttpTransport)
    assert plain.options.headers is None
    assert plain.options.auth_provider is None


def test_a_header_that_cannot_be_resolved_fails_the_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFG-25: the server's connection fails, naming the server and the header.
    monkeypatch.delenv("PIPY_TEST_MCP_MISSING", raising=False)
    with FakeMcpHttpServer(_SCENARIO) as server:
        connection = _connect(
            server, tmp_path, headers={"X-Key": "${PIPY_TEST_MCP_MISSING}"}
        )
        with pytest.raises(McpServerConnectionError) as raised:
            connection.get_client()
        assert 'MCP server "docs" header "X-Key"' in str(raised.value)
        assert connection.state == "failed"
        assert server.requests == []
        connection.close()


def test_an_http_server_is_connected_called_and_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPY_TEST_MCP_TOKEN", "secret")
    before = set(threading.enumerate())
    with FakeMcpHttpServer({**_SCENARIO, "sessionId": "s1"}) as server:
        connection = _connect(
            server, tmp_path, headers={"X-Key": "${PIPY_TEST_MCP_TOKEN}"}
        )
        connection.get_client()
        view = connection.view()
        assert view.state == "connected"
        assert [tool["name"] for tool in view.tools] == ["echo"]
        assert view.resources == (_INTRO,)
        assert _text(connection.call_tool("echo", {"text": "hi"})) == "hi"
        assert connection.read_resource("docs://intro") == {
            "contents": [{"uri": "docs://intro", "text": "hello"}]
        }
        assert all(request.headers["x-key"] == "secret" for request in server.requests)

        connection.close()

        # The transport reported its close and ended the server's session.
        assert connection.state == "closed"
        assert [request.method for request in server.requests][-1] == "DELETE"
        _wait_until(lambda: _http_threads(before) == [])


def test_closing_ends_an_http_connect_the_server_has_not_answered(
    tmp_path: Path,
) -> None:
    before = set(threading.enumerate())
    scenario = {**_SCENARIO, "methods": {"initialize": [{"wait": "never"}]}}
    with FakeMcpHttpServer(scenario) as server:
        connection = _connect(server, tmp_path)
        failures: list[BaseException] = []

        def connect() -> None:
            try:
                connection.get_client()
            except McpServerConnectionError as error:
                failures.append(error)

        caller = threading.Thread(target=connect)
        caller.start()
        _wait_until(lambda: "wait:never" in server.events)
        started = time.monotonic()
        connection.close()
        caller.join(_WAIT_SECONDS)
        assert time.monotonic() - started < 5
        assert "failed to connect" in str(failures[0])
        assert connection.state == "closed"
        _wait_until(lambda: _http_threads(before) == [])


def test_an_http_connect_is_tried_again_after_a_503(
    tmp_path: Path, fast_retries: None
) -> None:
    unavailable = [{"status": 503, "body": "starting"}]
    scenario = {**_SCENARIO, "postSequence": {"initialize": [unavailable]}}
    with FakeMcpHttpServer(scenario) as server:
        connection = _connect(server, tmp_path)
        connection.get_client()
        assert connection.state == "connected"
        assert len(server.messages("initialize")) == 2
        connection.close()

    scenario = {**_SCENARIO, "postSequence": {"initialize": [unavailable] * 5}}
    with FakeMcpHttpServer(scenario) as server:
        connection = _connect(server, tmp_path)
        with pytest.raises(McpServerConnectionError, match="status 503: starting"):
            connection.get_client()
        assert connection.state == "failed"
        assert len(server.messages("initialize")) == 3
        connection.close()


def test_a_server_that_is_not_there_is_tried_three_times(
    tmp_path: Path, fast_retries: None
) -> None:
    with FakeMcpHttpServer(_SCENARIO) as server:
        url = server.url
    created: list[McpTransport] = []

    def create(entry: McpServerEntry, cwd: str, auth: Any) -> McpTransport:
        created.append(create_default_transport(entry, cwd, auth))
        return created[-1]

    connection = McpServerConnection(
        entry=_entry({"url": url}),
        cwd=tmp_path,
        client_version="1.2.3",
        on_tools=lambda connection: None,
        create_transport=create,
    )
    with pytest.raises(McpServerConnectionError, match="failed to connect"):
        connection.get_client()
    assert len(created) == 3
    connection.close()


def test_a_restarted_http_server_gets_a_new_session(tmp_path: Path) -> None:
    before = set(threading.enumerate())
    with FakeMcpHttpServer({**_SCENARIO, "sessions": True}) as server:
        connection = _connect(server, tmp_path)
        assert _text(connection.call_tool("echo", {"text": "one"})) == "one"
        first = server.messages("tools/call")[0].headers["mcp-session-id"]

        server.expire_sessions()

        assert _text(connection.call_tool("echo", {"text": "two"})) == "two"
        calls = server.messages("tools/call")
        assert [call.headers["mcp-session-id"] for call in calls] == [
            first,
            first,
            "session-2",
        ]
        assert len(server.messages("initialize")) == 2
        assert connection.state == "connected"
        # A resource read moves to a new session the same way.
        server.expire_sessions()
        assert connection.read_resource("docs://intro")["contents"]
        assert len(server.messages("initialize")) == 3
        connection.close()
        _wait_until(lambda: _http_threads(before) == [])


def test_an_http_resource_read_is_repeated_after_a_502_and_a_tool_call_is_not(
    tmp_path: Path, fast_retries: None
) -> None:
    bad_gateway = [{"status": 502}]
    scenario = {
        **_SCENARIO,
        "postSequence": {"resources/read": [bad_gateway], "tools/call": [bad_gateway]},
    }
    with FakeMcpHttpServer(scenario) as server:
        connection = _connect(server, tmp_path)
        assert connection.read_resource("docs://intro")["contents"]
        assert len(server.messages("resources/read")) == 2
        with pytest.raises(McpHttpError, match="status 502"):
            connection.call_tool("echo", {})
        assert len(server.messages("tools/call")) == 1
        assert len(server.messages("initialize")) == 1
        connection.close()
