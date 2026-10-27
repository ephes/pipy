"""Finite C1 recovery observations; no counter, crash or graph parity claim."""

from __future__ import annotations

import copy

from durable_reference.details_values import fields, wire_tools

PHASE = "interrupted-tool-continuation-v1"
CASES = [
    {"id": "safe_same", "stored": "safe", "current": "same"},
    {"id": "unsafe_same", "stored": "unsafe", "current": "same"},
    {"id": "safe_missing", "stored": "safe", "current": "missing"},
]
IDS = tuple(row["id"] for row in CASES)
RECIPE = {"schema_version": 1, "phase": PHASE, "cases": CASES}
SCOPE = {
    "common": "saved-safe-replay-or-noninvoking-interruption-retained-details-next-request-answer-reopen",
    "native_cut": "SIGKILL-after-progress-COMMIT-before-details-ACK",
    "reference_cut": "graceful-close-after-details-ACK",
    "raw_differences": [
        "cut-and-ACK",
        "error-text-diagnostics",
        "task-graph",
        "counter-semantics",
        "private-receipt-shape",
        "timing",
        "session-local-read-revision",
    ],
    "open": [
        "C2-historical-blocked-migration",
        "DE14-restart-policy",
        "P15",
        "P16",
        "DE27/28",
    ],
}
FIELDS = {
    "completed",
    "interrupted",
    "details",
    "next_details",
    "reopened_details",
    "prefix_details",
    "retained_output",
    "model_error",
    "next_answer",
    "provider_calls",
    "tool_calls",
    "prepares",
    "offered",
    "reopen_unchanged",
    "wire_correlated",
}
MUTANTS = (
    "details",
    "next_details",
    "reopened_details",
    "prefix_details",
    "retained_output",
    "interrupted",
    "model_error",
    "next_answer",
    "provider_calls",
    "tool_calls",
    "prepares",
    "offered",
    "completed",
    "reopen_unchanged",
    "wire_correlated",
)
RETAINED = {"retained": [None, False, 0]}


def expected(name):
    interrupted = name != "safe_same"
    detail = {"state": "value", "value": RETAINED if interrupted else {"normal": True}}
    return {
        "completed": True,
        "interrupted": interrupted,
        "details": detail,
        "next_details": detail,
        "reopened_details": detail,
        "prefix_details": {"state": "value", "value": RETAINED},
        "retained_output": True,
        "model_error": interrupted,
        "next_answer": "terminal answer",
        "provider_calls": 2,
        "tool_calls": 1 if interrupted else 2,
        "prepares": 1,
        "offered": ["later", "unused"]
        if name == "safe_missing"
        else ["later", "keyed", "unused"],
        "reopen_unchanged": True,
        "wire_correlated": True,
    }


def validate(value, d, codec):
    d.shape(value, {"schema_version", "phase", "cases"}, "Interrupted trace")
    d.require(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["phase"] == PHASE,
        "Interrupted identity",
    )
    d.require(
        type(value["cases"]) is list
        and [x.get("id") for x in value["cases"] if type(x) is dict] == list(IDS),
        "Interrupted cases",
    )
    for row in value["cases"]:
        d.shape(row, {"id", "observed"}, "Interrupted row")
        result = codec.decode(row["observed"])
        d.shape(result, FIELDS, "Interrupted observation")
        d.require(codec.encode(result) == row["observed"], "Interrupted number profile")
        for key in (
            "completed",
            "interrupted",
            "retained_output",
            "model_error",
            "reopen_unchanged",
            "wire_correlated",
        ):
            d.require(type(result[key]) is bool, "Interrupted boolean")
        for key in ("provider_calls", "tool_calls", "prepares"):
            d.require(
                type(result[key]) in (int, float) and result[key] >= 0,
                "Interrupted count",
            )
        for key in ("details", "next_details", "reopened_details", "prefix_details"):
            item = result[key]
            d.require(
                type(item) is dict and item.get("state") in ("absent", "value"),
                "Interrupted presence",
            )
            d.shape(
                item,
                {"state", "value"} if item["state"] == "value" else {"state"},
                "Interrupted presence shape",
            )
        d.require(
            type(result["next_answer"]) is str
            and type(result["offered"]) is list
            and all(type(x) is str for x in result["offered"]),
            "Interrupted text",
        )


