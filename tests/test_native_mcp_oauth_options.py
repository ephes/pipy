"""Tests for the ``oauth`` options of an MCP server entry in a sign-in and
in a refresh (MCP parity plan S32): a client registered beforehand, the
callback URL and port, the client name, Client ID Metadata Documents, and
the configured metadata URL of the authorization server.

Tests that mirror a case of Pi's ``mcp-oauth-refresh.test.ts`` ("MCP OAuth
client ID metadata documents", "uses the configured authorization server
metadata URL") or ``suite/agent-session-mcp-oauth.test.ts`` name it in a
``Pi:`` comment. Pi's documents are at ``https://pi.dev/oauth``; pipy's are
under ``oauth.clientMetadataBaseUrl`` (plan DEV-06).
"""

from __future__ import annotations

import base64
import hashlib
import socket
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.mcp.config import LoadedMcpConfig, McpServerEntry
from pipy_harness.native.mcp.credentials import (
    McpOAuthCredentialStore,
    McpOAuthServerStore,
    McpOAuthSettings,
    callback_id,
    client_metadata_document,
    mcp_auth_path,
)
from pipy_harness.native.mcp.manager import McpManager
from pipy_harness.native.mcp.oauth.errors import OAuthFlowError
from pipy_harness.native.mcp.oauth.flow import OAuthClientMetadataDocument
from pipy_harness.native.mcp.sign_in import sign_in_mcp_server, wait_for_cancel

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_oauth import FakeBrowser, FakeOAuthMcpServer  # noqa: E402

_WAIT_SECONDS = 10.0
_BACKGROUND = ("mcp-oauth-fetch", "mcp-sign-in-prompt", "mcp-oauth-callback", "mcp-")
_BASE = "https://id.example/oauth"
_CIMD = McpOAuthSettings(
    client_registration="cimd", client_metadata_base_url=f"{_BASE}/"
)
_UNSUPPORTED = "does not support Client ID Metadata Documents for public clients"


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.005)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def _id(server_url: str) -> str:
    """Computed like Codex: the first 9 bytes of the SHA-256 of the URL."""

    digest = hashlib.sha256(server_url.encode()).digest()
    return base64.urlsafe_b64encode(digest[:9]).decode()


class _Browser:
    """The user of a sign-in, whose browser reaches the loopback port.

    With ``paste`` the browser is not followed; the function is given the
    authorization URL and returns what the user pastes instead.
    """

    def __init__(self, paste: Callable[[str], str] | None = None) -> None:
        self._paste = paste
        self.opened: list[str] = []

    def show_authorization_url(self, url: str) -> None:
        self.opened.append(url)
        if self._paste is None:
            FakeBrowser().open(url)

    def prompt_for_redirect_url(self, cancel_event: threading.Event) -> str | None:
        if self._paste is not None:
            return self._paste(self.opened[-1])
        wait_for_cancel(cancel_event)
        return None

    def query(self, index: int = -1) -> dict[str, str]:
        query = urllib.parse.urlsplit(self.opened[index]).query
        return dict(urllib.parse.parse_qsl(query, True))


class _Fake:
    """A fake OAuth MCP server with the store of one configured server."""

    def __init__(self, server: FakeOAuthMcpServer, home: Path) -> None:
        self.server = server
        self.store: McpOAuthServerStore = McpOAuthCredentialStore(
            mcp_auth_path(home)
        ).for_server("test", server.url)

    def sign_in(
        self, settings: McpOAuthSettings, browser: _Browser | None = None
    ) -> _Browser:
        browser = browser or _Browser()
        sign_in_mcp_server(
            server_url=self.server.url,
            store=self.store,
            settings=settings,
            prompt=browser,
        )
        return browser

    def stored(self) -> dict[str, Any]:
        state = self.store.load()
        assert state is not None
        return state


_StartServer = Callable[..., _Fake]


