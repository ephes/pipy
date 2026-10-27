"""C1 actual interrupted receipt readers; B controls remain real producers."""

import asyncio
import json

import pytest
import test_tool_fact2_reader_matrix as baseline
from interrupted_reader_support import (
    ALL_READERS,
    C1_MUTATIONS,
    COMMON_MUTATIONS,
    RETAINED_DETAILS,
    TEXT,
    assert_public_no_private,
    counts,
    cut_actual_sdk,
    interrupted_base,
    mutated_world,
    public_tree,
    read_captured,
    recover_actual_sdk,
)
from test_tool_fact2_reader_matrix import actual_fact2 as actual_fact2

from pipy_harness.native.agent.tools import ToolExecutionInterruption
from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.native.durable.records import TaskRecord


@pytest.mark.parametrize("reader", baseline.READERS)
def test_b_actual_fact2_positive_reader(actual_fact2, reader):
    baseline.read(actual_fact2, reader)


@pytest.mark.parametrize(
    "reader",
    [
        "async_graph_result",
        "tasktx_round",
        "tasktx_call",
        "reconcile",
        "completed_capture",
    ],
)
def test_b_actual_fact2_positive_session_reader(actual_fact2, reader):
    baseline.test_actual_session_reader_migration_on_captured_memory_world(
        actual_fact2, reader, "none"
    )


def test_b_actual_fact1_recorded_v1_positive(actual_fact2):
    baseline.test_actual_normal_fact1_and_recorded_v1_are_not_given_receipt2_duty(
        actual_fact2
    )


@pytest.mark.parametrize(
    "mutation", ["failed_without_result", "aborted", "faulted", "orphaned"]
)
def test_b_generic_terminal_noauthority(actual_fact2, mutation):
    baseline.test_generic_terminal_child_has_no_model_result_authority(
        actual_fact2, mutation
    )


def test_c1_actual_sdk_before_invoke_frontier(tmp_path, monkeypatch):
    async def scenario():
        base = await cut_actual_sdk(tmp_path, monkeypatch)
        await recover_actual_sdk(base, monkeypatch)
        before = base["before_resume"]
        source = next(r for r in before["rows"] if r["id"] == base["child_id"])
        witness = {
            "source_phase": source["state"]["checkpoint"]["phase"],
            "source_status": source["state"]["status"],
            "resume_status": base["result"].status,
            "prepared": base["prepared"],
            "invoked": base["invoked"],
            "providers": len(base["wire"].calls),
            "produced_settlements": len(base["captures"]),
            "cut": "actual public SDK execute commit then healthy owner close; no SIGKILL",
        }
        (tmp_path / "frontier-witness.json").write_text(json.dumps(witness, indent=2))
        assert source["state"]["checkpoint"]["phase"] == "execute"
        interrupted_base(base)

    asyncio.run(asyncio.wait_for(scenario(), 25))


@pytest.fixture(scope="module")
def actual_interrupted(tmp_path_factory):
    """Actual SDK receipt, with an ACKed retained prefix before healthy close."""
    root = tmp_path_factory.mktemp("actual-c1-interrupted")
    patch = pytest.MonkeyPatch()

    async def produce():
        base = await cut_actual_sdk(root, patch, with_prefix=True)
        await recover_actual_sdk(base, patch)
        return interrupted_base(base)

    try:
        base = asyncio.run(asyncio.wait_for(produce(), 25))
    finally:
        patch.undo()
    assert base["prepared"] == ["first", "second"]
    assert base["invoked"] == ["first", "second"]  # No first-tool reissue.
    assert len(base["wire"].calls) == 2
    yield base
    assert counts(base) == base["baseline_counts"]


