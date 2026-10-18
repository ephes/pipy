"""Real local SSE evidence for ordinary catalog-selected Responses adapters."""

from __future__ import annotations

import errno
import io
import json
import os
import pty
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.adapters.native import CodingSessionAdapter
from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent.content import TextContent, ThinkingContent
from pipy_harness.native.agent.provider_retry import ProviderManagedRetryPolicy
from pipy_harness.native.agent.provider_turn import ProviderTurnExecutor
from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.automation.run_modes import run_json_mode
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native.catalog import build_builtin_catalog
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.provider_construction import (
    build_provider,
    resolve_construction,
)
from pipy_harness.native.providers.openai_responses import OpenAIResponsesProvider
from pipy_harness.native.session_tree import NativeSessionTree

REASONING = {
    "type": "reasoning",
    "id": "rs_1",
    "summary": [{"type": "summary_text", "text": "plan"}],
}
MESSAGE = {
    "type": "message",
    "id": "msg_1",
    "phase": "final_answer",
    "content": [{"type": "output_text", "text": "answer"}],
}
CALL = {
    "type": "function_call",
    "id": "fc_1",
    "call_id": "call_1",
    "name": "read",
    "arguments": '{"path":"README.md"}',
}


def _done(item: dict, index: int = 0) -> dict:
    return {"type": "response.output_item.done", "output_index": index, "item": item}


def _terminal(output: list[dict] | None = None, **response: Any) -> dict:
    body = {"status": "completed", **response}
    if output is not None:
        body["output"] = output
    return {"type": "response.completed", "response": body}


@dataclass
class _Reply:
    initial: list[dict] = field(default_factory=list)
    final: list[dict] = field(default_factory=list)
    status: int = 200
    error_body: bytes = b'{"error":{"type":"overloaded_error"}}'
    error_content_length: int | None = None
    block_terminal: bool = False
    block_eof: bool = False
    block_headers: bool = False
    requested: threading.Event = field(default_factory=threading.Event)
    sent: threading.Event = field(default_factory=threading.Event)
    terminal_sent: threading.Event = field(default_factory=threading.Event)
    release_headers: threading.Event = field(default_factory=threading.Event)
    release_terminal: threading.Event = field(default_factory=threading.Event)
    release_eof: threading.Event = field(default_factory=threading.Event)


def _wait(enabled: bool, release: threading.Event) -> None:
    if enabled:
        assert release.wait(10)


@contextmanager
def _server(*replies: _Reply) -> Iterator[tuple[str, list[dict]]]:
    requests: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            index = len(requests)
            requests.append(
                {
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": json.loads(
                        self.rfile.read(int(self.headers["Content-Length"]))
                    ),
                }
            )
            reply = replies[index]
            reply.requested.set()
            _wait(reply.block_headers, reply.release_headers)
            try:
                self.send_response(reply.status)
                self.send_header("Content-Type", "text/event-stream")
                if reply.error_content_length is not None:
                    self.send_header("Content-Length", str(reply.error_content_length))
                self.send_header("Connection", "close")
                self.end_headers()
                if reply.status != 200:
                    self.wfile.write(reply.error_body)
                    self.wfile.flush()
                    reply.sent.set()
                    _wait(reply.block_eof, reply.release_eof)
                    return
                self._events(reply.initial)
                reply.sent.set()
                _wait(reply.block_terminal, reply.release_terminal)
                self._events(reply.final)
                reply.terminal_sent.set()
                _wait(reply.block_eof, reply.release_eof)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _events(self, events: list[dict]) -> None:
            for event in events:
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02))
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        for reply in replies:
            reply.release_headers.set()
            reply.release_terminal.set()
            reply.release_eof.set()
        server.shutdown()
        server.server_close()
        worker.join(5)
        assert not worker.is_alive()


def _catalog_provider(
    tmp_path: Path, url: str, name: str = "openai"
) -> OpenAIResponsesProvider:
    rows = build_builtin_catalog().rows
    source = next(
        row
        for row in rows
        if row.provider_name == ("openai" if name == "custom" else name)
        and row.api == "openai-responses"
    )
    spec = replace(source, provider_name=name, base_url=url)
    resolved = resolve_construction(
        spec,
        store=AuthStore(path=tmp_path / "auth.json"),
        env={},
        runtime_api_key="sk-synthetic",
        models_json_auth=None,
        thinking_level="medium",
    )
    assert resolved.ok, resolved.error
    provider = build_provider(resolved, spec=spec)
    assert (
        isinstance(provider, OpenAIResponsesProvider) and provider.http_client is None
    )
    return provider