@pytest.fixture
def start_server(tmp_path: Path) -> Iterator[_StartServer]:
    """Starts fake servers: ``cimd`` advertises Client ID Metadata
    Documents, ``iss_parameter`` advertises and sends the ``iss`` parameter
    of authorization responses (the options of Pi's ``startOAuthMcpServer``).
    """

    before = set(threading.enumerate())
    servers: list[FakeOAuthMcpServer] = []

    def start(*, cimd: bool = False, iss_parameter: bool = False) -> _Fake:
        metadata: dict[str, Any] = {}
        if cimd:
            metadata["client_id_metadata_document_supported"] = True
        if iss_parameter:
            metadata["authorization_response_iss_parameter_supported"] = True
        server = FakeOAuthMcpServer(metadata=metadata)
        server.send_iss = iss_parameter
        servers.append(server)
        home = tmp_path / f"home-{len(servers)}"
        home.mkdir()
        return _Fake(server, home)

    yield start
    for server in servers:
        server.close()
    # No test leaves a thread of a manager or of a sign-in behind.
    _wait_until(
        lambda: (
            not [
                thread
                for thread in set(threading.enumerate()) - before
                if thread.name.startswith(_BACKGROUND)
            ]
        )
    )


# -- the document -----------------------------------------------------------------

_SUPPORTED: dict[str, Any] = {
    "client_id_metadata_document_supported": True,
    "token_endpoint_auth_methods_supported": ["client_secret_basic", "none"],
}
_REDIRECT = "http://127.0.0.1:8123/callback"


def test_the_callback_id_is_computed_over_the_server_url_without_its_fragment() -> None:
    # OAU-34.
    url = "https://mcp.example/mcp?tenant=a"

    assert callback_id(url) == _id(url)
    assert callback_id(f"{url}#section") == _id(url)
    assert len(callback_id(url)) == 12
    # The URL is hashed as the config parser writes it.
    assert callback_id("HTTPS://MCP.Example:443") == _id("https://mcp.example/")
    assert callback_id("https://fa\u00df.de/") == _id("https://xn--fa-hia.de/")
    assert callback_id(url) != callback_id("https://mcp.example/other")
    with pytest.raises(ValueError, match="Invalid MCP server URL"):
        callback_id("mcp.example")


def test_a_server_that_names_its_issuer_gets_the_shared_document() -> None:
    # OAU-34.
    metadata = {**_SUPPORTED, "authorization_response_iss_parameter_supported": True}

    for base in (_BASE, f"{_BASE}/"):
        assert client_metadata_document(
            "https://mcp.example/mcp", _REDIRECT, base, metadata
        ) == OAuthClientMetadataDocument(f"{_BASE}/client.json", _REDIRECT)
    # A base URL without a path.
    assert (
        client_metadata_document(
            "https://mcp.example/mcp", _REDIRECT, "https://id.example", metadata
        ).url
        == "https://id.example/client.json"
    )


def test_any_other_server_gets_a_document_and_a_redirect_uri_of_its_own() -> None:
    # OAU-34.
    server_url = "https://mcp.example/mcp"
    identifier = _id(server_url)

    for redirect, expected in (
        (_REDIRECT, f"http://127.0.0.1:8123/callback/{identifier}"),
        (
            "http://localhost:9000/callback",
            f"http://localhost:9000/callback/{identifier}",
        ),
    ):
        assert client_metadata_document(
            server_url, redirect, f"{_BASE}/", _SUPPORTED
        ) == OAuthClientMetadataDocument(f"{_BASE}/{identifier}/client.json", expected)


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {**_SUPPORTED, "client_id_metadata_document_supported": False},
        {**_SUPPORTED, "token_endpoint_auth_methods_supported": ["client_secret_post"]},
        {"client_id_metadata_document_supported": True},
    ],
)
def test_a_server_without_documents_for_public_clients_is_refused(
    metadata: dict[str, Any] | None,
) -> None:
    # OAU-33.
    with pytest.raises(OAuthFlowError) as raised:
        client_metadata_document("https://mcp.example/mcp", _REDIRECT, _BASE, metadata)

    assert str(raised.value) == (
        "The authorization server does not support Client ID Metadata Documents "
        'for public clients; remove oauth.clientRegistration "cimd"'
    )


def test_a_document_needs_the_base_url() -> None:
    # DEV-06: the config requires the field; settings made by hand may lack it.
    with pytest.raises(OAuthFlowError, match="requires oauth.clientMetadataBaseUrl"):
        client_metadata_document("https://mcp.example/mcp", _REDIRECT, None, _SUPPORTED)


# -- Client ID Metadata Documents in a sign-in ----------------------------------


def test_registers_dynamically_by_default_even_when_the_server_supports_documents(
    start_server: _StartServer,
) -> None:
    # Pi: "registers dynamically by default, even when the server supports
    # documents". OAU-33.
    fake = start_server(cimd=True, iss_parameter=True)

    fake.sign_in(McpOAuthSettings())

    assert len(fake.server.registrations) == 1
    assert fake.server.authorizations[0]["client_id"] == "client-1"


