from __future__ import annotations

from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.runtime_ports import (
    AgentQueuedInput,
    AgentQueuedInputKind,
    AgentQueuedInputPort,
)
from pipy_harness.native.coding.input_queue import (
    CodingInputQueue,
    CodingInputSelection,
    CodingInputSource,
)


class _ExternalQueue:
    def __init__(self, items: tuple[AgentQueuedInput, ...] = ()) -> None:
        self.items = deque(items)
        self.poll_count = 0

    def take_next(self) -> AgentQueuedInput | None:
        self.poll_count += 1
        return self.items.popleft() if self.items else None


def _queued(value: str, kind: AgentQueuedInputKind) -> AgentQueuedInput:
    return AgentQueuedInput(ProductContent(value), kind)


def _values(selections: list[CodingInputSelection]) -> list[str]:
    return [selection.content.value for selection in selections]


def test_take_next_uses_complete_product_priority_and_polls_upstream_once() -> None:
    external_item = _queued("external", AgentQueuedInputKind.FOLLOW_UP)
    external = _ExternalQueue((external_item,))
    queue = CodingInputQueue(
        external_inputs=(external,),
        seeds=(ProductContent("seed"),),
    )
    retained = _queued("retained", AgentQueuedInputKind.STEERING)
    queue.retain_agent_input(retained)
    queue.enqueue_extension_steering(ProductContent("extension-steering"))
    queue.enqueue_extension_follow_up(ProductContent("extension-follow-up"))
    queue.enqueue_extension_prompt(ProductContent("extension-prompt"))
    queue.defer_local_command(ProductContent("/local"))

    selections = [queue.take_next() for _ in range(7)]

    assert all(selection is not None for selection in selections)
    complete = [selection for selection in selections if selection is not None]
    assert _values(complete) == [
        "/local",
        "retained",
        "external",
        "seed",
        "extension-steering",
        "extension-follow-up",
        "extension-prompt",
    ]
    assert [selection.source for selection in complete] == [
        CodingInputSource.LOCAL_COMMAND,
        CodingInputSource.RETAINED_AGENT_INPUT,
        CodingInputSource.EXTERNAL_QUEUE,
        CodingInputSource.POSITIONAL_SEED,
        CodingInputSource.EXTENSION_STEERING,
        CodingInputSource.EXTENSION_FOLLOW_UP,
        CodingInputSource.EXTENSION_PROMPT,
    ]
    assert external.poll_count == 5
    assert queue.take_next() is None
    assert external.poll_count == 6


def test_retained_loop_handoff_is_intact_and_consumed_exactly_once() -> None:
    external = _ExternalQueue((_queued("external", AgentQueuedInputKind.STEERING),))
    queue = CodingInputQueue(external_inputs=(external,))
    retained = _queued("next\n\n", AgentQueuedInputKind.FOLLOW_UP)
    queue.retain_agent_input(retained)

    selection = queue.take_next()

    assert selection is not None
    assert selection.content is retained.content
    assert selection.content.value == "next\n\n"
    assert selection.queued_input is retained
    assert selection.queued_input.kind is AgentQueuedInputKind.FOLLOW_UP
    assert external.poll_count == 0
    assert queue.take_next() is not None
    assert external.poll_count == 1


def test_pending_command_defers_without_polling_or_consuming_retained_input() -> None:
    external = _ExternalQueue((_queued("external", AgentQueuedInputKind.STEERING),))
    queue = CodingInputQueue(external_inputs=(external,))
    retained = _queued("retained", AgentQueuedInputKind.FOLLOW_UP)
    queue.retain_agent_input(retained)
    queue.defer_local_command(ProductContent("!local"))

    assert queue.agent_loop_port.take_next() is None
    assert external.poll_count == 0
    command = queue.take_next()
    next_prompt = queue.take_next()

    assert command is not None
    assert command.source is CodingInputSource.LOCAL_COMMAND
    assert not command.bypass_local_command_dispatch
    assert next_prompt is not None
    assert next_prompt.queued_input is retained
    assert external.poll_count == 0


