"""Central provider/model catalog state (built-in + models.json + auth).

Ties together the built-in catalog, the ``models.json`` overlay
(:class:`ModelCatalog`), and the :class:`AuthStore` so every user-facing
surface — ``--list-models``, the ``/model`` selector, ``--models`` cycling, and
initial-model selection — reads one catalog with one availability gate.

The pipy analogue of how Pi's ``ModelRegistry`` exposes
``getAll``/``getAvailable``/``find`` over a single merged model list.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pipy_harness.native.auth_store import (
    AuthStore,
    ProviderAuthRequestConfig,
    provider_available as _auth_provider_available,
    provider_auth_status,
)
from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.models_json import (
    ModelCatalog,
    default_models_json_path,
)


def format_token_count(count: int) -> str:
    """Pi's ``formatTokenCount``: 200000 -> "200K", 1000000 -> "1M"."""

    if count >= 1_000_000:
        millions = count / 1_000_000
        return f"{int(millions)}M" if millions.is_integer() else f"{millions:.1f}M"
    if count >= 1_000:
        thousands = count / 1_000
        return f"{int(thousands)}K" if thousands.is_integer() else f"{thousands:.1f}K"
    return str(count)


def _fuzzy_match(haystack: str, needle: str) -> bool:
    """Case-insensitive subsequence match (Pi's fuzzyFilter analogue)."""

    haystack = haystack.lower()
    needle = needle.lower()
    pos = 0
    for char in needle:
        if char == " ":
            continue
        pos = haystack.find(char, pos)
        if pos == -1:
            return False
        pos += 1
    return True


@dataclass
class ProviderCatalogState:
    """Merged catalog + auth availability gate."""

    models_json_path: Path | None = None
    auth_store: AuthStore | None = None
    env: Mapping[str, str] | None = None
    openai_codex_auth_path: Path | None = None
    runtime_api_key: str | None = None

    catalog: ModelCatalog = field(init=False)

    def __post_init__(self) -> None:
        if self.models_json_path is None:
            self.models_json_path = default_models_json_path(self.env)
        if self.auth_store is None:
            self.auth_store = AuthStore()
        self.catalog = ModelCatalog(
            models_json_path=self.models_json_path,
            extra_providers=self._extra_providers(),
        )

    def _extra_providers(self):
        """Synthesized provider configs (currently the ds4 env-var shim)."""

        from pipy_harness.native.ds4 import synthesize_ds4_provider_config

        ds4 = synthesize_ds4_provider_config(self._env())
        return {"ds4": ds4} if ds4 is not None else None

    # -- read-through --------------------------------------------------------

    @property
    def error(self) -> str | None:
        return self.catalog.error

    def get_all(self) -> list[NativeModelSpec]:
        return self.catalog.get_all()

    def find(self, provider: str, model_id: str) -> NativeModelSpec | None:
        return self.catalog.find(provider, model_id)

    def models_for(self, provider: str) -> list[NativeModelSpec]:
        return self.catalog.models_for(provider)

    def refresh(self) -> None:
        self.catalog.refresh()
        if self.auth_store is not None:
            self.auth_store.reload()

    # -- availability --------------------------------------------------------

    def _env(self) -> Mapping[str, str]:
        return self.env if self.env is not None else os.environ

    def _models_json_auth(self, provider: str) -> ProviderAuthRequestConfig | None:
        config = self.catalog.provider_request_configs.get(provider)
        if config is None:
            return None
        return ProviderAuthRequestConfig(
            api_key=config.api_key,
            headers=config.headers,
            auth_header=config.auth_header,
        )

    def provider_available(self, provider: str) -> bool:
        if provider == "fake":
            return True
        if provider == "openai-codex":
            if self._openai_codex_logged_in():
                return True
        assert self.auth_store is not None
        if self.runtime_api_key:
            return True
        return _auth_provider_available(
            provider,
            store=self.auth_store,
            env=self._env(),
            models_json_config=self._models_json_auth(provider),
        )

    def availability_reason(self, provider: str) -> str | None:
        if self.provider_available(provider):
            return None
        if provider == "openai-codex":
            return "login-required"
        return "auth-missing"

    def auth_status(self, provider: str):
        assert self.auth_store is not None
        return provider_auth_status(
            provider,
            store=self.auth_store,
            env=self._env(),
            models_json_config=self._models_json_auth(provider),
            runtime_api_key=self.runtime_api_key,
        )

    def get_available(self) -> list[NativeModelSpec]:
        return [r for r in self.get_all() if self.provider_available(r.provider_name)]

    def _openai_codex_logged_in(self) -> bool:
        if self.openai_codex_auth_path is None:
            from pipy_harness.native.openai_codex_provider import (
                default_openai_codex_auth_path,
            )

            self.openai_codex_auth_path = default_openai_codex_auth_path()
        return self.openai_codex_auth_path.exists()


# --------------------------------------------------------------------------- #
# --list-models rendering (Pi's list-models.ts)
# --------------------------------------------------------------------------- #

_COLUMNS = ("provider", "model", "context", "max-out", "thinking", "images")


def format_list_models(
    rows: list[NativeModelSpec],
    *,
    search: str | None,
    load_error: str | None,
) -> str:
    lines: list[str] = []
    if load_error:
        lines.append(f"Warning: errors loading models.json:\n{load_error}")

    if not rows:
        lines.append(
            "No models available. Configure a provider (set an API key env var, "
            "run /login, or add a provider to models.json)."
        )
        return "\n".join(lines)

    filtered = rows
    if search:
        filtered = [r for r in rows if _fuzzy_match(f"{r.provider_name} {r.model_id}", search)]
    if not filtered:
        lines.append(f'No models matching "{search}"')
        return "\n".join(lines)

    filtered = sorted(filtered, key=lambda r: (r.provider_name, r.model_id))

    table_rows = [
        (
            r.provider_name,
            r.model_id,
            format_token_count(r.context_window),
            format_token_count(r.max_tokens),
            "yes" if r.reasoning else "no",
            "yes" if "image" in r.input else "no",
        )
        for r in filtered
    ]

    widths = [
        max(len(_COLUMNS[col]), *(len(row[col]) for row in table_rows))
        for col in range(len(_COLUMNS))
    ]

    def _format_row(values: tuple[str, ...]) -> str:
        return "  ".join(value.ljust(widths[i]) for i, value in enumerate(values))

    lines.append(_format_row(_COLUMNS))
    for row in table_rows:
        lines.append(_format_row(row))
    return "\n".join(lines)
