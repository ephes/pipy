"""Tests for sign-in, sign-out and the pickup of a sign-in made elsewhere
at the manager of a session's MCP servers, and for the session's turn-start
hook that does the pickup (MCP parity plan S31).

The servers are the fake OAuth MCP server over real HTTP; the credentials
are a file in a temporary directory, and a second store on that file stands
in for another process. Tests that mirror a case of Pi's
``suite/agent-session-mcp-oauth.test.ts`` name it in a ``Pi:`` comment.
"""

from __future__ import annotations

import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.agent import AgentCancellationReason
from pipy_harness.native.agent.tools import ToolExecutionInterruption
from pipy_harness.native.mcp.config import LoadedMcpConfig, McpServerEntry
from pipy_harness.native.mcp.credentials import (
    McpOAuthCredentialStore,
    McpOAuthSettings,
    mcp_auth_path,
)
from pipy_harness.native.mcp.manager import SIGN_IN_CANCELLED, McpManager
from pipy_harness.native.mcp.manager_flow import McpBackgroundActions
from pipy_harness.native.mcp.sign_in import sign_in_mcp_server, wait_for_cancel
from pipy_harness.native.repl.mcp_session import McpServers, McpSession

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_oauth import FakeBrowser, FakeOAuthMcpServer, Reply  # noqa: E402

_WAIT_SECONDS = 10.0
_NEEDS_SIGN_IN = 'MCP server "issues" requires sign-in. Run /mcp to sign in.'
_BACKGROUND = ("mcp-oauth-fetch", "mcp-sign-in-prompt", "mcp-oauth-callback", "mcp-")


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.005)


class Browser:
    """The user of a sign-in (``McpSignInPrompt``).

    ``follow``: approves in a browser that reaches the loopback port.
    ``ignore``: never approves. ``after`` runs once the browser is done.
    """

    def __init__(
        self, mode: str = "follow", after: Callable[[], object] | None = None
    ) -> None:
        self._mode = mode
        self._after = after
        self.opened: list[str] = []
        self.shown = threading.Event()

    def show_authorization_url(self, url: str) -> None:
        self.opened.append(url)
        if self._mode == "follow":
            FakeBrowser().open(url)
        if self._after is not None:
            self._after()
        self.shown.set()

    def prompt_for_redirect_url(self, cancel_event: threading.Event) -> str | None:
        wait_for_cancel(cancel_event)
        return None

    def scope(self, index: int = -1) -> str | None:
        query = urllib.parse.urlsplit(self.opened[index]).query
        return dict(urllib.parse.parse_qsl(query, True)).get("scope")


class Servers:
    """A manager over the fake server ``issues`` and a stdio entry that is
    never started (``off``)."""

    def __init__(
        self,
        server: FakeOAuthMcpServer,
        tmp_path: Path,
        oauth: dict[str, Any] | None = None,
    ) -> None:
        self.server = server
        self.home = tmp_path / "home"
        self.home.mkdir(exist_ok=True)
        self.published: list[tuple[str, ...]] = []
        config: dict[str, Any] = {"url": server.url, "exposure": "direct"}
        if oauth is not None:
            config["oauth"] = oauth
        entries = [
            McpServerEntry(name="issues", config=config, source="test"),  # type: ignore[arg-type]
            McpServerEntry(
                name="off",
                config={"command": "unused", "enabled": False},
                source="test",
            ),
        ]
        self.manager = McpManager(
            config=LoadedMcpConfig(servers=entries, errors=[]),
            cwd=tmp_path,
            client_version="1.2.3",
            publish_tools=self._publish,
            credentials=self.credentials(),
        )

    def _publish(self, registry: Any) -> tuple[str, ...]:
        self.published.append(tuple(registry))
        return ()

    def credentials(self) -> McpOAuthCredentialStore:
        """A store on the session's credentials file, as a process has one."""

        return McpOAuthCredentialStore(mcp_auth_path(self.home))

    def start(self) -> None:
        self.manager.start()
        assert self.manager.wait_until_settled(_WAIT_SECONDS)

    def state(self) -> str:
        view = self.manager.servers()[0].connection
        assert view is not None
        return view.state

    def whoami(self) -> str:
        connection = self.manager._caller("issues", "whoami")
        result: Any = connection.call_tool("whoami", {})
        text: str = result["content"][0]["text"]
        return text

    def sign_in_elsewhere(self) -> None:
        """What ``pipy mcp login`` does in another process."""

        sign_in_mcp_server(
            server_url=self.server.url,
            store=self.credentials().for_server("issues", self.server.url),
            settings=McpOAuthSettings(),
            prompt=Browser(),
        )


