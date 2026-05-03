from __future__ import annotations

import json
import sys
from pathlib import Path

from pipy_harness.cli import main
from pipy_session import (
    inspect_finalized_session,
    list_finalized_sessions,
    search_finalized_sessions,
    verify_session_archive,
)


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_cli_native_smoke_uses_fake_provider_and_finalizes_record(tmp_path, capfd):
    root = tmp_path / "sessions"

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--slug",
            "native-smoke",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--goal",
            "Native bootstrap smoke",
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert "pipy native fake provider completed." in captured.out
    assert "session finalized" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types[-1] == "session.finalized"
    assert "native.session.started" in event_types
    assert "native.provider.completed" in event_types
    provider_payloads = [
        event["payload"] for event in events if event["type"] == "native.provider.completed"
    ]
    assert provider_payloads[0]["provider"] == "fake"
    assert provider_payloads[0]["model_id"] == "fake-native-bootstrap"
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "pipy native fake provider completed." not in combined
    assert "You are the native pipy runtime bootstrap" not in combined
    assert verify_session_archive(root=root).ok is True
    assert list_finalized_sessions(root=root)[0].jsonl_path == finalized[0]
    assert search_finalized_sessions("native.provider.completed", root=root)
    inspection = inspect_finalized_session(finalized[0], root=root)
    assert inspection.event_types["native.session.completed"] == 1


def test_cli_native_rejects_command_after_separator(tmp_path, capsys):
    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--slug",
            "native-command",
            "--root",
            str(tmp_path / "sessions"),
            "--goal",
            "Native command rejection",
            "--",
            sys.executable,
            "-c",
            "print('should not run')",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "do not accept a command" in captured.err


def test_cli_native_requires_goal_before_creating_record(tmp_path, capsys):
    root = tmp_path / "sessions"

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--slug",
            "native-missing-goal",
            "--root",
            str(root),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "require --goal" in captured.err
    assert not root.exists()


def test_cli_subprocess_behavior_still_requires_command(tmp_path, capsys):
    exit_code = main(["run", "--agent", "custom", "--slug", "missing", "--root", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "command after --" in captured.err
