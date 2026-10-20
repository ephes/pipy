"""DE02a1 verification: Pi consumed records versus native owned JSON/DTOs.

Not Python Storage conformance. Requires an original complete fresh acceptance
bundle; replay is never labelled a new bootstrap. Full Memory/Seq/index/race
comparison belongs to DE02a2. Exit0 bounded equality,1 valid mismatch,2 invalid.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import shutil
import struct
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pipy_harness.native.durable.codec import (  # noqa: E402
    OwnedJson,
    decode_value,
    encode_value,
)
from pipy_harness.native.durable.records import (  # noqa: E402
    RECORD_TYPES,
    decode_record,
    encode_record,
)

spec = importlib.util.spec_from_file_location(
    "storage_reference_bootstrap", Path(__file__).with_name("durable_pi_comparison.py")
)
assert spec and spec.loader
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)
MANIFEST = ROOT / "tests/fixtures/durable/storage-records-v1.manifest.json"
INPUT = ROOT / "tests/fixtures/durable/storage-records-v1.input.json"
DRIVER = ROOT / "scripts/durable_reference/cases/storage-records.mts"
DESTINATION = "storage-records.mts"
PHASE = "values-records-a1"
MODULES = ["packages/chord/src/json.ts", "packages/durable/src/storage/memory.ts"]
CLOSURE = (
    {
        "src/pipy_harness/native/durable/" + name + ".py"
        for name in ("__init__", "codec", "records", "storage")
    }
    | {
        "tests/durable/" + name + ".py"
        for name in ("test_json_codec", "test_records", "test_storage_contract")
    }
    | {
        "scripts/durable_storage_comparison.py",
        "scripts/durable_reference/cases/storage-records.mts",
        "tests/fixtures/durable/storage-records-v1.input.json",
    }
)


_artifacts_spec = importlib.util.spec_from_file_location(
    "storage_acceptance_attestation",
    Path(__file__).with_name("durable_reference_artifacts.py"),
)
assert _artifacts_spec and _artifacts_spec.loader
artifacts = importlib.util.module_from_spec(_artifacts_spec)
_artifacts_spec.loader.exec_module(artifacts)


def load(path: Path) -> Any:
    # Verification transport uses finite ASCII hex scalars; product JSON is a
    # separate full finite domain. Deep transport does not use the old recursive
    # TraceSafeNumber walker. This is not a public product byte parser.
    value = json.loads(
        path.read_text(), object_pairs_hook=o._pairs, parse_constant=o._constant
    )
    OwnedJson(value)
    return value


def dump(path: Path, value: Any) -> None:
    OwnedJson(value)
    path.write_text(
        json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        + "\n"
    )


def require(ok: bool, message: str) -> None:
    if not ok:
        raise o.EvidenceError(message)


def unhex(value: Any, *, size: int | None = None) -> bytes:
    require(type(value) is str and len(value) % 2 == 0, "Invalid hex transport")
    require(all(x in "0123456789abcdef" for x in value), "Invalid hex transport")
    result = bytes.fromhex(value)
    require(size is None or len(result) == size, "Invalid scalar width")
    return result


def decode(item: Any) -> Any:
    require(
        type(item) is list and bool(item) and type(item[0]) is str,
        "Invalid typed value",
    )
    kind = item[0]
    require(len(item) == (1 if kind == "null" else 2), "Invalid typed value arity")
    if kind == "null":
        return None
    if kind == "bool":
        require(type(item[1]) is bool, "Invalid boolean transport")
        return item[1]
    if kind == "string":
        return unhex(item[1]).decode("utf-16-be", "surrogatepass")
    if kind == "number":
        value = struct.unpack(">d", unhex(item[1], size=8))[0]
        require(math.isfinite(value), "Nonfinite binary64 transport")
        return (
            int(value) if value.is_integer() and math.copysign(1, value) > 0 else value
        )
    return decode_container(kind, item[1])


def decode_container(kind: str, value: Any) -> Any:
    require(type(value) is list, "Invalid transport container")
    if kind == "array":
        return [decode(child) for child in value]
    require(kind == "object", "Unknown transport tag")
    result = {}
    for pair in value:
        require(type(pair) is list and len(pair) == 2, "Invalid transport member")
        key = unhex(pair[0]).decode("utf-16-be", "surrogatepass")
        require(key not in result, "Duplicate transport member")
        result[key] = decode(pair[1])
    return result


def encode(value: Any) -> list[Any]:
    if value is None:
        return ["null"]
    if type(value) is bool:
        return ["bool", value]
    if type(value) is str:
        return ["string", value.encode("utf-16-be", "surrogatepass").hex()]
    if type(value) in {int, float}:
        return ["number", struct.pack(">d", float(value)).hex()]
    if type(value) is list:
        return ["array", [encode(child) for child in value]]
    require(type(value) is dict, "Cannot encode nonplain comparison value")
    return [
        "object",
        sorted(
            [
                [key.encode("utf-16-be", "surrogatepass").hex(), encode(child)]
                for key, child in value.items()
            ]
        ),
    ]


def fixture(value: Any, *, observed: bool = False) -> dict[str, Any]:
    fields = {"schema_version", "phase", "values", "records"} | (
        {"facts"} if observed else set()
    )
    o.contract.shape(value, fields, "a1 comparison tree")
    require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Unsupported a1 comparison version/phase",
    )
    for item in o.contract.array(value["values"], "a1 values"):
        OwnedJson(decode(item))
    for record in o.contract.array(value["records"], "a1 records"):
        o.contract.shape(record, {"kind", "value"}, "a1 record")
        require(
            type(record["kind"]) is str and record["kind"] in RECORD_TYPES,
            "Unknown native record family",
        )
        RECORD_TYPES[record["kind"]](decode(record["value"]))
    if observed:
        facts = o.contract.shape(
            value["facts"],
            {
                "source_detached",
                "placements_independent",
                "read_detached",
                "omission_distinct",
                "nonfinite_rejected",
                "utf16_key_identity",
            },
            "a1 ownership facts",
        )
        require(
            all(
                type(facts[key]) is bool for key in facts if key != "nonfinite_rejected"
            ),
            "Invalid ownership fact",
        )
        require(
            type(facts["nonfinite_rejected"]) is list
            and len(facts["nonfinite_rejected"]) == 3
            and all(type(x) is bool for x in facts["nonfinite_rejected"]),
            "Invalid rejection facts",
        )
    return value


def native(value: Any) -> dict[str, Any]:
    fixture(value)
    values = [
        encode(decode_value(encode_value(OwnedJson(decode(item)))).thaw())
        for item in value["values"]
    ]
    records = []
    for item in value["records"]:
        record = RECORD_TYPES[item["kind"]](decode(item["value"]))
        records.append(
            {
                "kind": record.kind,
                "value": encode(decode_record(encode_record(record)).to_tree()),
            }
        )
    shared = {"items": [1]}
    owned = OwnedJson({"left": shared, "right": shared})
    shared["items"].append(2)
    copy = o.contract.shape(owned.thaw(), {"left", "right"}, "ownership tree")
    source_detached = copy["left"]["items"] == [1]
    copy["left"]["items"].append(3)
    independent = copy["right"]["items"] == [1]
    entry = next((row for row in records if row["kind"] == "entry"), None)
    if entry is None:
        raise o.EvidenceError("Missing scripted ownership entry")
    record = RECORD_TYPES["entry"](decode(entry["value"]))
    read = o.contract.shape(record.to_tree()["data"], {"nested"}, "scripted entry data")
    read["nested"].append("caller mutation")
    retained = o.contract.shape(
        record.to_tree()["data"], {"nested"}, "retained entry data"
    )
    detached = retained["nested"] == [None]
    from pipy_harness.native.durable.records import OMITTED, EntryRecord

    absent = EntryRecord(
        {"id": 2, "conversationId": 1, "kind": "", "data": OMITTED}
    ).to_tree()
    present = EntryRecord(
        {"id": 2, "conversationId": 1, "kind": "", "data": None}
    ).to_tree()
    try:
        OwnedJson([OMITTED])
    except ValueError:
        rejects_omitted_array = True
    else:
        rejects_omitted_array = False
    rejected = []
    for number in (math.nan, math.inf, -math.inf):
        try:
            OwnedJson(number)
        except ValueError:
            rejected.append(True)
        else:
            rejected.append(False)
    return {
        "schema_version": 1,
        "phase": PHASE,
        "values": values,
        "records": records,
        "facts": {
            "source_detached": source_detached,
            "placements_independent": independent,
            "read_detached": detached,
            "omission_distinct": "data" not in absent
            and present["data"] is None
            and rejects_omitted_array,
            "nonfinite_rejected": rejected,
            "utf16_key_identity": "\U0001f600".encode("utf-16-be", "surrogatepass")
            == "\ud83d\ude00".encode("utf-16-be", "surrogatepass"),
        },
    }


def configuration() -> dict[str, Any]:
    value = load(MANIFEST)
    o.contract.shape(
        value,
        {
            "schema_version",
            "phase",
            "reference_commit",
            "baseline_manifest_sha256",
            "closure",
            "reference_sources",
        },
        "a1 manifest",
    )
    require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE
        and value["reference_commit"] == o.PIN,
        "Unsupported native comparison manifest",
    )
    o.contract.shape(value["closure"], CLOSURE, "a1 closed artifact inventory")
    for path, digest in value["closure"].items():
        o.contract.digest(digest)
        require(o.sha(ROOT / path) == digest, "Native comparison artifact drift")
    require(
        o.sha(o.FIXTURES / "manifest.json") == value["baseline_manifest_sha256"],
        "Baseline manifest drift",
    )
    o.contract.shape(
        value["reference_sources"],
        set(MODULES) | {"packages/durable/src/types.ts"},
        "a1 pinned sources",
    )
    for digest in value["reference_sources"].values():
        o.contract.digest(digest)
    return value


def comparison(expected: Any, actual: Any) -> int:
    fixture(expected, observed=True)
    fixture(actual, observed=True)
    return 0 if expected == actual else 1


def execute(acceptance: Path, output: Path) -> int:
    manifest_sha = o.sha(MANIFEST)
    config = configuration()
    base = o.manifest()
    acceptance = acceptance.resolve(strict=True)
    # This reopens the ORIGINAL archive/snapshot/DBs, never a staged upload.
    artifacts.attest(acceptance, o.FIXTURES)
    run = o.acceptance.attest_reference(o, acceptance / "reference", base, o.FIXTURES)
    parent = o.load(acceptance / "acceptance.json")
    require(
        parent.get("status") == "acceptance-verified"
        and parent.get("complete") is True,
        "Acceptance is not complete",
    )
    parent_sha = o.sha(acceptance / "acceptance.json")
    snapshot = acceptance / "reference/snapshot"
    o.verify_sources(snapshot, config["reference_sources"])
    output = output.resolve()
    require(
        not output.is_relative_to(acceptance),
        "Comparison output must be outside acceptance",
    )
    output.mkdir(mode=0o700, exist_ok=False)
    output.chmod(0o700)
    driver = snapshot / DESTINATION
    require(
        not driver.exists() and not driver.is_symlink(),
        "Comparison adapter destination exists",
    )
    shutil.copyfile(DRIVER, driver)
    driver.chmod(0o600)
    require(
        o.sha(driver) == config["closure"][str(DRIVER.relative_to(ROOT))],
        "Caller artifact copy drift",
    )
    shutil.copyfile(INPUT, output / "input.json")
    env = o.child_env(output)
    for runtime in ("node", "npm"):
        actual = (
            o.run(
                [runtime, "--version"], snapshot, output, runtime + "-version", env, 15
            )
            .decode()
            .strip()
        )
        require(actual == base["runtime"][runtime], "Native comparison runtime drift")
    o.run(
        [
            "node",
            "--import",
            str(snapshot / o.RESOLVER),
            str(driver),
            str(output / "input.json"),
            str(output),
        ],
        snapshot,
        output,
        "pi-value-records",
        env,
        60,
    )
    originals = o.load(acceptance / "reference/source-sha256.json")
    modules = o.verify_resolutions(
        snapshot,
        o.load(output / "resolutions.json"),
        originals,
        {DESTINATION: o.sha(DRIVER)},
        MODULES,
    )
    o.module_dependencies(snapshot, modules, run["installed_dependencies"])
    dependencies = o.load(acceptance / "reference/dependency-sha256.json")
    for path, digest in modules.items():
        if "node_modules" in Path(path).parts:
            require(dependencies.get(path) == digest, "Actual dependency byte drift")
    raw = fixture(load(output / "observed.json"), observed=True)
    product = native(load(INPUT))
    dump(output / "native.json", product)
    code = comparison(raw, product)
    # Also decode/encode the actual Pi-consumed DTOs, preserving every observed field.
    roundtrip = json.loads(json.dumps(raw))
    for row in roundtrip["records"]:
        record = RECORD_TYPES[row["kind"]](decode(row["value"]))
        row["value"] = encode(decode_record(encode_record(record)).to_tree())
    dump(output / "roundtrip.json", roundtrip)
    roundtrip_code = comparison(raw, roundtrip)
    mutants = execute_mutants(output, raw, env)
    o.acceptance.attest_reference(o, acceptance / "reference", base, o.FIXTURES)
    artifacts.attest(acceptance, o.FIXTURES)
    configuration()
    require(
        o.sha(MANIFEST) == manifest_sha
        and o.sha(acceptance / "acceptance.json") == parent_sha,
        "Comparison parent identity drift",
    )
    equal = code == 0 and roundtrip_code == 0
    artifact_names = [
        "input.json",
        "observed.json",
        "native.json",
        "roundtrip.json",
        "resolutions.json",
        "reference-context.json",
        "pi-value-records.json",
    ]
    if not equal:
        dump(
            output / "mismatch-report.json",
            {
                "schema_version": 1,
                "phase": PHASE,
                "status": "mismatch",
                "comparisons": [
                    {
                        "id": identity,
                        "equal": result == 0,
                        "expected": "observed.json",
                        "actual": actual,
                        "expected_sha256": o.sha(output / "observed.json"),
                        "actual_sha256": o.sha(output / actual),
                    }
                    for identity, result, actual in (
                        ("pi-native", code, "native.json"),
                        ("pi-consumed-dto-roundtrip", roundtrip_code, "roundtrip.json"),
                    )
                ],
            },
        )
        artifact_names.append("mismatch-report.json")
    o.dump(
        output / "result.json",
        {
            "schema_version": 1,
            "phase": PHASE,
            "status": "PASS" if equal else "mismatch",
            "execution_id": str(uuid.uuid4()),
            "fresh_reference_bootstrap": False,
            "parent_acceptance_sha256": parent_sha,
            "parent_execution_id": parent["execution_id"],
            "reference_run_sha256": o.sha(acceptance / "reference/run.json"),
            "manifest_sha256": manifest_sha,
            "module_sha256": modules,
            "artifacts_sha256": {name: o.sha(output / name) for name in artifact_names},
            "mutants": mutants,
            "python_storage_implemented": False,
            "parent_acceptance_execution_is_separate": True,
        },
    )
    return 0 if equal else 1


def execute_mutants(
    output: Path, raw: Any, env: dict[str, str]
) -> list[dict[str, Any]]:

    mutants = []
    for name in ("field", "id", "number", "alias"):
        value = json.loads(json.dumps(raw))
        if name in {"field", "id"}:
            entry = next(
                (row for row in value["records"] if row["kind"] == "entry"), None
            )
            if entry is None:
                raise o.EvidenceError("Missing mutable probe entry")
            tree = decode(entry["value"])
            if name == "field":
                del tree["data"]
            else:
                tree["id"] = 100
            entry["value"] = encode(tree)
        elif name == "number":
            value["values"][4] = encode(0.0)  # -0 -> +0 remains schema-valid.
        else:
            value["facts"]["placements_independent"] = False
        path = output / (name + "-mutant.json")
        dump(path, value)
        fixture(value, observed=True)
        for label, actual, wanted in (
            ("positive", output / "observed.json", 0),
            ("negative", path, 1),
        ):
            identity = name + "-" + label
            try:
                o.run(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "compare",
                        str(output / "observed.json"),
                        str(actual),
                    ],
                    ROOT,
                    output,
                    identity,
                    {"PATH": env["PATH"], "HOME": env["HOME"]},
                    30,
                )
            except o.EvidenceError:
                require(wanted == 1, "Positive actual comparison failed")
            observed = o.load(output / (identity + ".json"))["exit_code"]
            require(
                type(observed) is int and observed == wanted,
                "Mutant must be valid mismatch1, never invalid2",
            )
        mutants.append(
            {
                "id": name,
                "schema_valid": True,
                "positive_exit": 0,
                "negative_exit": 1,
                "sha256": o.sha(path),
            }
        )
    return mutants


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    compare = sub.add_parser("compare")
    compare.add_argument("expected", type=Path)
    compare.add_argument("actual", type=Path)
    run = sub.add_parser("run")
    run.add_argument("--acceptance", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "compare":
            return comparison(load(args.expected), load(args.actual))
        return execute(args.acceptance, args.output)
    except (
        o.EvidenceError,
        ValueError,
        OSError,
        KeyError,
        TypeError,
        RecursionError,
    ) as error:
        print(f"Invalid a1 evidence: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