@pytest.fixture
def server() -> Iterator[FakeOAuthMcpServer]:
    before = set(threading.enumerate())
    with FakeOAuthMcpServer() as fake:
        yield fake
    # No test leaves a thread of the manager or of a sign-in behind.
    _wait_until(
        lambda: (
            not [
                thread
                for thread in set(threading.enumerate()) - before
                if thread.name.startswith(_BACKGROUND)
            ]
        )
    )


@pytest.fixture
def oauth(server: FakeOAuthMcpServer, tmp_path: Path) -> Iterator[Servers]:
    fixture = Servers(server, tmp_path)
    try:
        yield fixture
    finally:
        fixture.manager.close()


# -- sign-in --------------------------------------------------------------------


def test_signs_in_through_the_browser_and_connects_the_server(oauth: Servers) -> None:
    # Pi: "signs in through the browser, refreshes expired tokens, and signs
    # out" (the sign-in). OAU-29.
    oauth.start()
    assert oauth.state() == "needs-auth"
    assert oauth.manager.awaits_sign_in()
    browser = Browser()

    assert oauth.manager.sign_in("issues", browser) is None

    assert len(browser.opened) == 1
    assert oauth.server.log == ["401 none", "register", "token code"]
    assert oauth.state() == "connected"
    assert oauth.published[-1] == ("mcp__issues__whoami",)
    assert oauth.whoami().startswith("token access-")
    assert not oauth.manager.awaits_sign_in()


def test_only_an_enabled_oauth_server_can_be_signed_in_to(oauth: Servers) -> None:
    # OAU-29.
    oauth.start()
    browser = Browser()

    assert (
        oauth.manager.sign_in("off", browser) == 'MCP server "off" does not use OAuth.'
    )
    assert oauth.manager.sign_in("nope", browser) == 'No MCP server named "nope".'
    assert oauth.manager.sign_out("off") is False
    assert oauth.manager.sign_out("nope") is False
    assert browser.opened == []


def test_a_manager_without_a_credential_store_signs_in_to_nothing(
    server: FakeOAuthMcpServer, tmp_path: Path
) -> None:
    entry = McpServerEntry(name="issues", config={"url": server.url}, source="test")
    manager = McpManager(
        config=LoadedMcpConfig(servers=[entry], errors=[]),
        cwd=tmp_path,
        client_version="1.2.3",
        publish_tools=lambda registry: (),
    )
    manager.start()
    try:
        assert manager.wait_until_settled(_WAIT_SECONDS)
        assert (
            manager.sign_in("issues", Browser())
            == 'MCP server "issues" does not use OAuth.'
        )
        assert manager.sign_out("issues") is False
    finally:
        manager.close()


def test_reports_a_sign_in_that_failed(oauth: Servers) -> None:
    # OAU-29.
    oauth.start()
    oauth.server.response_iss = "https://attacker.example"

    failure = oauth.manager.sign_in("issues", Browser())

    assert failure == (
        "Sign-in failed: OAuth issuer mismatch: "
        f'expected "{oauth.server.origin}", received "https://attacker.example"'
    )
    assert oauth.state() == "needs-auth"


