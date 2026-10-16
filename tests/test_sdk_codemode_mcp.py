"""``codemode``, ``tool_search`` and MCP in an SDK session (MCP parity plan
S38, rows SDK-01 to SDK-03).

A product session with a scripted provider and the fake stdio server as a
real child process. The session has none of the three until the embedder
asks, and what the options leave out is what the ``pipy`` command does.
"""

from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from test_native_extension_tool_metadata import _result, _tool_names, _tool_results
from test_native_mcp_session import (
    _LATE,
    _NEVER,
    _events,
    _mcp_threads,
    _Provider,
    _server,
    _tool_calls,
    _wait_until,
    _write_mcp_json,
)

from pipy_harness.native.codemode import selftest
from pipy_harness.native.codemode.selftest import Availability
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.mcp.commands import McpCommand
from pipy_harness.native.mcp.config import (
    LoadedMcpConfig,
    McpServerEntry,
    load_mcp_config,
)
from pipy_harness.native.mcp.connection import create_default_transport
from pipy_harness.native.mcp.credentials import McpOAuthCredentialStore
from pipy_harness.native.mcp.manager import McpManager, update_config_file
from pipy_harness.native.mcp.options import McpOptions
from pipy_harness.native.mcp.readiness import STILL_CONNECTING_NOTICE
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.repl import mcp_loading, wiring
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.session_builtins import (
    CodemodeOptions,
    SessionBuiltins,
    codemode_loadout,
)
from pipy_harness.native.settings import SettingsManager
from pipy_harness.sdk import create_product_session, open_product_session

_OPTIONAL = {"codemode", "tool_search"}


def _config(tmp_path: Path, monkeypatch, **settings: Any) -> Path:
    """An isolated config home, with a settings file when given."""

    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    if settings:
        (config / "settings.json").write_text(json.dumps(settings))
    return config


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "work"
    workspace.mkdir()
    return workspace


def _submit(
    workspace: Path, provider: _Provider, prompts: tuple[str, ...] = ("go",), **options
) -> str:
    """Run the prompts in one session; returns its diagnostics."""

    diagnostics: list[str] = []
    with create_product_session(
        workspace=workspace,
        provider=provider,
        diagnostic_sink=diagnostics.append,
        load_context_files=False,
        **options,
    ) as session:
        for prompt in prompts:
            result = session.submit(prompt)
            assert result.provider_failure is result.preparation_failure is None
    return "".join(diagnostics)


def _mcp_names(names: list[str]) -> list[str]:
    return [name for name in names if name.startswith("mcp__")]


# -- SDK-01 ----------------------------------------------------------------


def test_a_session_has_none_of_the_three_unless_asked(
    tmp_path: Path, monkeypatch
) -> None:
    # The settings and `mcp.json` would give a `pipy` session all three.
    config = _config(tmp_path, monkeypatch, defaultTools=["+codemode", "+tool_search"])
    monkeypatch.setattr(selftest, "availability", lambda: Availability(True))
    entry, record = _server(tmp_path / "servers")
    _write_mcp_json(config / "mcp.json", {"docs": {**entry, "exposure": "direct"}})

    def refuse(**_kwargs: object) -> None:
        raise AssertionError("mcp.json was read")

    monkeypatch.setattr(mcp_loading, "load_mcp_config", refuse)
    provider = _Provider([_result(final_text="done")])
    before = set(threading.enumerate())

    diagnostics = _submit(_workspace(tmp_path), provider)

    names = _tool_names(provider.requests[0])
    assert "read" in names
    assert not _OPTIONAL & set(names) and not _mcp_names(names)
    assert "mcp_servers" not in provider.requests[0].system_prompt
    # Nothing was started, and nothing is said about what was left out.
    assert not record.exists()
    assert _mcp_threads(before) == []
    assert "MCP" not in diagnostics and "tool name" not in diagnostics
    assert not (config / "mcp.log").exists()


def test_the_settings_activate_the_tools_the_session_was_given(
    tmp_path: Path, monkeypatch
) -> None:
    _config(tmp_path, monkeypatch, defaultTools=["+codemode", "+tool_search"])
    monkeypatch.setattr(selftest, "availability", lambda: Availability(True))
    workspace = _workspace(tmp_path)

    both = _Provider([_result(final_text="done")])
    _submit(workspace, both, codemode=CodemodeOptions(), tool_search=True)
    only_search = _Provider([_result(final_text="done")])
    _submit(workspace, only_search, tool_search=True)
    only_codemode = _Provider([_result(final_text="done")])
    _submit(workspace, only_codemode, codemode=CodemodeOptions())

    assert _OPTIONAL <= set(_tool_names(both.requests[0]))
    assert _OPTIONAL & set(_tool_names(only_search.requests[0])) == {"tool_search"}
    assert _OPTIONAL & set(_tool_names(only_codemode.requests[0])) == {"codemode"}


