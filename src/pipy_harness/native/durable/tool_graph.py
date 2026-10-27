"""Session-line-only direct model-tool graph checks; no general DE15 scheduler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
)
from pipy_harness.native.agent.request import AgentProviderRequestSnapshot

from .canonical_codec import decode_canonical, encode_canonical
from .checkpoint_codec import decode_checkpoint
from .codec import OwnedJson
from .generation_tasks import (
    GENERATION_NAME,
    GENERATION_VERSION,
    TOOL_NAME,
    TOOL_VERSION,
    tool_input,
)
from .records import EntryRecord, safe_integer
from .session_core import AsyncTx
from .tool_output_values import ending_text, project_tool_output
from .tool_values import (
    checkpoint,
    entry_tool_output,
    match_result,
    refuse,
)

Tree = dict[str, Any]
LIVE = ("pending", "running", "waiting", "sleeping", "completing")


@dataclass(frozen=True, slots=True)
class ModelCall:
    parent: Tree
    call: AgentToolCall
    snapshot: AgentProviderRequestSnapshot
    round: Tree
    has_descendants: bool = False


async def entry(
    tx: AsyncTx, id: int, entries: dict[int, EntryRecord] | None = None
) -> EntryRecord | None:
    staged = next((e for e in tx.staged_entries() if e.id == id), None)
    if staged is not None:
        return staged
    return await tx.entry(id) if entries is None else entries.get(id)


def overlay(rows: list[Tree], tx: AsyncTx) -> list[Tree]:
    merged = {r["id"]: r for r in rows}
    merged.update({r.id: r.to_tree() for r in tx.staged_tasks()})
    return list(merged.values())


async def round_context(
    tx: AsyncTx, parent: Tree, entries: dict[int, EntryRecord] | None = None
) -> tuple[Tree, AgentAssistantMessage, AgentProviderRequestSnapshot]:
    from .generation import ROUND_DOC

    if (parent["kind"], parent["version"]) != (
        GENERATION_NAME,
        GENERATION_VERSION,
    ) or parent["abortRequested"]:
        refuse()
    state = parent["state"]
    if state["status"] not in ("waiting", "running", "pending"):
        refuse()
    frame = decode_checkpoint(
        state["checkpoint"],
        name=GENERATION_NAME,
        version=GENERATION_VERSION,
        phase="tools",
        role="provider_outcome",
    )
    round_value = (await tx.doc(ROUND_DOC, task_id=parent["id"])).snapshot().thaw()
    if type(round_value) is not dict or set(round_value) != {
        "assistant",
        "tools",
        "slots",
    }:
        refuse()
    if state["status"] == "waiting" and (
        state["policy"] != "allSettled" or state["on"] != round_value["tools"]
    ):
        refuse()
    found = await entry(tx, round_value["assistant"], entries)
    if found is None:
        refuse()
    tree = cast(Tree, found.to_tree())
    if (
        tree["conversationId"] != parent["conversationId"]
        or tree.get("byTaskId") != parent["id"]
        or tree["kind"] != "pi.assistant"
    ):
        refuse()
    assistant = decode_canonical(tree["model"][0])
    snapshot = decode_canonical(tree["data"]["request"])
    if (
        type(assistant) is not AgentAssistantMessage
        or type(snapshot) is not AgentProviderRequestSnapshot
    ):
        refuse()
    stored = tree["data"]["receipt"]
    stored["phase"] = "tools"
    if OwnedJson(stored) != OwnedJson(frame.to_tree()):
        refuse()
    _round_calls(round_value, assistant, snapshot)
    return round_value, assistant, snapshot


def _round_calls(
    round_value: Tree,
    assistant: AgentAssistantMessage,
    snapshot: AgentProviderRequestSnapshot,
) -> None:
    calls = assistant.tool_calls
    if len(calls) != len(round_value["slots"]) or len(
        {c.provider_correlation_id for c in calls}
    ) != len(calls):
        refuse()
    if type(round_value["slots"]) is not list or type(round_value["tools"]) is not list:
        refuse()
    for id in round_value["tools"]:
        safe_integer(id)
    tools = []
    for call, slot in zip(calls, round_value["slots"], strict=True):
        if type(slot) is not dict or slot.get("callId") != call.provider_correlation_id:
            refuse()
        expected = (
            {"callId", "taskId"}
            if snapshot.authorizes(call.tool_name)
            else {"callId", "entryId"}
        )
        if set(slot) != expected:
            refuse()
        safe_integer(slot["taskId"] if "taskId" in slot else slot["entryId"])
        if "taskId" in slot:
            tools.append(slot["taskId"])
    if tools != round_value["tools"] or len(set(tools)) != len(tools):
        refuse()


def owned_child(
    parent: Tree, assistant_id: int, call: AgentToolCall, slot: Tree, rows: list[Tree]
) -> Tree:
    """Validate the actual owned link independently of child progress."""
    child = next((r for r in rows if r["id"] == slot["taskId"]), None)
    if (
        child is None
        or child.get("owner") != parent["id"]
        or child["conversationId"] != parent["conversationId"]
        or (child["kind"], child["version"]) != (TOOL_NAME, TOOL_VERSION)
        or child["input"]
        != {
            "kind": "model",
            "assistant": assistant_id,
            "callId": call.provider_correlation_id,
        }
    ):
        refuse()
    assert child is not None
    return child


def _child_result_id(
    parent: Tree,
    assistant_id: int,
    call: AgentToolCall,
    slot: Tree,
    rows: list[Tree],
    found: EntryRecord | None = None,
) -> int | None:
    child = owned_child(parent, assistant_id, call, slot, rows)
    state = child["state"]
    if state["status"] != "terminal":
        return None
    terminal = state["outcome"]
    if terminal["status"] not in ("completed", "failed"):
        return None
    if terminal["status"] == "failed" and "result" not in terminal:
        return None
    output = state["outcome"]["result"]
    if type(output) is not dict or output.get("kind") != "model":
        refuse()
    id = safe_integer(output.get("entryId"))
    if terminal["status"] == "failed":
        if found is None or found.id != id:
            refuse()
        result_value(found, parent, assistant_id, call, slot)
        raw = entry_tool_output(cast(Tree, found.to_tree())["data"])[0]
        if (
            raw["version"] != 2
            or raw["ending"] not in ("failed", "interrupted")
            or set(terminal) != {"status", "error", "result"}
            or terminal["error"] != {"message": ending_text(raw)}
            or set(output) != {"kind", "entryId"}
            or child["abortRequested"]
            or child["background"]
            or child.get("abandonOnRestart")
            or any(row.get("owner") == child["id"] for row in rows)
        ):
            refuse()
    elif found is not None:
        raw = entry_tool_output(cast(Tree, found.to_tree())["data"])[0]
        if raw.get("version") == 2 and raw.get("ending") != "completed":
            refuse()
    return id


def child_result_id(
    parent: Tree,
    assistant_id: int,
    call: AgentToolCall,
    slot: Tree,
    rows: list[Tree],
    entries: dict[int, EntryRecord],
) -> int | None:
    child = owned_child(parent, assistant_id, call, slot, rows)
    output = child["state"].get("outcome", {}).get("result")
    found = (
        entries.get(output["entryId"])
        if type(output) is dict and type(output.get("entryId")) is int
        else None
    )
    id = _child_result_id(parent, assistant_id, call, slot, rows, found)
    if id is not None:
        if found is None:
            refuse()
        result_value(found, parent, assistant_id, call, slot)
    return id


async def result(
    tx: AsyncTx,
    parent: Tree,
    assistant_id: int,
    call: AgentToolCall,
    slot: Tree,
    rows: list[Tree],
    entries: dict[int, EntryRecord] | None = None,
) -> tuple[int, AgentToolResultMessage]:
    # Candidate acquisition is not result-bearing authorization. The shared
    # predicate runs only after its actual entry/receipt have been captured.
    if "taskId" in slot:
        child = owned_child(parent, assistant_id, call, slot, rows)
        output = child["state"].get("outcome", {}).get("result")
        if type(output) is not dict:
            refuse()
        id = safe_integer(output.get("entryId"))
    else:
        id = slot["entryId"]
    found = await entry(tx, id, entries)
    if found is None:
        refuse()
    if (
        "taskId" in slot
        and _child_result_id(parent, assistant_id, call, slot, rows, found) != id
    ):
        refuse()
    return id, result_value(found, parent, assistant_id, call, slot)


def result_value(
    found: EntryRecord,
    parent: Tree,
    assistant_id: int,
    call: AgentToolCall,
    slot: Tree,
) -> AgentToolResultMessage:
    """Pure owned-entry validation shared with committed observation."""
    tree = cast(Tree, found.to_tree())
    if (
        tree["conversationId"] != parent["conversationId"]
        or tree["kind"] != "pi.tool-result"
        or len(tree["model"]) != 1
    ):
        refuse()
    message = decode_canonical(tree["model"][0])
    if type(message) is not AgentToolResultMessage:
        refuse()
    match_result(message, call)
    if "taskId" in slot:
        if tree.get("byTaskId") != slot["taskId"]:
            refuse()
        full, actual, recorded_call, key = entry_tool_output(tree["data"])
        if (
            recorded_call
            != {
                "kind": "model",
                "assistant": assistant_id,
                "callId": call.provider_correlation_id,
            }
            or type(key) is not str
            or not key
        ):
            refuse()
        if OwnedJson(tree["model"][0]) != OwnedJson(
            encode_canonical(
                project_tool_output(full, actual),
                codec_version=tree["model"][0]["codec_version"],
            )
        ):
            refuse()
    elif (
        tree.get("byTaskId") != parent["id"]
        or not message.is_error
        or message.details != {"code": "tool_unavailable"}
    ):
        refuse()
    return message


async def _ordered_call(
    tx: AsyncTx,
    current: Tree,
    parent: Tree,
    round_value: Tree,
    assistant: AgentAssistantMessage,
    snapshot: AgentProviderRequestSnapshot,
    rows: list[Tree],
    entries: dict[int, EntryRecord] | None,
) -> AgentToolCall | None:
    for call, slot in zip(assistant.tool_calls, round_value["slots"], strict=True):
        if slot.get("taskId") == current["id"]:
            if current["input"] != {
                "kind": "model",
                "assistant": round_value["assistant"],
                "callId": call.provider_correlation_id,
            } or not snapshot.authorizes(call.tool_name):
                return None
            return call
        _, previous = await result(
            tx, parent, round_value["assistant"], call, slot, rows, entries
        )
        if previous.terminate:
            return None
    return None


async def model_call(
    tx: AsyncTx,
    current: Tree,
    rows: list[Tree],
    conversations: list[Tree],
    entries: dict[int, EntryRecord] | None = None,
) -> ModelCall | None:
    if (current["kind"], current["version"]) != (
        TOOL_NAME,
        TOOL_VERSION,
    ) or "owner" not in current:
        return None
    try:
        tool_input(current["input"])
        if current["abortRequested"] or current.get("abandonOnRestart"):
            return None
        if any(
            r.get("owner") == current["id"] and r["state"]["status"] in LIVE
            for r in rows
        ) or any(
            c.get("owner", {}).get("taskId") == current["id"]
            or c["id"] == current["conversationId"]
            and "owner" in c
            for c in conversations
        ):
            return None
        parent = next((r for r in rows if r["id"] == current["owner"]), None)
        if (
            parent is None
            or parent["conversationId"] != current["conversationId"]
            or parent["state"]["status"] != "waiting"
        ):
            return None
        round_value, assistant, snapshot = await round_context(tx, parent, entries)
        if not _parent_leaf_round(parent, round_value, rows, conversations):
            return None
        call = await _ordered_call(
            tx, current, parent, round_value, assistant, snapshot, rows, entries
        )
        if call is None:
            return None
        state = current["state"]
        if (
            state["status"] not in ("pending", "running")
            or checkpoint(state["checkpoint"])["phase"] == "blocked"
        ):
            return None
        return ModelCall(
            parent,
            call,
            snapshot,
            round_value,
            any(row.get("owner") == current["id"] for row in rows),
        )
    except (ValueError, TypeError, KeyError, IndexError):
        return None


async def reconcile(
    tx: AsyncTx,
    rows: list[Tree],
    entries: dict[int, EntryRecord],
    conversations: list[Tree],
) -> list[Tree]:
    rows = overlay(rows, tx)
    for parent in tuple(rows):
        if (
            (parent["kind"], parent["version"]) != (GENERATION_NAME, GENERATION_VERSION)
            or parent["abortRequested"]
            or parent["state"]["status"] != "waiting"
        ):
            continue
        try:
            value, assistant, _ = await round_context(tx, parent, entries)
            if not _parent_leaf_round(parent, value, rows, conversations):
                continue
            for call, slot in zip(assistant.tool_calls, value["slots"], strict=True):
                await result(tx, parent, value["assistant"], call, slot, rows, entries)
        except (ValueError, TypeError, KeyError, IndexError):
            continue
        tx.set_task(
            {
                **parent,
                "state": {
                    "status": "pending",
                    "checkpoint": parent["state"]["checkpoint"],
                },
            }
        )
        rows = overlay(rows, tx)
    return rows


def _dependency_ids(value: object, by_id: dict[int, Tree]) -> set[int]:
    if (
        type(value) is not dict
        or type(value.get("assistant")) is not int
        or type(value.get("slots")) is not list
    ):
        return set()
    ids = {value["assistant"]}
    for slot in value["slots"]:
        if type(slot) is not dict:
            continue
        if type(slot.get("entryId")) is int:
            ids.add(slot["entryId"])
        child = by_id.get(slot["taskId"]) if type(slot.get("taskId")) is int else None
        if child is not None and child["state"]["status"] == "terminal":
            output = child["state"]["outcome"].get("result")
            if type(output) is dict and type(output.get("entryId")) is int:
                ids.add(output["entryId"])
    return ids


async def capture_entries(tx: AsyncTx, rows: list[Tree]) -> dict[int, EntryRecord]:
    """Read graph dependencies before writes; overlays supply entries created later."""
    from .generation import ROUND_DOC

    ids: set[int] = set()
    by_id = {row["id"]: row for row in rows}
    for parent in rows:
        if not _needs_entries(parent):
            continue
        value = (await tx.doc(ROUND_DOC, task_id=parent["id"])).snapshot().thaw()
        ids.update(_dependency_ids(value, by_id))
    result = {}
    for id in ids:
        found = await tx.entry(id)
        if found is not None:
            result[id] = found
    return result


def _needs_entries(parent: Tree) -> bool:
    state = parent["state"]
    return (
        (parent["kind"], parent["version"]) == (GENERATION_NAME, GENERATION_VERSION)
        and state["status"] in ("pending", "running", "waiting")
        and type(state["checkpoint"]) is dict
        and state["checkpoint"].get("phase") == "tools"
    )


def _parent_leaf_round(
    parent: Tree, value: Tree, rows: list[Tree], conversations: list[Tree]
) -> bool:
    return (
        "owner" not in parent
        and not any(
            r.get("owner") == parent["id"]
            and r["state"]["status"] in LIVE
            and r["id"] not in value["tools"]
            for r in rows
        )
        and not any(
            c.get("owner", {}).get("taskId") == parent["id"]
            or c["id"] == parent["conversationId"]
            and "owner" in c
            for c in conversations
        )
    )
