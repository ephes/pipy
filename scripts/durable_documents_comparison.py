"""DE04a bounded typed-document Pi/async-native oracle; compare0/1/invalid2.

Actual Pi Memory Session is paired with actual native Memory/SQLite/reopen,
24 recipes each. Blocking synchronous facade semantics are covered separately
by the real Session matrix; no same-Tx pending SyncTx operation is invented.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.util
import shutil
import sys
import threading
import traceback
import uuid
from collections import Counter
from functools import partial
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "typed_documents_parent", ROOT / "scripts/durable_sqlite_comparison.py"
)
assert spec and spec.loader
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
o, v, b = d.o, d.v, d.b
from pipy_harness.native.durable.delta_ops import own_operations  # noqa: E402
from pipy_harness.native.durable.documents import (  # noqa: E402
    DocFamily,
    Scope,
    define_doc,
    define_doc_family,
)  # noqa: E402
from pipy_harness.native.durable.memory import MemoryStorage  # noqa: E402
from pipy_harness.native.durable.session_core import Session  # noqa: E402
from pipy_harness.native.durable.sqlite import open_sqlite_storage  # noqa: E402
from pipy_harness.native.durable.storage import StorageWrite  # noqa: E402
from pipy_harness.native.durable.tasks import define_task  # noqa: E402
from pipy_harness.native.durable.tx import RawDocumentChange  # noqa: E402


async def unexecuted_task(*args: Any) -> None:
    raise AssertionError("Creation-only oracle cannot dispatch tasks")


PHASE = "session-documents-a-v1"
CASES = tuple(
    [
        *(f"A{i}" for i in range(1, 10)),
        *(f"T{i}" for i in range(1, 11)),
        "P1",
        "P2",
        "P3",
        "C1",
        "C2",
    ]
)
BACKENDS = ("memory", "sqlite", "reopen")
INPUT = ROOT / "tests/fixtures/durable/session-documents-v1.input.json"
MANIFEST = ROOT / "tests/fixtures/durable/session-documents-v1.manifest.json"
DRIVER = ROOT / "scripts/durable_reference/cases/session-documents.mts"
DESTINATION = "session-documents-a.mts"
SOURCES = {
    "packages/chord/src/cell.ts",
    "packages/chord/src/cluster.ts",
    "packages/chord/src/connection.ts",
    "packages/chord/src/context/index.ts",
    "packages/chord/src/contract.ts",
    "packages/chord/src/delta/apply-immutable-trusted.ts",
    "packages/chord/src/delta/diff.ts",
    "packages/chord/src/delta/index.ts",
    "packages/chord/src/delta/tracker.ts",
    "packages/chord/src/errors.ts",
    "packages/chord/src/host.ts",
    "packages/chord/src/index.ts",
    "packages/chord/src/json.ts",
    "packages/chord/src/link.ts",
    "packages/chord/src/object.ts",
    "packages/chord/src/publish.ts",
    "packages/chord/src/registry.ts",
    "packages/chord/src/reload.ts",
    "packages/chord/src/remote.ts",
    "packages/chord/src/schema.ts",
    "packages/chord/src/state.ts",
    "packages/chord/src/websocket.ts",
    "packages/chord/src/wire.ts",
    "packages/durable/src/documents.ts",
    "packages/durable/src/errors.ts",
    "packages/durable/src/ids.ts",
    "packages/durable/src/session/forks.ts",
    "packages/durable/src/session/observation.ts",
    "packages/durable/src/session/session.ts",
    "packages/durable/src/session/transaction.ts",
    "packages/durable/src/storage/memory.ts",
    "packages/durable/src/storage/scan.ts",
    "packages/durable/src/timers.ts",
    "packages/durable/src/types.ts",
    "packages/telemetry/src/index.ts",
    "packages/telemetry/src/memory.ts",
    "packages/telemetry/src/metrics.ts",
    "packages/telemetry/src/noop.ts",
}
UPSTREAM_TESTS = {
    "packages/durable/test/" + s
    for s in (
        "session-definitions.test.ts",
        "session-documents.test.ts",
        "session-tables.test.ts",
    )
}
CLOSURE = (
    d.CLOSURE
    | {str(p.relative_to(ROOT)) for p in (Path(__file__), DRIVER, INPUT)}
    | {
        "src/pipy_harness/native/durable/documents.py",
        "tests/durable/test_document_definitions.py",
        "tests/durable/test_session_documents.py",
        "docs/durable-session-deltas.md",
        "tests/durable/test_session_deltas.py",
        "tests/fixtures/durable/session-documents-deltas-v1.expected.json",
        "tests/durable/test_documents_comparison.py",
    }
)


def recipe() -> Any:
    value = d.load(INPUT)
    d.shape(
        value,
        {
            "schema_version",
            "phase",
            "clock",
            "retirement_count",
            "cases",
            "error_spellings",
        },
        "document input",
    )
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "document input identity",
    )
    d.require(
        type(value["clock"]) is int
        and value["clock"] == 1000
        and type(value["retirement_count"]) is int
        and value["retirement_count"] == 257,
        "document closed counts",
    )
    d.require(
        type(value["cases"]) is list and d.same(value["cases"], list(CASES)),
        "document closed recipes",
    )
    errors(value)
    return value


def task(id: int = 20, terminal: bool = False) -> Any:
    return {
        "id": id,
        "conversationId": 7,
        "kind": "probe.task",
        "version": 1,
        "input": None,
        "state": {
            "status": "terminal",
            "outcome": {"status": "completed", "result": {"ok": True}},
        }
        if terminal
        else {"status": "pending", "checkpoint": None},
        "background": False,
        "abortRequested": False,
    }


def base(
    id: int, kind: str = "probe.single", owner: int = 20, key: str | None = None
) -> Any:
    return {
        "type": "document.create",
        "record": {
            "id": id,
            "kind": kind,
            "scope": {"kind": "task", "taskId": owner},
            **({"key": key} if key is not None else {}),
        },
        "content": {"kind": "base", "version": 1, "value": {"lines": ["raw"]}},
    }


def dto(value: Any) -> Any:
    if type(value) in (tuple, list):
        return [dto(x) for x in value]
    if hasattr(value, "to_tree"):
        return value.to_tree()
    if type(value) is d.m.Cursor:
        return {
            "after": value.after,
            **({"order": value.order} if value.order is not None else {}),
        }
    return d.m._dto(value)


class Recording:
    """Private driver instrumentation; all operations delegate to actual Storage."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[Any] = []
        self.writes: list[Any] = []
        self.hold: Any = None
        self.lock = threading.Lock()

    def __getattr__(self, name: str) -> Any:
        return partial(self.call, name)

    def call(self, name: str, *args: Any) -> Any:
        count = (
            3
            if name == "scan_documents"
            else 2
            if name in {"find_document", "document"}
            else 0
            if name in {"close", "mint_id"}
            else 1
        )
        with self.lock:
            self.calls.append({"op": name, "args": v.encode(dto(args[:count]))})
            gate = (
                self.hold if self.hold is not None and self.hold["op"] == name else None
            )
            if gate is not None:
                self.hold = None
            if name == "commit":
                self.writes.extend(dto(args[0]))
        if gate is not None:
            gate["entered"].set()
            if not gate["release"].wait(10):
                raise RuntimeError("driver deadline")
        return getattr(self.inner, name)(*args)

    def gate(self) -> Any:
        gate = {
            "op": "find_document",
            "entered": threading.Event(),
            "release": threading.Event(),
        }
        self.hold = gate
        return gate


