"""OpenAI Responses API provider for the native pipy runtime."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pipy_harness.models import HarnessStatus
from pipy_harness.native._provider_helpers import (
    failed_provider_result,
    serialize_tool_for_responses,
    utc_now,
)
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.http import (
    ApiErrorField,
    JsonHTTPClient,
    ProviderHTTPError,
    UrllibJsonHTTPClient,
)
from pipy_harness.native.http import (
    JsonResponse as JsonResponse,
)
from pipy_harness.native.models import CacheRetention, ProviderRequest, ProviderResult
from pipy_harness.native.provider import (
    COPILOT_PROVIDER_NAME,
    StreamChunkSink,
    apply_provider_headers,
    copilot_dynamic_headers,
)
from pipy_harness.native.providers.openai_prompt_cache import (
    clamp_openai_prompt_cache_key,
    resolve_cache_retention,
)
from pipy_harness.native.providers.openai_responses_wire import (
    ResponsesTranscriptOptions,
    parse_response,
    resolve_responses_transcript,
    responses_input,
)

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_NESTED_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("input_tokens_details", "cached_tokens"),
    # Pi ``openai-responses-shared.ts``: ``input_tokens_details.cache_write_tokens``.
    ("input_tokens_details", "cache_write_tokens"),
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
    # Pi ``buildParams`` (``openai-responses.ts:343-358``): an on-state effort
    # also sends ``reasoning.summary`` ("auto") and asks for the encrypted
    # reasoning item; xai asks for it on every reasoning request. Resolved by
    # catalog construction; a directly constructed adapter sends neither.
    reasoning_summary: str | None = None
    include_encrypted_reasoning: bool = False
    supports_tool_search: bool = False
    # Pi's mid-conversation system-message compat (SYS1b), resolved per flag
    # by catalog construction: later system messages stay in place, later
    # tools load as ``additional_tools`` items, and later messages use
    # ``instruction_role``.
    supports_mid_convo_system_messages: bool = False
    supports_additional_tools: bool = False
    instruction_role: str = "developer"
    # Pi ``OpenAIResponsesCompat`` prompt-cache bits, resolved from the catalog
    # row at construction (``openai-responses.ts:68-80``): the session-affinity
    # header format (``openai`` | ``openai-nosession`` | ``openrouter``),
    # long-retention support (default true) and GPT-5.6+ explicit cache mode.
    session_affinity_format: str = "openai"
    supports_long_cache_retention: bool = True
    supports_explicit_prompt_cache_mode: bool = False

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

        transcript = resolve_responses_transcript(
            request,
            ResponsesTranscriptOptions(
                supports_mid_convo_system_messages=(
                    self.supports_mid_convo_system_messages
                ),
                supports_additional_tools=self.supports_additional_tools,
                supports_tool_search=self.supports_tool_search,
            ),
        )
        body: dict[str, Any] = {
            "model": self.model_id,
            "instructions": transcript.instructions,
            "input": responses_input(
                request,
                parse_error_class=OpenAIResponseParseError,
                transcript=transcript,
                instruction_role=self.instruction_role,
                attach_images=True,
            ),
            "store": False,
        }
        retention = resolve_cache_retention(request.cache_retention)
        self._apply_prompt_cache_fields(body, request, retention)
        if transcript.tools:
            body["tools"] = [
                serialize_tool_for_responses(tool) for tool in transcript.tools
            ]
        self._apply_reasoning_fields(body)
        headers = self._request_headers(
            request, retention, has_explicit_authorization=has_explicit_authorization
        )
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
            result = parse_response(
                response.body,
                parse_error_class=OpenAIResponseParseError,
                response_label="OpenAI",
                nested_usage_fields=OPENAI_NESTED_USAGE_FIELDS,
                tool_call_provider_prefix="openai",
            )
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

    def _apply_reasoning_fields(self, body: dict[str, Any]) -> None:
        """Responses-native thinking: the mapped effort in ``reasoning.effort``."""

        if self.reasoning_effort is not None:
            reasoning: dict[str, str] = {"effort": self.reasoning_effort}
            if self.reasoning_summary is not None:
                reasoning["summary"] = self.reasoning_summary
            body["reasoning"] = reasoning
        if self.include_encrypted_reasoning:
            body["include"] = ["reasoning.encrypted_content"]

    def _request_headers(
        self,
        request: ProviderRequest,
        retention: CacheRetention,
        *,
        has_explicit_authorization: bool,
    ) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        # Merged models.json/model headers (may include an explicit Authorization).
        for header_name, header_value in self.extra_headers.items():
            headers[header_name] = header_value
        # Copilot per-request headers follow the model headers (Pi
        # ``createClient``); this adapter sends the attachments as images.
        if self.provider_name == COPILOT_PROVIDER_NAME:
            headers.update(
                copilot_dynamic_headers(request, images_sent=bool(request.attachments))
            )
        # Session-affinity headers follow the model headers; the request-scoped
        # hook may still override them, like Pi's ``options.headers``.
        headers.update(self._session_affinity_headers(request, retention))
        # Apply ``Bearer api_key`` only when no explicit Authorization is present.
        if self.api_key and not has_explicit_authorization:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _apply_prompt_cache_fields(
        self,
        body: dict[str, Any],
        request: ProviderRequest,
        retention: CacheRetention,
    ) -> None:
        """Pi ``buildParams`` cache fields (``openai-responses.ts:309-316``)."""

        if retention != "none":
            key = clamp_openai_prompt_cache_key(request.session_id)
            if key is not None:
                body["prompt_cache_key"] = key
        if (
            retention == "long"
            and self.supports_long_cache_retention
            and not self.supports_explicit_prompt_cache_mode
        ):
            body["prompt_cache_retention"] = "24h"
        if self.supports_explicit_prompt_cache_mode:
            if retention == "none":
                body["prompt_cache_options"] = {"mode": "explicit"}
            elif retention == "long" and self.supports_long_cache_retention:
                body["prompt_cache_options"] = {"ttl": "30m"}

    def _session_affinity_headers(
        self, request: ProviderRequest, retention: CacheRetention
    ) -> dict[str, str]:
        """Pi ``createClient`` affinity headers (``openai-responses.ts:259-268``).

        They carry the unclamped session id and are omitted when retention is
        ``none`` or there is no session.
        """

        if retention == "none":
            return {}
        session_id = request.session_id
        if not session_id:
            return {}
        if self.session_affinity_format == "openrouter":
            return {"x-session-id": session_id}
        headers: dict[str, str] = {}
        if self.session_affinity_format == "openai":
            headers["session_id"] = session_id
        headers["x-client-request-id"] = session_id
        return headers


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
