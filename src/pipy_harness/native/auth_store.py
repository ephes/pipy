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
from types import MappingProxyType


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


# API-key env vars per provider (env-api-keys.ts parity, pipy provider names).
# Providers that require OAuth tokens (openai-codex) have no env entry: env
# lookup "will not return API keys for providers that require OAuth tokens".
_API_KEY_ENV_VARS: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "openai-completions": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
    "google": ("GEMINI_API_KEY",),
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
    if gac:
        has_creds = Path(gac).expanduser().exists()
    else:
        # Fall back to the default ADC path (env-api-keys.ts).
        has_creds = (
            Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
        ).exists()
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


class _FrozenAuthList(tuple[object, ...]):
    def __eq__(self, other: object) -> bool:
        return type(other) is _FrozenAuthList and tuple.__eq__(self, other)

    __ne__ = object.__ne__
    __hash__ = tuple.__hash__


def _freeze_auth_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_auth_value(item) for key, item in value.items()}
        )
    if isinstance(value, _FrozenAuthList):
        return value
    if isinstance(value, list):
        return _FrozenAuthList(_freeze_auth_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_auth_value(item) for item in value)
    return value


def _detach_auth_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _detach_auth_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_detach_auth_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_detach_auth_value(item) for item in value)
    return value


def _thaw_auth_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_auth_value(item) for key, item in value.items()}
    if isinstance(value, _FrozenAuthList):
        return [_thaw_auth_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_thaw_auth_value(item) for item in value)
    if isinstance(value, list):
        return [_thaw_auth_value(item) for item in value]
    return value


def _detach_auth_data(value: Mapping[str, object]) -> dict[str, object]:
    return {
        str(provider): _detach_auth_value(entry) for provider, entry in value.items()
    }


def _freeze_auth_data(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(
        {provider: _freeze_auth_value(entry) for provider, entry in value.items()}
    )


_EMPTY_AUTH_RELOAD_DATA: Mapping[str, object] = MappingProxyType({})


@dataclass(slots=True)
class AuthStoreReloadValue:
    """Redacted owner token and detached live-shape replacement."""

    expected_owner_token: object | None = field(repr=False)
    replacement_owner_token: object | None = field(repr=False)
    data: Mapping[str, object] = field(repr=False)
    validated_data: Mapping[str, object] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.expected_owner_token) is not object:
            raise TypeError("expected_owner_token must be an exact object")
        if type(self.replacement_owner_token) is not object:
            raise TypeError("replacement_owner_token must be an exact object")
        self.data = _detach_auth_data(self.data)
        self.validated_data = _freeze_auth_data(self.validated_data)


