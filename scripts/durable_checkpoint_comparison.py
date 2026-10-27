"""DE06b actual pinned rich-value task transport vs native canonical phases.

The paired observable is a declared language projection, not Pi's private arena
or provider wire protocol. Native privacy/reconstruction has separate evidence.
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


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


r = load_module(
    "checkpoint_runtime_reference", ROOT / "scripts/durable_task_runtime_comparison.py"
)
f = load_module(
    "checkpoint_payload_fixtures",
    ROOT / "scripts/durable_reference/canonical_payloads.py",
)
d, b, o, v = r.d, r.b, r.o, r.v
from pipy_harness.native.agent.usage import AgentTokenPricing  # noqa:E402
from pipy_harness.native.auth_material import (  # noqa:E402
    RequestMaterialRef,
    read_request_material,
)
from pipy_harness.native.auth_store import ProviderAuthRequestConfig  # noqa:E402
from pipy_harness.native.catalog import NativeModelSpec  # noqa:E402
from pipy_harness.native.durable.checkpoint_codec import (  # noqa:E402
    commit_checkpoint,
    decode_checkpoint,
    define_canonical_task,
    encode_checkpoint,
)
from pipy_harness.native.durable.memory import MemoryStorage  # noqa:E402
from pipy_harness.native.durable.provider_checkpoint import (  # noqa:E402
    _binding,
    prepare_provider_settings,
)
from pipy_harness.native.durable.scheduler import TaskScheduler  # noqa:E402
from pipy_harness.native.durable.session_core import Session  # noqa:E402
from pipy_harness.native.durable.sqlite import open_sqlite_storage  # noqa:E402

PHASE = "canonical-checkpoint-b-v1"
MANIFEST = o.FIXTURES / "canonical-checkpoint-v1.manifest.json"
DRIVER = ROOT / "scripts/durable_reference/cases/canonical-checkpoint.mts"
DESTINATION = "canonical-checkpoint.mts"
MODULES = r.MODULES | {
    "packages/durable/src/storage/sqlite/node.ts",
    "packages/durable/src/storage/sqlite/storage.ts",
}
SOURCES = r.SOURCES | {
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
}
CLOSURE = (
    r.CLOSURE
    | {str(p.relative_to(ROOT)) for p in (ROOT / "src").rglob("*.py")}
    | {
        "scripts/durable_checkpoint_comparison.py",
        "scripts/durable_reference/canonical_payloads.py",
        "scripts/durable_reference/cases/canonical-checkpoint.mts",
        "tests/durable/test_checkpoint_comparison.py",
        "tests/durable/test_checkpoint_codec.py",
        "tests/durable/test_checkpoint_sqlite.py",
        "tests/durable/test_provider_checkpoint.py",
        "tests/durable/test_auth_material.py",
        "docs/durable-canonical-codec.md",
        "tests/fixtures/durable/canonical-input-v1.json",
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
CASES = ("request", "provider", "tool-missing", "tool-null", "cancelled")
MUTANTS = (
    "input",
    "signature",
    "redacted",
    "blocks",
    "arguments_json",
    "usage",
    "reasoning",
    "cache_write_1h",
    "cost",
    "provider_correlation_id",
    "structured_content",
    "terminate",
    "session_id",
    "cutoff",
    "pricing",
    "api",
    "headers",
    "execution_seconds",
    "started_at",
    "error_message",
    "details",
    "tool_request_id",
    "attachments",
    "hidden_tool_names",
    "undeclared_tool_names",
    "system_messages",
    "cache_retention",
    "available_tools",
    "provider_turn_index",
    "provider_turn_label",
    "retry_wait_cancelled",
    "partial",
)
NUMBER_PROFILE = "finite-binary64-live-v1/persisted-json-zero-v1"
DTO_PROFILE = "legacy-v1-dto/absent-native-nesting-v1"


def _legacy_dto_fields(observed):
    """Select only the finite DTO positions in the original paired recipe."""
    for frame in [*observed["phases"], observed["result"], observed["reopened"]]:
        payload = frame.get("payload")
        if type(payload) is not dict:
            continue
        if payload.get("$type") == "AgentProviderRequestSnapshot":
            messages = payload["fields"]["request"]["fields"]["messages"]["$tuple"]
            results = [
                message["fields"]
                for message in messages
                if message["$type"] == "AgentToolResultMessage"
            ]
        elif payload.get("$type") == "ToolExecutionOutcome":
            result = payload["fields"]["result"]
            d.require(
                result["$type"] == "AgentToolResultMessage",
                "Legacy canonical tool outcome result type drift",
            )
            results = [result["fields"]]
        else:
            continue
        for fields in results:
            if "nested_call_evidence" in fields:
                d.require(
                    fields["nested_call_evidence"] is None,
                    "Native nesting is outside the legacy canonical common profile",
                )
                del fields["nested_call_evidence"]


def _persisted_numbers(value):
    if type(value) is float and value == 0:
        return 0.0
    if type(value) is dict:
        return {key: _persisted_numbers(item) for key, item in value.items()}
    if type(value) is list:
        return [_persisted_numbers(item) for item in value]
    return value


def common_profile(value):
    """Legacy DTO subset; only persisted views use Pi's JSON zero rule."""
    changed = copy.deepcopy(value)
    for row in changed["cases"]:
        observed = v.decode(row["observed"])
        _legacy_dto_fields(observed)
        for key in ("result", "reopened"):
            observed[key] = _persisted_numbers(observed[key])
        row["observed"] = v.encode(observed)
    return changed


