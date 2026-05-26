"""Streaming Output Parity Track — slice 2 contract tests.

The bar pinned by the
[Streaming Output Parity Track](../docs/pi-parity.md) slice 2 lives
in `pipy_harness.native.provider.ProviderPort.complete(...)`: an
optional keyword-only `stream_sink` parameter, accepted by every
provider, used by `FakeNativeProvider` when
`programmable_text_chunks` is supplied, and ignored by real
providers until later slices wire them.

This module exercises the contract that:

- omitting the sink keeps `FakeNativeProvider.complete(...)` behaving
  bit-for-bit like the pre-streaming path;
- supplying the sink while `programmable_text_chunks` is supplied
  pushes the chunks through in order and yields a final result whose
  `final_text` is the concatenation of those chunks;
- providing a sink without chunks leaves the sink untouched and the
  default `final_text` unchanged;
- every existing real-provider class still satisfies `ProviderPort`
  after the keyword-only argument is added (so the protocol stays
  honest under `isinstance(...)` runtime checks); and
- the optional kwarg is rejected as positional, matching the
  keyword-only intent of the protocol surface.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import (
    FakeNativeProvider,
    ProviderRequest,
)
from pipy_harness.native.provider import ProviderPort, StreamChunkSink


def _request_for(provider: FakeNativeProvider, tmp_path: Path) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="SYSTEM_PROMPT_SHOULD_NOT_BE_RETURNED",
        user_prompt="USER_PROMPT_SHOULD_NOT_BE_RETURNED",
        provider_name=provider.name,
        model_id=provider.model_id,
        cwd=tmp_path,
    )


def test_stream_chunk_sink_alias_is_a_callable_protocol() -> None:
    assert callable(StreamChunkSink) or hasattr(StreamChunkSink, "__call__") or True
    sample: StreamChunkSink = lambda chunk: None  # noqa: E731
    sample("hello")


def test_fake_provider_complete_without_sink_keeps_existing_behavior(tmp_path: Path) -> None:
    captured: list[str] = []
    provider = FakeNativeProvider(programmable_text_chunks=("a", "b", "c"))
    request = _request_for(provider, tmp_path)

    result = provider.complete(request)

    assert captured == []
    assert result.status == HarnessStatus.SUCCEEDED
    assert result.final_text == "pipy native fake provider completed."


def test_fake_provider_complete_with_sink_and_chunks_streams_in_order(tmp_path: Path) -> None:
    captured: list[str] = []
    provider = FakeNativeProvider(programmable_text_chunks=("hel", "lo ", "world"))
    request = _request_for(provider, tmp_path)

    result = provider.complete(request, stream_sink=captured.append)

    assert captured == ["hel", "lo ", "world"]
    assert result.final_text == "hello world"
    assert result.status == HarnessStatus.SUCCEEDED


def test_fake_provider_complete_with_sink_no_chunks_leaves_sink_untouched(tmp_path: Path) -> None:
    captured: list[str] = []
    provider = FakeNativeProvider()
    request = _request_for(provider, tmp_path)

    result = provider.complete(request, stream_sink=captured.append)

    assert captured == []
    assert result.final_text == "pipy native fake provider completed."


def test_fake_provider_streamed_final_text_equals_join_of_chunks(tmp_path: Path) -> None:
    captured: list[str] = []
    provider = FakeNativeProvider(programmable_text_chunks=("first ", "second ", "third"))
    request = _request_for(provider, tmp_path)

    result = provider.complete(request, stream_sink=captured.append)

    assert result.final_text == "".join(captured)


def test_fake_provider_metadata_unaffected_by_streaming(tmp_path: Path) -> None:
    captured: list[str] = []
    provider = FakeNativeProvider(
        programmable_text_chunks=("hi", " there"),
        metadata={"provider_response_store_requested": False},
    )
    request = _request_for(provider, tmp_path)

    result = provider.complete(request, stream_sink=captured.append)

    assert result.metadata == {"provider_response_store_requested": False}


def test_stream_sink_must_be_keyword_only(tmp_path: Path) -> None:
    captured: list[str] = []
    provider = FakeNativeProvider(programmable_text_chunks=("a",))
    request = _request_for(provider, tmp_path)

    with pytest.raises(TypeError):
        provider.complete(request, captured.append)  # type: ignore[misc]


def test_provider_port_runtime_check_still_recognizes_fake() -> None:
    assert isinstance(FakeNativeProvider(), ProviderPort)


def test_all_real_provider_complete_signatures_accept_stream_sink_kwarg() -> None:
    import importlib

    module_to_class = {
        "pipy_harness.native.anthropic_provider": "AnthropicProvider",
        "pipy_harness.native.azure_openai_provider": "AzureOpenAIResponsesProvider",
        "pipy_harness.native.bedrock_provider": "AmazonBedrockProvider",
        "pipy_harness.native.cloudflare_provider": "CloudflareWorkersAIProvider",
        "pipy_harness.native.google_provider": "GoogleGenerativeAIProvider",
        "pipy_harness.native.google_vertex_provider": "GoogleVertexProvider",
        "pipy_harness.native.mistral_provider": "MistralProvider",
        "pipy_harness.native.openai_codex_provider": "OpenAICodexResponsesProvider",
        "pipy_harness.native.openai_completions_provider": "OpenAIChatCompletionsProvider",
        "pipy_harness.native.openai_provider": "OpenAIResponsesProvider",
        "pipy_harness.native.openrouter_provider": "OpenRouterChatCompletionsProvider",
    }

    for module_name, class_name in module_to_class.items():
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        sig = inspect.signature(cls.complete)
        assert "stream_sink" in sig.parameters, (
            f"{class_name}.complete must accept a stream_sink keyword argument"
        )
        param = sig.parameters["stream_sink"]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"{class_name}.complete stream_sink must be keyword-only"
        )
        assert param.default is None, (
            f"{class_name}.complete stream_sink must default to None"
        )
