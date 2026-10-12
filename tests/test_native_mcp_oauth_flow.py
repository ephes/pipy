"""Tests for the OAuth flow, against the fake authorization server.

Tests that mirror a case of Pi's ``packages/mcp/test/oauth.test.ts`` name it
in a ``Pi:`` comment. Where a Pi case drives the flow through
``adaptOAuthProvider`` and an MCP connection, the flow is called here as the
adapter calls it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.mcp.oauth.discovery import parse_www_authenticate
from pipy_harness.native.mcp.oauth.errors import (
    OAuthError,
    OAuthFlowError,
    OAuthInsecureEndpointError,
    OAuthIssuerMismatchError,
    OAuthRegistrationError,
)
from pipy_harness.native.mcp.oauth.fetch import OAuthResponse
from pipy_harness.native.mcp.oauth.flow import (
    InvalidationKind,
    OAuthClientMetadataDocument,
    OAuthDiscoveryState,
    OAuthFlowOptions,
    TokenRequestOptions,
    authorize_mcp,
    exchange_authorization_code,
    refresh_authorization,
    register_client,
    start_authorization,
)
from pipy_harness.native.mcp.oauth.types import (
    AuthorizationServerMetadata,
    OAuthClientInformation,
    OAuthTokens,
)
from pipy_harness.native.mcp.protocol import McpAbortError

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_oauth import (  # noqa: E402
    FakeAuthorizationServer,
    FakeOAuthServer,
    OAuthRequest,
    Reply,
    authorization_response,
    error_reply,
    form,
    json_reply,
)

_WAIT_SECONDS = 10.0
_PRM = "/.well-known/oauth-protected-resource"
_AS = "/.well-known/oauth-authorization-server"
_REDIRECT = "http://127.0.0.1/callback"


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.005)


def _fetch_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "mcp-oauth-fetch"]


@pytest.fixture
def server() -> Iterator[FakeAuthorizationServer]:
    with FakeAuthorizationServer() as fake:
        yield fake
    # No test leaves a request thread behind.
    _wait_until(lambda: not _fetch_threads())


@pytest.fixture
def plain() -> Iterator[FakeOAuthServer]:
    """A server with no routes but the ones a test adds."""

    with FakeOAuthServer() as fake:
        yield fake
    _wait_until(lambda: not _fetch_threads())


class _Provider:
    """A provider that keeps everything in attributes, as Pi's test provider."""

    def __init__(self, redirect_url: str = _REDIRECT) -> None:
        self.redirect_url = redirect_url
        self.client_metadata: dict[str, Any] = {
            "redirect_uris": [redirect_url],
            "client_name": "pipy-mcp-test",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        self.client: OAuthClientInformation | None = None
        self.token_set: OAuthTokens | None = None
        self.verifier: str | None = None
        self.discovery: OAuthDiscoveryState | None = None
        self.authorization_url: str | None = None
        self.invalidated: list[InvalidationKind] = []

    def state(self) -> str | None:
        return "expected-state"

    def client_information(self) -> OAuthClientInformation | None:
        return self.client

    def save_client_information(self, information: OAuthClientInformation) -> None:
        self.client = information

    def tokens(self) -> OAuthTokens | None:
        return self.token_set

    def save_tokens(self, tokens: OAuthTokens) -> None:
        self.token_set = tokens

    def redirect_to_authorization(self, url: str) -> None:
        self.authorization_url = url

    def save_code_verifier(self, verifier: str) -> None:
        self.verifier = verifier

    def code_verifier(self) -> str:
        if not self.verifier:
            raise AssertionError("Missing code verifier")
        return self.verifier

    def invalidate_credentials(self, kind: InvalidationKind) -> None:
        self.invalidated.append(kind)
        if kind in ("all", "client"):
            self.client = None
        if kind in ("all", "tokens"):
            self.token_set = None
        if kind in ("all", "verifier"):
            self.verifier = None
        if kind in ("all", "discovery"):
            self.discovery = None

    def save_discovery_state(self, state: OAuthDiscoveryState) -> None:
        self.discovery = state

    def discovery_state(self) -> OAuthDiscoveryState | None:
        return self.discovery


class _BareProvider:
    """A provider with the required members only."""

    def __init__(self, client: OAuthClientInformation | None = None) -> None:
        self.redirect_url = _REDIRECT
        self.client_metadata: dict[str, Any] = {"redirect_uris": [_REDIRECT]}
        self.client = client
        self.token_set: OAuthTokens | None = None
        self.verifier = ""
        self.authorization_url: str | None = None

    def client_information(self) -> OAuthClientInformation | None:
        return self.client

    def tokens(self) -> OAuthTokens | None:
        return self.token_set

    def save_tokens(self, tokens: OAuthTokens) -> None:
        self.token_set = tokens

    def redirect_to_authorization(self, url: str) -> None:
        self.authorization_url = url

    def save_code_verifier(self, verifier: str) -> None:
        self.verifier = verifier

    def code_verifier(self) -> str:
        return self.verifier


def _query(url: str | None) -> dict[str, str]:
    assert url is not None
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query, True))


def _metadata(issuer: str, **fields: Any) -> AuthorizationServerMetadata:
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "response_types_supported": ["code"],
        **fields,
    }


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class _Fetch:
    """A fetch that records its calls and answers with scripted responses."""

    def __init__(self, *responses: OAuthResponse) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        cancel_event: threading.Event | None = None,
    ) -> OAuthResponse:
        self.calls.append(
            {
                "url": url,
                "method": method,
                "headers": dict(headers or {}),
                "body": body,
                "cancel_event": cancel_event,
            }
        )
        return self.responses.pop(0)

    @property
    def form(self) -> dict[str, str]:
        """The form fields of the last request."""

        body: bytes = self.calls[-1]["body"]
        return dict(urllib.parse.parse_qsl(body.decode("ascii"), True))


def _json_response(document: Any, status: int = 200) -> OAuthResponse:
    return OAuthResponse(
        status, {"content-type": "application/json"}, json.dumps(document).encode()
    )


_TOKENS: OAuthTokens = {"access_token": "a1", "token_type": "Bearer"}


# -- the authorization request ------------------------------------------------------


def test_the_authorization_url_carries_the_request() -> None:
    request = start_authorization(
        "https://as.example",
        client_information={"client_id": "client"},
        redirect_url="http://127.0.0.1:1234/callback",
        metadata=_metadata("https://as.example"),
        scope="read write",
        state="s1",
        resource="https://mcp.example/mcp",
    )
    address, _, query = request.authorization_url.partition("?")
    assert address == "https://as.example/authorize"
    assert dict(urllib.parse.parse_qsl(query)) == {
        "response_type": "code",
        "client_id": "client",
        "code_challenge": _s256(request.code_verifier),
        "code_challenge_method": "S256",
        "redirect_uri": "http://127.0.0.1:1234/callback",
        "state": "s1",
        "scope": "read write",
        "resource": "https://mcp.example/mcp",
    }
    # 32 random bytes in URL-safe base64 without padding.
    assert len(request.code_verifier) == 43
    assert set(request.code_verifier) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )


