# ruff: noqa: E402
"""Supplied current deltas: atomic owned candidates and semantic schema2."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

pytest.register_assert_rewrite("storage_contract_cases")
import storage_contract_cases as contract  # noqa: E402
from sqlite_child_support import Child  # noqa: E402
from storage_contract_cases import ALL, root
from test_memory_storage import (
    address,
    change_document,
    commit,
    create_document,
    document_query,
    entry,
    submission,
    task,
)
from test_sqlite_schema import store_in_strict_column
from test_sqlite_storage import _integrity, _raw, _store

from pipy_harness.native.durable import sqlite, sqlite_schema
from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.delta_ops import DeltaPathError
from pipy_harness.native.durable.records import RecordError
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.sqlite_schema import StorageCorruption
from pipy_harness.native.durable.storage import StorageRequestError

backend = contract.backend


def delta(id, ops, version=1):
    return {
        "type": "document.change",
        "id": id,
        "content": {"kind": "delta", "version": version, "ops": ops},
    }


@ALL
@pytest.mark.parametrize("version", [0, -0.0, -1, 1.5, 2**100, 1.7976931348623157e308])
def test_supplied_empty_and_noop_deltas_count_reset_and_keep_base_version(
    backend, version
):
    store = root(backend)
    commit(store, create_document(2, value={"n": -0.0}, version=version))
    prior = store.document(2)
    for count, ops in enumerate([[], [["s", ["n"], -0.0]], [["d", ["absent"]]]], 1):
        # +0 and -0 match as version numbers; materialization retains base bits.
        commit(store, delta(2, ops, 0 if version == 0 else version))
        current = store.document(2).to_tree()
        assert current["deltasSinceBase"] == count
        assert struct.pack(">d", current["version"]) == struct.pack(">d", version)
        assert OwnedJson(current["value"]) == OwnedJson({"n": -0.0})
    assert prior.to_tree()["deltasSinceBase"] == 0
    commit(store, change_document(2, {"reset": True}, 2))
    assert store.document(2).to_tree()["deltasSinceBase"] == 0
    commit(store, delta(2, [["s", ["n"], 3]], 2))
    assert store.document(2).to_tree()["deltasSinceBase"] == 1


@ALL
def test_every_operation_reserved_payload_and_utf16_over_multiple_revisions(backend):
    store = root(backend)
    commit(store, create_document(2, value={"text": "😀tail", "a": [0, 1, 2]}))
    retained = store.document(2)
    commit(
        store,
        delta(
            2,
            [
                ["t", ["text"], 1],
                ["a", ["text"], "\ud800"],
                ["p", ["a"], 1, 1, [{"__proto__": -0.0}]],
                ["m", ["a"], [2, 1, 0]],
                ["d", ["a", 2]],
                ["s", ["key"], {"constructor": {"prototype": 2**100}}],
            ],
        ),
    )
    expected = {
        "text": "\ude00tail\ud800",
        "a": [2, {"__proto__": -0.0}],
        "key": {"constructor": {"prototype": 2**100}},
    }
    assert OwnedJson(store.document(2).to_tree()["value"]) == OwnedJson(expected)
    commit(store, delta(2, [["r", [0]], ["r", {"final": True}]]))
    assert store.document(2).to_tree()["value"] == {"final": True}
    assert store.document(2).to_tree()["deltasSinceBase"] == 2
    assert retained.to_tree()["value"] == {"text": "😀tail", "a": [0, 1, 2]}


@ALL
def test_late_bad_operation_rolls_back_all_tables_documents_indices_and_counters(
    backend,
):
    store = root(backend)
    commit(
        store,
        create_document(2, value={"a": {"n": 0}}),
        create_document(3, key="second", value={"x": 0}),
        submission(4, "kept"),
    )
    before = backend.state(store)
    retained = store.document(2)
    with pytest.raises(DeltaPathError):
        commit(
            store,
            entry(100),
            task(101),
            submission(4, "changed"),
            create_document(102, key="new"),
            delta(2, [["s", ["a", "n"], 1]]),
            delta(3, [["s", ["x"], 1], ["s", ["missing", "child"], 2], ["bad"]]),
        )
    assert backend.state(store) == before
    assert store.document(2) == retained
    assert store.entry(100) is None and store.task(101) is None
    assert store.submission_by_request(1, "kept").id == 4
    assert store.submission_by_request(1, "changed") is None
    assert store.find_document(address(key="new")) is None
    assert [
        record.id for record in store.scan_documents(document_query(), 20).items
    ] == [2, 3]
    assert store.mint_id() == 5
    assert store.commit(()) == 3


@ALL
@pytest.mark.parametrize("failure", ["missing", "version", "root", "retired"])
def test_invalid_delta_is_an_atomic_request_failure(backend, failure):
    store = root(backend)
    commit(store, create_document(2))
    if failure == "retired":
        commit(store, {"type": "document.retire", "id": 2})
    before = backend.state(store)
    id = 3 if failure == "missing" else 2
    ops = [["r", []]] if failure == "root" else []
    error = {
        "missing": RuntimeError,
        "version": StorageRequestError,
        "root": RecordError,
        "retired": RuntimeError,
    }[failure]
    with pytest.raises(error):
        commit(store, entry(100), delta(id, ops, 2 if failure == "version" else 1))
    assert backend.state(store) == before


@ALL
def test_deep_owned_delta_payload_has_no_recursion_limit(backend):
    store = root(backend)
    value = {"zero": -0.0, "max": 1.7976931348623157e308, "min": 5e-324}
    for _ in range(1500):
        value = {"child": value}
    commit(store, create_document(2))
    write = delta(2, [["s", ["deep"], value]])
    commit(store, write)
    assert OwnedJson(store.document(2).to_tree()["value"]) == OwnedJson({"deep": value})
    value.clear()
    assert store.document(2).to_tree()["value"]["deep"]


@pytest.mark.parametrize("rewindable", [False, True])
def test_sqlite_appends_actual_ops_without_forced_bases_and_prunes_on_base(
    tmp_path, rewindable
):
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
    commit(store, create_document(2, value={"n": 0}, **policy))
    connection = store._database.connection()
    base = connection.execute("SELECT * FROM document_revisions").fetchall()
    for n in range(1, 41):
        commit(store, delta(2, [["s", ["n"], n]]))
        assert store.document(2).to_tree()["deltasSinceBase"] == n
    rows = connection.execute(
        "SELECT * FROM document_revisions ORDER BY seq"
    ).fetchall()
    assert rows[:1] == base and len(rows) == 41
    assert all(row[2] == "delta" for row in rows[1:])
    assert sqlite._decode(rows[-1][4]) == [["s", ["n"], 40]]
    commit(store, change_document(2, {"fresh": True}, 2))
    assert store.document(2).to_tree()["deltasSinceBase"] == 0
    assert connection.execute("SELECT count(*) FROM document_revisions").fetchone()[
        0
    ] == (42 if rewindable else 1)
    commit(store, delta(2, [], 2))
    commit(store, {"type": "document.retire", "id": 2})
    assert store.document(2) is None
    assert connection.execute("SELECT count(*) FROM document_revisions").fetchone()[
        0
    ] == (43 if rewindable else 0)
    store.close()
    _integrity(path)


@pytest.mark.parametrize("column", [name for name, _ in sqlite._REVISION_COLUMNS])
def test_genuine_delta_rows_enforce_every_storage_class(tmp_path, column):
    store, path = _store(tmp_path)
    commit(store, create_document(2))
    commit(store, delta(2, [["s", ["n"], 1]]))
    store.close()
    wrong = {
        "document_id": "2.0",
        "seq": "3.0",
        "kind": "CAST(kind AS BLOB)",
        "version": "CAST(version AS TEXT)",
        "content": "CAST(content AS TEXT)",
    }[column]
    store_in_strict_column(path, "document_revisions", column, wrong, "seq = 3")
    store = open_sqlite_storage(path, mode="open")
    try:
        with pytest.raises(StorageCorruption, match=column + " column"):
            store.document(2)
        with pytest.raises(StorageCorruption):
            commit(store, entry(100), delta(2, []))
        assert store.entry(100) is None
    finally:
        store.close()


@pytest.mark.parametrize(
    "sql,params",
    [
        ("DELETE FROM document_revisions WHERE kind = 'base'", ()),
        ("UPDATE document_revisions SET version = ? WHERE seq = 3", (b"2",)),
        ("UPDATE document_revisions SET seq = 100 WHERE seq = 3", ()),
        ("UPDATE document_revisions SET seq = 1 WHERE seq = 3", ()),
        ("UPDATE document_revisions SET seq = 0 WHERE kind = 'base'", ()),
        ("UPDATE document_revisions SET kind = CAST(x'ff' AS TEXT) WHERE seq = 3", ()),
        (
            "UPDATE document_revisions SET content = ? WHERE seq = 3",
            (b'[["s",["PRIVATE","x"],1]]',),
        ),
        ("UPDATE document_revisions SET content = ? WHERE seq = 3", (b'[["r",[]]]',)),
        (
            "UPDATE document_revisions SET content = ? WHERE seq = 3",
            (b'[["PRIVATE"]]',),
        ),
        ("UPDATE document_revisions SET content = ? WHERE seq = 3", (b"{}",)),
    ],
)
def test_corrupt_required_tail_fails_without_payload_or_partial_commit(
    tmp_path, sql, params
):
    store, path = _store(tmp_path)
    commit(store, create_document(2))
    commit(store, delta(2, [["s", ["n"], 1]]))
    store.close()
    # Constraints may need bypassing solely to install genuine damaged rows.
    _raw(path, ("PRAGMA ignore_check_constraints = ON", ()), (sql, params))
    store = open_sqlite_storage(path, mode="open")
    try:
        with pytest.raises(StorageCorruption) as failed:
            store.document(2)
        error = failed.value
        while error:
            assert "PRIVATE" not in str(error) and "PRIVATE" not in repr(error)
            error = error.__cause__ or error.__context__
        with pytest.raises(StorageCorruption):
            commit(store, entry(100), delta(2, []))
        assert store.entry(100) is None and store.mint_id() == 3
    finally:
        store.close()


def test_current_replay_does_not_decode_irrelevant_retained_history(
    tmp_path, monkeypatch
):
    store, path = _store(tmp_path)
    commit(
        store,
        create_document(
            2,
            history="rewindable",
            fork="initial",
            scope={"kind": "conversation", "conversationId": 1},
        ),
    )
    for n in range(30):
        commit(store, change_document(2, {"n": n}))
    commit(store, delta(2, [["s", ["tail"], 1]]))
    decoded = []
    original = sqlite._decode

    def decode(blob):
        decoded.append(blob)
        return original(blob)

    monkeypatch.setattr(sqlite, "_decode", decode)
    assert store.document(2).to_tree()["value"] == {"n": 29, "tail": 1}
    # One document, two versions and two payloads; older bases are never decoded.
    assert len(decoded) == 5
    store.close()
    _integrity(path)


def test_current_base_plus_tail_uses_one_read_snapshot(tmp_path):
    from test_sqlite_storage import _FOREIGN_WRITER

    store, path = _store(tmp_path)
    commit(store, create_document(2, value={"n": 0}))
    commit(store, delta(2, [["s", ["n"], 1]]))
    connection = store._database.connection()
    with Child(_FOREIGN_WRITER, path, home=tmp_path) as writer:
        writer.expect("ready")
        seen = []

        def between(sql):
            if "FROM document_revisions" in sql and not seen:
                seen.append(True)
                writer.send(
                    json.dumps(
                        [
                            [
                                "INSERT INTO document_revisions VALUES (2, 4, 'delta', ?, ?)",
                                [
                                    {"hex": b"1".hex()},
                                    {"hex": sqlite._encode([["s", ["n"], 2]]).hex()},
                                ],
                            ],
                            ["UPDATE durable_metadata SET next_seq = 5", []],
                        ]
                    )
                )
                writer.expect("written")

        connection.set_trace_callback(between)
        try:
            old = store.document(2).to_tree()
            assert old["value"] == {"n": 1} and old["deltasSinceBase"] == 1
        finally:
            connection.set_trace_callback(None)
        new = store.document(2).to_tree()
        assert new["value"] == {"n": 2} and new["deltasSinceBase"] == 2
        writer.send("done")
        writer.expect("closed")
        assert writer.wait() == 0
    store.close()
    _integrity(path)


_OLD_COMMIT = "6e3b838fd253e7793c1d1365c6efc2d233a0ecce"
_OLD_MODULES = (
    "__init__",
    "codec",
    "records",
    "storage",
    "memory",
    "ownership",
    "sqlite_schema",
    "sqlite_connection",
    "sqlite",
)


@pytest.fixture
def actual_old_source(tmp_path):
    """Actual delivered source, isolated package; no relabelled current constants."""
    import hashlib

    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures/durable/storage-current-deltas-v1.old-source.json"
    )
    original = json.loads(fixture.read_text())
    assert original["commit"] == _OLD_COMMIT
    destination = tmp_path / "old_durable"
    destination.mkdir()
    hashes = {}
    assert set(original["sources"]) == {
        f"src/pipy_harness/native/durable/{module}.py" for module in _OLD_MODULES
    }
    for module in _OLD_MODULES:
        relative = f"src/pipy_harness/native/durable/{module}.py"
        item = original["sources"][relative]
        data = item["source"].encode()
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
        (destination / f"{module}.py").write_bytes(data)
        hashes[relative] = item["sha256"]
    (tmp_path / "old-source-hashes.json").write_text(json.dumps(hashes, indent=2))
    return destination


@pytest.mark.parametrize("wal_only", [False, True])
def test_semantic_schema2_upgrades_actual_v1_and_actual_old_opener_refuses(
    tmp_path, actual_old_source, wal_only
):
    from test_sqlite_schema import digest, tree

    path = str(tmp_path / "store.sqlite")
    old_import = """