def test_a_given_tool_stays_inactive_until_something_activates_it(
    tmp_path: Path, monkeypatch
) -> None:
    # Pi registers both with `defaultActive: false`.
    _config(tmp_path, monkeypatch)
    monkeypatch.setattr(selftest, "availability", lambda: Availability(True))
    provider = _Provider([_result(final_text="done")])

    _submit(
        _workspace(tmp_path), provider, codemode=CodemodeOptions(), tool_search=True
    )

    assert not _OPTIONAL & set(_tool_names(provider.requests[0]))


def test_the_tools_are_given_whatever_registry_the_embedder_passes(
    tmp_path: Path, monkeypatch
) -> None:
    # With its own `tools` the session does not read `defaultTools`; MCP
    # still activates what its servers need.
    config = _config(tmp_path, monkeypatch)
    monkeypatch.setattr(selftest, "availability", lambda: Availability(True))
    entry, _record = _server(tmp_path / "servers")
    _write_mcp_json(
        config / "mcp.json",
        {"docs": {**entry, "toolExposure": {"wipe": "deferred"}}},
    )
    provider = _Provider([_result(final_text="done")])

    diagnostics = _submit(
        _workspace(tmp_path),
        provider,
        tools={},
        codemode=CodemodeOptions(),
        tool_search=True,
        mcp=McpOptions(),
    )

    assert _tool_names(provider.requests[0]) == ["tool_search", "codemode"]
    assert "MCP tools are only reachable" not in diagnostics


def test_mcp_without_the_tools_that_reach_its_servers_says_so(
    tmp_path: Path, monkeypatch
) -> None:
    # Pi: the MCP extension alone, without the codemode extension.
    config = _config(tmp_path, monkeypatch)
    monkeypatch.setattr(selftest, "availability", lambda: Availability(True))
    entry, _record = _server(tmp_path / "servers")
    _write_mcp_json(config / "mcp.json", {"docs": entry})
    provider = _Provider([_result(final_text="done")])

    diagnostics = _submit(_workspace(tmp_path), provider, mcp=McpOptions())

    assert not _OPTIONAL & set(_tool_names(provider.requests[0]))
    assert (
        "MCP tools are only reachable from the codemode or tool_search tool, "
        "but neither is active; they cannot be called." in diagnostics
    )


def test_a_reopened_session_takes_the_same_options(tmp_path: Path, monkeypatch) -> None:
    from pipy_harness.native.session_tree import NativeSessionTree

    config = _config(tmp_path, monkeypatch)
    workspace = _workspace(tmp_path)
    entry, record = _server(tmp_path / "servers")
    _write_mcp_json(config / "mcp.json", {"docs": {**entry, "exposure": "direct"}})
    tree = NativeSessionTree.create(workspace, session_dir=tmp_path / "saved")
    _submit(workspace, _Provider([_result(final_text="one")]), tree=tree)
    assert tree.path is not None and not record.exists()
    provider = _Provider([_result(final_text="two")])

    with open_product_session(
        workspace=workspace,
        session_path=tree.path,
        provider=provider,
        load_context_files=False,
        mcp=McpOptions(),
    ) as session:
        session.submit("again")

    assert _mcp_names(_tool_names(provider.requests[0])) == [
        "mcp__docs__search",
        "mcp__docs__wipe",
    ]


@pytest.mark.parametrize(
    ("options", "error"),
    [
        ({"codemode": True}, "codemode must be CodemodeOptions or None"),
        ({"tool_search": 1}, "tool_search must be a bool"),
        ({"mcp": {}}, "mcp must be McpOptions or None"),
    ],
)
def test_options_of_another_type_are_refused(
    tmp_path: Path, options: dict[str, Any], error: str
) -> None:
    with pytest.raises(TypeError, match=error):
        create_product_session(
            workspace=tmp_path, provider=_Provider([]), **options
        ).close()


# -- SDK-02 ----------------------------------------------------------------


