from __future__ import annotations

from pipy_harness.native.provider_registry import native_provider_available


def _available(env: dict[str, str]) -> bool:
    return native_provider_available(
        "google-vertex", env=env, openai_codex_credentials_exist=False
    )


def test_available_with_express_api_key_alone():
    # Pi: a Vertex Express API key is sufficient (no project/location needed).
    assert _available({"GOOGLE_CLOUD_API_KEY": "vk"})


def test_available_with_adc_token_and_project():
    assert _available(
        {"GOOGLE_ACCESS_TOKEN": "ya29.x", "GOOGLE_CLOUD_PROJECT": "p"}
    )
    assert _available(
        {"GOOGLE_ACCESS_TOKEN": "ya29.x", "GOOGLE_PROJECT_ID": "p"}
    )


def test_unavailable_without_key_or_adc():
    assert not _available({})
    # ADC token without a project is not enough.
    assert not _available({"GOOGLE_ACCESS_TOKEN": "ya29.x"})
    # A project without an access token (and no api key) is not enough.
    assert not _available({"GOOGLE_CLOUD_PROJECT": "p"})
