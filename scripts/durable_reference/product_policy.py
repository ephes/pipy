"""Native-only public product controls; no Pi lifecycle/API parity claim."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import threading
from dataclasses import asdict, fields, replace
from pathlib import Path

from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.durable.canonical_codec import (
    decode_canonical,
    encode_canonical,
)
from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.generation_transport import owned_call
from pipy_harness.native.durable.records import TaskId, TaskRecord
from pipy_harness.native.http import JsonResponse
from pipy_harness.sdk import (
    DurableProductError,
    DurableProviderProfile,
    DurableRunSelection,
    UnsupportedDurableCapability,
    create_durable_product_session,
    open_durable_product_session,
)

MUTANTS = (
    "pre_ack_effect",
    "sticky_dispatch",
    "sibling",
    "seed",
    "selector",
    "old_success",
    "double_usage",
    "leaked_frame",
    "early_close",
    "detached_host",
    "lost_lease",
)
OPENER = r"""
import hashlib,json,os,sys
from pathlib import Path
from pipy_harness.native.durable import sqlite
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.ownership import StorageOwnershipError
try:store=open_sqlite_storage(sys.argv[1],mode='open')
except StorageOwnershipError:status='refused'
else:store.close();status='opened'
source=Path(sqlite.__file__)
print(json.dumps({'status':status,'pid':os.getpid(),'pgid':os.getpgid(0),'source':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}))
"""


class Wire:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def post_json(self, url, **kwargs):
        self.calls.append(copy.deepcopy(kwargs["body"]))
        if self.fail:
            raise RuntimeError("Synthetic transport failed before a receipt")
        return JsonResponse(
            200,
            {
                "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                    "prompt_tokens_details": {"cached_tokens": 4},
                },
            },
        )


def profile(folder, wire):
    return DurableProviderProfile(
        NativeModelSpec("openai", "gpt-test", "Test", "openai-completions"),
        auth_path=folder / "auth.json",
        env={},
        runtime_api_key="fixture-key",
        http_client=wire,
    )


async def row(product, task_id):
    return await product._read(
        lambda: product._session._owner.storage.task(TaskId(task_id)).to_tree()
    )


async def retry_fixture(folder):
    folder.mkdir(parents=True)
    wire = Wire(fail=True)
    authority = profile(folder, wire)
    refused = None
    try:
        await create_durable_product_session(
            folder / "unsupported" / "store.sqlite",
            workspace=folder,
            profile=authority,
            tools={},
            extensions=object(),
        )
    except UnsupportedDurableCapability as error:
        refused = error.code
    product = await create_durable_product_session(
        folder / "store.sqlite", workspace=folder, profile=authority, tools={}
    )
    try:
        admission = await product.submit("input")
        pre_resume = len(wire.calls)
        failed = await product.resume()
        source = await row(product, admission.generation_task_id)
        before = len(wire.calls)
        nonce = "a" * 32
        winner = await product.authorize_retry(
            admission.generation_task_id, nonce=nonce
        )
        pending = await row(product, winner.new_task_id)
        after = len(wire.calls)
        duplicate = await product.authorize_retry(
            admission.generation_task_id, nonce=nonce
        )
        sibling_error, selection_error = None, None
        try:
            await product.authorize_retry(admission.generation_task_id, nonce="b" * 32)
        except DurableProductError as error:
            sibling_error = error.code
        try:
            await product.resume()
        except DurableProductError as error:
            selection_error = error.code
        wire.fail = False
        result = await product.resume(DurableRunSelection(winner.new_task_id, nonce))
        header = await product._read(product._header)
        final_row = await row(product, winner.new_task_id)
        with product._scheduler._guard:
            quiescent = {
                "enabled": product._scheduler._enabled,
                "draining": product._scheduler._draining,
                "invocation": product._scheduler._invocation is not None,
            }
        raw = {
            "admission": asdict(admission),
            "pre_resume": pre_resume,
            "failed": failed.inspection.to_tree(),
            "source": source,
            "winner": asdict(winner),
            "pending": pending,
            "duplicate": asdict(duplicate),
            "before": before,
            "after_authorize": after,
            "sibling_error": sibling_error,
            "selection_error": selection_error,
            "header": header,
            "final_row": final_row,
            "returned_task": result.task_id,
            "returned_status": result.status,
            "inspection": result.inspection.to_tree(),
            "quiescent": quiescent,
            "ledger": result.completed.usage.thaw(),
            "messages": [m.canonical.thaw() for m in result.completed.messages],
            "message_ids": [m.entry_id for m in result.completed.messages],
            "dto_extra_fields": sorted(
                {f.name for f in fields(result)}
                - {"task_id", "status", "inspection", "completed"}
            ),
            "unsupported": {
                "code": refused,
                "path_exists": (folder / "unsupported").exists(),
            },
        }
    finally:
        await product.close()
    raw["host_alive_after_close"] = product._scheduler._thread.is_alive()
    raw["end"] = product._session.end.reason
    count = len(wire.calls)
    for material in folder.glob("auth.json*"):
        material.unlink()
    reopened = await open_durable_product_session(
        folder / "store.sqlite",
        workspace=folder,
        tools={},
        selection=DurableRunSelection(winner.new_task_id, nonce),
    )
    try:
        retained = await reopened.resume()
        raw["terminal_reopen"] = {
            "task": retained.task_id,
            "outcome": retained.inspection.outcome,
            "calls_before": count,
            "calls_after": len(wire.calls),
            "scheduler_created": reopened._scheduler is not None,
        }
    finally:
        await reopened.close()
    return raw


async def lifetime_fixture(folder, d, process, processes, env):
    folder.mkdir(parents=True)
    entered, release, finalizing, finish, exited = (threading.Event() for _ in range(5))

    class Held(Wire):
        def post_json(self, *args, **kwargs):
            entered.set()
            try:
                d.require(release.wait(15), "Product held provider timed out")
                return super().post_json(*args, **kwargs)
            finally:
                finalizing.set()
                d.require(finish.wait(15), "Product held finalizer timed out")
                exited.set()

    wire = Held()
    product = await create_durable_product_session(
        folder / "store.sqlite",
        workspace=folder,
        profile=profile(folder, wire),
        tools={},
    )
    observations = {}

    async def probe(label):
        stdout, stderr, code = await owned_call(
            lambda: process(
                d,
                [sys.executable, "-I", "-c", OPENER, str(folder / "store.sqlite")],
                Path(__file__).resolve().parents[2],
                processes,
                label,
                env,
                15,
            )
        )
        d.require(code == 0 and not stderr, "Product lifetime opener failed")
        return json.loads(stdout)

    try:
        await product.submit("input")
        running = asyncio.create_task(product.resume())
        d.require(await asyncio.to_thread(entered.wait, 5), "Product provider absent")
        running.cancel()
        cancelled = False
        try:
            await running
        except asyncio.CancelledError:
            cancelled = True
        closing = asyncio.create_task(product.close())
        await asyncio.sleep(0)
        closing.cancel()
        try:
            await closing
        except asyncio.CancelledError:
            pass
        observations["waiter_cancelled"] = cancelled
        observations["provider_exited_early"] = exited.is_set()
        observations["provider"] = await probe("lifetime-provider")
        release.set()
        d.require(
            await asyncio.to_thread(finalizing.wait, 5), "Product finalizer absent"
        )
        observations["finalizer_exited_early"] = exited.is_set()
        observations["finalizer"] = await probe("lifetime-finalizer")
        finish.set()
        await product.close()
        observations["exited_after_close"] = exited.is_set()
        observations["host_alive_after_close"] = product._scheduler._thread.is_alive()
        observations["closed"] = await probe("lifetime-closed")
    finally:
        release.set()
        finish.set()
        await product.close()
    return observations


def produce(folder, d, process, processes, env):
    return {
        "schema_version": 1,
        "profile": "native-product-contract-v1",
        "retry": asyncio.run(retry_fixture(folder / "retry")),
        "lifetime": asyncio.run(
            lifetime_fixture(folder / "lifetime", d, process, processes, env)
        ),
    }


def protocol(value, d):
    d.shape(
        value,
        {"schema_version", "profile", "retry", "lifetime"},
        "Native product policy",
    )
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["profile"] == "native-product-contract-v1",
        "Native product profile",
    )
    d.shape(
        value["retry"],
        {
            "admission",
            "pre_resume",
            "failed",
            "source",
            "winner",
            "pending",
            "duplicate",
            "before",
            "after_authorize",
            "sibling_error",
            "selection_error",
            "header",
            "final_row",
            "returned_task",
            "returned_status",
            "inspection",
            "quiescent",
            "ledger",
            "messages",
            "message_ids",
            "dto_extra_fields",
            "unsupported",
            "host_alive_after_close",
            "end",
            "terminal_reopen",
        },
        "Native product retry",
    )
    d.shape(
        value["lifetime"],
        {
            "waiter_cancelled",
            "provider_exited_early",
            "provider",
            "finalizer_exited_early",
            "finalizer",
            "exited_after_close",
            "host_alive_after_close",
            "closed",
        },
        "Native product lifetime",
    )
    for name in ("source", "pending", "final_row"):
        TaskRecord(value["retry"][name])
    # Every body is finite JSON; nested identity changes remain semantic controls.
    OwnedJson(value)


def valid(value, d):
    protocol(value, d)
    r, life = value["retry"], value["lifetime"]
    try:
        return retry_valid(r) and lifetime_valid(life)
    except (KeyError, IndexError, AttributeError, TypeError, ValueError):
        raise d.o.EvidenceError("Native product policy body is incompatible") from None


def retry_valid(r):
    a, winner = r["admission"], r["winner"]
    identity = (
        winner["source_task_id"]
        == a["generation_task_id"]
        == r["source"]["id"]
        == r["header"]["initial_generation"]
        and a["entry_id"] == r["header"]["initial_entry"]
        and winner["new_task_id"]
        == r["pending"]["id"]
        == r["final_row"]["id"]
        == r["returned_task"]
        and winner["source_task_id"] != winner["new_task_id"]
    )
    control = (
        r["pre_resume"] == 0
        and r["before"] == r["after_authorize"] == 1
        and r["duplicate"] == winner
        and r["sibling_error"] == "successor_exists"
        and r["selection_error"] == "selection_required"
        and r["pending"]["state"]["status"] == "pending"
        and r["quiescent"] == {"enabled": False, "draining": False, "invocation": False}
    )
    seed = bound_template(r) and OwnedJson(r["source"]["input"]) == OwnedJson(
        r["pending"]["input"]
    ) == OwnedJson(r["final_row"]["input"])
    completion = (
        r["source"]["state"]["outcome"]["status"] == r["failed"]["outcome"] == "faulted"
        and r["returned_status"] == "terminal"
        and r["inspection"]["outcome"]
        == r["final_row"]["state"]["outcome"]["status"]
        == "completed"
    )
    reopened = r["terminal_reopen"]
    cleanup = (
        r["end"] == "closed"
        and r["host_alive_after_close"] is False
        and reopened["task"] == winner["new_task_id"]
        and reopened["outcome"] == "completed"
        and reopened["calls_before"] == reopened["calls_after"] == 2
        and reopened["scheduler_created"] is False
        and r["dto_extra_fields"] == []
        and r["unsupported"] == {"code": "incompatible", "path_exists": False}
    )
    scope = (
        all(
            record["conversationId"] == a["conversation_id"] and "owner" not in record
            for record in (r["source"], r["pending"], r["final_row"])
        )
        and r["header"]["conversation"] == a["conversation_id"]
    )
    return (
        identity
        and scope
        and control
        and seed
        and completion
        and cleanup
        and ledger_valid(r)
    )


def bound_template(r):
    template = decode_canonical(r["header"]["template"])
    user = decode_canonical(r["messages"][0])
    expected = replace(
        template,
        request=replace(
            template.request, user_prompt=user.content.value, messages=(user,)
        ),
    )
    return (
        OwnedJson(encode_canonical(expected)) == OwnedJson(r["source"]["input"])
        and r["header"]["selection"] == ["openai", "gpt-test", "openai-completions"]
        and r["header"]["workspace"] == str(expected.request.cwd)
        and r["header"]["initial_entry"] == r["message_ids"][0]
    )


def ledger_valid(r):
    messages = [decode_canonical(m) for m in r["messages"]]
    assistant = messages[-1]
    model = r["ledger"]["models"]["openai/gpt-test"]
    return (
        len(messages) == 2
        and messages[0].content.value == "input"
        and assistant.content.value == "done"
        and list(r["ledger"]["settlements"].values()) == [r["message_ids"][-1]]
        and model["input"] == assistant.usage.input
        and model["output"] == assistant.usage.output
        and model["total_tokens"] == assistant.usage.total_tokens
        and model["cost"]["total"] == assistant.usage.cost.total
    )


def lifetime_valid(life):
    return (
        life["waiter_cancelled"] is True
        and life["provider_exited_early"] is False
        and life["finalizer_exited_early"] is False
        and life["provider"]["status"] == life["finalizer"]["status"] == "refused"
        and life["closed"]["status"] == "opened"
        and life["exited_after_close"] is True
        and life["host_alive_after_close"] is False
    )


def mutate(value, name):
    changed = copy.deepcopy(value)
    r, life = changed["retry"], changed["lifetime"]
    life_changes = {
        "early_close": ("provider_exited_early", True),
        "detached_host": ("host_alive_after_close", True),
    }
    if name in life_changes:
        key, replacement = life_changes[name]
        life[key] = replacement
        return changed
    leaf_changes = {
        "sibling": ("sibling_error", None),
        "selector": ("returned_task", r["source"]["id"]),
        "leaked_frame": ("dto_extra_fields", ["checkpoint"]),
    }
    if name in leaf_changes:
        key, replacement = leaf_changes[name]
        r[key] = replacement
        return changed
    if name == "pre_ack_effect":
        r["pre_resume"] += 1
    elif name == "sticky_dispatch":
        r["after_authorize"] += 1
    elif name == "seed":
        r["pending"]["input"] = {"unbound": True}
    elif name == "old_success":
        r["source"]["state"]["outcome"] = {"status": "completed", "result": None}
    elif name == "double_usage":
        r["ledger"]["models"]["openai/gpt-test"]["total_tokens"] *= 2
    elif name == "lost_lease":
        life["finalizer"]["status"] = "opened"
    else:
        raise ValueError("Unknown native product control")
    return changed
