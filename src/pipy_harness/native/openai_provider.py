"""OpenAI Responses API provider for the native pipy runtime."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pipy_harness.capture import sanitize_text
from pipy_harness.native._provider_helpers import utc_now, failed_provider_result, serialize_tool_for_responses, extract_responses_tool_calls
from pipy_harness.native.http import (
    ApiErrorField,
    JsonResponse as JsonResponse,
    JsonHTTPClient,
    ProviderHTTPError,
    UrllibJsonHTTPClient,
    extract_responses_usage,
)
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.models import HarnessStatus
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.deferred_tools import (
    responses_tool_search_items,
    split_deferred_tools,
)
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.provider import StreamChunkSink, apply_provider_headers
from pipy_harness.native.tools.base import ToolDefinition

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_NESTED_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("input_tokens_details", "cached_tokens"),
    ("output_tokens_details", "reasoning_tokens"),
)


def openai_http_client() -> UrllibJsonHTTPClient:
    """Build the shared JSON client wired with OpenAI Responses error types."""

    return UrllibJsonHTTPClient(
        provider_label="OpenAI API",
        status_error_class=OpenAIHTTPStatusError,
        transport_error_class=OpenAITransportError,
        parse_error_class=OpenAIResponseParseError,
    )


@dataclass(frozen=True, slots=True)
class OpenAIResponsesProvider:
    """OpenAI Responses API provider behind ProviderPort.

    Real adapter with `supports_tool_calls=True`. When
    `ProviderRequest.messages` is non-empty the provider serializes them
    into the Responses API `input` list (with `function_call` and
    `function_call_output` items) and declares `tools` from
    `available_tools`. Legacy single-turn callers leave `messages` empty
    and keep the previous string/list `input` body builder.
    """

    model_id: str
    api_key: str | None = field(
        default_factory=lambda: os.environ.get("OPENAI_API_KEY"), repr=False
    )
    http_client: JsonHTTPClient = field(default_factory=openai_http_client)
    endpoint: str = OPENAI_RESPONSES_URL
    timeout_seconds: float = 60.0
    supports_tool_calls: bool = True
    provider_name: str = "openai"
    # Catalog-resolved request config (parity with the completions adapter).
    # ``extra_headers`` are merged models.json/model headers (an explicit
    # Authorization wins over ``Bearer api_key``); ``reasoning_effort`` is the
    # mapped thinking value, placed in the Responses ``reasoning.effort`` key.
    extra_headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    reasoning_effort: str | None = None
    supports_tool_search: bool = False

    @property
    def name(self) -> str:
        return self.provider_name

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
        if not self.model_id:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="OpenAIConfigurationError",
                error_message=f"--native-model is required for native provider {self.name}.",
            )
        has_explicit_authorization = any(
            header_name.lower() == "authorization" for header_name in self.extra_headers
        )
        if not self.api_key and not has_explicit_authorization:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="OpenAIAuthError",
                error_message=(
                    "OpenAI API key is required in the environment for native "
                    f"provider {self.name}."
                ),
            )

        immediate_tools, deferred_tools = split_deferred_tools(
            request,
            enabled=self.supports_tool_search,
        )
        body: dict[str, Any] = {
            "model": self.model_id,
            "instructions": request.system_prompt,
            "input": _responses_input(
                request,
                deferred_tools={tool.name: tool for tool in deferred_tools},
            ),
            "store": False,
        }
        if immediate_tools:
            body["tools"] = [
                serialize_tool_for_responses(tool)
                for tool in immediate_tools
            ]
        # Responses-native thinking: the mapped effort goes in ``reasoning.effort``.
        if self.reasoning_effort is not None:
            body["reasoning"] = {"effort": self.reasoning_effort}
        headers = {"Content-Type": "application/json"}
        # Merged models.json/model headers (may include an explicit Authorization).
        for header_name, header_value in self.extra_headers.items():
            headers[header_name] = header_value
        # Apply ``Bearer api_key`` only when no explicit Authorization is present.
        if self.api_key and not has_explicit_authorization:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers = apply_provider_headers(request, headers)

        try:
            response = self.http_client.post_json(
                self.endpoint,
                headers=headers,
                body=body,
                timeout_seconds=self.timeout_seconds,
                cancel_token=cancel_token,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise OpenAIHTTPStatusError(
                    f"OpenAI API request failed with HTTP status {response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = _parse_response(response.body)
        except OpenAIProviderError as exc:
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


def _responses_input(
    request: ProviderRequest,
    *,
    deferred_tools: Mapping[str, ToolDefinition] | None = None,
) -> str | list[dict[str, object]]:
    if request.messages:
        items: list[dict[str, object]] = []
        loaded_tool_names: set[str] = set()
        for envelope in request.messages:
            items.extend(_envelope_to_input_items(envelope))
            if isinstance(envelope, AgentToolResultMessage) and deferred_tools:
                items.extend(
                    responses_tool_search_items(
                        envelope,
                        deferred_tools=deferred_tools,
                        loaded_tool_names=loaded_tool_names,
                    )
                )
        _attach_images(items, request)
        return items
    if request.no_tool_repl_context is None or not request.no_tool_repl_context.exchanges:
        if not request.attachments:
            return request.user_prompt
        single: list[dict[str, object]] = [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": request.user_prompt}],
            }
        ]
        _attach_images(single, request)
        return single
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
                "content": [{"type": "output_text", "text": exchange.provider_final_text}],
            }
        )
    messages.append(
        {
            "role": "user",
            "content": [{"type": "input_text", "text": request.user_prompt}],
        }
    )
    _attach_images(messages, request)
    return messages


def _attach_images(items: list[dict[str, object]], request: ProviderRequest) -> None:
    """Append ``input_image`` data-URL blocks to the latest user message.

    Image attachments belong to the current user turn, so they ride on the last
    user message. The Responses API accepts ``input_image`` content parts with a
    base64 ``data:`` URL alongside ``input_text``.
    """

    if not request.attachments:
        return
    for item in reversed(items):
        if item.get("role") != "user":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            return
        for attachment in request.attachments:
            content.append(
                {
                    "type": "input_image",
                    "image_url": (
                        f"data:{attachment.media_type};base64,"
                        f"{attachment.data_base64}"
                    ),
                }
            )
        return


def _envelope_to_input_items(envelope: Any) -> list[dict[str, object]]:
    """Translate one ``AgentMessage`` into Responses API input items."""

    if isinstance(envelope, AgentUserMessage):
        return [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": envelope.content.value}],
            }
        ]
    if isinstance(envelope, AgentAssistantMessage):
        items: list[dict[str, object]] = []
        if envelope.content.value:
            items.append(
                {
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": envelope.content.value}
                    ],
                }
            )
        for call in envelope.tool_calls:
            items.append(
                {
                    "type": "function_call",
                    "call_id": call.provider_correlation_id,
                    "name": call.tool_name,
                    "arguments": call.arguments_json.value,
                }
            )
        return items
    if isinstance(envelope, AgentToolResultMessage):
        return [
            {
                "type": "function_call_output",
                "call_id": envelope.provider_correlation_id,
                "output": envelope.content.value,
            }
        ]
    raise OpenAIResponseParseError(
        f"unsupported message envelope: {type(envelope).__name__}"
    )

@dataclass(frozen=True, slots=True)
class ParsedOpenAIResponse:
    final_text: str | None
    usage: dict[str, int | float]
    response_status: str
    tool_calls: tuple[ProviderToolCall, ...] = ()


class OpenAIProviderError(ProviderHTTPError):
    """Base class for sanitized OpenAI provider errors."""


class OpenAIHTTPStatusError(OpenAIProviderError):
    """Raised when OpenAI returns a non-success HTTP status."""

    provider_label = "OpenAI API"
    api_error_fields = (
        ApiErrorField("type", "api_error_type", sanitize=False, allow_int=False),
        ApiErrorField("code", "api_error_code", sanitize=False, allow_int=False),
    )


class OpenAITransportError(OpenAIProviderError):
    """Raised when the HTTP request cannot reach OpenAI."""


class OpenAIResponseParseError(OpenAIProviderError):
    """Raised when the OpenAI response shape is unsupported."""


def _parse_response(body: Mapping[str, Any]) -> ParsedOpenAIResponse:
    status = body.get("status")
    response_status = sanitize_text(status) if isinstance(status, str) else "unknown"
    if response_status and response_status != "completed":
        raise OpenAIResponseParseError(
            f"OpenAI response status was {response_status}.",
            metadata={
                "provider_response_store_requested": False,
                "response_status": response_status,
            },
        )

    final_text = _extract_final_text(body)
    tool_calls = extract_responses_tool_calls(body.get("output"), provider_prefix="openai")
    if not final_text and not tool_calls:
        raise OpenAIResponseParseError(
            "OpenAI response did not include final output text or tool calls.",
            metadata={
                "provider_response_store_requested": False,
                "response_status": response_status,
            },
        )

    return ParsedOpenAIResponse(
        final_text=final_text,
        usage=extract_responses_usage(body.get("usage"), OPENAI_NESTED_USAGE_FIELDS),
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
            if content_item.get("type") == "output_text" and isinstance(content_item.get("text"), str):
                chunks.append(content_item["text"])
    if not chunks:
        return None
    return "".join(chunks)
