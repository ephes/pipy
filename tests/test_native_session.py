from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipy_harness.adapters.native import PipyNativeAdapter
from pipy_harness.models import HarnessStatus, RunRequest
from pipy_harness.native import (
    FakeNativeProvider,
    FakeNoOpNativeTool,
    NATIVE_PATCH_PROPOSAL_RECORDED_EVENT,
    NativeRunInput,
    NativeToolRequest,
    NativeToolObservation,
    NativeToolObservationReason,
    NativeToolObservationStatus,
    NativeToolResult,
    NativeToolStatus,
    PROVIDER_PATCH_PROPOSAL_METADATA_KEY,
    PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY,
    PROVIDER_TOOL_OBSERVATION_FIXTURE_METADATA_KEY,
    PROVIDER_TOOL_INTENT_METADATA_KEY,
    ProviderRequest,
    ProviderResult,
)
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
        payload: Mapping[str, object] | None = None,
    ) -> None:
        self.events.append((event_type, summary, dict(payload) if payload is not None else None))


@dataclass(slots=True)
class CapturingProvider:
    final_text: str = "MODEL_OUTPUT_SHOULD_PRINT_ONLY"
    status: HarnessStatus = HarnessStatus.SUCCEEDED
    metadata: dict[str, object] | None = None
    usage: dict[str, object] | None = None
    captured_request: ProviderRequest | None = None
    complete_calls: int = 0

    @property
    def name(self) -> str:
        return "capturing-fake"

    @property
    def model_id(self) -> str:
        return "capturing-model"

    def complete(self, request: ProviderRequest) -> ProviderResult:
        self.complete_calls += 1
        self.captured_request = request
        now = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)
        return ProviderResult(
            status=self.status,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text=self.final_text if self.status == HarnessStatus.SUCCEEDED else None,
            usage=self.usage,
            metadata=self.metadata,
        )


@dataclass(slots=True)
class SequentialCapturingProvider:
    results: list[ProviderResult]
    captured_requests: list[ProviderRequest] | None = None

    @property
    def name(self) -> str:
        return "capturing-fake"

    @property
    def model_id(self) -> str:
        return "capturing-model"

    def complete(self, request: ProviderRequest) -> ProviderResult:
        if self.captured_requests is None:
            self.captured_requests = []
        self.captured_requests.append(request)
        if not self.results:
            raise RuntimeError("unexpected extra provider call")
        return self.results.pop(0)


class ExplodingProvider:
    name = "exploding-fake"
    model_id = "exploding-model"

    def complete(self, request: ProviderRequest) -> ProviderResult:
        time.sleep(0.001)
        raise RuntimeError("provider exploded")


@dataclass(slots=True)
class FollowUpExplodingProvider:
    initial_result: ProviderResult
    captured_requests: list[ProviderRequest] | None = None

    @property
    def name(self) -> str:
        return "exploding-follow-up"

    @property
    def model_id(self) -> str:
        return "exploding-follow-up-model"

    def complete(self, request: ProviderRequest) -> ProviderResult:
        if self.captured_requests is None:
            self.captured_requests = []
        self.captured_requests.append(request)
        if len(self.captured_requests) == 1:
            return self.initial_result
        raise RuntimeError("provider echoed provider only context")


class ExplodingTool:
    name = "noop"

    def invoke(self, request: NativeToolRequest) -> NativeToolResult:
        raise RuntimeError("tool exploded token=SECRET123")


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def safe_noop_intent() -> dict[str, object]:
    return {
        "tool_name": "noop",
        "tool_kind": "internal_noop",
        "turn_index": 0,
        "intent_source": "fake_provider",
        "approval_policy": "not-required",
        "approval_required": False,
        "sandbox_policy": "no-workspace-access",
        "filesystem_mutation_allowed": False,
        "shell_execution_allowed": False,
        "network_access_allowed": False,
        "tool_payloads_stored": False,
        "stdout_stored": False,
        "stderr_stored": False,
        "diffs_stored": False,
        "file_contents_stored": False,
        "metadata": {"fixture": "safe-noop", "safe_count": 1},
    }


def safe_synthetic_observation_fixture() -> dict[str, object]:
    return {
        "fixture_source": "synthetic_safe_noop",
        "tool_request_id": "native-tool-0001",
        "turn_index": 0,
        "tool_name": "noop",
        "tool_kind": "internal_noop",
        "status": "succeeded",
        "reason_label": "tool_result_succeeded",
        "duration_seconds": 0.001,
        "tool_payloads_stored": False,
        "stdout_stored": False,
        "stderr_stored": False,
        "diffs_stored": False,
        "file_contents_stored": False,
        "prompt_stored": False,
        "model_output_stored": False,
        "provider_responses_stored": False,
        "raw_transcript_imported": False,
    }


def safe_read_only_intent() -> dict[str, object]:
    return {
        "tool_name": "read_only_repo_inspection",
        "tool_kind": "read_only_workspace",
        "turn_index": 0,
        "intent_source": "fake_provider",
        "approval_policy": "required",
        "approval_required": True,
        "sandbox_policy": "read-only-workspace",
        "workspace_read_allowed": True,
        "filesystem_mutation_allowed": False,
        "shell_execution_allowed": False,
        "network_access_allowed": False,
        "tool_payloads_stored": False,
        "stdout_stored": False,
        "stderr_stored": False,
        "diffs_stored": False,
        "file_contents_stored": False,
        "metadata": {"fixture": "safe-read-only", "request_kind": "explicit-file-excerpt"},
    }


def safe_read_only_tool_fixture(path: str) -> dict[str, object]:
    return {
        "fixture_source": "pipy_owned_explicit_file_excerpt",
        "tool_request_id": "native-tool-0001",
        "turn_index": 0,
        "request_kind": "explicit-file-excerpt",
        "approval_decision": "allowed",
        "decision_authority": "pipy-owned",
        "workspace_relative_path": path,
        "target_authority": "pipy-owned",
        "scope_label": "single-explicit-file",
    }


def safe_patch_proposal() -> dict[str, object]:
    return {
        "proposal_source": "pipy_owned_patch_proposal",
        "tool_request_id": "native-tool-0001",
        "turn_index": 0,
        "status": "proposed",
        "reason_label": "structured_proposal_accepted",
        "file_count": 2,
        "operation_count": 3,
        "operation_labels": ["modify", "create"],
        "patch_text_stored": False,
        "diffs_stored": False,
        "file_contents_stored": False,
        "prompt_stored": False,
        "model_output_stored": False,
        "provider_responses_stored": False,
        "raw_transcript_imported": False,
        "workspace_mutated": False,
    }


