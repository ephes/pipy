"""CM1 T9: public product delivery with the installed WASI interpreter."""

from __future__ import annotations

import io
import json
import os
import threading
import time
from pathlib import Path

import pytest
from codemode_driver import REAL_RUNTIME, group_is_gone, real_runtime_skip_reason
from test_native_coding_session import _CollectingAgentEventSink
from test_native_extension_live_session_hooks import _write_ext
from test_native_session_history_render import _Terminal

import pipy_harness.cli as cli
from pipy_harness.adapters.native import CodingSessionAdapter
from pipy_harness.native.agent import AgentToolResultMessage, ToolCallCompleted
from pipy_harness.native.codemode import host, selftest
from pipy_harness.native.codemode.runtime import RUNTIME_PIN, CodemodePaths
from pipy_harness.native.coding import codemode_runner
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tools.codemode import CodemodeTool
from pipy_harness.sdk import create_product_session


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("production phase did not arrive")


SUMMARY_CODE = """rows = []
for path in ['alpha.txt', 'beta.txt', 'gamma.txt']:
    for line in tools.read(path=path).splitlines():
        if line.startswith('KEEP '):
            rows.append({'file': path, 'value': line[5:]})
text({'matches': rows, 'count': len(rows)})
"""
EXPECTED = {
    "matches": [
        {"file": "alpha.txt", "value": "apple"},
        {"file": "beta.txt", "value": "berry"},
        {"file": "gamma.txt", "value": "cherry"},
    ],
    "count": 3,
}


def fixtures(root: Path) -> None:
    for name, value in zip(
        ("alpha", "beta", "gamma"), ("apple", "berry", "cherry"), strict=True
    ):
        (root / f"{name}.txt").write_text(
            f"DROP private bulk\nKEEP {value}\nDROP noise\n"
        )


def provider(code: str = SUMMARY_CODE, later=()) -> FakeNativeProvider:
    return FakeNativeProvider(
        model_id="fake-tools",
        supports_tool_calls=True,
        final_text="T9_DONE",
        programmable_tool_calls=(
            (ProviderToolCall("parent", "codemode", json.dumps({"code": code})),),
            (),
            *later,
        ),
    )


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    reason = real_runtime_skip_reason()
    if reason:
        if os.environ.get("PIPY_CODEMODE_REQUIRE_RUNTIME") == "1":
            pytest.fail(reason)
        pytest.skip(reason)

    class InstalledPaths(CodemodePaths):
        def runtime_dir(self, pin=RUNTIME_PIN):
            return REAL_RUNTIME

    paths = InstalledPaths(tmp_path / "runtime-state")
    probe = selftest.availability
    monkeypatch.setattr(selftest, "availability", lambda: probe(paths))
    init = codemode_runner.CodemodeCompositeRunner.__init__
    monkeypatch.setattr(
        codemode_runner.CodemodeCompositeRunner,
        "__init__",
        lambda self, **kwargs: init(self, paths=paths, **kwargs),
    )
    for key, leaf in [
        ("PIPY_CONFIG_HOME", "config"),
        ("PIPY_NATIVE_THEME_PATH", "theme.json"),
        ("PIPY_NATIVE_SESSIONS_ROOT", "sessions"),
        ("PIPY_NATIVE_DEFAULTS_PATH", "defaults.json"),
        ("PIPY_AUTH_DIR", "auth"),
    ]:
        monkeypatch.setenv(key, str(tmp_path / leaf))
    monkeypatch.setenv("PIPY_OFFLINE", "1")
    pids = []
    worker = host._Worker

    class RecordingWorker(worker):
        def __init__(self, argv, status_write):
            super().__init__(argv, status_write)
            pids.append(self.process.pid)

    monkeypatch.setattr(host, "_Worker", RecordingWorker)
    yield pids
    assert all(group_is_gone(pid) for pid in pids)


def settings(root):
    return SettingsManager(
        global_path=root / "global.json",
        project_path=root / "project.json",
        overrides={"defaultTools": ["+codemode"], "theme": "pi"},
    )


