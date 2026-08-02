"""Slice 5 tests for extension lifecycle event hooks.

An extension may register `@api.on(...)` handlers for the lifecycle
events `session_start`, `session_shutdown`, `agent_start`, `agent_end`,
`agent_settled`, `turn_start`, and `turn_end`. They are observe-only: the return value is
ignored, a crashing observer never breaks the session, and the events
carry only safe metadata (event name + a `reason` for session_start).
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import extension_hooks as extension_hooks_module
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentEvent,
    AgentRunCompleted,
    AgentRunOutcome,
    AgentRunResult,
    AgentRunStarted,
    AgentTurnOutcome,
    ProductContent,
    TurnCompleted,
    TurnStarted,
)
from pipy_harness.native.extension_hooks import (
    _ExtensionLifecycleAgentEventAdapter,
    dispatch_lifecycle_hooks,
    extension_event_hooks,
)
from pipy_harness.native.extension_runtime import (
    ExtensionUiDriver,
    LifecycleEvent,
    activate_extensions,
)
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.tool_loop_session import NativeToolReplSession


def _generation_snapshot(
    hooks: dict[str, tuple[object, ...]], flags: dict[str, object] | None = None
):
    projection = SimpleNamespace(
        hooks=SimpleNamespace(lifecycle=hooks),
        runtime_flags=SimpleNamespace(values={} if flags is None else flags),
        chrome=None,
    )
    snapshot = SimpleNamespace(
        generation=SimpleNamespace(projection=projection), generation_id=0
    )
    return lambda: snapshot


class _FinalTextProvider:
    name = "stub"
    model_id = "stub-model"

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="done",
            tool_calls=(),
        )


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


def _write(workspace: Path, name: str, body: str) -> None:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text(body, encoding="utf-8")


def _activate(workspace: Path) -> list:
    return activate_extensions(
        discover_extensions(
            workspace,
            config_home_env={},
            home_dir=workspace,
            include_workspace_defaults=True,
        )
    )


def _dispatch(workspace, event_name, reason=None):
    hooks = extension_event_hooks(_activate(workspace), event_name)
    dispatch_lifecycle_hooks(
        hooks,
        LifecycleEvent(name=event_name, reason=reason),
        cwd=str(workspace),
        has_ui=False,
    )


def test_lifecycle_hook_fires_and_records(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    proof = tmp_path / "proof.txt"
    _write(
        workspace,
        "tracker",
        "from pathlib import Path\n"
        f"PROOF = Path({str(proof)!r})\n"
        "def activate(api):\n"
        "    @api.on('session_start')\n"
        "    def started(event, ctx):\n"
        "        PROOF.write_text(event.name + ':' + (event.reason or ''))\n",
    )

    _dispatch(workspace, "session_start", reason="startup")

    assert proof.read_text() == "session_start:startup"


def test_all_lifecycle_events_collect(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "many",
        "def activate(api):\n"
        "    for name in ('session_start','session_shutdown','agent_start',\n"
        "                 'agent_end','agent_settled','turn_start','turn_end'):\n"
        "        api.on(name, lambda event, ctx: None)\n",
    )
    activated = _activate(workspace)

    for name in (
        "session_start",
        "session_shutdown",
        "agent_start",
        "agent_end",
        "agent_settled",
        "turn_start",
        "turn_end",
    ):
        assert len(extension_event_hooks(activated, name)) == 1


def test_observer_return_value_is_ignored(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "ret",
        "def activate(api):\n"
        "    @api.on('turn_start')\n"
        "    def obs(event, ctx):\n"
        "        return 'ignored'\n",
    )

    # Should simply not raise; the return value has no effect.
    _dispatch(workspace, "turn_start")


def test_crashing_observer_does_not_break_dispatch(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    proof = tmp_path / "ran.txt"
    _write(
        workspace,
        "crasher",
        "def activate(api):\n"
        "    @api.on('agent_settled')\n"
        "    def boom(event, ctx):\n"
        "        raise RuntimeError('boom')\n",
    )
    _write(
        workspace,
        "later",
        "from pathlib import Path\n"
        f"PROOF = Path({str(proof)!r})\n"
        "def activate(api):\n"
        "    @api.on('agent_settled')\n"
        "    def ok(event, ctx):\n"
        "        PROOF.write_text('ran')\n",
    )

    # The crashing observer must not prevent the later observer running,
    # and dispatch must not raise.
    _dispatch(workspace, "agent_settled")

    assert proof.read_text() == "ran"


def test_async_lifecycle_hook(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    proof = tmp_path / "async.txt"
    _write(
        workspace,
        "asyncobs",
        "from pathlib import Path\n"
        f"PROOF = Path({str(proof)!r})\n"
        "def activate(api):\n"
        "    @api.on('agent_start')\n"
        "    async def obs(event, ctx):\n"
        "        PROOF.write_text('async-ran')\n",
    )

    _dispatch(workspace, "agent_start")

    assert proof.read_text() == "async-ran"


def test_agent_event_adapter_delivers_immediately_then_maps_lifecycle() -> None:
    trace: list[str] = []

    class ImmediateSink:
        def emit(self, event: AgentEvent) -> None:
            trace.append(f"immediate:{type(event).__name__}")

    def observe(event: LifecycleEvent, _ctx: object) -> None:
        trace.append(f"extension:{event.name}")

    lifecycle_names = (
        "agent_start",
        "turn_start",
        "turn_end",
        "agent_end",
        "agent_settled",
    )
    adapter = _ExtensionLifecycleAgentEventAdapter(
        ImmediateSink(),
        generation_snapshot=cast(
            Any,
            _generation_snapshot({name: (observe,) for name in lifecycle_names}),
        ),
        cwd="/workspace",
        has_ui=False,
    )
    assistant = AgentAssistantMessage(ProductContent("done"))
    events: tuple[AgentEvent, ...] = (
        AgentRunStarted(),
        TurnStarted(0),
        TurnCompleted(0, AgentTurnOutcome.SUCCEEDED, assistant),
        AgentRunCompleted(AgentRunResult(AgentRunOutcome.SUCCEEDED, (assistant,))),
    )

    for event in events:
        adapter.emit(event)
    adapter.agent_settled()

    assert trace == [
        "immediate:AgentRunStarted",
        "extension:agent_start",
        "immediate:TurnStarted",
        "extension:turn_start",
        "immediate:TurnCompleted",
        "extension:turn_end",
        "immediate:AgentRunCompleted",
        "extension:agent_end",
        "extension:agent_settled",
    ]

    class FailingSink:
        def emit(self, event: AgentEvent) -> None:
            del event
            raise RuntimeError("projection failed")

    failing = _ExtensionLifecycleAgentEventAdapter(
        FailingSink(),
        generation_snapshot=cast(
            Any, _generation_snapshot({"agent_start": (observe,)})
        ),
        cwd="/workspace",
        has_ui=False,
    )
    with pytest.raises(RuntimeError, match="projection failed"):
        failing.emit(AgentRunStarted())
    assert trace[-1] == "extension:agent_settled"


def test_agent_event_adapter_snapshots_hooks_flags_and_chrome_context_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[object, ...]] = []

    def old_hook(_event: object, _ctx: object) -> None:
        return None

    def new_hook(_event: object, _ctx: object) -> None:
        return None

    driver = cast(ExtensionUiDriver, object())

    def capture_dispatch(
        hooks: object,
        event: LifecycleEvent,
        **context: object,
    ) -> None:
        captured.append((hooks, event, context))

    class ImmediateSink:
        def emit(self, event: AgentEvent) -> None:
            del event

    monkeypatch.setattr(
        extension_hooks_module, "dispatch_lifecycle_hooks", capture_dispatch
    )
    flags = {"plan": False}
    projection = SimpleNamespace(
        hooks=SimpleNamespace(lifecycle={"session_start": (old_hook,)}),
        runtime_flags=SimpleNamespace(values=flags),
        chrome=None,
    )
    replacement = SimpleNamespace(
        hooks=SimpleNamespace(lifecycle={"session_start": (new_hook,)}),
        runtime_flags=SimpleNamespace(values={"replacement": True}),
        chrome=None,
    )
    snapshot_calls = 0

    def generation_snapshot() -> object:
        nonlocal snapshot_calls, projection
        snapshot_calls += 1
        held = projection
        projection = replacement
        return SimpleNamespace(
            generation=SimpleNamespace(projection=held),
            generation_id=snapshot_calls - 1,
        )

    adapter = _ExtensionLifecycleAgentEventAdapter(
        ImmediateSink(),
        generation_snapshot=cast(Any, generation_snapshot),
        cwd="/workspace",
        has_ui=True,
        notify_sink=None,
        ui_driver=driver,
        project_trusted=True,
    )
    flags["plan"] = True

    adapter.fire_lifecycle("session_start", reason="startup")

    hooks, event, context = captured[0]
    assert hooks == (old_hook,)
    assert event == LifecycleEvent(name="session_start", reason="startup")
    assert context == {
        "cwd": "/workspace",
        "has_ui": True,
        "notify_sink": None,
        "ui_driver": driver,
        "flags": {"plan": True},
        "project_trusted": True,
    }
    assert snapshot_calls == 1


def test_lifecycle_events_fire_through_the_session(tmp_path, monkeypatch) -> None:
    # Product path: a one-prompt session run fires the full lifecycle
    # sequence to extension observers.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    proof = tmp_path / "events.txt"
    _write(
        tmp_path,
        "recorder",
        "from pathlib import Path\n"
        f"PROOF = Path({str(proof)!r})\n"
        "def activate(api):\n"
        "    def make(name):\n"
        "        def obs(event, ctx):\n"
        "            with PROOF.open('a') as fh:\n"
        "                fh.write(event.name + ':' + (event.reason or '') + '\\n')\n"
        "        return obs\n"
        "    for n in ('session_start','session_shutdown','agent_start',\n"
        "              'agent_end','agent_settled','turn_start','turn_end'):\n"
        "        api.on(n, make(n))\n",
    )
    session = NativeToolReplSession(provider=_FinalTextProvider(), tool_registry={})

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("hello\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    recorded = proof.read_text().splitlines()
    assert "session_start:startup" in recorded
    assert "agent_start:" in recorded
    assert "turn_start:" in recorded
    assert "turn_end:" in recorded
    assert "agent_end:" in recorded
    assert "agent_settled:" in recorded
    assert "session_shutdown:" in recorded
    # Ordering: session brackets the agent run.
    assert recorded.index("session_start:startup") < recorded.index("agent_start:")
    assert recorded.index("agent_end:") < recorded.index("agent_settled:")
    assert recorded.index("agent_settled:") < recorded.index("session_shutdown:")


def test_agent_settled_waits_for_extension_queued_continuation(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    proof = tmp_path / "events.txt"
    _write(
        tmp_path,
        "recorder",
        "from pathlib import Path\n"
        f"PROOF = Path({str(proof)!r})\n"
        "sent = False\n"
        "def activate(api):\n"
        "    def record(name):\n"
        "        with PROOF.open('a') as fh:\n"
        "            fh.write(name + '\\n')\n"
        "    @api.on('agent_start')\n"
        "    def started(event, ctx):\n"
        "        record(event.name)\n"
        "    @api.on('agent_end')\n"
        "    def ended(event, ctx):\n"
        "        global sent\n"
        "        record(event.name)\n"
        "        if not sent:\n"
        "            sent = True\n"
        "            api.send_user_message('queued follow-up')\n"
        "    @api.on('agent_settled')\n"
        "    def settled(event, ctx):\n"
        "        record(event.name + ':' + (event.reason or ''))\n",
    )
    session = NativeToolReplSession(provider=_FinalTextProvider(), tool_registry={})

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("hello\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert proof.read_text().splitlines() == [
        "agent_start",
        "agent_end",
        "agent_start",
        "agent_end",
        "agent_settled:",
    ]


def test_agent_settled_handler_can_schedule_a_new_run_without_blocking(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    proof = tmp_path / "events.txt"
    _write(
        tmp_path,
        "recorder",
        "from pathlib import Path\n"
        f"PROOF = Path({str(proof)!r})\n"
        "sent = False\n"
        "def activate(api):\n"
        "    @api.on('agent_settled')\n"
        "    def settled(event, ctx):\n"
        "        global sent\n"
        "        with PROOF.open('a') as fh:\n"
        "            fh.write(event.name + '\\n')\n"
        "        if not sent:\n"
        "            sent = True\n"
        "            api.send_user_message('new run after idle')\n",
    )
    provider = _FinalTextProvider()
    session = NativeToolReplSession(provider=provider, tool_registry={})

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("hello\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert result.user_turn_count == 2
    assert proof.read_text().splitlines() == ["agent_settled", "agent_settled"]


def test_agent_settled_fires_after_unexpected_mid_run_failure(
    tmp_path: Path, monkeypatch
) -> None:
    import pytest

    class _RaisingProvider(_FinalTextProvider):
        def complete(
            self, request: ProviderRequest, **_kwargs: object
        ) -> ProviderResult:
            raise RuntimeError("provider exploded")

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    proof = tmp_path / "events.txt"
    _write(
        tmp_path,
        "recorder",
        "from pathlib import Path\n"
        f"PROOF = Path({str(proof)!r})\n"
        "def activate(api):\n"
        "    def record(event, ctx):\n"
        "        with PROOF.open('a') as fh:\n"
        "            fh.write(event.name + '\\n')\n"
        "    api.on('agent_start', record)\n"
        "    api.on('agent_end', record)\n"
        "    api.on('agent_settled', record)\n"
        "    api.on('session_shutdown', record)\n",
    )
    session = NativeToolReplSession(provider=_RaisingProvider(), tool_registry={})

    with pytest.raises(RuntimeError, match="provider exploded"):
        session.run(
            workspace_root=tmp_path,
            input_stream=io.StringIO("hello\n"),
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        )

    assert proof.read_text().splitlines() == [
        "agent_start",
        "agent_settled",
        "session_shutdown",
    ]


def test_agent_settled_fires_after_completed_budget_exhaustion(
    tmp_path: Path, monkeypatch
) -> None:
    class _MalformedProvider(_FinalTextProvider):
        def __init__(self) -> None:
            self.turn = 0

        def complete(
            self, request: ProviderRequest, **_kwargs: object
        ) -> ProviderResult:
            self.turn += 1
            now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text=None,
                tool_calls=(
                    ProviderToolCall(
                        provider_correlation_id=f"bad-{self.turn}",
                        tool_name=f"missing-{self.turn}",
                        arguments_json="{}",
                    ),
                ),
            )

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    proof = tmp_path / "events.txt"
    _write(
        tmp_path,
        "recorder",
        "from pathlib import Path\n"
        f"PROOF = Path({str(proof)!r})\n"
        "def activate(api):\n"
        "    def record(event, ctx):\n"
        "        with PROOF.open('a') as fh:\n"
        "            fh.write(event.name + '\\n')\n"
        "    api.on('agent_start', record)\n"
        "    api.on('agent_end', record)\n"
        "    api.on('agent_settled', record)\n"
        "    api.on('session_shutdown', record)\n",
    )
    session = NativeToolReplSession(provider=_MalformedProvider(), tool_registry={})

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("hello\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert result.error_type is None
    assert result.budget_exhausted_count == 2
    assert proof.read_text().splitlines() == [
        "agent_start",
        "agent_end",
        "agent_settled",
        "session_shutdown",
    ]


def test_agent_settled_fires_after_controlled_malformed_fatal(
    tmp_path: Path, monkeypatch
) -> None:
    class _MalformedBashProvider(_FinalTextProvider):
        def __init__(self) -> None:
            self.turn = 0
            self.advertised_tools: list[tuple[str, ...]] = []

        def complete(
            self, request: ProviderRequest, **_kwargs: object
        ) -> ProviderResult:
            self.turn += 1
            self.advertised_tools.append(
                tuple(tool.name for tool in request.available_tools)
            )
            if self.turn > 3:
                raise AssertionError("malformed fatal must stop before another request")
            now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text=None,
                tool_calls=(
                    ProviderToolCall(
                        provider_correlation_id=f"malformed-{self.turn}",
                        tool_name="bash",
                        arguments_json="{",
                    ),
                ),
            )

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    proof = tmp_path / "events.txt"
    _write(
        tmp_path,
        "recorder",
        "from pathlib import Path\n"
        f"PROOF = Path({str(proof)!r})\n"
        "def activate(api):\n"
        "    def record(event, ctx):\n"
        "        with PROOF.open('a') as fh:\n"
        "            fh.write(event.name + '\\n')\n"
        "    api.on('agent_start', record)\n"
        "    api.on('agent_end', record)\n"
        "    api.on('agent_settled', record)\n"
        "    api.on('session_shutdown', record)\n",
    )
    provider = _MalformedBashProvider()
    session = NativeToolReplSession(provider=provider)

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("hello\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.FAILED
    assert result.error_type == "NativeToolLoopMalformedFatal"
    assert result.tool_invocation_count == 0
    assert result.malformed_argument_count == 3
    assert result.consecutive_malformed_streak == 3
    assert provider.turn == 3
    assert all("bash" in names for names in provider.advertised_tools)
    assert proof.read_text().splitlines() == [
        "agent_start",
        "agent_end",
        "agent_settled",
        "session_shutdown",
    ]


def test_agent_settled_does_not_fire_without_agent_run(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    proof = tmp_path / "events.txt"
    _write(
        tmp_path,
        "recorder",
        "from pathlib import Path\n"
        f"PROOF = Path({str(proof)!r})\n"
        "def activate(api):\n"
        "    def record(event, ctx):\n"
        "        with PROOF.open('a') as fh:\n"
        "            fh.write(event.name + '\\n')\n"
        "    api.on('agent_settled', record)\n"
        "    api.on('session_shutdown', record)\n",
    )
    session = NativeToolReplSession(provider=_FinalTextProvider(), tool_registry={})

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(""),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert proof.read_text().splitlines() == ["session_shutdown"]


def test_keyboard_interrupt_propagates(tmp_path: Path) -> None:
    import pytest

    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "intr",
        "def activate(api):\n"
        "    @api.on('turn_end')\n"
        "    def obs(event, ctx):\n"
        "        raise KeyboardInterrupt()\n",
    )

    with pytest.raises(KeyboardInterrupt):
        _dispatch(workspace, "turn_end")
