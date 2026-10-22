"""Bounded tables/current raw-base acceptance; Session/history belong later slices."""

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
    Storage,
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


def _as_storage(store: MemoryStorage) -> Storage:
    return store


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


def test_unsupported_document_commands_refuse_whole_batch_and_all_method_close():
    store = _as_storage(root())
    with pytest.raises(UnsupportedStorageOperation):
        commit(
            store,
            entry(2),
            {
                "type": "document.copy",
                "record": {"id": 3, "kind": "copy", "scope": SESSION},
                "source": {"id": 4, "at": "current"},
            },
        )
    assert store.entry(2) is None and store.mint_id() == 2
    assert store.document(4) is None
    assert operations(store)[-2]() is None
    assert operations(store)[-1]().items == ()
    store.close()
    store.close()
    for operation in operations(store):
        with pytest.raises(StorageClosedError):
            operation()
    with pytest.raises(StorageClosedError):
        commit(store, entry(100), create_document(101))


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


SESSION = {"kind": "session"}


def create_document(id, *, kind="cache", scope=None, value=None, version=1, **record):
    return {
        "type": "document.create",
        "record": {
            "id": id,
            "kind": kind,
            "scope": SESSION if scope is None else scope,
            **record,
        },
        "content": {
            "kind": "base",
            "version": version,
            "value": {} if value is None else value,
        },
    }


def change_document(id, value, version=1):
    return {
        "type": "document.change",
        "id": id,
        "content": {"kind": "base", "version": version, "value": value},
    }


def document_query(scope=None, **extra):
    return DocumentQuery(
        {"scope": SESSION if scope is None else scope, "at": "current", **extra}
    )


def address(kind="cache", scope=None, **extra):
    return DocumentAddress(
        {"scope": SESSION if scope is None else scope, "kind": kind, **extra}
    )


def test_document_current_base_replaces_value_version_and_keeps_old_snapshots():
    store = root()
    write = create_document(2, value={"old": [None], "__proto__": {"x": -0.0}})
    assert commit(store, write) == 2
    old = store.document(2)
    assert old.to_tree() == {
        "record": {"id": 2, "kind": "cache", "scope": SESSION, "createdAt": 2},
        "version": 1,
        "value": write["content"]["value"],
        "deltasSinceBase": 0,
    }
    assert commit(store, change_document(2, {"new": [2**60]}, 7)) == 3
    assert store.document(2).to_tree()["value"] == {"new": [2**60]}
    assert store.document(2).to_tree()["version"] == 7
    assert "old" in old.to_tree()["value"] and old.to_tree()["version"] == 1
    assert store.find_document(address()).to_tree() == old.to_tree()["record"]
    assert (
        store.scan_documents(document_query(), 10).items[0].to_tree()
        == old.to_tree()["record"]
    )
    with pytest.raises(UnsupportedStorageOperation, match="history"):
        store.document(2, 2)
    with pytest.raises(UnsupportedStorageOperation):
        store.find_document(address(), 2)
    with pytest.raises(UnsupportedStorageOperation):
        store.scan_documents(DocumentQuery({"scope": SESSION, "at": 2}), 10)
    store.close()


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "operation", ["empty-new", "empty-occupied", "change-retire", "replace"]
)
def test_document_per_id_grouping_and_final_address_cardinality(reverse, operation):
    store = root()
    if operation != "empty-new":
        commit(store, create_document(2, value={"old": True}))
    id = 3 if operation in {"empty-new", "empty-occupied", "replace"} else 2
    content = (
        change_document(2, {"new": True}, 2)
        if operation == "change-retire"
        else create_document(id)
    )
    retire = {"type": "document.retire", "id": 2 if operation == "replace" else id}
    seq = commit(store, *([retire, content] if reverse else [content, retire]))
    if operation == "replace":
        assert store.document(2) is None
        assert store.find_document(address()).id == 3
        assert store.document(3).to_tree()["record"]["createdAt"] == seq
    elif operation == "empty-occupied":
        assert store.document(3) is None and store.find_document(address()).id == 2
        assert ids(store.scan_documents(document_query(), 10)) == [2]
    else:
        assert store.document(id) is None and store.find_document(address()) is None
    retained = store._state.documents[2 if operation == "replace" else id].to_tree()[
        "record"
    ]
    assert retained["retiredAt"] == seq
    if operation.startswith("empty"):
        assert retained["createdAt"] == seq
    with pytest.raises(RuntimeError, match="already belongs to document"):
        commit(store, create_document(id))
    assert store.mint_id() == max(3, id + 1)
    store.close()


