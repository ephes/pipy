"""Actual pinned task runtime: compare 0 equal, 1 mismatch, 2 invalid."""

from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.util
import shutil
import sys
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "task_runtime_documents", ROOT / "scripts/durable_documents_comparison.py"
)
assert spec and spec.loader
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
d, b, o, v = a.d, a.b, a.o, a.v
# The scheduler's DE08 graph path lazily reaches these real product modules.
# Load the same dependency roots in execute and fresh-process attestation so
# actual imported module identity is stable without rerunning product effects.
from pipy_harness.native.durable.generation import ROUND_DOC  # noqa: E402,F401
from pipy_harness.native.durable.memory import MemoryStorage  # noqa: E402
from pipy_harness.native.durable.scheduler import TaskScheduler  # noqa: E402
from pipy_harness.native.durable.session_core import Session  # noqa: E402
from pipy_harness.native.durable.sqlite import open_sqlite_storage  # noqa: E402
from pipy_harness.native.durable.storage import StorageWrite  # noqa: E402
from pipy_harness.native.durable.tasks import define_task  # noqa: E402
from pipy_harness.native.durable.tool_graph import capture_entries  # noqa: E402,F401

PHASE = "task-runtime-b-v1"
MANIFEST = o.FIXTURES / "task-runtime-v1.manifest.json"
DRIVER = ROOT / "scripts/durable_reference/cases/task-runtime.mts"
DESTINATION = "task-runtime.mts"
MODULES = set(b.MODULES) - {
    "packages/durable/src/documents.ts",
    "packages/chord/src/delta/index.ts",
} | {"packages/durable/src/tasks.ts", "packages/durable/src/harness/scheduler.ts"}
SOURCES = b.SOURCES | {
    "packages/durable/src/tasks.ts",
    "packages/durable/src/harness/scheduler.ts",
}
CLOSURE = (
    a.CLOSURE
    | {str(p.relative_to(ROOT)) for p in (ROOT / "src").rglob("*.py")}
    | {
        "src/pipy_harness/native/durable/scheduler.py",
        "src/pipy_harness/native/durable/task_values.py",
        "tests/durable/test_task_runtime_sqlite.py",
        "scripts/durable_task_runtime_comparison.py",
        "scripts/durable_reference/cases/task-runtime.mts",
        "tests/durable/test_task_runtime.py",
        "tests/durable/test_task_migration_core.py",
        "tests/durable/test_task_migration.py",
        "tests/durable/task_migration_support.py",
        "tests/durable/test_restart_policy_runtime.py",
        "tests/durable/test_restart_parking_sqlite.py",
        "tests/durable/adjudication_sdk_support.py",
        "tests/durable/test_restart_retained_progress.py",
        "tests/durable/test_task_runtime_comparison.py",
        "docs/durable-task-runtime.md",
        "docs/durable-task-migration.md",
        "docs/specs/2026-10-28-de14b1-cold-task-migration-plan.md",
        "docs/durable-restart-parking.md",
        "docs/specs/2026-10-28-de14a-restart-parking-plan.md",
    }
)
CASES = ("counter", "recovery", "no-progress", "throws", "terminal-late", "close")
MUTANTS = ("dispatch", "checkpoint", "memo", "restarts", "result", "late", "close")


def recipe():
    return {
        "schema_version": 1,
        "phase": PHASE,
        "cases": [{"id": name} for name in CASES],
    }


class Recording:
    def __init__(self, inner):
        self.inner = inner
        self.last = None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def close(self):
        self.last = self.inner.task(20).to_tree()
        return self.inner.close()


def _handler(name, phases, memos, runtimes, reached, flags):
    async def phase(task, runtime, context):
        phases.append(task["state"]["checkpoint"]["n"])
        if not any(old is runtime for old in runtimes):
            runtimes.append(runtime)
        memos.append(await runtime.memo("stable", None))
        memos.append(await runtime.memo("stable", 999))
        if name == "throws":
            raise ValueError("boom")
        if name == "no-progress":
            await runtime.commit(
                lambda tx, current: {
                    "status": "running",
                    "checkpoint": {"n": 0, "phase": "start"},
                }
            )
            return
        if name == "close":
            await runtime.commit(
                lambda tx, current: {
                    "status": "running",
                    "checkpoint": {"phase": "start", "n": 1},
                }
            )
            reached.set()
            await context.signal.wait()
            return
        n = task["state"]["checkpoint"]["n"] + 1
        await runtime.commit(
            lambda tx, current: (
                {"status": "terminal", "outcome": {"status": "completed", "result": n}}
                if n == 2 or name == "terminal-late"
                else {"status": "running", "checkpoint": {"phase": "start", "n": n}}
            )
        )
        if name == "terminal-late":
            try:
                await runtime.memo("late", 1)
            except RuntimeError:
                flags["late"] = True
            raise ValueError("after terminal")

    return phase


