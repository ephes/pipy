# ruff: noqa: E402
"""DE10 genuine pinned inspect/getTask metadata, not native operator API parity."""

from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.util
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "inspection_completed_parent", ROOT / "scripts/durable_completed_comparison.py"
)
assert spec and spec.loader
canonical = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canonical)
d, b, o, v = canonical.d, canonical.b, canonical.o, canonical.v

policy_spec = importlib.util.spec_from_file_location(
    "inspection_native_policy", ROOT / "scripts/durable_reference/inspection_policy.py"
)
assert policy_spec and policy_spec.loader
policy = importlib.util.module_from_spec(policy_spec)
policy_spec.loader.exec_module(policy)


# Explicit pure roots reproduce lazy execution import scope in a fresh attester.
importlib.import_module("pipy_harness.native.durable.inspection_support")
from pipy_harness.native.durable.generation_transport import owned_call
from pipy_harness.native.durable.inspection import inspect_live, inspect_task
from pipy_harness.native.durable.inspection_values import InspectionSupport
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.records import TaskId
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import StorageWrite
from pipy_harness.native.durable.tasks import define_task

PHASE = "inspection-v1"
MANIFEST = o.FIXTURES / "inspection-v1.manifest.json"
DRIVER = ROOT / "scripts/durable_reference/cases/inspection.mts"
DESTINATION = "inspection.mts"
MODULES = {
    "packages/durable/src/index.ts",
    "packages/durable/src/harness/harness.ts",
    "packages/durable/src/harness/scheduler.ts",
    "packages/durable/src/session/transaction.ts",
    "packages/durable/src/storage/sqlite/node.ts",
    "packages/durable/src/tasks.ts",
}
SOURCES = canonical.SOURCES | {
    "packages/durable/test/harness-inspect.test.ts",
    "packages/durable/src/harness/types.ts",
    "packages/durable/src/harness/harness.ts",
    "packages/durable/src/harness/scheduler.ts",
}
CLOSURE = canonical.CLOSURE | {
    "scripts/durable_inspection_comparison.py",
    "scripts/durable_reference/inspection_policy.py",
    "scripts/durable_reference/cases/inspection.mts",
    "tests/fixtures/durable/inspection-v1.input.json",
    "tests/durable/test_inspection_comparison.py",
    "tests/durable/test_inspection.py",
    "tests/durable/test_inspection_sqlite.py",
    "tests/durable/test_inspection_lifecycle.py",
    "tests/durable/test_inspection_wal.py",
    "tests/durable/test_inspection_compatibility.py",
    "tests/durable/test_resolution.py",
    "tests/durable/test_resolution_faults.py",
    "tests/durable/test_resolution_process.py",
    "tests/durable/test_resolution_races.py",
    "docs/durable-inspection.md",
    "docs/session-storage.md",
}
CASES = ("metadata",)
MUTANTS = (
    "order",
    "version",
    "missing",
    "newer",
    "older",
    "terminal",
    "execution",
    "write",
    "read",
    "repeat",
    "extra",
)
NUMBER_PROFILE = "finite-binary64-live-v1"
COMPARISON_PROFILE = "logical-inspect-metadata-v1"
BACKENDS = {"metadata": ["memory", "sqlite", "reopen"]}
FIELDS = {
    "live",
    "tasks",
    "code_runs_delta",
    "read_count",
    "repeat_equal",
    "extra_live",
}
TASK_FIELDS = {
    "label",
    "version",
    "status",
    "phase",
    "outcome",
    "fit",
    "reason",
    "same_conversation",
    "record_unchanged",
}


def recipe():
    value = d.load(o.FIXTURES / "inspection-v1.input.json")
    d.shape(value, {"schema_version", "phase", "cases"}, "Inspection recipe")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Inspection recipe identity",
    )
    d.require(
        [case["id"] for case in value["cases"]] == list(CASES),
        "Inspection recipe cases",
    )
    for case in value["cases"]:
        d.shape(case, {"id", "tasks"}, "Inspection case")
        d.require(
            [row["label"] for row in case["tasks"]]
            == ["ready", "missing", "newer", "older", "terminal"],
            "Inspection labels",
        )
        for row in case["tasks"]:
            d.shape(
                row,
                {"label", "stored", "current", "terminal"},
                "Inspection task recipe",
            )
            d.require(
                type(row["stored"]) in (int, float)
                and (row["current"] is None or type(row["current"]) in (int, float))
                and type(row["terminal"]) is bool,
                "Inspection versions",
            )
            v.encode(row)
    return value


