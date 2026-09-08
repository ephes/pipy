"""Pure estimates of native request components, not provider-exact token counts.

Text uses ceil(UTF-8 bytes / 3), rounded separately per field. Schemas use
compact JSON with literal Unicode. Framing adds 32 tokens per request and 8 per
message, tool definition, tool call and image. Each image adds 4096 tokens,
independent of compressed bytes. A further 25% safety allowance covers the whole
input estimate. These deliberately cautious heuristics still cannot guarantee
fit for every tokenizer, image resolution or adapter-specific wire expansion.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace

from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentToolResultMessage,
)
from pipy_harness.native.agent.request import validate_frozen_provider_request
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.tools.base import materialize_tool_input_schema


@dataclass(frozen=True, slots=True)
class RequestEstimate:
    """Numeric accounting only: no request, prompt, schema or attachment retained."""

    system_tokens: int
    message_tokens: int
    tool_tokens: int
    tool_call_tokens: int
    tool_result_tokens: int
    image_tokens: int
    framing_tokens: int
    safety_tokens: int
    output_reserve: int

    def __post_init__(self) -> None:
        for item in fields(self):
            _nonnegative_integer(getattr(self, item.name), item.name)

    @property
    def input_tokens(self) -> int:
        return sum(
            getattr(self, item.name)
            for item in fields(self)
            if item.name != "output_reserve"
        )

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_reserve


@dataclass(frozen=True, slots=True)
class RequestBudget:
    """Resolved policy allowance; unknown context has no model-fit verdict."""

    context_window: int | None
    output_reserve: int

    def __post_init__(self) -> None:
        _optional_positive_integer(self.context_window, "context window")
        _nonnegative_integer(self.output_reserve, "output reserve")
        if (
            self.context_window is not None
            and self.output_reserve >= self.context_window
        ):
            raise ValueError("output reserve must be smaller than the context window")

    @property
    def input_allowance(self) -> int | None:
        if self.context_window is None:
            return None
        return self.context_window - self.output_reserve

    def allows(self, estimate: RequestEstimate) -> bool | None:
        """Compare estimates inclusively, without implying provider acceptance."""

        if type(estimate) is not RequestEstimate:
            raise TypeError("estimate must be RequestEstimate")
        if estimate.output_reserve != self.output_reserve:
            raise ValueError("estimate and budget must use the same output reserve")
        if self.context_window is None:
            return None
        return estimate.total_tokens <= self.context_window


def resolve_request_budget(
    *,
    declared_context_window: int | None,
    explicit_ceiling: int | None,
    output_reserve: int,
) -> RequestBudget:
    """Resolve supplied declarations/policy only, without catalog/settings reads."""

    _optional_positive_integer(declared_context_window, "declared context window")
    _optional_positive_integer(explicit_ceiling, "explicit context ceiling")
    limits = tuple(
        value
        for value in (declared_context_window, explicit_ceiling)
        if value is not None
    )
    return RequestBudget(min(limits) if limits else None, output_reserve)


def estimate_request(
    request: ProviderRequest, *, image_count: int, output_reserve: int
) -> RequestEstimate:
    """Measure supplied frozen request values plus an extracted image count.

    The caller supplies the count for this request's images. Attachment objects,
    raw image data and header callbacks are neither inspected nor invoked here.
    Tool schemas must already be frozen by the canonical request boundary; a
    callback-free pre-hook snapshot can use that same boundary before measuring.
    """

    if type(request) is not ProviderRequest:
        raise TypeError("request must be ProviderRequest")
    _nonnegative_integer(image_count, "image count")
    _nonnegative_integer(output_reserve, "output reserve")
    validate_frozen_provider_request(
        replace(request, attachments=(), provider_header_callback=None)
    )
    message_tokens = tool_call_tokens = tool_result_tokens = call_count = 0
    for message in request.messages:
        if isinstance(message, AgentToolResultMessage):
            tool_result_tokens += sum(
                _text_tokens(value)
                for value in (
                    message.content.value,
                    message.tool_name,
                    message.tool_request_id,
                    message.provider_correlation_id,
                    str(message.is_error).lower(),
                    *message.added_tool_names,
                )
            )
        else:
            message_tokens += _text_tokens(message.content.value)
        if isinstance(message, AgentAssistantMessage):
            call_count += len(message.tool_calls)
            for call in message.tool_calls:
                tool_call_tokens += sum(
                    _text_tokens(value)
                    for value in (
                        call.tool_name,
                        call.provider_correlation_id,
                        call.arguments_json.value,
                    )
                )
    if not request.messages:
        message_tokens = _text_tokens(request.user_prompt)
    tool_tokens = sum(
        _text_tokens(tool.name)
        + _text_tokens(tool.description)
        + _text_tokens(
            json.dumps(
                materialize_tool_input_schema(tool.input_schema),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        for tool in request.available_tools
    )
    system_tokens = _text_tokens(request.system_prompt)
    image_tokens = 4096 * image_count
    framing_tokens = 32 + 8 * (
        max(1, len(request.messages))
        + len(request.available_tools)
        + call_count
        + image_count
    )
    subtotal = (
        system_tokens
        + message_tokens
        + tool_tokens
        + tool_call_tokens
        + tool_result_tokens
        + image_tokens
        + framing_tokens
    )
    return RequestEstimate(
        system_tokens,
        message_tokens,
        tool_tokens,
        tool_call_tokens,
        tool_result_tokens,
        image_tokens,
        framing_tokens,
        (subtotal + 3) // 4,
        output_reserve,
    )


def _text_tokens(text: str) -> int:
    return (len(text.encode("utf-8")) + 2) // 3


def _nonnegative_integer(value: int, label: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{label} must be an integer")
    if value < 0:
        raise ValueError(f"{label} must be nonnegative")


def _optional_positive_integer(value: int | None, label: str) -> None:
    if value is not None:
        _nonnegative_integer(value, label)
        if value == 0:
            raise ValueError(f"{label} must be positive")