def _request(tmp_path: Path, provider: OpenAIResponsesProvider) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="Synthetic system",
        user_prompt="Synthetic prompt",
        provider_name=provider.name,
        model_id=provider.model_id,
        cwd=tmp_path,
        session_id="synthetic-session",
        cache_retention="long",
    )


def _start(
    callback: Callable[[], Any],
) -> tuple[threading.Thread, list[Any], list[BaseException]]:
    results: list[Any] = []
    errors: list[BaseException] = []

    def run():
        try:
            results.append(callback())
        except BaseException as exc:  # noqa: BLE001 - transfer worker failure to test thread
            errors.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker, results, errors


@pytest.mark.parametrize("name", ["openai", "xai", "github-copilot", "custom"])
def test_catalog_sse_callbacks_precede_terminal_and_keep_request_auth(
    tmp_path: Path, name: str
) -> None:
    initial: list[dict[str, Any]] = [
        {"type": "response.output_item.added", "output_index": 0, "item": REASONING},
        {
            "type": "response.reasoning_summary_text.delta",
            "output_index": 0,
            "delta": "plan",
        },
        {"type": "response.output_item.added", "output_index": 1, "item": MESSAGE},
        {"type": "response.output_text.delta", "output_index": 1, "delta": "answer"},
    ]
    reply = _Reply(
        initial,
        [_done(REASONING), _done(MESSAGE, 1), _terminal([REASONING, MESSAGE])],
        block_terminal=True,
        block_eof=True,
    )
    text, thinking = threading.Event(), threading.Event()
    chunks: list[tuple[str, str]] = []

    def on_text(chunk):
        chunks.append(("text", chunk))
        text.set()

    def on_thinking(chunk):
        chunks.append(("thinking", chunk))
        thinking.set()

    with _server(reply) as (url, requests):
        provider = _catalog_provider(tmp_path, url, name)
        worker, results, errors = _start(
            lambda: provider.complete(
                _request(tmp_path, provider),
                stream_sink=on_text,
                reasoning_sink=on_thinking,
            )
        )
        try:
            assert text.wait(5) and thinking.wait(5)
            assert worker.is_alive() and not reply.release_terminal.is_set()
            assert not results and not errors
            reply.release_terminal.set()
            worker.join(5)
            assert not worker.is_alive() and not errors
            assert not reply.release_eof.is_set()  # completed before EOF
        finally:
            reply.release_terminal.set()
            reply.release_eof.set()
            worker.join(5)
        assert results[0].status is HarnessStatus.SUCCEEDED
        assert results[0].final_text == "answer"
        assert chunks == [("thinking", "plan"), ("text", "answer")]
        body = requests[0]["body"]
        headers = {key.lower(): value for key, value in requests[0]["headers"].items()}
        assert requests[0]["path"] == "/responses"
        assert body["stream"] is True and body["store"] is False
        assert body["input"][-1]["content"] == [
            {"type": "input_text", "text": "Synthetic prompt"}
        ]
        assert body["prompt_cache_key"] == "synthetic-session"
        assert body["reasoning"]["effort"]
        assert headers["authorization"] == "Bearer sk-synthetic"
        if name == "github-copilot":
            assert headers["copilot-integration-id"] == "vscode-chat"
            assert headers["x-initiator"] == "user"


def test_terminal_content_usage_and_signatures_are_authoritative_without_duplicate_deltas(
    tmp_path: Path,
) -> None:
    encrypted = {**REASONING, "encrypted_content": "opaque"}
    final_message = {
        **MESSAGE,
        "content": [{"type": "output_text", "text": "answer final"}],
    }
    reply = _Reply(
        [
            {
                "type": "response.reasoning_summary_text.delta",
                "output_index": 0,
                "delta": "plan",
            },
            _done(REASONING),
            {
                "type": "response.output_text.delta",
                "output_index": 1,
                "delta": "answer",
            },
            _done(MESSAGE, 1),
            _done(CALL, 2),
            _terminal(
                [encrypted, final_message, CALL],
                usage={
                    "input_tokens": 10,
                    "output_tokens": 7,
                    "total_tokens": 17,
                    "input_tokens_details": {
                        "cached_tokens": 3,
                        "cache_write_tokens": 2,
                    },
                    "output_tokens_details": {"reasoning_tokens": 4},
                },
            ),
            {"type": "response.output_text.delta", "delta": "never"},
        ]
    )
    chunks: list[str] = []
    with _server(reply) as (url, _):
        provider = _catalog_provider(tmp_path, url)
        result = provider.complete(
            _request(tmp_path, provider), stream_sink=chunks.append
        )
    assert result.status is HarnessStatus.SUCCEEDED
    assert result.final_text == "answer final" and chunks == ["answer"]
    thinking, text, call = result.content_blocks
    assert isinstance(thinking, ThinkingContent) and thinking.signature is not None
    assert json.loads(thinking.signature)["encrypted_content"] == "opaque"
    assert isinstance(text, TextContent) and text.signature is not None
    assert json.loads(text.signature) == {
        "v": 1,
        "id": "msg_1",
        "phase": "final_answer",
    }
    assert call == result.tool_calls[0]
    assert (call.provider_correlation_id, call.arguments_json) == (
        "call_1|fc_1",
        CALL["arguments"],
    )
    assert result.usage == {
        "input_tokens": 10,
        "output_tokens": 7,
        "total_tokens": 17,
        "cached_tokens": 3,
        "cache_write_tokens": 2,
        "reasoning_tokens": 4,
    }


