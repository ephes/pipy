from __future__ import annotations

import base64
import errno
import io
import json
import stat
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import replace
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import (
    NativeToolReplSession,
    ProviderRequest,
)
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native.openai_codex_provider import (
    FileOpenAICodexCredentialStore,
    OAuthTokenResponse,
    OpenAICodexCredentials,
    OpenAICodexHTTPStatusError,
    OpenAICodexResponsesProvider,
    OpenAICodexStreamInterruptedError,
    OpenAICodexTransportError,
    SseResponse,
    OpenAICodexAuthManager,
    UrllibSseHTTPClient,
    create_authorization_flow,
    parse_authorization_input,
)
from pipy_harness.native.retry import RetryPolicy


class FakeSseHTTPClient:
    def __init__(self, response: SseResponse | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests: list[dict[str, Any]] = []

    def post_sse(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float | None,
        cancel_token: object = None,
    ) -> SseResponse:
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
        cancel_token: object = None,
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
    client = FakeSseHTTPClient(
        SseResponse(
            status_code=200,
            body=sse_payload(
                [
                    {
                        "type": "response.output_text.delta",
                        "delta": "hello",
                    },
                    {
                        "type": "response.output_text.delta",
                        "delta": " codex",
                    },
                    {
                        "type": "response.completed",
                        "response": {
                            "status": "completed",
                            "usage": {
                                "input_tokens": 10,
                                "input_tokens_details": {"cached_tokens": 4},
                                "output_tokens": 2,
                                "output_tokens_details": {"reasoning_tokens": 1},
                                "total_tokens": 12,
                                "native_unlisted": 99,
                            },
                        },
                    },
                ]
            ),
        )
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
        transport="sse",
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
    assert posted["headers"]["Accept"] == "text/event-stream"
    assert posted["headers"]["chatgpt-account-id"] == "acct_original"
    assert posted["headers"]["originator"] == "pipy"
    assert posted["headers"]["OpenAI-Beta"] == "responses=experimental"
    assert posted["headers"]["Content-Type"] == "application/json"
    assert posted["timeout_seconds"] == 300.0
    assert posted["body"] == {
        "model": "gpt-test",
        "instructions": "SYSTEM_PROMPT_SHOULD_BE_SENT_NOT_STORED",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "SAFE_GOAL_METADATA"}],
            }
        ],
        "store": False,
        "stream": True,
        "text": {"verbosity": "low"},
        "include": ["reasoning.encrypted_content"],
        "reasoning": {"summary": "auto"},
        "tool_choice": "auto",
        "parallel_tool_calls": True,
    }


def _completed_sse() -> SseResponse:
    return SseResponse(
        status_code=200,
        body=sse_payload(
            [
                {"type": "response.output_text.delta", "delta": "ok"},
                {"type": "response.completed", "response": {"status": "completed"}},
            ]
        ),
    )


def test_openai_codex_provider_emits_reasoning_effort_when_set(tmp_path):
    client = FakeSseHTTPClient(_completed_sse())
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-5.6-sol",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
        transport="sse",
        reasoning_effort="max",
    )

    provider.complete(provider_request(tmp_path))

    posted = client.requests[0]
    assert posted["url"] == "https://chatgpt.com/backend-api/codex/responses"
    assert posted["body"]["reasoning"] == {"summary": "auto", "effort": "max"}


def test_openai_codex_provider_emits_mapped_low_effort(tmp_path):
    # The REPL boundary maps Sol's `minimal` to `low`; the provider carries and
    # emits the pre-resolved value.
    client = FakeSseHTTPClient(_completed_sse())
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-5.6-sol",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
        transport="sse",
        reasoning_effort="low",
    )

    provider.complete(provider_request(tmp_path))

    assert client.requests[0]["body"]["reasoning"] == {"summary": "auto", "effort": "low"}


def test_openai_codex_provider_omits_effort_when_unset(tmp_path):
    client = FakeSseHTTPClient(_completed_sse())
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-5.6-sol",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
        transport="sse",
    )

    provider.complete(provider_request(tmp_path))

    assert client.requests[0]["body"]["reasoning"] == {"summary": "auto"}


