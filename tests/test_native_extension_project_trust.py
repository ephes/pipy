from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pipy_harness.cli import _build_extension_activation_batch
from pipy_harness.native.extension_hooks import (
    dispatch_before_provider_request_hooks,
    dispatch_input_hooks,
    dispatch_lifecycle_hooks,
    dispatch_project_trust_hooks,
    dispatch_session_before_hooks,
    dispatch_tool_call_hooks,
)
from pipy_harness.native.extension_runtime import (
    REASON_RESERVED_TOOL,
    LifecycleEvent,
    activate_extension_batch,
    activate_extensions,
    dispatch_extension_command,
    dispatch_extension_shortcut,
    drain_user_messages,
    extension_command_map,
    extension_shortcuts,
    make_extension_context,
)
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.settings import SettingsManager


def _write_extension(root: Path, name: str, source: str) -> None:
    path = root / ".pipy" / "extensions" / f"{name}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _activate(root: Path):
    return activate_extensions(
        discover_extensions(root, include_workspace_defaults=True)
    )


def test_project_trust_handlers_are_serial_first_decision_owners(
    tmp_path: Path,
) -> None:
    proof = tmp_path / "proof.txt"
    _write_extension(
        tmp_path,
        "a",
        "def activate(api):\n"
        "    @api.on('project_trust')\n"
        "    def decide(event, ctx):\n"
        f"        open({str(proof)!r}, 'a').write('a')\n"
        "        assert event.type == 'project_trust'\n"
        "        assert event.cwd == ctx.cwd\n"
        "        return {'trusted': 'undecided', 'remember': True}\n",
    )
    _write_extension(
        tmp_path,
        "b",
        "def activate(api):\n"
        "    @api.on('project_trust')\n"
        "    async def decide(event, ctx):\n"
        f"        open({str(proof)!r}, 'a').write('b')\n"
        "        return {'trusted': 'yes', 'remember': True}\n",
    )
    _write_extension(
        tmp_path,
        "c",
        "def activate(api):\n"
        "    @api.on('project_trust')\n"
        "    def decide(event, ctx):\n"
        f"        open({str(proof)!r}, 'a').write('c')\n"
        "        return {'trusted': 'no'}\n",
    )

    result = dispatch_project_trust_hooks(
        _activate(tmp_path),
        cwd=str(tmp_path.resolve()),
        mode="print",
        has_ui=False,
    )

    assert result.trusted == "yes"
    assert result.remember is True
    assert result.errors == ()
    assert proof.read_text() == "ab"


def test_project_trust_errors_continue_and_headless_ui_is_inert(tmp_path: Path) -> None:
    notices: list[tuple[str, str]] = []
    _write_extension(
        tmp_path,
        "a",
        "def activate(api):\n"
        "    @api.on('project_trust')\n"
        "    def decide(event, ctx):\n"
        "        assert ctx.mode == 'json'\n"
        "        assert ctx.has_ui is False and ctx.hasUI is False\n"
        "        assert ctx.ui.select('pick', ['x']) is None\n"
        "        assert ctx.ui.confirm('confirm', 'message') is False\n"
        "        assert ctx.ui.input('input') is None\n"
        "        ctx.ui.notify('headless notice', 'warning')\n"
        "        raise RuntimeError('secret body')\n",
    )
    _write_extension(
        tmp_path,
        "b",
        "def activate(api):\n"
        "    api.on('project_trust', lambda event, ctx: {'trusted': 'maybe'})\n",
    )
    _write_extension(
        tmp_path,
        "c",
        "def activate(api):\n"
        "    api.on('project_trust', lambda event, ctx: {'trusted': 'no', 'remember': 1})\n",
    )

    result = dispatch_project_trust_hooks(
        _activate(tmp_path),
        cwd=str(tmp_path.resolve()),
        mode="json",
        has_ui=False,
        notify_sink=lambda kind, text: notices.append((kind, text)),
    )

    assert result.trusted == "no"
    assert result.remember is False
    assert [error.extension for error in result.errors] == [
        ".pipy/extensions/a.py",
        ".pipy/extensions/b.py",
    ]
    assert [error.error for error in result.errors] == ["RuntimeError", "ValueError"]
    assert notices == [("warning", "headless notice")]


