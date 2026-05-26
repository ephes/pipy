from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipy_session import (
    append_event,
    finalize_session,
    init_session,
)
from pipy_session.cli import main
from pipy_session.export import (
    SCHEMA_NAME,
    SCHEMA_VERSION,
    export_session,
)

FIXED_NOW = datetime(2026, 4, 30, 13, 30, 0, tzinfo=UTC)


def _make_record(
    tmp_path: Path,
    *,
    slug: str = "export-basic",
    summary_text: str | None = None,
    extra_events: list[dict[str, object]] | None = None,
) -> Path:
    active = init_session(
        agent="codex",
        slug=slug,
        root=tmp_path,
        machine="studio",
        goal="Export the session safely.",
        now=FIXED_NOW,
    )
    if extra_events is not None:
        for event in extra_events:
            append_event(
                active,
                root=tmp_path,
                event_type=str(event["type"]),
                summary=str(event.get("summary", "")) or None,
                payload=event.get("payload"),  # type: ignore[arg-type]
                now=FIXED_NOW,
            )
    record = finalize_session(active, root=tmp_path, summary_text=summary_text)
    return record.jsonl_path


def test_export_returns_metadata_only_by_default(tmp_path):
    record_path = _make_record(
        tmp_path,
        slug="export-meta-only",
        extra_events=[
            {
                "type": "decision.recorded",
                "summary": "Use metadata-only export by default.",
                "payload": {
                    "raw_model_output": "SECRET completion text",
                    "tool_arguments": {"path": "/etc/secret"},
                },
            }
        ],
    )

    payload = export_session(
        record_path.stem,
        session_root=tmp_path,
    )

    assert payload["schema"] == SCHEMA_NAME
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["transcript_events"] is None

    record_block = payload["record"]
    assert isinstance(record_block, dict)
    assert record_block["stem"] == record_path.stem
    assert record_block["basename"] == record_path.name
    assert record_block["size_bytes"] == record_path.stat().st_size
    assert record_block["started_at"] == "2026-04-30T13:30:00+00:00"
    assert record_block["agent"] == "codex"
    assert record_block["machine"] == "studio"
    assert record_block["slug"] == "export-meta-only"
    assert record_block["partial"] is False

    serialized = json.dumps(payload, sort_keys=True)
    assert "SECRET" not in serialized
    assert "/etc/secret" not in serialized