def test_openai_codex_provider_accepts_output_item_done_text_without_delta(tmp_path):
    client = FakeSseHTTPClient(
        SseResponse(
            status_code=200,
            body=sse_payload(
                [
                    {
                        "type": "response.output_item.done",
                        "item": {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "short text"}],
                        },
                    },
                    {"type": "response.completed", "response": {"status": "completed"}},
                ]
            ),
        )
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
        transport="sse",
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "short text"


def test_openai_codex_provider_accepts_crlf_done_and_ignores_non_data_sse_lines(tmp_path):
    delta = json.dumps({"type": "response.output_text.delta", "delta": "hello"})
    completed = json.dumps({"type": "response.completed", "response": {"status": "completed"}})
    client = FakeSseHTTPClient(
        SseResponse(
            status_code=200,
            body=(
                "event: response.created\r\n\r\n"
                ": keepalive\r\n\r\n"
                f"data: {delta}\r\n\r\n"
                "not-data: ignored\r\n\r\n"
                f"data: {completed}\r\n\r\n"
                "data: [DONE]\r\n\r\n"
            ),
        )
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
        transport="sse",
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "hello"


def test_openai_codex_provider_missing_credentials_fails_without_http(tmp_path):
    client = FakeSseHTTPClient()
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
        hdrs=Message(),
        fp=io.BytesIO(error_body),
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=FakeSseHTTPClient(error=OpenAICodexHTTPStatusError.from_http_error(http_error)),
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenAICodexHTTPStatusError"
    assert result.error_message == "OpenAI Codex request failed with HTTP status 400."
    assert result.metadata == {
        "api_error_type": "invalid_request_error",
        "attempt": 1,
        "exhausted": False,
        "http_status": 400,
        "max_attempts": 4,
        "progress": "none",
        "retryable": False,
    }
    assert "SYSTEM_PROMPT" not in json.dumps(result.metadata, sort_keys=True)
    assert "SYSTEM_PROMPT" not in (result.error_message or "")


def test_openai_codex_provider_non_success_boundary_status_fails_safely(tmp_path):
    client = FakeSseHTTPClient(
        SseResponse(
            status_code=503,
            body=sse_payload(
                [
                    {
                        "type": "response.output_text.delta",
                        "delta": "MODEL_OUTPUT_SHOULD_NOT_PRINT",
                    },
                    {"type": "response.completed", "response": {"status": "completed"}},
                ]
            ),
        )
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
        retry_policy=RetryPolicy(max_attempts=1),
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "OpenAICodexHTTPStatusError"
    assert result.error_message == "OpenAI Codex request failed with HTTP status 503."
    assert result.metadata == {
        "attempt": 1,
        "exhausted": True,
        "http_status": 503,
        "max_attempts": 1,
        "progress": "none",
        "retryable": True,
    }
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


def test_urllib_sse_http_client_translates_http_error_without_raw_body(monkeypatch):
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
        hdrs=Message(),
        fp=io.BytesIO(error_body),
    )

    def fake_urlopen(request: urllib.request.Request, timeout: float | None) -> None:
        assert request.full_url == "https://chatgpt.com/backend-api/codex/responses"
        assert request.get_method() == "POST"
        assert timeout == 12.0
        assert request.headers["Content-type"] == "application/json"
        assert request.data == b'{"model": "gpt-test"}'
        raise http_error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    try:
        UrllibSseHTTPClient().post_sse(
            "https://chatgpt.com/backend-api/codex/responses",
            headers={"Content-Type": "application/json"},
            body={"model": "gpt-test"},
            timeout_seconds=12.0,
        )
    except OpenAICodexHTTPStatusError as exc:
        assert str(exc) == "OpenAI Codex request failed with HTTP status 400."
        assert exc.metadata == {
            "api_error_type": "invalid_request_error",
            "http_status": 400,
        }
        assert "SYSTEM_PROMPT" not in str(exc)
    else:
        raise AssertionError("expected OpenAICodexHTTPStatusError")


@pytest.mark.parametrize(
    "unsafe_label",
    [
        "PROMPT_SHOULD_NOT_LEAK",
        "https://private.example/body",
        "sk-proj-TOKEN_SHOULD_NOT_LEAK",
        "response body text",
        "control\ntext",
        "étiquette",
        "x" * 65,
    ],
)
def test_http_error_omits_unknown_or_payload_like_api_labels(
    unsafe_label: str,
) -> None:
    error_body = json.dumps(
        {"error": {"type": unsafe_label, "code": unsafe_label}}
    ).encode("utf-8")
    http_error = urllib.error.HTTPError(
        url="https://chatgpt.com/backend-api/codex/responses",
        code=400,
        msg="Bad Request",
        hdrs=Message(),
        fp=io.BytesIO(error_body),
    )

    error = OpenAICodexHTTPStatusError.from_http_error(http_error)

    assert error.metadata == {"http_status": 400}
    assert unsafe_label not in str(error)
    assert unsafe_label not in json.dumps(error.metadata, sort_keys=True)


class _RaisingHTTPErrorBody:
    def __init__(
        self,
        error: BaseException,
        *,
        before_raise: object | None = None,
    ) -> None:
        self.error = error
        self.before_raise = before_raise

    def read(self, *_args: object, **_kwargs: object) -> bytes:
        if callable(self.before_raise):
            self.before_raise()
        raise self.error

    def close(self) -> None:
        pass


def _http_error_with_body_reader(reader: Any) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://chatgpt.com/backend-api/codex/responses",
        code=503,
        msg="Service Unavailable",
        hdrs=Message(),
        fp=reader,
    )


