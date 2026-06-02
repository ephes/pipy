"""Static built-in model catalog data (pipy-owned).

This is the pipy analogue of Pi's generated ``models.generated.ts`` table, kept
hand-authored and deliberately smaller: it covers every provider pipy
implements with a real adapter, carrying multiple rows per provider (aliases +
dated versions + a default) so pattern matching, ``--list-models`` and the
``/model`` selector are useful. It does not need to be byte-identical to Pi's
table.

ds4 is intentionally absent: it is a ``models.json`` custom provider, not a
built-in row. ``fake`` is the deterministic bootstrap.

``api`` values map onto existing ``ProviderPort`` adapter families:
  anthropic-messages, openai-responses, openai-codex-responses,
  openai-completions, google-generative-ai, google-vertex, amazon-bedrock,
  azure-openai-responses, cloudflare-workers-ai, mistral, fake.
"""

from __future__ import annotations

from pipy_harness.native.catalog import NativeModelCost, NativeModelSpec


# Default per-provider base URLs. Pi's catalog rows always carry a baseUrl, and
# the models.json custom-model parser skips a row whose baseUrl cannot be
# resolved (Pi: ``if (!baseUrl) continue``), so every built-in provider must
# supply one for inherited custom models to resolve.
_PROVIDER_BASE_URL: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
    "openai-completions": "https://api.openai.com/v1",
    "openai-codex": "https://chatgpt.com/backend-api/codex",
    "openrouter": "https://openrouter.ai/api/v1",
    "google": "https://generativelanguage.googleapis.com",
    "google-vertex": "https://aiplatform.googleapis.com",
    "mistral": "https://api.mistral.ai/v1",
    "amazon-bedrock": "https://bedrock-runtime.us-east-1.amazonaws.com",
    "azure-openai": "https://azure-openai.example/openai",
    "cloudflare": "https://api.cloudflare.com/client/v4",
}


def _m(
    provider: str,
    model_id: str,
    display_name: str,
    api: str,
    *,
    base_url: str | None = None,
    reasoning: bool = False,
    thinking: dict[str, str | None] | None = None,
    image: bool = False,
    cost: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0),
    context_window: int = 128_000,
    max_tokens: int = 16_384,
) -> NativeModelSpec:
    return NativeModelSpec(
        provider_name=provider,
        model_id=model_id,
        display_name=display_name,
        api=api,
        base_url=base_url or _PROVIDER_BASE_URL.get(provider),
        reasoning=reasoning,
        thinking_level_map=dict(thinking or {}),
        input=("text", "image") if image else ("text",),
        cost=NativeModelCost(
            input=cost[0], output=cost[1], cache_read=cost[2], cache_write=cost[3]
        ),
        context_window=context_window,
        max_tokens=max_tokens,
    )


_ANTHROPIC_URL = "https://api.anthropic.com"
_REASONING_LEVELS = {
    "off": None,
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
}
_REASONING_LEVELS_XHIGH = {**_REASONING_LEVELS, "xhigh": "xhigh"}


