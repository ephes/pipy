"""MCP servers registered by extensions (MCP parity plan S36, rows EXT-06
to EXT-09): ``api.register_mcp_server``, ``api.unregister_mcp_server`` and
``api.get_mcp_servers``.

First the extension API against the registry of an activation batch, then
coding sessions with a scripted provider and the fake stdio server as a
real child process: the mirrors of Pi's "AgentSession MCP servers
registered by extensions" cases (``agent-session-mcp.test.ts``). The
registry and the manager's side are tested in
``test_native_mcp_registry.py`` and ``test_native_mcp_manager_registered.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from test_native_extension_activation_sealing import _host
from test_native_extension_tool_metadata import _result, _tool_names, _tool_results
from test_native_mcp_reload import _Loads, _Steps
from test_native_mcp_reload import _run as _run_steps
from test_native_mcp_session import (
    _events,
    _extension,
    _Provider,
    _recorded_managers,
    _run,
    _server,
    _settings,
    _wait_until,
)

from pipy_harness.extensions import (
    ExtensionCapabilityError,
    McpServerRegistrationError,
    RegisteredMcpServer,
)
from pipy_harness.models import HarnessStatus
from pipy_harness.native.extension_types import REASON_DUPLICATE_MCP_SERVER
from pipy_harness.native.extensions.activation import (
    _ACTIVATION_LIFECYCLE_TOKEN,
    activate_extension_batch,
)
from pipy_harness.native.extensions.contracts import ExtensionActivationBatch
from pipy_harness.native.extensions.packages import discover_extensions
from pipy_harness.native.mcp.registry import McpServerRegistry
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.repl.mcp_session import McpSession
from pipy_harness.native.resource_loading import RuntimeResourceOptions

_A = ".pipy/extensions/a.py"
_B = ".pipy/extensions/b.py"


def _write(root: Path, name: str, body: str) -> None:
    _extension(root, name, body)


def _batch(root: Path, **kwargs: Any) -> ExtensionActivationBatch:
    return activate_extension_batch(
        discover_extensions(root, include_workspace_defaults=True), **kwargs
    )


def _by_name(batch: ExtensionActivationBatch) -> dict[str, Any]:
    return {item.name: item for item in batch.activated}


def _listed(registry: McpServerRegistry) -> list[tuple[str, Any, str]]:
    return [
        (server.name, server.config.get("url"), server.extension_path)
        for server in registry.list()
    ]


# -- the API while extensions load --------------------------------------------


def test_servers_registered_while_extensions_load_are_in_the_batch_registry(
    tmp_path: Path,
) -> None:
    # EXT-06, EXT-08.
    _write(
        tmp_path,
        "a",
        "import json\n"
        "from pathlib import Path\n"
        "def activate(api):\n"
        "    config = {'url': 'http://a.invalid', 'exposure': 'codemode-deferred'}\n"
        "    api.register_mcp_server('alpha', config)\n"
        "    config['url'] = 'http://changed.invalid'\n"
        "    api.register_mcp_server('gone', {'command': 'run'})\n"
        "    api.unregister_mcp_server('gone')\n"
        "    api.unregister_mcp_server('never')\n"
        "    # An extension does not see its own registrations while it loads.\n"
        "    Path(__file__).with_name('a.json').write_text(\n"
        "        json.dumps([s.name for s in api.get_mcp_servers()]))\n",
    )
    _write(
        tmp_path,
        "b",
        "import json\n"
        "from pathlib import Path\n"
        "def activate(api):\n"
        "    api.unregister_mcp_server('alpha')\n"
        "    api.register_mcp_server('beta', {'url': 'http://b.invalid'})\n"
        "    seen = api.get_mcp_servers()\n"
        "    seen[0].config['url'] = 'http://changed.invalid'\n"
        "    Path(__file__).with_name('b.json').write_text(json.dumps(\n"
        "        [[s.name, s.extension_path] for s in api.get_mcp_servers()]))\n",
    )

    batch = _batch(tmp_path)

    extensions = tmp_path / ".pipy" / "extensions"
    assert [item.status for item in _by_name(batch).values()] == ["activated"] * 2
    assert json.loads((extensions / "a.json").read_text()) == []
    assert json.loads((extensions / "b.json").read_text()) == [["alpha", _A]]
    assert batch.mcp_servers.list() == [
        RegisteredMcpServer(
            "alpha", {"url": "http://a.invalid", "exposure": "codemode"}, _A
        ),
        RegisteredMcpServer("beta", {"url": "http://b.invalid"}, _B),
    ]
    assert [server.name for server in _by_name(batch)["a"].mcp_servers] == ["alpha"]


def test_names_another_extension_registered_are_rejected(tmp_path: Path) -> None:
    # EXT-07, Pi "rejects names another extension registered".
    _write(
        tmp_path,
        "a",
        "def activate(api):\n"
        "    api.register_mcp_server('taken', {'url': 'http://x.invalid'})\n"
        "    # Registering again replaces the extension's own registration.\n"
        "    api.register_mcp_server('taken', {'url': 'http://y.invalid'})\n"
        "    api.register_mcp_server('my-server', {'url': 'http://x.invalid'})\n",
    )
    _write(
        tmp_path,
        "b",
        "import json\n"
        "from pathlib import Path\n"
        "from pipy_harness.extensions import McpServerRegistrationError\n"
        "def activate(api):\n"
        "    errors = []\n"
        "    for name in ('taken', 'my_server'):\n"
        "        try:\n"
        "            api.register_mcp_server(name, {'url': 'http://z.invalid'})\n"
        "        except McpServerRegistrationError as error:\n"
        "            errors.append(str(error))\n"
        "    Path(__file__).with_name('errors.json').write_text(json.dumps(errors))\n",
    )

    batch = _batch(tmp_path)

    errors = json.loads((tmp_path / ".pipy" / "extensions" / "errors.json").read_text())
    assert errors == [
        f'MCP server "taken" is already registered by extension "{_A}"',
        # Names that differ only in - and _ share a namespace (#10239).
        'MCP server "my_server" conflicts with registered server "my-server"',
    ]
    # The extension that caught the errors is loaded.
    assert [item.status for item in _by_name(batch).values()] == ["activated"] * 2
    assert _listed(batch.mcp_servers) == [
        ("taken", "http://y.invalid", _A),
        ("my-server", "http://x.invalid", _A),
    ]


@pytest.mark.parametrize(
    ("call", "reason"),
    [
        ("'my server', {'command': 'run'}", 'invalid server name "my server"'),
        ("'docs', {'url': 'ftp://x'}", "url must be an http or https URL"),
        ("'docs', ['run']", 'server "docs" must be an object'),
        ("7, {'command': 'run'}", "the server name must be a string"),
    ],
)
def test_an_invalid_registration_raises_and_disables_an_extension_that_lets_it(
    tmp_path: Path, call: str, reason: str
) -> None:
    # EXT-07.
    _write(
        tmp_path,
        "a",
        "def activate(api):\n"
        "    api.register_mcp_server('fine', {'command': 'run'})\n"
        f"    api.register_mcp_server({call})\n",
    )
    _write(
        tmp_path,
        "b",
        "from pathlib import Path\n"
        "def activate(api):\n"
        "    try:\n"
        f"        api.register_mcp_server({call})\n"
        "    except ValueError as error:\n"
        "        Path(__file__).with_name('error.txt').write_text(str(error))\n"
        "    api.register_mcp_server('kept', {'command': 'run'})\n",
    )

    batch = _batch(tmp_path)

    failed, caught = _by_name(batch)["a"], _by_name(batch)["b"]
    assert (failed.status, failed.reason) == ("disabled", "activation_error")
    assert caught.status == "activated"
    message = (tmp_path / ".pipy" / "extensions" / "error.txt").read_text()
    assert message.startswith(f'Invalid MCP server registered by extension "{_B}": ')
    assert reason in message
    # Nothing of the extension that failed is registered.
    assert [server.name for server in batch.mcp_servers.list()] == ["kept"]


def test_one_extension_cannot_register_two_names_of_one_namespace(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "a",
        "from pathlib import Path\n"
        "def activate(api):\n"
        "    api.register_mcp_server('my-server', {'command': 'run'})\n"
        "    try:\n"
        "        api.register_mcp_server('my_server', {'command': 'run'})\n"
        "    except ValueError as error:\n"
        "        Path(__file__).with_name('error.txt').write_text(str(error))\n",
    )

    batch = _batch(tmp_path)

    assert (tmp_path / ".pipy" / "extensions" / "error.txt").read_text() == (
        'MCP server "my_server" conflicts with registered server "my-server"'
    )
    assert [server.name for server in batch.mcp_servers.list()] == ["my-server"]


_REGISTER_SHARED = (
    "def activate(api):\n"
    "    api.register_mcp_server('{own}', {{'command': 'run'}})\n"
    "    api.register_mcp_server('{shared}', {{'command': '{own}'}})\n"
)


def test_pending_extensions_settle_a_shared_name_when_they_are_finalized(
    tmp_path: Path,
) -> None:
    """Extensions that load before the project is trusted do not see each
    other's registrations; the later one is disabled when both are
    finalized, like a later extension with a command name that is taken."""

    _write(tmp_path, "a", _REGISTER_SHARED.format(own="a", shared="my-docs"))
    _write(tmp_path, "b", _REGISTER_SHARED.format(own="b", shared="my_docs"))
    descriptors = discover_extensions(tmp_path, include_workspace_defaults=True)

    pending = activate_extension_batch(descriptors, pending=True)

    assert [item.status for item in _by_name(pending).values()] == ["activated"] * 2
    # Nothing is registered before an extension is finalized.
    assert pending.mcp_servers.list() == []
    host = _by_name(pending)["a"]._pending_activation._host
    with pytest.raises(ExtensionCapabilityError):
        host.register_mcp_server("late", {"command": "run"})
    with pytest.raises(ExtensionCapabilityError):
        host.get_mcp_servers()

    final = activate_extension_batch(descriptors, preloaded=pending)

    first, second = _by_name(final)["a"], _by_name(final)["b"]
    assert first.status == "activated"
    assert (second.status, second.reason, second.diagnostic) == (
        "disabled",
        REASON_DUPLICATE_MCP_SERVER,
        'MCP server "my_docs" conflicts with registered server "my-docs"',
    )
    assert final.mcp_servers is pending.mcp_servers
    assert [
        (server.name, server.extension_path) for server in final.mcp_servers.list()
    ] == [("a", _A), ("my-docs", _A)]


# -- the API after the extensions loaded --------------------------------------


def test_a_loaded_extension_registers_and_unregisters_in_the_registry(
    tmp_path: Path,
) -> None:
    # EXT-06 to EXT-08: what a handler does through the `api` it kept.
    _write(tmp_path, "a", "def activate(api):\n    pass\n")
    _write(
        tmp_path,
        "b",
        "def activate(api):\n"
        "    api.register_mcp_server('loaded', {'url': 'http://b.invalid'})\n",
    )
    batch = _batch(tmp_path)
    first = _by_name(batch)["a"]._activation_host
    second = _by_name(batch)["b"]._activation_host
    changes: list[list[str]] = []
    registry = batch.mcp_servers
    registry.set_change_listener(
        lambda: changes.append([server.name for server in registry.list()])
    )

    first.register_mcp_server("la-te", {"url": "http://one.invalid"})
    first.register_mcp_server("la-te", {"url": "http://two.invalid"})
    with pytest.raises(McpServerRegistrationError, match="already registered"):
        first.register_mcp_server("loaded", {"url": "http://a.invalid"})
    with pytest.raises(McpServerRegistrationError, match="conflicts with"):
        second.register_mcp_server("la_te", {"command": "x"})
    with pytest.raises(McpServerRegistrationError, match="Invalid MCP server"):
        first.register_mcp_server("bad name", {"command": "run"})
    # Only the own registration is removed.
    first.unregister_mcp_server("loaded")
    second.unregister_mcp_server("la-te")

    assert _listed(registry) == [
        ("loaded", "http://b.invalid", _B),
        ("la-te", "http://two.invalid", _A),
    ]
    assert [(s.name, s.extension_path) for s in first.get_mcp_servers()] == [
        ("loaded", _B),
        ("la-te", _A),
    ]
    first.unregister_mcp_server("la-te")
    assert changes == [["loaded", "la-te"], ["loaded", "la-te"], ["loaded"]]

    first._dispose()
    with pytest.raises(ExtensionCapabilityError):
        first.register_mcp_server("after", {"command": "run"})
    with pytest.raises(ExtensionCapabilityError):
        first.unregister_mcp_server("after")
    with pytest.raises(ExtensionCapabilityError):
        first.get_mcp_servers()
    second._dispose()


def _cli_extension(root: Path, name: str, body: str) -> Path:
    """An extension directory called ``plugin`` whose manifest names it
    ``name``; every one of them has the path label ``<cli>/plugin``."""

    directory = root / name / "plugin"
    directory.mkdir(parents=True)
    (directory / "extension.py").write_text(body, encoding="utf-8")
    (directory / "pipy-extension.toml").write_text(
        f'name = "{name}"\n', encoding="utf-8"
    )
    return directory


def test_extensions_with_one_path_label_own_their_servers_separately(
    tmp_path: Path,
) -> None:
    """The path label names the owner to the user; it does not make two
    extensions one owner (EXT-07, EXT-08)."""

    workspace = tmp_path / "work"
    workspace.mkdir()
    first = _cli_extension(
        tmp_path,
        "one",
        "def activate(api):\n"
        "    api.register_mcp_server('docs', {'url': 'http://one.invalid'})\n",
    )
    second = _cli_extension(
        tmp_path,
        "two",
        "from pathlib import Path\n"
        "def activate(api):\n"
        "    try:\n"
        "        api.register_mcp_server('docs', {'url': 'http://two.invalid'})\n"
        "    except ValueError as error:\n"
        "        Path(__file__).with_name('error.txt').write_text(str(error))\n"
        "    api.unregister_mcp_server('docs')\n"
        "    api.register_mcp_server('own', {'url': 'http://two.invalid'})\n",
    )
    descriptors = discover_extensions(
        workspace,
        config_home_env={},
        home_dir=tmp_path,
        explicit_paths=(first, second),
        include_defaults=False,
    )
    assert [descriptor.path_label for descriptor in descriptors] == ["<cli>/plugin"] * 2

    batch = activate_extension_batch(descriptors)

    assert [item.status for item in batch.activated] == ["activated"] * 2
    assert (second / "error.txt").read_text() == (
        'MCP server "docs" is already registered by extension "<cli>/plugin"'
    )
    assert _listed(batch.mcp_servers) == [
        ("docs", "http://one.invalid", "<cli>/plugin"),
        ("own", "http://two.invalid", "<cli>/plugin"),
    ]
    hosts: list[Any] = [item._activation_host for item in batch.activated]
    one, two = hosts
    # Also after loading: neither replaces or removes the other's server.
    with pytest.raises(McpServerRegistrationError, match="already registered"):
        one.register_mcp_server("own", {"url": "http://one.invalid"})
    one.unregister_mcp_server("own")
    two.unregister_mcp_server("docs")
    assert [name for name, _, _ in _listed(batch.mcp_servers)] == ["docs", "own"]
    two.register_mcp_server("own", {"url": "http://again.invalid"})
    one.unregister_mcp_server("docs")
    assert _listed(batch.mcp_servers) == [
        ("own", "http://again.invalid", "<cli>/plugin")
    ]
    # What an extension is given does not carry the owners' tokens.
    assert [server.owner for server in one.get_mcp_servers()] == [None]
    one._dispose()
    two._dispose()


def test_the_commit_loads_the_staged_servers_before_later_calls_change_them() -> None:
    """A call that finds the host committed finds its servers registered:
    what it removes stays removed and what it replaces stays replaced."""

    during: list[list[tuple[str, Any, str]]] = []

    def boundary(event: str) -> None:
        if event != "frozen_commit_flush":
            return
        during.append(_listed(api._mcp_servers))
        api.unregister_mcp_server("docs")
        api.register_mcp_server("other", {"url": "http://new.invalid"})

    api = _host(boundary_observer=boundary)
    api.register_mcp_server("docs", {"url": "http://docs.invalid"})
    api.register_mcp_server("other", {"url": "http://old.invalid"})
    api._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    with pytest.raises(ExtensionCapabilityError):
        api.unregister_mcp_server("docs")
    api._accept_message_route()

    api._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)

    assert during == [
        [
            ("docs", "http://docs.invalid", "sealed-test"),
            ("other", "http://old.invalid", "sealed-test"),
        ]
    ]
    assert _listed(api._mcp_servers) == [("other", "http://new.invalid", "sealed-test")]
    api._dispose()


def test_a_commit_whose_servers_are_refused_leaves_the_host_sealed() -> None:
    api = _host()
    api.register_mcp_server("docs", {"url": "http://docs.invalid"})
    api.register_mcp_server("mine", {"url": "http://mine.invalid"})
    api._seal_and_freeze(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)
    api._accept_message_route()
    # Another extension got the name in between; the callers rule that out.
    api._mcp_servers.register(RegisteredMcpServer("docs", {"command": "run"}, _B))

    with pytest.raises(McpServerRegistrationError, match="already registered"):
        api._commit_activation(_lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN)

    assert api._state == "sealed"
    assert [name for name, _, _ in _listed(api._mcp_servers)] == ["docs"]
    with pytest.raises(ExtensionCapabilityError):
        api.get_mcp_servers()
    api._dispose()


# -- sessions -----------------------------------------------------------------

_PLUGIN = ".pipy/extensions/plugin.py"

# Registers the servers of `servers.json` while it loads, and offers tools
# that register and unregister one of `later.json` during the session.
_PLUGIN_BODY = """import json
from pathlib import Path
from pipy_harness.extensions import ExtensionTool, ToolResult

