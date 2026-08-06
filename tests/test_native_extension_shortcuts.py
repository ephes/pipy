"""Slice D: extension keyboard-shortcut registration + dispatch.

Pi's answer.ts binds `ctrl+.` to the same handler as the `/answer` command.
`api.register_shortcut(key, handler)` records that binding; the live tool loop
intercepts the key and dispatches the handler through `dispatch_extension_shortcut`
with the same mode-aware context as a command (conversation, completion,
custom UI, notifications). Reserved built-in hotkeys and duplicates are refused
(disabling that extension, never clobbering a built-in binding).
"""

from __future__ import annotations

import re
from io import StringIO
from pathlib import Path

from pipy_harness.native import FakeNativeProvider
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.extension_types import (
    RESERVED_SHORTCUT_KEYS,
    ExtensionCodingSessionControl,
    normalize_shortcut_key,
)
from pipy_harness.native.extensions.activation import activate_extensions
from pipy_harness.native.extensions.dispatch import (
    dispatch_extension_shortcut,
    extension_shortcuts,
)
from pipy_harness.native.keybindings import KeybindingsManager
from pipy_harness.native.tui import TerminalUi


def test_normalize_canonicalizes_modifier_order() -> None:
    # Modifier reordering must not bypass a reserved key or split a binding.
    assert normalize_shortcut_key("Ctrl+Shift+P") == "shift-ctrl-p"
    assert normalize_shortcut_key("Shift+Ctrl+P") == "shift-ctrl-p"
    assert normalize_shortcut_key("Ctrl+.") == "ctrl-."
    assert normalize_shortcut_key("ALT+ENTER") == "alt-enter"


def test_modifier_reordered_reserved_key_is_refused(tmp_path) -> None:
    # "Ctrl+Shift+P" canonicalizes to the reserved "shift-ctrl-p" and is refused.
    _write_ext(
        tmp_path,
        "bad",
        "def activate(api):\n"
        "    api.register_shortcut('Ctrl+Shift+P', lambda ctx, args: None)\n",
    )
    from pipy_harness.native.extensions.packages import discover_extensions

    descriptors = discover_extensions(
        tmp_path,
        config_home_env={},
        home_dir=tmp_path,
        include_workspace_defaults=True,
    )
    activated = activate_extensions(descriptors)
    assert activated[0].status == "disabled"
    assert extension_shortcuts(activated) == {}


def test_default_external_editor_shortcut_is_reserved(tmp_path) -> None:
    # Pi reserves app.editor.external (default Ctrl+G) for the editor, so an
    # extension shortcut must not shadow it.
    _write_ext(
        tmp_path,
        "bad",
        "def activate(api):\n"
        "    api.register_shortcut('Ctrl+G', lambda ctx, args: None)\n",
    )
    from pipy_harness.native.extensions.packages import discover_extensions

    descriptors = discover_extensions(
        tmp_path,
        config_home_env={},
        home_dir=tmp_path,
        include_workspace_defaults=True,
    )
    activated = activate_extensions(descriptors)
    assert activated[0].status == "disabled"
    assert extension_shortcuts(activated) == {}