def test_reports_a_reconnect_that_fails_after_the_sign_in(oauth: Servers) -> None:
    # OAU-29.
    oauth.start()
    gone = Browser(after=lambda: oauth.server.route("/mcp", Reply(404)))

    failure = oauth.manager.sign_in("issues", gone)

    assert failure is not None and failure.startswith("Signed in, but ")
    assert "token code" in oauth.server.log
    assert oauth.state() == "failed"


def test_a_cancelled_sign_in_says_so(oauth: Servers) -> None:
    # OAU-28.
    oauth.start()
    browser = Browser("ignore")
    cancel = threading.Event()
    failures: list[str | None] = []
    thread = threading.Thread(
        target=lambda: failures.append(oauth.manager.sign_in("issues", browser, cancel))
    )
    thread.start()
    assert browser.shown.wait(_WAIT_SECONDS)

    cancel.set()
    thread.join(_WAIT_SECONDS)

    assert failures == [SIGN_IN_CANCELLED]
    assert SIGN_IN_CANCELLED == "Sign-in cancelled."
    assert oauth.state() == "needs-auth"


@pytest.mark.parametrize(
    "path", [None, "/.well-known/oauth-authorization-server", "/token"]
)
def test_closing_the_manager_cancels_a_running_sign_in(
    oauth: Servers, path: str | None
) -> None:
    # Pi: "cancels a sign-in waiting on <path> when the session shuts down".
    # OAU-28, SES-17. Without a path the sign-in waits for the browser.
    oauth.start()
    browser = Browser("ignore" if path != "/token" else "follow")
    if path is not None:
        oauth.server.route(path, Reply(hang=True))
    failures: list[str | None] = []
    thread = threading.Thread(
        target=lambda: failures.append(oauth.manager.sign_in("issues", browser))
    )
    thread.start()
    if path is None:
        assert browser.shown.wait(_WAIT_SECONDS)
    else:
        _wait_until(lambda: path in oauth.server.paths)

    started = time.monotonic()
    oauth.manager.close()

    # The sign-in had ended when `close` returned.
    assert failures == [SIGN_IN_CANCELLED]
    assert time.monotonic() - started < 2.0
    thread.join(_WAIT_SECONDS)
    assert oauth.credentials().tokens("issues", oauth.server.url) is None
    # A closed manager signs in to nothing.
    assert oauth.manager.sign_in("issues", Browser()) == SIGN_IN_CANCELLED


def test_a_step_up_keeps_the_scopes_granted_so_far(
    server: FakeOAuthMcpServer, tmp_path: Path
) -> None:
    # OAU-21 through a connection: the server rejects a call for too little
    # scope, and the sign-in asks for the configured scope, the scope
    # granted so far and the challenged one.
    servers = Servers(server, tmp_path, oauth={"scope": "issues:read"})
    try:
        servers.start()
        first = Browser()
        assert servers.manager.sign_in("issues", first) is None
        assert first.scope() == "issues:read"

        server.insufficient_scope = "issues:write"
        with pytest.raises(Exception) as raised:
            servers.whoami()
        assert str(raised.value) == _NEEDS_SIGN_IN
        assert servers.state() == "needs-auth"
        server.insufficient_scope = None

        second = Browser()
        assert servers.manager.sign_in("issues", second) is None

        # Through the browser, not with the stored refresh token (OAU-24).
        assert second.scope() == "issues:read issues:write"
        assert server.log.count("token refresh") == 0
        tokens = servers.credentials().tokens("issues", server.url)
        assert tokens is not None and tokens["scope"] == "issues:read issues:write"
        assert servers.whoami() == f"token {tokens['access_token']}"
        # The challenge is answered: the next sign-in is no step-up.
        connection = servers.manager._caller("issues", "whoami")
        assert connection.challenge is None
    finally:
        servers.manager.close()


# -- sign-out -------------------------------------------------------------------


