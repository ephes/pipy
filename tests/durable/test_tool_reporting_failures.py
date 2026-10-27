"""Fixed failures come from actual invocation/reporting boundaries, never text."""

import asyncio
import contextlib
import copy
import json
import sqlite3
import threading
from dataclasses import replace

import pytest
from cli_process_support import run_cli
from generation_support import seed
from test_product_session import profile
from test_resolution import setup as resolution_setup
from test_tool_boundaries import MultiRound, isolated, run_idle
from tool_support import (
    CountedWire,
    InfrastructureFailureFirst,
    Keyed,
    counts,
    database,
    definitions,
)

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.agent.tools import ToolExecutor
from pipy_harness.native.durable.canonical_codec import decode_canonical
from pipy_harness.native.durable.generation import ROUND_DOC, USAGE_DOC
from pipy_harness.native.durable.memory import MemoryStorage
from pipy_harness.native.durable.resolution import AUDIT
from pipy_harness.native.durable.resolution_values import ResolutionError
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import _decode, open_sqlite_storage
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    _capture,
    builtin_registrations,
    define_tool_task,
)
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
)
from pipy_harness.sdk import (
    UnsupportedDurableCapability,
    create_durable_product_session,
    open_durable_product_session,
)

CASES = {
    "invoke": "invocation_exception",
    "runtime": "invocation_exception",
    "returned_invalid": "invalid_result",
    "tuple": "invalid_progress",
    "nonexact": "invalid_progress",
    "bytes": "progress_capacity",
    "nodes": "progress_capacity",
    "diagnostic": "invalid_progress",
    "returned_error": None,
}


def _invoke(case, request, context):
    context.report_details({"acknowledged": [1]})
    if case == "invoke":
        raise ValueError(
            "PRIVATE invalid_result progress_capacity text is not classification"
        )
    if case == "runtime":
        raise RuntimeError("PRIVATE ordinary RuntimeError")
    if case == "returned_invalid":
        return None
    invalid = {
        "tuple": (1,),
        "nonexact": 9007199254740993,
        "bytes": "x" * (512 * 1024),
        "nodes": [0] * 32768,
    }
    if case in invalid:
        context.report_details(invalid[case])
        pytest.fail("invalid explicit report must raise")
    if case == "diagnostic":
        context.report_diagnostic(severity="warning", message="\ud800")
    return ToolExecutionResult(
        request.tool_request_id, "ordinary final", is_error=case == "returned_error"
    )


