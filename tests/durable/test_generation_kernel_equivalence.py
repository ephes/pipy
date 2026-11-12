"""Turn-kernel equivalence: the loop's kernel against the durable generation.

``AgentLoop`` settles provider results and admits tool calls through
``native/agent/turn.py``; the durable ``pi.generation`` task still has its own
copy (``_Generation.settle`` and ``_tool_round``) until DE22/DE25 adopt the
kernel. Each case runs one real durable generation, reads the committed
assistant entry and the provider outcome its receipt holds, and runs the
same outcome through the kernel.

Shared cells must agree on the committed assistant and on which calls run.
``EXPECTED_DELTAS`` names the cells where the durable path lacks the loop's
behaviour today; each records the slice that owns the adoption, and a test
pins today's difference so that adoption changes this file on purpose.
"""

import inspect

import pytest
from generation_support import Wire, admit, completion, definition, seed

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.loop_policy import (
    AgentToolPolicyAction,
    AgentToolPolicyDecision,
    AgentToolPolicyState,
    settle_tool_execution,
)
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentStopReason,
    AgentToolCall,
    AgentToolResultMessage,
)
from pipy_harness.native.agent.tools import ToolExecutionOutcome
from pipy_harness.native.agent.turn import (
    admit_tool_call,
    settle_provider_result,
    transform_tool_result,
)
from pipy_harness.native.agent.usage import AgentTokenPricing
from pipy_harness.native.durable.canonical_codec import (
    decode_canonical,
    encode_canonical,
)
from pipy_harness.native.durable.checkpoint_codec import decode_checkpoint
from pipy_harness.native.durable.generation import define_generation
from pipy_harness.native.durable.generation_tasks import (
    GENERATION_NAME,
    GENERATION_VERSION,
)
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.storage import EntryQuery
from pipy_harness.native.durable.tool_execution import define_tool_task
from pipy_harness.native.http import JsonResponse

PRICING = AgentTokenPricing(1, 2, 3, 4)

# Cell -> the slice whose durable adoption of the kernel closes it
# (docs/durable-loop-integration-design.md, sections 4.2 and 7).
EXPECTED_DELTAS = {
    "budget_exhaustion": "DE25",
    "unauthorized_tool": "DE25",
    "policy_transform": "DE22",
}


class _FailingWire(Wire):
    """A provider that answers with an HTTP error instead of a completion."""

    def post_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return JsonResponse(500, {"error": {"message": "overloaded"}})


