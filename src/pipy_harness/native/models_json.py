"""``models.json`` custom provider/model + override loader (pipy-owned).

Pipy analogue of Pi's ``ModelRegistry.loadCustomModels``/``mergeCustomModels``/
``validateConfig``/``parseModels`` (model-registry.ts). Loads
``<config>/models.json``, strips ``//`` line comments and trailing commas,
parses with stdlib ``json``, validates with a pipy-owned validator producing
path-qualified errors, and deep-merges the result over the built-in catalog.

Load failures degrade gracefully: the built-in catalog is kept and a
path-qualified error is surfaced (never crashes startup). No new dependency:
stdlib ``json`` + ``re`` only.

Compat/routing knobs are carried through as plain mappings here; M4 types them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from pipy_harness.native._resource_files import resolve_global_resource_root
from pipy_harness.native.catalog import (
    NativeCatalog,
    NativeModelCost,
    NativeModelSpec,
    build_builtin_catalog,
)


# --------------------------------------------------------------------------- #
# Comment / trailing-comma stripping (matches Pi's stripJsonComments regex)
# --------------------------------------------------------------------------- #

# Match a JSON string literal OR a // line comment; keep strings, drop comments.
_STRING_OR_LINE_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*')
# Match a JSON string literal OR a trailing comma before } or ]; keep strings.
_STRING_OR_TRAILING_COMMA = re.compile(r'"(?:\\.|[^"\\])*"|,(\s*[}\]])')


def strip_json_comments(text: str) -> str:
    """Strip ``//`` line comments and trailing commas, leaving strings intact."""

    def _drop_comment(match: re.Match[str]) -> str:
        token = match.group(0)
        return token if token.startswith('"') else ""

    def _drop_trailing_comma(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.startswith('"'):
            return token
        return match.group(1)

    without_comments = _STRING_OR_LINE_COMMENT.sub(_drop_comment, text)
    return _STRING_OR_TRAILING_COMMA.sub(_drop_trailing_comma, without_comments)


# --------------------------------------------------------------------------- #
# Config-root resolution
# --------------------------------------------------------------------------- #


def default_models_json_path(env: Mapping[str, str] | None = None) -> Path:
    """``<config>/models.json`` via PIPY_CONFIG_HOME -> XDG -> ~/.config/pipy."""

    return resolve_global_resource_root(env=env) / "models.json"


# --------------------------------------------------------------------------- #
# Parsed schema dataclasses
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ModelDefinition:
    id: str
    name: str | None = None
    api: str | None = None
    base_url: str | None = None
    reasoning: bool | None = None
    thinking_level_map: Mapping[str, str | None] | None = None
    input: tuple[str, ...] | None = None
    cost: NativeModelCost | None = None
    context_window: int | None = None
    max_tokens: int | None = None
    headers: Mapping[str, str] | None = None
    compat: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ModelOverride:
    name: str | None = None
    reasoning: bool | None = None
    thinking_level_map: Mapping[str, str | None] | None = None
    input: tuple[str, ...] | None = None
    # Partial cost: only the sub-fields present in models.json (attr-named:
    # input/output/cache_read/cache_write). An explicit ``0`` is preserved so it
    # can override a non-zero built-in (Pi merges with ``??``, not truthiness).
    cost: Mapping[str, float] | None = None
    context_window: int | None = None
    max_tokens: int | None = None
    headers: Mapping[str, str] | None = None
    compat: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    api: str | None = None
    headers: Mapping[str, str] | None = None
    auth_header: bool = False
    compat: Mapping[str, object] | None = None
    models: tuple[ModelDefinition, ...] = ()
    model_overrides: Mapping[str, ModelOverride] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderRequestConfig:
    """Per-provider request auth/header config from models.json.

    Consumed by the auth layer (M6); ``api_key`` may be a literal key, an
    env-var name, or a ``!command`` value resolved at request time.
    """

    api_key: str | None = None
    headers: Mapping[str, str] | None = None
    auth_header: bool = False


class ModelsJsonError(Exception):
    """Raised internally during load; carries a fully-formatted message."""


# --------------------------------------------------------------------------- #
# Validation + parsing
# --------------------------------------------------------------------------- #


def _type_error(path: str, expected: str) -> str:
    # Dot-path format (Pi's formatValidationPath), e.g.
    # "providers.anthropic.baseUrl" rather than a JSON pointer.
    dotted = path.lstrip("/").replace("/", ".")
    return f"  - {dotted}: expected {expected}"


def _coerce_cost_fields(value: object, path: str, errors: list[str]) -> dict[str, float]:
    """Return only the cost sub-fields present in the JSON (attr-named)."""

    if not isinstance(value, dict):
        errors.append(_type_error(path, "object"))
        return {}
    nums: dict[str, float] = {}
    for key, attr in (
        ("input", "input"),
        ("output", "output"),
        ("cacheRead", "cache_read"),
        ("cacheWrite", "cache_write"),
    ):
        if key in value:
            raw = value[key]
            if not isinstance(raw, (int, float)) or isinstance(raw, bool):
                errors.append(_type_error(f"{path}/{key}", "number"))
            else:
                nums[attr] = float(raw)
    return nums


def _coerce_cost(value: object, path: str, errors: list[str]) -> NativeModelCost | None:
    """Full cost object for a custom model (missing sub-fields default to 0)."""

    fields = _coerce_cost_fields(value, path, errors)
    return NativeModelCost(**fields)


def _coerce_input(value: object, path: str, errors: list[str]) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not all(
        isinstance(v, str) and v in ("text", "image") for v in value
    ):
        errors.append(_type_error(path, 'array of "text"/"image"'))
        return None
    return tuple(value)


def _coerce_full_cost(value: object, path: str, errors: list[str]) -> NativeModelCost | None:
    """A custom model's full cost: all four sub-fields required (Pi schema)."""

    if not isinstance(value, dict):
        errors.append(_type_error(path, "object"))
        return None
    required = ("input", "output", "cacheRead", "cacheWrite")
    missing = [key for key in required if key not in value]
    if missing:
        errors.append(
            _type_error(path, f"object with {', '.join(required)} (missing {', '.join(missing)})")
        )
        return None
    return _coerce_cost(value, path, errors)


def _coerce_model_def(
    raw: object, path: str, errors: list[str]
) -> ModelDefinition | None:
    if not isinstance(raw, dict):
        errors.append(_type_error(path, "object"))
        return None
    model_id = raw.get("id")
    if not isinstance(model_id, str) or not model_id:
        errors.append(_type_error(f"{path}/id", "non-empty string"))
        model_id = model_id if isinstance(model_id, str) else ""
    cost = (
        _coerce_full_cost(raw["cost"], f"{path}/cost", errors)
        if "cost" in raw
        else None
    )
    input_ = (
        _coerce_input(raw["input"], f"{path}/input", errors) if "input" in raw else None
    )
    return ModelDefinition(
        id=model_id,
        name=_opt_str(raw, "name", f"{path}/name", errors),
        api=_opt_str(raw, "api", f"{path}/api", errors),
        base_url=_opt_str(raw, "baseUrl", f"{path}/baseUrl", errors),
        reasoning=_opt_bool(raw, "reasoning", f"{path}/reasoning", errors),
        thinking_level_map=_opt_str_map(
            raw, "thinkingLevelMap", f"{path}/thinkingLevelMap", errors
        ),
        input=input_,
        cost=cost,
        context_window=_opt_number_as_int(
            raw, "contextWindow", f"{path}/contextWindow", errors
        ),
        max_tokens=_opt_number_as_int(raw, "maxTokens", f"{path}/maxTokens", errors),
        headers=_opt_str_map(raw, "headers", f"{path}/headers", errors),
        compat=_opt_obj(raw, "compat", f"{path}/compat", errors),
    )


def _coerce_model_override(
    raw: object, path: str, errors: list[str]
) -> ModelOverride | None:
    if not isinstance(raw, dict):
        errors.append(_type_error(path, "object"))
        return None
    cost = (
        _coerce_cost_fields(raw["cost"], f"{path}/cost", errors)
        if "cost" in raw
        else None
    )
    input_ = (
        _coerce_input(raw["input"], f"{path}/input", errors) if "input" in raw else None
    )
    return ModelOverride(
        name=_opt_str(raw, "name", f"{path}/name", errors),
        reasoning=_opt_bool(raw, "reasoning", f"{path}/reasoning", errors),
        thinking_level_map=_opt_str_map(
            raw, "thinkingLevelMap", f"{path}/thinkingLevelMap", errors
        ),
        input=input_,
        cost=cost,
        context_window=_opt_int(raw, "contextWindow", f"{path}/contextWindow", errors),
        max_tokens=_opt_int(raw, "maxTokens", f"{path}/maxTokens", errors),
        headers=_opt_str_map(raw, "headers", f"{path}/headers", errors),
        compat=_opt_obj(raw, "compat", f"{path}/compat", errors),
    )


def _opt_str(raw: dict, key: str, path: str, errors: list[str]) -> str | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, str):
        errors.append(_type_error(path, "string"))
        return None
    return value