def test_uses_the_document_when_authorization_responses_name_their_issuer(
    start_server: _StartServer,
) -> None:
    # Pi: "uses pi's document when authorization responses name their
    # issuer". OAU-33, OAU-34.
    fake = start_server(cimd=True, iss_parameter=True)
    server = fake.server

    fake.sign_in(_CIMD)

    (authorization,) = server.authorizations
    assert authorization["client_id"] == f"{_BASE}/client.json"
    redirect = urllib.parse.urlsplit(authorization["redirect_uri"])
    assert (redirect.hostname, redirect.path) == ("127.0.0.1", "/callback")
    assert redirect.port is not None
    assert server.registrations == []
    assert server.token_forms[0]["client_id"] == f"{_BASE}/client.json"
    assert server.token_forms[0]["redirect_uri"] == authorization["redirect_uri"]
    # The document is not stored, so signing in again refreshes the tokens
    # instead of discarding them.
    assert "clientInformation" not in fake.stored()
    again = fake.sign_in(_CIMD)
    assert again.opened == []
    assert len(server.authorizations) == 1
    assert server.token_forms[1]["grant_type"] == "refresh_token"
    assert server.token_forms[1]["client_id"] == f"{_BASE}/client.json"


def test_uses_a_document_and_callback_path_of_the_server_without_the_iss_parameter(
    start_server: _StartServer,
) -> None:
    # Pi: "uses a document and callback path specific to the MCP server
    # without the iss parameter". OAU-34.
    fake = start_server(cimd=True)
    server = fake.server

    fake.sign_in(_CIMD)

    identifier = _id(server.url)
    (authorization,) = server.authorizations
    assert authorization["client_id"] == f"{_BASE}/{identifier}/client.json"
    redirect = urllib.parse.urlsplit(authorization["redirect_uri"])
    assert redirect.path == f"/callback/{identifier}"
    assert server.registrations == []
    assert server.token_forms[0]["redirect_uri"] == authorization["redirect_uri"]
    assert fake.stored()["tokens"]["access_token"].startswith("access-")


def test_a_configured_callback_port_is_the_port_of_the_server_specific_redirect_uri(
    start_server: _StartServer,
) -> None:
    # OAU-31 with OAU-34: the fixed redirect URI gets the server's path.
    fake = start_server(cimd=True)
    port = _free_port()
    settings = McpOAuthSettings(
        client_registration="cimd",
        client_metadata_base_url=_BASE,
        callback_url="http://localhost/callback",
        callback_port=port,
    )

    browser = fake.sign_in(settings)

    assert browser.query()["redirect_uri"] == (
        f"http://localhost:{port}/callback/{_id(fake.server.url)}"
    )
    assert "tokens" in fake.stored()


def test_replaces_a_registered_client_when_switching_to_the_document(
    start_server: _StartServer,
) -> None:
    # Pi: "replaces a registered client when switching to the document".
    # OAU-34.
    fake = start_server(cimd=True, iss_parameter=True)
    fake.sign_in(McpOAuthSettings())
    assert fake.stored()["clientInformation"]["client_id"] == "client-1"

    fake.sign_in(_CIMD)

    # The registered client's tokens are not refreshed with another client.
    assert [request["client_id"] for request in fake.server.authorizations] == [
        "client-1",
        f"{_BASE}/client.json",
    ]
    assert [form["grant_type"] for form in fake.server.token_forms] == [
        "authorization_code",
        "authorization_code",
    ]
    assert "clientInformation" not in fake.stored()


def test_accepts_the_authorization_response_only_on_the_server_specific_redirect_uri(
    start_server: _StartServer,
) -> None:
    # Pi: "accepts the authorization response only on the server-specific
    # redirect URI". OAU-34.
    # A mixed-up authorization server redirects to the shared callback path.
    mixed_up = start_server(cimd=True)
    mixed_up.server.redirect_path = "/callback"
    with pytest.raises(OAuthFlowError, match="arrived on another redirect URI"):
        mixed_up.sign_in(_CIMD)
    assert mixed_up.server.token_forms == []

    # The same for a redirect URL pasted from the browser.
    def paste(authorization_url: str) -> str:
        query = dict(
            urllib.parse.parse_qsl(urllib.parse.urlsplit(authorization_url).query)
        )
        redirect = urllib.parse.urlsplit(query["redirect_uri"])._replace(
            path="/callback", query=f"code=code-1&state={query['state']}"
        )
        return redirect.geturl()

    by_paste = start_server(cimd=True)
    with pytest.raises(
        OAuthFlowError, match="does not match this sign-in's redirect URI"
    ):
        by_paste.sign_in(_CIMD, _Browser(paste))
    assert by_paste.server.token_forms == []