def test_export_includes_safe_metadata_fields(tmp_path):
    record_path = _make_record(
        tmp_path,
        slug="export-safe-meta",
        extra_events=[
            {"type": "verification.performed", "summary": "uv run pytest passed."},
            {"type": "decision.recorded", "summary": "Keep metadata-first archive."},
        ],
    )

    payload = export_session(record_path.stem, session_root=tmp_path)

    metadata = payload["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["agent"] == "codex"
    assert metadata["machine"] == "studio"
    assert metadata["slug"] == "export-safe-meta"
    assert metadata["project"] == "pipy"
    assert metadata["partial"] is False
    assert metadata["goal"] == "Export the session safely."
    assert metadata["event_count"] == 3
    assert metadata["event_type_counts"] == {
        "decision.recorded": 1,
        "session.started": 1,
        "verification.performed": 1,
    }


def test_export_includes_markdown_summary_when_present(tmp_path):
    record_path = _make_record(
        tmp_path,
        slug="export-with-md",
        summary_text="# Summary\n\nMetadata-first export.",
    )

    payload = export_session(record_path.stem, session_root=tmp_path)

    assert payload["markdown_summary"] == "# Summary\n\nMetadata-first export.\n"
    label = payload["markdown_path_label"]
    assert isinstance(label, str)
    assert label.endswith(".md")
    assert label.startswith("pipy/")


def test_export_markdown_summary_is_null_when_absent(tmp_path):
    record_path = _make_record(tmp_path, slug="export-no-md")

    payload = export_session(record_path.stem, session_root=tmp_path)

    assert payload["markdown_summary"] is None
    assert payload["markdown_path_label"] is None


def test_export_omits_payload_bodies_by_default(tmp_path):
    record_path = _make_record(
        tmp_path,
        slug="export-no-payload",
        extra_events=[
            {
                "type": "decision.recorded",
                "summary": "Payloads must be stripped.",
                "payload": {"secret_field": "DO_NOT_LEAK"},
            },
            {
                "type": "file.changed",
                "summary": "Updated archive policy.",
                "payload": {"path": "/private/sensitive.txt"},
            },
        ],
    )

    payload = export_session(record_path.stem, session_root=tmp_path)

    events = payload["events"]
    assert isinstance(events, list)
    assert len(events) == 3
    for event in events:
        assert isinstance(event, dict)
        assert "payload" not in event
        assert "type" in event

    types = {event["type"] for event in events}
    assert types == {"session.started", "decision.recorded", "file.changed"}

    serialized = json.dumps(payload, sort_keys=True)
    assert "DO_NOT_LEAK" not in serialized
    assert "/private/sensitive.txt" not in serialized
    assert "secret_field" not in serialized


def test_export_omits_raw_model_output(tmp_path):
    active = init_session(
        agent="codex",
        slug="export-no-raw-model-output",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    # Bypass the recorder's structured payload-only API to synthesize a
    # hostile event with raw model output and other top-level secrets.
    forged_event = {
        "type": "assistant.message",
        "timestamp": "2026-04-30T13:30:05+00:00",
        "summary": "Assistant produced text (metadata only is what we keep).",
        "model_output": "RAW_MODEL_OUTPUT_TEXT that must not leak",
        "raw_response": {"choices": [{"message": "RAW_PROVIDER_PAYLOAD"}]},
        "tool_result": "RAW_TOOL_RESULT bytes",
        "diff": "--- a\n+++ b\nLEAK_DIFF",
        "prompt": "RAW_PROMPT contents",
        "secret_token": "sk-LEAKLEAKLEAK",
    }
    with active.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(forged_event, sort_keys=True))
        handle.write("\n")

    record = finalize_session(active, root=tmp_path)

    payload = export_session(record.jsonl_path.stem, session_root=tmp_path)

    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "RAW_MODEL_OUTPUT_TEXT",
        "RAW_PROVIDER_PAYLOAD",
        "RAW_TOOL_RESULT",
        "LEAK_DIFF",
        "RAW_PROMPT",
        "sk-LEAKLEAKLEAK",
        "model_output",
        "raw_response",
        "tool_result",
        "diff",
        "prompt",
        "secret_token",
    ):
        assert forbidden not in serialized, forbidden

    events = payload["events"]
    assert isinstance(events, list)
    assistant_events = [event for event in events if event.get("type") == "assistant.message"]
    assert len(assistant_events) == 1
    assistant_event = assistant_events[0]
    assert set(assistant_event.keys()) <= {
        "type",
        "timestamp",
        "summary",
        "agent",
        "machine",
        "project",
        "slug",
        "partial",
        "sequence",
    }
    assert assistant_event["summary"].startswith("Assistant produced text")


def test_export_with_include_transcript_reads_sidecar(tmp_path, monkeypatch):
    record_path = _make_record(tmp_path, slug="export-include-transcript")

    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    sidecar_path = transcript_dir / f"{record_path.stem}.jsonl"
    transcript_lines = [
        {
            "type": "user.message",
            "recorded_at": "2026-04-30T13:30:01+00:00",
            "discriminator": "pipy-transcript-sidecar",
            "payload": {"text": "Hello from the user."},
        },
        {
            "type": "assistant.message",
            "recorded_at": "2026-04-30T13:30:02+00:00",
            "discriminator": "pipy-transcript-sidecar",
            "payload": {"text": "Hello back."},
        },
    ]
    with sidecar_path.open("w", encoding="utf-8") as handle:
        for entry in transcript_lines:
            handle.write(json.dumps(entry, sort_keys=True))
            handle.write("\n")

    monkeypatch.setenv("PIPY_TRANSCRIPT_DIR", str(transcript_dir))

    payload = export_session(
        record_path.stem,
        include_transcript=True,
        session_root=tmp_path,
    )

    transcript_events = payload["transcript_events"]
    assert isinstance(transcript_events, list)
    assert len(transcript_events) == 2
    assert transcript_events[0]["type"] == "user.message"
    assert transcript_events[1]["payload"]["text"] == "Hello back."
    assert payload["transcript_path_label"] == str(sidecar_path)