def _opt_bool(raw: dict, key: str, path: str, errors: list[str]) -> bool | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, bool):
        errors.append(_type_error(path, "boolean"))
        return None
    return value


def _opt_int(raw: dict, key: str, path: str, errors: list[str]) -> int | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, int) or isinstance(value, bool):
        errors.append(_type_error(path, "integer"))
        return None
    return value


def _opt_number_as_int(raw: dict, key: str, path: str, errors: list[str]) -> int | None:
    """Accept any JSON number (Pi schema uses ``Type.Number``), store as int."""

    if key not in raw:
        return None
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(_type_error(path, "number"))
        return None
    return int(value)


def _opt_str_map(
    raw: dict, key: str, path: str, errors: list[str]
) -> dict[str, str | None] | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, dict):
        errors.append(_type_error(path, "object"))
        return None
    return dict(value)


def _opt_obj(
    raw: dict, key: str, path: str, errors: list[str]
) -> dict[str, object] | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, dict):
        errors.append(_type_error(path, "object"))
        return None
    return dict(value)


def _coerce_provider_config(
    raw: object, path: str, errors: list[str]
) -> ProviderConfig | None:
    if not isinstance(raw, dict):
        errors.append(_type_error(path, "object"))
        return None
    models: list[ModelDefinition] = []
    if "models" in raw:
        if not isinstance(raw["models"], list):
            errors.append(_type_error(f"{path}/models", "array"))
        else:
            for index, entry in enumerate(raw["models"]):
                model = _coerce_model_def(entry, f"{path}/models/{index}", errors)
                if model is not None:
                    models.append(model)
    overrides: dict[str, ModelOverride] = {}
    if "modelOverrides" in raw:
        if not isinstance(raw["modelOverrides"], dict):
            errors.append(_type_error(f"{path}/modelOverrides", "object"))
        else:
            for model_id, entry in raw["modelOverrides"].items():
                override = _coerce_model_override(
                    entry, f"{path}/modelOverrides/{model_id}", errors
                )
                if override is not None:
                    overrides[model_id] = override
    return ProviderConfig(
        name=_opt_str(raw, "name", f"{path}/name", errors),
        base_url=_opt_str(raw, "baseUrl", f"{path}/baseUrl", errors),
        api_key=_opt_str(raw, "apiKey", f"{path}/apiKey", errors),
        api=_opt_str(raw, "api", f"{path}/api", errors),
        headers=_opt_str_map(raw, "headers", f"{path}/headers", errors),
        auth_header=bool(_opt_bool(raw, "authHeader", f"{path}/authHeader", errors)),
        compat=_opt_obj(raw, "compat", f"{path}/compat", errors),
        models=tuple(models),
        model_overrides=overrides,
    )


