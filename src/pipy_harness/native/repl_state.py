"""Native REPL provider/model selection state."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO

from pipy_harness.capture import sanitize_text
from pipy_harness.native.openai_codex_provider import (
    OpenAICodexAuthManager,
    default_openai_codex_auth_path,
)
from pipy_harness.native.provider_registry import (
    DEFAULT_NATIVE_MODELS,
    NATIVE_PROVIDER_REGISTRY,
    SUPPORTED_NATIVE_PROVIDERS,
    native_provider_available,
    native_provider_unavailable_message,
)
from pipy_harness.native.provider import ProviderPort


@dataclass(frozen=True, slots=True)
class NativeModelSelection:
    """Current provider/model selection for one native REPL."""

    provider_name: str
    model_id: str

    @property
    def reference(self) -> str:
        return f"{self.provider_name}/{self.model_id}"


@dataclass(frozen=True, slots=True)
class NativeModelOption:
    """A model reference exposed by the REPL selector / settings overlay.

    Capability metadata (context window, reasoning, image input) is optional so
    the legacy one-default-per-provider path keeps working; the catalog-backed
    path populates it so the selector can render Pi-equivalent rows.
    """

    selection: NativeModelSelection
    available: bool
    reason: str | None = None
    context_window: int | None = None
    max_tokens: int | None = None
    reasoning: bool | None = None
    image_input: bool | None = None


class NativeProviderFactory(Protocol):
    def __call__(self, selection: NativeModelSelection) -> ProviderPort:
        """Build a provider for the selected provider/model."""


class NativeDefaultsStore:
    """Private JSON store for non-secret native provider/model defaults."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_native_defaults_path()

    def load(self) -> NativeModelSelection | None:
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(body, dict):
            return None
        if body.get("schema") != "pipy.native-defaults" or body.get("schema_version") != 1:
            return None
        provider_name = body.get("provider")
        model_id = body.get("model_id")
        if not isinstance(provider_name, str) or provider_name not in SUPPORTED_NATIVE_PROVIDERS:
            return None
        if not isinstance(model_id, str) or not model_id.strip():
            return None
        return NativeModelSelection(provider_name=provider_name, model_id=model_id.strip())

    def save(self, selection: NativeModelSelection) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        payload = {
            "schema": "pipy.native-defaults",
            "schema_version": 1,
            "provider": selection.provider_name,
            "model_id": selection.model_id,
        }
        temporary_path = self.path.with_name(f"{self.path.name}.partial")
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        temporary_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        temporary_path.replace(self.path)
        self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)


