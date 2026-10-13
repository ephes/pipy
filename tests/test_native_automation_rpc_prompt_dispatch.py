"""An RPC ``prompt`` is dispatched as Pi's ``AgentSession.prompt`` dispatches
it, and its response says what became of it (MCP parity plan X1, rows SLH-07
to SLH-10).

The server runs over real pipes with a scripted provider. The same rules for
``--print`` and ``--mode json`` are in
``test_native_automation_prompt_dispatch.py``.
"""

from __future__ import annotations

import io
import json
import os
import queue
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from test_native_automation_prompt_dispatch import _COMMANDS, _mcp_json, _resource
from test_native_extension_tool_metadata import _result
from test_native_mcp_session import _extension, _Provider, _server, _settings

from pipy_harness.adapters.native import CodingSessionAdapter
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.automation.rpc import NativeRpcServer
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tools.registry import production_tool_registry


class _Client:
    """One RPC server and the two ends of its pipes."""

    def __init__(self, workspace: Path, provider: _Provider) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        stdin_r, stdin_w = os.pipe()
        stdout_r, stdout_w = os.pipe()
        self._stdin_read = os.fdopen(stdin_r, "r")
        self._stdin_write = os.fdopen(stdin_w, "w")
        self._stdout_read = os.fdopen(stdout_r, "r", encoding="utf-8", newline="\n")
        self._stdout_write = os.fdopen(stdout_w, "wb")
        self.errors = io.StringIO()
        self._server = NativeRpcServer(
            adapter=CodingSessionAdapter(
                provider=provider,
                tool_registry=production_tool_registry(),
                settings_manager=_settings(workspace),
            ),
            cwd=workspace,
            native_session=NativeSessionTree.create(workspace, persist=False),
            stdin=self._stdin_read,
            stdout_buffer=self._stdout_write,
            error_stream=self.errors,
        )
        self._records: queue.Queue[dict[str, Any]] = queue.Queue()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._reader.start()
        self._thread.start()

    def _read(self) -> None:
        for line in self._stdout_read:
            self._records.put(json.loads(line))

    def send(self, **command: Any) -> None:
        self._stdin_write.write(json.dumps(command) + "\n")
        self._stdin_write.flush()

    def until(
        self, done: Callable[[dict[str, Any]], bool], timeout: float = 10.0
    ) -> list[dict[str, Any]]:
        """The records up to and including the first one ``done`` accepts."""

        records: list[dict[str, Any]] = []
        while True:
            try:
                record = self._records.get(timeout=timeout)
            except queue.Empty:
                raise AssertionError(
                    f"timed out; got {records}; stderr {self.errors.getvalue()!r}"
                ) from None
            records.append(record)
            if done(record):
                return records

    def response(self, request_id: str) -> dict[str, Any]:
        return self.until(lambda record: record.get("id") == request_id)[-1]

    def close(self) -> list[dict[str, Any]]:
        """End the session; returns what was written and not read yet."""

        self._stdin_write.close()
        self._thread.join(timeout=20.0)
        assert not self._thread.is_alive(), "RPC server did not stop after EOF"
        self._stdout_write.close()
        self._reader.join(timeout=5.0)
        self._stdout_read.close()
        self._stdin_read.close()
        rest: list[dict[str, Any]] = []
        while not self._records.empty():
            rest.append(self._records.get())
        return rest


@pytest.fixture()
def connect(tmp_path: Path) -> Iterator[Callable[[_Provider], _Client]]:
    clients: list[_Client] = []

    def start(provider: _Provider) -> _Client:
        clients.append(_Client(tmp_path / "work", provider))
        return clients[-1]

    yield start
    for client in clients:
        if client._thread.is_alive():
            client.close()


def _is_response(record: dict[str, Any]) -> bool:
    return record.get("type") == "response"


def _without_id(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "id"}


def _is_request(method: str) -> Callable[[dict[str, Any]], bool]:
    return lambda record: (
        record.get("type") == "extension_ui_request" and record.get("method") == method
    )


