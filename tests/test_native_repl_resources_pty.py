"""Real-PTY product-TUI tests for resource slash commands.

These exercise the actual product paint path over a pseudo-TTY at both
80x24 and 100x40: the inline slash menu discovers a valid workspace
custom command and executes it through the real session command path
(one bounded provider turn with the expanded text), an unsafe/unknown
resource is rejected and fails closed with no provider turn, and the
renderer never enters the alternate screen.
"""

from __future__ import annotations

import os
import pty
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO, cast

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest, ProviderResult
from pipy_harness.native.resources import WorkspaceResources
from pipy_harness.native.tool_loop_session import (
    NativeToolReplSession,
    _tool_loop_command_descriptions,
    _tool_loop_command_names,
)
from pipy_harness.native.tui import ToolLoopTerminalUi


@dataclass
class _CapturingToolProvider:
    final_text: str = "RESPONSE_MARKER_DONE"
    requests: list[ProviderRequest] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "capturing-tool-fake"

    @property
    def model_id(self) -> str:
        return "capturing-tool-model"

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        now = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text=self.final_text,
            usage=None,
            metadata=None,
            tool_calls=(),
        )


def _spawn_live_drainer(fd: int) -> tuple[threading.Thread, list[bytes]]:
    collected: list[bytes] = []

    def _drain() -> None:
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            collected.append(chunk)

    thread = threading.Thread(target=_drain, daemon=True)
    thread.start()
    return thread, collected


def _wait_for(collected: list[bytes], needle: str, *, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    encoded = needle.encode("utf-8")
    while time.monotonic() < deadline:
        if encoded in b"".join(collected):
            return True
        time.sleep(0.02)
    return False


def _seed_command(tmp_path: Path) -> None:
    commands = tmp_path / ".pipy" / "commands"
    commands.mkdir(parents=True)
    (commands / "deploy.md").write_text(
        "---\nname: deploy\ndescription: Deploy summary\n---\n"
        "COMMAND_deploy_for $ARGUMENTS\n",
        encoding="utf-8",
    )


def _drive_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    columns: int,
    rows: int,
    interact,
) -> tuple[_CapturingToolProvider, str, object]:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setenv("LINES", str(rows))
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))

    in_master, in_slave = pty.openpty()
    err_master, err_slave = pty.openpty()
    stdin = os.fdopen(in_slave, "r", buffering=1, encoding="utf-8")
    terminal = os.fdopen(err_slave, "w", buffering=1, encoding="utf-8")
    err_thread, err_chunks = _spawn_live_drainer(err_master)

    resources = WorkspaceResources.discover(
        tmp_path, config_home_env={}, home_dir=tmp_path
    )
    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, stdin),
        terminal_stream=cast(TextIO, terminal),
        cwd=tmp_path,
        command_names=_tool_loop_command_names(resources),
        command_descriptions=_tool_loop_command_descriptions(resources),
    )
    provider = _CapturingToolProvider()
    session = NativeToolReplSession(provider=provider, tool_registry={})
    monkeypatch.setattr(
        NativeToolReplSession,
        "_build_terminal_ui",
        lambda self, input_stream, error_stream, workspace, resources=None, **_kwargs: ui,
    )

    result_holder: list[object] = []

    def _run() -> None:
        result_holder.append(
            session.run(
                workspace_root=tmp_path,
                input_stream=cast(TextIO, stdin),
                output_stream=cast(TextIO, terminal),
                error_stream=cast(TextIO, terminal),
            )
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        assert _wait_for(err_chunks, "escape interrupt"), "startup chrome never painted"
        interact(in_master, err_chunks)
        os.write(in_master, b"\x04")
        worker.join(timeout=8.0)
    finally:
        try:
            os.write(in_master, b"\x04")
        except OSError:
            pass
        terminal.flush()
        terminal.close()
        stdin.close()
        err_thread.join(timeout=8.0)
        os.close(in_master)
        os.close(err_master)

    captured = b"".join(err_chunks).decode("utf-8", errors="replace")
    result = result_holder[0] if result_holder else None
    return provider, captured, result


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(("columns", "rows"), [(80, 24), (100, 40)])
def test_pty_slash_menu_discovers_and_runs_custom_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, columns: int, rows: int
):
    _seed_command(tmp_path)

    def interact(in_master: int, err_chunks: list[bytes]) -> None:
        # Type the command prefix so the slash menu filters to the discovered
        # custom command (the unfiltered menu caps its visible rows).
        os.write(in_master, b"/deploy")
        assert _wait_for(err_chunks, "deploy"), "custom command not in slash menu"
        # Finish the command and execute it through the real command path.
        os.write(in_master, b" staging\n")
        assert _wait_for(err_chunks, "RESPONSE_MARKER_DONE"), "provider turn never ran"

    provider, captured, result = _drive_session(
        tmp_path, monkeypatch, columns, rows, interact
    )

    assert result is not None
    assert getattr(result, "status") == HarnessStatus.SUCCEEDED
    # Inline model only: never the alternate screen.
    assert "\x1b[?1049h" not in captured
    # Exactly one bounded provider turn, carrying the expanded command text.
    assert len(provider.requests) == 1
    assert provider.requests[0].user_prompt.strip() == "COMMAND_deploy_for staging"
    assert getattr(result, "resource_invocation_count") == 1


@pytest.mark.skipif(os.name != "posix", reason="pty integration requires posix")
@pytest.mark.parametrize(("columns", "rows"), [(80, 24), (100, 40)])
def test_pty_unsafe_resource_is_rejected_without_provider_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, columns: int, rows: int
):
    _seed_command(tmp_path)

    def interact(in_master: int, err_chunks: list[bytes]) -> None:
        os.write(in_master, b"/skill nope\n")
        assert _wait_for(err_chunks, "no skill named 'nope'"), "rejection not shown"

    provider, captured, result = _drive_session(
        tmp_path, monkeypatch, columns, rows, interact
    )

    assert result is not None
    assert getattr(result, "status") == HarnessStatus.SUCCEEDED
    assert "\x1b[?1049h" not in captured
    # Fail closed: the unknown resource issued no provider turn at all.
    assert provider.requests == []
    assert getattr(result, "resource_invocation_count") == 0
