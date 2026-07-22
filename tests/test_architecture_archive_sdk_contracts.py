"""Architecture contracts for the synchronous SDK and the two session stores.

These tests deliberately characterize boundaries rather than implementation
details.  The SDK remains a synchronous ``RunRequest``-in/``RunResult``-out
surface, while the raw native product session and the metadata-only
``pipy-session`` workflow archive remain separate stores with different
privacy contracts.
"""

from __future__ import annotations

import inspect
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import get_type_hints

import pytest

from pipy_harness import sdk
from pipy_harness.adapters import PipyNativeToolReplAdapter
from pipy_harness.models import HarnessStatus, RunRequest, RunResult
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)
from pipy_harness.runner import HarnessRunner


_PRIVATE_PROMPT = "PIPY_PRIVATE_PROMPT_6f2b6d6f"
_PRIVATE_MODEL_OUTPUT = "PIPY_PRIVATE_MODEL_OUTPUT_895ef3f1"
_PRIVATE_TOOL_ARGUMENT = "PIPY_PRIVATE_TOOL_ARGUMENT_4e5660f7"
_PRIVATE_TOOL_OUTPUT = "PIPY_PRIVATE_TOOL_OUTPUT_36c48c20"
_PROHIBITED_ARCHIVE_MARKERS = (
    _PRIVATE_PROMPT,
    _PRIVATE_MODEL_OUTPUT,
    _PRIVATE_TOOL_ARGUMENT,
    _PRIVATE_TOOL_OUTPUT,
)

# The metadata-only workflow archive for a native product run emits only these
# lifecycle/summary event types.  None of them is a per-message, per-assistant,
# per-tool, or per-turn content record, so the archive stays counts-only and no
# full-content crossover from the raw native product session is possible.
_ARCHIVE_METADATA_ONLY_EVENT_TYPES = frozenset(
    {
        "session.started",
        "capture.limitations",
        "native.workspace_context.loaded",
        "native.session.compacted",
        "native.session.resumed",
        "harness.run.started",
        "harness.run.completed",
        "harness.run.aborted",
        "harness.run.adapter_failed",
        "harness.run.exception",
        "session.finalized",
    }
)
_PROHIBITED_ARCHIVE_EVENT_TYPE_PREFIXES = (
    "message.",
    "assistant.",
    "tool.",
    "turn.",
)