sys.path.insert(0, sys.argv[2])
from old_durable.sqlite import open_sqlite_storage as old_open
from old_durable.storage import StorageWrite as OldWrite
from old_durable.sqlite_schema import NewerStoreError as OldNewer
import old_durable.sqlite_schema as old_schema
assert old_schema.MIGRATIONS[-1].version == 1
assert old_schema.__file__.startswith(sys.argv[2])
"""
    with Child(
        old_import
        + """
store = old_open(sys.argv[1], mode="create")
store.commit((OldWrite({"type":"document.create", "record":{"id":2,"kind":"cache","scope":{"kind":"session"}}, "content":{"kind":"base","version":1,"value":{"n":0}}}),))
store.close()
say("created", source=old_schema.__file__)
""",
        path,
        str(tmp_path),
        home=tmp_path,
    ) as child:
        child.expect("created")
        assert child.wait() == 0
    before = _raw(path, ("SELECT * FROM document_revisions", ()))
    if wal_only:
        with Child(
            """
from pipy_harness.native.durable.sqlite import open_sqlite_storage
store = open_sqlite_storage(sys.argv[1], mode="open")
assert store._database.schema_version == 2
say("upgraded")
os._exit(0)
""",
            path,
            home=tmp_path,
        ) as child:
            child.expect("upgraded")
            assert child.wait() == 0
        assert Path(path).read_bytes()[60:64] == b"\0\0\0\1"
        protected = {name: digest(Path(path + name)) for name in ("", "-wal")}
    else:
        store = open_sqlite_storage(path, mode="open")
        assert store._database.schema_version == 2
        assert store.document(2).to_tree()["value"] == {"n": 0}
        store.close()
        assert _raw(path, ("SELECT * FROM document_revisions", ())) == before
        protected = tree(tmp_path)
    with Child(
        old_import
        + """
