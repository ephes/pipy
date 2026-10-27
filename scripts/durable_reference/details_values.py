"""Pure normal-result projection from retained committed facts and next history."""

from __future__ import annotations

import copy

IDS = ("absent", "null", "false", "zero", "scalar", "array", "mapping")
FIELDS = {
    "completed",
    "roles",
    "result",
    "next_result",
    "reopened_result",
    "provider_calls",
    "tool_calls",
    "prepares",
    "repeat_unchanged",
    "next_wire_tools",
}
MUTANTS = (
    "null_absent",
    "absent_null",
    "false_zero",
    "scalar_lost",
    "array_lost",
    "next_history",
    "reopen",
    "double_effect",
    "reprepare",
    "not_completed",
    "wire_details",
    "wire_correlation",
)


def fields(message, *, native=False):
    if native:
        state = message["details_state"]
        assert state in ("absent", "value")
        assert ("details" in message) is (state == "value")
        return {
            "state": state,
            **({"value": message["details"]} if state == "value" else {}),
        }
    return (
        {"state": "value", "value": message["details"]}
        if "details" in message
        else {"state": "absent"}
    )


def wire_tools(raw):
    messages = raw["wire"][-1]["messages"]
    calls = {
        call["id"]
        for message in messages
        if message["role"] == "assistant"
        for call in message.get("tool_calls", [])
    }
    # Raw wire IDs stay retained. Native's existing tc_ transport identities
    # differ from Pi's IDs; compare actual within-body correlation, not a
    # fabricated common raw identity or whole P15/DE33 transport parity.
    return [
        {
            "fields": sorted(message),
            "role": message["role"],
            "content": message["content"],
            "correlated": message["tool_call_id"] in calls,
        }
        for message in messages
        if message["role"] == "tool"
    ]


def native(raw, codec):
    messages = [row["message"] for row in raw["first"]["completed"]["messages"]]
    result = next(m for m in messages if m["role"] == "tool_result")
    reopened = next(
        row["message"]
        for row in raw["repeated"]["completed"]["messages"]
        if row["message"]["role"] == "tool_result"
    )
    snapshot = codec.decode_canonical(raw["next_request"])
    from pipy_harness.native.agent.messages import AgentToolResultMessage

    returned = next(
        m for m in snapshot.request.messages if isinstance(m, AgentToolResultMessage)
    )
    from pipy_harness.native.agent.tool_details import has_tool_details

    next_value = (
        {"state": "value", "value": returned.details}
        if has_tool_details(returned.details)
        else {"state": "absent"}
    )
    tasks = raw["tasks"]
    return {
        "completed": raw["first"]["inspection"]["outcome"] == "completed"
        and bool(tasks)
        and all(
            t["state"]["status"] == "terminal"
            and t["state"]["outcome"]["status"] == "completed"
            for t in tasks
        ),
        "roles": [
            {"tool_result": "toolResult"}.get(m["role"], m["role"]) for m in messages
        ],
        "result": fields(result, native=True),
        "next_result": next_value,
        "reopened_result": fields(reopened, native=True),
        "provider_calls": raw["after"]["providers"],
        "tool_calls": raw["after"]["calls"],
        "prepares": raw["after"]["prepares"],
        "repeat_unchanged": raw["before"] == raw["after"],
        "next_wire_tools": wire_tools(raw),
    }


def reference(raw):
    messages = [
        entry["model"][0]
        for entry in raw["final"]["entries"]
        if len(entry.get("model", [])) == 1
        # Native completed snapshots intentionally exclude construction/context
        # system entries. Keep those raw Pi facts, outside this common subset.
        and entry["model"][0]["role"] in ("user", "assistant", "toolResult")
    ]
    result = next(m for m in messages if m["role"] == "toolResult")
    returned = next(m for m in raw["requests"][1] if m["role"] == "toolResult")
    reopened = next(
        entry["model"][0]
        for entry in raw["retained"]["entries"]
        if len(entry.get("model", [])) == 1
        and entry["model"][0]["role"] == "toolResult"
    )
    tasks = raw["tasks"]
    return {
        "completed": bool(tasks)
        and all(
            t["state"]["status"] == "terminal"
            and t["state"]["outcome"]["status"] == "completed"
            for t in tasks
        ),
        "roles": [m["role"] for m in messages],
        "result": fields(result),
        "next_result": fields(returned),
        "reopened_result": fields(reopened),
        "provider_calls": raw["after"]["providers"],
        "tool_calls": raw["after"]["calls"],
        "prepares": raw["after"]["prepares"],
        "repeat_unchanged": raw["before"] == raw["after"]
        and raw["repeated"]["status"] == "done",
        "next_wire_tools": wire_tools(raw),
    }


def mutate(trace, name, codec):
    result = copy.deepcopy(trace)
    case = {
        "null_absent": 1,
        "absent_null": 0,
        "false_zero": 2,
        "scalar_lost": 4,
        "array_lost": 5,
    }.get(name, 1)
    actual = codec.decode(result["cases"][case]["observed"])
    if name == "null_absent":
        actual["result"] = {"state": "absent"}
    elif name == "absent_null":
        actual["result"] = {"state": "value", "value": None}
    elif name == "false_zero":
        actual["result"]["value"] = 0
    elif name in ("scalar_lost", "array_lost"):
        actual["result"] = {"state": "absent"}
    elif name == "next_history":
        actual["next_result"] = {"state": "absent"}
    elif name == "reopen":
        actual["reopened_result"] = {"state": "absent"}
    elif name in ("double_effect", "reprepare"):
        actual[{"double_effect": "tool_calls", "reprepare": "prepares"}[name]] += 1
    elif name == "not_completed":
        actual["completed"] = False
    elif name in ("wire_details", "wire_correlation"):
        _mutate_wire(actual, name)
    else:
        raise ValueError("Unknown details control")
    result["cases"][case]["observed"] = codec.encode(actual)
    return result


def _mutate_wire(actual, name):
    if name == "wire_details":
        actual["next_wire_tools"][0]["fields"].append("details")
    else:
        actual["next_wire_tools"][0]["correlated"] = False
