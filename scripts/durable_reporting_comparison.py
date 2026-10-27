"""DE13b retained reporting: actual pinned Harness versus native CLI/SQLite.

Only declared reporting/presence/ordinary-failure observables are paired.
Raw error text, graph form, required native final text and policy deltas remain
retained. No unsafe recovery/full parity claim. attest() never runs producers.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import durable_details_comparison as parent  # noqa: E402
from durable_reference import reporting_native  # noqa: E402
from durable_reference import reporting_values as values  # noqa: E402

from pipy_harness.native.durable import canonical_codec as codec  # noqa: E402

d, o, v, proc = parent.d, parent.o, parent.v, parent.proc
PHASE = "retained-tool-reporting-v1"
MANIFEST = o.FIXTURES / (PHASE + ".manifest.json")
INPUT = o.FIXTURES / (PHASE + ".input.json")
DRIVER = ROOT / "scripts/durable_reference/cases/reporting.mts"
NATIVE = ROOT / "scripts/durable_reference/reporting_native.py"
MODULES = parent.MODULES | {"packages/ai/src/api/openai-completions.ts"}
SOURCES = parent.SOURCES | {
    "packages/agent/src/types.ts",
    "packages/ai/src/types.ts",
    "packages/durable/test/harness-structured-output.test.ts",
    "packages/ai/src/api/openai-completions.ts",
    "packages/ai/src/api/transform-messages.ts",
    "packages/chord/src/output.ts",
    "packages/durable/src/harness/agent.ts",
    "packages/durable/src/harness/tool.ts",
    "packages/durable/src/harness/types.ts",
    "packages/durable/src/truncate.ts",
}
CLOSURE = parent.CLOSURE | {
    "tests/fixtures/durable/retained-tool-reporting-v1.input.json",
    "scripts/durable_reference/cases/reporting.mts",
    "scripts/durable_reporting_comparison.py",
    "scripts/durable_reference/reporting_native.py",
    "tests/durable/test_reporting_comparison.py",
    "scripts/durable_reference/reporting_values.py",
    "docs/durable-tools.md",
    "docs/durable-reference.md",
    "tests/durable/test_tool_fact2_reader_matrix.py",
    "tests/durable/test_tool_progress_checkpoints.py",
    "tests/durable/test_tool_progress_physical.py",
    "tests/durable/test_tool_progress_physical_matrix.py",
    "tests/durable/test_tool_progress_values.py",
    "tests/durable/test_tool_progress_writer.py",
    "tests/durable/test_tool_reporting_continuation.py",
    "tests/durable/test_tool_reporting_cuts.py",
    "tests/durable/test_tool_reporting_failures.py",
    "tests/durable/test_tool_reporting_presence.py",
    "tests/durable/test_tool_reporting_projection.py",
    "tests/durable/test_tool_reporting_reentry.py",
    "tests/durable/test_tool_retained_gate.py",
    "tests/durable/reporting_cut_support.py",
}
NUMBER_PROFILE = parent.parent.NUMBER_PROFILE

PROFILE = "retained-reporting-ordinary-failure-common-observables-v1"


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
        "Reporting manifest",
    )
    d.require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN,
        "Reporting identity",
    )
    d.require(
        config["parents"] == {"normal_details": o.sha(parent.MANIFEST)},
        "Reporting parent drift",
    )
    for key, names in (("closure", CLOSURE), ("reference_sources", SOURCES)):
        d.shape(config[key], names, "Reporting " + key)
        for name, digest in config[key].items():
            o.contract.digest(digest)
            if key == "closure":
                d.require(o.sha(ROOT / name) == digest, "Reporting source drift")
    d.require(
        d.same(config["comparison_scope"], values.SCOPE), "Reporting declared scope"
    )
    parent.configuration()
    return config


def recipe():
    value = d.load(INPUT)
    d.require(d.same(value, values.RECIPE), "Reporting immutable recipe")
    return value


def trace(raw):
    values.validate(raw, d, v)
    return raw


def compare(expected, actual):
    return int(not d.same(trace(expected), trace(actual)))


def projection(rows, native=False):
    d.require([row["id"] for row in rows] == list(values.IDS), "Reporting raw cases")
    return {
        "schema_version": 1,
        "phase": PHASE,
        "cases": [
            {
                "id": row["id"],
                "observed": v.encode(
                    values.native(row["raw"], codec)
                    if native
                    else values.reference(row)
                ),
            }
            for row in rows
        ],
    }


def successful(value):
    for row in trace(value)["cases"]:
        d.require(
            v.encode(values.expected(row["id"])) == row["observed"],
            "Reporting lossy or incomplete fixture",
        )


def reference_modules(acceptance, output, config):
    snapshot = acceptance / "reference/snapshot"
    o.verify_sources(snapshot, config["reference_sources"])
    modules = o.verify_resolutions(
        snapshot,
        d.load(output / "resolutions.json"),
        d.load(acceptance / "reference/source-sha256.json"),
        {"reporting.mts": o.sha(DRIVER)},
        sorted(MODULES),
    )
    run = o.acceptance.attest_reference(
        o, acceptance / "reference", o.manifest(), o.FIXTURES
    )
    o.module_dependencies(snapshot, modules, run["installed_dependencies"])
    return modules


def native_modules(output):
    actual = d.load(output / "native-modules.json")
    d.require(type(actual) is dict and actual, "Reporting module map")
    for name, digest in actual.items():
        d.require(
            name in CLOSURE and o.sha(ROOT / name) == digest,
            "Reporting actual unlisted/drifted import",
        )
    d.require(
        d.same(actual, reporting_native.base.parent.native_modules()),
        "Reporting pure import scope differs",
    )
    return actual


def checked_process(output, label, argv, cwd, code):
    record = d.load(output / "processes" / (label + ".json"))
    proc.attest(d, record, output / "processes", label)
    d.require(
        record["argv"] == argv
        and record["cwd"] == str(cwd)
        and record["returncode"] == code,
        "Reporting process binding",
    )
    stdout = (output / "processes" / (label + ".stdout")).read_bytes()
    stderr = (output / "processes" / (label + ".stderr")).read_bytes()
    d.require(
        not stdout and (stderr.startswith(b"INVALID:") if code == 2 else not stderr),
        "Reporting diagnostics",
    )
    return record


def producer_attest(output, acceptance):
    snapshot = acceptance / "reference/snapshot"
    reference = checked_process(
        output,
        "pi-reporting",
        [
            "node",
            "--import",
            str(snapshot / o.RESOLVER),
            str(snapshot / "reporting.mts"),
            str(output / "input.json"),
            str(output),
        ],
        snapshot,
        0,
    )
    runtime = d.load(output / "runtime.json")
    d.require(
        runtime["pid"] == reference["pid"]
        and runtime["cwd"] == str(snapshot)
        and runtime["version"] == "v26.12.0"
        and runtime["argv"][1:]
        == [str(snapshot / "reporting.mts"), str(output / "input.json"), str(output)],
        "Reporting actual pinned runtime",
    )
    native = checked_process(
        output,
        "native-reporting",
        [sys.executable, "-I", str(NATIVE), str(output / "input.json"), str(output)],
        ROOT,
        0,
    )
    reference_raw = d.load(output / "raw-stored.json")
    for case in reference_raw:
        d.require(
            len(case["wire"]) == 1
            and len(case["wireText"]) == 1
            and d.same(json.loads(case["wireText"][0]), case["wire"][0]),
            "Reporting actual adapter body/text binding",
        )
    raw = d.load(output / "native-raw.json")
    for case in raw:
        records = output / "native-records" / case["id"]
        d.require(
            d.same(case["raw"], d.load(records / "raw.json")),
            "Reporting raw source join",
        )
        for label, key in (("start", "first"), ("resume", "repeated")):
            invocation = d.load(records / (label + "-invocation.json"))
            folder = output / "stores/native" / case["id"]
            common = [
                "--store",
                str(folder / "store.sqlite"),
                "--workspace",
                str(folder),
                "--format",
                "json",
            ]
            argv = [
                "durable",
                label,
                *common,
                *(
                    ["--provider", "openai", "--model", "gpt-test", "--prompt", "input"]
                    if label == "start"
                    else []
                ),
            ]
            d.require(
                invocation
                == {
                    "argv": argv,
                    "code": 0,
                    "producer_pid": native["pid"],
                    "in_process_cli": True,
                },
                "Reporting actual CLI binding",
            )
            d.require(
                d.same(d.load(records / (label + ".stdout")), case["raw"][key])
                and not (records / (label + ".stderr")).read_bytes(),
                "Reporting actual CLI stdout join",
            )
        final = next(
            e for e in reversed(case["raw"]["entries"]) if e["kind"] == "pi.assistant"
        )
        d.require(
            d.same(final["data"]["request"], case["raw"]["next_request"]),
            "Reporting committed next-request join",
        )
    d.require(
        d.same(projection(raw, True), d.load(output / "native.json"))
        and d.same(
            projection(d.load(output / "raw-stored.json")),
            d.load(output / "observed.json"),
        ),
        "Reporting actual raw projections",
    )
    d.require(
        d.same(
            values.differences(reference_raw, raw), d.load(output / "differences.json")
        ),
        "Reporting raw delta binding",
    )
    successful(d.load(output / "native.json"))
    successful(d.load(output / "observed.json"))
    for name in values.MUTANTS:
        path = output / "mutants" / (name + ".json")
        d.require(
            d.same(values.mutate(d.load(output / "native.json"), name, v), d.load(path))
            and compare(d.load(output / "observed.json"), d.load(path)) == 1,
            "Reporting semantic mutant",
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
    invalid = output / "mutants/invalid.json"
    wanted = d.load(output / "native.json")
    wanted["unknown"] = True
    d.require(d.same(wanted, d.load(invalid)), "Reporting malformed body")
    checked_process(
        output,
        "invalid",
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "compare",
            str(output / "observed.json"),
            str(invalid),
        ],
        ROOT,
        2,
    )


def artifacts(output):
    return {
        str(p.relative_to(output)): o.sha(p)
        for p in output.rglob("*")
        if p.is_file()
        and p != output / "result.json"
        and not p.is_relative_to(output / "stores")
    }


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
        "native_execution": "actual-cli-sqlite-next-request-reopen",
        "reference_boundary": "first-streamSimple-fixture-next-real-openai-adapter-with-fake-fetch-and-fixture-tools",
        "raw_differences": "retained-error-text-graph-shape-required-native-final-text-and-policy",
        "unsafe_recovery": "not-exercised-or-authorized",
    }


def execute(acceptance, output):
    d.require(
        not output.exists() and not output.is_symlink(), "Reporting output exists"
    )
    acceptance, output = acceptance.resolve(strict=True), output.absolute()
    d.require(
        not output.resolve().is_relative_to(acceptance),
        "Reporting output inside parent",
    )
    config = configuration()
    parent.parent.v.artifacts.attest(acceptance, o.FIXTURES)
    output.mkdir(mode=0o700)
    (output / "processes").mkdir()
    (output / "mutants").mkdir()
    d.dump(output / "input.json", recipe())
    env = o.child_env(output)
    snapshot = acceptance / "reference/snapshot"
    destination = snapshot / "reporting.mts"
    d.require(
        not destination.exists() and not destination.is_symlink(),
        "Reporting driver exists",
    )
    shutil.copyfile(DRIVER, destination)
    commands = [
        (
            "pi-reporting",
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
            "native-reporting",
            [
                sys.executable,
                "-I",
                str(NATIVE),
                str(output / "input.json"),
                str(output),
            ],
            ROOT,
        ),
    ]
    for label, argv, cwd in commands:
        stdout, stderr, code = proc.run(
            d, argv, cwd, output / "processes", label, env, 120
        )
        d.require(
            code == 0 and not stdout and not stderr,
            "Reporting actual producer failed: " + label,
        )
    d.dump(
        output / "differences.json",
        values.differences(
            d.load(output / "raw-stored.json"), d.load(output / "native-raw.json")
        ),
    )
    d.dump(output / "observed.json", projection(d.load(output / "raw-stored.json")))
    d.dump(output / "native.json", projection(d.load(output / "native-raw.json"), True))
    d.require(
        compare(d.load(output / "observed.json"), d.load(output / "native.json")) == 0,
        "Reporting common mismatch",
    )
    for name in values.MUTANTS:
        target = output / "mutants" / (name + ".json")
        d.dump(target, values.mutate(d.load(output / "native.json"), name, v))
        for suffix, file, code in (
            ("positive", output / "native.json", 0),
            ("negative", target, 1),
        ):
            argv = [
                sys.executable,
                str(Path(__file__).resolve()),
                "compare",
                str(output / "observed.json"),
                str(file),
            ]
            stdout, stderr, actual = proc.run(
                d, argv, ROOT, output / "processes", name + "-" + suffix, env, 60
            )
            d.require(
                actual == code and not stdout and not stderr, "Reporting control failed"
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
    value = result_base(acceptance)
    value.update(
        execution_id=str(uuid.uuid4()),
        module_sha256=reference_modules(acceptance, output, config),
        native_module_sha256=native_modules(output),
        artifacts_sha256=artifacts(output),
    )
    d.dump(output / "result.json", value)
    return 0


def attest(output, acceptance):
    config = configuration()
    parent.parent.v.artifacts.attest(acceptance, o.FIXTURES)
    value = d.load(output / "result.json")
    expected = result_base(acceptance)
    d.shape(
        value,
        set(expected)
        | {"execution_id", "module_sha256", "native_module_sha256", "artifacts_sha256"},
        "Reporting result",
    )
    for key, item in expected.items():
        d.require(d.same(value[key], item), "Reporting result drift")
    d.require(
        str(uuid.UUID(value["execution_id"])) == value["execution_id"],
        "Reporting execution id",
    )
    d.require(
        d.same(value["artifacts_sha256"], artifacts(output)),
        "Reporting artifacts drift",
    )
    d.require(
        d.same(value["module_sha256"], reference_modules(acceptance, output, config))
        and d.same(value["native_module_sha256"], native_modules(output)),
        "Reporting module drift",
    )
    d.require(d.same(recipe(), d.load(output / "input.json")), "Reporting input drift")
    producer_attest(output, acceptance)
    d.require(
        compare(d.load(output / "observed.json"), d.load(output / "native.json")) == 0,
        "Reporting retained mismatch",
    )
    return value


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
    ) as exc:
        print("INVALID: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
