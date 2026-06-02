"""ds4 as a ``models.json`` custom provider (M11 reframe).

Pi has no ``ds4`` built-in. ds4 is a local OpenAI-compatible Chat Completions
server, exactly the case ``models.json`` custom providers exist for. So ds4 is
not a built-in catalog row; it is:

- a documented ``models.json`` preset users can paste (:func:`ds4_preset_dict`),
  and
- an opt-in env-var convenience shim (``PIPY_DS4_BASE_URL`` /
  ``PIPY_DS4_API_KEY``) that synthesizes the *same* ``models.json``-style custom
  provider entry (:func:`synthesize_ds4_provider_config`).

Because Pi's ``validateConfig`` requires both ``baseUrl`` and ``apiKey`` for a
non-built-in provider that defines custom models, the ds4 entry carries a
placeholder ``apiKey`` (the local server ignores it).
"""

from __future__ import annotations

from collections.abc import Mapping

from pipy_harness.native.models_json import ModelDefinition, ProviderConfig


DS4_BASE_URL_ENV = "PIPY_DS4_BASE_URL"
DS4_API_KEY_ENV = "PIPY_DS4_API_KEY"
DS4_DEFAULT_BASE_URL = "http://127.0.0.1:8000/v1"
DS4_DEFAULT_MODEL = "deepseek-v4-flash"
DS4_PLACEHOLDER_API_KEY = "local"


def ds4_preset_dict(
    *, base_url: str = DS4_DEFAULT_BASE_URL, api_key: str = DS4_PLACEHOLDER_API_KEY
) -> dict:
    """The canonical ds4 ``models.json`` preset, as a plain dict."""

    return {
        "providers": {
            "ds4": {
                "baseUrl": base_url,
                "apiKey": api_key,
                "api": "openai-completions",
                "models": [
                    {
                        "id": DS4_DEFAULT_MODEL,
                        "name": "DeepSeek V4 Flash (ds4, local)",
                        "reasoning": True,
                        "input": ["text"],
                        "contextWindow": 131072,
                        "maxTokens": 16384,
                    }
                ],
            }
        }
    }


def synthesize_ds4_provider_config(
    env: Mapping[str, str],
) -> ProviderConfig | None:
    """Synthesize a ds4 ``ProviderConfig`` from the env shim, or ``None``.

    The shim is opt-in: it activates only when ``PIPY_DS4_BASE_URL`` or
    ``PIPY_DS4_API_KEY`` is set. The result is identical in shape to the
    :func:`ds4_preset_dict` custom provider.
    """

    base_url = env.get(DS4_BASE_URL_ENV)
    api_key = env.get(DS4_API_KEY_ENV)
    if not base_url and not api_key:
        return None
    return ProviderConfig(
        base_url=base_url or DS4_DEFAULT_BASE_URL,
        api_key=api_key or DS4_PLACEHOLDER_API_KEY,
        api="openai-completions",
        models=(
            ModelDefinition(
                id=DS4_DEFAULT_MODEL,
                name="DeepSeek V4 Flash (ds4, local)",
                reasoning=True,
                input=("text",),
                context_window=131072,
                max_tokens=16384,
            ),
        ),
    )
