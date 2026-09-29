"""Tests for the stdlib OAuth provider registry (M7).

HTTP is injected as a fake transport so no network is touched.
"""

from __future__ import annotations

import json

from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.oauth_providers import (
    AnthropicOAuthProvider,
    GitHubCopilotOAuthProvider,
    OpenAICodexOAuthProvider,
    copilot_base_url_from_token,
    get_oauth_provider,
    get_oauth_provider_ids,
)


class FakeTransport:
    def __init__(self, responses: dict[str, tuple[int, str]]):
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, method, url, *, headers=None, data=None):
        self.calls.append((method, url, {"headers": headers or {}, "data": data}))
        for key, value in self.responses.items():
            if key in url:
                return value
        return (404, "")


FIXED_NOW_MS = 1_000_000_000_000


def test_registry_lists_three_builtins():
    assert get_oauth_provider_ids() == [
        "anthropic",
        "github-copilot",
        "openai-codex",
    ]
    assert isinstance(get_oauth_provider("anthropic"), AnthropicOAuthProvider)
    assert get_oauth_provider("unknown") is None


def test_anthropic_refresh_applies_five_minute_margin():
    transport = FakeTransport(
        {
            "oauth/token": (
                200,
                json.dumps(
                    {
                        "access_token": "acc",
                        "refresh_token": "ref2",
                        "expires_in": 3600,
                    }
                ),
            )
        }
    )
    provider = AnthropicOAuthProvider(transport=transport, now_ms=lambda: FIXED_NOW_MS)
    cred = provider.refresh_token({"refresh": "ref"})
    assert cred["access"] == "acc"
    assert cred["refresh"] == "ref2"
    # 5-minute safety margin subtracted
    assert cred["expires"] == FIXED_NOW_MS + 3600 * 1000 - 5 * 60 * 1000


def test_anthropic_get_api_key_returns_access():
    provider = AnthropicOAuthProvider()
    assert provider.get_api_key({"type": "oauth", "access": "tok"}) == "tok"


def test_codex_refresh_has_no_margin():
    transport = FakeTransport(
        {
            "token": (
                200,
                json.dumps(
                    {"access_token": "a", "refresh_token": "r", "expires_in": 3600}
                ),
            )
        }
    )
    provider = OpenAICodexOAuthProvider(
        transport=transport, now_ms=lambda: FIXED_NOW_MS
    )
    cred = provider.refresh_token({"refresh": "r"})
    # No safety margin for Codex (Date.now() + expires_in*1000)
    assert cred["expires"] == FIXED_NOW_MS + 3600 * 1000


def test_copilot_base_url_from_proxy_ep_converts_proxy_to_api():
    token = "tid=abc;exp=123;proxy-ep=proxy.individual.githubcopilot.com;more=x"
    # Pi converts the proxy. host prefix to api.
    assert (
        copilot_base_url_from_token(token) == "https://api.individual.githubcopilot.com"
    )


def test_copilot_request_base_url_follows_pi_to_auth():
    # Pi ``toAuth``: token proxy-ep, else enterprise, else the default.
    provider = GitHubCopilotOAuthProvider()
    assert (
        provider.request_base_url({"access": "tid=x;proxy-ep=proxy.example.com;"})
        == "https://api.example.com"
    )
    assert (
        provider.request_base_url({"access": "opaque", "enterpriseUrl": "ghe.acme.io"})
        == "https://copilot-api.ghe.acme.io"
    )
    assert (
        provider.request_base_url({"access": "opaque"})
        == "https://api.individual.githubcopilot.com"
    )
    # Pi removed the load-time modifyModels rewrite for Copilot.
    assert not hasattr(provider, "modify_models")


def test_copilot_filter_models_keeps_available_ids_only():
    provider = GitHubCopilotOAuthProvider()
    rows = [
        NativeModelSpec(
            provider_name="github-copilot",
            model_id=model_id,
            display_name=model_id,
            api="openai-responses",
        )
        for model_id in ("gpt-5.4", "grok-4.7")
    ] + [
        NativeModelSpec(
            provider_name="anthropic",
            model_id="claude",
            display_name="claude",
            api="anthropic-messages",
        )
    ]
    cred = {"type": "oauth", "access": "a", "availableModelIds": ["gpt-5.4"]}
    kept = [r.reference for r in provider.filter_models(rows, cred)]
    assert kept == ["github-copilot/gpt-5.4", "anthropic/claude"]
    # No list (or a malformed one) leaves the rows alone, as in Pi.
    assert provider.filter_models(rows, {"type": "oauth"}) == rows
    assert (
        provider.filter_models(rows, {"type": "oauth", "availableModelIds": [1]})
        == rows
    )
    assert provider.filter_models(rows, None) == rows


def test_copilot_enable_model_hits_policy_endpoint():
    transport = FakeTransport({"/policy": (200, "{}")})
    provider = GitHubCopilotOAuthProvider(transport=transport)
    ok = provider.enable_model("tid=x;proxy-ep=proxy.example.com;", "gpt-5.4")
    assert ok is True
    method, url, meta = transport.calls[-1]
    assert url == "https://api.example.com/models/gpt-5.4/policy"
    assert json.loads(meta["data"])["state"] == "enabled"
    # Copilot editor headers are required on the policy call.
    assert meta["headers"]["Copilot-Integration-Id"] == "vscode-chat"


def test_copilot_refresh_uses_bearer_and_expires_at_with_margin():
    transport = FakeTransport(
        {
            "copilot_internal/v2/token": (
                200,
                json.dumps({"token": "ctok", "expires_at": 2000}),
            ),
            "/models": (
                200,
                json.dumps({"data": [{"id": "gpt-5.4", "model_picker_enabled": True}]}),
            ),
        }
    )
    provider = GitHubCopilotOAuthProvider(
        transport=transport, now_ms=lambda: FIXED_NOW_MS
    )
    cred = provider.refresh_token({"refresh": "gh-token"})
    assert cred["access"] == "ctok"
    # expires_at (seconds) * 1000 - 5min margin
    assert cred["expires"] == 2000 * 1000 - 5 * 60 * 1000
    # Pi's refresh re-lists the account's models.
    assert cred["availableModelIds"] == ["gpt-5.4"]
    _, url, meta = transport.calls[0]
    assert url == "https://api.github.com/copilot_internal/v2/token"
    assert meta["headers"]["Authorization"] == "Bearer gh-token"
    assert meta["headers"]["Copilot-Integration-Id"] == "vscode-chat"
    _, url, meta = transport.calls[1]
    assert url == "https://api.individual.githubcopilot.com/models"
    assert meta["headers"]["Authorization"] == "Bearer ctok"
    assert meta["headers"]["X-GitHub-Api-Version"] == "2026-06-01"


def test_credentials_never_serialize_authorization_url(tmp_path):
    # The auth store entry holds only token material; refresh returns a plain
    # dict with access/refresh/expires and nothing resembling an auth URL.
    transport = FakeTransport(
        {
            "oauth/token": (
                200,
                json.dumps(
                    {"access_token": "a", "refresh_token": "r", "expires_in": 10}
                ),
            )
        }
    )
    provider = AnthropicOAuthProvider(transport=transport, now_ms=lambda: FIXED_NOW_MS)
    cred = provider.refresh_token({"refresh": "r"})
    assert set(cred.keys()) <= {"type", "access", "refresh", "expires"}
