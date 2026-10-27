"""Pure declared reporting observables; raw implementation differences survive."""

from __future__ import annotations

import copy

from durable_reference.details_values import fields, wire_tools

PHASE = "retained-tool-reporting-v1"
SCOPE = {
    "common": "committed-details-prefix-ack-reported-diagnostics-failed-task-next-request-answer-counts-reopen",
    "native_normal_output": "explicit-final-text-required-by-existing-DTO",
    "reference_normal_output": "omitted-final-output-uses-Pi-retained-output",
    "raw_differences": [
        "input-output-presence",
        "automatic-error-diagnostics-and-text",
        "warn-versus-warning-and-final-versus-reported-diagnostic",
        "task-graph-shape",
        "timing-and-capacity-policy",
    ],
    "open_gates": [
        "pipy-durable-p15-tool-text-signatures",
        "pipy-durable-p16-omitted-tool-output",
        "DE13c-unsafe-recovery",
        "DE26/33-timing-capacity",
        "DE27/28-storage-graph",
    ],
}
CASES = [
    {
        "id": "reported_details_absent_final",
        "reportedDetails": {"phase": "reported", "renderer": "report-only"},
        "final": "absent",
    },
    {
        "id": "reported_details_null_final",
        "reportedDetails": {"phase": "reported", "renderer": "report-only"},
        "final": "null",
    },
    {
        "id": "throw_after_reports",
        "reportedDetails": ["report-only", False, 0, None],
        "final": "throw",
    },
    {"id": "throw_without_details", "final": "throw"},
]
RECIPE = {"schema_version": 1, "phase": PHASE, "cases": CASES}
IDS = tuple(case["id"] for case in CASES)
INFO = {"severity": "info", "message": "reported-note", "code": "fixture_report"}
WARNING = {"severity": "warning", "message": "final-note", "code": "fixture_final"}
FIELDS = {
    "completed",
    "tool_status",
    "roles",
    "result",
    "next_result",
    "reopened_result",
    "prefix_ack",
    "diagnostics",
    "result_text_prefix",
    "model_error",
    "next_answer",
    "provider_calls",
    "tool_calls",
    "applications",
    "prepares",
    "repeat_unchanged",
    "wire",
}
MUTANTS = (
    "null_absent",
    "absent_null",
    "array_lost",
    "ack_lost",
    "ack_value",
    "diagnostic_lost",
    "result_text_prefix",
    "failed_completed",
    "model_error",
    "next_history",
    "reopen",
    "double_effect",
    "reprepare",
    "not_completed",
    "next_answer",
    "wire_details",
    "wire_correlation",
)


def _case(name):
    return next(row for row in CASES if row["id"] == name)


def _presence(value, key):
    return (
        {"state": "value", "value": value[key]} if key in value else {"state": "absent"}
    )


def expected(name):
    recipe = _case(name)
    failed = recipe["final"] == "throw"
    detail = (
        {"state": "value", "value": None}
        if recipe["final"] == "null"
        else _presence(recipe, "reportedDetails")
    )
    prefix = (
        None
        if "reportedDetails" not in recipe
        else {
            "output": "partial-body",
            "details": _presence(recipe, "reportedDetails"),
            "diagnostics": [INFO],
        }
    )
    return {
        "completed": True,
        "tool_status": "failed" if failed else "completed",
        "roles": ["user", "assistant", "toolResult", "assistant"],
        "result": detail,
        "next_result": detail,
        "reopened_result": detail,
        "prefix_ack": prefix,
        "diagnostics": [INFO] if failed else [INFO, WARNING],
        "result_text_prefix": "partial-body",
        "model_error": failed,
        "next_answer": "done",
        "provider_calls": 2,
        "tool_calls": 1,
        "applications": 1,
        "prepares": 1,
        "repeat_unchanged": True,
        "wire": {
            "fields": ["content", "role", "tool_call_id"],
            "correlated": True,
            "reported_note": True,
            "final_note": not failed,
        },
    }