@pytest.mark.parametrize("finished", [True, False])
def test_tool_only_success_requires_item_done_even_after_arguments_done(
    tmp_path: Path, finished: bool
) -> None:
    events = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {**CALL, "arguments": ""},
        },
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": CALL["arguments"],
        },
        {
            "type": "response.function_call_arguments.done",
            "output_index": 0,
            "arguments": CALL["arguments"],
        },
    ]
    if finished:
        events.append(_done(CALL))
    events.append(_terminal([CALL]))
    with _server(_Reply(events)) as (url, _):
        provider = _catalog_provider(tmp_path, url)
        result = provider.complete(_request(tmp_path, provider))
    assert result.status is (
        HarnessStatus.SUCCEEDED if finished else HarnessStatus.FAILED
    )
    if finished:
        assert (
            result.final_text is None
            and result.tool_calls[0].arguments_json == CALL["arguments"]
        )
    else:
        assert (
            not result.tool_calls
        )  # partial blocks are history, never executable results
        assert result.error_message is not None
        assert result.content_blocks and "unfinished tool" in result.error_message


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_cancel_closes_blocked_stream_and_retains_partial_thinking(
    tmp_path: Path, phase: str
) -> None:
    reply = _Reply(
        [{"type": "response.reasoning_text.delta", "delta": "partial"}],
        block_headers=phase == "headers",
        block_terminal=True,
    )
    token, thinking = CancelToken(), threading.Event()
    with _server(reply) as (url, _):
        provider = _catalog_provider(tmp_path, url)
        worker, results, errors = _start(
            lambda: provider.complete(
                _request(tmp_path, provider),
                cancel_token=token,
                reasoning_sink=lambda _: thinking.set(),
            )
        )
        try:
            assert reply.requested.wait(5)
            if phase == "body":
                assert thinking.wait(5)
            token.cancel()
            worker.join(5)
            assert not worker.is_alive() and not results
            assert len(errors) == 1 and isinstance(errors[0], ProviderCancelledError)
            partial = errors[0].partial
            assert partial is not None
            assert partial.content_blocks == (
                (ThinkingContent("partial"),) if phase == "body" else ()
            )
        finally:
            reply.release_headers.set()
            reply.release_terminal.set()
            worker.join(5)


@pytest.mark.parametrize("after_delta", [False, True])
def test_managed_retry_starts_new_attempt_before_or_after_partial_output(
    tmp_path: Path, after_delta: bool
) -> None:
    failed = _Reply(
        [{"type": "response.output_text.delta", "delta": "discard"}]
        if after_delta
        else []
    )
    success = _Reply(
        [
            {"type": "response.output_text.delta", "delta": "answer"},
            _done(MESSAGE),
            _terminal([MESSAGE]),
        ]
    )

    class Sink:
        def __init__(self):
            self.events = []

        def emit(self, event):
            self.events.append(event)

    sink = Sink()
    with _server(failed, success) as (url, requests):
        provider = _catalog_provider(tmp_path, url)
        outcome = ProviderTurnExecutor(retry_sleep=lambda _: None).complete(
            provider,
            _request(tmp_path, provider),
            sink,
            turn_index=0,
            retry_policy=ProviderManagedRetryPolicy(2, 0, 0),
            before_reissue=lambda: None,
        )
    assert outcome.result is not None
    assert outcome.result.status is HarnessStatus.SUCCEEDED
    assert outcome.result.final_text == "answer"
    assert len(requests) == 2 and requests[0]["body"] == requests[1]["body"]
    names = [type(event).__name__ for event in sink.events]
    assert names.count("RetryScheduled") == names.count("RetryCompleted") == 1
    assert names.count("AssistantTextDelta") == (2 if after_delta else 1)


