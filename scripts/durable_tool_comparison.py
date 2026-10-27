# ruff: noqa: E402
"""DE08 actual pinned Harness tools versus native owned sequential execution.

Unsafe recovery deliberately compares the effect/replay prefix only: Pi emits
an interrupted result and continues; native blocks under the Milestone A rule.
Both complete raw outcomes remain retained. This is not full P20/P21/P22 parity.
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
    "tools_generation_parent", ROOT / "scripts/durable_generation_comparison.py"
)
assert spec and spec.loader
canonical = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canonical)
d, b, o, v = canonical.d, canonical.b, canonical.o, canonical.v
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import (
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
from pipy_harness.native.durable.generation import (
    USAGE_DOC,
    define_generation,
    message_entry,
)
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import EntryQuery, TaskQuery
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)

# Actual generation phases reach this pure history dependency lazily; fresh
# postexecution attestation imports the identical source root without effects.
from pipy_harness.native.durable.tool_history import ORDER_DOC  # noqa: E402,F401
from pipy_harness.native.http import JsonResponse
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.tools.base import ToolDefinition, ToolExecutionResult

PHASE = "tool-execution-b-v1"
MANIFEST = o.FIXTURES / "tool-execution-v1.manifest.json"
DRIVER = ROOT / "scripts/durable_reference/cases/tools.mts"
DESTINATION = "tools.mts"
MODULES = canonical.MODULES | {"packages/durable/src/harness/tool.ts"}
SOURCES = canonical.SOURCES | {
    "packages/durable/test/harness-tools-recovery.test.ts",
    "packages/durable/test/harness-tools.test.ts",
}
CLOSURE = canonical.CLOSURE | {
    "src/pipy_harness/native/durable/tool_progress.py",
    "src/pipy_harness/native/durable/tool_progress_values.py",
    "src/pipy_harness/native/durable/tool_output_values.py",
    "scripts/durable_tool_comparison.py",
    "scripts/durable_reference/cases/tools.mts",
    "tests/fixtures/durable/tools-input-v1.json",
    "tests/durable/test_tool_comparison.py",
    "tests/durable/test_tool_execution.py",
    "tests/durable/test_tool_boundaries.py",
    "tests/durable/test_tool_sqlite.py",
    "tests/durable/test_tool_lifecycle.py",
    "tests/durable/test_tool_faults.py",
    "tests/durable/tool_support.py",
    "docs/durable-tools.md",
}
CASES = ("loop", "safe-safe", "safe-unsafe", "unsafe-safe", "unsafe-unsafe")
MUTANTS = (
    "intersection",
    "preack",
    "prepared",
    "key",
    "doubleeffect",
    "doublereceipt",
    "output",
    "order",
    "cutoff",
    "parentearly",
    "latewrites",
    "lifetime",
    "usage",
    "usagepresence",
    "reuse",
    "request",
    "terminal",
)
NUMBER_PROFILE = "finite-binary64-live-v1"
COMPARISON_PROFILE = "owned-tool-prefix-and-closed-round-v1"
BACKENDS = {"loop": ["memory", "sqlite", "reopen"], "recovery": ["sqlite", "reopen"]}


def common_profile(value):
    return copy.deepcopy(value)


def recipe():
    value = d.load(o.FIXTURES / "tools-input-v1.json")
    d.require(
        value["schema_version"] == 1 and value["phase"] == PHASE, "Tool recipe identity"
    )
    d.require(
        [case["id"] for case in value["cases"]] == list(CASES), "Tool recipe cases"
    )
    return value


def usage_total(values):
    """Same presence-sensitive aggregate predicate in both producer languages."""
    result = {}
    for value in values:
        for key, item in value.items():
            if key == "cost":
                target = result.setdefault("cost", {})
                for name, amount in item.items():
                    target[name] = target.get(name, 0) + amount
            else:
                result[key] = result.get(key, 0) + item
    return result


def usage_matches(values, total):
    return v.encode(usage_total(values)) == v.encode(total)


class Wire:
    def __init__(self, case):
        self.case, self.calls = case, []

    def post_json(self, url, **kwargs):
        body = kwargs["body"]
        self.calls.append(body)
        names = self.case["names"] if len(self.calls) == 1 else []
        message = {"content": "done"}
        if names:
            message["tool_calls"] = [
                {
                    "id": "call:" + str(i),
                    "type": "function",
                    "function": {"name": name, "arguments": ' { "x":  1 } '},
                }
                for i, name in enumerate(names)
            ]
        usage = {
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "total_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 4},
        }
        if "reasoning" in self.case:
            usage["completion_tokens_details"] = {
                "reasoning_tokens": self.case["reasoning"]
            }
        return JsonResponse(
            200,
            {
                "choices": [
                    {
                        "message": message,
                        "finish_reason": "tool_calls" if names else "stop",
                    }
                ],
                "usage": usage,
            },
        )


class Tool:
    def __init__(self, name, state, session, held):
        self.definition = ToolDefinition(
            name,
            name,
            {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "prepared": {"type": "boolean"},
                },
            },
        )
        self.state, self.session, self.held = state, session, held

    def prepare_arguments(self, arguments):
        self.state["prepares"].append(self.definition.name)
        return {**arguments, "prepared": True}

    def invoke(self, request, context):
        # Observe actual acknowledged task intent independently of fixture args.
        rows = (
            self.session[0].commit(lambda tx: tx.scan_tasks(TaskQuery({}), 100)).items
        )
        task = next(
            row.to_tree()
            for row in rows
            if row.to_tree().get("memos", {}).get("pi.tool.call-key")
            == context.call_key
        )
        checkpoint = task["state"].get("checkpoint", {})
        before = (
            checkpoint.get("phase") == "execute"
            and checkpoint.get("intent", {}).get("arguments") == request.arguments
        )
        call = {
            "name": request.tool_name,
            "key": context.call_key,
            "args": copy.deepcopy(request.arguments),
            "intent": before,
            "task": task["id"],
            "ended": False,
        }
        self.state["calls"].append(call)
        old = self.state["applications"].get(context.call_key)
        d.require(old is None or old == request.arguments, "Fixture key collision")
        self.state["applications"][context.call_key] = copy.deepcopy(request.arguments)
        try:
            if self.held[0]:
                self.held[0] = False
                self.held[1].set()
                d.require(
                    context.cancel_event.wait(15), "Fixture cancellation not delivered"
                )
                self.held[2].set()
            return ToolExecutionResult(
                request.tool_request_id,
                "actual:" + request.tool_name,
                provider_correlation_id=request.provider_correlation_id,
                details={"key": context.call_key},
                structured_content=None,
            )
        finally:
            call["ended"] = True


async def idle(session, scheduler):
    done = threading.Event()

    async def install():
        original = scheduler._reserve

        async def observed():
            result = await original()
            if result is None:
                done.set()
            return result

        scheduler._reserve = observed
        return original

    original = await asyncio.wrap_future(
        asyncio.run_coroutine_threadsafe(install(), scheduler._loop)
    )
    try:
        scheduler.resume()
        d.require(
            await asyncio.to_thread(done.wait, 15), "Tool scheduler did not drain"
        )
    finally:

        async def restore():
            scheduler._reserve = original

        await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(restore(), scheduler._loop)
        )


def project(case, raw):
    calls = raw["effects"]["calls"]
    values = [
        canonical.project_usage(decode_canonical(e["model"][0]).usage)
        for e in raw["entries"]
        if e["kind"] == "pi.assistant"
    ]
    total = canonical.project_ledger_usage(raw["ledger"]["models"]["openai/gpt-test"])
    closed = case["id"] == "loop" or case["stored"] == case["current"] == "safe"
    value = {
        "calls": len(calls),
        "applications": len(raw["effects"]["applications"]),
        "prepares": len(raw["effects"]["prepares"]),
        "arguments": [call["args"] for call in calls],
        "intent_before_effect": all(call["intent"] for call in calls),
        "same_key": all(
            a["task"] != b["task"] or (a["key"] == b["key"] and a["args"] == b["args"])
            for a in calls
            for b in calls
        ),
        "distinct_keys": len({call["key"] for call in calls})
        == len({call["task"] for call in calls}),
        "dispatch": [call["name"] for call in calls],
        "worker_joined": raw["lifecycle"]["worker_joined"],
        "post_close_calls": raw["lifecycle"]["post_close_calls"],
        "reuse_calls": raw["lifecycle"]["reuse_calls"],
    }
    # This profile does not assert status/continued-turn equivalence on unsafe
    # recovery. Their exact distinct outcomes remain in raw records.
    if closed:
        messages = (
            []
            if not raw["entries"]
            else [
                decode_canonical(e["data"]["request"]).request.messages
                for e in raw["entries"]
                if e["kind"] == "pi.assistant"
            ][-1]
        )
        results = [m for m in messages if type(m) is AgentToolResultMessage]
        receipts = [e for e in raw["entries"] if e["kind"] == "pi.tool-result"]
        parent = next(row for row in raw["tasks"] if row["kind"] == "pi.generation")
        assistants = [
            entry for entry in raw["entries"] if entry["kind"] == "pi.assistant"
        ]
        identities = [entry["data"]["receipt"]["identity"] for entry in assistants]
        turns = [
            decode_canonical(entry["data"]["request"]).request.provider_turn_index
            for entry in assistants
        ]
        value["closed"] = {
            "fresh_round_identity": len({item["request"] for item in identities})
            == len(identities)
            and len({item["attempt"] for item in identities}) == len(identities)
            and all(
                later == earlier + 1
                for earlier, later in zip(turns, turns[1:], strict=False)
            ),
            "provider_calls": len(raw["wire"]),
            "result_names": [m.tool_name for m in results],
            "result_text": [
                m.content.value for m in results if m.tool_name != "unavailable"
            ],
            "receipts": len(receipts),
            "terminal": parent["state"]["status"] == "terminal"
            and parent["state"]["outcome"]["status"] == "completed",
            "usage": total,
            "usage_once": usage_matches(values, total),
            "parent_after_results": len(results) == len(case["names"]),
            "cutoff": raw["cutoffs_valid"],
            "retained_terminal": raw["lifecycle"]["retained_terminal"],
        }
    return value


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
    wire = Wire(case)
    effects = {"calls": [], "applications": {}, "prepares": []}
    held = [case["id"] != "loop", threading.Event(), threading.Event()]
    ports = [Tool(name, effects, owner, held) for name in ("keyed", "later")]

    def definitions(policy, missing=False):
        generation = define_generation(
            NativeModelSpec("openai", "gpt-test", "Test", "openai-completions"),
            auth_path=folder / "auth.json",
            env={},
            runtime_api_key="fixture-key",
            pricing=AgentTokenPricing(1, 2, 3, 4),
            http_client=wire,
        )
        tool = define_tool_task(
            {}
            if missing
            else {
                p.definition.name: ToolRegistration(
                    p, "fixture." + p.definition.name, "1", policy
                )
                for p in ports
            },
            workspace_root=folder,
        )
        return generation, tool

    generation, tool = definitions(case["stored"])
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
    publications = []

    def observe(value):
        publications.append(
            [x.to_tree() for x in value.changes if hasattr(x, "to_tree")]
        )

    def scheduler_for(current, policy, missing=False):
        owner[0] = current
        current.subscribe_commits(observe)
        return TaskScheduler(current, list(definitions(policy, missing)))

    try:
        if backend == "reopen":
            await session.aclose()
            session = Session(
                open_sqlite_storage(str(path), mode="open"), now=lambda: 1000
            )
        scheduler = scheduler_for(session, case["stored"])
        await scheduler.aopen()
        if case["id"] != "loop":
            scheduler.resume()
            d.require(
                await asyncio.to_thread(held[1].wait, 15),
                "Tool did not reach effect cut",
            )
            await session.aclose()
            d.require(
                held[2].is_set() and not scheduler._thread.is_alive(),
                "Tool worker detached",
            )
            effects_before = copy.deepcopy(effects)
            session = Session(
                open_sqlite_storage(str(path), mode="open"), now=lambda: 1000
            )
            scheduler = scheduler_for(session, case["current"])
            await scheduler.aopen()
            d.require(effects_before == effects, "Open dispatched tool")
        await idle(session, scheduler)
        tasks = [r.to_tree() for r in scheduler.inspect()]
        entries = sorted(
            [
                e.to_tree()
                for e in (
                    await session.acommit(
                        lambda tx: tx.scan_entries(
                            EntryQuery({"conversationId": cid}), 100
                        )
                    )
                ).items
            ],
            key=lambda e: e["id"],
        )
        ledger = (await session.asnapshot(USAGE_DOC, conversation_id=cid)).value.thaw()
        closed = case["id"] == "loop" or case["stored"] == case["current"] == "safe"
        parent_record = next(row for row in tasks if row["id"] == parent)
        if not closed:
            child = next(row for row in tasks if row["kind"] == "pi.tool")
            d.require(
                child["state"]["status"] == "pending"
                and child["state"]["checkpoint"]["phase"] == "blocked"
                and parent_record["state"]["status"] == "waiting"
                and len(wire.calls) == 1,
                "Unsafe recovery did not block",
            )
        lifecycle = {
            "worker_joined": all(call["ended"] for call in effects["calls"]),
            "post_close_calls": 0,
            "reuse_calls": 0,
            "retained_terminal": None,
        }
        if backend == "memory":
            before_reuse = len(effects["calls"])
            await idle(session, scheduler)
            retained = next(r.to_tree() for r in scheduler.inspect() if r.id == parent)
            lifecycle["retained_terminal"] = d.same(
                retained["state"], parent_record["state"]
            )
            lifecycle["reuse_calls"] = len(effects["calls"]) - before_reuse
        before_close = len(effects["calls"])
        await session.aclose()
        d.require(not scheduler._thread.is_alive(), "Tool host detached")
        lifecycle["worker_joined"] = (
            lifecycle["worker_joined"] and not scheduler._thread.is_alive()
        )
        lifecycle["post_close_calls"] = len(effects["calls"]) - before_close
        if backend != "memory":
            session = Session(
                open_sqlite_storage(str(path), mode="open"), now=lambda: 1000
            )
            replay = scheduler_for(session, case["current"], missing=True)
            await replay.aopen()
            await idle(session, replay)
            retained = next(r.to_tree() for r in replay.inspect() if r.id == parent)
            lifecycle["retained_terminal"] = (
                d.same(retained["state"], parent_record["state"]) if closed else None
            )
            lifecycle["reuse_calls"] = len(effects["calls"]) - before_close
            await session.aclose()
        cutoffs_valid = all(
            e["data"]["receipt"]["identity"]["cutoff"]
            == max(x["id"] for x in entries if x["id"] < e["id"])
            for e in entries
            if e["kind"] == "pi.assistant"
        )
        raw = {
            "tasks": tasks,
            "entries": entries,
            "wire": wire.calls,
            "effects": effects,
            "ledger": ledger,
            "publications": publications,
            "lifecycle": lifecycle,
            "cutoffs_valid": cutoffs_valid,
        }
        target = folder.parents[2] / "native-records" / backend / (case["id"] + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        d.dump(target, raw)
        return {"id": case["id"], "observed": v.encode(project(case, raw))}
    finally:
        await session.aclose()


async def native(folder):
    rows = [
        await native_case(case, "sqlite", folder / "sqlite" / case["id"])
        for case in recipe()["cases"]
    ]
    for case, wanted in zip(recipe()["cases"], rows, strict=True):
        actual = await native_case(case, "reopen", folder / "reopen" / case["id"])
        d.require(d.same(actual, wanted), "Tool recovery backend mismatch")
    memory = await native_case(
        recipe()["cases"][0], "memory", folder / "memory" / "loop"
    )
    d.require(d.same(memory, rows[0]), "Memory tool loop mismatch")
    value = {"schema_version": 1, "phase": PHASE, "cases": rows}
    d.dump(folder.parent / "native-exact.json", value)
    return common_profile(value)


def trace(value):
    d.shape(value, {"schema_version", "phase", "cases"}, "Tool trace")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Tool trace identity",
    )
    d.require(
        type(value["cases"]) is list
        and [row.get("id") for row in value["cases"] if type(row) is dict]
        == list(CASES),
        "Tool trace cases",
    )
    for row, case in zip(value["cases"], recipe()["cases"], strict=True):
        d.shape(row, {"id", "observed"}, "Tool row")
        observation = v.decode(row["observed"])
        d.shape(
            observation,
            {
                "calls",
                "applications",
                "prepares",
                "arguments",
                "intent_before_effect",
                "same_key",
                "distinct_keys",
                "dispatch",
                "worker_joined",
                "post_close_calls",
                "reuse_calls",
            }
            | (
                {"closed"}
                if case["id"] == "loop" or case["stored"] == case["current"] == "safe"
                else set()
            ),
            "Tool observation",
        )
        d.require(v.encode(observation) == row["observed"], "Tool value profile")
    return value


def compare(expected, actual):
    trace(expected)
    trace(actual)
    return int(not d.same(expected, actual))


def mutate(actual, name):
    changed = copy.deepcopy(actual)
    row = changed["cases"][1 if name == "intersection" else 0]
    value = v.decode(row["observed"])
    paths = {
        "intersection": ("calls",),
        "preack": ("intent_before_effect",),
        "prepared": ("arguments", 0, "prepared"),
        "key": ("same_key",),
        "doubleeffect": ("applications",),
        "doublereceipt": ("closed", "receipts"),
        "output": ("closed", "result_text", 0),
        "order": ("closed", "result_names"),
        "cutoff": ("closed", "cutoff"),
        "parentearly": ("closed", "parent_after_results"),
        "latewrites": ("post_close_calls",),
        "lifetime": ("worker_joined",),
        "usage": ("closed", "usage", "input"),
        "reuse": ("reuse_calls",),
        "request": ("closed", "fresh_round_identity"),
        "terminal": ("closed", "terminal"),
    }
    if name == "usagepresence":
        value["closed"]["usage"]["cacheWrite1h"] = 0
    else:
        path = paths[name]
        target = value
        for key in path[:-1]:
            target = target[key]
        old = target[path[-1]]
        target[path[-1]] = (
            not old
            if type(old) is bool
            else list(reversed(old))
            if name == "order"
            else old + 1
            if type(old) in (int, float)
            else "different"
        )
    row["observed"] = v.encode(value)
    return changed


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
        "tool manifest",
    )
    d.require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN,
        "Tool manifest identity",
    )
    for key, names in (("closure", CLOSURE), ("reference_sources", SOURCES)):
        d.shape(config[key], names, "tool " + key)
        for name, digest in config[key].items():
            o.contract.digest(digest)
            if key == "closure":
                d.require(o.sha(ROOT / name) == digest, "Tool closure drift")
    d.require(
        config["parents"] == {"generation": o.sha(canonical.MANIFEST)},
        "Tool parent drift",
    )
    canonical.configuration()
    return config


def native_modules():
    modules = {}
    for name, module in tuple(sys.modules.items()):
        if name != "pipy_harness" and not name.startswith("pipy_harness."):
            continue
        filename = getattr(module, "__file__", None)
        d.require(filename is not None, "Tool producer lacks a source file")
        path = Path(filename).resolve()
        d.require(path.is_relative_to(ROOT), "Tool producer outside checkout")
        relative = str(path.relative_to(ROOT))
        d.require(relative in CLOSURE, "Tool producer outside closure")
        modules[relative] = o.sha(path)
    return modules


def execute(acceptance, output):
    d.require(
        not output.exists() and not output.is_symlink(),
        "Tool output exists",
    )
    acceptance, output = acceptance.resolve(strict=True), output.absolute()
    d.require(
        not output.resolve().is_relative_to(acceptance),
        "Tool output inside parent",
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
        "Tool driver exists",
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
            "Tool runtime drift",
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
        "pi-tool",
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
            d.require(code == wanted, "Tool semantic CLI control failed")
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
    d.require(code == 2, "Tool invalid CLI control failed")
    configuration()
    o.verify_sources(snapshot, config["reference_sources"])
    o.acceptance.attest_reference(o, acceptance / "reference", parent, o.FIXTURES)
    v.artifacts.attest(acceptance, o.FIXTURES)
    d.require(
        manifest_sha == o.sha(MANIFEST) and o.sha(driver) == o.sha(DRIVER),
        "Tool producer drift",
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
    elif label == "pi-tool":
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
        "Tool child identity drift",
    )
    stdout, stderr = (
        (processes / (label + ".stdout")).read_bytes(),
        (processes / (label + ".stderr")).read_bytes(),
    )
    if label in ("node-version", "npm-version"):
        d.require(
            stdout.decode().strip() == o.manifest()["runtime"][label.split("-")[0]]
            and not stderr,
            "Tool runtime receipt drift",
        )
    elif label == "invalid-cli":
        d.require(
            not stdout
            and stderr.startswith(b"INVALID:")
            and b"Traceback" not in stderr,
            "Tool invalid CLI diagnostics",
        )
    else:
        d.require(not stdout and not stderr, "Tool child diagnostics")


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
        "tool result",
    )
    for key, wanted in expected.items():
        d.require(d.same(value[key], wanted), "Tool result drift: " + key)
    d.require(
        str(uuid.UUID(value["execution_id"])) == value["execution_id"],
        "Tool execution ID",
    )
    files = artifact_hashes(folder)
    d.require(d.same(value["artifacts_sha256"], files), "Tool artifact drift")
    reference, actual = d.load(folder / "observed.json"), d.load(folder / "native.json")
    d.require(
        compare(reference, actual) == 0
        and d.same(d.load(folder / "input.json"), recipe()),
        "Tool actual pair/input drift",
    )
    for backend in ("sqlite", "reopen"):
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
                "Tool actual decoded projection drift",
            )
    exact = trace(d.load(folder / "native-exact.json"))
    d.require(d.same(common_profile(exact), actual), "Tool numeric projection drift")
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
        "Tool loaded modules drift",
    )
    d.require(
        d.same(value["native_module_sha256"], native_modules()),
        "Tool native producer drift",
    )
    results = {
        "node-version": 0,
        "npm-version": 0,
        "pi-tool": 0,
        "invalid-cli": 2,
    }
    for name in MUTANTS:
        mutant = d.load(folder / "mutants" / (name + ".json"))
        d.require(
            d.same(mutant, mutate(actual, name)) and compare(reference, mutant) == 1,
            "Tool mutant drift",
        )
        results[name + "-positive"] = 0
        results[name + "-negative"] = 1
    for label, code in results.items():
        process = d.load(folder / "processes" / (label + ".json"))
        process_identity(process, label, folder, snapshot)
        d.require(process["returncode"] == code, "Tool child exit drift")
    invalid = copy.deepcopy(actual)
    invalid["unknown"] = True
    retained = d.load(folder / "mutants" / "invalid.json")
    d.require(d.same(retained, invalid), "Tool invalid input was not retained")
    try:
        trace(retained)
    except o.EvidenceError:
        pass
    else:
        raise o.EvidenceError("Tool invalid producer became valid")
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
