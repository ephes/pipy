"""Characterization contracts for the current native execution modes.

These tests intentionally describe the observable ordering shared by the real
tool loop, extensions, and the JSON/RPC adapters.  They are a compact migration
guard for the architecture work: future internal event types may replace the
current dictionaries, but these mode-level traces must remain stable unless a
separate behavior change deliberately updates the contract.
"""

from __future__ import annotations

import io
import json
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.adapters.native import CodingSessionAdapter
from pipy_harness.native.agent import (
    AgentEvent,
    AssistantTextDelta,
    FollowUpConsumed,
    ProductContent,
    SteeringConsumed,
)
from pipy_harness.native.automation.rpc import NativeRpcServer
from pipy_harness.native.automation.run_modes import run_json_mode
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.fake import AutomationFakeProvider, FakeNativeProvider
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolPort,
    ToolRequest,
)


@pytest.fixture(autouse=True)
def _isolate_global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep host settings and globally installed extensions out of event traces."""

    config_home = tmp_path / "empty-global-config"
    config_home.mkdir()
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config_home))


class _CollectingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))


class _CanonicalCollectingSink:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


class _ReleaseAfterSteerResponse(io.BytesIO):
    """Release a blocked provider once both queue commands were dispatched."""

    def __init__(self, release: threading.Event) -> None:
        super().__init__()
        self._release = release

    def write(self, data: Any, /) -> int:
        written = super().write(data)
        if not self._release.is_set():
            # Parse only LF-terminated raw-byte records. A transport write may
            # end mid-codepoint; the incomplete tail becomes valid on a later
            # write and must not make the queue-order harness fail early.
            complete_lines = self.getvalue().split(b"\n")[:-1]
            for line in complete_lines:
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if (
                    isinstance(record, dict)
                    and record.get("id") == "s"
                    and record.get("type") == "response"
                ):
                    self._release.set()
                    break
        return written


class _BlockingFirstProvider:
    name = "fake"
    model_id = "fake-tools"
    supports_tool_calls = True

    def __init__(self, release: threading.Event) -> None:
        self._release = release
        self._delegate = AutomationFakeProvider()
        self._calls = 0
        self.release_observed = False

    def complete(self, request: Any, **kwargs: Any) -> Any:
        self._calls += 1
        if self._calls == 1:
            self.release_observed = self._release.wait(timeout=5.0)
        return self._delegate.complete(request, **kwargs)


def test_rpc_release_writer_tolerates_split_utf8_jsonl() -> None:
    release = threading.Event()
    writer = _ReleaseAfterSteerResponse(release)
    encoded = (
        json.dumps(
            {"note": "queue ☕", "id": "s", "type": "response"},
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n"
    )
    split_at = encoded.index("☕".encode("utf-8")) + 1

    writer.write(encoded[:split_at])
    assert not release.is_set()

    writer.write(encoded[split_at:])
    assert release.is_set()


@dataclass(frozen=True, slots=True)
class _EchoTool:
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="echo",
            description="Echo a required text argument.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        del context
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=str(request.arguments["text"]),
            provider_correlation_id=request.provider_correlation_id,
        )


def _run_events(
    tmp_path: Path,
    *,
    provider: FakeNativeProvider,
    tools: dict[str, ToolPort] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    sink = _CollectingSink()
    session = CodingSession(
        provider=provider,
        tool_registry=tools or {},
        automation_observer=sink,
    )
    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("prompt\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    return sink.events, result.malformed_argument_count


def _event_trace(events: Iterable[dict[str, Any]]) -> list[str]:
    """Reduce payload-heavy events to their ordered architectural roles."""

    trace: list[str] = []
    for event in events:
        event_type = str(event["type"])
        if event_type in {"message_start", "message_end"}:
            role = event.get("message", {}).get("role", "?")
            trace.append(f"{event_type}:{role}")
        elif event_type == "message_update":
            update_type = event.get("assistantMessageEvent", {}).get("type", "?")
            trace.append(f"message_update:{update_type}")
        elif event_type.startswith("tool_execution_"):
            suffix = ":error" if event.get("isError") is True else ""
            trace.append(f"{event_type}:{event.get('toolName', '?')}{suffix}")
        else:
            trace.append(event_type)
    return trace


def test_tool_loop_exposes_one_mode_neutral_canonical_trace(tmp_path: Path) -> None:
    canonical = _CanonicalCollectingSink()
    automation = _CollectingSink()
    session = CodingSession(
        provider=FakeNativeProvider(
            supports_tool_calls=True,
            programmable_text_chunks=("hello", " world"),
        ),
        tool_registry={},
        automation_observer=automation,
        agent_event_sink=canonical,
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("prompt\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status.value == "succeeded"
    assert [type(event).__name__ for event in canonical.events] == [
        "AgentRunStarted",
        "TurnStarted",
        "MessageStarted",
        "MessageCompleted",
        "MessageStarted",
        "AssistantTextDelta",
        "AssistantTextDelta",
        "UsageUpdated",
        "MessageCompleted",
        "TurnCompleted",
        "AgentRunCompleted",
    ]
    deltas = [
        event.delta
        for event in canonical.events
        if isinstance(event, AssistantTextDelta)
    ]
    assert deltas == [ProductContent("hello"), ProductContent(" world")]
    assert _event_trace(automation.events) == [
        "agent_start",
        "turn_start",
        "message_start:user",
        "message_end:user",
        "message_start:assistant",
        "message_update:text_delta",
        "message_update:text_delta",
        "message_end:assistant",
        "turn_end",
        "agent_end",
    ]


@pytest.mark.parametrize(
    ("tool_call", "tool_registry", "expected_middle", "malformed_count"),
    [
        (None, {}, [], 0),
        (
            ProviderToolCall(
                provider_correlation_id="call-ok",
                tool_name="echo",
                arguments_json='{"text":"hello"}',
            ),
            {"echo": _EchoTool()},
            [
                "tool_execution_start:echo",
                "tool_execution_end:echo",
                "turn_end",
                "turn_start",
                "message_start:assistant",
                "message_end:assistant",
            ],
            0,
        ),
        (
            ProviderToolCall(
                provider_correlation_id="call-bad",
                tool_name="echo",
                arguments_json="{not-json",
            ),
            {"echo": _EchoTool()},
            [
                "tool_execution_start:echo",
                "tool_execution_end:echo:error",
                "turn_end",
                "turn_start",
                "message_start:assistant",
                "message_end:assistant",
            ],
            1,
        ),
    ],
)
def test_tool_loop_order_contract(
    tmp_path: Path,
    tool_call: ProviderToolCall | None,
    tool_registry: dict[str, ToolPort],
    expected_middle: list[str],
    malformed_count: int,
) -> None:
    scripts = ((tool_call,), ()) if tool_call is not None else ()
    events, actual_malformed = _run_events(
        tmp_path,
        provider=FakeNativeProvider(
            supports_tool_calls=True,
            programmable_tool_calls=scripts,
            final_text="done",
        ),
        tools=tool_registry,
    )

    expected = [
        "agent_start",
        "turn_start",
        "message_start:user",
        "message_end:user",
        "message_start:assistant",
        "message_end:assistant",
        *expected_middle,
        "turn_end",
        "agent_end",
    ]
    assert _event_trace(events) == expected
    assert actual_malformed == malformed_count


def test_json_mode_preserves_real_loop_order_with_mode_boundaries(
    tmp_path: Path,
) -> None:
    adapter = CodingSessionAdapter(
        provider=FakeNativeProvider(
            supports_tool_calls=True,
            programmable_text_chunks=("hello", " world"),
        )
    )
    tree = NativeSessionTree.create(tmp_path, persist=False)
    stdout = io.BytesIO()

    exit_code = run_json_mode(
        adapter=adapter,
        prompt="prompt",
        cwd=tmp_path,
        native_session=tree,
        stdout_buffer=stdout,
        error_stream=io.StringIO(),
    )

    records = [
        json.loads(line) for line in stdout.getvalue().decode("utf-8").splitlines()
    ]
    assert exit_code == 0
    assert records[0]["type"] == "session"
    assert _event_trace(records[1:]) == [
        "agent_start",
        "turn_start",
        "message_start:user",
        "message_end:user",
        "message_start:assistant",
        "message_update:text_delta",
        "message_update:text_delta",
        "message_end:assistant",
        "turn_end",
        "agent_end",
        "agent_settled",
    ]


def test_rpc_queues_steering_before_follow_up_and_settles_once(tmp_path: Path) -> None:
    commands = "\n".join(
        json.dumps(command)
        for command in (
            {"id": "p", "type": "prompt", "message": "ROOT"},
            {"id": "f", "type": "follow_up", "message": "FOLLOW"},
            {"id": "s", "type": "steer", "message": "STEER"},
        )
    )
    release = threading.Event()
    stdout = _ReleaseAfterSteerResponse(release)
    provider = _BlockingFirstProvider(release)
    canonical = _CanonicalCollectingSink()
    server = NativeRpcServer(
        adapter=CodingSessionAdapter(
            provider=provider,
            agent_event_sink=canonical,
        ),
        cwd=tmp_path,
        native_session=NativeSessionTree.create(tmp_path, persist=False),
        stdin=io.StringIO(commands + "\n"),
        stdout_buffer=stdout,
        error_stream=io.StringIO(),
    )

    assert server.run() == 0

    records = [
        json.loads(line) for line in stdout.getvalue().decode("utf-8").splitlines()
    ]
    user_prompts = [
        "".join(block.get("text", "") for block in record["message"]["content"])
        for record in records
        if record.get("type") == "message_start"
        and record.get("message", {}).get("role") == "user"
    ]
    event_types = [record.get("type") for record in records]

    # RPC intake dispatches commands serially, prompt acceptance marks the turn
    # active synchronously, and reservation always drains steering before follow-up.
    assert provider.release_observed, (
        "queue-order harness did not observe the RPC steer response"
    )
    assert user_prompts == ["ROOT", "STEER", "FOLLOW"]
    assert event_types.count("agent_start") == 3
    assert event_types.count("agent_end") == 3
    assert event_types.count("agent_settled") == 1
    assert event_types[-2:] == ["agent_end", "agent_settled"]
    consumed = [
        event
        for event in canonical.events
        if isinstance(event, (SteeringConsumed, FollowUpConsumed))
    ]
    assert consumed == [
        SteeringConsumed(ProductContent("STEER")),
        FollowUpConsumed(ProductContent("FOLLOW")),
    ]


def test_rpc_abort_reaches_provider_and_closes_the_event_lifecycle(
    tmp_path: Path,
) -> None:
    commands = "\n".join(
        json.dumps(command)
        for command in (
            {"id": "p", "type": "prompt", "message": "BLOCK slow"},
            {"id": "a", "type": "abort"},
        )
    )
    provider = AutomationFakeProvider(block_timeout_seconds=30.0)
    stdout = io.BytesIO()
    server = NativeRpcServer(
        adapter=CodingSessionAdapter(provider=provider),
        cwd=tmp_path,
        native_session=NativeSessionTree.create(tmp_path, persist=False),
        stdin=io.StringIO(commands + "\n"),
        stdout_buffer=stdout,
        error_stream=io.StringIO(),
    )

    assert server.run() == 0

    records = [
        json.loads(line) for line in stdout.getvalue().decode("utf-8").splitlines()
    ]
    events = [record for record in records if record.get("type") != "response"]
    assistant_end = next(
        record
        for record in events
        if record.get("type") == "message_end"
        and record.get("message", {}).get("role") == "assistant"
    )

    assert provider.cancel_observed
    assert _event_trace(events) == [
        "agent_start",
        "turn_start",
        "message_start:user",
        "message_end:user",
        "message_start:assistant",
        "message_end:assistant",
        "turn_end",
        "agent_end",
        "agent_settled",
    ]
    assert assistant_end["message"]["content"] == []


def test_extension_lifecycle_brackets_queued_continuation(tmp_path: Path) -> None:
    proof = tmp_path / "lifecycle.jsonl"
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "architecture_contract.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"PROOF = Path({str(proof)!r})\n"
        "queued = False\n"
        "def activate(api):\n"
        "    def observe(event, ctx):\n"
        "        global queued\n"
        "        with PROOF.open('a') as handle:\n"
        "            handle.write(json.dumps({'type': event.name}) + '\\n')\n"
        "        if event.name == 'agent_end' and not queued:\n"
        "            queued = True\n"
        "            api.send_user_message('extension follow-up')\n"
        "    for name in ('session_start', 'agent_start', 'turn_start',\n"
        "                 'turn_end', 'agent_end', 'agent_settled',\n"
        "                 'session_shutdown'):\n"
        "        api.on(name, observe)\n",
        encoding="utf-8",
    )
    session = CodingSession(
        provider=FakeNativeProvider(supports_tool_calls=True, final_text="done"),
        tool_registry={},
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("prompt\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    lifecycle = [json.loads(line)["type"] for line in proof.read_text().splitlines()]
    assert lifecycle == [
        "session_start",
        "agent_start",
        "turn_start",
        "turn_end",
        "agent_end",
        "agent_start",
        "turn_start",
        "turn_end",
        "agent_end",
        "agent_settled",
        "session_shutdown",
    ]