@dataclass(frozen=True, slots=True)
class ModelsConfig:
    providers: Mapping[str, ProviderConfig]


def _validate_schema(parsed: object, path: Path) -> tuple[ModelsConfig | None, str | None]:
    errors: list[str] = []
    if not isinstance(parsed, dict):
        return None, _format_schema_error(["  - (root): expected object"], path)
    providers_raw = parsed.get("providers")
    if not isinstance(providers_raw, dict):
        return None, _format_schema_error(
            ["  - providers: expected object"], path
        )
    providers: dict[str, ProviderConfig] = {}
    for provider_name, provider_raw in providers_raw.items():
        config = _coerce_provider_config(
            provider_raw, f"/providers/{provider_name}", errors
        )
        if config is not None:
            providers[provider_name] = config
    if errors:
        return None, _format_schema_error(errors, path)
    return ModelsConfig(providers=providers), None


def _format_schema_error(errors: list[str], path: Path) -> str:
    body = "\n".join(errors) or "Unknown schema error"
    return f"Invalid models.json schema:\n{body}\n\nFile: {path}"


def _validate_semantics(
    config: ModelsConfig, builtin_providers: set[str], path: Path
) -> str | None:
    """Pi's ``validateConfig`` semantic checks, wrapped path-qualified."""

    for provider_name, provider_config in config.providers.items():
        is_builtin = provider_name in builtin_providers
        has_provider_api = bool(provider_config.api)
        models = provider_config.models
        has_overrides = bool(provider_config.model_overrides)

        if not models:
            # Pi treats a present (even empty) JS object as truthy here, so a
            # present headers/compat object counts as a usable field.
            if (
                not provider_config.base_url
                and provider_config.headers is None
                and provider_config.compat is None
                and not has_overrides
            ):
                return _wrap_semantic(
                    f'Provider {provider_name}: must specify "baseUrl", '
                    '"headers", "compat", "modelOverrides", or "models".',
                    path,
                )
        elif not is_builtin:
            if not provider_config.base_url:
                return _wrap_semantic(
                    f'Provider {provider_name}: "baseUrl" is required when '
                    "defining custom models.",
                    path,
                )
            if not provider_config.api_key:
                return _wrap_semantic(
                    f'Provider {provider_name}: "apiKey" is required when '
                    "defining custom models.",
                    path,
                )

        for model_def in models:
            has_model_api = bool(model_def.api)
            if not has_provider_api and not has_model_api and not is_builtin:
                return _wrap_semantic(
                    f'Provider {provider_name}, model {model_def.id}: no "api" '
                    "specified. Set at provider or model level.",
                    path,
                )
            if model_def.context_window is not None and model_def.context_window <= 0:
                return _wrap_semantic(
                    f"Provider {provider_name}, model {model_def.id}: invalid "
                    "contextWindow",
                    path,
                )
            if model_def.max_tokens is not None and model_def.max_tokens <= 0:
                return _wrap_semantic(
                    f"Provider {provider_name}, model {model_def.id}: invalid "
                    "maxTokens",
                    path,
                )
    return None


