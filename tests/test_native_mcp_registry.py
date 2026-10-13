"""Tests for the registry of MCP servers that extensions register (MCP
parity plan S36, rows EXT-07 and EXT-08): Pi ``McpServerRegistry`` and the
checks of ``registerMcpServer``.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from pipy_harness.native.mcp.registry import (
    McpServerRegistrationError,
    McpServerRegistry,
    RegisteredMcpServer,
    registration_conflict,
    validated_registration,
)

_A = "<global>/extensions/a.py"
_B = "<global>/extensions/b.py"


def _server(name: str, owner: str = _A, **config: object) -> RegisteredMcpServer:
    return validated_registration(name, {"url": "http://x.invalid", **config}, owner)


def test_a_registration_holds_a_validated_copy_of_the_config() -> None:
    raw = {"command": "run", "args": ["a"], "exposure": "codemode-deferred"}

    server = validated_registration("docs", raw, _A)

    raw["args"].append("b")  # type: ignore[attr-defined]
    assert server == RegisteredMcpServer(
        "docs", {"command": "run", "args": ["a"], "exposure": "codemode"}, _A
    )


@pytest.mark.parametrize(
    ("name", "config", "reason"),
    [
        ("my server", {"command": "run"}, 'invalid server name "my server"'),
        ("docs", "run", 'server "docs" must be an object'),
        ("docs", {}, 'server "docs" needs either "command"'),
        ("docs", {"url": "ftp://x"}, "url must be an http or https URL"),
        ("docs", {"command": "run", "exposure": "all"}, "exposure must be one of"),
        (7, {"command": "run"}, "the server name must be a string"),
    ],
)
def test_an_invalid_name_or_config_names_the_extension(
    name: object, config: object, reason: str
) -> None:
    # EXT-07.
    with pytest.raises(McpServerRegistrationError) as raised:
        validated_registration(name, config, _A)

    message = str(raised.value)
    assert message.startswith(f'Invalid MCP server registered by extension "{_A}": ')
    assert reason in message


def test_servers_are_listed_in_registration_order_as_copies() -> None:
    # EXT-08.
    registry = McpServerRegistry()
    registry.register(_server("b"))
    registry.register(_server("a", _B))

    listed = registry.list()
    listed[0].config["url"] = "http://changed.invalid"

    assert [(server.name, server.extension_path) for server in registry.list()] == [
        ("b", _A),
        ("a", _B),
    ]
    assert registry.list()[0].config == {"url": "http://x.invalid"}
    found = registry.get("a")
    assert found is not None and found.extension_path == _B
    found.config["url"] = "http://changed.invalid"
    assert registry.get("a") == _server("a", _B)
    assert registry.get("c") is None


def test_the_registry_keeps_its_own_copy_of_a_registration() -> None:
    registry = McpServerRegistry()
    server = _server("docs")
    registry.register(server)

    server.config["url"] = "http://changed.invalid"

    assert registry.list() == [_server("docs")]


def test_registering_a_name_again_replaces_the_own_registration_in_place() -> None:
    # EXT-07.
    registry = McpServerRegistry()
    registry.register(_server("a"))
    registry.register(_server("b"))

    registry.register(_server("a", url="http://y.invalid"))

    assert [(server.name, server.config["url"]) for server in registry.list()] == [
        ("a", "http://y.invalid"),
        ("b", "http://x.invalid"),
    ]


def test_a_name_another_extension_registered_is_refused() -> None:
    # EXT-07, Pi "rejects names another extension registered".
    registry = McpServerRegistry()
    registry.register(_server("taken"))
    registry.register(_server("my-server"))

    with pytest.raises(McpServerRegistrationError) as taken:
        registry.register(_server("taken", _B))
    with pytest.raises(McpServerRegistrationError) as clash:
        registry.register(_server("my_server", _B))
    with pytest.raises(McpServerRegistrationError) as own_clash:
        registry.register(_server("my_server"))

    assert str(taken.value) == (
        f'MCP server "taken" is already registered by extension "{_A}"'
    )
    conflict = 'MCP server "my_server" conflicts with registered server "my-server"'
    assert str(clash.value) == conflict
    assert str(own_clash.value) == conflict
    assert [server.name for server in registry.list()] == ["taken", "my-server"]


def test_the_owner_of_the_name_is_reported_before_a_namespace_clash() -> None:
    others = [_server("my_docs", _A), _server("my-docs", _B)]

    assert registration_conflict(_server("my-docs", _A), others) == (
        f'MCP server "my-docs" is already registered by extension "{_B}"'
    )
    assert registration_conflict(_server("other", _A), others) is None
    assert registration_conflict(_server("my_docs", _A), others[:1]) is None


def test_an_owner_token_tells_two_extensions_with_one_label_apart() -> None:
    """The label is for display; with tokens they are what owns."""

    registry = McpServerRegistry()
    one, two = object(), object()
    config = {"command": "run"}
    registry.register(validated_registration("docs", config, _A, one))

    with pytest.raises(McpServerRegistrationError) as taken:
        registry.register(validated_registration("docs", config, _A, two))
    registry.unregister("docs", two)
    registry.unregister("docs", _A)
    assert str(taken.value) == (
        f'MCP server "docs" is already registered by extension "{_A}"'
    )
    assert [server.owner for server in registry.list()] == [one]
    assert registry.load_conflict(
        [validated_registration("docs", config, _A, two)]
    ) == str(taken.value)

    registry.register(validated_registration("docs", {"command": "new"}, _A, one))
    assert registry.list()[0].config == {"command": "new"}
    registry.unregister("docs", one)
    assert registry.list() == []
    # The token is not part of what a registration equals.
    assert validated_registration("docs", config, _A, one) == (
        validated_registration("docs", config, _A, two)
    )


def test_load_registers_all_of_an_extensions_servers_or_none() -> None:
    registry = McpServerRegistry()
    registry.load([_server("taken", _B)])
    changes: list[None] = []
    registry.set_change_listener(lambda: changes.append(None))

    with pytest.raises(McpServerRegistrationError, match="already registered"):
        registry.load([_server("fine"), _server("taken")])
    assert registry.load_conflict([_server("a-b"), _server("a_b")]) == (
        'MCP server "a_b" conflicts with registered server "a-b"'
    )
    assert [server.name for server in registry.list()] == ["taken"]

    assert registry.load_conflict([_server("fine"), _server("more")]) is None
    registry.load([_server("fine"), _server("more")])

    assert [server.name for server in registry.list()] == ["taken", "fine", "more"]
    # Loading is not a change anybody follows.
    assert changes == []
    registry.close()
    with pytest.raises(McpServerRegistrationError, match="loaded again"):
        registry.load([_server("late")])


def test_unregister_only_removes_the_own_registration() -> None:
    # EXT-08.
    registry = McpServerRegistry()
    changes: list[list[str]] = []
    registry.set_change_listener(
        lambda: changes.append([server.name for server in registry.list()])
    )
    registry.register(_server("docs"))

    registry.unregister("docs", _B)
    registry.unregister("unknown", _A)
    assert [server.name for server in registry.list()] == ["docs"]
    registry.unregister("docs", _A)

    assert registry.list() == []
    # Only what changed something is announced.
    assert changes == [["docs"], []]


def test_the_listener_runs_after_the_change_and_outside_the_lock() -> None:
    registry = McpServerRegistry()
    seen: list[int] = []

    def listener() -> None:
        # Reading and changing the registry from the listener must not block.
        seen.append(len(registry.list()))
        if len(seen) == 1:
            registry.register(_server("second"))

    registry.set_change_listener(listener)
    registry.register(_server("first"))

    assert seen == [1, 2]
    registry.set_change_listener(None)
    registry.register(_server("third"))
    assert seen == [1, 2]


def _names(changes: Any) -> list[list[str]]:
    return [[server.name for server in change] for change in changes]


def test_the_registry_keeps_what_each_change_left_until_it_is_taken() -> None:
    # EXT-10: the registrations after every change, with or without a
    # listener, as copies.
    registry = McpServerRegistry()
    registry.load([_server("loaded")])
    # What was loaded is not a change.
    assert registry.take_changes() == ()

    registry.register(_server("early"))
    registry.unregister("loaded", _A)
    registry.unregister("unknown", _A)
    with pytest.raises(McpServerRegistrationError):
        registry.register(_server("early", _B))
    registry.set_change_listener(lambda: None)
    registry.register(_server("early", url="http://y.invalid"))

    changes = registry.take_changes()
    assert _names(changes) == [["loaded", "early"], ["early"], ["early"]]
    assert changes[2][0].config == {"url": "http://y.invalid"}
    changes[2][0].config["url"] = "http://changed.invalid"
    assert registry.list()[0].config == {"url": "http://y.invalid"}
    assert registry.take_changes() == ()

    registry.register(_server("late"))
    registry.close()
    # A closed registry has no changes for anybody.
    assert registry.take_changes() == ()


def test_copying_a_config_never_happens_under_the_registry_lock() -> None:
    """A configuration may be a dict subclass whose copy runs the
    extension's code, which may call the registry."""

    registry = McpServerRegistry()
    calls: list[int] = []

    class Config(dict[str, Any]):
        def __deepcopy__(self, memo: Any) -> Config:
            # Blocks for ever when the caller holds the registry's lock.
            calls.append(1)
            registry.get("absent")
            return Config(self)

    def exercise() -> None:
        server = validated_registration("docs", Config(url="http://x.invalid"), _A)
        registry.register(server)
        registry.load([validated_registration("more", Config(command="run"), _A)])
        registry.register(server)
        registry.list()
        registry.get("docs")
        registry.take_changes()
        registry.load_conflict([server])
        registry.unregister("docs", _A)
        registry.take_changes()

    thread = threading.Thread(target=exercise, daemon=True)
    thread.start()
    thread.join(10)

    assert not thread.is_alive()
    assert calls and [server.name for server in registry.list()] == ["more"]


