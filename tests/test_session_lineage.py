"""Tests for session lineage value objects and branch-label validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipy_harness.models import (
    RESUME_RELATIONSHIP_BRANCH,
    RESUME_RELATIONSHIP_RESUME,
    SessionLineage,
    validate_branch_label,
)
from pipy_harness.native.session_resume import (
    ResumeContext,
    build_session_lineage,
)
from pipy_session import append_event, finalize_session, init_session
from pipy_harness.native.session_resume import resume_session_from_archive


def test_validate_branch_label_accepts_safe_labels() -> None:
    assert validate_branch_label("experiment-1") == "experiment-1"
    assert validate_branch_label("  spaced label  ") == "spaced label"
    assert validate_branch_label("v2.feature_x") == "v2.feature_x"


@pytest.mark.parametrize(
    "label",
    [
        "",
        "   ",
        "../escape",
        "with/slash",
        "back\\slash",
        "~home",
        ".hidden",
        "line\nbreak",
        "tab\tchar",
        "weird$chars",
        "x" * 49,
    ],
)
def test_validate_branch_label_rejects_unsafe(label: str) -> None:
    with pytest.raises(ValueError):
        validate_branch_label(label)


def test_validate_branch_label_rejects_secret_shaped() -> None:
    # `token abc` is charset-valid (letters + space) but the project secret
    # detector redacts it, so the label is refused defense-in-depth.
    with pytest.raises(ValueError):
        validate_branch_label("token abc")


def test_session_lineage_branch_requires_label() -> None:
    with pytest.raises(ValueError):
        SessionLineage(
            parent_session_id="2026-04-30T133000Z-studio-codex-parent",
            relationship=RESUME_RELATIONSHIP_BRANCH,
            fork_timestamp="2026-05-30T00:00:00+00:00",
        )


def test_session_lineage_archive_payload_is_safe() -> None:
    lineage = SessionLineage(
        parent_session_id="2026-04-30T133000Z-studio-codex-parent",
        relationship=RESUME_RELATIONSHIP_BRANCH,
        fork_timestamp="2026-05-30T00:00:00+00:00",
        branch_label="try-idea",
        prior_provider_name="openai",
        prior_model_id="gpt-5",
        prior_turn_count=3,
    )
    payload = lineage.archive_payload()
    assert payload["relationship"] == "branch"
    assert payload["branch_label"] == "try-idea"
    assert payload["parent_session_id"].endswith("codex-parent")
    assert payload["prior_turn_count"] == 3


def test_session_lineage_rejects_path_parent_id() -> None:
    with pytest.raises(ValueError):
        SessionLineage(
            parent_session_id="dir/parent",
            relationship=RESUME_RELATIONSHIP_RESUME,
            fork_timestamp="2026-05-30T00:00:00+00:00",
        )


def test_build_session_lineage_from_context() -> None:
    context = ResumeContext(
        prior_session_id="2026-04-30T133000Z-studio-codex-parent",
        prior_provider_name="anthropic",
        prior_model_id="claude-opus",
        prior_turn_count=5,
        prior_workspace_hash="HASH",
        prior_started_at="2026-04-30T13:30:00+00:00",
        prior_ended_at="2026-04-30T14:00:00+00:00",
        prior_summary=None,
    )
    lineage = build_session_lineage(
        context,
        relationship=RESUME_RELATIONSHIP_BRANCH,
        fork_timestamp="2026-05-30T00:00:00+00:00",
        branch_label="explore",
    )
    assert lineage.parent_session_id == context.prior_session_id
    assert lineage.relationship == "branch"
    assert lineage.branch_label == "explore"
    assert lineage.prior_provider_name == "anthropic"
    assert lineage.prior_turn_count == 5


def _make_child_record(tmp_path: Path) -> Path:
    active = init_session(
        agent="pipy-native",
        slug="child",
        root=tmp_path,
        machine="studio",
        goal="child run",
        initial_fields={
            "resume": {
                "parent_session_id": "2026-04-30T133000Z-studio-codex-parent",
                "relationship": "branch",
                "branch_label": "explore",
                "fork_timestamp": "2026-05-30T00:00:00+00:00",
                # Hostile extra keys must be ignored by the reader.
                "prompt": "RAW_PROMPT_LEAK",
                "secret_token": "sk-LEAK",
            }
        },
    )
    append_event(
        active,
        root=tmp_path,
        event_type="native.session.compacted",
        summary="Context compacted.",
        payload={"compaction_dropped_group_count": 2},
    )
    record = finalize_session(active, root=tmp_path)
    return record.jsonl_path


def test_resume_reader_surfaces_lineage_and_compaction(tmp_path: Path) -> None:
    record_path = _make_child_record(tmp_path)

    context = resume_session_from_archive(record_path.stem, session_root=tmp_path)

    assert context.prior_relationship == "branch"
    assert context.prior_parent_session_id == "2026-04-30T133000Z-studio-codex-parent"
    assert context.prior_branch_label == "explore"
    assert context.prior_fork_timestamp == "2026-05-30T00:00:00+00:00"
    assert context.prior_compaction_event_count == 1


def test_resume_reader_drops_unsafe_provider_model_labels(tmp_path: Path) -> None:
    # A forged/foreign parent record whose provider/model labels carry a
    # secret-shaped value and a raw control byte must not flow into the resume
    # context, banner, or seeded system prompt.
    active = init_session(
        agent="pipy-native",
        slug="forged",
        root=tmp_path,
        machine="studio",
        goal="forged",
    )
    append_event(
        active,
        root=tmp_path,
        event_type="native.session.started",
        summary="started",
        payload={
            "provider": "api_key=sk-LEAKLEAKLEAK",
            "model_id": "model\x1b[2Jcleared",
            "cwd_sha256": "ok-hash",
        },
    )
    record = finalize_session(active, root=tmp_path)

    context = resume_session_from_archive(record.jsonl_path.stem, session_root=tmp_path)

    # Unsafe labels are dropped (fail closed to None / "unknown").
    assert context.prior_provider_name is None
    assert context.prior_model_id is None
    assert context.prior_workspace_hash == "ok-hash"

    from pipy_harness.native.session_resume import (
        compose_resume_status_line,
        compose_resume_system_block,
    )

    block = compose_resume_system_block(context)
    banner = compose_resume_status_line(context)
    for forbidden in ("api_key=", "sk-LEAKLEAKLEAK", "\x1b[2J"):
        assert forbidden not in block
        assert forbidden not in banner


def test_resume_reader_drops_unsafe_timestamps(tmp_path: Path) -> None:
    # A forged finalized record whose session.started timestamp embeds a
    # control byte must not leak it into the banner or seeded system prompt;
    # the started timestamp falls back to the regex-validated filename stamp.
    import json

    archive_dir = tmp_path / "pipy" / "2026" / "04"
    archive_dir.mkdir(parents=True)
    record_path = archive_dir / "2026-04-30T133000Z-studio-pipy-native-forgedts.jsonl"
    events = [
        {
            "type": "session.started",
            "timestamp": "started\x1b[2Jcleared",
            "machine": "studio",
            "agent": "pipy-native",
            "slug": "forgedts",
        },
        {
            "type": "native.session.completed",
            "timestamp": "ended\x1b[2Jcleared",
            "payload": {
                "provider": "fake",
                "model_id": "fake-native-bootstrap",
                "turn_count": 1,
            },
        },
    ]
    with record_path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")

    context = resume_session_from_archive(record_path.stem, session_root=tmp_path)

    assert "\x1b[2J" not in context.prior_started_at
    assert "\x1b[2J" not in context.prior_ended_at
    # The started timestamp fell back to the safe filename stamp.
    assert context.prior_started_at == "2026-04-30T133000Z"

    from pipy_harness.native.session_resume import (
        compose_resume_status_line,
        compose_resume_system_block,
    )

    assert "\x1b[2J" not in compose_resume_system_block(context)
    assert "\x1b[2J" not in compose_resume_status_line(context)


def test_resume_reader_ignores_forged_lineage_keys(tmp_path: Path) -> None:
    record_path = _make_child_record(tmp_path)
    context = resume_session_from_archive(record_path.stem, session_root=tmp_path)
    import json

    serialized = json.dumps(context.to_dict(), sort_keys=True)
    assert "RAW_PROMPT_LEAK" not in serialized
    assert "sk-LEAK" not in serialized
    assert "secret_token" not in serialized
