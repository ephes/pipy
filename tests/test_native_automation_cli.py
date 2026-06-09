"""End-to-end CLI tests for the headless automation modes via `main()`.

These drive the real `pipy repl` argument parsing → mode resolution →
tool-loop adapter → automation driver path with a deterministic, CLI-selectable,
tool-capable fake provider (`--native-provider fake --native-model fake-tools`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from pipy_harness.cli import main


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPY_NATIVE_DEFAULTS_PATH", str(tmp_path / "defaults.json"))
    monkeypatch.setenv("PIPY_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    # A positional prompt selects one-shot mode; keep stdin a "TTY" so the
    # positional-prompt path is what triggers it (not non-TTY auto-detection).
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)


def _workspace(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    return work


def test_cli_mode_json_emits_header_and_events(
    tmp_path: Path, capfdbinary: pytest.CaptureFixture[bytes]
) -> None:
    work = _workspace(tmp_path)
    exit_code = main(
        [
            "repl",
            "--cwd",
            str(work),
            "--native-provider",
            "fake",
            "--native-model",
            "fake-tools",
            "--no-session",
            "--mode",
            "json",
            "ROOT",
        ]
    )
    assert exit_code == 0
    out = capfdbinary.readouterr().out.decode("utf-8")
    records = [json.loads(line) for line in out.splitlines() if line]
    assert records[0]["type"] == "session"
    types = [r["type"] for r in records[1:]]
    assert types[0] == "agent_start"
    assert types[-1] == "agent_end"
    assert "message_update" in types
    message_end = next(r for r in records if r["type"] == "message_end")
    text = "".join(
        b["text"]
        for b in message_end["message"]["content"]
        if b.get("type") == "text"
    )
    assert "ROOT" in text


def test_cli_mode_rpc_rejects_positional_prompt(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    work = _workspace(tmp_path)
    exit_code = main(
        [
            "repl",
            "--cwd",
            str(work),
            "--native-provider",
            "fake",
            "--native-model",
            "fake-tools",
            "--no-session",
            "--mode",
            "rpc",
            "oops",
        ]
    )
    assert exit_code == 2
    assert "does not accept a positional prompt" in capfd.readouterr().err


def test_cli_print_mode_emits_final_text(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    work = _workspace(tmp_path)
    exit_code = main(
        [
            "repl",
            "--cwd",
            str(work),
            "--native-provider",
            "fake",
            "--native-model",
            "fake-tools",
            "--no-session",
            "--print",
            "ROOT",
        ]
    )
    assert exit_code == 0
    out = capfd.readouterr().out
    assert "ROOT" in out
    # No JSON records in print mode — just the final text line.
    assert not out.lstrip().startswith("{")