BUILTIN_MODEL_ROWS: tuple[NativeModelSpec, ...] = (
    # ---- anthropic (anthropic-messages) -------------------------------------
    _m(
        "anthropic", "claude-opus-4-7", "Claude Opus 4.7", "anthropic-messages",
        base_url=_ANTHROPIC_URL, reasoning=True, thinking={"xhigh": "xhigh"},
        image=True, cost=(5.0, 25.0, 0.5, 6.25),
        context_window=1_000_000, max_tokens=128_000,
    ),
    _m(
        "anthropic", "claude-sonnet-4-5", "Claude Sonnet 4.5", "anthropic-messages",
        base_url=_ANTHROPIC_URL, reasoning=True, image=True,
        cost=(3.0, 15.0, 0.3, 3.75), context_window=200_000, max_tokens=64_000,
    ),
    _m(
        "anthropic", "claude-sonnet-4-5-20250929", "Claude Sonnet 4.5 (2025-09-29)",
        "anthropic-messages", base_url=_ANTHROPIC_URL, reasoning=True, image=True,
        cost=(3.0, 15.0, 0.3, 3.75), context_window=200_000, max_tokens=64_000,
    ),
    _m(
        "anthropic", "claude-3-5-sonnet-latest", "Claude 3.5 Sonnet (latest)",
        "anthropic-messages", base_url=_ANTHROPIC_URL, image=True,
        cost=(3.0, 15.0, 0.3, 3.75), context_window=200_000, max_tokens=8_192,
    ),
    _m(
        "anthropic", "claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet (2024-10-22)",
        "anthropic-messages", base_url=_ANTHROPIC_URL, image=True,
        cost=(3.0, 15.0, 0.3, 3.75), context_window=200_000, max_tokens=8_192,
    ),
    _m(
        "anthropic", "claude-3-5-haiku-20241022", "Claude 3.5 Haiku (2024-10-22)",
        "anthropic-messages", base_url=_ANTHROPIC_URL,
        cost=(0.8, 4.0, 0.08, 1.0), context_window=200_000, max_tokens=8_192,
    ),

    # ---- openai (openai-responses) ------------------------------------------
    _m(
        "openai", "gpt-5.5", "GPT-5.5", "openai-responses",
        reasoning=True, thinking=_REASONING_LEVELS_XHIGH, image=True,
        cost=(1.25, 10.0, 0.125, 0.0), context_window=400_000, max_tokens=128_000,
    ),
    _m(
        "openai", "gpt-5.4", "GPT-5.4", "openai-responses",
        reasoning=True, thinking=_REASONING_LEVELS, image=True,
        cost=(1.25, 10.0, 0.125, 0.0), context_window=400_000, max_tokens=128_000,
    ),
    _m(
        "openai", "gpt-5.1-codex", "GPT-5.1 Codex", "openai-responses",
        reasoning=True, thinking=_REASONING_LEVELS, image=True,
        cost=(1.25, 10.0, 0.125, 0.0), context_window=400_000, max_tokens=128_000,
    ),
    _m(
        "openai", "gpt-4o", "GPT-4o", "openai-responses", image=True,
        cost=(2.5, 10.0, 1.25, 0.0), context_window=128_000, max_tokens=16_384,
    ),
    _m(
        "openai", "gpt-4o-mini", "GPT-4o mini", "openai-responses", image=True,
        cost=(0.15, 0.6, 0.075, 0.0), context_window=128_000, max_tokens=16_384,
    ),

    # ---- openai-codex (openai-codex-responses) ------------------------------
    _m(
        "openai-codex", "gpt-5.5", "GPT-5.5 (Codex/ChatGPT)", "openai-codex-responses",
        reasoning=True, thinking=_REASONING_LEVELS_XHIGH, image=True,
        cost=(0.0, 0.0, 0.0, 0.0), context_window=400_000, max_tokens=128_000,
    ),
    _m(
        "openai-codex", "gpt-5.4", "GPT-5.4 (Codex/ChatGPT)", "openai-codex-responses",
        reasoning=True, thinking=_REASONING_LEVELS, image=True,
        context_window=400_000, max_tokens=128_000,
    ),
    _m(
        "openai-codex", "gpt-5.1-codex", "GPT-5.1 Codex (Codex/ChatGPT)",
        "openai-codex-responses", reasoning=True, thinking=_REASONING_LEVELS,
        image=True, context_window=400_000, max_tokens=128_000,
    ),

    # ---- openai-completions (openai-completions) ----------------------------
    _m(
        "openai-completions", "gpt-4o-mini", "GPT-4o mini (Completions)",
        "openai-completions", image=True, cost=(0.15, 0.6, 0.075, 0.0),
        context_window=128_000, max_tokens=16_384,
    ),
    _m(
        "openai-completions", "gpt-4o", "GPT-4o (Completions)", "openai-completions",
        image=True, cost=(2.5, 10.0, 1.25, 0.0),
        context_window=128_000, max_tokens=16_384,
    ),
    _m(
        "openai-completions", "gpt-4.1", "GPT-4.1 (Completions)", "openai-completions",
        image=True, cost=(2.0, 8.0, 0.5, 0.0),
        context_window=1_000_000, max_tokens=32_768,
    ),

    # ---- openrouter (openai-completions) ------------------------------------
    _m(
        "openrouter", "openai/gpt-5.1-codex", "OpenRouter: GPT-5.1 Codex",
        "openai-completions", base_url="https://openrouter.ai/api/v1",
        reasoning=True, image=True, context_window=400_000, max_tokens=128_000,
    ),
    _m(
        "openrouter", "anthropic/claude-opus-4-7", "OpenRouter: Claude Opus 4.7",
        "openai-completions", base_url="https://openrouter.ai/api/v1",
        reasoning=True, image=True, context_window=1_000_000, max_tokens=128_000,
    ),
    _m(
        "openrouter", "moonshotai/kimi-k2.6", "OpenRouter: Kimi K2.6",
        "openai-completions", base_url="https://openrouter.ai/api/v1",
        reasoning=True, context_window=256_000, max_tokens=32_768,
    ),
    _m(
        "openrouter", "openai/gpt-4o:extended", "OpenRouter: GPT-4o (extended)",
        "openai-completions", base_url="https://openrouter.ai/api/v1",
        image=True, context_window=128_000, max_tokens=64_000,
    ),

    # ---- google (google-generative-ai) --------------------------------------
    _m(
        "google", "gemini-3.1-pro-preview", "Gemini 3.1 Pro (preview)",
        "google-generative-ai", reasoning=True, image=True,
        cost=(1.25, 10.0, 0.0, 0.0), context_window=1_000_000, max_tokens=65_536,
    ),
    _m(
        "google", "gemini-2.5-pro", "Gemini 2.5 Pro", "google-generative-ai",
        reasoning=True, image=True, cost=(1.25, 10.0, 0.0, 0.0),
        context_window=1_000_000, max_tokens=65_536,
    ),
    _m(
        "google", "gemini-2.0-flash-exp", "Gemini 2.0 Flash (exp)",
        "google-generative-ai", image=True, context_window=1_000_000,
        max_tokens=8_192,
    ),

    # ---- google-vertex (google-vertex) --------------------------------------
    _m(
        "google-vertex", "gemini-3.1-pro-preview", "Vertex: Gemini 3.1 Pro (preview)",
        "google-vertex", reasoning=True, image=True,
        context_window=1_000_000, max_tokens=65_536,
    ),
    _m(
        "google-vertex", "gemini-2.5-pro", "Vertex: Gemini 2.5 Pro", "google-vertex",
        reasoning=True, image=True, context_window=1_000_000, max_tokens=65_536,
    ),
    _m(
        "google-vertex", "gemini-2.0-flash-001", "Vertex: Gemini 2.0 Flash",
        "google-vertex", image=True, context_window=1_000_000, max_tokens=8_192,
    ),

    # ---- mistral (mistral) ---------------------------------------------------
    _m(
        "mistral", "mistral-large-latest", "Mistral Large (latest)", "mistral",
        cost=(2.0, 6.0, 0.0, 0.0), context_window=131_072, max_tokens=32_768,
    ),
    _m(
        "mistral", "devstral-medium-latest", "Devstral Medium (latest)", "mistral",
        cost=(0.4, 2.0, 0.0, 0.0), context_window=131_072, max_tokens=32_768,
    ),
    _m(
        "mistral", "mistral-small-latest", "Mistral Small (latest)", "mistral",
        cost=(0.2, 0.6, 0.0, 0.0), context_window=131_072, max_tokens=32_768,
    ),

    # ---- amazon-bedrock (amazon-bedrock) ------------------------------------
    _m(
        "amazon-bedrock", "us.anthropic.claude-opus-4-6-v1",
        "Bedrock: Claude Opus 4.6", "amazon-bedrock", reasoning=True, image=True,
        cost=(5.0, 25.0, 0.5, 6.25), context_window=1_000_000, max_tokens=128_000,
    ),
    _m(
        "amazon-bedrock", "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "Bedrock: Claude 3.5 Sonnet v2", "amazon-bedrock", image=True,
        cost=(3.0, 15.0, 0.3, 3.75), context_window=200_000, max_tokens=8_192,
    ),
    _m(
        "amazon-bedrock", "anthropic.claude-3-5-sonnet-20240620-v1:0",
        "Bedrock: Claude 3.5 Sonnet v1", "amazon-bedrock", image=True,
        cost=(3.0, 15.0, 0.3, 3.75), context_window=200_000, max_tokens=8_192,
    ),

    # ---- azure-openai (azure-openai-responses) ------------------------------
    _m(
        "azure-openai", "gpt-5.4", "Azure: GPT-5.4", "azure-openai-responses",
        reasoning=True, thinking=_REASONING_LEVELS, image=True,
        context_window=400_000, max_tokens=128_000,
    ),
    _m(
        "azure-openai", "gpt-4o", "Azure: GPT-4o", "azure-openai-responses",
        image=True, cost=(2.5, 10.0, 1.25, 0.0),
        context_window=128_000, max_tokens=16_384,
    ),
    _m(
        "azure-openai", "gpt-4o-mini", "Azure: GPT-4o mini", "azure-openai-responses",
        image=True, cost=(0.15, 0.6, 0.075, 0.0),
        context_window=128_000, max_tokens=16_384,
    ),

    # ---- cloudflare (cloudflare-workers-ai) ---------------------------------
    _m(
        "cloudflare", "@cf/moonshotai/kimi-k2.6", "Cloudflare: Kimi K2.6",
        "cloudflare-workers-ai", reasoning=True,
        context_window=256_000, max_tokens=32_768,
    ),
    _m(
        "cloudflare", "@cf/meta/llama-3.3-70b-instruct",
        "Cloudflare: Llama 3.3 70B", "cloudflare-workers-ai",
        context_window=131_072, max_tokens=16_384,
    ),
    _m(
        "cloudflare", "@cf/meta/llama-3.1-8b-instruct",
        "Cloudflare: Llama 3.1 8B", "cloudflare-workers-ai",
        context_window=128_000, max_tokens=8_192,
    ),

    # ---- fake (deterministic bootstrap) -------------------------------------
    _m("fake", "fake-native-bootstrap", "Fake (bootstrap)", "fake"),
)
