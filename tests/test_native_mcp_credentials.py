"""Tests for the credentials file of MCP servers.

Tests that mirror a case of Pi's
``packages/coding-agent/test/mcp-oauth-store.test.ts`` name it in a ``Pi:``
comment.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.mcp.credentials import (
    MCP_AUTH_FILE_NAME,
    McpOAuthCredentialStore,
    McpRefreshLockTimeoutError,
    mcp_auth_path,
)
from pipy_harness.native.mcp.oauth.provider import (
    McpOAuthProvider,
    McpOAuthProviderOptions,
)

_SERVER_URL = "https://mcp.example.com/mcp"
_WAIT_SECONDS = 10.0

# Takes a server's refresh lock, says so and keeps it until it is killed.
_HOLD_LOCK = """
import sys, time
from pipy_harness.native.mcp.credentials import McpOAuthCredentialStore

store = McpOAuthCredentialStore(sys.argv[1]).for_server("test", sys.argv[2])
with store.refresh_lock():
    print("locked", flush=True)
    time.sleep(600)
"""


def _state(access_token: str) -> dict[str, Any]:
    return {
        "serverUrl": _SERVER_URL,
        "tokens": {"access_token": access_token, "token_type": "Bearer"},
    }


def _access_token(state: dict[str, Any] | None) -> str | None:
    return None if state is None else state["tokens"]["access_token"]


@pytest.fixture
def store(tmp_path: Path) -> McpOAuthCredentialStore:
    return McpOAuthCredentialStore(mcp_auth_path(tmp_path / "home"))


def test_keeps_separate_credentials_for_servers_sharing_a_url(
    store: McpOAuthCredentialStore,
) -> None:
    # Pi: keeps separate credentials for servers sharing a SERVER_URL
    store.for_server("work", _SERVER_URL).save(_state("work-token"))
    store.for_server("personal", _SERVER_URL).save(_state("personal-token"))

    assert _access_token(store.for_server("work", _SERVER_URL).load()) == "work-token"
    assert (
        _access_token(store.for_server("personal", _SERVER_URL).load())
        == "personal-token"
    )

    assert store.remove("work", _SERVER_URL) is True
    assert store.for_server("work", _SERVER_URL).load() is None
    personal = store.tokens("personal", _SERVER_URL)
    assert personal is not None and personal["access_token"] == "personal-token"
    assert store.remove("work", _SERVER_URL) is False


def test_the_key_is_the_namespace_and_the_url(store: McpOAuthCredentialStore) -> None:
    store.for_server("my-work", "HTTPS://MCP.example.com:443/mcp#part").save(
        _state("token")
    )

    assert list(json.loads(store.path.read_text(encoding="utf-8"))) == [
        f"mcp__my_work|{_SERVER_URL}"
    ]
    # Names that differ only in `-` and `_` are the same server.
    assert _access_token(store.for_server("my_work", _SERVER_URL).load()) == "token"
    assert store.for_server("my-work", f"{_SERVER_URL}/other").load() is None
    assert store.tokens("other", _SERVER_URL) is None


def test_a_server_with_the_same_name_and_url_has_one_entry_in_every_store(
    store: McpOAuthCredentialStore,
) -> None:
    other = McpOAuthCredentialStore(store.path)
    store.for_server("work", _SERVER_URL).save(_state("token"))

    assert _access_token(other.for_server("work", _SERVER_URL).load()) == "token"
    assert other.remove("work", _SERVER_URL) is True
    assert store.for_server("work", _SERVER_URL).load() is None


@pytest.mark.parametrize(
    "url", ["", "ftp://mcp.example.com/mcp", "https://user:secret@mcp.example.com/"]
)
def test_refuses_a_url_that_is_no_server_url(
    store: McpOAuthCredentialStore, url: str
) -> None:
    with pytest.raises(ValueError, match="Invalid MCP server URL"):
        store.for_server("work", url)
    with pytest.raises(ValueError, match="Invalid MCP server URL"):
        store.tokens("work", url)
    with pytest.raises(ValueError, match="Invalid MCP server URL"):
        store.remove("work", url)


def test_the_file_is_private_and_readable_json(
    store: McpOAuthCredentialStore,
) -> None:
    assert store.path.name == MCP_AUTH_FILE_NAME
    # A file a write left behind is not trusted to be private.
    store.path.parent.mkdir(parents=True)
    partial = store.path.with_name(f"{MCP_AUTH_FILE_NAME}.partial")
    partial.write_text("left behind", encoding="utf-8")
    partial.chmod(0o644)

    store.for_server("work", _SERVER_URL).save(_state("tökén"))

    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    assert not partial.exists()
    key = f"mcp__work|{_SERVER_URL}"
    expected = json.dumps({key: _state("tökén")}, indent=2, ensure_ascii=False)
    assert store.path.read_text(encoding="utf-8") == expected + "\n"


def test_reading_writes_nothing(store: McpOAuthCredentialStore) -> None:
    assert store.for_server("work", _SERVER_URL).load() is None
    assert store.tokens("work", _SERVER_URL) is None

    # Not even the directory of the file, or its lock file.
    assert not store.path.parent.exists()

    assert store.remove("work", _SERVER_URL) is False
    assert not store.path.exists()


@pytest.mark.parametrize("content", ["", "  \n", "[]", '"text"', "null"])
def test_a_file_without_an_object_has_no_credentials(
    store: McpOAuthCredentialStore, content: str
) -> None:
    store.path.parent.mkdir(parents=True)
    store.path.write_text(content, encoding="utf-8")
    server = store.for_server("work", _SERVER_URL)

    assert server.load() is None
    server.save(_state("token"))
    assert _access_token(server.load()) == "token"


def test_an_entry_that_is_no_object_is_no_state(
    store: McpOAuthCredentialStore,
) -> None:
    store.path.parent.mkdir(parents=True)
    store.path.write_text(
        json.dumps({f"mcp__work|{_SERVER_URL}": "text"}), encoding="utf-8"
    )

    assert store.for_server("work", _SERVER_URL).load() is None
    assert store.tokens("work", _SERVER_URL) is None


@pytest.mark.parametrize(
    "content",
    [
        '{"mcp__work|',
        # Not JSON, though Python's parser reads them.
        "NaN",
        "-Infinity",
        '{"mcp__other|https://other.example/": {"tokensExpireAt": Infinity}}',
    ],
)
def test_a_damaged_file_is_an_error_and_is_not_replaced(
    store: McpOAuthCredentialStore, content: str
) -> None:
    store.path.parent.mkdir(parents=True)
    store.path.write_text(content, encoding="utf-8")
    server = store.for_server("work", _SERVER_URL)

    with pytest.raises(ValueError):
        server.load()
    with pytest.raises(ValueError):
        server.save(_state("token"))
    with pytest.raises(ValueError):
        store.tokens("work", _SERVER_URL)
    with pytest.raises(ValueError):
        store.remove("work", _SERVER_URL)
    assert store.path.read_text(encoding="utf-8") == content


def test_a_state_that_cannot_be_written_as_json_changes_nothing(
    store: McpOAuthCredentialStore,
) -> None:
    server = store.for_server("work", _SERVER_URL)
    server.save(_state("token"))
    before = store.path.read_text(encoding="utf-8")

    with pytest.raises(ValueError):
        server.save({**_state("other"), "tokensExpireAt": float("nan")})

    assert store.path.read_text(encoding="utf-8") == before
    assert not store.path.with_name(f"{MCP_AUTH_FILE_NAME}.partial").exists()


def test_writes_of_several_threads_and_stores_keep_each_other(
    store: McpOAuthCredentialStore,
) -> None:
    names = [f"server-{index}" for index in range(12)]
    start = threading.Barrier(len(names))

    def save(name: str) -> None:
        own = McpOAuthCredentialStore(store.path)
        start.wait()
        for _ in range(5):
            own.for_server(name, _SERVER_URL).save(_state(name))

    threads = [threading.Thread(target=save, args=(name,)) for name in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for name in names:
        assert _access_token(store.for_server(name, _SERVER_URL).load()) == name


def test_a_provider_keeps_its_state_in_the_file(
    store: McpOAuthCredentialStore,
) -> None:
    def provider() -> McpOAuthProvider:
        return McpOAuthProvider(
            McpOAuthProviderOptions(
                server_url=_SERVER_URL,
                redirect_url="http://127.0.0.1/callback",
                client_metadata={"client_name": "pipy"},
                on_redirect=lambda url: None,
                store=McpOAuthCredentialStore(store.path).for_server(
                    "work", _SERVER_URL
                ),
            )
        )

    provider().save_tokens({"access_token": "one", "token_type": "Bearer"})

    assert provider().tokens() == {"access_token": "one", "token_type": "Bearer"}
    stored = store.tokens("work", _SERVER_URL)
    assert stored is not None and stored["access_token"] == "one"


# -- the refresh lock -------------------------------------------------------


def _lock_files(directory: Path) -> list[str]:
    return sorted(
        path.name
        for path in directory.iterdir()
        if path.name.startswith("mcp-auth-refresh-")
    )


def test_the_refresh_lock_admits_one_holder_at_a_time(
    store: McpOAuthCredentialStore,
) -> None:
    inside = 0
    most = 0
    count = threading.Lock()
    start = threading.Barrier(6)

    def refresh() -> None:
        nonlocal inside, most
        # Stores that share the file stand in for separate processes.
        own = McpOAuthCredentialStore(store.path, lock_retry_ms=1)
        start.wait()
        for _ in range(5):
            with own.for_server("test", _SERVER_URL).refresh_lock():
                with count:
                    inside += 1
                    most = max(most, inside)
                time.sleep(0.002)
                with count:
                    inside -= 1

    threads = [threading.Thread(target=refresh) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert most == 1
    # The lock is released, and its file is gone.
    assert _lock_files(store.path.parent) == []


def test_the_refresh_locks_of_two_servers_are_independent(
    store: McpOAuthCredentialStore,
) -> None:
    waiting = McpOAuthCredentialStore(store.path, lock_wait_ms=50, lock_retry_ms=5)
    with store.for_server("one", _SERVER_URL).refresh_lock():
        assert len(_lock_files(store.path.parent)) == 1
        with waiting.for_server("two", _SERVER_URL).refresh_lock():
            assert len(_lock_files(store.path.parent)) == 2
        with waiting.for_server("one", f"{_SERVER_URL}/other").refresh_lock():
            pass


def test_the_lock_directory_can_be_chosen(tmp_path: Path) -> None:
    locks = tmp_path / "locks" / "deep"
    store = McpOAuthCredentialStore(tmp_path / MCP_AUTH_FILE_NAME, lock_dir=locks)

    with store.for_server("test", _SERVER_URL).refresh_lock():
        assert len(_lock_files(locks)) == 1
        assert stat.S_IMODE(locks.stat().st_mode) == 0o700
        (lock_file,) = locks.iterdir()
        assert stat.S_IMODE(lock_file.stat().st_mode) == 0o600
    assert _lock_files(locks) == []


def test_a_waiter_gives_up_when_the_refresh_lock_stays_taken(
    store: McpOAuthCredentialStore,
) -> None:
    waiting = McpOAuthCredentialStore(store.path, lock_wait_ms=60, lock_retry_ms=5)
    with store.for_server("test", _SERVER_URL).refresh_lock():
        started = time.monotonic()
        with pytest.raises(McpRefreshLockTimeoutError, match="Timed out waiting"):
            with waiting.for_server("test", _SERVER_URL).refresh_lock():
                raise AssertionError("the lock was taken twice")
        assert time.monotonic() - started >= 0.06
        # The waiter left the holder's lock file alone.
        assert len(_lock_files(store.path.parent)) == 1

    with waiting.for_server("test", _SERVER_URL).refresh_lock():
        pass


def test_the_refresh_lock_is_released_when_the_refresh_fails(
    store: McpOAuthCredentialStore,
) -> None:
    server = store.for_server("test", _SERVER_URL)

    with pytest.raises(RuntimeError, match="refresh failed"):
        with server.refresh_lock():
            raise RuntimeError("refresh failed")

    assert _lock_files(store.path.parent) == []
    with server.refresh_lock():
        pass


def _hold_lock_in_another_process(
    store: McpOAuthCredentialStore,
) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK, str(store.path), _SERVER_URL],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline() == "locked\n"
    return process


def test_another_process_is_kept_out_and_its_lock_is_free_when_it_dies(
    store: McpOAuthCredentialStore,
) -> None:
    waiting = McpOAuthCredentialStore(store.path, lock_wait_ms=100, lock_retry_ms=5)
    process = _hold_lock_in_another_process(store)
    try:
        with pytest.raises(McpRefreshLockTimeoutError):
            with waiting.for_server("test", _SERVER_URL).refresh_lock():
                raise AssertionError("the lock was taken twice")

        os.kill(process.pid, signal.SIGKILL)
        process.wait(_WAIT_SECONDS)
        # The file of the killed holder is still there, and it is not locked.
        assert len(_lock_files(store.path.parent)) == 1
        with waiting.for_server("test", _SERVER_URL).refresh_lock():
            pass
        assert _lock_files(store.path.parent) == []
    finally:
        process.kill()
        process.wait(_WAIT_SECONDS)
        assert process.stdout is not None
        process.stdout.close()