@dataclass(slots=True)
class NativeReplProviderState:
    """Late-bound provider state for local REPL auth/model commands."""

    selection: NativeModelSelection
    provider_factory: NativeProviderFactory
    defaults_store: NativeDefaultsStore | None = None
    auth_manager_factory: Callable[[], OpenAICodexAuthManager] = OpenAICodexAuthManager
    env: Mapping[str, str] | None = None
    openai_codex_auth_path: Path | None = None
    persist_defaults: bool = True
    # When set, model_options() and select_model() read the full pipy catalog
    # (built-in + models.json) with the shared matcher and availability gate,
    # mirroring Pi's /model selector over getAvailable(). When None, the legacy
    # one-default-per-provider registry path is used (backward compatible).
    catalog_state: "object | None" = None
    thinking_level: str | None = None

    def current_selection(self) -> NativeModelSelection:
        return self.selection

    def current_provider(self) -> ProviderPort:
        return self.provider_for(self.selection)

    def provider_for(self, selection: NativeModelSelection) -> ProviderPort:
        """Construct the provider for any selection (catalog-first).

        Used by ``current_provider`` and by the ``/model`` selector's
        tool-capability probe so a ``models.json`` custom provider/model is
        constructed the same way it will be used (not via the legacy factory).
        """

        if self.catalog_state is not None:
            catalog_provider = self._catalog_provider(selection)
            if catalog_provider is not None:
                return catalog_provider
        return self.provider_factory(selection)

    def _catalog_provider(self, selection: NativeModelSelection) -> ProviderPort | None:
        """Construct a provider from the catalog (spec item 18).

        Returns ``None`` when the selection is not a catalog row or its API
        family is not catalog-wired, so the caller falls back to the legacy
        factory (preserving built-in providers like openai-codex/fake). A
        catalog-wired family whose auth fails returns a fail-closed provider
        (no silent legacy fallback).
        """

        from pipy_harness.native.model_resolver import build_fallback_model
        from pipy_harness.native.provider_construction import (
            build_provider,
            resolve_construction,
        )

        state = self.catalog_state
        spec = state.find(selection.provider_name, selection.model_id)  # type: ignore[attr-defined]
        if spec is None:
            # A synthesized fallback selection (e.g. a not-yet-cataloged model on
            # a known provider) must still construct from the provider's catalog
            # base (baseUrl/headers/auth), not fall back to the legacy factory.
            spec = build_fallback_model(
                selection.provider_name, selection.model_id, state.get_all()  # type: ignore[attr-defined]
            )
        if spec is None:
            return None
        resolved = resolve_construction(
            spec,
            store=state.auth_store,  # type: ignore[attr-defined]
            env=state._env(),  # type: ignore[attr-defined]
            runtime_api_key=state.runtime_api_key,  # type: ignore[attr-defined]
            models_json_auth=state._models_json_auth(spec.provider_name),  # type: ignore[attr-defined]
            thinking_level=self.thinking_level,
        )
        return build_provider(resolved)

    def provider_available(self, provider_name: str) -> bool:
        if self.catalog_state is not None:
            return self.catalog_state.provider_available(provider_name)  # type: ignore[attr-defined]
        return self._provider_available(provider_name)

    def model_options(self) -> list[NativeModelOption]:
        if self.catalog_state is not None:
            return self._catalog_model_options()
        options: list[NativeModelOption] = []
        for provider_name, spec in NATIVE_PROVIDER_REGISTRY.items():
            available = self._provider_available(provider_name)
            options.append(
                NativeModelOption(
                    NativeModelSelection(provider_name, spec.default_model),
                    available=available,
                    reason=None if available else _availability_reason(spec.availability),
                )
            )
        return options

    def _catalog_model_options(self) -> list[NativeModelOption]:
        state = self.catalog_state
        options: list[NativeModelOption] = []
        for row in state.get_all():  # type: ignore[attr-defined]
            available = state.provider_available(row.provider_name)  # type: ignore[attr-defined]
            reason = (
                None
                if available
                else state.availability_reason(row.provider_name)  # type: ignore[attr-defined]
            )
            options.append(
                NativeModelOption(
                    NativeModelSelection(row.provider_name, row.model_id),
                    available=available,
                    reason=reason,
                    context_window=row.context_window,
                    max_tokens=row.max_tokens,
                    reasoning=row.reasoning,
                    image_input="image" in row.input,
                )
            )
        return options

    def select_model(self, reference: str) -> tuple[bool, str]:
        parsed = reference.strip()
        if not parsed:
            return False, "pipy: malformed /model command. Provide <provider>/<model> or <model>."

        if self.catalog_state is not None:
            return self._catalog_select_model(parsed)

        selection, reason = self._resolve_model_reference(parsed)
        if selection is None:
            return False, reason

        self.selection = selection
        self._save_default(selection)
        return True, f"pipy: selected model {selection.reference}."

    def _catalog_select_model(self, reference: str) -> tuple[bool, str]:
        """Resolve direct ``/model <ref>`` through the shared catalog resolver.

        Uses :func:`resolve_cli_model` so exact ``provider/id``, bare id, fuzzy
        alias, ``provider/id:level``, colon-in-id models, and the strict invalid-
        suffix / per-provider fallback behaviour all match Pi's model-resolver.
        Selection is then gated by availability (an unavailable target is refused
        with the prior selection intact).
        """

        from pipy_harness.native.model_resolver import resolve_cli_model

        state = self.catalog_state
        result = resolve_cli_model(
            cli_provider=None, cli_model=reference, rows=state.get_all()  # type: ignore[attr-defined]
        )
        if result.error is not None:
            return False, f"pipy: {sanitize_text(result.error)}"
        model = result.model
        if model is None:
            return False, "pipy: unsupported or unknown model reference."

        if not state.provider_available(model.provider_name):  # type: ignore[attr-defined]
            reason = state.availability_reason(model.provider_name)  # type: ignore[attr-defined]
            return False, (
                f"pipy: {model.provider_name} is unavailable ({reason or 'unknown'}); "
                "selection unchanged."
            )

        selection = NativeModelSelection(model.provider_name, model.model_id)
        self.selection = selection
        if result.thinking_level is not None:
            self.thinking_level = result.thinking_level
        self._save_default(selection)

        notes: list[str] = []
        if result.thinking_level is not None:
            notes.append(f"thinking: {result.thinking_level}")
        if result.warning:
            notes.append(sanitize_text(result.warning))
        suffix = f" ({'; '.join(notes)})" if notes else ""
        return True, f"pipy: selected model {selection.reference}{suffix}."

    def login(self, provider_name: str, *, input_stream: TextIO, output_stream: TextIO) -> tuple[bool, str]:
        provider = provider_name.strip() or "openai-codex"
        if provider != "openai-codex":
            return False, "pipy: unsupported login provider. Only openai-codex OAuth is supported."
        self.auth_manager_factory().login_interactive(
            input_stream=input_stream,
            output_stream=output_stream,
            open_browser=True,
        )
        return True, "pipy: openai-codex OAuth login stored."

    def logout(self, provider_name: str) -> tuple[bool, str]:
        provider = provider_name.strip() or "openai-codex"
        if provider != "openai-codex":
            return False, "pipy: unsupported logout provider. Only openai-codex OAuth is supported."
        removed = self.auth_manager_factory().logout()
        if self.selection.provider_name == "openai-codex":
            self.selection = NativeModelSelection("fake", DEFAULT_NATIVE_MODELS["fake"])
            self._save_default(self.selection)
        if removed:
            return True, "pipy: openai-codex OAuth credentials removed."
        return True, "pipy: no openai-codex OAuth credentials were stored."

    def _resolve_model_reference(self, reference: str) -> tuple[NativeModelSelection | None, str]:
        if "/" in reference:
            provider_name, model_id = reference.split("/", 1)
            provider_name = provider_name.strip()
            model_id = model_id.strip()
            if provider_name not in SUPPORTED_NATIVE_PROVIDERS or not model_id:
                return None, "pipy: unsupported model reference."
            # Availability is checked through the public gate (catalog-aware when
            # a catalog_state is set, registry-based otherwise) so it agrees with
            # model_options(); the diagnostic keeps the provider-named message.
            if not self.provider_available(provider_name):
                return None, self._provider_unavailable_message(provider_name)
            return NativeModelSelection(provider_name, model_id), ""

        matches = [
            option.selection
            for option in self.model_options()
            if option.available and option.selection.model_id.lower() == reference.lower()
        ]
        if len(matches) == 1:
            return matches[0], ""
        if len(matches) > 1:
            return None, "pipy: ambiguous model reference. Use <provider>/<model>."
        return None, "pipy: unsupported or unavailable model reference."

    def _provider_available(self, provider_name: str) -> bool:
        return native_provider_available(
            provider_name,
            env=self._env(),
            openai_codex_credentials_exist=self._openai_codex_credentials_exist(),
        )

    def _provider_unavailable_message(self, provider_name: str) -> str:
        return native_provider_unavailable_message(provider_name)

    def _save_default(self, selection: NativeModelSelection) -> None:
        if not self.persist_defaults or self.defaults_store is None:
            return
        try:
            self.defaults_store.save(selection)
        except OSError:
            pass

    def _env(self) -> Mapping[str, str]:
        return self.env if self.env is not None else os.environ

    def _openai_codex_credentials_exist(self) -> bool:
        path = self.openai_codex_auth_path or default_openai_codex_auth_path()
        return path.exists()


