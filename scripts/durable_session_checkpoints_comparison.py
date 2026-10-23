"""Actual Session checkpoint/unload pairing: equal0, semantic mismatch1, invalid2.

Native Memory/SQLite/reopen must first agree without any projection. The Pi
comparison alone uses the delivered JS member-order profile and the explicit
persisted-value decision for signed-zero recipes. Raw observations are retained.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import dataclasses
import hashlib
import importlib.util
import inspect
import json
import lzma
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def module(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


a = module("checkpoint_documents", "durable_documents_comparison.py")
t = module("checkpoint_tracker", "durable_draft_tracker_comparison.py")
d, o, v = a.d, a.o, a.v
from pipy_harness.native.durable.codec import OwnedJson  # noqa: E402
from pipy_harness.native.durable.delta_ops import (  # noqa: E402
    apply_operations,
    own_operations,
)
from pipy_harness.native.durable.documents import (  # noqa: E402
    define_doc,
    define_doc_family,
)
from pipy_harness.native.durable.memory import MemoryStorage  # noqa: E402
from pipy_harness.native.durable.session import SessionClosedError  # noqa: E402
from pipy_harness.native.durable.session_core import (  # noqa: E402
    ReentrantSessionCall,
    Session,
)
from pipy_harness.native.durable.sqlite import open_sqlite_storage  # noqa: E402
from pipy_harness.native.durable.storage import (  # noqa: E402
    DocumentAddress,
    StorageWrite,
)
from pipy_harness.native.durable.tx import DraftRevoked, RawDocumentChange  # noqa: E402

PHASE = "session-checkpoints-j-v1"
PROFILE = "binary64-utf16-ordered-v1"
BACKENDS = ("memory", "sqlite", "reopen")
CASES = (
    *("counts-" + policy for policy in ("absent", "false", "true", "count>=2")),
    *(
        "scope-" + scope + "-" + family
        for scope in ("conversation", "task")
        for family in ("false", "true")
    ),
    "structural-equal",
    "empty-policy-skip",
    "dirty-retired",
    "clean-retired",
    "terminal-pages",
    "second-policy-rollback",
    "root-fallback",
    "creation-final-zero",
    *(
        "zero-" + "-".join((policy, zero, variant, dirty))
        for policy in ("absent", "false", "true")
        for zero in ("positive", "negative")
        for variant in ("simple", "accessed", "untouched")
        for dirty in ("empty", "dirty")
    ),
)
INPUT = ROOT / "tests/fixtures/durable/session-checkpoints-v1.input.json"
EXPECTED = ROOT / "tests/fixtures/durable/session-checkpoints-v1.expected.json"
MANIFEST = ROOT / "tests/fixtures/durable/session-checkpoints-v1.manifest.json"
DRIVER = ROOT / "scripts/durable_reference/cases/session-checkpoints.mts"
DESTINATION = "session-checkpoints-j.mts"
SOURCES = a.SOURCES | d.SOURCES
MODULES = a.SOURCES | (
    set(d.MODULES) - {"packages/durable/src/testing/storage-conformance.ts"}
)
UPSTREAM_TESTS = a.UPSTREAM_TESTS | {
    "packages/durable/test/session-checkpoints-migrations.test.ts",
}
CLOSURE = (
    a.CLOSURE
    | t.CLOSURE
    | {str(p.relative_to(ROOT)) for p in (Path(__file__), INPUT, EXPECTED, DRIVER)}
    | {
        "src/pipy_harness/native/durable/documents.py",
        "src/pipy_harness/native/durable/tx.py",
        "src/pipy_harness/native/durable/session.py",
        "src/pipy_harness/native/durable/session_core.py",
        "tests/durable/test_document_definitions.py",
        "tests/durable/test_session_tx_core.py",
        "tests/durable/test_session_commit.py",
        "tests/durable/test_session_deltas.py",
        "tests/durable/test_session_sqlite.py",
        "tests/durable/test_session_commit_comparison.py",
        "tests/durable/test_session_checkpoints.py",
        "tests/durable/test_session_callback_authority.py",
        "tests/durable/test_session_unload.py",
        "tests/durable/test_session_checkpoints_comparison.py",
        "docs/durable-session-checkpoints.md",
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
            "terminal_members",
            "profile",
            "working_probe",
            "cases",
        },
        "checkpoint input",
    )
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "checkpoint identity",
    )
    d.require(
        type(value["clock"]) is int
        and value["clock"] == 1000
        and type(value["terminal_members"]) is int
        and value["terminal_members"] == 257,
        "checkpoint constants",
    )
    d.require(
        value["profile"] == PROFILE
        and value["working_probe"]
        == "derived-plain-mirror-only; no live Draft traversal",
        "checkpoint profile",
    )
    d.require(
        type(value["cases"]) is list and len(value["cases"]) == 52,
        "checkpoint recipe count",
    )
    d.require(
        tuple(row.get("id") for row in value["cases"] if type(row) is dict) == CASES,
        "checkpoint recipe inventory",
    )
    seen = set()
    for row in value["cases"]:
        d.shape(
            row,
            {"id", "base", "steps", "policy", "scope", "family", "special", "creation"},
            "checkpoint recipe",
        )
        d.require(
            type(row["id"]) is str and row["id"] not in seen, "checkpoint recipe ID"
        )
        seen.add(row["id"])
        d.require(
            row["policy"] in ("absent", "false", "true", "count>=2")
            and row["scope"] in ("session", "conversation", "task")
            and type(row["family"]) is bool,
            "checkpoint policy/scope",
        )
        d.require(
            row["special"] in ("ordinary", "retire", "terminal", "rollback", "root"),
            "checkpoint special",
        )
        t.valid_profile(row["base"])
        d.require(type(v.decode(row["base"])) is dict, "checkpoint base object")
        d.require(type(row["steps"]) is list and row["steps"], "checkpoint steps")
        for commands in [row["creation"], *row["steps"]]:
            d.require(type(commands) is list, "checkpoint action list")
            for action in commands:
                d.require(
                    type(action) is list and len(action) in (2, 3), "checkpoint action"
                )
                op, path = action[:2]
                d.require(
                    type(op) is str and op in ("set", "flip", "pop-reinsert", "bulk"),
                    "checkpoint action name",
                )
                d.require(
                    type(path) is list and all(type(k) in (str, int) for k in path),
                    "checkpoint action path",
                )
                if op == "set":
                    d.require(len(action) == 3 and path, "checkpoint set")
                    t.valid_profile(action[2])
                elif op == "bulk":
                    d.require(
                        len(action) == 3
                        and action[2] == 4097
                        and type(action[2]) is int
                        and not path,
                        "checkpoint bulk",
                    )
                else:
                    d.require(len(action) == 2 and path, "checkpoint action arity")
    return value


def at(value: Any, path: list[Any]) -> Any:
    for key in path:
        value = value[key]
    return value


def actions(value: Any, commands: Any) -> None:
    for action in commands:
        op, path = action[:2]
        if op == "set":
            at(value, path[:-1])[path[-1]] = v.decode(action[2])
        elif op == "flip":
            parent, key = at(value, path[:-1]), path[-1]
            old = parent[key]
            parent[key] = 1
            parent[key] = -float(old)
        elif op == "pop-reinsert":
            array = at(value, path)
            old = array.pop(0)
            array.insert(0, old)
        else:
            for index in range(action[2]):
                value["field" + str(index)] = 1


def task(terminal: bool = False) -> Any:
    return {
        "id": 20,
        "conversationId": 7,
        "kind": "checkpoint.task",
        "version": 1,
        "input": None,
        "state": {
            "status": "terminal",
            "outcome": {"status": "completed", "result": None},
        }
        if terminal
        else {"status": "pending", "checkpoint": None},
        "background": False,
        "abortRequested": False,
    }


class Recording:
    def __init__(self, inner: Any, effects: list[str]) -> None:
        self.inner, self.effects = inner, effects
        self.writes: list[Any] = []

    def commit(self, writes: Any) -> Any:
        result = self.inner.commit(writes)
        self.writes.append([w.to_tree() for w in writes])
        self.effects.append("commit")
        return result

    def scan_documents(self, *args: Any) -> Any:
        self.effects.append("scan")
        return self.inner.scan_documents(*args)

    def submission(self, *args: Any) -> Any:
        self.effects.append("submission")
        return self.inner.submission(*args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def frozen_info(info: Any) -> bool:
    try:
        info.deltas_since_base = info.deltas_since_base
    except dataclasses.FrozenInstanceError:
        return True
    return False


async def native_case(row: Any, inner: Any, path: Path, backend: str) -> Any:  # noqa: C901 - closed recipe execution
    inner.commit(
        tuple(
            StorageWrite(w)
            for w in (
                {"type": "conversation", "value": {"id": 1}},
                {"type": "conversation", "value": {"id": 7}},
                {"type": "task", "value": task()},
            )
        )
    )
    if row["special"] == "terminal":
        inner.commit(
            tuple(
                StorageWrite(
                    {
                        "type": "document.create",
                        "record": {
                            "id": 1000 + i,
                            "kind": f"unknown.{i}",
                            "scope": {"kind": "task", "taskId": 20},
                        },
                        "content": {"kind": "base", "version": 1, "value": {"n": 0}},
                    }
                )
                for i in range(257)
            )
        )
    effects: list[str] = []
    storage = Recording(inner, effects)
    publications: list[Any] = []
    callbacks: list[Any] = []
    roots: list[Any] = []
    reports: list[Any] = []
    fail_second = False
    sentinel = ValueError("second policy")

    def make_session() -> Session:
        session = Session(
            storage, now=lambda: 1000, reporter=lambda *args: reports.append(args)
        )

        def published(publication: Any) -> None:
            publications.append(publication)
            effects.append("publication")

        session.observe_commits(published)
        return session

    session = make_session()
    base = (
        {f"field{i}": 0 for i in range(4097)}
        if row["special"] == "root"
        else v.decode(row["base"])
    )

    def make(kind: str, second: bool = False) -> Any:
        def policy(value: OwnedJson, ops: OwnedJson, info: Any) -> bool:
            revoked = True
            for root in roots:
                try:
                    len(root)
                    revoked = False
                except DraftRevoked:
                    pass
            callbacks.append(
                {
                    "value": t.profile(value.thaw()),
                    "ops": t.profile(ops.thaw()),
                    "count": info.deltas_since_base,
                    "revoked": revoked,
                    "frozen": [
                        type(value) is OwnedJson,
                        type(ops) is OwnedJson,
                        frozen_info(info),
                    ],
                }
            )
            effects.append("policy")
            if second and fail_second:
                raise sentinel
            return (
                row["policy"] == "true"
                or row["policy"] == "count>=2"
                and info.deltas_since_base >= 2
            )

        factory = define_doc_family if row["family"] else define_doc

        def initial(*_seed: Any) -> Any:
            return copy.deepcopy(base)

        return factory(
            kind,
            1,
            row["scope"],
            initial,
            "latest" if row["scope"] == "conversation" else None,
            "initial" if row["scope"] == "conversation" else None,
            None if row["policy"] == "absent" else policy,
        )

    spec = make("checkpoint." + row["id"])
    options: dict[str, Any] = (
        {"conversation_id": 7}
        if row["scope"] == "conversation"
        else {"task_id": 20}
        if row["scope"] == "task"
        else {}
    ) | ({"key": ""} if row["family"] else {})

    def acquire(tx: Any, definition: Any = spec) -> Any:
        return tx.doc(
            definition, **options, **({"seed": None} if row["family"] else {})
        )

    async def snap(definition: Any = spec) -> Any:
        return await session.asnapshot(definition, **options)

    def read(definition: Any = spec) -> Any:
        scope = {"kind": row["scope"]} | (
            {"conversationId": 7}
            if row["scope"] == "conversation"
            else {"taskId": 20}
            if row["scope"] == "task"
            else {}
        )
        record = inner.find_document(
            DocumentAddress(
                {"kind": definition.kind, "scope": scope}
                | ({"key": ""} if row["family"] else {})
            ),
            "current",
        )
        return inner.document(record.id).to_tree() if record is not None else None

    def value(snapshot: Any) -> Any:
        return snapshot.value.thaw() if snapshot is not None else None

    def start() -> Any:
        return {
            "write": len(storage.writes),
            "pub": len(publications),
            "callback": len(callbacks),
            "effect": len(effects),
        }

    async def observe(
        before: Any, cut: Any, working: Any, before_stored: Any = None
    ) -> Any:
        warm, stored = await snap(), read()
        pub = publications[-1] if len(publications) > cut["pub"] else None
        doc = (
            next(
                (
                    change
                    for change in pub.changes
                    if type(change) is RawDocumentChange
                    and change.record.to_tree()["kind"] == spec.kind
                ),
                None,
            )
            if pub
            else None
        )
        batch = storage.writes[-1] if len(storage.writes) > cut["write"] else []
        write = next(
            (
                w
                for w in batch
                if w["type"] in ("document.change", "document.create")
                and (
                    w.get("id") == (before.record.id if before else None)
                    or w.get("record", {}).get("kind") == spec.kind
                )
            ),
            None,
        )
        content = write["content"] if write else {}
        recent = callbacks[cut["callback"] :]
        actual_ops = (
            v.decode(recent[0]["ops"])
            if recent
            else content.get("ops", doc.ops.thaw() if doc else [])
        )
        replay = (
            apply_operations(
                OwnedJson(before_stored["value"]), OwnedJson(actual_ops)
            ).thaw()
            if before_stored
            else content.get("value")
        )
        basis = {
            "value": t.profile(before_stored["value"] if before_stored else None),
            "count": before_stored["deltasSinceBase"] if before_stored else None,
            "ops_source": "creation-base"
            if before_stored is None
            else "checkpoint-argument"
            if recent
            else "submitted-delta"
            if content.get("kind") == "delta"
            else "publication"
            if doc is not None and doc.ops.thaw()
            else "empty-no-content",
        }
        before_unload = len(storage.writes), len(publications)
        await session.aunload_documents()
        cold = await snap()
        assert before_unload == (len(storage.writes), len(publications))
        return {
            "value": t.profile(value(warm)),
            "stored": t.profile(stored["value"] if stored else None),
            "stored_replay": t.profile(replay),
            "cold": t.profile(value(cold)),
            "replay_basis": basis,
            "count": stored["deltasSinceBase"] if stored else None,
            "version": stored["version"] if stored else None,
            "same_snapshot": warm is before,
            "admitted": len(storage.writes) - cut["write"],
            "published": len(publications) - cut["pub"],
            "callbacks": callbacks[cut["callback"] :],
            "ops": t.profile(doc.ops.thaw() if doc else []),
            "publication_value": t.profile(
                doc.value.thaw() if doc and doc.value is not None else None
            ),
            "write_kind": content.get("kind"),
            "write_value": t.profile(content.get("value")),
            "write_ops": t.profile(content.get("ops", [])),
            "retired": sum(w["type"] == "document.retire" for w in batch),
            "effects": effects[cut["effect"] :],
            "working": {
                "source": "derived-plain-mirror; not observed live Draft",
                "value": t.profile(working),
            },
        }

    try:
        creation_working = copy.deepcopy(base)
        actions(creation_working, row["creation"])
        cut = start()

        async def create(tx: Any) -> Any:
            draft = await acquire(tx)
            roots[:] = [draft]
            actions(draft, row["creation"])

        await session.acommit(create)
        creation = await observe(None, cut, creation_working)
        facts: dict[str, Any] = {}
        if row["special"] == "rollback":
            second = make("checkpoint.second", True)
            await session.acommit(lambda tx: acquire(tx, second))
            old, other = await snap(), await snap(second)
            before = len(storage.writes), len(publications)
            candidates: dict[str, Any] = {}
            fail_second = True

            async def rollback(tx: Any) -> Any:
                one, two = await acquire(tx), await acquire(tx, second)
                roots[:] = [one, two]
                one["n"], two["n"] = 8, 9
                candidates["entry"] = await tx.append_entry(
                    7, {"kind": "checkpoint.entry"}
                )
                candidates["submission"] = await tx.create_submission(
                    {
                        "conversationId": 7,
                        "type": "input",
                        "status": "queued",
                        "requestId": "checkpoint.rollback",
                    }
                )
                tx.set_task(task(True))
                await tx.retire_doc(spec, **options)

            caught = None
            try:
                await session.acommit(rollback)
            except ValueError as error:
                caught = error
            facts["rollback"] = {
                "same_error": caught is sentinel,
                "same_first": await snap() is old,
                "same_second": await snap(second) is other,
                "no_admission": len(storage.writes) == before[0],
                "no_publication": len(publications) == before[1],
                "entry_absent": inner.entry(candidates["entry"].id) is None,
                "submission_absent": inner.submission(candidates["submission"].id)
                is None,
                "task_pending": inner.task(20).to_tree()["state"]["status"]
                == "pending",
            }
            fail_second = False
        steps = []
        for commands in row["steps"]:
            before = await snap()
            before_stored = read()
            working = copy.deepcopy(value(before))
            actions(working, commands)
            cut = start()

            async def edit(tx: Any) -> Any:
                draft = await acquire(tx)
                roots[:] = [draft]
                actions(draft, commands)
                if row["special"] == "terminal":
                    tx.set_task(task(True))
                if row["special"] == "retire":
                    await tx.retire_doc(spec, **options)

            await session.acommit(edit)
            steps.append(await observe(before, cut, working, before_stored))
        final = value(await snap())
        if backend != "memory":
            await session.aclose()
            inner = open_sqlite_storage(str(path), mode="open")
            storage.inner = inner
            session = make_session()
        else:
            await session.aunload_documents()
        fresh = value(await snap())
        assert not reports
        return {
            "id": row["id"],
            "creation": creation,
            "steps": steps,
            "facts": facts,
            "final": t.profile(final),
            "fresh": t.profile(fresh),
        }
    finally:
        await session.aclose()


async def native(folder: Path) -> Any:
    config = recipe()
    folder.mkdir(mode=0o700, exist_ok=False)
    lanes = []
    for backend in BACKENDS:
        cases = []
        for row in config["cases"]:
            path = folder / (backend + "-" + row["id"] + ".sqlite")
            inner = (
                MemoryStorage()
                if backend == "memory"
                else d.ReopeningStorage(path)
                if backend == "reopen"
                else open_sqlite_storage(str(path), mode="create")
            )
            async with asyncio.timeout(30):
                cases.append(await native_case(row, inner, path, backend))
        lanes.append(
            {
                "backend": backend,
                "cases": cases,
                "runtime": await native_runtime(backend, folder),
            }
        )
    return {
        "schema_version": 1,
        "phase": PHASE,
        "producer": "native",
        "profile": PROFILE,
        "backends": lanes,
    }


async def native_runtime(backend: str, folder: Path) -> Any:  # noqa: C901 - three explicit runtime boundaries
    path = folder / ("runtime-" + backend + ".sqlite")
    inner = (
        MemoryStorage()
        if backend == "memory"
        else d.ReopeningStorage(path)
        if backend == "reopen"
        else open_sqlite_storage(str(path), mode="create")
    )
    events: list[str] = []
    storage = Recording(inner, [])
    session = Session(storage, now=lambda: 1000)
    session.observe_commits(lambda _: events.append("publication"))
    returned: list[Any] = []
    children: list[Any] = []
    refused = []

    async def answer() -> bool:
        raise AssertionError("An invalid coroutine result must never be started")

    def promise(*_args: Any) -> Any:
        result = answer()
        returned.append(result)
        return result

    def nested(*_args: Any) -> bool:
        body = session.acommit(lambda _tx: None)
        try:
            body.send(None)
        except ReentrantSessionCall:
            refused.append(True)
        else:
            refused.append(False)
        finally:
            try:
                body.throw(asyncio.CancelledError())
            except (asyncio.CancelledError, StopIteration, RuntimeError):
                pass
            body.close()
        children.append(
            asyncio.create_task(session.acommit(lambda _tx: events.append("child")))
        )
        return False

    first = define_doc(
        "runtime.promise", 1, "session", lambda: {"n": 0}, checkpoint_when=promise
    )
    second = define_doc(
        "runtime.nested", 1, "session", lambda: {"n": 0}, checkpoint_when=nested
    )
    try:

        async def create(tx: Any) -> Any:
            await tx.doc(first)
            await tx.doc(second)

        async def edit(tx: Any, spec: Any) -> Any:
            (await tx.doc(spec))["n"] = 1

        await session.acommit(create)
        before = len(storage.writes)
        rejected = False
        try:
            await session.acommit(lambda tx: edit(tx, first))
        except TypeError:
            rejected = True
        assert (
            len(returned) == 1
            and inspect.getcoroutinestate(returned[0]) == inspect.CORO_CLOSED
        )
        content = (
            storage.writes[-1][0]["content"] if len(storage.writes) > before else {}
        )
        current = await session.asnapshot(first)
        assert current is not None
        result = {
            "refused": rejected,
            "admitted": len(storage.writes) - before,
            "content_kind": content.get("kind"),
            "value": t.profile(current.value.thaw()),
        }
        events.clear()
        await session.acommit(lambda tx: edit(tx, second))
        await asyncio.gather(*children)
        nested_result = {
            "same_task_refused": refused == [True],
            "child_after_publication": events == ["publication", "child"],
        }
        await session.aclose()
        closed_unload = True
        try:
            await session.aunload_documents()
        except SessionClosedError:
            closed_unload = False
        return {
            "promise": result,
            "nested": nested_result,
            "closed_unload_fulfilled": closed_unload,
        }
    finally:
        await session.aclose()


STEP_FIELDS = {
    "value",
    "stored",
    "stored_replay",
    "replay_basis",
    "cold",
    "count",
    "version",
    "same_snapshot",
    "admitted",
    "published",
    "callbacks",
    "ops",
    "publication_value",
    "write_kind",
    "write_value",
    "write_ops",
    "retired",
    "effects",
    "working",
}
VALUE_FIELDS = {
    "value",
    "stored",
    "stored_replay",
    "cold",
    "ops",
    "publication_value",
    "write_value",
    "write_ops",
}
ROLLBACK_FIELDS = {
    "same_error",
    "same_first",
    "same_second",
    "no_admission",
    "no_publication",
    "entry_absent",
    "submission_absent",
    "task_pending",
}


def step_shape(step: Any, reference: bool) -> None:
    d.shape(
        step,
        STEP_FIELDS | ({"owner_replay"} if reference else set()),
        "checkpoint step",
    )
    for field in VALUE_FIELDS:
        t.valid_profile(step[field])
    for field in ("ops", "write_ops"):
        own_operations(v.decode(step[field]))
    d.shape(
        step["replay_basis"],
        {"value", "count", "ops_source"},
        "checkpoint replay basis",
    )
    t.valid_profile(step["replay_basis"]["value"])
    d.require(
        step["replay_basis"]["count"] is None
        or type(step["replay_basis"]["count"]) is int
        and step["replay_basis"]["count"] >= 0,
        "checkpoint prior count",
    )
    d.require(
        step["replay_basis"]["ops_source"]
        in (
            "creation-base",
            "checkpoint-argument",
            "submitted-delta",
            "publication",
            "empty-no-content",
        ),
        "checkpoint replay operation provenance",
    )
    for field in ("count", "version"):
        d.require(
            step[field] is None
            or type(step[field]) is int
            and step[field] >= (1 if field == "version" else 0),
            "checkpoint count/version",
        )
    for field in ("admitted", "published", "retired"):
        d.require(type(step[field]) is int and step[field] >= 0, "checkpoint counter")
    d.require(
        type(step["same_snapshot"]) is bool
        and step["write_kind"] in (None, "base", "delta"),
        "checkpoint selection",
    )
    d.require(
        type(step["effects"]) is list
        and all(
            type(x) is str
            and x in ("scan", "submission", "policy", "commit", "publication")
            for x in step["effects"]
        ),
        "checkpoint effects",
    )
    d.shape(step["working"], {"source", "value"}, "checkpoint working mirror")
    d.require(
        step["working"]["source"] == "derived-plain-mirror; not observed live Draft",
        "checkpoint working provenance",
    )
    t.valid_profile(step["working"]["value"])
    if reference:
        d.shape(
            step["owner_replay"], {"source", "value", "ops"}, "checkpoint replay mirror"
        )
        d.require(
            step["owner_replay"]["source"]
            == (
                "plain final creation mirror; not actual Session"
                if step["replay_basis"]["ops_source"] == "creation-base"
                else "separate pinned Chord replay; not actual Session"
            ),
            "checkpoint replay provenance",
        )
        t.valid_profile(step["owner_replay"]["value"])
        t.valid_profile(step["owner_replay"]["ops"])
        own_operations(v.decode(step["owner_replay"]["ops"]))
    d.require(type(step["callbacks"]) is list, "checkpoint callbacks")
    for row in step["callbacks"]:
        d.shape(
            row, {"value", "ops", "count", "revoked", "frozen"}, "checkpoint callback"
        )
        t.valid_profile(row["value"])
        t.valid_profile(row["ops"])
        own_operations(v.decode(row["ops"]))
        d.require(
            type(row["count"]) is int
            and row["count"] >= 0
            and type(row["revoked"]) is bool,
            "checkpoint callback count",
        )
        d.require(
            type(row["frozen"]) is list
            and len(row["frozen"]) == 3
            and all(type(b) is bool for b in row["frozen"]),
            "checkpoint callback immutability",
        )


def trace(value: Any, reference: bool) -> Any:
    d.shape(
        value,
        {"schema_version", "phase", "producer", "profile", "backends"},
        "checkpoint producer",
    )
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE
        and value["producer"] == ("pi" if reference else "native")
        and value["profile"] == PROFILE,
        "checkpoint producer identity",
    )
    d.require(
        type(value["backends"]) is list and len(value["backends"]) == 3,
        "checkpoint backends",
    )
    for lane, backend in zip(value["backends"], BACKENDS, strict=True):
        d.shape(lane, {"backend", "cases", "runtime"}, "checkpoint backend")
        runtime_shape(lane["runtime"])
        d.require(
            lane["backend"] == backend
            and type(lane["cases"]) is list
            and len(lane["cases"]) == len(recipe()["cases"]),
            "checkpoint backend inventory",
        )
        for row, definition in zip(lane["cases"], recipe()["cases"], strict=True):
            d.shape(
                row,
                {"id", "creation", "steps", "facts", "final", "fresh"},
                "checkpoint observation",
            )
            d.require(
                row["id"] == definition["id"]
                and type(row["steps"]) is list
                and len(row["steps"]) == len(definition["steps"]),
                "checkpoint observation inventory",
            )
            for step in [row["creation"], *row["steps"]]:
                step_shape(step, reference)
            t.valid_profile(row["final"])
            t.valid_profile(row["fresh"])
            d.shape(
                row["facts"],
                {"rollback"} if definition["special"] == "rollback" else set(),
                "checkpoint facts",
            )
            if row["facts"]:
                d.shape(
                    row["facts"]["rollback"], ROLLBACK_FIELDS, "checkpoint rollback"
                )
                d.require(
                    all(type(x) is bool for x in row["facts"]["rollback"].values()),
                    "checkpoint rollback booleans",
                )
    return value


def _comparison_view(value: Any) -> Any:
    """Only after strict native/backend comparison: JS member-order projection."""
    if type(value) is list:
        if (
            value
            and type(value[0]) is str
            and value[0] in ("null", "bool", "number", "string", "array", "object")
        ):
            return t.comparison_profile(value)
        return [_comparison_view(x) for x in value]
    if type(value) is dict:
        return {k: _comparison_view(x) for k, x in value.items()}
    return value


def expected_native(reference: Any) -> Any:  # noqa: C901 - closed projection paths
    """Closed differences: immutable args; named zero recipes use pinned replay.

    The raw Pi warm/stored/cold/sign observations remain in reference. Never
    normalize zero globally or derive a reference observation from native data.
    """
    expected = copy.deepcopy(reference)
    expected["producer"] = "native"
    for lane in expected["backends"]:
        lane["runtime"] = runtime_contract(False)
        for row in lane["cases"]:
            zero = row["id"].startswith("zero-") or row["id"] == "creation-final-zero"
            previous = ["null"]
            for index, step in enumerate([row["creation"], *row["steps"]]):
                replay = step.pop("owner_replay")
                for callback in step["callbacks"]:
                    callback["frozen"] = [True, True, True]
                    if zero:
                        callback["value"] = copy.deepcopy(replay["value"])
                if zero:
                    step["replay_basis"]["value"] = copy.deepcopy(previous)
                    for field in ("value", "stored", "stored_replay", "cold"):
                        step[field] = copy.deepcopy(replay["value"])
                    if step["published"]:
                        step["publication_value"] = copy.deepcopy(replay["value"])
                    if step["write_kind"] == "base":
                        step["write_value"] = copy.deepcopy(replay["value"])
                    if index == len(row["steps"]):
                        row["final"] = copy.deepcopy(replay["value"])
                        row["fresh"] = copy.deepcopy(replay["value"])
                previous = replay["value"]
    return expected


def comparison(expected: Any, actual: Any) -> int:
    trace(expected, True)
    trace(actual, False)
    if not reference_consistent(expected):
        return 1
    # No projection, sorting, JSON-zero roundtrip or native-derived oracle here.
    first = {k: v for k, v in actual["backends"][0].items() if k != "backend"}
    if any(
        not d.same(first, {k: v for k, v in lane.items() if k != "backend"})
        for lane in actual["backends"][1:]
    ):
        return 1
    return (
        0
        if d.same(_comparison_view(expected_native(expected)), _comparison_view(actual))
        else 1
    )


def zero_difference(left: Any, right: Any, path: tuple[str, ...]) -> bool:
    """Only the named zero member may differ, and only between its two signs."""
    left, right = copy.deepcopy(left), copy.deepcopy(right)
    for value in (left, right):
        current = value
        for key in path[:-1]:
            if type(current) is not dict or key not in current:
                return False
            current = current[key]
        if type(current) is not dict or path[-1] not in current:
            return False
        number = current[path[-1]]
        if type(number) not in (int, float) or number != 0:
            return False
        current[path[-1]] = 0.0
    return d.same(
        t.comparison_profile(t.profile(left)), t.comparison_profile(t.profile(right))
    )


def reference_consistent(value: Any) -> bool:  # noqa: C901 - closed provenance and zero checks
    """Projection never masks arbitrary reference values or unused mirror fields."""
    for lane in value["backends"]:
        if not d.same(lane["runtime"], runtime_contract(True)):
            return False
        for row in lane["cases"]:
            zero = row["id"].startswith("zero-") or row["id"] == "creation-final-zero"
            path = (
                ("nested", "zero")
                if "-accessed-" in row["id"] or "-untouched-" in row["id"]
                else ("zero",)
            )
            prior, prior_count = ["null"], None
            for step in [row["creation"], *row["steps"]]:
                if (
                    not d.same(step["replay_basis"]["value"], prior)
                    or step["replay_basis"]["count"] != prior_count
                ):
                    return False
                prior, prior_count = step["stored"], step["count"]
                replay = step["owner_replay"]
                callbacks = step["callbacks"]
                if any(
                    callback["frozen"] != [False, False, False]
                    for callback in callbacks
                ):
                    return False
                actual_ops = (
                    callbacks[0]["ops"]
                    if callbacks
                    else step["write_ops"]
                    if step["write_kind"] == "delta"
                    else step["ops"]
                )
                if not t.profile_equivalent(replay["ops"], actual_ops):
                    return False
                candidates = [callback["value"] for callback in callbacks]
                candidates += [
                    step[field]
                    for field in ("value", "stored", "stored_replay", "cold")
                    if step[field] != ["null"]
                ]
                if step["published"] and step["publication_value"] != ["null"]:
                    candidates.append(step["publication_value"])
                if step["write_kind"] == "base":
                    candidates.append(step["write_value"])
                for observed in candidates:
                    if t.profile_equivalent(observed, replay["value"]):
                        continue
                    if not zero or not zero_difference(
                        v.decode(observed), v.decode(replay["value"]), path
                    ):
                        return False
            if not t.profile_equivalent(
                row["final"], prior
            ) or not t.profile_equivalent(row["fresh"], prior):
                return False
    return True


def runtime_shape(value: Any) -> None:
    d.shape(
        value,
        {"promise", "nested", "closed_unload_fulfilled"},
        "checkpoint runtime boundaries",
    )
    d.shape(
        value["promise"],
        {"refused", "admitted", "content_kind", "value"},
        "checkpoint awaitable boundary",
    )
    d.shape(
        value["nested"],
        {"same_task_refused", "child_after_publication"},
        "checkpoint nested boundary",
    )
    d.require(
        type(value["closed_unload_fulfilled"]) is bool
        and type(value["promise"]["refused"]) is bool
        and type(value["promise"]["admitted"]) is int
        and value["promise"]["admitted"] >= 0
        and value["promise"]["content_kind"] in (None, "base", "delta"),
        "checkpoint runtime scalars",
    )
    d.require(
        all(type(x) is bool for x in value["nested"].values()),
        "checkpoint nested booleans",
    )
    t.valid_profile(value["promise"]["value"])


def runtime_contract(reference: bool) -> Any:
    return {
        "promise": {
            "refused": not reference,
            "admitted": 1 if reference else 0,
            "content_kind": "base" if reference else None,
            "value": t.profile({"n": 1 if reference else 0}),
        },
        "nested": {"same_task_refused": not reference, "child_after_publication": True},
        "closed_unload_fulfilled": reference,
    }


def fixture(path: Path = EXPECTED) -> Any:
    value = d.load(path)
    d.shape(
        value,
        {
            "schema_version",
            "phase",
            "producer",
            "encoding",
            "raw_bytes",
            "raw_sha256",
            "payload",
        },
        "checkpoint fixture",
    )
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE
        and value["producer"] == "pi"
        and value["encoding"] == "lzma-base64",
        "checkpoint fixture identity",
    )
    d.require(
        type(value["raw_bytes"]) is int
        and 0 < value["raw_bytes"] <= 32_000_000
        and type(value["payload"]) is str,
        "checkpoint fixture bound",
    )
    o.contract.digest(value["raw_sha256"])
    unpack = lzma.LZMADecompressor(memlimit=64 * 1024 * 1024)
    raw = unpack.decompress(
        base64.b64decode(value["payload"], validate=True), 32_000_001
    )
    d.require(
        unpack.eof
        and not unpack.unused_data
        and len(raw) == value["raw_bytes"]
        and hashlib.sha256(raw).hexdigest() == value["raw_sha256"],
        "checkpoint fixture integrity",
    )
    observed = json.loads(raw, object_pairs_hook=o._pairs, parse_constant=o._constant)
    trace(observed, True)
    return observed


def parents() -> Any:
    return a.parents() | {"documents": o.sha(a.MANIFEST), "tracker": o.sha(t.MANIFEST)}


def configuration() -> Any:
    value = d.load(MANIFEST)
    d.shape(
        value,
        {
            "schema_version",
            "phase",
            "reference_commit",
            "parents",
            "closure",
            "reference_sources",
            "upstream_tests",
        },
        "checkpoint manifest",
    )
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE
        and value["reference_commit"] == o.PIN,
        "checkpoint manifest identity",
    )
    for key, names in (
        ("closure", CLOSURE),
        ("reference_sources", SOURCES),
        ("upstream_tests", UPSTREAM_TESTS),
    ):
        d.shape(value[key], names, "checkpoint " + key)
        for path, digest in value[key].items():
            o.contract.digest(digest)
            if key == "closure":
                d.require(o.sha(ROOT / path) == digest, "Checkpoint closure drift")
    d.require(d.same(value["parents"], parents()), "Checkpoint parent drift")
    a.configuration()
    t.manifest()
    return value


MUTANTS = (
    "kind",
    "count",
    "ops",
    "value",
    "cold",
    "rollback",
    "native-sign",
    "native-order",
)


def mutate(actual: Any, name: str) -> Any:
    changed = copy.deepcopy(actual)
    for lane in (
        changed["backends"][:1] if name.startswith("native-") else changed["backends"]
    ):
        mutate_lane(lane, name)
    return changed


def mutate_lane(lane: Any, name: str) -> None:
    row = next(
        row
        for row in lane["cases"]
        if row["id"]
        == (
            "second-policy-rollback"
            if name == "rollback"
            else "zero-true-negative-untouched-dirty"
            if name in ("native-sign", "native-order")
            else "counts-count>=2"
        )
    )
    step = row["steps"][2 if name == "kind" else 0]
    if name == "kind":
        step["write_kind"] = "delta"
    elif name == "count":
        step["count"] += 1
    elif name == "ops":
        step["ops"] = t.profile([])
    elif name in ("value", "cold"):
        step[name] = t.profile({"n": 99})
    elif name == "rollback":
        row["facts"]["rollback"]["no_admission"] = False
    else:
        tree = v.decode(step["value"])
        if name == "native-sign":
            tree["nested"]["zero"] = 0.0
        else:
            nested = tree["nested"]
            tree["nested"] = dict(reversed(list(nested.items())))
        step["value"] = t.profile(tree)


def execute(acceptance: Path, sqlite: Path, output: Path) -> int:
    d.require(
        not output.exists() and not output.is_symlink(), "Checkpoint output exists"
    )
    acceptance, sqlite, output = (
        acceptance.resolve(strict=True),
        sqlite.resolve(strict=True),
        output.absolute(),
    )
    d.require(
        not output.resolve().is_relative_to(acceptance)
        and not output.resolve().is_relative_to(sqlite),
        "Checkpoint output inside predecessor",
    )
    config = configuration()
    manifest_sha = o.sha(MANIFEST)
    parent = o.manifest()
    v.artifacts.attest(acceptance, o.FIXTURES)
    run = o.acceptance.attest_reference(o, acceptance / "reference", parent, o.FIXTURES)
    predecessor = a.attest_sqlite(sqlite, acceptance)
    predecessor_sha = o.sha(sqlite / "result.json")
    snapshot = acceptance / "reference/snapshot"
    o.verify_sources(snapshot, config["reference_sources"])
    o.verify_sources(snapshot, config["upstream_tests"])
    driver = snapshot / DESTINATION
    d.require(
        not driver.exists() and not driver.is_symlink(), "Checkpoint driver exists"
    )
    output.mkdir(mode=0o700, exist_ok=False)
    d.dump(output / "input.json", recipe())
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
            "Checkpoint runtime drift",
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
        "pi-checkpoints",
        env,
        120,
    )
    d.require(code == 0 and not stdout and not stderr, "Pi checkpoint driver failed")
    modules = o.verify_resolutions(
        snapshot,
        d.load(output / "resolutions.json"),
        d.load(acceptance / "reference/source-sha256.json"),
        {DESTINATION: o.sha(DRIVER)},
        sorted(MODULES),
    )
    o.module_dependencies(snapshot, modules, run["installed_dependencies"])
    dependencies = d.load(acceptance / "reference/dependency-sha256.json")
    for name, digest in modules.items():
        d.require(
            name in config["reference_sources"]
            or name == DESTINATION
            or "node_modules" in Path(name).parts
            and dependencies.get(name) == digest,
            "Unbound checkpoint import",
        )
    stdout, stderr, code = d.process(
        [
            sys.executable,
            "-I",
            str(Path(__file__).resolve()),
            "_native",
            str(output / "native.json"),
            str(output / "stores"),
        ],
        ROOT,
        output,
        "native-checkpoints",
        {"PATH": env["PATH"], "HOME": env["HOME"]},
        180,
    )
    d.require(
        code == 0 and not stdout and not stderr, "Native checkpoint driver failed"
    )
    expected, actual = d.load(output / "observed.json"), d.load(output / "native.json")
    d.require(
        d.same(expected, fixture()),
        "Fresh Pi capture differs from recorded actual fixture",
    )
    status = comparison(expected, actual)
    controls = []
    if status == 0:
        folder = output / "mutants"
        folder.mkdir(mode=0o700)
        for name in MUTANTS:
            path = folder / (name + ".json")
            d.dump(path, mutate(actual, name))
            for label, source, wanted in (
                ("positive", output / "native.json", 0),
                ("negative", path, 1),
            ):
                _, _, code = d.process(
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
                    60,
                )
                d.require(
                    code == wanted,
                    "Checkpoint control must be semantic mismatch1, never invalid2",
                )
            controls.append(
                {
                    "id": name,
                    "positive_exit": 0,
                    "negative_exit": 1,
                    "sha256": o.sha(path),
                }
            )
        invalid = copy.deepcopy(actual)
        invalid["unknown"] = True
        path = folder / "invalid.json"
        d.dump(path, invalid)
        _, _, code = d.process(
            [
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                "compare",
                str(output / "observed.json"),
                str(path),
            ],
            ROOT,
            output,
            "invalid-negative",
            {"PATH": env["PATH"], "HOME": env["HOME"]},
            60,
        )
        d.require(code == 2, "Checkpoint invalid producer must exit2")
    o.acceptance.attest_reference(o, acceptance / "reference", parent, o.FIXTURES)
    v.artifacts.attest(acceptance, o.FIXTURES)
    a.attest_sqlite(sqlite, acceptance)
    configuration()
    d.require(
        manifest_sha == o.sha(MANIFEST)
        and predecessor_sha == o.sha(sqlite / "result.json")
        and o.sha(driver) == o.sha(DRIVER),
        "Checkpoint execution drift",
    )
    result = {
        "schema_version": 1,
        "phase": PHASE,
        "status": "PASS" if status == 0 else "mismatch",
        "execution_id": str(uuid.uuid4()),
        "fresh_reference_bootstrap": False,
        "parent_execution_id": d.load(acceptance / "acceptance.json")["execution_id"],
        "parent_acceptance_sha256": o.sha(acceptance / "acceptance.json"),
        "reference_run_sha256": o.sha(acceptance / "reference/run.json"),
        "sqlite_execution_id": predecessor["execution_id"],
        "sqlite_result_sha256": predecessor_sha,
        "manifest_sha256": manifest_sha,
        "parents": parents(),
        "module_sha256": modules,
        "artifacts_sha256": {
            str(p.relative_to(output)): o.sha(p)
            for p in output.rglob("*")
            if p.is_file()
        },
        "mutants": controls,
        "invalid_exit": 2 if status == 0 else None,
        "reference_cases": {
            lane["backend"]: len(lane["cases"]) for lane in expected["backends"]
        },
        "native_cases": {
            lane["backend"]: len(lane["cases"]) for lane in actual["backends"]
        },
        "native_mode": "async",
        "product_integration": False,
        "full_document_parity": False,
    }
    d.dump(output / "result.json", result)
    return status


def attest(folder: Path, acceptance: Path, sqlite: Path) -> Any:
    """Recheck this run's actual producers, artifacts, children and source chain."""
    configuration()
    v.artifacts.attest(acceptance, o.FIXTURES)
    parent = o.acceptance.attest_reference(
        o, acceptance / "reference", o.manifest(), o.FIXTURES
    )
    predecessor = a.attest_sqlite(sqlite, acceptance)
    value = d.load(folder / "result.json")
    d.shape(
        value,
        {
            "schema_version",
            "phase",
            "status",
            "execution_id",
            "fresh_reference_bootstrap",
            "parent_execution_id",
            "parent_acceptance_sha256",
            "reference_run_sha256",
            "sqlite_execution_id",
            "sqlite_result_sha256",
            "manifest_sha256",
            "parents",
            "module_sha256",
            "artifacts_sha256",
            "mutants",
            "invalid_exit",
            "reference_cases",
            "native_cases",
            "native_mode",
            "product_integration",
            "full_document_parity",
        },
        "checkpoint result",
    )
    expected = {
        "schema_version": 1,
        "phase": PHASE,
        "status": "PASS",
        "fresh_reference_bootstrap": False,
        "parent_execution_id": d.load(acceptance / "acceptance.json")["execution_id"],
        "parent_acceptance_sha256": o.sha(acceptance / "acceptance.json"),
        "reference_run_sha256": o.sha(acceptance / "reference/run.json"),
        "sqlite_execution_id": predecessor["execution_id"],
        "sqlite_result_sha256": o.sha(sqlite / "result.json"),
        "manifest_sha256": o.sha(MANIFEST),
        "parents": parents(),
        "invalid_exit": 2,
        "native_mode": "async",
        "product_integration": False,
        "full_document_parity": False,
        "reference_cases": dict.fromkeys(BACKENDS, 52),
        "native_cases": dict.fromkeys(BACKENDS, 52),
    }
    for key, wanted in expected.items():
        d.require(d.same(value[key], wanted), "Checkpoint result drift: " + key)
    d.require(
        str(uuid.UUID(value["execution_id"])) == value["execution_id"],
        "Checkpoint execution ID",
    )
    files = {
        str(p.relative_to(folder)): o.sha(p)
        for p in folder.rglob("*")
        if p.is_file() and p.name != "result.json"
    }
    d.require(d.same(value["artifacts_sha256"], files), "Checkpoint artifact drift")
    actual = d.load(folder / "native.json")
    reference = d.load(folder / "observed.json")
    d.require(
        d.same(reference, fixture()) and comparison(reference, actual) == 0,
        "Checkpoint actual pair drift",
    )
    modules = o.verify_resolutions(
        acceptance / "reference/snapshot",
        d.load(folder / "resolutions.json"),
        d.load(acceptance / "reference/source-sha256.json"),
        {DESTINATION: o.sha(DRIVER)},
        sorted(MODULES),
    )
    o.module_dependencies(
        acceptance / "reference/snapshot", modules, parent["installed_dependencies"]
    )
    d.require(d.same(modules, value["module_sha256"]), "Checkpoint modules drift")
    d.require(
        type(value["mutants"]) is list and len(value["mutants"]) == len(MUTANTS),
        "Checkpoint mutants",
    )
    for row, name in zip(value["mutants"], MUTANTS, strict=True):
        path = folder / "mutants" / (name + ".json")
        d.require(
            d.same(
                row,
                {
                    "id": name,
                    "positive_exit": 0,
                    "negative_exit": 1,
                    "sha256": o.sha(path),
                },
            ),
            "Checkpoint mutant identity",
        )
        d.require(
            comparison(reference, d.load(path)) == 1,
            "Checkpoint mutation no longer discriminates",
        )
    child_results = {
        "node-version": 0,
        "npm-version": 0,
        "pi-checkpoints": 0,
        "native-checkpoints": 0,
        "invalid-negative": 2,
    }
    child_results.update(
        {
            name + "-" + suffix: code
            for name in MUTANTS
            for suffix, code in (("positive", 0), ("negative", 1))
        }
    )
    for label, code in child_results.items():
        process = d.load(folder / (label + ".json"))
        d.process_record(process, folder, label)
        d.require(process["returncode"] == code, "Checkpoint child exit drift")
        stdout = (folder / (label + ".stdout")).read_bytes()
        stderr = (folder / (label + ".stderr")).read_bytes()
        if label in ("node-version", "npm-version"):
            d.require(
                stdout.decode().strip() == o.manifest()["runtime"][label.split("-")[0]]
                and not stderr,
                "Checkpoint child version drift",
            )
        elif code == 2:
            d.require(
                not stdout and stderr.startswith(b"INVALID:"),
                "Checkpoint invalid child evidence",
            )
        else:
            d.require(not stdout and not stderr, "Checkpoint child diagnostics")
        if label not in (
            "node-version",
            "npm-version",
            "pi-checkpoints",
            "native-checkpoints",
        ):
            source = (
                folder / "native.json"
                if label.endswith("-positive")
                else folder
                / "mutants"
                / (
                    "invalid.json"
                    if label == "invalid-negative"
                    else label.removesuffix("-negative") + ".json"
                )
            )
            d.require(
                process["argv"][1:]
                == [
                    "-I",
                    str(Path(__file__).resolve()),
                    "compare",
                    str(folder / "observed.json"),
                    str(source),
                ]
                and process["cwd"] == str(ROOT),
                "Checkpoint comparison child identity",
            )
    invalid = d.load(folder / "mutants/invalid.json")
    try:
        trace(invalid, False)
    except (TypeError, ValueError):
        pass
    else:
        raise o.EvidenceError("Checkpoint invalid control became valid")
    return value


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
    internal.add_argument("stores", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "compare":
            return comparison(d.load(args.expected), d.load(args.actual))
        if args.command == "_native":
            d.dump(args.output, asyncio.run(native(args.stores)))
            return 0
        return execute(args.acceptance, args.sqlite, args.output)
    except Exception as error:  # noqa: BLE001 - fail closed, no process-control swallowing
        print("INVALID: " + type(error).__name__ + ": " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
