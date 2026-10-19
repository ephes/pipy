"""Opt-in real AgentLoop replay experiment; no production entry point imports it."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from loop_codec import dumps, loads
from runtime import BLOCKED_EXIT, Faults, Store, steps

from pipy_harness.native.agent.active_input import AgentActiveInput
from pipy_harness.native.agent.content import (
    ImageContent,
    ProductContent,
    TextContent,
    ThinkingContent,
)
from pipy_harness.native.agent.events import (
    AgentRunSettled,
    MessageCompleted,
    ToolCallCompleted,
)
from pipy_harness.native.agent.loop import (
    AgentLoop,
    AgentLoopRequestPreparation,
    AgentLoopRunInput,
)
from pipy_harness.native.agent.loop_policy import (
    AgentToolPolicyDecision,
    AgentToolPolicyState,
)
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.agent.provider_turn import ProviderTurnExecutor
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.agent.usage import AgentTokenPricing, AgentUsageAccumulator
from pipy_harness.native.agent_adapters import (
    NativeProductSessionActionSink,
    ProductSessionEventProjection,
    SynchronousAgentEventComposite,
)
from pipy_harness.native.agent_loop_policy import (
    NativeAgentToolPolicy,
    materialize_provider_request,
)
from pipy_harness.native.agent_runtime import (
    NativeAgentQueuedInputPort,
    NativeAgentUsagePublisher,
)
from pipy_harness.native.coding.agent_run import (
    AgentLoopProviderTurnAdapter,
    AgentLoopRequestSourceAdapter,
    AgentLoopStatusPolicyAdapter,
)
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.providers.replay_content import (
    ReplayTarget,
    transform_assistant_blocks,
)
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    ToolFilterOptions,
)
from pipy_harness.native.tools.bash import BashTool
from pipy_harness.native.tools.read import ReadTool
from pipy_harness.native.tools.write import WriteTool
from pipy_harness.status import HarnessStatus

KIND = "canonical-loop-replay-v1"
IDENTITY = {
    "provider": "durable-offline",
    "model": "rich-fixture-v1",
    "api": "durable-fixture",
}
EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations(key TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS loop_meta(key TEXT PRIMARY KEY, body TEXT NOT NULL);
"""


class ReplayBlocked(Exception):
    """Stop this process without manufacturing a canonical result."""


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def code_pin():
    native = Path(__file__).resolve().parents[2] / "src" / "pipy_harness" / "native"
    files = [
        *sorted(native.rglob("*.py")),
        Path(__file__),
        Path(__file__).with_name("loop_codec.py"),
        Path(__file__).with_name("runtime.py"),
    ]
    return digest(
        "".join(
            str(path.relative_to(native.parent.parent.parent))
            + ":"
            + digest(path.read_text())
            for path in files
        )
    )


def configuration(workspace, scenario, terminate=False):
    workspace = workspace.resolve()
    stat = workspace.stat()
    return {
        "kind": KIND,
        "format": 1,
        "workspace": str(workspace),
        "workspace_identity": [stat.st_dev, stat.st_ino],
        "scenario": scenario,
        "terminate": terminate,
        "identity": IDENTITY,
        "code": code_pin(),
        "prompt": "Run the offline rich native-tool fixture.",
        "system": "Durable canonical-loop integration experiment.",
        "tools": steps(scenario),
        "budget": 4,
        "pricing": [1.0, 2.0, 0.5, 1.25],
    }


