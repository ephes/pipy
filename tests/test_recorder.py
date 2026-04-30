from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest

from pipy_session import (
    FinalizedRecordError,
    append_event,
    finalize_session,
    init_session,
    resolve_active_path,
    resolve_session_root,
)


FIXED_NOW = datetime(2026, 4, 30, 13, 30, 0, tzinfo=UTC)


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_default_path_resolution_uses_home(monkeypatch, tmp_path):
    monkeypatch.delenv("PIPY_SESSION_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert resolve_session_root() == tmp_path / ".local" / "state" / "pipy" / "sessions"


def test_environment_override_via_pipy_session_dir(monkeypatch, tmp_path):
    configured = tmp_path / "custom-sessions"
    monkeypatch.setenv("PIPY_SESSION_DIR", str(configured))

    assert resolve_session_root() == configured


def test_init_creates_active_session_under_in_progress(tmp_path):
    path = init_session(
        agent="codex",
        slug="some-topic",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )

    assert path == (
        tmp_path
        / ".in-progress"
        / "pipy"
        / "2026-04-30T133000Z-studio-codex-some-topic.jsonl"
    )
    assert path.exists()

    events = read_jsonl(path)
    assert events == [
        {
            "agent": "codex",
            "machine": "studio",
            "project": "pipy",
            "slug": "some-topic",
            "timestamp": "2026-04-30T13:30:00+00:00",
            "type": "session.started",
        }
    ]


def test_append_writes_valid_jsonl_events(tmp_path):
    path = init_session(
        agent="codex",
        slug="append-test",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )

    append_event(
        path,
        root=tmp_path,
        event_type="decision.recorded",
        summary="Use active files until finalize.",
        payload={"reason": "sync safety"},
        now=FIXED_NOW,
    )

    events = read_jsonl(path)
    assert len(events) == 2
    assert events[1] == {
        "payload": {"reason": "sync safety"},
        "summary": "Use active files until finalize.",
        "timestamp": "2026-04-30T13:30:00+00:00",
        "type": "decision.recorded",
    }


def test_append_supports_partial_reconstruction_records(tmp_path):
    path = init_session(
        agent="codex",
        slug="partial-test",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
        partial=True,
    )

    events = read_jsonl(path)
    assert events[0]["partial"] is True
    assert events[1]["type"] == "capture.limitations"
    assert "Partial reconstruction" in events[1]["summary"]


def test_finalize_moves_active_record_to_project_year_month_archive(tmp_path):
    active = init_session(
        agent="codex",
        slug="finalize-test",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )

    record = finalize_session(active, root=tmp_path)

    assert not active.exists()
    assert record.markdown_path is None
    assert record.jsonl_path.parent == tmp_path / "pipy" / "2026" / "04"
    assert record.jsonl_path.name == "2026-04-30T133000Z-studio-codex-finalize-test.jsonl"
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{6}Z-studio-codex-finalize-test\.jsonl",
        record.jsonl_path.name,
    )
    assert read_jsonl(record.jsonl_path)[0]["type"] == "session.started"


def test_finalize_refuses_to_overwrite_existing_finalized_jsonl(tmp_path):
    active = init_session(
        agent="codex",
        slug="existing-final-test",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    final_dir = tmp_path / "pipy" / "2026" / "04"
    final_dir.mkdir(parents=True)
    final_path = final_dir / active.name
    final_path.write_text('{"type":"session.started"}\n', encoding="utf-8")

    with pytest.raises(FileExistsError, match="finalized JSONL already exists"):
        finalize_session(active, root=tmp_path)

    assert active.exists()
    assert final_path.read_text(encoding="utf-8") == '{"type":"session.started"}\n'


def test_normal_apis_do_not_modify_finalized_records(tmp_path):
    active = init_session(
        agent="codex",
        slug="immutable-test",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    record = finalize_session(active, root=tmp_path)
    before = record.jsonl_path.read_text(encoding="utf-8")

    with pytest.raises(FinalizedRecordError):
        append_event(
            record.jsonl_path,
            root=tmp_path,
            event_type="summary.corrected",
            summary="Do not mutate finalized records.",
        )

    assert record.jsonl_path.read_text(encoding="utf-8") == before


def test_finalize_can_create_matching_markdown_summary_from_text(tmp_path):
    active = init_session(
        agent="codex",
        slug="summary-test",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )

    record = finalize_session(active, root=tmp_path, summary_text="# Summary\n\nDone.")

    assert record.markdown_path == record.jsonl_path.with_suffix(".md")
    assert record.markdown_path.read_text(encoding="utf-8") == "# Summary\n\nDone.\n"


def test_finalize_creates_markdown_for_explicit_empty_summary(tmp_path):
    active = init_session(
        agent="codex",
        slug="empty-summary-test",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )

    record = finalize_session(active, root=tmp_path, summary_text="")

    assert record.markdown_path == record.jsonl_path.with_suffix(".md")
    assert record.markdown_path.read_text(encoding="utf-8") == "\n"


def test_finalize_can_create_matching_markdown_summary_from_file(tmp_path):
    active = init_session(
        agent="codex",
        slug="summary-file-test",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    source = tmp_path / "summary.md"
    source.write_text("# Summary\n\nFrom file.\n", encoding="utf-8")

    record = finalize_session(active, root=tmp_path, summary_file=source)

    assert record.markdown_path == record.jsonl_path.with_suffix(".md")
    assert record.markdown_path.read_text(encoding="utf-8") == "# Summary\n\nFrom file.\n"


def test_markdown_staging_uses_sync_excluded_partial_suffix(tmp_path, monkeypatch):
    active = init_session(
        agent="codex",
        slug="partial-suffix-test",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    original_rename = type(active).rename
    staged_names = []

    def fail_markdown_rename(self, target):
        if self.name.endswith(".partial"):
            staged_names.append(self.name)
            raise RuntimeError("simulated markdown rename failure")
        return original_rename(self, target)

    monkeypatch.setattr(type(active), "rename", fail_markdown_rename)

    with pytest.raises(RuntimeError):
        finalize_session(active, root=tmp_path, summary_text="# Summary")

    assert staged_names == ["2026-04-30T133000Z-studio-codex-partial-suffix-test.md.partial"]
    assert not list((tmp_path / "pipy" / "2026" / "04").glob("*.partial"))


def test_init_collision_suffix_is_finalizeable_as_part_of_slug(tmp_path):
    first = init_session(
        agent="codex",
        slug="collision-test",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    second = init_session(
        agent="codex",
        slug="collision-test",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )

    assert first.name == "2026-04-30T133000Z-studio-codex-collision-test.jsonl"
    assert second.name == "2026-04-30T133000Z-studio-codex-collision-test-2.jsonl"
    record = finalize_session(second, root=tmp_path)
    assert record.jsonl_path.name == "2026-04-30T133000Z-studio-codex-collision-test-2.jsonl"


def test_resolve_active_path_rejects_absolute_non_active_path(tmp_path):
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"type":"session.started"}\n', encoding="utf-8")

    with pytest.raises(FinalizedRecordError):
        resolve_active_path(outside, root=tmp_path)


def test_finalize_rejects_malformed_active_filename(tmp_path):
    active_dir = tmp_path / ".in-progress" / "pipy"
    active_dir.mkdir(parents=True)
    active = active_dir / "current-session.jsonl"
    active.write_text('{"type":"session.started"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="active session filename must match"):
        finalize_session(active, root=tmp_path)