@pytest.mark.parametrize("case,category", CASES.items())
def test_actual_error_category_failed_child_next_provider_and_no_reissue(
    case, category, tmp_path, monkeypatch
):
    isolated(monkeypatch, tmp_path)
    calls = []

    class Port:
        definition = ToolDefinition(
            "reported",
            "reported",
            {"type": "object", "properties": {"x": {"type": "integer"}}},
        )

        def invoke(self, request, context):
            calls.append(context.call_key)
            return _invoke(case, request, context)

    port, wire = Port(), MultiRound((("reported",), ()))
    path = tmp_path / "failure.sqlite"
    template = snapshot_provider_request(
        replace(
            seed(tmp_path).request,
            user_prompt="",
            messages=(),
            available_tools=(port.definition,),
        )
    )

    async def run():
        product = await create_durable_product_session(
            path,
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools={"reported": ToolRegistration(port, "fixture.reported", "1")},
            request_template=template,
        )
        try:
            await product.submit("input")
            result = await product.resume()
            assert (
                result.status == "terminal" and result.inspection.outcome == "completed"
            )
            message = next(
                row
                for row in result.completed.messages
                if isinstance(row.decode(), AgentToolResultMessage)
            )
            decoded = message.decode()
            assert decoded.is_error
            assert decoded.details == {"acknowledged": [1]}
            fact = message.tool_output.thaw()
            assert fact["version"] == (1 if category is None else 2)
            if category is not None:
                assert fact["recovery"] == {
                    "reason": "tool_error",
                    "category": category,
                }
                assert "Tool failed (" + category + ")." in decoded.content.value
            assert "PRIVATE" not in decoded.content.value
            ledger = product._session.snapshot(
                USAGE_DOC, conversation_id=message.conversation_id
            ).value.thaw()
            assert len(ledger["settlements"]) == 2
            assert ledger["models"]["openai/gpt-test"]["input"] == 32
            assert ledger["tools"] == {}
        finally:
            await product.close()
        with sqlite3.connect(path) as db:
            tasks = [_decode(row[0]) for row in db.execute("SELECT record FROM tasks")]
            entries = [
                _decode(row[0]) for row in db.execute("SELECT record FROM entries")
            ]
        child = next(row for row in tasks if row["kind"] == "pi.tool")
        assert child["state"]["outcome"]["status"] == (
            "completed" if category is None else "failed"
        )
        assert sum(row["kind"] == "pi.tool-result" for row in entries) == 1
        reopened = await open_durable_product_session(
            path, workspace=tmp_path, tools={}
        )
        try:
            assert (await reopened.resume()).status == "terminal"
        finally:
            await reopened.close()
        assert len(calls) == 1 and len(wire.calls) == 2
        sent = next(
            row for row in wire.calls[1][1]["body"]["messages"] if row["role"] == "tool"
        )
        assert "PRIVATE" not in str(sent)

    asyncio.run(asyncio.wait_for(run(), 15))


