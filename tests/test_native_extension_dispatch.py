"""Slice 3 tests for extension command dispatch.

Slice 3 runs an activated extension's `/<command>` locally: it resolves
the command, invokes the handler with a mode-aware context and the raw
argument string, captures any `ctx.ui.notify` output, and runs **no
provider turn**. A handler exception is bounded into a safe error
without crashing the loop. Unknown / non-slash input is not an extension
command (the caller falls through to its normal handling).
"""

from __future__ import annotations

import gc
import warnings
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest, ProviderResult
from pipy_harness.native.extension_runtime import (
    activate_extensions,
    dispatch_extension_command,
    extension_command_map,
    extension_flags,
    extension_message_renderers,
    parse_extension_flag_tokens,
    render_extension_message,
    safe_custom_entry_data,
)
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.session_tree import CustomEntry, NativeSessionTree
from pipy_harness.native.tool_loop_session import NativeToolReplSession


@dataclass
class _CapturingProvider:
    requests: list[ProviderRequest] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "capturing-fake"

    @property
    def model_id(self) -> str:
        return "capturing-model"

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
            final_text="OK",
            usage=None,
            metadata=None,
            tool_calls=(),
        )


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


def _ext_dir(workspace: Path) -> Path:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write(workspace: Path, name: str, body: str) -> None:
    (_ext_dir(workspace) / f"{name}.py").write_text(body, encoding="utf-8")


def _activate(workspace: Path):
    descriptors = discover_extensions(workspace, config_home_env={}, home_dir=workspace)
    return activate_extensions(descriptors)


def _command_map(workspace: Path) -> dict:
    return extension_command_map(_activate(workspace))


def test_dispatch_get_flag_sees_parsed_runtime_overrides(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "flagger",
        "from pipy_harness.extensions import ExtensionFlag\n"
        "def activate(api):\n"
        "    api.register_flag(ExtensionFlag('plan', 'boolean', default=False))\n"
        "    api.register_flag(ExtensionFlag('ticket', 'string', default='default'))\n"
        "    def show(ctx, args):\n"
        "        ctx.ui.notify(f\"api={api.get_flag('plan')}/{api.get_flag('ticket')} ctx={ctx.flags['plan']}/{ctx.flags['ticket']}\")\n"
        "    api.register_command('show-flags', 'show flags', show)\n",
    )
    activated = _activate(workspace)
    flags, error = parse_extension_flag_tokens(
        extension_flags(activated), ("--plan", "--ticket", "PIPY-123")
    )
    assert error is None

    dispatch = dispatch_extension_command(
        "/show-flags",
        extension_command_map(activated),
        cwd=str(workspace),
        has_ui=True,
        flags=flags,
    )

    assert dispatch is not None
    assert dispatch.messages == (("info", "api=True/PIPY-123 ctx=True/PIPY-123"),)


def test_dispatch_runs_handler_and_captures_notify(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "statusext",
        "def activate(api):\n"
        "    def status(ctx, args):\n"
        "        ctx.ui.notify('extension status: ok')\n"
        "    api.register_command('ext-status', 'Show status', status)\n",
    )
    command_map = _command_map(workspace)

    dispatch = dispatch_extension_command(
        "/ext-status", command_map, cwd=str(workspace), has_ui=True
    )

    assert dispatch is not None
    assert dispatch.name == "ext-status"
    assert dispatch.ran is True
    assert dispatch.error is None
    assert dispatch.messages == (("info", "extension status: ok"),)


def test_dispatch_passes_raw_args(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "echoext",
        "def activate(api):\n"
        "    def echo(ctx, args):\n"
        "        ctx.ui.notify('got:' + args)\n"
        "    api.register_command('echo', 'echo', echo)\n",
    )
    command_map = _command_map(workspace)

    dispatch = dispatch_extension_command(
        "/echo hello there", command_map, cwd=str(workspace), has_ui=True
    )

    assert dispatch is not None
    assert dispatch.messages == (("info", "got:hello there"),)

    # Raw args are preserved verbatim after the first delimiter space,
    # including intentional extra whitespace.
    spaced = dispatch_extension_command(
        "/echo  keep  spaces ", command_map, cwd=str(workspace), has_ui=True
    )
    assert spaced is not None
    assert spaced.messages == (("info", "got: keep  spaces "),)


