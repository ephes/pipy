"""Tests for OAuth discovery, against the fake authorization server.

Tests that mirror a case of Pi's ``packages/mcp/test/oauth.test.ts`` name it
in a ``Pi:`` comment. Pi's cases run the whole flow; the part of each that is
discovery is mirrored here, the rest belongs to the flow's tests.
"""

from __future__ import annotations

import http.client
import json
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.mcp.config import parse_http_url
from pipy_harness.native.mcp.oauth.discovery import (
    build_authorization_server_discovery_urls,
    discover_authorization_server_metadata,
    discover_oauth_server_info,
    discover_protected_resource_metadata,
    parse_www_authenticate,
    resource_url_from_server_url,
    select_resource,
)
from pipy_harness.native.mcp.oauth.errors import (
    OAuthFlowError,
    OAuthIssuerMismatchError,
)
from pipy_harness.native.mcp.oauth.fetch import (
    OAuthResponse,
    create_oauth_fetch,
    is_network_error,
)
from pipy_harness.native.mcp.oauth.types import OAuthChallenge, OAuthServerInfo
from pipy_harness.native.mcp.protocol import LATEST_PROTOCOL_VERSION, McpAbortError

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_oauth import FakeOAuthServer, Reply  # noqa: E402

_WAIT_SECONDS = 10.0
_PRM = "/.well-known/oauth-protected-resource"
_AS = "/.well-known/oauth-authorization-server"
_OIDC = "/.well-known/openid-configuration"


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.005)


def _fetch_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "mcp-oauth-fetch"]


@pytest.fixture
def server() -> Iterator[FakeOAuthServer]:
    with FakeOAuthServer() as fake:
        yield fake
    # No test leaves a request thread behind.
    _wait_until(lambda: not _fetch_threads())


def _metadata(issuer: str, **fields: Any) -> dict[str, Any]:
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "response_types_supported": ["code"],
        **fields,
    }


# -- URL parts ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "pathname"),
    [
        ("https://h.example", "/"),
        ("https://h.example/a/b", "/a/b"),
        ("https://h.example/a/b/", "/a/b/"),
        ("https://h.example/a/./b", "/a/b"),
        ("https://h.example/a/../b", "/b"),
        ("https://h.example/a/b/..", "/a/"),
        ("https://h.example/a/b/.", "/a/b/"),
        ("https://h.example/../../a", "/a"),
        ("https://h.example/..", "/"),
        ("https://h.example/a/%2e%2E/b/%2E/c/.%2e", "/b/"),
        ("https://h.example//a//b", "//a//b"),
        ("https://h.example/a/.../b/..c", "/a/.../b/..c"),
    ],
)
def test_the_pathname_of_a_url_has_no_dot_segments(url: str, pathname: str) -> None:
    parsed = parse_http_url(url + "?q=1#f")
    assert parsed is not None
    assert parsed.pathname == pathname
    assert parsed.href == f"https://h.example{pathname}?q=1"


def test_origin_and_href_of_a_url() -> None:
    parsed = parse_http_url("https://user:secret@h.example/a")
    assert parsed is not None and parsed.has_user_info
    assert (parsed.origin, parsed.href) == ("https://h.example", "https://h.example/a")
    parsed = parse_http_url("https://h.example/a@b?c=@")
    assert parsed is not None and not parsed.has_user_info
    parsed = parse_http_url("HTTP://[::1]:8080/a/../b#f")
    assert parsed is not None
    assert (parsed.origin, parsed.href) == ("http://[::1]:8080", "http://[::1]:8080/b")
    parsed = parse_http_url("https://H.example:443")
    assert parsed is not None
    assert (parsed.origin, parsed.href) == ("https://h.example", "https://h.example/")


# -- WWW-Authenticate (OAU-05) --------------------------------------------------


@pytest.mark.parametrize("header", [None, "", "   ", "Basic realm=x", "Bearertoken"])
def test_no_challenge_without_a_bearer_or_dpop_header(header: str | None) -> None:
    assert parse_www_authenticate(header) == OAuthChallenge()