@pytest.mark.parametrize("failure", ["semantic_argument", "ordinary_runtime"])
def test_builtin_find_semantic_argument_error_fact1_and_ordinary_failure(
    failure, tmp_path, monkeypatch
):
    """Invoke-only semantic refusal must match the shared normal executor contract."""
    isolated(monkeypatch, tmp_path)
    registration = builtin_registrations()["find"]
    port = registration.port
    invoked = []
    original_invoke = type(port).invoke

    def counted(self, request, context):
        invoked.append((request.arguments, context.call_key))
        if failure == "ordinary_runtime":
            raise RuntimeError("PRIVATE ordinary runtime exception")
        return original_invoke(self, request, context)

    monkeypatch.setattr(type(port), "invoke", counted)

    class EmptyPatternWire(MultiRound):
        def post_json(self, url, **kwargs):
            response = super().post_json(url, **kwargs)
            body = copy.deepcopy(response.body)
            message = body["choices"][0]["message"]
            for call in message.get("tool_calls", []):
                call["function"]["arguments"] = '{"pattern":""}'
            return replace(response, body=body)

    wire = EmptyPatternWire((("find",), ()))
    path = tmp_path / "builtin-find.sqlite"

    async def run():
        product = await create_durable_product_session(
            path,
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools={"find": registration},
        )
        try:
            await product.submit("input")
            result = await product.resume()
            assert result.status == "terminal"
            assert result.inspection.outcome == "completed"
            completed = next(
                row
                for row in result.completed.messages
                if isinstance(row.decode(), AgentToolResultMessage)
            )
            message = completed.decode()
            fact = completed.tool_output.thaw()
            raw = decode_canonical(fact["outcome"])
        finally:
            await product.close()
        with contextlib.closing(sqlite3.connect(path)) as db:
            tasks = [_decode(row[0]) for row in db.execute("SELECT record FROM tasks")]
            entries = [
                _decode(row[0]) for row in db.execute("SELECT record FROM entries")
            ]
        child = next(row for row in tasks if row["kind"] == "pi.tool")
        first = next(
            decode_canonical(row["model"][0])
            for row in entries
            if row["kind"] == "pi.assistant"
        )
        assert json.loads(first.tool_calls[0].arguments_json.value) == {"pattern": ""}
        assert len(invoked) == 1 and invoked[0][0] == {"pattern": ""}
        assert sum(row["kind"] == "pi.tool-result" for row in entries) == 1
        cli = run_cli(
            tmp_path,
            "builtin-find-terminal",
            [
                "resume",
                "--store",
                str(path),
                "--workspace",
                str(tmp_path),
                "--format",
                "json",
            ],
        )
        assert cli.returncode == 0, cli.stderr
        cli_result = json.loads(cli.stdout)
        cli_message = next(
            row["message"]
            for row in cli_result["completed"]["messages"]
            if row["message"]["role"] == "tool_result"
        )
        reopened = await open_durable_product_session(
            path, workspace=tmp_path, tools={}
        )
        try:
            repeated = await reopened.resume()
            assert repeated.status == "terminal"
            repeated_message = next(
                row.decode()
                for row in repeated.completed.messages
                if isinstance(row.decode(), AgentToolResultMessage)
            )
        finally:
            await reopened.close()
        sent = next(
            row for row in wire.calls[1][1]["body"]["messages"] if row["role"] == "tool"
        )
        (tmp_path / "builtin-find-witness.json").write_text(
            json.dumps(
                {
                    "failure": failure,
                    "invocations": len(invoked),
                    "provider_calls": len(wire.calls),
                    "fact_version": fact["version"],
                    "raw_malformed_arguments": raw.malformed_arguments,
                    "child_outcome": child["state"]["outcome"]["status"],
                    "message": message.content.value,
                    "next_provider_tool": sent,
                    "cli_message": cli_message,
                },
                indent=2,
            )
            + "\n"
        )
        assert len(invoked) == 1 and len(wire.calls) == 2
        assert message.is_error and repeated_message == message
        assert cli_message["content"] == message.content.value
        if failure == "semantic_argument":
            assert (
                message.content.value
                == "find.pattern: pattern must be a non-empty string"
            )
            assert fact["version"] == 1 and raw.malformed_arguments is True
            assert child["state"]["outcome"]["status"] == "completed"
            assert sent["content"] == message.content.value
        else:
            assert fact["version"] == 2 and raw.malformed_arguments is False
            assert fact["recovery"] == {
                "reason": "tool_error",
                "category": "invocation_exception",
            }
            assert child["state"]["outcome"]["status"] == "failed"
            assert message.content.value == (
                "<harness>\nTool failed (invocation_exception).\n</harness>"
            )
            assert sent["content"] == message.content.value
            assert "PRIVATE" not in str(cli_message) + str(sent)

    asyncio.run(asyncio.wait_for(run(), 20))


@pytest.mark.parametrize(
    "pinned", [False, True], ids=["shared_builtin", "durable_pinned_builtin"]
)
def test_real_builtin_semantic_argument_error_shared_executor_contract(
    pinned, tmp_path, monkeypatch
):
    """A causal invoke discriminator, independent of the public template gate."""
    registration = builtin_registrations()["find"]
    invoked = []
    original = type(registration.port).invoke

    def counted(self, request, context):
        invoked.append(request.arguments)
        return original(self, request, context)

    monkeypatch.setattr(type(registration.port), "invoke", counted)
    port = _capture("find", registration).port if pinned else registration.port
    call = AgentToolCall("call:semantic", "find", ProductContent('{"pattern":""}'))
    executor = ToolExecutor({"find": port})
    assert executor.validated_arguments(call) == {"pattern": ""}
    try:
        outcome = executor.execute(call, ToolContext(tmp_path))
    finally:
        (tmp_path / "builtin-direct-witness.json").write_text(
            json.dumps({"pinned": pinned, "invoked": invoked}) + "\n"
        )
    assert invoked == [{"pattern": ""}]
    assert outcome.malformed_arguments is True and outcome.result.is_error
    assert (
        outcome.result.content.value
        == "find.pattern: pattern must be a non-empty string"
    )