def recipe():
    value = d.load(o.FIXTURES / "canonical-input-v1.json")
    d.require(
        value["schema_version"] == 1 and value["phase"] == PHASE,
        "Canonical recipe identity",
    )
    d.require(
        [case["id"] for case in value["cases"]] == list(CASES), "Canonical recipe cases"
    )
    return value


def observed(frame, auth):
    settings = frame.settings
    semantic = None
    if settings is not None:
        tree = settings.to_tree()
        material = read_request_material(
            auth, RequestMaterialRef.from_tree(tree["material"]), _binding(tree)
        )
        semantic = {
            key: tree[key]
            for key in ("provider", "model", "api", "thinking", "options", "pricing")
        }
        semantic["headers"] = material["configured_headers"]
    return {
        "payload": f.project(frame.payload),
        "identity": frame.identity,
        "settings": semantic,
    }


async def native_case(case, backend, folder):
    folder.mkdir(parents=True)
    path, auth = folder / "session.sqlite", folder / "auth.json"
    inner = (
        MemoryStorage()
        if backend == "memory"
        else open_sqlite_storage(str(path), mode="create")
    )
    session = Session(inner, now=lambda: 1000)
    name = case["id"]
    value = f.construct(case["payload"])
    saved = None
    if not name.startswith("tool-"):
        snapshot = f.construct(recipe()["cases"][0]["payload"])
        saved = prepare_provider_settings(
            snapshot,
            NativeModelSpec("fixture", "model", "Model", "openai-completions"),
            auth_path=auth,
            env={},
            runtime_api_key="fake-key",
            config=ProviderAuthRequestConfig(
                headers={"X-Fixture": "fake-private-header"}
            ),
            thinking="high",
            pricing=AgentTokenPricing(1, 2, 3, 4),
        )
    role = (
        "request"
        if name == "request"
        else "tool_outcome"
        if name.startswith("tool-")
        else "provider_outcome"
    )

    def packet(phase, payload, settings):
        return encode_checkpoint(
            name="canonical.pair",
            version=-1.5,
            phase=phase,
            role=role,
            identity=case["identity"],
            payload=payload,
            settings=settings,
        )

    phases = []
    reached = threading.Event()

    async def start(frame, runtime, context):
        phases.append(observed(frame, auth))
        await commit_checkpoint(
            runtime, packet("settle", frame.payload, frame.settings)
        )

    async def settle(frame, runtime, context):
        phases.append(observed(frame, auth))
        await commit_checkpoint(
            runtime, packet("settle", frame.payload, frame.settings), terminal=True
        )
        reached.set()

    async def abort(*args):
        raise AssertionError("abort not selected")

    definition = define_canonical_task(
        "canonical.pair",
        -1.5,
        initial_phase="start",
        phases={"start": (role, start), "settle": (role, settle)},
        abort=abort,
    )
    conversation = (
        await session.acommit(lambda tx: tx.create_conversation({"kind": "ownerless"}))
    ).id
    await session.acommit(
        lambda tx: tx.create_task(
            definition,
            packet("start", value, saved).to_tree(),
            ownership={"kind": "conversation"},
            conversation_id=conversation,
        )
    )
    if backend == "reopen":
        await session.aclose()
        session = Session(open_sqlite_storage(str(path), mode="open"), now=lambda: 1000)
    scheduler = TaskScheduler(session, [definition])
    await scheduler.aopen()
    before_calls = len(phases)
    scheduler.resume()
    d.require(
        await asyncio.to_thread(reached.wait, 10), "Canonical phases did not finish"
    )
    receipt = scheduler.inspect()[0].to_tree()["state"]["outcome"]["result"]
    terminal = decode_checkpoint(
        receipt, name="canonical.pair", version=-1.5, phase="settle", role=role
    )
    result = observed(terminal, auth)
    d.dump(folder / "decoded-receipt.json", receipt)
    await session.aclose()
    reopened = result
    if backend != "memory":
        again = Session(open_sqlite_storage(str(path), mode="open"), now=lambda: 1000)
        replay = TaskScheduler(again, [definition])
        await replay.aopen()
        retained = replay.inspect()[0].to_tree()["state"]["outcome"]["result"]
        reopened = observed(
            decode_checkpoint(
                retained, name="canonical.pair", version=-1.5, phase="settle", role=role
            ),
            auth,
        )
        await again.aclose()
    return {
        "id": name,
        "observed": v.encode(
            {
                "before_calls": before_calls,
                "phases": phases,
                "result": result,
                "reopened": reopened,
            }
        ),
    }


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
        "Canonical native backend mismatch",
    )
    d.dump(folder.parent / "native-exact.json", outputs[0])
    return common_profile(outputs[0])


