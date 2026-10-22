"""DE03c2 raw bases, persisted revisions and real transaction failure cuts."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import stat
from pathlib import Path

import pytest
from sqlite_child_support import Child
from test_memory_storage import (
    address,
    change_document,
    commit,
    create_document,
    document_query,
    entry,
)
from test_sqlite_schema import store_in_strict_column
from test_sqlite_storage import _deny, _integrity, _raw, _store

from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.sqlite_schema import StorageCorruption
from pipy_harness.native.durable.storage import StorageClosedError


@pytest.mark.parametrize("version", [0, -1, 1.5, -0.0, 2**100, -(2**100)])
def test_document_versions_and_values_are_lossless_blobs_across_reopen(
    tmp_path, version
):
    store, path = _store(tmp_path)
    value = {"zero": -0.0, "large": 2**100, "\ud800": ["\ud83d\ude00", "😀"]}
    commit(store, create_document(2, value=value, version=version))
    expected = store.document(2)
    store.close()
    rows = _raw(
        path,
        (
            "SELECT typeof(version), typeof(content), version FROM document_revisions",
            (),
        ),
    )
    assert rows == [("blob", "blob", sqlite._encode(version))]
    store = open_sqlite_storage(path, mode="open")
    assert store.document(2) == expected
    assert OwnedJson(store.document(2).to_tree()["value"]) == OwnedJson(value)
    commit(store, change_document(2, {"changed": True}, version))
    assert OwnedJson(store.document(2).to_tree()["version"]) == OwnedJson(version)
    store.close()
    _integrity(path)


@pytest.mark.parametrize("rewindable", [False, True])
def test_base_revisions_follow_retention_and_retirement(tmp_path, rewindable):
    store, path = _store(tmp_path)
    policy = (
        {
            "history": "rewindable",
            "fork": "initial",
            "scope": {"kind": "conversation", "conversationId": 1},
        }
        if rewindable
        else {}
    )
    commit(store, create_document(2, **policy))
    for seq in range(3, 8):
        assert commit(store, change_document(2, {"seq": seq}, seq)) == seq
    assert store.document(2).to_tree()["value"] == {"seq": 7}
    store.close()
    rows = _raw(path, ("SELECT seq FROM document_revisions ORDER BY seq", ()))
    assert rows == [(seq,) for seq in (range(2, 8) if rewindable else [7])]
    store = open_sqlite_storage(path, mode="open")
    assert commit(store, {"type": "document.retire", "id": 2}) == 8
    assert store.document(2) is None
    store.close()
    assert _raw(path, ("SELECT seq FROM document_revisions ORDER BY seq", ())) == (
        rows if rewindable else []
    )
    _integrity(path)


_DOC_READS = {
    "document": lambda store: store.document(2),
    "find": lambda store: store.find_document(address()),
    "scan": lambda store: store.scan_documents(document_query(), 20),
    "write": lambda store: commit(store, change_document(2, {})),
}


@pytest.mark.parametrize(
    "column", [name for name, _ in sqlite._COLUMNS["document"] if name != "id"]
)
@pytest.mark.parametrize("reader", _DOC_READS)
def test_document_columns_refuse_wrong_storage_classes(tmp_path, column, reader):
    store, path = _store(tmp_path)
    commit(store, create_document(2))
    store.close()
    # A REAL in an INTEGER column remains filter-equal, but must be refused.
    allowed = dict(sqlite._COLUMNS["document"])[column]
    value, storage_class = (
        ("CAST(" + column + " AS REAL)", "real")
        if "integer" in allowed
        else ("CAST(" + column + " AS BLOB)", "blob")
    )
    if column == "retired_at":
        value = "0.0"  # NULL's wrong-class replacement is hidden by current filters.
    if column == "record":
        value, storage_class = "CAST(record AS TEXT)", "text"
    found = store_in_strict_column(path, "documents", column, value, "id = 2")
    assert found == [storage_class]
    store = open_sqlite_storage(path, mode="open")
    try:
        # Wrong-class indexed values are invisible to equality filters; each
        # path that reaches the row rejects it, matching DE03c1's boundary.
        blind = reader in {"find", "scan"} and (
            column in {"scope_kind", "retired_at"}
            or (reader == "find" and column in {"kind", "key_value"})
        )
        if blind:
            result = _DOC_READS[reader](store)
            assert result is None if reader == "find" else result.items == ()
        else:
            with pytest.raises(StorageCorruption, match=column + " column"):
                _DOC_READS[reader](store)
    finally:
        store.close()


@pytest.mark.parametrize("column", [name for name, _ in sqlite._REVISION_COLUMNS])
@pytest.mark.parametrize("reader", ["document", "write"])
def test_revision_columns_refuse_wrong_storage_classes(tmp_path, column, reader):
    store, path = _store(tmp_path)
    commit(store, create_document(2))
    store.close()
    value = {
        "document_id": "2.0",
        "seq": "2.0",
        "kind": "CAST(kind AS BLOB)",
        "version": "CAST(version AS TEXT)",
        "content": "CAST(content AS TEXT)",
    }[column]
    store_in_strict_column(path, "document_revisions", column, value, "document_id = 2")
    store = open_sqlite_storage(path, mode="open")
    try:
        with pytest.raises(StorageCorruption, match=column + " column"):
            _DOC_READS[reader](store)
    finally:
        store.close()


_PRIVATE_PAYLOAD = "PRIVATE-document-payload-8a6b"
_PRIVATE_KIND = "PRIVATE-document-kind-4f2c"
_PRIVATE_TAG = "PRIVATE-document-key-3d9e"
_PRIVATE_VERSION = "PRIVATE-document-version-6c1a"
_PRIVATE_SENTINELS = (_PRIVATE_PAYLOAD, _PRIVATE_KIND, _PRIVATE_TAG, _PRIVATE_VERSION)


def _blob_sql(value):
    return "x'" + value.hex() + "'"


def _assert_private_errors(error):
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        assert all(
            secret not in str(current) and secret not in repr(current)
            for secret in _PRIVATE_SENTINELS
        )
        pending.extend(
            item
            for item in (current.__cause__, current.__context__)
            if item is not None
        )


@pytest.mark.parametrize(
    "table,column,value,message",
    [
        ("documents", "kind", "CAST(x'ff' AS TEXT)", "kind column is not UTF-8"),
        (
            "document_revisions",
            "kind",
            "CAST(x'ff' AS TEXT)",
            "kind column is not UTF-8",
        ),
        ("document_revisions", "kind", "'delta'", "invalid document base"),
        ("document_revisions", "seq", "0", "invalid document base"),
        ("document_revisions", "version", "x'ff'", "unreadable document base"),
        (
            "document_revisions",
            "content",
            _blob_sql(b'{"private":"' + _PRIVATE_PAYLOAD.encode()),
            "unreadable document base",
        ),
        (
            "document_revisions",
            "version",
            _blob_sql(sqlite._encode(_PRIVATE_VERSION)),
            "unreadable document base",
        ),
        (
            "documents",
            "record",
            _blob_sql(b'{"private":"' + _PRIVATE_PAYLOAD.encode()),
            "unreadable document record",
        ),
        ("documents", "owner_id", "9", "index columns contradict"),
    ],
)
def test_document_damage_has_specific_data_free_messages(
    tmp_path, table, column, value, message
):
    store, path = _store(tmp_path)
    commit(
        store,
        create_document(
            2, kind=_PRIVATE_KIND, key=_PRIVATE_TAG, value={"private": _PRIVATE_PAYLOAD}
        ),
    )
    store.close()
    where = "id = 2" if table == "documents" else "document_id = 2"
    store_in_strict_column(path, table, column, value, where)
    [(record, version, content)] = _raw(
        path,
        (
            "SELECT d.record, r.version, r.content FROM documents d JOIN document_revisions r ON r.document_id = d.id",
            (),
        ),
    )
    assert any(_PRIVATE_PAYLOAD.encode() in blob for blob in (record, version, content))
    if column == "version" and _PRIVATE_VERSION.encode() in bytes.fromhex(value[2:-1]):
        assert _PRIVATE_VERSION.encode() in version
    store = open_sqlite_storage(path, mode="open")
    try:
        with pytest.raises(StorageCorruption, match=message) as failure:
            store.document(2)
        _assert_private_errors(failure.value)
    finally:
        store.close()


def test_missing_base_is_corruption_and_rolls_back_mixed_commit(tmp_path):
    store, path = _store(tmp_path)
    commit(store, create_document(2))
    store.close()
    _raw(path, ("DELETE FROM document_revisions", ()))
    store = open_sqlite_storage(path, mode="open")
    try:
        with pytest.raises(StorageCorruption, match="required base"):
            commit(store, entry(100), change_document(2, {}))
        assert store.entry(100) is None and store.mint_id() == 3
    finally:
        store.close()
    _integrity(path)


@pytest.mark.parametrize("refusal", ["readonly", "full", "busy"])
def test_real_document_write_refusals_keep_state_and_counters(tmp_path, refusal):
    store, path = _store(tmp_path)
    commit(store, create_document(2, value={"old": True}))
    with contextlib.ExitStack() as lifetime:
        lifetime.callback(store.close)
        connection = store._database.connection()
        writer = None
        if refusal == "readonly":
            connection.execute("PRAGMA query_only = ON")
        elif refusal == "full":
            pages = connection.execute("PRAGMA page_count").fetchone()[0]
            connection.execute(f"PRAGMA max_page_count = {pages}")
        else:
            writer = lifetime.enter_context(
                Child(
                    """