@pytest.fixture(autouse=True)
def _isolated_product_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _streaming_reply() -> _Reply:
    return _Reply(
        [
            {
                "type": "response.reasoning_text.delta",
                "output_index": 0,
                "delta": "plan",
            },
            {
                "type": "response.output_text.delta",
                "output_index": 1,
                "delta": "answer",
            },
        ],
        [_done(REASONING), _done(MESSAGE, 1), _terminal([REASONING, MESSAGE])],
        block_terminal=True,
    )


class _ObservedJson(io.BytesIO):
    def __init__(self) -> None:
        super().__init__()
        self.text_seen = threading.Event()
        self.thinking_seen = threading.Event()

    def write(self, data: Any) -> int:
        count = super().write(data)
        for line in self.getvalue().splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            kind = record.get("assistantMessageEvent", {}).get("type")
            if kind == "text_delta":
                self.text_seen.set()
            if kind == "thinking_delta":
                self.thinking_seen.set()
        return count


def test_catalog_sse_reaches_json_mode_before_terminal(tmp_path: Path) -> None:
    output = _ObservedJson()
    reply = _streaming_reply()
    with _server(reply) as (url, requests):
        provider = _catalog_provider(tmp_path, url)
        worker, results, errors = _start(
            lambda: run_json_mode(
                adapter=CodingSessionAdapter(provider=provider),
                prompt="Synthetic prompt",
                cwd=tmp_path,
                native_session=NativeSessionTree.create(tmp_path, persist=False),
                stdout_buffer=output,
                error_stream=io.StringIO(),
            )
        )
        try:
            assert output.text_seen.wait(5) and output.thinking_seen.wait(5)
            assert worker.is_alive() and not reply.release_terminal.is_set()
        finally:
            reply.release_terminal.set()
            worker.join(5)
        assert not worker.is_alive() and not errors and results == [0]
        assert requests[0]["body"]["stream"] is True
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    deltas = [
        r["assistantMessageEvent"] for r in records if r["type"] == "message_update"
    ]
    assert [d["delta"] for d in deltas if d["type"] == "text_delta"] == ["answer"]
    final = next(
        r["message"]
        for r in records
        if r["type"] == "message_end" and r["message"]["role"] == "assistant"
    )
    assert [block["text"] for block in final["content"] if block["type"] == "text"] == [
        "answer"
    ]


def test_catalog_sse_reaches_rpc_before_terminal(tmp_path: Path) -> None:
    from test_native_automation_rpc import _RpcClient

    reply = _streaming_reply()
    with _server(reply) as (url, requests):
        client = _RpcClient(tmp_path, provider=_catalog_provider(tmp_path, url))
        try:
            client.send({"type": "prompt", "message": "Synthetic prompt"})
            text = client.wait_for(
                lambda r: r.get("assistantMessageEvent", {}).get("type") == "text_delta"
            )
            assert text["assistantMessageEvent"]["delta"] == "answer"
            assert any(
                r.get("assistantMessageEvent", {}).get("type") == "thinking_delta"
                for r in client._seen
            )
            assert not reply.release_terminal.is_set()
            reply.release_terminal.set()
            client.wait_for(lambda r: r.get("type") == "agent_settled")
            assert requests[0]["body"]["stream"] is True
            assert len(requests) == 1
        finally:
            reply.release_terminal.set()
            client.close()


@pytest.mark.skipif(os.name != "posix", reason="real PTY requires POSIX")
def test_catalog_sse_reaches_real_pty_before_terminal_without_duplicate_final(
    tmp_path: Path,
) -> None:

    reply = _streaming_reply()
    master, slave = pty.openpty()
    output = os.fdopen(slave, "w", buffering=1, encoding="utf-8")
    # A drainer observing real bytes, not stream callbacks, releases the fixture.
    seen = threading.Event()
    chunks: list[bytes] = []

    def drain():
        while True:
            try:
                data = os.read(master, 4096)
            except OSError:
                return
            if not data:
                return
            chunks.append(data)
            if b"answer" in b"".join(chunks):
                seen.set()

    drainer = threading.Thread(target=drain, daemon=True)
    drainer.start()
    with _server(reply) as (url, requests):
        session = CodingSession(
            provider=_catalog_provider(tmp_path, url),
            input_runtime="plain",
            native_session=NativeSessionTree.create(tmp_path, persist=False),
        )
        worker, results, errors = _start(
            lambda: session.run(
                workspace_root=tmp_path,
                input_stream=io.StringIO("Synthetic prompt\n"),
                output_stream=output,
                error_stream=io.StringIO(),
            )
        )
        try:
            assert seen.wait(5)
            assert worker.is_alive() and not reply.release_terminal.is_set()
        finally:
            reply.release_terminal.set()
            worker.join(5)
            output.close()
            drainer.join(5)
            os.close(master)
        assert not worker.is_alive() and not drainer.is_alive() and not errors
        assert results[0].status is HarnessStatus.SUCCEEDED
        assert requests[0]["body"]["stream"] is True
    assert b"".join(chunks).count(b"answer") == 1


