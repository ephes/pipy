"""Saved request semantics and reconstruction through existing native owners."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pipy_harness.native.agent.request import AgentProviderRequestSnapshot
from pipy_harness.native.agent.usage import AgentTokenPricing
from pipy_harness.native.auth_material import (
    RequestMaterialRef,
    protect_request_material,
    read_request_material,
)
from pipy_harness.native.auth_store import (
    ProviderAuthRequestConfig,
    read_credential_snapshot,
)
from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.http import JsonHTTPClient
from pipy_harness.native.oauth_providers import (
    PER_REQUEST_OAUTH_PROVIDERS,
    OAuthCredentialCache,
)
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.provider_construction import (
    ConstructionOptions,
    PerRequestOAuthProvider,
    ResolvedConstruction,
    _adapter_environment,
    build_provider,
    checkpoint_adapter_defaults,
    checkpoint_construction_semantics,
    checkpoint_iam_target,
    resolve_construction,
)

from .canonical_codec import decode_canonical, encode_canonical
from .codec import OwnedJson
from .provider_inputs import own_construction_inputs
from .provider_settings import (
    decode_options,
    decode_pricing,
    encode_options,
    encode_pricing,
    refuse,
)

FAMILIES = frozenset(
    {
        "openai-completions",
        "openai-responses",
        "azure-openai-responses",
        "anthropic-messages",
        "google-generative-ai",
        "cloudflare-workers-ai",
        "mistral",
        "openai-codex-responses",
        "fake",
        "amazon-bedrock",
        "google-vertex",
    }
)


def _shape(value: object, spec: Mapping[str, object]) -> None:
    if type(value) is not dict or set(value) != set(spec):
        refuse()
    for key, expected in spec.items():
        item = value[key]
        if type(expected) is dict:
            _shape(item, expected)
        elif type(expected) is tuple:
            if not any(type(item) is kind for kind in expected):
                refuse()
        elif type(item) is not expected:
            refuse()


SEMANTICS = {
    "reasoning_effort": (str, type(None)),
    "reasoning_off": bool,
    "reasoning_summary": (str, type(None)),
    "include_encrypted_reasoning": bool,
    "thinking_disabled": bool,
    "force_adaptive_thinking": bool,
    "supports_tool_search": bool,
    "transcript": {
        "supports_mid_convo_system_messages": bool,
        "supports_additional_tools": bool,
        "supports_mid_convo_tool_additions": bool,
        "supports_mid_convo_tool_changes": bool,
        "supports_mid_convo_effort": bool,
        "instruction_role": str,
        "requires_thinking_as_text": bool,
        "requires_reasoning_content": bool,
    },
    "responses_cache": (dict, type(None)),
    "anthropic_cache": (dict, type(None)),
}


@dataclass(frozen=True, slots=True, repr=False)
class PreparedProviderSettings:
    _owned: OwnedJson

    def to_tree(self) -> dict[str, Any]:
        if type(self._owned) is not OwnedJson:
            refuse()
        value = self._owned.thaw()
        assert type(value) is dict
        return value

    @property
    def pricing(self) -> AgentTokenPricing | None:
        return decode_pricing(self.to_tree()["pricing"])


def decode_provider_settings(value: object) -> PreparedProviderSettings:
    try:
        tree = OwnedJson(value).thaw()
        _shape(
            tree,
            {
                "version": int,
                "provider": str,
                "model": str,
                "api": str,
                "thinking": (str, type(None)),
                "semantics": SEMANTICS,
                "transport_profile": str,
                "options": dict,
                "pricing": (dict, type(None)),
                "material": dict,
                "runtime_override": bool,
            },
        )
        assert type(tree) is dict
        if (
            tree["version"] != 1
            or tree["api"] not in FAMILIES
            or tree["transport_profile"] not in ("native-v1", "json-client-v1")
        ):
            refuse()
        for key, fields in (
            (
                "responses_cache",
                {
                    "session_affinity_format": str,
                    "supports_long_cache_retention": bool,
                    "supports_explicit_prompt_cache_mode": bool,
                },
            ),
            (
                "anthropic_cache",
                {
                    "supports_long_cache_retention": bool,
                    "supports_cache_control_on_tools": bool,
                    "send_session_affinity_headers": bool,
                    "session_affinity_format": (str, type(None)),
                },
            ),
        ):
            if tree["semantics"][key] is not None:
                _shape(tree["semantics"][key], fields)
        decode_options(tree["options"])
        decode_pricing(tree["pricing"])
        RequestMaterialRef.from_tree(tree["material"])
        return PreparedProviderSettings(OwnedJson(tree))
    except (TypeError, ValueError, RecursionError, OverflowError):
        refuse()


def _binding(tree: dict[str, Any]) -> dict[str, object]:
    return {
        key: tree[key]
        for key in (
            "provider",
            "model",
            "api",
            "thinking",
            "semantics",
            "transport_profile",
            "options",
            "pricing",
            "runtime_override",
        )
    }


def _resolve(
    spec: NativeModelSpec,
    *,
    auth_path: Path,
    env: Mapping[str, str],
    runtime_api_key: str | None,
    config: ProviderAuthRequestConfig | None,
    thinking: str | None,
    credentials: OAuthCredentialCache,
) -> tuple[ResolvedConstruction, dict[str, object] | None, dict[str, object]]:
    if config is not None and type(config) is not ProviderAuthRequestConfig:
        refuse()
    if type(spec) is not NativeModelSpec or spec.api not in FAMILIES:
        refuse()
    lookup = read_credential_snapshot(auth_path)
    credential = lookup.get(spec.provider_name)
    oauth = (
        credential
        if (
            not runtime_api_key
            and spec.provider_name in PER_REQUEST_OAUTH_PROVIDERS
            and credential is not None
            and credential.get("type") == "oauth"
        )
        else None
    )
    if oauth is not None:
        oauth = credentials.fresh(spec.provider_name, oauth)
    resolved = resolve_construction(
        spec,
        store=lookup,
        env=env,
        runtime_api_key=runtime_api_key,
        models_json_auth=config,
        thinking_level=thinking,
        oauth_credential=oauth,
    )
    if not resolved.ok and spec.api not in ("fake", "openai-codex-responses"):
        refuse()
    sources = {
        "provider_headers": dict(config.headers or {}) if config else {},
        "model_headers": dict(spec.headers or {}),
        "auth_header": bool(config and config.auth_header),
        "base_url_source": spec.base_url,
        # OAuth owns token-derived endpoint renewal; configured endpoint stays bound above.
        "endpoint": resolved.base_url if oauth is None else None,
        "body": dict(resolved.body_extra),
        "configured_headers": dict(resolved.configured_headers),
        "iam_target": checkpoint_iam_target(resolved, env),
        "adapter_defaults": checkpoint_adapter_defaults(resolved, env),
    }
    return resolved, oauth, sources


def prepare_provider_settings(
    snapshot: AgentProviderRequestSnapshot,
    spec: NativeModelSpec,
    *,
    auth_path: Path,
    env: Mapping[str, str],
    runtime_api_key: str | None = None,
    config: ProviderAuthRequestConfig | None = None,
    thinking: str | None = None,
    options: ConstructionOptions | None = None,
    pricing: AgentTokenPricing | None = None,
    json_client: bool = False,
    credentials: OAuthCredentialCache | None = None,
    material_id: str | None = None,
) -> PreparedProviderSettings:
    """Resolve/capture privately before checkpoint adoption; no provider effect."""
    try:
        if (
            type(json_client) is not bool
            or type(snapshot) is not AgentProviderRequestSnapshot
        ):
            refuse()
        if thinking is not None and type(thinking) is not str:
            refuse()
        spec, config, env = own_construction_inputs(spec, config, env)
        if spec.api == "openai-codex-responses" and json_client:
            refuse()
        options = decode_options(encode_options(options or ConstructionOptions()))
        pricing = decode_pricing(encode_pricing(pricing))
        owned_snapshot = decode_canonical(encode_canonical(snapshot))
        assert type(owned_snapshot) is AgentProviderRequestSnapshot
        snapshot = owned_snapshot
        if (
            snapshot.request.provider_name != spec.provider_name
            or snapshot.request.model_id != spec.model_id
        ):
            refuse()
        credentials = credentials or OAuthCredentialCache(auth_path=auth_path)
        resolved, _, sources = _resolve(
            spec,
            auth_path=auth_path,
            env=env,
            runtime_api_key=runtime_api_key,
            config=config,
            thinking=thinking,
            credentials=credentials,
        )
        tree = {
            "version": 1,
            "provider": spec.provider_name,
            "model": spec.model_id,
            "api": spec.api,
            "thinking": thinking,
            "semantics": checkpoint_construction_semantics(resolved, spec, thinking),
            "transport_profile": "json-client-v1" if json_client else "native-v1",
            "options": encode_options(options or ConstructionOptions()),
            "pricing": encode_pricing(pricing),
            "runtime_override": bool(runtime_api_key),
        }
        ref = protect_request_material(
            auth_path, _binding(tree), sources, material_id=material_id
        )
        tree["material"] = ref.to_tree()
        return decode_provider_settings(tree)
    except Exception:  # noqa: BLE001 - private failures must never reach persisted diagnostics
        refuse()


def reconstruct_provider(
    settings: PreparedProviderSettings,
    spec: NativeModelSpec,
    *,
    auth_path: Path,
    env: Mapping[str, str],
    runtime_api_key: str | None = None,
    config: ProviderAuthRequestConfig | None = None,
    credentials: OAuthCredentialCache | None = None,
    http_client: JsonHTTPClient | None = None,
) -> ProviderPort:
    """Validate saved semantics/material before building through the existing owner."""
    try:
        if (
            type(settings) is not PreparedProviderSettings
            or type(spec) is not NativeModelSpec
        ):
            refuse()
        tree = decode_provider_settings(settings.to_tree()).to_tree()
        spec, config, env = own_construction_inputs(spec, config, env)
        if bool(runtime_api_key) != tree["runtime_override"] or (
            http_client is not None
        ) != (tree["transport_profile"] == "json-client-v1"):
            refuse()
        if (spec.provider_name, spec.model_id, spec.api) != (
            tree["provider"],
            tree["model"],
            tree["api"],
        ):
            refuse()
        saved = read_request_material(
            auth_path, RequestMaterialRef.from_tree(tree["material"]), _binding(tree)
        )
        credentials = credentials or OAuthCredentialCache(auth_path=auth_path)
        resolved, oauth, sources = _resolve(
            spec,
            auth_path=auth_path,
            env=env,
            runtime_api_key=runtime_api_key,
            config=config,
            thinking=tree["thinking"],
            credentials=credentials,
        )
        if (spec.provider_name, spec.model_id, spec.api) != (
            tree["provider"],
            tree["model"],
            tree["api"],
        ):
            refuse()
        if OwnedJson(sources) != OwnedJson(saved) or OwnedJson(
            checkpoint_construction_semantics(resolved, spec, tree["thinking"])
        ) != OwnedJson(tree["semantics"]):
            refuse()
        options = decode_options(tree["options"])
        if oauth is not None:
            return PerRequestOAuthProvider(
                resolved=resolved,
                spec=spec,
                credential=oauth,
                credentials=credentials,
                thinking_level=tree["thinking"],
                options=options,
                auth_header=bool(config and config.auth_header),
                http_client=http_client,
                environment=_adapter_environment(env),
            )
        port = build_provider(
            resolved,
            spec=spec,
            thinking_level=tree["thinking"],
            options=options,
            http_client=http_client,
            environment=env,
        )
        if spec.api == "openai-codex-responses":
            from pipy_harness.native.openai_codex_provider import (
                FileOpenAICodexCredentialStore,
                OpenAICodexAuthManager,
                OpenAICodexResponsesProvider,
            )

            assert type(port) is OpenAICodexResponsesProvider
            port = replace(
                port,
                auth_manager=OpenAICodexAuthManager(
                    store=FileOpenAICodexCredentialStore(
                        auth_path.with_name("openai-codex.json")
                    )
                ),
            )
        return port
    except Exception:  # noqa: BLE001 - private failures must never reach persisted diagnostics
        refuse()
