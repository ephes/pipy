"""Owned identity and real fake-HTTP boundaries; no remote providers."""

from dataclasses import FrozenInstanceError, replace
from importlib import metadata

import pytest
from test_native_openai_completions_provider import FakeJsonHTTPClient
from test_native_prompt_cache_affinity import _codex, _request

from pipy_harness.native.agent.request import freeze_provider_request
from pipy_harness.native.client_identity import (
    ClientAttribution,
    ClientIdentity,
    CustomUserAgent,
    format_user_agent,
    identity_headers,
    merge_client_identity,
)
from pipy_harness.native.http import JsonResponse
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.provider import apply_provider_headers
from pipy_harness.native.providers.openai_completions import (
    OpenAIChatCompletionsProvider,
)
from pipy_harness.status import HarnessStatus


def request(tmp_path, **kwargs):
    return ProviderRequest("system", "input", "openai", "gpt-test", tmp_path, **kwargs)


def test_owned_categories_empty_clear_and_merge():
    categories = ["cli-agent"]
    attribution = ClientAttribution("app", "https://app.invalid", categories)
    categories.append("caller-mutation")
    assert attribution.categories == ("cli-agent",)
    default = ClientIdentity("short", attribution, "1")
    assert merge_client_identity(None) is None
    assert merge_client_identity(None, ClientIdentity()) == ClientIdentity()
    assert merge_client_identity(
        default, ClientIdentity(attribution=None, version="")
    ) == ClientIdentity("short", None, "")
    assert (
        merge_client_identity(
            default, ClientIdentity(attribution=ClientAttribution("new", "", []))
        ).attribution.categories
        == ()
    )
    with pytest.raises(FrozenInstanceError):
        attribution.name = "changed"


@pytest.mark.parametrize("value", [object(), {}, True, "short"])
def test_foreign_identity_refused_without_callbacks(tmp_path, value):
    with pytest.raises(TypeError):
        freeze_provider_request(request(tmp_path, identity=value))


def test_foreign_carriers_never_iterate_or_compare(tmp_path):
    class Poison:
        def __iter__(self):
            raise AssertionError("foreign iteration")

        def __eq__(self, other):
            raise AssertionError("foreign equality")

    with pytest.raises(TypeError):
        ClientAttribution("app", "url", Poison())
    with pytest.raises(TypeError):
        ClientIdentity(user_agent=Poison())
    with pytest.raises(TypeError):
        ClientIdentity(attribution=Poison())
    with pytest.raises(TypeError):
        ClientIdentity(version=Poison())


def test_version_truth_and_missing_distribution(monkeypatch):
    from pipy_harness.native import client_identity as identity

    monkeypatch.setattr(
        identity.metadata,
        "version",
        lambda name: "9.8.7" if name == "pipy" else pytest.fail(name),
    )
    assert format_user_agent(ClientIdentity("short")) == "pipy/9.8.7"
    assert format_user_agent(ClientIdentity("short", version="")) == "pipy/"
    detailed = format_user_agent(ClientIdentity(version="test-version"))
    assert detailed.startswith("pipy/test-version (") and "; python/" in detailed

    def absent(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(identity.metadata, "version", absent)
    assert format_user_agent(ClientIdentity("short")) == "pipy"
    assert format_user_agent().startswith("pipy (")
    assert (
        format_user_agent(ClientIdentity("short", version="explicit"))
        == "pipy/explicit"
    )

    def broken(name):
        raise RuntimeError("actual metadata corruption")

    monkeypatch.setattr(identity.metadata, "version", broken)
    assert format_user_agent(ClientIdentity(CustomUserAgent(""))) == ""
    with pytest.raises(RuntimeError, match="metadata corruption"):
        format_user_agent()


@pytest.mark.parametrize(
    "provider,url,key,value",
    [
        ("openrouter", None, "X-OpenRouter-Title", "app"),
        (
            "custom",
            "https://openrouter.ai/api/v1",
            "HTTP-Referer",
            "https://app.invalid",
        ),
        ("nvidia", None, "X-BILLING-INVOKE-ORIGIN", "App"),
        (
            "custom",
            "https://integrate.api.nvidia.com/v1",
            "X-BILLING-INVOKE-ORIGIN",
            "App",
        ),
        ("opencode-go", None, "x-opencode-client", "app"),
        ("custom", "https://opencode.ai/zen", "x-opencode-client", "app"),
    ],
)
def test_release_target_table(provider, url, key, value):
    identity = ClientIdentity(
        CustomUserAgent("native-fixture"),
        ClientAttribution("app", "https://app.invalid", ["one", "two"]),
    )
    headers = identity_headers(identity, provider, target_url=url)
    assert headers[key] == value and headers["User-Agent"] == "native-fixture"
    if key == "X-OpenRouter-Title":
        assert headers["X-OpenRouter-Categories"] == "one,two"


@pytest.mark.parametrize(
    "url",
    [
        None,
        "not a URL",
        "https://opencode.ai.attacker.invalid/",
        "https://opencode.ai:bad/",
        "https://opencode.ai:99999/",
        "https://integrate.api.nvidia.com:bad/",
        "https://integrate.api.nvidia.com:99999/",
        "https://[invalid",
        "https://unrelated.invalid",
    ],
)
def test_unknown_or_malformed_custom_target_does_not_gain_attribution(url):
    headers = identity_headers(
        ClientIdentity(CustomUserAgent("ua"), ClientAttribution("app", "url")),
        "custom",
        target_url=url,
        session_id="session",
    )
    assert headers == {"User-Agent": "ua"}


def test_nvidia_uppercases_one_JS_UTF16_unit_and_empty_categories_omit_header():
    for name, expected in [("ßapp", "SSapp"), ("𐐨app", "𐐨app"), ("", "")]:
        headers = identity_headers(
            ClientIdentity(attribution=ClientAttribution(name, "")), "nvidia"
        )
        assert headers["X-BILLING-INVOKE-ORIGIN"] == expected
    headers = identity_headers(
        ClientIdentity(attribution=ClientAttribution("app", "", ())), "openrouter"
    )
    assert "X-OpenRouter-Categories" not in headers


@pytest.mark.parametrize("session", [None, "", "conversation-1"])
def test_opencode_session_is_independent_of_attribution_and_low_precedence(
    tmp_path, session
):
    req = request(
        tmp_path,
        identity=ClientIdentity(CustomUserAgent("ua"), None),
        session_id=session,
    )
    headers = apply_provider_headers(req, {}, target_url="https://opencode.ai/zen")
    assert headers.get("x-opencode-session") == (session or None)
    assert "x-opencode-client" not in headers
    if session:
        assert apply_provider_headers(
            req, {"X-OpenCode-Session": "configured"}, target_url="https://opencode.ai"
        ) == {"User-Agent": "ua", "X-OpenCode-Session": "configured"}


def test_real_completion_case_insensitive_layers_and_callback_receiver(tmp_path):
    wire = FakeJsonHTTPClient(
        JsonResponse(
            200,
            {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]},
        )
    )
    calls = []

    def callback(headers):
        calls.append(dict(headers))
        headers["USER-AGENT"] = None
        headers["x-opencode-client"] = "request"
        headers["x-delete"] = None

    provider = OpenAIChatCompletionsProvider(
        model_id="gpt-test",
        api_key="fixture-key",
        endpoint="https://opencode.ai/zen",
        extra_headers={"user-agent": "configured", "X-Delete": "delete"},
        http_client=wire,
    )
    result = provider.complete(
        request(
            tmp_path,
            identity=ClientIdentity(
                CustomUserAgent("default"), ClientAttribution("default", "")
            ),
            provider_header_callback=callback,
        )
    )
    assert result.status is HarnessStatus.SUCCEEDED
    assert len(calls) == 1 and calls[0]["user-agent"] == "configured"
    sent = wire.requests[0]["headers"]
    assert not any(k.lower() in ("user-agent", "x-delete") for k in sent)
    assert sent["x-opencode-client"] == "request"
    assert sent["Authorization"] == "Bearer fixture-key"