def test_export_with_include_transcript_raises_if_sidecar_missing(
    tmp_path, monkeypatch
):
    record_path = _make_record(tmp_path, slug="export-missing-transcript")

    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    monkeypatch.setenv("PIPY_TRANSCRIPT_DIR", str(transcript_dir))

    with pytest.raises(FileNotFoundError):
        export_session(
            record_path.stem,
            include_transcript=True,
            session_root=tmp_path,
        )


def test_export_unknown_record_raises_lookup_error(tmp_path):
    with pytest.raises(LookupError):
        export_session(
            "2026-04-30T133000Z-studio-codex-does-not-exist",
            session_root=tmp_path,
        )


def test_export_rejects_absolute_path_argument(tmp_path):
    record_path = _make_record(tmp_path, slug="export-absolute-path")

    with pytest.raises(ValueError):
        export_session(str(record_path), session_root=tmp_path)


def test_export_cli_emits_json_to_stdout(tmp_path, capsys):
    record_path = _make_record(
        tmp_path,
        slug="export-cli-json",
        summary_text="# Summary\n\nCLI export.",
        extra_events=[
            {
                "type": "decision.recorded",
                "summary": "CLI export keeps metadata-only.",
                "payload": {"raw_model_output": "CLI_SECRET_OUTPUT"},
            }
        ],
    )

    exit_code = main(
        ["--root", str(tmp_path), "export", record_path.stem]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    parsed = json.loads(captured.out)
    assert parsed["schema"] == SCHEMA_NAME
    assert parsed["schema_version"] == SCHEMA_VERSION
    assert parsed["transcript_events"] is None
    assert parsed["markdown_summary"] == "# Summary\n\nCLI export.\n"
    assert "CLI_SECRET_OUTPUT" not in captured.out
    assert "raw_model_output" not in captured.out


def test_export_cli_supports_include_transcript_flag(tmp_path, monkeypatch, capsys):
    record_path = _make_record(tmp_path, slug="export-cli-transcript")

    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    sidecar_path = transcript_dir / f"{record_path.stem}.jsonl"
    with sidecar_path.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "user.message",
                    "recorded_at": "2026-04-30T13:30:03+00:00",
                    "discriminator": "pipy-transcript-sidecar",
                    "payload": {"text": "CLI transcript ok."},
                },
                sort_keys=True,
            )
        )
        handle.write("\n")
    monkeypatch.setenv("PIPY_TRANSCRIPT_DIR", str(transcript_dir))

    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "export",
            record_path.stem,
            "--include-transcript",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    parsed = json.loads(captured.out)
    transcript_events = parsed["transcript_events"]
    assert isinstance(transcript_events, list)
    assert transcript_events[0]["payload"]["text"] == "CLI transcript ok."


def test_export_cli_reports_missing_record(tmp_path, capsys):
    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "export",
            "2026-04-30T133000Z-studio-codex-missing",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "missing" in captured.err
    assert captured.out == ""


def test_export_does_not_modify_archive(tmp_path):
    record_path = _make_record(
        tmp_path,
        slug="export-no-mutation",
        summary_text="# Summary\n\nNo mutation.",
    )
    markdown_path = record_path.with_suffix(".md")

    before_jsonl = record_path.stat()
    before_md = markdown_path.stat()
    before_jsonl_bytes = record_path.read_bytes()
    before_md_bytes = markdown_path.read_bytes()

    export_session(record_path.stem, session_root=tmp_path)

    after_jsonl = record_path.stat()
    after_md = markdown_path.stat()

    assert after_jsonl.st_size == before_jsonl.st_size
    assert after_jsonl.st_mtime_ns == before_jsonl.st_mtime_ns
    assert after_md.st_size == before_md.st_size
    assert after_md.st_mtime_ns == before_md.st_mtime_ns
    assert record_path.read_bytes() == before_jsonl_bytes
    assert markdown_path.read_bytes() == before_md_bytes
