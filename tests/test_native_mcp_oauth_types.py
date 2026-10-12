"""Tests for the OAuth documents' validation and the OAuth error types."""

from __future__ import annotations

from typing import Any

import pytest

from pipy_harness.native.mcp.oauth.errors import (
    McpOAuthAuthorizationRequiredError,
    OAuthError,
    OAuthFlowError,
    OAuthInsecureEndpointError,
    OAuthIssuerMismatchError,
    OAuthRegistrationError,
)
from pipy_harness.native.mcp.oauth.types import (
    can_parse_url,
    parse_authorization_server_metadata,
    parse_client_information,
    parse_oauth_tokens,
    parse_protected_resource_metadata,
)

_SERVER: dict[str, Any] = {
    "issuer": "https://auth.example",
    "authorization_endpoint": "https://auth.example/authorize",
    "token_endpoint": "https://auth.example/token",
    "response_types_supported": ["code"],
}


def _invalid(parse: Any, value: object, name: str) -> None:
    with pytest.raises(OAuthFlowError) as raised:
        parse(value)
    assert str(raised.value) == f"Invalid {name}"


# -- errors -------------------------------------------------------------------


def test_an_oauth_error_carries_code_message_and_uri() -> None:
    error = OAuthError("invalid_grant", "The grant expired", "https://e.example/1")
    assert (error.code, str(error), error.error_uri) == (
        "invalid_grant",
        "The grant expired",
        "https://e.example/1",
    )
    assert str(OAuthError("invalid_grant", "")) == "invalid_grant"
    assert OAuthError("invalid_grant", "").error_uri is None


def test_an_issuer_mismatch_names_both_issuers() -> None:
    error = OAuthIssuerMismatchError("https://a.example", 'https://b.example/"x"')
    assert str(error) == (
        'OAuth issuer mismatch: expected "https://a.example", '
        'received "https://b.example/\\"x\\""'
    )
    assert (error.expected, error.received) == (
        "https://a.example",
        'https://b.example/"x"',
    )
    assert str(OAuthIssuerMismatchError(None, None)) == (
        "OAuth issuer mismatch: expected none, received none"
    )
    assert str(OAuthIssuerMismatchError("https://ä.example", None)) == (
        'OAuth issuer mismatch: expected "https://ä.example", received none'
    )


def test_the_other_errors_have_pi_s_messages() -> None:
    insecure = OAuthInsecureEndpointError("http://idp.example/token")
    assert str(insecure) == (
        "Refusing to send OAuth credentials to non-HTTPS endpoint "
        "http://idp.example/token"
    )
    assert insecure.endpoint == "http://idp.example/token"
    registration = OAuthRegistrationError(403, "no")
    assert str(registration) == (
        "OAuth dynamic client registration failed with status 403: no"
    )
    assert (registration.status, registration.body) == (403, "no")
    assert str(McpOAuthAuthorizationRequiredError()) == (
        "MCP OAuth authorization requires user interaction"
    )


# -- URLs ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "https://auth.example",
        "http://127.0.0.1:8080/path?x=1#f",
        " https://auth.example/\n",
        "HTTPS://AUTH.EXAMPLE",
        "urn:example:resource",
        "com.example.app:/callback",
        "mailto:someone@example.com",
    ],
)
def test_an_absolute_url_can_be_parsed(value: str) -> None:
    assert can_parse_url(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not a url",
        "/relative/path",
        "auth.example/path",
        "//auth.example",
        "https://",
        "https:auth.example",
        "https://auth.example:99999",
        "https://auth example",
        "1https://auth.example",
    ],
)
def test_other_text_cannot_be_parsed_as_a_url(value: str) -> None:
    assert not can_parse_url(value)


# -- protected resource metadata ----------------------------------------------


def test_protected_resource_metadata_keeps_unknown_fields() -> None:
    document = {
        "resource": "https://mcp.example/mcp",
        "authorization_servers": ["https://auth.example"],
        "scopes_supported": ["read", "write"],
        "bearer_methods_supported": ["header"],
        "resource_name": None,
    }
    parsed = parse_protected_resource_metadata(document)
    assert parsed == document
    assert parsed is not document
    assert parsed["authorization_servers"] is not document["authorization_servers"]


