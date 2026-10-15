"""OAuth discovery for an MCP server.

Given the server's URL and, when there was one, its ``WWW-Authenticate``
challenge, discovery finds:

1. the server's protected resource metadata (RFC 9728): at the URL the
   challenge names, else at ``/.well-known/oauth-protected-resource<path>``
   and then at the same place without the path;
2. the authorization server: the first one that metadata lists, else the
   MCP server's own origin;
3. the authorization server's metadata (RFC 8414, OpenID Connect Discovery),
   at up to three well-known URLs.

A network failure, as ``fetch.is_network_error`` defines it, and a cancelled
request end discovery with their exception. Any other failure to load the
protected resource metadata (a failure status, a body that is not the
document, a time limit of the fetch) means the server has none. For the
authorization server's metadata, 4xx and 502 mean "not at this URL"; a server
with no document at any of the URLs has none, and the flow then uses default
endpoints. Any other failure there ends discovery.

Where this differs from Pi:

- URLs are read with the config parser (``config.parse_http_url``) and not
  with a WHATWG URL parser. A path loses its dot segments here as there, in
  the URLs built here and in the ones given (a challenge's metadata URL, a
  configured metadata document). ``select_resource`` compares the paths
  percent-encoded as a WHATWG parser writes them, so ``/café`` and
  ``/caf%C3%A9`` are one path; the host is compared in the ASCII form the
  parser gives it, as in Pi. A URL that parser does not accept (also one
  that is not ``http`` or ``https``) raises ``http.client.InvalidURL`` where a
  request to it would be made, which counts as a network failure, and is no
  match in ``select_resource``.
- A cancelled request always ends discovery. In Pi the abort of the first
  request is caught, and the next request then fails at once with the same
  abort.
"""

from __future__ import annotations

import http.client
import re
import threading
import urllib.parse
from typing import Final, Literal

from pipy_harness.native.mcp.config import HttpUrl, parse_http_url
from pipy_harness.native.mcp.oauth.errors import (
    OAuthFlowError,
    OAuthIssuerMismatchError,
)
from pipy_harness.native.mcp.oauth.fetch import (
    OAuthFetch,
    OAuthResponse,
    is_network_error,
    oauth_fetch,
)
from pipy_harness.native.mcp.oauth.types import (
    AuthorizationServerMetadata,
    OAuthChallenge,
    OAuthProtectedResourceMetadata,
    OAuthServerInfo,
    can_parse_url,
    parse_authorization_server_metadata,
    parse_protected_resource_metadata,
)
from pipy_harness.native.mcp.protocol import LATEST_PROTOCOL_VERSION, McpAbortError

DiscoveryUrlType = Literal["oauth", "oidc"]

# What JavaScript's ``\s`` matches.
_SPACE: Final = "\t\n\v\f\r    -     　﻿"
_CHALLENGE_SCHEMES: Final = ("bearer", "dpop")
_PROTECTED_RESOURCE: Final = "/.well-known/oauth-protected-resource"
# The printable ASCII characters of the WHATWG path percent-encode set.
_PATH_PERCENT_ENCODE: Final = frozenset('"#<>?^`{}')


def _field(header: str, name: str) -> str | None:
    match = re.search(
        rf'(?:^|[,{_SPACE}]){name}=(?:"([^"]*)"|([^{_SPACE},]+))',
        header,
        re.IGNORECASE | re.ASCII,
    )
    # An empty value (`scope=""`) carries no information, so it counts as absent.
    return (match[1] or match[2] or None) if match else None


def parse_www_authenticate(header: str | None) -> OAuthChallenge:
    """The challenge of a ``Bearer`` or ``DPoP`` ``WWW-Authenticate`` header.

    Another scheme, and no header, give a challenge without fields. A
    ``resource_metadata`` value that is not a URL is left out.
    """

    if not header:
        return OAuthChallenge()
    scheme = re.match(f"[{_SPACE}]*([^{_SPACE}]*)", header)
    if scheme is None or scheme[1].lower() not in _CHALLENGE_SCHEMES:
        return OAuthChallenge()
    resource_metadata = _field(header, "resource_metadata")
    if resource_metadata is not None and not can_parse_url(resource_metadata):
        resource_metadata = None
    return OAuthChallenge(
        resource_metadata_url=resource_metadata,
        scope=_field(header, "scope"),
        error=_field(header, "error"),
        error_description=_field(header, "error_description"),
    )


