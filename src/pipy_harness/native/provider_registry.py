"""Native provider/model capability registry."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass


DS4_BASE_URL_ENV = "PIPY_DS4_BASE_URL"
DS4_API_KEY_ENV = "PIPY_DS4_API_KEY"
DS4_DEFAULT_BASE_URL = "http://127.0.0.1:8000/v1"
DS4_DEFAULT_MODEL = "deepseek-v4-flash"


@dataclass(frozen=True, slots=True)
class NativeProviderSpec:
    """Static native provider metadata used before constructing adapters."""

    provider_name: str
    default_model: str
    availability: str
    unavailable_message: str | None = None
    requires_model_for_run: bool = True
    supports_tool_calls: bool = False
    auto_default: bool = True


NATIVE_PROVIDER_REGISTRY: "OrderedDict[str, NativeProviderSpec]" = OrderedDict(
    (
        (
            "fake",
            NativeProviderSpec(
                provider_name="fake",
                default_model="fake-native-bootstrap",
                availability="always",
                requires_model_for_run=False,
            ),
        ),
        (
            "ds4",
            NativeProviderSpec(
                provider_name="ds4",
                default_model=DS4_DEFAULT_MODEL,
                availability="local",
                unavailable_message=None,
                requires_model_for_run=False,
                supports_tool_calls=True,
                auto_default=False,
            ),
        ),
        (
            "openai-codex",
            NativeProviderSpec(
                provider_name="openai-codex",
                default_model="gpt-5.5",
                availability="openai-codex-login",
                unavailable_message=(
                    "pipy: openai-codex is not logged in. "
                    "Run /login openai-codex first."
                ),
                supports_tool_calls=True,
            ),
        ),
        (
            "openai",
            NativeProviderSpec(
                provider_name="openai",
                default_model="gpt-5.5",
                availability="env:OPENAI_API_KEY",
                unavailable_message=(
                    "pipy: openai is unavailable because OPENAI_API_KEY is not set."
                ),
                supports_tool_calls=True,
            ),
        ),
        (
            "openrouter",
            NativeProviderSpec(
                provider_name="openrouter",
                default_model="openai/gpt-5.1-codex",
                availability="env:OPENROUTER_API_KEY",
                unavailable_message=(
                    "pipy: openrouter is unavailable because OPENROUTER_API_KEY is not set."
                ),
                supports_tool_calls=True,
            ),
        ),
        (
            "anthropic",
            NativeProviderSpec(
                provider_name="anthropic",
                default_model="claude-3-5-sonnet-20241022",
                availability="env:ANTHROPIC_API_KEY",
                unavailable_message=(
                    "pipy: anthropic is unavailable because ANTHROPIC_API_KEY is not set."
                ),
                supports_tool_calls=True,
            ),
        ),
        (
            "google",
            NativeProviderSpec(
                provider_name="google",
                default_model="gemini-2.0-flash-exp",
                availability="env-any:GOOGLE_API_KEY,GEMINI_API_KEY",
                unavailable_message=(
                    "pipy: google is unavailable because GOOGLE_API_KEY or "
                    "GEMINI_API_KEY is not set."
                ),
                supports_tool_calls=True,
            ),
        ),
        (
            "mistral",
            NativeProviderSpec(
                provider_name="mistral",
                default_model="mistral-large-latest",
                availability="env:MISTRAL_API_KEY",
                unavailable_message=(
                    "pipy: mistral is unavailable because MISTRAL_API_KEY is not set."
                ),
                supports_tool_calls=True,
            ),
        ),
        (
            "amazon-bedrock",
            NativeProviderSpec(
                provider_name="amazon-bedrock",
                default_model="anthropic.claude-3-5-sonnet-20240620-v1:0",
                availability="env-all:AWS_ACCESS_KEY_ID,AWS_SECRET_ACCESS_KEY",
                unavailable_message=(
                    "pipy: amazon-bedrock is unavailable because AWS_ACCESS_KEY_ID "
                    "or AWS_SECRET_ACCESS_KEY is not set."
                ),
                supports_tool_calls=True,
            ),
        ),
        (
            "openai-completions",
            NativeProviderSpec(
                provider_name="openai-completions",
                default_model="gpt-4o-mini",
                availability="env:OPENAI_API_KEY",
                unavailable_message=(
                    "pipy: openai-completions is unavailable because "
                    "OPENAI_API_KEY is not set."
                ),
                supports_tool_calls=True,
            ),
        ),
        (
            "azure-openai",
            NativeProviderSpec(
                provider_name="azure-openai",
                default_model="gpt-4o",
                availability="env-all:AZURE_OPENAI_ENDPOINT,AZURE_OPENAI_API_KEY",
                unavailable_message=(
                    "pipy: azure-openai is unavailable because AZURE_OPENAI_ENDPOINT "
                    "or AZURE_OPENAI_API_KEY is not set."
                ),
                supports_tool_calls=True,
            ),
        ),
        (
            "cloudflare",
            NativeProviderSpec(
                provider_name="cloudflare",
                default_model="@cf/meta/llama-3.1-8b-instruct",
                availability="env-all:CLOUDFLARE_ACCOUNT_ID,CLOUDFLARE_API_TOKEN",
                unavailable_message=(
                    "pipy: cloudflare is unavailable because CLOUDFLARE_ACCOUNT_ID "
                    "or CLOUDFLARE_API_TOKEN is not set."
                ),
                supports_tool_calls=True,
            ),
        ),
        (
            "google-vertex",
            NativeProviderSpec(
                provider_name="google-vertex",
                default_model="gemini-2.0-flash-001",
                availability=(
                    "env-google-vertex:GOOGLE_ACCESS_TOKEN,"
                    "GOOGLE_CLOUD_PROJECT,GOOGLE_PROJECT_ID"
                ),
                unavailable_message=(
                    "pipy: google-vertex is unavailable because GOOGLE_ACCESS_TOKEN "
                    "or GOOGLE_CLOUD_PROJECT is not set."
                ),
                supports_tool_calls=True,
            ),
        ),
    )
)

SUPPORTED_NATIVE_PROVIDERS = frozenset(NATIVE_PROVIDER_REGISTRY)
DEFAULT_NATIVE_MODELS = {
    name: spec.default_model for name, spec in NATIVE_PROVIDER_REGISTRY.items()
}


def native_provider_spec(provider_name: str) -> NativeProviderSpec | None:
    return NATIVE_PROVIDER_REGISTRY.get(provider_name)


def native_provider_available(
    provider_name: str,
    *,
    env: Mapping[str, str],
    openai_codex_credentials_exist: bool,
    for_auto_default: bool = False,
) -> bool:
    spec = native_provider_spec(provider_name)
    if spec is None:
        return False
    if for_auto_default and not spec.auto_default:
        return False
    availability = spec.availability
    if availability == "always":
        return True
    if availability == "local":
        return True
    if availability == "openai-codex-login":
        return openai_codex_credentials_exist
    if availability.startswith("env:"):
        return bool(env.get(availability.removeprefix("env:")))
    if availability.startswith("env-any:"):
        names = _split_env_names(availability.removeprefix("env-any:"))
        return any(env.get(name) for name in names)
    if availability.startswith("env-all:"):
        names = _split_env_names(availability.removeprefix("env-all:"))
        return all(env.get(name) for name in names)
    if availability.startswith("env-google-vertex:"):
        return bool(
            env.get("GOOGLE_ACCESS_TOKEN")
            and (env.get("GOOGLE_CLOUD_PROJECT") or env.get("GOOGLE_PROJECT_ID"))
        )
    return False


def native_provider_unavailable_message(provider_name: str) -> str:
    spec = native_provider_spec(provider_name)
    if spec is not None and spec.unavailable_message is not None:
        return spec.unavailable_message
    return "pipy: unsupported native provider."


def _split_env_names(value: str) -> tuple[str, ...]:
    return tuple(name.strip() for name in value.split(",") if name.strip())
