"""OpenAI Sign in with ChatGPT (Pi ``02eed88fd``).

Covers the OAuth flow against a local stub token server and a real local
callback listener, the persisted per-request refresh, the auth-file merge, the
Responses request fields a ChatGPT token omits, the usage-limit error text,
the retry codes, the global ``deviceId`` setting, and ``/login``/``/logout``
for ``openai``. Nothing touches the network or the real auth store.
"""

from __future__ import annotations

import io
import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Mapping
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest
from pipy_harness.native.agent.provider_retry import (
    is_managed_retry_eligible,
    is_retryable_error_text,
)
from pipy_harness.native.auth_store import AuthStore, modify_stored_credential
from pipy_harness.native.catalog_state import ProviderCatalogState
from pipy_harness.native.http import JsonResponse
from pipy_harness.native.oauth_providers import (
    OAuthCredentialCache,
    OAuthError,
    OpenAIChatGPTOAuthProvider,
    _default_transport,
    chatgpt_agent_host_id,
)
from pipy_harness.native.provider_construction import (
    ConstructionOptions,
    PerRequestOAuthProvider,
)
from pipy_harness.native.providers.openai_responses import OpenAIResponsesProvider
from pipy_harness.native.repl_state import (
    ModelRuntime,
    NativeModelSelection,
    NativeReplProviderState,
)
from pipy_harness.native.settings import SCOPE_GLOBAL, SettingsManager

DEVICE_ID = "E61BBE28-07EF-466D-8E5D-A344F94AB305"
REQUIRED_SCOPE = (
    "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
)
REDIRECT = "http://127.0.0.1:1455/auth/callback"
NOW_MS = 1_800_000_000_000
USAGE_LINK = "Check your ChatGPT usage: https://chatgpt.com/settings/usage"


# ---- stub token server ---------------------------------------------------------