def test_c1_actual_sdk_retains_committed_prefix_and_next_answer(actual_interrupted):
    base = actual_interrupted
    full = base["receipt"]["nativeToolOutput"]
    outcome = decode_canonical(full["outcome"])
    assert full["ending"] == "interrupted"
    assert set(full["recovery"]) == {"reason", "attempts", "restarts"}
    assert full["recovery"]["reason"] == "interrupted"
    for key in ("attempts", "restarts"):
        value = full["recovery"][key]
        assert value is None or (type(value) is int and 0 <= value <= 2**53 - 1)
    assert outcome.result.content.value == "retained interruption prefix"
    assert outcome.result.details == RETAINED_DETAILS
    assert full["diagnostics"] == [
        {"severity": "info", "message": "retained note", "code": "c1-note"}
    ]
    assert outcome.result.is_error and not outcome.result.terminate
    assert not outcome.cancelled and not outcome.malformed_arguments
    assert outcome.interruption is ToolExecutionInterruption.SETTLED
    assert outcome.execution_seconds is None
    assert base["child"]["state"]["outcome"]["error"] == {"message": TEXT}
    model = decode_canonical(base["entries"][base["entry_id"]].to_tree()["model"][0])
    assert model.content.value.startswith("retained interruption prefix")
    assert TEXT in model.content.value and "<harness>" in model.content.value
    # Real OpenAI-adapter next request, not a manually constructed projection.
    wire_payload = json.dumps(base["wire"].calls[1][1]["body"], ensure_ascii=True)
    assert TEXT in wire_payload and "retained interruption prefix" in wire_payload
    assert base["result"].status == "terminal"
    assert base["result"].inspection.outcome == "completed"
    for payload in (
        base["metadata"],
        base["result"].completed,
        base["wire"].calls[1][1]["body"],
    ):
        assert_public_no_private(payload, base)
    with pytest.raises(AssertionError):
        assert_public_no_private(
            {"actual_saved_intent": base["receipt"]["intent"]}, base
        )


@pytest.mark.parametrize("reader", ALL_READERS)
def test_c1_actual_interrupted_all_readers_positive(actual_interrupted, reader):
    read_captured(actual_interrupted, reader)


@pytest.mark.parametrize("reader", ALL_READERS)
@pytest.mark.parametrize("mutation", C1_MUTATIONS)
def test_c1_schema_valid_receipt_cause_counter_and_control_refuses(
    actual_interrupted, reader, mutation
):
    # Qualification is separate from the exception-catching consumer assertion.
    mutated_world(actual_interrupted, None, mutation)
    read_captured(actual_interrupted, reader, mutation, accepted=False)


@pytest.mark.parametrize("reader", ALL_READERS)
@pytest.mark.parametrize("mutation", COMMON_MUTATIONS)
def test_c1_original_graph_arena_and_control_guards_retained(
    actual_interrupted, reader, mutation
):
    mutated_world(actual_interrupted, None, mutation)
    read_captured(actual_interrupted, reader, mutation, accepted=False)


@pytest.mark.parametrize("reader", ALL_READERS)
@pytest.mark.parametrize(
    "mutation",
    ["attempts_null", "restarts_null", "attempts_safe_max", "restarts_safe_max"],
)
def test_c1_explicit_null_and_safe_receipt_counter_boundary(
    actual_interrupted, reader, mutation
):
    read_captured(actual_interrupted, reader, mutation)


@pytest.mark.parametrize("reader", ALL_READERS)
@pytest.mark.parametrize(
    "mutation",
    [
        "task_restart_absent",
        "task_restart_float",
        "task_restart_negative",
        "task_restart_unsafe",
    ],
)
def test_c1_valid_receipt_counter_authority_ignores_current_task_counter(
    actual_interrupted, reader, mutation
):
    # No synthesis: only reading a real saved receipt. Current-row numeric values
    # are storage-schema-valid yet do not replace the receipt's saved counters.
    changed = mutated_world(actual_interrupted, None, mutation)
    assert (
        changed["entries"][actual_interrupted["entry_id"]].to_tree()
        == actual_interrupted["entries"][actual_interrupted["entry_id"]].to_tree()
    )
    read_captured(actual_interrupted, reader, mutation)