def test_protected_resource_metadata_drops_checked_fields_that_are_null() -> None:
    parsed = parse_protected_resource_metadata(
        {
            "resource": "https://mcp.example/mcp",
            "authorization_servers": None,
            "scopes_supported": None,
        }
    )
    assert parsed == {"resource": "https://mcp.example/mcp"}


@pytest.mark.parametrize(
    ("document", "name"),
    [
        ([], "OAuth protected resource metadata"),
        (None, "OAuth protected resource metadata"),
        ({}, "OAuth protected resource metadata resource"),
        ({"resource": ""}, "OAuth protected resource metadata resource"),
        ({"resource": 5}, "OAuth protected resource metadata resource"),
        ({"resource": "mcp"}, "OAuth protected resource metadata resource"),
        (
            {"resource": "javascript:alert(1)"},
            "OAuth protected resource metadata resource",
        ),
        (
            {"resource": "https://m.example", "authorization_servers": "x"},
            "authorization_servers",
        ),
        (
            {"resource": "https://m.example", "authorization_servers": [1]},
            "authorization_servers",
        ),
        (
            # Pi: "shares one refresh ..." serves this document, and discovery
            # then goes on without resource metadata.
            {"resource": "https://m.example", "authorization_servers": ["not a url"]},
            "authorization server URL",
        ),
        (
            {"resource": "https://m.example", "authorization_servers": [""]},
            "authorization server URL",
        ),
        (
            {"resource": "https://m.example", "scopes_supported": ["a", None]},
            "scopes_supported",
        ),
    ],
)
def test_invalid_protected_resource_metadata_is_rejected(
    document: object, name: str
) -> None:
    _invalid(parse_protected_resource_metadata, document, name)


# -- authorization server metadata --------------------------------------------


def test_authorization_server_metadata_keeps_unknown_fields() -> None:
    document = {
        **_SERVER,
        "registration_endpoint": "https://auth.example/register",
        "scopes_supported": ["read"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": False,
        "jwks_uri": "https://auth.example/jwks",
        "service_documentation": None,
    }
    assert parse_authorization_server_metadata(document) == document


def test_authorization_server_metadata_drops_optional_fields_without_a_value() -> None:
    parsed = parse_authorization_server_metadata(
        {
            **_SERVER,
            "registration_endpoint": "",
            "scopes_supported": None,
            "grant_types_supported": None,
            "token_endpoint_auth_methods_supported": None,
            "code_challenge_methods_supported": None,
            # Not booleans, so they say nothing.
            "client_id_metadata_document_supported": "true",
            "authorization_response_iss_parameter_supported": 1,
        }
    )
    assert parsed == _SERVER
    assert (
        parse_authorization_server_metadata({**_SERVER, "registration_endpoint": None})
        == _SERVER
    )


@pytest.mark.parametrize(
    ("change", "name"),
    [
        ({"issuer": None}, "authorization server issuer"),
        ({"issuer": "auth.example"}, "authorization server issuer"),
        ({"issuer": "data:text/plain,x"}, "authorization server issuer"),
        ({"authorization_endpoint": ""}, "authorization endpoint"),
        ({"authorization_endpoint": "vbscript:x"}, "authorization endpoint"),
        ({"token_endpoint": 7}, "token endpoint"),
        ({"registration_endpoint": "register"}, "registration endpoint"),
        ({"registration_endpoint": 7}, "registration endpoint"),
        ({"response_types_supported": None}, "response_types_supported"),
        ({"response_types_supported": "code"}, "response_types_supported"),
        ({"scopes_supported": "read"}, "scopes_supported"),
        ({"grant_types_supported": [1]}, "grant_types_supported"),
        (
            {"token_endpoint_auth_methods_supported": {}},
            "token_endpoint_auth_methods_supported",
        ),
        (
            {"code_challenge_methods_supported": "S256"},
            "code_challenge_methods_supported",
        ),
    ],
)
def test_invalid_authorization_server_metadata_is_rejected(
    change: dict[str, Any], name: str
) -> None:
    _invalid(parse_authorization_server_metadata, {**_SERVER, **change}, name)


def test_authorization_server_metadata_must_be_an_object() -> None:
    _invalid(parse_authorization_server_metadata, "x", "authorization server metadata")
    missing = {k: v for k, v in _SERVER.items() if k != "response_types_supported"}
    _invalid(parse_authorization_server_metadata, missing, "response_types_supported")


# -- tokens -------------------------------------------------------------------


def test_a_token_response_keeps_only_its_known_fields() -> None:
    assert parse_oauth_tokens(
        {
            "access_token": "a",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "read write",
            "refresh_token": "r",
            "id_token": "i",
            "extra": "dropped",
        }
    ) == {
        "access_token": "a",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": "read write",
        "refresh_token": "r",
        "id_token": "i",
    }


def test_empty_and_null_token_fields_count_as_absent() -> None:
    # Pi: "discovers, registers, authorizes with PKCE, and refreshes on 401"
    # has a server that answers with these (#10266).
    for empty in ("", None):
        assert parse_oauth_tokens(
            {
                "access_token": "a",
                "token_type": "Bearer",
                "expires_in": empty,
                "scope": empty,
                "refresh_token": empty,
                "id_token": empty,
            }
        ) == {"access_token": "a", "token_type": "Bearer"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 0), (3600, 3600), (1.5, 1.5), ("3600", 3600), (" 60 ", 60), ("1.5", 1.5)],
)
def test_expires_in_is_a_number_or_a_numeric_string(
    value: object, expected: float
) -> None:
    tokens = parse_oauth_tokens(
        {"access_token": "a", "token_type": "Bearer", "expires_in": value}
    )
    assert tokens["expires_in"] == expected
    assert type(tokens["expires_in"]) is type(expected)


