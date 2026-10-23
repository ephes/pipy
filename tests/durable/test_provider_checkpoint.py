"""Fake-wire request reconstruction, real private auth material and target pinning."""

import json
from dataclasses import replace

import pytest
from test_canonical_codec import SNAPSHOT

from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.agent.usage import AgentTokenPricing, AgentTokenPricingTier
from pipy_harness.native.auth_store import AuthStore, ProviderAuthRequestConfig
from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.durable.provider_checkpoint import (
    decode_provider_settings,
    prepare_provider_settings,
    reconstruct_provider,
)
from pipy_harness.native.durable.provider_settings import ProviderCheckpointError
from pipy_harness.native.http import JsonResponse


class Wire:
    def __init__(self):
        self.calls = []

    def post_json(self, url, **kwargs):
        self.calls.append((url, dict(kwargs["headers"]), kwargs["body"]))
        return JsonResponse(
            200,
            {
                "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    }
                ],
                "content": [{"type": "text", "text": "done"}],
                "stop_reason": "end_turn",
                "candidates": [
                    {"content": {"parts": [{"text": "done"}]}, "finishReason": "STOP"}
                ],
                "result": {"response": "done"},
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        )


def request(spec):
    # The field-rich DE06a snapshot remains the input, with actual adapter identity.
    return snapshot_provider_request(
        replace(
            SNAPSHOT.request,
            provider_name=spec.provider_name,
            model_id=spec.model_id,
            messages=(),
            available_tools=(),
            hidden_tool_names=(),
            undeclared_tool_names=(),
            attachments=(),
            system_messages=(),
        )
    )


def prepare(tmp_path, spec, *, env=None, config=None, **kwargs):
    return prepare_provider_settings(
        request(spec),
        spec,
        auth_path=tmp_path / "auth.json",
        env=env or {},
        config=config,
        json_client=True,
        **kwargs,
    )


@pytest.mark.parametrize(
    "api,provider,model",
    [
        ("openai-completions", "openai", "gpt-test"),
        ("openai-responses", "openai", "gpt-test"),
        ("azure-openai-responses", "azure-openai", "gpt-test"),
        ("anthropic-messages", "anthropic", "claude-test"),
        ("google-generative-ai", "google", "gemini-test"),
        ("mistral", "mistral", "mistral-test"),
        ("cloudflare-workers-ai", "cloudflare", "model-test"),
    ],
)
def test_catalog_fake_wire_key_rotation_and_header_privacy(
    tmp_path, api, provider, model
):
    path = tmp_path / "auth.json"
    store = AuthStore(path)
    store.set(provider, {"type": "api_key", "key": "fake-first-key"})
    spec = NativeModelSpec(
        provider,
        model,
        "Test",
        api,
        base_url="https://fake.invalid/private-endpoint",
        headers={"X-Layer": "model-private-header"},
    )
    config = ProviderAuthRequestConfig(
        headers={"X-Private": "PRIVATE_ENV", "X-Layer": "provider-private-header"}
    )
    env = {"PRIVATE_ENV": "fake-secret-header"}
    saved = prepare(
        tmp_path,
        spec,
        env=env,
        config=config,
        thinking="high",
        pricing=AgentTokenPricing(1, 2, 3, 4, (AgentTokenPricingTier(10, 5, 6, 7, 8),)),
    )
    encoded = json.dumps(saved.to_tree())
    for secret in (
        "fake-first-key",
        "fake-secret-header",
        "PRIVATE_ENV",
        "private-endpoint",
        "model-private-header",
        "provider-private-header",
    ):
        assert secret not in encoded
    store.set(provider, {"type": "api_key", "key": "fake-key-2"})
    wire = Wire()
    replay = reconstruct_provider(
        saved, spec, auth_path=path, env=env, config=config, http_client=wire
    )
    replay.complete(request(spec).request)
    assert len(wire.calls) == 1
    url, headers, body = wire.calls[0]
    assert headers["X-Private"] == "fake-secret-header"
    assert headers["X-Layer"] == "model-private-header"
    assert "fake-key-2" in json.dumps((url, headers))
    assert "fake-first-key" not in json.dumps((url, headers))
    assert saved.pricing.rates_for(11).input_per_million == 5