def test_a_bearer_challenge_is_parsed() -> None:
    assert parse_www_authenticate(
        'Bearer realm="mcp", resource_metadata="https://mcp.example/.well-known/x", '
        'scope="read write", error="insufficient_scope", '
        'error_description="More scope, please"'
    ) == OAuthChallenge(
        resource_metadata_url="https://mcp.example/.well-known/x",
        scope="read write",
        error="insufficient_scope",
        error_description="More scope, please",
    )


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer", OAuthChallenge()),
        ("DPoP scope=read", OAuthChallenge(scope="read")),
        (
            "  bEaReR SCOPE=read,Error=invalid_token",
            OAuthChallenge(scope="read", error="invalid_token"),
        ),
        ("Bearer\tscope=a,b", OAuthChallenge(scope="a")),
        ("Bearer error_description=only", OAuthChallenge(error_description="only")),
        ("Bearer xscope=no, the_error=no", OAuthChallenge()),
        # As in Pi, quoted text is not skipped when looking for a name.
        ('Bearer realm="a scope=inner"', OAuthChallenge(scope='inner"')),
    ],
)
def test_challenge_fields_in_other_spellings(
    header: str, expected: OAuthChallenge
) -> None:
    assert parse_www_authenticate(header) == expected


def test_empty_challenge_values_count_as_absent() -> None:
    # Pi: "discovers, registers, authorizes with PKCE, and refreshes on 401":
    # an empty scope falls through to the resource metadata's scopes.
    assert parse_www_authenticate(
        'Bearer resource_metadata="http://127.0.0.1:1/.well-known/'
        'oauth-protected-resource/mcp", scope=""'
    ) == OAuthChallenge(
        resource_metadata_url=(
            "http://127.0.0.1:1/.well-known/oauth-protected-resource/mcp"
        )
    )
    assert parse_www_authenticate('Bearer error="", scope=""') == OAuthChallenge()


def test_a_step_up_challenge_is_parsed() -> None:
    # Pi: "asks for authorization instead of refreshing when the server needs
    # more scope" sends this header.
    assert parse_www_authenticate(
        'Bearer error="insufficient_scope", scope="repo admin"'
    ) == OAuthChallenge(scope="repo admin", error="insufficient_scope")


@pytest.mark.parametrize("value", ["/relative", "not a url", "https://"])
def test_resource_metadata_that_is_no_url_is_left_out(value: str) -> None:
    assert parse_www_authenticate(
        f'Bearer resource_metadata="{value}", scope="s"'
    ) == OAuthChallenge(scope="s")


def test_only_the_first_scheme_of_a_header_counts() -> None:
    assert parse_www_authenticate('Basic realm="x", Bearer scope="s"') == (
        OAuthChallenge()
    )


# -- candidate URLs (OAU-03) ----------------------------------------------------


@pytest.mark.parametrize(
    "url", ["https://auth.example", "https://auth.example/", "HTTPS://Auth.Example:443"]
)
def test_an_issuer_without_a_path_has_two_candidate_urls(url: str) -> None:
    assert build_authorization_server_discovery_urls(url) == [
        ("https://auth.example/.well-known/oauth-authorization-server", "oauth"),
        ("https://auth.example/.well-known/openid-configuration", "oidc"),
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://auth.example:8080/tenant/a",
        "http://auth.example:8080/tenant/a/",
        "http://auth.example:8080/tenant/a?x=1#f",
        "http://auth.example:8080/tenant/b/../a",
        "http://auth.example:8080/./tenant/%2E%2e/tenant/a/.",
    ],
)
def test_an_issuer_with_a_path_has_three_candidate_urls(url: str) -> None:
    origin = "http://auth.example:8080"
    assert build_authorization_server_discovery_urls(url) == [
        (f"{origin}/.well-known/oauth-authorization-server/tenant/a", "oauth"),
        (f"{origin}/.well-known/openid-configuration/tenant/a", "oidc"),
        (f"{origin}/tenant/a/.well-known/openid-configuration", "oidc"),
    ]


@pytest.mark.parametrize("url", ["urn:example:issuer", "auth.example", ""])
def test_an_issuer_that_is_no_http_url_cannot_be_asked(url: str) -> None:
    with pytest.raises(http.client.InvalidURL) as raised:
        build_authorization_server_discovery_urls(url)
    assert is_network_error(raised.value)


# -- protected resource metadata (OAU-02) ---------------------------------------


