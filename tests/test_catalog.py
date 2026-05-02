from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from pipy_session import (
    append_event,
    finalize_session,
    init_session,
    inspect_finalized_session,
    list_finalized_sessions,
    verify_session_archive,
)
from pipy_session.catalog import format_archive_verification, format_session_table
from pipy_session.cli import main


FIXED_NOW = datetime(2026, 4, 30, 13, 30, 0, tzinfo=UTC)


def issue_by_path_and_kind(issues, path, kind):
    matches = [issue for issue in issues if issue.path == path and issue.kind == kind]
    assert len(matches) == 1
    return matches[0]


def test_verify_session_archive_reports_ok_for_empty_archive(tmp_path):
    verification = verify_session_archive(root=tmp_path)

    assert verification.ok is True
    assert verification.issue_count == 0
    assert verification.issues == []
    assert verification.to_dict() == {
        "ok": True,
        "issue_count": 0,
        "root": str(tmp_path),
        "issues": [],
    }
    assert format_archive_verification(verification).splitlines() == [
        "status\tissue\tpath\tdetail",
        "ok",
    ]


def test_verify_session_archive_reports_ok_for_valid_record_with_summary(tmp_path):
    active = init_session(
        agent="codex",
        slug="verify-valid",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    finalize_session(active, root=tmp_path, summary_text="# Summary\n\nVerified.")

    verification = verify_session_archive(root=tmp_path)

    assert verification.ok is True
    assert verification.issues == []


def test_verify_session_archive_reports_malformed_finalized_jsonl(tmp_path):
    archive = tmp_path / "pipy" / "2026" / "04"
    archive.mkdir(parents=True)
    empty = archive / "2026-04-30T133000Z-studio-codex-empty.jsonl"
    non_utf8 = archive / "2026-04-30T133001Z-studio-codex-non-utf8.jsonl"
    invalid = archive / "2026-04-30T133001Z-studio-codex-invalid.jsonl"
    not_object = archive / "2026-04-30T133002Z-studio-codex-not-object.jsonl"
    wrong_event = archive / "2026-04-30T133003Z-studio-codex-wrong.jsonl"
    empty.write_text("", encoding="utf-8")
    non_utf8.write_bytes(b"\xff\xfe\n")
    invalid.write_text("{not-json}\n", encoding="utf-8")
    not_object.write_text('["session.started"]\n', encoding="utf-8")
    wrong_event.write_text('{"type":"decision.recorded","summary":"hidden"}\n', encoding="utf-8")

    verification = verify_session_archive(root=tmp_path)

    assert verification.ok is False
    assert issue_by_path_and_kind(verification.issues, empty, "malformed-jsonl").detail == "empty first line"
    assert issue_by_path_and_kind(verification.issues, non_utf8, "malformed-jsonl").detail == (
        "first line is not valid UTF-8"
    )
    assert issue_by_path_and_kind(verification.issues, invalid, "malformed-jsonl").detail == (
        "invalid JSON first line"
    )
    assert issue_by_path_and_kind(verification.issues, not_object, "malformed-jsonl").detail == (
        "first line is not a JSON object"
    )
    assert issue_by_path_and_kind(verification.issues, wrong_event, "malformed-jsonl").detail == (
        "first event is not session.started"
    )
    assert "hidden" not in format_archive_verification(verification)


def test_verify_session_archive_reports_orphan_summary_and_partial_leftovers(tmp_path):
    archive = tmp_path / "pipy" / "2026" / "04"
    archive.mkdir(parents=True)
    orphan = archive / "2026-04-30T133000Z-studio-codex-orphan.md"
    archive_partial = archive / "2026-04-30T133001Z-studio-codex-staged.jsonl.partial"
    active_partial = tmp_path / ".in-progress" / "pipy" / "active.jsonl.partial"
    orphan.write_text("# Summary\n\nIntentional human text.", encoding="utf-8")
    archive_partial.write_text('{"type":"session.started"}\n', encoding="utf-8")
    active_partial.parent.mkdir(parents=True)
    active_partial.write_text('{"type":"session.started"}\n', encoding="utf-8")

    verification = verify_session_archive(root=tmp_path)

    assert issue_by_path_and_kind(verification.issues, orphan, "orphan-summary").severity == "warning"
    assert issue_by_path_and_kind(verification.issues, archive_partial, "partial-file").severity == "warning"
    assert issue_by_path_and_kind(verification.issues, active_partial, "partial-file").severity == "warning"


def test_verify_session_archive_reports_unexpected_archive_files(tmp_path):
    archive = tmp_path / "pipy"
    direct = archive / "direct.jsonl"
    year_file = archive / "2026" / "year-level.jsonl"
    deep = archive / "2026" / "04" / "nested" / "deep.jsonl"
    unsupported = archive / "2026" / "04" / "notes.txt"
    malformed_name = archive / "2026" / "04" / "bad-name.jsonl"
    direct.parent.mkdir(parents=True)
    year_file.parent.mkdir(parents=True)
    deep.parent.mkdir(parents=True)
    unsupported.parent.mkdir(parents=True, exist_ok=True)
    for path in [direct, year_file, deep, unsupported, malformed_name]:
        path.write_text('{"type":"session.started"}\n', encoding="utf-8")

    verification = verify_session_archive(root=tmp_path)

    assert issue_by_path_and_kind(verification.issues, direct, "unexpected-archive-file").severity == "error"
    assert issue_by_path_and_kind(verification.issues, year_file, "unexpected-archive-file").severity == "error"
    assert issue_by_path_and_kind(verification.issues, deep, "unexpected-archive-file").severity == "error"
    assert issue_by_path_and_kind(verification.issues, unsupported, "unsupported-archive-file").severity == (
        "warning"
    )
    assert issue_by_path_and_kind(verification.issues, malformed_name, "malformed-filename").severity == "error"


def test_verify_session_archive_reports_duplicate_basename_and_stem_ambiguity(tmp_path):
    basename = "2026-04-30T133000Z-studio-codex-ambiguous.jsonl"
    first = tmp_path / "pipy" / "2026" / "04" / basename
    second = tmp_path / "pipy" / "2026" / "05" / basename
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    content = (
        '{"agent":"codex","machine":"studio","project":"pipy","slug":"ambiguous",'
        '"timestamp":"2026-04-30T13:30:00+00:00","type":"session.started"}\n'
    )
    first.write_text(content, encoding="utf-8")
    second.write_text(content, encoding="utf-8")

    verification = verify_session_archive(root=tmp_path)

    basename_issue = issue_by_path_and_kind(verification.issues, first, "ambiguous-basename")
    stem_issue = issue_by_path_and_kind(verification.issues, first, "ambiguous-stem")
    assert basename_issue.severity == "warning"
    assert stem_issue.severity == "warning"
    assert str(second) in basename_issue.detail
    assert str(second) in stem_issue.detail


def test_verify_session_archive_ignores_active_jsonl_and_state_files(tmp_path):
    active = init_session(
        agent="codex",
        slug="active-ignored",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    state_dir = tmp_path / ".in-progress" / "pipy" / ".state"
    state_dir.mkdir(parents=True)
    state_file = state_dir / "codex-state.json"
    state_file.write_text('{"active_path":"/tmp/active.jsonl"}\n', encoding="utf-8")

    verification = verify_session_archive(root=tmp_path)

    assert verification.ok is True
    assert active.exists()
    assert state_file.exists()


def test_cli_verify_supports_human_and_json_output(tmp_path, capsys):
    archive = tmp_path / "pipy" / "2026" / "04"
    archive.mkdir(parents=True)
    malformed = archive / "2026-04-30T133000Z-studio-codex-bad.jsonl"
    malformed.write_text('{"type":"decision.recorded","summary":"do not print"}\n', encoding="utf-8")

    human_code = main(["--root", str(tmp_path), "verify"])
    human_output = capsys.readouterr()

    assert human_code == 0
    assert "status\tissue\tpath\tdetail" in human_output.out
    assert f"error\tmalformed-jsonl\t{malformed}\tfirst event is not session.started" in human_output.out
    assert "do not print" not in human_output.out

    json_code = main(["--root", str(tmp_path), "verify", "--json"])
    json_output = capsys.readouterr()

    assert json_code == 0
    parsed = json.loads(json_output.out)
    assert parsed == {
        "ok": False,
        "issue_count": 1,
        "root": str(tmp_path),
        "issues": [
            {
                "severity": "error",
                "kind": "malformed-jsonl",
                "path": str(malformed),
                "detail": "first event is not session.started",
            }
        ],
    }


def test_cli_verify_reports_non_utf8_first_line_without_failing_scan(tmp_path, capsys):
    archive = tmp_path / "pipy" / "2026" / "04"
    archive.mkdir(parents=True)
    malformed = archive / "2026-04-30T133000Z-studio-codex-non-utf8.jsonl"
    malformed.write_bytes(b"\xff\xfe\n")

    human_exit_code = main(["--root", str(tmp_path), "verify"])
    human_output = capsys.readouterr()

    assert human_exit_code == 0
    assert f"error\tmalformed-jsonl\t{malformed}\tfirst line is not valid UTF-8" in human_output.out
    assert "\xff" not in human_output.out
    assert "\xfe" not in human_output.out
    assert human_output.err == ""

    exit_code = main(["--root", str(tmp_path), "verify", "--json"])
    output = capsys.readouterr()

    assert exit_code == 0
    parsed = json.loads(output.out)
    assert parsed == {
        "ok": False,
        "issue_count": 1,
        "root": str(tmp_path),
        "issues": [
            {
                "severity": "error",
                "kind": "malformed-jsonl",
                "path": str(malformed),
                "detail": "first line is not valid UTF-8",
            }
        ],
    }
    assert output.err == ""


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


def test_list_finalized_sessions_skips_non_utf8_first_line(tmp_path):
    active = init_session(
        agent="codex",
        slug="valid-work",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    finalized = finalize_session(active, root=tmp_path)
    non_utf8 = tmp_path / "pipy" / "2026" / "04" / "2026-04-30T133100Z-studio-codex-non-utf8.jsonl"
    non_utf8.write_bytes(b"\xff\xfe\n")

    records = list_finalized_sessions(root=tmp_path)

    assert [record.jsonl_path for record in records] == [finalized.jsonl_path]


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


def test_cli_list_skips_non_utf8_first_line_in_human_and_json_output(tmp_path, capsys):
    active = init_session(
        agent="codex",
        slug="valid-list",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    record = finalize_session(active, root=tmp_path, summary_text="# Summary")
    non_utf8 = tmp_path / "pipy" / "2026" / "04" / "2026-04-30T133100Z-studio-codex-non-utf8.jsonl"
    non_utf8.write_bytes(b"\xff\xfe\n")

    table_code = main(["--root", str(tmp_path), "list"])
    table_output = capsys.readouterr()

    assert table_code == 0
    assert f"\tstudio\tcodex\tvalid-list\tcomplete\tyes\t{record.jsonl_path}" in table_output.out
    assert str(non_utf8) not in table_output.out
    assert "\xff" not in table_output.out
    assert "\xfe" not in table_output.out
    assert table_output.err == ""

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
            "slug": "valid-list",
            "started": "2026-04-30T13:30:00+00:00",
        }
    ]
    assert str(non_utf8) not in json_output.out
    assert "\xff" not in json_output.out
    assert "\xfe" not in json_output.out
    assert json_output.err == ""


def test_inspect_finalized_session_by_absolute_path_reads_metadata_counts_and_summary(tmp_path):
    active = init_session(
        agent="codex",
        slug="inspect-absolute",
        root=tmp_path,
        machine="studio",
        partial=True,
        now=FIXED_NOW,
    )
    append_event(
        active,
        root=tmp_path,
        event_type="decision.recorded",
        summary="Inspect records without dumping raw content.",
        now=FIXED_NOW,
    )
    record = finalize_session(active, root=tmp_path, summary_text="# Summary\n\nSafe inspection.")

    inspection = inspect_finalized_session(record.jsonl_path, root=tmp_path)

    assert inspection.started == "2026-04-30T13:30:00+00:00"
    assert inspection.machine == "studio"
    assert inspection.agent == "codex"
    assert inspection.slug == "inspect-absolute"
    assert inspection.capture == "partial"
    assert inspection.jsonl_path == record.jsonl_path
    assert inspection.markdown_path == record.markdown_path
    assert inspection.event_count == 3
    assert inspection.event_types == {
        "capture.limitations": 1,
        "decision.recorded": 1,
        "session.started": 1,
    }
    assert inspection.summary_text == "# Summary\n\nSafe inspection.\n"


def test_inspect_finalized_session_by_basename_and_stem(tmp_path):
    active = init_session(
        agent="codex",
        slug="inspect-name",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    record = finalize_session(active, root=tmp_path)

    by_basename = inspect_finalized_session(record.jsonl_path.name, root=tmp_path)
    by_dot_basename = inspect_finalized_session(f"./{record.jsonl_path.name}", root=tmp_path)
    by_stem = inspect_finalized_session(record.jsonl_path.stem, root=tmp_path)

    assert by_basename.jsonl_path == record.jsonl_path
    assert by_dot_basename.jsonl_path == record.jsonl_path
    assert by_stem.jsonl_path == record.jsonl_path


def test_cli_inspect_json_output_contains_metadata_counts_and_summary(tmp_path, capsys):
    active = init_session(
        agent="codex",
        slug="cli-inspect-json",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    append_event(
        active,
        root=tmp_path,
        event_type="verification.performed",
        summary="pytest passed.",
        now=FIXED_NOW,
    )
    record = finalize_session(active, root=tmp_path, summary_text="# Summary\n\nJSON output.")

    exit_code = main(["--root", str(tmp_path), "inspect", record.jsonl_path.stem, "--json"])
    output = capsys.readouterr()

    assert exit_code == 0
    parsed = json.loads(output.out)
    assert parsed == {
        "agent": "codex",
        "capture": "complete",
        "event_count": 2,
        "event_types": {
            "session.started": 1,
            "verification.performed": 1,
        },
        "has_summary": True,
        "jsonl_path": str(record.jsonl_path),
        "machine": "studio",
        "markdown_path": str(record.markdown_path),
        "partial": False,
        "slug": "cli-inspect-json",
        "started": "2026-04-30T13:30:00+00:00",
        "summary_path": str(record.markdown_path),
        "summary_text": "# Summary\n\nJSON output.\n",
    }


def test_cli_inspect_human_output_includes_metadata_counts_and_summary(tmp_path, capsys):
    active = init_session(
        agent="codex",
        slug="cli-inspect-human",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    append_event(
        active,
        root=tmp_path,
        event_type="file.changed",
        summary="Updated catalog.",
        now=FIXED_NOW,
    )
    record = finalize_session(active, root=tmp_path, summary_text="# Summary\n\nHuman output.")

    exit_code = main(["--root", str(tmp_path), "inspect", str(record.jsonl_path)])
    output = capsys.readouterr()

    assert exit_code == 0
    assert "started: 2026-04-30T13:30:00+00:00" in output.out
    assert "machine: studio" in output.out
    assert "agent: codex" in output.out
    assert "slug: cli-inspect-human" in output.out
    assert "capture: complete" in output.out
    assert f"jsonl_path: {record.jsonl_path}" in output.out
    assert f"markdown_path: {record.markdown_path}" in output.out
    assert "event_count: 2" in output.out
    assert "  session.started: 1" in output.out
    assert "  file.changed: 1" in output.out
    assert "# Summary\n\nHuman output." in output.out


def test_cli_inspect_rejects_active_records_state_files_and_partials(tmp_path, capsys):
    active = init_session(
        agent="codex",
        slug="active-reject",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    state_dir = tmp_path / ".in-progress" / "pipy" / ".state"
    state_dir.mkdir(parents=True)
    state_file = state_dir / "codex-state.json"
    state_file.write_text('{"active_path":"/tmp/active.jsonl"}\n', encoding="utf-8")
    archive = tmp_path / "pipy" / "2026" / "04"
    archive.mkdir(parents=True)
    partial = archive / "2026-04-30T133001Z-studio-codex-staged.jsonl.partial"
    partial.write_text('{"type":"session.started"}\n', encoding="utf-8")

    for rejected in [active, state_file, partial]:
        exit_code = main(["--root", str(tmp_path), "inspect", str(rejected)])
        output = capsys.readouterr()

        assert exit_code == 2
        assert "pipy-session:" in output.err


def test_cli_inspect_rejects_arbitrary_outside_path(tmp_path, capsys):
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside = outside_dir / "2026-04-30T133000Z-studio-codex-outside.jsonl"
    outside.write_text(
        (
            '{"agent":"codex","machine":"studio","project":"pipy","slug":"outside",'
            '"timestamp":"2026-04-30T13:30:00+00:00","type":"session.started"}\n'
        ),
        encoding="utf-8",
    )

    exit_code = main(["--root", str(tmp_path), "inspect", str(outside)])
    output = capsys.readouterr()

    assert exit_code == 2
    assert "pipy-session: not a finalized archive JSONL record" in output.err


def test_cli_inspect_rejects_malformed_finalized_jsonl(tmp_path, capsys):
    malformed = tmp_path / "pipy" / "2026" / "04" / "2026-04-30T133000Z-studio-codex-bad.jsonl"
    malformed.parent.mkdir(parents=True)
    malformed.write_text("{not-json}\n", encoding="utf-8")

    exit_code = main(["--root", str(tmp_path), "inspect", str(malformed)])
    output = capsys.readouterr()

    assert exit_code == 2
    assert "pipy-session: malformed JSONL event at line 1" in output.err


def test_cli_inspect_rejects_empty_finalized_jsonl(tmp_path, capsys):
    empty = tmp_path / "pipy" / "2026" / "04" / "2026-04-30T133000Z-studio-codex-empty.jsonl"
    empty.parent.mkdir(parents=True)
    empty.write_text("", encoding="utf-8")

    exit_code = main(["--root", str(tmp_path), "inspect", str(empty)])
    output = capsys.readouterr()

    assert exit_code == 2
    assert "pipy-session: malformed finalized session record: empty file" in output.err


def test_cli_inspect_rejects_finalized_jsonl_without_session_started_first(tmp_path, capsys):
    wrong_event = tmp_path / "pipy" / "2026" / "04" / "2026-04-30T133000Z-studio-codex-wrong.jsonl"
    wrong_event.parent.mkdir(parents=True)
    wrong_event.write_text('{"type":"decision.recorded"}\n', encoding="utf-8")

    exit_code = main(["--root", str(tmp_path), "inspect", str(wrong_event)])
    output = capsys.readouterr()

    assert exit_code == 2
    assert "pipy-session: malformed finalized session record: first event is not session.started" in output.err


def test_cli_inspect_rejects_ambiguous_basename_matches(tmp_path, capsys):
    basename = "2026-04-30T133000Z-studio-codex-ambiguous.jsonl"
    first = tmp_path / "pipy" / "2026" / "04" / basename
    second = tmp_path / "pipy" / "2026" / "05" / basename
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    content = (
        '{"agent":"codex","machine":"studio","project":"pipy","slug":"ambiguous",'
        '"timestamp":"2026-04-30T13:30:00+00:00","type":"session.started"}\n'
    )
    first.write_text(content, encoding="utf-8")
    second.write_text(content, encoding="utf-8")

    exit_code = main(["--root", str(tmp_path), "inspect", basename])
    output = capsys.readouterr()

    assert exit_code == 2
    assert "pipy-session: ambiguous finalized session record" in output.err