@pytest.mark.parametrize(
    "change",
    ["literal", "env", "source", "endpoint", "thinking", "model", "missing", "corrupt"],
)
def test_private_source_target_and_settings_drift_refuses_before_transport(
    tmp_path, change
):
    spec = NativeModelSpec(
        "openai",
        "gpt-test",
        "Test",
        "openai-completions",
        headers={"X-Private": "PRIVATE_ENV"},
    )
    env = {"OPENAI_API_KEY": "fake-key", "PRIVATE_ENV": "fake-header"}
    saved = prepare(tmp_path, spec, env=env)
    if change == "literal":
        spec = replace(spec, headers={"X-Private": "different"})
    elif change == "env":
        env["PRIVATE_ENV"] = "different"
    elif change == "source":
        spec = replace(spec, headers={"X-Private": "fake-header"})
    elif change == "endpoint":
        spec = replace(spec, base_url="https://different.invalid/secret")
    elif change == "thinking":
        tree = saved.to_tree()
        tree["thinking"] = "high"
        saved = decode_provider_settings(tree)
    elif change == "model":
        spec = replace(spec, model_id="different")
    elif change == "missing":
        (tmp_path / "auth.json.request-material").unlink()
    else:
        (tmp_path / "auth.json.request-material").write_text("{fake-private-broken")
    wire = Wire()
    with pytest.raises(ProviderCheckpointError) as error:
        reconstruct_provider(
            saved, spec, auth_path=tmp_path / "auth.json", env=env, http_client=wire
        )
    assert wire.calls == []
    assert "fake" not in str(error.value)


@pytest.mark.parametrize(
    "api,provider,model,env,target",
    [
        (
            "amazon-bedrock",
            "amazon-bedrock",
            "anthropic.claude-test",
            {
                "AWS_REGION": "us-east-2",
                "AWS_ACCESS_KEY_ID": "fake-access",
                "AWS_SECRET_ACCESS_KEY": "fake-sign",
            },
            "us-east-2",
        ),
        (
            "google-vertex",
            "google-vertex",
            "gemini-test",
            {
                "GOOGLE_CLOUD_PROJECT": "fake-project",
                "GOOGLE_CLOUD_LOCATION": "us-east1",
                "GOOGLE_ACCESS_TOKEN": "fake-token",
            },
            "fake-project",
        ),
        (
            "google-vertex",
            "google-vertex",
            "gemini-test",
            {"GOOGLE_CLOUD_API_KEY": "fake-express"},
            "aiplatform.googleapis.com",
        ),
    ],
)
def test_iam_saved_target_live_auth_rotation_and_target_drift(
    tmp_path, api, provider, model, env, target
):
    spec = NativeModelSpec(provider, model, "Test", api)
    saved = prepare(tmp_path, spec, env=env)
    public = json.dumps(saved.to_tree())
    assert all(value not in public for value in env.values())
    rotated = dict(env)
    for key in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "GOOGLE_ACCESS_TOKEN",
        "GOOGLE_CLOUD_API_KEY",
    ):
        if key in rotated:
            rotated[key] += "-rotated"
    wire = Wire()
    port = reconstruct_provider(
        saved, spec, auth_path=tmp_path / "auth.json", env=rotated, http_client=wire
    )
    port.complete(request(spec).request)
    assert len(wire.calls) == 1
    assert target in wire.calls[0][0]
    headers = json.dumps(wire.calls[0][1])
    assert "rotated" in headers
    drifted = dict(rotated)
    if api == "amazon-bedrock":
        drifted["AWS_REGION"] = "us-west-2"
    elif "GOOGLE_CLOUD_API_KEY" in env:
        drifted.pop("GOOGLE_CLOUD_API_KEY")
        drifted.update(
            GOOGLE_CLOUD_PROJECT="fake-project", GOOGLE_ACCESS_TOKEN="fake-token"
        )
    else:
        drifted["GOOGLE_CLOUD_PROJECT"] = "other-project"
    with pytest.raises(ProviderCheckpointError):
        reconstruct_provider(
            saved, spec, auth_path=tmp_path / "auth.json", env=drifted, http_client=wire
        )
    assert len(wire.calls) == 1