def trace(value):
    d.shape(value, {"schema_version", "phase", "cases"}, "canonical checkpoint trace")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Canonical checkpoint identity",
    )
    rows = value["cases"]
    d.require(
        type(rows) is list
        and [r.get("id") for r in rows if type(r) is dict] == list(CASES),
        "Canonical checkpoint cases",
    )
    for row in rows:
        d.shape(row, {"id", "observed"}, "Canonical checkpoint row")
        decoded = v.decode(row["observed"])
        d.shape(
            decoded,
            {"before_calls", "phases", "result", "reopened"},
            "Canonical checkpoint observation",
        )
        d.require(
            v.encode(decoded) == row["observed"], "Canonical checkpoint value profile"
        )
    return value


def compare(expected, actual):
    trace(expected)
    trace(actual)
    return int(not d.same(expected, actual))


def mutate(actual, name):
    changed = copy.deepcopy(actual)
    field = "user_prompt" if name == "input" else name
    for row in changed["cases"]:
        observed = v.decode(row["observed"])
        pending = [observed["result"]]
        while pending:
            value = pending.pop()
            if type(value) is dict:
                if field in value:
                    old = value[field]
                    value[field] = (
                        not old
                        if type(old) is bool
                        else old + "~"
                        if type(old) is str
                        else old + 1
                        if type(old) in (int, float)
                        else False
                        if old is None
                        else None
                    )
                    row["observed"] = v.encode(observed)
                    trace(changed)
                    return changed
                pending.extend(value.values())
            elif type(value) is list:
                pending.extend(value)
    raise o.EvidenceError("Missing canonical mutant field")


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
        "canonical checkpoint manifest",
    )
    d.require(
        type(config["schema_version"]) is int
        and config["schema_version"] == 1
        and config["phase"] == PHASE
        and config["reference_commit"] == o.PIN,
        "Canonical checkpoint manifest identity",
    )
    for key, names in (("closure", CLOSURE), ("reference_sources", SOURCES)):
        d.shape(config[key], names, "canonical checkpoint " + key)
        for name, digest in config[key].items():
            o.contract.digest(digest)
            if key == "closure":
                d.require(
                    o.sha(ROOT / name) == digest, "Canonical checkpoint closure drift"
                )
    d.require(
        config["parents"] == {"task-runtime": o.sha(r.MANIFEST)},
        "Canonical checkpoint parent drift",
    )
    r.configuration()
    return config


def native_modules():
    modules = {}
    for name, module in tuple(sys.modules.items()):
        if name != "pipy_harness" and not name.startswith("pipy_harness."):
            continue
        filename = getattr(module, "__file__", None)
        d.require(filename is not None, "Canonical producer lacks a source file")
        path = Path(filename).resolve()
        d.require(path.is_relative_to(ROOT), "Canonical producer outside checkout")
        relative = str(path.relative_to(ROOT))
        d.require(relative in CLOSURE, "Canonical producer outside closure")
        modules[relative] = o.sha(path)
    return modules