def test_seed_blocks_extension_continuations_but_not_external_queue() -> None:
    external_item = _queued("rpc", AgentQueuedInputKind.STEERING)
    external = _ExternalQueue((external_item,))
    queue = CodingInputQueue(
        external_inputs=(external,),
        seeds=(ProductContent("seed"),),
    )
    queue.enqueue_extension_steering(ProductContent("steer"))
    queue.enqueue_extension_follow_up(ProductContent("follow"))

    assert queue.agent_loop_port.take_next() is external_item
    assert external.poll_count == 1
    assert queue.agent_loop_port.take_next() is None
    assert external.poll_count == 2
    seed = queue.take_next()
    steering = queue.agent_loop_port.take_next()
    follow_up = queue.agent_loop_port.take_next()

    assert seed is not None
    assert seed.source is CodingInputSource.POSITIONAL_SEED
    assert steering is not None
    assert steering.content.value == "steer"
    assert follow_up is not None
    assert follow_up.content.value == "follow"


@pytest.mark.parametrize("value", ["/hotkeys", "!echo hello", "line\n\n"])
def test_every_queued_prompt_bypasses_local_dispatch_and_preserves_text(
    value: str,
) -> None:
    queue = CodingInputQueue()
    queue.enqueue_extension_prompt(ProductContent(value))

    selection = queue.take_next()

    assert selection is not None
    assert selection.content.value == value
    assert selection.bypass_local_command_dispatch


def test_next_turn_context_is_taken_once_when_any_provider_run_is_accepted() -> None:
    queue = CodingInputQueue(seeds=(ProductContent("prompt-one"),))
    first = ProductContent("context-one")
    second = ProductContent("context-two\n")
    queue.enqueue_next_turn_context(first)
    queue.enqueue_next_turn_context(second)
    queue.defer_local_command(ProductContent("/local"))

    command = queue.take_next()
    prompt = queue.take_next()

    assert command is not None
    assert prompt is not None
    assert queue.take_next_turn_context() == (first, second)
    assert queue.take_next_turn_context() == ()


def test_clear_drops_only_extension_inputs_without_polling_external() -> None:
    external_item = _queued("external", AgentQueuedInputKind.FOLLOW_UP)
    external = _ExternalQueue((external_item,))
    queue = CodingInputQueue(
        external_inputs=(external,),
        seeds=(ProductContent("seed"),),
    )
    queue.defer_local_command(ProductContent("/local"))
    queue.retain_agent_input(_queued("retained", AgentQueuedInputKind.STEERING))
    queue.enqueue_extension_steering(ProductContent("steer"))
    queue.enqueue_extension_follow_up(ProductContent("follow"))
    queue.enqueue_extension_prompt(ProductContent("trigger"))
    queue.enqueue_next_turn_context(ProductContent("context"))

    queue.clear_extension_inputs()

    assert external.poll_count == 0
    selections = [queue.take_next() for _ in range(4)]
    assert all(selection is not None for selection in selections)
    complete = [selection for selection in selections if selection is not None]
    assert _values(complete) == ["/local", "retained", "external", "seed"]
    assert complete[2].queued_input is external_item
    assert queue.take_next_turn_context() == ()
    assert queue.take_next() is None


def test_agent_loop_port_is_typed_stable_and_excludes_trigger_prompts() -> None:
    queue = CodingInputQueue()
    queue.enqueue_extension_prompt(ProductContent("trigger"))

    assert isinstance(queue.agent_loop_port, AgentQueuedInputPort)
    assert queue.agent_loop_port is queue.agent_loop_port
    assert queue.agent_loop_port.take_next() is None
    selection = queue.take_next()
    assert selection is not None
    assert selection.source is CodingInputSource.EXTENSION_PROMPT


def test_external_queue_is_polled_at_most_once_per_selection_attempt() -> None:
    external = _ExternalQueue()
    queue = CodingInputQueue(external_inputs=(external,))
    queue.enqueue_extension_prompt(ProductContent("trigger"))

    assert queue.take_next() is not None
    assert external.poll_count == 1
    assert queue.agent_loop_port.take_next() is None
    assert external.poll_count == 2


class _ContentSubclass(ProductContent):
    pass


class _QueuedInputSubclass(AgentQueuedInput):
    pass


@pytest.mark.parametrize(
    "action",
    [
        lambda queue: queue.enqueue_seed(_ContentSubclass("seed")),
        lambda queue: queue.enqueue_extension_prompt(_ContentSubclass("prompt")),
        lambda queue: queue.enqueue_next_turn_context(_ContentSubclass("context")),
        lambda queue: queue.retain_agent_input(
            _QueuedInputSubclass(
                ProductContent("queued"), AgentQueuedInputKind.STEERING
            )
        ),
    ],
)
def test_public_mutations_reject_payload_subclasses(action: object) -> None:
    mutation = cast(Callable[[CodingInputQueue], None], action)

    with pytest.raises(TypeError, match="exact"):
        mutation(CodingInputQueue())


