from __future__ import annotations

import io
import json
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from session_generation_test_support import build_test_projection

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import AgentUserMessage, ProductContent
from pipy_harness.native.agent.events import (
    AgentRunCompleted,
    AgentRunStarted,
    TurnStarted,
)
from pipy_harness.native.agent.provider_turn import ProviderTurnExecutor
from pipy_harness.native.agent.usage import AgentProviderUsageSample
from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.catalog_state import ProviderCatalogState
from pipy_harness.native.coding import CodingInputQueue
from pipy_harness.native.coding.effects import CodingEffectCoordinator
from pipy_harness.native.coding.session import production_tool_registry
from pipy_harness.native.coding.session_controller import _NativeSessionControl
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.extension_hooks import (
    _compose_extension_bundle,
    dispatch_before_agent_start_hooks,
)
from pipy_harness.native.extension_types import QueuedCustomMessage
from pipy_harness.native.extensions.command_context import ExtensionCapabilityError
from pipy_harness.native.extensions.contracts import (
    RegisteredMessageRenderer,
)
from pipy_harness.native.extensions.message_routing import (
    GenerationMessageRetirement,
    GenerationMessageRouting,
)
from pipy_harness.native.models import ProviderResult, ProviderToolCall
from pipy_harness.native.repl.collaborators import SessionCollaborators
from pipy_harness.native.repl.loop_scope import RunControlState
from pipy_harness.native.repl.provider_selection import ProviderMutationEffects
from pipy_harness.native.repl_state import (
    ModelRuntime,
    NativeDefaultsStore,
    NativeModelOption,
    NativeModelSelection,
    NativeReplProviderState,
)
from pipy_harness.native.session_generation import (
    SessionExtensionGeneration,
    SessionGenerationRef,
)
from pipy_harness.native.session_tree import (
    CustomEntry,
    CustomMessageEntry,
    LabelEntry,
    NativeSessionTree,
    SessionEntry,
    SessionInfoEntry,
    ThinkingLevelChangeEntry,
)
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    ToolFilterOptions,
)
from pipy_harness.native.ui.components.custom_entry_renderer import (
    CustomEntryRenderer,
    CustomRendererProjectionSnapshot,
)

_RLOCK_BASE = type(threading.RLock())


class _OrderCheckingMutationLock(_RLOCK_BASE):  # type: ignore[misc,valid-type]
    def __init__(self, session_mutex: threading.RLock) -> None:
        super().__init__()
        self._session_mutex = session_mutex

    def __enter__(self) -> _OrderCheckingMutationLock:
        if cast(Any, self._session_mutex)._is_owned():
            raise RuntimeError("mutation lock acquired under session mutex")
        return cast(_OrderCheckingMutationLock, super().__enter__())


def test_coordinator_close_allows_only_owner_nesting_and_releases_depth() -> None:
    coordinator = CodingEffectCoordinator()
    owner_entered = threading.Event()
    waiter_started = threading.Event()
    nested_finished = threading.Event()
    close_finished = threading.Event()
    results: list[tuple[str, bool]] = []

    def owner() -> None:
        with coordinator.effect() as admitted:
            assert admitted
            owner_entered.set()
            while not coordinator.terminal:
                pass
            with coordinator.effect() as nested:
                results.append(("nested", nested))
                nested_finished.set()

    def waiter() -> None:
        waiter_started.set()
        with coordinator.effect() as admitted:
            results.append(("waiter", admitted))

    def close() -> None:
        with coordinator.terminal_section() as first:
            results.append(("close", first))
        close_finished.set()

    owner_thread = threading.Thread(target=owner)
    owner_thread.start()
    assert owner_entered.wait(1)
    waiter_thread = threading.Thread(target=waiter)
    waiter_thread.start()
    assert waiter_started.wait(1)
    close_thread = threading.Thread(target=close)
    close_thread.start()

    assert nested_finished.wait(1)
    owner_thread.join(1)
    waiter_thread.join(1)
    close_thread.join(1)
    assert close_finished.is_set()
    assert sorted(results) == sorted(
        [("nested", True), ("waiter", False), ("close", True)]
    )
    with coordinator.effect() as admitted:
        assert not admitted
    with coordinator.terminal_section() as first:
        assert not first


def test_coordinator_exception_releases_owner_for_an_unrelated_thread() -> None:
    coordinator = CodingEffectCoordinator()
    with pytest.raises(RuntimeError, match="effect failed"):
        with coordinator.effect() as admitted:
            assert admitted
            with coordinator.effect() as nested:
                assert nested
                raise RuntimeError("effect failed")

    accepted: list[bool] = []
    thread = threading.Thread(
        target=lambda: _record_effect_admission(coordinator, accepted)
    )
    thread.start()
    thread.join(1)
    assert accepted == [True]


def _record_effect_admission(
    coordinator: CodingEffectCoordinator, accepted: list[bool]
) -> None:
    with coordinator.effect() as admitted:
        accepted.append(admitted)


def _generation_ref(lock: threading.RLock) -> SessionGenerationRef:
    user: list[Any] = []
    custom: list[Any] = []
    routing = GenerationMessageRouting(user, custom, mutex=lock)
    runtime = _compose_extension_bundle((), user, custom, routing)
    projection = build_test_projection(runtime, {}, queue_mutex=lock)
    return SessionGenerationRef(
        SessionExtensionGeneration(runtime, projection), lock=lock
    )


def _close_effects(
    coordinator: CodingEffectCoordinator, finished: threading.Event
) -> None:
    with coordinator.terminal_section() as first:
        assert first
    finished.set()


def _guarded_read_finishes(read: Callable[[], object]) -> bool:
    finished = threading.Event()

    def run() -> None:
        read()
        finished.set()

    threading.Thread(target=run, daemon=True).start()
    return finished.wait(1)


def test_tree_append_keeps_id_parent_memory_and_jsonl_in_one_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = CodingEffectCoordinator()
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "sessions")
    tree.bind_mutation_lock(coordinator.lock)
    first_write_entered = threading.Event()
    release_first_write = threading.Event()
    original_write = tree._write_entry
    calls = 0

    def blocking_write(entry: SessionEntry) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_write_entered.set()
            assert release_first_write.wait(1)
        original_write(entry)

    monkeypatch.setattr(tree, "_write_entry", blocking_write)
    results: list[CustomEntry] = []
    first = threading.Thread(target=lambda: results.append(tree.append_custom("first")))
    second = threading.Thread(
        target=lambda: results.append(tree.append_custom("second"))
    )
    first.start()
    assert first_write_entered.wait(1)
    second.start()

    generation_ref = _generation_ref(threading.RLock())
    assert generation_ref.lock.acquire(blocking=False)
    generation_ref.lock.release()
    assert second.is_alive(), "a second append passed the blocked durable write"
    release_first_write.set()
    first.join(1)
    second.join(1)

    entries = tree.get_entries()
    assert all(isinstance(entry, CustomEntry) for entry in entries)
    assert [
        entry.custom_type for entry in entries if isinstance(entry, CustomEntry)
    ] == [
        "first",
        "second",
    ]
    assert entries[0].id != entries[1].id and entries[0].parent_id is None
    assert entries[1].parent_id == entries[0].id
    assert tree.path is not None
    durable = [json.loads(line) for line in tree.path.read_text().splitlines()][1:]
    assert [row["id"] for row in durable] == [entry.id for entry in entries]
    assert [row["parentId"] for row in durable] == [
        entry.parent_id for entry in entries
    ]


