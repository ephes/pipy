"""MCP servers across ``/reload`` (MCP parity plan S22, rows SES-20 and
TSR-10).

A coding session with a scripted provider and the fake stdio server as a real
child process. Between two input lines a step may change files and wait, so
a test edits ``mcp.json`` or the settings and then types ``/reload``.

The capability value's part of TSR-10 (pending tool names) is in
``test_native_tool_capabilities_mcp.py``.
"""

from __future__ import annotations

import io
import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_native_extension_tool_metadata import (
    _result,
    _tool_names,
    _tool_results,
)
from test_native_mcp_connection import _Server
from test_native_mcp_manager import _entry, _tool
from test_native_mcp_session import (
    _WAIT_CALL,
    _WAITER,
    _WIPE,
    _events,
    _extension,
    _mcp_threads,
    _Provider,
    _received,
    _recorded_managers,
    _records,
    _server,
    _tool_calls,
    _wait_until,
    _write_mcp_json,
)
from test_native_mcp_session_section import _section
from test_native_tool_filter_patterns import _Tool

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import AgentToolCall, ProductContent
from pipy_harness.native.coding.session import (
    CodingSession,
    production_tool_registry,
)
from pipy_harness.native.mcp.commands import MCP_COMMAND_DESCRIPTION
from pipy_harness.native.mcp.config import LoadedMcpConfig, McpServerEntry
from pipy_harness.native.mcp.discovery import McpDiscovery
from pipy_harness.native.mcp.manager import McpManager
from pipy_harness.native.mcp.manager_flow import McpBackgroundActions
from pipy_harness.native.mcp.readiness import STILL_CONNECTING_NOTICE, McpReadiness
from pipy_harness.native.mcp.transport import McpTransport
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.repl.mcp_session import McpServers, McpSession
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    ToolFilterOptions,
)

_WAIT_SECONDS = 20.0
_LATE = {"initialize": [{"sleep": 0.3}, {"default": True}]}
_KEPT = "pipy: keeping the previous extensions."


class _Steps(io.StringIO):
    """Input lines; a callable among them runs before the next line is read."""

    def __init__(self, *steps: str | Callable[[], object]) -> None:
        super().__init__("")
        self._steps = list(steps)

    def readline(self, size: int | None = -1, /) -> str:  # type: ignore[override]
        while self._steps:
            step = self._steps.pop(0)
            if isinstance(step, str):
                return step + "\n"
            step()
        return ""


def _run(
    workspace: Path,
    provider: _Provider,
    steps: _Steps,
    *,
    settings: SettingsManager | None = None,
    resource_options: RuntimeResourceOptions | None = None,
) -> tuple[HarnessStatus, str]:
    errors = io.StringIO()
    session = CodingSession(
        provider=provider,
        tool_registry=production_tool_registry(),
        tool_budget=8,
        settings_manager=settings,
        resource_options=resource_options or RuntimeResourceOptions.empty(),
        tool_filter_options=ToolFilterOptions.empty(),
    )
    result = session.run(
        workspace_root=workspace,
        input_stream=steps,
        output_stream=io.StringIO(),
        error_stream=errors,
    )
    return result.status, errors.getvalue()


def _mcp_names(request: Any) -> list[str]:
    return [name for name in _tool_names(request) if name.startswith("mcp__")]


def _settled(managers: list[Any], index: int) -> Callable[[], object]:
    """A step that waits until the manager of that load connected its servers."""

    def wait() -> None:
        assert len(managers) > index, "the configuration was not loaded again"
        assert managers[index].wait_until_settled(_WAIT_SECONDS)

    return wait


# -- SES-20 -------------------------------------------------------------------


