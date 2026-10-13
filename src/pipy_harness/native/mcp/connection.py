"""One configured MCP server: connect, list its tools, reconnect, close.

``McpServerConnection`` owns the clients of one server. ``get_client``
returns the connected client or connects; every caller that arrives while a
connect is in progress waits for that one attempt. A connection that dropped
is reported as ``disconnected`` and the next call connects again.

HTTP servers. A connect to a server configured with ``url`` that fails with a
network error or with status 408, 429 or 5xx other than 501 is made again
after 250 ms and once more after a second; ``close()`` ends the wait. A
request the server answers with "session expired" is made once more on a new
connection, a tool call too, because the server did not run it. The client of
the old session is not closed at once: its other calls in flight get the same
answer and start again the same way, and the client is closed when the last
of them has ended. A resource read or listing is also made once more, after
250 ms, when the server answers with one of the statuses above; a tool call
is not, since it may have run.

Threads. An attempt runs on a daemon thread of its own (``mcp-connect``), so
the callers that wait for it can give up when their cancel event is set. A
``notifications/tools/list_changed`` or ``notifications/resources/list_changed``
is handled on a second daemon thread (``mcp-refresh``) that lives only while
a refresh is wanted, because a notification listener runs on the client's
reading thread and must not send a request. That thread lists one kind after
the other, so a slow resource listing delays a tool refresh. While a server
connects, its resources are listed on a thread ``mcp-resources`` next to the
tool listing. One lock guards the connection's state; it is never held while the
connection calls a client, a transport or a callback. The connection takes a
client's lock while it holds its own, never the other way round.

Callbacks. ``on_tools`` and ``on_change`` are called on whichever thread made
the change: the attempt thread, the refresh thread, a transport thread or the
caller of ``get_client`` or ``close``. They say that something changed, not
what; the receiver reads ``view()``. Two changes made at nearly the same
time may be announced in either order, and a change that was being announced
when ``close()`` returned may still arrive afterwards. An exception a
callback raises is dropped. A callback may call ``close()``: that call shuts
the connection down but waits for nothing, because the thread it runs on may
be the one a transport needs back before it can finish closing. A callback
must not call ``get_client`` for the attempt it is announcing.

OAuth. An HTTP server signs in with OAuth unless its entry has an
``Authorization`` header or ``auth`` (``uses_oauth``). A connection that is
given a credential store sends such a server its stored access token and
refreshes it (``McpServerAuthProvider`` in ``credentials.py``); it never
opens a browser. When the server asks for a sign-in that the stored
credentials cannot answer, the state becomes ``needs-auth``, the client is
closed, and the connect or the request fails with ``MCP server "<name>"
requires sign-in. Run /mcp to sign in.`` The next call tries to connect
again. That is the case when a refresh is not possible or is refused, when
the server asks for more scope, and when the server answers 401 again after
a refresh. ``challenge`` keeps the last challenge the server sent, for the
sign-in. ``close()`` lets no token refresh start any more and waits for one
that is running, so its new tokens are saved before the process may end.
Without a credential store
nothing of this happens: a 401 is a failed connect.

The sign-in itself is ``sign_in.py``; whoever ran it calls
``clear_challenge()`` and ``reconnect()``.

Provider tokens. A server whose entry has ``auth.provider`` is sent the
token of that provider instead: ``provider_token`` is asked for it before
every request, and nothing of it is kept here. MCP OAuth never runs for such
a server. ``close()`` treats it like a token refresh: it is not asked after
``close()`` was called, and a call that is running is waited for, because it
may be refreshing the provider's login. When it answers 401 the state becomes ``needs-auth`` as above and
the text ends ``Run /login <provider> to sign in.`` Without
``provider_token``, or when it returns ``None``, no token is sent.
"""

from __future__ import annotations

import http.client
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, TypeVar

from pipy_harness.native.fuzzy import js_trim
from pipy_harness.native.mcp.client import (
    McpClient,
    McpClientOptions,
    McpRequestOptions,
)
from pipy_harness.native.mcp.config import McpServerEntry
from pipy_harness.native.mcp.config_values import (
    resolve_config_headers,
    resolve_config_value,
)
from pipy_harness.native.mcp.credentials import (
    McpOAuthCredentialStore,
    McpOAuthSettings,
    McpServerAuthProvider,
)
from pipy_harness.native.mcp.http import (
    McpAuthProvider,
    McpAuthRequiredError,
    McpHttpError,
    McpSessionExpiredError,
    StreamableHttpTransport,
    StreamableHttpTransportOptions,
)
from pipy_harness.native.mcp.log import McpServerLog
from pipy_harness.native.mcp.oauth.errors import McpOAuthAuthorizationRequiredError
from pipy_harness.native.mcp.oauth.types import OAuthChallenge
from pipy_harness.native.mcp.protocol import (
    METHOD_NOT_FOUND,
    JsonObject,
    JsonRpcMessage,
    McpAbortError,
    McpConnectionClosedError,
    McpError,
    is_number,
)
from pipy_harness.native.mcp.resources import is_mcp_app_resource, settle_all
from pipy_harness.native.mcp.stdio import StdioTransport, StdioTransportOptions
from pipy_harness.native.mcp.transport import (
    CloseListener,
    Disposer,
    ErrorListener,
    McpTransport,
    MessageListener,
    RequestFailureListener,
)

_T = TypeVar("_T")