@dataclass(slots=True)
class StaticNativeReplProviderState:
    """Compatibility state for tests and callers that inject one provider."""

    provider: ProviderPort

    def current_selection(self) -> NativeModelSelection:
        return NativeModelSelection(self.provider.name, self.provider.model_id)

    def current_provider(self) -> ProviderPort:
        return self.provider

    def model_options(self) -> list[NativeModelOption]:
        return [
            NativeModelOption(
                NativeModelSelection(self.provider.name, self.provider.model_id),
                available=True,
            )
        ]

    def select_model(self, reference: str) -> tuple[bool, str]:
        return False, "pipy: /model is unavailable for this REPL provider state."

    def login(self, provider_name: str, *, input_stream: TextIO, output_stream: TextIO) -> tuple[bool, str]:
        return False, "pipy: /login is unavailable for this REPL provider state."

    def logout(self, provider_name: str) -> tuple[bool, str]:
        return False, "pipy: /logout is unavailable for this REPL provider state."


def settings_overlay_lines(
    provider_state: "NativeReplProviderState | StaticNativeReplProviderState",
    settings_manager: "object | None" = None,
) -> list[str]:
    """Build the read-only settings/status display lines.

    Shared by the no-tool ``/settings`` command and the product-TUI
    ``/settings`` overlay so both surface the same safe selection, the
    registered defaults, and the local availability (with reasons) of each
    supported provider. It is strictly read-only: it neither switches
    models/providers, starts login/logout, mutates auth state, invokes
    tools, nor creates a provider turn. Availability is derived from local
    environment and credential-file probes only.

    The builder deliberately emits no command-availability footer. Each
    caller appends a footer honest for its own command surface (both the
    no-tool REPL and the product tool-loop TUI can run
    ``/model``/``/login``/``/logout``; a static single-provider state can run
    none of them), so no surface advertises a command it cannot execute.
    """

    current = provider_state.current_selection()
    lines = [
        "pipy native REPL settings:",
        f"  active: {sanitize_text(current.provider_name)}/{sanitize_text(current.model_id)}",
        "  registered providers:",
    ]
    for option in provider_state.model_options():
        availability = (
            "available"
            if option.available
            else f"unavailable ({option.reason or 'unknown'})"
        )
        lines.append(
            "    "
            f"{sanitize_text(option.selection.provider_name)}/"
            f"{sanitize_text(option.selection.model_id)} "
            f"[{availability}]"
        )
    if settings_manager is not None:
        from pipy_harness.native.settings import (
            SettingsManager,
            settings_report_lines,
        )

        if isinstance(settings_manager, SettingsManager):
            lines.extend(settings_report_lines(settings_manager))
    return lines


