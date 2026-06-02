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
    ids = set(get_oauth_provider_ids())
    assert {"anthropic", "github-copilot", "openai-codex"} <= ids
    assert isinstance(get_oauth_provider("anthropic"), AnthropicOAuthProvider)


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
    provider = OpenAICodexOAuthProvider(transport=transport, now_ms=lambda: FIXED_NOW_MS)
    cred = provider.refresh_token({"refresh": "r"})
    # No safety margin for Codex (Date.now() + expires_in*1000)
    assert cred["expires"] == FIXED_NOW_MS + 3600 * 1000


def test_copilot_base_url_from_proxy_ep():
    token = "tid=abc;exp=123;proxy-ep=proxy.individual.githubcopilot.com;more=x"
    assert (
        copilot_base_url_from_token(token)
        == "https://proxy.individual.githubcopilot.com"
    )


def test_copilot_modify_models_rewrites_base_url():
    provider = GitHubCopilotOAuthProvider()
    rows = [
        NativeModelSpec(
            provider_name="github-copilot",
            model_id="gpt-5.4",
            display_name="x",
            api="openai-completions",
            base_url="https://old",
        ),
        NativeModelSpec(
            provider_name="anthropic",
            model_id="claude",
            display_name="y",
            api="anthropic-messages",
            base_url="https://api.anthropic.com",
        ),
    ]
    cred = {"type": "oauth", "access": "tid=x;proxy-ep=proxy.example.com;"}
    out = provider.modify_models(rows, cred)
    copilot = next(r for r in out if r.provider_name == "github-copilot")
    other = next(r for r in out if r.provider_name == "anthropic")
    assert copilot.base_url == "https://proxy.example.com"
    assert other.base_url == "https://api.anthropic.com"  # untouched


def test_copilot_enable_model_hits_policy_endpoint():
    transport = FakeTransport({"/policy": (200, "{}")})
    provider = GitHubCopilotOAuthProvider(transport=transport)
    ok = provider.enable_model("tid=x;proxy-ep=proxy.example.com;", "gpt-5.4")
    assert ok is True
    method, url, meta = transport.calls[-1]
    assert url == "https://proxy.example.com/models/gpt-5.4/policy"
    assert json.loads(meta["data"])["state"] == "enabled"


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