def common_profile(value):
    return copy.deepcopy(value)


def project(case, raw):
    reports = raw["reports"]
    labels = {id: label for label, id in raw["ids"].items()}
    live = reports[0]["tasks"]
    tasks = []
    for label, row in raw["after"].items():
        shown = raw["task_reports"][label]
        tasks.append(
            {
                "label": label,
                "version": row["version"],
                "status": shown["status"],
                "phase": shown["phase"],
                "outcome": shown["outcome"],
                "fit": None
                if shown["status"] == "terminal"
                else shown["compatibility"] == "supported",
                "reason": shown["reason"],
                "same_conversation": row["conversationId"] == raw["conversation"],
                "record_unchanged": d.same(raw["before"][label], row),
            }
        )
    return {
        "live": [labels[row["task_id"]] for row in live],
        "tasks": tasks,
        "code_runs_delta": raw["codeAfter"] - raw["codeBefore"],
        "read_count": sum(row is not None for row in raw["after"].values()),
        "repeat_equal": d.same(reports[0], reports[1]),
        "extra_live": sum(row["task_id"] not in labels for row in live),
    }


def native_case(case, backend, folder):
    folder.mkdir(parents=True, mode=0o700)
    path = str(folder / "session.sqlite")
    storage = (
        MemoryStorage()
        if backend == "memory"
        else open_sqlite_storage(path, mode="create")
    )
    code = []

    async def poison(*args):
        code.append("callback")
        raise AssertionError("inspection executed task code")

    def initial(value):
        code.append("initial")
        raise AssertionError("inspection initialized task")

    definitions = tuple(
        define_task(
            "fixture." + row["label"],
            row["current"],
            initial,
            {"execute": poison},
            poison,
        )
        for row in case["tasks"]
        if row["current"] is not None
    )
    support = InspectionSupport(definitions)
    rows, ids = [], {}
    for id, item in enumerate(case["tasks"], 2):
        ids[item["label"]] = id
        rows.append(
            {
                "id": id,
                "conversationId": 1,
                "kind": "fixture." + item["label"],
                "version": item["stored"],
                "input": {"private": "synthetic-input-sentinel"},
                "background": False,
                "abortRequested": False,
                "state": {
                    "status": "terminal",
                    "outcome": {
                        "status": "aborted",
                        "reason": "synthetic-private-reason",
                    },
                }
                if item["terminal"]
                else {
                    "status": "pending",
                    "checkpoint": {
                        "phase": "execute",
                        "private": "synthetic-frame-sentinel",
                    },
                },
            }
        )
    storage.commit(
        (
            StorageWrite({"type": "conversation", "value": {"id": 1}}),
            *(StorageWrite({"type": "task", "value": row}) for row in rows),
        )
    )
    if backend == "reopen":
        storage.close()
        storage = open_sqlite_storage(path, mode="open")
    session = Session(storage)
    try:
        before = session.read_on_line(
            lambda: {
                label: session._owner.storage.task(TaskId(id)).to_tree()
                for label, id in ids.items()
            }
        )
        code_before = len(code)
        reports = [
            inspect_live(session, support=support).to_tree(),
            inspect_live(session, support=support).to_tree(),
        ]
        task_reports = {
            label: inspect_task(session, id, support=support).to_tree()
            for label, id in ids.items()
        }
        after = session.read_on_line(
            lambda: {
                label: session._owner.storage.task(TaskId(id)).to_tree()
                for label, id in ids.items()
            }
        )
        raw = {
            "ids": ids,
            "conversation": 1,
            "before": before,
            "after": after,
            "reports": reports,
            "task_reports": task_reports,
            "codeBefore": code_before,
            "codeAfter": len(code),
        }
        retained = folder.parents[2] / "native-records" / backend
        retained.mkdir(parents=True, exist_ok=True, mode=0o700)
        d.dump(retained / (case["id"] + ".json"), raw)
        return {"id": case["id"], "observed": v.encode(project(case, raw))}
    finally:
        session.close()