try:
    old_open(sys.argv[1], mode="open")
except OldNewer as error:
    assert str(error) == "SQLite schema version 2 is newer than supported version 1"
    say("refused", source=old_schema.__file__)
else:
    raise AssertionError("actual old opener accepted schema2")
""",
        path,
        str(tmp_path),
        home=tmp_path,
    ) as child:
        child.expect("refused")
        assert child.wait() == 0
    if wal_only:
        assert {name: digest(Path(path + name)) for name in ("", "-wal")} == protected
    else:
        assert tree(tmp_path) == protected
    store = open_sqlite_storage(path, mode="open")
    assert sqlite_schema.MIGRATIONS[1] == sqlite_schema.Migration(2, ())
    assert (
        store._database.connection()
        .execute("SELECT * FROM document_revisions")
        .fetchall()
        == before
    )
    commit(store, delta(2, [["s", ["n"], 1]]))
    store.close()
    store = open_sqlite_storage(path, mode="open")
    assert store.document(2).to_tree()["deltasSinceBase"] == 1
    store.close()
    _integrity(path)


@pytest.mark.parametrize("cut", ["before", "after", "mid-batch"])
def test_sigkill_delta_commit_cuts_keep_atomic_values_counts_and_private_files(
    tmp_path, cut
):
    import stat

    path = str(tmp_path / "store.sqlite")
    store, _ = _store(tmp_path)
    commit(
        store,
        create_document(2, value={"n": 0}),
        create_document(3, key="second", value={"n": 0}),
    )
    store.close()
    with Child(
        """
