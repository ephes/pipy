"""Actual owned-round ordering, rich receipt preservation and fail-closed boundaries."""

import threading
from dataclasses import replace

import pytest
from generation_support import Wire, admit, definition, seed
from test_tool_execution import KeyedTool

from pipy_harness.native.agent.content import (
    NO_STRUCTURED_CONTENT,
    ImageContent,
    TextContent,
)
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.native.durable.generation import ROUND_DOC, USAGE_DOC
from pipy_harness.native.durable.generation_tasks import TOOL_TASK
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.scheduler import TaskRuntime, TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import EntryQuery
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)
from pipy_harness.native.durable.tool_history import ORDER_DOC
from pipy_harness.native.durable.tool_values import (
    blocked_record,
    blocked_transition,
    read_native_tool_output,
)
from pipy_harness.native.tools.base import ToolExecutionResult


def isolated(monkeypatch, root):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(root / name.lower()))


def run_idle(session, definitions, scheduler=None):
    scheduler = scheduler or TaskScheduler(session, definitions)
    idle = threading.Event()
    original = scheduler._reserve

    async def reserve():
        result = await original()
        if result is None:
            idle.set()
        return result

    scheduler._reserve = reserve
    scheduler.open()
    scheduler.resume()
    assert idle.wait(10), (scheduler.inspect(), session.failure)
    return scheduler


class MultiRound(Wire):
    def __init__(self, rounds):
        super().__init__()
        self.rounds = rounds

    def post_json(self, url, **kwargs):
        self.tool_calls = self.rounds[len(self.calls)]
        return super().post_json(url, **kwargs)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.parametrize("empty", [False, True])
def test_real_closed_rounds_preserve_order_and_distinct_prepare_identities(
    tmp_path, monkeypatch, backend, empty
):
    isolated(monkeypatch, tmp_path)
    session = Session(
        MemoryStorage()
        if backend == "memory"
        else open_sqlite_storage(str(tmp_path / "session.sqlite"), mode="create")
    )
    calls = []
    port = KeyedTool("keyed", calls)
    tool = define_tool_task(
        {"keyed": ToolRegistration(port, "fixture", "1", "safe")},
        workspace_root=tmp_path,
    )
    rounds = (
        (("missing",), ("absent",), ())
        if empty
        else (("keyed", "missing"), ("missing", "keyed"), ())
    )
    wire = MultiRound(rounds)
    generation = definition(tmp_path, wire)
    snapshot = snapshot_provider_request(
        replace(
            seed(tmp_path).request, available_tools=() if empty else (port.definition,)
        )
    )
    conversation, _, parent = admit(session, generation, snapshot)
    try:
        scheduler = run_idle(session, [generation, tool])
        assert (
            next(r.to_tree() for r in scheduler.inspect() if r.id == parent)["state"][
                "outcome"
            ]["status"]
            == "completed"
        )
        assert len(wire.calls) == 3
        entries = sorted(
            session.commit(
                lambda tx: tx.scan_entries(
                    EntryQuery({"conversationId": conversation}), 100
                )
            ).items,
            key=lambda e: e.id,
        )
        assistants = [
            e.to_tree() for e in entries if e.to_tree()["kind"] == "pi.assistant"
        ]
        assert [
            decode_canonical(e["data"]["request"]).request.provider_turn_index
            for e in assistants
        ] == [0, 1, 2]
        assert (
            len({e["data"]["receipt"]["identity"]["request"] for e in assistants}) == 3
        )
        assert (
            len({e["data"]["receipt"]["identity"]["attempt"] for e in assistants}) == 3
        )
        saved = decode_canonical(assistants[-1]["data"]["request"]).request.messages
        # Canonical DTOs as well as translated provider wire carry both ordered rounds.
        assert [m.tool_name for m in saved if hasattr(m, "tool_name")] == [
            name for names in rounds for name in names
        ]
        index = session.snapshot(ORDER_DOC, conversation_id=conversation).value.thaw()
        assert set(index["rounds"]) == {str(e["id"]) for e in assistants[:-1]}
        ledger = session.snapshot(USAGE_DOC, conversation_id=conversation).value.thaw()
        assert len(ledger["settlements"]) == 3
        assert ledger["models"]["openai/gpt-test"]["input"] == 48
        assert len(calls) == port.prepared == (0 if empty else 2)
        assert len({key for _, _, key in calls}) == len(calls)
    finally:
        session.close(timeout=10)


