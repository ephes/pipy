"""Cold/large/candidate/lifetime integration of genuine ordinary-live queries."""

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from ordinary_owned_live_support import (
    DONE,
    INVALID,
    QueryWindow,
    ReadAudit,
    Rollback,
    ScanHold,
    assert_rollback,
    child,
    complete_task_pages,
    done,
    page_evidence,
    published,
    query,
    reopen,
)
from orphan_missing_definition_support import (
    GraphScans,
    populate_large_graph,
    unrelated_history,
)
from restart_policy_live_support import Tickets, held_line
from task_migration_support import Rig, definition, sqlite_rows

from pipy_harness.native.durable import ownership_query, sqlite
from pipy_harness.native.durable.documents import define_doc
from pipy_harness.native.durable.ordinary_ownership import OwnedLiveQuery
from pipy_harness.native.durable.ownership import StorageOwnershipError
from pipy_harness.native.durable.scheduler import UnsupportedTaskExecution
from pipy_harness.native.durable.session import CancelLatch
from pipy_harness.native.durable.session_core import CommitCancelled
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.tx import TransactionSettled


@pytest.fixture(params=["memory", "sqlite"])
def rig(request, tmp_path, monkeypatch):
    for key in (
        "HOME",
        "PIPY_AUTH_DIR",
        "PIPY_CONFIG_HOME",
        "XDG_STATE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "TMPDIR",
    ):
        path = tmp_path / key
        path.mkdir()
        monkeypatch.setenv(key, str(path))
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))
    monkeypatch.setenv("PIPY_THEME", "pi")
    value = Rig(request.param, tmp_path)
    try:
        yield value
    finally:
        value.storage.before_commit = None
        value.storage.after_commit = None
        value.close()


@pytest.fixture
def evidence(request, tmp_path):
    events = []
    yield events
    (tmp_path / "ordinary-live-witness.json").write_text(
        json.dumps({"nodeid": request.node.nodeid, "events": events}, indent=2) + "\n"
    )


def test_real_deep_paged_terminal_path_background_cut_and_cold_reopen(
    rig, monkeypatch, evidence
):
    root = rig.create(definition())
    depth = sys.getrecursionlimit() + 5

    def build(tx):
        chain, owner = [], root
        for _ in range(depth):
            owner = child(tx, owner)
            chain.append(owner)
        conversation = tx.create_conversation({"kind": "task", "taskId": owner})
        leaf = tx.create_task(
            definition(),
            None,
            ownership={"kind": "conversation"},
            conversation_id=conversation.id,
        )
        background = child(tx, root)
        background_leaf = child(tx, background)
        return chain, leaf, background, background_leaf

    chain, leaf, background, background_leaf = rig.session.commit(build)

    def end_intermediates(tx):
        rows = [tx.task(task_id).to_tree() for task_id in chain]
        bg = tx.task(background).to_tree()
        for row in rows:
            tx.set_task({**row, "state": DONE})
        tx.set_task({**bg, "background": True})

    rig.session.commit(end_intermediates)
    scans = GraphScans(rig, monkeypatch)
    assert query(rig, root) is True
    assert query(rig, background) is True
    assert query(rig, background_leaf) is False
    complete_task_pages(scans)
    if rig.backend == "sqlite":
        reopen(rig)
        assert query(rig, root) is True
    done(rig, leaf)
    assert query(rig, root) is False
    if rig.backend == "sqlite":
        reopen(rig)
        assert query(rig, root) is False
    sibling = rig.session.commit(lambda tx: child(tx, root))
    assert query(rig, root) is True and query(rig, sibling) is False
    evidence.append(
        {
            "depth": depth,
            "recursion_limit": sys.getrecursionlimit(),
            "terminal_intermediates": len(chain),
            "answers": [True, False, True],
            "cold_reopen": rig.backend == "sqlite",
            "paged_tasks": True,
        }
    )


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
@pytest.mark.parametrize("corruption", ["dangling", "self", "cycle", "cross"])
def test_genuine_late_committed_bad_graph_refuses_early_true_after_cold_open(
    rig, monkeypatch, corruption, evidence
):
    root = rig.create(definition())
    rig.session.commit(lambda tx: child(tx, root))
    conversations, tasks = populate_large_graph(rig, definition(), 130)
    assert query(rig, root) is True
    bad, other = tasks[-1], tasks[-2]
    if corruption == "cycle":
        # Same-conversation owner cycle, genuinely committed by set_task.
        other = rig.session.commit(lambda tx: child(tx, bad))
    owner = (
        900000 if corruption == "dangling" else bad if corruption == "self" else other
    )
    rig.change(bad, lambda row: row.update(owner=owner))
    reopen(rig)
    scans = GraphScans(rig, monkeypatch)
    before = rig.silence()
    with pytest.raises(ValueError, match=INVALID):
        rig.session.commit(lambda tx: OwnedLiveQuery.capture(tx).has(root))
    rig.assert_silent(before)
    scans.assert_complete_multiple_pages()
    assert any(page[1] is not None and bad in page[3] for page in scans.pages)
    evidence.append(
        {
            "native_real_committed_corruption": corruption,
            "late_task": bad,
            "conversation_rows": len(conversations),
            "early_root_previously_true": True,
            "complete_pages": page_evidence(scans.pages),
            "query_only_no_write": True,
        }
    )


