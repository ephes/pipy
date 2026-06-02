"""Pipy auth store + per-request auth/header resolution (M6).

Pipy analogue of Pi's ``AuthStorage`` (auth-storage.ts) +
``ModelRegistry.getApiKeyAndHeaders`` (model-registry.ts) +
``resolveConfigValue`` (resolve-config-value.ts) + ``env-api-keys.ts``.

Resolution priority for a per-request API key (Pi's order):

1. runtime ``--api-key`` override
2. stored ``api_key`` in ``auth.json`` (resolved as literal/env-name/``!command``)
3. stored OAuth token (refresh-on-expiry is the OAuth layer's job, M7)
4. provider env var(s)
5. the provider's ``models.json`` ``apiKey`` (literal/env-name/``!command``),
   resolved by the catalog layer *after* the auth-store path

Secrets, ``!command`` values, refresh tokens, and ``Authorization`` headers are
never archived. Status checks never execute ``!command`` values and never
refresh tokens. Stdlib only (``json``/``subprocess``/``os``/``stat``).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path


CommandRunner = Callable[[str], str | None]
OAuthTokenResolver = Callable[[str, Mapping[str, object]], str | None]


# --------------------------------------------------------------------------- #
# resolve_config_value (literal / env-name / !command)
# --------------------------------------------------------------------------- #


def _default_run_command(command: str) -> str | None:
    try:
        result = subprocess.run(  # noqa: S602 - explicit user-configured command
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = (result.stdout or "").strip()
    return value or None


def resolve_config_value(
    value: str,
    *,
    env: Mapping[str, str],
    run_command: CommandRunner | None = None,
) -> str | None:
    """Resolve a config value: ``!command`` -> stdout, env-name -> env, else literal.

    Mirrors Pi's ``resolveConfigValue``: ``process.env[config] || config``.
    """

    if value.startswith("!"):
        runner = run_command or _default_run_command
        result = runner(value[1:])
        if result is None:
            return None
        stripped = result.strip()
        return stripped or None
    return env.get(value) or value


# --------------------------------------------------------------------------- #
# Provider env-credential detection (env-api-keys.ts analogue, pipy names)
# --------------------------------------------------------------------------- #


_API_KEY_ENV_VARS: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "openai-completions": ("OPENAI_API_KEY",),
    "openai-codex": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "azure-openai": ("AZURE_OPENAI_API_KEY",),
    "cloudflare": ("CLOUDFLARE_API_KEY",),
    "github-copilot": ("COPILOT_GITHUB_TOKEN",),
    "google-vertex": ("GOOGLE_CLOUD_API_KEY",),
}

_AMBIENT_AUTHENTICATED = "<authenticated>"


def find_env_keys(provider: str, env: Mapping[str, str]) -> list[str] | None:
    """Names of configured API-key env vars for a provider (excludes ambient)."""

    names = _API_KEY_ENV_VARS.get(provider)
    if not names:
        return None
    found = [name for name in names if env.get(name)]
    return found or None


def _bedrock_ambient(env: Mapping[str, str]) -> bool:
    return bool(
        env.get("AWS_PROFILE")
        or (env.get("AWS_ACCESS_KEY_ID") and env.get("AWS_SECRET_ACCESS_KEY"))
        or env.get("AWS_BEARER_TOKEN_BEDROCK")
        or env.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
        or env.get("AWS_CONTAINER_CREDENTIALS_FULL_URI")
        or env.get("AWS_WEB_IDENTITY_TOKEN_FILE")
    )


def _vertex_adc(env: Mapping[str, str]) -> bool:
    gac = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    has_creds = bool(gac and Path(gac).expanduser().exists())
    has_project = bool(env.get("GOOGLE_CLOUD_PROJECT") or env.get("GCLOUD_PROJECT"))
    has_location = bool(env.get("GOOGLE_CLOUD_LOCATION"))
    return has_creds and has_project and has_location


def env_api_key(provider: str, env: Mapping[str, str]) -> str | None:
    """API key (or ``<authenticated>`` for ambient creds) from env, or ``None``.

    Mirrors ``getEnvApiKey`` plus the provider-specific ambient sources for
    Amazon Bedrock and Google Vertex ADC.
    """

    keys = find_env_keys(provider, env)
    if keys:
        return env.get(keys[0])

    if provider == "google-vertex" and _vertex_adc(env):
        return _AMBIENT_AUTHENTICATED
    if provider == "amazon-bedrock" and _bedrock_ambient(env):
        return _AMBIENT_AUTHENTICATED
    return None


# --------------------------------------------------------------------------- #
# models.json provider request config (auth-relevant subset)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ProviderAuthRequestConfig:
    api_key: str | None = None
    headers: Mapping[str, str] | None = None
    auth_header: bool = False


# --------------------------------------------------------------------------- #
# Auth store
# --------------------------------------------------------------------------- #


def default_auth_store_path() -> Path:
    configured = os.environ.get("PIPY_AUTH_DIR")
    base = (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".local" / "state" / "pipy" / "auth"
    )
    return base / "auth.json"


class AuthStore:
    """Owner-only JSON credential store keyed by provider name."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_auth_store_path()
        self._data: dict[str, dict[str, object]] = {}
        self._load()

    def _load(self) -> None:
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            self._data = {}
            return
        self._data = body if isinstance(body, dict) else {}

    def reload(self) -> None:
        self._load()

    def get(self, provider: str) -> dict[str, object] | None:
        entry = self._data.get(provider)
        return dict(entry) if isinstance(entry, dict) else None

    def set(self, provider: str, entry: Mapping[str, object]) -> None:
        self._data[provider] = dict(entry)
        self._persist()

    def remove(self, provider: str) -> bool:
        if provider in self._data:
            del self._data[provider]
            self._persist()
            return True
        return False

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        temporary = self.path.with_name(f"{self.path.name}.partial")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(self._data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
        temporary.replace(self.path)
        self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)


