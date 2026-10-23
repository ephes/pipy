"""Isolated fixed-wire support shared by actual DE07 execution probes."""

import threading
from pathlib import Path

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import AgentUserMessage
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.durable.canonical_codec import encode_canonical
from pipy_harness.native.durable.generation import define_generation, message_entry
from pipy_harness.native.http import JsonResponse
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.tools.base import ToolDefinition

SPEC = NativeModelSpec("openai", "gpt-test", "Test", "openai-completions")


class Wire:
    def __init__(self, calls=(), *, failure=False):
        self.calls = []
        self.tool_calls = tuple(calls)
        self.failure = failure
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def post_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        self.entered.set()
        assert self.release.wait(10)
        if self.failure:
            raise RuntimeError("private transport header must never persist")
        message = {"content": "done"}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": f"call:{i}",
                    "type": "function",
                    "function": {"name": name, "arguments": ' { "x":  1 } '},
                }
                for i, name in enumerate(self.tool_calls)
            ]
        return JsonResponse(
            200,
            {
                "choices": [
                    {
                        "message": message,
                        "finish_reason": "tool_calls" if self.tool_calls else "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                    "prompt_tokens_details": {"cached_tokens": 4},
                    "completion_tokens_details": {"reasoning_tokens": 3},
                },
            },
        )


def definition(tmp_path, wire, **kwargs):
    return define_generation(
        SPEC,
        auth_path=tmp_path / "auth.json",
        env={},
        runtime_api_key="fixture-key",
        http_client=wire,
        **kwargs,
    )


def seed(tmp_path, tools=()):
    return snapshot_provider_request(
        ProviderRequest(
            "system",
            "input",
            "openai",
            "gpt-test",
            Path(tmp_path),
            available_tools=tuple(
                ToolDefinition(name, name, {"type": "object"}) for name in tools
            ),
        )
    )


def admit(session, task, snapshot):
    conversation = session.commit(
        lambda tx: tx.create_conversation({"kind": "ownerless"})
    ).id
    source = session.commit(
        lambda tx: tx.append_entry(
            conversation, message_entry(AgentUserMessage(ProductContent("input")))
        )
    )
    task_id = session.commit(
        lambda tx: tx.create_task(
            task,
            encode_canonical(snapshot),
            ownership={"kind": "conversation"},
            conversation_id=conversation,
        )
    )
    return conversation, source.id, task_id


def completion(session, task_id):
    done = threading.Event()
    publications = []

    def observe(publication):
        for change in publication.changes:
            if hasattr(change, "to_tree"):
                value = change.to_tree()
                if value["type"] == "task" and value["value"]["id"] == task_id:
                    if value["value"]["state"]["status"] in ("terminal", "waiting"):
                        publications.append(publication)
                        done.set()

    session.subscribe_commits(observe)
    return done, publications