class _TokenServer:
    """Local stub of ``auth.openai.com``'s token endpoint."""

    def __init__(self, replies: list[tuple[int, dict[str, Any]]]) -> None:
        self.replies = replies
        self.forms: list[dict[str, str]] = []
        self.headers: list[dict[str, str]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode("utf-8")
                owner.forms.append(dict(urllib.parse.parse_qsl(raw)))
                owner.headers.append({k.lower(): v for k, v in self.headers.items()})
                status, body = (
                    owner.replies.pop(0) if len(owner.replies) > 1 else owner.replies[0]
                )
                payload = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return (
            f"http://127.0.0.1:{self.server.server_address[1]}/api/accounts/oauth/token"
        )

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def token_server() -> Iterator[_TokenServer]:
    server = _TokenServer([(200, _token_response())])
    yield server
    server.close()


def _token_response(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "access_token": "chatgpt-access",
        "refresh_token": "chatgpt-refresh",
        "expires_in": 3600,
        "id_token": "id-token",
        "scope": REQUIRED_SCOPE,
    }
    body.update(overrides)
    return {key: value for key, value in body.items() if value is not None}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _provider(
    server: _TokenServer, *, port: int | None = None
) -> OpenAIChatGPTOAuthProvider:
    return OpenAIChatGPTOAuthProvider(
        transport=_default_transport,
        now_ms=lambda: NOW_MS,
        token_url=server.url,
        callback_host="127.0.0.1",
        callback_port=port if port is not None else _free_port(),
    )


class _Interaction:
    """Records notify events; answers the manual prompt via ``answer``."""

    def __init__(self, answer: Any) -> None:
        self.events: list[dict[str, Any]] = []
        self.prompts: list[dict[str, Any]] = []
        self._answer = answer

    def notify(self, event: Mapping[str, object]) -> None:
        self.events.append(dict(event))

    def prompt(self, prompt: Mapping[str, object]) -> str:
        self.prompts.append(dict(prompt))
        return str(self._answer(self))

    def authorize_params(self) -> dict[str, str]:
        url = next(e["url"] for e in self.events if e["type"] == "auth_url")
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def _callback(interaction: _Interaction, **params: str) -> str:
    state = interaction.authorize_params()["state"]
    query = {"code": "authorization-code", "state": state, **params}
    return f"{REDIRECT}?{urllib.parse.urlencode(query)}"


# ---- OAuth flow -------------------------------------------------------------------


def test_authorize_url_matches_pi_parameters(token_server: _TokenServer) -> None:
    interaction = _Interaction(lambda i: _callback(i, client_id="oaiapp_issued"))
    _provider(token_server).login(
        prompt=interaction.prompt,
        notify=interaction.notify,
        get_device_id=lambda: DEVICE_ID,
    )
    url = next(e for e in interaction.events if e["type"] == "auth_url")
    assert url["url"].startswith("https://auth.openai.com/api/accounts/authorize?")
    assert url["instructions"] == (
        "Complete sign-in in your browser. If the callback does not complete, "
        "paste the final redirect URL here."
    )
    params = urllib.parse.parse_qsl(urllib.parse.urlsplit(url["url"]).query)
    assert [key for key, _ in params] == [
        "client_id",
        "agent_name_hint",
        "ext_agent_host_id",
        "response_type",
        "redirect_uri",
        "resource",
        "scope",
        "state",
        "code_challenge",
        "code_challenge_method",
        "nonce",
    ]
    values = dict(params)
    assert values["client_id"] == "dynamic_agent_client"
    assert values["agent_name_hint"] == "pipy"
    assert values["ext_agent_host_id"] == f"urn:uuid:{DEVICE_ID.lower()}"
    assert values["response_type"] == "code"
    assert values["redirect_uri"] == REDIRECT
    assert values["resource"] == "https://api.openai.com/v1"
    assert values["scope"] == REQUIRED_SCOPE
    assert values["code_challenge_method"] == "S256"
    assert values["state"] != values["nonce"]
    assert interaction.prompts == [
        {
            "type": "manual_code",
            "message": "Complete login in your browser, or paste the final redirect URL here:",
            "placeholder": REDIRECT,
        }
    ]


@pytest.mark.parametrize("device_id", [None, "", "not-a-uuid", 42])
def test_login_requires_a_uuid_device_id(device_id: object) -> None:
    provider = OpenAIChatGPTOAuthProvider(transport=lambda *a, **k: (500, ""))
    with pytest.raises(OAuthError, match="requires a device ID"):
        provider.login(
            prompt=lambda _p: "",
            notify=lambda _e: None,
            get_device_id=lambda: device_id,
        )
    with pytest.raises(OAuthError, match="requires a device ID"):
        chatgpt_agent_host_id(device_id)


def test_manual_login_exchanges_code_and_stores_issued_client(
    token_server: _TokenServer,
) -> None:
    interaction = _Interaction(lambda i: _callback(i, client_id=" oaiapp_issued "))
    credential = _provider(token_server).login(
        prompt=interaction.prompt,
        notify=interaction.notify,
        get_device_id=lambda: DEVICE_ID,
    )
    assert credential == {
        "type": "oauth",
        "access": "chatgpt-access",
        "refresh": "chatgpt-refresh",
        "expires": NOW_MS + 3600 * 1000 - 3 * 60 * 1000,
        "clientId": "oaiapp_issued",
        "scopes": REQUIRED_SCOPE.split(),
    }
    form = token_server.forms[0]
    assert set(form) == {
        "grant_type",
        "client_id",
        "code",
        "code_verifier",
        "redirect_uri",
        "resource",
    }
    assert form["grant_type"] == "authorization_code"
    assert form["client_id"] == "oaiapp_issued"
    assert form["code"] == "authorization-code"
    assert form["redirect_uri"] == REDIRECT
    assert form["resource"] == "https://api.openai.com/v1"
    assert token_server.headers[0]["accept"] == "application/json"
    assert (
        token_server.headers[0]["content-type"] == "application/x-www-form-urlencoded"
    )
    assert {
        "type": "progress",
        "message": "Exchanging authorization code for tokens...",
    } in (interaction.events)


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ("authorization-code", "Paste the full callback URL from the browser"),
        (
            "http://localhost:1455/auth/callback?code=c&state=s",
            f"The pasted callback URL must start with {REDIRECT}",
        ),
        (
            "http://127.0.0.1:1455/other?code=c&state=s",
            f"The pasted callback URL must start with {REDIRECT}",
        ),
        (
            f"{REDIRECT}?error=access_denied",
            "ChatGPT authorization failed: access_denied",
        ),
        (f"{REDIRECT}?state=s", "Missing authorization code"),
        (f"{REDIRECT}?code=c", "Missing OAuth state"),
        (f"{REDIRECT}?code=c&state=wrong&client_id=x", "OAuth state mismatch"),
    ],
)
def test_manual_input_validation(answer: str, message: str) -> None:
    transport_calls: list[str] = []

    def transport(*_args: object, **_kwargs: object) -> tuple[int, str]:
        transport_calls.append("x")
        return 500, ""

    provider = OpenAIChatGPTOAuthProvider(
        transport=transport, callback_port=_free_port()
    )
    # A non-empty answer is always parsed; an empty one waits for the browser
    # callback (covered by the busy-port test for the no-listener case).
    with pytest.raises(OAuthError) as excinfo:
        provider.login(
            prompt=lambda _p: answer,
            notify=lambda _e: None,
            get_device_id=lambda: DEVICE_ID,
        )
    assert str(excinfo.value) == message
    assert transport_calls == []