def test_every_authorization_request_has_its_own_verifier() -> None:
    def verifier() -> str:
        return start_authorization(
            "https://as.example",
            client_information={"client_id": "client"},
            redirect_url=_REDIRECT,
        ).code_verifier

    assert verifier() != verifier()


def test_state_scope_and_resource_are_left_out_without_a_value() -> None:
    request = start_authorization(
        "https://as.example",
        client_information={"client_id": "client"},
        redirect_url=_REDIRECT,
        scope="",
        state="",
        resource="",
    )
    assert sorted(_query(request.authorization_url)) == [
        "client_id",
        "code_challenge",
        "code_challenge_method",
        "redirect_uri",
        "response_type",
    ]


@pytest.mark.parametrize(
    ("scope", "prompt"),
    [
        ("offline_access", "consent"),
        ("read offline_access write", "consent"),
        ("read offline_accessible", None),
        ("read", None),
    ],
)
def test_offline_access_asks_for_consent(scope: str, prompt: str | None) -> None:
    request = start_authorization(
        "https://as.example",
        client_information={"client_id": "client"},
        redirect_url=_REDIRECT,
        scope=scope,
    )
    assert _query(request.authorization_url).get("prompt") == prompt


def test_without_metadata_the_authorization_endpoint_is_the_default_one() -> None:
    request = start_authorization(
        "https://as.example/tenant/",
        client_information={"client_id": "client"},
        redirect_url=_REDIRECT,
    )
    assert request.authorization_url.startswith("https://as.example/authorize?")


def test_the_query_and_fragment_of_the_authorization_endpoint_are_kept() -> None:
    metadata = _metadata(
        "https://as.example",
        authorization_endpoint=(
            "https://as.example/authorize?tenant=a%20b&client_id=old&x=1"
            "&client_id=older#frag"
        ),
    )
    request = start_authorization(
        "https://as.example",
        client_information={"client_id": "new id"},
        redirect_url=_REDIRECT,
        metadata=metadata,
    )
    url = request.authorization_url
    assert url.startswith(
        "https://as.example/authorize?tenant=a+b&client_id=new+id&x=1&response_type="
    )
    assert url.endswith("#frag")
    assert url.count("client_id=") == 1


def test_a_server_without_the_code_response_type_fails() -> None:
    metadata = _metadata("https://as.example", response_types_supported=["token"])
    with pytest.raises(OAuthFlowError) as raised:
        start_authorization(
            "https://as.example",
            client_information={"client_id": "client"},
            redirect_url=_REDIRECT,
            metadata=metadata,
        )
    assert str(raised.value) == (
        "Authorization server does not support authorization codes"
    )


def test_a_server_without_s256_fails() -> None:
    metadata = _metadata(
        "https://as.example", code_challenge_methods_supported=["plain"]
    )
    with pytest.raises(OAuthFlowError) as raised:
        start_authorization(
            "https://as.example",
            client_information={"client_id": "client"},
            redirect_url=_REDIRECT,
            metadata=metadata,
        )
    assert str(raised.value) == "Authorization server does not support PKCE S256"


def test_client_information_without_a_client_id_fails() -> None:
    with pytest.raises(OAuthFlowError, match="Invalid client_id"):
        start_authorization(
            "https://as.example", client_information={}, redirect_url=_REDIRECT
        )


# -- dynamic client registration ----------------------------------------------------


def test_registers_with_an_application_type_derived_from_the_redirect_uris(
    plain: FakeOAuthServer,
) -> None:
    # Pi: "registers with an application_type derived from the redirect URIs
    # unless one is set" (#10493)
    def register(request: OAuthRequest) -> Reply:
        return json_reply({**json.loads(request.body), "client_id": "client"}, 201)

    plain.route("/register", register)

    def run(redirect_uris: list[str], application_type: str | None = None) -> None:
        metadata: dict[str, Any] = {"redirect_uris": redirect_uris}
        if application_type:
            metadata["application_type"] = application_type
        register_client(plain.origin, client_metadata=metadata)

    run(["http://127.0.0.1:1234/callback"])
    run(["http://[::1]/callback"])
    run(["com.example.app:/callback"])
    run(["https://app.example/callback"])
    run(["http://localhost/callback"], "web")
    run(["not a url", "https://app.example/callback"])
    run(["https://app.example/callback", "http://LOCALHOST:8080/callback"])
    assert [json.loads(r.body)["application_type"] for r in plain.requests] == [
        "native",
        "native",
        "native",
        "web",
        "web",
        "web",
        "native",
    ]


