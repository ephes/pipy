"""Tests for the pipy auth store + per-request auth resolution (M6)."""

from __future__ import annotations

from pipy_harness.native.auth_store import (
    AuthStore,
    ProviderAuthRequestConfig,
    env_api_key,
    find_env_keys,
    provider_auth_status,
    provider_available,
    resolve_config_value,
    resolve_request_auth,
)


# ---- resolve_config_value (literal / env-name / !command) ------------------


def test_resolve_config_value_literal():
    assert resolve_config_value("sk-literal", env={}) == "sk-literal"


def test_resolve_config_value_env_name():
    assert resolve_config_value("MY_KEY", env={"MY_KEY": "secret"}) == "secret"


def test_resolve_config_value_command_runs_and_strips():
    calls: list[str] = []

    def runner(command: str) -> str | None:
        calls.append(command)
        return "  cmd-secret\n"

    assert resolve_config_value("!echo hi", env={}, run_command=runner) == "cmd-secret"
    assert calls == ["echo hi"]


# ---- env credential detection ----------------------------------------------


def test_find_env_keys_anthropic_prefers_oauth_token():
    env = {"ANTHROPIC_API_KEY": "k", "ANTHROPIC_OAUTH_TOKEN": "t"}
    assert find_env_keys("anthropic", env) == [
        "ANTHROPIC_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
    ]


def test_env_api_key_openai():
    assert env_api_key("openai", {"OPENAI_API_KEY": "sk-x"}) == "sk-x"


def test_openai_codex_has_no_env_api_key():
    # openai-codex requires OAuth; env lookup must not return OPENAI_API_KEY.
    assert env_api_key("openai-codex", {"OPENAI_API_KEY": "sk-x"}) is None
    assert find_env_keys("openai-codex", {"OPENAI_API_KEY": "sk-x"}) is None


def test_google_uses_only_gemini_api_key():
    assert env_api_key("google", {"GEMINI_API_KEY": "g"}) == "g"
    assert env_api_key("google", {"GOOGLE_API_KEY": "g"}) is None


def test_azure_availability_depends_only_on_api_key():
    # No AZURE_OPENAI_ENDPOINT needed for availability (resolved later).
    assert env_api_key("azure-openai", {"AZURE_OPENAI_API_KEY": "k"}) == "k"
    assert env_api_key("azure-openai", {"AZURE_OPENAI_ENDPOINT": "x"}) is None


def test_cloudflare_availability_depends_only_on_api_key():
    assert env_api_key("cloudflare", {"CLOUDFLARE_API_KEY": "k"}) == "k"
    assert env_api_key("cloudflare", {"CLOUDFLARE_ACCOUNT_ID": "x"}) is None


def test_bedrock_ambient_credentials_count_as_available():
    assert env_api_key("amazon-bedrock", {"AWS_PROFILE": "default"}) == "<authenticated>"
    assert (
        env_api_key(
            "amazon-bedrock",
            {"AWS_ACCESS_KEY_ID": "a", "AWS_SECRET_ACCESS_KEY": "b"},
        )
        == "<authenticated>"
    )
    assert env_api_key("amazon-bedrock", {}) is None
    # partial IAM creds do not count
    assert env_api_key("amazon-bedrock", {"AWS_ACCESS_KEY_ID": "a"}) is None


def test_vertex_adc_requires_creds_project_and_location(tmp_path):
    creds = tmp_path / "adc.json"
    creds.write_text("{}", encoding="utf-8")
    full = {
        "GOOGLE_APPLICATION_CREDENTIALS": str(creds),
        "GOOGLE_CLOUD_PROJECT": "proj",
        "GOOGLE_CLOUD_LOCATION": "us-central1",
    }
    assert env_api_key("google-vertex", full) == "<authenticated>"
    # explicit api key path
    assert env_api_key("google-vertex", {"GOOGLE_CLOUD_API_KEY": "k"}) == "k"
    # missing location → not available via ADC
    no_loc = dict(full)
    del no_loc["GOOGLE_CLOUD_LOCATION"]
    assert env_api_key("google-vertex", no_loc) is None


# ---- auth store CRUD + availability ----------------------------------------


def _store(tmp_path) -> AuthStore:
    return AuthStore(path=tmp_path / "auth.json")


def test_auth_store_set_get_remove(tmp_path):
    store = _store(tmp_path)
    store.set("openai", {"type": "api_key", "key": "sk-stored"})
    assert store.get("openai") == {"type": "api_key", "key": "sk-stored"}
    # reload from disk
    store2 = _store(tmp_path)
    assert store2.get("openai") == {"type": "api_key", "key": "sk-stored"}
    store2.remove("openai")
    assert _store(tmp_path).get("openai") is None


