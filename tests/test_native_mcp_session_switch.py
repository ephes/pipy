"""MCP servers when another session replaces this one in the process:
``/new``, ``/resume``, ``/fork``, ``/clone`` and ``/import``.

Pi creates a whole runtime for each of them: its MCP extension closes every
connection on ``session_shutdown`` and reads ``mcp.json`` again on
``session_start`` (``extensions/mcp/index.ts``). The session tests run a
persistent session with a scripted provider and the fake stdio server as a
real child process, and edit ``mcp.json`` before the switch. The
``McpSession`` tests use the in-memory loads of the reload tests.
"""

from __future__ import annotations

import io
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_native_extension_mcp_servers import _A, _Extensions
from test_native_extension_tool_metadata import _result, _tool_names
from test_native_mcp_reload import (
    _LATE,
    _WAIT_SECONDS,
    _load,
    _Loads,
    _mcp_names,
    _settled,
    _started,
    _Steps,
)
from test_native_mcp_session import (
    _events,
    _mcp_threads,
    _Provider,
    _received,
    _recorded_managers,
    _records,
    _server,
    _wait_until,
    _write_mcp_json,
)

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import AgentUserMessage, ProductContent
from pipy_harness.native.coding.session import (
    CodingSession,
    production_tool_registry,
)
from pipy_harness.native.mcp.config import LoadedMcpConfig, McpServerEntry
from pipy_harness.native.mcp.options import McpOptions
from pipy_harness.native.mcp.registry import McpServerRegistry, RegisteredMcpServer
from pipy_harness.native.repl.mcp_session import McpSession
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tool_capabilities import ToolFilterOptions
from pipy_harness.sdk import create_product_session

_REPORT = "MCP servers need attention:\n  gone: failed: "


def _run(
    workspace: Path, provider: _Provider, steps: _Steps, tree: NativeSessionTree
) -> tuple[HarnessStatus, str]:
    errors = io.StringIO()
    session = CodingSession(
        provider=provider,
        tool_registry=production_tool_registry(),
        tool_budget=8,
        native_session=tree,
        resource_options=RuntimeResourceOptions.empty(),
        tool_filter_options=ToolFilterOptions.empty(),
    )
    result = session.run(
        workspace_root=workspace,
        input_stream=steps,
        output_stream=io.StringIO(),
        error_stream=errors,
    )
    return result.status, errors.getvalue()


def _initializes(record: Path) -> int:
    return sum(
        line.get("received", {}).get("method") == "initialize"
        for line in _records(record)
    )


def _switch(command: str, workspace: Path, sessions: Path) -> str:
    """The input line that replaces the session; ``/resume`` and ``/import``
    get a session of their own to switch to."""

    if command not in ("/resume", "/import"):
        return command
    directory = sessions if command == "/resume" else workspace.parent / "elsewhere"
    other = NativeSessionTree.create(workspace, session_dir=directory)
    other.append_message(AgentUserMessage(content=ProductContent("other")))
    assert other.path is not None
    if command == "/resume":
        return f"/resume {other.session_id}"
    return f"/import {other.path} --yes"


