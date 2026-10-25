"""Opt-in async native durable product session; legacy SDK remains separate.

This facade owns one SQLite/Session and at most its existing scheduler host.
Submit admits one literal input. Only explicit resume enables execution.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar, cast

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import AgentUserMessage
from pipy_harness.native.agent.request import (
    AgentProviderRequestSnapshot,
    snapshot_provider_request,
)
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.tools.base import ToolDefinition

from .native.durable.canonical_codec import decode_canonical, encode_canonical
from .native.durable.codec import OwnedJson
from .native.durable.completed_projection import CompletedWatch, observe_completed
from .native.durable.generation import message_entry
from .native.durable.generation_tasks import GENERATION_NAME
from .native.durable.generation_transport import owned_call
from .native.durable.inspection import ainspect_live, ainspect_task
from .native.durable.inspection_read import round_facts, tasks
from .native.durable.inspection_values import (
    InspectionError,
    InspectionPage,
    InspectionSupport,
    TaskInspection,
)
from .native.durable.product_acquisition import acquire
from .native.durable.product_binding import (
    HEADER,
    bound_seed,
    header,
    retry_edges,
    seed,
    select_run,
)
from .native.durable.product_values import (
    DurableAdmission,
    DurableProductError,
    DurableProviderProfile,
    DurableRunResult,
    DurableRunSelection,
    UnsupportedDurableCapability,
    product_task_id,
)
from .native.durable.product_wake import ProductWake
from .native.durable.records import TaskId
from .native.durable.resolution import GENERATION_ADMISSION, _winner
from .native.durable.resolution_values import ResolutionError, ResolutionResult
from .native.durable.scheduler import TaskScheduler
from .native.durable.session import SessionClosedError
from .native.durable.session_core import Session, SyncTx
from .native.durable.sqlite import open_sqlite_storage
from .native.durable.storage import Cursor, Storage
from .native.durable.tasks import Task
from .native.durable.tool_execution import (
    ToolRegistration,
    builtin_registrations,
    define_tool_task,
)

T = TypeVar("T")
Tree = dict[str, Any]


def _observe_error(done: asyncio.Task[Any]) -> None:
    if not done.cancelled():
        done.exception()


def _paths(path: Path, workspace: Path) -> tuple[Path, Path]:
    if type(path) is not type(Path()) or type(workspace) is not type(Path()):
        raise DurableProductError("invalid_request")
    # Preserve the database basename for the existing owner's symlink/hardlink
    # checks; only directory aliases are canonicalized here.
    return path.absolute().parent.resolve() / path.name, workspace.resolve()


def _tool_definition(
    tools: Mapping[str, ToolRegistration] | None, workspace: Path
) -> Task[Any, Any, Any]:
    try:
        return define_tool_task(
            builtin_registrations() if tools is None else tools,
            workspace_root=workspace,
        )
    except Exception:  # noqa: BLE001 - private registration payloads stay private
        raise UnsupportedDurableCapability from None


def _tool_tree(definition: Task[Any, Any, Any]) -> Tree:
    assert definition.support is not None
    return definition.support.to_tree()


def _template(
    profile: DurableProviderProfile,
    workspace: Path,
    tool: Task[Any, Any, Any],
    request_template: AgentProviderRequestSnapshot | None,
) -> AgentProviderRequestSnapshot:
    try:
        advertised = tuple(
            cast(ToolDefinition, decode_canonical(raw["advertised"]))
            for raw in _tool_tree(tool)["tools"].values()
        )
        if request_template is None:
            request_template = snapshot_provider_request(
                ProviderRequest(
                    "",
                    "",
                    profile._selection[0],
                    profile._selection[1],
                    workspace,
                    available_tools=advertised,
                )
            )
        # Canonical ingress rejects live callbacks and preserves equality-excluded fields.
        result = decode_canonical(encode_canonical(request_template))
        if type(result) is not AgentProviderRequestSnapshot:
            raise UnsupportedDurableCapability
        request = result.request
        if (
            request.cwd != workspace
            or request.session_id is not None
            or request.user_prompt
            or request.messages
            or (request.provider_name, request.model_id) != profile._selection[:2]
            or OwnedJson(encode_canonical(request.available_tools))
            != OwnedJson(encode_canonical(advertised))
        ):
            raise UnsupportedDurableCapability
        return result
    except Exception:  # noqa: BLE001 - no request contents in errors
        raise UnsupportedDurableCapability from None


def _user_message(content: str | AgentUserMessage) -> AgentUserMessage:
    try:
        message = (
            AgentUserMessage(ProductContent(content))
            if type(content) is str
            else content
        )
        decoded = decode_canonical(encode_canonical(message))
        if type(decoded) is not AgentUserMessage:
            raise DurableProductError("invalid_request")
    except Exception:  # noqa: BLE001 - no accepted input in error text
        raise DurableProductError("invalid_request") from None

    assert type(decoded) is AgentUserMessage
    return decoded


class DurableProductSession:
    """Caller-loop affiliated, single-input, explicitly resumed product lifetime."""

    def __init__(
        self,
        session: Session,
        path: Path,
        workspace: Path,
        profile: DurableProviderProfile | None,
        tool: Task[Any, Any, Any],
        selection: DurableRunSelection | None,
    ) -> None:
        self._session, self._path, self._workspace = session, path, workspace
        self._profile, self._tool, self._selection = profile, tool, selection
        self._loop = asyncio.get_running_loop()
        self._control = asyncio.Lock()
        self._operations: set[asyncio.Task[Any]] = set()
        self._watches: list[CompletedWatch] = []
        self._scheduler: TaskScheduler | None = None
        self._closing = False
        self._initial_admitted = False
        self._submission_inflight = False
        self._close_work: asyncio.Task[None] | None = None
        self._resume_work: (
            tuple[DurableRunSelection | None, asyncio.Task[DurableRunResult]] | None
        ) = None

    def _check(self, method: str, *, closing: bool = False) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise DurableProductError("wrong_loop")
        self._session._refuse_reentry(method)
        if any(
            asyncio.current_task() is watch._consumer_task for watch in self._watches
        ):
            raise DurableProductError("reentrant")
        if self._closing and not closing:
            raise DurableProductError("closed")

    async def _owned(self, body: Callable[[], Awaitable[T]]) -> T:
        async def serialized() -> T:
            async with self._control:
                if self._closing:
                    raise DurableProductError("closed")
                try:
                    return await body()
                except (DurableProductError, InspectionError, ResolutionError):
                    raise
                except SessionClosedError:
                    raise DurableProductError("closed") from None
                except Exception:  # noqa: BLE001 - no backend/initializer payload escapes
                    raise DurableProductError from None

        work = asyncio.create_task(serialized())
        self._operations.add(work)
        work.add_done_callback(self._operations.discard)
        work.add_done_callback(_observe_error)
        return await asyncio.shield(work)

    async def _read(self, job: Callable[[], T]) -> T:
        try:
            return await owned_call(lambda: self._session.read_on_line(job))
        except DurableProductError:
            raise
        except SessionClosedError:
            raise DurableProductError("closed") from None
        except Exception:  # noqa: BLE001 - guarded metadata errors remain payload-free
            raise DurableProductError from None

    def _header(self) -> Tree:
        return header(self._session._owner.storage, self._path, self._workspace)

    def _support(self) -> InspectionSupport:
        definitions = (
            (self._tool,)
            if self._profile is None
            else (self._profile._definition, self._tool)
        )
        return InspectionSupport(definitions)

    async def submit(
        self, content: str | AgentUserMessage, **unsupported: object
    ) -> DurableAdmission:
        _options(unsupported)
        self._check("durable_submit")
        if self._initial_admitted:
            raise DurableProductError("already_admitted")
        if self._submission_inflight:
            raise DurableProductError("busy")
        message = _user_message(content)

        async def admit() -> DurableAdmission:
            if self._scheduler is not None:
                await self._scheduler.apause()

            def change(tx: SyncTx) -> DurableAdmission:
                value = self._header()
                if value["initial_generation"] is not None:
                    raise DurableProductError("already_admitted")
                draft = tx.doc(HEADER)
                tx.conversation(value["conversation"])
                template = decode_canonical(value["template"])
                assert type(template) is AgentProviderRequestSnapshot
                entry = tx.append_entry(value["conversation"], message_entry(message))
                task_id = tx.create_task(
                    GENERATION_ADMISSION,
                    encode_canonical(seed(template, message)),
                    ownership={"kind": "conversation"},
                    conversation_id=value["conversation"],
                )
                draft.update({"initial_entry": entry.id, "initial_generation": task_id})
                return DurableAdmission(value["conversation"], entry.id, task_id)

            result = await owned_call(lambda: self._session.commit(change))
            self._initial_admitted = True
            return result

        async def accepted() -> DurableAdmission:
            try:
                return await admit()
            finally:
                self._submission_inflight = False

        # This is only immediate rejection of a second offered input. The
        # actual first winner and acceptance acknowledgement remain in SQLite.
        self._submission_inflight = True
        try:
            return await self._owned(accepted)
        except Exception:
            self._submission_inflight = False
            raise

    async def inspect(
        self,
        task_id: int | None = None,
        *,
        limit: int = 128,
        cursor: Cursor | None = None,
    ) -> TaskInspection | InspectionPage | None:
        self._check("durable_inspect")
        if (
            type(limit) is not int
            or not 1 <= limit <= 128
            or cursor is not None
            and type(cursor) is not Cursor
            or task_id is not None
            and (cursor is not None or limit != 128)
        ):
            raise DurableProductError("invalid_request")
        if task_id is not None:
            task_id = product_task_id(task_id)
        value = await self._read(self._header)
        if task_id is None:
            return await ainspect_live(
                self._session,
                conversation_id=value["conversation"],
                limit=limit,
                cursor=cursor,
                support=self._support(),
            )
        row = await ainspect_task(self._session, task_id, support=self._support())
        if row is not None and row.conversation_id != value["conversation"]:
            raise DurableProductError("incompatible")
        return row

    async def observe_completed(self) -> CompletedWatch:
        self._check("durable_observe_completed")
        value = await self._read(self._header)
        watch = await observe_completed(self._session, value["conversation"])
        if self._closing:
            watch.stop()
            raise DurableProductError("closed")
        self._watches.append(watch)
        return watch

    def _selected(self, selection: DurableRunSelection | None) -> tuple[Tree, Tree]:
        value = self._header()
        return value, select_run(self._session._owner.storage, value, selection)

    async def _scheduler_for_run(self, value: Tree) -> TaskScheduler:
        if self._profile is None:
            raise DurableProductError("profile_required")
        if list(self._profile._selection) != value["selection"] or OwnedJson(
            _tool_tree(self._tool)
        ) != OwnedJson(value["tools"]):
            raise DurableProductError("incompatible")
        profile = self._profile
        if self._scheduler is None:
            scheduler = await owned_call(
                lambda: TaskScheduler(
                    self._session,
                    (profile._definition, self._tool),
                )
            )
            self._scheduler = scheduler
            await scheduler.aopen()
        assert self._scheduler is not None
        return self._scheduler

    def _preflight(self, root: Tree) -> None:
        storage = self._session._owner.storage
        rows = tasks(storage)
        allowed = {root["id"]}
        live_children = [
            row
            for row in rows
            if row.get("owner") == root["id"] and row["state"]["status"] != "terminal"
        ]
        if (
            live_children
            or root["state"]["status"] == "waiting"
            or root["state"].get("checkpoint", {}).get("phase") in ("tools", "blocked")
        ):
            doc, _, _, _ = round_facts(storage, root, rows)
            allowed.update(doc["value"]["tools"])
        for row in rows:
            if row["state"]["status"] == "terminal":
                continue
            if (
                row["conversationId"] != root["conversationId"]
                or row["id"] not in allowed
            ):
                raise DurableProductError("foreign_live_task")
            if row["id"] != root["id"] and (
                row.get("owner") != root["id"] or row["kind"] != self._tool.name
            ):
                raise DurableProductError("foreign_live_task")

    async def resume(
        self, selection: DurableRunSelection | None = None, **unsupported: object
    ) -> DurableRunResult:
        _options(unsupported)
        self._check("durable_resume")
        selection = self._selection if selection is None else selection
        if selection is not None and type(selection) is not DurableRunSelection:
            raise DurableProductError("invalid_request")
        if self._resume_work is not None and not self._resume_work[1].done():
            if self._resume_work[0] != selection:
                raise DurableProductError("selection_required")
            return await asyncio.shield(self._resume_work[1])
        work = asyncio.create_task(self._owned(lambda: self._run(selection)))
        work.add_done_callback(_observe_error)
        self._resume_work = (selection, work)
        return await asyncio.shield(work)

    async def _run(self, selection: DurableRunSelection | None) -> DurableRunResult:
        if self._scheduler is not None:
            await self._scheduler.apause()
        value, root = await self._read(lambda: self._selected(selection))
        watch = await observe_completed(self._session, value["conversation"])
        self._watches.append(watch)
        wake = asyncio.Event()

        async def changed_snapshot(_: object) -> None:
            wake.set()

        watch.start(changed_snapshot)

        bridge = ProductWake(self._session, wake)
        watcher = asyncio.create_task(watch.wait_closed())
        closed = asyncio.create_task(self._session.await_closed())
        scheduler = None
        try:
            if root["state"]["status"] != "terminal":
                scheduler = await self._scheduler_for_run(value)
                await scheduler.apause()
                await self._read(lambda: self._preflight(root))
                scheduler.resume()
            return await self._wait_result(root, value, watch, wake, watcher, closed)
        finally:
            bridge.stop()
            watch.stop()
            self._watches.remove(watch)
            watcher.cancel()
            closed.cancel()
            await asyncio.gather(watcher, closed, return_exceptions=True)
            if scheduler is not None:
                try:
                    await scheduler.apause()
                except SessionClosedError:
                    if not self._closing:
                        raise

    async def _wait_result(
        self,
        root: Tree,
        value: Tree,
        watch: CompletedWatch,
        wake: asyncio.Event,
        watcher: asyncio.Task[Any],
        closed: asyncio.Task[Any],
    ) -> DurableRunResult:
        while True:
            wake.clear()
            metadata = await ainspect_task(
                self._session, root["id"], support=self._support()
            )
            if metadata is None:
                raise DurableProductError("incompatible")
            held = metadata.reason is not None and metadata.status != "terminal"
            if not held and metadata.status != "terminal":
                cursor = None
                while True:
                    page = await ainspect_live(
                        self._session,
                        conversation_id=value["conversation"],
                        cursor=cursor,
                        support=self._support(),
                    )
                    held = any(row.reason is not None for row in page.tasks)
                    if held or page.next is None:
                        break
                    cursor = page.next
                if held:
                    # The hold may have committed after the first parent read.
                    # Preserve the parent's own derived reason, not a child's.
                    metadata = await ainspect_task(
                        self._session, root["id"], support=self._support()
                    )
                    if metadata is None:
                        raise DurableProductError("incompatible")
            if metadata.status == "terminal" or held:
                snapshot = await watch.drain()
                return DurableRunResult(
                    root["id"],
                    "terminal" if metadata.status == "terminal" else "held",
                    metadata,
                    snapshot,
                )
            changed = asyncio.create_task(wake.wait())
            try:
                ready, _ = await asyncio.wait(
                    (changed, watcher, closed), return_when=asyncio.FIRST_COMPLETED
                )
                if watcher in ready or closed in ready:
                    raise DurableProductError("closed")
            finally:
                changed.cancel()
                await asyncio.gather(changed, return_exceptions=True)

    async def authorize_retry(
        self, task_id: int, *, nonce: str, **unsupported: object
    ) -> ResolutionResult:
        _options(unsupported)
        self._check("durable_authorize_retry")
        task_id = product_task_id(task_id)
        DurableRunSelection(task_id, nonce)

        async def authorize() -> ResolutionResult:
            value = await self._read(self._header)
            scheduler = await self._scheduler_for_run(value)
            await scheduler.apause()

            row, request, winner = await self._read(
                lambda: self._retry_proposal(value, task_id, nonce)
            )
            if winner is not None:
                return winner

            await self._read(lambda: self._retry_preflight(value, row))
            target = await scheduler.aresolution_target(task_id)
            return await scheduler.aresolve(
                target,
                nonce=nonce,
                new_request=cast(AgentProviderRequestSnapshot | None, request),
            )

        return await self._owned(authorize)

    def _retry_proposal(
        self, value: Tree, task_id: int, nonce: str
    ) -> tuple[Tree, object, ResolutionResult | None]:
        storage = self._session._owner.storage
        row_record = storage.task(TaskId(task_id))
        if (
            row_record is None
            or row_record.to_tree()["conversationId"] != value["conversation"]
        ):
            raise DurableProductError("incompatible")
        row = cast(Tree, row_record.to_tree())
        original = bound_seed(storage, value)
        request = original if row["kind"] == GENERATION_NAME else None
        proposed = {
            "source_task_id": task_id,
            "new_request": None if request is None else encode_canonical(request),
        }
        edges = retry_edges(storage, value)
        if (
            request is not None
            and task_id != value["initial_generation"]
            and task_id not in edges
        ):
            raise DurableProductError("incompatible")
        winner = _winner(storage, value["conversation"], nonce, proposed)
        if winner is not None:
            return row, request, winner
        if request is not None:
            if any(source == task_id for source, _ in edges.values()):
                raise DurableProductError("successor_exists")
            if any(
                r["kind"] == GENERATION_NAME
                and r["id"] != task_id
                and r["state"]["status"] != "terminal"
                for r in tasks(storage)
            ):
                raise DurableProductError("foreign_live_task")
        return row, request, None

    def _retry_preflight(self, value: Tree, row: Tree) -> None:
        root = row
        if row["kind"] != GENERATION_NAME:
            parent = self._session._owner.storage.task(TaskId(row["owner"]))
            if parent is None:
                raise DurableProductError("incompatible")
            root = cast(Tree, parent.to_tree())
            edges = retry_edges(self._session._owner.storage, value)
            if root["id"] != value["initial_generation"] and root["id"] not in edges:
                raise DurableProductError("incompatible")
        self._preflight(root)

    async def close(self) -> None:
        self._check("durable_close", closing=True)
        if self._close_work is None:
            self._closing = True
            self._session.request_close()
            for watch in self._watches:
                watch.stop()

            async def finish() -> None:
                await self._session.aclose()
                async with self._control:
                    pass

            self._close_work = asyncio.create_task(finish())
            self._close_work.add_done_callback(_observe_error)
        await asyncio.shield(self._close_work)

    async def __aenter__(self) -> DurableProductSession:
        self._check("durable_enter")
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def compact(self, *args: object, **kwargs: object) -> None:
        raise UnsupportedDurableCapability

    async def fork(self, *args: object, **kwargs: object) -> None:
        raise UnsupportedDurableCapability

    async def reset(self, *args: object, **kwargs: object) -> None:
        raise UnsupportedDurableCapability

    async def steer(self, *args: object, **kwargs: object) -> None:
        raise UnsupportedDurableCapability

    async def follow_up(self, *args: object, **kwargs: object) -> None:
        raise UnsupportedDurableCapability

    async def tree(self, *args: object, **kwargs: object) -> None:
        raise UnsupportedDurableCapability

    async def clone(self, *args: object, **kwargs: object) -> None:
        raise UnsupportedDurableCapability

    async def handoff(self, *args: object, **kwargs: object) -> None:
        raise UnsupportedDurableCapability

    async def inject(self, *args: object, **kwargs: object) -> None:
        raise UnsupportedDurableCapability

    async def abort(self, *args: object, **kwargs: object) -> None:
        raise UnsupportedDurableCapability

    async def configure(self, *args: object, **kwargs: object) -> None:
        raise UnsupportedDurableCapability


def _options(unsupported: dict[str, object]) -> None:
    if unsupported:
        raise UnsupportedDurableCapability


async def _acquire(
    opener: Callable[[], Storage], setup: Callable[[Session], None]
) -> Session:
    try:
        return await acquire(opener, setup)
    except DurableProductError:
        raise
    except Exception:  # noqa: BLE001 - storage failures never expose paths or payloads
        raise DurableProductError from None


async def create_durable_product_session(
    path: Path,
    *,
    workspace: Path,
    profile: DurableProviderProfile,
    request_template: AgentProviderRequestSnapshot | None = None,
    tools: Mapping[str, ToolRegistration] | None = None,
    **unsupported: object,
) -> DurableProductSession:
    _options(unsupported)
    if type(profile) is not DurableProviderProfile:
        raise UnsupportedDurableCapability
    path, workspace = _paths(path, workspace)
    tool = _tool_definition(tools, workspace)
    template = _template(profile, workspace, tool, request_template)

    def setup(session: Session) -> None:
        def create(tx: SyncTx) -> None:
            draft = tx.doc(HEADER)
            conversation = tx.create_conversation({"kind": "ownerless"})
            draft.update(
                {
                    "version": 1,
                    "path": str(path),
                    "workspace": str(workspace),
                    "conversation": conversation.id,
                    "selection": list(profile._selection),
                    "tools": _tool_tree(tool),
                    "template": encode_canonical(template),
                    "initial_entry": None,
                    "initial_generation": None,
                }
            )

        session.commit(create)

    session = await _acquire(
        lambda: open_sqlite_storage(str(path), mode="create", durability="full"), setup
    )
    return DurableProductSession(session, path, workspace, profile, tool, None)


async def open_durable_product_session(
    path: Path,
    *,
    workspace: Path,
    profile: DurableProviderProfile | None = None,
    tools: Mapping[str, ToolRegistration] | None = None,
    selection: DurableRunSelection | None = None,
    **unsupported: object,
) -> DurableProductSession:
    _options(unsupported)
    if (
        profile is not None
        and type(profile) is not DurableProviderProfile
        or selection is not None
        and type(selection) is not DurableRunSelection
    ):
        raise UnsupportedDurableCapability
    path, workspace = _paths(path, workspace)
    tool = _tool_definition(tools, workspace)

    initial: list[bool] = []

    def setup(session: Session) -> None:
        def validate() -> None:
            value = header(session._owner.storage, path, workspace)
            if selection is not None:
                select_run(session._owner.storage, value, selection)
            if profile is not None and list(profile._selection) != value["selection"]:
                raise DurableProductError("incompatible")
            initial.append(value["initial_generation"] is not None)

        session.read_on_line(validate)

    session = await _acquire(
        lambda: open_sqlite_storage(
            str(path), mode="open", schema_policy="validate_current"
        ),
        setup,
    )
    product = DurableProductSession(session, path, workspace, profile, tool, selection)
    product._initial_admitted = initial[0]
    return product