def test_mcp_options_default_to_what_the_command_does(
    tmp_path: Path, monkeypatch
) -> None:
    # `mcp.json` of the config home, real transports, and the first prompt
    # waits for the server with `direct` tools. The `extensions` setting
    # that turns the built-in off for `pipy` does not apply: the embedder
    # asked for MCP.
    config = _config(tmp_path, monkeypatch, extensions=["-builtin:mcp"])
    entry, record = _server(
        tmp_path / "servers",
        methods={
            **_LATE,
            "tools/call": [
                {
                    "send": {
                        "jsonrpc": "2.0",
                        "method": "notifications/message",
                        "params": {"level": "info", "data": "searching"},
                    }
                },
                {"default": True},
            ],
        },
    )
    _write_mcp_json(config / "mcp.json", {"docs": {**entry, "exposure": "direct"}})
    provider = _Provider(
        [
            _result(
                tool_calls=(
                    ProviderToolCall("c1", "mcp__docs__search", '{"query": "pipy"}'),
                )
            ),
            _result(final_text="done"),
        ]
    )
    before = set(threading.enumerate())

    diagnostics = _submit(_workspace(tmp_path), provider, mcp=McpOptions())

    assert _mcp_names(_tool_names(provider.requests[0])) == [
        "mcp__docs__search",
        "mcp__docs__wipe",
    ]
    found = _tool_results(provider.requests[1])[-1]
    assert (found.is_error, found.content.value) == (False, "pipy guide\npipy faq")
    assert _tool_calls(record) == [("search", {"query": "pipy"})]
    assert STILL_CONNECTING_NOTICE not in diagnostics
    assert "searching" in (config / "mcp.log").read_text()
    # Closing the session closed the server.
    assert "stdin-closed" in _events(record)
    _wait_until(lambda: _mcp_threads(before) == [])


def test_no_mcp_turns_mcp_off_for_a_session_that_asked(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch)
    entry, record = _server(tmp_path / "servers")
    _write_mcp_json(config / "mcp.json", {"docs": {**entry, "exposure": "direct"}})
    provider = _Provider([_result(final_text="done")])

    _submit(
        _workspace(tmp_path),
        provider,
        mcp=McpOptions(),
        resources=RuntimeResourceOptions(no_mcp=True),
    )

    assert not _mcp_names(_tool_names(provider.requests[0]))
    assert not record.exists()


def test_the_embedder_loads_the_configuration_and_builds_the_transports(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch)
    workspace = _workspace(tmp_path)
    entry, record = _server(tmp_path / "servers")
    # Not where the session would look.
    elsewhere = tmp_path / "elsewhere"
    _write_mcp_json(elsewhere / "mcp.json", {"docs": {**entry, "exposure": "direct"}})
    _write_mcp_json(config / "mcp.json", {"ignored": {"command": "nonexistent"}})
    loads: list[dict[str, Any]] = []
    built: list[tuple[str, str]] = []

    def load_config(**context: Any) -> LoadedMcpConfig:
        loads.append(context)
        loaded = load_mcp_config(
            config_home=elsewhere, cwd=context["cwd"], project_trusted=False
        )
        extra = McpServerEntry(
            name="off", config={"command": "nonexistent", "enabled": False}, source="x"
        )
        return LoadedMcpConfig(servers=[*loaded.servers, extra], errors=[])

    def create_transport(server: McpServerEntry, cwd: str, auth: Any) -> Any:
        built.append((server.name, cwd))
        return create_default_transport(server, cwd, auth)

    log = tmp_path / "logs" / "servers.log"
    provider = _Provider(
        [
            _result(tool_calls=(ProviderToolCall("c1", "mcp__docs__wipe", "{}"),)),
            _result(final_text="done"),
        ]
    )

    diagnostics = _submit(
        workspace,
        provider,
        mcp=McpOptions(
            load_config=load_config, create_transport=create_transport, log_path=log
        ),
    )

    assert loads == [
        {"config_home": config, "cwd": workspace.resolve(), "project_trusted": False}
    ]
    assert built == [("docs", str(workspace.resolve()))]
    assert _mcp_names(_tool_names(provider.requests[0])) == [
        "mcp__docs__search",
        "mcp__docs__wipe",
    ]
    assert _tool_results(provider.requests[1])[-1].content.value == "wiped"
    assert _tool_calls(record) == [("wipe", {})]
    assert "ignored" not in diagnostics
    assert not (config / "mcp.log").exists()


