"""The ``/mcp`` command of a session (Pi ``registerCommand("mcp")``).

``/mcp`` opens the manager in a terminal and prints one line per configured
server elsewhere. ``/mcp reconnect [server]`` connects a server again.
``/mcp login [server]`` signs in to a server that uses OAuth and ``/mcp
logout [server]`` deletes its stored credentials.

Nothing here touches a user interface. The command reports through
``notify(level, message)``, asks through ``select(title, options)``, opens
the browser of a sign-in through ``open_url(url)`` and is run on the session
thread. A step that can take long (waiting for the startup connections, a
reconnect, a sign-out) is handed to ``run_step``, which the session supplies
so that Escape or an abort ends the waiting.

The manager. A session with a terminal UI supplies ``show_manager``, which
shows the manager view and runs a flow in it (``manager_flow.py``) until the
view closes. ``/mcp`` then opens the list of servers at once, without
waiting for the startup connections, and ``/mcp login`` signs in with the
view's sign-in screen, which also takes a pasted redirect URL.

Login without the manager view (a session that has a user interface but no
terminal UI; no such session exists yet). The sign-in runs on a thread of
its own (``mcp-sign-in``), because it blocks until the browser came back,
while everything the user sees happens on the session thread: the command
waits, through ``run_step``, for the authorization URL, shows it as a notice
and opens the browser, and waits again for the end of the sign-in. Ending
either wait cancels the sign-in. Without ``open_url`` the session has no
user interface to sign in with, and login says so.

Different from Pi: the usage text for an unknown action is printed without
waiting for the startup connections first. Pi's command cannot be
interrupted. Here a wait can be: ``/mcp`` then prints the servers as they
are, a subcommand is not run when the wait for the startup connections was
ended, a reconnect or a sign-out the user stops waiting for goes on in the
background, and a sign-in is cancelled. Login without the manager view
never asks for a pasted redirect URL; Pi asks through its input dialog
there.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pipy_harness.native.mcp.config import (
    DEFAULT_MCP_EXPOSURE,
    McpServerEntry,
    global_mcp_config_path,
)
from pipy_harness.native.mcp.credentials import McpRefreshLockTimeoutError
from pipy_harness.native.mcp.manager import (
    SIGN_IN_CANCELLED,
    McpManager,
    McpServerStatus,
    describe_state,
)
from pipy_harness.native.mcp.manager_flow import (
    McpBackgroundActions,
    McpManagerFlow,
    McpUi,
)
from pipy_harness.native.mcp.sign_in import wait_for_cancel

MCP_COMMAND_NAME: Final = "mcp"
MCP_COMMAND_DESCRIPTION: Final = (
    "Manage MCP servers: sign in, reconnect, enable or disable, and change exposure"
)
MCP_USAGE: Final = (
    "Usage: /mcp, /mcp login [server], /mcp logout [server], /mcp reconnect [server]"
)
NO_OAUTH_SERVER: Final = (
    "No enabled MCP server uses OAuth. "
    "Only HTTP servers without an Authorization header do."
)
NO_SERVER_TO_RECONNECT: Final = "No enabled MCP server to reconnect."
_ACTIONS: Final = ("login", "logout", "reconnect")
_WHITESPACE: Final = re.compile(r"\s+")
# How often a wait of a login looks at what it waits for.
_POLL_SECONDS: Final = 0.05

Notify = Callable[[str, str], None]
Select = Callable[[str, Sequence[str]], str | None]
# Runs ``step(cancel_event)`` and returns whether it finished. ``False``
# means the user stopped waiting: the cancel event is set, and a step that
# does not look at it goes on in the background.
RunStep = Callable[[Callable[[threading.Event], object]], bool]
# Shows the manager view until it closes. It calls what it is given with the
# view, once; that shows the first screen and returns the flow, which the
# view polls while it is open and stops when it closed.
ShowManager = Callable[[Callable[[McpUi], McpManagerFlow]], None]


def _open_nothing(url: str) -> None:
    """For a manager view in a session that cannot open a browser: the
    sign-in screen shows the URL."""


def run_step_inline(step: Callable[[threading.Event], object]) -> bool:
    """Run a step on the caller's thread; nothing can interrupt it."""

    step(threading.Event())
    return True


@dataclass(frozen=True, slots=True)
class McpArgumentCompletion:
    """One completion of the command's argument text (Pi ``AutocompleteItem``)."""

    value: str
    label: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class _Pick:
    """Which servers a subcommand can act on (Pi ``pickServer`` options)."""

    eligible: Callable[[McpServerStatus], bool]
    preferred: Callable[[McpServerStatus], bool]
    none: str


