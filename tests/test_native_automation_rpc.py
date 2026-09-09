"""Integration tests for the `--mode rpc` JSONL protocol server.

Drives :class:`NativeRpcServer` over real OS pipes with a deterministic,
tool-capable fake provider, exercising the Pi command/response/event vocabulary:
async ``prompt`` with a streamed event sequence, ``get_state``/``get_messages``/
``get_session_stats``, ``bash``, mid-turn ``steer`` (``queue_update``) and
``abort``, ``set_session_name``, unknown-command and parse-error envelopes,
and clean EOF shutdown.
"""

from __future__ import annotations

import io
import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Never, cast

import pytest

import pipy_harness.native.automation.rpc as rpc_module
import pipy_harness.native.repl.loop_step as loop_step_module
import pipy_harness.native.repl.wiring as loop_module
from pipy_harness.adapters.native import CodingSessionAdapter
from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import (
    AgentEvent,
    AgentRunStarted,
    AgentUserMessage,
    FollowUpConsumed,
    ProductContent,
    SteeringConsumed,
)
from pipy_harness.native.agent.runtime_ports import (
    AgentQueuedInput,
)
from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.automation.jsonl import JsonlLineBuffer
from pipy_harness.native.automation.rpc import NativeRpcServer, _WakeChannel
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.catalog_state import ProviderCatalogState
from pipy_harness.native.coding.session_controller import (
    _NativeControlFailed,
    _NativeSessionControlBridge,
)
from pipy_harness.native.command_sandbox import (
    CommandPolicy,
    CommandResult,
    CommandStatus,
)
from pipy_harness.native.fake import AutomationFakeProvider
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import (
    PreparedProviderCompletion,
    ProviderAttemptAllowance,
    ProviderPort,
    StreamChunkSink,
)
from pipy_harness.native.repl.provider_selection import (
    RpcConfigurationResult,
    RpcConfigurationSnapshot,
    RpcModelCycleResult,
)
from pipy_harness.native.repl.session_transition import CanonicalSessionLeaseRegistry
from pipy_harness.native.repl_state import (
    ModelRuntime,
    NativeModelSelection,
    NativeReplProviderState,
)
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tools import ToolPort


class _CanonicalCollectingSink:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


class _BlockingFirstAutomationProvider:
    """Hold the first provider call while the RPC reader queues continuations."""

    def __init__(self) -> None:
        self._delegate = AutomationFakeProvider()
        self._release = threading.Event()
        self.requests: list[ProviderRequest] = []

    @property
    def name(self) -> str:
        return self._delegate.name

    @property
    def model_id(self) -> str:
        return self._delegate.model_id

    @property
    def supports_tool_calls(self) -> bool:
        return self._delegate.supports_tool_calls

    def release(self) -> None:
        self._release.set()

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        self.requests.append(request)
        if len(self.requests) == 1 and not self._release.wait(timeout=5.0):
            raise AssertionError("RPC queue harness did not release the provider")
        return self._delegate.complete(
            request,
            stream_sink=stream_sink,
            reasoning_sink=reasoning_sink,
            cancel_token=cancel_token,
        )


class _BlockingCompactionAutomationProvider:
    """Pause only the private no-tool summary request until abort reaches it."""

    def __init__(self) -> None:
        self._delegate = AutomationFakeProvider()
        self._block_summaries = False
        self._fail_summaries = False
        self.summary_entered = threading.Event()
        self.release_summary = threading.Event()

    @property
    def name(self) -> str:
        return self._delegate.name

    @property
    def model_id(self) -> str:
        return self._delegate.model_id

    @property
    def supports_tool_calls(self) -> bool:
        return self._delegate.supports_tool_calls

    def block_summaries(self) -> None:
        self._block_summaries = True

    def fail_summaries(self) -> None:
        self._fail_summaries = True

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        if self._fail_summaries and not request.available_tools:
            raise RuntimeError("private summary failed")
        if self._block_summaries and not request.available_tools:
            self.summary_entered.set()
            assert cancel_token is not None
            while not cancel_token.cancelled and not self.release_summary.wait(0.005):
                time.sleep(0.005)
            cancel_token.raise_if_cancelled()
        return self._delegate.complete(
            request,
            stream_sink=stream_sink,
            reasoning_sink=reasoning_sink,
            cancel_token=cancel_token,
        )


class _RetryingAutomationProvider:
    """Expose one transient first request, then a successful successor."""

    name = "fixture"
    model_id = "fixture-model"
    supports_tool_calls = True

    def __init__(self) -> None:
        self.prepared = 0
        self.attempts: list[tuple[int, int]] = []

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        del request
        raise AssertionError("managed RPC retry must use the prepared capability")

    def prepare_completion(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> PreparedProviderCompletion:
        del stream_sink, reasoning_sink, cancel_token
        self.prepared += 1
        request_index = self.prepared
        provider = self

        class _Handle:
            def complete_attempt(
                self, allowance: ProviderAttemptAllowance
            ) -> ProviderResult:
                provider.attempts.append((request_index, allowance.attempt))
                now = datetime.now(UTC)
                if request_index == 1 and allowance.attempt == 1:
                    return ProviderResult(
                        status=HarnessStatus.FAILED,
                        provider_name=provider.name,
                        model_id=provider.model_id,
                        started_at=now,
                        ended_at=now,
                        error_type="TransientError",
                        error_message="retry later",
                        metadata={"retryable": True, "progress": "none"},
                    )
                return ProviderResult(
                    status=HarnessStatus.SUCCEEDED,
                    provider_name=provider.name,
                    model_id=provider.model_id,
                    started_at=now,
                    ended_at=now,
                    final_text=f"done:{request.user_prompt}",
                )

        return _Handle()


class _RecordingConfiguredProvider:
    """Construction seam proving the live coding binding serves the next turn."""

    def __init__(self, provider_name: str, model_id: str, thinking: str | None) -> None:
        self._provider_name = provider_name
        self._model_id = model_id
        self.thinking = thinking
        self.requests: list[ProviderRequest] = []

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        self.requests.append(request)
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="configured answer",
        )


