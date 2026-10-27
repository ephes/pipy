"""Versioned full-content CLI output from committed, public completed facts.

This is not a workflow archive serializer. Request/material/audit and arbitrary
terminal error payloads have no route into this envelope.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pipy_harness.native.agent.content import (
    NO_STRUCTURED_CONTENT,
    ImageContent,
    TextContent,
    ThinkingContent,
)
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.tool_details import (
    LEGACY_TOOL_DETAILS_NONE,
    NO_TOOL_DETAILS,
    tool_details_fields,
)
from pipy_harness.native.agent.usage_json import usage_to_json
from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.completed_values import (
    CompletedMessage,
    TerminalOutcome,
)
from pipy_harness.native.durable.product_values import DurableRunResult
from pipy_harness.native.durable.tool_values import read_native_tool_output


def json_output(value: object) -> str:
    """Encode the existing OwnedJson domain without a new nesting cutoff."""
    work: list[tuple[bool, object]] = [(False, OwnedJson(value).thaw())]
    output: list[str] = []
    while work:
        literal, current = work.pop()
        if literal:
            assert type(current) is str
            output.append(current)
        elif type(current) is list or type(current) is dict:
            is_object = type(current) is dict
            output.append("{" if is_object else "[")
            work.append((True, "}" if is_object else "]"))
            rows = (
                list(current.items())
                if type(current) is dict
                else list(enumerate(current))
            )
            for index in range(len(rows) - 1, -1, -1):
                key, child = rows[index]
                work.append((False, child))
                if is_object:
                    work.append((True, ":"))
                    work.append((False, key))
                if index:
                    work.append((True, ","))
        else:
            output.append(json.dumps(current, ensure_ascii=True, allow_nan=False))
    return "".join(output) + "\n"


def block(value: object) -> dict[str, Any]:
    if type(value) is TextContent:
        return {"type": "text", "text": value.text, "signature": value.signature}
    if type(value) is ThinkingContent:
        return {
            "type": "thinking",
            "thinking": value.thinking,
            "signature": value.signature,
            "redacted": value.redacted,
        }
    if type(value) is ImageContent:
        return {"type": "image", "data": value.data, "mime_type": value.mime_type}
    if type(value) is AgentToolCall:
        return {
            "type": "tool_call",
            "provider_correlation_id": value.provider_correlation_id,
            "tool_name": value.tool_name,
            "arguments_json": value.arguments_json.value,
            "thought_signature": value.thought_signature,
        }
    raise ValueError("Unsupported completed content")


def message(value: object) -> dict[str, Any]:
    if type(value) is AgentUserMessage:
        return {"role": "user", "content": value.content.value}
    if type(value) is AgentAssistantMessage:
        return {
            "role": "assistant",
            "content": value.content.value,
            "tool_calls": [block(c) for c in value.tool_calls],
            "blocks": [block(b) for b in value.blocks],
            "stop_reason": None
            if value.stop_reason is None
            else value.stop_reason.value,
            "error_message": value.error_message,
            "usage": None if value.usage is None else usage_to_json(value.usage),
            "provider": value.provider,
            "api": value.api,
            "model": value.model,
            "provider_thinking_level": value.provider_thinking_level,
        }
    if type(value) is AgentToolResultMessage:
        result = {
            "role": "tool_result",
            "tool_request_id": value.tool_request_id,
            "tool_name": value.tool_name,
            "content": value.content.value,
            "provider_correlation_id": value.provider_correlation_id,
            "is_error": value.is_error,
            "details_state": "absent"
            if value.details is NO_TOOL_DETAILS
            else "legacy_unspecified"
            if value.details is LEGACY_TOOL_DETAILS_NONE
            else "value",
            **tool_details_fields(value.details),
            **(
                {"nested_call_evidence": value.nested_call_evidence.to_json()}
                if value.nested_call_evidence is not None
                else {}
            ),
            "blocks": [block(b) for b in value.blocks],
            "terminate": value.terminate,
        }
        if value.structured_content is not NO_STRUCTURED_CONTENT:
            result["structured_content"] = OwnedJson(value.structured_content).thaw()
        return result
    raise ValueError("Unsupported completed message")


def completed_message(value: CompletedMessage) -> dict[str, Any]:
    result = {
        "conversation_id": value.conversation_id,
        "entry_id": value.entry_id,
        "commit_seq": value.commit_seq,
        "by_task_id": value.by_task_id,
        "message": message(value.decode()),
        # These are public completed payloads, not checkpoint frames: no saved
        # request/settings/material. Keep their versioned canonical representation
        # to preserve every provider/native-output field and presence distinction.
        "provider_outcome": None
        if value.provider_outcome is None
        else value.provider_outcome.thaw(),
        "tool_output": None if value.tool_output is None else value.tool_output.thaw(),
    }
    if value.tool_output is not None:
        raw, outcome = read_native_tool_output(value.tool_output.thaw())
        completed_at = decode_canonical(raw["completedAt"])
        assert type(completed_at) is datetime
        execution: dict[str, Any] = {
            "malformed_arguments": outcome.malformed_arguments,
            "interruption": outcome.interruption.value,
            "cancelled": outcome.cancelled,
            "execution_seconds": outcome.execution_seconds,
            "completed_at": completed_at.isoformat(),
        }
        result["tool_execution"] = execution
        if raw["version"] == 2:
            execution.update(
                ending=raw["ending"],
                diagnostics=raw["diagnostics"],
                recovery=raw["recovery"],
            )
    return result


def terminal(value: TerminalOutcome) -> dict[str, Any]:
    outcome = value.outcome.thaw()
    if type(outcome) is not dict or type(outcome.get("status")) is not str:
        raise ValueError("Unsupported terminal outcome")
    status = outcome["status"]
    if status not in {"completed", "failed", "aborted", "orphaned", "faulted"}:
        raise ValueError("Unsupported terminal outcome")
    # Terminal task payloads can contain private diagnostics. They are not the
    # public message channel and never get a generic serializer here.
    family = {"pi.generation": "generation", "pi.tool": "model_tool"}.get(
        value.kind, "custom"
    )
    result: dict[str, Any] = {
        "task_id": value.task_id,
        "family": family,
        "version": value.version,
        "status": status,
    }
    if outcome.get("reason") == "operator_retry":
        result["reason"] = "operator_retry"
    target = outcome.get("result")
    if family != "custom" and type(target) is dict:
        entry_id = target.get("entryId")
        if type(entry_id) is int and 0 <= entry_id <= 2**53 - 1:
            result["result_entry_id"] = entry_id
    return result


def run_output(value: DurableRunResult) -> dict[str, Any]:
    snapshot = value.completed
    return {
        "format": "pipy-durable-output",
        "version": 2,
        "operation": "run",
        "task_id": value.task_id,
        "status": value.status,
        "inspection": value.inspection.to_tree(),
        "completed": {
            "conversation_id": snapshot.conversation_id,
            "revision": snapshot.revision,
            "publication_seq": snapshot.publication_seq,
            "messages": [completed_message(m) for m in snapshot.messages],
            "rounds": [
                {"assistant_id": r.assistant_id, "result_ids": list(r.result_ids)}
                for r in snapshot.rounds
            ],
            "outcomes": [terminal(outcome) for outcome in snapshot.outcomes],
            "usage": None if snapshot.usage is None else snapshot.usage.thaw(),
        },
    }


def final_text(value: DurableRunResult) -> str:
    for row in reversed(value.completed.messages):
        decoded = row.decode()
        if type(decoded) is AgentAssistantMessage:
            return decoded.content.value
    return ""