@pytest.mark.parametrize(
    "read_error",
    [
        TimeoutError("The read operation timed out"),
        OSError(errno.ECONNRESET, "PRIVATE_CONNECTION_DETAIL"),
        OSError(errno.ETIMEDOUT, "PRIVATE_TIMEOUT_DETAIL"),
    ],
)
def test_http_error_body_transport_failure_returns_status_only_error(
    monkeypatch: pytest.MonkeyPatch, read_error: BaseException
) -> None:
    http_error = _http_error_with_body_reader(_RaisingHTTPErrorBody(read_error))
    monkeypatch.setattr(
        "pipy_harness.native.openai_codex_provider.open_url_cancellable",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(http_error),
    )

    with pytest.raises(OpenAICodexHTTPStatusError) as raised:
        UrllibSseHTTPClient().post_sse(
            "https://chatgpt.com/backend-api/codex/responses",
            headers={"Content-Type": "application/json"},
            body={"model": "gpt-test"},
            timeout_seconds=300.0,
        )

    assert str(raised.value) == (
        "OpenAI Codex request failed with HTTP status 503."
    )
    assert raised.value.metadata == {"http_status": 503}
    assert "PRIVATE" not in str(raised.value)
    assert "read operation timed out" not in str(raised.value).lower()


def test_http_error_body_unrelated_os_error_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_error = _http_error_with_body_reader(
        _RaisingHTTPErrorBody(OSError(errno.EACCES, "PRIVATE_PATH"))
    )
    monkeypatch.setattr(
        "pipy_harness.native.openai_codex_provider.open_url_cancellable",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(http_error),
    )

    with pytest.raises(OSError, match="PRIVATE_PATH"):
        UrllibSseHTTPClient().post_sse(
            "https://chatgpt.com/backend-api/codex/responses",
            headers={"Content-Type": "application/json"},
            body={"model": "gpt-test"},
            timeout_seconds=300.0,
        )


def test_http_error_body_cancel_wins_timeout_close_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = CancelToken()
    http_error = _http_error_with_body_reader(
        _RaisingHTTPErrorBody(
            TimeoutError("The read operation timed out"),
            before_raise=token.cancel,
        )
    )
    monkeypatch.setattr(
        "pipy_harness.native.openai_codex_provider.open_url_cancellable",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(http_error),
    )

    with pytest.raises(ProviderCancelledError):
        UrllibSseHTTPClient().post_sse(
            "https://chatgpt.com/backend-api/codex/responses",
            headers={"Content-Type": "application/json"},
            body={"model": "gpt-test"},
            timeout_seconds=300.0,
            cancel_token=token,
        )


