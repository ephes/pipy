"""Internal table acceptance; document capability and Session belong later slices."""

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from pipy_harness.native.durable import memory
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.records import RECORD_TYPES, SAFE_INTEGER
from pipy_harness.native.durable.storage import (
    ConversationQuery,
    Cursor,
    DocumentAddress,
    DocumentQuery,
    EntryQuery,
    Page,
    StorageClosedError,
    StorageRequestError,
    StorageWrite,
    SubmissionQuery,
    TaskQuery,
    UnsupportedStorageOperation,
)


def commit(store, *writes):
    return store.commit(tuple(StorageWrite(write) for write in writes))


def root():
    store = MemoryStorage()
    assert commit(store, {"type": "conversation", "value": {"id": 1}}) == 1
    return store


def entry(id, conversation=1, **extra):
    return {
        "type": "entry",
        "value": {"id": id, "conversationId": conversation, "kind": "", **extra},
    }


def task(id, status="pending", **extra):
    state = {"status": status, "checkpoint": None}
    if status == "terminal":
        state = {"status": status, "outcome": {"status": "completed", "result": None}}
    return {
        "type": "task",
        "value": {
            "id": id,
            "conversationId": 1,
            "kind": "",
            "version": 1,
            "input": None,
            "background": False,
            "abortRequested": False,
            "state": state,
            **extra,
        },
    }


def submission(id, request="x", **extra):
    return {
        "type": "submission",
        "value": {
            "id": id,
            "conversationId": 1,
            "type": "input",
            "status": "queued",
            "requestId": request,
            **extra,
        },
    }


def ids(page):
    return [record.id for record in page.items]


def test_root_global_namespace_mint_seq_empty_and_exhaustion():
    store = MemoryStorage()
    assert store.conversation(1) is None
    assert store.mint_id() == 2
    assert commit(store, {"type": "conversation", "value": {"id": 1}}) == 1
    assert store.commit(()) == 2
    assert store.mint_id() == 3
    commit(store, entry(100))
    assert store.mint_id() == 101
    with pytest.raises(RuntimeError, match="already belongs to entry"):
        commit(store, task(100))
    commit(store, entry(SAFE_INTEGER))
    for _ in range(2):
        with pytest.raises(RuntimeError, match="exhausted"):
            store.mint_id()
    assert store.entry(100).commit_seq == 3
    assert store.conversation(1).id == 1


def test_supported_last_failure_does_not_leak_any_table_index_or_counter():
    store = root()
    commit(store, entry(2), task(3), submission(4))
    before = store._state
    with pytest.raises(RuntimeError, match="written more than once"):
        commit(
            store,
            {
                "type": "conversation",
                "value": {"id": 10, "owner": {"conversationId": 1, "taskId": 3}},
            },
            task(3, "running"),
            submission(4, "changed"),
            entry(11),
            entry(11),
        )
    assert store._state is before
    assert store.conversation(10) is None and store.entry(11) is None
    assert (
        ids(store.scan_conversations(ConversationQuery({"ownerTaskId": 3}), 10)) == []
    )
    assert ids(store.scan_tasks(TaskQuery({"status": "pending"}), 10)) == [3]
    assert store.submission_by_request(1, "changed") is None
    assert store.submission_by_request(1, "x").id == 4
    assert store.mint_id() == 5
    assert store.commit(()) == 3


def test_candidate_index_failure_is_reached_after_preparation_and_is_atomic(
    monkeypatch,
):
    store = root()
    before = store._state
    observed = []
    original = memory._indexes

    def failure(candidate):
        assert candidate.tables["entry"][100].to_tree()["data"] == {"x": [None]}
        assert candidate.requests[(1, b"\x00x")] == 102
        observed.append(candidate)
        raise RuntimeError("injected preparation fault")

    monkeypatch.setattr(memory, "_indexes", failure)
    with pytest.raises(RuntimeError, match="injected preparation fault"):
        commit(store, entry(100, data={"x": [None]}), task(101), submission(102))
    assert len(observed) == 1 and store._state is before
    assert store.entry(100) is None and store.task(101) is None
    assert store.submission_by_request(1, "x") is None
    monkeypatch.setattr(memory, "_indexes", original)
    assert store.mint_id() == 2 and store.commit(()) == 2