async def native(folder):
    outputs = []
    for backend in BACKENDS["metadata"]:
        outputs.append(
            {
                "schema_version": 1,
                "phase": PHASE,
                "cases": [
                    await owned_call(
                        lambda case=case: native_case(
                            case, backend, folder / backend / case["id"]
                        )
                    )
                    for case in recipe()["cases"]
                ],
            }
        )
    d.require(
        all(d.same(outputs[0], output) for output in outputs[1:]),
        "Inspection native backend mismatch",
    )
    d.dump(folder.parent / "native-exact.json", outputs[0])
    return common_profile(outputs[0])


def trace(value):
    d.shape(value, {"schema_version", "phase", "cases"}, "Inspection trace")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Inspection trace identity",
    )
    d.require(
        type(value["cases"]) is list
        and [row.get("id") for row in value["cases"] if type(row) is dict]
        == list(CASES),
        "Inspection trace cases",
    )
    for row in value["cases"]:
        d.shape(row, {"id", "observed"}, "Inspection row")
        observed = v.decode(row["observed"])
        d.shape(observed, FIELDS, "Inspection observation")
        d.require(
            type(observed["live"]) is list and type(observed["tasks"]) is list,
            "Inspection lists",
        )
        for task in observed["tasks"]:
            d.shape(task, TASK_FIELDS, "Inspection task")
            d.require(
                type(task["label"]) is str
                and type(task["version"]) in (int, float)
                and type(task["status"]) is str,
                "Inspection task metadata",
            )
            d.require(
                task["fit"] is None or type(task["fit"]) is bool, "Inspection fit"
            )
        d.require(v.encode(observed) == row["observed"], "Inspection number profile")
    return value


def compare(expected, actual):
    trace(expected)
    trace(actual)
    return int(not d.same(expected, actual))


def mutate(actual, name):
    value = copy.deepcopy(actual)
    row = v.decode(value["cases"][0]["observed"])
    task_changes = {
        "version": (0, "version", row["tasks"][0]["version"] + 1),
        "missing": (1, "reason", None),
        "newer": (2, "fit", True),
        "older": (3, "fit", True),
        "terminal": (4, "status", "pending"),
        "write": (0, "record_unchanged", False),
    }
    if name in task_changes:
        index, field, replacement = task_changes[name]
        row["tasks"][index][field] = replacement
    elif name == "order":
        row["live"].reverse()
    else:
        field, replacement = {
            "execution": ("code_runs_delta", row["code_runs_delta"] + 1),
            "read": ("read_count", row["read_count"] - 1),
            "repeat": ("repeat_equal", False),
            "extra": ("extra_live", row["extra_live"] + 1),
        }[name]
        row[field] = replacement
    value["cases"][0]["observed"] = v.encode(row)
    return value


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
        "Inspection manifest",
    )
    d.require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN,
        "Inspection manifest identity",
    )
    for key, names in (("closure", CLOSURE), ("reference_sources", SOURCES)):
        d.shape(config[key], names, "Inspection " + key)
        for name, digest in config[key].items():
            o.contract.digest(digest)
            if key == "closure":
                d.require(o.sha(ROOT / name) == digest, "Inspection closure drift")
    d.require(
        config["parents"] == {"completed": o.sha(canonical.MANIFEST)},
        "Inspection parent drift",
    )
    canonical.configuration()
    return config


def native_modules():
    modules = {}
    for name, module in tuple(sys.modules.items()):
        if name != "pipy_harness" and not name.startswith("pipy_harness."):
            continue
        filename = getattr(module, "__file__", None)
        d.require(filename is not None, "Inspection producer lacks source")
        path = Path(filename).resolve()
        d.require(path.is_relative_to(ROOT), "Inspection producer outside checkout")
        relative = str(path.relative_to(ROOT))
        d.require(relative in CLOSURE, "Inspection loaded producer outside closure")
        modules[relative] = o.sha(path)
    return modules


def policy_check(value):
    return int(not policy.valid(value, canonical.canonical, d))


