"""Azure-owned construction and default SSE wire/composition evidence."""

from __future__ import annotations

import io
import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from test_native_responses_streaming import (
    _done,
    _Reply,
    _server,
    _start,
    _terminal,
)

from pipy_harness.adapters.native import CodingSessionAdapter
from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent.content import ThinkingContent
from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.automation.run_modes import run_json_mode
from pipy_harness.native.catalog import build_builtin_catalog
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.provider_construction import (
    build_provider,
    resolve_construction,
)
from pipy_harness.native.providers.azure_openai_responses import (
    AZURE_OPENAI_RESPONSES_API,
    AzureOpenAIResponsesProvider,
)
from pipy_harness.native.session_tree import NativeSessionTree

THINKING = {
    "type": "reasoning",
    "id": "rs_azure",
    "summary": [{"type": "summary_text", "text": "Azure plan"}],
}
ANSWER = {
    "type": "message",
    "id": "msg_azure",
    "phase": "final_answer",
    "content": [{"type": "output_text", "text": "Azure answer"}],
}
TOOL = {
    "type": "function_call",
    "id": "fc_azure",
    "call_id": "call_azure",
    "name": "read",
    "arguments": '{"path":"README.md"}',
}


@pytest.fixture(autouse=True)
def _isolated_azure_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AZURE_OPENAI_BASE_URL",
        "AZURE_OPENAI_RESOURCE_NAME",
        "AZURE_OPENAI_DEPLOYMENT_NAME_MAP",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))


def _azure_provider(
    tmp_path: Path,
    base: str,
    *,
    model: str = "gpt-5.4",
    name: str = "azure-openai",
    headers: dict[str, str] | None = None,
) -> AzureOpenAIResponsesProvider:
    source = build_builtin_catalog().find("azure-openai", model)
    assert source is not None
    spec = replace(source, provider_name=name, base_url=base, headers=headers)
    resolved = resolve_construction(
        spec,
        store=AuthStore(path=tmp_path / "auth.json"),
        env={},
        runtime_api_key="synthetic-azure-key",
        models_json_auth=None,
        thinking_level="medium",
    )
    assert resolved.ok, resolved.error
    provider = build_provider(resolved, spec=spec)
    assert (
        isinstance(provider, AzureOpenAIResponsesProvider)
        and provider.http_client is None
    )
    return provider


def _azure_request(
    tmp_path: Path, provider: AzureOpenAIResponsesProvider
) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="Azure synthetic system",
        user_prompt="Azure synthetic prompt",
        provider_name=provider.name,
        model_id=provider.model_id,
        cwd=tmp_path,
        session_id="azure-session",
        cache_retention="none",
    )


def _barrier_reply() -> _Reply:
    return _Reply(
        [
            {
                "type": "response.reasoning_summary_text.delta",
                "output_index": 0,
                "delta": "Azure plan",
            },
            {
                "type": "response.output_text.delta",
                "output_index": 1,
                "delta": "Azure answer",
            },
        ],
        [_done(THINKING), _done(ANSWER, 1), _terminal([THINKING, ANSWER])],
        block_terminal=True,
        block_eof=True,
    )


