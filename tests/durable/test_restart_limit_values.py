"""Closed restart-limit tool cause, receipt intent rule and count text."""

from datetime import datetime, timezone

import pytest

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.identity import AGENT_TOOL_REQUEST_ID_PREFIX
from pipy_harness.native.agent.messages import AgentToolResultMessage
from pipy_harness.native.agent.tools import ToolExecutionOutcome
from pipy_harness.native.durable.generation_tasks import LIMITS_RESTARTS
from pipy_harness.native.durable.restart_policy import (
    RestartPolicy,
    cut_off_count,
    number_text,
)
from pipy_harness.native.durable.tool_output_values import (
    INTERRUPTED_TEXT,
    ending_error,
    ending_text,
    project_tool_output,
)
from pipy_harness.native.durable.tool_values import (
    DurableToolError,
    native_tool_output,
    read_native_tool_output,
    receipt,
)

CALL = {"kind": "model", "assistant": 3, "callId": "call:0"}


def outcome(text="partial"):
    return ToolExecutionOutcome(
        AgentToolResultMessage(
            AGENT_TOOL_REQUEST_ID_PREFIX + "limit",
            "keyed",
            ProductContent(text),
            "call:0",
            is_error=True,
        ),
        execution_seconds=None,
    )


def fact(reason="restart_limit", restarts=5, attempts=1):
    return native_tool_output(
        outcome(),
        datetime.now(timezone.utc),
        diagnostics=[],
        interrupted={"reason": reason, "attempts": attempts, "restarts": restarts},
    )


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0, "0"),
        (5, "5"),
        (5.0, "5"),
        (7.5, "7.5"),
        (2**53, "9007199254740992"),
        (1e20, "100000000000000000000"),
        (1e21, "1e+21"),
        (1e100, "1e+100"),
        (1e300, "1e+300"),
        (0.000001, "0.000001"),
        (1.5e-7, "1.5e-7"),
        (-2.5, "-2.5"),
    ],
)
def test_number_text_follows_javascript_number_strings(value, text):
    assert number_text(value) == text


def test_cut_off_count_wording():
    assert cut_off_count(1) == cut_off_count(1.0) == "once"
    assert cut_off_count(2) == "2 times in a row"
    assert cut_off_count(7.5) == "7.5 times in a row"


def test_built_in_kinds_end_themselves_and_the_policy_only_counts():
    # The built-in kinds end themselves in their phases; the scheduler faults
    # any other kind at the limit (R15-2c). Nothing parks any more.
    assert LIMITS_RESTARTS == {"pi.generation", "pi.tool"}
    assert not hasattr(RestartPolicy, "parked")
    policy = RestartPolicy(5)
    for kind in ("pi.tool", "pi.generation", "x"):
        row = {
            "kind": kind,
            "state": {"status": "pending", "checkpoint": {"phase": "run"}},
            "abortRequested": False,
            "restarts": 5,
        }
        assert policy.reached(row)
    assert not RestartPolicy(6).reached(row)
    assert not RestartPolicy(float("inf")).reached(row)
    assert not policy.reached({**row, "restarts": 4})
    del row["restarts"]
    assert not policy.reached(row)


def test_restart_limit_cause_text_error_and_projection():
    tree, decoded = read_native_tool_output(fact())
    assert tree["ending"] == "interrupted"
    text = "Tool execution was interrupted 5 times in a row; it is not run again."
    assert ending_text(tree) == text
    assert ending_error(tree) == {
        "message": text,
        "detail": {"reason": "restart_limit"},
    }
    projected = project_tool_output(tree, decoded)
    assert projected.content.value == "partial\n<harness>\n" + text + "\n</harness>"
    once, _ = read_native_tool_output(fact(restarts=1, attempts=None))
    assert ending_text(once).startswith("Tool execution was interrupted once;")
    plain, _ = read_native_tool_output(fact("interrupted", restarts=None))
    assert ending_text(plain) == INTERRUPTED_TEXT
    assert ending_error(plain) == {"message": INTERRUPTED_TEXT}


@pytest.mark.parametrize("restarts", [None, 0, -1, 1.5])
def test_restart_limit_cause_requires_a_positive_integer_count(restarts):
    with pytest.raises(DurableToolError):
        fact(restarts=restarts)


@pytest.mark.parametrize("reason", ["restart", "aborted", ""])
def test_interrupted_causes_stay_closed(reason):
    with pytest.raises(DurableToolError):
        fact(reason)


def test_only_a_restart_limit_ending_may_omit_its_intent():
    saved = {
        "version": 2,
        "intent": None,
        "call": CALL,
        "key": "pipy-tool-limit",
        "nativeToolOutput": fact(attempts=None),
    }
    tree, _ = receipt(saved)
    assert tree["intent"] is None
    with pytest.raises(DurableToolError):
        receipt({**saved, "nativeToolOutput": fact("interrupted")})
