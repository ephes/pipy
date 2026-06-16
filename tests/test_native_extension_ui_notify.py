"""Slice 9 tests for minimal extension UI notifications (ctx.ui.notify).

A command / hook handler's `ctx.ui.notify(message, kind)` is surfaced to
the live UI. Hooks route notifications through a notify sink so they reach
the session error stream; in non-interactive mode notify is deterministic
(records / emits, never blocks). Notification text is live UI output only.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native.extension_runtime import (
    activate_extensions,
    dispatch_lifecycle_hooks,
    extension_event_hooks,
    make_extension_context,
)
from pipy_harness.native.extension_runtime import (
    LifecycleEvent,
)
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.tool_loop_session import NativeToolReplSession


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


def _write(workspace: Path, name: str, body: str) -> None:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text(body, encoding="utf-8")


def test_notify_records_in_non_interactive_mode(tmp_path: Path) -> None:
    ctx = make_extension_context("/tmp", has_ui=False)
    ctx.ui.notify("hello")
    ctx.ui.notify("careful", kind="warning")
    ctx.ui.notify("bad-kind", kind="bogus")

    assert ctx.ui.messages == [  # type: ignore[attr-defined]
        ("info", "hello"),
        ("warning", "careful"),
        ("info", "bad-kind"),
    ]


def test_notify_routes_to_sink(tmp_path: Path) -> None:
    captured: list[tuple[str, str]] = []
    ctx = make_extension_context(
        "/tmp", has_ui=True, notify_sink=lambda kind, msg: captured.append((kind, msg))
    )
    ctx.ui.notify("live message")

    assert captured == [("info", "live message")]


def test_lifecycle_hook_notify_reaches_sink(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "noisy",
        "def activate(api):\n"
        "    @api.on('turn_start')\n"
        "    def obs(event, ctx):\n"
        "        ctx.ui.notify('turn starting')\n",
    )
    hooks = extension_event_hooks(
        activate_extensions(
            discover_extensions(workspace, config_home_env={}, home_dir=workspace)
        ),
        "turn_start",
    )
    captured: list[tuple[str, str]] = []

    dispatch_lifecycle_hooks(
        hooks,
        LifecycleEvent(name="turn_start"),
        cwd=str(workspace),
        has_ui=False,
        notify_sink=lambda kind, msg: captured.append((kind, msg)),
    )

    assert ("info", "turn starting") in captured


# -- product path: notify reaches the session error stream ----------------


class _FinalText:
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


def test_hook_notify_surfaces_in_the_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    ext = tmp_path / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "noisy.py").write_text(
        "def activate(api):\n"
        "    @api.on('agent_start')\n"
        "    def obs(event, ctx):\n"
        "        ctx.ui.notify('AGENT_STARTING_NOW')\n",
        encoding="utf-8",
    )
    session = NativeToolReplSession(provider=_FinalText(), tool_registry={})
    error_stream = io.StringIO()

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("hello\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    assert "AGENT_STARTING_NOW" in error_stream.getvalue()


def test_command_notify_still_surfaces(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    ext = tmp_path / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "st.py").write_text(
        "def activate(api):\n"
        "    def status(ctx, args):\n"
        "        ctx.ui.notify('STATUS_OK')\n"
        "    api.register_command('st', 'status', status)\n",
        encoding="utf-8",
    )
    session = NativeToolReplSession(provider=_FinalText(), tool_registry={})
    error_stream = io.StringIO()

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/st\n"),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )

    assert "STATUS_OK" in error_stream.getvalue()