def test_manual_callback_without_client_id_is_rejected(
    token_server: _TokenServer,
) -> None:
    interaction = _Interaction(lambda i: _callback(i))
    with pytest.raises(OAuthError) as excinfo:
        _provider(token_server).login(
            prompt=interaction.prompt,
            notify=interaction.notify,
            get_device_id=lambda: DEVICE_ID,
        )
    assert str(excinfo.value) == (
        "OpenAI OAuth registration callback did not contain an issued client ID"
    )
    assert token_server.forms == []


def _get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def test_browser_callback_completes_login_after_invalid_ones(
    token_server: _TokenServer,
) -> None:
    port = _free_port()
    seen: list[tuple[int, str]] = []

    def browser(interaction: _Interaction) -> str:
        state = interaction.authorize_params()["state"]
        base = f"http://127.0.0.1:{port}"
        seen.append(_get(f"{base}/elsewhere"))
        # An invalid callback answers 400 and the listener keeps waiting.
        seen.append(_get(f"{base}/auth/callback?code=c&state=wrong&client_id=x"))
        seen.append(
            _get(
                f"{base}/auth/callback?"
                + urllib.parse.urlencode(
                    {
                        "code": "authorization-code",
                        "state": state,
                        "client_id": "oaiapp_cb",
                    }
                )
            )
        )
        return ""  # Enter: take the browser callback

    interaction = _Interaction(browser)
    credential = _provider(token_server, port=port).login(
        prompt=interaction.prompt,
        notify=interaction.notify,
        get_device_id=lambda: DEVICE_ID,
    )
    assert [status for status, _ in seen] == [404, 400, 200]
    assert "Callback route not found." in seen[0][1]
    assert "OAuth state mismatch" in seen[1][1]
    assert "ChatGPT authentication completed" in seen[2][1]
    assert credential["clientId"] == "oaiapp_cb"
    assert token_server.forms[0]["client_id"] == "oaiapp_cb"
    # The listener is closed after the login.
    with pytest.raises(OSError):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/auth/callback", timeout=2)  # noqa: S310


def test_browser_error_callback_fails_the_login(token_server: _TokenServer) -> None:
    port = _free_port()

    def browser(_interaction: _Interaction) -> str:
        status, _body = _get(
            f"http://127.0.0.1:{port}/auth/callback?error=access_denied"
        )
        assert status == 400
        return ""

    with pytest.raises(OAuthError) as excinfo:
        _provider(token_server, port=port).login(
            prompt=_Interaction(browser).prompt,
            notify=lambda _e: None,
            get_device_id=lambda: DEVICE_ID,
        )
    assert str(excinfo.value) == "ChatGPT authorization failed: access_denied"
    assert token_server.forms == []


def test_idle_browser_preconnection_does_not_block_the_callback(
    token_server: _TokenServer,
) -> None:
    port = _free_port()
    idle: list[socket.socket] = []

    def browser(interaction: _Interaction) -> str:
        # A browser opens a spare connection ahead of time and sends nothing.
        spare = socket.create_connection(("127.0.0.1", port), timeout=5)
        idle.append(spare)
        status, _body = _get(
            _callback(interaction, client_id="oaiapp").replace(
                "127.0.0.1:1455", f"127.0.0.1:{port}"
            )
        )
        assert status == 200
        return ""

    interaction = _Interaction(browser)
    result: dict[str, object] = {}

    def run() -> None:
        result["credential"] = _provider(token_server, port=port).login(
            prompt=interaction.prompt,
            notify=interaction.notify,
            get_device_id=lambda: DEVICE_ID,
        )

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=20)
    assert not worker.is_alive(), "login hung behind an idle connection"
    assert result["credential"]["clientId"] == "oaiapp"  # type: ignore[index]
    # close() dropped the idle pre-connection (Pi closeAllConnections).
    idle[0].settimeout(5)
    assert idle[0].recv(1) == b""
    idle[0].close()