def _schema_with_required_enum_and_nested_arrays():
    return {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "prepared": {"type": "boolean"},
            "selection": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"tag": {"type": "string", "enum": ["a", "b"]}},
                    "required": ["tag"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["x"],
        "additionalProperties": False,
    }


def _schema_ports(root, *, arrays=True):
    return [
        Keyed(
            root,
            name,
            schema=_schema_with_required_enum_and_nested_arrays() if arrays else None,
        )
        for name in ("keyed", "later")
    ]


def _schema_template(root, ports):
    return snapshot_provider_request(
        replace(
            seed(root).request,
            user_prompt="",
            messages=(),
            available_tools=tuple(port.definition for port in ports),
        )
    )


@pytest.mark.parametrize("template_mode", ["default", "provided", "plain_control"])
def test_schema_array_public_default_provided_submit_cli_and_reopen(
    template_mode, tmp_path, monkeypatch
):
    isolated(monkeypatch, tmp_path)
    database(tmp_path)
    ports = _schema_ports(tmp_path, arrays=template_mode != "plain_control")
    wire = CountedWire(tmp_path)
    registry = {
        p.definition.name: ToolRegistration(p, "fixture." + p.definition.name, "1")
        for p in ports
    }
    path = tmp_path / "schema-public.sqlite"
    options = (
        {"request_template": _schema_template(tmp_path, ports)}
        if template_mode == "provided"
        else {}
    )

    async def run():
        product = await create_durable_product_session(
            path,
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools=registry,
            **options,
        )
        try:
            assert counts(tmp_path) == {
                "calls": 0,
                "applications": 0,
                "prepares": 0,
                "providers": 0,
            }
            await product.submit("literal single input")
            assert counts(tmp_path)["providers"] == 0  # admission does not dispatch
            result = await product.resume()
            assert (
                result.status == "terminal" and result.inspection.outcome == "completed"
            )
            assert (
                sum(
                    isinstance(row.decode(), AgentToolResultMessage)
                    for row in result.completed.messages
                )
                == 2
            )
        finally:
            await product.close()
        before = counts(tmp_path)
        assert before == {"calls": 2, "applications": 2, "prepares": 2, "providers": 2}
        cli = run_cli(
            tmp_path,
            "schema-terminal",
            [
                "resume",
                "--store",
                str(path),
                "--workspace",
                str(tmp_path),
                "--format",
                "json",
            ],
        )
        assert cli.returncode == 0, cli.stderr
        assert json.loads(cli.stdout)["status"] == "terminal"
        reopened = await open_durable_product_session(
            path, workspace=tmp_path, tools={}
        )
        try:
            assert (await reopened.resume()).status == "terminal"
        finally:
            await reopened.close()
        assert counts(tmp_path) == before and len(wire.calls) == 2
        with contextlib.closing(sqlite3.connect(tmp_path / "effects.sqlite")) as db:
            keys = [row[0] for row in db.execute("SELECT key FROM calls")]
        assert len(keys) == len(set(keys)) == 2

    asyncio.run(asyncio.wait_for(run(), 20))


def _change_schema_declaration(definition, mutation):
    schema = copy.deepcopy(definition.input_schema)
    if mutation == "required":
        schema["required"] = ["x", "selection"]
    elif mutation in ("enum", "enum_order"):
        schema["properties"]["selection"]["items"]["properties"]["tag"]["enum"] = (
            ["a", "c"] if mutation == "enum" else ["b", "a"]
        )
    elif mutation == "schema_mode":
        return replace(definition, schema_mode="foreign")
    elif mutation == "name":
        return replace(definition, name="other")
    elif mutation == "description":
        return replace(definition, description="changed model declaration")
    else:
        raise AssertionError(mutation)
    return replace(definition, input_schema=schema)