def test_name_label_and_snapshot_reads_wait_for_complete_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = CodingEffectCoordinator()
    tree = NativeSessionTree.create(tmp_path, persist=False)
    tree.bind_mutation_lock(coordinator.lock)
    target = tree.append_custom("target")
    write_entered = threading.Event()
    release_write = threading.Event()
    block_type: type[SessionInfoEntry] | type[LabelEntry] = SessionInfoEntry
    original_write = tree._write_entry

    def blocking_write(entry: SessionEntry) -> None:
        if isinstance(entry, block_type):
            write_entered.set()
            assert release_write.wait(1)
        original_write(entry)

    monkeypatch.setattr(tree, "_write_entry", blocking_write)
    writer = threading.Thread(target=lambda: tree.append_session_info("complete-name"))
    writer.start()
    assert write_entered.wait(1)
    name_snapshot: list[tuple[str | None, int, str | None]] = []

    def read_name_snapshot() -> None:
        name = tree.name
        entries, leaf = tree.snapshot_entries_and_leaf()
        name_snapshot.append((name, len(entries), leaf))

    tree_snapshot: list[object] = []
    tree_read_started = threading.Event()

    def read_tree_snapshot() -> None:
        tree_read_started.set()
        tree_snapshot.extend(tree.get_tree())

    reader = threading.Thread(target=read_name_snapshot)
    tree_reader = threading.Thread(target=read_tree_snapshot)
    reader.start()
    tree_reader.start()
    assert tree_read_started.wait(1)
    reader.join(0.05)
    tree_reader.join(0.05)
    assert reader.is_alive() and tree_reader.is_alive()
    release_write.set()
    writer.join(1)
    reader.join(1)
    tree_reader.join(1)
    assert name_snapshot == [("complete-name", 2, tree.get_leaf_id())]
    assert len(tree_snapshot) == 1

    block_type = LabelEntry
    write_entered = threading.Event()
    release_write = threading.Event()
    writer = threading.Thread(
        target=lambda: tree.append_label_change(target.id, "complete-label")
    )
    writer.start()
    assert write_entered.wait(1)
    label_snapshot: list[tuple[str | None, str | None]] = []

    def read_label_snapshot() -> None:
        label = tree.get_label(target.id)
        roots = tree.get_tree()
        label_snapshot.append((label, roots[0].label))

    reader = threading.Thread(target=read_label_snapshot)
    reader.start()
    reader.join(0.05)
    assert reader.is_alive()
    release_write.set()
    writer.join(1)
    reader.join(1)
    assert label_snapshot == [("complete-label", "complete-label")]


def test_actual_lock_order_instrumentation_rejects_the_reverse_edge(
    tmp_path: Path,
) -> None:
    ref = _generation_ref(threading.RLock())
    coordinator = CodingEffectCoordinator(_OrderCheckingMutationLock(ref.lock))
    tree = NativeSessionTree.create(tmp_path, persist=False)
    tree.bind_mutation_lock(coordinator.lock)

    with ref.lock:
        with pytest.raises(RuntimeError, match="under session mutex"):
            tree.get_entries()
    with coordinator.lock, ref.lock:
        pass


def test_completion_terminal_barrier_and_provider_callback_read_are_unlocked(
    tmp_path: Path,
) -> None:
    coordinator = CodingEffectCoordinator()
    tree = NativeSessionTree.create(tmp_path, persist=False)
    tree.bind_mutation_lock(coordinator.lock)
    provider_entered = threading.Event()
    release_provider = threading.Event()
    last_effects: list[str] = []
    callback_reads: list[bool] = []

    def provider_callback(_headers: object) -> None:
        callback_reads.append(_guarded_read_finishes(lambda: tree.name))

    class BlockingProvider:
        def complete(self, request: object) -> object:
            cast(Any, request).provider_header_callback({})
            provider_entered.set()
            assert release_provider.wait(1)
            last_effects.append("complete")
            return SimpleNamespace(
                status=HarnessStatus.SUCCEEDED, final_text="accepted"
            )

    collaborators = cast(
        SessionCollaborators,
        SimpleNamespace(
            coding_effects=coordinator,
            coding_state=SimpleNamespace(
                provider_name="test",
                model_id="test-model",
                provider=BlockingProvider(),
            ),
            cwd=tmp_path,
            active_provider_header_callback=lambda: provider_callback,
        ),
    )
    results: list[str] = []
    completion = threading.Thread(
        target=lambda: results.append(
            SessionCollaborators.extension_complete(collaborators, "system", "user")
        )
    )
    completion.start()
    assert provider_entered.wait(1)

    close_done = threading.Event()
    closer = threading.Thread(target=lambda: _close_effects(coordinator, close_done))
    closer.start()
    deadline = time.monotonic() + 1
    while not coordinator.terminal and time.monotonic() < deadline:
        time.sleep(0)
    assert coordinator.terminal and not close_done.is_set()
    assert _guarded_read_finishes(lambda: tree.get_entries())

    release_provider.set()
    completion.join(1)
    closer.join(1)
    assert results == ["accepted"] and last_effects == ["complete"]
    assert callback_reads == [True]
    with pytest.raises(ExtensionCapabilityError, match="coding session is closed"):
        SessionCollaborators.extension_complete(
            collaborators, "late-system", "late-user"
        )
    assert last_effects == ["complete"]


def test_custom_message_owner_blocks_terminal_until_tree_then_input_finish(
    tmp_path: Path,
) -> None:
    coordinator = CodingEffectCoordinator()
    tree = NativeSessionTree.create(tmp_path, persist=False)
    tree.bind_mutation_lock(coordinator.lock)
    input_published = threading.Event()
    release_input = threading.Event()

    class BlockingQueue(CodingInputQueue):
        def enqueue_extension_prompt(self, content: ProductContent) -> None:
            super().enqueue_extension_prompt(content)
            input_published.set()
            assert release_input.wait(1)

    queue = BlockingQueue(mutation_lock=coordinator.lock)
    state = SimpleNamespace(session_tree=tree, extension_in_agent_turn=False)
    renderer = CustomEntryRenderer(
        ctl=state,
        terminal=None,
        coding_input_queue=queue,
        coding_effects=coordinator,
        error_stream=io.StringIO(),
    )
    callback_reads: list[bool] = []

    def render(_data: object) -> list[str]:
        callback_reads.append(_guarded_read_finishes(lambda: tree.get_tree()))
        return ["rendered"]

    projection = CustomRendererProjectionSnapshot(
        {"notice": RegisteredMessageRenderer("notice", render, "test")}, {}
    )
    result: list[object] = []
    writer = threading.Thread(
        target=lambda: result.append(
            renderer._deliver_custom_message(
                QueuedCustomMessage(
                    "notice", "payload", True, None, {"triggerTurn": True}
                ),
                projection,
            )
        )
    )
    writer.start()
    assert input_published.wait(1)
    entries = tree.get_entries()
    assert len(entries) == 1 and isinstance(entries[0], CustomMessageEntry)
    assert entries[0].content == "payload"

    close_done = threading.Event()
    closer = threading.Thread(target=lambda: _close_effects(coordinator, close_done))
    closer.start()
    assert not close_done.wait(0.05)
    release_input.set()
    writer.join(1)
    closer.join(1)
    assert close_done.is_set() and len(result) == 1
    assert callback_reads == [True]
    assert queue.take_next() is not None

    before = tree.get_entries()
    with pytest.raises(ExtensionCapabilityError, match="coding session is closed"):
        renderer.extension_send_message(
            "notice", "too late", False, {"triggerTurn": True}
        )
    assert tree.get_entries() == before
    assert queue.take_next() is None
    assert tree.name is None  # guarded read-only views remain available at terminal


