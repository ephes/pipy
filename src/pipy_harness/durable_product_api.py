"""Opt-in async native durable product session; legacy SDK remains separate.

This facade owns one SQLite/Session and at most its existing scheduler host.
Submit admits one literal input. Only explicit resume enables execution.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
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
from .native.durable.completed_projection import (
    CompletedWatch,
    ProjectionClosed,
    observe_completed,
)
from .native.durable.generation import message_entry
from .native.durable.generation_tasks import GENERATION_NAME
from .native.durable.generation_transport import owned_call
from .native.durable.inspection import ainspect_live, ainspect_task
from .native.durable.inspection_read import (
    peek_document,
    round_facts,
    tasks,
    terminate_index,
)
from .native.durable.inspection_values import (
    InspectionError,
    InspectionPage,
    InspectionSupport,
    TaskInspection,
)
from .native.durable.product_acquisition import acquire
from .native.durable.product_binding import (
    bound_seed,
    header,
    retry_edges,
    seed,
    select_run,
)
from .native.durable.product_operation import RunOperation
from .native.durable.product_values import (
    DurableAdjudication,
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
from .native.durable.recovery_catalog import HEADER, RecoveryCatalog
from .native.durable.resolution import GENERATION_ADMISSION, _winner
from .native.durable.resolution_values import ResolutionError, ResolutionResult
from .native.durable.restart_policy import RestartPolicy
from .native.durable.scheduler import TaskScheduler
from .native.durable.session import SessionClosedError
from .native.durable.session_core import Session, SyncTx
from .native.durable.sqlite import open_sqlite_storage
from .native.durable.storage import Cursor, Storage
from .native.durable.tasks import Task
from .native.durable.tool_execution import (
    RECEIPT_DOC,
    ToolRegistration,
    _capture_tool,
    _Tool,
    builtin_registrations,
)
from .native.durable.tool_execution import (
    _tool_definition as _captured_tool_definition,
)
from .native.durable.tool_recovery import committed, counter
from .native.durable.tool_values import _model_declaration

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
) -> _Tool:
    try:
        return _capture_tool(
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
            or tuple(_model_declaration(tool) for tool in request.available_tools)
            != tuple(_model_declaration(tool) for tool in advertised)
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
    """Caller-loop affiliated, single-input, owned selected-run lifetime."""

    def __init__(
        self,
        session: Session,
        path: Path,
        workspace: Path,
        profile: DurableProviderProfile | None,
        tool: _Tool,
        selection: DurableRunSelection | None,
        restart_limit: object = 5,
    ) -> None:
        self._restart_policy = RestartPolicy(restart_limit)
        self._session, self._path, self._workspace = session, path, workspace
        self._tool_driver = tool
        self._profile, self._tool, self._selection = (
            profile,
            _captured_tool_definition(tool),
            selection,
        )
        self._catalog: RecoveryCatalog | None = None
        self._loop = asyncio.get_running_loop()
        self._control = asyncio.Lock()
        self._operations: set[asyncio.Task[Any]] = set()
        self._watches: list[CompletedWatch] = []
        self._scheduler: TaskScheduler | None = None
        self._closing = False
        self._initial_admitted = False
        self._submission_inflight = False
        self._close_work: asyncio.Task[None] | None = None
        self._current: RunOperation | None = None

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

    def _mutation_gate(self) -> None:
        if self._closing:
            raise DurableProductError("closed")

    @staticmethod
    def _product_error(error: BaseException) -> DurableProductError:
        if isinstance(error, (DurableProductError, InspectionError, ResolutionError)):
            return DurableProductError(error.code)
        if isinstance(error, SessionClosedError):
            return DurableProductError("closed")
        return DurableProductError()

    def _spawn(
        self,
        body: Callable[[], Awaitable[T]],
        install: Callable[[asyncio.Task[T]], None] | None = None,
        *,
        track: bool = True,
    ) -> asyncio.Task[T]:
        gate = self._loop.create_future()

        async def gated() -> T:
            await gate
            return await body()

        offered = gated()
        try:
            work = asyncio.create_task(offered)
        except BaseException:
            offered.close()
            raise
        work.add_done_callback(_observe_error)
        if track:
            self._operations.add(work)
            work.add_done_callback(self._operations.discard)
        try:
            if install is not None:
                install(work)
        except BaseException:
            # The task remains owned/observed until its still-gated cancellation
            # settles. No body or native effect has been entered.
            work.cancel()
            raise
        gate.set_result(None)
        return work

    async def _owned(self, body: Callable[[], Awaitable[T]]) -> T:
        self._mutation_gate()

        async def serialized() -> T:
            async with self._control:
                self._mutation_gate()
                try:
                    return await body()
                except (DurableProductError, InspectionError, ResolutionError):
                    raise
                except SessionClosedError:
                    raise DurableProductError("closed") from None
                except Exception:  # noqa: BLE001 - private backend payloads stay private
                    raise DurableProductError from None

        return await asyncio.shield(self._spawn(serialized))

    @staticmethod
    async def _settle(work: asyncio.Task[Any]) -> None:
        # Ownership barriers cannot be shortened by cancellation of a waiter.
        joined = asyncio.gather(work, return_exceptions=True)
        while not joined.done():
            try:
                await asyncio.shield(joined)
            except asyncio.CancelledError:
                continue
        joined.result()

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
        self._mutation_gate()
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
                restart_limit=self._restart_policy,
            )
        row = await ainspect_task(
            self._session,
            task_id,
            support=self._support(),
            restart_limit=self._restart_policy,
        )
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

    async def _scheduler_for_run(
        self, value: Tree, catalog: RecoveryCatalog
    ) -> TaskScheduler:
        if self._profile is None:
            raise DurableProductError("profile_required")
        if list(self._profile._selection) != value["selection"]:
            raise DurableProductError("incompatible")
        profile = self._profile
        if self._scheduler is None:
            scheduler = await owned_call(
                lambda: TaskScheduler(
                    self._session,
                    (
                        profile._bound_definition(catalog),
                        _captured_tool_definition(
                            replace(self._tool_driver, catalog=catalog)
                        ),
                    ),
                    restart_limit=self._restart_policy,
                )
            )
            self._catalog = catalog
            self._scheduler = scheduler
            await scheduler.aopen()
        elif self._catalog != catalog:
            raise DurableProductError("incompatible")
        return self._scheduler

    def _preflight_ownership(self, root: Tree) -> tuple[list[Tree], list[Tree]]:
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

        return rows, live_children

    def _preflight(self, root: Tree, value: Tree) -> RecoveryCatalog:
        _, live_children = self._preflight_ownership(root)
        storage = self._session._owner.storage

        try:
            catalog = RecoveryCatalog.capture(value, _tool_tree(self._tool))
            for row in live_children:
                if row["state"].get("checkpoint", {}).get("phase") != "execute":
                    continue
                saved = peek_document(
                    storage, RECEIPT_DOC, row["conversationId"], row["id"]
                )
                raw = {} if saved is None else saved["value"]
                if type(raw) is dict and not raw and "restarts" in row:
                    counter(row["restarts"])
            if not catalog.unchanged:
                self._recovery_preflight(storage, root, live_children, catalog)
            return catalog
        except (ValueError, TypeError, KeyError, IndexError):
            raise DurableProductError("incompatible") from None

    def _recovery_preflight(
        self,
        storage: Storage,
        root: Tree,
        children: list[Tree],
        catalog: RecoveryCatalog,
    ) -> None:
        # No old provider request, pre-intent child or mixed safe replay can
        # enter the exception. Refusal occurs before the scheduler is opened.
        if (
            root["state"].get("checkpoint", {}).get("phase") != "tools"
            or root["abortRequested"]
            or root["background"]
            or root.get("abandonOnRestart")
        ):
            raise DurableProductError("incompatible")
        rows = tasks(storage)
        doc, assistant, snapshot, entries = round_facts(storage, root, rows)
        if terminate_index(root, doc["value"], assistant, rows, entries) is not None:
            raise DurableProductError("incompatible")
        catalog.next_snapshot(self._header(), root["conversationId"], snapshot)
        for row in children:
            frame = row["state"]["checkpoint"]
            # At the restart limit a call ends without running again, so it
            # needs no current capability (R15-2b); a pre-intent call too.
            limited = self._limit_ends(row)
            if not (limited and frame["phase"] == "call"):
                decision = committed(storage, row, _tool_tree(self._tool))
                if decision.disposition == "replay" and not limited:
                    raise DurableProductError("incompatible")
            if frame["phase"] == "execute":
                call_id = row["input"]["callId"]
                call = next(
                    c
                    for c in assistant.tool_calls
                    if c.provider_correlation_id == call_id
                )
                catalog.validate_intent(frame["intent"], call.tool_name)

    def _limit_ends(self, row: Tree) -> bool:
        """Whether this owner's restart limit ends the call when it next runs.

        Opening the scheduler first counts the restart of a cold running row.
        Only a count the ending can record qualifies: any other row keeps its
        existing classification, so a valid receipt still settles first.
        """
        restarts = row.get("restarts", 0)
        if row["state"]["status"] == "running" and type(restarts) is int:
            restarts += 1
        try:
            counter(restarts)
        except ValueError:
            return False
        return restarts > 0 and self._restart_policy.reached({"restarts": restarts})

    def _run_selection(
        self, selection: DurableRunSelection | None
    ) -> DurableRunSelection | None:
        selection = self._selection if selection is None else selection
        if selection is not None and type(selection) is not DurableRunSelection:
            raise DurableProductError("invalid_request")
        return selection

    def _own_close_stop(self, operation: RunOperation, error: BaseException) -> bool:
        if not (
            operation.close_requested
            and self._close_work is not None
            and self._closing
            and self._session.failure is None
        ):
            return False
        if isinstance(error, ProjectionClosed):
            return error.reason in ("stopped", "session_closed")
        return self._product_error(error).code == "closed"

    def _start_operation(self, selection: DurableRunSelection | None) -> RunOperation:
        self._mutation_gate()
        operation: RunOperation | None = None

        def install(work: asyncio.Task[DurableRunResult]) -> None:
            nonlocal operation
            operation = RunOperation(selection, work)
            self._current = operation

        async def serialized() -> DurableRunResult:
            assert operation is not None
            async with self._control:
                try:
                    self._mutation_gate()
                    return await self._run(selection)
                except BaseException as error:
                    if isinstance(error, asyncio.CancelledError):
                        raise
                    raise self._operation_failure(operation, error) from None

        self._spawn(serialized, install)
        assert operation is not None
        return operation

    def _operation_failure(
        self, operation: RunOperation, error: BaseException
    ) -> DurableProductError:
        if self._own_close_stop(operation, error):
            return DurableProductError("closed")
        return self._product_error(error)

    async def resume(
        self, selection: DurableRunSelection | None = None, **unsupported: object
    ) -> DurableRunResult:
        _options(unsupported)
        self._check("durable_resume")
        self._mutation_gate()
        selection = self._run_selection(selection)
        current = self._current
        if current is not None and not current.task.done():
            if current.selection != selection:
                raise DurableProductError("selection_required")
            work = current.task
        else:
            work = self._start_operation(selection).task
        return await asyncio.shield(work)

    def _admission_check(self) -> None:
        self._mutation_gate()
        if self._session.failure is not None:
            raise DurableProductError("closed")

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
                if self._profile is None:
                    raise DurableProductError("profile_required")
                catalog = await self._read(lambda: self._preflight(root, value))
                scheduler = await self._scheduler_for_run(value, catalog)
                await scheduler.apause()
                self._admission_check()
                scheduler.resume()
            self._admission_check()
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
                    if not self._closing or self._session.failure is not None:
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
                self._session,
                root["id"],
                support=self._support(),
                restart_limit=self._restart_policy,
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
                        restart_limit=self._restart_policy,
                    )
                    held = any(row.reason is not None for row in page.tasks)
                    if held or page.next is None:
                        break
                    cursor = page.next
                if held:
                    # The hold may have committed after the first parent read.
                    # Preserve the parent's own derived reason, not a child's.
                    metadata = await ainspect_task(
                        self._session,
                        root["id"],
                        support=self._support(),
                        restart_limit=self._restart_policy,
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

    async def adjudicate_interrupted(
        self, task_id: int, *, nonce: str, **unsupported: object
    ) -> DurableAdjudication:
        """Explicitly end an uncertain historical attempt without invoking it.

        Replaying a validated decision may be auth-free. Fresh admission needs
        a matching profile; only a later explicit resume can execute a provider.
        """
        from .native.durable import adjudication

        _options(unsupported)
        self._check("durable_adjudicate_interrupted")
        self._mutation_gate()
        task_id = product_task_id(task_id)
        DurableRunSelection(task_id, nonce)

        def target() -> Any:
            value = self._header()
            if (
                self._profile is not None
                and list(self._profile._selection) != value["selection"]
            ):
                raise DurableProductError("incompatible")
            storage = self._session._owner.storage
            found = adjudication.winner(storage, value, task_id, nonce)
            if found is not None:
                return found
            if self._profile is None:
                raise DurableProductError("profile_required")
            adjudication.refuse_second_decision(storage, value, task_id)
            catalog = RecoveryCatalog.capture(value, _tool_tree(self._tool))
            if self._scheduler is not None and self._catalog != catalog:
                raise DurableProductError("incompatible")
            return adjudication.prepare(
                storage, self._path, self._workspace, task_id, _tool_tree(self._tool)
            )

        async def authorize() -> DurableAdjudication:
            try:
                adjudication.metadata(self._scheduler, drained=False)
                if self._scheduler is not None:
                    await self._scheduler.apause()
                adjudication.metadata(self._scheduler)
                # Keep private fixed policy codes across this operation only.
                chosen = await owned_call(lambda: self._session.read_on_line(target))
                if type(chosen) is DurableAdjudication:
                    return chosen
                return await owned_call(
                    lambda: adjudication.commit(
                        self._session,
                        self._path,
                        self._workspace,
                        _tool_tree(self._tool),
                        chosen,
                        nonce,
                        self._scheduler,
                    )
                )
            except ResolutionError as error:
                raise DurableProductError(error.code) from None

        return await self._owned(authorize)

    async def authorize_retry(
        self, task_id: int, *, nonce: str, **unsupported: object
    ) -> ResolutionResult:
        _options(unsupported)
        self._check("durable_authorize_retry")
        self._mutation_gate()
        task_id = product_task_id(task_id)
        DurableRunSelection(task_id, nonce)

        async def authorize() -> ResolutionResult:
            value = await self._read(self._header)
            catalog = RecoveryCatalog.capture(value, _tool_tree(self._tool))
            if not catalog.unchanged:
                raise DurableProductError("incompatible")
            scheduler = await self._scheduler_for_run(value, catalog)
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
        self._preflight_ownership(root)

    def _start_close(self) -> asyncio.Task[None]:
        self._check("durable_close", closing=True)
        if self._close_work is not None:
            return self._close_work

        def install(work: asyncio.Task[None]) -> None:
            self._close_work = work
            self._closing = True

        async def finish() -> None:
            try:
                try:
                    self._session.request_close()
                    if self._current is not None:
                        self._current.close_requested = True
                    for watch in self._watches:
                        watch.stop()
                finally:
                    await self._session.aclose()
            finally:
                # Native errors stay on this same close task, after every
                # admitted owner physically retires. Close never joins itself.
                for work in tuple(self._operations):
                    await self._settle(work)

        return self._spawn(finish, install, track=False)

    async def close(self) -> None:
        """Retire physical ownership, then report a native close failure."""
        await asyncio.shield(self._start_close())

    async def __aenter__(self) -> DurableProductSession:
        self._check("durable_enter")
        return self

    async def __aexit__(self, *args: object) -> None:
        work = self._start_close()
        interrupted = False
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                interrupted = True
        work.result()
        if interrupted and not (len(args) > 1 and args[1] is not None):
            raise asyncio.CancelledError

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
    restart_limit: object = 5,
    **unsupported: object,
) -> DurableProductSession:
    _options(unsupported)
    if type(profile) is not DurableProviderProfile:
        raise UnsupportedDurableCapability
    path, workspace = _paths(path, workspace)
    tool = _tool_definition(tools, workspace)
    definition = _captured_tool_definition(tool)
    template = _template(profile, workspace, definition, request_template)

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
                    "tools": _tool_tree(definition),
                    "template": encode_canonical(template),
                    "initial_entry": None,
                    "initial_generation": None,
                }
            )

        session.commit(create)

    session = await _acquire(
        lambda: open_sqlite_storage(str(path), mode="create", durability="full"), setup
    )
    return DurableProductSession(
        session, path, workspace, profile, tool, None, restart_limit
    )


async def open_durable_product_session(
    path: Path,
    *,
    workspace: Path,
    profile: DurableProviderProfile | None = None,
    tools: Mapping[str, ToolRegistration] | None = None,
    selection: DurableRunSelection | None = None,
    restart_limit: object = 5,
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
    product = DurableProductSession(
        session, path, workspace, profile, tool, selection, restart_limit
    )
    product._initial_admitted = initial[0]
    return product
