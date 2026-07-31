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
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import (
    dataclass,
    field,
    fields as dataclass_fields,
    is_dataclass,
    replace,
)
from pathlib import Path
from types import MappingProxyType
from typing import TypeVar, TypedDict, cast

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


def _string_object_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _object_list(value: object) -> list[object] | None:
    if not isinstance(value, list):
        return None
    return [item for item in value]


def _type_error(path: str, expected: str) -> str:
    # Dot-path format (Pi's formatValidationPath), e.g.
    # "providers.anthropic.baseUrl" rather than a JSON pointer.
    dotted = path.lstrip("/").replace("/", ".")
    return f"  - {dotted}: expected {expected}"


def _coerce_cost_fields(
    value: object, path: str, errors: list[str]
) -> dict[str, float]:
    """Return only the cost sub-fields present in the JSON (attr-named)."""

    values = _string_object_dict(value)
    if values is None:
        errors.append(_type_error(path, "object"))
        return {}
    nums: dict[str, float] = {}
    for key, attr in (
        ("input", "input"),
        ("output", "output"),
        ("cacheRead", "cache_read"),
        ("cacheWrite", "cache_write"),
    ):
        if key in values:
            raw = values[key]
            if not isinstance(raw, (int, float)) or isinstance(raw, bool):
                errors.append(_type_error(f"{path}/{key}", "number"))
            else:
                nums[attr] = float(raw)
    return nums


def _coerce_cost(value: object, path: str, errors: list[str]) -> NativeModelCost | None:
    """Full cost object for a custom model (missing sub-fields default to 0)."""

    fields = _coerce_cost_fields(value, path, errors)
    return NativeModelCost(**fields)


def _coerce_input(
    value: object, path: str, errors: list[str]
) -> tuple[str, ...] | None:
    values = _object_list(value)
    if values is None:
        errors.append(_type_error(path, 'array of "text"/"image"'))
        return None
    inputs: list[str] = []
    for item in values:
        if not isinstance(item, str) or item not in ("text", "image"):
            errors.append(_type_error(path, 'array of "text"/"image"'))
            return None
        inputs.append(item)
    return tuple(inputs)


def _coerce_full_cost(
    value: object, path: str, errors: list[str]
) -> NativeModelCost | None:
    """A custom model's full cost: all four sub-fields required (Pi schema)."""

    values = _string_object_dict(value)
    if values is None:
        errors.append(_type_error(path, "object"))
        return None
    required = ("input", "output", "cacheRead", "cacheWrite")
    missing = [key for key in required if key not in values]
    if missing:
        errors.append(
            _type_error(
                path,
                f"object with {', '.join(required)} (missing {', '.join(missing)})",
            )
        )
        return None
    return _coerce_cost(values, path, errors)


def _coerce_model_def(
    raw: object, path: str, errors: list[str]
) -> ModelDefinition | None:
    values = _string_object_dict(raw)
    if values is None:
        errors.append(_type_error(path, "object"))
        return None
    model_id = values.get("id")
    if not isinstance(model_id, str) or not model_id:
        errors.append(_type_error(f"{path}/id", "non-empty string"))
        model_id = model_id if isinstance(model_id, str) else ""
    cost = (
        _coerce_full_cost(values["cost"], f"{path}/cost", errors)
        if "cost" in values
        else None
    )
    input_ = (
        _coerce_input(values["input"], f"{path}/input", errors)
        if "input" in values
        else None
    )
    return ModelDefinition(
        id=model_id,
        name=_opt_str(values, "name", f"{path}/name", errors),
        api=_opt_str(values, "api", f"{path}/api", errors),
        base_url=_opt_str(values, "baseUrl", f"{path}/baseUrl", errors),
        reasoning=_opt_bool(values, "reasoning", f"{path}/reasoning", errors),
        thinking_level_map=_opt_thinking_map(
            values, "thinkingLevelMap", f"{path}/thinkingLevelMap", errors
        ),
        input=input_,
        cost=cost,
        context_window=_opt_number_as_int(
            values, "contextWindow", f"{path}/contextWindow", errors
        ),
        max_tokens=_opt_number_as_int(values, "maxTokens", f"{path}/maxTokens", errors),
        headers=_opt_header_map(values, "headers", f"{path}/headers", errors),
        compat=_opt_obj(values, "compat", f"{path}/compat", errors),
    )