def policy_controls(output, env):
    raw = policy.produce(output / "stores" / "operator", d)
    d.require(policy_check(raw) == 0, "Native operator relation failed")
    d.dump(output / "native-policy.json", raw)
    (output / "mutants" / "policy").mkdir(mode=0o700)
    for name in policy.MUTANTS:
        path = output / "mutants" / "policy" / (name + ".json")
        d.dump(path, policy.mutate(raw, name))
        for label, source, wanted in (
            ("positive", output / "native-policy.json", 0),
            ("negative", path, 1),
        ):
            stdout, stderr, code = d.process(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "native-check",
                    str(source),
                ],
                ROOT,
                output / "processes",
                "policy-" + name + "-" + label,
                env,
                60,
            )
            d.require(
                code == wanted and not stdout and not stderr,
                "Native operator CLI semantic control failed",
            )
    invalid = copy.deepcopy(raw)
    invalid["unknown"] = True
    d.dump(output / "mutants" / "policy" / "invalid.json", invalid)
    _, _, code = d.process(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "native-check",
            str(output / "mutants" / "policy" / "invalid.json"),
        ],
        ROOT,
        output / "processes",
        "policy-invalid",
        env,
        60,
    )
    d.require(code == 2, "Native operator malformed protocol became semantic")


def policy_attest(folder, snapshot):
    raw = d.load(folder / "native-policy.json")
    d.require(policy_check(raw) == 0, "Native operator actual relations drift")
    results = {"policy-invalid": 2}
    for name in policy.MUTANTS:
        changed = d.load(folder / "mutants" / "policy" / (name + ".json"))
        d.require(
            d.same(changed, policy.mutate(raw, name)) and policy_check(changed) == 1,
            "Native operator retained mutant drift",
        )
        results["policy-" + name + "-positive"] = 0
        results["policy-" + name + "-negative"] = 1
    invalid = copy.deepcopy(raw)
    invalid["unknown"] = True
    retained = d.load(folder / "mutants" / "policy" / "invalid.json")
    d.require(d.same(retained, invalid), "Native operator malformed input not retained")
    try:
        policy.protocol(retained, d)
    except o.EvidenceError:
        pass
    else:
        raise o.EvidenceError("Native operator malformed input became valid")
    for label, code in results.items():
        process = d.load(folder / "processes" / (label + ".json"))
        process_identity(process, label, folder, snapshot)
        d.require(process["returncode"] == code, "Native operator child exit drift")


