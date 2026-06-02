"""Catalog-driven provider construction (spec item 18).

Turns a resolved :class:`~pipy_harness.native.catalog.NativeModelSpec` plus
auth/routing/thinking into a concrete ``ProviderPort`` whose real request uses
the catalog's ``base_url``/``model_id``/headers/auth/routing and the mapped
thinking value — instead of the legacy provider factory that builds adapters by
name with only ``model_id``.

The catalog only resolves *which* provider/model/auth/routing/thinking to
construct; the concrete adapter still decides how to call its upstream API. This
module fully wires the ``openai-completions`` API family (custom ``models.json``
providers, ds4, OpenRouter are all Chat-Completions-shaped); other families
return ``None`` from :func:`build_provider` so the caller falls back to the
legacy factory. No secret value is placed on any archived field.
"""

from __future__ import annotations

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
    """The catalog-resolved inputs needed to construct a provider for a turn."""

    provider_name: str
    model_id: str
    api: str
    base_url: str | None
    ok: bool
    api_key: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    body_extra: Mapping[str, object] = field(default_factory=dict)
    reasoning_effort: str | None = None
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
            base_url=spec.base_url,
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
    # object; the OpenAI-style default uses the top-level ``reasoning_effort``.
    reasoning_value = map_thinking_level(spec, thinking_level)
    reasoning_effort: str | None = None
    if reasoning_value is not None:
        if _uses_openrouter_thinking(spec):
            body_extra["reasoning"] = {"effort": reasoning_value}
        else:
            reasoning_effort = reasoning_value

    return ResolvedConstruction(
        provider_name=spec.provider_name,
        model_id=spec.model_id,
        api=spec.api,
        base_url=spec.base_url,
        ok=True,
        api_key=auth.api_key,
        headers=headers,
        body_extra=body_extra,
        reasoning_effort=reasoning_effort,
    )


def _uses_openrouter_thinking(spec: NativeModelSpec) -> bool:
    compat = spec.compat if isinstance(spec.compat, dict) else {}
    if compat.get("thinkingFormat") == "openrouter":
        return True
    return "openrouter.ai" in (spec.base_url or "").lower()


# API families that speak the OpenAI Chat Completions envelope and are fully
# catalog-constructed here. Other pipy adapters keep their existing
# (legacy-factory) construction for now.
_COMPLETIONS_FAMILIES = {"openai-completions"}


def _chat_completions_endpoint(base_url: str | None) -> str:
    from pipy_harness.native.openai_completions_provider import (
        OPENAI_CHAT_COMPLETIONS_URL,
    )

    if not base_url:
        return OPENAI_CHAT_COMPLETIONS_URL
    return base_url.rstrip("/") + "/chat/completions"


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

    if resolved.api not in _COMPLETIONS_FAMILIES:
        return None
    if not resolved.ok:
        return _FailedAuthProvider(
            provider_name=resolved.provider_name,
            model_id=resolved.model_id,
            error=resolved.error or "auth resolution failed",
        )

    from pipy_harness.native.openai_completions_provider import (
        OpenAIChatCompletionsProvider,
        UrllibJsonHTTPClient,
    )

    client = http_client if http_client is not None else UrllibJsonHTTPClient()
    return OpenAIChatCompletionsProvider(
        model_id=resolved.model_id,
        api_key=resolved.api_key,
        http_client=client,  # type: ignore[arg-type]
        endpoint=_chat_completions_endpoint(resolved.base_url),
        provider_name=resolved.provider_name,
        extra_headers=dict(resolved.headers),
        extra_body=dict(resolved.body_extra),
        reasoning_effort=resolved.reasoning_effort,
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

    def complete(self, request, *, stream_sink=None, reasoning_sink=None):
        from pipy_harness.native._provider_helpers import (
            failed_provider_result,
            utc_now,
        )

        del stream_sink, reasoning_sink
        return failed_provider_result(
            request,
            provider_name=self.provider_name,
            started_at=utc_now(),
            error_type="CatalogAuthError",
            error_message=self.error,
        )