@pytest.mark.parametrize(
    ("model", "name", "instruction_role"),
    [
        ("gpt-5.4", "azure-openai", "developer"),
        ("gpt-4o", "azure-openai", "system"),
        ("gpt-4o-mini", "azure-openai", "system"),
        ("gpt-5.4", "custom-azure", "developer"),
    ],
)
def test_azure_default_catalog_wire_streams_before_terminal_and_eof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    name: str,
    instruction_role: str,
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2026-fixture")
    monkeypatch.setenv(
        "AZURE_OPENAI_DEPLOYMENT_NAME_MAP", f"{model}=deployment-fixture"
    )
    text, thinking = threading.Event(), threading.Event()
    chunks: list[tuple[str, str]] = []

    def on_text(chunk):
        chunks.append(("text", chunk))
        text.set()

    def on_thinking(chunk):
        chunks.append(("thinking", chunk))
        thinking.set()

    reply = _barrier_reply()
    with _server(reply) as (url, requests):
        provider = _azure_provider(tmp_path, url + "/azure/v1", model=model, name=name)
        worker, results, errors = _start(
            lambda: provider.complete(
                _azure_request(tmp_path, provider),
                stream_sink=on_text,
                reasoning_sink=on_thinking,
            )
        )
        try:
            assert text.wait(5) and thinking.wait(5)
            assert worker.is_alive() and not results and not errors
            assert not reply.release_terminal.is_set()
            reply.release_terminal.set()
            worker.join(5)
            assert not worker.is_alive() and not errors
            assert not reply.release_eof.is_set()
        finally:
            reply.release_terminal.set()
            reply.release_eof.set()
            worker.join(5)
    result = results[0]
    assert (
        result.status is HarnessStatus.SUCCEEDED
        and result.api == AZURE_OPENAI_RESPONSES_API
    )
    assert chunks == [("thinking", "Azure plan"), ("text", "Azure answer")]
    wire = requests[0]
    assert wire["path"] == "/azure/v1/responses?api-version=2026-fixture"
    headers = {key.lower(): value for key, value in wire["headers"].items()}
    assert (
        headers["api-key"] == "synthetic-azure-key" and "authorization" not in headers
    )
    body = wire["body"]
    assert body["model"] == "deployment-fixture"
    assert body["stream"] is True and body["store"] is False
    assert (
        body["prompt_cache_key"] == "azure-session"
    )  # Azure keeps it with retention:none
    assert body["input"] == [
        {"role": instruction_role, "content": "Azure synthetic system"},
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "Azure synthetic prompt"}],
        },
    ]
    if model == "gpt-5.4":
        assert body["reasoning"] == {"effort": "medium", "summary": "auto"}
        assert body["include"] == ["reasoning.encrypted_content"]
    else:
        assert "reasoning" not in body and "include" not in body


@pytest.mark.parametrize(
    "auth", [{"api-key": "explicit-azure"}, {"Authorization": "Bearer explicit-azure"}]
)
def test_azure_sse_explicit_auth_and_configuration_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, auth: dict[str, str]
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "v1-test")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_NAME_MAP", "gpt-5.4=mapped-deployment")
    with _server(_Reply([_done(ANSWER), _terminal([ANSWER])])) as (url, requests):
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "  " + url + "/chosen/v1/  ")
        provider = replace(
            _azure_provider(tmp_path, "https://unused.invalid", headers=auth),
            deployment="explicit-deployment",
            api_key=None,
            resource_name="unused-resource",
        )
        result = provider.complete(_azure_request(tmp_path, provider))
    assert result.status is HarnessStatus.SUCCEEDED
    assert requests[0]["path"] == "/chosen/v1/responses?api-version=v1-test"
    assert requests[0]["body"]["model"] == "explicit-deployment"
    headers = {key.lower(): value for key, value in requests[0]["headers"].items()}
    for key, value in auth.items():
        assert headers[key.lower()] == value
    if "Authorization" in auth:
        assert "api-key" not in headers