def _wrap_semantic(message: str, path: Path) -> str:
    return f"Failed to load models.json: {message}\n\nFile: {path}"


# --------------------------------------------------------------------------- #
# Merge helpers
# --------------------------------------------------------------------------- #


def _merge_compat(
    base: Mapping[str, object] | None, override: Mapping[str, object] | None
) -> Mapping[str, object] | None:
    # Pi: ``if (!overrideCompat) return base`` — a present (even empty) compat
    # object is truthy, so only ``None`` (absent) short-circuits.
    if override is None:
        return base
    merged: dict[str, object] = dict(base or {})
    merged.update(override)
    # Deep-merge nested routing objects (Pi merges openRouterRouting/vercel).
    for key in ("openRouterRouting", "vercelGatewayRouting"):
        base_val = (base or {}).get(key) if base else None
        over_val = override.get(key)
        if isinstance(base_val, dict) or isinstance(over_val, dict):
            merged[key] = {
                **(base_val if isinstance(base_val, dict) else {}),
                **(over_val if isinstance(over_val, dict) else {}),
            }
    return merged


def _apply_model_override(
    row: NativeModelSpec, override: ModelOverride
) -> NativeModelSpec:
    changes: dict[str, object] = {}
    if override.name is not None:
        changes["display_name"] = override.name
    if override.reasoning is not None:
        changes["reasoning"] = override.reasoning
    if override.thinking_level_map is not None:
        changes["thinking_level_map"] = {
            **dict(row.thinking_level_map),
            **dict(override.thinking_level_map),
        }
    if override.input is not None:
        changes["input"] = override.input
    if override.context_window is not None:
        changes["context_window"] = override.context_window
    if override.max_tokens is not None:
        changes["max_tokens"] = override.max_tokens
    if override.cost is not None:
        # Partial merge: each present sub-field (incl. explicit 0) wins; absent
        # sub-fields fall back to the built-in row's value.
        changes["cost"] = NativeModelCost(
            input=override.cost.get("input", row.cost.input),
            output=override.cost.get("output", row.cost.output),
            cache_read=override.cost.get("cache_read", row.cost.cache_read),
            cache_write=override.cost.get("cache_write", row.cost.cache_write),
        )
    if override.headers is not None:
        changes["headers"] = dict(override.headers)
    merged_compat = _merge_compat(row.compat, override.compat)
    if merged_compat is not None:
        changes["compat"] = merged_compat
    return replace(row, **changes)  # type: ignore[arg-type]


