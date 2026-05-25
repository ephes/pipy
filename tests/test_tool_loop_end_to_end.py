"""End-to-end tool-loop test against the OpenRouter transport.

This test proves the full loop closes: the loop sends messages and tool
declarations to OpenRouter, the (stubbed) provider returns a tool call,
the loop dispatches it through the production registry's `read` tool,
the loop sends the tool result back to the provider, and the provider
returns final text that lands on stdout. The HTTP transport is stubbed
so the test stays hermetic; no real network is required.
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.adapters import PipyNativeToolReplAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import RunRequest
from pipy_harness.native.openrouter_provider import (
    JsonResponse,
    OpenRouterChatCompletionsProvider,
)


class _ScriptedJsonHTTPClient:
    def __init__(self, responses: list[JsonResponse]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
    ) -> JsonResponse:
        self.requests.append({"url": url, "body": dict(body)})
        if not self.responses:
            raise RuntimeError("OpenRouter http stub exhausted")
        return self.responses.pop(0)


class _NullEventSink:
    def emit(self, event_type, *, summary, payload=None):
        return None


def test_openrouter_tool_loop_dispatches_read_and_returns_final_text(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("hello from notes\n", encoding="utf-8")

    first_turn = JsonResponse(
        status_code=200,
        body={
            "object": "chat.completion",
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_one",
                                "type": "function",
                                "function": {
                                    "name": "read",
                                    "arguments": json.dumps(
                                        {"path": "notes.txt"}
                                    ),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        },
    )
    final_turn = JsonResponse(
        status_code=200,
        body={
            "object": "chat.completion",
            "choices": [
                {
                    "message": {
                        "content": "the file says hello from notes",
                    },
                    "finish_reason": "stop",
                }
            ],
        },
    )
    client = _ScriptedJsonHTTPClient([first_turn, final_turn])

    provider = OpenRouterChatCompletionsProvider(
        model_id="openai/gpt-test",
        api_key="sk-or-test",
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

    second_body = client.requests[1]["body"]
    tool_message = next(
        message for message in second_body["messages"]
        if message["role"] == "tool"
    )
    assert tool_message["content"] == "hello from notes\n"
    assert tool_message["tool_call_id"] == "call_one"
