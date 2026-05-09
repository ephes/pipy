from __future__ import annotations

import base64
import io
import json
import stat
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest
from pipy_harness.native.openai_codex_provider import (
    FileOpenAICodexCredentialStore,
    JsonResponse,
    OAuthTokenResponse,
    OpenAICodexCredentials,
    OpenAICodexHTTPStatusError,
    OpenAICodexResponsesProvider,
    OpenAICodexAuthManager,
    UrllibJsonHTTPClient,
    create_authorization_flow,
    parse_authorization_input,
)


class FakeJsonHTTPClient:
    def __init__(self, response: JsonResponse | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests: list[dict[str, Any]] = []

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
    ) -> JsonResponse:
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers),
                "body": dict(body),
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


class FakeOAuthHTTPClient:
    def __init__(self, response: OAuthTokenResponse) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    def post_form(
        self,
        url: str,
        *,
        fields: Mapping[str, str],
        timeout_seconds: float,
    ) -> OAuthTokenResponse:
        self.requests.append(
            {
                "url": url,
                "fields": dict(fields),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.response


class InMemoryCredentialStore:
    def __init__(self, credentials: OpenAICodexCredentials | None) -> None:
        self.credentials = credentials
        self.saved: list[OpenAICodexCredentials] = []

    def load(self) -> OpenAICodexCredentials | None:
        return self.credentials

    def save(self, credentials: OpenAICodexCredentials) -> None:
        self.credentials = credentials
        self.saved.append(credentials)

    def delete(self) -> bool:
        had_credentials = self.credentials is not None
        self.credentials = None
        return had_credentials


def provider_request(tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED",
        user_prompt="SAFE_GOAL_METADATA",
        provider_name="openai-codex",
        model_id="gpt-test",
        cwd=tmp_path,
    )


def fake_jwt(account_id: str = "acct_test") -> str:
    header = _base64url({"alg": "none"})
    payload = _base64url({"https://api.openai.com/auth": {"chatgpt_account_id": account_id}})
    return f"{header}.{payload}.signature"


def credentials(*, expires_at: int = 4_102_444_800) -> OpenAICodexCredentials:
    return OpenAICodexCredentials(
        access_token=fake_jwt("acct_original"),
        refresh_token="refresh-original",
        expires_at=expires_at,
        account_id="acct_original",
    )


def auth_manager_with(credentials_value: OpenAICodexCredentials | None) -> OpenAICodexAuthManager:
    return OpenAICodexAuthManager(store=InMemoryCredentialStore(credentials_value))


def test_openai_codex_provider_posts_responses_request_and_parses_output(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=200,
            body={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "hello"},
                            {"type": "output_text", "text": " codex"},
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 10,
                    "input_tokens_details": {"cached_tokens": 4},
                    "output_tokens": 2,
                    "output_tokens_details": {"reasoning_tokens": 1},
                    "total_tokens": 12,
                    "native_unlisted": 99,
                },
            },
        )
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.provider_name == "openai-codex"
    assert result.model_id == "gpt-test"
    assert result.final_text == "hello codex"
    assert result.usage == {
        "cached_tokens": 4,
        "input_tokens": 10,
        "output_tokens": 2,
        "reasoning_tokens": 1,
        "total_tokens": 12,
    }
    assert result.metadata == {
        "provider_response_store_requested": False,
        "response_status": "completed",
    }
    posted = client.requests[0]
    assert posted["url"] == "https://chatgpt.com/backend-api/codex/responses"
    assert posted["headers"]["Authorization"] == f"Bearer {fake_jwt('acct_original')}"
    assert posted["headers"]["chatgpt-account-id"] == "acct_original"
    assert posted["headers"]["originator"] == "pipy"
    assert posted["headers"]["OpenAI-Beta"] == "responses=experimental"
    assert posted["headers"]["Content-Type"] == "application/json"
    assert posted["body"] == {
        "model": "gpt-test",
        "instructions": "SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED",
        "input": "SAFE_GOAL_METADATA",
        "store": False,
        "stream": False,
    }


def test_openai_codex_provider_accepts_top_level_output_text(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(status_code=200, body={"status": "completed", "output_text": "short text"})
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "short text"


def test_openai_codex_provider_missing_credentials_fails_without_http(tmp_path):
    client = FakeJsonHTTPClient()
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(None),
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenAICodexAuthError"
    assert "OpenAI Codex login is required" in (result.error_message or "")
    assert client.requests == []


def test_openai_codex_auth_manager_refreshes_expiring_credentials():
    store = InMemoryCredentialStore(credentials(expires_at=1))
    oauth_client = FakeOAuthHTTPClient(
        OAuthTokenResponse(
            access_token=fake_jwt("acct_refreshed"),
            refresh_token="refresh-next",
            expires_in=3600,
        )
    )
    manager = OpenAICodexAuthManager(store=store, oauth_client=oauth_client)

    refreshed = manager.get_credentials()

    assert refreshed is not None
    assert refreshed.account_id == "acct_refreshed"
    assert refreshed.refresh_token == "refresh-next"
    assert store.saved == [refreshed]
    request = oauth_client.requests[0]
    assert request["url"] == "https://auth.openai.com/oauth/token"
    assert request["fields"] == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh-original",
        "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
    }