def test_replacement_and_last_write_request_winner_not_largest_id():
    store = root()
    commit(
        store,
        task(2, trace={"traceparent": "synthetic"}),
        submission(20, "shared"),
        submission(10, "shared"),
    )
    assert store.submission_by_request(1, "shared").id == 10
    # Removing an old loser must not remove the winner's binding.
    commit(store, submission(20, "different"), task(2, "running"))
    assert store.submission_by_request(1, "shared").id == 10
    assert "trace" not in store.task(2).to_tree()
    assert ids(store.scan_tasks(TaskQuery({"status": "pending"}), 10)) == []
    assert ids(store.scan_tasks(TaskQuery({"status": "running"}), 10)) == [2]
    commit(store, submission(10, "next"), submission(20, "next"), task(2, "terminal"))
    assert store.submission_by_request(1, "shared") is None
    assert store.submission_by_request(1, "next").id == 20
    assert ids(store.scan_tasks(TaskQuery({"status": "terminal"}), 10)) == [2]


def test_ancestry_segment_order_caps_marker_and_owner_edges():
    store = root()
    commit(
        store,
        entry(2, head=2),
        entry(20),
        {
            "type": "conversation",
            "value": {
                "id": 3,
                "parent": {"conversationId": 1, "at": 2},
                "owner": {"conversationId": 1, "taskId": 99},
            },
        },
        entry(4, 3, head=2),
        entry(5, 3),
        {
            "type": "conversation",
            "value": {"id": 6, "parent": {"conversationId": 3, "at": 4}},
        },
        entry(7, 6),
    )
    assert ids(store.scan_entries(EntryQuery({"conversationId": 6}), 10)) == [7, 4, 2]
    assert ids(
        store.scan_entries(EntryQuery({"conversationId": 6, "order": "ascending"}), 10)
    ) == [2, 4, 7]
    marker = store.find_latest_head_marker(6)
    assert marker.id == 4 and marker.to_tree()["head"] == 2
    assert store.find_latest_head_marker(6, 2).id == 2
    assert store.entry(20, conversation_id=6) is None
    assert store.entry(20).commit_seq == 2
    assert store.entry(4, conversation_id=6).entry.id == 4
    assert ids(
        store.scan_conversations(
            ConversationQuery({"ownerTaskId": 99, "ownerConversationId": 1}), 10
        )
    ) == [3]
    assert (
        ids(
            store.scan_conversations(
                ConversationQuery({"ownerTaskId": 99, "ownerConversationId": 6}), 10
            )
        )
        == []
    )
    with pytest.raises(StorageRequestError, match="Unknown conversation"):
        store.entry(4, conversation_id=100)


def test_ascending_reverses_segments_not_global_ids():
    store = root()
    commit(
        store,
        entry(50),
        {
            "type": "conversation",
            "value": {"id": 2, "parent": {"conversationId": 1, "at": 50}},
        },
        entry(3, 2),
    )
    assert ids(
        store.scan_entries(EntryQuery({"conversationId": 2, "order": "ascending"}), 10)
    ) == [50, 3]


@pytest.mark.parametrize("order", ["ascending", "descending"])
def test_table_cursors_keep_order_position_after_new_commit(order):
    store = root()
    commit(store, entry(5), entry(2), entry(4), task(8), task(7), task(6))
    query = EntryQuery({"conversationId": 1, "order": order})
    page = store.scan_entries(query, 2)
    assert ids(page) == ([2, 4] if order == "ascending" else [5, 4])
    commit(store, entry(10))
    assert ids(
        store.scan_entries(EntryQuery({"conversationId": 1}), 10, page.next)
    ) == ([5, 10] if order == "ascending" else [2])
    with pytest.raises(StorageRequestError, match="cursor continues"):
        store.scan_entries(
            EntryQuery(
                {
                    "conversationId": 1,
                    "order": "descending" if order == "ascending" else "ascending",
                }
            ),
            10,
            page.next,
        )
    assert ids(store.scan_tasks(TaskQuery({"order": order}), 2)) == (
        [6, 7] if order == "ascending" else [8, 7]
    )
    assert ids(store.scan_tasks(TaskQuery({}), 10, Cursor(-1, "ascending"))) == [
        6,
        7,
        8,
    ]


