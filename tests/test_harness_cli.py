from __future__ import annotations

import json
import sys
from pathlib import Path

from pipy_harness.cli import main
from pipy_session import verify_session_archive


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_cli_run_smoke_streams_child_output_and_finalizes_record(tmp_path, capfd):
    root = tmp_path / "sessions"

    exit_code = main(
        [
            "run",
            "--agent",
            "custom",
            "--slug",
            "smoke",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--",
            sys.executable,
            "-c",
            "print('CLI_CHILD_OUT')",
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert "CLI_CHILD_OUT" in captured.out
    assert "session finalized" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    assert "CLI_CHILD_OUT" not in finalized[0].read_text(encoding="utf-8")
    assert verify_session_archive(root=root).ok is True


def test_cli_run_nonzero_returns_child_exit_after_finalization(tmp_path, capfd):
    root = tmp_path / "sessions"

    exit_code = main(
        [
            "run",
            "--agent",
            "custom",
            "--slug",
            "fail",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(5)",
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 5
    assert "session finalized" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    assert read_jsonl(finalized[0])[-1]["type"] == "session.finalized"
    assert verify_session_archive(root=root).ok is True


def test_cli_requires_command_after_separator(tmp_path, capsys):
    exit_code = main(["run", "--agent", "custom", "--slug", "missing", "--root", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "command after --" in captured.err


def test_cli_reports_sanitized_internal_run_error(tmp_path, capsys):
    exit_code = main(
        [
            "run",
            "--agent",
            "custom",
            "--slug",
            "missing-bin",
            "--root",
            str(tmp_path / "sessions"),
            "--cwd",
            str(tmp_path),
            "--",
            "definitely-not-a-real-pipy-test-binary",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "FileNotFoundError" in captured.err
    assert "definitely-not-a-real-pipy-test-binary" in captured.err
    assert "session finalized" in captured.err


def test_cli_record_files_records_changed_paths_when_enabled(tmp_path):
    import shutil
    import subprocess

    if shutil.which("git") is None:
        return
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True)
    root = tmp_path / "sessions"

    exit_code = main(
        [
            "run",
            "--agent",
            "custom",
            "--slug",
            "files",
            "--root",
            str(root),
            "--cwd",
            str(repo),
            "--record-files",
            "--",
            sys.executable,
            "-c",
            "from pathlib import Path; Path('created.txt').write_text('x')",
        ]
    )

    assert exit_code == 0
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    events = read_jsonl(finalized[0])
    payload = [event["payload"] for event in events if event["type"] == "workspace.files.changed"][0]
    assert payload["paths"] == ["created.txt"]