def test_protected_resource_metadata_is_found_below_the_server_path(
    server: FakeOAuthServer,
) -> None:
    document = {"resource": f"{server.origin}/mcp", "scopes_supported": ["a"]}
    server.json(f"{_PRM}/mcp", document)
    for url in ("/mcp", "/mcp/", "/mcp?x=1#f", "/x/../mcp"):
        assert discover_protected_resource_metadata(server.origin + url) == document
    assert server.paths == [f"{_PRM}/mcp"] * 4
    headers = server.requests[0].headers
    assert headers["accept"] == "application/json"
    assert headers["mcp-protocol-version"] == LATEST_PROTOCOL_VERSION
    assert server.requests[0].query == ""


def test_the_metadata_request_carries_the_given_protocol_version(
    server: FakeOAuthServer,
) -> None:
    server.json(_PRM, {"resource": server.origin})
    discover_protected_resource_metadata(server.origin, protocol_version="2025-03-26")
    assert server.requests[0].headers["mcp-protocol-version"] == "2025-03-26"


@pytest.mark.parametrize("status", [404, 401, 502])
def test_protected_resource_metadata_falls_back_to_the_root_form(
    server: FakeOAuthServer, status: int
) -> None:
    document = {"resource": server.origin}
    server.route(f"{_PRM}/a/mcp", Reply(status))
    server.json(_PRM, document)
    assert discover_protected_resource_metadata(f"{server.origin}/a/mcp") == document
    assert server.paths == [f"{_PRM}/a/mcp", _PRM]


def test_another_failure_status_does_not_try_the_root_form(
    server: FakeOAuthServer,
) -> None:
    server.route(f"{_PRM}/mcp", Reply(500))
    server.json(_PRM, {"resource": server.origin})
    with pytest.raises(OAuthFlowError) as raised:
        discover_protected_resource_metadata(f"{server.origin}/mcp")
    assert str(raised.value) == "HTTP 500 loading OAuth protected resource metadata"
    assert server.paths == [f"{_PRM}/mcp"]


def test_a_server_without_a_path_is_asked_once(server: FakeOAuthServer) -> None:
    for url in (server.origin, f"{server.origin}/"):
        with pytest.raises(OAuthFlowError) as raised:
            discover_protected_resource_metadata(url)
        assert str(raised.value) == (
            "HTTP 404 loading OAuth protected resource metadata"
        )
    assert server.paths == [_PRM, _PRM]


def test_a_named_metadata_url_is_the_only_one_asked(server: FakeOAuthServer) -> None:
    document = {"resource": f"{server.origin}/mcp"}
    server.json("/meta", document)
    server.json(_PRM, {"resource": f"{server.origin}/other"})
    assert (
        discover_protected_resource_metadata(
            f"{server.origin}/mcp", resource_metadata_url=f"{server.origin}/meta"
        )
        == document
    )
    with pytest.raises(OAuthFlowError, match="HTTP 404"):
        discover_protected_resource_metadata(
            f"{server.origin}/mcp", resource_metadata_url=f"{server.origin}/gone"
        )
    assert server.paths == ["/meta", "/gone"]


@pytest.mark.parametrize("path", ["/dir/../meta", "/dir/%2e%2E/./meta", "/meta#f"])
def test_a_named_metadata_url_is_asked_without_dot_segments_and_fragment(
    server: FakeOAuthServer, path: str
) -> None:
    document = {"resource": f"{server.origin}/mcp"}
    server.json("/meta", document)
    info = discover_oauth_server_info(
        f"{server.origin}/mcp", resource_metadata_url=server.origin + path
    )
    assert info.resource_metadata == document
    assert server.paths[0] == "/meta"


def test_a_body_that_is_not_the_document_is_an_error(server: FakeOAuthServer) -> None:
    server.route(f"{_PRM}/html", Reply(200, {}, b"<html>"))
    server.json(f"{_PRM}/list", ["x"])
    server.json(f"{_PRM}/empty", {})
    with pytest.raises(ValueError):
        discover_protected_resource_metadata(f"{server.origin}/html")
    for path in ("/list", "/empty"):
        with pytest.raises(OAuthFlowError, match="Invalid OAuth protected resource"):
            discover_protected_resource_metadata(server.origin + path)


# -- authorization server metadata (OAU-03, OAU-04) -----------------------------


