"""Catalog surfacing of resume/branch/compaction metadata (read-only, safe)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipy_session import append_event, finalize_session, init_session
from pipy_session.catalog import (
    inspect_finalized_session,
    list_finalized_sessions,
    resolve_finalized_record,
)
from pipy_session.export import export_session


def _make_branch_child(root: Path) -> Path:
    active = init_session(
        agent="pipy-native",
        slug="child",
        root=root,
        machine="studio",
        goal="child run",
        initial_fields={
            "resume": {
                "parent_session_id": "2026-04-30T133000Z-studio-pipy-native-parent",
                "relationship": "branch",
                "branch_label": "explore",
                "fork_timestamp": "2026-05-30T00:00:00+00:00",
                "prompt": "RAW_PROMPT_LEAK",
            }
        },
    )
    append_event(
        active,
        root=root,
        event_type="native.session.compacted",
        summary="Context compacted.",
        payload={"compaction_dropped_group_count": 2, "secret": "RAW_LEAK"},
    )
    record = finalize_session(active, root=root)
    return record.jsonl_path


def test_list_surfaces_branch_lineage(tmp_path: Path) -> None:
    _make_branch_child(tmp_path)
    listing = list_finalized_sessions(root=tmp_path)[0]
    assert listing.relationship == "branch"
    assert listing.branch_label == "explore"
    serialized = json.dumps(listing.to_dict(), sort_keys=True)
    assert "RAW_PROMPT_LEAK" not in serialized
    assert "prompt" not in serialized


def test_inspect_surfaces_compaction_count(tmp_path: Path) -> None:
    record = _make_branch_child(tmp_path)
    inspection = inspect_finalized_session(record.stem, root=tmp_path)
    assert inspection.compaction_event_count == 1
    assert inspection.relationship == "branch"
    serialized = json.dumps(inspection.to_dict(), sort_keys=True)
    assert "RAW_LEAK" not in serialized
    assert "RAW_PROMPT_LEAK" not in serialized


def test_export_surfaces_safe_lineage_only(tmp_path: Path) -> None:
    record = _make_branch_child(tmp_path)
    payload = export_session(record.stem, session_root=tmp_path)
    metadata = payload["metadata"]
    assert isinstance(metadata, dict)
    resume = metadata["resume"]
    assert isinstance(resume, dict)
    assert resume["relationship"] == "branch"
    assert resume["branch_label"] == "explore"
    assert "prompt" not in resume
    assert metadata["compaction_event_count"] == 1
    serialized = json.dumps(payload, sort_keys=True)
    assert "RAW_PROMPT_LEAK" not in serialized
    assert "RAW_LEAK" not in serialized


def test_catalog_drops_control_bytes_in_forged_lineage(tmp_path: Path) -> None:
    # A forged child record whose branch label embeds a clear-screen escape
    # must not reach the human list/inspect output as a raw control byte.
    active = init_session(
        agent="pipy-native",
        slug="forged-branch",
        root=tmp_path,
        machine="studio",
        initial_fields={
            "resume": {
                "parent_session_id": "2026-04-30T133000Z-studio-pipy-native-parent",
                "relationship": "branch",
                "branch_label": "evil\x1b[2Jlabel",
                "fork_timestamp": "2026-05-30T00:00:00+00:00",
            }
        },
    )
    record = finalize_session(active, root=tmp_path)

    from pipy_session.catalog import (
        format_session_inspection,
        format_session_table,
    )

    listing = list_finalized_sessions(root=tmp_path)[0]
    # The control-byte branch label is dropped entirely (fail closed).
    assert listing.branch_label is None
    table = format_session_table([listing])
    assert "\x1b[2J" not in table
    inspection = inspect_finalized_session(record.jsonl_path.stem, root=tmp_path)
    assert "\x1b[2J" not in format_session_inspection(inspection)


def test_catalog_drops_secret_shaped_lineage(tmp_path: Path) -> None:
    # A forged child record whose branch label is secret-shaped must not reach
    # human list/inspect output or export metadata.
    active = init_session(
        agent="pipy-native",
        slug="forged-secret",
        root=tmp_path,
        machine="studio",
        initial_fields={
            "resume": {
                "parent_session_id": "2026-04-30T133000Z-studio-pipy-native-parent",
                "relationship": "branch",
                "branch_label": "api_key=sk-LEAKLEAKLEAK",
                "fork_timestamp": "2026-05-30T00:00:00+00:00",
            }
        },
    )
    record = finalize_session(active, root=tmp_path)

    from pipy_session.catalog import (
        format_session_inspection,
        format_session_table,
    )

    listing = list_finalized_sessions(root=tmp_path)[0]
    assert listing.branch_label is None
    assert "sk-LEAKLEAKLEAK" not in format_session_table([listing])
    inspection = inspect_finalized_session(record.jsonl_path.stem, root=tmp_path)
    assert "sk-LEAKLEAKLEAK" not in format_session_inspection(inspection)

    payload = export_session(record.jsonl_path.stem, session_root=tmp_path)
    assert "sk-LEAKLEAKLEAK" not in json.dumps(payload, sort_keys=True)


def test_resolve_rejects_symlinked_record(tmp_path: Path) -> None:
    record = _make_branch_child(tmp_path)
    link = record.with_name("2026-04-30T133000Z-studio-pipy-native-link.jsonl")
    link.symlink_to(record)
    with pytest.raises(ValueError):
        inspect_finalized_session(link, root=tmp_path)


def test_resolve_rejects_out_of_archive_record(tmp_path: Path) -> None:
    outside = tmp_path / "loose.jsonl"
    outside.write_text(
        json.dumps({"type": "session.started"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError):
        resolve_finalized_record(outside, root=tmp_path)


def test_resolve_rejects_active_in_progress_record(tmp_path: Path) -> None:
    active = init_session(
        agent="pipy-native", slug="active", root=tmp_path, machine="studio"
    )
    with pytest.raises((FileNotFoundError, ValueError)):
        resolve_finalized_record(active, root=tmp_path)