async def native_case(id: str, inner: Any, config: Any) -> Any:  # noqa: C901 - closed recipe interpreter
    def seed(*values: Any) -> Any:
        return inner.commit(tuple(StorageWrite(x) for x in values))

    seed(
        {"type": "conversation", "value": {"id": 1}},
        {"type": "conversation", "value": {"id": 7}},
        {"type": "task", "value": task()},
    )
    storage = Recording(inner)
    initial: list[Any] = []
    publications: list[Any] = []
    raw: list[Any] = []
    facts: dict[str, Any] = {}
    reports: list[Any] = []
    session = Session(
        storage,
        now=lambda: config["clock"],
        reporter=lambda e, info: reports.append(
            {"name": type(e).__name__, "message": str(e), "info": info}
        ),
    )
    session.observe_commits(publications.append)

    def make(
        scope: Scope = "session",
        family: bool = False,
        kind: str = "probe.single",
        version: int = 1,
    ) -> Any:
        def init(seed: Any = None) -> Any:
            initial.append(
                {
                    "scope": scope,
                    "family": family,
                    "kind": kind,
                    **({"seed": copy.deepcopy(seed)} if family else {}),
                }
            )
            return {"seed": seed, "hits": 0} if family else {"lines": []}

        return (define_doc_family if family else define_doc)(
            kind,
            version,
            scope,
            init,
            history="latest" if scope == "conversation" else None,
            fork="initial" if scope == "conversation" else None,
        )

    S, C, T = make(), make("conversation"), make("task")
    FS, FC, FT = make("session", True), make("conversation", True), make("task", True)

    def options(token: Any, args: tuple[Any, ...], *, acquire: bool = False) -> Any:
        values = list(args)
        result = (
            {}
            if token.scope == "session"
            else {
                "conversation_id"
                if token.scope == "conversation"
                else "task_id": values.pop(0)
            }
        )
        if type(token) is DocFamily:
            result["key"] = values.pop(0)
            if acquire:
                result["seed"] = values.pop(0)
        assert not values
        return result

    def doc(tx: Any, token: Any, *args: Any) -> Any:
        return tx.doc(token, **options(token, args, acquire=True))

    async def retire(tx: Any, token: Any, *args: Any) -> Any:
        return await tx.retire_doc(token, **options(token, args))

    async def snap(token: Any, *args: Any) -> Any:
        value = await session.asnapshot(token, **options(token, args))
        return None if value is None else value.value.thaw()

    async def capture(label: str, fn: Any) -> None:
        try:
            value = fn()
            if hasattr(value, "__await__"):
                value = await value
            facts[label] = {
                "status": "fulfilled",
                **({"value": value} if value is not None else {}),
            }
        except (TypeError, ValueError, RuntimeError) as e:
            facts[label] = {
                "status": "rejected",
                "error": {
                    "name": type(e).__name__,
                    "message": str(e),
                    "code": getattr(e, "code", None),
                },
            }
            raw.append(
                {
                    "id": id,
                    "label": label,
                    "name": type(e).__name__,
                    "message": str(e),
                    "code": getattr(e, "code", None),
                    "stack": traceback.format_exc(),
                }
            )

    async def commit(cb: Any) -> Any:
        return await session.acommit(cb)

    gate = None
    try:
        if id == "A1":

            async def write(tx: Any) -> None:
                for token, args in [
                    (S, ()),
                    (C, (7,)),
                    (T, (20,)),
                    (FS, ("k", {"n": 1})),
                    (FC, (7, "k", {"n": 2})),
                    (FT, (20, "k", {"n": 3})),
                ]:
                    value = await doc(tx, token, *args)
                    if "hits" in value:
                        value["hits"] += 1
                    else:
                        value["lines"].append("value")

            await commit(write)
            facts["values"] = [
                await snap(S),
                await snap(C, 7),
                await snap(T, 20),
                await snap(FS, "k"),
                await snap(FC, 7, "k"),
                await snap(FT, 20, "k"),
            ]
        elif id == "A2":

            async def write(tx: Any) -> None:
                await doc(tx, S)
                for key in ("", "k", "\ud800", "\U0001f600"):
                    await doc(tx, FS, key, key)
                facts["same"] = await doc(
                    tx, FS, "\ud83d\ude00", object()
                ) is await doc(tx, FS, "\U0001f600", None)
                await doc(tx, FC, 7, "k", 2)
                await doc(tx, FT, 20, "k", 3)

            await commit(write)
            facts["values"] = [
                await snap(S),
                await snap(FS, ""),
                await snap(FS, "k"),
                await snap(FS, "\ud800"),
                await snap(FS, "\U0001f600"),
                await snap(FC, 7, "k"),
                await snap(FT, 20, "k"),
            ]
        elif id in {"A3", "A4", "A5"}:
            gate = storage.gate()
            caller = {"nested": {"n": 1}}

            async def write(tx: Any) -> None:
                assert gate is not None
                first = doc(tx, FS, "k", caller)
                assert await asyncio.to_thread(gate["entered"].wait, 10)
                caller["nested"]["n"] = 99
                second = doc(tx, FS, "k", object() if id == "A5" else {"tag": "second"})
                facts["same_pending"] = first is second
                gate["release"].set()
                facts["same"] = await first is await second

            await commit(write)
            facts["value"] = await snap(FS, "k")
        elif id in {"A6", "A7", "A8"}:
            if id == "A6":
                await commit(lambda tx: doc(tx, FS, "k", "stored"))
                await capture(
                    "invalid", lambda: commit(lambda tx: doc(tx, FS, "k", object()))
                )

                async def update(tx: Any) -> None:
                    value = await doc(tx, FS, "k", "unused")
                    value["hits"] += 1

                await commit(update)
            elif id == "A7":

                async def replace(tx: Any) -> None:
                    first = await doc(tx, FS, "k", "old")
                    await retire(tx, FS, "k")
                    facts["same"] = first is await doc(tx, FS, "k", "new")

                await commit(replace)

                async def invalid(tx: Any) -> None:
                    await retire(tx, FS, "k")
                    await doc(tx, FS, "k", object())

                await capture("invalid", lambda: commit(invalid))
            else:

                async def same(tx: Any) -> None:
                    first = await doc(tx, FS, "k", "first")
                    facts["same"] = (
                        await doc(tx, make("session", True, version=2), "k", object())
                        is first
                    )

                await commit(same)
                await capture(
                    "version",
                    lambda: commit(
                        lambda tx: doc(tx, make("session", True, version=2), "k", None)
                    ),
                )
            facts["value"] = await snap(FS, "k")
        elif id == "A9":
            tokens = [
                (S, ()),
                (C, (7,)),
                (T, (20,)),
                (FS, ("k",)),
                (FC, (7, "k")),
                (FT, (20, "k")),
            ]
            facts["values"] = [await snap(token, *args) for token, args in tokens]

            async def remove(tx: Any) -> None:
                for token, args in tokens:
                    await retire(tx, token, *args)

            await commit(remove)
        elif id == "T1":
            await capture("missing", lambda: commit(lambda tx: doc(tx, T, 4242)))
            seed({"type": "task", "value": task(terminal=True)})
            await capture("terminal", lambda: commit(lambda tx: doc(tx, T, 20)))
        elif id in {"T2", "T3"}:
            owner = 20 if id == "T2" else 4242
            seed(
                *(
                    [{"type": "task", "value": task(terminal=True)}]
                    if id == "T2"
                    else []
                ),
                base(100, owner=owner),
            )

            async def loaded(tx: Any) -> None:
                (await doc(tx, T, owner))["lines"].append("loaded")

            await commit(loaded)
            facts["value"] = await snap(T, owner)
        elif id in {"T4", "T5"}:
            seed(base(100))

            async def terminal(tx: Any) -> None:
                value = await doc(tx, T, 20) if id == "T5" else None
                tx.set_task(task(terminal=True))
                await capture("single", lambda: doc(tx, T, 20))
                await capture("family", lambda: doc(tx, FT, 20, "late", object()))
                if value is not None:
                    value["lines"].append("final")

            await commit(terminal)
            facts["value"] = await snap(T, 20)
        elif id in {"T6", "T7"}:

            async def new(tx: Any) -> None:
                n = await tx.create_task(
                    define_task(
                        "probe.task", 1, lambda _input: None, {}, unexecuted_task
                    ),
                    None,
                    ownership={"kind": "conversation"},
                    conversation_id=7,
                )
                (await doc(tx, T, n))["lines"].append("new")
                await doc(tx, FT, n, "one", None)
                if id == "T7":
                    tx.set_task(task(n, True))
                facts["task"] = n

            await commit(new)
            facts["values"] = [
                await snap(T, facts["task"]),
                await snap(FT, facts["task"], "one"),
            ]
        elif id in {"T8", "T9"}:
            if id == "T9":
                seed(base(100))
            gate = storage.gate()

            async def held(tx: Any) -> None:
                assert gate is not None
                pending = doc(tx, T, 20)
                assert await asyncio.to_thread(gate["entered"].wait, 10)
                tx.set_task(task(terminal=True))
                gate["release"].set()

                async def acquired() -> None:
                    value = await pending
                    if id == "T9":
                        value["lines"].append("last")

                await capture("acquire", acquired)

            await commit(held)
            facts["value"] = await snap(T, 20)
        elif id == "T10":
            seed({"type": "task", "value": task(terminal=True)}, base(100))
            await commit(lambda tx: retire(tx, make("task", version=2), 20))
            facts["value"] = await snap(T, 20)
        elif id in {"P1", "P2"}:
            seed(
                *(
                    base(
                        1000 + i,
                        "unknown." + str(i),
                        key=None if i % 3 == 0 else "" if i % 3 == 1 else "k",
                    )
                    for i in range(config["retirement_count"])
                )
            )

            async def finish(tx: Any) -> None:
                if id == "P2":
                    await retire(tx, make("task", kind="unknown.0"), 20)
                    (await doc(tx, make("task", True, "unknown.1"), 20, "", None))[
                        "lines"
                    ].append("final")
                tx.set_task(task(terminal=True))

            await commit(finish)
            facts["remaining"] = len(
                inner.scan_documents(
                    d.m.DocumentQuery(
                        {"scope": {"kind": "task", "taskId": 20}, "at": "current"}
                    ),
                    300,
                ).items
            )
        elif id == "P3":
            seed({"type": "task", "value": task(terminal=True)})
            await capture(
                "replacement",
                lambda: commit(lambda tx: tx.set_task(task(terminal=True))),
            )
        elif id == "C1":

            async def unrelated(tx: Any) -> None:
                await tx.create_conversation(ownership={"kind": "ownerless"})
                (await doc(tx, T, 20))["lines"].append("owner")

            await commit(unrelated)
            facts["value"] = await snap(T, 20)
        elif id == "C2":

            async def members(tx: Any) -> None:
                for key in ("", "a", "b"):
                    await doc(tx, FT, 20, key, key)

            await commit(members)
            old = await snap(FT, 20, "")
            await commit(lambda tx: tx.set_task(task(terminal=True)))
            # Pi's async listener snapshots queue after adoption; explicitly read the
            # same addresses after native synchronous publication has returned.
            facts["identity"] = [
                (await snap(FT, 20, key)) is None for key in ("", "a", "b")
            ]
            facts["old"] = old
            facts["values"] = [await snap(FT, 20, key) for key in ("", "a", "b")]
        published = [
            {
                "record": c.record.to_tree(),
                "conversationId": c.conversation_id,
                "version": c.version,
                "value": None if c.value is None else c.value.thaw(),
                "ops": c.ops.thaw(),
            }
            for p in publications
            for c in p.changes
            if type(c) is RawDocumentChange
        ]
        profile = {
            "facts": facts,
            "initial": initial,
            "writes": storage.writes,
            "counts": dict(Counter(c["op"] for c in storage.calls)),
            "scans": [c["args"] for c in storage.calls if c["op"] == "scan_documents"],
            "publications": published,
        }
        return {"id": id, "profile": v.encode(profile)}, {
            "id": id,
            "calls": storage.calls,
            "errors": raw,
            "reports": reports,
        }
    finally:
        if gate is not None:
            gate["release"].set()
        await asyncio.wait_for(session.aclose(), 10)