@pytest.mark.parametrize(
    "writes,message",
    [
        (
            [change_document(2, {}), change_document(2, {})],
            "more than one content command",
        ),
        ([create_document(3), change_document(3, {})], "more than one content command"),
        ([change_document(3, {}), create_document(3)], "more than one content command"),
        ([{"type": "document.retire", "id": 2}] * 2, "retired more than once"),
        ([change_document(999, {})], "Unknown document: 999"),
        ([{"type": "document.retire", "id": 999}], "Unknown document: 999"),
        ([create_document(3)], "address already has a current incarnation"),
    ],
)
def test_document_supported_failures_are_atomic_and_ordinary_errors(writes, message):
    store = root()
    commit(store, create_document(2), task(3), submission(4))
    # Conflict recipe must use a fresh global ID, not fail at ID claims first.
    writes = [
        {**w, "record": {**w["record"], "id": 5}}
        if w.get("type") == "document.create" and w["record"]["id"] == 3
        else w
        for w in writes
    ]
    if "content command" in message:
        writes = [{**w, "id": 5} if w.get("id") == 3 else w for w in writes]
    before = store._state
    with pytest.raises(RuntimeError, match=message) as failed:
        commit(store, entry(100), task(3, "running"), submission(4, "new"), *writes)
    assert type(failed.value) is RuntimeError and store._state is before
    assert (
        store.entry(100) is None
        and store.task(3).to_tree()["state"]["status"] == "pending"
    )
    assert store.submission_by_request(1, "new") is None
    assert store.document(2) and store.find_document(address()).id == 2
    assert store.mint_id() == 5 and store.commit(()) == 3
    store.close()


def test_document_retired_and_global_cross_kind_ids_never_recycle():
    store = root()
    commit(store, create_document(2), {"type": "document.retire", "id": 2})
    for write in (change_document(2, {}), {"type": "document.retire", "id": 2}):
        with pytest.raises(RuntimeError, match="Document 2 is retired"):
            commit(store, write)
    for write in (entry(2), task(2), create_document(2)):
        with pytest.raises(RuntimeError, match="already belongs to document"):
            commit(store, write)
    with pytest.raises(RuntimeError, match="written more than once"):
        commit(store, entry(3), create_document(3))
    assert store.mint_id() == 3
    store.close()