class LoopStore(Store):
    def __init__(self, path, faults):
        super().__init__(path, faults)
        try:
            row = self.db.execute("SELECT config FROM admission").fetchone()
            if row and json.loads(row[0]).get("kind") != KIND:
                raise ValueError("database belongs to a different prototype")
            self.db.executescript(EXTRA_SCHEMA)
        except BaseException:
            self.close()
            raise

    def admit(self, request_id, config):
        encoded = json.dumps(config, sort_keys=True)
        row = self.db.execute("SELECT * FROM admission").fetchone()
        if row:
            if row["request_id"] != request_id or json.loads(
                row["config"]
            ) != json.loads(encoded):
                raise ValueError("admission identity/configuration changed")
            return
        with self.transaction("loop.admission") as tx:
            tx.execute(
                "INSERT INTO admission VALUES(1,?,?,'active')", (request_id, encoded)
            )
        self.faults.hit("admitted")

    def blocked(self, reason):
        with self.transaction("loop.blocked") as tx:
            changed = tx.execute(
                "UPDATE admission SET phase='blocked' WHERE phase='active'"
            ).rowcount
            if changed:
                tx.execute(
                    "INSERT OR REPLACE INTO loop_meta VALUES('blocked',?)",
                    (json.dumps(reason),),
                )
        raise ReplayBlocked(reason)

    def valid_config(self):
        config = self.config()
        try:
            expected = configuration(
                Path(config["workspace"]), config["scenario"], config["terminate"]
            )
        except OSError:
            self.blocked("pinned workspace unavailable")
        if config != json.loads(json.dumps(expected)):
            self.blocked("pinned configuration changed")
        return config

    def validate_receipts(self):
        receipts = {row["id"]: row for row in self.db.execute("SELECT * FROM tasks")}
        for row in receipts.values():
            payload = json.loads(row["payload"])
            if not isinstance(payload.get("fingerprint"), str):
                self.blocked("receipt missing fingerprint")
            if row["phase"] == "done" and "result" not in payload:
                self.blocked("committed receipt missing result")
        for row in self.db.execute("SELECT task_id,body FROM entries").fetchall():
            key = row["task_id"]
            if key != "input" and (
                key not in receipts or receipts[key]["phase"] != "done"
            ):
                self.blocked("committed message missing completed receipt")
            message = loads(row["body"])
            if isinstance(message, AgentAssistantMessage):
                for call in message.tool_calls:
                    if (
                        "t" + call.provider_correlation_id.rsplit("-", 1)[1]
                        not in receipts
                    ):
                        self.blocked("admitted tool missing receipt")

    def receipt(self, key, kind, position, fingerprint, safe=False):
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (key,)).fetchone()
        if row:
            payload = json.loads(row["payload"])
            if payload.get("fingerprint") != fingerprint:
                self.blocked("receipt fingerprint changed")
            if row["phase"] == "done":
                if "result" not in payload:
                    self.blocked("committed receipt missing result")
                return loads(payload["result"])
            if row["phase"] == "running" and not (payload.get("safe") and safe):
                with self.transaction("loop.uncertain") as tx:
                    tx.execute("UPDATE tasks SET phase='uncertain' WHERE id=?", (key,))
                self.blocked("unsafe tool interrupted")
            if row["phase"] not in ("pending", "running"):
                self.blocked("receipt is not runnable")
        else:
            if self.db.execute(
                "SELECT 1 FROM entries WHERE task_id=?", (key,)
            ).fetchone():
                self.blocked("committed message missing receipt")
            with self.transaction("loop.intent") as tx:
                self.task(
                    tx, key, kind, position, {"fingerprint": fingerprint, "safe": safe}
                )
        return None

    def started(self, key):
        with self.transaction(key + ".intent") as tx:
            tx.execute(
                "UPDATE tasks SET phase='running',attempts=attempts+1 WHERE id=?",
                (key,),
            )

    def finished(self, key, result):
        row = self.db.execute("SELECT payload FROM tasks WHERE id=?", (key,)).fetchone()
        payload = json.loads(row[0])
        payload["result"] = dumps(result)
        with self.transaction(key + ".receipt") as tx:
            tx.execute(
                "UPDATE tasks SET phase='done',payload=? WHERE id=?",
                (json.dumps(payload), key),
            )
        self.faults.hit(key + ".receipt")

    def append_message(self, message):
        if isinstance(message, AgentUserMessage):
            key = "input"
        elif isinstance(message, AgentAssistantMessage):
            key = "g" + str(self.turn)
        elif isinstance(message, AgentToolResultMessage):
            key = "t" + message.provider_correlation_id.rsplit("-", 1)[1]
        else:
            raise ValueError("unexpected experimental message type")
        body = dumps(message)
        previous = self.db.execute(
            "SELECT body FROM entries WHERE task_id=?", (key,)
        ).fetchone()
        if previous:
            if previous[0] != body:
                self.blocked("canonical message changed during replay")
            return
        with self.transaction(key + ".message") as tx:
            tx.execute("INSERT INTO entries(task_id,body) VALUES(?,?)", (key, body))
            if isinstance(message, AgentAssistantMessage):
                for index, call in enumerate(message.tool_calls):
                    tool_key = "t" + str(index)
                    fingerprint = dumps(
                        {
                            "call": call,
                            "arguments": json.loads(call.arguments_json.value),
                        }
                    )
                    safe = self.config()["tools"][index][2]
                    self.task(
                        tx,
                        tool_key,
                        "tool",
                        index,
                        {"fingerprint": fingerprint, "safe": safe},
                    )
        self.faults.hit(key + ".message")

    def publish(self, key, body):
        # A fresh connection cannot see uncommitted writes on self.db.
        with sqlite3.connect(self.path, timeout=0) as observer:
            if key == "settled":
                row = observer.execute(
                    "SELECT body FROM loop_meta WHERE key='settled'"
                ).fetchone()
            else:
                row = observer.execute(
                    "SELECT body FROM entries WHERE task_id=?", (key,)
                ).fetchone()
            if row is None or row[0] != body:
                raise RuntimeError("publication preceded an externally visible commit")
            if key.startswith(("g", "t")):
                receipt = observer.execute(
                    "SELECT phase FROM tasks WHERE id=?", (key,)
                ).fetchone()
                if receipt is None or receipt[0] != "done":
                    raise RuntimeError("publication lacks completed external receipt")
            old = observer.execute(
                "SELECT body FROM observations WHERE key=?", (key,)
            ).fetchone()
            if old and old[0] != body:
                raise RuntimeError("published fact changed")
            observer.execute(
                "INSERT OR IGNORE INTO observations VALUES(?,?)", (key, body)
            )
        self.faults.hit(key + ".published")

    def publish_pending(self):
        for row in self.db.execute(
            "SELECT task_id,body FROM entries ORDER BY sequence"
        ).fetchall():
            self.publish(row[0], row[1])
        final = self.db.execute(
            "SELECT body FROM loop_meta WHERE key='settled'"
        ).fetchone()
        if final:
            self.publish("settled", final[0])

    def summary(self):
        value = super().summary()
        final = self.db.execute(
            "SELECT body FROM loop_meta WHERE key='settled'"
        ).fetchone()
        value["run"] = json.loads(final[0]) if final else None
        value["observations"] = self.db.execute(
            "SELECT count(*) FROM observations"
        ).fetchone()[0]
        blocked = self.db.execute(
            "SELECT body FROM loop_meta WHERE key='blocked'"
        ).fetchone()
        value["blocked_reason"] = json.loads(blocked[0]) if blocked else None
        return value


