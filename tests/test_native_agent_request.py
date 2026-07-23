"""Focused characterization for canonical provider-request validation."""

from __future__ import annotations

import pytest

from pipy_harness.native.agent.request import _validate_schema_semantics


@pytest.mark.parametrize(
    ("schema", "error_match"),
    [
        (
            {
                "type": "object",
                "minimum": True,
                "required": (1,),
                "properties": {"first": {"type": 1}},
            },
            "tool schema minimum must be an exact integer",
        ),
        (
            {
                "type": "object",
                "required": (1,),
                "properties": {"first": {"type": 1}},
            },
            "tool schema required must contain exact strings",
        ),
        (
            {
                "type": "object",
                "properties": {
                    "first": {"type": "string", "description": 1},
                    "second": {"type": 1},
                },
                "items": {"type": 1},
            },
            "tool schema description must be an exact string",
        ),
    ],
)
def test_schema_semantic_errors_remain_depth_first_in_keyword_order(
    schema: dict[str, object],
    error_match: str,
) -> None:
    with pytest.raises(TypeError, match=error_match):
        _validate_schema_semantics(schema)
