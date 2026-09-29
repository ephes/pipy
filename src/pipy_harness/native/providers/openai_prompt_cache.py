"""OpenAI prompt-cache affinity helpers (Pi ``api/openai-prompt-cache.ts``).

Pi derives the OpenAI ``prompt_cache_key`` from the durable session id and
clamps it to OpenAI's 64-character limit. The Responses family also resolves a
cache retention preference: an explicit request value wins, else
``PIPY_CACHE_RETENTION=long`` (Pi ``PI_CACHE_RETENTION``), else ``short``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from pipy_harness.native.models import CacheRetention

OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH = 64
CACHE_RETENTION_ENV = "PIPY_CACHE_RETENTION"


def clamp_openai_prompt_cache_key(key: str | None) -> str | None:
    """Return ``key`` cut to the first 64 code points; ``None`` stays ``None``."""

    if key is None:
        return None
    return key[:OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH]


def resolve_cache_retention(
    cache_retention: CacheRetention | None,
    env: Mapping[str, str] | None = None,
) -> CacheRetention:
    """Pi ``resolveCacheRetention`` for the Responses family.

    An explicit value wins; otherwise ``long`` when the environment asks for
    it, else ``short``. Read at request time, as Pi reads the env per call.
    """

    if cache_retention:
        return cache_retention
    source = os.environ if env is None else env
    if source.get(CACHE_RETENTION_ENV) == "long":
        return "long"
    return "short"