def validate(raw, d, codec):
    d.shape(raw, {"schema_version", "phase", "cases"}, "Reporting trace")
    d.require(
        type(raw["schema_version"]) is int
        and raw["schema_version"] == 1
        and raw["phase"] == PHASE,
        "Reporting trace identity",
    )
    d.require(
        type(raw["cases"]) is list
        and [row.get("id") for row in raw["cases"] if type(row) is dict] == list(IDS),
        "Reporting case identities",
    )
    for row in raw["cases"]:
        d.shape(row, {"id", "observed"}, "Reporting case")
        value = codec.decode(row["observed"])
        d.shape(value, FIELDS, "Reporting observation")
        d.require(codec.encode(value) == row["observed"], "Reporting numeric profile")
        for key in ("completed", "model_error", "repeat_unchanged"):
            d.require(type(value[key]) is bool, "Reporting flag")
        for key in ("provider_calls", "tool_calls", "applications", "prepares"):
            d.require(
                type(value[key]) in (int, float) and value[key] >= 0, "Reporting count"
            )
        for key in ("tool_status", "result_text_prefix", "next_answer"):
            d.require(type(value[key]) is str, "Reporting text")
        d.require(
            type(value["roles"]) is list
            and all(type(x) is str for x in value["roles"]),
            "Reporting roles",
        )
        for key in ("result", "next_result", "reopened_result"):
            _check_presence(value[key], d)
        d.shape(
            value["wire"],
            {"fields", "correlated", "reported_note", "final_note"},
            "Reporting wire",
        )
        d.require(
            type(value["wire"]["fields"]) is list
            and all(type(x) is str for x in value["wire"]["fields"])
            and all(
                type(value["wire"][key]) is bool
                for key in ("correlated", "reported_note", "final_note")
            ),
            "Reporting wire types",
        )
        _check_diagnostics(value["diagnostics"], d)
        if value["prefix_ack"] is not None:
            d.shape(
                value["prefix_ack"],
                {"output", "details", "diagnostics"},
                "Reporting prefix",
            )
            d.require(
                type(value["prefix_ack"]["output"]) is str, "Reporting prefix output"
            )
            _check_presence(value["prefix_ack"]["details"], d)
            _check_diagnostics(value["prefix_ack"]["diagnostics"], d)


def _check_presence(value, d):
    d.require(
        type(value) is dict and value.get("state") in ("absent", "value"),
        "Reporting presence",
    )
    d.shape(
        value,
        {"state", "value"} if value["state"] == "value" else {"state"},
        "Reporting presence fields",
    )


def _check_diagnostics(value, d):
    d.require(type(value) is list, "Reporting diagnostics")
    for row in value:
        d.shape(row, {"severity", "message", "code"}, "Reporting diagnostic")
        d.require(all(type(row[k]) is str for k in row), "Reporting diagnostic scalars")


def _wire(raw, expected_text):
    wire = wire_tools(raw)
    assert len(wire) == 1 and wire[0]["content"] == expected_text
    item = wire[0]
    return {
        "fields": item["fields"],
        "correlated": item["correlated"],
        "reported_note": "reported-note" in item["content"],
        "final_note": "final-note" in item["content"],
    }