@pytest.mark.parametrize(
    "unsafe_label",
    [
        "PROMPT_SHOULD_NOT_LEAK",
        "https://private.example/body",
        "sk-proj-TOKEN_SHOULD_NOT_LEAK",
        "response body text",
        {"prompt": "PRIVATE_BODY"},
    ],
)
def test_sse_error_event_omits_unknown_or_payload_like_code(
    tmp_path: Path, unsafe_label: object
) -> None:
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=FakeSseHTTPClient(
            SseResponse(
                status_code=200,
                body=sse_payload([{"type": "error", "code": unsafe_label}]),
            )
        ),
    )

    result = provider.complete(provider_request(tmp_path))

    serialized = json.dumps(result.metadata, sort_keys=True)
    assert result.status == HarnessStatus.FAILED
    assert result.error_message == "OpenAI Codex stream returned an error event."
    assert result.metadata == {
        "attempt": 1,
        "exhausted": False,
        "max_attempts": 4,
        "progress": "event",
        "provider_response_store_requested": False,
        "response_status": "unknown",
        "retryable": False,
    }
    assert str(unsafe_label) not in serialized
    assert str(unsafe_label) not in (result.error_message or "")


def test_sse_error_event_keeps_allowlisted_code(tmp_path: Path) -> None:
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=FakeSseHTTPClient(
            SseResponse(
                status_code=200,
                body=sse_payload([{"type": "error", "code": "rate_limit_error"}]),
            )
        ),
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.metadata == {
        "api_error_code": "rate_limit_error",
        "attempt": 1,
        "exhausted": False,
        "max_attempts": 4,
        "progress": "event",
        "provider_response_store_requested": False,
        "response_status": "unknown",
        "retryable": False,
    }


def test_sse_error_event_code_does_not_leak_through_repl_result_or_stderr(
    tmp_path: Path,
) -> None:
    unsafe_label = "PROMPT_BODY_MUST_NOT_REACH_DIAGNOSTICS"
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=FakeSseHTTPClient(
            SseResponse(
                status_code=200,
                body=sse_payload([{"type": "error", "code": unsafe_label}]),
            )
        ),
    )
    stderr = io.StringIO()

    result = NativeToolReplSession(provider=provider).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("hello\n"),
        output_stream=io.StringIO(),
        error_stream=stderr,
    )

    assert result.provider_failure_message == (
        "OpenAI Codex stream returned an error event."
    )
    assert unsafe_label not in stderr.getvalue()
    assert unsafe_label not in json.dumps(
        {
            "provider_failure_type": result.provider_failure_type,
            "provider_failure_message": result.provider_failure_message,
        },
        sort_keys=True,
    )


@pytest.mark.parametrize(
    "unsafe_status",
    [
        "PROMPT_SHOULD_NOT_LEAK",
        "https://private.example/body",
        "sk-proj-TOKEN_SHOULD_NOT_LEAK",
        "response body text",
        "control\ntext",
        "étiquette",
        "x" * 65,
    ],
)
def test_terminal_response_uses_frozen_status_allowlist(
    tmp_path: Path, unsafe_status: str
) -> None:
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=FakeSseHTTPClient(
            SseResponse(
                status_code=200,
                body=sse_payload(
                    [
                        {
                            "type": "response.done",
                            "response": {"status": unsafe_status},
                        }
                    ]
                ),
            )
        ),
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.error_message == (
        "OpenAI Codex response did not complete successfully."
    )
    assert result.metadata is not None
    assert result.metadata["response_status"] == "unknown"
    assert result.metadata["progress"] == "event"
    assert unsafe_status not in (result.error_message or "")
    assert unsafe_status not in json.dumps(result.metadata, sort_keys=True)


@pytest.mark.parametrize("status", ["failed", "incomplete", "cancelled"])
def test_terminal_non_success_status_is_fixed_and_non_retryable(
    tmp_path: Path, status: str
) -> None:
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=FakeSseHTTPClient(
            SseResponse(
                status_code=200,
                body=sse_payload(
                    [
                        {
                            "type": f"response.{status}",
                            "response": {"status": status},
                        }
                    ]
                ),
            )
        ),
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.error_message == (
        "OpenAI Codex response did not complete successfully."
    )
    assert result.metadata is not None
    assert result.metadata["response_status"] == status
    assert result.metadata["retryable"] is False


class _FailingStreamResponse:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.closed = False

    def getcode(self) -> int:
        return 200

    def __iter__(self) -> _FailingStreamResponse:
        return self

    def __next__(self) -> bytes:
        raise self.error

    def close(self) -> None:
        self.closed = True


