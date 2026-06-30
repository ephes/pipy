"""Conformance gate for the Pi-style provider/model catalog.

Drives the pipy-owned catalog with deterministic fixtures (a temp config root, a
temp ``models.json``, a fake auth store, fake OAuth HTTP transports, no network)
and fails unless the catalog behaves to spec: the built-in catalog, the matcher,
``models.json`` parsing/merge/validation, routing, thinking mapping, auth
resolution/status, the OAuth provider shape, availability, the ds4 reframe,
refresh/dynamic registration, secret non-leakage, product construction for the
``openai-completions`` API family, catalog-constructed non-completions families,
``pipy run`` one-shot construction, startup provider/model resolution, and
extension-provider catalog wiring. The product-path checks are driven through the actual
``NativeReplProviderState.current_provider``/``provider_for`` boundary and use
capturing fake HTTP clients; no real network or AI call is made.

Run:

    uv run python scripts/parity_checks/provider_catalog_conformance.py --json

Verifies the docs/provider-catalog.md "Verification Plan" items 1-25: catalog
foundation items 1-17, Chat-Completions product construction item 18, archive
secret checks item 19, non-completions product construction items 20-22,
``pipy run`` one-shot construction item 23, and startup provider/model
resolution item 24, plus extension-provider catalog wiring item 25. The
interactive direct-``/model`` resolver and product-TUI surfaces are exercised by
the focused pytest suites.

 1. built-in catalog: multiple rows per implemented provider + real metadata;
 2. exact provider/id matching, ambiguity rejection, bare-id matching;
 3. provider/id:level parsing incl colon-in-id + strict vs scope behaviour;
 4. fuzzy substring with alias-over-dated preference;
 5. glob scoping over provider/id and bare id with optional :level;
 6. resolve_cli_model provider inference, slash handling, fallback synthesis;
 7. models.json custom provider + custom models + provider/per-model override;
 8. comment/trailing-comma stripping + graceful degradation;
 9. validation rejects bad provider configs;
10. routing/compat blocks survive the merge and reach the request config;
11. auth resolution priority + authHeader + merged headers;
12. AuthStatus source labels; no !command exec and no token refresh on status;
13. OAuth provider shape (Anthropic/Copilot/Codex) against fake HTTP fixtures;
14. --thinking/:level set + map through thinking_level_map (off/unsupported);
15. availability gate reflects auth store + models.json keys, not just env;
16. ds4 resolves as a models.json custom provider; env shim is equivalent;
17. catalog refresh() picks up a models.json edit + simulated login/logout;
18. Chat-Completions product construction: custom models.json provider turn,
    catalog baseUrl/model/auth/headers, runtime --api-key, routing/thinking,
    legacy URL bypass, and no secret in the turn result;
19. no secret/token/Authorization/PKCE/auth-URL value in any archive surface;
20. Tier 1 non-completions construction (anthropic/openai-responses/mistral);
21. Tier 2 non-completions construction (google/azure/cloudflare);
22. Tier 3 non-completions construction (bedrock/vertex) plus codex exception;
23. pipy run one-shot construction uses the catalog-backed boundary;
24. startup --native-provider/--native-model resolution uses the shared matcher.

Exits 0 when every check passes, 1 otherwise. No real network/AI calls.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.native.auth_store import (
    AuthStore,
    ProviderAuthRequestConfig,
    provider_auth_status,
    resolve_request_auth,
)
from pipy_harness.native.catalog import build_builtin_catalog, default_model_per_provider
from pipy_harness.native.catalog_state import ProviderCatalogState, format_list_models
from pipy_harness.native.extension_runtime import (
    RegisteredProvider,
    activate_extensions,
    extension_providers,
)
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.ds4 import DS4_DEFAULT_BASE_URL, ds4_preset_dict
from pipy_harness.native.model_resolver import (
    find_exact_model_reference,
    parse_model_pattern,
    resolve_cli_model,
    resolve_model_scope,
)
from pipy_harness.native.models_json import (
    ModelCatalog,
    ModelDefinition,
    ProviderConfig,
)
from pipy_harness.native.openai_completions_provider import JsonResponse
from pipy_harness.native.provider_construction import (
    build_provider,
    resolve_construction,
)
from pipy_harness.native.repl_state import NativeModelSelection, NativeReplProviderState
from pipy_harness.native.oauth_providers import (
    AnthropicOAuthProvider,
    GitHubCopilotOAuthProvider,
    OpenAICodexOAuthProvider,
    copilot_base_url_from_token,
    get_oauth_provider_ids,
)
from pipy_harness.native.routing import model_request_routing
from pipy_harness.native.thinking import map_thinking_level, validate_thinking_level


IMPLEMENTED_PROVIDERS = (
    "anthropic",
    "openai",
    "openai-codex",
    "openai-completions",
    "openrouter",
    "google",
    "google-vertex",
    "mistral",
    "amazon-bedrock",
    "azure-openai",
    "cloudflare",
)

FIXED_NOW_MS = 1_000_000_000_000


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


class FakeTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, method, url, *, headers=None, data=None):
        self.calls.append((method, url, data))
        for key, value in self.responses.items():
            if key in url:
                return value
        return (404, "")


def _check_builtin_catalog(checks):
    catalog = build_builtin_catalog()
    multi = all(len(catalog.models_for(p)) >= 2 for p in IMPLEMENTED_PROVIDERS)
    opus = catalog.find("anthropic", "claude-opus-4-7")
    metadata = bool(
        opus
        and opus.context_window > 0
        and opus.max_tokens > 0
        and opus.reasoning
        and "image" in opus.input
        and opus.cost.input > 0
        and opus.thinking_level_map.get("xhigh")
    )
    checks.append(Check("01_builtin_multiple_rows", multi, "≥2 rows per provider"))
    checks.append(Check("01_builtin_metadata", metadata, "real capability metadata"))


def _check_exact_matching(checks):
    rows = build_builtin_catalog().get_all()
    canonical = find_exact_model_reference("anthropic/claude-opus-4-7", rows)
    bare = find_exact_model_reference("claude-opus-4-7", rows)
    # gpt-5.5 exists on openai and openai-codex → ambiguous bare id rejected.
    ambiguous = find_exact_model_reference("gpt-5.5", rows)
    checks.append(
        Check(
            "02_exact_and_bare_match",
            canonical is not None and bare is not None and ambiguous is None,
            f"canonical={bool(canonical)} bare={bool(bare)} ambiguous_rejected={ambiguous is None}",
        )
    )


def _check_level_parsing(checks):
    rows = build_builtin_catalog().get_all()
    leveled = parse_model_pattern("anthropic/claude-opus-4-7:high", rows)
    colon_id = parse_model_pattern("openrouter/openai/gpt-4o:extended", rows)
    scope_invalid = parse_model_pattern("openai/gpt-5.5:turbo", rows)
    strict_invalid = parse_model_pattern(
        "openai/gpt-5.5:turbo", rows, allow_invalid_thinking_level_fallback=False
    )
    ok = (
        leveled.thinking_level == "high"
        and colon_id.model is not None
        and colon_id.model.model_id == "openai/gpt-4o:extended"
        and scope_invalid.model is not None
        and scope_invalid.thinking_level is None
        and scope_invalid.warning is not None
        and strict_invalid.model is None
    )
    checks.append(Check("03_level_parsing", ok, "level/colon-in-id/strict-vs-scope"))


def _check_fuzzy(checks):
    rows = build_builtin_catalog().get_all()
    # "claude-sonnet-4-5" alias must beat the dated "...-20250929".
    result = parse_model_pattern("sonnet-4-5", rows)
    ok = result.model is not None and result.model.model_id == "claude-sonnet-4-5"
    checks.append(Check("04_fuzzy_alias_over_dated", ok, "alias preferred over dated"))


def _check_glob(checks):
    rows = build_builtin_catalog().get_all()
    scope = resolve_model_scope(["anthropic/claude-*:medium"], rows)
    has_level = scope.models and all(m.thinking_level == "medium" for m in scope.models)
    # minimatch: * does not cross / — every openrouter id has a slash.
    no_cross = resolve_model_scope(["openrouter/*"], rows)
    ok = bool(has_level) and no_cross.models == []
    checks.append(Check("05_glob_scope", ok, "glob over provider/id + bare id, :level"))


def _check_resolve_cli(checks):
    rows = build_builtin_catalog().get_all()
    inferred = resolve_cli_model(cli_provider=None, cli_model="anthropic/claude-opus-4-7", rows=rows)
    fallback = resolve_cli_model(cli_provider="anthropic", cli_model="claude-future-9", rows=rows)
    unknown = resolve_cli_model(cli_provider="nope", cli_model="x", rows=rows)
    ok = (
        inferred.model is not None
        and inferred.model.provider_name == "anthropic"
        and fallback.model is not None
        and fallback.model.model_id == "claude-future-9"
        and fallback.warning is not None
        and unknown.error is not None
    )
    checks.append(Check("06_resolve_cli_model", ok, "inference + fallback synthesis"))


def _check_models_json_merge(checks, tmp: Path):
    path = tmp / "merge.json"
    path.write_text(
        json.dumps(
            {
                "providers": {
                    "anthropic": {
                        "modelOverrides": {
                            "claude-opus-4-7": {
                                "cost": {"input": 0},
                                "thinkingLevelMap": {"low": "low"},
                            }
                        }
                    },
                    "acme": {
                        "baseUrl": "https://acme.example/v1",
                        "apiKey": "local",
                        "api": "openai-completions",
                        "models": [{"id": "rocket-1", "name": "Rocket"}],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    catalog = ModelCatalog(models_json_path=path)
    opus = catalog.find("anthropic", "claude-opus-4-7")
    rocket = catalog.find("acme", "rocket-1")
    ok = (
        catalog.error is None
        and opus is not None
        and opus.cost.input == 0.0
        and opus.cost.output == 25.0
        and opus.thinking_level_map.get("xhigh") == "xhigh"
        and opus.thinking_level_map.get("low") == "low"
        and rocket is not None
        and rocket.base_url == "https://acme.example/v1"
    )
    checks.append(Check("07_models_json_merge", ok, "override deep-merge + custom append"))


def _check_strip_and_degrade(checks, tmp: Path):
    good = tmp / "comments.json"
    good.write_text(
        '{\n  // comment\n  "providers": {"anthropic": {"baseUrl": "https://h//p",}},\n}',
        encoding="utf-8",
    )
    good_catalog = ModelCatalog(models_json_path=good)
    bad = tmp / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    bad_catalog = ModelCatalog(models_json_path=bad)
    ok = (
        good_catalog.error is None
        and good_catalog.find("anthropic", "claude-opus-4-7").base_url == "https://h//p"
        and bad_catalog.error is not None
        and str(bad) in bad_catalog.error
        and bad_catalog.find("anthropic", "claude-opus-4-7") is not None
    )
    checks.append(Check("08_strip_and_degrade", ok, "comments stripped; bad keeps built-ins"))


def _check_validation(checks, tmp: Path):
    override_only = tmp / "override.json"
    override_only.write_text(json.dumps({"providers": {"anthropic": {"name": "x"}}}), encoding="utf-8")
    c1 = ModelCatalog(models_json_path=override_only)

    custom = tmp / "custom.json"
    custom.write_text(
        json.dumps({"providers": {"custom": {"api": "openai-completions", "models": [{"id": "m"}]}}}),
        encoding="utf-8",
    )
    c2 = ModelCatalog(models_json_path=custom)
    ok = (
        c1.error is not None
        and "anthropic" in c1.error
        and c2.error is not None
        and "baseUrl" in c2.error
    )
    checks.append(Check("09_validation_rejects", ok, "override-only + non-builtin-no-auth"))


def _check_routing(checks, tmp: Path):
    path = tmp / "routing.json"
    path.write_text(
        json.dumps(
            {
                "providers": {
                    "openrouter": {
                        "modelOverrides": {
                            "moonshotai/kimi-k2.6": {
                                "compat": {"openRouterRouting": {"order": ["fireworks"], "data_collection": "deny"}}
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    catalog = ModelCatalog(models_json_path=path)
    row = catalog.find("openrouter", "moonshotai/kimi-k2.6")
    routing = model_request_routing(row) if row else {}
    ok = (
        row is not None
        and isinstance(row.compat, dict)
        and routing.get("provider", {}).get("order") == ["fireworks"]
        and routing["provider"]["data_collection"] == "deny"
    )
    checks.append(Check("10_routing_reaches_request", ok, "openRouterRouting -> provider param"))


def _check_auth_priority(checks, tmp: Path):
    store = AuthStore(path=tmp / "auth_pri.json")
    store.set("openai", {"type": "api_key", "key": "sk-stored"})
    runtime = resolve_request_auth(
        "openai", store=store, env={"OPENAI_API_KEY": "sk-env"}, runtime_api_key="sk-rt"
    )
    stored = resolve_request_auth("openai", store=store, env={"OPENAI_API_KEY": "sk-env"})
    # env fallback: no stored credential -> provider env var.
    env_only = resolve_request_auth(
        "openai", store=AuthStore(path=tmp / "auth_env.json"), env={"OPENAI_API_KEY": "sk-env"}
    )
    # stored OAuth resolved through the injected resolver, ahead of env.
    oauth_store = AuthStore(path=tmp / "auth_oauth.json")
    oauth_store.set("anthropic", {"type": "oauth", "access": "oauth-tok", "refresh": "r", "expires": 0})
    oauth = resolve_request_auth(
        "anthropic",
        store=oauth_store,
        env={"ANTHROPIC_API_KEY": "sk-env"},
        oauth_token_resolver=lambda provider, cred: cred["access"],
    )
    # models.json key is the LAST resort (after stored/env miss): literal,
    # env-name, and !command resolution all reach the request.
    empty = AuthStore(path=tmp / "auth_mj.json")
    mj_literal = resolve_request_auth(
        "ds4", store=empty, env={}, models_json_config=ProviderAuthRequestConfig(api_key="literal-key")
    )
    mj_env = resolve_request_auth(
        "ds4", store=empty, env={"DS4_KEY": "from-env"},
        models_json_config=ProviderAuthRequestConfig(api_key="DS4_KEY"),
    )
    mj_cmd = resolve_request_auth(
        "ds4", store=empty, env={},
        models_json_config=ProviderAuthRequestConfig(api_key="!print-key"),
        run_command=lambda c: "cmd-key",
    )
    # Ordering proof: with BOTH a provider env var AND a models.json apiKey set
    # for the same provider, env must win and the models.json !command must NOT
    # run. (Without this, a resolver that consulted models.json before env would
    # still pass the separate fallback cases above.)
    ordering_cmds: list[str] = []
    env_beats_models_json = resolve_request_auth(
        "openai",
        store=empty,
        env={"OPENAI_API_KEY": "sk-env-wins"},
        models_json_config=ProviderAuthRequestConfig(api_key="!should-not-run"),
        run_command=lambda c: ordering_cmds.append(c) or "models-json-loses",
    )
    headers = resolve_request_auth(
        "ds4",
        store=AuthStore(path=tmp / "auth_h.json"),
        env={"TOK": "abc"},
        models_json_config=ProviderAuthRequestConfig(
            api_key="TOK", auth_header=True, headers={"X-Org": "ORG"}
        ),
        env_for_headers={"ORG": "org-1"},
    )
    ok = (
        runtime.api_key == "sk-rt"
        and stored.api_key == "sk-stored"
        and env_only.api_key == "sk-env"
        and oauth.api_key == "oauth-tok"
        and mj_literal.api_key == "literal-key"
        and mj_env.api_key == "from-env"
        and mj_cmd.api_key == "cmd-key"
        and env_beats_models_json.api_key == "sk-env-wins"
        and not ordering_cmds  # models.json !command not run when env wins
        and headers.headers.get("Authorization") == "Bearer abc"
        and headers.headers.get("X-Org") == "org-1"
    )
    checks.append(
        Check(
            "11_auth_priority",
            ok,
            "runtime>stored>oauth>env>models.json (env beats models.json; literal/env/!command); authHeader+headers",
        )
    )


def _check_auth_status(checks, tmp: Path):
    store = AuthStore(path=tmp / "auth_st.json")
    executed = []
    cmd_status = provider_auth_status(
        "ds4",
        store=store,
        env={},
        models_json_config=ProviderAuthRequestConfig(api_key="!secret"),
        run_command=lambda c: executed.append(c) or "x",
    )
    env_status = provider_auth_status("openai", store=store, env={"OPENAI_API_KEY": "k"})
    store.set("anthropic", {"type": "api_key", "key": "sk"})
    stored_status = provider_auth_status("anthropic", store=store, env={})

    # Status on an EXPIRED stored OAuth credential must not refresh the token:
    # the refresh resolver must never be called by a status check.
    refreshed = []
    oauth_store = AuthStore(path=tmp / "auth_st_oauth.json")
    oauth_store.set(
        "openai-codex",
        {"type": "oauth", "access": "tok", "refresh": "r", "expires": 1},  # long expired
    )
    oauth_status = provider_auth_status(
        "openai-codex",
        store=oauth_store,
        env={},
        run_command=lambda c: refreshed.append(("cmd", c)) or "x",
    )

    # authHeader failure is an auth-resolution concern, asserted alongside the
    # status labels: authHeader with no resolvable key fails closed.
    auth_header_fail = resolve_request_auth(
        "ds4",
        store=AuthStore(path=tmp / "auth_st_ah.json"),
        env={},
        models_json_config=ProviderAuthRequestConfig(auth_header=True),
    )

    ok = (
        cmd_status.source == "models_json_command"
        and not executed  # no !command executed during status
        and env_status.source == "environment"
        and env_status.label == "OPENAI_API_KEY"
        and stored_status.source == "stored"
        and oauth_status.source == "stored"
        and not refreshed  # status never refreshed the expired OAuth token
        and auth_header_fail.ok is False
        and auth_header_fail.api_key is None
    )
    checks.append(
        Check(
            "12_auth_status_labels",
            ok,
            "labels correct; no !command exec; no token refresh; authHeader fails closed",
        )
    )


def _check_oauth(checks):
    ids = set(get_oauth_provider_ids())
    anthropic_tx = FakeTransport(
        {"oauth/token": (200, json.dumps({"access_token": "a", "refresh_token": "r", "expires_in": 3600}))}
    )
    anthropic = AnthropicOAuthProvider(transport=anthropic_tx, now_ms=lambda: FIXED_NOW_MS)
    a_cred = anthropic.refresh_token({"refresh": "r"})

    codex_tx = FakeTransport(
        {"token": (200, json.dumps({"access_token": "a", "refresh_token": "r", "expires_in": 3600}))}
    )
    codex = OpenAICodexOAuthProvider(transport=codex_tx, now_ms=lambda: FIXED_NOW_MS)
    c_cred = codex.refresh_token({"refresh": "r"})

    policy_tx = FakeTransport({"/policy": (200, "{}")})
    copilot = GitHubCopilotOAuthProvider(transport=policy_tx)
    enabled = copilot.enable_model("tid=x;proxy-ep=proxy.example.com;", "gpt-5.4")
    base = copilot_base_url_from_token("a;proxy-ep=proxy.example.com;b")

    ok = (
        {"anthropic", "github-copilot", "openai-codex"} <= ids
        and a_cred["expires"] == FIXED_NOW_MS + 3600 * 1000 - 5 * 60 * 1000
        and c_cred["expires"] == FIXED_NOW_MS + 3600 * 1000  # no margin
        and enabled is True
        and base == "https://api.example.com"  # proxy. -> api.
        and policy_tx.calls[-1][1] == "https://api.example.com/models/gpt-5.4/policy"
    )
    checks.append(Check("13_oauth_shape", ok, "anthropic/codex margins; copilot policy+proxy"))


def _check_thinking(checks):
    catalog = build_builtin_catalog()
    opus = catalog.find("anthropic", "claude-opus-4-7")
    haiku = catalog.find("anthropic", "claude-3-5-haiku-20241022")  # non-reasoning
    valid, _ = validate_thinking_level("high")
    _, warn = validate_thinking_level("turbo")
    ok = (
        valid == "high"
        and warn is not None
        and map_thinking_level(opus, "xhigh") == "xhigh"
        and map_thinking_level(opus, "off") is None
        and map_thinking_level(haiku, "high") is None
    )
    checks.append(Check("14_thinking_levels", ok, "validate + map through model map"))


def _check_availability(checks, tmp: Path):
    state_env = ProviderCatalogState(
        models_json_path=tmp / "absent_a.json",
        auth_store=AuthStore(path=tmp / "auth_av1.json"),
        env={"OPENAI_API_KEY": "k"},
        openai_codex_auth_path=tmp / "no-codex.json",
    )
    store = AuthStore(path=tmp / "auth_av2.json")
    store.set("anthropic", {"type": "api_key", "key": "sk"})
    state_store = ProviderCatalogState(
        models_json_path=tmp / "absent_b.json",
        auth_store=store,
        env={},
        openai_codex_auth_path=tmp / "no-codex.json",
    )
    ok = (
        state_env.provider_available("openai")
        and not state_env.provider_available("anthropic")
        and state_store.provider_available("anthropic")
    )
    checks.append(Check("15_availability_gate", ok, "store + env, not just env"))


def _check_ds4(checks, tmp: Path):
    catalog = build_builtin_catalog()
    not_builtin = catalog.models_for("ds4") == [] and "ds4" not in default_model_per_provider

    preset_path = tmp / "ds4_preset.json"
    preset_path.write_text(json.dumps(ds4_preset_dict()), encoding="utf-8")
    preset_state = ProviderCatalogState(
        models_json_path=preset_path,
        auth_store=AuthStore(path=tmp / "auth_ds4a.json"),
        env={},
        openai_codex_auth_path=tmp / "no-codex.json",
    )
    preset_row = preset_state.find("ds4", "deepseek-v4-flash")

    shim_state = ProviderCatalogState(
        models_json_path=tmp / "absent_ds4.json",
        auth_store=AuthStore(path=tmp / "auth_ds4b.json"),
        env={"PIPY_DS4_BASE_URL": DS4_DEFAULT_BASE_URL},
        openai_codex_auth_path=tmp / "no-codex.json",
    )
    shim_row = shim_state.find("ds4", "deepseek-v4-flash")

    ok = (
        not_builtin
        and preset_row is not None
        and shim_row is not None
        and preset_row.api == shim_row.api == "openai-completions"
        and preset_row.base_url == shim_row.base_url == DS4_DEFAULT_BASE_URL
        and shim_state.provider_available("ds4")
    )
    checks.append(Check("16_ds4_custom_provider", ok, "ds4 via models.json + env shim equivalent"))


def _check_refresh(checks, tmp: Path):
    path = tmp / "refresh.json"
    path.write_text(json.dumps({"providers": {"anthropic": {"models": [{"id": "v1"}]}}}), encoding="utf-8")
    catalog = ModelCatalog(models_json_path=path)
    before = catalog.find("anthropic", "v1") is not None
    path.write_text(json.dumps({"providers": {"anthropic": {"models": [{"id": "v2"}]}}}), encoding="utf-8")
    catalog.refresh()
    after = catalog.find("anthropic", "v2") is not None and catalog.find("anthropic", "v1") is None

    # Simulated login/logout via dynamic registration + oauth modifier.
    catalog.register_provider(
        "acme",
        ProviderConfig(
            base_url="https://acme/v1",
            api_key="k",
            api="openai-completions",
            models=(ModelDefinition(id="m"),),
        ),
    )
    login_ok = catalog.find("acme", "m") is not None
    catalog.unregister_provider("acme")
    logout_ok = catalog.find("acme", "m") is None
    checks.append(Check("17_refresh_and_register", before and after and login_ok and logout_ok, "edit + login/logout"))


class _CapturingHTTP:
    def __init__(self):
        self.requests = []

    def post_json(self, url, *, headers, body, timeout_seconds, cancel_token=None):
        self.requests.append({"url": url, "headers": dict(headers), "body": dict(body)})
        return JsonResponse(
            status_code=200,
            body={
                "object": "chat.completion",
                "choices": [
                    {"finish_reason": "stop", "message": {"role": "assistant", "content": "OK"}}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )


def _provider_request(tmp: Path, provider: str, model: str):
    from pipy_harness.native.models import ProviderRequest

    return ProviderRequest(
        system_prompt="SYS", user_prompt="hi", provider_name=provider, model_id=model, cwd=tmp
    )


def _state(tmp: Path, models_path: Path, env: dict):
    return ProviderCatalogState(
        models_json_path=models_path,
        auth_store=AuthStore(path=tmp / f"auth_{models_path.stem}.json"),
        env=env,
        openai_codex_auth_path=tmp / "no-codex.json",
    )


def _construct_and_capture(state, spec, *, runtime_api_key, thinking_level):
    resolved = resolve_construction(
        spec,
        store=state.auth_store,
        env=state._env(),
        runtime_api_key=runtime_api_key,
        models_json_auth=state._models_json_auth(spec.provider_name),
        thinking_level=thinking_level,
    )
    http = _CapturingHTTP()
    provider = build_provider(resolved, http_client=http)
    result = provider.complete(_provider_request(Path("."), spec.provider_name, spec.model_id))
    return http.requests[-1], result


def _check_product_construction(checks, tmp: Path):
    # 18a: a custom models.json provider runs a real (fake-HTTP) turn whose
    # request uses the catalog baseUrl, model id, resolved auth, headers, and
    # mapped thinking.
    custom_path = tmp / "prod_custom.json"
    custom_path.write_text(
        json.dumps(
            {
                "providers": {
                    "acme": {
                        "baseUrl": "https://acme.example/v1",
                        "apiKey": "models-json-key",
                        "api": "openai-completions",
                        "headers": {"X-Acme": "ACME_ENV"},
                        "models": [
                            {"id": "rocket-1", "reasoning": True,
                             "thinkingLevelMap": {"high": "high"}}
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    state = _state(tmp, custom_path, {"ACME_ENV": "acme-org"})
    spec = state.find("acme", "rocket-1")
    sent, _ = _construct_and_capture(state, spec, runtime_api_key=None, thinking_level="high")
    turn_ok = (
        sent["url"] == "https://acme.example/v1/chat/completions"
        and sent["body"]["model"] == "rocket-1"
        and sent["headers"]["Authorization"] == "Bearer models-json-key"
        and sent["headers"].get("X-Acme") == "acme-org"
        and sent["body"]["reasoning_effort"] == "high"
    )
    checks.append(Check("18_product_custom_turn", turn_ok, "custom provider turn uses catalog baseUrl/model/auth/headers/thinking"))

    # 18b: --api-key (runtime) reaches the outgoing Authorization header.
    sent_rt, _ = _construct_and_capture(state, spec, runtime_api_key="RUNTIME-KEY", thinking_level=None)
    auth_ok = sent_rt["headers"]["Authorization"] == "Bearer RUNTIME-KEY"
    checks.append(Check("18_product_runtime_auth", auth_ok, "--api-key reaches the request header"))

    # 18c: routing reaches the request body (OpenRouter provider param + Vercel
    # providerOptions.gateway).
    or_path = tmp / "prod_or.json"
    or_path.write_text(
        json.dumps(
            {
                "providers": {
                    "openrouter": {
                        "modelOverrides": {
                            "moonshotai/kimi-k2.6": {
                                "compat": {"openRouterRouting": {"order": ["fireworks"]}}
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    or_state = _state(tmp, or_path, {"OPENROUTER_API_KEY": "or-key"})
    or_spec = or_state.find("openrouter", "moonshotai/kimi-k2.6")
    or_sent, _ = _construct_and_capture(or_state, or_spec, runtime_api_key=None, thinking_level=None)

    vercel_path = tmp / "prod_vercel.json"
    vercel_path.write_text(
        json.dumps(
            {
                "providers": {
                    "vercel": {
                        "baseUrl": "https://ai-gateway.vercel.sh/v1",
                        "apiKey": "v-key",
                        "api": "openai-completions",
                        "models": [
                            {"id": "glm-5.1", "compat": {"vercelGatewayRouting": {"only": ["zai"], "order": ["zai"]}}}
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    v_state = _state(tmp, vercel_path, {})
    v_spec = v_state.find("vercel", "glm-5.1")
    v_sent, _ = _construct_and_capture(v_state, v_spec, runtime_api_key=None, thinking_level=None)
    routing_ok = (
        or_sent["body"].get("provider") == {"order": ["fireworks"]}
        and v_sent["body"].get("providerOptions") == {"gateway": {"only": ["zai"], "order": ["zai"]}}
    )
    checks.append(Check("18_product_routing", routing_ok, "OpenRouter provider param + Vercel providerOptions.gateway reach the body"))

    # 18d: OpenRouter thinking is the nested reasoning.effort, not reasoning_effort.
    or_think_sent, _ = _construct_and_capture(or_state, or_spec, runtime_api_key=None, thinking_level="high")
    # Off/unset on a reasoning-capable OpenRouter model disables reasoning at the
    # router with reasoning.effort = "none" (Pi openai-completions.ts:578-580),
    # rather than omitting the field.
    or_off_sent, _ = _construct_and_capture(or_state, or_spec, runtime_api_key=None, thinking_level=None)
    think_ok = (
        or_think_sent["body"].get("reasoning") == {"effort": "high"}
        and "reasoning_effort" not in or_think_sent["body"]
        and or_off_sent["body"].get("reasoning") == {"effort": "none"}
        and "reasoning_effort" not in or_off_sent["body"]
    )
    checks.append(Check("18_product_openrouter_thinking", think_ok, "OpenRouter thinking is nested reasoning.effort (on + off-state)"))

    # 18h: DeepSeek thinking format (openai-completions.ts:565-570). A reasoning
    # DeepSeek model emits thinking:{type:"enabled"} + top-level reasoning_effort
    # on-state, and thinking:{type:"disabled"} with no reasoning_effort off/unset.
    ds_path = tmp / "prod_deepseek.json"
    ds_path.write_text(
        json.dumps(
            {
                "providers": {
                    "deepseek": {
                        "baseUrl": "https://api.deepseek.com/v1",
                        "apiKey": "ds-key",
                        "api": "openai-completions",
                        "models": [
                            {"id": "deepseek-reasoner", "reasoning": True,
                             "thinkingLevelMap": {"high": "high"}}
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    ds_state = _state(tmp, ds_path, {"DEEPSEEK_API_KEY": "ds-key"})
    ds_spec = ds_state.find("deepseek", "deepseek-reasoner")
    ds_on, _ = _construct_and_capture(ds_state, ds_spec, runtime_api_key=None, thinking_level="high")
    ds_off, _ = _construct_and_capture(ds_state, ds_spec, runtime_api_key=None, thinking_level=None)
    deepseek_ok = (
        ds_on["body"].get("thinking") == {"type": "enabled"}
        and ds_on["body"].get("reasoning_effort") == "high"
        and ds_off["body"].get("thinking") == {"type": "disabled"}
        and "reasoning_effort" not in ds_off["body"]
    )
    checks.append(Check("18_product_deepseek_thinking", deepseek_ok, "DeepSeek thinking:{type} (+ reasoning_effort on-state)"))

    # 18i: Together thinking format (openai-completions.ts:586-594). A reasoning
    # Together model emits reasoning:{enabled:true} on-state and
    # reasoning:{enabled:false} off/unset. Together auto-detects
    # supportsReasoningEffort=False (isTogether), so reasoning_effort is omitted in
    # both states.
    tg_path = tmp / "prod_together.json"
    tg_path.write_text(
        json.dumps(
            {
                "providers": {
                    "together": {
                        "baseUrl": "https://api.together.xyz/v1",
                        "apiKey": "tg-key",
                        "api": "openai-completions",
                        "models": [
                            {"id": "deepseek-ai/DeepSeek-R1", "reasoning": True,
                             "thinkingLevelMap": {"high": "high"}}
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    tg_state = _state(tmp, tg_path, {"TOGETHER_API_KEY": "tg-key"})
    tg_spec = tg_state.find("together", "deepseek-ai/DeepSeek-R1")
    tg_on, _ = _construct_and_capture(tg_state, tg_spec, runtime_api_key=None, thinking_level="high")
    tg_off, _ = _construct_and_capture(tg_state, tg_spec, runtime_api_key=None, thinking_level=None)
    together_ok = (
        tg_on["body"].get("reasoning") == {"enabled": True}
        and "reasoning_effort" not in tg_on["body"]
        and tg_off["body"].get("reasoning") == {"enabled": False}
        and "reasoning_effort" not in tg_off["body"]
    )
    checks.append(Check("18_product_together_thinking", together_ok, "Together reasoning:{enabled} (no reasoning_effort, auto supportsReasoningEffort=False)"))

    # 18j: Z.ai thinking format (openai-completions.ts:556-557). A reasoning Z.ai
    # model emits a bare enable_thinking=true on-state and enable_thinking=false
    # off/unset. The zai branch never emits reasoning_effort and never consults
    # supportsReasoningEffort.
    zai_path = tmp / "prod_zai.json"
    zai_path.write_text(
        json.dumps(
            {
                "providers": {
                    "zai": {
                        "baseUrl": "https://api.z.ai/api/paas/v4",
                        "apiKey": "zai-key",
                        "api": "openai-completions",
                        "models": [
                            {"id": "glm-4.6", "reasoning": True,
                             "thinkingLevelMap": {"high": "high"}}
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    zai_state = _state(tmp, zai_path, {"ZAI_API_KEY": "zai-key"})
    zai_spec = zai_state.find("zai", "glm-4.6")
    zai_on, _ = _construct_and_capture(zai_state, zai_spec, runtime_api_key=None, thinking_level="high")
    zai_off, _ = _construct_and_capture(zai_state, zai_spec, runtime_api_key=None, thinking_level=None)
    zai_ok = (
        zai_on["body"].get("enable_thinking") is True
        and "reasoning_effort" not in zai_on["body"]
        and zai_off["body"].get("enable_thinking") is False
        and "reasoning_effort" not in zai_off["body"]
    )
    checks.append(Check("18_product_zai_thinking", zai_ok, "Z.ai enable_thinking boolean (no reasoning_effort)"))

    # 18e: legacy hardcoded path is bypassed — a models.json provider-level
    # baseUrl override on a built-in provider wins over the adapter default URL.
    bypass_path = tmp / "prod_bypass.json"
    bypass_path.write_text(
        json.dumps({"providers": {"openai-completions": {"baseUrl": "https://catalog-override.example/v1"}}}),
        encoding="utf-8",
    )
    bypass_state = _state(tmp, bypass_path, {"OPENAI_API_KEY": "k"})
    bypass_spec = bypass_state.find("openai-completions", "gpt-4o-mini")
    bypass_sent, _ = _construct_and_capture(bypass_state, bypass_spec, runtime_api_key=None, thinking_level=None)
    from pipy_harness.native.openai_completions_provider import OPENAI_CHAT_COMPLETIONS_URL

    bypass_ok = (
        bypass_sent["url"] == "https://catalog-override.example/v1/chat/completions"
        and bypass_sent["url"] != OPENAI_CHAT_COMPLETIONS_URL
    )
    checks.append(Check("18_product_legacy_bypass", bypass_ok, "catalog baseUrl wins over the legacy hardcoded adapter URL"))

    # 18f: no secret leaks into the turn result (final_text/metadata).
    _, leak_result = _construct_and_capture(state, spec, runtime_api_key="RUNTIME-KEY", thinking_level="high")
    leak_dump = json.dumps(
        {"final_text": leak_result.final_text, "metadata": leak_result.metadata, "model": leak_result.model_id}
    )
    no_leak = not any(s in leak_dump for s in ("models-json-key", "RUNTIME-KEY", "Bearer", "acme-org"))
    checks.append(Check("18_product_no_secret_in_result", no_leak, "turn result carries no secret/Authorization"))

    # 18g: the actual product boundary (NativeReplProviderState.provider_for /
    # current_provider, used by the REPL tool-loop) — not just build_provider —
    # constructs the catalog adapter. A regression to the legacy factory here
    # would fail this check (and the secret-bearing fields are repr-hidden).
    from pipy_harness.native.openai_completions_provider import (
        OpenAIChatCompletionsProvider,
    )
    from pipy_harness.native.repl_state import (
        NativeModelSelection,
        NativeReplProviderState,
    )

    def _no_legacy(_sel):
        raise AssertionError("legacy factory must not be used for a catalog model")

    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("acme", "rocket-1"),
        provider_factory=_no_legacy,
        catalog_state=state,
        thinking_level="high",
        persist_defaults=False,
    )
    product_provider = repl_state.current_provider()
    product_ok = (
        isinstance(product_provider, OpenAIChatCompletionsProvider)
        and product_provider.endpoint == "https://acme.example/v1/chat/completions"
        and product_provider.api_key == "models-json-key"
        and product_provider.model_id == "rocket-1"
        and product_provider.reasoning_effort == "high"
        and "models-json-key" not in repr(product_provider)
    )
    checks.append(Check("18_product_boundary_uses_catalog", product_ok, "current_provider/provider_for constructs from the catalog (not legacy); repr hides secrets"))


def _check_tier1_construction(checks, tmp: Path):
    # Item 20: catalog construction for the Tier 1 api-key families
    # (anthropic-messages, openai-responses, mistral). Each custom models.json
    # provider runs a real (fake-HTTP) turn whose request uses the catalog
    # baseUrl-derived endpoint, model id, the family's native auth header, merged
    # headers, and the mapped thinking in that family's native body key.

    # 20a: anthropic-messages -> x-api-key + thinking.budget_tokens, /v1/messages.
    a_path = tmp / "tier1_anthropic.json"
    a_path.write_text(
        json.dumps(
            {
                "providers": {
                    "acme-claude": {
                        "baseUrl": "https://acme.example",
                        "apiKey": "amk",
                        "api": "anthropic-messages",
                        "headers": {"X-Acme": "1"},
                        "models": [
                            {"id": "claude-x", "reasoning": True,
                             "thinkingLevelMap": {"high": "high"}}
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    a_state = _state(tmp, a_path, {})
    a_spec = a_state.find("acme-claude", "claude-x")
    a_sent, _ = _construct_and_capture(a_state, a_spec, runtime_api_key=None, thinking_level="high")
    anthropic_ok = (
        a_sent["url"] == "https://acme.example/v1/messages"
        and a_sent["body"]["model"] == "claude-x"
        and a_sent["headers"]["x-api-key"] == "amk"
        and "Authorization" not in a_sent["headers"]
        and a_sent["headers"].get("X-Acme") == "1"
        and a_sent["body"]["thinking"]
        == {"type": "enabled", "budget_tokens": 16384, "display": "summarized"}
        and "output_config" not in a_sent["body"]
    )
    checks.append(Check("20_anthropic_messages_construction", anthropic_ok, "anthropic-messages: catalog baseUrl/x-api-key/headers/thinking budget"))

    # 20a': anthropic adaptive model -> adaptive thinking + output_config.effort
    # (Pi's compat.forceAdaptiveThinking set), not the budget path.
    aa_path = tmp / "tier1_anthropic_adaptive.json"
    aa_path.write_text(
        json.dumps(
            {
                "providers": {
                    "acme-claude": {
                        "baseUrl": "https://acme.example",
                        "apiKey": "amk",
                        "api": "anthropic-messages",
                        "models": [
                            {"id": "claude-opus-4-8", "reasoning": True,
                             "thinkingLevelMap": {"high": "high"}}
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    aa_state = _state(tmp, aa_path, {})
    aa_spec = aa_state.find("acme-claude", "claude-opus-4-8")
    aa_sent, _ = _construct_and_capture(aa_state, aa_spec, runtime_api_key=None, thinking_level="high")
    anthropic_adaptive_ok = (
        aa_sent["body"]["thinking"] == {"type": "adaptive", "display": "summarized"}
        and aa_sent["body"]["output_config"] == {"effort": "high"}
    )
    checks.append(Check("20_anthropic_adaptive_thinking", anthropic_adaptive_ok, "anthropic-messages: adaptive models use type:adaptive + output_config.effort"))

    # 20b: openai-responses -> Authorization Bearer + reasoning.effort, /responses.
    r_path = tmp / "tier1_responses.json"
    r_path.write_text(
        json.dumps(
            {
                "providers": {
                    "acme-oai": {
                        "baseUrl": "https://oai.example/v1",
                        "apiKey": "ork",
                        "api": "openai-responses",
                        "models": [
                            {"id": "o-pro", "reasoning": True,
                             "thinkingLevelMap": {"high": "high"}}
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    r_state = _state(tmp, r_path, {})
    r_spec = r_state.find("acme-oai", "o-pro")
    r_sent, _ = _construct_and_capture(r_state, r_spec, runtime_api_key=None, thinking_level="high")
    responses_ok = (
        r_sent["url"] == "https://oai.example/v1/responses"
        and r_sent["body"]["model"] == "o-pro"
        and r_sent["headers"]["Authorization"] == "Bearer ork"
        and r_sent["body"]["reasoning"] == {"effort": "high"}
    )
    checks.append(Check("20_openai_responses_construction", responses_ok, "openai-responses: catalog baseUrl/Bearer/reasoning.effort"))

    # 20c: mistral -> Authorization Bearer, /chat/completions.
    m_path = tmp / "tier1_mistral.json"
    m_path.write_text(
        json.dumps(
            {
                "providers": {
                    "acme-mistral": {
                        "baseUrl": "https://mistral.example/v1",
                        "apiKey": "mmk",
                        "api": "mistral",
                        "models": [{"id": "mix-1"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    m_state = _state(tmp, m_path, {})
    m_spec = m_state.find("acme-mistral", "mix-1")
    m_sent, _ = _construct_and_capture(m_state, m_spec, runtime_api_key=None, thinking_level=None)
    mistral_ok = (
        m_sent["url"] == "https://mistral.example/v1/chat/completions"
        and m_sent["body"]["model"] == "mix-1"
        and m_sent["headers"]["Authorization"] == "Bearer mmk"
    )
    checks.append(Check("20_mistral_construction", mistral_ok, "mistral: catalog baseUrl/Bearer/chat-completions"))

    # 20d: the product boundary (current_provider) constructs a built-in
    # anthropic catalog model — not the legacy factory.
    from pipy_harness.native.anthropic_provider import AnthropicProvider
    from pipy_harness.native.repl_state import (
        NativeModelSelection,
        NativeReplProviderState,
    )

    def _no_legacy(_sel):
        raise AssertionError("legacy factory must not be used for a catalog model")

    builtin_state = _state(tmp, tmp / "tier1_builtin_missing.json", {"ANTHROPIC_API_KEY": "envk"})
    repl_state = NativeReplProviderState(
        selection=NativeModelSelection("anthropic", "claude-opus-4-7"),
        provider_factory=_no_legacy,
        catalog_state=builtin_state,
        thinking_level="xhigh",
        persist_defaults=False,
    )
    provider = repl_state.current_provider()
    boundary_ok = (
        isinstance(provider, AnthropicProvider)
        and provider.endpoint == "https://api.anthropic.com/v1/messages"
        and provider.api_key == "envk"
        and provider.reasoning_effort == "xhigh"
        and "envk" not in repr(provider)
    )
    checks.append(Check("20_tier1_boundary_uses_catalog", boundary_ok, "current_provider constructs a built-in anthropic catalog model (not legacy)"))

    # 20e: auth fail-closed for a Tier 1 family (authHeader set, no resolvable
    # key) -> build_provider returns a fail-closed provider, not None (which
    # would silently fall back to the legacy factory).
    fc_spec = build_builtin_catalog().find("anthropic", "claude-opus-4-7")
    fc_resolved = resolve_construction(
        fc_spec,
        store=AuthStore(path=tmp / "tier1_fc_auth.json"),
        env={},
        runtime_api_key=None,
        models_json_auth=ProviderAuthRequestConfig(auth_header=True),
        thinking_level=None,
    )
    fc_provider = build_provider(fc_resolved, http_client=None)
    fc_result = (
        fc_provider.complete(_provider_request(Path("."), "anthropic", "claude-opus-4-7"))
        if fc_provider
        else None
    )
    failclosed_ok = (
        fc_resolved.ok is False
        and fc_provider is not None
        and fc_result is not None
        and fc_result.error_type == "CatalogAuthError"
    )
    checks.append(Check("20_tier1_fail_closed", failclosed_ok, "Tier 1 authHeader with no key fails closed (not legacy fallback)"))


def _check_tier2_construction(checks, tmp: Path):
    # Item 21: catalog construction for the Tier 2 composed-endpoint families.
    # google-generative-ai (model-in-path + ?key=), azure-openai-responses
    # (/openai/v1 URL + api-version=v1 + deployment body model, api-key header),
    # cloudflare-workers-ai (account
    # id substituted into the base_url via {ENV} + OpenAI-compatible body).

    # 21a: google-generative-ai built-in row, key from env -> URL ?key=, no auth
    # header (Google authenticates via the query param). gemini-2.5-pro is a
    # reasoning model, so thinking_level=None resolves to Pi's per-model
    # *disabled* thinkingConfig (budget 0, no includeThoughts).
    g_state = _state(tmp, tmp / "tier2_google_missing.json", {"GEMINI_API_KEY": "gk"})
    g_spec = g_state.find("google", "gemini-2.5-pro")
    g_sent, _ = _construct_and_capture(g_state, g_spec, runtime_api_key=None, thinking_level=None)
    google_ok = (
        g_sent["url"]
        == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent?key=gk"
        and "Authorization" not in g_sent["headers"]
        and bool(g_sent["body"].get("contents"))
        and g_sent["body"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    )
    checks.append(Check("21_google_construction", google_ok, "google-generative-ai: catalog model-in-path URL + ?key= + disabled thinkingConfig"))

    # 21a': google thinking enabled -> per-model token budget under
    # generationConfig.thinkingConfig with includeThoughts (Pi's getGoogleBudget).
    g_think_sent, _ = _construct_and_capture(g_state, g_spec, runtime_api_key=None, thinking_level="high")
    google_thinking_ok = (
        g_think_sent["body"]["generationConfig"]["thinkingConfig"]
        == {"includeThoughts": True, "thinkingBudget": 32768}
    )
    checks.append(Check("21_google_thinking_config", google_thinking_ok, "google-generative-ai: per-model thinkingConfig token budget + includeThoughts"))

    # 21b: azure-openai-responses custom provider -> /openai/v1 URL with
    # api-version=v1, deployment as the body model, api-key header,
    # reasoning.effort thinking.
    az_path = tmp / "tier2_azure.json"
    az_path.write_text(
        json.dumps(
            {
                "providers": {
                    "acme-azure": {
                        "baseUrl": "https://acme.openai.azure.com",
                        "apiKey": "azk",
                        "api": "azure-openai-responses",
                        "models": [
                            {"id": "gpt-x", "reasoning": True,
                             "thinkingLevelMap": {"high": "high"}}
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    az_state = _state(tmp, az_path, {})
    az_spec = az_state.find("acme-azure", "gpt-x")
    az_sent, _ = _construct_and_capture(az_state, az_spec, runtime_api_key=None, thinking_level="high")
    azure_ok = (
        az_sent["url"]
        == "https://acme.openai.azure.com/openai/v1/responses?api-version=v1"
        and az_sent["body"]["model"] == "gpt-x"
        and az_sent["headers"]["api-key"] == "azk"
        and "Authorization" not in az_sent["headers"]
        and az_sent["body"]["reasoning"] == {"effort": "high"}
    )
    checks.append(Check("21_azure_construction", azure_ok, "azure-openai-responses: /openai/v1 URL + api-version=v1 + deployment body model + api-key header + reasoning.effort"))

    # 21c: cloudflare-workers-ai built-in row -> account id substituted into the
    # base_url, /chat/completions appended, Bearer token.
    cf_state = _state(
        tmp,
        tmp / "tier2_cf_missing.json",
        {"CLOUDFLARE_ACCOUNT_ID": "acct-9", "CLOUDFLARE_API_KEY": "cfk"},
    )
    cf_spec = cf_state.find("cloudflare", "@cf/meta/llama-3.3-70b-instruct")
    cf_sent, _ = _construct_and_capture(cf_state, cf_spec, runtime_api_key=None, thinking_level=None)
    cloudflare_ok = (
        cf_sent["url"]
        == "https://api.cloudflare.com/client/v4/accounts/acct-9/ai/v1/chat/completions"
        and cf_sent["headers"]["Authorization"] == "Bearer cfk"
        and cf_sent["body"]["model"] == "@cf/meta/llama-3.3-70b-instruct"
    )
    checks.append(Check("21_cloudflare_construction", cloudflare_ok, "cloudflare-workers-ai: {ENV} account substitution + /chat/completions + Bearer"))

    # 21d: cloudflare with a missing CLOUDFLARE_ACCOUNT_ID fails closed (the
    # base_url {ENV} placeholder cannot resolve), not a legacy fallback.
    cf_fc_state = _state(tmp, tmp / "tier2_cf_fc.json", {"CLOUDFLARE_API_KEY": "cfk"})
    cf_fc_spec = cf_fc_state.find("cloudflare", "@cf/meta/llama-3.3-70b-instruct")
    cf_fc_resolved = resolve_construction(
        cf_fc_spec,
        store=cf_fc_state.auth_store,
        env=cf_fc_state._env(),
        runtime_api_key=None,
        models_json_auth=cf_fc_state._models_json_auth("cloudflare"),
        thinking_level=None,
    )
    cf_fc_provider = build_provider(cf_fc_resolved, http_client=None)
    cf_failclosed_ok = (
        cf_fc_resolved.ok is False
        and cf_fc_provider is not None
        and cf_fc_provider.complete(
            _provider_request(Path("."), "cloudflare", "@cf/meta/llama-3.3-70b-instruct")
        ).error_type == "CatalogAuthError"
    )
    checks.append(Check("21_cloudflare_missing_account_fails_closed", cf_failclosed_ok, "cloudflare missing CLOUDFLARE_ACCOUNT_ID fails closed"))

    # 21e: product boundary constructs a built-in azure catalog model (not legacy).
    from pipy_harness.native.azure_openai_provider import AzureOpenAIResponsesProvider
    from pipy_harness.native.repl_state import (
        NativeModelSelection,
        NativeReplProviderState,
    )

    def _no_legacy(_sel):
        raise AssertionError("legacy factory must not be used for a catalog model")

    az_boundary_state = _state(
        tmp, tmp / "tier2_azure_boundary.json", {"AZURE_OPENAI_API_KEY": "azk2"}
    )
    az_repl = NativeReplProviderState(
        selection=NativeModelSelection("azure-openai", "gpt-5.4"),
        provider_factory=_no_legacy,
        catalog_state=az_boundary_state,
        thinking_level="high",
        persist_defaults=False,
    )
    az_provider = az_repl.current_provider()
    azure_boundary_ok = (
        isinstance(az_provider, AzureOpenAIResponsesProvider)
        and az_provider.endpoint_url == "https://azure-openai.example"
        and az_provider.api_key == "azk2"
        and az_provider.reasoning_effort == "high"
        and "azk2" not in repr(az_provider)
    )
    checks.append(Check("21_tier2_boundary_uses_catalog", azure_boundary_ok, "current_provider constructs a built-in azure catalog model (not legacy)"))


def _check_tier3_construction(checks, tmp: Path):
    # Item 22: catalog construction for the Tier 3 IAM/OAuth families. Auth
    # (AWS SigV4 / GCP ADC / Codex OAuth) and the region/project endpoint stay
    # env-resolved by the adapter; catalog construction injects model_id +
    # provider_name + headers + thinking (bedrock Anthropic budget, codex
    # reasoning.effort; vertex per-model generationConfig.thinkingConfig — see
    # 22f). Bedrock's resolved api_key is
    # NOT forwarded. google-vertex's IS forwarded so the adapter can use Pi's
    # Vertex Express api-key mode (global host + x-goog-api-key); see 22c/22e.
    from datetime import UTC, datetime

    from pipy_harness.native.bedrock_provider import AmazonBedrockProvider
    from pipy_harness.native.google_vertex_provider import GoogleVertexProvider
    from pipy_harness.native.repl_state import (
        NativeModelSelection,
        NativeReplProviderState,
    )

    # 22a: bedrock built-in row -> AmazonBedrockProvider with thinking threaded.
    bd_state = _state(tmp, tmp / "tier3_bd_missing.json", {"AWS_ACCESS_KEY_ID": "ak", "AWS_SECRET_ACCESS_KEY": "sk"})
    bd_spec = bd_state.find("amazon-bedrock", "us.anthropic.claude-opus-4-6-v1")
    bd_resolved = resolve_construction(
        bd_spec,
        store=bd_state.auth_store,
        env=bd_state._env(),
        runtime_api_key=None,
        models_json_auth=bd_state._models_json_auth("amazon-bedrock"),
        thinking_level="high",
    )
    bd_provider = build_provider(bd_resolved, http_client=None)
    bedrock_ok = (
        isinstance(bd_provider, AmazonBedrockProvider)
        and bd_provider.model_id == "us.anthropic.claude-opus-4-6-v1"
        and bd_provider.reasoning_effort == "high"
    )
    checks.append(Check("22_bedrock_construction", bedrock_ok, "amazon-bedrock: catalog construction threads model id + thinking effort"))

    # 22b: bedrock thinking reaches the signed request body. opus-4-6 is an
    # adaptive Claude model, so it uses adaptive thinking + output_config.effort
    # (Pi's supportsAdaptiveThinking), not the budget path.
    bd_http = _CapturingHTTP()
    bd_signed = AmazonBedrockProvider(
        model_id="us.anthropic.claude-opus-4-6-v1",
        region="us-east-1",
        access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        http_client=bd_http,
        reasoning_effort="high",
        _clock=lambda: datetime(2015, 8, 30, 12, 36, 0, tzinfo=UTC),
    )
    bd_signed.complete(_provider_request(Path("."), "amazon-bedrock", "us.anthropic.claude-opus-4-6-v1"))
    bd_sent = bd_http.requests[-1]
    bedrock_body_ok = (
        bd_sent["url"]
        == "https://bedrock-runtime.us-east-1.amazonaws.com/model/us.anthropic.claude-opus-4-6-v1/invoke"
        and bd_sent["body"]["thinking"] == {
            "type": "adaptive",
            "display": "summarized",
        }
        and bd_sent["body"]["output_config"] == {"effort": "high"}
        and bd_sent["headers"]["Authorization"].startswith("AWS4-HMAC-SHA256")
    )
    checks.append(Check("22_bedrock_thinking_body", bedrock_body_ok, "amazon-bedrock: adaptive thinking (display + output_config.effort) reaches the SigV4-signed body"))

    # 22c: vertex built-in row -> GoogleVertexProvider (auth/project env-resolved).
    vx_state = _state(tmp, tmp / "tier3_vx_missing.json", {"GOOGLE_CLOUD_API_KEY": "vk"})
    vx_spec = vx_state.find("google-vertex", "gemini-2.5-pro")
    vx_resolved = resolve_construction(
        vx_spec,
        store=vx_state.auth_store,
        env=vx_state._env(),
        runtime_api_key=None,
        models_json_auth=vx_state._models_json_auth("google-vertex"),
        thinking_level=None,
    )
    vx_provider = build_provider(vx_resolved, http_client=None)
    vertex_ok = (
        isinstance(vx_provider, GoogleVertexProvider)
        and vx_provider.model_id == "gemini-2.5-pro"
        and vx_provider.provider_name == "google-vertex"
        # The resolved Vertex Express api key is forwarded to the adapter.
        and vx_provider.api_key == "vk"
    )
    checks.append(Check("22_vertex_construction", vertex_ok, "google-vertex: catalog construction forwards the Vertex Express api key"))

    # 22e: the forwarded Vertex Express api key reaches the request as Pi's
    # express shape — global host (no project/location), x-goog-api-key header.
    class _VertexCapturingHTTP:
        def __init__(self):
            self.requests = []

        def post_json(self, url, *, headers, body, timeout_seconds, cancel_token=None):
            self.requests.append({"url": url, "headers": dict(headers), "body": dict(body)})
            return JsonResponse(
                status_code=200,
                body={
                    "candidates": [
                        {"content": {"parts": [{"text": "OK"}]}, "finishReason": "STOP"}
                    ],
                    "usageMetadata": {},
                },
            )

    vx_http = _VertexCapturingHTTP()
    vx_express = build_provider(vx_resolved, http_client=vx_http)
    vx_express.complete(_provider_request(Path("."), "google-vertex", "gemini-2.5-pro"))
    vx_sent = vx_http.requests[-1]
    vertex_express_ok = (
        vx_sent["url"]
        == "https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-2.5-pro:generateContent"
        and vx_sent["headers"].get("x-goog-api-key") == "vk"
        and "Authorization" not in vx_sent["headers"]
    )
    checks.append(Check("22_vertex_express_request", vertex_express_ok, "google-vertex: Vertex Express api key produces the global host + x-goog-api-key request"))

    # 22f: catalog-resolved thinking reaches the vertex request body as Pi's
    # per-model generationConfig.thinkingConfig (2.5-pro high -> budget 32768 +
    # includeThoughts), proving construction forwards reasoning_effort.
    vx_think_resolved = resolve_construction(
        vx_spec,
        store=vx_state.auth_store,
        env=vx_state._env(),
        runtime_api_key=None,
        models_json_auth=vx_state._models_json_auth("google-vertex"),
        thinking_level="high",
    )
    vx_think_http = _VertexCapturingHTTP()
    vx_think = build_provider(vx_think_resolved, http_client=vx_think_http)
    vx_think.complete(_provider_request(Path("."), "google-vertex", "gemini-2.5-pro"))
    vx_think_body = vx_think_http.requests[-1]["body"]
    vertex_thinking_ok = (
        vx_think.reasoning_effort == "high"
        and vx_think_body.get("generationConfig", {}).get("thinkingConfig")
        == {"includeThoughts": True, "thinkingBudget": 32768}
    )
    checks.append(Check("22_vertex_thinking_config", vertex_thinking_ok, "google-vertex: per-model generationConfig.thinkingConfig (budget + includeThoughts) reaches the request body"))

    # 22d: codex is deliberately NOT catalog-constructed (the legacy factory
    # injects a settings-derived RetryPolicy that catalog construction would
    # drop); build_provider returns None so it falls back to the legacy factory.
    cx_state = _state(tmp, tmp / "tier3_cx_missing.json", {})
    cx_spec = cx_state.find("openai-codex", "gpt-5.5")
    cx_resolved = resolve_construction(
        cx_spec,
        store=cx_state.auth_store,
        env=cx_state._env(),
        runtime_api_key=None,
        models_json_auth=cx_state._models_json_auth("openai-codex"),
        thinking_level="high",
    )
    codex_ok = build_provider(cx_resolved, http_client=None) is None
    checks.append(Check("22_codex_stays_on_legacy", codex_ok, "openai-codex-responses stays on the legacy factory (settings-derived RetryPolicy)"))

    # 22e: product boundary constructs a built-in bedrock catalog model (not legacy).
    def _no_legacy(_sel):
        raise AssertionError("legacy factory must not be used for a catalog model")

    bnd_state = _state(tmp, tmp / "tier3_boundary.json", {"AWS_ACCESS_KEY_ID": "ak", "AWS_SECRET_ACCESS_KEY": "sk"})
    bnd_repl = NativeReplProviderState(
        selection=NativeModelSelection("amazon-bedrock", "us.anthropic.claude-opus-4-6-v1"),
        provider_factory=_no_legacy,
        catalog_state=bnd_state,
        thinking_level="high",
        persist_defaults=False,
    )
    bnd_provider = bnd_repl.current_provider()
    boundary_ok = (
        isinstance(bnd_provider, AmazonBedrockProvider)
        and bnd_provider.reasoning_effort == "high"
    )
    checks.append(Check("22_tier3_boundary_uses_catalog", boundary_ok, "current_provider constructs a built-in bedrock catalog model (not legacy)"))


def _check_run_path_construction(checks, tmp: Path):
    # Item 23: the one-shot ``pipy run`` boundary constructs its provider via
    # catalog construction (the same boundary as the REPL), not the legacy
    # factory. A runtime --api-key + --thinking must reach the constructed
    # adapter; the legacy factory would ignore both.
    from pipy_harness.cli import _run_provider_for_selection
    from pipy_harness.native.anthropic_provider import AnthropicProvider
    from pipy_harness.native.repl_state import NativeModelSelection

    provider = _run_provider_for_selection(
        NativeModelSelection("anthropic", "claude-opus-4-7"),
        thinking="xhigh",
        api_key="RUNTIME-RUN-KEY",
    )
    run_ok = (
        isinstance(provider, AnthropicProvider)
        and provider.api_key == "RUNTIME-RUN-KEY"
        and provider.reasoning_effort == "xhigh"
        and "RUNTIME-RUN-KEY" not in repr(provider)
    )
    checks.append(Check("23_run_path_uses_catalog", run_ok, "pipy run one-shot construction uses catalog construction (honors --api-key/--thinking)"))


def _check_startup_cli_resolution(checks, tmp: Path):
    # Item 24: startup --native-provider/--native-model resolution accepts custom
    # models.json providers and bare model refs (Pi's resolveCliModel), and
    # rejects unknown providers — matching mid-session /model. Previously a bare
    # --native-model became fake/<ref> and a custom provider name was rejected by
    # the argparse choices.
    from pipy_harness.native.catalog import NativeModelCost, NativeModelSpec
    from pipy_harness.native.repl_state import (
        NativeModelSelection,
        default_selection_for,
        resolve_cli_selection,
    )

    rows = build_builtin_catalog().get_all()
    custom_rows = list(rows) + [
        NativeModelSpec(
            provider_name="acme",
            model_id="rocket-1",
            display_name="Acme Rocket 1",
            api="openai-completions",
            base_url="https://acme.example/v1",
            cost=NativeModelCost(),
        )
    ]

    # 24a: a bare --native-model infers its provider (not fake/<ref>).
    sel_bare, err_bare = resolve_cli_selection(None, "claude-opus-4-7", rows)
    bare_ok = err_bare is None and sel_bare == NativeModelSelection(
        "anthropic", "claude-opus-4-7"
    )
    checks.append(Check("24_startup_bare_model", bare_ok, "bare --native-model resolves its provider (not fake/<ref>)"))

    # 24b: a custom models.json provider name is accepted at startup.
    sel_custom, err_custom = resolve_cli_selection("acme", "rocket-1", custom_rows)
    custom_ok = err_custom is None and sel_custom == NativeModelSelection(
        "acme", "rocket-1"
    )
    checks.append(Check("24_startup_custom_provider", custom_ok, "custom models.json provider name accepted at startup"))

    # 24c: provider-only resolves the provider's default catalog model.
    sel_prov, err_prov = resolve_cli_selection("anthropic", None, rows)
    provider_only_ok = (
        err_prov is None
        and sel_prov is not None
        and sel_prov.provider_name == "anthropic"
        and any(
            r.provider_name == "anthropic" and r.model_id == sel_prov.model_id
            for r in rows
        )
    )
    checks.append(Check("24_startup_provider_default", provider_only_ok, "provider-only --native-provider resolves the catalog default model"))

    # 24d: an unknown provider errors clearly (no argparse choices guard).
    sel_unknown, err_unknown = resolve_cli_selection("nope", None, rows)
    unknown_ok = (
        sel_unknown is None
        and err_unknown is not None
        and 'Unknown provider "nope"' in err_unknown
    )
    checks.append(Check("24_startup_unknown_provider", unknown_ok, "unknown --native-provider errors clearly"))

    # 24e: default_selection_for(rows=...) raises ValueError on an unknown provider.
    raised = False
    try:
        default_selection_for(native_provider="nope", native_model=None, rows=rows)
    except ValueError:
        raised = True
    checks.append(Check("24_startup_default_selection_raises", raised, "default_selection_for(rows) raises on an unknown provider"))


def _check_no_secret_leak(checks, tmp: Path):
    # Configure secrets on every auth channel, then confirm that the actual
    # archive-/display-facing surfaces the catalog produces carry no secret
    # material — even though resolve_request_auth() (a request-time surface, not
    # archived) does resolve the secrets into the live request.
    secrets = ("SECRET-TOKEN", "SECRET-REFRESH", "SECRET-KEY", "SECRET-HDR", "Bearer ")
    store = AuthStore(path=tmp / "auth_leak.json")
    store.set(
        "anthropic",
        {"type": "oauth", "access": "SECRET-TOKEN", "refresh": "SECRET-REFRESH", "expires": 9999999999000},
    )
    models_path = tmp / "leak_models.json"
    models_path.write_text(
        json.dumps(
            {
                "providers": {
                    "ds4": {
                        "baseUrl": "http://127.0.0.1:8000/v1",
                        "apiKey": "SECRET-KEY",
                        "authHeader": True,
                        "headers": {"X-Secret": "SECRET-HDR"},
                        "api": "openai-completions",
                        "models": [{"id": "deepseek-v4-flash"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    state = ProviderCatalogState(
        models_json_path=models_path,
        auth_store=store,
        env={},
        openai_codex_auth_path=tmp / "no-codex.json",
    )

    # request-time resolution DOES surface the secret (proves the fixture is
    # real), but this result is never written to an archive.
    resolved = resolve_request_auth(
        "ds4",
        store=store,
        env={},
        models_json_config=ProviderAuthRequestConfig(
            api_key="SECRET-KEY", auth_header=True, headers={"X-Secret": "SECRET-HDR"}
        ),
    )

    # Archive-/display-facing surfaces: the merged catalog rows and the actual
    # --list-models output. None may contain any secret.
    rows_dump = json.dumps(
        [
            {
                "provider": r.provider_name,
                "model": r.model_id,
                "api": r.api,
                "base_url": r.base_url,
                "headers": dict(r.headers) if r.headers else None,
                "compat": r.compat,
            }
            for r in state.get_all()
        ]
    )
    list_models_output = format_list_models(
        state.get_available(), search=None, load_error=state.error
    )
    archive_surfaces = rows_dump + "\n" + list_models_output
    leaked = any(s in archive_surfaces for s in secrets)
    fixture_real = resolved.ok and resolved.api_key == "SECRET-KEY" and resolved.headers.get(
        "Authorization"
    ) == "Bearer SECRET-KEY"
    ok = fixture_real and not leaked
    checks.append(
        Check(
            "19_no_secret_in_archive",
            ok,
            "catalog rows + --list-models output carry no secret/header/Authorization",
        )
    )


def _write_extension(workspace: Path, name: str, body: str) -> None:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text(body, encoding="utf-8")


def _check_extension_provider_catalog_wiring(checks, tmp: Path):
    workspace = tmp / "extension-provider-catalog"
    workspace.mkdir()
    _write_extension(
        workspace,
        "provider_ext",
        "from pipy_harness.extensions import ExtensionProvider\n"
        "from pipy_harness.models import HarnessStatus\n"
        "from pipy_harness.native.models import ProviderResult\n"
        "from datetime import datetime, timezone\n"
        "class _Port:\n"
        "    def __init__(self, ctx): self._ctx = ctx\n"
        "    @property\n"
        "    def name(self): return self._ctx.provider_name\n"
        "    @property\n"
        "    def model_id(self): return self._ctx.model_id\n"
        "    @property\n"
        "    def supports_tool_calls(self): return True\n"
        "    def complete(self, request, **kwargs):\n"
        "        now = datetime(2026, 6, 15, tzinfo=timezone.utc)\n"
        "        return ProviderResult(status=HarnessStatus.SUCCEEDED,\n"
        "            provider_name=self.name, model_id=self.model_id,\n"
        "            started_at=now, ended_at=now, final_text='ok', tool_calls=())\n"
        "def activate(api):\n"
        "    api.register_provider(ExtensionProvider(name='extcat',\n"
        "        default_model='pro', models=('mini','pro'), factory=lambda ctx: _Port(ctx)))\n",
    )
    activated = activate_extensions(
        discover_extensions(
            workspace,
            config_home_env={"PIPY_CONFIG_HOME": str(tmp / "nocfg")},
            home_dir=workspace,
        )
    )
    providers: tuple[RegisteredProvider, ...] = extension_providers(activated)
    state = ProviderCatalogState(
        models_json_path=tmp / "absent-models.json",
        auth_store=AuthStore(path=tmp / "auth.json"),
        env={},
        openai_codex_auth_path=tmp / "no-codex.json",
    )
    state.set_extension_provider_contributions(providers, ())
    repl = NativeReplProviderState(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
        provider_factory=lambda _selection: None,
        catalog_state=state,
        persist_defaults=False,
    )
    list_output = format_list_models(
        state.get_available(), search="extcat", load_error=state.error
    )
    ok_select, _message = repl.select_model("extcat/mini")
    port = repl.current_provider() if ok_select else None
    ok = (
        any(row.reference == "extcat/pro" for row in state.get_all())
        and "extcat" in list_output
        and str(workspace) not in list_output
        and ok_select
        and port is not None
        and port.name == "extcat"
        and port.model_id == "mini"
    )
    checks.append(
        Check(
            "25_extension_provider_catalog_wiring",
            ok,
            "extension-registered providers appear in catalog and construct via ProviderPort",
        )
    )


def run_checks() -> list[Check]:
    checks: list[Check] = []
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        _check_builtin_catalog(checks)
        _check_exact_matching(checks)
        _check_level_parsing(checks)
        _check_fuzzy(checks)
        _check_glob(checks)
        _check_resolve_cli(checks)
        _check_models_json_merge(checks, tmp)
        _check_strip_and_degrade(checks, tmp)
        _check_validation(checks, tmp)
        _check_routing(checks, tmp)
        _check_auth_priority(checks, tmp)
        _check_auth_status(checks, tmp)
        _check_oauth(checks)
        _check_thinking(checks)
        _check_availability(checks, tmp)
        _check_ds4(checks, tmp)
        _check_refresh(checks, tmp)
        _check_product_construction(checks, tmp)
        _check_tier1_construction(checks, tmp)
        _check_tier2_construction(checks, tmp)
        _check_tier3_construction(checks, tmp)
        _check_run_path_construction(checks, tmp)
        _check_startup_cli_resolution(checks, tmp)
        _check_no_secret_leak(checks, tmp)
        _check_extension_provider_catalog_wiring(checks, tmp)
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provider/model catalog conformance gate")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    checks = run_checks()
    passed = all(c.passed for c in checks)
    if args.json:
        report = {
            "passed": passed,
            "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks],
        }
        print(json.dumps(report, indent=2))
    else:
        for c in checks:
            status = "PASS" if c.passed else "FAIL"
            print(f"[{status}] {c.name}: {c.detail}")
        print("ALL PASS" if passed else "FAILURES PRESENT")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