@pytest.mark.parametrize("via_browser", [True, False])
def test_callback_error_text_is_redacted_when_secret_looking(
    token_server: _TokenServer, via_browser: bool
) -> None:
    port = _free_port()
    error = urllib.parse.quote("access_token=FIXTURE_SECRET")

    def answer(_interaction: _Interaction) -> str:
        if via_browser:
            _get(f"http://127.0.0.1:{port}/auth/callback?error={error}")
            return ""
        return f"{REDIRECT}?error={error}"

    with pytest.raises(OAuthError) as excinfo:
        _provider(token_server, port=port).login(
            prompt=_Interaction(answer).prompt,
            notify=lambda _e: None,
            get_device_id=lambda: DEVICE_ID,
        )
    assert str(excinfo.value) == "ChatGPT authorization failed: [REDACTED]"


def test_busy_callback_port_falls_back_to_manual_input(
    token_server: _TokenServer,
) -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = int(busy.getsockname()[1])
        interaction = _Interaction(lambda i: _callback(i, client_id="oaiapp_manual"))
        credential = _provider(token_server, port=port).login(
            prompt=interaction.prompt,
            notify=interaction.notify,
            get_device_id=lambda: DEVICE_ID,
        )
        empty = _Interaction(lambda _i: "")
        with pytest.raises(OAuthError, match="Paste the full callback URL"):
            _provider(token_server, port=port).login(
                prompt=empty.prompt,
                notify=empty.notify,
                get_device_id=lambda: DEVICE_ID,
            )
    info = interaction.events[0]
    assert info["type"] == "info"
    assert info["message"].startswith(
        f"Could not listen on {REDIRECT}; paste the final redirect URL to continue."
    )
    assert credential["clientId"] == "oaiapp_manual"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"id_token": None}, "did not contain an ID token"),
        ({"id_token": "  "}, "did not contain an ID token"),
        ({"access_token": ""}, "invalid access_token"),
        ({"refresh_token": None}, "invalid refresh_token"),
        ({"scope": " "}, "invalid scope"),
        ({"expires_in": 0}, "invalid expires_in"),
        ({"expires_in": True}, "invalid expires_in"),
        ({"expires_in": "3600"}, "invalid expires_in"),
        (
            {"scope": "openid profile"},
            "OpenAI OAuth grant did not include chatgpt.tokens.use.direct",
        ),
    ],
)
def test_token_response_validation(overrides: dict[str, Any], message: str) -> None:
    server = _TokenServer([(200, _token_response(**overrides))])
    try:
        interaction = _Interaction(lambda i: _callback(i, client_id="oaiapp"))
        with pytest.raises(OAuthError, match=message):
            _provider(server).login(
                prompt=interaction.prompt,
                notify=interaction.notify,
                get_device_id=lambda: DEVICE_ID,
            )
    finally:
        server.close()


def test_token_request_failure_names_status_and_body() -> None:
    server = _TokenServer([(400, {"error": "invalid_grant"})])
    try:
        with pytest.raises(OAuthError) as excinfo:
            _provider(server).refresh_token(
                {"refresh": "r", "clientId": "oaiapp", "type": "oauth"}
            )
    finally:
        server.close()
    assert str(excinfo.value) == (
        'OpenAI OAuth token request failed (400): {"error": "invalid_grant"}'
    )


def test_refresh_sends_issued_client_and_rotates(token_server: _TokenServer) -> None:
    token_server.replies[:] = [
        (200, _token_response(id_token=None, refresh_token="r2"))
    ]
    refreshed = _provider(token_server).refresh_token(
        {"type": "oauth", "refresh": "r1", "clientId": "oaiapp", "access": "old"}
    )
    assert token_server.forms[0] == {
        "grant_type": "refresh_token",
        "client_id": "oaiapp",
        "refresh_token": "r1",
        "resource": "https://api.openai.com/v1",
    }
    assert refreshed["refresh"] == "r2"
    assert refreshed["clientId"] == "oaiapp"


@pytest.mark.parametrize("client_id", [None, "", "  "])
def test_refresh_requires_the_issued_client_id(client_id: object) -> None:
    provider = OpenAIChatGPTOAuthProvider(transport=lambda *a, **k: (500, ""))
    credential: dict[str, object] = {"type": "oauth", "refresh": "r"}
    if client_id is not None:
        credential["clientId"] = client_id
    with pytest.raises(OAuthError) as excinfo:
        provider.refresh_token(credential)
    assert str(excinfo.value) == (
        "Stored OpenAI OAuth credential does not contain an issued client ID; "
        "reconnect ChatGPT"
    )


# ---- auth file and persisted refresh ----------------------------------------------