@pytest.mark.parametrize(
    "mutation",
    [
        "required",
        "enum",
        "enum_order",
        "schema_mode",
        "name",
        "catalog_order",
        "description",
        "metadata",
        "live_hook",
    ],
)
def test_schema_public_real_declaration_drift_and_live_hook_refuse_before_store(
    mutation, tmp_path, monkeypatch
):
    isolated(monkeypatch, tmp_path)
    database(tmp_path)
    ports = _schema_ports(tmp_path, arrays=mutation not in ("metadata", "live_hook"))
    wire = CountedWire(tmp_path)
    registry = {
        p.definition.name: ToolRegistration(p, "fixture." + p.definition.name, "1")
        for p in ports
    }
    hook_calls = []
    if mutation in ("metadata", "live_hook"):
        template = _schema_template(tmp_path, ports)
        first = replace(
            template.request.available_tools[0],
            **(
                {"prompt_snippet": "not an authorized model declaration"}
                if mutation == "metadata"
                else {"prepare_loadout": lambda *args: hook_calls.append(args)}
            ),
        )
        tools = (first, template.request.available_tools[1])
        template = replace(
            template, request=replace(template.request, available_tools=tools)
        )
    else:
        declarations = [p.definition for p in ports]
        if mutation == "catalog_order":
            declarations.reverse()
        else:
            declarations[0] = _change_schema_declaration(declarations[0], mutation)
        template = snapshot_provider_request(
            replace(
                seed(tmp_path).request,
                user_prompt="",
                messages=(),
                available_tools=tuple(declarations),
            )
        )
    path = tmp_path / "must-not-create.sqlite"

    async def run():
        with pytest.raises(UnsupportedDurableCapability):
            await create_durable_product_session(
                path,
                workspace=tmp_path,
                profile=profile(tmp_path, wire),
                tools=registry,
                request_template=template,
            )

    asyncio.run(asyncio.wait_for(run(), 10))
    assert not path.exists()
    assert not list(tmp_path.glob("must-not-create.sqlite*"))
    assert counts(tmp_path) == {
        "calls": 0,
        "applications": 0,
        "prepares": 0,
        "providers": 0,
    }
    assert wire.calls == [] and hook_calls == []


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_schema_array_real_scheduler_call_admission(backend, tmp_path, monkeypatch):
    from generation_support import admit

    isolated(monkeypatch, tmp_path)
    generation, tool, snapshot = definitions(
        tmp_path, schema=_schema_with_required_enum_and_nested_arrays()
    )
    session = Session(
        MemoryStorage()
        if backend == "memory"
        else open_sqlite_storage(str(tmp_path / "schema-call.sqlite"), mode="create")
    )
    try:
        _, _, parent = admit(session, generation, snapshot)
        scheduler = run_idle(session, [generation, tool])
        actual = counts(tmp_path)
        (tmp_path / "call-schema-witness.json").write_text(
            json.dumps(
                {"counts": actual, "tasks": [r.to_tree() for r in scheduler.inspect()]}
            )
            + "\n"
        )
        assert actual == {"calls": 2, "applications": 2, "prepares": 2, "providers": 2}
        assert (
            next(r.to_tree() for r in scheduler.inspect() if r.id == parent)["state"][
                "outcome"
            ]["status"]
            == "completed"
        )
    finally:
        session.close(timeout=10)