def test_candidate_answers_are_fresh_without_postcapture_reads_or_durable_writes(
    rig, monkeypatch, evidence
):
    root = rig.create(definition())
    middle = rig.session.commit(lambda tx: child(tx, root))
    leaf = rig.session.commit(lambda tx: child(tx, middle))
    audit = ReadAudit(rig, monkeypatch)
    escaped = []

    def apply(tx):
        rows = {key: tx.task(key).to_tree() for key in (middle, leaf)}
        view = OwnedLiveQuery.capture(tx)
        escaped.append(view)
        initial, reads = QueryWindow(rig, tx), tuple(audit.calls)
        assert view.has(root) is True
        initial.unchanged(rig, tx)
        assert tuple(audit.calls) == reads
        tx.set_task({**rows[middle], "state": DONE})
        window = QueryWindow(rig, tx)
        assert view.has(root) is True
        window.unchanged(rig, tx)
        tx.set_task({**rows[leaf], "state": DONE})
        window = QueryWindow(rig, tx)
        assert view.has(root) is False and view.has(middle) is False
        window.unchanged(rig, tx)
        assert tuple(audit.calls) == reads
        evidence.append(
            {
                "same_transaction_answers": [True, True, False],
                "postcapture_backend_reads": 0,
                "query_windows_silent": True,
            }
        )

    rig.session.commit(apply)
    with pytest.raises(TransactionSettled):
        escaped[0].has(root)
    assert query(rig, root) is False


def test_waiting_dependency_is_not_ownership_and_false_does_not_relax_leaf(rig):
    root = rig.create(definition())
    terminal = rig.session.commit(lambda tx: child(tx, root))
    done(rig, terminal)
    assert query(rig, root) is False
    before = rig.silence()
    with pytest.raises(
        UnsupportedTaskExecution,
        match="^Task requires a validated unmarked pending root leaf$",
    ):
        rig.session.commit(
            lambda tx: ownership_query.require_unmarked_root_leaf(
                tx, tx.task(root).to_tree()
            )
        )
    rig.assert_silent(before)
    unrelated = rig.create(definition())
    rig.change(
        unrelated,
        lambda row: row.update(
            state={
                "status": "waiting",
                "checkpoint": {"phase": "start"},
                "on": [root],
                "policy": "allSettled",
            }
        ),
    )
    assert query(rig, root) is False
    ordinary = rig.session.commit(lambda tx: child(tx, root))
    assert query(rig, root) is True
    done(rig, ordinary)
    assert query(rig, root) is False


