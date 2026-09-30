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
- :func:`parse_response` / :func:`extract_tool_calls` turn a success body into
  a :class:`ParsedGeminiResponse` with Pi's ordered content (thought parts
  are thinking, never answer text);
  :func:`extract_gemini_usage` normalizes ``usageMetadata`` as Pi does.

The two adapters differ only where they genuinely differ, threaded through as
parameters here:

- the per-provider parse-error class (``parse_error_class``);
- the human-readable response label used in parse-error messages
  (``response_label``, e.g. ``"Google"`` vs ``"Google Vertex AI"``);
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
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from pipy_harness.native._provider_helpers import safe_response_label
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.content import TextContent, ThinkingContent
from pipy_harness.native.http import ProviderHTTPError
from pipy_harness.native.models import (
    ProviderContentBlock,
    ProviderRequest,
    ProviderToolCall,
)
from pipy_harness.native.providers.replay_content import (
    ReplayTarget,
    transform_assistant_blocks,
)
from pipy_harness.native.tool_call_ids import portable_tool_correlation_id
from pipy_harness.native.tools.base import materialize_tool_input_schema
from pipy_harness.native.usage import normalize_provider_usage


@dataclass(frozen=True, slots=True)
class ParsedGeminiResponse:
    """Parsed Gemini ``generateContent`` body shared by the two adapters."""

    final_text: str | None
    usage: dict[str, int | float]
    finish_reason: str
    tool_calls: tuple[ProviderToolCall, ...] = ()
    # Pi's ordered content: thought parts as thinking, text and function
    # calls with their ``thoughtSignature``.
    content_blocks: tuple[ProviderContentBlock, ...] = ()


def gemini_contents(
    request: ProviderRequest,
    *,
    parse_error_class: type[ProviderHTTPError],
    attach_images: bool = False,
    target: ReplayTarget | None = None,
) -> list[dict[str, Any]]:
    """Build Gemini ``contents`` from a ``ProviderRequest``.

    ``target`` is the model the request goes to: only its own thought
    signatures are sent back (Pi ``google-shared.ts`` ``convertMessages``).

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
                envelope_to_content(
                    envelope, parse_error_class=parse_error_class, target=target
                )
            )
        if attach_images:
            _attach_images(contents, request)
        return contents
    contents.append({"role": "user", "parts": [{"text": request.user_prompt}]})
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
    target: ReplayTarget | None = None,
) -> dict[str, Any]:
    """Translate one ``AgentMessage`` into a Gemini ``contents`` entry."""

    if isinstance(envelope, AgentUserMessage):
        return {"role": "user", "parts": [{"text": envelope.content.value}]}
    if isinstance(envelope, AgentAssistantMessage):
        parts = _model_parts(envelope, target)
        if not parts:
            parts.append({"text": ""})
        return {"role": "model", "parts": parts}
    if isinstance(envelope, AgentToolResultMessage):
        return {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "id": portable_tool_correlation_id(
                            envelope.provider_correlation_id
                        ),
                        "name": envelope.tool_name,
                        "response": {"result": envelope.content.value},
                    }
                }
            ],
        }
    raise parse_error_class(f"unsupported message envelope: {type(envelope).__name__}")


# Pi ``isValidThoughtSignature``: Google APIs take base64 (``TYPE_BYTES``).
_BASE64_SIGNATURE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


def _thought_signature(same_model: bool, signature: str | None) -> str | None:
    """Pi ``resolveThoughtSignature``: the same model's valid base64 only."""

    if not same_model or not signature or len(signature) % 4:
        return None
    return signature if _BASE64_SIGNATURE.fullmatch(signature) else None


def _model_parts(
    message: AgentAssistantMessage, target: ReplayTarget | None
) -> list[dict[str, Any]]:
    """Pi ``convertMessages`` for one assistant message.

    A signature is only replayed to the provider and model that made it (Pi
    compares provider and model id here). Blank text and thinking parts are
    dropped unless they carry a signature; another model's thinking has
    already become text (the transform).
    """

    same = target is not None and (
        message.provider == target.provider and message.model == target.model
    )
    parts: list[dict[str, Any]] = []
    for block in transform_assistant_blocks(message, target):
        if isinstance(block, TextContent):
            signature = _thought_signature(same, block.signature)
            if not block.text.strip() and signature is None:
                continue
            parts.append(_with_signature({"text": block.text}, signature))
        elif isinstance(block, ThinkingContent):
            if same:
                signature = _thought_signature(same, block.signature)
                if not block.thinking.strip() and signature is None:
                    continue
                part = {"thought": True, "text": block.thinking}
                parts.append(_with_signature(part, signature))
            elif block.thinking.strip():
                parts.append({"text": block.thinking})
        else:
            signature = _thought_signature(same, block.thought_signature)
            parts.append(_with_signature(_function_call_part(block), signature))
    return parts


