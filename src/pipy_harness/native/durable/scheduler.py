"""Private sequential task execution on the existing Session authority (DE05b).

Adapted from Pi durable scheduler.ts at 2ec55d808d455b1a64a2bf7888198207a096120a,
MIT Copyright (c) 2025 Mario Zechner; see UPSTREAM-LICENSE.txt.
All scheduler metadata uses SessionLifecycle._cond, also the close/start guard.
User phases run on one owned asyncio host, never from a publication callback.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import threading
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar, cast

from .codec import OwnedJson
from .documents import Doc, DocFamily, Token
from .inspection_values import InspectionSupport
from .records import OMITTED, EntryRecord, TaskRecord, safe_integer
from .restart_policy import RestartPolicy
from .session import SessionClosedError, SessionFailed
from .session_core import AsyncTx, CommitPublication, Session, SyncTx
from .storage import ConversationQuery, EntryQuery, StorageWrite, TaskQuery
from .task_values import identity, json_equal
from .tasks import Task, capture, migrate

if TYPE_CHECKING:
    from pipy_harness.native.agent.request import AgentProviderRequestSnapshot

    from .inspection_values import TaskInspection
    from .resolution_values import ResolutionResult, ResolutionTarget


Tree = dict[str, Any]
T = TypeVar("T")
LIVE = ("pending", "running", "waiting", "sleeping", "completing")


def _observed(future: asyncio.Future[Any]) -> None:
    if not future.cancelled():
        future.exception()


class InvocationEnded(RuntimeError):
    """The runtime no longer owns a running invocation."""


class UnsupportedTaskExecution(RuntimeError):
    """This private sequential runtime preserves work owned by a later slice."""


class TaskSignal:
    """Thread-safe cooperative shutdown signal; a waiter cannot cancel it."""

    def __init__(self) -> None:
        self._future: concurrent.futures.Future[None] = concurrent.futures.Future()

    @property
    def cancelled(self) -> bool:
        return self._future.done()

    async def wait(self) -> None:
        await asyncio.shield(asyncio.wrap_future(self._future))

    def _signal(self) -> None:
        # Only the host signals; concurrent readers use Future's own guard.
        if not self._future.done():
            self._future.set_result(None)


@dataclass(frozen=True, slots=True)
class TaskContext:
    signal: TaskSignal


@dataclass(eq=False, slots=True)
class _Invocation:
    task_id: int
    conversation_id: int
    definition: Task[Any, Any, Any]
    phases: dict[bytes, Callable[..., Awaitable[None]]]
    context: TaskContext
    ended: bool = False


def _state(record: Tree, state: Tree, *, progress: bool = False) -> Tree:
    result = {**record, "state": state}
    if progress or state["status"] in ("terminal", "completing"):
        result.pop("restarts", None)
    if state["status"] in ("terminal", "completing"):
        result.pop("memos", None)
    return result


async def _tasks(tx: AsyncTx) -> list[Tree]:
    result: list[Tree] = []
    cursor = None
    while True:
        page = await tx.scan_tasks(TaskQuery({}), 128, cursor)
        result.extend(row.to_tree() for row in page.items)
        cursor = page.next
        if cursor is None:
            return result


async def _conversations(tx: AsyncTx) -> list[Tree]:
    result: list[Tree] = []
    cursor = None
    while True:
        page = await tx.scan_conversations(ConversationQuery({}), 128, cursor)
        result.extend(row.to_tree() for row in page.items)
        cursor = page.next
        if cursor is None:
            return result


def _model_tool(record: Tree) -> bool:
    from .generation_tasks import TOOL_NAME

    return bool(record["kind"] == TOOL_NAME)


def _owned(record: Tree, tasks: Iterable[Tree], conversations: Iterable[Tree]) -> bool:
    """Conservative DE15 boundary, before a handler or terminal transition."""
    id = record["id"]
    if "owner" in record:
        return True
    if any(t.get("owner") == id and t["state"]["status"] in LIVE for t in tasks):
        return True
    return any(
        c.get("owner", {}).get("taskId") == id
        or (c["id"] == record["conversationId"] and "owner" in c)
        for c in conversations
    )


def _tool_wait(
    current: Tree,
    state: Tree | None,
    tasks: list[Tree],
    conversations: list[Tree],
    staged: list[Tree],
    staged_conversations: list[Tree],
    entries: tuple[EntryRecord, ...],
) -> bool:
    """Only DE07's new real children may cross the conservative DE15 fence."""
    if type(state) is not dict or state.get("status") != "waiting":
        return False
    from .checkpoint_codec import decode_checkpoint
    from .generation_tasks import GENERATION_NAME, GENERATION_VERSION

    if (
        (current["kind"], current["version"]) != (GENERATION_NAME, GENERATION_VERSION)
        or _owned(current, tasks, conversations)
        or staged_conversations
        or state.get("policy") != "allSettled"
    ):
        raise UnsupportedTaskExecution("Task ownership joins require DE15")
    previous = decode_checkpoint(
        current["state"]["checkpoint"],
        name=GENERATION_NAME,
        version=GENERATION_VERSION,
        phase="settle",
        role="provider_outcome",
    ).to_tree()
    candidate = decode_checkpoint(
        state.get("checkpoint"),
        name=GENERATION_NAME,
        version=GENERATION_VERSION,
        phase="tools",
        role="provider_outcome",
    ).to_tree()
    previous["phase"] = "tools"
    if OwnedJson(previous) != OwnedJson(candidate):
        raise UnsupportedTaskExecution("Tool wait must retain its response receipt")
    _new_tool_children(current, staged, state.get("on"), tasks)
    _tool_inputs(current, staged, entries)
    return True