@pytest.fixture(name="session")
def session_fixture(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    session = Session(MemoryStorage())
    yield session
    session.close(timeout=10)


def _durable_round(tmp_path, session, wire, offered):
    """Run one real generation; the committed facts the kernel is held to."""
    task = definition(tmp_path, wire, pricing=PRICING)
    conversation, _, task_id = admit(session, task, seed(tmp_path, offered))
    done, _ = completion(session, task_id)
    scheduler = TaskScheduler(session, [task])
    scheduler.open()
    scheduler.resume()
    assert done.wait(10)
    entries = [
        entry.to_tree()
        for entry in session.commit(
            lambda tx: tx.scan_entries(
                EntryQuery({"conversationId": conversation}), 100
            )
        ).items
    ]
    assistant_entry = next(e for e in entries if e["kind"] == "pi.assistant")
    receipt = assistant_entry["data"]
    outcome = decode_checkpoint(
        receipt["receipt"],
        name=GENERATION_NAME,
        version=GENERATION_VERSION,
        phase="settle",
        role="provider_outcome",
    ).payload
    rows = [row.to_tree() for row in scheduler.inspect()]
    return {
        "assistant": decode_canonical(assistant_entry["model"][0]),
        "snapshot": decode_canonical(receipt["request"]),
        "result": outcome.result,
        "runs": [row["input"]["callId"] for row in rows if row["kind"] == "pi.tool"],
        "refused": [
            decode_canonical(e["model"][0])
            for e in entries
            if e["kind"] == "pi.tool-result"
        ],
    }


def _kernel_admissions(snapshot, calls, budget):
    """The loop's admissions over one round, each admitted call settling."""
    state = AgentToolPolicyState(tool_budget=budget)
    admissions = []
    for call in calls:
        admitted = admit_tool_call(state, snapshot, call)
        admissions.append((call.provider_correlation_id, admitted))
        state = admitted.transition.state
        if admitted.transition.action is AgentToolPolicyAction.EXECUTE:
            # The loop's own settlement of a call that ran and settled.
            result = AgentToolResultMessage(
                f"pipy-tool-request-{len(admissions)}",
                call.tool_name,
                ProductContent("ok"),
                call.provider_correlation_id,
            )
            state = settle_tool_execution(state, ToolExecutionOutcome(result)).state
    return admissions


def _kernel_assistant(durable):
    return settle_provider_result(
        durable["snapshot"], durable["result"], PRICING
    ).assistant


def _same(left, right):
    return encode_canonical(left) == encode_canonical(right)


@pytest.mark.parametrize(
    "calls", [(), ("read",), ("read", "read")], ids=["text", "one", "two"]
)
def test_shared_cells_settle_and_admit_equally(tmp_path, session, calls):
    durable = _durable_round(tmp_path, session, Wire(calls), ("read",))
    assistant = _kernel_assistant(durable)
    assert type(assistant) is AgentAssistantMessage
    assert _same(assistant, durable["assistant"])
    admissions = _kernel_admissions(
        durable["snapshot"], assistant.tool_calls, budget=len(calls) or 1
    )
    executed = [
        call_id
        for call_id, admitted in admissions
        if admitted.transition.action is AgentToolPolicyAction.EXECUTE
    ]
    assert all(admitted.refusal is None for _, admitted in admissions)
    assert executed == durable["runs"] == [f"call:{i}" for i in range(len(calls))]
    assert durable["refused"] == []


def test_shared_cell_failed_provider_result(tmp_path, session):
    durable = _durable_round(tmp_path, session, _FailingWire(), ())
    assert durable["result"] is not None
    settlement = settle_provider_result(durable["snapshot"], durable["result"], PRICING)
    assert settlement.assistant.stop_reason is AgentStopReason.ERROR
    assert not settlement.counts_for_context
    assert _same(settlement.assistant, durable["assistant"])
    assert durable["runs"] == []


def test_expected_delta_budget_exhaustion(tmp_path, session):
    """DE25: the durable round has no tool budget; it runs every call."""
    durable = _durable_round(tmp_path, session, Wire(("read",) * 3), ("read",))
    assert _same(_kernel_assistant(durable), durable["assistant"])
    admissions = _kernel_admissions(
        durable["snapshot"], durable["assistant"].tool_calls, budget=2
    )
    assert [a.transition.action for _, a in admissions] == [
        AgentToolPolicyAction.EXECUTE,
        AgentToolPolicyAction.EXECUTE,
        AgentToolPolicyAction.BUDGET_EXHAUSTED,
    ]
    assert admissions[2][1].refusal == "tool budget exhausted (limit 2)"
    # Today's delta: the durable round admits the call over the budget.
    assert durable["runs"] == ["call:0", "call:1", "call:2"]


def test_expected_delta_unauthorized_tool(tmp_path, session):
    """DE25: an unoffered call is refused by both, with different results."""
    durable = _durable_round(
        tmp_path, session, Wire(("read", "unavailable")), ("read",)
    )
    assert _same(_kernel_assistant(durable), durable["assistant"])
    admissions = _kernel_admissions(
        durable["snapshot"], durable["assistant"].tool_calls, budget=2
    )
    assert [a.transition.action for _, a in admissions] == [
        AgentToolPolicyAction.EXECUTE,
        AgentToolPolicyAction.UNAUTHORIZED,
    ]
    assert admissions[1][1].refusal == "Tool unavailable not found"
    # The unauthorized call spends its place in the loop's budget.
    assert admissions[1][1].transition.state.invocations_this_turn == 2
    assert durable["runs"] == ["call:0"]
    # Today's delta: the durable round writes its own result for the call.
    (refused,) = durable["refused"]
    assert type(refused) is AgentToolResultMessage
    assert refused.provider_correlation_id == "call:1"
    assert refused.content == ProductContent("Tool is not available")
    assert refused.details == {"code": "tool_unavailable"}


class _RewritingPolicy:
    def before_execute(self, call, arguments, parent_call_id):
        return AgentToolPolicyDecision()

    def transform_result(self, call, result, arguments, parent_call_id):
        return ProductContent("rewritten")


def test_expected_delta_policy_transform():
    """DE22: the durable tasks take no tool policy, so no result transform."""
    call = AgentToolCall("call:0", "read", ProductContent("{}"))
    result = AgentToolResultMessage(
        "pipy-tool-request-0", "read", ProductContent("raw"), "call:0"
    )
    transformed = transform_tool_result(_RewritingPolicy(), call, result, {}, None)
    assert transformed.content == ProductContent("rewritten")
    # Today's delta: neither durable task definition has a policy input.
    for factory in (define_generation, define_tool_task):
        parameters = inspect.signature(factory).parameters
        assert not any("policy" in name for name in parameters), factory


def test_every_expected_delta_has_its_pinning_test():
    """Closing a delta removes its entry and its test together."""
    pinned = {
        name.removeprefix("test_expected_delta_")
        for name in globals()
        if name.startswith("test_expected_delta_")
    }
    assert pinned == set(EXPECTED_DELTAS)
    assert set(EXPECTED_DELTAS.values()) <= {"DE22", "DE25"}
