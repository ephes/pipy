"""Shared Anthropic Messages wire-translation helpers.

Both the first-party Anthropic Messages adapter
(``providers/anthropic_messages``) and the Amazon Bedrock InvokeModel adapter
(``providers/bedrock``) speak the identical Anthropic Messages
request/response wire shape (Bedrock wraps the same body inside its InvokeModel
envelope). This module owns the byte-identical translation in both directions:

- :func:`messages_payload` serializes canonical ``ProviderRequest`` messages into
  the Anthropic ``messages`` list (``text``/``tool_use``/``tool_result`` blocks),
  with the legacy single-turn shape.
- :func:`envelope_to_message` translates one ``AgentMessage`` envelope.
- :func:`convert_tool_result` turns one tool-result envelope into a
  ``tool_result`` block (plus any deferred ``tool_reference`` siblings).
- :func:`parse_response` / :func:`extract_final_text` /
  :func:`extract_tool_calls` turn a success body into a
  :class:`ParsedAnthropicMessagesResponse`.

The two adapters differ only where they genuinely differ, threaded through as
parameters here:

- the per-provider parse-error class (``parse_error_class``);
- the tool-call provider prefix used to synthesize a correlation id when the
  response omits one (``tool_call_provider_prefix``, e.g. ``"anthropic"`` vs
  ``"bedrock"``);
- the human-readable response label used in parse-error messages
  (``response_label``, e.g. ``"Anthropic"`` vs ``"Bedrock"``); and
- the Anthropic-only message extensions Bedrock omits: consecutive
  tool-result coalescing (``coalesce_tool_results``), deferred
  ``tool_reference`` emission (``deferred_tool_names``), and image-attachment
  blocks (``attach_images``). Bedrock passes none of them and gets the plain
  per-envelope translation.

Auth, URL/region resolution, SigV4 signing, thinking mapping, and the two
provider dataclasses and their separate error hierarchies stay in the adapter
modules.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pipy_harness.capture import sanitize_text
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.http import ProviderHTTPError, extract_anthropic_usage
from pipy_harness.native.models import ProviderRequest, ProviderToolCall


@dataclass(frozen=True, slots=True)
class ParsedAnthropicMessagesResponse:
    """Parsed Anthropic Messages body shared by the Anthropic and Bedrock adapters."""

    final_text: str | None
    usage: dict[str, int | float]
    stop_reason: str
    tool_calls: tuple[ProviderToolCall, ...] = ()


def messages_payload(
    request: ProviderRequest,
    *,
    parse_error_class: type[ProviderHTTPError],
    deferred_tool_names: frozenset[str] = frozenset(),
    attach_images: bool = False,
    coalesce_tool_results: bool = False,
) -> list[dict[str, object]]:
    """Serialize a ``ProviderRequest`` into the Anthropic ``messages`` payload.

    ``coalesce_tool_results``, ``deferred_tool_names``, and ``attach_images`` are
    the Anthropic-only message extensions: when enabled, consecutive tool
    results collapse into one user turn (carrying any deferred
    ``tool_reference`` blocks and their sibling text), and the current user turn
    gains base64 ``image`` blocks. Bedrock passes none of them and gets the
    plain per-envelope translation with no coalescing or image attachment.
    """

    if request.messages:
        if coalesce_tool_results:
            items: list[dict[str, object]] = []
            loaded_tool_names: set[str] = set()
            index = 0
            while index < len(request.messages):
                envelope = request.messages[index]
                if not isinstance(envelope, AgentToolResultMessage):
                    items.append(
                        envelope_to_message(envelope, parse_error_class=parse_error_class)
                    )
                    index += 1
                    continue
                tool_results: list[dict[str, object]] = []
                sibling_content: list[dict[str, object]] = []
                while index < len(request.messages) and isinstance(
                    request.messages[index], AgentToolResultMessage
                ):
                    tool_result = request.messages[index]
                    assert isinstance(tool_result, AgentToolResultMessage)
                    result, siblings = convert_tool_result(
                        tool_result,
                        deferred_tool_names=deferred_tool_names,
                        loaded_tool_names=loaded_tool_names,
                    )
                    tool_results.append(result)
                    sibling_content.extend(siblings)
                    index += 1
                items.append(
                    {
                        "role": "user",
                        "content": [*tool_results, *sibling_content],
                    }
                )
        else:
            items = [
                envelope_to_message(envelope, parse_error_class=parse_error_class)
                for envelope in request.messages
            ]
    else:
        items = [
            {
                "role": "user",
                "content": [{"type": "text", "text": request.user_prompt}],
            }
        ]
    if attach_images:
        _attach_images(items, request)
    return items


def _attach_images(items: list[dict[str, object]], request: ProviderRequest) -> None:
    """Append base64 image blocks to the latest user message in ``items``.

    Image attachments belong to the current user turn, so they ride on the last
    user message. Anthropic accepts ``image`` content blocks with a base64
    source alongside text blocks.
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
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": attachment.media_type,
                        "data": attachment.data_base64,
                    },
                }
            )
        return