def test_full_retained_and_returned_ownership_and_utf16_index_identity():
    store = root()
    shared = {"items": [None, -0.0, 2**60, "\ud800"]}
    source = entry(2, data={"left": shared, "right": shared})
    write = StorageWrite(source)
    shared["items"].append("caller")
    store.commit(
        (
            write,
            StorageWrite(task(3, kind="\U0001f600")),
            StorageWrite(submission(4, "\U0001f600")),
            StorageWrite(task(5, kind="\ud800")),
            StorageWrite(task(6, kind="\ud801")),
        )
    )
    value = store.entry(2).entry.to_tree()
    value["data"]["left"]["items"].append("read")
    assert len(value["data"]["right"]["items"]) == 4
    assert len(store.entry(2).entry.to_tree()["data"]["left"]["items"]) == 4
    assert ids(store.scan_tasks(TaskQuery({"kind": "\ud83d\ude00"}), 10)) == [3]
    assert store.submission_by_request(1, "\ud83d\ude00").id == 4
    assert ids(store.scan_tasks(TaskQuery({"kind": "\ud800"}), 10)) == [5]
    assert ids(store.scan_tasks(TaskQuery({"kind": "\ud801"}), 10)) == [6]
    assert store.task(3).to_tree()["kind"] == "\U0001f600"
    commit(store, task(3, kind="\ud83d\ude00"))
    assert store.task(3).to_tree()["kind"] == "\ud83d\ude00"
    assert ids(store.scan_tasks(TaskQuery({"kind": "\U0001f600"}), 10)) == [3]


def operations(store):
    return [
        lambda: store.mint_id(),
        lambda: store.commit(()),
        lambda: store.conversation(1),
        lambda: store.task(2),
        lambda: store.submission(3),
        lambda: store.submission_by_request(1, "x"),
        lambda: store.entry(2),
        lambda: store.find_latest_head_marker(1),
        lambda: store.scan_conversations(ConversationQuery({}), 1),
        lambda: store.scan_entries(EntryQuery({"conversationId": 1}), 1),
        lambda: store.scan_tasks(TaskQuery({}), 1),
        lambda: store.scan_submissions(SubmissionQuery({}), 1),
        lambda: store.document(4),
        lambda: store.find_document(
            DocumentAddress({"kind": "", "scope": {"kind": "session"}})
        ),
        lambda: store.scan_documents(
            DocumentQuery({"scope": {"kind": "session"}, "at": "current"}), 1
        ),
    ]


def test_explicit_document_refusal_and_all_method_close():
    store = root()
    with pytest.raises(UnsupportedStorageOperation):
        commit(store, entry(2), {"type": "document.retire", "id": 3})
    assert store.entry(2) is None and store.mint_id() == 2
    for operation in operations(store)[-3:]:
        with pytest.raises(UnsupportedStorageOperation):
            operation()
    store.close()
    store.close()
    for operation in operations(store):
        with pytest.raises(StorageClosedError):
            operation()


class WitnessLock:
    """Test-only observation of an actual blocked acquisition, never product API."""

    def __init__(self):
        self.lock = threading.Lock()
        self.attempt = threading.Event()

    def __enter__(self):
        if threading.current_thread().name.startswith("contender"):
            self.attempt.set()
        self.lock.acquire()

    def __exit__(self, *args):
        self.lock.release()


@pytest.mark.parametrize("contender", ["write", "read", "mint", "close"])
def test_one_authority_linearizes_real_contended_operations(monkeypatch, contender):
    store = root()
    ready, release = threading.Event(), threading.Event()
    authority = WitnessLock()
    store._lock = authority
    original = memory._indexes

    def barrier(candidate):
        if threading.current_thread().name.startswith("writer"):
            assert set(candidate.tables["entry"]) == {100, 101}
            ready.set()
            assert release.wait(5)
        return original(candidate)

    monkeypatch.setattr(memory, "_indexes", barrier)
    first = ThreadPoolExecutor(1, thread_name_prefix="writer")
    second = ThreadPoolExecutor(1, thread_name_prefix="contender")
    try:
        writer = first.submit(commit, store, entry(100), entry(101), task(102))
        assert ready.wait(5)
        choices = {
            "write": lambda: commit(store, submission(103)),
            "read": lambda: ids(
                store.scan_entries(EntryQuery({"conversationId": 1}), 10)
            ),
            "mint": store.mint_id,
            "close": store.close,
        }
        follower = second.submit(choices[contender])
        assert authority.attempt.wait(5)
        assert authority.lock.locked()  # writer still owns the actual common authority
        release.set()
        assert writer.result(5) == 2
        result = follower.result(5)
        if contender == "write":
            assert result == 3 and store.submission(103).id == 103 and store.entry(100)
        elif contender == "read":
            assert result == [101, 100]
        elif contender == "mint":
            assert result == 103
        else:
            with pytest.raises(StorageClosedError):
                store.commit(())
    finally:
        release.set()
        first.shutdown(wait=True)
        second.shutdown(wait=True)
    assert not [
        t for t in threading.enumerate() if t.name.startswith(("writer_", "contender_"))
    ]


