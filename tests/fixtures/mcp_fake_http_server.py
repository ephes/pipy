"""The scripted MCP server of ``mcp_fake_server.py``, over streamable HTTP.

Standard library only, and no code shared with ``pipy_harness``. Import it
with ``tests/fixtures`` on ``sys.path``; it runs in the test's own process:

    with FakeMcpHttpServer(scenario) as server:
        ... server.url, server.requests, server.events, server.release(name)

Every POST carries one JSON-RPC message and is answered by a scenario engine
of its own, on the HTTP server's thread for that connection, so a ``sleep``
or ``wait`` step holds only its own request. Without scripted steps a request
gets the built-in answer as one JSON body and a notification or a response
gets status 202. GET is answered 405 and DELETE 200. Every response closes
its connection.

Scenario keys besides those of ``mcp_fake_server.py`` (``record``,
``startup``, ``onStdinClose`` and ``onSigterm`` are not used):

- ``sessionId``: sent as ``Mcp-Session-Id`` with the answer to ``initialize``.
- ``httpMode``: ``"json"`` (default) or ``"sse"``, the form of an answer.
- ``requireBearer``: a token; a POST without it is answered 401 with
  ``WWW-Authenticate: Bearer``.
- ``toolSteps``: ``{<tool name>: [steps]}``, run for ``tools/call`` of that
  tool instead of ``methods["tools/call"]``.
- ``paths``: ``{<request path>: [steps]}``, run for a POST to that path
  instead of everything else.
- ``get``, ``delete``: steps answering a GET or a DELETE.

Steps besides those of ``mcp_fake_server.py`` (``stdout``, ``stderr``,
``spawn`` and ``exit`` are refused):

- ``{"mode": "json" | "sse"}``: the form of this answer. In JSON mode the
  messages of the steps are sent together when the steps end, as one object
  or as an array; in SSE mode each is sent at once as one event.
- ``{"status": code, "headers": {...}, "body": text}``: a complete response.
  With ``"contentLength": n`` it announces that length, whatever the body's.
- ``{"sse": text}``: ``text`` as is on the event stream.
- ``{"wait": name}``: block until the test calls ``release(name)``.
  ``events`` gets ``"wait:<name>"`` first.
- ``{"hold": true}``: keep the response as it is, unfinished, until the
  server is closed.
- ``{"drop": true}``: cut the connection without finishing the response.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from mcp_fake_server import Recorder, ScenarioEngine, StdioChannel


@dataclass(frozen=True)
class RecordedRequest:
    """One received HTTP request. Header names are lower case."""

    method: str
    path: str
    headers: dict[str, str]
    message: dict[str, Any] | None
    t: float


class _EventRecorder(Recorder):
    """Keeps the names of ``record`` steps in memory."""

    def __init__(self, events: list[str], lock: threading.Lock) -> None:
        super().__init__(None)
        self._events = events
        self._lock = lock

    def write(self, **fields: Any) -> None:
        if "event" in fields:
            with self._lock:
                self._events.append(fields["event"])


class _HttpChannel(StdioChannel):
    """The response to one HTTP request."""

    def __init__(self, handler: _Handler, mode: str, session_id: str | None) -> None:
        self.handler = handler
        self.mode = mode
        self.session_id = session_id
        self.messages: list[dict[str, Any]] = []
        self.streaming = False
        self.finished = False

    def send(self, message: dict[str, Any]) -> None:
        if self.mode == "sse":
            self.sse(f"data: {json.dumps(message, separators=(',', ':'))}\n\n")
        else:
            self.messages.append(message)

    def stdout(self, text: str) -> None:
        raise ValueError("The HTTP server has no stdout")

    def stderr(self, text: str) -> None:
        raise ValueError("The HTTP server has no stderr")

    def respond(
        self,
        status: int,
        headers: dict[str, str],
        body: bytes,
        content_length: int | None = None,
    ) -> None:
        handler = self.handler
        handler.send_response(status)
        for name, value in headers.items():
            handler.send_header(name, value)
        if content_length is None:
            content_length = len(body)
        handler.send_header("Content-Length", str(content_length))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(body)
        handler.wfile.flush()
        self.finished = True

    def sse(self, text: str) -> None:
        handler = self.handler
        if not self.streaming:
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Transfer-Encoding", "chunked")
            handler.send_header("Connection", "close")
            if self.session_id is not None:
                handler.send_header("Mcp-Session-Id", self.session_id)
            handler.end_headers()
            self.streaming = True
        data = text.encode("utf-8")
        if data:
            handler.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
        handler.wfile.flush()

    def drop(self) -> None:
        self.handler.wfile.flush()
        self.handler.connection.shutdown(socket.SHUT_RDWR)
        self.finished = True

    def finish(self, default_status: int) -> None:
        """End the response in the form the steps left it in."""

        if self.finished:
            return
        if self.streaming:
            self.handler.wfile.write(b"0\r\n\r\n")
            self.handler.wfile.flush()
        elif self.messages:
            body = self.messages[0] if len(self.messages) == 1 else self.messages
            headers = {"Content-Type": "application/json"}
            if self.session_id is not None:
                headers["Mcp-Session-Id"] = self.session_id
            self.respond(200, headers, json.dumps(body).encode("utf-8"))
        else:
            self.respond(default_status, {}, b"")
        self.finished = True


class _HttpScenarioEngine(ScenarioEngine):
    def __init__(
        self, server: FakeMcpHttpServer, channel: _HttpChannel, recorder: Recorder
    ) -> None:
        super().__init__(server.scenario, channel, recorder)
        self.server = server
        self.http = channel
        for refused in ("stdout", "stderr", "spawn", "exit"):
            del self._steps[refused]
        self._steps.update(
            {
                "mode": self._step_mode,
                "status": self._step_status,
                "sse": self._step_sse,
                "wait": self._step_wait,
                "hold": self._step_hold,
                "drop": self._step_drop,
            }
        )

    def receive(self, message: dict[str, Any]) -> None:
        if message.get("method") == "tools/call":
            name = (message.get("params") or {}).get("name")
            steps = self.scenario.get("toolSteps", {}).get(name)
            if steps is not None:
                self.run(steps, message)
                return
        super().receive(message)

    def _step_mode(self, step: dict[str, Any], request: Any) -> None:
        self.http.mode = step["mode"]

    def _step_status(self, step: dict[str, Any], request: Any) -> None:
        self.http.respond(
            step["status"],
            step.get("headers", {}),
            step.get("body", "").encode(),
            step.get("contentLength"),
        )

    def _step_sse(self, step: dict[str, Any], request: Any) -> None:
        self.http.sse(step["sse"])

    def _step_wait(self, step: dict[str, Any], request: Any) -> None:
        self.recorder.write(event=f"wait:{step['wait']}")
        gate = self.server.gate(step["wait"])
        while not gate.wait(0.05) and not self.server.stopped.is_set():
            pass

    def _step_hold(self, step: dict[str, Any], request: Any) -> None:
        self.server.stopped.wait()
        self.http.finished = True

    def _step_drop(self, step: dict[str, Any], request: Any) -> None:
        self.http.drop()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: _Server

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802 - the name BaseHTTPRequestHandler calls
        fake = self.server.fake
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        message = json.loads(raw)
        self._record(message)
        scenario = fake.scenario
        is_initialize = message.get("method") == "initialize"
        channel = _HttpChannel(
            self,
            scenario.get("httpMode", "json"),
            scenario.get("sessionId") if is_initialize else None,
        )
        engine = _HttpScenarioEngine(fake, channel, fake.recorder)
        bearer = scenario.get("requireBearer")

        def answer() -> None:
            steps = scenario.get("paths", {}).get(self.path)
            if bearer is not None and (
                self.headers.get("Authorization") != f"Bearer {bearer}"
            ):
                channel.respond(401, {"WWW-Authenticate": "Bearer"}, b"login required")
            elif steps is not None:
                engine.run(steps, message)
            else:
                engine.receive(message)

        self._answer(channel, answer, 202)

    def do_GET(self) -> None:  # noqa: N802 - the name BaseHTTPRequestHandler calls
        self._answer_steps("get", 405)

    def do_DELETE(self) -> None:  # noqa: N802 - the name BaseHTTPRequestHandler calls
        self._answer_steps("delete", 200)

    def _answer_steps(self, key: str, default_status: int) -> None:
        fake = self.server.fake
        self._record(None)
        channel = _HttpChannel(self, "sse", None)
        engine = _HttpScenarioEngine(fake, channel, fake.recorder)
        self._answer(
            channel, lambda: engine.run(fake.scenario.get(key, [])), default_status
        )

    def _answer(self, channel: _HttpChannel, answer: Any, default_status: int) -> None:
        self.close_connection = True
        try:
            answer()
            channel.finish(default_status)
        except OSError:
            pass  # The client went away, which is what some tests are about.

    def _record(self, message: dict[str, Any] | None) -> None:
        fake = self.server.fake
        with fake.lock:
            fake._requests.append(
                RecordedRequest(
                    method=self.command,
                    path=self.path,
                    headers={
                        name.lower(): value for name, value in self.headers.items()
                    },
                    message=message,
                    t=time.monotonic(),
                )
            )


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    fake: FakeMcpHttpServer


class FakeMcpHttpServer:
    """One scripted MCP server on ``127.0.0.1``, started by the constructor."""

    def __init__(
        self, scenario: dict[str, Any] | None = None, path: str = "/mcp"
    ) -> None:
        self.scenario = scenario or {}
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self._requests: list[RecordedRequest] = []
        self._events: list[str] = []
        self._gates: dict[str, threading.Event] = {}
        self.recorder = _EventRecorder(self._events, self.lock)
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.fake = self
        self.origin = f"http://127.0.0.1:{self._server.server_address[1]}"
        self.url = self.origin + path
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.01},
            name="fake-mcp-http-server",
            daemon=True,
        )
        self._thread.start()

    def __enter__(self) -> FakeMcpHttpServer:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def requests(self) -> list[RecordedRequest]:
        """Every request received so far, in order."""

        with self.lock:
            return list(self._requests)

    @property
    def events(self) -> list[str]:
        """The names of the ``record`` and ``wait`` steps run so far."""

        with self.lock:
            return list(self._events)

    def messages(self, method: str) -> list[RecordedRequest]:
        """The POSTs whose message has this JSON-RPC method."""

        return [
            request
            for request in self.requests
            if request.message is not None and request.message.get("method") == method
        ]

    def gate(self, name: str) -> threading.Event:
        with self.lock:
            return self._gates.setdefault(name, threading.Event())

    def release(self, name: str) -> None:
        """Let every ``wait`` step of this name continue, now and later."""

        self.gate(name).set()

    def close(self) -> None:
        """Stop serving and end every held or waiting response."""

        self.stopped.set()
        with self.lock:
            gates = list(self._gates.values())
        for gate in gates:
            gate.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()
