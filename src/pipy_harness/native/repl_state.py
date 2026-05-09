"""Native REPL provider/model selection state."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO

from pipy_harness.native.openai_codex_provider import (
    OpenAICodexAuthManager,
    default_openai_codex_auth_path,
)
from pipy_harness.native.provider import ProviderPort

SUPPORTED_NATIVE_PROVIDERS = frozenset({"fake", "openai", "openai-codex", "openrouter"})
DEFAULT_NATIVE_MODELS = {
    "fake": "fake-native-bootstrap",
    "openai": "gpt-5.4",
    "openai-codex": "gpt-5.4",
    "openrouter": "openai/gpt-5.1-codex",
}


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
    """A conservative model reference exposed by the line-oriented REPL."""

    selection: NativeModelSelection
    available: bool
    reason: str | None = None


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

    def current_selection(self) -> NativeModelSelection:
        return self.selection

    def current_provider(self) -> ProviderPort:
        return self.provider_factory(self.selection)

    def provider_available(self, provider_name: str) -> bool:
        return self._provider_available(provider_name)

    def model_options(self) -> list[NativeModelOption]:
        return [
            NativeModelOption(
                NativeModelSelection("fake", DEFAULT_NATIVE_MODELS["fake"]),
                available=True,
            ),
            NativeModelOption(
                NativeModelSelection("openai-codex", DEFAULT_NATIVE_MODELS["openai-codex"]),
                available=self._openai_codex_credentials_exist(),
                reason=None if self._openai_codex_credentials_exist() else "login-required",
            ),
            NativeModelOption(
                NativeModelSelection("openai", DEFAULT_NATIVE_MODELS["openai"]),
                available=bool(self._env().get("OPENAI_API_KEY")),
                reason=None if self._env().get("OPENAI_API_KEY") else "env-missing",
            ),
            NativeModelOption(
                NativeModelSelection("openrouter", DEFAULT_NATIVE_MODELS["openrouter"]),
                available=bool(self._env().get("OPENROUTER_API_KEY")),
                reason=None if self._env().get("OPENROUTER_API_KEY") else "env-missing",
            ),
        ]

    def select_model(self, reference: str) -> tuple[bool, str]:
        parsed = reference.strip()
        if not parsed:
            return False, "pipy: malformed /model command. Provide <provider>/<model> or <model>."

        selection, reason = self._resolve_model_reference(parsed)
        if selection is None:
            return False, reason

        self.selection = selection
        self._save_default(selection)
        return True, f"pipy: selected model {selection.reference}."

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
            if not self._provider_available(provider_name):
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
        if provider_name == "fake":
            return True
        if provider_name == "openai-codex":
            return self._openai_codex_credentials_exist()
        if provider_name == "openai":
            return bool(self._env().get("OPENAI_API_KEY"))
        if provider_name == "openrouter":
            return bool(self._env().get("OPENROUTER_API_KEY"))
        return False

    def _provider_unavailable_message(self, provider_name: str) -> str:
        if provider_name == "openai-codex":
            return "pipy: openai-codex is not logged in. Run /login openai-codex first."
        if provider_name == "openai":
            return "pipy: openai is unavailable because OPENAI_API_KEY is not set."
        if provider_name == "openrouter":
            return "pipy: openrouter is unavailable because OPENROUTER_API_KEY is not set."
        return "pipy: unsupported native provider."

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


def default_native_defaults_path() -> Path:
    configured_path = os.environ.get("PIPY_NATIVE_DEFAULTS_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    return Path.home() / ".local" / "state" / "pipy" / "native-defaults.json"


def default_selection_for(
    *,
    native_provider: str | None,
    native_model: str | None,
    defaults_store: NativeDefaultsStore | None = None,
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
        if loaded is not None:
            return loaded
    return NativeModelSelection("fake", DEFAULT_NATIVE_MODELS["fake"])