def test_mcp_is_handled_and_starts_no_run(tmp_path: Path, connect) -> None:
    # SLH-02, SLH-07, SLH-10: the response comes when the command has run,
    # it says `handled`, and no session event follows.
    workspace = tmp_path / "work"
    entry, _record = _server(tmp_path / "servers")
    _mcp_json(workspace, {"docs": {**entry, "exposure": "direct"}})
    provider = _Provider([])
    client = connect(provider)

    client.send(id="p", type="prompt", message="/mcp")
    records = client.until(_is_response)

    # SLH-11: the status is a `notify` request, sent before the response.
    assert [_without_id(record) for record in records] == [
        {
            "type": "extension_ui_request",
            "method": "notify",
            "message": "docs: connected, 2 tools (direct)",
            "notifyType": "info",
        },
        {
            "type": "response",
            "command": "prompt",
            "success": True,
            "data": {"disposition": "handled"},
        },
    ]
    assert records[0]["id"] != records[1]["id"] == "p"
    assert client.errors.getvalue() == ""
    assert client.close() == []
    assert provider.requests == []


def test_a_prompt_after_a_handled_command_runs(tmp_path: Path, connect) -> None:
    # The handled prompt's claim is settled: the session is idle again, and
    # the next prompt is the next run.
    workspace = tmp_path / "work"
    directory = _extension(workspace, "commands", _COMMANDS)
    provider = _Provider([_result(final_text="answer")])
    client = connect(provider)

    client.send(id="c", type="prompt", message="/note first")
    handled = client.until(_is_response)
    assert handled[-1]["data"] == {"disposition": "handled"}
    # An extension's notice is a `notify` request too.
    assert [(record["method"], record["message"]) for record in handled[:-1]] == [
        ("notify", "noted first")
    ]
    assert (directory / "note.txt").read_text() == repr("first")
    client.send(id="s", type="get_state")
    assert client.response("s")["data"]["isStreaming"] is False

    client.send(id="p", type="prompt", message="go on")
    records = client.until(lambda record: record.get("type") == "agent_settled")

    assert records[0]["data"] == {"disposition": "started"}
    assert records[1] == {"type": "agent_start"}
    assert [request.user_prompt for request in provider.requests] == ["go on"]


def test_a_failing_command_is_handled_too(tmp_path: Path, connect) -> None:
    workspace = tmp_path / "work"
    _extension(workspace, "commands", _COMMANDS)
    client = connect(_Provider([]))

    client.send(id="c", type="prompt", message="/broken")

    assert client.response("c")["data"] == {"disposition": "handled"}
    assert client.close() == []
    assert "extension command /broken failed" in client.errors.getvalue()


@pytest.mark.parametrize("prompt", ["/model stub/other", "/nosuch thing", "!echo x"])
def test_other_slash_texts_start_a_run(connect, prompt: str) -> None:
    # SLH-08.
    provider = _Provider([_result(final_text="answer")])
    client = connect(provider)

    client.send(id="p", type="prompt", message=prompt)
    records = client.until(lambda record: record.get("type") == "agent_settled")

    assert records[0]["data"] == {"disposition": "started"}
    user = [
        record["message"]["content"]
        for record in records
        if record["type"] == "message_end" and record["message"]["role"] == "user"
    ]
    assert user == [[{"type": "text", "text": prompt}]]
    assert [request.user_prompt for request in provider.requests] == [prompt]


def test_a_prompt_template_is_expanded(tmp_path: Path, connect) -> None:
    # SLH-09.
    workspace = tmp_path / "work"
    _resource(workspace / ".pipy" / "templates", "review", "Review $ARGUMENTS.\n")
    provider = _Provider([_result(final_text="done")])
    client = connect(provider)

    client.send(id="p", type="prompt", message="/review the diff")
    records = client.until(lambda record: record.get("type") == "agent_settled")

    assert records[0]["data"] == {"disposition": "started"}
    assert provider.requests[0].user_prompt.strip() == "Review the diff."


def test_a_prompt_behind_a_run_is_queued(connect) -> None:
    # SLH-10: `queued`, answered at once; `steer` and `follow_up` say the
    # same. What is queued is a message, not a command.
    entered, release = threading.Event(), threading.Event()

    def hold() -> None:
        entered.set()
        assert release.wait(10.0)

    provider = _Provider(
        [
            hold,
            _result(final_text="one"),
            _result(final_text="two"),
            _result(final_text="three"),
            _result(final_text="four"),
        ]
    )
    client = connect(provider)

    client.send(id="p1", type="prompt", message="first")
    assert client.response("p1")["data"] == {"disposition": "started"}
    assert entered.wait(10.0)
    client.send(id="p2", type="prompt", message="second")
    assert client.response("p2")["data"] == {"disposition": "queued"}
    client.send(id="s", type="steer", message="third")
    assert client.response("s")["data"] == {"disposition": "queued"}
    client.send(id="f", type="follow_up", message="fourth")
    assert client.response("f")["data"] == {"disposition": "queued"}
    release.set()

    rest = client.close()
    assert [record for record in rest if _is_response(record)] == []
    delivered = {
        record["message"]["content"][0]["text"]
        for record in rest
        if record["type"] == "message_end" and record["message"]["role"] == "user"
    }
    assert {"second", "third", "fourth"} <= delivered