def test_sequence_exhaustion_refuses_without_corrupting_state():
    store = root()
    store._state = replace(store._state, next_seq=SAFE_INTEGER)
    assert store.commit(()) == SAFE_INTEGER
    before = store._state
    with pytest.raises(RuntimeError, match="sequence"):
        store.commit(())
    assert store._state is before and store.conversation(1).id == 1


def test_request_winner_removal_has_no_fallback_and_legacy_cursor_uses_table_default():
    store = root()
    commit(store, submission(40, "retained"), submission(10, "retained"))
    assert store.submission_by_request(1, "retained").id == 10
    replacement = submission(10)
    del replacement["value"]["requestId"]
    commit(store, replacement)
    assert store.submission(40).to_tree()["requestId"] == "retained"
    assert store.submission_by_request(1, "retained") is None
    for query, method in (
        (EntryQuery({"conversationId": 1, "order": "ascending"}), store.scan_entries),
        (TaskQuery({"order": "descending"}), store.scan_tasks),
    ):
        with pytest.raises(StorageRequestError, match="cursor continues"):
            method(query, 10, Cursor(0))


@pytest.mark.parametrize("kind", ["conversation", "entry", "task", "submission"])
def test_all_table_exact_and_page_results_retain_independent_owned_snapshots(kind):
    store = root()
    source = {
        "conversation": {
            "type": "conversation",
            "value": {"id": 2, "parent": {"conversationId": 1, "at": 1}},
        },
        "entry": entry(2, data={"nested": [None]}),
        "task": task(2, input={"nested": [None]}),
        "submission": submission(
            2, status="unanswered", reason="synthetic", detail={"nested": [None]}
        ),
    }[kind]
    write = StorageWrite(source)
    store.commit((write,))
    methods = {
        "conversation": (
            store.conversation,
            store.scan_conversations,
            ConversationQuery({}),
        ),
        "entry": (
            lambda id: store.entry(id).entry,
            store.scan_entries,
            EntryQuery({"conversationId": 1}),
        ),
        "task": (store.task, store.scan_tasks, TaskQuery({})),
        "submission": (store.submission, store.scan_submissions, SubmissionQuery({})),
    }
    read, scan, query = methods[kind]
    before = read(2)
    tree = before.to_tree()
    page = scan(query, 10)
    selected = next(item for item in page.items if item.id == 2)
    expected = write.to_tree()["value"]
    source["value"].clear()
    tree.clear()
    selected.to_tree().clear()
    assert before.to_tree() == expected
    assert selected.to_tree() == expected
    assert read(2).to_tree() == expected
    if kind in {"task", "submission"}:
        changed = (
            task(2, input={"new": [True]})
            if kind == "task"
            else submission(
                2, status="unanswered", reason="synthetic", detail={"new": [True]}
            )
        )
        commit(store, changed)
        assert read(2).to_tree() != expected
        assert before.to_tree() == selected.to_tree() == expected


def dangling_parent_storage(*, two_entries=True):
    store = MemoryStorage()
    writes = [
        {"type": "conversation", "value": {"id": 1}},
        {
            "type": "conversation",
            "value": {"id": 2, "parent": {"conversationId": 999, "at": 10}},
        },
        entry(3, 2, head=3),
    ]
    if two_entries:
        writes.append(entry(4, 2))
    assert commit(store, *writes) == 1  # Raw storage accepts these records.
    return store