def test_dispatch_exposes_extension_flags_to_command(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "flagcmd",
        "def activate(api):\n"
        "    def show(ctx, args):\n"
        "        ctx.ui.notify(str(ctx.flags.get('plan')) + ':' + ctx.flags.get('ticket', ''))\n"
        "    api.register_command('show-flags', 'show flags', show)\n",
    )
    command_map = _command_map(workspace)

    dispatch = dispatch_extension_command(
        "/show-flags",
        command_map,
        cwd=str(workspace),
        has_ui=True,
        flags={"plan": True, "ticket": "PIPY-123"},
    )

    assert dispatch is not None
    assert dispatch.ran is True
    assert dispatch.messages == (("info", "True:PIPY-123"),)


class _FakeEditorUiDriver:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.pasted: list[str] = []

    def select(self, title: str, options: object) -> str | None:
        return None

    def input(self, title: str, placeholder: str | None = None) -> str | None:
        return None

    def editor(self, title: str, prefill: str | None = None) -> str | None:
        return None

    def confirm(self, title: str, message: str) -> bool:
        return False

    def set_status(self, key: str, text: str | None) -> None:
        pass

    def set_working_message(self, message: str | None = None) -> None:
        pass

    def set_working_visible(self, visible: bool) -> None:
        pass

    def set_widget(self, key: str, content: object, placement: str) -> None:
        pass

    def set_header(self, factory: object | None) -> None:
        pass

    def set_footer(self, factory: object | None) -> None:
        pass

    def set_title(self, title: str) -> None:
        pass

    def set_working_indicator(self, frames: object, interval_ms: object) -> None:
        pass

    def get_editor_text(self) -> str:
        return self.text

    def set_editor_text(self, text: str) -> None:
        self.text = text

    def paste_to_editor(self, text: str) -> None:
        self.pasted.append(text)
        self.text = text

    def apply_theme(self, name: str) -> tuple[bool, str | None]:
        return False, "not implemented"


def test_dispatch_exposes_editor_text_helpers_to_live_ui(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "editui",
        "def activate(api):\n"
        "    def edit(ctx, args):\n"
        "        ctx.ui.notify(ctx.ui.get_editor_text())\n"
        "        ctx.ui.set_editor_text('set:' + args)\n"
        "        ctx.ui.paste_to_editor('paste:' + args)\n"
        "    api.register_command('edit-ui', 'edit ui', edit)\n",
    )
    driver = _FakeEditorUiDriver("draft")

    dispatch = dispatch_extension_command(
        "/edit-ui hello",
        _command_map(workspace),
        cwd=str(workspace),
        has_ui=True,
        ui_driver=driver,
    )

    assert dispatch is not None
    assert dispatch.ran is True
    assert dispatch.messages == (("info", "draft"),)
    assert driver.pasted == ["paste:hello"]
    assert driver.text == "paste:hello"


def test_dispatch_editor_text_helpers_are_headless_noops(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "editui",
        "def activate(api):\n"
        "    def edit(ctx, args):\n"
        "        ctx.ui.notify('text=' + ctx.ui.get_editor_text())\n"
        "        ctx.ui.set_editor_text('ignored')\n"
        "        ctx.ui.paste_to_editor('ignored')\n"
        "    api.register_command('edit-ui', 'edit ui', edit)\n",
    )
    driver = _FakeEditorUiDriver("draft")

    dispatch = dispatch_extension_command(
        "/edit-ui hello",
        _command_map(workspace),
        cwd=str(workspace),
        has_ui=False,
        ui_driver=driver,
    )

    assert dispatch is not None
    assert dispatch.ran is True
    assert dispatch.messages == (("info", "text="),)
    assert driver.text == "draft"
    assert driver.pasted == []