def test_callable_headers_refused_without_executing_or_private_put(tmp_path):
    called = []

    def callback(headers):
        called.append(headers)

    source = snapshot_provider_request(
        replace(SNAPSHOT.request, provider_header_callback=callback)
    )
    spec = NativeModelSpec("provider", "model", "Test", "openai-completions")
    with pytest.raises(ProviderCheckpointError):
        prepare_provider_settings(
            source, spec, auth_path=tmp_path / "auth.json", env={}
        )
    assert called == []
    assert not (tmp_path / "auth.json.request-material").exists()


@pytest.mark.parametrize("api", ["openai-completions", "openai-responses"])
def test_existing_oauth_refresh_authority_reconstructs_rotated_key(
    tmp_path, monkeypatch, api
):
    from pipy_harness.native import provider_construction
    from pipy_harness.native.oauth_providers import OAuthCredentialCache

    path = tmp_path / "auth.json"
    store = AuthStore(path)
    store.set(
        "openai",
        {
            "type": "oauth",
            "access": "fake-initial",
            "refresh": "fake-refresh",
            "expires": 10000000,
        },
    )

    class OAuth:
        def refresh_token(self, value):
            return {
                **value,
                "access": "fake-new-access",
                "refresh": "fake-new-refresh",
                "expires": 10000000,
            }

        def get_api_key(self, value):
            return value["access"]

    authority = OAuth()
    monkeypatch.setattr(
        provider_construction, "get_oauth_provider", lambda name: authority
    )
    now = [0]
    cache = OAuthCredentialCache(
        providers=lambda name: authority, now_ms=lambda: now[0], auth_path=path
    )
    spec = NativeModelSpec("openai", "gpt-test", "Test", api)
    config = ProviderAuthRequestConfig(
        auth_header=True, headers={"X-Custom": "fake-static"}
    )
    saved = prepare(
        tmp_path,
        spec,
        env={"PIPY_CACHE_RETENTION": "long"},
        config=config,
        credentials=cache,
    )
    now[0] = 10000000
    wire = Wire()
    replay = reconstruct_provider(
        saved,
        spec,
        auth_path=path,
        env={"PIPY_CACHE_RETENTION": "long"},
        config=config,
        credentials=cache,
        http_client=wire,
    )
    monkeypatch.setenv("PIPY_CACHE_RETENTION", "short")
    replay.complete(replace(request(spec).request, cache_retention=None))
    assert wire.calls[0][1]["Authorization"] == "Bearer fake-new-access"
    assert wire.calls[0][1]["X-Custom"] == "fake-static"
    if api == "openai-responses":
        assert wire.calls[0][2]["prompt_cache_retention"] == "24h"
    assert AuthStore(path).get("openai")["refresh"] == "fake-new-refresh"
    assert "fake-" not in json.dumps(saved.to_tree())