@pytest.mark.parametrize("fault", ["address-conflict", "allocation"])
def test_document_late_candidate_failure_rolls_back_tables_docs_indexes_and_counters(
    monkeypatch, fault
):
    store = root()
    commit(store, entry(2, head=2), task(3), submission(4), create_document(5))
    assert store.mint_id() == 6  # Already-minted gap remains minted.
    before = store._state
    original = memory._validate_document_candidate
    witnessed = []

    def validator(candidate, counts):
        assert candidate is not before and candidate.tables["entry"][100]
        assert candidate.tables["task"][3].to_tree()["state"]["status"] == "running"
        assert candidate.owners[("taskId", 3)] == (99,)
        assert candidate.statuses[("task", "running")] == (3,)
        assert candidate.entries[1] == (2, 100)
        assert candidate.requests[(1, memory._units("new"))] == 4
        assert candidate.documents[101].to_tree()["value"] == {"prepared": True}
        assert 101 in candidate.scopes[memory._scope_key(SESSION)]
        witnessed.append(candidate)
        if fault == "allocation":
            original(candidate, counts)
            raise MemoryError("candidate allocation after mixed preparation")
        return original(candidate, counts)

    monkeypatch.setattr(memory, "_validate_document_candidate", validator)
    writes = [
        {
            "type": "conversation",
            "value": {"id": 99, "owner": {"conversationId": 1, "taskId": 3}},
        },
        entry(100),
        task(3, "running"),
        submission(4, "new"),
        create_document(101, kind="new", value={"prepared": True}),
    ]
    if fault == "address-conflict":
        writes.append(create_document(102))
    with pytest.raises(
        MemoryError if fault == "allocation" else RuntimeError,
        match="allocation|current incarnation",
    ):
        commit(store, *writes)
    assert len(witnessed) == 1 and store._state is before
    assert store.conversation(99) is None
    assert (
        store.scan_conversations(ConversationQuery({"ownerTaskId": 3}), 10).items == ()
    )
    assert store.entry(100) is None and store.find_latest_head_marker(1).id == 2
    assert store.entry(2).commit_seq == 2
    assert ids(store.scan_tasks(TaskQuery({"status": "pending"}), 10)) == [3]
    assert ids(store.scan_tasks(TaskQuery({"status": "running"}), 10)) == []
    assert (
        store.submission_by_request(1, "x").id == 4
        and store.submission_by_request(1, "new") is None
    )
    assert store.document(101) is None and store.find_document(address()).id == 5
    assert ids(store.scan_documents(document_query(), 10)) == [5]
    monkeypatch.setattr(memory, "_validate_document_candidate", original)
    assert store.mint_id() == 7 and store.commit(()) == 3
    store.close()


def test_document_scope_address_utf16_and_singleton_empty_family_identity():
    store = root()
    scopes = [
        SESSION,
        {"kind": "conversation", "conversationId": 1},
        {"kind": "task", "taskId": 1},
    ]
    writes = []
    for i, scope in enumerate(scopes):
        policy = (
            {"history": "rewindable", "fork": "initial"}
            if scope["kind"] == "conversation"
            else {}
        )
        writes += [
            create_document(10 + i * 2, scope=scope, kind="\U0001f600", **policy),
            create_document(
                11 + i * 2, scope=scope, kind="\ud83d\ude00", key="", **policy
            ),
        ]
    writes += [
        create_document(20, kind="\ud800", key="\ud800"),
        create_document(21, kind="\ud801", key="\ud801"),
    ]
    commit(store, *writes)
    for i, scope in enumerate(scopes):
        assert store.find_document(address("\ud83d\ude00", scope)).id == 10 + i * 2
        assert (
            store.find_document(address("\U0001f600", scope, key="")).id == 11 + i * 2
        )
        assert ids(
            store.scan_documents(document_query(scope, kind="\ud83d\ude00"), 10)
        ) == [10 + i * 2, 11 + i * 2]
    assert store.find_document(address("\ud800", key="\ud800")).id == 20
    assert store.find_document(address("\ud801", key="\ud801")).id == 21
    commit(store, create_document(22, kind="family", key="\U0001f600"))
    assert store.find_document(address("family", key="\ud83d\ude00")).id == 22
    with pytest.raises(RuntimeError, match="current incarnation"):
        commit(store, create_document(23, kind="family", key="\ud83d\ude00"))
    store.close()


@pytest.mark.parametrize("order", [None, "ascending", "descending"])
def test_document_cursor_ignores_order_and_always_resumes_ascending(order):
    store = root()
    commit(
        store,
        create_document(4, key="a"),
        create_document(2, key="b"),
        create_document(3, key="c"),
    )
    page = store.scan_documents(document_query(), 1, Cursor(-1, order))
    assert ids(page) == [2] and page.next == Cursor(2, "ascending")
    commit(store, create_document(5, key="new"))
    next_page = store.scan_documents(document_query(), 2, Cursor(2, order))
    assert ids(next_page) == [3, 4] and next_page.next == Cursor(4, "ascending")
    assert ids(store.scan_documents(document_query(), 10, Cursor(4, order))) == [5]
    store.close()


