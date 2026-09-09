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
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Never, cast

import pytest

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
from pipy_harness.native.coding.session_controller import _NativeControlFailed
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
from pipy_harness.native.repl_state import (
    ModelRuntime,
    NativeModelSelection,
    NativeReplProviderState,
)
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
    resp = client.wait_for(lambda r: r.get("id") == "b")
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