def test_real_completion_with_missing_metadata_still_succeeds(tmp_path, monkeypatch):
    from pipy_harness.native import client_identity as identity

    def absent(_):
        raise metadata.PackageNotFoundError("pipy")

    monkeypatch.setattr(identity.metadata, "version", absent)
    wire = FakeJsonHTTPClient(
        JsonResponse(
            200, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )
    )
    result = OpenAIChatCompletionsProvider(
        model_id="gpt-test", api_key="fixture", http_client=wire
    ).complete(request(tmp_path))
    assert result.status is HarnessStatus.SUCCEEDED
    assert wire.requests[0]["headers"]["User-Agent"].startswith("pipy (")


@pytest.mark.parametrize("transport", ["sse", "websocket"])
@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("session", [None, "session"])
def test_codex_owned_headers_beat_hook_on_both_transports(
    tmp_path, transport, configured, session
):
    provider, sse, ws = _codex(transport)
    provider = replace(
        provider,
        extra_headers={"uSeR-aGeNt": "configured", "Originator": "configured-origin"}
        if configured
        else {},
    )
    exposed = []

    def hook(headers):
        exposed.append(dict(headers))
        headers.update(
            {
                "user-agent": None,
                "originator": "wrong",
                "authorization": "wrong",
                "chatgpt-account-id": None,
                "session-id": "wrong",
                "OpenAI-Beta": "wrong",
            }
        )

    req = replace(
        _request(tmp_path, session_id=session, header_callback=hook),
        identity=ClientIdentity(CustomUserAgent("identity")),
    )
    assert provider.complete(req).status is HarnessStatus.SUCCEEDED
    sent = (sse.requests if transport == "sse" else ws.requests)[0]["headers"]
    normalized = {key.lower(): value for key, value in sent.items()}
    assert exposed == [{}]
    assert normalized["user-agent"] == ("configured" if configured else "identity")
    assert normalized["originator"] == ("configured-origin" if configured else "pipy")
    assert normalized["authorization"].startswith("Bearer ")
    if session is not None:
        assert normalized["session-id"] == session
    elif transport == "sse":
        assert "session-id" not in normalized
    else:
        assert normalized["session-id"].startswith("fresh-")
    assert normalized["openai-beta"] != "wrong"


def test_public_header_helper_two_positional_call_still_adds_named_target_defaults(
    tmp_path,
):
    req = replace(
        request(tmp_path),
        provider_name="openrouter",
        identity=ClientIdentity(CustomUserAgent("ua"), ClientAttribution("app", "url")),
    )
    assert apply_provider_headers(req, {"Authorization": "fixture"}) == {
        "User-Agent": "ua",
        "HTTP-Referer": "url",
        "X-OpenRouter-Title": "app",
        "Authorization": "fixture",
    }
