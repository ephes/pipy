"""Real DE07 generation: committed-only output, owned admissions and saved usage."""

import json
import sqlite3

import pytest
from generation_support import Wire, admit, completion, definition, seed

from pipy_harness.native.agent.usage import AgentTokenPricing
from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.native.durable.generation import PROVIDER_DOC, ROUND_DOC, USAGE_DOC
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.sqlite_schema import TABLES
from pipy_harness.native.durable.storage import EntryQuery


@pytest.fixture(name="rig", params=["memory", "sqlite"])
def rig(tmp_path, request, monkeypatch):
    # No failure path can fall back to owner HOME/auth/config state.
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    path = tmp_path / "session.sqlite"
    store = (
        MemoryStorage()
        if request.param == "memory"
        else open_sqlite_storage(str(path), mode="create")
    )
    session = Session(store)
    yield session, path if request.param == "sqlite" else None
    session.close(timeout=10)


def entries(session, conversation):
    return session.commit(
        lambda tx: tx.scan_entries(EntryQuery({"conversationId": conversation}), 100)
    ).items


@pytest.mark.parametrize("calls", [(), ("read", "read", "unavailable")])
def test_saved_request_atomic_all_child_admission_and_usage(tmp_path, rig, calls):
    session, path = rig
    wire = Wire(calls)
    task = definition(tmp_path, wire, pricing=AgentTokenPricing(1, 2, 3, 4))
    conversation, cutoff, task_id = admit(session, task, seed(tmp_path, ("read",)))
    done, publications = completion(session, task_id)
    independent = []

    def persisted(publication):
        if not publications or publications[-1].seq != publication.seq:
            return
        if path is not None:
            with sqlite3.connect(path) as connection:
                independent.append(
                    (
                        connection.execute(
                            f"SELECT count(*) FROM {TABLES['entries']}"
                        ).fetchone()[0],
                        connection.execute(
                            f"SELECT count(*) FROM {TABLES['tasks']} WHERE id != ?",
                            (task_id,),
                        ).fetchone()[0],
                        connection.execute(
                            f"SELECT status FROM {TABLES['tasks']} WHERE id=?",
                            (task_id,),
                        ).fetchone()[0],
                    )
                )

    session.subscribe_commits(persisted)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    assert wire.calls == []
    scheduler.resume()
    assert done.wait(10)
    rows = [r.to_tree() for r in scheduler.inspect()]
    parent = next(r for r in rows if r["id"] == task_id)
    assert parent["state"]["status"] == ("waiting" if calls else "terminal"), parent
    assert len(wire.calls) == 1
    model_entries = entries(session, conversation)
    assistant_entry = next(
        e for e in model_entries if e.to_tree()["kind"] == "pi.assistant"
    )
    assistant = decode_canonical(assistant_entry.to_tree()["model"][0])
    assert assistant.content.value == "done"
    assert assistant.usage.input == 16
    assert assistant.usage.output == 10
    assert assistant.usage.reasoning == 3
    assert assistant.usage.cache_read == 4
    assert assistant.usage.cost.total == pytest.approx((16 + 20 + 12) / 1e6)
    ledger = session.snapshot(USAGE_DOC, conversation_id=conversation).value.thaw()
    assert list(ledger["settlements"].values()) == [assistant_entry.id]
    assert ledger["models"]["openai/gpt-test"]["reasoning"] == 3
    provider_id = session.snapshot(
        PROVIDER_DOC, conversation_id=conversation
    ).value.thaw()["sessionId"]
    receipt = assistant_entry.to_tree()["data"]["receipt"]
    assert receipt["identity"]["cutoff"] == cutoff
    saved_request = decode_canonical(assistant_entry.to_tree()["data"]["request"])
    assert saved_request.request.session_id == provider_id
    assert saved_request.request.cache_retention is None
    children = [r for r in rows if r["kind"] == "pi.tool"]
    assert len(children) == (2 if calls else 0)
    if calls:
        assert [r["input"] for r in children] == [
            {"kind": "model", "assistant": assistant_entry.id, "callId": f"call:{i}"}
            for i in range(2)
        ]
        assert all(
            r["owner"] == task_id
            and r["conversationId"] == conversation
            and r["version"] == 2
            for r in children
        )
        assert parent["state"]["on"] == [r["id"] for r in children]
        round_state = session.snapshot(ROUND_DOC, task_id=task_id).value.thaw()
        assert round_state["tools"] == parent["state"]["on"]
        assert [c.arguments_json.value for c in assistant.tool_calls] == [
            ' { "x":  1 } '
        ] * 3
    if path is not None:
        assert independent == [
            (3 if calls else 2, 2 if calls else 0, "waiting" if calls else "terminal")
        ]
    scheduler.resume()
    assert len(wire.calls) == 1
    session.close(timeout=10)
    if path is not None:
        # Terminal and waiting inspection do not reconstruct auth or material.
        for material in tmp_path.glob("auth.json*"):
            material.unlink()
        reopened = Session(open_sqlite_storage(str(path), mode="open"))
        try:
            replay = TaskScheduler(reopened, [])
            replay.open()
            replay.resume()
            assert [r.to_tree() for r in replay.inspect()] == rows
            assert len(wire.calls) == 1
            assert (
                reopened.snapshot(USAGE_DOC, conversation_id=conversation).value.thaw()
                == ledger
            )
        finally:
            reopened.close(timeout=10)


