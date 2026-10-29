"""W1 strict-refusal regressions through actual scheduler/Session admission."""

import asyncio
import copy

import pytest
from definition_publication_live_support import owner
from orphan_refusal_support import (
    accept_neighbor,
    corrupted_graph,
    create_in,
    graph_frame,
    graph_pages,
    observed_pages,
    refused,
    seed_document,
    snapshot_graph,
    terminalize,
)
from task_migration_support import Rig, definition

from pipy_harness.native.durable.storage import Cursor, Page


@pytest.fixture(params=["memory", "sqlite"])
def rig(request, tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))
    value = Rig(request.param, tmp_path)
    try:
        yield value
    finally:
        value.close()


@pytest.mark.parametrize("method", ["scan_tasks", "scan_conversations"])
@pytest.mark.parametrize(
    "case",
    [
        "duplicate-ID",
        "nonascending-ID",
        "wrong-record-class",
        "empty-page-with-cursor",
        "cursor-after-mismatch",
        "cursor-order",
        "repeated-nonadvance-cursor",
        "duplicate-across-pages",
    ],
)
def test_typed_scan_protocol_refuses_without_durable_delta(
    rig, monkeypatch, tmp_path, method, case
):
    """Native-only snapshot corruption; the backend and API still really run."""
    ids = graph_frame(rig)
    scheduler = owner(rig, [])
    graph = snapshot_graph(rig)
    pages = graph_pages(graph)
    values = pages[method].items
    assert len(values) >= 2
    wrong = pages[
        "scan_conversations" if method == "scan_tasks" else "scan_tasks"
    ].items[0]
    last = values[-1].id
    controls = {
        "duplicate-ID": [Page((values[0], values[0], *values[1:]))],
        "nonascending-ID": [Page(tuple(reversed(values)))],
        "wrong-record-class": [Page((wrong,))],
        "empty-page-with-cursor": [Page((), Cursor(0, "ascending"))],
        "cursor-after-mismatch": [Page(values, Cursor(last + 1, "ascending"))],
        "cursor-order": [Page(values, Cursor(last, "descending"))],
        # Nonadvance is jointly invalid with after!=last under strictly increasing
        # IDs. Do not mutate frozen Cursor DTOs to manufacture a sole-conjunct hit.
        "repeated-nonadvance-cursor": [
            Page(values[:1], Cursor(values[0].id, "ascending")),
            Page(values[1:], Cursor(values[0].id, "ascending")),
        ],
        "duplicate-across-pages": [
            Page(values[:1], Cursor(values[0].id, "ascending")),
            Page(values),
        ],
    }

    def respond(name, real, cursor, ordinal):
        if name != method:
            return real
        assert ordinal < len(controls[case]), (
            "Protocol guard failed to stop corrupt continuation"
        )
        return controls[case][ordinal]

    with observed_pages(rig, monkeypatch, respond) as calls:
        refused(rig, scheduler, ids["target"], tmp_path, method + "-" + case)
    assert calls and all(
        call["thread"] == call["actual_ticket_thread"] for call in calls
    )
    # With the corrupt view removed, this exact genuine target is eligible.
    accept_neighbor(rig, scheduler, ids["target"])


@pytest.mark.parametrize(
    "case",
    [
        "shared-task-conversation-ID",
        "missing-containing-conversation",
        "missing-task-owner",
        "cross-conversation-task-owner",
        "missing-conversation-owner",
        "conversation-owner-mismatch",
        "task-self-cycle",
        "two-task-cycle",
        "task-conversation-cycle",
    ],
)
def test_unrelated_typed_graph_corruption_refuses_actual_api(
    rig, monkeypatch, tmp_path, case
):
    ids = graph_frame(rig)
    scheduler = owner(rig, [])
    original = snapshot_graph(rig)
    graph = corrupted_graph(original, ids, case)
    assert next(
        row for row in graph["scan_tasks"] if row["id"] == ids["target"]
    ) == rig.record(ids["target"])
    pages = graph_pages(graph)
    with observed_pages(
        rig, monkeypatch, lambda name, real, cursor, ordinal: pages[name]
    ) as calls:
        refused(rig, scheduler, ids["target"], tmp_path, case)
    assert {call["method"] for call in calls} == {"scan_tasks", "scan_conversations"}
    accept_neighbor(rig, scheduler, ids["target"])


@pytest.mark.parametrize(
    "case", ["target-absent-from-scan", "target-differs-from-committed"]
)
def test_target_scan_identity_is_checked_before_ownership(
    rig, monkeypatch, tmp_path, case
):
    ids = graph_frame(rig)
    scheduler = owner(rig, [])
    graph = snapshot_graph(rig)
    if case == "target-absent-from-scan":
        graph["scan_tasks"] = [
            row for row in graph["scan_tasks"] if row["id"] != ids["target"]
        ]
    else:
        next(row for row in graph["scan_tasks"] if row["id"] == ids["target"])[
            "input"
        ] = {"different": "typed-observation"}
    pages = graph_pages(graph)
    with observed_pages(
        rig, monkeypatch, lambda name, real, cursor, ordinal: pages[name]
    ):
        refused(rig, scheduler, ids["target"], tmp_path, case)
    accept_neighbor(rig, scheduler, ids["target"])


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_valid_absent_id_is_exact_unknown_and_never_writes(rig, asynchronous):
    ids = graph_frame(rig)
    scheduler = owner(rig, [])
    assert rig.session.read_on_line(lambda: rig.storage.task(ids["missing"])) is None
    before = rig.silence()
    with pytest.raises(ValueError) as actual:
        if asynchronous:
            asyncio.run(scheduler.aorphan_missing_definition(ids["missing"]))
        else:
            scheduler.orphan_missing_definition(ids["missing"])
    assert str(actual.value) == "Unknown task"
    rig.assert_silent(before)
    assert rig.session.failure is None
    accept_neighbor(rig, scheduler, ids["neighbor"])


