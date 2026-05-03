from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.adapters.native import PipyNativeAdapter
from pipy_harness.models import HarnessStatus, RunRequest
from pipy_harness.native import NativeRunInput, ProviderRequest, ProviderResult
from pipy_harness.native.session import NativeAgentSession, SYSTEM_PROMPT_ID, SYSTEM_PROMPT_VERSION
from pipy_harness.runner import HarnessRunner
from pipy_session import verify_session_archive


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, object] | None]] = []

    def emit(
        self,
        event_type: str,
        *,
        summary: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        self.events.append((event_type, summary, payload))


@dataclass(slots=True)
class CapturingProvider:
    final_text: str = "MODEL_OUTPUT_SHOULD_PRINT_ONLY"
    status: HarnessStatus = HarnessStatus.SUCCEEDED
    captured_request: ProviderRequest | None = None

    @property
    def name(self) -> str:
        return "capturing-fake"

    @property
    def model_id(self) -> str:
        return "capturing-model"

    def complete(self, request: ProviderRequest) -> ProviderResult:
        self.captured_request = request
        now = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)
        return ProviderResult(
            status=self.status,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text=self.final_text if self.status == HarnessStatus.SUCCEEDED else None,
        )


class ExplodingProvider:
    name = "exploding-fake"
    model_id = "exploding-model"

    def complete(self, request: ProviderRequest) -> ProviderResult:
        time.sleep(0.001)
        raise RuntimeError("provider exploded")


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_native_session_builds_prompt_calls_provider_and_emits_safe_events(tmp_path):
    provider = CapturingProvider()
    sink = RecordingSink()
    output = NativeAgentSession(provider=provider).run(
        NativeRunInput(
            goal="SAFE_GOAL_METADATA",
            cwd=tmp_path,
            provider_name=provider.name,
            model_id=provider.model_id,
            system_prompt_id=SYSTEM_PROMPT_ID,
            system_prompt_version=SYSTEM_PROMPT_VERSION,
        ),
        sink,
    )

    assert output.status == HarnessStatus.SUCCEEDED
    assert output.exit_code == 0
    assert output.final_text == "MODEL_OUTPUT_SHOULD_PRINT_ONLY"
    assert provider.captured_request is not None
    assert provider.captured_request.system_prompt
    assert provider.captured_request.user_prompt == "SAFE_GOAL_METADATA"
    assert [event[0] for event in sink.events] == [
        "native.session.started",
        "native.provider.started",
        "native.provider.completed",
        "native.session.completed",
    ]
    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert "SYSTEM_PROMPT" not in serialized
    assert "MODEL_OUTPUT_SHOULD_PRINT_ONLY" not in serialized
    for _, _, payload in sink.events:
        assert payload["system_prompt_id"] == SYSTEM_PROMPT_ID
        assert payload["prompt_stored"] is False
        assert payload["model_output_stored"] is False
        assert payload["tool_payloads_stored"] is False


def test_native_runner_finalizes_failed_provider_record(tmp_path):
    root = tmp_path / "sessions"
    result = HarnessRunner(
        adapter=PipyNativeAdapter(provider=ExplodingProvider()),
        id_factory=lambda: "native-failed",
    ).run(
        RunRequest(
            agent="pipy-native",
            slug="native-provider-failure",
            command=[],
            cwd=tmp_path,
            root=root,
            goal="Native provider failure smoke",
        )
    )

    assert result.exit_code == 1
    assert result.status == HarnessStatus.FAILED
    events = read_jsonl(result.record.jsonl_path)
    provider_failed = [event for event in events if event["type"] == "native.provider.failed"][0]
    assert provider_failed["payload"]["duration_seconds"] > 0
    assert [event["type"] for event in events[-2:]] == ["harness.run.failed", "session.finalized"]
    combined = result.record.jsonl_path.read_text(encoding="utf-8") + result.record.markdown_path.read_text(
        encoding="utf-8"
    )
    assert "You are the native pipy runtime bootstrap" not in combined
    assert "MODEL_OUTPUT" not in combined
    assert verify_session_archive(root=root).ok is True


def test_native_runner_finalizes_prepare_failure_for_missing_cwd(tmp_path):
    root = tmp_path / "sessions"
    result = HarnessRunner(
        adapter=PipyNativeAdapter(provider=CapturingProvider()),
        id_factory=lambda: "native-missing-cwd",
    ).run(
        RunRequest(
            agent="pipy-native",
            slug="native-missing-cwd",
            command=[],
            cwd=tmp_path / "missing",
            root=root,
            goal="Native missing cwd smoke",
        )
    )

    assert result.exit_code == 1
    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "ValueError"
    events = read_jsonl(result.record.jsonl_path)
    assert [event["type"] for event in events[-2:]] == ["harness.run.failed", "session.finalized"]
    assert verify_session_archive(root=root).ok is True
