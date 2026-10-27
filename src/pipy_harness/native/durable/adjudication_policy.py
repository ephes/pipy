"""Finite, noninvoking evidence for an explicit historical tool decision.

All storage reads run on the existing Session line. A preserved receipt is
selected before acquiring synthesis-only progress or restart dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, cast

from pipy_harness.native.agent.tools import ToolExecutionOutcome, ToolExecutor

from .codec import OwnedJson
from .generation_tasks import TOOL_NAME, TOOL_VERSION
from .inspection_read import peek_document, round_facts, tasks, terminate_index
from .product_binding import retry_edges
from .recovery_catalog import RecoveryCatalog
from .resolution_policy import _prefix, leaf
from .resolution_read import capture_facts
from .resolution_values import ResolutionError
from .storage import Storage
from .tool_execution import PROGRESS_DOC, RECEIPT_DOC
from .tool_graph import _parent_leaf_round
from .tool_progress_values import detail_value
from .tool_recovery import classify, committed
from .tool_values import checkpoint, match_result, native_tool_output, receipt

Tree = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Decision:
    facts: OwnedJson
    saved: OwnedJson
    decision: str


def _graph(storage: Storage, facts: Tree) -> tuple[Any, Any]:
    row = facts["task"]
    if (row["kind"], row["version"]) != (TOOL_NAME, TOOL_VERSION):
        raise ResolutionError("not_supported")
    if row["state"]["status"] != "pending":
        raise ResolutionError("not_supported")
    frame = checkpoint(row["state"]["checkpoint"])
    if frame["phase"] != "blocked":
        raise ResolutionError("not_supported")
    leaf(facts)
    parent = next((r for r in facts["tasks"] if r["id"] == row.get("owner")), None)
    if (
        parent is None
        or parent["state"]["status"] != "waiting"
        or parent["abortRequested"]
        or parent["background"]
        or parent.get("abandonOnRestart")
    ):
        raise ResolutionError("incompatible")
    doc, assistant, snapshot, entries = round_facts(storage, parent, facts["tasks"])
    if OwnedJson(doc) != OwnedJson(facts["round"]):
        raise ResolutionError("stale")
    value = doc["value"]
    if not _parent_leaf_round(parent, value, facts["tasks"], facts["conversations"]):
        raise ResolutionError("incompatible")
    if terminate_index(parent, value, assistant, facts["tasks"], entries) is not None:
        raise ResolutionError("retained_control")
    call = _prefix(parent, value, assistant, facts["tasks"], entries, row["id"])
    if row["input"] != {
        "kind": "model",
        "assistant": value["assistant"],
        "callId": call.provider_correlation_id,
    } or not snapshot.authorizes(call.tool_name):
        raise ResolutionError("incompatible")
    return call, snapshot


def capture(storage: Storage, task_id: int, header: Tree) -> Tree:
    facts = capture_facts(storage, task_id)
    facts["tasks"] = tasks(storage)
    facts["header"] = header
    found = facts["receipt"]
    if found is not None and found["version"] != RECEIPT_DOC.version:
        raise ResolutionError("receipt_held_needs_repair")
    raw = {} if found is None else found["value"]
    if type(raw) is dict and not raw:
        facts["progress"] = peek_document(
            storage,
            PROGRESS_DOC,
            facts["task"]["conversationId"],
            task_id,
        )
    else:
        # Irrelevant counter/progress changes cannot defeat the first winner.
        facts["task"].pop("restarts", None)
        for row in facts["tasks"]:
            if row["id"] == task_id:
                row.pop("restarts", None)
    return facts


def catalog_admission(storage: Storage, facts: Tree, catalog: RecoveryCatalog) -> None:
    row, header = facts["task"], facts["header"]
    parent = next(r for r in facts["tasks"] if r["id"] == row["owner"])
    if parent["id"] != header["initial_generation"] and parent["id"] not in retry_edges(
        storage, header
    ):
        raise ResolutionError("incompatible")
    allowed = {parent["id"], *facts["round"]["value"]["tools"]}
    for child in facts["tasks"]:
        if child["state"]["status"] == "terminal":
            continue
        if (
            child["conversationId"] != row["conversationId"]
            or child["id"] not in allowed
        ):
            raise ResolutionError("incompatible")
        if child["id"] in (row["id"], parent["id"]) or catalog.unchanged:
            continue
        if (
            committed(storage, child, cast(Tree, catalog.current.thaw())).disposition
            == "replay"
        ):
            raise ResolutionError("incompatible")
        frame = child["state"]["checkpoint"]
        if frame["phase"] == "execute":
            _, assistant, _, _ = round_facts(storage, parent, facts["tasks"])
            call = next(
                c
                for c in assistant.tool_calls
                if c.provider_correlation_id == child["input"]["callId"]
            )
            catalog.validate_intent(frame["intent"], call.tool_name)


def decide(storage: Storage, facts: Tree, catalog: RecoveryCatalog) -> Decision:
    call, snapshot = _graph(storage, facts)
    row = facts["task"]
    catalog.next_snapshot(facts["header"], row["conversationId"], snapshot)
    catalog_admission(storage, facts, catalog)
    frame = checkpoint(row["state"]["checkpoint"])
    found = facts["receipt"]
    raw = {} if found is None else found["value"]
    if type(raw) is not dict or raw:
        try:
            saved, outcome = receipt(raw)
            final = saved["intent"]
            if (
                final is None
                or saved["call"] != row["input"]
                or frame["intent"] is not None
                and OwnedJson(frame["intent"]) != OwnedJson(final)
            ):
                raise ValueError
            catalog.validate_intent(final, call.tool_name)
            match_result(outcome.result, call)
        except (ValueError, TypeError, KeyError, IndexError):
            raise ResolutionError("receipt_held_needs_repair") from None
        return Decision(OwnedJson(facts), OwnedJson(saved), "receipt")
    final = frame["intent"]
    if final is None:
        raise ResolutionError("not_supported")
    if any(r.get("owner") == row["id"] for r in facts["tasks"]) or any(
        c.get("owner", {}).get("taskId") == row["id"] for c in facts["conversations"]
    ):
        raise ResolutionError("incompatible")
    catalog.validate_intent(final, call.tool_name)
    retained = facts["progress"]
    # Reuse C1's exact saved-intent/progress/counter validation. The temporary
    # phase projection is pure; no prepared intent is invented or persisted.
    recovering = {
        **row,
        "state": {
            "status": "pending",
            "checkpoint": {
                "phase": "execute",
                "version": 1,
                "intent": final,
            },
        },
    }
    recovery = classify(
        recovering,
        call,
        snapshot,
        cast(Tree, catalog.current.thaw()),
        {},
        {} if retained is None else retained["value"],
    )
    assert recovery.retained is not None
    value = cast(Tree, recovery.retained.thaw())
    result = ToolExecutor({}).error_result(call, value["output"])
    outcome = ToolExecutionOutcome(
        replace(result, details=detail_value(value["details"])),
        execution_seconds=None,
    )
    saved = {
        "version": 2,
        "intent": final,
        "call": row["input"],
        "key": final["key"],
        "nativeToolOutput": native_tool_output(
            outcome,
            datetime.now(timezone.utc),
            diagnostics=value["diagnostics"],
            interrupted={
                "reason": "interrupted",
                "attempts": value["attempts"],
                "restarts": recovery.restarts,
            },
        ),
    }
    receipt(saved)
    return Decision(OwnedJson(facts), OwnedJson(saved), "interrupted")