def test_codex_static_headers_sse_live_auth_overwrites_and_saved_options(tmp_path):
    from test_native_openai_codex_provider import (
        FakeSseHTTPClient,
        auth_manager_with,
        credentials,
    )

    from pipy_harness.native.openai_codex_provider import SseResponse
    from pipy_harness.native.provider_construction import ConstructionOptions
    from pipy_harness.native.retry import RetryPolicy

    spec = NativeModelSpec(
        "openai-codex",
        "gpt-test",
        "Test",
        "openai-codex-responses",
        headers={
            "X-Private": "fake-static",
            "Authorization": "fake-ignored",
            "originator": "fake-origin",
            "User-Agent": "fake-agent",
        },
    )
    options = ConstructionOptions(
        retry_policy=RetryPolicy(max_attempts=1),
        openai_codex_timeout_seconds=7,
        openai_codex_transport="sse",
        openai_codex_websocket_connect_timeout_seconds=4,
    )
    saved = prepare_provider_settings(
        request(spec), spec, auth_path=tmp_path / "auth.json", env={}, options=options
    )
    port = reconstruct_provider(saved, spec, auth_path=tmp_path / "auth.json", env={})
    wire = FakeSseHTTPClient(
        response=SseResponse(
            status_code=200,
            body="",
            event_stream=iter(
                [
                    {"type": "response.output_text.delta", "delta": "done"},
                    {"type": "response.completed", "response": {"status": "completed"}},
                ]
            ),
        )
    )
    first = credentials()
    second = replace(first, access_token="fake-codex", account_id="fake-account")
    port = replace(port, auth_manager=auth_manager_with(second), http_client=wire)
    port.complete(request(spec).request)
    assert wire.requests[0]["headers"]["X-Private"] == "fake-static"
    assert wire.requests[0]["headers"]["originator"] == "fake-origin"
    assert wire.requests[0]["headers"]["User-Agent"] == "fake-agent"
    assert wire.requests[0]["headers"]["Authorization"] == "Bearer fake-codex"
    assert wire.requests[0]["headers"]["chatgpt-account-id"] == "fake-account"
    assert wire.requests[0]["timeout_seconds"] == 7
    assert port.websocket_connect_timeout_seconds == 4
    assert port.retry_policy.max_attempts == 1
    for secret in ("fake-static", "fake-ignored", "fake-codex", "fake-account"):
        assert secret not in json.dumps(saved.to_tree())


def test_foreign_header_expression_and_cyclic_compat_refused_before_auth(tmp_path):
    called = []

    class Foreign(dict):
        def items(self):
            called.append("items")
            raise AssertionError("foreign hook")

    spec = NativeModelSpec(
        "openai", "gpt-test", "Test", "openai-completions", headers=Foreign()
    )
    with pytest.raises(ProviderCheckpointError):
        prepare(tmp_path, spec)
    cycle = {}
    cycle["self"] = cycle
    with pytest.raises(ProviderCheckpointError):
        prepare(tmp_path, replace(spec, headers=None, compat=cycle))
    assert called == []
    assert not (tmp_path / "auth.json.request-material").exists()


def test_optional_native_options_and_retriable_numeric_types(tmp_path):
    from pipy_harness.native.durable.provider_settings import (
        decode_options,
        encode_options,
    )
    from pipy_harness.native.provider_construction import ConstructionOptions
    from pipy_harness.native.retry import RetryPolicy

    options = ConstructionOptions(
        retry_policy=RetryPolicy(),
        openai_codex_timeout_seconds=None,
        openai_codex_websocket_connect_timeout_seconds=None,
    )
    tree = encode_options(options)
    assert decode_options(tree) == options
    tree["retry"]["multiplier"] = True
    with pytest.raises(ProviderCheckpointError):
        decode_options(tree)