import sqlite3
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute("BEGIN IMMEDIATE")
say("locked")
assert command() == "release"
connection.execute("ROLLBACK")
connection.close()
say("released")
""",
                    path,
                    home=tmp_path,
                )
            )
            writer.expect("locked")
            # Exercise SQLite's actual timeout, bounded for repeated tests.
            connection.execute("PRAGMA busy_timeout = 20")
        try:
            with pytest.raises(sqlite3.OperationalError) as failure:
                commit(
                    store,
                    entry(100),
                    change_document(2, {"large": "x" * 300_000}),
                    create_document(101, key="new"),
                )
            assert type(failure.value) is sqlite3.OperationalError
            assert (
                failure.value.sqlite_errorcode
                == {
                    "readonly": sqlite3.SQLITE_READONLY,
                    "full": sqlite3.SQLITE_FULL,
                    "busy": sqlite3.SQLITE_BUSY,
                }[refusal]
            )
            assert not connection.in_transaction
        finally:
            if writer is not None:
                writer.send("release")
                writer.expect("released")
                assert writer.wait() == 0
            if refusal == "readonly":
                connection.execute("PRAGMA query_only = OFF")
            elif refusal == "full":
                connection.execute("PRAGMA max_page_count = 2147483646")
            else:
                connection.execute("PRAGMA busy_timeout = 5000")
        assert store.entry(100) is None and store.document(101) is None
        assert store.document(2).to_tree()["value"] == {"old": True}
        assert store.mint_id() == 3 and store.commit(()) == 3
        store.close()
        _integrity(path)


def test_after_commit_ack_loss_adopts_ids_and_is_durable(tmp_path, monkeypatch):
    store, path = _store(tmp_path)

    def lose_ack(connection):
        assert not connection.in_transaction
        raise RuntimeError("lost acknowledgement")

    monkeypatch.setattr(sqlite, "_after_commit", lose_ack)
    with pytest.raises(RuntimeError, match="lost acknowledgement"):
        commit(store, entry(100), create_document(101, version=-0.0))
    assert store.entry(100) and store.document(101)
    assert store.mint_id() == 102
    store.close()
    monkeypatch.undo()
    store = open_sqlite_storage(path, mode="open")
    assert store.entry(100).commit_seq == 2 and store.document(101)
    assert store.mint_id() == 102 and store.commit(()) == 3
    store.close()
    _integrity(path)


def test_document_commit_raise_poisons_until_reopen(tmp_path):
    store, path = _store(tmp_path)
    connection = store._database.connection()
    connection.set_authorizer(_deny("COMMIT"))
    with pytest.raises(sqlite3.DatabaseError) as failure:
        commit(store, entry(100), create_document(101))
    assert failure.value.sqlite_errorcode == sqlite3.SQLITE_AUTH
    connection.set_authorizer(None)
    assert not connection.in_transaction
    for read in (
        lambda: store.document(101),
        lambda: store.find_document(address()),
        lambda: store.scan_documents(document_query(), 10),
    ):
        with pytest.raises(StorageClosedError):
            read()
    store.close()
    store = open_sqlite_storage(path, mode="open")
    assert store.entry(100) is None and store.document(101) is None
    assert store.mint_id() == 2 and store.commit(()) == 2
    store.close()
    _integrity(path)


@pytest.mark.parametrize("cut", ["before", "after", "mid-batch"])
def test_sigkill_document_commit_cuts_are_atomic_private_and_reopenable(tmp_path, cut):
    path = str(tmp_path / "store.sqlite")
    with Child(
        r"""