@pytest.mark.parametrize("state", ["empty", "nonempty", "filtered-nomatch"])
@pytest.mark.parametrize("limit", [0, -1, -2, 1])
def test_document_raw_page_limit_boundary_keeps_source_cut(state, limit):
    store = root()
    if state != "empty":
        commit(store, create_document(2, key="a"), create_document(3, key="b"))
    query = document_query(
        **({"kind": "absent"} if state == "filtered-nomatch" else {})
    )
    if limit in (-1, -2) or (limit == 0 and state == "nonempty"):
        with pytest.raises(IndexError, match="tuple index out of range"):
            store.scan_documents(query, limit)
    else:
        page = store.scan_documents(query, limit)
        assert ids(page) == ([2] if state == "nonempty" else [])
        assert page.next == (Cursor(2, "ascending") if state == "nonempty" else None)
    store.close()


def test_document_live_aliases_inputs_pages_records_materializations_and_old_snapshots():
    store = root()
    shared = {"items": [None, -0.0, 2**60, "\ud800"]}
    value = {"left": shared, "right": shared, "__proto__": shared}
    write = create_document(2, value=value)
    dto = StorageWrite(write)
    store.commit((dto,))
    shared["items"].append("caller")
    first = store.document(2)
    tree = first.to_tree()
    assert len(tree["value"]["left"]["items"]) == 4
    tree["value"]["left"]["items"].append("read")
    assert len(tree["value"]["right"]["items"]) == 4
    assert len(store.document(2).to_tree()["value"]["left"]["items"]) == 4
    record = store.find_document(address())
    page = store.scan_documents(document_query(), 10)
    record.to_tree()["scope"]["kind"] = "changed"
    page.items[0].to_tree()["scope"]["kind"] = "changed"
    assert store.find_document(address()).to_tree()["scope"] == SESSION
    commit(store, change_document(2, {"new": []}))
    assert "left" in first.to_tree()["value"]
    assert dto.to_tree()["content"]["value"]["left"]["items"][-1] == "\ud800"
    store.close()


@pytest.mark.parametrize("contender", ["write", "read", "mint", "close", "failure"])
def test_mixed_document_candidate_uses_same_contended_authority(monkeypatch, contender):
    store = root()
    authority = WitnessLock()
    store._lock = authority
    ready = threading.Event()
    release = threading.Event()
    original = memory._indexes

    def barrier(candidate):
        if threading.current_thread().name.startswith("writer"):
            assert candidate.documents[102] and candidate.tables["entry"][100]
            ready.set()
            assert release.wait(5)
            if contender == "failure":
                raise MemoryError("mixed preparation failure")
        return original(candidate)

    monkeypatch.setattr(memory, "_indexes", barrier)
    first = ThreadPoolExecutor(1, thread_name_prefix="writer")
    second = ThreadPoolExecutor(1, thread_name_prefix="contender")
    try:
        writer = first.submit(
            commit, store, entry(100), task(101), create_document(102)
        )
        assert ready.wait(5)

        def read():
            with store._lock:
                # One public read has its own linearization; sample one complete
                # captured generation under that same authority for mixed proof.
                state = store._state
                captured = (100 in state.tables["entry"], 102 in state.documents)
            return (
                captured,
                store.entry(100) is not None,
                store.document(102) is not None,
            )

        choices = {
            "write": lambda: commit(
                store, submission(103), create_document(104, key="next")
            ),
            "read": read,
            "mint": store.mint_id,
            "close": store.close,
            "failure": lambda: commit(store, create_document(103)),
        }
        follower = second.submit(choices[contender])
        assert authority.attempt.wait(5)
        assert authority.lock.locked()
        release.set()
        if contender == "failure":
            with pytest.raises(MemoryError):
                writer.result(5)
            assert follower.result(5) == 2 and store.document(103)
            assert store.entry(100) is None and store.document(102) is None
        else:
            assert writer.result(5) == 2
            result = follower.result(5)
            if contender == "write":
                assert (
                    result == 3
                    and store.document(102)
                    and store.document(104)
                    and store.submission(103)
                )
            elif contender == "read":
                assert result == ((True, True), True, True)
            elif contender == "mint":
                assert result == 103
            else:
                for op in operations(store):
                    with pytest.raises(StorageClosedError):
                        op()
    finally:
        release.set()
        first.shutdown(wait=True)
        second.shutdown(wait=True)
        store.close()
    assert not [
        t for t in threading.enumerate() if t.name.startswith(("writer_", "contender_"))
    ]


