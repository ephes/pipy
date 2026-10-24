# ruff: noqa: E402
"""DE07 actual pinned Harness generation versus native completed settlement.

The paired observable is a declared language projection, not Pi's private arena
or provider wire protocol. Native privacy/reconstruction has separate evidence.
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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


r = load_module(
    "checkpoint_runtime_reference", ROOT / "scripts/durable_task_runtime_comparison.py"
)
canonical = load_module(
    "generation_parent_reference", ROOT / "scripts/durable_checkpoint_comparison.py"
)
d, b, o, v = r.d, r.b, r.o, r.v
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import AgentUserMessage
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.agent.usage import AgentTokenPricing  # noqa:E402
from pipy_harness.native.catalog import NativeModelSpec  # noqa:E402
from pipy_harness.native.durable.canonical_codec import (
    decode_canonical,
    encode_canonical,
)
from pipy_harness.native.durable.generation import (
    PROVIDER_DOC,
    USAGE_DOC,
    define_generation,
    message_entry,
)
from pipy_harness.native.durable.memory import MemoryStorage  # noqa:E402
from pipy_harness.native.durable.scheduler import TaskScheduler  # noqa:E402
from pipy_harness.native.durable.session_core import Session  # noqa:E402
from pipy_harness.native.durable.sqlite import open_sqlite_storage  # noqa:E402
from pipy_harness.native.durable.storage import EntryQuery

# Actual generation phases reach this pure history dependency lazily; fresh
# postexecution attestation imports the identical source root without effects.
from pipy_harness.native.durable.tool_history import ORDER_DOC  # noqa: E402,F401
from pipy_harness.native.http import JsonResponse
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.tools.base import ToolDefinition

PHASE = "generation-b-v1"
MANIFEST = o.FIXTURES / "generation-v1.manifest.json"
DRIVER = ROOT / "scripts/durable_reference/cases/generation.mts"
DESTINATION = "generation.mts"
MODULES = (canonical.MODULES - {"packages/durable/src/storage/memory.ts"}) | {
    "packages/durable/src/storage/sqlite/node.ts",
    "packages/durable/src/storage/sqlite/storage.ts",
}
SOURCES = canonical.SOURCES | {
    "packages/ai/src/api/openai-codex-responses.ts",
    "packages/ai/src/api/azure-openai-config.ts",
    "packages/ai/src/api/openai-responses.ts",
    "packages/ai/src/api/anthropic-messages.ts",
    "packages/ai/src/api/bedrock-converse-stream.ts",
    "packages/ai/src/utils/provider-env.ts",
    "packages/durable/src/storage/sqlite/node.ts",
    "packages/durable/src/storage/sqlite/storage.ts",
    "packages/durable/src/harness/generation.ts",
    "packages/durable/src/harness/tool.ts",
    "packages/durable/src/harness/types.ts",
    "packages/durable/src/harness/provider.ts",
    "packages/durable/src/harness/usage.ts",
    "packages/durable/src/harness/agent.ts",
    "packages/durable/test/harness-generation.test.ts",
    "packages/durable/test/harness-generation-recovery.test.ts",
    "packages/ai/src/providers/faux.ts",
}
CLOSURE = (
    canonical.CLOSURE
    | {str(p.relative_to(ROOT)) for p in (ROOT / "src").rglob("*.py")}
    | {
        "scripts/durable_generation_comparison.py",
        "scripts/durable_reference/canonical_payloads.py",
        "scripts/durable_reference/cases/generation.mts",
        "tests/durable/test_generation_comparison.py",
        "tests/durable/test_generation.py",
        "tests/durable/test_generation_lifecycle.py",
        "tests/durable/test_generation_sqlite.py",
        "tests/durable/test_generation_faults.py",
        "tests/durable/generation_support.py",
        "docs/durable-generation.md",
        "tests/durable/test_checkpoint_codec.py",
        "tests/durable/test_checkpoint_sqlite.py",
        "tests/durable/test_provider_checkpoint.py",
        "tests/durable/test_auth_material.py",
        "docs/durable-canonical-codec.md",
        "tests/fixtures/durable/generation-input-v1.json",
        "CHANGELOG.md",
        "docs/backlog.md",
        "docs/harness-spec.md",
        "docs/architecture.md",
        "docs/providers.md",
        "docs/durable-reference.md",
        "docs/specs/2026-10-19-durable-execution-plan.md",
        "docs/specs/2026-10-19-durable-execution-evidence.md",
        "docs/specs/2026-10-19-durable-upstream-audit.md",
        "tests/test_native_session_extension_generation.py",
        "tests/test_native_auth_store.py",
        "tests/test_native_provider_construction.py",
        "tests/test_native_openai_codex_provider.py",
        "tests/test_native_bedrock_provider.py",
        "tests/test_native_anthropic_provider.py",
        "tests/test_native_anthropic_prompt_cache.py",
        "tests/test_native_azure_openai_provider.py",
        "tests/test_native_azure_openai_responses_fixtures.py",
        "tests/test_native_openai_responses_fixtures.py",
        "tests/test_native_google_vertex_provider.py",
        "tests/conftest.py",
        "scripts/durable_reference_acceptance.py",
        "tests/durable/test_pi_comparison_coverage.py",
        "tests/fixtures/durable/coverage.json",
    }
)
CASES = ("answer", "tools")
MUTANTS = (
    "cutoff",
    "session",
    "admissions",
    "owner",
    "usage",
    "pricing",
    "request",
    "publication",
    "close",
    "reuse",
)

NUMBER_PROFILE = "finite-binary64-live-v1"


def common_profile(value):
    return copy.deepcopy(value)


def recipe():
    value = d.load(o.FIXTURES / "generation-input-v1.json")
    d.require(
        value["schema_version"] == 1 and value["phase"] == PHASE,
        "Generation recipe identity",
    )
    d.require(
        [case["id"] for case in value["cases"]] == list(CASES),
        "Generation recipe cases",
    )
    return value


class Wire:
    def __init__(self, case):
        self.case, self.calls = case, []

    def post_json(self, url, **kwargs):
        self.calls.append(kwargs["body"])
        message = {"content": "done"}
        if self.case["tools"]:
            message["tool_calls"] = [
                {
                    "id": f"call:{index}",
                    "type": "function",
                    "function": {"name": name, "arguments": ' { "x":  1 } '},
                }
                for index, name in enumerate(("read", "read", "unavailable"))
            ]
        return JsonResponse(
            200,
            {
                "choices": [
                    {
                        "message": message,
                        "finish_reason": "tool_calls" if self.case["tools"] else "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                    "prompt_tokens_details": {"cached_tokens": 4},
                    "completion_tokens_details": {"reasoning_tokens": 3},
                },
            },
        )


def project_usage(usage):
    result = {
        "input": usage.input,
        "output": usage.output,
        "cacheRead": usage.cache_read,
        "cacheWrite": usage.cache_write,
        "totalTokens": usage.total_tokens,
        "cost": {
            "input": usage.cost.input,
            "output": usage.cost.output,
            "cacheRead": usage.cost.cache_read,
            "cacheWrite": usage.cost.cache_write,
            "total": usage.cost.total,
        },
    }
    if usage.reasoning is not None:
        result["reasoning"] = usage.reasoning
    if usage.cache_write_1h is not None:
        result["cacheWrite1h"] = usage.cache_write_1h
    return result


def project_ledger_usage(usage):
    renamed = {
        "input": "input",
        "output": "output",
        "cacheRead": "cache_read",
        "cacheWrite": "cache_write",
        "totalTokens": "total_tokens",
        "reasoning": "reasoning",
        "cacheWrite1h": "cache_write_1h",
    }
    result = {
        public: usage[native] for public, native in renamed.items() if native in usage
    }
    result["cost"] = {
        public: usage["cost"][native]
        for public, native in {
            "input": "input",
            "output": "output",
            "cacheRead": "cache_read",
            "cacheWrite": "cache_write",
            "total": "total",
        }.items()
    }
    return result


def usage_matches(assistant_usage, model_total):
    return v.encode(project_ledger_usage(model_total)) == v.encode(
        project_usage(assistant_usage)
    )


async def resume_terminal(session, scheduler, task_id, expected):
    """Call resume and observe a real completed post-terminal empty reservation.

    A fresh owner cannot drain before resume. On the live Memory owner, an
    already pending terminal notification may also cause the observed drain.

    Installation/removal and every method read stay on the scheduler host. The
    wrapper awaits the unchanged real reservation, including Session settlement;
    it never invents a reservation result or runs a second drain concurrently.
    Only threading.Event crosses back to this caller's loop.
    """
    drained = threading.Event()

    async def install():
        original = scheduler._reserve

        async def observed():
            result = await original()
            if result is None:
                drained.set()
            return result

        scheduler._reserve = observed
        return original

    original = await asyncio.wrap_future(
        asyncio.run_coroutine_threadsafe(install(), scheduler._loop)
    )
    try:
        scheduler.resume()
        d.require(
            await asyncio.to_thread(drained.wait, 10), "Terminal resume never drained"
        )
        retained = await session.acommit(lambda tx: tx.task(task_id))
        return d.same(retained.to_tree()["state"], expected)
    finally:

        async def restore():
            scheduler._reserve = original

        await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(restore(), scheduler._loop)
        )


async def native_case(case, backend, folder):
    folder.mkdir(parents=True)
    path = folder / "session.sqlite"
    session = Session(
        MemoryStorage()
        if backend == "memory"
        else open_sqlite_storage(str(path), mode="create"),
        now=lambda: 1000,
    )
    try:
        wire = Wire(case)
        task = define_generation(
            NativeModelSpec("openai", "gpt-test", "Test", "openai-completions"),
            auth_path=folder / "auth.json",
            env={},
            runtime_api_key="fixture-key",
            pricing=AgentTokenPricing(1, 2, 3, 4),
            http_client=wire,
        )
        seed = snapshot_provider_request(
            ProviderRequest(
                "system",
                "input",
                "openai",
                "gpt-test",
                folder,
                available_tools=(ToolDefinition("read", "Read", {"type": "object"}),),
            )
        )
        conversation = (
            await session.acommit(
                lambda tx: tx.create_conversation({"kind": "ownerless"})
            )
        ).id
        source = await session.acommit(
            lambda tx: tx.append_entry(
                conversation, message_entry(AgentUserMessage(ProductContent("input")))
            )
        )
        task_id = await session.acommit(
            lambda tx: tx.create_task(
                task,
                encode_canonical(seed),
                ownership={"kind": "conversation"},
                conversation_id=conversation,
            )
        )
        if backend == "reopen":
            await session.aclose()
            session = Session(
                open_sqlite_storage(str(path), mode="open"), now=lambda: 1000
            )
        scheduler = TaskScheduler(session, [task])
        await scheduler.aopen()
        before_calls = len(wire.calls)
        done = threading.Event()
        publication = []

        def observe(value):
            for change in value.changes:
                if hasattr(change, "to_tree"):
                    tree = change.to_tree()
                    if (
                        tree["type"] == "task"
                        and tree["value"]["id"] == task_id
                        and tree["value"]["state"]["status"] in ("terminal", "waiting")
                    ):
                        publication.append(
                            [
                                x.to_tree()
                                for x in value.changes
                                if hasattr(x, "to_tree")
                            ]
                        )
                        done.set()

        session.subscribe_commits(observe)
        scheduler.resume()
        d.require(await asyncio.to_thread(done.wait, 10), "Generation did not settle")
        tasks = [row.to_tree() for row in scheduler.inspect()]
        parent = next(row for row in tasks if row["id"] == task_id)
        page = await session.acommit(
            lambda tx: tx.scan_entries(
                EntryQuery({"conversationId": conversation}), 100
            )
        )
        entries = [entry.to_tree() for entry in page.items]
        answer = next(entry for entry in entries if entry["kind"] == "pi.assistant")
        assistant = decode_canonical(answer["model"][0])
        request = decode_canonical(answer["data"]["request"])
        provider = (
            await session.asnapshot(PROVIDER_DOC, conversation_id=conversation)
        ).value.thaw()
        ledger = (
            await session.asnapshot(USAGE_DOC, conversation_id=conversation)
        ).value.thaw()
        children = [row for row in tasks if row["kind"] == "pi.tool"]
        unavailable = [
            decode_canonical(entry["model"][0])
            for entry in entries
            if entry["kind"] == "pi.tool-result"
        ]
        published_entries = [
            change["value"]["id"]
            for change in publication[0]
            if change["type"] == "entry"
        ]
        published_tasks = [
            change["value"]["id"]
            for change in publication[0]
            if change["type"] == "task"
        ]
        raw = {
            "tasks": tasks,
            "entries": entries,
            "wire": wire.calls,
            "ledger": ledger,
            "publication": publication,
        }
        raw_path = (
            folder.parents[2] / "native-records" / backend / (case["id"] + ".json")
        )
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "before_calls": before_calls,
            "request_calls": len(wire.calls),
            "provider": {
                "provider": request.request.provider_name,
                "model": request.request.model_id,
                "api": answer["data"]["receipt"]["settings"]["api"],
                "session": bool(provider["sessionId"])
                and provider["sessionId"] == request.request.session_id,
                "cutoff": answer["data"]["receipt"]["identity"]["cutoff"] == source.id,
            },
            "assistant": {
                "text": assistant.content.value,
                "calls": [
                    {
                        "id": call.provider_correlation_id,
                        "name": call.tool_name,
                        "arguments": json.loads(call.arguments_json.value),
                    }
                    for call in assistant.tool_calls
                ],
            },
            "usage": project_usage(assistant.usage),
            "ledger_usage": project_ledger_usage(ledger["models"]["openai/gpt-test"]),
            "admission": {
                "waiting": parent["state"]["status"] == "waiting",
                "children": [
                    {
                        "kind": row["kind"],
                        "version": row["version"],
                        "owner": row.get("owner") == task_id,
                        "conversation": row["conversationId"] == conversation,
                        "assistant": row["input"]["assistant"] == answer["id"],
                        "callId": row["input"]["callId"],
                    }
                    for row in children
                ],
                "unavailable": [
                    {
                        "callId": result.provider_correlation_id,
                        "isError": result.is_error,
                    }
                    for result in unavailable
                ],
            },
            "publication": answer["id"] in published_entries
            and all(row["id"] in published_tasks for row in children),
            "usage_once": usage_matches(
                assistant.usage, ledger["models"]["openai/gpt-test"]
            ),
        }
        value["resumed_after_terminal"] = not case["tools"]
        value["retained_terminal"] = None
        if backend == "memory" and not case["tools"]:
            value["retained_terminal"] = await resume_terminal(
                session, scheduler, task_id, parent["state"]
            )
        await session.aclose()
        value["close_reason"] = session.end.reason
        if backend != "memory":
            again = Session(open_sqlite_storage(str(path), mode="open"))
            try:
                replay = TaskScheduler(again, [task])
                await replay.aopen()
                if not case["tools"]:
                    value["retained_terminal"] = await resume_terminal(
                        again, replay, task_id, parent["state"]
                    )
            finally:
                await again.aclose()
            d.require(
                again.end.reason == "closed",
                "Generation reopened owner failed to close",
            )
        value["retained_calls"] = len(wire.calls)
        raw["lifecycle"] = {
            key: value[key]
            for key in (
                "resumed_after_terminal",
                "retained_terminal",
                "close_reason",
                "retained_calls",
            )
        }
        d.dump(raw_path, raw)
        return {"id": case["id"], "observed": v.encode(value)}
    finally:
        await session.aclose()


async def native(folder):
    outputs = []
    for backend in ("memory", "sqlite", "reopen"):
        rows = [
            await native_case(case, backend, folder / backend / case["id"])
            for case in recipe()["cases"]
        ]
        outputs.append({"schema_version": 1, "phase": PHASE, "cases": rows})
    d.require(
        all(compare(outputs[0], row) == 0 for row in outputs[1:]),
        "Generation native backend mismatch",
    )
    d.dump(folder.parent / "native-exact.json", outputs[0])
    return common_profile(outputs[0])


def trace(value):
    d.shape(value, {"schema_version", "phase", "cases"}, "generation trace")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Generation identity",
    )
    rows = value["cases"]
    d.require(
        type(rows) is list
        and [r.get("id") for r in rows if type(r) is dict] == list(CASES),
        "Generation cases",
    )
    for row in rows:
        d.shape(row, {"id", "observed"}, "Generation row")
        decoded = v.decode(row["observed"])
        d.shape(
            decoded,
            {
                "before_calls",
                "request_calls",
                "provider",
                "assistant",
                "usage",
                "ledger_usage",
                "admission",
                "publication",
                "usage_once",
                "close_reason",
                "resumed_after_terminal",
                "retained_terminal",
                "retained_calls",
            },
            "Generation observation",
        )
        d.require(v.encode(decoded) == row["observed"], "Generation value profile")
    return value


def compare(expected, actual):
    trace(expected)
    trace(actual)
    return int(not d.same(expected, actual))


def mutate(actual, name):
    changed = copy.deepcopy(actual)
    row = changed["cases"][1 if name in ("admissions", "owner") else 0]
    value = v.decode(row["observed"])
    paths = {
        "cutoff": ("provider", "cutoff"),
        "session": ("provider", "session"),
        "owner": ("admission", "children", 0, "owner"),
        "usage": ("ledger_usage", "input"),
        "pricing": ("ledger_usage", "cost", "total"),
        "request": ("provider", "model"),
        "publication": ("publication",),
        "close": ("close_reason",),
        "reuse": ("retained_calls",),
    }
    if name == "admissions":
        value["admission"]["children"].pop()
    else:
        path = paths[name]
        target = value
        for part in path[:-1]:
            target = target[part]
        old = target[path[-1]]
        target[path[-1]] = (
            not old
            if type(old) is bool
            else old * 2
            if name == "usage"
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
        "generation manifest",
    )
    d.require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN,
        "Generation manifest identity",
    )
    for key, names in (("closure", CLOSURE), ("reference_sources", SOURCES)):
        d.shape(config[key], names, "generation " + key)
        for name, digest in config[key].items():
            o.contract.digest(digest)
            if key == "closure":
                d.require(o.sha(ROOT / name) == digest, "Generation closure drift")
    d.require(
        config["parents"] == {"canonical-checkpoint": o.sha(canonical.MANIFEST)},
        "Generation parent drift",
    )
    canonical.configuration()
    return config


def native_modules():
    modules = {}
    for name, module in tuple(sys.modules.items()):
        if name != "pipy_harness" and not name.startswith("pipy_harness."):
            continue
        filename = getattr(module, "__file__", None)
        d.require(filename is not None, "Generation producer lacks a source file")
        path = Path(filename).resolve()
        d.require(path.is_relative_to(ROOT), "Generation producer outside checkout")
        relative = str(path.relative_to(ROOT))
        d.require(relative in CLOSURE, "Generation producer outside closure")
        modules[relative] = o.sha(path)
    return modules


def execute(acceptance, output):
    d.require(
        not output.exists() and not output.is_symlink(),
        "Generation output exists",
    )
    acceptance, output = acceptance.resolve(strict=True), output.absolute()
    d.require(
        not output.resolve().is_relative_to(acceptance),
        "Generation output inside parent",
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
        "Generation driver exists",
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
            "Generation runtime drift",
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
        "pi-generation",
        env,
        120,
    )
    d.require(
        code == 0 and not stdout and not stderr,
        "Pinned generation driver failed",
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
            d.require(code == wanted, "Generation semantic CLI control failed")
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
    d.require(code == 2, "Generation invalid CLI control failed")
    configuration()
    o.verify_sources(snapshot, config["reference_sources"])
    o.acceptance.attest_reference(o, acceptance / "reference", parent, o.FIXTURES)
    v.artifacts.attest(acceptance, o.FIXTURES)
    d.require(
        manifest_sha == o.sha(MANIFEST) and o.sha(driver) == o.sha(DRIVER),
        "Generation producer drift",
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
            "native_backends": ["memory", "sqlite", "reopen"],
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
    elif label == "pi-generation":
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
        "Generation child identity drift",
    )
    stdout, stderr = (
        (processes / (label + ".stdout")).read_bytes(),
        (processes / (label + ".stderr")).read_bytes(),
    )
    if label in ("node-version", "npm-version"):
        d.require(
            stdout.decode().strip() == o.manifest()["runtime"][label.split("-")[0]]
            and not stderr,
            "Generation runtime receipt drift",
        )
    elif label == "invalid-cli":
        d.require(
            not stdout
            and stderr.startswith(b"INVALID:")
            and b"Traceback" not in stderr,
            "Generation invalid CLI diagnostics",
        )
    else:
        d.require(not stdout and not stderr, "Generation child diagnostics")


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
        "native_backends": ["memory", "sqlite", "reopen"],
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
        "generation result",
    )
    for key, wanted in expected.items():
        d.require(d.same(value[key], wanted), "Generation result drift: " + key)
    d.require(
        str(uuid.UUID(value["execution_id"])) == value["execution_id"],
        "Generation execution ID",
    )
    files = artifact_hashes(folder)
    d.require(d.same(value["artifacts_sha256"], files), "Generation artifact drift")
    reference, actual = d.load(folder / "observed.json"), d.load(folder / "native.json")
    d.require(
        compare(reference, actual) == 0
        and d.same(d.load(folder / "input.json"), recipe()),
        "Generation actual pair/input drift",
    )
    for backend in ("memory", "sqlite", "reopen"):
        for case in CASES:
            raw = d.load(folder / "native-records" / backend / (case + ".json"))
            d.shape(
                raw,
                {"tasks", "entries", "wire", "ledger", "publication", "lifecycle"},
                "Generation exact native records",
            )
            d.require(len(raw["wire"]) == 1, "Generation exact native attempt count")
            d.require(
                len(raw["ledger"]["settlements"]) == 1,
                "Generation exact native usage count",
            )
    exact = trace(d.load(folder / "native-exact.json"))
    d.require(
        d.same(common_profile(exact), actual), "Generation numeric projection drift"
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
        "Generation loaded modules drift",
    )
    d.require(
        d.same(value["native_module_sha256"], native_modules()),
        "Generation native producer drift",
    )
    results = {
        "node-version": 0,
        "npm-version": 0,
        "pi-generation": 0,
        "invalid-cli": 2,
    }
    for name in MUTANTS:
        mutant = d.load(folder / "mutants" / (name + ".json"))
        d.require(
            d.same(mutant, mutate(actual, name)) and compare(reference, mutant) == 1,
            "Generation mutant drift",
        )
        results[name + "-positive"] = 0
        results[name + "-negative"] = 1
    for label, code in results.items():
        process = d.load(folder / "processes" / (label + ".json"))
        process_identity(process, label, folder, snapshot)
        d.require(process["returncode"] == code, "Generation child exit drift")
    invalid = copy.deepcopy(actual)
    invalid["unknown"] = True
    retained = d.load(folder / "mutants" / "invalid.json")
    d.require(d.same(retained, invalid), "Generation invalid input was not retained")
    try:
        trace(retained)
    except o.EvidenceError:
        pass
    else:
        raise o.EvidenceError("Generation invalid producer became valid")
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