def test_external_queue_rejects_nonexact_dto_and_nested_payloads() -> None:
    subclass_item = _QueuedInputSubclass(
        ProductContent("queued"), AgentQueuedInputKind.STEERING
    )
    queue = CodingInputQueue(external_inputs=(_ExternalQueue((subclass_item,)),))

    with pytest.raises(TypeError, match="exact AgentQueuedInput"):
        queue.take_next()

    invalid_content = AgentQueuedInput(
        _ContentSubclass("content"), AgentQueuedInputKind.FOLLOW_UP
    )
    queue = CodingInputQueue(external_inputs=(_ExternalQueue((invalid_content,)),))

    with pytest.raises(TypeError, match=r"external queued input\.content"):
        queue.agent_loop_port.take_next()


def test_invalid_external_port_and_duplicate_local_command_fail_closed() -> None:
    with pytest.raises(TypeError, match="AgentQueuedInputPort"):
        CodingInputQueue(external_inputs=(cast(AgentQueuedInputPort, object()),))

    queue = CodingInputQueue()
    queue.defer_local_command(ProductContent("one"))
    with pytest.raises(RuntimeError, match="already pending"):
        queue.defer_local_command(ProductContent("two"))


def test_retained_agent_handoffs_survive_command_and_append_fifo() -> None:
    first = _queued("first", AgentQueuedInputKind.STEERING)
    second = _queued("second", AgentQueuedInputKind.FOLLOW_UP)
    queue = CodingInputQueue()
    queue.retain_agent_input(first)
    queue.defer_local_command(ProductContent("/resource"))

    command = queue.take_next()
    queue.retain_agent_input(second)
    assert queue.agent_loop_port.take_next() is None
    first_selection = queue.take_next()
    second_selection = queue.take_next()

    assert command is not None
    assert command.source is CodingInputSource.LOCAL_COMMAND
    assert first_selection is not None
    assert first_selection.source is CodingInputSource.RETAINED_AGENT_INPUT
    assert first_selection.content is first.content
    assert first_selection.queued_input is first
    assert first_selection.queued_input.kind is AgentQueuedInputKind.STEERING
    assert second_selection is not None
    assert second_selection.source is CodingInputSource.RETAINED_AGENT_INPUT
    assert second_selection.content is second.content
    assert second_selection.queued_input is second
    assert second_selection.queued_input.kind is AgentQueuedInputKind.FOLLOW_UP
    assert queue.take_next() is None


def test_external_sources_keep_declared_priority() -> None:
    first = _ExternalQueue((_queued("first", AgentQueuedInputKind.STEERING),))
    second = _ExternalQueue((_queued("second", AgentQueuedInputKind.FOLLOW_UP),))
    queue = CodingInputQueue(external_inputs=(first, second))

    first_selection = queue.take_next()
    second_selection = queue.take_next()

    assert first_selection is not None
    assert first_selection.content.value == "first"
    assert second_selection is not None
    assert second_selection.content.value == "second"
    assert first.poll_count == 2
    assert second.poll_count == 1


def test_external_wake_classifies_registered_source_exactly_once() -> None:
    queued_input = _queued("wake\n\n", AgentQueuedInputKind.STEERING)
    external = _ExternalQueue((queued_input,))
    queue = CodingInputQueue(external_inputs=(external,))

    selection = queue.classify_external_wake(external, "wake\n\n")

    assert selection is not None
    assert selection.source is CodingInputSource.EXTERNAL_QUEUE
    assert selection.content is queued_input.content
    assert selection.queued_input is queued_input
    assert external.poll_count == 1


def test_external_wake_rejects_unregistered_source_without_polling() -> None:
    registered = _ExternalQueue()
    unregistered = _ExternalQueue((_queued("wake", AgentQueuedInputKind.FOLLOW_UP),))
    queue = CodingInputQueue(external_inputs=(registered,))

    with pytest.raises(ValueError, match="not registered"):
        queue.classify_external_wake(unregistered, "wake\n")

    assert registered.poll_count == 0
    assert unregistered.poll_count == 0


