"""Normalization leaves for extension provider registration."""

from __future__ import annotations

from dataclasses import replace

from pipy_harness.native.extension_types import (
    REASON_INVALID_PROVIDER,
    ExtensionOAuthConfig,
    _ActivationError,
)


def _coerce_activation_string(value: object, reason: str) -> str:
    """Accept prior str subclasses but detach an exact plain str before staging."""

    if not isinstance(value, str):
        raise _ActivationError(reason)
    return str.__str__(value)


def _normalize_provider_name(raw_name: object) -> str:
    name = _coerce_activation_string(raw_name, REASON_INVALID_PROVIDER).strip()
    if not name or "/" in name:
        raise _ActivationError(REASON_INVALID_PROVIDER)
    return name


def _normalize_provider_models(models: object) -> tuple[str, ...]:
    if not isinstance(models, tuple) or not models:
        raise _ActivationError(REASON_INVALID_PROVIDER)
    model_ids: list[str] = []
    for model in models:
        model_id = _coerce_activation_string(model, REASON_INVALID_PROVIDER).strip()
        if not model_id:
            raise _ActivationError(REASON_INVALID_PROVIDER)
        model_ids.append(model_id)
    return tuple(model_ids)


def _normalize_default_model(
    default_model: object,
    model_ids: tuple[str, ...],
) -> str | None:
    if isinstance(default_model, str):
        default_model = _coerce_activation_string(
            default_model, REASON_INVALID_PROVIDER
        ).strip()
    if default_model is not None and (
        not isinstance(default_model, str)
        or not default_model
        or default_model not in model_ids
    ):
        raise _ActivationError(REASON_INVALID_PROVIDER)
    return default_model


def _normalize_provider_oauth(oauth: object) -> ExtensionOAuthConfig | None:
    if oauth is None:
        return None
    if not isinstance(oauth, ExtensionOAuthConfig):
        raise _ActivationError(REASON_INVALID_PROVIDER)
    raw_oauth_name = oauth.name
    oauth_name = (
        _coerce_activation_string(raw_oauth_name, REASON_INVALID_PROVIDER).strip()
        if isinstance(raw_oauth_name, str)
        else ""
    )
    if not oauth_name:
        raise _ActivationError(REASON_INVALID_PROVIDER)
    login = oauth.login
    refresh_token = oauth.refresh_token
    get_api_key = oauth.get_api_key
    modify_models = oauth.modify_models
    if (
        not callable(login)
        or not callable(refresh_token)
        or not callable(get_api_key)
        or (modify_models is not None and not callable(modify_models))
    ):
        raise _ActivationError(REASON_INVALID_PROVIDER)
    return replace(oauth, name=oauth_name)