def test_pending_activation_is_reused_and_finalized_in_final_order(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    config = tmp_path / "config"
    cli_path = tmp_path / "cli.py"
    proof = tmp_path / "imports.txt"

    def source(mark: str, command: str) -> str:
        return (
            f"open({str(proof)!r}, 'a').write({mark!r})\n"
            "def activate(api):\n"
            f"    api.register_command({command!r}, 'test', lambda ctx, args: None)\n"
            f"    api.send_user_message({mark!r})\n"
            f"    api.send_message({{'customType': 'note', 'content': {mark!r}}})\n"
        )

    cli_path.write_text(source("c", "cli"), encoding="utf-8")
    global_path = config / "extensions" / "global.py"
    global_path.parent.mkdir(parents=True)
    global_path.write_text(source("g", "shared"), encoding="utf-8")
    _write_extension(workspace, "project", source("p", "shared"))
    env = {"PIPY_CONFIG_HOME": str(config)}

    pending_descriptors = discover_extensions(
        workspace,
        config_home_env=env,
        explicit_paths=(cli_path,),
        include_workspace_defaults=False,
    )
    pending = activate_extension_batch(pending_descriptors, pending=True)
    assert proof.read_text() == "cg"
    assert pending.message_outbox == []

    final_descriptors = discover_extensions(
        workspace,
        config_home_env=env,
        explicit_paths=(cli_path,),
        include_workspace_defaults=True,
    )
    final = activate_extension_batch(final_descriptors, preloaded=pending)

    assert proof.read_text() == "cgp"
    relevant = [item for item in final.activated if item.name != "__pycache__"]
    assert [item.name for item in relevant] == ["cli", "project", "global"]
    assert [item.status for item in relevant] == [
        "activated",
        "activated",
        "disabled",
    ]
    assert relevant[-1].reason == "duplicate_command"
    assert [
        message.content for message in drain_user_messages(final.message_outbox)
    ] == [
        "c",
        "p",
    ]
    assert [
        message.content
        for extension in relevant
        if extension.status == "activated"
        for message in extension.custom_messages
    ] == ["c", "p"]


def test_pending_cross_extension_collisions_are_resolved_only_in_final_order(
    tmp_path: Path,
) -> None:
    _write_extension(
        tmp_path,
        "a",
        "from pipy_harness.extensions import ExtensionTool, ToolResult\n"
        "def activate(api):\n"
        "    api.register_command('shared', 'a', lambda ctx, args: None)\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='session_only',\n"
        "        description='reserved only by the final session registry',\n"
        "        input_schema={'type': 'object'},\n"
        "        handler=lambda ctx, params: ToolResult(content='a'),\n"
        "    ))\n",
    )
    _write_extension(
        tmp_path,
        "b",
        "def activate(api):\n"
        "    api.register_command('shared', 'b', lambda ctx, args: None)\n",
    )
    descriptors = discover_extensions(tmp_path, include_workspace_defaults=True)

    pending = activate_extension_batch(descriptors, pending=True)
    relevant_pending = [item for item in pending.activated if item.name in {"a", "b"}]
    assert [item.status for item in relevant_pending] == ["activated", "activated"]

    final = activate_extension_batch(
        descriptors,
        preloaded=pending,
        reserved_tool_names=("session_only",),
    )
    ordinary = activate_extension_batch(
        descriptors,
        reserved_tool_names=("session_only",),
    )
    relevant_final = [item for item in final.activated if item.name in {"a", "b"}]
    relevant_ordinary = [item for item in ordinary.activated if item.name in {"a", "b"}]

    assert [(item.status, item.reason) for item in relevant_final] == [
        ("disabled", REASON_RESERVED_TOOL),
        ("activated", None),
    ]
    assert [(item.status, item.reason) for item in relevant_final] == [
        (item.status, item.reason) for item in relevant_ordinary
    ]
    assert [command.name for command in relevant_final[1].commands] == ["shared"]


def test_pending_entry_renderer_collisions_finalize_in_order(tmp_path: Path) -> None:
    body = (
        "def activate(api):\n"
        "    api.register_entry_renderer('card', lambda entry, ctx: None)\n"
    )
    _write_extension(tmp_path, "a", body)
    _write_extension(tmp_path, "b", body)
    descriptors = discover_extensions(tmp_path, include_workspace_defaults=True)

    pending = activate_extension_batch(descriptors, pending=True)
    relevant_pending = [item for item in pending.activated if item.name in {"a", "b"}]
    final = activate_extension_batch(descriptors, preloaded=pending)
    relevant_final = [item for item in final.activated if item.name in {"a", "b"}]

    assert [item.status for item in relevant_pending] == ["activated", "activated"]
    assert [(item.status, item.reason) for item in relevant_final] == [
        ("activated", None),
        ("disabled", "duplicate_entry_renderer"),
    ]


def test_failed_pretrust_activation_is_not_retried(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    config = tmp_path / "config"
    proof = tmp_path / "imports.txt"
    path = config / "extensions" / "broken.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"open({str(proof)!r}, 'a').write('x')\nraise RuntimeError('boom')\n",
        encoding="utf-8",
    )
    env = {"PIPY_CONFIG_HOME": str(config)}
    descriptors = discover_extensions(workspace, config_home_env=env)
    pending = activate_extension_batch(descriptors, pending=True)
    final = activate_extension_batch(descriptors, preloaded=pending)
    assert proof.read_text() == "x"
    assert final.activated[0].status == "disabled"
    assert final.activated[0].reason == "import_error"


def test_final_startup_batch_reserves_actual_session_tool_names(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    extension = tmp_path / "session_tool.py"
    extension.write_text(
        "from pipy_harness.extensions import ExtensionTool, ToolResult\n"
        "def activate(api):\n"
        "    api.register_tool(ExtensionTool(\n"
        "        name='session_only',\n"
        "        description='must not shadow the session registry',\n"
        "        input_schema={'type': 'object'},\n"
        "        handler=lambda ctx, params: ToolResult(content='shadowed'),\n"
        "    ))\n",
        encoding="utf-8",
    )
    settings = SettingsManager(
        global_path=tmp_path / "settings.json",
        project_trusted=True,
    )

    batch = _build_extension_activation_batch(
        workspace,
        settings_manager=settings,
        resource_options=RuntimeResourceOptions(
            extension_paths=(extension,),
            no_extensions=True,
        ),
        reserved_tool_names=("session_only",),
    )

    activated = next(item for item in batch.activated if item.name == "session_tool")
    assert activated.status == "disabled"
    assert activated.reason == REASON_RESERVED_TOOL


def _assert_trusted_context(ctx: object) -> None:
    snake = getattr(ctx, "is_project_trusted")
    camel = getattr(ctx, "isProjectTrusted")
    assert snake() is True
    assert camel() is True
    with pytest.raises(TypeError):
        snake(True)
    with pytest.raises(TypeError):
        camel(True)


def test_normal_extension_context_families_expose_zero_arg_trust_reads(
    tmp_path: Path,
) -> None:
    _write_extension(
        tmp_path,
        "reads",
        "def activate(api):\n"
        "    api.register_command('reads', 'reads', lambda ctx, args: "
        "(_ for _ in ()).throw(RuntimeError('untrusted')) "
        "if not (ctx.is_project_trusted() and ctx.isProjectTrusted()) else None)\n"
        "    api.register_shortcut('ctrl-r', lambda ctx, args: "
        "(_ for _ in ()).throw(RuntimeError('untrusted')) "
        "if not (ctx.is_project_trusted() and ctx.isProjectTrusted()) else None)\n",
    )
    activated = _activate(tmp_path)
    command = dispatch_extension_command(
        "/reads",
        extension_command_map(activated),
        cwd=str(tmp_path),
        has_ui=False,
        project_trusted=True,
    )
    shortcut = dispatch_extension_shortcut(
        "ctrl-r",
        extension_shortcuts(activated),
        cwd=str(tmp_path),
        has_ui=False,
        project_trusted=True,
    )
    assert command is not None and command.ran
    assert shortcut is not None and shortcut.ran

    seen: list[str] = []

    def observe(name: str):
        def handler(_event: object, ctx: object) -> None:
            _assert_trusted_context(ctx)
            seen.append(name)

        return handler

    dispatch_input_hooks(
        [observe("input")],
        "text",
        cwd=str(tmp_path),
        has_ui=False,
        project_trusted=True,
    )
    dispatch_lifecycle_hooks(
        [observe("lifecycle")],
        LifecycleEvent("session_start"),
        cwd=str(tmp_path),
        has_ui=False,
        project_trusted=True,
    )
    dispatch_tool_call_hooks(
        [observe("tool")],
        tool_name="read",
        tool_input={},
        cwd=str(tmp_path),
        has_ui=False,
        project_trusted=True,
    )
    dispatch_before_provider_request_hooks(
        [observe("provider")],
        SimpleNamespace(
            system_prompt="system",
            user_prompt="user",
            available_tools=(),
            messages=(),
            provider_name="fake",
            model_id="fake",
        ),
        cwd=str(tmp_path),
        has_ui=False,
        project_trusted=True,
    )
    dispatch_session_before_hooks(
        [observe("session")],
        operation="switch",
        cwd=str(tmp_path),
        has_ui=False,
        project_trusted=True,
    )
    tool_context = make_extension_context(str(tmp_path), False, project_trusted=True)
    _assert_trusted_context(tool_context)
    seen.append("tool-handler")
    assert seen == [
        "input",
        "lifecycle",
        "tool",
        "provider",
        "session",
        "tool-handler",
    ]
