"""Request routing extracted from a resolved model's compat (M4).

Pi forwards ``models.json`` routing blocks into each provider's own request
shape (openai-completions.ts):

- OpenRouter routing -> the top-level ``provider`` request param, gated on an
  ``openrouter.ai`` base URL.
- Vercel AI Gateway routing -> ``providerOptions.gateway = { only, order }``,
  gated on an ``ai-gateway.vercel.sh`` base URL (it is NOT a ``provider`` block).

The routing blocks survive the catalog merge as part of the resolved model's
``compat`` (deep-merged by the loader). This function turns them into the
request-config shape the relevant adapters send. Routing is provider-config and
is never archived.
"""

from __future__ import annotations

from pipy_harness.native.catalog import NativeModelSpec


def _string_object_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return {key: item for key, item in value.items() if isinstance(key, str)}


def model_request_routing(model: NativeModelSpec) -> dict[str, object]:
    """Return the request-config routing for a resolved model (or ``{}``)."""

    compat = _string_object_dict(model.compat)
    if compat is None:
        return {}
    base_url = (model.base_url or "").lower()
    request: dict[str, object] = {}

    open_router = _string_object_dict(compat.get("openRouterRouting"))
    if open_router is not None and "openrouter.ai" in base_url:
        request["provider"] = open_router

    vercel = _string_object_dict(compat.get("vercelGatewayRouting"))
    if vercel is not None and "ai-gateway.vercel.sh" in base_url:
        gateway: dict[str, object] = {}
        if vercel.get("only"):
            gateway["only"] = vercel["only"]
        if vercel.get("order"):
            gateway["order"] = vercel["order"]
        # Pi only sets providerOptions when only/order is present
        # (openai-completions.ts); an empty gateway is omitted.
        if gateway:
            request["providerOptions"] = {"gateway": gateway}

    return request