def default_native_defaults_path() -> Path:
    configured_path = os.environ.get("PIPY_NATIVE_DEFAULTS_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    return Path.home() / ".local" / "state" / "pipy" / "native-defaults.json"


AUTO_DEFAULT_PROVIDER_PRIORITY: tuple[str, ...] = (
    "openai-codex",
    "openai",
    "anthropic",
    "google",
    "openrouter",
    "mistral",
    "amazon-bedrock",
    "azure-openai",
    "cloudflare",
    "google-vertex",
    "openai-completions",
)
"""Order in which the REPL chooses a real provider for the default session.

The default invocation `pipy` should not show `fake/fake-native-bootstrap`
to a user who has a real provider configured. This priority list scans the
ambient environment (OAuth credential files and conventional API-key env
vars) and selects the first match. The list intentionally mirrors common
Pi defaults — `openai-codex` first because Pi uses it when the user has
logged in, then keyed providers in rough order of how widely deployed they
are. `fake` remains the last-resort fallback.
"""


def auto_default_selection(
    *,
    env: Mapping[str, str] | None = None,
    openai_codex_auth_path: Path | None = None,
) -> NativeModelSelection | None:
    """Probe the ambient environment for an available real provider.

    Returns `None` when no real provider is available; callers fall back to
    the deterministic fake provider in that case.
    """

    probe_env = env if env is not None else os.environ
    codex_path = openai_codex_auth_path or default_openai_codex_auth_path()
    for provider_name in AUTO_DEFAULT_PROVIDER_PRIORITY:
        if _provider_available_in_env(
            provider_name, env=probe_env, openai_codex_auth_path=codex_path
        ):
            return NativeModelSelection(
                provider_name=provider_name,
                model_id=DEFAULT_NATIVE_MODELS[provider_name],
            )
    return None


def _provider_available_in_env(
    provider_name: str,
    *,
    env: Mapping[str, str],
    openai_codex_auth_path: Path,
) -> bool:
    return native_provider_available(
        provider_name,
        env=env,
        openai_codex_credentials_exist=openai_codex_auth_path.exists(),
        for_auto_default=True,
    )


def default_selection_for(
    *,
    native_provider: str | None,
    native_model: str | None,
    defaults_store: NativeDefaultsStore | None = None,
    env: Mapping[str, str] | None = None,
    openai_codex_auth_path: Path | None = None,
) -> NativeModelSelection:
    if native_provider is not None:
        if native_provider not in SUPPORTED_NATIVE_PROVIDERS:
            raise ValueError(f"unsupported native provider: {native_provider}")
        return NativeModelSelection(
            provider_name=native_provider,
            model_id=native_model or DEFAULT_NATIVE_MODELS[native_provider],
        )
    if native_model is not None:
        return NativeModelSelection(provider_name="fake", model_id=native_model)
    if defaults_store is not None:
        loaded = defaults_store.load()
        if loaded is not None and loaded.provider_name != "fake":
            return loaded
    auto = auto_default_selection(
        env=env, openai_codex_auth_path=openai_codex_auth_path
    )
    if auto is not None:
        return auto
    return NativeModelSelection("fake", DEFAULT_NATIVE_MODELS["fake"])


def _availability_reason(availability: str) -> str:
    if availability == "openai-codex-login":
        return "login-required"
    if availability.startswith("env"):
        return "env-missing"
    return "unavailable"
