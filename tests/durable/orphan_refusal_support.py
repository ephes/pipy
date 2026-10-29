"""Native-only observation controls over genuine Session/Tx stores; no history claim."""

import copy
import json
import sys
import threading
from contextlib import contextmanager

import pytest
from task_migration_support import definition

from pipy_harness.native.durable import ownership_query
from pipy_harness.native.durable.documents import define_doc
from pipy_harness.native.durable.records import ConversationRecord, TaskRecord
from pipy_harness.native.durable.scheduler import UnsupportedTaskExecution
from pipy_harness.native.durable.storage import ConversationQuery, Page, TaskQuery

REFUSAL = "Task requires a validated unmarked pending root leaf"


def seed_document(rig, task_id):
    token = define_doc("orphan.refusal.evidence", 1, "task", lambda: {"n": 0})
    rig.session.commit(lambda tx: tx.doc(token, task_id=task_id))

    def update(tx):
        tx.doc(token, task_id=task_id)["n"] = 1

    rig.session.commit(update)


def create_in(rig, source, conversation=None, parent=None):
    return rig.session.commit(
        lambda tx: tx.create_task(
            source,
            {"fixture": "orphan-refusal"},
            ownership={"kind": "conversation"}
            if parent is None
            else {"kind": "task", "taskId": parent},
            conversation_id=rig.conversation if conversation is None else conversation,
        )
    )


def terminalize(rig, task_id):
    def edit(row):
        row.pop("memos", None)
        row.pop("restarts", None)
        row["state"] = {
            "status": "terminal",
            "outcome": {"status": "completed", "result": "genuine-Tx-terminal"},
        }

    rig.change(task_id, edit)


def graph_frame(rig):
    source = definition("refusal.target")
    target = create_in(rig, source)
    spare = create_in(rig, definition("refusal.spare"))
    other = create_in(rig, definition("refusal.other"))
    conversation = rig.session.commit(
        lambda tx: tx.create_conversation({"kind": "ownerless"})
    ).id
    foreign = create_in(rig, definition("refusal.foreign"), conversation)
    neighbor = create_in(rig, definition("refusal.neighbor"))
    seed_document(rig, target)
    seed_document(rig, neighbor)
    return {
        "source": source,
        "target": target,
        "spare": spare,
        "other": other,
        "foreign": foreign,
        "foreign_conversation": conversation,
        "neighbor": neighbor,
        "missing": 999_999_999,
    }


def snapshot_graph(rig):
    def capture():
        def all_rows(method, query):
            values, cursor = [], None
            while True:
                page = method(query, 128, cursor)
                values.extend(record.to_tree() for record in page.items)
                if page.next is None:
                    return values
                cursor = page.next

        return {
            "scan_tasks": all_rows(rig.storage.scan_tasks, TaskQuery({})),
            "scan_conversations": all_rows(
                rig.storage.scan_conversations, ConversationQuery({})
            ),
        }

    return rig.session.read_on_line(capture)


def graph_pages(graph):
    return {
        "scan_tasks": Page(
            tuple(
                TaskRecord(row)
                for row in sorted(graph["scan_tasks"], key=lambda row: row["id"])
            )
        ),
        "scan_conversations": Page(
            tuple(
                ConversationRecord(row)
                for row in sorted(
                    graph["scan_conversations"], key=lambda row: row["id"]
                )
            )
        ),
    }


@contextmanager
def observed_pages(rig, monkeypatch, responder):
    """Forward real reads, then supply explicitly labelled typed protocol data."""
    inner = rig.storage.inner
    calls = []
    with monkeypatch.context() as patch:

        def install(name):
            original = getattr(type(inner), name)

            def observed(actual, query, limit, cursor=None):
                page = original(actual, query, limit, cursor)
                if (
                    actual is inner
                    and query.to_tree() == {"order": "ascending"}
                    and limit == 128
                ):
                    holder = rig.session._owner._line_holder()
                    assert holder is not None and holder.thread == threading.get_ident()
                    ordinal = sum(call["method"] == name for call in calls)
                    calls.append(
                        {
                            "method": name,
                            "ordinal": ordinal,
                            "thread": threading.get_ident(),
                            "actual_ticket_thread": holder.thread,
                            "actual_backend_ids": [row.id for row in page.items],
                        }
                    )
                    return responder(name, page, cursor, ordinal)
                return page

            patch.setattr(type(inner), name, observed)

        install("scan_tasks")
        install("scan_conversations")
        yield calls