def test_the_registration_request_carries_the_client_metadata_and_the_scope(
    server: FakeAuthorizationServer,
) -> None:
    client = register_client(
        server.origin,
        client_metadata={
            "client_name": "pipy",
            "redirect_uris": ["http://127.0.0.1:4000/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
        metadata=server.metadata,
        scope="read write",
    )
    (request,) = server.requests
    assert (request.method, request.path) == ("POST", "/register")
    assert request.headers["content-type"] == "application/json"
    assert request.headers["accept"] == "application/json"
    assert json.loads(request.body) == {
        "client_name": "pipy",
        "redirect_uris": ["http://127.0.0.1:4000/callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "application_type": "native",
        "scope": "read write",
    }
    assert client["client_id"] == "client-1"
    assert client["redirect_uris"] == ["http://127.0.0.1:4000/callback"]


def test_the_scope_of_the_client_metadata_is_sent_when_none_is_requested() -> None:
    fetch = _Fetch(_json_response({"client_id": "c"}, 201))
    register_client(
        "https://as.example",
        client_metadata={"redirect_uris": [_REDIRECT], "scope": "configured"},
        fetch=fetch,
    )
    assert json.loads(fetch.calls[0]["body"])["scope"] == "configured"


def test_without_metadata_registration_uses_the_default_endpoint() -> None:
    fetch = _Fetch(_json_response({"client_id": "c", "client_secret": ""}, 201))
    cancel = threading.Event()
    client = register_client(
        "https://as.example/tenant",
        client_metadata={"redirect_uris": [_REDIRECT], "client_name": "Zoë"},
        fetch=fetch,
        cancel_event=cancel,
    )
    (call,) = fetch.calls
    assert call["url"] == "https://as.example/register"
    assert call["method"] == "POST"
    assert call["cancel_event"] is cancel
    # Sent as UTF-8, not as ASCII escapes.
    assert "Zoë".encode() in call["body"]
    # Empty and null optional fields count as absent (#10266).
    assert client == {"client_id": "c", "redirect_uris": []}


def test_a_server_without_a_registration_endpoint_cannot_register() -> None:
    fetch = _Fetch()
    with pytest.raises(OAuthFlowError) as raised:
        register_client(
            "https://as.example",
            client_metadata={"redirect_uris": [_REDIRECT]},
            metadata=_metadata("https://as.example"),
            fetch=fetch,
        )
    assert str(raised.value) == (
        "Authorization server does not support dynamic client registration"
    )
    assert fetch.calls == []


def test_a_refused_registration_reports_status_and_body() -> None:
    fetch = _Fetch(OAuthResponse(403, {}, b"clients are registered by hand"))
    with pytest.raises(OAuthRegistrationError) as raised:
        register_client(
            "https://as.example",
            client_metadata={"redirect_uris": [_REDIRECT]},
            fetch=fetch,
        )
    assert (raised.value.status, raised.value.body) == (
        403,
        "clients are registered by hand",
    )
    assert str(raised.value) == (
        "OAuth dynamic client registration failed with status 403: "
        "clients are registered by hand"
    )


@pytest.mark.parametrize("body", [b"<html>", b"[]", b'{"client_id": ""}'])
def test_a_registration_answer_that_is_no_client_fails(body: bytes) -> None:
    with pytest.raises(OAuthFlowError):
        register_client(
            "https://as.example",
            client_metadata={"redirect_uris": [_REDIRECT]},
            fetch=_Fetch(OAuthResponse(201, {}, body)),
        )


# -- token requests -----------------------------------------------------------------


def test_a_code_exchange_posts_the_grant_as_a_form() -> None:
    fetch = _Fetch(_json_response(_TOKENS))
    cancel = threading.Event()
    tokens = exchange_authorization_code(
        "https://as.example",
        TokenRequestOptions(
            client_information={"client_id": "client"},
            metadata=_metadata("https://as.example"),
            resource="https://mcp.example/mcp",
            fetch=fetch,
            cancel_event=cancel,
        ),
        code="the code",
        code_verifier="verifier",
        redirect_url=_REDIRECT,
    )
    assert tokens == _TOKENS
    (call,) = fetch.calls
    assert call["url"] == "https://as.example/token"
    assert call["method"] == "POST"
    assert call["headers"] == {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    assert call["cancel_event"] is cancel
    assert fetch.form == {
        "grant_type": "authorization_code",
        "code": "the code",
        "code_verifier": "verifier",
        "redirect_uri": _REDIRECT,
        "resource": "https://mcp.example/mcp",
        "client_id": "client",
    }


def test_without_metadata_the_token_endpoint_is_the_default_one() -> None:
    fetch = _Fetch(_json_response(_TOKENS))
    refresh_authorization(
        "https://as.example/tenant",
        TokenRequestOptions(client_information={"client_id": "client"}, fetch=fetch),
        refresh_token="r1",
    )
    assert fetch.calls[0]["url"] == "https://as.example/token"
    assert fetch.form == {
        "grant_type": "refresh_token",
        "refresh_token": "r1",
        "client_id": "client",
    }


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://idp.example/token",
        "http://127.0.0.2/token",
        "http://localhost.example/token",
        "ftp://127.0.0.1/token",
        "not a url",
    ],
)
def test_tokens_are_not_requested_from_an_insecure_endpoint(endpoint: str) -> None:
    fetch = _Fetch()
    with pytest.raises(OAuthInsecureEndpointError) as raised:
        refresh_authorization(
            "https://as.example",
            TokenRequestOptions(
                client_information={"client_id": "client", "client_secret": "s"},
                metadata=_metadata("https://as.example", token_endpoint=endpoint),
                fetch=fetch,
            ),
            refresh_token="r1",
        )
    assert str(raised.value) == (
        f"Refusing to send OAuth credentials to non-HTTPS endpoint {endpoint}"
    )
    assert fetch.calls == []


def test_the_default_token_endpoint_of_an_insecure_server_is_refused() -> None:
    fetch = _Fetch()
    with pytest.raises(OAuthInsecureEndpointError):
        refresh_authorization(
            "http://as.example",
            TokenRequestOptions(
                client_information={"client_id": "client"}, fetch=fetch
            ),
            refresh_token="r1",
        )
    assert fetch.calls == []


def test_an_insecure_endpoint_is_named_without_its_user_info() -> None:
    with pytest.raises(OAuthInsecureEndpointError) as raised:
        refresh_authorization(
            "https://as.example",
            TokenRequestOptions(
                client_information={"client_id": "client"},
                metadata=_metadata(
                    "https://as.example",
                    token_endpoint="http://user:secret@idp.example/token",
                ),
                fetch=_Fetch(),
            ),
            refresh_token="r1",
        )
    assert raised.value.endpoint == "http://idp.example/token"


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://idp.example/token",
        "http://localhost:8080/token",
        "http://127.0.0.1/token",
        "http://[::1]:9/token",
    ],
)
def test_tokens_are_requested_from_https_and_loopback_endpoints(endpoint: str) -> None:
    fetch = _Fetch(_json_response(_TOKENS))
    refresh_authorization(
        "https://as.example",
        TokenRequestOptions(
            client_information={"client_id": "client"},
            metadata=_metadata("https://as.example", token_endpoint=endpoint),
            fetch=fetch,
        ),
        refresh_token="r1",
    )
    assert fetch.calls[0]["url"] == endpoint


_BASIC = "Basic " + base64.b64encode(b"client:secret").decode()


@pytest.mark.parametrize(
    ("client", "supported", "expected"),
    [
        # The registered method, when the server supports it or says nothing.
        ({"token_endpoint_auth_method": "client_secret_post"}, None, "post"),
        (
            {"token_endpoint_auth_method": "client_secret_post"},
            ["client_secret_basic", "client_secret_post"],
            "post",
        ),
        ({"token_endpoint_auth_method": "none"}, ["client_secret_basic"], "basic"),
        (
            {"token_endpoint_auth_method": "none"},
            ["none", "client_secret_post"],
            "none",
        ),
        ({"token_endpoint_auth_method": "private_key_jwt"}, None, "basic"),
        # Else what the server supports: basic, post, none.
        ({}, None, "basic"),
        ({}, [], "basic"),
        ({}, ["client_secret_post", "client_secret_basic"], "basic"),
        ({}, ["client_secret_post", "none"], "post"),
        ({}, ["none"], "none"),
        ({}, ["private_key_jwt"], "post"),
    ],
)
def test_a_client_with_a_secret_authenticates_as_registered_or_as_the_server_supports(
    client: dict[str, str], supported: list[str] | None, expected: str
) -> None:
    fetch = _Fetch(_json_response(_TOKENS))
    metadata = _metadata("https://as.example")
    if supported is not None:
        metadata["token_endpoint_auth_methods_supported"] = supported
    refresh_authorization(
        "https://as.example",
        TokenRequestOptions(
            client_information={
                "client_id": "client",
                "client_secret": "secret",
                **client,
            },
            metadata=metadata,
            fetch=fetch,
        ),
        refresh_token="r1",
    )
    sent = (
        fetch.calls[0]["headers"].get("Authorization"),
        fetch.form.get("client_id"),
        fetch.form.get("client_secret"),
    )
    assert (
        sent
        == {
            "basic": (_BASIC, None, None),
            "post": (None, "client", "secret"),
            "none": (None, "client", None),
        }[expected]
    )


