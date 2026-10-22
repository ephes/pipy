"""DE03c1: SqliteStorage over the Storage contract cases, plus SQLite-only tests.

``storage_contract_cases`` runs MemoryStorage's acceptance over Memory, SQLite
and SQLite reopened after every commit; this module collects those cases and
adds what only the SQLite backend has. Files are read or tampered with only
while no store is open in this process (closing any descriptor of a store file
drops this process's SQLite locks on it). Real SQLite failures come from SQLite
itself (``max_page_count``, an authorizer, a damaged page); the foreign writer
is a real child process with bounded deadlines.
"""

from __future__ import annotations

import ast
import inspect
import json
import random
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

pytest.register_assert_rewrite("storage_contract_cases")
import storage_contract_cases as contract  # noqa: E402
from sqlite_child_support import Child  # noqa: E402
from test_memory_storage import (  # noqa: E402
    address,
    commit,
    create_document,
    document_query,
    entry,
    ids,
    submission,
    task,
)
from test_sqlite_schema import store_in_strict_column  # noqa: E402

from pipy_harness.native.durable import sqlite, sqlite_schema  # noqa: E402
from pipy_harness.native.durable.codec import OwnedJson  # noqa: E402
from pipy_harness.native.durable.memory import MemoryStorage  # noqa: E402
from pipy_harness.native.durable.records import EntryRecord  # noqa: E402
from pipy_harness.native.durable.sqlite import (  # noqa: E402
    SqliteStorage,
    open_sqlite_storage,
)
from pipy_harness.native.durable.sqlite_schema import (  # noqa: E402
    SqliteRollbackError,
    StorageCorruption,
)
from pipy_harness.native.durable.storage import (  # noqa: E402
    ConversationQuery,
    EntryLookup,
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

globals().update(contract.cases())


def _as_storage(store: SqliteStorage) -> Storage:
    return store


def _store(tmp_path: Path) -> tuple[SqliteStorage, str]:
    path = str(tmp_path / "store.sqlite")
    store = open_sqlite_storage(path, mode="create")
    assert commit(store, {"type": "conversation", "value": {"id": 1}}) == 1
    return store, path


def _raw(path: str, *statements: tuple[str, tuple[Any, ...]]) -> list[Any]:
    """Run statements on a closed store with a plain connection; last rows."""

    connection = sqlite3.connect(path, isolation_level=None)
    try:
        rows: list[Any] = []
        for sql, params in statements:
            rows = connection.execute(sql, params).fetchall()
        return rows
    finally:
        connection.close()


def _integrity(path: str) -> None:
    assert _raw(path, ("PRAGMA integrity_check", ())) == [("ok",)]


def test_every_original_memory_test_is_a_contract_case():
    source = Path(contract.originals.__file__).read_text()
    found = [
        node.name
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]
    assert sorted(found) == sorted(contract.ORIGINALS)
    assert len(set(contract.ORIGINALS)) == len(contract.ORIGINALS)
    for names in (*contract.VARIANTS.values(), *contract.SPLIT.values()):
        assert all(callable(getattr(contract, name)) for name in names)
    assert set(contract.SPLIT) <= set(contract.DOCUMENT_BODIES)
    # DE03c2 ports these with its SQLite document views; none runs here yet.
    assert len(contract.DE03C2_PORTS) == 7
    assert not any(hasattr(contract, name) for name in contract.DE03C2_PORTS)


def test_signatures_are_memory_storage_signatures():
    names = [name for name in vars(MemoryStorage) if not name.startswith("_")]
    assert len(names) == 16
    for name in names:
        expected = inspect.signature(getattr(MemoryStorage, name))
        assert inspect.signature(getattr(SqliteStorage, name)) == expected, name


# Values ---------------------------------------------------------------------------

_SAMPLES: list[Any] = [
    None,
    True,
    False,
    0,
    -1,
    2**60,
    -(2**64),
    1.5,
    -0.0,
    5e-324,
    1.7976931348623157e308,
    1e16,
    "",
    "\ud800",
    "\udfff",
    "😀",
    "\U0001f600",
    '\x00\x1f"\\/\n\t \x7f',
    [],
    {},
    [[{}], {"": []}],
    {"b": 1, "a": 2, "\ud800": [None], "\U0001f600": -0.0},
    {"__proto__": {"x": [1, 1.0]}},
]


def _random_tree(rng: random.Random, depth: int) -> Any:
    pick = rng.randrange(9 if depth else 6)
    if pick == 0:
        return rng.choice([None, True, False])
    if pick == 1:
        return rng.choice([0, -7, 2**53, -(2**60), rng.randrange(-(10**9), 10**9)])
    if pick == 2:
        return rng.choice([-0.0, 0.5, 1e300, -2.5e-310, rng.uniform(-1e6, 1e6)])
    if pick in (3, 4, 5):
        size = rng.randrange(6)
        return "".join(
            chr(rng.choice([rng.randrange(0x20, 0x7F), rng.randrange(0, 0x110000)]))
            for _ in range(size)
        )
    if pick in (6, 7):
        return [_random_tree(rng, depth - 1) for _ in range(rng.randrange(4))]
    keys = {_random_tree(rng, 0) for _ in range(rng.randrange(4))}
    return {key: _random_tree(rng, depth - 1) for key in keys if type(key) is str}


def test_payload_text_is_compact_json_written_and_read_without_recursion():
    rng = random.Random(20261022)
    trees = [*_SAMPLES, *(_random_tree(rng, 6) for _ in range(400))]
    for tree in trees:
        text = json.dumps(
            tree, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        blob = sqlite._encode(tree)
        assert blob == text.encode("utf-8", "surrogatepass")
        assert OwnedJson(sqlite._decode(blob)) == OwnedJson(tree)
        assert OwnedJson(sqlite._decode(blob)) == OwnedJson(json.loads(text))


@pytest.mark.parametrize(
    "blob",
    [b"", b" ", b"{", b"[1,]", b"[,1]", b'{"a":1,}', b'{"a" 1}', b"{1:2}", b"[1 2]"]
    + [b"01", b"1.", b"-", b".5", b"tru", b'"\\x"', b'"open', b'"\x01"', b"[1]]"]
    + [b"]", b"{}{}", b'"\\ud800"x', b"\xff", b"[\xed\xa0]"],
)
def test_malformed_payload_text_is_refused_as_json_refuses_it(blob):
    with pytest.raises(ValueError):
        sqlite._decode(blob)
    with pytest.raises(ValueError):
        json.loads(blob.decode("utf-8", "surrogatepass"))


@pytest.mark.parametrize(
    "blob", [b"NaN", b"[Infinity]", b"-Infinity", b'{"a":1,"a":2}']
)
def test_payload_text_refuses_what_owned_values_cannot_hold(blob):
    with pytest.raises(ValueError):
        sqlite._decode(blob)


def test_payload_nesting_has_no_limit_across_reopen(tmp_path):
    tree: Any = {"leaf": "\ud800"}
    for _ in range(100_000):  # json.dumps and json.loads both stop far earlier
        tree = [tree]
    store, path = _store(tmp_path)
    commit(store, entry(2, data=tree))
    store.close()
    store = open_sqlite_storage(path, mode="open")
    try:
        value = store.entry(2).entry.to_tree()["data"]
        for _ in range(100_000):
            value = value[0]
        assert value == {"leaf": "\ud800"}
    finally:
        store.close()
    _integrity(path)


def test_identity_columns_hold_ascii_json_and_payloads_keep_spelling(tmp_path):
    store, path = _store(tmp_path)
    commit(
        store,
        task(2, kind="\U0001f600"),
        task(3, kind="😀"),
        submission(4, "\ud800"),
    )
    store.close()
    kinds = _raw(path, ("SELECT kind, record FROM tasks ORDER BY id", ()))
    assert [kind for kind, _ in kinds] == ['"\\ud83d\\ude00"'] * 2
    assert "\U0001f600".encode() in kinds[0][1]
    assert "😀".encode("utf-8", "surrogatepass") in kinds[1][1]
    request = _raw(path, ("SELECT request_id FROM submission_requests", ()))
    assert request == [('"\\ud800"',)]
    _integrity(path)


@pytest.mark.parametrize(
    "text", ["", "plain", "\u00e9t\u00e9", "\U0001f600", "\ud800", "x\udfffy", '"\\\n']
)
def test_identity_text_reads_back_unchanged_through_strict_decoding(tmp_path, text):
    """D1: identity columns are ASCII JSON, so strict UTF-8 decoding never fails."""

    store, path = _store(tmp_path)
    commit(store, task(2, kind=text), submission(3, text))
    store.close()
    store = open_sqlite_storage(path, mode="open")
    try:
        found = store.task(2)
        assert found is not None and found.to_tree()["kind"] == text
        assert ids(store.scan_tasks(TaskQuery({"kind": text}), 9)) == [2]
        assert ids(store.scan_tasks(TaskQuery({"kind": text + "x"}), 9)) == []
        winner = store.submission_by_request(1, text)
        assert winner is not None and winner.to_tree()["requestId"] == text
        assert ids(store.scan_submissions(SubmissionQuery({}), 9)) == [3]
    finally:
        store.close()
    _integrity(path)


def test_records_sequences_and_ids_persist_across_reopen(tmp_path):
    """Pi sqlite-storage.test.ts: "persists records, sequence allocation ..."."""

    store, path = _store(tmp_path)
    id = store.mint_id()
    assert commit(store, entry(id)) == 2
    store.close()
    store = open_sqlite_storage(path, mode="open")
    try:
        lookup = store.entry(id)
        assert (
            lookup.entry == EntryRecord(entry(id)["value"]) and lookup.commit_seq == 2
        )
        assert store.mint_id() == id + 1
        assert commit(store, task(store.mint_id())) == 3
    finally:
        store.close()
    _integrity(path)


# Statements -----------------------------------------------------------------------


def _plan(connection: sqlite3.Connection, sql: str, params: Any) -> str:
    rows = connection.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return "\n".join(row[3] for row in rows)


_INDEXED_SCANS = [
    ("conversation", {"ownerConversationId": 1}, "conversations_by_owner_conversation"),
    ("conversation", {"ownerTaskId": 1}, "conversations_by_owner_task"),
    ("task", {"status": "pending"}, "tasks_by_status"),
    ("task", {"conversationId": 1}, "tasks_by_conversation"),
    ("task", {"kind": "probe"}, "tasks_by_kind"),
    ("task", {"abortRequested": True}, "tasks_by_abort_requested"),
    ("task", {"background": False}, "tasks_by_background"),
    ("submission", {"conversationId": 1}, "submissions_by_conversation"),
    ("submission", {"status": "queued"}, "submissions_by_status"),
]


def test_statements_bind_only_plain_placeholders():
    """Python 3.12.3's sqlite3 warns about a numbered placeholder ("?1") bound
    from a sequence, an error under -W error; later releases accept it."""

    for module in (sqlite, sqlite_schema):
        tree = ast.parse(inspect.getsource(module))
        texts = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        assert not [text for text in texts if re.search(r"\?\d", text)], module


def test_reads_use_their_indexes_and_never_sort(tmp_path):
    store, path = _store(tmp_path)
    connection = store._database.connection()
    try:
        plans: list[tuple[str, str]] = []
        for kind, value, index in _INDEXED_SCANS:
            for order in ("ascending", "descending"):
                for after in (None, 5):
                    sql, params = sqlite._scan_sql(kind, value, order, after, 10)
                    plans.append((_plan(connection, sql, params), index))
        for kind in ("conversation", "task", "submission"):
            sql, params = sqlite._scan_sql(kind, {}, "descending", 5, 10)
            plans.append((_plan(connection, sql, params), "INTEGER PRIMARY KEY"))
            sql, params = sqlite._scan_sql(kind, {}, "ascending", None, 10)
            plans.append((_plan(connection, sql, params), "SCAN"))  # in rowid order
        for order in ("ascending", "descending"):
            for bounds in ((None, None), (2, None), (None, 9), (2, 9)):
                sql, params = sqlite._segment(1, bounds[0], bounds[1], order, 10)
                plans.append(
                    (_plan(connection, sql, params), "entries_by_conversation")
                )
        for sql, params in ((sqlite._HEAD, (1,)), (sqlite._HEAD_AT, (1, 9))):
            plans.append(
                (_plan(connection, sql, params), "entry_heads_by_conversation")
            )
        winner = _plan(connection, sqlite._WINNER, (1, '"x"'))
        plans.append((winner, "sqlite_autoindex_submission_requests_1"))
        # A rewrite drops its previous binding by primary key, never by a scan.
        drop = _plan(connection, sqlite._DROP_WINNER, (4, 4))
        assert "SCAN" not in drop, drop
        plans.append((drop, "sqlite_autoindex_submission_requests_1"))
        plans.append((_plan(connection, sqlite._CLAIMED, (4,)), "INTEGER PRIMARY KEY"))
        for kind in ("conversation", "entry", "task", "submission"):
            exact = _plan(connection, sqlite._SELECT_ID[kind], (1,))
            plans.append((exact, "INTEGER PRIMARY KEY"))
        for plan, index in plans:
            assert index in plan, plan
            assert "TEMP B-TREE" not in plan, plan
    finally:
        store.close()
    _integrity(path)


_FOREIGN_WRITER = r"""
import sqlite3
connection = sqlite3.connect(sys.argv[1], isolation_level=None, timeout=10)
say("ready")
while (line := command()) != "done":
    connection.execute("BEGIN IMMEDIATE")
    for sql, params in json.loads(line):
        connection.execute(sql, [bytes.fromhex(p["hex"]) if type(p) is dict else p for p in params])
    connection.execute("COMMIT")
    say("written")
connection.close()
say("closed")
"""


def _foreign_entry(id: int, conversation: int, seq: int, **extra: Any) -> list[Any]:
    value = entry(id, conversation, **extra)["value"]
    blob = {"hex": sqlite._encode(value).hex()}
    return [
        [sqlite._CLAIM, [id, "entry"]],
        [sqlite._INSERT["entry"], [*sqlite._columns("entry", value), seq, blob]],
    ]


def _unparented(id: int) -> list[Any]:
    blob = {"hex": sqlite._encode({"id": id}).hex()}
    return [["UPDATE conversations SET record = ? WHERE id = ?", [blob, id]]]


# read: the statement marker the write lands before, its count, the foreign write,
# the result inside one snapshot and the result of a fresh read afterwards.
_SNAPSHOT_READS = {
    "descending": (
        "FROM entries",
        2,
        _foreign_entry(30, 1, 9),
        [42, 10, 2],
        [42, 30, 10, 2],
    ),
    "ascending": (
        "FROM entries",
        2,
        _foreign_entry(43, 3, 9),
        [2, 10, 42],
        [2, 10, 42, 43],
    ),
    "head": ("head IS NOT NULL", 2, _foreign_entry(30, 1, 9, head=30), 2, 30),
    "entry": ("FROM conversations", 2, _unparented(3), 2, None),
}


def _snapshot_read(store: Any, read: str) -> Any:
    if read in ("descending", "ascending"):
        query = EntryQuery({"conversationId": 3, "order": read})
        return ids(store.scan_entries(query, 10))
    if read == "head":
        return store.find_latest_head_marker(3).id
    lookup = store.entry(2, conversation_id=5)
    return None if lookup is None else lookup.entry.id


@pytest.mark.parametrize("read", sorted(_SNAPSHOT_READS))
def test_multi_statement_reads_see_one_committed_state(tmp_path, read):
    marker, nth, writes, inside, fresh = _SNAPSHOT_READS[read]
    store, path = _store(tmp_path)
    commit(
        store,
        entry(2, 1, head=2),
        entry(10, 1),
        {
            "type": "conversation",
            "value": {"id": 3, "parent": {"conversationId": 1, "at": 40}},
        },
        entry(42, 3),
        {
            "type": "conversation",
            "value": {"id": 5, "parent": {"conversationId": 3, "at": 45}},
        },
        entry(46, 5),
    )
    connection = store._database.connection()
    seen: list[str] = []
    failures: list[BaseException] = []
    home = tmp_path / "home"
    home.mkdir()
    with Child(_FOREIGN_WRITER, path, home=home) as child:
        child.expect("ready")

        def trace(statement: str) -> None:
            if marker not in statement:
                return
            seen.append(statement)
            if len(seen) == nth:
                try:  # a foreign commit between two statements of one read
                    child.send(json.dumps(writes))
                    child.expect("written", timeout=30)
                except BaseException as error:  # noqa: BLE001 - asserted after the read
                    failures.append(error)

        connection.set_trace_callback(trace)
        try:
            result = _snapshot_read(store, read)
        finally:
            connection.set_trace_callback(None)
        assert not failures and len(seen) >= nth, (failures, seen)
        assert result == inside
        assert _snapshot_read(store, read) == fresh
        child.send("done")
        child.expect("closed")
        assert child.wait(timeout=30) == 0, child.evidence()
    store.close()
    _integrity(path)


# Fork ------------------------------------------------------------------------------

# A writer thread holds the instance lock, paused at the commit seam, while the
# child forks. The grandchild calls every public method: each must refuse with
# StorageForkedError (close returns) without waiting for the inherited lock that
# no thread of its own will ever release. Its parent reaps it against a deadline,
# then lets the writer commit.
_FORK_UNDER_CONTENTION = r"""
import threading, time, warnings
from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.storage import (
    ConversationQuery, DocumentAddress, DocumentQuery, EntryQuery, StorageWrite,
    SubmissionQuery, TaskQuery,
)
store = sqlite.open_sqlite_storage(sys.argv[1], mode="open")
deadline = float(sys.argv[2])
held, release = threading.Event(), threading.Event()

def pause(connection):
    held.set()
    release.wait(deadline)

sqlite._before_commit = pause
write = StorageWrite({"type": "entry", "value": {"id": 7, "conversationId": 1, "kind": ""}})
committed = {}

def writer():
    try:
        committed["seq"] = int(store.commit((write,)))
    except BaseException as error:
        committed["error"] = repr(error)

thread = threading.Thread(target=writer)
thread.start()
assert held.wait(deadline) and store._lock.locked()
session = {"kind": "session"}
calls = {
    "commit": lambda: store.commit((write,)),
    "mint_id": lambda: store.mint_id(),
    "conversation": lambda: store.conversation(1),
    "entry": lambda: store.entry(2),
    "visible entry": lambda: store.entry(2, conversation_id=1),
    "find_latest_head_marker": lambda: store.find_latest_head_marker(1),
    "scan_entries": lambda: store.scan_entries(EntryQuery({"conversationId": 1}), 9),
    "scan_conversations": lambda: store.scan_conversations(ConversationQuery({}), 9),
    "task": lambda: store.task(3),
    "scan_tasks": lambda: store.scan_tasks(TaskQuery({}), 9),
    "submission": lambda: store.submission(4),
    "scan_submissions": lambda: store.scan_submissions(SubmissionQuery({}), 9),
    "submission_by_request": lambda: store.submission_by_request(1, "x"),
    "document": lambda: store.document(5),
    "find_document": lambda: store.find_document(
        DocumentAddress({"scope": session, "kind": "cache"})
    ),
    "scan_documents": lambda: store.scan_documents(
        DocumentQuery({"scope": session, "at": "current"}), 9
    ),
    "close": lambda: store.close(),
    "close again": lambda: store.close(),
}
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)  # fork() with threads
    pid = os.fork()
if pid == 0:
    started = time.monotonic()
    outcomes = {}
    for name, call in calls.items():
        try:
            outcomes[name] = repr(call())
        except ownership.StorageForkedError as error:
            outcomes[name] = "forked: " + str(error)
        except BaseException as error:
            outcomes[name] = type(error).__name__
    say(
        "grandchild",
        outcomes=outcomes,
        locked=store._lock.locked(),
        seconds=time.monotonic() - started,
    )
    os._exit(0)
limit = time.monotonic() + deadline
while (status := os.waitpid(pid, os.WNOHANG))[0] == 0:
    if time.monotonic() >= limit:
        os.kill(pid, 9)
        status = os.waitpid(pid, 0)
        break
    time.sleep(0.01)
release.set()
thread.join(deadline)
say(
    "reaped",
    status=status[1],
    committed=committed,
    writer_alive=thread.is_alive(),
    entry=store.entry(7).commit_seq,
)
store.close()
say("closed")
"""


def test_a_forked_child_never_waits_for_the_lock_another_thread_held(tmp_path):
    store, path = _store(tmp_path)
    commit(store, entry(2, head=2), task(3), submission(4))
    store.close()
    home = tmp_path / "home"
    home.mkdir()
    with Child(_FORK_UNDER_CONTENTION, path, "20", home=home) as child:
        grandchild = child.expect("grandchild", timeout=60)
        reaped = child.expect("reaped", timeout=60)
        child.expect("closed", timeout=60)
        assert child.wait(timeout=30) == 0, child.evidence()
        child.wait_stdout_eof()
    assert grandchild["pid"] != reaped["pid"]
    forked = "forked: A durable store owner cannot be used after fork()"
    assert grandchild["outcomes"] == {
        **{name: forked for name in grandchild["outcomes"]},
        "close": "None",
        "close again": "None",
    }
    assert len(grandchild["outcomes"]) == 18
    assert grandchild["locked"] is True  # it really forked while the lock was held
    assert grandchild["seconds"] < 20
    assert reaped["status"] == 0  # exited by itself, before the deadline
    assert reaped["committed"] == {"seq": 3} and reaped["writer_alive"] is False
    assert reaped["entry"] == 3  # the parent's paused commit landed
    store = open_sqlite_storage(path, mode="open")
    try:
        assert ids(store.scan_entries(EntryQuery({"conversationId": 1}), 9)) == [7, 2]
    finally:
        store.close()
    _integrity(path)


# Damage ---------------------------------------------------------------------------

# Every read method, by name; each damage below lists the reads that return or
# walk the damaged row.
_READS = {
    "conversation": lambda s: s.conversation(1),
    "conversation-scan": lambda s: s.scan_conversations(ConversationQuery({}), 9),
    "entry": lambda s: s.entry(2),
    "visible-entry": lambda s: s.entry(2, conversation_id=1),
    "head-marker": lambda s: s.find_latest_head_marker(1),
    "entry-scan": lambda s: s.scan_entries(EntryQuery({"conversationId": 1}), 9),
    "entry-scan-ascending": lambda s: s.scan_entries(
        EntryQuery({"conversationId": 1, "order": "ascending"}), 9
    ),
    "task": lambda s: s.task(3),
    "task-scan": lambda s: s.scan_tasks(TaskQuery({}), 9),
    "task-scan-by-conversation": lambda s: s.scan_tasks(
        TaskQuery({"conversationId": 1}), 9
    ),
    "task-scan-by-kind": lambda s: s.scan_tasks(TaskQuery({"kind": ""}), 9),
    "task-scan-by-status": lambda s: s.scan_tasks(TaskQuery({"status": "pending"}), 9),
    "task-scan-by-abort": lambda s: s.scan_tasks(
        TaskQuery({"abortRequested": False}), 9
    ),
    "task-scan-by-background": lambda s: s.scan_tasks(
        TaskQuery({"background": False}), 9
    ),
    "submission": lambda s: s.submission(4),
    "submission-scan": lambda s: s.scan_submissions(SubmissionQuery({}), 9),
    "submission-scan-by-conversation": lambda s: s.scan_submissions(
        SubmissionQuery({"conversationId": 1}), 9
    ),
    "submission-scan-by-status": lambda s: s.scan_submissions(
        SubmissionQuery({"status": "queued"}), 9
    ),
    "request-winner": lambda s: s.submission_by_request(1, "x"),
    "foreign-winner": lambda s: s.submission_by_request(1, "y"),
    "commit-claim": lambda s: s.commit((StorageWrite(task(3)),)),
}
_CONVERSATION_READS = (
    "conversation",
    "conversation-scan",
    "visible-entry",
    "head-marker",
    "entry-scan",
    "entry-scan-ascending",
)
_ENTRY_READS = (
    "entry",
    "visible-entry",
    "head-marker",
    "entry-scan",
    "entry-scan-ascending",
)
_TASK_READS = ("task", "task-scan", "task-scan-by-conversation")
_SUBMISSION_READS = ("submission", "submission-scan", "request-winner")
_UNCHECKED = "PRAGMA ignore_check_constraints = ON"  # tampers past the CHECKs
_STORED_TEXT = ("\ufffd", "\\xff", '"other"', '"y"', '{"id"', "nothing")
_NOT_UTF8 = "CAST(x'ff' AS TEXT)"
# A damaged row: the tamper, the reads that must refuse it, a read that must not,
# and the refusal's message.
_DAMAGE: dict[str, tuple[str, tuple[Any, ...], tuple[str, ...], str, str]] = {
    "conversation-not-json": (
        "UPDATE conversations SET record = ? WHERE id = 1",
        (b"[",),
        _CONVERSATION_READS,
        "task",
        r"unreadable conversation record \(ID 1\)",
    ),
    "conversation-owner-contradicts": (
        "UPDATE conversations SET owner_task_id = 7 WHERE id = 1",
        (),
        _CONVERSATION_READS,
        "task",
        r"conversation record \(ID 1\) that its index columns contradict",
    ),
    "entry-not-json": (
        "UPDATE entries SET record = ? WHERE id = 2",
        (b"{",),
        _ENTRY_READS,
        "task",
        r"unreadable entry record \(ID 2\)",
    ),
    "entry-not-utf8": (
        "UPDATE entries SET record = ? WHERE id = 2",
        (b'"\xff"',),
        _ENTRY_READS,
        "task",
        r"unreadable entry record \(ID 2\)",
    ),
    "entry-not-a-record": (
        "UPDATE entries SET record = ? WHERE id = 2",
        (b'{"id":2}',),
        _ENTRY_READS,
        "task",
        r"unreadable entry record \(ID 2\)",
    ),
    "entry-head-contradicts": (
        "UPDATE entries SET head = 3 WHERE id = 2",
        (),
        _ENTRY_READS,
        "task",
        r"entry record \(ID 2\) that its index columns contradict",
    ),
    "commit-seq-zero": (
        "UPDATE entries SET commit_seq = 0 WHERE id = 2",
        (),
        _ENTRY_READS,
        "task",
        r"entry \(ID 2\) without a commit sequence",
    ),
    "commit-seq-unsafe": (
        f"UPDATE entries SET commit_seq = {2**53} WHERE id = 2",
        (),
        _ENTRY_READS,
        "task",
        r"entry \(ID 2\) without a commit sequence",
    ),
    "task-kind-not-utf8": (
        f"UPDATE tasks SET kind = {_NOT_UTF8} WHERE id = 3",
        (),
        _TASK_READS,
        "submission",
        r"task record \(ID 3\) whose kind column is not UTF-8",
    ),
    "task-status-not-utf8": (
        f"UPDATE tasks SET status = {_NOT_UTF8} WHERE id = 3",
        (),
        _TASK_READS,
        "submission",
        r"task record \(ID 3\) whose status column is not UTF-8",
    ),
    "task-kind-contradicts": (
        "UPDATE tasks SET kind = '\"other\"' WHERE id = 3",
        (),
        _TASK_READS,
        "submission",
        r"task record \(ID 3\) that its index columns contradict",
    ),
    "task-not-a-record": (
        "UPDATE tasks SET record = ? WHERE id = 3",
        (b'{"id":3}',),
        _TASK_READS,
        "submission",
        r"unreadable task record \(ID 3\)",
    ),
    "submission-request-not-utf8": (
        f"UPDATE submissions SET request_id = {_NOT_UTF8} WHERE id = 4",
        (),
        _SUBMISSION_READS,
        "task",
        r"submission record \(ID 4\) whose request_id column is not UTF-8",
    ),
    "submission-status-not-utf8": (
        f"UPDATE submissions SET status = {_NOT_UTF8} WHERE id = 4",
        (),
        _SUBMISSION_READS,
        "task",
        r"submission record \(ID 4\) whose status column is not UTF-8",
    ),
    "submission-not-json": (
        "UPDATE submissions SET record = ? WHERE id = 4",
        (b"{",),
        _SUBMISSION_READS,
        "task",
        r"unreadable submission record \(ID 4\)",
    ),
    "winner-missing": (
        "UPDATE submission_requests SET submission_id = 999",
        (),
        ("request-winner",),
        "submission",
        "names a missing submission as a request winner",
    ),
    "winner-foreign": (
        "INSERT INTO submission_requests VALUES (1, '\"y\"', 4)",
        (),
        ("foreign-winner",),
        "request-winner",
        "names submission 4 as the winner of another request",
    ),
    "claim-not-utf8": (
        f"UPDATE record_ids SET record_type = {_NOT_UTF8} WHERE id = 3",
        (),
        ("commit-claim",),
        "task",
        r"ID claim \(3\) whose record_type column is not UTF-8",
    ),
    "claim-unknown": (
        "UPDATE record_ids SET record_type = 'nothing' WHERE id = 3",
        (),
        ("commit-claim",),
        "task",
        r"unreadable ID claim \(3\)",
    ),
}


@pytest.mark.parametrize(
    "damage,read",
    [
        (damage, read)
        for damage, (_, _, reads, _, _) in sorted(_DAMAGE.items())
        for read in reads
    ],
)
def test_damaged_rows_are_storage_corruption_on_every_read(tmp_path, damage, read):
    sql, params, _, healthy, message = _DAMAGE[damage]
    store, path = _store(tmp_path)
    commit(store, entry(2, head=2), task(3), submission(4))
    store.close()
    _raw(path, (_UNCHECKED, ()), (sql, params))
    store = open_sqlite_storage(path, mode="open")
    try:
        with pytest.raises(StorageCorruption, match=message) as damaged:
            _READS[read](store)
        assert not isinstance(damaged.value, StorageRequestError)
        # The message names the row, never its stored text.
        assert not any(text in str(damaged.value) for text in _STORED_TEXT)
        assert _READS[healthy](store) is not None  # other rows still read
    finally:
        store.close()


# A value of another storage class than its column allows, stored through SQL
# alone (``store_in_strict_column``): table, column, row, value and its class,
# the reads that reach the row (each must refuse it) and the reads that cannot:
# an equality filter on that very column, which SQLite never finds equal to a
# value of that class, so the row is not in its result (a filter on an INTEGER
# column does find a numerically equal REAL, and refuses it).
_TASK_READS_ALL = (
    "task",
    "task-scan",
    "task-scan-by-conversation",
    "task-scan-by-kind",
    "task-scan-by-status",
    "task-scan-by-abort",
    "task-scan-by-background",
)
_SUBMISSION_READS_ALL = (
    "submission",
    "submission-scan",
    "submission-scan-by-conversation",
    "submission-scan-by-status",
    "request-winner",
)
_ENTRY_BY_CONVERSATION = ("head-marker", "entry-scan", "entry-scan-ascending")
_HOLDER = {
    "conversations": (r"conversation record \(ID 1\)", 1, "task"),
    "entries": (r"entry record \(ID 2\)", 2, "task"),
    "tasks": (r"task record \(ID 3\)", 3, "submission"),
    "submissions": (r"submission record \(ID 4\)", 4, "task"),
    "submission_requests": ("request binding", 4, "task"),
    "record_ids": (r"ID claim \(3\)", 3, "submission"),
}


def _blind(
    reads: tuple[str, ...], *blind: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return tuple(read for read in reads if read not in blind), blind


_CLASSES: dict[str, tuple[str, str, str, str, tuple[str, ...], tuple[str, ...]]] = {
    # TEXT columns (kind and request_id are the identity columns)
    "task-kind-blob": (
        "tasks",
        "kind",
        "CAST(kind AS BLOB)",
        "blob",
        *_blind(_TASK_READS_ALL, "task-scan-by-kind"),
    ),
    "task-kind-integer": (
        "tasks",
        "kind",
        "5",
        "integer",
        *_blind(_TASK_READS_ALL, "task-scan-by-kind"),
    ),
    "task-kind-real": (
        "tasks",
        "kind",
        "1.5",
        "real",
        *_blind(_TASK_READS_ALL, "task-scan-by-kind"),
    ),
    "task-status-blob": (
        "tasks",
        "status",
        "CAST(status AS BLOB)",
        "blob",
        *_blind(_TASK_READS_ALL, "task-scan-by-status"),
    ),
    "task-status-integer": (
        "tasks",
        "status",
        "1",
        "integer",
        *_blind(_TASK_READS_ALL, "task-scan-by-status"),
    ),
    "task-status-null": (
        "tasks",
        "status",
        "NULL",
        "null",
        *_blind(_TASK_READS_ALL, "task-scan-by-status"),
    ),
    "submission-request-blob": (
        "submissions",
        "request_id",
        "CAST(request_id AS BLOB)",
        "blob",
        _SUBMISSION_READS_ALL,
        (),
    ),
    "submission-request-real": (
        "submissions",
        "request_id",
        "2.5",
        "real",
        _SUBMISSION_READS_ALL,
        (),
    ),
    "submission-status-blob": (
        "submissions",
        "status",
        "CAST(status AS BLOB)",
        "blob",
        *_blind(_SUBMISSION_READS_ALL, "submission-scan-by-status"),
    ),
    "submission-status-integer": (
        "submissions",
        "status",
        "7",
        "integer",
        *_blind(_SUBMISSION_READS_ALL, "submission-scan-by-status"),
    ),
    "binding-request-blob": (
        "submission_requests",
        "request_id",
        "CAST(request_id AS BLOB)",
        "blob",
        (),
        ("request-winner",),
    ),
    "binding-request-integer": (
        "submission_requests",
        "request_id",
        "5",
        "integer",
        (),
        ("request-winner",),
    ),
    "claim-blob": (
        "record_ids",
        "record_type",
        "CAST(record_type AS BLOB)",
        "blob",
        ("commit-claim",),
        (),
    ),
    "claim-integer": (
        "record_ids",
        "record_type",
        "1",
        "integer",
        ("commit-claim",),
        (),
    ),
    "claim-null": ("record_ids", "record_type", "NULL", "null", ("commit-claim",), ()),
    # INTEGER columns
    "conversation-owner-conversation-text": (
        "conversations",
        "owner_conversation_id",
        _NOT_UTF8,
        "text",
        _CONVERSATION_READS,
        (),
    ),
    "conversation-owner-conversation-real": (
        "conversations",
        "owner_conversation_id",
        "1.0",
        "real",
        _CONVERSATION_READS,
        (),
    ),
    "conversation-owner-task-text": (
        "conversations",
        "owner_task_id",
        "'x'",
        "text",
        _CONVERSATION_READS,
        (),
    ),
    "conversation-owner-task-blob": (
        "conversations",
        "owner_task_id",
        "x'01'",
        "blob",
        _CONVERSATION_READS,
        (),
    ),
    "entry-conversation-text": (
        "entries",
        "conversation_id",
        _NOT_UTF8,
        "text",
        ("entry", "visible-entry"),
        _ENTRY_BY_CONVERSATION,
    ),
    "entry-conversation-real": (
        "entries",
        "conversation_id",
        "1.0",
        "real",
        _ENTRY_READS,
        (),
    ),
    "entry-head-text": ("entries", "head", _NOT_UTF8, "text", _ENTRY_READS, ()),
    "entry-head-real": ("entries", "head", "2.0", "real", _ENTRY_READS, ()),
    "entry-commit-seq-text": (
        "entries",
        "commit_seq",
        _NOT_UTF8,
        "text",
        _ENTRY_READS,
        (),
    ),
    "entry-commit-seq-real": ("entries", "commit_seq", "2.0", "real", _ENTRY_READS, ()),
    "task-conversation-text": (
        "tasks",
        "conversation_id",
        _NOT_UTF8,
        "text",
        *_blind(_TASK_READS_ALL, "task-scan-by-conversation"),
    ),
    "task-conversation-real": (
        "tasks",
        "conversation_id",
        "1.0",
        "real",
        _TASK_READS_ALL,
        (),
    ),
    "task-abort-requested-text": (
        "tasks",
        "abort_requested",
        _NOT_UTF8,
        "text",
        *_blind(_TASK_READS_ALL, "task-scan-by-abort"),
    ),
    "task-abort-requested-real": (
        "tasks",
        "abort_requested",
        "0.0",
        "real",
        _TASK_READS_ALL,
        (),
    ),
    "task-background-blob": (
        "tasks",
        "background",
        "x'00'",
        "blob",
        *_blind(_TASK_READS_ALL, "task-scan-by-background"),
    ),
    "task-background-real": ("tasks", "background", "0.0", "real", _TASK_READS_ALL, ()),
    "submission-conversation-text": (
        "submissions",
        "conversation_id",
        _NOT_UTF8,
        "text",
        *_blind(_SUBMISSION_READS_ALL, "submission-scan-by-conversation"),
    ),
    "submission-conversation-real": (
        "submissions",
        "conversation_id",
        "1.0",
        "real",
        _SUBMISSION_READS_ALL,
        (),
    ),
    "binding-conversation-real": (
        "submission_requests",
        "conversation_id",
        "1.0",
        "real",
        ("request-winner",),
        (),
    ),
    "binding-conversation-text": (
        "submission_requests",
        "conversation_id",
        "'x'",
        "text",
        (),
        ("request-winner",),
    ),
    "binding-submission-real": (
        "submission_requests",
        "submission_id",
        "4.0",
        "real",
        ("request-winner",),
        (),
    ),
    "binding-submission-text": (
        "submission_requests",
        "submission_id",
        "'4'",
        "text",
        ("request-winner",),
        (),
    ),
    # BLOB columns (the payloads)
    "conversation-record-text": (
        "conversations",
        "record",
        "CAST(record AS TEXT)",
        "text",
        _CONVERSATION_READS,
        (),
    ),
    "conversation-record-not-utf8": (
        "conversations",
        "record",
        _NOT_UTF8,
        "text",
        _CONVERSATION_READS,
        (),
    ),
    "entry-record-text": (
        "entries",
        "record",
        "CAST(record AS TEXT)",
        "text",
        _ENTRY_READS,
        (),
    ),
    "entry-record-integer": ("entries", "record", "5", "integer", _ENTRY_READS, ()),
    "task-record-text": ("tasks", "record", _NOT_UTF8, "text", _TASK_READS_ALL, ()),
    "task-record-real": ("tasks", "record", "1.5", "real", _TASK_READS_ALL, ()),
    "submission-record-text": (
        "submissions",
        "record",
        "CAST(record AS TEXT)",
        "text",
        _SUBMISSION_READS_ALL,
        (),
    ),
    "submission-record-integer": (
        "submissions",
        "record",
        "5",
        "integer",
        _SUBMISSION_READS_ALL,
        (),
    ),
}


def _sees(result: Any, id: int) -> bool:
    """Whether a read's result holds the row with ``id``."""

    if result is None:
        return False
    if isinstance(result, Page):
        return id in ids(result)
    if isinstance(result, EntryLookup):
        return result.entry.id == id
    return result.id == id


@pytest.mark.parametrize(
    "damage,read",
    [
        (damage, read)
        for damage, (*_, reaches, blind) in sorted(_CLASSES.items())
        for read in (*reaches, *blind)
    ],
)
def test_values_of_another_storage_class_are_refused_by_every_read(
    tmp_path, damage, read
):
    """Every read that reaches the row refuses it before decoding anything; a
    read that cannot reach it (an equality filter on that column) returns
    without it, and never the row with a value of the wrong class."""

    table, column, value, stored, reaches, _ = _CLASSES[damage]
    holder, row, healthy = _HOLDER[table]
    store, path = _store(tmp_path)
    commit(store, entry(2, head=2), task(3), submission(4))
    store.close()
    # The fixture's one request binding has no ID of its own: all of its rows.
    where = "1" if table == "submission_requests" else f"id = {row}"
    assert store_in_strict_column(path, table, column, value, where) == [stored]
    store = open_sqlite_storage(path, mode="open")
    try:
        if read in reaches:
            message = rf"{holder} whose {column} column holds {stored} where the schema"
            with pytest.raises(StorageCorruption, match=message) as damaged:
                _READS[read](store)
            assert not any(text in str(damaged.value) for text in _STORED_TEXT)
        else:
            assert not _sees(_READS[read](store), row)
        assert _READS[healthy](store) is not None
    finally:
        store.close()


def test_read_classes_are_the_schema_declared_types(tmp_path):
    """Every column a read checks allows exactly its declared type's class, and
    ``null`` only where the column is nullable (``PRAGMA table_xinfo``)."""

    store, path = _store(tmp_path)
    store.close()

    def declared(
        connection: sqlite3.Connection, table: str
    ) -> dict[str, frozenset[str]]:
        found = {}
        for _, name, kind, notnull, _, pk, _ in connection.execute(
            f"PRAGMA table_xinfo({table})"
        ):
            nullable = not notnull and not pk
            found[name] = frozenset({kind.lower(), *(("null",) if nullable else ())})
        return found

    with sqlite3.connect(path) as connection:
        for kind, columns in sqlite._COLUMNS.items():
            table = declared(connection, sqlite._TABLES[kind])
            assert [name for name, _ in columns] == list(table), kind
            assert dict(columns) == table, kind
        bindings = declared(connection, "submission_requests")
        assert dict(sqlite._BINDING) == bindings
        claims = declared(connection, "record_ids")
        assert dict(sqlite._CLAIM_TYPE) == {"record_type": claims["record_type"]}
        metadata = declared(connection, "durable_metadata")
        assert dict(sqlite_schema.METADATA_COLUMNS) == {
            name: metadata[name] for name in ("next_id", "next_seq")
        }
        version = declared(connection, "durable_schema")
        assert dict(sqlite_schema.VERSION_COLUMNS) == {"version": version["version"]}
    connection.close()


# A damaged b-tree page of each table, reached by each kind of read and the commit.
_PAGES = {
    "exact-entry": ("entries", lambda s: s.entry(2)),
    "visible-entry": ("entries", lambda s: s.entry(2, conversation_id=1)),
    "head-marker": ("entries", lambda s: s.find_latest_head_marker(1)),
    "entry-scan": (
        "entries",
        lambda s: s.scan_entries(EntryQuery({"conversationId": 1}), 9),
    ),
    "exact-task": ("tasks", lambda s: s.task(3)),
    "task-scan": ("tasks", lambda s: s.scan_tasks(TaskQuery({}), 9)),
    "request-winner": (
        "submission_requests",
        lambda s: s.submission_by_request(1, "x"),
    ),
    "commit-claims": ("record_ids", lambda s: s.commit((StorageWrite(entry(5)),))),
}


@pytest.mark.parametrize("read", sorted(_PAGES))
def test_damage_sqlite_reports_is_storage_corruption_on_every_path(tmp_path, read):
    table, call = _PAGES[read]
    store, path = _store(tmp_path)
    commit(store, entry(2, head=2), task(3), submission(4))
    store.close()
    [(root, size)] = _raw(
        path,
        (
            "SELECT rootpage, (SELECT page_size FROM pragma_page_size())"
            " FROM sqlite_schema WHERE name = ?",
            (table,),
        ),
    )
    with open(path, "r+b") as stream:  # no store is open in this process
        stream.seek((root - 1) * size)
        stream.write(b"\x01")  # not a b-tree page type
    store = open_sqlite_storage(path, mode="open")
    try:
        with pytest.raises(StorageCorruption) as damaged:
            call(store)
        cause = damaged.value.__cause__
        assert isinstance(cause, sqlite3.DatabaseError)
        assert cause.sqlite_errorcode & 0xFF == sqlite3.SQLITE_CORRUPT
        assert store.conversation(1).id == 1
    finally:
        store.close()


# Commit failures ------------------------------------------------------------------


def _deny(operation: str) -> Any:
    def authorize(action: int, first: Any, *_: Any) -> int:
        refused = action == sqlite3.SQLITE_TRANSACTION and first == operation
        return sqlite3.SQLITE_DENY if refused else sqlite3.SQLITE_OK

    return authorize


def test_a_commit_that_sqlite_rolled_back_raises_its_own_error(tmp_path):
    store, path = _store(tmp_path)
    commit(store, entry(2))
    connection = store._database.connection()
    limit = connection.execute("PRAGMA max_page_count").fetchone()[0]
    pages = connection.execute("PRAGMA page_count").fetchone()[0]
    connection.execute(f"PRAGMA max_page_count = {pages}")
    try:
        with pytest.raises(sqlite3.OperationalError) as full:
            commit(store, entry(3, data={"text": "x" * 200_000}), task(4))
        assert type(full.value) is sqlite3.OperationalError
        assert full.value.sqlite_errorcode == sqlite3.SQLITE_FULL
        # SQLite already rolled the whole transaction back: nothing to roll back.
        assert not connection.in_transaction
    finally:
        connection.execute(f"PRAGMA max_page_count = {limit}")
    assert store.entry(3) is None and store.task(4) is None
    assert store.mint_id() == 3 and commit(store, entry(3)) == 3
    store.close()
    assert _raw(path, ("SELECT count(*) FROM record_ids", ())) == [(3,)]
    _integrity(path)


def test_a_failed_rollback_poisons_the_instance(tmp_path, monkeypatch):
    store, path = _store(tmp_path)

    def late(connection: sqlite3.Connection) -> None:
        raise RuntimeError("late failure")

    monkeypatch.setattr(sqlite, "_before_commit", late)
    connection = store._database.connection()
    connection.set_authorizer(_deny("ROLLBACK"))
    with pytest.raises(SqliteRollbackError) as failed:
        commit(store, entry(2))
    connection.set_authorizer(None)
    assert str(failed.value.error) == "late failure"
    assert failed.value.rollback_error.sqlite_errorcode == sqlite3.SQLITE_AUTH
    # Still inside the failed transaction: no read may see its rows.
    assert connection.in_transaction
    for operation in (store.mint_id, lambda: store.entry(2), lambda: store.commit(())):
        with pytest.raises(StorageClosedError) as poisoned:
            operation()
        assert poisoned.value.__cause__ is failed.value
    store.close()
    monkeypatch.undo()
    store = open_sqlite_storage(path, mode="open")
    assert store.entry(2) is None and store.commit(()) == 2
    store.close()
    _integrity(path)


def test_a_raising_commit_poisons_the_instance(tmp_path):
    """D4: after COMMIT raised, only a reopen can tell whether it committed."""

    store, path = _store(tmp_path)
    connection = store._database.connection()
    connection.set_authorizer(_deny("COMMIT"))
    with pytest.raises(sqlite3.DatabaseError) as failed:
        commit(store, entry(2))
    connection.set_authorizer(None)
    assert failed.value.sqlite_errorcode == sqlite3.SQLITE_AUTH
    assert not connection.in_transaction
    for operation in (store.mint_id, lambda: store.entry(2), lambda: store.commit(())):
        with pytest.raises(StorageClosedError) as poisoned:
            operation()
        assert poisoned.value.__cause__ is failed.value
    store.close()
    store.close()
    store = open_sqlite_storage(path, mode="open")  # the owner lock was released
    assert store.entry(2) is None and store.commit(()) == 2
    store.close()
    _integrity(path)


# The interim and the opener -------------------------------------------------------


def test_documents_refuse_as_an_explicit_interim_owned_by_de03c2(tmp_path):
    store, path = _store(tmp_path)
    delta = {
        "type": "document.change",
        "id": 3,
        "content": {"kind": "delta", "version": 1, "ops": []},
    }
    try:
        for call in (
            lambda: store.document(2),
            lambda: store.find_document(address()),
            lambda: store.scan_documents(document_query(), 1),
            lambda: commit(store, entry(2), create_document(3)),
            lambda: commit(store, {"type": "document.retire", "id": 3}),
        ):
            with pytest.raises(UnsupportedStorageOperation, match="DE03c2") as refused:
                call()
            assert type(refused.value) is UnsupportedStorageOperation
        with pytest.raises(UnsupportedStorageOperation, match="DE04/DE27"):
            commit(store, entry(2), delta)
        assert store.entry(2) is None and store.mint_id() == 2
    finally:
        store.close()
    with pytest.raises(StorageClosedError):
        store.document(2)
    _integrity(path)


def test_a_failing_open_closes_the_database_and_frees_the_lock(tmp_path, monkeypatch):
    path = str(tmp_path / "store.sqlite")
    open_sqlite_storage(path, mode="create").close()

    def missing(connection: sqlite3.Connection) -> Any:
        raise StorageCorruption("Durable SQLite metadata is missing")

    monkeypatch.setattr(sqlite, "read_metadata", missing)
    with pytest.raises(StorageCorruption, match="metadata is missing"):
        open_sqlite_storage(path, mode="open")
    monkeypatch.undo()
    store = open_sqlite_storage(path, mode="open")
    assert (
        store.commit((StorageWrite({"type": "conversation", "value": {"id": 1}}),)) == 1
    )
    store.close()
    _integrity(path)