def test_provider_available_reflects_store_env_and_models_json(tmp_path):
    store = _store(tmp_path)
    # via env
    assert provider_available("openai", store=store, env={"OPENAI_API_KEY": "k"})
    # via stored key
    assert not provider_available("openai", store=store, env={})
    store.set("openai", {"type": "api_key", "key": "sk"})
    assert provider_available("openai", store=store, env={})
    # via models.json key on an otherwise-unconfigured custom provider
    assert provider_available(
        "ds4",
        store=store,
        env={},
        models_json_config=ProviderAuthRequestConfig(api_key="local"),
    )


# ---- auth status labels (no !command exec, no refresh) ---------------------


def test_auth_status_stored(tmp_path):
    store = _store(tmp_path)
    store.set("openai", {"type": "api_key", "key": "sk"})
    status = provider_auth_status("openai", store=store, env={})
    assert status.source == "stored"
    assert status.configured is True


def test_auth_status_environment(tmp_path):
    store = _store(tmp_path)
    status = provider_auth_status("openai", store=store, env={"OPENAI_API_KEY": "k"})
    assert status.source == "environment"
    assert status.label == "OPENAI_API_KEY"


def test_auth_status_models_json_command_does_not_execute(tmp_path):
    store = _store(tmp_path)
    executed: list[str] = []

    status = provider_auth_status(
        "ds4",
        store=store,
        env={},
        models_json_config=ProviderAuthRequestConfig(api_key="!secret-tool"),
        run_command=lambda c: executed.append(c) or "x",
    )
    assert status.source == "models_json_command"
    assert executed == []  # status never runs the command


def test_auth_status_models_json_key(tmp_path):
    store = _store(tmp_path)
    status = provider_auth_status(
        "ds4",
        store=store,
        env={},
        models_json_config=ProviderAuthRequestConfig(api_key="local"),
    )
    assert status.source == "models_json_key"
    assert status.configured is True


def test_auth_status_models_json_env_name_reports_environment(tmp_path):
    store = _store(tmp_path)
    status = provider_auth_status(
        "ds4",
        store=store,
        env={"DS4_KEY": "secret"},
        models_json_config=ProviderAuthRequestConfig(api_key="DS4_KEY"),
    )
    assert status.source == "environment"
    assert status.label == "DS4_KEY"
    assert status.configured is True


def test_auth_status_ambient_credentials_not_labelled(tmp_path):
    store = _store(tmp_path)
    # Pi's status only labels actual API-key env vars, never ambient creds.
    status = provider_auth_status(
        "amazon-bedrock", store=store, env={"AWS_PROFILE": "default"}
    )
    assert status.source is None
    assert status.configured is False


# ---- request auth resolution (priority + headers + authHeader) -------------


def test_resolve_request_auth_priority_runtime_over_stored_over_env(tmp_path):
    store = _store(tmp_path)
    store.set("openai", {"type": "api_key", "key": "sk-stored"})
    resolved = resolve_request_auth(
        "openai",
        store=store,
        env={"OPENAI_API_KEY": "sk-env"},
        runtime_api_key="sk-runtime",
    )
    assert resolved.ok and resolved.api_key == "sk-runtime"


def test_resolve_request_auth_stored_over_env(tmp_path):
    store = _store(tmp_path)
    store.set("openai", {"type": "api_key", "key": "sk-stored"})
    resolved = resolve_request_auth(
        "openai", store=store, env={"OPENAI_API_KEY": "sk-env"}
    )
    assert resolved.ok and resolved.api_key == "sk-stored"


def test_resolve_request_auth_models_json_key_is_last(tmp_path):
    store = _store(tmp_path)
    resolved = resolve_request_auth(
        "ds4",
        store=store,
        env={},
        models_json_config=ProviderAuthRequestConfig(api_key="local"),
    )
    assert resolved.ok and resolved.api_key == "local"


def test_resolve_request_auth_authheader_and_headers(tmp_path):
    store = _store(tmp_path)
    resolved = resolve_request_auth(
        "ds4",
        store=store,
        env={"TOK": "abc"},
        models_json_config=ProviderAuthRequestConfig(
            api_key="TOK",
            auth_header=True,
            headers={"X-Org": "ORG_ENV"},
        ),
        env_for_headers={"ORG_ENV": "org-123"},
    )
    assert resolved.ok
    assert resolved.headers["Authorization"] == "Bearer abc"
    assert resolved.headers["X-Org"] == "org-123"


def test_resolve_request_auth_authheader_without_key_fails(tmp_path):
    store = _store(tmp_path)
    resolved = resolve_request_auth(
        "ds4",
        store=store,
        env={},
        models_json_config=ProviderAuthRequestConfig(auth_header=True),
    )
    assert resolved.ok is False
    assert resolved.error is not None and "ds4" in resolved.error


def test_resolve_request_auth_stored_oauth_via_resolver(tmp_path):
    store = _store(tmp_path)
    store.set(
        "anthropic",
        {"type": "oauth", "access": "tok", "refresh": "r", "expires": 9999999999000},
    )
    resolved = resolve_request_auth(
        "anthropic",
        store=store,
        env={},
        oauth_token_resolver=lambda provider, cred: cred["access"],
    )
    assert resolved.ok and resolved.api_key == "tok"
