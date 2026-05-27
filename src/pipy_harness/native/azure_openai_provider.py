"""Azure OpenAI Responses API provider for the native pipy runtime.

Azure OpenAI exposes the same Responses-style request/response shape as
OpenAI's first-party endpoint, but reaches it through a per-deployment URL
and authenticates with an ``api-key`` header instead of a bearer token.

This adapter intentionally duplicates the parsing helpers from
``openai_provider`` so the two providers remain decoupled and can drift
independently.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pipy_harness.capture import sanitize_text
from pipy_harness.native._provider_helpers import utc_now, failed_provider_result, JsonResponse, JsonHTTPClient, serialize_tool_for_responses, decode_json_object, extract_responses_tool_calls
from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
)
from pipy_harness.native.usage import NORMALIZED_PROVIDER_USAGE_KEYS, normalize_provider_usage

DEFAULT_AZURE_OPENAI_API_VERSION = "2024-12-01-preview"
AZURE_OPENAI_NESTED_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("input_tokens_details", "cached_tokens"),
    ("output_tokens_details", "reasoning_tokens"),
)


@dataclass(frozen=True, slots=True)
class UrllibJsonHTTPClient:
    """Standard-library JSON client for Azure OpenAI Responses calls."""

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
    ) -> JsonResponse:
        encoded = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=encoded,
            headers=dict(headers),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = response.read()
                status_code = response.getcode()
        except urllib.error.HTTPError as exc:
            raise AzureOpenAIHTTPStatusError.from_http_error(exc) from exc
        except urllib.error.URLError as exc:
            reason = (
                sanitize_text(str(exc.reason))
                if getattr(exc, "reason", None)
                else "request failed"
            )
            raise AzureOpenAITransportError(
                f"Azure OpenAI API request failed: {reason}"
            ) from exc

        return JsonResponse(status_code=status_code, body=decode_json_object(payload, error_class=AzureOpenAIResponseParseError, provider_label="Azure OpenAI API"))


@dataclass(frozen=True, slots=True)
class AzureOpenAIResponsesProvider:
    """Azure OpenAI Responses API provider behind ProviderPort.

    ``model_id`` doubles as the Azure deployment name unless ``deployment``
    is set explicitly. The endpoint URL is composed as
    ``{endpoint_url}/openai/deployments/{deployment}/responses?api-version={api_version}``.
    Authentication uses the ``api-key`` header (Azure's convention), not
    ``Authorization: Bearer``.
    """

    model_id: str
    endpoint_url: str | None = field(
        default_factory=lambda: os.environ.get("AZURE_OPENAI_ENDPOINT")
    )
    api_key: str | None = field(
        default_factory=lambda: os.environ.get("AZURE_OPENAI_API_KEY")
    )
    api_version: str = field(
        default_factory=lambda: os.environ.get("AZURE_OPENAI_API_VERSION")
        or DEFAULT_AZURE_OPENAI_API_VERSION
    )
    deployment: str | None = None
    http_client: JsonHTTPClient = field(default_factory=UrllibJsonHTTPClient)
    timeout_seconds: float = 60.0
    supports_tool_calls: bool = True

    @property
    def name(self) -> str:
        return "azure-openai"

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
    ) -> ProviderResult:
        del stream_sink, reasoning_sink
        started_at = utc_now()
        if not self.model_id:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="AzureOpenAIConfigurationError",
                error_message=(
                    "--native-model is required for native provider azure-openai."
                ),
            )
        if not self.endpoint_url:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="AzureOpenAIConfigurationError",
                error_message=(
                    "Azure OpenAI endpoint URL is required in the environment "
                    "for native provider azure-openai."
                ),
            )
        if not self.api_key:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="AzureOpenAIAuthError",
                error_message=(
                    "Azure OpenAI API key is required in the environment "
                    "for native provider azure-openai."
                ),
            )

        deployment = self.deployment or self.model_id
        normalized_endpoint = self.endpoint_url.rstrip("/")
        url = (
            f"{normalized_endpoint}/openai/deployments/{deployment}/responses"
            f"?api-version={self.api_version}"
        )

        body: dict[str, Any] = {
            "model": self.model_id,
            "instructions": request.system_prompt,
            "input": _responses_input(request),
            "store": False,
        }
        if request.available_tools:
            body["tools"] = [
                serialize_tool_for_responses(tool)
                for tool in request.available_tools
            ]
        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
        }

        try:
            response = self.http_client.post_json(
                url,
                headers=headers,
                body=body,
                timeout_seconds=self.timeout_seconds,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise AzureOpenAIHTTPStatusError(
                    "Azure OpenAI API request failed with HTTP status "
                    f"{response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = _parse_response(response.body)
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


def _responses_input(request: ProviderRequest) -> str | list[dict[str, object]]:
    if request.messages:
        items: list[dict[str, object]] = []
        for envelope in request.messages:
            items.extend(_envelope_to_input_items(envelope))
        return items
    if (
        request.no_tool_repl_context is None
        or not request.no_tool_repl_context.exchanges
    ):
        return request.user_prompt
    messages: list[dict[str, object]] = []
    for exchange in request.no_tool_repl_context.exchanges:
        messages.append(
            {
                "role": "user",
                "content": [{"type": "input_text", "text": exchange.user_prompt}],
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": exchange.provider_final_text}
                ],
            }
        )
    messages.append(
        {
            "role": "user",
            "content": [{"type": "input_text", "text": request.user_prompt}],
        }
    )
    return messages


def _envelope_to_input_items(envelope: Any) -> list[dict[str, object]]:
    """Translate one LoopMessage into one or more Responses API input items."""

    if isinstance(envelope, UserMessage):
        return [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": envelope.content}],
            }
        ]
    if isinstance(envelope, AssistantMessage):
        items: list[dict[str, object]] = []
        if envelope.content:
            items.append(
                {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": envelope.content}],
                }
            )
        for call in envelope.tool_calls:
            items.append(
                {
                    "type": "function_call",
                    "call_id": call.provider_correlation_id,
                    "name": call.tool_name,
                    "arguments": call.arguments_json,
                }
            )
        return items
    if isinstance(envelope, ToolResultMessage):
        return [
            {
                "type": "function_call_output",
                "call_id": _require_provider_correlation_id(envelope),
                "output": envelope.output_text,
            }
        ]
    raise AzureOpenAIResponseParseError(
        f"unsupported message envelope: {type(envelope).__name__}"
    )


def _require_provider_correlation_id(envelope: ToolResultMessage) -> str:
    if envelope.provider_correlation_id:
        return envelope.provider_correlation_id
    raise AzureOpenAIResponseParseError(
        "ToolResultMessage is missing provider_correlation_id."
    )


@dataclass(frozen=True, slots=True)
class ParsedAzureOpenAIResponse:
    final_text: str | None
    usage: dict[str, int | float]
    response_status: str
    tool_calls: tuple[ProviderToolCall, ...] = ()


class AzureOpenAIProviderError(Exception):
    """Base class for sanitized Azure OpenAI provider errors."""

    def __init__(
        self,
        message: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(sanitize_text(message))
        self.metadata = dict(metadata or {})


class AzureOpenAIHTTPStatusError(AzureOpenAIProviderError):
    """Raised when Azure OpenAI returns a non-success HTTP status."""

    @classmethod
    def from_http_error(
        cls, exc: urllib.error.HTTPError
    ) -> AzureOpenAIHTTPStatusError:
        metadata: dict[str, Any] = {"http_status": exc.code}
        try:
            body = decode_json_object(exc.read(), error_class=AzureOpenAIResponseParseError, provider_label="Azure OpenAI API")
        except AzureOpenAIResponseParseError:
            body = {}
        error = body.get("error")
        if isinstance(error, Mapping):
            error_type = error.get("type")
            error_code = error.get("code")
            if isinstance(error_type, str):
                metadata["api_error_type"] = error_type
            if isinstance(error_code, str):
                metadata["api_error_code"] = error_code
        return cls(
            f"Azure OpenAI API request failed with HTTP status {exc.code}.",
            metadata=metadata,
        )


class AzureOpenAITransportError(AzureOpenAIProviderError):
    """Raised when the HTTP request cannot reach Azure OpenAI."""


class AzureOpenAIResponseParseError(AzureOpenAIProviderError):
    """Raised when the Azure OpenAI response shape is unsupported."""


def _parse_response(body: Mapping[str, Any]) -> ParsedAzureOpenAIResponse:
    status = body.get("status")
    response_status = (
        sanitize_text(status) if isinstance(status, str) else "unknown"
    )
    if response_status and response_status != "completed":
        raise AzureOpenAIResponseParseError(
            f"Azure OpenAI response status was {response_status}.",
            metadata={
                "provider_response_store_requested": False,
                "response_status": response_status,
            },
        )

    final_text = _extract_final_text(body)
    tool_calls = extract_responses_tool_calls(body.get("output"), provider_prefix="azure-openai")
    if not final_text and not tool_calls:
        raise AzureOpenAIResponseParseError(
            "Azure OpenAI response did not include final output text or tool calls.",
            metadata={
                "provider_response_store_requested": False,
                "response_status": response_status,
            },
        )

    return ParsedAzureOpenAIResponse(
        final_text=final_text,
        usage=_extract_usage(body.get("usage")),
        response_status=response_status,
        tool_calls=tool_calls,
    )


def _extract_final_text(body: Mapping[str, Any]) -> str | None:
    output_text = body.get("output_text")
    if isinstance(output_text, str) and output_text:
        return output_text

    output = body.get("output")
    if not isinstance(output, list):
        return None

    chunks: list[str] = []
    for item in output:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") not in (None, "message"):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for content_item in content:
            if not isinstance(content_item, Mapping):
                continue
            if content_item.get("type") == "output_text" and isinstance(
                content_item.get("text"), str
            ):
                chunks.append(content_item["text"])
    if not chunks:
        return None
    return "".join(chunks)


def _extract_usage(value: Any) -> dict[str, int | float]:
    if not isinstance(value, Mapping):
        return {}
    usage: dict[str, Any] = {}
    for key in NORMALIZED_PROVIDER_USAGE_KEYS:
        usage[key] = value.get(key)

    for details_key, usage_key in AZURE_OPENAI_NESTED_USAGE_FIELDS:
        details = value.get(details_key)
        if isinstance(details, Mapping) and usage_key in details:
            usage[usage_key] = details[usage_key]

    return normalize_provider_usage(usage)
