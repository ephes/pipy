"""OAuth documents and their validation.

A document is the JSON object a server sent, as a ``dict`` with the key names
of its RFC (``authorization_endpoint``, ``scopes_supported``). The parsers
check the fields the flow reads and keep every other field as it came. A
checked optional field that is absent, ``null`` or, for strings, empty is left
out of the result, so a present key always holds a usable value.

Where this differs from Pi:

- Whether a string is a URL is decided by ``can_parse_url`` and not by a
  WHATWG URL parser: an ``http`` or ``https`` URL must pass the config
  parser (``config.parse_http_url``), and any other text that starts with
  a URL scheme counts as a URL.
- ``expires_in`` is a JSON number or a decimal number in a string. Pi
  converts with JavaScript's ``Number``, which also reads booleans, hex
  strings and one-element arrays.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Final

from pipy_harness.native.mcp.config import parse_http_url
from pipy_harness.native.mcp.oauth.errors import OAuthFlowError
from pipy_harness.native.mcp.protocol import is_number, is_object

OAuthProtectedResourceMetadata = dict[str, Any]
AuthorizationServerMetadata = dict[str, Any]
OAuthTokens = dict[str, Any]
OAuthClientMetadata = dict[str, Any]
OAuthClientInformation = dict[str, Any]


@dataclass(frozen=True, slots=True)
class OAuthServerInfo:
    """What discovery found out about an MCP server's authorization server."""

    authorization_server_url: str
    authorization_server_metadata: AuthorizationServerMetadata | None = None
    resource_metadata: OAuthProtectedResourceMetadata | None = None


@dataclass(frozen=True, slots=True)
class OAuthChallenge:
    """The fields of a ``WWW-Authenticate`` challenge the flow reads."""

    resource_metadata_url: str | None = None
    scope: str | None = None
    error: str | None = None
    error_description: str | None = None


_SCHEME: Final = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*):")
_UNSAFE_SCHEMES: Final = frozenset({"javascript", "data", "vbscript"})
# What a URL parser removes from both ends of its input.
_C0_CONTROL_OR_SPACE: Final = "".join(map(chr, range(0x21)))
_DECIMAL: Final = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _url_scheme(value: str) -> str | None:
    """The lower-case scheme of an absolute URL, ``None`` for anything else."""

    text = re.sub(r"[\t\n\r]", "", value.strip(_C0_CONTROL_OR_SPACE))
    match = _SCHEME.match(text)
    if match is None:
        return None
    scheme = match[1].lower()
    if scheme in ("http", "https") and parse_http_url(text) is None:
        return None
    return scheme


def can_parse_url(value: str) -> bool:
    """Whether ``value`` is an absolute URL."""

    return _url_scheme(value) is not None


def _object(value: object, name: str) -> dict[str, Any]:
    if not is_object(value):
        raise OAuthFlowError(f"Invalid {name}")
    return value


def _required_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise OAuthFlowError(f"Invalid {name}")
    return value


def _absent(value: object) -> bool:
    """Servers send ``null`` and ``""`` for fields they have no value for."""

    return value is None or value == ""


def _optional_string(value: object, name: str) -> str | None:
    return None if _absent(value) else _required_string(value, name)


