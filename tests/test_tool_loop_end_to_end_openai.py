"""End-to-end tool-loop test against the OpenAI Responses transport.

Parallel to `tests/test_tool_loop_end_to_end.py` for OpenRouter: the
loop sends messages and tool declarations to OpenAI Responses, the
(stubbed) provider returns a `function_call` output, the loop dispatches
it through the production registry's `read` tool, the loop sends the
tool result back as a `function_call_output` item, and the provider
returns final text that lands on stdout. The JSON HTTP transport is
stubbed so the test stays hermetic; no real network is required.
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
from pipy_harness.native.openai_provider import (
    JsonResponse,
    OpenAIResponsesProvider,
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
            raise RuntimeError("OpenAI Responses http stub exhausted")
        return self.responses.pop(0)


class _NullEventSink:
    def emit(self, event_type, *, summary, payload=None):
        return None


def test_openai_tool_loop_dispatches_read_and_returns_final_text(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("hello from notes\n", encoding="utf-8")

    first_turn = JsonResponse(
        status_code=200,
        body={
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "id": "fc_internal",
                    "call_id": "call_one",
                    "name": "read",
                    "arguments": json.dumps({"path": "notes.txt"}),
                }
            ],
        },
    )
    final_turn = JsonResponse(
        status_code=200,
        body={
            "status": "completed",
            "output_text": "the file says hello from notes",
        },
    )
    client = _ScriptedJsonHTTPClient([first_turn, final_turn])

    provider = OpenAIResponsesProvider(
        model_id="gpt-test",
        api_key="sk-test",
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

    # Second turn's request must include the function_call and the
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
