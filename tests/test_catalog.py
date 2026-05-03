from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pipy_session import (
    SessionReflectionItem,
    append_event,
    finalize_session,
    init_session,
    inspect_finalized_session,
    list_finalized_sessions,
    reflect_on_finalized_sessions,
    search_finalized_sessions,
    verify_session_archive,
)
from pipy_session.catalog import (
    format_archive_verification,
    format_session_inspection,
    format_session_reflection,
    format_session_search_results,
    format_session_table,
)
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


def test_verify_session_archive_reports_unreadable_finalized_jsonl_without_aborting(tmp_path, monkeypatch):
    active = init_session(
        agent="codex",
        slug="valid-alongside-unreadable",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    valid = finalize_session(active, root=tmp_path)
    unreadable = tmp_path / "pipy" / "2026" / "04" / "2026-04-30T133100Z-studio-codex-unreadable.jsonl"
    unreadable.write_bytes(
        b'{"type":"decision.recorded","summary":"SECRET_BODY should not print"}\n\xff\xfe\n'
    )

    original_open = Path.open

    def raise_for_unreadable(self, *args, **kwargs):
        if self == unreadable:
            raise OSError("SECRET_EXCEPTION with prompt text")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", raise_for_unreadable)

    verification = verify_session_archive(root=tmp_path)

    assert verification.ok is False
    assert [issue.path for issue in verification.issues] == [unreadable]
    issue = issue_by_path_and_kind(verification.issues, unreadable, "unreadable-jsonl")
    assert issue.severity == "error"
    assert issue.detail == "could not read first line"
    assert valid.jsonl_path not in [reported.path for reported in verification.issues]


def test_cli_verify_reports_unreadable_jsonl_without_exposing_exception_or_content(
    tmp_path, monkeypatch, capsys
):
    active = init_session(
        agent="codex",
        slug="valid-cli-unreadable",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    valid = finalize_session(active, root=tmp_path)
    unreadable = tmp_path / "pipy" / "2026" / "04" / "2026-04-30T133100Z-studio-codex-unreadable.jsonl"
    unreadable.write_bytes(
        b'{"type":"decision.recorded","summary":"SECRET_BODY should not print"}\n\xff\xfe\n'
    )

    original_open = Path.open

    def raise_for_unreadable(self, *args, **kwargs):
        if self == unreadable:
            raise OSError("SECRET_EXCEPTION with prompt text")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", raise_for_unreadable)

    human_code = main(["--root", str(tmp_path), "verify"])
    human_output = capsys.readouterr()

    assert human_code == 0
    assert (
        f"error\tunreadable-jsonl\t{unreadable}\tcould not read first line"
        in human_output.out
    )
    assert str(valid.jsonl_path) not in human_output.out
    assert "SECRET_EXCEPTION" not in human_output.out
    assert "SECRET_BODY" not in human_output.out
    assert "\xff" not in human_output.out
    assert "\xfe" not in human_output.out
    assert human_output.err == ""

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
                "kind": "unreadable-jsonl",
                "path": str(unreadable),
                "detail": "could not read first line",
            }
        ],
    }
    assert str(valid.jsonl_path) not in json_output.out
    assert "SECRET_EXCEPTION" not in json_output.out
    assert "SECRET_BODY" not in json_output.out
    assert "\xff" not in json_output.out
    assert "\xfe" not in json_output.out
    assert json_output.err == ""


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