def test_authorization_server_metadata_is_taken_from_the_first_url_that_has_it(
    server: FakeOAuthServer,
) -> None:
    issuer = f"{server.origin}/tenant"
    document = _metadata(issuer)
    server.route(f"{_AS}/tenant", Reply(404))
    server.route(f"{_OIDC}/tenant", Reply(502))
    server.json(f"/tenant{_OIDC}", document)
    assert discover_authorization_server_metadata(issuer) == document
    assert server.paths == [f"{_AS}/tenant", f"{_OIDC}/tenant", f"/tenant{_OIDC}"]
    for request in server.requests:
        assert request.headers["accept"] == "application/json"
        assert request.headers["mcp-protocol-version"] == LATEST_PROTOCOL_VERSION


def test_later_urls_are_not_asked_once_a_document_was_found(
    server: FakeOAuthServer,
) -> None:
    server.json(_AS, _metadata(server.origin))
    server.json(_OIDC, _metadata("https://other.example"))
    metadata = discover_authorization_server_metadata(
        server.origin, protocol_version="2025-06-18"
    )
    assert metadata is not None and metadata["issuer"] == server.origin
    assert server.paths == [_AS]
    assert server.requests[0].headers["mcp-protocol-version"] == "2025-06-18"


def test_a_server_with_no_document_has_no_metadata(server: FakeOAuthServer) -> None:
    assert discover_authorization_server_metadata(server.origin) is None
    assert discover_authorization_server_metadata(f"{server.origin}/t/") is None
    assert server.paths == [
        _AS,
        _OIDC,
        f"{_AS}/t",
        f"{_OIDC}/t",
        f"/t{_OIDC}",
    ]


@pytest.mark.parametrize("status", [500, 503, 302])
def test_another_failure_status_ends_the_search(
    server: FakeOAuthServer, status: int
) -> None:
    server.route(_AS, Reply(status))
    server.json(_OIDC, _metadata(server.origin))
    with pytest.raises(OAuthFlowError) as raised:
        discover_authorization_server_metadata(server.origin)
    assert str(raised.value) == (
        f"HTTP {status} loading authorization server metadata from {server.origin}{_AS}"
    )
    assert server.paths == [_AS]


def test_rejects_authorization_metadata_whose_issuer_does_not_match_discovery(
    server: FakeOAuthServer,
) -> None:
    # Pi: "rejects authorization metadata whose issuer does not match discovery"
    server.json(
        _AS,
        {
            "issuer": "https://attacker.example",
            "authorization_endpoint": f"{server.origin}/authorize",
            "token_endpoint": f"{server.origin}/token",
            "response_types_supported": ["code"],
        },
    )
    with pytest.raises(OAuthIssuerMismatchError) as raised:
        discover_authorization_server_metadata(server.origin)
    assert str(raised.value) == (
        f'OAuth issuer mismatch: expected "{server.origin}", '
        'received "https://attacker.example"'
    )
    assert (raised.value.expected, raised.value.received) == (
        server.origin,
        "https://attacker.example",
    )


def test_the_issuer_may_differ_by_one_trailing_slash(server: FakeOAuthServer) -> None:
    origin = server.origin
    server.json(_AS, _metadata(origin))
    server.json(f"{_AS}/slash", _metadata(f"{origin}/slash/"))
    server.json(f"{_AS}/two", _metadata(f"{origin}/two//"))
    server.json(f"{_AS}/case", _metadata(f"{origin}/CASE"))
    assert discover_authorization_server_metadata(f"{origin}/") is not None
    assert discover_authorization_server_metadata(f"{origin}/slash") is not None
    for issuer in (f"{origin}/two", f"{origin}/case"):
        with pytest.raises(OAuthIssuerMismatchError):
            discover_authorization_server_metadata(issuer)


def test_the_issuer_check_can_be_skipped(server: FakeOAuthServer) -> None:
    document = _metadata("https://other.example")
    server.json(_AS, document)
    assert (
        discover_authorization_server_metadata(
            server.origin, skip_issuer_validation=True
        )
        == document
    )


def test_an_invalid_metadata_document_ends_the_search(server: FakeOAuthServer) -> None:
    server.json(_AS, {"issuer": server.origin})
    server.json(_OIDC, _metadata(server.origin))
    with pytest.raises(OAuthFlowError, match="Invalid response_types_supported"):
        discover_authorization_server_metadata(server.origin)
    assert server.paths == [_AS]


