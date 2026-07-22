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
from pathlib import Path

import pytest

import pipy_harness.native.tool_loop_session as loop_module
from pipy_harness.adapters.native import PipyNativeToolReplAdapter
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
    AgentQueuedInputKind,
)
from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.catalog_state import ProviderCatalogState
from pipy_harness.native.automation.jsonl import JsonlLineBuffer
from pipy_harness.native.automation.rpc import NativeRpcServer, _PromptChannel
from pipy_harness.native.fake import AutomationFakeProvider
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import ProviderPort, StreamChunkSink
from pipy_harness.native.repl_state import (
    ModelRuntime,
    NativeModelSelection,
    NativeReplProviderState,
)
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tool_loop_session import NativeToolReplSession


class _PromptExitBarrierLock:
    """Pause a prompt after its first state-lock hold is released.

    With the old split-lock prompt path, that first hold only read
    ``_turn_active``; pausing here let ``agent_end`` settle before the prompt
    reacquired the lock to enqueue, deterministically stranding it. The fixed
    path classifies and mutates state during that first hold, so settlement must
    reserve the prompt instead.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.prompt_first_hold_released = threading.Event()
        self.allow_prompt_to_continue = threading.Event()
        self._prompt_paused = False

    def __enter__(self) -> None:
        self._lock.acquire()

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self._lock.release()
        if (
            threading.current_thread().name == "racing-prompt"
            and not self._prompt_paused
        ):
            self._prompt_paused = True
            self.prompt_first_hold_released.set()
            if not self.allow_prompt_to_continue.wait(timeout=5.0):
                raise AssertionError("prompt barrier was not released")


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


class _RpcClient:
    def __init__(self, tmp_path: Path, *, provider: ProviderPort | None = None) -> None:
        self._cwd = tmp_path
        stdin_r, self._stdin_w = os.pipe()
        self._stdout_r, stdout_w = os.pipe()
        self._stdin_read = os.fdopen(stdin_r, "r")
        self._stdin_write = os.fdopen(self._stdin_w, "w")
        self._stdout_read = os.fdopen(self._stdout_r, "rb")
        self._stdout_buffer = os.fdopen(stdout_w, "wb")
        self._error_stream = open(os.devnull, "w")

        self.canonical = _CanonicalCollectingSink()
        adapter = PipyNativeToolReplAdapter(
            provider=(
                provider
                if provider is not None
                else AutomationFakeProvider(block_timeout_seconds=5.0)
            ),
            agent_event_sink=self.canonical,
        )
        tree = NativeSessionTree.create(tmp_path, persist=False)
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

    _seen: list = []

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


def _provider_state_adapter(tmp_path: Path) -> PipyNativeToolReplAdapter:
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
    return PipyNativeToolReplAdapter(provider_state=state)


def test_set_thinking_level_updates_provider_state_before_construction(
    tmp_path: Path,
) -> None:
    adapter = _provider_state_adapter(tmp_path)
    tree = NativeSessionTree.create(tmp_path, persist=False)
    server = NativeRpcServer(
        adapter=adapter,
        cwd=tmp_path,
        native_session=tree,
        stdin=io.StringIO(),
        stdout_buffer=io.BytesIO(),
        error_stream=io.StringIO(),
    )

    server._cmd_set_thinking_level("t", {"level": "high"})

    assert adapter.provider_state is not None
    assert adapter.provider_state.thinking_level == "high"
    provider = adapter._current_provider()
    assert getattr(provider, "reasoning_effort", None) == "high"


def test_cycle_thinking_level_updates_provider_state_before_construction(
    tmp_path: Path,
) -> None:
    adapter = _provider_state_adapter(tmp_path)
    tree = NativeSessionTree.create(tmp_path, persist=False)
    server = NativeRpcServer(
        adapter=adapter,
        cwd=tmp_path,
        native_session=tree,
        stdin=io.StringIO(),
        stdout_buffer=io.BytesIO(),
        error_stream=io.StringIO(),
    )

    server._cmd_cycle_thinking_level("t", {})

    assert adapter.provider_state is not None
    assert adapter.provider_state.thinking_level == "minimal"
    provider = adapter._current_provider()
    assert getattr(provider, "reasoning_effort", None) == "minimal"


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


def test_agent_settled_emitted_after_idle(client) -> None:
    client.send({"id": "r1", "type": "prompt", "message": "ROOT"})
    records = client.collect_until(lambda r: r.get("type") == "agent_settled")

    types = [r["type"] for r in records]
    # `agent_settled` is the idle boundary: it is the final line and comes
    # strictly after the run's `agent_end`, with nothing between them.
    assert types[-1] == "agent_settled"
    assert types[-2] == "agent_end"
    # Pi's `agent_settled` carries no payload fields.
    assert records[-1] == {"type": "agent_settled"}


def test_prompt_racing_agent_end_is_reserved_not_stranded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stdout = io.BytesIO()
    adapter = PipyNativeToolReplAdapter(provider=AutomationFakeProvider())
    tree = NativeSessionTree.create(tmp_path, persist=False)
    server = NativeRpcServer(
        adapter=adapter,
        cwd=tmp_path,
        native_session=tree,
        stdin=io.StringIO(),
        stdout_buffer=stdout,
        error_stream=io.StringIO(),
    )
    server._turn_active = True
    barrier = _PromptExitBarrierLock()
    monkeypatch.setattr(server, "_lock", barrier)

    failures: "queue.Queue[BaseException]" = queue.Queue()

    def submit_prompt() -> None:
        try:
            server._cmd_prompt("p", {"type": "prompt", "message": "NEXT"})
        except BaseException as exc:  # pragma: no cover - surfaced below
            failures.put(exc)

    prompt_thread = threading.Thread(
        target=submit_prompt,
        name="racing-prompt",
        daemon=True,
    )
    prompt_thread.start()
    assert barrier.prompt_first_hold_released.wait(timeout=5.0)

    # The prompt has completed its first state-lock hold. Settlement now runs
    # before that prompt thread can continue. A split read/append would settle
    # idle and then strand NEXT; the atomic path has already queued NEXT, so this
    # boundary reserves it and suppresses agent_settled.
    settle_thread = threading.Thread(
        target=lambda: server.emit({"type": "agent_end", "willRetry": False}),
        daemon=True,
    )
    settle_thread.start()
    settle_thread.join(timeout=5.0)
    assert not settle_thread.is_alive(), "agent_end settlement deadlocked"

    barrier.allow_prompt_to_continue.set()
    prompt_thread.join(timeout=5.0)
    assert not prompt_thread.is_alive(), "prompt remained blocked after settlement"
    if not failures.empty():
        raise failures.get()

    records = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [record["type"] for record in records].count("agent_settled") == 0
    assert records[0]["type"] == "agent_end"
    with server._lock:
        assert server._turn_active is True
        assert server._steering == []
        assert server._follow_up == []
    queued = server._channel._q.get_nowait()
    assert queued is not None
    assert queued.line == "NEXT\n"
    assert queued.content == "NEXT"
    assert queued.kind == "follow_up"


def test_prompt_channel_classifies_only_the_just_delivered_line() -> None:
    channel = _PromptChannel()
    channel.push("ordinary prompt")

    assert channel.readline() == "ordinary prompt\n"
    classified = AgentQueuedInput(
        ProductContent("classified\n\n"),
        AgentQueuedInputKind.FOLLOW_UP,
    )
    channel.push(classified.content.value, kind=classified.kind)

    assert channel.take_next() is None
    assert channel.take_next() == classified


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


def test_cycle_model_returns_explicit_null_data(client) -> None:
    # Single configured model: cycle_model must carry an explicit `data: null`
    # (Pi's `... | null` contract), not omit the data field.
    client.send({"id": "c", "type": "cycle_model"})
    resp = client.wait_for(lambda r: r.get("id") == "c")
    assert resp["command"] == "cycle_model"
    assert resp["success"] is True
    assert "data" in resp
    assert resp["data"] is None


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
        _session: NativeToolReplSession,
        command_text: str,
        **_kwargs: object,
    ) -> None:
        shell_calls.append(command_text)

    monkeypatch.setattr(
        NativeToolReplSession,
        "_run_local_shell_shortcut",
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
    assert taken == [
        AgentQueuedInput(ProductContent(queued_slash), AgentQueuedInputKind.STEERING),
        AgentQueuedInput(
            ProductContent("!rpc-queued-shell"), AgentQueuedInputKind.FOLLOW_UP
        ),
    ]

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
    adapter = PipyNativeToolReplAdapter(provider=AutomationFakeProvider())
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
    from pipy_harness.native.session_tree import _entry_to_json, build_tree_nodes
    from pipy_harness.native.automation.rpc import _encode_session_tree
    from pipy_harness.native.agent import (
        AgentAssistantMessage,
        AgentUserMessage,
        ProductContent,
    )

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
