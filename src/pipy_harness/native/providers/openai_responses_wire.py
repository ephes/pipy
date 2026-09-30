"""Shared OpenAI Responses wire-translation helpers.

Both the first-party OpenAI Responses adapter (``providers/openai_responses``)
and the Azure OpenAI Responses adapter (``providers/azure_openai_responses``)
speak the identical Responses request/response wire shape. This module owns the
byte-identical translation in both directions:

- :func:`resolve_responses_transcript` and :func:`responses_input` serialize
  canonical ``ProviderRequest`` messages and later system messages into the
  Responses ``input`` list (``function_call`` / ``function_call_output``
  items, developer messages, anchored tool loads), with the legacy
  single-turn string and message shapes. Codex reuses the transcript items
  with its own envelope conversion.
- :func:`envelope_to_input_items` translates one ``AgentMessage`` envelope.
- :func:`parse_response` / :func:`extract_final_text` turn a Responses response
  body into a :class:`ParsedResponse`.

The two adapters differ only where they genuinely differ, threaded through as
parameters here:

- the OpenAI-only image-attachment extension (``attach_images``);
- the per-provider parse-error class (``parse_error_class``);
- the human-readable response label used in parse-error messages
  (``response_label``, e.g. ``"OpenAI"`` vs ``"Azure OpenAI"``);
- the nested-usage detail-field tuple (``nested_usage_fields``); and
- the tool-call provider prefix (``tool_call_provider_prefix``).

Auth, base-URL/deployment resolution, and the two provider dataclasses and their
error hierarchies stay in the adapter modules.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pipy_harness.capture import sanitize_text
from pipy_harness.native._provider_helpers import (
    extract_responses_tool_calls,
    serialize_tool_for_responses,
)
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentMessage,
    AgentSystemMessage,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.system_messages import render_system_message_update
from pipy_harness.native.deferred_tools import short_hash
from pipy_harness.native.http import ProviderHTTPError, extract_responses_usage
from pipy_harness.native.models import ProviderRequest, ProviderToolCall
from pipy_harness.native.providers.transcript import (
    ResolvedTranscript,
    declaration_definition,
    request_tools,
    resolve_request_transcript,
)
from pipy_harness.native.tool_call_ids import portable_tool_correlation_id
from pipy_harness.native.tools.base import ToolDefinition


@dataclass(frozen=True, slots=True)
class ParsedResponse:
    """Parsed Responses body shared by the OpenAI and Azure OpenAI adapters."""

    final_text: str | None
    usage: dict[str, int | float]
    response_status: str
    tool_calls: tuple[ProviderToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class ResponsesTranscriptOptions:
    """One Responses adapter's mid-conversation system-message compat.

    Pi ``convertResponsesMessages`` options: whether later system messages
    stay in place, how anchored tool additions load (message-anchored
    ``additional_tools`` first, else a client ``tool_search`` pair), and the
    role later messages use (``developer`` for reasoning models unless
    ``supportsDeveloperRole`` is false).
    """

    supports_mid_convo_system_messages: bool = False
    supports_additional_tools: bool = False
    supports_tool_search: bool = False
    instruction_role: str = "developer"


@dataclass(frozen=True, slots=True)
class ResponsesTranscript:
    """The ``instructions`` text, top-level tools and the ``input`` builder inputs."""

    instructions: str
    tools: tuple[ToolDefinition, ...]
    resolved: ResolvedTranscript
    tool_loading: str | None


def resolve_responses_transcript(
    request: ProviderRequest, options: ResponsesTranscriptOptions
) -> ResponsesTranscript:
    """Pi ``resolveTranscript`` + ``resolveTranscriptTools`` for a Responses body."""

    resolved = resolve_request_transcript(
        request, supports_mid_convo=options.supports_mid_convo_system_messages
    )
    tools, anchors = request_tools(
        resolved,
        request,
        supports_additions=(
            options.supports_additional_tools or options.supports_tool_search
        ),
    )
    tool_loading = None
    if anchors:
        tool_loading = (
            "additional_tools" if options.supports_additional_tools else "tool_search"
        )
    return ResponsesTranscript(resolved.leading_text, tools, resolved, tool_loading)


def responses_transcript_items(
    transcript: ResponsesTranscript,
    *,
    instruction_role: str,
    envelope_items: Callable[[AgentMessage], list[dict[str, object]]],
) -> list[dict[str, object]]:
    """Pi ``convertResponsesMessages`` over the resolved transcript.

    A later system message first loads the tools it adds (when additions are
    anchored), then, when its rendered update is not empty, becomes an
    instruction-role message. ``msgIndex`` (the tool-search seed) counts every
    converted message except an assistant turn that produced no items.
    """

    items: list[dict[str, object]] = []
    msg_index = 0
    for item in transcript.resolved.items:
        if isinstance(item, AgentSystemMessage):
            items.extend(
                _system_tool_additions(transcript.tool_loading, item, msg_index)
            )
            text = render_system_message_update(item)
            if text:
                items.append({"role": instruction_role, "content": text})
            msg_index += 1
            continue
        converted = envelope_items(item)
        items.extend(converted)
        if converted or not isinstance(item, AgentAssistantMessage):
            msg_index += 1
    return items


def _system_tool_additions(
    tool_loading: str | None, message: AgentSystemMessage, msg_index: int
) -> list[dict[str, object]]:
    if tool_loading is None or not message.tools_added:
        return []
    tools = [declaration_definition(tool) for tool in message.tools_added]
    if tool_loading == "additional_tools":
        return [
            {
                "type": "additional_tools",
                "role": "developer",
                "tools": [serialize_tool_for_responses(tool) for tool in tools],
            }
        ]
    names = [tool.name for tool in tools]
    seed = f"system:{msg_index}:" + ",".join(names)
    call_id = f"pi_tool_load_{short_hash(seed)}"
    return [
        {
            "type": "tool_search_call",
            "call_id": call_id,
            "execution": "client",
            "status": "completed",
            "arguments": {"query": " ".join(names), "limit": len(names)},
        },
        {
            "type": "tool_search_output",
            "call_id": call_id,
            "execution": "client",
            "status": "completed",
            "tools": [
                {
                    **serialize_tool_for_responses(tool),
                    "strict": False,
                    "defer_loading": True,
                }
                for tool in tools
            ],
        },
    ]


def responses_input(
    request: ProviderRequest,
    *,
    parse_error_class: type[ProviderHTTPError],
    transcript: ResponsesTranscript | None = None,
    instruction_role: str = "developer",
    attach_images: bool = False,
) -> str | list[dict[str, object]]:
    """Serialize a ``ProviderRequest`` into the Responses ``input`` payload.

    ``transcript`` carries the resolved system messages
    (:func:`resolve_responses_transcript`); without it the messages are sent
    as they are. ``attach_images`` is the OpenAI-only extension: the current
    user turn gains ``input_image`` blocks. Azure passes no images.
    """

    if request.messages:
        if transcript is None:
            transcript = resolve_responses_transcript(
                request, ResponsesTranscriptOptions()
            )
        items = responses_transcript_items(
            transcript,
            instruction_role=instruction_role,
            envelope_items=lambda envelope: envelope_to_input_items(
                envelope, parse_error_class=parse_error_class
            ),
        )
        if attach_images:
            _attach_images(items, request)
        return items
    if attach_images and request.attachments:
        single: list[dict[str, object]] = [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": request.user_prompt}],
            }
        ]
        _attach_images(single, request)
        return single
    return request.user_prompt


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
                        f"data:{attachment.media_type};base64,{attachment.data_base64}"
                    ),
                }
            )
        return


def envelope_to_input_items(
    envelope: Any,
    *,
    parse_error_class: type[ProviderHTTPError],
) -> list[dict[str, object]]:
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
                    "call_id": portable_tool_correlation_id(
                        call.provider_correlation_id
                    ),
                    "name": call.tool_name,
                    "arguments": call.arguments_json.value,
                }
            )
        return items
    if isinstance(envelope, AgentToolResultMessage):
        return [
            {
                "type": "function_call_output",
                "call_id": portable_tool_correlation_id(
                    envelope.provider_correlation_id
                ),
                "output": envelope.content.value,
            }
        ]
    raise parse_error_class(f"unsupported message envelope: {type(envelope).__name__}")


def response_error_metadata(body: Mapping[str, Any]) -> dict[str, str]:
    """``api_error_type``/``api_error_code`` from a Responses body's ``error``."""

    error = body.get("error")
    if not isinstance(error, Mapping):
        return {}
    metadata: dict[str, str] = {}
    for source, target in (("type", "api_error_type"), ("code", "api_error_code")):
        value = error.get(source)
        if isinstance(value, str) and value:
            metadata[target] = value
    return metadata


