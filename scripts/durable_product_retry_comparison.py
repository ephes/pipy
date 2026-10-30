"""Bounded public retry admission/progress comparison; exits 0/equal, 1/different, 2/invalid.

Complete lane records, counters, storage revisions and ACK/void differences stay raw.
Canonical parent execution/attestation requires the owner-integrated strict manifest.
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

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
import durable_ordinary_owned_live_comparison as parent  # noqa: E402
from durable_reference import product_retry_values as values  # noqa: E402

d, o, v, proc = parent.d, parent.o, parent.v, parent.proc
PHASE = values.PHASE
MANIFEST = o.FIXTURES / (PHASE + ".manifest.json")
INPUT = o.FIXTURES / (PHASE + ".input.json")
DRIVER = ROOT / "scripts/durable_reference/cases/pi_product_retry_reference.mts"
NATIVE = ROOT / "scripts/durable_reference/product_retry_native.py"
SOURCES = parent.SOURCES
MODULES = parent.MODULES
CLOSURE = parent.CLOSURE | {
    "scripts/durable_product_retry_comparison.py",
    "scripts/durable_reference/product_retry_native.py",
    "scripts/durable_reference/product_retry_values.py",
    "scripts/durable_reference/cases/pi_product_retry_reference.mts",
    "tests/durable/test_product_retry_comparison.py",
    "tests/durable/product_retry_reference_support.py",
    "tests/fixtures/durable/task-product-retry-enable-v1.input.json",
    "src/pipy_harness/native/durable/product_operation.py",
    "tests/durable/test_product_retry_continuation_core.py",
    "tests/durable/test_product_retry_continuation_broad.py",
    "tests/durable/product_retry_continuation_broad_support.py",
    "tests/durable/test_product_retry_queued_authority.py",
    "tests/durable/product_retry_queued_authority_support.py",
    "docs/durable-product-session.md",
    "docs/durable-headless.md",
    "docs/durable-restart-parking.md",
}
PROFILE = values.SCOPE["common"]
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
        "Product-retry manifest",
    )
    d.require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN,
        "Product-retry manifest identity",
    )
    d.require(
        config["parents"] == {"ordinary_owned_live": o.sha(parent.MANIFEST)},
        "Product-retry parent drift",
    )
    for key, names in (("closure", CLOSURE), ("reference_sources", SOURCES)):
        d.shape(config[key], names, "Product-retry " + key)
        for name, digest in config[key].items():
            o.contract.digest(digest)
            if key == "closure":
                d.require(
                    o.sha(ROOT / name) == digest,
                    "Product-retry source drift: " + name,
                )
    d.require(
        d.same(config["comparison_scope"], values.SCOPE),
        "Product-retry scope drift",
    )
    parent.configuration()
    return config


def recipe():
    value = values.checked_recipe(d.load(INPUT))
    d.require(d.same(value, values.RECIPE), "Product-retry recipe drift")
    return value


def trace(value):
    values.validate(value, d, v)
    return value


def compare(expected, actual):
    return int(not d.same(trace(expected), trace(actual)))


def projection(rows):
    d.require(
        type(rows) is list and [row["id"] for row in rows] == list(values.IDS),
        "Actual retry case order",
    )
    for row in rows:
        native = row.get("role") == "native"
        d.shape(
            row,
            {
                "id",
                "role",
                "backend_kind",
                "task_id",
                "records",
                "inspection_before",
                "reset",
                "gate",
                "events",
                "cut",
                "control",
                "callback_rows",
                "callback_sqlite",
                "closed",
                "sqlite_final",
            }
            | ({"native_only", "native_origins"} if native else {"reference_only"}),
            "Closed actual retry row",
        )
        d.require(
            type(row["task_id"]) is int and row["task_id"] > 0, "Actual task ID domain"
        )
        d.shape(
            row["records"],
            {"initial", "before_retry", "at_gate", "after_release"}
            | ({"after_close"} if native else set()),
            "Complete role-specific raw retry records",
        )
        d.require(
            all(type(record) is dict for record in row["records"].values()),
            "Actual complete retry records",
        )
        d.shape(
            row["gate"],
            {"retry_returned", "callback_entered", "released", "result_settled"},
            "Actual held callback gate",
        )
        d.require(
            all(type(x) is bool for x in row["gate"].values()), "Exact gate booleans"
        )
        d.shape(
            row["control"],
            {"typed_counter_seed", "arbitrary_terminal_mutation"},
            "Actual fixture origin",
        )
        d.shape(
            row["reset"],
            {"before", "after", "actual_commit_rows"},
            "Actual reset window",
        )
        d.require(
            type(row["reset"]["actual_commit_rows"]) is list, "Actual reset commit rows"
        )
        if row["id"] == values.IDS[0]:
            d.require(
                row["reset"]["after"] in row["reset"]["actual_commit_rows"],
                "Reset observation joins actual commit",
            )
        ledger = row["callback_rows"]
        d.require(
            type(ledger) is list
            and bool(ledger)
            and [x["seq"] for x in ledger] == list(range(1, len(ledger) + 1)),
            "Actual callback ledger sequence",
        )
        d.require(all(type(x["kind"]) is str for x in ledger), "Actual callback kind")
    result = {
        "schema_version": 1,
        "phase": PHASE,
        "cases": [
            {"id": row["id"], "observed": v.encode(values.project(row))} for row in rows
        ],
    }
    trace(result)
    return result


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
        {"pi_product_retry_reference.mts": o.sha(DRIVER)},
        sorted(MODULES),
    )
    for row in d.load(output / "raw-stored.json"):
        observed = row["reference_only"]["modules"]
        if row["cut"] is not None:
            child = o.verify_resolutions(
                snapshot,
                observed,
                d.load(acceptance / "reference/source-sha256.json"),
                {"pi_product_retry_reference.mts": o.sha(DRIVER)},
                sorted(MODULES),
            )
            modules.update(child)
        else:
            d.require(observed == [], "No fabricated no-op cut actor modules")
    run = o.acceptance.attest_reference(
        o, acceptance / "reference", o.manifest(), o.FIXTURES
    )
    o.module_dependencies(snapshot, modules, run["installed_dependencies"])
    return modules


def native_modules(output):
    actual = d.load(output / "native-modules.json")
    d.require(
        type(actual) is dict and bool(actual),
        "Product-retry actual native imports",
    )
    union = {}
    for row in d.load(output / "native-raw.json"):
        d.require(
            type(row["native_origins"]) is dict and row["native_origins"],
            "Product-retry actual per-case import observation",
        )
        union.update(row["native_origins"])
    d.require(d.same(union, actual), "Product-retry actual producer module joins")
    for name, digest in actual.items():
        d.require(
            name in CLOSURE and o.sha(ROOT / name) == digest,
            "Product-retry unlisted/drifted actual import",
        )
    return actual


def artifacts(output):
    result = {}
    for p in output.rglob("*"):
        d.require(not p.is_symlink(), "Product-retry artifact symlink")
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
        "Product-retry actual process argv/exit",
    )
    d.require(record["signal"] is None, "Product-retry producer/control timeout")


def producer_attest(output, acceptance):
    d.require(
        d.same(d.load(output / "input.json"), recipe()),
        "Product-retry retained recipe",
    )
    snapshot = acceptance / "reference/snapshot"
    checked_process(
        output,
        "pi-product-retry",
        [
            "node",
            "--import",
            str(snapshot / o.RESOLVER),
            str(snapshot / "pi_product_retry_reference.mts"),
            str(output / "input.json"),
            str(output),
        ],
        snapshot,
        0,
    )
    checked_process(
        output,
        "native-product-retry",
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
        "Product-retry raw/projection source join",
    )
    d.require(
        d.same(raw_differences(reference, native), d.load(output / "differences.json")),
        "Product-retry full raw difference retention",
    )
    sqlite_joins(output, reference, native)
    for row in native:
        folder = output / "stores/native" / row["id"]
        d.require(
            d.same(
                sqlite_snapshot(folder / "effects.sqlite"),
                row["native_only"]["effects_sqlite"],
            ),
            "Actual winning-effect database all-table join",
        )
        if row["cut"] is not None:
            actual = d.load(folder / "start-process.json")
            d.require(
                d.same(actual, row["cut"]["actual_process"])
                and actual["returncode"] == -9,
                "Actual native cut returncode and retained process join",
            )
    for row in reference:
        if row["cut"] is not None:
            actual = d.load(output / "processes" / (row["id"] + "-cut.json"))
            d.require(
                d.same(actual, row["cut"])
                and actual["exit_signal"] == "SIGKILL"
                and actual["reaped"] is True,
                "Actual Pi cut process join",
            )
    values.successful(d.load(output / "observed.json"), d, v)
    values.successful(d.load(output / "native.json"), d, v)
    for name in values.MUTANTS:
        path = output / "mutants" / (name + ".json")
        d.require(
            d.same(
                values.mutate(d.load(output / "native.json"), name, v), d.load(path)
            ),
            "Product-retry retained mutant",
        )
        d.require(
            compare(d.load(output / "observed.json"), d.load(path)) == 1,
            "Product-retry discriminating semantic mutant",
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
    d.require(d.same(invalid, d.load(path)), "Product-retry malformed body retained")
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
        "physical_SIGKILL": True,
    }


def execute(acceptance, output):
    d.require(
        not output.exists() and not output.is_symlink(),
        "Product-retry output exists",
    )
    acceptance, output = acceptance.resolve(strict=True), output.absolute()
    d.require(
        not output.resolve().is_relative_to(acceptance),
        "Product-retry output inside parent",
    )
    config = configuration()
    v.artifacts.attest(acceptance, o.FIXTURES)
    output.mkdir(mode=0o700)
    (output / "processes").mkdir()
    (output / "mutants").mkdir()
    d.dump(output / "input.json", recipe())
    snapshot = acceptance / "reference/snapshot"
    destination = snapshot / "pi_product_retry_reference.mts"
    d.require(
        not destination.exists() and not destination.is_symlink(),
        "Product-retry reference driver exists",
    )
    shutil.copyfile(DRIVER, destination)
    env = o.child_env(output)
    for label, argv, cwd in (
        (
            "native-product-retry",
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
        (
            "pi-product-retry",
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
            "Product-retry actual producer failed: " + label,
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
        "Product-retry common mismatch",
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
                "Product-retry semantic CLI control",
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
        "Product-retry result",
    )
    for key, value in expected.items():
        d.require(d.same(result[key], value), "Product-retry result identity")
    d.require(
        str(uuid.UUID(result["execution_id"])) == result["execution_id"],
        "Product-retry execution UUID",
    )
    d.require(
        d.same(result["artifacts_sha256"], artifacts(output)),
        "Product-retry closed artifact hashes",
    )
    d.require(
        d.same(result["module_sha256"], reference_modules(acceptance, output, config))
        and d.same(result["native_module_sha256"], native_modules(output)),
        "Product-retry actual module hashes",
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
