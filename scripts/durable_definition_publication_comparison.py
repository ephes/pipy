"""Bounded DE14b3 actual generic idle definition publication comparison; compare0/equal,1/different,2/invalid.

Actually created Task records and source-identity callbacks are retained; no physical crash claim.
Native idle/captured-ownership/ACK/notification controls are not a public registry or listener surface.
attest() validates retained bytes and never executes either producer.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import uuid
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
import durable_restart_policy_live_comparison as parent  # noqa:E402
from durable_reference import definition_publication_values as values  # noqa:E402

d, o, v, proc = parent.d, parent.o, parent.v, parent.proc
PHASE = values.PHASE
MANIFEST = o.FIXTURES / (PHASE + ".manifest.json")
INPUT = o.FIXTURES / (PHASE + ".input.json")
DRIVER = ROOT / "scripts/durable_reference/cases/definition_publication.mts"
NATIVE = ROOT / "scripts/durable_reference/definition_publication_native.py"
SOURCES = parent.SOURCES | {
    "packages/durable/test/harness-registry.test.ts",
    "packages/durable/test/harness-tasks-recovery.test.ts",
}
# Required actual runtime roots of THIS generic-task driver, not inherited provider calls.
# All resolutions, including additional dependencies, are still verified and retained.
MODULES = {
    "packages/durable/src/harness/harness.ts",
    "packages/durable/src/harness/scheduler.ts",
    "packages/durable/src/harness/agent.ts",
    "packages/durable/src/harness/registry.ts",
    "packages/durable/src/session/session.ts",
    "packages/durable/src/storage/sqlite/node.ts",
    "packages/durable/src/storage/sqlite/storage.ts",
    "packages/durable/src/tasks.ts",
    "packages/durable/src/documents.ts",
}
CLOSURE = parent.CLOSURE | {
    "scripts/durable_definition_publication_comparison.py",
    "scripts/durable_reference/definition_publication_values.py",
    "scripts/durable_reference/definition_publication_native.py",
    "scripts/durable_reference/cases/definition_publication.mts",
    "tests/durable/definition_publication_reference_support.py",
    "tests/durable/test_definition_publication_comparison.py",
    "tests/fixtures/durable/task-definition-publication-idle-v1.input.json",
    "src/pipy_harness/native/durable/definition_generation.py",
    "src/pipy_harness/native/durable/definition_publication.py",
    "tests/durable/test_definition_publication_core.py",
    "tests/durable/test_definition_publication_live.py",
    "tests/durable/definition_publication_live_support.py",
    "docs/specs/de14b3-definition-publication.md",
    "docs/evidence/de14b3-definition-publication.md",
}

PROFILE = "generic-idle-definition-publication-common-observables-v1"
NUMBER_PROFILE = parent.NUMBER_PROFILE


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
            "comparison_scope",
        },
        "Publication manifest",
    )
    d.require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN,
        "Publication manifest identity",
    )
    d.require(
        config["parents"] == {"restart_policy_live": o.sha(parent.MANIFEST)},
        "Publication parent drift",
    )
    for key, names in (("closure", CLOSURE), ("reference_sources", SOURCES)):
        d.shape(config[key], names, "Publication " + key)
        for name, digest in config[key].items():
            o.contract.digest(digest)
            if key == "closure":
                d.require(
                    o.sha(ROOT / name) == digest, "Publication source drift: " + name
                )
    d.require(
        d.same(config["comparison_scope"], values.SCOPE), "Publication scope drift"
    )
    parent.configuration()
    return config


def recipe():
    value = d.load(INPUT)
    d.require(d.same(value, values.RECIPE), "Publication recipe drift")
    return value


def trace(value):
    values.validate(value, d, v)
    return value


def compare(expected, actual):
    return int(not d.same(trace(expected), trace(actual)))


def projection(rows):
    d.require(
        type(rows) is list and [row["id"] for row in rows] == list(values.IDS),
        "Publication actual case order",
    )
    common = {
        "id",
        "control",
        "created",
        "steps",
        "old_snapshot",
        "final_record",
        "callback_log",
        "callback_rows",
        "callback_sqlite",
        "closed",
        "sqlite_final",
        "mutations",
    }
    step_common = {
        "label",
        "record",
        "before_record",
        "counts",
        "callback_rows",
        "order",
        "lookup",
        "generation",
        "old_snapshot",
        "sql",
    }
    for row in rows:
        native = "native_only" in row
        d.shape(
            row,
            common
            | ({"native_only", "native_origins"} if native else {"reference_only"}),
            "Closed raw publication row",
        )
        d.shape(
            row["control"],
            {"kind", "physical_SIGKILL", "fabricated_task_or_failure_memo"},
            "Actual fixture provenance",
        )
        d.require(
            row["control"]["physical_SIGKILL"] is False
            and row["control"]["fabricated_task_or_failure_memo"] is False,
            "No fabricated authority",
        )
        d.require(
            row["created"]["kind"] == "fixture.definition.main"
            and row["created"]["version"] == 1
            and row["created"]["state"]["status"] == "pending"
            and row["created"]["input"] == {"fixture": row["id"]},
            "Actual created authority",
        )
        for step in row["steps"]:
            d.shape(
                step,
                step_common | ({"native_only"} if native else {"reference_only"}),
                "Closed raw publication step",
            )
            d.shape(
                step["counts"],
                {"initial", "migrate", "handler", "abort"},
                "Actual callback counters",
            )
            d.require(step["counts"]["abort"] == 0, "No abort in common profile")
    return {
        "schema_version": 1,
        "phase": PHASE,
        "cases": [
            {"id": row["id"], "observed": v.encode(values.project(row))} for row in rows
        ],
    }


def sqlite_snapshot(path):
    # Producers have closed these owned databases. Immutable readonly access cannot
    # create a journal, checkpoint or change producer artifacts.
    def value(x):
        return {"sqlite_blob_hex": x.hex()} if type(x) is bytes else x

    with (
        closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as db,
        db,
    ):
        db.execute("PRAGMA query_only=ON")
        result = {}
        for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ):
            cursor = db.execute('SELECT * FROM "' + name.replace('"', '""') + '"')
            rows = [[value(x) for x in r] for r in cursor.fetchall()]
            result[name] = {
                "columns": [r[0] for r in cursor.description],
                "rows": sorted(rows, key=lambda r: json.dumps(r, sort_keys=True)),
            }
        return result


def sqlite_joins(output, reference, native):
    for role, rows in (("pi", reference), ("native", native)):
        for row in rows:
            folder = output / "stores" / role / row["id"]
            counter = sqlite_snapshot(folder / "callback-counter.sqlite")
            d.require(
                d.same(counter, row["callback_sqlite"]),
                "Actual callback SQLite full snapshot",
            )
            calls = counter["calls"]
            d.require(
                calls["columns"] == ["seq", "kind"], "Actual callback SQLite schema"
            )
            actual = [{"seq": seq, "kind": kind} for seq, kind in sorted(calls["rows"])]
            d.require(
                d.same(actual, row["callback_rows"]),
                "Actual callback database/count log join",
            )
            d.require(
                d.same(sqlite_snapshot(folder / "session.sqlite"), row["sqlite_final"]),
                "Actual final SQLite all-table/revision join",
            )


def raw_differences(reference, native):
    # Retain both full trees instead of silently discarding noncommon fields.
    return {"reference_raw": reference, "native_raw": native, "scope": values.SCOPE}


def reference_modules(acceptance, output, config):
    snapshot = acceptance / "reference/snapshot"
    o.verify_sources(snapshot, config["reference_sources"])
    modules = o.verify_resolutions(
        snapshot,
        d.load(output / "resolutions.json"),
        d.load(acceptance / "reference/source-sha256.json"),
        {"definition_publication.mts": o.sha(DRIVER)},
        sorted(MODULES),
    )
    run = o.acceptance.attest_reference(
        o, acceptance / "reference", o.manifest(), o.FIXTURES
    )
    o.module_dependencies(snapshot, modules, run["installed_dependencies"])
    return modules


def native_modules(output):
    actual = d.load(output / "native-modules.json")
    d.require(
        type(actual) is dict and bool(actual), "Publication actual native imports"
    )
    union = {}
    for row in d.load(output / "native-raw.json"):
        d.require(
            type(row["native_origins"]) is dict and row["native_origins"],
            "Publication actual per-case import observation",
        )
        union.update(row["native_origins"])
    d.require(d.same(union, actual), "Publication actual producer module joins")
    for name, digest in actual.items():
        d.require(
            name in CLOSURE and o.sha(ROOT / name) == digest,
            "Publication unlisted/drifted actual import",
        )
    return actual


def artifacts(output):
    result = {}
    for p in output.rglob("*"):
        d.require(not p.is_symlink(), "Publication artifact symlink")
        if p.is_file() and p != output / "result.json":
            result[str(p.relative_to(output))] = o.sha(p)
    return result


def checked_process(output, label, argv, cwd, code):
    record = d.load(output / "processes" / (label + ".json"))
    proc.attest(d, record, output / "processes", label)
    d.require(
        record["argv"] == argv
        and record["cwd"] == str(cwd)
        and record["returncode"] == code,
        "Publication actual process argv/exit",
    )
    d.require(record["signal"] is None, "Publication producer/control timeout")


def producer_attest(output, acceptance):
    d.require(
        d.same(d.load(output / "input.json"), recipe()), "Publication retained recipe"
    )
    snapshot = acceptance / "reference/snapshot"
    checked_process(
        output,
        "pi-definition-publication",
        [
            "node",
            "--import",
            str(snapshot / o.RESOLVER),
            str(snapshot / "definition_publication.mts"),
            str(output / "input.json"),
            str(output),
        ],
        snapshot,
        0,
    )
    checked_process(
        output,
        "native-definition-publication",
        [sys.executable, "-I", str(NATIVE), str(output / "input.json"), str(output)],
        ROOT,
        0,
    )
    reference, native = (
        d.load(output / "raw-stored.json"),
        d.load(output / "native-raw.json"),
    )
    d.require(
        d.same(projection(reference), d.load(output / "observed.json"))
        and d.same(projection(native), d.load(output / "native.json")),
        "Publication raw/projection source join",
    )
    d.require(
        d.same(raw_differences(reference, native), d.load(output / "differences.json")),
        "Publication full raw difference retention",
    )
    sqlite_joins(output, reference, native)
    values.successful(d.load(output / "observed.json"), d, v)
    values.successful(d.load(output / "native.json"), d, v)
    for name in values.MUTANTS:
        path = output / "mutants" / (name + ".json")
        d.require(
            d.same(
                values.mutate(d.load(output / "native.json"), name, v), d.load(path)
            ),
            "Publication retained mutant",
        )
        d.require(
            compare(d.load(output / "observed.json"), d.load(path)) == 1,
            "Publication discriminating semantic mutant",
        )
        for suffix, target, code in (
            ("positive", output / "native.json", 0),
            ("negative", path, 1),
        ):
            checked_process(
                output,
                name + "-" + suffix,
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "compare",
                    str(output / "observed.json"),
                    str(target),
                ],
                ROOT,
                code,
            )
    invalid = d.load(output / "native.json")
    invalid["unknown"] = True
    path = output / "mutants/invalid.json"
    d.require(d.same(invalid, d.load(path)), "Publication malformed body retained")
    checked_process(
        output,
        "invalid",
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "compare",
            str(output / "observed.json"),
            str(path),
        ],
        ROOT,
        2,
    )


def result_base(acceptance):
    return {
        "schema_version": 1,
        "phase": PHASE,
        "status": "PASS",
        "parent_execution_id": d.load(acceptance / "acceptance.json")["execution_id"],
        "parent_acceptance_sha256": o.sha(acceptance / "acceptance.json"),
        "manifest_sha256": o.sha(MANIFEST),
        "comparison_profile": PROFILE,
        "number_profile": NUMBER_PROFILE,
        "cases": len(values.IDS),
        "mutants": list(values.MUTANTS),
        "invalid_exits": [2],
        "fresh_reference_bootstrap": False,
        "control_scope": values.SCOPE["controls"],
        "public_api_parity": False,
        "physical_SIGKILL": False,
    }


def execute(acceptance, output):
    d.require(
        not output.exists() and not output.is_symlink(), "Publication output exists"
    )
    acceptance, output = acceptance.resolve(strict=True), output.absolute()
    d.require(
        not output.resolve().is_relative_to(acceptance),
        "Publication output inside parent",
    )
    config = configuration()
    v.artifacts.attest(acceptance, o.FIXTURES)
    output.mkdir(mode=0o700)
    (output / "processes").mkdir()
    (output / "mutants").mkdir()
    d.dump(output / "input.json", recipe())
    snapshot = acceptance / "reference/snapshot"
    destination = snapshot / "definition_publication.mts"
    d.require(
        not destination.exists() and not destination.is_symlink(),
        "Publication reference driver exists",
    )
    shutil.copyfile(DRIVER, destination)
    env = o.child_env(output)
    for label, argv, cwd in (
        (
            "pi-definition-publication",
            [
                "node",
                "--import",
                str(snapshot / o.RESOLVER),
                str(destination),
                str(output / "input.json"),
                str(output),
            ],
            snapshot,
        ),
        (
            "native-definition-publication",
            [
                sys.executable,
                "-I",
                str(NATIVE),
                str(output / "input.json"),
                str(output),
            ],
            ROOT,
        ),
    ):
        stdout, stderr, code = proc.run(
            d, argv, cwd, output / "processes", label, env, 120
        )
        d.require(
            code == 0 and not stdout and not stderr,
            "Publication actual producer failed: " + label,
        )
    reference, native = (
        d.load(output / "raw-stored.json"),
        d.load(output / "native-raw.json"),
    )
    d.dump(output / "differences.json", raw_differences(reference, native))
    d.dump(output / "observed.json", projection(reference))
    d.dump(output / "native.json", projection(native))
    d.require(
        compare(d.load(output / "observed.json"), d.load(output / "native.json")) == 0,
        "Publication common mismatch",
    )
    for name in values.MUTANTS:
        target = output / "mutants" / (name + ".json")
        d.dump(target, values.mutate(d.load(output / "native.json"), name, v))
        for suffix, path, wanted in (
            ("positive", output / "native.json", 0),
            ("negative", target, 1),
        ):
            stdout, stderr, code = proc.run(
                d,
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "compare",
                    str(output / "observed.json"),
                    str(path),
                ],
                ROOT,
                output / "processes",
                name + "-" + suffix,
                env,
                60,
            )
            d.require(
                code == wanted and not stdout and not stderr,
                "Publication semantic CLI control",
            )
    invalid = d.load(output / "native.json")
    invalid["unknown"] = True
    path = output / "mutants/invalid.json"
    d.dump(path, invalid)
    proc.run(
        d,
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "compare",
            str(output / "observed.json"),
            str(path),
        ],
        ROOT,
        output / "processes",
        "invalid",
        env,
        60,
    )
    producer_attest(output, acceptance)
    configuration()
    result = result_base(acceptance)
    result.update(
        execution_id=str(uuid.uuid4()),
        module_sha256=reference_modules(acceptance, output, config),
        native_module_sha256=native_modules(output),
        artifacts_sha256=artifacts(output),
    )
    d.dump(output / "result.json", result)
    return 0


def attest(output, acceptance):
    config = configuration()
    v.artifacts.attest(acceptance, o.FIXTURES)
    result = d.load(output / "result.json")
    expected = result_base(acceptance)
    d.shape(
        result,
        set(expected)
        | {"execution_id", "module_sha256", "native_module_sha256", "artifacts_sha256"},
        "Publication result",
    )
    for key, value in expected.items():
        d.require(d.same(result[key], value), "Publication result identity")
    d.require(
        str(uuid.UUID(result["execution_id"])) == result["execution_id"],
        "Publication execution UUID",
    )
    d.require(
        d.same(result["artifacts_sha256"], artifacts(output)),
        "Publication closed artifact hashes",
    )
    d.require(
        d.same(result["module_sha256"], reference_modules(acceptance, output, config))
        and d.same(result["native_module_sha256"], native_modules(output)),
        "Publication actual module hashes",
    )
    producer_attest(output, acceptance)
    return {
        "status": "PASS",
        "phase": PHASE,
        "execution_id": result["execution_id"],
        "parent_execution_id": result["parent_execution_id"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--acceptance", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    pair = commands.add_parser("compare")
    pair.add_argument("expected", type=Path)
    pair.add_argument("actual", type=Path)
    args = parser.parse_args()
    try:
        return (
            execute(args.acceptance, args.output)
            if args.command == "run"
            else compare(d.load(args.expected), d.load(args.actual))
        )
    except (
        o.EvidenceError,
        ValueError,
        KeyError,
        TypeError,
        OSError,
        AssertionError,
    ) as error:
        print("INVALID: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
