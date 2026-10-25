"""Exact existing paths, safe schema refusals and truthful empty-store recovery."""

import asyncio
import json
import os
import signal

import pytest
from cli_process_support import run_cli
from sqlite_child_support import Child
from test_cli_lifecycle import HELD_PROVIDER
from test_inspection_wal import cut, facts

from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import TaskQuery
from pipy_harness.sdk import (
    DurableProviderProfile,
    create_durable_product_session,
    open_durable_product_session,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


@pytest.mark.parametrize("action", ["inspect", "resume"])
def test_missing_path_no_creation_or_private_path_echo(tmp_path, action):
    path = tmp_path / "private-path-sentinel" / "missing.sqlite"
    args = [action, "--store", str(path)]
    if action == "resume":
        args += ["--workspace", str(tmp_path)]
    result = run_cli(tmp_path / "receipts", action, args)
    assert result.returncode == 1 and result.stdout == b""
    assert b"private-path-sentinel" not in result.stderr
    assert not path.parent.exists()


def test_foreign_and_corrupt_existing_path_never_creates_or_overwrites(tmp_path):
    for name, body in [
        ("foreign", b"private-foreign-file"),
        ("corrupt", b"SQLite format 3\0" + b"private-broken" * 60),
    ]:
        path = tmp_path / (name + ".sqlite")
        path.write_bytes(body)
        result = run_cli(tmp_path / "receipts", name, ["inspect", "--store", str(path)])
        assert result.returncode == 1 and result.stdout == b""
        assert b"private" not in result.stderr
        assert path.read_bytes() == body


@pytest.mark.parametrize("action", ["inspect", "resume"])
def test_read_only_store_refuses_without_permission_or_content_change(tmp_path, action):
    path = tmp_path / "readonly.sqlite"
    backend = open_sqlite_storage(str(path), mode="create")
    backend.close()
    before = path.read_bytes()
    path.chmod(0o400)
    try:
        args = [action, "--store", str(path)]
        if action == "resume":
            args += ["--workspace", str(tmp_path)]
        result = run_cli(tmp_path / "receipts", "readonly-" + action, args)
        assert result.returncode == 1 and not result.stdout
        assert path.read_bytes() == before and path.stat().st_mode & 0o777 == 0o400
    finally:
        path.chmod(0o600)


@pytest.mark.parametrize("variant", ["old", "newer"])
def test_cli_refuses_effective_wal_schema_before_migration_with_truthful_sidecar_scope(
    tmp_path, variant
):
    path = cut(tmp_path, variant)
    before = facts(path)
    result = run_cli(tmp_path / "receipts", variant, ["inspect", "--store", str(path)])
    assert (
        result.returncode == 1
        and result.stderr == b"Durable command unavailable: unsupported_store_version\n"
    )
    after = facts(path)
    assert before[""] == after[""] and before["-wal"] == after["-wal"]
    # SHM/lock diagnostics/empty-sidecar creation are permitted under the
    # existing RO maintenance contract; never claim blanket filesystem equality.
    (tmp_path / "schema-maintenance.json").write_text(
        json.dumps({"before": before, "after": after}, indent=2)
    )


def test_real_active_os_owner_refuses_passive_cli_without_stealing_lease(tmp_path):
    path = tmp_path / "active.sqlite"
    backend = open_sqlite_storage(str(path), mode="create")
    try:
        result = run_cli(
            tmp_path / "receipts", "busy", ["inspect", "--store", str(path)]
        )
        assert (
            result.returncode == 1
            and result.stderr == b"Durable command unavailable: busy\n"
        )
        assert backend.scan_tasks(TaskQuery({}), 128).items == ()
    finally:
        backend.close()
    result = run_cli(
        tmp_path / "receipts", "available", ["inspect", "--store", str(path)]
    )
    assert result.returncode == 0 and json.loads(result.stdout)["tasks"] == []


def test_empty_store_cli_no_input_and_documented_guarded_public_sdk_recovery(tmp_path):
    path = tmp_path / "empty.sqlite"
    profile = DurableProviderProfile(
        NativeModelSpec("fake", "fake-native-bootstrap", "Test", "fake"),
        auth_path=tmp_path / "auth.json",
        env={},
    )

    async def create():
        product = await create_durable_product_session(
            path, workspace=tmp_path, profile=profile, tools={}
        )
        await product.close()

    asyncio.run(create())
    before = path.read_bytes()
    no_input = run_cli(
        tmp_path / "receipts",
        "no-input",
        ["resume", "--store", str(path), "--workspace", str(tmp_path)],
    )
    assert (
        no_input.returncode == 1
        and no_input.stderr == b"Durable command unavailable: no_input\n"
    )
    assert path.read_bytes() == before

    async def recover():
        product = await open_durable_product_session(
            path, workspace=tmp_path, profile=profile, tools={}
        )
        try:
            admission = await product.submit("explicit recovered input")
            result = await product.resume()
            assert (
                result.task_id == admission.generation_task_id
                and result.inspection.outcome == "completed"
            )
        finally:
            await product.close()

    asyncio.run(recover())
    result = run_cli(
        tmp_path / "receipts",
        "recovered",
        [
            "resume",
            "--store",
            str(path),
            "--workspace",
            str(tmp_path),
            "--format",
            "json",
        ],
    )
    assert result.returncode == 0
    assert (
        json.loads(result.stdout)["completed"]["messages"][0]["message"]["content"]
        == "explicit recovered input"
    )


@pytest.mark.parametrize("ignored", [signal.SIGHUP, signal.SIGINT])
def test_actual_inherited_ignored_signal_does_not_cancel_cli(tmp_path, ignored):
    program = HELD_PROVIDER.replace(
        "root=Path(sys.argv[1]); path=root/'held.sqlite'",
        "signal.signal(int(sys.argv[2]),signal.SIG_IGN)\nroot=Path(sys.argv[1]); path=root/'held.sqlite'",
    )
    assert program != HELD_PROVIDER
    with Child(program, str(tmp_path), str(int(ignored)), home=tmp_path) as child:
        child.expect("invoke")
        os.kill(child.pid, ignored)
        child.send("release-invoke")
        child.expect("finalizer")
        os.kill(child.pid, ignored)
        child.send("release-finalizer")
        child.expect("worker-exiting")
        outcome = child.expect("finished")
        assert outcome["code"] == 0 and outcome["restored"]
        assert child.wait() == 0
        child.wait_stdout_eof()
        (tmp_path / "ignored-process.json").write_text(
            json.dumps(child.evidence(), indent=2)
        )
    assert (
        json.loads((tmp_path / "output.txt").read_text())["inspection"]["outcome"]
        == "completed"
    )
