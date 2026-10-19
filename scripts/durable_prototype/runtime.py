"""Isolated, text-only durable-run experiment. Never used by pipy entry points."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
)
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.models import ProviderRequest, ProviderToolCall
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolRequest,
    make_tool_request_id,
    validate_arguments,
)
from pipy_harness.native.tools.bash import BashTool
from pipy_harness.native.tools.read import ReadTool
from pipy_harness.native.tools.write import WriteTool

FORMAT = 1
BLOCKED_EXIT = 3
TOOLS = {"write": WriteTool(), "read": ReadTool(), "bash": BashTool(timeout_seconds=5)}
SYSTEM = "Execute the fixed offline durable-run experiment, then report its results."
SCHEMA = """
CREATE TABLE IF NOT EXISTS admission (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    request_id TEXT NOT NULL UNIQUE, config TEXT NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN ('active','blocked','done'))
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, phase TEXT NOT NULL,
    position INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    sequence INTEGER PRIMARY KEY, task_id TEXT NOT NULL UNIQUE, body TEXT NOT NULL
);
PRAGMA user_version=1;
"""


class Faults:
    """Deterministic process death or read-only SQLite failure at named seams."""

    def __init__(self, crash_at=None, pause_at=None, marker=None, fail_write_at=None):
        self.crash_at = crash_at
        self.pause_at = pause_at
        self.marker = marker
        self.fail_write_at = fail_write_at

    def hit(self, point):
        if point == self.crash_at:
            os._exit(86)  # Deliberately skip cleanup, including sqlite/lock close.
        if point == self.pause_at:
            if self.marker is None:
                raise ValueError("pause requires --marker")
            self.marker.write_text(point, encoding="utf-8")
            signal.pause()  # The experiment driver sends real SIGKILL here.
            raise RuntimeError("pause interrupted without process death")


class Store:
    """One process owns the DB for its entire lifetime, including external work."""

    def __init__(self, path, faults):
        self.path = path.resolve()
        self.faults = faults
        self.poisoned = False
        # Persistent sidecar inode: never unlink it, even after close.
        self.lock = open(str(self.path) + ".lock", "a+b")
        os.chmod(self.lock.name, 0o600)
        try:
            fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            os.chmod(self.path, 0o600)
            self.db = sqlite3.connect(self.path, isolation_level=None, timeout=0)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA journal_mode=DELETE")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, FORMAT):
                raise ValueError("unsupported experimental database format")
            self.db.executescript(SCHEMA)
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            self.lock.close()
            raise

    def close(self):
        self.db.close()
        self.lock.close()

    @contextmanager
    def transaction(self, label):
        if self.poisoned:
            raise RuntimeError("store failed; reopen before any further work")
        try:
            if label == self.faults.fail_write_at:
                self.db.execute("PRAGMA query_only=ON")
            self.db.execute("BEGIN IMMEDIATE")
            yield self.db
            self.db.execute("COMMIT")
            if self.faults.fail_write_at == label + ".after_commit":
                raise sqlite3.OperationalError("injected lost commit acknowledgement")
        except BaseException:
            self.poisoned = True
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def entry(self, tx, task_id, body):
        tx.execute(
            "INSERT INTO entries(task_id,body) VALUES (?,?)",
            (task_id, json.dumps(body)),
        )

    def task(self, tx, task_id, kind, position, payload):
        tx.execute(
            "INSERT INTO tasks(id,kind,phase,position,payload) VALUES (?,?,'pending',?,?)",
            (task_id, kind, position, json.dumps(payload)),
        )

    def admit(self, request_id, workspace, scenario, prompt):
        config = {
            "format": FORMAT,
            "workspace": str(workspace.resolve()),
            "scenario": scenario,
            "prompt": prompt,
        }
        existing = self.db.execute("SELECT * FROM admission").fetchone()
        if existing is not None:
            if (
                existing["request_id"] != request_id
                or json.loads(existing["config"]) != config
            ):
                raise ValueError(
                    "prototype DB already belongs to a different admission/config"
                )
            return False
        with self.transaction("admission") as tx:
            tx.execute(
                "INSERT INTO admission VALUES (1,?,?,'active')",
                (request_id, json.dumps(config)),
            )
            self.entry(tx, "input", {"role": "user", "text": prompt})
            self.task(tx, "g0", "generation", 0, {})
        self.faults.hit("admitted")
        return True

    def config(self):
        row = self.db.execute("SELECT config FROM admission").fetchone()
        if row is None:
            raise ValueError("no admission; use start first")
        config = json.loads(row[0])
        if config["format"] != FORMAT:
            raise ValueError("unsupported experimental run format")
        return config

    def summary(self):
        admission = self.db.execute("SELECT phase,request_id FROM admission").fetchone()
        tasks = [
            dict(row)
            for row in self.db.execute(
                "SELECT id,kind,phase,position,attempts FROM tasks ORDER BY position,kind"
            )
        ]
        return {
            "phase": admission["phase"] if admission else "empty",
            "tasks": tasks,
            "entries": self.db.execute("SELECT count(*) FROM entries").fetchone()[0],
            "tool_results": self.db.execute(
                "SELECT count(*) FROM entries WHERE task_id LIKE 't%'"
            ).fetchone()[0],
        }


def steps(scenario):
    # Only this exact overwrite, in an exclusively owned disposable workspace,
    # is declared safe. This is NOT a general replay policy for native write.
    if scenario == "safe":
        return [
            ("write", {"path": "effect.txt", "content": "effect\n"}, True),
            ("read", {"path": "effect.txt"}, True),
        ]
    return [
        ("bash", {"command": "printf 'effect\\n' >> effect.txt"}, False),
        ("read", {"path": "effect.txt"}, True),
    ]


def messages(store):
    """Deliberately narrow text codec; real canonical message objects at the port."""
    result = []
    for row in store.db.execute("SELECT body FROM entries ORDER BY sequence"):
        body = json.loads(row[0])
        text = ProductContent(body["text"])
        if body["role"] == "user":
            result.append(AgentUserMessage(text))
        elif body["role"] == "assistant":
            calls = tuple(
                AgentToolCall(c["provider_id"], c["name"], ProductContent(c["args"]))
                for c in body["calls"]
            )
            result.append(
                AgentAssistantMessage(
                    text, calls, provider="fake", model="durable-offline"
                )
            )
        else:
            result.append(
                AgentToolResultMessage(
                    body["request_id"],
                    body["name"],
                    text,
                    body["provider_id"],
                    body["is_error"],
                )
            )
    return tuple(result)


def generate(store, task, config):
    position = task["position"]
    point = f"generation.{position}"
    with store.transaction("generation.start") as tx:
        tx.execute(
            "UPDATE tasks SET phase='running', attempts=attempts+1 WHERE id=?",
            (task["id"],),
        )
    plan = steps(config["scenario"])
    calls = ()
    if position < len(plan):
        name, arguments, _ = plan[position]
        calls = (ProviderToolCall(f"offline-{position}", name, json.dumps(arguments)),)
    # Reconstruct the offline provider from the persisted turn, not its RAM call
    # counter. It exercises the same ProviderPort complete()/stream callback seam.
    provider = FakeNativeProvider(
        model_id="durable-offline",
        supports_tool_calls=True,
        programmable_tool_calls=(calls,),
        programmable_text_chunks=("scripted ", "response"),
    )
    request = ProviderRequest(
        SYSTEM,
        config["prompt"],
        provider.name,
        provider.model_id,
        Path(config["workspace"]),
        provider_turn_index=position,
        messages=messages(store),
        available_tools=tuple(tool.definition for tool in TOOLS.values()),
    )
    response = provider.complete(
        request, stream_sink=lambda _: store.faults.hit(point + ".stream")
    )
    store.faults.hit(point + ".response")
    body = {"role": "assistant", "text": response.final_text or "", "calls": []}
    # The script emits at most one call per turn; the prototype has no batches.
    for call in response.tool_calls:
        body["calls"].append(
            {
                "name": call.tool_name,
                "provider_id": call.provider_correlation_id,
                "args": call.arguments_json,
            }
        )
    with store.transaction("generation.result") as tx:
        store.entry(tx, task["id"], body)
        tx.execute("UPDATE tasks SET phase='done' WHERE id=?", (task["id"],))
        if response.tool_calls:
            call = response.tool_calls[0]
            payload = {
                "name": call.tool_name,
                "provider_id": call.provider_correlation_id,
                "arguments": json.loads(call.arguments_json),
                "request_id": make_tool_request_id(),
                "safe": plan[position][2],
            }
            store.task(tx, f"t{position}", "tool", position, payload)
        else:
            tx.execute("UPDATE admission SET phase='done'")
    store.faults.hit(point + ".committed")
    if response.tool_calls:
        # Same committed state as generation.N.committed, named for tool probes.
        store.faults.hit(f"tool.{position}.admitted")


def execute_tool(store, task, config):
    position = task["position"]
    point = f"tool.{position}"
    payload = json.loads(task["payload"])
    current_safe = steps(config["scenario"])[position][2]
    if task["phase"] == "running" and not (payload["safe"] and current_safe):
        with store.transaction("tool.uncertain") as tx:
            tx.execute("UPDATE tasks SET phase='uncertain' WHERE id=?", (task["id"],))
            tx.execute("UPDATE admission SET phase='blocked'")
        return
    tool = TOOLS[payload["name"]]
    arguments = validate_arguments(
        tool_name=payload["name"],
        schema=tool.definition.input_schema,
        arguments=payload["arguments"],
    )
    with store.transaction("tool.start") as tx:
        tx.execute(
            "UPDATE tasks SET phase='running', attempts=attempts+1 WHERE id=?",
            (task["id"],),
        )
    store.faults.hit(point + ".before_execute")
    result = tool.invoke(
        ToolRequest(
            payload["request_id"], payload["name"], arguments, payload["provider_id"]
        ),
        ToolContext(Path(config["workspace"])),
    )
    store.faults.hit(point + ".effect")
    # Restrict the codec explicitly; no silently discarded rich/native results.
    if result.blocks or result.terminate:
        raise ValueError("prototype only supports ordinary text tool results")
    body = {
        "role": "tool",
        "text": result.output_text,
        "name": payload["name"],
        "request_id": result.tool_request_id,
        "provider_id": payload["provider_id"],
        "is_error": result.is_error,
    }
    with store.transaction("tool.result") as tx:
        store.entry(tx, task["id"], body)
        tx.execute("UPDATE tasks SET phase='done' WHERE id=?", (task["id"],))
        store.task(tx, f"g{position + 1}", "generation", position + 1, {})
    store.faults.hit(point + ".committed")


def run(store):
    if store.poisoned:
        raise RuntimeError("store failed; reopen before any further work")
    config = store.config()
    while store.db.execute("SELECT phase FROM admission").fetchone()[0] == "active":
        task = store.db.execute(
            "SELECT * FROM tasks WHERE phase IN ('pending','running') ORDER BY position,kind LIMIT 1"
        ).fetchone()
        if task is None:
            raise RuntimeError("active admission has no runnable task")
        if task["kind"] == "generation":
            generate(store, task, config)
        else:
            execute_tool(store, task, config)
    return store.summary()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "resume", "inspect"))
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--scenario", choices=("safe", "unsafe"), default="safe")
    parser.add_argument("--request-id", default="example-1")
    parser.add_argument(
        "--prompt", default="Run the disposable offline tool experiment."
    )
    parser.add_argument("--crash-at")
    parser.add_argument("--pause-at")
    parser.add_argument("--marker", type=Path)
    parser.add_argument("--fail-write-at")
    args = parser.parse_args()
    if args.action == "start":
        if args.workspace is None or not args.workspace.is_dir():
            parser.error("start requires an existing disposable --workspace")
    elif not args.db.is_file():
        parser.error("resume/inspect requires an existing experimental database")
    faults = Faults(args.crash_at, args.pause_at, args.marker, args.fail_write_at)
    store = Store(args.db, faults)
    try:
        if args.action == "start":
            store.admit(args.request_id, args.workspace, args.scenario, args.prompt)
        result = store.summary() if args.action == "inspect" else run(store)
        # Sole publication: after every represented fact has committed.
        print(json.dumps(result, sort_keys=True))
        return BLOCKED_EXIT if result["phase"] == "blocked" else 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
