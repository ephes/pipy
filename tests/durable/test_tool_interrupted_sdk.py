"""Actual public cold recovery cuts; synthetic transports and counted effects."""

import hashlib
import json
import signal
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
import sqlite_child_support as child_support
from interrupted_cut_support import (
    NOTICE,
    PREPARED_MARKER,
    SAVED_NAMES,
    archived_b_source,
    counts,
    keys,
    logical,
    provider_bodies,
    raw,
    validate_archived_b_actor,
)
from sqlite_child_support import Child

from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.generation import USAGE_DOC
from pipy_harness.native.durable.records import RecordError, task
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import _decode, _encode, open_sqlite_storage

CODE = r"""
from pathlib import Path
from interrupted_cut_support import child_main
child_main(Path(sys.argv[1]),sys.argv[2],sys.argv[3],sys.argv[4],sys.argv[5],say)
"""


def start_cut(root, cut="execute", replay="unsafe"):
    root.mkdir(exist_ok=True)
    with Child(CODE, str(root), "start", replay, "same", cut, home=root) as child:
        admitted = child.expect("admitted")
        event = child.expect("cut")
        assert event["stage"] == cut
        child.kill_group()
        assert child.wait() == -signal.SIGKILL
        child.wait_stdout_eof()
        evidence = child.evidence()
    evidence["admission"] = admitted
    (root / "start-process.json").write_text(json.dumps(evidence, indent=2))
    state = raw(root)
    if cut in ("execute", "effect", "progress-after", "receipt-after"):
        task = next(row for row in state["tasks"] if row["kind"] == "pi.tool")
        assert task["state"]["checkpoint"]["phase"] == (
            "settle" if cut == "receipt-after" else "execute"
        )
    assert event["native_origins"]
    return event


def cold(root, *, replay="unsafe", current="same", action="resume", cut="", older=None):
    original = child_support.PRELUDE
    if older is not None:
        # Child argv owns a prefix before ANY native import, including PRELUDE.
        lines = original.splitlines()
        for index, line in enumerate(lines):
            if line.startswith("sys.path[:0] ="):
                lines[index] = "sys.path[:0] = " + repr(
                    list(
                        map(
                            str,
                            (
                                older / "src",
                                older / "tests",
                                older / "tests/durable",
                                Path(__file__).resolve().parent,
                            ),
                        )
                    )
                )
        child_support.PRELUDE = "\n".join(lines) + "\n"
    try:
        child = Child(CODE, str(root), action, replay, current, cut, home=root)
    finally:
        child_support.PRELUDE = original
    with child:
        event = child.next_event()
        if event["event"] == "cut":
            child.kill_group()
            assert child.wait() == -signal.SIGKILL
            child.wait_stdout_eof()
            closed = None
        else:
            assert event["event"] in ("result", "refused")
            closed = child.expect("closed")
            assert closed["native_origins"]
            assert child.wait() == 0
            child.wait_stdout_eof()
        evidence = child.evidence()
    label = f"{action}-{current}-{cut or 'complete'}-{len(list(root.glob('*-process.json')))}"
    (root / (label + "-process.json")).write_text(json.dumps(evidence, indent=2))
    return event, closed


def usage(root):
    cid = next(
        row["conversationId"]
        for row in raw(root)["tasks"]
        if row["kind"] == "pi.generation"
    )
    session = Session(open_sqlite_storage(str(root / "session.sqlite"), mode="open"))
    try:
        return session.snapshot(USAGE_DOC, conversation_id=cid).value.thaw()
    finally:
        session.close()


def final_tool(root):
    data = raw(root)
    result = [row for row in data["entries"] if row["kind"] == "pi.tool-result"]
    assert len(result) == 1
    return result[0]