def _ctl(c: CodingEffectCoordinator, tree: NativeSessionTree) -> RunControlState:
    return RunControlState(
        coding_effects=c,
        _session_tree=tree,
        tree_filter_mode="default",
        pending_prefill=None,
        package_roots=cast(Any, None),
        workspace_resources=cast(Any, None),
        generation_ref=cast(Any, None),
        agent_settled_pending=False,
        extension_in_agent_turn=False,
    )


def test_active_tree_pointer_selection_and_name_append_are_one_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = CodingEffectCoordinator()
    selected_tree = NativeSessionTree.create(tmp_path, persist=False)
    incoming = NativeSessionTree.create(tmp_path, persist=False)
    ctl = _ctl(coordinator, selected_tree)
    selected, release = (threading.Barrier(2) for _ in range(2))
    order: list[str] = []

    def paused_append(name: str | None) -> SessionInfoEntry:
        selected.wait(timeout=1)
        release.wait(timeout=1)
        appended = NativeSessionTree.append_session_info(selected_tree, name)
        order.append("append")
        return appended

    monkeypatch.setattr(selected_tree, "append_session_info", paused_append)
    collaborator = cast(
        SessionCollaborators, SimpleNamespace(coding_effects=coordinator, ctl=ctl)
    )
    writer = threading.Thread(
        target=lambda: SessionCollaborators.extension_set_session_name(
            collaborator, "accepted"
        )
    )
    writer.start()
    selected.wait(timeout=1)
    rebind_started, rebind_done = threading.Event(), threading.Event()

    def rebind() -> None:
        rebind_started.set()
        ctl.session_tree = incoming
        order.append("rebind")
        rebind_done.set()

    rebinder = threading.Thread(target=rebind)
    rebinder.start()
    assert rebind_started.wait(1) and not rebind_done.wait(0.05)
    release.wait(timeout=1)
    for thread in (writer, rebinder):
        thread.join(1)
    assert order == ["append", "rebind"]
    assert selected_tree.name == "accepted" and incoming.name is None
    assert ctl.session_tree is incoming


def test_incoming_tree_bind_and_active_pointer_swap_are_one_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = CodingEffectCoordinator()
    ctl = _ctl(coordinator, NativeSessionTree.create(tmp_path, persist=False))
    incoming = NativeSessionTree.create(tmp_path, persist=False)
    bound, release = (threading.Barrier(2) for _ in range(2))

    def paused_bind(lock: threading.RLock) -> None:
        NativeSessionTree.bind_mutation_lock(incoming, lock)
        bound.wait(timeout=1)
        release.wait(timeout=1)

    monkeypatch.setattr(incoming, "bind_mutation_lock", paused_bind)
    rebinder = threading.Thread(target=lambda: setattr(ctl, "session_tree", incoming))
    rebinder.start()
    bound.wait(timeout=1)
    appended: list[CustomEntry] = []
    writer = threading.Thread(
        target=lambda: appended.append(incoming.append_custom("after-swap"))
    )
    writer.start()
    writer.join(0.05)
    assert writer.is_alive() and not appended
    release.wait(timeout=1)
    for thread in (rebinder, writer):
        thread.join(1)
    assert len(appended) == 1
    assert ctl.session_tree is incoming and incoming.mutation_lock is coordinator.lock


def test_r5a_active_pointer_writer_and_rebind_inventory_is_guarded() -> None:
    root = Path(__file__).parents[1] / "src/pipy_harness/native"
    tui, renderer, scope, transfer, commands, collaborators = (
        (root / name).read_text()
        for name in (
            "tui.py",
            "ui/components/custom_entry_renderer.py",
            "repl/loop_scope.py",
            "repl/session_transfer.py",
            "repl/session_commands.py",
            "repl/collaborators.py",
        )
    )
    # The guarded readers travelled with the collaborators that hold `ctl`.
    assert collaborators.count("with self.ctl.session_tree_section() as tree:") == 3
    # The guarded tree writers travelled with the custom-entry renderer.
    assert tui.count("with self.coding_effects.lock:") == 0
    assert renderer.count("with self.coding_effects.lock:") == 2
    # The rebind itself lives with `RunControlState`, which owns the tree slot;
    # the guarded readers and writers stay at the composition root.
    assert (
        "with self.coding_effects.lock:\n            tree.bind_mutation_lock" in scope
    )
    # Every terminal rebind is now owned by SessionTransitionCoordinator:
    # `/new`, `/resume`, `/fork` and `/clone` delegate from session commands,
    # and `/import` delegates from transfer verbs. The composition root does
    # not perform a direct rebind.
    assert collaborators.count("self.ctl.session_tree = ") == 0
    assert commands.count("self.ctl.session_tree = ") == 3
    assert transfer.count("self.ctl.session_tree = ") == 0
    assert "import_terminal(" in (root / "repl/session_transition.py").read_text(
        encoding="utf-8"
    )


def _provider_mutation_fixture(
    tmp_path: Path,
    *,
    persist_tree: bool = False,
    persist_defaults: bool = False,
    order_check: bool = False,
    settings: Any = None,
) -> tuple[
    ProviderMutationEffects,
    NativeReplProviderState,
    NativeToolCapabilities,
    SessionGenerationRef,
    CodingEffectCoordinator,
    NativeSessionTree,
    list[str],
]:
    session_lock = threading.RLock()
    generation_ref = _generation_ref(session_lock)
    mutation_lock = (
        _OrderCheckingMutationLock(session_lock) if order_check else threading.RLock()
    )
    coordinator = CodingEffectCoordinator(mutation_lock)
    tree = NativeSessionTree.create(
        tmp_path,
        session_dir=tmp_path / "sessions",
        persist=persist_tree,
    )
    ctl = _ctl(coordinator, tree)
    ctl.generation_ref = generation_ref
    state = NativeReplProviderState(
        selection=NativeModelSelection("openai", "gpt-5.5"),
        model_runtime=ModelRuntime(
            ProviderCatalogState(
                models_json_path=tmp_path / "models.json",
                auth_store=AuthStore(path=tmp_path / "auth.json"),
                env={"OPENAI_API_KEY": "test-only"},
                openai_codex_auth_path=tmp_path / "missing-codex.json",
            )
        ),
        defaults_store=(
            NativeDefaultsStore(tmp_path / "defaults.json")
            if persist_defaults
            else None
        ),
        persist_defaults=persist_defaults,
    )
    state.bind_state_lock(session_lock)
    initial_provider = state.current_provider()
    coding_state = CodingSessionState(
        provider=initial_provider,
        provider_name=state.current_selection().provider_name,
        model_id=state.current_selection().model_id,
        state_lock=session_lock,
    )
    tools = NativeToolCapabilities(
        production_tool_registry(),
        {},
        workspace_root=tmp_path,
        reference_roots=(),
        stderr_sink=lambda _text: None,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=1.0,
        state_lock=session_lock,
    )
    footers: list[str] = []
    effects = ProviderMutationEffects(
        provider_state=state,
        ctl=ctl,
        extension_operations=cast(Any, None),
        coding_state=coding_state,
        product_session=cast(Any, None),
        terminal_ui=None,
        tool_capabilities=tools,
        settings=cast(Any, settings),
        cwd=tmp_path,
        input_stream=io.StringIO(),
        error_stream=io.StringIO(),
        refresh_footer_text=lambda: footers.append("footer"),
        extension_notify=lambda _kind, _message: None,
        mutation_io_lock=coordinator.lock,
        provider_turn_executor=ProviderTurnExecutor(),
        abort_event=None,
    )
    return effects, state, tools, generation_ref, coordinator, tree, footers