def test_advertised_read_filter_example_runs_without_bulk_output(
    tmp_path, monkeypatch, runtime
):
    fixtures(tmp_path)
    description = CodemodeTool().definition.description
    _, fence, example = description.partition("```python\n")
    assert fence, "Advertised codemode guidance needs an executable example"
    code, closing, _ = example.partition("\n```")
    assert closing
    fake = provider(code)
    requests = []
    complete = FakeNativeProvider.complete

    def capture(self, request, **kwargs):
        requests.append(request)
        return complete(self, request, **kwargs)

    monkeypatch.setattr(FakeNativeProvider, "complete", capture)
    sink = _CollectingAgentEventSink()
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "saved")
    with create_product_session(
        workspace=tmp_path,
        provider=fake,
        settings=settings(tmp_path),
        tree=tree,
        observer=sink,
        load_context_files=False,
    ) as session:
        result = session.submit("filter the small fixture files")
        assert result.provider_failure is result.preparation_failure is None
    assert code in next(
        t.description for t in requests[0].available_tools if t.name == "codemode"
    )
    parents = [e.result for e in sink.events if isinstance(e, ToolCallCompleted)]
    assert len(parents) == 1
    parent = parents[0]
    assert not parent.is_error
    answer = json.loads(parent.content.value.partition("Output:\n")[2])
    assert answer == {"matches": EXPECTED["matches"][:2], "count": 2}
    assert "DROP private bulk" not in parent.content.value
    assert [
        (c["name"], c["arguments"]["path"])
        for c in parent.details["nestedCalls"]["calls"]
    ] == [("read", "alpha.txt"), ("read", "beta.txt")]
    reopened = NativeSessionTree.open(tree.path, strict=True)
    saved = [
        e.message
        for e in reopened.get_entries()
        if isinstance(getattr(e, "message", None), AgentToolResultMessage)
    ]
    assert len(saved) == 1
    assert saved[0].details == parent.details


@pytest.mark.parametrize("adapter", [False, True])
def test_public_multifile_summary_and_durable_replay(
    tmp_path, monkeypatch, runtime, adapter
):
    fixtures(tmp_path)
    fake = provider()
    requests = []
    complete = FakeNativeProvider.complete
    monkeypatch.setattr(
        FakeNativeProvider,
        "complete",
        lambda self, request, **kw: (
            requests.append(request),
            complete(self, request, **kw),
        )[1],
    )
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "saved")
    sink = _CollectingAgentEventSink()
    args = dict(
        provider=fake,
        settings_manager=settings(tmp_path),
        native_session=tree,
        agent_event_sink=sink,
    )
    if adapter:
        owner = CodingSessionAdapter(**args)
        session = owner.build_session(owner.prepare_session_context(tmp_path))
    else:
        session = CodingSession(**args)
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("summarize\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    assert (
        next(t for t in requests[0].available_tools if t.name == "codemode")
        == CodemodeTool().definition
    )
    results = [m for m in requests[1].messages if isinstance(m, AgentToolResultMessage)]
    assert len(results) == 1 and results[0].provider_correlation_id == "parent"
    parent = next(e.result for e in sink.events if isinstance(e, ToolCallCompleted))
    assert not parent.is_error
    assert (
        json.loads(parent.content.value.split("Output:\n")[1].split("\nTool calls")[0])
        == EXPECTED
    )
    calls = parent.details["nestedCalls"]["calls"]
    assert [(c["name"], c["arguments"]["path"], c["status"]) for c in calls] == [
        ("read", f"{name}.txt", "ok") for name in ("alpha", "beta", "gamma")
    ]
    assert parent.details["nestedCalls"]["complete"] is True
    assert all(c["durationMs"] >= 0 for c in calls)
    records = [json.loads(line) for line in tree.path.read_text().splitlines()]
    saved = [
        r["message"]
        for r in records
        if r.get("type") == "message" and r["message"]["role"] == "tool"
    ]
    assert len(saved) == 1 and saved[0]["details"] == parent.details
    assert not any(r.get("type") == "custom" for r in records)
    terminal = _Terminal(tmp_path)
    terminal.tree = NativeSessionTree.open(tree.path)
    terminal.history.render_active_branch()
    rendered = str(terminal.rows())
    assert all(f"{name}.txt" in rendered for name in ("alpha", "beta", "gamma"))
    assert rendered.count("✓ read") == 3