def _optional_strings(value: object, name: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise OAuthFlowError(f"Invalid {name}")
    return list(value)


def _safe_url(value: object, name: str) -> str:
    text = _required_string(value, name)
    scheme = _url_scheme(text)
    if scheme is None or scheme in _UNSAFE_SCHEMES:
        raise OAuthFlowError(f"Invalid {name}")
    return text


def _optional_url(value: object, name: str) -> str | None:
    return None if _absent(value) else _safe_url(value, name)


def _optional_number(value: object) -> int | float | None:
    return value if is_number(value) else None


def _optional_boolean(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _with_fields(base: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    """``base`` with ``fields`` set; a field that is ``None`` is left out."""

    result = dict(base)
    for key, value in fields.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = value
    return result


def parse_protected_resource_metadata(value: object) -> OAuthProtectedResourceMetadata:
    """Check protected resource metadata (RFC 9728)."""

    document = _object(value, "OAuth protected resource metadata")
    servers = _optional_strings(
        document.get("authorization_servers"), "authorization_servers"
    )
    return _with_fields(
        document,
        {
            "resource": _safe_url(
                document.get("resource"), "OAuth protected resource metadata resource"
            ),
            "authorization_servers": None
            if servers is None
            else [_safe_url(url, "authorization server URL") for url in servers],
            "scopes_supported": _optional_strings(
                document.get("scopes_supported"), "scopes_supported"
            ),
        },
    )


def parse_authorization_server_metadata(value: object) -> AuthorizationServerMetadata:
    """Check authorization server metadata (RFC 8414, OpenID Connect Discovery)."""

    document = _object(value, "authorization server metadata")
    response_types = _optional_strings(
        document.get("response_types_supported"), "response_types_supported"
    )
    if response_types is None:
        raise OAuthFlowError("Invalid response_types_supported")
    fields: dict[str, Any] = {
        "issuer": _safe_url(document.get("issuer"), "authorization server issuer"),
        "authorization_endpoint": _safe_url(
            document.get("authorization_endpoint"), "authorization endpoint"
        ),
        "token_endpoint": _safe_url(document.get("token_endpoint"), "token endpoint"),
        "registration_endpoint": _optional_url(
            document.get("registration_endpoint"), "registration endpoint"
        ),
        "scopes_supported": _optional_strings(
            document.get("scopes_supported"), "scopes_supported"
        ),
        "response_types_supported": response_types,
    }
    for key in (
        "grant_types_supported",
        "token_endpoint_auth_methods_supported",
        "code_challenge_methods_supported",
    ):
        fields[key] = _optional_strings(document.get(key), key)
    for key in (
        "client_id_metadata_document_supported",
        "authorization_response_iss_parameter_supported",
    ):
        fields[key] = _optional_boolean(document.get(key))
    return _with_fields(document, fields)


def _expires_in(value: object) -> int | float | None:
    if _absent(value):
        return None
    number: int | float | None = None
    if is_number(value):
        number = value
    elif isinstance(value, str) and _DECIMAL.fullmatch(value.strip()):
        number = float(value)
        if number.is_integer():
            number = int(number)
    if number is None or not math.isfinite(number):
        raise OAuthFlowError("Invalid expires_in")
    return number


def parse_oauth_tokens(value: object) -> OAuthTokens:
    """Check a token response. Only the fields named here are kept."""

    document = _object(value, "OAuth token response")
    return _with_fields(
        {},
        {
            "access_token": _required_string(
                document.get("access_token"), "access_token"
            ),
            "token_type": _required_string(document.get("token_type"), "token_type"),
            "expires_in": _expires_in(document.get("expires_in")),
            "scope": _optional_string(document.get("scope"), "scope"),
            "refresh_token": _optional_string(
                document.get("refresh_token"), "refresh_token"
            ),
            "id_token": _optional_string(document.get("id_token"), "id_token"),
        },
    )


def parse_client_information(value: object) -> OAuthClientInformation:
    """Check the answer to a dynamic client registration (RFC 7591)."""

    document = _object(value, "OAuth client registration response")
    return _with_fields(
        document,
        {
            "client_id": _required_string(document.get("client_id"), "client_id"),
            "client_secret": _optional_string(
                document.get("client_secret"), "client_secret"
            ),
            "client_id_issued_at": _optional_number(
                document.get("client_id_issued_at")
            ),
            "client_secret_expires_at": _optional_number(
                document.get("client_secret_expires_at")
            ),
            "redirect_uris": _optional_strings(
                document.get("redirect_uris"), "redirect_uris"
            )
            or [],
        },
    )