def test_historical_stream_read_timeout_is_sanitized_provider_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _FailingStreamResponse(TimeoutError("The read operation timed out"))
    observed_timeouts: list[float | None] = []

    def fake_open_url(
        _request: urllib.request.Request,
        *,
        timeout_seconds: float | None,
        cancel_token: object = None,
    ) -> _FailingStreamResponse:
        del cancel_token
        observed_timeouts.append(timeout_seconds)
        return response

    monkeypatch.setattr(
        "pipy_harness.native.openai_codex_provider.open_url_cancellable",
        fake_open_url,
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=UrllibSseHTTPClient(),
        retry_policy=RetryPolicy(max_attempts=1),
    )

    result = provider.complete(provider_request(tmp_path))

    assert observed_timeouts == [300.0]
    assert response.closed is True
    assert result.status == HarnessStatus.FAILED
    assert result.error_type == OpenAICodexStreamInterruptedError.__name__
    assert result.error_message == (
        "OpenAI Codex stream was interrupted before completion."
    )
    assert result.metadata == {
        "attempt": 1,
        "exhausted": True,
        "max_attempts": 1,
        "phase": "stream",
        "progress": "none",
        "retryable": True,
        "transport": "sse",
    }
    assert "read operation timed out" not in json.dumps(result.metadata)
    assert "read operation timed out" not in (result.error_message or "").lower()


def test_header_timeout_is_sanitized_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_open_url(*_args: object, **_kwargs: object) -> None:
        raise TimeoutError("PRIVATE_RAW_TIMEOUT_TEXT")

    monkeypatch.setattr(
        "pipy_harness.native.openai_codex_provider.open_url_cancellable",
        fake_open_url,
    )

    with pytest.raises(OpenAICodexTransportError) as raised:
        UrllibSseHTTPClient().post_sse(
            "https://chatgpt.com/backend-api/codex/responses",
            headers={"Content-Type": "application/json"},
            body={"model": "gpt-test"},
            timeout_seconds=300.0,
        )

    assert str(raised.value) == (
        "OpenAI Codex transport failed while waiting for response headers."
    )
    assert raised.value.metadata == {
        "phase": "headers",
        "retryable": True,
        "transport": "sse",
    }
    assert "PRIVATE_RAW_TIMEOUT_TEXT" not in str(raised.value)


def test_unrelated_os_error_is_not_normalized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _FailingStreamResponse(OSError(13, "UNRELATED_PRIVATE_PATH"))

    monkeypatch.setattr(
        "pipy_harness.native.openai_codex_provider.open_url_cancellable",
        lambda *_args, **_kwargs: response,
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=UrllibSseHTTPClient(),
    )

    with pytest.raises(OSError, match="UNRELATED_PRIVATE_PATH"):
        provider.complete(provider_request(tmp_path))


def _base64url(value: Mapping[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode("utf-8")).decode("ascii").rstrip("=")


def sse_payload(events: list[Mapping[str, Any]]) -> str:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events)


def _streaming_sse_events() -> list[Mapping[str, Any]]:
    return [
        {"type": "response.output_text.delta", "delta": "Hello"},
        {"type": "response.output_text.delta", "delta": ", "},
        {"type": "response.output_text.delta", "delta": "world"},
        {"type": "response.output_text.delta", "delta": "!"},
        {
            "type": "response.completed",
            "response": {"status": "completed", "usage": {"input_tokens": 1, "output_tokens": 4, "total_tokens": 5}},
        },
    ]


def test_openai_codex_provider_streams_text_deltas_through_sink_in_source_order(tmp_path):
    captured: list[str] = []
    client = FakeSseHTTPClient(
        SseResponse(status_code=200, body=sse_payload(_streaming_sse_events()))
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
        transport="sse",
    )

    result = provider.complete(provider_request(tmp_path), stream_sink=captured.append)

    assert captured == ["Hello", ", ", "world", "!"]
    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "Hello, world!"
    assert result.metadata == {
        "provider_response_store_requested": False,
        "response_status": "completed",
    }


def test_openai_codex_provider_complete_without_sink_matches_streaming_final_text(tmp_path):
    no_sink_client = FakeSseHTTPClient(
        SseResponse(status_code=200, body=sse_payload(_streaming_sse_events()))
    )
    with_sink_client = FakeSseHTTPClient(
        SseResponse(status_code=200, body=sse_payload(_streaming_sse_events()))
    )
    provider_no_sink = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=no_sink_client,
        transport="sse",
    )
    provider_with_sink = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=with_sink_client,
        transport="sse",
    )
    captured: list[str] = []

    no_sink_result = provider_no_sink.complete(provider_request(tmp_path))
    with_sink_result = provider_with_sink.complete(
        provider_request(tmp_path), stream_sink=captured.append
    )

    assert no_sink_result.final_text == with_sink_result.final_text
    assert "".join(captured) == no_sink_result.final_text


