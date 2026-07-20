from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
import json
from pathlib import Path
from typing import cast

import pytest

from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.loop_policy import (
    AgentProviderRequestPolicy,
    AgentProviderRequestPolicyInput,
    AgentToolPolicy,
    AgentToolPolicyDecision,
)
from pipy_harness.native.agent.messages import (
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.request import (
    AgentProviderRequestSnapshot,
    snapshot_provider_request,
)
from pipy_harness.native.agent_loop_policy import (
    NativeAgentProviderRequestPolicy,
    NativeAgentToolPolicy,
    materialize_provider_request,
)
from pipy_harness.native.image_attachment import ProviderImageAttachment
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.tools import ToolDefinition


def _call() -> AgentToolCall:
    return AgentToolCall(
        "provider-call",
        "read",
        ProductContent('{"path":"README.md"}'),
    )


def _result() -> AgentToolResultMessage:
    return AgentToolResultMessage(
        tool_request_id="pipy-tool-request-1",
        tool_name="read",
        content=ProductContent("original"),
        provider_correlation_id="provider-call",
    )


def _request_policy_input(tmp_path: Path) -> AgentProviderRequestPolicyInput:
    accepted = AgentUserMessage(ProductContent("prompt"))
    return AgentProviderRequestPolicyInput(
        ProviderRequest(
            system_prompt="system",
            user_prompt="prompt",
            provider_name="fake",
            model_id="fake-model",
            cwd=tmp_path,
            messages=(accepted,),
        ),
        AgentActiveInput(accepted),
    )


def test_adapters_conform_to_canonical_runtime_protocols(tmp_path: Path) -> None:
    policy_input = _request_policy_input(tmp_path)
    snapshot = snapshot_provider_request(policy_input.baseline)
    request_policy = NativeAgentProviderRequestPolicy(lambda _input: snapshot)
    tool_policy = NativeAgentToolPolicy(
        lambda _call: AgentToolPolicyDecision(),
        lambda _call, result: result.content,
    )

    assert isinstance(request_policy, AgentProviderRequestPolicy)
    assert isinstance(tool_policy, AgentToolPolicy)
    assert request_policy.prepare(policy_input) is snapshot
    assert tool_policy.before_execute(_call()) == AgentToolPolicyDecision()


def test_request_policy_passes_exact_input_once_and_returns_exact_snapshot(
    tmp_path: Path,
) -> None:
    policy_input = _request_policy_input(tmp_path)
    snapshot = snapshot_provider_request(policy_input.baseline)
    seen: list[AgentProviderRequestPolicyInput] = []

    def prepare(value: AgentProviderRequestPolicyInput) -> AgentProviderRequestSnapshot:
        seen.append(value)
        return snapshot

    policy = NativeAgentProviderRequestPolicy(prepare)

    assert policy.prepare(policy_input) is snapshot
    assert seen == [policy_input]
    assert seen[0] is policy_input


def test_provider_projection_detaches_json_schemas_and_preserves_request_fields(
    tmp_path: Path,
) -> None:
    def callback(_headers: object) -> None:
        return None

    message = AgentUserMessage(ProductContent("prompt"))
    attachment = ProviderImageAttachment(
        "image/png", "encoded", 3, "sha", "fixture.png"
    )
    definition = ToolDefinition(
        "read",
        "Read fixture.",
        {
            "type": "object",
            "properties": {"path": {"type": "string", "enum": ["one", "two"]}},
            "required": ["path"],
            "additionalProperties": False,
        },
    )
    policy_input = AgentProviderRequestPolicyInput(
        ProviderRequest(
            system_prompt="system",
            user_prompt="prompt",
            provider_name="fake",
            model_id="fake-model",
            cwd=tmp_path,
            provider_turn_index=4,
            provider_turn_label="tool-4",
            messages=(message,),
            available_tools=(definition,),
            attachments=(attachment,),
            provider_header_callback=callback,
        ),
        AgentActiveInput(message),
    )
    snapshot = snapshot_provider_request(policy_input.baseline)

    provider_request = materialize_provider_request(snapshot)

    assert type(provider_request) is ProviderRequest
    assert provider_request is not snapshot.request
    assert tuple(field.name for field in fields(provider_request)) == tuple(
        field.name for field in fields(snapshot.request)
    )
    for field in fields(provider_request):
        if field.name != "available_tools":
            assert getattr(provider_request, field.name) is getattr(
                snapshot.request, field.name
            )
    assert provider_request.messages is snapshot.request.messages
    assert provider_request.attachments is snapshot.request.attachments
    assert provider_request.provider_header_callback is callback
    assert tuple(tool.name for tool in provider_request.available_tools) == ("read",)
    assert snapshot.advertised_tool_names == ("read",)
    assert snapshot.authorizes("read")
    assert not snapshot.authorizes("other")
    assert type(provider_request.available_tools[0]) is ToolDefinition
    assert (
        provider_request.available_tools[0] is not snapshot.request.available_tools[0]
    )
    provider_schema = provider_request.available_tools[0].input_schema
    assert type(provider_schema) is dict
    assert type(cast(dict[str, object], provider_schema)["required"]) is list
    json.dumps(provider_schema)

    cast(list[str], provider_schema["required"]).append("later")
    cast(dict[str, object], provider_schema)["later"] = True

    frozen_schema = snapshot.request.available_tools[0].input_schema
    assert frozen_schema["required"] == ("path",)
    assert "later" not in frozen_schema


def test_request_policy_revalidates_callback_snapshot_recursively(
    tmp_path: Path,
) -> None:
    class SnapshotSubclass(AgentProviderRequestSnapshot):
        pass

    class RequestSubclass(ProviderRequest):
        pass

    class ToolSubclass(ToolDefinition):
        pass

    class MessageSubclass(AgentUserMessage):
        pass

    class ContentSubclass(ProductContent):
        pass

    def valid_snapshot() -> AgentProviderRequestSnapshot:
        policy_input = AgentProviderRequestPolicyInput(
            ProviderRequest(
                "system",
                "prompt",
                "fake",
                "fake-model",
                tmp_path,
                messages=(AgentUserMessage(ProductContent("prompt")),),
                available_tools=(
                    ToolDefinition(
                        "read",
                        "Read fixture.",
                        {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                    ),
                ),
            ),
            AgentActiveInput(AgentUserMessage(ProductContent("prompt"))),
        )
        return snapshot_provider_request(policy_input.baseline)

    def snapshot_subclass() -> AgentProviderRequestSnapshot:
        source = valid_snapshot()
        value = object.__new__(SnapshotSubclass)
        object.__setattr__(value, "request", source.request)
        object.__setattr__(value, "advertised_tool_names", source.advertised_tool_names)
        return value

    def request_subclass() -> AgentProviderRequestSnapshot:
        value = valid_snapshot()
        request = RequestSubclass("system", "prompt", "fake", "fake-model", tmp_path)
        object.__setattr__(value, "request", request)
        object.__setattr__(value, "advertised_tool_names", ())
        return value

    def tool_subclass() -> AgentProviderRequestSnapshot:
        value = valid_snapshot()
        exact_tool = value.request.available_tools[0]
        tool = ToolSubclass(exact_tool.name, exact_tool.description, {"type": "object"})
        object.__setattr__(value.request, "available_tools", (tool,))
        return value

    def message_subclass() -> AgentProviderRequestSnapshot:
        value = valid_snapshot()
        object.__setattr__(
            value.request,
            "messages",
            (MessageSubclass(ProductContent("prompt")),),
        )
        return value

    def content_subclass() -> AgentProviderRequestSnapshot:
        value = valid_snapshot()
        object.__setattr__(
            value.request,
            "messages",
            (AgentUserMessage(ContentSubclass("prompt")),),
        )
        return value

    def mutable_schema() -> AgentProviderRequestSnapshot:
        value = valid_snapshot()
        object.__setattr__(
            value.request.available_tools[0],
            "input_schema",
            {"type": "object", "properties": {}, "required": []},
        )
        return value

    def tampered_field() -> AgentProviderRequestSnapshot:
        value = valid_snapshot()
        object.__setattr__(value.request, "provider_turn_index", True)
        return value

    def bad_names() -> AgentProviderRequestSnapshot:
        value = valid_snapshot()
        object.__setattr__(value, "advertised_tool_names", ("other",))
        return value

    for factory, error_match in (
        (snapshot_subclass, "AgentProviderRequestSnapshot"),
        (request_subclass, "ProviderRequest"),
        (tool_subclass, "ToolDefinition"),
        (message_subclass, "non-canonical message"),
        (content_subclass, "ProductContent"),
        (mutable_schema, "recursively immutable"),
        (tampered_field, "provider_turn_index"),
        (bad_names, "must match"),
    ):
        snapshot = factory()

        def prepare(
            _policy_input: AgentProviderRequestPolicyInput,
        ) -> AgentProviderRequestSnapshot:
            return snapshot

        policy = NativeAgentProviderRequestPolicy(prepare)
        with pytest.raises((TypeError, ValueError), match=error_match):
            policy.prepare(_request_policy_input(tmp_path))
        with pytest.raises((TypeError, ValueError), match=error_match):
            materialize_provider_request(snapshot)


def test_tool_policy_preserves_callback_arguments_and_call_order() -> None:
    call = _call()
    result = _result()
    decision = AgentToolPolicyDecision(ProductContent("blocked"))
    transformed = ProductContent("transformed")
    trace: list[tuple[str, object, object | None]] = []

    def before_execute(value: AgentToolCall) -> AgentToolPolicyDecision:
        trace.append(("before", value, None))
        return decision

    def transform_result(
        value: AgentToolCall,
        observation: AgentToolResultMessage,
    ) -> ProductContent:
        trace.append(("transform", value, observation))
        return transformed

    policy = NativeAgentToolPolicy(before_execute, transform_result)

    assert policy.before_execute(call) is decision
    assert policy.transform_result(call, result) is transformed
    assert trace == [
        ("before", call, None),
        ("transform", call, result),
    ]
    assert trace[0][1] is call
    assert trace[1][1] is call
    assert trace[1][2] is result


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: NativeAgentProviderRequestPolicy(
                cast(
                    Callable[
                        [AgentProviderRequestPolicyInput], AgentProviderRequestSnapshot
                    ],
                    1,
                )
            ),
            "prepare must be callable",
        ),
        (
            lambda: NativeAgentToolPolicy(
                cast(Callable[[AgentToolCall], AgentToolPolicyDecision], 1),
                lambda _call, result: result.content,
            ),
            "before_execute must be callable",
        ),
        (
            lambda: NativeAgentToolPolicy(
                lambda _call: AgentToolPolicyDecision(),
                cast(
                    Callable[[AgentToolCall, AgentToolResultMessage], ProductContent],
                    1,
                ),
            ),
            "transform_result must be callable",
        ),
    ],
)
def test_adapter_constructors_reject_noncallables(
    factory: Callable[[], object], message: str
) -> None:
    with pytest.raises(TypeError, match=message):
        factory()


