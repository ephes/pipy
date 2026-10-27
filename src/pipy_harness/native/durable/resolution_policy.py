"""Finite native retry eligibility; no resolver, auth, port or handler calls."""

from __future__ import annotations

from typing import Any, NoReturn

from pipy_harness.native.agent.request import AgentProviderRequestSnapshot

from .canonical_codec import decode_canonical
from .codec import OwnedJson
from .generation_tasks import (
    GENERATION_NAME,
    GENERATION_VERSION,
    TOOL_NAME,
    TOOL_VERSION,
)
from .inspection_read import round_facts, terminate_index
from .inspection_values import InspectionSupport
from .resolution_values import ResolutionError
from .storage import Storage
from .tool_graph import (
    _parent_leaf_round,
    child_result_id,
    result_value,
)
from .tool_values import _model_declaration, blocked_record, checkpoint

Tree = dict[str, Any]
LIVE = ("pending", "running", "waiting", "sleeping", "completing")


def _refuse(code: str = "incompatible") -> NoReturn:
    raise ResolutionError(code)


def leaf(facts: Tree) -> None:
    task = facts["task"]
    if task["abortRequested"] or task["background"] or task.get("abandonOnRestart"):
        _refuse()
    if any(
        r.get("owner") == task["id"] and r["state"]["status"] in LIVE
        for r in facts["tasks"]
    ):
        _refuse()
    if any(
        c.get("owner", {}).get("taskId") == task["id"] for c in facts["conversations"]
    ):
        _refuse()
    own = next(
        (c for c in facts["conversations"] if c["id"] == task["conversationId"]), None
    )
    if own is None or "owner" in own:
        _refuse()


def tool_retry(
    storage: Storage, facts: Tree, support: InspectionSupport
) -> tuple[Tree, Tree]:
    task = facts["task"]
    if (task["kind"], task["version"]) != (TOOL_NAME, TOOL_VERSION):
        _refuse("not_supported")
    raw = task["state"].get("checkpoint")
    if (
        task["state"]["status"] == "pending"
        and type(raw) is dict
        and raw.get("phase") == "blocked"
        and facts["receipt"] is not None
    ):
        _refuse("receipt_held_needs_repair")
    if task["state"]["status"] != "pending" or not blocked_record(task):
        _refuse("not_supported")
    leaf(facts)
    parent = next((r for r in facts["tasks"] if r["id"] == task.get("owner")), None)
    if (
        parent is None
        or parent["state"]["status"] != "waiting"
        or parent["abortRequested"]
    ):
        _refuse()
    doc, assistant, snapshot, entries = round_facts(storage, parent, facts["tasks"])
    if OwnedJson(doc) != OwnedJson(facts["round"]):
        _refuse("stale")
    value = doc["value"]
    if not _parent_leaf_round(parent, value, facts["tasks"], facts["conversations"]):
        _refuse()
    if terminate_index(parent, value, assistant, facts["tasks"], entries) is not None:
        _refuse("retained_control")
    selected = _prefix(parent, value, assistant, facts["tasks"], entries, task["id"])
    _registration(task, selected, snapshot, support)
    return parent, value


def _prefix(
    parent: Tree,
    value: Tree,
    assistant: Any,
    rows: list[Tree],
    entries: Any,
    task_id: int,
) -> Any:
    selected = None
    for call, slot in zip(assistant.tool_calls, value["slots"], strict=True):
        if slot.get("taskId") == task_id:
            selected = call
            break
        result_id = slot.get("entryId")
        if result_id is None:
            result_id = child_result_id(
                parent, value["assistant"], call, slot, rows, entries
            )
        if result_id not in entries:
            _refuse()
        result_value(
            entries[result_id],
            parent,
            value["assistant"],
            call,
            slot,
        )
    if selected is None:
        _refuse()
    seen = False
    for slot in value["slots"]:
        if slot.get("taskId") == task_id:
            seen = True
        elif seen and "taskId" in slot:
            later = next((r for r in rows if r["id"] == slot["taskId"]), None)
            if (
                later is None
                or later["abortRequested"]
                or later["state"]
                != {"status": "pending", "checkpoint": {"phase": "call"}}
            ):
                _refuse()
    return selected


def _registration(
    task: Tree,
    selected: Any,
    snapshot: AgentProviderRequestSnapshot,
    support: InspectionSupport,
) -> None:
    definition = support.definition(TOOL_NAME)
    current = None if definition is None else definition["support"]
    if (
        definition is None
        or current is None
        or current["family"] != "model_tool"
        or definition["version"] != TOOL_VERSION
    ):
        _refuse()
    registration = current["tools"].get(selected.tool_name)
    offered = next(
        (t for t in snapshot.request.available_tools if t.name == selected.tool_name),
        None,
    )
    if (
        registration is None
        or not registration["supported"]
        or offered is None
        or current["workspace"] != str(snapshot.request.cwd)
        or _model_declaration(decode_canonical(registration["advertised"]))
        != _model_declaration(offered)
    ):
        _refuse()
    previous = checkpoint(task["state"]["checkpoint"]).get("intent")
    if previous is not None and (
        previous["call"] != task["input"]
        or previous["workspace"] != current["workspace"]
        or OwnedJson(previous["definition"]) != OwnedJson(registration["descriptor"])
    ):
        _refuse()


def generation_retry(
    facts: Tree, support: InspectionSupport, new_request: object
) -> None:
    task = facts["task"]
    if (
        (task["kind"], task["version"]) != (GENERATION_NAME, GENERATION_VERSION)
        or "owner" in task
        or task["state"]["status"] != "terminal"
        or task["state"]["outcome"]["status"] not in ("failed", "faulted", "aborted")
    ):
        _refuse("not_supported")
    leaf(facts)
    snapshot = decode_canonical(new_request)
    definition = support.definition(GENERATION_NAME)
    current = None if definition is None else definition["support"]
    if (
        type(snapshot) is not AgentProviderRequestSnapshot
        or definition is None
        or current is None
        or current["family"] != "generation"
        or definition["version"] != GENERATION_VERSION
        or (snapshot.request.provider_name, snapshot.request.model_id)
        != (current["provider"], current["model"])
    ):
        _refuse()