def _custom_model_row(
    provider_name: str,
    provider_config: ProviderConfig,
    model_def: ModelDefinition,
    builtin_defaults: tuple[str, str | None] | None,
) -> NativeModelSpec | None:
    api = model_def.api or provider_config.api or (
        builtin_defaults[0] if builtin_defaults else None
    )
    if not api:
        return None
    base_url = model_def.base_url or provider_config.base_url or (
        builtin_defaults[1] if builtin_defaults else None
    )
    if not base_url:
        # Pi: ``if (!baseUrl) continue`` — skip a custom model whose baseUrl
        # cannot be resolved at model/provider/built-in level.
        return None
    compat = _merge_compat(provider_config.compat, model_def.compat)
    return NativeModelSpec(
        provider_name=provider_name,
        model_id=model_def.id,
        display_name=model_def.name or model_def.id,
        api=api,
        base_url=base_url,
        reasoning=bool(model_def.reasoning),
        thinking_level_map=dict(model_def.thinking_level_map or {}),
        input=model_def.input or ("text",),
        cost=model_def.cost or NativeModelCost(),
        context_window=model_def.context_window
        if model_def.context_window is not None
        else 128_000,
        max_tokens=model_def.max_tokens if model_def.max_tokens is not None else 16_384,
        headers=dict(model_def.headers) if model_def.headers else None,
        compat=compat,
    )


# --------------------------------------------------------------------------- #
# ModelCatalog — central registry (built-in + models.json)
# --------------------------------------------------------------------------- #