@pytest.mark.parametrize("family", ["tools", "thinking"])
@pytest.mark.parametrize("boundary", ["stale", "publication-pending", "terminal"])
def test_selection_mutations_refuse_without_any_effect(
    tmp_path: Path, family: str, boundary: str
) -> None:
    effects, state, tools, ref, coordinator, tree, footers = _provider_mutation_fixture(
        tmp_path
    )
    control = effects.model_runtime_control(0)
    before_tools = tools.state
    before_thinking = state.current_thinking_level()
    before_entries = tree.get_entries()

    scope: AbstractContextManager[None]
    if boundary == "stale":
        ref.publish(ref.current)
        scope = nullcontext()
    elif boundary == "publication-pending":
        scope = ref.publishing()
    else:
        retirement = GenerationMessageRetirement()
        with coordinator.terminal_section():
            with ref.lock:
                ref.detach_terminal_locked(retirement)
        retirement.finalize_retirement()
        scope = nullcontext()

    with scope:
        if family == "tools":
            assert control.set_active_tools_fn is not None
            result = control.set_active_tools_fn(())
        else:
            assert control.set_thinking_level_fn is not None
            result = control.set_thinking_level_fn("low")

    assert result is False
    assert tools.state is before_tools
    assert state.current_thinking_level() == before_thinking
    assert tree.get_entries() == before_entries
    assert footers == []


@pytest.mark.parametrize("family", ["tools", "thinking"])
def test_selection_call_admitted_before_gate_open_survives_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    family: str,
) -> None:
    effects, state, tools, ref, _coordinator, tree, footers = (
        _provider_mutation_fixture(tmp_path)
    )
    control = effects.model_runtime_control(0)
    entered = threading.Event()
    release = threading.Event()
    gate_open = threading.Event()
    results: list[bool] = []
    candidate_tools = tools.prepare_extensions({})

    if family == "tools":
        set_active_tools = control.set_active_tools_fn
        assert set_active_tools is not None
        original = NativeToolCapabilities.set_active_tools

        def blocked(owner: NativeToolCapabilities, names: Sequence[str]) -> bool:
            entered.set()
            assert release.wait(1)
            return original(owner, names)

        monkeypatch.setattr(NativeToolCapabilities, "set_active_tools", blocked)

        def mutate() -> None:
            results.append(set_active_tools(("read",)))
    else:
        set_thinking_level = control.set_thinking_level_fn
        assert set_thinking_level is not None
        original_thinking = NativeReplProviderState.set_supported_thinking_level

        def blocked_thinking(owner: NativeReplProviderState, level: str) -> str | None:
            entered.set()
            assert release.wait(1)
            return original_thinking(owner, level)

        monkeypatch.setattr(
            NativeReplProviderState,
            "set_supported_thinking_level",
            blocked_thinking,
        )

        def mutate() -> None:
            results.append(set_thinking_level("low"))

    def publish() -> None:
        with ref.publishing():
            gate_open.set()
            ref.publish(ref.current)
            tools.publish(candidate_tools)

    mutation_thread = threading.Thread(target=mutate)
    mutation_thread.start()
    assert entered.wait(1)
    publication_thread = threading.Thread(target=publish)
    publication_thread.start()
    assert not gate_open.wait(0.05)
    release.set()
    mutation_thread.join(1)
    publication_thread.join(1)

    assert results == [True] and gate_open.is_set()
    if family == "tools":
        assert tools.state.active_tool_names == frozenset({"read"})
        assert tree.get_entries() == [] and footers == []
    else:
        assert state.current_thinking_level() == "low"
        entries = tree.get_entries()
        assert len(entries) == 1
        thinking_entry = entries[0]
        assert isinstance(thinking_entry, ThinkingLevelChangeEntry)
        assert thinking_entry.thinking_level == "low"
        assert footers == ["footer"]


def test_concurrent_thinking_keeps_memory_jsonl_order_and_unlocks_before_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    effects, state, _tools, ref, coordinator, tree, footers = (
        _provider_mutation_fixture(tmp_path, persist_tree=True)
    )
    control = effects.model_runtime_control(0)
    set_thinking_level = control.set_thinking_level_fn
    assert set_thinking_level is not None
    first_write = threading.Event()
    release_write = threading.Event()
    session_owned_during_io: list[bool] = []
    original_write = tree._write_entry
    writes = 0

    def blocking_write(entry: SessionEntry) -> None:
        nonlocal writes
        writes += 1
        session_owned_during_io.append(cast(Any, ref.lock)._is_owned())
        if writes == 1:
            first_write.set()
            assert release_write.wait(1)
        original_write(entry)

    monkeypatch.setattr(tree, "_write_entry", blocking_write)

    results: list[bool] = []
    first = threading.Thread(target=lambda: results.append(set_thinking_level("low")))
    second = threading.Thread(target=lambda: results.append(set_thinking_level("high")))
    first.start()
    assert first_write.wait(1)
    second.start()
    second.join(0.05)
    assert second.is_alive()
    release_write.set()
    first.join(1)
    second.join(1)

    levels: list[str] = []
    for entry in tree.get_entries():
        assert isinstance(entry, ThinkingLevelChangeEntry)
        levels.append(entry.thinking_level)
    assert tree.path is not None
    durable = [json.loads(line) for line in tree.path.read_text().splitlines()][1:]
    assert results == [True, True]
    assert state.current_thinking_level() == "high"
    assert levels == ["low", "high"]
    assert [row["thinkingLevel"] for row in durable] == levels
    assert session_owned_during_io == [False, False]
    assert footers == ["footer", "footer"]
    assert not cast(Any, coordinator.lock)._is_owned()


def test_thinking_lock_order_instrumentation_rejects_reverse_edge(
    tmp_path: Path,
) -> None:
    effects, state, _tools, ref, _coordinator, tree, _footers = (
        _provider_mutation_fixture(tmp_path, order_check=True)
    )
    with ref.lock:
        with pytest.raises(RuntimeError, match="under session mutex"):
            effects.extension_set_thinking_level(0, "low")
    assert state.current_thinking_level() is None
    assert tree.get_entries() == []
    assert effects.extension_set_thinking_level(0, "low") is True


def _model_state_snapshot(
    effects: ProviderMutationEffects,
    state: NativeReplProviderState,
    footers: list[str],
) -> tuple[object, ...]:
    coding = effects.coding_state
    return (
        state.capture_model_mutation_state(),
        coding.provider_binding,
        coding.messages,
        coding.usage_snapshot(),
        coding.compaction_suffix,
        coding.compaction_count,
        tuple(footers),
    )


def _block_model_construction(
    monkeypatch: pytest.MonkeyPatch,
    ref: SessionGenerationRef,
    *,
    model_id: str = "gpt-5.4",
) -> tuple[threading.Event, threading.Event, list[bool]]:
    entered = threading.Event()
    release = threading.Event()
    lock_observations: list[bool] = []
    original = ModelRuntime.construct

    def blocked(
        runtime: ModelRuntime,
        selection: NativeModelSelection,
        *,
        thinking_level: str | None,
        options: Any,
    ) -> Any:
        lock_observations.append(cast(Any, ref.lock)._is_owned())
        if selection.model_id == model_id:
            entered.set()
            assert release.wait(1)
        return original(
            runtime,
            selection,
            thinking_level=thinking_level,
            options=options,
        )

    monkeypatch.setattr(ModelRuntime, "construct", blocked)
    return entered, release, lock_observations


