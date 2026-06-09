"""Integration tests for the `--mode rpc` JSONL protocol server.

Drives :class:`NativeRpcServer` over real OS pipes with a deterministic,
tool-capable fake provider, exercising the Pi command/response/event vocabulary:
async ``prompt`` with a streamed event sequence, ``get_state``/``get_messages``/
``get_session_stats``, ``bash``, mid-turn ``steer`` (``queue_update``) and
``abort``, ``set_session_name``, unknown-command and parse-error envelopes,
and clean EOF shutdown.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from pathlib import Path

import pytest

from pipy_harness.adapters.native import PipyNativeToolReplAdapter
from pipy_harness.native.automation.jsonl import JsonlLineBuffer
from pipy_harness.native.automation.rpc import NativeRpcServer
from pipy_harness.native.fake import AutomationFakeProvider
from pipy_harness.native.session_tree import NativeSessionTree


class _RpcClient:
    def __init__(self, tmp_path: Path) -> None:
        self._cwd = tmp_path
        stdin_r, self._stdin_w = os.pipe()
        self._stdout_r, stdout_w = os.pipe()
        self._stdin_read = os.fdopen(stdin_r, "r")
        self._stdin_write = os.fdopen(self._stdin_w, "w")
        self._stdout_read = os.fdopen(self._stdout_r, "rb")
        stdout_buffer = os.fdopen(stdout_w, "wb")

        adapter = PipyNativeToolReplAdapter(
            provider=AutomationFakeProvider(block_timeout_seconds=5.0)
        )
        tree = NativeSessionTree.create(tmp_path, persist=False)
        self._server = NativeRpcServer(
            adapter=adapter,
            cwd=tmp_path,
            native_session=tree,
            stdin=self._stdin_read,
            stdout_buffer=stdout_buffer,
            error_stream=open(os.devnull, "w"),
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
        return 0


@pytest.fixture()
def client(tmp_path: Path):
    c = _RpcClient(tmp_path)
    c._seen = []
    try:
        yield c
    finally:
        c.close()


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
        if r.get("type") == "message_start" and r.get("message", {}).get("role") == "user"
    ]
    assert "ROOT" in user_texts
    assert "SECOND" in user_texts


def test_prompt_emits_correlated_success_then_event_sequence(client) -> None:
    client.send({"id": "r1", "type": "prompt", "message": "ROOT"})
    records = client.collect_until(lambda r: r.get("type") == "agent_end")

    # The correlated prompt success precedes the event stream.
    success = records[0]
    assert success == {"id": "r1", "type": "response", "command": "prompt", "success": True}

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
        lambda r: r.get("type") == "queue_update" and "second prompt" in r.get("followUp", [])
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
