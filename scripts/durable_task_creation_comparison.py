"""Actual pinned typed task creation: compare 0 equal, 1 mismatch, 2 invalid."""

from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.util
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "task_creation_documents", ROOT / "scripts/durable_documents_comparison.py"
)
assert spec and spec.loader
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
d, b, o, v = a.d, a.b, a.o, a.v
from pipy_harness.native.durable.memory import MemoryStorage  # noqa: E402
from pipy_harness.native.durable.session_core import Session  # noqa: E402
from pipy_harness.native.durable.sqlite import open_sqlite_storage  # noqa: E402
from pipy_harness.native.durable.storage import StorageWrite  # noqa: E402
from pipy_harness.native.durable.tasks import define_task  # noqa: E402

PHASE = "task-creation-a-v1"
MANIFEST = o.FIXTURES / "task-creation-v1.manifest.json"
DRIVER = ROOT / "scripts/durable_reference/cases/task-creation.mts"
DESTINATION = "task-creation.mts"
MODULES = set(b.MODULES) - {
    "packages/durable/src/documents.ts",
    "packages/chord/src/delta/index.ts",
} | {"packages/durable/src/tasks.ts"}
SOURCES = b.SOURCES | {"packages/durable/src/tasks.ts"}
CLOSURE = a.CLOSURE | {
    "scripts/durable_task_creation_comparison.py",
    "scripts/durable_reference/cases/task-creation.mts",
    "tests/durable/test_task_creation.py",
    "tests/durable/test_task_creation_comparison.py",
    "docs/durable-task-creation.md",
}
CASES = (
    "v-zero",
    "v-negative",
    "v-fraction",
    "v-negative-zero",
    "v-max",
    "initial-mutation",
    "child",
    "missing-owner",
    "background-child",
    "missing-conversation",
    "initializer-throw",
    "unregistered-phase",
)
MUTANTS = (
    "kind",
    "version",
    "input",
    "checkpoint",
    "ownership",
    "order",
    "rollback",
    "publication",
)
FIELDS = {"result", "stored", "writes", "publications"}


def recipe():
    versions = {
        "v-zero": 0,
        "v-negative": -1,
        "v-fraction": 1.5,
        "v-negative-zero": -0.0,
        "v-max": 1.7976931348623157e308,
    }
    return {
        "schema_version": 1,
        "phase": PHASE,
        "cases": [{"id": name, "version": versions.get(name, 1)} for name in CASES],
    }


async def unexecuted(*args: Any) -> None:
    raise AssertionError("Creation must not dispatch")


class Recording:
    def __init__(self, inner):
        self.inner = inner
        self.calls = []
        self.writes = []

    def mint_id(self):
        self.calls.append("mint")
        return self.inner.mint_id()

    def conversation(self, id):
        self.calls.append("conversation")
        return self.inner.conversation(id)

    def task(self, id):
        self.calls.append("task")
        return self.inner.task(id)

    def commit(self, writes):
        self.calls.append("commit")
        self.writes.extend(write.to_tree() for write in writes)
        return self.inner.commit(writes)

    def __getattr__(self, name):
        return getattr(self.inner, name)


