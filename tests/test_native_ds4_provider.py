from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from pipy_harness.cli import main
from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest
from pipy_harness.native._provider_helpers import JsonResponse
from pipy_harness.native.ds4_provider import (
    Ds4ChatCompletionsProvider,
    ds4_chat_completions_endpoint,
)
from pipy_harness.native.provider_registry import (
    DEFAULT_NATIVE_MODELS,
    DS4_DEFAULT_BASE_URL,
    DS4_DEFAULT_MODEL,
    NATIVE_PROVIDER_REGISTRY,
)
from pipy_harness.native.repl_state import (
    NativeModelSelection,
    NativeReplProviderState,
    default_selection_for,
)
from pipy_harness.native.tools.read import ReadTool
from pipy_session import verify_session_archive


class FakeJsonHTTPClient:
    def __init__(self, response: JsonResponse) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
    ) -> JsonResponse:
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers),
                "body": dict(body),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.response


def _provider_request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYSTEM_PROMPT_SHOULD_NOT_BE_ARCHIVED",
        user_prompt="Reply with exactly one sentence explaining what ds4 is.",
        provider_name="ds4",
        model_id=DS4_DEFAULT_MODEL,
        cwd=tmp_path,
    )


def test_ds4_registry_entry_defaults_and_advertises_tool_loop(tmp_path: Path):
    spec = NATIVE_PROVIDER_REGISTRY["ds4"]

    assert spec.default_model == DS4_DEFAULT_MODEL
    assert DEFAULT_NATIVE_MODELS["ds4"] == DS4_DEFAULT_MODEL
    assert spec.requires_model_for_run is False
    assert spec.supports_tool_calls is True
    assert spec.auto_default is False
    assert default_selection_for(native_provider="ds4", native_model=None) == (
        NativeModelSelection("ds4", DS4_DEFAULT_MODEL)
    )
    assert default_selection_for(native_provider="ds4", native_model="custom-ds4") == (
        NativeModelSelection("ds4", "custom-ds4")
    )

    state = NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        provider_factory=lambda selection: Ds4ChatCompletionsProvider(
            model_id=selection.model_id
        ),
        env={},
        openai_codex_auth_path=tmp_path / "missing-openai-codex.json",
        persist_defaults=False,
    )
    options = {option.selection.provider_name: option for option in state.model_options()}
    assert options["ds4"].available is True
    assert options["ds4"].selection.model_id == DS4_DEFAULT_MODEL


def test_ds4_provider_reuses_chat_completions_shape_without_required_auth(
    tmp_path: Path,
):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "ds4 is a local DeepSeek server.",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 7,
                    "total_tokens": 10,
                },
            },
        )
    )
    provider = Ds4ChatCompletionsProvider(
        model_id=DS4_DEFAULT_MODEL,
        base_url=DS4_DEFAULT_BASE_URL,
        api_key=None,
        http_client=client,
    )

    result = provider.complete(_provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.provider_name == "ds4"
    assert result.model_id == DS4_DEFAULT_MODEL
    assert result.final_text == "ds4 is a local DeepSeek server."
    assert result.usage == {
        "input_tokens": 3,
        "output_tokens": 7,
        "total_tokens": 10,
    }
    assert provider.supports_tool_calls is True

    posted = client.requests[0]
    assert posted["url"] == "http://127.0.0.1:8000/v1/chat/completions"
    assert "Authorization" not in posted["headers"]
    assert posted["body"] == {
        "model": DS4_DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": "SYSTEM_PROMPT_SHOULD_NOT_BE_ARCHIVED"},
            {
                "role": "user",
                "content": "Reply with exactly one sentence explaining what ds4 is.",
            },
        ],
        "stream": False,
    }