class RichTool(KeyedTool):
    def __init__(self, structured, *, terminate=False):
        super().__init__("rich", [])
        self.structured, self.terminate = structured, terminate

    def invoke(self, request, context):
        self.calls.append(request)
        return ToolExecutionResult(
            request.tool_request_id,
            "before\nafter",
            is_error=True,
            provider_correlation_id=request.provider_correlation_id,
            details={"nested": {"null": None, "empty": [], "false": False}},
            structured_content=self.structured,
            blocks=(
                TextContent("before"),
                ImageContent("aW1hZ2U=", "image/png"),
                TextContent("after"),
            ),
            terminate=self.terminate,
        )


@pytest.mark.parametrize(
    "structured", [NO_STRUCTURED_CONTENT, None, {"x": [None, False, 0]}]
)
def test_actual_rich_receipt_preserves_missing_null_and_equality_excluded_fields(
    tmp_path, monkeypatch, structured
):
    isolated(monkeypatch, tmp_path)
    session = Session(
        open_sqlite_storage(str(tmp_path / "session.sqlite"), mode="create")
    )
    port = RichTool(structured)
    wire = MultiRound((("rich",), ()))
    generation = definition(tmp_path, wire)
    tool = define_tool_task(
        {"rich": ToolRegistration(port, "fixture.rich", "1")}, workspace_root=tmp_path
    )
    snapshot = snapshot_provider_request(
        replace(seed(tmp_path).request, available_tools=(port.definition,))
    )
    cid, _, parent = admit(session, generation, snapshot)
    try:
        scheduler = run_idle(session, [generation, tool])
        assert (
            next(r.to_tree() for r in scheduler.inspect() if r.id == parent)["state"][
                "status"
            ]
            == "terminal"
        )
        entries = session.commit(
            lambda tx: tx.scan_entries(EntryQuery({"conversationId": cid}), 100)
        ).items
        entry = next(
            e.to_tree() for e in entries if e.to_tree()["kind"] == "pi.tool-result"
        )
        _, outcome = read_native_tool_output(entry["data"]["nativeToolOutput"])
        model = decode_canonical(entry["model"][0])
        assert (
            outcome.result.details
            == model.details
            == {"nested": {"null": None, "empty": [], "false": False}}
        )
        if structured is NO_STRUCTURED_CONTENT:
            assert (
                model.structured_content
                is outcome.result.structured_content
                is NO_STRUCTURED_CONTENT
            )
        else:
            assert (
                model.structured_content
                == outcome.result.structured_content
                == structured
            )
        assert (
            model.blocks
            == outcome.result.blocks
            == (
                TextContent("before"),
                ImageContent("aW1hZ2U=", "image/png"),
                TextContent("after"),
            )
        )
        assert outcome.execution_seconds is not None and outcome.execution_seconds >= 0
        assert outcome.result.is_error and not outcome.malformed_arguments
        assert model.tool_request_id == port.calls[0].tool_request_id
        assert (
            model.provider_correlation_id
            == port.calls[0].provider_correlation_id
            == "call:0"
        )
        assistant = next(
            e.to_tree()
            for e in entries
            if e.to_tree()["kind"] == "pi.assistant"
            and decode_canonical(e.to_tree()["model"][0]).tool_calls
        )
        assert (
            decode_canonical(assistant["model"][0]).tool_calls[0].arguments_json.value
            == ' { "x":  1 } '
        )
        assert len(port.calls) == port.prepared == 1
    finally:
        session.close(timeout=10)


