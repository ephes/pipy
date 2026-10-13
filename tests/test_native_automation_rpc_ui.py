"""The UI requests of RPC mode, and ``/mcp`` through them (MCP parity plan
X1, rows SLH-11 and SLH-12).

Pi sends ``ctx.ui.notify``, ``select`` and ``input`` to an RPC client as
``extension_ui_request`` lines and reads the answers as
``extension_ui_response`` lines. ``/mcp`` uses the three: its status and
messages are notices, a server is chosen with ``select``, and a sign-in asks
for the pasted redirect URL with ``input``.
"""

from __future__ import annotations

import io
import json
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from test_native_automation_prompt_dispatch import _mcp_json
from test_native_automation_rpc_prompt_dispatch import (
    _Client,
    _is_request,
    _is_response,
    _without_id,
)
from test_native_mcp_session import _Provider, _server

import pipy_harness.native.repl.wiring as wiring
from pipy_harness.native.automation.jsonl import JsonlWriter
from pipy_harness.native.automation.rpc_ui import RpcUi
from pipy_harness.native.mcp.commands import (
    REDIRECT_URL_PLACEHOLDER,
    redirect_url_title,
)
from pipy_harness.native.mcp.credentials import (
    McpOAuthCredentialStore,
    mcp_auth_path,
)

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_oauth import FakeBrowser, FakeOAuthMcpServer  # noqa: E402


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


def _ui() -> tuple[RpcUi, io.BytesIO]:
    stream = io.BytesIO()
    return RpcUi(JsonlWriter(stream)), stream


def _written(stream: io.BytesIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().decode().splitlines()]


def _ask_on_a_thread(ask: Any) -> tuple[threading.Thread, list[Any]]:
    answers: list[Any] = []
    thread = threading.Thread(target=lambda: answers.append(ask()), daemon=True)
    thread.start()
    return thread, answers


def _wait_for_request(stream: io.BytesIO) -> dict[str, Any]:
    waited = threading.Event()
    for _ in range(200):
        written = _written(stream)
        if written:
            return written[-1]
        waited.wait(0.01)
    raise AssertionError("no request was written")


def test_notify_writes_one_request_and_waits_for_nothing() -> None:
    ui, stream = _ui()

    ui.notify("careful", "warning")
    ui.notify("plain")

    first, second = _written(stream)
    assert _without_id(first) == {
        "type": "extension_ui_request",
        "method": "notify",
        "message": "careful",
        "notifyType": "warning",
    }
    assert second["notifyType"] == "info"
    assert first["id"] != second["id"]


def test_select_returns_the_value_of_its_response() -> None:
    ui, stream = _ui()
    thread, answers = _ask_on_a_thread(lambda: ui.select("MCP server", ["a", "b"]))
    request = _wait_for_request(stream)
    assert _without_id(request) == {
        "type": "extension_ui_request",
        "method": "select",
        "title": "MCP server",
        "options": ["a", "b"],
    }

    # A response for another request is ignored.
    ui.respond({"type": "extension_ui_response", "id": "other", "value": "a"})
    ui.respond({"type": "extension_ui_response", "id": 7, "value": "a"})
    assert thread.is_alive()
    ui.respond({"type": "extension_ui_response", "id": request["id"], "value": "b"})
    thread.join(5.0)

    assert answers == ["b"]
    # A second response for the same request has nobody waiting.
    ui.respond({"type": "extension_ui_response", "id": request["id"], "value": "a"})


def test_a_cancelled_or_malformed_response_is_no_answer() -> None:
    for response in ({"cancelled": True}, {"value": 3}, {"confirmed": True}, {}):
        ui, stream = _ui()
        thread, answers = _ask_on_a_thread(lambda ui=ui: ui.input("Title", "hint"))
        request = _wait_for_request(stream)
        assert _without_id(request) == {
            "type": "extension_ui_request",
            "method": "input",
            "title": "Title",
            "placeholder": "hint",
        }

        ui.respond({"type": "extension_ui_response", "id": request["id"], **response})
        thread.join(5.0)

        assert answers == [None], response