def test_the_server_log_goes_to_the_given_path(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, monkeypatch)
    entry, _record = _server(
        tmp_path / "servers",
        startup=[
            {
                "send": {
                    "jsonrpc": "2.0",
                    "method": "notifications/message",
                    "params": {"level": "warning", "data": "disk almost full"},
                }
            }
        ],
    )
    _write_mcp_json(config / "mcp.json", {"docs": {**entry, "exposure": "direct"}})
    log = tmp_path / "logs" / "servers.log"

    _submit(
        _workspace(tmp_path),
        _Provider([_result(final_text="done")]),
        mcp=McpOptions(log_path=log),
    )

    assert "disk almost full" in log.read_text()
    assert not (config / "mcp.log").exists()


def test_the_first_prompt_waits_as_long_as_the_embedder_says(
    tmp_path: Path, monkeypatch
) -> None:
    # Pi `startupWaitMs`.
    config = _config(tmp_path, monkeypatch)
    entry, record = _server(
        tmp_path / "servers", "slow", methods=_NEVER, onStdinClose=[]
    )
    _write_mcp_json(config / "mcp.json", {"slow": {**entry, "exposure": "direct"}})
    provider = _Provider([_result(final_text="one"), _result(final_text="two")])
    started = time.monotonic()

    diagnostics = _submit(
        _workspace(tmp_path),
        provider,
        ("go", "again"),
        mcp=McpOptions(startup_wait_seconds=0.2),
    )

    # Far below the 10 seconds a session waits otherwise.
    assert time.monotonic() - started < 8
    assert [_mcp_names(_tool_names(request)) for request in provider.requests] == [
        [],
        [],
    ]
    assert diagnostics.count(STILL_CONNECTING_NOTICE) == 1, diagnostics
    assert "initialize" in _events(record)


def _recorded_managers(monkeypatch) -> list[dict[str, Any]]:
    """The arguments of the MCP managers the session builds from now on."""

    arguments: list[dict[str, Any]] = []

    class Recording(McpManager):
        def __init__(self, **kwargs: Any) -> None:
            arguments.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(mcp_loading, "McpManager", Recording)
    return arguments


def test_the_credential_store_and_the_config_updater_reach_the_manager(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch)
    managers = _recorded_managers(monkeypatch)
    credentials = McpOAuthCredentialStore(tmp_path / "vault" / "mcp-auth.json")
    updates: list[tuple[str, dict[str, object]]] = []

    def update_config(entry: McpServerEntry, patch: Any) -> None:
        updates.append((entry.name, dict(patch)))

    def load_config(**_context: Any) -> LoadedMcpConfig:
        entry = McpServerEntry(
            name="docs",
            config={"command": "nonexistent", "enabled": False},
            source=str(config / "mcp.json"),
        )
        return LoadedMcpConfig(servers=[entry], errors=[])

    workspace = _workspace(tmp_path)
    _submit(
        workspace,
        _Provider([_result(final_text="done")]),
        mcp=McpOptions(
            load_config=load_config,
            credentials=credentials,
            update_config=update_config,
        ),
    )
    _submit(workspace, _Provider([_result(final_text="done")]), mcp=McpOptions())

    given, default = managers
    assert given["credentials"] is credentials
    # A change made in the session is saved by the embedder's function.
    McpManager(**given).set_exposure("docs", "direct")
    assert updates == [("docs", {"exposure": "direct"})]
    assert not (config / "mcp.json").exists()
    assert default["credentials"].path == config / "mcp-auth.json"
    assert default["update_config"] is update_config_file
    assert default["create_transport"] is create_default_transport
    assert default["log"].path == config / "mcp.log"


def _mcp_commands(
    tmp_path: Path, monkeypatch, builtins: SessionBuiltins | None
) -> list[dict[str, Any]]:
    """The arguments of the `/mcp` command a session without a terminal
    builds when `/mcp` is entered."""

    arguments: list[dict[str, Any]] = []

    class Recording(McpCommand):
        def __init__(self, manager: McpManager, **kwargs: Any) -> None:
            arguments.append(kwargs)
            super().__init__(manager, **kwargs)

    monkeypatch.setattr(wiring, "McpCommand", Recording)
    session = CodingSession(
        provider=_Provider([]),
        settings_manager=SettingsManager(
            global_path=tmp_path / "config" / "settings.json",
            project_path=tmp_path / "project.json",
        ),
        resource_options=RuntimeResourceOptions(builtins=builtins),
    )
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/mcp\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    return arguments


