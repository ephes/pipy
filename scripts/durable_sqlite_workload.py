#!/usr/bin/env python3
"""DE03d1 bounded native SQLite workload. No Pi execution or P43 completion.

Ports only current-base timing-scale reads and the three write shapes from Pi
2ec55d80 packages/durable/src/testing/storage-benchmark.ts (MIT; upstream notice
in tests/fixtures/durable/UPSTREAM-LICENSE.txt). Delta/as-of/history are excluded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import (
    DocumentAddress,
    DocumentQuery,
    EntryQuery,
    StorageWrite,
    SubmissionQuery,
    TaskQuery,
)

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "tests/fixtures/durable/storage-sqlite-workload-v1.input.json"
SPEC = {
    "schema_version": 1,
    "identity": "storage-sqlite-workload-v1",
    "backend": "sqlite",
    "durability": "full",
    "entry_count": 1000,
    "task_count": 300,
    "document_count": 300,
    "batch_size": 100,
    "read_rounds": 3,
    "write_samples": 3,
    "excluded": ["delta", "as-of", "copy", "historical-reconstruction", "deep-fork"],
    "limits": {
        "child_seconds": 120,
        "seed_seconds": 30,
        "reopen_seconds": 15,
        "read_seconds": 30,
        "sample_seconds": 15,
        "database_bytes": 16777216,
        "wal_bytes": 33554432,
    },
}


RUNTIME_SOURCES = tuple(
    "src/pipy_harness/native/durable/" + name + ".py"
    for name in (
        "sqlite",
        "sqlite_connection",
        "sqlite_schema",
        "ownership",
        "memory",
        "records",
        "storage",
        "codec",
    )
)


class Invalid(ValueError):
    """Malformed, incomplete or unbound evidence: exit 2."""


class Mismatch(AssertionError):
    """Well-formed evidence fails required behavior/budget: exit 1."""


def load(path):
    return json.loads(
        Path(path).read_text(),
        parse_constant=lambda value: (_ for _ in ()).throw(Invalid("Nonfinite JSON")),
    )


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    Path(path).chmod(0o600)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message, cls=Invalid):
    if not condition:
        raise cls(message)


def configuration():
    spec = load(INPUT)
    require(
        json.dumps(spec, sort_keys=True, allow_nan=False)
        == json.dumps(SPEC, sort_keys=True, allow_nan=False),
        "Closed workload input drift",
    )
    return spec


def task(ident, index):
    status = ("pending", "running", "terminal")[index % 3]
    state = (
        {
            "status": status,
            "outcome": {"status": "completed", "result": {"index": index}},
        }
        if status == "terminal"
        else {"status": status, "checkpoint": {"index": index, "payload": "x" * 64}}
    )
    return {
        "id": ident,
        "conversationId": 1,
        "kind": "benchmark.filtered" if index % 4 == 0 else "benchmark.other",
        "version": 1,
        "input": {"index": index},
        "background": index % 5 == 0,
        "abortRequested": index % 7 == 0,
        "state": state,
    }


def document(ident, index, kind="benchmark.family", value=None):
    return {
        "type": "document.create",
        "record": {
            "id": ident,
            "kind": kind,
            "key": f"key-{index}" if kind == "benchmark.family" else str(ident),
            "scope": {"kind": "session"},
        },
        "content": {
            "kind": "base",
            "version": 1,
            "value": {"index": index, "text": "x" * 128} if value is None else value,
        },
    }


def apply(storage, writes, dataset):
    seq = storage.commit(tuple(StorageWrite(write) for write in writes))
    require(
        type(seq) is int and seq == dataset["next_seq"],
        "Commit sequence discontinuity",
        Mismatch,
    )
    dataset["next_seq"] = seq + 1
    for write in writes:
        if write["type"] == "document.create":
            dataset["documents"].append(
                {
                    "record": {**write["record"], "createdAt": seq},
                    "version": write["content"]["version"],
                    "value": write["content"]["value"],
                    "deltasSinceBase": 0,
                }
            )
        else:
            kind = write["type"]
            dataset[kind].append(write["value"])
            if kind == "entry":
                dataset["entry_seqs"][str(write["value"]["id"])] = seq

    return seq


def seed(storage, config):
    data = {
        "conversation": [],
        "entry": [],
        "task": [],
        "submission": [],
        "documents": [],
        "entry_seqs": {},
        "next_seq": 1,
        "seed_counts": {
            kind: config[kind + "_count"] for kind in ("entry", "task", "document")
        },
        "write_samples": [],
    }
    apply(storage, [{"type": "conversation", "value": {"id": 1}}], data)
    for kind, count in data["seed_counts"].items():
        for start in range(0, count, config["batch_size"]):
            writes = []
            for index in range(start, min(start + config["batch_size"], count)):
                ident = storage.mint_id()
                if kind == "entry":
                    value = {
                        "id": ident,
                        "conversationId": 1,
                        "kind": "benchmark.entry",
                        "data": {"index": index, "text": f"entry-{index}-" + "x" * 96},
                        **({"head": ident} if index == 0 else {}),
                    }
                    writes.append({"type": "entry", "value": value})
                elif kind == "task":
                    writes.append({"type": "task", "value": task(ident, index)})
                else:
                    writes.append(document(ident, index))
            apply(storage, writes, data)
    return data


def readback(storage, dataset):
    actual = {
        "conversation": [],
        "entry": [],
        "task": [],
        "submission": [],
        "documents": [],
        "entry_seqs": {},
        "request_index": [],
    }
    for kind in ("conversation", "entry", "task", "submission"):
        for expected in dataset[kind]:
            found = getattr(storage, kind)(expected["id"])
            if kind == "entry":
                actual["entry"].append(None if found is None else found.entry.to_tree())
                actual["entry_seqs"][str(expected["id"])] = (
                    None if found is None else found.commit_seq
                )
            else:
                actual[kind].append(None if found is None else found.to_tree())
            if kind == "submission":
                winner = storage.submission_by_request(
                    expected["conversationId"], expected["requestId"]
                )
                actual["request_index"].append(
                    None if winner is None else winner.to_tree()
                )
    for expected in dataset["documents"]:
        record = expected["record"]
        found = storage.document(record["id"])
        actual["documents"].append(None if found is None else found.to_tree())
        address = DocumentAddress(
            {key: record[key] for key in ("scope", "kind", "key")}
        )
        require(
            storage.find_document(address).to_tree() == record,
            "Document address index drift",
            Mismatch,
        )
    return actual


def read_shapes(storage, dataset, config):
    first = storage.scan_entries(
        EntryQuery({"conversationId": 1, "order": "ascending"}), config["batch_size"]
    )
    second = storage.scan_entries(
        EntryQuery({"conversationId": 1, "order": "ascending"}),
        config["batch_size"],
        first.next,
    )
    filtered = storage.scan_tasks(
        TaskQuery(
            {"kind": "benchmark.filtered", "status": "pending", "background": True}
        ),
        50,
    )
    last = dataset["documents"][config["document_count"] - 1]["record"]
    return {
        "exact_entry": storage.entry(dataset["entry"][0]["id"]).entry.to_tree(),
        "page": [record.id for record in first.items],
        "next_page": [record.id for record in second.items],
        "filtered_tasks": [record.id for record in filtered.items],
        "exact_family": storage.find_document(
            DocumentAddress({key: last[key] for key in ("kind", "key", "scope")})
        ).to_tree(),
        "current_document": storage.document(last["id"]).to_tree(),
    }


def observed_counts(storage, batch):
    counts = {}
    for name, method, query in (
        ("entry", storage.scan_entries, EntryQuery({"conversationId": 1})),
        ("task", storage.scan_tasks, TaskQuery({})),
        ("submission", storage.scan_submissions, SubmissionQuery({})),
        (
            "documents",
            storage.scan_documents,
            DocumentQuery({"scope": {"kind": "session"}, "at": "current"}),
        ),
    ):
        count, cursor = 0, None
        pages = 0
        while True:
            pages += 1
            require(pages <= 10000, "Public scan page bound exceeded")
            page = method(query, batch, cursor)
            count += len(page.items)
            cursor = page.next
            if cursor is None:
                break
        counts[name] = count
    return counts


def write_samples(storage, dataset, config):
    for shape, count in (("one-entry", 1), ("100-entries", 100), ("mixed", 4)):
        for sample in range(config["write_samples"]):
            before = observed_counts(storage, config["batch_size"])
            writes = []
            if shape != "mixed":
                for index in range(count):
                    ident = storage.mint_id()
                    writes.append(
                        {
                            "type": "entry",
                            "value": {
                                "id": ident,
                                "conversationId": 1,
                                "kind": "benchmark.write",
                                "data": {
                                    **({"index": index} if count == 100 else {}),
                                    "text": "x" * 128,
                                },
                            },
                        }
                    )
            else:
                entry, work, submission, doc = [storage.mint_id() for _ in range(4)]
                writes = [
                    {
                        "type": "entry",
                        "value": {
                            "id": entry,
                            "conversationId": 1,
                            "kind": "benchmark.mixed",
                        },
                    },
                    {"type": "task", "value": task(work, work)},
                    {
                        "type": "submission",
                        "value": {
                            "id": submission,
                            "conversationId": 1,
                            "requestId": f"benchmark-{submission}",
                            "type": "write",
                            "status": "done",
                            "entry": entry,
                        },
                    },
                    document(
                        doc, doc, "benchmark.mixed", {"entryId": entry, "taskId": work}
                    ),
                ]
            started = time.perf_counter()
            returned_seq = apply(storage, writes, dataset)
            elapsed = time.perf_counter() - started
            dataset["write_samples"].append(
                {
                    "shape": shape,
                    "sample": sample,
                    "submitted_count": len(writes),
                    "before_counts": before,
                    "after_counts": observed_counts(storage, config["batch_size"]),
                    "ids": [
                        write.get("value", write.get("record"))["id"]
                        for write in writes
                    ],
                    "seq": returned_seq,
                    "seconds": elapsed,
                }
            )


def hardware(storage):
    connection = storage._database.connection()
    # Configuration inspection only: workload reads/writes use public Storage.
    return {
        "runtime_source": str(
            Path(sys.modules[open_sqlite_storage.__module__].__file__).resolve()
        ),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "platform": platform.platform(),
        "backend": "sqlite",
        "durability": "full",
        "pragmas": {
            name: connection.execute("PRAGMA " + name).fetchone()[0]
            for name in (
                "synchronous",
                "journal_mode",
                "secure_delete",
                "busy_timeout",
                "fullfsync",
                "checkpoint_fullfsync",
            )
        },
    }


def child(mode, folder):
    config = configuration()
    if mode == "recover":
        recover(folder)
        print(json.dumps({"event": "complete", "pid": os.getpid()}), flush=True)
        return
    path = str(folder / "store.sqlite")
    storage = open_sqlite_storage(path, mode="create", durability="full")
    try:
        environment = hardware(storage)
        started = time.perf_counter()
        data = seed(storage, config)
        seed_seconds = time.perf_counter() - started
        if mode == "unclean":
            dump(folder / "expected.json", data)
            dump(
                folder / "child.json",
                {
                    "seed_seconds": seed_seconds,
                    "hardware": environment,
                    "wal_bytes": Path(path + "-wal").stat().st_size,
                },
            )
            print(
                json.dumps({"event": "committed-wal-ready", "pid": os.getpid()}),
                flush=True,
            )
            require(
                sys.stdin.readline().strip() == "unreachable",
                "Unexpected recovery command",
            )
            return
        started = time.perf_counter()
        reads = [
            read_shapes(storage, data, config) for _ in range(config["read_rounds"])
        ]
        read_seconds = time.perf_counter() - started
        write_samples(storage, data, config)
        wal_bytes = Path(path + "-wal").stat().st_size
    finally:
        storage.close()
    started = time.perf_counter()
    reopened = open_sqlite_storage(path, mode="open", durability="full")
    reopen_seconds = time.perf_counter() - started
    try:
        persisted = readback(reopened, data)
        ids = [
            row["id"]
            for kind in ("conversation", "entry", "task", "submission")
            for row in data[kind]
        ] + [row["record"]["id"] for row in data["documents"]]
        next_id = reopened.mint_id()
        seq = reopened.commit(())
        require(
            next_id == max(ids) + 1 and seq == data["next_seq"],
            "Persisted counters drift",
            Mismatch,
        )
        integrity = (
            reopened._database.connection().execute("PRAGMA integrity_check").fetchall()
        )
    finally:
        reopened.close()
    dump(folder / "expected.json", data)
    dump(
        folder / "result.json",
        {
            "hardware": environment,
            "seed_seconds": seed_seconds,
            "read_seconds": read_seconds,
            "reopen_seconds": reopen_seconds,
            "reads": reads,
            "persisted": persisted,
            "next_id": next_id,
            "next_seq": seq,
            "integrity": integrity,
            "database_bytes": Path(path).stat().st_size,
            "wal_bytes": wal_bytes,
            "wal_after_close_bytes": Path(path + "-wal").stat().st_size
            if Path(path + "-wal").exists()
            else 0,
        },
    )
    print(json.dumps({"event": "complete", "pid": os.getpid()}), flush=True)


def launch(mode, folder):
    """Bound and reap a no-descendant child; never address its PGID after reaping."""
    import selectors

    config = configuration()
    folder.mkdir(mode=0o700, exist_ok=mode == "recover")
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            str(Path(__file__).resolve()),
            "_child",
            mode,
            str(folder),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=folder,
        env={"PATH": os.defpath, "HOME": str(folder), "LC_ALL": "C.UTF-8"},
        start_new_session=True,
    )
    record = {
        "pid": process.pid,
        "group": process.pid,
        "mode": mode,
        "event": None,
        "signal": None,
    }
    stdout, stderr = b"", b""
    try:
        buffer = b""
        deadline = time.monotonic() + config["limits"]["child_seconds"]
        with selectors.DefaultSelector() as ready:
            ready.register(process.stdout, selectors.EVENT_READ)
            while b"\n" not in buffer:
                require(
                    bool(ready.select(timeout=max(0, deadline - time.monotonic()))),
                    "Child readiness deadline",
                )
                chunk = os.read(process.stdout.fileno(), 65536)
                require(bool(chunk), "Child exited before readiness")
                buffer += chunk
                require(len(buffer) <= 65536, "Oversize child event")
        line, remainder = buffer.split(b"\n", 1)
        require(not remainder, "Extra readiness output")
        event = json.loads(line)
        record["event"] = event
        require(
            event
            == {
                "event": "committed-wal-ready" if mode == "unclean" else "complete",
                "pid": process.pid,
            },
            "Unexpected child readiness",
        )
        if mode == "unclean":
            os.killpg(process.pid, signal.SIGKILL)
            record["signal"] = "SIGKILL"
        stdout, stderr = process.communicate(timeout=config["limits"]["child_seconds"])
        require(not stdout and not stderr, "Unexpected child output")
        require(
            process.returncode == (-signal.SIGKILL if mode == "unclean" else 0),
            "Unexpected child exit",
        )
        record["returncode"] = process.returncode
    finally:
        # Popen.returncode is set by communicate only after reaping. Never poll
        # or signal/probe a possibly reused PID/PGID after that point. These
        # workload targets never spawn descendants; this is not a general tree
        # supervisor. On every exceptional path the unreaped leader pins PGID.
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            tail, errors = process.communicate(timeout=10)
        else:
            tail, errors = stdout, stderr
        record["returncode"] = process.returncode
        record["stdout_tail"] = tail.decode(errors="replace")
        record["stderr"] = errors.decode(errors="replace")
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
        record["leader_reaped"] = process.returncode is not None
        dump(
            folder / ("recovery-process.json" if mode == "recover" else "process.json"),
            record,
        )
    return record


def recover(folder):
    config = configuration()
    data = load(folder / "expected.json")
    child_data = load(folder / "child.json")
    path = str(folder / "store.sqlite")
    started = time.perf_counter()
    storage = open_sqlite_storage(path, mode="open", durability="full")
    reopen_seconds = time.perf_counter() - started
    try:
        started = time.perf_counter()
        reads = [
            read_shapes(storage, data, config) for _ in range(config["read_rounds"])
        ]
        read_seconds = time.perf_counter() - started
        persisted = readback(storage, data)
        next_id = storage.mint_id()
        seq = storage.commit(())
        require(
            next_id == max(row["record"]["id"] for row in data["documents"]) + 1
            and seq == data["next_seq"],
            "Recovered counters drift",
            Mismatch,
        )
        integrity = (
            storage._database.connection().execute("PRAGMA integrity_check").fetchall()
        )
    finally:
        storage.close()
    dump(
        folder / "result.json",
        {
            "hardware": child_data["hardware"],
            "seed_seconds": child_data["seed_seconds"],
            "read_seconds": read_seconds,
            "reopen_seconds": reopen_seconds,
            "reads": reads,
            "persisted": persisted,
            "next_id": next_id,
            "next_seq": seq,
            "integrity": integrity,
            "database_bytes": Path(path).stat().st_size,
            "wal_bytes": child_data["wal_bytes"],
            "wal_after_close_bytes": Path(path + "-wal").stat().st_size
            if Path(path + "-wal").exists()
            else 0,
        },
    )


def same(actual, expected):
    """Owned representation equality: bool/int, int/float and signed zero differ."""
    return OwnedJson(actual) == OwnedJson(expected)


def fields(value, names):
    require(type(value) is dict and set(value) == set(names), "Invalid metadata shape")


def integer(value, minimum=0):
    require(type(value) is int and value >= minimum, "Invalid metadata integer")


def integer_map(value, names):
    fields(value, names)
    for item in value.values():
        integer(item)


def process_record(value, mode):
    fields(
        value,
        (
            "pid",
            "group",
            "mode",
            "event",
            "signal",
            "returncode",
            "stdout_tail",
            "stderr",
            "leader_reaped",
        ),
    )
    integer(value["pid"], 1)
    integer(value["group"], 1)
    require(type(value["returncode"]) is int, "Invalid process return code")
    fields(value["event"], ("event", "pid"))
    integer(value["event"]["pid"], 1)
    require(
        value["event"]["pid"] == value["pid"] == value["group"],
        "Wrong process identity",
    )
    require(
        value["mode"] == mode and value["leader_reaped"] is True, "Unreaped/wrong child"
    )
    require(
        value["stderr"] == "" and value["stdout_tail"] == "", "Unexpected child output"
    )
    require(
        value["returncode"] == (-9 if mode == "unclean" else 0), "Wrong process exit"
    )
    require(
        value["signal"] == ("SIGKILL" if mode == "unclean" else None),
        "Wrong child signal",
    )
    require(
        value["event"]["event"]
        == ("committed-wal-ready" if mode == "unclean" else "complete"),
        "Wrong child event",
    )


def admit_metadata(data, result):
    fields(
        data,
        (
            "conversation",
            "entry",
            "task",
            "submission",
            "documents",
            "entry_seqs",
            "next_seq",
            "seed_counts",
            "write_samples",
        ),
    )
    integer(data["next_seq"], 1)
    integer_map(data["seed_counts"], ("entry", "task", "document"))
    require(type(data["entry_seqs"]) is dict, "Invalid sequence map")
    for key, value in data["entry_seqs"].items():
        require(type(key) is str and key.isdecimal(), "Invalid sequence key")
        integer(value, 1)
    for sample in data["write_samples"]:
        fields(
            sample,
            (
                "shape",
                "sample",
                "submitted_count",
                "before_counts",
                "after_counts",
                "ids",
                "seq",
                "seconds",
            ),
        )
        for name in ("sample", "submitted_count", "seq"):
            integer(sample[name])
        require(type(sample["ids"]) is list, "Invalid sample ids")
        for ident in sample["ids"]:
            integer(ident, 1)
        for name in ("before_counts", "after_counts"):
            integer_map(sample[name], ("entry", "task", "submission", "documents"))
    fields(
        result,
        (
            "hardware",
            "seed_seconds",
            "read_seconds",
            "reopen_seconds",
            "reads",
            "persisted",
            "next_id",
            "next_seq",
            "integrity",
            "database_bytes",
            "wal_bytes",
            "wal_after_close_bytes",
        ),
    )
    for name in (
        "next_id",
        "next_seq",
        "database_bytes",
        "wal_bytes",
        "wal_after_close_bytes",
    ):
        integer(result[name])
    environment = result["hardware"]
    fields(
        environment,
        (
            "runtime_source",
            "python",
            "sqlite",
            "platform",
            "backend",
            "durability",
            "pragmas",
        ),
    )
    require(
        all(
            type(environment[name]) is str for name in environment if name != "pragmas"
        ),
        "Invalid environment metadata",
    )
    pragmas = environment["pragmas"]
    fields(
        pragmas,
        (
            "synchronous",
            "journal_mode",
            "secure_delete",
            "busy_timeout",
            "fullfsync",
            "checkpoint_fullfsync",
        ),
    )
    for name in pragmas:
        if name != "journal_mode":
            integer(pragmas[name])
    require(type(pragmas["journal_mode"]) is str, "Invalid journal metadata")


def manifest_shape(value):
    fields(
        value,
        (
            "identity",
            "script_sha256",
            "runtime_sha256",
            "spec",
            "input_sha256",
            "artifacts",
        ),
    )
    require(
        type(value["identity"]) is str and value["identity"] == SPEC["identity"],
        "Wrong manifest identity",
    )
    for name in ("runtime_sha256", "artifacts"):
        require(type(value[name]) is dict, "Invalid manifest hash map")
        require(
            all(
                type(key) is str
                and type(digest) is str
                and len(digest) == 64
                and all(c in "0123456789abcdef" for c in digest)
                for key, digest in value[name].items()
            ),
            "Invalid manifest hash",
        )
    require(type(value["spec"]) is dict, "Invalid manifest spec")
    for name in ("script_sha256", "input_sha256"):
        require(
            type(value[name]) is str and len(value[name]) == 64,
            "Invalid manifest identity hash",
        )


def _validate(data, result, process, unclean, config):
    admit_metadata(data, result)
    process_record(process, "unclean" if unclean else "clean")
    require(
        same(
            data["seed_counts"],
            {kind: config[kind + "_count"] for kind in ("entry", "task", "document")},
        ),
        "Seed operation/scale drift",
    )
    environment = result["hardware"]
    require(
        environment["runtime_source"]
        == str(ROOT / "src/pipy_harness/native/durable/sqlite.py"),
        "Wrong checkout runtime source",
    )
    require(
        environment["backend"] == "sqlite" and environment["durability"] == "full",
        "Backend/durability drift",
    )
    require(
        environment["pragmas"]["synchronous"] == 2
        and environment["pragmas"]["journal_mode"] == "wal"
        and environment["pragmas"]["secure_delete"] == 1,
        "SQLite configuration drift",
    )
    for name in ("seed", "read", "reopen"):
        value = result[name + "_seconds"]
        require(
            type(value) in (int, float) and math.isfinite(value) and value >= 0,
            "Invalid timing",
        )
        require(
            value <= config["limits"][name + "_seconds"],
            "Practical timing ceiling exceeded",
            Mismatch,
        )
    require(
        type(result["database_bytes"]) is int
        and type(result["wal_bytes"]) is int
        and result["database_bytes"] > 0
        and result["wal_bytes"] >= 0,
        "Invalid file sizes",
    )
    require(
        result["database_bytes"] <= config["limits"]["database_bytes"]
        and result["wal_bytes"] <= config["limits"]["wal_bytes"],
        "Practical size ceiling exceeded",
        Mismatch,
    )
    if unclean:
        require(result["wal_bytes"] > 0, "Missing committed WAL")
    persisted = result["persisted"]
    for kind in (
        "conversation",
        "entry",
        "task",
        "submission",
        "documents",
        "entry_seqs",
    ):
        require(
            same(persisted[kind], data[kind]),
            "Persisted field/count/sequence mismatch",
            Mismatch,
        )
    require(
        same(persisted["request_index"], data["submission"]),
        "Persisted request index mismatch",
        Mismatch,
    )
    require(result["integrity"] == [["ok"]], "Integrity failure", Mismatch)
    samples = data["write_samples"]
    require(
        len(samples) == (0 if unclean else 3 * config["write_samples"]),
        "Write sample count drift",
    )
    for index, sample in enumerate(samples):
        shape, count = (("one-entry", 1), ("100-entries", 100), ("mixed", 4))[
            index // config["write_samples"]
        ]
        require(
            sample["shape"] == shape
            and sample["sample"] == index % config["write_samples"]
            and len(sample["ids"]) == count,
            "Write operation inventory drift",
        )
        require(
            sample["seq"] == data["entry_seqs"][str(sample["ids"][0])],
            "Actual returned commit sequence mismatch",
            Mismatch,
        )
        require(
            same(sample["submitted_count"], count),
            "False submitted write count",
            Mismatch,
        )
        require(
            type(sample["seconds"]) in (int, float)
            and math.isfinite(sample["seconds"])
            and sample["seconds"] >= 0,
            "Invalid write timing",
        )
        require(
            sample["seconds"] <= config["limits"]["sample_seconds"],
            "Write ceiling exceeded",
            Mismatch,
        )
        expected_delta = {
            "entry": 1 if shape != "100-entries" else 100,
            "task": int(shape == "mixed"),
            "submission": int(shape == "mixed"),
            "documents": int(shape == "mixed"),
        }
        require(
            {
                kind: sample["after_counts"][kind] - sample["before_counts"][kind]
                for kind in expected_delta
            }
            == expected_delta,
            "Write count delta mismatch",
            Mismatch,
        )
    require(
        len(data["entry"])
        == (config["entry_count"] + (0 if unclean else 102 * config["write_samples"]))
        and len(data["task"])
        == (config["task_count"] + (0 if unclean else config["write_samples"]))
        and len(data["documents"])
        == (config["document_count"] + (0 if unclean else config["write_samples"]))
        and len(data["submission"]) == (0 if unclean else config["write_samples"]),
        "Persisted operation totals drift",
        Mismatch,
    )
    require(len(result["reads"]) == config["read_rounds"], "Read rounds drift")
    expected_filtered = [
        row["id"]
        for row in data["task"][: config["task_count"]]
        if row["kind"] == "benchmark.filtered"
        and row["state"]["status"] == "pending"
        and row["background"]
    ]
    expected_read = {
        "exact_entry": data["entry"][0],
        "page": [row["id"] for row in data["entry"][: config["batch_size"]]],
        "next_page": [
            row["id"]
            for row in data["entry"][config["batch_size"] : 2 * config["batch_size"]]
        ],
        "filtered_tasks": expected_filtered,
        "exact_family": data["documents"][config["document_count"] - 1]["record"],
        "current_document": data["documents"][config["document_count"] - 1],
    }
    for read in result["reads"]:
        require(same(read, expected_read), "Read shape/index mismatch", Mismatch)
    require(
        result["next_seq"] == data["next_seq"],
        "Persisted next sequence mismatch",
        Mismatch,
    )
    all_ids = [
        row["id"]
        for kind in ("conversation", "entry", "task", "submission")
        for row in data[kind]
    ] + [row["record"]["id"] for row in data["documents"]]
    require(
        len(set(all_ids)) == len(all_ids) and result["next_id"] == max(all_ids) + 1,
        "Persisted ID counter mismatch",
        Mismatch,
    )


def validate(data, result, process, unclean, config=None):
    config = configuration() if config is None else config
    try:
        return _validate(data, result, process, unclean, config)
    except Invalid:
        raise
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise Invalid("Malformed workload evidence") from error


def verify(folder):
    config = configuration()
    manifest = load(folder / "manifest.json")
    manifest_shape(manifest)
    require(
        manifest["input_sha256"] == sha(INPUT)
        and same(manifest["spec"], config)
        and manifest["script_sha256"] == sha(Path(__file__)),
        "Input identity drift",
    )
    require(
        manifest["runtime_sha256"]
        == {name: sha(ROOT / name) for name in RUNTIME_SOURCES},
        "Runtime source identity drift",
    )
    require(
        set(manifest["artifacts"])
        == {
            f"{mode}/{name}.json"
            for mode in ("clean", "unclean")
            for name in ("expected", "result", "process")
        }
        | {
            "unclean/child.json",
            "unclean/recovery-process.json",
            "clean/store.sqlite",
            "unclean/store.sqlite",
        },
        "Artifact inventory drift",
    )
    for name, digest in manifest["artifacts"].items():
        path = folder / name
        require(
            path.is_file() and not path.is_symlink() and sha(path) == digest,
            "Artifact identity drift",
        )
    recovery_process = load(folder / "unclean/recovery-process.json")
    process_record(recovery_process, "recover")
    for mode in ("clean", "unclean"):
        validate(
            load(folder / mode / "expected.json"),
            load(folder / mode / "result.json"),
            load(folder / mode / "process.json"),
            mode == "unclean",
            config,
        )
    return 0


def run(folder):
    # Anchor cwd without following an existing output symlink.
    folder = folder.absolute()
    configuration()
    captured = {
        "script": sha(Path(__file__)),
        "input": sha(INPUT),
        "runtime": {name: sha(ROOT / name) for name in RUNTIME_SOURCES},
    }

    def stable():
        require(
            captured
            == {
                "script": sha(Path(__file__)),
                "input": sha(INPUT),
                "runtime": {name: sha(ROOT / name) for name in RUNTIME_SOURCES},
            },
            "Input/runtime changed during workload",
        )

    folder.mkdir(mode=0o700, exist_ok=False)
    for mode in ("clean", "unclean"):
        launch(mode, folder / mode)
        stable()
        if mode == "unclean":
            launch("recover", folder / mode)
            stable()
    artifacts = {
        str(path.relative_to(folder)): sha(path)
        for mode in ("clean", "unclean")
        for path in (folder / mode).glob("*.json")
    }
    artifacts.update(
        {
            f"{mode}/store.sqlite": sha(folder / mode / "store.sqlite")
            for mode in ("clean", "unclean")
        }
    )
    stable()
    dump(
        folder / "manifest.json",
        {
            "identity": SPEC["identity"],
            "script_sha256": sha(Path(__file__)),
            "runtime_sha256": {name: sha(ROOT / name) for name in RUNTIME_SOURCES},
            "spec": SPEC,
            "input_sha256": sha(INPUT),
            "artifacts": artifacts,
        },
    )
    return verify(folder)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run").add_argument("--output", type=Path, required=True)
    commands.add_parser("verify").add_argument("root", type=Path)
    internal = commands.add_parser("_child")
    internal.add_argument("mode", choices=("clean", "unclean", "recover"))
    internal.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "_child":
            child(args.mode, args.root)
            return 0
        return run(args.output) if args.command == "run" else verify(args.root)
    except Mismatch as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 -- fail closed; BaseException still propagates
        print(f"Invalid workload evidence: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
