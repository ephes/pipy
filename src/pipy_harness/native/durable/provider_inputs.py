"""Own construction inputs before private resolution; no new provider policy."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Any, cast

from pipy_harness.native.auth_store import ProviderAuthRequestConfig
from pipy_harness.native.catalog import NativeModelSpec

from .codec import OwnedJson
from .provider_settings import refuse


def _own(value: object, active: set[int] | None = None) -> Any:
    active = set() if active is None else active
    kind = type(value)
    if kind not in (dict, MappingProxyType, tuple, list):
        return OwnedJson(value).thaw()
    if id(value) in active:
        refuse()
    active.add(id(value))
    try:
        if kind in (tuple, list):
            items = [
                _own(item, active)
                for item in cast(list[object] | tuple[object, ...], value)
            ]
            return tuple(items) if kind is tuple else items
        mapping = cast(Mapping[str, object], value)
        if any(type(key) is not str for key in mapping):
            refuse()
        owned = {key: _own(item, active) for key, item in mapping.items()}
        return MappingProxyType(owned) if kind is MappingProxyType else owned
    finally:
        active.remove(id(value))


def _headers(value: object) -> Mapping[str, str] | None:
    if value is None:
        return None
    if type(value) not in (dict, MappingProxyType):
        refuse()
    if any(
        type(key) is not str or type(item) is not str
        for key, item in cast(Mapping[str, object], value).items()
    ):
        refuse()
    return cast(Mapping[str, str], _own(value))


def own_construction_inputs(
    spec: NativeModelSpec,
    config: ProviderAuthRequestConfig | None,
    env: Mapping[str, str],
) -> tuple[NativeModelSpec, ProviderAuthRequestConfig | None, dict[str, str]]:
    if type(spec) is not NativeModelSpec or type(env) not in (
        dict,
        MappingProxyType,
        type(os.environ),
    ):
        refuse()
    if any(
        type(value) is not str
        for value in (spec.provider_name, spec.model_id, spec.api)
    ):
        refuse()
    if (spec.base_url is not None and type(spec.base_url) is not str) or type(
        spec.reasoning
    ) is not bool:
        refuse()
    if config is not None:
        if (
            type(config) is not ProviderAuthRequestConfig
            or type(config.auth_header) is not bool
        ):
            refuse()
        if config.api_key is not None and type(config.api_key) is not str:
            refuse()
        config = replace(config, headers=_headers(config.headers))
    environment = dict(env)
    if any(
        type(key) is not str or type(value) is not str
        for key, value in environment.items()
    ):
        refuse()
    thinking = spec.thinking_level_map
    if type(thinking) not in (dict, MappingProxyType) or any(
        type(key) is not str or (item is not None and type(item) is not str)
        for key, item in thinking.items()
    ):
        refuse()
    return (
        replace(
            spec,
            headers=_headers(spec.headers),
            thinking_level_map=cast(Mapping[str, str | None], _own(thinking)),
            compat=_own(spec.compat),
        ),
        config,
        environment,
    )