def native(raw, codec):
    from pipy_harness.native.agent.messages import AgentToolResultMessage
    from pipy_harness.native.durable.tool_output_values import project_tool_output
    from pipy_harness.native.durable.tool_values import receipt

    entries = raw["completed"]["entries"]
    entry = next(e for e in entries if e["kind"] == "pi.tool-result")
    assert set(entry["data"]) == {"nativeToolReceipt"} and len(entry["model"]) == 1
    saved, outcome = receipt(entry["data"]["nativeToolReceipt"])
    full = saved["nativeToolOutput"]
    projected = project_tool_output(full, outcome)
    assert (
        codec.encode_canonical(
            projected, codec_version=entry["model"][0]["codec_version"]
        )
        == entry["model"][0]
    )
    task = next(t for t in raw["completed"]["tasks"] if t["id"] == entry["byTaskId"])
    assert task["input"] == saved["call"] and task["state"]["outcome"]["result"] == {
        "kind": "model",
        "entryId": entry["id"],
    }
    interrupted = full["ending"] == "interrupted"
    assert task["state"]["outcome"]["status"] == (
        "failed" if interrupted else "completed"
    )
    if interrupted:
        assert full["recovery"] == {
            "reason": "interrupted",
            "attempts": 1,
            "restarts": 1,
        }
        assert outcome.execution_seconds is None
    prefix = raw["prefix"]["documents"]["pi.tool.progress"][-1]
    assert prefix["call"] == saved["call"] and prefix["key"] == saved["key"]
    snapshot = codec.decode_canonical(
        next(e for e in reversed(entries) if e["kind"] == "pi.assistant")["data"][
            "request"
        ]
    )
    returned = next(
        m for m in snapshot.request.messages if type(m) is AgentToolResultMessage
    )
    assert codec.encode_canonical(returned) == codec.encode_canonical(projected)
    messages = [x["message"] for x in raw["first"]["completed"]["messages"]]
    result = next(x for x in messages if x["role"] == "tool_result")
    repeated = next(
        x["message"]
        for x in raw["repeated"]["completed"]["messages"]
        if x["message"]["role"] == "tool_result"
    )
    wire = next(x for x in raw["wire"][-1]["messages"] if x["role"] == "tool")
    assert wire["content"] == projected.content.value
    offered = [x["function"]["name"] for x in raw["wire"][-1]["tools"]]
    assert (
        offered
        == [x.name for x in snapshot.request.available_tools]
        == list(snapshot.advertised_tool_names)
    )
    return {
        "completed": raw["first"]["inspection"]["outcome"] == "completed",
        "interrupted": interrupted,
        "details": fields(result, native=True),
        "next_details": {"state": "value", "value": returned.details},
        "reopened_details": fields(repeated, native=True),
        "prefix_details": {"state": "value", "value": prefix["details"]["value"]}
        if prefix["details"]["present"]
        else {"state": "absent"},
        "retained_output": outcome.result.content.value
        == ("retained output" if interrupted else "tool completed normally"),
        "model_error": result["is_error"],
        "next_answer": messages[-1]["content"],
        "provider_calls": raw["after"]["providers"],
        "tool_calls": raw["after"]["calls"],
        "prepares": raw["after"]["prepares"],
        "offered": offered,
        "reopen_unchanged": raw["after"] == raw["reopened_counts"]
        and raw["completed"] == raw["reopened"],
        "wire_correlated": wire_tools(raw)[0]["correlated"],
    }


def reference(raw):
    entry = next(e for e in raw["final"]["entries"] if e["kind"] == "pi.tool-result")
    result = entry["model"][0]
    task = next(t for t in raw["tasks"] if t["id"] == entry["byTaskId"])
    assert task["state"]["outcome"]["result"] == {
        "kind": "model",
        "entryId": entry["id"],
    }
    interrupted = task["state"]["outcome"]["status"] == "failed"
    diagnostics = entry["data"]["diagnostics"]
    assert diagnostics[0] == {
        "severity": "warn",
        "message": "committed diagnostic",
        "code": "fixture",
    }
    if interrupted:
        assert diagnostics[1:] == [
            {
                "severity": "error",
                "code": "interrupted",
                "message": "Tool keyed was interrupted and may have partially run",
            }
        ]
        assert task["state"]["outcome"]["error"] == {
            "message": diagnostics[1]["message"]
        }
    else:
        assert len(diagnostics) == 1
    returned = next(m for m in raw["requests"][-1] if m["role"] == "toolResult")
    assert returned == result
    repeated = next(
        e["model"][0]
        for e in raw["retained"]["entries"]
        if e["kind"] == "pi.tool-result"
    )
    prefix = raw["reports"][0]["committed"]["tools"][0]
    assert prefix["taskId"] == task["id"] and prefix["callId"] == result["toolCallId"]
    wire = next(x for x in raw["wire"][-1]["messages"] if x["role"] == "tool")
    assert wire["content"] == "\n".join(x["text"] for x in result["content"])
    messages = [
        e["model"][0] for e in raw["final"]["entries"] if len(e.get("model", [])) == 1
    ]
    return {
        "completed": raw["repeated"]["status"] == "done",
        "interrupted": interrupted,
        "details": fields(result),
        "next_details": fields(returned),
        "reopened_details": fields(repeated),
        "prefix_details": fields(prefix),
        "retained_output": result["content"][0]["text"].startswith(
            "retained output" if interrupted else "tool completed normally"
        ),
        "model_error": result["isError"],
        "next_answer": "".join(
            x["text"] for x in messages[-1]["content"] if x["type"] == "text"
        ),
        "provider_calls": raw["after"]["providers"],
        "tool_calls": raw["after"]["calls"],
        "prepares": raw["after"]["prepares"],
        "offered": [x["function"]["name"] for x in raw["wire"][-1]["tools"]],
        "reopen_unchanged": raw["before"] == raw["after"] and result == repeated,
        "wire_correlated": wire_tools(raw)[0]["correlated"],
    }


def differences(reference_rows, native_rows):
    return {"scope": SCOPE, "reference": reference_rows, "native": native_rows}


def mutate(actual, name, codec):
    result = copy.deepcopy(actual)
    row = result["cases"][1]
    value = codec.decode(row["observed"])
    old = value[name]
    value[name] = (
        not old
        if type(old) is bool
        else old + 1
        if type(old) in (int, float)
        else {"state": "absent"}
        if type(old) is dict
        else ["outside"]
        if type(old) is list
        else "changed"
    )
    row["observed"] = codec.encode(value)
    return result
