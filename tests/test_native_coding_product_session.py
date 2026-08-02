"""Contracts for the headless synchronous product-session coordinator."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from typing import Callable, cast

import pytest

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.coding.product_session import (
    CodingProductSessionCallbacks,
    CodingProductSessionCompaction,
    CodingProductSessionContext,
    CodingProductSessionCoordinator,
    CodingProductSessionPort,
)
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import ProviderPort, StreamChunkSink


class _FakeProvider:
    @property
    def name(self) -> str:
        return "fake"

    @property
    def model_id(self) -> str:
        return "fake-model"

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del request, stream_sink, reasoning_sink, cancel_token
        raise AssertionError("product-session contracts never invoke the provider")


class _ContextSubclass(CodingProductSessionContext):
    pass


class _CompactionSubclass(CodingProductSessionCompaction):
    pass


class _IncompletePort:
    def load_active_history(self) -> CodingProductSessionContext:
        return CodingProductSessionContext(())


class _LoadFailurePort:
    def load_active_history(self) -> CodingProductSessionContext:
        raise LookupError("load failed")

    def append_message(self, message: AgentMessage) -> None:
        del message

    def apply_compaction(self, action: CodingProductSessionCompaction) -> None:
        del action


class _UnexpectedReturn:
    def __await__(self) -> Iterator[None]:
        yield None


def _state(messages: tuple[AgentMessage, ...] = ()) -> CodingSessionState:
    provider: ProviderPort = _FakeProvider()
    return CodingSessionState(
        provider=provider,
        provider_name="explicit-provider",
        model_id="explicit-model",
        messages=messages,
    )


def _messages() -> tuple[AgentMessage, ...]:
    user = AgentUserMessage(ProductContent("hello"))
    call = AgentToolCall("provider-call", "read", ProductContent('{"path":"x"}'))
    assistant = AgentAssistantMessage(ProductContent("checking"), (call,))
    result = AgentToolResultMessage(
        "pipy-tool-1",
        "read",
        ProductContent("contents"),
        "provider-call",
    )
    return (user, assistant, result)


def _action(
    messages: tuple[AgentMessage, ...] | None = None,
) -> CodingProductSessionCompaction:
    return CodingProductSessionCompaction(
        retained_messages=messages or _messages(),
        summary_suffix=ProductContent("\n\nProvider suffix"),
        durable_summary=ProductContent("Durable branch summary"),
        dropped_group_count=2,
        measure_before=4096,
    )


def _callbacks(
    *,
    state: CodingSessionState,
    context: CodingProductSessionContext | None = None,
    events: list[str] | None = None,
) -> CodingProductSessionCallbacks:
    observed = events if events is not None else []

    def load() -> CodingProductSessionContext:
        observed.append("load")
        return context or CodingProductSessionContext(())

    def append(message: AgentMessage) -> None:
        assert state.messages[-1] is message
        observed.append("append")

    def compact(action: CodingProductSessionCompaction) -> None:
        assert state.messages == action.retained_messages
        assert state.compaction_suffix == action.summary_suffix.value
        observed.append("compact")

    return CodingProductSessionCallbacks(load, append, compact)


def test_append_preserves_exact_message_identity_order_and_state_first_timing() -> None:
    state = _state()
    events: list[str] = []
    callbacks = _callbacks(state=state, events=events)
    coordinator = CodingProductSessionCoordinator(state=state, port=callbacks)
    messages = _messages()

    for message in messages:
        coordinator.append_message(message)

    assert state.messages == messages
    assert all(actual is expected for actual, expected in zip(state.messages, messages))
    assert events == ["append", "append", "append"]


def test_append_callback_failure_propagates_after_state_advances() -> None:
    state = _state()
    message = _messages()[0]

    def fail(message_to_persist: AgentMessage) -> None:
        assert state.messages == (message_to_persist,)
        raise RuntimeError("append failed")

    callbacks = CodingProductSessionCallbacks(
        lambda: CodingProductSessionContext(()),
        fail,
        lambda action: None,
    )
    coordinator = CodingProductSessionCoordinator(state=state, port=callbacks)

    with pytest.raises(RuntimeError, match="append failed"):
        coordinator.append_message(message)

    assert state.messages == (message,)
    assert state.messages[0] is message


def test_rebuild_loads_exact_context_and_preserves_cumulative_counters() -> None:
    old = _messages()[0]
    state = _state((old,))
    state.record_input_accepted()
    state.apply_compaction(
        (old,),
        summary_suffix="old suffix",
        dropped_group_count=3,
    )
    loaded = _messages()
    callbacks = _callbacks(
        state=state,
        context=CodingProductSessionContext(loaded),
    )

    CodingProductSessionCoordinator(
        state=state,
        port=callbacks,
    ).rebuild_active_history()

    assert state.messages == loaded
    assert all(actual is expected for actual, expected in zip(state.messages, loaded))
    assert state.compaction_suffix == ""
    assert state.compaction_count == 1
    assert state.compaction_dropped_group_count == 3
    assert state.user_turn_count == 1


def test_load_failure_leaves_state_unchanged_and_propagates() -> None:
    messages = _messages()
    state = _state(messages)
    state.apply_compaction(
        messages,
        summary_suffix="existing suffix",
        dropped_group_count=1,
    )
    before = state.result_snapshot()
    coordinator = CodingProductSessionCoordinator(
        state=state,
        port=_LoadFailurePort(),
    )

    with pytest.raises(LookupError, match="load failed"):
        coordinator.rebuild_active_history()

    assert state.result_snapshot() == before
    assert all(actual is expected for actual, expected in zip(state.messages, messages))


@pytest.mark.parametrize("invalid_kind", ["list", "nested", "subclass"])
def test_invalid_loaded_context_leaves_state_unchanged(invalid_kind: str) -> None:
    original = _messages()
    state = _state(original)
    if invalid_kind == "subclass":
        invalid: object = object.__new__(_ContextSubclass)
        object.__setattr__(invalid, "messages", ())
    else:
        invalid = CodingProductSessionContext(_messages())
        if invalid_kind == "list":
            object.__setattr__(invalid, "messages", list(_messages()))
        else:
            nested = invalid.messages[0]
            assert isinstance(nested, AgentUserMessage)
            object.__setattr__(nested.content, "value", ["mutable"])

    def load_invalid() -> CodingProductSessionContext:
        return cast(CodingProductSessionContext, invalid)

    callbacks = CodingProductSessionCallbacks(
        load_invalid,
        lambda message: None,
        lambda action: None,
    )
    coordinator = CodingProductSessionCoordinator(state=state, port=callbacks)

    with pytest.raises(TypeError):
        coordinator.rebuild_active_history()

    assert state.messages == original
    assert all(actual is expected for actual, expected in zip(state.messages, original))


def test_compaction_applies_state_first_and_passes_the_exact_action() -> None:
    state = _state()
    observed: list[CodingProductSessionCompaction] = []
    action = _action()

    def compact(persisted: CodingProductSessionCompaction) -> None:
        assert state.messages == action.retained_messages
        assert state.compaction_suffix == action.summary_suffix.value
        assert state.compaction_count == 1
        observed.append(persisted)

    callbacks = CodingProductSessionCallbacks(
        lambda: CodingProductSessionContext(()),
        lambda message: None,
        compact,
    )

    CodingProductSessionCoordinator(
        state=state,
        port=callbacks,
    ).apply_compaction(action)

    assert observed == [action]
    assert observed[0] is action
    assert all(
        actual is expected
        for actual, expected in zip(state.messages, action.retained_messages)
    )


def test_compaction_callback_failure_propagates_after_state_advances() -> None:
    state = _state()
    action = _action()

    def fail(persisted: CodingProductSessionCompaction) -> None:
        assert persisted is action
        assert state.compaction_count == 1
        raise RuntimeError("compaction failed")

    callbacks = CodingProductSessionCallbacks(
        lambda: CodingProductSessionContext(()),
        lambda message: None,
        fail,
    )
    coordinator = CodingProductSessionCoordinator(state=state, port=callbacks)

    with pytest.raises(RuntimeError, match="compaction failed"):
        coordinator.apply_compaction(action)

    assert state.messages == action.retained_messages
    assert state.compaction_suffix == action.summary_suffix.value
    assert state.compaction_count == 1
    assert state.compaction_dropped_group_count == action.dropped_group_count


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("retained_messages", []),
        ("summary_suffix", ProductContent("")),
        ("durable_summary", ProductContent("")),
        ("dropped_group_count", 0),
        ("dropped_group_count", True),
        ("measure_before", -1),
        ("measure_before", 1.0),
    ],
)
def test_invalid_compaction_is_rejected_before_state_or_callback(
    field_name: str,
    invalid_value: object,
) -> None:
    state = _state(_messages())
    before = state.result_snapshot()
    persisted: list[CodingProductSessionCompaction] = []
    action = _action()
    object.__setattr__(action, field_name, invalid_value)
    callbacks = CodingProductSessionCallbacks(
        lambda: CodingProductSessionContext(()),
        lambda message: None,
        persisted.append,
    )
    coordinator = CodingProductSessionCoordinator(state=state, port=callbacks)

    with pytest.raises((TypeError, ValueError)):
        coordinator.apply_compaction(action)

    assert state.result_snapshot() == before
    assert persisted == []


def test_compaction_context_and_action_are_exact_frozen_slotted_dtos() -> None:
    context = CodingProductSessionContext(_messages())
    action = _action()

    with pytest.raises(FrozenInstanceError):
        setattr(context, "messages", ())
    with pytest.raises(FrozenInstanceError):
        setattr(action, "measure_before", 0)
    assert not hasattr(context, "__dict__")
    assert not hasattr(action, "__dict__")
    with pytest.raises(TypeError):
        _ContextSubclass(_messages())
    with pytest.raises(TypeError):
        _CompactionSubclass(
            _messages(),
            ProductContent("suffix"),
            ProductContent("summary"),
            1,
            0,
        )


def test_runtime_protocol_and_callback_adapter_validation() -> None:
    state = _state()
    callbacks = _callbacks(state=state)

    assert isinstance(callbacks, CodingProductSessionPort)
    with pytest.raises(TypeError, match="port must implement"):
        CodingProductSessionCoordinator(
            state=state,
            port=cast(CodingProductSessionPort, _IncompletePort()),
        )
    with pytest.raises(TypeError, match="state must be an exact"):
        CodingProductSessionCoordinator(
            state=cast(CodingSessionState, object()),
            port=callbacks,
        )

    invalid_callback = cast(Callable[[], CodingProductSessionContext], None)
    with pytest.raises(TypeError, match="load_active_history_callback"):
        CodingProductSessionCallbacks(
            invalid_callback,
            lambda message: None,
            lambda action: None,
        )


def test_append_rejects_awaitable_return_after_synchronous_state_transition() -> None:
    state = _state()
    message = _messages()[0]

    def append_returning_awaitable(message_to_persist: AgentMessage) -> None:
        assert message_to_persist is message
        return cast(None, _UnexpectedReturn())

    callbacks = CodingProductSessionCallbacks(
        lambda: CodingProductSessionContext(()),
        append_returning_awaitable,
        lambda action: None,
    )

    with pytest.raises(TypeError, match="must complete synchronously"):
        CodingProductSessionCoordinator(
            state=state,
            port=callbacks,
        ).append_message(message)

    assert state.messages == (message,)


def test_compaction_rejects_non_none_return_after_state_transition() -> None:
    state = _state()
    action = _action()

    def compact_returning_value(
        action_to_persist: CodingProductSessionCompaction,
    ) -> None:
        assert action_to_persist is action
        return cast(None, object())

    callbacks = CodingProductSessionCallbacks(
        lambda: CodingProductSessionContext(()),
        lambda message: None,
        compact_returning_value,
    )

    with pytest.raises(TypeError, match="must complete synchronously"):
        CodingProductSessionCoordinator(
            state=state,
            port=callbacks,
        ).apply_compaction(action)

    assert state.messages == action.retained_messages
    assert state.compaction_count == 1


def test_coordinator_module_has_no_filesystem_or_product_adapter_dependency() -> None:
    import pipy_harness.native.coding.product_session as module

    names = set(module.__dict__)
    assert names.isdisjoint(
        {
            "Path",
            "SessionTree",
            "json",
            "open",
            "pipy_session",
            "tui",
            "automation",
            "extensions",
        }
    )
