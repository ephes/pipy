"""Real selected Product retry fixtures; observers forward actual execution."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import sqlite3
import threading
from contextlib import closing
from importlib import import_module
from pathlib import Path

values = import_module("durable_reference.product_retry_values")
ROOT = Path(__file__).resolve().parents[2]
# Bound below from the immutable first Core export; a later source epoch must
# explicitly rebind these pins, never silently accept source drift.
NATIVE_BINDING_SOURCE = {
    "src/pipy_harness/durable_product_api.py": "15ed10c9bdd921034f3e8999525c0e0ea4539e27ec4a67ac4f43ea1055490570",
    "src/pipy_harness/native/durable/product_operation.py": "bd27823c6d4a2bbb10c35bf733bd2f4a26323aa4ae4845d3f274cb171f6bfa4f",
    "src/pipy_harness/native/durable/sqlite.py": "bfed03d3f19b6a2cd88e090e34898ddbe2a18cd276b26c22a2f361f3018a8daa",
    "tests/durable/interrupted_cut_support.py": "fb4644a32de8599ed1e0eb8719cbb7a8ce840eea35db6e744233104ceb182f42",
    "tests/durable/test_tool_interrupted_sdk.py": "db7939b940f1982ff1ac3e5ed919554598b191da868208be587d25fa4e8152cf",
    "tests/durable/restart_reset_sdk_support.py": "db1f58bda497e05c917b1e13e68112d5c66da9f7184d60dd3140779493d2ef06",
}


def require_native_binding():
    if not NATIVE_BINDING_SOURCE:
        raise ValueError("Native Product retry bindings PENDING immutable Core SUT")
    for name, digest in NATIVE_BINDING_SOURCE.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Native Product retry binding drift: " + name)


def origins():
    import sys

    result = {}
    for module in tuple(sys.modules.values()):
        name = getattr(module, "__file__", None)
        if name:
            path = Path(name).resolve()
            if path.is_file() and path.is_relative_to(ROOT):
                relative = str(path.relative_to(ROOT))
                if relative.startswith(("src/", "tests/", "scripts/")):
                    result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def sqlite_snapshot(path):
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as db:
        db.execute("PRAGMA query_only=ON")
        result = {}
        for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ):
            cursor = db.execute('SELECT * FROM "' + name.replace('"', '""') + '"')
            rows = [
                [{"sqlite_blob_hex": x.hex()} if type(x) is bytes else x for x in row]
                for row in cursor.fetchall()
            ]
            result[name] = {
                "columns": [c[0] for c in cursor.description],
                "rows": sorted(rows, key=lambda row: json.dumps(row, sort_keys=True)),
            }
        return result


def task_row(root, task_id):
    from pipy_harness.native.durable.sqlite import _decode

    # Live reads include WAL; immutable mode is reserved for closed final stores.
    with closing(
        sqlite3.connect(
            (root / "session.sqlite").resolve().as_uri() + "?mode=ro", uri=True
        )
    ) as db:
        db.execute("PRAGMA query_only=ON")
        rows = [
            _decode(row[0])
            for row in db.execute("SELECT record FROM tasks ORDER BY id")
        ]
    return next(row for row in rows if row["id"] == task_id)


def callbacks(root):
    with closing(
        sqlite3.connect(
            (root / "callback-counter.sqlite").resolve().as_uri() + "?mode=ro", uri=True
        )
    ) as db:
        db.execute("PRAGMA query_only=ON")
        return [
            {"seq": seq, "kind": kind}
            for seq, kind in db.execute("SELECT seq,kind FROM calls ORDER BY seq")
        ]


class ResetObserver:
    def __init__(self, before):
        self.condition = threading.Condition()
        self.before = copy.deepcopy(before)
        self.active = False
        self.rows = []

    def arm(self, active):
        with self.condition:
            self.active = active

    def after(self, connection):
        from pipy_harness.native.durable.sqlite import _decode

        with self.condition:
            if not self.active:
                return
            rows = [
                _decode(x[0])
                for x in connection.execute("SELECT record FROM tasks ORDER BY id")
            ]
            row = next(x for x in rows if x["id"] == self.before["id"])
            expected = dict(self.before)
            expected.pop("restarts", None)
            # Exact reset window, before later legitimate reservation/progress.
            if row == expected:
                self.rows.append(copy.deepcopy(row))

    def snapshot(self):
        with self.condition:
            return copy.deepcopy(self.rows)


class NativeRetryCase:
    """One isolated fixture owner; all real owner cleanup remains unconditional."""

    def __init__(self, case, folder):
        from pipy_harness import durable_cli
        from pipy_harness.native.durable import sqlite as native_sqlite

        self.case, self.folder = case, folder
        self.durable_cli, self.native_sqlite = durable_cli, native_sqlite
        self.old_profile = durable_cli.DurableProviderProfile
        self.old_after = native_sqlite._after_commit
        self.product = self.result_waiter = self.observer = None
        self.records, self.events, self.closed = {}, [], []
        self.cut = self.before_effects = None
        self.entered, self.release = threading.Event(), threading.Event()
        folder.mkdir(parents=True, mode=0o700)
        self.counter = folder / "callback-counter.sqlite"
        with closing(sqlite3.connect(self.counter)) as db, db:
            db.execute(
                "CREATE TABLE calls(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL)"
            )

    def event(self, name):
        self.events.append({"seq": len(self.events) + 1, "event": name})

    def held_wire(self):
        from interrupted_cut_support import Wire

        owner = self

        class HeldWire(Wire):
            def post_json(self, url, **kwargs):
                following = any(x["role"] == "tool" for x in kwargs["body"]["messages"])
                if not owner.case["parked"] or following:
                    with closing(sqlite3.connect(owner.counter)) as db, db:
                        db.execute(
                            "INSERT INTO calls(kind) VALUES (?)", ("provider-held",)
                        )
                    owner.entered.set()
                    if not owner.release.wait(30):
                        raise RuntimeError("Reference callback release missing")
                return super().post_json(url, **kwargs)

        return HeldWire(self.folder)

    async def open(self):
        from interrupted_cut_support import (
            _PROFILE_CLASS,
            authority,
            counts,
            database,
            profile,
            registry,
        )
        from restart_reset_sdk_support import child_row, open_product
        from test_tool_interrupted_sdk import start_cut

        from pipy_harness.sdk import create_durable_product_session

        authority(self.folder)
        database(self.folder)
        if self.case["parked"]:
            start_cut(self.folder, "progress-after")
            actual = json.loads((self.folder / "start-process.json").read_text())
            assert actual["returncode"] == -9
            self.cut = {
                "exit_signal": "SIGKILL",
                "reaped": True,
                "actual_process": actual,
            }
            self.task_id = child_row(self.folder)["id"]
            self.records["initial"] = task_row(self.folder, self.task_id)
            self.event("cut_reaped")
        wire = self.held_wire()
        self.durable_cli.DurableProviderProfile = lambda *a, **kw: _PROFILE_CLASS(
            *a, **kw, http_client=wire
        )
        if self.case["parked"]:
            self.product = await open_product(
                self.folder, restart_limit=self.case["restart_limit"]
            )
            recovered = await self.product.resume()
            assert recovered.status == "held"
            assert not self.entered.is_set(), (
                "Recovery must park before the held provider"
            )
        else:
            self.product = await create_durable_product_session(
                self.folder / "session.sqlite",
                workspace=self.folder,
                profile=profile(self.folder),
                tools=registry(self.folder),
                restart_limit=self.case["restart_limit"],
            )
            admission = await self.product.submit("reference retry live no-op")
            self.task_id = admission.generation_task_id
            self.records["initial"] = task_row(self.folder, self.task_id)
        self.inspection = await self.product.inspect(self.task_id)
        assert (
            self.inspection is not None
            and self.inspection.restart_parked is self.case["parked"]
        )
        self.records["before_retry"] = task_row(self.folder, self.task_id)
        self.before_effects = counts(self.folder) if self.case["parked"] else None

    async def reset_and_hold(self):
        self.observer = ResetObserver(self.records["before_retry"])

        def forwarded_after(connection):
            self.old_after(connection)
            self.observer.after(connection)

        self.native_sqlite._after_commit = forwarded_after
        self.observer.arm(True)
        try:
            self.ack = await self.product.retry_task(self.task_id)
        finally:
            self.observer.arm(False)
        self.event("retry_return")
        # Public active join only. No private RunOperation token drives this proof.
        self.result_waiter = asyncio.create_task(self.product.resume())
        assert await asyncio.to_thread(self.entered.wait, 10), (
            "Actual callback did not enter"
        )
        self.records["at_gate"] = task_row(self.folder, self.task_id)
        self.event("gate_read")
        self.gate = {
            "retry_returned": True,
            "callback_entered": self.entered.is_set(),
            "released": self.release.is_set(),
            "result_settled": self.result_waiter.done(),
        }
        self.reset_rows = self.observer.snapshot()
        if self.case["parked"]:
            assert self.reset_rows, "Missing actual committed reset window"
            self.reset_after = self.reset_rows[0]
        else:
            self.reset_after = copy.deepcopy(self.records["before_retry"])

    async def complete(self):
        from interrupted_cut_support import counts

        from pipy_harness.native.automation.durable_output import run_output

        self.release.set()
        self.event("release")
        result = await self.result_waiter
        self.event("complete")
        self.records["after_release"] = task_row(self.folder, self.task_id)
        self.result_tree = {
            "task_id": result.task_id,
            "status": result.status,
            "inspection": result.inspection.to_tree(),
            "complete_output": run_output(result),
        }
        self.after_effects = counts(self.folder)
        if self.before_effects is not None:
            assert self.after_effects["calls"] == self.before_effects["calls"]
            assert (
                self.after_effects["applications"]
                == self.before_effects["applications"]
            ), "Preserve winning external effect"
        await self.product.close()
        assert self.product._session.end is not None
        self.closed.append(self.product._session.end.reason)
        self.product = None
        self.event("closed")
        self.records["after_close"] = task_row(self.folder, self.task_id)

    def row(self):
        row = {
            "id": self.case["id"],
            "role": "native",
            "backend_kind": "sqlite",
            "task_id": self.task_id,
            "records": self.records,
            "inspection_before": self.inspection.to_tree(),
            "reset": {
                "before": self.records["before_retry"],
                "after": self.reset_after,
                "actual_commit_rows": self.reset_rows,
            },
            "gate": self.gate,
            "events": self.events,
            "cut": self.cut,
            "control": {
                "typed_counter_seed": False,
                "arbitrary_terminal_mutation": False,
            },
            "callback_rows": callbacks(self.folder),
            "callback_sqlite": sqlite_snapshot(self.counter),
            "closed": self.closed,
            "sqlite_final": sqlite_snapshot(self.folder / "session.sqlite"),
            "native_only": {
                "retry_ack": self.ack.to_tree(),
                "run_result": self.result_tree,
                "effects_before": self.before_effects,
                "effects_after": self.after_effects,
                "effects_sqlite": sqlite_snapshot(self.folder / "effects.sqlite"),
                "callback_kind": "actual fake-wire provider; no forced tool reapplication",
                "private_operation_token_used": False,
            },
            "native_origins": origins(),
        }
        values.project(row)
        return row

    async def cleanup(self, failure):
        self.release.set()
        if self.observer is not None:
            self.observer.arm(False)
        results = []
        try:
            try:
                if self.product is not None:
                    results = await asyncio.gather(
                        self.product.close(), return_exceptions=True
                    )
            finally:
                if self.result_waiter is not None:
                    await asyncio.gather(self.result_waiter, return_exceptions=True)
        finally:
            self.native_sqlite._after_commit = self.old_after
            self.durable_cli.DurableProviderProfile = self.old_profile
        errors = [error for error in results if isinstance(error, BaseException)]
        if errors:
            raise BaseExceptionGroup(
                "Product retry fixture cleanup",
                ([failure] if failure is not None else []) + errors,
            )

    async def run(self):
        failure = None
        try:
            await self.open()
            await self.reset_and_hold()
            await self.complete()
            return self.row()
        except BaseException as error:
            failure = error
            raise
        finally:
            await self.cleanup(failure)


def exercise(case, folder):
    require_native_binding()
    previous_env = dict(os.environ)
    try:
        return asyncio.run(NativeRetryCase(case, folder).run())
    finally:
        os.environ.clear()
        os.environ.update(previous_env)