def test_dispatch_exposes_append_entry_capability(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "entryext",
        "def activate(api):\n"
        "    def save(ctx, args):\n"
        "        ctx.append_entry('note', {'args': args})\n"
        "    api.register_command('save-note', 'save note', save)\n",
    )
    entries: list[tuple[str, object | None]] = []

    def append_entry(custom_type: str, data: object | None = None) -> object:
        entries.append((custom_type, data))
        return "entry-id"

    dispatch = dispatch_extension_command(
        "/save-note hello",
        _command_map(workspace),
        cwd=str(workspace),
        has_ui=True,
        append_entry_fn=append_entry,
    )

    assert dispatch is not None
    assert dispatch.ran is True
    assert entries == [("note", {"args": "hello"})]


def test_append_entry_rejects_invalid_custom_type(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "entryext",
        "def activate(api):\n"
        "    def save(ctx, args):\n"
        "        ctx.append_entry('bad/type', {'args': args})\n"
        "    api.register_command('save-note', 'save note', save)\n",
    )
    entries: list[tuple[str, object | None]] = []

    def append_entry(custom_type: str, data: object | None = None) -> object:
        entries.append((custom_type, data))
        return "entry-id"

    dispatch = dispatch_extension_command(
        "/save-note hello",
        _command_map(workspace),
        cwd=str(workspace),
        has_ui=True,
        append_entry_fn=append_entry,
    )

    assert dispatch is not None
    assert dispatch.ran is False
    assert dispatch.error == "ValueError"
    assert not entries


def test_message_renderer_gets_json_safe_copy(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "mutating",
        "def activate(api):\n"
        "    def render(data):\n"
        "        data['text'] = 'mutated'\n"
        "        return 'done'\n"
        "    api.register_message_renderer('note', render)\n",
    )
    descriptors = discover_extensions(workspace, config_home_env={}, home_dir=workspace)
    renderers = extension_message_renderers(activate_extensions(descriptors))
    payload = {"text": "original"}

    assert render_extension_message(renderers, "note", payload).lines == ("done",)
    assert payload == {"text": "original"}


def test_message_renderer_output_coercion_fails_soft(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "bad_output",
        "class Bad:\n"
        "    def __str__(self):\n"
        "        raise RuntimeError('secret')\n"
        "def activate(api):\n"
        "    api.register_message_renderer('note', lambda data: [Bad()])\n",
    )
    descriptors = discover_extensions(workspace, config_home_env={}, home_dir=workspace)
    renderers = extension_message_renderers(activate_extensions(descriptors))

    rendered = render_extension_message(renderers, "note", {"text": "hello"}).lines

    assert rendered == ("render error: RuntimeError",)


def test_async_message_renderer_fails_soft_without_unawaited_warning(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "async_render",
        "def activate(api):\n"
        "    async def render(data):\n"
        "        return 'async'\n"
        "    api.register_message_renderer('note', render)\n",
    )
    descriptors = discover_extensions(workspace, config_home_env={}, home_dir=workspace)
    renderers = extension_message_renderers(activate_extensions(descriptors))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rendered = render_extension_message(renderers, "note", {"text": "hello"}).lines
        gc.collect()

    assert rendered == ("render error: unsupported awaitable",)
    assert not [
        warning for warning in caught if "never awaited" in str(warning.message)
    ]


def test_message_renderer_fails_soft_and_data_is_json_safe(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "renderext",
        "def activate(api):\n"
        "    api.register_message_renderer('note', lambda data: ['Note', data['text']])\n"
        "    api.register_message_renderer('boom', lambda data: (_ for _ in ()).throw(RuntimeError('secret')))\n",
    )
    descriptors = discover_extensions(workspace, config_home_env={}, home_dir=workspace)
    activated = activate_extensions(descriptors)
    renderers = extension_message_renderers(activated)

    safe = safe_custom_entry_data({"text": "hello", "raw": object()})

    assert isinstance(safe, str)
    ordered = safe_custom_entry_data({"z": 1, "a": 2})
    assert isinstance(ordered, dict)
    assert list(ordered) == ["z", "a"]
    assert isinstance(safe_custom_entry_data({"v": float("inf")}), str)
    assert render_extension_message(renderers, "note", {"text": "hello"}).lines == (
        "Note",
        "hello",
    )
    assert render_extension_message(renderers, "boom", {}).lines == (
        "render error: RuntimeError",
    )