def _exposure(entry: McpServerEntry) -> str:
    exposure = entry.config.get("exposure")
    return DEFAULT_MCP_EXPOSURE if exposure is None else str(exposure)


def _uses_oauth(status: McpServerStatus) -> bool:
    """Whether ``login`` and ``logout`` can act on the server: an enabled
    HTTP server that signs in with OAuth."""

    return status.connection is not None and status.connection.oauth_url is not None


def _has_connection(status: McpServerStatus) -> bool:
    return status.connection is not None


def _state(status: McpServerStatus) -> str | None:
    return None if status.connection is None else status.connection.state


_OAUTH_PICK: Final = _Pick(
    eligible=_uses_oauth,
    preferred=lambda status: _state(status) == "needs-auth",
    none=NO_OAUTH_SERVER,
)
_RECONNECT_PICK: Final = _Pick(
    eligible=_has_connection,
    preferred=lambda status: _state(status) in ("failed", "disconnected"),
    none=NO_SERVER_TO_RECONNECT,
)


def format_status(
    servers: Sequence[McpServerStatus],
    config_errors: Sequence[str],
    config_home: Path | str,
    overridden: Sequence[str] = (),
) -> str:
    """The text ``/mcp`` prints (Pi ``formatStatus``). ``overridden`` has
    one line per registered server that ``mcp.json`` overrides."""

    if not servers and not config_errors and not overridden:
        return (
            "No MCP servers configured. Add them to "
            f"{global_mcp_config_path(config_home)} or .pipy/mcp.json."
        )
    lines = [_status_line(status) for status in servers]
    lines.extend(f"config error: {error}" for error in config_errors)
    lines.extend(f"overridden: {line}" for line in overridden)
    return "\n".join(lines)


def _status_line(status: McpServerStatus) -> str:
    name = status.entry.name
    exposure = _exposure(status.entry)
    view = status.connection
    if view is not None and view.state == "needs-auth":
        return f"{name}: needs sign-in, run /mcp login {name} ({exposure})"
    tools = (
        f", {len(view.tools)} tools"
        if view is not None and view.state == "connected"
        else ""
    )
    if not status.enabled:
        state = "disabled"
    elif view is None:
        state = "starting"
    elif view.state == "disconnected":
        state = "disconnected, reconnects on next call"
    else:
        state = view.state
    error = ""
    if view is not None and view.error and view.state != "connected":
        error = "\n    " + "\n    ".join(view.error.split("\n"))
    return f"{name}: {state}{tools} ({exposure}){error}"


def complete_mcp_arguments(
    servers: Sequence[McpServerStatus], prefix: str
) -> tuple[McpArgumentCompletion, ...] | None:
    """Completions for the text after ``/mcp `` (Pi
    ``getArgumentCompletions``): the actions, then the servers an action can
    name, each with its state."""

    action, *rest = _WHITESPACE.split(prefix.lstrip())
    if len(rest) > 1:
        return None
    if not rest:
        return (
            tuple(
                McpArgumentCompletion(value=f"{item} ", label=item)
                for item in _ACTIONS
                if item.startswith(action)
            )
            or None
        )
    if action not in _ACTIONS:
        return None
    eligible = _has_connection if action == "reconnect" else _uses_oauth
    return (
        tuple(
            McpArgumentCompletion(
                value=f"{action} {status.entry.name}",
                label=status.entry.name,
                description=describe_state(status),
            )
            for status in servers
            if eligible(status) and status.entry.name.startswith(rest[0])
        )
        or None
    )


class _AuthorizationUrl:
    """The prompt of a sign-in the session runs (``McpSignInPrompt``): it
    keeps the authorization URL for the session thread, which shows it."""

    def __init__(self) -> None:
        self.shown = threading.Event()
        self.url = ""

    def show_authorization_url(self, url: str) -> None:
        self.url = url
        self.shown.set()

    def prompt_for_redirect_url(self, cancel_event: threading.Event) -> str | None:
        wait_for_cancel(cancel_event)
        return None