def test_rpc_model_admission_wins_detached_preparation_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An accepted native reservation must prevent a prepared rebind publishing."""

    effects, state, _tools, ref, _coordinator, tree, footers = (
        _provider_mutation_fixture(tmp_path, persist_defaults=True)
    )
    control = _NativeSessionControl(CodingInputQueue())
    port = effects.rpc_configuration_port(control.publish_if_true_idle)
    coding = effects.coding_state
    history = AgentUserMessage(content=ProductContent("already visible"))
    coding.append_message(history)
    coding.absorb_usage(AgentProviderUsageSample(input_tokens=7, total_tokens=7))
    before = (
        state.capture_model_mutation_state(),
        coding.provider_binding,
        coding.messages,
        coding._usage_accumulator,
        coding.usage_snapshot(),
        tuple(tree.get_entries()),
        control.snapshot(),
        tuple(footers),
    )
    entered, release, lock_observations = _block_model_construction(monkeypatch, ref)
    results: list[object] = []
    worker = threading.Thread(
        target=lambda: results.append(
            port.set_model(NativeModelSelection("openai", "gpt-5.4"))
        )
    )
    worker.start()
    assert entered.wait(1)
    admitted = control.admit_prompt(ProductContent("reserved while preparing"))
    assert admitted.reservation is not None
    release.set()
    worker.join(1)

    assert not worker.is_alive()
    result = results[0]
    assert getattr(result, "success") is False
    assert getattr(result, "diagnostic") == "session is not idle"
    after = (
        state.capture_model_mutation_state(),
        coding.provider_binding,
        coding.messages,
        coding._usage_accumulator,
        coding.usage_snapshot(),
        tuple(tree.get_entries()),
        tuple(footers),
    )
    assert after == (*before[:6], before[-1])
    assert coding._usage_accumulator is before[3]
    assert control.snapshot() == admitted
    assert lock_observations and not any(lock_observations)


def test_rpc_configuration_gate_excludes_admission_after_detached_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The final assignment holds the native gate, after unlocked construction."""

    effects, state, _tools, ref, _coordinator, _tree, _footers = (
        _provider_mutation_fixture(tmp_path)
    )
    control = _NativeSessionControl(CodingInputQueue())
    port = effects.rpc_configuration_port(control.publish_if_true_idle)
    entered_commit = threading.Event()
    release_commit = threading.Event()
    construction_locks: list[bool] = []
    original_construct = ModelRuntime.construct

    def observed_construct(
        runtime: ModelRuntime,
        selection: NativeModelSelection,
        *,
        thinking_level: str | None,
        options: Any,
    ) -> Any:
        construction_locks.append(cast(Any, ref.lock)._is_owned())
        return original_construct(
            runtime, selection, thinking_level=thinking_level, options=options
        )

    original_publish = _NativeSessionControl.publish_if_true_idle

    def blocked_publish(
        owner: _NativeSessionControl, publish: Callable[[], None]
    ) -> bool:
        if owner is not control:
            return original_publish(owner, publish)
        with owner._gate:
            entered_commit.set()
            assert release_commit.wait(1)
            publish()
            return True

    monkeypatch.setattr(ModelRuntime, "construct", observed_construct)
    monkeypatch.setattr(_NativeSessionControl, "publish_if_true_idle", blocked_publish)
    # The port captured the method before monkeypatching; bind a fresh port.
    port = effects.rpc_configuration_port(control.publish_if_true_idle)
    results: list[object] = []
    mutation = threading.Thread(
        target=lambda: results.append(
            port.set_model(NativeModelSelection("openai", "gpt-5.4"))
        )
    )
    mutation.start()
    assert entered_commit.wait(1)
    admission: list[object] = []
    prompt = threading.Thread(
        target=lambda: admission.append(control.admit_prompt(ProductContent("next")))
    )
    prompt.start()
    assert not admission
    release_commit.set()
    mutation.join(1)
    prompt.join(1)

    assert getattr(results[0], "success") is True
    assert admission and getattr(admission[0], "reservation") is not None
    assert state.current_selection() == NativeModelSelection("openai", "gpt-5.4")
    assert effects.coding_state.provider_binding.model_id == "gpt-5.4"
    assert construction_locks and not any(construction_locks)


def test_rpc_model_resets_history_and_usage_but_thinking_refresh_retains_them(
    tmp_path: Path,
) -> None:
    effects, state, _tools, _ref, _coordinator, tree, _footers = (
        _provider_mutation_fixture(tmp_path)
    )
    control = _NativeSessionControl(CodingInputQueue())
    port = effects.rpc_configuration_port(control.publish_if_true_idle)
    coding = effects.coding_state
    message = AgentUserMessage(content=ProductContent("retained only for thinking"))
    coding.append_message(message)
    coding.absorb_usage(AgentProviderUsageSample(input_tokens=9, total_tokens=9))
    durable_before = tuple(tree.get_entries())
    old_usage = coding._usage_accumulator

    model = port.set_model(NativeModelSelection("openai", "gpt-5.4"))
    assert model.success
    assert coding.messages == ()
    assert coding._usage_accumulator is not old_usage
    assert coding.usage_snapshot().usage.input_tokens == 0
    assert tuple(tree.get_entries()) == durable_before

    coding.append_message(message)
    coding.absorb_usage(AgentProviderUsageSample(input_tokens=11, total_tokens=11))
    binding_before = coding.provider_binding
    usage_before = coding._usage_accumulator
    usage_snapshot = coding.usage_snapshot()
    history_before = coding.messages
    thinking = port.set_thinking_level("high")
    assert thinking.success
    assert state.current_selection() == NativeModelSelection("openai", "gpt-5.4")
    assert coding.provider_binding.provider is not binding_before.provider
    assert coding.messages == history_before
    assert coding._usage_accumulator is usage_before
    assert coding.usage_snapshot() == usage_snapshot


def test_rpc_post_commit_thinking_append_failure_is_sanitized_and_successful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    effects, _state, _tools, _ref, _coordinator, tree, _footers = (
        _provider_mutation_fixture(tmp_path)
    )
    control = _NativeSessionControl(CodingInputQueue())
    port = effects.rpc_configuration_port(control.publish_if_true_idle)

    def fail_append(_level: str) -> None:
        raise RuntimeError("secret=must-not-leak")

    monkeypatch.setattr(tree, "append_thinking_level_change", fail_append)
    result = port.set_thinking_level("high")
    diagnostic = cast(io.StringIO, effects.error_stream).getvalue()

    assert result.success
    assert effects.coding_state.provider_binding.provider is not None
    assert diagnostic.count("durable append failed") == 1
    assert "RuntimeError" in diagnostic
    assert "must-not-leak" not in diagnostic


