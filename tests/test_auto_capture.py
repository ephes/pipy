from __future__ import annotations

import json
import io
import sys
from datetime import UTC, datetime
from pathlib import Path

from pipy_session import (
    append_auto_event,
    finalize_session,
    handle_claude_hook,
    init_session,
    prune_auto_capture_state,
    reference_pi_session,
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


def test_auto_capture_does_not_resume_state_pointing_outside_active_dir(tmp_path):
    first = start_auto_capture(
        agent="claude",
        slug="outside-active",
        platform_session_id="outside-active",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"type":"session.started"}\n', encoding="utf-8")
    state_payload = json.loads(first.state_path.read_text(encoding="utf-8"))
    state_payload["active_path"] = str(outside)
    first.state_path.write_text(json.dumps(state_payload) + "\n", encoding="utf-8")

    second = start_auto_capture(
        agent="claude",
        slug="outside-active",
        platform_session_id="outside-active",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )

    assert second.active_path != outside
    assert second.active_path != first.active_path
    assert outside.read_text(encoding="utf-8") == '{"type":"session.started"}\n'


def test_prune_dry_run_reports_stale_state_without_deleting(tmp_path):
    state = start_auto_capture(
        agent="claude",
        slug="dry-run-prune",
        platform_session_id="dry-run-session",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    state.active_path.unlink()

    results = prune_auto_capture_state(root=tmp_path, dry_run=True)

    assert [(result.path, result.reason, result.removed) for result in results] == [
        (state.state_path, "active-not-found", False)
    ]
    assert state.state_path.exists()


def test_prune_removes_orphaned_state_when_active_path_is_missing(tmp_path):
    state = start_auto_capture(
        agent="claude",
        slug="orphan-prune",
        platform_session_id="orphan-prune",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    state.active_path.unlink()

    results = prune_auto_capture_state(root=tmp_path)

    assert [(result.path, result.reason, result.removed) for result in results] == [
        (state.state_path, "active-not-found", True)
    ]
    assert not state.state_path.exists()


def test_prune_removes_corrupt_and_non_object_state(tmp_path):
    directory = state_dir(tmp_path)
    directory.mkdir(parents=True)
    corrupt = directory / "claude-corrupt.json"
    non_object = directory / "claude-list.json"
    corrupt.write_text("{not-json", encoding="utf-8")
    non_object.write_text("[]\n", encoding="utf-8")

    results = prune_auto_capture_state(root=tmp_path)

    assert [(result.path.name, result.reason, result.removed) for result in results] == [
        ("claude-corrupt.json", "invalid-json", True),
        ("claude-list.json", "invalid-state", True),
    ]
    assert not corrupt.exists()
    assert not non_object.exists()


def test_prune_preserves_live_state_referencing_active_jsonl(tmp_path):
    state = start_auto_capture(
        agent="claude",
        slug="live-prune",
        platform_session_id="live-prune",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )

    results = prune_auto_capture_state(root=tmp_path)

    assert results == []
    assert state.state_path.exists()
    assert state.active_path.exists()


def test_prune_does_not_delete_active_jsonl_files(tmp_path):
    live = start_auto_capture(
        agent="codex",
        slug="keep-active",
        platform_session_id="keep-active",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    bad_state = state_dir(tmp_path) / "codex-bad.json"
    bad_state.write_text('{"active_path":""}\n', encoding="utf-8")

    results = prune_auto_capture_state(root=tmp_path)

    assert [(result.path, result.reason) for result in results] == [
        (bad_state, "missing-active-path")
    ]
    assert not bad_state.exists()
    assert live.active_path.exists()
    assert live.state_path.exists()


def test_prune_does_not_delete_finalized_archive_files(tmp_path):
    active = init_session(
        agent="codex",
        slug="archive-preserved",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    finalized = finalize_session(active, root=tmp_path)
    stale_state = state_dir(tmp_path) / "codex-finalized.json"
    stale_state.parent.mkdir(parents=True, exist_ok=True)
    stale_state.write_text(
        json.dumps({"active_path": str(finalized.jsonl_path)}) + "\n",
        encoding="utf-8",
    )

    results = prune_auto_capture_state(root=tmp_path)

    assert [(result.path, result.reason) for result in results] == [
        (stale_state, "non-active-record")
    ]
    assert not stale_state.exists()
    assert finalized.jsonl_path.exists()


def test_prune_ignores_partial_state_staging_files(tmp_path):
    directory = state_dir(tmp_path)
    directory.mkdir(parents=True)
    partial = directory / "claude-staged.json.partial"
    partial.write_text("{not-json", encoding="utf-8")

    results = prune_auto_capture_state(root=tmp_path)

    assert results == []
    assert partial.exists()


def test_cli_auto_prune_uses_root_and_reports_results(tmp_path, capsys):
    state = start_auto_capture(
        agent="claude",
        slug="cli-prune",
        platform_session_id="cli-prune",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    state.active_path.unlink()

    dry_run_code = main(["--root", str(tmp_path), "auto", "prune", "--dry-run"])
    dry_run = capsys.readouterr()

    assert dry_run_code == 0
    assert f"would-remove\t{state.state_path}\tactive-not-found" in dry_run.out
    assert "summary\twould-remove\t1" in dry_run.out
    assert state.state_path.exists()

    prune_code = main(["--root", str(tmp_path), "auto", "prune"])
    pruned = capsys.readouterr()

    assert prune_code == 0
    assert f"removed\t{state.state_path}\tactive-not-found" in pruned.out
    assert "summary\tremoved\t1" in pruned.out
    assert not state.state_path.exists()


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


def test_reference_pi_session_records_metadata_without_copying_raw_content(tmp_path):
    raw_secret = "RAW_PI_PROMPT token=SECRET123 assistant output"
    pi_session = tmp_path / ".pi" / "agent" / "sessions" / "native.jsonl"
    pi_session.parent.mkdir(parents=True)
    pi_session.write_text(f'{{"message":"{raw_secret}"}}\n', encoding="utf-8")

    record = reference_pi_session(
        pi_session,
        root=tmp_path / "pipy-sessions",
        slug="pi-native-reference",
        machine="studio",
        now=FIXED_NOW,
    )

    jsonl_text = record.jsonl_path.read_text(encoding="utf-8")
    markdown_text = record.markdown_path.read_text(encoding="utf-8")
    assert raw_secret not in jsonl_text
    assert raw_secret not in markdown_text
    assert str(pi_session) not in jsonl_text
    assert str(pi_session) not in markdown_text

    events = read_jsonl(record.jsonl_path)
    assert events[0]["agent"] == "pi"
    assert events[0]["partial"] is True
    reference_event = [event for event in events if event["type"] == "pi.session_reference"][0]
    payload = reference_event["payload"]
    assert payload["adapter"] == "pi-session-reference"
    assert payload["source_filename"] == "native.jsonl"
    assert payload["source_file_size_bytes"] == len(pi_session.read_bytes())
    assert len(payload["source_absolute_path_sha256"]) == 64
    assert payload["source_path_stored"] is False
    assert payload["raw_content_imported"] is False
    assert "not a transcript import" in markdown_text
    assert "Raw Pi session content copied: no" in markdown_text


def test_cli_auto_reference_pi_creates_finalized_partial_record(tmp_path, capsys):
    raw_secret = "RAW_PI_TOOL_OUTPUT password hunter2"
    pi_session = tmp_path / "pi-session.jsonl"
    pi_session.write_text(raw_secret, encoding="utf-8")
    root = tmp_path / "sessions"

    exit_code = main(
        [
            "--root",
            str(root),
            "auto",
            "reference-pi",
            str(pi_session),
            "--slug",
            "cli-pi-reference",
            "--machine",
            "studio",
            "--summary",
            "Reviewed Pi-native session externally.",
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    paths = [line for line in output.out.splitlines() if line]
    assert len(paths) == 2
    jsonl_path = paths[0]
    markdown_path = paths[1]
    combined = Path(jsonl_path).read_text(encoding="utf-8") + Path(markdown_path).read_text(
        encoding="utf-8"
    )
    assert raw_secret not in combined
    assert str(pi_session) not in combined
    assert "Reviewed Pi-native session externally." in combined
    assert output.err == ""


def test_reference_pi_session_redacts_sensitive_summary_lines_only(tmp_path):
    pi_session = tmp_path / "pi-session.jsonl"
    pi_session.write_text('{"message":"safe native content"}\n', encoding="utf-8")

    record = reference_pi_session(
        pi_session,
        root=tmp_path / "sessions",
        slug="summary-redaction",
        machine="studio",
        summary="Line 1\nLine 2 with password=hunter2\nLine 3",
        now=FIXED_NOW,
    )

    markdown_text = record.markdown_path.read_text(encoding="utf-8")
    assert "Line 1\n[REDACTED]\nLine 3" in markdown_text
    assert "hunter2" not in markdown_text


def test_reference_pi_session_rejects_missing_or_directory_path(tmp_path):
    missing = tmp_path / "missing.jsonl"
    try:
        reference_pi_session(missing, root=tmp_path)
    except FileNotFoundError as exc:
        assert "Pi session file not found" in str(exc)
    else:
        raise AssertionError("missing Pi session path should fail")

    try:
        reference_pi_session(tmp_path, root=tmp_path)
    except ValueError as exc:
        assert "Pi session path must be a file" in str(exc)
    else:
        raise AssertionError("directory Pi session path should fail")