async def native_case(case, backend, folder):
    name = case["id"]
    path = str(folder / (name + ".sqlite"))
    inner = (
        MemoryStorage()
        if backend == "memory"
        else open_sqlite_storage(path, mode="create")
    )
    row = {
        "id": 20,
        "conversationId": 1,
        "kind": "typed.work",
        "version": 1,
        "input": {"to": 2},
        "background": False,
        "abortRequested": False,
        "state": {
            "status": "running" if name == "recovery" else "pending",
            "checkpoint": {"phase": "start", "n": 1 if name == "recovery" else 0},
        },
    }
    if name == "recovery":
        row.update(restarts=2, memos={"stable": None}, startedAt=1000)
    inner.commit(
        (
            StorageWrite({"type": "conversation", "value": {"id": 1}}),
            StorageWrite({"type": "task", "value": row}),
        )
    )
    if backend == "reopen":
        inner.close()
        inner = open_sqlite_storage(path, mode="open")
    store = Recording(inner)
    session = Session(store, now=lambda: 1000)
    phases, memos, runtimes = [], [], []
    flags = {"late": False}
    reached = threading.Event()

    phase = _handler(name, phases, memos, runtimes, reached, flags)

    async def abort(*args):
        raise AssertionError("abort not selected")

    definition = define_task(
        "typed.work",
        1,
        lambda _: (_ for _ in ()).throw(AssertionError("initializer reran")),
        {"start": phase},
        abort,
    )
    scheduler = TaskScheduler(session, [definition])
    await scheduler.aopen()
    before = store.task(20).to_tree()
    before_calls = len(phases)

    def observe(publication):
        if store.task(20).to_tree()["state"]["status"] == "terminal":
            reached.set()

    session.subscribe_commits(observe)
    scheduler.resume()
    scheduler.resume()
    try:
        d.require(
            await asyncio.to_thread(reached.wait, 10), "Native task did not settle"
        )
    finally:
        await session.aclose()
    return {
        "id": name,
        "observed": v.encode(
            {
                "before": before,
                "before_calls": before_calls,
                "phases": phases,
                "memos": memos,
                "runtimes": len(runtimes),
                "late_refused": flags["late"],
                "stored": store.last,
                "closed": session.end.reason,
            }
        ),
    }


async def native(folder):
    outputs = []
    for backend in ("memory", "sqlite", "reopen"):
        location = folder / backend
        location.mkdir(parents=True)
        outputs.append(
            {
                "schema_version": 1,
                "phase": PHASE,
                "cases": [
                    await native_case(case, backend, location)
                    for case in recipe()["cases"]
                ],
            }
        )
    d.require(
        all(compare(outputs[0], row) == 0 for row in outputs[1:]),
        "Native backend runtime mismatch",
    )
    return outputs[0]


def trace(value):
    d.shape(value, {"schema_version", "phase", "cases"}, "task runtime trace")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Task runtime identity",
    )
    rows = value["cases"]
    d.require(
        type(rows) is list
        and [r.get("id") for r in rows if type(r) is dict] == list(CASES),
        "Task runtime cases",
    )
    for row in rows:
        d.shape(row, {"id", "observed"}, "Task runtime row")
        decoded = v.decode(row["observed"])
        d.shape(
            decoded,
            {
                "before",
                "before_calls",
                "phases",
                "memos",
                "runtimes",
                "late_refused",
                "stored",
                "closed",
            },
            "Task runtime observation",
        )
        d.require(v.encode(decoded) == row["observed"], "Task runtime value profile")
    return value


def compare(expected, actual):
    trace(expected)
    trace(actual)
    return int(not d.same(expected, actual))


def mutate(actual, name):
    changed = copy.deepcopy(actual)
    row = changed["cases"][
        1
        if name == "restarts"
        else 4
        if name == "late"
        else 5
        if name == "close"
        else 0
    ]
    observed = v.decode(row["observed"])
    if name == "dispatch":
        observed["before_calls"] = 1
    elif name == "checkpoint":
        observed["phases"] = [0, 0]
    elif name == "memo":
        observed["memos"] = [None, 999]
    elif name == "restarts":
        observed["before"]["restarts"] = 4
    elif name == "result":
        observed["stored"]["state"]["outcome"]["result"] = 999
    elif name == "late":
        observed["late_refused"] = False
    else:
        observed["stored"]["state"]["status"] = "running"
    row["observed"] = v.encode(observed)
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
        "task runtime manifest",
    )
    d.require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN,
        "Task runtime manifest identity",
    )
    for key, names in (("closure", CLOSURE), ("reference_sources", SOURCES)):
        d.shape(config[key], names, "task runtime " + key)
        for name, digest in config[key].items():
            o.contract.digest(digest)
            if key == "closure":
                d.require(o.sha(ROOT / name) == digest, "Task runtime closure drift")
    d.require(
        config["parents"] == {"documents": o.sha(a.MANIFEST)},
        "Task runtime parent drift",
    )
    a.configuration()
    return config


