from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pipy_harness.adapters.subprocess import SubprocessAdapter
from pipy_harness.capture import CapturePolicy, collect_changed_file_paths
from pipy_harness.models import RunRequest


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, object] | None]] = []

    def emit(
        self,
        event_type: str,
        *,
        summary: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        self.events.append((event_type, summary, payload))


def test_subprocess_adapter_streams_output_and_reports_process_events(tmp_path, capfd):
    adapter = SubprocessAdapter()
    request = RunRequest(
        agent="custom",
        slug="stream",
        command=[
            sys.executable,
            "-c",
            "import sys; print('VISIBLE_OUT'); print('VISIBLE_ERR', file=sys.stderr)",
        ],
        cwd=tmp_path,
    )
    prepared = adapter.prepare(request)
    sink = RecordingSink()

    result = adapter.run(prepared, event_sink=sink, capture_policy=CapturePolicy())

    captured = capfd.readouterr()
    assert result.exit_code == 0
    assert "VISIBLE_OUT" in captured.out
    assert "VISIBLE_ERR" in captured.err
    assert [event[0] for event in sink.events] == [
        "agent.process.started",
        "agent.process.exited",
    ]
    assert sink.events[0][2]["argv_stored"] is False
    assert sink.events[0][2]["stdout_stored"] is False
    assert sink.events[0][2]["stderr_stored"] is False


def test_subprocess_adapter_rejects_missing_command(tmp_path):
    adapter = SubprocessAdapter()
    request = RunRequest(agent="custom", slug="missing", command=[], cwd=tmp_path)

    with pytest.raises(ValueError, match="command after --"):
        adapter.prepare(request)


def test_collect_changed_file_paths_handles_non_git_directory(tmp_path):
    assert collect_changed_file_paths(tmp_path) == ()


def test_collect_changed_file_paths_reads_git_porcelain_paths_only(tmp_path):
    _run_git(tmp_path, "init")
    changed = tmp_path / "changed.txt"
    changed.write_text("content\n", encoding="utf-8")

    assert collect_changed_file_paths(tmp_path) == ("changed.txt",)


def test_subprocess_adapter_terminates_child_on_keyboard_interrupt(tmp_path, monkeypatch):
    fake_process = FakeInterruptingProcess()

    def fake_popen(command, cwd):
        assert command == ("fake",)
        assert cwd == tmp_path
        return fake_process

    monkeypatch.setattr("pipy_harness.adapters.subprocess.subprocess.Popen", fake_popen)
    adapter = SubprocessAdapter()
    prepared = adapter.prepare(
        RunRequest(agent="custom", slug="interrupt", command=["fake"], cwd=tmp_path)
    )

    with pytest.raises(KeyboardInterrupt):
        adapter.run(prepared, event_sink=RecordingSink(), capture_policy=CapturePolicy())

    assert fake_process.terminated is True
    assert fake_process.reaped_after_terminate is True


def test_subprocess_adapter_kills_child_when_graceful_interrupt_wait_is_interrupted(
    tmp_path,
    monkeypatch,
):
    fake_process = FakeDoubleInterruptingProcess()

    def fake_popen(command, cwd):
        assert command == ("fake",)
        assert cwd == tmp_path
        return fake_process

    monkeypatch.setattr("pipy_harness.adapters.subprocess.subprocess.Popen", fake_popen)
    adapter = SubprocessAdapter()
    prepared = adapter.prepare(
        RunRequest(agent="custom", slug="double-interrupt", command=["fake"], cwd=tmp_path)
    )

    with pytest.raises(KeyboardInterrupt):
        adapter.run(prepared, event_sink=RecordingSink(), capture_policy=CapturePolicy())

    assert fake_process.terminated is True
    assert fake_process.killed is True
    assert fake_process.reaped_after_kill is True


class FakeInterruptingProcess:
    terminated = False
    reaped_after_terminate = False

    def poll(self):
        return None if not self.reaped_after_terminate else -15

    def terminate(self):
        self.terminated = True

    def kill(self):
        raise AssertionError("process should not need kill after terminate")

    def wait(self, timeout=None):
        if timeout is None:
            raise KeyboardInterrupt
        self.reaped_after_terminate = True
        return -15


class FakeDoubleInterruptingProcess:
    terminated = False
    killed = False
    reaped_after_kill = False

    def poll(self):
        return None if not self.reaped_after_kill else -9

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        if timeout is None and not self.killed:
            raise KeyboardInterrupt
        if timeout is not None:
            raise KeyboardInterrupt
        self.reaped_after_kill = True
        return -9


def _run_git(cwd: Path, *args: str) -> None:
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    completed = subprocess.run(["git", *args], cwd=cwd, check=False)
    assert completed.returncode == 0