@pytest.mark.parametrize("failure", ["unfinished", "failed"])
def test_partial_tool_failure_is_kept_in_history_but_never_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from pipy_harness.native.tools.read import ReadTool

    def forbidden(*args, **kwargs):
        raise AssertionError("partial tools must never execute")

    monkeypatch.setattr(ReadTool, "invoke", forbidden)
    initial: list[dict[str, Any]] = [
        {"type": "response.output_text.delta", "delta": "partial answer"},
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {**CALL, "arguments": ""},
        },
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 1,
            "delta": '{"path":',
        },
    ]
    terminal: dict[str, Any] = (
        _terminal()
        if failure == "unfinished"
        else {
            "type": "response.failed",
            "response": {
                "status": "failed",
                "error": {"code": "invalid_request_error"},
            },
        }
    )
    output = io.BytesIO()
    with _server(_Reply(initial, [terminal])) as (url, requests):
        run_json_mode(
            adapter=CodingSessionAdapter(provider=_catalog_provider(tmp_path, url)),
            prompt="go",
            cwd=tmp_path,
            native_session=NativeSessionTree.create(tmp_path, persist=False),
            stdout_buffer=output,
            error_stream=io.StringIO(),
        )
    assert len(requests) == 1
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert not any(r["type"].startswith("tool_execution") for r in records)
    final = next(
        r["message"]
        for r in records
        if r["type"] == "message_end" and r["message"]["role"] == "assistant"
    )
    assert final["stopReason"] == "error"
    assert any(b.get("text") == "partial answer" for b in final["content"])
    assert any(b["type"] == "toolCall" for b in final["content"])


@pytest.mark.parametrize(
    ("events", "status", "received", "error_type"),
    [
        ([], 503, False, "OpenAIHTTPStatusError"),
        ([], 200, False, "OpenAITransportError"),
        (
            [{"type": "response.output_text.delta", "delta": "partial"}],
            200,
            True,
            "OpenAITransportError",
        ),
        ([{"unexpected": "private sentinel"}], 200, False, "OpenAIResponseParseError"),
        (
            [
                {
                    "type": "error",
                    "code": "invalid_request_error",
                    "message": "private sentinel",
                }
            ],
            200,
            True,
            "OpenAIResponseParseError",
        ),
        ([{"type": "response.completed"}], 200, True, "OpenAIResponseParseError"),
        (
            [{"type": "response.incomplete", "response": {"status": "incomplete"}}],
            200,
            True,
            "OpenAIResponseParseError",
        ),
    ],
)
def test_stream_failures_mark_event_progress_without_internal_replay_or_raw_error(
    tmp_path: Path, events: list[dict], status: int, received: bool, error_type: str
) -> None:
    with _server(_Reply(events, status=status)) as (url, requests):
        provider = _catalog_provider(tmp_path, url)
        result = provider.complete(_request(tmp_path, provider))
    assert len(requests) == 1 and result.status is HarnessStatus.FAILED
    assert result.error_type == error_type
    assert result.metadata is not None
    assert result.metadata["provider_event_received"] is received
    assert "private sentinel" not in str(result)
    assert not result.tool_calls
    if error_type == "OpenAITransportError":
        assert result.metadata["retryable"] is True


def test_terminal_error_keeps_special_usage_limit_guidance_without_metadata_prose(
    tmp_path: Path,
) -> None:
    reply = _Reply(
        [
            {
                "type": "response.failed",
                "response": {
                    "status": "failed",
                    "error": {
                        "code": "subscription_sharing_usage_limit_exceeded",
                        "message": "Synthetic shared usage limit",
                    },
                },
            }
        ]
    )
    with _server(reply) as (url, _):
        provider = _catalog_provider(tmp_path, url)
        result = provider.complete(_request(tmp_path, provider))
    assert result.status is HarnessStatus.FAILED
    assert result.error_message is not None and result.metadata is not None
    assert "https://chatgpt.com/settings/usage" in result.error_message
    assert "Synthetic shared usage limit" in result.error_message
    assert "api_error_message" not in result.metadata