def _http_url(value: str) -> HttpUrl:
    url = parse_http_url(value)
    if url is None:
        raise http.client.InvalidURL(f"Cannot request URL {value}")
    return url


def _request_url(value: str) -> str:
    """A URL that was given, as it is requested.

    User info is refused here as the default fetch refuses it, since the
    normalized URL no longer shows it.
    """

    url = _http_url(value)
    if url.has_user_info:
        raise http.client.InvalidURL(f"Cannot request URL {value}")
    return url.href


def _path_suffix(pathname: str) -> str:
    """The path to put after ``/.well-known/<kind>``; empty for the root path."""

    return pathname.removesuffix("/")


def _is_discovery_miss(status: int) -> bool:
    """4xx and 502 mean "not here", so discovery tries the next candidate URL."""

    return 400 <= status < 500 or status == 502


def _fetch_metadata(
    url: str,
    fetch: OAuthFetch | None,
    protocol_version: str | None,
    cancel_event: threading.Event | None,
) -> OAuthResponse:
    return (fetch or oauth_fetch)(
        url,
        headers={
            "Accept": "application/json",
            "MCP-Protocol-Version": protocol_version or LATEST_PROTOCOL_VERSION,
        },
        cancel_event=cancel_event,
    )


def discover_protected_resource_metadata(
    server_url: str,
    *,
    resource_metadata_url: str | None = None,
    protocol_version: str | None = None,
    fetch: OAuthFetch | None = None,
    cancel_event: threading.Event | None = None,
) -> OAuthProtectedResourceMetadata:
    """Load the protected resource metadata of the MCP server at ``server_url``.

    With ``resource_metadata_url`` only that URL is asked. Raises
    ``OAuthFlowError`` when the server has no valid document.
    """

    server = _http_url(server_url)
    path = server.pathname
    response = _fetch_metadata(
        _request_url(resource_metadata_url)
        if resource_metadata_url
        else f"{server.origin}{_PROTECTED_RESOURCE}{_path_suffix(path)}",
        fetch,
        protocol_version,
        cancel_event,
    )
    if (
        not resource_metadata_url
        and path != "/"
        and _is_discovery_miss(response.status)
    ):
        response = _fetch_metadata(
            f"{server.origin}{_PROTECTED_RESOURCE}",
            fetch,
            protocol_version,
            cancel_event,
        )
    if not response.ok:
        raise OAuthFlowError(
            f"HTTP {response.status} loading OAuth protected resource metadata"
        )
    return parse_protected_resource_metadata(response.json())


def build_authorization_server_discovery_urls(
    authorization_server_url: str,
) -> list[tuple[str, DiscoveryUrlType]]:
    """Where an authorization server's metadata may be, in the order to ask."""

    issuer = _http_url(authorization_server_url)
    path = _path_suffix(issuer.pathname)
    urls: list[tuple[str, DiscoveryUrlType]] = [
        (f"{issuer.origin}/.well-known/oauth-authorization-server{path}", "oauth"),
        (f"{issuer.origin}/.well-known/openid-configuration{path}", "oidc"),
    ]
    if path:
        urls.append((f"{issuer.origin}{path}/.well-known/openid-configuration", "oidc"))
    return urls


def _load_authorization_server_metadata(
    url: str, response: OAuthResponse
) -> AuthorizationServerMetadata:
    if not response.ok:
        raise OAuthFlowError(
            f"HTTP {response.status} loading authorization server metadata from {url}"
        )
    return parse_authorization_server_metadata(response.json())