def test_request_policy_rejects_bad_callback_return(tmp_path: Path) -> None:
    policy = NativeAgentProviderRequestPolicy(
        cast(
            Callable[[AgentProviderRequestPolicyInput], AgentProviderRequestSnapshot],
            lambda _input: object(),
        )
    )

    with pytest.raises(TypeError, match="must return AgentProviderRequestSnapshot"):
        policy.prepare(_request_policy_input(tmp_path))


def test_tool_policy_rejects_bad_callback_returns() -> None:
    call = _call()
    result = _result()
    before_policy = NativeAgentToolPolicy(
        cast(
            Callable[[AgentToolCall], AgentToolPolicyDecision],
            lambda _call: object(),
        ),
        lambda _call, observation: observation.content,
    )
    transform_policy = NativeAgentToolPolicy(
        lambda _call: AgentToolPolicyDecision(),
        cast(
            Callable[[AgentToolCall, AgentToolResultMessage], ProductContent],
            lambda _call, _result: "not product content",
        ),
    )

    with pytest.raises(TypeError, match="must return AgentToolPolicyDecision"):
        before_policy.before_execute(call)
    with pytest.raises(TypeError, match="must return ProductContent"):
        transform_policy.transform_result(call, result)


def test_tool_policy_revalidates_tampered_decision_recursively() -> None:
    decision = AgentToolPolicyDecision()
    blocked_reason = ProductContent("blocked")
    object.__setattr__(blocked_reason, "value", 1)
    object.__setattr__(decision, "blocked_reason", blocked_reason)
    policy = NativeAgentToolPolicy(
        lambda _call: decision,
        lambda _call, result: result.content,
    )

    with pytest.raises(TypeError, match=r"blocked_reason\.value"):
        policy.before_execute(_call())