def assert_terminal_once(root, before, *, reissued=False):
    actual = counts(root)
    assert actual["prepares"] == before["prepares"] == 1
    assert actual["calls"] == (before["calls"] + 1 if reissued else before["calls"])
    assert (
        actual["applications"] == 1
        if reissued
        else actual["applications"] == before["applications"]
    )
    assert actual["providers"] == 2
    ledger = usage(root)
    assert len(ledger["settlements"]) == 2
    assert ledger["models"]["openai/gpt-test"]["input"] == 32
    assert ledger["models"]["openai/gpt-test"]["reasoning"] == 6
    final_tool(root)


def authfree_repeated_terminal(root):
    before, graph = counts(root), logical(root)
    (root / "auth/auth.json").unlink(missing_ok=True)
    (root / "config/models.json").unlink(missing_ok=True)
    for action in ("terminal", "cli-terminal", "terminal"):
        event, _ = cold(root, action=action)
        assert event["event"] == "result" and event["status"] == "terminal"
        if action == "cli-terminal":
            assert event["code"] == 0
    assert counts(root) == before
    assert logical(root) == graph
    assert not (root / "auth/auth.json").exists()
    assert not (root / "config/models.json").exists()


@pytest.mark.parametrize("cut", ["execute", "effect"])
def test_unchanged_safe_whole_catalog_cold_replay_is_existing_control(tmp_path, cut):
    event = start_cut(tmp_path, cut, "safe")
    before = counts(tmp_path)
    assert before["calls"] == before["applications"] == (cut == "effect")
    assert before["prepares"] == before["providers"] == 1
    result, _ = cold(tmp_path, replay="safe")
    assert result["event"] == "result" and result["status"] == "terminal", result
    assert_terminal_once(tmp_path, before, reissued=True)
    actual_keys = keys(tmp_path)
    assert len(set(actual_keys)) == 1
    if cut == "effect":
        assert actual_keys[0] == event["key"]
    authfree_repeated_terminal(tmp_path)


def test_committed_normal_receipt_wins_after_ack_loss_existing_control(tmp_path):
    event = start_cut(tmp_path, "receipt-after")
    before = counts(tmp_path)
    assert before["calls"] == before["applications"] == before["prepares"] == 1
    receipt = raw(tmp_path)["documents"]["pi.tool.receipt"][-1]
    assert hashlib.sha256(_encode(receipt)).hexdigest() == event["receipt_sha256"]
    result, _ = cold(tmp_path)
    assert result["event"] == "result" and result["status"] == "terminal", result
    assert_terminal_once(tmp_path, before)
    assert final_tool(tmp_path)["data"] == {"nativeToolReceipt": receipt}
    authfree_repeated_terminal(tmp_path)


@pytest.mark.parametrize("cut", ["execute", "effect"])
def test_unsafe_cold_sdk_no_old_invocation_interrupted_answer(tmp_path, cut):
    start_cut(tmp_path, cut)
    before = counts(tmp_path)
    result, _ = cold(tmp_path)
    assert result["event"] == "result" and result["status"] == "terminal", result
    assert_terminal_once(tmp_path, before)
    entry = final_tool(tmp_path)
    assert set(entry["data"]) == {"nativeToolReceipt"}
    full = entry["data"]["nativeToolReceipt"]["nativeToolOutput"]
    assert full["version"] == 2 and full["ending"] == "interrupted"
    assert full["recovery"] == {"reason": "interrupted", "attempts": 1, "restarts": 1}
    decoded = decode_canonical(full["outcome"])
    assert decoded.result.is_error and not decoded.result.terminate
    assert not decoded.cancelled and not decoded.malformed_arguments
    assert decoded.interruption.value == "settled" and decoded.execution_seconds is None
    assert NOTICE in decode_canonical(entry["model"][0]).content.value
    child = next(row for row in raw(tmp_path)["tasks"] if row["kind"] == "pi.tool")
    assert child["state"]["outcome"]["status"] == "failed"
    assert child["state"]["outcome"]["error"] == {"message": NOTICE}
    body = provider_bodies(tmp_path)[-1]
    assert [row["function"]["name"] for row in body["tools"]] == list(SAVED_NAMES)
    assert NOTICE in next(
        row["content"] for row in body["messages"] if row["role"] == "tool"
    )
    output = (tmp_path / "resume-output.json").read_text()
    for private in (
        PREPARED_MARKER,
        keys(tmp_path)[0] if keys(tmp_path) else "never-existing-private-key",
        str(tmp_path),
    ):
        assert private not in output and private not in json.dumps(body)
    authfree_repeated_terminal(tmp_path)