@pytest.mark.parametrize("names", [("rich", "later"), ("rich",), ("later", "rich")])
def test_terminate_preserved_and_holds_later_call_and_next_provider(
    tmp_path, monkeypatch, names
):
    isolated(monkeypatch, tmp_path)
    session = Session(MemoryStorage())
    port, later = RichTool(None, terminate=True), KeyedTool("later", [])
    wire = Wire(names)
    generation = definition(tmp_path, wire)
    tool = define_tool_task(
        {
            p.definition.name: ToolRegistration(p, "fixture." + p.definition.name, "1")
            for p in (port, later)
        },
        workspace_root=tmp_path,
    )
    snapshot = snapshot_provider_request(
        replace(
            seed(tmp_path).request, available_tools=(port.definition, later.definition)
        )
    )
    cid, _, parent = admit(session, generation, snapshot)
    try:
        scheduler = run_idle(session, [generation, tool])
        rows = [r.to_tree() for r in scheduler.inspect()]
        owner = next(r for r in rows if r["id"] == parent)
        assert len(port.calls) == 1 and len(wire.calls) == 1
        assert len(later.calls) == later.prepared == int(names[0] == "later")
        entries = session.commit(
            lambda tx: tx.scan_entries(EntryQuery({"conversationId": cid}), 100)
        ).items
        rich = next(
            e.to_tree()
            for e in entries
            if e.to_tree()["kind"] == "pi.tool-result"
            and decode_canonical(e.to_tree()["model"][0]).tool_name == "rich"
        )
        assert read_native_tool_output(rich["data"]["nativeToolOutput"])[
            1
        ].result.terminate
        if names[-1] == "rich":
            assert owner["state"]["status"] == "pending"
            assert owner["state"]["checkpoint"]["phase"] == "blocked"
            assert blocked_record(owner) and blocked_transition(owner, owner["state"])
            unblocked = {
                **owner["state"],
                "checkpoint": {**owner["state"]["checkpoint"], "phase": "tools"},
            }
            assert not blocked_transition(owner, unblocked)
        else:
            assert owner["state"]["status"] == "waiting" and not blocked_record(owner)
            assert next(
                r
                for r in rows
                if r["kind"] == "pi.tool" and r["input"]["callId"] == "call:1"
            )["state"] == {"status": "pending", "checkpoint": {"phase": "call"}}
    finally:
        session.close(timeout=10)


@pytest.mark.parametrize("cut", ["prepared", "acknowledged_intent"])
def test_abort_winner_before_effect_holds_later_calls(tmp_path, monkeypatch, cut):
    isolated(monkeypatch, tmp_path)
    session = Session(MemoryStorage())
    calls = []
    first, later = KeyedTool("first", calls), KeyedTool("later", calls)
    wire = Wire(("first", "later"))
    generation = definition(tmp_path, wire)
    tool = define_tool_task(
        {
            p.definition.name: ToolRegistration(p, "fixture." + p.definition.name, "1")
            for p in (first, later)
        },
        workspace_root=tmp_path,
    )
    snapshot = snapshot_provider_request(
        replace(
            seed(tmp_path).request, available_tools=(first.definition, later.definition)
        )
    )
    _, _, parent = admit(session, generation, snapshot)
    won = threading.Event()
    original = TaskRuntime.commit

    async def commit(runtime, change, context=None):
        result = await original(runtime, change, context)
        if cut == "acknowledged_intent" and not won.is_set():

            async def mark(tx):
                row = (await tx.task(runtime.task_id)).to_tree()
                if (
                    row["kind"] == "pi.tool"
                    and row["state"].get("checkpoint", {}).get("phase") == "execute"
                ):
                    tx.set_task({**row, "abortRequested": True})
                    won.set()

            await session.acommit(mark)
        return result

    monkeypatch.setattr(TaskRuntime, "commit", commit)
    if cut == "prepared":
        prepare = first.prepare_arguments

        def aborting(raw):
            value = prepare(raw)

            def mark(tx):
                round_value = tx.doc(ROUND_DOC, task_id=parent).snapshot().thaw()
                row = tx.task(round_value["tools"][0]).to_tree()
                tx.set_task({**row, "abortRequested": True})

            session.commit(mark)
            won.set()
            return value

        first.prepare_arguments = aborting
        # Captured callable identity is frozen at definition; admit new token now.
        tool = define_tool_task(
            {
                p.definition.name: ToolRegistration(
                    p, "fixture." + p.definition.name, "1"
                )
                for p in (first, later)
            },
            workspace_root=tmp_path,
        )
    try:
        scheduler = run_idle(session, [generation, tool])
        assert won.is_set() and not calls and len(wire.calls) == 1
        rows = [r.to_tree() for r in scheduler.inspect()]
        assert (
            next(r for r in rows if r["id"] == parent)["state"]["status"] == "waiting"
        )
        assert next(
            r
            for r in rows
            if r["kind"] == "pi.tool" and r["input"]["callId"] == "call:1"
        )["state"]["checkpoint"] == {"phase": "call"}
    finally:
        session.close(timeout=10)


async def change_leaf(tx, task_id, drift, independent, other):
    row = (await tx.task(task_id)).to_tree()
    if drift == "owner":
        row.pop("owner")
    elif drift == "conversation":
        row["conversationId"] = other
    elif drift == "call":
        row["input"]["callId"] = "foreign-call"
    elif drift == "assistant":
        row["input"]["assistant"] += 100
    elif drift == "descendant":
        await tx.create_task(
            independent,
            None,
            ownership={"kind": "task", "taskId": task_id},
            conversation_id=row["conversationId"],
        )
    tx.set_task(row)