DEFAULT_TIMEOUT_SECONDS: Final = 60
STDERR_TAIL_CHARS: Final = 2_000
CLIENT_NAME: Final = "pipy"
# The waits between the attempts to connect to an HTTP server that failed
# with a transient error. The first is also the wait before a resource
# request is made again.
CONNECT_RETRY_DELAYS_MS: Final = (250, 1_000)
# How often a caller waiting for a connect looks at its cancel event.
_CANCEL_POLL_SECONDS: Final = 0.05

# ``disconnected``: the connection dropped (for example the stdio server
# exited); the next call reconnects. ``needs-auth``: the server wants a
# sign-in; the next call tries again with the credentials stored by then.
ServerState = Literal[
    "connecting", "connected", "disconnected", "needs-auth", "failed", "closed"
]
# The transport a factory returns must report its close exactly once after a
# ``start`` that succeeded, whoever closes it; ``close()`` relies on that.
McpTransportFactory = Callable[
    [McpServerEntry, str, McpAuthProvider | None], McpTransport
]


class McpServerConnectionError(Exception):
    """A server could not be connected, or its connection is shut down."""


@dataclass(frozen=True, slots=True)
class ConnectionView:
    """What a connection looked like at one moment.

    ``tools`` holds the definitions as the server sent them; treat them as
    read-only. They are kept while the server is disconnected, and so is
    what is known of its resources: ``has_resources`` says whether the server
    declared the capability at its last connect, and ``resources`` and
    ``resource_templates`` are what it listed then or at its last change,
    without MCP App resources. ``oauth_url`` is the server's URL when it
    signs in with OAuth, else ``None``.
    """

    name: str
    state: ServerState
    error: str | None
    tools: tuple[JsonObject, ...]
    instructions: str | None
    has_resources: bool = False
    resources: tuple[JsonObject, ...] = ()
    resource_templates: tuple[JsonObject, ...] = ()
    oauth_url: str | None = None


def uses_oauth(entry: McpServerEntry) -> bool:
    """Whether the server signs in with OAuth: an HTTP server does, unless
    its entry has an ``Authorization`` header (in any letter case) or
    ``auth`` (Pi ``usesOAuth``)."""

    config = entry.config
    if "url" not in config or config.get("auth"):
        return False
    return not any(
        header.lower() == "authorization" for header in config.get("headers") or {}
    )


def _auth_provider_name(entry: McpServerEntry) -> str | None:
    """The provider whose token the server is sent (``auth.provider``)."""

    config = entry.config
    auth = config.get("auth") if "url" in config else None
    return auth["provider"] if auth else None


def sign_in_required_message(entry: McpServerEntry) -> str:
    """What a call to a server that needs a sign-in fails with."""

    provider = _auth_provider_name(entry)
    command = f"/login {provider}" if provider else "/mcp"
    return f'MCP server "{entry.name}" requires sign-in. Run {command} to sign in.'


# The current token of a provider, or ``None`` when there is none. It is
# called on the thread that sends a request.
ProviderToken = Callable[[str], str | None]


class _ProviderTokenAuth:
    """The auth provider of a server with ``auth.provider``: the provider's
    token as it is at each request, so the provider's refreshes apply. It
    keeps no copy and has no ``on_unauthorized``, so a 401 is not retried.

    Asking for the token may refresh the provider's login and save the
    rotated credentials, so it follows the rule of the OAuth auth provider:
    after ``close`` nothing is asked any more, and ``settled`` waits for
    the calls that are running.
    """

    def __init__(self, provider: str, provider_token: ProviderToken | None) -> None:
        self._provider = provider
        self._provider_token = provider_token
        # Guards `_closed` and `_asking`. Never held during a call.
        self._idle = threading.Condition()
        self._closed = False
        self._asking = 0

    def token(self) -> str | None:
        if self._provider_token is None:
            return None
        with self._idle:
            if self._closed:
                raise McpConnectionClosedError()
            self._asking += 1
        try:
            return self._provider_token(self._provider)
        finally:
            with self._idle:
                self._asking -= 1
                self._idle.notify_all()

    def close(self) -> None:
        """Ask for no token from now on. A call that is running goes on;
        ``settled`` waits for it."""

        with self._idle:
            self._closed = True

    def settled(self) -> None:
        """Return when no call for a token is running."""

        with self._idle:
            while self._asking:
                self._idle.wait()


def expand_home(value: str) -> str:
    """``~`` and ``~/...`` name the home directory, as in a shell."""

    if value == "~":
        return os.path.expanduser("~")
    if not value.startswith("~/"):
        return value
    rest = value[2:]
    # Joined as text, so a second slash (``~//bin``) does not make the rest
    # an absolute path. A trailing slash is kept, as Node's ``path.join`` does.
    expanded = os.path.normpath(f"{os.path.expanduser('~')}/{rest}")
    return expanded + "/" if rest.endswith("/") and expanded != "/" else expanded