def test_unknown_command_is_not_dispatched(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "anyext",
        "def activate(api):\n"
        "    api.register_command('known', 'k', lambda ctx, args: None)\n",
    )
    command_map = _command_map(workspace)

    assert (
        dispatch_extension_command(
            "/unknown", command_map, cwd=str(workspace), has_ui=True
        )
        is None
    )


def test_non_slash_input_is_not_dispatched(tmp_path: Path) -> None:
    assert dispatch_extension_command("hello", {}, cwd="/tmp", has_ui=True) is None


def test_handler_exception_is_bounded(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "crashext",
        "def activate(api):\n"
        "    def boom(ctx, args):\n"
        "        raise RuntimeError('/Users/x/leak-tok-123')\n"
        "    api.register_command('boom', 'b', boom)\n",
    )
    command_map = _command_map(workspace)

    dispatch = dispatch_extension_command(
        "/boom", command_map, cwd=str(workspace), has_ui=True
    )

    assert dispatch is not None
    assert dispatch.ran is False
    assert dispatch.error is not None
    # Bounded, no raw message leak.
    assert "/Users/x" not in dispatch.error
    assert "leak-tok-123" not in dispatch.error


def test_extension_custom_entry_runs_through_the_session(tmp_path, monkeypatch) -> None:
    # Product path: a command can append a custom native-session entry and render
    # it through the extension's renderer with no provider turn.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    ext = tmp_path / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "card.py").write_text(
        "def activate(api):\n"
        "    api.register_message_renderer('card', lambda data: ['Card title: ' + data['title']])\n"
        "    def card(ctx, args):\n"
        "        entry_id = ctx.append_entry('card', {'title': args or 'untitled'})\n"
        "        ctx.ui.notify('ENTRY_ID:' + str(entry_id))\n"
        "    api.register_command('card', 'add card', card)\n",
        encoding="utf-8",
    )
    provider = _CapturingProvider()
    native_session = NativeSessionTree.create(tmp_path, persist=False)
    session = NativeToolReplSession(
        provider=provider,
        tool_registry={},
        native_session=native_session,
    )
    error_stream = StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=StringIO("/card hello\n"),
        output_stream=StringIO(),
        error_stream=error_stream,
    )

    assert "Card title: hello" in error_stream.getvalue()
    assert "ENTRY_ID:" in error_stream.getvalue()
    assert not provider.requests
    custom_entries = [e for e in native_session.entries if isinstance(e, CustomEntry)]
    assert len(custom_entries) == 1
    assert custom_entries[0].custom_type == "card"
    assert custom_entries[0].data == {"title": "hello"}
    assert result.user_turn_count == 0


def test_extension_command_runs_through_the_session(tmp_path, monkeypatch) -> None:
    # Product path: an activated extension /command runs in the real
    # tool-loop session, emits its notify output, and triggers NO
    # provider turn; a plain prompt still reaches the provider.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    ext = tmp_path / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "sayhi.py").write_text(
        "def activate(api):\n"
        "    def hi(ctx, args):\n"
        "        ctx.ui.notify('EXTENSION_RAN:' + args)\n"
        "    api.register_command('sayhi', 'say hi', hi)\n",
        encoding="utf-8",
    )
    provider = _CapturingProvider()
    session = NativeToolReplSession(provider=provider, tool_registry={})
    error_stream = StringIO()

    result = session.run(
        workspace_root=tmp_path,
        input_stream=StringIO("/sayhi there\nplain prompt\n"),
        output_stream=StringIO(),
        error_stream=error_stream,
    )

    assert "EXTENSION_RAN:there" in error_stream.getvalue()
    # Only the genuine prompt hit the provider; the extension command did not.
    assert len(provider.requests) == 1
    assert "plain prompt" in provider.requests[0].user_prompt
    assert result.user_turn_count == 1