def test_shipped_examples_do_not_register_reserved_external_editor_shortcut() -> None:
    repo = Path(__file__).resolve().parents[1]
    reserved_patterns = sorted(
        re.escape(key).replace(r"\-", "[-+]") for key in RESERVED_SHORTCUT_KEYS
    )
    forbidden = re.compile(
        r"register_?shortcut\s*\(\s*['\"](?:" + "|".join(reserved_patterns) + r")['\"]",
        re.IGNORECASE,
    )
    offenders: list[str] = []
    for base in (repo / "docs" / "examples" / "extensions", repo / "scripts"):
        assert base.exists(), base
        for path in base.rglob("*"):
            if path.suffix not in {".py", ".sh"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if forbidden.search(text):
                offenders.append(str(path.relative_to(repo)))
    assert offenders == []


def _write_ext(root, name: str, body: str) -> None:
    ext_dir = root / ".pipy" / "extensions" / name
    ext_dir.mkdir(parents=True)
    (ext_dir / "extension.py").write_text(body, encoding="utf-8")


def _activate(tmp_path):
    from pipy_harness.native.extensions.packages import discover_extensions

    descriptors = discover_extensions(
        tmp_path,
        config_home_env={},
        home_dir=tmp_path,
        include_workspace_defaults=True,
    )
    return activate_extensions(descriptors)


def test_register_shortcut_collected_and_normalized(tmp_path) -> None:
    _write_ext(
        tmp_path,
        "ans",
        """
def activate(api):
    def handler(ctx, args):
        ctx.ui.notify("fired:" + args, "info")
    api.register_command("answer", "Answer", handler)
    api.register_shortcut("Ctrl+.", handler)
""",
    )
    activated = _activate(tmp_path)
    assert [a.status for a in activated] == ["activated"]
    shortcuts = extension_shortcuts(activated)
    # Pi-style "Ctrl+." normalizes to pipy's internal "ctrl-." key string.
    assert "ctrl-." in shortcuts


def test_dispatch_shortcut_runs_handler_with_context(tmp_path) -> None:
    _write_ext(
        tmp_path,
        "ans",
        """
def activate(api):
    def handler(ctx, args):
        msg = ctx.conversation.last_assistant_message()
        ctx.ui.notify("seen:" + (msg.text if msg else "none"), "info")
    api.register_shortcut("ctrl-x", handler)
""",
    )
    activated = _activate(tmp_path)
    shortcuts = extension_shortcuts(activated)

    from pipy_harness.native.agent import AgentAssistantMessage, ProductContent

    dispatch = dispatch_extension_shortcut(
        "ctrl-x",
        shortcuts,
        cwd=str(tmp_path),
        has_ui=True,
        coding_session=ExtensionCodingSessionControl(
            messages=[AgentAssistantMessage(content=ProductContent("hello there"))]
        ),
    )
    assert dispatch is not None and dispatch.ran
    assert ("info", "seen:hello there") in dispatch.messages


def test_dispatch_unknown_shortcut_returns_none(tmp_path) -> None:
    assert (
        dispatch_extension_shortcut("ctrl-g", {}, cwd=str(tmp_path), has_ui=True)
        is None
    )


def test_reserved_shortcut_key_disables_extension(tmp_path) -> None:
    _write_ext(
        tmp_path,
        "bad",
        """
def activate(api):
    api.register_shortcut("enter", lambda ctx, args: None)
""",
    )
    activated = _activate(tmp_path)
    assert activated[0].status == "disabled"
    assert extension_shortcuts(activated) == {}


def test_single_character_shortcut_key_is_rejected(tmp_path) -> None:
    # A bare printable key (or raw char) would shadow ordinary typing, so it is
    # refused and the extension disabled.
    for key in ('"a"', '"."'):
        root = tmp_path / key.strip('"')
        _write_ext(
            root,
            "bad",
            f"def activate(api):\n    api.register_shortcut({key}, lambda ctx, args: None)\n",
        )
        from pipy_harness.native.extensions.packages import discover_extensions

        descriptors = discover_extensions(
            root,
            config_home_env={},
            home_dir=root,
            include_workspace_defaults=True,
        )
        activated = activate_extensions(descriptors)
        assert activated[0].status == "disabled", key
        assert extension_shortcuts(activated) == {}


def test_empty_base_shortcut_key_is_rejected(tmp_path) -> None:
    # "Ctrl+" / "ctrl-" has no base key and could never fire; it is refused.
    _write_ext(
        tmp_path,
        "bad",
        "def activate(api):\n"
        "    api.register_shortcut('Ctrl+', lambda ctx, args: None)\n",
    )
    from pipy_harness.native.extensions.packages import discover_extensions

    descriptors = discover_extensions(
        tmp_path,
        config_home_env={},
        home_dir=tmp_path,
        include_workspace_defaults=True,
    )
    activated = activate_extensions(descriptors)
    assert activated[0].status == "disabled"
    assert extension_shortcuts(activated) == {}


def test_shortcut_send_user_message_triggers_a_turn(tmp_path, monkeypatch) -> None:
    # Product path: a fired shortcut whose handler enqueues a user message via
    # api.send_user_message schedules a real provider turn. The shortcut key is
    # delivered to the session as the TUI sentinel line.
    from dataclasses import dataclass, field
    from datetime import UTC, datetime
    from io import StringIO

    from pipy_harness.models import HarnessStatus
    from pipy_harness.native import ProviderResult
    from pipy_harness.native.coding.session import CodingSession
    from pipy_harness.native.ui.components.custom_editor import (
        HOTKEY_EXTENSION_SHORTCUT_PREFIX,
    )

    @dataclass
    class _CapturingProvider:
        requests: list = field(default_factory=list)

        @property
        def name(self) -> str:
            return "capturing-fake"

        @property
        def model_id(self) -> str:
            return "capturing-model"

        @property
        def supports_tool_calls(self) -> bool:
            return True

        def complete(self, request, **_kwargs):
            self.requests.append(request)
            now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="ok",
            )

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write_ext(
        tmp_path,
        "ans",
        """
def activate(api):
    def handler(ctx, args):
        ctx.ui.notify("SHORTCUT_NOTICE")
        api.send_user_message("SHORTCUT_PROMPT")
    api.register_shortcut("ctrl-x", handler)
""",
    )
    provider = _CapturingProvider()
    session = CodingSession(provider=provider, tool_registry={})
    error_stream = StringIO()
    result = session.run(
        workspace_root=tmp_path,
        input_stream=StringIO(f"{HOTKEY_EXTENSION_SHORTCUT_PREFIX}ctrl-x\n"),
        output_stream=StringIO(),
        error_stream=error_stream,
    )
    assert len(provider.requests) == 1
    assert "SHORTCUT_PROMPT" in provider.requests[0].user_prompt
    assert result.user_turn_count == 1
    # Notifications surface (live sink) even in non-TUI mode, like a command.
    assert "SHORTCUT_NOTICE" in error_stream.getvalue()


