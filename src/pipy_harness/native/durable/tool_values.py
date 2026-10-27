"""Closed DE08 tool intents and full-content receipts, without live registrations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, NoReturn, cast

from pipy_harness.native.agent.messages import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.agent.tools import ToolExecutionOutcome
from pipy_harness.native.tools.base import ToolDefinition

from .canonical_codec import decode_canonical, encode_canonical
from .codec import OwnedJson
from .generation_tasks import tool_input
from .records import shape
from .tool_output_values import check_ending, project_tool_output

Tree = dict[str, Any]


class DurableToolError(ValueError):
    """Fixed diagnostics: scheduler persists exception text."""


def refuse() -> NoReturn:
    raise DurableToolError("Durable tool execution is incompatible") from None


def _model_declaration(value: object) -> OwnedJson:
    """Compare a complete supported declaration in the codec's schema-list form."""
    if type(value) is not ToolDefinition:
        refuse()
    # Encode first: unsupported metadata/hooks must not disappear in projection.
    return OwnedJson(encode_canonical(decode_canonical(encode_canonical(value))))


def declaration(value: object) -> Tree:
    try:
        result = shape(
            value,
            {
                "implementation",
                "revision",
                "schema",
                "schema_mode",
                "output_schema",
                "execution_mode",
                "replay",
            },
        )
        if any(
            type(result[k]) is not str or not result[k]
            for k in ("implementation", "revision")
        ):
            refuse()
        if (
            result["replay"] not in ("safe", "unsafe")
            or type(result["replay"]) is not str
        ):
            refuse()
        if (
            result["schema_mode"] not in ("strict", "foreign")
            or type(result["schema"]) is not dict
        ):
            refuse()
        if (
            result["output_schema"] is not None
            and type(result["output_schema"]) is not dict
        ):
            refuse()
        if result["execution_mode"] not in (None, "parallel", "sequential"):
            refuse()
        return cast(Tree, OwnedJson(result).thaw())
    except (ValueError, TypeError, KeyError, IndexError, RecursionError, OverflowError):
        refuse()


def intent(value: object) -> Tree:
    try:
        result = shape(value, {"call", "key", "definition", "arguments", "workspace"})
        tool_input(result["call"])
        if (
            type(result["key"]) is not str
            or not result["key"]
            or type(result["workspace"]) is not str
            or not result["workspace"]
        ):
            refuse()
        if type(result["arguments"]) is not dict:
            refuse()
        declaration(result["definition"])
        return cast(Tree, OwnedJson(result).thaw())
    except (ValueError, TypeError, KeyError, IndexError, RecursionError, OverflowError):
        refuse()


def checkpoint(value: object) -> Tree:
    try:
        result = OwnedJson(value).thaw()
        if result == {"phase": "call"}:
            return result
        if (
            type(result) is not dict
            or result.get("version") != 1
            or type(result.get("version")) is not int
        ):
            refuse()
        phase = result.get("phase")
        if phase in ("execute", "blocked"):
            unresolved = phase == "blocked" and result.get("intent") is None
            shape(
                result,
                {"phase", "version", "intent"}
                | ({"reason"} if phase == "blocked" else set())
                | ({"call"} if unresolved else set()),
            )
            if unresolved:
                tool_input(result["call"])
                if result["reason"] != "incompatible":
                    refuse()
            else:
                intent(result["intent"])
            if phase == "blocked" and result["reason"] not in (
                "uncertain",
                "incompatible",
                "invalid_result",
            ):
                refuse()
        elif phase == "settle":
            shape(result, {"phase", "version", "key"})
            if type(result["key"]) is not str or not result["key"]:
                refuse()
        else:
            refuse()
        return result
    except (ValueError, TypeError, KeyError, IndexError, RecursionError, OverflowError):
        refuse()


def native_tool_output(
    outcome: ToolExecutionOutcome,
    completed: datetime,
    *,
    diagnostics: list[Tree] | None = None,
    failure: str | None = None,
) -> Tree:
    tree: Tree = {
        "version": 1,
        "outcome": encode_canonical(outcome),
        "completedAt": encode_canonical(completed),
    }
    if diagnostics or failure is not None:
        tree.update(
            version=2,
            ending="completed" if failure is None else "failed",
            diagnostics=[] if diagnostics is None else diagnostics,
            recovery=None
            if failure is None
            else {"reason": "tool_error", "category": failure},
        )
    return read_native_tool_output(tree)[0]