@pytest.mark.parametrize(
    ("client", "supported"),
    [
        ({}, None),
        ({}, ["client_secret_basic", "client_secret_post"]),
        ({}, ["none"]),
        ({"token_endpoint_auth_method": "client_secret_post"}, None),
        ({"client_secret": ""}, None),
    ],
)
def test_a_client_without_a_secret_sends_its_id_only(
    client: dict[str, str], supported: list[str] | None
) -> None:
    fetch = _Fetch(_json_response(_TOKENS))
    metadata = _metadata("https://as.example")
    if supported is not None:
        metadata["token_endpoint_auth_methods_supported"] = supported
    refresh_authorization(
        "https://as.example",
        TokenRequestOptions(
            client_information={"client_id": "client", **client},
            metadata=metadata,
            fetch=fetch,
        ),
        refresh_token="r1",
    )
    assert "Authorization" not in fetch.calls[0]["headers"]
    assert fetch.form == {
        "grant_type": "refresh_token",
        "refresh_token": "r1",
        "client_id": "client",
    }


def test_basic_authentication_registered_without_a_secret_fails() -> None:
    fetch = _Fetch()
    with pytest.raises(OAuthFlowError) as raised:
        refresh_authorization(
            "https://as.example",
            TokenRequestOptions(
                client_information={
                    "client_id": "client",
                    "token_endpoint_auth_method": "client_secret_basic",
                },
                fetch=fetch,
            ),
            refresh_token="r1",
        )
    assert str(raised.value) == "client_secret_basic requires a client secret"
    assert fetch.calls == []


def test_a_provider_can_add_the_client_authentication_itself() -> None:
    fetch = _Fetch(_json_response(_TOKENS))
    metadata = _metadata("https://as.example")
    seen: list[Any] = []

    def authenticate(
        headers: dict[str, str],
        params: dict[str, str],
        url: str,
        given: AuthorizationServerMetadata | None,
    ) -> None:
        seen.extend([url, given, dict(params)])
        headers["Authorization"] = "Custom credential"
        params["client_assertion"] = "signed"

    refresh_authorization(
        "https://as.example",
        TokenRequestOptions(
            client_information={"client_id": "client", "client_secret": "secret"},
            metadata=metadata,
            resource="https://mcp.example/mcp",
            add_client_authentication=authenticate,
            fetch=fetch,
        ),
        refresh_token="r1",
    )
    assert seen == [
        "https://as.example/token",
        metadata,
        {
            "grant_type": "refresh_token",
            "refresh_token": "r1",
            "resource": "https://mcp.example/mcp",
        },
    ]
    assert fetch.calls[0]["headers"]["Authorization"] == "Custom credential"
    assert fetch.form == {
        "grant_type": "refresh_token",
        "refresh_token": "r1",
        "resource": "https://mcp.example/mcp",
        "client_assertion": "signed",
    }


def _refresh_with(response: OAuthResponse) -> OAuthTokens:
    return refresh_authorization(
        "https://as.example",
        TokenRequestOptions(
            client_information={"client_id": "client"}, fetch=_Fetch(response)
        ),
        refresh_token="r1",
    )


@pytest.mark.parametrize("status", [200, 400, 401, 500])
def test_an_error_field_is_an_oauth_error_whatever_the_status(status: int) -> None:
    document = {
        "error": "invalid_scope",
        "error_description": "No such scope",
        "error_uri": "https://as.example/errors#scope",
    }
    with pytest.raises(OAuthError) as raised:
        _refresh_with(_json_response(document, status))
    assert raised.value.code == "invalid_scope"
    assert str(raised.value) == "No such scope"
    assert raised.value.error_uri == "https://as.example/errors#scope"


def test_an_oauth_error_without_a_description_is_named_by_its_code() -> None:
    with pytest.raises(OAuthError) as raised:
        _refresh_with(
            _json_response({"error": "invalid_grant", "error_description": 7}, 400)
        )
    assert (raised.value.code, str(raised.value), raised.value.error_uri) == (
        "invalid_grant",
        "invalid_grant",
        None,
    )


@pytest.mark.parametrize(
    "body", [b"Bad gateway", b'{"error": 5}', b'{"access_token": "a"}', b"[]"]
)
def test_another_failure_status_is_a_server_error(body: bytes) -> None:
    with pytest.raises(OAuthError) as raised:
        _refresh_with(OAuthResponse(502, {}, body))
    assert raised.value.code == "server_error"
    assert str(raised.value) == f"HTTP 502: {body.decode()}"


@pytest.mark.parametrize("body", [b"<html>", b"[]", b"{}", b'{"access_token": "a"}'])
def test_a_success_that_is_no_token_response_fails(body: bytes) -> None:
    with pytest.raises(OAuthFlowError):
        _refresh_with(OAuthResponse(200, {}, body))


def test_empty_and_null_optional_token_fields_count_as_absent() -> None:
    tokens = _refresh_with(
        _json_response(
            {
                "access_token": "a2",
                "token_type": "Bearer",
                "refresh_token": "",
                "expires_in": None,
                "scope": "",
            }
        )
    )
    # A refresh without a new refresh token keeps the old one.
    assert tokens == {
        "refresh_token": "r1",
        "access_token": "a2",
        "token_type": "Bearer",
    }


def test_a_refresh_takes_a_new_refresh_token() -> None:
    tokens = _refresh_with(
        _json_response(
            {
                "access_token": "a2",
                "token_type": "Bearer",
                "refresh_token": "r2",
                "expires_in": 60,
            }
        )
    )
    assert tokens["refresh_token"] == "r2"
    assert tokens["expires_in"] == 60


def test_a_redirected_token_request_is_not_followed(
    server: FakeAuthorizationServer,
) -> None:
    server.token_replies.append(
        Reply(307, {"Location": f"{server.origin}/elsewhere"}, b"moved")
    )
    with pytest.raises(OAuthError) as raised:
        refresh_authorization(
            server.origin,
            TokenRequestOptions(
                client_information={"client_id": "client"}, metadata=server.metadata
            ),
            refresh_token="r1",
        )
    assert (raised.value.code, str(raised.value)) == ("server_error", "HTTP 307: moved")
    assert server.paths == ["/token"]