def test_private_transport_exception_never_enters_fault(tmp_path, rig):
    session, _ = rig
    wire = Wire(failure=True)
    task = definition(tmp_path, wire)
    _, _, task_id = admit(session, task, seed(tmp_path))
    done, _ = completion(session, task_id)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    scheduler.resume()
    assert done.wait(10)
    row = next(r.to_tree() for r in scheduler.inspect() if r.id == task_id)
    assert row["state"]["outcome"]["status"] == "faulted"
    assert (
        row["state"]["outcome"]["error"]["message"]
        == "Durable generation is incompatible"
    )
    assert "private transport" not in json.dumps(row)


def test_provider_identity_is_per_conversation_and_reused_by_later_generation(
    tmp_path, rig
):
    from pipy_harness.native.agent.content import ProductContent
    from pipy_harness.native.agent.messages import AgentUserMessage
    from pipy_harness.native.durable.canonical_codec import encode_canonical
    from pipy_harness.native.durable.generation import message_entry

    session, _ = rig
    wire = Wire()
    task = definition(tmp_path, wire)
    first, _, first_id = admit(session, task, seed(tmp_path))
    second, _, second_id = admit(session, task, seed(tmp_path))
    first_done, _ = completion(session, first_id)
    second_done, _ = completion(session, second_id)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    scheduler.resume()
    assert first_done.wait(10) and second_done.wait(10)
    first_provider = session.snapshot(PROVIDER_DOC, conversation_id=first).value.thaw()[
        "sessionId"
    ]
    second_provider = session.snapshot(
        PROVIDER_DOC, conversation_id=second
    ).value.thaw()["sessionId"]
    assert first_provider != second_provider
    session.commit(
        lambda tx: tx.append_entry(
            first, message_entry(AgentUserMessage(ProductContent("another")))
        )
    )
    later = session.commit(
        lambda tx: tx.create_task(
            task,
            encode_canonical(seed(tmp_path)),
            ownership={"kind": "conversation"},
            conversation_id=first,
        )
    )
    done, _ = completion(session, later)
    # The scheduler may finish between admission and subscription; its actual
    # task record, rather than a timing assumption, decides whether to wait.
    current = session.commit(lambda tx: tx.task(later)).to_tree()
    assert current["state"]["status"] == "terminal" or done.wait(10)
    assert (
        session.snapshot(PROVIDER_DOC, conversation_id=first).value.thaw()["sessionId"]
        == first_provider
    )
    assert len(wire.calls) == 3
    ledger = session.snapshot(USAGE_DOC, conversation_id=first).value.thaw()
    assert len(ledger["settlements"]) == 2
    assert ledger["models"]["openai/gpt-test"]["input"] == 32


def test_all_unavailable_round_is_an_empty_durable_wait_without_dispatch(tmp_path, rig):
    from dataclasses import replace

    from pipy_harness.native.agent.request import snapshot_provider_request

    session, _ = rig
    wire = Wire(("unavailable",))
    task = definition(tmp_path, wire)
    snapshot = snapshot_provider_request(
        replace(seed(tmp_path).request, cache_retention="long")
    )
    conversation, _, task_id = admit(session, task, snapshot)
    done, _ = completion(session, task_id)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    scheduler.resume()
    assert done.wait(10)
    parent = next(row.to_tree() for row in scheduler.inspect() if row.id == task_id)
    assert parent["state"]["status"] == "waiting" and parent["state"]["on"] == []
    assert len(scheduler.inspect()) == 1
    source = [entry.to_tree() for entry in entries(session, conversation)]
    assert [entry["kind"] for entry in source].count("pi.tool-result") == 1
    answer = next(entry for entry in source if entry["kind"] == "pi.assistant")
    assert decode_canonical(answer["data"]["request"]).request.cache_retention == "long"
    scheduler.resume()
    assert len(wire.calls) == 1


