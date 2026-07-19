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
    dispatch_before_provider_headers_hooks,
    dispatch_before_provider_request_hooks,
    dispatch_session_before_hooks,
    dispatch_user_bash_hooks,
)
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.provider import apply_provider_headers
from pipy_harness.native.tool_loop_session import (
    NativeToolReplSession,
    production_tool_registry,
)


class _CapturingProvider:
    name = "stub"
    model_id = "stub-model"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []
        self.headers: list[dict[str, str]] = []

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        self.headers.append(
            apply_provider_headers(
                request,
                {"X-Existing": "base", "X-Remove": "remove-me"},
            )
        )
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


def test_before_provider_headers_dispatch_mutates_in_order_and_fails_soft(
    tmp_path: Path,
) -> None:
    seen: list[str] = []

    def first(event, _ctx):
        assert event.type == "before_provider_headers"
        assert event.headers["X-Existing"] == "base"
        event.headers["X-Added"] = "first"
        seen.append("first")
        return {"ignored": True}

    async def second(event, _ctx):
        assert event.headers["X-Added"] == "first"
        event.headers["X-Added"] = "second"
        event.headers["X-Remove"] = None
        seen.append("second")

    def failing(event, _ctx):
        event.headers["X-Before-Failure"] = "kept"
        seen.append("failing")
        raise RuntimeError("bounded hook failure")

    def last(event, _ctx):
        assert event.headers["X-Before-Failure"] == "kept"
        seen.append("last")

    headers: dict[str, str | None] = {
        "X-Existing": "base",
        "X-Remove": "remove-me",
    }
    dispatch_before_provider_headers_hooks(
        (first, second, failing, last),
        headers,
        cwd=str(tmp_path),
        has_ui=False,
    )

    assert seen == ["first", "second", "failing", "last"]
    assert headers == {
        "X-Existing": "base",
        "X-Remove": None,
        "X-Added": "second",
        "X-Before-Failure": "kept",
    }


def test_apply_provider_headers_copies_and_filters_deletions(tmp_path: Path) -> None:
    original = {"X-Keep": "yes", "X-Delete": "old"}

    def callback(headers):
        headers["X-Delete"] = None
        headers["X-New"] = "new"

    request = ProviderRequest(
        system_prompt="sys",
        user_prompt="user",
        provider_name="stub",
        model_id="stub-model",
        cwd=tmp_path,
        provider_header_callback=callback,
    )

    assert apply_provider_headers(request, original) == {
        "X-Keep": "yes",
        "X-New": "new",
    }
    assert original == {"X-Keep": "yes", "X-Delete": "old"}


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
        message.content.value == "hello::hook"
        for message in provider.requests[0].messages
    )
    assert not any(
        message.content.value == "hello"
        for message in provider.requests[0].messages
    )