async def native_case(case, backend, folder):
    path = str(folder / (case["id"] + ".sqlite"))
    inner = (
        MemoryStorage()
        if backend == "memory"
        else open_sqlite_storage(path, mode="create")
    )
    owner = {
        "id": 20,
        "conversationId": 7,
        "kind": "owner",
        "version": 1,
        "input": None,
        "background": False,
        "abortRequested": False,
        "state": {"status": "pending", "checkpoint": None},
    }
    inner.commit(
        tuple(
            StorageWrite(value)
            for value in (
                {"type": "conversation", "value": {"id": 1}},
                {"type": "conversation", "value": {"id": 7}},
                {"type": "task", "value": owner},
            )
        )
    )
    store = Recording(inner)
    session = Session(store, now=lambda: 1000)
    publications = []
    session.observe_commits(
        lambda publication: publications.append(
            [change.to_tree() for change in publication.changes]
        )
    )
    caller = {"items": [1], "zero": -0.0}
    failure = ValueError("initial failed")

    def initial(value):
        store.calls.append("initial")
        if case["id"] == "initializer-throw":
            raise failure
        if case["id"] == "initial-mutation":
            value["items"].append(2)
        return (
            None
            if case["id"] == "unregistered-phase"
            else {"phase": "start", "value": value}
        )

    task = define_task(
        "typed.work", case["version"], initial, {"start": unexecuted}, unexecuted
    )
    ownership = (
        {"kind": "task", "taskId": 999 if case["id"] == "missing-owner" else 20}
        if case["id"] in {"child", "background-child", "missing-owner"}
        else {"kind": "conversation"}
    )
    options = (
        {}
        if ownership["kind"] == "task"
        else {"conversation_id": 999 if case["id"] == "missing-conversation" else 7}
    )
    if case["id"] == "background-child":
        options["background"] = True
    status, error, id = "fulfilled", None, None
    try:
        id = await session.acommit(
            lambda tx: tx.create_task(task, caller, ownership=ownership, **options)
        )
    except Exception as caught:  # noqa: BLE001 - compared application outcome
        status, error = "rejected", "initializer" if caught is failure else str(caught)
    stored = None if id is None else inner.task(id).to_tree()
    result = {
        "id": case["id"],
        "status": status,
        "error": error,
        "result": v.encode(id),
        "calls": store.calls.copy(),
        "stored": v.encode(stored),
        "writes": v.encode(store.writes),
        "publications": v.encode(publications),
    }
    await session.aclose()
    if backend == "reopen":
        reopened = open_sqlite_storage(path, mode="open")
        try:
            d.require(
                stored is None
                or v.encode(reopened.task(id).to_tree()) == result["stored"],
                "Typed creation reopen differs",
            )
        finally:
            reopened.close()
    return result


async def native(folder):
    folder.mkdir(mode=0o700)
    lanes = []
    for backend in ("memory", "sqlite", "reopen"):
        sub = folder / backend
        sub.mkdir(mode=0o700)
        lanes.append(
            [await native_case(case, backend, sub) for case in recipe()["cases"]]
        )
    d.require(
        all(d.same(lanes[0], lane) for lane in lanes[1:]),
        "Native typed backend disagreement",
    )
    return {"schema_version": 1, "phase": PHASE, "cases": lanes[0]}


def trace(value):
    d.shape(value, {"schema_version", "phase", "cases"}, "typed creation trace")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Typed creation identity",
    )
    rows = value["cases"]
    d.require(
        type(rows) is list
        and [row.get("id") for row in rows if type(row) is dict] == list(CASES),
        "Typed creation cases",
    )
    for row in rows:
        d.shape(row, {"id", "status", "error", "calls"} | FIELDS, "typed creation case")
        d.require(
            row["status"] in {"fulfilled", "rejected"}
            and (row["error"] is None or type(row["error"]) is str),
            "Typed creation outcome",
        )
        d.require(
            type(row["calls"]) is list
            and all(
                type(call) is str
                and call in {"conversation", "task", "mint", "commit", "initial"}
                for call in row["calls"]
            ),
            "Typed creation calls",
        )
        for key in FIELDS:
            decoded = v.decode(row[key])
            d.require(v.encode(decoded) == row[key], "Typed creation profile")
    return value


def compare(expected, actual):
    trace(expected)
    trace(actual)
    return int(not d.same(expected, actual))


def mutate(actual, name):
    changed = copy.deepcopy(actual)
    row = changed["cases"][
        6 if name == "ownership" else 10 if name == "rollback" else 0
    ]
    if name == "order":
        row["calls"] = list(reversed(row["calls"]))
    elif name == "rollback":
        row["calls"].append("commit")
    elif name == "publication":
        row["publications"] = v.encode([])
    else:
        record = v.decode(row["stored"])
        if name == "checkpoint":
            record["state"]["checkpoint"] = {"phase": "lost"}
        elif name == "ownership":
            record.pop("owner")
        else:
            record[{"kind": "kind", "version": "version", "input": "input"}[name]] = (
                "wrong" if name != "version" else 2
            )
        row["stored"] = v.encode(record)
    trace(changed)
    return changed