class RichTool:
    def __init__(self, native, terminate):
        self.native, self.terminate = native, terminate

    @property
    def definition(self):
        return self.native.definition

    def invoke(self, request, context):
        result = self.native.invoke(request, context)
        return replace(
            result,
            details={"fixture": {"ordered": [1, "é", None]}},
            structured_content={"nested": [False, {"receipt": request.tool_name}]},
            blocks=(
                TextContent(result.output_text),
                ImageContent("aW1hZ2U=", "image/png"),
            ),
            terminate=self.terminate,
        )


class ReceiptTools:
    def __init__(self, store, config):
        self.store, self.config = store, config
        registry = {
            "write": WriteTool(),
            "read": ReadTool(),
            "bash": BashTool(timeout_seconds=5),
        }
        self.native = NativeToolCapabilities(
            {
                name: RichTool(tool, config["terminate"])
                for name, tool in registry.items()
            },
            {},
            workspace_root=Path(config["workspace"]),
            filter_options=ToolFilterOptions(),
            cancel_join_timeout_seconds=2,
        )

    def definitions(self, allowed_names=None, /):
        return self.native.definitions(allowed_names)

    def validated_arguments(self, call, /):
        return self.native.validated_arguments(call)

    def error_result(self, call, output_text, /):
        return self.native.error_result(call, output_text)

    def execute(
        self, call, *, output_sink=None, wait_for_interrupt=None, arguments=None
    ):
        index = int(call.provider_correlation_id.rsplit("-", 1)[1])
        key = f"t{index}"
        fingerprint = dumps({"call": call, "arguments": arguments})
        cached = self.store.receipt(
            key, "tool", index, fingerprint, self.config["tools"][index][2]
        )
        if cached is not None:
            return cached
        self.store.started(key)
        self.store.faults.hit(key + ".intent")
        result = self.native.execute(
            call,
            output_sink=output_sink,
            wait_for_interrupt=wait_for_interrupt,
            arguments=arguments,
        )
        self.store.faults.hit(key + ".effect")
        self.store.finished(key, result)
        return result