def create_default_transport(
    entry: McpServerEntry, cwd: str, auth_provider: McpAuthProvider | None = None
) -> McpTransport:
    """The transport for ``entry``: streamable HTTP for a ``url`` entry,
    else a server process.

    ``headers`` and ``env`` values are resolved here, so a value that cannot
    be resolved fails the connect with a message naming the server and the
    header or the key.
    """

    config = entry.config
    if "url" in config:
        return StreamableHttpTransport(
            StreamableHttpTransportOptions(
                url=config["url"],
                headers=resolve_config_headers(
                    config.get("headers"), f'MCP server "{entry.name}"'
                ),
                auth_provider=auth_provider,
            )
        )
    env = {
        key: resolve_config_value(value, f'MCP server "{entry.name}" env "{key}"')
        for key, value in (config.get("env") or {}).items()
    }
    return StdioTransport(
        StdioTransportOptions(
            command=expand_home(config["command"]),
            args=tuple(expand_home(argument) for argument in config.get("args") or ()),
            cwd=os.path.normpath(
                os.path.join(
                    os.path.abspath(cwd), expand_home(config.get("cwd") or ".")
                )
            ),
            env=env,
        )
    )


def _error_message(error: BaseException) -> str:
    return str(error) or type(error).__name__


def _is_transient_error(error: BaseException) -> bool:
    """A network failure, or a server that is overloaded or restarting:
    worth another attempt."""

    if isinstance(error, McpHttpError):
        return error.status in (408, 429) or (
            error.status >= 500 and error.status != 501
        )
    return isinstance(error, (OSError, http.client.HTTPException))


def _is_truthy(value: object) -> bool:
    """JavaScript truthiness of a JSON value: ``{}`` and ``[]`` count."""

    if value is None or value is False or value == "":
        return False
    return not (is_number(value) and (value == 0 or value != value))


def _stderr_tail(transport: StdioTransport) -> str:
    return js_trim(transport.stderr)[-STDERR_TAIL_CHARS:]


def _without_templates(list_templates: Callable[[], _T], empty: _T) -> _T:
    """A server that does not implement ``resources/templates/list`` has no
    templates."""

    try:
        return list_templates()
    except McpError as error:
        if error.code == METHOD_NOT_FOUND:
            return empty
        raise


def _fetch_resources(
    client: McpClient,
) -> tuple[tuple[JsonObject, ...], tuple[JsonObject, ...]]:
    """The resources and templates of a server, for the counts in ``/mcp``
    and ``pipy mcp list``. Both lists are asked for at the same time. A list
    that fails is empty: the resource tools list and read on demand."""

    empty: list[JsonObject] = []
    resources, templates = settle_all(
        [
            client.list_resources,
            lambda: _without_templates(client.list_resource_templates, empty),
        ]
    )
    return (
        _without_apps([] if isinstance(resources, Exception) else resources),
        _without_apps([] if isinstance(templates, Exception) else templates),
    )


def _without_apps(items: list[JsonObject]) -> tuple[JsonObject, ...]:
    return tuple(item for item in items if not is_mcp_app_resource(item))


def _close_quietly(target: McpClient | McpTransport) -> None:
    try:
        target.close()
    except Exception:  # noqa: BLE001 - closing is best effort; the caller reports its own error
        pass


class _StartReporting:
    """A transport that says when its ``start`` succeeded.

    A transport that started reports its close exactly once, however and by
    whomever it is closed; one that did not start (the executable is
    missing, it was closed before it was handed over) has nothing open and
    may never report a close. ``McpServerConnection.close`` waits for the
    first kind only. Everything else is passed through.
    """

    def __init__(self, transport: McpTransport, on_started: Callable[[], None]) -> None:
        self._transport = transport
        self._on_started = on_started

    def start(self) -> None:
        self._transport.start()
        self._on_started()

    def send(self, message: JsonRpcMessage) -> None:
        self._transport.send(message)

    def close(self) -> None:
        self._transport.close()

    def on_message(self, listener: MessageListener) -> Disposer:
        return self._transport.on_message(listener)

    def on_error(self, listener: ErrorListener) -> Disposer:
        return self._transport.on_error(listener)

    def on_close(self, listener: CloseListener) -> Disposer:
        return self._transport.on_close(listener)

    def on_request_failure(self, listener: RequestFailureListener) -> Disposer:
        return self._transport.on_request_failure(listener)

    def set_protocol_version(self, version: str) -> None:
        self._transport.set_protocol_version(version)


class _Link:
    """One client and what belongs to it. Fields are guarded by the connection lock."""

    __slots__ = (
        "attempt",
        "calls",
        "client",
        "expired",
        "refresh_wanted",
        "refreshing",
        "resources_wanted",
        "stdio",
        "transport_closed",
    )

    def __init__(self, client: McpClient) -> None:
        self.client = client
        self.stdio: StdioTransport | None = None
        # Set by the transport when it has finished closing.
        self.transport_closed = threading.Event()
        # Which lists the server said have changed, and whether a thread is
        # listing them again.
        self.refresh_wanted = False
        self.resources_wanted = False
        self.refreshing = False
        # How many requests of the connection's callers are in flight on the
        # client, and whether the server said that its session expired. An
        # expired client is closed when the count is back at zero.
        self.calls = 0
        self.expired = False
        # The connect that published the link; ``None`` until then.
        self.attempt: _Attempt | None = None


class _Attempt:
    """One connect that every caller arriving meanwhile waits for."""

    __slots__ = ("cause", "client", "done", "failure", "thread")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.client: McpClient | None = None
        self.failure: str | None = None
        self.cause: BaseException | None = None
        self.thread: threading.Thread | None = None


