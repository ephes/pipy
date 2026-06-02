"""Pipy-owned built-in provider/model catalog.

This is the pipy analogue of Pi's ``ModelRegistry`` built-in table
(``packages/ai/src/models.generated.ts`` surfaced through
``getProviders()``/``getModels()``) plus ``defaultModelPerProvider``
(``packages/coding-agent/src/core/model-resolver.ts``).

It is a capability match, not a TypeScript port: rows are pipy-owned frozen
dataclasses (:class:`NativeModelSpec`) carrying real capability metadata, and
each row maps onto exactly one existing ``ProviderPort`` adapter family via its
``api`` field. ds4 is intentionally absent — it is a ``models.json`` custom
provider, not a built-in row.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


# The six-value thinking vocabulary used by the CLI surface (Pi's args.ts),
# which includes "off" alongside packages/ai/src/types.ts ThinkingLevel.
THINKING_LEVELS: tuple[str, ...] = ("off", "minimal", "low", "medium", "high", "xhigh")


@dataclass(frozen=True, slots=True)
class NativeModelCost:
    """Per-million-token cost metadata for a catalog row.

    Mirrors Pi's ``cost: { input, output, cacheRead, cacheWrite }``.
    """

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


@dataclass(frozen=True, slots=True)
class NativeModelSpec:
    """A single provider/model catalog row.

    Mirrors Pi's ``Model<Api>`` shape. ``api`` is the adapter family
    (``anthropic-messages``, ``openai-responses``, ``openai-completions``,
    ``openai-codex-responses``, ``google-generative-ai``, ``google-vertex``,
    ``amazon-bedrock``, ``azure-openai-responses``, ``cloudflare-workers-ai``,
    ``mistral``, ``fake``). It maps onto exactly one existing ``ProviderPort``
    adapter; adding a row must not require a new adapter unless the row uses an
    unimplemented API family.
    """

    provider_name: str
    model_id: str
    display_name: str
    api: str
    base_url: str | None = None
    reasoning: bool = False
    thinking_level_map: Mapping[str, str | None] = field(default_factory=dict)
    input: tuple[str, ...] = ("text",)
    cost: NativeModelCost = field(default_factory=NativeModelCost)
    context_window: int = 128_000
    max_tokens: int = 16_384
    headers: Mapping[str, str] | None = None
    # ``compat`` carries provider-compat + routing knobs; typed in M4. Kept as a
    # generic mapping here so M1 stays focused on the data model.
    compat: Any | None = None

    @property
    def reference(self) -> str:
        return f"{self.provider_name}/{self.model_id}"


@dataclass(frozen=True, slots=True)
class NativeCatalog:
    """An ordered, immutable collection of catalog rows + provider order.

    ``rows`` preserves insertion order (provider-major, then the order each
    provider's rows were declared). Lookups are case-insensitive on
    ``provider`` and ``id`` to match Pi's resolver.
    """

    rows: tuple[NativeModelSpec, ...]

    def get_all(self) -> list[NativeModelSpec]:
        return list(self.rows)

    def models_for(self, provider_name: str) -> list[NativeModelSpec]:
        lowered = provider_name.lower()
        return [row for row in self.rows if row.provider_name.lower() == lowered]

    def find(self, provider_name: str, model_id: str) -> NativeModelSpec | None:
        lowered_provider = provider_name.lower()
        lowered_id = model_id.lower()
        for row in self.rows:
            if (
                row.provider_name.lower() == lowered_provider
                and row.model_id.lower() == lowered_id
            ):
                return row
        return None

    def providers(self) -> list[str]:
        seen: list[str] = []
        for row in self.rows:
            if row.provider_name not in seen:
                seen.append(row.provider_name)
        return seen


def build_builtin_catalog() -> NativeCatalog:
    """Build the pipy built-in catalog from the static data table."""

    from pipy_harness.native.catalog_data import BUILTIN_MODEL_ROWS

    return NativeCatalog(rows=tuple(BUILTIN_MODEL_ROWS))


# Pipy's analogue of Pi's ``defaultModelPerProvider`` (model-resolver.ts): maps
# each implemented provider to its default model id, used by initial-model
# selection and per-provider fallback synthesis.
#
# This lives here (not in ``catalog_data``) as a pure string mapping so
# ``catalog.py`` has no module-level import of ``catalog_data``: ``catalog_data``
# imports the dataclasses from this module, and an eager back-import here would
# form an initialization cycle. Every value below is also a row in
# ``catalog_data.BUILTIN_MODEL_ROWS`` (asserted by the catalog tests).
default_model_per_provider: dict[str, str] = {
    "anthropic": "claude-3-5-sonnet-20241022",
    "openai": "gpt-5.5",
    "openai-codex": "gpt-5.5",
    "openai-completions": "gpt-4o-mini",
    "openrouter": "openai/gpt-5.1-codex",
    "google": "gemini-2.0-flash-exp",
    "google-vertex": "gemini-2.0-flash-001",
    "mistral": "mistral-large-latest",
    "amazon-bedrock": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "azure-openai": "gpt-4o",
    "cloudflare": "@cf/meta/llama-3.1-8b-instruct",
}