def test_changes_made_on_several_threads_are_kept_in_their_order() -> None:
    """Each entry is taken in the step that made the change: with servers
    that are only added, every entry is the one before plus one server."""

    registry = McpServerRegistry()
    start = threading.Barrier(8)

    def register(index: int) -> None:
        start.wait()
        for step in range(25):
            registry.register(_server(f"s{index}-{step}"))

    threads = [threading.Thread(target=register, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    changes = _names(registry.take_changes())
    assert len(changes) == 200
    assert [len(change) for change in changes] == list(range(1, 201))
    assert all(
        later[: len(earlier)] == earlier
        for earlier, later in zip(changes, changes[1:], strict=False)
    )
    assert changes[-1] == [server.name for server in registry.list()]


def test_a_refused_registration_is_not_announced() -> None:
    registry = McpServerRegistry()
    registry.load([_server("docs")])
    changes: list[None] = []
    registry.set_change_listener(lambda: changes.append(None))

    with pytest.raises(McpServerRegistrationError):
        registry.register(_server("docs", _B))

    assert changes == []


def test_a_closed_registry_refuses_changes_and_can_be_read() -> None:
    registry = McpServerRegistry()
    registry.load([_server("docs")])
    changes: list[None] = []
    registry.set_change_listener(lambda: changes.append(None))

    registry.close()

    with pytest.raises(McpServerRegistrationError, match="loaded again"):
        registry.register(_server("other"))
    with pytest.raises(McpServerRegistrationError, match="loaded again"):
        registry.unregister("docs", _A)
    assert [server.name for server in registry.list()] == ["docs"]
    assert changes == []


def test_one_name_registered_by_two_extensions_at_once_has_one_owner() -> None:
    """The owner check and the registration are one step."""

    for _ in range(50):
        registry = McpServerRegistry()
        start = threading.Barrier(2)
        outcomes: list[str] = []

        def register(owner: str) -> None:
            start.wait()
            try:
                registry.register(_server("docs", owner))
            except McpServerRegistrationError:
                outcomes.append("refused")
            else:
                outcomes.append(owner)

        threads = [
            threading.Thread(target=register, args=(owner,)) for owner in (_A, _B)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        owners = [outcome for outcome in outcomes if outcome != "refused"]
        assert len(owners) == 1 and len(outcomes) == 2
        assert [server.extension_path for server in registry.list()] == owners
