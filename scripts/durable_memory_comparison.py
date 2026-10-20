"""DE02a2a actual internal table comparison. run/compare:0 equal,1 mismatch,2 invalid.

Exported Node conformance is separate from seven paired scripted scenarios.
No document capability, Session, product factory or full Storage parity claim.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "a2_value_transport", Path(__file__).with_name("durable_storage_comparison.py")
)
assert spec and spec.loader
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)
o = v.o
from pipy_harness.native.durable.codec import OwnedJson  # noqa: E402
from pipy_harness.native.durable.memory import MemoryStorage  # noqa: E402
from pipy_harness.native.durable.records import (  # noqa: E402
    RECORD_TYPES,
    EntryRecord,
    Record,
    safe_integer,
)
from pipy_harness.native.durable.storage import (  # noqa: E402
    ConversationQuery,
    Cursor,
    EntryLookup,
    EntryQuery,
    Page,
    StorageRequestError,
    StorageWrite,
    SubmissionQuery,
    TaskQuery,
)

PHASE = "memory-tables-a2a-v1"
INPUT = ROOT / "tests/fixtures/durable/storage-memory-v1.input.json"
MANIFEST = ROOT / "tests/fixtures/durable/storage-memory-v1.manifest.json"
DRIVER = ROOT / "scripts/durable_reference/cases/storage-memory.mts"
DESTINATION = "storage-memory.mts"
MODULES = [
    "packages/durable/src/storage/memory.ts",
    "packages/durable/src/storage/scan.ts",
    "packages/durable/src/testing/storage-conformance.ts",
]
SOURCES = set(MODULES) | {
    "packages/durable/src/types.ts",
    "packages/durable/src/testing/types.ts",
}
CLOSURE = v.CLOSURE | {
    "src/pipy_harness/native/durable/memory.py",
    "scripts/durable_memory_comparison.py",
    "scripts/durable_reference/cases/storage-memory.mts",
    "tests/fixtures/durable/storage-memory-v1.input.json",
    "tests/durable/test_memory_storage.py",
    "tests/durable/test_memory_comparison.py",
}
EXPORTED = (
    "reserves ID 1 for the immutable root conversation",
    "commits mixed table writes atomically and rolls all of them back on failure",
    "detaches retained writes and every returned record",
    "detaches prototype-like JSON keys without changing object prototypes",
    "indexes entries committed out of ID order",
    "continues an entry cursor below its last item after a newer commit",
    "paginates conversations by opaque cursor in ascending ID order",
    "filters and pages conversations by durable owner edges",
    "scans deep fork history newest-first through every ancestor cap",
    "scans tables in either ID order and continues a cursor in its order",
    "replaces complete task records and pages filtered task scans",
    "stores owners and scans waiting and completing tasks by status",
    "stores and scans sleeping tasks by status",
    "indexes request IDs per conversation and replaces complete submission records",
    "stores passive write submissions without input-only lifecycle states",
    "keeps one global record ID namespace and rejects exhausted ID minting",
    "rejects every operation after close",
)
SCENARIOS = (
    "identity",
    "replacement",
    "ancestry",
    "cursor",
    "utf16",
    "ownership",
    "exhaustion",
)
OPERATIONS = {
    "mint",
    "commit",
    "conversation",
    "task",
    "submission",
    "request",
    "entry",
    "head",
    "scan_conversations",
    "scan_entries",
    "scan_tasks",
    "scan_submissions",
    "ownership",
    "close",
}
MUTANTS = ("field", "id", "seq", "request-index", "status-index", "cursor", "alias")
require = v.require
load = v.load
dump = v.dump


def shape(value: Any, fields: set[str], label: str) -> Any:
    return o.contract.shape(value, fields, label)


def recipe(value: Any) -> Any:
    shape(
        value,
        {"schema_version", "phase", "exported_cases", "scenarios"},
        "table recipe",
    )
    require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Unsupported table recipe",
    )
    require(value["exported_cases"] == list(EXPORTED), "Exported case identity drift")
    scenarios = o.contract.array(value["scenarios"], "scripted scenarios")
    require(
        [s.get("id") if type(s) is dict else None for s in scenarios]
        == list(SCENARIOS),
        "Scripted scenario identity drift",
    )
    for scenario in scenarios:
        shape(scenario, {"id", "operations"}, "scripted scenario")
        ids = []
        for step in o.contract.array(scenario["operations"], "operations"):
            shape(step, {"id", "op", "args"}, "scripted operation")
            require(
                type(step["id"]) is str
                and bool(step["id"])
                and type(step["op"]) is str
                and step["op"] in OPERATIONS,
                "Invalid scripted operation",
            )
            ids.append(step["id"])
            _recipe_args(step["op"], v.decode(step["args"]))
        require(
            bool(ids) and len(ids) == len(set(ids)), "Duplicate/missing operation IDs"
        )
    return value


def _cursor(value: Any) -> Any:
    # JS Number transport -> exact Python integer ONLY in the signed cursor role.
    # Payload numbers remain untouched (including signed zero and int/float type).
    require(
        type(value) is dict and set(value) in ({"after"}, {"after", "order"}),
        "Malformed cursor",
    )
    after = value["after"]
    require(
        type(after) in {int, float} and after == int(after),
        "Nonintegral cursor position",
    )
    result = {**value, "after": int(after)}
    safe_integer(result["after"], positive=False)
    return result


def _recipe_args(op: str, a: Any) -> None:
    required = (
        {"writes"}
        if op == "commit"
        else {"id"}
        if op in {"conversation", "entry", "task", "submission", "ownership"}
        else {"conversation_id", "request_id"}
        if op == "request"
        else {"conversation_id"}
        if op == "head"
        else {"query", "limit"}
        if op.startswith("scan_")
        else set()
    )
    optional = (
        {"conversation_id"}
        if op == "entry"
        else {"bound"}
        if op == "head"
        else {"cursor", "cursor_ref", "python_pair_fields"}
        if op.startswith("scan_")
        else {"python_pair_fields"}
        if op == "request"
        else set()
    )
    require(
        type(a) is dict and required <= a.keys() and a.keys() <= required | optional,
        "Malformed operation arguments",
    )
    OwnedJson(a)
    for name in ("id", "conversation_id", "bound"):
        if name in a:
            safe_integer(a[name])
    if op == "commit":
        for write in o.contract.array(a["writes"], "scripted writes"):
            StorageWrite(write)
    if op == "request":
        require(type(a["request_id"]) is str, "Malformed request key")
    if op.startswith("scan_"):
        require(type(a["limit"]) is int, "Malformed scan size")
        query = {
            "scan_conversations": ConversationQuery,
            "scan_entries": EntryQuery,
            "scan_tasks": TaskQuery,
            "scan_submissions": SubmissionQuery,
        }[op]
        query(a["query"])
    if "cursor" in a:
        require(
            type(a["cursor"]) is dict
            and set(a["cursor"]) in ({"after"}, {"after", "order"}),
            "Malformed recipe cursor",
        )
        Cursor(**_cursor(a["cursor"]))
    if "cursor_ref" in a:
        require(
            type(a["cursor_ref"]) is str
            and bool(a["cursor_ref"])
            and "cursor" not in a,
            "Malformed cursor reference",
        )
    for path in o.contract.array(
        a.get("python_pair_fields", []), "Python spelling fields"
    ):
        require(
            path in [["query", "kind"], ["request_id"]],
            "Unknown Python spelling boundary",
        )


def _dto(value: Any) -> Any:
    if isinstance(value, Record):
        return value.to_tree()
    if type(value) is EntryLookup:
        return {"entry": value.entry.to_tree(), "commitSeq": value.commit_seq}
    if type(value) is Page:
        result: dict[str, Any] = {"items": [item.to_tree() for item in value.items]}
        if value.next is not None:
            result["next"] = {
                "after": value.next.after,
                **({"order": value.next.order} if value.next.order is not None else {}),
            }
        return result
    return value


def arguments(step: Any, cache: dict[str, Any], spellings: list[Any]) -> Any:
    a = v.decode(step["args"])
    for path in a.pop("python_pair_fields", []):
        require(
            path in [["query", "kind"], ["request_id"]],
            "Unknown Python spelling boundary",
        )
        target = a["query"] if len(path) == 2 else a
        key = path[-1]
        text = target[key]
        require(type(text) is str, "Expected string spelling")
        units = text.encode("utf-16-be", "surrogatepass")
        target[key] = "".join(
            chr(int.from_bytes(units[i : i + 2], "big"))
            for i in range(0, len(units), 2)
        )
        spellings.append(
            {
                "operation": step["id"],
                "path": path,
                "python_codepoints": [ord(c) for c in target[key]],
            }
        )
    if "cursor_ref" in a:
        ref = a.pop("cursor_ref")
        require(
            type(ref) is str
            and ref in cache
            and type(cache[ref]) is dict
            and "next" in cache[ref],
            "Missing actual cursor reference",
        )
        a["cursor"] = cache[ref]["next"]
    return a


def _ownership(store: MemoryStorage, a: Any) -> Any:
    shared = {"items": [None, -0.0, 2**60, "\ud800"]}
    value = {
        "id": a["id"],
        "conversationId": 1,
        "kind": "",
        "data": {"left": shared, "right": shared},
    }
    writes = [{"type": "entry", "value": value}]
    before = v.encode(writes)
    seq = store.commit(tuple(StorageWrite(w) for w in writes))
    shared["items"].append("caller")
    lookup = store.entry(a["id"])
    assert lookup is not None
    first: Any = lookup.entry.to_tree()
    source_detached = len(first["data"]["left"]["items"]) == 4
    first["data"]["left"]["items"].append("read")
    placements_independent = len(first["data"]["right"]["items"]) == 4
    reread = store.entry(a["id"])
    assert reread is not None
    again: Any = reread.entry.to_tree()
    return {
        "calls": [
            {"op": "commit", "args": before, "result": v.encode(seq)},
            {"op": "entry", "id": a["id"], "mutated": v.encode(first)},
            {"op": "entry", "id": a["id"], "result": v.encode(again)},
        ],
        "source_detached": source_detached,
        "placements_independent": placements_independent,
        "read_detached": len(again["data"]["left"]["items"]) == 4,
    }


def invoke(store: MemoryStorage, op: str, a: Any) -> Any:
    if op == "mint":
        return store.mint_id()
    if op == "commit":
        return store.commit(tuple(StorageWrite(w) for w in a["writes"]))
    if op in {"conversation", "task", "submission"}:
        return getattr(store, op)(a["id"])
    if op == "request":
        return store.submission_by_request(a["conversation_id"], a["request_id"])
    if op == "entry":
        return store.entry(
            a["id"],
            **(
                {"conversation_id": a["conversation_id"]}
                if "conversation_id" in a
                else {}
            ),
        )
    if op == "head":
        return store.find_latest_head_marker(a["conversation_id"], a.get("bound"))
    if op == "ownership":
        return _ownership(store, a)
    if op == "close":
        return store.close()
    query = {
        "scan_conversations": ConversationQuery,
        "scan_entries": EntryQuery,
        "scan_tasks": TaskQuery,
        "scan_submissions": SubmissionQuery,
    }.get(op)
    if query is None:
        raise o.EvidenceError("Unknown table operation")
    cursor = Cursor(**_cursor(a["cursor"])) if "cursor" in a else None
    return getattr(store, op)(query(a["query"]), a["limit"], cursor)


def native(value: Any) -> tuple[Any, Any]:
    recipe(value)
    scenarios = []
    spellings: list[Any] = []
    for scenario in value["scenarios"]:
        store = MemoryStorage()
        operations = []
        cache: dict[str, Any] = {}
        try:
            for step in scenario["operations"]:
                a = arguments(step, cache, spellings)
                record = {"id": step["id"], "op": step["op"], "args": v.encode(a)}
                try:
                    result = _dto(invoke(store, step["op"], a))
                    cache[step["id"]] = result
                    record["outcome"] = {
                        "status": "fulfilled",
                        "value": ["undefined"] if result is None else v.encode(result),
                    }
                except (RuntimeError, StorageRequestError) as error:
                    record["outcome"] = {
                        "status": "rejected",
                        "error": {"name": type(error).__name__, "message": str(error)},
                    }
                operations.append(record)
        finally:
            store.close()
        scenarios.append({"id": scenario["id"], "operations": operations})
    return {
        "schema_version": 1,
        "phase": PHASE,
        "backend": "python",
        "scenarios": scenarios,
    }, {"schema_version": 1, "python_spellings": spellings}


def _page_value(value: Any, kind: str) -> None:
    require(
        type(value) is dict and set(value) in ({"items"}, {"items", "next"}),
        "Invalid observed page",
    )
    for record in o.contract.array(value["items"], "page items"):
        RECORD_TYPES[kind](record)
    if "next" in value:
        item = value["next"]
        require(
            type(item) is dict and set(item) in ({"after"}, {"after", "order"}),
            "Invalid observed cursor",
        )
        Cursor(**_cursor(item))


def _result(op: str, value: Any) -> None:
    if value == ["undefined"]:
        require(
            op
            in {
                "conversation",
                "task",
                "submission",
                "request",
                "entry",
                "head",
                "close",
            },
            "Unexpected undefined result",
        )
        return
    tree = v.decode(value)
    OwnedJson(tree)
    if op in {"mint", "commit"}:
        safe_integer(tree)
    elif op in {"conversation", "task", "submission", "request", "head"}:
        RECORD_TYPES[{"request": "submission", "head": "entry"}.get(op, op)](tree)
    elif op == "entry":
        shape(tree, {"entry", "commitSeq"}, "observed entry lookup")
        EntryRecord(tree["entry"])
        safe_integer(tree["commitSeq"])
    elif op.startswith("scan_"):
        _page_value(
            tree,
            {
                "scan_conversations": "conversation",
                "scan_entries": "entry",
                "scan_tasks": "task",
                "scan_submissions": "submission",
            }[op],
        )
    elif op == "ownership":
        shape(
            tree,
            {"calls", "source_detached", "placements_independent", "read_detached"},
            "live ownership observations",
        )
        require(
            all(
                type(tree[k]) is bool
                for k in ("source_detached", "placements_independent", "read_detached")
            ),
            "Malformed ownership fact",
        )
        require(
            type(tree["calls"]) is list and len(tree["calls"]) == 3,
            "Incomplete ownership calls",
        )
    else:
        require(False, "Unexpected void result")


def trace(value: Any) -> Any:
    shape(value, {"schema_version", "phase", "backend", "scenarios"}, "table trace")
    require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE
        and type(value["backend"]) is str
        and value["backend"] in {"node", "python"},
        "Unsupported table trace",
    )
    scripts = recipe(load(INPUT))["scenarios"]
    scenarios = o.contract.array(value["scenarios"], "trace scenarios")
    require(len(scenarios) == len(scripts), "Incomplete trace scenarios")
    for scenario, script in zip(scenarios, scripts, strict=True):
        shape(scenario, {"id", "operations"}, "trace scenario")
        require(scenario["id"] == script["id"], "Trace scenario identity drift")
        steps = o.contract.array(scenario["operations"], "trace operations")
        require(len(steps) == len(script["operations"]), "Incomplete trace operations")
        for step, expected in zip(steps, script["operations"], strict=True):
            shape(step, {"id", "op", "args", "outcome"}, "trace operation")
            require(
                step["id"] == expected["id"] and step["op"] == expected["op"],
                "Trace operation identity drift",
            )
            _recipe_args(step["op"], v.decode(step["args"]))
            outcome = step["outcome"]
            require(type(outcome) is dict, "Malformed outcome")
            if outcome.get("status") == "fulfilled":
                shape(outcome, {"status", "value"}, "fulfilled result")
                _result(step["op"], outcome["value"])
            else:
                shape(outcome, {"status", "error"}, "rejected result")
                require(outcome["status"] == "rejected", "Unknown result status")
                error = shape(outcome["error"], {"name", "message"}, "rejected error")
                require(
                    type(error["name"]) is str
                    and error["name"]
                    in {
                        "Error",
                        "RuntimeError",
                        "StorageClosedError",
                        "StorageRequestError",
                    }
                    and type(error["message"]) is str,
                    "Malformed error",
                )
    return value


def projection(value: Any) -> Any:
    result = json.loads(json.dumps(trace(value)))
    result["backend"] = "storage"
    for scenario in result["scenarios"]:
        for step in scenario["operations"]:
            outcome = step["outcome"]
            if outcome["status"] == "rejected":
                outcome["error"]["name"] = {
                    "RuntimeError": "Error",
                    "StorageClosedError": "Error",
                }.get(outcome["error"]["name"], outcome["error"]["name"])
    return result


def comparison(expected: Any, actual: Any) -> int:
    return 0 if projection(expected) == projection(actual) else 1


def configuration() -> Any:
    config = load(MANIFEST)
    shape(
        config,
        {
            "schema_version",
            "phase",
            "reference_commit",
            "baseline_manifest_sha256",
            "a1_manifest_sha256",
            "closure",
            "reference_sources",
        },
        "table manifest",
    )
    require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN,
        "Unsupported table manifest",
    )
    o.contract.shape(config["closure"], CLOSURE, "a2a closed artifact inventory")
    for name, digest in config["closure"].items():
        o.contract.digest(digest)
        require(o.sha(ROOT / name) == digest, "Table artifact drift")
    o.contract.shape(
        config["reference_sources"], SOURCES, "table pinned source inventory"
    )
    for digest in config["reference_sources"].values():
        o.contract.digest(digest)
    require(
        config["baseline_manifest_sha256"] == o.sha(o.FIXTURES / "manifest.json")
        and config["a1_manifest_sha256"] == o.sha(v.MANIFEST),
        "Parent manifest drift",
    )
    v.configuration()
    recipe(load(INPUT))
    return config


def _mutation(raw: Any, name: str) -> Any:
    value = json.loads(json.dumps(raw))
    scenario, operation = {
        "field": ("identity", "exact"),
        "id": ("identity", "exact"),
        "seq": ("identity", "exact"),
        "request-index": ("replacement", "winner"),
        "status-index": ("replacement", "running"),
        "cursor": ("cursor", "desc"),
        "alias": ("ownership", "owned"),
    }[name]
    rows = [s for s in value["scenarios"] if s["id"] == scenario]
    require(len(rows) == 1, "Missing mutation scenario")
    steps = [s for s in rows[0]["operations"] if s["id"] == operation]
    require(len(steps) == 1, "Missing mutation operation")
    outcome = steps[0]["outcome"]
    require(outcome["status"] == "fulfilled", "Missing fulfilled mutation result")
    tree = v.decode(outcome["value"])
    if name == "field":
        require("data" in tree["entry"], "Missing optional mutation field")
        del tree["entry"]["data"]
    elif name == "id":
        tree["entry"]["id"] = 104
    elif name == "seq":
        tree["commitSeq"] += 1
    elif name == "request-index":
        tree["id"] = 30
    elif name == "status-index":
        tree["items"][0]["state"] = {"status": "pending", "checkpoint": None}
    elif name == "cursor":
        tree["next"]["after"] += 1
    else:
        tree["placements_independent"] = False
    outcome["value"] = v.encode(tree)
    trace(value)
    return value


def execute_mutants(output: Path, raw: Any, env: dict[str, str]) -> Any:
    results = []
    for name in MUTANTS:
        path = output / (name + "-mutant.json")
        dump(path, _mutation(raw, name))
        for label, actual, wanted in [
            ("positive", output / "observed.json", 0),
            ("negative", path, 1),
        ]:
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
                require(wanted == 1, "Positive table comparison failed")
            require(
                o.load(output / (identity + ".json"))["exit_code"] == wanted,
                "Table mutant must be valid mismatch1, never invalid2",
            )
        results.append(
            {
                "id": name,
                "schema_valid": True,
                "positive_exit": 0,
                "negative_exit": 1,
                "sha256": o.sha(path),
            }
        )
    return results


def _raw_tag(value: Any) -> None:
    require(
        type(value) is list and bool(value) and type(value[0]) is str,
        "Malformed raw tag",
    )
    if value == ["undefined"]:
        return
    if value[0] == "array":
        require(len(value) == 2, "Malformed raw array")
        for item in o.contract.array(value[1], "raw array"):
            _raw_tag(item)
    elif value[0] == "object":
        require(len(value) == 2, "Malformed raw object")
        keys = []
        for item in o.contract.array(value[1], "raw fields"):
            require(type(item) is list and len(item) == 2, "Malformed raw member")
            v.unhex(item[0])
            keys.append(item[0])
            _raw_tag(item[1])
        require(len(keys) == len(set(keys)), "Duplicate raw UTF16 keys")
    else:
        OwnedJson(v.decode(value))


def conformance(context: Any, raw: Any, observed: Any) -> None:
    shape(raw, {"schema_version", "calls", "errors"}, "raw conformance")
    require(
        type(raw["schema_version"]) is int and raw["schema_version"] == 1,
        "Raw conformance version drift",
    )
    counts = {name: 0 for name in EXPORTED}
    rejected = 0
    for call in o.contract.array(raw["calls"], "raw conformance calls"):
        shape(call, {"case", "op", "args", "outcome"}, "raw conformance call")
        require(
            type(call["case"]) is str
            and call["case"] in counts
            and type(call["op"]) is str
            and bool(call["op"]),
            "Unknown conformance call",
        )
        counts[call["case"]] += 1
        _raw_tag(call["args"])
        outcome = call["outcome"]
        require(type(outcome) is dict, "Malformed raw outcome")
        if outcome.get("status") == "fulfilled":
            shape(outcome, {"status", "value"}, "raw result")
            _raw_tag(outcome["value"])
        else:
            shape(outcome, {"status", "error"}, "raw rejection")
            require(outcome["status"] == "rejected", "Unknown raw status")
            rejected += 1
            require(
                type(outcome["error"]) is dict
                and type(outcome["error"].get("message")) is str,
                "Malformed raw error",
            )
    require(
        counts
        == {case["name"]: case["operations"] for case in context["exported_cases"]},
        "Conformance raw call counts drift",
    )
    errors = o.contract.array(raw["errors"], "raw diagnostic errors")
    require(
        len(errors)
        == rejected
        + sum(
            step["outcome"]["status"] == "rejected"
            for scenario in observed["scenarios"]
            for step in scenario["operations"]
        ),
        "Missing raw error diagnostics",
    )
    for error in errors:
        require(
            type(error) is dict
            and set(error)
            in ({"name", "message", "stack"}, {"name", "message", "stack", "cause"}),
            "Malformed raw diagnostics",
        )
        require(
            type(error["name"]) is str
            and type(error["message"]) is str
            and (error["stack"] is None or type(error["stack"]) is str),
            "Malformed raw diagnostic scalars",
        )
        if "cause" in error:
            _raw_tag(error["cause"])


def execute(parent: Path, output: Path) -> int:
    manifest_sha = o.sha(MANIFEST)
    config = configuration()
    base = o.manifest()
    parent = parent.resolve(strict=True)
    v.artifacts.attest(parent, o.FIXTURES)
    run = o.acceptance.attest_reference(o, parent / "reference", base, o.FIXTURES)
    acceptance = o.load(parent / "acceptance.json")
    require(
        acceptance.get("status") == "acceptance-verified"
        and acceptance.get("complete") is True,
        "Incomplete parent acceptance",
    )
    parent_sha = o.sha(parent / "acceptance.json")
    snapshot = parent / "reference/snapshot"
    o.verify_sources(snapshot, config["reference_sources"])
    output = output.resolve()
    require(not output.is_relative_to(parent), "Table output must be outside parent")
    output.mkdir(mode=0o700, exist_ok=False)
    output.chmod(0o700)
    driver = snapshot / DESTINATION
    require(
        not driver.exists() and not driver.is_symlink(),
        "Table caller destination exists",
    )
    shutil.copyfile(DRIVER, driver)
    driver.chmod(0o600)
    require(o.sha(driver) == o.sha(DRIVER), "Table caller copy drift")
    shutil.copyfile(INPUT, output / "input.json")
    env = o.child_env(output)
    for runtime in ("node", "npm"):
        require(
            o.run(
                [runtime, "--version"], snapshot, output, runtime + "-version", env, 15
            )
            .decode()
            .strip()
            == base["runtime"][runtime],
            "Table runtime drift",
        )
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
        "pi-memory",
        env,
        60,
    )
    modules = o.verify_resolutions(
        snapshot,
        o.load(output / "resolutions.json"),
        o.load(parent / "reference/source-sha256.json"),
        {DESTINATION: o.sha(DRIVER)},
        MODULES,
    )
    o.module_dependencies(snapshot, modules, run["installed_dependencies"])
    deps = o.load(parent / "reference/dependency-sha256.json")
    for path, digest in modules.items():
        if "node_modules" in Path(path).parts:
            require(deps.get(path) == digest, "Table dependency byte drift")
    context = load(output / "reference-context.json")
    shape(
        context,
        {"schema_version", "phase", "exported_cases", "scenarios"},
        "table reference context",
    )
    require(
        type(context["schema_version"]) is int
        and context["schema_version"] == 1
        and context["phase"] == PHASE
        and context["scenarios"] == len(SCENARIOS),
        "Table context identity drift",
    )
    cases = o.contract.array(context["exported_cases"], "exported cases")
    require(
        [x.get("name") if type(x) is dict else None for x in cases] == list(EXPORTED),
        "Missing exported conformance case",
    )
    for case in cases:
        shape(case, {"name", "callback_calls", "closed", "operations"}, "exported case")
        require(
            type(case["callback_calls"]) is int
            and case["callback_calls"] == 1
            and case["closed"] is True
            and type(case["operations"]) is int
            and case["operations"] > 0,
            "Incomplete actual conformance execution",
        )
    raw = trace(load(output / "observed.json"))
    conformance(context, load(output / "conformance-raw.json"), raw)
    product, witness = native(load(INPUT))
    trace(product)
    dump(output / "native.json", product)
    dump(output / "native-context.json", witness)
    dump(output / "observed-projected.json", projection(raw))
    dump(output / "native-projected.json", projection(product))
    code = comparison(raw, product)
    mutants = execute_mutants(output, raw, env)
    o.acceptance.attest_reference(o, parent / "reference", base, o.FIXTURES)
    v.artifacts.attest(parent, o.FIXTURES)
    configuration()
    require(
        o.sha(MANIFEST) == manifest_sha
        and o.sha(parent / "acceptance.json") == parent_sha,
        "Table manifest/parent identity drift",
    )
    names = [
        "input.json",
        "observed.json",
        "native.json",
        "native-context.json",
        "observed-projected.json",
        "native-projected.json",
        "reference-context.json",
        "conformance-raw.json",
        "resolutions.json",
        "pi-memory.json",
    ]
    if code:
        dump(
            output / "mismatch-report.json",
            {
                "schema_version": 1,
                "phase": PHASE,
                "status": "mismatch",
                "expected": "observed.json",
                "actual": "native.json",
                "expected_sha256": o.sha(output / "observed.json"),
                "actual_sha256": o.sha(output / "native.json"),
                "expected_projection": "observed-projected.json",
                "actual_projection": "native-projected.json",
                "expected_projection_sha256": o.sha(output / "observed-projected.json"),
                "actual_projection_sha256": o.sha(output / "native-projected.json"),
            },
        )
        names.append("mismatch-report.json")
    dump(
        output / "result.json",
        {
            "schema_version": 1,
            "phase": PHASE,
            "status": "PASS" if code == 0 else "mismatch",
            "execution_id": str(uuid.uuid4()),
            "fresh_reference_bootstrap": False,
            "parent_execution_id": acceptance["execution_id"],
            "parent_acceptance_sha256": parent_sha,
            "manifest_sha256": manifest_sha,
            "reference_run_sha256": o.sha(parent / "reference/run.json"),
            "module_sha256": modules,
            "artifacts_sha256": {name: o.sha(output / name) for name in names},
            "mutants": mutants,
            "scenarios": len(SCENARIOS),
            "exported_node_cases": len(EXPORTED),
            "product_integration": False,
            "document_capability": False,
            "full_storage_parity": False,
        },
    )
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("expected", type=Path)
    compare.add_argument("actual", type=Path)
    run = commands.add_parser("run")
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
        print(
            f"Invalid table evidence: {type(error).__name__}: {error}", file=sys.stderr
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