@pytest.mark.parametrize("recovery", ["rotate", "missing-material", "header-drift"])
def test_actual_checkpoint_close_reopen_reconstruct_and_terminal_read_without_auth(
    tmp_path, recovery
):
    import threading

    from test_checkpoint_codec import abort

    from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
    from pipy_harness.native.catalog import NativeModelCost
    from pipy_harness.native.durable.checkpoint_codec import (
        commit_checkpoint,
        decode_checkpoint,
        define_canonical_task,
        encode_checkpoint,
    )
    from pipy_harness.native.durable.scheduler import TaskScheduler
    from pipy_harness.native.durable.session_core import Session
    from pipy_harness.native.durable.sqlite import open_sqlite_storage

    path, auth = tmp_path / "session.sqlite", tmp_path / "auth.json"
    spec = NativeModelSpec("openai", "gpt-test", "Test", "openai-completions")
    source = request(spec)
    config = ProviderAuthRequestConfig(headers={"X-Custom": "fake-static"})
    AuthStore(auth).set("openai", {"type": "api_key", "key": "fake-old-key"})
    saved = prepare(tmp_path, spec, config=config, pricing=AgentTokenPricing(1, 2))
    identity = {
        "attempt": "original-attempt",
        "request": "original-request",
        "cutoff": 42,
    }

    def packet(phase, role, payload, settings):
        return encode_checkpoint(
            name="recover.request",
            version=1,
            phase=phase,
            role=role,
            identity=identity,
            payload=payload,
            settings=settings,
        )

    entered = threading.Event()

    async def held(frame, runtime, context):
        entered.set()
        await context.signal.wait()

    definition = define_canonical_task(
        "recover.request",
        1,
        initial_phase="request",
        phases={"request": ("request", held)},
        abort=abort,
    )
    first = Session(open_sqlite_storage(str(path), mode="create"))
    conversation = first.commit(
        lambda tx: tx.create_conversation({"kind": "ownerless"})
    ).id
    task_id = first.commit(
        lambda tx: tx.create_task(
            definition,
            packet("request", "request", source, saved).to_tree(),
            ownership={"kind": "conversation"},
            conversation_id=conversation,
        )
    )
    owner = TaskScheduler(first, [definition])
    owner.open()
    owner.resume()
    assert entered.wait(5)
    first.close(timeout=5)
    stored_after_close = open_sqlite_storage(str(path), mode="open")
    assert stored_after_close.task(task_id).to_tree()["state"]["status"] == "pending"
    stored_after_close.close()
    AuthStore(auth).set("openai", {"type": "api_key", "key": "fake-new-key"})
    if recovery == "missing-material":
        (tmp_path / "auth.json.request-material").unlink()
    if recovery == "header-drift":
        config = replace(config, headers={"X-Custom": "different-secret"})
    wire = Wire()
    done = threading.Event()

    async def run(frame, runtime, context):
        assert frame.identity == identity
        assert frame.settings.pricing.input_per_million == 1
        port = reconstruct_provider(
            frame.settings,
            replace(spec, cost=NativeModelCost(input=999)),
            auth_path=auth,
            env={},
            config=config,
            http_client=wire,
        )
        result = port.complete(frame.payload.request)
        await commit_checkpoint(
            runtime,
            packet(
                "result",
                "provider_outcome",
                ProviderTurnOutcome(result=result),
                frame.settings,
            ),
            terminal=True,
        )

    again = define_canonical_task(
        "recover.request",
        1,
        initial_phase="request",
        phases={"request": ("request", run)},
        abort=abort,
    )
    reopened = Session(open_sqlite_storage(str(path), mode="open"))

    def observe(publication):
        if any(
            change.to_tree().get("value", {}).get("state", {}).get("status")
            == "terminal"
            for change in publication.changes
        ):
            done.set()

    reopened.observe_commits(observe)
    scheduler = TaskScheduler(reopened, [again])
    scheduler.open()
    assert wire.calls == []
    scheduler.resume()
    assert done.wait(5)
    reopened.close(timeout=5)
    state = scheduler.inspect()[0].to_tree()["state"]
    if recovery != "rotate":
        assert state["outcome"]["status"] == "faulted"
        assert wire.calls == []
        assert "secret" not in state["outcome"]["error"]["message"]
        return
    assert len(wire.calls) == 1
    assert wire.calls[0][1]["Authorization"] == "Bearer fake-new-key"
    assert wire.calls[0][1]["X-Custom"] == "fake-static"
    auth.unlink()
    (tmp_path / "auth.json.request-material").unlink()
    terminal = decode_checkpoint(
        state["outcome"]["result"],
        name="recover.request",
        version=1,
        phase="result",
        role="provider_outcome",
    )
    assert terminal.payload.result.final_text == "done"
    final = Session(open_sqlite_storage(str(path), mode="open"))
    final_owner = TaskScheduler(final, [again])
    final_owner.open()
    final_owner.resume()
    final.close(timeout=5)
    assert len(wire.calls) == 1


