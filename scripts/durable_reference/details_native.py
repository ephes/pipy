"""Actual CLI/SQLite normal result producer, with only synthetic tool/HTTP ports."""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from durable_reference import headless_native as base  # noqa: E402

from pipy_harness.native.agent.tool_details import NO_TOOL_DETAILS  # noqa: E402


def run(recipe, output):
    rows = []
    for case in recipe["cases"]:
        folder = output / "stores/native" / case["id"]
        records = output / "native-records" / case["id"]
        base.authority(folder)
        effects = {"calls": [], "applications": {}, "prepares": []}
        owner = []
        held = [False, base.parent.threading.Event(), base.parent.threading.Event()]

        class Tool(base.parent.tools.Tool):
            def invoke(self, request, context):
                result = super().invoke(request, context)
                return replace(result, details=case.get("details", NO_TOOL_DETAILS))

        port = Tool("value", effects, owner, held)
        registry = {
            "value": base.parent.ToolRegistration(port, "fixture.value", "1", "safe")
        }
        base.durable_product_api.builtin_registrations = lambda: registry
        original_create = base.durable_cli.create_durable_product_session

        async def create(*args, **kwargs):
            product = await original_create(*args, **kwargs)
            owner[:] = [product._session]
            return product

        base.durable_cli.create_durable_product_session = create
        wire = base.parent.tools.Wire({"names": ["value"]})
        base.install_wire(wire)
        path = folder / "store.sqlite"
        common = ["--store", str(path), "--workspace", str(folder), "--format", "json"]
        try:
            code, first = base.invoke(
                records,
                "start",
                [
                    "start",
                    *common,
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-test",
                    "--prompt",
                    "input",
                ],
            )
            assert code == 0
            before = {
                "providers": len(wire.calls),
                "calls": len(effects["calls"]),
                "prepares": len(effects["prepares"]),
            }
            code, repeated = base.invoke(records, "resume", ["resume", *common])
            assert code == 0
            after = {
                "providers": len(wire.calls),
                "calls": len(effects["calls"]),
                "prepares": len(effects["prepares"]),
            }
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
                db.execute("PRAGMA query_only=ON")
                tasks = [
                    json.loads(row[0])
                    for row in db.execute("SELECT record FROM tasks ORDER BY id")
                ]
                entries = [
                    json.loads(row[0])
                    for row in db.execute("SELECT record FROM entries ORDER BY id")
                ]
            final = next(
                entry for entry in reversed(entries) if entry["kind"] == "pi.assistant"
            )
            raw = {
                "first": first,
                "repeated": repeated,
                "before": before,
                "after": after,
                "tasks": tasks,
                "entries": entries,
                "next_request": final["data"]["request"],
                "wire": wire.calls,
                "effects": effects,
            }
            base.dump(records / "raw.json", raw)
            rows.append({"id": case["id"], "raw": raw})
        finally:
            base.durable_cli.create_durable_product_session = original_create
    base.dump(output / "native-raw.json", rows)
    base.dump(output / "native-modules.json", base.parent.native_modules())


if __name__ == "__main__":
    run(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]))