@pytest.mark.parametrize("mode", ["print", "json", "rpc"])
def test_cli_modes_real_delivery(tmp_path, monkeypatch, capfd, runtime, mode):
    import sys

    from pipy_harness.native import provider_construction

    fixtures(tmp_path)
    fake = provider()
    requests = []
    complete = FakeNativeProvider.complete

    def capture(self, request, **kwargs):
        requests.append(request)
        return complete(self, request, **kwargs)

    monkeypatch.setattr(FakeNativeProvider, "complete", capture)
    monkeypatch.setattr(
        provider_construction, "build_provider", lambda *args, **kwargs: fake
    )
    settled = threading.Event()

    class Output(io.BytesIO):
        def write(self, value):
            if b'"type":"agent_settled"' in value:
                settled.set()
            return super().write(value)

    class Input(io.StringIO):
        def readline(self, *args):
            line = super().readline(*args)
            if not line:
                assert settled.wait(15)
            return line

    output = Output()
    if mode == "rpc":
        monkeypatch.setattr(
            sys, "stdin", Input('{"type":"prompt","message":"summarize"}\n')
        )
        monkeypatch.setattr(sys, "stdout", type("Stdout", (), {"buffer": output})())
    args = [
        "repl",
        "--cwd",
        str(tmp_path),
        "--native-provider",
        "fake",
        "--native-model",
        "fake-tools",
        "--no-session",
        "--offline",
        "--tools",
        "+codemode",
    ]
    args += (
        ["--print", "summarize"]
        if mode == "print"
        else ["--mode", mode] + ([] if mode == "rpc" else ["summarize"])
    )
    assert cli.main(args) == 0
    assert (
        next(t for t in requests[0].available_tools if t.name == "codemode")
        == CodemodeTool().definition
    )
    results = [m for m in requests[1].messages if isinstance(m, AgentToolResultMessage)]
    assert len(results) == 1 and results[0].provider_correlation_id == "parent"
    assert json.loads(results[0].content.value.split("Output:\n")[1]) == EXPECTED
    assert len(results[0].details["nestedCalls"]["calls"]) == 3
    captured = capfd.readouterr()
    assert not captured.err
    if mode == "print":
        assert "T9_DONE" in captured.out
        return
    events = [
        json.loads(line)
        for line in (
            output.getvalue().decode() if mode == "rpc" else captured.out
        ).splitlines()
    ]
    children = [e for e in events if e.get("parentToolCallId") == "parent"]
    assert len(children) == 6
    assert [
        e["args"]["path"] for e in children if e["type"] == "tool_execution_end"
    ] == ["alpha.txt", "beta.txt", "gamma.txt"]
    end = next(
        e
        for e in events
        if e["type"] == "tool_execution_end" and e["toolCallId"] == "parent"
    )
    assert len(end["details"]["nestedCalls"]["calls"]) == 3
    messages = next(e["messages"] for e in events if e["type"] == "agent_end")
    assert [m["toolCallId"] for m in messages if m["role"] == "toolResult"] == [
        "parent"
    ]


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("adapter", [False, True])
def test_product_extension_hooks_frozen_builtins(
    tmp_path, monkeypatch, runtime, nested, adapter
):
    # Reuse the product extension fixture writer and ToolBlock/transform
    # patterns from the existing tool_call/tool_result conformance fixtures.
    _write_ext(
        tmp_path,
        "hooks",
        """from pipy_harness.extensions import ToolBlock, ToolResultTransform
def activate(api):
    @api.on('tool_call')
    def gate(event, ctx):
        if event.tool_name == 'codemode':
            assert ctx.set_active_tools(['codemode'])
        if event.tool_name == 'write':
            return ToolBlock(reason='T9 denied')
    @api.on('tool_result')
    def transform(event, ctx):
        if event.tool_name == 'read':
            return ToolResultTransform(content='WRAPPED::' + event.content)
        if event.tool_name == 'codemode':
            return ToolResultTransform(content='PARENT::' + event.content)
""",
    )
    (tmp_path / "alpha.txt").write_text("kept")
    sink = _CollectingAgentEventSink()
    if nested:
        fake = provider("""try:
    tools.write(path='denied', content='bad')
except ToolError as exc:
    text(str(exc))
text(tools.read(path='alpha.txt'))
""")
    else:
        fake = FakeNativeProvider(
            supports_tool_calls=True,
            programmable_tool_calls=(
                (
                    ProviderToolCall(
                        "blocked", "write", '{"path":"denied","content":"bad"}'
                    ),
                    ProviderToolCall("direct", "read", '{"path":"alpha.txt"}'),
                ),
                (),
            ),
        )
    requests = []
    complete = FakeNativeProvider.complete

    def capture(self, request, **kwargs):
        requests.append(request)
        return complete(self, request, **kwargs)

    monkeypatch.setattr(FakeNativeProvider, "complete", capture)
    args = dict(
        provider=fake, settings_manager=settings(tmp_path), agent_event_sink=sink
    )
    if adapter:
        owner = CodingSessionAdapter(**args)
        session = owner.build_session(owner.prepare_session_context(tmp_path))
    else:
        session = CodingSession(**args)
    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("run\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    parents = [e.result for e in sink.events if isinstance(e, ToolCallCompleted)]
    assert "WRAPPED::kept" in parents[-1].content.value
    assert not (tmp_path / "denied").exists()
    assert result.tool_invocation_count == (2 if nested else 1)
    if nested:
        assert parents[0].content.value.startswith("PARENT::Script completed")
        assert {t.name for t in requests[1].available_tools} == {"codemode"}
        assert not parents[0].is_error and "T9 denied" in parents[0].content.value
        assert [c["status"] for c in parents[0].details["nestedCalls"]["calls"]] == [
            "error",
            "ok",
        ]
    else:
        assert parents[0].is_error
        assert parents[-1].details is None


def test_public_malformed_and_budget_errors_are_catchable(tmp_path, runtime):
    (tmp_path / "alpha.txt").write_text("kept")
    sink = _CollectingAgentEventSink()
    code = """for i in range(3):
    try:
        tools.read(path=42)
    except ToolError:
        text('malformed caught')
text(tools.read(path='alpha.txt'))
try:
    tools.read(path='alpha.txt')
except ToolError:
    text('budget caught')
text('continued')
"""
    result = CodingSession(
        provider=provider(code),
        tool_budget=5,
        settings_manager=settings(tmp_path),
        agent_event_sink=sink,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("run\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    parent = next(e.result for e in sink.events if isinstance(e, ToolCallCompleted))
    assert not parent.is_error
    assert parent.content.value.count("malformed caught") == 3
    assert (
        "budget caught" in parent.content.value and "continued" in parent.content.value
    )
    assert [c["status"] for c in parent.details["nestedCalls"]["calls"]] == [
        "error"
    ] * 3 + ["ok", "error"]
    assert result.tool_invocation_count == 2


@pytest.mark.parametrize("phase", ["bash", "compute"])
def test_rpc_live_abort_reaps_and_next_turn_succeeds(
    tmp_path, monkeypatch, runtime, phase
):
    from test_native_automation_rpc import _RpcClient

    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.json").write_text(
        json.dumps({"defaultTools": ["+codemode"], "theme": "pi"})
    )
    fixtures(tmp_path)
    computing = threading.Event()
    original = host._Channel._on_text

    def on_text(channel, message):
        original(channel, message)
        if message.get("value") == "T9_COMPUTING":
            computing.set()

    monkeypatch.setattr(host._Channel, "_on_text", on_text)
    code = (
        "tools.bash(command='touch active; sleep 30')"
        if phase == "bash"
        else "text('T9_COMPUTING');\nwhile True: pass"
    )
    fake = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_tool_calls=(
            (ProviderToolCall("parent", "codemode", json.dumps({"code": code})),),
            (
                ProviderToolCall(
                    "fresh", "codemode", json.dumps({"code": SUMMARY_CODE})
                ),
            ),
            (),
        ),
    )
    client = _RpcClient(tmp_path, provider=fake, persist_tree=True)
    try:
        client.send({"type": "prompt", "message": "abort this"})
        wait_until(
            lambda: (
                (tmp_path / "active").exists()
                if phase == "bash"
                else computing.is_set()
            )
        )
        client.send({"id": "live", "type": "get_state"})
        assert client.wait_for(lambda e: e.get("id") == "live", timeout=10)["success"]
        client.send({"id": "stop", "type": "abort"})
        records = client.collect_until(
            lambda e: e["type"] == "agent_settled", timeout=10
        )
        assert next(e for e in records if e.get("id") == "stop")["success"]
        parent = next(
            e
            for e in records
            if e["type"] == "tool_execution_end" and e["toolCallId"] == "parent"
        )
        assert parent["isError"] and "Script aborted" in parent["result"]
        assert all(group_is_gone(pid) for pid in runtime)
        calls = parent["details"]["nestedCalls"]["calls"]
        assert [c["status"] for c in calls] == (
            ["cancelled"] if phase == "bash" else []
        )
        client.send({"type": "prompt", "message": "fresh summary"})
        fresh = client.collect_until(lambda e: e["type"] == "agent_settled", timeout=15)
        end = next(
            e
            for e in fresh
            if e["type"] == "tool_execution_end" and e["toolCallId"] == "fresh"
        )
        assert not end["isError"] and "cherry" in end["result"]
        saved = [json.loads(line) for line in client.tree.path.read_text().splitlines()]
        results = [
            e["message"]
            for e in saved
            if e.get("type") == "message" and e["message"]["role"] == "tool"
        ]
        assert [r["provider_correlation_id"] for r in results] == ["parent", "fresh"]
        assert results[0]["details"] == parent["details"]
    finally:
        client.close()