def parse_response(
    body: Mapping[str, Any],
    *,
    parse_error_class: type[ProviderHTTPError],
    response_label: str,
    nested_usage_fields: tuple[tuple[str, str], ...],
    tool_call_provider_prefix: str,
) -> ParsedResponse:
    """Parse a Responses success body into a :class:`ParsedResponse`."""

    status = body.get("status")
    response_status = sanitize_text(status) if isinstance(status, str) else "unknown"
    if response_status and response_status != "completed":
        raise parse_error_class(
            f"{response_label} response status was {response_status}.",
            metadata={
                "provider_response_store_requested": False,
                "response_status": response_status,
                # A failed response's ``error`` code/type, which Pi's retry
                # classifier and usage-limit link read from the error text.
                **response_error_metadata(body),
            },
        )

    final_text = extract_final_text(body)
    tool_calls = extract_responses_tool_calls(
        body.get("output"), provider_prefix=tool_call_provider_prefix
    )
    if not final_text and not tool_calls:
        raise parse_error_class(
            f"{response_label} response did not include final output text or tool calls.",
            metadata={
                "provider_response_store_requested": False,
                "response_status": response_status,
            },
        )

    return ParsedResponse(
        final_text=final_text,
        usage=extract_responses_usage(body.get("usage"), nested_usage_fields),
        response_status=response_status,
        tool_calls=tool_calls,
    )


def _message_output_text_chunks(output: list[object]) -> list[str]:
    """Collect message output-text chunks in Responses wire order."""

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
    return chunks


def extract_final_text(body: Mapping[str, Any]) -> str | None:
    """Extract the assistant final text from a Responses body."""

    output_text = body.get("output_text")
    if isinstance(output_text, str) and output_text:
        return output_text

    output = body.get("output")
    if not isinstance(output, list):
        return None

    chunks = _message_output_text_chunks(output)
    if not chunks:
        return None
    return "".join(chunks)