def test_external_wake_rejects_nonexact_dto() -> None:
    subclass_item = _QueuedInputSubclass(
        ProductContent("wake"), AgentQueuedInputKind.STEERING
    )
    external = _ExternalQueue((subclass_item,))
    queue = CodingInputQueue(external_inputs=(external,))

    with pytest.raises(TypeError, match="exact AgentQueuedInput"):
        queue.classify_external_wake(external, "wake\n")


def test_external_wake_mismatch_retains_exact_input_and_leaves_line_fresh() -> None:
    queued_input = _queued("different\n\n", AgentQueuedInputKind.FOLLOW_UP)
    external = _ExternalQueue((queued_input,))
    queue = CodingInputQueue(external_inputs=(external,))

    assert queue.classify_external_wake(external, "fresh line\n") is None
    assert external.poll_count == 1

    selection = queue.take_next()

    assert selection is not None
    assert selection.source is CodingInputSource.EXTERNAL_QUEUE
    assert selection.content is queued_input.content
    assert selection.queued_input is queued_input
    assert selection.queued_input.kind is AgentQueuedInputKind.FOLLOW_UP
    assert external.poll_count == 1
    assert queue.take_next() is None
    assert external.poll_count == 2


def test_external_wake_rejects_duplicate_retention_before_polling_again() -> None:
    first = _queued("first", AgentQueuedInputKind.STEERING)
    second = _queued("second", AgentQueuedInputKind.FOLLOW_UP)
    external = _ExternalQueue((first, second))
    queue = CodingInputQueue(external_inputs=(external,))

    assert queue.classify_external_wake(external, "fresh\n") is None
    with pytest.raises(RuntimeError, match="already retained"):
        queue.classify_external_wake(external, "another fresh line\n")

    assert external.poll_count == 1
    retained = queue.take_next()
    following = queue.take_next()
    assert retained is not None
    assert retained.queued_input is first
    assert following is not None
    assert following.queued_input is second
    assert external.poll_count == 2


def test_external_wake_returns_new_command_and_retains_input_exactly_once() -> None:
    queued_input = _queued("wake", AgentQueuedInputKind.FOLLOW_UP)
    external = _ExternalQueue((queued_input,))
    commands = deque((ProductContent("/local"),))
    queue = CodingInputQueue(
        external_inputs=(external,),
        pending_local_command_source=lambda: commands.popleft() if commands else None,
    )

    command = queue.classify_external_wake(external, "wake\n")
    retained = queue.take_next()

    assert command is not None
    assert command.source is CodingInputSource.LOCAL_COMMAND
    assert command.content.value == "/local"
    assert retained is not None
    assert retained.source is CodingInputSource.EXTERNAL_QUEUE
    assert retained.content is queued_input.content
    assert retained.queued_input is queued_input
    assert external.poll_count == 1
    assert queue.take_next() is None
    assert external.poll_count == 2


def test_unclassified_external_wake_remains_fresh_input() -> None:
    external = _ExternalQueue()
    queue = CodingInputQueue(external_inputs=(external,))

    assert queue.classify_external_wake(external, "fresh input\n") is None
    assert external.poll_count == 1


def test_unclassified_wake_returns_command_then_retained_fresh_line() -> None:
    external = _ExternalQueue()
    commands = deque((ProductContent("/pending"),))
    queue = CodingInputQueue(
        external_inputs=(external,),
        pending_local_command_source=lambda: commands.popleft() if commands else None,
    )

    command = queue.classify_external_wake(external, "/fresh command\n")
    fresh = queue.take_next()

    assert command is not None
    assert command.source is CodingInputSource.LOCAL_COMMAND
    assert command.content.value == "/pending"
    assert fresh is not None
    assert fresh.source is CodingInputSource.RETAINED_FRESH_INPUT
    assert fresh.content.value == "/fresh command\n"
    assert not fresh.bypass_local_command_dispatch
    assert external.poll_count == 1
    assert queue.take_next() is None
    assert external.poll_count == 2


def test_unclassified_eof_returns_command_without_retaining_empty_input() -> None:
    external = _ExternalQueue()
    commands = deque((ProductContent("/pending"),))
    queue = CodingInputQueue(
        external_inputs=(external,),
        pending_local_command_source=lambda: commands.popleft() if commands else None,
    )

    command = queue.classify_external_wake(external, "")

    assert command is not None
    assert command.source is CodingInputSource.LOCAL_COMMAND
    assert external.poll_count == 1
    assert queue.take_next() is None
    assert external.poll_count == 2


