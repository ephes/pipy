"""Synthetic transports and evidence for actual C2 public SDK/CLI paths."""

import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path


def native_origins():
    result = []
    for name, module in sorted(sys.modules.items()):
        if name.startswith("pipy_harness") and getattr(module, "__file__", None):
            path = Path(module.__file__).resolve()
            result.append(
                {
                    "module": name,
                    "file": str(path),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    return result


def disk_logical(path):
    with closing(
        sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        db.execute("BEGIN")
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        return {
            table: [
                [value.hex() if type(value) is bytes else value for value in row]
                for row in db.execute("SELECT * FROM " + table + " ORDER BY rowid")
            ]
            for table in (
                "conversations",
                "tasks",
                "entries",
                "documents",
                "document_revisions",
                "durable_metadata",
                "record_ids",
                "submissions",
                "submission_requests",
            )
        }


def logical(session):
    """Capture all durable SQL rows under both Session and SQLite read snapshots."""
    from pipy_harness.native.durable.documents import resolve
    from pipy_harness.native.durable.records import DocumentId
    from pipy_harness.native.durable.recovery_catalog import HEADER

    def capture():
        storage = session._owner.storage
        record = storage.find_document(resolve(HEADER)[1])
        assert record is not None
        stored = storage.document(DocumentId(record.id))
        assert stored is not None
        return disk_logical(stored.to_tree()["value"]["path"])

    return session.read_on_line(capture)


def save(root, name, value):
    (root / name).write_text(json.dumps(value, indent=2) + "\n")


CLI_BODY = r"""
import json,sys
from pathlib import Path
from dataclasses import replace
from generation_support import SPEC
from test_product_session import profile,tools
from tool_support import CountedWire,InfrastructureFailureFirst
from pipy_harness.sdk import DurableProviderProfile
from pipy_harness import durable_cli
from pipy_harness.cli import main
from adjudication_sdk_support import native_origins
root=Path(sys.argv[1]); label=sys.argv[2]; kind=sys.argv[3]; argv=json.loads(sys.argv[4]); built=[]
actual_open=durable_cli.open_durable_product_session
async def open_with_fixture(*args,**kwargs):
    if kwargs.get('profile') is not None:
        kwargs['tools']=tools(root,port_type=InfrastructureFailureFirst)
    return await actual_open(*args,**kwargs)
def build(command):
    built.append({'provider':command.provider,'model':command.model})
    if kind=='none':raise AssertionError('auth-free replay constructed a profile')
    if kind=='wrong':
        return DurableProviderProfile(replace(SPEC,model_id='other-model'),auth_path=root/'NO_AUTH.json',env={},runtime_api_key='synthetic-only',http_client=CountedWire(root))
    return profile(root,CountedWire(root))
durable_cli.open_durable_product_session=open_with_fixture
durable_cli.build_profile=build
code=None
try:
    code=main(argv)
finally:
    (root/(label+'-origins.json')).write_text(json.dumps({'pid':__import__('os').getpid(),'pgid':__import__('os').getpgrp(),'argv':sys.argv,'cli_argv':argv,'profile_constructions':built,'code':code,'loaded_native_origins':native_origins()},indent=2)+'\n')
sys.exit(code)
"""


def cli(
    root,
    label,
    task_id,
    nonce,
    *,
    kind="none",
    action="adjudicate-interrupted",
    extra=(),
):
    import pipy_harness

    source = Path(pipy_harness.__file__).resolve().parents[2]
    configured = os.environ.get("C2_SUT_SOURCE")
    if configured is not None:
        assert Path(configured).resolve() == source
    own = Path(__file__).resolve().parent
    prefix = (
        "import sys\nsys.dont_write_bytecode=True\nsys.path[:0]="
        + repr(
            [
                str(source / "src"),
                str(source / "tests"),
                str(source / "tests/durable"),
                str(own),
            ]
        )
        + "\n"
    )
    args = [
        "durable",
        action,
        "--store",
        str(root / "product.sqlite"),
        "--workspace",
        str(root),
        "--format",
        "json",
    ]
    if action == "adjudicate-interrupted":
        args += ["--task", str(task_id), "--nonce", nonce]
    if kind != "none":
        args += ["--provider", "openai", "--model", "gpt-test"]
    args += list(extra)
    argv = [
        sys.executable,
        "-I",
        "-c",
        prefix + CLI_BODY,
        str(root),
        label,
        kind,
        json.dumps(args),
    ]
    env = {
        key: os.environ[key]
        for key in (
            "PATH",
            "HOME",
            "PIPY_AUTH_DIR",
            "PIPY_CONFIG_HOME",
            "XDG_STATE_HOME",
            "TMPDIR",
        )
        if key in os.environ
    }
    with (
        (root / (label + ".stdout")).open("wb") as out,
        (root / (label + ".stderr")).open("wb") as err,
    ):
        process = subprocess.Popen(
            argv, cwd=root, env=env, stdout=out, stderr=err, start_new_session=True
        )
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    try:
        os.killpg(process.pid, 0)
        gone = False
    except ProcessLookupError:
        gone = True
    record = {
        "argv": argv,
        "cwd": str(root),
        "pid": process.pid,
        "pgid": process.pid,
        "exit": process.returncode,
        "reaped": True,
        "group_absent": gone,
        "stdout_sha256": hashlib.sha256(
            (root / (label + ".stdout")).read_bytes()
        ).hexdigest(),
        "stderr_sha256": hashlib.sha256(
            (root / (label + ".stderr")).read_bytes()
        ).hexdigest(),
    }
    save(root, label + "-process.json", record)
    assert gone
    observed = json.loads((root / (label + "-origins.json")).read_text())
    for item in observed["loaded_native_origins"]:
        assert Path(item["file"]).is_relative_to(source / "src"), item
    return (
        process.returncode,
        (root / (label + ".stdout")).read_text(),
        (root / (label + ".stderr")).read_text(),
        observed,
    )
