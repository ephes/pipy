"""Tests for the OAuth provider and its state store.

Tests that mirror a case of Pi's ``packages/mcp/test/oauth.test.ts`` name it
in a ``Pi:`` comment.
"""

from __future__ import annotations

import re
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.mcp.oauth.errors import OAuthFlowError
from pipy_harness.native.mcp.oauth.flow import (
    InvalidationKind,
    OAuthClientMetadataDocument,
    OAuthFlowOptions,
    authorize_mcp,
)
from pipy_harness.native.mcp.oauth.provider import (
    McpOAuthProvider,
    McpOAuthProviderOptions,
    McpOAuthState,
    McpOAuthStateStore,
    MemoryOAuthStateStore,
)
from pipy_harness.native.mcp.oauth.types import AuthorizationServerMetadata

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_oauth import (  # noqa: E402
    FakeAuthorizationServer,
    authorization_response,
)

_WAIT_SECONDS = 10.0
_REDIRECT = "http://127.0.0.1/callback"
_SERVER = "https://one.example/mcp"


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.005)


@pytest.fixture
def server() -> Iterator[FakeAuthorizationServer]:
    with FakeAuthorizationServer() as fake:
        yield fake
    # No test leaves a request thread behind.
    _wait_until(
        lambda: not [t for t in threading.enumerate() if t.name == "mcp-oauth-fetch"]
    )


def _provider(
    store: McpOAuthStateStore | None = None,
    server_url: str = _SERVER,
    **options: Any,
) -> McpOAuthProvider:
    options.setdefault("client_metadata", {"client_name": "test"})
    options.setdefault("on_redirect", lambda url: None)
    return McpOAuthProvider(
        McpOAuthProviderOptions(
            server_url=server_url, redirect_url=_REDIRECT, store=store, **options
        )
    )


def _stored(store: MemoryOAuthStateStore) -> McpOAuthState:
    state = store.load()
    assert state is not None
    return state


# -- the store ----------------------------------------------------------------------


def test_a_memory_store_is_empty_at_first() -> None:
    assert MemoryOAuthStateStore().load() is None


def test_a_memory_store_keeps_a_copy() -> None:
    store = MemoryOAuthStateStore()
    state: McpOAuthState = {"serverUrl": _SERVER, "tokens": {"access_token": "a"}}
    store.save(state)
    state["tokens"]["access_token"] = "changed after saving"
    loaded = _stored(store)
    assert loaded == {"serverUrl": _SERVER, "tokens": {"access_token": "a"}}
    loaded["tokens"]["access_token"] = "changed after loading"
    assert _stored(store)["tokens"]["access_token"] == "a"


# -- which state is a provider's ----------------------------------------------------


def test_binds_persisted_credentials_to_the_exact_mcp_server_url() -> None:
    # Pi: "binds persisted credentials to the exact MCP server URL"
    store = MemoryOAuthStateStore()
    first = _provider(store, "https://one.example/mcp")
    first.save_tokens({"access_token": "secret", "token_type": "Bearer"})
    tokens = first.tokens()
    assert tokens is not None
    assert tokens["access_token"] == "secret"

    second = _provider(store, "https://two.example/mcp")
    assert second.tokens() is None


def test_nothing_stored_for_another_server_url_is_used() -> None:
    store = MemoryOAuthStateStore()
    store.save(
        {
            "serverUrl": "https://one.example/mcp",
            "clientInformation": {"client_id": "client"},
            "tokens": {"access_token": "secret", "token_type": "Bearer"},
            "tokensExpireAt": 1,
            "codeVerifier": "verifier",
            "oauthState": "state-of-one",
            "discovery": {"authorizationServerUrl": "https://as.example"},
        }
    )
    other = _provider(store, "https://one.example/mcp/other")
    assert other.client_information() is None
    assert other.tokens() is None
    assert other.discovery_state() is None
    with pytest.raises(OAuthFlowError):
        other.code_verifier()
    # Reading does not touch the other server's state.
    assert _stored(store)["serverUrl"] == "https://one.example/mcp"
    assert other.state() != "state-of-one"
    # The first write replaces it as a whole.
    assert _stored(store) == {
        "serverUrl": "https://one.example/mcp/other",
        "oauthState": other.state(),
    }


