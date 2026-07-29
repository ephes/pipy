"""Shared OpenAI Responses wire-translation helpers.

Both the first-party OpenAI Responses adapter (``providers/openai_responses``)
and the Azure OpenAI Responses adapter (``providers/azure_openai_responses``)
speak the identical Responses request/response wire shape. This module owns the
byte-identical translation in both directions:

- :func:`responses_input` serializes canonical ``ProviderRequest`` messages into
  the Responses ``input`` list (``function_call`` / ``function_call_output``
  items), with the legacy single-turn string and message shapes.
- :func:`envelope_to_input_items` translates one ``AgentMessage`` envelope.
- :func:`parse_response` / :func:`extract_final_text` turn a Responses response
  body into a :class:`ParsedResponse`.

The two adapters differ only where they genuinely differ, threaded through as
parameters here:

- the OpenAI-only deferred-tools / image-attachment extension
  (``deferred_tools`` + ``attach_images``); Azure passes neither;
- the per-provider parse-error class (``parse_error_class``);
- the human-readable response label used in parse-error messages
  (``response_label``, e.g. ``"OpenAI"`` vs ``"Azure OpenAI"``);
- the nested-usage detail-field tuple (``nested_usage_fields``); and
- the tool-call provider prefix (``tool_call_provider_prefix``).

Auth, base-URL/deployment resolution, and the two provider dataclasses and their
error hierarchies stay in the adapter modules.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pipy_harness.capture import sanitize_text
from pipy_harness.native._provider_helpers import extract_responses_tool_calls
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.deferred_tools import responses_tool_search_items
from pipy_harness.native.http import ProviderHTTPError, extract_responses_usage
from pipy_harness.native.models import ProviderRequest, ProviderToolCall
from pipy_harness.native.tools.base import ToolDefinition


@dataclass(frozen=True, slots=True)
class ParsedResponse:
    """Parsed Responses body shared by the OpenAI and Azure OpenAI adapters."""

    final_text: str | None
    usage: dict[str, int | float]
    response_status: str
    tool_calls: tuple[ProviderToolCall, ...] = ()


def responses_input(
    request: ProviderRequest,
    *,
    parse_error_class: type[ProviderHTTPError],
    deferred_tools: Mapping[str, ToolDefinition] | None = None,
    attach_images: bool = False,
) -> str | list[dict[str, object]]:
    """Serialize a ``ProviderRequest`` into the Responses ``input`` payload.

    ``deferred_tools`` and ``attach_images`` are the OpenAI-only extension: when
    provided/enabled the message stream gains the completed client tool-search
    pair for deferred tools and the current user turn gains ``input_image``
    blocks. Azure passes neither and gets the plain Responses translation.
    """

    if request.messages:
        items: list[dict[str, object]] = []
        loaded_tool_names: set[str] = set()
        for envelope in request.messages:
            items.extend(
                envelope_to_input_items(envelope, parse_error_class=parse_error_class)
            )
            if deferred_tools and isinstance(envelope, AgentToolResultMessage):
                items.extend(
                    responses_tool_search_items(
                        envelope,
                        deferred_tools=deferred_tools,
                        loaded_tool_names=loaded_tool_names,
                    )
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
    raise parse_error_class(f"unsupported message envelope: {type(envelope).__name__}")


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
