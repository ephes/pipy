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
from pipy_harness.native.provider import ProviderPort

SUPPORTED_NATIVE_PROVIDERS = frozenset(
    {
        "fake",
        "openai",
        "openai-completions",
        "openai-codex",
        "openrouter",
        "anthropic",
        "google",
        "google-vertex",
        "mistral",
        "amazon-bedrock",
        "azure-openai",
        "cloudflare",
    }
)
DEFAULT_NATIVE_MODELS = {
    "fake": "fake-native-bootstrap",
    "openai": "gpt-5.5",
    "openai-completions": "gpt-4o-mini",
    "openai-codex": "gpt-5.5",
    "openrouter": "openai/gpt-5.1-codex",
    "anthropic": "claude-3-5-sonnet-20241022",
    "google": "gemini-2.0-flash-exp",
    "google-vertex": "gemini-2.0-flash-001",
    "mistral": "mistral-large-latest",
    "amazon-bedrock": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "azure-openai": "gpt-4o",
    "cloudflare": "@cf/meta/llama-3.1-8b-instruct",
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
            NativeModelOption(
                NativeModelSelection("anthropic", DEFAULT_NATIVE_MODELS["anthropic"]),
                available=bool(self._env().get("ANTHROPIC_API_KEY")),
                reason=None if self._env().get("ANTHROPIC_API_KEY") else "env-missing",
            ),
            NativeModelOption(
                NativeModelSelection("google", DEFAULT_NATIVE_MODELS["google"]),
                available=bool(
                    self._env().get("GOOGLE_API_KEY")
                    or self._env().get("GEMINI_API_KEY")
                ),
                reason=None
                if self._env().get("GOOGLE_API_KEY")
                or self._env().get("GEMINI_API_KEY")
                else "env-missing",
            ),
            NativeModelOption(
                NativeModelSelection("mistral", DEFAULT_NATIVE_MODELS["mistral"]),
                available=bool(self._env().get("MISTRAL_API_KEY")),
                reason=None if self._env().get("MISTRAL_API_KEY") else "env-missing",
            ),
            NativeModelOption(
                NativeModelSelection(
                    "amazon-bedrock", DEFAULT_NATIVE_MODELS["amazon-bedrock"]
                ),
                available=bool(
                    self._env().get("AWS_ACCESS_KEY_ID")
                    and self._env().get("AWS_SECRET_ACCESS_KEY")
                ),
                reason=None
                if self._env().get("AWS_ACCESS_KEY_ID")
                and self._env().get("AWS_SECRET_ACCESS_KEY")
                else "env-missing",
            ),
            NativeModelOption(
                NativeModelSelection(
                    "openai-completions",
                    DEFAULT_NATIVE_MODELS["openai-completions"],
                ),
                available=bool(self._env().get("OPENAI_API_KEY")),
                reason=None if self._env().get("OPENAI_API_KEY") else "env-missing",
            ),
            NativeModelOption(
                NativeModelSelection(
                    "azure-openai", DEFAULT_NATIVE_MODELS["azure-openai"]
                ),
                available=bool(
                    self._env().get("AZURE_OPENAI_ENDPOINT")
                    and self._env().get("AZURE_OPENAI_API_KEY")
                ),
                reason=None
                if self._env().get("AZURE_OPENAI_ENDPOINT")
                and self._env().get("AZURE_OPENAI_API_KEY")
                else "env-missing",
            ),
            NativeModelOption(
                NativeModelSelection(
                    "cloudflare", DEFAULT_NATIVE_MODELS["cloudflare"]
                ),
                available=bool(
                    self._env().get("CLOUDFLARE_ACCOUNT_ID")
                    and self._env().get("CLOUDFLARE_API_TOKEN")
                ),
                reason=None
                if self._env().get("CLOUDFLARE_ACCOUNT_ID")
                and self._env().get("CLOUDFLARE_API_TOKEN")
                else "env-missing",
            ),
            NativeModelOption(
                NativeModelSelection(
                    "google-vertex", DEFAULT_NATIVE_MODELS["google-vertex"]
                ),
                available=bool(
                    self._env().get("GOOGLE_ACCESS_TOKEN")
                    and (
                        self._env().get("GOOGLE_CLOUD_PROJECT")
                        or self._env().get("GOOGLE_PROJECT_ID")
                    )
                ),
                reason=None
                if self._env().get("GOOGLE_ACCESS_TOKEN")
                and (
                    self._env().get("GOOGLE_CLOUD_PROJECT")
                    or self._env().get("GOOGLE_PROJECT_ID")
                )
                else "env-missing",
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
        if provider_name == "anthropic":
            return bool(self._env().get("ANTHROPIC_API_KEY"))
        if provider_name == "google":
            return bool(
                self._env().get("GOOGLE_API_KEY")
                or self._env().get("GEMINI_API_KEY")
            )
        if provider_name == "mistral":
            return bool(self._env().get("MISTRAL_API_KEY"))
        if provider_name == "openai-completions":
            return bool(self._env().get("OPENAI_API_KEY"))
        if provider_name == "amazon-bedrock":
            return bool(
                self._env().get("AWS_ACCESS_KEY_ID")
                and self._env().get("AWS_SECRET_ACCESS_KEY")
            )
        if provider_name == "azure-openai":
            return bool(
                self._env().get("AZURE_OPENAI_ENDPOINT")
                and self._env().get("AZURE_OPENAI_API_KEY")
            )
        if provider_name == "cloudflare":
            return bool(
                self._env().get("CLOUDFLARE_ACCOUNT_ID")
                and self._env().get("CLOUDFLARE_API_TOKEN")
            )
        if provider_name == "google-vertex":
            return bool(
                self._env().get("GOOGLE_ACCESS_TOKEN")
                and (
                    self._env().get("GOOGLE_CLOUD_PROJECT")
                    or self._env().get("GOOGLE_PROJECT_ID")
                )
            )
        return False

    def _provider_unavailable_message(self, provider_name: str) -> str:
        if provider_name == "openai-codex":
            return "pipy: openai-codex is not logged in. Run /login openai-codex first."
        if provider_name == "openai":
            return "pipy: openai is unavailable because OPENAI_API_KEY is not set."
        if provider_name == "openrouter":
            return "pipy: openrouter is unavailable because OPENROUTER_API_KEY is not set."
        if provider_name == "anthropic":
            return "pipy: anthropic is unavailable because ANTHROPIC_API_KEY is not set."
        if provider_name == "google":
            return "pipy: google is unavailable because GOOGLE_API_KEY or GEMINI_API_KEY is not set."
        if provider_name == "mistral":
            return "pipy: mistral is unavailable because MISTRAL_API_KEY is not set."
        if provider_name == "openai-completions":
            return "pipy: openai-completions is unavailable because OPENAI_API_KEY is not set."
        if provider_name == "amazon-bedrock":
            return "pipy: amazon-bedrock is unavailable because AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY is not set."
        if provider_name == "azure-openai":
            return "pipy: azure-openai is unavailable because AZURE_OPENAI_ENDPOINT or AZURE_OPENAI_API_KEY is not set."
        if provider_name == "cloudflare":
            return "pipy: cloudflare is unavailable because CLOUDFLARE_ACCOUNT_ID or CLOUDFLARE_API_TOKEN is not set."
        if provider_name == "google-vertex":
            return "pipy: google-vertex is unavailable because GOOGLE_ACCESS_TOKEN or GOOGLE_CLOUD_PROJECT is not set."
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


def settings_overlay_lines(
    provider_state: "NativeReplProviderState | StaticNativeReplProviderState",
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
    caller appends a footer honest for its own command surface (the no-tool
    REPL can run ``/model``/``/login``/``/logout``; the tool-loop TUI cannot
    yet), so neither surface advertises a command it cannot execute.
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
        if _provider_available_in_env(provider_name, env=probe_env, openai_codex_auth_path=codex_path):
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
    if provider_name == "openai-codex":
        return openai_codex_auth_path.exists()
    if provider_name == "openai":
        return bool(env.get("OPENAI_API_KEY"))
    if provider_name == "openai-completions":
        return bool(env.get("OPENAI_API_KEY"))
    if provider_name == "anthropic":
        return bool(env.get("ANTHROPIC_API_KEY"))
    if provider_name == "google":
        return bool(env.get("GOOGLE_API_KEY") or env.get("GEMINI_API_KEY"))
    if provider_name == "openrouter":
        return bool(env.get("OPENROUTER_API_KEY"))
    if provider_name == "mistral":
        return bool(env.get("MISTRAL_API_KEY"))
    if provider_name == "amazon-bedrock":
        return bool(env.get("AWS_ACCESS_KEY_ID") and env.get("AWS_SECRET_ACCESS_KEY"))
    if provider_name == "azure-openai":
        return bool(env.get("AZURE_OPENAI_ENDPOINT") and env.get("AZURE_OPENAI_API_KEY"))
    if provider_name == "cloudflare":
        return bool(env.get("CLOUDFLARE_ACCOUNT_ID") and env.get("CLOUDFLARE_API_TOKEN"))
    if provider_name == "google-vertex":
        return bool(
            env.get("GOOGLE_ACCESS_TOKEN")
            and (env.get("GOOGLE_CLOUD_PROJECT") or env.get("GOOGLE_PROJECT_ID"))
        )
    return False


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
