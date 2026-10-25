"""Pure declared CLI/Harness common projection; native policy stays separate."""

from __future__ import annotations

import copy

FIELDS = {
    "user_text",
    "completed",
    "roles",
    "result_names",
    "result_text",
    "provider_calls",
    "tool_calls",
    "applications",
    "prepares",
    "reconnect",
    "repeat_unchanged",
    "usage",
    "usage_once",
}
MUTANTS = (
    "wrong_input",
    "not_completed",
    "order",
    "effects",
    "applications",
    "prepares",
    "usage",
    "usage_presence",
    "reconnect",
    "repeat",
)


def project(raw, parent):
    snapshot = raw["repeated"]["completed"]
    messages = [row["message"] for row in snapshot["messages"]]
    roles = {"user": "user", "assistant": "assistant", "tool_result": "toolResult"}
    results = [m for m in messages if m["role"] == "tool_result"]
    usage = parent.generation.project_ledger_usage(
        snapshot["usage"]["models"]["openai/gpt-test"]
    )
    samples = [m["usage"] for m in messages if m["role"] == "assistant"]
    tasks = raw["tasks"]
    return {
        "user_text": next(m["content"] for m in messages if m["role"] == "user"),
        "completed": raw["first"]["inspection"]["outcome"] == "completed"
        and any(t["kind"] == "pi.generation" for t in tasks)
        and all(
            t["state"]["status"] == "terminal"
            and t["state"]["outcome"]["status"] == "completed"
            for t in tasks
            if t["kind"] in ("pi.generation", "pi.tool")
        ),
        "roles": [roles[m["role"]] for m in messages],
        "result_names": [m["tool_name"] for m in results],
        "result_text": [m["content"] for m in results],
        "provider_calls": raw["after"]["providers"],
        "tool_calls": raw["after"]["calls"],
        "applications": raw["after"]["applications"],
        "prepares": raw["after"]["prepares"],
        "reconnect": parent.d.same(
            raw["first"]["completed"]["messages"], snapshot["messages"]
        ),
        "repeat_unchanged": raw["before"] == raw["after"]
        and raw["repeated"]["inspection"]["outcome"] == "completed",
        "usage": usage,
        "usage_once": parent.tools.usage_matches(samples, usage),
    }


def mutate(value, name, codec):
    result = copy.deepcopy(value)
    actual = codec.decode(result["cases"][1]["observed"])
    if name == "wrong_input":
        actual["user_text"] += " changed"
    elif name == "not_completed":
        actual["completed"] = False
    elif name == "order":
        actual["result_names"].reverse()
    elif name in ("effects", "applications", "prepares"):
        actual[{"effects": "tool_calls"}.get(name, name)] += 1
    elif name == "usage":
        actual["usage"]["input"] += 1
    elif name == "usage_presence":
        actual["usage"].pop("reasoning")
    elif name == "reconnect":
        actual["reconnect"] = False
    elif name == "repeat":
        actual["repeat_unchanged"] = False
    else:
        raise ValueError("Unknown headless semantic control")
    result["cases"][1]["observed"] = codec.encode(actual)
    return result
