"""Tests for the servers extensions register, at the session's MCP manager
(MCP parity plan S36, rows EXT-06 to EXT-09): the manager over a registry
and in-memory servers. The extension API that fills the registry is tested
in ``test_native_extension_mcp_servers.py``.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_native_mcp_connection import _Server, _wait_until
from test_native_mcp_manager import (
    _WAIT_SECONDS,
    _entry,
    _in_thread,
    _invoke,
    _LockOrder,
    _refuse,
    _serve,
    _Session,
    _SlowClose,
    _status_of,
    _tool,
)

from pipy_harness.native.mcp import manager as manager_module
from pipy_harness.native.mcp.commands import format_status
from pipy_harness.native.mcp.connection import McpServerConnection
from pipy_harness.native.mcp.manager import describe_state
from pipy_harness.native.mcp.manager_menus import servers_menu
from pipy_harness.native.mcp.registry import (
    McpServerRegistry,
    validated_registration,
)

_PLUGIN = "<global>/extensions/plugin.py"


@pytest.fixture
def sessions() -> Iterator[list[_Session]]:
    opened: list[_Session] = []
    yield opened
    for session in opened:
        session.manager.close()


def _register(
    registry: McpServerRegistry, name: str, owner: str = _PLUGIN, **config: Any
) -> None:
    registry.register(validated_registration(name, {"command": "run", **config}, owner))


def _registered_session(
    tmp_path: Path,
    sessions: list[_Session],
    servers: dict[str, _Server],
    entries: list[Any] | None = None,
) -> tuple[_Session, McpServerRegistry]:
    registry = McpServerRegistry()
    session = _Session(tmp_path, servers, entries or [], registered=registry.list)
    sessions.append(session)
    return session, registry


def _names(session: _Session) -> list[str]:
    return [status.entry.name for status in session.manager.servers()]


def _settle(session: _Session) -> None:
    assert session.manager.wait_until_settled(_WAIT_SECONDS)


# -- EXT-06: registered before the session starts -----------------------------


def test_servers_registered_before_the_start_connect_with_it(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # Pi "connects servers registered while extensions load".
    session, registry = _registered_session(
        tmp_path,
        sessions,
        {
            "docs": _Server(tools=[_tool("read")]),
            "plugin": _Server(tools=[_tool("search")]),
        },
        [_entry("docs")],
    )
    _register(registry, "plugin", exposure="direct")
    # Nothing is listed or opened before the start.
    assert _names(session) == ["docs"]

    session.start()

    assert session.opened.count("plugin") == 1
    assert [
        (status.entry.name, status.entry.scope, status.entry.source)
        for status in session.manager.servers()
    ] == [("docs", None, "test"), ("plugin", "extension", _PLUGIN)]
    assert session.mcp_names() == ("mcp__docs__read", "mcp__plugin__search")
    assert "mcp__plugin__search" in session.declared()
    assert session.manager.configured_exposures() == frozenset({"codemode", "direct"})
    assert session.call("mcp__plugin__search") == "ok"


def test_a_registered_server_that_is_not_enabled_does_not_connect(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session, registry = _registered_session(tmp_path, sessions, {"plugin": _Server()})
    _register(registry, "plugin", enabled=False)

    session.start()

    assert session.opened == []
    assert describe_state(_status_of(session, "plugin")) == "disabled"


def test_a_registration_synced_before_the_start_only_changes_the_list(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # A server registered in a ``session_start`` handler.
    session, registry = _registered_session(
        tmp_path, sessions, {"plugin": _Server(tools=[_tool("search")])}
    )
    _register(registry, "plugin")
    _register(registry, "gone")

    session.manager.sync_registered()
    registry.unregister("gone", _PLUGIN)
    session.manager.sync_registered()

    assert _names(session) == ["plugin"]
    assert session.opened == []
    assert _status_of(session, "plugin").connection is None
    session.start()
    assert session.opened == ["plugin"]
    assert session.mcp_names() == ("mcp__plugin__search",)


# -- EXT-09: mcp.json wins ----------------------------------------------------


@pytest.mark.parametrize("name", ["my-docs", "my_docs"])
def test_the_mcp_json_server_wins_over_a_registered_one(
    name: str, tmp_path: Path, sessions: list[_Session]
) -> None:
    # Pi "prefers the mcp.json server over a registered %s" (#10239).
    session, registry = _registered_session(
        tmp_path,
        sessions,
        {"my-docs": _Server(tools=[_tool("read")])},
        [_entry("my-docs")],
    )
    _register(registry, name)

    session.start()

    assert session.opened == ["my-docs"]
    assert _names(session) == ["my-docs"]
    line = f'"{name}" registered by {_PLUGIN} is overridden by "my-docs" in test'
    assert session.manager.overridden() == (line,)
    # A later registration is overridden as well; an unregistration clears it.
    registry.unregister(name, _PLUGIN)
    session.manager.sync_registered()
    assert session.manager.overridden() == ()
    assert session.opened == ["my-docs"]


def test_status_and_menu_list_the_overridden_registrations(tmp_path: Path) -> None:
    # EXT-09, SLH-02, MGR-02.
    line = '"docs" registered by ext.py is overridden by "docs" in mcp.json'

    assert format_status([], ["a.json: bad"], tmp_path, [line]) == (
        f"config error: a.json: bad\noverridden: {line}"
    )
    assert format_status([], [], tmp_path, [line]) == f"overridden: {line}"
    assert servers_menu((), ["a: bad"], tmp_path, [line]).error == (
        f"config: a: bad\noverridden: {line}"
    )
    assert servers_menu((), (), tmp_path, [line]).error == f"overridden: {line}"
    assert servers_menu((), (), tmp_path).error is None


# -- EXT-06, EXT-08: changes in a session -------------------------------------


def test_a_server_registered_later_connects_and_one_unregistered_disconnects(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # Pi "connects and disconnects servers registered during the session".
    late = _Server(tools=[_tool("search")])
    session, registry = _registered_session(tmp_path, sessions, {"late": late})
    session.start()
    assert session.opened == []

    _register(registry, "late")
    session.manager.sync_registered()
    # The attempt exists when the call returns, so a wait covers it.
    _settle(session)

    assert session.opened == ["late"]
    assert "mcp__late__search" in session.callable_names()
    assert session.manager.configured_exposures() == frozenset({"codemode"})
    prepared = session.port("mcp__late__search")
    connection = session.manager._servers[0].connection
    assert connection is not None

    registry.unregister("late", _PLUGIN)
    session.manager.sync_registered()

    # The tools are gone when the call returns; the connection closes after.
    assert session.mcp_names() == ()
    assert _names(session) == []
    assert _invoke(prepared, tmp_path) == (True, 'MCP server "late" is disabled.')
    _wait_until(lambda: connection.state == "closed")
    assert session.manager.configured_exposures() == frozenset()
    assert session.notices() == []


def test_registering_the_same_config_again_changes_nothing(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # EXT-07.
    session, registry = _registered_session(
        tmp_path, sessions, {"plugin": _Server(tools=[_tool("search")])}
    )
    _register(registry, "plugin", args=["a"])
    session.start()
    connection = session.manager._servers[0].connection
    # A change made in the session lasts while the registration stays.
    assert session.manager.set_exposure("plugin", "direct") is None

    _register(registry, "plugin", args=["a"])
    session.manager.sync_registered()
    _settle(session)

    assert session.opened == ["plugin"]
    assert session.manager._servers[0].connection is connection
    assert _status_of(session, "plugin").entry.config["exposure"] == "direct"
    assert session.saves == []


def test_registering_another_config_reconnects_once_the_old_connection_closed(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # EXT-07: Pi drops the server, closes its connection, then connects.
    first = _Server(tools=[_tool("search")])
    second = _Server(tools=[_tool("fetch")])
    session, registry = _registered_session(tmp_path, sessions, {"plugin": first})
    slow = _SlowClose(first.client_end)
    _serve(session, slow, second.client_end)
    _register(registry, "plugin", args=["a"])
    session.start()
    assert session.manager.set_exposure("plugin", "direct") is None

    _register(registry, "plugin", args=["b"])
    session.manager.sync_registered()

    assert slow.closing.wait(_WAIT_SECONDS)
    # The old tools are gone and nothing is opened while the old transport
    # is still closing.
    assert session.mcp_names() == ()
    assert session.opened == ["plugin"]
    status = _status_of(session, "plugin")
    assert status.connection is None and describe_state(status) == "starting"
    assert status.entry.config == {"command": "run", "args": ["b"]}
    assert not session.manager.wait_for_servers(timeout=0.05)

    slow.release.set()
    _settle(session)

    assert session.opened == ["plugin", "plugin"]
    assert session.mcp_names() == ("mcp__plugin__fetch",)
    assert session.notices() == []


def test_a_server_registered_again_later_waits_for_the_closing_connection(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    """The unregistration and the new registration are two changes: the
    second one still finds what the first is closing."""

    first = _Server(tools=[_tool("search")])
    second = _Server(tools=[_tool("fetch")])
    session, registry = _registered_session(tmp_path, sessions, {"plugin": first})
    slow = _SlowClose(first.client_end)
    _serve(session, slow, second.client_end)
    _register(registry, "plugin")
    session.start()

    registry.unregister("plugin", _PLUGIN)
    session.manager.sync_registered()
    assert slow.closing.wait(_WAIT_SECONDS)
    _register(registry, "other", enabled=False)
    session.manager.sync_registered()
    _register(registry, "plugin")
    session.manager.sync_registered()

    assert session.opened == ["plugin"]
    assert describe_state(_status_of(session, "plugin")) == "starting"
    assert not session.manager.wait_for_servers(timeout=0.05)

    slow.release.set()
    _settle(session)

    assert session.opened == ["plugin", "plugin"]
    assert session.mcp_names() == ("mcp__plugin__fetch",)
    # What has closed is forgotten with the next change.
    registry.unregister("other", _PLUGIN)
    session.manager.sync_registered()
    assert session.manager._closing_registered == {}


def test_removing_a_server_ends_a_wait_for_its_attempt(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    """A wait that began while the replacement waited for the old connection
    to close does not wait for that close once the replacement is gone."""

    first = _Server(tools=[_tool("search")])
    session, registry = _registered_session(tmp_path, sessions, {"plugin": first})
    slow = _SlowClose(first.client_end)
    _serve(session, slow)
    _register(registry, "plugin", args=["a"])
    session.start()
    _register(registry, "plugin", args=["b"])
    session.manager.sync_registered()
    assert slow.closing.wait(_WAIT_SECONDS)
    waiting, waited = _in_thread(
        lambda: session.manager.wait_for_servers(timeout=_WAIT_SECONDS)
    )
    waiting.join(0.1)
    assert waiting.is_alive()

    registry.unregister("plugin", _PLUGIN)
    session.manager.sync_registered()
    waiting.join(_WAIT_SECONDS)

    assert waited == [True]
    assert _names(session) == []
    assert session.manager.wait_for_servers(timeout=0)
    slow.release.set()
    _wait_until(lambda: session.manager._retired == [])
    assert session.opened == ["plugin"]


def test_removing_a_server_ends_a_wait_for_an_earlier_attempt_of_it(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    """The wait began on the attempt of the replacement; a disable and an
    enable gave the server another attempt before it was removed."""

    first = _Server(tools=[_tool("search")])
    session, registry = _registered_session(tmp_path, sessions, {"plugin": first})
    slow = _SlowClose(first.client_end)
    _serve(session, slow)
    _register(registry, "plugin", args=["a"])
    session.start()
    _register(registry, "plugin", args=["b"])
    session.manager.sync_registered()
    assert slow.closing.wait(_WAIT_SECONDS)
    waiting, waited = _in_thread(
        lambda: session.manager.wait_for_servers(timeout=_WAIT_SECONDS)
    )
    waiting.join(0.1)
    assert waiting.is_alive()
    earlier = session.manager._servers[0].ready
    disabling, _ = _in_thread(lambda: session.manager.set_enabled("plugin", False))
    _wait_until(lambda: not _status_of(session, "plugin").enabled)
    enabling, _ = _in_thread(lambda: session.manager.set_enabled("plugin", True))
    _wait_until(lambda: session.manager._servers[0].ready is not earlier)

    registry.unregister("plugin", _PLUGIN)
    session.manager.sync_registered()
    waiting.join(_WAIT_SECONDS)

    assert waited == [True]
    # Nothing of the old connection had to end for that.
    assert not slow.release.is_set()
    slow.release.set()
    for thread in (disabling, enabling):
        thread.join(_WAIT_SECONDS)
        assert not thread.is_alive()
    assert session.opened == ["plugin"]


def test_a_key_order_change_of_tool_exposure_is_another_config(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    """Among ``toolExposure`` patterns the first match wins, so the order of
    the keys is part of the configuration."""

    session, registry = _registered_session(
        tmp_path,
        sessions,
        {"plugin": _Server(tools=[_tool("search")])},
    )
    _register(registry, "plugin", toolExposure={"s*": "direct", "*h": "hidden"})
    session.start()
    assert "mcp__plugin__search" in session.declared()
    session.servers["plugin"] = _Server(tools=[_tool("search")])

    _register(registry, "plugin", toolExposure={"*h": "hidden", "s*": "direct"})
    session.manager.sync_registered()
    _settle(session)

    assert session.opened == ["plugin", "plugin"]
    assert "mcp__plugin__search" not in session.callable_names()


def test_a_server_registered_later_that_fails_is_reported_alone(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # Pi `reportProblems(ctx, connecting)`: no config errors, only the new ones.
    session, registry = _registered_session(
        tmp_path,
        sessions,
        {
            "docs": _Server(handlers={"initialize": _refuse}),
            "late": _Server(handlers={"initialize": _refuse}),
            "fine": _Server(tools=[_tool("search")]),
        },
        [_entry("docs")],
    )
    session.manager.config_errors = ("/cfg/mcp.json: bad",)
    session.start()
    assert len(session.notices()) == 1

    _register(registry, "late")
    _register(registry, "fine")
    session.manager.sync_registered()
    _settle(session)
    _wait_until(lambda: bool(session.manager._notices))

    error = _status_of(session, "late").connection.error  # type: ignore[union-attr]
    assert error is not None
    assert session.notices() == [
        (
            "warning",
            "MCP servers need attention:\n"
            f"  late: failed: {error.split(chr(10))[0]}\n"
            "Run /mcp to fix.",
        )
    ]


def test_disabling_a_registered_server_lasts_while_it_stays_registered(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    # EXT-09.
    session, registry = _registered_session(
        tmp_path, sessions, {"plugin": _Server(tools=[_tool("search")])}
    )
    _register(registry, "plugin")
    session.start()

    assert session.manager.set_enabled("plugin", False) is None
    _register(registry, "other", enabled=False)
    session.manager.sync_registered()

    assert _names(session) == ["plugin", "other"]
    assert not _status_of(session, "plugin").enabled
    assert session.opened == ["plugin"]
    assert session.saves == []
    # Registered again with another configuration, it is a new server.
    session.servers["plugin"] = _Server(tools=[_tool("search")])
    _register(registry, "plugin", args=["x"])
    session.manager.sync_registered()
    _settle(session)
    assert _names(session) == ["other", "plugin"]
    assert _status_of(session, "plugin").enabled
    assert session.opened == ["plugin", "plugin"]


def test_registered_changed_returns_at_once_also_while_publications_are_held(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session, registry = _registered_session(
        tmp_path, sessions, {"late": _Server(tools=[_tool("search")])}
    )
    session.start()
    registry.set_change_listener(session.manager.registered_changed)

    with session.manager.publications_held():
        _register(registry, "late")
        assert _names(session) == []

    _wait_until(lambda: session.mcp_names() == ("mcp__late__search",))
    registry.unregister("late", _PLUGIN)
    _wait_until(lambda: session.mcp_names() == ())


def test_close_closes_a_registered_server_and_later_changes_do_nothing(
    tmp_path: Path,
) -> None:
    first = _Server(tools=[_tool("search")])
    registry = McpServerRegistry()
    session = _Session(tmp_path, {"plugin": first}, [], registered=registry.list)
    slow = _SlowClose(first.client_end)
    _serve(session, slow)
    _register(registry, "plugin")
    session.start()
    registry.unregister("plugin", _PLUGIN)
    session.manager.sync_registered()
    assert slow.closing.wait(_WAIT_SECONDS)

    closing = threading.Thread(target=session.manager.close, daemon=True)
    closing.start()
    closing.join(0.1)
    # ``close`` waits for the connection the unregistration is still closing.
    assert closing.is_alive()
    slow.release.set()
    closing.join(_WAIT_SECONDS)
    assert not closing.is_alive()

    _register(registry, "plugin")
    session.manager.sync_registered()
    assert session.manager.servers() == ()
    assert session.opened == ["plugin"]


def test_registered_changes_take_the_locks_in_the_documented_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order = _LockOrder()
    # The registry's lock is a leaf: nothing is taken while it is held, and
    # it is never taken under the manager lock.
    order.RANKS = {**_LockOrder.RANKS, "registry": 4}  # type: ignore[misc]

    def recorded_connection(**kwargs: Any) -> McpServerConnection:
        connection = McpServerConnection(**kwargs)
        connection._lock = order.lock("connection", connection._lock)
        return connection

    monkeypatch.setattr(manager_module, "McpServerConnection", recorded_connection)
    registry = McpServerRegistry()
    registry._lock = order.lock("registry", registry._lock)  # type: ignore[assignment]
    session = _Session(
        tmp_path,
        {"plugin": _Server(tools=[_tool("search")])},
        [],
        state_lock=order.lock("state", threading.RLock()),
        registered=registry.list,
    )
    manager = session.manager
    manager._lock = order.lock("manager", manager._lock)  # type: ignore[assignment]
    manager._publication = order.lock("publication", manager._publication)  # type: ignore[assignment]
    registry.set_change_listener(manager.sync_registered)

    _register(registry, "plugin", exposure="direct")
    session.start()
    session.servers["plugin"] = _Server(tools=[_tool("search")])
    _register(registry, "plugin", exposure="codemode")
    _settle(session)
    registry.unregister("plugin", _PLUGIN)
    manager.close()

    assert order.taken == {"publication", "manager", "connection", "state", "registry"}
    assert order.violations == []
