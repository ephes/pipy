"""The stored OAuth credentials of MCP servers, and the auth provider that
uses them for a server's connections (Pi ``ext/oauth.ts``).

``McpOAuthCredentialStore`` keeps the OAuth state of every server in one
file, ``<config home>/mcp-auth.json``, readable and writable only by the
user. The file is one JSON object. A server's state document (the
``McpOAuthState`` of ``oauth/provider.py``: client registration, tokens,
pending PKCE verifier) is stored under the key ``<namespace>|<server URL>``,
where the namespace is the one of the server's tools (``mcp__<name>`` with
``-`` as ``_``). So two servers with one URL and different names keep
separate accounts, and a server with the same name and URL in two
``mcp.json`` files has one.

``for_server`` gives the store of one server, which is what an
``McpOAuthProvider`` is created with. A write takes the file's lock
(``auth_file_lock``: a lock in the process and ``flock`` on
``mcp-auth.json.lock``), reads the file again under that lock and replaces
it as a whole, so the entries of other servers and of other processes are
kept. A read takes no lock, since it finds the file as it was before or
after a write, and creates nothing: asking for the token of a server the
user never signed in to leaves no file and no directory behind.

The refresh lock. Many authorization servers rotate refresh tokens, so two
refreshes with the same refresh token lose the grant. ``refresh_lock`` of a
server's store keeps other providers and other processes out from reading
the tokens to saving the new ones. It is a file
``mcp-auth-refresh-<hash of the key>`` next to the credentials file, locked
with ``flock`` and removed when the lock is released. A waiter tries every
100 ms and gives up with ``McpRefreshLockTimeoutError`` after 25 s.

The auth provider. ``McpServerAuthProvider`` is what the HTTP transport of
an OAuth server is given. A connection never opens a browser:

- ``token`` gives the stored access token. One that expires within 30
  seconds is refreshed first when a refresh token is stored; when that
  fails the old token is sent and the server decides.
- ``on_unauthorized`` is called with a request the server rejected. It
  hands the challenge to ``on_challenge`` and refreshes the tokens. When
  the challenge reports ``insufficient_scope``, when no refresh token is
  stored and when the refresh ends in a new authorization, it raises
  ``McpOAuthAuthorizationRequiredError``: the user has to sign in.
- Requests in one process share one refresh: a request that arrives while
  one is running waits for it and gets its outcome. The refresh holds the
  server's refresh lock from reading the tokens to saving the new ones.
  When the stored access token is not the one that expired or was rejected
  (another process refreshed it, or the user signed in), nothing is
  refreshed and the stored token is used.
- A refresh is never cancelled: an answer the server gave may have rotated
  the refresh token, so it must be saved. Each request to the
  authorization server is limited to 15 seconds, so a server that does not
  answer cannot hold the refresh lock or a shutdown for long. ``settled``
  returns when no refresh is running.
- ``close`` ends the provider's refreshing: one that is running goes on, and
  no other is started, whatever a request thread was about to do. ``close``
  and then ``settled`` is how a connection shuts down, so that no refresh is
  cut off by the end of the process. A closed provider still gives the
  stored token; a rejected request then fails with
  ``McpConnectionClosedError``.

A refresh runs on the thread of the request that started it, which is an
I/O thread of the transport.

Where this differs from Pi:

- Pi's lock is a directory another process takes over when its holder has
  not renewed it for 20 s. A ``flock`` is released by the kernel when its
  holder ends, so here the lock of a process that died is free at once and
  no lock is ever taken from a process that is alive.
- The server URL in a key is the one ``config.parse_http_url`` reads: without
  its fragment, where Pi keeps the fragment. A URL that parser does not
  accept, or one with user info, is refused with ``ValueError``.
- Credentials an older Pi stored under the URL alone are not looked for; no
  pipy version wrote that key.
- The file is replaced as a whole (written next to it and renamed), where Pi
  writes into it, locks it for a read too and creates it empty at the first
  read.
- The requests of a refresh are sent by the provider's own fetch. Pi sends
  them with the fetch of the transport that was rejected.
- ``oauth.clientRegistration: "cimd"`` is not acted on yet: the provider of
  a refresh offers no Client ID Metadata Document.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, NoReturn, TypeVar

from pipy_harness.native.auth_store import auth_file_lock
from pipy_harness.native.mcp.config import mcp_namespace, parse_http_url
from pipy_harness.native.mcp.http import UnauthorizedContext
from pipy_harness.native.mcp.oauth.discovery import parse_www_authenticate
from pipy_harness.native.mcp.oauth.errors import McpOAuthAuthorizationRequiredError
from pipy_harness.native.mcp.oauth.fetch import OAuthFetch, create_oauth_fetch
from pipy_harness.native.mcp.oauth.flow import OAuthFlowOptions, authorize_mcp
from pipy_harness.native.mcp.oauth.provider import (
    McpOAuthProvider,
    McpOAuthProviderOptions,
    McpOAuthState,
    McpOAuthStateStore,
)
from pipy_harness.native.mcp.oauth.types import OAuthChallenge, OAuthTokens
from pipy_harness.native.mcp.protocol import (
    McpConnectionClosedError,
    is_number,
    is_object,
)

_T = TypeVar("_T")

MCP_AUTH_FILE_NAME: Final = "mcp-auth.json"
# How long to wait for another holder of a refresh lock, and how often to try.
REFRESH_LOCK_WAIT_MS: Final = 25_000
REFRESH_LOCK_RETRY_MS: Final = 100
_FILE_MODE: Final = 0o600
APP_NAME: Final = "pipy"
CALLBACK_HOST: Final = "127.0.0.1"
CALLBACK_PATH: Final = "/callback"
# The redirect URI of a refresh when none is stored. A refresh never
# redirects the user.
FALLBACK_REDIRECT_URL: Final = f"http://{CALLBACK_HOST}{CALLBACK_PATH}"
# An access token this close to its expiry is refreshed before it is sent.
REFRESH_SKEW_MS: Final = 30_000
# The limit of each request to the authorization server.
OAUTH_REQUEST_TIMEOUT_MS: Final = 15_000
_DEFAULT_PORTS: Final = {"http": 80, "https": 443}

_States = dict[str, Any]


class McpRefreshLockTimeoutError(Exception):
    """Another holder kept a server's refresh lock for the whole wait."""

    def __init__(self) -> None:
        super().__init__("Timed out waiting for another MCP OAuth token refresh")


