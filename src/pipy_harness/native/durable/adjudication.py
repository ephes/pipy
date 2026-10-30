"""One explicit historical decision on the existing Session transaction line.

The audit is a decision witness, never a second tool outcome. Only the ordinary
tool settlement publishes a model entry. Retry authority is entirely separate.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

from pipy_harness.native.agent.messages import AgentAssistantMessage, AgentToolCall
from pipy_harness.native.agent.request import AgentProviderRequestSnapshot

from .adjudication_policy import Decision, capture, decide
from .canonical_codec import decode_canonical, encode_canonical
from .codec import OwnedJson
from .documents import DocFamily, define_doc_family, resolve
from .generation_tasks import (
    GENERATION_NAME,
    GENERATION_VERSION,
    TOOL_NAME,
    TOOL_VERSION,
)
from .inspection_read import entries, peek_document, tasks
from .product_binding import header, retry_edges
from .product_values import DurableAdjudication
from .records import DocumentCreate, TaskId, TaskRecord, safe_integer, shape
from .recovery_catalog import RecoveryCatalog
from .resolution_values import ResolutionError
from .scheduler import TaskScheduler
from .session_core import Session, SyncTx
from .storage import DocumentQuery, Storage
from .tool_execution import RECEIPT_DOC
from .tool_graph import _round_calls, child_result_id, owned_child
from .tool_values import checkpoint, intent, receipt
from .tx import CommittedDocument, _Created

Tree = dict[str, Any]
AUDIT: DocFamily[None, Tree] = define_doc_family(
    "pipy.operator.adjudication",
    1,
    "conversation",
    lambda _: {},
    "latest",
    "initial",
    checkpoint_when=lambda _value, _ops, _info: False,
)


def fingerprint(value: object) -> str:
    # Existing canonical encoding makes arbitrary depth a flat node table. This
    # is an audit fingerprint, not a new serialization format or tool carrier.
    encoded = json.dumps(
        encode_canonical(value),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def document_identity(found: Tree) -> Tree:
    return {
        "version": found["version"],
        "record": {
            k: v
            for k, v in found["record"].items()
            if k not in {"createdAt", "retiredAt"}
        },
    }


def _acquired_receipt_identity(tx: SyncTx, task_id: int) -> Tree:
    """Read only our already-acquired receipt's actual canonical Tx metadata.

    Draft intentionally exposes no record ID. The ID must nevertheless join the
    audit in this SAME commit, including a newly minted receipt. No second
    commit, guessed ID, mirror, or new Storage API can supply that guarantee.
    Core._enter enforces the existing open/affinity guard before map access.
    """
    core = tx._core
    core._enter()
    key, address = resolve(RECEIPT_DOC, task_id=task_id)
    entry = core._latest.get(key)
    if entry is None or entry.retiring or entry.change is None:
        raise ResolutionError("incompatible")
    entry.change.guard()
    target = entry.target
    if type(target) not in (_Created, CommittedDocument):
        raise ResolutionError("incompatible")
    assert isinstance(target, (_Created, CommittedDocument))
    record = cast(Tree, target.record.to_tree())
    if any(record.get(k) != v for k, v in address.to_tree().items()):
        raise ResolutionError("incompatible")
    return document_identity({"version": target.version, "record": record})


def metadata(scheduler: TaskScheduler | None, *, drained: bool = True) -> None:
    if scheduler is None:
        return
    with scheduler._guard:
        closed = (
            not scheduler._opened
            or scheduler._owner._line_sealed
            or scheduler._owner._failure is not None
            or scheduler._host_done
        )
        busy = scheduler._invocation is not None or scheduler._draining
    if closed:
        raise ResolutionError("closed")
    if drained and busy:
        raise ResolutionError("busy")


def prepare(
    storage: Storage, path: Path, workspace: Path, task_id: int, current: Tree
) -> Decision:
    try:
        actual = header(storage, path, workspace)
        catalog = RecoveryCatalog.capture(actual, current)
        return decide(storage, capture(storage, task_id, actual), catalog)
    except ResolutionError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, StopIteration):
        raise ResolutionError("incompatible") from None


def _source(
    storage: Storage, actual: Tree, audit: Tree, task_id: int
) -> tuple[Tree, Tree]:
    source = cast(Tree, TaskRecord(audit["source"]).to_tree())
    found = storage.task(TaskId(task_id))
    if found is None:
        raise ResolutionError("incompatible")
    row = cast(Tree, found.to_tree())
    if (
        source["id"] != task_id
        or source["conversationId"] != actual["conversation"]
        or (source["kind"], source["version"]) != (TOOL_NAME, TOOL_VERSION)
        or source["state"]["status"] != "pending"
        or checkpoint(source["state"]["checkpoint"])["phase"] != "blocked"
        or any(
            OwnedJson(row.get(k)) != OwnedJson(source.get(k))
            for k in ("id", "conversationId", "kind", "version", "owner", "input")
        )
    ):
        raise ResolutionError("incompatible")
    parent = storage.task(TaskId(source["owner"]))
    if parent is None:
        raise ResolutionError("incompatible")
    parent_row = cast(Tree, parent.to_tree())
    if (
        parent_row["conversationId"] != source["conversationId"]
        or (parent_row["kind"], parent_row["version"])
        != (GENERATION_NAME, GENERATION_VERSION)
        or source["owner"] != actual["initial_generation"]
        and source["owner"] not in retry_edges(storage, actual)
        or source["abortRequested"]
        or source["background"]
        or source.get("abandonOnRestart")
    ):
        raise ResolutionError("incompatible")
    return row, parent_row


def _authority(
    storage: Storage, actual: Tree, row: Tree, parent: Tree, audit: Tree
) -> AgentToolCall:
    by_id = entries(storage, row["conversationId"])
    found = by_id.get(row["input"]["assistant"])
    if found is None:
        raise ResolutionError("incompatible")
    assistant = cast(Tree, found.to_tree())
    if (
        assistant.get("byTaskId") != parent["id"]
        or assistant["kind"] != "pi.assistant"
        or len(assistant["model"]) != 1
    ):
        raise ResolutionError("incompatible")
    message = decode_canonical(assistant["model"][0])
    snapshot = decode_canonical(assistant["data"]["request"])
    if (
        type(message) is not AgentAssistantMessage
        or type(snapshot) is not AgentProviderRequestSnapshot
    ):
        raise ResolutionError("incompatible")
    _round_calls(audit["round"], message, snapshot)
    if audit["round"]["assistant"] != found.id:
        raise ResolutionError("incompatible")
    rows = tasks(storage)
    chosen = None
    for call, slot in zip(message.tool_calls, audit["round"]["slots"], strict=True):
        if "taskId" in slot:
            owned_child(parent, found.id, call, slot, rows)
        if slot.get("taskId") == row["id"]:
            chosen = call
    if chosen is None:
        raise ResolutionError("incompatible")
    final = intent(audit["authority"])
    source_frame = checkpoint(audit["source"]["state"]["checkpoint"])
    if (
        final["call"] != row["input"]
        or source_frame["intent"] is not None
        and OwnedJson(source_frame["intent"]) != OwnedJson(final)
    ):
        raise ResolutionError("incompatible")
    catalog = RecoveryCatalog.capture(actual, actual["tools"])
    catalog.next_snapshot(actual, row["conversationId"], snapshot)
    catalog.validate_intent(final, chosen.tool_name)
    return chosen


def _preterminal(storage: Storage, row: Tree, audit: Tree) -> None:
    state, binding = row["state"], audit["receipt"]
    frame = checkpoint(state["checkpoint"])
    if state["status"] not in ("pending", "running"):
        raise ResolutionError("incompatible")
    if frame["phase"] == "settle":
        if frame["key"] != audit["authority"]["key"]:
            raise ResolutionError("incompatible")
    elif state != {
        "status": "pending",
        "checkpoint": {
            "phase": "blocked",
            "version": 1,
            "intent": None,
            "reason": "incompatible",
            "call": row["input"],
        },
    }:
        raise ResolutionError("incompatible")
    found = peek_document(storage, RECEIPT_DOC, row["conversationId"], row["id"])
    if (
        found is None
        or OwnedJson(document_identity(found)) != OwnedJson(binding["identity"])
        or fingerprint(found["value"]) != binding["digest"]
    ):
        raise ResolutionError("incompatible")
    _receipt_binding(found["value"], row, audit)


def _receipt_binding(raw: object, row: Tree, audit: Tree) -> None:
    saved, _ = receipt(raw)
    binding = audit["receipt"]
    if (
        saved["call"] != row["input"]
        or OwnedJson(saved["intent"]) != OwnedJson(audit["authority"])
        or saved["version"] != binding["version"]
        or fingerprint(saved) != binding["digest"]
        or fingerprint(
            {
                "nativeToolOutput": saved["nativeToolOutput"],
                "call": saved["call"],
                "key": saved["key"],
            }
        )
        != binding["projection"]
        or audit["decision"] == "interrupted"
        and saved["nativeToolOutput"].get("ending") != "interrupted"
    ):
        raise ResolutionError("incompatible")


def _terminal(
    storage: Storage, row: Tree, parent: Tree, audit: Tree, call: AgentToolCall
) -> None:
    outcome = row["state"]["outcome"]
    if outcome["status"] in ("aborted", "faulted"):
        return  # Decision-only ACK; no retired receipt or fabricated settlement.
    if outcome["status"] not in ("completed", "failed"):
        raise ResolutionError("incompatible")
    by_id = entries(storage, row["conversationId"])
    result_id = child_result_id(
        parent,
        row["input"]["assistant"],
        call,
        {"callId": call.provider_correlation_id, "taskId": row["id"]},
        tasks(storage),
        by_id,
    )
    if result_id is None:
        raise ResolutionError("incompatible")
    data = cast(Tree, by_id[result_id].to_tree())["data"]
    binding = audit["receipt"]
    if binding["version"] == 2:
        if (
            set(data) != {"nativeToolReceipt"}
            or fingerprint(data["nativeToolReceipt"]) != binding["digest"]
        ):
            raise ResolutionError("incompatible")
        _receipt_binding(data["nativeToolReceipt"], row, audit)
    elif (
        set(data) != {"nativeToolOutput", "call", "key"}
        or fingerprint(data) != binding["projection"]
    ):
        raise ResolutionError("incompatible")


def _audit_binding(audit: Tree, task_id: int) -> None:
    binding = shape(audit["receipt"], {"identity", "version", "digest", "projection"})
    if type(binding["version"]) is not int or binding["version"] not in (1, 2):
        raise ResolutionError("incompatible")
    if audit["decision"] == "interrupted" and binding["version"] != 2:
        raise ResolutionError("incompatible")
    identity = shape(binding["identity"], {"version", "record"})
    record = DocumentCreate(identity["record"]).to_tree()
    if (
        type(identity["version"]) is not int
        or identity["version"] != RECEIPT_DOC.version
        or set(record) != {"id", "kind", "scope"}
        or record["kind"] != RECEIPT_DOC.kind
        or record["scope"] != {"kind": "task", "taskId": task_id}
        or any(
            type(binding[k]) is not str
            or re.fullmatch("[0-9a-f]{64}", cast(str, binding[k])) is None
            for k in ("digest", "projection")
        )
    ):
        raise ResolutionError("incompatible")


def winner(
    storage: Storage, actual: Tree, task_id: int, nonce: str
) -> DurableAdjudication | None:
    try:
        found = peek_document(storage, AUDIT, actual["conversation"], key=nonce)
        if found is None:
            return None
        audit = shape(
            found["value"],
            {
                "version",
                "action",
                "proposal",
                "source",
                "authority",
                "decision",
                "receipt",
                "round",
            },
        )
        proposal = shape(audit["proposal"], {"task_id"})
        if safe_integer(proposal["task_id"]) != task_id:
            raise ResolutionError("conflict")
        if (
            type(audit["version"]) is not int
            or audit["version"] != 1
            or audit["action"] != "interrupted"
        ):
            raise ResolutionError("incompatible")
        _audit_binding(audit, task_id)
        row, parent = _source(storage, actual, audit, task_id)
        call = _authority(storage, actual, row, parent, audit)
        if row["state"]["status"] == "terminal":
            _terminal(storage, row, parent, audit, call)
        else:
            _preterminal(storage, row, audit)
        decision = audit["decision"]
        if type(decision) is not str:
            raise ResolutionError("incompatible")
        return DurableAdjudication(task_id, decision)
    except ResolutionError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, StopIteration):
        raise ResolutionError("incompatible") from None


def refuse_second_decision(storage: Storage, actual: Tree, task_id: int) -> None:
    """A reblocked decision is replayed with its nonce, never adjudicated twice."""
    try:
        _existing_decisions(storage, actual, task_id)
    except ResolutionError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, StopIteration):
        raise ResolutionError("incompatible") from None


def _existing_decisions(storage: Storage, actual: Tree, task_id: int) -> None:
    cursor = None
    while True:
        page = storage.scan_documents(
            DocumentQuery(
                {
                    "scope": {
                        "kind": "conversation",
                        "conversationId": actual["conversation"],
                    },
                    "at": "current",
                    "kind": AUDIT.kind,
                }
            ),
            128,
            cursor,
        )
        for row in page.items:
            record = row.to_tree()
            nonce = record.get("key")
            if type(nonce) is not str or re.fullmatch("[0-9a-f]{32}", nonce) is None:
                raise ResolutionError("incompatible")
            found = peek_document(storage, AUDIT, actual["conversation"], key=nonce)
            if found is None:
                raise ResolutionError("incompatible")
            proposal = shape(found["value"].get("proposal"), {"task_id"})
            if safe_integer(proposal["task_id"]) == task_id:
                winner(storage, actual, task_id, nonce)
                raise ResolutionError("not_supported")
        cursor = page.next
        if cursor is None:
            return


def commit(
    session: Session,
    path: Path,
    workspace: Path,
    current: Tree,
    expected: Decision,
    nonce: str,
    scheduler: TaskScheduler | None,
) -> DurableAdjudication:
    facts = cast(Tree, expected.facts.thaw())
    task_id, cid = facts["task"]["id"], facts["task"]["conversationId"]

    def change(tx: SyncTx) -> DurableAdjudication:
        storage = session._owner.storage
        metadata(scheduler)
        actual = header(storage, path, workspace)
        prior = winner(storage, actual, task_id, nonce)
        if prior is not None:
            return prior
        refuse_second_decision(storage, actual, task_id)
        captured = capture(storage, task_id, actual)
        if OwnedJson(captured) != expected.facts:
            raise ResolutionError("stale")
        decide(storage, captured, RecoveryCatalog.capture(actual, current))
        saved = cast(Tree, expected.saved.thaw())
        doc = tx.doc(RECEIPT_DOC, task_id=task_id)
        if expected.decision == "interrupted":
            doc.update(saved)
        elif OwnedJson(doc.snapshot().thaw()) != expected.saved:
            raise ResolutionError("stale")
        identity = _acquired_receipt_identity(tx, task_id)
        audit = tx.doc(AUDIT, conversation_id=cid, key=nonce, seed=None)
        audit.update(
            {
                "version": 1,
                "action": "interrupted",
                "proposal": {"task_id": task_id},
                "source": facts["task"],
                "authority": saved["intent"],
                "round": facts["round"]["value"],
                "decision": expected.decision,
                "receipt": {
                    "identity": identity,
                    "version": saved["version"],
                    "digest": fingerprint(saved),
                    "projection": fingerprint(
                        {
                            "nativeToolOutput": saved["nativeToolOutput"],
                            "call": saved["call"],
                            "key": saved["key"],
                        }
                    ),
                },
            }
        )
        actual_task = storage.task(TaskId(task_id))
        assert actual_task is not None
        tx.set_task(
            {
                **actual_task.to_tree(),
                "state": {
                    "status": "pending",
                    "checkpoint": {
                        "phase": "settle",
                        "version": 1,
                        "key": saved["key"],
                    },
                },
            }
        )
        return DurableAdjudication(task_id, expected.decision)

    return session._commit(change, conversation_id=cid)
