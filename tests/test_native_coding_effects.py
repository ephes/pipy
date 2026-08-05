from __future__ import annotations

import io
import json
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from session_generation_test_support import build_test_projection

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import AgentUserMessage, ProductContent
from pipy_harness.native.agent.usage import AgentProviderUsageSample
from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.catalog_state import ProviderCatalogState
from pipy_harness.native.coding import CodingInputQueue
from pipy_harness.native.coding.effects import CodingEffectCoordinator
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.extension_hooks import (
    _compose_extension_runtime,
    dispatch_before_agent_start_hooks,
)
from pipy_harness.native.extension_runtime import (
    QueuedCustomMessage,
)
from pipy_harness.native.extensions.command_context import ExtensionCapabilityError
from pipy_harness.native.extensions.contracts import (
    RegisteredMessageRenderer,
)
from pipy_harness.native.extensions.message_routing import (
    GenerationMessageRetirement,
    GenerationMessageRouting,
)
from pipy_harness.native.repl.collaborators import SessionCollaborators
from pipy_harness.native.repl.loop_scope import RunControlState
from pipy_harness.native.repl.provider_selection import ProviderMutationEffects
from pipy_harness.native.repl_state import (
    ModelRuntime,
    NativeDefaultsStore,
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
from pipy_harness.native.tool_loop_session import production_tool_registry
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
    runtime = _compose_extension_runtime((), user, custom, routing)
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
    # Every rebind of the active pointer travelled to the command that performs
    # it -- `/new`, `/resume`, `/fork` and `/clone` to the session commands,
    # `/import` to the transfer verbs. The composition root rebinds none.
    assert collaborators.count("self.ctl.session_tree = ") == 0
    assert commands.count("self.ctl.session_tree = ") == 3
    assert transfer.count("self.ctl.session_tree = ") == 1


def _provider_mutation_fixture(
    tmp_path: Path,
    *,
    persist_tree: bool = False,
    persist_defaults: bool = False,
    order_check: bool = False,
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
        settings=cast(Any, None),
        cwd=tmp_path,
        input_stream=io.StringIO(),
        error_stream=io.StringIO(),
        refresh_footer_text=lambda: footers.append("footer"),
        extension_notify=lambda _kind, _message: None,
        mutation_io_lock=coordinator.lock,
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
    coding.append_message(message)
    coding.absorb_usage(AgentProviderUsageSample(input_tokens=7, total_tokens=7))
    coding.apply_compaction(
        (message,), summary_suffix="\nretained compaction", dropped_group_count=1
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
