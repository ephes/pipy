"""Counted public reset fixtures; numeric row changes are typed controls."""

import hashlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from adjudication_sdk_support import disk_logical
from interrupted_cut_support import profile, raw, registry

from pipy_harness.native.durable.records import task
from pipy_harness.sdk import open_durable_product_session


def child_row(root):
    return next(row for row in raw(root)["tasks"] if row["kind"] == "pi.tool")


def typed_counter(root, count, *, pending=False):
    """A real Session commit keeps the typed record and storage indices coherent."""
    from pipy_harness.native.durable.session_core import Session
    from pipy_harness.native.durable.sqlite import open_sqlite_storage

    original = child_row(root)
    assert original["state"]["status"] == "running"
    session = Session(open_sqlite_storage(str(root / "session.sqlite"), mode="open"))
    try:

        def change(tx):
            row = tx.task(original["id"]).to_tree()
            row["restarts"] = count
            if pending:
                row["state"]["status"] = "pending"
            task(row)
            tx.set_task(row)
            return row

        return session.commit(change)
    finally:
        session.close()


async def open_product(
    root, *, current="same", with_profile=True, wrong_model=False, **options
):
    selected_profile = profile(root) if with_profile else None
    if wrong_model:
        from dataclasses import replace

        from generation_support import SPEC
        from interrupted_cut_support import Wire

        from pipy_harness.sdk import DurableProviderProfile

        selected_profile = DurableProviderProfile(
            replace(SPEC, model_id="other-model"),
            auth_path=root / "auth/auth.json",
            env={},
            runtime_api_key="synthetic-only",
            http_client=Wire(root),
        )
    return await open_durable_product_session(
        root / "session.sqlite",
        workspace=root,
        profile=selected_profile,
        tools=registry(root, current=current) if with_profile else {},
        **options,
    )


def snapshot(root):
    return disk_logical(root / "session.sqlite")


CLI_BODY = r"""
import contextlib,io,json,os,sys
from pathlib import Path
from interrupted_cut_support import authority,install_wire,registry
from adjudication_sdk_support import native_origins
from pipy_harness import durable_cli,durable_product_api
from pipy_harness.cli import main
root=Path(sys.argv[1]); label=sys.argv[2]; argv=json.loads(sys.argv[3])
authority(root); install_wire(root)
durable_product_api.builtin_registrations=lambda:registry(root)
code=None
try:
    code=main(argv)
finally:
    (root/(label+'-origins.json')).write_text(json.dumps({'pid':os.getpid(),'pgid':os.getpgrp(),'argv':sys.argv,'cli_argv':argv,'code':code,'loaded_native_origins':native_origins()},indent=2)+'\n')
sys.exit(code)
"""


def cli(root, label, action, *, target=None, extra=(), credentials=True):
    import pipy_harness

    source = Path(pipy_harness.__file__).resolve().parents[2]
    prefix = (
        "import sys\nsys.dont_write_bytecode=True\nsys.path[:0]="
        + repr(
            [str(source / "src"), str(source / "tests"), str(source / "tests/durable")]
        )
        + "\n"
    )
    args = [
        "durable",
        action,
        "--store",
        str(root / "session.sqlite"),
        "--format",
        "json",
    ]
    if action != "inspect":
        args += ["--workspace", str(root)]
    if target is not None:
        args += ["--target-task", str(target)]
    if credentials:
        args += ["--provider", "openai", "--model", "gpt-test"]
    args += list(extra)
    argv = [
        sys.executable,
        "-I",
        "-c",
        prefix + CLI_BODY,
        str(root),
        label,
        json.dumps(args),
    ]
    env = {
        key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ
    }
    with (
        (root / (label + ".stdout")).open("wb") as out,
        (root / (label + ".stderr")).open("wb") as err,
    ):
        process = subprocess.Popen(
            argv, cwd=root, env=env, stdout=out, stderr=err, start_new_session=True
        )
        try:
            process.wait(timeout=45)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    try:
        os.killpg(process.pid, 0)
        absent = False
    except ProcessLookupError:
        absent = True
    record = {
        "argv": argv,
        "cwd": str(root),
        "pid": process.pid,
        "pgid": process.pid,
        "exit": process.returncode,
        "reaped": True,
        "group_absent": absent,
    }
    for stream in ("stdout", "stderr"):
        record[stream + "_sha256"] = hashlib.sha256(
            (root / (label + "." + stream)).read_bytes()
        ).hexdigest()
    (root / (label + "-process.json")).write_text(json.dumps(record, indent=2) + "\n")
    assert absent
    observed = json.loads((root / (label + "-origins.json")).read_text())
    for item in observed["loaded_native_origins"]:
        assert Path(item["file"]).is_relative_to(source / "src"), item
    stdout = (root / (label + ".stdout")).read_text()
    return (
        process.returncode,
        json.loads(stdout) if stdout else None,
        (root / (label + ".stderr")).read_text(),
    )
