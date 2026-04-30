from __future__ import annotations

import json
import io
import sys
from datetime import UTC, datetime

from pipy_session import (
    append_auto_event,
    handle_claude_hook,
    start_auto_capture,
    state_dir,
    stop_auto_capture,
)
from pipy_session.auto_capture import _redacted_argv, run_wrapped_agent
from pipy_session.cli import main


FIXED_NOW = datetime(2026, 4, 30, 13, 30, 0, tzinfo=UTC)


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_auto_capture_state_lives_under_in_progress_project_state(tmp_path):
    state = start_auto_capture(
        agent="claude",
        slug="state-test",
        platform_session_id="session-123",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )

    assert state.state_path == state_dir(tmp_path) / "claude-session-123.json"
    assert state.state_path.exists()
    assert state.active_path.parent == tmp_path / ".in-progress" / "pipy"
    assert ".in-progress/pipy/.state" in state.state_path.as_posix()


def test_auto_capture_start_event_stop_finalizes_partial_record_and_removes_state(tmp_path):
    state = start_auto_capture(
        agent="codex",
        slug="auto-lifecycle",
        platform_session_id="codex-session",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    append_auto_event(
        root=tmp_path,
        agent="codex",
        platform_session_id="codex-session",
        event_type="codex.turn.observed",
        summary="Observed a Codex turn boundary.",
        metadata={"turn_id": "turn-1"},
        now=FIXED_NOW,
    )

    record = stop_auto_capture(
        root=tmp_path,
        agent="codex",
        platform_session_id="codex-session",
        metadata={"reason": "wrapper-exit"},
        now=FIXED_NOW,
    )

    assert not state.active_path.exists()
    assert not state.state_path.exists()
    assert record.jsonl_path.parent == tmp_path / "pipy" / "2026" / "04"
    assert record.markdown_path == record.jsonl_path.with_suffix(".md")
    events = read_jsonl(record.jsonl_path)
    assert events[0]["partial"] is True
    assert events[1]["type"] == "capture.limitations"
    assert events[2]["type"] == "auto_capture.started"
    assert events[3]["type"] == "codex.turn.observed"
    assert events[-1]["type"] == "auto_capture.ended"
    assert "partial" in record.markdown_path.read_text(encoding="utf-8")


def test_auto_capture_reuses_live_state_for_repeated_start(tmp_path):
    first = start_auto_capture(
        agent="claude",
        slug="duplicate-start",
        platform_session_id="same-session",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    second = start_auto_capture(
        agent="claude",
        slug="duplicate-start",
        platform_session_id="same-session",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )

    assert second.active_path == first.active_path
    active_records = list((tmp_path / ".in-progress" / "pipy").glob("*.jsonl"))
    assert active_records == [first.active_path]
    events = read_jsonl(first.active_path)
    assert events[-1]["type"] == "auto_capture.resumed"


def test_claude_hook_start_prompt_metadata_and_end_are_partial_and_redacted(tmp_path):
    start_payload = {
        "session_id": "abc123",
        "transcript_path": "/Users/example/.claude/projects/pipy/session.jsonl",
        "cwd": "/Users/example/projects/pipy",
        "hook_event_name": "SessionStart",
        "source": "startup",
        "model": "claude-sonnet-4-6",
    }

    result = handle_claude_hook(start_payload, root=tmp_path, machine="studio", now=FIXED_NOW)
    assert result.active_path is not None

    prompt_payload = {
        "session_id": "abc123",
        "transcript_path": "/Users/example/.claude/projects/pipy/session.jsonl",
        "cwd": "/Users/example/projects/pipy",
        "hook_event_name": "UserPromptSubmit",
        "prompt": "please use token=SECRET123 and password hunter2",
    }
    handle_claude_hook(prompt_payload, root=tmp_path, now=FIXED_NOW)

    end_payload = {
        "session_id": "abc123",
        "transcript_path": "/Users/example/.claude/projects/pipy/session.jsonl",
        "cwd": "/Users/example/projects/pipy",
        "hook_event_name": "SessionEnd",
        "reason": "other",
    }
    end_result = handle_claude_hook(end_payload, root=tmp_path, now=FIXED_NOW)

    assert end_result.record is not None
    text = end_result.record.jsonl_path.read_text(encoding="utf-8")
    assert "SECRET123" not in text
    assert "hunter2" not in text

    events = read_jsonl(end_result.record.jsonl_path)
    assert events[0]["agent"] == "claude"
    assert events[0]["partial"] is True
    prompt_event = [event for event in events if event["type"] == "claude.userpromptsubmit"][0]
    assert prompt_event["payload"]["prompt"] == {"characters": 47, "redacted": True}
    assert prompt_event["payload"]["transcript_file"] == "session.jsonl"
    assert prompt_event["payload"]["cwd_name"] == "pipy"


def test_claude_session_end_without_state_is_ignored(tmp_path):
    result = handle_claude_hook(
        {
            "session_id": "missing",
            "hook_event_name": "SessionEnd",
            "reason": "other",
        },
        root=tmp_path,
        now=FIXED_NOW,
    )

    assert result.record is None
    assert "no active state" in result.message


def test_claude_hook_cli_writes_ignored_messages_to_stderr(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"hook_event_name":"SessionEnd","session_id":"missing"}'))

    exit_code = main(["--root", str(tmp_path), "auto", "hook", "claude"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "no active state" in captured.err
    assert captured.out == ""


def test_claude_hook_removes_orphaned_state_when_active_record_is_missing(tmp_path):
    state = start_auto_capture(
        agent="claude",
        slug="orphan-state",
        platform_session_id="orphan-session",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    state.active_path.unlink()

    result = handle_claude_hook(
        {
            "session_id": "orphan-session",
            "hook_event_name": "SessionEnd",
            "reason": "other",
        },
        root=tmp_path,
        now=FIXED_NOW,
    )

    assert "no active state" in result.message
    assert not state.state_path.exists()


def test_claude_hook_removes_corrupt_state_and_treats_it_as_missing(tmp_path):
    path = state_dir(tmp_path) / "claude-corrupt-session.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")

    result = handle_claude_hook(
        {
            "session_id": "corrupt-session",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "hello",
        },
        root=tmp_path,
        now=FIXED_NOW,
    )

    assert "no active state" in result.message
    assert not path.exists()


def test_metadata_redacts_sensitive_keys_and_values(tmp_path):
    state = start_auto_capture(
        agent="codex",
        slug="redaction",
        platform_session_id="redaction-session",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )

    append_auto_event(
        root=tmp_path,
        agent="codex",
        platform_session_id="redaction-session",
        event_type="codex.metadata",
        metadata={"api_key": "abc123", "note": "contains token value"},
        now=FIXED_NOW,
    )

    text = state.active_path.read_text(encoding="utf-8")
    assert "abc123" not in text
    assert "contains token value" not in text
    assert text.count("[REDACTED]") == 2


def test_sensitive_session_id_is_not_written_to_state_or_record(tmp_path):
    state = start_auto_capture(
        agent="claude",
        slug="sensitive-session",
        platform_session_id="token=SECRET123",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )

    assert "SECRET123" not in state.state_path.name
    assert "SECRET123" not in state.state_path.read_text(encoding="utf-8")

    record = stop_auto_capture(
        root=tmp_path,
        agent="claude",
        platform_session_id="token=SECRET123",
        now=FIXED_NOW,
    )

    assert "SECRET123" not in record.jsonl_path.read_text(encoding="utf-8")


def test_sensitive_session_id_resume_keeps_raw_id_in_memory_without_persisting_it(tmp_path):
    first = start_auto_capture(
        agent="claude",
        slug="sensitive-resume",
        platform_session_id="token=SECRET123",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    second = start_auto_capture(
        agent="claude",
        slug="sensitive-resume",
        platform_session_id="token=SECRET123",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )

    assert second.platform_session_id == "token=SECRET123"
    assert second.active_path == first.active_path
    assert "SECRET123" not in first.active_path.read_text(encoding="utf-8")


def test_redacted_argv_preserves_command_names_and_redacts_secret_values():
    assert _redacted_argv(["secret-tool", "lookup", "service"]) == [
        "secret-tool",
        "lookup",
        "service",
    ]
    assert _redacted_argv(["codex", "--password", "hunter2", "--model", "gpt-5"]) == [
        "codex",
        "--password",
        "[REDACTED]",
        "--model",
        "gpt-5",
    ]
    assert _redacted_argv(["codex", "--password=hunter2", "API_TOKEN=abc123"]) == [
        "codex",
        "--password=[REDACTED]",
        "API_TOKEN=[REDACTED]",
    ]


def test_wrapper_argv_metadata_keeps_redacted_argv_shape(tmp_path):
    state = start_auto_capture(
        agent="codex",
        slug="argv-shape",
        platform_session_id="argv-session",
        root=tmp_path,
        machine="studio",
        metadata={
            "adapter": "wrapper",
            "argv": _redacted_argv(["secret-tool", "--password", "hunter2"]),
        },
        now=FIXED_NOW,
    )

    events = read_jsonl(state.active_path)
    assert events[2]["payload"]["metadata"]["argv"] == [
        "secret-tool",
        "--password",
        "[REDACTED]",
    ]


def test_cli_metadata_json_argv_is_redacted_before_recording(tmp_path):
    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "auto",
            "start",
            "--agent",
            "codex",
            "--slug",
            "argv-bypass",
            "--session-id",
            "argv-bypass",
            "--machine",
            "studio",
            "--metadata-json",
            '{"argv":["codex","--password","hunter2","API_KEY=raw-secret"]}',
        ]
    )

    assert exit_code == 0
    active_records = list((tmp_path / ".in-progress" / "pipy").glob("*-studio-codex-argv-bypass.jsonl"))
    assert len(active_records) == 1
    active = active_records[0]
    text = active.read_text(encoding="utf-8")
    assert "hunter2" not in text
    assert "raw-secret" not in text

    events = read_jsonl(active)
    assert events[2]["payload"]["metadata"]["argv"] == [
        "codex",
        "--password",
        "[REDACTED]",
        "API_KEY=[REDACTED]",
    ]


def test_wrapped_agent_records_partial_lifecycle(tmp_path):
    return_code = run_wrapped_agent(
        agent="pi",
        slug="wrapper",
        command=[sys.executable, "-c", "raise SystemExit(3)"],
        root=tmp_path,
    )

    assert return_code == 3
    finalized = list((tmp_path / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    events = read_jsonl(finalized[0])
    assert events[0]["agent"] == "pi"
    assert events[0]["partial"] is True
    assert events[-1]["payload"]["return_code"] == 3
