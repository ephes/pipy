"""Tests for the menus of the ``/mcp`` manager (MCP parity plan S34, rows
MGR-01 and MGR-03 to MGR-07): what each menu holds for each state of a
server. The view that draws them is tested in
``test_native_mcp_manager_view.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_native_mcp_manager import _entry, _Session, sessions  # noqa: F401

from pipy_harness.native.mcp.config import McpConfigScope, McpServerEntry
from pipy_harness.native.mcp.connection import ConnectionView
from pipy_harness.native.mcp.manager import McpServerStatus
from pipy_harness.native.mcp.manager_menus import (
    EXPOSURE_DESCRIPTIONS,
    McpMenu,
    McpMenuItem,
    attention_rank,
    describe_transport,
    exposure_menu,
    server_menu,
    servers_menu,
    tools_menu,
)

_PROJECT = "/work/.pipy/mcp.json"
_GLOBAL = "/home/.pipy/mcp.json"


def _status(
    name: str,
    state: str | None,
    *,
    scope: McpConfigScope | None = "global",
    source: str = _GLOBAL,
    override: str | None = None,
    error: str | None = None,
    message: str | None = None,
    tools: tuple[dict[str, Any], ...] = (),
    oauth_url: str | None = None,
    **config: Any,
) -> McpServerStatus:
    entry = McpServerEntry(
        name=name,
        config={"command": "run", **config},  # type: ignore[typeddict-item]
        source=source,
        scope=scope,
        override=override,
    )
    view = (
        None
        if state is None
        else ConnectionView(
            name=name,
            state=state,  # type: ignore[arg-type]
            error=error,
            tools=tools,
            instructions=None,
            oauth_url=oauth_url,
        )
    )
    return McpServerStatus(entry, config.get("enabled") is not False, view, message)


def _values(menu: McpMenu) -> list[str]:
    return [item.value for item in menu.items]


def _item(menu: McpMenu, value: str) -> McpMenuItem:
    return next(item for item in menu.items if item.value == value)


# -- the list of servers (MGR-01, MGR-02) -----------------------------------


def test_servers_that_need_the_user_come_first_then_by_name(tmp_path: Path) -> None:
    servers = [
        _status("off", None, enabled=False),
        _status("zeta", "connected"),
        _status("Alpha", "connected"),
        _status("beta", "connected"),
        _status("slow", "connecting"),
        _status("new", None),
        _status("gone", "disconnected"),
        _status("bad", "failed", error="boom"),
        _status("auth", "needs-auth"),
    ]
    menu = servers_menu(servers, (), tmp_path)
    assert _values(menu) == [
        "auth",
        "bad",
        "gone",
        "new",
        "slow",
        "Alpha",
        "beta",
        "zeta",
        "off",
    ]
    assert [attention_rank(status) for status in servers] == [5, 4, 4, 4, 3, 3, 2, 1, 0]
    assert (menu.title, menu.confirm_label, menu.cancel_label) == (
        "MCP servers",
        "manage",
        "close",
    )
    assert menu.selected is None
    assert menu.error is None


def test_a_server_is_listed_with_state_exposure_and_source(tmp_path: Path) -> None:
    tools = ({"name": "a"}, {"name": "b"})
    menu = servers_menu(
        [
            _status("user", "connected", tools=tools),
            _status(
                "proj", "failed", scope="project", error="no\nmore", exposure="direct"
            ),
            _status("ext", "needs-auth", scope="extension", source="/ext/x.py"),
            _status(
                "both", None, override=_PROJECT, enabled=False, exposure="deferred"
            ),
            _status("bare", "disconnected", scope=None, source="somewhere"),
        ],
        (),
        tmp_path,
    )
    assert {item.label: item.description for item in menu.items} == {
        "user": "connected · 2 tools · codemode · global",
        "proj": "failed: no · direct · project",
        "ext": "needs sign-in · codemode · extension",
        "both": "disabled · deferred · global, project override",
        "bare": "disconnected · codemode · somewhere",
    }


def test_config_errors_are_shown_above_the_list(tmp_path: Path) -> None:
    menu = servers_menu(
        [_status("docs", "connected")], ("a: bad", "b: worse"), tmp_path
    )
    assert menu.error == "config: a: bad\nconfig: b: worse"


def test_without_servers_the_list_says_where_to_add_them(tmp_path: Path) -> None:
    menu = servers_menu((), (), tmp_path)
    assert menu.items == ()
    assert menu.empty == (
        f"No MCP servers configured. Add them to {tmp_path / 'mcp.json'}"
        " or .pipy/mcp.json."
    )


# -- one server (MGR-03, MGR-04, MGR-05) ------------------------------------


def test_the_actions_depend_on_the_state() -> None:
    def actions(state: str | None, **fields: Any) -> list[str]:
        return _values(server_menu([_status("docs", state, **fields)], "docs", None))

    assert actions("needs-auth") == ["signin", "reconnect", "exposure", "disable"]
    assert actions("connected") == ["tools", "reconnect", "exposure", "disable"]
    assert actions("connected", oauth_url="https://mcp.example/") == [
        "tools",
        "reconnect",
        "signout",
        "exposure",
        "disable",
    ]
    assert actions("failed") == ["reconnect", "exposure", "disable"]
    assert actions("disconnected") == ["reconnect", "exposure", "disable"]
    assert actions("connecting") == ["exposure", "disable"]
    assert actions(None) == ["exposure", "disable"]
    assert actions(None, enabled=False) == ["enable"]
    # A server that needs a sign-in is not signed out of.
    assert "signout" not in actions("needs-auth", oauth_url="https://mcp.example/")


def test_each_action_says_what_it_does_and_where_it_is_saved() -> None:
    tools = ({"name": "a"},)
    menu = server_menu(
        [
            _status(
                "docs",
                "connected",
                tools=tools,
                oauth_url="https://mcp.example/",
                exposure="direct",
            )
        ],
        "docs",
        None,
    )
    assert menu.items == (
        McpMenuItem("tools", "Tools", "1 offered"),
        McpMenuItem("reconnect", "Reconnect"),
        McpMenuItem("signout", "Sign out", "deletes the stored credentials"),
        McpMenuItem("exposure", "Exposure", "direct"),
        McpMenuItem("disable", "Disable", "saved to the global mcp.json"),
    )
    assert menu.selected == "tools"
    assert (menu.confirm_label, menu.cancel_label) == ("select", "back")
    sign_in = server_menu([_status("docs", "needs-auth")], "docs", None)
    assert sign_in.items[0] == McpMenuItem("signin", "Sign in", "opens the browser")


@pytest.mark.parametrize(
    ("fields", "saved"),
    [
        ({"scope": "global"}, "saved to the global mcp.json"),
        ({"scope": "project"}, "saved to the project mcp.json"),
        ({"scope": "global", "override": _PROJECT}, "saved to the project mcp.json"),
        ({"scope": "extension"}, "for this session"),
        ({"scope": None}, "saved to mcp.json"),
    ],
)
def test_enable_and_disable_say_where_the_change_is_saved(
    fields: dict[str, Any], saved: str
) -> None:
    enabled = server_menu([_status("docs", "connected", **fields)], "docs", None)
    assert _item(enabled, "disable").description == saved
    disabled = server_menu(
        [_status("docs", None, enabled=False, **fields)], "docs", None
    )
    assert _item(disabled, "enable").description == saved


def test_a_user_level_server_can_be_changed_for_the_trusted_project_alone() -> None:
    enabled = server_menu([_status("docs", "connected")], "docs", _PROJECT)
    assert _values(enabled)[-2:] == ["disable", "disable-project"]
    assert _item(enabled, "disable-project") == McpMenuItem(
        "disable-project", "Disable in this project", "saved to the project mcp.json"
    )
    disabled = server_menu([_status("docs", None, enabled=False)], "docs", _PROJECT)
    assert disabled.items == (
        McpMenuItem("enable", "Enable", "saved to the global mcp.json"),
        McpMenuItem(
            "enable-project", "Enable in this project", "saved to the project mcp.json"
        ),
    )


@pytest.mark.parametrize(
    "fields",
    [
        # No trusted project.
        {"project_config": None},
        # The server has an override already: changes go there.
        {"project_config": _PROJECT, "override": _PROJECT},
        {"project_config": _PROJECT, "scope": "project"},
        {"project_config": _PROJECT, "scope": "extension"},
    ],
)
def test_other_servers_have_no_project_actions(fields: dict[str, Any]) -> None:
    project_config = fields["project_config"]
    states: tuple[tuple[str | None, dict[str, Any]], ...] = (
        ("connected", {}),
        (None, {"enabled": False}),
    )
    for state, config in states:
        entry_fields = {k: v for k, v in fields.items() if k != "project_config"}
        menu = server_menu(
            [_status("docs", state, **entry_fields, **config)], "docs", project_config
        )
        assert not [value for value in _values(menu) if value.endswith("-project")]


def test_a_server_shows_its_transport_source_and_state() -> None:
    stdio = server_menu(
        [_status("docs", "connected", tools=({"name": "a"},), args=["-m", "srv"])],
        "docs",
        None,
    )
    assert stdio.title == "MCP server docs"
    assert stdio.details == (
        f"run -m srv\nglobal: {_GLOBAL}\nState: connected · 1 tool"
    )
    assert stdio.error is None
    http = _status("web", "failed", error="HTTP 500\nstderr: boom", override=_PROJECT)
    http.entry.config.pop("command")  # type: ignore[misc]
    http.entry.config["url"] = "https://mcp.example/mcp"  # type: ignore[typeddict-unknown-key]
    menu = server_menu([http], "web", _PROJECT)
    assert menu.details == (
        "https://mcp.example/mcp\n"
        f"global: {_GLOBAL}\n"
        f"project override: {_PROJECT}\n"
        "State: failed"
    )
    # The whole error of a server that is not connected, stderr tail included.
    assert menu.error == "HTTP 500\nstderr: boom"
    bare = server_menu([_status("x", None, scope=None, source="src")], "x", None)
    assert bare.details == "run\nconfig: src\nState: starting"


def test_the_last_actions_error_is_shown_before_the_servers() -> None:
    status = _status("docs", "failed", error="refused", message="Could not update x")
    assert server_menu([status], "docs", None).error == "Could not update x\nrefused"
    # A connected server keeps the error of an earlier attempt to itself.
    connected = _status("docs", "connected", error="old", message="Sign-in cancelled.")
    assert server_menu([connected], "docs", None).error == "Sign-in cancelled."


def test_a_server_that_is_gone_says_so() -> None:
    menu = server_menu([_status("docs", "connected")], "other", None)
    assert menu == McpMenu(
        title="other",
        items=(),
        empty="This server is no longer configured.",
        confirm_label="",
        cancel_label="back",
    )


def test_describe_transport_is_the_url_or_the_command_line() -> None:
    assert describe_transport(_entry("docs")) == "unused"
    assert (
        describe_transport(_entry("docs", command="npx", args=["-y", "srv"]))
        == "npx -y srv"
    )
    entry = McpServerEntry(name="web", config={"url": "https://x/mcp"}, source="s")
    assert describe_transport(entry) == "https://x/mcp"


# -- tools and exposure (MGR-06, MGR-07) ------------------------------------


def test_tools_show_the_first_line_and_an_exposure_that_differs() -> None:
    tools = (
        {"name": "search", "description": "Search the docs.\nMore text."},
        {"name": "write", "description": "Write a page."},
        {"name": "bare"},
    )
    plain = tools_menu(_status("docs", "connected", tools=tools))
    assert plain.title == "Tools of docs"
    assert plain.details == f"Exposure codemode: {EXPOSURE_DESCRIPTIONS['codemode']}"
    assert plain.items == (
        McpMenuItem("search", "search", "Search the docs."),
        McpMenuItem("write", "write", "Write a page."),
        McpMenuItem("bare", "bare", ""),
    )
    assert (plain.confirm_label, plain.cancel_label) == ("back", "back")
    mixed = tools_menu(
        _status(
            "docs",
            "connected",
            tools=tools,
            exposure="deferred",
            toolExposure={"write": "direct", "bare": "hidden"},
        )
    )
    assert mixed.details == (
        f"Exposure deferred: {EXPOSURE_DESCRIPTIONS['deferred']}\n"
        "Some tools override it with toolExposure."
    )
    assert [item.description for item in mixed.items] == [
        "Search the docs.",
        "[direct] Write a page.",
        "[hidden] ",
    ]


def test_tools_of_a_hidden_server_are_unreachable_and_none_is_said() -> None:
    menu = tools_menu(_status("docs", "connected", exposure="hidden"))
    assert menu.details == "Exposure hidden: unreachable"
    assert menu.items == ()
    assert menu.empty == "The server offers no tools."


def test_exposure_offers_the_three_and_marks_the_current_one() -> None:
    menu = exposure_menu(_status("docs", "connected", exposure="deferred"))
    assert menu.title == "Exposure of docs"
    assert menu.details == f"Saved to {_GLOBAL}."
    assert menu.items == (
        McpMenuItem("codemode", "  codemode", EXPOSURE_DESCRIPTIONS["codemode"]),
        McpMenuItem("deferred", "✓ deferred", EXPOSURE_DESCRIPTIONS["deferred"]),
        McpMenuItem("direct", "  direct", EXPOSURE_DESCRIPTIONS["direct"]),
    )
    assert menu.selected == "deferred"
    assert (menu.confirm_label, menu.cancel_label) == ("save", "back")
    # ``hidden`` is not offered; a hidden server has no item marked.
    hidden = exposure_menu(_status("docs", "connected", exposure="hidden"))
    assert all(item.label.startswith("  ") for item in hidden.items)


def test_exposure_says_where_the_choice_is_saved() -> None:
    override = exposure_menu(_status("docs", "connected", override=_PROJECT))
    assert override.details == f"Saved to {_PROJECT}."
    extension = exposure_menu(
        _status("docs", "connected", scope="extension", source="/ext/x.py")
    )
    assert extension.details == (
        "Applies to this session; the server is registered by /ext/x.py."
    )


# -- the message of the last action -----------------------------------------


def test_the_manager_keeps_what_the_last_action_said(
    sessions: list[_Session],  # noqa: F811
    tmp_path: Path,
) -> None:
    session = _Session(tmp_path, {}, [_entry("docs"), _entry("other")])
    sessions.append(session)
    manager = session.manager
    assert [status.message for status in manager.servers()] == [None, None]
    manager.set_message("docs", "Could not update mcp.json: denied")
    manager.set_message("nobody", "ignored")
    assert [status.message for status in manager.servers()] == [
        "Could not update mcp.json: denied",
        None,
    ]
    manager.set_message("docs", None)
    assert [status.message for status in manager.servers()] == [None, None]
