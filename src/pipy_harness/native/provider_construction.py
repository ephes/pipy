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

    # The adapter sets ``Authorization`` from ``api_key`` itself; pass the rest
    # of the resolved headers through as extra headers (avoids a duplicate /
    # conflicting Authorization).
    headers = {
        name: value
        for name, value in auth.headers.items()
        if name.lower() != "authorization"
    }
    routing = model_request_routing(spec)
    reasoning = map_thinking_level(spec, thinking_level)

    return ResolvedConstruction(
        provider_name=spec.provider_name,
        model_id=spec.model_id,
        api=spec.api,
        base_url=spec.base_url,
        ok=True,
        api_key=auth.api_key,
        headers=headers,
        body_extra=routing,
        reasoning_effort=reasoning,
    )


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
    fall back to the legacy provider factory.
    """

    if not resolved.ok:
        return None
    if resolved.api not in _COMPLETIONS_FAMILIES:
        return None

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
