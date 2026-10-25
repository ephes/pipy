"""DE12 actual native CLI versus pinned Harness completed/P16 common facts.

Pi has no durable CLI export. Product exits/signals/operator policy and protected
prepared-target refusal are native evidence, not a Pi CLI/API equivalence claim.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import durable_product_comparison as parent  # noqa: E402


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


values = load("headless_values", "scripts/durable_reference/headless_values.py")
profiles = load("headless_profiles", "scripts/durable_reference/headless_profiles.py")
witness = load("headless_attest", "scripts/durable_reference/headless_attest.py")
d, o, v, proc = parent.d, parent.o, parent.v, parent.proc

# Pure lazy roots reached by actual CLI construction, never a provider call.
importlib.import_module("pipy_harness.cli")
importlib.import_module("pipy_harness.durable_cli")
importlib.import_module("pipy_harness.native.repl.turn_leaves")
importlib.import_module("pipy_harness.native.catalog_data")
importlib.import_module("pipy_harness.native.ds4")

PHASE = "headless-cli-v1"
MANIFEST = o.FIXTURES / (PHASE + ".manifest.json")
INPUT = o.FIXTURES / (PHASE + ".input.json")
NATIVE = ROOT / "scripts/durable_reference/headless_native.py"
DRIVERS = {
    name: ROOT / "scripts/durable_reference/cases" / name
    for name in ("headless.mts", "headless-profile.mts")
}
MODULES, SOURCES = parent.MODULES, parent.SOURCES
CLOSURE = parent.CLOSURE | {
    "scripts/durable_headless_comparison.py",
    "scripts/durable_reference/headless_native.py",
    "scripts/durable_reference/headless_values.py",
    "scripts/durable_reference/headless_profiles.py",
    "scripts/durable_reference/headless_attest.py",
    "scripts/durable_reference/cases/headless.mts",
    "scripts/durable_reference/cases/headless-profile.mts",
    "tests/fixtures/durable/headless-cli-v1.input.json",
    "tests/durable/cli_process_support.py",
    "tests/durable/test_cli_boundary.py",
    "tests/durable/test_cli_lifecycle.py",
    "tests/durable/test_cli_output.py",
    "tests/durable/test_cli_output_lifecycle.py",
    "tests/durable/test_cli_process.py",
    "tests/durable/test_cli_profiles.py",
    "tests/durable/test_cli_profile_recovery.py",
    "tests/durable/test_cli_paths.py",
    "tests/durable/test_cli_shared_io.py",
    "tests/durable/test_cli_io_ownership.py",
    "tests/durable/test_cli_documentation.py",
    "tests/durable/test_headless_comparison.py",
    "tests/test_cli_top_level_routing.py",
    "tests/test_native_session_extension_generation.py",
    "docs/durable-headless.md",
    "docs/usage.md",
}
CASES = ("plain", "tools")
COMPARISON_PROFILE = "cli-harness-committed-message-usage-subset-v1"
NUMBER_PROFILE = parent.NUMBER_PROFILE


def recipe():
    result = d.load(INPUT)
    d.require(
        result
        == {
            "schema_version": 1,
            "phase": PHASE,
            "cases": [
                {"id": "plain", "names": []},
                {"id": "tools", "names": ["keyed", "later"]},
            ],
            "profiles": [
                {"id": "unprepared-current", "cut": "prepare", "stream": {}},
                {"id": "prepared-current", "cut": "request", "stream": {}},
            ],
        },
        "Headless immutable recipe",
    )
    return result


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
        "Headless manifest",
    )
    d.require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN,
        "Headless manifest identity",
    )
    d.require(
        config["parents"] == {"product": o.sha(parent.MANIFEST)},
        "Headless parent drift",
    )
    for field, names in (("closure", CLOSURE), ("reference_sources", SOURCES)):
        d.shape(config[field], names, "Headless " + field)
        for name, digest in config[field].items():
            o.contract.digest(digest)
            if field == "closure":
                d.require(o.sha(ROOT / name) == digest, "Headless source drift")
    parent.configuration()
    return config


def trace(value):
    d.shape(value, {"schema_version", "phase", "cases"}, "Headless trace")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Headless trace identity",
    )
    d.require(
        type(value["cases"]) is list
        and [c.get("id") for c in value["cases"] if type(c) is dict] == list(CASES),
        "Headless trace cases",
    )
    for case in value["cases"]:
        d.shape(case, {"id", "observed"}, "Headless case")
        observed = v.decode(case["observed"])
        d.shape(observed, values.FIELDS, "Headless common fields")
        d.require(
            v.encode(observed) == case["observed"], "Headless tagged value profile"
        )
        observation_types(observed)
    return value


def observation_types(value):
    for name in ("completed", "reconnect", "repeat_unchanged", "usage_once"):
        d.require(type(value[name]) is bool, "Headless flag type")
    d.require(type(value["user_text"]) is str, "Headless input type")
    for name in ("roles", "result_names", "result_text"):
        d.require(
            type(value[name]) is list and all(type(v) is str for v in value[name]),
            "Headless ordered strings",
        )
    for name in ("provider_calls", "tool_calls", "applications", "prepares"):
        d.require(
            type(value[name]) in (int, float) and value[name] >= 0,
            "Headless count type",
        )
    d.require(type(value["usage"]) is dict, "Headless usage type")
    usage_types(value["usage"])


def usage_types(usage):
    required = {"input", "output", "cacheRead", "cacheWrite", "totalTokens", "cost"}
    d.require(
        required <= usage.keys() <= required | {"reasoning", "cacheWrite1h"},
        "Headless usage fields",
    )
    d.require(
        all(
            type(number) in (int, float) and number >= 0
            for name, number in usage.items()
            if name != "cost"
        ),
        "Headless usage numbers",
    )
    d.shape(
        usage["cost"],
        {"input", "output", "cacheRead", "cacheWrite", "total"},
        "Headless usage cost",
    )
    d.require(
        all(
            type(number) in (int, float) and number >= 0
            for number in usage["cost"].values()
        ),
        "Headless cost numbers",
    )


def compare(expected, actual):
    return int(not d.same(trace(expected), trace(actual)))


def native_profile_records(output):
    return [
        {
            "id": name,
            "capture": d.load(
                output / "native-records/profiles" / name / "capture.json"
            ),
            "resumed": d.load(
                output / "native-records/profiles" / name / "resume.json"
            ),
        }
        for name in profiles.IDS
    ]


def profile_protocol(value):
    d.shape(
        value, {"schema_version", "profile", "reference", "native"}, "P16 policy input"
    )
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["profile"] == profiles.POLICY,
        "P16 policy profile",
    )
    for key in ("reference", "native"):
        d.require(
            type(value[key]) is list
            and [c.get("id") for c in value[key] if type(c) is dict]
            == list(profiles.IDS),
            "P16 policy cases",
        )
    return value


def profile_check(value):
    value = profile_protocol(value)
    try:
        return int(
            not profiles.policy_valid(value["reference"], value["native"], parent)
        )
    except o.EvidenceError:
        # A well-shaped measured record violating an identity/retention relation
        # is a semantic mismatch, not a malformed comparison protocol.
        return 1


def native(output, env):
    command = [
        sys.executable,
        "-I",
        str(NATIVE),
        "common",
        str(output / "input.json"),
        str(output),
    ]
    stdout, stderr, code = proc.run(
        d, command, ROOT, output / "processes", "native-common", env, 120
    )
    d.require(code == 0 and not stdout and not stderr, "Native CLI producer failed")
    for name in profiles.IDS:
        for mode, wanted in (("profile-capture", -9), ("profile-resume", 0)):
            command = [
                sys.executable,
                "-I",
                str(NATIVE),
                mode,
                str(output / "input.json"),
                str(output),
                "--case",
                name,
            ]
            stdout, stderr, code = proc.run(
                d,
                command,
                ROOT,
                output / "processes",
                "native-" + name + "-" + mode,
                env,
                60,
            )
            d.require(
                code == wanted and not stdout and not stderr,
                "Native P16 producer failed",
            )
    raw = d.load(output / "native-common-raw.json")
    return {
        "schema_version": 1,
        "phase": PHASE,
        "cases": [
            {"id": c["id"], "observed": v.encode(values.project(c["raw"], parent))}
            for c in raw
        ],
    }


def reference(acceptance, output, config, env):
    snapshot = acceptance / "reference/snapshot"
    for name, source in DRIVERS.items():
        destination = snapshot / name
        d.require(
            not destination.exists() and not destination.is_symlink(),
            "Headless driver exists",
        )
        shutil.copyfile(source, destination)
    command = [
        "node",
        "--import",
        str(snapshot / o.RESOLVER),
        str(snapshot / "headless.mts"),
        str(output / "input.json"),
        str(output),
        sys.executable,
        str(parent.READER),
    ]
    stdout, stderr, code = proc.run(
        d, command, snapshot, output / "processes", "pi-headless", env, 120
    )
    d.require(
        code == 0 and not stdout and not stderr, "Pinned headless producer failed"
    )
    return reference_modules(acceptance, output, config)


def reference_modules(acceptance, output, config):
    snapshot = acceptance / "reference/snapshot"
    resolutions = d.load(output / "resolutions.json")
    for case in d.load(output / "profile-reference.json"):
        resolutions += case["capture"]["resolutions"] + case["resumed"]["resolutions"]
    o.verify_sources(snapshot, config["reference_sources"])
    modules = o.verify_resolutions(
        snapshot,
        resolutions,
        d.load(acceptance / "reference/source-sha256.json"),
        {n: o.sha(f) for n, f in DRIVERS.items()},
        sorted(MODULES),
    )
    run = o.acceptance.attest_reference(
        o, acceptance / "reference", o.manifest(), o.FIXTURES
    )
    o.module_dependencies(snapshot, modules, run["installed_dependencies"])
    return modules


def native_modules(output):
    paths = [output / "native-common-modules.json"]
    paths += [
        output / "native-records/profiles" / name / (mode + "-modules.json")
        for name in profiles.IDS
        for mode in ("capture", "resume")
    ]
    actual = {}
    for path in paths:
        modules = d.load(path)
        d.require(
            type(modules) is dict and modules, "Headless native module map absent"
        )
        for name, digest in modules.items():
            d.require(
                name in CLOSURE and o.sha(ROOT / name) == digest,
                "Headless loaded unlisted/drifted native module",
            )
            d.require(
                name not in actual or actual[name] == digest,
                "Headless conflicting loaded source",
            )
            actual[name] = digest
    d.require(
        d.same(actual, parent.native_modules()),
        "Headless fresh pure import scope differs",
    )
    return actual


def control(output, mode, label, expected, actual, wanted, env):
    argv = [sys.executable, str(Path(__file__).resolve()), mode, str(expected)]
    if actual is not None:
        argv.append(str(actual))
    stdout, stderr, code = proc.run(d, argv, ROOT, output / "processes", label, env, 60)
    d.require(
        code == wanted and not stdout and (not stderr if wanted != 2 else bool(stderr)),
        "Headless actual CLI control failed",
    )


def controls(output, actual, policy, env):
    for name in values.MUTANTS:
        target = output / "mutants" / (name + ".json")
        d.dump(target, values.mutate(actual, name, v))
        control(
            output,
            "compare",
            name + "-positive",
            output / "observed.json",
            output / "native.json",
            0,
            env,
        )
        control(
            output,
            "compare",
            name + "-negative",
            output / "observed.json",
            target,
            1,
            env,
        )
    (output / "mutants/profiles").mkdir(mode=0o700)
    for name in profiles.MUTANTS:
        target = output / "mutants/profiles" / (name + ".json")
        changed = copy.deepcopy(policy)
        changed["native"] = profiles.mutate(changed["native"], name)
        d.dump(target, changed)
        control(
            output,
            "profile-check",
            "profile-" + name + "-positive",
            output / "profile-policy.json",
            None,
            0,
            env,
        )
        control(
            output,
            "profile-check",
            "profile-" + name + "-negative",
            target,
            None,
            1,
            env,
        )
    for mode, source in (
        ("compare", "native.json"),
        ("profile-check", "profile-policy.json"),
    ):
        malformed = d.load(output / source)
        malformed["unknown"] = True
        target = output / "mutants" / (mode + "-invalid.json")
        d.dump(target, malformed)
        control(
            output,
            mode,
            mode + "-invalid",
            output / "observed.json" if mode == "compare" else target,
            target if mode == "compare" else None,
            2,
            env,
        )


def artifact_hashes(folder):
    return {
        str(p.relative_to(folder)): o.sha(p)
        for p in folder.rglob("*")
        if p.is_file()
        and p != folder / "result.json"
        and not p.is_relative_to(folder / "stores")
    }


def producer_attest(folder, acceptance):
    module = sys.modules[__name__] if __name__ in sys.modules else None
    # Importlib callers need no global module registration: pass the explicit
    # finite namespace, never execute the effect-producing entry point.
    if module is None:
        from types import SimpleNamespace

        module = SimpleNamespace(**globals())
    witness.native(module, folder)
    witness.reference(module, folder, acceptance / "reference/snapshot")
    witness.controls(module, folder)
    wanted = {
        "schema_version": 1,
        "profile": profiles.POLICY,
        "reference": d.load(folder / "profile-reference.json"),
        "native": native_profile_records(folder),
    }
    d.require(
        d.same(wanted, d.load(folder / "profile-policy.json")),
        "Headless policy source join",
    )


def execute(acceptance, output):
    d.require(not output.exists() and not output.is_symlink(), "Headless output exists")
    acceptance, output = acceptance.resolve(strict=True), output.absolute()
    d.require(
        not output.resolve().is_relative_to(acceptance), "Headless output inside parent"
    )
    config = configuration()
    parent.v.artifacts.attest(acceptance, o.FIXTURES)
    output.mkdir(mode=0o700)
    (output / "processes").mkdir(mode=0o700)
    (output / "mutants").mkdir(mode=0o700)
    d.dump(output / "input.json", recipe())
    env = o.child_env(output)
    modules = reference(acceptance, output, config, env)
    actual = native(output, env)
    d.dump(output / "native.json", actual)
    policy = {
        "schema_version": 1,
        "profile": profiles.POLICY,
        "reference": d.load(output / "profile-reference.json"),
        "native": native_profile_records(output),
    }
    d.dump(output / "profile-policy.json", policy)
    d.require(
        compare(d.load(output / "observed.json"), actual) == 0
        and profile_check(policy) == 0,
        "Headless common/profile relation mismatch",
    )
    controls(output, actual, policy, env)
    producer_attest(output, acceptance)
    configuration()
    value = result_base(acceptance)
    value.update(
        execution_id=str(uuid.uuid4()),
        module_sha256=modules,
        native_module_sha256=native_modules(output),
        artifacts_sha256=artifact_hashes(output),
    )
    d.dump(output / "result.json", value)
    return 0


def result_base(acceptance):
    return {
        "schema_version": 1,
        "phase": PHASE,
        "status": "PASS",
        "parent_execution_id": d.load(acceptance / "acceptance.json")["execution_id"],
        "parent_acceptance_sha256": o.sha(acceptance / "acceptance.json"),
        "manifest_sha256": o.sha(MANIFEST),
        "comparison_profile": COMPARISON_PROFILE,
        "number_profile": NUMBER_PROFILE,
        "cases": len(CASES),
        "mutants": list(values.MUTANTS),
        "profile_mutants": list(profiles.MUTANTS),
        "invalid_exits": [2, 2],
        "profile_policy": profiles.POLICY,
        "fresh_reference_bootstrap": False,
        "scheduler": True,
        "native_execution": "actual-in-process-cli-over-sqlite-plus-real-P16-SIGKILL-reopen",
    }


def attest(folder, acceptance):
    """Pure verification only; actual effect producers are never rerun here."""
    config = configuration()
    parent.v.artifacts.attest(acceptance, o.FIXTURES)
    result = d.load(folder / "result.json")
    expected = result_base(acceptance)
    d.shape(
        result,
        set(expected)
        | {"execution_id", "module_sha256", "native_module_sha256", "artifacts_sha256"},
        "Headless result",
    )
    for key, value in expected.items():
        d.require(d.same(value, result[key]), "Headless result field drift")
    d.require(
        str(uuid.UUID(result["execution_id"])) == result["execution_id"],
        "Headless execution ID",
    )
    d.require(
        d.same(result["artifacts_sha256"], artifact_hashes(folder)),
        "Headless artifacts drift",
    )
    d.require(
        d.same(result["module_sha256"], reference_modules(acceptance, folder, config)),
        "Headless reference module drift",
    )
    d.require(
        d.same(result["native_module_sha256"], native_modules(folder)),
        "Headless native module drift",
    )
    d.require(
        compare(d.load(folder / "observed.json"), d.load(folder / "native.json")) == 0
        and profile_check(d.load(folder / "profile-policy.json")) == 0,
        "Headless retained relation mismatch",
    )
    d.require(
        d.same(recipe(), d.load(folder / "input.json")),
        "Headless immutable input drift",
    )
    producer_attest(folder, acceptance)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--acceptance", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    compare_parser = commands.add_parser("compare")
    compare_parser.add_argument("expected", type=Path)
    compare_parser.add_argument("actual", type=Path)
    check = commands.add_parser("profile-check")
    check.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "run":
            return execute(args.acceptance, args.output)
        if args.command == "profile-check":
            return profile_check(d.load(args.path))
        return compare(d.load(args.expected), d.load(args.actual))
    except (
        o.EvidenceError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        RecursionError,
    ):
        print("INVALID: incompatible headless comparison evidence", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