def _with_signature(part: dict[str, Any], signature: str | None) -> dict[str, Any]:
    if signature is not None:
        part["thoughtSignature"] = signature
    return part


def _function_call_part(call: AgentToolCall) -> dict[str, Any]:
    try:
        parsed_args: Any = (
            json.loads(call.arguments_json.value) if call.arguments_json.value else {}
        )
    except json.JSONDecodeError:
        parsed_args = {}
    if not isinstance(parsed_args, Mapping):
        parsed_args = {}
    return {
        "functionCall": {
            "id": portable_tool_correlation_id(call.provider_correlation_id),
            "name": call.tool_name,
            "args": dict(parsed_args),
        }
    }


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

    tool_calls = extract_tool_calls(
        parts, tool_call_provider_prefix=tool_call_provider_prefix
    )
    blocks = _content_blocks(parts, tool_calls)
    final_text = "".join(b.text for b in blocks if isinstance(b, TextContent)) or None

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
        usage=extract_gemini_usage(body.get("usageMetadata")),
        finish_reason=finish_reason,
        tool_calls=_blocks_tool_calls(blocks),
        content_blocks=blocks,
    )


def _blocks_tool_calls(
    blocks: tuple[ProviderContentBlock, ...],
) -> tuple[ProviderToolCall, ...]:
    return tuple(b for b in blocks if isinstance(b, ProviderToolCall))


def _content_blocks(
    parts: Any, tool_calls: tuple[ProviderToolCall, ...]
) -> tuple[ProviderContentBlock, ...]:
    """Pi's stream assembly over one candidate's parts.

    Consecutive text parts of the same kind (``thought: true`` or not) merge
    into one block that keeps the last non-empty ``thoughtSignature``
    (``retainThoughtSignature``); a function call closes the current block and
    carries its part's signature.
    """

    if not isinstance(parts, list):
        return tuple(tool_calls)
    calls = iter(tool_calls)
    blocks: list[ProviderContentBlock] = []
    current: list[Any] | None = None  # [is_thinking, text, signature]

    def close() -> None:
        nonlocal current
        if current is not None:
            thinking, text, signature = current
            blocks.append(
                ThinkingContent(text, signature)
                if thinking
                else TextContent(text, signature)
            )
        current = None

    for part in parts:
        if not isinstance(part, Mapping):
            continue
        signature = part.get("thoughtSignature")
        signature = signature if isinstance(signature, str) and signature else None
        text = part.get("text")
        if isinstance(text, str):
            thinking = part.get("thought") is True
            if current is None or current[0] is not thinking:
                close()
                current = [thinking, "", None]
            current[1] += text
            current[2] = signature or current[2]
        if isinstance(part.get("functionCall"), Mapping) and _is_named_call(part):
            close()
            call = next(calls, None)
            if call is not None:
                blocks.append(replace(call, thought_signature=signature))
    close()
    blocks.extend(calls)
    return tuple(blocks)


def _is_named_call(part: Mapping[str, Any]) -> bool:
    name = part["functionCall"].get("name")
    return isinstance(name, str) and bool(name)


def extract_gemini_usage(value: Any) -> dict[str, int | float]:
    """Normalize ``usageMetadata`` the way Pi's Google adapters count it.

    Pi (``google-generative-ai.ts`` / ``google-vertex.ts``): output is
    ``candidatesTokenCount + thoughtsTokenCount``, reasoning is the thoughts,
    and ``cachedContentTokenCount`` is a cache read inside ``promptTokenCount``
    (the accumulator subtracts it to price uncached input).
    """

    if not isinstance(value, Mapping):
        return {}
    candidates = _usage_count(value.get("candidatesTokenCount"))
    thoughts = _usage_count(value.get("thoughtsTokenCount"))
    usage: dict[str, Any] = {
        "input_tokens": value.get("promptTokenCount"),
        "total_tokens": value.get("totalTokenCount"),
        "cached_tokens": value.get("cachedContentTokenCount"),
        "reasoning_tokens": value.get("thoughtsTokenCount"),
    }
    if candidates is not None or thoughts is not None:
        usage["output_tokens"] = (candidates or 0) + (thoughts or 0)
    return normalize_provider_usage(usage)


def _usage_count(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


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
        returned_id = function_call.get("id")
        correlation = (
            returned_id
            if isinstance(returned_id, str) and returned_id
            else f"{tool_call_provider_prefix}-tool-{index}"
        )
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
