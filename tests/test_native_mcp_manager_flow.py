"""Tests for the flow of the ``/mcp`` manager (MCP parity plan S34, rows
MGR-02, MGR-04, MGR-05 and MGR-07 to MGR-09): what a choice does.

The flow runs over a manager with in-memory servers, and its sign-in over
the fake OAuth server. The user interface is a double that keeps the screen
it was told to show; the real view is tested in
``test_native_mcp_manager_view.py`` and the whole in a terminal in
``test_native_mcp_manager_pty.py``.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from test_native_mcp_connection import _NO_ANSWER, _Server, _wait_until
from test_native_mcp_manager import _Session, _tool, sessions  # noqa: F401
from test_native_mcp_manager_oauth import Servers, oauth, server  # noqa: F401

from pipy_harness.native.mcp.config import McpServerEntry
from pipy_harness.native.mcp.manager import SIGN_IN_CANCELLED, McpManager
from pipy_harness.native.mcp.manager_flow import (
    CONNECTING_TEXT,
    CONTACTING_TEXT,
    McpBackgroundActions,
    McpManagerFlow,
)
from pipy_harness.native.mcp.manager_menus import McpMenu

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_oauth import FakeBrowser, Reply  # noqa: E402

_GLOBAL = "/home/mcp.json"
_PROJECT = "/work/.pipy/mcp.json"


class _Ui:
    """The screen the flow showed last (``McpUi``), and the user at it."""

    def __init__(self) -> None:
        self.screen: tuple[Any, ...] = ("nothing",)
        self.shown: list[str] = []
        self.refreshes = 0
        self.closed = False

    def menu(
        self,
        build: Callable[[], McpMenu],
        on_choice: Callable[[str | None], None],
        *,
        live: bool = False,
    ) -> None:
        self.screen = ("menu", build, on_choice, live)
        self.shown.append(build().title)

    def status(
        self, title: str, message: str, on_cancel: Callable[[], None] | None = None
    ) -> None:
        self.screen = ("status", title, message, on_cancel)
        self.shown.append(message)

    def redirect_url(
        self,
        title: str,
        authorization_url: str,
        on_answer: Callable[[str | None], None],
    ) -> None:
        self.screen = ("redirect", title, authorization_url, on_answer)
        self.shown.append("redirect")

    def refresh(self) -> None:
        self.refreshes += 1

    def close(self) -> None:
        self.closed = True

    # -- the user ------------------------------------------------------------

    @property
    def current(self) -> McpMenu:
        """The menu on the screen, built from the servers as they are."""

        assert self.screen[0] == "menu", self.screen
        menu: McpMenu = self.screen[1]()
        return menu

    @property
    def live(self) -> bool:
        return bool(self.screen[3])

    def choose(self, value: str | None) -> None:
        if value is not None:
            assert value in [item.value for item in self.current.items]
        self.screen[2](value)

    def description(self, value: str) -> str | None:
        return next(i.description for i in self.current.items if i.value == value)

    @property
    def status_text(self) -> str:
        assert self.screen[0] == "status", self.screen
        return str(self.screen[2])

    def cancel_status(self) -> None:
        assert self.screen[0] == "status", self.screen
        self.screen[3]()


class _Flow:
    """A flow with its user interface and what it was given."""

    def __init__(self, manager: McpManager, tmp_path: Path) -> None:
        self.ui = _Ui()
        self.discovery_calls = 0
        self.opened: list[str] = []
        self.open_url: Callable[[str], object] = self.opened.append
        self.actions = McpBackgroundActions(manager)
        self.flow = McpManagerFlow(
            self.ui,
            manager,
            actions=self.actions,
            config_home=tmp_path / "home",
            ensure_discovery=self._ensure_discovery,
            open_url=lambda url: self.open_url(url),
        )

    def _ensure_discovery(self) -> None:
        self.discovery_calls += 1

    def poll_until(self, condition: Callable[[], bool]) -> None:
        def polled() -> bool:
            self.flow.poll()
            return condition()

        _wait_until(polled)


def _global(name: str, **config: Any) -> McpServerEntry:
    return McpServerEntry(
        name=name,
        config={"command": "unused", **config},  # type: ignore[typeddict-item]
        source=_GLOBAL,
        scope="global",
    )


def _no_action_threads() -> bool:
    return not [
        thread
        for thread in threading.enumerate()
        if thread.name in ("mcp-manager-action", "mcp-sign-in")
    ]


# -- the screens (MGR-02) ------------------------------------------------------


def test_the_manager_opens_at_once_and_follows_the_servers(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    release = threading.Event()

    def initialize(message: Any) -> object:
        release.wait()
        return slow._default(message)

    slow = _Server(tools=[_tool("search")], handlers={"initialize": initialize})
    session = _Session(tmp_path, {"slow": slow}, errors=["bad file"])
    sessions.append(session)
    session.manager.start()
    made = _Flow(session.manager, tmp_path)
    ui = made.ui

    # The servers are still connecting; the list is there all the same.
    made.flow.manage()

    assert (ui.current.title, ui.live) == ("MCP servers", True)
    assert ui.description("slow") == "connecting… · codemode · test"
    assert ui.current.error == "config: bad file"
    # Every poll has the view build its live menu again.
    made.flow.poll()
    assert ui.refreshes == 1
    release.set()
    _wait_until(
        lambda: ui.description("slow") == "connected · 1 tool · codemode · test"
    )
    ui.choose(None)
    assert ui.closed


def test_a_server_opens_its_menu_and_cancel_goes_back_to_the_list(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    session = _Session(tmp_path, {"docs": _Server(tools=[_tool("search")])})
    sessions.append(session)
    session.start()
    made = _Flow(session.manager, tmp_path)
    ui = made.ui
    made.flow.manage()

    ui.choose("docs")

    assert (ui.current.title, ui.live) == ("MCP server docs", True)
    ui.choose(None)
    assert ui.current.title == "MCP servers"
    assert not ui.closed
    assert made.discovery_calls == 0


def test_tools_are_shown_until_a_key_and_then_the_server_again(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    # MGR-06.
    docs = _Server(tools=[_tool("search", description="Search.\nMore.")])
    session = _Session(tmp_path, {"docs": docs})
    sessions.append(session)
    session.start()
    made = _Flow(session.manager, tmp_path)
    ui = made.ui
    made.flow.manage()
    ui.choose("docs")

    ui.choose("tools")

    assert (ui.current.title, ui.live) == ("Tools of docs", False)
    assert ui.description("search") == "Search."
    ui.choose("search")
    assert ui.current.title == "MCP server docs"
    ui.choose("tools")
    ui.choose(None)
    assert ui.current.title == "MCP server docs"


# -- changes that run in the background (MGR-04, MGR-05, MGR-08) ---------------


def test_disabling_and_enabling_run_in_the_background(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    first = _Server(tools=[_tool("search")])
    session = _Session(tmp_path, {"docs": first}, [_global("docs")])
    sessions.append(session)
    session.start()
    made = _Flow(session.manager, tmp_path)
    ui = made.ui
    made.flow.manage()
    ui.choose("docs")

    ui.choose("disable")

    # The server's menu is back at once and follows the change.
    assert ui.current.title == "MCP server docs"
    made.poll_until(lambda: [i.value for i in ui.current.items] == ["enable"])
    assert [patch for _, patch in session.saves] == [{"enabled": False}]
    assert ui.current.error is None

    release = threading.Event()

    def initialize(message: Any) -> object:
        release.wait()
        return second._default(message)

    second = _Server(tools=[_tool("search")], handlers={"initialize": initialize})
    session.servers["docs"] = second
    calls = made.discovery_calls
    ui.choose("enable")

    # The connect has not ended, and the manager is usable: back to the
    # list, which shows the server connecting.
    _wait_until(lambda: "initialize" in second.methods)
    ui.choose(None)
    assert ui.description("docs") == "connecting… · codemode · global"
    release.set()
    made.poll_until(
        lambda: ui.description("docs") == "connected · 1 tool · codemode · global"
    )
    # The end of the change activates what the server's exposure needs.
    made.poll_until(lambda: made.discovery_calls > calls + 1)
    assert [patch for _, patch in session.saves][-1] == {"enabled": True}


def test_a_change_for_the_project_is_saved_as_an_override(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    # MGR-05.
    session = _Session(
        tmp_path,
        {"docs": _Server(tools=[_tool("search")])},
        [_global("docs")],
        project_config=_PROJECT,
    )
    sessions.append(session)
    session.start()
    made = _Flow(session.manager, tmp_path)
    ui = made.ui
    made.flow.manage()
    ui.choose("docs")
    assert ui.description("disable") == "saved to the global mcp.json"

    ui.choose("disable-project")

    made.poll_until(lambda: [i.value for i in ui.current.items] == ["enable"])
    entry, patch = session.saves[-1]
    assert (entry.override, patch) == (_PROJECT, {"enabled": False})
    # Later changes go to the override, and there is no second one to make.
    assert ui.description("enable") == "saved to the project mcp.json"
    assert f"project override: {_PROJECT}" in (ui.current.details or "")
    session.servers["docs"] = _Server(tools=[_tool("search")])
    ui.choose("enable")
    made.poll_until(lambda: "disable" in [i.value for i in ui.current.items])
    entry, patch = session.saves[-1]
    assert (entry.override, patch) == (_PROJECT, {"enabled": True})
    _wait_until(_no_action_threads)


def test_a_change_that_cannot_be_saved_says_so_in_the_servers_menu(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    session = _Session(
        tmp_path, {"docs": _Server(tools=[_tool("search")])}, [_global("docs")]
    )
    sessions.append(session)
    session.start()
    made = _Flow(session.manager, tmp_path)
    ui = made.ui
    made.flow.manage()
    ui.choose("docs")
    session.save_error = OSError("read-only file system")

    ui.choose("disable")

    failure = f"Could not update {_GLOBAL}: read-only file system"
    made.poll_until(lambda: ui.current.error == failure)
    assert "disable" in [item.value for item in ui.current.items]
    # The next action forgets it.
    session.save_error = None
    session.servers["docs"] = _Server(tools=[_tool("search")])
    ui.choose("reconnect")
    made.poll_until(_no_action_threads)
    assert ui.current.error is None


def test_reconnect_runs_in_the_background_and_its_error_is_the_servers(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    session = _Session(tmp_path, {"docs": _Server(tools=[_tool("search")])})
    sessions.append(session)
    session.start()
    made = _Flow(session.manager, tmp_path)
    ui = made.ui
    made.flow.manage()
    ui.choose("docs")
    hanging = _Server(handlers={"initialize": lambda message: _NO_ANSWER})
    session.servers["docs"] = hanging

    ui.choose("reconnect")

    assert ui.current.title == "MCP server docs"
    _wait_until(lambda: "initialize" in hanging.methods)
    made.poll_until(lambda: "State: connecting…" in (ui.current.details or ""))
    assert session.opened == ["docs", "docs"]
    # What a reconnect returns is not a message: the state says it.
    session.manager.set_enabled("docs", False)
    made.poll_until(_no_action_threads)
    assert ui.current.error is None


def test_of_two_changes_of_a_server_the_later_one_has_the_last_word(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    session = _Session(tmp_path, {}, [_global("docs", enabled=False)])
    sessions.append(session)
    actions = McpBackgroundActions(session.manager)
    first_may_end = threading.Event()
    first_started = threading.Event()

    def first() -> str:
        first_started.set()
        first_may_end.wait()
        return "the first failed"

    actions.run("docs", first)
    assert first_started.wait(10)
    assert not actions.take_finished()
    actions.run("docs", lambda: None)
    _wait_until(actions.take_finished)
    first_may_end.set()
    _wait_until(actions.take_finished)
    assert session.manager.servers()[0].message is None
    # An error a change raised is its message.

    def fails() -> str | None:
        raise OSError("disk full")

    actions.run("docs", fails)
    _wait_until(actions.take_finished)
    assert session.manager.servers()[0].message == "disk full"
    assert not actions.take_finished()


# -- exposure (MGR-07) ---------------------------------------------------------


def test_choosing_an_exposure_saves_it_and_registers_the_tools_again(
    tmp_path: Path,
    sessions: list[_Session],  # noqa: F811
) -> None:
    session = _Session(
        tmp_path, {"docs": _Server(tools=[_tool("search")])}, [_global("docs")]
    )
    sessions.append(session)
    session.start()
    made = _Flow(session.manager, tmp_path)
    ui = made.ui
    made.flow.manage()
    ui.choose("docs")
    assert session.declared() == ("read",)

    ui.choose("exposure")
    assert (ui.current.title, ui.current.selected) == ("Exposure of docs", "codemode")
    ui.choose("direct")

    assert ui.current.title == "MCP server docs"
    assert ui.description("exposure") == "direct"
    assert [patch for _, patch in session.saves] == [{"exposure": "direct"}]
    assert session.declared() == ("read", "mcp__docs__search")
    assert made.discovery_calls == 1
    # The same choice, and no choice, save nothing.
    ui.choose("exposure")
    ui.choose("direct")
    ui.choose("exposure")
    ui.choose(None)
    assert len(session.saves) == 1
    assert ui.current.title == "MCP server docs"
    # A choice that cannot be saved is the server's message.
    session.save_error = OSError("denied")
    ui.choose("exposure")
    ui.choose("deferred")
    assert ui.current.error == f"Could not update {_GLOBAL}: denied"
    assert ui.description("exposure") == "direct"


# -- sign-in and sign-out (MGR-09) ---------------------------------------------


def test_sign_in_shows_each_step_and_ends_at_the_servers_menu(
    tmp_path: Path,
    oauth: Servers,  # noqa: F811
) -> None:
    oauth.start()
    made = _Flow(oauth.manager, tmp_path)
    ui = made.ui
    session_thread = threading.current_thread()
    made.flow.manage()
    ui.choose("issues")
    assert [item.value for item in ui.current.items][0] == "signin"

    ui.choose("signin")

    assert ui.screen[:3] == ("status", "Sign in to issues", CONTACTING_TEXT)
    made.poll_until(lambda: ui.screen[0] == "redirect")
    # The browser was opened by the session thread, with the URL shown.
    assert ui.screen[1:3] == ("Sign in to issues", made.opened[0])

    def approve() -> None:
        assert threading.current_thread() is not session_thread
        FakeBrowser().open(made.opened[0])

    browser = threading.Thread(target=approve)
    browser.start()
    made.poll_until(lambda: ui.screen[0] == "menu")
    browser.join()
    assert ui.shown[-4:] == [
        CONTACTING_TEXT,
        "redirect",
        CONNECTING_TEXT,
        "MCP server issues",
    ]
    assert ui.current.error is None
    assert "State: connected · 1 tool" in (ui.current.details or "")
    assert [i.value for i in ui.current.items][:3] == ["tools", "reconnect", "signout"]
    assert made.discovery_calls == 1

    ui.choose("signout")

    assert oauth.credentials().tokens("issues", oauth.server.url) is None
    made.poll_until(lambda: "State: needs sign-in" in (ui.current.details or ""))


def test_a_pasted_redirect_url_finishes_the_sign_in(
    tmp_path: Path,
    oauth: Servers,  # noqa: F811
) -> None:
    # The browser runs on another machine and cannot reach the loopback port.
    oauth.start()
    made = _Flow(oauth.manager, tmp_path)
    ui = made.ui
    made.flow.manage()
    ui.choose("issues")
    ui.choose("signin")
    made.poll_until(lambda: ui.screen[0] == "redirect")

    # Where the authorization server sends the browser: the redirect URL.
    ui.screen[3](FakeBrowser().get(made.opened[0]).headers["location"])

    made.poll_until(lambda: ui.screen[0] == "menu")
    assert ui.shown[-2:] == [CONNECTING_TEXT, "MCP server issues"]
    assert oauth.state() == "connected"


def test_cancel_stops_a_sign_in_that_waits_for_the_authorization_server(
    tmp_path: Path,
    oauth: Servers,  # noqa: F811
) -> None:
    oauth.start()
    path = "/.well-known/oauth-authorization-server"
    oauth.server.route(path, Reply(hang=True))
    made = _Flow(oauth.manager, tmp_path)
    ui = made.ui
    made.flow.manage()
    ui.choose("issues")
    ui.choose("signin")
    _wait_until(lambda: path in oauth.server.paths)
    assert ui.status_text == CONTACTING_TEXT

    ui.cancel_status()

    assert ui.current.error == SIGN_IN_CANCELLED
    assert made.opened == []
    # The sign-in itself ends too.
    _wait_until(_no_action_threads)


def test_cancel_at_the_sign_in_screen_and_while_connecting_stops_the_sign_in(
    tmp_path: Path,
    oauth: Servers,  # noqa: F811
) -> None:
    oauth.start()
    made = _Flow(oauth.manager, tmp_path)
    ui = made.ui
    made.flow.manage()
    ui.choose("issues")
    ui.choose("signin")
    made.poll_until(lambda: ui.screen[0] == "redirect")

    ui.screen[3](None)

    # The server's menu is back at once.
    assert ui.current.error == SIGN_IN_CANCELLED
    _wait_until(_no_action_threads)
    assert oauth.state() == "needs-auth"
    # Again, cancelled once the token request hangs.
    oauth.server.route("/token", Reply(hang=True))
    ui.choose("signin")
    made.poll_until(lambda: ui.screen[0] == "redirect")
    browser = threading.Thread(target=lambda: FakeBrowser().open(made.opened[-1]))
    browser.start()
    made.poll_until(lambda: ui.screen[0] == "status")
    assert ui.status_text == CONNECTING_TEXT
    ui.cancel_status()
    assert ui.current.error == SIGN_IN_CANCELLED
    browser.join()
    _wait_until(_no_action_threads)
    assert oauth.credentials().tokens("issues", oauth.server.url) is None


def test_cancel_ends_the_wait_for_a_server_that_does_not_connect_again(
    tmp_path: Path,
    oauth: Servers,  # noqa: F811
) -> None:
    # The browser came back and the tokens are stored; the server then never
    # answers the connect that follows.
    oauth.start()
    made = _Flow(oauth.manager, tmp_path)
    ui = made.ui
    made.flow.manage()
    ui.choose("issues")
    ui.choose("signin")
    made.poll_until(lambda: ui.screen[0] == "redirect")
    requests = len(oauth.server.requests)
    oauth.server.route("/mcp", Reply(hang=True))
    browser = threading.Thread(target=lambda: FakeBrowser().open(made.opened[0]))
    browser.start()
    made.poll_until(lambda: ui.screen[0] == "status")
    browser.join()
    _wait_until(lambda: "/mcp" in [r.path for r in oauth.server.requests[requests:]])
    assert ui.status_text == CONNECTING_TEXT

    ui.cancel_status()

    # Nobody waits any more, and a later end of the sign-in shows nothing.
    assert ui.current.title == "MCP server issues"
    assert ui.current.error == SIGN_IN_CANCELLED
    made.flow.poll()
    assert ui.screen[0] == "menu"
    # The sign-in itself was done.
    assert oauth.credentials().tokens("issues", oauth.server.url) is not None


def test_a_failed_sign_in_is_the_servers_message(
    tmp_path: Path,
    oauth: Servers,  # noqa: F811
) -> None:
    oauth.start()
    oauth.server.response_iss = "https://attacker.example"
    made = _Flow(oauth.manager, tmp_path)
    made.open_url = lambda url: threading.Thread(
        target=lambda: FakeBrowser().open(url)
    ).start()
    ui = made.ui
    made.flow.manage()
    ui.choose("issues")

    ui.choose("signin")

    made.poll_until(lambda: ui.screen[0] == "menu")
    assert (ui.current.error or "").startswith("Sign-in failed: OAuth issuer mismatch")
    assert made.discovery_calls == 1


def test_closing_the_view_cancels_the_sign_in(
    tmp_path: Path,
    oauth: Servers,  # noqa: F811
) -> None:
    oauth.start()
    made = _Flow(oauth.manager, tmp_path)
    ui = made.ui
    outcomes: list[str | None] = []
    made.flow.sign_in("issues", outcomes.append)
    made.poll_until(lambda: ui.screen[0] == "redirect")

    made.flow.stop()

    _wait_until(_no_action_threads)
    made.flow.poll()
    # Nobody is told: the view is gone.
    assert outcomes == []
    assert ui.screen[0] == "redirect"
    assert oauth.state() == "needs-auth"


def test_a_sign_out_that_fails_is_the_servers_message(
    tmp_path: Path,
    oauth: Servers,  # noqa: F811
) -> None:
    oauth.start()
    oauth.sign_in_elsewhere()
    assert oauth.manager.reconnect("issues") is None
    made = _Flow(oauth.manager, tmp_path)
    ui = made.ui
    made.flow.manage()
    ui.choose("issues")
    path = oauth.home / "mcp-auth.json"
    path.write_text("not json")

    ui.choose("signout")

    assert (ui.current.error or "").startswith(
        'Could not sign out of MCP server "issues": '
    )
    assert oauth.state() == "connected"