def test_a_cancel_event_ends_the_wait() -> None:
    ui, stream = _ui()
    cancel = threading.Event()
    thread, answers = _ask_on_a_thread(lambda: ui.input("Title", None, cancel))
    request = _wait_for_request(stream)
    assert "placeholder" not in request

    cancel.set()
    thread.join(5.0)

    assert answers == [None]
    # Asked with the event already set, nothing is sent (Pi: aborted signal).
    assert ui.select("MCP server", ["a"], cancel) is None
    assert len(_written(stream)) == 1


def test_close_ends_waiting_and_later_requests() -> None:
    ui, stream = _ui()
    thread, answers = _ask_on_a_thread(lambda: ui.select("MCP server", ["a"]))
    _wait_for_request(stream)

    ui.close()
    thread.join(5.0)

    assert answers == [None]
    assert ui.input("Title") is None
    assert len(_written(stream)) == 1


def test_mcp_asks_the_client_which_server_to_reconnect(tmp_path: Path, connect) -> None:
    # SLH-05 over RPC: two eligible servers, none preferred.
    workspace = tmp_path / "work"
    entry, _record = _server(tmp_path / "servers")
    _mcp_json(workspace, {"docs": entry, "more": entry})
    client: _Client = connect(_Provider([]))

    client.send(id="p", type="prompt", message="/mcp reconnect")
    request = client.until(_is_request("select"))[-1]
    assert _without_id(request) == {
        "type": "extension_ui_request",
        "method": "select",
        "title": "MCP server",
        "options": ["docs", "more"],
    }
    client.send(type="extension_ui_response", id=request["id"], value="more")
    records = client.until(_is_response)

    assert [_without_id(record) for record in records] == [
        {
            "type": "extension_ui_request",
            "method": "notify",
            "message": 'Reconnected to MCP server "more" (connected · 2 tools).',
            "notifyType": "info",
        },
        {
            "type": "response",
            "command": "prompt",
            "success": True,
            "data": {"disposition": "handled"},
        },
    ]

    # A cancelled choice does nothing.
    client.send(id="q", type="prompt", message="/mcp reconnect")
    request = client.until(_is_request("select"))[-1]
    client.send(type="extension_ui_response", id=request["id"], cancelled=True)
    assert [record["type"] for record in client.until(_is_response)] == ["response"]


def _oauth_session(
    tmp_path: Path, connect: Any, server: FakeOAuthMcpServer
) -> tuple[_Client, McpOAuthCredentialStore]:
    workspace = tmp_path / "work"
    _mcp_json(workspace, {"issues": {"url": server.url, "exposure": "direct"}})
    client: _Client = connect(_Provider([]))
    return client, McpOAuthCredentialStore(mcp_auth_path(workspace))


_STARTUP_REPORT = (
    "MCP servers need attention:\n  issues: needs sign-in\nRun /mcp to fix."
)


def _authorization_url(records: list[dict[str, Any]]) -> str:
    notice = next(
        record["message"]
        for record in records
        if record.get("method") == "notify"
        and record["message"].startswith("Sign in to MCP server")
    )
    first, url = notice.split("\n")
    assert first == 'Sign in to MCP server "issues" in your browser:'
    return url