def test_openai_codex_credential_store_uses_pipy_owned_private_file(tmp_path):
    auth_path = tmp_path / "auth" / "openai-codex.json"
    store = FileOpenAICodexCredentialStore(auth_path)

    store.save(credentials())

    assert auth_path.exists()
    assert stat.S_IMODE(auth_path.stat().st_mode) == 0o600
    loaded = store.load()
    assert loaded == credentials()
    body = json.loads(auth_path.read_text(encoding="utf-8"))
    assert body["provider"] == "openai-codex"
    assert body["type"] == "oauth"
    assert store.delete() is True
    assert not auth_path.exists()
    assert store.delete() is False


def test_openai_codex_auth_manager_logout_deletes_stored_credentials(tmp_path):
    auth_path = tmp_path / "auth" / "openai-codex.json"
    store = FileOpenAICodexCredentialStore(auth_path)
    manager = OpenAICodexAuthManager(store=store)
    store.save(credentials())

    assert manager.logout() is True
    assert not auth_path.exists()
    assert manager.logout() is False


def test_openai_codex_provider_http_error_keeps_message_conservative(tmp_path):
    error_body = json.dumps(
        {
            "error": {
                "type": "invalid_request_error",
                "code": "bad_request",
                "message": "SYSTEM_PROMPT_SHOULD_NOT_BE_STORED",
            }
        }
    ).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url="https://chatgpt.com/backend-api/codex/responses",
        code=400,
        msg="Bad Request",
        hdrs={},
        fp=io.BytesIO(error_body),
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=FakeJsonHTTPClient(error=OpenAICodexHTTPStatusError.from_http_error(http_error)),
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenAICodexHTTPStatusError"
    assert result.error_message == "OpenAI Codex request failed with HTTP status 400."
    assert result.metadata == {
        "api_error_code": "bad_request",
        "api_error_type": "invalid_request_error",
        "http_status": 400,
    }
    assert "SYSTEM_PROMPT" not in json.dumps(result.metadata, sort_keys=True)
    assert "SYSTEM_PROMPT" not in (result.error_message or "")


def test_openai_codex_provider_non_success_boundary_status_fails_safely(tmp_path):
    client = FakeJsonHTTPClient(
        JsonResponse(
            status_code=503,
            body={"status": "completed", "output_text": "MODEL_OUTPUT_SHOULD_NOT_PRINT"},
        )
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenAICodexHTTPStatusError"
    assert result.error_message == "OpenAI Codex request failed with HTTP status 503."
    assert result.metadata == {"http_status": 503}
    assert result.final_text is None


def test_openai_codex_authorization_flow_uses_pkce_and_pi_reference_shape():
    flow = create_authorization_flow()
    parsed = urllib.parse.urlparse(flow.url)
    params = urllib.parse.parse_qs(parsed.query)

    assert parsed.geturl().startswith("https://auth.openai.com/oauth/authorize?")
    assert params["client_id"] == ["app_EMoamEEZ73f0CkXaXp7hrann"]
    assert params["redirect_uri"] == ["http://localhost:1455/auth/callback"]
    assert params["scope"] == ["openid profile email offline_access"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["originator"] == ["pipy"]
    assert params["codex_cli_simplified_flow"] == ["true"]
    assert params["id_token_add_organizations"] == ["true"]
    assert params["state"] == [flow.state]
    assert params["code_challenge"][0] != flow.verifier


def test_openai_codex_parse_authorization_input_accepts_url_query_and_code_state_pair():
    parsed_url = parse_authorization_input(
        "http://localhost:1455/auth/callback?code=abc123&state=state456"
    )
    parsed_query = parse_authorization_input("code=abc123&state=state456")
    parsed_pair = parse_authorization_input("abc123#state456")
    parsed_code = parse_authorization_input("abc123")

    assert parsed_url.code == "abc123"
    assert parsed_url.state == "state456"
    assert parsed_query == parsed_url
    assert parsed_pair == parsed_url
    assert parsed_code.code == "abc123"
    assert parsed_code.state is None


def test_urllib_json_http_client_translates_http_error_without_raw_body(monkeypatch):
    error_body = json.dumps(
        {
            "error": {
                "type": "invalid_request_error",
                "code": "bad_request",
                "message": "SYSTEM_PROMPT_SHOULD_NOT_BE_STORED",
            }
        }
    ).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url="https://chatgpt.com/backend-api/codex/responses",
        code=400,
        msg="Bad Request",
        hdrs={},
        fp=io.BytesIO(error_body),
    )

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> None:
        assert request.full_url == "https://chatgpt.com/backend-api/codex/responses"
        assert request.get_method() == "POST"
        assert timeout == 12.0
        assert request.headers["Content-type"] == "application/json"
        assert request.data == b'{"model": "gpt-test"}'
        raise http_error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    try:
        UrllibJsonHTTPClient().post_json(
            "https://chatgpt.com/backend-api/codex/responses",
            headers={"Content-Type": "application/json"},
            body={"model": "gpt-test"},
            timeout_seconds=12.0,
        )
    except OpenAICodexHTTPStatusError as exc:
        assert str(exc) == "OpenAI Codex request failed with HTTP status 400."
        assert exc.metadata == {
            "api_error_code": "bad_request",
            "api_error_type": "invalid_request_error",
            "http_status": 400,
        }
        assert "SYSTEM_PROMPT" not in str(exc)
    else:
        raise AssertionError("expected OpenAICodexHTTPStatusError")


def _base64url(value: Mapping[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode("utf-8")).decode("ascii").rstrip("=")