def provider_result(
    *,
    final_text: str,
    metadata: dict[str, object] | None = None,
    usage: dict[str, object] | None = None,
    status: HarnessStatus = HarnessStatus.SUCCEEDED,
) -> ProviderResult:
    now = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)
    return ProviderResult(
        status=status,
        provider_name="capturing-fake",
        model_id="capturing-model",
        started_at=now,
        ended_at=now,
        final_text=final_text if status == HarnessStatus.SUCCEEDED else None,
        usage=usage,
        metadata=metadata,
    )


def test_native_session_no_intent_builds_prompt_calls_provider_and_emits_safe_events(tmp_path):
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
    assert "SAFE_GOAL_METADATA" not in serialized
    for _, _, payload in sink.events:
        assert payload is not None
        assert payload["system_prompt_id"] == SYSTEM_PROMPT_ID
        assert payload["prompt_stored"] is False
        assert payload["model_output_stored"] is False
        assert payload["tool_payloads_stored"] is False
    assert not [event for event in sink.events if event[0].startswith("native.tool.")]


def test_native_session_normalizes_provider_usage_before_archiving(tmp_path):
    provider = CapturingProvider(
        usage={
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 12,
            "cached_tokens": 3,
            "reasoning_tokens": 1,
            "input_characters": 999,
            "raw_provider_usage": "SHOULD_NOT_PERSIST",
        }
    )
    sink = RecordingSink()

    NativeAgentSession(provider=provider).run(
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

    provider_completed = [event for event in sink.events if event[0] == "native.provider.completed"][0]
    assert provider_completed[2]["usage"] == {
        "cached_tokens": 3,
        "input_tokens": 10,
        "output_tokens": 2,
        "reasoning_tokens": 1,
        "total_tokens": 12,
    }
    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert "input_characters" not in serialized
    assert "SHOULD_NOT_PERSIST" not in serialized


def test_native_session_drops_raw_content_provider_metadata_before_archiving(tmp_path):
    provider = CapturingProvider(
        metadata={
            "response_status": "completed",
            "raw_patch_text": "SHOULD_NOT_PERSIST",
            "raw_diff": "SHOULD_NOT_PERSIST",
            "file_contents": "SHOULD_NOT_PERSIST",
            "stdout": "SHOULD_NOT_PERSIST",
            "stderr": "SHOULD_NOT_PERSIST",
            "request_body": "SHOULD_NOT_PERSIST",
        }
    )
    sink = RecordingSink()

    NativeAgentSession(provider=provider).run(
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

    provider_completed = [event for event in sink.events if event[0] == "native.provider.completed"][0]
    assert provider_completed[2]["provider_metadata"] == {"response_status": "completed"}
    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert "SHOULD_NOT_PERSIST" not in serialized


def test_native_session_safe_fake_noop_intent_invokes_tool_after_detected_event(tmp_path):
    provider = FakeNativeProvider(tool_intent=safe_noop_intent())
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
    assert [event[0] for event in sink.events] == [
        "native.session.started",
        "native.provider.started",
        "native.provider.completed",
        "native.tool.intent.detected",
        "native.tool.started",
        "native.tool.completed",
        "native.session.completed",
    ]
    provider_completed = [event for event in sink.events if event[0] == "native.provider.completed"][0]
    assert provider_completed[2]["provider_metadata"] == {"tool_intent_metadata_present": True}
    intent_detected = [event for event in sink.events if event[0] == "native.tool.intent.detected"][0]
    intent_payload = intent_detected[2]
    assert intent_payload is not None
    assert intent_payload["tool_request_id"] == "native-tool-0001"
    assert intent_payload["turn_index"] == 0
    assert intent_payload["intent_source"] == "fake_provider"
    assert intent_payload["intent_metadata"] == {
        "fixture": "safe-noop",
        "internal_noop": True,
        "safe_count": 1,
        "tool_payloads_stored": False,
    }
    tool_completed = [event for event in sink.events if event[0] == "native.tool.completed"][0]
    tool_payload = tool_completed[2]
    assert tool_payload is not None
    assert tool_payload["tool_request_id"] == "native-tool-0001"
    assert tool_payload["tool_name"] == "noop"
    assert tool_payload["tool_kind"] == "internal_noop"
    assert tool_payload["approval_policy"] == "not-required"
    assert tool_payload["sandbox_policy"] == "no-workspace-access"
    assert tool_payload["filesystem_mutation_allowed"] is False
    assert tool_payload["shell_execution_allowed"] is False
    assert tool_payload["network_access_allowed"] is False
    assert tool_payload["stdout_stored"] is False
    assert tool_payload["stderr_stored"] is False
    assert tool_payload["diffs_stored"] is False
    assert tool_payload["file_contents_stored"] is False


def test_native_session_safe_noop_intent_does_not_call_provider_after_tool_result(tmp_path):
    provider = CapturingProvider(
        final_text="MODEL_OUTPUT_SHOULD_NOT_BE_ARCHIVED_AFTER_TOOL",
        metadata={PROVIDER_TOOL_INTENT_METADATA_KEY: safe_noop_intent()},
    )
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

    event_types = [event[0] for event in sink.events]
    assert output.status == HarnessStatus.SUCCEEDED
    assert output.exit_code == 0
    assert provider.complete_calls == 1
    assert event_types.count("native.tool.intent.detected") == 1
    assert event_types.count("native.tool.started") == 1
    assert event_types.count("native.tool.completed") == 1
    assert event_types == [
        "native.session.started",
        "native.provider.started",
        "native.provider.completed",
        "native.tool.intent.detected",
        "native.tool.started",
        "native.tool.completed",
        "native.session.completed",
    ]
    assert event_types.count("native.provider.started") == 1
    assert event_types.count("native.provider.completed") == 1
    tool_completed_index = event_types.index("native.tool.completed")
    assert event_types.index("native.provider.completed") < tool_completed_index
    assert not event_types[tool_completed_index + 1 : -1]
    tool_payloads = [
        payload for event_type, _, payload in sink.events if event_type.startswith("native.tool.")
    ]
    assert [payload["tool_request_id"] for payload in tool_payloads if payload is not None] == [
        "native-tool-0001",
        "native-tool-0001",
        "native-tool-0001",
    ]
    intent_turn_indexes = [
        payload["turn_index"]
        for event_type, _, payload in sink.events
        if event_type == "native.tool.intent.detected" and payload is not None
    ]
    assert intent_turn_indexes == [0]

    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert "MODEL_OUTPUT_SHOULD_NOT_BE_ARCHIVED_AFTER_TOOL" not in serialized
    assert "SAFE_GOAL_METADATA" not in serialized
    for event_type, _, payload in sink.events:
        assert payload is not None
        assert payload["prompt_stored"] is False
        assert payload["model_output_stored"] is False
        assert payload["tool_payloads_stored"] is False
        if event_type.startswith("native.tool."):
            assert payload["stdout_stored"] is False
            assert payload["stderr_stored"] is False
            assert payload["diffs_stored"] is False
            assert payload["file_contents_stored"] is False


def test_native_session_supported_synthetic_observation_fixture_makes_one_follow_up_turn(tmp_path):
    provider = SequentialCapturingProvider(
        results=[
            provider_result(
                final_text="INITIAL_MODEL_OUTPUT_SHOULD_NOT_PRINT",
                metadata={
                    PROVIDER_TOOL_INTENT_METADATA_KEY: safe_noop_intent(),
                    PROVIDER_TOOL_OBSERVATION_FIXTURE_METADATA_KEY: safe_synthetic_observation_fixture(),
                },
                usage={"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
            ),
            provider_result(
                final_text="FOLLOW_UP_MODEL_OUTPUT_SHOULD_PRINT_ONLY",
                usage={"input_tokens": 7, "output_tokens": 11, "total_tokens": 18},
            ),
        ]
    )
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
    assert output.final_text == "FOLLOW_UP_MODEL_OUTPUT_SHOULD_PRINT_ONLY"
    assert output.usage == {"input_tokens": 10, "output_tokens": 16, "total_tokens": 26}
    assert provider.captured_requests is not None
    assert len(provider.captured_requests) == 2
    initial_request, follow_up_request = provider.captured_requests
    assert initial_request.provider_turn_index == 0
    assert initial_request.provider_turn_label == "initial"
    assert initial_request.tool_observation is None
    assert follow_up_request.provider_turn_index == 1
    assert follow_up_request.provider_turn_label == "post_tool_observation"
    assert follow_up_request.user_prompt != "SAFE_GOAL_METADATA"
    assert "native-tool-0001" in follow_up_request.user_prompt
    assert "tool_result_succeeded" in follow_up_request.user_prompt
    assert "INITIAL_MODEL_OUTPUT_SHOULD_NOT_PRINT" not in follow_up_request.user_prompt
    assert isinstance(follow_up_request.tool_observation, NativeToolObservation)
    assert follow_up_request.tool_observation.tool_request_id == "native-tool-0001"
    assert follow_up_request.tool_observation.turn_index == 0
    assert follow_up_request.tool_observation.status == NativeToolObservationStatus.SUCCEEDED
    assert follow_up_request.tool_observation.reason_label == NativeToolObservationReason.TOOL_RESULT_SUCCEEDED

    event_types = [event[0] for event in sink.events]
    assert event_types == [
        "native.session.started",
        "native.provider.started",
        "native.provider.completed",
        "native.tool.intent.detected",
        "native.tool.started",
        "native.tool.completed",
        "native.tool.observation.recorded",
        "native.provider.started",
        "native.provider.completed",
        "native.session.completed",
    ]
    provider_payloads = [payload for event_type, _, payload in sink.events if event_type.startswith("native.provider.")]
    assert [payload["provider_turn_index"] for payload in provider_payloads if payload is not None] == [
        0,
        0,
        1,
        1,
    ]
    assert [payload["provider_turn_label"] for payload in provider_payloads if payload is not None] == [
        "initial",
        "initial",
        "post_tool_observation",
        "post_tool_observation",
    ]
    observation_event = [event for event in sink.events if event[0] == "native.tool.observation.recorded"][0]
    observation_payload = observation_event[2]
    assert observation_payload == {
        "adapter": "pipy-native",
        "provider": "capturing-fake",
        "model_id": "capturing-model",
        "system_prompt_id": SYSTEM_PROMPT_ID,
        "system_prompt_version": SYSTEM_PROMPT_VERSION,
        "prompt_stored": False,
        "model_output_stored": False,
        "tool_payloads_stored": False,
        "raw_transcript_imported": False,
        "tool_request_id": "native-tool-0001",
        "turn_index": 0,
        "tool_name": "noop",
        "tool_kind": "internal_noop",
        "status": "succeeded",
        "reason_label": "tool_result_succeeded",
        "duration_seconds": 0.001,
        "stdout_stored": False,
        "stderr_stored": False,
        "diffs_stored": False,
        "file_contents_stored": False,
        "provider_responses_stored": False,
    }
    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert "SAFE_GOAL_METADATA" not in serialized
    assert "INITIAL_MODEL_OUTPUT_SHOULD_NOT_PRINT" not in serialized
    assert "FOLLOW_UP_MODEL_OUTPUT_SHOULD_PRINT_ONLY" not in serialized


def test_native_session_read_only_tool_context_reaches_follow_up_provider_only_in_memory(tmp_path):
    source = tmp_path / "src" / "example.py"
    source.parent.mkdir()
    source.write_text("def visible_context():\n    return 'provider only context'\n", encoding="utf-8")
    provider = SequentialCapturingProvider(
        results=[
            provider_result(
                final_text="INITIAL_MODEL_OUTPUT_SHOULD_NOT_PRINT",
                metadata={
                    PROVIDER_TOOL_INTENT_METADATA_KEY: safe_read_only_intent(),
                    PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY: safe_read_only_tool_fixture(
                        "src/example.py"
                    ),
                },
                usage={"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
            ),
            provider_result(
                final_text="FOLLOW_UP_MODEL_OUTPUT_SHOULD_PRINT_ONLY",
                usage={"input_tokens": 7, "output_tokens": 11, "total_tokens": 18},
            ),
        ]
    )
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
    assert output.final_text == "FOLLOW_UP_MODEL_OUTPUT_SHOULD_PRINT_ONLY"
    assert output.usage == {"input_tokens": 10, "output_tokens": 16, "total_tokens": 26}
    assert provider.captured_requests is not None
    assert len(provider.captured_requests) == 2
    follow_up_request = provider.captured_requests[1]
    assert follow_up_request.provider_turn_index == 1
    assert follow_up_request.provider_turn_label == "post_tool_observation"
    assert "source_label=example.py" in follow_up_request.user_prompt
    assert "provider only context" in follow_up_request.user_prompt
    assert isinstance(follow_up_request.tool_observation, NativeToolObservation)
    assert follow_up_request.tool_observation.tool_request_id == "native-tool-0001"
    assert follow_up_request.tool_observation.tool_name == "read_only_repo_inspection"
    assert follow_up_request.tool_observation.tool_kind == "read_only_workspace"

    event_types = [event[0] for event in sink.events]
    assert event_types == [
        "native.session.started",
        "native.provider.started",
        "native.provider.completed",
        "native.tool.intent.detected",
        "native.tool.started",
        "native.tool.completed",
        "native.tool.observation.recorded",
        "native.provider.started",
        "native.provider.completed",
        "native.session.completed",
    ]
    provider_completed = [event for event in sink.events if event[0] == "native.provider.completed"][0]
    assert provider_completed[2]["provider_metadata"] == {
        "read_only_tool_fixture_metadata_present": True,
        "tool_intent_metadata_present": True,
    }
    tool_completed = [event for event in sink.events if event[0] == "native.tool.completed"][0]
    tool_payload = tool_completed[2]
    assert tool_payload is not None
    assert tool_payload["tool_name"] == "read_only_repo_inspection"
    assert tool_payload["tool_kind"] == "read_only_workspace"
    assert tool_payload["approval_policy"] == "required"
    assert tool_payload["approval_required"] is True
    assert tool_payload["sandbox_policy"] == "read-only-workspace"
    assert tool_payload["workspace_read_allowed"] is True
    assert tool_payload["file_contents_stored"] is False
    assert tool_payload["tool_metadata"]["source_label"] == "example.py"
    assert tool_payload["tool_metadata"]["excerpt_count"] == 1
    observation_payload = [event[2] for event in sink.events if event[0] == "native.tool.observation.recorded"][0]
    assert observation_payload is not None
    assert observation_payload["tool_name"] == "read_only_repo_inspection"
    assert observation_payload["tool_kind"] == "read_only_workspace"
    assert observation_payload["status"] == "succeeded"
    assert observation_payload["reason_label"] == "tool_result_succeeded"
    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert "provider only context" not in serialized
    assert "INITIAL_MODEL_OUTPUT_SHOULD_NOT_PRINT" not in serialized
    assert "FOLLOW_UP_MODEL_OUTPUT_SHOULD_PRINT_ONLY" not in serialized
    assert "SAFE_GOAL_METADATA" not in serialized


def test_native_session_records_patch_proposal_metadata_after_read_only_follow_up(tmp_path):
    source = tmp_path / "src" / "example.py"
    source.parent.mkdir()
    source.write_text("def visible_context():\n    return 'provider only context'\n", encoding="utf-8")
    provider = SequentialCapturingProvider(
        results=[
            provider_result(
                final_text="INITIAL_MODEL_OUTPUT_SHOULD_NOT_PRINT",
                metadata={
                    PROVIDER_TOOL_INTENT_METADATA_KEY: safe_read_only_intent(),
                    PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY: safe_read_only_tool_fixture(
                        "src/example.py"
                    ),
                },
            ),
            provider_result(
                final_text="FOLLOW_UP_MODEL_OUTPUT_SHOULD_PRINT_ONLY",
                metadata={
                    PROVIDER_PATCH_PROPOSAL_METADATA_KEY: {
                        **safe_patch_proposal(),
                        "raw_patch_text": "SHOULD_NOT_PERSIST",
                    }
                },
            ),
        ]
    )
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
    assert output.final_text == "FOLLOW_UP_MODEL_OUTPUT_SHOULD_PRINT_ONLY"
    event_types = [event[0] for event in sink.events]
    assert event_types == [
        "native.session.started",
        "native.provider.started",
        "native.provider.completed",
        "native.tool.intent.detected",
        "native.tool.started",
        "native.tool.completed",
        "native.tool.observation.recorded",
        "native.provider.started",
        "native.provider.completed",
        NATIVE_PATCH_PROPOSAL_RECORDED_EVENT,
        "native.session.completed",
    ]
    follow_up_completed = [event for event in sink.events if event[0] == "native.provider.completed"][1]
    assert follow_up_completed[2]["provider_metadata"] == {"patch_proposal_metadata_present": True}
    proposal_payload = [event[2] for event in sink.events if event[0] == NATIVE_PATCH_PROPOSAL_RECORDED_EVENT][0]
    assert proposal_payload == {
        "adapter": "pipy-native",
        "provider": "capturing-fake",
        "model_id": "capturing-model",
        "system_prompt_id": SYSTEM_PROMPT_ID,
        "system_prompt_version": SYSTEM_PROMPT_VERSION,
        "prompt_stored": False,
        "model_output_stored": False,
        "tool_payloads_stored": False,
        "raw_transcript_imported": False,
        "tool_request_id": "native-tool-0001",
        "turn_index": 0,
        "status": "skipped",
        "reason_label": "unsafe_proposal",
        "file_count": 0,
        "operation_count": 0,
        "operation_labels": [],
        "patch_text_stored": False,
        "diffs_stored": False,
        "file_contents_stored": False,
        "provider_responses_stored": False,
        "workspace_mutated": False,
    }
    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert "SHOULD_NOT_PERSIST" not in serialized
    assert "provider only context" not in serialized
    assert "FOLLOW_UP_MODEL_OUTPUT_SHOULD_PRINT_ONLY" not in serialized
    assert "INITIAL_MODEL_OUTPUT_SHOULD_NOT_PRINT" not in serialized
    assert "SAFE_GOAL_METADATA" not in serialized


def test_native_session_records_supported_patch_proposal_metadata_only(tmp_path):
    source = tmp_path / "src" / "example.py"
    source.parent.mkdir()
    source.write_text("def visible_context():\n    return 'provider only context'\n", encoding="utf-8")
    provider = SequentialCapturingProvider(
        results=[
            provider_result(
                final_text="INITIAL_MODEL_OUTPUT_SHOULD_NOT_PRINT",
                metadata={
                    PROVIDER_TOOL_INTENT_METADATA_KEY: safe_read_only_intent(),
                    PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY: safe_read_only_tool_fixture(
                        "src/example.py"
                    ),
                },
            ),
            provider_result(
                final_text="FOLLOW_UP_MODEL_OUTPUT_SHOULD_PRINT_ONLY",
                metadata={PROVIDER_PATCH_PROPOSAL_METADATA_KEY: safe_patch_proposal()},
            ),
        ]
    )
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
    proposal_payload = [event[2] for event in sink.events if event[0] == NATIVE_PATCH_PROPOSAL_RECORDED_EVENT][0]
    assert proposal_payload is not None
    assert proposal_payload["tool_request_id"] == "native-tool-0001"
    assert proposal_payload["turn_index"] == 0
    assert proposal_payload["status"] == "proposed"
    assert proposal_payload["reason_label"] == "structured_proposal_accepted"
    assert proposal_payload["file_count"] == 2
    assert proposal_payload["operation_count"] == 3
    assert proposal_payload["operation_labels"] == ["modify", "create"]
    assert proposal_payload["patch_text_stored"] is False
    assert proposal_payload["diffs_stored"] is False
    assert proposal_payload["file_contents_stored"] is False
    assert proposal_payload["workspace_mutated"] is False
    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert "provider only context" not in serialized
    assert "FOLLOW_UP_MODEL_OUTPUT_SHOULD_PRINT_ONLY" not in serialized
    assert "INITIAL_MODEL_OUTPUT_SHOULD_NOT_PRINT" not in serialized
    assert "SAFE_GOAL_METADATA" not in serialized


def test_native_session_drops_initial_patch_proposal_metadata_without_presence_flag(tmp_path):
    provider = CapturingProvider(
        final_text="MODEL_OUTPUT_SHOULD_PRINT_ONLY",
        metadata={PROVIDER_PATCH_PROPOSAL_METADATA_KEY: safe_patch_proposal()},
    )
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
    assert provider.complete_calls == 1
    assert NATIVE_PATCH_PROPOSAL_RECORDED_EVENT not in [event[0] for event in sink.events]
    provider_completed = [event for event in sink.events if event[0] == "native.provider.completed"][0]
    assert provider_completed[2]["provider_metadata"] == {}
    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert "pipy_native_patch_proposal" not in serialized
    assert "patch_proposal_metadata_present" not in serialized
    assert "MODEL_OUTPUT_SHOULD_PRINT_ONLY" not in serialized
    assert "SAFE_GOAL_METADATA" not in serialized


def test_native_session_drops_synthetic_follow_up_patch_proposal_metadata_without_presence_flag(tmp_path):
    provider = SequentialCapturingProvider(
        results=[
            provider_result(
                final_text="INITIAL_MODEL_OUTPUT_SHOULD_NOT_PRINT",
                metadata={
                    PROVIDER_TOOL_INTENT_METADATA_KEY: safe_noop_intent(),
                    PROVIDER_TOOL_OBSERVATION_FIXTURE_METADATA_KEY: safe_synthetic_observation_fixture(),
                },
            ),
            provider_result(
                final_text="FOLLOW_UP_MODEL_OUTPUT_SHOULD_PRINT_ONLY",
                metadata={PROVIDER_PATCH_PROPOSAL_METADATA_KEY: safe_patch_proposal()},
            ),
        ]
    )
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
    assert NATIVE_PATCH_PROPOSAL_RECORDED_EVENT not in [event[0] for event in sink.events]
    follow_up_completed = [event for event in sink.events if event[0] == "native.provider.completed"][1]
    assert follow_up_completed[2]["provider_metadata"] == {}
    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert "pipy_native_patch_proposal" not in serialized
    assert "patch_proposal_metadata_present" not in serialized
    assert "FOLLOW_UP_MODEL_OUTPUT_SHOULD_PRINT_ONLY" not in serialized
    assert "INITIAL_MODEL_OUTPUT_SHOULD_NOT_PRINT" not in serialized
    assert "SAFE_GOAL_METADATA" not in serialized


@pytest.mark.parametrize(
    ("proposal", "expected_reason"),
    [
        ("not-a-mapping", "unsafe_proposal"),
        ({**safe_patch_proposal(), "proposal_source": "provider_native_tool_call"}, "unsupported_proposal"),
        ({**safe_patch_proposal(), "status": "raw_diff"}, "unsupported_proposal"),
        ({**safe_patch_proposal(), "tool_request_id": "provider-owned-id"}, "unsafe_proposal"),
        ({**safe_patch_proposal(), "operation_labels": ["modify", "shell"]}, "unsupported_proposal"),
        ({**safe_patch_proposal(), "patch_text_stored": True}, "unsafe_proposal"),
        ({**safe_patch_proposal(), "diffs_stored": True}, "unsafe_proposal"),
        ({**safe_patch_proposal(), "file_contents_stored": True}, "unsafe_proposal"),
    ],
)
def test_native_session_unsafe_or_unsupported_patch_proposal_records_skipped_metadata_only(
    tmp_path,
    proposal: object,
    expected_reason: str,
):
    source = tmp_path / "src" / "example.py"
    source.parent.mkdir()
    source.write_text("def visible_context():\n    return 'provider only context'\n", encoding="utf-8")
    provider = SequentialCapturingProvider(
        results=[
            provider_result(
                final_text="INITIAL_MODEL_OUTPUT_SHOULD_NOT_PRINT",
                metadata={
                    PROVIDER_TOOL_INTENT_METADATA_KEY: safe_read_only_intent(),
                    PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY: safe_read_only_tool_fixture(
                        "src/example.py"
                    ),
                },
            ),
            provider_result(
                final_text="FOLLOW_UP_MODEL_OUTPUT_SHOULD_PRINT_ONLY",
                metadata={PROVIDER_PATCH_PROPOSAL_METADATA_KEY: proposal},
            ),
        ]
    )
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
    proposal_payload = [event[2] for event in sink.events if event[0] == NATIVE_PATCH_PROPOSAL_RECORDED_EVENT][0]
    assert proposal_payload is not None
    assert proposal_payload["status"] == "skipped"
    assert proposal_payload["reason_label"] == expected_reason
    assert proposal_payload["file_count"] == 0
    assert proposal_payload["operation_count"] == 0
    assert proposal_payload["operation_labels"] == []
    assert proposal_payload["patch_text_stored"] is False
    assert proposal_payload["diffs_stored"] is False
    assert proposal_payload["file_contents_stored"] is False
    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert "provider-owned-id" not in serialized
    assert "provider_native_tool_call" not in serialized
    assert "raw_diff" not in serialized
    assert "provider only context" not in serialized
    assert "FOLLOW_UP_MODEL_OUTPUT_SHOULD_PRINT_ONLY" not in serialized


def test_native_session_read_only_follow_up_provider_exception_does_not_archive_context(tmp_path):
    source = tmp_path / "src" / "example.py"
    source.parent.mkdir()
    source.write_text("def visible_context():\n    return 'provider only context'\n", encoding="utf-8")
    provider = FollowUpExplodingProvider(
        initial_result=provider_result(
            final_text="INITIAL_MODEL_OUTPUT_SHOULD_NOT_PRINT",
            metadata={
                PROVIDER_TOOL_INTENT_METADATA_KEY: safe_read_only_intent(),
                PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY: safe_read_only_tool_fixture(
                    "src/example.py"
                ),
            },
        )
    )
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

    assert output.status == HarnessStatus.FAILED
    assert output.exit_code == 1
    assert output.final_text is None
    assert output.error_type == "RuntimeError"
    assert output.error_message == "RuntimeError"
    assert provider.captured_requests is not None
    assert len(provider.captured_requests) == 2
    assert "provider only context" in provider.captured_requests[1].user_prompt
    provider_failed = [event for event in sink.events if event[0] == "native.provider.failed"][0]
    assert provider_failed[2] is not None
    assert provider_failed[2]["error_message"] == "RuntimeError"
    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert "provider only context" not in serialized
    assert "provider echoed" not in serialized
    assert "INITIAL_MODEL_OUTPUT_SHOULD_NOT_PRINT" not in serialized
    assert "SAFE_GOAL_METADATA" not in serialized


def test_native_session_read_only_tool_skips_generated_target_before_provider_visibility(tmp_path):
    generated = tmp_path / "node_modules" / "package" / "index.js"
    generated.parent.mkdir(parents=True)
    generated.write_text("module.exports = 'SHOULD_NOT_REACH_PROVIDER';\n", encoding="utf-8")
    provider = CapturingProvider(
        final_text="MODEL_OUTPUT_SHOULD_NOT_PRINT_FOR_SKIPPED_READ",
        metadata={
            PROVIDER_TOOL_INTENT_METADATA_KEY: safe_read_only_intent(),
            PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY: safe_read_only_tool_fixture(
                "node_modules/package/index.js"
            ),
        },
    )
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

    assert output.status == HarnessStatus.FAILED
    assert output.exit_code == 1
    assert output.final_text is None
    assert output.error_message == "ignored_or_generated_file"
    assert provider.complete_calls == 1
    event_types = [event[0] for event in sink.events]
    assert event_types == [
        "native.session.started",
        "native.provider.started",
        "native.provider.completed",
        "native.tool.intent.detected",
        "native.tool.started",
        "native.tool.skipped",
        "native.session.completed",
    ]
    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert "SHOULD_NOT_REACH_PROVIDER" not in serialized
    assert "MODEL_OUTPUT_SHOULD_NOT_PRINT_FOR_SKIPPED_READ" not in serialized
    assert "SAFE_GOAL_METADATA" not in serialized


def test_native_session_unsafe_read_only_fixture_skips_before_read_or_follow_up(tmp_path):
    (tmp_path / "example.txt").write_text("SHOULD_NOT_REACH_PROVIDER\n", encoding="utf-8")
    provider = CapturingProvider(
        final_text="MODEL_OUTPUT_SHOULD_NOT_PRINT_FOR_UNSAFE_READ",
        metadata={
            PROVIDER_TOOL_INTENT_METADATA_KEY: safe_read_only_intent(),
            PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY: {
                **safe_read_only_tool_fixture("example.txt"),
                "raw_payload": "SHOULD_NOT_PERSIST",
            },
        },
    )
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

    assert output.status == HarnessStatus.FAILED
    assert output.exit_code == 1
    assert output.final_text is None
    assert output.error_message == "unsafe_read_only_context"
    assert provider.complete_calls == 1
    event_types = [event[0] for event in sink.events]
    assert event_types == [
        "native.session.started",
        "native.provider.started",
        "native.provider.completed",
        "native.tool.intent.detected",
        "native.tool.skipped",
        "native.session.completed",
    ]
    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert "SHOULD_NOT_REACH_PROVIDER" not in serialized
    assert "SHOULD_NOT_PERSIST" not in serialized
    assert "MODEL_OUTPUT_SHOULD_NOT_PRINT_FOR_UNSAFE_READ" not in serialized
    assert "SAFE_GOAL_METADATA" not in serialized


def test_native_session_read_only_intent_without_fixture_skips_before_read_or_follow_up(tmp_path):
    (tmp_path / "example.txt").write_text("SHOULD_NOT_REACH_PROVIDER\n", encoding="utf-8")
    provider = CapturingProvider(
        final_text="MODEL_OUTPUT_SHOULD_NOT_PRINT_FOR_MISSING_READ_FIXTURE",
        metadata={PROVIDER_TOOL_INTENT_METADATA_KEY: safe_read_only_intent()},
    )
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

    assert output.status == HarnessStatus.FAILED
    assert output.exit_code == 1
    assert output.final_text is None
    assert output.error_message == "missing_read_only_context"
    assert provider.complete_calls == 1
    assert [event[0] for event in sink.events] == [
        "native.session.started",
        "native.provider.started",
        "native.provider.completed",
        "native.tool.intent.detected",
        "native.tool.skipped",
        "native.session.completed",
    ]
    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert "SHOULD_NOT_REACH_PROVIDER" not in serialized
    assert "MODEL_OUTPUT_SHOULD_NOT_PRINT_FOR_MISSING_READ_FIXTURE" not in serialized
    assert "SAFE_GOAL_METADATA" not in serialized


@pytest.mark.parametrize(
    ("fixture", "expected_reason"),
    [
        ({"fixture_source": "synthetic_safe_noop", "payload": "SHOULD_NOT_PERSIST"}, "unsafe_observation"),
        (
            {
                **safe_synthetic_observation_fixture(),
                "fixture_source": "raw_provider_observation",
            },
            "unsupported_observation",
        ),
        (
            {
                **safe_synthetic_observation_fixture(),
                "tool_request_id": "provider-owned-id",
            },
            "unsafe_observation",
        ),
        (
            {
                **safe_synthetic_observation_fixture(),
                "status": "failed",
                "reason_label": "tool_result_failed",
            },
            "unsupported_observation",
        ),
        (
            {
                **safe_synthetic_observation_fixture(),
                "duration_seconds": float("inf"),
            },
            "unsupported_observation",
        ),
        (
            {
                **safe_synthetic_observation_fixture(),
                "duration_seconds": float("nan"),
            },
            "unsupported_observation",
        ),
    ],
)
def test_native_session_unsafe_or_unsupported_observation_fixture_skips_before_follow_up_provider(
    tmp_path,
    fixture: object,
    expected_reason: str,
):
    provider = CapturingProvider(
        final_text="MODEL_OUTPUT_SHOULD_NOT_PRINT_FOR_SKIPPED_OBSERVATION",
        metadata={
            PROVIDER_TOOL_INTENT_METADATA_KEY: safe_noop_intent(),
            PROVIDER_TOOL_OBSERVATION_FIXTURE_METADATA_KEY: fixture,
        },
    )
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

    assert output.status == HarnessStatus.FAILED
    assert output.exit_code == 1
    assert output.final_text is None
    assert output.error_message == expected_reason
    assert provider.complete_calls == 1
    event_types = [event[0] for event in sink.events]
    assert event_types == [
        "native.session.started",
        "native.provider.started",
        "native.provider.completed",
        "native.tool.intent.detected",
        "native.tool.started",
        "native.tool.completed",
        "native.tool.observation.recorded",
        "native.session.completed",
    ]
    observation_payload = [event[2] for event in sink.events if event[0] == "native.tool.observation.recorded"][0]
    assert observation_payload is not None
    assert observation_payload["tool_request_id"] == "native-tool-0001"
    assert observation_payload["turn_index"] == 0
    assert observation_payload["status"] == "skipped"
    assert observation_payload["reason_label"] == expected_reason
    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert "SHOULD_NOT_PERSIST" not in serialized
    assert "provider-owned-id" not in serialized
    assert "raw_provider_observation" not in serialized
    assert "SAFE_GOAL_METADATA" not in serialized


def test_native_session_unsafe_intent_skips_without_detected_or_started_events(tmp_path):
    provider = FakeNativeProvider(
        final_text="MODEL_OUTPUT_SHOULD_NOT_PRINT_FOR_UNSAFE_INTENT",
        tool_intent={
            "tool_name": "noop",
            "tool_kind": "internal_noop",
            "intent_source": "fake_provider",
            "payload": {"command": "SHOULD_NOT_PERSIST"},
            "metadata": {"raw_payload": "SHOULD_NOT_PERSIST"},
        },
    )
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

    event_types = [event[0] for event in sink.events]
    assert output.status == HarnessStatus.FAILED
    assert output.exit_code == 1
    assert output.final_text is None
    assert "native.tool.intent.detected" not in event_types
    assert "native.tool.started" not in event_types
    assert [event for event in event_types if event.startswith("native.tool.")] == ["native.tool.skipped"]
    tool_skipped = [event for event in sink.events if event[0] == "native.tool.skipped"][0]
    assert tool_skipped[2]["reason"] == "unsafe_tool_intent_keys"
    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert "SHOULD_NOT_PERSIST" not in serialized
    assert "raw_payload" not in serialized


@pytest.mark.parametrize(
    ("tool_intent", "reason"),
    [
        (["not", "a", "mapping"], "unsafe_tool_intent_shape"),
        (
            {
                "tool_name": "noop",
                "tool_kind": "internal_noop",
                "turn_index": 0,
                "intent_source": "fake_provider",
                "metadata": {"command": "SHOULD_NOT_PERSIST"},
            },
            "unsafe_tool_intent_metadata",
        ),
        (
            {
                "request_id": "provider-owned-id",
                "tool_name": "noop",
                "tool_kind": "internal_noop",
                "turn_index": 0,
                "intent_source": "fake_provider",
            },
            "unsafe_tool_intent_request_id",
        ),
        (
            {
                "tool_name": "noop",
                "tool_kind": "internal_noop",
                "turn_index": 1,
                "intent_source": "fake_provider",
            },
            "unsafe_tool_intent_turn_index",
        ),
        (
            {
                "tool_name": "noop",
                "tool_kind": "internal_noop",
                "turn_index": 0,
                "intent_source": "raw_provider_tool_call",
            },
            "unsafe_tool_intent_source",
        ),
        (
            {
                "tool_name": "noop",
                "tool_kind": "internal_noop",
                "turn_index": 0,
                "intent_source": "fake_provider",
                "approval_required": True,
            },
            "unsafe_tool_intent_policy",
        ),
    ],
)
def test_native_session_unsafe_intent_reasons_are_sanitized_and_skipped(
    tmp_path,
    tool_intent: object,
    reason: str,
):
    provider = FakeNativeProvider(
        final_text="MODEL_OUTPUT_SHOULD_NOT_PRINT_FOR_UNSAFE_INTENT",
        metadata={PROVIDER_TOOL_INTENT_METADATA_KEY: tool_intent},
    )
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

    event_types = [event[0] for event in sink.events]
    assert output.status == HarnessStatus.FAILED
    assert output.final_text is None
    assert "native.tool.intent.detected" not in event_types
    assert "native.tool.started" not in event_types
    tool_skipped = [event for event in sink.events if event[0] == "native.tool.skipped"][0]
    tool_payload = tool_skipped[2]
    assert tool_payload is not None
    assert tool_payload["reason"] == reason
    assert tool_payload["tool_request_id"] == "native-tool-0001"
    assert tool_payload["tool_name"] == "unsafe"
    assert tool_payload["tool_kind"] == "unsafe_intent"
    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert "SHOULD_NOT_PERSIST" not in serialized
    assert "provider-owned-id" not in serialized
    assert "raw_provider_tool_call" not in serialized


def test_native_session_provider_request_like_id_is_not_archived_as_tool_request_id(tmp_path):
    provider_request_id = "provider-call-secret-like-9999"
    provider = FakeNativeProvider(
        final_text="MODEL_OUTPUT_SHOULD_NOT_PRINT_FOR_PROVIDER_ID",
        metadata={
            PROVIDER_TOOL_INTENT_METADATA_KEY: {
                "request_id": provider_request_id,
                "tool_name": "noop",
                "tool_kind": "internal_noop",
                "turn_index": 0,
                "intent_source": "fake_provider",
            }
        },
    )
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

    event_types = [event[0] for event in sink.events]
    assert output.status == HarnessStatus.FAILED
    assert "native.tool.intent.detected" not in event_types
    assert "native.tool.started" not in event_types
    assert [event for event in event_types if event.startswith("native.tool.")] == ["native.tool.skipped"]
    tool_skipped = [event for event in sink.events if event[0] == "native.tool.skipped"][0]
    assert tool_skipped[2] is not None
    assert tool_skipped[2]["reason"] == "unsafe_tool_intent_request_id"
    assert tool_skipped[2]["tool_request_id"] == "native-tool-0001"
    assert "turn_index" not in tool_skipped[2]

    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    assert provider_request_id not in serialized


def test_native_session_unsupported_intent_skips_without_invoking_tool(tmp_path):
    provider = FakeNativeProvider(
        tool_intent={
            "tool_name": "shell",
            "tool_kind": "external_shell",
            "turn_index": 0,
            "intent_source": "fake_provider",
        },
    )
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

    event_types = [event[0] for event in sink.events]
    assert output.status == HarnessStatus.FAILED
    assert "native.tool.intent.detected" not in event_types
    assert "native.tool.started" not in event_types
    tool_skipped = [event for event in sink.events if event[0] == "native.tool.skipped"][0]
    assert tool_skipped[2]["reason"] == "unsupported_tool_intent"
    assert tool_skipped[2]["tool_name"] == "unsupported"
    assert tool_skipped[2]["tool_kind"] == "unsupported_intent"


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
    tool_skipped = [event for event in events if event["type"] == "native.tool.skipped"][0]
    assert tool_skipped["payload"]["status"] == "skipped"
    assert tool_skipped["payload"]["reason"] == "provider_not_succeeded"
    assert "native.tool.started" not in [event["type"] for event in events]
    assert [event["type"] for event in events[-2:]] == ["harness.run.failed", "session.finalized"]
    combined = result.record.jsonl_path.read_text(encoding="utf-8") + result.record.markdown_path.read_text(
        encoding="utf-8"
    )
    assert "You are the native pipy runtime bootstrap" not in combined
    assert "MODEL_OUTPUT" not in combined
    assert verify_session_archive(root=root).ok is True


def test_native_runner_finalizes_failed_tool_record_without_printing_provider_text(tmp_path, capfd):
    root = tmp_path / "sessions"
    result = HarnessRunner(
        adapter=PipyNativeAdapter(
            provider=CapturingProvider(
                final_text="MODEL_OUTPUT_SHOULD_NOT_PRINT_ON_TOOL_FAILURE",
                metadata={PROVIDER_TOOL_INTENT_METADATA_KEY: safe_noop_intent()},
            ),
            tool=FakeNoOpNativeTool(
                status=NativeToolStatus.FAILED,
                metadata={"api_token": "SECRET123", "safe_count": 1},
                error_type="FakeToolError",
                error_message="tool failed safely",
            ),
        ),
        id_factory=lambda: "native-tool-failed",
    ).run(
        RunRequest(
            agent="pipy-native",
            slug="native-tool-failure",
            command=[],
            cwd=tmp_path,
            root=root,
            goal="Native tool failure smoke",
        )
    )

    captured = capfd.readouterr()
    assert result.exit_code == 1
    assert result.status == HarnessStatus.FAILED
    assert "MODEL_OUTPUT_SHOULD_NOT_PRINT_ON_TOOL_FAILURE" not in captured.out
    events = read_jsonl(result.record.jsonl_path)
    assert "native.provider.completed" in [event["type"] for event in events]
    tool_failed = [event for event in events if event["type"] == "native.tool.failed"][0]
    assert tool_failed["payload"]["error_type"] == "FakeToolError"
    assert tool_failed["payload"]["error_message"] == "tool failed safely"
    assert tool_failed["payload"]["tool_metadata"] == {"api_token": "[REDACTED]", "safe_count": 1}
    combined = result.record.jsonl_path.read_text(encoding="utf-8") + result.record.markdown_path.read_text(
        encoding="utf-8"
    )
    assert "MODEL_OUTPUT_SHOULD_NOT_PRINT_ON_TOOL_FAILURE" not in combined
    assert "SECRET123" not in combined
    assert verify_session_archive(root=root).ok is True


def test_native_runner_records_raising_tool_as_sanitized_failure(tmp_path):
    root = tmp_path / "sessions"
    result = HarnessRunner(
        adapter=PipyNativeAdapter(
            provider=CapturingProvider(
                final_text="MODEL_OUTPUT_SHOULD_NOT_PRINT_AFTER_RAISE",
                metadata={PROVIDER_TOOL_INTENT_METADATA_KEY: safe_noop_intent()},
            ),
            tool=ExplodingTool(),
        ),
        id_factory=lambda: "native-tool-raised",
    ).run(
        RunRequest(
            agent="pipy-native",
            slug="native-tool-raised",
            command=[],
            cwd=tmp_path,
            root=root,
            goal="Native raising tool smoke",
        )
    )

    assert result.exit_code == 1
    assert result.status == HarnessStatus.FAILED
    events = read_jsonl(result.record.jsonl_path)
    assert [event["type"] for event in events if str(event["type"]).startswith("native.tool.")] == [
        "native.tool.intent.detected",
        "native.tool.started",
        "native.tool.failed",
    ]
    tool_failed = [event for event in events if event["type"] == "native.tool.failed"][0]
    assert tool_failed["payload"]["error_type"] == "RuntimeError"
    assert tool_failed["payload"]["error_message"] == "[REDACTED]"
    combined = result.record.jsonl_path.read_text(encoding="utf-8") + result.record.markdown_path.read_text(
        encoding="utf-8"
    )
    assert "MODEL_OUTPUT_SHOULD_NOT_PRINT_AFTER_RAISE" not in combined
    assert "SECRET123" not in combined
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
