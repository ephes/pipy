"""DE13c1 interrupted continuation: actual pinned Harness versus native SDK/CLI.

Only declared retained-prefix/replay/continuation observations are paired.
Raw cut/ACK, error, counter and graph differences remain retained.
No historical blocked migration or full parity claim. attest() never runs producers.
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
import durable_reporting_comparison as parent  # noqa: E402
from durable_reference import interrupted_native  # noqa: E402
from durable_reference import interrupted_values as values  # noqa: E402

from pipy_harness.native.durable import canonical_codec as codec  # noqa: E402

d, o, v, proc = parent.d, parent.o, parent.v, parent.proc
PHASE = "interrupted-tool-continuation-v1"
MANIFEST = o.FIXTURES / (PHASE + ".manifest.json")
INPUT = o.FIXTURES / (PHASE + ".input.json")
DRIVER = ROOT / "scripts/durable_reference/cases/interrupted.mts"
NATIVE = ROOT / "scripts/durable_reference/interrupted_native.py"
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
    "tests/fixtures/durable/interrupted-tool-continuation-v1.input.json",
    "scripts/durable_reference/cases/interrupted.mts",
    "scripts/durable_interrupted_comparison.py",
    "scripts/durable_reference/interrupted_native.py",
    "scripts/durable_reference/interrupted_values.py",
    "tests/durable/test_interrupted_comparison.py",
    "tests/durable/interrupted_cut_support.py",
    "tests/durable/test_tool_interrupted_sdk.py",
    "tests/durable/interrupted_reader_support.py",
    "tests/durable/test_tool_interrupted_readers.py",
    "tests/durable/test_tool_recovery_catalog.py",
    "tests/durable/test_tool_recovery_historical_holds.py",
    "tests/durable/test_tool_recovery_live_fences.py",
    "src/pipy_harness/native/durable/recovery_catalog.py",
    "src/pipy_harness/native/durable/tool_recovery.py",
    "docs/specs/2026-10-27-de13c1-interrupted-continuation-plan.md",
}
NUMBER_PROFILE = parent.NUMBER_PROFILE

PROFILE = "interrupted-tool-continuation-common-observables-v1"


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
        "Interrupted manifest",
    )
    d.require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN,
        "Interrupted identity",
    )
    d.require(
        config["parents"] == {"retained_reporting": o.sha(parent.MANIFEST)},
        "Interrupted parent drift",
    )
    for key, names in (("closure", CLOSURE), ("reference_sources", SOURCES)):
        d.shape(config[key], names, "Interrupted " + key)
        for name, digest in config[key].items():
            o.contract.digest(digest)
            if key == "closure":
                d.require(o.sha(ROOT / name) == digest, "Interrupted source drift")
    d.require(
        d.same(config["comparison_scope"], values.SCOPE), "Interrupted declared scope"
    )
    parent.configuration()
    return config


def recipe():
    value = d.load(INPUT)
    d.require(d.same(value, values.RECIPE), "Interrupted immutable recipe")
    return value


def trace(raw):
    values.validate(raw, d, v)
    return raw


def compare(expected, actual):
    return int(not d.same(trace(expected), trace(actual)))


def projection(rows, native=False):
    d.require([row["id"] for row in rows] == list(values.IDS), "Interrupted raw cases")
    return {
        "schema_version": 1,
        "phase": PHASE,
        "cases": [
            {
                "id": row["id"],
                "observed": v.encode(
                    values.native(row, codec) if native else values.reference(row)
                ),
            }
            for row in rows
        ],
    }


def successful(value):
    for row in trace(value)["cases"]:
        d.require(
            v.encode(values.expected(row["id"])) == row["observed"],
            "Interrupted lossy or incomplete fixture",
        )


def reference_modules(acceptance, output, config):
    snapshot = acceptance / "reference/snapshot"
    o.verify_sources(snapshot, config["reference_sources"])
    modules = o.verify_resolutions(
        snapshot,
        d.load(output / "resolutions.json"),
        d.load(acceptance / "reference/source-sha256.json"),
        {"interrupted.mts": o.sha(DRIVER)},
        sorted(MODULES),
    )
    run = o.acceptance.attest_reference(
        o, acceptance / "reference", o.manifest(), o.FIXTURES
    )
    o.module_dependencies(snapshot, modules, run["installed_dependencies"])
    return modules


def native_modules(output):
    actual = d.load(output / "native-modules.json")
    d.require(type(actual) is dict and actual, "Interrupted module map")
    for name, digest in actual.items():
        d.require(
            name in CLOSURE and o.sha(ROOT / name) == digest,
            "Interrupted actual unlisted/drifted import",
        )
    observed = {}
    for path in (output / "native-records").rglob("*-process.json"):
        for binding in d.load(path)["events"][-1]["native_origins"].values():
            source = Path(binding["path"])
            d.require(source.is_relative_to(ROOT), "Interrupted child import root")
            observed[str(source.relative_to(ROOT))] = binding["sha256"]
    d.require(d.same(actual, observed), "Interrupted actual child import census")
    parent_modules = d.load(output / "native-parent-modules.json")
    d.require(
        type(parent_modules) is dict and parent_modules,
        "Interrupted controller imports",
    )
    for name, digest in parent_modules.items():
        d.require(
            name in CLOSURE and o.sha(ROOT / name) == digest,
            "Interrupted controller import drift",
        )
    return actual


def checked_process(output, label, argv, cwd, code):
    record = d.load(output / "processes" / (label + ".json"))
    proc.attest(d, record, output / "processes", label)
    d.require(
        record["argv"] == argv
        and record["cwd"] == str(cwd)
        and record["returncode"] == code,
        "Interrupted process binding",
    )
    stdout = (output / "processes" / (label + ".stdout")).read_bytes()
    stderr = (output / "processes" / (label + ".stderr")).read_bytes()
    d.require(
        not stdout and (stderr.startswith(b"INVALID:") if code == 2 else not stderr),
        "Interrupted diagnostics",
    )
    return record


def producer_attest(output, acceptance):
    snapshot = acceptance / "reference/snapshot"
    reference = checked_process(
        output,
        "pi-interrupted",
        [
            "node",
            "--import",
            str(snapshot / o.RESOLVER),
            str(snapshot / "interrupted.mts"),
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
        == [str(snapshot / "interrupted.mts"), str(output / "input.json"), str(output)],
        "Interrupted actual pinned runtime",
    )
    checked_process(
        output,
        "native-interrupted",
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
            "Interrupted actual adapter body/text binding",
        )
    raw = d.load(output / "native-raw.json")
    native_actors(output, raw)
    d.require(
        d.same(projection(raw, True), d.load(output / "native.json"))
        and d.same(
            projection(d.load(output / "raw-stored.json")),
            d.load(output / "observed.json"),
        ),
        "Interrupted actual raw projections",
    )
    d.require(
        d.same(
            values.differences(reference_raw, raw), d.load(output / "differences.json")
        ),
        "Interrupted raw delta binding",
    )
    successful(d.load(output / "native.json"))
    successful(d.load(output / "observed.json"))
    for name in values.MUTANTS:
        path = output / "mutants" / (name + ".json")
        d.require(
            d.same(values.mutate(d.load(output / "native.json"), name, v), d.load(path))
            and compare(d.load(output / "observed.json"), d.load(path)) == 1,
            "Interrupted semantic mutant",
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
    d.require(d.same(wanted, d.load(invalid)), "Interrupted malformed body")
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


def native_actors(output, raw):
    for case in raw:
        records = output / "native-records" / case["id"]
        d.require(
            d.same(case, d.load(records / "raw.json")), "Interrupted raw source join"
        )
        for label, expected_code in (("cut", -9), ("resume", 0), ("reopen", 0)):
            saved = d.load(records / (label + "-process.json"))
            actor = saved["process"]
            d.require(
                actor["returncode"] == expected_code
                and actor["leader_reaped"] is True
                and actor["group_absent"] is True
                and actor["pid"] == actor["group"]
                and type(actor["pid"]) is int
                and actor["pid"] > 0
                and not actor["stderr"]
                and not actor["pending_stdout"]
                and actor["events"] == saved["events"],
                "Interrupted physical child lifecycle",
            )
            folder = output / "stores/native" / case["id"]
            recipe_row = next(x for x in recipe()["cases"] if x["id"] == case["id"])
            action = {"cut": "start", "resume": "cli", "reopen": "cli-terminal"}[label]
            current = recipe_row["current"] if label == "resume" else "same"
            d.require(
                actor["argv"]
                == [
                    sys.executable,
                    "-I",
                    "-c",
                    interrupted_native.PRELUDE + interrupted_native.CODE,
                    str(folder),
                    action,
                    recipe_row["stored"],
                    current,
                    "progress-after" if label == "cut" else "",
                ],
                "Interrupted child argv",
            )
            observed = saved["events"][-1]
            d.require(
                observed["event"] == ("cut" if label == "cut" else "closed")
                and observed["pid"] == actor["pid"],
                "Interrupted child observation",
            )
            if label == "cut":
                d.require(
                    observed["stage"] == "progress-after"
                    and observed["callback_acknowledged"] is False,
                    "Interrupted cut ACK boundary",
                )
            d.require(
                type(observed["native_origins"]) is dict and observed["native_origins"],
                "Interrupted child module evidence",
            )
            for binding in observed["native_origins"].values():
                path = Path(binding["path"])
                d.require(
                    path.is_relative_to(ROOT)
                    and str(path.relative_to(ROOT)) in CLOSURE
                    and o.sha(path) == binding["sha256"],
                    "Interrupted child source",
                )
        d.require(
            case["invocation"]["code"] == 0
            and case["invocation"]["in_process_actual_cli"] is True,
            "Interrupted actual CLI",
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
        "native_execution": "actual-sdk-SIGKILL-then-cold-cli-sqlite-next-request-reopen",
        "reference_boundary": "first-streamSimple-fixture-next-real-openai-adapter-with-fake-fetch-and-fixture-tools",
        "raw_differences": "cut-ACK-error-text-counters-graph-and-policy",
        "unsafe_recovery": "eligible-execute-only-historical-blocked-held",
    }


def execute(acceptance, output):
    d.require(
        not output.exists() and not output.is_symlink(), "Interrupted output exists"
    )
    acceptance, output = acceptance.resolve(strict=True), output.absolute()
    d.require(
        not output.resolve().is_relative_to(acceptance),
        "Interrupted output inside parent",
    )
    config = configuration()
    v.artifacts.attest(acceptance, o.FIXTURES)
    output.mkdir(mode=0o700)
    (output / "processes").mkdir()
    (output / "mutants").mkdir()
    d.dump(output / "input.json", recipe())
    env = o.child_env(output)
    snapshot = acceptance / "reference/snapshot"
    destination = snapshot / "interrupted.mts"
    d.require(
        not destination.exists() and not destination.is_symlink(),
        "Interrupted driver exists",
    )
    shutil.copyfile(DRIVER, destination)
    commands = [
        (
            "pi-interrupted",
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
            "native-interrupted",
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
            "Interrupted actual producer failed: " + label,
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
        "Interrupted common mismatch",
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
                actual == code and not stdout and not stderr,
                "Interrupted control failed",
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
    v.artifacts.attest(acceptance, o.FIXTURES)
    value = d.load(output / "result.json")
    expected = result_base(acceptance)
    d.shape(
        value,
        set(expected)
        | {"execution_id", "module_sha256", "native_module_sha256", "artifacts_sha256"},
        "Interrupted result",
    )
    for key, item in expected.items():
        d.require(d.same(value[key], item), "Interrupted result drift")
    d.require(
        str(uuid.UUID(value["execution_id"])) == value["execution_id"],
        "Interrupted execution id",
    )
    d.require(
        d.same(value["artifacts_sha256"], artifacts(output)),
        "Interrupted artifacts drift",
    )
    d.require(
        d.same(value["module_sha256"], reference_modules(acceptance, output, config))
        and d.same(value["native_module_sha256"], native_modules(output)),
        "Interrupted module drift",
    )
    d.require(
        d.same(recipe(), d.load(output / "input.json")), "Interrupted input drift"
    )
    producer_attest(output, acceptance)
    d.require(
        compare(d.load(output / "observed.json"), d.load(output / "native.json")) == 0,
        "Interrupted retained mismatch",
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
