"""Parity row E3 behavior check: session branching/forking.

Seeds a finalized parent record, then runs the no-tool REPL product path as a
child *branch* of that parent (``RunRequest.resume`` carrying a validated
branch label). It proves the child archive records safe parent id, branch
label, fork timestamp, and relationship metadata; that a
``native.session.resumed`` event is emitted; and that the parent record is
never mutated (byte-for-byte identical before and after the child run).

Exits 0 when all branch behaviors hold, 1 otherwise. No real network/AI calls.
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

from pipy_harness.adapters.native import PipyNativeReplAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import (
    RESUME_RELATIONSHIP_BRANCH,
    RunRequest,
    SessionLineage,
)
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.runner import HarnessRunner
from pipy_session import append_event, finalize_session, init_session


def _seed_parent(root: Path) -> Path:
    active = init_session(
        agent="pipy-native",
        slug="parity-parent",
        root=root,
        machine="studio",
        goal="parent",
    )
    append_event(
        active,
        root=root,
        event_type="native.session.completed",
        summary="parent done",
        payload={"provider": "fake", "model_id": "fake-native-bootstrap", "turn_count": 2},
    )
    return finalize_session(active, root=root).jsonl_path


def main() -> int:
    root = Path(tempfile.mkdtemp())
    cwd = Path(tempfile.mkdtemp())
    parent = _seed_parent(root)
    parent_bytes_before = parent.read_bytes()

    lineage = SessionLineage(
        parent_session_id=parent.stem,
        relationship=RESUME_RELATIONSHIP_BRANCH,
        fork_timestamp="2026-05-30T00:00:00+00:00",
        branch_label="parity-branch",
        prior_provider_name="fake",
        prior_model_id="fake-native-bootstrap",
        prior_turn_count=2,
    )
    adapter = PipyNativeReplAdapter(
        provider=FakeNativeProvider(),
        input_stream=io.StringIO("/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    result = HarnessRunner(adapter=adapter).run(
        RunRequest(
            agent="pipy-native",
            slug="parity-child",
            command=[],
            cwd=cwd,
            goal="child branch",
            root=root,
            capture_policy=CapturePolicy(),
            resume=lineage,
        )
    )

    # Parent must be byte-for-byte unchanged.
    if parent.read_bytes() != parent_bytes_before:
        return 1

    events = [
        json.loads(line)
        for line in result.record.jsonl_path.read_text(encoding="utf-8").splitlines()
    ]
    started = events[0]
    resume = started.get("resume", {})
    if resume.get("relationship") != "branch":
        return 1
    if resume.get("branch_label") != "parity-branch":
        return 1
    if resume.get("parent_session_id") != parent.stem:
        return 1
    if not resume.get("fork_timestamp"):
        return 1
    if not any(e.get("type") == "native.session.resumed" for e in events):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
