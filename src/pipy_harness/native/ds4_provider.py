"""OpenAI-compatible ds4 provider for local DeepSeek V4 Flash."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from pipy_harness.native._provider_helpers import JsonHTTPClient
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.openai_completions_provider import (
    OpenAIChatCompletionsProvider,
    UrllibJsonHTTPClient,
)
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.provider_registry import (
    DS4_API_KEY_ENV,
    DS4_BASE_URL_ENV,
    DS4_DEFAULT_BASE_URL,
)


@dataclass(frozen=True, slots=True)
class Ds4ChatCompletionsProvider:
    """Local ds4 Chat Completions provider behind ProviderPort.

    ds4 exposes OpenAI-compatible endpoints. This adapter reuses pipy's
    Chat Completions implementation with ds4 defaults and no required API key.
    Tool calls are enabled after a live ds4 smoke verified OpenAI-style
    `tool_calls` responses and pipy's bounded tool loop end to end.
    """

    model_id: str
    base_url: str = field(
        default_factory=lambda: os.environ.get(DS4_BASE_URL_ENV, DS4_DEFAULT_BASE_URL)
    )
    api_key: str | None = field(default_factory=lambda: os.environ.get(DS4_API_KEY_ENV))
    http_client: JsonHTTPClient = field(
        default_factory=lambda: UrllibJsonHTTPClient(provider_label="ds4 API")
    )
    timeout_seconds: float = 60.0
    supports_tool_calls: bool = True

    @property
    def name(self) -> str:
        return "ds4"

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        provider = OpenAIChatCompletionsProvider(
            model_id=self.model_id,
            api_key=self.api_key,
            http_client=self.http_client,
            endpoint=ds4_chat_completions_endpoint(self.base_url),
            timeout_seconds=self.timeout_seconds,
            supports_tool_calls=self.supports_tool_calls,
            provider_name=self.name,
            auth_required=False,
        )
        return provider.complete(
            request,
            stream_sink=stream_sink,
            reasoning_sink=reasoning_sink,
            cancel_token=cancel_token,
        )


def ds4_chat_completions_endpoint(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/") or DS4_DEFAULT_BASE_URL
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"