def execute(acceptance, output):
    d.require(
        not output.exists() and not output.is_symlink(),
        "Canonical checkpoint output exists",
    )
    acceptance, output = acceptance.resolve(strict=True), output.absolute()
    d.require(
        not output.resolve().is_relative_to(acceptance),
        "Canonical checkpoint output inside parent",
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
        "Canonical checkpoint driver exists",
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
            "Canonical checkpoint runtime drift",
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
        "pi-canonical-checkpoint",
        env,
        120,
    )
    d.require(
        code == 0 and not stdout and not stderr,
        "Pinned canonical checkpoint driver failed",
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
            d.require(
                code == wanted, "Canonical checkpoint semantic CLI control failed"
            )
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
    d.require(code == 2, "Canonical checkpoint invalid CLI control failed")
    configuration()
    o.verify_sources(snapshot, config["reference_sources"])
    o.acceptance.attest_reference(o, acceptance / "reference", parent, o.FIXTURES)
    v.artifacts.attest(acceptance, o.FIXTURES)
    d.require(
        manifest_sha == o.sha(MANIFEST) and o.sha(driver) == o.sha(DRIVER),
        "Canonical checkpoint producer drift",
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
            "dto_profile": DTO_PROFILE,
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
    elif label == "pi-canonical-checkpoint":
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
        "Canonical checkpoint child identity drift",
    )
    stdout, stderr = (
        (processes / (label + ".stdout")).read_bytes(),
        (processes / (label + ".stderr")).read_bytes(),
    )
    if label in ("node-version", "npm-version"):
        d.require(
            stdout.decode().strip() == o.manifest()["runtime"][label.split("-")[0]]
            and not stderr,
            "Canonical checkpoint runtime receipt drift",
        )
    elif label == "invalid-cli":
        d.require(
            not stdout
            and stderr.startswith(b"INVALID:")
            and b"Traceback" not in stderr,
            "Canonical checkpoint invalid CLI diagnostics",
        )
    else:
        d.require(not stdout and not stderr, "Canonical checkpoint child diagnostics")


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
        "dto_profile": DTO_PROFILE,
    }
    d.shape(
        value,
        set(expected)
        | {"execution_id", "module_sha256", "native_module_sha256", "artifacts_sha256"},
        "canonical checkpoint result",
    )
    for key, wanted in expected.items():
        d.require(
            d.same(value[key], wanted), "Canonical checkpoint result drift: " + key
        )
    d.require(
        str(uuid.UUID(value["execution_id"])) == value["execution_id"],
        "Canonical checkpoint execution ID",
    )
    files = artifact_hashes(folder)
    d.require(
        d.same(value["artifacts_sha256"], files), "Canonical checkpoint artifact drift"
    )
    reference, actual = d.load(folder / "observed.json"), d.load(folder / "native.json")
    d.require(
        compare(reference, actual) == 0
        and d.same(d.load(folder / "input.json"), recipe()),
        "Canonical checkpoint actual pair/input drift",
    )
    exact = trace(d.load(folder / "native-exact.json"))
    d.require(
        d.same(common_profile(exact), actual), "Canonical numeric projection drift"
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
        "Canonical checkpoint loaded modules drift",
    )
    d.require(
        d.same(value["native_module_sha256"], native_modules()),
        "Canonical checkpoint native producer drift",
    )
    results = {
        "node-version": 0,
        "npm-version": 0,
        "pi-canonical-checkpoint": 0,
        "invalid-cli": 2,
    }
    for name in MUTANTS:
        mutant = d.load(folder / "mutants" / (name + ".json"))
        d.require(
            d.same(mutant, mutate(actual, name)) and compare(reference, mutant) == 1,
            "Canonical checkpoint mutant drift",
        )
        results[name + "-positive"] = 0
        results[name + "-negative"] = 1
    for label, code in results.items():
        process = d.load(folder / "processes" / (label + ".json"))
        process_identity(process, label, folder, snapshot)
        d.require(
            process["returncode"] == code, "Canonical checkpoint child exit drift"
        )
    invalid = copy.deepcopy(actual)
    invalid["unknown"] = True
    retained = d.load(folder / "mutants" / "invalid.json")
    d.require(
        d.same(retained, invalid), "Canonical checkpoint invalid input was not retained"
    )
    try:
        trace(retained)
    except o.EvidenceError:
        pass
    else:
        raise o.EvidenceError("Canonical checkpoint invalid producer became valid")
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
