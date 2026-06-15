"""Slice 5 tests for extension lifecycle event hooks.

An extension may register `@api.on(...)` handlers for the lifecycle
events `session_start`, `session_shutdown`, `agent_start`, `agent_end`,
`turn_start`, and `turn_end`. They are observe-only: the return value is
ignored, a crashing observer never breaks the session, and the events
carry only safe metadata (event name + a `reason` for session_start).
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native.extension_runtime import (
    LifecycleEvent,
    activate_extensions,
    dispatch_lifecycle_hooks,
    extension_event_hooks,
)
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.tool_loop_session import NativeToolReplSession


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
        discover_extensions(workspace, config_home_env={}, home_dir=workspace)
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
        "                 'agent_end','turn_start','turn_end'):\n"
        "        api.on(name, lambda event, ctx: None)\n",
    )
    activated = _activate(workspace)

    for name in (
        "session_start",
        "session_shutdown",
        "agent_start",
        "agent_end",
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
        "    @api.on('agent_end')\n"
        "    def boom(event, ctx):\n"
        "        raise RuntimeError('boom')\n",
    )
    _write(
        workspace,
        "later",
        "from pathlib import Path\n"
        f"PROOF = Path({str(proof)!r})\n"
        "def activate(api):\n"
        "    @api.on('agent_end')\n"
        "    def ok(event, ctx):\n"
        "        PROOF.write_text('ran')\n",
    )

    # The crashing observer must not prevent the later observer running,
    # and dispatch must not raise.
    _dispatch(workspace, "agent_end")

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
        "              'agent_end','turn_start','turn_end'):\n"
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
    assert "session_shutdown:" in recorded
    # Ordering: session brackets the agent run.
    assert recorded.index("session_start:startup") < recorded.index("agent_start:")
    assert recorded.index("agent_end:") < recorded.index("session_shutdown:")


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
