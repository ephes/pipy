"""Azure OpenAI Responses API provider for the native pipy runtime.

Azure OpenAI exposes the same Responses-style request/response shape as
OpenAI's first-party endpoint, but reaches it through a per-deployment URL
and authenticates with an ``api-key`` header instead of a bearer token.

This adapter shares the Responses wire-translation helpers with
``providers/openai_responses`` through ``providers/openai_responses_wire``; the
two adapters keep their own auth/URL/deployment resolution, provider dataclass,
and error hierarchy so they can drift independently.
"""

from __future__ import annotations

import os
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pipy_harness.native._provider_helpers import utc_now, failed_provider_result, serialize_tool_for_responses
from pipy_harness.native.http import (
    ApiErrorField,
    JsonResponse as JsonResponse,
    JsonHTTPClient,
    ProviderHTTPError,
    UrllibJsonHTTPClient,
)
from pipy_harness.models import HarnessStatus
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import StreamChunkSink, apply_provider_headers
from pipy_harness.native.providers.openai_responses_wire import (
    parse_response,
    responses_input,
)

DEFAULT_AZURE_OPENAI_API_VERSION = "v1"
# Azure host suffixes for which the base URL is normalized to ``/openai/v1``,
# matching Pi's ``normalizeAzureBaseUrl`` (azure-openai-responses.ts).
_AZURE_HOST_SUFFIXES = (".openai.azure.com", ".cognitiveservices.azure.com")
AZURE_OPENAI_NESTED_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("input_tokens_details", "cached_tokens"),
    ("output_tokens_details", "reasoning_tokens"),
)


@dataclass(frozen=True, slots=True)
class _AzureOpenAIRequestConfiguration:
    """Validated, single-read configuration for one Responses request."""

    url: str
    deployment: str
    api_key: str | None
    has_explicit_auth: bool
    extra_headers: tuple[tuple[str, str], ...]


def _parse_deployment_name_map(value: str | None) -> dict[str, str]:
    """Parse Pi's ``AZURE_OPENAI_DEPLOYMENT_NAME_MAP`` (``modelId=deployment,...``).

    Mirrors ``parseDeploymentNameMap`` in Pi's ``azure-openai-responses.ts``:
    split the value on commas; trim each entry and skip empties; split the entry
    on ``=`` taking only the first two parts (Pi uses JS ``split("=", 2)``, which
    **discards** any remainder, so ``a=b=c`` maps ``a -> b``); skip the entry when
    either of those raw, pre-trim parts is empty; then store the trimmed model id
    and deployment name. Later duplicates win (``Map.set`` semantics).
    """

    result: dict[str, str] = {}
    if not value:
        return result
    for entry in value.split(","):
        trimmed = entry.strip()
        if not trimmed:
            continue
        parts = trimmed.split("=")
        model_part = parts[0]
        deployment_part = parts[1] if len(parts) > 1 else ""
        if not model_part or not deployment_part:
            continue
        result[model_part.strip()] = deployment_part.strip()
    return result


def _normalize_azure_base_url(base_url: str) -> str:
    """Normalize an Azure base URL to Pi's ``/openai/v1`` surface.

    Mirrors ``normalizeAzureBaseUrl`` in Pi's ``azure-openai-responses.ts``:
    for an Azure host (``*.openai.azure.com`` / ``*.cognitiveservices.azure.com``)
    whose path is empty/``/``/``/openai``, rewrite the path to ``/openai/v1`` and
    drop the query so the request becomes
    ``<host>/openai/v1/responses?api-version=...``. Any other base URL (custom
    gateway, or an Azure host already carrying a path) is returned trimmed with
    trailing slashes stripped, otherwise unchanged.
    """

    trimmed = base_url.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(trimmed)
    hostname = (parsed.hostname or "").lower()
    is_azure_host = any(hostname.endswith(suffix) for suffix in _AZURE_HOST_SUFFIXES)
    normalized_path = parsed.path.rstrip("/")
    if is_azure_host and normalized_path in ("", "/openai"):
        rebuilt = parsed._replace(path="/openai/v1", query="")
        return urllib.parse.urlunsplit(rebuilt).rstrip("/")
    return trimmed