def configuration():
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
        },
        "typed creation manifest",
    )
    d.require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN,
        "Typed creation manifest identity",
    )
    for key, names in (("closure", CLOSURE), ("reference_sources", SOURCES)):
        d.shape(config[key], names, "typed creation " + key)
        for name, digest in config[key].items():
            o.contract.digest(digest)
            if key == "closure":
                d.require(o.sha(ROOT / name) == digest, "Typed creation closure drift")
    d.require(
        config["parents"] == {"documents": o.sha(a.MANIFEST)},
        "Typed creation parent drift",
    )
    a.configuration()
    return config


def native_modules():
    modules = {}
    for relative in sorted(CLOSURE):
        if not relative.startswith(
            "src/pipy_harness/native/durable/"
        ) or not relative.endswith(".py"):
            continue
        name = (
            relative.removeprefix("src/")
            .removesuffix(".py")
            .replace("/", ".")
            .removesuffix(".__init__")
        )
        module = sys.modules.get(name)
        d.require(module is not None, "Typed creation native module not imported")
        path = Path(module.__file__).resolve()
        d.require(
            path == ROOT / relative, "Typed creation native module outside closure"
        )
        modules[relative] = o.sha(path)
    return modules


def execute(acceptance, output):
    d.require(
        not output.exists() and not output.is_symlink(), "Typed creation output exists"
    )
    acceptance, output = acceptance.resolve(strict=True), output.absolute()
    d.require(
        not output.resolve().is_relative_to(acceptance),
        "Typed creation output inside parent",
    )
    config = configuration()
    manifest_sha = o.sha(MANIFEST)
    parent = o.manifest()
    v.artifacts.attest(acceptance, o.FIXTURES)
    run = o.acceptance.attest_reference(o, acceptance / "reference", parent, o.FIXTURES)
    snapshot = acceptance / "reference/snapshot"
    o.verify_sources(snapshot, config["reference_sources"])
    driver = snapshot / DESTINATION
    d.require(
        not driver.exists() and not driver.is_symlink(), "Typed creation driver exists"
    )
    output.mkdir(mode=0o700)
    processes, mutants = output / "processes", output / "mutants"
    processes.mkdir(mode=0o700)
    mutants.mkdir(mode=0o700)
    d.dump(output / "input.json", recipe())
    shutil.copyfile(DRIVER, driver)
    env = o.child_env(output)
    for binary in ("node", "npm"):
        stdout, stderr, code = d.process(
            [binary, "--version"], snapshot, processes, binary + "-version", env, 15
        )
        d.require(
            code == 0
            and not stderr
            and stdout.decode().strip() == parent["runtime"][binary],
            "Typed creation runtime drift",
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
        processes,
        "pi-typed-creation",
        env,
        120,
    )
    d.require(
        code == 0 and not stdout and not stderr, "Pinned typed creation driver failed"
    )
    modules = o.verify_resolutions(
        snapshot,
        d.load(output / "resolutions.json"),
        d.load(acceptance / "reference/source-sha256.json"),
        {DESTINATION: o.sha(DRIVER)},
        sorted(MODULES),
    )
    o.module_dependencies(snapshot, modules, run["installed_dependencies"])
    expected = trace(d.load(output / "observed.json"))
    imports = native_modules()
    actual = asyncio.run(native(output / "stores"))
    d.require(imports == native_modules(), "Typed creation native import drift")
    d.dump(output / "native.json", actual)
    status = compare(expected, actual)
    controls = []
    for name in MUTANTS:
        path = mutants / (name + ".json")
        d.dump(path, mutate(actual, name))
        for label, source, wanted in (
            ("positive", output / "native.json", 0),
            ("negative", path, 1),
        ):
            _, _, code = d.process(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "compare",
                    str(output / "observed.json"),
                    str(source),
                ],
                ROOT,
                processes,
                name + "-" + label,
                env,
                60,
            )
            d.require(code == wanted, "Typed creation semantic CLI control failed")
        controls.append(name)
    invalid = copy.deepcopy(actual)
    invalid["unknown"] = True
    d.dump(mutants / "invalid.json", invalid)
    _, _, code = d.process(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "compare",
            str(output / "observed.json"),
            str(mutants / "invalid.json"),
        ],
        ROOT,
        processes,
        "invalid-cli",
        env,
        60,
    )
    d.require(code == 2, "Typed creation invalid CLI control failed")
    configuration()
    o.verify_sources(snapshot, config["reference_sources"])
    o.acceptance.attest_reference(o, acceptance / "reference", parent, o.FIXTURES)
    v.artifacts.attest(acceptance, o.FIXTURES)
    d.require(
        manifest_sha == o.sha(MANIFEST) and o.sha(driver) == o.sha(DRIVER),
        "Typed creation producer drift",
    )
    d.dump(
        output / "result.json",
        {
            "schema_version": 1,
            "phase": PHASE,
            "status": "PASS" if status == 0 else "mismatch",
            "execution_id": str(uuid.uuid4()),
            "parent_execution_id": d.load(acceptance / "acceptance.json")[
                "execution_id"
            ],
            "parent_acceptance_sha256": o.sha(acceptance / "acceptance.json"),
            "manifest_sha256": manifest_sha,
            "module_sha256": modules,
            "native_module_sha256": imports,
            "artifacts_sha256": artifact_hashes(output),
            "native_backends": ["memory", "sqlite", "reopen"],
            "cases": len(CASES),
            "mutants": controls,
            "invalid_exit": 2,
            "fresh_reference_bootstrap": False,
            "scheduler": False,
        },
    )
    return status