def test_tool_policy_revalidates_tampered_transform_content() -> None:
    class StringSubclass(str):
        pass

    content = ProductContent("transformed")
    object.__setattr__(content, "value", StringSubclass("tampered"))
    policy = NativeAgentToolPolicy(
        lambda _call: AgentToolPolicyDecision(),
        lambda _call, _result: content,
    )

    with pytest.raises(TypeError, match=r"callback result\.value"):
        policy.transform_result(_call(), _result())


def test_callback_failure_propagates_without_invoking_a_later_callback() -> None:
    trace: list[str] = []

    def fail_before(_call: AgentToolCall) -> AgentToolPolicyDecision:
        trace.append("before")
        raise RuntimeError("policy callback failed")

    def transform(
        _call: AgentToolCall, result: AgentToolResultMessage
    ) -> ProductContent:
        trace.append("transform")
        return result.content

    policy = NativeAgentToolPolicy(fail_before, transform)

    with pytest.raises(RuntimeError, match="policy callback failed"):
        policy.before_execute(_call())
    assert trace == ["before"]


def test_request_callback_failure_propagates_synchronously(tmp_path: Path) -> None:
    def fail(_input: AgentProviderRequestPolicyInput) -> AgentProviderRequestSnapshot:
        raise RuntimeError("request callback failed")

    policy = NativeAgentProviderRequestPolicy(fail)

    with pytest.raises(RuntimeError, match="request callback failed"):
        policy.prepare(_request_policy_input(tmp_path))


def test_adapters_expose_only_callbacks_and_protocol_operations(tmp_path: Path) -> None:
    policy_input = _request_policy_input(tmp_path)
    request_policy = NativeAgentProviderRequestPolicy(
        lambda value: snapshot_provider_request(value.baseline)
    )
    tool_policy = NativeAgentToolPolicy(
        lambda _call: AgentToolPolicyDecision(),
        lambda _call, result: result.content,
    )

    assert not hasattr(request_policy, "__dict__")
    assert not hasattr(tool_policy, "__dict__")
    assert set(request_policy.__slots__) == {"_prepare"}
    assert set(tool_policy.__slots__) == {"_before_execute", "_transform_result"}
    for forbidden in (
        "emit",
        "append",
        "enqueue",
        "clear",
        "reserve",
        "settle",
        "complete",
        "execute",
    ):
        assert not hasattr(request_policy, forbidden)
        assert not hasattr(tool_policy, forbidden)
    assert request_policy.prepare(policy_input).request == policy_input.baseline