@pytest.mark.parametrize(
    "case",
    [
        "target-owner",
        "owned-containing-conversation",
        "owned-conversation",
        "live-child",
        "typed-background-owned-child",
        "grandchild-behind-terminal-child",
    ],
)
def test_real_leaf_boundaries_and_labelled_impossible_snapshot(
    rig, monkeypatch, tmp_path, case
):
    source = definition("refusal.leaf")
    parent = create_in(rig, source)
    target = parent
    child = None
    if case == "target-owner":
        target = create_in(rig, source, parent=parent)
    elif case == "owned-containing-conversation":
        conversation = rig.session.commit(
            lambda tx: tx.create_conversation({"kind": "task", "taskId": parent})
        ).id
        target = create_in(rig, source, conversation)
    elif case == "owned-conversation":
        rig.session.commit(
            lambda tx: tx.create_conversation({"kind": "task", "taskId": target})
        )
    else:
        child = create_in(rig, source, parent=target)
        if case == "grandchild-behind-terminal-child":
            create_in(rig, source, parent=child)  # Build while owner is LIVE.
            terminalize(rig, child)
    neighbor = create_in(rig, definition("refusal.unrelated.positive"))
    seed_document(rig, target)
    scheduler = owner(rig, [])
    if case == "typed-background-owned-child":
        # Real Tx rejects a background owned child. Only this ABI-valid returned
        # observation is changed; no historical/real producer claim is made.
        graph = snapshot_graph(rig)
        next(row for row in graph["scan_tasks"] if row["id"] == child)["background"] = (
            True
        )
        pages = graph_pages(graph)
        with observed_pages(
            rig, monkeypatch, lambda name, real, cursor, ordinal: pages[name]
        ) as calls:
            refused(rig, scheduler, target, tmp_path, case)
            assert calls
    else:
        refused(rig, scheduler, target, tmp_path, case)
    accept_neighbor(rig, scheduler, neighbor)


@pytest.mark.parametrize("status", ["running", "waiting", "sleeping", "completing"])
def test_nonpending_target_states_seeded_after_open_refuse(rig, tmp_path, status):
    ids = graph_frame(rig)
    scheduler = owner(rig, [])

    def change(row):
        checkpoint = copy.deepcopy(row["state"]["checkpoint"])
        states = {
            "running": {"status": "running", "checkpoint": checkpoint},
            "waiting": {
                "status": "waiting",
                "checkpoint": checkpoint,
                "on": [ids["spare"]],
                "policy": "allSettled",
            },
            "sleeping": {"status": "sleeping", "checkpoint": checkpoint, "until": 1e12},
            "completing": {
                "status": "completing",
                "outcome": {"status": "completed", "result": None},
            },
        }
        row["state"] = states[status]
        if status == "completing":
            row.pop("memos", None)

    # Typed kernel state, not an actual active invocation/crash producer. Open
    # precedes the seed so recovery cannot normalize the discriminator away.
    rig.change(ids["target"], change)
    assert rig.record(ids["target"])["state"]["status"] == status
    refused(rig, scheduler, ids["target"], tmp_path, "status-" + status)
    accept_neighbor(rig, scheduler, ids["neighbor"])


@pytest.mark.parametrize(
    "field,value",
    [("abortRequested", True), ("abortReason", "restart"), ("abandonOnRestart", True)],
)
def test_each_live_mark_refuses_without_rewrite(rig, tmp_path, field, value):
    ids = graph_frame(rig)
    scheduler = owner(rig, [])
    rig.change(ids["target"], lambda row: row.__setitem__(field, value))
    assert rig.record(ids["target"])[field] == value
    refused(rig, scheduler, ids["target"], tmp_path, field)
    accept_neighbor(rig, scheduler, ids["neighbor"])


@pytest.mark.parametrize(
    "name", ["pi.generation", "pi.tool", "pi.compaction", "pi.future-private"]
)
def test_reserved_terminal_kind_precedes_terminal_replay(rig, tmp_path, name):
    # Genuine Task/Tx-created reserved KIND control, not a Product model/tool
    # frame, saved receipt, historical producer or public abort claim.
    target = create_in(rig, definition(name))
    neighbor = create_in(rig, definition("piX.generic-terminal"))
    terminalize(rig, target)
    terminalize(rig, neighbor)
    scheduler = owner(rig, [])
    refused(
        rig,
        scheduler,
        target,
        tmp_path,
        name,
        message="Built-in tasks cannot be orphaned here",
    )
    before = rig.silence()
    assert scheduler.orphan_missing_definition(neighbor) == "terminal"
    rig.assert_silent(before)


@pytest.mark.parametrize("version", [-1.5, 1, 3])
def test_present_definition_never_fits_or_migrates(rig, tmp_path, version):
    ids = graph_frame(rig)
    calls = []

    def migrate(*args):
        calls.append(args)
        raise AssertionError("Definition-present refusal must precede migration")

    current = definition(ids["source"].name, version=version, migrate=migrate)
    scheduler = owner(rig, [current])
    refused(
        rig,
        scheduler,
        ids["target"],
        tmp_path,
        "definition-present",
        message="Task definition is present",
    )
    assert calls == []
    accept_neighbor(rig, scheduler, ids["neighbor"])