def test_reload_reads_mcp_json_again(tmp_path: Path, monkeypatch) -> None:
    # SES-20: the removed server is closed and its tools are gone, the new
    # one connects, the changed one reconnects with its new entry, and the
    # startup report and the first prompt's wait apply again.
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    workspace.mkdir()
    docs, docs_record = _server(tmp_path / "first", "docs")
    old, old_record = _server(tmp_path / "first", "old")
    notes, notes_record = _server(tmp_path / "first", "notes")
    gone = {"command": str(tmp_path / "no-such-server"), "exposure": "direct"}
    _write_mcp_json(
        config / "mcp.json",
        {
            "docs": {**docs, "exposure": "direct"},
            "old": {**old, "exposure": "direct"},
            "notes": {**notes, "exposure": "deferred", "description": "First notes."},
            "gone": gone,
        },
    )
    # After the reload `docs` offers one tool and answers late, so the next
    # request has its tool only when the prompt waited for it.
    changed, changed_record = _server(
        tmp_path / "second", "docs", tools=[_WIPE], methods=_LATE
    )
    added, added_record = _server(tmp_path / "second", "new")

    def edit() -> None:
        _write_mcp_json(
            config / "mcp.json",
            {
                "docs": {**changed, "exposure": "direct"},
                "new": {**added, "exposure": "direct"},
                "notes": {
                    **notes,
                    "exposure": "deferred",
                    "description": "Second notes.",
                },
                "gone": gone,
            },
        )

    managers = _recorded_managers(monkeypatch)
    before = set(threading.enumerate())
    provider = _Provider(
        [
            _result(final_text="one"),
            _result(
                tool_calls=(
                    ProviderToolCall("c1", "mcp__new__search", '{"query": "q"}'),
                    ProviderToolCall("c2", "mcp__old__search", '{"query": "q"}'),
                )
            ),
            _result(final_text="two"),
            _result(final_text="three"),
        ]
    )

    status, errors = _run(
        workspace,
        provider,
        _Steps(
            "go",
            _settled(managers, 0),
            edit,
            "/reload",
            "again",
            _settled(managers, 1),
            "/mcp",
            "last",
        ),
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    assert _mcp_names(provider.requests[0]) == [
        "mcp__docs__search",
        "mcp__docs__wipe",
        "mcp__old__search",
        "mcp__old__wipe",
    ]
    # The request after the reload: no prompt went out before the servers
    # with `direct` tools had connected again.
    assert _mcp_names(provider.requests[1]) == [
        "mcp__docs__wipe",
        "mcp__new__search",
        "mcp__new__wipe",
    ]
    assert STILL_CONNECTING_NOTICE not in errors
    new_call, old_call = _tool_results(provider.requests[2])[-2:]
    assert (new_call.is_error, new_call.content.value) == (
        False,
        "pipy guide\npipy faq",
    )
    assert old_call.is_error
    assert _tool_calls(added_record) == [("search", {"query": "q"})]
    assert _tool_calls(old_record) == []
    # Every server of the first configuration was closed, also the ones the
    # new configuration still has, and each of those was started again.
    for record in (docs_record, old_record):
        assert "stdin-closed" in _events(record)
    for record in (docs_record, old_record, changed_record, added_record):
        assert sum("initialize" in str(line) for line in _records(record)) >= 1
    assert (
        sum(
            line.get("received", {}).get("method") == "initialize"
            for line in _records(notes_record)
        )
        == 2
    )
    assert len(managers) == 2
    # The prompt lists the servers of the configuration that is in use.
    assert "First notes." in (_section(provider.requests[0]) or "")
    section = _section(provider.requests[1]) or ""
    assert "Second notes." in section and "First notes." not in section
    # The startup report is made for each configuration.
    assert errors.count("MCP servers need attention:\n  gone: failed: ") == 2
    # `/mcp` shows the servers that are in use.
    status_text = errors[errors.rindex("docs: connected") :]
    assert status_text.startswith("docs: connected, 1 tools (direct)\nnew: connected")
    assert "old:" not in status_text
    _wait_until(lambda: _mcp_threads(before) == [])


def test_a_refused_reload_keeps_the_servers(tmp_path: Path, monkeypatch) -> None:
    # Plan 4.10: a reload that is not accepted leaves the running manager
    # as it is and starts nothing of the configuration it read.
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    # The extension declares the flag the run was started with only until
    # the marker exists; without it the reloaded extensions are refused.
    extensions = _extension(
        workspace,
        "flagged",
        "from pathlib import Path\n"
        "from pipy_harness.extensions import ExtensionFlag\n"
        "\n"
        "def activate(api):\n"
        "    if not Path(__file__).with_name('changed').exists():\n"
        "        api.register_flag(ExtensionFlag('needs-value', 'string', 'x'))\n",
    )
    docs, docs_record = _server(tmp_path / "first", "docs")
    added, added_record = _server(tmp_path / "second", "new")
    _write_mcp_json(config / "mcp.json", {"docs": {**docs, "exposure": "direct"}})

    def edit() -> None:
        (extensions / "changed").write_text("")
        _write_mcp_json(config / "mcp.json", {"new": {**added, "exposure": "direct"}})

    managers = _recorded_managers(monkeypatch)
    provider = _Provider(
        [
            _result(final_text="one"),
            _result(
                tool_calls=(
                    ProviderToolCall("c1", "mcp__docs__search", '{"query": "q"}'),
                )
            ),
            _result(final_text="two"),
        ]
    )

    status, errors = _run(
        workspace,
        provider,
        _Steps("go", edit, "/reload", "again"),
        resource_options=replace(
            RuntimeResourceOptions.empty(),
            extension_flag_tokens=("--needs-value=x",),
        ),
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    assert errors.count(_KEPT) == 1, errors
    assert _mcp_names(provider.requests[1]) == [
        "mcp__docs__search",
        "mcp__docs__wipe",
    ]
    called = _tool_results(provider.requests[2])[-1]
    assert (called.is_error, called.content.value) == (False, "pipy guide\npipy faq")
    # One connection served both prompts, and the other server never ran.
    assert (
        sum(
            line.get("received", {}).get("method") == "initialize"
            for line in _records(docs_record)
        )
        == 1
    )
    assert not added_record.exists()
    assert len(managers) == 2
    assert managers[1].servers()[0].connection is None


def test_reload_evaluates_the_mcp_switch_again(tmp_path: Path, monkeypatch) -> None:
    # EXP-12 on reload: `-builtin:mcp` in the settings turns MCP off, and
    # removing it turns MCP on again.
    config = tmp_path / "config"
    config.mkdir()
    workspace = tmp_path / "work"
    workspace.mkdir()
    settings_path = config / "settings.json"
    settings_path.write_text(json.dumps({"extensions": []}))
    docs, record = _server(tmp_path / "servers")
    _write_mcp_json(config / "mcp.json", {"docs": {**docs, "exposure": "direct"}})
    settings = SettingsManager(
        global_path=settings_path,
        project_path=workspace / ".pipy" / "settings.json",
        overrides={"theme": "pi"},
    )
    managers = _recorded_managers(monkeypatch)
    provider = _Provider([_result(final_text=text) for text in ("1", "2", "3")])

    def switch(*extensions: str) -> Callable[[], object]:
        return lambda: settings_path.write_text(
            json.dumps({"extensions": list(extensions)})
        )

    status, errors = _run(
        workspace,
        provider,
        _Steps(
            "go",
            switch("-builtin:mcp"),
            "/reload",
            "/mcp",
            "again",
            switch(),
            "/reload",
            _settled(managers, 2),
            "/mcp",
            "last",
        ),
        settings=settings,
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    assert _mcp_names(provider.requests[0]) == [
        "mcp__docs__search",
        "mcp__docs__wipe",
    ]
    # Off: the server is closed, its tools are gone and there is no `/mcp`.
    assert _mcp_names(provider.requests[1]) == []
    assert "stdin-closed" in _events(record)
    assert managers[1].servers() == ()
    off = errors[errors.index("reloaded settings") :]
    assert "docs: connected" not in off[: off.index("reloaded settings", 1)]
    # On again.
    assert _mcp_names(provider.requests[2]) == [
        "mcp__docs__search",
        "mcp__docs__wipe",
    ]
    assert errors.count("docs: connected, 2 tools (direct)") == 1
    assert (
        sum(
            line.get("received", {}).get("method") == "initialize"
            for line in _records(record)
        )
        == 2
    )


# -- TSR-10 -------------------------------------------------------------------


def _load(query: str = "search the docs") -> Any:
    return _result(
        tool_calls=(
            ProviderToolCall(
                "c1", "tool_search", json.dumps({"query": query, "limit": 1})
            ),
        )
    )


def test_tools_tool_search_loaded_are_declared_again_after_reload(
    tmp_path: Path, monkeypatch
) -> None:
    # TSR-10; Pi: "declares tools tool_search loaded again after /reload".
    # One tool of the server is `direct`, so the prompt after the reload
    # waits until the server has connected again.
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    workspace.mkdir()
    docs, record = _server(tmp_path / "servers", methods=_LATE)
    _write_mcp_json(
        config / "mcp.json",
        {"docs": {**docs, "exposure": "deferred", "toolExposure": {"wipe": "direct"}}},
    )
    provider = _Provider(
        [
            _load(),
            _result(final_text="loaded"),
            _result(
                tool_calls=(
                    ProviderToolCall("c2", "mcp__docs__search", '{"query": "again"}'),
                )
            ),
            _result(final_text="done"),
        ]
    )

    status, errors = _run(workspace, provider, _Steps("load", "/reload", "use it"))

    assert status is HarnessStatus.SUCCEEDED, errors
    assert _mcp_names(provider.requests[0]) == ["mcp__docs__wipe"]
    assert "mcp__docs__search" in _tool_names(provider.requests[1])
    # The first request after the reload declares the loaded tool again.
    assert set(_mcp_names(provider.requests[2])) == {
        "mcp__docs__search",
        "mcp__docs__wipe",
    }
    called = _tool_results(provider.requests[3])[-1]
    assert (called.is_error, called.content.value) == (False, "pipy guide\npipy faq")
    assert _tool_calls(record) == [("search", {"query": "again"})]
    assert sum(_received_initialize(record)) == 2


def _received_initialize(record: Path) -> list[bool]:
    return [
        line.get("received", {}).get("method") == "initialize"
        for line in _records(record)
    ]


def test_a_loaded_tool_that_registers_after_the_prompt_started_stays_inactive(
    tmp_path: Path, monkeypatch
) -> None:
    # TSR-10; Pi: "does not activate restored tools that register after the
    # next prompt starts". No prompt waits for a server without `direct`
    # tools, so the prompt after the reload starts while it still connects.
    config = tmp_path / "config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    workspace = tmp_path / "work"
    _extension(workspace, "waiter", _WAITER)
    docs, record = _server(
        tmp_path / "servers",
        methods={"initialize": [{"sleep": 1.5}, {"default": True}]},
    )
    _write_mcp_json(config / "mcp.json", {"docs": {**docs, "exposure": "deferred"}})
    provider = _Provider(
        [
            _load(),
            _result(final_text="loaded"),
            _result(tool_calls=(_WAIT_CALL,)),
            _result(final_text="done"),
            _result(final_text="later"),
        ]
    )

    status, errors = _run(workspace, provider, _Steps("load", "/reload", "go", "later"))

    assert status is HarnessStatus.SUCCEEDED, errors
    assert "mcp__docs__search" in _tool_names(provider.requests[1])
    assert _mcp_names(provider.requests[2]) == []
    # The server has connected again by now, and the tool stays undeclared.
    waited = _tool_results(provider.requests[3])[-1]
    assert (waited.is_error, waited.content.value) == (False, "ready")
    assert _mcp_names(provider.requests[3]) == []
    assert _mcp_names(provider.requests[4]) == []
    assert _received(record, "tools/list")


# -- McpSession ----------------------------------------------------------------


class _Loads:
    """Loads for an ``McpSession``: each has its own manager over in-memory
    servers, and all publish into one capability value."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.tools = NativeToolCapabilities(
            {"read": _Tool("read")},
            {},
            workspace_root=tmp_path,
            filter_options=ToolFilterOptions.empty(),
            cancel_join_timeout_seconds=0.1,
        )
        self.tools.publish(self.tools.prepare_extensions({}))
        # The servers the next load configures, by name.
        self.configured: dict[str, list[str]] = {"docs": ["search"]}
        self.loaded: list[McpServers] = []
        self.servers: list[dict[str, _Server]] = []
        self.opened: list[tuple[int, str]] = []
        self.notices: list[tuple[str, str]] = []

    def __call__(self) -> McpServers:
        index = len(self.loaded)
        servers = {
            name: _Server(tools=[_tool(tool) for tool in tools])
            for name, tools in self.configured.items()
        }
        self.servers.append(servers)

        def create(entry: McpServerEntry, cwd: str, auth: object) -> McpTransport:
            self.opened.append((index, entry.name))
            return servers[entry.name].client_end

        manager = McpManager(
            config=LoadedMcpConfig(
                servers=[_entry(name, exposure="direct") for name in servers],
                errors=[],
            ),
            cwd=self.tmp_path,
            client_version="1.2.3",
            publish_tools=self.tools.publish_mcp_tools,
            create_transport=create,
            update_config=lambda entry, patch: None,
            deactivate_tools=self.tools.deactivate_indirect_mcp_tools,
        )
        loaded = McpServers(
            manager=manager,
            discovery=McpDiscovery(
                configured_exposures=manager.configured_exposures,
                auto_enable_codemode=manager.auto_enable_codemode,
                tools=self.tools,
                notify=manager.post_notice,
            ),
            readiness=McpReadiness(manager, self.tools.all_tools),
            enabled=True,
            actions=McpBackgroundActions(manager),
        )
        self.loaded.append(loaded)
        return loaded

    def notify(self, level: str, message: str) -> None:
        self.notices.append((level, message))

    def declared(self) -> tuple[str, ...]:
        return tuple(definition.name for definition in self.tools.definitions())


@pytest.fixture
def loads(tmp_path: Path) -> Iterator[_Loads]:
    value = _Loads(tmp_path)
    yield value
    for loaded in value.loaded:
        loaded.manager.close()


def _started(loads: _Loads) -> McpSession:
    session = McpSession(loads, tools=loads.tools, notify=loads.notify)
    session.start()
    assert session.servers.manager.wait_until_settled(_WAIT_SECONDS)
    return session


def test_an_accepted_reload_replaces_the_servers(loads: _Loads) -> None:
    session = _started(loads)
    first = session.servers
    assert first.readiness.first_prompt_wait() is None
    assert loads.declared() == ("read", "mcp__docs__search")
    loads.configured = {"docs": ["search", "wipe"], "new": ["find"]}

    with session.reloading() as accept:
        # Read, but nothing of it runs before the reload is accepted.
        assert session.servers is first and len(loads.loaded) == 2
        assert loads.opened == [(0, "docs")]
        accept()

    second = session.servers
    assert second is loads.loaded[1] and second is not first
    # The old connections are closed when the reload returns, and their
    # tools are unregistered before the new servers offer theirs.
    assert first.manager.servers() == ()
    assert second.manager.wait_until_settled(_WAIT_SECONDS)
    assert sorted(loads.opened) == [(0, "docs"), (1, "docs"), (1, "new")]
    assert loads.declared() == (
        "read",
        "mcp__docs__search",
        "mcp__docs__wipe",
        "mcp__new__find",
    )
    assert dict(session.commands) == {"/mcp": MCP_COMMAND_DESCRIPTION}
    session.close()
    assert second.manager.servers() == ()


def test_a_reload_that_is_not_accepted_changes_nothing(loads: _Loads) -> None:
    session = _started(loads)
    first = session.servers
    state = loads.tools.state

    with session.reloading():
        pass
    with pytest.raises(RuntimeError, match="refused"):
        with session.reloading():
            raise RuntimeError("refused")

    assert session.servers is first
    assert loads.tools.state is state
    assert loads.opened == [(0, "docs")]
    assert len(loads.loaded) == 3
    for unused in loads.loaded[1:]:
        assert [status.connection for status in unused.manager.servers()] == [None]
    assert loads.tools.execute(_call("mcp__docs__search")).result.is_error is False


def test_the_running_servers_publish_nothing_while_a_reload_is_decided(
    loads: _Loads,
) -> None:
    # The reload gate compares the tools it was prepared with to the tools
    # at the swap. A server that changes its tools in between would make the
    # reload fail, so its publication waits.
    session = _started(loads)
    state = loads.tools.state
    changing = threading.Thread(
        target=session.servers.manager.set_exposure, args=("docs", "deferred")
    )

    with session.reloading():
        changing.start()
        changing.join(0.3)
        assert changing.is_alive()
        assert loads.tools.state is state

    changing.join(_WAIT_SECONDS)
    assert not changing.is_alive()
    assert loads.tools.state.mcp_registry["mcp__docs__search"].definition.exposure == (
        "deferred"
    )


def test_a_reload_shows_what_the_closed_servers_still_had_to_say(
    loads: _Loads,
) -> None:
    session = _started(loads)
    session.servers.manager.post_notice("warning", "said before the reload")

    with session.reloading() as accept:
        accept()

    assert loads.notices == [("warning", "said before the reload")]
    session.servers.manager.post_notice("info", "said after it")
    session.deliver_notices()
    assert loads.notices[-1] == ("info", "said after it")


def _call(name: str) -> AgentToolCall:
    return AgentToolCall(
        provider_correlation_id="call-1",
        tool_name=name,
        arguments_json=ProductContent("{}"),
    )