def native_modules():
    modules = {}
    for name, module in tuple(sys.modules.items()):
        if name != "pipy_harness" and not name.startswith("pipy_harness."):
            continue
        filename = getattr(module, "__file__", None)
        d.require(filename is not None, "Task runtime producer lacks a source file")
        path = Path(filename).resolve()
        d.require(
            path.is_relative_to(ROOT), "Task runtime native module outside checkout"
        )
        relative = str(path.relative_to(ROOT))
        d.require(relative in CLOSURE, "Task runtime native module outside closure")
        modules[relative] = o.sha(path)
    return modules


def execute(acceptance, output):
    d.require(
        not output.exists() and not output.is_symlink(), "Task runtime output exists"
    )
    acceptance, output = acceptance.resolve(strict=True), output.absolute()
    d.require(
        not output.resolve().is_relative_to(acceptance),
        "Task runtime output inside parent",
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
        not driver.exists() and not driver.is_symlink(), "Task runtime driver exists"
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
            "Task runtime runtime drift",
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
        "pi-task-runtime",
        env,
        120,
    )
    d.require(
        code == 0 and not stdout and not stderr, "Pinned task runtime driver failed"
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
    d.require(imports == native_modules(), "Task runtime native import drift")
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
            d.require(code == wanted, "Task runtime semantic CLI control failed")
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
    d.require(code == 2, "Task runtime invalid CLI control failed")
    configuration()
    o.verify_sources(snapshot, config["reference_sources"])
    o.acceptance.attest_reference(o, acceptance / "reference", parent, o.FIXTURES)
    v.artifacts.attest(acceptance, o.FIXTURES)
    d.require(
        manifest_sha == o.sha(MANIFEST) and o.sha(driver) == o.sha(DRIVER),
        "Task runtime producer drift",
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
            "scheduler": True,
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
    elif label == "pi-task-runtime":
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
        "Task runtime child identity drift",
    )
    stdout, stderr = (
        (processes / (label + ".stdout")).read_bytes(),
        (processes / (label + ".stderr")).read_bytes(),
    )
    if label in ("node-version", "npm-version"):
        d.require(
            stdout.decode().strip() == o.manifest()["runtime"][label.split("-")[0]]
            and not stderr,
            "Task runtime runtime receipt drift",
        )
    elif label == "invalid-cli":
        d.require(
            not stdout
            and stderr.startswith(b"INVALID:")
            and b"Traceback" not in stderr,
            "Task runtime invalid CLI diagnostics",
        )
    else:
        d.require(not stdout and not stderr, "Task runtime child diagnostics")


def artifact_hashes(folder):
    """Exclude only the root receipt and native stores, never nested names."""
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
        "scheduler": True,
    }
    d.shape(
        value,
        set(expected)
        | {"execution_id", "module_sha256", "native_module_sha256", "artifacts_sha256"},
        "task runtime result",
    )
    for key, wanted in expected.items():
        d.require(d.same(value[key], wanted), "Task runtime result drift: " + key)
    d.require(
        str(uuid.UUID(value["execution_id"])) == value["execution_id"],
        "Task runtime execution ID",
    )
    files = artifact_hashes(folder)
    d.require(d.same(value["artifacts_sha256"], files), "Task runtime artifact drift")
    reference, actual = d.load(folder / "observed.json"), d.load(folder / "native.json")
    d.require(
        compare(reference, actual) == 0
        and d.same(d.load(folder / "input.json"), recipe()),
        "Task runtime actual pair/input drift",
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
        "Task runtime loaded modules drift",
    )
    d.require(
        d.same(value["native_module_sha256"], native_modules()),
        "Task runtime native producer drift",
    )
    results = {
        "node-version": 0,
        "npm-version": 0,
        "pi-task-runtime": 0,
        "invalid-cli": 2,
    }
    for name in MUTANTS:
        mutant = d.load(folder / "mutants" / (name + ".json"))
        d.require(
            d.same(mutant, mutate(actual, name)) and compare(reference, mutant) == 1,
            "Task runtime mutant drift",
        )
        results[name + "-positive"] = 0
        results[name + "-negative"] = 1
    for label, code in results.items():
        process = d.load(folder / "processes" / (label + ".json"))
        process_identity(process, label, folder, snapshot)
        d.require(process["returncode"] == code, "Task runtime child exit drift")
    invalid = copy.deepcopy(actual)
    invalid["unknown"] = True
    retained = d.load(folder / "mutants" / "invalid.json")
    d.require(d.same(retained, invalid), "Task runtime invalid input was not retained")
    try:
        trace(retained)
    except o.EvidenceError:
        pass
    else:
        raise o.EvidenceError("Task runtime invalid producer became valid")
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