# -- the flow -----------------------------------------------------------------------


def _resource_metadata(server: FakeOAuthServer, **fields: Any) -> dict[str, Any]:
    document = {
        "resource": f"{server.origin}/mcp",
        "authorization_servers": [server.origin],
        **fields,
    }
    server.json(f"{_PRM}/mcp", document)
    return document


def _options(server: FakeOAuthServer, **fields: Any) -> OAuthFlowOptions:
    return OAuthFlowOptions(server_url=f"{server.origin}/mcp", **fields)


def test_discovers_registers_authorizes_with_pkce_and_refreshes(
    server: FakeAuthorizationServer,
) -> None:
    # Pi: "discovers, registers, authorizes with PKCE, and refreshes on 401"
    resource_metadata = _resource_metadata(server, scopes_supported=["org:read"])
    provider = _Provider("http://127.0.0.1:4321/callback")
    # An empty scope falls through to the resource metadata's scopes_supported.
    challenge = parse_www_authenticate(
        f'Bearer resource_metadata="{server.origin}{_PRM}/mcp", scope=""'
    )
    options = _options(
        server,
        resource_metadata_url=challenge.resource_metadata_url,
        scope=challenge.scope,
    )
    assert authorize_mcp(provider, options) == "REDIRECT"
    assert server.paths == [f"{_PRM}/mcp", _AS, "/register"]
    assert server.registrations == [
        {
            **provider.client_metadata,
            "application_type": "native",
            "scope": "org:read",
        }
    ]
    assert provider.client is not None
    assert provider.client["client_id"] == "client-1"
    assert provider.discovery == {
        "authorizationServerUrl": server.origin,
        "authorizationServerMetadata": server.metadata,
        "resourceMetadata": resource_metadata,
        "resourceMetadataUrl": f"{server.origin}{_PRM}/mcp",
    }
    query = _query(provider.authorization_url)
    assert query["scope"] == "org:read"
    assert query["resource"] == f"{server.origin}/mcp"
    assert query["state"] == "expected-state"
    assert query["client_id"] == "client-1"
    assert query["redirect_uri"] == "http://127.0.0.1:4321/callback"
    assert provider.verifier is not None
    assert query["code_challenge"] == _s256(provider.verifier)

    assert provider.authorization_url is not None
    response = authorization_response(provider.authorization_url)
    assert response["state"] == "expected-state"
    # Empty and null optional fields count as absent (#10266).
    server.token_replies.append(
        json_reply(
            {
                "access_token": "first-token",
                "refresh_token": "refresh-token",
                "token_type": "Bearer",
                "scope": "",
            }
        )
    )
    with_code = _options(
        server,
        resource_metadata_url=challenge.resource_metadata_url,
        scope=challenge.scope,
        authorization_code=response["code"],
    )
    assert authorize_mcp(provider, with_code) == "AUTHORIZED"
    assert server.token_forms[-1] == {
        "grant_type": "authorization_code",
        "code": response["code"],
        "code_verifier": provider.verifier,
        "redirect_uri": "http://127.0.0.1:4321/callback",
        "resource": f"{server.origin}/mcp",
        "client_id": "client-1",
    }
    # The stored discovery state is used: nothing is discovered again.
    assert server.paths.count(_AS) == 1
    assert provider.token_set == {
        "access_token": "first-token",
        "refresh_token": "refresh-token",
        "token_type": "Bearer",
        "scope": "org:read",
    }

    server.token_replies.append(
        json_reply(
            {
                "access_token": "refreshed-token",
                "token_type": "Bearer",
                "refresh_token": "",
                "expires_in": None,
            }
        )
    )
    provider.authorization_url = None
    assert authorize_mcp(provider, options) == "AUTHORIZED"
    assert server.token_forms[-1] == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh-token",
        "resource": f"{server.origin}/mcp",
        "client_id": "client-1",
    }
    # Neither token response names a scope, so the grant has the requested scope.
    assert provider.token_set == {
        "access_token": "refreshed-token",
        "refresh_token": "refresh-token",
        "token_type": "Bearer",
        "scope": "org:read",
    }
    assert provider.authorization_url is None
    assert len(server.requests_to("/register")) == 1


def test_the_code_of_the_fake_server_is_exchanged_with_the_stored_verifier(
    server: FakeAuthorizationServer,
) -> None:
    server.send_iss = True
    server.granted_scope = "granted"
    provider = _Provider()
    assert authorize_mcp(provider, _options(server, scope="asked")) == "REDIRECT"
    assert provider.authorization_url is not None
    response = authorization_response(provider.authorization_url)
    options = _options(
        server,
        scope="asked",
        authorization_code=response["code"],
        iss=response["iss"],
    )
    assert authorize_mcp(provider, options) == "AUTHORIZED"
    # The scope the server names is the granted one.
    assert provider.token_set == {
        "access_token": "access-4",
        "refresh_token": "refresh-3",
        "token_type": "Bearer",
        "scope": "granted",
        "expires_in": 3600,
    }


def _signed_in(server: FakeAuthorizationServer, **tokens: Any) -> _Provider:
    """A provider with a client and a refresh token the server knows."""

    server.add_client("client")
    server.refresh_tokens["r1"] = "client"
    provider = _Provider()
    provider.client = {"client_id": "client"}
    provider.token_set = {
        "access_token": "a1",
        "refresh_token": "r1",
        "token_type": "Bearer",
        **tokens,
    }
    return provider


def test_a_refresh_stores_the_rotated_refresh_token(
    server: FakeAuthorizationServer,
) -> None:
    # Pi: "shares one refresh between concurrent 401s when refresh tokens
    # rotate", the part of one refresh.
    # Invalid resource metadata falls back to the server origin instead of
    # failing discovery.
    server.json(
        f"{_PRM}/mcp",
        {"resource": f"{server.origin}/mcp", "authorization_servers": ["not a url"]},
    )
    provider = _signed_in(server, scope="repo")
    assert authorize_mcp(provider, _options(server)) == "AUTHORIZED"
    assert [f["refresh_token"] for f in server.token_forms] == ["r1"]
    # A refresh without `scope` keeps the scope of the grant.
    assert provider.token_set == {
        "access_token": "access-2",
        "refresh_token": "refresh-1",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": "repo",
    }
    assert provider.authorization_url is None
    assert provider.invalidated == []


def test_a_refresh_token_the_server_no_longer_knows_asks_for_authorization(
    server: FakeAuthorizationServer,
) -> None:
    provider = _signed_in(server)
    del server.refresh_tokens["r1"]
    assert authorize_mcp(provider, _options(server)) == "REDIRECT"
    # The tokens are dropped, the client is kept, and the flow ran once more.
    assert provider.invalidated == ["tokens"]
    assert provider.token_set is None
    assert provider.client == {"client_id": "client"}
    assert len(server.token_forms) == 1
    assert server.requests_to("/register") == []
    assert _query(provider.authorization_url)["client_id"] == "client"