def test_unsupported_document_batch_does_not_publish_supported_mixed_prefix():
    store = root()
    commit(store, create_document(2))
    before = store._state
    final = {
        "type": "document.copy",
        "record": {"id": 101, "kind": "copy", "scope": SESSION},
        "source": {"id": 2, "at": "current"},
    }
    with pytest.raises(UnsupportedStorageOperation, match="DE27"):
        commit(store, entry(100), create_document(102, key="supported"), final)
    assert (
        store._state is before
        and store.entry(100) is None
        and store.document(102) is None
    )
    assert store.mint_id() == 3
    store.close()


def _foreign_point(kind, calls):
    class Hooks:
        def __eq__(self, other):
            calls.append("eq")
            return True

        def __ne__(self, other):
            calls.append("ne")
            return False

        def __repr__(self):
            calls.append("repr")
            return "<foreign point>"

        def __str__(self):
            calls.append("str")
            return "current"

        def __hash__(self):
            calls.append("hash")
            return 1

        def __int__(self):
            calls.append("int")
            return 1

        def __index__(self):
            calls.append("index")
            return 1

    class StringPoint(Hooks, str):
        pass

    class IntegerPoint(Hooks, int):
        pass

    if kind in {"eq-only", "repr-only"}:
        return _other_foreign_point(kind, calls)
    return {
        "foreign": Hooks,
        "str-subclass": lambda: StringPoint("current"),
        "int-subclass": lambda: IntegerPoint(1),
    }[kind]()


def _other_foreign_point(kind, calls):
    class EqualPoint:
        def __eq__(self, other):
            calls.append("eq")
            return True

        def __repr__(self):
            calls.append("repr")
            return "<equal point>"

    class ReprPoint:
        def __repr__(self):
            calls.append("repr")
            return "<repr point>"

    return {"eq-only": EqualPoint, "repr-only": ReprPoint}[kind]()


def _point_call(store, method, value):
    if method == "document":
        return store.document(2, value)
    if method == "find":
        return store.find_document(address(), value)
    if method == "query-point":
        query = DocumentQuery({"scope": SESSION, "at": value})
        return store.scan_documents(query, 1)
    return store.scan_documents(value, 1)


@pytest.mark.parametrize(
    "kind", ["foreign", "eq-only", "repr-only", "str-subclass", "int-subclass"]
)
@pytest.mark.parametrize("method", ["document", "find", "query-point", "raw-query"])
def test_document_point_ingress_rejects_foreign_without_any_hook(kind, method):
    store = root()
    commit(store, create_document(2))
    calls = []
    value = _foreign_point(kind, calls)
    calls.clear()  # Construction is outside the public call's evidence boundary.
    before = store._state
    try:
        with pytest.raises(ValueError) as failure:
            _point_call(store, method, value)
        assert not isinstance(failure.value, UnsupportedStorageOperation)
        assert calls == []
        assert store._state is before and not store._lock.locked()
    finally:
        store.close()


@pytest.mark.parametrize(
    "kind", ["foreign", "eq-only", "repr-only", "str-subclass", "int-subclass"]
)
@pytest.mark.parametrize("method", ["document", "find", "raw-query"])
def test_closed_document_ingress_refuses_before_foreign_admission(kind, method):
    store = root()
    store.close()
    calls = []
    value = _foreign_point(kind, calls)
    calls.clear()
    with pytest.raises(StorageClosedError):
        _point_call(store, method, value)
    assert calls == [] and not store._lock.locked()