def test_mcp_login_takes_the_pasted_redirect_url(
    tmp_path: Path, connect, monkeypatch
) -> None:
    # SLH-12, SLH-04: the browser cannot reach this machine, so the client
    # answers the `input` request with the URL the browser was sent to.
    opened: list[str] = []
    monkeypatch.setattr(wiring, "open_browser", opened.append)
    with FakeOAuthMcpServer() as server:
        client, store = _oauth_session(tmp_path, connect, server)

        client.send(id="p", type="prompt", message="/mcp login issues")
        records = client.until(_is_request("input"))
        url = _authorization_url(records)
        assert opened == [url]
        asked = records[-1]
        assert _without_id(asked) == {
            "type": "extension_ui_request",
            "method": "input",
            "title": redirect_url_title("issues"),
            "placeholder": REDIRECT_URL_PLACEHOLDER,
        }
        assert asked["title"] == (
            'Waiting for sign-in to "issues". If the browser cannot reach this '
            "machine, paste the URL it was redirected to."
        )
        redirected_to = FakeBrowser().get(url).headers["location"]
        client.send(type="extension_ui_response", id=asked["id"], value=redirected_to)
        rest = client.until(_is_response)

        assert [_without_id(record) for record in rest] == [
            {
                "type": "extension_ui_request",
                "method": "notify",
                "message": 'Signed in to MCP server "issues" (1 tools).',
                "notifyType": "info",
            },
            {
                "type": "response",
                "command": "prompt",
                "success": True,
                "data": {"disposition": "handled"},
            },
        ]
        assert store.tokens("issues", server.url) is not None
        # The startup report is a notice too. The session shows its notices
        # before its next step, which here is the end of the input.
        assert [record["message"] for record in client.close()] == [_STARTUP_REPORT]


def test_mcp_login_through_the_browser_ends_the_input_request(
    tmp_path: Path, connect, monkeypatch
) -> None:
    # SLH-12: the browser arrives first; the question is no longer wanted
    # and a late answer to it is ignored.
    monkeypatch.setattr(wiring, "open_browser", lambda url: FakeBrowser().open(url))
    with FakeOAuthMcpServer() as server:
        client, store = _oauth_session(tmp_path, connect, server)

        client.send(id="p", type="prompt", message="/mcp login")
        records = client.until(_is_response)

        asked = [record for record in records if record.get("method") == "input"]
        assert len(asked) <= 1
        assert records[-2]["message"] == 'Signed in to MCP server "issues" (1 tools).'
        assert records[-1]["data"] == {"disposition": "handled"}
        assert store.tokens("issues", server.url) is not None
        for request in asked:
            client.send(type="extension_ui_response", id=request["id"], value="late")
        client.send(id="s", type="get_state")
        assert client.response("s")["success"] is True


def test_mcp_login_is_cancelled_by_the_client_or_the_end_of_input(
    tmp_path: Path, connect, monkeypatch
) -> None:
    monkeypatch.setattr(wiring, "open_browser", lambda url: None)
    with FakeOAuthMcpServer() as server:
        client, store = _oauth_session(tmp_path, connect, server)

        client.send(id="p", type="prompt", message="/mcp login issues")
        asked = client.until(_is_request("input"))[-1]
        client.send(type="extension_ui_response", id=asked["id"], cancelled=True)
        records = client.until(_is_response)
        assert [record.get("message") for record in records[:-1]] == [
            "Sign-in cancelled."
        ]
        assert records[-1]["data"] == {"disposition": "handled"}

        # The client goes away while a second sign-in waits for it.
        client.send(id="q", type="prompt", message="/mcp login issues")
        client.until(_is_request("input"))
        rest = client.close()

        assert [record.get("message") for record in rest[:-1]] == ["Sign-in cancelled."]
        assert rest[-1]["data"] == {"disposition": "handled"}
        assert store.tokens("issues", server.url) is None


def test_abort_cancels_a_login_that_waits(tmp_path: Path, connect, monkeypatch) -> None:
    # The waits of `/mcp` end with the session's abort, as a running tool
    # does; the `input` request ends with them.
    monkeypatch.setattr(wiring, "open_browser", lambda url: None)
    with FakeOAuthMcpServer() as server:
        client, store = _oauth_session(tmp_path, connect, server)

        client.send(id="p", type="prompt", message="/mcp login issues")
        client.until(_is_request("input"))
        client.send(id="a", type="abort")
        records = client.until(lambda record: record.get("id") == "p")

        assert "Sign-in cancelled." in [record.get("message") for record in records]
        assert records[-1]["data"] == {"disposition": "handled"}
        assert store.tokens("issues", server.url) is None
        # The session is idle again and takes the next prompt.
        client.send(id="q", type="prompt", message="/mcp")
        assert client.response("q")["data"] == {"disposition": "handled"}