@pytest.mark.parametrize(
    "selector",
    [
        "AZURE_OPENAI_BASE_URL",
        "AZURE_OPENAI_RESOURCE_NAME",
        "AZURE_OPENAI_DEPLOYMENT_NAME_MAP",
        "AZURE_OPENAI_API_VERSION",
    ],
)
def test_azure_detached_target_and_drift_before_effect(tmp_path, monkeypatch, selector):
    env = {
        "AZURE_OPENAI_API_KEY": "fake-key",
        "AZURE_OPENAI_BASE_URL": "https://private.azure.invalid/openai/v1",
        "AZURE_OPENAI_RESOURCE_NAME": "private-resource",
        "AZURE_OPENAI_DEPLOYMENT_NAME_MAP": "gpt-test=private-deploy",
        "AZURE_OPENAI_API_VERSION": "private-version",
    }
    if selector == "AZURE_OPENAI_RESOURCE_NAME":
        env.pop("AZURE_OPENAI_BASE_URL")
    spec = NativeModelSpec("azure-openai", "gpt-test", "Test", "azure-openai-responses")
    saved = prepare(tmp_path, spec, env=env)
    wire = Wire()
    port = reconstruct_provider(
        saved, spec, auth_path=tmp_path / "auth.json", env=env, http_client=wire
    )
    # Mutating the caller map and ambient process after construction cannot redirect.
    changed = {**env, selector: "other-value"}
    env[selector] = "caller-mutated"
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://ambient.invalid/openai/v1")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_NAME_MAP", "gpt-test=ambient-deploy")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "ambient-version")
    port.complete(request(spec).request)
    assert len(wire.calls) == 1
    url, _, body = wire.calls[0]
    assert "ambient" not in url
    assert (
        "private.azure.invalid" in url
        if selector != "AZURE_OPENAI_RESOURCE_NAME"
        else "private-resource.openai.azure.com" in url
    )
    assert "api-version=private-version" in url
    assert body["model"] == "private-deploy"
    with pytest.raises(ProviderCheckpointError):
        reconstruct_provider(
            saved, spec, auth_path=tmp_path / "auth.json", env=changed, http_client=wire
        )
    assert len(wire.calls) == 1
    assert "private-" not in json.dumps(saved.to_tree())


@pytest.mark.parametrize(
    "api,provider,model",
    [
        ("openai-responses", "openai", "gpt-test"),
        ("anthropic-messages", "anthropic", "claude-test"),
        ("amazon-bedrock", "amazon-bedrock", "application-profile"),
    ],
)
def test_detached_cache_defaults_recovery_and_ambient_drift(
    tmp_path, monkeypatch, api, provider, model
):
    env = {
        "OPENAI_API_KEY": "fake-key",
        "ANTHROPIC_API_KEY": "fake-key",
        "PIPY_CACHE_RETENTION": "long",
        "AWS_BEDROCK_FORCE_CACHE": "1",
        "AWS_REGION": "us-east-2",
        "AWS_ACCESS_KEY_ID": "fake-access",
        "AWS_SECRET_ACCESS_KEY": "fake-sign",
    }
    spec = NativeModelSpec(provider, model, "Test", api)
    saved = prepare(tmp_path, spec, env=env)
    saved = decode_provider_settings(json.loads(json.dumps(saved.to_tree())))
    wire = Wire()
    port = reconstruct_provider(
        saved, spec, auth_path=tmp_path / "auth.json", env=env, http_client=wire
    )
    monkeypatch.setenv("PIPY_CACHE_RETENTION", "short")
    monkeypatch.setenv("AWS_BEDROCK_FORCE_CACHE", "0")
    source = replace(request(spec).request, cache_retention=None)
    port.complete(source)
    assert len(wire.calls) == 1
    body = wire.calls[0][2]
    if api == "openai-responses":
        assert body["prompt_cache_retention"] == "24h"
    else:
        assert '"ttl": "1h"' in json.dumps(body)
    for selector in (
        ["PIPY_CACHE_RETENTION", "AWS_BEDROCK_FORCE_CACHE"]
        if api == "amazon-bedrock"
        else ["PIPY_CACHE_RETENTION"]
    ):
        with pytest.raises(ProviderCheckpointError):
            reconstruct_provider(
                saved,
                spec,
                auth_path=tmp_path / "auth.json",
                env={**env, selector: "0"},
                http_client=wire,
            )
    assert len(wire.calls) == 1
    # Explicit canonical caller fields win the captured transport default.
    port.complete(replace(source, cache_retention="none"))
    explicit_body = wire.calls[-1][2]
    assert "prompt_cache_retention" not in explicit_body
    assert '"cache_control"' not in json.dumps(explicit_body)
    assert source.cache_retention is None