class _RunningSignIn:
    """A sign-in of the manager on a thread of its own (``mcp-sign-in``),
    and the waits of the session thread for it.

    Both waits go through ``run_step``, so the user can end them; ending
    one cancels the sign-in, whose result is then ``Sign-in cancelled.``
    """

    def __init__(self, manager: McpManager, name: str, run_step: RunStep) -> None:
        self._run_step = run_step
        self._prompt = _AuthorizationUrl()
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._failures: list[str | None] = []
        self._stopped = False

        def sign_in() -> None:
            try:
                self._failures.append(manager.sign_in(name, self._prompt, self._cancel))
            except Exception as error:  # noqa: BLE001 - cross-thread capture: reported by the session thread
                self._failures.append(f"Sign-in failed: {error}")
            finally:
                self._done.set()

        threading.Thread(target=sign_in, name="mcp-sign-in", daemon=True).start()

    def authorization_url(self) -> str | None:
        """Wait for the URL the user has to open. ``None`` when the sign-in
        ended without one, or the user stopped waiting."""

        shown = self._prompt.shown
        self._wait_for(shown)
        if self._stopped or self._done.is_set() or not shown.is_set():
            return None
        return self._prompt.url

    def result(self) -> str | None:
        """Wait for the end of the sign-in: ``None`` when the server is
        signed in to and connected, else what went wrong."""

        if not self._stopped:
            self._wait_for(self._done)
        if self._stopped:
            return SIGN_IN_CANCELLED
        return self._failures[0]

    def _wait_for(self, event: threading.Event) -> None:
        """Wait for ``event`` or the end of the sign-in. When the user
        stops waiting the sign-in is cancelled and counts as stopped."""

        done = self._done

        def wait(stop: threading.Event) -> None:
            while not (event.is_set() or done.is_set()):
                if stop.wait(_POLL_SECONDS):
                    return

        try:
            finished = self._run_step(wait)
        except BaseException:
            self._cancel.set()
            raise
        if not finished or not (event.is_set() or done.is_set()):
            self._cancel.set()
            self._stopped = True


