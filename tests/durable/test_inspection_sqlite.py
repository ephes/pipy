"""Same-owner nonmigrating open and truthful SQLite maintenance boundaries."""

import pytest

from pipy_harness.native.durable import sqlite_connection, sqlite_schema
from pipy_harness.native.durable.inspection import inspect_store
from pipy_harness.native.durable.inspection_values import InspectionError
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.sqlite_connection import UnsupportedInspectionVersion


def test_current_validation_ro_then_rw_without_migration(tmp_path, monkeypatch):
    path = tmp_path / "store.sqlite"
    open_sqlite_storage(str(path), mode="create").close()
    calls = []
    connect = sqlite_connection._connect

    def tracked(database, mode):
        calls.append(mode)
        return connect(database, mode)

    monkeypatch.setattr(sqlite_connection, "_connect", tracked)
    monkeypatch.setattr(
        sqlite_schema,
        "prepare",
        lambda *args, **kwargs: pytest.fail("inspection migrated"),
    )
    store = open_sqlite_storage(
        str(path), mode="open", schema_policy="validate_current"
    )
    assert calls == ["ro", "rw"]
    store.close()
    assert inspect_store(str(path)).tasks == ()


def test_older_clean_refuses_before_rw_and_preserves_existing_bytes(
    tmp_path, monkeypatch
):
    path = tmp_path / "store.sqlite"
    with monkeypatch.context() as context:
        context.setattr(sqlite_schema, "MIGRATIONS", sqlite_schema.MIGRATIONS[:1])
        open_sqlite_storage(str(path), mode="create").close()
    before = path.read_bytes()
    assert not path.with_name(path.name + "-wal").exists()
    calls = []
    connect = sqlite_connection._connect

    def tracked(database, mode):
        calls.append(mode)
        return connect(database, mode)

    monkeypatch.setattr(sqlite_connection, "_connect", tracked)
    monkeypatch.setattr(
        sqlite_schema,
        "prepare",
        lambda *args, **kwargs: pytest.fail("inspection migrated"),
    )
    with pytest.raises(UnsupportedInspectionVersion):
        open_sqlite_storage(str(path), mode="open", schema_policy="validate_current")
    assert calls == ["ro"] and path.read_bytes() == before
    wal = path.with_name(path.name + "-wal")
    if wal.exists():
        assert wal.stat().st_size == 0
        assert wal.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "policy,mode", [(None, "open"), ("other", "open"), ("validate_current", "create")]
)
def test_policy_refusal_has_no_filesystem_effect(tmp_path, policy, mode):
    path = tmp_path / "absent" / "store.sqlite"
    with pytest.raises(ValueError):
        open_sqlite_storage(str(path), mode=mode, schema_policy=policy)
    assert not path.parent.exists()


def test_store_inspection_missing_does_not_create(tmp_path):
    path = tmp_path / "absent.sqlite"
    with pytest.raises(InspectionError) as caught:
        inspect_store(str(path))
    assert caught.value.code == "missing_store"
    assert not list(tmp_path.iterdir())


def test_offline_terminal_read_without_handlers_auth_or_payload_decoder(
    tmp_path, monkeypatch
):
    from pipy_harness.native.durable.inspection import inspect_store_task
    from pipy_harness.native.durable.session_core import Session
    from pipy_harness.native.durable.tasks import define_task

    async def poison(*args):
        pytest.fail("terminal inspection ran code")

    path = tmp_path / "terminal.sqlite"
    session = Session(open_sqlite_storage(str(path), mode="create"))
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    task = define_task(
        "private-kind-sentinel",
        79.5,
        lambda _: {"phase": "unknown", "private": "saved-frame-sentinel"},
        {},
        poison,
    )
    tid = session.commit(
        lambda tx: tx.create_task(
            task,
            {"material": "private-ref-sentinel"},
            ownership={"kind": "conversation"},
            conversation_id=cid,
        )
    )

    def finish(tx):
        row = tx.task(tid).to_tree()
        tx.set_task(
            {
                **row,
                "state": {
                    "status": "terminal",
                    "outcome": {
                        "status": "aborted",
                        "result": {"private": "saved-frame-sentinel"},
                    },
                },
            }
        )

    session.commit(finish)
    session.close()
    monkeypatch.setattr(
        "pipy_harness.native.durable.inspection.decode_checkpoint",
        lambda *a, **k: pytest.fail("terminal decoded"),
    )
    report = inspect_store_task(str(path), tid)
    assert (
        report.status == "terminal"
        and report.outcome == "aborted"
        and report.compatibility == "terminal_readonly"
    )
    assert report.family == "custom" and report.version == 79.5
    assert "sentinel" not in __import__("json").dumps(report.to_tree())


@pytest.mark.parametrize("limit", [0, 129, True, "private"])
def test_invalid_offline_query_refuses_before_open(tmp_path, limit):
    path = tmp_path / "never-created.sqlite"
    with pytest.raises(InspectionError):
        inspect_store(str(path), limit=limit)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("invalid_id", [0, -1, True, "private-id", 1.5, 2**53])
@pytest.mark.parametrize("api", ["task", "conversation"])
def test_invalid_offline_ids_have_closed_errors_before_path_access(
    tmp_path, monkeypatch, invalid_id, api
):
    from pipy_harness.native.durable.inspection import inspect_store_task

    monkeypatch.setattr(
        "pipy_harness.native.durable.sqlite.open_sqlite_storage",
        lambda *a, **k: pytest.fail("invalid id opened a store"),
    )
    path = tmp_path / "never-created.sqlite"
    with pytest.raises(InspectionError) as error:
        if api == "task":
            inspect_store_task(str(path), invalid_id)
        else:
            inspect_store(str(path), conversation_id=invalid_id)
    assert error.value.code == "invalid_request"
    assert not list(tmp_path.iterdir())


def test_offline_bounded_pages_continue_after_terminal_only_page(tmp_path):
    from pipy_harness.native.durable.session_core import Session
    from pipy_harness.native.durable.tasks import define_task

    async def poison(*args):
        pytest.fail("offline page executed task code")

    path = tmp_path / "pages.sqlite"
    session = Session(open_sqlite_storage(str(path), mode="create"))
    cid = session.commit(lambda tx: tx.create_conversation({"kind": "ownerless"})).id
    task = define_task(
        "private-page-task",
        -0.5,
        lambda _: {"phase": "execute"},
        {"execute": poison},
        poison,
    )
    ids = [
        session.commit(
            lambda tx: tx.create_task(
                task, None, ownership={"kind": "conversation"}, conversation_id=cid
            )
        )
        for _ in range(3)
    ]

    def terminal(tx):
        row = tx.task(ids[0]).to_tree()
        tx.set_task(
            {**row, "state": {"status": "terminal", "outcome": {"status": "aborted"}}}
        )

    session.commit(terminal)
    session.close()
    first = inspect_store(str(path), conversation_id=cid, limit=1)
    assert first.tasks == () and first.next is not None
    second = inspect_store(str(path), conversation_id=cid, limit=1, cursor=first.next)
    third = inspect_store(str(path), conversation_id=cid, limit=1, cursor=second.next)
    assert [second.tasks[0].task_id, third.tasks[0].task_id] == ids[1:]
    assert third.next is None
