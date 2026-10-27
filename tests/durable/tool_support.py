"""Isolated keyed fixture: attempts and deduplicated applications are independent."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace

from generation_support import Wire, definition, seed

from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)
from pipy_harness.native.tools.base import (
    ToolAnnotations,
    ToolDefinition,
    ToolExecutionResult,
    dispatch_tool_output,
)


def database(root):
    with sqlite3.connect(root / "effects.sqlite") as db:
        db.execute("CREATE TABLE IF NOT EXISTS calls (name TEXT,key TEXT,args TEXT)")
        db.execute(
            "CREATE TABLE IF NOT EXISTS applications (key TEXT PRIMARY KEY,args TEXT)"
        )
        db.execute("CREATE TABLE IF NOT EXISTS prepares (name TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS providers (body TEXT)")


def counts(root):
    with closing(sqlite3.connect(root / "effects.sqlite")) as db, db:
        return {
            name: db.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
            for name in ("calls", "applications", "prepares", "providers")
        }


class Keyed:
    def __init__(
        self,
        root,
        name="keyed",
        barrier=None,
        cut="",
        *,
        schema=None,
        hints=False,
        terminate=False,
    ):
        self.root, self.barrier, self.cut, self.terminate = (
            root,
            barrier,
            cut,
            terminate,
        )
        self.definition = ToolDefinition(
            name,
            name,
            schema
            or {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "prepared": {"type": "boolean"},
                },
            },
            annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True)
            if hints
            else None,
        )

    def prepare_arguments(self, raw):
        with sqlite3.connect(self.root / "effects.sqlite") as db:
            db.execute("INSERT INTO prepares VALUES (?)", (self.definition.name,))
        if self.cut == "prepare" and self.barrier:
            self.barrier(self.cut)
        return {**raw, "prepared": True}

    def invoke(self, request, context):
        # The fixture's replay contract requires this real durable task key.
        assert context.call_key.startswith("pipy-tool-")
        args = json.dumps(request.arguments, sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self.root / "effects.sqlite") as db:
            db.execute(
                "INSERT INTO calls VALUES (?,?,?)",
                (request.tool_name, context.call_key, args),
            )
        if self.cut == "invoke-before-effect" and self.barrier:
            self.barrier(self.cut)
        with sqlite3.connect(self.root / "effects.sqlite") as db:
            old = db.execute(
                "SELECT args FROM applications WHERE key=?", (context.call_key,)
            ).fetchone()
            assert old is None or old == (args,), "key divergence"
            db.execute(
                "INSERT OR IGNORE INTO applications VALUES (?,?)",
                (context.call_key, args),
            )
        if self.cut == "effect" and self.barrier:
            self.barrier(self.cut)
        return ToolExecutionResult(
            request.tool_request_id,
            "actual fixture output",
            provider_correlation_id=request.provider_correlation_id,
            details={"key": context.call_key},
            structured_content=None,
            terminate=self.terminate,
        )


def reporting_infrastructure_failure(root, request, context, result):
    """Lose reporting authority after a real effect, not via an invalid result."""
    assert type(result) is ToolExecutionResult
    with closing(sqlite3.connect(root / "effects.sqlite")) as db, db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM applications WHERE key=?", (context.call_key,)
            ).fetchone()[0]
            == 1
        )
    captured = []

    def capture_fault():
        captured.append(True)
        raise RuntimeError("synthetic reporting infrastructure failure")

    dispatch_tool_output(context.output_sink, capture_fault)
    assert captured == [True]  # actual capture raised and the void sink returned
    with closing(sqlite3.connect(root / "effects.sqlite")) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS reporting_faults "
            "(name TEXT,key TEXT,capture_ran INTEGER,void_returned INTEGER)"
        )
        db.execute(
            "INSERT INTO reporting_faults VALUES (?,?,1,1)",
            (request.tool_name, context.call_key),
        )
    return result  # the original valid result, with every field unchanged


def reporting_faults(root):
    with closing(sqlite3.connect(root / "effects.sqlite")) as db, db:
        return db.execute(
            "SELECT name,key,capture_ran,void_returned FROM reporting_faults"
        ).fetchall()


class InfrastructureFailureFirst(Keyed):
    def invoke(self, request, context):
        result = super().invoke(request, context)
        if self.definition.name == "keyed" and counts(self.root)["calls"] == 1:
            return reporting_infrastructure_failure(self.root, request, context, result)
        return result


class CountedWire(Wire):
    def __init__(self, root, names=("keyed", "later")):
        super().__init__(names)
        self.root, self.names = root, names

    def post_json(self, url, **kwargs):
        with sqlite3.connect(self.root / "effects.sqlite") as db:
            db.execute(
                "INSERT INTO providers VALUES (?)",
                (json.dumps(kwargs["body"], sort_keys=True),),
            )
        self.tool_calls = (
            ()
            if any(m["role"] == "tool" for m in kwargs["body"]["messages"])
            else self.names
        )
        return super().post_json(url, **kwargs)


def definitions(
    root,
    *,
    replay="unsafe",
    revision="1",
    schema=None,
    missing=False,
    barrier=None,
    cut="",
    hints=False,
    workspace_root=None,
):
    database(root)
    ports = [
        Keyed(
            root,
            name,
            barrier,
            cut if name == "keyed" else "",
            schema=schema if name == "keyed" else None,
            hints=hints,
        )
        for name in ("keyed", "later")
    ]
    registry = (
        {}
        if missing
        else {
            p.definition.name: ToolRegistration(
                p,
                "fixture." + p.definition.name,
                revision if p.definition.name == "keyed" else "1",
                replay,
            )
            for p in ports
        }
    )
    tool = define_tool_task(
        registry, workspace_root=root if workspace_root is None else workspace_root
    )
    generation = definition(root, CountedWire(root))
    # The model declaration excludes private executable hints/registration data.
    declared = tuple(
        ToolDefinition(
            p.definition.name, p.definition.description, p.definition.input_schema
        )
        for p in ports
    )
    snapshot = snapshot_provider_request(
        replace(seed(root).request, available_tools=declared)
    )
    return generation, tool, snapshot
