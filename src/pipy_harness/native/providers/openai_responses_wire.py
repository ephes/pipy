"""Shared OpenAI Responses wire-translation helpers.

Both the first-party OpenAI Responses adapter (``providers/openai_responses``)
and the Azure OpenAI Responses adapter (``providers/azure_openai_responses``)
speak the identical Responses request/response wire shape. This module owns the
byte-identical translation in both directions:

- :func:`resolve_responses_transcript` and :func:`responses_input` serialize
  canonical ``ProviderRequest`` messages and system messages into the
  Responses ``input`` list (the leading prompt, ``function_call`` /
  ``function_call_output`` items, developer messages, anchored tool loads).
  Codex reuses the transcript items with its own envelope conversion and
  sends the leading prompt as ``instructions``.
- :class:`ResponsesReplay` translates one ``AgentMessage`` envelope as Pi's
  ``convertResponsesMessages`` does for the target model: stored reasoning
  items, message items with their ids and phase, ``function_call`` ids.
- :func:`parse_response` turns a Responses response body into a
  :class:`ParsedResponse`, with Pi's ordered content
  (``providers/responses_output.py``).

The two adapters differ only where they genuinely differ, threaded through as
parameters here:

- the OpenAI-only image-attachment extension (``attach_images``);
- the per-provider parse-error class (``parse_error_class``);
- the human-readable response label used in parse-error messages
  (``response_label``, e.g. ``"OpenAI"`` vs ``"Azure OpenAI"``);
- the nested-usage detail-field tuple (``nested_usage_fields``);
- the tool-call provider prefix (``tool_call_provider_prefix``); and
- the replay target: the adapter's API and the providers whose compound
  tool-call ids it accepts (Pi's ``*_TOOL_CALL_PROVIDERS``).

Auth, base-URL/deployment resolution, and the two provider dataclasses and their
error hierarchies stay in the adapter modules.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pipy_harness.capture import sanitize_text
from pipy_harness.native._provider_helpers import serialize_tool_for_responses
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentSystemMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.content import TextContent, ThinkingContent
from pipy_harness.native.agent.system_messages import render_system_message_update
from pipy_harness.native.deferred_tools import short_hash
from pipy_harness.native.http import ProviderHTTPError, extract_responses_usage
from pipy_harness.native.models import (
    ProviderContentBlock,
    ProviderRequest,
    ProviderToolCall,
)
from pipy_harness.native.providers.replay_content import (
    ReplayTarget,
    transform_assistant_blocks,
)
from pipy_harness.native.providers.responses_output import (
    output_blocks,
    parse_text_signature,
)
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
    content_blocks: tuple[ProviderContentBlock, ...] = ()


# Pi ``OPENAI_TOOL_CALL_PROVIDERS`` / ``CODEX_TOOL_CALL_PROVIDERS`` and
# ``AZURE_TOOL_CALL_PROVIDERS``: targets that accept another provider's
# compound ``call_id|item_id`` tool-call ids with a hashed ``fc_`` item id.
OPENAI_TOOL_CALL_PROVIDERS = frozenset({"openai", "openai-codex", "opencode"})
AZURE_TOOL_CALL_PROVIDERS = OPENAI_TOOL_CALL_PROVIDERS | {"azure-openai-responses"}


@dataclass(frozen=True, slots=True)
class ResponsesReplay:
    """Pi ``convertResponsesMessages`` for one target model's history.

    ``target`` is the model the request goes to; ``tool_call_providers`` the
    providers whose compound tool-call ids it accepts. Tool-call ``call_id``
    values use pipy's portable projection of the call part (D8a), which keeps
    Pi's safe ids byte for byte; the item ``id`` follows Pi.
    """

    target: ReplayTarget
    tool_call_providers: frozenset[str]
    parse_error_class: type[ProviderHTTPError] = ProviderHTTPError

    def items(self, envelope: Any, msg_index: int) -> list[dict[str, object]]:
        """Translate one ``AgentMessage`` into Responses input items."""

        if isinstance(envelope, AgentUserMessage):
            return [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": envelope.content.value}],
                }
            ]
        if isinstance(envelope, AgentAssistantMessage):
            return self._assistant_items(envelope, msg_index)
        if isinstance(envelope, AgentToolResultMessage):
            return [
                {
                    "type": "function_call_output",
                    "call_id": _call_id(envelope.provider_correlation_id),
                    "output": envelope.content.value,
                }
            ]
        raise self.parse_error_class(
            f"unsupported message envelope: {type(envelope).__name__}"
        )

    def _assistant_items(
        self, message: AgentAssistantMessage, msg_index: int
    ) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        text_index = 0
        for block in transform_assistant_blocks(message, self.target):
            if isinstance(block, ThinkingContent):
                # Only a signed block (the stored reasoning item) is sent.
                reasoning = _reasoning_item(block.signature)
                if reasoning is not None:
                    items.append(reasoning)
            elif isinstance(block, TextContent):
                items.append(_message_item(block, msg_index, text_index))
                text_index += 1
            else:
                items.append(self._function_call(block, message))
        return items

    def _function_call(
        self, call: AgentToolCall, source: AgentAssistantMessage
    ) -> dict[str, object]:
        item: dict[str, object] = {
            "type": "function_call",
            "call_id": _call_id(call.provider_correlation_id),
            "name": call.tool_name,
            "arguments": call.arguments_json.value,
        }
        item_id = self._item_id(call.provider_correlation_id, source)
        if item_id is not None:
            item["id"] = item_id
        return item

    def _item_id(self, correlation: str, source: AgentAssistantMessage) -> str | None:
        """Pi's ``function_call.id``: kept for the same model, hashed for a
        foreign provider or API on an accepting target, dropped otherwise and
        whenever it is not an ``fc_`` id."""

        _, sep, item_id = correlation.partition("|")
        if not sep or not item_id:
            return None
        target = self.target
        if not target.produced(source):
            foreign = source.provider != target.provider or source.api != target.api
            if not foreign or target.provider not in self.tool_call_providers:
                return None
            item_id = f"fc_{short_hash(item_id)}"[:64]
        return item_id if item_id.startswith("fc_") else None


def _call_id(correlation: str) -> str:
    return portable_tool_correlation_id(correlation.partition("|")[0])


def _reasoning_item(signature: str | None) -> dict[str, object] | None:
    if not signature:
        return None
    try:
        item = json.loads(signature)
    except json.JSONDecodeError:
        return None
    return item if isinstance(item, dict) else None


def _message_item(
    block: TextContent, msg_index: int, text_index: int
) -> dict[str, object]:
    """Pi's replayed assistant message item (id from ``textSignature``)."""

    parsed = parse_text_signature(block.signature)
    message_id = parsed[0] if parsed is not None else ""
    if not message_id:
        message_id = (
            f"msg_pi_{msg_index}"
            if text_index == 0
            else f"msg_pi_{msg_index}_{text_index}"
        )
    elif len(message_id) > 64:
        message_id = f"msg_{short_hash(message_id)}"
    item: dict[str, object] = {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": block.text, "annotations": []}],
        "status": "completed",
        "id": message_id,
    }
    if parsed is not None and parsed[1] is not None:
        item["phase"] = parsed[1]
    return item


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
    """The leading prompt, top-level tools and the ``input`` builder inputs."""

    leading_prompt: str
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
    replay: ResponsesReplay,
    include_leading_prompt: bool,
) -> list[dict[str, object]]:
    """Pi ``convertResponsesMessages`` over the resolved transcript.

    With ``include_leading_prompt`` (Pi ``includeSystemPrompt``, off for
    Codex, which sends ``instructions``) a non-empty leading prompt is the
    first item, in the instruction role. A later system message first loads
    the tools it adds (when additions are anchored), then, when its rendered
    update is not empty, becomes an instruction-role message. ``msgIndex``
    (the tool-search seed) counts every converted message except an
    assistant turn that produced no items.
    """

    items: list[dict[str, object]] = []
    if include_leading_prompt and transcript.leading_prompt:
        items.append({"role": instruction_role, "content": transcript.leading_prompt})
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
        converted = replay.items(item, msg_index)
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
    api: str = "openai-responses",
    tool_call_providers: frozenset[str] = OPENAI_TOOL_CALL_PROVIDERS,
) -> list[dict[str, object]]:
    """Serialize a ``ProviderRequest`` into the Responses ``input`` payload.

    Pi ``convertResponsesMessages`` with ``includeSystemPrompt``: the
    leading prompt is the first item (OpenAI and Azure send no
    ``instructions``). ``transcript`` carries the resolved system messages
    (:func:`resolve_responses_transcript`); without it the request collapses.
    A request without messages sends its ``user_prompt`` as one user item.
    ``attach_images`` is the OpenAI-only extension: the current user turn
    gains ``input_image`` blocks. Azure passes no images.
    """

    if transcript is None:
        transcript = resolve_responses_transcript(request, ResponsesTranscriptOptions())
    items = responses_transcript_items(
        transcript,
        instruction_role=instruction_role,
        replay=ResponsesReplay(
            ReplayTarget.of(request, api), tool_call_providers, parse_error_class
        ),
        include_leading_prompt=True,
    )
    if not request.messages:
        items.append(
            {
                "role": "user",
                "content": [{"type": "input_text", "text": request.user_prompt}],
            }
        )
    if attach_images:
        _attach_images(items, request)
    return items


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

    blocks = output_blocks(
        body.get("output"),
        provider_prefix=tool_call_provider_prefix,
        output_text=body.get("output_text"),
    )
    final_text = "".join(b.text for b in blocks if isinstance(b, TextContent)) or None
    tool_calls = tuple(b for b in blocks if isinstance(b, ProviderToolCall))
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
        content_blocks=blocks,
    )