def test_openai_codex_provider_does_not_stream_when_sink_is_none(tmp_path):
    client = FakeSseHTTPClient(
        SseResponse(status_code=200, body=sse_payload(_streaming_sse_events()))
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
        transport="sse",
    )

    result = provider.complete(provider_request(tmp_path), stream_sink=None)

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "Hello, world!"


def test_openai_codex_provider_streaming_does_not_call_sink_for_empty_delta(tmp_path):
    captured: list[str] = []
    events = [
        {"type": "response.output_text.delta", "delta": ""},
        {"type": "response.output_text.delta", "delta": "ok"},
        {"type": "response.completed", "response": {"status": "completed"}},
    ]
    client = FakeSseHTTPClient(
        SseResponse(status_code=200, body=sse_payload(events))
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=client,
        transport="sse",
    )

    result = provider.complete(provider_request(tmp_path), stream_sink=captured.append)

    assert captured == ["ok"]
    assert result.final_text == "ok"


class FakeWebSocketClient:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.requests: list[dict[str, Any]] = []

    def post_events(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        connect_timeout_seconds: float | None,
        idle_timeout_seconds: float | None,
        cancel_token: object = None,
    ):
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers),
                "body": dict(body),
                "connect_timeout_seconds": connect_timeout_seconds,
                "idle_timeout_seconds": idle_timeout_seconds,
            }
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return iter(outcome)


def completed_events(text: str = "ws ok") -> list[Mapping[str, Any]]:
    return [
        {"type": "response.output_text.delta", "delta": text},
        {"type": "response.completed", "response": {"status": "completed"}},
    ]


def test_transport_sse_does_not_construct_websocket(tmp_path: Path) -> None:
    sse = FakeSseHTTPClient(SseResponse(status_code=200, body=sse_payload(completed_events("sse ok"))))
    ws = FakeWebSocketClient([completed_events()])
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=sse,
        websocket_client=ws,
        transport="sse",
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "sse ok"
    assert ws.requests == []
    assert len(sse.requests) == 1


def test_transport_websocket_uses_ws_headers_and_request(tmp_path: Path) -> None:
    sse = FakeSseHTTPClient(SseResponse(status_code=200, body=sse_payload(completed_events("sse"))))
    ws = FakeWebSocketClient([completed_events("ws ok")])
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=sse,
        websocket_client=ws,
        transport="websocket",
        request_id_factory=lambda: "request-1",
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "ws ok"
    assert sse.requests == []
    assert ws.requests[0]["headers"]["OpenAI-Beta"] == "responses_websockets=2026-02-06"
    assert ws.requests[0]["headers"]["session-id"] == "request-1"
    assert ws.requests[0]["headers"]["x-client-request-id"] == "request-1"
    assert ws.requests[0]["connect_timeout_seconds"] == 15.0
    assert ws.requests[0]["idle_timeout_seconds"] == 300.0


def test_pre_event_websocket_transport_failure_falls_back_to_sse(tmp_path: Path) -> None:
    sse = FakeSseHTTPClient(SseResponse(status_code=200, body=sse_payload(completed_events("sse fallback"))))
    ws = FakeWebSocketClient([
        OpenAICodexTransportError(
            "OpenAI Codex transport failed while waiting for response headers.",
            metadata={"phase": "headers", "retryable": True, "transport": "websocket"},
        )
    ])
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=sse,
        websocket_client=ws,
        transport="websocket",
        retry_policy=RetryPolicy(max_attempts=1),
    )

    hook_calls = 0

    def mutate(headers):
        nonlocal hook_calls
        hook_calls += 1
        headers["X-Trace"] = "shared"

    request = replace(
        provider_request(tmp_path), provider_header_callback=mutate
    )
    result = provider.complete(request)

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "sse fallback"
    assert hook_calls == 1
    assert len(ws.requests) == 1
    assert len(sse.requests) == 1
    assert ws.requests[0]["headers"]["X-Trace"] == "shared"
    assert sse.requests[0]["headers"]["X-Trace"] == "shared"