def test_builtin_is_not_shadowed_by_extension(tmp_path, monkeypatch) -> None:
    # An extension that registers a built-in name is disabled at
    # activation (reserved), so the built-in keeps working and the
    # extension command never dispatches.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    ext = tmp_path / ".pipy" / "extensions"
    ext.mkdir(parents=True)
    (ext / "shadow.py").write_text(
        "def activate(api):\n"
        "    api.register_command('help', 'x', lambda ctx, args: None)\n",
        encoding="utf-8",
    )
    descriptors = discover_extensions(tmp_path, config_home_env={}, home_dir=tmp_path)
    # Reserved against the built-in 'help'.
    activated = activate_extensions(descriptors, reserved_command_names=("help",))
    command_map = extension_command_map(activated)

    assert "help" not in command_map


def test_keyboard_interrupt_propagates(tmp_path: Path) -> None:
    # A user abort (Ctrl-C) during a handler must not be swallowed into a
    # bounded extension failure.
    import pytest

    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "interruptext",
        "def activate(api):\n"
        "    def cmd(ctx, args):\n"
        "        raise KeyboardInterrupt()\n"
        "    api.register_command('intr', 'i', cmd)\n",
    )
    command_map = _command_map(workspace)

    with pytest.raises(KeyboardInterrupt):
        dispatch_extension_command(
            "/intr", command_map, cwd=str(workspace), has_ui=True
        )


def test_non_interactive_notify_degrades(tmp_path: Path) -> None:
    # With has_ui=False, ctx.has_ui is False and notify still records a
    # message deterministically (no blocking, no crash).
    workspace = _make_workspace(tmp_path)
    _write(
        workspace,
        "uiext",
        "def activate(api):\n"
        "    def cmd(ctx, args):\n"
        "        ctx.ui.notify('hi' if ctx.has_ui else 'noui')\n"
        "    api.register_command('uic', 'u', cmd)\n",
    )
    command_map = _command_map(workspace)

    dispatch = dispatch_extension_command(
        "/uic", command_map, cwd=str(workspace), has_ui=False
    )

    assert dispatch is not None
    assert dispatch.ran is True
    assert dispatch.messages == (("info", "noui"),)


def test_command_context_exposes_read_only_session_manager(tmp_path):
    workspace = _make_workspace(tmp_path)
    captured = {}

    def handler(ctx, args):
        session = ctx.session_manager
        alias = ctx.sessionManager
        captured["same"] = alias is session
        captured["cwd"] = session.get_cwd()
        captured["session_id"] = session.get_session_id()
        captured["session_file"] = session.get_session_file()
        captured["session_dir"] = session.get_session_dir()
        captured["name"] = session.get_session_name()
        captured["leaf_id"] = session.get_leaf_id()
        captured["leaf_entry_type"] = session.get_leaf_entry().type
        captured["entries"] = [entry.type for entry in session.get_entries()]
        captured["branch"] = [entry.type for entry in session.get_branch()]
        captured["label"] = session.get_label(session.get_entries()[0].id)
        captured["tree_label"] = session.get_tree()[0].label
        captured["header"] = session.get_header().to_dict()
        captured["entry_dict"] = session.get_leaf_entry().to_dict()
        captured["has_mutation"] = hasattr(session, "append_custom")

    tree = NativeSessionTree.create(workspace, session_dir=tmp_path / "sessions")
    first = tree.append_custom("note", {"body": "hello"})
    tree.append_label_change(first.id, "bookmark")
    tree.append_session_info("demo")
    command = type("C", (), {})()
    command.handler = handler
    command_map = {"inspect": command}

    dispatch = dispatch_extension_command(
        "/inspect", command_map, cwd=str(workspace), has_ui=True, session_tree=tree
    )

    assert dispatch is not None
    assert dispatch.ran is True
    assert captured["same"] is True
    assert captured["cwd"] == str(workspace)
    assert captured["session_id"] == tree.session_id
    assert captured["session_file"] == str(tree.path)
    assert captured["session_dir"] == str(tree.path.parent)
    assert captured["name"] == "demo"
    assert captured["leaf_id"] == tree.get_leaf_id()
    assert captured["leaf_entry_type"] == "session_info"
    assert captured["entries"] == ["custom", "label", "session_info"]
    assert captured["branch"] == ["custom", "label", "session_info"]
    assert captured["label"] == "bookmark"
    assert captured["tree_label"] == "bookmark"
    assert captured["header"]["id"] == tree.session_id
    assert captured["entry_dict"]["type"] == "session_info"
    assert captured["has_mutation"] is False