def _tool_inputs(
    parent: Tree, children: list[Tree], entries: tuple[EntryRecord, ...]
) -> None:
    from pipy_harness.native.agent.messages import AgentAssistantMessage
    from pipy_harness.native.agent.request import AgentProviderRequestSnapshot

    from .canonical_codec import decode_canonical

    assistants = [
        entry.to_tree()
        for entry in entries
        if entry.to_tree()["kind"] == "pi.assistant"
    ]
    if len(assistants) != 1:
        raise UnsupportedTaskExecution(
            "Tool admission requires its new assistant entry"
        )
    entry = assistants[0]
    model = entry.get("model", [])
    if type(model) is not list or len(model) != 1:
        raise UnsupportedTaskExecution(
            "Tool admission requires its canonical assistant"
        )
    assistant = decode_canonical(model[0])
    snapshot = decode_canonical(parent["input"])
    if (
        type(assistant) is not AgentAssistantMessage
        or type(snapshot) is not AgentProviderRequestSnapshot
    ):
        raise UnsupportedTaskExecution("Tool admission requires its canonical request")
    expected = [
        {
            "kind": "model",
            "assistant": entry["id"],
            "callId": call.provider_correlation_id,
        }
        for call in assistant.tool_calls
        if snapshot.authorizes(call.tool_name)
    ]
    if [child["input"] for child in children] != expected:
        raise UnsupportedTaskExecution("Tool admission must include every offered call")


def _new_tool_children(
    parent: Tree, staged: list[Tree], on: object, tasks: list[Tree]
) -> None:
    from .generation_tasks import TOOL_NAME, TOOL_VERSION, tool_input

    existing = {task["id"] for task in tasks}
    for child in staged:
        if (
            child["id"] in existing
            or (child["kind"], child["version"]) != (TOOL_NAME, TOOL_VERSION)
            or child.get("owner") != parent["id"]
            or child["conversationId"] != parent["conversationId"]
            or child["background"]
            or child["abortRequested"]
            or child["state"] != {"status": "pending", "checkpoint": {"phase": "call"}}
        ):
            raise UnsupportedTaskExecution("Tool wait requires new owned tool tasks")
        tool_input(child["input"])
    if type(on) is not list or on != [child["id"] for child in staged]:
        raise UnsupportedTaskExecution("Tool wait must name every admitted child")


