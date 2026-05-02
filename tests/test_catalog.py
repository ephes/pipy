from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from pipy_session import finalize_session, init_session, list_finalized_sessions
from pipy_session.catalog import format_session_table
from pipy_session.cli import main


FIXED_NOW = datetime(2026, 4, 30, 13, 30, 0, tzinfo=UTC)


def test_list_finalized_sessions_returns_archive_records_newest_first(tmp_path):
    older_active = init_session(
        agent="codex",
        slug="older-work",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    older = finalize_session(older_active, root=tmp_path, summary_text="# Summary\n\nOlder.")
    newer_active = init_session(
        agent="claude",
        slug="newer-work",
        root=tmp_path,
        machine="atlas",
        partial=True,
        now=FIXED_NOW + timedelta(hours=1),
    )
    newer = finalize_session(newer_active, root=tmp_path)

    records = list_finalized_sessions(root=tmp_path)

    assert [record.jsonl_path for record in records] == [newer.jsonl_path, older.jsonl_path]
    assert records[0].machine == "atlas"
    assert records[0].agent == "claude"
    assert records[0].slug == "newer-work"
    assert records[0].capture == "partial"
    assert records[0].has_summary is False
    assert records[1].capture == "complete"
    assert records[1].markdown_path == older.markdown_path
    assert records[1].has_summary is True


def test_list_finalized_sessions_sorts_by_filename_stamp_when_timestamp_is_missing(tmp_path):
    older = tmp_path / "pipy" / "2026" / "04" / "2026-04-30T133000Z-studio-codex-older.jsonl"
    newer = tmp_path / "pipy" / "2026" / "04" / "2026-04-30T133001Z-studio-codex-newer.jsonl"
    older.parent.mkdir(parents=True)
    older.write_text(
        '{"agent":"codex","machine":"studio","project":"pipy","slug":"older","type":"session.started"}\n',
        encoding="utf-8",
    )
    newer.write_text(
        (
            '{"agent":"codex","machine":"studio","project":"pipy","slug":"newer",'
            '"timestamp":"2026-04-30T13:30:00+00:00","type":"session.started"}\n'
        ),
        encoding="utf-8",
    )

    records = list_finalized_sessions(root=tmp_path)

    assert [record.slug for record in records] == ["newer", "older"]


def test_list_finalized_sessions_ignores_active_partial_and_malformed_files(tmp_path):
    active = init_session(
        agent="codex",
        slug="active-work",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    finalized_active = init_session(
        agent="codex",
        slug="finished-work",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW + timedelta(minutes=1),
    )
    finalized = finalize_session(finalized_active, root=tmp_path)

    archive = tmp_path / "pipy" / "2026" / "04"
    partial = archive / "2026-04-30T133200Z-studio-codex-staged.jsonl.partial"
    partial.write_text('{"type":"session.started"}\n', encoding="utf-8")
    malformed = archive / "2026-04-30T133300Z-studio-codex-bad.jsonl"
    malformed.write_text("{not-json}\n", encoding="utf-8")
    wrong_event = archive / "2026-04-30T133400Z-studio-codex-wrong.jsonl"
    wrong_event.write_text('{"type":"decision.recorded"}\n', encoding="utf-8")
    state_dir = tmp_path / ".in-progress" / "pipy" / ".state"
    state_dir.mkdir(parents=True)
    state_file = state_dir / "codex-state.json"
    state_file.write_text('{"active_path":"/tmp/active.jsonl"}\n', encoding="utf-8")

    records = list_finalized_sessions(root=tmp_path)

    assert [record.jsonl_path for record in records] == [finalized.jsonl_path]
    assert active.exists()
    assert state_file.exists()


def test_format_session_table_prints_header_and_rows(tmp_path):
    active = init_session(
        agent="codex",
        slug="table-work",
        root=tmp_path,
        machine="studio",
        partial=True,
        now=FIXED_NOW,
    )
    record = finalize_session(active, root=tmp_path, summary_text="# Summary")

    table = format_session_table(list_finalized_sessions(root=tmp_path))

    assert table.splitlines() == [
        "started\tmachine\tagent\tslug\tcapture\tsummary\tpath",
        (
            "2026-04-30T13:30:00+00:00\tstudio\tcodex\ttable-work\tpartial\tyes\t"
            f"{record.jsonl_path}"
        ),
    ]


def test_cli_list_supports_table_and_json_output(tmp_path, capsys):
    active = init_session(
        agent="codex",
        slug="cli-list",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    record = finalize_session(active, root=tmp_path, summary_text="# Summary")

    table_code = main(["--root", str(tmp_path), "list"])
    table_output = capsys.readouterr()

    assert table_code == 0
    assert "started\tmachine\tagent\tslug\tcapture\tsummary\tpath" in table_output.out
    assert f"\tstudio\tcodex\tcli-list\tcomplete\tyes\t{record.jsonl_path}" in table_output.out

    json_code = main(["--root", str(tmp_path), "list", "--json"])
    json_output = capsys.readouterr()

    assert json_code == 0
    parsed = json.loads(json_output.out)
    assert parsed == [
        {
            "agent": "codex",
            "capture": "complete",
            "has_summary": True,
            "jsonl_path": str(record.jsonl_path),
            "machine": "studio",
            "markdown_path": str(record.markdown_path),
            "partial": False,
            "slug": "cli-list",
            "started": "2026-04-30T13:30:00+00:00",
        }
    ]