def test_command_context_session_manager_is_empty_without_session(tmp_path):
    workspace = _make_workspace(tmp_path)
    captured = {}

    def handler(ctx, args):
        captured["id"] = ctx.session_manager.get_session_id()
        captured["entries"] = ctx.session_manager.get_entries()
        captured["header"] = ctx.sessionManager.get_header().to_dict()

    command = type("C", (), {})()
    command.handler = handler
    command_map = {"inspect": command}
    dispatch = dispatch_extension_command(
        "/inspect", command_map, cwd=str(workspace), has_ui=False
    )

    assert dispatch is not None
    assert dispatch.ran is True
    assert captured["id"] is None
    assert captured["entries"] == ()
    assert captured["header"] == {
        "id": None,
        "timestamp": None,
        "cwd": None,
        "version": None,
        "parentSession": None,
    }


def test_dispatch_exposes_session_metadata_actions(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    captured = {}

    def handler(ctx, args):
        captured["initial"] = ctx.get_session_name()
        captured["alias_initial"] = ctx.getSessionName()
        captured["set_name"] = ctx.setSessionName("new name")
        captured["set_label"] = ctx.setLabel("entry-1", "bookmark")
        captured["clear_label"] = ctx.set_label("entry-1", None)

    command = type("C", (), {})()
    command.handler = handler
    calls: list[tuple[str, ...]] = []

    def set_name(name: str | None) -> str:
        calls.append(("name", "" if name is None else name))
        return "name-id"

    def set_label(entry_id: str, label: str | None) -> str:
        calls.append(("label", entry_id, "" if label is None else label))
        return "label-id"

    dispatch = dispatch_extension_command(
        "/meta",
        {"meta": command},
        cwd=str(workspace),
        has_ui=False,
        set_session_name_fn=set_name,
        get_session_name_fn=lambda: "old name",
        set_label_fn=set_label,
    )

    assert dispatch is not None
    assert dispatch.ran is True
    assert captured == {
        "initial": "old name",
        "alias_initial": "old name",
        "set_name": "name-id",
        "set_label": "label-id",
        "clear_label": "label-id",
    }
    assert calls == [
        ("name", "new name"),
        ("label", "entry-1", "bookmark"),
        ("label", "entry-1", ""),
    ]


def test_session_metadata_actions_fail_predictably_without_capabilities(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    captured = {}

    def handler(ctx, args):
        captured["name"] = ctx.getSessionName()
        try:
            ctx.set_session_name("x")
        except Exception as exc:  # noqa: BLE001 - assert safe capability error
            captured["set_name_error"] = type(exc).__name__
        try:
            ctx.setLabel("entry-1", "bookmark")
        except Exception as exc:  # noqa: BLE001 - assert safe capability error
            captured["set_label_error"] = type(exc).__name__

    command = type("C", (), {})()
    command.handler = handler

    dispatch = dispatch_extension_command(
        "/meta", {"meta": command}, cwd=str(workspace), has_ui=False
    )

    assert dispatch is not None
    assert dispatch.ran is True
    assert captured == {
        "name": None,
        "set_name_error": "ExtensionCapabilityError",
        "set_label_error": "ExtensionCapabilityError",
    }
