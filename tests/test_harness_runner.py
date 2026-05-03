from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pipy_harness.adapters import SubprocessAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import AdapterResult, HarnessStatus, PreparedRun, RunRequest
from pipy_harness.runner import HarnessRunner
from pipy_session import (
    SessionRecord,
    inspect_finalized_session,
    list_finalized_sessions,
    search_finalized_sessions,
    verify_session_archive,
)


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_runner_success_creates_finalized_partial_record_without_child_output(tmp_path, capfd):
    result = HarnessRunner(adapter=SubprocessAdapter(), id_factory=lambda: "run123").run(
        RunRequest(
            agent="custom",
            slug="smoke",
            command=[sys.executable, "-c", "print('hello child output')"],
            cwd=tmp_path,
            root=tmp_path / "sessions",
        )
    )

    captured = capfd.readouterr()
    assert result.exit_code == 0
    assert "hello child output" in captured.out
    assert result.record.jsonl_path.exists()
    assert result.record.markdown_path.exists()

    combined = result.record.jsonl_path.read_text(encoding="utf-8") + result.record.markdown_path.read_text(
        encoding="utf-8"
    )
    assert "hello child output" not in combined
    events = read_jsonl(result.record.jsonl_path)
    assert events[0]["type"] == "session.started"
    assert events[0]["partial"] is True
    assert events[0]["run_id"] == "run123"
    assert events[0]["event_id"] == "run123-0000"
    assert events[0]["sequence"] == 0
    assert events[0]["harness_protocol_version"] == 1
    harness_events = [event for event in events if str(event["type"]).startswith("harness.")]
    assert [event["type"] for event in harness_events] == [
        "harness.run.started",
        "harness.run.completed",
    ]
    sequenced = [event for event in events if "sequence" in event]
    assert [event["sequence"] for event in sequenced] == list(range(len(sequenced)))
    assert events[-1]["type"] == "session.finalized"
    assert verify_session_archive(root=tmp_path / "sessions").ok is True


def test_runner_nonzero_child_finalizes_and_returns_native_exit_code(tmp_path):
    result = HarnessRunner(adapter=SubprocessAdapter(), id_factory=lambda: "run-failed").run(
        RunRequest(
            agent="custom",
            slug="failure",
            command=[sys.executable, "-c", "raise SystemExit(7)"],
            cwd=tmp_path,
            root=tmp_path / "sessions",
        )
    )

    assert result.exit_code == 7
    assert result.status.value == "failed"
    events = read_jsonl(result.record.jsonl_path)
    assert events[-2]["type"] == "harness.run.failed"
    assert events[-1]["type"] == "session.finalized"
    assert verify_session_archive(root=tmp_path / "sessions").ok is True


def test_runner_records_no_full_command_or_prompt_like_argv_by_default(tmp_path):
    secret_prompt = "PROMPT_SECRET token=SECRET123"
    result = HarnessRunner(adapter=SubprocessAdapter(), id_factory=lambda: "run-secret").run(
        RunRequest(
            agent="custom",
            slug="privacy",
            command=[sys.executable, "-c", f"print({secret_prompt!r})"],
            cwd=tmp_path,
            root=tmp_path / "sessions",
        )
    )

    combined = result.record.jsonl_path.read_text(encoding="utf-8") + result.record.markdown_path.read_text(
        encoding="utf-8"
    )
    assert "SECRET123" not in combined
    assert secret_prompt not in combined
    assert f"print({secret_prompt!r})" not in combined
    assert '"argv_stored":false' in combined


def test_runner_record_files_is_opt_in_and_records_paths_only(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    root = tmp_path / "sessions"

    default_result = HarnessRunner(adapter=SubprocessAdapter(), id_factory=lambda: "run-no-files").run(
        RunRequest(
            agent="custom",
            slug="no-files",
            command=[sys.executable, "-c", "from pathlib import Path; Path('default.txt').write_text('x')"],
            cwd=repo,
            root=root,
        )
    )
    assert "default.txt" not in default_result.record.jsonl_path.read_text(encoding="utf-8")

    recorded_result = HarnessRunner(adapter=SubprocessAdapter(), id_factory=lambda: "run-files").run(
        RunRequest(
            agent="custom",
            slug="with-files",
            command=[
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "Path('recorded.txt').write_text('x'); "
                    "Path('secret_config.py').write_text('x'); "
                    "Path('auth_token.py').write_text('x')"
                ),
            ],
            cwd=repo,
            root=root,
            capture_policy=CapturePolicy(record_file_paths=True),
        )
    )
    events = read_jsonl(recorded_result.record.jsonl_path)
    file_events = [event for event in events if event["type"] == "workspace.files.changed"]
    assert len(file_events) == 1
    payload = file_events[0]["payload"]
    assert "recorded.txt" in payload["paths"]
    assert "secret_config.py" in payload["paths"]
    assert "auth_token.py" in payload["paths"]
    assert "[REDACTED]" not in payload["paths"]
    assert payload["diffs_stored"] is False
    assert payload["file_contents_stored"] is False
    assert "write_text('x')" not in recorded_result.record.jsonl_path.read_text(encoding="utf-8")