def _stored(**overrides: object) -> dict[str, object]:
    credential: dict[str, object] = {
        "type": "oauth",
        "access": "chatgpt-access",
        "refresh": "chatgpt-refresh",
        "expires": NOW_MS + 60 * 60 * 1000,
        "clientId": "oaiapp",
        "scopes": REQUIRED_SCOPE.split(),
    }
    credential.update(overrides)
    return credential


def test_modify_stored_credential_writes_only_its_key(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"anthropic": {"type": "api_key", "key": "k"}}))
    result = modify_stored_credential(path, "openai", lambda current: _stored())
    assert result == _stored()
    assert json.loads(path.read_text()) == {
        "anthropic": {"type": "api_key", "key": "k"},
        "openai": _stored(),
    }
    unchanged = modify_stored_credential(path, "openai", lambda current: None)
    assert unchanged == _stored()


def test_auth_store_writes_keep_entries_changed_on_disk(tmp_path: Path) -> None:
    store = AuthStore(path=tmp_path / "auth.json")
    store.set("openai", _stored(refresh="old"))
    # Another writer (a worker-thread refresh, another process) rotates it.
    modify_stored_credential(store.path, "openai", lambda _c: _stored(refresh="new"))
    store.set("anthropic", {"type": "api_key", "key": "k"})
    assert json.loads(store.path.read_text())["openai"]["refresh"] == "new"
    assert store.get("openai") == _stored(refresh="new")
    store.remove("anthropic")
    assert json.loads(store.path.read_text()) == {"openai": _stored(refresh="new")}


class _CountingRefresh:
    id = "openai"

    def __init__(self, refresh: str = "rotated") -> None:
        self.calls: list[Mapping[str, object]] = []
        self.refresh = refresh

    def refresh_token(self, credentials: Mapping[str, object]) -> dict[str, object]:
        self.calls.append(dict(credentials))
        return _stored(access=f"access-{len(self.calls)}", refresh=self.refresh)

    def get_api_key(self, credentials: Mapping[str, object]) -> str:
        return str(credentials["access"])


def test_persisted_refresh_writes_the_rotated_credential(tmp_path: Path) -> None:
    store = AuthStore(path=tmp_path / "auth.json")
    store.set("openai", _stored(expires=0))
    fake = _CountingRefresh()
    cache = OAuthCredentialCache(
        providers=lambda _id: fake, now_ms=lambda: NOW_MS, auth_path=store.path
    )
    snapshot = store.get("openai")
    assert snapshot is not None
    fresh = cache.fresh("openai", snapshot)
    assert fresh["access"] == "access-1"
    assert json.loads(store.path.read_text())["openai"]["refresh"] == "rotated"
    # The stale owner snapshot now maps to the rotated credential.
    assert cache.fresh("openai", snapshot)["access"] == "access-1"
    assert cache.current("openai", snapshot)["refresh"] == "rotated"
    assert len(fake.calls) == 1
    assert fake.calls[0]["refresh"] == "chatgpt-refresh"


def test_persisted_refresh_reuses_a_credential_another_process_refreshed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"openai": _stored(refresh="theirs")}))
    fake = _CountingRefresh()
    cache = OAuthCredentialCache(
        providers=lambda _id: fake, now_ms=lambda: NOW_MS, auth_path=path
    )
    fresh = cache.fresh("openai", _stored(expires=0))
    assert fresh["refresh"] == "theirs"
    assert fake.calls == []


def test_persisted_refresh_fails_when_logged_out_meanwhile(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({}))
    fake = _CountingRefresh()
    cache = OAuthCredentialCache(
        providers=lambda _id: fake, now_ms=lambda: NOW_MS, auth_path=path
    )
    with pytest.raises(OAuthError, match="No OAuth credential stored for openai"):
        cache.fresh("openai", _stored(expires=0))
    assert fake.calls == []


def test_copilot_refresh_stays_in_memory(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({}))
    fake = _CountingRefresh(refresh="chatgpt-refresh")
    cache = OAuthCredentialCache(
        providers=lambda _id: fake, now_ms=lambda: NOW_MS, auth_path=path
    )
    cache.fresh("github-copilot", _stored(expires=0))
    assert json.loads(path.read_text()) == {}


# ---- ModelRuntime + request shape --------------------------------------------------


class _CapturingHTTP:
    def __init__(self, response: JsonResponse | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.response = response or JsonResponse(
            status_code=200,
            body={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "ok"}],
                    }
                ],
            },
        )

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
        cancel_token: object = None,
    ) -> JsonResponse:
        self.requests.append({"url": url, "headers": dict(headers), "body": dict(body)})
        return self.response