# -- the whole of discovery (OAU-02, OAU-03) ------------------------------------


def test_discovery_finds_the_authorization_server_the_resource_names(
    server: FakeOAuthServer,
) -> None:
    # Pi: "discovers, registers, authorizes with PKCE, and refreshes on 401"
    with FakeOAuthServer() as auth:
        resource = {
            "resource": f"{server.origin}/mcp",
            "authorization_servers": [auth.origin, "https://second.example"],
            "scopes_supported": ["org:read"],
        }
        metadata = _metadata(auth.origin, registration_endpoint=f"{auth.origin}/r")
        server.json(f"{_PRM}/mcp", resource)
        auth.json(_AS, metadata)
        info = discover_oauth_server_info(f"{server.origin}/mcp")
        assert info == OAuthServerInfo(auth.origin, metadata, resource)
        assert server.paths == [f"{_PRM}/mcp"]
        assert auth.paths == [_AS]


def test_without_resource_metadata_the_server_s_origin_is_the_authorization_server(
    server: FakeOAuthServer,
) -> None:
    # The issuer has no trailing slash; the origin used as fallback has one.
    metadata = _metadata(server.origin)
    server.json(_AS, metadata)
    info = discover_oauth_server_info(f"{server.origin}/a/mcp?x=1")
    assert info == OAuthServerInfo(f"{server.origin}/", metadata, None)
    assert server.paths == [f"{_PRM}/a/mcp", _PRM, _AS]


def test_invalid_resource_metadata_falls_back_to_the_server_s_origin(
    server: FakeOAuthServer,
) -> None:
    # Pi: "shares one refresh between concurrent 401s when refresh tokens rotate"
    metadata = _metadata(server.origin)
    server.json(
        f"{_PRM}/mcp",
        {"resource": f"{server.origin}/mcp", "authorization_servers": ["not a url"]},
    )
    server.json(_AS, metadata)
    info = discover_oauth_server_info(f"{server.origin}/mcp")
    assert info == OAuthServerInfo(f"{server.origin}/", metadata, None)


@pytest.mark.parametrize(
    "reply", [Reply(500), Reply(200, {}, b"<html>"), Reply(200, {}, b"[]")]
)
def test_resource_metadata_that_cannot_be_used_means_none(
    server: FakeOAuthServer, reply: Reply
) -> None:
    server.route(f"{_PRM}/mcp", reply)
    info = discover_oauth_server_info(f"{server.origin}/mcp")
    assert info == OAuthServerInfo(f"{server.origin}/", None, None)


def test_resource_metadata_without_servers_uses_the_server_s_origin(
    server: FakeOAuthServer,
) -> None:
    variants: list[dict[str, Any]] = [{}, {"authorization_servers": []}]
    for servers in variants:
        resource = {"resource": f"{server.origin}/mcp", **servers}
        server.json(f"{_PRM}/mcp", resource)
        info = discover_oauth_server_info(f"{server.origin}/mcp")
        assert info == OAuthServerInfo(f"{server.origin}/", None, resource)


def test_the_challenge_s_metadata_url_is_asked_instead_of_the_well_known_ones(
    server: FakeOAuthServer,
) -> None:
    resource = {"resource": f"{server.origin}/mcp"}
    server.json("/meta", resource)
    info = discover_oauth_server_info(
        f"{server.origin}/mcp", resource_metadata_url=f"{server.origin}/meta"
    )
    assert info.resource_metadata == resource
    info = discover_oauth_server_info(
        f"{server.origin}/mcp", resource_metadata_url=f"{server.origin}/gone"
    )
    assert info.resource_metadata is None
    assert server.paths == ["/meta", _AS, _OIDC, "/gone", _AS, _OIDC]


def test_a_network_failure_loading_resource_metadata_fails_discovery(
    server: FakeOAuthServer,
) -> None:
    server.route(f"{_PRM}/mcp", Reply(drop=True))
    with pytest.raises((OSError, http.client.HTTPException)):
        discover_oauth_server_info(f"{server.origin}/mcp")
    assert server.paths == [f"{_PRM}/mcp"]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(OSError):
        discover_oauth_server_info(f"http://127.0.0.1:{port}/mcp")