def process_identity(record, label, folder, snapshot):
    """Bind a receipt to the actual CLI/driver input, not just an exit code."""
    processes = folder / "processes"
    d.process_record(record, processes, label)
    if label in ("node-version", "npm-version"):
        binary = label.split("-")[0]
        wanted = [binary, "--version"]
        cwd = snapshot
    elif label == "pi-typed-creation":
        wanted = [
            "node",
            "--import",
            str(snapshot / o.RESOLVER),
            str(snapshot / DESTINATION),
            str(folder / "input.json"),
            str(folder),
        ]
        cwd = snapshot
    else:
        source = (
            folder / "native.json"
            if label.endswith("-positive")
            else folder / "mutants" / "invalid.json"
            if label == "invalid-cli"
            else folder / "mutants" / (label.removesuffix("-negative") + ".json")
        )
        wanted = [
            sys.executable,
            str(Path(__file__).resolve()),
            "compare",
            str(folder / "observed.json"),
            str(source),
        ]
        cwd = ROOT
    d.require(
        record["argv"] == wanted and record["cwd"] == str(cwd),
        "Typed creation child identity drift",
    )
    stdout, stderr = (
        (processes / (label + ".stdout")).read_bytes(),
        (processes / (label + ".stderr")).read_bytes(),
    )
    if label in ("node-version", "npm-version"):
        d.require(
            stdout.decode().strip() == o.manifest()["runtime"][label.split("-")[0]]
            and not stderr,
            "Typed creation runtime receipt drift",
        )
    elif label == "invalid-cli":
        d.require(
            not stdout
            and stderr.startswith(b"INVALID:")
            and b"Traceback" not in stderr,
            "Typed creation invalid CLI diagnostics",
        )
    else:
        d.require(not stdout and not stderr, "Typed creation child diagnostics")


def artifact_hashes(folder):
    """Keep producer artifacts even when an output ancestor is named stores."""
    return {
        str(path.relative_to(folder)): o.sha(path)
        for path in folder.rglob("*")
        if path.is_file()
        and path != folder / "result.json"
        and not path.is_relative_to(folder / "stores")
    }