def test_rpc_catalog_filters_tool_capability_and_cycles_scoped_or_unscoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RPC projects only usable rows, retaining active custom selections."""

    (tmp_path / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "acme": {
                        "baseUrl": "http://127.0.0.1:9000/v1",
                        "apiKey": "local-test-key",
                        "api": "openai-completions",
                        "models": [{"id": "rocket-1"}, {"id": "rocket-2"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    enabled = ["openai/gpt-5.4", "acme/rocket-1"]
    settings = SimpleNamespace(get_enabled_models=lambda: enabled)
    effects, state, _tools, _ref, _coordinator, _tree, _footers = (
        _provider_mutation_fixture(tmp_path, settings=settings)
    )
    control = _NativeSessionControl(CodingInputQueue())
    port = effects.rpc_configuration_port(control.publish_if_true_idle)
    original_construct = ModelRuntime.construct

    def no_tools_for_gpt4o(
        runtime: ModelRuntime,
        selection: NativeModelSelection,
        *,
        thinking_level: str | None,
        options: Any,
    ) -> Any:
        if selection == NativeModelSelection("openai", "gpt-4o"):
            from pipy_harness.native.fake import FakeNativeProvider

            return FakeNativeProvider(model_id="gpt-4o", supports_tool_calls=False)
        return original_construct(
            runtime, selection, thinking_level=thinking_level, options=options
        )

    monkeypatch.setattr(ModelRuntime, "construct", no_tools_for_gpt4o)
    available = port.available_models()
    assert NativeModelSelection("acme", "rocket-1") in available
    assert NativeModelSelection("anthropic", "claude-opus-4-7") not in available
    assert NativeModelSelection("openai", "gpt-4o") not in available
    before = state.capture_model_mutation_state()
    refused = port.set_model(NativeModelSelection("openai", "gpt-4o"))
    assert not refused.success
    assert state.capture_model_mutation_state() == before

    state.replace_selection(NativeModelSelection("openai", "gpt-4o"))
    assert NativeModelSelection("openai", "gpt-4o") not in port.available_models()
    enabled.clear()
    unscoped_from_filtered_active = port.cycle_model()
    assert unscoped_from_filtered_active is not None
    assert unscoped_from_filtered_active.is_scoped is False
    assert unscoped_from_filtered_active.result.success
    assert unscoped_from_filtered_active.result.snapshot is not None
    assert unscoped_from_filtered_active.result.snapshot.selection != (
        NativeModelSelection("openai", "gpt-4o")
    )

    state.replace_selection(NativeModelSelection("anthropic", "claude-opus-4-7"))
    assert NativeModelSelection("anthropic", "claude-opus-4-7") not in (
        port.available_models()
    )

    state.replace_selection(NativeModelSelection("acme", "outside-catalog"))
    assert NativeModelSelection("acme", "outside-catalog") in port.available_models()
    enabled[:] = ["openai/gpt-5.4", "acme/rocket-1"]
    scoped = port.cycle_model()
    assert scoped is not None and scoped.is_scoped is True
    assert scoped.result.success
    assert scoped.result.snapshot is not None
    assert (
        scoped.result.snapshot.selection,
        scoped.result.snapshot.thinking_level,
        scoped.is_scoped,
    ) == (NativeModelSelection("openai", "gpt-5.4"), "off", True)

    enabled[:] = ["acme/rocket-*"]
    state.replace_selection(NativeModelSelection("acme", "outside-catalog"))
    glob_scoped = port.cycle_model()
    assert glob_scoped is not None and glob_scoped.is_scoped is True
    assert glob_scoped.result.success
    assert glob_scoped.result.snapshot is not None
    assert glob_scoped.result.snapshot.selection == NativeModelSelection(
        "acme", "rocket-1"
    )

    enabled[:] = ["missing/*"]
    unscoped = port.cycle_model()
    assert unscoped is not None and unscoped.is_scoped is False
    assert unscoped.result.success
    assert unscoped.result.snapshot is not None
    assert unscoped.result.snapshot.selection != NativeModelSelection(
        "acme", "rocket-1"
    )


def test_rpc_cycle_returns_null_for_zero_or_one_selectable_catalog_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    effects, state, _tools, _ref, _coordinator, _tree, _footers = (
        _provider_mutation_fixture(
            tmp_path,
            settings=SimpleNamespace(get_enabled_models=lambda: []),
        )
    )
    control = _NativeSessionControl(CodingInputQueue())
    port = effects.rpc_configuration_port(control.publish_if_true_idle)
    active = NativeModelSelection("openai", "gpt-5.5")
    state.replace_selection(active)

    monkeypatch.setattr(
        NativeReplProviderState,
        "model_options",
        lambda _state: [NativeModelOption(active, available=False)],
    )
    assert port.available_models() == ()
    assert port.cycle_model() is None

    monkeypatch.setattr(
        NativeReplProviderState,
        "model_options",
        lambda _state: [
            NativeModelOption(active, available=False),
            NativeModelOption(
                NativeModelSelection("openai", "gpt-5.4"), available=True
            ),
        ],
    )
    assert port.available_models() == (NativeModelSelection("openai", "gpt-5.4"),)
    assert port.cycle_model() is None


@pytest.mark.parametrize(
    "boundary",
    [
        "stale-after-prepare",
        "gate-open-during-prepare",
        "terminal-during-prepare",
    ],
)
def test_model_mutation_refuses_when_admission_changes_during_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    effects, state, _tools, ref, coordinator, _tree, footers = (
        _provider_mutation_fixture(tmp_path)
    )
    control = effects.model_runtime_control(0)
    set_model = control.set_model_fn
    assert set_model is not None
    before = _model_state_snapshot(effects, state, footers)
    entered, release, lock_observations = _block_model_construction(monkeypatch, ref)
    results: list[bool] = []
    worker = threading.Thread(
        target=lambda: results.append(set_model("openai/gpt-5.4"))
    )
    worker.start()
    assert entered.wait(1)

    closer: threading.Thread | None = None
    if boundary == "stale-after-prepare":
        ref.publish(ref.current)
        release.set()
        worker.join(1)
    elif boundary == "gate-open-during-prepare":
        with ref.publishing():
            release.set()
            worker.join(1)
    else:
        close_finished = threading.Event()
        closer = threading.Thread(
            target=lambda: _close_effects(coordinator, close_finished)
        )
        closer.start()
        deadline = time.monotonic() + 1
        while not coordinator.terminal and time.monotonic() < deadline:
            pass
        assert coordinator.terminal
        release.set()
        worker.join(1)
        closer.join(1)
        assert close_finished.is_set()
    assert not worker.is_alive()
    assert closer is None or not closer.is_alive()
    assert results == [False]
    assert _model_state_snapshot(effects, state, footers) == before
    assert lock_observations == [False]


def test_prepared_model_never_overwrites_a_newer_same_generation_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effects, state, _tools, ref, _coordinator, _tree, footers = (
        _provider_mutation_fixture(tmp_path)
    )
    set_model = effects.model_runtime_control(0).set_model_fn
    assert set_model is not None
    entered, release, _locks = _block_model_construction(monkeypatch, ref)
    results: list[bool] = []
    worker = threading.Thread(
        target=lambda: results.append(set_model("openai/gpt-5.4"))
    )
    worker.start()
    assert entered.wait(1)

    newer_ok, newer_message = effects.apply_model_selection("openai/gpt-4o")
    assert newer_ok, newer_message
    newer_binding = effects.coding_state.provider_binding
    release.set()
    worker.join(1)

    assert results == [False]
    assert state.current_selection() == NativeModelSelection("openai", "gpt-4o")
    assert effects.coding_state.provider_binding is newer_binding
    assert footers == ["footer"]


def test_model_provider_construction_failure_is_safe_and_non_mutating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effects, state, _tools, ref, _coordinator, _tree, footers = (
        _provider_mutation_fixture(tmp_path)
    )
    before = _model_state_snapshot(effects, state, footers)
    lock_observations: list[bool] = []

    def fail_construct(*_args: object, **_kwargs: object) -> Any:
        lock_observations.append(cast(Any, ref.lock)._is_owned())
        raise RuntimeError("credential=must-not-leak")

    monkeypatch.setattr(ModelRuntime, "construct", fail_construct)
    ok, message = effects.apply_model_selection("openai/gpt-5.4")
    set_model = effects.model_runtime_control(0).set_model_fn
    assert set_model is not None

    assert not ok and set_model("openai/gpt-5.4") is False
    assert "RuntimeError" in message
    assert "must-not-leak" not in message
    assert cast(io.StringIO, effects.error_stream).getvalue() == ""
    assert _model_state_snapshot(effects, state, footers) == before
    assert lock_observations == [False, False]


def test_model_persistence_failure_is_post_commit_and_fail_soft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effects, state, _tools, ref, _coordinator, _tree, footers = (
        _provider_mutation_fixture(tmp_path, persist_defaults=True)
    )
    assert state.defaults_store is not None
    save_locks: list[bool] = []

    def fail_save(_selection: NativeModelSelection) -> None:
        save_locks.append(cast(Any, ref.lock)._is_owned())
        raise OSError("private filesystem detail")

    monkeypatch.setattr(state.defaults_store, "save", fail_save)
    set_model = effects.model_runtime_control(0).set_model_fn
    assert set_model is not None

    assert set_model("openai/gpt-5.4") is True
    assert state.current_selection() == NativeModelSelection("openai", "gpt-5.4")
    assert effects.coding_state.model_id == "gpt-5.4"
    assert state.pending_default_value() is None
    assert state.defaults_store.load() is None
    assert footers == ["footer"]
    assert save_locks == [False]
    assert (
        "private filesystem detail"
        not in cast(io.StringIO, effects.error_stream).getvalue()
    )


def test_successful_model_commit_preserves_rebind_contract_for_current_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effects, state, _tools, ref, _coordinator, _tree, footers = (
        _provider_mutation_fixture(tmp_path, persist_defaults=True)
    )
    coding = effects.coding_state
    message = AgentUserMessage(content=ProductContent("prior context"))
    coding.append_message(AgentUserMessage(content=ProductContent("removed")))
    coding.append_message(message)
    coding.absorb_usage(AgentProviderUsageSample(input_tokens=7, total_tokens=7))
    coding.apply_compaction(
        (message,),
        summary_suffix="\nretained compaction",
        dropped_group_count=1,
        dropped_message_count=1,
    )
    construct_locks: list[bool] = []
    save_locks: list[bool] = []
    original_construct = ModelRuntime.construct
    assert state.defaults_store is not None
    original_save = state.defaults_store.save

    def observed_construct(
        runtime: ModelRuntime,
        selection: NativeModelSelection,
        *,
        thinking_level: str | None,
        options: Any,
    ) -> Any:
        construct_locks.append(cast(Any, ref.lock)._is_owned())
        return original_construct(
            runtime,
            selection,
            thinking_level=thinking_level,
            options=options,
        )

    def observed_save(selection: NativeModelSelection) -> None:
        save_locks.append(cast(Any, ref.lock)._is_owned())
        original_save(selection)

    monkeypatch.setattr(ModelRuntime, "construct", observed_construct)
    monkeypatch.setattr(state.defaults_store, "save", observed_save)
    hook_results: list[bool] = []

    def switch_model(_event: object, ctx: Any) -> None:
        hook_results.append(ctx.set_model("openai/gpt-5.4:high"))

    dispatch_before_agent_start_hooks(
        (switch_model,),
        cwd=str(tmp_path),
        has_ui=False,
        model_runtime=effects.model_runtime_control(0),
    )

    assert hook_results == [True]
    assert state.current_selection() == NativeModelSelection("openai", "gpt-5.4")
    assert state.current_thinking_level() == "high"
    assert coding.provider is coding.provider_binding.provider
    assert (coding.provider_name, coding.model_id) == ("openai", "gpt-5.4")
    assert coding.messages == ()
    assert coding.usage.input_tokens == 0
    assert coding.compaction_suffix == "\nretained compaction"
    assert coding.compaction_count == 1
    assert state.defaults_store.load() == NativeModelSelection("openai", "gpt-5.4")
    assert state.pending_default_value() is None
    assert footers == ["footer"]
    assert construct_locks == [False]
    assert save_locks == [False]


def test_retained_model_callable_after_terminal_cannot_prepare_or_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effects, state, _tools, ref, coordinator, _tree, footers = (
        _provider_mutation_fixture(tmp_path, persist_defaults=True)
    )
    set_model = effects.model_runtime_control(0).set_model_fn
    assert set_model is not None
    before = _model_state_snapshot(effects, state, footers)
    constructions: list[str] = []
    saves: list[NativeModelSelection] = []
    monkeypatch.setattr(
        ModelRuntime,
        "construct",
        lambda *_args, **_kwargs: constructions.append("constructed"),
    )
    assert state.defaults_store is not None
    monkeypatch.setattr(state.defaults_store, "save", saves.append)

    retirement = GenerationMessageRetirement()
    with coordinator.terminal_section():
        with ref.lock:
            ref.detach_terminal_locked(retirement)
    retirement.finalize_retirement()

    assert set_model("openai/gpt-5.4") is False
    assert constructions == []
    assert saves == []
    assert _model_state_snapshot(effects, state, footers) == before


def _stale_run_provider_result(
    boundary: str,
    owners: list[ProviderMutationEffects],
    requests: list[object],
    mutate: Callable[[], None],
    request: object,
) -> ProviderResult:
    assert not cast(Any, owners[0].coding_state.state_lock)._is_owned()
    assert not cast(Any, owners[0].ctl.coding_effects.lock)._is_owned()
    requests.append(request)
    if boundary == "provider-return":
        mutate()
    now = datetime.now(UTC)
    calls: tuple[ProviderToolCall, ...] = ()
    if boundary == "later-turn" and len(requests) == 1:
        calls = (
            ProviderToolCall(
                provider_correlation_id="read-1",
                tool_name="read",
                arguments_json='{"path":"notes.txt"}',
            ),
        )
    return ProviderResult(
        status=HarnessStatus.SUCCEEDED,
        provider_name="openai",
        model_id="gpt-5.5",
        started_at=now,
        ended_at=now,
        final_text="answer" if not calls else "",
        tool_calls=calls,
        usage={"input_tokens": 7},
    )


class _StaleRunMutationSink:
    def __init__(self, boundary: str, mutate: Callable[[], None]) -> None:
        self.boundary = boundary
        self.mutate = mutate

    def emit(self, event: object) -> None:
        boundary = self.boundary
        if (
            (boundary == "run-start" and isinstance(event, AgentRunStarted))
            or (boundary == "turn-start" and isinstance(event, TurnStarted))
            or (
                boundary == "later-turn"
                and isinstance(event, TurnStarted)
                and event.turn_index == 1
            )
            or (boundary == "run-finish" and isinstance(event, AgentRunCompleted))
        ):
            self.mutate()


@pytest.mark.parametrize(
    "boundary",
    [
        "run-start",
        "after-mirror",
        "request-hooks",
        "turn-start",
        "later-turn",
        "provider-return",
        "run-finish",
    ],
)
def test_retained_model_control_stops_stale_coding_run_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    from pipy_harness.native.agent_loop_policy import NativeAgentProviderRequestPolicy
    from pipy_harness.native.coding.session import CodingSession
    from pipy_harness.native.coding.state import CodingContextChangedError

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    _effects, provider_state, _tools, _ref, _coordinator, _tree, _footers = (
        _provider_mutation_fixture(tmp_path)
    )
    retained: list[Callable[[str], bool]] = []
    owners: list[ProviderMutationEffects] = []
    original_control = ProviderMutationEffects.model_runtime_control

    def capture_control(
        owner: ProviderMutationEffects, *args: Any, **kwargs: Any
    ) -> Any:
        control = original_control(owner, *args, **kwargs)
        if control.set_model_fn is not None:
            retained.append(control.set_model_fn)
            owners.append(owner)
        return control

    monkeypatch.setattr(
        ProviderMutationEffects, "model_runtime_control", capture_control
    )
    mutations: list[object] = []

    def mutate() -> None:
        if mutations:
            return
        assert retained and retained[0]("openai/gpt-5.4")
        owner = owners[0]
        mutations.append(
            (
                owner.coding_state.provider_binding,
                owner.coding_state.messages,
                owner.ctl.session_tree.get_entries(),
            )
        )

    original_append = NativeSessionTree.append_message
    append_checks: list[object] = []

    def append(tree: NativeSessionTree, message: Any) -> Any:
        owner = owners[0]
        assert cast(Any, owner.ctl.coding_effects.lock)._is_owned()
        assert not cast(Any, owner.coding_state.state_lock)._is_owned()
        assert owner.coding_state.messages[-1] is message
        append_checks.append(message)
        return original_append(tree, message)

    monkeypatch.setattr(NativeSessionTree, "append_message", append)
    requests: list[object] = []

    provider = provider_state.current_provider()
    monkeypatch.setattr(
        type(provider),
        "complete",
        lambda _provider, request, **_kwargs: _stale_run_provider_result(
            boundary, owners, requests, mutate, request
        ),
    )
    (tmp_path / "notes.txt").write_text("tool evidence", encoding="utf-8")
    from pipy_harness.native.repl.loop_step import _RequestPreparationEffects

    original_compact = _RequestPreparationEffects._compact_if_needed

    def compact(preparation: Any, *args: Any, **kwargs: Any) -> Any:
        result = original_compact(preparation, *args, **kwargs)
        if boundary == "after-mirror":
            mutate()
        return result

    monkeypatch.setattr(_RequestPreparationEffects, "_compact_if_needed", compact)
    original_prepare = NativeAgentProviderRequestPolicy.prepare

    def prepare(policy: Any, request: Any) -> Any:
        assert not cast(Any, owners[0].coding_state.state_lock)._is_owned()
        assert not cast(Any, owners[0].ctl.coding_effects.lock)._is_owned()
        result = original_prepare(policy, request)
        if boundary == "request-hooks":
            mutate()
        return result

    monkeypatch.setattr(NativeAgentProviderRequestPolicy, "prepare", prepare)

    session = CodingSession(
        provider=provider,
        provider_state=provider_state,
        agent_event_sink=_StaleRunMutationSink(boundary, mutate),
    )
    with pytest.raises(CodingContextChangedError, match="session stopped"):
        session.run(
            workspace_root=tmp_path,
            input_stream=io.StringIO("accepted input\n/exit\n"),
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        )
    assert len(mutations) == 1
    owner = owners[0]
    binding, messages, entries = cast(tuple[Any, Any, Any], mutations[0])
    assert owner.coding_state.provider_binding is binding
    assert owner.coding_state.messages == messages == ()
    assert owner.coding_state.usage.input_tokens == 0
    assert owner.ctl.session_tree.get_entries() == entries
    assert owner.ctl.coding_effects.terminal
    assert retained[0]("openai/gpt-4o") is False
    # The run's finally released the witness despite exceptional lifetime cleanup.
    witness = owner.coding_state.begin_agent_run()
    owner.coding_state.end_agent_run(witness)
    assert len(requests) == (
        1 if boundary in {"later-turn", "provider-return", "run-finish"} else 0
    )
    assert (
        len(append_checks)
        == {
            "run-start": 0,
            "after-mirror": 0,
            "request-hooks": 0,
            "turn-start": 0,
            "later-turn": 3,
            "provider-return": 1,
            "run-finish": 2,
        }[boundary]
    )


@pytest.mark.parametrize("trigger", ["auto", "manual"])
def test_compaction_gate_model_replacement_stops_only_an_active_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trigger: str
) -> None:
    from pipy_harness.native.coding.session import CodingSession
    from pipy_harness.native.coding.state import CodingContextChangedError
    from pipy_harness.native.session_tree import MessageEntry
    from pipy_harness.native.settings import SettingsManager

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    _effects, provider_state, _tools, _ref, _coordinator, _tree, _footers = (
        _provider_mutation_fixture(tmp_path)
    )
    evidence = tmp_path / "gate-evidence.txt"
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "compaction_gate.py").write_text(
        "from pathlib import Path\n"
        f"EVIDENCE = Path({str(evidence)!r})\n"
        "def record(value):\n"
        "    with EVIDENCE.open('a', encoding='utf-8') as file:\n"
        "        file.write(value + '\\n')\n"
        "def activate(api):\n"
        "    @api.on('session_before_compact')\n"
        "    def compact(event, ctx):\n"
        "        changed = ctx.set_model('openai/gpt-5.4')\n"
        "        record(f'gate:{event.trigger}:{changed}')\n"
        "    @api.on('before_provider_request')\n"
        "    def provider(_event, ctx):\n"
        "        record(f'provider-model-denied:{ctx.set_model(\"openai/gpt-4o\")}')\n"
        "    @api.on('session_shutdown')\n"
        "    def shutdown(_event, _ctx):\n"
        "        record('shutdown')\n",
        encoding="utf-8",
    )
    settings = SettingsManager(
        global_path=tmp_path / "settings.json",
        env={},
        overrides={"compaction": {"contextWindow": 1, "reserveTokens": 0}}
        if trigger == "auto"
        else {},
    )
    provider = provider_state.current_provider()
    requests: list[object] = []

    def complete(
        _provider: object, request: object, **_kwargs: object
    ) -> ProviderResult:
        requests.append(request)
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name="openai",
            model_id="gpt-5.4",
            started_at=now,
            ended_at=now,
            final_text="answer",
        )

    monkeypatch.setattr(type(provider), "complete", complete)
    tree = NativeSessionTree.create(tmp_path, persist=False)
    for text in ("older", "recent", "latest"):
        tree.append_message(AgentUserMessage(ProductContent(text)))
    prior_messages = tuple(
        entry for entry in tree.get_entries() if isinstance(entry, MessageEntry)
    )
    session = CodingSession(
        provider=provider,
        provider_state=provider_state,
        native_session=tree,
        settings_manager=settings,
    )
    inputs = (
        "accepted input\n/exit\n"
        if trigger == "auto"
        else "/compact\naccepted input\n/exit\n"
    )

    def run() -> Any:
        return session.run(
            workspace_root=tmp_path,
            input_stream=io.StringIO(inputs),
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        )

    if trigger == "auto":
        with pytest.raises(CodingContextChangedError, match="session stopped"):
            run()
        assert session._coding_state.messages == ()
        assert (
            tuple(
                entry for entry in tree.get_entries() if isinstance(entry, MessageEntry)
            )
            == prior_messages
        )
        assert requests == []
        assert evidence.read_text(encoding="utf-8").splitlines() == [
            "gate:auto:True",
            "shutdown",
        ]
    else:
        assert run().status is HarnessStatus.SUCCEEDED
        assert [
            message.content.value for message in session._coding_state.messages
        ] == ["accepted input", "answer"]
        assert len(requests) == 1
        assert evidence.read_text(encoding="utf-8").splitlines() == [
            "gate:manual:True",
            "provider-model-denied:False",
            "shutdown",
        ]
    assert session._coding_state.model_id == "gpt-5.4"
    assert session._coding_state.compaction_count == 0
    assert session._coding_state.usage.input_tokens == 0
    witness = session._coding_state.begin_agent_run()
    session._coding_state.end_agent_run(witness)