@pytest.mark.parametrize(
    "other",
    [
        "https://one.example/mcp/",
        "https://one.example/MCP",
        "https://one.example/mcp?tenant=a",
        "https://one.example:8443/mcp",
        "http://one.example/mcp",
        "https://sub.one.example/mcp",
    ],
)
def test_a_different_url_is_a_different_server(other: str) -> None:
    store = MemoryOAuthStateStore()
    _provider(store).save_tokens({"access_token": "secret", "token_type": "Bearer"})
    assert _provider(store, other).tokens() is None


@pytest.mark.parametrize(
    "same",
    [
        "HTTPS://ONE.example/mcp",
        "https://one.example:443/mcp",
        "https://one.example/a/../mcp",
        "https://one.example/mcp#fragment",
        " https://one.example/mcp ",
    ],
)
def test_another_spelling_of_the_url_is_the_same_server(same: str) -> None:
    store = MemoryOAuthStateStore()
    _provider(store).save_tokens({"access_token": "secret", "token_type": "Bearer"})
    assert _provider(store, same).tokens() is not None
    assert _stored(store)["serverUrl"] == _SERVER


@pytest.mark.parametrize(
    "url",
    ["", "one.example/mcp", "ftp://one.example/mcp", "https://user:pw@one.example/mcp"],
)
def test_a_server_url_that_cannot_be_used_is_refused(url: str) -> None:
    with pytest.raises(ValueError, match="Invalid MCP server URL"):
        _provider(server_url=url)


def test_a_stored_document_that_is_no_object_counts_as_empty() -> None:
    class Broken:
        def load(self) -> Any:
            return ["not", "a", "state"]

        def save(self, state: McpOAuthState) -> None:
            raise AssertionError("not written")

    assert _provider(Broken()).tokens() is None


def test_a_store_that_is_false_while_empty_is_used() -> None:
    class Mapping(dict[str, Any]):
        def load(self) -> McpOAuthState | None:
            return dict(self) or None

        def save(self, state: McpOAuthState) -> None:
            self.clear()
            self.update(state)

    store = Mapping()
    assert not store
    _provider(store).save_code_verifier("verifier")
    assert store == {"serverUrl": _SERVER, "codeVerifier": "verifier"}
    assert _provider(store).code_verifier() == "verifier"


def test_without_a_store_the_state_is_kept_in_memory() -> None:
    provider = _provider()
    provider.save_code_verifier("verifier")
    assert provider.code_verifier() == "verifier"


# -- client metadata and the client -------------------------------------------------