class TaskRuntime:
    """Invocation-scoped async commits and first-writer memos."""

    def __init__(self, scheduler: TaskScheduler, invocation: _Invocation) -> None:
        self._scheduler = scheduler
        self._invocation = invocation

    @property
    def task_id(self) -> int:
        return self._invocation.task_id

    @property
    def conversation_id(self) -> int:
        return self._invocation.conversation_id

    @property
    def signal(self) -> TaskSignal:
        return self._invocation.context.signal

    def _reporting_preflight(self) -> None:
        """Refuse a blocking reporter on its own Session line or ended token."""
        self._scheduler._session._refuse_reentry("report_details")
        self._scheduler._check(self._invocation)

    async def commit(
        self,
        change: Callable[[TaskTx, Tree], object | Awaitable[object]],
        context: TaskContext | None = None,
    ) -> None:
        async def apply(tx: AsyncTx, current: Tree) -> None:
            tasks, conversations = await _tasks(tx), await _conversations(tx)
            from .tool_graph import capture_entries

            graph_entries = await capture_entries(tx, tasks)
            value = change(
                TaskTx(tx, self.conversation_id, self.task_id),
                cast(Tree, OwnedJson(current).thaw()),
            )
            while inspect.isawaitable(value):
                value = await value
            staged_tasks = [r.to_tree() for r in tx.staged_tasks()]
            staged_conversations = [r.to_tree() for r in tx.staged_conversations()]
            state = None if value is None else cast(Tree, OwnedJson(value).thaw())
            waiting = _tool_wait(
                current,
                state,
                tasks,
                conversations,
                staged_tasks,
                staged_conversations,
                tx.staged_entries(),
            )
            from .tool_graph import model_call, overlay, reconcile

            supported_leaf = await model_call(
                tx,
                current,
                overlay(tasks, tx),
                list((*conversations, *staged_conversations)),
                graph_entries,
            )
            if (
                not waiting
                and supported_leaf is None
                and _owned(
                    current,
                    (*tasks, *staged_tasks),
                    (*conversations, *staged_conversations),
                )
            ):
                raise UnsupportedTaskExecution("Task ownership joins require DE15")
            if value is None:
                return
            assert state is not None
            from .tool_values import blocked_transition

            blocked = blocked_transition(current, state)
            if (
                type(state) is not dict
                or state.get("status")
                not in (
                    "running",
                    "terminal",
                )
                and not waiting
                and not blocked
            ):
                raise UnsupportedTaskExecution(
                    "Only running and terminal transitions are supported"
                )
            tx.set_task(
                _state(current, state, progress=not json_equal(current["state"], state))
            )
            if self._scheduler._tool_execution_enabled():
                await reconcile(
                    tx,
                    tasks,
                    graph_entries,
                    list((*conversations, *staged_conversations)),
                )

        ignored: object = await self._scheduler._gated(
            self._invocation, cast(Callable[[AsyncTx, Tree], Awaitable[object]], apply)
        )
        del ignored

    async def memo(
        self, name: str, candidate: object = OMITTED, context: TaskContext | None = None
    ) -> object:
        key = identity(name)
        owned = None if candidate is OMITTED else OwnedJson(candidate)

        def apply(tx: AsyncTx, current: Tree) -> object:
            memos = current.get("memos", {})
            for existing, value in memos.items():
                if identity(existing) == key:
                    return OwnedJson(value).thaw()
            if owned is None:
                return OMITTED
            value = owned.thaw()
            tx.set_task({**current, "memos": {**memos, name: value}})
            return owned.thaw()

        return await self._scheduler._gated(self._invocation, apply)


class TaskTx:
    """Scoped entry/document mutation; no raw task replacement or foreign IDs."""

    def __init__(self, tx: AsyncTx, conversation_id: int, task_id: int) -> None:
        self._tx = tx
        self._conversation_id = conversation_id
        self._task_id = task_id

    def append_entry(self, draft: object) -> Any:
        return self._tx.append_entry(self._conversation_id, draft)

    async def entry(self, id: int) -> EntryRecord | None:
        record = await self._tx.entry(id)
        if (
            record is not None
            and record.to_tree()["conversationId"] != self._conversation_id
        ):
            raise UnsupportedTaskExecution("Entry is outside the task conversation")
        return record

    async def entries(self) -> tuple[EntryRecord, ...]:
        result: list[EntryRecord] = []
        cursor = None
        while True:
            page = await self._tx.scan_entries(
                EntryQuery({"conversationId": self._conversation_id}), 128, cursor
            )
            result.extend(page.items)
            cursor = page.next
            if cursor is None:
                return tuple(result)

    async def model_tool_call(self) -> Any:
        from .tool_graph import model_call, overlay

        found = await self._tx.task(self._task_id)
        if found is None:
            raise UnsupportedTaskExecution("Model tool no longer exists")
        value = await model_call(
            self._tx,
            found.to_tree(),
            overlay(await _tasks(self._tx), self._tx),
            await _conversations(self._tx),
        )
        if value is None:
            raise UnsupportedTaskExecution("Model tool authority is incompatible")
        return value

    async def tool_round(self) -> Any:
        from .tool_graph import overlay, result, round_context

        found = await self._tx.task(self._task_id)
        if found is None:
            raise UnsupportedTaskExecution("Tool round no longer exists")
        parent = found.to_tree()
        value, assistant, snapshot = await round_context(self._tx, parent)
        rows = overlay(await _tasks(self._tx), self._tx)
        outputs = []
        for call, slot in zip(assistant.tool_calls, value["slots"], strict=True):
            outputs.append(
                await result(self._tx, parent, value["assistant"], call, slot, rows)
            )
        return value, assistant, snapshot, tuple(outputs)

    def create_task(self, task: Task[Any, Any, Any], input: object) -> Any:
        """Own before suspension; callers cannot select another owner or scope."""
        return self._tx.create_task(
            task,
            input,
            ownership={"kind": "task", "taskId": self._task_id},
            conversation_id=self._conversation_id,
        )

    def doc(self, raw: Token, *, key: object = OMITTED, seed: object = OMITTED) -> Any:
        conversation_id, task_id = self._document_ids(raw)
        return self._tx.doc(
            raw, conversation_id=conversation_id, task_id=task_id, key=key, seed=seed
        )

    def retire_doc(self, raw: Token, *, key: object = OMITTED) -> Any:
        conversation_id, task_id = self._document_ids(raw)
        return self._tx.retire_doc(
            raw, conversation_id=conversation_id, task_id=task_id, key=key
        )

    def _document_ids(self, raw: Token) -> tuple[int | None, int | None]:
        if type(raw) not in (Doc, DocFamily):
            raise TypeError("Expected a document token")
        if type(raw.scope) is not str:
            raise TypeError("Document scope must be an exact string")
        return (
            self._conversation_id if raw.scope == "conversation" else None,
            self._task_id if raw.scope == "task" else None,
        )