def test_auto_transport_remembers_sse_after_pre_event_ws_failure(tmp_path: Path) -> None:
    sse = FakeSseHTTPClient(SseResponse(status_code=200, body=sse_payload(completed_events("sse"))))
    ws = FakeWebSocketClient([
        OpenAICodexTransportError(

            "OpenAI Codex transport failed while waiting for response headers.",
            metadata={"phase": "headers", "retryable": True, "transport": "websocket"},
        ),
        completed_events("unexpected ws"),
    ])
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=sse,
        websocket_client=ws,
        transport="auto",
        retry_policy=RetryPolicy(max_attempts=1),
    )

    first = provider.complete(provider_request(tmp_path))
    second = provider.complete(provider_request(tmp_path))

    assert first.final_text == "sse"
    assert second.final_text == "sse"
    assert len(ws.requests) == 1
    assert len(sse.requests) == 2


def test_websocket_connection_limit_gets_one_fresh_ws_retry(tmp_path: Path) -> None:
    sse = FakeSseHTTPClient(SseResponse(status_code=200, body=sse_payload(completed_events("sse"))))
    ws = FakeWebSocketClient([
        [
            {"type": "error", "code": "websocket_connection_limit_reached"},
        ],
        completed_events("ws retry ok"),
    ])
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=sse,
        websocket_client=ws,
        transport="auto",
        retry_policy=RetryPolicy(max_attempts=1),
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "ws retry ok"
    assert len(ws.requests) == 2
    assert sse.requests == []


def test_post_event_websocket_failure_does_not_fallback(tmp_path: Path) -> None:
    def failing_events():
        yield {"type": "response.output_text.delta", "delta": "partial"}
        raise OpenAICodexStreamInterruptedError(
            "OpenAI Codex stream was interrupted before completion.",
            metadata={"phase": "stream", "retryable": True, "transport": "websocket"},
        )

    sse = FakeSseHTTPClient(SseResponse(status_code=200, body=sse_payload(completed_events("sse"))))
    ws = FakeWebSocketClient([failing_events()])
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=sse,
        websocket_client=ws,
        transport="websocket",
        retry_policy=RetryPolicy(max_attempts=1),
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert result.metadata is not None
    assert result.metadata["progress"] == "event"
    assert result.metadata["transport"] == "websocket"
    assert sse.requests == []


class _FakeRawWebSocket:
    def __init__(self, messages: list[object]) -> None:
        self.messages = messages
        self.sent: list[str] = []
        self.closed = False

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self, *, timeout: float | None = None) -> object:
        del timeout
        if not self.messages:
            raise StopIteration
        item = self.messages.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self) -> None:
        self.closed = True