class AuthStore:
    """Owner-only JSON credential store keyed by provider name.

    This synchronous owner is confined to the single session thread and is not
    thread-safe. ``path`` is immutable by contract after construction; callers
    mutate credentials only through ``set()``, ``remove()``, and ``reload()``.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_auth_store_path()
        self._data: Mapping[str, object] = {}
        self._reload_identity = object()
        self._load()

    def _read_data(self) -> dict[str, object]:
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        return _detach_auth_data(body) if isinstance(body, dict) else {}

    def _load(self) -> None:
        self._data = self._read_data()
        self._reload_identity = object()

    def reload(self) -> None:
        self._load()

    def capture_reload_expected(self) -> object:
        return self._reload_identity

    def prepare_reload_data_from_snapshot(
        self, expected_owner_token: object
    ) -> AuthStoreReloadValue:
        replacement = self._read_data()
        prepared = AuthStoreReloadValue(
            expected_owner_token=expected_owner_token,
            replacement_owner_token=object(),
            data=replacement,
            validated_data=replacement,
        )
        if not self.validate_prepared_reload_data(prepared):
            raise ValueError("invalid prepared auth replacement")
        return prepared

    def prepare_reload_data(self) -> AuthStoreReloadValue:
        return self.prepare_reload_data_from_snapshot(self.capture_reload_expected())

    def validate_prepared_reload_data(self, prepared: object) -> bool:
        return (
            type(prepared) is AuthStoreReloadValue
            and _freeze_auth_data(prepared.data) == prepared.validated_data
        )

    def reload_data_matches_expected(self, prepared: object) -> bool:
        return (
            type(prepared) is AuthStoreReloadValue
            and prepared.expected_owner_token is self._reload_identity
        )

    def publish_reload_data(self, prepared: AuthStoreReloadValue) -> None:
        if prepared.expected_owner_token is None:
            return
        self._data = prepared.data
        self._reload_identity = prepared.replacement_owner_token
        prepared.data = _EMPTY_AUTH_RELOAD_DATA
        prepared.validated_data = _EMPTY_AUTH_RELOAD_DATA
        prepared.expected_owner_token = None
        prepared.replacement_owner_token = None

    def get(self, provider: str) -> dict[str, object] | None:
        entry = self._data.get(provider)
        if not isinstance(entry, Mapping):
            return None
        return {str(name): _thaw_auth_value(value) for name, value in entry.items()}

    def set(self, provider: str, entry: Mapping[str, object]) -> None:
        replacement = dict(self._data)
        replacement[provider] = _detach_auth_value(entry)
        self._data = replacement
        self._reload_identity = object()
        self._persist()

    def remove(self, provider: str) -> bool:
        if provider in self._data:
            replacement = dict(self._data)
            del replacement[provider]
            self._data = replacement
            self._reload_identity = object()
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
            json.dump(_thaw_auth_value(self._data), handle, indent=2, sort_keys=True)
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
    # Only a models.json apiKey counts as an auth source (Pi: hasConfiguredAuth
    # checks providerRequestConfigs.apiKey !== undefined). Request headers alone
    # are not auth.
    if models_json_config is not None and models_json_config.api_key:
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
    # AuthStorage.getAuthStatus order (stored/runtime/environment), then the
    # ModelRegistry.getProviderAuthStatus models.json layer. Ambient credentials
    # (Bedrock/Vertex) are intentionally NOT labelled here — Pi's status only
    # reports actual API-key env vars from findEnvKeys.
    if store.get(provider) is not None:
        return AuthStatus(configured=True, source="stored")
    if runtime_api_key:
        return AuthStatus(configured=False, source="runtime", label="--api-key")
    keys = find_env_keys(provider, env)
    if keys:
        return AuthStatus(configured=False, source="environment", label=keys[0])
    if models_json_config is not None and models_json_config.api_key:
        api_key = models_json_config.api_key
        if api_key.startswith("!"):
            return AuthStatus(configured=True, source="models_json_command")
        if env.get(api_key):
            # The configured value names an env var that is set.
            return AuthStatus(configured=True, source="environment", label=api_key)
        return AuthStatus(configured=True, source="models_json_key")
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


def _resolve_request_api_key(
    provider: str,
    *,
    store: AuthStore,
    env: Mapping[str, str],
    runtime_api_key: str | None,
    models_json_config: ProviderAuthRequestConfig | None,
    run_command: CommandRunner | None,
    oauth_token_resolver: OAuthTokenResolver | None,
) -> str | None:
    """Resolve only the prioritized credential sources for one request."""

    if runtime_api_key:
        return runtime_api_key
    api_key = _stored_api_key(store, provider, env, run_command, oauth_token_resolver)
    if api_key is not None:
        return api_key
    api_key = env_api_key(provider, env)
    if api_key is not None:
        return api_key
    if models_json_config is not None and models_json_config.api_key:
        return resolve_config_value(
            models_json_config.api_key, env=env, run_command=run_command
        )
    return None


def _resolve_request_headers(
    *,
    models_json_config: ProviderAuthRequestConfig | None,
    model_headers: Mapping[str, str] | None,
    env: Mapping[str, str],
    run_command: CommandRunner | None,
) -> dict[str, str]:
    """Resolve models.json headers before model headers, preserving overwrites."""

    headers: dict[str, str] = {}
    configured_headers = (
        models_json_config.headers if models_json_config is not None else None
    )
    for raw_headers in (configured_headers, model_headers):
        if raw_headers:
            for name, raw in raw_headers.items():
                resolved = resolve_config_value(raw, env=env, run_command=run_command)
                if resolved is not None:
                    headers[name] = resolved
    return headers


def _finalize_request_auth(
    provider: str,
    *,
    api_key: str | None,
    headers: dict[str, str],
    auth_header: bool,
) -> ResolvedRequestAuth:
    """Apply authHeader's failure/overwrite contract to resolved values."""

    if auth_header:
        if not api_key:
            # Pi returns ok:false when authHeader is set but no key resolves.
            return ResolvedRequestAuth(
                ok=False, error=f'No API key found for "{provider}"'
            )
        headers["Authorization"] = f"Bearer {api_key}"
    return ResolvedRequestAuth(ok=True, api_key=api_key, headers=headers)


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

    api_key = _resolve_request_api_key(
        provider,
        store=store,
        env=env,
        runtime_api_key=runtime_api_key,
        models_json_config=models_json_config,
        run_command=run_command,
        oauth_token_resolver=oauth_token_resolver,
    )
    headers = _resolve_request_headers(
        models_json_config=models_json_config,
        model_headers=model_headers,
        env=env_for_headers if env_for_headers is not None else env,
        run_command=run_command,
    )
    return _finalize_request_auth(
        provider,
        api_key=api_key,
        headers=headers,
        auth_header=(
            models_json_config.auth_header if models_json_config is not None else False
        ),
    )
