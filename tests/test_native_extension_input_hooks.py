"""Slice 6 tests for input / before_agent_start hooks and send_user_message.

`input` hooks observe or transform a submitted prompt before a turn;
`before_agent_start` hooks inject bounded context / alter system-prompt
options; `api.send_user_message` enqueues a deterministic provider turn.
All are fail-safe: a crashing input/before-agent hook leaves the prompt /
system prompt unchanged, never breaking submission.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native.extension_runtime import (
    activate_extensions,
    dispatch_before_agent_start_hooks,
    dispatch_input_hooks,
    drain_user_messages,
    extension_event_hooks,
)
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.tool_loop_session import NativeToolReplSession


class _RecordingProvider:
    name = "stub"
    model_id = "stub-model"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
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


def _activate(workspace: Path, outbox: list | None = None) -> list:
    return activate_extensions(
        discover_extensions(workspace, config_home_env={}, home_dir=workspace),
        message_outbox=outbox,
    )


def test_input_hook_transforms_text(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "upper",
        "from pipy_harness.extensions import InputTransform\n"
        "def activate(api):\n"
        "    @api.on('input')\n"
        "    def transform(event, ctx):\n"
        "        return InputTransform(text=event.text.upper())\n",
    )
    hooks = extension_event_hooks(_activate(workspace), "input")

    result = dispatch_input_hooks(hooks, "hello", cwd=str(workspace), has_ui=False)

    assert result == "HELLO"


def test_input_hook_observe_only_leaves_text(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "obs",
        "def activate(api):\n"
        "    @api.on('input')\n"
        "    def observe(event, ctx):\n"
        "        return None\n",
    )
    hooks = extension_event_hooks(_activate(workspace), "input")

    assert dispatch_input_hooks(hooks, "hello", cwd=str(workspace), has_ui=False) == "hello"


def test_input_hooks_chain(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "aaa",
        "from pipy_harness.extensions import InputTransform\n"
        "def activate(api):\n"
        "    @api.on('input')\n"
        "    def t(event, ctx):\n"
        "        return InputTransform(text=event.text + '-a')\n",
    )
    _write(
        workspace,
        "bbb",
        "from pipy_harness.extensions import InputTransform\n"
        "def activate(api):\n"
        "    @api.on('input')\n"
        "    def t(event, ctx):\n"
        "        return InputTransform(text=event.text + '-b')\n",
    )
    hooks = extension_event_hooks(_activate(workspace), "input")

    assert dispatch_input_hooks(hooks, "x", cwd=str(workspace), has_ui=False) == "x-a-b"


def test_input_hook_non_string_transform_is_ignored(tmp_path: Path) -> None:
    # A non-string InputTransform.text must not propagate; the prompt is
    # left unchanged (fail-safe).
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "bad",
        "from pipy_harness.extensions import InputTransform\n"
        "def activate(api):\n"
        "    @api.on('input')\n"
        "    def t(event, ctx):\n"
        "        return InputTransform(text=object())\n",
    )
    hooks = extension_event_hooks(_activate(workspace), "input")

    assert dispatch_input_hooks(hooks, "keep", cwd=str(workspace), has_ui=False) == "keep"


def test_before_agent_start_hooks_see_accumulated_prompt(tmp_path: Path) -> None:
    # A later before_agent_start hook sees earlier hooks' appended context
    # in event.system_prompt (ordered composition).
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "a",
        "from pipy_harness.extensions import BeforeAgentStartResult\n"
        "def activate(api):\n"
        "    @api.on('before_agent_start')\n"
        "    def first(event, ctx):\n"
        "        return BeforeAgentStartResult(append_system_prompt='FIRST')\n",
    )
    _write(
        workspace,
        "b",
        "from pipy_harness.extensions import BeforeAgentStartResult\n"
        "def activate(api):\n"
        "    @api.on('before_agent_start')\n"
        "    def second(event, ctx):\n"
        "        saw = 'YES' if 'FIRST' in event.system_prompt else 'NO'\n"
        "        return BeforeAgentStartResult(append_system_prompt='SAW_FIRST=' + saw)\n",
    )
    hooks = extension_event_hooks(_activate(workspace), "before_agent_start")

    result = dispatch_before_agent_start_hooks(
        hooks, cwd=str(workspace), has_ui=False, system_prompt="BASE"
    )

    assert "SAW_FIRST=YES" in (result.append_system_prompt or "")


def test_crashing_input_hook_leaves_text(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "boom",
        "def activate(api):\n"
        "    @api.on('input')\n"
        "    def t(event, ctx):\n"
        "        raise RuntimeError('x')\n",
    )
    hooks = extension_event_hooks(_activate(workspace), "input")

    assert dispatch_input_hooks(hooks, "keep", cwd=str(workspace), has_ui=False) == "keep"


def test_before_agent_start_appends_system_prompt(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "ctx",
        "from pipy_harness.extensions import BeforeAgentStartResult\n"
        "def activate(api):\n"
        "    @api.on('before_agent_start')\n"
        "    def inject(event, ctx):\n"
        "        return BeforeAgentStartResult(append_system_prompt='EXTRA')\n",
    )
    hooks = extension_event_hooks(_activate(workspace), "before_agent_start")

    result = dispatch_before_agent_start_hooks(hooks, cwd=str(workspace), has_ui=False)

    assert result.append_system_prompt == "EXTRA"


def test_before_agent_start_concatenates(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    for name, text in (("a", "ONE"), ("b", "TWO")):
        _write(
            workspace,
            name,
            "from pipy_harness.extensions import BeforeAgentStartResult\n"
            "def activate(api):\n"
            "    @api.on('before_agent_start')\n"
            "    def inject(event, ctx):\n"
            f"        return BeforeAgentStartResult(append_system_prompt='{text}')\n",
        )
    hooks = extension_event_hooks(_activate(workspace), "before_agent_start")

    result = dispatch_before_agent_start_hooks(hooks, cwd=str(workspace), has_ui=False)

    assert "ONE" in (result.append_system_prompt or "")
    assert "TWO" in (result.append_system_prompt or "")


def test_before_agent_start_injection_is_bounded(tmp_path: Path) -> None:
    # A before_agent_start injection is bounded so a buggy/malicious
    # extension cannot create unbounded provider input.
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "big",
        "from pipy_harness.extensions import BeforeAgentStartResult\n"
        "def activate(api):\n"
        "    @api.on('before_agent_start')\n"
        "    def inject(event, ctx):\n"
        "        return BeforeAgentStartResult(append_system_prompt='X' * 200000)\n",
    )
    hooks = extension_event_hooks(_activate(workspace), "before_agent_start")

    result = dispatch_before_agent_start_hooks(hooks, cwd=str(workspace), has_ui=False)

    assert result.append_system_prompt is not None
    assert len(result.append_system_prompt) <= 32 * 1024


def test_send_user_message_enqueues(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "sender",
        "def activate(api):\n"
        "    def cmd(ctx, args):\n"
        "        api.send_user_message('queued prompt')\n"
        "    api.register_command('go', 'g', cmd)\n",
    )
    outbox: list = []
    activated = _activate(workspace, outbox)
    # Invoke the command handler (as the dispatcher would).
    handler = next(
        c.handler for a in activated for c in a.commands if c.name == "go"
    )
    handler(object(), "")

    queued = drain_user_messages(outbox)
    assert [m.content for m in queued] == ["queued prompt"]
    assert drain_user_messages(outbox) == []  # drained once


def test_input_hook_transforms_provider_prompt_through_session(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    ext = tmp_path / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "tag.py").write_text(
        "from pipy_harness.extensions import InputTransform\n"
        "def activate(api):\n"
        "    @api.on('input')\n"
        "    def t(event, ctx):\n"
        "        return InputTransform(text='[TAGGED] ' + event.text)\n",
        encoding="utf-8",
    )
    provider = _RecordingProvider()
    session = NativeToolReplSession(provider=provider, tool_registry={})

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("hello world\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert "[TAGGED] hello world" in provider.requests[0].user_prompt


def test_before_agent_start_injects_system_prompt_through_session(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    ext = tmp_path / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "ctx.py").write_text(
        "from pipy_harness.extensions import BeforeAgentStartResult\n"
        "def activate(api):\n"
        "    @api.on('before_agent_start')\n"
        "    def inject(event, ctx):\n"
        "        return BeforeAgentStartResult(append_system_prompt='INJECTED_CONTEXT')\n",
        encoding="utf-8",
    )
    provider = _RecordingProvider()
    session = NativeToolReplSession(provider=provider, tool_registry={})

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("hello\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert "INJECTED_CONTEXT" in (provider.requests[0].system_prompt or "")


def test_send_user_message_triggers_a_turn_through_session(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    ext = tmp_path / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "trigger.py").write_text(
        "def activate(api):\n"
        "    def cmd(ctx, args):\n"
        "        api.send_user_message('probe prompt')\n"
        "    api.register_command('go', 'g', cmd)\n",
        encoding="utf-8",
    )
    provider = _RecordingProvider()
    session = NativeToolReplSession(provider=provider, tool_registry={})

    # The command issues no provider turn; the enqueued message does.
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert len(provider.requests) == 1
    assert "probe prompt" in provider.requests[0].user_prompt


def test_send_user_message_slash_is_a_prompt_not_a_command(
    tmp_path, monkeypatch
) -> None:
    # A queued message that looks like a slash command must be delivered
    # as a provider prompt, never parsed as a local command.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    ext = tmp_path / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "trigger.py").write_text(
        "def activate(api):\n"
        "    def cmd(ctx, args):\n"
        "        api.send_user_message('/help me please')\n"
        "    api.register_command('go', 'g', cmd)\n",
        encoding="utf-8",
    )
    provider = _RecordingProvider()
    session = NativeToolReplSession(provider=provider, tool_registry={})

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    # The "/help..." message reached the provider as a prompt.
    assert len(provider.requests) == 1
    assert "/help me please" in provider.requests[0].user_prompt


def test_send_user_message_from_a_hook_is_scheduled(tmp_path, monkeypatch) -> None:
    # A message enqueued from a hook (not a command) is still scheduled
    # as a deterministic turn (drained at the top of the loop).
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    ext = tmp_path / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    guard = tmp_path / "fired"
    (ext / "hookq.py").write_text(
        "from pathlib import Path\n"
        f"GUARD = Path({str(guard)!r})\n"
        "def activate(api):\n"
        "    @api.on('input')\n"
        "    def once(event, ctx):\n"
        "        if not GUARD.exists():\n"
        "            GUARD.write_text('x')\n"
        "            api.send_user_message('hook followup')\n"
        "        return None\n",
        encoding="utf-8",
    )
    provider = _RecordingProvider()
    session = NativeToolReplSession(provider=provider, tool_registry={})

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("first\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    prompts = [r.user_prompt for r in provider.requests]
    assert any("first" in p for p in prompts)
    assert any("hook followup" in p for p in prompts)


def test_before_agent_start_non_string_is_failsafe(tmp_path, monkeypatch) -> None:
    # A hook returning a non-string append_system_prompt must not break
    # the turn (fail-safe), and must not inject anything.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    ext = tmp_path / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "bad.py").write_text(
        "from pipy_harness.extensions import BeforeAgentStartResult\n"
        "def activate(api):\n"
        "    @api.on('before_agent_start')\n"
        "    def inject(event, ctx):\n"
        "        return BeforeAgentStartResult(append_system_prompt=object())\n",
        encoding="utf-8",
    )
    provider = _RecordingProvider()
    session = NativeToolReplSession(provider=provider, tool_registry={})

    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("hello\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    # The run still completed (the provider was called).
    assert len(provider.requests) == 1


def test_send_user_message_during_failed_activation_is_discarded(
    tmp_path: Path,
) -> None:
    # If activation fails after send_user_message, the queued prompt is
    # discarded (a disabled extension never triggers a turn).
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "failq",
        "def activate(api):\n"
        "    api.send_user_message('ghost')\n"
        "    api.register_command('Bad Name', 'x', lambda ctx, args: None)\n",
    )
    outbox: list = []
    activated = _activate(workspace, outbox)

    failq = next(a for a in activated if a.name == "failq")
    assert failq.status == "disabled"
    assert drain_user_messages(outbox) == []


def test_send_user_message_during_successful_activation_is_committed(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "okq",
        "def activate(api):\n    api.send_user_message('startup msg')\n",
    )
    outbox: list = []
    _activate(workspace, outbox)

    assert [m.content for m in drain_user_messages(outbox)] == ["startup msg"]


def test_keyboard_interrupt_propagates_in_input_hook(tmp_path: Path) -> None:
    import pytest

    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "intr",
        "def activate(api):\n"
        "    @api.on('input')\n"
        "    def t(event, ctx):\n"
        "        raise KeyboardInterrupt()\n",
    )
    hooks = extension_event_hooks(_activate(workspace), "input")

    with pytest.raises(KeyboardInterrupt):
        dispatch_input_hooks(hooks, "x", cwd=str(workspace), has_ui=False)