def test_a_metadata_url_that_cannot_be_requested_fails_discovery(
    server: FakeOAuthServer,
) -> None:
    with pytest.raises(http.client.InvalidURL):
        discover_oauth_server_info(
            f"{server.origin}/mcp", resource_metadata_url="file:///etc/passwd"
        )
    with pytest.raises(http.client.InvalidURL):
        discover_oauth_server_info("ftp://mcp.example/mcp")
    server.json(
        f"{_PRM}/mcp",
        {"resource": f"{server.origin}/mcp", "authorization_servers": ["urn:x:y"]},
    )
    with pytest.raises(http.client.InvalidURL):
        discover_oauth_server_info(f"{server.origin}/mcp")


@pytest.mark.parametrize("user_info", ["user:secret@", "user@", "@"])
def test_a_given_metadata_url_with_user_info_fails_discovery(
    server: FakeOAuthServer, user_info: str
) -> None:
    # The documents exist, and would be found without the user info.
    server.json("/meta", {"resource": f"{server.origin}/mcp"})
    server.json("/idp.json", _metadata("https://idp.example"))
    url = f"http://{user_info}127.0.0.1:{server.port}"
    with pytest.raises(http.client.InvalidURL) as raised:
        discover_oauth_server_info(
            f"{server.origin}/mcp", resource_metadata_url=f"{url}/meta"
        )
    assert str(raised.value) == f"Cannot request URL {url}/meta"
    with pytest.raises(http.client.InvalidURL) as raised:
        discover_oauth_server_info(
            f"{server.origin}/mcp", authorization_server_metadata_url=f"{url}/idp.json"
        )
    assert str(raised.value) == f"Cannot request URL {url}/idp.json"
    with pytest.raises(http.client.InvalidURL):
        discover_protected_resource_metadata(
            f"{server.origin}/mcp", resource_metadata_url=f"{url}/meta"
        )
    assert "/meta" not in server.paths and "/idp.json" not in server.paths


def test_user_info_is_not_part_of_an_origin(server: FakeOAuthServer) -> None:
    # As in Pi, where the well-known URLs are built on `URL.origin`.
    issuer = f"http://user:secret@127.0.0.1:{server.port}/t"
    assert build_authorization_server_discovery_urls(issuer)[0] == (
        f"{server.origin}{_AS}/t",
        "oauth",
    )


def test_a_time_limit_loading_resource_metadata_means_none(
    server: FakeOAuthServer,
) -> None:
    metadata = _metadata(server.origin)
    server.route(f"{_PRM}/mcp", Reply(hang=True))
    server.json(_AS, metadata)
    info = discover_oauth_server_info(
        f"{server.origin}/mcp", fetch=create_oauth_fetch(timeout_ms=100)
    )
    assert info == OAuthServerInfo(f"{server.origin}/", metadata, None)


def test_stops_when_cancelled_without_asking_further(server: FakeOAuthServer) -> None:
    # Pi: "stops when its signal aborts, without falling back to a redirect"
    # (#10565), the discovery part: the server accepts the request and never
    # answers.
    server.route(f"{_PRM}/mcp", Reply(hang=True))
    cancel = threading.Event()
    raised: list[BaseException] = []

    def call() -> None:
        try:
            discover_oauth_server_info(f"{server.origin}/mcp", cancel_event=cancel)
        except McpAbortError as error:
            raised.append(error)

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    _wait_until(lambda: server.paths == [f"{_PRM}/mcp"])
    cancel.set()
    thread.join(_WAIT_SECONDS)
    assert not thread.is_alive()
    assert len(raised) == 1
    assert server.paths == [f"{_PRM}/mcp"]


def test_cancelling_stops_the_search_for_authorization_server_metadata(
    server: FakeOAuthServer,
) -> None:
    cancel = threading.Event()

    def hang_then_cancel(request: Any) -> Reply:
        cancel.set()
        return Reply(hang=True)

    server.route(_AS, hang_then_cancel)
    with pytest.raises(McpAbortError):
        discover_oauth_server_info(server.origin, cancel_event=cancel)
    assert server.paths == [_PRM, _AS]