def _coerce_model_override(
    raw: object, path: str, errors: list[str]
) -> ModelOverride | None:
    values = _string_object_dict(raw)
    if values is None:
        errors.append(_type_error(path, "object"))
        return None
    cost = (
        _coerce_cost_fields(values["cost"], f"{path}/cost", errors)
        if "cost" in values
        else None
    )
    input_ = (
        _coerce_input(values["input"], f"{path}/input", errors)
        if "input" in values
        else None
    )
    return ModelOverride(
        name=_opt_str(values, "name", f"{path}/name", errors),
        reasoning=_opt_bool(values, "reasoning", f"{path}/reasoning", errors),
        thinking_level_map=_opt_thinking_map(
            values, "thinkingLevelMap", f"{path}/thinkingLevelMap", errors
        ),
        input=input_,
        cost=cost,
        context_window=_opt_int(
            values, "contextWindow", f"{path}/contextWindow", errors
        ),
        max_tokens=_opt_int(values, "maxTokens", f"{path}/maxTokens", errors),
        headers=_opt_header_map(values, "headers", f"{path}/headers", errors),
        compat=_opt_obj(values, "compat", f"{path}/compat", errors),
    )


def _opt_str(
    raw: Mapping[str, object], key: str, path: str, errors: list[str]
) -> str | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, str):
        errors.append(_type_error(path, "string"))
        return None
    return value


def _opt_bool(
    raw: Mapping[str, object], key: str, path: str, errors: list[str]
) -> bool | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, bool):
        errors.append(_type_error(path, "boolean"))
        return None
    return value


def _opt_int(
    raw: Mapping[str, object], key: str, path: str, errors: list[str]
) -> int | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, int) or isinstance(value, bool):
        errors.append(_type_error(path, "integer"))
        return None
    return value


def _opt_number_as_int(
    raw: Mapping[str, object], key: str, path: str, errors: list[str]
) -> int | None:
    """Accept any JSON number (Pi schema uses ``Type.Number``), store as int."""

    if key not in raw:
        return None
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(_type_error(path, "number"))
        return None
    return int(value)


def _opt_thinking_map(
    raw: Mapping[str, object], key: str, path: str, errors: list[str]
) -> dict[str, str | None] | None:
    """A thinkingLevelMap: known level keys mapping to ``string | null``.

    Mirrors Pi's ``ThinkingLevelMap`` (Partial<Record<level, string|null>>).
    """

    from pipy_harness.native.catalog import THINKING_LEVELS

    if key not in raw:
        return None
    value = _string_object_dict(raw[key])
    if value is None:
        errors.append(_type_error(path, "object"))
        return None
    out: dict[str, str | None] = {}
    for level, mapped in value.items():
        if level not in THINKING_LEVELS:
            errors.append(
                _type_error(f"{path}/{level}", f"one of {', '.join(THINKING_LEVELS)}")
            )
            continue
        if mapped is not None and not isinstance(mapped, str):
            errors.append(_type_error(f"{path}/{level}", "string or null"))
            continue
        out[level] = mapped
    return out


def _opt_header_map(
    raw: Mapping[str, object], key: str, path: str, errors: list[str]
) -> dict[str, str] | None:
    if key not in raw:
        return None
    value = _string_object_dict(raw[key])
    if value is None or not all(isinstance(item, str) for item in value.values()):
        errors.append(_type_error(path, "object of string values"))
        return None
    return {name: item for name, item in value.items() if isinstance(item, str)}


def _opt_obj(
    raw: Mapping[str, object], key: str, path: str, errors: list[str]
) -> dict[str, object] | None:
    if key not in raw:
        return None
    value = _string_object_dict(raw[key])
    if value is None:
        errors.append(_type_error(path, "object"))
        return None
    return value