@pytest.mark.parametrize("code", ["invalid_client", "unauthorized_client"])
def test_a_client_the_server_refuses_is_registered_again(
    server: FakeAuthorizationServer, code: str
) -> None:
    provider = _signed_in(server)
    server.token_replies.append(error_reply(code, 401))
    assert authorize_mcp(provider, _options(server)) == "REDIRECT"
    assert provider.invalidated == ["all"]
    assert provider.token_set is None
    assert provider.client is not None
    assert provider.client["client_id"] == "client-1"
    assert len(server.token_forms) == 1
    assert _query(provider.authorization_url)["client_id"] == "client-1"


def test_the_flow_runs_again_only_once(server: FakeAuthorizationServer) -> None:
    provider = _signed_in(server)
    provider.verifier = "verifier"
    options = _options(server, authorization_code="unknown-code")
    with pytest.raises(OAuthError) as raised:
        authorize_mcp(provider, options)
    assert raised.value.code == "invalid_grant"
    assert provider.invalidated == ["tokens"]
    assert [f["code"] for f in server.token_forms] == ["unknown-code"] * 2


def test_a_provider_that_cannot_forget_still_runs_again(
    server: FakeAuthorizationServer,
) -> None:
    server.add_client("client")
    provider = _BareProvider({"client_id": "client"})
    provider.token_set = {
        "access_token": "a1",
        "refresh_token": "gone",
        "token_type": "Bearer",
    }
    with pytest.raises(OAuthError) as raised:
        authorize_mcp(provider, _options(server))
    assert raised.value.code == "invalid_grant"
    assert len(server.token_forms) == 2


@pytest.mark.parametrize(
    "reply",
    [
        Reply(500),
        error_reply("server_error", 503),
        Reply(200, {}, b"<html>not tokens</html>"),
        Reply(drop=True),
    ],
)
def test_a_refresh_that_fails_otherwise_asks_for_authorization(
    server: FakeAuthorizationServer, reply: Reply
) -> None:
    provider = _signed_in(server)
    server.token_replies.append(reply)
    assert authorize_mcp(provider, _options(server)) == "REDIRECT"
    # The working grant is kept until the user authorizes again.
    assert provider.token_set is not None
    assert provider.token_set["refresh_token"] == "r1"
    assert provider.invalidated == []
    assert len(server.token_forms) == 1
    assert provider.authorization_url is not None


def test_another_oauth_error_of_a_refresh_is_reported(
    server: FakeAuthorizationServer,
) -> None:
    provider = _signed_in(server)
    server.token_replies.append(error_reply("invalid_scope", 400, "No such scope"))
    with pytest.raises(OAuthError) as raised:
        authorize_mcp(provider, _options(server))
    assert (raised.value.code, str(raised.value)) == ("invalid_scope", "No such scope")
    assert provider.authorization_url is None
    assert provider.token_set is not None
    assert provider.invalidated == []
    assert len(server.token_forms) == 1


def test_a_refresh_is_not_sent_to_an_insecure_token_endpoint(
    plain: FakeOAuthServer,
) -> None:
    provider = _Provider()
    provider.client = {"client_id": "client"}
    provider.token_set = {
        "access_token": "a1",
        "refresh_token": "r1",
        "token_type": "Bearer",
    }
    provider.discovery = {
        "authorizationServerUrl": plain.origin,
        "authorizationServerMetadata": _metadata(
            plain.origin, token_endpoint="http://idp.example/token"
        ),
    }
    with pytest.raises(OAuthInsecureEndpointError):
        authorize_mcp(provider, _options(plain))
    assert provider.authorization_url is None
    assert plain.requests == []


def test_asks_for_authorization_instead_of_refreshing_when_the_server_needs_more_scope(
    server: FakeAuthorizationServer,
) -> None:
    # Pi: "asks for authorization instead of refreshing when the server needs
    # more scope". The adapter merges the granted and the challenged scopes;
    # here the flow is given the merged scope.
    provider = _signed_in(server, scope="repo read:org")
    challenge = parse_www_authenticate(
        'Bearer error="insufficient_scope", scope="repo admin"'
    )
    assert challenge.error == "insufficient_scope"
    options = _options(server, scope="repo read:org admin", skip_refresh=True)
    assert authorize_mcp(provider, options) == "REDIRECT"
    assert _query(provider.authorization_url)["scope"] == "repo read:org admin"
    # The working grant is kept until the user authorizes the new scope.
    assert provider.token_set is not None
    assert provider.token_set["access_token"] == "a1"
    assert server.token_forms == []


def test_uses_a_configured_authorization_server_metadata_document_as_is(
    plain: FakeOAuthServer,
) -> None:
    # Pi: "uses a configured authorization server metadata document as is"
    # (#10172)
    origin = plain.origin
    # Names the MCP server itself, which serves no authorization server metadata.
    _resource_metadata(plain)
    # Not derivable from the document URL; a configured document is not checked.
    plain.json(
        "/idp/metadata.json",
        {
            "issuer": "https://idp.example",
            "authorization_endpoint": f"{origin}/idp/authorize",
            "token_endpoint": f"{origin}/idp/token",
            "response_types_supported": ["code"],
        },
    )
    provider = _Provider()
    provider.client = {"client_id": "client"}
    # Discovery state is neither used nor stored with a configured document.
    stale: OAuthDiscoveryState = {
        "authorizationServerUrl": "https://stale.example",
        "authorizationServerMetadata": _metadata("https://stale.example"),
    }
    provider.discovery = dict(stale)
    options = _options(
        plain, authorization_server_metadata_url=f"{origin}/idp/metadata.json"
    )
    assert authorize_mcp(provider, options) == "REDIRECT"
    assert provider.authorization_url is not None
    assert provider.authorization_url.startswith(f"{origin}/idp/authorize?")
    assert _query(provider.authorization_url)["resource"] == f"{origin}/mcp"
    assert provider.discovery == stale
    assert plain.paths == [f"{_PRM}/mcp", "/idp/metadata.json"]

    insecure = _options(
        plain, authorization_server_metadata_url="http://idp.example/metadata.json"
    )
    with pytest.raises(OAuthInsecureEndpointError):
        authorize_mcp(provider, insecure)
    assert plain.paths == [f"{_PRM}/mcp", "/idp/metadata.json"]