@pytest.mark.parametrize(
    ("change", "name"),
    [
        ({"access_token": ""}, "access_token"),
        ({"access_token": None}, "access_token"),
        ({"token_type": 5}, "token_type"),
        ({"expires_in": "soon"}, "expires_in"),
        ({"expires_in": "1e999"}, "expires_in"),
        ({"expires_in": True}, "expires_in"),
        ({"expires_in": [60]}, "expires_in"),
        ({"scope": 5}, "scope"),
        ({"refresh_token": ["r"]}, "refresh_token"),
        ({"id_token": 5}, "id_token"),
    ],
)
def test_an_invalid_token_response_is_rejected(
    change: dict[str, Any], name: str
) -> None:
    document = {"access_token": "a", "token_type": "Bearer", **change}
    _invalid(parse_oauth_tokens, document, name)


def test_a_token_response_must_be_an_object() -> None:
    _invalid(parse_oauth_tokens, ["a"], "OAuth token response")


# -- client information -------------------------------------------------------


def test_client_information_keeps_the_registered_metadata() -> None:
    document = {
        "client_id": "client",
        "client_secret": "secret",
        "client_id_issued_at": 1700000000,
        "client_secret_expires_at": 0,
        "redirect_uris": ["http://127.0.0.1/callback"],
        "client_name": "pipy",
        "token_endpoint_auth_method": "none",
    }
    assert parse_client_information(document) == document


def test_client_information_drops_fields_without_a_usable_value() -> None:
    # Pi: the registration answer of "discovers, registers, ..." has an empty
    # client secret (#10266).
    assert parse_client_information(
        {
            "client_id": "client",
            "client_secret": "",
            "client_id_issued_at": "now",
            "client_secret_expires_at": True,
            "redirect_uris": None,
        }
    ) == {"client_id": "client", "redirect_uris": []}
    assert parse_client_information({"client_id": "c", "client_secret": None}) == {
        "client_id": "c",
        "redirect_uris": [],
    }


@pytest.mark.parametrize(
    ("document", "name"),
    [
        ("client", "OAuth client registration response"),
        ({}, "client_id"),
        ({"client_id": ""}, "client_id"),
        ({"client_id": "c", "client_secret": 5}, "client_secret"),
        ({"client_id": "c", "redirect_uris": "x"}, "redirect_uris"),
    ],
)
def test_invalid_client_information_is_rejected(document: object, name: str) -> None:
    _invalid(parse_client_information, document, name)