def _request(tmp_path: Path, retention: Any = "long") -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYS",
        user_prompt="hello",
        provider_name="openai",
        model_id="gpt-6.1-sol",
        cwd=tmp_path,
        session_id="session-1",
        cache_retention=retention,
    )


def _catalog_state(
    tmp_path: Path, store: AuthStore, env: dict[str, str]
) -> ProviderCatalogState:
    return ProviderCatalogState(
        models_json_path=tmp_path / "models.json",
        auth_store=store,
        env=env,
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )


def test_stored_chatgpt_login_binds_a_per_request_provider(tmp_path: Path) -> None:
    store = AuthStore(path=tmp_path / "auth.json")
    store.set("openai", _stored(expires=0))
    state = _catalog_state(tmp_path, store, {"OPENAI_API_KEY": "sk-env"})
    fake = _CountingRefresh()
    state.oauth_credentials = OAuthCredentialCache(
        providers=lambda _id: fake, now_ms=lambda: NOW_MS, auth_path=store.path
    )
    assert state.is_using_subscription("openai")

    provider = ModelRuntime(catalog=state).construct(
        NativeModelSelection("openai", "gpt-6.1-sol"),
        thinking_level="high",
        options=ConstructionOptions(),
    )
    assert isinstance(provider, PerRequestOAuthProvider)
    http = _CapturingHTTP()
    result = replace(provider, http_client=http).complete(_request(tmp_path))

    assert result.status is HarnessStatus.SUCCEEDED
    sent = http.requests[0]
    assert sent["url"] == "https://api.openai.com/v1/responses"
    assert sent["headers"]["Authorization"] == "Bearer access-1"
    assert sent["body"]["prompt_cache_key"] == "session-1"
    assert "prompt_cache_retention" not in sent["body"]
    assert "prompt_cache_options" not in sent["body"]
    assert "max_output_tokens" not in sent["body"]
    assert "temperature" not in sent["body"]
    assert sent["headers"]["session_id"] == "session-1"
    # The rotated credential is persisted for the next process.
    assert json.loads(store.path.read_text())["openai"]["refresh"] == "rotated"


def test_api_key_credentials_keep_the_cache_fields(tmp_path: Path) -> None:
    state = _catalog_state(
        tmp_path, AuthStore(path=tmp_path / "auth.json"), {"OPENAI_API_KEY": "sk-env"}
    )
    provider = ModelRuntime(catalog=state).construct(
        NativeModelSelection("openai", "gpt-6.1-sol"),
        thinking_level="high",
        options=ConstructionOptions(),
    )
    assert not isinstance(provider, PerRequestOAuthProvider)
    assert isinstance(provider, OpenAIResponsesProvider)
    http = _CapturingHTTP()
    replace(provider, http_client=http).complete(_request(tmp_path))
    assert http.requests[0]["body"]["prompt_cache_options"] == {"ttl": "30m"}


def _adapter(**fields: Any) -> tuple[OpenAIResponsesProvider, _CapturingHTTP]:
    http = _CapturingHTTP(fields.pop("response", None))
    values: dict[str, Any] = {
        "model_id": "gpt-5.5",
        "api_key": "chatgpt-access",
        "base_url": "https://api.openai.com/v1",
        "http_client": http,
    }
    values.update(fields)
    return OpenAIResponsesProvider(**values), http


@pytest.mark.parametrize(
    ("fields", "omitted"),
    [
        ({}, True),
        ({"api_key": "sk-proj-abc"}, False),
        ({"api_key": None, "extra_headers": {"Authorization": "Bearer x"}}, False),
        ({"base_url": "https://api.openai.com/v1/"}, False),
        ({"base_url": "https://proxy.example/v1"}, False),
        ({"base_url": None}, False),
        ({"provider_name": "openrouter"}, False),
    ],
)
def test_chatgpt_sign_in_predicate(
    tmp_path: Path, fields: dict[str, Any], omitted: bool
) -> None:
    provider, http = _adapter(**fields)
    provider.complete(_request(tmp_path, "long"))
    body = http.requests[0]["body"]
    assert ("prompt_cache_retention" not in body) is omitted
    explicit, explicit_http = _adapter(
        supports_explicit_prompt_cache_mode=True, **fields
    )
    explicit.complete(_request(tmp_path, "none"))
    assert ("prompt_cache_options" not in explicit_http.requests[0]["body"]) is omitted