HERE = Path(__file__).parent


def _servers(name):
    path = HERE / name
    return json.loads(path.read_text()) if path.exists() else {}


def activate(api):
    for name, config in _servers('servers.json').items():
        api.register_mcp_server(name, config)

    def add(ctx, params):
        api.register_mcp_server(params['name'], _servers('later.json')[params['name']])
        return ToolResult(content='registered')

    def remove(ctx, params):
        api.unregister_mcp_server(params['name'])
        return ToolResult(content='unregistered')

    for name, handler in (('add_server', add), ('remove_server', remove)):
        api.register_tool(ExtensionTool(
            name=name, description=name,
            input_schema={'type': 'object',
                          'properties': {'name': {'type': 'string'}},
                          'required': ['name'], 'additionalProperties': False},
            handler=handler))

    @api.on('session_start')
    def started(event, ctx):
        for name, config in _servers('at-start.json').items():
            api.register_mcp_server(name, config)
"""

# Waits until the MCP tools are `count` many.
_COUNT_BODY = """import time
from pipy_harness.extensions import ExtensionTool, ToolResult


def activate(api):
    def wait(ctx, params):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            names = [t.name for t in ctx.get_all_tools() if t.name.startswith('mcp__')]
            if len(names) == params['count']:
                return ToolResult(content=' '.join(names) or 'none')
            time.sleep(0.02)
        return ToolResult(content='timed out', is_error=True)

    api.register_tool(ExtensionTool(
        name='wait_for_count', description='Wait for the MCP tools.',
        input_schema={'type': 'object',
                      'properties': {'count': {'type': 'integer'}},
                      'required': ['count'], 'additionalProperties': False},
        handler=wait))
