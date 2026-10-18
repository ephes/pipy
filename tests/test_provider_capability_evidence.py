"""Transport evidence: callbacks must precede terminal data, not just EOF parsing."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from websockets.sync.server import ServerConnection, serve

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent.content import TextContent, ThinkingContent
from pipy_harness.native.catalog import build_builtin_catalog
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.openai_codex_provider import (
    OpenAICodexAuthManager,
    OpenAICodexCredentials,
    OpenAICodexResponsesProvider,
)
from pipy_harness.native.provider import ProviderPort, StreamChunkSink
from pipy_harness.native.providers.openai_responses import (
    OpenAIResponsesProvider,
    openai_http_client,
)


@dataclass
class _MemoryCredentials:
    credentials: OpenAICodexCredentials

    def load(self) -> OpenAICodexCredentials:
        return self.credentials

    def save(self, credentials: OpenAICodexCredentials) -> None:
        self.credentials = credentials

    def delete(self) -> bool:
        raise AssertionError("Transport evidence must not change authentication")


@contextmanager
def _response_barrier(
    initial: bytes, final: bytes, content_type: str
) -> Iterator[tuple[str, threading.Event, threading.Event]]:
    sent = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(initial)
            self.wfile.flush()
            sent.set()
            if release.wait(timeout=10):
                self.wfile.write(final)
                self.wfile.flush()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05))
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/responses", sent, release
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


def _start_completion(
    provider: ProviderPort,
    request: ProviderRequest,
    text: StreamChunkSink,
    thinking: StreamChunkSink,
) -> tuple[threading.Thread, list[ProviderResult], list[BaseException]]:
    results: list[ProviderResult] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(
                provider.complete(request, stream_sink=text, reasoning_sink=thinking)
            )
        except BaseException as exc:  # noqa: BLE001 - propagate worker failures to test thread
            errors.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker, results, errors


@contextmanager
def _websocket_barrier(
    initial: list[dict[str, Any]], final: list[dict[str, Any]]
) -> Iterator[tuple[str, threading.Event, threading.Event]]:
    sent = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def handle(connection: ServerConnection) -> None:
        try:
            connection.recv(timeout=5)  # Synthetic response.create request.
            for event in initial:
                connection.send(json.dumps(event))
            sent.set()
            if release.wait(timeout=10):
                for event in final:
                    connection.send(json.dumps(event))
        finally:
            finished.set()

    with serve(handle, "127.0.0.1", 0, close_timeout=1) as server:
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            yield (
                f"ws://127.0.0.1:{server.socket.getsockname()[1]}/responses",
                sent,
                release,
            )
        finally:
            release.set()
            server.shutdown()
            worker.join(timeout=5)
            assert not worker.is_alive()
            if sent.is_set():
                assert finished.wait(timeout=5)


def _request(tmp_path: Path, provider: str) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="Synthetic transport evidence.",
        user_prompt="Respond with the fixture.",
        provider_name=provider,
        model_id="fixture-model",
        cwd=tmp_path,
    )


def _sse(*events: dict[str, Any]) -> bytes:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


@pytest.mark.parametrize("transport", ["sse", "websocket"])
def test_codex_event_transports_text_and_thinking_arrive_before_terminal_and_eof(
    tmp_path: Path, transport: str
) -> None:
    initial: list[dict[str, Any]] = [
        {"type": "response.reasoning_summary_text.delta", "delta": "plan"},
        {"type": "response.output_text.delta", "delta": "answer"},
        {
            "type": "response.output_item.added",
            "output_index": 2,
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "read",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_1",
            "delta": '{"path":',
        },
    ]
    final: list[dict[str, Any]] = [
        {
            "type": "response.function_call_arguments.done",
            "item_id": "fc_1",
            "arguments": '{"path":"README.md"}',
        },
        {"type": "response.completed", "response": {"status": "completed"}},
    ]
    text_seen = threading.Event()
    thinking_seen = threading.Event()
    actions: list[tuple[str, str]] = []

    def text(chunk: str) -> None:
        actions.append(("text", chunk))
        text_seen.set()

    def thinking(chunk: str) -> None:
        actions.append(("thinking", chunk))
        thinking_seen.set()

    barrier = (
        _response_barrier(_sse(*initial), _sse(*final), "text/event-stream")
        if transport == "sse"
        else _websocket_barrier(initial, final)
    )
    with barrier as (url, sent, release):
        provider = OpenAICodexResponsesProvider(
            model_id="fixture-model",
            endpoint=url,
            websocket_endpoint=url,
            transport=transport,
            auth_manager=OpenAICodexAuthManager(
                store=_MemoryCredentials(
                    OpenAICodexCredentials(
                        "synthetic-token", "unused", 4_102_444_800, "fixture-account"
                    )
                )
            ),
        )
        worker, results, errors = _start_completion(
            provider, _request(tmp_path, "openai-codex"), text, thinking
        )
        try:
            assert sent.wait(timeout=5)
            assert thinking_seen.wait(timeout=5), errors
            assert text_seen.wait(timeout=5), errors
            assert not release.is_set()
            assert worker.is_alive() and not results and not errors
            assert actions == [("thinking", "plan"), ("text", "answer")]
        finally:
            release.set()
            worker.join(timeout=5)
        assert not worker.is_alive() and not errors
        (result,) = results
        assert result.status is HarnessStatus.SUCCEEDED
        assert result.final_text == "answer"
        assert result.content_blocks[:2] == (
            ThinkingContent("plan"),
            TextContent("answer"),
        )
        (call,) = result.tool_calls
        assert (call.tool_name, call.arguments_json, call.provider_correlation_id) == (
            "read",
            '{"path":"README.md"}',
            "call_1|fc_1",
        )


def test_explicit_json_responses_buffers_body_and_returns_final_content_without_deltas(
    tmp_path: Path,
) -> None:
    body = json.dumps(
        {
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "plan"}],
                },
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "answer"}],
                },
                {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "read",
                    "arguments": '{"path":"README.md"}',
                },
            ],
        }
    ).encode()
    actions: list[tuple[str, str]] = []
    with _response_barrier(body[:-1], body[-1:], "application/json") as (
        url,
        sent,
        release,
    ):
        provider = OpenAIResponsesProvider(
            model_id="fixture-model",
            api_key="synthetic-key",
            endpoint=url,
            http_client=openai_http_client(),
        )
        worker, results, errors = _start_completion(
            provider,
            _request(tmp_path, "openai"),
            lambda chunk: actions.append(("text", chunk)),
            lambda chunk: actions.append(("thinking", chunk)),
        )
        try:
            assert sent.wait(timeout=5)
            assert not release.is_set()
            assert worker.is_alive() and not results and not errors
            assert actions == []
        finally:
            release.set()
            worker.join(timeout=5)
        assert not worker.is_alive() and not errors
        (result,) = results
        assert result.status is HarnessStatus.SUCCEEDED
        assert result.final_text == "answer"
        thinking_block, text_block = result.content_blocks[:2]
        assert isinstance(thinking_block, ThinkingContent)
        assert thinking_block.thinking == "plan"
        assert text_block == TextContent("answer")
        (call,) = result.tool_calls
        assert call.arguments_json == '{"path":"README.md"}'
        assert actions == []


def test_capability_matrix_covers_every_builtin_api_family() -> None:
    doc = (
        Path(__file__).resolve().parents[1] / "docs/provider-capabilities.md"
    ).read_text()
    documented = re.findall(r"^\| `([^`]+)` \|", doc, flags=re.MULTILINE)
    assert len(documented) == len(set(documented)), "One current row per API family"
    assert set(documented) == {row.api for row in build_builtin_catalog().rows}