@pytest.mark.parametrize("command", ["/new", "/resume", "/fork", "/clone", "/import"])
def test_another_session_reads_mcp_json_again(
    tmp_path: Path, monkeypatch, command: str
) -> None:
    # K6/E10: the servers of the old configuration are closed, the new
    # configuration's servers connect, the startup report is made again and
    # the first prompt of the new session waits for servers with `direct`
    # tools, as at the start of a session.
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = (tmp_path / "work").resolve()
    workspace.mkdir()
    sessions = tmp_path / "sessions"
    tree = NativeSessionTree.create(workspace, session_dir=sessions)
    old, old_record = _server(tmp_path / "first", "old")
    added, added_record = _server(tmp_path / "second", "new", methods=_LATE)
    gone = {"command": str(tmp_path / "no-such-server"), "exposure": "direct"}
    _write_mcp_json(
        config / "mcp.json", {"old": {**old, "exposure": "direct"}, "gone": gone}
    )

    def edit() -> None:
        _write_mcp_json(
            config / "mcp.json", {"new": {**added, "exposure": "direct"}, "gone": gone}
        )

    managers = _recorded_managers(monkeypatch)
    before = set(threading.enumerate())
    provider = _Provider([_result(final_text="one"), _result(final_text="two")])

    status, errors = _run(
        workspace,
        provider,
        _Steps(
            "go",
            _settled(managers, 0),
            edit,
            _switch(command, workspace, sessions),
            "again",
        ),
        tree,
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    assert _mcp_names(provider.requests[0]) == ["mcp__old__search", "mcp__old__wipe"]
    # The new server answers late; the prompt waited for it.
    assert _mcp_names(provider.requests[1]) == ["mcp__new__search", "mcp__new__wipe"]
    assert len(managers) == 2
    assert "stdin-closed" in _events(old_record)
    assert _initializes(old_record) == 1
    assert _initializes(added_record) == 1
    assert errors.count(_REPORT) == 2, errors
    _wait_until(lambda: _mcp_threads(before) == [])


def test_a_tool_tool_search_loaded_is_not_declared_in_the_new_session(
    tmp_path: Path, monkeypatch
) -> None:
    # Unlike /reload (TSR-10), a new session starts from the configured
    # tools: the loaded tool has to be loaded again; the `direct` one is
    # declared once its server connected again.
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = (tmp_path / "work").resolve()
    workspace.mkdir()
    tree = NativeSessionTree.create(workspace, session_dir=tmp_path / "sessions")
    docs, record = _server(tmp_path / "servers", methods=_LATE)
    _write_mcp_json(
        config / "mcp.json",
        {"docs": {**docs, "exposure": "deferred", "toolExposure": {"wipe": "direct"}}},
    )
    provider = _Provider(
        [_load(), _result(final_text="loaded"), _result(final_text="new")]
    )

    status, errors = _run(workspace, provider, _Steps("load", "/new", "next"), tree)

    assert status is HarnessStatus.SUCCEEDED, errors
    assert "mcp__docs__search" in _tool_names(provider.requests[1])
    assert _mcp_names(provider.requests[2]) == ["mcp__docs__wipe"]
    assert "tool_search" in _tool_names(provider.requests[2])
    assert _initializes(record) == 2
    assert _received(record, "tools/list")


def test_a_product_session_transition_loads_the_servers_again(
    tmp_path: Path, monkeypatch
) -> None:
    # The SDK's new_session() replaces the session like /new: the embedder's
    # load_config is asked again and the old connection is closed.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    workspace = (tmp_path / "work").resolve()
    workspace.mkdir()
    tree = NativeSessionTree.create(workspace, session_dir=tmp_path / "sessions")
    old, old_record = _server(tmp_path / "first", "old")
    added, _ = _server(tmp_path / "second", "new", methods=_LATE)
    configured = [{"old": {**old, "exposure": "direct"}}]
    loads: list[str] = []

    def load_config(**context: Any) -> LoadedMcpConfig:
        loads.append(str(context["cwd"]))
        return LoadedMcpConfig(
            servers=[
                McpServerEntry(name=name, config=config, source="sdk")
                for name, config in configured[-1].items()
            ],
            errors=[],
        )

    provider = _Provider([_result(final_text="one"), _result(final_text="two")])
    with create_product_session(
        workspace=workspace,
        provider=provider,
        tree=tree,
        load_context_files=False,
        mcp=McpOptions(load_config=load_config),
    ) as session:
        session.submit("one")
        configured.append({"new": {**added, "exposure": "direct"}})
        assert session.new_session().status == "completed"
        assert "stdin-closed" in _events(old_record)
        session.submit("two")

    assert loads == [str(workspace)] * 2
    assert _mcp_names(provider.requests[0]) == ["mcp__old__search", "mcp__old__wipe"]
    assert _mcp_names(provider.requests[1]) == ["mcp__new__search", "mcp__new__wipe"]


# -- McpSession ----------------------------------------------------------------


@pytest.fixture
def loads(tmp_path: Path) -> Iterator[_Loads]:
    value = _Loads(tmp_path)
    yield value
    for loaded in value.loaded:
        loaded.manager.close()


def test_replacing_the_session_replaces_the_servers(loads: _Loads) -> None:
    session = _started(loads)
    first = session.servers
    first.manager.post_notice("warning", "said before the switch")
    loads.configured = {"new": ["find"]}

    session.replace_session()

    second = session.servers
    assert second is loads.loaded[1]
    assert first.manager.servers() == ()
    assert loads.notices == [("warning", "said before the switch")]
    assert second.manager.wait_until_settled(_WAIT_SECONDS)
    assert loads.opened == [(0, "docs"), (1, "new")]
    assert loads.declared() == ("read", "mcp__new__find")
    # The first prompt of the new session waits like a first prompt.
    assert second.readiness is not first.readiness
    session.close()
    assert second.manager.servers() == ()


def test_a_session_only_change_is_gone_in_the_new_session(loads: _Loads) -> None:
    # A change made in /mcp to a server an extension registered applies to
    # the session only (Pi `saveConfig` for scope "extension").
    registry = McpServerRegistry()
    session = McpSession(loads, tools=loads.tools, notify=loads.notify)
    session.follow_extensions(lambda: _Extensions(registry))
    off = {"command": sys.executable, "enabled": False, "exposure": "direct"}
    registry.register(RegisteredMcpServer("ext", off, _A))

    def exposure() -> object:
        statuses = session.servers.manager.servers()
        return next(
            status.entry.config.get("exposure")
            for status in statuses
            if status.entry.name == "ext"
        )

    try:
        session.start()
        assert session.servers.manager.set_exposure("ext", "deferred") is None
        assert exposure() == "deferred"

        session.replace_session()

        assert exposure() == "direct"
    finally:
        session.close()


def test_a_registered_server_nothing_connects_is_reported_again(
    loads: _Loads,
) -> None:
    # Pi reports such a server once per runtime, and a new session is a new
    # runtime.
    registry = McpServerRegistry()
    session = McpSession(
        lambda registered, replaced_by: loads(registered, "replacer"),
        tools=loads.tools,
        notify=loads.notify,
    )
    session.follow_extensions(lambda: _Extensions(registry))
    registry.register(RegisteredMcpServer("ext", {"command": "x"}, _A))

    def unhandled() -> list[str]:
        return [message for _, message in loads.notices if "no loaded" in message]

    try:
        session.start()
        assert len(unhandled()) == 1
        session.replace_session()
        assert len(unhandled()) == 2
    finally:
        session.close()
