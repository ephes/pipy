"""Finite synthetic SDK/CLI crash fixture; no alternative runtime authority."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tarfile
import threading
from contextlib import closing
from pathlib import Path

from pipy_harness import durable_cli, durable_product_api
from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.automation.durable_args import parse_args
from pipy_harness.native.automation.durable_output import run_output
from pipy_harness.native.durable import generation, sqlite, tool_execution
from pipy_harness.native.durable.tool_execution import ToolRegistration
from pipy_harness.native.http import JsonResponse
from pipy_harness.native.tools.base import ToolDefinition, ToolExecutionResult
from pipy_harness.sdk import (
    DurableProductError,
    DurableProviderProfile,
    create_durable_product_session,
    open_durable_product_session,
)

B_COMMIT = "a069a8a81affb8672f78f33177c490dce84c1588"
B_TREE = "28ee70a2f61eec9fd5eb3b221b335780b9ef9efc"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_B_PARSER_HASHES = {
    "tool_execution.py": "4ca9ea19077f8c76a90d3fe9917b4e466bb74b8166c9fcd43011ee27f648bdfd",
    "tool_values.py": "bf8b63806867085afc5116e2fa650e1a52bf11729ea61697679f8d4d21e65c56",
}


def _read_git(repository, arguments, evidence):
    argv = [
        "git",
        "--no-optional-locks",
        "-c",
        "core.hooksPath=/dev/null",
        "-C",
        str(repository),
        *arguments,
    ]
    process = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
    )
    try:
        stdout, stderr = process.communicate(timeout=60)
    except BaseException:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise
    try:
        os.killpg(process.pid, 0)
        group_absent = False
    except ProcessLookupError:
        group_absent = True
    evidence.append(
        {
            "argv": argv,
            "pid": process.pid,
            "pgid": process.pid,
            "exit": process.returncode,
            "reaped": True,
            "group_absent": group_absent,
            "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
            "stderr": stderr.decode(),
        }
    )
    assert process.returncode == 0 and group_absent, evidence[-1]
    return stdout


def _validate_b_parsers(older):
    for filename, expected in _B_PARSER_HASHES.items():
        assert (
            hashlib.sha256(
                (older / "src/pipy_harness/native/durable" / filename).read_bytes()
            ).hexdigest()
            == expected
        )


def archived_b_source(root):
    """Materialize actual pinned Git source; explicit archive overrides stay usable."""
    override = os.environ.get("C1_ARCHIVED_B_SOURCE")
    if override:
        older = Path(override).resolve()
        _validate_b_parsers(older)
        (root / "archived-b-provenance.json").write_text(
            json.dumps(
                {
                    "mode": "explicit caller-owned pinned source override",
                    "commit": B_COMMIT,
                    "source": str(older),
                    "pinned_parser_sha256": _B_PARSER_HASHES,
                },
                indent=2,
            )
        )
        return older
    older = root / "archived-b-source"
    provenance = archived_git_source(
        older, B_COMMIT, B_TREE, ("src", "tests/durable/sqlite_child_support.py")
    )
    _validate_b_parsers(older)
    provenance["pinned_parser_sha256"] = _B_PARSER_HASHES
    (root / "archived-b-provenance.json").write_text(json.dumps(provenance, indent=2))
    return older


def archived_git_source(older, commit, tree, paths):
    """Shared test factory: exact pinned Git source, no imports or network."""
    evidence = []
    assert (
        _read_git(_REPOSITORY_ROOT, ["rev-parse", commit + "^{tree}"], evidence)
        .decode()
        .strip()
        == tree
    )
    archive = _read_git(
        _REPOSITORY_ROOT,
        ["archive", "--format=tar", commit, *paths],
        evidence,
    )
    older.mkdir(parents=True)
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source:
        for member in source:
            name = Path(member.name)
            assert not name.is_absolute() and ".." not in name.parts
            assert name.parts[0] in {"src", "tests"}
            if member.isdir():
                (older / name).mkdir(parents=True, exist_ok=True)
                continue
            assert member.isfile(), member.name
            target = older / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.extractfile(member).read())
            target.chmod(member.mode)
            files[member.name] = {
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "mode": member.mode,
            }
    return {
        "mode": "actual read-only pinned Git archive",
        "commit": commit,
        "tree": tree,
        "source": str(older),
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
        "processes": evidence,
        "files": files,
    }


def validate_archived_b_actor(older, event):
    """Require actual loaded B modules, never a source-label-only assertion."""
    _validate_b_parsers(older)
    observed = event["native_origins"]
    assert "pipy_harness.native.durable.tool_execution" in observed
    assert "pipy_harness.native.durable.tool_values" in observed
    for value in observed.values():
        path = Path(value["path"])
        assert path.is_relative_to(older / "src"), value
        assert hashlib.sha256(path.read_bytes()).hexdigest() == value["sha256"]


SAVED_NAMES = ("later", "keyed", "unused")
NOTICE = "Tool execution interrupted; result is uncertain."
PREPARED_MARKER = "C1_PRIVATE_PREPARED_ARGUMENT"
RETAINED = {"retained": [None, False, 0]}
_PROFILE_CLASS = DurableProviderProfile
_EMIT = None
_CUT = ""
_ROOT = None
_CUT_ONCE = threading.Event()


def authority(root, *, material=True):
    for name, folder in (
        ("HOME", "home"),
        ("PIPY_AUTH_DIR", "auth"),
        ("PIPY_CONFIG_HOME", "config"),
        ("XDG_STATE_HOME", "state"),
        ("XDG_CONFIG_HOME", "xdgconfig"),
        ("XDG_CACHE_HOME", "cache"),
        ("TMPDIR", "tmp"),
    ):
        path = root / folder
        path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path)
    os.environ["PIPY_NATIVE_THEME_PATH"] = str(root / "theme.json")
    os.environ.pop("PYTEST_ADDOPTS", None)
    if not material:
        return
    config = root / "config/models.json"
    config.write_text(
        json.dumps(
            {
                "providers": {
                    "openai": {
                        "api": "openai-completions",
                        "baseUrl": "https://fixture.invalid/c1",
                        "models": [
                            {
                                "id": "gpt-test",
                                "name": "Test",
                                "reasoning": True,
                                "cost": {
                                    "input": 1,
                                    "output": 2,
                                    "cacheRead": 3,
                                    "cacheWrite": 4,
                                },
                            }
                        ],
                    }
                }
            }
        )
    )
    AuthStore().set("openai", {"type": "api_key", "key": "c1-fixture-key"})


def database(root):
    with closing(sqlite3.connect(root / "effects.sqlite")) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS calls (name TEXT,key TEXT,args TEXT)")
        db.execute(
            "CREATE TABLE IF NOT EXISTS applications (key TEXT PRIMARY KEY,args TEXT)"
        )
        db.execute("CREATE TABLE IF NOT EXISTS prepares (name TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS providers (body TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS acknowledgements (name TEXT)")


def counts(root):
    with closing(sqlite3.connect(root / "effects.sqlite")) as db:
        return {
            name: db.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
            for name in (
                "calls",
                "applications",
                "prepares",
                "providers",
                "acknowledgements",
            )
        }


def provider_bodies(root):
    with closing(sqlite3.connect(root / "effects.sqlite")) as db:
        return [
            json.loads(row[0])
            for row in db.execute("SELECT body FROM providers ORDER BY rowid")
        ]


def keys(root):
    with closing(sqlite3.connect(root / "effects.sqlite")) as db:
        return [row[0] for row in db.execute("SELECT key FROM calls ORDER BY rowid")]


def cut_here(stage, **fields):
    if _CUT_ONCE.is_set():
        return
    _CUT_ONCE.set()
    _EMIT("cut", stage=stage, counts=counts(_ROOT), native_origins=origins(), **fields)
    if not threading.Event().wait(30):
        raise RuntimeError("Missing synthetic process-death controller")


class Port:
    def __init__(self, root, name, changed=False):
        self.root, self.name = root, name
        self.definition = ToolDefinition(
            name,
            "changed declaration" if changed else "fixture " + name,
            {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "prepared": {"type": "boolean"},
                    "private_marker": {"type": "string"},
                },
                "required": ["x"],
            },
        )

    def prepare_arguments(self, arguments):
        with closing(sqlite3.connect(self.root / "effects.sqlite")) as db, db:
            db.execute("INSERT INTO prepares VALUES (?)", (self.name,))
        return {**arguments, "prepared": True, "private_marker": PREPARED_MARKER}

    def invoke(self, request, context):
        key = context.call_key
        encoded = json.dumps(request.arguments, sort_keys=True)
        with closing(sqlite3.connect(self.root / "effects.sqlite")) as db, db:
            db.execute("INSERT INTO calls VALUES (?,?,?)", (self.name, key, encoded))
            old = db.execute(
                "SELECT args FROM applications WHERE key=?", (key,)
            ).fetchone()
            assert old is None or old == (encoded,)
            db.execute(
                "INSERT OR IGNORE INTO applications VALUES (?,?)", (key, encoded)
            )
        if _CUT == "effect":
            cut_here("effect", key=key)
        context.output_sink("retained output")
        context.report_diagnostic(
            severity="warning", message="committed diagnostic", code="fixture"
        )
        context.report_details(RETAINED)
        with closing(sqlite3.connect(self.root / "effects.sqlite")) as db, db:
            db.execute("INSERT INTO acknowledgements VALUES (?)", (self.name,))
        return ToolExecutionResult(
            request.tool_request_id,
            "tool completed normally",
            provider_correlation_id=request.provider_correlation_id,
            details={"normal": True},
        )


class Wire:
    def __init__(self, root):
        self.root = root

    def post_json(self, url, **kwargs):
        body = copy.deepcopy(kwargs["body"])
        with closing(sqlite3.connect(self.root / "effects.sqlite")) as db, db:
            db.execute("INSERT INTO providers VALUES (?)", (json.dumps(body),))
        following = any(row["role"] == "tool" for row in body["messages"])
        message = {"content": "terminal answer" if following else "call tool"}
        if not following:
            message["tool_calls"] = [
                {
                    "id": "call:0",
                    "type": "function",
                    "function": {"name": "keyed", "arguments": '{"x":1}'},
                }
            ]
        return JsonResponse(
            200,
            {
                "choices": [
                    {
                        "message": message,
                        "finish_reason": "stop" if following else "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                    "prompt_tokens_details": {"cached_tokens": 4},
                    "completion_tokens_details": {"reasoning_tokens": 3},
                },
            },
        )


def registry(root, original_replay="unsafe", current="same"):
    names = list(SAVED_NAMES)
    if current == "empty":
        names = []
    elif current == "missing":
        names.remove("keyed")
    elif current == "unrelated_removed":
        names.remove("unused")
    elif current == "extra":
        names = ["outside", "unused", "keyed", "later"]
    result = {}
    for name in names:
        replay = original_replay if name == "keyed" else "unsafe"
        if current == "unsafe" and name == "keyed":
            replay = "unsafe"
        port = Port(root, name, changed=current == "declaration" and name == "keyed")
        result[name] = ToolRegistration(
            port,
            "fixture.interrupted." + name,
            "2" if current == "version" and name == "keyed" else "1",
            replay,
        )
    return result


def install_wire(root):
    wire = Wire(root)
    durable_cli.DurableProviderProfile = lambda *args, **kwargs: _PROFILE_CLASS(
        *args, **kwargs, http_client=wire
    )
    return wire


def profile(root):
    command = parse_args(
        [
            "resume",
            "--store",
            str(root / "session.sqlite"),
            "--workspace",
            str(root),
            "--provider",
            "openai",
            "--model",
            "gpt-test",
            "--format",
            "json",
        ]
    )
    return durable_cli.build_profile(command)


def raw(root):
    with closing(sqlite3.connect(root / "session.sqlite")) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        result = {
            "tasks": [
                sqlite._decode(row[0])
                for row in db.execute("SELECT record FROM tasks ORDER BY id")
            ],
            "entries": [
                sqlite._decode(row[0])
                for row in db.execute("SELECT record FROM entries ORDER BY id")
            ],
            "documents": {},
        }
        for kind, content in db.execute(
            "SELECT d.kind,r.content FROM documents d JOIN document_revisions r ON d.id=r.document_id ORDER BY r.document_id,r.seq"
        ):
            result["documents"].setdefault(json.loads(kind), []).append(
                sqlite._decode(content)
            )
        return result


def logical(root):
    with closing(sqlite3.connect(root / "session.sqlite")) as db:
        return {
            table: [
                [v.hex() if isinstance(v, bytes) else v for v in row]
                for row in db.execute("SELECT * FROM " + table + " ORDER BY rowid")
            ]
            for table in (
                "conversations",
                "tasks",
                "entries",
                "documents",
                "document_revisions",
            )
        }


def _after_cut(connection, cut):
    if cut not in ("progress-after", "receipt-after", "interrupted-receipt-after"):
        return
    kinds = {
        json.dumps("pi.tool.progress"): "progress",
        json.dumps("pi.tool.receipt"): "receipt",
    }
    for kind, content in connection.execute(
        "SELECT d.kind,r.content FROM documents d JOIN document_revisions r ON d.id=r.document_id"
    ):
        if kind not in kinds:
            continue
        value = sqlite._decode(content)
        if (
            cut == "progress-after"
            and kinds[kind] == "progress"
            and value.get("details") == {"present": True, "value": RETAINED}
        ):
            cut_here("progress-after", callback_acknowledged=False)
        if kinds[kind] == "receipt" and value:
            full = value.get("nativeToolOutput", {})
            if cut == "receipt-after" or (
                cut == "interrupted-receipt-after"
                and full.get("ending") == "interrupted"
            ):
                cut_here(
                    cut,
                    receipt_sha256=hashlib.sha256(sqlite._encode(value)).hexdigest(),
                )


def install_cuts(root, cut):
    original_run, original_call = tool_execution._Tool.run, tool_execution._Tool.call
    original_request = generation._Generation.request

    async def run(self, record, runtime, context, *args, **kwargs):
        if cut == "execute":
            cut_here("execute")
        return await original_run(self, record, runtime, context, *args, **kwargs)

    async def call(self, record, runtime, context):
        if cut == "child":
            cut_here("child")
        return await original_call(self, record, runtime, context)

    async def request(self, record, runtime, context):
        snapshot = generation._read(record, "request", "request").payload
        with (root / "request-snapshots.jsonl").open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "available_tools": [
                            t.name for t in snapshot.request.available_tools
                        ],
                        "advertised_tool_names": list(snapshot.advertised_tool_names),
                    }
                )
                + "\n"
            )
        if cut == "request":
            cut_here("request")
        return await original_request(self, record, runtime, context)

    tool_execution._Tool.run, tool_execution._Tool.call = run, call
    generation._Generation.request = request
    sqlite._after_commit = lambda connection: _after_cut(connection, cut)


def origins():
    result = {}
    for name, module in tuple(sys.modules.items()):
        if name.startswith(("pipy_harness", "pipy_session")):
            value = getattr(module, "__file__", None)
            if value:
                path = Path(value).resolve()
                result[name] = {
                    "path": str(path),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
    return result


def child_main(root, action, replay, current, cut, emit):
    global _ROOT, _CUT, _EMIT
    root = Path(root)
    _ROOT, _CUT, _EMIT = root, cut, emit
    authority(root, material=action not in ("terminal", "cli-terminal"))
    database(root)
    install_wire(root)
    durable_product_api.builtin_registrations = lambda: registry(root, replay, current)
    install_cuts(root, cut)

    async def sdk():
        product = await (
            create_durable_product_session(
                root / "session.sqlite",
                workspace=root,
                profile=profile(root),
                tools=registry(root, replay),
            )
            if action == "start"
            else open_durable_product_session(
                root / "session.sqlite",
                workspace=root,
                profile=profile(root) if action != "terminal" else None,
                tools=registry(root, replay, current) if action != "terminal" else {},
            )
        )
        try:
            if action == "start":
                admission = await product.submit("literal input")
                emit(
                    "admitted",
                    task_id=admission.generation_task_id,
                    cid=admission.conversation_id,
                )
            result = await product.resume()
            value = run_output(result)
            (root / (action + "-output.json")).write_text(json.dumps(value))
            emit(
                "result",
                status=result.status,
                outcome=result.inspection.outcome,
                counts=counts(root),
            )
        finally:
            await product.close()

    if action in ("cli", "cli-terminal"):
        from pipy_harness.cli import main

        argv = [
            "durable",
            "resume",
            "--store",
            str(root / "session.sqlite"),
            "--workspace",
            str(root),
            "--format",
            "json",
        ]
        if action == "cli":
            argv += ["--provider", "openai", "--model", "gpt-test"]
        import contextlib
        import io

        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(argv)
        (root / "cli.stdout").write_text(stdout.getvalue())
        (root / "cli.stderr").write_text(stderr.getvalue())
        (root / "cli-invocation.json").write_text(
            json.dumps(
                {
                    "argv": argv,
                    "code": code,
                    "in_process_actual_cli": True,
                    "pid": os.getpid(),
                }
            )
        )
        emit(
            "result",
            status=json.loads(stdout.getvalue()).get("status")
            if stdout.getvalue()
            else "refused",
            code=code,
            counts=counts(root),
        )
    else:
        try:
            asyncio.run(sdk())
        except DurableProductError as error:
            emit("refused", code=error.code, counts=counts(root))
    emit("closed", native_origins=origins())