def azure_openai_http_client() -> UrllibJsonHTTPClient:
    """Build the shared JSON client wired with Azure OpenAI Responses error types."""

    return UrllibJsonHTTPClient(
        provider_label="Azure OpenAI API",
        status_error_class=AzureOpenAIHTTPStatusError,
        transport_error_class=AzureOpenAITransportError,
        parse_error_class=AzureOpenAIResponseParseError,
    )


@dataclass(frozen=True, slots=True)
class AzureOpenAIResponsesProvider:
    """Azure OpenAI Responses API provider behind ProviderPort.

    ``model_id`` doubles as the Azure deployment name unless ``deployment``
    is set explicitly. The endpoint URL is composed as Pi's ``AzureOpenAI`` SDK
    v1 surface: the base URL is normalized to ``/openai/v1`` for Azure hosts and
    the request is ``{normalized_base}/responses?api-version={api_version}``
    (default ``api-version=v1``), with the deployment carried as the body
    ``model`` field rather than in the URL path. Authentication uses the
    ``api-key`` header (Azure's convention), not ``Authorization: Bearer``.

    This mirrors Pi (``azure-openai-responses.ts``), which delegates URL
    composition to the ``AzureOpenAI`` SDK with a ``/openai/v1`` base and
    ``api-version=v1``. Catalog construction reuses this adapter contract.

    Config-source resolution matches Pi's ``resolveAzureConfig`` /
    ``resolveDeploymentName``:

    - **Base URL** precedence (``_resolve_base_url``): env ``AZURE_OPENAI_BASE_URL``
      (trimmed) > a default base built from the resource name (``resource_name``
      field or env ``AZURE_OPENAI_RESOURCE_NAME``, read verbatim — Pi does not
      trim it) as ``https://{name}.openai.azure.com/openai/v1`` >
      ``endpoint_url`` (the catalog/models.json ``base_url``, Pi's
      ``model.baseUrl``). The resolved base is then run through
      ``_normalize_azure_base_url``.
    - **Deployment** precedence: the explicit ``deployment`` field > the
      ``AZURE_OPENAI_DEPLOYMENT_NAME_MAP`` model->deployment map lookup by
      ``model_id`` > ``model_id`` itself.
    - **api-version**: the ``api_version`` field (env ``AZURE_OPENAI_API_VERSION``
      or ``v1``).
    """

    model_id: str
    endpoint_url: str | None = None
    resource_name: str | None = None
    api_key: str | None = field(
        default_factory=lambda: os.environ.get("AZURE_OPENAI_API_KEY"), repr=False
    )
    api_version: str = field(
        default_factory=lambda: os.environ.get("AZURE_OPENAI_API_VERSION")
        or DEFAULT_AZURE_OPENAI_API_VERSION
    )
    deployment: str | None = None
    http_client: JsonHTTPClient = field(default_factory=azure_openai_http_client)
    timeout_seconds: float = 60.0
    supports_tool_calls: bool = True
    provider_name: str = "azure-openai"
    # Catalog-resolved request config (parity with the completions adapter).
    # ``extra_headers`` are merged models.json/model headers (an explicit
    # ``api-key``/``Authorization`` wins over the native ``api-key``);
    # ``reasoning_effort`` is the mapped thinking value (Responses-shaped).
    extra_headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    reasoning_effort: str | None = None

    @property
    def name(self) -> str:
        return self.provider_name

    def _resolve_base_url(self) -> str | None:
        """Resolve the effective Azure base URL (Pi ``resolveAzureConfig``)."""

        env_base = os.environ.get("AZURE_OPENAI_BASE_URL")
        if env_base and env_base.strip():
            return env_base.strip()
        resource_name = self.resource_name or os.environ.get(
            "AZURE_OPENAI_RESOURCE_NAME"
        )
        if resource_name:
            return f"https://{resource_name}.openai.azure.com/openai/v1"
        return self.endpoint_url

    def _resolve_deployment(self) -> str:
        """Resolve the deployment name (Pi ``resolveDeploymentName``)."""

        if self.deployment:
            return self.deployment
        mapped = _parse_deployment_name_map(
            os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME_MAP")
        ).get(self.model_id)
        return mapped or self.model_id

    def _configuration_preflight(
        self,
        request: ProviderRequest,
        started_at: datetime,
    ) -> _AzureOpenAIRequestConfiguration | ProviderResult:
        """Validate and resolve request configuration in failure-order."""

        if not self.model_id:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="AzureOpenAIConfigurationError",
                error_message=(
                    f"--native-model is required for native provider {self.name}."
                ),
            )
        base_url = self._resolve_base_url()
        if not base_url:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="AzureOpenAIConfigurationError",
                error_message=(
                    "Azure OpenAI base URL is required: set AZURE_OPENAI_BASE_URL "
                    "or AZURE_OPENAI_RESOURCE_NAME (or a catalog base URL) for "
                    f"native provider {self.name}."
                ),
            )
        extra_headers = tuple(self.extra_headers.items())
        has_explicit_auth = any(
            header_name.lower() in ("authorization", "api-key")
            for header_name, _ in extra_headers
        )
        if not self.api_key and not has_explicit_auth:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="AzureOpenAIAuthError",
                error_message=(
                    "Azure OpenAI API key is required in the environment "
                    f"for native provider {self.name}."
                ),
            )

        deployment = self._resolve_deployment()
        normalized_endpoint = _normalize_azure_base_url(base_url)
        return _AzureOpenAIRequestConfiguration(
            url=f"{normalized_endpoint}/responses?api-version={self.api_version}",
            deployment=deployment,
            api_key=self.api_key,
            has_explicit_auth=has_explicit_auth,
            extra_headers=extra_headers,
        )

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del stream_sink, reasoning_sink
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        started_at = utc_now()
        configuration = self._configuration_preflight(request, started_at)
        if isinstance(configuration, ProviderResult):
            return configuration

        body: dict[str, Any] = {
            # Pi's AzureOpenAI v1 surface passes the deployment as the body
            # ``model`` field (buildParams: ``model: deploymentName``).
            "model": configuration.deployment,
            "instructions": request.system_prompt,
            "input": responses_input(
                request,
                parse_error_class=AzureOpenAIResponseParseError,
            ),
            "store": False,
        }
        if request.available_tools:
            body["tools"] = [
                serialize_tool_for_responses(tool)
                for tool in request.available_tools
            ]
        # Azure shares the Responses thinking shape (reasoning.effort).
        if self.reasoning_effort is not None:
            body["reasoning"] = {"effort": self.reasoning_effort}
        headers = {"Content-Type": "application/json"}
        # Merged models.json/model headers (may include an explicit api-key).
        for header_name, header_value in configuration.extra_headers:
            headers[header_name] = header_value
        # Apply the native ``api-key`` header only when no explicit auth header
        # is present, so an explicit models.json auth header wins.
        if configuration.api_key and not configuration.has_explicit_auth:
            headers["api-key"] = configuration.api_key
        headers = apply_provider_headers(request, headers)

        try:
            response = self.http_client.post_json(
                configuration.url,
                headers=headers,
                body=body,
                timeout_seconds=self.timeout_seconds,
                cancel_token=cancel_token,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise AzureOpenAIHTTPStatusError(
                    "Azure OpenAI API request failed with HTTP status "
                    f"{response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = parse_response(
                response.body,
                parse_error_class=AzureOpenAIResponseParseError,
                response_label="Azure OpenAI",
                nested_usage_fields=AZURE_OPENAI_NESTED_USAGE_FIELDS,
                tool_call_provider_prefix="azure-openai",
            )
        except AzureOpenAIProviderError as exc:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type=type(exc).__name__,
                error_message=str(exc),
                metadata=exc.metadata,
            )

        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=started_at,
            ended_at=utc_now(),
            final_text=result.final_text,
            usage=result.usage,
            metadata={
                "provider_response_store_requested": False,
                "response_status": result.response_status,
            },
            tool_calls=result.tool_calls,
        )


class AzureOpenAIProviderError(ProviderHTTPError):
    """Base class for sanitized Azure OpenAI provider errors."""


class AzureOpenAIHTTPStatusError(AzureOpenAIProviderError):
    """Raised when Azure OpenAI returns a non-success HTTP status."""

    provider_label = "Azure OpenAI API"
    api_error_fields = (
        ApiErrorField("type", "api_error_type", sanitize=False, allow_int=False),
        ApiErrorField("code", "api_error_code", sanitize=False, allow_int=False),
    )


class AzureOpenAITransportError(AzureOpenAIProviderError):
    """Raised when the HTTP request cannot reach Azure OpenAI."""


class AzureOpenAIResponseParseError(AzureOpenAIProviderError):
    """Raised when the Azure OpenAI response shape is unsupported."""
