"""Archive non-leak test for the `bash` tool on the real product path.

Drives ``PipyNativeToolReplAdapter`` (the path ``pipy repl --agent pipy-native
--repl-mode tool-loop`` uses) with a stubbed OpenRouter transport so a model
turn emits a ``bash`` tool call. The command output is allowed to reach the
provider (it is model-visible), but the metadata-first archive — the event
sink payloads and the result metadata — must contain neither the raw command
string nor the command output body.
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

_OUTPUT_TOKEN = "SUPERSECRETPAYLOAD123"
_COMMAND = "cat notes.txt"


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


class _CapturingEventSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, *, summary: Any, payload: Any = None) -> None:
        self.events.append({"type": event_type, "summary": summary, "payload": payload})


def test_bash_tool_loop_archive_has_no_command_or_output(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text(_OUTPUT_TOKEN + "\n", encoding="utf-8")

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
                                "id": "call_bash",
                                "type": "function",
                                "function": {
                                    "name": "bash",
                                    "arguments": json.dumps({"command": _COMMAND}),
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
                    "message": {"content": "done inspecting"},
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
    sink = _CapturingEventSink()
    adapter = PipyNativeToolReplAdapter(
        provider=provider,
        input_stream=io.StringIO("please cat notes.txt\n"),
        output_stream=output_stream,
        error_stream=error_stream,
    )
    prepared = adapter.prepare(
        RunRequest(
            agent="pipy-native",
            slug="bash-archive",
            command=[],
            cwd=tmp_path,
            goal="bash archive",
            capture_policy=CapturePolicy(),
        )
    )
    result = adapter.run(prepared, event_sink=sink, capture_policy=CapturePolicy())

    assert result.exit_code == 0
    metadata = result.metadata or {}
    assert metadata["tool_invocation_count"] == 1

    # The output IS model-visible: it reached the provider's second request.
    provider_blob = json.dumps(client.requests, default=str)
    assert _OUTPUT_TOKEN in provider_blob

    # The metadata-first archive must contain neither the raw command nor the
    # output body — only safe counters/labels.
    archive_blob = json.dumps(sink.events, default=str)
    assert _OUTPUT_TOKEN not in archive_blob
    assert _COMMAND not in archive_blob
    metadata_blob = json.dumps(metadata, default=str)
    assert _OUTPUT_TOKEN not in metadata_blob
    assert _COMMAND not in metadata_blob
