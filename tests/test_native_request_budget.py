"""Pure numeric request accounting and declared/explicit budget resolution."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.request import freeze_provider_request
from pipy_harness.native.coding.request_budget import (
    RequestBudget,
    estimate_request,
    resolve_request_budget,
)
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.tools.base import ToolDefinition


def _request(**kwargs: Any) -> ProviderRequest:
    return freeze_provider_request(
        ProviderRequest(
            system_prompt=kwargs.pop("system_prompt", ""),
            user_prompt=kwargs.pop("user_prompt", ""),
            provider_name="injected",
            model_id="unknown",
            cwd=Path("/unused"),
            **kwargs,
        )
    )


def test_estimate_includes_each_native_request_component() -> None:
    request = _request(
        system_prompt="abc",
        user_prompt="not counted a second time",
        messages=(
            AgentUserMessage(ProductContent("abcd")),
            AgentAssistantMessage(
                ProductContent("é"),
                (AgentToolCall("call", "read", ProductContent("{}")),),
            ),
            AgentToolResultMessage(
                "pipy-tool-1",
                "read",
                ProductContent("done"),
                "call",
                added_tool_names=("next",),
            ),
        ),
        available_tools=(ToolDefinition("read", "files", {"type": "object"}),),
    )
    estimate = estimate_request(request, image_count=1, output_reserve=32)
    assert asdict(estimate) == {
        "system_tokens": 1,
        "message_tokens": 3,
        "tool_tokens": 10,
        "tool_call_tokens": 5,
        "tool_result_tokens": 14,
        "image_tokens": 4096,
        "framing_tokens": 80,
        "safety_tokens": 1053,
        "output_reserve": 32,
    }
    assert estimate.input_tokens == 5262
    assert estimate.total_tokens == 5294


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", 0), ("a", 1), ("abc", 1), ("abcd", 2), ("é", 1), ("😀", 2), ("a\nb\n", 2)],
)
def test_utf8_text_rounding_and_empty_request_framing(text: str, expected: int) -> None:
    estimate = estimate_request(
        _request(user_prompt=text), image_count=0, output_reserve=0
    )
    assert estimate.message_tokens == expected
    assert estimate.framing_tokens == 40
    assert estimate.safety_tokens >= 10
    assert estimate.total_tokens == estimate.input_tokens


def test_effective_messages_count_overlay_once_without_mutating_history() -> None:
    history = (AgentUserMessage(ProductContent("real")),)
    overlay = AgentUserMessage(ProductContent("PRIVATE_OVERLAY"))
    baseline = _request(messages=history, user_prompt="real")
    expanded = replace(
        baseline, messages=(*history, overlay), user_prompt="IGNORE" * 100
    )
    estimate = estimate_request(expanded, image_count=0, output_reserve=12)
    assert estimate.message_tokens == 7
    assert estimate.framing_tokens == 48
    assert history == baseline.messages and len(history) == 1
    assert (
        estimate.input_tokens
        > estimate_request(baseline, image_count=0, output_reserve=12).input_tokens
    )
    assert "PRIVATE" not in repr(estimate) and "IGNORE" not in repr(estimate)
    assert all(type(value) is int for value in asdict(estimate).values())
    with pytest.raises(FrozenInstanceError):
        setattr(estimate, "message_tokens", 0)


def test_nested_schema_literals_and_unicode_are_counted_as_json() -> None:
    plain = _request(available_tools=(ToolDefinition("x", "y", {"type": "object"}),))
    nested = _request(
        available_tools=(
            ToolDefinition(
                "x",
                "y",
                {
                    "type": "object",
                    "properties": {
                        "é": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["a\nb", "😀"]},
                        }
                    },
                    "required": ["é"],
                    "additionalProperties": False,
                },
            ),
        )
    )
    first = estimate_request(plain, image_count=0, output_reserve=0)
    second = estimate_request(nested, image_count=0, output_reserve=0)
    assert first.tool_tokens == 8
    assert second.tool_tokens > first.tool_tokens + 30
    assert second.safety_tokens > first.safety_tokens
    assert second.message_tokens == first.message_tokens
    assert second.framing_tokens == first.framing_tokens
    with pytest.raises(TypeError, match="immutable"):
        estimate_request(
            replace(
                plain, available_tools=(ToolDefinition("x", "y", {"type": "object"}),)
            ),
            image_count=0,
            output_reserve=0,
        )


def test_images_use_only_extracted_count_and_header_callback_is_not_invoked() -> None:
    class UnreadableImage:
        def __getattribute__(self, name: str) -> Any:
            raise AssertionError("image contents must not be inspected")

    def forbidden(*_args: object) -> None:
        raise AssertionError("header callback must not be invoked")

    request = replace(
        _request(),
        attachments=(UnreadableImage(),),  # type: ignore[arg-type]
        provider_header_callback=forbidden,
    )
    empty = estimate_request(request, image_count=0, output_reserve=10)
    images = estimate_request(request, image_count=2, output_reserve=10)
    assert images.image_tokens == 8192
    assert images.framing_tokens == empty.framing_tokens + 16
    assert images.safety_tokens > empty.safety_tokens
    assert images.system_tokens == empty.system_tokens == 0


@pytest.mark.parametrize(
    ("declared", "ceiling", "resolved"),
    [
        (100, None, 100),
        (None, 80, 80),
        (100, 80, 80),
        (80, 100, 80),
        (80, 80, 80),
        (None, None, None),
    ],
)
def test_resolve_only_declared_or_explicit_limits(
    declared: int | None, ceiling: int | None, resolved: int | None
) -> None:
    budget = resolve_request_budget(
        declared_context_window=declared, explicit_ceiling=ceiling, output_reserve=20
    )
    assert budget.context_window == resolved
    assert budget.input_allowance == (None if resolved is None else resolved - 20)
    with pytest.raises(FrozenInstanceError):
        setattr(budget, "context_window", 1)


def test_inclusive_threshold_unknown_verdict_and_reserve_agreement() -> None:
    estimate = estimate_request(_request(), image_count=0, output_reserve=20)
    assert estimate.total_tokens == 70
    assert RequestBudget(70, 20).allows(estimate) is True
    assert RequestBudget(69, 20).allows(estimate) is False
    assert RequestBudget(None, 20).allows(estimate) is None
    with pytest.raises(ValueError, match="same output reserve"):
        RequestBudget(70, 19).allows(estimate)


@pytest.mark.parametrize("bad", [True, False, -1, 0, 1.5, "100"])
@pytest.mark.parametrize("field", ["declared_context_window", "explicit_ceiling"])
def test_invalid_limit_is_not_hidden_by_other_limit(bad: Any, field: str) -> None:
    values = {"declared_context_window": 100, "explicit_ceiling": 50}
    values[field] = bad
    with pytest.raises((TypeError, ValueError)):
        resolve_request_budget(**values, output_reserve=1)


@pytest.mark.parametrize("bad", [True, False, -1, 1.5, "10"])
def test_invalid_reserve_and_image_counts(bad: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        RequestBudget(None, bad)
    with pytest.raises((TypeError, ValueError)):
        estimate_request(_request(), image_count=bad, output_reserve=0)
    with pytest.raises((TypeError, ValueError)):
        estimate_request(_request(), image_count=0, output_reserve=bad)
    with pytest.raises((TypeError, ValueError)):
        replace(
            estimate_request(_request(), image_count=0, output_reserve=0),
            tool_tokens=bad,
        )


@pytest.mark.parametrize("reserve", [50, 51, 100])
def test_reserve_without_input_allowance_is_not_clamped(reserve: int) -> None:
    with pytest.raises(ValueError, match="output reserve must be smaller"):
        resolve_request_budget(
            declared_context_window=100, explicit_ceiling=50, output_reserve=reserve
        )


def test_large_integer_limits_and_counts_do_not_round_through_floats() -> None:
    count = 10**100
    estimate = estimate_request(_request(), image_count=count, output_reserve=count)
    assert estimate.image_tokens == 4096 * count
    budget = resolve_request_budget(
        declared_context_window=estimate.total_tokens,
        explicit_ceiling=None,
        output_reserve=count,
    )
    assert budget.allows(estimate) is True
    assert RequestBudget(estimate.total_tokens - 1, count).allows(estimate) is False
