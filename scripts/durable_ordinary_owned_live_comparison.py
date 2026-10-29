"""Bounded DE15a ordinary-owned-live held completion comparison; compare0/equal,1/different,2/invalid.

Actual Task graph creation, raw lane asymmetries and backend revisions are retained; no native execution claim.
Candidate/global graph/lifecycle controls remain native-only; no dispatch/completing/public Product claim.
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
import durable_orphan_missing_migration_comparison as parent  # noqa:E402
from durable_reference import ordinary_owned_live_values as values  # noqa:E402

d, o, v, proc = parent.d, parent.o, parent.v, parent.proc
PHASE = values.PHASE
MANIFEST = o.FIXTURES / (PHASE + ".manifest.json")
INPUT = o.FIXTURES / (PHASE + ".input.json")
DRIVER = ROOT / "scripts/durable_reference/cases/pi_ordinary_owned_live_reference.mts"
NATIVE = ROOT / "scripts/durable_reference/ordinary_owned_live_native.py"
SOURCES = parent.SOURCES | {
    "packages/durable/src/harness/live.ts",
    "packages/durable/src/session/transaction.ts",
    "packages/durable/test/harness-tasks-recovery.test.ts",
    "packages/durable/test/harness-blocked.test.ts",
    "packages/durable/test/harness-ownership.test.ts",
    "packages/durable/src/harness/task-graph.ts",
    "packages/durable/test/task-support.ts",
}
MODULES = parent.MODULES | {
    "packages/durable/src/harness/live.ts",
    "packages/durable/src/session/transaction.ts",
    "packages/durable/src/storage/memory.ts",
    "packages/durable/src/harness/task-graph.ts",
}
CLOSURE = parent.CLOSURE | {
    "tests/durable/ordinary_owned_live_reference_support.py",
    "docs/specs/de15a-ordinary-owned-live-tests.md",
    "scripts/durable_reference/cases/pi_ordinary_owned_live_reference.mts",
    "tests/durable/test_ordinary_owned_live_comparison.py",
    "tests/durable/test_ordinary_owned_live.py",
    "tests/durable/ordinary_owned_live_support.py",
    "src/pipy_harness/native/durable/ordinary_ownership.py",
    "scripts/durable_reference/ordinary_owned_live_native.py",
    "tests/durable/test_ordinary_owned_live_core.py",
    "scripts/durable_reference/ordinary_owned_live_values.py",
    "docs/evidence/de15a-ordinary-owned-live.md",
    "tests/fixtures/durable/task-ordinary-owned-live-v1.input.json",
    "docs/specs/de15a-ordinary-owned-live.md",
    "scripts/durable_ordinary_owned_live_comparison.py",
}


PROFILE = "ordinary-owned-live-held-completion-common-v1"
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
        "Ordinary-owned-live manifest",
    )
    d.require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN,
        "Ordinary-owned-live manifest identity",
    )
    d.require(
        config["parents"] == {"orphan_missing_migration": o.sha(parent.MANIFEST)},
        "Ordinary-owned-live parent drift",
    )
    for key, names in (("closure", CLOSURE), ("reference_sources", SOURCES)):
        d.shape(config[key], names, "Ordinary-owned-live " + key)
        for name, digest in config[key].items():
            o.contract.digest(digest)
            if key == "closure":
                d.require(
                    o.sha(ROOT / name) == digest,
                    "Ordinary-owned-live source drift: " + name,
                )
    d.require(
        d.same(config["comparison_scope"], values.SCOPE),
        "Ordinary-owned-live scope drift",
    )
    parent.configuration()
    return config


def recipe():
    value = values.checked_recipe(d.load(INPUT))
    d.require(d.same(value, values.RECIPE), "Ordinary-owned-live recipe drift")
    return value


def trace(value):
    values.validate(value, d, v)
    return value


def compare(expected, actual):
    return int(not d.same(trace(expected), trace(actual)))


def projection(rows):
    d.require(
        type(rows) is list and [r["id"] for r in rows] == list(values.IDS),
        "Actual ownership case order",
    )
    for row, case in zip(rows, values.CASES, strict=True):
        native = row.get("role") == "native"
        d.shape(
            row,
            {
                "id",
                "role",
                "backend_kind",
                "control",
                "topology",
                "steps",
                "commit_batches",
                "minted",
                "callback_log",
                "callback_rows",
                "callback_sqlite",
                "closed",
                "sqlite_final",
                "final_backend",
                "mutations",
            }
            | ({"native_only", "native_origins"} if native else {"reference_only"}),
            "Closed actual ownership row",
        )
        d.require(
            row["role"] in ("native", "pi") and row["backend_kind"] == case["backend"],
            "Actual ownership lane/backend",
        )
        d.require(
            row["control"]
            == {
                "actual_Task_Tx_creation": True,
                "native_direct_Tx_terminalization": native,
                "physical_SIGKILL": False,
            },
            "Actual graph fixture origin",
        )
        d.require(
            type(row["closed"]) is list
            and len(row["closed"]) == (2 if case["cold"] else 1)
            and all(x == "closed" for x in row["closed"]),
            "Actual owner closure",
        )
        if native:
            d.shape(
                row["native_only"],
                {"execution", "terminalization", "lifecycle"},
                "Closed native ownership scope",
            )
            d.require(
                row["native_only"]["execution"] == "DISABLED_NO_SCHEDULER"
                and row["native_only"]["terminalization"]
                == "ACTUAL_DIRECT_TX_NOT_OWNED_EXECUTION",
                "Native direct Tx asymmetry",
            )
            d.require(
                row["callback_log"]
                == ["initial:" + name for name in ("P", "B", "A", "Z", "D")],
                "Actual native creation callbacks only",
            )
        else:
            d.shape(
                row["reference_only"],
                {
                    "held_frame",
                    "watch_initial",
                    "frames",
                    "commit_acks",
                    "release_outcome",
                    "lifecycle",
                    "reports",
                    "private_owned_live",
                    "actual_Harness_hook",
                    "observation",
                },
                "Closed public Pi ownership scope",
            )
            side = row["reference_only"]
            d.require(
                side["held_frame"] in side["frames"]
                and side["watch_initial"]["tasks"][str(row["topology"]["P"])]["state"][
                    "status"
                ]
                in ("pending", "running"),
                "Actual public watch acquisition and delivered frame",
            )
        _raw_steps(row)
    result = {
        "schema_version": 1,
        "phase": PHASE,
        "cases": [
            {"id": r["id"], "observed": v.encode(values.project(r))} for r in rows
        ],
    }
    trace(result)
    return result


def _raw_steps(row):
    ledger = row["callback_rows"]
    d.require(
        type(ledger) is list
        and [x["seq"] for x in ledger] == list(range(1, len(ledger) + 1)),
        "Actual callback SQLite sequence",
    )
    d.require(
        [x["kind"] for x in ledger] == row["callback_log"],
        "Actual callback ledger/log join",
    )
    for step in row["steps"]:
        d.shape(
            step,
            {
                "label",
                "snapshot",
                "callbacks",
                "batch_count",
                "mint_count",
                "backend",
                "query",
            },
            "Closed ownership raw boundary",
        )
        d.shape(
            step["snapshot"],
            {"tasks", "conversations", "documents"},
            "Complete actual graph/doc snapshot",
        )
        d.require(
            all(type(step["snapshot"][k]) is list for k in step["snapshot"]),
            "Actual graph lists",
        )
        d.require(
            type(step["batch_count"]) is int and type(step["mint_count"]) is int,
            "Actual graph tape positions",
        )
        d.require(
            step["callbacks"] == ledger[: len(step["callbacks"])],
            "Actual callback boundary prefix",
        )
        if row["role"] == "native":
            d.shape(
                step["query"],
                {"answer", "before", "after", "silent"},
                "Actual native query boundary",
            )
            d.require(
                type(step["query"]["answer"]) is bool
                and type(step["query"]["silent"]) is bool,
                "Actual query exact booleans",
            )
        else:
            d.require(step["query"] is None, "No fabricated Pi query result")


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
            if row["backend_kind"] == "sqlite":
                d.require(
                    d.same(
                        sqlite_snapshot(folder / "session.sqlite"), row["sqlite_final"]
                    ),
                    "Actual own final SQLite all-table/revision join",
                )
            else:
                d.require(
                    row["sqlite_final"] is None
                    and not (folder / "session.sqlite").exists(),
                    "Memory current-only evidence, no invented SQLite/history",
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
        {"pi_ordinary_owned_live_reference.mts": o.sha(DRIVER)},
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
        type(actual) is dict and bool(actual),
        "Ordinary-owned-live actual native imports",
    )
    union = {}
    for row in d.load(output / "native-raw.json"):
        d.require(
            type(row["native_origins"]) is dict and row["native_origins"],
            "Ordinary-owned-live actual per-case import observation",
        )
        union.update(row["native_origins"])
    d.require(d.same(union, actual), "Ordinary-owned-live actual producer module joins")
    for name, digest in actual.items():
        d.require(
            name in CLOSURE and o.sha(ROOT / name) == digest,
            "Ordinary-owned-live unlisted/drifted actual import",
        )
    return actual


def artifacts(output):
    result = {}
    for p in output.rglob("*"):
        d.require(not p.is_symlink(), "Ordinary-owned-live artifact symlink")
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
        "Ordinary-owned-live actual process argv/exit",
    )
    d.require(record["signal"] is None, "Ordinary-owned-live producer/control timeout")


def producer_attest(output, acceptance):
    d.require(
        d.same(d.load(output / "input.json"), recipe()),
        "Ordinary-owned-live retained recipe",
    )
    snapshot = acceptance / "reference/snapshot"
    checked_process(
        output,
        "pi-ordinary-owned-live",
        [
            "node",
            "--import",
            str(snapshot / o.RESOLVER),
            str(snapshot / "pi_ordinary_owned_live_reference.mts"),
            str(output / "input.json"),
            str(output),
        ],
        snapshot,
        0,
    )
    checked_process(
        output,
        "native-ordinary-owned-live",
        [
            sys.executable,
            "-B",
            "-I",
            str(NATIVE),
            str(output / "input.json"),
            str(output),
        ],
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
        "Ordinary-owned-live raw/projection source join",
    )
    d.require(
        d.same(raw_differences(reference, native), d.load(output / "differences.json")),
        "Ordinary-owned-live full raw difference retention",
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
            "Ordinary-owned-live retained mutant",
        )
        d.require(
            compare(d.load(output / "observed.json"), d.load(path)) == 1,
            "Ordinary-owned-live discriminating semantic mutant",
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
    d.require(
        d.same(invalid, d.load(path)), "Ordinary-owned-live malformed body retained"
    )
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
        not output.exists() and not output.is_symlink(),
        "Ordinary-owned-live output exists",
    )
    acceptance, output = acceptance.resolve(strict=True), output.absolute()
    d.require(
        not output.resolve().is_relative_to(acceptance),
        "Ordinary-owned-live output inside parent",
    )
    config = configuration()
    v.artifacts.attest(acceptance, o.FIXTURES)
    output.mkdir(mode=0o700)
    (output / "processes").mkdir()
    (output / "mutants").mkdir()
    d.dump(output / "input.json", recipe())
    snapshot = acceptance / "reference/snapshot"
    destination = snapshot / "pi_ordinary_owned_live_reference.mts"
    d.require(
        not destination.exists() and not destination.is_symlink(),
        "Ordinary-owned-live reference driver exists",
    )
    shutil.copyfile(DRIVER, destination)
    env = o.child_env(output)
    for label, argv, cwd in (
        (
            "pi-ordinary-owned-live",
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
            "native-ordinary-owned-live",
            [
                sys.executable,
                "-B",
                "-I",
                str(NATIVE),
                str(output / "input.json"),
                str(output),
            ],
            ROOT,
        ),
    ):
        stdout, stderr, code = proc.run(
            d,
            argv,
            cwd,
            output / "processes",
            label,
            env,
            300 if label.startswith("pi-") else 120,
        )
        d.require(
            code == 0 and not stdout and not stderr,
            "Ordinary-owned-live actual producer failed: " + label,
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
        "Ordinary-owned-live common mismatch",
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
                "Ordinary-owned-live semantic CLI control",
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
        "Ordinary-owned-live result",
    )
    for key, value in expected.items():
        d.require(d.same(result[key], value), "Ordinary-owned-live result identity")
    d.require(
        str(uuid.UUID(result["execution_id"])) == result["execution_id"],
        "Ordinary-owned-live execution UUID",
    )
    d.require(
        d.same(result["artifacts_sha256"], artifacts(output)),
        "Ordinary-owned-live closed artifact hashes",
    )
    d.require(
        d.same(result["module_sha256"], reference_modules(acceptance, output, config))
        and d.same(result["native_module_sha256"], native_modules(output)),
        "Ordinary-owned-live actual module hashes",
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