class TaskScheduler:
    """One private execution owner; open recovers, explicit resume dispatches."""

    def __init__(
        self,
        session: Session,
        definitions: Iterable[Task[Any, Any, Any]],
        *,
        restart_limit: object = 5,
    ) -> None:
        definitions_by_name: dict[bytes, Task[Any, Any, Any]] = {}
        for raw in definitions:
            definition = capture(raw)
            key = identity(definition.name)
            if key in definitions_by_name:
                raise ValueError("Duplicate task definition UTF-16 identity")
            definitions_by_name[key] = definition
        self._restart_policy = RestartPolicy(restart_limit)
        self._session = session
        self._owner = session._owner
        self._guard = self._owner._cond
        self._definitions = definitions_by_name
        self._inspection_support = InspectionSupport(
            tuple(definitions_by_name.values())
        )
        self._migration_failures: dict[int, Task[Any, Any, Any]] = {}
        self._index: dict[int, TaskRecord] = {}
        self._invocation: _Invocation | None = None
        self._stopped: set[int] = set()
        self._enabled = False
        self._draining = False
        self._opened = False
        self._opening = False
        self._host_done = False
        self._startup_failed = False
        self._loop = asyncio.new_event_loop()
        self._wake = asyncio.Event()
        # Only the host operates this event. Activity itself uses the same
        # lifecycle guard as reservation/enable/close; event completion is
        # deliberately outside that guard.
        self._quiescent = asyncio.Event()
        self._quiescent.set()
        self._ready = threading.Event()
        self._service_task: asyncio.Task[None] | None = None
        self._thread = threading.Thread(
            target=self._host, name="pipy-durable-phases", daemon=True
        )
        # Bind all trusted hooks atomically before any close or observation.
        with self._guard:
            refused = self._owner._line_sealed or self._owner._join_close is not None
            if not refused:
                self._owner._join_close = self._join
                self._owner._join_done = False
                session._close_change = self._rewrite_stopped
                session._internal_listeners.add(self._observe)
        if refused:
            self._loop.close()
            raise RuntimeError("Session already has an invocation owner or is closing")
        try:
            self._thread.start()
        except BaseException as error:
            if not self._thread.is_alive():
                self._loop.close()
                with self._guard:
                    self._startup_failed = True
                    self._host_done = True
                self._ready.set()
            session.fail(error)
            raise
        self._ready.wait()

    def _host(self) -> None:
        asyncio.set_event_loop(self._loop)
        service = self._service()
        try:
            self._service_task = self._loop.create_task(service)
        except BaseException as error:  # noqa: BLE001 - startup has no phase to abandon
            service.close()
            self._loop.close()
            with self._guard:
                self._startup_failed = True
                self._host_done = True
            self._ready.set()
            self._session.fail(error)
            return
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.run_until_complete(self._loop.shutdown_default_executor())
            self._loop.close()
            with self._guard:
                self._host_done = True
                failures, self._migration_failures = self._migration_failures, {}
            del failures

    def _notify(self) -> None:
        try:
            self._loop.call_soon_threadsafe(self._wake.set)
        except RuntimeError:
            with self._guard:
                closing = self._owner._line_sealed
            if not closing:
                raise

    async def aopen(self) -> None:
        """Recover atomically; cancelling this waiter does not cancel recovery."""
        with self._guard:
            if self._opening or self._opened:
                raise RuntimeError("Scheduler open may only be called once")
            if self._owner._line_sealed:
                raise SessionClosedError()
            self._opening = True
        recovery = self._recover()
        try:
            future = asyncio.run_coroutine_threadsafe(recovery, self._loop)
        except BaseException:
            recovery.close()
            raise
        await asyncio.shield(asyncio.wrap_future(future))

    def open(self) -> None:
        _off_loop("open", "aopen")
        asyncio.run(self.aopen())

    async def _recover(self) -> None:
        async def change(tx: AsyncTx) -> None:
            rows = await _tasks(tx)
            conversations = await _conversations(tx)
            from .tool_graph import capture_entries

            graph_entries = (
                await capture_entries(tx, rows)
                if self._tool_execution_enabled()
                else {}
            )
            # Commit observation installs replacements. Seed every unchanged row
            # on the line before publication; every value is already owned.
            captured = {r["id"]: TaskRecord(r) for r in rows}
            with self._guard:
                old, self._index = self._index, captured
            del old
            for row in rows:
                if row["state"]["status"] == "running":
                    pending = _state(
                        row,
                        {"status": "pending", "checkpoint": row["state"]["checkpoint"]},
                    )
                    pending["restarts"] = row.get("restarts", 0) + 1
                    tx.set_task(pending)
            if self._tool_execution_enabled():
                from .tool_graph import reconcile

                await reconcile(tx, rows, graph_entries, conversations)

        try:
            ignored: object = await self._session.acommit(
                cast(Callable[[AsyncTx], Awaitable[object]], change)
            )
            del ignored
        except BaseException as error:
            if type(error) is not SessionClosedError:
                self._session.fail(error)
            raise
        with self._guard:
            self._opened = True

    def resume(self) -> None:
        """Enable sequential scheduling. Idempotent; no caller owns its lifetime."""
        with self._guard:
            if not self._opened:
                raise RuntimeError("Open the scheduler before resume")
            if self._owner._line_sealed:
                return
            self._enabled = True
        self._notify()

    async def apause(self) -> None:
        """Disable dispatch and await real reservation/invocation retirement.

        Already-admitted work drains normally. Cancelling this waiter does not
        re-enable dispatch or cancel the host's barrier. Callers serialize any
        competing resume; a competing enable refuses this pause acknowledgement.
        """
        self._session._refuse_reentry("apause")
        if asyncio.get_running_loop() is self._loop:
            raise RuntimeError("Cannot pause the scheduler from its phase host")
        with self._guard:
            self._enabled = False
            closed = self._owner._line_sealed or self._host_done
        if closed:
            raise SessionClosedError()

        async def settled() -> None:
            while True:
                with self._guard:
                    closed = self._owner._line_sealed or self._host_done
                    enabled = self._enabled
                    busy = self._draining or self._invocation is not None
                if closed:
                    raise SessionClosedError()
                if enabled:
                    raise RuntimeError("Scheduler resumed during pause")
                if not busy:
                    return
                await self._quiescent.wait()

        barrier = settled()
        try:
            future = asyncio.run_coroutine_threadsafe(barrier, self._loop)
        except BaseException:
            barrier.close()
            raise
        waiting = asyncio.wrap_future(future)
        waiting.add_done_callback(_observed)
        await asyncio.shield(waiting)

    def _retry_ready(self) -> None:
        with self._guard:
            closed = (
                self._owner._line_sealed
                or self._owner._failure is not None
                or self._host_done
            )
            opened = self._opened
        if closed:
            raise SessionClosedError()
        if not opened:
            raise RuntimeError("Open the scheduler before retry")

    def _publish_restart_policy(self, policy: RestartPolicy) -> bool:
        def publish() -> bool:
            with self._guard:
                failure = self._owner._failure
                closed = self._owner._line_sealed or self._host_done
                # _opening is a sticky admission marker, including after success.
                opened = self._opened
                previous = self._restart_policy
                changed = (
                    failure is None
                    and not closed
                    and opened
                    and previous.limit != policy.limit
                )
                if changed:
                    self._restart_policy = policy
            # Neither exception construction nor displaced references belong
            # under the shared metadata condition.
            del previous
            if failure is not None:
                raise SessionFailed(failure) from failure
            if closed:
                raise SessionClosedError()
            if not opened:
                raise RuntimeError("Open the scheduler before setting restart limit")
            return changed

        # Host metadata publication is serialized with reads and transactions,
        # but is neither a read-only public job nor a durable commit.
        return self._session._on_line_sync("set_restart_limit", publish)

    def set_restart_limit(self, value: object) -> bool:
        """Publish host policy only; explicit resume requests another pass."""
        self._session._refuse_reentry("set_restart_limit")
        _off_loop("set_restart_limit", "aset_restart_limit")
        return self._publish_restart_policy(RestartPolicy(value))

    async def aset_restart_limit(self, value: object) -> bool:
        """Publish without waking work; cancellation joins the actual line job."""
        from .generation_transport import owned_call

        self._session._refuse_reentry("aset_restart_limit")
        policy = RestartPolicy(value)
        return await owned_call(lambda: self._publish_restart_policy(policy))

    async def aretry(self, task_id: int) -> bool:
        """Clear a parked task's current streak, then enable after success."""
        self._session._refuse_reentry("aretry")
        safe_integer(task_id)
        self._retry_ready()

        async def retry_owned() -> bool:
            async def change(tx: AsyncTx) -> bool:
                self._retry_ready()
                with self._guard:
                    policy = self._restart_policy
                record = await tx.task(task_id)
                if record is None:
                    raise ValueError("Unknown task")
                cleared = policy.cleared(cast(Tree, record.to_tree()))
                if cleared is not None:
                    tx.set_task(cleared)
                return cleared is not None

            changed: bool = await self._session.acommit(change)
            with self._guard:
                if (
                    not self._owner._line_sealed
                    and self._owner._failure is None
                    and not self._host_done
                ):
                    self._enabled = True
            self._notify()
            return changed

        work = retry_owned()
        try:
            future = asyncio.run_coroutine_threadsafe(work, self._loop)
        except BaseException:
            work.close()
            raise
        waiting = asyncio.wrap_future(future)
        waiting.add_done_callback(_observed)
        return await asyncio.shield(waiting)

    def retry(self, task_id: int) -> bool:
        _off_loop("retry", "aretry")
        return asyncio.run(self.aretry(task_id))

    def inspect(self) -> tuple[TaskRecord, ...]:
        """One immutable committed revision; record trees detach on access."""
        with self._guard:
            return tuple(self._index[id] for id in sorted(self._index))

    def inspect_task(self, task_id: int) -> TaskInspection | None:
        """Read committed metadata with this owner's observed migration failures."""
        from .inspection import _inspect_owned_task

        return _inspect_owned_task(self, task_id)

    async def ainspect_task(self, task_id: int) -> TaskInspection | None:
        from .inspection import _Read

        self._session._refuse_reentry("ainspect_task")
        read = _Read(lambda: self.inspect_task(task_id))
        return cast("TaskInspection | None", await asyncio.shield(read.work))

    def resolution_target(self, task_id: int) -> ResolutionTarget:
        """Capture private expected state without recovering or dispatching work."""
        from .resolution import resolution_target

        return resolution_target(self, task_id)

    async def aresolution_target(self, task_id: int) -> ResolutionTarget:
        from .resolution import aresolution_target

        return await aresolution_target(self, task_id)

    def resolve(
        self,
        target: ResolutionTarget,
        *,
        nonce: str,
        new_request: AgentProviderRequestSnapshot | None = None,
    ) -> ResolutionResult:
        """Authorize one NEW native attempt, deduplicated by a durable nonce."""
        from .resolution import resolve

        return resolve(self, target, nonce=nonce, new_request=new_request)

    async def aresolve(
        self,
        target: ResolutionTarget,
        *,
        nonce: str,
        new_request: AgentProviderRequestSnapshot | None = None,
    ) -> ResolutionResult:
        from .resolution import aresolve

        return await aresolve(self, target, nonce=nonce, new_request=new_request)

    def _observe(self, publication: CommitPublication) -> None:
        updates = []
        retired = []
        for change in publication.changes:
            if type(change) is StorageWrite:
                tree = change.to_tree()
                if tree["type"] == "task":
                    updates.append(TaskRecord(tree["value"]))
        terminal = {
            row.id
            for row in updates
            if cast(Tree, row.to_tree())["state"]["status"] == "terminal"
        }
        with self._guard:
            old = [self._index.get(r.id) for r in updates]
            for row in updates:
                self._index[row.id] = row
                if row.id in terminal:
                    retired.append(self._migration_failures.pop(row.id, None))
        del old
        del retired
        self._notify()

    async def _service(self) -> None:
        try:
            while True:
                await self._wake.wait()
                self._wake.clear()
                with self._guard:
                    closing = self._owner._line_sealed
                    enabled = self._enabled and self._opened
                    if enabled and not closing:
                        self._draining = True
                if closing:
                    return
                if enabled:
                    self._quiescent.clear()
                    try:
                        await self._drain()
                    finally:
                        with self._guard:
                            self._draining = False
                        self._quiescent.set()
        except BaseException as error:  # noqa: BLE001 - host failure seals its owner
            self._session.fail(error)

    async def _drain(self) -> None:
        while True:
            invocation = await self._reserve()
            if invocation is None:
                return
            try:
                await self._run(invocation)
            finally:
                self._retire(invocation)

    async def _reserve(self) -> _Invocation | None:
        tentative: _Invocation | None = None

        async def change(tx: AsyncTx) -> None:
            nonlocal tentative
            with self._guard:
                policy = self._restart_policy
            rows, conversations = await _tasks(tx), await _conversations(tx)
            from .tool_graph import capture_entries

            graph_entries = (
                await capture_entries(tx, rows)
                if self._tool_execution_enabled()
                else {}
            )
            rows = await self._reconciled(tx, rows, graph_entries, conversations)
            for row in rows:
                candidate = await self._candidate(
                    tx, row, rows, conversations, graph_entries, policy
                )
                if candidate is None:
                    continue
                definition, row = candidate
                invocation = _Invocation(
                    row["id"],
                    row["conversationId"],
                    definition,
                    {identity(k): v for k, v in definition.phases.items()},
                    TaskContext(TaskSignal()),
                )
                with self._guard:
                    if self._owner._line_sealed or not self._enabled:
                        return
                    if self._invocation is not None:
                        raise RuntimeError(
                            "Sequential reservation already owns an invocation"
                        )
                    self._invocation = invocation
                    self._stopped.add(invocation.task_id)
                tentative = invocation
                tx.set_task(
                    _state(
                        row,
                        {"status": "running", "checkpoint": row["state"]["checkpoint"]},
                    )
                )
                return

        with self._guard:
            if self._owner._line_sealed:
                return None
        try:
            ignored: object = await self._session.acommit(
                cast(Callable[[AsyncTx], Awaitable[object]], change)
            )
            del ignored
            return tentative
        except BaseException as error:
            if tentative is not None:
                self._retire(tentative)
            if type(error) is SessionClosedError and self._session.failure is None:
                return None
            raise

    async def _reconciled(
        self,
        tx: AsyncTx,
        rows: list[Tree],
        graph_entries: dict[int, EntryRecord],
        conversations: list[Tree],
    ) -> list[Tree]:
        if not self._tool_execution_enabled():
            return rows
        from .tool_graph import reconcile

        return await reconcile(tx, rows, graph_entries, conversations)

    def _tool_execution_enabled(self) -> bool:
        from .generation_tasks import TOOL_NAME, TOOL_VERSION

        key = identity(TOOL_NAME)
        with self._guard:
            definition = self._definitions.get(key)
        return (
            definition is not None
            and definition.version == TOOL_VERSION
            and {"call", "execute", "settle"} <= set(definition.phases)
        )

    async def _candidate(
        self,
        tx: AsyncTx,
        row: Tree,
        rows: list[Tree],
        conversations: list[Tree],
        graph_entries: dict[int, EntryRecord],
        policy: RestartPolicy,
    ) -> tuple[Task[Any, Any, Any], Tree] | None:
        if (
            row["state"]["status"] != "pending"
            or row["abortRequested"]
            or row.get("abandonOnRestart")
            or policy.parked(row)
        ):
            return None
        from .tool_values import blocked_record

        if blocked_record(row):
            return None
        definition = self._fitting_definition(row)
        if definition is None:
            return None
        if _model_tool(row) or _owned(row, rows, conversations):
            from .tool_graph import model_call

            if await model_call(tx, row, rows, conversations, graph_entries) is None:
                return None
        return self._fit(row, definition)

    def _fitting_definition(self, row: Tree) -> Task[Any, Any, Any] | None:
        from .generation_tasks import GENERATION_NAME, TOOL_NAME

        key = identity(row["kind"])
        with self._guard:
            definition = self._definitions.get(key)
        if definition is None or definition.version < row["version"]:
            return None
        if definition.version != row["version"] and (
            row["kind"] in (GENERATION_NAME, TOOL_NAME) or definition.migrate is None
        ):
            # Preserve refusal before acquiring an irrelevant model graph.
            return None
        return definition

    def _fit(
        self, row: Tree, definition: Task[Any, Any, Any]
    ) -> tuple[Task[Any, Any, Any], Tree] | None:
        if definition.version == row["version"]:
            return definition, row
        from .generation_tasks import GENERATION_NAME, TOOL_NAME

        # Generic migration must never reinterpret a built-in authority frame.
        if row["kind"] in (GENERATION_NAME, TOOL_NAME) or definition.migrate is None:
            return None
        with self._guard:
            if self._owner._line_sealed or not self._enabled:
                return None
            failed = self._migration_failures.get(row["id"]) is definition
        if failed:
            return None
        try:
            migrated = cast(Tree, TaskRecord(migrate(definition, row)).to_tree())
        except Exception:  # noqa: BLE001 - trusted callback failure is finite metadata
            # The exception (and its traceback/payload) is released outside the
            # metadata condition. No failed candidate mutation has been staged.
            pass
        else:
            return definition, migrated
        with self._guard:
            previous = self._migration_failures.get(row["id"])
            self._migration_failures[row["id"]] = definition
        del previous
        # _reserve continues this same pass, preserving legitimate reconciliation
        # writes and allowing later eligible work without an external wake.
        return None

    def _retire(self, invocation: _Invocation) -> None:
        self._end(invocation)
        invocation.context.signal._signal()
        with self._guard:
            old = self._invocation
            if old is invocation:
                self._invocation = None
        del old

    def _end(self, invocation: _Invocation) -> None:
        with self._guard:
            invocation.ended = True

    def _check(self, invocation: _Invocation) -> None:
        with self._guard:
            if invocation.ended or self._invocation is not invocation:
                raise InvocationEnded("Task invocation has ended")
            if self._owner._line_sealed:
                raise SessionClosedError()

    async def _gated(
        self,
        invocation: _Invocation,
        change: Callable[[AsyncTx, Tree], T | Awaitable[T]],
    ) -> T:
        self._check(invocation)

        async def apply(tx: AsyncTx) -> T:
            self._check(invocation)
            record = await tx.task(invocation.task_id)
            self._check(invocation)
            if record is None:
                raise InvocationEnded("Task no longer exists")
            current = cast(Tree, record.to_tree())
            if current["state"]["status"] != "running" or current["abortRequested"]:
                raise InvocationEnded("Task is not an unmarked running task")
            rows, conversations = await _tasks(tx), await _conversations(tx)
            if _model_tool(current) or _owned(current, rows, conversations):
                from .tool_graph import model_call

                if await model_call(tx, current, rows, conversations) is None:
                    raise InvocationEnded("Model tool authority ended")
            value = change(tx, current)
            while inspect.isawaitable(value):
                value = await value
            return value

        return await self._session._acommit(
            apply,
            conversation_id=invocation.conversation_id,
            task_id=invocation.task_id,
        )

    async def _step(
        self,
        invocation: _Invocation,
        previous: object = OMITTED,
        error: BaseException | None = None,
    ) -> Tree | None:
        async def change(tx: AsyncTx) -> Tree | None:
            found = await tx.task(invocation.task_id)
            current = None if found is None else cast(Tree, found.to_tree())
            with self._guard:
                stopping = self._owner._line_sealed or invocation.ended
            if (
                stopping
                or current is None
                or current["state"]["status"] != "running"
                or current["abortRequested"]
            ):
                self._end(invocation)
                return None
            rows, conversations = await _tasks(tx), await _conversations(tx)
            from .tool_graph import model_call

            if (
                _model_tool(current) or _owned(current, rows, conversations)
            ) and await model_call(tx, current, rows, conversations) is None:
                self._end(invocation)
                tx.set_task(
                    _state(
                        current,
                        {
                            "status": "pending",
                            "checkpoint": current["state"]["checkpoint"],
                        },
                    )
                )
                return None
            no_progress = previous is not OMITTED and json_equal(
                previous, current["state"]["checkpoint"]
            )
            if error is not None or no_progress:
                self._end(invocation)
                message = (
                    str(error)
                    if error is not None
                    else f"Task {current['kind']} phase {cast(Tree, previous).get('phase')} returned without durable progress"
                )
                tx.set_task(
                    _state(
                        current,
                        {
                            "status": "terminal",
                            "outcome": {
                                "status": "faulted",
                                "error": {"message": message},
                            },
                        },
                    )
                )
                return None
            return current

        try:
            return await self._session.acommit(change)
        except BaseException as error:  # noqa: BLE001 - failed decision cannot retry
            self._end(invocation)
            with self._guard:
                closing = self._owner._line_sealed
            if not closing:
                self._session.fail(error)
            return None

    async def _run(self, invocation: _Invocation) -> None:
        runtime = TaskRuntime(self, invocation)
        current = await self._step(invocation)
        while current is not None:
            with self._guard:
                admitted = not self._owner._line_sealed and not invocation.ended
            if not admitted:
                return
            checkpoint = current["state"]["checkpoint"]
            failure = None
            try:
                phase = checkpoint.get("phase") if type(checkpoint) is dict else None
                handler = (
                    invocation.phases.get(identity(phase))
                    if type(phase) is str
                    else None
                )
                if handler is None:
                    raise UnsupportedTaskExecution(
                        "Task checkpoint has no phase handler"
                    )
                await handler(current, runtime, invocation.context)
            except Exception as error:  # noqa: BLE001 - phase faults commit on the line
                failure = error
            current = await self._step(invocation, checkpoint, failure)

    async def _stop(self) -> None:
        with self._guard:
            invocation = self._invocation
        if invocation is not None:
            invocation.context.signal._signal()
        self._wake.set()
        service = self._service_task
        if service is not None:
            await service
        # Runtime calls spawned by a phase still belong to this host. Do not
        # cancel Session's shielded settlement tasks while closing the loop.
        while pending := [
            t for t in asyncio.all_tasks() if t is not asyncio.current_task()
        ]:
            await asyncio.gather(*pending, return_exceptions=True)

    def _join(self) -> None:
        self._ready.wait()
        with self._guard:
            startup_failed = self._startup_failed
        if startup_failed:
            if self._thread.ident is not None:
                self._thread.join()
            return
        future = asyncio.run_coroutine_threadsafe(self._stop(), self._loop)
        future.result()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()

    def _rewrite_stopped(self, tx: SyncTx) -> None:
        with self._guard:
            stopped = tuple(self._stopped)
        rows = [tx.task(id) for id in stopped]
        for record in rows:
            if record is not None:
                row = cast(Tree, record.to_tree())
                if row["state"]["status"] == "running":
                    tx.set_task(
                        _state(
                            row,
                            {
                                "status": "pending",
                                "checkpoint": row["state"]["checkpoint"],
                            },
                        )
                    )


def _off_loop(method: str, alternative: str) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(f"Use {alternative} instead of {method} on an event-loop thread")