def test_fails_instead_of_registering_when_the_server_does_not_support_documents(
    start_server: _StartServer,
) -> None:
    # Pi: "fails instead of registering when the server does not support
    # documents". OAU-33.
    fake = start_server(iss_parameter=True)

    with pytest.raises(OAuthFlowError, match=_UNSUPPORTED):
        fake.sign_in(_CIMD)

    assert fake.server.registrations == []
    assert fake.server.authorizations == []


# -- the metadata URL of the authorization server ---------------------------------


def test_uses_the_configured_authorization_server_metadata_url(
    start_server: _StartServer,
) -> None:
    # Pi: "uses the configured authorization server metadata URL". OAU-35.
    fake = start_server()
    settings = McpOAuthSettings(
        auth_server_metadata_url=f"{fake.server.origin}/missing"
    )

    with pytest.raises(
        Exception, match="HTTP 404 loading authorization server metadata"
    ):
        fake.sign_in(settings)


def test_the_configured_metadata_is_used_as_written_and_never_cached(
    start_server: _StartServer,
) -> None:
    # OAU-35: the document's issuer is not the one discovery expects, and a
    # changed URL applies at once.
    fake = start_server()
    server = fake.server
    for name in ("one", "two"):
        server.json(
            f"/{name}/metadata",
            {
                **server.metadata,
                "issuer": f"https://{name}.example",
                "token_endpoint": f"{server.origin}/{name}/token",
            },
        )
        server.route(f"/{name}/token", server._token)

    def sign_in(name: str) -> None:
        fake.sign_in(
            McpOAuthSettings(
                auth_server_metadata_url=f"{server.origin}/{name}/metadata"
            )
        )

    sign_in("one")
    assert "discovery" not in fake.stored()
    state = fake.stored()
    del state["tokens"]
    fake.store.save(state)
    sign_in("two")

    assert len(server.requests_to("/one/token")) == 1
    assert len(server.requests_to("/two/token")) == 1
    assert "/.well-known/oauth-authorization-server" not in server.paths
    assert "discovery" not in fake.stored()


# -- the options of an entry, at the manager --------------------------------------


class _Session:
    """A manager over one entry ``issues`` for the fake server."""

    def __init__(self, fake: _Fake, home: Path, oauth: dict[str, Any]) -> None:
        self.server = fake.server
        entry = McpServerEntry(
            name="issues",
            config={"url": fake.server.url, "exposure": "direct", "oauth": oauth},  # type: ignore[arg-type]
            source="test",
        )
        self.store = McpOAuthCredentialStore(mcp_auth_path(home)).for_server(
            "issues", fake.server.url
        )
        self.manager = McpManager(
            config=LoadedMcpConfig(servers=[entry], errors=[]),
            cwd=home,
            client_version="1.2.3",
            publish_tools=lambda registry: (),
            credentials=McpOAuthCredentialStore(mcp_auth_path(home)),
        )
        self.manager.start()
        assert self.manager.wait_until_settled(_WAIT_SECONDS)

    def login(self) -> _Browser:
        browser = _Browser()
        assert self.manager.sign_in("issues", browser) is None
        return browser

    def token(self) -> str:
        state = self.store.load()
        assert state is not None
        token: str = state["tokens"]["access_token"]
        return token

    def whoami(self) -> str:
        connection = self.manager._caller("issues", "whoami")
        result: Any = connection.call_tool("whoami", {})
        text: str = result["content"][0]["text"]
        return text


_StartSession = Callable[..., _Session]


@pytest.fixture
def session(start_server: _StartServer, tmp_path: Path) -> Iterator[_StartSession]:
    sessions: list[_Session] = []

    def start(oauth: dict[str, Any], **server_options: bool) -> _Session:
        home = tmp_path / f"session-{len(sessions)}"
        home.mkdir()
        sessions.append(_Session(start_server(**server_options), home, oauth))
        return sessions[-1]

    yield start
    for started in sessions:
        started.manager.close()