def test_usage_limit_http_error_links_to_chatgpt_usage(tmp_path: Path) -> None:
    provider, _http = _adapter(
        response=JsonResponse(
            status_code=429,
            body={
                "error": {
                    "code": "subscription_sharing_usage_limit_exceeded",
                    "message": "Usage limit reached.",
                    "type": "rate_limit_error",
                }
            },
        )
    )
    result = provider.complete(_request(tmp_path))
    assert result.status is HarnessStatus.FAILED
    assert result.error_message == (
        "OpenAI API request failed with HTTP status 429. "
        "subscription_sharing_usage_limit_exceeded: Usage limit reached. "
        f"{USAGE_LINK}"
    )
    assert "api_error_message" not in (result.metadata or {})
    assert not is_managed_retry_eligible(result)


def test_usage_limit_failed_response_links_to_chatgpt_usage(tmp_path: Path) -> None:
    provider, _http = _adapter(
        response=JsonResponse(
            status_code=200,
            body={
                "status": "failed",
                "error": {
                    "code": "subscription_sharing_usage_limit_exceeded",
                    "message": "Usage limit reached.",
                },
            },
        )
    )
    result = provider.complete(_request(tmp_path))
    assert result.error_message == (
        "OpenAI response status was failed. "
        "subscription_sharing_usage_limit_exceeded: Usage limit reached. "
        f"{USAGE_LINK}"
    )
    assert (result.metadata or {})[
        "api_error_code"
    ] == "subscription_sharing_usage_limit_exceeded"
    assert not is_managed_retry_eligible(result)


def test_other_errors_keep_their_message_and_prose_is_not_lifted(
    tmp_path: Path,
) -> None:
    provider, _http = _adapter(
        response=JsonResponse(
            status_code=400,
            body={"error": {"code": "bad_request", "message": "SECRET_PROMPT_ECHO"}},
        )
    )
    result = provider.complete(_request(tmp_path))
    assert result.error_message == "OpenAI API request failed with HTTP status 400."
    assert "SECRET_PROMPT_ECHO" not in json.dumps(result.metadata, default=str)


@pytest.mark.parametrize(
    "code",
    ["subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable"],
)
def test_temporary_subscription_errors_are_retried(tmp_path: Path, code: str) -> None:
    assert is_retryable_error_text(f"{code}: Usage cannot be checked.")
    provider, _http = _adapter(
        response=JsonResponse(
            status_code=200, body={"status": "failed", "error": {"code": code}}
        )
    )
    assert is_managed_retry_eligible(provider.complete(_request(tmp_path)))


def test_usage_limit_text_is_not_retryable() -> None:
    assert not is_retryable_error_text(
        'OpenAI API error (429): {"code":"subscription_sharing_usage_limit_exceeded"}'
    )


# ---- deviceId ---------------------------------------------------------------------


def test_device_id_is_created_once_globally_and_ignores_project(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.json").write_text(json.dumps({"theme": "dark"}))
    workspace = tmp_path / "ws"
    (workspace / ".pipy").mkdir(parents=True)
    (workspace / ".pipy" / "settings.json").write_text(
        json.dumps({"deviceId": "project-device"})
    )

    def manager() -> SettingsManager:
        return SettingsManager(
            global_path=config / "settings.json",
            project_path=workspace / ".pipy" / "settings.json",
            env={},
        )

    first = manager()
    device_id = first.get_or_create_device_id()
    assert isinstance(device_id, str)
    chatgpt_agent_host_id(device_id)  # a valid UUID
    assert device_id != "project-device"
    assert first.get_or_create_device_id() == device_id
    second = manager()
    assert second.get_or_create_device_id() == device_id
    assert json.loads((config / "settings.json").read_text()) == {
        "theme": "dark",
        "deviceId": device_id,
    }
    assert second.raw_scope(SCOPE_GLOBAL)["deviceId"] == device_id


# ---- /login and /logout ----------------------------------------------------------


class _StubChatGPTLogin:
    device_ids: list[object] = []

    def login(self, *, prompt, notify, get_device_id):
        _StubChatGPTLogin.device_ids.append(get_device_id())
        notify(
            {
                "type": "auth_url",
                "url": "https://auth.openai.com/api/accounts/authorize?x=1",
                "instructions": "Complete sign-in in your browser.",
            }
        )
        assert prompt({"type": "manual_code", "message": "Paste:"}) == ""
        notify(
            {
                "type": "progress",
                "message": "Exchanging authorization code for tokens...",
            }
        )
        return _stored()


def test_repl_login_and_logout_openai(tmp_path: Path) -> None:
    store = AuthStore(path=tmp_path / "auth.json")
    state = _catalog_state(tmp_path, store, {})
    opened: list[str] = []
    _StubChatGPTLogin.device_ids = []
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        model_runtime=ModelRuntime(catalog=state),
        persist_defaults=False,
        chatgpt_oauth_factory=_StubChatGPTLogin,  # type: ignore[arg-type]
        device_id_provider=lambda: DEVICE_ID,
        browser_opener=opened.append,
    )
    assert not state.provider_available("openai")

    out = io.StringIO()
    ok, message = repl_state.login(
        "openai", input_stream=io.StringIO("\n"), output_stream=out
    )
    assert ok, message
    assert message == "pipy: openai OAuth login stored."
    assert opened == ["https://auth.openai.com/api/accounts/authorize?x=1"]
    assert _StubChatGPTLogin.device_ids == [DEVICE_ID]
    shown = out.getvalue()
    assert "https://auth.openai.com/api/accounts/authorize?x=1" in shown
    assert "Exchanging authorization code for tokens..." in shown
    assert "chatgpt-access" not in shown
    assert store.get("openai") == _stored()
    assert state.provider_available("openai")
    assert state.is_using_subscription("openai")

    ok, message = repl_state.logout("openai")
    assert ok and message == "pipy: openai OAuth credentials removed."
    assert store.get("openai") is None
    assert not state.is_using_subscription("openai")


