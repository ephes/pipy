"""Tests for metadata-only session resume."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipy_harness.native.session_resume import (
    ResumeContext,
    compose_resume_system_block,
    resume_session_from_archive,
)
from pipy_session import append_event, finalize_session, init_session
from pipy_session.cli import main

FIXED_NOW = datetime(2026, 4, 30, 13, 30, 0, tzinfo=UTC)


def _make_record(
    tmp_path: Path,
    *,
    slug: str = "resume-basic",
    summary_text: str | None = None,
    extra_events: list[dict[str, object]] | None = None,
) -> Path:
    active = init_session(
        agent="codex",
        slug=slug,
        root=tmp_path,
        machine="studio",
        goal="Resume the session safely.",
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


def test_resume_returns_metadata_only_context(tmp_path: Path) -> None:
    record_path = _make_record(
        tmp_path,
        slug="resume-meta-only",
        extra_events=[
            {
                "type": "native.session.started",
                "summary": "Native session started.",
                "payload": {
                    "provider": "openai",
                    "model_id": "gpt-5",
                    "cwd_sha256": "ABC123CWDHASH",
                },
            },
            {
                "type": "native.session.completed",
                "summary": "Native session completed.",
                "payload": {
                    "provider": "openai",
                    "model_id": "gpt-5",
                    "turn_count": 4,
                },
            },
        ],
    )

    resume_context = resume_session_from_archive(
        record_path.stem,
        session_root=tmp_path,
    )

    assert isinstance(resume_context, ResumeContext)
    assert resume_context.prior_session_id == record_path.stem
    assert resume_context.prior_provider_name == "openai"
    assert resume_context.prior_model_id == "gpt-5"
    assert resume_context.prior_turn_count == 4
    assert resume_context.prior_workspace_hash == "ABC123CWDHASH"
    assert resume_context.prior_started_at == "2026-04-30T13:30:00+00:00"


def test_resume_reads_provider_and_model_from_session_started_event(
    tmp_path: Path,
) -> None:
    record_path = _make_record(
        tmp_path,
        slug="resume-provider-model",
        extra_events=[
            {
                "type": "native.session.started",
                "summary": "Native session started.",
                "payload": {
                    "provider": "anthropic",
                    "model_id": "claude-opus",
                    "cwd_sha256": "WORKSPACEHASHXYZ",
                },
            },
        ],
    )

    resume_context = resume_session_from_archive(
        record_path.stem,
        session_root=tmp_path,
    )

    assert resume_context.prior_provider_name == "anthropic"
    assert resume_context.prior_model_id == "claude-opus"
    assert resume_context.prior_workspace_hash == "WORKSPACEHASHXYZ"


def test_resume_reads_prior_turn_count_from_finalized_metadata(
    tmp_path: Path,
) -> None:
    record_path = _make_record(
        tmp_path,
        slug="resume-turn-count",
        extra_events=[
            {
                "type": "native.session.started",
                "summary": "Started.",
                "payload": {"provider": "openai", "model_id": "gpt-5"},
            },
            {
                "type": "native.provider.completed",
                "summary": "Turn 1 done.",
                "payload": {"provider": "openai", "model_id": "gpt-5"},
            },
            {
                "type": "native.provider.completed",
                "summary": "Turn 2 done.",
                "payload": {"provider": "openai", "model_id": "gpt-5"},
            },
            {
                "type": "native.session.completed",
                "summary": "Completed.",
                "payload": {
                    "provider": "openai",
                    "model_id": "gpt-5",
                    "turn_count": 2,
                },
            },
        ],
    )

    resume_context = resume_session_from_archive(
        record_path.stem,
        session_root=tmp_path,
    )

    assert resume_context.prior_turn_count == 2


def test_resume_reads_summary_when_markdown_present(tmp_path: Path) -> None:
    record_path = _make_record(
        tmp_path,
        slug="resume-with-md",
        summary_text="# Summary\n\nResume picks this up.",
    )

    resume_context = resume_session_from_archive(
        record_path.stem,
        session_root=tmp_path,
    )

    assert resume_context.prior_summary == "# Summary\n\nResume picks this up.\n"


def test_resume_summary_is_none_when_no_markdown(tmp_path: Path) -> None:
    record_path = _make_record(tmp_path, slug="resume-no-md")

    resume_context = resume_session_from_archive(
        record_path.stem,
        session_root=tmp_path,
    )

    assert resume_context.prior_summary is None


def test_resume_unknown_record_raises_lookup_error(tmp_path: Path) -> None:
    with pytest.raises(LookupError):
        resume_session_from_archive(
            "2026-04-30T133000Z-studio-codex-not-real",
            session_root=tmp_path,
        )


def test_resume_malformed_first_event_raises_value_error(tmp_path: Path) -> None:
    # Synthesize a finalized record whose first event is not session.started
    # by writing the file directly into the finalized archive layout.
    archive_dir = tmp_path / "pipy" / "2026" / "04"
    archive_dir.mkdir(parents=True)
    record_path = (
        archive_dir / "2026-04-30T133000Z-studio-codex-malformed-first.jsonl"
    )
    with record_path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "not.session.started"}, sort_keys=True))
        handle.write("\n")

    with pytest.raises(ValueError):
        resume_session_from_archive(record_path.stem, session_root=tmp_path)


def test_resume_does_not_read_payload_bodies(tmp_path: Path) -> None:
    record_path = _make_record(
        tmp_path,
        slug="resume-no-payload-leak",
        summary_text="# Summary\n\nNo payload leak.",
        extra_events=[
            {
                "type": "native.session.started",
                "summary": "Started.",
                "payload": {
                    "provider": "openai",
                    "model_id": "gpt-5",
                    "cwd_sha256": "SAFEHASH",
                    "secret_field": "DO_NOT_LEAK",
                    "prompt": "RAW_PROMPT_TEXT",
                    "raw_response": {"completion": "RAW_MODEL_TEXT"},
                    "tool_result": "RAW_TOOL_RESULT_BYTES",
                    "diff": "--- a\n+++ b\nLEAK_DIFF",
                    "secret_token": "sk-LEAKLEAK",
                },
            },
            {
                "type": "decision.recorded",
                "summary": "Decision summary.",
                "payload": {
                    "secret_field": "ANOTHER_SECRET",
                    "raw_model_output": "MORE_LEAK",
                },
            },
            {
                "type": "native.session.completed",
                "summary": "Completed.",
                "payload": {
                    "provider": "openai",
                    "model_id": "gpt-5",
                    "turn_count": 1,
                    "raw_response": {"completion": "RAW_FINAL_MODEL_TEXT"},
                },
            },
        ],
    )

    resume_context = resume_session_from_archive(
        record_path.stem,
        session_root=tmp_path,
    )

    serialized = json.dumps(resume_context.to_dict(), sort_keys=True)
    for forbidden in (
        "DO_NOT_LEAK",
        "RAW_PROMPT_TEXT",
        "RAW_MODEL_TEXT",
        "RAW_TOOL_RESULT_BYTES",
        "LEAK_DIFF",
        "sk-LEAKLEAK",
        "ANOTHER_SECRET",
        "MORE_LEAK",
        "RAW_FINAL_MODEL_TEXT",
        "secret_field",
        "secret_token",
        "raw_response",
        "tool_result",
        "raw_model_output",
        "diff",
        "prompt",
    ):
        assert forbidden not in serialized, forbidden

    # Allowlisted metadata is preserved.
    assert resume_context.prior_provider_name == "openai"
    assert resume_context.prior_model_id == "gpt-5"
    assert resume_context.prior_workspace_hash == "SAFEHASH"
    assert resume_context.prior_turn_count == 1


def test_compose_block_contains_safe_labels_only(tmp_path: Path) -> None:
    record_path = _make_record(
        tmp_path,
        slug="resume-compose-labels",
        summary_text="# Summary\n\nUSER_TEXT_FROM_SUMMARY should not leak.",
        extra_events=[
            {
                "type": "native.session.started",
                "summary": "Started.",
                "payload": {
                    "provider": "anthropic",
                    "model_id": "claude-opus",
                    "cwd_sha256": "WORKSPACEHASH",
                    "raw_prompt": "USER_PROMPT_TEXT_LEAK",
                },
            },
            {
                "type": "native.session.completed",
                "summary": "Completed.",
                "payload": {
                    "provider": "anthropic",
                    "model_id": "claude-opus",
                    "turn_count": 3,
                },
            },
        ],
    )

    resume_context = resume_session_from_archive(
        record_path.stem,
        session_root=tmp_path,
    )

    block = compose_resume_system_block(resume_context)

    assert resume_context.prior_session_id in block
    assert "anthropic" in block
    assert "claude-opus" in block
    assert "3 prior turns" in block
    assert "Resumed from session" in block

    # The block carries only safe labels — never user text from the prior run.
    assert "USER_TEXT_FROM_SUMMARY" not in block
    assert "USER_PROMPT_TEXT_LEAK" not in block
    # Summary content must not be folded into the prompt block even though it
    # was loaded into the ResumeContext for inspection.
    assert "Summary" not in block


def test_resume_info_cli_emits_json_to_stdout(tmp_path: Path, capsys) -> None:
    record_path = _make_record(
        tmp_path,
        slug="resume-cli-json",
        summary_text="# Summary\n\nCLI resume.",
        extra_events=[
            {
                "type": "native.session.started",
                "summary": "Started.",
                "payload": {
                    "provider": "openai",
                    "model_id": "gpt-5",
                    "cwd_sha256": "CLIHASH",
                },
            },
            {
                "type": "native.session.completed",
                "summary": "Completed.",
                "payload": {
                    "provider": "openai",
                    "model_id": "gpt-5",
                    "turn_count": 2,
                },
            },
        ],
    )

    exit_code = main(
        ["--root", str(tmp_path), "resume-info", record_path.stem]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    parsed = json.loads(captured.out)
    assert parsed["prior_session_id"] == record_path.stem
    assert parsed["prior_provider_name"] == "openai"
    assert parsed["prior_model_id"] == "gpt-5"
    assert parsed["prior_turn_count"] == 2
    assert parsed["prior_workspace_hash"] == "CLIHASH"
    assert parsed["prior_summary"] == "# Summary\n\nCLI resume.\n"


def test_resume_info_cli_reports_missing_record(tmp_path: Path, capsys) -> None:
    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "resume-info",
            "2026-04-30T133000Z-studio-codex-missing",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "missing" in captured.err
    assert captured.out == ""
