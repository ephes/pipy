from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.cli import main
from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest, ProviderResult
from pipy_session import (
    inspect_finalized_session,
    list_finalized_sessions,
    search_finalized_sessions,
    verify_session_archive,
)


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def assert_no_structured_status_stdout(stdout: str) -> None:
    # The exact stdout assertions above this helper pin today's behavior; this
    # guard records that default native stdout must not become structured status
    # output if those text fixtures are relaxed later.
    for line in stdout.splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        assert not (
            isinstance(parsed, dict)
            and parsed.get("schema") == "pipy.native_output"
            and "status" in parsed
        )


def test_cli_native_smoke_uses_fake_provider_and_finalizes_record(tmp_path, capfd):
    root = tmp_path / "sessions"

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--slug",
            "native-smoke",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--goal",
            "Native bootstrap smoke",
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == "pipy native fake provider completed.\n"
    assert_no_structured_status_stdout(captured.out)
    assert "pipy native fake provider completed." not in captured.err
    assert "session finalized" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types[-1] == "session.finalized"
    assert "native.session.started" in event_types
    assert "native.provider.completed" in event_types
    assert not [event_type for event_type in event_types if str(event_type).startswith("native.tool.")]
    provider_payloads = [
        event["payload"] for event in events if event["type"] == "native.provider.completed"
    ]
    assert provider_payloads[0]["provider"] == "fake"
    assert provider_payloads[0]["model_id"] == "fake-native-bootstrap"
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "pipy native fake provider completed." not in combined
    assert "You are the native pipy runtime bootstrap" not in combined
    assert verify_session_archive(root=root).ok is True
    assert list_finalized_sessions(root=root)[0].jsonl_path == finalized[0]
    assert search_finalized_sessions("native.provider.completed", root=root)
    assert not search_finalized_sessions("native.tool.completed", root=root)
    assert not search_finalized_sessions("pipy native fake provider completed.", root=root)
    inspection = inspect_finalized_session(finalized[0], root=root)
    assert inspection.event_types["native.session.completed"] == 1
    assert "native.tool.completed" not in inspection.event_types


def test_cli_native_rejects_command_after_separator(tmp_path, capsys):
    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--slug",
            "native-command",
            "--root",
            str(tmp_path / "sessions"),
            "--goal",
            "Native command rejection",
            "--",
            sys.executable,
            "-c",
            "print('should not run')",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "do not accept a command" in captured.err


def test_cli_native_openai_provider_is_selectable_without_storing_output(tmp_path, capfd, monkeypatch):
    root = tmp_path / "sessions"

    class CliFakeOpenAIProvider:
        name = "openai"
        model_id = "gpt-test"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="OPENAI_OUTPUT_SHOULD_PRINT_ONLY",
                usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                metadata={"provider_response_store_requested": False, "response_status": "completed"},
            )

    monkeypatch.setattr("pipy_harness.cli.OpenAIResponsesProvider", CliFakeOpenAIProvider)

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-provider",
            "openai",
            "--native-model",
            "gpt-test",
            "--slug",
            "openai-smoke",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--goal",
            "Say hello briefly",
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == "OPENAI_OUTPUT_SHOULD_PRINT_ONLY\n"
    assert_no_structured_status_stdout(captured.out)
    assert "OPENAI_OUTPUT_SHOULD_PRINT_ONLY" not in captured.err
    assert "session finalized" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    events = read_jsonl(finalized[0])
    provider_completed = [event for event in events if event["type"] == "native.provider.completed"][0]
    assert provider_completed["payload"]["provider"] == "openai"
    assert provider_completed["payload"]["model_id"] == "gpt-test"
    assert provider_completed["payload"]["provider_metadata"] == {
        "provider_response_store_requested": False,
        "response_status": "completed",
    }
    assert not [event["type"] for event in events if str(event["type"]).startswith("native.tool.")]
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "OPENAI_OUTPUT_SHOULD_PRINT_ONLY" not in combined
    assert "You are the native pipy runtime bootstrap" not in combined
    assert not search_finalized_sessions("OPENAI_OUTPUT_SHOULD_PRINT_ONLY", root=root)
    assert verify_session_archive(root=root).ok is True


def test_cli_native_openai_failure_does_not_print_or_store_provider_final_text(
    tmp_path, capfd, monkeypatch
):
    root = tmp_path / "sessions"

    class CliFailingOpenAIProvider:
        name = "openai"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.FAILED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="OPENAI_OUTPUT_SHOULD_NOT_PRINT_ON_FAILURE",
                metadata={"provider_response_store_requested": False},
                error_type="OpenAITestFailure",
                error_message="provider failed safely",
            )

    monkeypatch.setattr("pipy_harness.cli.OpenAIResponsesProvider", CliFailingOpenAIProvider)

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-provider",
            "openai",
            "--native-model",
            "gpt-test",
            "--slug",
            "openai-provider-failed",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--goal",
            "Say hello briefly",
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert_no_structured_status_stdout(captured.out)
    assert "OpenAITestFailure" in captured.err
    assert "OPENAI_OUTPUT_SHOULD_NOT_PRINT_ON_FAILURE" not in captured.err
    assert "session finalized" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert "native.provider.failed" in event_types
    assert "native.tool.skipped" in event_types
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "OPENAI_OUTPUT_SHOULD_NOT_PRINT_ON_FAILURE" not in combined
    assert not search_finalized_sessions("OPENAI_OUTPUT_SHOULD_NOT_PRINT_ON_FAILURE", root=root)
    assert verify_session_archive(root=root).ok is True


def test_cli_native_openai_missing_credentials_finalizes_failed_record(tmp_path, capfd, monkeypatch):
    root = tmp_path / "sessions"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-provider",
            "openai",
            "--native-model",
            "gpt-test",
            "--slug",
            "openai-missing-key",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--goal",
            "Say hello briefly",
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "OpenAIAuthError" in captured.err
    assert "API key is required" in captured.err
    assert "session finalized" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    events = read_jsonl(finalized[0])
    provider_failed = [event for event in events if event["type"] == "native.provider.failed"][0]
    assert provider_failed["payload"]["provider"] == "openai"
    assert provider_failed["payload"]["error_type"] == "OpenAIAuthError"
    assert "API key is required" in provider_failed["payload"]["error_message"]
    tool_skipped = [event for event in events if event["type"] == "native.tool.skipped"][0]
    assert tool_skipped["payload"]["reason"] == "provider_not_succeeded"
    assert verify_session_archive(root=root).ok is True


def test_cli_native_openai_requires_model_before_creating_record(tmp_path, capsys):
    root = tmp_path / "sessions"

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-provider",
            "openai",
            "--slug",
            "openai-missing-model",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--goal",
            "Say hello briefly",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "--native-model is required" in captured.err
    assert not root.exists()


def test_cli_native_requires_goal_before_creating_record(tmp_path, capsys):
    root = tmp_path / "sessions"

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--slug",
            "native-missing-goal",
            "--root",
            str(root),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "require --goal" in captured.err
    assert not root.exists()


def test_cli_subprocess_behavior_still_requires_command(tmp_path, capsys):
    exit_code = main(["run", "--agent", "custom", "--slug", "missing", "--root", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "command after --" in captured.err
