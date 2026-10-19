"""Process-death evidence for the isolated prototype, not product durability claims."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from pipy_harness.native.agent.messages import AgentToolResultMessage

DIRECTORY = Path(__file__).resolve().parents[1] / "scripts" / "durable_prototype"


def load(name):
    spec = importlib.util.spec_from_file_location(name, DIRECTORY / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


experiments = load("experiments")
runtime = load("runtime")


@pytest.mark.parametrize("scenario", ["safe", "unsafe"])
@pytest.mark.parametrize("point", experiments.CRASH_POINTS)
def test_process_crash_recovery(tmp_path, scenario, point):
    experiments.case(tmp_path, scenario, point)


@pytest.mark.parametrize("scenario", ["safe", "unsafe"])
def test_sigkill_after_native_effect(tmp_path, scenario):
    experiments.case(tmp_path, scenario, "tool.0.effect", sigkill=True)


@pytest.mark.parametrize("scenario", ["safe", "unsafe"])
@pytest.mark.parametrize(
    "failure", ["admission", "tool.start", "tool.result", "tool.result.after_commit"]
)
def test_storage_failure_reopen(tmp_path, scenario, failure):
    experiments.case(tmp_path, scenario, fail_write_at=failure)


def test_concurrent_process_owner(tmp_path):
    experiments.concurrent_open(tmp_path)


def test_admission_identity_is_immutable(tmp_path):
    initial = experiments.invoke(tmp_path, "start")
    assert initial.returncode == 0, initial.stderr
    duplicate = experiments.invoke(tmp_path, "start")
    assert duplicate.returncode == 0
    assert duplicate.stdout == initial.stdout
    for scenario, flags in (
        ("safe", ("--request-id", "different")),
        ("safe", ("--prompt", "different")),
        ("unsafe", ()),
    ):
        conflict = experiments.invoke(tmp_path, "start", scenario, *flags)
        assert conflict.returncode == 1 and not conflict.stdout
    other_workspace = tmp_path / "other"
    other_workspace.mkdir()
    command = experiments.command(tmp_path, "start")
    command[command.index("--workspace") + 1] = str(other_workspace)
    conflict = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert conflict.returncode == 1 and not conflict.stdout
    resumed = experiments.invoke(tmp_path, "resume")
    assert resumed.stdout == initial.stdout
    assert experiments.effect_lines(tmp_path) == 1


@pytest.mark.parametrize("action", ["start", "resume", "inspect"])
def test_invalid_cli_paths_create_no_store(tmp_path, action):
    db = tmp_path / "missing.sqlite"
    result = subprocess.run(
        [sys.executable, str(experiments.RUNTIME), action, "--db", str(db)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 2  # usage; uncertain runs have the distinct exit 3
    assert not result.stdout
    assert list(tmp_path.iterdir()) == []
    if action == "start":
        invalid = experiments.invoke(
            tmp_path, "start", "safe", "--workspace", str(tmp_path / "absent")
        )
        assert invalid.returncode == 2 and not invalid.stdout
        assert list(tmp_path.iterdir()) == []


def test_usage_errors_cannot_be_mistaken_for_blocked(tmp_path):
    crashed = experiments.invoke(
        tmp_path, "start", "unsafe", "--crash-at", "tool.0.effect"
    )
    assert crashed.returncode == 86
    blocked = experiments.invoke(tmp_path, "resume")
    assert blocked.returncode == 3 and json.loads(blocked.stdout)["phase"] == "blocked"
    usage = experiments.invoke(
        tmp_path, "start", "unsafe", "--workspace", str(tmp_path / "absent")
    )
    assert usage.returncode == 2 and not usage.stdout
    invalid_choice = experiments.invoke(tmp_path, "start", "invalid")
    assert invalid_choice.returncode == 2 and not invalid_choice.stdout
    inspected = experiments.invoke(tmp_path, "inspect")
    assert (
        inspected.returncode == 3 and json.loads(inspected.stdout)["phase"] == "blocked"
    )
    assert experiments.effect_lines(tmp_path) == 1


def test_optimized_driver_refuses_to_skip_verification(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-O",
            str(DIRECTORY / "experiments.py"),
            "--output",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1 and "requires assertions" in result.stderr
    assert not result.stdout and list(tmp_path.iterdir()) == []


def test_failed_commit_poison_requires_reopen(tmp_path):
    store = runtime.Store(
        tmp_path / "run.sqlite", runtime.Faults(fail_write_at="admission.after_commit")
    )
    try:
        with pytest.raises(
            sqlite3.OperationalError, match="lost commit acknowledgement"
        ):
            store.admit("request", tmp_path, "safe", "experiment")
        # The write succeeded although its acknowledgement was lost. Continuing
        # on an assumed rollback would be wrong; this owner cannot execute work.
        with pytest.raises(RuntimeError, match="reopen"):
            runtime.run(store)
        assert not (tmp_path / "effect.txt").exists()
    finally:
        store.close()
    reopened = runtime.Store(tmp_path / "run.sqlite", runtime.Faults())
    try:
        assert runtime.run(reopened)["phase"] == "done"
    finally:
        reopened.close()


def test_stored_and_current_policy_both_must_allow_replay(tmp_path, monkeypatch):
    crashed = experiments.invoke(
        tmp_path, "start", "safe", "--crash-at", "tool.0.effect"
    )
    assert crashed.returncode == 86
    original = runtime.steps
    monkeypatch.setattr(
        runtime,
        "steps",
        lambda scenario: [
            (name, arguments, False) for name, arguments, _ in original(scenario)
        ],
    )
    store = runtime.Store(tmp_path / "run.sqlite", runtime.Faults())
    try:
        summary = runtime.run(store)
        assert summary["phase"] == "blocked"
        assert summary["tool_results"] == 0
        assert next(t for t in summary["tasks"] if t["id"] == "t0")["attempts"] == 1
    finally:
        store.close()


def test_native_message_context_and_correlations_survive_reopen(tmp_path, monkeypatch):
    crashed = experiments.invoke(
        tmp_path, "start", "safe", "--crash-at", "tool.0.committed"
    )
    assert crashed.returncode == 86
    requests = []
    original = runtime.FakeNativeProvider.complete

    def capture(provider, request, **kwargs):
        requests.append(request)
        return original(provider, request, **kwargs)

    monkeypatch.setattr(runtime.FakeNativeProvider, "complete", capture)
    store = runtime.Store(tmp_path / "run.sqlite", runtime.Faults())
    try:
        assert runtime.run(store)["phase"] == "done"
        assert [len(request.messages) for request in requests] == [3, 5]
        for request in requests:
            calls = {
                call.provider_correlation_id: call
                for message in request.messages
                if isinstance(message, runtime.AgentAssistantMessage)
                for call in message.tool_calls
            }
            for result in request.messages:
                if isinstance(result, AgentToolResultMessage):
                    assert result.provider_correlation_id in calls
                    assert (
                        calls[result.provider_correlation_id].tool_name
                        == result.tool_name
                    )
                    assert not result.is_error
        # The final provider request receives the real native read result.
        assert requests[-1].messages[-1].content.value == "effect\n"
        snapshot = store.summary()
        assert runtime.run(store) == snapshot
        assert len(requests) == 2  # completed generations are not called again
        body = json.loads(
            store.db.execute("SELECT body FROM entries WHERE task_id='t0'").fetchone()[
                0
            ]
        )
        assert body["request_id"].startswith("pipy-")
    finally:
        store.close()


def test_committed_read_is_reused_even_if_file_changes(tmp_path, monkeypatch):
    crashed = experiments.invoke(
        tmp_path, "start", "safe", "--crash-at", "tool.1.committed"
    )
    assert crashed.returncode == 86
    (tmp_path / "effect.txt").write_text("changed later\n", encoding="utf-8")
    requests = []
    original = runtime.FakeNativeProvider.complete

    def capture(provider, request, **kwargs):
        requests.append(request)
        return original(provider, request, **kwargs)

    monkeypatch.setattr(runtime.FakeNativeProvider, "complete", capture)
    store = runtime.Store(tmp_path / "run.sqlite", runtime.Faults())
    try:
        assert runtime.run(store)["phase"] == "done"
        assert len(requests) == 1
        assert requests[0].messages[-1].content.value == "effect\n"
        assert (tmp_path / "effect.txt").read_text(
            encoding="utf-8"
        ) == "changed later\n"
    finally:
        store.close()