def envelope_to_message(
    envelope: Any,
    *,
    parse_error_class: type[ProviderHTTPError],
) -> dict[str, object]:
    """Translate one ``AgentMessage`` into one Anthropic-shape message dict."""

    if isinstance(envelope, AgentUserMessage):
        return {
            "role": "user",
            "content": [{"type": "text", "text": envelope.content.value}],
        }
    if isinstance(envelope, AgentAssistantMessage):
        content: list[dict[str, object]] = []
        if envelope.content.value:
            content.append({"type": "text", "text": envelope.content.value})
        for call in envelope.tool_calls:
            try:
                parsed_input = (
                    json.loads(call.arguments_json.value)
                    if call.arguments_json.value
                    else {}
                )
            except json.JSONDecodeError:
                parsed_input = {}
            if not isinstance(parsed_input, Mapping):
                parsed_input = {}
            content.append(
                {
                    "type": "tool_use",
                    "id": call.provider_correlation_id,
                    "name": call.tool_name,
                    "input": dict(parsed_input),
                }
            )
        return {"role": "assistant", "content": content}
    if isinstance(envelope, AgentToolResultMessage):
        result, siblings = convert_tool_result(
            envelope,
            deferred_tool_names=frozenset(),
            loaded_tool_names=set(),
        )
        return {"role": "user", "content": [result, *siblings]}
    raise parse_error_class(
        f"unsupported message envelope: {type(envelope).__name__}"
    )


def convert_tool_result(
    envelope: AgentToolResultMessage,
    *,
    deferred_tool_names: frozenset[str],
    loaded_tool_names: set[str],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Convert one tool-result envelope into a ``tool_result`` block.

    Returns the block plus any sibling content blocks. When a result activates a
    deferred tool for the first time, the block carries ``tool_reference`` blocks
    and the result text moves into a sibling ``text`` block.
    """

    references: list[dict[str, object]] = []
    for name in envelope.added_tool_names:
        if name not in deferred_tool_names or name in loaded_tool_names:
            continue
        loaded_tool_names.add(name)
        references.append({"type": "tool_reference", "tool_name": name})
    block: dict[str, object] = {
        "type": "tool_result",
        "tool_use_id": envelope.provider_correlation_id,
        "content": references if references else envelope.content.value,
    }
    if envelope.is_error:
        block["is_error"] = True
    siblings: list[dict[str, object]] = (
        [{"type": "text", "text": envelope.content.value}]
        if references and envelope.content.value.strip()
        else []
    )
    return block, siblings


def parse_response(
    body: Mapping[str, Any],
    *,
    parse_error_class: type[ProviderHTTPError],
    response_label: str,
    tool_call_provider_prefix: str,
) -> ParsedAnthropicMessagesResponse:
    """Parse an Anthropic Messages success body into a result."""

    stop_reason_raw = body.get("stop_reason")
    stop_reason = (
        sanitize_text(stop_reason_raw)
        if isinstance(stop_reason_raw, str)
        else "unknown"
    )

    content = body.get("content")
    final_text = extract_final_text(content)
    tool_calls = extract_tool_calls(
        content, tool_call_provider_prefix=tool_call_provider_prefix
    )

    if not final_text and not tool_calls:
        raise parse_error_class(
            f"{response_label} response did not include final output text or tool calls.",
            metadata={"stop_reason": stop_reason},
        )

    return ParsedAnthropicMessagesResponse(
        final_text=final_text,
        usage=extract_anthropic_usage(body.get("usage")),
        stop_reason=stop_reason,
        tool_calls=tool_calls,
    )


def extract_final_text(content: Any) -> str | None:
    """Extract the assistant final text from an Anthropic ``content`` list."""

    if not isinstance(content, list):
        return None
    chunks: list[str] = []
    for item in content:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") != "text":
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            chunks.append(text)
    if not chunks:
        return None
    return "".join(chunks)


def extract_tool_calls(
    content: Any,
    *,
    tool_call_provider_prefix: str,
) -> tuple[ProviderToolCall, ...]:
    """Parse Anthropic ``content`` items of type ``tool_use``."""

    if not isinstance(content, list):
        return ()
    calls: list[ProviderToolCall] = []
    for index, item in enumerate(content):
        if not isinstance(item, Mapping):
            continue
        if item.get("type") != "tool_use":
            continue
        name = item.get("name")
        tool_input = item.get("input")
        call_id = item.get("id")
        if not isinstance(name, str) or not name:
            continue
        if isinstance(tool_input, Mapping):
            arguments_json = json.dumps(dict(tool_input), sort_keys=True)
        else:
            arguments_json = "{}"
        correlation: str
        if isinstance(call_id, str) and call_id:
            correlation = call_id
        else:
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
