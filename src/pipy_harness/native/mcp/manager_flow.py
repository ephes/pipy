"""What the ``/mcp`` manager does with a choice (Pi ``manage``,
``runAction``, ``signInWithUi`` and ``runInBackground`` in ``ext/index.ts``).

``McpManagerFlow`` moves through the manager's screens: the list of servers,
one server with its actions, its tools, the choice of exposure, and the
sign-in. It shows them through ``McpUi``, which the view of
``native/ui/components/mcp_manager_view.py`` implements, and touches no user
interface itself.

Pi's flow is an async function that awaits each screen. Here a screen is
shown with the function that takes its outcome, and everything runs on the
session thread: the view calls those functions from its key handling, and
the key loop calls ``poll`` about ten times a second while it waits for a
key. ``poll`` is where the flow takes in what other threads did: it shows a
sign-in's next step, calls ``ensure_discovery`` when a background change
ended, and has the view build its live menus again, which is how the lists
follow servers that connect (Pi ``subscribe``).

Threads. Enabling, disabling and reconnecting block until the server has
connected or closed, so they run on a thread each (``mcp-manager-action``)
and the manager stays usable; their outcome shows in the server's state. A
sign-in runs on a thread of its own (``mcp-sign-in``) and reaches the user
through a queue that ``poll`` empties. A sign-out and a change of exposure
are made on the session thread: they are short, and the view shows their
result at once.

Different from Pi:

- A background change that ends after the manager was closed still records
  its message, and ``McpBackgroundActions.take_finished`` tells the session,
  which then calls ``ensure_discovery`` when the next turn starts. Pi calls
  it from the change's own continuation.
- Of two background changes of one server, only the one started last
  records what it had to say. Pi compares the server's connection attempt.
- The cancel key ends a sign-in at once at every step. Until the tokens
  are stored that cancels the sign-in. Afterwards, while the server is
  connected again, nothing is left to cancel: the connect goes on and
  shows in the server's state, and only the waiting ends. In Pi the key
  does nothing during that connect.
- A sign-out that fails (a credentials file that cannot be read, a refresh
  that holds the server's lock for too long) is shown as the server's
  message, as ``/mcp logout`` reports it. Pi's manager would fail.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Final, Protocol, cast

from pipy_harness.native.mcp.config import MCP_EXPOSURES, McpExposure
from pipy_harness.native.mcp.credentials import McpRefreshLockTimeoutError
from pipy_harness.native.mcp.manager import (
    SIGN_IN_CANCELLED,
    McpManager,
    McpServerStatus,
)
from pipy_harness.native.mcp.manager_menus import (
    ACTION_DISABLE,
    ACTION_DISABLE_IN_PROJECT,
    ACTION_ENABLE,
    ACTION_ENABLE_IN_PROJECT,
    ACTION_EXPOSURE,
    ACTION_RECONNECT,
    ACTION_SIGN_IN,
    ACTION_SIGN_OUT,
    ACTION_TOOLS,
    McpMenu,
    exposure_menu,
    server_menu,
    servers_menu,
    tools_menu,
)

CONTACTING_TEXT: Final = "Contacting the authorization server…"
CONNECTING_TEXT: Final = "Connecting…"
# How often the prompt of a sign-in looks whether its answer is still wanted.
_POLL_SECONDS: Final = 0.05
_SET_ENABLED: Final = {
    ACTION_ENABLE: (True, False),
    ACTION_DISABLE: (False, False),
    ACTION_ENABLE_IN_PROJECT: (True, True),
    ACTION_DISABLE_IN_PROJECT: (False, True),
}


class McpUi(Protocol):
    """The screens of the manager (Pi ``McpUi``), as the flow uses them."""

    def menu(
        self,
        build: Callable[[], McpMenu],
        on_choice: Callable[[str | None], None],
        *,
        live: bool = False,
    ) -> None:
        """Show a menu; ``on_choice`` gets the chosen item's value, or
        ``None`` when the user cancelled. A ``live`` menu is built again by
        ``refresh``."""

    def status(
        self, title: str, message: str, on_cancel: Callable[[], None] | None = None
    ) -> None:
        """Show a message while an operation runs. With ``on_cancel`` the
        cancel key calls it."""

    def redirect_url(
        self,
        title: str,
        authorization_url: str,
        on_answer: Callable[[str | None], None],
    ) -> None:
        """Show the authorization URL and ask for the redirect URL;
        ``on_answer`` gets the pasted text, or ``None`` when the user
        cancelled."""

    def refresh(self) -> None:
        """Build a live menu again."""

    def close(self) -> None:
        """End the view."""


def _error_message(error: BaseException) -> str:
    return str(error) or type(error).__name__


class McpBackgroundActions:
    """The changes of servers that the manager view started and does not
    wait for (Pi ``runInBackground``). One per loaded configuration; it
    outlives the view, because a change may end after the view was closed.
    """

    def __init__(self, manager: McpManager) -> None:
        self._manager = manager
        self._lock = threading.Lock()
        # The number of the change started last, per server.
        self._latest: dict[str, int] = {}
        self._finished = False

    def run(self, name: str, operation: Callable[[], str | None]) -> None:
        """Run ``operation`` on a thread of its own. What it returns, or
        the error it raised, becomes the server's message unless another
        change of the server was started meanwhile."""

        with self._lock:
            serial = self._latest[name] = self._latest.get(name, 0) + 1

        def run() -> None:
            try:
                message = operation()
            except Exception as error:  # noqa: BLE001 - cross-thread capture: shown as the server's message
                message = _error_message(error)
            with self._lock:
                if self._latest[name] == serial:
                    self._manager.set_message(name, message)
                self._finished = True

        threading.Thread(target=run, name="mcp-manager-action", daemon=True).start()

    def take_finished(self) -> bool:
        """Whether a change ended since this was last asked. The session
        thread asks, and then calls ``McpDiscovery.ensure_active``."""

        with self._lock:
            finished, self._finished = self._finished, False
            return finished


class _Answer:
    """The redirect URL the user pasted, handed from the session thread to
    the prompt of the sign-in."""

    def __init__(self) -> None:
        self.given = threading.Event()
        self.value: str | None = None

    def give(self, value: str | None) -> None:
        self.value = value
        self.given.set()


class _SignIn:
    """A sign-in of the manager on a thread of its own, and its prompt
    (``McpSignInPrompt``). What the user has to see is put on ``events`` for
    the session thread: ``("url", url)``, ``("prompt", answer)`` when the
    redirect URL can be pasted, ``("prompted", None)`` when that is over,
    and ``("done", failure)`` at the end."""

    def __init__(self, manager: McpManager, name: str) -> None:
        self.title = f"Sign in to {name}"
        self.url = ""
        self.events: queue.SimpleQueue[tuple[str, object]] = queue.SimpleQueue()
        self.cancel = threading.Event()

        def sign_in() -> None:
            failure: str | None
            try:
                failure = manager.sign_in(name, self, self.cancel)
            except Exception as error:  # noqa: BLE001 - cross-thread capture: reported by the session thread
                failure = f"Sign-in failed: {_error_message(error)}"
            self.events.put(("done", failure))

        threading.Thread(target=sign_in, name="mcp-sign-in", daemon=True).start()

    def show_authorization_url(self, url: str) -> None:
        self.events.put(("url", url))

    def prompt_for_redirect_url(self, cancel_event: threading.Event) -> str | None:
        answer = _Answer()
        self.events.put(("prompt", answer))
        while not answer.given.is_set() and not cancel_event.is_set():
            answer.given.wait(_POLL_SECONDS)
        self.events.put(("prompted", None))
        return answer.value


class McpManagerFlow:
    """The manager for one opening of the view. Session thread only."""

    def __init__(
        self,
        ui: McpUi,
        manager: McpManager,
        *,
        actions: McpBackgroundActions,
        config_home: Path | str,
        ensure_discovery: Callable[[], None],
        open_url: Callable[[str], object],
    ) -> None:
        """``ensure_discovery`` is ``McpDiscovery.ensure_active``, called
        after every action. ``open_url`` opens the authorization URL of a
        sign-in in the user's browser."""

        self._ui = ui
        self._manager = manager
        self._actions = actions
        self._config_home = config_home
        self._ensure_discovery = ensure_discovery
        self._open_url = open_url
        # The sign-in that is running, and what takes its outcome.
        self._signing_in: tuple[_SignIn, Callable[[str | None], None]] | None = None

    # -- the screens ---------------------------------------------------------

    def manage(self) -> None:
        """Show the list of servers (Pi ``manage``). The list is shown at
        once, whatever the servers are doing."""

        manager = self._manager
        self._ui.menu(
            lambda: servers_menu(
                manager.servers(), manager.config_errors, self._config_home
            ),
            self._server_chosen,
            live=True,
        )

    def _server_chosen(self, name: str | None) -> None:
        if name is None:
            self._ui.close()
        else:
            self._show_server(name)

    def _show_server(self, name: str) -> None:
        manager = self._manager
        self._ui.menu(
            lambda: server_menu(manager.servers(), name, manager.project_config),
            lambda action: self._action_chosen(name, action),
            live=True,
        )

    def _status(self, name: str) -> McpServerStatus | None:
        return next((s for s in self._manager.servers() if s.entry.name == name), None)

    def _action_chosen(self, name: str, action: str | None) -> None:
        """Run what was chosen for a server (Pi ``runAction``)."""

        status = self._status(name)
        if action is None or status is None:
            self.manage()
        elif action in _SET_ENABLED:
            enabled, in_project = _SET_ENABLED[action]
            manager = self._manager
            self._in_background(
                name,
                lambda: manager.set_enabled(name, enabled, in_project=in_project),
            )
        elif action == ACTION_RECONNECT:
            self._in_background(name, partial(self._reconnect, name))
        else:
            self._run(status, action)

    def _run(self, status: McpServerStatus, action: str) -> None:
        """An action the view waits for; the server's menu follows it."""

        name = status.entry.name
        done = partial(self._done, name)
        if action == ACTION_SIGN_IN:
            self.sign_in(name, done)
        elif action == ACTION_SIGN_OUT:
            done(self._sign_out(name))
        elif action == ACTION_TOOLS:
            self._ui.menu(lambda: tools_menu(status), lambda _: done(None))
        elif action == ACTION_EXPOSURE:
            self._choose_exposure(status, done)
        else:
            done(None)

    def _back(self, name: str) -> None:
        self._ensure_discovery()
        self._show_server(name)

    def _done(self, name: str, message: str | None) -> None:
        """An action ended: ``message`` is what it had to say."""

        self._manager.set_message(name, message)
        self._back(name)

    def _in_background(self, name: str, operation: Callable[[], str | None]) -> None:
        """Start a change the view does not wait for (Pi
        ``runInBackground``). The last message is forgotten before the
        change starts, which may record its own at once."""

        self._manager.set_message(name, None)
        self._actions.run(name, operation)
        self._back(name)

    def _reconnect(self, name: str) -> None:
        # The server's state and error say how a reconnect ended, also that
        # it needs a sign-in.
        self._manager.reconnect(name)

    def _sign_out(self, name: str) -> str | None:
        try:
            self._manager.sign_out(name)
        except (ValueError, OSError, McpRefreshLockTimeoutError) as error:
            return f'Could not sign out of MCP server "{name}": {error}'
        return None

    def _choose_exposure(
        self, status: McpServerStatus, done: Callable[[str | None], None]
    ) -> None:
        """Pi ``chooseExposure``: a choice that changes nothing saves
        nothing."""

        menu = exposure_menu(status)
        name = status.entry.name

        def chosen(choice: str | None) -> None:
            if choice is None or choice == menu.selected or choice not in MCP_EXPOSURES:
                done(None)
            else:
                done(self._manager.set_exposure(name, cast("McpExposure", choice)))

        self._ui.menu(lambda: menu, chosen)

    # -- signing in ----------------------------------------------------------

    def sign_in(self, name: str, on_done: Callable[[str | None], None]) -> None:
        """Sign in to the server with the sign-in screen (Pi
        ``signInWithUi``). ``on_done`` gets ``None`` when the server is
        signed in to and connected, else what went wrong; it is called from
        ``poll``, or by the cancel key, which ends the sign-in at every
        step."""

        running = _SignIn(self._manager, name)
        self._signing_in = (running, on_done)
        self._ui.status(running.title, CONTACTING_TEXT, self._cancel_sign_in)

    def _cancel_sign_in(self) -> None:
        """The cancel key during a sign-in: the sign-in is told to stop and
        nobody waits for it. What it has done by then stays: once the tokens
        are stored, the server still connects again."""

        if self._signing_in is None:
            return
        running, on_done = self._signing_in
        self._signing_in = None
        running.cancel.set()
        on_done(SIGN_IN_CANCELLED)

    def _redirect_url_given(self, answer: _Answer, value: str | None) -> None:
        if value is None:
            self._cancel_sign_in()
        else:
            answer.give(value)

    def _advance_sign_in(self) -> None:
        """Show what the running sign-in has reached."""

        while self._signing_in is not None:
            running, on_done = self._signing_in
            try:
                kind, value = running.events.get_nowait()
            except queue.Empty:
                return
            if kind == "url":
                running.url = str(value)
                self._open_url(running.url)
            elif kind == "prompt":
                self._ui.redirect_url(
                    running.title,
                    running.url,
                    partial(self._redirect_url_given, cast("_Answer", value)),
                )
            elif kind == "prompted":
                self._ui.status(running.title, CONNECTING_TEXT, self._cancel_sign_in)
            else:
                self._signing_in = None
                on_done(cast("str | None", value))

    # -- the key loop --------------------------------------------------------

    def poll(self) -> None:
        """Take in what other threads did since the last call."""

        if self._actions.take_finished():
            self._ensure_discovery()
        self._advance_sign_in()
        self._ui.refresh()

    def stop(self) -> None:
        """The view was closed: a sign-in that is still running is
        cancelled, and nobody is told how it ended."""

        if self._signing_in is not None:
            running, _ = self._signing_in
            self._signing_in = None
            running.cancel.set()