def test_new_records_remain_compatible_with_catalog_commands(tmp_path):
    root = tmp_path / "sessions"
    result = HarnessRunner(adapter=SubprocessAdapter(), id_factory=lambda: "run-catalog").run(
        RunRequest(
            agent="custom",
            slug="catalog",
            command=[sys.executable, "-c", "print('catalog output')"],
            cwd=tmp_path,
            root=root,
        )
    )

    listings = list_finalized_sessions(root=root)
    assert [listing.jsonl_path for listing in listings] == [result.record.jsonl_path]
    assert search_finalized_sessions("harness.run.completed", root=root)
    inspection = inspect_finalized_session(result.record.jsonl_path, root=root)
    assert inspection.event_types["session.finalized"] == 1
    assert verify_session_archive(root=root).ok is True


def test_runner_returns_after_recorder_finalize(tmp_path):
    finalized = {"done": False}

    class MarkingRecorder:
        def init(self, request, *, run_id, started_at, initial_fields):
            return init_path

        def append(self, active_path, event, *, root):
            with init_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event) + "\n")

        def finalize(self, active_path, *, root, summary):
            finalized["done"] = True
            return SessionRecord(active_path)

    init_path = tmp_path / "active.jsonl"
    init_path.write_text('{"type":"session.started"}\n', encoding="utf-8")

    result = HarnessRunner(adapter=SubprocessAdapter(), recorder=MarkingRecorder()).run(
        RunRequest(
            agent="custom",
            slug="finalize-order",
            command=[sys.executable, "-c", "raise SystemExit(3)"],
            cwd=tmp_path,
        )
    )

    assert result.exit_code == 3
    assert finalized["done"] is True


def test_runner_finalizes_aborted_record_when_adapter_raises_keyboard_interrupt(tmp_path):
    result = HarnessRunner(adapter=InterruptingAdapter(), id_factory=lambda: "run-abort").run(
        RunRequest(
            agent="custom",
            slug="abort",
            command=["fake"],
            cwd=tmp_path,
            root=tmp_path / "sessions",
            capture_policy=CapturePolicy(record_file_paths=True),
        )
    )

    assert result.exit_code == 130
    assert result.status == HarnessStatus.ABORTED
    assert result.error_type == "KeyboardInterrupt"
    events = read_jsonl(result.record.jsonl_path)
    assert [event["type"] for event in events[-2:]] == ["harness.run.aborted", "session.finalized"]
    assert events[-2]["payload"]["duration_seconds"] >= 0
    assert "none collected because the run did not complete" in result.record.markdown_path.read_text(
        encoding="utf-8"
    )
    assert verify_session_archive(root=tmp_path / "sessions").ok is True


def test_runner_finalizes_failed_record_when_adapter_raises_exception(tmp_path):
    result = HarnessRunner(adapter=ExplodingAdapter(), id_factory=lambda: "run-error").run(
        RunRequest(
            agent="custom",
            slug="adapter-error",
            command=["fake"],
            cwd=tmp_path,
            root=tmp_path / "sessions",
        )
    )

    assert result.exit_code == 1
    assert result.status == HarnessStatus.FAILED
    assert result.error_type == "RuntimeError"
    assert result.error_message == "adapter exploded"
    events = read_jsonl(result.record.jsonl_path)
    assert [event["type"] for event in events[-2:]] == ["harness.run.failed", "session.finalized"]
    assert events[-2]["payload"]["error_type"] == "RuntimeError"
    assert events[-2]["payload"]["error_message"] == "adapter exploded"
    assert events[-2]["payload"]["duration_seconds"] >= 0
    assert verify_session_archive(root=tmp_path / "sessions").ok is True


def test_runner_clock_sets_monotonic_sequence_not_event_time_order(tmp_path):
    times = [
        datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC),
        datetime(2026, 5, 1, 12, 0, 3, tzinfo=UTC),
        datetime(2026, 5, 1, 12, 0, 1, tzinfo=UTC),
        datetime(2026, 5, 1, 12, 0, 2, tzinfo=UTC),
    ]

    def clock() -> datetime:
        if times:
            return times.pop(0)
        return datetime(2026, 5, 1, 12, 0, 4, tzinfo=UTC) + timedelta(seconds=len(times))

    result = HarnessRunner(adapter=SubprocessAdapter(), clock=clock, id_factory=lambda: "run-clock").run(
        RunRequest(
            agent="custom",
            slug="clock",
            command=[sys.executable, "-c", "pass"],
            cwd=tmp_path,
            root=tmp_path / "sessions",
        )
    )
    events = read_jsonl(result.record.jsonl_path)
    sequenced = [event for event in events if "sequence" in event]
    assert [event["sequence"] for event in sequenced] == list(range(len(sequenced)))


class InterruptingAdapter:
    name = "fake-interrupt"

    def prepare(self, request: RunRequest) -> PreparedRun:
        return PreparedRun(
            command=tuple(request.command),
            cwd=request.cwd,
            adapter=self.name,
            command_executable="fake",
        )

    def run(self, prepared, *, event_sink, capture_policy) -> AdapterResult:
        raise KeyboardInterrupt


class ExplodingAdapter:
    name = "fake-exception"

    def prepare(self, request: RunRequest) -> PreparedRun:
        return PreparedRun(
            command=tuple(request.command),
            cwd=request.cwd,
            adapter=self.name,
            command_executable="fake",
        )

    def run(self, prepared, *, event_sink, capture_policy) -> AdapterResult:
        raise RuntimeError("adapter exploded")


def _run_git(cwd: Path, *args: str) -> None:
    import shutil
    import subprocess

    if shutil.which("git") is None:
        raise AssertionError("git is required for this test")
    completed = subprocess.run(["git", *args], cwd=cwd, check=False)
    assert completed.returncode == 0
