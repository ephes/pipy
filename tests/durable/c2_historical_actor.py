# Executed only after explicit pinned-source sys.path prelude supplied by parent.
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import threading
from contextlib import closing
from pathlib import Path

from test_product_session import profile, tools
from tool_support import CountedWire, Keyed, counts

from pipy_harness.native.durable import sqlite as backend
from pipy_harness.sdk import (
    create_durable_product_session,
    open_durable_product_session,
)

ROOT = Path(sys.argv[1])
ROLE = sys.argv[2]
ACTION = sys.argv[3]
CUT = sys.argv[4]

if ROLE == "B":
    from tool_support import reporting_faults

SOURCE = Path(os.environ["HIST_SOURCE"])


def origins():
    result = []
    for name, module in sorted(sys.modules.items()):
        if name.startswith(("pipy_harness", "pipy_session")) and getattr(
            module, "__file__", None
        ):
            p = Path(module.__file__)
            assert p.is_relative_to(SOURCE / "src"), (name, str(p))
            result.append(
                {
                    "module": name,
                    "file": str(p),
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                }
            )
    return result


def say(event, **extra):
    print(
        json.dumps(
            {
                "event": event,
                "pid": os.getpid(),
                "pgid": os.getpgrp(),
                "native_origins": origins(),
                **extra,
            }
        ),
        flush=True,
    )


def raw():
    with closing(
        sqlite3.connect("file:" + str(ROOT / "product.sqlite") + "?mode=ro", uri=True)
    ) as db:
        return {
            table: [
                json.loads(bytes(row[0]).decode("utf8", "surrogatepass"))
                for row in db.execute("SELECT record FROM " + table + " ORDER BY id")
            ]
            for table in ["conversations", "tasks", "entries", "documents"]
        }


def barrier(label):
    say("cut", label=label)
    assert threading.Event().wait(60), "missing parent kill"


def registry_for_cut():
    if ACTION == "produce" and CUT in ("error", "backend"):
        if ROLE == "DE12":
            from test_resolution import InvalidFirst as ErrorPort
        else:
            from tool_support import InfrastructureFailureFirst as ErrorPort
        registry = tools(ROOT, port_type=ErrorPort)
    else:

        def port(root, name, **kwargs):
            return Keyed(
                root,
                name,
                barrier=barrier,
                cut="effect"
                if CUT == "effect" and name == "keyed" and ACTION == "produce"
                else "",
                **kwargs,
            )

        registry = tools(ROOT, port_type=port)
    if CUT == "preintent":
        from dataclasses import replace

        class SequentialPort(Keyed):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                if self.definition.name == "keyed":
                    self.definition = replace(
                        self.definition, execution_mode="sequential"
                    )

        registry = tools(ROOT, port_type=SequentialPort)
    return registry


def install_hooks():
    injections = []
    if (ACTION == "produce" and CUT == "intent-after") or (
        ACTION == "recover" and CUT == "receipt-after"
    ):

        def at(connection):
            rows = [
                json.loads(bytes(r[0]).decode("utf8", "surrogatepass"))
                for r in connection.execute("SELECT record FROM tasks")
            ]
            wanted = next(
                (
                    r
                    for r in rows
                    if r["kind"] == "pi.tool" and r["input"].get("callId") == "call:0"
                ),
                None,
            )
            if wanted and wanted["state"].get("checkpoint", {}).get("phase") == (
                "execute" if CUT == "intent-after" else "settle"
            ):
                barrier(CUT)

        backend._after_commit = at
    if ACTION == "produce" and CUT == "backend":

        def inject(connection):
            rows = [
                json.loads(bytes(r[0]).decode("utf8", "surrogatepass"))
                for r in connection.execute("SELECT record FROM tasks")
            ]
            child = next(
                (
                    r
                    for r in rows
                    if r["kind"] == "pi.tool" and r["input"]["callId"] == "call:0"
                ),
                None,
            )
            if child and child["state"].get("checkpoint", {}).get("phase") == "blocked":
                injections.append(child)
                (ROOT / "BACKEND-INJECTION.json").write_text(
                    json.dumps(injections) + "\n"
                )
                raise sqlite3.OperationalError(
                    "synthetic historical block commit failure"
                )

        backend._before_commit = inject
    return injections


async def scenario():
    registry = registry_for_cut()
    wire = CountedWire(ROOT)
    factory = (
        create_durable_product_session
        if ACTION == "produce"
        else open_durable_product_session
    )
    async with await factory(
        ROOT / "product.sqlite",
        workspace=ROOT,
        profile=profile(ROOT, wire),
        tools=registry,
    ) as product:
        if ACTION == "produce":
            await product.submit("input")
        injections = install_hooks()
        try:
            result = await product.resume()
        except Exception:
            if CUT != "backend":
                raise
            assert len(injections) == 1
            await product.close()
            assert product._session.end.reason == "failed"
            result = None
        if CUT != "backend":
            assert result.status == "held", (
                ACTION,
                CUT,
                result.status,
                result.inspection,
            )
        graph = raw()
        child = next(
            r
            for r in graph["tasks"]
            if r["kind"] == "pi.tool" and r["input"]["callId"] == "call:0"
        )
        (ROOT / "HISTORICAL-FRONTIER.json").write_text(
            json.dumps(
                {
                    "role": ROLE,
                    "origin": CUT,
                    "counts": counts(ROOT),
                    "child": child,
                    "graph": graph,
                    "public_status": None if result is None else result.status,
                    "native_origins": origins(),
                },
                indent=2,
            )
            + "\n"
        )
        if ROLE == "B" and CUT in ("error", "backend"):
            witness = reporting_faults(ROOT)
            assert len(witness) == 1 and witness[0][2:] == (1, 1)
            (ROOT / "CAPTURE-RETURN-WITNESS.json").write_text(
                json.dumps(witness) + "\n"
            )
        say(
            "held",
            phase=child["state"]["checkpoint"]["phase"],
            reason=child["state"]["checkpoint"].get("reason"),
            intent_present=child["state"]["checkpoint"].get("intent") is not None,
            counts=counts(ROOT),
        )
    say("closed")


try:
    asyncio.run(asyncio.wait_for(scenario(), 35))
finally:
    (ROOT / ("actor-" + ROLE + "-" + ACTION + "-" + CUT + "-origins.json")).write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "pgid": os.getpgrp(),
                "source": str(SOURCE),
                "native_origins": origins(),
            },
            indent=2,
        )
        + "\n"
    )