def test_repl_login_without_device_id_fails_like_pi(tmp_path: Path) -> None:
    state = _catalog_state(tmp_path, AuthStore(path=tmp_path / "auth.json"), {})
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        model_runtime=ModelRuntime(catalog=state),
        persist_defaults=False,
        browser_opener=lambda _url: None,
    )
    ok, message = repl_state.login(
        "openai", input_stream=io.StringIO("\n"), output_stream=io.StringIO()
    )
    assert not ok
    assert message == (
        "pipy: openai login failed: Sign in with ChatGPT requires a device ID "
        "(UUID) for this installation"
    )


def test_repl_login_reports_flow_errors_and_cancellation(tmp_path: Path) -> None:
    server = _TokenServer([(200, _token_response(id_token=None))])
    port = _free_port()

    def factory() -> OpenAIChatGPTOAuthProvider:
        return OpenAIChatGPTOAuthProvider(
            transport=_default_transport,
            token_url=server.url,
            callback_host="127.0.0.1",
            callback_port=port,
        )

    state = _catalog_state(tmp_path, AuthStore(path=tmp_path / "auth.json"), {})
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        model_runtime=ModelRuntime(catalog=state),
        persist_defaults=False,
        chatgpt_oauth_factory=factory,
        device_id_provider=lambda: DEVICE_ID,
        browser_opener=lambda _url: None,
    )
    out = io.StringIO()

    class _PasteCallback(io.StringIO):
        def readline(self, *_args: object) -> str:  # type: ignore[override]
            url = out.getvalue().split("Open this URL in your browser:\n", 1)[1]
            query = urllib.parse.urlsplit(url.splitlines()[0]).query
            state_value = dict(urllib.parse.parse_qsl(query))["state"]
            params = {"code": "c", "state": state_value, "client_id": "oaiapp"}
            return f"{REDIRECT}?{urllib.parse.urlencode(params)}\n"

    try:
        ok, message = repl_state.login(
            "openai", input_stream=_PasteCallback(), output_stream=out
        )
    finally:
        server.close()
    assert not ok
    assert message == (
        "pipy: openai login failed: OpenAI OAuth token response did not contain "
        "an ID token"
    )
    assert "Complete login in your browser, or paste the final redirect URL" in (
        out.getvalue()
    )

    class _Interrupt(io.StringIO):
        def readline(self, *_args: object) -> str:  # type: ignore[override]
            raise KeyboardInterrupt

    ok, message = repl_state.login(
        "openai", input_stream=_Interrupt(), output_stream=io.StringIO()
    )
    assert (ok, message) == (False, "pipy: openai login cancelled.")
    assert state.auth_store is not None and state.auth_store.get("openai") is None


def test_logout_keeps_an_openai_selection_usable_through_the_env_key(
    tmp_path: Path,
) -> None:
    store = AuthStore(path=tmp_path / "auth.json")
    store.set("openai", _stored())
    state = _catalog_state(tmp_path, store, {"OPENAI_API_KEY": "sk-env"})
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("openai", "gpt-5.5"),
        model_runtime=ModelRuntime(catalog=state),
        persist_defaults=False,
    )
    ok, _message = repl_state.logout("openai")
    assert ok
    assert repl_state.current_selection() == NativeModelSelection("openai", "gpt-5.5")
