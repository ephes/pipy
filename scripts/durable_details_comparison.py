"""DE13a normal final details: actual pinned Harness versus native CLI/SQLite.

Only the completed normal-value/history subset is paired. Native canonical
versions, finite nested evidence and CLI format are native ABI controls, not Pi
API parity. Effect producers are never called by attest().
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import durable_headless_comparison as parent  # noqa: E402
from durable_reference import details_native  # noqa: E402
from durable_reference import details_values as values  # noqa: E402

from pipy_harness.native.durable import canonical_codec as codec  # noqa: E402

d, o, v, proc = parent.d, parent.o, parent.v, parent.proc
PHASE = "normal-details-v1"
MANIFEST = o.FIXTURES / (PHASE + ".manifest.json")
INPUT = o.FIXTURES / (PHASE + ".input.json")
DRIVER = ROOT / "scripts/durable_reference/cases/details.mts"
NATIVE = ROOT / "scripts/durable_reference/details_native.py"
MODULES = parent.MODULES | {"packages/ai/src/api/openai-completions.ts"}
SOURCES = parent.SOURCES | {
    "packages/agent/src/types.ts",
    "packages/ai/src/types.ts",
    "packages/durable/test/harness-structured-output.test.ts",
    "packages/ai/src/api/openai-completions.ts",
    "packages/ai/src/api/transform-messages.ts",
}
CLOSURE = parent.CLOSURE | {
    "scripts/durable_details_comparison.py",
    "scripts/durable_reference/details_values.py",
    "scripts/durable_reference/details_native.py",
    "scripts/durable_reference/cases/details.mts",
    "tests/fixtures/durable/normal-details-v1.input.json",
    "tests/fixtures/durable/tool-details-legacy-v1.json",
    "tests/fixtures/durable/tool-details-legacy-deep-v1.json",
    "tests/fixtures/durable/tool-details-legacy-strings-v1.json",
    "tests/fixtures/durable/tool-details-abi-inventory.json",
    "tests/test_native_tool_details_presence.py",
    "tests/test_native_tool_details_versions.py",
    "tests/test_native_tool_details_domain.py",
    "tests/test_native_tool_details_admission.py",
    "tests/test_native_export_distribution.py",
    "tests/test_native_export_nested_calls.py",
    "tests/test_native_export_tool_rows.py",
    "tests/test_native_ui_transcript.py",
    "tests/test_native_session_history_render.py",
    "tests/test_native_nested_record.py",
    "tests/test_native_agent_loop.py",
    "tests/test_native_agent_nested_calls.py",
    "tests/durable/test_tool_details_presence.py",
    "tests/durable/test_details_comparison.py",
    "docs/tool-details.md",
}
PROFILE = "normal-final-details-history-and-wire-tool-subset-v1"


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
        "Details manifest",
    )
    d.require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN,
        "Details identity",
    )
    d.require(
        config["parents"] == {"headless": o.sha(parent.MANIFEST)},
        "Details parent drift",
    )
    for key, names in (("closure", CLOSURE), ("reference_sources", SOURCES)):
        d.shape(config[key], names, "Details " + key)
        for name, digest in config[key].items():
            o.contract.digest(digest)
            if key == "closure":
                d.require(o.sha(ROOT / name) == digest, "Details source drift")
    parent.configuration()
    inventory = d.load(o.FIXTURES / "tool-details-abi-inventory.json")
    d.require(
        inventory["base_commit"] == "df5c31373a1cca45812200bf3b3e949244f924f1"
        and len(inventory["original_producers"]) == 112
        and len(inventory["original_consumers"]) == 100,
        "Details frozen producer/consumer inventory",
    )
    paths = {
        row["path"]
        for row in inventory["original_producers"] + inventory["original_consumers"]
    }
    d.require(
        paths == {row["path"] for row in inventory["files"]},
        "Details missing disposition",
    )
    for row in inventory["files"]:
        d.require(
            row["path"] in CLOSURE
            and o.sha(ROOT / row["path"]) == row["current_sha256"]
            and type(row["disposition"]) is str
            and bool(row["disposition"]),
            "Details disposition/source drift",
        )
    return config


def recipe():
    value = d.load(INPUT)
    d.require(
        value
        == {
            "schema_version": 1,
            "phase": PHASE,
            "cases": [
                {"id": "absent"},
                {"id": "null", "details": None},
                {"id": "false", "details": False},
                {"id": "zero", "details": 0},
                {"id": "scalar", "details": "scalar"},
                {"id": "array", "details": [None, False, "s"]},
                {"id": "mapping", "details": {"key": "value"}},
            ],
        },
        "Details immutable recipe",
    )
    return value


def trace(raw):
    d.shape(raw, {"schema_version", "phase", "cases"}, "Details trace")
    d.require(
        type(raw["schema_version"]) is int
        and raw["schema_version"] == 1
        and raw["phase"] == PHASE,
        "Details trace identity",
    )
    d.require(
        type(raw["cases"]) is list
        and [c.get("id") for c in raw["cases"] if type(c) is dict] == list(values.IDS),
        "Details cases",
    )
    for case in raw["cases"]:
        d.shape(case, {"id", "observed"}, "Details case")
        item = v.decode(case["observed"])
        d.shape(item, values.FIELDS, "Details observation")
        d.require(v.encode(item) == case["observed"], "Details numeric profile")
        for key in ("completed", "repeat_unchanged"):
            d.require(type(item[key]) is bool, "Details flag")
        d.require(
            type(item["roles"]) is list
            and all(type(role) is str for role in item["roles"]),
            "Details roles",
        )
        for key in ("result", "next_result", "reopened_result"):
            member = item[key]
            d.require(
                type(member) is dict and member.get("state") in ("absent", "value"),
                "Details presence",
            )
            d.shape(
                member,
                {"state", "value"} if member["state"] == "value" else {"state"},
                "Details member",
            )
        for key in ("provider_calls", "tool_calls", "prepares"):
            d.require(
                type(item[key]) in (int, float) and item[key] >= 0, "Details count"
            )
        d.require(
            type(item["next_wire_tools"]) is list
            and all(type(message) is dict for message in item["next_wire_tools"]),
            "Details next wire tool messages",
        )
    return raw


def compare(expected, actual):
    return int(not d.same(trace(expected), trace(actual)))


def projection(rows, native=False):
    d.require(
        [row["id"] for row in rows] == list(values.IDS), "Details actual raw cases"
    )
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
    """A fixture cannot pass because both implementations silently lost data."""
    for case, offered in zip(trace(value)["cases"], recipe()["cases"], strict=True):
        actual = v.decode(case["observed"])
        expected = (
            {"state": "value", "value": offered["details"]}
            if "details" in offered
            else {"state": "absent"}
        )
        d.require(
            actual["completed"]
            and actual["repeat_unchanged"]
            and actual["roles"] == ["user", "assistant", "toolResult", "assistant"]
            and (actual["provider_calls"], actual["tool_calls"], actual["prepares"])
            == (2, 1, 1)
            and v.encode(actual["next_wire_tools"])
            == v.encode(
                [
                    {
                        "fields": ["content", "role", "tool_call_id"],
                        "role": "tool",
                        "content": "actual:value",
                        "correlated": True,
                    }
                ]
            )
            and all(
                v.encode(actual[field]) == v.encode(expected)
                for field in ("result", "next_result", "reopened_result")
            ),
            "Details incomplete or lossy actual fixture",
        )


def reference_modules(acceptance, output, config):
    snapshot = acceptance / "reference/snapshot"
    o.verify_sources(snapshot, config["reference_sources"])
    modules = o.verify_resolutions(
        snapshot,
        d.load(output / "resolutions.json"),
        d.load(acceptance / "reference/source-sha256.json"),
        {"details.mts": o.sha(DRIVER)},
        sorted(MODULES),
    )
    run = o.acceptance.attest_reference(
        o, acceptance / "reference", o.manifest(), o.FIXTURES
    )
    o.module_dependencies(snapshot, modules, run["installed_dependencies"])
    return modules


def native_modules(output):
    actual = d.load(output / "native-modules.json")
    d.require(type(actual) is dict and actual, "Details module map")
    for name, digest in actual.items():
        d.require(
            name in CLOSURE and o.sha(ROOT / name) == digest,
            "Details actual unlisted/drifted import",
        )
    d.require(
        d.same(actual, details_native.base.parent.native_modules()),
        "Details pure import scope differs",
    )
    return actual


def checked_process(output, label, argv, cwd, code):
    record = d.load(output / "processes" / (label + ".json"))
    proc.attest(d, record, output / "processes", label)
    d.require(
        record["argv"] == argv
        and record["cwd"] == str(cwd)
        and record["returncode"] == code,
        "Details process binding",
    )
    stdout = (output / "processes" / (label + ".stdout")).read_bytes()
    stderr = (output / "processes" / (label + ".stderr")).read_bytes()
    d.require(
        not stdout and (stderr.startswith(b"INVALID:") if code == 2 else not stderr),
        "Details diagnostics",
    )
    return record


def producer_attest(output, acceptance):
    snapshot = acceptance / "reference/snapshot"
    reference = checked_process(
        output,
        "pi-details",
        [
            "node",
            "--import",
            str(snapshot / o.RESOLVER),
            str(snapshot / "details.mts"),
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
        == [str(snapshot / "details.mts"), str(output / "input.json"), str(output)],
        "Details actual pinned runtime",
    )
    native = checked_process(
        output,
        "native-details",
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
            "Details actual adapter body/text binding",
        )
    raw = d.load(output / "native-raw.json")
    for case in raw:
        records = output / "native-records" / case["id"]
        d.require(
            d.same(case["raw"], d.load(records / "raw.json")), "Details raw source join"
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
                "Details actual CLI binding",
            )
            d.require(
                d.same(d.load(records / (label + ".stdout")), case["raw"][key])
                and not (records / (label + ".stderr")).read_bytes(),
                "Details actual CLI stdout join",
            )
        final = next(
            e for e in reversed(case["raw"]["entries"]) if e["kind"] == "pi.assistant"
        )
        d.require(
            d.same(final["data"]["request"], case["raw"]["next_request"]),
            "Details committed next-request join",
        )
    d.require(
        d.same(projection(raw, True), d.load(output / "native.json"))
        and d.same(
            projection(d.load(output / "raw-stored.json")),
            d.load(output / "observed.json"),
        ),
        "Details actual raw projections",
    )
    successful(d.load(output / "native.json"))
    successful(d.load(output / "observed.json"))
    for name in values.MUTANTS:
        path = output / "mutants" / (name + ".json")
        d.require(
            d.same(values.mutate(d.load(output / "native.json"), name, v), d.load(path))
            and compare(d.load(output / "observed.json"), d.load(path)) == 1,
            "Details semantic mutant",
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
    d.require(d.same(wanted, d.load(invalid)), "Details malformed body")
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
        "number_profile": parent.NUMBER_PROFILE,
        "cases": len(values.IDS),
        "mutants": list(values.MUTANTS),
        "invalid_exits": [2],
        "fresh_reference_bootstrap": False,
        "native_execution": "actual-cli-sqlite-next-request-reopen",
        "reference_boundary": "first-streamSimple-fixture-next-real-openai-adapter-with-fake-fetch-and-fixture-tools",
        "nested_evidence": "native-only-finite-ABI-not-common-Pi-policy",
    }


def execute(acceptance, output):
    d.require(not output.exists() and not output.is_symlink(), "Details output exists")
    acceptance, output = acceptance.resolve(strict=True), output.absolute()
    d.require(
        not output.resolve().is_relative_to(acceptance), "Details output inside parent"
    )
    config = configuration()
    parent.parent.v.artifacts.attest(acceptance, o.FIXTURES)
    output.mkdir(mode=0o700)
    (output / "processes").mkdir()
    (output / "mutants").mkdir()
    d.dump(output / "input.json", recipe())
    env = o.child_env(output)
    snapshot = acceptance / "reference/snapshot"
    destination = snapshot / "details.mts"
    d.require(
        not destination.exists() and not destination.is_symlink(),
        "Details driver exists",
    )
    shutil.copyfile(DRIVER, destination)
    commands = [
        (
            "pi-details",
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
            "native-details",
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
            "Details actual producer failed: " + label,
        )
    d.dump(output / "observed.json", projection(d.load(output / "raw-stored.json")))
    d.dump(output / "native.json", projection(d.load(output / "native-raw.json"), True))
    d.require(
        compare(d.load(output / "observed.json"), d.load(output / "native.json")) == 0,
        "Details common mismatch",
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
                actual == code and not stdout and not stderr, "Details control failed"
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
        "Details result",
    )
    for key, item in expected.items():
        d.require(d.same(value[key], item), "Details result drift")
    d.require(
        str(uuid.UUID(value["execution_id"])) == value["execution_id"],
        "Details execution id",
    )
    d.require(
        d.same(value["artifacts_sha256"], artifacts(output)), "Details artifacts drift"
    )
    d.require(
        d.same(value["module_sha256"], reference_modules(acceptance, output, config))
        and d.same(value["native_module_sha256"], native_modules(output)),
        "Details module drift",
    )
    d.require(d.same(recipe(), d.load(output / "input.json")), "Details input drift")
    producer_attest(output, acceptance)
    d.require(
        compare(d.load(output / "observed.json"), d.load(output / "native.json")) == 0,
        "Details retained mismatch",
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