def test_sign_out_deletes_the_credentials_and_the_server_needs_a_sign_in(
    oauth: Servers,
) -> None:
    # Pi: "signs in through the browser, refreshes expired tokens, and signs
    # out" (the sign-out).
    oauth.start()
    assert oauth.manager.sign_in("issues", Browser()) is None
    token = oauth.whoami().removeprefix("token ")

    assert oauth.manager.sign_out("issues") is True

    assert oauth.credentials().tokens("issues", oauth.server.url) is None
    assert oauth.state() == "needs-auth"
    # The connection was closed with the token it had.
    assert oauth.server.deletes == [token]
    with pytest.raises(Exception) as raised:
        oauth.whoami()
    assert str(raised.value) == _NEEDS_SIGN_IN
    # Nothing is stored any more.
    assert oauth.manager.sign_out("issues") is False


def test_sign_out_leaves_the_connection_alone_when_the_file_cannot_be_read(
    oauth: Servers,
) -> None:
    oauth.start()
    assert oauth.manager.sign_in("issues", Browser()) is None
    path = mcp_auth_path(oauth.home)
    content = path.read_text(encoding="utf-8")
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError):
        oauth.manager.sign_out("issues")

    assert oauth.state() == "connected"
    path.write_text(content, encoding="utf-8")


# -- a sign-in made by another process (SES-18) ---------------------------------


def test_reconnects_a_server_somebody_signed_in_to_elsewhere(oauth: Servers) -> None:
    # Pi: "uses credentials from pi mcp login on the next turn" (the
    # manager's part).
    oauth.start()
    assert oauth.manager.awaits_sign_in()
    # Nothing changed yet: nothing is connected again.
    assert oauth.manager.reconnect_signed_in() is False
    assert oauth.server.log == ["401 none"]

    oauth.sign_in_elsewhere()

    assert oauth.state() == "needs-auth"
    assert oauth.manager.reconnect_signed_in() is True
    assert oauth.state() == "connected"
    assert oauth.published[-1] == ("mcp__issues__whoami",)
    assert not oauth.manager.awaits_sign_in()
    assert oauth.manager.reconnect_signed_in() is False


def test_a_server_that_still_needs_a_sign_in_waits_for_the_next_change(
    oauth: Servers,
) -> None:
    # The stored tokens changed, but the server rejects them too.
    oauth.start()
    oauth.sign_in_elsewhere()
    oauth.server.expire_access_tokens()
    oauth.server.refresh_tokens.clear()

    assert oauth.manager.reconnect_signed_in() is True

    assert oauth.state() == "needs-auth"
    # It waits again, with what is stored now: no reconnect at every turn.
    assert oauth.manager.awaits_sign_in()
    requests = len(oauth.server.requests)
    assert oauth.manager.reconnect_signed_in() is False
    assert len(oauth.server.requests) == requests

    oauth.sign_in_elsewhere()
    assert oauth.manager.reconnect_signed_in() is True
    assert oauth.state() == "connected"


def test_a_server_that_signed_out_waits_for_a_sign_in_again(oauth: Servers) -> None:
    oauth.start()
    assert oauth.manager.sign_in("issues", Browser()) is None
    assert not oauth.manager.awaits_sign_in()

    oauth.manager.sign_out("issues")

    assert oauth.manager.awaits_sign_in()
    assert oauth.manager.reconnect_signed_in() is False
    oauth.sign_in_elsewhere()
    assert oauth.manager.reconnect_signed_in() is True
    assert oauth.state() == "connected"


def test_the_wait_for_the_reconnects_can_be_ended(oauth: Servers) -> None:
    oauth.start()
    oauth.sign_in_elsewhere()
    oauth.server.route("/mcp", Reply(hang=True))
    cancel = threading.Event()
    results: list[bool] = []
    thread = threading.Thread(
        target=lambda: results.append(oauth.manager.reconnect_signed_in(cancel))
    )
    thread.start()
    _wait_until(lambda: oauth.state() == "connecting")

    cancel.set()
    thread.join(_WAIT_SECONDS)

    # The reconnect goes on; the caller no longer waits for it.
    assert results == [False]
    assert oauth.state() == "connecting"