def native(raw, codec):
    from pipy_harness.native.agent.messages import AgentToolResultMessage
    from pipy_harness.native.agent.tool_details import has_tool_details
    from pipy_harness.native.durable.tool_output_values import project_tool_output
    from pipy_harness.native.durable.tool_values import receipt

    messages = [row["message"] for row in raw["first"]["completed"]["messages"]]
    result = next(m for m in messages if m["role"] == "tool_result")
    repeated = next(
        row["message"]
        for row in raw["repeated"]["completed"]["messages"]
        if row["message"]["role"] == "tool_result"
    )
    entry = next(row for row in raw["entries"] if row["kind"] == "pi.tool-result")
    assert set(entry["data"]) == {"nativeToolReceipt"} and len(entry["model"]) == 1
    carrier, outcome = receipt(entry["data"]["nativeToolReceipt"])
    fact = carrier["nativeToolOutput"]
    assert carrier["version"] == fact["version"] == 2
    projected = project_tool_output(fact, outcome)
    assert (
        codec.encode_canonical(
            projected, codec_version=entry["model"][0]["codec_version"]
        )
        == entry["model"][0]
    )
    task = next(row for row in raw["tasks"] if row["id"] == entry["byTaskId"])
    assert task["kind"] == "pi.tool" and task["input"] == carrier["call"]
    assert task["state"]["outcome"]["result"] == {
        "kind": "model",
        "entryId": entry["id"],
    }
    failed = fact["ending"] == "failed"
    assert fact["recovery"] == (
        {"reason": "tool_error", "category": "invocation_exception"} if failed else None
    )
    notices = "[info (fixture_report)] reported-note\n" + (
        "Tool failed (invocation_exception)."
        if failed
        else "[warning (fixture_final)] final-note"
    )
    model_text = "partial-body\n<harness>\n" + notices + "\n</harness>"
    assert (
        projected.content.value == model_text
        and outcome.result.content.value == "partial-body"
    )
    assert fact["diagnostics"] == ([INFO] if failed else [INFO, WARNING])
    snapshot = codec.decode_canonical(raw["next_request"])
    returned = next(
        m for m in snapshot.request.messages if isinstance(m, AgentToolResultMessage)
    )
    assert returned.content.value == model_text
    next_value = (
        {"state": "value", "value": returned.details}
        if has_tool_details(returned.details)
        else {"state": "absent"}
    )
    prefix = _native_prefix(raw["reports"], carrier)
    return {
        "completed": raw["first"]["inspection"]["outcome"] == "completed",
        "tool_status": task["state"]["outcome"]["status"],
        "roles": [
            {"tool_result": "toolResult"}.get(m["role"], m["role"]) for m in messages
        ],
        "result": fields(result, native=True),
        "next_result": next_value,
        "reopened_result": fields(repeated, native=True),
        "prefix_ack": prefix,
        "diagnostics": fact["diagnostics"],
        "result_text_prefix": outcome.result.content.value,
        "model_error": result["is_error"],
        "next_answer": messages[-1]["content"],
        "provider_calls": raw["after"]["providers"],
        "tool_calls": raw["after"]["calls"],
        "applications": len(raw["effects"]["applications"]),
        "prepares": raw["after"]["prepares"],
        "repeat_unchanged": raw["before"] == raw["after"],
        "wire": _wire(raw, model_text),
    }


def _native_prefix(reports, carrier):
    if not reports:
        return None
    assert len(reports) == 1 and reports[0]["operation"] == "details-ack"
    value = reports[0]["committed"]
    assert value["call"] == carrier["call"] and value["key"] == carrier["key"]
    detail = (
        {"state": "value", "value": value["details"]["value"]}
        if value["details"]["present"]
        else {"state": "absent"}
    )
    return {
        "output": value["output"],
        "details": detail,
        "diagnostics": value["diagnostics"],
    }


def reference(raw):
    messages = [
        entry["model"][0]
        for entry in raw["final"]["entries"]
        if len(entry.get("model", [])) == 1
        and entry["model"][0]["role"] in ("user", "assistant", "toolResult")
    ]
    entry = next(
        row for row in raw["final"]["entries"] if row["kind"] == "pi.tool-result"
    )
    result = entry["model"][0]
    task = next(row for row in raw["tasks"] if row["id"] == entry["byTaskId"])
    assert task["kind"] == "pi.tool" and task["state"]["outcome"]["result"] == {
        "kind": "model",
        "entryId": entry["id"],
    }
    failed = task["state"]["outcome"]["status"] == "failed"
    diag = (
        [
            INFO,
            {
                "severity": "error",
                "message": "fixture-thrown-error",
                "code": "tool_error",
            },
        ]
        if failed
        else [INFO, {**WARNING, "severity": "warn"}]
    )
    assert entry["data"]["diagnostics"] == diag
    expected_text = (
        "partial-body\n<harness>\n[info] reported-note\n"
        + ("[error] fixture-thrown-error" if failed else "[warn] final-note")
        + "\n</harness>"
    )
    assert "\n".join(block["text"] for block in result["content"]) == expected_text
    returned = next(m for m in raw["requests"][1] if m["role"] == "toolResult")
    assert returned["content"] == result["content"]
    repeated = next(
        entry["model"][0]
        for entry in raw["retained"]["entries"]
        if len(entry.get("model", [])) == 1
        and entry["model"][0]["role"] == "toolResult"
    )
    prefix = _reference_prefix(raw["reports"], result, task)
    return {
        "completed": raw["repeated"]["status"] == "done",
        "tool_status": task["state"]["outcome"]["status"],
        "roles": [m["role"] for m in messages],
        "result": fields(result),
        "next_result": fields(returned),
        "reopened_result": fields(repeated),
        "prefix_ack": prefix,
        "diagnostics": [INFO] if failed else [INFO, WARNING],
        "result_text_prefix": result["content"][0]["text"],
        "model_error": result["isError"],
        "next_answer": "\n".join(
            block["text"]
            for block in messages[-1]["content"]
            if block["type"] == "text"
        ),
        "provider_calls": raw["after"]["providers"],
        "tool_calls": raw["after"]["calls"],
        "applications": len(raw["effects"]["calls"]),
        "prepares": raw["after"]["prepares"],
        "repeat_unchanged": raw["before"] == raw["after"],
        "wire": _wire(raw, expected_text),
    }