def interrupted_value(root):
    return final_tool(root)["data"]["nativeToolReceipt"]["nativeToolOutput"]


@pytest.mark.parametrize(
    "current", ["missing", "unsafe", "declaration", "version", "extra"]
)
@pytest.mark.parametrize("action", ["resume", "cli"])
def test_changed_execute_catalog_noninvoking_narrowed_request(
    tmp_path, current, action
):
    start_cut(tmp_path, "effect", "safe" if current != "extra" else "unsafe")
    before = counts(tmp_path)
    result, _ = cold(
        tmp_path,
        replay="safe" if current != "extra" else "unsafe",
        current=current,
        action=action,
    )
    assert result["event"] == "result" and result["status"] == "terminal", result
    assert_terminal_once(tmp_path, before)
    full = interrupted_value(tmp_path)
    assert full["ending"] == "interrupted"
    expected = list(SAVED_NAMES) if current == "extra" else ["later", "unused"]
    body = provider_bodies(tmp_path)[-1]
    assert [t["function"]["name"] for t in body["tools"]] == expected
    observed = [
        json.loads(line)
        for line in (tmp_path / "request-snapshots.jsonl").read_text().splitlines()
    ]
    assert observed[-1]["available_tools"] == expected
    assert observed[-1]["advertised_tool_names"] == expected
    authfree_repeated_terminal(tmp_path)


@pytest.mark.parametrize(
    "cut,replay,current",
    [
        ("child", "unsafe", "missing"),
        ("request", "unsafe", "missing"),
        ("execute", "safe", "unrelated_removed"),
    ],
)
def test_changed_catalog_unqualified_frontier_refuses_without_logical_mutation(
    tmp_path, cut, replay, current
):
    start_cut(tmp_path, cut, replay)
    before, graph = counts(tmp_path), logical(tmp_path)
    result, _ = cold(tmp_path, replay=replay, current=current)
    assert result["event"] == "refused" and result["code"] == "incompatible", result
    assert logical(tmp_path) == graph
    assert counts(tmp_path) == before
    # The same archive can still execute under its exact original authorization.
    result, _ = cold(tmp_path, replay=replay)
    assert result["event"] == "result", result
    assert result["status"] == "terminal"


def replace_restarts(root, value):
    with closing(sqlite3.connect(root / "session.sqlite")) as db:
        row = next(
            (id, _decode(record))
            for id, record in db.execute("SELECT id,record FROM tasks")
            if _decode(record)["kind"] == "pi.tool"
        )
        id, record = row
        record["restarts"] = value
        try:
            task(record)
            OwnedJson(record)
        except (RecordError, ValueError, TypeError):
            return False
        with db:
            db.execute("UPDATE tasks SET record=? WHERE id=?", (_encode(record), id))
        return True


@pytest.mark.parametrize(
    "value", [1.5, -1, 2**53, True], ids=["float", "negative", "unsafe", "bool"]
)
def test_receipt_absent_invalid_restart_qualifies_schema_then_mutationfree_refusal(
    tmp_path, value
):
    start_cut(tmp_path)
    schema_valid = replace_restarts(tmp_path, value)
    if value is True:
        assert not schema_valid
        return
    assert schema_valid
    before, graph = counts(tmp_path), logical(tmp_path)
    result, _ = cold(tmp_path)
    assert result["event"] == "refused" and result["code"] == "incompatible", result
    assert logical(tmp_path) == graph and counts(tmp_path) == before