def test_websockets_sync_client_sends_response_create_and_decodes_raw_json(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _FakeRawWebSocket([
        json.dumps({"type": "response.output_text.delta", "delta": "hi"}),
        json.dumps({"type": "response.completed", "response": {"status": "completed"}}),
    ])
    observed: dict[str, Any] = {}

    def fake_connect(url: str, **kwargs: Any) -> _FakeRawWebSocket:
        observed["url"] = url
        observed.update(kwargs)
        return raw

    monkeypatch.setattr("websockets.sync.client.connect", fake_connect)
    from pipy_harness.native.openai_codex_provider import WebsocketsSyncClient

    events = WebsocketsSyncClient().post_events(
        "wss://example.test/responses",
        headers={"OpenAI-Beta": "responses_websockets=2026-02-06"},
        body={"model": "gpt-test"},
        connect_timeout_seconds=None,
        idle_timeout_seconds=12.0,
    )

    assert list(events) == [
        {"type": "response.output_text.delta", "delta": "hi"},
        {"type": "response.completed", "response": {"status": "completed"}},
    ]
    assert json.loads(raw.sent[0]) == {"type": "response.create", "model": "gpt-test"}
    assert raw.closed is True
    assert observed["url"] == "wss://example.test/responses"
    assert observed["open_timeout"] is None
    assert observed["ping_interval"] is None


def test_repeated_websocket_connection_limit_retries_once_then_falls_back(tmp_path: Path) -> None:
    sse = FakeSseHTTPClient(SseResponse(status_code=200, body=sse_payload(completed_events("sse after limit"))))
    ws = FakeWebSocketClient([
        [{"type": "error", "code": "websocket_connection_limit_reached"}],
        [{"type": "error", "code": "websocket_connection_limit_reached"}],
    ])
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=sse,
        websocket_client=ws,
        transport="websocket",
        retry_policy=RetryPolicy(max_attempts=1),
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "sse after limit"
    assert len(ws.requests) == 2
    assert len(sse.requests) == 1


def test_websockets_sync_client_treats_normal_close_as_clean_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    from websockets.exceptions import ConnectionClosedOK
    from websockets.frames import Close

    raw = _FakeRawWebSocket([
        json.dumps({"type": "response.completed", "response": {"status": "completed"}}),
        ConnectionClosedOK(Close(1000, ""), Close(1000, ""), True),
    ])

    monkeypatch.setattr("websockets.sync.client.connect", lambda *_args, **_kwargs: raw)
    from pipy_harness.native.openai_codex_provider import WebsocketsSyncClient

    events = WebsocketsSyncClient().post_events(
        "wss://example.test/responses",
        headers={},
        body={"model": "gpt-test"},
        connect_timeout_seconds=1.0,
        idle_timeout_seconds=1.0,
    )

    assert list(events) == [
        {"type": "response.completed", "response": {"status": "completed"}}
    ]


def test_pre_event_websocket_stream_interruption_falls_back_to_sse(tmp_path: Path) -> None:
    sse = FakeSseHTTPClient(
        SseResponse(status_code=200, body=sse_payload(completed_events("sse after close")))
    )
    ws = FakeWebSocketClient(
        [
            OpenAICodexStreamInterruptedError(
                "OpenAI Codex stream was interrupted before completion.",
                metadata={
                    "phase": "stream",
                    "retryable": True,
                    "transport": "websocket",
                },
            )
        ]
    )
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=sse,
        websocket_client=ws,
        transport="websocket",
        retry_policy=RetryPolicy(max_attempts=1),
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "sse after close"
    assert len(ws.requests) == 1
    assert len(sse.requests) == 1


def test_websockets_sync_client_cancel_token_closes_live_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _FakeRawWebSocket([])
    token = CancelToken()

    def fake_connect(*_args: object, **_kwargs: object) -> _FakeRawWebSocket:
        token.cancel()
        return raw

    monkeypatch.setattr("websockets.sync.client.connect", fake_connect)
    from pipy_harness.native.openai_codex_provider import WebsocketsSyncClient

    events = WebsocketsSyncClient().post_events(
        "wss://example.test/responses",
        headers={},
        body={"model": "gpt-test"},
        connect_timeout_seconds=1.0,
        idle_timeout_seconds=1.0,
        cancel_token=token,
    )

    with pytest.raises(ProviderCancelledError):
        list(events)

    assert raw.closed is True


def test_pre_event_websocket_protocol_error_does_not_fallback(tmp_path: Path) -> None:
    sse = FakeSseHTTPClient(SseResponse(status_code=200, body=sse_payload(completed_events("sse"))))
    ws = FakeWebSocketClient([
        [
            {"type": "error", "code": "authentication_error"},
        ]
    ])
    provider = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        auth_manager=auth_manager_with(credentials()),
        http_client=sse,
        websocket_client=ws,
        transport="websocket",
        retry_policy=RetryPolicy(max_attempts=1),
    )

    result = provider.complete(provider_request(tmp_path))

    assert result.status == HarnessStatus.FAILED
    assert sse.requests == []


def test_websockets_sync_client_normalizes_raw_handshake_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from websockets.exceptions import InvalidHandshake

    def fake_connect(*_args: object, **_kwargs: object) -> _FakeRawWebSocket:
        raise InvalidHandshake("PRIVATE_HANDSHAKE_TEXT")

    monkeypatch.setattr("websockets.sync.client.connect", fake_connect)
    from pipy_harness.native.openai_codex_provider import WebsocketsSyncClient

    with pytest.raises(OpenAICodexTransportError) as raised:
        WebsocketsSyncClient().post_events(
            "wss://example.test/responses",
            headers={},
            body={"model": "gpt-test"},
            connect_timeout_seconds=1.0,
            idle_timeout_seconds=1.0,
        )

    assert raised.value.metadata == {
        "phase": "headers",
        "retryable": True,
        "transport": "websocket",
    }
    assert "PRIVATE_HANDSHAKE_TEXT" not in str(raised.value)