def test_c1_task_boolean_restart_is_record_schema_failure_not_reader_evidence(
    actual_interrupted,
):
    import copy

    row = copy.deepcopy(actual_interrupted["child"])
    row["restarts"] = True
    with pytest.raises(ValueError):
        TaskRecord(row)


@pytest.mark.parametrize(
    "reader", ["child_result_id", "completed_prefix", "inspection_terminate"]
)
def test_c1_completing_has_no_terminal_result_authority(actual_interrupted, reader):
    result = read_captured(actual_interrupted, reader, "completing")
    if reader == "completed_prefix":
        assert actual_interrupted["entry_id"] not in result[0]
    else:
        assert result is None


@pytest.mark.parametrize(
    "mutation", ["failed_without_result", "aborted", "faulted", "orphaned"]
)
def test_c1_generic_terminal_is_not_interrupted_authority(actual_interrupted, mutation):
    # These controls retain the actual receipt but remove terminal model authority.
    baseline.test_generic_terminal_child_has_no_model_result_authority(
        actual_interrupted, mutation
    )


@pytest.mark.parametrize(
    "reader",
    [
        r
        for r in ALL_READERS
        if r not in ("completed_closed_links", "completed_capture")
    ],
)
def test_c1_slot_cannot_name_foreign_child(actual_interrupted, reader):
    mutated_world(actual_interrupted, None, "slot_task_foreign")
    read_captured(actual_interrupted, reader, "slot_task_foreign", accepted=False)


@pytest.mark.parametrize("reader", ["tasktx_round", "tasktx_call", "reconcile"])
def test_c1_actual_round_cannot_alias_same_child_twice(actual_interrupted, reader):
    read_captured(actual_interrupted, reader, "slot_task_alias", accepted=False)


def test_c1_repeated_public_terminal_reopen_does_not_reissue(actual_interrupted):
    from test_product_session import profile

    from pipy_harness.sdk import open_durable_product_session

    base = actual_interrupted
    before = counts(base)

    async def run():
        for _ in range(2):
            product = await open_durable_product_session(
                base["path"],
                workspace=base["root"],
                profile=profile(base["root"], base["wire"]),
                tools=base["registry"],
            )
            try:
                result = await product.resume()
                assert result.status == "terminal"
                # Projection revision/publication sequence belong to this Session's
                # observer; compare every persisted completed payload field.
                for field in (
                    "conversation_id",
                    "messages",
                    "rounds",
                    "outcomes",
                    "usage",
                ):
                    assert public_tree(getattr(result.completed, field)) == public_tree(
                        getattr(base["result"].completed, field)
                    )
                assert_public_no_private(result.completed, base)
            finally:
                await product.close()

    asyncio.run(asyncio.wait_for(run(), 20))
    assert counts(base) == before


def test_actual_normal_malformed_arguments_intent_none_remains_old_abi(
    tmp_path, monkeypatch
):
    from interrupted_reader_support import produce_normal_malformed_sdk, read_predicate

    base = asyncio.run(
        asyncio.wait_for(produce_normal_malformed_sdk(tmp_path, monkeypatch), 20)
    )
    saved = base["saved"]
    assert saved["version"] == 1 and saved["intent"] is None
    assert saved["nativeToolOutput"]["version"] == 1
    raw = decode_canonical(saved["nativeToolOutput"]["outcome"])
    assert raw.malformed_arguments and raw.result.is_error
    assert not raw.result.terminate
    assert base["child"]["state"]["outcome"]["status"] == "completed"
    assert set(base["entries"][base["entry_id"]].to_tree()["data"]) == {
        "nativeToolOutput",
        "call",
        "key",
    }
    for reader in baseline.READERS:
        read_predicate(base, reader)
    assert base["prepared"] == base["invoked"] == ["second"]
    assert len(base["wire"].calls) == 2