def _reference_prefix(reports, result, task):
    if not reports:
        return None
    assert len(reports) == 1 and reports[0]["operation"] == "details-ack"
    tools = reports[0]["committed"]["tools"]
    assert len(tools) == 1
    value = tools[0]
    assert (
        value["taskId"] == task["id"]
        and value["callId"] == result["toolCallId"]
        and value["status"] == "running"
    )
    return {
        "output": value["output"],
        "details": fields(value),
        "diagnostics": value["diagnostics"],
    }


def differences(reference_rows, native_rows):
    # Full original rows remain artifacts too. These actual slices make each
    # excluded difference explicit; no equality/parity claim hides them.
    return {
        "profile": "reporting-raw-differences-v1",
        "native_policy_source_only": {
            "period_ms": 200,
            "bytes_per_second": 131072,
            "snapshot_bytes": 524288,
            "snapshot_nodes": 32768,
        },
        "reference_policy_source_only": {"period_ms": 100, "bytes_per_second": 102400},
        "cases": [
            {
                "id": left["id"],
                "reference": {
                    "tasks": left["tasks"],
                    "reports": left["reports"],
                    "wire": left["wire"],
                    "entries": left["final"]["entries"],
                },
                "native": {
                    "tasks": right["raw"]["tasks"],
                    "reports": right["raw"]["reports"],
                    "wire": right["raw"]["wire"],
                    "entries": right["raw"]["entries"],
                    "return_shape": right["raw"]["native_return_shape"],
                },
            }
            for left, right in zip(reference_rows, native_rows, strict=True)
        ],
    }


def mutate(trace, name, codec):
    result = copy.deepcopy(trace)
    index = (
        3
        if name == "absent_null"
        else 2
        if name in ("failed_completed", "model_error", "array_lost")
        else 1
        if name == "null_absent"
        else 0
    )
    actual = codec.decode(result["cases"][index]["observed"])
    changes = {
        "null_absent": ("result", {"state": "absent"}),
        "absent_null": ("result", {"state": "value", "value": None}),
        "array_lost": ("result", {"state": "absent"}),
        "ack_lost": ("prefix_ack", None),
        "diagnostic_lost": ("diagnostics", []),
        "result_text_prefix": ("result_text_prefix", ""),
        "failed_completed": ("tool_status", "completed"),
        "model_error": ("model_error", False),
        "next_history": ("next_result", {"state": "absent"}),
        "reopen": ("reopened_result", {"state": "absent"}),
        "not_completed": ("completed", False),
        "next_answer": ("next_answer", ""),
    }
    if name in changes:
        key, value = changes[name]
        actual[key] = value
    elif name == "ack_value":
        actual["prefix_ack"]["details"] = {"state": "absent"}
    elif name in ("double_effect", "reprepare"):
        actual["applications" if name == "double_effect" else "prepares"] += 1
    elif name == "wire_details":
        actual["wire"]["fields"].append("details")
    elif name == "wire_correlation":
        actual["wire"]["correlated"] = False
    else:
        raise ValueError("Unknown reporting mutant")
    result["cases"][index]["observed"] = codec.encode(actual)
    return result
