# ruff: noqa: E402
"""DE11 genuine public Pi admission/completion subset versus native product API.

Native explicit resume, one-input restriction, retry selectors, OS ownership and
physical worker lifetime are independently declared native policy, not Pi parity.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.util
import json
import shutil
import sys
import threading
import uuid
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


canonical = load(
    "product_inspection_parent", ROOT / "scripts/durable_inspection_comparison.py"
)
policy = load(
    "native_product_policy", ROOT / "scripts/durable_reference/product_policy.py"
)
proc = load(
    "product_process_receipts", ROOT / "scripts/durable_reference/product_process.py"
)
d, b, o, v = canonical.d, canonical.b, canonical.o, canonical.v
completed = canonical.canonical
tools = completed.canonical
generation = tools.canonical

from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.usage import AgentTokenPricing
from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.native.durable.inspection_read import tasks
from pipy_harness.native.durable.storage import StorageWrite
from pipy_harness.native.durable.tool_execution import ToolRegistration
from pipy_harness.native.durable.tx import RawDocumentChange
from pipy_harness.sdk import (
    DurableProviderProfile,
    create_durable_product_session,
    open_durable_product_session,
)

# Pure lazy roots also reached by the actual fixed transport and guarded reads.
# Fresh attestation loads these sources without constructing/running a provider.
importlib.import_module("pipy_harness.native.providers.openai_completions")

PHASE = "product-session-v1"
MANIFEST = o.FIXTURES / "product-session-v1.manifest.json"
DRIVER = ROOT / "scripts/durable_reference/cases/product.mts"
READER = ROOT / "scripts/durable_reference/product_read.py"
DESTINATION = "product.mts"
MODULES = canonical.MODULES | {
    "packages/durable/src/harness/submissions.ts",
    "packages/durable/src/harness/generation.ts",
    "packages/durable/src/harness/tool.ts",
    "packages/durable/src/harness/usage.ts",
}
SOURCES = canonical.SOURCES | {
    "packages/durable/src/harness/submissions.ts",
    "packages/durable/src/harness/live.ts",
    "packages/durable/src/storage/sqlite/schema.ts",
    "packages/durable/src/storage/sqlite/migrations.ts",
    "packages/durable/test/harness-submissions.test.ts",
    "packages/durable/test/harness-lifecycle.test.ts",
}
CLOSURE = canonical.CLOSURE | {
    "scripts/durable_product_comparison.py",
    "scripts/durable_reference/cases/product.mts",
    "scripts/durable_reference/product_read.py",
    "scripts/durable_reference/product_process.py",
    "scripts/durable_reference/product_policy.py",
    "tests/fixtures/durable/product-session-v1.input.json",
    "tests/durable/test_product_comparison.py",
    "tests/durable/test_scheduler_pause.py",
    "tests/durable/test_product_session.py",
    "tests/durable/test_product_faults.py",
    "tests/durable/test_product_fences.py",
    "tests/durable/test_product_lifecycle.py",
    "tests/durable/test_product_process.py",
    "tests/durable/test_product_profiles.py",
    "tests/durable/test_product_values.py",
    "tests/durable/test_tool_adjudication.py",
    "tests/durable/test_tool_adjudication_atomic.py",
    "tests/durable/test_tool_adjudication_lifecycle.py",
    "tests/durable/test_tool_adjudication_sdk_replay.py",
    "tests/durable/adjudication_sdk_support.py",
    "tests/durable/test_c2_historical_joins.py",
    "tests/durable/c2_historical_join_support.py",
    "tests/durable/c2_historical_actor.py",
    "tests/durable/interrupted_cut_support.py",
    "tests/durable/test_tool_recovery_historical_holds.py",
    "docs/durable-product-session.md",
    "docs/durable-adjudication.md",
    "docs/specs/2026-10-27-de13c2-explicit-historical-adjudication-plan.md",
    "docs/sdk.md",
    "zensical.toml",
    "tests/test_sdk.py",
    "tests/test_architecture_archive_sdk_contracts.py",
}
CASES = ("plain", "tools")
MUTANTS = (
    "split_admission",
    "precommit_publish",
    "wrong_input",
    "not_completed",
    "order",
    "effects",
    "double_usage",
    "usage_once",
    "reconnect",
    "repeat",
)
NUMBER_PROFILE = "finite-binary64-live-v1"
COMPARISON_PROFILE = "public-product-admission-completed-subset-v1"
BACKENDS = {name: ["sqlite-product-with-empty-and-terminal-reopen"] for name in CASES}
FIELDS = {
    "empty_reopen",
    "atomic_admission",
    "commit_readable",
    "user_text",
    "completed",
    "roles",
    "result_names",
    "result_text",
    "provider_calls",
    "tool_calls",
    "applications",
    "prepares",
    "reconnect",
    "repeat_unchanged",
    "usage",
    "usage_once",
}


def recipe():
    value = d.load(o.FIXTURES / "product-session-v1.input.json")
    d.shape(value, {"schema_version", "phase", "cases"}, "Product recipe")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Product recipe identity",
    )
    d.require(
        value["cases"]
        == [{"id": "plain", "names": []}, {"id": "tools", "names": ["keyed", "later"]}],
        "Product recipe cases",
    )
    return value


def common_profile(value):
    return copy.deepcopy(value)


def publication_tree(publication):
    changes = []
    for change in publication.changes:
        if type(change) is StorageWrite:
            changes.append(change.to_tree())
        else:
            d.require(type(change) is RawDocumentChange, "Unknown product publication")
            changes.append(
                {
                    "type": "document",
                    "record": change.record.to_tree(),
                    "conversationId": change.conversation_id,
                    "version": change.version,
                    "value": None if change.value is None else change.value.thaw(),
                    "ops": change.ops.thaw(),
                }
            )
    return {"seq": publication.seq, "changes": changes}


def birth_facts(raw):
    birth = next(
        publication
        for publication in raw["publications"]
        if any(
            change["type"] == "entry" and change["value"]["kind"] == "pi.user"
            for change in publication["changes"]
        )
    )
    user = next(
        change["value"]
        for change in birth["changes"]
        if change["type"] == "entry" and change["value"]["kind"] == "pi.user"
    )
    run = next(
        (
            change["value"]
            for change in birth["changes"]
            if change["type"] == "task" and change["value"]["kind"] == "pi.generation"
        ),
        None,
    )
    binding = next(
        (
            change["value"]
            for change in birth["changes"]
            if change["type"] == "document"
            and change["record"]["kind"] == "pipy.product.binding"
        ),
        None,
    )
    return birth, user, run, binding


def project(case, raw):
    snapshot = raw["retained"]
    messages = [decode_canonical(row["canonical"]) for row in snapshot["messages"]]
    roles = {
        AgentUserMessage: "user",
        AgentAssistantMessage: "assistant",
        AgentToolResultMessage: "toolResult",
    }
    results = [m for m in messages if type(m) is AgentToolResultMessage]
    ledger = generation.project_ledger_usage(
        snapshot["usage"]["models"]["openai/gpt-test"]
    )
    usages = [
        generation.project_usage(m.usage)
        for m in messages
        if type(m) is AgentAssistantMessage
    ]
    birth, user, run, binding = birth_facts(raw)
    atomic = (
        run is not None
        and binding is not None
        and user["conversationId"]
        == run["conversationId"]
        == binding["conversation"]
        == raw["admission"]["conversation_id"]
        and binding["initial_entry"] == user["id"] == raw["admission"]["entry_id"]
        and binding["initial_generation"]
        == run["id"]
        == raw["admission"]["generation_task_id"]
    )
    stored = raw["independent"]["stored"]
    selected = raw["tasks"]
    return {
        "empty_reopen": raw["empty_before"]["messages"]
        == raw["empty_after"]["messages"]
        == []
        and raw["empty_counts"] == {"providers": 0, "calls": 0},
        "atomic_admission": bool(atomic),
        "commit_readable": d.same(stored["entry"], user)
        and d.same(stored["task"], run)
        and stored["commit_seq"] == birth["seq"],
        "user_text": next(
            m.content.value for m in messages if type(m) is AgentUserMessage
        ),
        "completed": any(row["kind"] == "pi.generation" for row in selected)
        and all(
            row["state"]["status"] == "terminal"
            and row["state"]["outcome"]["status"] == "completed"
            for row in selected
            if row["kind"] in ("pi.generation", "pi.tool")
        ),
        "roles": [roles[type(m)] for m in messages],
        "result_names": [m.tool_name for m in results],
        "result_text": [m.content.value for m in results],
        "provider_calls": raw["after"]["providers"],
        "tool_calls": raw["after"]["calls"],
        "applications": raw["after"]["applications"],
        "prepares": raw["after"]["prepares"],
        "reconnect": raw["final"]["messages"] == snapshot["messages"],
        "repeat_unchanged": raw["before"] == raw["after"]
        and raw["repeat"]["outcome"] == "completed",
        "usage": ledger,
        "usage_once": tools.usage_matches(usages, ledger),
    }


async def native_case(case, folder, output, env):
    folder.mkdir(parents=True)
    effects = {"calls": [], "applications": {}, "prepares": []}
    owner = []
    held = [False, threading.Event(), threading.Event()]
    ports = [tools.Tool(name, effects, owner, held) for name in ("keyed", "later")]
    registry = {
        port.definition.name: ToolRegistration(
            port, "fixture." + port.definition.name, "1", "safe"
        )
        for port in ports
    }
    wire = tools.Wire({**case, "reasoning": 3})
    profile = DurableProviderProfile(
        NativeModelSpec("openai", "gpt-test", "Test", "openai-completions"),
        auth_path=folder / "auth.json",
        env={},
        runtime_api_key="fixture-key",
        pricing=AgentTokenPricing(1, 2, 3, 4),
        http_client=wire,
    )
    path = folder / "store.sqlite"
    product = await create_durable_product_session(
        path, workspace=folder, profile=profile, tools=registry
    )
    watch = await product.observe_completed()
    empty_before = completed.snapshot_tree(watch.snapshot)
    watch.stop()
    await product.close()
    product = await open_durable_product_session(
        path, workspace=folder, profile=profile, tools=registry
    )
    owner.append(product._session)
    watch = await product.observe_completed()
    empty_after = completed.snapshot_tree(watch.snapshot)
    watch.stop()
    empty_counts = {"providers": len(wire.calls), "calls": len(effects["calls"])}
    publications, independent = [], []

    def committed(publication):
        value = publication_tree(publication)
        publications.append(value)
        user = next(
            (
                change["value"]
                for change in value["changes"]
                if change["type"] == "entry" and change["value"]["kind"] == "pi.user"
            ),
            None,
        )
        if user is None:
            return
        run = next(
            change["value"]
            for change in value["changes"]
            if change["type"] == "task" and change["value"]["kind"] == "pi.generation"
        )
        stdout, stderr, code = proc.run(
            d,
            [
                sys.executable,
                "-I",
                str(READER),
                str(path),
                str(user["id"]),
                str(run["id"]),
            ],
            ROOT,
            output / "processes",
            "reader-" + case["id"],
            env,
            15,
        )
        d.require(code == 0 and not stderr, "Product independent commit reader failed")
        independent.append(json.loads(stdout))

    unsubscribe = product._session.subscribe_commits(committed)
    try:
        admission = await product.submit("input")
        pre_resume = {"providers": len(wire.calls), "calls": len(effects["calls"])}
        d.require(
            pre_resume == {"providers": 0, "calls": 0},
            "Product dispatched without explicit resume",
        )
        result = await product.resume()
        final = completed.snapshot_tree(result.completed)
        rows = await product._read(lambda: tasks(product._session._owner.storage))
        header = await product._read(product._header)
        d.require(len(independent) == 1, "Product admission reader missing")
        before = {
            "providers": len(wire.calls),
            "calls": len(effects["calls"]),
            "applications": len(effects["applications"]),
            "prepares": len(effects["prepares"]),
        }
    finally:
        unsubscribe()
        await product.close()
    host_alive = product._scheduler._thread.is_alive()
    for material in folder.glob("auth.json*"):
        material.unlink()
    reopened = await open_durable_product_session(path, workspace=folder, tools={})
    try:
        repeated = await reopened.resume()
        retained = completed.snapshot_tree(repeated.completed)
        repeat = repeated.inspection.to_tree()
        after = {
            "providers": len(wire.calls),
            "calls": len(effects["calls"]),
            "applications": len(effects["applications"]),
            "prepares": len(effects["prepares"]),
        }
    finally:
        await reopened.close()
    raw = {
        "admission": asdict(admission),
        "header": header,
        "empty_before": empty_before,
        "empty_after": empty_after,
        "empty_counts": empty_counts,
        "publications": publications,
        "independent": independent[0],
        "pre_resume": pre_resume,
        "tasks": rows,
        "effects": effects,
        "requests": wire.calls,
        "final": final,
        "retained": retained,
        "before": before,
        "after": after,
        "repeat": repeat,
        "host_alive_after_close": host_alive,
    }
    target = output / "native-records" / "sqlite"
    target.mkdir(parents=True, exist_ok=True)
    d.dump(target / (case["id"] + ".json"), raw)
    return {"id": case["id"], "observed": v.encode(project(case, raw))}


async def native(folder, env=None):
    output = folder.parent
    env = o.child_env(output) if env is None else env
    rows = [
        await native_case(case, folder / "sqlite" / case["id"], output, env)
        for case in recipe()["cases"]
    ]
    value = {"schema_version": 1, "phase": PHASE, "cases": rows}
    d.dump(output / "native-exact.json", value)
    return common_profile(value)


def trace(value):
    d.shape(value, {"schema_version", "phase", "cases"}, "Product trace")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Product trace identity",
    )
    d.require(
        type(value["cases"]) is list
        and [row.get("id") for row in value["cases"] if type(row) is dict]
        == list(CASES),
        "Product trace cases",
    )
    for row in value["cases"]:
        d.shape(row, {"id", "observed"}, "Product row")
        observed = v.decode(row["observed"])
        d.shape(observed, FIELDS, "Product observation")
        observation_types(observed)
        d.require(v.encode(observed) == row["observed"], "Product value profile")
    return value


def observation_types(value):
    flags = {
        "empty_reopen",
        "atomic_admission",
        "commit_readable",
        "completed",
        "reconnect",
        "repeat_unchanged",
        "usage_once",
    }
    d.require(
        all(type(value[name]) is bool for name in flags), "Product observation flags"
    )
    d.require(type(value["user_text"]) is str, "Product input text")
    for name in ("roles", "result_names", "result_text"):
        d.require(
            type(value[name]) is list
            and all(type(item) is str for item in value[name]),
            "Product message lists",
        )
    d.require(
        all(
            type(value[name]) is int and value[name] >= 0
            for name in ("provider_calls", "tool_calls", "applications", "prepares")
        ),
        "Product counted effects",
    )
    usage = value["usage"]
    required = {"input", "output", "cacheRead", "cacheWrite", "totalTokens", "cost"}
    d.require(
        type(usage) is dict
        and required <= usage.keys() <= required | {"reasoning", "cacheWrite1h"},
        "Product usage fields",
    )
    d.require(
        all(
            type(number) in (int, float) and number >= 0
            for name, number in usage.items()
            if name != "cost"
        ),
        "Product usage numbers",
    )
    d.shape(
        usage["cost"],
        {"input", "output", "cacheRead", "cacheWrite", "total"},
        "Product cost",
    )
    d.require(
        all(
            type(number) in (int, float) and number >= 0
            for number in usage["cost"].values()
        ),
        "Product cost numbers",
    )


def compare(expected, actual):
    trace(expected)
    trace(actual)
    return int(not d.same(expected, actual))


def mutate(actual, name):
    value = copy.deepcopy(actual)
    row = v.decode(value["cases"][1]["observed"])
    if name == "order":
        row["result_names"].reverse()
    elif name == "effects":
        row["provider_calls"] += 1
    elif name == "double_usage":
        row["usage"]["totalTokens"] *= 2
    elif name == "wrong_input":
        row["user_text"] = "other input"
    else:
        field = {
            "split_admission": "atomic_admission",
            "precommit_publish": "commit_readable",
            "not_completed": "completed",
            "usage_once": "usage_once",
            "reconnect": "reconnect",
            "repeat": "repeat_unchanged",
        }[name]
        row[field] = not row[field]
    value["cases"][1]["observed"] = v.encode(row)
    return value


def reader_attest(folder, raw, case_id):
    label = "reader-" + case_id
    record = d.load(folder / "processes" / (label + ".json"))
    process_identity(record, label, folder, folder.parent)
    value = d.load(folder / "processes" / (label + ".stdout"))
    d.require(
        d.same(value, raw["independent"])
        and value["pid"] == record["pid"]
        and value["pgid"] == record["pgid"],
        "Product independent process identity drift",
    )
    d.require(
        value["helper_sha256"] == o.sha(READER)
        and Path(value["executable"]).resolve() == Path(sys.executable).resolve(),
        "Product independent helper/interpreter drift",
    )


def reference_readers(folder, snapshot):
    process = d.load(folder / "processes" / "pi-product.json")
    runtime = d.load(folder / "runtime.json")
    d.require(
        runtime["pid"] == process["pid"]
        and Path(runtime["executable"]).resolve() == Path(process["executable"]),
        "Pi product runtime process drift",
    )
    d.require(
        runtime["version"] == o.manifest()["runtime"]["node"]
        and type(runtime["sqlite"]) is str,
        "Pi product actual runtime version",
    )
    for raw in d.load(folder / "raw-stored.json"):
        d.require(len(raw["independent"]) == 1, "Pi product independent reader missing")
        child = raw["independent"][0]
        observed = json.loads(child["stdout"])
        wanted = [
            sys.executable,
            "-I",
            str(READER),
            str(folder / "stores" / "pi" / (raw["id"] + ".sqlite")),
            str(observed["stored"]["entry"]["id"]),
            str(observed["stored"]["task"]["id"]),
        ]
        d.require(
            child["argv"] == wanted
            and child["cwd"] == str(snapshot)
            and child["status"] == 0
            and not child["stderr"],
            "Pi product reader argv/exit drift",
        )
        d.require(
            child["pid"] == observed["pid"]
            and observed["pgid"] == process["pid"]
            and observed["helper_sha256"] == o.sha(READER),
            "Pi product actual reader identity drift",
        )
        d.require(
            d.same(child["observed"], observed),
            "Pi product retained reader facts drift",
        )


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
        "Product manifest",
    )
    d.require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN,
        "Product manifest identity",
    )
    for key, names in (("closure", CLOSURE), ("reference_sources", SOURCES)):
        d.shape(config[key], names, "Product " + key)
        for name, digest in config[key].items():
            o.contract.digest(digest)
            if key == "closure":
                d.require(o.sha(ROOT / name) == digest, "Product closure drift")
    d.require(
        config["parents"] == {"inspection": o.sha(canonical.MANIFEST)},
        "Product parent drift",
    )
    canonical.configuration()
    return config


def native_modules():
    modules = {}
    for name, module in tuple(sys.modules.items()):
        if not any(
            name == package or name.startswith(package + ".")
            for package in ("pipy_harness", "pipy_session")
        ):
            continue
        filename = getattr(module, "__file__", None)
        d.require(filename is not None, "Product producer lacks source")
        path = Path(filename).resolve()
        d.require(path.is_relative_to(ROOT), "Product producer outside checkout")
        relative = str(path.relative_to(ROOT))
        d.require(relative in CLOSURE, "Product loaded producer outside closure")
        modules[relative] = o.sha(path)
    return modules


def policy_check(value):
    return int(not policy.valid(value, d))


def policy_controls(output, env):
    raw = policy.produce(
        output / "stores" / "product-policy", d, proc.run, output / "processes", env
    )
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
            stdout, stderr, code = proc.run(
                d,
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
    _, _, code = proc.run(
        d,
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
    for label, key in (
        ("lifetime-provider", "provider"),
        ("lifetime-finalizer", "finalizer"),
        ("lifetime-closed", "closed"),
    ):
        process = d.load(folder / "processes" / (label + ".json"))
        process_identity(process, label, folder, snapshot)
        observed = d.load(folder / "processes" / (label + ".stdout"))
        d.require(
            d.same(observed, raw["lifetime"][key])
            and observed["pid"] == process["pid"]
            and observed["pgid"] == process["pgid"],
            "Product actual lifetime process drift",
        )
        d.require(
            observed["source"]
            == str(ROOT / "src/pipy_harness/native/durable/sqlite.py")
            and observed["sha256"]
            == o.sha(ROOT / "src/pipy_harness/native/durable/sqlite.py"),
            "Product actual lifetime import drift",
        )
    for label, code in results.items():
        process = d.load(folder / "processes" / (label + ".json"))
        process_identity(process, label, folder, snapshot)
        d.require(process["returncode"] == code, "Native operator child exit drift")


def execute(acceptance, output):
    d.require(
        not output.exists() and not output.is_symlink(),
        "Product output exists",
    )
    acceptance, output = acceptance.resolve(strict=True), output.absolute()
    d.require(
        not output.resolve().is_relative_to(acceptance),
        "Product output inside parent",
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
        "Product driver exists",
    )
    output.mkdir(mode=0o700)
    processes, mutants = output / "processes", output / "mutants"
    processes.mkdir(mode=0o700)
    mutants.mkdir(mode=0o700)
    d.dump(output / "input.json", recipe())
    shutil.copyfile(DRIVER, driver)
    env = o.child_env(output)
    for binary in ("node", "npm"):
        stdout, stderr, code = proc.run(
            d, [binary, "--version"], snapshot, processes, binary + "-version", env, 15
        )
        d.require(
            code == 0
            and not stderr
            and stdout.decode().strip() == parent["runtime"][binary],
            "Product runtime drift",
        )
    stdout, stderr, code = proc.run(
        d,
        [
            "node",
            "--import",
            str(snapshot / o.RESOLVER),
            str(driver),
            str(output / "input.json"),
            str(output),
            sys.executable,
            str(READER),
        ],
        snapshot,
        processes,
        "pi-product",
        env,
        120,
    )
    d.require(
        code == 0 and not stdout and not stderr,
        "Pinned product driver failed",
    )
    modules = o.verify_resolutions(
        snapshot,
        d.load(output / "resolutions.json"),
        d.load(acceptance / "reference/source-sha256.json"),
        {DESTINATION: o.sha(DRIVER)},
        sorted(MODULES),
    )
    o.module_dependencies(snapshot, modules, run["installed_dependencies"])
    reference_readers(output, snapshot)
    expected = trace(d.load(output / "observed.json"))
    actual = asyncio.run(native(output / "stores", env))
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
            _, _, code = proc.run(
                d,
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
            d.require(code == wanted, "Product semantic CLI control failed")
        controls.append(name)
    invalid = copy.deepcopy(actual)
    invalid["unknown"] = True
    d.dump(mutants / "invalid.json", invalid)
    _, _, code = proc.run(
        d,
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
    d.require(code == 2, "Product invalid CLI control failed")
    configuration()
    o.verify_sources(snapshot, config["reference_sources"])
    o.acceptance.attest_reference(o, acceptance / "reference", parent, o.FIXTURES)
    v.artifacts.attest(acceptance, o.FIXTURES)
    d.require(
        manifest_sha == o.sha(MANIFEST) and o.sha(driver) == o.sha(DRIVER),
        "Product producer drift",
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
            "native_only_product": {
                "profile": "native-product-contract-v1",
                "mutants": list(policy.MUTANTS),
            },
        },
    )
    return status


def process_identity(record, label, folder, snapshot):
    """Bind a receipt to the actual CLI/driver input, not just an exit code."""
    processes = folder / "processes"
    proc.attest(d, record, processes, label)
    if label in ("node-version", "npm-version"):
        binary = label.split("-")[0]
        wanted = [binary, "--version"]
        cwd = snapshot
    elif label == "pi-product":
        wanted = [
            "node",
            "--import",
            str(snapshot / o.RESOLVER),
            str(snapshot / DESTINATION),
            str(folder / "input.json"),
            str(folder),
            sys.executable,
            str(READER),
        ]
        cwd = snapshot
    elif label.startswith("reader-"):
        raw = d.load(
            folder
            / "native-records"
            / "sqlite"
            / (label.removeprefix("reader-") + ".json")
        )
        birth = raw["admission"]
        wanted = [
            sys.executable,
            "-I",
            str(READER),
            str(
                folder
                / "stores"
                / "sqlite"
                / label.removeprefix("reader-")
                / "store.sqlite"
            ),
            str(birth["entry_id"]),
            str(birth["generation_task_id"]),
        ]
        cwd = ROOT
    elif label.startswith("lifetime-"):
        wanted = [
            sys.executable,
            "-I",
            "-c",
            policy.OPENER,
            str(folder / "stores" / "product-policy" / "lifetime" / "store.sqlite"),
        ]
        cwd = ROOT
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
        "Product child identity drift",
    )
    stdout, stderr = (
        (processes / (label + ".stdout")).read_bytes(),
        (processes / (label + ".stderr")).read_bytes(),
    )
    if label in ("node-version", "npm-version"):
        d.require(
            stdout.decode().strip() == o.manifest()["runtime"][label.split("-")[0]]
            and not stderr,
            "Product runtime receipt drift",
        )
    elif label.startswith(("reader-", "lifetime-")):
        d.require(bool(stdout) and not stderr, "Product observer diagnostics")
    elif label in ("invalid-cli", "policy-invalid"):
        d.require(
            not stdout
            and stderr.startswith(b"INVALID:")
            and b"Traceback" not in stderr,
            "Product invalid CLI diagnostics",
        )
    else:
        d.require(not stdout and not stderr, "Product child diagnostics")


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
        "native_only_product": {
            "profile": "native-product-contract-v1",
            "mutants": list(policy.MUTANTS),
        },
    }
    d.shape(
        value,
        set(expected)
        | {"execution_id", "module_sha256", "native_module_sha256", "artifacts_sha256"},
        "product result",
    )
    for key, wanted in expected.items():
        d.require(d.same(value[key], wanted), "Product result drift: " + key)
    d.require(
        str(uuid.UUID(value["execution_id"])) == value["execution_id"],
        "Product execution ID",
    )
    files = artifact_hashes(folder)
    d.require(d.same(value["artifacts_sha256"], files), "Product artifact drift")
    reference_readers(folder, snapshot)
    reference, actual = d.load(folder / "observed.json"), d.load(folder / "native.json")
    d.require(
        compare(reference, actual) == 0
        and d.same(d.load(folder / "input.json"), recipe()),
        "Product actual pair/input drift",
    )
    for case in recipe()["cases"]:
        raw = d.load(folder / "native-records" / "sqlite" / (case["id"] + ".json"))
        d.require(
            d.same(
                v.encode(project(case, raw)),
                next(
                    row["observed"]
                    for row in actual["cases"]
                    if row["id"] == case["id"]
                ),
            ),
            "Product actual decoded projection drift",
        )
        reader_attest(folder, raw, case["id"])
    exact = trace(d.load(folder / "native-exact.json"))
    d.require(d.same(common_profile(exact), actual), "Product numeric projection drift")
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
        "Product loaded modules drift",
    )
    d.require(
        d.same(value["native_module_sha256"], native_modules()),
        "Product native producer drift",
    )
    results = {
        "node-version": 0,
        "npm-version": 0,
        "pi-product": 0,
        "invalid-cli": 2,
    }
    for name in MUTANTS:
        mutant = d.load(folder / "mutants" / (name + ".json"))
        d.require(
            d.same(mutant, mutate(actual, name)) and compare(reference, mutant) == 1,
            "Product mutant drift",
        )
        results[name + "-positive"] = 0
        results[name + "-negative"] = 1
    for label, code in results.items():
        process = d.load(folder / "processes" / (label + ".json"))
        process_identity(process, label, folder, snapshot)
        d.require(process["returncode"] == code, "Product child exit drift")
    invalid = copy.deepcopy(actual)
    invalid["unknown"] = True
    retained = d.load(folder / "mutants" / "invalid.json")
    d.require(d.same(retained, invalid), "Product invalid input was not retained")
    try:
        trace(retained)
    except o.EvidenceError:
        pass
    else:
        raise o.EvidenceError("Product invalid producer became valid")
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
