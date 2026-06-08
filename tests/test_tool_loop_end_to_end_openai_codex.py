"""End-to-end tool-loop test against the OpenAI Codex Responses transport.

Parallel to `tests/test_tool_loop_end_to_end.py` (OpenRouter) and
`tests/test_tool_loop_end_to_end_openai.py` (OpenAI Platform Responses)
for the OpenAI Codex subscription Responses streaming endpoint: the
loop sends messages and tool declarations to Codex, the (stubbed)
SSE transport returns a streamed `function_call`, the loop dispatches
it through the production registry's `read` tool, the loop sends the
tool result back as a `function_call_output` item, and Codex returns
final text on stdout. The SSE transport is stubbed so the test stays
hermetic; no real network, OAuth, or subscription access is required.
"""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.adapters import PipyNativeToolReplAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import RunRequest
from pipy_harness.native.openai_codex_provider import (
    OpenAICodexAuthManager,
    OpenAICodexCredentials,
    OpenAICodexResponsesProvider,
    SseResponse,
)


class _ScriptedSseHTTPClient:
    def __init__(self, responses: list[SseResponse]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    def post_sse(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
        cancel_token: object = None,
    ) -> SseResponse:
        self.requests.append({"url": url, "body": dict(body)})
        if not self.responses:
            raise RuntimeError("OpenAI Codex sse stub exhausted")
        return self.responses.pop(0)


class _NullEventSink:
    def emit(self, event_type, *, summary, payload=None):
        return None


class _InMemoryCredentialStore:
    def __init__(self, credentials: OpenAICodexCredentials | None) -> None:
        self.credentials: OpenAICodexCredentials | None = credentials

    def load(self) -> OpenAICodexCredentials | None:
        return self.credentials

    def save(self, credentials: OpenAICodexCredentials) -> None:
        self.credentials = credentials

    def delete(self) -> bool:
        had = self.credentials is not None
        self.credentials = None
        return had


def _base64url(value: Mapping[str, Any]) -> str:
    return (
        base64.urlsafe_b64encode(json.dumps(value).encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )


def _fake_jwt() -> str:
    header = _base64url({"alg": "none"})
    payload = _base64url(
        {"https://api.openai.com/auth": {"chatgpt_account_id": "acct_test"}}
    )
    return f"{header}.{payload}.signature"


def _credentials() -> OpenAICodexCredentials:
    return OpenAICodexCredentials(
        access_token=_fake_jwt(),
        refresh_token="refresh-test",
        expires_at=4_102_444_800,
        account_id="acct_test",
    )


def _sse_payload(events: list[Mapping[str, Any]]) -> str:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events)


def test_openai_codex_tool_loop_dispatches_read_and_returns_final_text(
    tmp_path: Path,
):
    (tmp_path / "notes.txt").write_text("hello from notes\n", encoding="utf-8")

    first_turn = SseResponse(
        status_code=200,
        body=_sse_payload(
            [
                {
                    "type": "response.output_item.added",
                    "item": {
                        "type": "function_call",
                        "id": "fc_one",
                        "call_id": "call_one",
                        "name": "read",
                    },
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "fc_one",
                    "delta": '{"path":',
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "fc_one",
                    "delta": ' "notes.txt"}',
                },
                {
                    "type": "response.function_call_arguments.done",
                    "item_id": "fc_one",
                    "arguments": '{"path": "notes.txt"}',
                },
                {
                    "type": "response.completed",
                    "response": {"status": "completed"},
                },
            ]
        ),
    )
    final_turn = SseResponse(
        status_code=200,
        body=_sse_payload(
            [
                {
                    "type": "response.output_text.delta",
                    "delta": "the file says ",
                },
                {
                    "type": "response.output_text.delta",
                    "delta": "hello from notes",
                },
                {
                    "type": "response.completed",
                    "response": {"status": "completed"},
                },
            ]
        ),
    )
    client = _ScriptedSseHTTPClient([first_turn, final_turn])

    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=OpenAICodexAuthManager(
            store=_InMemoryCredentialStore(_credentials())
        ),
        http_client=client,
    )
    output_stream = io.StringIO()
    error_stream = io.StringIO()
    adapter = PipyNativeToolReplAdapter(
        provider=provider,
        input_stream=io.StringIO("please read notes.txt\n"),
        output_stream=output_stream,
        error_stream=error_stream,
    )
    prepared = adapter.prepare(
        RunRequest(
            agent="pipy-native",
            slug="loop-smoke",
            command=[],
            cwd=tmp_path,
            goal="loop smoke",
            capture_policy=CapturePolicy(),
        )
    )

    result = adapter.run(
        prepared, event_sink=_NullEventSink(), capture_policy=CapturePolicy()
    )

    assert result.exit_code == 0
    metadata = result.metadata or {}
    assert metadata["repl_mode"] == "tool-loop"
    assert metadata["tool_invocation_count"] == 1
    assert metadata["malformed_argument_count"] == 0
    assert "the file says hello from notes" in output_stream.getvalue()

    # First turn's request carries the user message and the read tool declaration.
    first_body = client.requests[0]["body"]
    assert isinstance(first_body["tools"], list)
    assert first_body["tools"][0]["type"] == "function"
    assert first_body["tools"][0]["name"] == "read"
    assert first_body["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "please read notes.txt"}
            ],
        }
    ]

    # Second turn's request must include the function_call and
    # function_call_output items for the dispatched read.
    second_body = client.requests[1]["body"]
    items = second_body["input"]
    function_call_item = next(
        item for item in items if item.get("type") == "function_call"
    )
    assert function_call_item["call_id"] == "call_one"
    assert function_call_item["name"] == "read"
    function_call_output_item = next(
        item for item in items if item.get("type") == "function_call_output"
    )
    assert function_call_output_item["call_id"] == "call_one"
    assert function_call_output_item["output"] == "hello from notes\n"