def mcp_auth_path(config_home: Path | str) -> Path:
    """Where the credentials of MCP servers are stored."""

    return Path(config_home) / MCP_AUTH_FILE_NAME


def _store_key(name: str, server_url: str) -> str:
    """The key of a server's state: its namespace and its URL."""

    url = parse_http_url(server_url)
    if url is None or url.has_user_info:
        raise ValueError(f"Invalid MCP server URL: {server_url}")
    return f"{mcp_namespace(name)}|{url.href}"


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"Invalid JSON constant {name}")


def _parse_states(content: str | None) -> _States:
    """The states in the file's text. Text that is not JSON is an error, so a
    damaged file is never replaced by an empty one."""

    if content is None or not content.strip():
        return {}
    parsed = json.loads(content, parse_constant=_reject_constant)
    return parsed if is_object(parsed) else {}


def _state(states: _States, key: str) -> McpOAuthState | None:
    state = states.get(key)
    return state if is_object(state) else None


@contextmanager
def _file_lock(path: Path, wait_ms: float, retry_ms: float) -> Iterator[None]:
    """Hold an exclusive ``flock`` on the file ``path``, which exists only
    while somebody holds or waits for the lock.

    Raises ``McpRefreshLockTimeoutError`` when the lock is not free within
    ``wait_ms``.
    """

    deadline = time.monotonic() + wait_ms / 1000
    while True:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, _FILE_MODE)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            if time.monotonic() >= deadline:
                raise McpRefreshLockTimeoutError() from None
            time.sleep(retry_ms / 1000)
            continue
        except BaseException:
            os.close(fd)
            raise
        # The last holder removed the file it had locked. A lock on a file
        # that is no longer at `path` keeps nobody out, so try again.
        if _is_file_at(fd, path):
            break
        os.close(fd)
    try:
        yield
    finally:
        # Removed while it is still locked, so nobody can lock this file
        # after it is gone from the directory without noticing.
        try:
            os.unlink(path)
        except OSError:
            pass
        os.close(fd)