def _coerce_provider_config(
    raw: object, path: str, errors: list[str]
) -> ProviderConfig | None:
    values = _string_object_dict(raw)
    if values is None:
        errors.append(_type_error(path, "object"))
        return None
    models: list[ModelDefinition] = []
    if "models" in values:
        model_values = _object_list(values["models"])
        if model_values is None:
            errors.append(_type_error(f"{path}/models", "array"))
        else:
            for index, entry in enumerate(model_values):
                model = _coerce_model_def(entry, f"{path}/models/{index}", errors)
                if model is not None:
                    models.append(model)
    overrides: dict[str, ModelOverride] = {}
    if "modelOverrides" in values:
        override_values = _string_object_dict(values["modelOverrides"])
        if override_values is None:
            errors.append(_type_error(f"{path}/modelOverrides", "object"))
        else:
            for model_id, entry in override_values.items():
                override = _coerce_model_override(
                    entry, f"{path}/modelOverrides/{model_id}", errors
                )
                if override is not None:
                    overrides[model_id] = override
    return ProviderConfig(
        name=_opt_str(values, "name", f"{path}/name", errors),
        base_url=_opt_str(values, "baseUrl", f"{path}/baseUrl", errors),
        api_key=_opt_str(values, "apiKey", f"{path}/apiKey", errors),
        api=_opt_str(values, "api", f"{path}/api", errors),
        headers=_opt_header_map(values, "headers", f"{path}/headers", errors),
        auth_header=bool(_opt_bool(values, "authHeader", f"{path}/authHeader", errors)),
        compat=_opt_obj(values, "compat", f"{path}/compat", errors),
        models=tuple(models),
        model_overrides=overrides,
    )


@dataclass(frozen=True, slots=True)
class ModelsConfig:
    providers: Mapping[str, ProviderConfig]


def _validate_schema(
    parsed: object, path: Path
) -> tuple[ModelsConfig | None, str | None]:
    errors: list[str] = []
    root = _string_object_dict(parsed)
    if root is None:
        return None, _format_schema_error(["  - (root): expected object"], path)
    providers_raw = _string_object_dict(root.get("providers"))
    if providers_raw is None:
        return None, _format_schema_error(["  - providers: expected object"], path)
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
        provider_error = _validate_provider_semantics(
            provider_name, provider_config, is_builtin
        )
        if provider_error is not None:
            return _wrap_semantic(provider_error, path)
        for model_def in provider_config.models:
            model_error = _validate_model_semantics(
                provider_name, provider_config, model_def, is_builtin
            )
            if model_error is not None:
                return _wrap_semantic(model_error, path)
    return None


def _validate_provider_semantics(
    provider_name: str, provider_config: ProviderConfig, is_builtin: bool
) -> str | None:
    if not provider_config.models:
        # Pi treats a present (even empty) JS object as truthy here, so a
        # present headers/compat object counts as a usable field.
        if (
            not provider_config.base_url
            and provider_config.headers is None
            and provider_config.compat is None
            and not provider_config.model_overrides
        ):
            return (
                f'Provider {provider_name}: must specify "baseUrl", '
                '"headers", "compat", "modelOverrides", or "models".'
            )
        return None
    if is_builtin:
        return None
    if not provider_config.base_url:
        return (
            f'Provider {provider_name}: "baseUrl" is required when '
            "defining custom models."
        )
    if not provider_config.api_key:
        return (
            f'Provider {provider_name}: "apiKey" is required when '
            "defining custom models."
        )
    return None


def _validate_model_semantics(
    provider_name: str,
    provider_config: ProviderConfig,
    model_def: ModelDefinition,
    is_builtin: bool,
) -> str | None:
    if not provider_config.api and not model_def.api and not is_builtin:
        return (
            f'Provider {provider_name}, model {model_def.id}: no "api" '
            "specified. Set at provider or model level."
        )
    if model_def.context_window is not None and model_def.context_window <= 0:
        return f"Provider {provider_name}, model {model_def.id}: invalid contextWindow"
    if model_def.max_tokens is not None and model_def.max_tokens <= 0:
        return f"Provider {provider_name}, model {model_def.id}: invalid maxTokens"
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
        base_object = _string_object_dict(base_val)
        override_object = _string_object_dict(over_val)
        if base_object is not None or override_object is not None:
            merged[key] = {
                **(base_object or {}),
                **(override_object or {}),
            }
    return merged


def _apply_model_override(
    row: NativeModelSpec, override: ModelOverride
) -> NativeModelSpec:
    thinking_level_map = row.thinking_level_map
    if override.thinking_level_map is not None:
        thinking_level_map = {
            **dict(row.thinking_level_map),
            **dict(override.thinking_level_map),
        }

    cost = row.cost
    if override.cost is not None:
        # Partial merge: each present sub-field (incl. explicit 0) wins; absent
        # sub-fields fall back to the built-in row's value.
        cost = NativeModelCost(
            input=override.cost.get("input", row.cost.input),
            output=override.cost.get("output", row.cost.output),
            cache_read=override.cost.get("cache_read", row.cost.cache_read),
            cache_write=override.cost.get("cache_write", row.cost.cache_write),
        )

    return replace(
        row,
        display_name=override.name if override.name is not None else row.display_name,
        reasoning=(
            override.reasoning if override.reasoning is not None else row.reasoning
        ),
        thinking_level_map=thinking_level_map,
        input=override.input if override.input is not None else row.input,
        cost=cost,
        context_window=(
            override.context_window
            if override.context_window is not None
            else row.context_window
        ),
        max_tokens=(
            override.max_tokens if override.max_tokens is not None else row.max_tokens
        ),
        headers=(
            dict(override.headers) if override.headers is not None else row.headers
        ),
        compat=_merge_compat(row.compat, override.compat),
    )