def test_list_human_output_sanitizes_metadata_table_cells(tmp_path, capsys):
    root = tmp_path / "session\troot\nname"
    archive = root / "pipy" / "2026" / "04"
    archive.mkdir(parents=True)
    record_path = archive / "2026-04-30T133000Z-studio-codex-safe-name.jsonl"
    first_event = {
        "type": "session.started",
        "timestamp": "2026-04-30T13:30:00+00:00\tBAD\nNEXT",
        "machine": "studio\twest\nrack",
        "agent": "codex\tcli\nagent",
        "slug": "safe\tname\ninjected",
    }
    record_path.write_text(f"{json.dumps(first_event)}\n", encoding="utf-8")

    table = format_session_table(list_finalized_sessions(root=root))
    lines = table.splitlines()

    assert len(lines) == 2
    assert lines[0] == "started\tmachine\tagent\tslug\tcapture\tsummary\tpath"
    columns = lines[1].split("\t")
    assert len(columns) == 7
    assert columns == [
        "2026-04-30T13:30:00+00:00 BAD NEXT",
        "studio west rack",
        "codex cli agent",
        "safe name injected",
        "complete",
        "no",
        str(record_path).replace("\t", " ").replace("\n", " "),
    ]

    json_code = main(["--root", str(root), "list", "--json"])
    json_output = capsys.readouterr()

    assert json_code == 0
    assert json.loads(json_output.out) == [
        {
            "agent": "codex\tcli\nagent",
            "capture": "complete",
            "has_summary": False,
            "jsonl_path": str(record_path),
            "machine": "studio\twest\nrack",
            "markdown_path": None,
            "partial": False,
            "slug": "safe\tname\ninjected",
            "started": "2026-04-30T13:30:00+00:00\tBAD\nNEXT",
        }
    ]
    assert json_output.err == ""


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