class OfflineProvider:
    supports_tool_calls = True
    name, model_id = IDENTITY["provider"], IDENTITY["model"]

    def __init__(self, store, config):
        self.store, self.config = store, config

    def complete(
        self, request, *, stream_sink=None, reasoning_sink=None, cancel_token=None
    ):
        turn = request.provider_turn_index
        if reasoning_sink:
            reasoning_sink("transient reasoning")
        if stream_sink:
            stream_sink("transient text")
        self.store.faults.hit(f"g{turn}.stream")
        calls = (
            tuple(
                ProviderToolCall(
                    f"fixture-call-{index}",
                    name,
                    json.dumps(arguments, ensure_ascii=False, indent=2),
                    thought_signature=f"call-signature-{index}",
                )
                for index, (name, arguments, _) in enumerate(self.config["tools"])
            )
            if turn == 0
            else ()
        )
        blocks = (
            ThinkingContent("ordered reasoning", "thinking-signature"),
            ThinkingContent("[Reasoning redacted]", "opaque-redacted", True),
            TextContent("before é", "text-signature"),
            *calls,
            TextContent("after", "trailing-signature"),
        )
        now = datetime(2026, 10, 19, tzinfo=UTC)
        return ProviderResult(
            HarnessStatus.SUCCEEDED,
            self.name,
            self.model_id,
            now,
            now,
            final_text="before éafter",
            usage={
                "input_tokens": 100,
                "output_tokens": 20,
                "reasoning_tokens": 7,
                "cached_tokens": 30,
                "cache_write_tokens": 10,
                "cache_write_1h_tokens": 4,
                "total_tokens": 127,
            },
            tool_calls=calls,
            content_blocks=blocks,
            api=IDENTITY["api"],
            provider_thinking_level="high",
        )


class LoopAdapters:
    def __init__(self, store, config):
        self.store, self.config = store, config
        self.usage = AgentUsageAccumulator(AgentTokenPricing(*config["pricing"]))
        projection = ProductSessionEventProjection(
            NativeProductSessionActionSink(store.append_message)
        )
        self.events = SynchronousAgentEventComposite((projection, self))

    def prepare(self, history, active_input, turn_index, available_tools):
        self.store.turn = turn_index
        request = ProviderRequest(
            self.config["system"],
            self.config["prompt"],
            IDENTITY["provider"],
            IDENTITY["model"],
            Path(self.config["workspace"]),
            provider_turn_index=turn_index,
            messages=active_input.request_messages(history),
            available_tools=available_tools,
        )
        return AgentLoopRequestPreparation(history, snapshot_provider_request(request))

    def complete(self, snapshot, event_sink, turn_index):
        request = materialize_provider_request(snapshot)
        target = ReplayTarget.of(request, IDENTITY["api"])
        views = tuple(
            transform_assistant_blocks(m, target)
            for m in request.messages
            if isinstance(m, AgentAssistantMessage)
        )
        fingerprint = dumps(
            {
                "identity": self.config["identity"],
                "system": request.system_prompt,
                "user": request.user_prompt,
                "messages": request.messages,
                "tools": [
                    (d.name, d.description, d.input_schema)
                    for d in request.available_tools
                ],
                "target_views": views,
                "turn": turn_index,
                "workspace": str(request.cwd),
            }
        )
        key = f"g{turn_index}"
        cached = self.store.receipt(key, "provider", turn_index, fingerprint, True)
        if cached is not None:
            return cached
        self.store.started(key)
        result = ProviderTurnExecutor().complete(
            OfflineProvider(self.store, self.config),
            request,
            event_sink,
            turn_index=turn_index,
        )
        self.store.faults.hit(key + ".response")
        self.store.finished(key, result)
        return result

    def emit(self, event):
        if isinstance(event, (MessageCompleted, ToolCallCompleted)):
            # The real product projection has already committed this exact object.
            message = (
                event.message if isinstance(event, MessageCompleted) else event.result
            )
            key = (
                "input"
                if isinstance(message, AgentUserMessage)
                else (
                    f"g{self.store.turn}"
                    if isinstance(message, AgentAssistantMessage)
                    else "t" + message.provider_correlation_id.rsplit("-", 1)[1]
                )
            )
            self.store.publish(key, dumps(message))
        elif isinstance(event, AgentRunSettled):
            body = json.dumps(
                {
                    "usage": asdict(event.result.usage),
                    "session_usage": asdict(self.usage.agent_usage()),
                    "outcome": event.result.outcome.value,
                    "messages": len(event.result.messages),
                },
                sort_keys=True,
            )
            old = self.store.db.execute(
                "SELECT body FROM loop_meta WHERE key='settled'"
            ).fetchone()
            if old and old[0] != body:
                self.store.blocked("settled summary changed during replay")
            with self.store.transaction("loop.settled") as tx:
                tx.execute(
                    "INSERT OR REPLACE INTO loop_meta VALUES('settled',?)", (body,)
                )
                tx.execute("UPDATE admission SET phase='done'")
            self.store.faults.hit("settled.committed")
            self.store.publish("settled", body)


