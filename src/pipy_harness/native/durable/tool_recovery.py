"""Finite, noninvoking recovery decisions over committed model-tool authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

from pipy_harness.native.agent.messages import AgentToolCall
from pipy_harness.native.agent.request import AgentProviderRequestSnapshot

from .canonical_codec import _schema_lists, decode_canonical, encode_canonical
from .codec import OwnedJson
from .generation_tasks import TOOL_NAME, TOOL_VERSION
from .records import safe_integer
from .tool_progress_values import initial_progress, progress
from .tool_values import _model_declaration, checkpoint, match_result, receipt, refuse

Tree = dict[str, Any]


def counter(value: object) -> int:
    number = safe_integer(value, positive=False)
    if number < 0:
        refuse()
    return number


@dataclass(frozen=True, slots=True)
class Recovery:
    disposition: Literal["replay", "interrupted", "settle"]
    saved: OwnedJson | None
    retained: OwnedJson | None
    restarts: int | None


def classify(
    row: Tree,
    call: AgentToolCall,
    snapshot: AgentProviderRequestSnapshot,
    current: Tree,
    raw_receipt: object,
    raw_progress: object,
) -> Recovery:
    """Graph ownership is proved by the caller on its existing Session line.

    Historical blocked states never enter this classifier. Receipt presence is
    checked before synthesizing output or considering current invocation power.
    """
    frame = checkpoint(row["state"]["checkpoint"])
    if (
        (row["kind"], row["version"]) != (TOOL_NAME, TOOL_VERSION)
        or row["state"]["status"] not in ("pending", "running")
        or frame["phase"] not in ("execute", "settle")
        or row["abortRequested"]
        or row["background"]
        or row.get("abandonOnRestart")
    ):
        refuse()
    final = frame.get("intent")
    key = frame["key"] if final is None else final["key"]
    if type(raw_receipt) is not dict or raw_receipt:
        saved, outcome = receipt(raw_receipt)
        if (
            saved["call"] != row["input"]
            or saved["key"] != key
            or final is not None
            and OwnedJson(saved["intent"]) != OwnedJson(final)
        ):
            refuse()
        match_result(outcome.result, call)
        return Recovery("settle", OwnedJson(saved), None, None)
    if final is None:
        refuse()
    restarts = counter(row["restarts"]) if "restarts" in row else None
    offered = next(
        (
            tool
            for tool in snapshot.request.available_tools
            if tool.name == call.tool_name
        ),
        None,
    )
    if (
        offered is None
        or not snapshot.authorizes(call.tool_name)
        or final["call"] != row["input"]
        or final["workspace"] != str(snapshot.request.cwd)
        or current["family"] != "model_tool"
        or current["workspace"] != final["workspace"]
        or final["definition"]["execution_mode"] == "sequential"
        or final["definition"]["schema_mode"] != offered.schema_mode
        or OwnedJson(final["definition"]["schema"])
        != OwnedJson(
            _schema_lists(decode_canonical(encode_canonical(offered.input_schema)))
        )
    ):
        refuse()
    retained = (
        progress(raw_progress)
        if type(raw_progress) is not dict or raw_progress
        else initial_progress(row["input"], key, attempts=None, restarts=None)
    )
    value = cast(Tree, retained.thaw())
    if value["call"] != row["input"] or value["key"] != key:
        refuse()
    if value["attempts"] is not None:
        counter(value["attempts"])
    registration = current["tools"].get(call.tool_name)
    replay = (
        registration is not None
        and registration["supported"]
        and current["workspace"] == final["workspace"]
        and OwnedJson(registration["descriptor"]) == OwnedJson(final["definition"])
        and _model_declaration(decode_canonical(registration["advertised"]))
        == _model_declaration(offered)
        and final["definition"]["replay"] == "safe"
    )
    return Recovery("replay" if replay else "interrupted", None, retained, restarts)


def committed(storage: Any, row: Tree, current: Tree) -> Recovery:
    """Read-only counterpart of TaskTx.model_tool_call; no document seeding."""
    from .inspection_read import (
        conversations,
        peek_document,
        round_facts,
        tasks,
        terminate_index,
    )
    from .tool_execution import PROGRESS_DOC, RECEIPT_DOC
    from .tool_graph import _parent_leaf_round, child_result_id, result_value

    rows = tasks(storage)
    parent = next((r for r in rows if r["id"] == row.get("owner")), None)
    if parent is None or parent["state"]["status"] != "waiting":
        refuse()
    doc, assistant, snapshot, entries = round_facts(storage, parent, rows)
    value = doc["value"]
    owners = conversations(storage)
    if (
        not _parent_leaf_round(parent, value, rows, owners)
        or terminate_index(parent, value, assistant, rows, entries) is not None
        or any(r.get("owner") == row["id"] for r in rows)
        or any(c.get("owner", {}).get("taskId") == row["id"] for c in owners)
    ):
        refuse()
    selected = None
    for call, slot in zip(assistant.tool_calls, value["slots"], strict=True):
        if slot.get("taskId") == row["id"]:
            selected = call
            break
        id = slot.get("entryId")
        if id is None:
            id = child_result_id(parent, value["assistant"], call, slot, rows, entries)
        if id not in entries:
            refuse()
        result_value(entries[id], parent, value["assistant"], call, slot)
    if selected is None:
        refuse()
    saved = peek_document(storage, RECEIPT_DOC, row["conversationId"], row["id"])
    raw_saved = {} if saved is None else saved["value"]
    retained = (
        peek_document(storage, PROGRESS_DOC, row["conversationId"], row["id"])
        if type(raw_saved) is dict and not raw_saved
        else None
    )
    return classify(
        row,
        selected,
        snapshot,
        current,
        raw_saved,
        {} if retained is None else retained["value"],
    )