def attest(folder, acceptance):
    """Recheck actual producers, full artifacts, loaded modules and CLI receipts."""
    config = configuration()
    v.artifacts.attest(acceptance, o.FIXTURES)
    parent = o.acceptance.attest_reference(
        o, acceptance / "reference", o.manifest(), o.FIXTURES
    )
    snapshot = acceptance / "reference/snapshot"
    o.verify_sources(snapshot, config["reference_sources"])
    value = d.load(folder / "result.json")
    expected = {
        "schema_version": 1,
        "phase": PHASE,
        "status": "PASS",
        "parent_execution_id": d.load(acceptance / "acceptance.json")["execution_id"],
        "parent_acceptance_sha256": o.sha(acceptance / "acceptance.json"),
        "manifest_sha256": o.sha(MANIFEST),
        "native_backends": ["memory", "sqlite", "reopen"],
        "cases": len(CASES),
        "mutants": list(MUTANTS),
        "invalid_exit": 2,
        "fresh_reference_bootstrap": False,
        "scheduler": False,
    }
    d.shape(
        value,
        set(expected)
        | {"execution_id", "module_sha256", "native_module_sha256", "artifacts_sha256"},
        "typed creation result",
    )
    for key, wanted in expected.items():
        d.require(d.same(value[key], wanted), "Typed creation result drift: " + key)
    d.require(
        str(uuid.UUID(value["execution_id"])) == value["execution_id"],
        "Typed creation execution ID",
    )
    files = artifact_hashes(folder)
    d.require(d.same(value["artifacts_sha256"], files), "Typed creation artifact drift")
    reference, actual = d.load(folder / "observed.json"), d.load(folder / "native.json")
    d.require(
        compare(reference, actual) == 0
        and d.same(d.load(folder / "input.json"), recipe()),
        "Typed creation actual pair/input drift",
    )
    modules = o.verify_resolutions(
        snapshot,
        d.load(folder / "resolutions.json"),
        d.load(acceptance / "reference/source-sha256.json"),
        {DESTINATION: o.sha(DRIVER)},
        sorted(MODULES),
    )
    o.module_dependencies(snapshot, modules, parent["installed_dependencies"])
    d.require(
        d.same(modules, value["module_sha256"])
        and o.sha(snapshot / DESTINATION) == o.sha(DRIVER),
        "Typed creation loaded modules drift",
    )
    d.require(
        d.same(value["native_module_sha256"], native_modules()),
        "Typed creation native producer drift",
    )
    results = {
        "node-version": 0,
        "npm-version": 0,
        "pi-typed-creation": 0,
        "invalid-cli": 2,
    }
    for name in MUTANTS:
        mutant = d.load(folder / "mutants" / (name + ".json"))
        d.require(
            d.same(mutant, mutate(actual, name)) and compare(reference, mutant) == 1,
            "Typed creation mutant drift",
        )
        results[name + "-positive"] = 0
        results[name + "-negative"] = 1
    for label, code in results.items():
        process = d.load(folder / "processes" / (label + ".json"))
        process_identity(process, label, folder, snapshot)
        d.require(process["returncode"] == code, "Typed creation child exit drift")
    invalid = copy.deepcopy(actual)
    invalid["unknown"] = True
    retained = d.load(folder / "mutants" / "invalid.json")
    d.require(
        d.same(retained, invalid), "Typed creation invalid input was not retained"
    )
    try:
        trace(retained)
    except o.EvidenceError:
        pass
    else:
        raise o.EvidenceError("Typed creation invalid producer became valid")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--acceptance", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    cmp = sub.add_parser("compare")
    cmp.add_argument("expected", type=Path)
    cmp.add_argument("actual", type=Path)
    args = parser.parse_args()
    try:
        return (
            execute(args.acceptance, args.output)
            if args.command == "run"
            else compare(d.load(args.expected), d.load(args.actual))
        )
    except (o.EvidenceError, OSError, ValueError, TypeError, RecursionError) as error:
        print("INVALID: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