@pytest.fixture(autouse=True)
def _isolated_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep SDK and product-session runs independent of user configuration."""

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))


def _jsonl_events(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _assert_private_markers_present(text: str) -> None:
    if not all(marker in text for marker in _PROHIBITED_ARCHIVE_MARKERS):
        raise AssertionError("raw native product session omitted private turn content")


def _assert_private_markers_absent(*texts: str) -> None:
    if any(marker in text for text in texts for marker in _PROHIBITED_ARCHIVE_MARKERS):
        # Keep prohibited bodies out of assertion output as well as the archive.
        raise AssertionError(
            "metadata-only workflow surface leaked private turn content"
        )


def test_sdk_surface_is_synchronous_streaming_and_returns_a_finalized_result(
    tmp_path: Path,
) -> None:
    expected_exports = {
        "CapturePolicy",
        "DEFAULT_NATIVE_AGENT",
        "DEFAULT_NATIVE_SLUG",
        "HarnessRunner",
        "HarnessStatus",
        "ProviderPort",
        "RunRequest",
        "RunResult",
        "StreamChunkSink",
        "make_native_run_request",
        "run_native",
    }
    assert set(sdk.__all__) == expected_exports
    assert len(sdk.__all__) == len(expected_exports)

    signature = inspect.signature(sdk.run_native)
    hints = get_type_hints(sdk.run_native)
    assert tuple(signature.parameters) == ("request", "provider", "stream_sink")
    assert hints["request"] is RunRequest
    assert hints["return"] is RunResult
    assert inspect.iscoroutinefunction(sdk.run_native) is False

    trace: list[tuple[str, str]] = []
    provider = FakeNativeProvider(
        programmable_text_chunks=("sdk-stream-one", "sdk-stream-two"),
    )
    request = sdk.make_native_run_request(
        goal="bounded SDK architecture characterization",
        cwd=tmp_path,
        root=tmp_path / "workflow-archive",
    )

    result = sdk.run_native(
        request,
        provider=provider,
        stream_sink=lambda chunk: trace.append(("chunk", chunk)),
    )
    trace.append(("caller", "returned"))

    assert trace == [
        ("chunk", "sdk-stream-one"),
        ("chunk", "sdk-stream-two"),
        ("caller", "returned"),
    ]
    assert isinstance(result, RunResult)
    assert result.status is HarnessStatus.SUCCEEDED
    assert result.exit_code == 0
    assert result.record.jsonl_path.exists()
    assert result.record.markdown_path is not None
    assert result.record.markdown_path.exists()
    assert ".in-progress" not in result.record.jsonl_path.parts

    events = _jsonl_events(result.record.jsonl_path)
    assert events[-1]["type"] == "session.finalized"
    assert events[-1]["run_id"] == result.run_id


@dataclass(frozen=True, slots=True)
class _PrivateOutputTool:
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="private_output",
            description="Return a private fixture body.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string", "maxLength": 1024}},
                "required": ["text"],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        del context
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=_PRIVATE_TOOL_OUTPUT,
            provider_correlation_id=request.provider_correlation_id,
        )


def test_raw_native_product_session_is_distinct_from_metadata_workflow_archive(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    native_tree = NativeSessionTree.create(
        workspace,
        session_dir=tmp_path / "private-native-sessions",
    )
    provider = FakeNativeProvider(
        final_text=_PRIVATE_MODEL_OUTPUT,
        supports_tool_calls=True,
        programmable_tool_calls=(
            (
                ProviderToolCall(
                    provider_correlation_id="private-call",
                    tool_name="private_output",
                    arguments_json=json.dumps({"text": _PRIVATE_TOOL_ARGUMENT}),
                ),
            ),
            (),
        ),
    )
    adapter = PipyNativeToolReplAdapter(
        provider=provider,
        tool_registry={"private_output": _PrivateOutputTool()},
        input_stream=io.StringIO(f"{_PRIVATE_PROMPT}\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
        native_session=native_tree,
        settings_manager=SettingsManager.for_workspace(
            workspace,
            home_dir=tmp_path / "home",
            project_trusted=False,
        ),
    )
    request = RunRequest(
        agent="pipy-native",
        slug="architecture-store-boundary",
        command=[],
        cwd=workspace,
        goal="native session storage architecture characterization",
        root=tmp_path / "workflow-archive",
    )

    result = HarnessRunner(
        adapter=adapter,
        id_factory=lambda: "architecture-store-boundary",
    ).run(request)

    # Check the metadata copy before value assertions so pytest assertion
    # introspection can never print a body-bearing mapping or future result
    # field on failure.
    result_metadata = json.dumps(result.metadata, sort_keys=True)
    result_repr = repr(result)
    _assert_private_markers_absent(result_metadata, result_repr)

    assert result.status is HarnessStatus.SUCCEEDED
    assert isinstance(result.metadata, dict)
    assert result.metadata["tool_invocation_count"] == 1
    assert native_tree.path is not None
    _assert_private_markers_present(native_tree.path.read_text(encoding="utf-8"))

    archive_jsonl = result.record.jsonl_path.read_text(encoding="utf-8")
    assert result.record.markdown_path is not None
    archive_markdown = result.record.markdown_path.read_text(encoding="utf-8")
    _assert_private_markers_absent(
        archive_jsonl,
        archive_markdown,
    )

    archive_events = _jsonl_events(result.record.jsonl_path)
    assert archive_events[-1]["type"] == "session.finalized"
    assert archive_events[-1]["run_id"] == "architecture-store-boundary"


def _run_private_native_product_session(
    tmp_path: Path,
) -> tuple[NativeSessionTree, RunResult]:
    """Drive one real native product run whose every turn carries a sentinel."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    native_tree = NativeSessionTree.create(
        workspace,
        session_dir=tmp_path / "private-native-sessions",
    )
    provider = FakeNativeProvider(
        final_text=_PRIVATE_MODEL_OUTPUT,
        supports_tool_calls=True,
        programmable_tool_calls=(
            (
                ProviderToolCall(
                    provider_correlation_id="private-call",
                    tool_name="private_output",
                    arguments_json=json.dumps({"text": _PRIVATE_TOOL_ARGUMENT}),
                ),
            ),
            (),
        ),
    )
    adapter = PipyNativeToolReplAdapter(
        provider=provider,
        tool_registry={"private_output": _PrivateOutputTool()},
        input_stream=io.StringIO(f"{_PRIVATE_PROMPT}\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
        native_session=native_tree,
        settings_manager=SettingsManager.for_workspace(
            workspace,
            home_dir=tmp_path / "home",
            project_trusted=False,
        ),
    )
    request = RunRequest(
        agent="pipy-native",
        slug="architecture-counts-only",
        command=[],
        cwd=workspace,
        goal="native session storage counts-only characterization",
        root=tmp_path / "workflow-archive",
    )
    result = HarnessRunner(
        adapter=adapter,
        id_factory=lambda: "architecture-counts-only",
    ).run(request)
    return native_tree, result


def test_metadata_workflow_archive_stays_counts_only_after_a_real_run(
    tmp_path: Path,
) -> None:
    native_tree, result = _run_private_native_product_session(tmp_path)

    assert result.status is HarnessStatus.SUCCEEDED

    # The raw native product session holds the full prompt/assistant/tool-content
    # sentinels, so this run genuinely exercised private content end to end.
    assert native_tree.path is not None
    _assert_private_markers_present(native_tree.path.read_text(encoding="utf-8"))

    # The metadata-only workflow archive carries only fixed lifecycle/summary
    # event types — no per-message, per-assistant, per-tool, or per-turn content
    # record — so it stays counts-only with no crossover.
    archive_events = _jsonl_events(result.record.jsonl_path)
    observed_types = {str(event["type"]) for event in archive_events}
    assert observed_types <= _ARCHIVE_METADATA_ONLY_EVENT_TYPES
    # Guard the allowlist constant itself, not the observed subset.  Once the
    # subset check above passes, every observed type is one of the 11 allowlist
    # entries and none of those carries a prohibited prefix, so a prohibited
    # check over ``observed_types`` would be vacuous.  Asserting on the allowlist
    # keeps the guard non-vacuous: it fails if the metadata-only allowlist is
    # ever widened to admit a per-message/assistant/tool/turn content record.
    assert not any(
        allowed_type.startswith(_PROHIBITED_ARCHIVE_EVENT_TYPE_PREFIXES)
        for allowed_type in _ARCHIVE_METADATA_ONLY_EVENT_TYPES
    )

    # Every archive event body (summary and metadata payload) is sentinel-free,
    # and so is the human-readable markdown summary.
    for event in archive_events:
        _assert_private_markers_absent(json.dumps(event, sort_keys=True))
    assert result.record.markdown_path is not None
    _assert_private_markers_absent(
        result.record.markdown_path.read_text(encoding="utf-8"),
    )

    assert archive_events[-1]["type"] == "session.finalized"
    assert archive_events[-1]["run_id"] == "architecture-counts-only"