from pipy_harness.native.durable import sqlite as storage
from pipy_harness.native.durable.storage import StorageWrite
store = storage.open_sqlite_storage(sys.argv[1], mode="open")
def cut(connection):
    say("cut", transaction=connection.in_transaction, modes=modes(os.path.dirname(sys.argv[1])))
    command()
if sys.argv[2] == "before":
    storage._before_commit = cut
elif sys.argv[2] == "after":
    storage._after_commit = cut
else:
    original = storage.SqliteStorage._write_documents
    def partial(self, connection, prepared, actions, seq):
        first = next(iter(prepared))
        original(self, connection, {first:prepared[first]}, {first:actions[first]}, seq)
        cut(connection)
    storage.SqliteStorage._write_documents = partial
writes = [{"type":"entry", "value":{"id":100,"conversationId":1,"kind":"test"}}]
for id in [2,3]:
    writes.append({"type":"document.change","id":id,"content":{"kind":"delta","version":1,"ops":[["s",["n"],1],["s",["large"],"x"*300000]]}})
store.commit(tuple(StorageWrite(value) for value in writes))
""",
        path,
        cut,
        home=tmp_path,
    ) as child:
        event = child.expect("cut")
        assert event["transaction"] == (cut != "after")
        assert all(
            mode == "0o600"
            for name, mode in event["modes"].items()
            if name.startswith("store.sqlite")
        )
        child.kill_group()
        assert child.wait() == -9
    (tmp_path / "delta-crash-evidence.json").write_text(
        json.dumps(child.evidence(), indent=2)
    )
    store = open_sqlite_storage(path, mode="open")
    count = int(cut == "after")
    for id in (2, 3):
        value = store.document(id).to_tree()
        assert value["deltasSinceBase"] == count and value["value"]["n"] == count
    assert (store.entry(100) is not None) == bool(count)
    assert store.mint_id() == (101 if count else 4)
    assert store.commit(()) == 3 + count
    store.close()
    assert stat.S_IMODE(Path(path).stat().st_mode) == 0o600
    _integrity(path)


@pytest.mark.parametrize("failure", ["readonly", "full", "busy", "commit", "ack"])
def test_real_delta_sql_failures_and_acknowledgement_outcomes(
    tmp_path, monkeypatch, failure
):
    import contextlib
    import sqlite3

    from test_sqlite_storage import _deny

    from pipy_harness.native.durable.storage import StorageClosedError

    store, path = _store(tmp_path)
    commit(store, create_document(2, value={"n": 0}), create_document(3, key="second"))
    with contextlib.ExitStack() as lifetime:
        lifetime.callback(store.close)
        connection = store._database.connection()
        writer = None
        if failure == "readonly":
            connection.execute("PRAGMA query_only = ON")
        elif failure == "full":
            pages = connection.execute("PRAGMA page_count").fetchone()[0]
            connection.execute(f"PRAGMA max_page_count = {pages}")
        elif failure == "busy":
            writer = lifetime.enter_context(
                Child(
                    """