@pytest.mark.parametrize("first", ["query", "mutation"])
def test_actual_line_tickets_choose_query_snapshot_in_both_orders(
    rig, monkeypatch, first, evidence
):
    root = rig.create(definition())
    leaf = rig.session.commit(lambda tx: child(tx, root))
    tickets = Tickets(rig.session, monkeypatch)
    actions = {
        "query": lambda: rig.session.commit(
            lambda tx: OwnedLiveQuery.capture(tx).has(root)
        ),
        "mutation": lambda: done(rig, leaf),
    }
    with ThreadPoolExecutor(max_workers=3) as pool:
        with held_line(rig.session, pool):
            jobs = {first: pool.submit(actions[first])}
            tickets.wait(1)
            second = "mutation" if first == "query" else "query"
            jobs[second] = pool.submit(actions[second])
            actual = tickets.wait(2)
            assert actual[0][0] != actual[1][0] and actual[0][1] != actual[1][1]
        assert jobs["query"].result(10) is (first == "query")
        jobs["mutation"].result(10)
    assert query(rig, root) is False
    evidence.append(
        {
            "first": first,
            "actual_ticket_id_threads": actual,
            "query_answer": first == "query",
            "workers_joined": True,
        }
    )


def test_cancelled_queued_sync_query_never_enters_callback(rig, monkeypatch, evidence):
    root = rig.create(definition())
    latch, called = CancelLatch(), []
    tickets = Tickets(rig.session, monkeypatch)
    before = rig.silence()

    def apply(tx):
        called.append(threading.get_ident())
        return OwnedLiveQuery.capture(tx).has(root)

    with ThreadPoolExecutor(max_workers=2) as pool:
        with held_line(rig.session, pool):
            work = pool.submit(rig.session.commit, apply, cancel=latch.view())
            actual = tickets.wait(1)
            latch.cancel()
        with pytest.raises(CommitCancelled):
            work.result(10)
    assert called == []
    rig.assert_silent(before)
    evidence.append(
        {
            "queued_ticket": actual,
            "callback_entries": called,
            "cancelled_before_grant": True,
            "worker_joined": True,
        }
    )


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_admitted_sync_query_cancel_close_and_lease_wait_for_actual_worker(
    rig, monkeypatch, evidence
):
    root = rig.create(definition())
    rig.session.commit(lambda tx: child(tx, root))
    before = sqlite_rows(rig.path)
    commits = len(rig.storage.commits)
    hold = ScanHold(rig, monkeypatch)
    latch, exited, callbacks = CancelLatch(), threading.Event(), []

    def work():
        try:

            def apply(tx):
                callbacks.append(threading.get_ident())
                return OwnedLiveQuery.capture(tx).has(root)

            return rig.session.commit(apply, cancel=latch.view())
        finally:
            exited.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(work)
        try:
            assert hold.entered.wait(5)
            latch.cancel()
            rig.session.request_close()
            assert not hold.retired.is_set() and not rig.storage.closed.is_set()
            assert rig.session.end is None and not exited.is_set()
            with pytest.raises(StorageOwnershipError):
                open_sqlite_storage(str(rig.path), mode="open")
        finally:
            hold.release.set()
        assert future.result(10) is True
    rig.session.wait_closed(timeout=10)
    assert hold.retired.is_set() and exited.is_set() and rig.storage.closed.is_set()
    assert callbacks == [hold.thread]
    assert sqlite_rows(rig.path) == before and len(rig.storage.commits) == commits
    fresh = open_sqlite_storage(str(rig.path), mode="open")
    fresh.close()
    evidence.append(
        {
            "callback_threads": callbacks,
            "scan_retired": True,
            "worker_exited": True,
            "actual_close_complete": True,
            "inprocess_lease_refused_then_opened": True,
            "new_actors": 0,
        }
    )


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
@pytest.mark.parametrize("ending", ["explicit", "assembly"])
def test_writing_outer_rollback_is_distinct_from_silent_query(rig, ending, evidence):
    root = rig.create(definition())
    token, _ = unrelated_history(rig)
    before, commits, mints = (
        published(rig),
        len(rig.storage.commits),
        len(rig.storage.mints),
    )
    observed, escaped = [], []

    def apply(tx):
        row = tx.task(root).to_tree()
        view = OwnedLiveQuery.capture(tx)
        escaped.append(view)
        conv = tx.create_conversation({"kind": "task", "taskId": root})
        tx.create_task(
            definition(),
            None,
            ownership={"kind": "conversation"},
            conversation_id=conv.id,
        )
        draft = tx.doc(token, conversation_id=rig.conversation)
        draft["saved"] = 3
        if ending == "assembly":
            tx.set_task({**row, "state": {**DONE, "status": "completing"}})
        window = QueryWindow(rig, tx)
        observed.append(view.has(root))
        window.unchanged(rig, tx)
        assert draft["saved"] == 3
        if ending == "explicit":
            raise Rollback

    exception = Rollback if ending == "explicit" else RuntimeError
    with pytest.raises(
        exception, match=None if ending == "explicit" else "is completing"
    ):
        rig.session.commit(apply)
    assert observed == [True]
    assert_rollback(rig, before, commits, mints)
    with pytest.raises(TransactionSettled):
        escaped[0].has(root)
    assert query(rig, root) is False
    evidence.append(
        {
            "ending": ending,
            "query_answer": True,
            "outer_records_seq_revisions_rolled_back": True,
            "intentional_mints": len(rig.storage.mints) - mints,
        }
    )


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
@pytest.mark.parametrize("cut", ["before", "after"])
def test_real_outer_commit_failure_and_ack_loss_recapture_only_after_reopen(
    rig, monkeypatch, cut, evidence
):
    root = rig.create(definition())
    leaf = rig.session.commit(lambda tx: child(tx, root))
    token, _ = unrelated_history(rig)
    before = sqlite_rows(rig.path)
    commits, entered, answers = len(rig.storage.commits), [], []
    connection = rig.storage.inner._database.connection()
    original = getattr(sqlite, "_" + cut + "_commit")
    error = RuntimeError("ordinary-live-" + cut + "-commit")

    def fail(actual):
        if actual is not connection:
            return original(actual)
        entered.append(actual.in_transaction)
        raise error

    def apply(tx):
        row = tx.task(leaf).to_tree()
        view = OwnedLiveQuery.capture(tx)
        tx.set_task({**row, "state": DONE})
        tx.doc(token, conversation_id=rig.conversation)["saved"] = 3
        window = QueryWindow(rig, tx)
        answers.append(view.has(root))
        window.unchanged(rig, tx)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite, "_" + cut + "_commit", fail)
        with pytest.raises(RuntimeError) as caught:
            rig.session.commit(apply)
        assert caught.value is error
    assert entered == [cut == "before"] and answers == [False]
    assert len(rig.storage.commits) == commits + 1 and rig.session.failure is error
    reopen(rig)
    assert query(rig, root) is (cut == "before")
    if cut == "before":
        assert sqlite_rows(rig.path) == before
    else:
        assert rig.record(leaf)["state"] == DONE
        saved = rig.session.snapshot(token, conversation_id=rig.conversation)
        assert saved is not None and saved.value.thaw() == {"saved": 3}
        assert sqlite_rows(rig.path) != before
    evidence.append(
        {
            "real_cut": cut,
            "connection_in_transaction": entered,
            "query_answer_before_commit": answers,
            "commit_attempts": 1,
            "fresh_owner_query": cut == "before",
            "no_retry": True,
        }
    )


@pytest.mark.parametrize("rig", ["sqlite"], indirect=True)
def test_full_sql_oracle_detects_restored_document_with_revision_only_delta(rig):
    token = define_doc(
        "ordinary.query.oracle",
        1,
        "session",
        lambda: {"n": 1},
        checkpoint_when=lambda _value, _ops, _info: False,
    )
    rig.session.commit(lambda tx: tx.doc(token))
    before = sqlite_rows(rig.path)

    def change(tx, value):
        tx.doc(token)["n"] = value

    rig.session.commit(lambda tx: change(tx, 2))
    rig.session.commit(lambda tx: change(tx, 1))
    after = sqlite_rows(rig.path)
    assert before["documents"] == after["documents"]
    assert before["document_revisions"] != after["document_revisions"]
    assert before != after
