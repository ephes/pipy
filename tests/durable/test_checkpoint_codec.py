"""DE06b real runtime phase activation, rich fields and refusal before effects."""

import copy
import threading
from dataclasses import replace

import pytest
from test_canonical_codec import OUTCOME, PROVIDER, SNAPSHOT

from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.durable.canonical_codec import encode_canonical
from pipy_harness.native.durable.checkpoint_codec import (
    CheckpointCodecError,
    commit_checkpoint,
    decode_checkpoint,
    define_canonical_task,
    encode_checkpoint,
)
from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.provider_checkpoint import prepare_provider_settings
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage

IDENTITY = {"attempt": "attempt:1", "request": "request:1", "cutoff": 17}
SPEC = NativeModelSpec("provider", "model", "Model", "openai-completions")


def settings(tmp_path):
    return prepare_provider_settings(
        SNAPSHOT,
        SPEC,
        auth_path=tmp_path / "auth.json",
        env={},
        runtime_api_key="fake-key",
    )


def frame(phase, role, payload, saved=None):
    return encode_checkpoint(
        name="canonical",
        version=-1.5,
        phase=phase,
        role=role,
        identity=IDENTITY,
        payload=payload,
        settings=saved,
    )


async def abort(*args):
    raise AssertionError("DE16")


def create(session, definition, value):
    conversation = session.commit(
        lambda tx: tx.create_conversation({"kind": "ownerless"})
    ).id
    return session.commit(
        lambda tx: tx.create_task(
            definition,
            value.to_tree(),
            ownership={"kind": "conversation"},
            conversation_id=conversation,
        )
    )


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_real_phases_own_decode_rich_request_and_both_outcomes(tmp_path, backend):
    saved = settings(tmp_path)
    request = frame("request", "request", SNAPSHOT, saved)
    provider = frame(
        "provider", "provider_outcome", ProviderTurnOutcome(result=PROVIDER), saved
    )
    tool = frame("tool", "tool_outcome", OUTCOME)
    seen = []
    done = threading.Event()

    async def prepare(value, runtime, context):
        assert value.payload is None
        assert await runtime.memo("identity", IDENTITY) == IDENTITY
        assert await runtime.memo("identity", {"loser": True}) == IDENTITY
        await commit_checkpoint(runtime, request)

    async def requested(value, runtime, context):
        seen.append(encode_canonical(value.payload))
        assert value.settings.to_tree() == saved.to_tree()
        await commit_checkpoint(runtime, provider)

    async def received(value, runtime, context):
        seen.append(encode_canonical(value.payload))
        await commit_checkpoint(runtime, tool)

    async def settled(value, runtime, context):
        seen.append(encode_canonical(value.payload))
        await commit_checkpoint(runtime, tool, terminal=True)
        done.set()

    definition = define_canonical_task(
        "canonical",
        -1.5,
        initial_phase="prepare",
        phases={
            "prepare": ("prepare", prepare),
            "request": ("request", requested),
            "provider": ("provider_outcome", received),
            "tool": ("tool_outcome", settled),
        },
        abort=abort,
    )
    storage = (
        MemoryStorage()
        if backend == "memory"
        else open_sqlite_storage(str(tmp_path / "session.sqlite"), mode="create")
    )
    session = Session(storage)
    try:
        create(session, definition, frame("prepare", "prepare", None))
        scheduler = TaskScheduler(session, [definition])
        scheduler.open()
        assert seen == []
        scheduler.resume()
        assert done.wait(5)
    finally:
        session.close(timeout=5)
    assert seen == [
        encode_canonical(SNAPSHOT),
        encode_canonical(ProviderTurnOutcome(result=PROVIDER)),
        encode_canonical(OUTCOME),
    ]
    result = scheduler.inspect()[0].to_tree()["state"]["outcome"]["result"]
    decoded = decode_checkpoint(
        result, name="canonical", version=-1.5, phase="tool", role="tool_outcome"
    )
    assert decoded.payload.execution_seconds == OUTCOME.execution_seconds
    if backend == "sqlite":
        reopened = Session(
            open_sqlite_storage(str(tmp_path / "session.sqlite"), mode="open")
        )
        try:
            replay = TaskScheduler(reopened, [definition])
            replay.open()
            replay.resume()
            assert replay.inspect()[0].to_tree()["state"]["outcome"]["result"] == result
            assert len(seen) == 3
        finally:
            reopened.close(timeout=5)