def test_terminal_without_output_uses_completed_items(
    tmp_path: Path,
) -> None:
    reply = _Reply([_done(REASONING), _done(MESSAGE, 1), _terminal()])
    with _server(reply) as (url, _):
        provider = _catalog_provider(tmp_path, url)
        result = provider.complete(_request(tmp_path, provider))
    assert result.status is HarnessStatus.SUCCEEDED and result.final_text == "answer"
    assert result.content_blocks[0] == ThinkingContent(
        "plan", json.dumps(REASONING, separators=(",", ":"))
    )


def test_explicit_authorization_headers_survive_default_sse(tmp_path: Path) -> None:
    reply = _Reply([_done(MESSAGE), _terminal([MESSAGE])])
    with _server(reply) as (url, requests):
        provider = replace(
            _catalog_provider(tmp_path, url),
            api_key=None,
            extra_headers={
                "Authorization": "Bearer explicit-synthetic",
                "X-Model": "fixture",
            },
        )
        result = provider.complete(_request(tmp_path, provider))
    assert result.status is HarnessStatus.SUCCEEDED
    headers = {key.lower(): value for key, value in requests[0]["headers"].items()}
    assert headers["authorization"] == "Bearer explicit-synthetic"
    assert headers["x-model"] == "fixture"


def test_terminal_encryption_only_reasoning_keeps_completed_summary(
    tmp_path: Path,
) -> None:
    # Some Responses endpoints put encryption only on the terminal item; an
    # omitted/empty summary must not erase reasoning already finalized.
    encrypted = {
        "type": "reasoning",
        "id": "rs_1",
        "encrypted_content": "opaque",
        "summary": [],
    }
    reply = _Reply(
        [_done(REASONING), _done(MESSAGE, 1), _terminal([encrypted, MESSAGE])]
    )
    with _server(reply) as (url, _):
        provider = _catalog_provider(tmp_path, url)
        result = provider.complete(_request(tmp_path, provider))
    assert result.status is HarnessStatus.SUCCEEDED
    thinking = result.content_blocks[0]
    assert isinstance(thinking, ThinkingContent) and thinking.thinking == "plan"
    assert thinking.signature is not None
    assert json.loads(thinking.signature)["encrypted_content"] == "opaque"


@pytest.mark.parametrize("after_partial", [False, True])
def test_json_mode_managed_retry_settles_new_answer_after_fresh_attempt(
    tmp_path: Path, after_partial: bool
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.json").write_text(
        json.dumps({"retry": {"enabled": True, "maxRetries": 1, "baseDelayMs": 0}})
    )
    first = _Reply(
        [{"type": "response.output_text.delta", "delta": "discard"}]
        if after_partial
        else []
    )
    last = _Reply(
        [
            {"type": "response.output_text.delta", "delta": "answer"},
            _done(MESSAGE),
            _terminal([MESSAGE]),
        ]
    )
    output = io.BytesIO()
    with _server(first, last) as (url, requests):
        run_json_mode(
            adapter=CodingSessionAdapter(provider=_catalog_provider(tmp_path, url)),
            prompt="go",
            cwd=tmp_path,
            native_session=NativeSessionTree.create(tmp_path, persist=False),
            stdout_buffer=output,
            error_stream=io.StringIO(),
        )
    assert len(requests) == 2
    assert requests[0]["body"] == requests[1]["body"]
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [r["type"] for r in records].count("auto_retry_start") == 1
    assert [r["type"] for r in records].count("auto_retry_end") == 1
    assistants = [
        r["message"]
        for r in records
        if r["type"] == "message_end" and r["message"]["role"] == "assistant"
    ]
    final = assistants[-1]
    assert final["stopReason"] == "stop"
    assert [b["text"] for b in final["content"] if b["type"] == "text"] == ["answer"]