async def native(folder: Path, config: Any | None = None) -> Any:
    config = recipe() if config is None else config
    folder.mkdir(mode=0o700, exist_ok=False)
    profiles, raw = [], []
    for backend in BACKENDS:
        cases, traces = [], []
        for id in config["cases"]:
            path = folder / (backend + "-" + id + ".sqlite")
            inner = (
                MemoryStorage()
                if backend == "memory"
                else (
                    d.ReopeningStorage(path)
                    if backend == "reopen"
                    else open_sqlite_storage(str(path), mode="create")
                )
            )
            observed, errors = await asyncio.wait_for(
                native_case(id, inner, config), 30
            )
            cases.append(observed)
            traces.append(errors)
        profiles.append({"backend": backend, "cases": cases})
        raw.append({"backend": backend, "cases": traces})
    return {"schema_version": 1, "phase": PHASE, "backends": profiles}, raw


ERROR_POINTS = {
    ("A6", "invalid"),
    ("A7", "invalid"),
    ("A8", "version"),
    ("T1", "missing"),
    ("T1", "terminal"),
    ("T4", "single"),
    ("T4", "family"),
    ("T5", "single"),
    ("T5", "family"),
    ("T8", "acquire"),
    ("P3", "replacement"),
}


def errors(config: Any) -> Any:
    values = config["error_spellings"]
    d.require(
        type(values) is list and len(values) == len(ERROR_POINTS),
        "Closed document spellings",
    )
    seen = set()
    for row in values:
        d.shape(row, {"case", "fact", "pi", "native"}, "Document spelling")
        d.require(
            type(row["case"]) is str and type(row["fact"]) is str,
            "Document spelling identity",
        )
        point = (row["case"], row["fact"])
        d.require(
            point in ERROR_POINTS and point not in seen, "Document spelling point"
        )
        seen.add(point)
        for role in ("pi", "native"):
            d.shape(row[role], {"name", "message", "code"}, "Document raw error")
            d.require(
                type(row[role]["name"]) is str
                and type(row[role]["message"]) is str
                and (row[role]["code"] is None or type(row[role]["code"]) is str),
                "Document raw error scalar",
            )
        d.require(
            not d.same(row["pi"], row["native"]), "Unused equal document spelling"
        )
    return values