def _is_file_at(fd: int, path: Path) -> bool:
    try:
        there = os.stat(path)
    except OSError:
        return False
    held = os.fstat(fd)
    return (there.st_dev, there.st_ino) == (held.st_dev, held.st_ino)


class McpOAuthServerStore:
    """The OAuth state of one server in the credentials file.

    It is the ``McpOAuthStateStore`` of the server's ``McpOAuthProvider``.
    """

    def __init__(self, credentials: McpOAuthCredentialStore, key: str) -> None:
        self._credentials = credentials
        self._key = key

    def load(self) -> McpOAuthState | None:
        return self._credentials._read(lambda states: _state(states, self._key))

    def save(self, state: McpOAuthState) -> None:
        def change(states: _States) -> None:
            states[self._key] = state

        self._credentials._write(change)

    @contextmanager
    def refresh_lock(self) -> Iterator[None]:
        """Keep every other provider and process from refreshing this
        server's tokens (Pi ``withRefreshLock``)."""

        credentials = self._credentials
        credentials._lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        digest = hashlib.sha256(self._key.encode("utf-8")).hexdigest()[:16]
        path = credentials._lock_dir / f"mcp-auth-refresh-{digest}"
        with _file_lock(path, credentials._lock_wait_ms, credentials._lock_retry_ms):
            yield


class McpOAuthCredentialStore:
    """The OAuth state of every MCP server, in one file."""

    def __init__(
        self,
        path: Path | str,
        *,
        lock_dir: Path | str | None = None,
        lock_wait_ms: float = REFRESH_LOCK_WAIT_MS,
        lock_retry_ms: float = REFRESH_LOCK_RETRY_MS,
    ) -> None:
        """``path`` is the credentials file. ``lock_dir`` is where the
        refresh lock files are made, by default the file's directory; stores
        that share credentials must share it too."""

        self.path = Path(path)
        self._lock_dir = self.path.parent if lock_dir is None else Path(lock_dir)
        self._lock_wait_ms = lock_wait_ms
        self._lock_retry_ms = lock_retry_ms

    def for_server(self, name: str, server_url: str) -> McpOAuthServerStore:
        """The store of the server ``name`` at ``server_url``."""

        return McpOAuthServerStore(self, _store_key(name, server_url))

    def tokens(self, name: str, server_url: str) -> OAuthTokens | None:
        """The stored tokens of a server, for noticing a sign-in another
        process did."""

        key = _store_key(name, server_url)
        state = self._read(lambda states: _state(states, key))
        tokens: OAuthTokens | None = None if state is None else state.get("tokens")
        return tokens

    def remove(self, name: str, server_url: str) -> bool:
        """Delete a server's credentials. Returns whether there were any."""

        key = _store_key(name, server_url)

        def change(states: _States) -> bool:
            if key not in states:
                return False
            del states[key]
            return True

        return self._write(change)

    def _read(self, read: Callable[[_States], _T]) -> _T:
        # Without the lock: a write replaces the file as a whole.
        return read(self._load())

    def _write(self, change: Callable[[_States], object]) -> bool:
        """Change the states under the file's lock. A ``change`` that
        returns ``False`` leaves the file as it is. Returns whether the file
        was written."""

        with auth_file_lock(self.path):
            states = self._load()
            if change(states) is False:
                return False
            self._save(states)
            return True

    def _load(self) -> _States:
        try:
            content = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        return _parse_states(content)

    def _save(self, states: _States) -> None:
        text = json.dumps(states, indent=2, ensure_ascii=False, allow_nan=False)
        text += "\n"
        temporary = self.path.with_name(f"{self.path.name}.partial")
        fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, _FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            # The mode of `os.open` does not apply to a file that was left
            # behind by a write that did not finish.
            os.fchmod(handle.fileno(), _FILE_MODE)
            handle.write(text)
        os.replace(temporary, self.path)


