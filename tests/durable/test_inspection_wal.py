"""Actual process cuts prove effective-WAL validation and truthful maintenance."""

import hashlib
import json
import signal
import sqlite3
from pathlib import Path

import pytest
from sqlite_child_support import Child

from pipy_harness.native.durable import sqlite_connection, sqlite_schema
from pipy_harness.native.durable.inspection import inspect_store
from pipy_harness.native.durable.inspection_values import InspectionError
from pipy_harness.native.durable.sqlite_connection import (
    UnsupportedInspectionVersion,
    open_owned_database,
)
from pipy_harness.native.durable.sqlite_schema import NewerStoreError

COMMITTED = r"""
from pathlib import Path
mode,variant=sys.argv[2:4]
if variant=='old':sqlite_schema.MIGRATIONS=sqlite_schema.MIGRATIONS[:1]
if variant=='newer':sqlite_schema.MIGRATIONS=(*sqlite_schema.MIGRATIONS,sqlite_schema.Migration(3,()))
real=sqlite_schema.apply_migrations
def committed(connection,migrations=None,**options):
    version=real(connection,migrations,**options)
    say('committed',version=version,wal=os.stat(sys.argv[1]+'-wal').st_size)
    command()
    return version
sqlite_schema.apply_migrations=committed
open_owned_database(sys.argv[1],mode=mode)
say('unexpected-return')
"""


def facts(path):
    return {
        suffix: None
        if not Path(str(path) + suffix).exists()
        else {
            "sha256": hashlib.sha256(Path(str(path) + suffix).read_bytes()).hexdigest(),
            "size": Path(str(path) + suffix).stat().st_size,
            "mode": Path(str(path) + suffix).stat().st_mode & 0o777,
        }
        for suffix in ("", "-wal", "-shm", ".lock")
    }


def cut(root, variant):
    path = root / "session.sqlite"
    mode = "create"
    if variant in ("upgraded", "newer"):
        if variant == "upgraded":
            original = sqlite_schema.MIGRATIONS
            try:
                sqlite_schema.MIGRATIONS = original[:1]
                open_owned_database(str(path), mode="create").close()
            finally:
                sqlite_schema.MIGRATIONS = original
        else:
            open_owned_database(str(path), mode="create").close()
        mode = "open"
    with Child(COMMITTED, str(path), mode, variant, home=root) as child:
        event = child.expect("committed")
        assert event["wal"] > 0
        child.kill_group()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
        (root / "wal-cut-process.json").write_text(
            json.dumps({"event": event, "process": child.evidence()}, indent=2)
        )
    return path


def tracked(monkeypatch):
    calls = []
    actual = sqlite_connection._connect

    def connect(path, mode):
        calls.append(mode)
        return actual(path, mode)

    monkeypatch.setattr(sqlite_connection, "_connect", connect)
    monkeypatch.setattr(
        sqlite_schema, "prepare", lambda *a, **k: pytest.fail("inspect called prepare")
    )
    monkeypatch.setattr(
        sqlite_schema,
        "apply_migrations",
        lambda *a, **k: pytest.fail("inspect applied migrations"),
    )
    return calls


@pytest.mark.parametrize("variant", ["current", "upgraded", "old", "newer"])
def test_effective_wal_schema_current_only_never_migrates(
    tmp_path, monkeypatch, variant
):
    path = cut(tmp_path, variant)
    before = facts(path)
    calls = tracked(monkeypatch)
    if variant in ("old", "newer"):
        with pytest.raises((UnsupportedInspectionVersion, NewerStoreError)):
            open_owned_database(
                str(path), mode="open", schema_policy="validate_current"
            )
        after = facts(path)
        assert calls == ["ro"]
        assert after[""] == before[""] and after["-wal"] == before["-wal"]
    else:
        store = open_owned_database(
            str(path), mode="open", schema_policy="validate_current"
        )
        try:
            assert store.schema_version == 2 and calls == ["ro", "rw"]
            assert store.connection().execute("PRAGMA integrity_check").fetchone() == (
                "ok",
            )
        finally:
            store.close()
        after = facts(path)
    (tmp_path / "wal-observed.json").write_text(
        json.dumps(
            {
                "variant": variant,
                "before": before,
                "after": after,
                "connections": calls,
            },
            indent=2,
        )
    )


def logical(path):
    connection = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)
    try:
        return {
            name: connection.execute('SELECT * FROM "' + name + '"').fetchall()
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        connection.close()


@pytest.mark.parametrize("stage", ["configure", "second-validation", "identity"])
def test_rw_refusal_allows_implicit_checkpoint_without_logical_writes(
    tmp_path, monkeypatch, stage
):
    path = cut(tmp_path, "current")
    before, records = facts(path), logical(path)
    calls = tracked(monkeypatch)
    if stage == "configure":
        monkeypatch.setattr(
            sqlite_connection,
            "_configure",
            lambda *a: (_ for _ in ()).throw(
                RuntimeError("fixture private configuration fault")
            ),
        )
    elif stage == "second-validation":
        validate = sqlite_schema.validate_store
        count = 0

        def second(*a, **k):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("fixture private second-validation fault")
            return validate(*a, **k)

        monkeypatch.setattr(sqlite_schema, "validate_store", second)
    else:
        verify = sqlite_connection._verify_identity
        count = 0

        def identity(owner):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("fixture private identity fault")
            return verify(owner)

        monkeypatch.setattr(sqlite_connection, "_verify_identity", identity)
    with pytest.raises(InspectionError) as error:
        inspect_store(str(path))
    assert error.value.code == "invalid_store" and calls == ["ro", "rw"]
    after = facts(path)
    assert logical(path) == records
    # No byte-preservation claim after RW opened: last connection close may
    # checkpoint WAL/remove sidecars even though backend admission failed.
    (tmp_path / "rw-refusal.json").write_text(
        json.dumps(
            {
                "stage": stage,
                "before": before,
                "after": after,
                "connections": calls,
                "logical_records_equal": True,
            },
            indent=2,
        )
    )


def test_captured_schema_target_is_same_for_both_validations(tmp_path, monkeypatch):
    path = tmp_path / "session.sqlite"
    open_owned_database(str(path), mode="create").close()
    actual = sqlite_connection._connect
    calls = []

    def connect(database, mode):
        calls.append(mode)
        connection = actual(database, mode)
        if mode == "ro":
            monkeypatch.setattr(
                sqlite_schema, "MIGRATIONS", sqlite_schema.MIGRATIONS[:1]
            )
        return connection

    monkeypatch.setattr(sqlite_connection, "_connect", connect)
    store = open_owned_database(
        str(path), mode="open", schema_policy="validate_current"
    )
    assert calls == ["ro", "rw"] and store.schema_version == 2
    store.close()