"""


def _plugin(workspace: Path, **files: dict[str, Any]) -> Path:
    """The plugin extension with its server files (`servers`, `later`,
    `at_start`)."""

    directory = _extension(workspace, "plugin", _PLUGIN_BODY)
    for name, servers in files.items():
        (directory / f"{name.replace('_', '-')}.json").write_text(json.dumps(servers))
    return directory


def _mcp_names(request: Any) -> list[str]:
    return [name for name in _tool_names(request) if name.startswith("mcp__")]


def _call(call_id: str, tool: str, /, **arguments: Any) -> ProviderToolCall:
    return ProviderToolCall(call_id, tool, json.dumps(arguments))


def test_a_server_registered_while_extensions_load_connects_with_the_session(
    tmp_path: Path, monkeypatch
) -> None:
    # EXT-06, Pi "connects servers registered while extensions load".
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    workspace = tmp_path / "work"
    entry, record = _server(tmp_path / "servers", "plugin")
    _plugin(workspace, servers={"plugin": {**entry, "exposure": "direct"}})
    provider = _Provider([_result(final_text="ready")])

    status, errors = _run(workspace, provider, "start\n")

    assert status is HarnessStatus.SUCCEEDED, errors
    # The first prompt waited for the server, as for one of mcp.json.
    assert _mcp_names(provider.requests[0]) == [
        "mcp__plugin__search",
        "mcp__plugin__wipe",
    ]
    assert "stdin-closed" in _events(record)
    assert "MCP" not in errors


def test_a_server_registered_in_a_session_start_handler_connects_with_the_session(
    tmp_path: Path, monkeypatch
) -> None:
    # EXT-06: `session_start` runs before the servers start.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    workspace = tmp_path / "work"
    entry, _record = _server(tmp_path / "servers", "early")
    _plugin(workspace, at_start={"early": {**entry, "exposure": "direct"}})
    provider = _Provider([_result(final_text="ready")])

    status, errors = _run(workspace, provider, "start\n")

    assert status is HarnessStatus.SUCCEEDED, errors
    assert _mcp_names(provider.requests[0]) == [
        "mcp__early__search",
        "mcp__early__wipe",
    ]


def test_servers_registered_during_the_session_connect_and_disconnect(
    tmp_path: Path, monkeypatch
) -> None:
    # EXT-06, EXT-08, Pi "connects and disconnects servers registered
    # during the session".
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    workspace = tmp_path / "work"
    entry, record = _server(tmp_path / "servers", "late")
    _plugin(workspace, later={"late": {**entry, "exposure": "direct"}})
    _extension(workspace, "count", _COUNT_BODY)
    provider = _Provider(
        [
            _result(tool_calls=(_call("c1", "add_server", name="late"),)),
            _result(tool_calls=(_call("c2", "wait_for_count", count=2),)),
            _result(
                tool_calls=(
                    _call("c3", "mcp__late__search", query="pipy"),
                    _call("c4", "remove_server", name="late"),
                )
            ),
            _result(tool_calls=(_call("c5", "mcp__late__search", query="again"),)),
            _result(final_text="done"),
        ]
    )

    status, errors = _run(workspace, provider)

    assert status is HarnessStatus.SUCCEEDED, errors
    assert _mcp_names(provider.requests[0]) == []
    assert _tool_results(provider.requests[1])[-1].content.value == "registered"
    # Registered while the run was going on: connected by the next turn's
    # tool call, declared from the turn after it.
    assert _tool_results(provider.requests[2])[-1].content.value == (
        "mcp__late__search mcp__late__wipe"
    )
    assert _mcp_names(provider.requests[2]) == [
        "mcp__late__search",
        "mcp__late__wipe",
    ]
    found, removed = _tool_results(provider.requests[3])[-2:]
    assert (found.is_error, found.content.value) == (False, "pipy guide\npipy faq")
    assert removed.content.value == "unregistered"
    # Unregistered: the tools are gone from the next request, a call to one
    # fails, and the server process was told to end.
    assert _mcp_names(provider.requests[3]) == []
    assert _tool_results(provider.requests[4])[-1].is_error
    _wait_until(lambda: "stdin-closed" in _events(record))
    assert "MCP servers need attention" not in errors


def test_a_server_registered_during_the_session_activates_tool_search(
    tmp_path: Path, monkeypatch
) -> None:
    # EXT-06 with SES-11: what the new server's exposure needs is activated
    # when the next turn starts.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    workspace = tmp_path / "work"
    entry, _record = _server(tmp_path / "servers", "late")
    _plugin(workspace, later={"late": {**entry, "exposure": "deferred"}})
    provider = _Provider(
        [
            _result(tool_calls=(_call("c1", "add_server", name="late"),)),
            _result(final_text="done"),
        ]
    )

    status, errors = _run(workspace, provider)

    assert status is HarnessStatus.SUCCEEDED, errors
    assert "tool_search" not in _tool_names(provider.requests[0])
    assert "tool_search" in _tool_names(provider.requests[1])


def test_a_later_registered_server_that_fails_is_reported(
    tmp_path: Path, monkeypatch
) -> None:
    # Pi `reportProblems(ctx, connecting)`.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    workspace = tmp_path / "work"
    _plugin(workspace, later={"gone": {"command": str(tmp_path / "no-such-server")}})
    managers = _recorded_managers(monkeypatch)
    provider = _Provider(
        [
            _result(tool_calls=(_call("c1", "add_server", name="gone"),)),
            lambda: _wait_until(lambda: bool(managers[0]._notices)),
            _result(final_text="one"),
            _result(final_text="two"),
        ]
    )

    status, errors = _run(workspace, provider, "go\nagain\n")

    assert status is HarnessStatus.SUCCEEDED, errors
    assert errors.count("MCP servers need attention:\n  gone: failed: ") == 1, errors


@pytest.mark.parametrize("name", ["my-docs", "my_docs"])
def test_the_mcp_json_server_is_preferred_over_a_registered_one(
    name: str, tmp_path: Path
) -> None:
    # EXT-09, Pi "prefers the mcp.json server over a registered %s".
    workspace = tmp_path / "work"
    configured, configured_record = _server(tmp_path / "servers", "configured")
    registered, registered_record = _server(tmp_path / "servers", "registered")
    workspace.mkdir()
    config = workspace / "mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {"my-docs": {**configured, "exposure": "direct"}}})
    )
    _plugin(workspace, servers={name: {**registered, "exposure": "direct"}})
    provider = _Provider([_result(final_text="done")])

    status, errors = _run(
        workspace, provider, "/mcp\ngo\n", settings=_settings(workspace)
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    assert _mcp_names(provider.requests[0]) == [
        "mcp__my_docs__search",
        "mcp__my_docs__wipe",
    ]
    assert configured_record.exists() and not registered_record.exists()
    # `/mcp` lists the overridden registration after the servers.
    assert (
        "my-docs: connected, 2 tools (direct)\n"
        f'overridden: "{name}" registered by {_PLUGIN} is overridden by '
        f'"my-docs" in {config}'
    ) in errors


def test_mcp_lists_a_registered_server_like_a_configured_one(tmp_path: Path) -> None:
    # EXT-09, SLH-02.
    workspace = tmp_path / "work"
    workspace.mkdir()
    entry, _record = _server(tmp_path / "servers", "plugin")
    _plugin(workspace, servers={"plugin": entry, "off": {**entry, "enabled": False}})

    status, errors = _run(
        workspace, _Provider([]), "/mcp\n", settings=_settings(workspace)
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    assert "plugin: connected, 2 tools (codemode)\noff: disabled (codemode)" in errors


def test_with_mcp_off_a_registered_server_does_not_connect(tmp_path: Path) -> None:
    # EXP-11 for registered servers: nothing connects them.
    workspace = tmp_path / "work"
    workspace.mkdir()
    entry, record = _server(tmp_path / "servers", "plugin")
    later, later_record = _server(tmp_path / "servers", "late")
    _plugin(
        workspace,
        servers={"plugin": {**entry, "exposure": "direct"}},
        later={"late": {**later, "exposure": "direct"}},
    )
    provider = _Provider(
        [
            _result(tool_calls=(_call("c1", "add_server", name="late"),)),
            _result(final_text="done"),
        ]
    )

    status, errors = _run(
        workspace,
        provider,
        settings=_settings(workspace),
        resource_options=RuntimeResourceOptions(
            no_mcp=True, extension_paths=(workspace / ".pipy" / "extensions",)
        ),
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    assert _tool_results(provider.requests[1])[-1].content.value == "registered"
    assert [_mcp_names(request) for request in provider.requests] == [[], []]
    assert not record.exists() and not later_record.exists()


def test_reload_takes_the_registrations_of_the_extensions_it_loads(
    tmp_path: Path, monkeypatch
) -> None:
    # EXT-06, SES-20: registrations are not kept; the extensions that are
    # loaded again register again.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    workspace = tmp_path / "work"
    old, old_record = _server(tmp_path / "servers", "old")
    new, new_record = _server(tmp_path / "servers", "new")
    directory = _plugin(workspace, servers={"old": {**old, "exposure": "direct"}})
    provider = _Provider([_result(final_text="one"), _result(final_text="two")])

    def edit() -> None:
        (directory / "servers.json").write_text(
            json.dumps({"new": {**new, "exposure": "direct"}})
        )

    status, errors = _run_steps(
        workspace, provider, _Steps("one", edit, "/reload", "two")
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    assert _mcp_names(provider.requests[0]) == ["mcp__old__search", "mcp__old__wipe"]
    assert _mcp_names(provider.requests[1]) == ["mcp__new__search", "mcp__new__wipe"]
    assert "stdin-closed" in _events(old_record)
    assert "stdin-closed" in _events(new_record)


def test_a_reload_closes_the_registry_of_the_extensions_it_replaced(
    tmp_path: Path,
) -> None:
    """An extension of the replaced generation that still has its `api` is
    refused, and what it registered is gone with its generation."""

    loads = _Loads(tmp_path)
    old, new = McpServerRegistry(), McpServerRegistry()
    current = [old]
    session = McpSession(loads, tools=loads.tools, notify=loads.notify)
    session.follow_extensions(lambda: current[0])
    off = {"command": sys.executable, "enabled": False}

    def names() -> list[str]:
        return [status.entry.name for status in session.servers.manager.servers()]

    try:
        old.register(RegisteredMcpServer("before", off, _A))
        session.start()
        assert names() == ["docs", "before"]
        # A later change reaches the manager without the session thread.
        old.register(RegisteredMcpServer("later", off, _A))
        _wait_until(lambda: names() == ["docs", "before", "later"])

        with session.reloading() as accept:
            current[0] = new
            new.load([RegisteredMcpServer("after", off, _B)])
            accept()

        assert names() == ["docs", "after"]
        with pytest.raises(McpServerRegistrationError, match="loaded again"):
            old.register(RegisteredMcpServer("stale", off, _A))
        new.unregister("after", _B)
        _wait_until(lambda: names() == ["docs"])

        # A reload that is refused keeps the registry it followed.
        with session.reloading():
            pass
        new.register(RegisteredMcpServer("kept", off, _B))
        _wait_until(lambda: names() == ["docs", "kept"])
    finally:
        session.close()
        for loaded in loads.loaded:
            loaded.manager.close()
    # After the session closed nothing follows the registry.
    new.register(RegisteredMcpServer("ignored", off, _B))
    assert session.servers.manager.servers() == ()