def discover_authorization_server_metadata(
    authorization_server_url: str,
    *,
    fetch: OAuthFetch | None = None,
    protocol_version: str | None = None,
    skip_issuer_validation: bool = False,
    cancel_event: threading.Event | None = None,
) -> AuthorizationServerMetadata | None:
    """Load an authorization server's metadata; ``None`` when it has none.

    The document must name ``authorization_server_url`` as its issuer, one
    trailing slash on either side aside; otherwise
    ``OAuthIssuerMismatchError`` is raised.
    """

    for url, _ in build_authorization_server_discovery_urls(authorization_server_url):
        response = _fetch_metadata(url, fetch, protocol_version, cancel_event)
        if not response.ok and _is_discovery_miss(response.status):
            continue
        metadata = _load_authorization_server_metadata(url, response)
        issuer: str = metadata["issuer"]
        if not skip_issuer_validation and issuer.removesuffix(
            "/"
        ) != authorization_server_url.removesuffix("/"):
            raise OAuthIssuerMismatchError(authorization_server_url, issuer)
        return metadata
    return None


def discover_oauth_server_info(
    server_url: str,
    *,
    resource_metadata_url: str | None = None,
    authorization_server_metadata_url: str | None = None,
    fetch: OAuthFetch | None = None,
    skip_issuer_validation: bool = False,
    cancel_event: threading.Event | None = None,
) -> OAuthServerInfo:
    """Find the authorization server of the MCP server at ``server_url``.

    ``authorization_server_metadata_url`` names a metadata document to use
    instead of looking for one. It is trusted as configured, so its issuer is
    not checked.
    """

    resource_metadata: OAuthProtectedResourceMetadata | None = None
    try:
        resource_metadata = discover_protected_resource_metadata(
            server_url,
            resource_metadata_url=resource_metadata_url,
            fetch=fetch,
            cancel_event=cancel_event,
        )
    except Exception as error:  # noqa: BLE001 - as Pi: whatever else went wrong, the server has no usable document
        if is_network_error(error) or isinstance(error, McpAbortError):
            raise
    if authorization_server_metadata_url:
        url = _request_url(authorization_server_metadata_url)
        metadata = _load_authorization_server_metadata(
            url, _fetch_metadata(url, fetch, None, cancel_event)
        )
        return OAuthServerInfo(metadata["issuer"], metadata, resource_metadata)
    servers = (resource_metadata or {}).get("authorization_servers")
    authorization_server_url: str = (
        servers[0] if servers else f"{_http_url(server_url).origin}/"
    )
    return OAuthServerInfo(
        authorization_server_url,
        discover_authorization_server_metadata(
            authorization_server_url,
            fetch=fetch,
            skip_issuer_validation=skip_issuer_validation,
            cancel_event=cancel_event,
        ),
        resource_metadata,
    )


def resource_url_from_server_url(value: str) -> str:
    """The server URL as a resource identifier: without its fragment."""

    return _http_url(value).href


def select_resource(
    server_url: str, metadata: OAuthProtectedResourceMetadata | None = None
) -> str | None:
    """The ``resource`` to request tokens for; ``None`` without metadata.

    The metadata's ``resource`` must have the server's origin and its path
    must be the server URL's path or a parent of it. Otherwise the document
    describes another resource, and ``OAuthFlowError`` is raised.
    """

    if metadata is None:
        return None
    requested = _http_url(server_url)
    resource: str = metadata["resource"]
    configured = parse_http_url(resource)
    if (
        configured is None
        or configured.origin != requested.origin
        or not _directory(requested).startswith(_directory(configured))
    ):
        raise OAuthFlowError(
            f"Protected resource {resource} does not match MCP server "
            f"{resource_url_from_server_url(server_url)}"
        )
    return resource


def _encoded_path(pathname: str) -> str:
    """``pathname`` as a WHATWG URL parser writes it: the characters of the
    path percent-encode set, and every one that is not ASCII, as their UTF-8
    bytes in ``%XX`` escapes; an escape that is there already stays as it
    was written."""

    return "".join(
        char
        if " " < char < "\x7f" and char not in _PATH_PERCENT_ENCODE
        # UTF-8 has no lone surrogate; the parser writes U+FFFD for one.
        else urllib.parse.quote(
            "\ufffd" if "\ud800" <= char <= "\udfff" else char, safe=""
        )
        for char in pathname
    )


def _directory(url: HttpUrl) -> str:
    pathname = _encoded_path(url.pathname)
    return pathname if pathname.endswith("/") else f"{pathname}/"