# -- the auth provider of a server's connections ------------------------------


@dataclass(frozen=True, slots=True)
class McpOAuthSettings:
    """The ``oauth`` settings of a server entry, ready to use.

    ``client_secret`` is the resolved value. ``scope`` holds the scopes to
    request, separated by spaces. ``client_name`` is the ``client_name`` of a
    dynamic client registration, ``pipy`` when it is ``None``.
    """

    client_id: str | None = None
    client_secret: str | None = None
    callback_port: int | None = None
    callback_url: str | None = None
    scope: str | None = None
    client_name: str | None = None
    client_registration: str | None = None
    auth_server_metadata_url: str | None = None


def fixed_redirect_url(settings: McpOAuthSettings) -> str | None:
    """The redirect URI, when the settings fix its port; else ``None``.

    A configured URI that names a port is used as written, since servers
    compare redirect URIs as strings. A configured port is put into the
    configured URI, or into ``http://127.0.0.1/callback``.
    """

    default = f"http://{CALLBACK_HOST}{CALLBACK_PATH}"
    url = parse_http_url(
        default if settings.callback_url is None else settings.callback_url
    )
    if url is None:
        return None
    if url.port is not None:
        return settings.callback_url
    port = settings.callback_port
    if port is None:
        return None
    # A URL does not name the default port of its scheme.
    return replace(url, port=None if port == _DEFAULT_PORTS[url.scheme] else port).href


def registered_redirect_urls(state: McpOAuthState | None) -> list[str]:
    """The redirect URIs the stored client was registered with."""

    client = None if state is None else state.get("clientInformation")
    uris = client.get("redirect_uris") if is_object(client) else None
    if not isinstance(uris, list):
        return []
    return [uri for uri in uris if isinstance(uri, str)]


def _tokens(state: McpOAuthState | None) -> dict[str, Any]:
    tokens = None if state is None else state.get("tokens")
    return tokens if is_object(tokens) else {}


def _access_token(state: McpOAuthState | None) -> str | None:
    token = _tokens(state).get("access_token")
    return token if isinstance(token, str) else None


def _expires_soon(state: McpOAuthState | None) -> bool:
    """Whether the access token expires within ``REFRESH_SKEW_MS``."""

    expires_at = None if state is None else state.get("tokensExpireAt")
    if not is_number(expires_at):
        return False
    return expires_at - REFRESH_SKEW_MS <= time.time() * 1000


def create_oauth_provider(
    server_url: str,
    store: McpOAuthStateStore,
    settings: McpOAuthSettings,
    redirect_url: str,
    on_redirect: Callable[[str], None],
) -> McpOAuthProvider:
    """The provider the OAuth flow of a configured server is run with."""

    return McpOAuthProvider(
        McpOAuthProviderOptions(
            server_url=server_url,
            redirect_url=redirect_url,
            client_metadata={"client_name": settings.client_name or APP_NAME},
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            store=store,
            on_redirect=on_redirect,
        )
    )


class _Refresh:
    """One refresh, which every request that arrives meanwhile waits for."""

    __slots__ = ("done", "error")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.error: Exception | None = None