def read_native_tool_output(value: object) -> tuple[Tree, ToolExecutionOutcome]:
    try:
        tree = cast(Tree, OwnedJson(value).thaw())
        if type(tree.get("version")) is not int or tree["version"] not in (1, 2):
            refuse()
        shape(
            tree,
            {"version", "outcome", "completedAt"}
            | (
                {"ending", "diagnostics", "recovery"} if tree["version"] == 2 else set()
            ),
        )
        outcome = decode_canonical(tree["outcome"])
        if (
            type(outcome) is not ToolExecutionOutcome
            or type(decode_canonical(tree["completedAt"])) is not datetime
        ):
            refuse()
        if tree["version"] == 2:
            check_ending(tree, outcome)
            project_tool_output(tree, outcome)
        return tree, outcome
    except (ValueError, TypeError, KeyError, IndexError, RecursionError, OverflowError):
        refuse()


def receipt(value: object) -> tuple[Tree, ToolExecutionOutcome]:
    try:
        tree = cast(Tree, OwnedJson(value).thaw())
        shape(tree, {"version", "intent", "call", "key", "nativeToolOutput"})
        if type(tree["version"]) is not int or tree["version"] not in (1, 2):
            refuse()
        tool_input(tree["call"])
        if type(tree["key"]) is not str or not tree["key"]:
            refuse()
        if tree["intent"] is not None:
            final = intent(tree["intent"])
            if final["call"] != tree["call"] or final["key"] != tree["key"]:
                refuse()
        output, outcome = read_native_tool_output(tree["nativeToolOutput"])
        if tree["version"] != output["version"]:
            refuse()
        return tree, outcome
    except (ValueError, TypeError, KeyError, IndexError, RecursionError, OverflowError):
        refuse()


def match_result(message: AgentToolResultMessage, call: AgentToolCall) -> None:
    if (
        message.tool_name != call.tool_name
        or message.provider_correlation_id != call.provider_correlation_id
    ):
        refuse()


def entry_tool_output(value: object) -> tuple[Tree, ToolExecutionOutcome, Tree, str]:
    """Read the one final carrier, without reviving retired task documents.

    New entries retain the actual receipt2 read by settlement. Historical
    fact1 entries keep their old carrier and do not acquire a receipt duty.
    The private receipt/intent is never part of the returned public full fact.
    """
    try:
        if type(value) is not dict:
            refuse()
        if "nativeToolReceipt" in value:
            shape(value, {"nativeToolReceipt"})
            saved, outcome = receipt(value["nativeToolReceipt"])
            if saved["version"] != 2:
                refuse()
            return saved["nativeToolOutput"], outcome, saved["call"], saved["key"]
        full, outcome = read_native_tool_output(value["nativeToolOutput"])
        if full["version"] != 1:
            refuse()
        return full, outcome, value["call"], value["key"]
    except (ValueError, TypeError, KeyError, IndexError):
        refuse()


def blocked_record(record: Tree) -> bool:
    from .generation_tasks import (
        GENERATION_NAME,
        GENERATION_VERSION,
        TOOL_NAME,
        TOOL_VERSION,
    )

    if record["state"]["status"] not in ("pending", "running"):
        return False
    raw = record["state"]["checkpoint"]
    if type(raw) is not dict or raw.get("phase") != "blocked":
        return False
    if (record["kind"], record["version"]) == (TOOL_NAME, TOOL_VERSION):
        try:
            value = checkpoint(raw)
            call = value["call"] if value["intent"] is None else value["intent"]["call"]
            return bool(call == record["input"])
        except DurableToolError:
            return False
    if (record["kind"], record["version"]) == (GENERATION_NAME, GENERATION_VERSION):
        from .checkpoint_codec import decode_checkpoint

        try:
            decode_checkpoint(
                raw,
                name=GENERATION_NAME,
                version=GENERATION_VERSION,
                phase="blocked",
                role="provider_outcome",
            )
            return True
        except ValueError:
            return False
    return False


def blocked_transition(current: Tree, state: object) -> bool:
    if type(state) is not dict or state.get("status") != "pending":
        return False
    candidate = {**current, "state": state}
    if not blocked_record(candidate):
        return False
    raw = state["checkpoint"]
    if raw.get("intent") is None and "call" in raw and raw["call"] != current["input"]:
        refuse()
    if (
        "intent" in raw
        and raw["intent"] is not None
        and raw["intent"]["call"] != current["input"]
    ):
        refuse()
    return True