def test_search_finalized_sessions_finds_metadata_events_and_markdown(tmp_path):
    active = init_session(
        agent="codex",
        slug="search-target",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    append_event(
        active,
        root=tmp_path,
        event_type="decision.recorded",
        summary="Use searchable finalized session summaries.",
        payload={"secret": "PAYLOAD_SECRET should stay hidden"},
        now=FIXED_NOW,
    )
    record = finalize_session(
        active,
        root=tmp_path,
        summary_text="# Summary\n\nMarkdown helps future agents find work.",
    )
    other_active = init_session(
        agent="claude",
        slug="other-work",
        root=tmp_path,
        machine="atlas",
        now=FIXED_NOW + timedelta(minutes=1),
    )
    finalize_session(other_active, root=tmp_path)

    slug_results = search_finalized_sessions("search-target", root=tmp_path)
    agent_results = search_finalized_sessions("CODEX", root=tmp_path)
    event_type_results = search_finalized_sessions("decision.recorded", root=tmp_path)
    summary_results = search_finalized_sessions("FINALIZED SESSION", root=tmp_path)
    markdown_results = search_finalized_sessions("future agents", root=tmp_path)

    assert [result.jsonl_path for result in slug_results] == [record.jsonl_path]
    assert [result.jsonl_path for result in agent_results] == [record.jsonl_path]
    assert [result.jsonl_path for result in event_type_results] == [record.jsonl_path]
    assert [result.jsonl_path for result in summary_results] == [record.jsonl_path]
    assert [result.jsonl_path for result in markdown_results] == [record.jsonl_path]
    assert "metadata.slug" in {match.field for match in slug_results[0].matches}
    assert event_type_results[0].matches[0].field == "event.type"
    assert summary_results[0].matches[0].field == "event.summary"
    assert markdown_results[0].matches[0].field == "markdown.summary"
    assert search_finalized_sessions("PAYLOAD_SECRET", root=tmp_path) == []


def test_search_finalized_sessions_returns_newest_first(tmp_path):
    older_active = init_session(
        agent="codex",
        slug="older-search",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    append_event(
        older_active,
        root=tmp_path,
        event_type="decision.recorded",
        summary="shared search needle",
        now=FIXED_NOW,
    )
    older = finalize_session(older_active, root=tmp_path)
    newer_active = init_session(
        agent="codex",
        slug="newer-search",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW + timedelta(hours=1),
    )
    append_event(
        newer_active,
        root=tmp_path,
        event_type="decision.recorded",
        summary="shared search needle",
        now=FIXED_NOW + timedelta(hours=1),
    )
    newer = finalize_session(newer_active, root=tmp_path)

    results = search_finalized_sessions("shared search needle", root=tmp_path)

    assert [result.jsonl_path for result in results] == [newer.jsonl_path, older.jsonl_path]


def test_search_ignores_active_state_partials_unsupported_and_malformed_records(tmp_path):
    active = init_session(
        agent="codex",
        slug="needlexyz-active",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    append_event(
        active,
        root=tmp_path,
        event_type="decision.recorded",
        summary="needlexyz active record should not be searched",
        now=FIXED_NOW,
    )
    clean_active = init_session(
        agent="codex",
        slug="clean-record",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW + timedelta(minutes=1),
    )
    clean = finalize_session(clean_active, root=tmp_path)

    archive = tmp_path / "pipy" / "2026" / "04"
    partial = archive / "2026-04-30T133200Z-studio-codex-needlexyz.jsonl.partial"
    partial.write_text('{"type":"session.started","summary":"needlexyz"}\n', encoding="utf-8")
    unsupported = archive / "needlexyz.txt"
    unsupported.write_text("needlexyz unsupported", encoding="utf-8")
    malformed = archive / "2026-04-30T133300Z-studio-codex-needlexyz-bad.jsonl"
    malformed.write_text("{not-json with needlexyz}\n", encoding="utf-8")
    later_malformed = archive / "2026-04-30T133400Z-studio-codex-needlexyz-later-bad.jsonl"
    later_malformed.write_text(
        (
            '{"agent":"codex","machine":"studio","project":"pipy","slug":"needlexyz-later-bad",'
            '"timestamp":"2026-04-30T13:34:00+00:00","type":"session.started"}\n'
            '{"type":"decision.recorded","summary":"needlexyz"\n'
        ),
        encoding="utf-8",
    )
    state_dir = tmp_path / ".in-progress" / "pipy" / ".state"
    state_dir.mkdir(parents=True)
    state_file = state_dir / "needlexyz-state.json"
    state_file.write_text('{"active_path":"needlexyz"}\n', encoding="utf-8")

    results = search_finalized_sessions("needlexyz", root=tmp_path)

    assert results == []
    assert clean.jsonl_path.exists()
    assert active.exists()
    assert state_file.exists()


def test_search_skips_unreadable_records_without_aborting(tmp_path, monkeypatch):
    active = init_session(
        agent="codex",
        slug="resilient-search",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    append_event(
        active,
        root=tmp_path,
        event_type="decision.recorded",
        summary="resilient needle",
        now=FIXED_NOW,
    )
    valid = finalize_session(active, root=tmp_path)
    unreadable = tmp_path / "pipy" / "2026" / "04" / "2026-04-30T133100Z-studio-codex-unreadable.jsonl"
    unreadable.write_text(
        (
            '{"agent":"codex","machine":"studio","project":"pipy","slug":"unreadable",'
            '"timestamp":"2026-04-30T13:31:00+00:00","type":"session.started"}\n'
            '{"type":"decision.recorded","summary":"SECRET_BODY resilient needle"}\n'
        ),
        encoding="utf-8",
    )

    original_open = Path.open

    def raise_for_unreadable(self, *args, **kwargs):
        if self == unreadable:
            raise OSError("SECRET_EXCEPTION with prompt text")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", raise_for_unreadable)

    results = search_finalized_sessions("resilient needle", root=tmp_path)

    assert [result.jsonl_path for result in results] == [valid.jsonl_path]
    assert "SECRET_BODY" not in format_session_search_results(results)


def test_format_session_search_results_is_stable_and_privacy_safe(tmp_path):
    active = init_session(
        agent="codex",
        slug="privacy-search",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    append_event(
        active,
        root=tmp_path,
        event_type="tool.command",
        summary="Needle public summary.",
        payload={
            "command": "cat secret.txt",
            "output": "PAYLOAD_SECRET prompt text tool output transcript body",
        },
        now=FIXED_NOW,
    )
    record = finalize_session(active, root=tmp_path)

    table = format_session_search_results(search_finalized_sessions("needle", root=tmp_path))

    assert table.splitlines() == [
        "started\tmachine\tagent\tslug\tcapture\tmatches\tpath",
        (
            "2026-04-30T13:30:00+00:00\tstudio\tcodex\tprivacy-search\tcomplete\t"
            f"summary\t{record.jsonl_path}"
        ),
    ]
    assert "PAYLOAD_SECRET" not in table
    assert "prompt text" not in table
    assert "tool output" not in table
    assert "transcript body" not in table
    assert '{"type"' not in table


def test_search_human_output_sanitizes_event_type_labels(tmp_path):
    active = init_session(
        agent="codex",
        slug="label-sanitize",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    append_event(
        active,
        root=tmp_path,
        event={
            "type": "tool\tcommand\nINJECTED",
            "summary": "Needle summary with\tcollapsed\nwhitespace.",
        },
        now=FIXED_NOW,
    )
    record = finalize_session(active, root=tmp_path)

    table = format_session_search_results(search_finalized_sessions("tool", root=tmp_path))
    lines = table.splitlines()

    assert lines == [
        "started\tmachine\tagent\tslug\tcapture\tmatches\tpath",
        (
            "2026-04-30T13:30:00+00:00\tstudio\tcodex\tlabel-sanitize\tcomplete\t"
            f"event:tool command INJECTED\t{record.jsonl_path}"
        ),
    ]
    assert len(lines[1].split("\t")) == 7


def test_cli_search_supports_human_and_json_output_without_payload_values(tmp_path, capsys):
    active = init_session(
        agent="codex",
        slug="cli-search",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    append_event(
        active,
        root=tmp_path,
        event_type="decision.recorded",
        summary="Needle public event summary.",
        payload={"secret": "PAYLOAD_SECRET prompt text tool output"},
        now=FIXED_NOW,
    )
    record = finalize_session(
        active,
        root=tmp_path,
        summary_text="# Summary\n\nNeedle markdown summary.",
    )
    invalid = tmp_path / "pipy" / "2026" / "04" / "2026-04-30T133100Z-studio-codex-invalid.jsonl"
    invalid.write_bytes(b"\xff\xfe\n")

    human_code = main(["--root", str(tmp_path), "search", "needle"])
    human_output = capsys.readouterr()

    assert human_code == 0
    assert "started\tmachine\tagent\tslug\tcapture\tmatches\tpath" in human_output.out
    assert f"\tstudio\tcodex\tcli-search\tcomplete\tsummary, markdown\t{record.jsonl_path}" in (
        human_output.out
    )
    assert "PAYLOAD_SECRET" not in human_output.out
    assert "prompt text" not in human_output.out
    assert "tool output" not in human_output.out
    assert '{"type"' not in human_output.out
    assert "\xff" not in human_output.out
    assert "\xfe" not in human_output.out
    assert human_output.err == ""

    json_code = main(["--root", str(tmp_path), "search", "needle", "--json"])
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
            "matches": [
                {
                    "event_type": "decision.recorded",
                    "field": "event.summary",
                    "line": 2,
                    "snippet": "Needle public event summary.",
                },
                {
                    "event_type": None,
                    "field": "markdown.summary",
                    "line": 3,
                    "snippet": "# Summary Needle markdown summary.",
                },
            ],
            "partial": False,
            "slug": "cli-search",
            "started": "2026-04-30T13:30:00+00:00",
        }
    ]
    assert "PAYLOAD_SECRET" not in json_output.out
    assert "prompt text" not in json_output.out
    assert "tool output" not in json_output.out
    assert '{"type"' not in json_output.out
    assert "\xff" not in json_output.out
    assert "\xfe" not in json_output.out
    assert json_output.err == ""


def test_search_rejects_empty_query(tmp_path, capsys):
    active = init_session(
        agent="codex",
        slug="empty-query",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    finalize_session(active, root=tmp_path)

    for query in ["", " \t\n"]:
        try:
            search_finalized_sessions(query, root=tmp_path)
        except ValueError as exc:
            assert str(exc) == "search query must not be empty"
        else:
            raise AssertionError("empty search query should fail")

    exit_code = main(["--root", str(tmp_path), "search", ""])
    output = capsys.readouterr()

    assert exit_code == 2
    assert output.out == ""
    assert "pipy-session: search query must not be empty" in output.err


def test_cli_search_empty_results(tmp_path, capsys):
    active = init_session(
        agent="codex",
        slug="empty-search",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    finalize_session(active, root=tmp_path)

    human_code = main(["--root", str(tmp_path), "search", "missing"])
    human_output = capsys.readouterr()

    assert human_code == 0
    assert human_output.out == "started\tmachine\tagent\tslug\tcapture\tmatches\tpath\n"
    assert human_output.err == ""

    json_code = main(["--root", str(tmp_path), "search", "missing", "--json"])
    json_output = capsys.readouterr()

    assert json_code == 0
    assert json.loads(json_output.out) == []
    assert json_output.err == ""


def test_reflect_on_finalized_sessions_extracts_summary_safe_learning_signals(tmp_path):
    active = init_session(
        agent="codex",
        slug="reflection-target",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    append_event(
        active,
        root=tmp_path,
        event_type="decision.recorded",
        summary="Keep reflection read-only and summary-safe.",
        payload={"secret": "PAYLOAD_SECRET prompt text tool output"},
        now=FIXED_NOW,
    )
    append_event(
        active,
        root=tmp_path,
        event_type="lesson.learned",
        summary="Markdown summaries are useful curated learning artifacts.",
        now=FIXED_NOW,
    )
    record = finalize_session(
        active,
        root=tmp_path,
        summary_text="# Summary\n\nImplemented reflection over finalized session summaries.",
    )

    low_signal_active = init_session(
        agent="claude",
        slug="lifecycle-only",
        root=tmp_path,
        machine="studio",
        partial=True,
        now=FIXED_NOW + timedelta(minutes=1),
    )
    append_event(
        low_signal_active,
        root=tmp_path,
        event_type="auto_capture.started",
        summary="Automatic capture started for claude.",
        now=FIXED_NOW + timedelta(minutes=1),
    )
    append_event(
        low_signal_active,
        root=tmp_path,
        event_type="auto_capture.ended",
        summary="Automatic capture ended.",
        now=FIXED_NOW + timedelta(minutes=1),
    )
    finalize_session(
        low_signal_active,
        root=tmp_path,
        summary_text=(
            "# Summary\n\nAutomatic claude capture finalized.\n\n"
            "This record is partial: the adapter captured lifecycle metadata."
        ),
    )

    malformed = tmp_path / "pipy" / "2026" / "04" / "2026-04-30T133200Z-studio-codex-bad.jsonl"
    malformed.write_text("{not-json PAYLOAD_SECRET}\n", encoding="utf-8")

    reflection = reflect_on_finalized_sessions(root=tmp_path)
    formatted = format_session_reflection(reflection)

    assert reflection.session_count == 2
    assert reflection.sessions_with_markdown == 2
    assert reflection.low_signal_session_count == 1
    assert reflection.agent_counts == {"claude": 1, "codex": 1}
    assert reflection.capture_counts == {"complete": 1, "partial": 1}
    assert reflection.event_type_counts["decision.recorded"] == 1
    assert reflection.event_type_counts["lesson.learned"] == 1
    assert reflection.summary_event_count == 5
    assert [
        (item.category, item.event_type, item.summary)
        for item in reflection.items
        if item.jsonl_path == record.jsonl_path
    ] == [
        ("decisions", "decision.recorded", "Keep reflection read-only and summary-safe."),
        (
            "lessons",
            "lesson.learned",
            "Markdown summaries are useful curated learning artifacts.",
        ),
        (
            "session-summaries",
            None,
            "# Summary Implemented reflection over finalized session summaries.",
        ),
    ]
    assert "## Decisions" in formatted
    assert "Keep reflection read-only and summary-safe." in formatted
    assert "Markdown summaries are useful curated learning artifacts." in formatted
    assert "PAYLOAD_SECRET" not in formatted
    assert "prompt text" not in formatted
    assert "tool output" not in formatted
    assert '{"type"' not in formatted


def test_cli_reflect_supports_human_and_json_output(tmp_path, capsys):
    active = init_session(
        agent="codex",
        slug="cli-reflect",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    append_event(
        active,
        root=tmp_path,
        event_type="recommendation.recorded",
        summary="Add a reflection command before building an index.",
        now=FIXED_NOW,
    )
    record = finalize_session(active, root=tmp_path)

    human_code = main(["--root", str(tmp_path), "reflect"])
    human_output = capsys.readouterr()

    assert human_code == 0
    assert "# Session Reflection" in human_output.out
    assert "## Recommendations" in human_output.out
    assert "Add a reflection command before building an index." in human_output.out
    assert human_output.err == ""

    json_code = main(["--root", str(tmp_path), "reflect", "--json"])
    json_output = capsys.readouterr()

    assert json_code == 0
    parsed = json.loads(json_output.out)
    assert parsed["session_count"] == 1
    assert parsed["items"] == [
        {
            "agent": "codex",
            "capture": "complete",
            "category": "recommendations",
            "event_type": "recommendation.recorded",
            "jsonl_path": str(record.jsonl_path),
            "line": 2,
            "machine": "studio",
            "slug": "cli-reflect",
            "started": "2026-04-30T13:30:00+00:00",
            "summary": "Add a reflection command before building an index.",
        }
    ]
    assert json_output.err == ""


def test_cli_workflow_events_are_reflected_as_learning_signals(tmp_path, capsys):
    active = init_session(
        agent="codex",
        slug="sandwich-mode",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )

    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "workflow",
                "role",
                str(active),
                "--role",
                "implementer",
                "--agent",
                "codex",
                "--model",
                "gpt-5.3-codex",
                "--phase",
                "implementation",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "workflow",
                "subagent",
                str(active),
                "--role",
                "explorer",
                "--agent",
                "codex",
                "--model",
                "gpt-5.3-codex",
                "--task-kind",
                "review-support",
                "--outcome",
                "findings-used",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "workflow",
                "review-outcome",
                str(active),
                "--implementer-agent",
                "codex",
                "--implementer-model",
                "gpt-5.3-codex",
                "--reviewer-agent",
                "claude",
                "--reviewer-model",
                "claude-opus",
                "--high",
                "1",
                "--medium",
                "2",
                "--low",
                "4",
                "--accepted",
                "7",
                "--fixed",
                "7",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "--root",
                str(tmp_path),
                "workflow",
                "evaluation",
                str(active),
                "--pattern",
                "codex-implementation-claude-opus-review",
                "--confidence",
                "medium",
                "--recommendation",
                "keep-testing",
                "--summary",
                "Reviewer found lifecycle risks implementer missed.",
            ]
        )
        == 0
    )
    capsys.readouterr()
    record = finalize_session(active, root=tmp_path)

    reflection = reflect_on_finalized_sessions(root=tmp_path)
    formatted = format_session_reflection(reflection)

    assert reflection.event_type_counts["workflow.role"] == 1
    assert reflection.event_type_counts["subagent.used"] == 1
    assert reflection.event_type_counts["review.outcome"] == 1
    assert reflection.event_type_counts["workflow.evaluation"] == 1
    assert [
        (item.category, item.event_type)
        for item in reflection.items
        if item.jsonl_path == record.jsonl_path
    ] == [
        ("workflow-roles", "workflow.role"),
        ("subagents", "subagent.used"),
        ("review-outcomes", "review.outcome"),
        ("workflow-evaluations", "workflow.evaluation"),
    ]
    assert "## Workflow Roles" in formatted
    assert "## Subagents" in formatted
    assert "## Review Outcomes" in formatted
    assert "## Workflow Evaluations" in formatted
    assert "model=gpt-5.3-codex" in formatted
    assert "reviewer_model=claude-opus" in formatted
    assert "pattern=codex-implementation-claude-opus-review" in formatted


def test_cli_workflow_review_outcome_rejects_negative_counts(tmp_path, capsys):
    active = init_session(
        agent="codex",
        slug="negative-review-count",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )

    exit_code = main(
        [
            "--root",
            str(tmp_path),
            "workflow",
            "review-outcome",
            str(active),
            "--high",
            "-1",
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 2
    assert "workflow count fields must be non-negative: high" in output.err
    assert "review.outcome" not in active.read_text(encoding="utf-8")


def test_reflect_ignores_corrupt_jsonl_body_without_dropping_markdown_summary(tmp_path):
    archive = tmp_path / "pipy" / "2026" / "04"
    archive.mkdir(parents=True)
    record = archive / "2026-04-30T133000Z-studio-codex-corrupt-body.jsonl"
    markdown = record.with_suffix(".md")
    record.write_text(
        (
            '{"agent":"codex","machine":"studio","partial":true,"project":"pipy",'
            '"slug":"corrupt-body","timestamp":"2026-04-30T13:30:00+00:00",'
            '"type":"session.started"}\n'
            '{"type":"auto_capture.started","summary":"PAYLOAD_SECRET should be discarded"}\n'
            '{"type":"auto_capture.ended","summary":"broken"\n'
        ),
        encoding="utf-8",
    )
    markdown.write_text("# Summary\n\nCurated summary survives corrupt JSONL body.", encoding="utf-8")

    reflection = reflect_on_finalized_sessions(root=tmp_path)
    formatted = format_session_reflection(reflection)

    assert reflection.session_count == 1
    assert reflection.sessions_with_markdown == 1
    assert reflection.event_type_counts == {}
    assert reflection.summary_event_count == 0
    assert reflection.low_signal_session_count == 0
    assert [item.to_dict() for item in reflection.items] == [
        {
            "agent": "codex",
            "capture": "partial",
            "category": "session-summaries",
            "event_type": None,
            "jsonl_path": str(record),
            "line": None,
            "machine": "studio",
            "slug": "corrupt-body",
            "started": "2026-04-30T13:30:00+00:00",
            "summary": "# Summary Curated summary survives corrupt JSONL body.",
        }
    ]
    assert "Curated summary survives corrupt JSONL body." in formatted
    assert "PAYLOAD_SECRET" not in formatted


def test_reflect_markdown_summary_snippet_is_width_limited_with_ellipsis(tmp_path):
    active = init_session(
        agent="codex",
        slug="long-summary",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    record = finalize_session(
        active,
        root=tmp_path,
        summary_text="# Summary\n\n" + ("Long curated detail. " * 30),
    )

    reflection = reflect_on_finalized_sessions(root=tmp_path)
    [item] = reflection.items

    assert item.jsonl_path == record.jsonl_path
    assert item.category == "session-summaries"
    assert item.summary.endswith("...")
    assert len(item.summary) <= 240


def test_reflect_generic_auto_summary_filter_requires_auto_phrase_at_start(tmp_path):
    auto_active = init_session(
        agent="claude",
        slug="generic-auto",
        root=tmp_path,
        machine="studio",
        partial=True,
        now=FIXED_NOW,
    )
    finalize_session(
        auto_active,
        root=tmp_path,
        summary_text="# Summary\n\nAutomatic claude capture finalized.\n\nLifecycle metadata only.",
    )
    curated_active = init_session(
        agent="codex",
        slug="curated-mention",
        root=tmp_path,
        machine="studio",
        partial=True,
        now=FIXED_NOW + timedelta(minutes=1),
    )
    curated = finalize_session(
        curated_active,
        root=tmp_path,
        summary_text=(
            "# Summary\n\n"
            "Investigated why the phrase automatic claude capture finalized appears in generic summaries."
        ),
    )

    reflection = reflect_on_finalized_sessions(root=tmp_path)

    assert [item.jsonl_path for item in reflection.items] == [curated.jsonl_path]
    assert "Investigated why the phrase" in reflection.items[0].summary


def test_format_session_reflection_renders_unknown_categories_after_canonical_order(tmp_path):
    active = init_session(
        agent="codex",
        slug="unknown-category",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    record = finalize_session(active, root=tmp_path)
    listing = list_finalized_sessions(root=tmp_path)[0]
    reflection = reflect_on_finalized_sessions(root=tmp_path)
    reflection = type(reflection)(
        root=reflection.root,
        session_count=reflection.session_count,
        sessions_with_markdown=reflection.sessions_with_markdown,
        agent_counts=reflection.agent_counts,
        capture_counts=reflection.capture_counts,
        event_type_counts=reflection.event_type_counts,
        summary_event_count=reflection.summary_event_count,
        low_signal_session_count=reflection.low_signal_session_count,
        items=[
            *reflection.items,
            SessionReflectionItem(
                category="zz-extra-category",
                listing=listing,
                summary="Extra category should render in Markdown.",
            ),
        ],
    )

    formatted = format_session_reflection(reflection)

    assert record.jsonl_path == listing.jsonl_path
    assert "## Session Summaries" in formatted
    assert "## Zz Extra Category" in formatted
    assert formatted.index("## Session Summaries") < formatted.index("## Zz Extra Category")
    assert "Extra category should render in Markdown." in formatted


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


def test_format_session_inspection_collapses_control_whitespace_in_metadata(tmp_path, capsys):
    root = tmp_path / "session\troot\nsandbox"
    archive = root / "pipy" / "2026" / "04"
    archive.mkdir(parents=True)
    record_path = archive / "2026-04-30T133000Z-studio-codex-control-whitespace.jsonl"
    markdown_path = record_path.with_suffix(".md")
    started = "2026-04-30T13:30:00+00:00\nmachine: forged"
    machine = "studio\tagent: forged"
    agent = "codex\nslug: forged"
    slug = "control\twhitespace\nsummary: forged"
    summary_text = "# Summary\n\nKeep\tintentional\nMarkdown lines.\n"
    events = [
        {
            "agent": agent,
            "machine": machine,
            "project": "pipy",
            "slug": slug,
            "timestamp": started,
            "type": "session.started",
        },
        {
            "summary": "Human inspect output collapses metadata whitespace.",
            "type": "decision.recorded",
        },
    ]
    record_path.write_text(
        "".join(f"{json.dumps(event, sort_keys=True)}\n" for event in events),
        encoding="utf-8",
    )
    markdown_path.write_text(summary_text, encoding="utf-8")

    inspection = inspect_finalized_session(record_path, root=root)
    formatted = format_session_inspection(inspection)

    assert inspection.started == started
    assert inspection.machine == machine
    assert inspection.agent == agent
    assert inspection.slug == slug
    assert inspection.jsonl_path == record_path
    assert inspection.markdown_path == markdown_path
    assert inspection.summary_text == summary_text
    assert f"jsonl_path: {' '.join(str(record_path).split())}" in formatted
    assert f"markdown_path: {' '.join(str(markdown_path).split())}" in formatted
    assert "summary_text:\n# Summary\n\nKeep\tintentional\nMarkdown lines." in formatted
    lines = formatted.splitlines()
    assert "started: 2026-04-30T13:30:00+00:00 machine: forged" in lines
    assert "machine: studio agent: forged" in lines
    assert "agent: codex slug: forged" in lines
    assert "slug: control whitespace summary: forged" in lines
    assert "machine: forged" not in lines
    assert "agent: forged" not in lines
    assert "slug: forged" not in lines
    assert "summary: forged" not in lines

    exit_code = main(["--root", str(root), "inspect", str(record_path), "--json"])
    output = capsys.readouterr()
    parsed = json.loads(output.out)

    assert exit_code == 0
    assert output.err == ""
    assert parsed["started"] == started
    assert parsed["machine"] == machine
    assert parsed["agent"] == agent
    assert parsed["slug"] == slug
    assert parsed["jsonl_path"] == str(record_path)
    assert parsed["markdown_path"] == str(markdown_path)
    assert parsed["summary_text"] == summary_text


def test_inspect_human_output_sanitizes_event_type_labels_but_json_keeps_raw(tmp_path, capsys):
    active = init_session(
        agent="codex",
        slug="inspect-event-label",
        root=tmp_path,
        machine="studio",
        now=FIXED_NOW,
    )
    raw_event_type = "tool\tcommand\nforged.label:\r 999"
    append_event(
        active,
        root=tmp_path,
        event={
            "type": raw_event_type,
            "summary": "Human inspect event labels should be one physical line.",
        },
        now=FIXED_NOW,
    )
    record = finalize_session(active, root=tmp_path)

    exit_code = main(["--root", str(tmp_path), "inspect", str(record.jsonl_path)])
    output = capsys.readouterr()

    assert exit_code == 0
    lines = output.out.splitlines()
    assert f"  {' '.join(raw_event_type.split())}: 1" in lines
    assert "forged.label:" not in lines
    assert all(line != " 999: 1" for line in lines)

    json_code = main(["--root", str(tmp_path), "inspect", str(record.jsonl_path), "--json"])
    json_output = capsys.readouterr()

    assert json_code == 0
    parsed = json.loads(json_output.out)
    assert parsed["event_types"][raw_event_type] == 1
    assert raw_event_type in parsed["event_types"]
    assert json_output.err == ""


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