@pytest.mark.parametrize("method", ["document", "find", "query-point"])
def test_document_point_exact_builtin_controls_keep_numeric_history_unsupported(method):
    store = root()
    commit(store, create_document(2))
    try:
        assert _point_call(store, method, "current") is not None
        with pytest.raises(
            UnsupportedStorageOperation, match="Numeric document history"
        ):
            _point_call(store, method, 1)
    finally:
        store.close()


@pytest.mark.parametrize("version", [0, -1, 1.5, 1])
def test_raw_document_content_version_is_separate_from_point_and_definition_domain(
    version,
):
    store = root()
    commit(store, create_document(2, version=version))
    assert store.document(2).to_tree()["version"] == version
    commit(store, change_document(2, {"retained": True}, version))
    assert store.document(2).to_tree()["version"] == version
    store.close()


_POINT_CHILD = r"""
import json,sys
from pipy_harness.native.durable import memory as m
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.storage import DocumentAddress
store = MemoryStorage()
print("ready", flush=True)
if sys.stdin.readline().strip() != "go":
    raise RuntimeError("Missing parent readiness acknowledgement")
class Point:
    def __ne__(self, other):
        print(json.dumps({"hook_entered":True,"lock_held":store._lock.locked(),"next_call":"mint_id"}),flush=True)
        store.mint_id()
        return False
try:
    if sys.argv[1] == "document":
        store.document(2, Point())
    else:
        store.find_document(DocumentAddress({"kind":"cache","scope":{"kind":"session"}}), Point())
except ValueError as error:
    print(json.dumps({"rejected":True,"error_type":type(error).__name__,"next_id":store.mint_id()}),flush=True)
else:
    print(json.dumps({"rejected":False}),flush=True)
finally:
    store.close()
"""


@pytest.mark.parametrize("method", ["document", "find"])
def test_document_point_reentry_child_is_bounded_and_never_runs_hook(tmp_path, method):
    import json
    import os
    import selectors
    import signal
    import subprocess
    import sys
    from pathlib import Path

    home = tmp_path / "home"
    home.mkdir()
    child = subprocess.Popen(
        [sys.executable, "-c", _POINT_CHILD, method],
        cwd=Path(__file__).resolve().parents[2],
        env={"PATH": os.defpath, "HOME": str(home)},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    # Ownership is established before a possible timeout/kill; no foreign PID/PGID.
    own_group = child.pid
    assert os.getpgid(child.pid) == own_group
    timed_out = False
    try:
        # Import/startup is fenced separately; the bad-point operation retains
        # its own2s deadline and never spends it on host-dependent import work.
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdout, selectors.EVENT_READ)
            assert ready.select(timeout=10), "Child startup readiness deadline"
            assert child.stdout.readline() == b"ready\n"
        try:
            out, err = child.communicate(input=b"go\n", timeout=2)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(own_group, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                out, err = child.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(own_group, signal.SIGKILL)
                out, err = child.communicate(timeout=2)
    finally:
        if child.poll() is None:
            os.killpg(own_group, signal.SIGKILL)
            child.communicate(timeout=2)
    assert child.poll() is not None
    # No running descendant is expected from this child; retain actual group proof.
    try:
        os.killpg(own_group, 0)
    except ProcessLookupError:
        group_exists = False
    else:
        group_exists = True
    evidence = {
        "method": method,
        "pid": child.pid,
        "pgid": own_group,
        "timed_out": timed_out,
        "returncode": child.returncode,
        "reaped": child.poll() is not None,
        "group_exists_after": group_exists,
        "stdout": out.decode(),
        "stderr": err.decode(),
    }
    (tmp_path / "point-child-evidence.json").write_text(
        json.dumps(evidence, indent=2) + "\n"
    )
    assert not group_exists
    assert not timed_out, evidence
    assert child.returncode == 0 and not err, evidence
    result = json.loads(out)
    assert result == {"rejected": True, "error_type": "RecordError", "next_id": 2}