def guard_drift(session, scheduler, monkeypatch, gate, drift, independent, other):
    changed = threading.Event()

    async def change(tx, task_id):
        await change_leaf(tx, task_id, drift, independent, other)
        changed.set()

    if gate == "step":
        original = scheduler._reserve

        async def reserve():
            invocation = await original()
            if invocation is not None and not changed.is_set():
                current = next(
                    r.to_tree()
                    for r in scheduler.inspect()
                    if r.id == invocation.task_id
                )
                if current["kind"] == "pi.tool":
                    await session.acommit(lambda tx: change(tx, invocation.task_id))
            return invocation

        scheduler._reserve = reserve
    else:
        original_commit = TaskRuntime.commit

        async def commit(runtime, callback, context=None):
            result = await original_commit(runtime, callback, context)
            if not changed.is_set():

                async def after(tx):
                    row = (await tx.task(runtime.task_id)).to_tree()
                    if (
                        row["kind"] == "pi.tool"
                        and row["state"].get("checkpoint", {}).get("phase") == "execute"
                    ):
                        await change(tx, runtime.task_id)

                await session.acommit(after)
            return result

        monkeypatch.setattr(TaskRuntime, "commit", commit)
    return changed


@pytest.mark.parametrize("gate", ["step", "runtime"])
@pytest.mark.parametrize("drift", ["owner", "call", "assistant", "descendant"])
def test_actual_dispatch_and_runtime_refuse_changed_direct_leaf_without_poisoning_other_conversation(
    tmp_path, monkeypatch, gate, drift
):
    from pipy_harness.native.durable.tasks import define_task

    isolated(monkeypatch, tmp_path)
    session = Session(MemoryStorage())
    calls = []
    port = KeyedTool("keyed", calls)
    wire = Wire(("keyed", "keyed"))
    generation = definition(tmp_path, wire)
    tool = define_tool_task(
        {"keyed": ToolRegistration(port, "fixture.keyed", "1")}, workspace_root=tmp_path
    )
    snapshot = snapshot_provider_request(
        replace(seed(tmp_path).request, available_tools=(port.definition,))
    )
    _, _, parent = admit(session, generation, snapshot)
    healthy_done = threading.Event()

    async def healthy(record, runtime, context):
        await runtime.commit(
            lambda tx, current: {
                "status": "terminal",
                "outcome": {"status": "completed", "result": "healthy"},
            }
        )
        healthy_done.set()

    async def abort(*args):
        raise AssertionError("must not dispatch abort")

    independent = define_task(
        "independent", 1, lambda _: {"phase": "start"}, {"start": healthy}, abort
    )
    other = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    healthy_id = session.commit(
        lambda tx: tx.create_task(
            independent, None, ownership={"kind": "conversation"}, conversation_id=other
        )
    )
    scheduler = TaskScheduler(session, [generation, tool, independent])
    changed = guard_drift(
        session, scheduler, monkeypatch, gate, drift, independent, other
    )
    try:
        run_idle(session, [], scheduler=scheduler)
        assert changed.is_set() and healthy_done.is_set()
        assert not calls and len(wire.calls) == 1
        rows = [r.to_tree() for r in scheduler.inspect()]
        assert (
            next(r for r in rows if r["id"] == healthy_id)["state"]["outcome"]["result"]
            == "healthy"
        )
        assert (
            next(r for r in rows if r["id"] == parent)["state"]["status"] == "waiting"
        )
        assert next(
            r
            for r in rows
            if r["kind"] == "pi.tool" and r["input"]["callId"] == "call:1"
        )["state"]["checkpoint"] == {"phase": "call"}
    finally:
        session.close(timeout=10)