def test_rebound_external_editor_key_warns_when_extension_shortcut_shadowed(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _write_ext(
        tmp_path,
        "shadow",
        """
def activate(api):
    api.register_shortcut("Ctrl+X", lambda ctx, args: None)
""",
    )
    provider = FakeNativeProvider(supports_tool_calls=True)
    error_stream = StringIO()
    terminal_ui = TerminalUi(
        input_stream=StringIO(),
        terminal_stream=error_stream,
        cwd=tmp_path,
    )

    def read_line(self, prompt_label, *, footer=None):
        del self, prompt_label, footer
        return "/exit"

    monkeypatch.setattr(TerminalUi, "read_line", read_line)
    monkeypatch.setattr(
        CodingSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kw: (
            terminal_ui
        ),
    )
    session = CodingSession(
        provider=provider,
        tool_registry={},
        keybindings_manager=KeybindingsManager({"app.editor.external": "Ctrl+X"}),
    )

    session.run(
        workspace_root=tmp_path,
        input_stream=StringIO(),
        output_stream=StringIO(),
        error_stream=error_stream,
    )

    assert (
        "extension shortcut 'ctrl-x' is shadowed by app.editor.external"
        in error_stream.getvalue()
    )


def test_duplicate_shortcut_key_disables_second_extension(tmp_path) -> None:
    for name in ("aaa", "bbb"):
        _write_ext(
            tmp_path,
            name,
            """
def activate(api):
    api.register_shortcut("ctrl-x", lambda ctx, args: None)
""",
        )
    activated = _activate(tmp_path)
    statuses = {a.name: a.status for a in activated}
    # First (sorted) keeps the binding; the second is disabled as a duplicate.
    assert statuses["aaa"] == "activated"
    assert statuses["bbb"] == "disabled"
    assert set(extension_shortcuts(activated)) == {"ctrl-x"}