def test_exchanges_a_code_only_when_its_iss_parameter_names_the_authorization_server(
    plain: FakeOAuthServer,
) -> None:
    # Pi: "exchanges a code only when its iss parameter names the
    # authorization server"
    origin = plain.origin
    plain.route("/token", json_reply({"access_token": "token", "token_type": "Bearer"}))

    def exchange(
        code: str, iss: str | None, iss_parameter_supported: bool, metadata: bool = True
    ) -> str:
        provider = _Provider()
        provider.client = {"client_id": "client"}
        provider.verifier = "verifier"
        provider.discovery = {"authorizationServerUrl": origin}
        if metadata:
            provider.discovery["authorizationServerMetadata"] = _metadata(
                origin,
                authorization_response_iss_parameter_supported=iss_parameter_supported,
            )
        return authorize_mcp(
            provider, _options(plain, authorization_code=code, iss=iss)
        )

    with pytest.raises(OAuthIssuerMismatchError) as other:
        exchange("other", "https://attacker.example", False)
    assert (other.value.expected, other.value.received) == (
        origin,
        "https://attacker.example",
    )
    with pytest.raises(OAuthIssuerMismatchError) as missing:
        exchange("missing", None, True)
    assert (missing.value.expected, missing.value.received) == (origin, None)
    assert exchange("matching", origin, True) == "AUTHORIZED"
    # Servers that do not promise the parameter may omit it.
    assert exchange("omitted", None, False) == "AUTHORIZED"
    # Without metadata there is no issuer to compare with, so a server cannot
    # skip the check by hiding it.
    with pytest.raises(OAuthIssuerMismatchError) as unverifiable:
        exchange("unverifiable", "https://attacker.example", False, False)
    assert unverifiable.value.expected is None
    assert exchange("no-metadata", None, False, False) == "AUTHORIZED"
    codes = [form(r)["code"] for r in plain.requests if r.method == "POST"]
    assert codes == ["matching", "omitted", "no-metadata"]


def test_stops_when_cancelled_without_falling_back_to_a_redirect(
    plain: FakeOAuthServer,
) -> None:
    # Pi: "stops when its signal aborts, without falling back to a redirect"
    # (#10565). The server accepts the requests and never answers.
    plain.route(f"{_PRM}/mcp", Reply(hang=True))
    plain.route("/token", Reply(hang=True))

    def run(provider: _Provider) -> str:
        cancel = threading.Event()
        count = len(plain.requests)
        raised: list[BaseException] = []

        def call() -> None:
            try:
                authorize_mcp(provider, _options(plain, cancel_event=cancel))
            except McpAbortError as error:
                raised.append(error)

        thread = threading.Thread(target=call, daemon=True)
        thread.start()
        _wait_until(lambda: len(plain.requests) == count + 1)
        cancel.set()
        thread.join(_WAIT_SECONDS)
        assert not thread.is_alive()
        assert len(raised) == 1
        assert provider.authorization_url is None
        assert len(plain.requests) == count + 1
        return plain.paths[-1]

    # Discovery.
    assert run(_Provider()) == f"{_PRM}/mcp"

    # A failed refresh otherwise falls back to a new authorization.
    refreshing = _Provider()
    refreshing.client = {"client_id": "client"}
    refreshing.token_set = {
        "access_token": "a1",
        "refresh_token": "r1",
        "token_type": "Bearer",
    }
    refreshing.discovery = {
        "authorizationServerUrl": plain.origin,
        "authorizationServerMetadata": _metadata(plain.origin),
    }
    assert run(refreshing) == "/token"
    assert refreshing.token_set["refresh_token"] == "r1"


def test_a_cancelled_registration_ends_the_flow(
    server: FakeAuthorizationServer,
) -> None:
    cancel = threading.Event()

    def hang_then_cancel(request: OAuthRequest) -> Reply:
        cancel.set()
        return Reply(hang=True)

    server.route("/register", hang_then_cancel)
    provider = _Provider()
    with pytest.raises(McpAbortError):
        authorize_mcp(provider, _options(server, cancel_event=cancel))
    assert provider.client is None
    assert provider.authorization_url is None


@pytest.mark.parametrize(
    ("given", "supported", "configured", "expected"),
    [
        ("explicit", ["org:read"], "configured", "explicit"),
        (None, ["org:read", "org:write"], "configured", "org:read org:write"),
        ("", ["org:read"], "configured", "org:read"),
        (None, [], "configured", "configured"),
        (None, None, "configured", "configured"),
        (None, None, None, None),
        ("", [], "", None),
    ],
)
def test_the_requested_scope_is_the_first_source_that_has_one(
    server: FakeAuthorizationServer,
    given: str | None,
    supported: list[str] | None,
    configured: str | None,
    expected: str | None,
) -> None:
    if supported is None:
        _resource_metadata(server)
    else:
        _resource_metadata(server, scopes_supported=supported)
    provider = _Provider()
    if configured is not None:
        provider.client_metadata["scope"] = configured
    assert authorize_mcp(provider, _options(server, scope=given)) == "REDIRECT"
    assert _query(provider.authorization_url).get("scope") == expected
    assert server.registrations[0].get("scope") == (expected or configured)


def test_a_token_response_without_scope_records_the_requested_scope(
    server: FakeAuthorizationServer,
) -> None:
    provider = _Provider()
    options = _options(server, scope="asked")
    assert authorize_mcp(provider, options) == "REDIRECT"
    assert provider.authorization_url is not None
    code = authorization_response(provider.authorization_url)["code"]
    assert (
        authorize_mcp(
            provider, _options(server, scope="asked", authorization_code=code)
        )
        == "AUTHORIZED"
    )
    assert provider.token_set is not None
    assert provider.token_set["scope"] == "asked"
    assert provider.token_set["refresh_token"] == "refresh-3"


def test_without_a_requested_scope_none_is_recorded(
    server: FakeAuthorizationServer,
) -> None:
    provider = _signed_in(server)
    assert authorize_mcp(provider, _options(server)) == "AUTHORIZED"
    assert provider.token_set is not None
    assert "scope" not in provider.token_set


def test_stored_discovery_state_is_used_instead_of_discovery(
    server: FakeAuthorizationServer,
) -> None:
    provider = _signed_in(server)
    provider.discovery = {
        "authorizationServerUrl": server.origin,
        "authorizationServerMetadata": server.metadata,
        "resourceMetadata": {"resource": f"{server.origin}/mcp"},
        "resourceMetadataUrl": "https://old.example/prm",
    }
    assert authorize_mcp(provider, _options(server)) == "AUTHORIZED"
    assert server.paths == ["/token"]
    assert server.token_forms[0]["resource"] == f"{server.origin}/mcp"
    # What is stored again is what was used, without the old metadata URL.
    assert provider.discovery == {
        "authorizationServerUrl": server.origin,
        "authorizationServerMetadata": server.metadata,
        "resourceMetadata": {"resource": f"{server.origin}/mcp"},
    }


def test_stored_discovery_state_without_metadata_looks_the_metadata_up(
    server: FakeAuthorizationServer,
) -> None:
    provider = _signed_in(server)
    provider.discovery = {"authorizationServerUrl": server.origin}
    assert authorize_mcp(provider, _options(server)) == "AUTHORIZED"
    assert server.paths == [_AS, "/token"]
    assert provider.discovery == {
        "authorizationServerUrl": server.origin,
        "authorizationServerMetadata": server.metadata,
    }