class McpCommand:
    """``/mcp`` for one session."""

    def __init__(
        self,
        manager: McpManager,
        *,
        config_home: Path | str,
        notify: Notify,
        select: Select,
        ensure_discovery: Callable[[], None],
        run_step: RunStep = run_step_inline,
        open_url: Callable[[str], object] | None = None,
        show_manager: ShowManager | None = None,
        actions: McpBackgroundActions | None = None,
    ) -> None:
        """``ensure_discovery`` is ``McpDiscovery.ensure_active``, called
        after a reconnect and a sign-in. ``select`` returns ``None`` when
        nothing was chosen or nothing can be asked. ``open_url`` opens the
        authorization URL of a sign-in in the user's browser; ``None`` says
        that the session has no user interface, and nobody can sign in.
        ``show_manager`` shows the manager view of a terminal UI; ``None``
        says that the session has none. ``actions`` runs the changes the
        manager does not wait for; a session passes the one of its servers,
        so that it learns of a change that ends after the view closed."""

        self._manager = manager
        self._config_home = config_home
        self._notify = notify
        self._select = select
        self._ensure_discovery = ensure_discovery
        self._run_step = run_step
        self._open_url = open_url
        self._show_manager = show_manager
        self._actions = McpBackgroundActions(manager) if actions is None else actions

    def complete(self, prefix: str) -> tuple[McpArgumentCompletion, ...] | None:
        return complete_mcp_arguments(self._manager.servers(), prefix)

    def run(self, args: str) -> None:
        """Run ``/mcp <args>`` on the session thread."""

        words = args.split()
        if not words and self._show_manager is not None:
            # The manager opens at once and follows the servers itself.
            self._show_manager(self._manage)
            return
        if not words:
            self._wait_for_startup()
            self._notify(
                "info",
                format_status(
                    self._manager.servers(),
                    self._manager.config_errors,
                    self._config_home,
                    self._manager.overridden(),
                ),
            )
            return
        action = words[0]
        if len(words) > 2 or action not in _ACTIONS:
            self._notify("warning", MCP_USAGE)
            return
        name = words[1] if len(words) == 2 else None
        if not self._wait_for_startup():
            self._notify(
                "info",
                f"MCP servers are still connecting; /mcp {action} was not run.",
            )
            return
        if action == "reconnect":
            server = self._pick(name, _RECONNECT_PICK)
            if server is not None:
                self._reconnect(server)
            return
        server = self._pick(name, _OAUTH_PICK)
        if server is None:
            return
        if action == "login":
            self._login(server)
        else:
            self._logout(server)

    def _flow(self, view: McpUi) -> McpManagerFlow:
        return McpManagerFlow(
            view,
            self._manager,
            actions=self._actions,
            config_home=self._config_home,
            ensure_discovery=self._ensure_discovery,
            open_url=self._open_url or _open_nothing,
        )

    def _manage(self, view: McpUi) -> McpManagerFlow:
        flow = self._flow(view)
        flow.manage()
        return flow

    def _sign_in_with_view(self, show_manager: ShowManager, name: str) -> str | None:
        """Sign in with the sign-in screen of the manager view, which is
        closed when the sign-in ended. A view that was closed before that
        cancelled the sign-in."""

        outcome: list[str | None] = []

        def start(view: McpUi) -> McpManagerFlow:
            def done(failure: str | None) -> None:
                outcome.append(failure)
                view.close()

            flow = self._flow(view)
            flow.sign_in(name, done)
            return flow

        show_manager(start)
        return outcome[0] if outcome else SIGN_IN_CANCELLED

    def _wait_for_startup(self) -> bool:
        """Pi ``await pending``; ``False`` when the user stopped waiting.

        The wait's own answer decides: a wait that was cancelled may have
        returned before the runner looked, and then counts as finished.
        """

        manager = self._manager
        settled: list[bool] = []
        self._run_step(
            lambda cancel: settled.append(manager.wait_for_startup(cancel_event=cancel))
        )
        return settled == [True]

    def _pick(self, name: str | None, pick: _Pick) -> str | None:
        """The server a subcommand acts on (Pi ``pickServer``): the named
        one, the only eligible one, the only preferred one, or the one the
        user selects."""

        servers = self._manager.servers()
        if name is not None:
            named = next((s for s in servers if s.entry.name == name), None)
            if named is None:
                self._notify("error", f'No MCP server named "{name}".')
                return None
            if not pick.eligible(named):
                self._notify("error", pick.none)
                return None
            return name
        candidates = [s.entry.name for s in servers if pick.eligible(s)]
        if not candidates:
            self._notify("info", pick.none)
            return None
        if len(candidates) == 1:
            return candidates[0]
        preferred = [
            s.entry.name for s in servers if pick.eligible(s) and pick.preferred(s)
        ]
        if len(preferred) == 1:
            return preferred[0]
        choice = self._select("MCP server", candidates)
        return choice if choice in candidates else None

    def _reconnect(self, name: str) -> None:
        manager = self._manager
        failures: list[str | None] = []
        if not self._run_step(lambda cancel: failures.append(manager.reconnect(name))):
            self._notify(
                "info",
                f'Still reconnecting MCP server "{name}". Run /mcp to see its state.',
            )
            return
        failure = failures[0]
        if failure is not None:
            self._notify("error", failure)
            return
        self._ensure_discovery()
        status = next((s for s in manager.servers() if s.entry.name == name), None)
        state = "disabled" if status is None else describe_state(status)
        self._notify("info", f'Reconnected to MCP server "{name}" ({state}).')

    def _tool_count(self, name: str) -> int:
        status = next(
            (s for s in self._manager.servers() if s.entry.name == name), None
        )
        view = None if status is None else status.connection
        return 0 if view is None else len(view.tools)

    def _login(self, name: str) -> None:
        """Sign in to the server and report how it ended (Pi
        ``loginCommand``)."""

        open_url = self._open_url
        if open_url is None:
            self._notify(
                "error",
                f'Signing in to MCP server "{name}" requires interactive mode.',
            )
            return
        if self._show_manager is not None:
            failure = self._sign_in_with_view(self._show_manager, name)
        else:
            sign_in = _RunningSignIn(self._manager, name, self._run_step)
            url = sign_in.authorization_url()
            if url is not None:
                self._notify(
                    "info", f'Sign in to MCP server "{name}" in your browser:\n{url}'
                )
                open_url(url)
            failure = sign_in.result()
        if failure is not None:
            self._notify("info" if failure == SIGN_IN_CANCELLED else "error", failure)
            return
        self._ensure_discovery()
        self._notify(
            "info",
            f'Signed in to MCP server "{name}" ({self._tool_count(name)} tools).',
        )

    def _logout(self, name: str) -> None:
        manager = self._manager
        removed: list[bool] = []
        try:
            finished = self._run_step(
                lambda cancel: removed.append(manager.sign_out(name))
            )
        except (ValueError, OSError, McpRefreshLockTimeoutError) as error:
            self._notify("error", f'Could not sign out of MCP server "{name}": {error}')
            return
        if not finished:
            self._notify(
                "info",
                f'Still signing out of MCP server "{name}". Run /mcp to see its state.',
            )
            return
        self._notify(
            "info",
            f'Signed out of MCP server "{name}".'
            if removed[0]
            else f'No stored credentials for MCP server "{name}".',
        )
