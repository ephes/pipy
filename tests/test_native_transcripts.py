"""Slice 11 tests: opt-in transcript sidecar.

These tests pin the privacy invariants of the `--archive-transcript`
sidecar: when the flag is off, no sidecar file is created and the
metadata archive is unchanged; when the flag is on, raw turns land in
the sidecar JSONL outside the pipy session archive; `pipy-session
list/search/inspect` do not see the transcripts directory because it
lives outside the configured session root.
"""

from __future__ import annotations

import io
import json
import stat
from pathlib import Path

import pytest

from pipy_harness.adapters import PipyNativeToolReplAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.cli import build_parser
from pipy_harness.models import RunRequest
from pipy_harness.native import (
    FakeNativeProvider,
    ProviderToolCall,
)
from pipy_harness.native.transcripts import (
    DEFAULT_TRANSCRIPT_DIR,
    SENSITIVE_MARKER,
    TRANSCRIPT_DIR_ENV,
    TranscriptSink,
    default_transcript_dir,
    new_transcript_id,
)


class _NullEventSink:
    def emit(self, event_type, *, summary, payload=None):
        return None


def test_transcript_sink_default_path_is_under_pipy_state_home():
    assert DEFAULT_TRANSCRIPT_DIR.as_posix().endswith(
        ".local/state/pipy/transcripts"
    )


def test_default_transcript_dir_honors_environment_override(monkeypatch, tmp_path):
    monkeypatch.setenv(TRANSCRIPT_DIR_ENV, str(tmp_path / "custom"))

    resolved = default_transcript_dir()

    assert resolved == tmp_path / "custom"


def test_new_transcript_id_is_prefixed_and_unique():
    one = new_transcript_id()
    two = new_transcript_id()

    assert one != two
    assert one.startswith("pipy-transcript-")


def test_transcript_sink_does_not_create_file_until_append(tmp_path):
    sink = TranscriptSink(directory=tmp_path)

    assert sink.path.parent == tmp_path
    assert not sink.path.exists()


def test_transcript_sink_writes_jsonl_lines_with_sensitive_marker(tmp_path):
    sink = TranscriptSink(directory=tmp_path)
    sink.append("user", {"content": "hello"})
    sink.append("assistant", {"content": "hi", "tool_calls": []})
    sink.close()

    raw = sink.path.read_text(encoding="utf-8").splitlines()

    assert len(raw) == 2
    first = json.loads(raw[0])
    assert first["type"] == "user"
    assert first["sensitive_marker"] == SENSITIVE_MARKER
    assert first["payload"]["content"] == "hello"
    mode = stat.S_IMODE(sink.path.stat().st_mode)
    assert mode == 0o600


def test_transcript_sink_rejects_path_traversal_transcript_id(tmp_path):
    sink = TranscriptSink(directory=tmp_path, transcript_id="../outside")

    with pytest.raises(ValueError, match="filename-safe"):
        _ = sink.path


def test_transcript_sink_refuses_preexisting_symlink(tmp_path):
    outside = tmp_path / "outside.jsonl"
    outside.write_text("", encoding="utf-8")
    link = tmp_path / "fixed.jsonl"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    sink = TranscriptSink(directory=tmp_path, transcript_id="fixed")

    with pytest.raises(FileExistsError):
        sink.append("session", {"x": 1})

    assert outside.read_text(encoding="utf-8") == ""


def test_transcript_sink_rejects_unknown_event_type(tmp_path):
    sink = TranscriptSink(directory=tmp_path)

    with pytest.raises(ValueError, match="unsupported transcript event type"):
        sink.append("unsupported", {"x": 1})


def test_transcript_sink_rejects_non_dict_payload(tmp_path):
    sink = TranscriptSink(directory=tmp_path)

    with pytest.raises(ValueError, match="must be a dict"):
        sink.append("user", "string-payload")  # type: ignore[arg-type]


def test_archive_transcript_flag_defaults_to_false():
    parser = build_parser()
    args = parser.parse_args(["repl"])

    assert args.archive_transcript is False


def test_archive_transcript_flag_round_trips():
    parser = build_parser()
    args = parser.parse_args(["repl", "--archive-transcript"])

    assert args.archive_transcript is True


def test_adapter_does_not_create_transcript_when_flag_off(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(TRANSCRIPT_DIR_ENV, str(tmp_path / "transcripts"))
    provider = FakeNativeProvider(
        supports_tool_calls=True, programmable_tool_calls=((),)
    )
    adapter = PipyNativeToolReplAdapter(
        provider=provider,
        input_stream=io.StringIO("hi\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    prepared = adapter.prepare(
        RunRequest(
            agent="pipy-native",
            slug="test",
            command=[],
            cwd=tmp_path,
            goal="t",
            capture_policy=CapturePolicy(),
        )
    )

    adapter.run(prepared, event_sink=_NullEventSink(), capture_policy=CapturePolicy())

    assert not (tmp_path / "transcripts").exists()


def test_adapter_writes_transcript_when_sink_supplied(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(TRANSCRIPT_DIR_ENV, str(tmp_path / "transcripts"))
    call = ProviderToolCall(
        provider_correlation_id="call_1",
        tool_name="read",
        arguments_json='{"path": "notes.txt"}',
    )
    (tmp_path / "notes.txt").write_text("body\n", encoding="utf-8")
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_tool_calls=((call,), ()),
        final_text="done",
    )
    sink = TranscriptSink(directory=tmp_path / "transcripts")
    adapter = PipyNativeToolReplAdapter(
        provider=provider,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
        transcript_sink=sink,
    )
    prepared = adapter.prepare(
        RunRequest(
            agent="pipy-native",
            slug="test",
            command=[],
            cwd=tmp_path,
            goal="t",
            capture_policy=CapturePolicy(),
        )
    )

    adapter.run(prepared, event_sink=_NullEventSink(), capture_policy=CapturePolicy())

    assert sink.path.exists()
    records = [
        json.loads(line)
        for line in sink.path.read_text(encoding="utf-8").splitlines()
    ]
    types = [record["type"] for record in records]
    assert "user" in types
    assert "assistant" in types
    assert "tool_result" in types
    assert "session" in types


def test_transcript_sidecar_lives_outside_pipy_session_root(monkeypatch, tmp_path):
    monkeypatch.setenv(TRANSCRIPT_DIR_ENV, str(tmp_path / "transcripts"))
    monkeypatch.setenv("PIPY_SESSION_DIR", str(tmp_path / "sessions"))

    sink = TranscriptSink(directory=default_transcript_dir())
    sink.append("session", {"x": 1})
    sink.close()

    session_root = tmp_path / "sessions"
    transcript_path = sink.path
    assert session_root not in transcript_path.parents


def test_pipy_session_list_does_not_walk_transcript_directory(
    tmp_path: Path, monkeypatch
):
    """`pipy-session list` operates on `PIPY_SESSION_DIR`; the transcript
    sidecar lives at a different path, so the catalog cannot reach it.
    """

    monkeypatch.setenv(TRANSCRIPT_DIR_ENV, str(tmp_path / "transcripts"))
    monkeypatch.setenv("PIPY_SESSION_DIR", str(tmp_path / "sessions"))
    sink = TranscriptSink(directory=tmp_path / "transcripts")
    sink.append("session", {"x": 1})
    sink.close()

    from pipy_session.catalog import list_finalized_sessions

    records = list_finalized_sessions(root=tmp_path / "sessions")

    assert records == []