def publication_shape(value: Any) -> None:
    d.shape(
        value,
        {"record", "conversationId", "version", "value", "ops"},
        "document publication",
    )
    try:
        own_operations(value["ops"])
    except (TypeError, ValueError):
        d.require(False, "Malformed document operations")
    retired = value["value"] is None
    d.require((value["version"] is None) == retired, "Document publication version")
    d.require(not retired or value["ops"] == [], "Retirement has operations")
    d.require(retired or type(value["value"]) is dict, "Document publication value")


def trace(value: Any, *, reference: bool) -> Any:
    d.shape(
        value,
        {"schema_version", "phase", "cases"}
        if reference
        else {"schema_version", "phase", "backends"},
        "document trace",
    )
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "document trace identity",
    )
    if reference:
        lanes = [value]
    else:
        d.require(
            type(value["backends"]) is list
            and tuple(b.get("backend") for b in value["backends"]) == BACKENDS,
            "document backend inventory",
        )
        lanes = value["backends"]
        for lane in lanes:
            d.shape(lane, {"backend", "cases"}, "document backend")
    for lane in lanes:
        d.require(
            type(lane["cases"]) is list and len(lane["cases"]) == len(CASES),
            "document recipe inventory",
        )
        for row, id in zip(lane["cases"], CASES, strict=True):
            d.shape(row, {"id", "profile"}, "document case")
            d.require(row["id"] == id, "document case identity")
            d.m._raw_tag(row["profile"])
            profile = v.decode(row["profile"])
            d.shape(
                profile,
                {"facts", "initial", "writes", "counts", "scans", "publications"},
                "document profile",
            )
            d.require(
                type(profile["facts"]) is dict
                and all(
                    type(profile[k]) is list
                    for k in ("initial", "writes", "scans", "publications")
                ),
                "document profile containers",
            )
            for publication in profile["publications"]:
                publication_shape(publication)
            d.require(type(profile["counts"]) is dict, "document counters")
            for op, count in profile["counts"].items():
                d.require(
                    type(op) is str
                    and op
                    in {
                        "mint_id",
                        "commit",
                        "find_document",
                        "document",
                        "scan_documents",
                        "task",
                        "conversation",
                    }
                    and type(count) is int
                    and 0 <= count <= 10000,
                    "document counter scalar",
                )
    return value