def test_rpc_abort_after_partial_text_never_executes_pending_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_native_automation_rpc import _RpcClient

    from pipy_harness.native.tools.read import ReadTool

    def forbidden(*args, **kwargs):
        raise AssertionError("cancelled historical tool must not execute")

    monkeypatch.setattr(ReadTool, "invoke", forbidden)
    reply = _Reply(
        [
            {"type": "response.output_text.delta", "delta": "partial"},
            {
                "type": "response.output_item.added",
                "output_index": 1,
                "item": {**CALL, "arguments": ""},
            },
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 1,
                "delta": '{"path":',
            },
        ],
        block_terminal=True,
    )
    with _server(reply) as (url, requests):
        client = _RpcClient(tmp_path, provider=_catalog_provider(tmp_path, url))
        try:
            client.send({"type": "prompt", "message": "go"})
            client.wait_for(
                lambda r: r.get("assistantMessageEvent", {}).get("type") == "text_delta"
            )
            client.send({"type": "abort"})
            client.wait_for(lambda r: r.get("type") == "agent_settled")
            assert len(requests) == 1 and not reply.release_terminal.is_set()
            assert not any(r["type"].startswith("tool_execution") for r in client._seen)
            assistants = [
                r["message"]
                for r in client._seen
                if r.get("type") == "message_end"
                and r["message"]["role"] == "assistant"
            ]
            assert assistants[-1]["stopReason"] == "aborted"
        finally:
            reply.release_terminal.set()
            client.close()


def test_catalog_sse_tool_only_turn_executes_once_and_replays_paired_result(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("Synthetic stored file")
    output = io.BytesIO()
    first = _Reply([_done(CALL), _terminal([CALL])])
    last = _Reply([_done(MESSAGE), _terminal([MESSAGE])])
    with _server(first, last) as (url, requests):
        run_json_mode(
            adapter=CodingSessionAdapter(provider=_catalog_provider(tmp_path, url)),
            prompt="read README.md",
            cwd=tmp_path,
            native_session=NativeSessionTree.create(tmp_path, persist=False),
            stdout_buffer=output,
            error_stream=io.StringIO(),
        )
    assert len(requests) == 2
    replayed = requests[1]["body"]["input"]
    call = next(item for item in replayed if item.get("type") == "function_call")
    result = next(
        item for item in replayed if item.get("type") == "function_call_output"
    )
    assert call["call_id"] == result["call_id"] == "call_1"
    assert call["id"] == "fc_1" and call["arguments"] == CALL["arguments"]
    assert "Synthetic stored file" in result["output"]
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [r["type"] for r in records].count("tool_execution_start") == 1
    assert [r["type"] for r in records].count("tool_execution_end") == 1


def test_malformed_terminal_output_cannot_override_partial_text_as_success(
    tmp_path: Path,
) -> None:
    reply = _Reply(
        [
            {"type": "response.output_text.delta", "delta": "partial"},
            {
                "type": "response.completed",
                "response": {"status": "completed", "output": "invalid"},
            },
        ]
    )
    with _server(reply) as (url, _):
        provider = _catalog_provider(tmp_path, url)
        result = provider.complete(_request(tmp_path, provider))
    assert result.status is HarnessStatus.FAILED
    assert result.content_blocks == (TextContent("partial"),)


def test_sink_failure_propagates_and_closes_without_waiting_for_terminal(
    tmp_path: Path,
) -> None:
    reply = _Reply(
        [{"type": "response.output_text.delta", "delta": "partial"}],
        block_terminal=True,
    )

    def fail(chunk):
        raise RuntimeError("synthetic sink failure")

    with _server(reply) as (url, requests):
        provider = _catalog_provider(tmp_path, url)
        worker, results, errors = _start(
            lambda: provider.complete(_request(tmp_path, provider), stream_sink=fail)
        )
        try:
            worker.join(5)
            assert not worker.is_alive() and not results
            assert len(errors) == 1 and str(errors[0]) == "synthetic sink failure"
            assert len(requests) == 1 and not reply.release_terminal.is_set()
        finally:
            reply.release_terminal.set()
            worker.join(5)


@pytest.mark.parametrize("channel", ["text", "thinking"])
@pytest.mark.parametrize("error_kind", ["broken_pipe", "os_error", "timeout"])
def test_canonical_sink_os_errors_preserve_identity_and_never_retry_provider(
    tmp_path: Path, channel: str, error_kind: str
) -> None:
    error = {
        "broken_pipe": BrokenPipeError("synthetic output pipe"),
        "os_error": OSError(errno.ECONNRESET, "synthetic observer reset"),
        "timeout": TimeoutError("synthetic observer timeout"),
    }[error_kind]
    expected_event = (
        "AssistantTextDelta" if channel == "text" else "AssistantReasoningDelta"
    )

    class FailingSink:
        def __init__(self):
            self.events = []

        def emit(self, event):
            self.events.append(event)
            if type(event).__name__ == expected_event:
                raise error

    event = {
        "type": "response.output_text.delta"
        if channel == "text"
        else "response.reasoning_text.delta",
        "delta": "partial",
    }
    first, forbidden_retry = (
        _Reply([event], block_terminal=True),
        _Reply([event], block_terminal=True),
    )
    sink = FailingSink()
    with _server(first, forbidden_retry) as (url, requests):
        provider = _catalog_provider(tmp_path, url)
        with pytest.raises(type(error)) as caught:
            ProviderTurnExecutor(retry_sleep=lambda _: None).complete(
                provider,
                _request(tmp_path, provider),
                sink,
                turn_index=0,
                retry_policy=ProviderManagedRetryPolicy(2, 0, 0),
                before_reissue=lambda: None,
            )
        assert caught.value is error
        assert len(requests) == 1
        assert not any(
            type(event).__name__ == "RetryScheduled" for event in sink.events
        )
        assert not first.release_terminal.is_set()  # failure closes before terminal/EOF


@pytest.mark.parametrize("content_length", [1024, None])
def test_cancel_during_http_error_body_read_wins_over_read_exception_or_eof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content_length: int | None
) -> None:
    from pipy_harness.native.providers.openai_responses import OpenAIHTTPStatusError

    read_begun, closed = threading.Event(), threading.Event()
    original = OpenAIHTTPStatusError.from_http_error

    def normalize(error):
        read, close = error.read, error.close

        def observed_read(*args, **kwargs):
            read_begun.set()
            return read(*args, **kwargs)

        def observed_close():
            closed.set()
            close()

        error.read, error.close = observed_read, observed_close
        return original(error)

    monkeypatch.setattr(OpenAIHTTPStatusError, "from_http_error", normalize)
    reply = _Reply(
        status=503, error_body=b"{", error_content_length=content_length, block_eof=True
    )
    token = CancelToken()
    with _server(reply) as (url, requests):
        provider = _catalog_provider(tmp_path, url)
        worker, results, errors = _start(
            lambda: provider.complete(_request(tmp_path, provider), cancel_token=token)
        )
        try:
            assert reply.sent.wait(5) and read_begun.wait(5)
            assert not reply.release_eof.is_set()
            token.cancel()
            worker.join(5)
            assert not worker.is_alive() and not results
            assert len(errors) == 1 and isinstance(errors[0], ProviderCancelledError)
            assert (
                errors[0].partial is not None and errors[0].partial.content_blocks == ()
            )
            assert closed.is_set() and len(requests) == 1
            assert not reply.release_eof.is_set()
        finally:
            reply.release_eof.set()
            worker.join(5)