# --------------------------------------------------------------------------- #
# Availability + status
# --------------------------------------------------------------------------- #


def provider_available(
    provider: str,
    *,
    store: AuthStore,
    env: Mapping[str, str],
    models_json_config: ProviderAuthRequestConfig | None = None,
) -> bool:
    """Pi's ``hasAuth`` analogue, extended to consult models.json keys."""

    if store.get(provider) is not None:
        return True
    if env_api_key(provider, env):
        return True
    if models_json_config is not None and (
        models_json_config.api_key or models_json_config.headers
        or models_json_config.auth_header
    ):
        return True
    return False


@dataclass(frozen=True, slots=True)
class AuthStatus:
    configured: bool
    source: str | None = None
    label: str | None = None


def provider_auth_status(
    provider: str,
    *,
    store: AuthStore,
    env: Mapping[str, str],
    models_json_config: ProviderAuthRequestConfig | None = None,
    runtime_api_key: str | None = None,
    run_command: CommandRunner | None = None,  # accepted but never invoked here
) -> AuthStatus:
    """Auth status with source labels. Never executes ``!command`` or refreshes.

    Mirrors Pi's ``getAuthStatus`` (stored/runtime/environment) extended with
    the model-registry ``models_json_key``/``models_json_command`` labels.
    """

    del run_command  # status must not execute commands
    if store.get(provider) is not None:
        return AuthStatus(configured=True, source="stored")
    if runtime_api_key:
        return AuthStatus(configured=False, source="runtime", label="--api-key")
    keys = find_env_keys(provider, env)
    if keys:
        return AuthStatus(configured=False, source="environment", label=keys[0])
    if env_api_key(provider, env):  # ambient (bedrock/vertex)
        return AuthStatus(configured=False, source="environment", label="ambient")
    if models_json_config is not None and models_json_config.api_key:
        if models_json_config.api_key.startswith("!"):
            return AuthStatus(configured=False, source="models_json_command")
        return AuthStatus(configured=False, source="models_json_key")
    if models_json_config is not None and (
        models_json_config.headers or models_json_config.auth_header
    ):
        return AuthStatus(configured=False, source="fallback", label="custom provider config")
    return AuthStatus(configured=False)


# --------------------------------------------------------------------------- #
# Per-request auth resolution
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ResolvedRequestAuth:
    ok: bool
    api_key: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None


def _stored_api_key(
    store: AuthStore,
    provider: str,
    env: Mapping[str, str],
    run_command: CommandRunner | None,
    oauth_token_resolver: OAuthTokenResolver | None,
) -> str | None:
    cred = store.get(provider)
    if cred is None:
        return None
    cred_type = cred.get("type")
    if cred_type == "api_key":
        key = cred.get("key")
        if isinstance(key, str):
            return resolve_config_value(key, env=env, run_command=run_command)
        return None
    if cred_type == "oauth":
        if oauth_token_resolver is not None:
            return oauth_token_resolver(provider, cred)
        access = cred.get("access")
        return access if isinstance(access, str) else None
    return None


def resolve_request_auth(
    provider: str,
    *,
    store: AuthStore,
    env: Mapping[str, str],
    runtime_api_key: str | None = None,
    models_json_config: ProviderAuthRequestConfig | None = None,
    model_headers: Mapping[str, str] | None = None,
    env_for_headers: Mapping[str, str] | None = None,
    run_command: CommandRunner | None = None,
    oauth_token_resolver: OAuthTokenResolver | None = None,
) -> ResolvedRequestAuth:
    """Resolve the API key + headers for a request using Pi's priority order."""

    header_env = env_for_headers if env_for_headers is not None else env

    api_key: str | None = None
    if runtime_api_key:
        api_key = runtime_api_key
    if api_key is None:
        api_key = _stored_api_key(
            store, provider, env, run_command, oauth_token_resolver
        )
    if api_key is None:
        api_key = env_api_key(provider, env)
    if api_key is None and models_json_config is not None and models_json_config.api_key:
        api_key = resolve_config_value(
            models_json_config.api_key, env=env, run_command=run_command
        )

    headers: dict[str, str] = {}
    if models_json_config is not None and models_json_config.headers:
        for name, raw in models_json_config.headers.items():
            resolved = resolve_config_value(raw, env=header_env, run_command=run_command)
            if resolved is not None:
                headers[name] = resolved
    if model_headers:
        for name, raw in model_headers.items():
            resolved = resolve_config_value(raw, env=header_env, run_command=run_command)
            if resolved is not None:
                headers[name] = resolved

    if models_json_config is not None and models_json_config.auth_header and api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    return ResolvedRequestAuth(ok=True, api_key=api_key, headers=headers)