def test_a_credentials_file_that_cannot_be_read_is_no_sign_in(oauth: Servers) -> None:
    oauth.start()
    path = mcp_auth_path(oauth.home)
    path.write_text("{not json", encoding="utf-8")

    # Unreadable is different from "nothing stored", so one reconnect is
    # tried, which leaves the server as it was.
    assert oauth.manager.reconnect_signed_in() is True
    assert oauth.state() in ("needs-auth", "failed")
    path.unlink()


def test_a_closed_manager_waits_for_no_sign_in(oauth: Servers) -> None:
    oauth.start()
    assert oauth.manager.awaits_sign_in()

    oauth.manager.close()

    assert not oauth.manager.awaits_sign_in()
    assert oauth.manager.reconnect_signed_in() is False


# -- the session's turn-start hook (SES-18) -------------------------------------


class _Discovery:
    """Counts the calls of ``McpDiscovery.ensure_active``."""

    def __init__(self) -> None:
        self.calls = 0

    def ensure_active(self) -> None:
        self.calls += 1


class _Tools:
    def restart_mcp_tools(self, *, keep_declared: bool = True) -> None:
        raise AssertionError("nothing is reloaded here")


def _session(oauth: Servers) -> tuple[McpSession, _Discovery]:
    discovery = _Discovery()
    loaded = McpServers(
        manager=oauth.manager,
        discovery=discovery,  # type: ignore[arg-type]
        readiness=None,  # type: ignore[arg-type]
        enabled=True,
        actions=McpBackgroundActions(oauth.manager),
    )
    session = McpSession(
        lambda registered, replaced_by: loaded,
        tools=_Tools(),
        notify=lambda level, message: None,
    )
    oauth.start()
    return session, discovery


def _watching(
    done: threading.Event, cancel: threading.Event
) -> ToolExecutionInterruption:
    """A waiter nobody interrupts."""

    assert done.wait(_WAIT_SECONDS)
    return ToolExecutionInterruption.SETTLED


def _refusing(
    done: threading.Event, cancel: threading.Event
) -> ToolExecutionInterruption:
    raise AssertionError("there is nothing to wait for")


def test_the_turn_start_follows_a_change_the_manager_view_did_not_wait_for(
    oauth: Servers,
) -> None:
    # S34, MGR-08: the view was closed before the change ended.
    session, discovery = _session(oauth)
    actions = session.servers.actions
    actions.run("issues", lambda: None)
    _wait_until(
        lambda: not [t for t in threading.enumerate() if t.name == "mcp-manager-action"]
    )

    assert session.before_turn(_refusing) is None

    assert discovery.calls == 1
    assert session.before_turn(_refusing) is None
    assert discovery.calls == 1


@pytest.mark.parametrize("waiter", [None, _watching])
def test_the_turn_start_reconnects_a_server_signed_in_to_elsewhere(
    oauth: Servers, waiter: Any
) -> None:
    session, discovery = _session(oauth)
    # Nothing changed: the turn starts at once, without a wait.
    assert session.before_turn(_refusing) is None
    assert discovery.calls == 0
    oauth.sign_in_elsewhere()

    assert session.before_turn(waiter) is None

    assert oauth.state() == "connected"
    assert discovery.calls == 1
    # Later turns have nothing to do.
    assert session.before_turn(_refusing) is None
    assert discovery.calls == 1


def test_an_interruption_of_the_reconnect_cancels_the_run(oauth: Servers) -> None:
    session, discovery = _session(oauth)
    oauth.sign_in_elsewhere()
    oauth.server.route("/mcp", Reply(hang=True))

    def escape(
        done: threading.Event, cancel: threading.Event
    ) -> ToolExecutionInterruption:
        _wait_until(lambda: oauth.state() == "connecting")
        cancel.set()
        return ToolExecutionInterruption.OPERATOR_ABORT

    assert session.before_turn(escape) is AgentCancellationReason.OPERATOR_ABORT

    # The reconnect goes on; the session did not wait for it.
    assert oauth.state() == "connecting"
    assert discovery.calls == 0


