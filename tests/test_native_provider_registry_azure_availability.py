from __future__ import annotations

from pipy_harness.native.provider_registry import native_provider_available


def _available(env: dict[str, str]) -> bool:
    return native_provider_available(
        "azure-openai", env=env, openai_codex_credentials_exist=False
    )


def test_available_with_base_url_and_api_key():
    assert _available(
        {
            "AZURE_OPENAI_BASE_URL": "https://r.openai.azure.com",
            "AZURE_OPENAI_API_KEY": "k",
        }
    )


def test_available_with_resource_name_and_api_key():
    assert _available(
        {"AZURE_OPENAI_RESOURCE_NAME": "myacct", "AZURE_OPENAI_API_KEY": "k"}
    )


def test_unavailable_with_only_api_key():
    assert not _available({"AZURE_OPENAI_API_KEY": "k"})


def test_unavailable_with_only_base_source():
    assert not _available({"AZURE_OPENAI_BASE_URL": "https://r.openai.azure.com"})
    assert not _available({"AZURE_OPENAI_RESOURCE_NAME": "myacct"})


def test_legacy_endpoint_env_no_longer_grants_availability():
    # The pipy-only AZURE_OPENAI_ENDPOINT name was dropped for Pi parity.
    assert not _available(
        {"AZURE_OPENAI_ENDPOINT": "https://r.openai.azure.com", "AZURE_OPENAI_API_KEY": "k"}
    )