def test_before_provider_headers_hook_mutates_product_http_headers(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write_ext(
        tmp_path,
        "headers",
        "def activate(api):\n"
        "    @api.on('before_provider_headers')\n"
        "    def before(event, ctx):\n"
        "        assert event.type == 'before_provider_headers'\n"
        "        assert event.headers['X-Existing'] == 'base'\n"
        "        event.headers['X-Existing'] = 'overridden'\n"
        "        event.headers['X-Remove'] = None\n"
        "        event.headers['X-Session-Id'] = ctx.session_manager.get_session_id()\n",
    )
    provider = _CapturingProvider()
    session = NativeToolReplSession(
        provider=provider,
        tool_registry=production_tool_registry(),
    )
    output = io.StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("hello\n"),
        output_stream=output,
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert len(provider.headers) == 1
    assert provider.headers[0]["X-Existing"] == "overridden"
    assert "X-Remove" not in provider.headers[0]
    assert provider.headers[0]["X-Session-Id"]
    assert "X-Session-Id" not in output.getvalue()


def test_before_provider_headers_hooks_refresh_after_reload(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write_ext(
        tmp_path,
        "reload_headers",
        "from pathlib import Path\n"
        "STATE = Path(__file__).with_name('header-state.txt')\n"
        "VALUE = STATE.read_text(encoding='utf-8') if STATE.exists() else 'before'\n"
        "def activate(api):\n"
        "    @api.on('before_provider_headers')\n"
        "    def before(event, ctx):\n"
        "        event.headers['X-Reloaded'] = VALUE\n"
        "    def flip(ctx, args):\n"
        "        STATE.write_text('after', encoding='utf-8')\n"
        "    api.register_command('flip-header', 'change header generation', flip)\n",
    )
    provider = _CapturingProvider()
    session = NativeToolReplSession(
        provider=provider,
        tool_registry=production_tool_registry(),
    )

    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("first\n/flip-header\n/reload\nsecond\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert [headers["X-Reloaded"] for headers in provider.headers] == [
        "before",
        "after",
    ]


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
        message.content.value for message in provider.requests[0].messages
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


class _FakeUiDriver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.editor_text = "draft"
        self.tools_expanded = False

    def set_status(self, key: str, text: str | None) -> None:
        self.calls.append(("status", (key, text)))

    def set_widget(self, key: str, content: object, placement: str) -> None:
        self.calls.append(("widget", (key, content, placement)))

    def get_editor_text(self) -> str:
        self.calls.append(("get_editor", None))
        return self.editor_text

    def set_editor_text(self, text: str) -> None:
        self.calls.append(("set_editor", text))
        self.editor_text = text

    def paste_to_editor(self, text: str) -> None:
        self.calls.append(("paste", text))
        self.editor_text += text

    def set_tools_expanded(self, expanded: bool) -> None:
        self.calls.append(("set_tools_expanded", expanded))
        self.tools_expanded = bool(expanded)

    def get_tools_expanded(self) -> bool:
        self.calls.append(("get_tools_expanded", None))
        return self.tools_expanded


def test_non_lifecycle_dispatchers_can_paint_live_ui_driver(tmp_path: Path) -> None:
    from pipy_harness.native.extension_runtime import (
        BeforeAgentStartResult,
        InputTransform,
        ToolBlock,
        ToolResultTransform,
        dispatch_before_agent_start_hooks,
        dispatch_input_hooks,
        dispatch_tool_call_hooks,
        dispatch_tool_result_hooks,
    )

    driver = _FakeUiDriver()

    text = dispatch_input_hooks(
        (
            lambda event, ctx: (
                ctx.ui.set_status("input", event.text),
                ctx.ui.set_editor_text(event.text + "!"),
                InputTransform(text=ctx.ui.get_editor_text()),
            )[-1],
        ),
        "hello",
        cwd=str(tmp_path),
        has_ui=True,
        ui_driver=driver,  # type: ignore[arg-type]
    )
    before = dispatch_before_agent_start_hooks(
        (
            lambda event, ctx: (
                ctx.ui.set_widget(
                    "before", event.system_prompt, placement="below_editor"
                ),
                BeforeAgentStartResult(append_system_prompt="extra"),
            )[-1],
        ),
        cwd=str(tmp_path),
        has_ui=True,
        system_prompt="sys",
        ui_driver=driver,  # type: ignore[arg-type]
    )
    block = dispatch_tool_call_hooks(
        (
            lambda event, ctx: (
                ctx.ui.set_status("tool-call", event.tool_name),
                ToolBlock(reason="blocked"),
            )[-1],
        ),
        tool_name="bash",
        tool_input={},
        cwd=str(tmp_path),
        has_ui=True,
        ui_driver=driver,  # type: ignore[arg-type]
    )
    result = dispatch_tool_result_hooks(
        (
            lambda event, ctx: (
                ctx.ui.set_tools_expanded(True),
                ToolResultTransform(content=event.content + "::result"),
            )[-1],
        ),
        tool_name="bash",
        content="out",
        is_error=False,
        cwd=str(tmp_path),
        has_ui=True,
        ui_driver=driver,  # type: ignore[arg-type]
    )

    assert text == "hello!"
    assert before.append_system_prompt == "extra"
    assert isinstance(block, ToolBlock)
    assert result == "out::result"
    assert ("status", ("input", "hello")) in driver.calls
    assert ("set_editor", "hello!") in driver.calls
    assert ("widget", ("before", "sys", "below_editor")) in driver.calls
    assert ("status", ("tool-call", "bash")) in driver.calls
    assert ("set_tools_expanded", True) in driver.calls


def test_live_session_gate_dispatchers_can_paint_live_ui_driver(tmp_path: Path) -> None:
    driver = _FakeUiDriver()

    def user_bash(event, ctx):
        ctx.ui.paste_to_editor(" bash")
        return UserBashDecision(command=event.command + " --safe")

    def before_provider(event, ctx):
        ctx.ui.set_widget("provider", event.user_prompt, placement="above_editor")
        return ProviderRequestTransform(user_prompt=event.user_prompt + "::provider")

    def session_before(event, ctx):
        ctx.ui.set_status("session", event.operation)
        return SessionDecision(allow=False, reason=ctx.ui.get_editor_text())

    bash_decision = dispatch_user_bash_hooks(
        (user_bash,),
        command="echo ok",
        exclude_from_context=False,
        cwd=str(tmp_path),
        has_ui=True,
        ui_driver=driver,  # type: ignore[arg-type]
    )
    request = ProviderRequest(
        system_prompt="sys",
        user_prompt="prompt",
        provider_name="stub",
        model_id="model",
        cwd=tmp_path,
        available_tools=(),
    )
    provider_transform = dispatch_before_provider_request_hooks(
        (before_provider,),
        request,
        cwd=str(tmp_path),
        has_ui=True,
        ui_driver=driver,  # type: ignore[arg-type]
    )
    session_decision = dispatch_session_before_hooks(
        (session_before,),
        operation="compact",
        cwd=str(tmp_path),
        has_ui=True,
        ui_driver=driver,  # type: ignore[arg-type]
    )

    assert bash_decision.allowed
    assert bash_decision.command == "echo ok --safe"
    assert provider_transform.user_prompt == "prompt::provider"
    assert not session_decision.allow
    assert session_decision.reason == "draft bash"
    assert ("paste", " bash") in driver.calls
    assert ("widget", ("provider", "prompt", "above_editor")) in driver.calls
    assert ("status", ("session", "compact")) in driver.calls


def test_non_lifecycle_ui_driver_is_noop_when_headless(tmp_path: Path) -> None:
    driver = _FakeUiDriver()

    def hook(_event, ctx):
        ctx.ui.set_status("input", "ignored")
        return None

    assert dispatch_user_bash_hooks(
        (
            lambda event, ctx: (
                ctx.ui.set_status("bash", event.command),
                UserBashDecision(),
            )[-1],
        ),
        command="echo ok",
        exclude_from_context=False,
        cwd=str(tmp_path),
        has_ui=False,
        ui_driver=driver,  # type: ignore[arg-type]
    ).allowed
    assert dispatch_before_provider_request_hooks(
        (hook,),
        ProviderRequest(
            system_prompt="sys",
            user_prompt="prompt",
            provider_name="stub",
            model_id="model",
            cwd=tmp_path,
            available_tools=(),
        ),
        cwd=str(tmp_path),
        has_ui=False,
        ui_driver=driver,  # type: ignore[arg-type]
    ).user_prompt == "prompt"
    assert driver.calls == []