class _RpcClient:
    def __init__(
        self,
        tmp_path: Path,
        *,
        provider: ProviderPort | None = None,
        provider_state: NativeReplProviderState | None = None,
        tools: dict[str, ToolPort] | None = None,
        persist_tree: bool = False,
        resource_options: RuntimeResourceOptions | None = None,
    ) -> None:
        self._cwd = tmp_path
        stdin_r, self._stdin_w = os.pipe()
        self._stdout_r, stdout_w = os.pipe()
        self._stdin_read = os.fdopen(stdin_r, "r")
        self._stdin_write = os.fdopen(self._stdin_w, "w")
        self._stdout_read = os.fdopen(self._stdout_r, "rb")
        self._stdout_buffer = os.fdopen(stdout_w, "wb")
        self._error_stream = open(os.devnull, "w")

        self.canonical = _CanonicalCollectingSink()
        adapter = CodingSessionAdapter(
            tool_registry=tools,
            provider_state=provider_state,
            provider=(
                None
                if provider_state is not None
                else (
                    provider
                    if provider is not None
                    else AutomationFakeProvider(block_timeout_seconds=5.0)
                )
            ),
            agent_event_sink=self.canonical,
            resource_options=resource_options,
        )
        self.adapter = adapter
        tree = NativeSessionTree.create(tmp_path, persist=persist_tree)
        self.tree = tree
        self._server = NativeRpcServer(
            adapter=adapter,
            cwd=tmp_path,
            native_session=tree,
            stdin=self._stdin_read,
            stdout_buffer=self._stdout_buffer,
            error_stream=self._error_stream,
        )
        self._records: "queue.Queue[dict]" = queue.Queue()
        self._seen: list[dict] = []
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._server_thread = threading.Thread(target=self._server.run, daemon=True)
        self._reader.start()
        self._server_thread.start()

    def _read_stdout(self) -> None:
        buf = JsonlLineBuffer()
        while True:
            chunk = self._stdout_read.read(1)
            if chunk == b"":
                break
            for line in buf.feed(chunk.decode("utf-8")):
                self._records.put(json.loads(line))

    def send(self, command: dict) -> None:
        self._stdin_write.write(json.dumps(command) + "\n")
        self._stdin_write.flush()

    def wait_for(self, predicate, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                record = self._records.get(timeout=deadline - time.monotonic())
            except queue.Empty:
                break
            self._seen.append(record)
            if predicate(record):
                return record
        raise AssertionError(f"timed out; saw {self._seen}")

    def collect_until(self, predicate, timeout: float = 5.0) -> list[dict]:
        records: list[dict] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                record = self._records.get(timeout=deadline - time.monotonic())
            except queue.Empty:
                break
            records.append(record)
            if predicate(record):
                return records
        raise AssertionError(f"timed out; collected {records}")

    def close(self) -> int:
        self._stdin_write.close()
        self._server_thread.join(timeout=10.0)
        if self._server_thread.is_alive():
            raise AssertionError("RPC server did not stop after stdin EOF")
        self._stdout_buffer.close()
        self._reader.join(timeout=5.0)
        if self._reader.is_alive():
            raise AssertionError("RPC stdout reader did not stop after writer close")
        self._stdout_read.close()
        self._stdin_read.close()
        self._error_stream.close()
        return 0


@pytest.fixture()
def client(tmp_path: Path):
    c = _RpcClient(tmp_path)
    c._seen = []
    try:
        yield c
    finally:
        c.close()


def _provider_state_adapter(tmp_path: Path) -> CodingSessionAdapter:
    catalog = ProviderCatalogState(
        models_json_path=tmp_path / "models.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={"OPENAI_API_KEY": "sk-test"},
        openai_codex_auth_path=tmp_path / "codex.json",
    )
    state = NativeReplProviderState(
        selection=NativeModelSelection("openai", "gpt-5.5"),
        model_runtime=ModelRuntime(catalog=catalog),
        persist_defaults=False,
    )
    return CodingSessionAdapter(provider_state=state)


def test_set_thinking_level_refreshes_live_provider_binding(
    tmp_path: Path,
) -> None:
    adapter = _provider_state_adapter(tmp_path)
    client = _RpcClient(tmp_path, provider_state=adapter.provider_state)
    try:
        client.send({"id": "t", "type": "set_thinking_level", "level": "high"})
        records = client.collect_until(
            lambda record: record.get("type") == "thinking_level_changed"
        )
        assert records[-2]["command"] == "set_thinking_level"
        assert adapter.provider_state is not None
        assert adapter.provider_state.current_thinking_level() == "high"
        assert (
            getattr(client.adapter._current_provider(), "reasoning_effort", None)
            == "high"
        )
    finally:
        client.close()


def test_cycle_thinking_level_refreshes_live_provider_binding(
    tmp_path: Path,
) -> None:
    adapter = _provider_state_adapter(tmp_path)
    client = _RpcClient(tmp_path, provider_state=adapter.provider_state)
    try:
        client.send({"id": "t", "type": "cycle_thinking_level"})
        client.collect_until(
            lambda record: record.get("type") == "thinking_level_changed"
        )
        assert adapter.provider_state is not None
        assert adapter.provider_state.current_thinking_level() == "minimal"
        assert (
            getattr(client.adapter._current_provider(), "reasoning_effort", None)
            == "minimal"
        )
    finally:
        client.close()


def test_retry_commands_validate_and_use_the_native_control_port(
    tmp_path: Path,
) -> None:
    client = _RpcClient(tmp_path)
    try:
        client.send({"id": "missing", "type": "set_auto_retry"})
        missing = client.wait_for(
            lambda record: (
                record.get("type") == "response" and record.get("id") == "missing"
            )
        )
        assert missing["success"] is False

        client.send({"id": "integer", "type": "set_auto_retry", "enabled": 1})
        integer = client.wait_for(
            lambda record: (
                record.get("type") == "response" and record.get("id") == "integer"
            )
        )
        assert integer["success"] is False

        client.send({"id": "enabled", "type": "set_auto_retry", "enabled": False})
        enabled = client.wait_for(
            lambda record: (
                record.get("type") == "response" and record.get("id") == "enabled"
            )
        )
        assert enabled["success"] is True

        client.send({"id": "idle", "type": "abort_retry"})
        idle = client.wait_for(
            lambda record: (
                record.get("type") == "response" and record.get("id") == "idle"
            )
        )
        assert idle["success"] is True

        def fail_retry_write(_enabled: bool) -> bool:
            raise OSError("private settings path")

        client._server._retry = SimpleNamespace(
            abort_retry=lambda: False,
            set_enabled=fail_retry_write,
        )
        client.send({"id": "write-failed", "type": "set_auto_retry", "enabled": True})
        write_failed = client.wait_for(
            lambda record: (
                record.get("type") == "response" and record.get("id") == "write-failed"
            )
        )
        assert write_failed["success"] is False
        assert write_failed["error"] == "could not update retry policy"
        assert "private settings path" not in str(write_failed)
    finally:
        client.close()


def test_abort_retry_cancels_exact_rpc_backoff_and_next_prompt_runs(
    tmp_path: Path,
) -> None:
    provider = _RetryingAutomationProvider()
    client = _RpcClient(tmp_path, provider=provider)
    try:
        client.send({"id": "first", "type": "prompt", "message": "first"})
        records = client.collect_until(
            lambda record: record.get("type") == "auto_retry_start"
        )
        client.send({"id": "steer", "type": "steer", "message": "second"})
        records.extend(client.collect_until(lambda record: record.get("id") == "steer"))
        client.send({"id": "abort-retry", "type": "abort_retry"})
        records.extend(
            client.collect_until(lambda record: record.get("type") == "agent_settled")
        )

        abort_response = next(
            record for record in records if record.get("id") == "abort-retry"
        )
        retry_end = next(
            record for record in records if record.get("type") == "auto_retry_end"
        )
        assert abort_response["success"] is True
        assert retry_end["success"] is False
        assert any(
            record.get("type") == "message_end"
            and record["message"]["content"]
            == [{"type": "text", "text": "done:second"}]
            for record in records
        )
        assert provider.attempts == [(1, 1), (2, 1)]

        client.send({"id": "late", "type": "abort_retry"})
        assert client.wait_for(lambda record: record.get("id") == "late")["success"]
    finally:
        client.close()


def test_rpc_configuration_drives_the_actual_next_provider_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configuration must reach CodingSessionState, not only adapter lookup."""

    instances: list[_RecordingConfiguredProvider] = []

    def construct(
        _runtime: ModelRuntime,
        selection: NativeModelSelection,
        *,
        thinking_level: str | None,
        options: object,
    ) -> _RecordingConfiguredProvider:
        provider = _RecordingConfiguredProvider(
            selection.provider_name, selection.model_id, thinking_level
        )
        instances.append(provider)
        return provider

    monkeypatch.setattr(ModelRuntime, "construct", construct)
    adapter = _provider_state_adapter(tmp_path)
    assert adapter.provider_state is not None
    client = _RpcClient(tmp_path, provider_state=adapter.provider_state)
    try:
        client.send(
            {
                "id": "model",
                "type": "set_model",
                "provider": "openai",
                "modelId": "gpt-5.4",
            }
        )
        assert client.wait_for(lambda record: record.get("id") == "model")["success"]
        client.send({"id": "thinking", "type": "set_thinking_level", "level": "high"})
        client.collect_until(
            lambda record: record.get("type") == "thinking_level_changed"
        )
        client.send({"id": "prompt", "type": "prompt", "message": "use new binding"})
        client.collect_until(lambda record: record.get("type") == "agent_end")

        used = [
            provider
            for provider in instances
            if provider.model_id == "gpt-5.4" and provider.thinking == "high"
        ]
        assert len(used) == 1
        assert len(used[0].requests) == 1
        request = used[0].requests[0]
        assert (request.provider_name, request.model_id) == ("openai", "gpt-5.4")
    finally:
        client.close()


def test_batch_eof_drains_queued_followup(tmp_path: Path) -> None:
    # A batch client submits a prompt + a follow-up, then closes stdin. The
    # queued follow-up must still run before shutdown (not dropped behind EOF).
    c = _RpcClient(tmp_path)
    c._seen = []
    c.send({"id": "p", "type": "prompt", "message": "ROOT"})
    c.send({"id": "f", "type": "follow_up", "message": "SECOND"})
    exit_code = c.close()
    assert exit_code == 0

    records: list[dict] = []
    while not c._records.empty():
        records.append(c._records.get())
    user_texts = [
        "".join(b.get("text", "") for b in r["message"]["content"])
        for r in records
        if r.get("type") == "message_start"
        and r.get("message", {}).get("role") == "user"
    ]
    assert "ROOT" in user_texts
    assert "SECOND" in user_texts

    # Pi emits exactly one `agent_settled` when the agent becomes idle, after the
    # final `agent_end`. pipy runs each queued follow-up as a separate run, so run
    # A's `agent_end` reserves SECOND (no settle) and only run B's `agent_end`
    # settles to idle. There must be exactly one `agent_settled`, it must follow
    # the second `agent_end`, and none may appear between the two runs.
    types = [r.get("type") for r in records]
    assert types.count("agent_settled") == 1
    agent_end_indices = [i for i, t in enumerate(types) if t == "agent_end"]
    assert len(agent_end_indices) == 2
    settled_index = types.index("agent_settled")
    assert settled_index > agent_end_indices[1]
    assert not any(t == "agent_settled" for t in types[: agent_end_indices[1]])


def test_eof_drains_an_admitted_manual_compaction(tmp_path: Path) -> None:
    client = _RpcClient(tmp_path)
    client._seen = []
    for index in range(4):
        client.send({"id": f"p{index}", "type": "prompt", "message": f"ROOT-{index}"})
        client.collect_until(lambda record: record.get("type") == "agent_settled")
    client.send({"id": "compact", "type": "compact"})
    assert client.close() == 0
    records: list[dict] = []
    while not client._records.empty():
        records.append(client._records.get())
    assert any(record.get("type") == "compaction_end" for record in records)
    response = next(record for record in records if record.get("id") == "compact")
    assert response["success"] is True


def test_agent_settled_emitted_after_idle(client) -> None:
    client.send({"id": "r1", "type": "prompt", "message": "ROOT"})
    records = client.collect_until(lambda r: r.get("type") == "agent_settled")

    types = [r["type"] for r in records]
    # `agent_settled` is the idle boundary: it is the final line and comes
    # strictly after the run's `agent_end`, with nothing between them.
    assert types[-1] == "agent_settled"
    assert types[-2:] == ["agent_end", "agent_settled"]
    # Pi's `agent_settled` carries no payload fields.
    assert records[-1] == {"type": "agent_settled"}


def test_post_end_queue_update_precedes_promoted_agent_start(client) -> None:
    client.send({"id": "p", "type": "prompt", "message": "ROOT"})
    client.send({"id": "f", "type": "follow_up", "message": "NEXT"})
    records = client.collect_until(lambda record: record.get("type") == "agent_settled")
    types = [record["type"] for record in records]
    first_end = types.index("agent_end")
    next_start = types.index("agent_start", first_end + 1)
    assert types[first_end + 1 : next_start] == ["queue_update"]


def test_wake_channel_carries_only_wake_and_eof() -> None:
    channel = _WakeChannel()
    channel.wake()
    assert channel.readline() == ""
    channel.signal_eof()
    assert channel.readline() == ""


def test_startup_failure_rejects_all_provisional_control_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The reader never handles input until a native control publishes ready."""

    stdout = io.BytesIO()
    adapter = CodingSessionAdapter(provider=AutomationFakeProvider())
    server = NativeRpcServer(
        adapter=adapter,
        cwd=tmp_path,
        native_session=NativeSessionTree.create(tmp_path, persist=False),
        stdin=io.StringIO(
            "\n".join(
                json.dumps(command)
                for command in (
                    {"id": "p", "type": "prompt", "message": "ROOT"},
                    {"id": "s", "type": "steer", "message": "STEER"},
                    {"id": "f", "type": "follow_up", "message": "FOLLOW"},
                    {"id": "a", "type": "abort"},
                )
            )
            + "\n"
        ),
        stdout_buffer=stdout,
        error_stream=io.StringIO(),
    )
    failure = LookupError("startup failed before ready")

    def fail_run(*_args: object, **_kwargs: object) -> None:
        server._bridge.publish_failure(failure)
        raise failure

    monkeypatch.setattr(adapter, "run", fail_run)

    assert server.run() == 1
    assert stdout.getvalue() == b""
    outcome = server._bridge.wait_ready(0)
    assert outcome is not None and type(outcome).__name__ == "_NativeControlFailed"
    with pytest.raises(RuntimeError, match="not ready"):
        server._bridge.admit_prompt(ProductContent("must not admit"))


def test_prepare_failure_publishes_startup_failure_before_command_intake(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A preparation error unblocks startup without reading a provisional prompt."""

    class UnreadableInput(io.StringIO):
        reads = 0

        def readline(self, size: int | None = -1) -> Never:
            del size
            self.reads += 1
            raise AssertionError("RPC command intake must wait for native readiness")

    stdin = UnreadableInput()
    stdout = io.BytesIO()
    adapter = CodingSessionAdapter(provider=AutomationFakeProvider())
    server = NativeRpcServer(
        adapter=adapter,
        cwd=tmp_path,
        native_session=NativeSessionTree.create(tmp_path, persist=False),
        stdin=stdin,
        stdout_buffer=stdout,
        error_stream=io.StringIO(),
    )
    failure = LookupError("prepare failed before ready")

    def fail_prepare(_request: object) -> object:
        raise failure

    monkeypatch.setattr(adapter, "prepare", fail_prepare)

    started = time.monotonic()
    assert server.run() == 1
    assert time.monotonic() - started < 2.0
    assert stdin.reads == 0
    assert stdout.getvalue() == b""
    outcome = server._bridge.wait_ready(0)
    assert isinstance(outcome, _NativeControlFailed)
    assert outcome.error is failure
    with pytest.raises(RuntimeError, match="not ready"):
        server._bridge.admit_prompt(ProductContent("must not admit"))


def test_prompt_emits_correlated_success_then_event_sequence(client) -> None:
    client.send({"id": "r1", "type": "prompt", "message": "ROOT"})
    records = client.collect_until(lambda r: r.get("type") == "agent_end")

    # The correlated prompt success precedes the event stream.
    success = records[0]
    assert success == {
        "id": "r1",
        "type": "response",
        "command": "prompt",
        "success": True,
    }

    types = [r["type"] for r in records[1:]]
    assert types[0] == "agent_start"
    assert "turn_start" in types
    assert "message_start" in types
    assert types.count("message_update") >= 1
    assert "message_end" in types
    assert types[-1] == "agent_end"

    agent_end = records[-1]
    assert agent_end["willRetry"] is False

    # Streamed text deltas concatenate to the assistant's final text.
    deltas = "".join(
        r["assistantMessageEvent"]["delta"]
        for r in records
        if r["type"] == "message_update"
    )
    assert deltas == "SEEN:ROOT"
    message_end = next(
        r
        for r in records
        if r["type"] == "message_end" and r["message"]["role"] == "assistant"
    )
    assert message_end["message"]["content"] == [{"type": "text", "text": "SEEN:ROOT"}]


def test_get_state_and_get_messages(client) -> None:
    client.send({"id": "p", "type": "prompt", "message": "ROOT"})
    client.collect_until(lambda r: r.get("type") == "agent_end")

    client.send({"id": "s", "type": "get_state"})
    state = client.wait_for(
        lambda r: r.get("type") == "response" and r.get("id") == "s"
    )
    assert state["success"] is True
    data = state["data"]
    assert data["isStreaming"] is False
    assert data["steeringMode"] == "all"
    assert data["sessionId"]
    assert data["messageCount"] >= 2

    client.send({"id": "m", "type": "get_messages"})
    msgs = client.wait_for(lambda r: r.get("id") == "m")
    roles = [m["role"] for m in msgs["data"]["messages"]]
    assert "user" in roles and "assistant" in roles


def test_manual_compact_uses_native_worker_and_correlates_after_end(client) -> None:
    for index in range(4):
        client.send({"id": f"p{index}", "type": "prompt", "message": f"ROOT-{index}"})
        client.collect_until(lambda record: record.get("type") == "agent_settled")

    client.send(
        {"id": "compact", "type": "compact", "customInstructions": "keep the task"}
    )
    records = client.collect_until(lambda record: record.get("id") == "compact")
    types = [record["type"] for record in records]
    start = types.index("compaction_start")
    end = types.index("compaction_end")
    assert start < end < len(records) - 1
    response = records[-1]
    assert response["success"] is True
    assert response["data"]["summary"]
    assert response["data"]["firstKeptEntryId"] is None
    assert not any(record.get("type") == "agent_start" for record in records[start:])


def test_manual_compact_persists_exact_origin_and_reopens(tmp_path: Path) -> None:
    client = _RpcClient(tmp_path, persist_tree=True)
    try:
        for index in range(4):
            client.send(
                {"id": f"p{index}", "type": "prompt", "message": f"ROOT-{index}"}
            )
            client.collect_until(lambda record: record.get("type") == "agent_settled")
        client.send({"id": "compact", "type": "compact"})
        response = client.collect_until(lambda record: record.get("id") == "compact")[
            -1
        ]
        assert response["success"] is True
        origin = response["data"]["firstKeptEntryId"]
        assert isinstance(origin, str) and origin
        tree_path = client.tree.path
        assert tree_path is not None
        reopened = NativeSessionTree.open(tree_path).build_coding_context()
        assert reopened.prior_summary == response["data"]["summary"]
        assert origin in reopened.entry_ids
    finally:
        client.close()


def test_manual_compact_projects_accepted_persistence_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = NativeSessionTree.append_compaction

    def fail_append(self: NativeSessionTree, **_kwargs: object) -> str:
        raise OSError("durable append failed")

    monkeypatch.setattr(NativeSessionTree, "append_compaction", fail_append)
    client = _RpcClient(tmp_path, persist_tree=True)
    try:
        for index in range(4):
            client.send(
                {"id": f"p{index}", "type": "prompt", "message": f"ROOT-{index}"}
            )
            client.collect_until(lambda record: record.get("type") == "agent_settled")
        client.send({"id": "compact", "type": "compact"})
        records = client.collect_until(lambda record: record.get("id") == "compact")
        end = next(
            record for record in records if record.get("type") == "compaction_end"
        )
        response = records[-1]
        assert end["result"] is not None
        assert (
            end["errorMessage"] == "Compaction accepted but durable persistence failed"
        )
        assert response["success"] is False
        assert response["error"] == "compaction persistence failed"
        compaction_port = client._server._compaction
        assert compaction_port is not None
        assert cast(Any, compaction_port)._effects.coding_state.compaction_count == 1
    finally:
        monkeypatch.setattr(NativeSessionTree, "append_compaction", original)
        client.close()


def test_manual_compact_refuses_busy_without_lifecycle_events(tmp_path: Path) -> None:
    provider = _BlockingFirstAutomationProvider()
    client = _RpcClient(tmp_path, provider=provider)
    try:
        client.send({"id": "prompt", "type": "prompt", "message": "ROOT"})
        client.wait_for(lambda record: record.get("type") == "agent_start")
        client.send({"id": "compact", "type": "compact"})
        records = client.collect_until(lambda record: record.get("id") == "compact")
        assert records[-1]["success"] is False
        assert records[-1]["error"] == "session is not idle"
        assert not any(
            record.get("type") in {"compaction_start", "compaction_end"}
            for record in records
        )
        provider.release()
        client.collect_until(lambda record: record.get("type") == "agent_end")
    finally:
        client.close()


def test_preclaimed_abort_skips_manual_compaction_owner(tmp_path: Path) -> None:
    client = _RpcClient(tmp_path)
    called = False
    token = object()

    def compact(_custom: ProductContent | None) -> object:
        nonlocal called
        called = True
        raise AssertionError("aborted work must not enter semantic compaction")

    try:
        client._server._compaction = SimpleNamespace(compact=compact)
        client._server._compact_ids[token] = "compact"
        claim = SimpleNamespace(token=token, is_aborted=True)
        publish = client._server._run_manual_compaction(claim, None)
        publish(SimpleNamespace(reservation=None))
        response = client.wait_for(lambda record: record.get("id") == "compact")
        assert called is False
        assert response["success"] is False
        assert response["error"] == "compaction cancelled"
    finally:
        client.close()


def test_rpc_auto_compaction_requires_exact_bool_and_projects_effective_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    client = _RpcClient(tmp_path)
    try:
        client.send({"id": "off", "type": "set_auto_compaction", "enabled": False})
        assert client.wait_for(lambda record: record.get("id") == "off")["success"]
        client.send({"id": "state", "type": "get_state"})
        assert (
            client.wait_for(lambda record: record.get("id") == "state")["data"][
                "autoCompactionEnabled"
            ]
            is False
        )
        client.send({"id": "invalid", "type": "set_auto_compaction", "enabled": 1})
        invalid = client.wait_for(lambda record: record.get("id") == "invalid")
        assert invalid["success"] is False
        assert invalid["error"] == "enabled must be a boolean"
    finally:
        client.close()


def test_manual_compact_abort_cancels_private_summary_and_projects_state(
    tmp_path: Path,
) -> None:
    provider = _BlockingCompactionAutomationProvider()
    client = _RpcClient(tmp_path, provider=provider)
    try:
        for index in range(4):
            client.send(
                {"id": f"p{index}", "type": "prompt", "message": f"ROOT-{index}"}
            )
            client.collect_until(lambda record: record.get("type") == "agent_settled")
        provider.block_summaries()
        client.send({"id": "compact", "type": "compact"})
        assert provider.summary_entered.wait(timeout=5)
        client.send({"id": "state", "type": "get_state"})
        state = client.wait_for(lambda record: record.get("id") == "state")
        assert state["data"]["isCompacting"] is True
        assert state["data"]["isStreaming"] is False

        client.send({"id": "abort", "type": "abort"})
        records = client.collect_until(lambda record: record.get("id") == "compact")
        end = next(
            record for record in records if record.get("type") == "compaction_end"
        )
        response = records[-1]
        assert end["aborted"] is True
        assert end["willRetry"] is False
        assert response["success"] is False
        assert response["error"] == "compaction cancelled"
    finally:
        client.close()


def test_second_manual_compact_refuses_while_first_is_active(tmp_path: Path) -> None:
    provider = _BlockingCompactionAutomationProvider()
    client = _RpcClient(tmp_path, provider=provider)
    try:
        for index in range(4):
            client.send(
                {"id": f"p{index}", "type": "prompt", "message": f"ROOT-{index}"}
            )
            client.collect_until(lambda record: record.get("type") == "agent_settled")
        provider.block_summaries()
        client.send({"id": "first", "type": "compact"})
        assert provider.summary_entered.wait(timeout=5)
        client.send({"id": "second", "type": "compact"})
        second = client.wait_for(lambda record: record.get("id") == "second")
        assert second["success"] is False
        assert second["error"] == "session is not idle"
        client.send({"id": "abort", "type": "abort"})
        records = client.collect_until(lambda record: record.get("id") == "first")
        all_records = [*client._seen, *records]
        assert (
            sum(record.get("type") == "compaction_start" for record in all_records) == 1
        )
        assert (
            sum(record.get("type") == "compaction_end" for record in all_records) == 1
        )
    finally:
        client.close()


def test_manual_compact_settlement_projects_successor_before_direct_promotion(
    tmp_path: Path,
) -> None:
    provider = _BlockingCompactionAutomationProvider()
    client = _RpcClient(tmp_path, provider=provider)
    try:
        for index in range(4):
            client.send(
                {"id": f"p{index}", "type": "prompt", "message": f"ROOT-{index}"}
            )
            client.collect_until(lambda record: record.get("type") == "agent_settled")
        provider.block_summaries()
        client.send({"id": "compact", "type": "compact"})
        assert provider.summary_entered.wait(timeout=5)
        client.send({"id": "next", "type": "prompt", "message": "NEXT"})
        assert client.wait_for(lambda record: record.get("id") == "next")["success"]
        provider.release_summary.set()
        records = client.collect_until(
            lambda record: record.get("type") == "agent_start"
        )
        types = [record.get("type") for record in records]
        end_index = types.index("compaction_end")
        compact_response_index = next(
            index
            for index, record in enumerate(records)
            if record.get("id") == "compact"
        )
        queue_index = types.index("queue_update")
        promoted_start = len(records) - 1
        assert end_index < compact_response_index < queue_index < promoted_start
    finally:
        client.close()


def test_automatic_threshold_compaction_projects_balanced_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loop_step_module, "AGENT_HISTORY_MAX_MESSAGES", 3)
    client = _RpcClient(tmp_path)
    try:
        seen: list[dict] = []
        for index in range(4):
            client.send(
                {"id": f"p{index}", "type": "prompt", "message": f"ROOT-{index}"}
            )
            seen.extend(
                client.collect_until(
                    lambda record: record.get("type") == "agent_settled"
                )
            )
        starts = [record for record in seen if record.get("type") == "compaction_start"]
        ends = [record for record in seen if record.get("type") == "compaction_end"]
        assert starts and len(starts) == len(ends)
        assert all(record["reason"] == "threshold" for record in starts + ends)
        assert all(record["willRetry"] is False for record in ends)
        assert all(record["result"] is None for record in ends)
        assert "SEEN:Provide" not in json.dumps(seen)
    finally:
        client.close()


def test_automatic_compaction_reports_streaming_and_compacting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loop_step_module, "AGENT_HISTORY_MAX_MESSAGES", 3)
    provider = _BlockingCompactionAutomationProvider()
    client = _RpcClient(tmp_path, provider=provider)
    try:
        for index in range(3):
            client.send(
                {"id": f"p{index}", "type": "prompt", "message": f"ROOT-{index}"}
            )
            client.collect_until(lambda record: record.get("type") == "agent_settled")
        provider.block_summaries()
        client.send({"id": "active", "type": "prompt", "message": "NEXT"})
        assert provider.summary_entered.wait(timeout=5)
        client.send({"id": "state", "type": "get_state"})
        state = client.wait_for(lambda record: record.get("id") == "state")
        assert state["data"]["isStreaming"] is True
        assert state["data"]["isCompacting"] is True
        client.send({"id": "abort", "type": "abort"})
        client.collect_until(lambda record: record.get("type") == "agent_end")
    finally:
        client.close()


def test_automatic_compaction_failure_projects_bounded_end_and_clears_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loop_step_module, "AGENT_HISTORY_MAX_MESSAGES", 3)
    provider = _BlockingCompactionAutomationProvider()
    client = _RpcClient(tmp_path, provider=provider)
    try:
        for index in range(3):
            client.send(
                {"id": f"p{index}", "type": "prompt", "message": f"ROOT-{index}"}
            )
            client.collect_until(lambda record: record.get("type") == "agent_settled")
        provider.fail_summaries()
        client.send({"id": "active", "type": "prompt", "message": "NEXT"})
        records = client.collect_until(
            lambda record: record.get("type") == "agent_settled"
        )
        end = next(
            record for record in records if record.get("type") == "compaction_end"
        )
        assert end["result"] is None
        assert end["errorMessage"] == "Compaction failed"
        client.send({"id": "state", "type": "get_state"})
        state = client.wait_for(lambda record: record.get("id") == "state")
        assert state["data"]["isCompacting"] is False
    finally:
        client.close()


def test_automatic_persistence_failure_projects_private_accepted_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loop_step_module, "AGENT_HISTORY_MAX_MESSAGES", 3)
    client = _RpcClient(tmp_path, persist_tree=True)
    try:
        for index in range(3):
            client.send(
                {"id": f"p{index}", "type": "prompt", "message": f"ROOT-{index}"}
            )
            client.collect_until(lambda record: record.get("type") == "agent_settled")
        compaction_port = client._server._compaction
        assert compaction_port is not None
        effects = cast(Any, compaction_port)._effects
        before_count = effects.coding_state.compaction_count

        monkeypatch.setattr(
            NativeSessionTree,
            "append_compaction",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("durable append failed")
            ),
        )
        client.send({"id": "active", "type": "prompt", "message": "NEXT"})
        records = client.collect_until(
            lambda record: record.get("type") == "compaction_end"
        )
        end = records[-1]
        assert end["reason"] == "threshold"
        assert end["result"] is None
        assert (
            end["errorMessage"] == "Compaction accepted but durable persistence failed"
        )
        assert "SEEN:Provide" not in json.dumps(records)
        assert effects.coding_state.compaction_count == before_count + 1
        assert client._server._worker is not None
        client._server._worker.join(timeout=2)
        assert not client._server._worker.is_alive()
    finally:
        client.close()


def test_static_injected_provider_configuration_fallback(client) -> None:
    client.send({"id": "state", "type": "get_state"})
    state = client.wait_for(lambda record: record.get("id") == "state")
    model = state["data"]["model"]
    assert state["data"]["thinkingLevel"] == "off"

    client.send({"id": "models", "type": "get_available_models"})
    assert client.wait_for(lambda record: record.get("id") == "models")["data"] == {
        "models": [model]
    }
    client.send(
        {
            "id": "same",
            "type": "set_model",
            "provider": model["provider"],
            "modelId": model["id"],
        }
    )
    assert client.wait_for(lambda record: record.get("id") == "same")["success"]
    for command in ("cycle_model", "cycle_thinking_level"):
        client.send({"id": command, "type": command})
        assert (
            client.wait_for(lambda record: record.get("id") == command)["data"] is None
        )
    client.send(
        {"id": "other", "type": "set_model", "provider": "other", "modelId": "x"}
    )
    assert not client.wait_for(lambda record: record.get("id") == "other")["success"]
    client.send({"id": "thinking", "type": "set_thinking_level", "level": "high"})
    assert not client.wait_for(lambda record: record.get("id") == "thinking")["success"]
    client.send({"id": "state-after", "type": "get_state"})
    client.wait_for(lambda record: record.get("id") == "state-after")
    assert not hasattr(client._server, "_thinking_level")
    assert not any(
        record.get("type") == "thinking_level_changed" for record in client._seen
    )
    assert not [
        entry
        for entry in client.tree.entries
        if getattr(entry, "type", "") == "thinking_level_change"
    ]


def test_catalog_model_selection_uses_owner_and_emits_no_model_event(
    tmp_path: Path,
) -> None:
    adapter = _provider_state_adapter(tmp_path)
    assert adapter.provider_state is not None
    client = _RpcClient(tmp_path, provider_state=adapter.provider_state)
    try:
        client.send({"id": "models", "type": "get_available_models"})
        available = client.wait_for(lambda record: record.get("id") == "models")
        models = available["data"]["models"]
        assert {"provider": "openai", "id": "gpt-5.5"} in models
        assert {"provider": "openai", "id": "gpt-5.4"} in models

        client.send(
            {
                "id": "set",
                "type": "set_model",
                "provider": "openai",
                "modelId": "gpt-5.4",
            }
        )
        response = client.wait_for(lambda record: record.get("id") == "set")
        assert response["data"] == {"provider": "openai", "id": "gpt-5.4"}
        assert adapter.provider_state.current_selection() == NativeModelSelection(
            "openai", "gpt-5.4"
        )
        assert not [
            record for record in client._seen if record.get("type") == "model_changed"
        ]

        client.send(
            {
                "id": "same",
                "type": "set_model",
                "provider": "openai",
                "modelId": "gpt-5.4",
            }
        )
        assert client.wait_for(lambda record: record.get("id") == "same")["success"]
    finally:
        client.close()


def test_rpc_configuration_noops_emit_no_thinking_event_or_durable_entry(
    tmp_path: Path,
) -> None:
    adapter = _provider_state_adapter(tmp_path)
    assert adapter.provider_state is not None
    client = _RpcClient(tmp_path, provider_state=adapter.provider_state)
    client._seen = []
    try:
        client.send({"id": "thinking", "type": "set_thinking_level", "level": "high"})
        client.collect_until(
            lambda record: record.get("type") == "thinking_level_changed"
        )
        entries_before = tuple(client.tree.entries)
        client.send(
            {
                "id": "same-model",
                "type": "set_model",
                "provider": "openai",
                "modelId": "gpt-5.5",
            }
        )
        client.wait_for(lambda record: record.get("id") == "same-model")
        client.send(
            {"id": "same-thinking", "type": "set_thinking_level", "level": "high"}
        )
        client.wait_for(lambda record: record.get("id") == "same-thinking")
        # Commands are serialized, so this barrier response follows either
        # hypothetical event and makes its absence deterministic.
        seen_before = len(client._seen)
        client.send({"id": "barrier", "type": "get_state"})
        client.wait_for(lambda record: record.get("id") == "barrier")

        assert tuple(client.tree.entries) == entries_before
        assert not any(
            record.get("type") == "thinking_level_changed"
            for record in client._seen[seen_before:]
        )
    finally:
        client.close()


def test_model_switch_clamps_thinking_and_orders_response_before_event(
    tmp_path: Path,
) -> None:
    adapter = _provider_state_adapter(tmp_path)
    assert adapter.provider_state is not None
    adapter.provider_state.assign_thinking_level("max")
    client = _RpcClient(tmp_path, provider_state=adapter.provider_state)
    try:
        client.send(
            {
                "id": "set",
                "type": "set_model",
                "provider": "openai",
                "modelId": "gpt-4o",
            }
        )
        records = client.collect_until(
            lambda record: record.get("type") == "thinking_level_changed"
        )
        response_index = next(
            index for index, record in enumerate(records) if record.get("id") == "set"
        )
        event_index = next(
            index
            for index, record in enumerate(records)
            if record.get("type") == "thinking_level_changed"
        )
        assert response_index < event_index
        assert records[response_index]["data"] == {"provider": "openai", "id": "gpt-4o"}
        assert records[event_index]["level"] == "off"
        assert adapter.provider_state.current_thinking_level() == "off"
        entries = [
            entry
            for entry in client.tree.entries
            if getattr(entry, "type", "") == "thinking_level_change"
        ]
        assert len(entries) == 1
        assert getattr(entries[0], "thinking_level") == "off"
    finally:
        client.close()


def test_thinking_refuses_level_not_supported_by_current_model(tmp_path: Path) -> None:
    adapter = _provider_state_adapter(tmp_path)
    assert adapter.provider_state is not None
    adapter.provider_state.replace_selection(NativeModelSelection("openai", "gpt-4o"))
    client = _RpcClient(tmp_path, provider_state=adapter.provider_state)
    try:
        client.send({"id": "thinking", "type": "set_thinking_level", "level": "high"})
        response = client.wait_for(lambda record: record.get("id") == "thinking")
        assert response["success"] is False
        assert adapter.provider_state.current_thinking_level() is None
    finally:
        client.close()


def test_cycle_model_returns_explicit_null_data(client) -> None:
    # Single configured model: cycle_model must carry an explicit `data: null`
    # (Pi's `... | null` contract), not omit the data field.
    client.send({"id": "c", "type": "cycle_model"})
    resp = client.wait_for(lambda r: r.get("id") == "c")
    assert resp["command"] == "cycle_model"
    assert resp["success"] is True
    assert "data" in resp
    assert resp["data"] is None


def test_cycle_model_handler_returns_exact_scoped_projection(tmp_path: Path) -> None:
    """Exercise the handler payload independently of threaded settings setup."""

    class _TwoModelConfigurationPort:
        def __init__(self) -> None:
            self.calls = 0

        def cycle_model(self) -> RpcModelCycleResult:
            self.calls += 1
            selection = NativeModelSelection(
                "openai", "gpt-5.4" if self.calls == 1 else "gpt-5.5"
            )
            return RpcModelCycleResult(
                RpcConfigurationResult(
                    True, RpcConfigurationSnapshot(selection, "high")
                ),
                is_scoped=self.calls == 1,
            )

    output = io.BytesIO()
    server = NativeRpcServer(
        adapter=object(),
        cwd=tmp_path,
        native_session=NativeSessionTree.create(tmp_path, persist=False),
        stdin=io.StringIO(),
        stdout_buffer=output,
        error_stream=io.StringIO(),
    )
    server._configuration = _TwoModelConfigurationPort()
    server._cmd_cycle_model("scoped", {})
    server._cmd_cycle_model("unscoped", {})
    records = [json.loads(line) for line in output.getvalue().decode().splitlines()]

    assert records[0]["data"] == {
        "model": {"provider": "openai", "id": "gpt-5.4"},
        "thinkingLevel": "high",
        "isScoped": True,
    }
    assert records[1]["data"] == {
        "model": {"provider": "openai", "id": "gpt-5.5"},
        "thinkingLevel": "high",
        "isScoped": False,
    }


def test_no_payload_response_omits_data(client) -> None:
    # A command with no payload must omit the data field entirely.
    client.send({"id": "n", "type": "set_session_name", "name": "x"})
    resp = client.wait_for(lambda r: r.get("id") == "n")
    assert resp["success"] is True
    assert "data" not in resp


def test_bash_returns_bash_result(client) -> None:
    client.send({"id": "b", "type": "bash", "command": "echo hi"})
    resp = client.wait_for(lambda r: r.get("id") == "b" and r.get("type") == "response")
    assert resp["success"] is True
    assert "hi" in resp["data"]["output"]
    assert resp["data"]["exitCode"] == 0
    assert resp["data"]["cancelled"] is False


def test_unknown_command_and_parse_error(client) -> None:
    client.send({"type": "frobnicate"})
    unknown = client.wait_for(
        lambda r: r.get("type") == "response" and r.get("command") == "frobnicate"
    )
    assert unknown["success"] is False
    assert unknown["error"] == "Unknown command: frobnicate"
    assert "id" not in unknown  # unknown-command errors drop the id (Pi parity)

    client._stdin_write.write("{ this is not json\n")
    client._stdin_write.flush()
    parse = client.wait_for(
        lambda r: r.get("type") == "response" and r.get("command") == "parse"
    )
    assert parse["success"] is False
    assert "Failed to parse command" in parse["error"]

    # Non-standard NaN/Infinity is rejected as a parse error (strict JSONL).
    client._stdin_write.write('{"type":"get_state","x":NaN}\n')
    client._stdin_write.flush()
    nan_parse = client.wait_for(
        lambda r: r.get("type") == "response" and r.get("command") == "parse"
    )
    assert nan_parse["success"] is False


def test_non_string_command_type_does_not_crash(client) -> None:
    # A parseable command whose `type` is a non-string (unhashable) value must
    # produce a well-formed Unknown-command error, never crash the loop.
    client.send({"type": []})
    unknown = client.wait_for(
        lambda r: r.get("type") == "response" and r.get("command") == "[]"
    )
    assert unknown["success"] is False
    assert "Unknown command" in unknown["error"]
    # The process is still alive: a normal command still responds.
    client.send({"id": "ok", "type": "get_state"})
    state = client.wait_for(lambda r: r.get("id") == "ok")
    assert state["success"] is True


def test_abort_bash_is_honest_when_idle(client) -> None:
    # With no bash in flight, abort_bash is a valid no-op success.
    client.send({"id": "ab", "type": "abort_bash"})
    resp = client.wait_for(lambda r: r.get("id") == "ab")
    assert resp["command"] == "abort_bash"
    assert resp["success"] is True


def _bare_bash_server(tmp_path: Path) -> tuple[NativeRpcServer, io.BytesIO]:
    output = io.BytesIO()
    return (
        NativeRpcServer(
            adapter=object(),
            cwd=tmp_path,
            native_session=NativeSessionTree.create(tmp_path, persist=False),
            stdin=io.StringIO(),
            stdout_buffer=output,
            error_stream=io.StringIO(),
        ),
        output,
    )


class _BlockingWriteBuffer(io.BytesIO):
    """Hold the first serialized update write to model stdout backpressure."""

    def __init__(self) -> None:
        super().__init__()
        self.write_started = threading.Event()
        self.release = threading.Event()

    def write(self, data: Any) -> int:
        if not self.write_started.is_set():
            self.write_started.set()
            assert self.release.wait(timeout=3.0)
        return super().write(data)


def _join_bash_workers(server: NativeRpcServer) -> None:
    with server._lock:
        workers = list(server._bash_threads)
    for worker in workers:
        worker.join(timeout=3.0)
        assert not worker.is_alive()


def test_rpc_bash_publishes_paced_safe_updates_before_terminal_response(
    client, tmp_path: Path
) -> None:
    fifo = tmp_path / "paced"
    os.mkfifo(fifo)
    client.send({"id": "paced", "type": "bash", "command": "cat paced"})

    fd = os.open(fifo, os.O_WRONLY)
    try:
        os.write(fd, b"first\n")
        first = client.wait_for(
            lambda record: (
                record.get("type") == "bash_execution_update"
                and record.get("id") == "paced"
            )
        )
        assert first["delta"] == "first\n"
        os.write(fd, b"second\n")
    finally:
        os.close(fd)

    records = [
        first,
        *client.collect_until(
            lambda record: (
                record.get("id") == "paced" and record.get("type") == "response"
            )
        ),
    ]
    terminal_index = next(
        index
        for index, record in enumerate(records)
        if record.get("id") == "paced" and record.get("type") == "response"
    )
    assert records[terminal_index]["type"] == "response"
    updates = [
        record
        for record in records[:terminal_index]
        if record.get("type") == "bash_execution_update"
    ]
    assert "".join(record["delta"] for record in updates) == "first\nsecond\n"
    assert records[terminal_index]["data"]["output"] == "first\nsecond\n"


def test_rpc_bash_updates_do_not_redefine_terminal_stream_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, output = _bare_bash_server(tmp_path)

    def ordered_callback(
        _command: str,
        _policy: Any,
        *,
        cancel_event: threading.Event | None = None,
        output_callback: Callable[[str], None] | None = None,
    ) -> CommandResult:
        assert output_callback is not None
        output_callback("stderr-ready\n")
        output_callback("stdout-ready\n")
        return CommandResult(
            status=CommandStatus.COMPLETED,
            stdout="stdout-terminal\n",
            stderr="stderr-terminal\n",
            exit_code=0,
        )

    monkeypatch.setattr(rpc_module, "run_command", ordered_callback)
    server._cmd_bash("ordered", {"command": "echo ordered"})
    _join_bash_workers(server)
    records = [json.loads(line) for line in output.getvalue().decode().splitlines()]

    assert (
        "".join(
            record["delta"]
            for record in records
            if record.get("type") == "bash_execution_update"
        )
        == "stderr-ready\nstdout-ready\n"
    )
    terminal = next(
        record
        for record in records
        if record.get("id") == "ordered" and record.get("type") == "response"
    )
    assert terminal["data"]["output"] == "stdout-terminal\nstderr-terminal\n"


def test_rpc_bash_secret_output_is_redacted_in_updates_and_terminal(
    client, tmp_path: Path
) -> None:
    secret = "api_key=ABCDEFGHIJKLMNOP\n"
    (tmp_path / "creds").write_text(secret, encoding="utf-8")
    client.send({"id": "secret", "type": "bash", "command": "cat creds"})
    records = client.collect_until(
        lambda record: record.get("id") == "secret" and record.get("type") == "response"
    )
    updates = [
        record["delta"]
        for record in records
        if record.get("type") == "bash_execution_update"
    ]
    terminal = records[-1]
    assert updates == ["[redacted: secret-shaped content]\n"]
    assert "ABCDEFGHIJKLMNOP" not in "".join(updates)
    assert terminal["data"]["output"] == "[redacted: secret-shaped content]\n"


def test_rpc_bash_update_omits_missing_id_and_isolated_per_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, output = _bare_bash_server(tmp_path)

    def emit_once(
        command: str,
        _policy: Any,
        *,
        cancel_event: threading.Event | None = None,
        output_callback: Callable[[str], None] | None = None,
    ) -> CommandResult:
        assert output_callback is not None
        output_callback(f"{command}\n")
        return CommandResult(
            status=CommandStatus.COMPLETED, stdout=command, exit_code=0
        )

    monkeypatch.setattr(rpc_module, "run_command", emit_once)
    server._cmd_bash(None, {"command": "no-id"})
    server._cmd_bash("two", {"command": "two"})
    _join_bash_workers(server)
    records = [json.loads(line) for line in output.getvalue().decode().splitlines()]

    updates = [
        record for record in records if record["type"] == "bash_execution_update"
    ]
    assert any(
        "id" not in record and record["delta"] == "no-id\n" for record in updates
    )
    assert any(
        record.get("id") == "two" and record["delta"] == "two\n" for record in updates
    )
    for cid in (None, "two"):
        relevant = [
            record
            for record in records
            if (record.get("id") if "id" in record else None) == cid
        ]
        assert relevant[-1]["type"] == "response"
        assert relevant[-1]["command"] == "bash"


def test_rpc_bash_update_is_closed_before_successor_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, output = _bare_bash_server(tmp_path)
    first_callback: Callable[[str], None] | None = None

    def controlled(
        command: str,
        _policy: Any,
        *,
        cancel_event: threading.Event | None = None,
        output_callback: Callable[[str], None] | None = None,
    ) -> CommandResult:
        nonlocal first_callback
        assert output_callback is not None
        if command == "first":
            first_callback = output_callback
            output_callback("first\n")
        else:
            output_callback("second\n")
        return CommandResult(
            status=CommandStatus.COMPLETED, stdout=command, exit_code=0
        )

    monkeypatch.setattr(rpc_module, "run_command", controlled)
    server._cmd_bash("same", {"command": "first"})
    _join_bash_workers(server)
    assert first_callback is not None
    first_callback("late\n")
    server._cmd_bash("same", {"command": "second"})
    _join_bash_workers(server)
    records = [json.loads(line) for line in output.getvalue().decode().splitlines()]

    assert [
        record["delta"]
        for record in records
        if record["type"] == "bash_execution_update"
    ] == [
        "first\n",
        "second\n",
    ]
    assert (
        len(
            [
                record
                for record in records
                if record.get("id") == "same" and record["type"] == "response"
            ]
        )
        == 2
    )


def test_blocked_update_writer_does_not_block_abort_or_terminal_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _BlockingWriteBuffer()
    server = NativeRpcServer(
        adapter=object(),
        cwd=tmp_path,
        native_session=NativeSessionTree.create(tmp_path, persist=False),
        stdin=io.StringIO(),
        stdout_buffer=output,
        error_stream=io.StringIO(),
    )
    callback_admitted = threading.Event()
    sandbox_returned = threading.Event()

    def blocked_sandbox(
        _command: str,
        _policy: Any,
        *,
        cancel_event: threading.Event | None = None,
        output_callback: Callable[[str], None] | None = None,
    ) -> CommandResult:
        assert cancel_event is not None and output_callback is not None
        output_callback("safe\n")
        callback_admitted.set()
        assert cancel_event.wait(timeout=3.0)
        sandbox_returned.set()
        return CommandResult(status=CommandStatus.CANCELLED, stdout="safe\n")

    monkeypatch.setattr(rpc_module, "run_command", blocked_sandbox)
    server._cmd_bash("bash", {"command": "echo safe"})
    assert callback_admitted.wait(timeout=3.0)
    assert output.write_started.wait(timeout=3.0)

    abort_thread = threading.Thread(
        target=lambda: server._cmd_abort_bash("abort", {}), daemon=True
    )
    abort_thread.start()
    assert sandbox_returned.wait(timeout=3.0)

    output.release.set()
    abort_thread.join(timeout=3.0)
    assert not abort_thread.is_alive()
    _join_bash_workers(server)
    records = [json.loads(line) for line in output.getvalue().decode().splitlines()]
    update = next(
        record for record in records if record.get("type") == "bash_execution_update"
    )
    terminal = next(
        record
        for record in records
        if record.get("id") == "bash" and record.get("type") == "response"
    )
    assert terminal["data"]["cancelled"] is True
    assert records.index(update) < records.index(terminal)


@pytest.mark.parametrize(
    ("termination", "expected_cancelled"),
    [("abort", True), ("timeout", False)],
)
def test_blocked_update_writer_does_not_delay_real_sandbox_reap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    termination: str,
    expected_cancelled: bool,
) -> None:
    """A blocked update write cannot hold up the real Popen cleanup path."""

    output = _BlockingWriteBuffer()
    server = NativeRpcServer(
        adapter=object(),
        cwd=tmp_path,
        native_session=NativeSessionTree.create(tmp_path, persist=False),
        stdin=io.StringIO(),
        stdout_buffer=output,
        error_stream=io.StringIO(),
    )
    (tmp_path / "input").write_text("safe\n", encoding="utf-8")
    original_popen = subprocess.Popen
    reaped = threading.Event()
    instances: list[Any] = []

    class RecordingPopen:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._child = original_popen(*args, **kwargs)
            instances.append(self)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._child, name)

        def wait(self, *args: Any, **kwargs: Any) -> Any:
            try:
                return self._child.wait(*args, **kwargs)
            finally:
                reaped.set()

    def short_policy(*, workspace_root: Path) -> CommandPolicy:
        return CommandPolicy(
            workspace_root=workspace_root,
            allowed_executables=frozenset({"tail"}),
            timeout_seconds=0.08,
        )

    monkeypatch.setattr(subprocess, "Popen", RecordingPopen)
    monkeypatch.setattr(rpc_module, "CommandPolicy", short_policy)
    server._cmd_bash("bash", {"command": "tail -f input"})
    assert output.write_started.wait(timeout=3.0)
    assert len(instances) == 1

    abort_thread: threading.Thread | None = None
    try:
        if termination == "abort":
            abort_thread = threading.Thread(
                target=lambda: server._cmd_abort_bash("abort", {}), daemon=True
            )
            abort_thread.start()
        # ``wait`` is called by the sandbox after its selector drain and direct
        # child cleanup. It must happen while the sole JsonlWriter remains held.
        assert reaped.wait(timeout=3.0)
    finally:
        output.release.set()

    if abort_thread is not None:
        abort_thread.join(timeout=3.0)
        assert not abort_thread.is_alive()
    _join_bash_workers(server)
    records = [json.loads(line) for line in output.getvalue().decode().splitlines()]
    updates = [
        record for record in records if record.get("type") == "bash_execution_update"
    ]
    terminals = [
        record
        for record in records
        if record.get("id") == "bash" and record.get("type") == "response"
    ]
    assert updates and updates[0]["delta"] == "safe\n"
    assert len(terminals) == 1
    terminal = terminals[0]
    assert terminal["data"]["cancelled"] is expected_cancelled
    assert terminal["data"]["exitCode"] is None
    assert records.index(updates[-1]) < records.index(terminal)


def test_abort_bash_snapshots_all_active_operations_and_preserves_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, output = _bare_bash_server(tmp_path)
    entered = threading.Barrier(3)

    def blocked_result(
        _command: str,
        _policy: Any,
        *,
        cancel_event: threading.Event | None = None,
        output_callback: Callable[[str], None] | None = None,
    ) -> CommandResult:
        assert cancel_event is not None
        entered.wait(timeout=3.0)
        assert cancel_event.wait(timeout=3.0)
        return CommandResult(
            status=CommandStatus.COMPLETED,
            stdout="drained-output",
            exit_code=0,
        )

    monkeypatch.setattr(rpc_module, "run_command", blocked_result)
    server._cmd_bash("one", {"command": "echo one"})
    server._cmd_bash("two", {"command": "echo two"})
    entered.wait(timeout=3.0)

    server._cmd_abort_bash("abort", {})
    _join_bash_workers(server)
    records = [json.loads(line) for line in output.getvalue().decode().splitlines()]

    assert [record["id"] for record in records].count("abort") == 1
    assert next(record for record in records if record.get("id") == "abort")["success"]
    terminal = [record for record in records if record.get("id") in {"one", "two"}]
    assert len(terminal) == 2
    assert all(record["success"] for record in terminal)
    assert all(record["data"]["cancelled"] is True for record in terminal)
    assert all(record["data"]["exitCode"] is None for record in terminal)
    assert all(record["data"]["output"] == "drained-output" for record in terminal)
    with server._lock:
        assert not server._bash_operations


def test_abort_bash_and_timeout_fixation_have_deterministic_lock_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # First operation: abort is marked while its timeout result is held at a
    # barrier, so abort wins at the shared registry lock.
    server, output = _bare_bash_server(tmp_path)
    timeout_ready = threading.Event()
    release_timeout = threading.Event()

    def held_timeout(
        _command: str,
        _policy: Any,
        *,
        cancel_event: threading.Event | None = None,
        output_callback: Callable[[str], None] | None = None,
    ) -> CommandResult:
        timeout_ready.set()
        assert release_timeout.wait(timeout=3.0)
        return CommandResult(status=CommandStatus.TIMED_OUT)

    monkeypatch.setattr(rpc_module, "run_command", held_timeout)
    server._cmd_bash("abort-wins", {"command": "echo held"})
    assert timeout_ready.wait(timeout=3.0)
    server._cmd_abort_bash("abort-first", {})
    release_timeout.set()
    _join_bash_workers(server)

    # Second operation: its timeout result fixes and retires before the abort
    # snapshot, so the later abort is an idle success and cannot reclassify it.
    server._cmd_bash("timeout-wins", {"command": "echo timeout"})
    _join_bash_workers(server)
    server._cmd_abort_bash("abort-late", {})

    records = [json.loads(line) for line in output.getvalue().decode().splitlines()]
    abort_wins = next(record for record in records if record.get("id") == "abort-wins")
    timeout_wins = next(
        record for record in records if record.get("id") == "timeout-wins"
    )
    assert abort_wins["data"]["cancelled"] is True
    assert abort_wins["data"]["exitCode"] is None
    assert timeout_wins["data"]["cancelled"] is False
    assert timeout_wins["data"]["exitCode"] is None


def test_late_abort_cannot_spill_into_a_fresh_bash_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, output = _bare_bash_server(tmp_path)

    def completed(
        command: str,
        _policy: Any,
        *,
        cancel_event: threading.Event | None = None,
        output_callback: Callable[[str], None] | None = None,
    ) -> CommandResult:
        assert cancel_event is not None and not cancel_event.is_set()
        return CommandResult(
            status=CommandStatus.COMPLETED, stdout=command, exit_code=0
        )

    monkeypatch.setattr(rpc_module, "run_command", completed)
    server._cmd_bash("first", {"command": "echo first"})
    _join_bash_workers(server)
    server._cmd_abort_bash("late", {})
    server._cmd_bash("successor", {"command": "echo successor"})
    _join_bash_workers(server)

    records = [json.loads(line) for line in output.getvalue().decode().splitlines()]
    successor = next(record for record in records if record.get("id") == "successor")
    assert successor["data"]["cancelled"] is False
    assert successor["data"]["exitCode"] == 0


def test_abort_bash_cancels_an_active_sandbox_child(client, tmp_path: Path) -> None:
    # ``cat`` on a FIFO is an accepted, real sandbox child. The direct
    # substrate test separately proves group termination reaches descendants.
    fifo = tmp_path / "blocked"
    os.mkfifo(fifo)
    client.send({"id": "bash", "type": "bash", "command": "cat blocked"})
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        with client._server._lock:
            if client._server._bash_operations:
                break
        time.sleep(0.01)
    with client._server._lock:
        assert len(client._server._bash_operations) == 1

    client.send({"id": "abort", "type": "abort_bash"})
    abort = client.wait_for(lambda record: record.get("id") == "abort")
    terminal = client.wait_for(
        lambda record: record.get("id") == "bash" and record.get("type") == "response"
    )
    assert abort["success"] is True
    assert terminal["success"] is True
    assert terminal["data"]["cancelled"] is True
    assert terminal["data"]["exitCode"] is None


def test_rpc_bash_timeout_is_not_reported_as_explicit_cancellation(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def short_policy(*, workspace_root: Path) -> CommandPolicy:
        return CommandPolicy(
            workspace_root=workspace_root,
            allowed_executables=frozenset({"tail"}),
            timeout_seconds=0.05,
        )

    monkeypatch.setattr(rpc_module, "CommandPolicy", short_policy)
    (tmp_path / "input").write_text("waiting\n", encoding="utf-8")
    client.send({"id": "timeout", "type": "bash", "command": "tail -f input"})
    response = client.wait_for(
        lambda record: (
            record.get("id") == "timeout" and record.get("type") == "response"
        )
    )
    assert response["success"] is True
    assert response["data"]["cancelled"] is False
    assert response["data"]["exitCode"] is None


def test_eof_joins_the_direct_bash_terminal_response(tmp_path: Path) -> None:
    client = _RpcClient(tmp_path)
    try:
        client.send({"id": "bash", "type": "bash", "command": "echo eof"})
        assert client.close() == 0
        records: list[dict[str, Any]] = []
        while not client._records.empty():
            records.append(client._records.get())
        terminal = next(
            record
            for record in records
            if record.get("id") == "bash" and record.get("type") == "response"
        )
        update = next(
            record
            for record in records
            if record.get("id") == "bash"
            and record.get("type") == "bash_execution_update"
        )
        assert terminal["success"] is True
        assert terminal["data"]["exitCode"] == 0
        assert update["delta"] == "eof\n"
        assert records.index(update) < records.index(terminal)
    finally:
        # ``close`` is idempotent only at the test's resource layer; it has
        # already closed every stream on the successful path above.
        if client._server_thread.is_alive():
            client.close()


def test_eof_joins_an_abort_in_progress_without_losing_either_response(
    tmp_path: Path,
) -> None:
    client = _RpcClient(tmp_path)
    fifo = tmp_path / "blocked"
    os.mkfifo(fifo)
    try:
        client.send({"id": "bash", "type": "bash", "command": "cat blocked"})
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            with client._server._lock:
                if client._server._bash_operations:
                    break
            time.sleep(0.01)
        with client._server._lock:
            assert client._server._bash_operations

        # Both frames are already in the input pipe when EOF arrives. Shutdown
        # must still dispatch the abort and join the terminal bash worker.
        client.send({"id": "abort", "type": "abort_bash"})
        assert client.close() == 0
        records: list[dict[str, Any]] = []
        while not client._records.empty():
            records.append(client._records.get())
        abort = next(record for record in records if record.get("id") == "abort")
        terminal = next(
            record
            for record in records
            if record.get("id") == "bash" and record.get("type") == "response"
        )
        assert abort["success"] is True
        assert terminal["data"]["cancelled"] is True
    finally:
        if client._server_thread.is_alive():
            client.close()


def test_set_session_name_then_get_state(client) -> None:
    client.send({"id": "n", "type": "set_session_name", "name": "my-session"})
    client.wait_for(lambda r: r.get("id") == "n" and r.get("success") is True)

    client.send({"id": "s2", "type": "get_state"})
    state = client.wait_for(lambda r: r.get("id") == "s2")
    assert state["data"]["sessionName"] == "my-session"


def test_steer_emits_queue_update_and_abort_terminates(client) -> None:
    client.send({"id": "p", "type": "prompt", "message": "BLOCK and wait"})
    client.wait_for(lambda r: r.get("type") == "agent_start")

    client.send({"id": "st", "type": "steer", "message": "go left"})
    qu = client.wait_for(lambda r: r.get("type") == "queue_update")
    assert "go left" in qu["steering"]

    client.send({"id": "ab", "type": "abort"})
    client.wait_for(lambda r: r.get("id") == "ab" and r.get("success") is True)
    client.wait_for(lambda r: r.get("type") == "agent_end")


def test_steering_queue_is_consumed_not_stale(client) -> None:
    # Run one turn so the session is idle.
    client.send({"id": "p", "type": "prompt", "message": "ROOT"})
    client.collect_until(lambda r: r.get("type") == "agent_end")
    # Steer while idle: it is delivered as the next run and the queue is cleared,
    # not left reporting stale pending steering forever.
    client.send({"id": "s", "type": "steer", "message": "STEERED"})
    client.wait_for(lambda r: r.get("id") == "s" and r.get("success") is True)
    client.collect_until(lambda r: r.get("type") == "agent_end")
    client.send({"id": "st", "type": "get_state"})
    state = client.wait_for(lambda r: r.get("id") == "st")
    assert state["data"]["pendingMessageCount"] == 0


@pytest.mark.parametrize(
    "queued_slash",
    [
        "/new",
        "/tree select 1",
        "/resume",
        "/resume delete victim --yes",
        "/fork",
        "/fork 1",
        "/clone",
        "/trust",
        "/settings",
        "/export full-session.html",
        "/import source.jsonl --yes",
    ],
)
def test_classified_rpc_queue_bypasses_slash_and_shell_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queued_slash: str,
) -> None:
    provider = _BlockingFirstAutomationProvider()
    shell_calls: list[str] = []
    taken: list[AgentQueuedInput] = []
    original_queued_input_port = loop_module.NativeAgentQueuedInputPort

    class RecordingQueuedInputPort:
        def __init__(self, take_next: Callable[[], AgentQueuedInput | None]) -> None:
            self._delegate = original_queued_input_port(take_next)

        def take_next(self) -> AgentQueuedInput | None:
            queued_input = self._delegate.take_next()
            if queued_input is not None:
                taken.append(queued_input)
            return queued_input

    def record_shell_dispatch(
        command_text: str,
        **_kwargs: object,
    ) -> None:
        shell_calls.append(command_text)

    # Patch the loop-step owner binding used by the shell phase.
    monkeypatch.setattr(
        loop_step_module,
        "run_local_shell_shortcut",
        record_shell_dispatch,
    )
    monkeypatch.setattr(
        loop_module,
        "NativeAgentQueuedInputPort",
        RecordingQueuedInputPort,
    )
    c = _RpcClient(tmp_path, provider=provider)
    c._seen = []
    try:
        c.send({"id": "p", "type": "prompt", "message": "ROOT"})
        c.wait_for(lambda record: record.get("type") == "agent_start")

        # Enqueue follow-up first, then steering. RPC owns priority and must
        # reserve steering first while retaining each closed delivery kind.
        c.send(
            {
                "id": "f",
                "type": "follow_up",
                "message": "!rpc-queued-shell",
            }
        )
        c.wait_for(lambda record: record.get("id") == "f")
        c.send({"id": "s", "type": "steer", "message": queued_slash})
        c.wait_for(lambda record: record.get("id") == "s")
        provider.release()

        tail = c.collect_until(lambda record: record.get("type") == "agent_settled")
        records = [*c._seen, *tail]
    finally:
        provider.release()
        c.close()

    assert [request.user_prompt for request in provider.requests] == [
        "ROOT",
        queued_slash,
        "!rpc-queued-shell",
    ]
    user_messages = [
        "".join(block.get("text", "") for block in record["message"]["content"])
        for record in records
        if record.get("type") == "message_start"
        and record.get("message", {}).get("role") == "user"
    ]
    assert user_messages == ["ROOT", queued_slash, "!rpc-queued-shell"]
    assert shell_calls == []
    assert [
        event.content.value
        for event in c.canonical.events
        if isinstance(event, (SteeringConsumed, FollowUpConsumed))
    ] == [queued_slash, "!rpc-queued-shell"]

    classified_events = [
        event
        for event in c.canonical.events
        if isinstance(event, (AgentRunStarted, SteeringConsumed, FollowUpConsumed))
    ]
    assert classified_events == [
        AgentRunStarted(),
        AgentRunStarted(),
        SteeringConsumed(ProductContent(queued_slash)),
        AgentRunStarted(),
        FollowUpConsumed(ProductContent("!rpc-queued-shell")),
    ]
    assert [
        message.content.value
        for message in c.tree.build_context().messages
        if isinstance(message, AgentUserMessage)
    ] == ["ROOT", queued_slash, "!rpc-queued-shell"]


@pytest.mark.parametrize("trailing_newlines", ["\n", "\n\n"])
def test_post_run_rpc_queue_preserves_trailing_newlines(
    trailing_newlines: str,
    tmp_path: Path,
) -> None:
    provider = _BlockingFirstAutomationProvider()
    queued_content = f"queued steering{trailing_newlines}"
    c = _RpcClient(tmp_path, provider=provider)
    c._seen = []
    try:
        c.send({"id": "p", "type": "prompt", "message": "ROOT"})
        c.wait_for(lambda record: record.get("type") == "agent_start")
        c.send({"id": "s", "type": "steer", "message": queued_content})
        c.wait_for(lambda record: record.get("id") == "s")
        provider.release()
        c.collect_until(lambda record: record.get("type") == "agent_settled")
    finally:
        provider.release()
        c.close()

    assert [request.user_prompt for request in provider.requests] == [
        "ROOT",
        queued_content,
    ]
    assert [
        event.content.value
        for event in c.canonical.events
        if isinstance(event, SteeringConsumed)
    ] == [queued_content]


@pytest.mark.parametrize("trailing_newlines", ["\n", "\n\n"])
def test_idle_wake_rpc_queue_preserves_trailing_newlines(
    trailing_newlines: str,
    tmp_path: Path,
) -> None:
    provider = _BlockingFirstAutomationProvider()
    provider.release()
    queued_content = f"idle follow-up{trailing_newlines}"
    c = _RpcClient(tmp_path, provider=provider)
    c._seen = []
    try:
        c.send({"id": "p", "type": "prompt", "message": "ROOT"})
        c.collect_until(lambda record: record.get("type") == "agent_settled")
        c.send({"id": "f", "type": "follow_up", "message": queued_content})
        c.wait_for(lambda record: record.get("id") == "f")
        c.collect_until(lambda record: record.get("type") == "agent_settled")
    finally:
        c.close()

    assert [request.user_prompt for request in provider.requests] == [
        "ROOT",
        queued_content,
    ]
    assert [
        event.content.value
        for event in c.canonical.events
        if isinstance(event, FollowUpConsumed)
    ] == [queued_content]


def test_get_state_after_agent_end_is_settled(client) -> None:
    # agent_end is the settled boundary: a get_state immediately after it must
    # show the run no longer streaming and the queue empty (no stale state).
    client.send({"id": "p", "type": "prompt", "message": "ROOT"})
    client.collect_until(lambda r: r.get("type") == "agent_end")
    client.send({"id": "s", "type": "get_state"})
    state = client.wait_for(lambda r: r.get("id") == "s")
    assert state["data"]["isStreaming"] is False
    assert state["data"]["pendingMessageCount"] == 0


def test_get_last_assistant_text_from_session_tree(client) -> None:
    client.send({"id": "p", "type": "prompt", "message": "ROOT"})
    client.collect_until(lambda r: r.get("type") == "agent_end")
    client.send({"id": "t", "type": "get_last_assistant_text"})
    resp = client.wait_for(lambda r: r.get("id") == "t")
    assert resp["data"]["text"] == "SEEN:ROOT"


def test_prompt_during_active_run_is_queued_observably(client) -> None:
    # Start a blocking run, then send a second prompt: it must be observable in
    # the queue (queue_update + pendingMessageCount), not silently deferred.
    client.send({"id": "p1", "type": "prompt", "message": "BLOCK and hold"})
    client.wait_for(lambda r: r.get("type") == "agent_start")
    client.send({"id": "p2", "type": "prompt", "message": "second prompt"})
    qu = client.wait_for(
        lambda r: (
            r.get("type") == "queue_update" and "second prompt" in r.get("followUp", [])
        )
    )
    assert "second prompt" in qu["followUp"]
    client.send({"id": "s", "type": "get_state"})
    state = client.wait_for(lambda r: r.get("id") == "s")
    assert state["data"]["pendingMessageCount"] >= 1


def test_idle_abort_does_not_poison_next_prompt(client) -> None:
    # An abort with no turn in flight must be a no-op, not poison the next run.
    client.send({"id": "a", "type": "abort"})
    client.wait_for(lambda r: r.get("id") == "a" and r.get("success") is True)

    client.send({"id": "p", "type": "prompt", "message": "ROOT"})
    records = client.collect_until(lambda r: r.get("type") == "agent_end")
    # The following prompt streams and completes normally (not cancelled).
    assert any(r["type"] == "message_update" for r in records)
    message_end = next(
        r
        for r in records
        if r["type"] == "message_end" and r["message"]["role"] == "assistant"
    )
    assert message_end["message"]["content"] == [{"type": "text", "text": "SEEN:ROOT"}]


def test_aborted_turn_emits_balanced_lifecycle(client) -> None:
    client.send({"id": "p", "type": "prompt", "message": "BLOCK now"})
    client.wait_for(lambda r: r.get("type") == "agent_start")
    client.send({"id": "ab", "type": "abort"})
    records = client.collect_until(lambda r: r.get("type") == "agent_end")
    types = [r["type"] for r in records]
    # Lifecycle stays balanced on abort: every message_start/turn_start has a
    # matching message_end/turn_end before agent_end.
    assert types.count("message_start") == types.count("message_end")
    assert types.count("turn_start") == types.count("turn_end")
    assert types[-1] == "agent_end"


# --------------------------------------------------------------------------
# get_entries / get_tree (read-only session inspection)
# --------------------------------------------------------------------------


def _direct_server(tmp_path: Path, tree: NativeSessionTree):
    adapter = CodingSessionAdapter(provider=AutomationFakeProvider())
    buf = io.BytesIO()
    server = NativeRpcServer(
        adapter=adapter,
        cwd=tmp_path,
        native_session=tree,
        stdin=io.StringIO(),
        stdout_buffer=buf,
        error_stream=io.StringIO(),
    )
    return server, buf


def _last_line(buf: io.BytesIO) -> str:
    return buf.getvalue().decode("utf-8").splitlines()[-1]


def _last_record(buf: io.BytesIO) -> dict:
    return json.loads(_last_line(buf))


def _seed_two(tmp_path: Path):
    from pipy_harness.native.agent import (
        AgentAssistantMessage,
        AgentUserMessage,
        ProductContent,
    )

    tree = NativeSessionTree.create(tmp_path, persist=False)
    root = tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    reply = tree.append_message(AgentAssistantMessage(content=ProductContent("REPLY")))
    return tree, root, reply


def test_get_entries_returns_all_entries_and_leaf(tmp_path: Path) -> None:
    tree, root, reply = _seed_two(tmp_path)
    server, buf = _direct_server(tmp_path, tree)
    server._cmd_get_entries("g", {})
    rec = _last_record(buf)
    assert rec["id"] == "g"
    assert rec["command"] == "get_entries"
    assert rec["success"] is True
    data = rec["data"]
    assert [e["id"] for e in data["entries"]] == [root.id, reply.id]
    assert data["leafId"] == reply.id
    first = data["entries"][0]
    assert first["type"] == "message"
    assert first["parentId"] is None and "timestamp" in first


def test_get_entries_since_slices_after_the_match(tmp_path: Path) -> None:
    tree, root, reply = _seed_two(tmp_path)
    server, buf = _direct_server(tmp_path, tree)
    server._cmd_get_entries("g", {"since": root.id})
    assert [e["id"] for e in _last_record(buf)["data"]["entries"]] == [reply.id]
    # since == last entry -> empty tail.
    server._cmd_get_entries("g", {"since": reply.id})
    assert _last_record(buf)["data"]["entries"] == []


def test_get_entries_unknown_since_errors(tmp_path: Path) -> None:
    tree, _root, _reply = _seed_two(tmp_path)
    server, buf = _direct_server(tmp_path, tree)
    server._cmd_get_entries("g", {"since": "nope"})
    rec = _last_record(buf)
    assert rec["success"] is False
    assert rec["command"] == "get_entries"
    assert rec["error"] == "Entry not found: nope"


def test_get_entries_explicit_null_since_errors_as_null(tmp_path: Path) -> None:
    # Pi gates on `since !== undefined`, so an explicit null is present and
    # errors (never returns the full list), and renders as the JS `null`.
    tree, _root, _reply = _seed_two(tmp_path)
    server, buf = _direct_server(tmp_path, tree)
    server._cmd_get_entries("g", {"since": None})
    rec = _last_record(buf)
    assert rec["success"] is False
    assert rec["error"] == "Entry not found: null"


def test_get_tree_returns_nested_nodes_and_leaf(tmp_path: Path) -> None:
    tree, root, reply = _seed_two(tmp_path)
    server, buf = _direct_server(tmp_path, tree)
    server._cmd_get_tree("t", {})
    rec = _last_record(buf)
    assert rec["command"] == "get_tree" and rec["success"] is True
    assert rec["data"]["leafId"] == reply.id
    roots = rec["data"]["tree"]
    assert len(roots) == 1
    node = roots[0]
    assert node["entry"]["id"] == root.id
    assert [c["entry"]["id"] for c in node["children"]] == [reply.id]
    # Unlabelled nodes omit label keys entirely (Pi JSON.stringify undefined).
    assert "label" not in node and "labelTimestamp" not in node


def test_get_tree_includes_resolved_label(tmp_path: Path) -> None:
    tree, root, _reply = _seed_two(tmp_path)
    tree.append_label_change(root.id, "pinned")
    server, buf = _direct_server(tmp_path, tree)
    server._cmd_get_tree("t", {})
    node = _last_record(buf)["data"]["tree"][0]
    assert node["label"] == "pinned"
    assert isinstance(node["labelTimestamp"], str)


def test_get_tree_deep_history_encodes_without_recursionerror(
    tmp_path: Path,
) -> None:
    from pipy_harness.native.agent import AgentUserMessage, ProductContent

    depth = 2000
    tree = NativeSessionTree.create(tmp_path, persist=False)
    for i in range(depth):
        tree.append_message(AgentUserMessage(content=ProductContent(str(i))))
    server, buf = _direct_server(tmp_path, tree)
    # Must not raise RecursionError despite a ~2000-deep nested tree.
    server._cmd_get_tree("d", {})
    raw = _last_line(buf)
    # Depth-safe string assertions (json.loads would itself recurse and fail).
    assert raw.startswith(
        '{"id":"d","type":"response","command":"get_tree","success":true'
    )
    assert raw.count('"children":[') == depth
    assert f'"leafId":"{tree.leaf_id}"' in raw
    assert tree.entries[-1].id in raw


def test_encode_session_tree_is_byte_identical_to_json_dumps(tmp_path: Path) -> None:
    # The iterative encoder must produce byte-for-byte the same output as a
    # canonical (recursive) json.dumps of the equivalent nested structure, in
    # Pi's SessionTreeNode field order (entry, children, label?, labelTimestamp?),
    # with the same compact/ensure_ascii options as serialize_json_line. Proven
    # on a shallow labelled+branched tree where json.dumps is safe.
    from pipy_harness.native.agent import (
        AgentAssistantMessage,
        AgentUserMessage,
        ProductContent,
    )
    from pipy_harness.native.automation.rpc import _encode_session_tree
    from pipy_harness.native.session_tree import _entry_to_json, build_tree_nodes

    tree = NativeSessionTree.create(tmp_path, persist=False)
    root = tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    tree.append_message(AgentAssistantMessage(content=ProductContent("REPLY")))
    tree.append_label_change(root.id, "pinned")
    tree.branch(root.id)
    tree.append_message(AgentUserMessage(content=ProductContent("ALT")))

    roots = build_tree_nodes(tree.entries)

    def to_dict(node) -> dict:  # noqa: ANN001 - SessionTreeNode
        out: dict = {"entry": _entry_to_json(node.entry)}
        out["children"] = [to_dict(child) for child in node.children]
        if node.label is not None:
            out["label"] = node.label
        if node.label_timestamp is not None:
            out["labelTimestamp"] = node.label_timestamp
        return out

    reference = json.dumps(
        [to_dict(r) for r in roots],
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert _encode_session_tree(roots) == reference


def test_rpc_abort_cancels_model_tool_then_next_prompt_succeeds(tmp_path: Path) -> None:
    from test_native_coding_session_resume_compact import _RecordingToolProvider

    from pipy_harness.native.agent.events import RunCancelled
    from pipy_harness.native.models import ProviderToolCall
    from pipy_harness.native.tools import (
        ToolContext,
        ToolDefinition,
        ToolExecutionResult,
        ToolRequest,
    )

    started = threading.Event()
    finished = threading.Event()

    class BlockingTool:
        definition = ToolDefinition(
            "wait", "cooperative fixture", {"type": "object", "properties": {}}
        )

        def invoke(
            self, request: ToolRequest, context: ToolContext
        ) -> ToolExecutionResult:
            assert context.cancel_event is not None
            started.set()
            try:
                assert context.cancel_event.wait(5)
                return ToolExecutionResult(request.tool_request_id, "late result")
            finally:
                finished.set()

    provider = _RecordingToolProvider(
        call_script=((ProviderToolCall("wait-1", "wait", "{}"),),)
    )
    client = _RpcClient(tmp_path, provider=provider, tools={"wait": BlockingTool()})
    try:
        client.send({"id": "one", "type": "prompt", "message": "use model tool"})
        assert started.wait(5)
        client.send({"id": "abort", "type": "abort"})
        client.wait_for(
            lambda r: r.get("type") == "response" and r.get("id") == "abort"
        )
        client.wait_for(lambda r: r.get("type") == "agent_settled")
        assert finished.is_set()
        assert (
            len([e for e in client.canonical.events if isinstance(e, RunCancelled)])
            == 1
        )
        client.send({"id": "two", "type": "prompt", "message": "next succeeds"})
        client.wait_for(lambda r: r.get("type") == "agent_settled")
        assert provider.requests[-1].messages[-1].content.value == "next succeeds"
        assert (
            len([e for e in client.canonical.events if isinstance(e, RunCancelled)])
            == 1
        )
    finally:
        client.close()


def test_rpc_new_session_adopts_native_tree_and_releases_initial_lease(
    tmp_path: Path,
) -> None:
    client = _RpcClient(tmp_path, persist_tree=True)
    original = client.tree.path
    assert original is not None
    try:
        client.send({"id": "ready", "type": "get_state"})
        client.wait_for(lambda record: record.get("id") == "ready")
        with pytest.raises(RuntimeError, match="already active"):
            CanonicalSessionLeaseRegistry.claim(original)
        client.send({"id": "new", "type": "new_session"})
        response = client.wait_for(
            lambda record: (
                record.get("type") == "response" and record.get("id") == "new"
            )
        )
        assert response == {
            "id": "new",
            "type": "response",
            "command": "new_session",
            "success": True,
            "data": {"cancelled": False},
        }
        client.send({"id": "state", "type": "get_state"})
        state = client.wait_for(lambda record: record.get("id") == "state")
        assert state["data"]["sessionFile"] != str(original)
        assert client.adapter.native_session is client._server._tree
        adopted_tree = client._server._tree
        assert isinstance(adopted_tree, NativeSessionTree)
        adopted = adopted_tree.path
        assert adopted is not None
        released_old = CanonicalSessionLeaseRegistry.claim(original)
        released_old.finish()
        with pytest.raises(RuntimeError, match="already active"):
            CanonicalSessionLeaseRegistry.claim(adopted)
    finally:
        client.close()
    released = CanonicalSessionLeaseRegistry.claim(original)
    released.finish()
    released_adopted = CanonicalSessionLeaseRegistry.claim(adopted)
    released_adopted.finish()


def test_rpc_transition_busy_refuses_without_queueing_then_succeeds(
    tmp_path: Path,
) -> None:
    provider = _BlockingFirstAutomationProvider()
    client = _RpcClient(tmp_path, provider=provider, persist_tree=True)
    try:
        client.send({"id": "prompt", "type": "prompt", "message": "hold"})
        client.wait_for(lambda record: record.get("id") == "prompt")
        deadline = time.monotonic() + 5
        while not provider.requests and time.monotonic() < deadline:
            time.sleep(0.01)
        assert provider.requests

        original = client._server._tree
        client.send({"id": "busy", "type": "new_session"})
        busy = client.wait_for(lambda record: record.get("id") == "busy")
        assert busy == {
            "id": "busy",
            "type": "response",
            "command": "new_session",
            "success": False,
            "error": "session is not idle",
        }
        assert client._server._tree is original

        provider.release()
        client.wait_for(lambda record: record.get("type") == "agent_settled")
        client.send({"id": "after", "type": "new_session"})
        assert client.wait_for(lambda record: record.get("id") == "after")["success"]
        assert client._server._tree is not original
    finally:
        client.close()


def test_rpc_initial_lease_releases_when_readiness_startup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_ready(
        _self: _NativeSessionControlBridge, *_args: object, **_kwargs: object
    ) -> None:
        raise RuntimeError("injected readiness failure")

    monkeypatch.setattr(_NativeSessionControlBridge, "publish_ready", fail_ready)
    client = _RpcClient(tmp_path, persist_tree=True)
    path = client.tree.path
    assert path is not None
    try:
        client._server_thread.join(timeout=5)
        assert not client._server_thread.is_alive()
        released = CanonicalSessionLeaseRegistry.claim(path)
        released.finish()
    finally:
        client.close()


def test_rpc_session_transition_validates_and_projects_idle_refusal(
    tmp_path: Path,
) -> None:
    client = _RpcClient(tmp_path, persist_tree=True)
    try:
        client.send({"id": "parent", "type": "new_session", "parentSession": "x"})
        parent = client.wait_for(lambda record: record.get("id") == "parent")
        assert parent["success"] is False
        assert parent["error"] == "parentSession is not supported"

        client.send({"id": "fork", "type": "fork"})
        fork = client.wait_for(lambda record: record.get("id") == "fork")
        assert fork["success"] is False
        assert fork["error"] == "fork requires entryId"

        client.send({"id": "clone", "type": "clone"})
        clone = client.wait_for(lambda record: record.get("id") == "clone")
        assert clone["success"] is False
        assert clone["error"] == "session transition refused: missing_leaf"
    finally:
        client.close()


def test_rpc_fork_clone_and_switch_use_the_admitted_native_transition_port(
    tmp_path: Path,
) -> None:
    client = _RpcClient(tmp_path, persist_tree=True)
    original = client.tree.path
    assert original is not None
    try:
        client.send({"id": "prompt", "type": "prompt", "message": "root"})
        client.wait_for(lambda record: record.get("id") == "prompt")
        client.wait_for(lambda record: record.get("type") == "agent_settled")
        entry_id = client._server._tree.leaf_id
        assert entry_id is not None

        client.send({"id": "fork", "type": "fork", "entryId": entry_id})
        fork = client.wait_for(lambda record: record.get("id") == "fork")
        assert fork["data"] == {"text": "Forked native session", "cancelled": False}
        fork_path = client._server._tree.path
        assert fork_path is not None and fork_path != original

        client.send({"id": "clone", "type": "clone"})
        clone = client.wait_for(lambda record: record.get("id") == "clone")
        assert clone["data"] == {"cancelled": False}

        client.send(
            {"id": "switch", "type": "switch_session", "sessionPath": str(original)}
        )
        switched = client.wait_for(lambda record: record.get("id") == "switch")
        assert switched["data"] == {"cancelled": False}
        assert client._server._tree.path == original

        client.send(
            {"id": "same", "type": "switch_session", "sessionPath": str(original)}
        )
        same = client.wait_for(lambda record: record.get("id") == "same")
        assert same["data"] == {"cancelled": False}
    finally:
        client.close()


def test_rpc_switch_rebinds_every_tree_projection_and_next_prompt_persists(
    tmp_path: Path,
) -> None:
    client = _RpcClient(tmp_path, persist_tree=True)
    try:
        target = NativeSessionTree.create(tmp_path, persist=True)
        target.append_message(AgentUserMessage(ProductContent("adopted history")))
        target.append_session_info("adopted name")
        assert target.path is not None
        target_path = target.path

        client.send(
            {
                "id": "switch",
                "type": "switch_session",
                "sessionPath": str(target_path),
            }
        )
        assert client.wait_for(lambda record: record.get("id") == "switch")["data"] == {
            "cancelled": False
        }

        for command in (
            "get_state",
            "get_messages",
            "get_entries",
            "get_tree",
            "get_session_stats",
        ):
            client.send({"id": command, "type": command})
        state = client.wait_for(lambda record: record.get("id") == "get_state")
        messages = client.wait_for(lambda record: record.get("id") == "get_messages")
        entries = client.wait_for(lambda record: record.get("id") == "get_entries")
        tree = client.wait_for(lambda record: record.get("id") == "get_tree")
        stats = client.wait_for(lambda record: record.get("id") == "get_session_stats")
        assert state["data"]["sessionFile"] == str(target_path)
        assert state["data"]["sessionName"] == "adopted name"
        assert messages["data"]["messages"][0]["content"] == [
            {"type": "text", "text": "adopted history"}
        ]
        assert entries["data"]["entries"][-1]["type"] == "session_info"
        assert tree["data"]["leafId"] == target.leaf_id
        assert stats["data"]["sessionFile"] == str(target_path)

        client.send({"id": "prompt", "type": "prompt", "message": "adopted prompt"})
        client.wait_for(lambda record: record.get("id") == "prompt")
        client.wait_for(lambda record: record.get("type") == "agent_settled")
        reopened = NativeSessionTree.open(target_path, strict=True)
        assert any(
            getattr(getattr(entry, "message", None), "content", None)
            == ProductContent("adopted prompt")
            for entry in reopened.entries
        )
    finally:
        client.close()


def test_rpc_transition_prepublication_failure_keeps_old_tree_usable(
    tmp_path: Path,
) -> None:
    client = _RpcClient(tmp_path, persist_tree=True)
    try:
        client.send({"id": "ready", "type": "get_state"})
        client.wait_for(lambda record: record.get("id") == "ready")
        transition_port = client._server._transition
        assert transition_port is not None
        transition = transition_port.transition
        original = client._server._tree
        object.__setattr__(
            transition,
            "set_tree",
            lambda _candidate: (_ for _ in ()).throw(LookupError("publish")),
        )
        client.send({"id": "new", "type": "new_session"})
        failure = client.wait_for(lambda record: record.get("id") == "new")
        assert failure["success"] is False
        assert client._server._tree is original

        client.send({"id": "prompt", "type": "prompt", "message": "still usable"})
        client.wait_for(lambda record: record.get("id") == "prompt")
        client.wait_for(lambda record: record.get("type") == "agent_settled")
    finally:
        client.close()


def test_rpc_strict_load_and_lease_failures_retain_old_tree(
    tmp_path: Path,
) -> None:
    client = _RpcClient(tmp_path, persist_tree=True)
    try:
        client.send({"id": "ready", "type": "get_state"})
        before = client.wait_for(lambda record: record.get("id") == "ready")
        malformed = tmp_path / "malformed.jsonl"
        malformed.write_text("not json\n", encoding="utf-8")
        client.send(
            {"id": "malformed", "type": "switch_session", "sessionPath": str(malformed)}
        )
        malformed_response = client.wait_for(
            lambda record: record.get("id") == "malformed"
        )
        assert malformed_response["success"] is False

        missing = tmp_path / "missing.jsonl"
        client.send(
            {"id": "missing", "type": "switch_session", "sessionPath": str(missing)}
        )
        assert not client.wait_for(lambda record: record.get("id") == "missing")[
            "success"
        ]

        foreign_workspace = tmp_path / "foreign-workspace"
        foreign_workspace.mkdir()
        foreign = NativeSessionTree.create(foreign_workspace, persist=True)
        assert foreign.path is not None
        client.send(
            {
                "id": "foreign",
                "type": "switch_session",
                "sessionPath": str(foreign.path),
            }
        )
        assert not client.wait_for(lambda record: record.get("id") == "foreign")[
            "success"
        ]

        target = NativeSessionTree.create(tmp_path, persist=True)
        assert target.path is not None
        held = CanonicalSessionLeaseRegistry.claim(target.path)
        try:
            client.send(
                {
                    "id": "leased",
                    "type": "switch_session",
                    "sessionPath": str(target.path),
                }
            )
            leased_response = client.wait_for(
                lambda record: record.get("id") == "leased"
            )
            assert leased_response["success"] is False
            assert (
                leased_response["error"] == "session transition refused: lease_conflict"
            )
        finally:
            held.finish()

        client.send({"id": "after", "type": "get_state"})
        after = client.wait_for(lambda record: record.get("id") == "after")
        assert after["data"]["sessionFile"] == before["data"]["sessionFile"]
    finally:
        client.close()


@pytest.mark.parametrize(
    "body",
    (
        "        return SessionDecision(allow=False)\n",
        "        raise RuntimeError('private extension failure')\n",
    ),
)
def test_rpc_extension_refusal_projects_cancelled_without_rebinding(
    tmp_path: Path, body: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    extension = tmp_path / ".pipy" / "extensions" / "switch_gate.py"
    extension.parent.mkdir(parents=True)
    extension.write_text(
        "from pipy_harness.extensions import SessionDecision\n"
        "def activate(api):\n"
        "    @api.on('session_before_switch')\n"
        "    def gate(event, ctx):\n"
        "        assert event.operation == 'switch'\n"
        "        assert event.target == 'new'\n" + body,
        encoding="utf-8",
    )
    starts: list[object] = []
    shutdowns: list[object] = []
    start = loop_step_module._ReplLoopStep.fire_session_start
    shutdown = loop_step_module._ReplLoopStep.fire_session_shutdown

    def record_start(self: object, **kwargs: object) -> None:
        starts.append(self)
        start(self, **kwargs)  # type: ignore[arg-type]

    def record_shutdown(self: object, **kwargs: object) -> None:
        shutdowns.append(self)
        shutdown(self, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        loop_step_module._ReplLoopStep, "fire_session_start", record_start
    )
    monkeypatch.setattr(
        loop_step_module._ReplLoopStep, "fire_session_shutdown", record_shutdown
    )
    client = _RpcClient(
        tmp_path,
        persist_tree=True,
        resource_options=RuntimeResourceOptions(extension_paths=(extension,)),
    )
    try:
        client.send({"id": "ready", "type": "get_state"})
        client.wait_for(lambda record: record.get("id") == "ready")
        original = client._server._tree
        client.send({"id": "new", "type": "new_session"})
        response = client.wait_for(lambda record: record.get("id") == "new")
        assert response["data"] == {"cancelled": True}
        assert client._server._tree is original
        assert client.adapter.native_session is original
    finally:
        client.close()
    assert len(starts) == 1
    assert len(shutdowns) == 1


def test_rpc_published_transition_failure_holds_lease_until_controller_retires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutdown_entered = threading.Event()
    release_shutdown = threading.Event()
    shutdown = loop_step_module._ReplLoopStep.fire_session_shutdown

    def block_shutdown(self: object, **kwargs: object) -> None:
        shutdown_entered.set()
        assert release_shutdown.wait(timeout=5)
        shutdown(self, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        loop_step_module._ReplLoopStep, "fire_session_shutdown", block_shutdown
    )
    client = _RpcClient(tmp_path, persist_tree=True)
    try:
        client.send({"id": "ready", "type": "get_state"})
        client.wait_for(lambda record: record.get("id") == "ready")
        transition_port = client._server._transition
        assert transition_port is not None
        transition = transition_port.transition
        object.__setattr__(
            transition,
            "rebuild",
            lambda: (_ for _ in ()).throw(LookupError("rebuild")),
        )
        client._stdin_write.write(
            json.dumps({"id": "new", "type": "new_session"})
            + "\n"
            + json.dumps({"id": "successor", "type": "get_state"})
            + "\n"
        )
        client._stdin_write.flush()
        failure = client.wait_for(lambda record: record.get("id") == "new")
        assert failure["success"] is False
        assert shutdown_entered.wait(timeout=5)
        active_path = transition_port.leases.current_path()
        assert active_path is not None
        with pytest.raises(RuntimeError, match="already active"):
            CanonicalSessionLeaseRegistry.claim(active_path)
        release_shutdown.set()
        client._server_thread.join(timeout=5)
        assert not client._server_thread.is_alive()
        assert client._server._retired is True
        assert transition_port.leases.current_path() is None
        assert not any(record.get("id") == "successor" for record in client._seen)
    finally:
        release_shutdown.set()
        client.close()


def test_rpc_transition_response_without_id_keeps_command_correlation(
    tmp_path: Path,
) -> None:
    client = _RpcClient(tmp_path, persist_tree=True)
    try:
        client.send({"type": "new_session"})
        response = client.wait_for(
            lambda record: record.get("command") == "new_session"
        )
        assert response == {
            "type": "response",
            "command": "new_session",
            "success": True,
            "data": {"cancelled": False},
        }
    finally:
        client.close()


def test_rpc_transition_settlement_failure_retires_and_releases_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _RpcClient(tmp_path, persist_tree=True)
    try:
        client.send({"id": "ready", "type": "get_state"})
        client.wait_for(lambda record: record.get("id") == "ready")
        transition_port = client._server._transition
        assert transition_port is not None

        def fail_settle(_self: _NativeSessionControlBridge, _claim: object) -> object:
            raise RuntimeError("injected settle failure")

        monkeypatch.setattr(
            _NativeSessionControlBridge, "settle_transition_operation", fail_settle
        )
        client._stdin_write.write(
            json.dumps({"id": "new", "type": "new_session"})
            + "\n"
            + json.dumps({"id": "successor", "type": "get_state"})
            + "\n"
        )
        client._stdin_write.flush()
        failure = client.wait_for(lambda record: record.get("id") == "new")
        assert failure["success"] is False
        client._server_thread.join(timeout=5)
        assert not client._server_thread.is_alive()
        assert client._server._retired is True
        assert transition_port.leases.current_path() is None
        assert not any(record.get("id") == "successor" for record in client._seen)
    finally:
        client.close()


def test_rpc_transition_rebind_failure_retires_and_releases_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _RpcClient(tmp_path, persist_tree=True)
    try:
        client.send({"id": "ready", "type": "get_state"})
        client.wait_for(lambda record: record.get("id") == "ready")
        transition_port = client._server._transition
        assert transition_port is not None
        monkeypatch.setattr(
            NativeRpcServer,
            "_rebind_transition_tree",
            lambda _self: (_ for _ in ()).throw(
                RuntimeError("injected rebind failure")
            ),
        )
        client._stdin_write.write(
            json.dumps({"id": "new", "type": "new_session"})
            + "\n"
            + json.dumps({"id": "successor", "type": "get_state"})
            + "\n"
        )
        client._stdin_write.flush()
        failure = client.wait_for(lambda record: record.get("id") == "new")
        assert failure["success"] is False
        client._server_thread.join(timeout=5)
        assert not client._server_thread.is_alive()
        assert client._server._retired is True
        assert transition_port.leases.current_path() is None
        assert not any(record.get("id") == "successor" for record in client._seen)
    finally:
        client.close()
