"""Tests for the ``/mcp`` command (MCP parity plan S21, rows SLH-01 to
SLH-03, SLH-05, SLH-06; S31, row SLH-04).

The command is tested over a manager with in-memory servers, and its login
and logout over the fake OAuth server; the session level is in
``test_native_mcp_session_command.py``.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_native_mcp_connection import _NO_ANSWER, _RpcError, _Server, _wait_until
from test_native_mcp_manager import _entry, _Session, _tool
from test_native_mcp_manager_oauth import Servers, oauth, server  # noqa: F401

from pipy_harness.native.agent.tools import ToolExecutionInterruption
from pipy_harness.native.editor_state import CompletionItem
from pipy_harness.native.mcp.commands import (
    MCP_COMMAND_DESCRIPTION,
    MCP_USAGE,
    NO_OAUTH_SERVER,
    NO_SERVER_TO_RECONNECT,
    McpArgumentCompletion,
    McpCommand,
    complete_mcp_arguments,
    format_status,
    run_step_inline,
)
from pipy_harness.native.mcp.config import McpServerEntry
from pipy_harness.native.mcp.connection import ConnectionView
from pipy_harness.native.mcp.manager import McpManager, McpServerStatus
from pipy_harness.native.repl.command_menu import (
    tool_loop_command_descriptions,
    tool_loop_command_names,
)
from pipy_harness.native.repl.mcp_command import (
    MCP_SLASH_NAME,
    McpSessionCommand,
    interruptible_steps,
    mcp_session_commands,
)
from pipy_harness.native.resources import WorkspaceResources

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_oauth import FakeBrowser, Reply  # noqa: E402

_WAIT_SECONDS = 10.0


def _status(
    name: str,
    state: str | None,
    *,
    error: str | None = None,
    tools: int = 0,
    **config: Any,
) -> McpServerStatus:
    entry = McpServerEntry(
        name=name, config={"command": "unused", **config}, source="test"
    )
    view = (
        None
        if state is None
        else ConnectionView(
            name=name,
            state=state,  # type: ignore[arg-type]
            error=error,
            tools=tuple({"name": f"t{index}"} for index in range(tools)),
            instructions=None,
        )
    )
    return McpServerStatus(entry, config.get("enabled") is not False, view)


def _refuse(message: Any) -> object:
    raise _RpcError(-32000, "boom\nsecond line")


class _Ui:
    """Collects what the command reports and answers its selections."""

    def __init__(self) -> None:
        self.notices: list[tuple[str, str]] = []
        self.selections: list[tuple[str, tuple[str, ...]]] = []
        self.choice: str | None = None
        self.discovery_calls = 0

    def notify(self, level: str, message: str) -> None:
        self.notices.append((level, message))

    def select(self, title: str, options: Any) -> str | None:
        self.selections.append((title, tuple(options)))
        return self.choice

    def ensure_discovery(self) -> None:
        self.discovery_calls += 1


def _command(
    session: _Session | Servers,
    ui: _Ui,
    tmp_path: Path,
    run_step: Callable[..., bool] = run_step_inline,
    open_url: Callable[[str], object] | None = None,
) -> McpCommand:
    manager: McpManager = session.manager
    return McpCommand(
        manager,
        config_home=tmp_path / "home",
        notify=ui.notify,
        select=ui.select,
        ensure_discovery=ui.ensure_discovery,
        run_step=run_step,
        open_url=open_url,
    )


@pytest.fixture
def sessions() -> Iterator[list[_Session]]:
    opened: list[_Session] = []
    yield opened
    for session in opened:
        session.manager.close()


# -- SLH-02: the status text ---------------------------------------------------


def test_status_has_one_line_per_server_for_each_state(tmp_path: Path) -> None:
    servers = [
        _status("docs", "connected", tools=2, exposure="direct"),
        _status("one", "connected", tools=1, error="Failed to refresh tools: x"),
        _status("auth", "needs-auth", exposure="deferred"),
        _status("gone", "failed", error="spawn failed\nstderr line\n\nlast"),
        _status("dropped", "disconnected", error="Connection closed"),
        _status("slow", "connecting"),
        _status("off", None, enabled=False, exposure="hidden"),
        _status("soon", None),
        _status("shut", "closed"),
    ]

    text = format_status(servers, ["a.json: bad", "b.json: worse"], tmp_path)

    assert text.split("\n") == [
        "docs: connected, 2 tools (direct)",
        # Always "tools", and a connected server's error is not shown.
        "one: connected, 1 tools (codemode)",
        "auth: needs sign-in, run /mcp login auth (deferred)",
        "gone: failed (codemode)",
        "    spawn failed",
        "    stderr line",
        "    ",
        "    last",
        "dropped: disconnected, reconnects on next call (codemode)",
        "    Connection closed",
        "slow: connecting (codemode)",
        "off: disabled (hidden)",
        "soon: starting (codemode)",
        "shut: closed (codemode)",
        "config error: a.json: bad",
        "config error: b.json: worse",
    ]


def test_status_without_servers_says_where_to_add_them(tmp_path: Path) -> None:
    assert format_status([], [], tmp_path) == (
        f"No MCP servers configured. Add them to {tmp_path / 'mcp.json'} "
        "or .pipy/mcp.json."
    )
    assert format_status([], ["x.json: bad"], tmp_path) == "config error: x.json: bad"


def test_mcp_waits_for_the_startup_connections_and_prints_the_status(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    release = threading.Event()

    def slow_initialize(message: Any) -> object:
        release.wait(_WAIT_SECONDS)
        return slow._default(message)

    slow = _Server(tools=[_tool("search")], handlers={"initialize": slow_initialize})
    session = _Session(
        tmp_path,
        {"slow": slow, "off": _Server()},
        [_entry("slow", exposure="direct"), _entry("off", enabled=False)],
        errors=["bad file"],
    )
    sessions.append(session)
    ui = _Ui()
    command = _command(session, ui, tmp_path)
    session.manager.start()
    _wait_until(lambda: "initialize" in slow.methods)

    running = threading.Thread(target=lambda: command.run("  "), daemon=True)
    running.start()
    running.join(0.1)
    assert running.is_alive() and ui.notices == []
    release.set()
    running.join(_WAIT_SECONDS)

    assert ui.notices == [
        (
            "info",
            "slow: connected, 1 tools (direct)\n"
            "off: disabled (codemode)\n"
            "config error: bad file",
        )
    ]


# -- SLH-06: usage -------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    ["reconnect docs extra", "frobnicate", "login a b", "enable docs", "Reconnect"],
)
def test_too_many_arguments_or_an_unknown_action_prints_the_usage(
    tmp_path: Path, sessions: list[_Session], args: str
) -> None:
    session = _Session(tmp_path, {"docs": _Server()})
    sessions.append(session)
    session.start()
    ui = _Ui()

    _command(session, ui, tmp_path).run(args)

    assert ui.notices == [("warning", MCP_USAGE)]
    assert MCP_USAGE == (
        "Usage: /mcp, /mcp login [server], /mcp logout [server], "
        "/mcp reconnect [server]"
    )
    assert session.opened == ["docs"]


# -- SLH-03, SLH-05: reconnect and choosing the server -------------------------


def test_reconnect_reports_the_new_state(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _Session(tmp_path, {"docs": _Server(tools=[_tool("search")])})
    sessions.append(session)
    session.start()
    session.servers["docs"] = _Server(tools=[_tool("search"), _tool("fetch")])
    ui = _Ui()

    _command(session, ui, tmp_path).run("reconnect docs")

    assert ui.notices == [
        ("info", 'Reconnected to MCP server "docs" (connected · 2 tools).')
    ]
    assert ui.discovery_calls == 1
    assert session.opened == ["docs", "docs"]
    assert session.mcp_names() == ("mcp__docs__search", "mcp__docs__fetch")


def test_reconnect_reports_the_error_of_a_failed_connect(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _Session(tmp_path, {"docs": _Server(tools=[_tool("search")])})
    sessions.append(session)
    session.start()
    session.servers["docs"] = _Server(handlers={"initialize": _refuse})
    ui = _Ui()

    _command(session, ui, tmp_path).run("reconnect")

    assert ui.notices == [
        ("error", 'MCP server "docs" failed to connect: boom\nsecond line')
    ]
    assert ui.discovery_calls == 0


def test_an_unknown_or_ineligible_name_is_an_error(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _Session(
        tmp_path,
        {"docs": _Server(), "off": _Server()},
        [_entry("docs"), _entry("off", enabled=False)],
    )
    sessions.append(session)
    session.start()
    ui = _Ui()
    command = _command(session, ui, tmp_path)

    command.run("reconnect nope")
    command.run("reconnect off")
    command.run("login nope")
    command.run("login docs")
    command.run("logout docs")

    assert ui.notices == [
        ("error", 'No MCP server named "nope".'),
        ("error", NO_SERVER_TO_RECONNECT),
        ("error", 'No MCP server named "nope".'),
        ("error", NO_OAUTH_SERVER),
        ("error", NO_OAUTH_SERVER),
    ]
    assert session.opened == ["docs"]


def test_without_an_eligible_server_the_message_is_a_notice(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _Session(tmp_path, {"off": _Server()}, [_entry("off", enabled=False)])
    sessions.append(session)
    session.start()
    ui = _Ui()
    command = _command(session, ui, tmp_path)

    command.run("reconnect")
    command.run("login")
    command.run("logout")

    assert ui.notices == [
        ("info", "No enabled MCP server to reconnect."),
        (
            "info",
            "No enabled MCP server uses OAuth. "
            "Only HTTP servers without an Authorization header do.",
        ),
        ("info", NO_OAUTH_SERVER),
    ]
    assert ui.selections == []


def test_the_one_failed_server_is_preferred(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _Session(
        tmp_path,
        {"good": _Server(), "bad": _Server(handlers={"initialize": _refuse})},
    )
    sessions.append(session)
    session.start()
    session.servers["bad"] = _Server(tools=[_tool("search")])
    ui = _Ui()

    _command(session, ui, tmp_path).run("reconnect")

    assert ui.selections == []
    assert ui.notices == [
        ("info", 'Reconnected to MCP server "bad" (connected · 1 tool).')
    ]
    assert session.opened.count("good") == 1


def test_several_candidates_are_offered_for_selection(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _Session(
        tmp_path,
        {"a": _Server(), "b": _Server(), "off": _Server()},
        [_entry("a"), _entry("b"), _entry("off", enabled=False)],
    )
    sessions.append(session)
    session.start()
    ui = _Ui()
    command = _command(session, ui, tmp_path)

    command.run("reconnect")  # nothing chosen: nothing happens
    assert ui.selections == [("MCP server", ("a", "b"))]
    assert ui.notices == [] and sorted(session.opened) == ["a", "b"]

    ui.choice = "b"
    session.servers["b"] = _Server()
    command.run("reconnect")

    assert ui.notices == [
        ("info", 'Reconnected to MCP server "b" (connected · 0 tools).')
    ]
    assert sorted(session.opened) == ["a", "b", "b"]


def test_a_reconnect_the_user_stops_waiting_for_goes_on(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    release = threading.Event()

    def slow_initialize(message: Any) -> object:
        release.wait(_WAIT_SECONDS)
        return second._default(message)

    second = _Server(tools=[_tool("fetch")], handlers={"initialize": slow_initialize})
    session = _Session(tmp_path, {"docs": _Server(tools=[_tool("search")])})
    sessions.append(session)
    session.start()
    session.servers["docs"] = second
    ui = _Ui()

    def interrupted(done: threading.Event, cancel: threading.Event) -> Any:
        if done.wait(0.5):
            return ToolExecutionInterruption.SETTLED
        return ToolExecutionInterruption.OPERATOR_ABORT

    _command(session, ui, tmp_path, interruptible_steps(interrupted)).run("reconnect")

    assert ui.notices == [
        ("info", 'Still reconnecting MCP server "docs". Run /mcp to see its state.')
    ]
    release.set()
    _wait_until(lambda: session.mcp_names() == ("mcp__docs__fetch",))
    assert ui.notices[1:] == []


def test_mcp_prints_the_status_when_the_user_stops_waiting_for_startup(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    hanging = _Server(handlers={"initialize": lambda message: _NO_ANSWER})
    session = _Session(tmp_path, {"slow": hanging})
    sessions.append(session)
    session.manager.start()
    _wait_until(lambda: "initialize" in hanging.methods)
    ui = _Ui()
    cancels: list[threading.Event] = []

    def interrupted(done: threading.Event, cancel: threading.Event) -> Any:
        cancels.append(cancel)
        return ToolExecutionInterruption.OPERATOR_ABORT

    command = _command(session, ui, tmp_path, interruptible_steps(interrupted))
    command.run("")

    assert ui.notices == [("info", "slow: connecting (codemode)")]
    assert cancels[0].is_set()

    # A subcommand is not run then.
    command.run("reconnect slow")

    assert ui.notices[1:] == [
        ("info", "MCP servers are still connecting; /mcp reconnect was not run.")
    ]
    assert session.opened == ["slow"]


def test_a_cancelled_startup_wait_that_returned_in_time_is_not_a_finished_one(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    """The cancelled wait can return before the runner looks at it; the
    subcommand must not run then either."""

    hanging = _Server(handlers={"initialize": lambda message: _NO_ANSWER})
    session = _Session(tmp_path, {"slow": hanging})
    sessions.append(session)
    session.manager.start()
    _wait_until(lambda: "initialize" in hanging.methods)
    ui = _Ui()

    def interrupted(done: threading.Event, cancel: threading.Event) -> Any:
        cancel.set()
        assert done.wait(_WAIT_SECONDS)
        return ToolExecutionInterruption.OPERATOR_ABORT

    run_step = interruptible_steps(interrupted)
    assert run_step(lambda cancel: None)  # the runner itself calls it finished
    _command(session, ui, tmp_path, run_step).run("reconnect slow")

    assert ui.notices == [
        ("info", "MCP servers are still connecting; /mcp reconnect was not run.")
    ]
    assert session.opened == ["slow"]


# -- SLH-04: login and logout --------------------------------------------------


def _browser(url: str) -> None:
    """The user approves in a browser that reaches the loopback port."""

    FakeBrowser().open(url)


def _no_browser(url: str) -> None:
    raise AssertionError(f"no browser is opened here, but was asked for {url}")


def test_login_shows_the_url_opens_the_browser_and_reports_the_tools(
    tmp_path: Path,
    oauth: Servers,  # noqa: F811
) -> None:
    # Pi: "signs in through the browser, refreshes expired tokens, and signs
    # out" (the command's part). SLH-04, OAU-26, OAU-29.
    oauth.start()
    ui = _Ui()
    opened: list[str] = []
    session_thread = threading.current_thread()

    def open_url(url: str) -> None:
        # The browser is opened by the thread the command runs on.
        assert threading.current_thread() is session_thread
        opened.append(url)
        _browser(url)

    command = _command(oauth, ui, tmp_path, open_url=open_url)

    command.run("login issues")

    assert ui.notices == [
        ("info", f'Sign in to MCP server "issues" in your browser:\n{opened[0]}'),
        ("info", 'Signed in to MCP server "issues" (1 tools).'),
    ]
    assert ui.discovery_calls == 1
    assert oauth.server.log == ["401 none", "register", "token code"]
    assert oauth.state() == "connected"


def test_login_without_a_name_takes_the_server_that_needs_a_sign_in(
    tmp_path: Path,
    oauth: Servers,  # noqa: F811
) -> None:
    # SLH-05: `off` is disabled, so `issues` is the only eligible server.
    oauth.start()
    ui = _Ui()

    _command(oauth, ui, tmp_path, open_url=_browser).run("login")

    assert ui.notices[-1] == ("info", 'Signed in to MCP server "issues" (1 tools).')
    assert ui.selections == []


def test_login_requires_a_user_interface(
    tmp_path: Path,
    oauth: Servers,  # noqa: F811
) -> None:
    # SLH-04: without one nothing is contacted.
    oauth.start()
    ui = _Ui()
    requests = len(oauth.server.requests)

    _command(oauth, ui, tmp_path).run("login issues")

    assert ui.notices == [
        ("error", 'Signing in to MCP server "issues" requires interactive mode.')
    ]
    assert len(oauth.server.requests) == requests
    assert ui.discovery_calls == 0


def test_login_of_a_signed_in_server_refreshes_without_a_browser(
    tmp_path: Path,
    oauth: Servers,  # noqa: F811
) -> None:
    # OAU-24: no URL is shown when the stored refresh token does it.
    oauth.start()
    oauth.sign_in_elsewhere()
    ui = _Ui()

    _command(oauth, ui, tmp_path, open_url=_no_browser).run("login issues")

    assert ui.notices == [("info", 'Signed in to MCP server "issues" (1 tools).')]
    assert oauth.server.log[-1] == "token refresh"


def test_login_reports_a_failure_as_an_error(
    tmp_path: Path,
    oauth: Servers,  # noqa: F811
) -> None:
    # OAU-29.
    oauth.start()
    oauth.server.response_iss = "https://attacker.example"
    ui = _Ui()

    _command(oauth, ui, tmp_path, open_url=_browser).run("login issues")

    level, message = ui.notices[-1]
    assert level == "error"
    assert message.startswith("Sign-in failed: OAuth issuer mismatch")
    assert ui.discovery_calls == 0


def _interrupted_when(
    condition: Callable[[], bool],
) -> Callable[[Callable[[threading.Event], object]], bool]:
    """A ``RunStep`` whose user stops waiting once ``condition`` holds."""

    def run_step(step: Callable[[threading.Event], object]) -> bool:
        stop = threading.Event()
        thread = threading.Thread(target=step, args=(stop,))
        thread.start()
        while thread.is_alive() and not condition():
            thread.join(0.01)
        finished = not thread.is_alive()
        stop.set()
        thread.join()
        return finished

    return run_step


def test_a_login_the_user_stops_waiting_for_is_cancelled(
    tmp_path: Path,
    oauth: Servers,  # noqa: F811
) -> None:
    # OAU-28: Escape while the browser is open.
    oauth.start()
    ui = _Ui()
    opened: list[str] = []
    command = _command(
        oauth,
        ui,
        tmp_path,
        run_step=_interrupted_when(lambda: bool(opened)),
        open_url=opened.append,
    )

    command.run("login issues")

    assert ui.notices == [
        ("info", f'Sign in to MCP server "issues" in your browser:\n{opened[0]}'),
        ("info", "Sign-in cancelled."),
    ]
    assert ui.discovery_calls == 0
    # The sign-in itself ends; nothing was stored.
    _wait_until(
        lambda: not [t for t in threading.enumerate() if t.name == "mcp-sign-in"]
    )
    assert oauth.credentials().tokens("issues", oauth.server.url) is None
    assert oauth.state() == "needs-auth"


def test_a_login_is_cancelled_while_the_authorization_server_hangs(
    tmp_path: Path,
    oauth: Servers,  # noqa: F811
) -> None:
    # OAU-28: Escape before there is a URL to show.
    oauth.start()
    path = "/.well-known/oauth-authorization-server"
    oauth.server.route(path, Reply(hang=True))
    ui = _Ui()
    command = _command(
        oauth,
        ui,
        tmp_path,
        run_step=_interrupted_when(lambda: path in oauth.server.paths),
        open_url=_no_browser,
    )

    command.run("login issues")

    assert ui.notices == [("info", "Sign-in cancelled.")]
    _wait_until(
        lambda: not [t for t in threading.enumerate() if t.name == "mcp-sign-in"]
    )


def test_logout_deletes_the_credentials_and_says_whether_there_were_any(
    tmp_path: Path,
    oauth: Servers,  # noqa: F811
) -> None:
    # SLH-04.
    oauth.start()
    oauth.sign_in_elsewhere()
    assert oauth.manager.reconnect_signed_in()
    ui = _Ui()
    command = _command(oauth, ui, tmp_path)

    command.run("logout issues")
    command.run("logout")

    assert ui.notices == [
        ("info", 'Signed out of MCP server "issues".'),
        ("info", 'No stored credentials for MCP server "issues".'),
    ]
    assert oauth.state() == "needs-auth"
    assert ui.discovery_calls == 0


def test_logout_reports_a_credentials_file_it_cannot_read(
    tmp_path: Path,
    oauth: Servers,  # noqa: F811
) -> None:
    oauth.start()
    oauth.sign_in_elsewhere()
    assert oauth.manager.reconnect_signed_in()
    path = oauth.home / "mcp-auth.json"
    content = path.read_text(encoding="utf-8")
    path.write_text("{not json", encoding="utf-8")
    ui = _Ui()

    _command(oauth, ui, tmp_path).run("logout issues")

    ((level, message),) = ui.notices
    assert level == "error"
    assert message.startswith('Could not sign out of MCP server "issues": ')
    assert oauth.state() == "connected"
    path.write_text(content, encoding="utf-8")


# -- interruptible steps -------------------------------------------------------


def _settled(done: threading.Event, cancel: threading.Event) -> Any:
    assert done.wait(_WAIT_SECONDS)
    return ToolExecutionInterruption.SETTLED


def test_an_interruptible_step_runs_off_the_callers_thread() -> None:
    threads: list[str] = []
    run_step = interruptible_steps(_settled)

    assert run_step(lambda cancel: threads.append(threading.current_thread().name))

    assert threads == ["mcp-command"]


def test_an_interruptible_step_raises_what_the_step_raised() -> None:
    def fail(cancel: threading.Event) -> None:
        raise ValueError("broken")

    with pytest.raises(ValueError, match="broken"):
        interruptible_steps(_settled)(fail)


def test_an_interrupted_step_is_told_to_stop_and_left_alone() -> None:
    seen: list[bool] = []
    finished = threading.Event()

    def step(cancel: threading.Event) -> None:
        seen.append(cancel.wait(_WAIT_SECONDS))
        finished.set()

    def interrupted(done: threading.Event, cancel: threading.Event) -> Any:
        return ToolExecutionInterruption.OPERATOR_ABORT

    assert interruptible_steps(interrupted)(step) is False
    assert finished.wait(_WAIT_SECONDS) and seen == [True]


def test_an_interruption_that_raises_still_tells_the_step_to_stop() -> None:
    finished = threading.Event()

    def step(cancel: threading.Event) -> None:
        cancel.wait(_WAIT_SECONDS)
        finished.set()

    def ctrl_c(done: threading.Event, cancel: threading.Event) -> Any:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        interruptible_steps(ctrl_c)(step)
    assert finished.wait(_WAIT_SECONDS)


# -- SLH-01: description and completion -----------------------------------------


def test_the_command_is_described_as_in_pi() -> None:
    assert mcp_session_commands(enabled=True) == {
        "/mcp": "Manage MCP servers: sign in, reconnect, enable or disable, "
        "and change exposure"
    }
    assert mcp_session_commands(enabled=False) == {}
    assert MCP_SLASH_NAME == "/mcp"
    assert MCP_COMMAND_DESCRIPTION.startswith("Manage MCP servers")


def test_arguments_complete_the_actions_then_the_eligible_servers() -> None:
    servers = [
        _status("docs", "connected", tools=2),
        _status("dropped", "disconnected", error="Connection closed"),
        _status("gone", "failed", error="boom\nmore"),
        _status("off", None, enabled=False),
        _status("soon", None),
    ]

    def complete(prefix: str) -> list[tuple[str, str, str | None]] | None:
        items = complete_mcp_arguments(servers, prefix)
        if items is None:
            return None
        return [(item.value, item.label, item.description) for item in items]

    actions = [
        ("login ", "login", None),
        ("logout ", "logout", None),
        ("reconnect ", "reconnect", None),
    ]
    assert complete("") == actions
    assert complete("  ") == actions
    assert complete("lo") == actions[:2]
    assert complete("r") == actions[2:]
    assert complete("reconnect") == actions[2:]
    assert complete("x") is None
    assert complete("reconnect ") == [
        ("reconnect docs", "docs", "connected · 2 tools"),
        ("reconnect dropped", "dropped", "disconnected"),
        ("reconnect gone", "gone", "failed: boom"),
    ]
    assert complete("  reconnect   d") == [
        ("reconnect docs", "docs", "connected · 2 tools"),
        ("reconnect dropped", "dropped", "disconnected"),
    ]
    assert complete("reconnect z") is None
    assert complete("reconnect docs ") is None
    assert complete("enable ") is None
    # None of these servers signs in with OAuth.
    assert complete("login ") is None
    assert complete("logout d") is None


def test_login_and_logout_complete_the_servers_that_use_oauth() -> None:
    # SLH-01.
    def signing_in(name: str, state: str) -> McpServerStatus:
        status = _status(name, state)
        assert status.connection is not None
        view = replace(status.connection, oauth_url=f"https://{name}.example/mcp")
        return McpServerStatus(status.entry, True, view)

    statuses = [
        _status("docs", "connected", tools=2),
        signing_in("issues", "needs-auth"),
        signing_in("wiki", "connected"),
    ]

    def complete(prefix: str) -> list[tuple[str, str, str | None]] | None:
        items = complete_mcp_arguments(statuses, prefix)
        if items is None:
            return None
        return [(item.value, item.label, item.description) for item in items]

    assert complete("login ") == [
        ("login issues", "issues", "needs sign-in"),
        ("login wiki", "wiki", "connected · 0 tools"),
    ]
    assert complete("logout w") == [("logout wiki", "wiki", "connected · 0 tools")]
    assert complete("login d") is None


def test_the_session_command_dispatches_only_mcp_and_completes_its_arguments(
    tmp_path: Path, sessions: list[_Session]
) -> None:
    session = _Session(tmp_path, {"docs": _Server(tools=[_tool("search")])})
    sessions.append(session)
    session.start()
    ui = _Ui()
    current: list[Any] = [_command(session, ui, tmp_path)]
    command = McpSessionCommand(lambda: current[0])

    assert not command.dispatch("/mcpx")
    assert not command.dispatch("/model x")
    assert not command.dispatch("mcp")
    assert ui.notices == []
    assert command.dispatch("/mcp")
    assert command.dispatch("/mcp  a b c")
    assert ui.notices == [
        ("info", "docs: connected, 1 tools (codemode)"),
        ("warning", MCP_USAGE),
    ]

    other = (CompletionItem("opus", "opus"),)
    complete = command.completer(lambda name, argument: other)
    assert complete("model", "o") == other
    assert complete("mcp", "rec") == (CompletionItem("reconnect ", "reconnect", None),)
    assert complete("mcp", "reconnect ") == (
        CompletionItem("reconnect docs", "docs", "connected · 1 tool"),
    )
    assert complete("mcp", "nope") is None
    assert McpArgumentCompletion("a", "b").description is None

    # A reload turned MCP off: there is no `/mcp` any more.
    current[0] = None
    assert not command.dispatch("/mcp")
    assert complete("mcp", "rec") == other
    assert len(ui.notices) == 2


def test_the_slash_menu_lists_mcp_after_the_extension_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    resources = WorkspaceResources.discover(
        tmp_path, config_home_env={}, home_dir=tmp_path
    )
    session_commands = mcp_session_commands(enabled=True)

    names = tool_loop_command_names(resources, ("/hello",), session_commands)
    descriptions = tool_loop_command_descriptions(
        resources, {"/hello": "Greets."}, session_commands
    )

    assert names[-2:] == ("/hello", "/mcp")
    assert descriptions["/mcp"] == MCP_COMMAND_DESCRIPTION
    # An extension command named `mcp` is what runs, so it is what is shown.
    own = tool_loop_command_names(resources, ("/mcp",), session_commands)
    assert own.count("/mcp") == 1
    assert (
        tool_loop_command_descriptions(resources, {"/mcp": "Mine."}, session_commands)[
            "/mcp"
        ]
        == "Mine."
    )
    # With MCP turned off the menu has no such entry.
    assert "/mcp" not in tool_loop_command_names(
        resources, (), mcp_session_commands(enabled=False)
    )
    assert "/mcp" not in tool_loop_command_names(resources)
