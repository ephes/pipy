"""Catalog-driven provider construction (spec item 18).

Turns a resolved :class:`~pipy_harness.native.catalog.NativeModelSpec` plus
auth/routing/thinking into a concrete ``ProviderPort`` whose real request uses
the catalog's ``base_url``/``model_id``/headers/auth/routing and the mapped
thinking value — instead of the legacy provider factory that builds adapters by
name with only ``model_id``.

The catalog only resolves *which* provider/model/auth/routing/thinking to
construct; the concrete adapter still decides how to call its upstream API. This
module wires the ``openai-completions`` family (custom ``models.json`` providers,
ds4, OpenRouter are all Chat-Completions-shaped), the Tier 1 api-key families
``anthropic-messages``, ``openai-responses`` and ``mistral``, and the Tier 2
composed-endpoint families ``google-generative-ai`` (model-in-path + ``?key=``),
``azure-openai-responses`` (deployment + api-version, ``api-key`` header) and
``cloudflare-workers-ai`` (account id substituted into the base URL via ``{ENV}``
placeholders, OpenAI-compatible body), and the Tier 3 IAM families
``amazon-bedrock`` (SigV4) and ``google-vertex`` (ADC token, or a forwarded
Vertex Express ``GOOGLE_CLOUD_API_KEY``) — whose ADC/SigV4 credentials and
region/project-derived endpoint stay self-resolved by the adapter from the
environment (bedrock's resolved api key is not forwarded; vertex's IS, so the
adapter can use Express api-key mode and otherwise falls back to ADC). Each
adapter
places the mapped thinking effort in its own native body key
(completions/cloudflare: top-level ``reasoning_effort``; responses/azure:
``reasoning.effort``; anthropic/bedrock: adaptive ``output_config.effort`` for
the adaptive Claude models and ``thinking.budget_tokens`` otherwise;
google-generative-ai and google-vertex: per-model
``generationConfig.thinkingConfig`` (level enum vs token budget; vertex uses the
``THINKING_LEVEL_MAP`` variant — no flash-lite table, no Gemma 4)).
``openai-codex-responses``
and the deterministic ``fake`` bootstrap are not catalog-constructed (codex keeps
the legacy factory's settings-derived ``RetryPolicy`` injection): they return
``None`` from :func:`build_provider` so the caller falls back to the legacy
factory. No secret value is placed on any archived field.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from pipy_harness.native.auth_store import (
    AuthStore,
    ProviderAuthRequestConfig,
    resolve_request_auth,
)
from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.routing import model_request_routing
from pipy_harness.native.thinking import map_thinking_level


@dataclass(frozen=True, slots=True)
class ResolvedConstruction:
    """The catalog-resolved inputs needed to construct a provider for a turn.

    ``api_key`` and ``headers`` carry resolved secret material, so they are
    ``repr=False`` — a stray repr/log of this object never leaks the key or an
    auth header (parity with the repr-hidden adapter fields).
    """

    provider_name: str
    model_id: str
    api: str
    base_url: str | None
    ok: bool
    api_key: str | None = field(default=None, repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    body_extra: Mapping[str, object] = field(default_factory=dict)
    reasoning_effort: str | None = None
    # ``True`` when the model is reasoning-capable but the request resolves
    # thinking to off/unset. Only the anthropic-messages adapter consumes this,
    # to emit Pi's explicit ``thinking:{type:"disabled"}`` shape. Mutually
    # exclusive with ``reasoning_effort``.
    thinking_disabled: bool = False
    error: str | None = None


def resolve_construction(
    spec: NativeModelSpec,
    *,
    store: AuthStore,
    env: Mapping[str, str],
    runtime_api_key: str | None,
    models_json_auth: ProviderAuthRequestConfig | None,
    thinking_level: str | None,
) -> ResolvedConstruction:
    """Resolve auth + headers + routing + thinking for a catalog model."""

    # ``base_url`` may carry ``{ENV_VAR}`` placeholders (Cloudflare embeds the
    # account id this way). Substitute them from the environment, failing closed
    # if a referenced var is unset (Pi's ``resolveCloudflareBaseUrl`` throws).
    base_url, base_url_error = _resolve_base_url_placeholders(spec.base_url, env)
    if base_url_error is not None:
        return ResolvedConstruction(
            provider_name=spec.provider_name,
            model_id=spec.model_id,
            api=spec.api,
            base_url=spec.base_url,
            ok=False,
            error=base_url_error,
        )

    auth = resolve_request_auth(
        spec.provider_name,
        store=store,
        env=env,
        runtime_api_key=runtime_api_key,
        models_json_config=models_json_auth,
        model_headers=spec.headers,
        env_for_headers=env,
    )
    if not auth.ok:
        return ResolvedConstruction(
            provider_name=spec.provider_name,
            model_id=spec.model_id,
            api=spec.api,
            base_url=base_url,
            ok=False,
            error=auth.error,
        )

    # Pass all resolved headers through (including an explicit ``Authorization``
    # from models.json headers). The adapter applies ``Bearer api_key`` only when
    # no ``Authorization`` header is already present, so an explicit one is
    # preserved (Pi only overwrites Authorization when ``authHeader`` is set).
    headers = dict(auth.headers)

    body_extra: dict[str, object] = dict(model_request_routing(spec))

    # Thinking shape mirrors Pi's per-format handling (openai-completions.ts):
    # OpenRouter normalises reasoning into a nested ``reasoning: {effort}``
    # object; DeepSeek sends a ``thinking: {type}`` object; Together sends a
    # ``reasoning: {enabled}`` object (both plus a top-level ``reasoning_effort``
    # when supported); Z.ai sends a bare ``enable_thinking`` boolean (no
    # ``reasoning_effort``); the OpenAI-style default uses the top-level
    # ``reasoning_effort``. ``thinking_format`` follows Pi's getCompat
    # precedence (explicit ``compat.thinkingFormat`` over provider/base-URL
    # detection). The off-state branches gate on the raw off/unset level — not
    # merely ``reasoning_value is None`` — so an unsupported clamped level stays
    # out of the off branch (Pi treats it as still-thinking-and-clamp; pipy does
    # not clamp, so it emits neither on- nor off-state).
    reasoning_value = map_thinking_level(spec, thinking_level)
    thinking_off = (
        bool(spec.reasoning)
        and reasoning_value is None
        and (thinking_level is None or thinking_level == "off")
    )
    reasoning_effort: str | None = None
    thinking_format = _resolve_thinking_format(spec)

    if thinking_format == "openrouter":
        if reasoning_value is not None:
            body_extra["reasoning"] = {"effort": reasoning_value}
        elif thinking_off:
            # ``reasoning: {effort: thinkingLevelMap.off ?? "none"}`` disables
            # reasoning at the router rather than omitting the field
            # (openai-completions.ts:578-580).
            off_effort = _openrouter_off_effort(spec)
            if off_effort is not None:
                body_extra["reasoning"] = {"effort": off_effort}
    elif thinking_format == "zai" and bool(spec.reasoning):
        # ``enable_thinking: bool`` is the entire Z.ai thinking shape: true when a
        # reasoning level is active, false when off/unset, for every
        # reasoning-capable zai-format request (openai-completions.ts:556-557). The
        # zai branch emits NO reasoning_effort and never consults
        # supportsReasoningEffort (unlike deepseek/together).
        if reasoning_value is not None:
            body_extra["enable_thinking"] = True
        elif thinking_off:
            body_extra["enable_thinking"] = False
    elif thinking_format == "deepseek" and bool(spec.reasoning):
        # ``thinking: {type: enabled|disabled}`` is emitted for every
        # reasoning-capable DeepSeek request; ``reasoning_effort`` rides along on
        # the on-state only when the model supports it (openai-completions.ts:565-570).
        if reasoning_value is not None:
            body_extra["thinking"] = {"type": "enabled"}
            if _supports_reasoning_effort(spec):
                reasoning_effort = reasoning_value
        elif thinking_off:
            body_extra["thinking"] = {"type": "disabled"}
    elif thinking_format == "together" and bool(spec.reasoning):
        # ``reasoning: {enabled: bool}`` is emitted for every reasoning-capable
        # Together request; ``reasoning_effort`` rides along on the on-state only
        # when the model supports it (openai-completions.ts:586-594). Together's
        # own provider/base URL auto-detects supportsReasoningEffort=False
        # (isTogether), so the on-state normally omits reasoning_effort unless an
        # explicit compat flag — or an explicit thinkingFormat="together" on a
        # non-excluded provider — flips it back on.
        if reasoning_value is not None:
            body_extra["reasoning"] = {"enabled": True}
            if _supports_reasoning_effort(spec):
                reasoning_effort = reasoning_value
        elif thinking_off:
            body_extra["reasoning"] = {"enabled": False}
    elif reasoning_value is not None:
        # Default OpenAI-style top-level ``reasoning_effort``. The not-yet-ported
        # formats (qwen/qwen-chat-template/ant-ling/string-thinking) resolve to
        # their own name and fall here unchanged — a documented deferral, not a
        # regression.
        reasoning_effort = reasoning_value

    # Pi makes the off-state explicit for reasoning-capable anthropic-messages
    # models too (``thinkingEnabled === false`` -> ``thinking:{type:"disabled"}``);
    # only the anthropic adapter consumes ``thinking_disabled`` (the completions
    # families above carry their off-state in ``body_extra``).
    thinking_disabled = thinking_off

    return ResolvedConstruction(
        provider_name=spec.provider_name,
        model_id=spec.model_id,
        api=spec.api,
        base_url=base_url,
        ok=True,
        api_key=auth.api_key,
        headers=headers,
        body_extra=body_extra,
        reasoning_effort=reasoning_effort,
        thinking_disabled=thinking_disabled,
    )


_ENV_PLACEHOLDER = re.compile(r"\{([A-Z_][A-Z0-9_]*)\}")


def _resolve_base_url_placeholders(
    base_url: str | None, env: Mapping[str, str]
) -> tuple[str | None, str | None]:
    """Substitute ``{ENV_VAR}`` placeholders in ``base_url`` from ``env``.

    Returns ``(resolved_url, None)`` on success, or ``(None, error)`` if a
    referenced variable is unset (Pi's ``resolveCloudflareBaseUrl`` raises).
    """

    if not base_url or "{" not in base_url:
        return base_url, None
    missing: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = env.get(name)
        if not value:
            missing.append(name)
            return ""
        return value

    resolved = _ENV_PLACEHOLDER.sub(_replace, base_url)
    if missing:
        return None, (
            f"{missing[0]} is required for the provider base URL but is not set."
        )
    return resolved, None


# Provider names / base-URL substrings Pi's ``detectCompat`` excludes from
# ``supportsReasoningEffort`` (openai-completions.ts:1079-1119). Each entry is
# ``(provider_names, base_url_substrings)``; a match on either disables
# ``reasoning_effort``.
_NO_REASONING_EFFORT_SIGNALS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("xai",), ("api.x.ai",)),  # isGrok
    (("zai",), ("api.z.ai",)),  # isZai
    (("moonshotai", "moonshotai-cn"), ("api.moonshot.",)),  # isMoonshot
    (("together",), ("api.together.ai", "api.together.xyz")),  # isTogether
    (("cloudflare-ai-gateway",), ("gateway.ai.cloudflare.com",)),  # isCloudflareAiGateway
    (("nvidia",), ("integrate.api.nvidia.com",)),  # isNvidia
    (("ant-ling",), ("api.ant-ling.com",)),  # isAntLing
)


def _resolve_thinking_format(spec: NativeModelSpec) -> str:
    """Resolve the model's openai-completions ``thinkingFormat`` (partial port).

    Mirrors Pi's getCompat precedence (explicit ``model.compat.thinkingFormat``
    overrides provider/base-URL detection; openai-completions.ts:1174). Only the
    formats pipy emits a distinct request shape for are detected by name —
    ``openrouter`` (nested ``reasoning``), ``deepseek`` (``thinking`` object),
    ``zai`` (``enable_thinking`` boolean), and ``together``
    (``reasoning: {enabled}``); every other model resolves to ``"openai"``
    (top-level ``reasoning_effort``). The remaining Pi formats
    (qwen/qwen-chat-template/ant-ling/string-thinking) are deferred follow-ons and
    fall through to the default. The detection order mirrors Pi's ``detectCompat``
    ``thinkingFormat`` chain (isDeepSeek > isZai > isTogether > isAntLing >
    isOpenRouter; openai-completions.ts:1126-1136), so ``zai`` is tested before
    ``together`` and ``openrouter`` (the deferred ``ant-ling`` rung between
    ``together`` and ``openrouter`` falls through to the default without reordering
    the implemented rungs).
    """

    compat = spec.compat if isinstance(spec.compat, dict) else {}
    explicit = compat.get("thinkingFormat")
    if isinstance(explicit, str):
        return explicit
    provider = (spec.provider_name or "").lower()
    base_url = (spec.base_url or "").lower()
    if provider == "deepseek" or "deepseek.com" in base_url:
        return "deepseek"
    if provider == "zai" or "api.z.ai" in base_url:
        return "zai"
    if (
        provider == "together"
        or "api.together.ai" in base_url
        or "api.together.xyz" in base_url
    ):
        return "together"
    if provider == "openrouter" or "openrouter.ai" in base_url:
        return "openrouter"
    return "openai"


def _supports_reasoning_effort(spec: NativeModelSpec) -> bool:
    """Whether the model accepts a top-level ``reasoning_effort`` (Pi predicate).

    Mirrors Pi's ``detectCompat`` ``supportsReasoningEffort`` (resolved
    independently of ``thinkingFormat``; openai-completions.ts:1118-1119): an
    explicit ``compat.supportsReasoningEffort`` bool wins, else ``False`` when the
    provider/base URL matches one of Pi's exclusion signals, ``True`` otherwise.
    This is a single bounded predicate, not a full ``detectCompat`` port.
    """

    compat = spec.compat if isinstance(spec.compat, dict) else {}
    explicit = compat.get("supportsReasoningEffort")
    if isinstance(explicit, bool):
        return explicit
    provider = (spec.provider_name or "").lower()
    base_url = (spec.base_url or "").lower()
    for providers, base_urls in _NO_REASONING_EFFORT_SIGNALS:
        if provider in providers or any(part in base_url for part in base_urls):
            return False
    return True


def _openrouter_off_effort(spec: NativeModelSpec) -> str | None:
    """Resolve the OpenRouter off-state ``reasoning.effort`` value, or ``None``.

    Mirrors Pi's ``model.thinkingLevelMap?.off !== null`` gate plus the
    ``?? "none"`` fallback (openai-completions.ts:578-580): an absent ``off`` key
    emits ``"none"``, an explicit ``None`` (Pi ``null``) suppresses the emission,
    and a string value is emitted verbatim. An absent key and a key mapped to
    ``None`` are distinct here, so membership is tested before lookup rather than
    via ``.get`` (which conflates them).
    """

    if "off" not in spec.thinking_level_map:
        return "none"
    return spec.thinking_level_map["off"]


# API families that are fully catalog-constructed here, mapped to the path
# suffix appended to the catalog ``base_url`` to form the request endpoint (Pi
# delegates this to each provider SDK; pipy's hand-rolled adapters own the
# suffix). Other pipy adapters keep their existing (legacy-factory) construction
# for now and return ``None`` from :func:`build_provider`.
_ENDPOINT_SUFFIX: dict[str, str] = {
    "openai-completions": "/chat/completions",
    "anthropic-messages": "/v1/messages",
    "openai-responses": "/responses",
    "mistral": "/chat/completions",
}
# Tier 2 families compose their endpoint differently (model-in-path query for
# google, deployment/api-version for azure, account-substituted base for
# cloudflare), so they are built explicitly rather than via _ENDPOINT_SUFFIX.
# Tier 3 IAM families: AWS SigV4 / GCP ADC and the region/project-derived
# endpoint stay self-resolved by the adapter from the environment — catalog
# construction injects model_id + provider_name + merged headers + mapped
# thinking where the body shape is known. Exception: google-vertex also receives
# the forwarded api key so it can use Pi's Vertex Express api-key mode.
#
# ``openai-codex-responses`` is deliberately NOT catalog-constructed: the legacy
# factory builds it with a settings-derived ``RetryPolicy`` (cli.py), which
# catalog construction would drop, and its OAuth/SSE auth is fully self-contained.
# It stays on the legacy factory (``build_provider`` returns ``None`` for it).
_IAM_FAMILIES = frozenset({"amazon-bedrock", "google-vertex"})
_CATALOG_WIRED_FAMILIES = frozenset(
    {
        *_ENDPOINT_SUFFIX,
        "google-generative-ai",
        "azure-openai-responses",
        "cloudflare-workers-ai",
        *_IAM_FAMILIES,
    }
)


def _default_endpoint(api: str) -> str:
    if api == "openai-completions":
        from pipy_harness.native.openai_completions_provider import (
            OPENAI_CHAT_COMPLETIONS_URL,
        )

        return OPENAI_CHAT_COMPLETIONS_URL
    if api == "anthropic-messages":
        from pipy_harness.native.anthropic_provider import ANTHROPIC_MESSAGES_URL

        return ANTHROPIC_MESSAGES_URL
    if api == "openai-responses":
        from pipy_harness.native.openai_provider import OPENAI_RESPONSES_URL

        return OPENAI_RESPONSES_URL
    from pipy_harness.native.mistral_provider import MISTRAL_CHAT_COMPLETIONS_URL

    return MISTRAL_CHAT_COMPLETIONS_URL


def _endpoint_for(api: str, base_url: str | None) -> str:
    if base_url:
        return base_url.rstrip("/") + _ENDPOINT_SUFFIX[api]
    return _default_endpoint(api)


def build_provider(
    resolved: ResolvedConstruction,
    *,
    http_client: object | None = None,
) -> ProviderPort | None:
    """Construct a ``ProviderPort`` from a resolved catalog model.

    Returns ``None`` for API families not yet catalog-wired so the caller can
    fall back to the legacy provider factory. For a catalog-wired family whose
    auth resolution FAILED, returns a fail-closed provider that reports the auth
    error (Pi fails closed on ``getApiKeyAndHeaders`` errors rather than silently
    using a different construction).
    """

    if resolved.api not in _CATALOG_WIRED_FAMILIES:
        return None
    if not resolved.ok:
        return _FailedAuthProvider(
            provider_name=resolved.provider_name,
            model_id=resolved.model_id,
            error=resolved.error or "auth resolution failed",
        )

    # The remaining families share the same injection surface (api_key +
    # endpoint + merged headers + mapped thinking effort); each adapter places
    # the effort in its own native body key. When ``http_client`` is None the
    # adapter's own urllib client default applies.
    http_kwargs = {} if http_client is None else {"http_client": http_client}

    if resolved.api == "google-generative-ai":
        from pipy_harness.native.google_provider import (
            GOOGLE_GENERATIVE_AI_ENDPOINT_TEMPLATE,
            GoogleGenerativeAIProvider,
        )

        base = (resolved.base_url or "").rstrip("/")
        template = (
            base + "/v1beta/models/{model}:generateContent?key={key}"
            if base
            else GOOGLE_GENERATIVE_AI_ENDPOINT_TEMPLATE
        )
        return GoogleGenerativeAIProvider(
            model_id=resolved.model_id,
            api_key=resolved.api_key,
            endpoint_template=template,
            provider_name=resolved.provider_name,
            extra_headers=dict(resolved.headers),
            reasoning_effort=resolved.reasoning_effort,
            thinking_disabled=resolved.thinking_disabled,
            **http_kwargs,  # type: ignore[arg-type]
        )

    if resolved.api == "azure-openai-responses":
        from pipy_harness.native.azure_openai_provider import (
            AzureOpenAIResponsesProvider,
        )

        return AzureOpenAIResponsesProvider(
            model_id=resolved.model_id,
            endpoint_url=resolved.base_url,
            api_key=resolved.api_key,
            provider_name=resolved.provider_name,
            extra_headers=dict(resolved.headers),
            reasoning_effort=resolved.reasoning_effort,
            **http_kwargs,  # type: ignore[arg-type]
        )

    if resolved.api == "cloudflare-workers-ai":
        from pipy_harness.native.cloudflare_provider import (
            CloudflareWorkersAIProvider,
        )

        # ``base_url`` already has the account id substituted (resolve_construction).
        endpoint = (resolved.base_url or "").rstrip("/") + "/chat/completions"
        return CloudflareWorkersAIProvider(
            model_id=resolved.model_id,
            api_token=resolved.api_key,
            endpoint=endpoint,
            provider_name=resolved.provider_name,
            extra_headers=dict(resolved.headers),
            reasoning_effort=resolved.reasoning_effort,
            **http_kwargs,  # type: ignore[arg-type]
        )

    if resolved.api in _IAM_FAMILIES:
        return _build_iam_provider(resolved, http_kwargs)

    endpoint = _endpoint_for(resolved.api, resolved.base_url)

    if resolved.api == "openai-completions":
        from pipy_harness.native.openai_completions_provider import (
            OpenAIChatCompletionsProvider,
            UrllibJsonHTTPClient,
        )

        client = http_client if http_client is not None else UrllibJsonHTTPClient()
        return OpenAIChatCompletionsProvider(
            model_id=resolved.model_id,
            api_key=resolved.api_key,
            http_client=client,  # type: ignore[arg-type]
            endpoint=endpoint,
            provider_name=resolved.provider_name,
            extra_headers=dict(resolved.headers),
            extra_body=dict(resolved.body_extra),
            reasoning_effort=resolved.reasoning_effort,
        )

    if resolved.api == "anthropic-messages":
        from pipy_harness.native.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            model_id=resolved.model_id,
            api_key=resolved.api_key,
            endpoint=endpoint,
            provider_name=resolved.provider_name,
            extra_headers=dict(resolved.headers),
            reasoning_effort=resolved.reasoning_effort,
            thinking_disabled=resolved.thinking_disabled,
            **http_kwargs,  # type: ignore[arg-type]
        )

    if resolved.api == "openai-responses":
        from pipy_harness.native.openai_provider import OpenAIResponsesProvider

        return OpenAIResponsesProvider(
            model_id=resolved.model_id,
            api_key=resolved.api_key,
            endpoint=endpoint,
            provider_name=resolved.provider_name,
            extra_headers=dict(resolved.headers),
            reasoning_effort=resolved.reasoning_effort,
            **http_kwargs,  # type: ignore[arg-type]
        )

    from pipy_harness.native.mistral_provider import MistralProvider

    return MistralProvider(
        model_id=resolved.model_id,
        api_key=resolved.api_key,
        endpoint=endpoint,
        provider_name=resolved.provider_name,
        extra_headers=dict(resolved.headers),
        reasoning_effort=resolved.reasoning_effort,
        **http_kwargs,  # type: ignore[arg-type]
    )


def _build_iam_provider(
    resolved: ResolvedConstruction,
    http_kwargs: Mapping[str, object],
) -> ProviderPort:
    """Construct a Tier 3 IAM/OAuth adapter from a resolved catalog model.

    For **bedrock**, the AWS SigV4 credentials and the region-derived endpoint
    stay self-resolved by the adapter (they are not api keys), so
    ``resolved.api_key`` is intentionally not forwarded.

    For **google-vertex**, the resolved api key IS forwarded: Pi supports a
    Vertex Express API key (``GOOGLE_CLOUD_API_KEY``), and the adapter uses it
    when present (global host + ``x-goog-api-key``), otherwise falls back to its
    ADC bearer path. Forwarding the ``<authenticated>`` ambient sentinel is safe
    — the adapter's ``_resolve_express_api_key`` rejects placeholders, mirroring
    Pi forwarding ``getEnvApiKey()`` into ``options.apiKey`` and filtering it in
    ``resolveApiKey``. Only the model id, provider name, merged headers (and the
    mapped thinking effort for families whose body shape is known) are otherwise
    injected. Vertex receives the resolved ``reasoning_effort``/``thinking_disabled``
    so it emits Pi's per-model ``generationConfig.thinkingConfig``; bedrock does
    not (it carries thinking in its own adaptive body keys).
    """

    if resolved.api == "amazon-bedrock":
        from pipy_harness.native.bedrock_provider import AmazonBedrockProvider

        return AmazonBedrockProvider(
            model_id=resolved.model_id,
            provider_name=resolved.provider_name,
            extra_headers=dict(resolved.headers),
            reasoning_effort=resolved.reasoning_effort,
            **http_kwargs,  # type: ignore[arg-type]
        )

    from pipy_harness.native.google_vertex_provider import GoogleVertexProvider

    return GoogleVertexProvider(
        model_id=resolved.model_id,
        api_key=resolved.api_key,
        provider_name=resolved.provider_name,
        extra_headers=dict(resolved.headers),
        reasoning_effort=resolved.reasoning_effort,
        thinking_disabled=resolved.thinking_disabled,
        **http_kwargs,  # type: ignore[arg-type]
    )


@dataclass(frozen=True, slots=True)
class _FailedAuthProvider:
    """Fail-closed provider for a catalog-wired model whose auth did not resolve.

    Keeps the catalog selection bound (no silent fallback to a different
    construction) and surfaces the auth error on use. ``error`` is already a
    Pi-shaped message ("No API key found for ...") and carries no secret.
    """

    provider_name: str
    model_id: str
    error: str
    supports_tool_calls: bool = True

    @property
    def name(self) -> str:
        return self.provider_name

    def complete(
        self, request, *, stream_sink=None, reasoning_sink=None, cancel_token=None
    ):
        from pipy_harness.native._provider_helpers import (
            failed_provider_result,
            utc_now,
        )

        del stream_sink, reasoning_sink, cancel_token
        return failed_provider_result(
            request,
            provider_name=self.provider_name,
            started_at=utc_now(),
            error_type="CatalogAuthError",
            error_message=self.error,
        )