def execute(acceptance, output):
    d.require(
        not output.exists() and not output.is_symlink(),
        "Inspection output exists",
    )
    acceptance, output = acceptance.resolve(strict=True), output.absolute()
    d.require(
        not output.resolve().is_relative_to(acceptance),
        "Inspection output inside parent",
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
        not driver.exists() and not driver.is_symlink(),
        "Inspection driver exists",
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
            "Inspection runtime drift",
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
        "pi-inspection",
        env,
        120,
    )
    d.require(
        code == 0 and not stdout and not stderr,
        "Pinned inspection driver failed",
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
    actual = asyncio.run(native(output / "stores"))
    policy_controls(output, env)
    imports = native_modules()
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
            d.require(code == wanted, "Inspection semantic CLI control failed")
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
    d.require(code == 2, "Inspection invalid CLI control failed")
    configuration()
    o.verify_sources(snapshot, config["reference_sources"])
    o.acceptance.attest_reference(o, acceptance / "reference", parent, o.FIXTURES)
    v.artifacts.attest(acceptance, o.FIXTURES)
    d.require(
        manifest_sha == o.sha(MANIFEST) and o.sha(driver) == o.sha(DRIVER),
        "Inspection producer drift",
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
            "native_backends": BACKENDS,
            "comparison_profile": COMPARISON_PROFILE,
            "cases": len(CASES),
            "mutants": controls,
            "invalid_exit": 2,
            "fresh_reference_bootstrap": False,
            "scheduler": True,
            "number_profile": NUMBER_PROFILE,
            "native_only_operator": {
                "profile": "native-operator-relations-v1",
                "mutants": list(policy.MUTANTS),
            },
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
    elif label == "pi-inspection":
        wanted = [
            "node",
            "--import",
            str(snapshot / o.RESOLVER),
            str(snapshot / DESTINATION),
            str(folder / "input.json"),
            str(folder),
        ]
        cwd = snapshot
    elif label.startswith("policy-"):
        source = (
            folder / "native-policy.json"
            if label.endswith("-positive")
            else folder / "mutants" / "policy" / "invalid.json"
            if label == "policy-invalid"
            else folder
            / "mutants"
            / "policy"
            / (label.removeprefix("policy-").removesuffix("-negative") + ".json")
        )
        wanted = [
            sys.executable,
            str(Path(__file__).resolve()),
            "native-check",
            str(source),
        ]
        cwd = ROOT
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
        "Inspection child identity drift",
    )
    stdout, stderr = (
        (processes / (label + ".stdout")).read_bytes(),
        (processes / (label + ".stderr")).read_bytes(),
    )
    if label in ("node-version", "npm-version"):
        d.require(
            stdout.decode().strip() == o.manifest()["runtime"][label.split("-")[0]]
            and not stderr,
            "Inspection runtime receipt drift",
        )
    elif label in ("invalid-cli", "policy-invalid"):
        d.require(
            not stdout
            and stderr.startswith(b"INVALID:")
            and b"Traceback" not in stderr,
            "Inspection invalid CLI diagnostics",
        )
    else:
        d.require(not stdout and not stderr, "Inspection child diagnostics")


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
        "native_backends": BACKENDS,
        "comparison_profile": COMPARISON_PROFILE,
        "cases": len(CASES),
        "mutants": list(MUTANTS),
        "invalid_exit": 2,
        "fresh_reference_bootstrap": False,
        "scheduler": True,
        "number_profile": NUMBER_PROFILE,
        "native_only_operator": {
            "profile": "native-operator-relations-v1",
            "mutants": list(policy.MUTANTS),
        },
    }
    d.shape(
        value,
        set(expected)
        | {"execution_id", "module_sha256", "native_module_sha256", "artifacts_sha256"},
        "inspection result",
    )
    for key, wanted in expected.items():
        d.require(d.same(value[key], wanted), "Inspection result drift: " + key)
    d.require(
        str(uuid.UUID(value["execution_id"])) == value["execution_id"],
        "Inspection execution ID",
    )
    files = artifact_hashes(folder)
    d.require(d.same(value["artifacts_sha256"], files), "Inspection artifact drift")
    reference, actual = d.load(folder / "observed.json"), d.load(folder / "native.json")
    d.require(
        compare(reference, actual) == 0
        and d.same(d.load(folder / "input.json"), recipe()),
        "Inspection actual pair/input drift",
    )
    for backend in ("memory", "sqlite", "reopen"):
        for case in recipe()["cases"]:
            raw = d.load(folder / "native-records" / backend / (case["id"] + ".json"))
            d.require(
                d.same(
                    v.encode(project(case, raw)),
                    next(
                        row["observed"]
                        for row in actual["cases"]
                        if row["id"] == case["id"]
                    ),
                ),
                "Inspection actual decoded projection drift",
            )
    exact = trace(d.load(folder / "native-exact.json"))
    d.require(
        d.same(common_profile(exact), actual), "Inspection numeric projection drift"
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
        "Inspection loaded modules drift",
    )
    d.require(
        d.same(value["native_module_sha256"], native_modules()),
        "Inspection native producer drift",
    )
    results = {
        "node-version": 0,
        "npm-version": 0,
        "pi-inspection": 0,
        "invalid-cli": 2,
    }
    for name in MUTANTS:
        mutant = d.load(folder / "mutants" / (name + ".json"))
        d.require(
            d.same(mutant, mutate(actual, name)) and compare(reference, mutant) == 1,
            "Inspection mutant drift",
        )
        results[name + "-positive"] = 0
        results[name + "-negative"] = 1
    for label, code in results.items():
        process = d.load(folder / "processes" / (label + ".json"))
        process_identity(process, label, folder, snapshot)
        d.require(process["returncode"] == code, "Inspection child exit drift")
    invalid = copy.deepcopy(actual)
    invalid["unknown"] = True
    retained = d.load(folder / "mutants" / "invalid.json")
    d.require(d.same(retained, invalid), "Inspection invalid input was not retained")
    try:
        trace(retained)
    except o.EvidenceError:
        pass
    else:
        raise o.EvidenceError("Inspection invalid producer became valid")
    policy_attest(folder, snapshot)
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
    native_check = sub.add_parser("native-check")
    native_check.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "native-check":
            return policy_check(d.load(args.path))
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
