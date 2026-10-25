"""One private product header; guarded reads reuse the Session authority."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from pipy_harness.native.agent.messages import AgentUserMessage
from pipy_harness.native.agent.request import AgentProviderRequestSnapshot

from .canonical_codec import decode_canonical, encode_canonical
from .codec import OwnedJson
from .documents import Doc, define_doc, resolve
from .execution_support import ExecutionSupport
from .generation import message_entry
from .generation_tasks import GENERATION_NAME, GENERATION_VERSION
from .inspection_read import conversations, entries, tasks
from .product_values import DurableProductError, DurableRunSelection
from .provider_checkpoint import FAMILIES
from .records import safe_integer
from .resolution import AUDIT, _winner_binding
from .storage import DocumentQuery, Storage

Tree = dict[str, Any]
HEADER: Doc[Tree] = define_doc("pipy.product.binding", 1, "session", lambda: {})


def header(storage: Storage, path: Path, workspace: Path) -> Tree:
    from .records import DocumentId

    _, address = resolve(HEADER)
    record = storage.find_document(address)
    if record is None:
        raise DurableProductError("uninitialized_product")
    stored = storage.document(DocumentId(record.id))
    if stored is None:
        raise DurableProductError("incompatible")
    found = stored.to_tree()
    if found["version"] != 1 or found["record"] != record.to_tree():
        raise DurableProductError("incompatible")
    value = found["value"]
    if (
        type(value) is not dict
        or set(value)
        != {
            "version",
            "path",
            "workspace",
            "conversation",
            "selection",
            "tools",
            "template",
            "initial_entry",
            "initial_generation",
        }
        or type(value["version"]) is not int
        or value["version"] != 1
    ):
        raise DurableProductError("incompatible")
    if value["path"] != str(path) or value["workspace"] != str(workspace):
        raise DurableProductError("incompatible")
    safe_integer(value["conversation"])
    selected = value["selection"]
    if (
        type(selected) is not list
        or len(selected) != 3
        or any(type(s) is not str for s in selected)
    ):
        raise DurableProductError("incompatible")
    template = decode_canonical(value["template"])
    if type(template) is not AgentProviderRequestSnapshot:
        raise DurableProductError("incompatible")
    request = template.request
    if (
        request.cwd != workspace
        or request.session_id is not None
        or request.provider_name != selected[0]
        or request.model_id != selected[1]
        or request.user_prompt
        or request.messages
        or request.provider_header_callback is not None
    ):
        raise DurableProductError("incompatible")
    _profile(value, template, workspace)
    _structure(storage, value)
    return cast(Tree, value)


def _profile(
    value: Tree, template: AgentProviderRequestSnapshot, workspace: Path
) -> None:
    from pipy_harness.native.tools.base import ToolDefinition

    if value["selection"][2] not in FAMILIES:
        raise DurableProductError("incompatible")
    tool = ExecutionSupport(value["tools"]).to_tree()
    if tool["family"] != "model_tool" or tool["workspace"] != str(workspace):
        raise DurableProductError("incompatible")
    advertised = []
    for name, row in tool["tools"].items():
        definition = decode_canonical(row["advertised"])
        if type(definition) is not ToolDefinition or definition.name != name:
            raise DurableProductError("incompatible")
        advertised.append(definition)
    if OwnedJson(encode_canonical(tuple(advertised))) != OwnedJson(
        encode_canonical(template.request.available_tools)
    ):
        raise DurableProductError("incompatible")


def _structure(storage: Storage, value: Tree) -> None:
    rows = conversations(storage)
    if not any(r == {"id": value["conversation"]} for r in rows):
        raise DurableProductError("incompatible")
    # A generic empty kernel root may coexist. No other product/history owner
    # is inferred from row order or numerical identities.
    for row in rows:
        if row["id"] != value["conversation"] and (
            entries(storage, row["id"]) or tasks(storage, row["id"])
        ):
            raise DurableProductError("foreign_live_task")
    initial = value["initial_generation"]
    if (initial is None) != (value["initial_entry"] is None):
        raise DurableProductError("incompatible")
    if initial is None:
        if entries(storage, value["conversation"]) or tasks(
            storage, value["conversation"]
        ):
            raise DurableProductError("incompatible")
    else:
        bound_seed(storage, value)


def seed(
    template: AgentProviderRequestSnapshot, message: AgentUserMessage
) -> AgentProviderRequestSnapshot:
    return replace(
        template,
        request=replace(
            template.request,
            user_prompt=message.content.value,
            messages=(message,),
        ),
    )


def bound_seed(storage: Storage, value: Tree) -> AgentProviderRequestSnapshot:
    from .records import EntryId, TaskId

    safe_integer(value["initial_generation"])
    safe_integer(value["initial_entry"])
    lookup = storage.entry(
        EntryId(value["initial_entry"]), conversation_id=value["conversation"]
    )
    root = storage.task(TaskId(value["initial_generation"]))
    if lookup is None or root is None:
        raise DurableProductError("incompatible")
    entry = cast(Tree, lookup.entry.to_tree())
    if entry.get("kind") != "pi.user" or "byTaskId" in entry:
        raise DurableProductError("incompatible")
    message = decode_canonical(entry["model"][0])
    if type(message) is not AgentUserMessage or OwnedJson(entry) != OwnedJson(
        {
            **message_entry(message),
            "id": value["initial_entry"],
            "conversationId": value["conversation"],
        }
    ):
        raise DurableProductError("incompatible")
    template = decode_canonical(value["template"])
    if type(template) is not AgentProviderRequestSnapshot:
        raise DurableProductError("incompatible")
    expected = seed(template, message)
    validate_generation(cast(Tree, root.to_tree()), value, encode_canonical(expected))
    return expected


def validate_generation(row: Tree, value: Tree, expected: object) -> None:
    if (
        (row["kind"], row["version"]) != (GENERATION_NAME, GENERATION_VERSION)
        or row["conversationId"] != value["conversation"]
        or "owner" in row
        or OwnedJson(row["input"]) != OwnedJson(expected)
    ):
        raise DurableProductError("incompatible")


def retry_edges(storage: Storage, value: Tree) -> dict[int, tuple[int, Tree]]:
    """Validate every generation winner, including progressed descendant rows."""

    expected = encode_canonical(bound_seed(storage, value))
    cid = value["conversation"]
    result: dict[int, tuple[int, Tree]] = {}
    sources: set[int] = set()
    cursor = None
    while True:
        page = storage.scan_documents(
            DocumentQuery(
                {
                    "scope": {"kind": "conversation", "conversationId": cid},
                    "at": "current",
                    "kind": AUDIT.kind,
                }
            ),
            128,
            cursor,
        )
        for record in page.items:
            source, audit = _audit(storage, record.id, cid, value, expected)
            if audit["proposal"]["new_request"] is None:
                continue
            new_id = audit["new_task_id"]
            if source["id"] in sources or new_id in result:
                raise DurableProductError("incompatible")
            sources.add(source["id"])
            result[new_id] = (source["id"], audit)
        cursor = page.next
        if cursor is None:
            break
    root = value["initial_generation"]
    for child in result:
        seen: set[int] = set()
        while child != root:
            if child in seen or child not in result:
                raise DurableProductError("incompatible")
            seen.add(child)
            child = result[child][0]
    return result


def _audit(
    storage: Storage, record_id: int, cid: int, value: Tree, expected: object
) -> tuple[Tree, Tree]:
    from .records import DocumentId, TaskId, TaskRecord

    found = storage.document(DocumentId(record_id))
    if found is None or found.to_tree()["version"] != 1:
        raise DurableProductError("incompatible")
    audit = found.to_tree()["value"]
    if (
        type(audit) is not dict
        or set(audit) != {"version", "proposal", "source", "new_task_id"}
        or type(audit["version"]) is not int
        or audit["version"] != 1
    ):
        raise DurableProductError("incompatible")
    proposal = audit["proposal"]
    source = cast(Tree, TaskRecord(audit["source"]).to_tree())
    new = storage.task(TaskId(audit["new_task_id"]))
    old = storage.task(TaskId(source["id"]))
    if old is None or new is None:
        raise DurableProductError("incompatible")
    _winner_binding(
        source, cast(Tree, old.to_tree()), cast(Tree, new.to_tree()), cid, proposal
    )
    if proposal["new_request"] is not None:
        validate_generation(source, value, expected)
        validate_generation(cast(Tree, new.to_tree()), value, expected)
        if OwnedJson(proposal["new_request"]) != OwnedJson(expected):
            raise DurableProductError("incompatible")
    return source, cast(Tree, audit)


def select_run(
    storage: Storage, value: Tree, selection: DurableRunSelection | None
) -> Tree:
    from .inspection_read import peek_document
    from .records import TaskId

    if value["initial_generation"] is None:
        raise DurableProductError("no_input")
    edges = retry_edges(storage, value)
    if selection is None:
        if edges:
            raise DurableProductError("selection_required")
        task_id = value["initial_generation"]
    else:
        if type(selection) is not DurableRunSelection or selection.task_id not in edges:
            raise DurableProductError("incompatible")
        task_id = selection.task_id
        audit = peek_document(
            storage, AUDIT, value["conversation"], key=selection.resolution_nonce
        )
        if audit is None or OwnedJson(audit["value"]) != OwnedJson(edges[task_id][1]):
            raise DurableProductError("incompatible")
    row = storage.task(TaskId(task_id))
    if row is None:
        raise DurableProductError("incompatible")
    result = cast(Tree, row.to_tree())
    if result["state"]["status"] == "terminal":
        _terminal_leaf(storage, result)
    return result


def _terminal_leaf(storage: Storage, row: Tree) -> None:
    # A terminal fast path never bypasses the existing ownership invariant.
    # This is a refusal guard, not dispatch or general DE15 graph recovery.
    if any(
        child.get("owner") == row["id"] and child["state"]["status"] != "terminal"
        for child in tasks(storage)
    ):
        raise DurableProductError("incompatible")
    if any(
        conversation.get("owner", {}).get("taskId") == row["id"]
        for conversation in conversations(storage)
    ):
        raise DurableProductError("incompatible")