def comparison(expected: Any, actual: Any, config: Any | None = None) -> int:
    config = recipe() if config is None else config
    trace(expected, reference=True)
    trace(actual, reference=False)
    spellings = errors(config)
    reference = {row["id"]: v.decode(row["profile"]) for row in expected["cases"]}
    for row in spellings:
        observed = reference[row["case"]]["facts"].get(row["fact"])
        d.require(
            type(observed) is dict
            and observed.get("status") == "rejected"
            and d.same(observed.get("error"), row["pi"]),
            "Stale Pi document error spelling",
        )
    for backend in projected_native(actual, config)["backends"]:
        for row in backend["cases"]:
            if not d.same(v.decode(row["profile"]), reference[row["id"]]):
                return 1
    return 0


def projected_native(actual: Any, config: Any) -> Any:
    result = copy.deepcopy(trace(actual, reference=False))
    for backend in result["backends"]:
        for row in backend["cases"]:
            observed = v.decode(row["profile"])
            for spelling in errors(config):
                if spelling["case"] != row["id"]:
                    continue
                fact = observed["facts"].get(spelling["fact"])
                # Potential native spelling only: unused entries do not hide behavior bugs.
                if (
                    type(fact) is dict
                    and fact.get("status") == "rejected"
                    and d.same(fact.get("error"), spelling["native"])
                ):
                    fact["error"] = copy.deepcopy(spelling["pi"])
            row["profile"] = v.encode(observed)
    return result


