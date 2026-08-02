from __future__ import annotations

import io
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import ProductContent
from pipy_harness.native.coding import CodingInputQueue
from pipy_harness.native.coding.effects import CodingEffectCoordinator
from pipy_harness.native.extension_runtime import (
    ExtensionCapabilityError,
    GenerationMessageRouting,
    QueuedCustomMessage,
    RegisteredMessageRenderer,
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
)
from pipy_harness.native.tool_loop_session import (
    _RunControlState,
    _SessionCollaborators,
)
from pipy_harness.native.tui import (
    _CustomEntryRenderer,
    _CustomRendererProjectionSnapshot,
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
    runtime = cast(
        Any, SimpleNamespace(message_routing=GenerationMessageRouting([], []))
    )
    return SessionGenerationRef(SessionExtensionGeneration(runtime), lock=lock)


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
        _SessionCollaborators,
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
            _SessionCollaborators.extension_complete(collaborators, "system", "user")
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
        _SessionCollaborators.extension_complete(
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
    renderer = _CustomEntryRenderer(
        session=SimpleNamespace(_emit_diagnostic=lambda *_args: None),
        ctl=state,
        terminal_ui=None,
        coding_input_queue=queue,
        coding_effects=coordinator,
        error_stream=io.StringIO(),
    )
    callback_reads: list[bool] = []

    def render(_data: object) -> list[str]:
        callback_reads.append(_guarded_read_finishes(lambda: tree.get_tree()))
        return ["rendered"]

    projection = _CustomRendererProjectionSnapshot(
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


def _ctl(c: CodingEffectCoordinator, tree: NativeSessionTree) -> _RunControlState:
    return _RunControlState(
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
        _SessionCollaborators, SimpleNamespace(coding_effects=coordinator, ctl=ctl)
    )
    writer = threading.Thread(
        target=lambda: _SessionCollaborators.extension_set_session_name(
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
    loop, tui = (
        (root / name).read_text() for name in ("tool_loop_session.py", "tui.py")
    )
    assert loop.count("with self.ctl.session_tree_section() as tree:") == 3
    assert tui.count("with self.coding_effects.lock:") == 2
    assert "with self.coding_effects.lock:\n            tree.bind_mutation_lock" in loop
    assert loop.count("self.ctl.session_tree = ") == 4