def test_uses_the_configured_callback_url_and_scope(session: _StartSession) -> None:
    # Pi: "uses the configured callback URL and scope". OAU-31.
    callback_url = f"http://localhost:{_free_port()}/callback"
    started = session({"callbackUrl": callback_url, "scope": "issues:read"})

    browser = started.login()

    assert browser.query()["redirect_uri"] == callback_url
    assert browser.query()["scope"] == "issues:read"
    assert started.server.registrations[0]["redirect_uris"] == [callback_url]
    assert started.whoami() == f"token {started.token()}"


def test_registers_with_the_configured_client_name(session: _StartSession) -> None:
    # Pi: "registers with the configured client name". OAU-32.
    named = session({"clientName": "Claude Code"})
    named.login()
    assert [r["client_name"] for r in named.server.registrations] == ["Claude Code"]

    fallback = session({})
    fallback.login()
    (registration,) = fallback.server.registrations
    assert registration["client_name"] == "pipy"
    # OpenID Connect servers reject the loopback redirect URI of a `web`
    # client.
    assert registration["application_type"] == "native"


def test_a_changed_client_name_applies_only_to_a_new_registration(
    start_server: _StartServer,
) -> None:
    # OAU-32: the name is sent when a client is registered, so a stored
    # client keeps the name it was registered with.
    fake = start_server()
    fake.sign_in(McpOAuthSettings(client_name="First"))
    state = fake.stored()
    del state["tokens"]
    fake.store.save(state)

    fake.sign_in(McpOAuthSettings(client_name="Second"))

    assert [r["client_name"] for r in fake.server.registrations] == ["First"]


def test_adds_the_listening_port_to_a_callback_url_without_one(
    session: _StartSession,
) -> None:
    # Pi: "adds the listening port to a callback URL without one". OAU-31.
    free = session({"callbackUrl": "http://127.0.0.1/oauth/done"}).login()
    redirect = urllib.parse.urlsplit(free.query()["redirect_uri"])
    assert redirect.port is not None
    assert free.query()["redirect_uri"] == (
        f"http://127.0.0.1:{redirect.port}/oauth/done"
    )

    port = _free_port()
    fixed = session(
        {"callbackUrl": "http://127.0.0.1/oauth/done", "callbackPort": port}
    ).login()
    assert fixed.query()["redirect_uri"] == f"http://127.0.0.1:{port}/oauth/done"


def test_a_callback_port_alone_fixes_the_default_redirect_uri(
    session: _StartSession,
) -> None:
    # OAU-31.
    port = _free_port()

    browser = session({"callbackPort": port}).login()

    assert browser.query()["redirect_uri"] == f"http://127.0.0.1:{port}/callback"


def test_a_configured_client_is_used_without_a_registration(
    session: _StartSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # OAU-30: the secret is resolved for the token requests and sent with
    # them.
    monkeypatch.setenv("PIPY_TEST_OAUTH_SECRET", "s3cret")
    started = session(
        {"clientId": "configured", "clientSecret": "${PIPY_TEST_OAUTH_SECRET}"}
    )
    server = started.server
    server.add_client("configured")
    server.metadata["token_endpoint_auth_methods_supported"] = ["client_secret_post"]
    server.json("/.well-known/oauth-authorization-server", server.metadata)

    browser = started.login()

    assert browser.query()["client_id"] == "configured"
    assert server.registrations == []
    assert "clientInformation" not in (started.store.load() or {})
    assert server.token_forms[0]["client_id"] == "configured"
    assert server.token_forms[0]["client_secret"] == "s3cret"

    # The refresh of a connection names the same client.
    first = started.token()
    server.expire_access_tokens()
    assert started.whoami() == f"token {started.token()}"
    assert started.token() != first
    assert server.token_forms[1]["grant_type"] == "refresh_token"
    assert server.token_forms[1]["client_secret"] == "s3cret"


def test_a_connection_refreshes_with_the_document_it_signed_in_with(
    session: _StartSession,
) -> None:
    # OAU-33: a document client is not stored, so the refresh of a
    # connection has to choose the document again.
    started = session(
        {"clientRegistration": "cimd", "clientMetadataBaseUrl": f"{_BASE}/"},
        cimd=True,
    )
    server = started.server
    client_id = f"{_BASE}/{_id(server.url)}/client.json"

    started.login()
    first = started.token()
    assert started.whoami() == f"token {first}"
    server.expire_access_tokens()

    assert started.whoami() == f"token {started.token()}"
    assert started.token() != first
    assert server.registrations == []
    assert [form["grant_type"] for form in server.token_forms] == [
        "authorization_code",
        "refresh_token",
    ]
    assert [form["client_id"] for form in server.token_forms] == [client_id] * 2