from pipy_harness.native.durable import sqlite as storage
from pipy_harness.native.durable.storage import StorageWrite
store = storage.open_sqlite_storage(sys.argv[1], mode="create")
store.commit((StorageWrite({"type":"conversation", "value":{"id":1}}),))
def cut(connection):
    say("cut", in_transaction=connection.in_transaction, modes=modes(os.path.dirname(sys.argv[1])))
    assert command() == "unreachable"
if sys.argv[2] == "before":
    storage._before_commit = cut
elif sys.argv[2] == "after":
    storage._after_commit = cut
else:
    # Cut after the first document, before the remainder of the same batch.
    original_method = storage.SqliteStorage._write_documents
    def partial(self, connection, prepared, actions, seq):
        first = next(iter(prepared))
        original_method(self, connection, {first:prepared[first]}, {first:actions[first]}, seq)
        cut(connection)
    storage.SqliteStorage._write_documents = partial
writes = [
    {"type":"entry", "value":{"id":100,"conversationId":1,"kind":"test"}},
    {"type":"document.create", "record":{"id":101,"kind":"cache","scope":{"kind":"session"}}, "content":{"kind":"base","version":-0.0,"value":{"large":"x"*300000}}},
    {"type":"document.create", "record":{"id":102,"kind":"cache","key":"next","scope":{"kind":"session"}}, "content":{"kind":"base","version":1.5,"value":{}}},
]
store.commit(tuple(StorageWrite(value) for value in writes))
""",
        path,
        cut,
        home=tmp_path,
    ) as child:
        event = child.expect("cut")
        assert event["in_transaction"] == (cut != "after")
        for name, mode in event["modes"].items():
            if name.startswith("store.sqlite"):
                assert mode == "0o600"
        child.kill_group()
        assert child.wait() == -9
    (tmp_path / "crash-evidence.json").write_text(
        json.dumps(child.evidence(), indent=2)
    )
    store = open_sqlite_storage(path, mode="open")
    present = cut == "after"
    assert (store.entry(100) is not None) == present
    assert (store.document(101) is not None) == present
    assert (store.document(102) is not None) == present
    assert store.mint_id() == (103 if present else 2)
    assert store.commit(()) == (3 if present else 2)
    store.close()
    for name in (path, path + ".lock"):
        assert stat.S_IMODE(Path(name).stat().st_mode) == 0o600
    _integrity(path)


def test_document_churn_reuses_pages_with_concrete_size_bound_and_close_checkpoint(
    tmp_path, monkeypatch
):
    store, path = _store(tmp_path)
    connection = store._database.connection()
    assert connection.execute("PRAGMA secure_delete").fetchone() == (1,)
    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    page_counts = []
    for round in range(20):
        id = round + 2
        commit(store, create_document(id, value={"text": "a" * 128_000}))
        commit(store, change_document(id, {"text": "b" * 128_000}, -0.0))
        commit(store, {"type": "document.retire", "id": id})
        page_counts.append(connection.execute("PRAGMA page_count").fetchone()[0])
    # Twenty 256 KiB write/retire cycles reuse a bounded set of pages; metadata,
    # 20 retired records and immutable ID claims remain. This is a workload bound,
    # not a promise of constant size for arbitrarily many document identities.
    assert max(page_counts) * page_size <= 512 * 1024
    assert page_counts[-1] <= page_counts[0] + 4
    assert Path(path + "-wal").stat().st_size > 0
    observed = []
    # The raw connection cannot be used after close; observe TRUNCATE through
    # SQLite's trace callback while close still owns it, without opening an fd.
    connection.set_trace_callback(observed.append)
    store.close()
    assert any("wal_checkpoint(TRUNCATE)" in sql for sql in observed)
    assert not Path(path + "-wal").exists() or Path(path + "-wal").stat().st_size == 0
    assert Path(path).stat().st_size <= 512 * 1024
    _integrity(path)


def test_document_materialization_reads_one_snapshot_across_foreign_commit(tmp_path):
    from test_sqlite_storage import _FOREIGN_WRITER

    store, path = _store(tmp_path)
    commit(store, create_document(2, value={"old": True}))
    connection = store._database.connection()
    with Child(_FOREIGN_WRITER, path, home=tmp_path) as writer:
        writer.expect("ready")
        commits = []

        def between(sql):
            if "FROM document_revisions" in sql and not commits:
                commits.append(True)
                writer.send(
                    json.dumps(
                        [
                            [
                                "UPDATE document_revisions SET content = ? WHERE document_id = 2",
                                [{"hex": sqlite._encode({"new": True}).hex()}],
                            ]
                        ]
                    )
                )
                writer.expect("written")

        connection.set_trace_callback(between)
        try:
            assert store.document(2).to_tree()["value"] == {"old": True}
        finally:
            connection.set_trace_callback(None)
        assert commits == [True]
        assert store.document(2).to_tree()["value"] == {"new": True}
        writer.send("done")
        writer.expect("closed")
        assert writer.wait() == 0
    store.close()
    _integrity(path)


def test_document_reads_use_address_scope_and_revision_indexes(tmp_path):
    from test_sqlite_storage import _plan

    store, path = _store(tmp_path)
    commit(store, create_document(2))
    connection = store._database.connection()
    statements = []
    connection.set_trace_callback(statements.append)
    store.find_document(address())
    store.scan_documents(document_query(), 10)
    store.scan_documents(document_query(kind="cache"), 10)
    store.document(2)
    connection.set_trace_callback(None)
    selected = [sql for sql in statements if "SELECT" in sql]
    expected = [
        "documents_by_address",
        "documents_by_scope",
        "documents_by_scope_kind",
        "INTEGER PRIMARY KEY",
        "sqlite_autoindex_document_revisions_1",
    ]
    assert len(selected) == len(expected)
    for sql, index in zip(selected, expected, strict=True):
        plan = _plan(connection, sql, ())
        assert index in plan and "TEMP B-TREE" not in plan, plan
    store.close()
    _integrity(path)


@pytest.mark.parametrize("table", ["documents", "document_revisions"])
@pytest.mark.parametrize("reader", ["document", "write"])
def test_damaged_document_btree_is_storage_corruption(tmp_path, table, reader):
    store, path = _store(tmp_path)
    commit(store, create_document(2))
    store.close()
    [(root, size)] = _raw(
        path,
        (
            "SELECT rootpage, (SELECT page_size FROM pragma_page_size()) FROM sqlite_schema WHERE name = ?",
            (table,),
        ),
    )
    with open(path, "r+b") as stream:
        stream.seek((root - 1) * size)
        stream.write(b"\x01")
    store = open_sqlite_storage(path, mode="open")
    try:
        with pytest.raises(StorageCorruption) as failure:
            _DOC_READS[reader](store)
        assert failure.value.__cause__.sqlite_errorcode & 0xFF == sqlite3.SQLITE_CORRUPT
    finally:
        store.close()


def test_document_write_refusal_readiness_failure_reaps_child_and_closes_store(
    tmp_path, monkeypatch
):
    from sqlite_child_support import group_exists

    original = Child.expect
    children = []

    def readiness_failure(self, name, timeout=60.0):
        event = original(self, name, timeout)
        if name == "locked":
            children.append(self)
            raise RuntimeError("injected readiness observation failure")
        return event

    monkeypatch.setattr(Child, "expect", readiness_failure)
    with pytest.raises(RuntimeError, match="readiness observation failure"):
        test_real_document_write_refusals_keep_state_and_counters(tmp_path, "busy")
    assert len(children) == 1
    child = children[0]
    assert child.process.poll() == -9 and not group_exists(child.group)
    assert all(
        stream.closed
        for stream in (child.process.stdin, child.process.stdout, child.process.stderr)
    )
    store = open_sqlite_storage(str(tmp_path / "store.sqlite"), mode="open")
    try:
        assert store.document(2).to_tree()["value"] == {"old": True}
        assert store.mint_id() == 3
    finally:
        store.close()
    _integrity(str(tmp_path / "store.sqlite"))
