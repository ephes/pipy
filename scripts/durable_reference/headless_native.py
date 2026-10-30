"""Synthetic actual CLI producers; never imported by the product runtime.

The common view comes from real serialized CLI stdout and an independent closed
SQLite read. Only explicit fixture tool/transport construction is replaced.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import signal
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import durable_product_comparison as parent  # noqa: E402

from pipy_harness import (  # noqa: E402
    cli,
    durable_cli,  # noqa: E402
    durable_product_api,
)
from pipy_harness.native.auth_store import AuthStore  # noqa: E402
from pipy_harness.native.durable import sqlite  # noqa: E402
from pipy_harness.native.durable.completed_values import document  # noqa: E402
from pipy_harness.native.durable.generation import PROVIDER_DOC  # noqa: E402
from pipy_harness.native.durable.storage import EntryQuery, TaskQuery  # noqa: E402


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n")


def authority(folder: Path, *, target: str = "original") -> None:
    # Fixture isolation only. The real CLI keeps the existing normal auth/env
    # authority; this synthetic producer must never inherit user credentials.
    path = os.environ.get("PATH", os.defpath)
    os.environ.clear()
    os.environ.update({"PATH": path, "LC_ALL": "C.UTF-8"})
    for name, directory in (
        ("HOME", "home"),
        ("PIPY_AUTH_DIR", "auth"),
        ("PIPY_CONFIG_HOME", "config"),
        ("XDG_STATE_HOME", "state"),
    ):
        path = folder / directory
        path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path)
    dump(
        folder / "config/models.json",
        {
            "providers": {
                "openai": {
                    "api": "openai-completions",
                    "baseUrl": "https://fixture.invalid/" + target,
                    "models": [
                        {
                            "id": "gpt-test",
                            "name": "Test",
                            "reasoning": True,
                            "cost": {
                                "input": 1,
                                "output": 2,
                                "cacheRead": 3,
                                "cacheWrite": 4,
                            },
                        }
                    ],
                }
            }
        },
    )
    AuthStore().set("openai", {"type": "api_key", "key": "fixture-key"})


def install_wire(wire) -> None:
    original = parent.DurableProviderProfile
    durable_cli.DurableProviderProfile = lambda *args, **kwargs: original(
        *args, **kwargs, http_client=wire
    )


def invoke(folder: Path, label: str, argv: list[str]) -> tuple[int, dict]:
    output, errors = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        code = cli.main(["durable", *argv])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (label + ".stdout")).write_text(output.getvalue())
    (folder / (label + ".stderr")).write_text(errors.getvalue())
    dump(
        folder / (label + "-invocation.json"),
        {
            "argv": ["durable", *argv],
            "code": code,
            "producer_pid": os.getpid(),
            "in_process_cli": True,
        },
    )
    return code, json.loads(output.getvalue()) if output.getvalue() else {}


def common_case(recipe: dict, output: Path) -> dict:
    name = recipe["id"]
    folder = output / "stores/native" / name
    records = output / "native-records/common" / name
    authority(folder)
    effects = {"calls": [], "applications": {}, "prepares": []}
    owner = []
    held = [False, parent.threading.Event(), parent.threading.Event()]
    ports = [
        parent.tools.Tool(tool, effects, owner, held) for tool in ("keyed", "later")
    ]
    registry = {
        port.definition.name: parent.ToolRegistration(
            port, "fixture." + port.definition.name, "1", "safe"
        )
        for port in ports
    }
    durable_product_api.builtin_registrations = lambda: registry
    original_create = durable_cli.create_durable_product_session
    original_open = durable_cli.open_durable_product_session

    async def create(*args, **kwargs):
        product = await original_create(*args, **kwargs)
        owner[:] = [product._session]
        return product

    async def reopen(*args, **kwargs):
        product = await original_open(*args, **kwargs)
        owner[:] = [product._session]
        return product

    durable_cli.create_durable_product_session = create
    durable_cli.open_durable_product_session = reopen
    wire = parent.tools.Wire({**recipe, "reasoning": 3})
    install_wire(wire)
    path = folder / "store.sqlite"
    common = ["--store", str(path), "--workspace", str(folder), "--format", "json"]
    tool_flags = [] if recipe["names"] else ["--no-tools"]
    code, first = invoke(
        records,
        "start",
        [
            "start",
            *common,
            "--provider",
            "openai",
            "--model",
            "gpt-test",
            *tool_flags,
            "--prompt",
            "input",
        ],
    )
    assert code == 0
    before = {
        "providers": len(wire.calls),
        "calls": len(effects["calls"]),
        "applications": len(effects["applications"]),
        "prepares": len(effects["prepares"]),
    }
    code, repeated = invoke(records, "terminal-resume", ["resume", *common])
    assert code == 0
    after = {
        "providers": len(wire.calls),
        "calls": len(effects["calls"]),
        "applications": len(effects["applications"]),
        "prepares": len(effects["prepares"]),
    }
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        tasks = [
            json.loads(row[0])
            for row in db.execute("SELECT record FROM tasks ORDER BY id")
        ]
    raw = {
        "first": first,
        "repeated": repeated,
        "before": before,
        "after": after,
        "tasks": tasks,
        "effects": effects,
        "requests": wire.calls,
    }
    dump(records / "raw.json", raw)
    # Restore fixture seams before the next actual CLI case, not live registry
    # mutation while a host is running. Every prior command physically closed.
    durable_cli.create_durable_product_session = original_create
    durable_cli.open_durable_product_session = original_open
    return raw


def product_tables(product):
    storage = product._session._owner.storage
    header = product._header()
    tasks = storage.scan_tasks(TaskQuery({}), 100).items
    entries = storage.scan_entries(
        EntryQuery({"conversationId": header["conversation"]}), 100
    ).items
    return {
        "tasks": [t.to_tree() for t in tasks],
        "entries": [e.to_tree() for e in entries],
        "provider": document(storage, PROVIDER_DOC, header["conversation"]),
    }


def capture_profile(recipe, folder, records, output, wire):
    """Kill only after SQLite actually committed, retaining independent readback."""
    reader = ROOT / "scripts/durable_reference/product_read.py"

    def committed(db):
        rows = [
            sqlite._decode(row[0]) for row in db.execute("SELECT record FROM tasks")
        ]
        for task in rows:
            state = task["state"]
            phase = state.get("checkpoint", {}).get("phase")
            wanted = (
                recipe["cut"] == "prepare"
                and state["status"] == "pending"
                and phase == "prepare"
                or recipe["cut"] == "request"
                and state["status"] == "running"
                and phase == "request"
            )
            if task["kind"] != "pi.generation" or not wanted:
                continue
            entry = db.execute(
                "SELECT id FROM entries ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
            argv = [
                sys.executable,
                "-I",
                str(reader),
                str(folder / "store.sqlite"),
                str(entry),
                str(task["id"]),
            ]
            label = "native-profile-" + recipe["id"] + "-reader"
            stdout, stderr, code = parent.proc.run(
                parent.d, argv, ROOT, output / "processes", label, dict(os.environ), 15
            )
            assert code == 0 and not stderr
            dump(
                records / "capture.json",
                {
                    "task": task,
                    "entry": entry,
                    "before_requests": len(wire.calls),
                    "independent": {
                        "argv": argv,
                        "cwd": str(ROOT),
                        "status": code,
                        "stdout": stdout.decode(),
                        "stderr": stderr.decode(),
                        "observed": json.loads(stdout),
                        "process": parent.d.load(
                            output / "processes" / (label + ".json")
                        ),
                    },
                },
            )
            dump(records / "capture-modules.json", parent.native_modules())
            os.kill(os.getpid(), signal.SIGKILL)

    sqlite._after_commit = committed


def profile_case(recipe, output, mode):
    folder = output / "stores/native-profile" / recipe["id"]
    records = output / "native-records/profiles" / recipe["id"]
    authority(folder, target="original" if mode == "profile-capture" else "current")
    requests = []

    class Wire(parent.tools.Wire):
        def post_json(self, url, **kwargs):
            requests.append(
                {"url": url, "headers": dict(kwargs["headers"]), "body": kwargs["body"]}
            )
            return super().post_json(url, **kwargs)

    wire = Wire({"names": [], "reasoning": 0})
    install_wire(wire)
    before, final = [], []
    original = durable_cli.open_durable_product_session

    async def opening(*args, **kwargs):
        product = await original(*args, **kwargs)
        before.append(await product._read(lambda: product_tables(product)))
        close_outcome = product._close_outcome

        async def closing():
            try:
                final.append(await product._read(lambda: product_tables(product)))
            finally:
                outcome = await close_outcome()
            return outcome

        product._close_outcome = closing
        return product

    durable_cli.open_durable_product_session = opening
    if mode == "profile-capture":
        capture_profile(recipe, folder, records, output, wire)
    common = [
        "--store",
        str(folder / "store.sqlite"),
        "--workspace",
        str(folder),
        "--format",
        "json",
        "--provider",
        "openai",
        "--model",
        "gpt-test",
        "--thinking",
        "low",
        "--no-tools",
    ]
    action = "start" if mode == "profile-capture" else "resume"
    code, result = invoke(
        records,
        mode,
        [action, *common, *(["--prompt", "input"] if action == "start" else [])],
    )
    assert mode != "profile-capture", "Required P16 COMMIT cut absent"
    dump(
        records / "resume.json",
        {
            "code": code,
            "output": result,
            "before": before,
            "final": final,
            "requests": requests,
        },
    )
    dump(records / "resume-modules.json", parent.native_modules())
    return code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("common", "profile-capture", "profile-resume"))
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--case")
    args = parser.parse_args()
    (args.output / "processes").mkdir(parents=True, exist_ok=True)
    recipe = json.loads(args.input.read_text())
    if args.mode != "common":
        case = next(c for c in recipe["profiles"] if c["id"] == args.case)
        profile_case(case, args.output, args.mode)
        # The producer command succeeded even for the explicitly observed native
        # protected-target refusal. The actual CLI code is retained separately.
        return 0
    raw = []
    for case in recipe["cases"]:
        raw.append({"id": case["id"], "raw": common_case(case, args.output)})
    dump(args.output / "native-common-raw.json", raw)
    dump(args.output / "native-common-modules.json", parent.native_modules())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