def test_client_metadata_gets_the_defaults_of_a_public_client() -> None:
    provider = _provider(client_metadata={"client_name": "pipy", "scope": "read"})
    assert provider.redirect_url == _REDIRECT
    assert provider.client_metadata == {
        "client_name": "pipy",
        "scope": "read",
        "redirect_uris": [_REDIRECT],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


def test_a_client_secret_makes_the_default_method_client_secret_post() -> None:
    provider = _provider(client_id="client", client_secret="secret")
    assert provider.client_metadata["token_endpoint_auth_method"] == (
        "client_secret_post"
    )


def test_given_client_metadata_is_kept_and_not_changed() -> None:
    given = {
        "client_name": "test",
        "redirect_uris": ["http://localhost:1/callback"],
        "grant_types": ["authorization_code"],
        "response_types": ["code", "none"],
        "token_endpoint_auth_method": "client_secret_basic",
    }
    before = {key: list(v) if isinstance(v, list) else v for key, v in given.items()}
    provider = _provider(client_metadata=given, client_secret="secret")
    assert provider.client_metadata == before
    assert given == before
    assert provider.client_metadata is not given


def test_a_registered_client_is_stored() -> None:
    store = MemoryOAuthStateStore()
    provider = _provider(store)
    assert provider.client_information() is None
    provider.save_client_information({"client_id": "registered", "redirect_uris": []})
    assert provider.client_information() == {
        "client_id": "registered",
        "redirect_uris": [],
    }
    assert _stored(store) == {
        "serverUrl": _SERVER,
        "clientInformation": {"client_id": "registered", "redirect_uris": []},
    }


def test_a_configured_client_is_used_and_never_stored() -> None:
    store = MemoryOAuthStateStore()
    store.save({"serverUrl": _SERVER, "clientInformation": {"client_id": "stored"}})
    provider = _provider(store, client_id="configured")
    assert provider.client_information() == {"client_id": "configured"}
    provider.save_client_information({"client_id": "registered"})
    assert provider.client_information() == {"client_id": "configured"}
    assert _stored(store)["clientInformation"] == {"client_id": "stored"}


def test_a_configured_client_has_its_secret() -> None:
    provider = _provider(client_id="configured", client_secret="secret")
    assert provider.client_information() == {
        "client_id": "configured",
        "client_secret": "secret",
    }
    # What a caller does to the answer does not reach the provider.
    information = provider.client_information()
    assert information is not None
    information["client_secret"] = "changed"
    assert provider.client_information() == {
        "client_id": "configured",
        "client_secret": "secret",
    }


def test_a_client_secret_without_a_client_id_configures_no_client() -> None:
    assert _provider(client_secret="secret").client_information() is None


def test_the_client_metadata_document_comes_from_the_given_function() -> None:
    document = OAuthClientMetadataDocument("https://c.example/client.json", _REDIRECT)
    asked: list[AuthorizationServerMetadata | None] = []

    def choose(
        metadata: AuthorizationServerMetadata | None,
    ) -> OAuthClientMetadataDocument | None:
        asked.append(metadata)
        return document

    provider = _provider(client_metadata_document=choose)
    metadata = {"issuer": "https://as.example"}
    assert provider.client_metadata_document(metadata) is document
    assert provider.client_metadata_document(None) is document
    assert asked == [metadata, None]
    assert _provider().client_metadata_document(metadata) is None


# -- tokens, verifier, state, discovery ---------------------------------------------


def test_saved_tokens_record_when_the_access_token_expires() -> None:
    store = MemoryOAuthStateStore()
    provider = _provider(store)
    before = time.time() * 1000
    provider.save_tokens(
        {"access_token": "a", "token_type": "Bearer", "expires_in": 3600}
    )
    after = time.time() * 1000
    state = _stored(store)
    assert state["tokens"] == {
        "access_token": "a",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    expires_at = state["tokensExpireAt"]
    assert isinstance(expires_at, int)
    assert before + 3_600_000 - 1 <= expires_at <= after + 3_600_000


def test_a_fraction_of_a_second_counts_in_the_expiry() -> None:
    store = MemoryOAuthStateStore()
    provider = _provider(store)
    before = time.time() * 1000
    provider.save_tokens(
        {"access_token": "a", "token_type": "Bearer", "expires_in": 0.5}
    )
    assert before + 499 <= _stored(store)["tokensExpireAt"] <= before + 5000


def test_tokens_without_a_lifetime_remove_the_recorded_expiry() -> None:
    store = MemoryOAuthStateStore()
    provider = _provider(store)
    provider.save_tokens(
        {"access_token": "a", "token_type": "Bearer", "expires_in": 60}
    )
    provider.save_tokens({"access_token": "b", "token_type": "Bearer"})
    assert _stored(store) == {
        "serverUrl": _SERVER,
        "tokens": {"access_token": "b", "token_type": "Bearer"},
    }
    assert provider.tokens() == {"access_token": "b", "token_type": "Bearer"}


def test_the_code_verifier_is_stored() -> None:
    store = MemoryOAuthStateStore()
    provider = _provider(store)
    with pytest.raises(OAuthFlowError) as raised:
        provider.code_verifier()
    assert str(raised.value) == "No OAuth PKCE code verifier is stored"
    provider.save_code_verifier("verifier")
    assert provider.code_verifier() == "verifier"
    assert _provider(store).code_verifier() == "verifier"


def test_the_state_parameter_is_32_random_bytes_in_hex() -> None:
    store = MemoryOAuthStateStore()
    provider = _provider(store)
    state = provider.state()
    assert re.fullmatch("[0-9a-f]{64}", state)
    # It stays the same until it is forgotten.
    assert provider.state() == state
    assert _provider(store).state() == state
    assert _stored(store)["oauthState"] == state
    assert _provider().state() != state


def test_a_forgotten_state_parameter_is_replaced_by_a_new_one() -> None:
    store = MemoryOAuthStateStore()
    provider = _provider(store)
    first = provider.state()
    # A sign-in removes the key from the stored state to get a new value.
    state = _stored(store)
    del state["oauthState"]
    store.save(state)
    second = provider.state()
    assert second != first
    provider.invalidate_credentials("all")
    assert provider.state() not in (first, second)


def test_concurrent_first_calls_agree_on_the_state_parameter() -> None:
    class Slow(MemoryOAuthStateStore):
        def load(self) -> McpOAuthState | None:
            state = super().load()
            time.sleep(0.01)
            return state

    provider = _provider(Slow())
    states: list[str] = []
    threads = [
        threading.Thread(target=lambda: states.append(provider.state()))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(_WAIT_SECONDS)
    assert len(states) == 8
    assert len(set(states)) == 1


def test_discovery_state_is_stored() -> None:
    store = MemoryOAuthStateStore()
    provider = _provider(store)
    assert provider.discovery_state() is None
    discovery = {
        "authorizationServerUrl": "https://as.example",
        "resourceMetadata": {"resource": _SERVER},
    }
    provider.save_discovery_state(discovery)
    assert provider.discovery_state() == discovery
    assert _stored(store) == {"serverUrl": _SERVER, "discovery": discovery}


def test_the_authorization_url_is_handed_to_on_redirect() -> None:
    urls: list[str] = []
    provider = _provider(on_redirect=urls.append)
    provider.redirect_to_authorization("https://as.example/authorize?x=1")
    assert urls == ["https://as.example/authorize?x=1"]


_FULL: McpOAuthState = {
    "serverUrl": _SERVER,
    "clientInformation": {"client_id": "client"},
    "tokens": {"access_token": "a", "token_type": "Bearer"},
    "tokensExpireAt": 5,
    "codeVerifier": "verifier",
    "oauthState": "state",
    "discovery": {"authorizationServerUrl": "https://as.example"},
}


@pytest.mark.parametrize(
    ("kind", "removed"),
    [
        ("client", ["clientInformation"]),
        ("tokens", ["tokens", "tokensExpireAt"]),
        ("verifier", ["codeVerifier"]),
        ("discovery", ["discovery"]),
        (
            "all",
            [
                "clientInformation",
                "tokens",
                "tokensExpireAt",
                "codeVerifier",
                "discovery",
                "oauthState",
            ],
        ),
    ],
)
def test_invalidating_removes_the_keys_of_its_kind(
    kind: InvalidationKind, removed: list[str]
) -> None:
    store = MemoryOAuthStateStore()
    store.save(_FULL)
    _provider(store).invalidate_credentials(kind)
    assert _stored(store) == {k: v for k, v in _FULL.items() if k not in removed}


def test_changes_from_several_threads_are_all_kept() -> None:
    class Slow(MemoryOAuthStateStore):
        def load(self) -> McpOAuthState | None:
            state = super().load()
            # Long enough for another thread to write between a load and its
            # save, were the two not one step.
            time.sleep(0.002)
            return state

    store = Slow()
    provider = _provider(store)
    changes: list[Callable[[], object]] = [
        lambda: provider.save_tokens({"access_token": "a", "token_type": "Bearer"}),
        lambda: provider.save_code_verifier("verifier"),
        lambda: provider.save_client_information({"client_id": "client"}),
        lambda: provider.save_discovery_state({"authorizationServerUrl": "https://a"}),
        provider.state,
    ]
    for _ in range(10):
        store.save({"serverUrl": _SERVER})
        threads = [threading.Thread(target=change) for change in changes]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(_WAIT_SECONDS)
        assert sorted(_stored(store)) == [
            "clientInformation",
            "codeVerifier",
            "discovery",
            "oauthState",
            "serverUrl",
            "tokens",
        ]


# -- with the flow ------------------------------------------------------------------


def test_a_sign_in_through_the_provider_stores_the_whole_state(
    server: FakeAuthorizationServer,
) -> None:
    store = MemoryOAuthStateStore()
    urls: list[str] = []
    server_url = f"{server.origin}/mcp"
    provider = _provider(
        store,
        server_url,
        client_metadata={"client_name": "pipy"},
        on_redirect=urls.append,
    )
    options = OAuthFlowOptions(server_url=server_url, scope="read")
    assert authorize_mcp(provider, options) == "REDIRECT"
    # OAU-08: what a dynamic registration posts.
    assert server.registrations == [
        {
            "client_name": "pipy",
            "redirect_uris": [_REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "application_type": "native",
            "scope": "read",
        }
    ]
    (url,) = urls
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert query["state"] == provider.state()
    assert query["client_id"] == "client-1"

    response = authorization_response(url)
    assert response["state"] == provider.state()
    with_code = OAuthFlowOptions(
        server_url=server_url, scope="read", authorization_code=response["code"]
    )
    assert authorize_mcp(provider, with_code) == "AUTHORIZED"
    state = _stored(store)
    assert sorted(state) == [
        "clientInformation",
        "codeVerifier",
        "discovery",
        "oauthState",
        "serverUrl",
        "tokens",
        "tokensExpireAt",
    ]
    assert state["serverUrl"] == server_url
    assert state["clientInformation"]["client_id"] == "client-1"
    assert state["tokens"] == {
        "access_token": "access-4",
        "refresh_token": "refresh-3",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": "read",
    }
    assert state["discovery"] == {
        "authorizationServerUrl": f"{server.origin}/",
        "authorizationServerMetadata": server.metadata,
    }
    assert state["tokensExpireAt"] > time.time() * 1000 + 3_500_000


def test_a_refresh_through_the_provider_stores_the_rotated_tokens(
    server: FakeAuthorizationServer,
) -> None:
    # Pi: "shares one refresh between concurrent 401s when refresh tokens
    # rotate", the provider's part: the new grant and its expiry are stored.
    server.add_client("client")
    server.refresh_tokens["r1"] = "client"
    store = MemoryOAuthStateStore()
    server_url = f"{server.origin}/mcp"
    provider = _provider(store, server_url, client_id="client")
    provider.save_tokens(
        {"access_token": "a1", "refresh_token": "r1", "token_type": "Bearer"}
    )
    assert "tokensExpireAt" not in _stored(store)
    options = OAuthFlowOptions(server_url=server_url)
    assert authorize_mcp(provider, options) == "AUTHORIZED"
    assert [f["refresh_token"] for f in server.token_forms] == ["r1"]
    state = _stored(store)
    assert state["tokens"]["access_token"] == "access-2"
    assert state["tokens"]["refresh_token"] == "refresh-1"
    assert state["tokensExpireAt"] > time.time() * 1000 + 3_500_000
    # The configured client is not stored.
    assert "clientInformation" not in state


def test_a_refused_grant_is_forgotten_and_the_user_is_asked_again(
    server: FakeAuthorizationServer,
) -> None:
    server.add_client("client")
    store = MemoryOAuthStateStore()
    urls: list[str] = []
    server_url = f"{server.origin}/mcp"
    provider = _provider(store, server_url, client_id="client", on_redirect=urls.append)
    provider.save_tokens(
        {
            "access_token": "a1",
            "refresh_token": "unknown",
            "token_type": "Bearer",
            "expires_in": 60,
        }
    )
    assert authorize_mcp(provider, OAuthFlowOptions(server_url=server_url)) == (
        "REDIRECT"
    )
    state = _stored(store)
    assert "tokens" not in state
    assert "tokensExpireAt" not in state
    assert len(urls) == 1
    assert len(server.token_forms) == 1
