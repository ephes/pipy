"""The stored OAuth credentials of MCP servers (Pi ``ext/oauth.ts``).

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
``McpOAuthProvider`` is created with. Every read and every write of it takes
the file's lock (``auth_file_lock``: a lock in the process and ``flock`` on
``mcp-auth.json.lock``), and a write reads the file again under that lock,
so the entries of other servers and of other processes are kept.

The refresh lock. Many authorization servers rotate refresh tokens, so two
refreshes with the same refresh token lose the grant. ``refresh_lock`` of a
server's store keeps other providers and other processes out from reading
the tokens to saving the new ones. It is a file
``mcp-auth-refresh-<hash of the key>`` next to the credentials file, locked
with ``flock`` and removed when the lock is released. A waiter tries every
100 ms and gives up with ``McpRefreshLockTimeoutError`` after 25 s.

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
  writes into it.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final, NoReturn, TypeVar

from pipy_harness.native.auth_store import auth_file_lock
from pipy_harness.native.mcp.config import mcp_namespace, parse_http_url
from pipy_harness.native.mcp.oauth.provider import McpOAuthState
from pipy_harness.native.mcp.oauth.types import OAuthTokens
from pipy_harness.native.mcp.protocol import is_object

_T = TypeVar("_T")

MCP_AUTH_FILE_NAME: Final = "mcp-auth.json"
# How long to wait for another holder of a refresh lock, and how often to try.
REFRESH_LOCK_WAIT_MS: Final = 25_000
REFRESH_LOCK_RETRY_MS: Final = 100
_FILE_MODE: Final = 0o600

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
        with auth_file_lock(self.path):
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