def parents() -> Any:
    return d.parents() | {"sqlite": o.sha(d.MANIFEST)}


def configuration() -> Any:
    config = d.load(MANIFEST)
    d.shape(
        config,
        {
            "schema_version",
            "phase",
            "reference_commit",
            "parents",
            "closure",
            "reference_sources",
            "upstream_tests",
        },
        "document manifest",
    )
    d.require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN,
        "document manifest identity",
    )
    for role, inventory in (
        ("closure", CLOSURE),
        ("reference_sources", SOURCES),
        ("upstream_tests", UPSTREAM_TESTS),
    ):
        d.shape(config[role], inventory, "document " + role)
        for path, digest in config[role].items():
            o.contract.digest(digest)
            if role == "closure":
                d.require(o.sha(ROOT / path) == digest, "Document closure drift")
    d.require(d.same(config["parents"], parents()), "Document parent drift")
    d.configuration()
    errors(recipe())
    return config


def attest_sqlite(folder: Path, acceptance: Path) -> Any:
    result = d.load(folder / "result.json")
    d.shape(
        result,
        {
            "schema_version",
            "phase",
            "status",
            "execution_id",
            "fresh_reference_bootstrap",
            "parent_execution_id",
            "parent_acceptance_sha256",
            "reference_run_sha256",
            "manifest_sha256",
            "parents",
            "module_sha256",
            "artifacts_sha256",
            "mutants",
            "scripted_scenarios_per_backend",
            "scripted_operations_per_backend",
            "exported_cases_per_reference_backend",
            "reference_backends",
            "native_backends",
            "product_integration",
            "full_storage_parity",
            "d1_evidence",
        },
        "SQLite predecessor result",
    )
    expected = {
        "schema_version": 1,
        "phase": d.PHASE,
        "status": "PASS",
        "fresh_reference_bootstrap": False,
        "parent_execution_id": d.load(acceptance / "acceptance.json")["execution_id"],
        "parent_acceptance_sha256": o.sha(acceptance / "acceptance.json"),
        "reference_run_sha256": o.sha(acceptance / "reference/run.json"),
        "manifest_sha256": o.sha(d.MANIFEST),
        "parents": d.parents(),
        "scripted_scenarios_per_backend": 34,
        "scripted_operations_per_backend": 258,
        "exported_cases_per_reference_backend": 21,
        "reference_backends": list(d.REFERENCE),
        "native_backends": list(d.NATIVE),
        "product_integration": False,
        "full_storage_parity": False,
        "d1_evidence": d.configuration()["d1_evidence"],
    }
    for key in (
        "schema_version",
        "scripted_scenarios_per_backend",
        "scripted_operations_per_backend",
        "exported_cases_per_reference_backend",
    ):
        d.require(type(result[key]) is int, "SQLite predecessor counter type")
    d.require(
        all(d.same(result[k], v) for k, v in expected.items()),
        "SQLite predecessor binding drift",
    )
    d.require(
        type(result["execution_id"]) is str
        and str(uuid.UUID(result["execution_id"])) == result["execution_id"],
        "SQLite execution identity",
    )
    d.require(
        not any(p.is_symlink() for p in folder.rglob("*")),
        "SQLite predecessor artifact symlink",
    )
    actual = {
        str(p.relative_to(folder)): o.sha(p)
        for p in folder.rglob("*")
        if p.is_file() and p.name != "result.json"
    }
    d.require(
        d.same(actual, result["artifacts_sha256"]), "SQLite predecessor artifacts drift"
    )
    d.require(
        d.comparison(d.load(folder / "observed.json"), d.load(folder / "native.json"))
        == 0,
        "SQLite predecessor mismatch",
    )
    d.require(
        type(result["mutants"]) is list
        and tuple(row.get("id") for row in result["mutants"]) == d.MUTANTS,
        "SQLite predecessor controls",
    )
    for row in result["mutants"]:
        d.shape(
            row,
            {"id", "schema_valid", "positive_exit", "negative_exit", "sha256"},
            "SQLite predecessor control",
        )
        d.require(
            d.same(
                {k: row[k] for k in ("schema_valid", "positive_exit", "negative_exit")},
                {"schema_valid": True, "positive_exit": 0, "negative_exit": 1},
            )
            and row["sha256"] == o.sha(folder / (row["id"] + "-mutant.json")),
            "SQLite predecessor control drift",
        )
    modules = o.verify_resolutions(
        acceptance / "reference/snapshot",
        d.load(folder / "resolutions.json"),
        d.load(acceptance / "reference/source-sha256.json"),
        {d.DESTINATION: o.sha(d.DRIVER)},
        d.MODULES,
    )
    d.require(
        d.same(result["module_sha256"], modules), "SQLite predecessor modules drift"
    )
    return result


