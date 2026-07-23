"""Shared Google ``generateContent`` wire-translation helpers.

Both the Google Gemini Generative AI adapter
(``providers/google_generative_ai``) and the Google Vertex AI adapter
(``providers/google_vertex``) front the same Gemini models and therefore speak
the identical ``generateContent`` request/response wire shape. This module owns
the byte-identical translation in both directions:

- :func:`gemini_contents` serializes a canonical ``ProviderRequest`` into the
  Gemini ``contents`` list (``functionCall``/``functionResponse``/``inlineData``
  parts), with the legacy single-turn fallback.
- :func:`envelope_to_content` translates one ``AgentMessage`` envelope.
- :func:`serialize_tool_for_gemini` turns one ``ToolDefinition`` into the Gemini
  function-declaration shape.
- :func:`parse_response` / :func:`extract_final_text` / :func:`extract_tool_calls`
  turn a success body into a :class:`ParsedGeminiResponse`.

The two adapters differ only where they genuinely differ, threaded through as
parameters here:

- the per-provider parse-error class (``parse_error_class``);
- the human-readable response label used in parse-error messages
  (``response_label``, e.g. ``"Google"`` vs ``"Google Vertex AI"``);
- the ``usageMetadata`` remap tuple (``usage_fields``);
- the tool-call provider prefix used to synthesize a correlation id
  (``tool_call_provider_prefix``, e.g. ``"google"`` vs ``"google-vertex"``); and
- the Google-only ``inlineData`` image attachment (``attach_images``). The
  Generative AI adapter enables it; Vertex omits image attachment entirely.

Auth, URL/region resolution, the two thinking-config mappings, and the two
provider dataclasses and their separate error hierarchies stay in the adapter
modules.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pipy_harness.native._provider_helpers import safe_response_label
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.http import ProviderHTTPError, extract_usage_from_fields
from pipy_harness.native.models import ProviderRequest, ProviderToolCall
from pipy_harness.native.tools.base import materialize_tool_input_schema


@dataclass(frozen=True, slots=True)
class ParsedGeminiResponse:
    """Parsed Gemini ``generateContent`` body shared by the two adapters."""

    final_text: str | None
    usage: dict[str, int | float]
    finish_reason: str
    tool_calls: tuple[ProviderToolCall, ...] = ()


def gemini_contents(
    request: ProviderRequest,
    *,
    parse_error_class: type[ProviderHTTPError],
    attach_images: bool = False,
) -> list[dict[str, Any]]:
    """Build Gemini ``contents`` from a ``ProviderRequest``.

    When ``request.messages`` is non-empty, translate the envelope. Otherwise
    fall back to the current ``user_prompt``. ``attach_images`` is the
    Generative-AI-only extension: when enabled, ``inlineData`` image parts ride
    on the current user turn. Vertex passes ``attach_images=False`` and gets no
    image attachment.
    """

    contents: list[dict[str, Any]] = []
    if request.messages:
        for envelope in request.messages:
            contents.append(
                envelope_to_content(envelope, parse_error_class=parse_error_class)
            )
        if attach_images:
            _attach_images(contents, request)
        return contents
    contents.append(
        {"role": "user", "parts": [{"text": request.user_prompt}]}
    )
    if attach_images:
        _attach_images(contents, request)
    return contents


def _attach_images(contents: list[dict[str, Any]], request: ProviderRequest) -> None:
    """Append ``inlineData`` image parts to the latest user content.

    Image attachments belong to the current user turn, so they ride on the last
    user content. Gemini accepts ``inlineData`` parts carrying a base64-encoded
    payload and its ``mimeType`` alongside text parts.
    """

    if not request.attachments:
        return
    for content in reversed(contents):
        if content.get("role") != "user":
            continue
        parts = content.get("parts")
        if not isinstance(parts, list):
            return
        for attachment in request.attachments:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": attachment.media_type,
                        "data": attachment.data_base64,
                    }
                }
            )
        return


def envelope_to_content(
    envelope: Any,
    *,
    parse_error_class: type[ProviderHTTPError],
) -> dict[str, Any]:
    """Translate one ``AgentMessage`` into a Gemini ``contents`` entry."""

    if isinstance(envelope, AgentUserMessage):
        return {"role": "user", "parts": [{"text": envelope.content.value}]}
    if isinstance(envelope, AgentAssistantMessage):
        parts: list[dict[str, Any]] = []
        if envelope.content.value:
            parts.append({"text": envelope.content.value})
        for call in envelope.tool_calls:
            try:
                parsed_args: Any = (
                    json.loads(call.arguments_json.value)
                    if call.arguments_json.value
                    else {}
                )
            except json.JSONDecodeError:
                parsed_args = {}
            if not isinstance(parsed_args, Mapping):
                parsed_args = {}
            parts.append(
                {
                    "functionCall": {
                        "name": call.tool_name,
                        "args": dict(parsed_args),
                    }
                }
            )
        if not parts:
            parts.append({"text": ""})
        return {"role": "model", "parts": parts}
    if isinstance(envelope, AgentToolResultMessage):
        return {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": envelope.tool_name,
                        "response": {"result": envelope.content.value},
                    }
                }
            ],
        }
    raise parse_error_class(
        f"unsupported message envelope: {type(envelope).__name__}"
    )


def serialize_tool_for_gemini(tool: Any) -> dict[str, Any]:
    """Translate a ``ToolDefinition`` into the Gemini function declaration shape."""

    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": materialize_tool_input_schema(tool.input_schema),
    }


def parse_response(
    body: Mapping[str, Any],
    *,
    parse_error_class: type[ProviderHTTPError],
    response_label: str,
    usage_fields: tuple[tuple[str, str], ...],
    tool_call_provider_prefix: str,
) -> ParsedGeminiResponse:
    """Parse a Gemini ``generateContent`` success body into a result."""

    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise parse_error_class(
            f"{response_label} response did not include a candidate.",
            metadata={"provider_response_store_requested": False},
        )
    first_candidate = candidates[0]
    if not isinstance(first_candidate, Mapping):
        raise parse_error_class(
            f"{response_label} response included an unsupported candidate.",
            metadata={"provider_response_store_requested": False},
        )

    finish_reason = safe_response_label(
        first_candidate.get("finishReason"), default="unknown"
    )
    content = first_candidate.get("content")
    parts = content.get("parts") if isinstance(content, Mapping) else None

    final_text = extract_final_text(parts)
    tool_calls = extract_tool_calls(
        parts, tool_call_provider_prefix=tool_call_provider_prefix
    )

    if not final_text and not tool_calls:
        raise parse_error_class(
            f"{response_label} response did not include final output text or tool calls.",
            metadata={
                "provider_response_store_requested": False,
                "finish_reason": finish_reason,
            },
        )

    return ParsedGeminiResponse(
        final_text=final_text,
        usage=extract_usage_from_fields(body.get("usageMetadata"), usage_fields),
        finish_reason=finish_reason,
        tool_calls=tool_calls,
    )


def extract_final_text(parts: Any) -> str | None:
    """Extract the assistant final text from a Gemini ``parts`` list."""

    if not isinstance(parts, list):
        return None
    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, Mapping):
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            chunks.append(text)
    if not chunks:
        return None
    return "".join(chunks)


def extract_tool_calls(
    parts: Any,
    *,
    tool_call_provider_prefix: str,
) -> tuple[ProviderToolCall, ...]:
    """Parse Gemini ``functionCall`` parts into ProviderToolCall values."""

    if not isinstance(parts, list):
        return ()
    calls: list[ProviderToolCall] = []
    for index, part in enumerate(parts):
        if not isinstance(part, Mapping):
            continue
        function_call = part.get("functionCall")
        if not isinstance(function_call, Mapping):
            continue
        name = function_call.get("name")
        args = function_call.get("args")
        if not isinstance(name, str) or not name:
            continue
        if isinstance(args, Mapping):
            arguments_json = json.dumps(dict(args), sort_keys=True)
        elif isinstance(args, str):
            arguments_json = args
        else:
            arguments_json = "{}"
        correlation = f"{tool_call_provider_prefix}-tool-{index}"
        try:
            calls.append(
                ProviderToolCall(
                    provider_correlation_id=correlation[
                        : ProviderToolCall.PROVIDER_CORRELATION_ID_MAX_LENGTH
                    ],
                    tool_name=name[: ProviderToolCall.TOOL_NAME_MAX_LENGTH],
                    arguments_json=arguments_json[
                        : ProviderToolCall.ARGUMENTS_JSON_MAX_LENGTH
                    ],
                )
            )
        except ValueError:
            continue
    return tuple(calls)