@pytest.mark.parametrize(
    "failed,fallback", [(False, False), (True, False), (False, True)]
)
def test_rich_original_receipt_and_saved_tier_usage_are_committed_once(
    tmp_path, rig, monkeypatch, failed, fallback
):
    from datetime import datetime, timedelta, timezone

    from pipy_harness.native.agent.content import TextContent, ThinkingContent
    from pipy_harness.native.agent.usage import (
        AgentProviderUsageSample,
        AgentTokenPricingTier,
        message_usage,
    )
    from pipy_harness.native.durable import generation
    from pipy_harness.native.durable.canonical_codec import encode_canonical
    from pipy_harness.native.durable.checkpoint_codec import decode_checkpoint
    from pipy_harness.native.models import ProviderResult, ProviderToolCall
    from pipy_harness.status import HarnessStatus

    session, _ = rig
    when = datetime(
        2026, 10, 22, 12, 34, 56, 789, timezone(timedelta(hours=2), "offset"), fold=1
    )
    call = ProviderToolCall(
        "call:rich", "read", ' { "x": [1,  2] } \n', "call-signature"
    )
    result = ProviderResult(
        HarnessStatus.FAILED if failed else HarnessStatus.SUCCEEDED,
        "openai",
        "gpt-test",
        when,
        when + timedelta(seconds=2),
        "done",
        usage={
            "input_tokens": 100,
            "output_tokens": 20,
            "cached_tokens": 10,
            "cache_write_tokens": 30,
            "cache_write_1h_tokens": 5,
            "reasoning_tokens": 7,
            "total_tokens": 160,
        },
        metadata={
            "deep": [{"zero": -0.0, "float": 1.0, "integer": 1, "present": None}]
        },
        error_type="fixture-error" if failed else None,
        error_message="fixture failed" if failed else None,
        tool_calls=() if fallback else (call,),
        content_blocks=()
        if fallback
        else (
            ThinkingContent("thought", "thinking-signature", True),
            call,
            TextContent("done", "text-signature"),
        ),
        provider_thinking_level="high",
        api="openai-completions",
    )

    class RichProvider:
        name = "openai"
        model_id = "gpt-test"
        supports_tool_calls = True

        def complete(self, request, **kwargs):
            kwargs["reasoning_sink"]("fallback thinking")
            return result

    monkeypatch.setattr(
        generation, "reconstruct_provider", lambda *args, **kwargs: RichProvider()
    )
    pricing = AgentTokenPricing(1, 2, 3, 4, (AgentTokenPricingTier(100, 5, 6, 7, 8),))
    task = definition(tmp_path, Wire(), pricing=pricing)
    conversation, _, task_id = admit(session, task, seed(tmp_path, ("read",)))
    done, _ = completion(session, task_id)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    scheduler.resume()
    assert done.wait(10)
    answer = next(
        entry.to_tree()
        for entry in entries(session, conversation)
        if entry.to_tree()["kind"] == "pi.assistant"
    )
    receipt = decode_checkpoint(
        answer["data"]["receipt"],
        name="pi.generation",
        version=1,
        phase="settle",
        role="provider_outcome",
    )
    assert encode_canonical(receipt.payload.result) == encode_canonical(result)
    assistant = decode_canonical(answer["model"][0])
    expected_usage = message_usage(
        AgentProviderUsageSample.from_mapping(result.usage), pricing
    )
    assert encode_canonical(assistant.usage) == encode_canonical(expected_usage)
    assert assistant.usage.cache_write_1h == 5 and assistant.usage.reasoning == 7
    assert assistant.provider_thinking_level == "high"
    assert assistant.ordered_content()[0].thinking == (
        "fallback thinking" if fallback else "thought"
    )
    ledger = session.snapshot(USAGE_DOC, conversation_id=conversation).value.thaw()
    assert (
        ledger["models"]["openai/gpt-test"]["cost"]["total"]
        == expected_usage.cost.total
    )
    assert list(ledger["settlements"].values()) == [answer["id"]]
    assert len(
        [row for row in scheduler.inspect() if row.to_tree()["kind"] == "pi.tool"]
    ) == int(not failed and not fallback)
