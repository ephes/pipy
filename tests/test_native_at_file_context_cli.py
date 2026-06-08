"""Product-path integration tests for user-directed ``@file`` context.

These drive the real CLI (`pipy repl --agent pipy-native`) in both the no-tool
and tool-loop modes, feeding a genuine user prompt that names workspace files
with ``@path``. They assert the bounded excerpts reach the provider request/
context and that no file content leaks into the metadata-first archive.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from collections.abc import Mapping

from pipy_harness.adapters import PipyNativeToolReplAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.cli import main
from pipy_harness.models import HarnessStatus, RunRequest
from pipy_harness.native import ProviderRequest, ProviderResult
from pipy_harness.native.tools import UserMessage


class _NullEventSink:
    def emit(
        self,
        event_type: str,
        *,
        summary: str,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        return None


@pytest.fixture(autouse=True)
def isolate_native_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "PIPY_NATIVE_DEFAULTS_PATH", str(tmp_path / "native-defaults.json")
    )
    monkeypatch.setenv("PIPY_AUTH_DIR", str(tmp_path / "isolated-auth"))
    for env_name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "MISTRAL_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_KEY",
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
        "PIPY_READ_ROOTS",
    ):
        monkeypatch.delenv(env_name, raising=False)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]


def test_no_tool_repl_loads_at_file_context_into_provider_request(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "sessions"
    (tmp_path / "notes.txt").write_text(
        "ALPHA_FILE_LINE\nBETA_FILE_LINE\n", encoding="utf-8"
    )
    captured_requests: list[ProviderRequest] = []

    class CapturingProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(
            self, request: ProviderRequest, **_kwargs: object
        ) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="ANSWER",
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CapturingProvider)
    monkeypatch.setattr(
        sys, "stdin", StringIO("summarize @notes.txt for me\n/exit\n")
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--repl-mode",
            "no-tool",
            "--slug",
            "native-at-file",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert len(captured_requests) == 1
    user_prompt = captured_requests[0].user_prompt
    # The user's literal text is preserved.
    assert "summarize @notes.txt for me" in user_prompt
    # The bounded excerpt content reached the provider request.
    assert "ALPHA_FILE_LINE" in user_prompt
    assert "BETA_FILE_LINE" in user_prompt

    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    combined = finalized[0].read_text(encoding="utf-8") + finalized[
        0
    ].with_suffix(".md").read_text(encoding="utf-8")
    # No file contents or raw prompt body leak into the archive.
    assert "ALPHA_FILE_LINE" not in combined
    assert "BETA_FILE_LINE" not in combined
    assert "summarize @notes.txt for me" not in combined
    # Safe counters are recorded.
    events = read_jsonl(finalized[0])
    completed = [
        event["payload"]
        for event in events
        if event["type"] == "native.session.completed"
    ][0]
    assert completed["file_reference_loaded_count"] == 1


def test_tool_loop_repl_loads_at_file_context_into_provider_messages(
    tmp_path,
) -> None:
    (tmp_path / "notes.txt").write_text(
        "GAMMA_FILE_LINE\nDELTA_FILE_LINE\n", encoding="utf-8"
    )

    class CapturingToolProvider:
        name = "fake"
        supports_tool_calls = True

        def __init__(self) -> None:
            self.model_id = "fake-native-bootstrap"
            self.captured: list[ProviderRequest] = []

        def complete(
            self,
            request: ProviderRequest,
            *,
            stream_sink: object = None,
            reasoning_sink: object = None,
            cancel_token: object = None,
        ) -> ProviderResult:
            self.captured.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="DONE",
                tool_calls=(),
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            )

    provider = CapturingToolProvider()
    adapter = PipyNativeToolReplAdapter(
        provider=provider,
        input_stream=StringIO("explain @notes.txt please\n"),
        output_stream=StringIO(),
        error_stream=StringIO(),
        tool_budget=3,
    )
    prepared = adapter.prepare(
        RunRequest(
            agent="pipy-native",
            slug="test",
            command=[],
            cwd=tmp_path,
            goal="t",
            capture_policy=CapturePolicy(),
        )
    )

    result = adapter.run(
        prepared, event_sink=_NullEventSink(), capture_policy=CapturePolicy()
    )

    assert result.exit_code == 0
    assert len(provider.captured) == 1
    request = provider.captured[0]
    # The user message envelope carries the literal prompt and the excerpt.
    user_messages = [
        message.content
        for message in request.messages
        if isinstance(message, UserMessage)
    ]
    combined = "\n".join(user_messages)
    assert "explain @notes.txt please" in combined
    assert "GAMMA_FILE_LINE" in combined
    assert "DELTA_FILE_LINE" in combined
    metadata = result.metadata or {}
    assert metadata["file_reference_loaded_count"] == 1


def test_tool_loop_repl_image_attachment_counter_reaches_adapter_metadata(
    tmp_path,
) -> None:
    """The tool-loop adapter forwards safe image-attachment counters.

    Proves the safe counters cross the archive boundary as documented: a
    ``@image:`` attachment is reflected in ``AdapterResult.metadata`` (which the
    harness records), while no raw image bytes appear in that metadata.
    """

    import base64

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 48
    (tmp_path / "shot.png").write_bytes(png)

    class CapturingToolProvider:
        name = "fake"
        supports_tool_calls = True

        def __init__(self) -> None:
            self.model_id = "fake-native-bootstrap"
            self.captured: list[ProviderRequest] = []

        def complete(
            self,
            request: ProviderRequest,
            *,
            stream_sink: object = None,
            reasoning_sink: object = None,
            cancel_token: object = None,
        ) -> ProviderResult:
            self.captured.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="DONE",
                tool_calls=(),
            )

    provider = CapturingToolProvider()
    adapter = PipyNativeToolReplAdapter(
        provider=provider,
        input_stream=StringIO("describe @image:shot.png\n"),
        output_stream=StringIO(),
        error_stream=StringIO(),
        tool_budget=3,
    )
    prepared = adapter.prepare(
        RunRequest(
            agent="pipy-native",
            slug="test",
            command=[],
            cwd=tmp_path,
            goal="t",
            capture_policy=CapturePolicy(),
        )
    )
    result = adapter.run(
        prepared, event_sink=_NullEventSink(), capture_policy=CapturePolicy()
    )

    assert result.exit_code == 0
    assert provider.captured and len(provider.captured[0].attachments) == 1
    metadata = result.metadata or {}
    assert metadata["image_attachment_loaded_count"] == 1
    assert metadata["image_attachment_count"] == 1
    assert metadata["image_attachment_failed_count"] == 0
    # Safe counters only — never the raw image payload.
    assert base64.b64encode(png).decode("ascii") not in json.dumps(metadata)