@pytest.mark.parametrize("transport", ["sse", "websocket"])
def test_codex_custom_auth_authority_and_case_insensitive_wire_headers(
    tmp_path, monkeypatch, transport
):
    from test_native_openai_codex_provider import (
        FakeSseHTTPClient,
        FakeWebSocketClient,
        completed_events,
        credentials,
    )

    from pipy_harness.native.openai_codex_provider import (
        FileOpenAICodexCredentialStore,
        SseResponse,
    )
    from pipy_harness.native.provider_construction import ConstructionOptions

    path = tmp_path / "chosen" / "auth.json"
    store = FileOpenAICodexCredentialStore(path.with_name("openai-codex.json"))
    store.save(
        replace(credentials(), access_token="fake-first", account_id="first-account")
    )
    monkeypatch.setenv("PIPY_AUTH_DIR", str(tmp_path / "wrong-authority"))
    spec = NativeModelSpec(
        "openai-codex",
        "gpt-test",
        "Test",
        "openai-codex-responses",
        headers={
            "authorization": "fake-ignored",
            "Chatgpt-Account-Id": "wrong-account",
            "user-agent": "private-agent",
            "Originator": "private-origin",
            "accept": "wrong-accept",
            "openai-beta": "wrong-beta",
        },
    )
    saved = prepare_provider_settings(
        request(spec),
        spec,
        auth_path=path,
        env={},
        options=ConstructionOptions(openai_codex_transport=transport),
    )
    port = reconstruct_provider(saved, spec, auth_path=path, env={})
    # Actual selected file authority rotates after construction, without replacing auth_manager.
    store.save(
        replace(credentials(), access_token="fake-second", account_id="second-account")
    )
    sse = FakeSseHTTPClient(
        SseResponse(status_code=200, body="", event_stream=iter(completed_events()))
    )
    ws = FakeWebSocketClient([completed_events()])
    port = replace(port, http_client=sse, websocket_client=ws)
    result = port.complete(request(spec).request)
    calls = sse.requests if transport == "sse" else ws.requests
    assert len(calls) == 1, result
    headers = calls[0]["headers"]
    names = [key.lower() for key in headers]
    assert len(names) == len(set(names))
    lower = {key.lower(): value for key, value in headers.items()}
    assert lower["authorization"] == "Bearer fake-second"
    assert lower["chatgpt-account-id"] == "second-account"
    assert lower["user-agent"] == "private-agent"
    assert lower["originator"] == "private-origin"
    assert "wrong-beta" != lower["openai-beta"]
    assert all(
        word not in json.dumps(saved.to_tree())
        for word in ("fake-second", "private-agent", "wrong-authority")
    )


@pytest.mark.parametrize("transport", ["sse", "websocket"])
def test_codex_mixed_case_static_headers_on_existing_transport(transport):
    from test_native_openai_codex_provider import (
        FakeSseHTTPClient,
        FakeWebSocketClient,
        auth_manager_with,
        completed_events,
        credentials,
    )

    from pipy_harness.native.openai_codex_provider import (
        OpenAICodexResponsesProvider,
        SseResponse,
    )

    sse = FakeSseHTTPClient(
        SseResponse(status_code=200, body="", event_stream=iter(completed_events()))
    )
    ws = FakeWebSocketClient([completed_events()])
    port = OpenAICodexResponsesProvider(
        model_id="gpt-test",
        transport=transport,
        auth_manager=auth_manager_with(credentials()),
        http_client=sse,
        websocket_client=ws,
        extra_headers={
            "authorization": "ignored",
            "Chatgpt-Account-Id": "ignored",
            "user-agent": "fake-agent",
            "Originator": "fake-origin",
            "accept": "ignored",
            "openai-beta": "ignored",
        },
    )
    port.complete(
        request(
            NativeModelSpec(
                "openai-codex", "gpt-test", "Test", "openai-codex-responses"
            )
        ).request
    )
    headers = (sse.requests if transport == "sse" else ws.requests)[0]["headers"]
    names = [key.lower() for key in headers]
    assert len(names) == len(set(names))
    lower = {key.lower(): value for key, value in headers.items()}
    assert lower["user-agent"] == "fake-agent"
    assert lower["originator"] == "fake-origin"