def test_azure_terminal_encryption_usage_tool_only_turn_and_stateless_replay(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("Azure fixture file")
    encrypted = {
        "type": "reasoning",
        "id": "rs_azure",
        "summary": [],
        "encrypted_content": "azure-opaque",
    }
    first = _Reply(
        [
            _done(THINKING),
            _done(TOOL, 1),
            _terminal(
                [encrypted, TOOL],
                usage={
                    "input_tokens": 12,
                    "output_tokens": 5,
                    "input_tokens_details": {
                        "cached_tokens": 3,
                        "cache_write_tokens": 2,
                    },
                    "output_tokens_details": {"reasoning_tokens": 4},
                    "total_tokens": 17,
                },
            ),
        ]
    )
    last = _Reply([_done(ANSWER), _terminal([ANSWER])])
    output = io.BytesIO()
    with _server(first, last) as (url, requests):
        run_json_mode(
            adapter=CodingSessionAdapter(provider=_azure_provider(tmp_path, url)),
            prompt="read README.md",
            cwd=tmp_path,
            native_session=NativeSessionTree.create(tmp_path, persist=False),
            stdout_buffer=output,
            error_stream=io.StringIO(),
        )
    assert len(requests) == 2
    replay = requests[1]["body"]["input"]
    reasoning = next(item for item in replay if item.get("type") == "reasoning")
    assert reasoning["encrypted_content"] == "azure-opaque"
    call = next(item for item in replay if item.get("type") == "function_call")
    result = next(item for item in replay if item.get("type") == "function_call_output")
    assert call["call_id"] == result["call_id"] == "call_azure"
    assert call["id"] == "fc_azure" and call["arguments"] == TOOL["arguments"]
    assert "Azure fixture file" in result["output"]
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [r["type"] for r in records].count("tool_execution_start") == 1
    assistants = [
        r["message"]
        for r in records
        if r["type"] == "message_end" and r["message"]["role"] == "assistant"
    ]
    assert assistants[0]["api"] == AZURE_OPENAI_RESPONSES_API
    assert [b["type"] for b in assistants[0]["content"]] == ["thinking", "toolCall"]
    assert assistants[0]["content"][0]["thinking"] == "Azure plan"
    assert assistants[0]["usage"]["input"] == 7
    assert (
        assistants[0]["usage"]["cacheRead"] == 3
        and assistants[0]["usage"]["cacheWrite"] == 2
    )
    assert (
        assistants[0]["usage"]["reasoning"] == 4
        and assistants[0]["usage"]["output"] == 5
    )
    final = assistants[-1]["content"][0]
    assert final["type"] == "text" and final["text"] == "Azure answer"
    assert json.loads(final["textSignature"]) == {
        "v": 1,
        "id": "msg_azure",
        "phase": "final_answer",
    }


@pytest.mark.parametrize("phase", ["headers", "body", "error_body"])
def test_azure_cancellation_closes_headers_sse_and_http_error_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError

    read_begun = threading.Event()
    closed = threading.Event()
    if phase == "error_body":
        from pipy_harness.native.providers.azure_openai_responses import (
            AzureOpenAIHTTPStatusError,
        )

        original = AzureOpenAIHTTPStatusError.from_http_error

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

        monkeypatch.setattr(AzureOpenAIHTTPStatusError, "from_http_error", normalize)
    reply = _Reply(
        [{"type": "response.reasoning_text.delta", "delta": "Azure partial"}],
        status=503 if phase == "error_body" else 200,
        error_body=b"{",
        error_content_length=1024 if phase == "error_body" else None,
        block_eof=phase == "error_body",
        block_headers=phase == "headers",
        block_terminal=True,
    )
    token, thinking = CancelToken(), threading.Event()
    with _server(reply) as (url, requests):
        provider = _azure_provider(tmp_path, url)
        worker, results, errors = _start(
            lambda: provider.complete(
                _azure_request(tmp_path, provider),
                cancel_token=token,
                reasoning_sink=lambda _: thinking.set(),
            )
        )
        try:
            assert reply.requested.wait(5)
            if phase == "body":
                assert thinking.wait(5)
            if phase == "error_body":
                assert read_begun.wait(5)
            token.cancel()
            worker.join(5)
            assert not worker.is_alive() and not results
            assert len(errors) == 1 and isinstance(errors[0], ProviderCancelledError)
            assert errors[0].partial is not None
            assert errors[0].partial.content_blocks == (
                (ThinkingContent("Azure partial"),) if phase == "body" else ()
            )
            assert len(requests) == 1 and not reply.release_eof.is_set()
            if phase == "error_body":
                assert closed.is_set()
        finally:
            reply.release_headers.set()
            reply.release_terminal.set()
            reply.release_eof.set()
            worker.join(5)


@pytest.mark.parametrize("channel", ["text", "thinking"])
def test_azure_local_output_failure_never_becomes_a_managed_provider_retry(
    tmp_path: Path, channel: str
) -> None:
    from pipy_harness.native.agent.provider_retry import ProviderManagedRetryPolicy
    from pipy_harness.native.agent.provider_turn import ProviderTurnExecutor

    failure = BrokenPipeError("synthetic Azure output failure")
    target = "AssistantTextDelta" if channel == "text" else "AssistantReasoningDelta"

    class FailingSink:
        def emit(self, event):
            if type(event).__name__ == target:
                raise failure

    event = {
        "type": "response.output_text.delta"
        if channel == "text"
        else "response.reasoning_text.delta",
        "delta": "Azure partial",
    }
    first, forbidden_retry = (
        _Reply([event], block_terminal=True),
        _Reply([event], block_terminal=True),
    )
    with _server(first, forbidden_retry) as (url, requests):
        provider = _azure_provider(tmp_path, url)
        with pytest.raises(BrokenPipeError) as caught:
            ProviderTurnExecutor(retry_sleep=lambda _: None).complete(
                provider,
                _azure_request(tmp_path, provider),
                FailingSink(),
                turn_index=0,
                retry_policy=ProviderManagedRetryPolicy(2, 0, 0),
                before_reissue=lambda: None,
            )
        assert caught.value is failure and len(requests) == 1
        assert not first.release_terminal.is_set()


def test_azure_callback_cancellation_keeps_partial_and_original_exception(
    tmp_path: Path,
) -> None:
    from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError

    token = CancelToken()
    raised: list[ProviderCancelledError] = []

    def cancel(chunk):
        token.cancel()
        try:
            token.raise_if_cancelled()
        except ProviderCancelledError as error:
            raised.append(error)
            raise

    reply = _Reply(
        [{"type": "response.reasoning_text.delta", "delta": "Azure partial"}],
        block_terminal=True,
    )
    with _server(reply) as (url, requests):
        provider = _azure_provider(tmp_path, url)
        with pytest.raises(ProviderCancelledError) as caught:
            provider.complete(
                _azure_request(tmp_path, provider),
                cancel_token=token,
                reasoning_sink=cancel,
            )
        assert caught.value is raised[0] and caught.value.partial is not None
        assert caught.value.partial.content_blocks == (
            ThinkingContent("Azure partial"),
        )
        assert len(requests) == 1 and not reply.release_terminal.is_set()


@pytest.mark.parametrize("after_partial", [False, True])
def test_azure_managed_retry_starts_fresh_request_before_or_after_delta(
    tmp_path: Path, after_partial: bool
) -> None:
    from pipy_harness.native.agent.provider_retry import ProviderManagedRetryPolicy
    from pipy_harness.native.agent.provider_turn import ProviderTurnExecutor

    first = _Reply(
        [{"type": "response.output_text.delta", "delta": "Azure discarded"}]
        if after_partial
        else []
    )
    last = _Reply([_done(ANSWER), _terminal([ANSWER])])

    class Sink:
        def __init__(self):
            self.events = []

        def emit(self, event):
            self.events.append(event)

    sink = Sink()
    with _server(first, last) as (url, requests):
        provider = _azure_provider(tmp_path, url)
        outcome = ProviderTurnExecutor(retry_sleep=lambda _: None).complete(
            provider,
            _azure_request(tmp_path, provider),
            sink,
            turn_index=0,
            retry_policy=ProviderManagedRetryPolicy(2, 0, 0),
            before_reissue=lambda: None,
        )
    assert (
        outcome.result is not None and outcome.result.status is HarnessStatus.SUCCEEDED
    )
    assert (
        outcome.result.api == AZURE_OPENAI_RESPONSES_API
        and outcome.result.final_text == "Azure answer"
    )
    assert len(requests) == 2 and requests[0]["body"] == requests[1]["body"]
    assert [type(event).__name__ for event in sink.events].count("RetryScheduled") == 1


@pytest.mark.parametrize(
    ("reply", "error_type", "received"),
    [
        (_Reply(status=503), "AzureOpenAIHTTPStatusError", False),
        (
            _Reply([{"type": "response.output_text.delta", "delta": "Azure partial"}]),
            "AzureOpenAITransportError",
            True,
        ),
        (
            _Reply(
                [
                    {
                        "type": "response.failed",
                        "response": {
                            "status": "failed",
                            "error": {
                                "code": "invalid_request_error",
                                "message": "private Azure sentinel",
                            },
                        },
                    }
                ]
            ),
            "AzureOpenAIResponseParseError",
            True,
        ),
    ],
)
def test_azure_errors_keep_azure_types_safe_metadata_and_no_internal_retry(
    tmp_path: Path, reply: _Reply, error_type: str, received: bool
) -> None:
    with _server(reply) as (url, requests):
        provider = _azure_provider(tmp_path, url)
        result = provider.complete(_azure_request(tmp_path, provider))
    assert result.status is HarnessStatus.FAILED and result.error_type == error_type
    assert result.api == AZURE_OPENAI_RESPONSES_API and not result.tool_calls
    assert (
        result.metadata is not None
        and result.metadata["provider_event_received"] is received
    )
    assert len(requests) == 1 and "private Azure sentinel" not in str(result)


@pytest.mark.parametrize("terminal_type", ["response.completed", "response.failed"])
def test_azure_incomplete_or_failed_tool_history_never_executes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal_type: str
) -> None:
    from pipy_harness.native.tools.read import ReadTool

    def forbidden(*args, **kwargs):
        raise AssertionError("Azure unfinished tools must not execute")

    monkeypatch.setattr(ReadTool, "invoke", forbidden)
    terminal = (
        _terminal()
        if terminal_type == "response.completed"
        else {
            "type": "response.failed",
            "response": {
                "status": "failed",
                "error": {"code": "invalid_request_error"},
            },
        }
    )
    reply = _Reply(
        [
            {"type": "response.output_text.delta", "delta": "Azure partial"},
            {
                "type": "response.output_item.added",
                "output_index": 1,
                "item": {**TOOL, "arguments": ""},
            },
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 1,
                "delta": '{"path":',
            },
        ],
        [terminal],
    )
    output = io.BytesIO()
    with _server(reply) as (url, requests):
        run_json_mode(
            adapter=CodingSessionAdapter(provider=_azure_provider(tmp_path, url)),
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
    assert [block["type"] for block in final["content"]] == ["text", "toolCall"]


def test_default_azure_sse_reaches_json_mode_before_terminal(tmp_path: Path) -> None:
    from test_native_responses_streaming import _ObservedJson

    output, reply = _ObservedJson(), _barrier_reply()
    with _server(reply) as (url, requests):
        worker, results, errors = _start(
            lambda: run_json_mode(
                adapter=CodingSessionAdapter(provider=_azure_provider(tmp_path, url)),
                prompt="Azure synthetic prompt",
                cwd=tmp_path,
                native_session=NativeSessionTree.create(tmp_path, persist=False),
                stdout_buffer=output,
                error_stream=io.StringIO(),
            )
        )
        try:
            assert output.text_seen.wait(5) and output.thinking_seen.wait(5)
            assert worker.is_alive() and not reply.release_terminal.is_set()
            reply.release_terminal.set()
            worker.join(5)
            assert not worker.is_alive() and not errors and results == [0]
            assert not reply.release_eof.is_set()
        finally:
            reply.release_terminal.set()
            reply.release_eof.set()
            worker.join(5)
    assert requests[0]["body"]["stream"] is True
    assert requests[0]["path"] == "/responses?api-version=v1"
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    final = next(
        r["message"]
        for r in records
        if r["type"] == "message_end" and r["message"]["role"] == "assistant"
    )
    assert final["api"] == AZURE_OPENAI_RESPONSES_API
    assert [b["text"] for b in final["content"] if b["type"] == "text"] == [
        "Azure answer"
    ]


def test_default_azure_sse_reaches_rpc_before_terminal(tmp_path: Path) -> None:
    from test_native_automation_rpc import _RpcClient

    reply = _barrier_reply()
    with _server(reply) as (url, requests):
        client = _RpcClient(tmp_path, provider=_azure_provider(tmp_path, url))
        try:
            client.send({"type": "prompt", "message": "Azure synthetic prompt"})
            text = client.wait_for(
                lambda r: r.get("assistantMessageEvent", {}).get("type") == "text_delta"
            )
            assert text["assistantMessageEvent"]["delta"] == "Azure answer"
            assert any(
                r.get("assistantMessageEvent", {}).get("type") == "thinking_delta"
                for r in client._seen
            )
            assert not reply.release_terminal.is_set()
            reply.release_terminal.set()
            client.wait_for(lambda r: r.get("type") == "agent_settled")
            assert len(requests) == 1 and requests[0]["body"]["stream"] is True
            assert requests[0]["path"] == "/responses?api-version=v1"
        finally:
            reply.release_terminal.set()
            reply.release_eof.set()
            client.close()


def test_default_azure_sse_reaches_real_pty_before_terminal_without_duplication(
    tmp_path: Path,
) -> None:
    import os
    import pty

    from pipy_harness.native.coding.session import CodingSession

    if os.name != "posix":
        pytest.skip("real PTY requires POSIX")
    reply = _barrier_reply()
    master, slave = pty.openpty()
    output = os.fdopen(slave, "w", buffering=1, encoding="utf-8")
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
            if b"Azure answer" in b"".join(chunks):
                seen.set()

    drainer = threading.Thread(target=drain, daemon=True)
    drainer.start()
    with _server(reply) as (url, requests):
        session = CodingSession(
            provider=_azure_provider(tmp_path, url),
            input_runtime="plain",
            native_session=NativeSessionTree.create(tmp_path, persist=False),
        )
        worker, results, errors = _start(
            lambda: session.run(
                workspace_root=tmp_path,
                input_stream=io.StringIO("Azure synthetic prompt\n"),
                output_stream=output,
                error_stream=io.StringIO(),
            )
        )
        try:
            assert seen.wait(5)
            assert worker.is_alive() and not reply.release_terminal.is_set()
        finally:
            reply.release_terminal.set()
            reply.release_eof.set()
            worker.join(5)
            output.close()
            drainer.join(5)
            os.close(master)
        assert not worker.is_alive() and not drainer.is_alive() and not errors
        assert results[0].status is HarnessStatus.SUCCEEDED
        assert requests[0]["body"]["stream"] is True
        assert requests[0]["path"] == "/responses?api-version=v1"
    assert b"".join(chunks).count(b"Azure answer") == 1


def test_explicit_azure_json_transport_remains_buffered_without_callbacks(
    tmp_path: Path,
) -> None:
    from test_provider_capability_evidence import _response_barrier

    from pipy_harness.native.providers.azure_openai_responses import (
        azure_openai_http_client,
    )

    body = json.dumps({"status": "completed", "output": [THINKING, ANSWER]}).encode()
    chunks: list[str] = []
    with _response_barrier(body[:-1], body[-1:], "application/json") as (
        url,
        sent,
        release,
    ):
        provider = AzureOpenAIResponsesProvider(
            model_id="gpt-5.4",
            api_key="synthetic-key",
            endpoint_url=url.removesuffix("/responses"),
            http_client=azure_openai_http_client(),
        )
        worker, results, errors = _start(
            lambda: provider.complete(
                _azure_request(tmp_path, provider),
                stream_sink=chunks.append,
                reasoning_sink=chunks.append,
            )
        )
        try:
            assert sent.wait(5)
            assert worker.is_alive() and not results and not errors and not chunks
        finally:
            release.set()
            worker.join(5)
        assert not worker.is_alive() and not errors
    assert results[0].status is HarnessStatus.SUCCEEDED and not chunks
    assert results[0].final_text == "Azure answer"
