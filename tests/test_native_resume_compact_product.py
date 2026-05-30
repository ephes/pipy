"""Product-path tests: no-tool REPL resume, branch, and /compact."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from pipy_harness.cli import main
from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_session import append_event, finalize_session, init_session


def _make_parent_record(root: Path, *, slug: str = "parent") -> Path:
    active = init_session(
        agent="pipy-native",
        slug=slug,
        root=root,
        machine="studio",
        goal="parent run",
        now=datetime(2026, 4, 30, 13, 30, 0, tzinfo=UTC),
    )
    append_event(
        active,
        root=root,
        event_type="native.session.started",
        summary="Native session started.",
        payload={"provider": "fake", "model_id": "fake-native-bootstrap"},
    )
    append_event(
        active,
        root=root,
        event_type="native.session.completed",
        summary="Native session completed.",
        payload={
            "provider": "fake",
            "model_id": "fake-native-bootstrap",
            "turn_count": 2,
        },
    )
    record = finalize_session(
        active, root=root, summary_text="# Summary\n\nPRIOR_SUMMARY_SECRET_BODY"
    )
    return record.jsonl_path


class _CliFakeReplProvider:
    name = "fake"
    captured: list[ProviderRequest] = []

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        type(self).captured.append(request)
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text=f"OUT_{request.provider_turn_index}",
        )


def _read_events(path: Path) -> list[dict]:
    import json

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _finalized(root: Path) -> list[Path]:
    return sorted((root / "pipy").glob("*/*/*.jsonl"))


def test_no_tool_resume_seeds_prompt_and_records_safe_metadata(
    tmp_path, capfd, monkeypatch
) -> None:
    root = tmp_path / "sessions"
    parent = _make_parent_record(root)
    parent_bytes = parent.read_bytes()

    _CliFakeReplProvider.captured = []
    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", _CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("hello\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "resumed",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--resume",
            parent.stem,
        ]
    )
    captured = capfd.readouterr()
    assert exit_code == 0
    # Safe resumed-state banner appears, with parent id but no summary body.
    assert "Resumed (resume) from session" in captured.err
    assert parent.stem in captured.err
    assert "PRIOR_SUMMARY_SECRET_BODY" not in captured.err

    # The provider request system prompt is seeded with the safe resume block.
    assert _CliFakeReplProvider.captured
    system_prompt = _CliFakeReplProvider.captured[0].system_prompt
    assert "Resumed from session" in system_prompt
    assert "PRIOR_SUMMARY_SECRET_BODY" not in system_prompt

    # The parent record is never mutated.
    assert parent.read_bytes() == parent_bytes

    # The new child record carries only safe resume metadata.
    children = [p for p in _finalized(root) if p != parent]
    assert len(children) == 1
    events = _read_events(children[0])
    started = events[0]
    assert started["resume"]["relationship"] == "resume"
    assert started["resume"]["parent_session_id"] == parent.stem
    assert started["resume"]["branch_label"] is None
    assert [e for e in events if e["type"] == "native.session.resumed"]
    combined = children[0].read_text(encoding="utf-8")
    assert "PRIOR_SUMMARY_SECRET_BODY" not in combined


def test_no_tool_branch_records_branch_label(tmp_path, capfd, monkeypatch) -> None:
    root = tmp_path / "sessions"
    parent = _make_parent_record(root)
    parent_bytes = parent.read_bytes()

    _CliFakeReplProvider.captured = []
    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", _CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "branched",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--resume",
            parent.stem,
            "--branch",
            "try-idea",
        ]
    )
    captured = capfd.readouterr()
    assert exit_code == 0
    assert "Resumed (branch try-idea)" in captured.err
    assert parent.read_bytes() == parent_bytes

    children = [p for p in _finalized(root) if p != parent]
    events = _read_events(children[0])
    assert events[0]["resume"]["relationship"] == "branch"
    assert events[0]["resume"]["branch_label"] == "try-idea"


def test_branch_without_resume_is_rejected(tmp_path, capfd, monkeypatch) -> None:
    root = tmp_path / "sessions"
    monkeypatch.setattr(sys, "stdin", StringIO("/exit\n"))
    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "bad-branch",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--branch",
            "orphan",
        ]
    )
    captured = capfd.readouterr()
    assert exit_code == 2
    assert "--branch requires --resume" in captured.err


def test_resume_missing_record_is_rejected(tmp_path, capfd, monkeypatch) -> None:
    root = tmp_path / "sessions"
    monkeypatch.setattr(sys, "stdin", StringIO("/exit\n"))
    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "bad-resume",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--resume",
            "2026-04-30T133000Z-studio-pipy-native-nope",
        ]
    )
    captured = capfd.readouterr()
    assert exit_code == 2
    assert "not found" in captured.err


def test_no_tool_manual_compact_records_event(tmp_path, capfd, monkeypatch) -> None:
    root = tmp_path / "sessions"
    _CliFakeReplProvider.captured = []
    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", _CliFakeReplProvider)
    # Three exchanges, then /compact (keeps 2, drops 1), then exit.
    monkeypatch.setattr(sys, "stdin", StringIO("a\nb\nc\n/compact\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "compacted",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )
    captured = capfd.readouterr()
    assert exit_code == 0
    assert "compacted conversation context" in captured.err

    events = _read_events(_finalized(root)[0])
    compacted = [e for e in events if e["type"] == "native.session.compacted"]
    assert len(compacted) == 1
    assert compacted[0]["payload"]["compaction_trigger"] == "manual"
    assert compacted[0]["payload"]["compaction_dropped_exchange_count"] == 1
    completed = [e for e in events if e["type"] == "native.session.completed"][0]
    assert completed["payload"]["compaction_count"] == 1


def test_no_tool_auto_compaction_threshold_fires(tmp_path, capfd, monkeypatch) -> None:
    root = tmp_path / "sessions"
    _CliFakeReplProvider.captured = []
    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", _CliFakeReplProvider)
    # Six provider turns: by the sixth, 5 accumulated exchanges exceed the
    # default no-tool threshold (4), so automatic compaction fires.
    monkeypatch.setattr(sys, "stdin", StringIO("1\n2\n3\n4\n5\n6\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "auto-compacted",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )
    capfd.readouterr()
    assert exit_code == 0

    events = _read_events(_finalized(root)[0])
    auto = [
        e
        for e in events
        if e["type"] == "native.session.compacted"
        and e["payload"].get("compaction_trigger") == "auto"
    ]
    assert auto, "expected an automatic compaction event"