def test_uses_a_configured_authorization_server_metadata_document_as_is(
    server: FakeOAuthServer,
) -> None:
    # Pi: "uses a configured authorization server metadata document as is"
    # (#10172), the discovery part.
    resource = {
        "resource": f"{server.origin}/mcp",
        # Names the MCP server itself, which serves no such metadata.
        "authorization_servers": [server.origin],
    }
    metadata = {
        # Not derivable from the document URL; a configured document is not
        # checked.
        "issuer": "https://idp.example",
        "authorization_endpoint": f"{server.origin}/idp/authorize",
        "token_endpoint": f"{server.origin}/idp/token",
        "response_types_supported": ["code"],
    }
    server.json(f"{_PRM}/mcp", resource)
    server.json("/idp/metadata.json", metadata)
    info = discover_oauth_server_info(
        f"{server.origin}/mcp",
        authorization_server_metadata_url=f"{server.origin}/idp/metadata.json",
    )
    assert info == OAuthServerInfo("https://idp.example", metadata, resource)
    assert server.paths == [f"{_PRM}/mcp", "/idp/metadata.json"]
    assert server.requests[1].headers["mcp-protocol-version"] == (
        LATEST_PROTOCOL_VERSION
    )


def test_a_configured_metadata_document_that_cannot_be_loaded_is_an_error(
    server: FakeOAuthServer,
) -> None:
    url = f"{server.origin}/idp/metadata.json"
    with pytest.raises(OAuthFlowError) as raised:
        discover_oauth_server_info(
            f"{server.origin}/mcp", authorization_server_metadata_url=url
        )
    assert str(raised.value) == (
        f"HTTP 404 loading authorization server metadata from {url}"
    )
    # The well-known URLs are no fallback for a configured document.
    assert server.paths == [f"{_PRM}/mcp", _PRM, "/idp/metadata.json"]


def test_a_configured_metadata_url_is_asked_without_dot_segments() -> None:
    urls: list[str] = []

    def fetch(url: str, **options: Any) -> OAuthResponse:
        urls.append(url)
        return OAuthResponse(404, {})

    with pytest.raises(OAuthFlowError) as raised:
        discover_oauth_server_info(
            "https://mcp.example/a/../mcp",
            resource_metadata_url="HTTPS://MCP.example:443/x/../meta?v=1#f",
            authorization_server_metadata_url="https://idp.example/a/%2E%2e/m.json",
            fetch=fetch,
        )
    assert urls == ["https://mcp.example/meta?v=1", "https://idp.example/m.json"]
    assert str(raised.value) == (
        "HTTP 404 loading authorization server metadata from https://idp.example/m.json"
    )


def test_every_request_goes_through_the_given_fetch() -> None:
    origin = "https://mcp.example"
    resource = {"resource": f"{origin}/mcp", "authorization_servers": [origin]}
    documents = {
        f"{origin}{_PRM}/mcp": resource,
        f"{origin}{_AS}": _metadata(origin),
    }
    cancel = threading.Event()
    calls: list[tuple[str, str, threading.Event | None]] = []

    def fetch(
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        cancel_event: threading.Event | None = None,
    ) -> OAuthResponse:
        assert body is None
        calls.append((method, url, cancel_event))
        return OAuthResponse(200, {}, json.dumps(documents[url]).encode())

    info = discover_oauth_server_info(f"{origin}/mcp", fetch=fetch, cancel_event=cancel)
    assert info == OAuthServerInfo(origin, _metadata(origin), resource)
    assert calls == [
        ("GET", f"{origin}{_PRM}/mcp", cancel),
        ("GET", f"{origin}{_AS}", cancel),
    ]


def test_an_error_of_the_fetch_that_is_no_network_failure_means_no_resource_metadata() -> (
    None
):
    def fetch(url: str, **options: Any) -> OAuthResponse:
        if _PRM in url:
            raise RuntimeError("no")
        return OAuthResponse(404, {})

    info = discover_oauth_server_info("https://mcp.example/mcp", fetch=fetch)
    assert info == OAuthServerInfo("https://mcp.example/", None, None)


# -- the resource (OAU-06) ------------------------------------------------------


def test_no_resource_without_metadata() -> None:
    assert select_resource("https://mcp.example/mcp") is None
    assert select_resource("https://mcp.example/mcp", None) is None