def test_the_url_opener_reaches_the_mcp_command(tmp_path: Path, monkeypatch) -> None:
    _config(tmp_path, monkeypatch)
    opened: list[str] = []

    (given,) = _mcp_commands(
        tmp_path,
        monkeypatch,
        SessionBuiltins(mcp=McpOptions(open_url=opened.append)),
    )
    given["open_url"]("https://auth.example/authorize")
    (default,) = _mcp_commands(tmp_path, monkeypatch, SessionBuiltins(mcp=McpOptions()))

    assert opened == ["https://auth.example/authorize"]
    # As for `pipy` without a terminal: nobody is there to sign in.
    assert default["open_url"] is None


@pytest.mark.parametrize("wait", [-1, float("inf"), float("nan")])
def test_a_startup_wait_that_is_no_duration_is_refused(wait: float) -> None:
    with pytest.raises(ValueError, match="startup_wait_seconds"):
        McpOptions(startup_wait_seconds=wait)


@pytest.mark.parametrize("wait", ["1", True, None])
def test_a_startup_wait_must_be_a_number(wait: Any) -> None:
    if wait is None:
        assert McpOptions(startup_wait_seconds=None).startup_wait_seconds is None
        return
    with pytest.raises(TypeError, match="startup_wait_seconds"):
        McpOptions(startup_wait_seconds=wait)


# -- SDK-03 ----------------------------------------------------------------


def test_codemode_options_replace_the_settings(tmp_path: Path) -> None:
    def settings(**codemode: object) -> SettingsManager:
        return SettingsManager(
            global_path=tmp_path / "global.json",
            project_path=tmp_path / "project.json",
            overrides={"codemode": codemode} if codemode else {},
        )

    configured = settings(mode="only", inlineBudget=50)

    def loadout(options: CodemodeOptions | None) -> tuple[str, float]:
        builtins = None if options is None else SessionBuiltins(codemode=options)
        value = codemode_loadout(configured, builtins)
        return value.mode, value.inline_budget

    assert loadout(None) == ("only", 50)
    assert loadout(CodemodeOptions()) == ("only", 50)
    assert loadout(CodemodeOptions(mode="on")) == ("on", 50)
    assert loadout(CodemodeOptions(inline_budget=0)) == ("only", 0)
    assert loadout(CodemodeOptions(mode="on", inline_budget=7.5)) == ("on", 7.5)
    assert codemode_loadout(settings(), SessionBuiltins()) == codemode_loadout(
        settings(), None
    )


def test_a_session_presents_its_tools_as_the_codemode_options_say(
    tmp_path: Path, monkeypatch
) -> None:
    # The settings say mode `on` and the default budget.
    _config(tmp_path, monkeypatch, defaultTools=["+codemode"])
    monkeypatch.setattr(selftest, "availability", lambda: Availability(True))
    workspace = _workspace(tmp_path)

    def first_request(options: CodemodeOptions):
        provider = _Provider([_result(final_text="done")])
        _submit(workspace, provider, codemode=options)
        return provider.requests[0]

    as_configured = first_request(CodemodeOptions())
    only = first_request(CodemodeOptions(mode="only"))
    no_budget = first_request(CodemodeOptions(mode="only", inline_budget=0))

    def codemode(request) -> str:
        return next(
            tool.description
            for tool in request.available_tools
            if tool.name == "codemode"
        )

    assert "read" in _tool_names(as_configured)
    assert "### `read`" not in codemode(as_configured)
    # Mode `only`: the other declarations are left out and `codemode` lists
    # the tools.
    assert _tool_names(only) == ["codemode"]
    assert "### `read`" in codemode(only)
    # Without a budget no tool is described.
    assert _tool_names(no_budget) == ["codemode"]
    assert "### `read`" not in codemode(no_budget)


@pytest.mark.parametrize(
    ("options", "error"),
    [
        ({"mode": "off"}, ValueError),
        ({"inline_budget": -1}, ValueError),
        ({"inline_budget": float("nan")}, ValueError),
        ({"inline_budget": "3000"}, TypeError),
        ({"inline_budget": True}, TypeError),
    ],
)
def test_codemode_options_that_are_no_setting_are_refused(
    options: dict[str, Any], error: type[Exception]
) -> None:
    with pytest.raises(error):
        CodemodeOptions(**options)