def test_ds4_provider_serializes_tool_definitions_when_available(
    tmp_path: Path,
):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_ds4_read",
                                    "type": "function",
                                    "function": {
                                        "name": "read",
                                        "arguments": '{"path":"notes.txt"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        )
    )
    provider = Ds4ChatCompletionsProvider(
        model_id=DS4_DEFAULT_MODEL,
        base_url=DS4_DEFAULT_BASE_URL,
        api_key=None,
        http_client=client,
    )
    read_tool = ReadTool()
    request = ProviderRequest(
        system_prompt="SYS",
        user_prompt="please read",
        provider_name="ds4",
        model_id=DS4_DEFAULT_MODEL,
        cwd=tmp_path,
        available_tools=(read_tool.definition,),
    )

    result = provider.complete(request)

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_name == "read"
    body = client.requests[0]["body"]
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["function"]["name"] == "read"


def test_ds4_base_url_override_accepts_base_or_endpoint():
    assert (
        ds4_chat_completions_endpoint("http://localhost:9000/v1")
        == "http://localhost:9000/v1/chat/completions"
    )
    assert (
        ds4_chat_completions_endpoint(
            "http://localhost:9000/v1/chat/completions"
        )
        == "http://localhost:9000/v1/chat/completions"
    )


def test_cli_ds4_product_path_against_hermetic_openai_compatible_server(
    tmp_path: Path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    captured: list[dict[str, Any]] = []
    server = _start_ds4_stub_server(captured)
    base_url = f"http://127.0.0.1:{server.server_port}/v1"
    monkeypatch.setenv("PIPY_DS4_BASE_URL", base_url)
    monkeypatch.delenv("PIPY_DS4_API_KEY", raising=False)

    try:
        exit_code = main(
            [
                "run",
                "--agent",
                "pipy-native",
                "--native-provider",
                "ds4",
                "--slug",
                "ds4-hermetic",
                "--root",
                str(root),
                "--cwd",
                str(tmp_path),
                "--goal",
                "Reply with exactly one sentence explaining what ds4 is.",
            ]
        )
    finally:
        server.shutdown()
        server.server_close()

    captured_output = capfd.readouterr()
    assert exit_code == 0
    assert captured_output.out == "DS4_STUB_FINAL_TEXT_SHOULD_NOT_ARCHIVE\n"
    assert captured
    assert captured[0]["path"] == "/v1/chat/completions"
    assert captured[0]["headers"]["content-type"] == "application/json"
    # `pipy run` now constructs ds4 through catalog construction (matching the
    # REPL), so the synthesized models.json placeholder apiKey ("local") is sent
    # as a Bearer token the same way Pi's OpenAI client would (the hermetic
    # server ignores it). The legacy ds4 adapter omitted the header.
    assert captured[0]["headers"]["authorization"] == "Bearer local"
    assert captured[0]["body"]["model"] == DS4_DEFAULT_MODEL
    assert captured[0]["body"]["stream"] is False

    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    events = [
        json.loads(line)
        for line in finalized[0].read_text(encoding="utf-8").splitlines()
    ]
    provider_payloads = [
        event["payload"]
        for event in events
        if event["type"] == "native.provider.completed"
    ]
    assert provider_payloads[0]["provider"] == "ds4"
    assert provider_payloads[0]["model_id"] == DS4_DEFAULT_MODEL
    assert provider_payloads[0]["provider_metadata"] == {
        "provider_response_store_requested": False,
        "response_object": "chat.completion",
        "finish_reason": "stop",
    }
    combined = (
        finalized[0].read_text(encoding="utf-8")
        + finalized[0].with_suffix(".md").read_text(encoding="utf-8")
    )
    assert "DS4_STUB_FINAL_TEXT_SHOULD_NOT_ARCHIVE" not in combined
    assert "SYSTEM_PROMPT_SHOULD_NOT_BE_ARCHIVED" not in combined
    assert "chatcmpl-ds4-provider-id-should-not-archive" not in combined
    assert verify_session_archive(root=root).ok is True


def _start_ds4_stub_server(
    captured: list[dict[str, Any]],
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            captured.append(
                {
                    "path": self.path,
                    "headers": {
                        key.lower(): value for key, value in self.headers.items()
                    },
                    "body": body,
                }
            )
            payload = {
                "id": "chatcmpl-ds4-provider-id-should-not-archive",
                "object": "chat.completion",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "DS4_STUB_FINAL_TEXT_SHOULD_NOT_ARCHIVE",
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