def noop(*args):
    pass


def run(store):
    if store.poisoned:
        raise RuntimeError("store failed; reopen before further work")
    if store.summary()["phase"] != "active":
        # Terminal evidence does not authorize execution. Drain already committed
        # facts without requiring current code/workspace compatibility to run.
        store.publish_pending()
        return store.summary()
    try:
        config = store.valid_config()
        store.validate_receipts()
        store.publish_pending()
        if store.summary()["phase"] != "active":
            return store.summary()
        ports = LoopAdapters(store, config)
        status = AgentLoopStatusPolicyAdapter(
            **{
                name: noop
                for name in (
                    "run_entered",
                    "input_accepted",
                    "preparation_failed",
                    "provider_result_observed",
                    "provider_cancellation_observed",
                    "tool_policy_state_changed",
                    "provider_succeeded",
                    "provider_failed",
                    "no_tool_assistant",
                    "malformed_fatal",
                )
            }
        )
        loop = AgentLoop(
            request_source=AgentLoopRequestSourceAdapter(ports.prepare),
            provider_turn=AgentLoopProviderTurnAdapter(ports.complete),
            tool_capabilities=ReceiptTools(store, config),
            tool_policy=NativeAgentToolPolicy(
                lambda *_: AgentToolPolicyDecision(),
                lambda call, result, *_: result.content,
            ),
            event_sink=ports.events,
            usage_publisher=NativeAgentUsagePublisher(
                lambda sample, counts: ports.usage.absorb(
                    sample, counts_for_context=counts
                ),
                ports.events,
            ),
            queued_input_port=NativeAgentQueuedInputPort(lambda: None),
            status_policy=status,
            tool_execution="sequential",
        )
        outcome = loop.run(
            AgentLoopRunInput(
                (),
                AgentActiveInput(AgentUserMessage(ProductContent(config["prompt"]))),
                AgentToolPolicyState(tool_budget=config["budget"]),
                pricing=AgentTokenPricing(*config["pricing"]),
            )
        )
        if outcome.final_tool_state.tool_invocation_count != 2:
            raise RuntimeError("canonical loop tool accounting changed")
    except ReplayBlocked:
        pass
    return store.summary()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "resume", "inspect"))
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--scenario", choices=("safe", "unsafe"), default="safe")
    parser.add_argument("--terminate", action="store_true")
    parser.add_argument("--request-id", default="canonical-example-1")
    parser.add_argument("--crash-at")
    parser.add_argument("--pause-at")
    parser.add_argument("--marker", type=Path)
    parser.add_argument("--fail-write-at")
    args = parser.parse_args()
    if args.action == "start":
        if args.workspace is None or not args.workspace.is_dir():
            parser.error("start requires an existing disposable workspace")
    elif not args.db.is_file():
        parser.error("resume/inspect requires an existing database")
    store = LoopStore(
        args.db, Faults(args.crash_at, args.pause_at, args.marker, args.fail_write_at)
    )
    try:
        if args.action == "start":
            store.admit(
                args.request_id,
                configuration(args.workspace, args.scenario, args.terminate),
            )
        summary = store.summary() if args.action == "inspect" else run(store)
        print(json.dumps(summary, sort_keys=True))
        return BLOCKED_EXIT if summary["phase"] == "blocked" else 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