def test_uncancelled_unrelated_http_error_normalizer_failure_propagates_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipy_harness.native.providers.openai_responses import OpenAIHTTPStatusError

    failure = ValueError("synthetic normalizer failure")
    closed = threading.Event()

    def normalize(error):
        original_close = error.close

        def close():
            closed.set()
            original_close()

        error.close = close
        raise failure

    monkeypatch.setattr(OpenAIHTTPStatusError, "from_http_error", normalize)
    with _server(_Reply(status=503)) as (url, _):
        provider = _catalog_provider(tmp_path, url)
        with pytest.raises(ValueError) as caught:
            provider.complete(_request(tmp_path, provider), cancel_token=CancelToken())
    assert caught.value is failure and closed.is_set()


@pytest.mark.parametrize("channel", ["text", "thinking"])
def test_callback_token_cancellation_retains_partial_blocks_and_exception_identity(
    tmp_path: Path, channel: str
) -> None:
    token = CancelToken()
    raised: list[ProviderCancelledError] = []

    def cancel(chunk):
        token.cancel()
        try:
            token.raise_if_cancelled()
        except ProviderCancelledError as error:
            raised.append(error)
            raise

    event_type = (
        "response.output_text.delta"
        if channel == "text"
        else "response.reasoning_text.delta"
    )
    reply = _Reply([{"type": event_type, "delta": "partial"}], block_terminal=True)
    with _server(reply) as (url, requests):
        provider = _catalog_provider(tmp_path, url)
        with pytest.raises(ProviderCancelledError) as caught:
            provider.complete(
                _request(tmp_path, provider),
                cancel_token=token,
                stream_sink=cancel if channel == "text" else None,
                reasoning_sink=cancel if channel == "thinking" else None,
            )
        assert caught.value is raised[0]
        assert caught.value.partial is not None
        expected = (
            TextContent("partial") if channel == "text" else ThinkingContent("partial")
        )
        assert caught.value.partial.content_blocks == (expected,)
        assert len(requests) == 1 and not reply.release_terminal.is_set()