def test_malformed_persisted_foreign_conversation_is_not_executable(
    tmp_path, monkeypatch
):
    import sqlite3

    from pipy_harness.native.durable import sqlite

    isolated(monkeypatch, tmp_path)
    path = str(tmp_path / "session.sqlite")
    calls = []
    port = KeyedTool("keyed", calls)
    wire = Wire(("keyed",))
    generation = definition(tmp_path, wire)
    tool = define_tool_task(
        {"keyed": ToolRegistration(port, "fixture.keyed", "1")}, workspace_root=tmp_path
    )
    session = Session(open_sqlite_storage(path, mode="create"))
    snapshot = snapshot_provider_request(
        replace(seed(tmp_path).request, available_tools=(port.definition,))
    )
    _, _, parent = admit(session, generation, snapshot)
    other = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    scheduler = run_idle(session, [generation, TOOL_TASK])
    child = next(
        r.to_tree() for r in scheduler.inspect() if r.to_tree()["kind"] == "pi.tool"
    )
    assert (
        next(r.to_tree() for r in scheduler.inspect() if r.id == parent)["state"][
            "status"
        ]
        == "waiting"
    )
    session.close(timeout=10)
    # Corruption fixture outside any owner, not a legal Task conversation change.
    with sqlite3.connect(path) as raw:
        child["conversationId"] = other
        raw.execute(
            "UPDATE tasks SET record=?,conversation_id=? WHERE id=?",
            (sqlite._encode(child), other, child["id"]),
        )
    reopened = Session(open_sqlite_storage(path, mode="open"))
    try:
        replay = run_idle(reopened, [generation, tool])
        assert not calls and len(wire.calls) == 1
        assert (
            next(r.to_tree() for r in replay.inspect() if r.id == child["id"])["state"]
            == child["state"]
        )
    finally:
        reopened.close(timeout=10)


@pytest.mark.parametrize(
    "profile", ["hidden", "prepare_loadout", "reaches_tools", "sequential"]
)
def test_unsupported_current_registration_blocks_before_prepare_and_effect(
    tmp_path, monkeypatch, profile
):
    isolated(monkeypatch, tmp_path)
    port = KeyedTool("keyed", [])
    offered = port.definition

    def forbidden(*args):
        raise AssertionError("unsupported callback ran")

    field = (
        {"exposure": "hidden"}
        if profile == "hidden"
        else {"execution_mode": "sequential"}
        if profile == "sequential"
        else {profile: forbidden}
    )
    port.definition = replace(port.definition, **field)
    tool = define_tool_task(
        {"keyed": ToolRegistration(port, "fixture.keyed", "1", "safe")},
        workspace_root=tmp_path,
    )
    wire = Wire(("keyed",))
    generation = definition(tmp_path, wire)
    session = Session(MemoryStorage())
    snapshot = snapshot_provider_request(
        replace(seed(tmp_path).request, available_tools=(offered,))
    )
    _, _, parent = admit(session, generation, snapshot)
    try:
        scheduler = run_idle(session, [generation, tool])
        rows = [row.to_tree() for row in scheduler.inspect()]
        child = next(row for row in rows if row["kind"] == "pi.tool")
        assert (
            child["state"]["status"] == "pending"
            and child["state"]["checkpoint"]["phase"] == "blocked"
        )
        assert not port.calls and port.prepared == 0 and len(wire.calls) == 1
        assert (
            next(row for row in rows if row["id"] == parent)["state"]["status"]
            == "waiting"
        )
    finally:
        session.close(timeout=10)


def test_malformed_prepared_args_commit_actual_flag_without_invoking(
    tmp_path, monkeypatch
):
    isolated(monkeypatch, tmp_path)
    port = KeyedTool("keyed", [])

    def malformed(raw):
        port.prepared += 1
        return {"x": True}

    port.prepare_arguments = malformed
    tool = define_tool_task(
        {"keyed": ToolRegistration(port, "fixture.keyed", "1")}, workspace_root=tmp_path
    )
    wire = MultiRound((("keyed",), ()))
    generation = definition(tmp_path, wire)
    session = Session(MemoryStorage())
    snapshot = snapshot_provider_request(
        replace(seed(tmp_path).request, available_tools=(port.definition,))
    )
    cid, _, _ = admit(session, generation, snapshot)
    try:
        run_idle(session, [generation, tool])
        entries = session.commit(
            lambda tx: tx.scan_entries(EntryQuery({"conversationId": cid}), 100)
        ).items
        entry = next(
            e.to_tree() for e in entries if e.to_tree()["kind"] == "pi.tool-result"
        )
        _, outcome = read_native_tool_output(entry["data"]["nativeToolOutput"])
        assert outcome.malformed_arguments and outcome.result.is_error
        assert outcome.execution_seconds is None and not outcome.cancelled
        assert port.prepared == 1 and not port.calls and len(wire.calls) == 2
    finally:
        session.close(timeout=10)