def _apply_provider_override(
    row: NativeModelSpec, provider_config: ProviderConfig
) -> NativeModelSpec:
    new_row = row
    if provider_config.base_url or provider_config.compat is not None:
        new_row = replace(
            new_row,
            base_url=provider_config.base_url or new_row.base_url,
            compat=_merge_compat(new_row.compat, provider_config.compat),
        )
    model_override = provider_config.model_overrides.get(row.model_id)
    if model_override is not None:
        new_row = _apply_model_override(new_row, model_override)
    return new_row


def _custom_model_row(
    provider_name: str,
    provider_config: ProviderConfig,
    model_def: ModelDefinition,
    builtin_defaults: tuple[str, str | None] | None,
) -> NativeModelSpec | None:
    api = (
        model_def.api
        or provider_config.api
        or (builtin_defaults[0] if builtin_defaults else None)
    )
    if not api:
        return None
    base_url = (
        model_def.base_url
        or provider_config.base_url
        or (builtin_defaults[1] if builtin_defaults else None)
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


def _replace_or_append_model(
    merged: list[NativeModelSpec], custom: NativeModelSpec
) -> None:
    index = next(
        (
            index
            for index, row in enumerate(merged)
            if row.provider_name == custom.provider_name
            and row.model_id == custom.model_id
        ),
        -1,
    )
    if index >= 0:
        merged[index] = custom
    else:
        merged.append(custom)


# --------------------------------------------------------------------------- #
# ModelCatalog — central registry (built-in + models.json)
# --------------------------------------------------------------------------- #


_ModelRowsModifier = Callable[[list[NativeModelSpec]], list[NativeModelSpec]]


def _freeze_object(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_object(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_object(item) for item in value)
    return value


_DetachedValue = TypeVar("_DetachedValue")


def _detach_object(value: _DetachedValue) -> _DetachedValue:
    """Copy catalog values without relying on mapping-proxy pickling."""
    if isinstance(value, Mapping):
        detached_mapping = {key: _detach_object(item) for key, item in value.items()}
        return cast(_DetachedValue, detached_mapping)
    if isinstance(value, (list, tuple)):
        items = (_detach_object(item) for item in value)
        detached_sequence = list(items) if isinstance(value, list) else tuple(items)
        return cast(_DetachedValue, detached_sequence)
    if is_dataclass(value) and not isinstance(value, type):
        changes = {
            item.name: _detach_object(getattr(value, item.name))
            for item in dataclass_fields(value)
        }
        return cast(_DetachedValue, replace(value, **changes))
    return deepcopy(value)


def _freeze_compat(
    value: Mapping[str, object] | None,
) -> Mapping[str, object] | None:
    if value is None:
        return None
    frozen = _freeze_object(value)
    if not isinstance(frozen, Mapping):
        raise TypeError("compat must be a mapping")
    return frozen


_MappingValue = TypeVar("_MappingValue")


def _freeze_mapping(
    value: Mapping[str, _MappingValue] | None,
) -> Mapping[str, _MappingValue] | None:
    return MappingProxyType(dict(value)) if value is not None else None


def _freeze_model_row(row: NativeModelSpec) -> NativeModelSpec:
    return replace(
        row,
        thinking_level_map=MappingProxyType(dict(row.thinking_level_map)),
        headers=_freeze_mapping(row.headers),
        compat=_freeze_compat(row.compat),
    )


def _freeze_provider_config(config: ProviderConfig) -> ProviderConfig:
    models = tuple(
        replace(
            model,
            thinking_level_map=_freeze_mapping(model.thinking_level_map),
            headers=_freeze_mapping(model.headers),
            compat=_freeze_compat(model.compat),
        )
        for model in config.models
    )
    overrides = MappingProxyType(
        {
            model_id: replace(
                override,
                thinking_level_map=_freeze_mapping(override.thinking_level_map),
                cost=_freeze_mapping(override.cost),
                headers=_freeze_mapping(override.headers),
                compat=_freeze_compat(override.compat),
            )
            for model_id, override in config.model_overrides.items()
        }
    )
    return replace(
        config,
        headers=_freeze_mapping(config.headers),
        compat=_freeze_compat(config.compat),
        models=models,
        model_overrides=overrides,
    )


def _freeze_models_config(config: ModelsConfig | None) -> ModelsConfig | None:
    if config is None:
        return None
    return ModelsConfig(
        providers=MappingProxyType(
            {
                name: _freeze_provider_config(provider)
                for name, provider in config.providers.items()
            }
        )
    )


def _freeze_request_configs(
    configs: Mapping[str, ProviderRequestConfig],
) -> Mapping[str, ProviderRequestConfig]:
    return MappingProxyType(
        {
            name: replace(
                request,
                headers=(
                    MappingProxyType(dict(request.headers))
                    if request.headers is not None
                    else None
                ),
            )
            for name, request in configs.items()
        }
    )


_EMPTY_CATALOG_ROWS: tuple[NativeModelSpec, ...] = ()
_EMPTY_CATALOG_REQUEST_CONFIGS: Mapping[str, ProviderRequestConfig] = MappingProxyType(
    {}
)
_EMPTY_CATALOG_CONFIG: ModelsConfig | None = None


class _CatalogReloadCapture(TypedDict):
    owner_token: object
    extra_providers: Mapping[str, ProviderConfig] | None
    registered_providers: Mapping[str, ProviderConfig]
    oauth_modifiers: tuple[_ModelRowsModifier, ...]


@dataclass(slots=True, repr=False)
class ModelCatalogRefreshValue:
    expected_owner_token: object | None
    replacement_owner_token: object | None
    rows: tuple[NativeModelSpec, ...]
    error: str | None
    provider_request_configs: Mapping[str, ProviderRequestConfig]
    config: ModelsConfig | None
    replacement_rows: tuple[NativeModelSpec, ...] = field(repr=False)
    replacement_provider_request_configs: Mapping[str, ProviderRequestConfig] = field(
        repr=False
    )
    replacement_config: ModelsConfig | None = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.expected_owner_token) is not object:
            raise TypeError("expected_owner_token must be an exact object")
        if type(self.replacement_owner_token) is not object:
            raise TypeError("replacement_owner_token must be an exact object")
        self.rows = tuple(_freeze_model_row(row) for row in self.rows)
        self.provider_request_configs = _freeze_request_configs(
            self.provider_request_configs
        )
        self.config = _freeze_models_config(self.config)
        self.replacement_rows = _detach_object(self.replacement_rows)
        self.replacement_provider_request_configs = _detach_object(
            self.replacement_provider_request_configs
        )
        self.replacement_config = _detach_object(self.replacement_config)


@dataclass
class ModelCatalog:
    """Built-in catalog deep-merged with ``models.json`` custom/override layer.

    The pipy analogue of Pi's ``ModelRegistry`` for the catalog/merge concern.
    Auth resolution (M6), OAuth ``modify_models`` (M7), and dynamic provider
    registration (M12) compose here. This synchronous owner is confined to the
    single session thread and is not thread-safe. Inputs/results are immutable
    by contract; only owner refresh/registration/OAuth APIs replace state.
    """

    builtin: NativeCatalog = field(default_factory=build_builtin_catalog)
    models_json_path: Path | None = None
    env: Mapping[str, str] | None = None
    # Provider configs injected as if defined in models.json (e.g. the ds4 env
    # shim). A real file ``models.json`` entry for the same provider wins.
    extra_providers: Mapping[str, "ProviderConfig"] | None = None

    rows: tuple[NativeModelSpec, ...] = field(init=False, default=())
    error: str | None = field(init=False, default=None)
    provider_request_configs: Mapping[str, ProviderRequestConfig] = field(
        init=False, default_factory=dict
    )
    _config: ModelsConfig | None = field(init=False, default=None)
    # Dynamically registered providers (Pi's registerProvider): applied after
    # the file + extra providers, so a dynamic registration overrides both.
    _registered: Mapping[str, "ProviderConfig"] = field(
        init=False, default_factory=dict
    )
    # OAuth modify-models hooks applied to the merged rows (Pi's modifyModels,
    # e.g. Copilot rewriting baseUrl from the token's proxy-ep claim).
    _oauth_modifiers: tuple[_ModelRowsModifier, ...] = field(init=False, default=())
    _reload_identity: object = field(init=False, default_factory=object)

    def __post_init__(self) -> None:
        self.refresh()

    # -- dynamic registration ------------------------------------------------

    def register_provider(self, name: str, config: "ProviderConfig") -> None:
        """Register (or replace) a provider config dynamically, then refresh.

        Supports full replacement / override-only / custom-model registration
        the same way a ``models.json`` provider entry does.
        """

        self._registered = {**self._registered, name: config}
        self.refresh()

    def unregister_provider(self, name: str) -> None:
        self._registered = {
            registered_name: config
            for registered_name, config in self._registered.items()
            if registered_name != name
        }
        self.refresh()

    def set_oauth_modifiers(self, modifiers: list[_ModelRowsModifier]) -> None:
        """Set the OAuth modify-models hooks applied after each merge."""

        self._oauth_modifiers = tuple(modifiers)
        self._reload_identity = object()

    # -- load / merge --------------------------------------------------------

    def refresh(self) -> None:
        self.provider_request_configs = {}
        self.error = None
        self._config = None
        self._reload_identity = object()

        file_config = self._load_models_json()
        combined = self._combine(file_config)
        merged = self._merge(combined)
        for modifier in self._oauth_modifiers:
            merged = list(modifier(merged))
        self.rows = tuple(merged)
        self._reload_identity = object()

    def capture_catalog_reload_expected(self) -> _CatalogReloadCapture:
        return {
            "owner_token": self._reload_identity,
            "extra_providers": (
                _detach_object(self.extra_providers)
                if self.extra_providers is not None
                else None
            ),
            "registered_providers": _detach_object(self._registered),
            "oauth_modifiers": tuple(self._oauth_modifiers),
        }

    def prepare_catalog_reload_from_snapshot(
        self, captured: _CatalogReloadCapture
    ) -> ModelCatalogRefreshValue:
        file_config, error = self._read_models_json()
        combined = self._combine_inputs(
            file_config,
            captured["extra_providers"],
            captured["registered_providers"],
        )
        merged, request_configs = self._merge_detached(combined)
        modifiers = captured["oauth_modifiers"]
        for modifier in modifiers:
            merged = list(modifier(merged))
        replacement_rows = tuple(merged)
        prepared = ModelCatalogRefreshValue(
            expected_owner_token=captured["owner_token"],
            replacement_owner_token=object(),
            rows=replacement_rows,
            error=error,
            provider_request_configs=request_configs,
            config=file_config,
            replacement_rows=replacement_rows,
            replacement_provider_request_configs=request_configs,
            replacement_config=file_config,
        )
        if not self.validate_prepared_catalog_reload(prepared):
            raise ValueError("invalid prepared catalog replacement")
        return prepared

    def prepare_catalog_reload(self) -> ModelCatalogRefreshValue:
        return self.prepare_catalog_reload_from_snapshot(
            self.capture_catalog_reload_expected()
        )

    def validate_prepared_catalog_reload(self, prepared: object) -> bool:
        return (
            type(prepared) is ModelCatalogRefreshValue
            and tuple(_freeze_model_row(row) for row in prepared.replacement_rows)
            == prepared.rows
            and _freeze_request_configs(prepared.replacement_provider_request_configs)
            == prepared.provider_request_configs
            and _freeze_models_config(prepared.replacement_config) == prepared.config
        )

    def catalog_reload_matches_expected(self, prepared: object) -> bool:
        return (
            type(prepared) is ModelCatalogRefreshValue
            and prepared.expected_owner_token is self._reload_identity
        )

    def publish_catalog_reload(self, prepared: ModelCatalogRefreshValue) -> None:
        if prepared.expected_owner_token is None:
            return
        self.rows = prepared.replacement_rows
        self.error = prepared.error
        self.provider_request_configs = prepared.replacement_provider_request_configs
        self._config = prepared.replacement_config
        self._reload_identity = prepared.replacement_owner_token
        prepared.rows = _EMPTY_CATALOG_ROWS
        prepared.error = None
        prepared.provider_request_configs = _EMPTY_CATALOG_REQUEST_CONFIGS
        prepared.config = _EMPTY_CATALOG_CONFIG
        prepared.replacement_rows = _EMPTY_CATALOG_ROWS
        prepared.replacement_provider_request_configs = _EMPTY_CATALOG_REQUEST_CONFIGS
        prepared.replacement_config = _EMPTY_CATALOG_CONFIG
        prepared.expected_owner_token = None
        prepared.replacement_owner_token = None

    def _combine(self, file_config: ModelsConfig | None) -> ModelsConfig | None:
        return self._combine_inputs(file_config, self.extra_providers, self._registered)

    def _combine_inputs(
        self,
        file_config: ModelsConfig | None,
        extra_providers: Mapping[str, ProviderConfig] | None,
        registered: Mapping[str, ProviderConfig],
    ) -> ModelsConfig | None:
        if not extra_providers and not registered:
            return file_config
        # Precedence (low -> high): extra providers (e.g. ds4 env shim), then a
        # real file entry, then a dynamically registered provider.
        providers: dict[str, ProviderConfig] = dict(extra_providers or {})
        if file_config is not None:
            providers.update(file_config.providers)
        providers.update(registered)
        return ModelsConfig(providers=providers)

    def _read_models_json(self) -> tuple[ModelsConfig | None, str | None]:
        path = self.models_json_path
        if path is None or not path.exists():
            return None, None
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            return None, f"Failed to load models.json: {exc}\n\nFile: {path}"
        try:
            parsed = json.loads(strip_json_comments(content))
        except json.JSONDecodeError as exc:
            return None, f"Failed to parse models.json: {exc}\n\nFile: {path}"

        config, schema_error = _validate_schema(parsed, path)
        if schema_error is not None:
            return None, schema_error
        assert config is not None
        semantic_error = _validate_semantics(
            config, set(self.builtin.providers()), path
        )
        if semantic_error is not None:
            return None, semantic_error
        return config, None

    def _load_models_json(self) -> ModelsConfig | None:
        config, error = self._read_models_json()
        if error is not None:
            self.error = error
            return None
        self._config = config
        return config

    def _merge(self, config: ModelsConfig | None) -> list[NativeModelSpec]:
        overrides = dict(config.providers) if config else {}
        merged = self._merge_builtin_rows(overrides)
        self._store_provider_request_configs(config)
        self._merge_custom_model_rows(merged, config)
        return merged

    def _merge_detached(
        self, config: ModelsConfig | None
    ) -> tuple[list[NativeModelSpec], dict[str, ProviderRequestConfig]]:
        overrides = dict(config.providers) if config else {}
        merged = self._merge_builtin_rows(overrides)
        request_configs = self._provider_request_configs(config)
        self._merge_custom_model_rows(merged, config)
        return merged, request_configs

    def _merge_builtin_rows(
        self, overrides: Mapping[str, ProviderConfig]
    ) -> list[NativeModelSpec]:
        merged: list[NativeModelSpec] = []
        for row in self.builtin.get_all():
            provider_config = overrides.get(row.provider_name)
            new_row = row
            if provider_config is not None:
                new_row = _apply_provider_override(new_row, provider_config)
            merged.append(new_row)
        return merged

    def _provider_request_configs(
        self, config: ModelsConfig | None
    ) -> dict[str, ProviderRequestConfig]:
        request_configs: dict[str, ProviderRequestConfig] = {}
        if config is None:
            return request_configs
        for provider_name, provider_config in config.providers.items():
            if (
                provider_config.api_key
                or provider_config.headers
                or provider_config.auth_header
            ):
                request_configs[provider_name] = ProviderRequestConfig(
                    api_key=provider_config.api_key,
                    headers=provider_config.headers,
                    auth_header=provider_config.auth_header,
                )
        return request_configs

    def _store_provider_request_configs(self, config: ModelsConfig | None) -> None:
        self.provider_request_configs = {
            **self.provider_request_configs,
            **self._provider_request_configs(config),
        }

    def _merge_custom_model_rows(
        self, merged: list[NativeModelSpec], config: ModelsConfig | None
    ) -> None:
        if config is None:
            return
        builtin_providers = set(self.builtin.providers())
        for provider_name, provider_config in config.providers.items():
            builtin_defaults = self._builtin_defaults(provider_name, builtin_providers)
            for model_def in provider_config.models:
                custom = _custom_model_row(
                    provider_name, provider_config, model_def, builtin_defaults
                )
                if custom is not None:
                    _replace_or_append_model(merged, custom)

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
