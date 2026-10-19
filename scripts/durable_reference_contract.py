"""Verification-only tagged values and bounded case-specific projection. No product API."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any


class EvidenceError(ValueError):
    """Missing or invalid comparison evidence."""


UNDEFINED = object()
CASES = {
    "kernel-memory-v1": (1, "finite-json-v1", None),
    "harness-round-v1": (2, "harness-identity-v1", "harness.settled"),
    "context-heads-v1": (2, "context-identity-v1", "context.selection"),
    "tool-recovery-v1": (2, "recovery-identity-v1", "recovery.matrix"),
    "durable-abort-v1": (2, "abort-identity-v1", "abort.settled"),
    "env-conformance-v1": (2, "env-path-time-v1", "env.conformance"),
}
ADAPTERS = {
    "scripts/durable_pi_reference.mts": "durable_pi_reference.mts",
    "scripts/durable_reference/contract.mts": "durable_reference/contract.mts",
    "scripts/durable_reference/cases/harness-round.mts": "durable_reference/cases/harness-round.mts",
    "scripts/durable_reference/cases/context-heads.mts": "durable_reference/cases/context-heads.mts",
}
ADAPTERS.update(
    {
        f"scripts/durable_reference/cases/{name}.mts": f"durable_reference/cases/{name}.mts"
        for name in ("tool-recovery", "durable-abort", "env-conformance")
    }
)
COMPARISON = {
    "scripts/durable_pi_comparison.py",
    "scripts/durable_reference_contract.py",
}


MODULES = {
    "kernel-memory-v1": [
        "packages/durable/src/session/session.ts",
        "packages/durable/src/storage/memory.ts",
        "packages/durable/src/documents.ts",
    ],
    "harness-round-v1": [
        "packages/durable/src/harness/harness.ts",
        "packages/durable/src/harness/tool.ts",
        "packages/durable/src/storage/memory.ts",
        "packages/ai/src/providers/faux.ts",
    ],
    "context-heads-v1": [
        "packages/durable/src/harness/harness.ts",
        "packages/durable/src/harness/context.ts",
        "packages/durable/src/storage/memory.ts",
    ],
}
TESTS = {
    "kernel-memory-v1": [
        (
            "packages/durable/src/testing/storage-conformance.ts",
            "source-derived atomic commit/document probe",
        )
    ],
    "harness-round-v1": [
        (
            "packages/durable/test/harness-tools.test.ts",
            "runs input, tool call, tool result, and answer, and settles the input",
        ),
        (
            "packages/durable/test/harness-tools.test.ts",
            "uses explicit null details instead of the last reported value",
        ),
    ],
    "context-heads-v1": [
        (
            "packages/durable/test/harness-context.test.ts",
            "resolves self heads and uses the newest head marker",
        )
    ],
}
INPUTS = {
    "kernel-memory-v1": {"clock_epoch_ms"},
    "harness-round-v1": {
        "clock_epoch_ms",
        "api",
        "tool_name",
        "tool_description",
        "tool_arguments",
        "tool_call_id",
        "input",
        "progress_details",
        "tool_result",
        "answer",
    },
    "context-heads-v1": {"clock_epoch_ms", "old", "fresh", "after", "summary", "tail"},
}

POLICIES = [
    ("unsafe", "unsafe"),
    ("safe", "safe"),
    ("safe", "unsafe"),
    ("unsafe", "safe"),
]
SCENARIOS = [
    {
        "id": "binary-reader",
        "name": "binary reader reads byte ranges of the opened file",
    },
    {
        "id": "literal-argv",
        "name": "argv exec passes arguments to the program without shell parsing",
    },
]
INPUTS.update(
    {
        "tool-recovery-v1": {"clock_epoch_ms", "api", "variants"},
        "durable-abort-v1": {"clock_epoch_ms"},
        "env-conformance-v1": {"clock_epoch_ms", "scenarios"},
    }
)
MODULES.update(
    {
        "tool-recovery-v1": [
            "packages/durable/src/harness/harness.ts",
            "packages/durable/src/harness/tool.ts",
            "packages/durable/src/storage/sqlite/node.ts",
            "packages/durable/src/storage/sqlite/storage.ts",
            "packages/chord/src/sqlite/node.ts",
            "packages/ai/src/providers/faux.ts",
        ],
        "durable-abort-v1": [
            "packages/durable/src/harness/harness.ts",
            "packages/durable/src/harness/scheduler.ts",
            "packages/durable/src/session/session.ts",
            "packages/durable/src/tasks.ts",
            "packages/durable/src/storage/memory.ts",
        ],
        "env-conformance-v1": [
            "packages/durable/src/env/node.ts",
            "packages/durable/src/env/index.ts",
            "packages/durable/src/testing/env-conformance.ts",
        ],
    }
)
TESTS.update(
    {
        "tool-recovery-v1": [
            ("packages/durable/test/harness-tools-recovery.test.ts", name)
            for name in (
                "answers an unsafe tool interrupted after intent with its durable partial output",
                "reruns a tool only when both the stored and the current replay policy are safe",
            )
        ],
        "durable-abort-v1": [
            (
                "packages/durable/test/harness-tasks.test.ts",
                "rejects the run's commits and memo writes after the mark, and settles through a fresh abort invocation",
            )
        ],
        "env-conformance-v1": [
            ("packages/durable/src/testing/env-conformance.ts", item["name"])
            for item in SCENARIOS
        ],
    }
)


def validate_input(value: Any, case: str) -> None:
    shape(value, INPUTS[case], "case input")
    if type(value["clock_epoch_ms"]) is not int or value["clock_epoch_ms"] <= 0:
        raise EvidenceError("Invalid fixed clock")
    strings = INPUTS[case] - {
        "clock_epoch_ms",
        "tool_arguments",
        "progress_details",
        "tool_result",
        "variants",
        "scenarios",
    }
    if any(type(value[key]) is not str for key in strings):
        raise EvidenceError("Invalid scripted input string")
    if case == "tool-recovery-v1":
        if value["variants"] != [
            {"id": f"{a}-{b}", "stored": a, "current": b} for a, b in POLICIES
        ]:
            raise EvidenceError("Incomplete recovery input matrix")
    if case == "env-conformance-v1" and value["scenarios"] != SCENARIOS:
        raise EvidenceError("Incomplete environment input cases")
    if case == "harness-round-v1":
        for key in ("tool_arguments", "progress_details", "tool_result"):
            if type(value[key]) is not dict:
                raise EvidenceError("Invalid tool input object")
        result = shape(
            value["tool_result"], {"output", "details"}, "scripted tool result"
        )
        array(result["output"], "scripted tool output")


def shape(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise EvidenceError(f"Invalid {label} shape")
    return value


def array(value: Any, label: str) -> list[Any]:
    if type(value) is not list:
        raise EvidenceError(f"Invalid {label} array")
    return value


def decode(value: Any) -> Any:
    """Decode a collision-free JS value; plain data objects are always tagged."""
    if type(value) is not dict:
        if value is None or type(value) in (str, bool, int, float):
            return value
        raise EvidenceError("Invalid tagged scalar")
    kind = value.get("type")
    if kind == "undefined":
        shape(value, {"type"}, "undefined")
        return UNDEFINED
    if kind == "array":
        shape(value, {"type", "items"}, "tagged array")
        return [decode(v) for v in array(value["items"], "items")]
    shape(value, {"type", "entries"}, "tagged object")
    if kind != "object":
        raise EvidenceError("Unknown tagged value")
    result = {}
    for pair in array(value["entries"], "entries"):
        if type(pair) is not list or len(pair) != 2 or type(pair[0]) is not str:
            raise EvidenceError("Invalid tagged object entry")
        if pair[0] in result:
            raise EvidenceError("Duplicate tagged object key")
        result[pair[0]] = decode(pair[1])
    return result


def encode(value: Any) -> Any:
    if value is UNDEFINED:
        return {"type": "undefined"}
    if type(value) is dict:
        return {
            "type": "object",
            "entries": [[k, encode(v)] for k, v in sorted(value.items())],
        }
    if type(value) is list:
        return {"type": "array", "items": [encode(v) for v in value]}
    return value


def record(value: Any, required: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or not required <= value.keys():
        raise EvidenceError(f"Invalid {label} record")
    return value


def entry(value: Any) -> None:
    row = record(value, {"id", "conversationId", "kind"}, "entry")
    if type(row["kind"]) is not str:
        raise EvidenceError("Invalid entry kind")
    if type(row["conversationId"]) is not int or row["conversationId"] != 1:
        raise EvidenceError("Root conversation identity drift")
    if "model" in row:
        messages(row["model"])


def messages(value: Any) -> None:
    for message in array(value, "messages"):
        record(message, {"role", "content"}, "message")
        if type(message["role"]) is not str or message["role"] not in {
            "user",
            "system",
            "assistant",
            "toolResult",
        }:
            raise EvidenceError("Invalid message role")
        content = message["content"]
        if message["role"] in {"assistant", "toolResult"} and type(content) is not list:
            raise EvidenceError("Invalid assistant/tool content")
        if type(content) is not str:
            for item in array(content, "message content"):
                record(item, {"type"}, "content")


def view(value: Any) -> None:
    value = shape(value, {"head", "entries", "contributions", "messages"}, "context")
    if value["head"] is not UNDEFINED:
        entry(value["head"])
    for row in array(value["entries"], "context entries"):
        entry(row)
    for contribution in array(value["contributions"], "contributions"):
        messages(contribution)
    messages(value["messages"])


def usage(value: Any) -> None:
    shape(value, {"models", "tools"}, "usage ledger")
    for bucket in value.values():
        if type(bucket) is not dict:
            raise EvidenceError("Invalid usage bucket")
        for item in bucket.values():
            record(
                item,
                {"input", "output", "cacheRead", "cacheWrite", "totalTokens", "cost"},
                "usage",
            )
            for key in (
                "input",
                "output",
                "cacheRead",
                "cacheWrite",
                "totalTokens",
                "cacheWrite1h",
                "reasoning",
            ):
                if key in item and (type(item[key]) is not int or item[key] < 0):
                    raise EvidenceError("Invalid usage counter")
            record(
                item["cost"],
                {"input", "output", "cacheRead", "cacheWrite", "total"},
                "usage cost",
            )
            if any(type(v) not in (int, float) or v < 0 for v in item["cost"].values()):
                raise EvidenceError("Invalid usage cost")


def observations(value: Any, case: str) -> dict[str, Any]:
    if case == "tool-recovery-v1":
        return recovery_observation(value)
    if case == "durable-abort-v1":
        return abort_observation(value)
    if case == "env-conformance-v1":
        return env_observation(value)
    if case == "harness-round-v1":
        value = shape(
            value,
            {
                "submission",
                "history",
                "tasks",
                "invocations",
                "requests",
                "provider",
                "usage",
                "sessionUsage",
                "live",
                "reports",
                "faux",
                "close",
            },
            "H1 observation",
        )
        record(
            value["submission"],
            {"id", "conversationId", "type", "status"},
            "submission",
        )
        for key in ("tasks", "invocations", "requests", "reports"):
            array(value[key], key)
        record(value["provider"], {"sessionId"}, "provider")
        usage(value["usage"])
        usage(value["sessionUsage"])
        if (
            value["submission"]["conversationId"] != 1
            or type(value["submission"]["conversationId"]) is not int
        ):
            raise EvidenceError("Root submission conversation drift")
        for request in value["requests"]:
            shape(
                request,
                {"messages", "sessionId", "cacheRetention"},
                "request observation",
            )
            messages(request["messages"])
    else:
        value = shape(
            value,
            {"history", "first", "final", "requested_cutoff", "close"},
            "C1 observation",
        )
        view(value["first"])
        view(value["final"])
    record(value["close"], {"reason"}, "close result")
    if type(value["close"]["reason"]) is not str:
        raise EvidenceError("Invalid close reason")
    for row in array(value["history"], "history"):
        entry(row)
    return value


def task_record(value: Any) -> None:
    record(value, {"id", "conversationId", "kind", "input", "state"}, "task")
    if type(value["id"]) is not int or value["id"] <= 1 or value["conversationId"] != 1:
        raise EvidenceError("Invalid root task identity")
    record(value["state"], {"status"}, "task state")


def close_result(value: Any) -> None:
    record(value, {"reason"}, "close result")
    if type(value["reason"]) is not str:
        raise EvidenceError("Invalid close reason")


def abort_observation(value: Any) -> dict[str, Any]:
    shape(
        value,
        {
            "id",
            "admitted",
            "running",
            "publications",
            "events",
            "memoRead",
            "late",
            "callbackEntered",
            "abortInvocations",
            "freshRuntime",
            "abortCurrent",
            "abortResult",
            "final",
            "history",
            "reports",
            "close",
        },
        "H3 observation",
    )
    if type(value["id"]) is not int or value["id"] <= 1:
        raise EvidenceError("Invalid abort subject")
    for key in ("admitted", "running", "abortCurrent", "final"):
        task_record(value[key])
    for publication in array(value["publications"], "publications"):
        abort_publication(publication)
    if any(type(v) is not str for v in array(value["events"], "events")):
        raise EvidenceError("Invalid abort events")
    for item in array(value["late"], "late operations"):
        late_operation(item)
    for key in ("callbackEntered", "freshRuntime"):
        if type(value[key]) is not bool:
            raise EvidenceError("Invalid abort boolean")
    if type(value["abortInvocations"]) is not int or value["abortInvocations"] < 0:
        raise EvidenceError("Invalid abort count")
    for row in array(value["history"], "history"):
        entry(row)
    array(value["reports"], "reports")
    close_result(value["close"])
    return value


def late_operation(item: Any) -> None:
    record(item, {"op", "status"}, "late operation")
    if item["op"] not in ("memo", "commit") or item["status"] not in (
        "fulfilled",
        "rejected",
    ):
        raise EvidenceError("Invalid late operation")
    if "error" in item:
        record(item["error"], {"name", "message"}, "late error")


def abort_publication(publication: Any) -> None:
    shape(publication, {"seq", "changes"}, "publication")
    if type(publication["seq"]) is not int or publication["seq"] < 0:
        raise EvidenceError("Invalid commit sequence")
    for change in array(publication["changes"], "changes"):
        shape(change, {"type", "value"}, "task change")
        if change["type"] != "task":
            raise EvidenceError("Unsupported abort publication change")
        task_record(change["value"])


def env_observation(value: Any) -> dict[str, Any]:
    shape(value, {"scenarios"}, "E1 observation")
    scenarios = array(value["scenarios"], "scenarios")
    if [(v.get("id"), v.get("name")) for v in scenarios if type(v) is dict] != [
        (v["id"], v["name"]) for v in SCENARIOS
    ]:
        raise EvidenceError("Incomplete environment scenarios")
    for scenario in scenarios:
        shape(
            scenario,
            {"id", "name", "cwd", "calls", "operations", "cleanup"},
            "env scenario",
        )
        if type(scenario["cwd"]) is not str or not Path(scenario["cwd"]).is_absolute():
            raise EvidenceError("Invalid controlled environment root")
        if type(scenario["calls"]) is not int or scenario["calls"] < 0:
            raise EvidenceError("Invalid environment invocation count")
        shape(scenario["cleanup"], {"status"}, "cleanup")
        for operation in array(scenario["operations"], "operations"):
            env_operation(operation)
    return value


def env_operation(operation: Any) -> None:
    record(operation, {"op", "result"}, "environment operation")
    name = operation["op"]
    if type(name) is not str or name not in {
        "writeFile",
        "openBinaryReader",
        "reader.info",
        "reader.read",
        "reader.close",
        "exec",
        "exists",
    }:
        raise EvidenceError("Unknown environment operation")
    if name == "exec":
        array(operation.get("argv"), "literal argv")
        shape(operation.get("streams"), {"stdout", "stderr"}, "settled streams")
    else:
        array(operation.get("args"), "operation arguments")
    result = operation["result"]
    if name == "reader.close":
        if result is not UNDEFINED:
            raise EvidenceError("Invalid void reader-close result")
        return
    record(result, {"ok"}, "Result")
    if type(result["ok"]) is not bool:
        raise EvidenceError("Invalid Result discriminator")
    shape(result, {"ok", "value" if result["ok"] else "error"}, "Result")
    if not result["ok"]:
        record(
            result["error"],
            {"name", "message", "code", "path"},
            "FileError DTO",
        )
    elif name == "reader.info":
        record(
            result["value"],
            {"name", "path", "kind", "size", "mtimeMs"},
            "FileInfo DTO",
        )
    elif name == "reader.read":
        if any(
            type(b) is not int or not 0 <= b <= 255
            for b in array(result["value"], "bytes")
        ):
            raise EvidenceError("Invalid read byte")


def recovery_observation(value: Any) -> dict[str, Any]:
    shape(value, {"variants"}, "H2 observation")
    variants = array(value["variants"], "variants")
    identities = [
        (v.get("id"), v.get("stored"), v.get("current"))
        for v in variants
        if type(v) is dict
    ]
    if identities != [(f"{a}-{b}", a, b) for a, b in POLICIES]:
        raise EvidenceError("Incomplete recovery policy matrix")
    harness_fields = {
        "submission",
        "history",
        "tasks",
        "invocations",
        "requests",
        "provider",
        "usage",
        "sessionUsage",
        "live",
        "reports",
        "faux",
        "close",
    }
    for variant in variants:
        shape(
            variant,
            harness_fields
            | {
                "id",
                "stored",
                "current",
                "database",
                "admitted",
                "beforeClose",
                "reopened",
                "firstClose",
                "runs",
                "clock",
            },
            "recovery variant",
        )
        observations({key: variant[key] for key in harness_fields}, "harness-round-v1")
        if type(variant["runs"]) is not int or variant["runs"] < 0:
            raise EvidenceError("Invalid effect count")
        record(
            variant["admitted"], {"id", "conversationId", "type", "status"}, "admission"
        )
        shape(variant["database"], {"name", "header"}, "SQLite evidence")
        close_result(variant["firstClose"])
        for fact in array(variant["clock"], "fixture clock facts"):
            record(fact, {"op", "id", "at"}, "clock fact")
            if fact["op"] not in {
                "arm",
                "advance",
                "callback",
                "cancel",
                "decline-background",
            }:
                raise EvidenceError("Unknown fixture clock operation")
        for name in ("beforeClose", "reopened"):
            recovery_phase(variant[name])
        for invocation in variant["invocations"]:
            shape(
                invocation,
                {"ordinal", "phase", "taskId", "conversationId", "callId", "args"},
                "execute fact",
            )
            if (
                type(invocation["ordinal"]) is not int
                or invocation["ordinal"] < 1
                or invocation["phase"] not in ("before-close", "after-reopen")
            ):
                raise EvidenceError("Invalid execute fact")
    return value


def recovery_phase(phase: Any) -> None:
    phase = shape(
        phase,
        {"task", "tasks", "history", "provider", "live", "runs", "callCount"},
        "recovery phase",
    )
    task_record(phase["task"])
    for task in array(phase["tasks"], "phase tasks"):
        task_record(task)
    record(phase["provider"], {"sessionId"}, "persisted provider")
    for row in array(phase["history"], "phase history"):
        entry(row)


def validate_trace(value: Any) -> None:
    case = value.get("case_id") if type(value) is dict else None
    if type(case) is not str or case not in CASES:
        raise EvidenceError("Unsupported case or normalization")
    version, normalization, op = CASES[case]
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != version
    ):
        raise EvidenceError("Unsupported trace schema version")
    if value.get("normalization") != normalization:
        raise EvidenceError("Unsupported case or normalization")
    if version == 1:
        return
    shape(
        value, {"schema_version", "case_id", "normalization", "codec", "steps"}, "trace"
    )
    if value["codec"] != "tagged-js-v1":
        raise EvidenceError("Unsupported trace codec")
    steps = array(value["steps"], "steps")
    if len(steps) != 1:
        raise EvidenceError("Invalid observation step count")
    step = shape(steps[0], {"op", "value"}, "step")
    if step["op"] != op:
        raise EvidenceError("Unknown trace operation")
    observations(decode(step["value"]), case)


def pointer(parts: tuple[Any, ...]) -> str:
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


class Projection:
    """Typed paths only; one global durable-ID namespace, separate provider IDs."""

    def __init__(self, value: dict[str, Any]):
        self.value = value
        self.ids: dict[int, str | int] = {1: 1}
        self.anchors: set[str] = set()
        self.providers: dict[str, str] = {}
        self.witness: list[dict[str, Any]] = []
        self.durations: dict[str, int] = {}

    def bind(self, raw: Any, anchor: str) -> None:
        if (
            type(raw) is not int
            or raw <= 1
            or raw in self.ids
            or anchor in self.anchors
        ):
            raise EvidenceError("Ambiguous/colliding durable identity anchor")
        self.ids[raw] = anchor
        self.anchors.add(anchor)

    def change(
        self,
        obj: Any,
        key: str | int,
        after: Any,
        path: tuple[Any, ...],
        rule: str,
    ) -> None:
        before = obj[key]
        if type(before) is not type(after) or before != after:
            self.witness.append(
                {
                    "path": pointer(path + (key,)),
                    "rule": rule,
                    "before": encode(before),
                    "after": encode(after),
                }
            )
            obj[key] = after

    def identity(self, obj: dict[str, Any], key: str, path: tuple[Any, ...]) -> None:
        if key not in obj:
            return
        raw = obj[key]
        if type(raw) is not int or raw not in self.ids:
            raise EvidenceError("Unknown durable identity reference")
        self.change(obj, key, self.ids[raw], path, "durable-id-bijection-v1")

    def provider(self, obj: dict[str, Any], key: str, path: tuple[Any, ...]) -> None:
        raw = obj[key]
        if type(raw) is not str or not raw:
            raise EvidenceError("Invalid provider identity")
        if raw not in self.providers:
            self.providers[raw] = f"provider-session:{len(self.providers) + 1}"
        self.change(obj, key, self.providers[raw], path, "provider-id-bijection-v1")

    def messages(self, messages: list[Any], path: tuple[Any, ...]) -> None:
        for index, message in enumerate(messages):
            record(message, {"role", "content"}, "message")
            if message["role"] != "toolResult" or "durationMs" not in message:
                continue
            duration = message["durationMs"]
            call = message.get("toolCallId")
            if type(call) is not str or type(duration) is not int or duration < 0:
                raise EvidenceError("Invalid tool elapsed milliseconds")
            # A duplicate copy with a different value must NOT collapse to equality.
            if call in self.durations and self.durations[call] == duration:
                self.change(
                    message,
                    "durationMs",
                    {"elapsed_ms": "observed", "origin": call},
                    path + (index,),
                    "correlated-tool-elapsed-ms-v1",
                )

    def visit_entry(self, row: dict[str, Any], path: tuple[Any, ...]) -> None:
        for key in ("id", "conversationId", "head", "byTaskId"):
            self.identity(row, key, path)
        if "model" in row:
            self.messages(row["model"], path + ("model",))
        for i, edit in enumerate(row.get("edits", [])):
            self.identity(edit, "target", path + ("edits", i))

    def visit_view(self, item: dict[str, Any], name: str) -> None:
        if item["head"] is not UNDEFINED:
            self.visit_entry(item["head"], (name, "head"))
        for i, row in enumerate(item["entries"]):
            self.visit_entry(row, (name, "entries", i))
        for i, contribution in enumerate(item["contributions"]):
            self.messages(contribution, (name, "contributions", i))
        self.messages(item["messages"], (name, "messages"))

    def bind_history(self) -> None:
        seen: dict[str, int] = {}
        for row in self.value["history"]:
            models = row.get("model", [])
            role = models[0].get("role", "none") if models else "none"
            calls = role == "assistant" and any(
                v.get("type") == "toolCall" for v in models[0]["content"]
            )
            label = f"entry:{row['kind']}:{role}{':call' if calls else ''}"
            seen[label] = seen.get(label, 0) + 1
            self.bind(row["id"], f"{label}:{seen[label]}")

    def bind_tasks(self) -> None:
        for task in self.value["tasks"]:
            record(task, {"id", "kind", "conversationId", "input", "state"}, "task")
            record(task["state"], {"status"}, "task state")
            if type(task["input"]) is not dict:
                raise EvidenceError("Invalid task input")
            outcome = task["state"].get("outcome", {})
            if type(outcome) is not dict:
                raise EvidenceError("Invalid task outcome")
            result = outcome.get("result", {})
            target = result.get("entryId") if type(result) is dict else None
            suffix = (
                self.ids[target]
                if type(target) is int and target in self.ids
                else f"without-entry:{len(self.anchors)}"
            )
            self.bind(task["id"], f"task:{task['kind']}:{suffix}")

    def duration_origins(self) -> None:
        # Establish raw duration origins BEFORE mutating any history copy.
        for row in self.value["history"]:
            for message in row.get("model", []):
                if message.get("role") == "toolResult" and "durationMs" in message:
                    call, duration = message.get("toolCallId"), message["durationMs"]
                    if type(call) is not str or call in self.durations:
                        raise EvidenceError("Ambiguous elapsed-time origin")
                    self.durations[call] = duration

    def harness(self) -> None:
        self.bind_tasks()
        sub = self.value["submission"]
        self.bind(sub["id"], "submission:input")
        self.duration_origins()
        for key in ("id", "conversationId", "entry", "answer"):
            self.identity(sub, key, ("submission",))
        for i, task in enumerate(self.value["tasks"]):
            path = ("tasks", i)
            for key in ("id", "conversationId", "owner"):
                self.identity(task, key, path)
            if task["kind"] == "pi.tool":
                self.identity(task["input"], "assistant", path + ("input",))
            result = task["state"].get("outcome", {}).get("result")
            if type(result) is dict:
                self.identity(result, "entryId", path + ("state", "outcome", "result"))
        self.provider(self.value["provider"], "sessionId", ("provider",))
        for i, request in enumerate(self.value["requests"]):
            self.provider(request, "sessionId", ("requests", i))
            self.messages(request["messages"], ("requests", i, "messages"))


def scan_relations(rows: list[Any]) -> list[bool]:
    """Keep order only for captured ID-ordered scans, never arbitrary arrays."""
    ids = []
    for row in rows:
        record(row, {"id"}, "ID-ordered scan row")
        if type(row["id"]) is not int:
            raise EvidenceError("Invalid scan identity")
        ids.append(row["id"])
    return [left < right for left, right in zip(ids, ids[1:], strict=False)]


def visit_task(
    projection: Projection, task: dict[str, Any], path: tuple[Any, ...]
) -> None:
    task_record(task)
    for key in ("id", "conversationId", "owner"):
        projection.identity(task, key, path)
    if task["kind"] == "pi.tool":
        projection.identity(task["input"], "assistant", path + ("input",))
    state = task["state"]
    checkpoint = state.get("checkpoint", {})
    if task["kind"] == "pi.generation" and type(checkpoint) is dict:
        projection.identity(checkpoint, "assistant", path + ("state", "checkpoint"))
        if "tools" in checkpoint:
            visit_id_list(
                projection, checkpoint["tools"], path + ("state", "checkpoint", "tools")
            )
    if "on" in state:
        visit_id_list(projection, state["on"], path + ("state", "on"))
    outcome = record(state.get("outcome", {}), set(), "task outcome")
    result = outcome.get("result")
    if type(result) is dict:
        projection.identity(result, "entryId", path + ("state", "outcome", "result"))


def visit_id_list(projection: Projection, values: Any, path: tuple[Any, ...]) -> None:
    for i, raw in enumerate(array(values, "identity references")):
        if type(raw) is not int or raw not in projection.ids:
            raise EvidenceError("Unknown durable identity list reference")
        projection.change(
            values, i, projection.ids[raw], path, "durable-id-bijection-v1"
        )


def visit_live(projection: Projection, live: Any, path: tuple[Any, ...]) -> None:
    if type(live) is not dict:
        raise EvidenceError("Invalid LiveDoc")
    if "run" in live:
        projection.identity(live["run"], "taskId", path + ("run",))
        if "inputs" in live["run"]:
            visit_id_list(projection, live["run"]["inputs"], path + ("run", "inputs"))
    for i, slot in enumerate(live.get("tools", [])):
        for key in ("taskId", "entry"):
            projection.identity(slot, key, path + ("tools", i))


def project_recovery(value: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
    witness: list[Any] = []
    relations = {}
    for i, variant in enumerate(value["variants"]):
        projection = Projection(variant)
        relations[f"/variants/{i}/history"] = scan_relations(variant["history"])
        relations[f"/variants/{i}/tasks"] = scan_relations(variant["tasks"])
        projection.bind_history()
        # Same mapper across every persistence phase; do not re-anchor snapshots.
        projection.bind_tasks()
        projection.bind(variant["submission"]["id"], "submission:input")
        projection.duration_origins()
        for name in ("submission", "admitted"):
            for key in ("id", "conversationId", "entry", "answer"):
                projection.identity(variant[name], key, (name,))
        for j, task in enumerate(variant["tasks"]):
            visit_task(projection, task, ("tasks", j))
        for j, row in enumerate(variant["history"]):
            projection.visit_entry(row, ("history", j))
        projection.provider(variant["provider"], "sessionId", ("provider",))
        for j, request in enumerate(variant["requests"]):
            projection.provider(request, "sessionId", ("requests", j))
            projection.messages(request["messages"], ("requests", j, "messages"))
        project_invocations(projection, variant)
        visit_live(projection, variant["live"], ("live",))
        for name in ("beforeClose", "reopened"):
            project_recovery_phase(projection, variant, name, i, relations)
        for item in projection.witness:
            item["path"] = f"/variants/{i}" + item["path"]
            witness.append(item)
    return witness, relations


def project_invocations(projection: Projection, variant: Any) -> None:
    for j, invocation in enumerate(variant["invocations"]):
        for key in ("taskId", "conversationId"):
            projection.identity(invocation, key, ("invocations", j))


def project_recovery_phase(
    projection: Projection, variant: Any, name: str, i: int, relations: dict[str, Any]
) -> None:
    phase = variant[name]
    relations[f"/variants/{i}/{name}/history"] = scan_relations(phase["history"])
    relations[f"/variants/{i}/{name}/tasks"] = scan_relations(phase["tasks"])
    visit_task(projection, phase["task"], (name, "task"))
    for j, task in enumerate(phase["tasks"]):
        visit_task(projection, task, (name, "tasks", j))
    for j, row in enumerate(phase["history"]):
        projection.visit_entry(row, (name, "history", j))
    projection.provider(phase["provider"], "sessionId", (name, "provider"))
    visit_live(projection, phase["live"], (name, "live"))


def project_abort(value: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
    relations = {"/history": scan_relations(value["history"])}
    projection = Projection(value)
    projection.bind_history()
    subject = value["id"]
    projection.bind(subject, "task:abort-subject")
    projection.identity(value, "id", ())
    for name in ("admitted", "running", "abortCurrent", "final"):
        visit_task(projection, value[name], (name,))
    for i, publication in enumerate(value["publications"]):
        for j, change in enumerate(publication["changes"]):
            visit_task(
                projection, change["value"], ("publications", i, "changes", j, "value")
            )
    for i, operation in enumerate(value["late"]):
        error = operation.get("error")
        if (
            type(error) is dict
            and error.get("message") == f"Task {subject} has a durable abort mark"
        ):
            projection.change(
                error,
                "message",
                "Task task:abort-subject has a durable abort mark",
                ("late", i, "error"),
                "exact-durable-abort-subject-error-v1",
            )
    for i, row in enumerate(value["history"]):
        projection.visit_entry(row, ("history", i))
    return projection.witness, relations


def project_env(value: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
    projection = Projection(value)
    for i, scenario in enumerate(value["scenarios"]):
        root = Path(scenario["cwd"])
        if root.as_posix() != scenario["cwd"] or ".." in root.parts:
            raise EvidenceError("Invalid controlled root representation")
        for j, operation in enumerate(scenario["operations"]):
            project_env_operation(projection, root, scenario, i, operation, j)
        projection.change(
            scenario,
            "cwd",
            {"root": scenario["id"]},
            ("scenarios", i),
            "controlled-env-root-v1",
        )
    return projection.witness, {}


def project_env_operation(
    projection: Projection, root: Path, scenario: Any, i: int, operation: Any, j: int
) -> None:
    result = operation["result"]
    if type(result) is not dict:
        return
    target = result.get("value") if result["ok"] else result.get("error")
    if type(target) is not dict:
        return
    info = operation["op"] == "reader.info" and result["ok"]
    error = not result["ok"]
    path = (
        "scenarios",
        i,
        "operations",
        j,
        "result",
        "value" if result["ok"] else "error",
    )
    if (info or error) and "path" in target and target["path"] is not UNDEFINED:
        raw = target["path"]
        if type(raw) is not str:
            raise EvidenceError("Invalid environment path")
        file = Path(raw)
        if (
            not file.is_relative_to(root)
            or ".." in file.parts
            or file.as_posix() != raw
        ):
            raise EvidenceError("Environment file escaped controlled root")
        projection.change(
            target,
            "path",
            {
                "root": scenario["id"],
                "relative": file.relative_to(root).as_posix(),
            },
            path,
            "controlled-env-path-v1",
        )
    if info:
        stamp = target["mtimeMs"]
        if type(stamp) not in (int, float) or stamp < 0:
            raise EvidenceError("Invalid filesystem mtime milliseconds")
        projection.change(
            target,
            "mtimeMs",
            {"filesystem_mtime_ms": "observed"},
            path,
            "file-info-mtime-ms-v1",
        )


def project(value: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    validate_trace(value)
    if value["schema_version"] == 1:
        return copy.deepcopy(value), []
    # Decode creates fresh containers and preserves the singleton undefined value.
    decoded = observations(decode(value["steps"][0]["value"]), value["case_id"])
    case = value["case_id"]
    if case in {"tool-recovery-v1", "durable-abort-v1", "env-conformance-v1"}:
        visitor = {
            "tool-recovery-v1": project_recovery,
            "durable-abort-v1": project_abort,
            "env-conformance-v1": project_env,
        }[case]
        witness, relations = visitor(decoded)
    else:
        # Preserve only recorded ID-ordered scans, never arbitrary allocation order.
        relations = {"/history": scan_relations(decoded["history"])}
        if case == "harness-round-v1":
            relations["/tasks"] = scan_relations(decoded["tasks"])
        projection = Projection(decoded)
        projection.bind_history()
        if case == "harness-round-v1":
            projection.harness()
        else:
            projection.identity(decoded, "requested_cutoff", ())
            projection.visit_view(decoded["first"], "first")
            projection.visit_view(decoded["final"], "final")
        for i, row in enumerate(decoded["history"]):
            projection.visit_entry(row, ("history", i))
        witness = projection.witness
    result = {
        **value,
        "steps": [{**value["steps"][0], "value": encode(decoded)}],
        "scan_order_relations": relations,
    }
    return result, witness


def safe_path(value: Any) -> str:
    if type(value) is not str or not value or "\\" in value:
        raise EvidenceError("Invalid relative evidence path")
    p = Path(value)
    if (
        not p.parts
        or p.is_absolute()
        or p.as_posix() != value
        or any(v in (".", "..") for v in p.parts)
    ):
        raise EvidenceError("Unsafe relative evidence path")
    return value


def digest(value: Any) -> None:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise EvidenceError("Invalid artifact hash")


def registry(
    config: Any,
    root: Path,
    fixtures: Path,
    load: Callable[..., Any],
    sha: Callable[..., str],
    driver: Path,
) -> None:
    shape(
        config,
        {
            "schema_version",
            "reference",
            "runtime",
            "adapters",
            "comparison",
            "cases",
            "python_adapter",
            "parity_rows",
        },
        "manifest",
    )
    if type(config["schema_version"]) is not int or config["schema_version"] != 2:
        raise EvidenceError("Unsupported manifest version")
    if config["python_adapter"] != "OPEN" or config["parity_rows"] != "OPEN":
        raise EvidenceError("Unsupported parity claim")
    shape(config["adapters"], set(ADAPTERS), "adapter closure")
    for source, dest in ADAPTERS.items():
        item = shape(config["adapters"][source], {"destination", "sha256"}, "adapter")
        if item["destination"] != dest:
            raise EvidenceError("Adapter destination drift")
        digest(item["sha256"])
        path = driver if source == "scripts/durable_pi_reference.mts" else root / source
        if (
            path.is_symlink()
            or path.resolve() != path.absolute()
            or sha(path) != item["sha256"]
        ):
            raise EvidenceError(
                "Driver hash drift" if path == driver else "Adapter hash drift"
            )
    shape(config["comparison"], COMPARISON, "comparison closure")
    for source, expected in config["comparison"].items():
        digest(expected)
        if (root / source).resolve() != (root / source).absolute() or sha(
            root / source
        ) != expected:
            raise EvidenceError("Comparison artifact hash drift")
    cases = array(config["cases"], "cases")
    ids: set[str] = set()
    paths: set[str] = set()
    for case in cases:
        _case(case, fixtures, load, sha, ids, paths)
    if ids != set(CASES):
        raise EvidenceError("Incomplete required case registry")


def _case(
    case: Any,
    fixtures: Path,
    load: Callable[..., Any],
    sha: Callable[..., str],
    ids: set[str],
    paths: set[str],
) -> None:
    shape(
        case,
        {
            "id",
            "input",
            "expected",
            "trace_schema",
            "normalization",
            "adapter",
            "required_modules",
            "upstream_tests",
        },
        "case",
    )
    name = case["id"]
    if type(name) is not str or name not in CASES or name in ids:
        raise EvidenceError("Unknown or duplicate case")
    ids.add(name)
    version, profile, _ = CASES[name]
    if (
        type(case["trace_schema"]) is not int
        or case["trace_schema"] != version
        or case["normalization"] != profile
    ):
        raise EvidenceError("Unsupported case contract")
    if case["adapter"] != "durable_pi_reference.mts":
        raise EvidenceError("Unknown case adapter")
    for key in ("input", "expected"):
        _fixture(case[key], key, name, fixtures, load, sha, paths)
    _case_sources(case, name)


def _fixture(
    item: Any,
    key: str,
    name: str,
    fixtures: Path,
    load: Callable[..., Any],
    sha: Callable[..., str],
    paths: set[str],
) -> None:
    item = shape(item, {"path", "sha256"}, "fixture")
    relative = safe_path(item["path"])
    if relative in paths or relative != name + (
        ".input.json" if key == "input" else ".json"
    ):
        raise EvidenceError("Aliased fixture path")
    paths.add(relative)
    digest(item["sha256"])
    path = fixtures / relative
    if (
        path.is_symlink()
        or path.resolve() != path.absolute()
        or sha(path) != item["sha256"]
    ):
        raise EvidenceError("Fixture hash drift")
    value = load(path)
    if key == "input":
        validate_input(value, name)
    if key == "expected":
        validate_trace(value)
        if value["case_id"] != name:
            raise EvidenceError("Fixture case identity drift")


def _case_sources(case: dict[str, Any], name: str) -> None:
    if case["required_modules"] != MODULES[name]:
        raise EvidenceError("Case module anchor drift")
    tests = case["upstream_tests"]
    if type(tests) is not list or any(type(v) is not dict for v in tests):
        raise EvidenceError("Invalid upstream tests")
    if [(v.get("path"), v.get("name")) for v in tests] != TESTS[name]:
        raise EvidenceError("Case source test identity drift")
    for key in ("required_modules", "upstream_tests"):
        items = array(case[key], key)
        if not items:
            raise EvidenceError("Missing case source provenance")
        for item in items:
            if key == "required_modules":
                safe_path(item)
            else:
                shape(item, {"path", "name", "sha256"}, "upstream test")
                safe_path(item["path"])
                digest(item["sha256"])
                if type(item["name"]) is not str or not item["name"]:
                    raise EvidenceError("Missing source test identity")
