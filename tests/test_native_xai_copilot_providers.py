"""xai and github-copilot providers (backlog PR1/PR2, Pi ``4df157433``).

Covers the catalog rows and registry, the Responses/Anthropic/Chat Completions
request shapes Pi sends for both providers, the Copilot device-code OAuth flow
against a fake transport, the per-request credential cache, and the REPL
``/login``/``/logout`` path. No network is touched.
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.auth_store import AuthStore, env_api_key
from pipy_harness.native.catalog import build_builtin_catalog
from pipy_harness.native.catalog_state import ProviderCatalogState
from pipy_harness.native.http import JsonResponse
from pipy_harness.native.image_attachment import ProviderImageAttachment
from pipy_harness.native.oauth_providers import (
    GitHubCopilotOAuthProvider,
    OAuthCredentialCache,
    OAuthError,
    normalize_copilot_domain,
    parse_copilot_model_catalog,
)
from pipy_harness.native.provider_construction import (
    ConstructionOptions,
    PerRequestOAuthProvider,
    build_provider,
    resolve_construction,
)
from pipy_harness.native.provider_registry import (
    DEFAULT_NATIVE_MODELS,
    native_provider_available,
    native_provider_spec,
)
from pipy_harness.native.repl_state import (
    ModelRuntime,
    NativeModelSelection,
    NativeReplProviderState,
)

COPILOT = "github-copilot"
COPILOT_TOKEN = "tid=1;exp=2;proxy-ep=proxy.business.githubcopilot.com;st=x"
COPILOT_BASE = "https://api.business.githubcopilot.com"
STATIC_HEADERS = {
    "User-Agent": "GitHubCopilotChat/0.35.0",
    "Editor-Version": "vscode/1.107.0",
    "Editor-Plugin-Version": "copilot-chat/0.35.0",
    "Copilot-Integration-Id": "vscode-chat",
}
NOW_MS = 1_800_000_000_000


class CapturingHTTP:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

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
        return JsonResponse(status_code=500, body={"error": {"message": "stop"}})


class FakeTransport:
    """Scripted OAuth transport: each URL substring maps to queued replies."""

    def __init__(self, routes: dict[str, list[tuple[int, object]]]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, method, url, *, headers=None, data=None):
        self.calls.append((method, url, {"headers": dict(headers or {}), "data": data}))
        for key, replies in self.routes.items():
            if key in url:
                status, body = replies[0] if len(replies) == 1 else replies.pop(0)
                return status, body if isinstance(body, str) else json.dumps(body)
        return 404, ""

    def urls(self) -> list[str]:
        return [url for _method, url, _meta in self.calls]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


def _request(tmp_path: Path, **kwargs: Any) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYS",
        user_prompt="hello",
        provider_name="p",
        model_id="m",
        cwd=tmp_path,
        **kwargs,
    )


def _image() -> ProviderImageAttachment:
    return ProviderImageAttachment(
        media_type="image/png",
        data_base64="aGVsbG8=",
        byte_count=5,
        sha256="0" * 64,
        source_label="shot.png",
    )


def _tool_turn() -> tuple[Any, ...]:
    return (
        AgentUserMessage(content=ProductContent("read it")),
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(
                AgentToolCall(
                    provider_correlation_id="call_1",
                    tool_name="read_file",
                    arguments_json=ProductContent('{"path":"a"}'),
                ),
            ),
        ),
        AgentToolResultMessage(
            tool_request_id="pipy-tool-0001",
            tool_name="read_file",
            content=ProductContent("contents"),
            provider_correlation_id="call_1",
        ),
    )


def _copilot_credential(**overrides: Any) -> dict[str, object]:
    credential: dict[str, object] = {
        "type": "oauth",
        "access": COPILOT_TOKEN,
        "refresh": "gh-token",
        "expires": NOW_MS + 60 * 60 * 1000,
    }
    credential.update(overrides)
    return credential


def _send(
    tmp_path: Path,
    provider: str,
    model_id: str,
    level: str | None,
    *,
    env: Mapping[str, str] | None = None,
    oauth_credential: Mapping[str, object] | None = None,
    runtime_api_key: str | None = None,
    **request_kwargs: Any,
) -> dict[str, Any]:
    spec = build_builtin_catalog().find(provider, model_id)
    assert spec is not None
    resolved = resolve_construction(
        spec,
        store=AuthStore(path=tmp_path / "auth.json"),
        env=env or {},
        runtime_api_key=runtime_api_key,
        models_json_auth=None,
        thinking_level=level,
        oauth_credential=oauth_credential,
    )
    assert resolved.ok, resolved.error
    http = CapturingHTTP()
    build_provider(resolved, spec=spec, http_client=http).complete(
        _request(tmp_path, **request_kwargs)
    )
    return http.requests[-1]


# ---- catalog + registry -------------------------------------------------------


def test_catalog_carries_pi_rows_for_both_providers() -> None:
    catalog = build_builtin_catalog()
    xai = catalog.models_for("xai")
    assert [row.model_id for row in xai] == [
        "grok-4.3",
        "grok-4.5",
        "grok-4.6",
        "grok-4.7",
    ]
    assert {row.api for row in xai} == {"openai-responses"}
    assert {row.base_url for row in xai} == {"https://api.x.ai/v1"}

    copilot = catalog.models_for(COPILOT)
    by_api: dict[str, int] = {}
    for row in copilot:
        by_api[row.api] = by_api.get(row.api, 0) + 1
        assert row.headers == STATIC_HEADERS
        assert row.base_url == "https://api.individual.githubcopilot.com"
    assert by_api == {
        "anthropic-messages": 10,
        "openai-completions": 6,
        "openai-responses": 17,
    }


def test_registry_defaults_and_env_auth() -> None:
    assert DEFAULT_NATIVE_MODELS["xai"] == "grok-4.7"
    assert DEFAULT_NATIVE_MODELS[COPILOT] == "gpt-5.4"
    for provider in ("xai", COPILOT):
        spec = native_provider_spec(provider)
        assert spec is not None and spec.supports_tool_calls
        assert build_builtin_catalog().find(provider, spec.default_model) is not None
    assert env_api_key("xai", {"XAI_API_KEY": "xk"}) == "xk"
    assert native_provider_available(
        "xai", env={"XAI_API_KEY": "xk"}, openai_codex_credentials_exist=False
    )
    assert native_provider_available(
        COPILOT,
        env={"COPILOT_GITHUB_TOKEN": "t"},
        openai_codex_credentials_exist=False,
    )
    assert not native_provider_available(
        COPILOT, env={}, openai_codex_credentials_exist=False
    )


# ---- xai request shape -----------------------------------------------------------


def test_xai_on_state_sends_summary_and_encrypted_include(tmp_path: Path) -> None:
    sent = _send(tmp_path, "xai", "grok-4.7", "high", env={"XAI_API_KEY": "xk"})
    assert sent["url"] == "https://api.x.ai/v1/responses"
    assert sent["headers"]["Authorization"] == "Bearer xk"
    assert sent["body"]["reasoning"] == {"effort": "high", "summary": "auto"}
    assert sent["body"]["include"] == ["reasoning.encrypted_content"]
    # ``supportsLongCacheRetention: false`` and no Copilot headers for xai.
    assert "X-Initiator" not in sent["headers"]


@pytest.mark.parametrize(
    ("model_id", "reasoning"),
    [
        # grok-4.3 maps off to "none"; 4.7 marks off unsupported (null).
        ("grok-4.3", {"effort": "none"}),
        ("grok-4.7", None),
    ],
)
def test_xai_off_state_still_requests_encrypted_reasoning(
    tmp_path: Path, model_id: str, reasoning: dict[str, str] | None
) -> None:
    sent = _send(tmp_path, "xai", model_id, "off", env={"XAI_API_KEY": "xk"})
    assert sent["body"].get("reasoning") == reasoning
    # Pi: ``if (model.provider === "xai") params.include = [...]``.
    assert sent["body"]["include"] == ["reasoning.encrypted_content"]


def test_openai_off_state_has_no_include(tmp_path: Path) -> None:
    sent = _send(tmp_path, "openai", "gpt-6-sol", "off", env={"OPENAI_API_KEY": "k"})
    assert sent["body"]["reasoning"] == {"effort": "none"}
    assert "include" not in sent["body"]


# ---- Copilot request shape --------------------------------------------------------


def test_copilot_responses_uses_token_base_url_and_headers(tmp_path: Path) -> None:
    sent = _send(
        tmp_path,
        COPILOT,
        "gpt-5.4",
        "high",
        oauth_credential=_copilot_credential(),
    )
    assert sent["url"] == f"{COPILOT_BASE}/responses"
    headers = sent["headers"]
    assert headers["Authorization"] == f"Bearer {COPILOT_TOKEN}"
    for name, value in STATIC_HEADERS.items():
        assert headers[name] == value
    assert headers["X-Initiator"] == "user"
    assert headers["Openai-Intent"] == "conversation-edits"
    assert "Copilot-Vision-Request" not in headers
    assert sent["body"]["reasoning"] == {"effort": "high", "summary": "auto"}
    assert sent["body"]["include"] == ["reasoning.encrypted_content"]


def test_copilot_responses_skips_off_state_even_with_string_off(
    tmp_path: Path,
) -> None:
    # gpt-6-sol maps off to "none", but Pi sends no off-state for Copilot.
    spec = build_builtin_catalog().find(COPILOT, "gpt-6-sol")
    assert spec is not None and spec.thinking_level_map["off"] == "none"
    sent = _send(
        tmp_path, COPILOT, "gpt-6-sol", "off", oauth_credential=_copilot_credential()
    )
    assert "reasoning" not in sent["body"]
    assert "include" not in sent["body"]


def test_copilot_responses_vision_and_agent_initiator(tmp_path: Path) -> None:
    sent = _send(
        tmp_path,
        COPILOT,
        "gpt-5.4",
        "high",
        oauth_credential=_copilot_credential(),
        messages=_tool_turn(),
        attachments=(_image(),),
    )
    assert sent["headers"]["X-Initiator"] == "agent"
    assert sent["headers"]["Copilot-Vision-Request"] == "true"


def test_copilot_anthropic_uses_bearer_not_api_key(tmp_path: Path) -> None:
    sent = _send(
        tmp_path,
        COPILOT,
        "claude-sonnet-5",
        "high",
        oauth_credential=_copilot_credential(),
        messages=(AgentUserMessage(content=ProductContent("hi")),),
        attachments=(_image(),),
        session_id="session-1",
    )
    assert sent["url"] == f"{COPILOT_BASE}/v1/messages"
    headers = sent["headers"]
    assert headers["Authorization"] == f"Bearer {COPILOT_TOKEN}"
    assert "x-api-key" not in headers
    assert headers["X-Initiator"] == "user"
    assert headers["Openai-Intent"] == "conversation-edits"
    assert headers["Copilot-Vision-Request"] == "true"
    assert headers["Copilot-Integration-Id"] == "vscode-chat"
    assert "x-session-affinity" not in headers and "x-session-id" not in headers


def test_copilot_completions_sends_no_reasoning_effort(tmp_path: Path) -> None:
    sent = _send(
        tmp_path,
        COPILOT,
        "gemini-3.5-flash",
        "high",
        oauth_credential=_copilot_credential(),
        messages=_tool_turn(),
        attachments=(_image(),),
    )
    assert sent["url"] == f"{COPILOT_BASE}/chat/completions"
    # ``compat.supportsReasoningEffort: false`` gates Pi's default branch.
    assert "reasoning_effort" not in sent["body"]
    assert sent["headers"]["X-Initiator"] == "agent"
    # The Chat Completions adapter sends no images, so no vision header.
    assert "Copilot-Vision-Request" not in sent["headers"]
    assert sent["headers"]["Authorization"] == f"Bearer {COPILOT_TOKEN}"


def test_completions_default_branch_honours_supports_reasoning_effort(
    tmp_path: Path,
) -> None:
    spec = build_builtin_catalog().find("openai-completions", "gpt-5.5")
    assert spec is not None
    for compat, expected in ((None, True), ({"supportsReasoningEffort": False}, False)):
        row = replace(spec, compat=compat)
        resolved = resolve_construction(
            row,
            store=AuthStore(path=tmp_path / "auth.json"),
            env={"OPENAI_API_KEY": "k"},
            runtime_api_key=None,
            models_json_auth=None,
            thinking_level="high",
        )
        assert (resolved.reasoning_effort is not None) is expected


def test_copilot_env_token_uses_default_base_url(tmp_path: Path) -> None:
    sent = _send(
        tmp_path,
        COPILOT,
        "gpt-5.4",
        "high",
        env={"COPILOT_GITHUB_TOKEN": "env-token"},
    )
    assert sent["url"] == "https://api.individual.githubcopilot.com/responses"
    assert sent["headers"]["Authorization"] == "Bearer env-token"


def test_runtime_api_key_overrides_copilot_oauth(tmp_path: Path) -> None:
    sent = _send(
        tmp_path,
        COPILOT,
        "gpt-5.4",
        "high",
        oauth_credential=_copilot_credential(),
        runtime_api_key="runtime-key",
    )
    assert sent["url"] == "https://api.individual.githubcopilot.com/responses"
    assert sent["headers"]["Authorization"] == "Bearer runtime-key"


# ---- Copilot OAuth: device flow, models, policies ----------------------------------


def _login_transport(
    *,
    device: Mapping[str, object] | None = None,
    polls: list[tuple[int, object]] | None = None,
    models: object | None = None,
    policy: list[tuple[int, object]] | None = None,
) -> FakeTransport:
    return FakeTransport(
        {
            "/login/device/code": [
                (
                    200,
                    device
                    or {
                        "device_code": "dev",
                        "user_code": "ABCD-1234",
                        "verification_uri": "https://github.com/login/device",
                        "interval": 5,
                        "expires_in": 900,
                    },
                )
            ],
            "/login/oauth/access_token": polls or [(200, {"access_token": "gh-token"})],
            "copilot_internal/v2/token": [
                (200, {"token": COPILOT_TOKEN, "expires_at": 1_900_000_000})
            ],
            "/policy": policy or [(200, {})],
            "/models": [
                (
                    200,
                    models
                    or {
                        "data": [
                            {"id": "gpt-5.4", "model_picker_enabled": True},
                            {
                                "id": "claude-sonnet-5",
                                "model_picker_enabled": True,
                                "policy": {"state": "unconfigured"},
                            },
                        ]
                    },
                )
            ],
        }
    )


def _provider(transport: FakeTransport, clock: FakeClock) -> GitHubCopilotOAuthProvider:
    return GitHubCopilotOAuthProvider(
        transport=transport, sleep=clock.sleep, monotonic=clock.monotonic
    )


def test_copilot_login_device_flow_success() -> None:
    clock = FakeClock()
    transport = _login_transport(
        polls=[
            (200, {"error": "authorization_pending"}),
            (200, {"error": "slow_down", "interval": 7}),
            (200, {"access_token": "gh-token"}),
        ]
    )
    prompts: list[Mapping[str, object]] = []
    events: list[Mapping[str, object]] = []

    def prompt(message: Mapping[str, object]) -> str:
        prompts.append(message)
        return ""

    credentials = _provider(transport, clock).login(prompt=prompt, notify=events.append)

    assert (
        prompts[0]["message"] == "GitHub Enterprise URL/domain (blank for github.com)"
    )
    assert events[0]["type"] == "device_code"
    assert events[0]["userCode"] == "ABCD-1234"
    assert events[1] == {"type": "progress", "message": "Enabling models..."}
    # Wait one interval before the first poll, keep it on pending, then use
    # the server's slow_down interval.
    assert clock.sleeps == [5.0, 5.0, 7.0]
    urls = transport.urls()
    assert urls[0] == "https://github.com/login/device/code"
    assert urls[4] == "https://api.github.com/copilot_internal/v2/token"
    assert urls[5] == f"{COPILOT_BASE}/models"
    assert urls[6] == f"{COPILOT_BASE}/models/claude-sonnet-5/policy"
    device_form = transport.calls[0][2]["data"]
    assert "scope=read%3Auser" in device_form
    poll_form = transport.calls[1][2]["data"]
    assert "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Adevice_code" in (
        poll_form
    )
    assert credentials == {
        "type": "oauth",
        "refresh": "gh-token",
        "access": COPILOT_TOKEN,
        "expires": 1_900_000_000 * 1000 - 5 * 60 * 1000,
        "availableModelIds": ["gpt-5.4", "claude-sonnet-5"],
    }


def test_copilot_login_enterprise_domain() -> None:
    clock = FakeClock()
    transport = _login_transport(models={"data": []})
    credentials = _provider(transport, clock).login(
        prompt=lambda _p: "https://ghe.acme.io/some/path", notify=lambda _e: None
    )
    urls = transport.urls()
    assert urls[0] == "https://ghe.acme.io/login/device/code"
    assert "https://ghe.acme.io/login/oauth/access_token" in urls
    assert "https://api.ghe.acme.io/copilot_internal/v2/token" in urls
    assert credentials["enterpriseUrl"] == "ghe.acme.io"
    assert credentials["availableModelIds"] == []


def test_normalize_copilot_domain() -> None:
    assert normalize_copilot_domain("company.ghe.com") == "company.ghe.com"
    assert normalize_copilot_domain(" https://Company.ghe.com/x ") == "company.ghe.com"
    assert normalize_copilot_domain("") is None
    assert normalize_copilot_domain("http://") is None


def test_copilot_login_rejects_invalid_enterprise_domain() -> None:
    with pytest.raises(OAuthError, match="Invalid GitHub Enterprise"):
        _provider(_login_transport(), FakeClock()).login(
            prompt=lambda _p: "http://", notify=lambda _e: None
        )


def test_copilot_login_device_flow_failure_message() -> None:
    transport = _login_transport(
        polls=[(200, {"error": "access_denied", "error_description": "nope"})]
    )
    with pytest.raises(OAuthError, match="Device flow failed: access_denied: nope"):
        _provider(transport, FakeClock()).login(
            prompt=lambda _p: "", notify=lambda _e: None
        )


@pytest.mark.parametrize(
    ("poll", "message"),
    [
        ({"error": "authorization_pending"}, "Device flow timed out"),
        ({"error": "slow_down"}, "clock drift"),
    ],
)
def test_copilot_login_times_out(poll: dict[str, str], message: str) -> None:
    transport = _login_transport(
        device={
            "device_code": "dev",
            "user_code": "X",
            "verification_uri": "https://github.com/login/device",
            "interval": 5,
            "expires_in": 12,
        },
        polls=[(200, poll)],
    )
    clock = FakeClock()
    with pytest.raises(OAuthError, match=message):
        _provider(transport, clock).login(prompt=lambda _p: "", notify=lambda _e: None)
    assert sum(clock.sleeps) == pytest.approx(12.0)


def test_copilot_login_rejects_untrusted_verification_uri() -> None:
    transport = _login_transport(
        device={
            "device_code": "dev",
            "user_code": "X",
            "verification_uri": "javascript:alert(1)",
            "expires_in": 900,
        }
    )
    with pytest.raises(OAuthError, match="Untrusted verification_uri"):
        _provider(transport, FakeClock()).login(
            prompt=lambda _p: "", notify=lambda _e: None
        )


def test_parse_copilot_model_catalog_matches_pi() -> None:
    raw = {
        "data": [
            {"id": "gpt-5.4", "model_picker_enabled": True},
            {
                "id": "grok-4.7",
                "model_picker_enabled": True,
                "policy": {"state": "disabled"},
            },
            {
                "id": "gpt-5-mini",
                "model_picker_enabled": True,
                "capabilities": {"supports": {"tool_calls": False}},
            },
            {
                "id": "claude-opus-5",
                "model_picker_enabled": True,
                "policy": {"state": "unconfigured"},
            },
            {
                "id": "not-in-pi-catalog",
                "model_picker_enabled": True,
                "policy": {"state": "unconfigured"},
            },
        ]
    }
    available, policy = parse_copilot_model_catalog(raw, allow_policy_fallback=True)
    assert available == ["gpt-5.4", "claude-opus-5", "not-in-pi-catalog"]
    assert policy == ["claude-opus-5"]

    # No picker models: the Individual endpoint falls back to enabled policies.
    fallback = {
        "data": [
            {"id": "gpt-5.4", "policy": {"state": "enabled"}},
            {"id": "grok-4.7", "policy": {"state": "unconfigured"}},
        ]
    }
    assert parse_copilot_model_catalog(fallback, allow_policy_fallback=True) == (
        ["gpt-5.4"],
        ["grok-4.7"],
    )
    assert parse_copilot_model_catalog(fallback, allow_policy_fallback=False) == (
        [],
        [],
    )
    with pytest.raises(OAuthError):
        parse_copilot_model_catalog({"data": None}, allow_policy_fallback=True)


def test_enable_models_stops_after_exhausted_rate_limit() -> None:
    transport = FakeTransport({"/policy": [(429, "")]})
    clock = FakeClock()
    enabled = _provider(transport, clock).enable_models(
        COPILOT_TOKEN, ["claude-opus-5", "grok-4.7"], None
    )
    assert enabled == []
    # Two retries (0.5 s, 1 s) on the first model, then the batch stops.
    assert clock.sleeps == [0.5, 1.0]
    assert all("claude-opus-5" in url for url in transport.urls())


# ---- per-request credential cache ------------------------------------------------


class _RefreshingProvider:
    id = "fake-oauth"

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[Mapping[str, object]] = []
        self.fail = fail

    def refresh_token(self, credentials: Mapping[str, object]) -> dict[str, object]:
        self.calls.append(dict(credentials))
        if self.fail:
            raise RuntimeError("boom secret-token")
        return {
            **credentials,
            "access": f"new-{len(self.calls)}",
            "expires": NOW_MS * 2,
        }

    def get_api_key(self, credentials: Mapping[str, object]) -> str:
        return str(credentials["access"])


def test_cache_refreshes_inside_five_minute_window_only() -> None:
    fake = _RefreshingProvider()
    cache = OAuthCredentialCache(providers=lambda _id: fake, now_ms=lambda: NOW_MS)
    valid = _copilot_credential(expires=NOW_MS + 6 * 60 * 1000)
    assert cache.fresh(COPILOT, valid)["access"] == COPILOT_TOKEN
    assert fake.calls == []

    expiring = _copilot_credential(expires=NOW_MS + 4 * 60 * 1000)
    assert cache.fresh(COPILOT, expiring)["access"] == "new-1"
    # The refreshed credential is reused for the same refresh token.
    assert cache.fresh(COPILOT, expiring)["access"] == "new-1"
    assert cache.current(COPILOT, expiring)["access"] == "new-1"
    assert len(fake.calls) == 1
    # A different login (refresh token) never sees the cached credential.
    other = _copilot_credential(refresh="other", expires=NOW_MS + 60 * 60 * 1000)
    assert cache.current(COPILOT, other)["access"] == COPILOT_TOKEN


def test_cache_refresh_failure_is_a_safe_oauth_error() -> None:
    cache = OAuthCredentialCache(
        providers=lambda _id: _RefreshingProvider(fail=True), now_ms=lambda: NOW_MS
    )
    with pytest.raises(OAuthError) as excinfo:
        cache.fresh(COPILOT, _copilot_credential(expires=0))
    assert "secret-token" not in str(excinfo.value)


# ---- ModelRuntime: per-request OAuth provider --------------------------------------


def _catalog_state(
    tmp_path: Path, store: AuthStore, models_json: dict[str, Any] | None = None
) -> ProviderCatalogState:
    models_path = tmp_path / "models.json"
    if models_json is not None:
        models_path.write_text(json.dumps(models_json), encoding="utf-8")
    return ProviderCatalogState(
        models_json_path=models_path,
        auth_store=store,
        env={},
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )


def _refresh_transport(token: str) -> FakeTransport:
    return FakeTransport(
        {
            "copilot_internal/v2/token": [
                (200, {"token": token, "expires_at": 4_000_000_000})
            ],
            "/models": [
                (200, {"data": [{"id": "gpt-5.4", "model_picker_enabled": True}]})
            ],
        }
    )


def test_model_runtime_refreshes_copilot_token_per_request(tmp_path: Path) -> None:
    store = AuthStore(path=tmp_path / "auth.json")
    store.set(COPILOT, _copilot_credential(access="stale-token", expires=0))
    before = (tmp_path / "auth.json").read_text(encoding="utf-8")
    state = _catalog_state(tmp_path, store)
    transport = _refresh_transport(COPILOT_TOKEN)
    state.oauth_credentials = OAuthCredentialCache(
        providers=lambda _id: GitHubCopilotOAuthProvider(transport=transport)
    )

    provider = ModelRuntime(catalog=state).construct(
        NativeModelSelection(COPILOT, "gpt-5.4"),
        thinking_level="high",
        options=ConstructionOptions(),
    )
    assert isinstance(provider, PerRequestOAuthProvider)
    # Binding does not refresh; the first request does.
    assert transport.calls == []
    http = CapturingHTTP()
    provider = replace(provider, http_client=http)
    provider.complete(_request(tmp_path))
    provider.complete(_request(tmp_path))

    assert [url for url in transport.urls() if "v2/token" in url] == [
        "https://api.github.com/copilot_internal/v2/token"
    ]
    for sent in http.requests:
        assert sent["url"] == f"{COPILOT_BASE}/responses"
        assert sent["headers"]["Authorization"] == f"Bearer {COPILOT_TOKEN}"
    # The refreshed token stays in memory; the auth file is untouched.
    assert (tmp_path / "auth.json").read_text(encoding="utf-8") == before
    # The refreshed model list drives availability.
    assert [
        row.model_id for row in state.get_available() if row.provider_name == COPILOT
    ] == ["gpt-5.4"]


def test_auth_header_is_regenerated_from_the_fresh_token(tmp_path: Path) -> None:
    store = AuthStore(path=tmp_path / "auth.json")
    store.set(COPILOT, _copilot_credential(access="stale-token", expires=0))
    state = _catalog_state(
        tmp_path,
        store,
        {"providers": {COPILOT: {"authHeader": True, "headers": {"X-Team": "t1"}}}},
    )
    assert state.error is None
    transport = _refresh_transport(COPILOT_TOKEN)
    state.oauth_credentials = OAuthCredentialCache(
        providers=lambda _id: GitHubCopilotOAuthProvider(transport=transport)
    )
    provider = ModelRuntime(catalog=state).construct(
        NativeModelSelection(COPILOT, "gpt-5.4"),
        thinking_level="high",
        options=ConstructionOptions(),
    )
    assert isinstance(provider, PerRequestOAuthProvider)
    assert provider.auth_header is True
    assert provider.resolved.headers["Authorization"] == "Bearer stale-token"
    http = CapturingHTTP()
    replace(provider, http_client=http).complete(_request(tmp_path))
    assert http.requests[-1]["headers"]["Authorization"] == f"Bearer {COPILOT_TOKEN}"
    # Token-independent resolved headers are kept.
    assert http.requests[-1]["headers"]["X-Team"] == "t1"


def test_refresh_failure_fails_the_request_safely(tmp_path: Path) -> None:
    store = AuthStore(path=tmp_path / "auth.json")
    store.set(COPILOT, _copilot_credential(expires=0))
    state = _catalog_state(tmp_path, store)
    transport = FakeTransport({"copilot_internal/v2/token": [(401, "denied")]})
    state.oauth_credentials = OAuthCredentialCache(
        providers=lambda _id: GitHubCopilotOAuthProvider(transport=transport)
    )
    provider = ModelRuntime(catalog=state).construct(
        NativeModelSelection(COPILOT, "gpt-5.4"),
        thinking_level=None,
        options=ConstructionOptions(),
    )
    assert isinstance(provider, PerRequestOAuthProvider)
    http = CapturingHTTP()
    result = replace(provider, http_client=http).complete(_request(tmp_path))
    assert result.status is HarnessStatus.FAILED
    assert http.requests == []
    assert result.error_message == "OAuth refresh failed for github-copilot."
    assert "gh-token" not in json.dumps(result.metadata, default=str)


# ---- REPL /login and /logout ------------------------------------------------------------


class _StubCopilotLogin:
    def login(self, *, prompt, notify):
        domain = prompt(
            {"message": "GitHub Enterprise URL/domain (blank for github.com)"}
        )
        assert domain == ""
        notify(
            {
                "type": "device_code",
                "userCode": "WXYZ-0001",
                "verificationUri": "https://github.com/login/device",
            }
        )
        notify({"type": "progress", "message": "Enabling models..."})
        return _copilot_credential(availableModelIds=["gpt-5.4"])


def test_repl_login_and_logout_github_copilot(tmp_path: Path) -> None:
    store = AuthStore(path=tmp_path / "auth.json")
    state = _catalog_state(tmp_path, store)
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        model_runtime=ModelRuntime(catalog=state),
        persist_defaults=False,
        copilot_oauth_factory=_StubCopilotLogin,  # type: ignore[arg-type]
    )
    assert state.availability_reason(COPILOT) == "login-required"

    out = io.StringIO()
    ok, message = repl_state.login(
        COPILOT, input_stream=io.StringIO("\n"), output_stream=out
    )
    assert ok, message
    assert message == "pipy: github-copilot OAuth login stored."
    shown = out.getvalue()
    assert "https://github.com/login/device" in shown
    assert "WXYZ-0001" in shown
    assert "Enabling models..." in shown
    assert COPILOT_TOKEN not in shown
    assert store.get(COPILOT) == _copilot_credential(availableModelIds=["gpt-5.4"])
    assert state.provider_available(COPILOT)

    ok, message = repl_state.logout(COPILOT)
    assert ok and message == "pipy: github-copilot OAuth credentials removed."
    assert store.get(COPILOT) is None


def test_repl_login_failure_reports_provider(tmp_path: Path) -> None:
    class Failing:
        def login(self, *, prompt, notify):
            raise OAuthError("Device flow timed out")

    state = _catalog_state(tmp_path, AuthStore(path=tmp_path / "auth.json"))
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        model_runtime=ModelRuntime(catalog=state),
        persist_defaults=False,
        copilot_oauth_factory=Failing,  # type: ignore[arg-type]
    )
    ok, message = repl_state.login(
        COPILOT, input_stream=io.StringIO("\n"), output_stream=io.StringIO()
    )
    assert not ok
    assert message == "pipy: github-copilot login failed: Device flow timed out"


def test_repl_model_lists_and_selection_follow_copilot_account(
    tmp_path: Path,
) -> None:
    store = AuthStore(path=tmp_path / "auth.json")
    store.set(COPILOT, _copilot_credential(availableModelIds=["gpt-5.4"]))
    state = _catalog_state(tmp_path, store)
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        model_runtime=ModelRuntime(catalog=state),
        persist_defaults=False,
    )
    options = {
        option.selection.model_id: option
        for option in repl_state.model_options()
        if option.selection.provider_name == COPILOT
    }
    assert options["gpt-5.4"].available is True
    assert options["grok-4.7"].available is False
    assert options["grok-4.7"].reason == "not-in-account"

    ok, message = repl_state.select_model(f"{COPILOT}/grok-4.7")
    assert not ok
    assert "not available for this github-copilot account" in message
    ok, message = repl_state.select_model(f"{COPILOT}/gpt-5.4")
    assert ok, message
