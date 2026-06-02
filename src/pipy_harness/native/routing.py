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


def model_request_routing(model: NativeModelSpec) -> dict:
    """Return the request-config routing for a resolved model (or ``{}``)."""

    compat = model.compat
    if not isinstance(compat, dict):
        return {}
    base_url = (model.base_url or "").lower()
    request: dict = {}

    open_router = compat.get("openRouterRouting")
    if isinstance(open_router, dict) and "openrouter.ai" in base_url:
        request["provider"] = dict(open_router)

    vercel = compat.get("vercelGatewayRouting")
    if isinstance(vercel, dict) and "ai-gateway.vercel.sh" in base_url:
        gateway: dict = {}
        if "only" in vercel:
            gateway["only"] = vercel["only"]
        if "order" in vercel:
            gateway["order"] = vercel["order"]
        request["providerOptions"] = {"gateway": gateway}

    return request
