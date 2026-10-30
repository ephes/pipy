"""Explicit native operator authorization, not Pi retryTask API parity.

The existing Session line is the only mutation authority. Private durable nonce
winners survive task-document retirement; the outward result is numeric only.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any, cast

from pipy_harness.native.agent.request import AgentProviderRequestSnapshot

from .canonical_codec import encode_canonical
from .codec import OwnedJson
from .documents import DocFamily, define_doc_family
from .generation import ROUND_DOC
from .generation import _initial as generation_initial
from .generation_tasks import GENERATION_NAME, GENERATION_VERSION, TOOL_TASK
from .inspection import _Read
from .inspection_read import peek_document
from .inspection_values import InspectionSupport
from .records import RecordError, TaskId, TaskRecord, safe_integer
from .resolution_policy import generation_retry, tool_retry
from .resolution_read import capture_facts
from .resolution_values import ResolutionError, ResolutionResult, ResolutionTarget
from .scheduler import _state
from .session import SessionClosedError, SessionFailed
from .session_core import SyncTx
from .tasks import define_task

if TYPE_CHECKING:
    from .scheduler import TaskScheduler

Tree = dict[str, Any]
AUDIT: DocFamily[None, Tree] = define_doc_family(
    "pipy.operator.resolution",
    1,
    "conversation",
    lambda _: {},
    "latest",
    "initial",
    checkpoint_when=lambda _value, _ops, _info: False,
)


async def _never(*args: Any) -> None:
    raise ResolutionError("not_supported")


GENERATION_ADMISSION = define_task(
    GENERATION_NAME, GENERATION_VERSION, generation_initial, {}, _never
)


def _metadata(
    scheduler: TaskScheduler, task_id: int | tuple[int, ...] | None = None
) -> None:
    # Closed owned metadata only. No I/O, user code, future completion or joining.
    with scheduler._guard:
        closed = scheduler._owner._line_sealed or scheduler._owner._failure is not None
        invocation = scheduler._invocation
        busy = invocation is not None and (
            invocation.task_id in task_id
            if type(task_id) is tuple
            else invocation.task_id == task_id
        )
    if closed:
        raise ResolutionError("closed")
    if busy:
        raise ResolutionError("busy")


def _support(scheduler: TaskScheduler) -> InspectionSupport:
    with scheduler._guard:
        generation = scheduler._definition_generation
    return generation.support


def resolution_target(scheduler: TaskScheduler, task_id: int) -> ResolutionTarget:
    try:
        safe_integer(task_id)
    except RecordError:
        raise ResolutionError("incompatible") from None
    _metadata(scheduler)
    try:
        facts = scheduler._session.read_on_line(
            lambda: capture_facts(scheduler._owner.storage, task_id)
        )
        return ResolutionTarget(scheduler, OwnedJson(facts))
    except (SessionClosedError, SessionFailed):
        raise ResolutionError("closed") from None
    except ResolutionError:
        raise
    except Exception:  # noqa: BLE001 - no private payload in operator failures
        raise ResolutionError("unavailable") from None


def _inputs(
    scheduler: TaskScheduler, target: ResolutionTarget, nonce: str, new_request: object
) -> Tree:
    if type(target) is not ResolutionTarget or target._scheduler is not scheduler:
        raise ResolutionError("stale")
    if type(nonce) is not str or re.fullmatch("[0-9a-f]{32}", nonce) is None:
        raise ResolutionError("incompatible")
    if (
        new_request is not None
        and type(new_request) is not AgentProviderRequestSnapshot
    ):
        raise ResolutionError("incompatible")
    return {
        "source_task_id": target.task_id,
        "new_request": None if new_request is None else encode_canonical(new_request),
    }


def _winner(
    storage: Any, cid: int, nonce: str, proposal: Tree
) -> ResolutionResult | None:
    winner = peek_document(storage, AUDIT, cid, key=nonce)
    if winner is None:
        return None
    value = winner["value"]
    if (
        type(value) is not dict
        or set(value) != {"version", "proposal", "source", "new_task_id"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or OwnedJson(value["proposal"]) != OwnedJson(proposal)
    ):
        raise ResolutionError("conflict")
    source = TaskRecord(value["source"]).to_tree()
    newer = storage.task(TaskId(safe_integer(value["new_task_id"])))
    older = storage.task(TaskId(proposal["source_task_id"]))
    if newer is None or older is None:
        raise ResolutionError("conflict")
    _winner_binding(
        source, cast(Tree, older.to_tree()), cast(Tree, newer.to_tree()), cid, proposal
    )
    return ResolutionResult(proposal["source_task_id"], value["new_task_id"])


def _winner_binding(
    source: Tree, old: Tree, new: Tree, cid: int, proposal: Tree
) -> None:
    if (
        source["id"] != proposal["source_task_id"]
        or source["conversationId"] != cid
        or new["conversationId"] != cid
        or new["id"] == source["id"]
        or (new["kind"], new["version"]) != (source["kind"], source["version"])
        or new.get("owner") != source.get("owner")
    ):
        raise ResolutionError("conflict")
    if proposal["new_request"] is None:
        if OwnedJson(new["input"]) != OwnedJson(source["input"]) or old["state"] != {
            "status": "terminal",
            "outcome": {"status": "aborted", "reason": "operator_retry"},
        }:
            raise ResolutionError("conflict")
    elif OwnedJson(old) != OwnedJson(source) or OwnedJson(new["input"]) != OwnedJson(
        proposal["new_request"]
    ):
        raise ResolutionError("conflict")


def resolve(
    scheduler: TaskScheduler,
    target: ResolutionTarget,
    *,
    nonce: str,
    new_request: AgentProviderRequestSnapshot | None = None,
) -> ResolutionResult:
    proposal = _inputs(scheduler, target, nonce, new_request)
    _metadata(scheduler)
    expected = target._facts
    cid = target.conversation_id

    def change(tx: SyncTx) -> ResolutionResult:
        storage = scheduler._owner.storage
        # Durable first winner precedes stale-state/busy gates, including reopen.
        winner = _winner(storage, cid, nonce, proposal)
        if winner is not None:
            return winner
        _metadata(scheduler, target.task_id)
        facts = capture_facts(storage, target.task_id)
        if OwnedJson(facts) != expected:
            raise ResolutionError("stale")
        support = _support(scheduler)
        if proposal["new_request"] is None:
            parent, round_value = tool_retry(storage, facts, support)
            _metadata(scheduler, (target.task_id, parent["id"]))
            round_draft = tx.doc(ROUND_DOC, task_id=parent["id"])
            audit = tx.doc(AUDIT, conversation_id=cid, key=nonce, seed=None)
            new_id = tx.create_task(
                TOOL_TASK,
                facts["task"]["input"],
                ownership={"kind": "task", "taskId": parent["id"]},
                conversation_id=cid,
            )
            _replace(tx, facts["task"], parent, round_value, round_draft, new_id)
        else:
            generation_retry(facts, support, proposal["new_request"])
            _metadata(scheduler, target.task_id)
            audit = tx.doc(AUDIT, conversation_id=cid, key=nonce, seed=None)
            new_id = tx.create_task(
                GENERATION_ADMISSION,
                proposal["new_request"],
                ownership={"kind": "conversation"},
                conversation_id=cid,
            )
        audit.update(
            {
                "version": 1,
                "proposal": proposal,
                "source": facts["task"],
                "new_task_id": new_id,
            }
        )
        return ResolutionResult(target.task_id, new_id)

    try:
        return scheduler._session._commit(change, conversation_id=cid)
    except (SessionClosedError, SessionFailed):
        raise ResolutionError("closed") from None
    except ResolutionError:
        raise
    except Exception:  # noqa: BLE001 - no private payload in operator failures
        raise ResolutionError("unavailable") from None


def _replace(
    tx: SyncTx, old: Tree, parent: Tree, round_value: Tree, draft: Any, new_id: int
) -> None:
    old_id = old["id"]
    slots = [
        {**slot, "taskId": new_id} if slot.get("taskId") == old_id else slot
        for slot in round_value["slots"]
    ]
    tools = [new_id if id == old_id else id for id in round_value["tools"]]
    draft.update({**round_value, "slots": slots, "tools": tools})
    tx.set_task(
        _state(
            old,
            {
                "status": "terminal",
                "outcome": {"status": "aborted", "reason": "operator_retry"},
            },
        )
    )
    tx.set_task({**parent, "state": {**parent["state"], "on": tools}})


async def aresolution_target(
    scheduler: TaskScheduler, task_id: int
) -> ResolutionTarget:
    scheduler._session._refuse_reentry("aresolution_target")
    work = _Read(lambda: resolution_target(scheduler, task_id))
    return cast(ResolutionTarget, await asyncio.shield(work.work))


async def aresolve(
    scheduler: TaskScheduler,
    target: ResolutionTarget,
    *,
    nonce: str,
    new_request: AgentProviderRequestSnapshot | None = None,
) -> ResolutionResult:
    scheduler._session._refuse_reentry("aresolve")
    # Own caller input on this loop, before the first suspension.
    proposal = _inputs(scheduler, target, nonce, new_request)
    from .canonical_codec import decode_canonical

    detached = (
        None
        if proposal["new_request"] is None
        else decode_canonical(proposal["new_request"])
    )
    work = _Read(
        lambda: resolve(
            scheduler,
            target,
            nonce=nonce,
            new_request=cast(AgentProviderRequestSnapshot | None, detached),
        )
    )
    return cast(ResolutionResult, await asyncio.shield(work.work))
