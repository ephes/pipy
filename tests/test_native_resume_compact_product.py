"""Product-path tests: no-tool REPL durable ``/compact``.

The old metadata-only ``--resume RECORD`` / ``--branch LABEL`` repl flags were
retired in favour of the native product session tree (Pi-style
``--session``/``--fork``/``-c``/``-r``); ``pipy-session resume-info`` remains the
separate archive utility. This module now covers the durable ``/compact``
product behavior that those flags used to share a file with.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from pipy_harness.cli import main
from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult


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