import sqlite3
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute("BEGIN IMMEDIATE")
say("locked")
command()
connection.execute("ROLLBACK")
connection.close()
say("released")
""",
                    path,
                    home=tmp_path,
                )
            )
            writer.expect("locked")
            connection.execute("PRAGMA busy_timeout = 20")
        elif failure == "commit":
            connection.set_authorizer(_deny("COMMIT"))
        else:

            def lost(connection):
                assert not connection.in_transaction
                raise RuntimeError("lost acknowledgement")

            monkeypatch.setattr(sqlite, "_after_commit", lost)
        with pytest.raises(
            RuntimeError if failure == "ack" else sqlite3.DatabaseError
        ) as failed:
            commit(
                store,
                entry(100),
                delta(2, [["s", ["n"], 1], ["s", ["large"], "x" * 300_000]]),
                delta(3, []),
            )
        if failure != "ack":
            assert (
                failed.value.sqlite_errorcode
                == {
                    "readonly": sqlite3.SQLITE_READONLY,
                    "full": sqlite3.SQLITE_FULL,
                    "busy": sqlite3.SQLITE_BUSY,
                    "commit": sqlite3.SQLITE_AUTH,
                }[failure]
            )
        assert not connection.in_transaction
        connection.set_authorizer(None)
        connection.execute("PRAGMA query_only = OFF")
        connection.execute("PRAGMA max_page_count = 2147483646")
        if writer:
            writer.send("release")
            writer.expect("released")
            assert writer.wait() == 0
        if failure == "commit":
            with pytest.raises(StorageClosedError):
                store.document(2)
        else:
            assert store.document(2).to_tree()["deltasSinceBase"] == int(
                failure == "ack"
            )
        store.close()
    monkeypatch.undo()
    store = open_sqlite_storage(path, mode="open")
    count = int(failure == "ack")
    assert store.document(2).to_tree()["deltasSinceBase"] == count
    assert store.document(3).to_tree()["deltasSinceBase"] == count
    assert (store.entry(100) is not None) == bool(count)
    assert store.mint_id() == (101 if count else 4)
    assert store.commit(()) == 3 + count
    store.close()
    _integrity(path)


@pytest.fixture
def comparison_module():
    import importlib.util

    path = (
        Path(__file__).resolve().parents[2]
        / "scripts/durable_current_deltas_comparison.py"
    )
    spec = importlib.util.spec_from_file_location("current_delta_test_comparison", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "mutation", ["count", "version", "value", "native-sqlite-zero"]
)
def test_current_delta_comparator_rejects_schema_valid_mutants(
    tmp_path, comparison_module, mutation
):
    module = comparison_module
    native = module.native(module.recipes(), tmp_path)
    observed = module.expected_reference(native)
    assert module.compare(observed, native) == 0
    damaged = json.loads(json.dumps(native))
    row = (
        damaged[0]
        if mutation != "native-sqlite-zero"
        else next(
            row
            for row in damaged
            if row["backend"] == "sqlite" and row["id"] == "empty-noop"
        )
    )
    step = row["steps"][1]
    if mutation == "native-sqlite-zero":
        step["document"] = module.normalized_profile(step["document"])
    else:
        name = {"count": "deltasSinceBase", "version": "version", "value": "value"}[
            mutation
        ]
        key = name.encode("utf-16-be").hex()
        for pair in step["document"][1]:
            if pair[0] == key:
                pair[1] = module.d.profile(
                    123 if mutation != "value" else {"wrong": True}
                )
    assert module.compare(observed, damaged) == 1


def test_current_delta_comparator_rejects_incomplete_or_invalid_evidence(
    tmp_path, comparison_module
):
    module = comparison_module
    native = module.native(module.recipes(), tmp_path)
    observed = module.expected_reference(native)
    for damaged in [observed[:-1], [{**observed[0], "steps": []}, *observed[1:]]]:
        with pytest.raises(module.o.EvidenceError):
            module.compare(damaged, native)
    native[0]["steps"][0]["seq"] = True
    with pytest.raises(module.o.EvidenceError):
        module.compare(observed, native)