def test_committed_receipt_wins_over_invalid_current_restart_counter(tmp_path):
    start_cut(tmp_path, "receipt-after")
    saved = raw(tmp_path)["documents"]["pi.tool.receipt"][-1]
    assert replace_restarts(tmp_path, 1.5)
    before = counts(tmp_path)
    result, _ = cold(tmp_path)
    assert result["event"] == "result" and result["status"] == "terminal", result
    assert_terminal_once(tmp_path, before)
    assert final_tool(tmp_path)["data"] == {"nativeToolReceipt": saved}


def test_progress_commit_before_callback_ack_retains_last_committed_details(tmp_path):
    cut = start_cut(tmp_path, "progress-after")
    assert not cut["callback_acknowledged"]
    before = counts(tmp_path)
    result, _ = cold(tmp_path)
    assert result["event"] == "result" and result["status"] == "terminal", result
    assert_terminal_once(tmp_path, before)
    decoded = decode_canonical(interrupted_value(tmp_path)["outcome"])
    assert decoded.result.details == {"retained": [None, False, 0]}


def test_archived_b_receipt_parser_blocks_and_c1_respects_historical_hold(tmp_path):
    older = archived_b_source(tmp_path)

    start_cut(tmp_path, "effect")
    before = counts(tmp_path)
    event, _ = cold(tmp_path, cut="interrupted-receipt-after")
    assert event["event"] == "cut", event
    saved = raw(tmp_path)["documents"]["pi.tool.receipt"][-1]
    assert saved["nativeToolOutput"]["ending"] == "interrupted"
    # Matching original catalog/profile reaches actual old receipt parser.
    result, closed = cold(tmp_path, older=older)
    validate_archived_b_actor(older, closed)
    assert result["event"] == "result" and result["status"] == "held", result
    child = next(row for row in raw(tmp_path)["tasks"] if row["kind"] == "pi.tool")
    assert child["state"]["checkpoint"]["intent"] is None
    assert child["state"]["checkpoint"]["phase"] == "blocked"
    graph = logical(tmp_path)
    result, _ = cold(tmp_path)
    assert result["event"] == "result" and result["status"] == "held", result
    assert logical(tmp_path) == graph
    assert counts(tmp_path) == before
    assert raw(tmp_path)["documents"]["pi.tool.receipt"][-1] == saved


@pytest.mark.parametrize("cut", ["execute", "effect"])
def test_unsafe_cold_cli_no_old_invocation_interrupted_answer(tmp_path, cut):
    start_cut(tmp_path, cut)
    before = counts(tmp_path)
    result, _ = cold(tmp_path, action="cli")
    assert result["code"] == 0 and result["status"] == "terminal", result
    assert_terminal_once(tmp_path, before)
    assert interrupted_value(tmp_path)["ending"] == "interrupted"
    assert NOTICE in next(
        row["content"]
        for row in provider_bodies(tmp_path)[-1]["messages"]
        if row["role"] == "tool"
    )
    authfree_repeated_terminal(tmp_path)


def test_interrupted_receipt_ack_loss_settles_saved_fact_despite_current_counter(
    tmp_path,
):
    start_cut(tmp_path, "effect")
    before = counts(tmp_path)
    event, _ = cold(tmp_path, cut="interrupted-receipt-after")
    assert event["event"] == "cut", event
    saved = raw(tmp_path)["documents"]["pi.tool.receipt"][-1]
    assert saved["nativeToolOutput"]["ending"] == "interrupted"
    assert saved["nativeToolOutput"]["recovery"] == {
        "reason": "interrupted",
        "attempts": 1,
        "restarts": 1,
    }
    assert replace_restarts(tmp_path, 1.5)
    result, _ = cold(tmp_path)
    assert result["event"] == "result" and result["status"] == "terminal", result
    assert_terminal_once(tmp_path, before)
    assert final_tool(tmp_path)["data"] == {"nativeToolReceipt": saved}
    authfree_repeated_terminal(tmp_path)