@pytest.mark.parametrize(
    "mutation", ["version", "role", "phase", "root", "unknown", "identity", "settings"]
)
def test_wrong_envelope_refuses_before_real_phase_effect(tmp_path, mutation):
    original = frame("request", "request", SNAPSHOT, settings(tmp_path)).to_tree()
    bad = copy.deepcopy(original)
    if mutation == "version":
        bad["envelope_version"] = 2
    elif mutation == "role":
        bad["role"] = "provider_outcome"
    elif mutation == "phase":
        bad["phase"] = "settle"
    elif mutation == "root":
        bad["payload"] = encode_canonical(ProviderTurnOutcome(result=PROVIDER))
    elif mutation == "unknown":
        bad["secret-value"] = "fake-private"
    elif mutation == "identity":
        bad["identity"]["request"] = None
    else:
        bad["settings"]["semantics"]["thinking_disabled"] = "fake-private"
    effects = []

    async def handler(*args):
        effects.append("called")

    definition = define_canonical_task(
        "canonical",
        -1.5,
        initial_phase="request",
        phases={"request": ("request", handler)},
        abort=abort,
    )
    session = Session(MemoryStorage())
    try:
        conversation = session.commit(
            lambda tx: tx.create_conversation({"kind": "ownerless"})
        ).id
        with pytest.raises(CheckpointCodecError) as error:
            session.commit(
                lambda tx: tx.create_task(
                    definition,
                    bad,
                    ownership={"kind": "conversation"},
                    conversation_id=conversation,
                )
            )
        assert "fake-private" not in str(error.value)
        assert effects == []
    finally:
        session.close(timeout=5)


def test_encode_owns_every_nested_value_before_suspension(tmp_path):
    source = replace(PROVIDER, usage={"deep": [{"counter": 1}]})
    encoded = frame(
        "provider",
        "provider_outcome",
        ProviderTurnOutcome(result=source),
        settings(tmp_path),
    )
    source.usage["deep"][0]["counter"] = 9
    first = encoded.payload
    assert first.result.usage["deep"][0]["counter"] == 1
    first.result.usage["deep"][0]["counter"] = 8
    assert encoded.payload.result.usage["deep"][0]["counter"] == 1
    assert OwnedJson(encoded.to_tree()) == OwnedJson(encoded.to_tree())


@pytest.mark.parametrize("after", [False, True])
def test_ack_failure_seals_original_owner_and_reuses_persisted_envelope(
    tmp_path, after
):
    import asyncio

    from pipy_harness.native.durable.scheduler import InvocationEnded
    from pipy_harness.native.durable.session import SessionClosedError

    saved = settings(tmp_path)
    prepared = frame("request", "request", SNAPSHOT, saved)
    calls = []
    runtime_holder = []
    done = threading.Event()

    async def prepare(value, runtime, context):
        runtime_holder.append(runtime)
        await commit_checkpoint(runtime, prepared)
        calls.append("after-ack")

    async def request(value, runtime, context):
        calls.append("request")
        await commit_checkpoint(
            runtime, frame("result", "tool_outcome", OUTCOME), terminal=True
        )
        done.set()

    definition = define_canonical_task(
        "canonical",
        -1.5,
        initial_phase="prepare",
        phases={"prepare": ("prepare", prepare), "request": ("request", request)},
        abort=abort,
    )
    path = tmp_path / "ack.sqlite"
    inner = open_sqlite_storage(str(path), mode="create")

    class Ack:
        armed = False

        def __getattr__(self, name):
            return getattr(inner, name)

        def commit(self, writes):
            selected = self.armed and any(
                write.to_tree()
                .get("value", {})
                .get("state", {})
                .get("checkpoint", {})
                .get("role")
                == "request"
                for write in writes
            )
            if selected:
                self.armed = False
                if after:
                    inner.commit(writes)
                raise OSError("Synthetic lost checkpoint acknowledgement")
            return inner.commit(writes)

    storage = Ack()
    session = Session(storage)
    create(session, definition, frame("prepare", "prepare", None))
    scheduler = TaskScheduler(session, [definition])
    scheduler.open()
    storage.armed = True
    scheduler.resume()
    assert session.wait_closed(timeout=5).reason == "failed"
    assert calls == []
    with pytest.raises((InvocationEnded, SessionClosedError)):
        asyncio.run(runtime_holder[0].memo("late", 2))
    reopened = Session(open_sqlite_storage(str(path), mode="open"))
    try:
        again = TaskScheduler(reopened, [definition])
        again.open()
        assert again.inspect()[0].to_tree()["state"]["checkpoint"]["role"] == (
            "request" if after else "prepare"
        )
        again.resume()
        assert done.wait(5)
        assert calls == (["request"] if after else ["after-ack", "request"])
    finally:
        reopened.close(timeout=5)


def test_recovery_validates_corrupt_checkpoint_before_actual_phase_effect(tmp_path):
    effects = []
    done = threading.Event()

    async def handler(*args):
        effects.append("effect")

    definition = define_canonical_task(
        "canonical",
        -1.5,
        initial_phase="request",
        phases={"request": ("request", handler)},
        abort=abort,
    )
    session = Session(MemoryStorage())
    try:
        created = create(
            session,
            definition,
            frame("request", "request", SNAPSHOT, settings(tmp_path)),
        )

        def corrupt(tx):
            row = tx.task(created).to_tree()
            row["state"]["checkpoint"]["payload"] = encode_canonical(OUTCOME)
            tx.set_task(row)

        session.commit(corrupt)

        def observe(publication):
            for change in publication.changes:
                tree = change.to_tree()
                if tree.get("value", {}).get("state", {}).get("status") == "terminal":
                    done.set()

        session.observe_commits(observe)
        scheduler = TaskScheduler(session, [definition])
        scheduler.open()
        scheduler.resume()
        assert done.wait(5)
        assert effects == []
        assert (
            scheduler.inspect()[0].to_tree()["state"]["outcome"]["status"] == "faulted"
        )
    finally:
        session.close(timeout=5)