class McpServerAuthProvider:
    """The auth provider of one server's connections (Pi
    ``createMcpAuthProvider``); see the module text.

    ``settings`` is called only when a refresh is needed, so a client secret
    that cannot be resolved fails the refresh and not the connection.
    ``on_challenge`` is given the challenge of every rejected request, for
    the sign-in that may follow. ``fetch`` sends the requests to the
    authorization server; the default limits each to
    ``OAUTH_REQUEST_TIMEOUT_MS``.
    """

    def __init__(
        self,
        *,
        server_url: str,
        store: McpOAuthServerStore,
        settings: Callable[[], McpOAuthSettings],
        on_challenge: Callable[[OAuthChallenge], None],
        fetch: OAuthFetch | None = None,
    ) -> None:
        self._server_url = server_url
        self._store = store
        self._settings = settings
        self._on_challenge = on_challenge
        self._fetch: OAuthFetch = (
            create_oauth_fetch(timeout_ms=OAUTH_REQUEST_TIMEOUT_MS)
            if fetch is None
            else fetch
        )
        # Guards `_refreshing` and `_closed`. Never held during a refresh.
        self._lock = threading.Lock()
        self._refreshing: _Refresh | None = None
        self._closed = False

    def token(self) -> str | None:
        """The stored access token, refreshed first when it is about to
        expire. ``None`` when nothing is stored."""

        self.settled()
        state = self._store.load()
        token = _access_token(state)
        if not _expires_soon(state) or not _tokens(state).get("refresh_token"):
            return token
        try:
            self._refresh(token, None)
        except Exception:  # noqa: BLE001 - as Pi: the request goes out with the old token and the server decides
            pass
        return _access_token(self._store.load())

    def on_unauthorized(self, context: UnauthorizedContext) -> None:
        """Refresh the tokens after the server rejected ``context.token``.

        Raises ``McpOAuthAuthorizationRequiredError`` when the user has to
        sign in.
        """

        challenge = parse_www_authenticate(context.www_authenticate)
        self._on_challenge(challenge)
        # A refresh keeps the granted scope, so more scope needs a sign-in.
        if challenge.error == "insufficient_scope":
            raise McpOAuthAuthorizationRequiredError()
        self._refresh(context.token, challenge)

    def close(self) -> None:
        """Start no refresh from now on. One that is running goes on;
        ``settled`` waits for it."""

        with self._lock:
            self._closed = True

    def settled(self) -> None:
        """Return when no refresh is running, so that a shutdown does not
        drop rotated tokens before they are saved. After ``close`` none
        starts later either."""

        with self._lock:
            running = self._refreshing
        if running is not None:
            running.done.wait()

    def _refresh(
        self, stale_token: str | None, challenge: OAuthChallenge | None
    ) -> None:
        """Replace ``stale_token``, the access token that expired or was
        rejected. A caller that arrives while a refresh is running waits for
        that one and gets its outcome. Raises ``McpConnectionClosedError``
        instead of starting a refresh once the provider is closed."""

        with self._lock:
            running = self._refreshing
            started = running is None
            if running is None:
                if self._closed:
                    raise McpConnectionClosedError()
                running = self._refreshing = _Refresh()
        if not started:
            running.done.wait()
            if running.error is not None:
                raise running.error
            return
        try:
            with self._store.refresh_lock():
                self._refresh_locked(stale_token, challenge)
        except Exception as error:
            running.error = error
            raise
        finally:
            with self._lock:
                self._refreshing = None
            running.done.set()

    def _refresh_locked(
        self, stale_token: str | None, challenge: OAuthChallenge | None
    ) -> None:
        state = self._store.load()
        if _access_token(state) != stale_token:
            # Another process refreshed the tokens, or the user signed in.
            return
        if not _tokens(state).get("refresh_token"):
            raise McpOAuthAuthorizationRequiredError()
        settings = self._settings()
        redirect_url = (
            fixed_redirect_url(settings)
            or next(iter(registered_redirect_urls(state)), None)
            or FALLBACK_REDIRECT_URL
        )
        provider = create_oauth_provider(
            self._server_url, self._store, settings, redirect_url, lambda url: None
        )
        # Not cancellable: a refresh the server answered may have rotated
        # the refresh token, so its answer must be saved.
        result = authorize_mcp(
            provider,
            OAuthFlowOptions(
                server_url=self._server_url,
                resource_metadata_url=(
                    None if challenge is None else challenge.resource_metadata_url
                ),
                authorization_server_metadata_url=settings.auth_server_metadata_url,
                scope=None if challenge is None else challenge.scope,
                fetch=self._fetch,
            ),
        )
        if result == "REDIRECT":
            raise McpOAuthAuthorizationRequiredError()