def test_a_handled_prompt_settles_without_the_server_lock(
    tmp_path: Path, connect
) -> None:
    # The handled response is published under the control gate. The drain at
    # the end of input holds the server's lock while it waits for that gate,
    # so the publication must not need the lock.
    workspace = tmp_path / "work"
    _extension(workspace, "commands", _COMMANDS)
    client = connect(_Provider([]))
    client.send(id="s", type="get_state")
    client.response("s")

    with client._server._lock:
        client.send(id="c", type="prompt", message="/note x")
        response = client.response("c")

    assert response["data"] == {"disposition": "handled"}


def test_a_queued_prompt_is_never_answered_as_started(connect, monkeypatch) -> None:
    # The run in flight ends right after the admission, before the reader
    # has answered: the promoted prompt's `agent_start` must not take the
    # answer, which was decided as `queued` when the prompt was admitted.
    entered, release = threading.Event(), threading.Event()

    def hold() -> None:
        entered.set()
        assert release.wait(10.0)

    provider = _Provider([hold, _result(final_text="one"), _result(final_text="two")])
    client = connect(provider)
    client.send(id="p1", type="prompt", message="first")
    assert client.response("p1")["data"] == {"disposition": "started"}
    assert entered.wait(10.0)
    bridge_type = type(client._server._bridge)
    admit = bridge_type.admit_prompt

    def admit_then_let_the_run_end(self: Any, *args: Any, **kwargs: Any) -> Any:
        snapshot = admit(self, *args, **kwargs)
        release.set()
        # Time for the first run to end and the promoted one to start.
        threading.Event().wait(0.3)
        return snapshot

    monkeypatch.setattr(bridge_type, "admit_prompt", admit_then_let_the_run_end)
    client.send(id="p2", type="prompt", message="second")
    records = client.until(lambda record: record.get("id") == "p2")
    monkeypatch.undo()

    assert records[-1]["data"] == {"disposition": "queued"}
    responses = [record for record in client.close() if _is_response(record)]
    assert responses == []
    assert [request.user_prompt for request in provider.requests][-1] == "second"


def test_a_prompt_after_the_session_ended_is_refused(connect) -> None:
    # Nothing would answer a prompt once the worker has ended, so the reader
    # refuses it. Here the provider ends the worker with an exception.
    client = connect(_Provider([]))
    client.send(id="p1", type="prompt", message="first")
    assert client.response("p1")["data"] == {"disposition": "started"}
    worker = client._server._worker
    assert worker is not None
    worker.join(timeout=10.0)
    assert not worker.is_alive()

    client.send(id="p2", type="prompt", message="second")
    response = client.response("p2")

    assert (response["success"], response["error"]) == (
        False,
        "the session has ended: IndexError",
    )


def test_an_ended_worker_answers_the_prompt_it_still_owes(connect) -> None:
    # The session can also end without an exception, after a run that fails
    # it. A prompt that was next to run is answered then, with the reason.
    entered, release = threading.Event(), threading.Event()

    def hold() -> None:
        entered.set()
        assert release.wait(10.0)

    client = connect(_Provider([hold, _result(final_text="one")]))
    client.send(id="p1", type="prompt", message="first")
    assert client.response("p1")["data"] == {"disposition": "started"}
    assert entered.wait(10.0)
    server = client._server
    owed = ProductContent("owed")
    with server._prompt_lock:
        server._owed_prompts[id(owed)] = (owed, "p2")

    server._worker_has_ended("the session has ended")

    assert client.response("p2") == {
        "id": "p2",
        "type": "response",
        "command": "prompt",
        "success": False,
        "error": "the session has ended",
    }
    client.send(id="p3", type="prompt", message="third")
    assert client.response("p3")["error"] == "the session has ended"
    release.set()