def test_a_provider_without_discovery_state_discovers_every_time(
    server: FakeAuthorizationServer,
) -> None:
    server.add_client("client")
    provider = _BareProvider({"client_id": "client"})
    assert authorize_mcp(provider, _options(server)) == "REDIRECT"
    assert authorize_mcp(provider, _options(server)) == "REDIRECT"
    assert server.paths == [f"{_PRM}/mcp", _PRM, _AS] * 2
    # A provider without `state` sends none.
    assert "state" not in _query(provider.authorization_url)


def test_resource_metadata_for_another_resource_fails_the_flow(
    server: FakeAuthorizationServer,
) -> None:
    server.json(
        f"{_PRM}/mcp",
        {
            "resource": f"{server.origin}/other",
            "authorization_servers": [server.origin],
        },
    )
    provider = _Provider()
    with pytest.raises(OAuthFlowError, match="does not match MCP server"):
        authorize_mcp(provider, _options(server))
    assert server.requests_to("/register") == []
    assert provider.authorization_url is None


def test_a_provider_that_cannot_store_a_client_does_not_register(
    server: FakeAuthorizationServer,
) -> None:
    provider = _BareProvider()
    with pytest.raises(OAuthFlowError) as raised:
        authorize_mcp(provider, _options(server))
    assert str(raised.value) == "OAuth client information cannot be persisted"
    assert server.requests_to("/register") == []


def test_a_code_is_not_exchanged_without_a_client(
    server: FakeAuthorizationServer,
) -> None:
    provider = _Provider()
    with pytest.raises(OAuthFlowError) as raised:
        authorize_mcp(provider, _options(server, authorization_code="code"))
    assert str(raised.value) == (
        "OAuth client information is missing during code exchange"
    )
    assert server.requests_to("/register") == []
    assert server.token_forms == []


def test_a_refused_registration_ends_the_flow(server: FakeAuthorizationServer) -> None:
    server.route("/register", Reply(400, {}, b"no"))
    provider = _Provider()
    with pytest.raises(OAuthRegistrationError):
        authorize_mcp(provider, _options(server))
    assert provider.client is None
    assert provider.authorization_url is None


class _DocumentProvider(_Provider):
    def __init__(self, document: OAuthClientMetadataDocument | None) -> None:
        super().__init__()
        self.document = document
        self.asked: list[AuthorizationServerMetadata | None] = []

    def client_metadata_document(
        self, metadata: AuthorizationServerMetadata | None
    ) -> OAuthClientMetadataDocument | None:
        self.asked.append(metadata)
        return self.document


def test_a_client_metadata_document_is_used_instead_of_registering(
    server: FakeAuthorizationServer,
) -> None:
    document = OAuthClientMetadataDocument(
        "https://client.example/oauth/client.json",
        "http://127.0.0.1:4000/callback/server-id",
    )
    server.add_client(document.url)
    provider = _DocumentProvider(document)
    assert authorize_mcp(provider, _options(server)) == "REDIRECT"
    assert provider.asked == [server.metadata]
    assert server.requests_to("/register") == []
    # The document is not stored as a client.
    assert provider.client is None
    query = _query(provider.authorization_url)
    assert query["client_id"] == document.url
    # The document's redirect URI is used, not the provider's.
    assert query["redirect_uri"] == document.redirect_url

    assert provider.authorization_url is not None
    code = authorization_response(provider.authorization_url)["code"]
    assert (
        authorize_mcp(provider, _options(server, authorization_code=code))
        == "AUTHORIZED"
    )
    assert server.token_forms[0]["client_id"] == document.url
    assert server.token_forms[0]["redirect_uri"] == document.redirect_url


def test_a_stored_client_is_used_before_a_client_metadata_document(
    server: FakeAuthorizationServer,
) -> None:
    provider = _DocumentProvider(
        OAuthClientMetadataDocument("https://client.example/client.json", _REDIRECT)
    )
    server.add_client("client")
    provider.client = {"client_id": "client"}
    assert authorize_mcp(provider, _options(server)) == "REDIRECT"
    assert provider.asked == []
    assert _query(provider.authorization_url)["client_id"] == "client"


def test_a_provider_without_a_document_registers(
    server: FakeAuthorizationServer,
) -> None:
    provider = _DocumentProvider(None)
    assert authorize_mcp(provider, _options(server)) == "REDIRECT"
    assert len(server.requests_to("/register")) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://client.example/client.json",
        "https://client.example",
        "https://client.example/",
        "client.json",
    ],
)
def test_a_client_metadata_url_must_be_https_with_a_path(
    server: FakeAuthorizationServer, url: str
) -> None:
    provider = _DocumentProvider(OAuthClientMetadataDocument(url, _REDIRECT))
    with pytest.raises(OAuthFlowError) as raised:
        authorize_mcp(provider, _options(server))
    assert str(raised.value) == "Invalid OAuth client metadata URL"
    assert provider.authorization_url is None


def test_a_server_without_metadata_gets_the_default_endpoints() -> None:
    with FakeAuthorizationServer(serve_metadata=False) as server:
        provider = _Provider()
        assert authorize_mcp(provider, _options(server)) == "REDIRECT"
        assert server.paths[-1] == "/register"
        assert provider.authorization_url is not None
        assert provider.authorization_url.startswith(f"{server.origin}/authorize?")
        code = authorization_response(provider.authorization_url)["code"]
        options = _options(server, authorization_code=code)
        assert authorize_mcp(provider, options) == "AUTHORIZED"
        assert provider.discovery == {"authorizationServerUrl": f"{server.origin}/"}
    _wait_until(lambda: not _fetch_threads())


def test_the_provider_s_client_authentication_is_used_for_token_requests(
    server: FakeAuthorizationServer,
) -> None:
    class Authenticating(_Provider):
        def add_client_authentication(
            self,
            headers: dict[str, str],
            params: dict[str, str],
            url: str,
            metadata: AuthorizationServerMetadata | None,
        ) -> None:
            params["client_id"] = "client"
            params["client_assertion"] = "signed"

    provider = Authenticating()
    server.add_client("client")
    server.refresh_tokens["r1"] = "client"
    provider.client = {"client_id": "other", "client_secret": "unused"}
    provider.token_set = {
        "access_token": "a1",
        "refresh_token": "r1",
        "token_type": "Bearer",
    }
    assert authorize_mcp(provider, _options(server)) == "AUTHORIZED"
    assert server.token_forms[0]["client_assertion"] == "signed"
    assert "client_secret" not in server.token_forms[0]
    assert "authorization" not in server.requests_to("/token")[0].headers
