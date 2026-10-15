"""What the extensions see when another session replaces this one in the
process: ``/new``, ``/resume``, ``/fork``, ``/clone`` and ``/import``.

Pi creates a whole runtime for each of them (``core/agent-session-runtime.ts``):
the old extensions get ``session_shutdown`` with the reason and the file of
the session that follows, the new ones ``session_start`` with the reason and
the file of the session before, and the tool selection is the configured one
again. pipy keeps its extensions loaded and tells them the same.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from test_native_extension_tool_metadata import (
    _result,
    _StubProvider,
    _tool_names,
    _write,
)
from test_native_mcp_reload import _Steps
from test_native_mcp_session_switch import _switch

from pipy_harness.models import HarnessStatus
from pipy_harness.native.coding.session import (
    CodingSession,
    production_tool_registry,
)
from pipy_harness.native.session_tree import NativeSessionTree

_RECORDER = (
    "PROOF = Path(__file__).with_name('events.jsonl')\n"
    "def activate(api):\n"
    "    def record(event, ctx):\n"
    "        with PROOF.open('a') as fh:\n"
    "            fh.write(json.dumps([event.name, event.reason,\n"
    "                event.previous_session_file, event.target_session_file]) + '\\n')\n"
    "    api.on('session_start', record)\n"
    "    api.on('session_shutdown', record)\n"
)


def _run(
    workspace: Path, provider: _StubProvider, steps: _Steps, tree: NativeSessionTree
) -> HarnessStatus:
    session = CodingSession(
        provider=provider,
        tool_registry=production_tool_registry(),
        tool_budget=5,
        native_session=tree,
    )
    result = session.run(
        workspace_root=workspace,
        input_stream=steps,
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    return result.status


def _events(workspace: Path) -> list[list[str | None]]:
    proof = workspace / ".pipy" / "extensions" / "events.jsonl"
    return [json.loads(line) for line in proof.read_text().splitlines()]


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        ("/new", "new"),
        ("/resume", "resume"),
        ("/fork", "fork"),
        ("/clone", "fork"),
        ("/import", "resume"),
    ],
)
def test_a_replacement_shuts_the_session_down_and_starts_the_next(
    tmp_path: Path, monkeypatch, command: str, reason: str
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    workspace = (tmp_path / "work").resolve()
    workspace.mkdir()
    sessions = tmp_path / "sessions"
    tree = NativeSessionTree.create(workspace, session_dir=sessions)
    first = str(tree.path)
    _write(workspace, "recorder", _RECORDER)
    provider = _StubProvider([_result(final_text="one"), _result(final_text="two")])

    status = _run(
        workspace,
        provider,
        _Steps("go", _switch(command, workspace, sessions), "again"),
        tree,
    )

    assert status is HarnessStatus.SUCCEEDED
    started, shutdown, restarted, quit_ = _events(workspace)
    assert started == ["session_start", "startup", None, None]
    assert shutdown[:3] == ["session_shutdown", reason, None]
    assert restarted == ["session_start", reason, first, None]
    target = shutdown[3]
    assert target is not None and target != first
    assert Path(target).is_file()
    assert quit_ == ["session_shutdown", "quit", None, None]


def test_a_refused_replacement_sends_neither_event(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    workspace = (tmp_path / "work").resolve()
    workspace.mkdir()
    tree = NativeSessionTree.create(workspace, session_dir=tmp_path / "sessions")
    _write(
        workspace,
        "recorder",
        _RECORDER + "    from pipy_harness.extensions import SessionDecision\n"
        "    api.on('session_before_switch',\n"
        "        lambda event, ctx: SessionDecision(allow=False, reason='stay'))\n",
    )
    provider = _StubProvider([_result(final_text="one")])

    status = _run(workspace, provider, _Steps("go", "/new"), tree)

    assert status is HarnessStatus.SUCCEEDED
    assert [event[:2] for event in _events(workspace)] == [
        ["session_start", "startup"],
        ["session_shutdown", "quit"],
    ]


def test_a_new_session_starts_from_the_configured_tools(
    tmp_path: Path, monkeypatch
) -> None:
    # An extension narrowed the tools of the old session; the new session
    # declares the configured tools again.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    workspace = (tmp_path / "work").resolve()
    workspace.mkdir()
    tree = NativeSessionTree.create(workspace, session_dir=tmp_path / "sessions")
    _write(
        workspace,
        "narrow",
        "def activate(api):\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='lookup', description='look up',\n"
        "        input_schema={'type': 'object'},\n"
        "        handler=lambda ctx, params: ToolResult(content='ok')))\n"
        "    api.register_command(\n"
        "        'narrow', 'only read',\n"
        "        lambda ctx, args: ctx.set_active_tools(['read']))\n",
    )
    provider = _StubProvider([_result(final_text="one"), _result(final_text="two")])

    status = _run(workspace, provider, _Steps("/narrow", "one", "/new", "two"), tree)

    assert status is HarnessStatus.SUCCEEDED
    narrowed, configured = provider.requests
    assert _tool_names(narrowed) == ["read"]
    assert {"read", "bash", "edit", "write", "lookup"} <= set(_tool_names(configured))


def test_what_session_shutdown_sends_stays_with_the_old_session(
    tmp_path: Path, monkeypatch
) -> None:
    # Pi's `session_shutdown` handlers run while the outgoing session is
    # still the session: a custom message goes to its file, and a user
    # message starts no run in the new session. What the message's renderer
    # sends while the message is delivered does not reach it either.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    workspace = (tmp_path / "work").resolve()
    workspace.mkdir()
    tree = NativeSessionTree.create(workspace, session_dir=tmp_path / "sessions")
    first = tree.path
    assert first is not None
    _write(
        workspace,
        "farewell",
        "sent = []\n"
        "def activate(api):\n"
        "    def bye(event, ctx):\n"
        "        if event.reason == 'new':\n"
        "            api.send_message({'customType': 'farewell', 'content': 'bye',\n"
        "                              'display': True})\n"
        "            api.send_user_message('leftover prompt')\n"
        "    def render(data):\n"
        "        if not sent:\n"
        "            sent.append(True)\n"
        "            api.send_message({'customType': 'echo', 'content': 'again'})\n"
        "            api.send_user_message('renderer prompt')\n"
        "        return 'bye'\n"
        "    api.register_message_renderer('farewell', render)\n"
        "    api.on('session_shutdown', bye)\n",
    )
    provider = _StubProvider([_result(final_text="one"), _result(final_text="two")])

    status = _run(workspace, provider, _Steps("go", "/new", "next"), tree)

    assert status is HarnessStatus.SUCCEEDED
    assert len(provider.requests) == 2
    assert "leftover prompt" not in repr(provider.requests[1].messages)
    assert "renderer prompt" not in repr(provider.requests[1].messages)
    assert "farewell" in first.read_text()
    (second,) = [path for path in first.parent.glob("*.jsonl") if path != first]
    assert "farewell" not in second.read_text()
    assert "echo" not in second.read_text()