class _SchemaInfrastructureFailureFirst(InfrastructureFailureFirst):
    def __init__(self, root, name):
        super().__init__(
            root, name, schema=_schema_with_required_enum_and_nested_arrays()
        )


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_schema_array_genuine_operator_retry_keeps_nonce_and_new_key(
    backend, tmp_path, monkeypatch
):
    isolated(monkeypatch, tmp_path)
    session, scheduler, cid, parent, old, idle = resolution_setup(
        tmp_path, backend, port_type=_SchemaInfrastructureFailureFirst
    )
    try:
        assert counts(tmp_path) == {
            "calls": 1,
            "applications": 1,
            "prepares": 1,
            "providers": 1,
        }
        initial_round = session.snapshot(ROUND_DOC, task_id=parent).value.thaw()
        old_key = old["state"]["checkpoint"]["intent"]["key"]
        target = scheduler.resolution_target(old["id"])
        nonce = "7" * 32
        done = threading.Event()

        def observe(publication):
            for change in publication.changes:
                if hasattr(change, "to_tree"):
                    row = change.to_tree()
                    if (
                        row["type"] == "task"
                        and row["value"]["id"] == parent
                        and row["value"]["state"]["status"] == "terminal"
                    ):
                        done.set()

        session.subscribe_commits(observe)
        result = scheduler.resolve(target, nonce=nonce)
        assert result.source_task_id == old["id"] and result.new_task_id != old["id"]
        assert scheduler.resolve(target, nonce=nonce) == result
        assert done.wait(10), session.failure
        rows = [r.to_tree() for r in scheduler.inspect()]
        assert next(r for r in rows if r["id"] == old["id"])["state"]["outcome"] == {
            "status": "aborted",
            "reason": "operator_retry",
        }
        assert next(r for r in rows if r["id"] == result.new_task_id)["owner"] == parent
        assert (
            next(r for r in rows if r["id"] == parent)["state"]["outcome"]["status"]
            == "completed"
        )
        assert counts(tmp_path) == {
            "calls": 3,
            "applications": 3,
            "prepares": 3,
            "providers": 2,
        }
        with contextlib.closing(sqlite3.connect(tmp_path / "effects.sqlite")) as db:
            keys = [r[0] for r in db.execute("SELECT key FROM calls")]
        assert len(keys) == len(set(keys)) == 3 and keys[0] == old_key
        audit = session.snapshot(AUDIT, conversation_id=cid, key=nonce).value.thaw()
        assert audit["source"] == old and audit["new_task_id"] == result.new_task_id
        assert initial_round["tools"][0] == old["id"]
        assert (
            session.snapshot(USAGE_DOC, conversation_id=cid).value.thaw()["models"][
                "openai/gpt-test"
            ]["input"]
            == 32
        )
    finally:
        session.close(timeout=10)


@pytest.mark.parametrize(
    "mutation", ["required", "enum", "schema_mode", "name", "description", "live_hook"]
)
def test_schema_retry_changed_current_declaration_has_no_new_effect_or_nonce(
    mutation, tmp_path, monkeypatch
):
    from generation_support import definition

    isolated(monkeypatch, tmp_path)
    session, _, cid, parent, old, _ = resolution_setup(
        tmp_path, "sqlite", port_type=_SchemaInfrastructureFailureFirst
    )
    try:
        assert counts(tmp_path) == {
            "calls": 1,
            "applications": 1,
            "prepares": 1,
            "providers": 1,
        }
    finally:
        session.close(timeout=10)
    ports = _schema_ports(tmp_path)
    hook_calls = []
    ports[0].definition = (
        replace(
            ports[0].definition, prepare_loadout=lambda *args: hook_calls.append(args)
        )
        if mutation == "live_hook"
        else _change_schema_declaration(ports[0].definition, mutation)
    )
    registry = {
        p.definition.name: ToolRegistration(p, "fixture." + p.definition.name, "1")
        for p in ports
    }
    generation = definition(tmp_path, CountedWire(tmp_path))
    tool = define_tool_task(registry, workspace_root=tmp_path)
    reopened = Session(
        open_sqlite_storage(str(tmp_path / "session.sqlite"), mode="open")
    )
    scheduler = TaskScheduler(reopened, [generation, tool])
    try:
        scheduler.open()  # passive; explicit operator authorization must refuse
        before_tasks = [r.to_tree() for r in scheduler.inspect()]
        before_round = reopened.snapshot(ROUND_DOC, task_id=parent).value
        before_counts = counts(tmp_path)
        target = scheduler.resolution_target(old["id"])
        nonce = "8" * 32
        with pytest.raises(ResolutionError) as refusal:
            scheduler.resolve(target, nonce=nonce)
        assert refusal.value.code == "incompatible"
        assert [r.to_tree() for r in scheduler.inspect()] == before_tasks
        assert reopened.snapshot(ROUND_DOC, task_id=parent).value == before_round
        assert reopened.snapshot(AUDIT, conversation_id=cid, key=nonce) is None
        assert counts(tmp_path) == before_counts
        assert hook_calls == []
    finally:
        reopened.close(timeout=10)


