"""Product-path tests for `/hotkeys` in the native tool-loop REPL.

These drive the real `NativeToolReplSession.run` command loop with the
deterministic fake provider (captured-stream fallback) and assert that
`/hotkeys` renders from the resolved keybinding manager — both an injected
manager and the default `<config>/keybindings.json` discovery path — rather than
a hardcoded table.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.keybindings import KeybindingsManager
from pipy_harness.native.tool_loop_session import NativeToolReplSession


def _run(session: NativeToolReplSession, inputs: str, tmp_path: Path) -> str:
    error_stream = io.StringIO()
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(inputs),
        output_stream=io.StringIO(),
        error_stream=error_stream,
    )
    return error_stream.getvalue()


def test_hotkeys_renders_grouped_table_from_default_bindings(tmp_path: Path) -> None:
    session = NativeToolReplSession(
        provider=FakeNativeProvider(supports_tool_calls=True),
        keybindings_manager=KeybindingsManager(user_bindings={}),
    )
    out = _run(session, "/hotkeys\n/exit\n", tmp_path)
    assert "Keyboard Shortcuts" in out
    assert "Navigation" in out
    assert "Send message" in out
    # Resolved default for the model cycle is rendered.
    assert "Cycle models" in out


def test_hotkeys_reflects_injected_user_override(tmp_path: Path) -> None:
    session = NativeToolReplSession(
        provider=FakeNativeProvider(supports_tool_calls=True),
        keybindings_manager=KeybindingsManager(
            user_bindings={"app.model.cycleForward": "ctrl+j"}
        ),
    )
    out = _run(session, "/hotkeys\n/exit\n", tmp_path)
    assert "Ctrl+J" in out


def test_hotkeys_reads_edited_keybindings_json_from_config_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "config"
    config_home.mkdir()
    (config_home / "keybindings.json").write_text(
        json.dumps({"app.model.cycleForward": "ctrl+y"}), encoding="utf-8"
    )
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config_home))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # No injected manager: the session loads <config>/keybindings.json itself.
    session = NativeToolReplSession(provider=FakeNativeProvider(supports_tool_calls=True))
    out = _run(session, "/hotkeys\n/exit\n", workspace)
    assert "Ctrl+Y" in out