@dataclass
class ModelCatalog:
    """Built-in catalog deep-merged with ``models.json`` custom/override layer.

    The pipy analogue of Pi's ``ModelRegistry`` for the catalog/merge concern.
    Auth resolution (M6), OAuth ``modify_models`` (M7), and dynamic provider
    registration (M12) compose with this structure.
    """

    builtin: NativeCatalog = field(default_factory=build_builtin_catalog)
    models_json_path: Path | None = None
    env: Mapping[str, str] | None = None
    # Provider configs injected as if defined in models.json (e.g. the ds4 env
    # shim). A real file ``models.json`` entry for the same provider wins.
    extra_providers: Mapping[str, "ProviderConfig"] | None = None

    rows: tuple[NativeModelSpec, ...] = field(init=False, default=())
    error: str | None = field(init=False, default=None)
    provider_request_configs: dict[str, ProviderRequestConfig] = field(
        init=False, default_factory=dict
    )
    _config: ModelsConfig | None = field(init=False, default=None)
    # Dynamically registered providers (Pi's registerProvider): applied after
    # the file + extra providers, so a dynamic registration overrides both.
    _registered: dict[str, "ProviderConfig"] = field(init=False, default_factory=dict)
    # OAuth modify-models hooks applied to the merged rows (Pi's modifyModels,
    # e.g. Copilot rewriting baseUrl from the token's proxy-ep claim).
    _oauth_modifiers: list = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self.refresh()

    # -- dynamic registration ------------------------------------------------

    def register_provider(self, name: str, config: "ProviderConfig") -> None:
        """Register (or replace) a provider config dynamically, then refresh.

        Supports full replacement / override-only / custom-model registration
        the same way a ``models.json`` provider entry does.
        """

        self._registered[name] = config
        self.refresh()

    def unregister_provider(self, name: str) -> None:
        self._registered.pop(name, None)
        self.refresh()

    def set_oauth_modifiers(self, modifiers: list) -> None:
        """Set the OAuth modify-models hooks applied after each merge."""

        self._oauth_modifiers = list(modifiers)

    # -- load / merge --------------------------------------------------------

    def refresh(self) -> None:
        self.provider_request_configs = {}
        self.error = None
        self._config = None

        file_config = self._load_models_json()
        combined = self._combine(file_config)
        merged = self._merge(combined)
        for modifier in self._oauth_modifiers:
            merged = list(modifier(merged))
        self.rows = tuple(merged)

    def _combine(self, file_config: ModelsConfig | None) -> ModelsConfig | None:
        if not self.extra_providers and not self._registered:
            return file_config
        # Precedence (low -> high): extra providers (e.g. ds4 env shim), then a
        # real file entry, then a dynamically registered provider.
        providers: dict[str, ProviderConfig] = dict(self.extra_providers or {})
        if file_config is not None:
            providers.update(file_config.providers)
        providers.update(self._registered)
        return ModelsConfig(providers=providers)

    def _load_models_json(self) -> ModelsConfig | None:
        path = self.models_json_path
        if path is None or not path.exists():
            return None
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            self.error = f"Failed to load models.json: {exc}\n\nFile: {path}"
            return None
        try:
            parsed = json.loads(strip_json_comments(content))
        except json.JSONDecodeError as exc:
            self.error = f"Failed to parse models.json: {exc}\n\nFile: {path}"
            return None

        config, schema_error = _validate_schema(parsed, path)
        if schema_error is not None:
            self.error = schema_error
            return None
        assert config is not None
        semantic_error = _validate_semantics(
            config, set(self.builtin.providers()), path
        )
        if semantic_error is not None:
            self.error = semantic_error
            return None
        self._config = config
        return config

    def _merge(self, config: ModelsConfig | None) -> list[NativeModelSpec]:
        # 1. Built-ins with provider-level + per-model overrides applied.
        overrides: dict[str, ProviderConfig] = (
            dict(config.providers) if config else {}
        )
        merged: list[NativeModelSpec] = []
        for row in self.builtin.get_all():
            provider_config = overrides.get(row.provider_name)
            new_row = row
            if provider_config is not None:
                if provider_config.base_url or provider_config.compat is not None:
                    new_row = replace(
                        new_row,
                        base_url=provider_config.base_url or new_row.base_url,
                        compat=_merge_compat(new_row.compat, provider_config.compat),
                    )
                model_override = provider_config.model_overrides.get(row.model_id)
                if model_override is not None:
                    new_row = _apply_model_override(new_row, model_override)
            merged.append(new_row)

        # 2. Store provider request configs (auth/headers) for the auth layer.
        if config:
            for provider_name, provider_config in config.providers.items():
                if (
                    provider_config.api_key
                    or provider_config.headers
                    or provider_config.auth_header
                ):
                    self.provider_request_configs[provider_name] = ProviderRequestConfig(
                        api_key=provider_config.api_key,
                        headers=provider_config.headers,
                        auth_header=provider_config.auth_header,
                    )

        # 3. Parse + merge custom models by provider+id (custom wins).
        if config:
            builtin_providers = set(self.builtin.providers())
            for provider_name, provider_config in config.providers.items():
                if not provider_config.models:
                    continue
                builtin_defaults = self._builtin_defaults(
                    provider_name, builtin_providers
                )
                for model_def in provider_config.models:
                    custom = _custom_model_row(
                        provider_name, provider_config, model_def, builtin_defaults
                    )
                    if custom is None:
                        continue
                    index = next(
                        (
                            i
                            for i, m in enumerate(merged)
                            if m.provider_name == custom.provider_name
                            and m.model_id == custom.model_id
                        ),
                        -1,
                    )
                    if index >= 0:
                        merged[index] = custom
                    else:
                        merged.append(custom)
        return merged

    def _builtin_defaults(
        self, provider_name: str, builtin_providers: set[str]
    ) -> tuple[str, str | None] | None:
        if provider_name not in builtin_providers:
            return None
        rows = self.builtin.models_for(provider_name)
        if not rows:
            return None
        return rows[0].api, rows[0].base_url

    # -- read API ------------------------------------------------------------

    def get_all(self) -> list[NativeModelSpec]:
        return list(self.rows)

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

    def models_for(self, provider_name: str) -> list[NativeModelSpec]:
        lowered = provider_name.lower()
        return [r for r in self.rows if r.provider_name.lower() == lowered]

    def providers(self) -> list[str]:
        seen: list[str] = []
        for row in self.rows:
            if row.provider_name not in seen:
                seen.append(row.provider_name)
        return seen
