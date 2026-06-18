"""Slice 13 tests for Pi-shaped live-session extension hooks.

This covers the follow-on extension surfaces that act on a live product
session: user-bash gates, provider-request transforms, session-operation
gates, and dynamic active tool controls.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native.extension_runtime import (
    ProviderRequestTransform,
    SessionDecision,
    UserBashDecision,
    dispatch_before_provider_request_hooks,
    dispatch_session_before_hooks,
    dispatch_user_bash_hooks,
)
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.tool_loop_session import (
    NativeToolReplSession,
    production_tool_registry,
)


class _CapturingProvider:
    name = "stub"
    model_id = "stub-model"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="ok",
        )


def _write_ext(root: Path, name: str, body: str) -> None:
    ext = root / ".pipy" / "extensions"
    ext.mkdir(parents=True, exist_ok=True)
    (ext / f"{name}.py").write_text(body, encoding="utf-8")


def test_dispatchers_expose_dynamic_control_context(tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []

    def set_tools(names):
        calls.append(("set_tools_arg", tuple(names)))
        return True

    def set_model(ref):
        calls.append(("set_model_arg", ref))
        return True

    def set_thinking(level):
        calls.append(("set_thinking_arg", level))
        return True

    def before_provider(event, ctx):
        assert event.available_tools == ("read", "bash")
        calls.append(("tools", ctx.set_active_tools(["bash"])))
        calls.append(("model", ctx.set_model("fake/fake-tools")))
        calls.append(("thinking", ctx.set_thinking_level("low")))
        return ProviderRequestTransform(user_prompt=event.user_prompt + "::hook")

    request = ProviderRequest(
        system_prompt="sys",
        user_prompt="hello",
        provider_name="stub",
        model_id="stub-model",
        cwd=tmp_path,
        available_tools=(
            type("T", (), {"name": "read"})(),
            type("T", (), {"name": "bash"})(),
        ),
    )
    transform = dispatch_before_provider_request_hooks(
        (before_provider,),
        request,
        cwd=str(tmp_path),
        has_ui=False,
        set_active_tools_fn=set_tools,
        set_model_fn=set_model,
        set_thinking_level_fn=set_thinking,
    )

    assert transform.user_prompt == "hello::hook"
    assert ("set_tools_arg", ("bash",)) in calls
    assert ("set_model_arg", "fake/fake-tools") in calls
    assert ("set_thinking_arg", "low") in calls


def test_user_bash_dispatch_rewrites_and_synthesizes(tmp_path: Path) -> None:
    def hook(event, _ctx):
        assert event.command == "echo real"
        return UserBashDecision(
            command="echo synthetic",
            exclude_from_context=False,
            result="SYNTHETIC\n",
            exit_code=0,
        )

    decision = dispatch_user_bash_hooks(
        (hook,),
        command="echo real",
        exclude_from_context=True,
        cwd=str(tmp_path),
        has_ui=False,
    )

    assert decision.allowed
    assert decision.command == "echo synthetic"
    assert decision.exclude_from_context is False
    assert decision.result == "SYNTHETIC\n"


def test_session_gate_blocks_operation(tmp_path: Path) -> None:
    def hook(event, _ctx):
        assert event.operation == "compact"
        return SessionDecision(allow=False, reason="policy")

    decision = dispatch_session_before_hooks(
        (hook,), operation="compact", cwd=str(tmp_path), has_ui=False
    )

    assert not decision.allow
    assert decision.reason == "policy"


def test_before_provider_request_hook_transforms_product_request(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write_ext(
        tmp_path,
        "request",
        "from pipy_harness.extensions import ProviderRequestTransform\n"
        "def activate(api):\n"
        "    @api.on('before_provider_request')\n"
        "    def before(event, ctx):\n"
        "        ok = ctx.set_active_tools(['bash'])\n"
        "        assert ok\n"
        "        assert ctx.set_model('fake/fake-native-bootstrap') is False\n"
        "        return ProviderRequestTransform(user_prompt=event.user_prompt + '::hook')\n",
    )
    provider = _CapturingProvider()
    session = NativeToolReplSession(
        provider=provider,
        tool_registry=production_tool_registry(),
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("hello\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert provider.requests[0].user_prompt == "hello::hook"
    assert [tool.name for tool in provider.requests[0].available_tools] == ["bash"]
    assert any(
        getattr(message, "content", "") == "hello::hook"
        for message in provider.requests[0].messages
    )
    assert not any(
        getattr(message, "content", "") == "hello"
        for message in provider.requests[0].messages
    )


def test_user_bash_hook_synthetic_result_reaches_next_prompt_context(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write_ext(
        tmp_path,
        "shell",
        "from pipy_harness.extensions import UserBashDecision\n"
        "def activate(api):\n"
        "    @api.on('user_bash')\n"
        "    def shell(event, ctx):\n"
        "        return UserBashDecision(result='SYNTHETIC-OUTPUT\\n', "
        "exclude_from_context=False)\n",
    )
    provider = _CapturingProvider()
    err = io.StringIO()
    session = NativeToolReplSession(provider=provider, tool_registry={})

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("!echo real\nask\n"),
        output_stream=io.StringIO(),
        error_stream=err,
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert len(provider.requests) == 1
    messages = " ".join(
        str(getattr(message, "content", "") or getattr(message, "output_text", ""))
        for message in provider.requests[0].messages
    )
    assert "SYNTHETIC-OUTPUT" in messages
    assert "echo real" in messages


def test_session_before_compact_hook_blocks_product_command(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write_ext(
        tmp_path,
        "gate",
        "from pipy_harness.extensions import SessionDecision\n"
        "def activate(api):\n"
        "    @api.on('session_before_compact')\n"
        "    def compact(event, ctx):\n"
        "        return SessionDecision(allow=False, reason='no compact')\n",
    )
    provider = _CapturingProvider()
    err = io.StringIO()
    session = NativeToolReplSession(provider=provider, tool_registry={})

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("/compact\n"),
        output_stream=io.StringIO(),
        error_stream=err,
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert "compact blocked by extension: no compact" in err.getvalue()