@pytest.mark.parametrize("read", ["page", "point", "head"])
def test_raw_local_reads_return_before_unneeded_parent_access(read):
    store = dangling_parent_storage()
    try:
        if read == "page":
            page = store.scan_entries(EntryQuery({"conversationId": 2}), 1)
            assert ids(page) == [4] and page.next == Cursor(4, "descending")
        elif read == "point":
            lookup = store.entry(3, conversation_id=2)
            assert lookup.entry.to_tree() == entry(3, 2, head=3)["value"]
            assert lookup.commit_seq == 1
        else:
            assert (
                store.find_latest_head_marker(2).to_tree()
                == entry(3, 2, head=3)["value"]
            )
    finally:
        store.close()


@pytest.mark.parametrize("order", ["ascending", "descending"])
def test_ancestry_minimum_cap_stops_before_following_unneeded_parent(order):
    store = dangling_parent_storage()
    try:
        query = EntryQuery({"conversationId": 2, "minEntryId": 11, "order": order})
        assert ids(store.scan_entries(query, 10)) == []
        # Current entries are below the minimum; the parent cap10 is below it too.
        assert store.entry(11, conversation_id=2) is None
    finally:
        store.close()


@pytest.mark.parametrize("read", ["ascending", "page-lookahead", "head-below-local"])
def test_raw_reads_still_access_parent_when_source_requires_it(read):
    store = dangling_parent_storage(two_entries=read != "page-lookahead")
    try:
        # Raw malformed ancestry has no Session graph validation. Preserve the
        # actual traversal cut, not a new validity rule or a swallowed exception.
        with pytest.raises(KeyError, match="999"):
            if read == "ascending":
                store.scan_entries(
                    EntryQuery({"conversationId": 2, "order": "ascending"}), 1
                )
            elif read == "page-lookahead":
                store.scan_entries(EntryQuery({"conversationId": 2}), 1)
            else:
                store.find_latest_head_marker(2, 2)
    finally:
        store.close()


def _limit_boundary_storage(kind, match_state):
    root_write = {"type": "conversation", "value": {"id": 1}}
    submissions = [submission(id) for id in (2, 3)]
    for write in submissions:
        del write["value"]["requestId"]
    writes = {
        "conversation": [root_write, {"type": "conversation", "value": {"id": 2}}],
        "entry": [root_write, entry(2), entry(3)],
        "task": [root_write, task(2, kind="probe"), task(3, kind="probe")],
        "submission": [root_write, *submissions],
    }[kind]
    store = MemoryStorage()
    seed = (
        ([] if kind == "conversation" else [root_write])
        if match_state == "empty"
        else writes
    )
    if seed:
        commit(store, *seed)
    base = {"conversationId": 1} if kind == "entry" else {}
    filtered = {
        "conversation": {"ownerTaskId": 999},
        "entry": {"conversationId": 1, "minEntryId": 999},
        "task": {"kind": "absent"},
        "submission": {"status": "done"},
    }[kind]
    query = {
        "conversation": ConversationQuery,
        "entry": EntryQuery,
        "task": TaskQuery,
        "submission": SubmissionQuery,
    }[kind](filtered if match_state == "filtered-nomatch" else base)
    rows = [write["value"] for write in writes if write["type"] == kind]
    if kind == "entry":
        rows.reverse()
    return store, query, rows


@pytest.mark.parametrize("kind", ["conversation", "entry", "task", "submission"])
@pytest.mark.parametrize("match_state", ["empty", "nonempty", "filtered-nomatch"])
@pytest.mark.parametrize("limit", [0, -1, -2, 1])
def test_pinned_raw_scan_limit_boundary_preserves_pages_and_python_failures(
    kind, match_state, limit
):
    # Empirical PIN domain: these four values, not a new positivity policy.
    # Actual Node raw errors are TypeError; native raw errors remain IndexError.
    store, query, rows = _limit_boundary_storage(kind, match_state)
    method = getattr(
        store,
        "scan_"
        + {
            "conversation": "conversations",
            "entry": "entries",
            "task": "tasks",
            "submission": "submissions",
        }[kind],
    )
    try:
        if limit in (-1, -2) or (limit == 0 and match_state == "nonempty"):
            with pytest.raises(IndexError) as failure:
                method(query, limit)
            assert type(failure.value) is IndexError
            assert str(failure.value) == "tuple index out of range"
        else:
            items = (RECORD_TYPES[kind](rows[0]),) if match_state == "nonempty" else ()
            order = "descending" if kind == "entry" else "ascending"
            cursor = Cursor(rows[0]["id"], order) if items else None
            assert method(query, limit) == Page(items, cursor)
    finally:
        store.close()