@contextmanager
def refusal_trace():
    """Observe the actual unmodified refusal function; never stub its behavior."""
    rows = []
    previous = sys.getprofile()
    code = ownership_query._refuse.__code__

    def profile(frame, event, arg):
        if event == "call" and frame.f_code is code:
            caller = frame.f_back
            rows.append(
                {
                    "function": caller.f_code.co_name,
                    "line": caller.f_lineno,
                    "file": caller.f_code.co_filename,
                }
            )
        if previous is not None:
            previous(frame, event, arg)

    sys.setprofile(profile)
    try:
        yield rows
    finally:
        sys.setprofile(previous)


def refused(rig, scheduler, target, output, case, *, message=REFUSAL):
    before = rig.silence()
    with refusal_trace() as trace:
        with pytest.raises(UnsupportedTaskExecution) as actual:
            scheduler.orphan_missing_definition(target)
    assert str(actual.value) == message
    rig.assert_silent(before)
    assert rig.session.failure is None
    (output / ("orphan-refusal-" + case + ".json")).write_text(
        json.dumps(
            {
                "scope": "Actual API/refusal observation, not mutation or branch completeness claim",
                "case": case,
                "exception": type(actual.value).__name__,
                "message": str(actual.value),
                "actual_refusal_callers": trace,
                "whole_durable_no_write": True,
                "oracle": "SQLite all9tables incl sequence/revisions or Memory committed state identity plus commits/mints",
            },
            indent=2,
        )
        + "\n"
    )


def accept_neighbor(rig, scheduler, target):
    before = rig.silence()
    assert scheduler.orphan_missing_definition(target) == "orphaned"
    assert rig.record(target)["state"]["outcome"] == {
        "status": "orphaned",
        "reason": "missing_task",
    }
    # This actual write must be visible to the same no-write oracle.
    with pytest.raises(AssertionError):
        rig.assert_silent(before)


def corrupted_graph(original, ids, case):
    """All replacements are schema-valid owned DTOs, never raw storage writes."""
    graph = copy.deepcopy(original)
    tasks = {row["id"]: row for row in graph["scan_tasks"]}
    conversations = {row["id"]: row for row in graph["scan_conversations"]}
    spare, other, foreign = (tasks[ids[key]] for key in ("spare", "other", "foreign"))
    if case == "shared-task-conversation-ID":
        graph["scan_conversations"].append({"id": spare["id"]})
    elif case == "missing-containing-conversation":
        spare["conversationId"] = ids["missing"]
    elif case == "missing-task-owner":
        spare["owner"] = ids["missing"]
    elif case == "cross-conversation-task-owner":
        spare["owner"] = foreign["id"]
    elif case == "missing-conversation-owner":
        conversations[ids["foreign_conversation"]]["owner"] = {
            "taskId": ids["missing"],
            "conversationId": spare["conversationId"],
        }
    elif case == "conversation-owner-mismatch":
        conversations[ids["foreign_conversation"]]["owner"] = {
            "taskId": spare["id"],
            "conversationId": ids["foreign_conversation"],
        }
    elif case == "task-self-cycle":
        spare["owner"] = spare["id"]
    elif case == "two-task-cycle":
        spare["owner"], other["owner"] = other["id"], spare["id"]
    else:
        assert case == "task-conversation-cycle"
        conversations[ids["foreign_conversation"]]["owner"] = {
            "taskId": foreign["id"],
            "conversationId": foreign["conversationId"],
        }
    return graph