def test_eof_wake_delivers_exact_valid_dto_without_shutdown_loss() -> None:
    queued_input = _queued("queued at eof\n\n", AgentQueuedInputKind.STEERING)
    external = _ExternalQueue((queued_input,))
    queue = CodingInputQueue(external_inputs=(external,))

    selection = queue.classify_external_wake(external, "")

    assert selection is not None
    assert selection.source is CodingInputSource.EXTERNAL_QUEUE
    assert selection.content is queued_input.content
    assert selection.queued_input is queued_input
    assert selection.queued_input.kind is AgentQueuedInputKind.STEERING
    assert external.poll_count == 1


def test_eof_wake_returns_command_then_exact_valid_dto_without_repoll() -> None:
    queued_input = _queued("queued at eof", AgentQueuedInputKind.FOLLOW_UP)
    external = _ExternalQueue((queued_input,))
    commands = deque((ProductContent("/pending"),))
    queue = CodingInputQueue(
        external_inputs=(external,),
        pending_local_command_source=lambda: commands.popleft() if commands else None,
    )

    command = queue.classify_external_wake(external, "")
    retained = queue.take_next()

    assert command is not None
    assert command.source is CodingInputSource.LOCAL_COMMAND
    assert retained is not None
    assert retained.source is CodingInputSource.EXTERNAL_QUEUE
    assert retained.content is queued_input.content
    assert retained.queued_input is queued_input
    assert retained.queued_input.kind is AgentQueuedInputKind.FOLLOW_UP
    assert external.poll_count == 1
    assert queue.take_next() is None
    assert external.poll_count == 2


def test_mismatched_wake_returns_command_fresh_line_then_exact_dto() -> None:
    queued_input = _queued("newer", AgentQueuedInputKind.FOLLOW_UP)
    external = _ExternalQueue((queued_input,))
    commands = deque((ProductContent("/pending"),))
    queue = CodingInputQueue(
        external_inputs=(external,),
        pending_local_command_source=lambda: commands.popleft() if commands else None,
    )

    command = queue.classify_external_wake(external, "fresh line\n")

    assert command is not None
    assert command.source is CodingInputSource.LOCAL_COMMAND
    assert queue.agent_loop_port.take_next() is None
    assert external.poll_count == 1
    fresh = queue.take_next()
    queued = queue.take_next()
    assert fresh is not None
    assert fresh.source is CodingInputSource.RETAINED_FRESH_INPUT
    assert fresh.content.value == "fresh line\n"
    assert not fresh.bypass_local_command_dispatch
    assert queued is not None
    assert queued.source is CodingInputSource.EXTERNAL_QUEUE
    assert queued.content is queued_input.content
    assert queued.queued_input is queued_input
    assert external.poll_count == 1
    assert queue.take_next() is None
    assert external.poll_count == 2


def test_pending_command_source_is_polled_by_outer_and_agent_loop_paths() -> None:
    commands = deque((ProductContent("/first"), ProductContent("!second")))
    queue = CodingInputQueue(
        pending_local_command_source=lambda: commands.popleft() if commands else None
    )

    assert queue.agent_loop_port.take_next() is None
    first = queue.take_next()
    second = queue.take_next()

    assert first is not None
    assert first.content.value == "/first"
    assert second is not None
    assert second.content.value == "!second"


def test_product_composition_deleted_superseded_queue_state_and_helpers() -> None:
    native_root = Path(__file__).resolve().parents[1] / "src/pipy_harness/native"
    source = "\n".join(
        (native_root / relative).read_text(encoding="utf-8")
        for relative in ("tool_loop_session.py", "repl/loop_step.py")
    )

    for obsolete in (
        "extension_pending_messages",
        "extension_pending_next_turn_custom_messages",
        "extension_pending_steering_messages",
        "extension_pending_follow_up_messages",
        "seed_pending_messages",
        "controller_pending_queued_input",
        "deferred_pending_command",
        "take_next_agent_queued_input",
        "take_next_agent_loop_queued_input",
        "clear_extension_delivery_queues",
        "input_queued_input_port.take_next()",
    ):
        assert obsolete not in source