def test_schema_complete_builtin_default_registry_find_sdk_cli_authfree_reopen(
    tmp_path, monkeypatch
):
    isolated(monkeypatch, tmp_path)
    registration = builtin_registrations()["find"]
    original = type(registration.port).invoke
    invocations = []

    def counted(self, request, context):
        invocations.append((request.arguments, context.call_key))
        return original(self, request, context)

    monkeypatch.setattr(type(registration.port), "invoke", counted)

    class FindWire(MultiRound):
        def post_json(self, url, **kwargs):
            response = super().post_json(url, **kwargs)
            body = copy.deepcopy(response.body)
            for call in body["choices"][0]["message"].get("tool_calls", []):
                call["function"]["arguments"] = '{"pattern":""}'
            return replace(response, body=body)

    wire = FindWire((("find",), ()))
    path = tmp_path / "complete-builtin.sqlite"

    async def run():
        product = await create_durable_product_session(
            path, workspace=tmp_path, profile=profile(tmp_path, wire), tools=None
        )
        try:
            assert invocations == [] and wire.calls == []
            await product.submit("literal single input")
            result = await product.resume()
            assert (
                result.status == "terminal" and result.inspection.outcome == "completed"
            )
            row = next(
                row
                for row in result.completed.messages
                if isinstance(row.decode(), AgentToolResultMessage)
            )
            assert (
                row.decode().content.value
                == "find.pattern: pattern must be a non-empty string"
            )
            fact = row.tool_output.thaw()
            assert fact["version"] == 1
            assert decode_canonical(fact["outcome"]).malformed_arguments is True
        finally:
            await product.close()
        assert len(invocations) == 1 and invocations[0][0] == {"pattern": ""}
        assert len(wire.calls) == 2
        sent = next(
            row for row in wire.calls[1][1]["body"]["messages"] if row["role"] == "tool"
        )
        assert sent["content"] == "find.pattern: pattern must be a non-empty string"
        with contextlib.closing(sqlite3.connect(path)) as db:
            tasks = [_decode(r[0]) for r in db.execute("SELECT record FROM tasks")]
            entries = [_decode(r[0]) for r in db.execute("SELECT record FROM entries")]
        tools = [row for row in tasks if row["kind"] == "pi.tool"]
        assert len(tools) == 1 and tools[0]["state"]["outcome"]["status"] == "completed"
        assert sum(row["kind"] == "pi.tool-result" for row in entries) == 1
        request = decode_canonical(
            next(
                row["data"]["request"]
                for row in entries
                if row["kind"] == "pi.assistant"
            )
        )
        assert request.advertised_tool_names == tuple(builtin_registrations())
        cli = run_cli(
            tmp_path,
            "complete-builtins-terminal",
            [
                "resume",
                "--store",
                str(path),
                "--workspace",
                str(tmp_path),
                "--format",
                "json",
            ],
        )
        assert cli.returncode == 0, cli.stderr
        public = json.loads(cli.stdout)
        assert public["status"] == "terminal"
        assert (
            next(
                row["message"]
                for row in public["completed"]["messages"]
                if row["message"]["role"] == "tool_result"
            )["content"]
            == sent["content"]
        )
        reopened = await open_durable_product_session(
            path, workspace=tmp_path, tools={}
        )
        try:
            assert (await reopened.resume()).status == "terminal"
        finally:
            await reopened.close()
        assert len(invocations) == 1 and len(wire.calls) == 2

    asyncio.run(asyncio.wait_for(run(), 20))