class McpServerConnection:
    """One configured server. Reconnects when a call finds the connection gone."""

    def __init__(
        self,
        *,
        entry: McpServerEntry,
        cwd: Path | str,
        client_version: str,
        on_tools: Callable[[McpServerConnection], None],
        on_change: Callable[[McpServerConnection], None] | None = None,
        create_transport: McpTransportFactory = create_default_transport,
        log: McpServerLog | None = None,
        credentials: McpOAuthCredentialStore | None = None,
        provider_token: ProviderToken | None = None,
    ) -> None:
        """``on_tools`` is called when the tool list or the resources were
        read, ``on_change`` when the state, the error, the tools or the
        resources changed. ``log`` receives the
        server's log messages. ``cwd`` is the session directory.
        ``credentials`` holds the OAuth credentials of servers; without it
        no server is sent an OAuth token. ``provider_token`` returns the
        current token of a provider, for a server with ``auth.provider``."""

        self.entry = entry
        self._cwd = os.path.abspath(cwd)
        self._client_version = client_version
        self._on_tools = on_tools
        self._on_change = on_change
        self._create_transport = create_transport
        self._log = log
        self._lock = threading.Lock()
        self._state: ServerState = "connecting"
        self._error: str | None = None
        self._tools: tuple[JsonObject, ...] = ()
        self._instructions: str | None = None
        self._has_resources = False
        self._resources: tuple[JsonObject, ...] = ()
        self._resource_templates: tuple[JsonObject, ...] = ()
        self._link: _Link | None = None  # the connected client
        self._pending_link: _Link | None = None  # the client of the running attempt
        # Every link whose transport started and has not reported its close,
        # whether the link is connecting, connected or dropped.
        self._open_links: list[_Link] = []
        # How many callbacks of this connection the current thread is inside.
        self._callback_depth = threading.local()
        self._opening: _Attempt | None = None
        # Stderr of the last stdio server that failed to connect.
        self._failed_stderr: str | None = None
        self._closed = False
        # Set by ``close()``; ends the wait before a retry.
        self._shutdown = threading.Event()
        # The last OAuth challenge of the server.
        self._challenge: OAuthChallenge | None = None
        self._auth_provider = self._new_auth_provider(credentials, provider_token)

    @property
    def name(self) -> str:
        return self.entry.name

    @property
    def timeout_ms(self) -> float:
        """The request timeout: ``timeout`` seconds from the config, default 60."""

        timeout = self.entry.config.get("timeout")
        seconds: float = DEFAULT_TIMEOUT_SECONDS if timeout is None else timeout
        return seconds * 1000

    @property
    def oauth_url(self) -> str | None:
        """The server's URL when it signs in with OAuth, else ``None``."""

        config = self.entry.config
        return config["url"] if "url" in config and uses_oauth(self.entry) else None

    @property
    def challenge(self) -> OAuthChallenge | None:
        """The last OAuth challenge of the server; a sign-in uses its
        resource metadata URL and its scope."""

        with self._lock:
            return self._challenge

    def clear_challenge(self) -> None:
        """Forget the challenge: the sign-in it asked for was made."""

        with self._lock:
            self._challenge = None

    def oauth_settings(self) -> McpOAuthSettings:
        """The entry's ``oauth`` settings. The client secret is resolved
        here, at every call; one that cannot be resolved raises
        ``McpConfigValueError`` naming the server."""

        config = self.entry.config
        oauth = config.get("oauth") if "url" in config else None
        if not oauth:
            return McpOAuthSettings()
        secret = oauth.get("clientSecret")
        return McpOAuthSettings(
            client_id=oauth.get("clientId"),
            client_secret=None
            if secret is None
            else resolve_config_value(
                secret, f'MCP server "{self.entry.name}" oauth.clientSecret'
            ),
            callback_port=oauth.get("callbackPort"),
            callback_url=oauth.get("callbackUrl"),
            scope=oauth.get("scope"),
            client_name=oauth.get("clientName"),
            client_registration=oauth.get("clientRegistration"),
            client_metadata_base_url=oauth.get("clientMetadataBaseUrl"),
            auth_server_metadata_url=oauth.get("authServerMetadataUrl"),
        )

    def _new_auth_provider(
        self,
        credentials: McpOAuthCredentialStore | None,
        provider_token: ProviderToken | None,
    ) -> McpServerAuthProvider | _ProviderTokenAuth | None:
        url = self.oauth_url
        if url is None:
            provider = _auth_provider_name(self.entry)
            if provider is None:
                return None
            return _ProviderTokenAuth(provider, provider_token)
        if credentials is None:
            return None
        try:
            store = credentials.for_server(self.entry.name, url)
        except ValueError:
            # No URL a transport connects to: the connect fails and says so.
            return None
        return McpServerAuthProvider(
            server_url=url,
            store=store,
            settings=self.oauth_settings,
            on_challenge=self._set_challenge,
        )

    def _set_challenge(self, challenge: OAuthChallenge) -> None:
        with self._lock:
            self._challenge = challenge

    def view(self) -> ConnectionView:
        oauth_url = self.oauth_url
        with self._lock:
            return ConnectionView(
                name=self.entry.name,
                state=self._state,
                error=self._error,
                tools=self._tools,
                instructions=self._instructions,
                has_resources=self._has_resources,
                resources=self._resources,
                resource_templates=self._resource_templates,
                oauth_url=oauth_url,
            )

    @property
    def state(self) -> ServerState:
        with self._lock:
            return self._state

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def tools(self) -> tuple[JsonObject, ...]:
        with self._lock:
            return self._tools

    @property
    def has_resources(self) -> bool:
        """Whether the server declared the ``resources`` capability at its
        last connect. Kept while it is disconnected."""

        with self._lock:
            return self._has_resources

    @property
    def instructions(self) -> str | None:
        """The server's instructions from ``initialize``, trimmed; ``None`` when empty."""

        with self._lock:
            return self._instructions

    def get_client(self, cancel_event: threading.Event | None = None) -> McpClient:
        """Return the connected client, connecting first when there is none.

        Blocks until the connect ends. Raises ``McpServerConnectionError``
        when it fails or the connection is shut down, and ``McpAbortError``
        when ``cancel_event`` is set first; the attempt then goes on for the
        other callers and the next one.
        """

        with self._lock:
            if self._closed:
                raise McpServerConnectionError(
                    f'MCP server "{self.entry.name}" is shut down'
                )
            link = self._link
            if link is not None:
                if link.client.connection_state == "connected":
                    return link.client
                # It dropped and its close listener has not run yet. That
                # listener finds the link replaced and reports nothing.
                self._link = None
            attempt = self._opening
            started = attempt is None
            if attempt is None:
                attempt = _Attempt()
                attempt.thread = threading.Thread(
                    target=self._run_attempt,
                    args=(attempt,),
                    name="mcp-connect",
                    daemon=True,
                )
                attempt.thread.start()
                self._opening = attempt
                self._state = "connecting"
        if started:
            self._changed()
        if attempt.thread is threading.current_thread():
            # A callback announcing this attempt's failure asked for the
            # client: waiting would be waiting for itself.
            raise McpServerConnectionError(
                f'MCP server "{self.entry.name}" is still connecting'
            )
        while not attempt.done.wait(
            None if cancel_event is None else _CANCEL_POLL_SECONDS
        ):
            if cancel_event is not None and cancel_event.is_set():
                raise McpAbortError()
        if attempt.client is None:
            raise McpServerConnectionError(attempt.failure) from attempt.cause
        return attempt.client

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, object] | None = None,
        options: McpRequestOptions | None = None,
    ) -> JsonObject:
        """Call a tool of the server, connecting first when needed."""

        return self._with_client(
            options, lambda client: client.call_tool(name, arguments, options)
        )

    def read_resource(
        self, uri: str, options: McpRequestOptions | None = None
    ) -> JsonObject:
        """Read a resource of the server, connecting first when needed."""

        return self._with_client(
            options, lambda client: client.read_resource(uri, options), read_only=True
        )

    def resources_page(
        self, cursor: str | None = None, options: McpRequestOptions | None = None
    ) -> JsonObject:
        """One page of the server's resources, starting at ``cursor``."""

        return self._with_client(
            options,
            lambda client: client.list_resources_page(cursor, options),
            read_only=True,
        )

    def resource_templates_page(
        self, cursor: str | None = None, options: McpRequestOptions | None = None
    ) -> JsonObject:
        """One page of the server's resource templates; none when the server
        does not implement the method."""

        empty: JsonObject = {"resourceTemplates": []}
        return self._with_client(
            options,
            lambda client: _without_templates(
                lambda: client.list_resource_templates_page(cursor, options), empty
            ),
            read_only=True,
        )

    def all_resources(
        self, options: McpRequestOptions | None = None
    ) -> list[JsonObject]:
        """Every resource of the server, through all pages."""

        return self._with_client(
            options, lambda client: client.list_resources(options), read_only=True
        )

    def all_resource_templates(
        self, options: McpRequestOptions | None = None
    ) -> list[JsonObject]:
        """Every resource template of the server; none when the server does
        not implement the method."""

        empty: list[JsonObject] = []
        return self._with_client(
            options,
            lambda client: _without_templates(
                lambda: client.list_resource_templates(options), empty
            ),
            read_only=True,
        )

    def _with_client(
        self,
        options: McpRequestOptions | None,
        run: Callable[[McpClient], _T],
        *,
        read_only: bool = False,
    ) -> _T:
        """Run a request, connecting first when needed (Pi ``withClient``).

        The request is made a second time in two cases, and never a third
        time: on a new connection when the server answers that the session
        expired, and, for a ``read_only`` request, after 250 ms when it
        answers with a transient status. A tool call is not repeated for a
        status, since it may have run. The wait ends with ``McpAbortError``
        when the caller's cancel event is set.

        A request that ends with the need to sign in closes the client,
        leaves the server in ``needs-auth`` and raises
        ``McpServerConnectionError`` with the message that says how.
        """

        cancel_event = None if options is None else options.cancel_event
        first = True
        while True:
            link = self._begin_call(cancel_event)
            expired = False
            try:
                return run(link.client)
            except (McpHttpError, McpOAuthAuthorizationRequiredError) as error:
                if self._needs_sign_in(error):
                    sign_in: BaseException = error
                    break
                if not first or not isinstance(error, McpHttpError):
                    raise
                if isinstance(error, McpSessionExpiredError):
                    # The server no longer knows the session (it restarted),
                    # so it did not run the request.
                    expired = True
                elif not (read_only and _is_transient_error(error)):
                    raise
            finally:
                self._end_call(link, expired=expired)
            first = False
            if not expired:
                self._wait_to_retry(CONNECT_RETRY_DELAYS_MS[0], cancel_event)
        self._drop_for_sign_in(link)
        raise McpServerConnectionError(
            sign_in_required_message(self.entry)
        ) from sign_in

    def _needs_sign_in(self, error: BaseException) -> bool:
        """Whether ``error`` says that the user has to sign in: the auth
        provider said so, or a server with an auth provider still answers
        401 after the provider had its turn (Pi ``needsSignIn``)."""

        return isinstance(error, McpOAuthAuthorizationRequiredError) or (
            self._auth_provider is not None and isinstance(error, McpAuthRequiredError)
        )

    def _enter_needs_auth(self, link: _Link | None) -> bool:
        """Set the state ``needs-auth`` for ``link``, the client that asked
        for the sign-in and was detached, or ``None`` when there was none.

        Nothing changes when the connection is shut down or has something
        newer than ``link``: another connected client, or a connect that
        started later. The connect that published ``link`` is not newer; it
        is still in progress while it announces the client. The caller
        holds the lock and announces the change.
        """

        attempt = self._opening
        newer = attempt is not None and (link is None or attempt is not link.attempt)
        if self._closed or newer or self._link is not None:
            return False
        self._state = "needs-auth"
        self._error = None
        return True

    def _drop_for_sign_in(self, link: _Link) -> None:
        """A request on ``link`` ended with the need to sign in: detach and
        close its client (Pi ``dropClient``) and enter ``needs-auth``.

        The two happen under one lock. A request on a client that was
        replaced meanwhile (its session expired, or the server was
        reconnected) therefore leaves the state of the new one alone. The
        close is not reported as ``disconnected``.
        """

        with self._lock:
            if self._link is link:
                self._link = None
            changed = self._enter_needs_auth(link)
        _close_quietly(link.client)
        if changed:
            self._changed()

    def _begin_call(self, cancel_event: threading.Event | None) -> _Link:
        """The connected link, counted as carrying one more request."""

        while True:
            client = self.get_client(cancel_event)
            with self._lock:
                link = self._link
                if link is not None and link.client is client:
                    link.calls += 1
                    return link
            # It dropped or was replaced since ``get_client`` returned.

    def _end_call(self, link: _Link, *, expired: bool) -> None:
        """One request on ``link`` ended; ``expired`` when the server
        answered it with "session expired".

        An expired link is detached, so the next request connects again. Its
        client stays open for the requests still in flight on it, which get
        the same answer, and is closed when the last of them has ended.
        """

        with self._lock:
            link.calls -= 1
            if expired:
                link.expired = True
                if self._link is link:
                    self._link = None
            close = link.expired and link.calls == 0
        if close:
            _close_quietly(link.client)

    def _wait_to_retry(
        self, delay_ms: float, cancel_event: threading.Event | None = None
    ) -> None:
        """Wait ``delay_ms``, or until the connection is closed. Raises
        ``McpAbortError`` when ``cancel_event`` is set first."""

        deadline = time.monotonic() + delay_ms / 1000
        while not self._shutdown.is_set():
            if cancel_event is not None and cancel_event.is_set():
                raise McpAbortError()
            left = deadline - time.monotonic()
            if left <= 0:
                return
            self._shutdown.wait(
                left if cancel_event is None else min(left, _CANCEL_POLL_SECONDS)
            )

    def reconnect(self) -> None:
        """Drop the connected client and connect again (Pi ``reconnect``).

        Waits for a connect in progress first, whatever it ends with, then
        closes the client that is connected, waits until its transport has
        closed, and connects. The dropped client's close is not reported as
        ``disconnected``. Raises what ``get_client`` raises. A call made from
        inside a callback of this connection does not wait for the transport.
        """

        self._drop_connected()
        self.get_client()

    def sign_out(self) -> None:
        """Disconnect after the stored credentials were removed (Pi
        ``signOut``): the server then needs a sign-in.

        Waits like ``reconnect`` and connects nothing. When another caller
        started to connect meanwhile, the state is left to that attempt,
        which finds no credentials.
        """

        self._drop_connected(needs_auth=True)

    def _drop_connected(self, *, needs_auth: bool = False) -> None:
        """Wait for a connect in progress, then close the connected client
        and wait until its transport has closed. With ``needs_auth`` the
        state becomes ``needs-auth`` in the step that detaches the client."""

        with self._lock:
            attempt = self._opening
        if attempt is not None and attempt.thread is not threading.current_thread():
            attempt.done.wait()
        with self._lock:
            # A closed connection has no link.
            link = self._link
            self._link = None
            waits = link is not None and link in self._open_links
            changed = needs_auth and self._enter_needs_auth(link)
        if link is not None:
            _close_quietly(link.client)
            if waits and not getattr(self._callback_depth, "value", 0):
                link.transport_closed.wait()
        if changed:
            self._changed()

    def close(self) -> None:
        """Shut the connection down for good.

        Stops a connect in progress, also one that is waiting to try again,
        and returns only when no transport of this server is open, including
        one that was still connecting, one whose session expired and one that
        somebody else is closing. Afterwards every call fails with
        ``MCP server "<name>" is shut down``. Every caller waits, also when
        another thread closed first; only a call made from inside a callback
        of this connection returns without waiting.

        No token refresh starts once ``close()`` was called, also not by a
        request that was about to. One that is running is waited for last:
        the authorization server may have rotated the refresh token, and
        ending the process before the new tokens are saved would lose the
        sign-in.
        """

        if self._auth_provider is not None:
            self._auth_provider.close()

        with self._lock:
            first = not self._closed
            self._closed = True
            self._state = "closed"
            link = self._link
            self._link = None
            pending = self._pending_link
            attempt = self._opening
            # Links whose session expired while requests were in flight on
            # them: nothing else closes those before their requests end.
            detached = [
                open_link
                for open_link in self._open_links
                if open_link is not link and open_link is not pending
            ]
            self._shutdown.set()
        if first:
            self._changed()
        if link is not None:
            _close_quietly(link.client)
        if pending is not None:
            # Fails its pending initialize or list requests and closes its
            # transport; the attempt then ends.
            _close_quietly(pending.client)
        for open_link in detached:
            _close_quietly(open_link.client)
        if getattr(self._callback_depth, "value", 0):
            return
        # Every caller waits for the facts, not for the first caller. A
        # ``close()`` of a client or a transport returns at once when another
        # thread is already closing it, so the return of such a call says
        # nothing; a transport that started says itself when it has closed.
        if attempt is not None and attempt.thread is not threading.current_thread():
            attempt.done.wait()
        # No attempt runs now and none can start, so no transport starts any
        # more and the list only shrinks.
        with self._lock:
            still_open = list(self._open_links)
        for open_link in still_open:
            open_link.transport_closed.wait()
        if self._auth_provider is not None:
            self._auth_provider.settled()

    # -- connecting -------------------------------------------------------

    def _run_attempt(self, attempt: _Attempt) -> None:
        try:
            attempt.client = self._open()
        except Exception as error:  # noqa: BLE001 - any failure is this attempt's result
            attempt.cause = error
            attempt.failure = self._connect_failed(error)
        finally:
            if attempt.client is None and attempt.failure is None:
                attempt.failure = (
                    f'MCP server "{self.entry.name}" failed to connect: '
                    "the attempt ended unexpectedly"
                )
            with self._lock:
                if self._opening is attempt:
                    self._opening = None
            attempt.done.set()

    def _open(self) -> McpClient:
        """Connect; an HTTP server that fails with a transient error is tried
        again after each of ``CONNECT_RETRY_DELAYS_MS``. A stdio server is
        tried once. Raises the error of the last attempt."""

        delays = CONNECT_RETRY_DELAYS_MS if "url" in self.entry.config else ()
        for delay_ms in delays:
            try:
                return self._connect_once()
            except Exception as error:
                if self._shutdown.is_set() or not _is_transient_error(error):
                    raise
                self._wait_to_retry(delay_ms)
                if self._shutdown.is_set():
                    raise
        return self._connect_once()

    def _connect_once(self) -> McpClient:
        link = _Link(self._new_client())
        client = link.client
        transport: McpTransport | None = None
        with self._lock:
            self._failed_stderr = None
            if self._closed:
                raise McpServerConnectionError("shut down while connecting")
            # From here ``close()`` closes this client, which ends the steps
            # below.
            self._pending_link = link
        try:
            transport = self._create_transport(
                self.entry, self._cwd, self._auth_provider
            )
            # Registered before the client starts the transport, so the close
            # of a transport that started is always heard.
            transport.on_close(lambda: self._transport_closed(link))
            refresh = self._set_up(link, transport)
        except BaseException:
            with self._lock:
                if self._pending_link is link:
                    self._pending_link = None
            _close_quietly(client)
            if transport is not None:
                # A transport the client never started is still to be closed.
                _close_quietly(transport)
            if isinstance(transport, StdioTransport):
                with self._lock:
                    self._failed_stderr = _stderr_tail(transport) or None
            raise
        self._changed(tools=True)
        if refresh:
            self._start_refresher(link)
        return client

    def _new_client(self) -> McpClient:
        name = self.entry.name
        client = McpClient(
            McpClientOptions(
                name=CLIENT_NAME,
                version=self._client_version,
                request_timeout_ms=self.timeout_ms,
                roots=(
                    {
                        "uri": Path(self._cwd).as_uri(),
                        "name": os.path.basename(self._cwd),
                    },
                ),
            )
        )
        log = self._log
        if log is not None:
            client.on_notification(
                "notifications/message", lambda params: log.write(name, params)
            )
        return client

    def _set_up(self, link: _Link, transport: McpTransport) -> bool:
        """Connect ``link``'s client, list the tools and the resources and
        publish the link.

        Returns whether the tools or the resources changed while they were
        being listed, in which case the caller starts a refresh.
        """

        client = link.client
        client.connect(
            _StartReporting(transport, lambda: self._transport_started(link))
        )
        client.on_notification(
            "notifications/tools/list_changed",
            lambda params: self._schedule_refresh(link),
        )
        client.on_notification(
            "notifications/resources/list_changed",
            lambda params: self._schedule_refresh(link, resources=True),
        )
        if isinstance(transport, StdioTransport):
            link.stdio = transport
        client.on_close(lambda: self._handle_client_close(link))
        capabilities = client.server_capabilities or {}
        # Pi tests for a key that is not undefined, so ``null`` counts.
        has_resources = "resources" in capabilities
        listed: list[tuple[tuple[JsonObject, ...], tuple[JsonObject, ...]]] = []
        fetcher: threading.Thread | None = None
        if has_resources:
            # Next to the tool listing, as in Pi. When that fails the client
            # is closed, which ends this thread's requests.
            fetcher = threading.Thread(
                target=lambda: listed.append(_fetch_resources(client)),
                name="mcp-resources",
                daemon=True,
            )
            fetcher.start()
        # A server without the tools capability (prompts or resources only)
        # does not answer tools/list.
        tools = client.list_tools() if _is_truthy(capabilities.get("tools")) else []
        if fetcher is not None:
            fetcher.join()
        resources, templates = listed[0] if listed else ((), ())
        instructions = js_trim(client.instructions or "") or None
        with self._lock:
            if self._closed:
                raise McpServerConnectionError("shut down while connecting")
            # Checked under the lock the close listener takes: a client that
            # closes after this is reported by that listener.
            if client.connection_state != "connected":
                raise McpServerConnectionError("connection closed during setup")
            self._pending_link = None
            self._link = link
            link.attempt = self._opening
            self._tools = tuple(tools)
            self._has_resources = has_resources
            self._resources = resources
            self._resource_templates = templates
            self._instructions = instructions
            self._state = "connected"
            self._error = None
            link.refreshing = link.refresh_wanted or link.resources_wanted
            return link.refreshing

    def _connect_failed(self, error: Exception) -> str:
        """Record a failed connect; return the text its callers are given."""

        if self._needs_sign_in(error):
            with self._lock:
                # The failed attempt is still the one in progress here.
                needs_auth = not self._closed
                if needs_auth:
                    self._state = "needs-auth"
                    self._error = None
            if needs_auth:
                self._changed()
                return sign_in_required_message(self.entry)
        detail = _error_message(error)
        with self._lock:
            self._state = "closed" if self._closed else "failed"
            self._error = (
                f"{detail}\n{self._failed_stderr}" if self._failed_stderr else detail
            )
            recorded = self._error
        self._changed()
        return f'MCP server "{self.entry.name}" failed to connect: {recorded}'

    def _handle_client_close(self, link: _Link) -> None:
        """The transport dropped. The next call reconnects; the status says why."""

        with self._lock:
            if self._link is not link or self._closed:
                return
            self._link = None
            self._state = "disconnected"
            stderr = "" if link.stdio is None else _stderr_tail(link.stdio)
            self._error = (
                f"Connection closed\n{stderr}" if stderr else "Connection closed"
            )
        self._changed()

    def _transport_started(self, link: _Link) -> None:
        with self._lock:
            # It may have closed again already, on another thread.
            if not link.transport_closed.is_set():
                self._open_links.append(link)

    def _transport_closed(self, link: _Link) -> None:
        """``link``'s transport finished closing. Runs on a transport thread."""

        link.transport_closed.set()
        with self._lock:
            if link in self._open_links:
                self._open_links.remove(link)

    # -- list changes -----------------------------------------------------

    def _schedule_refresh(self, link: _Link, *, resources: bool = False) -> None:
        """Have ``link``'s tools, or its resources, listed again. Runs on a
        client's reading thread."""

        with self._lock:
            if self._closed or link not in (self._link, self._pending_link):
                return
            if resources:
                link.resources_wanted = True
            else:
                link.refresh_wanted = True
            if link is not self._link or link.refreshing:
                # A link that is still connecting is refreshed once it is
                # published; a running refresher lists again.
                return
            link.refreshing = True
        self._start_refresher(link)

    def _start_refresher(self, link: _Link) -> None:
        try:
            threading.Thread(
                target=self._refresh,
                args=(link,),
                name="mcp-refresh",
                daemon=True,
            ).start()
        except RuntimeError as error:
            with self._lock:
                link.refreshing = False
            self._refresh_failed(link, error)

    def _refresh(self, link: _Link) -> None:
        """List again what the server said has changed, one list per round,
        until nothing is wanted."""

        while True:
            with self._lock:
                wanted = link.refresh_wanted or link.resources_wanted
                if self._link is not link or self._closed or not wanted:
                    link.refreshing = False
                    return
                tools_wanted = link.refresh_wanted
                if tools_wanted:
                    link.refresh_wanted = False
                else:
                    link.resources_wanted = False
            if not tools_wanted:
                self._refresh_resources(link)
                continue
            try:
                tools = link.client.list_tools()
            except Exception as error:  # noqa: BLE001 - recorded as the connection's error
                self._refresh_failed(link, error)
                continue
            with self._lock:
                if self._link is not link or self._closed:
                    continue
                self._tools = tuple(tools)
            self._changed(tools=True)

    def _refresh_resources(self, link: _Link) -> None:
        resources, templates = _fetch_resources(link.client)
        with self._lock:
            if self._link is not link or self._closed:
                return
            self._resources = resources
            self._resource_templates = templates
        self._changed(tools=True)

    def _refresh_failed(self, link: _Link, error: Exception) -> None:
        with self._lock:
            if self._link is not link or self._closed:
                return
            self._error = f"Failed to refresh tools: {_error_message(error)}"
        self._changed()

    def _changed(self, *, tools: bool = False) -> None:
        """Announce a change. An exception from a callback is dropped: it
        must not end the thread that connects, refreshes or reads."""

        callbacks = (self._on_tools if tools else None, self._on_change)
        depth = getattr(self._callback_depth, "value", 0)
        self._callback_depth.value = depth + 1
        try:
            for callback in callbacks:
                if callback is None:
                    continue
                try:
                    callback(self)
                except Exception:  # noqa: BLE001 - see above; there is nowhere to report it
                    pass
        finally:
            self._callback_depth.value = depth