MUTANTS = (
    "family-key",
    "seed-alias",
    "terminal-memo",
    "retirement-page",
    "task-owner",
    "retired-cache",
    "publication-ops",
    "default-base",
)


def mutate(actual: Any, name: str) -> Any:
    result = copy.deepcopy(actual)
    case = {
        "family-key": "A2",
        "seed-alias": "A4",
        "terminal-memo": "T5",
        "retirement-page": "P1",
        "task-owner": "C1",
        "retired-cache": "T7",
        "publication-ops": "T2",
        "default-base": "T2",
    }[name]
    row = next(r for r in result["backends"][0]["cases"] if r["id"] == case)
    profile = v.decode(row["profile"])
    if name == "family-key":
        next(w for w in profile["writes"] if w["record"].get("key") == "")["record"][
            "key"
        ] = "changed"
    elif name == "seed-alias":
        profile["facts"]["value"]["seed"]["nested"]["n"] = 99
    elif name == "terminal-memo":
        profile["facts"]["single"] = {"status": "fulfilled"}
    elif name == "retirement-page":
        profile["publications"].pop()
    elif name == "task-owner":
        profile["publications"][0]["conversationId"] = 1
    elif name == "publication-ops":
        profile["publications"][0]["ops"] = []
    elif name == "default-base":
        write = next(w for w in profile["writes"] if w["type"] == "document.change")
        write["content"] = {
            "kind": "base",
            "version": 1,
            "value": {"lines": ["raw", "loaded"]},
        }
    else:
        profile["facts"]["values"][0] = {"lines": ["new"]}
    row["profile"] = v.encode(profile)
    return result


