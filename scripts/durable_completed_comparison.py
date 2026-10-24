# ruff: noqa: E402
"""DE09 actual pinned public watches versus native completed replacement facts.

Only logical completed messages/usage and bounded attachment observations are
paired. Raw Pi events/task graph and native canonical arenas remain retained.
Native process death, read tickets and worker ownership have separate tests.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.util
import shutil
import sys
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "completed_tools_parent", ROOT / "scripts/durable_tool_comparison.py"
)
assert spec and spec.loader
canonical = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canonical)
d, b, o, v = canonical.d, canonical.b, canonical.o, canonical.v

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.agent.usage import AgentTokenPricing
from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.durable.canonical_codec import (
    decode_canonical,
    encode_canonical,
)
from pipy_harness.native.durable.completed_projection import observe_completed
from pipy_harness.native.durable.documents import resolve
from pipy_harness.native.durable.generation import (
    USAGE_DOC,
    define_generation,
    message_entry,
)
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.records import ConversationId, EntryId, TaskId
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import EntryQuery, TaskQuery
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)
from pipy_harness.native.models import ProviderRequest

PHASE = "completed-projection-b-v1"
MANIFEST = o.FIXTURES / "completed-projection-v1.manifest.json"
DRIVER = ROOT / "scripts/durable_reference/cases/completed.mts"
DESTINATION = "completed.mts"
MODULES = canonical.MODULES | {"packages/durable/src/harness/events.ts"}
SOURCES = canonical.SOURCES | {
    "packages/durable/src/index.ts",
    "packages/durable/src/types.ts",
    "packages/durable/src/harness/view.ts",
    "packages/durable/src/harness/events.ts",
    "packages/durable/src/session/observation.ts",
    "packages/durable/test/harness-events.test.ts",
    "packages/durable/test/harness-view.test.ts",
    "packages/durable/test/session-watches.test.ts",
}
CLOSURE = canonical.CLOSURE | {
    "scripts/durable_completed_comparison.py",
    "scripts/durable_reference/cases/completed.mts",
    "tests/fixtures/durable/completed-input-v1.json",
    "tests/durable/test_completed_comparison.py",
    "tests/durable/test_completed_projection.py",
    "tests/durable/test_completed_projection_values.py",
    "tests/durable/test_completed_projection_lifecycle.py",
    "tests/durable/test_completed_projection_crash.py",
    "tests/durable/test_completed_projection_fences.py",
    "docs/durable-completed-projection.md",
}
CASES = ("completed",)
MUTANTS = (
    "order",
    "usage",
    "doubleusage",
    "completed",
    "automatic",
    "independent",
    "isolation",
    "reconnect",
    "stop",
    "effects",
)
NUMBER_PROFILE = "finite-binary64-live-v1"
COMPARISON_PROFILE = "logical-completed-watch-facts-v1"
BACKENDS = {"completed": ["memory", "sqlite", "reopen"]}


def common_profile(value):
    return copy.deepcopy(value)


def recipe():
    value = d.load(o.FIXTURES / "completed-input-v1.json")
    d.shape(value, {"schema_version", "phase", "cases"}, "Completed recipe")
    d.require(
        value["schema_version"] == 1 and value["phase"] == PHASE,
        "Completed recipe identity",
    )
    d.require(
        value["cases"]
        == [
            {
                "id": "completed",
                "names": ["keyed", "unavailable", "later"],
                "reasoning": 3,
            }
        ],
        "Completed recipe cases",
    )
    return value


def snapshot_tree(snapshot):
    return {
        "conversationId": snapshot.conversation_id,
        "revision": snapshot.revision,
        "publicationSeq": snapshot.publication_seq,
        "messages": [
            {
                "entryId": m.entry_id,
                "commitSeq": m.commit_seq,
                "byTaskId": m.by_task_id,
                "canonical": m.canonical.thaw(),
                "providerOutcome": None
                if m.provider_outcome is None
                else m.provider_outcome.thaw(),
                "toolOutput": None if m.tool_output is None else m.tool_output.thaw(),
            }
            for m in snapshot.messages
        ],
        "rounds": [
            {"assistant": r.assistant_id, "results": list(r.result_ids)}
            for r in snapshot.rounds
        ],
        "outcomes": [
            {
                "taskId": row.task_id,
                "kind": row.kind,
                "version": row.version,
                "outcome": row.outcome.thaw(),
            }
            for row in snapshot.outcomes
        ],
        "usage": None if snapshot.usage is None else snapshot.usage.thaw(),
    }


def project(case, raw):
    snapshot = raw["retained"]
    messages = [decode_canonical(row["canonical"]) for row in snapshot["messages"]]
    roles = {
        AgentUserMessage: "user",
        AgentAssistantMessage: "assistant",
        AgentToolResultMessage: "toolResult",
    }
    results = [m for m in messages if type(m) is AgentToolResultMessage]
    ledger = canonical.canonical.project_ledger_usage(
        snapshot["usage"]["models"]["openai/gpt-test"]
    )
    usages = [
        canonical.canonical.project_usage(m.usage)
        for m in messages
        if type(m) is AgentAssistantMessage
    ]
    return {
        "completed": all(
            row["state"]["status"] == "terminal"
            and row["state"]["outcome"]["status"] == "completed"
            for row in raw["tasks"]
            if row["kind"] in ("pi.generation", "pi.tool")
        ),
        "roles": [roles[type(m)] for m in messages],
        "result_names": [m.tool_name for m in results],
        "result_text": [
            m.content.value for m in results if m.tool_name != "unavailable"
        ],
        "automatic": any(
            any(outcome["taskId"] == raw["parent"] for outcome in value["outcomes"])
            for value in raw["snapshots"]
        ),
        "independent": bool(raw["independent"]) and all(raw["independent"]),
        "isolation": raw["badEnd"] == "listener_error",
        "reconnect": raw["final"]["messages"] == snapshot["messages"],
        "stop": raw["stopEnd"] == "stopped",
        "effects_unchanged": raw["before"] == raw["after"],
        "provider_calls": raw["after"]["providers"],
        "tool_calls": raw["after"]["calls"],
        "usage": ledger,
        "usage_once": canonical.usage_matches(usages, ledger),
    }


async def independent(session, snapshot):
    def read():
        storage = session._owner.storage
        for message in snapshot.messages:
            lookup = storage.entry(
                EntryId(message.entry_id),
                conversation_id=ConversationId(snapshot.conversation_id),
            )
            d.require(
                lookup is not None and lookup.commit_seq == message.commit_seq,
                "Completed entry not independently readable",
            )
            d.require(
                lookup.entry.to_tree()["model"][0] == message.canonical.thaw(),
                "Completed entry model changed",
            )
        for outcome in snapshot.outcomes:
            row = storage.task(TaskId(outcome.task_id))
            d.require(
                row is not None
                and row.to_tree()["state"]
                == {"status": "terminal", "outcome": outcome.outcome.thaw()},
                "Completed task not independently readable",
            )
        if any(outcome.kind == "pi.generation" for outcome in snapshot.outcomes):
            _, address = resolve(USAGE_DOC, conversation_id=snapshot.conversation_id)
            record = storage.find_document(address)
            d.require(record is not None, "Completed usage document absent")
            found = storage.document(record.id)
            d.require(
                found is not None and found.to_tree()["value"] == snapshot.usage.thaw(),
                "Completed usage not independently readable",
            )
        return True

    return await asyncio.to_thread(lambda: session.read_on_line(read))


async def native_case(case, backend, folder):
    folder.mkdir(parents=True)
    path = folder / "session.sqlite"
    session = Session(
        MemoryStorage()
        if backend == "memory"
        else open_sqlite_storage(str(path), mode="create"),
        now=lambda: 1000,
    )
    owner = [session]
    effects = {"calls": [], "applications": {}, "prepares": []}
    held = [False, threading.Event(), threading.Event()]
    ports = [canonical.Tool(name, effects, owner, held) for name in ("keyed", "later")]
    wire = canonical.Wire(case)
    generation = define_generation(
        NativeModelSpec("openai", "gpt-test", "Test", "openai-completions"),
        auth_path=folder / "auth.json",
        env={},
        runtime_api_key="fixture-key",
        pricing=AgentTokenPricing(1, 2, 3, 4),
        http_client=wire,
    )
    tool = define_tool_task(
        {
            p.definition.name: ToolRegistration(
                p, "fixture." + p.definition.name, "1", "safe"
            )
            for p in ports
        },
        workspace_root=folder,
    )
    cid = (
        await session.acommit(lambda tx: tx.create_conversation({"kind": "ownerless"}))
    ).id
    await session.acommit(
        lambda tx: tx.append_entry(
            cid, message_entry(AgentUserMessage(ProductContent("input")))
        )
    )
    seed = snapshot_provider_request(
        ProviderRequest(
            "system",
            "input",
            "openai",
            "gpt-test",
            folder,
            available_tools=tuple(p.definition for p in ports),
        )
    )
    parent = await session.acommit(
        lambda tx: tx.create_task(
            generation,
            encode_canonical(seed),
            ownership={"kind": "conversation"},
            conversation_id=cid,
        )
    )
    if backend == "reopen":
        await session.aclose()
        session = Session(open_sqlite_storage(str(path), mode="open"), now=lambda: 1000)
        owner[0] = session
    snapshots, reads, publications = [], [], []
    session.subscribe_commits(
        lambda p: publications.append(
            {
                "seq": p.seq,
                "changes": [c.to_tree() for c in p.changes if hasattr(c, "to_tree")],
            }
        )
    )
    scheduler = TaskScheduler(session, [generation, tool])
    await scheduler.aopen()
    watch, bad = (
        await observe_completed(session, cid),
        await observe_completed(session, cid),
    )
    automatic = asyncio.Event()

    async def consume(value):
        reads.append(await independent(session, value))
        snapshots.append(snapshot_tree(value))
        if any(outcome.task_id == parent for outcome in value.outcomes):
            automatic.set()

    async def throw(value):
        raise RuntimeError("synthetic observer failure")

    try:
        watch.start(consume)
        bad.start(throw)
        scheduler.resume()
        await asyncio.wait_for(automatic.wait(), 15)
        await canonical.idle(session, scheduler)
        final = await watch.drain()
        rows = (
            await session.acommit(
                lambda tx: tx.scan_tasks(TaskQuery({"conversationId": cid}), 100)
            )
        ).items
        entries = (
            await session.acommit(
                lambda tx: tx.scan_entries(EntryQuery({"conversationId": cid}), 100)
            )
        ).items
        bad_end = await bad.wait_closed()
        watch.stop()
        stop_end = await watch.wait_closed()
        before = {
            "providers": len(wire.calls),
            "calls": len(effects["calls"]),
            "applications": len(effects["applications"]),
            "prepares": len(effects["prepares"]),
        }
        if backend != "memory":
            await session.aclose()
            # Observation after reopen has no scheduler, registrations or live
            # provider reconstruction. Terminal facts require only storage.
            session = Session(
                open_sqlite_storage(str(path), mode="open"), now=lambda: 1000
            )
        fresh = await observe_completed(session, cid)
        retained = snapshot_tree(fresh.snapshot)
        fresh.stop()
        await fresh.wait_closed()
        after = {
            "providers": len(wire.calls),
            "calls": len(effects["calls"]),
            "applications": len(effects["applications"]),
            "prepares": len(effects["prepares"]),
        }
        raw = {
            "id": case["id"],
            "backend": backend,
            "parent": parent,
            "tasks": [r.to_tree() for r in rows],
            "entries": [e.to_tree() for e in entries],
            "effects": effects,
            "requests": wire.calls,
            "publications": publications,
            "snapshots": snapshots,
            "independent": reads,
            "badEnd": bad_end,
            "stopEnd": stop_end,
            "final": snapshot_tree(final),
            "retained": retained,
            "before": before,
            "after": after,
        }
        target = folder.parents[2] / "native-records" / backend / (case["id"] + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        d.dump(target, raw)
        return {"id": case["id"], "observed": v.encode(project(case, raw))}
    finally:
        watch.stop()
        bad.stop()
        await session.aclose()


async def native(folder):
    outputs = []
    for backend in BACKENDS["completed"]:
        rows = [
            await native_case(case, backend, folder / backend / case["id"])
            for case in recipe()["cases"]
        ]
        outputs.append({"schema_version": 1, "phase": PHASE, "cases": rows})
    d.require(
        all(d.same(outputs[0], value) for value in outputs[1:]),
        "Completed backend mismatch",
    )
    d.dump(folder.parent / "native-exact.json", outputs[0])
    return common_profile(outputs[0])


def trace(value):
    d.shape(value, {"schema_version", "phase", "cases"}, "Completed trace")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Completed trace identity",
    )
    d.require(
        type(value["cases"]) is list
        and [row.get("id") for row in value["cases"] if type(row) is dict]
        == list(CASES),
        "Completed trace cases",
    )
    for row in value["cases"]:
        d.shape(row, {"id", "observed"}, "Completed row")
        observation = v.decode(row["observed"])
        d.shape(
            observation,
            {
                "completed",
                "roles",
                "result_names",
                "result_text",
                "automatic",
                "independent",
                "isolation",
                "reconnect",
                "stop",
                "effects_unchanged",
                "provider_calls",
                "tool_calls",
                "usage",
                "usage_once",
            },
            "Completed observation",
        )
        d.require(v.encode(observation) == row["observed"], "Completed value profile")
    return value


def compare(expected, actual):
    trace(expected)
    trace(actual)
    return int(not d.same(expected, actual))


def mutate(actual, name):
    value = copy.deepcopy(actual)
    row = v.decode(value["cases"][0]["observed"])
    if name == "order":
        row["result_names"].reverse()
    elif name == "usage":
        row["usage"]["input"] += 1
    elif name == "doubleusage":
        row["usage"]["totalTokens"] *= 2
    elif name == "effects":
        row["effects_unchanged"] = False
    else:
        row[name] = not row[name]
    value["cases"][0]["observed"] = v.encode(row)
    return value


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
        "completed manifest",
    )
    d.require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN,
        "Completed manifest identity",
    )
    for key, names in (("closure", CLOSURE), ("reference_sources", SOURCES)):
        d.shape(config[key], names, "completed " + key)
        for name, digest in config[key].items():
            o.contract.digest(digest)
            if key == "closure":
                d.require(o.sha(ROOT / name) == digest, "Completed closure drift")
    d.require(
        config["parents"] == {"tools": o.sha(canonical.MANIFEST)},
        "Completed parent drift",
    )
    canonical.configuration()
    return config


def native_modules():
    modules = {}
    for name, module in tuple(sys.modules.items()):
        if name != "pipy_harness" and not name.startswith("pipy_harness."):
            continue
        filename = getattr(module, "__file__", None)
        d.require(filename is not None, "Completed producer lacks a source file")
        path = Path(filename).resolve()
        d.require(path.is_relative_to(ROOT), "Completed producer outside checkout")
        relative = str(path.relative_to(ROOT))
        d.require(relative in CLOSURE, "Completed producer outside closure")
        modules[relative] = o.sha(path)
    return modules


def execute(acceptance, output):
    d.require(
        not output.exists() and not output.is_symlink(),
        "Completed output exists",
    )
    acceptance, output = acceptance.resolve(strict=True), output.absolute()
    d.require(
        not output.resolve().is_relative_to(acceptance),
        "Completed output inside parent",
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
        "Completed driver exists",
    )
    output.mkdir(mode=0o700)
    processes, mutants = output / "processes", output / "mutants"
    processes.mkdir(mode=0o700)
    mutants.mkdir(mode=0o700)
    d.dump(output / "input.json", recipe())
    shutil.copyfile(DRIVER, driver)
    env = o.child_env(output)
    for binary in ("node", "npm"):
        stdout, stderr, code = d.process(
            [binary, "--version"], snapshot, processes, binary + "-version", env, 15
        )
        d.require(
            code == 0
            and not stderr
            and stdout.decode().strip() == parent["runtime"][binary],
            "Completed runtime drift",
        )
    stdout, stderr, code = d.process(
        [
            "node",
            "--import",
            str(snapshot / o.RESOLVER),
            str(driver),
            str(output / "input.json"),
            str(output),
        ],
        snapshot,
        processes,
        "pi-completed",
        env,
        120,
    )
    d.require(
        code == 0 and not stdout and not stderr,
        "Pinned tool driver failed",
    )
    modules = o.verify_resolutions(
        snapshot,
        d.load(output / "resolutions.json"),
        d.load(acceptance / "reference/source-sha256.json"),
        {DESTINATION: o.sha(DRIVER)},
        sorted(MODULES),
    )
    o.module_dependencies(snapshot, modules, run["installed_dependencies"])
    expected = trace(d.load(output / "observed.json"))
    actual = asyncio.run(native(output / "stores"))
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
            _, _, code = d.process(
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
            d.require(code == wanted, "Completed semantic CLI control failed")
        controls.append(name)
    invalid = copy.deepcopy(actual)
    invalid["unknown"] = True
    d.dump(mutants / "invalid.json", invalid)
    _, _, code = d.process(
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
    d.require(code == 2, "Completed invalid CLI control failed")
    configuration()
    o.verify_sources(snapshot, config["reference_sources"])
    o.acceptance.attest_reference(o, acceptance / "reference", parent, o.FIXTURES)
    v.artifacts.attest(acceptance, o.FIXTURES)
    d.require(
        manifest_sha == o.sha(MANIFEST) and o.sha(driver) == o.sha(DRIVER),
        "Completed producer drift",
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
        },
    )
    return status


def process_identity(record, label, folder, snapshot):
    """Bind a receipt to the actual CLI/driver input, not just an exit code."""
    processes = folder / "processes"
    d.process_record(record, processes, label)
    if label in ("node-version", "npm-version"):
        binary = label.split("-")[0]
        wanted = [binary, "--version"]
        cwd = snapshot
    elif label == "pi-completed":
        wanted = [
            "node",
            "--import",
            str(snapshot / o.RESOLVER),
            str(snapshot / DESTINATION),
            str(folder / "input.json"),
            str(folder),
        ]
        cwd = snapshot
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
        "Completed child identity drift",
    )
    stdout, stderr = (
        (processes / (label + ".stdout")).read_bytes(),
        (processes / (label + ".stderr")).read_bytes(),
    )
    if label in ("node-version", "npm-version"):
        d.require(
            stdout.decode().strip() == o.manifest()["runtime"][label.split("-")[0]]
            and not stderr,
            "Completed runtime receipt drift",
        )
    elif label == "invalid-cli":
        d.require(
            not stdout
            and stderr.startswith(b"INVALID:")
            and b"Traceback" not in stderr,
            "Completed invalid CLI diagnostics",
        )
    else:
        d.require(not stdout and not stderr, "Completed child diagnostics")


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
    }
    d.shape(
        value,
        set(expected)
        | {"execution_id", "module_sha256", "native_module_sha256", "artifacts_sha256"},
        "completed result",
    )
    for key, wanted in expected.items():
        d.require(d.same(value[key], wanted), "Completed result drift: " + key)
    d.require(
        str(uuid.UUID(value["execution_id"])) == value["execution_id"],
        "Completed execution ID",
    )
    files = artifact_hashes(folder)
    d.require(d.same(value["artifacts_sha256"], files), "Completed artifact drift")
    reference, actual = d.load(folder / "observed.json"), d.load(folder / "native.json")
    d.require(
        compare(reference, actual) == 0
        and d.same(d.load(folder / "input.json"), recipe()),
        "Completed actual pair/input drift",
    )
    for backend in ("memory", "sqlite", "reopen"):
        for case in recipe()["cases"]:
            raw = d.load(folder / "native-records" / backend / (case["id"] + ".json"))
            d.require(
                d.same(
                    v.encode(project(case, raw)),
                    next(
                        row["observed"]
                        for row in actual["cases"]
                        if row["id"] == case["id"]
                    ),
                ),
                "Completed actual decoded projection drift",
            )
    exact = trace(d.load(folder / "native-exact.json"))
    d.require(
        d.same(common_profile(exact), actual), "Completed numeric projection drift"
    )
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
        "Completed loaded modules drift",
    )
    d.require(
        d.same(value["native_module_sha256"], native_modules()),
        "Completed native producer drift",
    )
    results = {
        "node-version": 0,
        "npm-version": 0,
        "pi-completed": 0,
        "invalid-cli": 2,
    }
    for name in MUTANTS:
        mutant = d.load(folder / "mutants" / (name + ".json"))
        d.require(
            d.same(mutant, mutate(actual, name)) and compare(reference, mutant) == 1,
            "Completed mutant drift",
        )
        results[name + "-positive"] = 0
        results[name + "-negative"] = 1
    for label, code in results.items():
        process = d.load(folder / "processes" / (label + ".json"))
        process_identity(process, label, folder, snapshot)
        d.require(process["returncode"] == code, "Completed child exit drift")
    invalid = copy.deepcopy(actual)
    invalid["unknown"] = True
    retained = d.load(folder / "mutants" / "invalid.json")
    d.require(d.same(retained, invalid), "Completed invalid input was not retained")
    try:
        trace(retained)
    except o.EvidenceError:
        pass
    else:
        raise o.EvidenceError("Completed invalid producer became valid")
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
    args = parser.parse_args()
    try:
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