@pytest.mark.parametrize(
    ("server_url", "resource"),
    [
        ("https://mcp.example/mcp", "https://mcp.example/mcp"),
        ("https://mcp.example/mcp", "https://mcp.example/mcp/"),
        ("https://mcp.example/mcp/", "https://mcp.example/mcp"),
        ("https://mcp.example/a/b/mcp?x=1#f", "https://mcp.example/a/b"),
        ("https://mcp.example/a/mcp", "https://mcp.example"),
        ("https://mcp.example/a/mcp", "https://mcp.example/"),
        ("https://mcp.example", "https://mcp.example/"),
        ("https://MCP.example:443/mcp", "https://mcp.EXAMPLE/mcp"),
        ("http://127.0.0.1:8080/mcp", "http://127.0.0.1:8080/mcp?tenant=1"),
        ("https://mcp.example/a/x/../mcp", "https://mcp.example/a/mcp"),
        ("https://mcp.example/a/mcp", "https://mcp.example/a/x/.."),
        # Paths are compared as a WHATWG URL parser writes them: characters
        # of the path percent-encode set and non-ASCII ones are encoded.
        ("https://mcp.example/café/mcp", "https://mcp.example/caf%C3%A9"),
        ("https://mcp.example/caf%C3%A9/mcp", "https://mcp.example/café"),
        ("https://mcp.example/a b/mcp", "https://mcp.example/a%20b"),
        (
            'https://mcp.example/a^`{}"<>\x7f/mcp',
            "https://mcp.example/a%5E%60%7B%7D%22%3C%3E%7F",
        ),
        ("https://mcp.example/\ud800/mcp", "https://mcp.example/%EF%BF%BD"),
    ],
)
def test_a_resource_at_or_above_the_server_url_is_selected(
    server_url: str, resource: str
) -> None:
    # The value is sent as the server wrote it.
    assert select_resource(server_url, {"resource": resource}) == resource


@pytest.mark.parametrize(
    ("server_url", "resource"),
    [
        ("https://mcp.example/mcp", "https://other.example/mcp"),
        ("https://mcp.example/mcp", "http://mcp.example/mcp"),
        ("https://mcp.example/mcp", "https://mcp.example:8443/mcp"),
        ("https://mcp.example/mcp", "https://mcp.example/mcp/deeper"),
        ("https://mcp.example/mcp2", "https://mcp.example/mcp"),
        ("https://mcp.example/a", "https://mcp.example/b"),
        ("https://mcp.example", "https://mcp.example/mcp"),
        # The path the server is asked for decides, not the one written.
        ("https://mcp.example/a/../mcp", "https://mcp.example/a"),
        ("https://mcp.example/a/%2e%2E/mcp", "https://mcp.example/a"),
        ("https://mcp.example/mcp", "urn:example:mcp"),
        ("https://mcp.example/mcp", "https:mcp.example/mcp"),
        # An escape is kept as written, and `|` is not encoded (as by Node).
        ("https://mcp.example/café/mcp", "https://mcp.example/caf%c3%a9"),
        ("https://mcp.example/%41/mcp", "https://mcp.example/A"),
        ("https://mcp.example/a|b/mcp", "https://mcp.example/a%7Cb"),
    ],
)
def test_a_resource_that_is_not_the_server_s_fails(
    server_url: str, resource: str
) -> None:
    with pytest.raises(OAuthFlowError) as raised:
        select_resource(server_url, {"resource": resource})
    assert str(raised.value).startswith(
        f"Protected resource {resource} does not match MCP server https://mcp.example"
    )


def test_the_mismatch_names_the_server_url_without_its_fragment() -> None:
    with pytest.raises(OAuthFlowError) as raised:
        select_resource(
            "https://MCP.example/a/../mcp?x=1#f", {"resource": "https://x.example"}
        )
    assert str(raised.value) == (
        "Protected resource https://x.example does not match MCP server "
        "https://mcp.example/mcp?x=1"
    )


def test_the_resource_url_of_a_server_url_has_no_fragment() -> None:
    assert resource_url_from_server_url("https://mcp.example/mcp?x=1#frag") == (
        "https://mcp.example/mcp?x=1"
    )
    assert resource_url_from_server_url("https://mcp.example") == (
        "https://mcp.example/"
    )
    with pytest.raises(http.client.InvalidURL):
        resource_url_from_server_url("mcp.example")
    with pytest.raises(http.client.InvalidURL):
        select_resource("mcp.example", {"resource": "https://mcp.example"})
