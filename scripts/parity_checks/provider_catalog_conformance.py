"""Hard conformance gate for the Pi-style provider/model catalog.

Drives the pipy-owned catalog with deterministic fixtures (a temp config root, a
temp ``models.json``, a fake auth store, fake OAuth HTTP transports, no network)
and fails unless the full Pi-equivalent capability set works. It is the
implementation source of truth for ``docs/provider-catalog.md``.

Run:

    uv run python scripts/parity_checks/provider_catalog_conformance.py --json

Verifies (numbered per docs/provider-catalog.md "Verification Plan"):

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
18. no secret/token/Authorization/PKCE/auth-URL value in any archive surface.

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
from pipy_harness.native.catalog_state import ProviderCatalogState
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
        and headers.headers.get("Authorization") == "Bearer abc"
        and headers.headers.get("X-Org") == "org-1"
    )
    checks.append(Check("11_auth_priority", ok, "runtime>stored>env; authHeader+headers"))


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
    ok = (
        cmd_status.source == "models_json_command"
        and not executed
        and env_status.source == "environment"
        and env_status.label == "OPENAI_API_KEY"
        and stored_status.source == "stored"
    )
    checks.append(Check("12_auth_status_labels", ok, "labels correct; no !command exec"))


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
        and base == "https://proxy.example.com"
        and policy_tx.calls[-1][1] == "https://proxy.example.com/models/gpt-5.4/policy"
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


def _check_no_secret_leak(checks, tmp: Path):
    # Resolve auth (produces secrets/headers) and confirm the catalog's own
    # archive-facing surfaces (model rows) carry no secret material.
    store = AuthStore(path=tmp / "auth_leak.json")
    store.set("anthropic", {"type": "oauth", "access": "SECRET-TOKEN", "refresh": "SECRET-REFRESH", "expires": 0})
    resolved = resolve_request_auth(
        "ds4",
        store=store,
        env={},
        models_json_config=ProviderAuthRequestConfig(api_key="SECRET-KEY", auth_header=True),
    )
    # The catalog rows (the archive-facing data) must not contain any secret.
    catalog = build_builtin_catalog()
    serialized = json.dumps(
        [
            {
                "provider": r.provider_name,
                "model": r.model_id,
                "api": r.api,
                "base_url": r.base_url,
            }
            for r in catalog.get_all()
        ]
    )
    leaked = any(s in serialized for s in ("SECRET-TOKEN", "SECRET-REFRESH", "SECRET-KEY", "Bearer"))
    ok = resolved.api_key == "SECRET-KEY" and not leaked
    checks.append(Check("18_no_secret_in_archive", ok, "catalog rows carry no secrets"))


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
        _check_no_secret_leak(checks, tmp)
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