def test_ctrl_c_during_the_reconnect_cancels_the_run(oauth: Servers) -> None:
    session, discovery = _session(oauth)
    oauth.sign_in_elsewhere()
    oauth.server.route("/mcp", Reply(hang=True))

    def ctrl_c(
        done: threading.Event, cancel: threading.Event
    ) -> ToolExecutionInterruption:
        raise KeyboardInterrupt

    assert session.before_turn(ctrl_c) is AgentCancellationReason.OPERATOR_ABORT
    assert discovery.calls == 0


# -- races the review found ------------------------------------------------------


def test_a_refresh_that_ends_after_a_sign_out_does_not_restore_the_credentials(
    oauth: Servers,
) -> None:
    oauth.start()
    assert oauth.manager.sign_in("issues", Browser()) is None
    # The next call finds its token rejected and refreshes; the token
    # endpoint does not answer yet.
    oauth.server.expire_access_tokens()
    gate = threading.Event()
    answer = oauth.server._token

    def held(request: Any) -> Reply:
        gate.wait(_WAIT_SECONDS)
        return answer(request)

    oauth.server.route("/token", held)
    refreshes = len(oauth.server.requests_to("/token"))
    caller = threading.Thread(target=lambda: _quietly(oauth.whoami))
    caller.start()
    _wait_until(lambda: len(oauth.server.requests_to("/token")) > refreshes)
    removed: list[bool] = []
    leaver = threading.Thread(
        target=lambda: removed.append(oauth.manager.sign_out("issues"))
    )
    leaver.start()
    leaver.join(0.3)
    # The sign-out waits for the refresh to save what it gets.
    assert leaver.is_alive()

    gate.set()
    leaver.join(_WAIT_SECONDS)
    caller.join(_WAIT_SECONDS)

    assert removed == [True]
    assert oauth.server.log.count("token refresh") == 1
    assert oauth.credentials().tokens("issues", oauth.server.url) is None
    # Nothing looks like a sign-in made elsewhere.
    _wait_until(lambda: oauth.state() == "needs-auth")
    assert oauth.manager.reconnect_signed_in() is False
    assert oauth.state() == "needs-auth"


def _quietly(call: Callable[[], object]) -> None:
    try:
        call()
    except Exception:  # noqa: BLE001 - the call may fail; the test looks at the store
        pass


class _SlowToRead:
    """A connection whose first reader of ``state`` is held up, as a thread
    that is switched out between reading the state and acting on it."""

    name = "issues"
    oauth_url = None

    def __init__(self) -> None:
        self.current = "connecting"
        self.reading = threading.Event()
        self.release = threading.Event()
        self._held = False

    @property
    def state(self) -> str:
        found = self.current
        if not self._held:
            self._held = True
            self.reading.set()
            assert self.release.wait(_WAIT_SECONDS)
        return found


def test_an_overtaken_change_does_not_undo_the_watch_for_a_sign_in(
    oauth: Servers,
) -> None:
    # The connection announces `connecting` and then `needs-auth`; the
    # first announcement is handled last.
    manager = oauth.manager
    connection: Any = _SlowToRead()
    first = threading.Thread(target=manager._note_sign_in_state, args=(connection,))
    first.start()
    assert connection.reading.wait(_WAIT_SECONDS)
    connection.current = "needs-auth"
    second = threading.Thread(target=manager._note_sign_in_state, args=(connection,))
    second.start()
    second.join(0.2)

    connection.release.set()
    first.join(_WAIT_SECONDS)
    second.join(_WAIT_SECONDS)

    # The watch is there: a sign-in made elsewhere will be noticed.
    assert manager.awaits_sign_in()
    connection.current = "connected"
    manager._note_sign_in_state(connection)
    assert not manager.awaits_sign_in()