def execute(acceptance: Path, sqlite: Path, output: Path) -> int:
    d.require(
        not output.exists() and not output.is_symlink(), "Document output entry exists"
    )
    output = output.absolute()
    acceptance, sqlite = acceptance.resolve(strict=True), sqlite.resolve(strict=True)
    d.require(
        not output.resolve().is_relative_to(acceptance)
        and not output.resolve().is_relative_to(sqlite),
        "Document output inside predecessor",
    )
    config, recipes = configuration(), recipe()
    identity = o.sha(MANIFEST)
    parent = o.manifest()
    v.artifacts.attest(acceptance, o.FIXTURES)
    run = o.acceptance.attest_reference(o, acceptance / "reference", parent, o.FIXTURES)
    sqlite_result = attest_sqlite(sqlite, acceptance)
    predecessor_sha = o.sha(sqlite / "result.json")
    snapshot = acceptance / "reference/snapshot"
    o.verify_sources(snapshot, config["reference_sources"])
    o.verify_sources(snapshot, config["upstream_tests"])
    driver = snapshot / DESTINATION
    d.require(
        not driver.exists() and not driver.is_symlink(),
        "Document driver destination exists",
    )
    output.mkdir(mode=0o700, exist_ok=False)
    d.dump(output / "input.json", recipes)
    shutil.copyfile(DRIVER, driver)
    env = o.child_env(output)
    for runtime in ("node", "npm"):
        stdout, stderr, code = d.process(
            [runtime, "--version"], snapshot, output, runtime + "-version", env, 15
        )
        d.require(
            code == 0
            and not stderr
            and stdout.decode().strip() == parent["runtime"][runtime],
            "Document runtime drift",
        )
    stdout, stderr, code = d.process(
        [
            "node",
            "--import",
            str(snapshot / o.RESOLVER),
            str(driver),
            str(output / "input.json"),
            str(output),
        ],
        snapshot,
        output,
        "pi-documents",
        env,
    )
    d.require(code == 0 and not stdout and not stderr, "Pi document driver failed")
    modules = o.verify_resolutions(
        snapshot,
        d.load(output / "resolutions.json"),
        d.load(acceptance / "reference/source-sha256.json"),
        {DESTINATION: o.sha(DRIVER)},
        b.MODULES,
    )
    o.module_dependencies(snapshot, modules, run["installed_dependencies"])
    dependencies = d.load(acceptance / "reference/dependency-sha256.json")
    for name, digest in modules.items():
        d.require(
            name in config["reference_sources"]
            or name == DESTINATION
            or (
                "node_modules" in Path(name).parts and dependencies.get(name) == digest
            ),
            "Unbound document import",
        )
    stdout, stderr, code = d.process(
        [
            sys.executable,
            "-I",
            str(Path(__file__).resolve()),
            "_native",
            str(output / "native.json"),
            str(output / "native-raw.json"),
            str(output / "stores"),
        ],
        ROOT,
        output,
        "native-documents",
        {"PATH": env["PATH"], "HOME": env["HOME"]},
    )
    d.require(code == 0 and not stdout and not stderr, "Native document driver failed")
    expected, actual = d.load(output / "observed.json"), d.load(output / "native.json")
    result = comparison(expected, actual, recipes)
    d.dump(output / "native-projected.json", projected_native(actual, recipes))
    controls = []
    if result:
        d.dump(
            output / "mismatch-report.json",
            {
                "expected_sha256": o.sha(output / "observed.json"),
                "actual_sha256": o.sha(output / "native.json"),
            },
        )
    else:
        folder = output / "mutants"
        folder.mkdir(mode=0o700)
        for name in MUTANTS:
            path = folder / (name + ".json")
            d.dump(path, mutate(actual, name))
            for label, source, wanted in (
                ("positive", output / "native.json", 0),
                ("negative", path, 1),
            ):
                _, _, exit = d.process(
                    [
                        sys.executable,
                        "-I",
                        str(Path(__file__).resolve()),
                        "compare",
                        str(output / "observed.json"),
                        str(source),
                    ],
                    ROOT,
                    output,
                    name + "-" + label,
                    {"PATH": env["PATH"], "HOME": env["HOME"]},
                    30,
                )
                d.require(
                    exit == wanted,
                    "Document control must be valid mismatch1, not invalid2",
                )
            controls.append(
                {
                    "id": name,
                    "positive_exit": 0,
                    "negative_exit": 1,
                    "sha256": o.sha(path),
                }
            )
    o.acceptance.attest_reference(o, acceptance / "reference", parent, o.FIXTURES)
    v.artifacts.attest(acceptance, o.FIXTURES)
    attest_sqlite(sqlite, acceptance)
    configuration()
    d.require(
        identity == o.sha(MANIFEST)
        and predecessor_sha == o.sha(sqlite / "result.json")
        and o.sha(driver) == o.sha(DRIVER),
        "Document execution identity drift",
    )
    artifacts = {
        str(p.relative_to(output)): o.sha(p)
        for p in output.rglob("*")
        if p.is_file() and p.name != "result.json"
    }
    d.dump(
        output / "result.json",
        {
            "schema_version": 1,
            "phase": PHASE,
            "status": "PASS" if result == 0 else "mismatch",
            "execution_id": str(uuid.uuid4()),
            "fresh_reference_bootstrap": False,
            "parent_execution_id": d.load(acceptance / "acceptance.json")[
                "execution_id"
            ],
            "parent_acceptance_sha256": o.sha(acceptance / "acceptance.json"),
            "sqlite_execution_id": sqlite_result["execution_id"],
            "sqlite_result_sha256": predecessor_sha,
            "manifest_sha256": identity,
            "parents": parents(),
            "module_sha256": modules,
            "artifacts_sha256": artifacts,
            "mutants": controls,
            "reference_cases": len(expected["cases"]),
            "native_cases": {
                lane["backend"]: len(lane["cases"]) for lane in actual["backends"]
            },
            "native_mode": "async",
            "product_integration": False,
            "full_document_parity": False,
        },
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("expected", type=Path)
    compare.add_argument("actual", type=Path)
    run = commands.add_parser("run")
    run.add_argument("--acceptance", type=Path, required=True)
    run.add_argument("--sqlite", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    internal = commands.add_parser("_native")
    internal.add_argument("output", type=Path)
    internal.add_argument("raw", type=Path)
    internal.add_argument("stores", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "compare":
            return comparison(d.load(args.expected), d.load(args.actual))
        if args.command == "_native":
            observed, raw = asyncio.run(asyncio.wait_for(native(args.stores), 120))
            d.dump(args.output, observed)
            d.dump(args.raw, raw)
            return 0
        return execute(args.acceptance, args.sqlite, args.output)
    except Exception as error:  # noqa: BLE001 - fail closed; BaseException still propagates
        print("INVALID: " + type(error).__name__ + ": " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
