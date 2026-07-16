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
    # The one-shot run settles into idle, so the stream ends with a single
    # payload-free `agent_settled` immediately after the run's `agent_end`.
    # CANONICAL json-mode terminator, duplicated in
    # tests/test_native_automation_json_mode.py and (for the RPC path)
    # scripts/parity_checks/automation_rpc_conformance.py. Before changing the
    # terminator, grep ALL of tests/ and scripts/parity_checks/ (e.g.
    # `agent_settled`, `types[-2:]`, `agent_end"`) so every duplicated assertion is
    # updated in one pass instead of surfacing later on a full `just check`.
    assert types[-2:] == ["agent_end", "agent_settled"]
    assert types.count("agent_settled") == 1
    assert records[-1] == {"type": "agent_settled"}
    assert "message_update" in types
    message_end = next(r for r in records if r["type"] == "message_end")
    text = "".join(
        b["text"] for b in message_end["message"]["content"] if b.get("type") == "text"
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


def test_json_trust_extension_reuses_activation_and_keeps_stdout_protocol_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    work = _workspace(tmp_path)
    config = tmp_path / "pipy-config"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    proof = tmp_path / "extension-proof.txt"
    global_extension = config / "extensions" / "trust.py"
    global_extension.parent.mkdir(parents=True)
    global_extension.write_text(
        f"open({str(proof)!r}, 'a').write('g')\n"
        "def activate(api):\n"
        "    @api.on('project_trust')\n"
        "    def trust(event, ctx):\n"
        "        assert ctx.mode == 'json' and ctx.hasUI is False\n"
        "        assert ctx.ui.select('pick', ['yes']) is None\n"
        "        assert ctx.ui.confirm('confirm', 'message') is False\n"
        "        assert ctx.ui.input('input') is None\n"
        "        ctx.ui.notify('trust decided', 'info')\n"
        "        return {'trusted': 'yes'}\n"
        "    @api.on('session_start')\n"
        "    def started(event, ctx):\n"
        "        assert ctx.is_project_trusted() is True\n"
        "        assert ctx.isProjectTrusted() is True\n"
        f"        open({str(proof)!r}, 'a').write('G')\n",
        encoding="utf-8",
    )
    project_extension = work / ".pipy" / "extensions" / "project.py"
    project_extension.parent.mkdir(parents=True)
    project_extension.write_text(
        f"open({str(proof)!r}, 'a').write('p')\n"
        "def activate(api):\n"
        "    @api.on('session_start')\n"
        "    def started(event, ctx):\n"
        "        assert ctx.is_project_trusted() is True\n"
        "        assert ctx.isProjectTrusted() is True\n"
        f"        open({str(proof)!r}, 'a').write('P')\n",
        encoding="utf-8",
    )

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
    captured = capfdbinary.readouterr()
    records = [json.loads(line) for line in captured.out.decode().splitlines() if line]
    assert records[0]["type"] == "session"
    assert "trust decided" in captured.err.decode()
    assert proof.read_text() == "gpPG"
