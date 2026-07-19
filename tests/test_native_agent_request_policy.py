"""Contracts for frozen, request-local provider authorization snapshots."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest

from pipy_harness.native.agent import AgentUserMessage, ProductContent
from pipy_harness.native.agent.request import (
    AgentProviderRequestSnapshot,
    snapshot_provider_request,
)
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.tools import ToolDefinition


def _definition(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Fixture {name} tool.",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    )


def _request(
    tmp_path: Path,
    *,
    tool_names: tuple[str, ...] = ("first", "second", "third"),
) -> ProviderRequest:
    return ProviderRequest(
        system_prompt="system",
        user_prompt="user",
        provider_name="fake",
        model_id="fake-model",
        cwd=tmp_path,
        messages=(AgentUserMessage(ProductContent("user")),),
        available_tools=tuple(_definition(name) for name in tool_names),
    )


def test_snapshot_is_frozen_and_authorizes_only_its_exact_request(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    snapshot = snapshot_provider_request(
        request,
        available_tool_names=("third", "first", "third", "unknown"),
    )

    assert snapshot.request is not request
    assert snapshot.advertised_tool_names == ("first", "third")
    assert tuple(tool.name for tool in snapshot.request.available_tools) == (
        "first",
        "third",
    )
    assert snapshot.authorizes("first")
    assert snapshot.authorizes("third")
    assert not snapshot.authorizes("second")
    assert not snapshot.authorizes("unknown")
    with pytest.raises(FrozenInstanceError):
        setattr(snapshot, "advertised_tool_names", ("second",))


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (None, ("first", "second", "third")),
        ((), ()),
        (("third", "first"), ("first", "third")),
        (("second", "second", "missing", ""), ("second",)),
    ],
)
def test_snapshot_tool_selection_is_a_monotonic_intersection_in_prior_order(
    tmp_path: Path,
    requested: tuple[str, ...] | None,
    expected: tuple[str, ...],
) -> None:
    snapshot = snapshot_provider_request(
        _request(tmp_path),
        available_tool_names=requested,
    )

    assert snapshot.advertised_tool_names == expected


def test_successive_snapshots_cannot_reenable_a_removed_tool(tmp_path: Path) -> None:
    first = snapshot_provider_request(
        _request(tmp_path),
        available_tool_names=("first", "third"),
    )
    second = snapshot_provider_request(
        first.request,
        available_tool_names=("second", "third", "first"),
    )

    assert second.advertised_tool_names == ("first", "third")
    assert not second.authorizes("second")


def test_prompt_transform_uses_exact_caller_supplied_messages(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    older = AgentUserMessage(ProductContent("user"))
    request = ProviderRequest(
        system_prompt=request.system_prompt,
        user_prompt=request.user_prompt,
        provider_name=request.provider_name,
        model_id=request.model_id,
        cwd=request.cwd,
        messages=(older, AgentUserMessage(ProductContent("other")), *request.messages),
        available_tools=request.available_tools,
    )

    snapshot = snapshot_provider_request(
        request,
        system_prompt="transformed system",
        user_prompt="transformed user",
        messages=(
            *request.messages[:-1],
            AgentUserMessage(ProductContent("transformed user")),
        ),
    )

    assert snapshot.request.system_prompt == "transformed system"
    assert snapshot.request.user_prompt == "transformed user"
    assert [message.content.value for message in snapshot.request.messages] == [
        "user",
        "other",
        "transformed user",
    ]
    assert request.messages[-1].content.value == "user"


def test_prompt_transform_requires_caller_supplied_exact_messages(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)

    with pytest.raises(ValueError, match="messages are required"):
        snapshot_provider_request(request, user_prompt="transformed user")

    assert request.user_prompt == "user"
    assert request.messages == (AgentUserMessage(ProductContent("user")),)


def test_each_provider_iteration_freezes_a_fresh_authorization_policy(
    tmp_path: Path,
) -> None:
    base = _request(tmp_path)
    first = snapshot_provider_request(base, available_tool_names=("first",))
    second = snapshot_provider_request(base, available_tool_names=("second",))

    assert first.advertised_tool_names == ("first",)
    assert first.authorizes("first")
    assert not first.authorizes("second")
    assert second.advertised_tool_names == ("second",)
    assert not second.authorizes("first")
    assert second.authorizes("second")


def test_snapshot_runtime_validation_rejects_mutable_or_inconsistent_state(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path, tool_names=("first",))

    with pytest.raises(TypeError, match="request must be ProviderRequest"):
        AgentProviderRequestSnapshot(cast(ProviderRequest, object()), ("first",))
    with pytest.raises(TypeError, match="tuple of names"):
        AgentProviderRequestSnapshot(request, cast(tuple[str, ...], ["first"]))
    with pytest.raises(TypeError, match="tuple of names"):
        AgentProviderRequestSnapshot(request, (cast(str, 3),))
    with pytest.raises(TypeError, match="tuple of names"):
        AgentProviderRequestSnapshot(request, ("",))
    with pytest.raises(ValueError, match="must not contain duplicates"):
        AgentProviderRequestSnapshot(request, ("first", "first"))
    with pytest.raises(ValueError, match="must match"):
        AgentProviderRequestSnapshot(request, ())
    with pytest.raises(TypeError, match="request must be ProviderRequest"):
        snapshot_provider_request(cast(ProviderRequest, object()))


def test_headless_request_snapshot_needs_only_value_objects(tmp_path: Path) -> None:
    """A fake provider can consume the canonical seam without product runtime state."""

    seen: list[tuple[str, ...]] = []

    def fake_provider(request: ProviderRequest) -> str:
        seen.append(tuple(tool.name for tool in request.available_tools))
        return request.user_prompt

    request = _request(tmp_path)
    snapshot = snapshot_provider_request(
        request,
        user_prompt="headless",
        messages=(AgentUserMessage(ProductContent("headless")),),
        available_tool_names=("second",),
    )

    assert fake_provider(snapshot.request) == "headless"
    assert seen == [("second",)]
