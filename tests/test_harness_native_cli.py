from __future__ import annotations

import json
import hashlib
import sys
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.cli import main
from pipy_harness.models import HarnessStatus
from pipy_harness.native import (
    NATIVE_PATCH_APPLY_RECORDED_EVENT,
    NATIVE_VERIFICATION_RECORDED_EVENT,
    OpenAICodexProviderError,
    PROVIDER_PATCH_PROPOSAL_METADATA_KEY,
    PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY,
    PROVIDER_TOOL_INTENT_METADATA_KEY,
    NativeToolStatus,
    NativeVerificationApprovalDecision,
    NativeVerificationGateDecision,
    NativeVerificationReason,
    NativeVerificationRequest,
    NativeVerificationResult,
    ProviderRequest,
    ProviderResult,
)
from pipy_session import (
    inspect_finalized_session,
    list_finalized_sessions,
    search_finalized_sessions,
    verify_session_archive,
)


@pytest.fixture(autouse=True)
def isolate_native_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PIPY_NATIVE_DEFAULTS_PATH", str(tmp_path / "native-defaults.json"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def parse_single_json_stdout(stdout: str) -> dict[str, object]:
    lines = stdout.splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert isinstance(parsed, dict)
    return parsed


def safe_repl_patch_proposal() -> dict[str, object]:
    return {
        "proposal_source": "pipy_owned_patch_proposal",
        "tool_request_id": "native-tool-0001",
        "turn_index": 0,
        "status": "proposed",
        "reason_label": "structured_proposal_accepted",
        "file_count": 1,
        "operation_count": 1,
        "operation_labels": ["modify"],
        "patch_text_stored": False,
        "diffs_stored": False,
        "file_contents_stored": False,
        "prompt_stored": False,
        "model_output_stored": False,
        "provider_responses_stored": False,
        "raw_transcript_imported": False,
        "workspace_mutated": False,
    }


def repl_apply_proposal_text(path: str, old_text: str, new_text: str) -> str:
    return (
        "Review this one-file proposal.\n"
        "```pipy-apply-proposal-v1\n"
        "operation: modify\n"
        f"workspace_relative_path: {path}\n"
        f"expected_sha256: {hashlib.sha256(old_text.encode('utf-8')).hexdigest()}\n"
        "--- replacement_text ---\n"
        f"{new_text}"
        "--- end_replacement_text ---\n"
        "```\n"
    )


def repl_verification_result(
    request: NativeVerificationRequest,
    gate: NativeVerificationGateDecision,
    *,
    status: NativeToolStatus,
    reason: NativeVerificationReason,
    exit_code: int,
) -> NativeVerificationResult:
    now = datetime.now(UTC)
    return NativeVerificationResult(
        status=status,
        reason_label=reason,
        tool_request_id=request.tool_request_id,
        turn_index=request.turn_index,
        command_label="just-check",
        started_at=now,
        ended_at=now,
        exit_code=exit_code,
        approval_policy=request.approval_policy.mode,
        approval_decision=gate.approval_decision,
        sandbox_policy=request.sandbox_policy.mode,
        workspace_read_allowed=request.sandbox_policy.workspace_read_allowed,
        filesystem_mutation_allowed=request.sandbox_policy.filesystem_mutation_allowed,
        shell_execution_allowed=request.sandbox_policy.shell_execution_allowed,
        network_access_allowed=request.sandbox_policy.network_access_allowed,
        scope_label=request.scope_label,
        error_label=None
        if status == NativeToolStatus.SUCCEEDED
        else NativeVerificationReason.COMMAND_FAILED.value,
    )


def test_cli_openai_codex_auth_login_uses_no_browser_and_reports_success(
    capfd,
    monkeypatch,
) -> None:
    captured_call: dict[str, object] = {}

    class CliFakeAuthManager:
        def login_interactive(self, *, input_stream, output_stream, open_browser: bool):
            captured_call["input_stream"] = input_stream
            captured_call["output_stream"] = output_stream
            captured_call["open_browser"] = open_browser
            return object()

    monkeypatch.setattr("pipy_harness.cli.OpenAICodexAuthManager", CliFakeAuthManager)

    exit_code = main(["auth", "openai-codex", "login", "--no-browser"])

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert "openai-codex OAuth login stored" in captured.err
    assert captured_call["open_browser"] is False


def test_cli_openai_codex_auth_login_reports_sanitized_provider_error(
    capfd,
    monkeypatch,
) -> None:
    class CliFailingAuthManager:
        def login_interactive(self, *, input_stream, output_stream, open_browser: bool):
            raise OpenAICodexProviderError("safe auth failure")

    monkeypatch.setattr("pipy_harness.cli.OpenAICodexAuthManager", CliFailingAuthManager)

    exit_code = main(["auth", "openai-codex", "login", "--no-browser"])

    captured = capfd.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "OpenAICodexProviderError" in captured.err
    assert "safe auth failure" in captured.err


def test_cli_native_repl_repeats_no_tool_provider_turns_and_finalizes_record(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    captured_requests: list[ProviderRequest] = []

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text=f"REPL_OUTPUT_{request.provider_turn_index}",
                usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                metadata={
                    PROVIDER_TOOL_INTENT_METADATA_KEY: {"raw_args": "SHOULD_NOT_PERSIST"},
                    "raw_provider_response": "SHOULD_NOT_PERSIST",
                },
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("FIRST_REPL_PROMPT\nSECOND_REPL_PROMPT\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == "REPL_OUTPUT_0\nREPL_OUTPUT_1\n"
    assert "pipy-native>" in captured.err
    assert "session finalized" in captured.err
    assert [
        (request.provider_turn_index, request.provider_turn_label, request.user_prompt)
        for request in captured_requests
    ] == [
        (0, "initial", "FIRST_REPL_PROMPT"),
        (1, "no_tool_repl", "SECOND_REPL_PROMPT"),
    ]
    assert all(request.tool_observation is None for request in captured_requests)

    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.provider.started") == 2
    assert event_types.count("native.provider.completed") == 2
    assert not [event_type for event_type in event_types if str(event_type).startswith("native.tool.")]
    assert "native.patch.proposal.recorded" not in event_types
    assert "native.verification.recorded" not in event_types
    provider_payloads = [
        event["payload"] for event in events if event["type"] == "native.provider.completed"
    ]
    assert [payload["provider_turn_index"] for payload in provider_payloads] == [0, 1]
    assert [payload["provider_turn_label"] for payload in provider_payloads] == [
        "initial",
        "no_tool_repl",
    ]
    assert provider_payloads[0]["provider_metadata"] == {}
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["mode"] == "repl"
    assert completed_payload["tools_enabled"] is True
    assert completed_payload["read_only_commands_enabled"] is True
    assert completed_payload["read_command_used"] is False
    assert completed_payload["turn_count"] == 2
    assert completed_payload["exit_reason"] == "explicit_exit"
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "FIRST_REPL_PROMPT" not in combined
    assert "SECOND_REPL_PROMPT" not in combined
    assert "REPL_OUTPUT_0" not in combined
    assert "REPL_OUTPUT_1" not in combined
    assert "SHOULD_NOT_PERSIST" not in combined
    assert verify_session_archive(root=root).ok is True
    assert list_finalized_sessions(root=root)[0].jsonl_path == finalized[0]
    assert search_finalized_sessions("native.provider.completed", root=root)
    assert not search_finalized_sessions("FIRST_REPL_PROMPT", root=root)
    inspection = inspect_finalized_session(finalized[0], root=root)
    assert inspection.event_types["native.session.completed"] == 1


def test_cli_native_repl_eof_exits_cleanly_without_provider_turn(tmp_path, capfd, monkeypatch):
    root = tmp_path / "sessions"
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("provider should not be called on immediate EOF")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO(""))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-eof",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert provider_calls == 0
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    assert "native.provider.started" not in [event["type"] for event in events]
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["exit_reason"] == "eof"
    assert completed_payload["turn_count"] == 0
    assert completed_payload["read_command_used"] is False
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_interrupt_finalizes_aborted_record(tmp_path, capfd, monkeypatch):
    root = tmp_path / "sessions"

    class InterruptingStdin:
        def readline(self) -> str:
            raise KeyboardInterrupt

    monkeypatch.setattr(sys, "stdin", InterruptingStdin())

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-interrupt",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 130
    assert captured.out == ""
    assert "KeyboardInterrupt" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert not [event_type for event_type in event_types if event_type.startswith("native.tool.")]
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["status"] == "aborted"
    assert completed_payload["exit_code"] == 130
    assert completed_payload["exit_reason"] == "interrupt"
    assert completed_payload["read_command_used"] is False
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_skips_blank_lines_and_accepts_quit(tmp_path, capfd, monkeypatch) -> None:
    root = tmp_path / "sessions"
    captured_requests: list[ProviderRequest] = []

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="ONLY_NON_BLANK_INPUT_PRODUCES_OUTPUT",
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("\n   \nhello\n/quit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-quit",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == "ONLY_NON_BLANK_INPUT_PRODUCES_OUTPUT\n"
    assert [(request.provider_turn_index, request.provider_turn_label, request.user_prompt) for request in captured_requests] == [
        (0, "initial", "hello")
    ]
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["exit_reason"] == "explicit_exit"
    assert completed_payload["turn_count"] == 1
    assert completed_payload["read_command_used"] is False
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_help_prints_static_usage_without_provider_or_tools(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("help command should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/help\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-help",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == ""
    assert "pipy native REPL commands:" in captured.err
    assert "  /help" in captured.err
    assert "  /clear" in captured.err
    assert "  /status" in captured.err
    assert "  /login [openai-codex]" in captured.err
    assert "  /logout [openai-codex]" in captured.err
    assert "  /model [<provider>/<model>|<model>]" in captured.err
    assert "  /read <workspace-relative-path>" in captured.err
    assert "  /ask-file <workspace-relative-path> -- <question>" in captured.err
    assert "  /propose-file <workspace-relative-path> -- <change-request>" in captured.err
    assert "  /apply-proposal <workspace-relative-path>" in captured.err
    assert "  /verify just-check" in captured.err
    assert "  /exit" in captured.err
    assert "  /quit" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert "native.provider.started" not in event_types
    assert not [event_type for event_type in event_types if event_type.startswith("native.tool.")]
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["turn_count"] == 0
    assert completed_payload["read_command_used"] is False
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "/help" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_status_prints_safe_state_without_provider_tool_or_archive_text(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("status command should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/status\n/status extra\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-status",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == ""
    assert "pipy native REPL status:" in captured.err
    assert "  provider: fake" in captured.err
    assert "  model: fake-native-bootstrap" in captured.err
    assert "  provider_turns: 0/8" in captured.err
    assert "  no_tool_history: retained=false exchanges=0/8 bytes=0/4096" in captured.err
    assert "  read_budget: can_attempt=true successful_used=false" in captured.err
    assert "  pending_proposal_available: false" in captured.err
    assert "  verification_available: false" in captured.err
    assert "malformed /status command. Supported command usage:" in captured.err
    assert "  /status" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert "native.provider.started" not in event_types
    assert not [event_type for event_type in event_types if event_type.startswith("native.tool.")]
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["turn_count"] == 0
    assert completed_payload["read_command_used"] is False
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "/status" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_model_status_prints_to_stderr_without_provider_or_read_limit(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("model status should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/model\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-model-status",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == ""
    assert "pipy: current model: fake/fake-native-bootstrap" in captured.err
    assert "fake/fake-native-bootstrap" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert "native.provider.started" not in event_types
    assert not [event_type for event_type in event_types if event_type.startswith("native.tool.")]
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["read_command_used"] is False
    assert completed_payload["turn_count"] == 0
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "/model" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_model_selection_late_binds_subsequent_provider_turn_and_persists_default(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    defaults_path = tmp_path / "native-defaults.json"
    captured_requests: list[ProviderRequest] = []

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text=f"MODEL={self.model_id}",
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/model fake/fake-after-switch\nhello\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-model-switch",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == "MODEL=fake-after-switch\n"
    assert "selected model fake/fake-after-switch" in captured.err
    assert [(request.provider_name, request.model_id, request.user_prompt) for request in captured_requests] == [
        ("fake", "fake-after-switch", "hello")
    ]
    defaults = json.loads(defaults_path.read_text(encoding="utf-8"))
    assert defaults["provider"] == "fake"
    assert defaults["model_id"] == "fake-after-switch"
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    provider_started = [event["payload"] for event in events if event["type"] == "native.provider.started"]
    assert provider_started[0]["provider"] == "fake"
    assert provider_started[0]["model_id"] == "fake-after-switch"
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "/model fake/fake-after-switch" not in combined
    assert "hello" not in combined
    assert "MODEL=fake-after-switch" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_clear_clears_context_without_resetting_model_or_turn_index(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    captured_requests: list[ProviderRequest] = []

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text=f"TURN={request.provider_turn_index};MODEL={self.model_id}",
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/model fake/after-clear-model\nfirst prompt\n/clear\nsecond prompt\n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-clear-context",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == "TURN=0;MODEL=after-clear-model\nTURN=1;MODEL=after-clear-model\n"
    assert "local conversation context cleared" in captured.err
    assert [
        (request.provider_turn_index, request.provider_turn_label, request.model_id)
        for request in captured_requests
    ] == [
        (0, "initial", "after-clear-model"),
        (1, "no_tool_repl", "after-clear-model"),
    ]
    assert captured_requests[0].no_tool_repl_context is not None
    assert captured_requests[0].no_tool_repl_context.exchanges == ()
    assert captured_requests[1].no_tool_repl_context is not None
    assert captured_requests[1].no_tool_repl_context.exchanges == ()
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert not [event_type for event_type in event_types if event_type.startswith("native.tool.")]
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["turn_count"] == 2
    assert completed_payload["read_command_used"] is False
    assert completed_payload["no_tool_context_retained_at_end"] is True
    assert completed_payload["no_tool_context_retained_exchange_count"] == 1
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "/clear" not in combined
    assert "first prompt" not in combined
    assert "second prompt" not in combined
    assert "TURN=0" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_login_invokes_openai_codex_auth_manager_without_provider_turn(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    captured_call: dict[str, object] = {}
    provider_calls = 0

    class CliFakeAuthManager:
        def login_interactive(self, *, input_stream, output_stream, open_browser: bool):
            captured_call["input_stream"] = input_stream
            captured_call["output_stream"] = output_stream
            captured_call["open_browser"] = open_browser
            return object()

        def logout(self) -> bool:
            raise AssertionError("logout should not be called")

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("login command should not call provider")

    monkeypatch.setattr("pipy_harness.cli.OpenAICodexAuthManager", CliFakeAuthManager)
    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/login openai-codex\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-login",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert provider_calls == 0
    assert captured_call["open_browser"] is True
    assert "openai-codex OAuth login stored" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert "native.provider.started" not in event_types
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "/login" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_login_logout_reject_unsupported_provider_without_side_effects(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    provider_calls = 0

    class CliFakeAuthManager:
        def login_interactive(self, *, input_stream, output_stream, open_browser: bool):
            raise AssertionError("unsupported login provider should not start OAuth")

        def logout(self) -> bool:
            raise AssertionError("unsupported logout provider should not touch credentials")

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("unsupported auth commands should not call provider")

    monkeypatch.setattr("pipy_harness.cli.OpenAICodexAuthManager", CliFakeAuthManager)
    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/login openai\n/logout openai\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-unsupported-auth",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert provider_calls == 0
    assert "unsupported login provider" in captured.err
    assert "unsupported logout provider" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert "native.provider.started" not in event_types
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "/login openai" not in combined
    assert "/logout openai" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_model_resolution_rejects_unavailable_ambiguous_and_unknown_models(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    (auth_dir / "openai-codex.json").write_text("{}", encoding="utf-8")
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("model diagnostics should not call provider")

    monkeypatch.setenv("PIPY_AUTH_DIR", str(auth_dir))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/model gpt-5.4\n/model missing-model\n/model openrouter/example\n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-model-rejections",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert provider_calls == 0
    assert "ambiguous model reference" in captured.err
    assert "unsupported or unavailable model reference" in captured.err
    assert "openrouter is unavailable because OPENROUTER_API_KEY is not set" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert "native.provider.started" not in event_types
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "missing-model" not in combined
    assert "openrouter/example" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_model_bare_single_match_and_unavailable_provider_gate(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    captured_requests: list[ProviderRequest] = []

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text=f"{request.provider_name}/{request.model_id}",
            )

    monkeypatch.setenv("PIPY_AUTH_DIR", str(tmp_path / "empty-auth"))
    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/model fake-native-bootstrap\n/model openai-codex/gpt-test\nhello\n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-model-gates",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert "selected model fake/fake-native-bootstrap" in captured.err
    assert "openai-codex is not logged in" in captured.err
    assert captured.out == "fake/fake-native-bootstrap\n"
    assert [(request.provider_name, request.model_id, request.user_prompt) for request in captured_requests] == [
        ("fake", "fake-native-bootstrap", "hello")
    ]


def test_cli_native_repl_unavailable_stored_default_falls_back_to_fake(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    defaults_path = tmp_path / "native-defaults.json"
    defaults_path.write_text(
        json.dumps(
            {
                "schema": "pipy.native-defaults",
                "schema_version": 1,
                "provider": "openai-codex",
                "model_id": "gpt-test",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("PIPY_AUTH_DIR", str(tmp_path / "empty-auth"))
    monkeypatch.setattr(sys, "stdin", StringIO("/model\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-stored-default-fallback",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert "pipy: current model: fake/fake-native-bootstrap" in captured.err
    assert "openai-codex/gpt-test" not in captured.err


def test_cli_native_repl_logout_removes_openai_codex_credentials_and_resets_selection(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    defaults_path = tmp_path / "native-defaults.json"
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    (auth_dir / "openai-codex.json").write_text("{}", encoding="utf-8")
    logout_calls = 0

    class CliFakeAuthManager:
        def login_interactive(self, *, input_stream, output_stream, open_browser: bool):
            raise AssertionError("login should not be called")

        def logout(self) -> bool:
            nonlocal logout_calls
            logout_calls += 1
            return True

    class CliFakeReplProvider:
        name = "fake"

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
                final_text=f"{request.provider_name}/{request.model_id}",
            )

    monkeypatch.setenv("PIPY_AUTH_DIR", str(auth_dir))
    monkeypatch.setattr("pipy_harness.cli.OpenAICodexAuthManager", CliFakeAuthManager)
    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/model openai-codex/gpt-test\n/logout openai-codex\nhello\n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-logout",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert logout_calls == 1
    assert "openai-codex OAuth credentials removed" in captured.err
    assert captured.out == "fake/fake-native-bootstrap\n"
    defaults = json.loads(defaults_path.read_text(encoding="utf-8"))
    assert defaults["provider"] == "fake"
    assert defaults["model_id"] == "fake-native-bootstrap"


def test_cli_bare_pipy_starts_native_repl_with_default_slug(tmp_path, capfd, monkeypatch) -> None:
    root = tmp_path / "sessions"
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("help command should not call provider")

    monkeypatch.setenv("PIPY_SESSION_DIR", str(root))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/help\n/exit\n"))

    exit_code = main([])

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == ""
    assert "pipy native REPL commands:" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    assert "native-repl" in finalized[0].name


def test_cli_native_repl_malformed_help_prints_usage_without_provider_or_tools(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("malformed help command should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/help private/noise\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-malformed-help",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == ""
    assert "malformed /help command. Supported command usage:" in captured.err
    assert "  /help" in captured.err
    assert "private/noise" not in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert "native.provider.started" not in event_types
    assert not [event_type for event_type in event_types if event_type.startswith("native.tool.")]
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "/help" not in combined
    assert "private/noise" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_malformed_clear_prints_usage_without_provider_or_tools(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("malformed clear command should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/clear private/noise\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-malformed-clear",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == ""
    assert "malformed /clear command. Supported command usage:" in captured.err
    assert "  /clear" in captured.err
    assert "private/noise" not in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert "native.provider.started" not in event_types
    assert not [event_type for event_type in event_types if event_type.startswith("native.tool.")]
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "/clear" not in combined
    assert "private/noise" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_read_command_prints_excerpt_without_approval_prompt(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    source = tmp_path / "docs" / "visible.txt"
    source.parent.mkdir()
    source.write_text("APPROVED_EXCERPT_TEXT\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("read command should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/read docs/visible.txt\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-read",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == "APPROVED_EXCERPT_TEXT\n"
    assert "pipy approval required" not in captured.err
    assert "Approve? [y/N]:" not in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert "native.provider.started" not in event_types
    assert event_types.count("native.tool.started") == 1
    assert event_types.count("native.tool.completed") == 1
    tool_payload = [event["payload"] for event in events if event["type"] == "native.tool.completed"][0]
    assert tool_payload["status"] == "succeeded"
    assert tool_payload["file_contents_stored"] is False
    assert tool_payload["tool_metadata"]["file_contents_stored"] is False
    assert tool_payload["tool_metadata"]["approval_policy"] == "not-required"
    assert tool_payload["tool_metadata"]["approval_required"] is False
    assert tool_payload["tool_metadata"]["approval_decision"] == "allowed"
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["turn_count"] == 0
    assert completed_payload["read_command_used"] is True
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "APPROVED_EXCERPT_TEXT" not in combined
    assert verify_session_archive(root=root).ok is True
    assert search_finalized_sessions("native.tool.completed", root=root)
    assert not search_finalized_sessions("APPROVED_EXCERPT_TEXT", root=root)


def test_cli_native_repl_malformed_read_prints_usage_without_consuming_read_limit(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    source = tmp_path / "docs" / "after-malformed-read.txt"
    source.parent.mkdir()
    source.write_text("READ_AFTER_MALFORMED_READ\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("malformed read and later read should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/read\n/read docs/after-malformed-read.txt\n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-malformed-read-budget",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == "READ_AFTER_MALFORMED_READ\n"
    assert "malformed /read command. Supported command usage:" in captured.err
    assert "  /read <workspace-relative-path>" in captured.err
    assert "read_command_limit_reached" not in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.tool.completed") == 1
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["read_command_used"] is True
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "READ_AFTER_MALFORMED_READ" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_ask_file_sends_excerpt_to_provider_without_approval_prompt(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    source = tmp_path / "docs" / "context.txt"
    source.parent.mkdir()
    source.write_text("APPROVED_PROVIDER_CONTEXT\n", encoding="utf-8")
    captured_requests: list[ProviderRequest] = []

    class CliFakeAskFileProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="ASK_FILE_PROVIDER_OUTPUT",
                usage={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                metadata={"raw_provider_response": "SHOULD_NOT_PERSIST"},
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeAskFileProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/ask-file docs/context.txt -- What does this say?\n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-ask-file",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == "ASK_FILE_PROVIDER_OUTPUT\n"
    assert "APPROVED_PROVIDER_CONTEXT" not in captured.out
    assert "pipy approval required" not in captured.err
    assert "Approve? [y/N]:" not in captured.err
    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert request.provider_turn_index == 0
    assert request.provider_turn_label == "ask_file_repl"
    assert "What does this say?" in request.user_prompt
    assert "APPROVED_PROVIDER_CONTEXT" in request.user_prompt
    assert "source_label=context.txt" in request.user_prompt
    assert request.tool_observation is not None
    assert request.tool_observation.tool_name == "read_only_repo_inspection"

    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.tool.started") == 1
    assert event_types.count("native.tool.completed") == 1
    assert event_types.count("native.tool.observation.recorded") == 1
    assert event_types.count("native.provider.started") == 1
    assert event_types.count("native.provider.completed") == 1
    provider_payload = [event["payload"] for event in events if event["type"] == "native.provider.completed"][0]
    assert provider_payload["provider_turn_index"] == 0
    assert provider_payload["provider_turn_label"] == "ask_file_repl"
    assert provider_payload["provider_metadata"] == {}
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["turn_count"] == 1
    assert completed_payload["read_command_used"] is True
    assert completed_payload["ask_file_command_used"] is True
    assert completed_payload["provider_visible_context_used"] is True
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "APPROVED_PROVIDER_CONTEXT" not in combined
    assert "What does this say?" not in combined
    assert "ASK_FILE_PROVIDER_OUTPUT" not in combined
    assert "SHOULD_NOT_PERSIST" not in combined
    assert verify_session_archive(root=root).ok is True
    assert search_finalized_sessions("native.tool.observation.recorded", root=root)
    assert not search_finalized_sessions("APPROVED_PROVIDER_CONTEXT", root=root)


def test_cli_native_repl_ask_file_accepts_whitespace_delimited_separator(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    source = tmp_path / "docs" / "context.txt"
    source.parent.mkdir()
    source.write_text("TAB_DELIMITED_PROVIDER_CONTEXT\n", encoding="utf-8")
    captured_requests: list[ProviderRequest] = []

    class CliFakeAskFileProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="ASK_FILE_PROVIDER_OUTPUT",
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeAskFileProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/ask-file\tdocs/context.txt\t--\tWhat does this say?\n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-ask-file-tab-separator",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == "ASK_FILE_PROVIDER_OUTPUT\n"
    assert "TAB_DELIMITED_PROVIDER_CONTEXT" not in captured.out
    assert "pipy approval required" not in captured.err
    assert "Approve? [y/N]:" not in captured.err
    assert len(captured_requests) == 1
    assert "What does this say?" in captured_requests[0].user_prompt
    assert "TAB_DELIMITED_PROVIDER_CONTEXT" in captured_requests[0].user_prompt
    assert verify_session_archive(root=root).ok is True
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "TAB_DELIMITED_PROVIDER_CONTEXT" not in combined
    assert "What does this say?" not in combined
    assert "ASK_FILE_PROVIDER_OUTPUT" not in combined


def test_cli_native_repl_propose_file_records_metadata_only_proposal(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    source = tmp_path / "docs" / "proposal-context.txt"
    source.parent.mkdir()
    source.write_text("APPROVED_PROPOSAL_CONTEXT\n", encoding="utf-8")
    captured_requests: list[ProviderRequest] = []

    class CliFakeProposeFileProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="PROPOSE_FILE_PROVIDER_OUTPUT",
                usage={"input_tokens": 5, "output_tokens": 7, "total_tokens": 12},
                metadata={
                    PROVIDER_PATCH_PROPOSAL_METADATA_KEY: safe_repl_patch_proposal(),
                    "raw_provider_response": "SHOULD_NOT_PERSIST",
                },
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeProposeFileProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/propose-file docs/proposal-context.txt -- Rename the helper\n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-propose-file",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == "PROPOSE_FILE_PROVIDER_OUTPUT\n"
    assert "APPROVED_PROPOSAL_CONTEXT" not in captured.out
    assert "pipy approval required" not in captured.err
    assert "Approve? [y/N]:" not in captured.err
    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert request.provider_turn_index == 0
    assert request.provider_turn_label == "propose_file_repl"
    assert "Rename the helper" in request.user_prompt
    assert "APPROVED_PROPOSAL_CONTEXT" in request.user_prompt
    assert "source_label=proposal-context.txt" in request.user_prompt
    assert "pipy_native_patch_proposal" in request.user_prompt
    assert request.tool_observation is not None
    assert request.tool_observation.tool_name == "read_only_repo_inspection"

    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.tool.started") == 1
    assert event_types.count("native.tool.completed") == 1
    assert event_types.count("native.tool.observation.recorded") == 1
    assert event_types.count("native.provider.started") == 1
    assert event_types.count("native.provider.completed") == 1
    assert event_types.count("native.patch.proposal.recorded") == 1
    provider_payload = [event["payload"] for event in events if event["type"] == "native.provider.completed"][0]
    assert provider_payload["provider_turn_label"] == "propose_file_repl"
    assert provider_payload["provider_metadata"] == {}
    proposal_payload = [
        event["payload"] for event in events if event["type"] == "native.patch.proposal.recorded"
    ][0]
    assert proposal_payload["status"] == "proposed"
    assert proposal_payload["reason_label"] == "structured_proposal_accepted"
    assert proposal_payload["file_count"] == 1
    assert proposal_payload["operation_count"] == 1
    assert proposal_payload["operation_labels"] == ["modify"]
    assert proposal_payload["patch_text_stored"] is False
    assert proposal_payload["diffs_stored"] is False
    assert proposal_payload["file_contents_stored"] is False
    assert proposal_payload["workspace_mutated"] is False
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["turn_count"] == 1
    assert completed_payload["read_command_used"] is True
    assert completed_payload["ask_file_command_used"] is False
    assert completed_payload["propose_file_command_used"] is True
    assert completed_payload["provider_visible_context_used"] is True
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "APPROVED_PROPOSAL_CONTEXT" not in combined
    assert "Rename the helper" not in combined
    assert "PROPOSE_FILE_PROVIDER_OUTPUT" not in combined
    assert "SHOULD_NOT_PERSIST" not in combined
    assert "pipy_native_patch_proposal" not in combined
    assert verify_session_archive(root=root).ok is True
    assert search_finalized_sessions("native.patch.proposal.recorded", root=root)
    assert not search_finalized_sessions("APPROVED_PROPOSAL_CONTEXT", root=root)


def test_cli_native_repl_propose_file_accepts_whitespace_delimited_separator(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    source = tmp_path / "docs" / "proposal-context.txt"
    source.parent.mkdir()
    source.write_text("TAB_DELIMITED_PROPOSAL_CONTEXT\n", encoding="utf-8")
    captured_requests: list[ProviderRequest] = []

    class CliFakeProposeFileProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="PROPOSE_FILE_PROVIDER_OUTPUT",
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeProposeFileProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/propose-file\tdocs/proposal-context.txt\t--\tAdd a guard\n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-propose-file-tab-separator",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == "PROPOSE_FILE_PROVIDER_OUTPUT\n"
    assert len(captured_requests) == 1
    assert captured_requests[0].provider_turn_label == "propose_file_repl"
    assert "Add a guard" in captured_requests[0].user_prompt
    assert "TAB_DELIMITED_PROPOSAL_CONTEXT" in captured_requests[0].user_prompt
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "TAB_DELIMITED_PROPOSAL_CONTEXT" not in combined
    assert "Add a guard" not in combined
    assert "PROPOSE_FILE_PROVIDER_OUTPUT" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_malformed_propose_file_does_not_consume_read_limit(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    source = tmp_path / "docs" / "after-malformed-propose.txt"
    source.parent.mkdir()
    source.write_text("READ_AFTER_MALFORMED_PROPOSE_FILE\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("malformed propose-file and later read should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "/propose-file docs/after-malformed-propose.txt -- \n"
            "/read docs/after-malformed-propose.txt\n/exit\n"
        ),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-malformed-propose-file-budget",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == "READ_AFTER_MALFORMED_PROPOSE_FILE\n"
    assert "malformed /propose-file command. Supported command usage:" in captured.err
    assert "  /propose-file <workspace-relative-path> -- <change-request>" in captured.err
    assert "read_command_limit_reached" not in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.tool.completed") == 1
    assert "native.tool.observation.recorded" not in event_types
    assert "native.patch.proposal.recorded" not in event_types
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["read_command_used"] is True
    assert completed_payload["propose_file_command_used"] is False
    assert completed_payload["provider_visible_context_used"] is False
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "READ_AFTER_MALFORMED_PROPOSE_FILE" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_propose_file_rejects_unsafe_target_before_provider(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    provider_calls = 0

    class CliFakeProposeFileProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("unsafe propose-file target should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeProposeFileProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/propose-file ../outside.txt -- Change it\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-propose-file-unsafe-target",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == ""
    assert "pipy approval required" not in captured.err
    assert "unsafe_repl_read_target" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert "native.provider.started" not in event_types
    assert "native.tool.started" not in event_types
    assert "native.patch.proposal.recorded" not in event_types
    assert event_types.count("native.tool.skipped") == 1
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["read_command_used"] is True
    assert completed_payload["propose_file_command_used"] is True
    assert "../outside.txt" not in finalized[0].read_text(encoding="utf-8")
    assert "Change it" not in finalized[0].read_text(encoding="utf-8")
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_read_command_rejects_unsafe_target_before_read(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("unsafe read target should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/read ../outside.txt\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-read-unsafe-target",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == ""
    assert "pipy approval required" not in captured.err
    assert "unsafe_repl_read_target" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert "native.provider.started" not in event_types
    assert "native.tool.started" not in event_types
    assert event_types.count("native.tool.skipped") == 1
    tool_payload = [event["payload"] for event in events if event["type"] == "native.tool.skipped"][0]
    assert tool_payload["reason"] == "unsafe_repl_read_target"
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["read_command_used"] is True
    assert "../outside.txt" not in finalized[0].read_text(encoding="utf-8")
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_unsafe_read_target_preserves_successful_read_budget(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    valid = tmp_path / "docs" / "valid.txt"
    valid.parent.mkdir()
    valid.write_text("VALID_EXCERPT_AFTER_UNSAFE_READ\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("read commands should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/read ../outside.txt\n/read docs/valid.txt\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-read-unsafe-target-limit",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == "VALID_EXCERPT_AFTER_UNSAFE_READ\n"
    assert "unsafe_repl_read_target" in captured.err
    assert "read_command_limit_reached" not in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.tool.skipped") == 1
    assert event_types.count("native.tool.completed") == 1
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["read_command_used"] is True
    assert completed_payload["successful_read_budget_used"] is True
    assert completed_payload["failed_read_attempt_budget_used"] is True
    assert completed_payload["read_recovery_attempt_after_failure_used"] is False
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "VALID_EXCERPT_AFTER_UNSAFE_READ" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_ask_file_failed_read_preserves_later_read_budget(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    valid = tmp_path / "docs" / "valid.txt"
    valid.parent.mkdir()
    valid.write_text("READ_AFTER_MISSING_ASK_FILE\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("failed ask-file and later read should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/ask-file docs/missing.txt -- Use it\n/read docs/valid.txt\n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-ask-file-failure-then-read",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == "READ_AFTER_MISSING_ASK_FILE\n"
    assert "ask-file command skipped: missing_file" in captured.err
    assert "read_command_limit_reached" not in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.tool.skipped") == 1
    assert event_types.count("native.tool.completed") == 1
    assert "native.provider.started" not in event_types
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["successful_read_budget_used"] is True
    assert completed_payload["failed_read_attempt_budget_used"] is True
    assert completed_payload["ask_file_command_used"] is True
    assert completed_payload["provider_visible_context_used"] is False
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "READ_AFTER_MISSING_ASK_FILE" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_propose_file_skipped_read_preserves_later_success_budget(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    secret = tmp_path / "docs" / "config.txt"
    valid = tmp_path / "docs" / "valid.txt"
    secret.parent.mkdir()
    secret.write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")
    valid.write_text("PROPOSE_AFTER_FILTERED_READ_SKIP\n", encoding="utf-8")
    captured_requests: list[ProviderRequest] = []

    class CliFakeProposeFileProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="PROPOSAL_AFTER_SKIPPED_READ",
                metadata={PROVIDER_PATCH_PROPOSAL_METADATA_KEY: safe_repl_patch_proposal()},
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeProposeFileProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "/propose-file docs/config.txt -- Change it\n"
            "/propose-file docs/valid.txt -- Change it\n/exit\n"
        ),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-propose-file-skipped-then-success",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == "PROPOSAL_AFTER_SKIPPED_READ\n"
    assert "propose-file command skipped: secret_looking_content" in captured.err
    assert "read_command_limit_reached" not in captured.err
    assert len(captured_requests) == 1
    assert captured_requests[0].provider_turn_label == "propose_file_repl"
    assert "PROPOSE_AFTER_FILTERED_READ_SKIP" in captured_requests[0].user_prompt
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.patch.proposal.recorded") == 1
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["successful_read_budget_used"] is True
    assert completed_payload["failed_read_attempt_budget_used"] is True
    assert completed_payload["propose_file_command_used"] is True
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "OPENAI_API_KEY" not in combined
    assert "PROPOSE_AFTER_FILTERED_READ_SKIP" not in combined
    assert "PROPOSAL_AFTER_SKIPPED_READ" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_two_failed_read_attempts_exhaust_recovery_before_later_read(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    valid = tmp_path / "docs" / "valid.txt"
    valid.parent.mkdir()
    valid.write_text("VALID_EXCERPT_AFTER_TWO_FAILURES_SHOULD_NOT_PRINT\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("failed reads should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/read ../outside.txt\n/read ../outside-again.txt\n/read docs/valid.txt\n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-two-failed-read-attempts",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == ""
    assert captured.err.count("unsafe_repl_read_target") == 2
    assert "read command skipped: read_command_limit_reached" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.tool.skipped") == 2
    assert "native.tool.completed" not in event_types
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["successful_read_budget_used"] is False
    assert completed_payload["failed_read_attempt_budget_used"] is True
    assert completed_payload["read_recovery_attempt_after_failure_used"] is True
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "VALID_EXCERPT_AFTER_TWO_FAILURES_SHOULD_NOT_PRINT" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_local_commands_do_not_consume_failed_read_recovery_budget(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    valid = tmp_path / "docs" / "valid.txt"
    valid.parent.mkdir()
    valid.write_text("VALID_EXCERPT_AFTER_LOCAL_COMMANDS\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeAuthManager:
        def login_interactive(self, *, input_stream, output_stream, open_browser: bool):
            raise AssertionError("unsupported login should not start OAuth")

        def logout(self) -> bool:
            raise AssertionError("unsupported logout should not touch credentials")

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("local commands and display reads should not call provider")

    monkeypatch.setattr("pipy_harness.cli.OpenAICodexAuthManager", CliFakeAuthManager)
    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "/read ../outside.txt\n"
            "/help\n"
            "/clear\n"
            "/login openai\n"
            "/logout openai\n"
            "/model\n"
            "/apply-proposal docs/valid.txt\n"
            "/verify just-check\n"
            "/unknown private/raw/path\n"
            "/read docs/valid.txt\n/exit\n"
        ),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-local-commands-outside-read-budgets",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == "VALID_EXCERPT_AFTER_LOCAL_COMMANDS\n"
    assert "unsafe_repl_read_target" in captured.err
    assert "unsupported login provider" in captured.err
    assert "unsupported logout provider" in captured.err
    assert "local conversation context cleared" in captured.err
    assert "pipy: current model: fake/fake-native-bootstrap" in captured.err
    assert "apply-proposal command skipped: no_pending_proposal" in captured.err
    assert "verify command skipped: no_successful_apply_proposal" in captured.err
    assert "unsupported REPL slash command. Supported command usage:" in captured.err
    assert "read_command_limit_reached" not in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.tool.skipped") == 1
    assert event_types.count("native.tool.completed") == 1
    assert "native.provider.started" not in event_types
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["read_command_used"] is True
    assert completed_payload["successful_read_budget_used"] is True
    assert completed_payload["failed_read_attempt_budget_used"] is True
    assert completed_payload["read_recovery_attempt_after_failure_used"] is False
    assert completed_payload["ask_file_command_used"] is False
    assert completed_payload["propose_file_command_used"] is False
    assert completed_payload["apply_proposal_command_used"] is False
    assert completed_payload["verification_command_used"] is False
    assert completed_payload["provider_visible_context_used"] is False
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "VALID_EXCERPT_AFTER_LOCAL_COMMANDS" not in combined
    assert "/login openai" not in combined
    assert "/logout openai" not in combined
    assert "/clear" not in combined
    assert "private/raw/path" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_read_command_is_limited_to_one_request(tmp_path, capfd, monkeypatch):
    root = tmp_path / "sessions"
    first = tmp_path / "docs" / "first.txt"
    second = tmp_path / "docs" / "second.txt"
    first.parent.mkdir()
    first.write_text("FIRST_EXCERPT_TEXT\n", encoding="utf-8")
    second.write_text("SECOND_EXCERPT_TEXT\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("read command limit should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/read docs/first.txt\n/clear\n/read docs/second.txt\n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-read-limit",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == "FIRST_EXCERPT_TEXT\n"
    assert "local conversation context cleared" in captured.err
    assert "read_command_limit_reached" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.tool.completed") == 1
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "/clear" not in combined
    assert "FIRST_EXCERPT_TEXT" not in combined
    assert "SECOND_EXCERPT_TEXT" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_read_command_blocks_later_ask_file(tmp_path, capfd, monkeypatch):
    root = tmp_path / "sessions"
    first = tmp_path / "docs" / "first.txt"
    second = tmp_path / "docs" / "second.txt"
    first.parent.mkdir()
    first.write_text("FIRST_READ_TEXT\n", encoding="utf-8")
    second.write_text("SECOND_CONTEXT_SHOULD_NOT_READ\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("second ask-file command should be blocked before provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/read docs/first.txt\n/ask-file docs/second.txt -- Use this\n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-read-blocks-ask-file",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == "FIRST_READ_TEXT\n"
    assert "ask-file command skipped: read_command_limit_reached" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "SECOND_CONTEXT_SHOULD_NOT_READ" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_ask_file_command_blocks_later_read(tmp_path, capfd, monkeypatch) -> None:
    root = tmp_path / "sessions"
    first = tmp_path / "docs" / "first.txt"
    second = tmp_path / "docs" / "second.txt"
    first.parent.mkdir()
    first.write_text("FIRST_CONTEXT_FOR_PROVIDER\n", encoding="utf-8")
    second.write_text("SECOND_READ_SHOULD_NOT_PRINT\n", encoding="utf-8")
    captured_requests: list[ProviderRequest] = []

    class CliFakeAskFileProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="ASK_FILE_FIRST_OUTPUT",
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeAskFileProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/ask-file docs/first.txt -- Use this\n/read docs/second.txt\n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-ask-file-blocks-read",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == "ASK_FILE_FIRST_OUTPUT\n"
    assert "SECOND_READ_SHOULD_NOT_PRINT" not in captured.out
    assert "read command skipped: read_command_limit_reached" in captured.err
    assert len(captured_requests) == 1
    assert "FIRST_CONTEXT_FOR_PROVIDER" in captured_requests[0].user_prompt
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.tool.completed") == 1
    assert event_types.count("native.provider.completed") == 1
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["read_command_used"] is True
    assert completed_payload["ask_file_command_used"] is True
    assert completed_payload["provider_visible_context_used"] is True
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "FIRST_CONTEXT_FOR_PROVIDER" not in combined
    assert "SECOND_READ_SHOULD_NOT_PRINT" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_malformed_ask_file_after_read_limit_prints_usage(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    first = tmp_path / "docs" / "first.txt"
    first.parent.mkdir()
    first.write_text("FIRST_READ_BEFORE_MALFORMED_ASK\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("malformed ask-file after read should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/read docs/first.txt\n/ask-file docs/first.txt -- \n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-malformed-ask-after-read-limit",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == "FIRST_READ_BEFORE_MALFORMED_ASK\n"
    assert "malformed /ask-file command. Supported command usage:" in captured.err
    assert "ask-file command skipped: read_command_limit_reached" not in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.tool.completed") == 1
    assert event_types.count("native.provider.started") == 0
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["read_command_used"] is True
    assert completed_payload["ask_file_command_used"] is False
    assert completed_payload["provider_visible_context_used"] is False
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "FIRST_READ_BEFORE_MALFORMED_ASK" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_malformed_propose_file_after_read_limit_prints_usage(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    first = tmp_path / "docs" / "first.txt"
    first.parent.mkdir()
    first.write_text("FIRST_READ_BEFORE_MALFORMED_PROPOSE\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("malformed propose-file after read should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/read docs/first.txt\n/propose-file docs/first.txt -- \n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-malformed-propose-after-read-limit",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == "FIRST_READ_BEFORE_MALFORMED_PROPOSE\n"
    assert "malformed /propose-file command. Supported command usage:" in captured.err
    assert "propose-file command skipped: read_command_limit_reached" not in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.tool.completed") == 1
    assert event_types.count("native.provider.started") == 0
    assert "native.patch.proposal.recorded" not in event_types
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["read_command_used"] is True
    assert completed_payload["propose_file_command_used"] is False
    assert completed_payload["provider_visible_context_used"] is False
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "FIRST_READ_BEFORE_MALFORMED_PROPOSE" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_read_command_blocks_later_propose_file(tmp_path, capfd, monkeypatch):
    root = tmp_path / "sessions"
    first = tmp_path / "docs" / "first.txt"
    second = tmp_path / "docs" / "second.txt"
    first.parent.mkdir()
    first.write_text("FIRST_READ_FOR_PROPOSE_BUDGET\n", encoding="utf-8")
    second.write_text("SECOND_PROPOSE_CONTEXT_SHOULD_NOT_READ\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("propose-file command should be blocked before provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/read docs/first.txt\n/propose-file docs/second.txt -- Change it\n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-read-blocks-propose-file",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == "FIRST_READ_FOR_PROPOSE_BUDGET\n"
    assert "propose-file command skipped: read_command_limit_reached" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "SECOND_PROPOSE_CONTEXT_SHOULD_NOT_READ" not in combined
    assert "Change it" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_ask_file_command_blocks_later_propose_file(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    first = tmp_path / "docs" / "first.txt"
    second = tmp_path / "docs" / "second.txt"
    first.parent.mkdir()
    first.write_text("FIRST_ASK_CONTEXT_FOR_BUDGET\n", encoding="utf-8")
    second.write_text("SECOND_PROPOSE_CONTEXT_SHOULD_NOT_READ\n", encoding="utf-8")
    captured_requests: list[ProviderRequest] = []

    class CliFakeAskFileProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="ASK_FILE_FOR_PROPOSE_BUDGET_OUTPUT",
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeAskFileProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "/ask-file docs/first.txt -- Use this\n"
            "/propose-file docs/second.txt -- Change it\n/exit\n"
        ),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-ask-file-blocks-propose-file",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == "ASK_FILE_FOR_PROPOSE_BUDGET_OUTPUT\n"
    assert "propose-file command skipped: read_command_limit_reached" in captured.err
    assert len(captured_requests) == 1
    assert captured_requests[0].provider_turn_label == "ask_file_repl"
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "SECOND_PROPOSE_CONTEXT_SHOULD_NOT_READ" not in combined
    assert "Change it" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_propose_file_command_blocks_later_read_and_ask_file(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    first = tmp_path / "docs" / "first.txt"
    second = tmp_path / "docs" / "second.txt"
    third = tmp_path / "docs" / "third.txt"
    first.parent.mkdir()
    first.write_text("FIRST_PROPOSE_CONTEXT\n", encoding="utf-8")
    second.write_text("SECOND_READ_SHOULD_NOT_PRINT_AFTER_PROPOSE\n", encoding="utf-8")
    third.write_text("THIRD_ASK_SHOULD_NOT_REACH_PROVIDER_AFTER_PROPOSE\n", encoding="utf-8")
    captured_requests: list[ProviderRequest] = []

    class CliFakeProposeFileProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="PROPOSE_FILE_FOR_BUDGET_OUTPUT",
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeProposeFileProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "/propose-file docs/first.txt -- Change it\n"
            "/read docs/second.txt\n"
            "/ask-file docs/third.txt -- Use it\n/exit\n"
        ),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-propose-file-blocks-read-and-ask",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == "PROPOSE_FILE_FOR_BUDGET_OUTPUT\n"
    assert "read command skipped: read_command_limit_reached" in captured.err
    assert "ask-file command skipped: read_command_limit_reached" in captured.err
    assert len(captured_requests) == 1
    assert captured_requests[0].provider_turn_label == "propose_file_repl"
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["read_command_used"] is True
    assert completed_payload["propose_file_command_used"] is True
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "FIRST_PROPOSE_CONTEXT" not in combined
    assert "SECOND_READ_SHOULD_NOT_PRINT_AFTER_PROPOSE" not in combined
    assert "THIRD_ASK_SHOULD_NOT_REACH_PROVIDER_AFTER_PROPOSE" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_repeated_propose_file_is_limited_to_one_request(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    first = tmp_path / "docs" / "first.txt"
    second = tmp_path / "docs" / "second.txt"
    first.parent.mkdir()
    first.write_text("FIRST_PROPOSE_ONCE_CONTEXT\n", encoding="utf-8")
    second.write_text("SECOND_PROPOSE_SHOULD_NOT_READ\n", encoding="utf-8")
    captured_requests: list[ProviderRequest] = []

    class CliFakeProposeFileProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="PROPOSE_FILE_ONCE_OUTPUT",
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeProposeFileProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "/propose-file docs/first.txt -- First change\n"
            "/propose-file docs/second.txt -- Second change\n/exit\n"
        ),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-propose-file-limit",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == "PROPOSE_FILE_ONCE_OUTPUT\n"
    assert "propose-file command skipped: read_command_limit_reached" in captured.err
    assert len(captured_requests) == 1
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.tool.completed") == 1
    assert event_types.count("native.provider.completed") == 1
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "SECOND_PROPOSE_SHOULD_NOT_READ" not in combined
    assert "Second change" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_propose_file_unsafe_proposal_metadata_is_skipped_metadata_only(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    source = tmp_path / "docs" / "unsafe-proposal-context.txt"
    source.parent.mkdir()
    source.write_text("UNSAFE_PROPOSAL_CONTEXT\n", encoding="utf-8")

    class CliFakeProposeFileProvider:
        name = "fake"

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
                final_text="UNSAFE_PROPOSAL_PROVIDER_OUTPUT",
                metadata={
                    PROVIDER_PATCH_PROPOSAL_METADATA_KEY: {
                        **safe_repl_patch_proposal(),
                        "raw_patch_text": "SHOULD_NOT_PERSIST",
                    },
                    "raw_diff": "SHOULD_NOT_PERSIST",
                },
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeProposeFileProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/propose-file docs/unsafe-proposal-context.txt -- Change it\n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-propose-file-unsafe-proposal",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == "UNSAFE_PROPOSAL_PROVIDER_OUTPUT\n"
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    provider_payload = [event["payload"] for event in events if event["type"] == "native.provider.completed"][0]
    assert provider_payload["provider_metadata"] == {}
    proposal_payload = [
        event["payload"] for event in events if event["type"] == "native.patch.proposal.recorded"
    ][0]
    assert proposal_payload["status"] == "skipped"
    assert proposal_payload["reason_label"] == "unsafe_proposal"
    assert proposal_payload["file_count"] == 0
    assert proposal_payload["operation_count"] == 0
    assert proposal_payload["operation_labels"] == []
    assert proposal_payload["patch_text_stored"] is False
    assert proposal_payload["diffs_stored"] is False
    assert proposal_payload["file_contents_stored"] is False
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "UNSAFE_PROPOSAL_CONTEXT" not in combined
    assert "Change it" not in combined
    assert "UNSAFE_PROPOSAL_PROVIDER_OUTPUT" not in combined
    assert "SHOULD_NOT_PERSIST" not in combined
    assert "pipy_native_patch_proposal" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_malformed_apply_proposal_does_not_call_provider_or_mutate(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    target = tmp_path / "docs" / "target.txt"
    target.parent.mkdir()
    target.write_text("unchanged\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("malformed apply-proposal should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/apply-proposal\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-malformed-apply-proposal",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert target.read_text(encoding="utf-8") == "unchanged\n"
    assert "malformed /apply-proposal command. Supported command usage:" in captured.err
    assert "  /apply-proposal <workspace-relative-path>" in captured.err
    events = read_jsonl(next((root / "pipy").glob("*/*/*.jsonl")))
    event_types = [event["type"] for event in events]
    assert "native.provider.started" not in event_types
    assert NATIVE_PATCH_APPLY_RECORDED_EVENT not in event_types
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_apply_proposal_without_pending_fails_closed_without_mutation(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    target = tmp_path / "docs" / "target.txt"
    target.parent.mkdir()
    target.write_text("unchanged\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("apply-proposal without pending proposal should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/apply-proposal docs/target.txt\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-apply-proposal-no-pending",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert target.read_text(encoding="utf-8") == "unchanged\n"
    assert "apply-proposal command skipped: no_pending_proposal" in captured.err
    events = read_jsonl(next((root / "pipy").glob("*/*/*.jsonl")))
    event_types = [event["type"] for event in events]
    assert "native.provider.started" not in event_types
    assert NATIVE_PATCH_APPLY_RECORDED_EVENT not in event_types
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_apply_proposal_mismatched_path_fails_closed_without_mutation(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    old_text = "old value\n"
    new_text = "new value\n"
    proposed = tmp_path / "docs" / "proposed.txt"
    other = tmp_path / "docs" / "other.txt"
    proposed.parent.mkdir()
    proposed.write_text(old_text, encoding="utf-8")
    other.write_text("other value\n", encoding="utf-8")
    captured_requests: list[ProviderRequest] = []

    class CliFakeProposeFileProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text=repl_apply_proposal_text("docs/proposed.txt", old_text, new_text),
                metadata={PROVIDER_PATCH_PROPOSAL_METADATA_KEY: safe_repl_patch_proposal()},
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeProposeFileProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "/propose-file docs/proposed.txt -- Change it\n"
            "/apply-proposal docs/other.txt\n/exit\n"
        ),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-apply-proposal-mismatch",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert len(captured_requests) == 1
    assert proposed.read_text(encoding="utf-8") == old_text
    assert other.read_text(encoding="utf-8") == "other value\n"
    assert "apply-proposal command skipped: proposal_path_mismatch" in captured.err
    events = read_jsonl(next((root / "pipy").glob("*/*/*.jsonl")))
    event_types = [event["type"] for event in events]
    assert event_types.count("native.provider.started") == 1
    assert NATIVE_PATCH_APPLY_RECORDED_EVENT not in event_types
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_apply_proposal_mutates_one_file_and_archives_metadata_only(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    old_text = "old value\n"
    new_text = "new value from reviewed proposal\n"
    target = tmp_path / "docs" / "target.txt"
    target.parent.mkdir()
    target.write_text(old_text, encoding="utf-8")
    captured_requests: list[ProviderRequest] = []

    class CliFakeProposeFileProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text=repl_apply_proposal_text("docs/target.txt", old_text, new_text),
                usage={"input_tokens": 5, "output_tokens": 7, "total_tokens": 12},
                metadata={PROVIDER_PATCH_PROPOSAL_METADATA_KEY: safe_repl_patch_proposal()},
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeProposeFileProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "/propose-file docs/target.txt -- Change it\n"
            "/apply-proposal docs/target.txt\n/exit\n"
        ),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-apply-proposal-success",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert len(captured_requests) == 1
    assert target.read_text(encoding="utf-8") == new_text
    assert "apply-proposal command succeeded: patch_applied" in captured.err

    finalized = next((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized)
    event_types = [event["type"] for event in events]
    assert event_types.count("native.provider.started") == 1
    assert event_types.count(NATIVE_PATCH_APPLY_RECORDED_EVENT) == 1
    assert NATIVE_VERIFICATION_RECORDED_EVENT not in event_types
    apply_payload = [
        event["payload"] for event in events if event["type"] == NATIVE_PATCH_APPLY_RECORDED_EVENT
    ][0]
    assert apply_payload["status"] == "succeeded"
    assert apply_payload["reason_label"] == "patch_applied"
    assert apply_payload["file_count"] == 1
    assert apply_payload["operation_count"] == 1
    assert apply_payload["operation_labels"] == ["modify"]
    assert apply_payload["approval_decision"] == "allowed"
    assert apply_payload["sandbox_policy"] == "mutating-workspace"
    assert apply_payload["workspace_read_allowed"] is True
    assert apply_payload["filesystem_mutation_allowed"] is True
    assert apply_payload["shell_execution_allowed"] is False
    assert apply_payload["network_access_allowed"] is False
    assert apply_payload["workspace_mutated"] is True
    assert apply_payload["patch_text_stored"] is False
    assert apply_payload["diffs_stored"] is False
    assert apply_payload["file_contents_stored"] is False

    combined = finalized.read_text(encoding="utf-8") + finalized.with_suffix(".md").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        old_text.strip(),
        new_text.strip(),
        "Change it",
        "pipy-apply-proposal-v1",
        "replacement_text",
    ):
        assert forbidden not in combined
        assert not search_finalized_sessions(forbidden, root=root)
    assert search_finalized_sessions(NATIVE_PATCH_APPLY_RECORDED_EVENT, root=root)
    inspection = inspect_finalized_session(finalized, root=root)
    assert inspection.event_types[NATIVE_PATCH_APPLY_RECORDED_EVENT] == 1
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_verify_requires_successful_apply_without_provider_or_tool(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    provider_calls = 0
    verification_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("verify without apply should not call provider")

    class CliFakeVerificationTool:
        def __init__(self, workspace: Path) -> None:
            self.workspace = workspace

        def invoke(self, request, gate):
            nonlocal verification_calls
            verification_calls += 1
            raise AssertionError("verify without apply should not invoke verification")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr("pipy_harness.native.session.NativeVerificationTool", CliFakeVerificationTool)
    monkeypatch.setattr(sys, "stdin", StringIO("/verify just-check\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-verify-no-apply",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert verification_calls == 0
    assert "verify command skipped: no_successful_apply_proposal" in captured.err
    events = read_jsonl(next((root / "pipy").glob("*/*/*.jsonl")))
    event_types = [event["type"] for event in events]
    assert NATIVE_VERIFICATION_RECORDED_EVENT not in event_types
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_verify_before_apply_preserves_pending_proposal(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    old_text = "old value\n"
    new_text = "new value after skipped early verification\n"
    target = tmp_path / "docs" / "target.txt"
    target.parent.mkdir()
    target.write_text(old_text, encoding="utf-8")
    verification_calls = 0

    class CliFakeProposeFileProvider:
        name = "fake"

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
                final_text=repl_apply_proposal_text("docs/target.txt", old_text, new_text),
                metadata={PROVIDER_PATCH_PROPOSAL_METADATA_KEY: safe_repl_patch_proposal()},
            )

    class CliFakeVerificationTool:
        def __init__(self, workspace: Path) -> None:
            self.workspace = workspace

        def invoke(self, request, gate):
            nonlocal verification_calls
            verification_calls += 1
            raise AssertionError("verify before apply should not invoke verification")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeProposeFileProvider)
    monkeypatch.setattr("pipy_harness.native.session.NativeVerificationTool", CliFakeVerificationTool)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "/propose-file docs/target.txt -- Change it\n"
            "/status\n"
            "/status malformed\n"
            "/verify just-check\n"
            "/apply-proposal docs/target.txt\n/exit\n"
        ),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-verify-before-apply-preserves-draft",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert verification_calls == 0
    assert target.read_text(encoding="utf-8") == new_text
    assert "verify command skipped: no_successful_apply_proposal" in captured.err
    assert "  pending_proposal_available: true" in captured.err
    assert "  verification_available: false" in captured.err
    assert "malformed /status command. Supported command usage:" in captured.err
    assert "apply-proposal command succeeded: patch_applied" in captured.err
    events = read_jsonl(next((root / "pipy").glob("*/*/*.jsonl")))
    event_types = [event["type"] for event in events]
    assert event_types.count(NATIVE_PATCH_APPLY_RECORDED_EVENT) == 1
    assert NATIVE_VERIFICATION_RECORDED_EVENT not in event_types
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_verify_after_apply_records_metadata_only_without_provider_call(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    old_text = "old value\n"
    new_text = "new value before verification\n"
    target = tmp_path / "docs" / "target.txt"
    target.parent.mkdir()
    target.write_text(old_text, encoding="utf-8")
    captured_requests: list[ProviderRequest] = []
    verification_calls: list[tuple[Path, NativeVerificationRequest, NativeVerificationGateDecision]] = []

    class CliFakeProposeFileProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text=repl_apply_proposal_text("docs/target.txt", old_text, new_text),
                metadata={PROVIDER_PATCH_PROPOSAL_METADATA_KEY: safe_repl_patch_proposal()},
            )

    class CliFakeVerificationTool:
        def __init__(self, workspace: Path) -> None:
            self.workspace = workspace

        def invoke(self, request, gate):
            verification_calls.append((self.workspace, request, gate))
            return repl_verification_result(
                request,
                gate,
                status=NativeToolStatus.SUCCEEDED,
                reason=NativeVerificationReason.VERIFICATION_SUCCEEDED,
                exit_code=0,
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeProposeFileProvider)
    monkeypatch.setattr("pipy_harness.native.session.NativeVerificationTool", CliFakeVerificationTool)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "/propose-file docs/target.txt -- Change it\n"
            "/apply-proposal docs/target.txt\n"
            "/status\n"
            "/verify just-check\n/exit\n"
        ),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-verify-success",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert len(captured_requests) == 1
    assert len(verification_calls) == 1
    workspace, request, gate = verification_calls[0]
    assert workspace == tmp_path
    assert request.command_label == "just-check"
    assert request.scope_label == "interactive_verify"
    assert request.sandbox_policy.mode.value == "read-only-workspace"
    assert request.sandbox_policy.workspace_read_allowed is True
    assert request.sandbox_policy.filesystem_mutation_allowed is False
    assert request.sandbox_policy.shell_execution_allowed is True
    assert request.sandbox_policy.network_access_allowed is False
    assert gate.approval_decision == NativeVerificationApprovalDecision.ALLOWED
    assert "verify command succeeded: verification_succeeded" in captured.err
    assert "  pending_proposal_available: false" in captured.err
    assert "  verification_available: true" in captured.err
    assert target.read_text(encoding="utf-8") == new_text

    finalized = next((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized)
    event_types = [event["type"] for event in events]
    assert event_types.count("native.provider.started") == 1
    assert event_types.count(NATIVE_PATCH_APPLY_RECORDED_EVENT) == 1
    assert event_types.count(NATIVE_VERIFICATION_RECORDED_EVENT) == 1
    verification_payload = [
        event["payload"] for event in events if event["type"] == NATIVE_VERIFICATION_RECORDED_EVENT
    ][0]
    assert verification_payload["command_label"] == "just-check"
    assert verification_payload["status"] == "succeeded"
    assert verification_payload["reason_label"] == "verification_succeeded"
    assert verification_payload["exit_code"] == 0
    assert verification_payload["approval_decision"] == "allowed"
    assert verification_payload["sandbox_policy"] == "read-only-workspace"
    assert verification_payload["workspace_read_allowed"] is True
    assert verification_payload["filesystem_mutation_allowed"] is False
    assert verification_payload["shell_execution_allowed"] is True
    assert verification_payload["network_access_allowed"] is False
    assert verification_payload["stdout_stored"] is False
    assert verification_payload["stderr_stored"] is False
    assert verification_payload["command_output_stored"] is False
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["apply_proposal_command_used"] is True
    assert completed_payload["verification_command_used"] is True
    combined = finalized.read_text(encoding="utf-8") + finalized.with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "just check" not in combined
    assert old_text.strip() not in combined
    assert new_text.strip() not in combined
    assert search_finalized_sessions(NATIVE_VERIFICATION_RECORDED_EVENT, root=root)
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_clear_preserves_verify_after_apply_availability(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    old_text = "old value\n"
    new_text = "new value before post-clear verification\n"
    target = tmp_path / "docs" / "target.txt"
    target.parent.mkdir()
    target.write_text(old_text, encoding="utf-8")
    captured_requests: list[ProviderRequest] = []
    verification_calls: list[tuple[Path, NativeVerificationRequest, NativeVerificationGateDecision]] = []

    class CliFakeProposeFileProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text=repl_apply_proposal_text("docs/target.txt", old_text, new_text),
                metadata={PROVIDER_PATCH_PROPOSAL_METADATA_KEY: safe_repl_patch_proposal()},
            )

    class CliFakeVerificationTool:
        def __init__(self, workspace: Path) -> None:
            self.workspace = workspace

        def invoke(self, request, gate):
            verification_calls.append((self.workspace, request, gate))
            return repl_verification_result(
                request,
                gate,
                status=NativeToolStatus.SUCCEEDED,
                reason=NativeVerificationReason.VERIFICATION_SUCCEEDED,
                exit_code=0,
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeProposeFileProvider)
    monkeypatch.setattr("pipy_harness.native.session.NativeVerificationTool", CliFakeVerificationTool)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "/propose-file docs/target.txt -- Change it\n"
            "/apply-proposal docs/target.txt\n"
            "/clear\n"
            "/verify just-check\n/exit\n"
        ),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-clear-preserves-verify",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert len(captured_requests) == 1
    assert len(verification_calls) == 1
    workspace, request, gate = verification_calls[0]
    assert workspace == tmp_path
    assert request.command_label == "just-check"
    assert gate.approval_decision == NativeVerificationApprovalDecision.ALLOWED
    assert target.read_text(encoding="utf-8") == new_text
    assert "local conversation context cleared" in captured.err
    assert "verify command succeeded: verification_succeeded" in captured.err

    finalized = next((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized)
    event_types = [event["type"] for event in events]
    assert event_types.count("native.provider.started") == 1
    assert event_types.count(NATIVE_PATCH_APPLY_RECORDED_EVENT) == 1
    assert event_types.count(NATIVE_VERIFICATION_RECORDED_EVENT) == 1
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["apply_proposal_command_used"] is True
    assert completed_payload["verification_command_used"] is True
    combined = finalized.read_text(encoding="utf-8") + finalized.with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "/clear" not in combined
    assert old_text.strip() not in combined
    assert new_text.strip() not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_failed_second_apply_does_not_relock_verification(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    old_text = "old value\n"
    new_text = "new value before verification\n"
    target = tmp_path / "docs" / "target.txt"
    target.parent.mkdir()
    target.write_text(old_text, encoding="utf-8")
    verification_calls: list[tuple[Path, NativeVerificationRequest, NativeVerificationGateDecision]] = []

    class CliFakeProposeFileProvider:
        name = "fake"

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
                final_text=repl_apply_proposal_text("docs/target.txt", old_text, new_text),
                metadata={PROVIDER_PATCH_PROPOSAL_METADATA_KEY: safe_repl_patch_proposal()},
            )

    class CliFakeVerificationTool:
        def __init__(self, workspace: Path) -> None:
            self.workspace = workspace

        def invoke(self, request, gate):
            verification_calls.append((self.workspace, request, gate))
            return repl_verification_result(
                request,
                gate,
                status=NativeToolStatus.SUCCEEDED,
                reason=NativeVerificationReason.VERIFICATION_SUCCEEDED,
                exit_code=0,
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeProposeFileProvider)
    monkeypatch.setattr("pipy_harness.native.session.NativeVerificationTool", CliFakeVerificationTool)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "/propose-file docs/target.txt -- Change it\n"
            "/apply-proposal docs/target.txt\n"
            "/apply-proposal docs/target.txt\n"
            "/verify just-check\n/exit\n"
        ),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-second-apply-does-not-relock-verify",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert len(verification_calls) == 1
    assert target.read_text(encoding="utf-8") == new_text
    assert "apply-proposal command skipped: no_pending_proposal" in captured.err
    assert "verify command succeeded: verification_succeeded" in captured.err
    events = read_jsonl(next((root / "pipy").glob("*/*/*.jsonl")))
    event_types = [event["type"] for event in events]
    assert event_types.count(NATIVE_PATCH_APPLY_RECORDED_EVENT) == 1
    assert event_types.count(NATIVE_VERIFICATION_RECORDED_EVENT) == 1
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_failed_verify_after_apply_fails_run_with_metadata_only_event(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    old_text = "old value\n"
    new_text = "new value before failed verification\n"
    target = tmp_path / "docs" / "target.txt"
    target.parent.mkdir()
    target.write_text(old_text, encoding="utf-8")

    class CliFakeProposeFileProvider:
        name = "fake"

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
                final_text=repl_apply_proposal_text("docs/target.txt", old_text, new_text),
                metadata={PROVIDER_PATCH_PROPOSAL_METADATA_KEY: safe_repl_patch_proposal()},
            )

    class CliFakeVerificationTool:
        def __init__(self, workspace: Path) -> None:
            self.workspace = workspace

        def invoke(self, request, gate):
            return repl_verification_result(
                request,
                gate,
                status=NativeToolStatus.FAILED,
                reason=NativeVerificationReason.COMMAND_FAILED,
                exit_code=7,
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeProposeFileProvider)
    monkeypatch.setattr("pipy_harness.native.session.NativeVerificationTool", CliFakeVerificationTool)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "/propose-file docs/target.txt -- Change it\n"
            "/apply-proposal docs/target.txt\n"
            "/verify just-check\n/exit\n"
        ),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-verify-failed",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 1
    assert target.read_text(encoding="utf-8") == new_text
    assert "verify command failed: command_failed" in captured.err
    finalized = next((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized)
    verification_payload = [
        event["payload"] for event in events if event["type"] == NATIVE_VERIFICATION_RECORDED_EVENT
    ][0]
    assert verification_payload["status"] == "failed"
    assert verification_payload["reason_label"] == "command_failed"
    assert verification_payload["exit_code"] == 7
    assert verification_payload["command_output_stored"] is False
    combined = finalized.read_text(encoding="utf-8") + finalized.with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "just check" not in combined
    assert old_text.strip() not in combined
    assert new_text.strip() not in combined
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["status"] == "failed"
    assert completed_payload["exit_code"] == 1
    assert completed_payload["exit_reason"] == "verification_failed"
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_visible_apply_draft_without_metadata_does_not_synthesize_proposal_event(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    old_text = "old value\n"
    new_text = "new value from visible draft\n"
    target = tmp_path / "docs" / "target.txt"
    target.parent.mkdir()
    target.write_text(old_text, encoding="utf-8")
    captured_requests: list[ProviderRequest] = []

    class CliFakeProposeFileProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text=repl_apply_proposal_text("docs/target.txt", old_text, new_text),
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeProposeFileProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "/propose-file docs/target.txt -- Change it\n"
            "/apply-proposal docs/target.txt\n/exit\n"
        ),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-visible-apply-no-proposal-event",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert len(captured_requests) == 1
    assert target.read_text(encoding="utf-8") == new_text
    assert "apply-proposal command succeeded: patch_applied" in captured.err

    events = read_jsonl(next((root / "pipy").glob("*/*/*.jsonl")))
    event_types = [event["type"] for event in events]
    assert "native.patch.proposal.recorded" not in event_types
    assert event_types.count(NATIVE_PATCH_APPLY_RECORDED_EVENT) == 1
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_local_command_clears_pending_apply_proposal(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    old_text = "old value\n"
    new_text = "new value from reviewed proposal\n"
    target = tmp_path / "docs" / "target.txt"
    target.parent.mkdir()
    target.write_text(old_text, encoding="utf-8")
    captured_requests: list[ProviderRequest] = []

    class CliFakeProposeFileProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text=repl_apply_proposal_text("docs/target.txt", old_text, new_text),
                metadata={PROVIDER_PATCH_PROPOSAL_METADATA_KEY: safe_repl_patch_proposal()},
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeProposeFileProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "/propose-file docs/target.txt -- Change it\n"
            "/clear\n"
            "/apply-proposal docs/target.txt\n/exit\n"
        ),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-local-command-clears-apply-proposal",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert len(captured_requests) == 1
    assert target.read_text(encoding="utf-8") == old_text
    assert "local conversation context cleared" in captured.err
    assert "apply-proposal command skipped: no_pending_proposal" in captured.err
    events = read_jsonl(next((root / "pipy").glob("*/*/*.jsonl")))
    event_types = [event["type"] for event in events]
    assert event_types.count(NATIVE_PATCH_APPLY_RECORDED_EVENT) == 0
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_apply_proposal_stale_hash_fails_closed_without_mutation(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    old_text = "old value\n"
    intervening_text = "changed before apply\n"
    new_text = "new value from reviewed proposal\n"
    target = tmp_path / "docs" / "target.txt"
    target.parent.mkdir()
    target.write_text(old_text, encoding="utf-8")
    captured_requests: list[ProviderRequest] = []

    class CliFakeProposeFileProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            captured_requests.append(request)
            now = datetime.now(UTC)
            result = ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text=repl_apply_proposal_text("docs/target.txt", old_text, new_text),
                metadata={PROVIDER_PATCH_PROPOSAL_METADATA_KEY: safe_repl_patch_proposal()},
            )
            target.write_text(intervening_text, encoding="utf-8")
            return result

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeProposeFileProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            "/propose-file docs/target.txt -- Change it\n"
            "/apply-proposal docs/target.txt\n/exit\n"
        ),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-apply-proposal-stale-hash",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert len(captured_requests) == 1
    assert target.read_text(encoding="utf-8") == intervening_text
    assert "apply-proposal command skipped: expected_hash_mismatch" in captured.err
    finalized = next((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized)
    event_types = [event["type"] for event in events]
    assert event_types.count("native.provider.started") == 1
    assert event_types.count(NATIVE_PATCH_APPLY_RECORDED_EVENT) == 1
    assert NATIVE_VERIFICATION_RECORDED_EVENT not in event_types
    apply_payload = [
        event["payload"] for event in events if event["type"] == NATIVE_PATCH_APPLY_RECORDED_EVENT
    ][0]
    assert apply_payload["status"] == "skipped"
    assert apply_payload["reason_label"] == "expected_hash_mismatch"
    assert apply_payload["workspace_mutated"] is False
    combined = finalized.read_text(encoding="utf-8") + finalized.with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert new_text.strip() not in combined
    assert intervening_text.strip() not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_malformed_ask_file_does_not_consume_read_limit(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    source = tmp_path / "docs" / "after-malformed.txt"
    source.parent.mkdir()
    source.write_text("READ_AFTER_MALFORMED_ASK_FILE\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("malformed ask-file and later read should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/ask-file docs/after-malformed.txt -- \n/read docs/after-malformed.txt\n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-malformed-ask-file-budget",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == "READ_AFTER_MALFORMED_ASK_FILE\n"
    assert "malformed /ask-file command. Supported command usage:" in captured.err
    assert "  /ask-file <workspace-relative-path> -- <question>" in captured.err
    assert "read_command_limit_reached" not in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.tool.completed") == 1
    assert "native.tool.observation.recorded" not in event_types
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["read_command_used"] is True
    assert completed_payload["ask_file_command_used"] is False
    assert completed_payload["provider_visible_context_used"] is False
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "READ_AFTER_MALFORMED_ASK_FILE" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_unsupported_slash_command_prints_usage_without_provider_or_tools(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("unsupported slash command should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/unknown private/raw/path\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-unsupported-slash",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert provider_calls == 0
    assert captured.out == ""
    assert "unsupported REPL slash command. Supported command usage:" in captured.err
    assert "/unknown" not in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert "native.provider.started" not in event_types
    assert not [event_type for event_type in event_types if event_type.startswith("native.tool.")]
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "/unknown" not in combined
    assert "private/raw/path" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_provider_failure_stops_without_printing_final_text(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"

    class CliFailingReplProvider:
        name = "fake"

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
                final_text="FAILED_REPL_OUTPUT_SHOULD_NOT_PRINT",
                error_type="ReplProviderFailure",
                error_message="provider failed safely",
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFailingReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("hello\nSECOND_PROMPT_SHOULD_NOT_BE_READ\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-provider-failed",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "ReplProviderFailure" in captured.err
    assert "FAILED_REPL_OUTPUT_SHOULD_NOT_PRINT" not in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.provider.failed") == 1
    assert not [event_type for event_type in event_types if str(event_type).startswith("native.tool.")]
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["status"] == "failed"
    assert completed_payload["exit_reason"] == "provider_failed"
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "FAILED_REPL_OUTPUT_SHOULD_NOT_PRINT" not in combined
    assert "SECOND_PROMPT_SHOULD_NOT_BE_READ" not in combined
    assert verify_session_archive(root=root).ok is True


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
    assert "native.verification.recorded" not in event_types
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


def test_cli_native_json_mode_uses_fake_provider_and_finalizes_record(tmp_path, capfd):
    root = tmp_path / "sessions"

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-output",
            "json",
            "--slug",
            "native-json",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--goal",
            "Native JSON smoke",
        ]
    )

    captured = capfd.readouterr()
    output = parse_single_json_stdout(captured.out)
    assert exit_code == 0
    assert output["schema"] == "pipy.native_output"
    assert output["schema_version"] == 1
    assert output["status"] == "succeeded"
    assert output["exit_code"] == 0
    assert output["agent"] == "pipy-native"
    assert output["adapter"] == "pipy-native"
    assert output["provider"] == "fake"
    assert output["model_id"] == "fake-native-bootstrap"
    assert output["capture"] == {
        "partial": True,
        "stdout_stored": False,
        "stderr_stored": False,
        "prompt_stored": False,
        "model_output_stored": False,
        "tool_payloads_stored": False,
        "raw_transcript_imported": False,
    }
    record = output["record"]
    assert isinstance(record, dict)
    finalized = Path(record["jsonl_path"])
    assert finalized.exists()
    markdown_path = record["markdown_path"]
    assert isinstance(markdown_path, str)
    assert Path(markdown_path).exists()
    assert finalized in list((root / "pipy").glob("*/*/*.jsonl"))
    assert "pipy native fake provider completed." not in captured.out
    assert "Native JSON smoke" not in captured.out
    assert "You are the native pipy runtime bootstrap" not in captured.out
    assert "session finalized" in captured.err
    assert verify_session_archive(root=root).ok is True
    assert list_finalized_sessions(root=root)[0].jsonl_path == finalized
    assert search_finalized_sessions("native.provider.completed", root=root)
    inspection = inspect_finalized_session(finalized, root=root)
    assert inspection.event_types["native.session.completed"] == 1


def test_cli_native_json_mode_omits_patch_proposal_raw_content(tmp_path, capfd, monkeypatch):
    root = tmp_path / "sessions"
    source = tmp_path / "src" / "example.py"
    source.parent.mkdir()
    source.write_text("def visible_context():\n    return 'provider only context'\n", encoding="utf-8")

    class CliFakeProposalProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            now = datetime.now(UTC)
            if request.provider_turn_index == 0:
                metadata = {
                    PROVIDER_TOOL_INTENT_METADATA_KEY: {
                        "tool_name": "read_only_repo_inspection",
                        "tool_kind": "read_only_workspace",
                        "turn_index": 0,
                        "intent_source": "fake_provider",
                        "approval_policy": "required",
                        "approval_required": True,
                        "sandbox_policy": "read-only-workspace",
                        "workspace_read_allowed": True,
                        "filesystem_mutation_allowed": False,
                        "shell_execution_allowed": False,
                        "network_access_allowed": False,
                        "tool_payloads_stored": False,
                        "stdout_stored": False,
                        "stderr_stored": False,
                        "diffs_stored": False,
                        "file_contents_stored": False,
                        "metadata": {
                            "fixture": "safe-read-only",
                            "request_kind": "explicit-file-excerpt",
                        },
                    },
                    PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY: {
                        "fixture_source": "pipy_owned_explicit_file_excerpt",
                        "tool_request_id": "native-tool-0001",
                        "turn_index": 0,
                        "request_kind": "explicit-file-excerpt",
                        "approval_decision": "allowed",
                        "decision_authority": "pipy-owned",
                        "workspace_relative_path": "src/example.py",
                        "target_authority": "pipy-owned",
                    },
                }
                return ProviderResult(
                    status=HarnessStatus.SUCCEEDED,
                    provider_name=self.name,
                    model_id=self.model_id,
                    started_at=now,
                    ended_at=now,
                    final_text="INITIAL_OUTPUT_SHOULD_NOT_PRINT",
                    metadata=metadata,
                )
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="FOLLOW_UP_OUTPUT_SHOULD_NOT_PRINT_IN_JSON",
                metadata={
                    PROVIDER_PATCH_PROPOSAL_METADATA_KEY: {
                        "proposal_source": "pipy_owned_patch_proposal",
                        "tool_request_id": "native-tool-0001",
                        "turn_index": 0,
                        "status": "proposed",
                        "reason_label": "structured_proposal_accepted",
                        "file_count": 1,
                        "operation_count": 1,
                        "operation_labels": ["modify"],
                        "patch_text_stored": False,
                        "diffs_stored": False,
                        "file_contents_stored": False,
                        "raw_patch_text": "SHOULD_NOT_PERSIST",
                    }
                },
            )

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeProposalProvider)

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-output",
            "json",
            "--slug",
            "native-json-proposal",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--goal",
            "Native JSON proposal smoke",
        ]
    )

    captured = capfd.readouterr()
    output = parse_single_json_stdout(captured.out)
    assert exit_code == 0
    assert output["status"] == "succeeded"
    assert "FOLLOW_UP_OUTPUT_SHOULD_NOT_PRINT_IN_JSON" not in captured.out
    assert "SHOULD_NOT_PERSIST" not in captured.out
    finalized = Path(output["record"]["jsonl_path"])
    events = read_jsonl(finalized)
    assert "native.patch.proposal.recorded" in [event["type"] for event in events]
    assert "native.verification.recorded" not in [event["type"] for event in events]
    combined = finalized.read_text(encoding="utf-8") + finalized.with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "SHOULD_NOT_PERSIST" not in combined
    assert "provider only context" not in combined
    assert "FOLLOW_UP_OUTPUT_SHOULD_NOT_PRINT_IN_JSON" not in combined
    assert "Native JSON proposal smoke" not in captured.out
    assert verify_session_archive(root=root).ok is True


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


def test_cli_native_openai_provider_json_mode_omits_provider_final_text(
    tmp_path, capfd, monkeypatch
):
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
                final_text="OPENAI_OUTPUT_SHOULD_NOT_PRINT_IN_JSON",
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
            "--native-output",
            "json",
            "--slug",
            "openai-json",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--goal",
            "Say hello briefly",
        ]
    )

    captured = capfd.readouterr()
    output = parse_single_json_stdout(captured.out)
    assert exit_code == 0
    assert output["status"] == "succeeded"
    assert output["provider"] == "openai"
    assert output["model_id"] == "gpt-test"
    assert output["usage"] == {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
    assert "OPENAI_OUTPUT_SHOULD_NOT_PRINT_IN_JSON" not in captured.out
    assert "OPENAI_OUTPUT_SHOULD_NOT_PRINT_IN_JSON" not in captured.err
    finalized = Path(output["record"]["jsonl_path"])
    combined = finalized.read_text(encoding="utf-8") + finalized.with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "OPENAI_OUTPUT_SHOULD_NOT_PRINT_IN_JSON" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_openrouter_provider_is_selectable_without_storing_output(
    tmp_path, capfd, monkeypatch
):
    root = tmp_path / "sessions"

    class CliFakeOpenRouterProvider:
        name = "openrouter"
        model_id = "openai/gpt-test"

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
                final_text="OPENROUTER_OUTPUT_SHOULD_PRINT_ONLY",
                usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                metadata={
                    "provider_response_store_requested": False,
                    "response_object": "chat.completion",
                    "finish_reason": "stop",
                },
            )

    monkeypatch.setattr(
        "pipy_harness.cli.OpenRouterChatCompletionsProvider",
        CliFakeOpenRouterProvider,
    )

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-provider",
            "openrouter",
            "--native-model",
            "openai/gpt-test",
            "--slug",
            "openrouter-smoke",
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
    assert captured.out == "OPENROUTER_OUTPUT_SHOULD_PRINT_ONLY\n"
    assert_no_structured_status_stdout(captured.out)
    assert "OPENROUTER_OUTPUT_SHOULD_PRINT_ONLY" not in captured.err
    assert "session finalized" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    events = read_jsonl(finalized[0])
    provider_completed = [event for event in events if event["type"] == "native.provider.completed"][0]
    assert provider_completed["payload"]["provider"] == "openrouter"
    assert provider_completed["payload"]["model_id"] == "openai/gpt-test"
    assert provider_completed["payload"]["usage"] == {
        "input_tokens": 1,
        "output_tokens": 2,
        "total_tokens": 3,
    }
    assert provider_completed["payload"]["provider_metadata"] == {
        "provider_response_store_requested": False,
        "response_object": "chat.completion",
        "finish_reason": "stop",
    }
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "OPENROUTER_OUTPUT_SHOULD_PRINT_ONLY" not in combined
    assert "You are the native pipy runtime bootstrap" not in combined
    assert not search_finalized_sessions("OPENROUTER_OUTPUT_SHOULD_PRINT_ONLY", root=root)
    assert verify_session_archive(root=root).ok is True


def test_cli_native_openrouter_provider_json_mode_omits_provider_final_text(
    tmp_path, capfd, monkeypatch
):
    root = tmp_path / "sessions"

    class CliFakeOpenRouterProvider:
        name = "openrouter"

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
                final_text="OPENROUTER_OUTPUT_SHOULD_NOT_PRINT_IN_JSON",
                usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                metadata={"provider_response_store_requested": False},
            )

    monkeypatch.setattr(
        "pipy_harness.cli.OpenRouterChatCompletionsProvider",
        CliFakeOpenRouterProvider,
    )

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-provider",
            "openrouter",
            "--native-model",
            "openai/gpt-test",
            "--native-output",
            "json",
            "--slug",
            "openrouter-json",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--goal",
            "Say hello briefly",
        ]
    )

    captured = capfd.readouterr()
    output = parse_single_json_stdout(captured.out)
    assert exit_code == 0
    assert output["status"] == "succeeded"
    assert output["provider"] == "openrouter"
    assert output["model_id"] == "openai/gpt-test"
    assert output["usage"] == {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
    assert "OPENROUTER_OUTPUT_SHOULD_NOT_PRINT_IN_JSON" not in captured.out
    assert "OPENROUTER_OUTPUT_SHOULD_NOT_PRINT_IN_JSON" not in captured.err
    finalized = Path(output["record"]["jsonl_path"])
    combined = finalized.read_text(encoding="utf-8") + finalized.with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "OPENROUTER_OUTPUT_SHOULD_NOT_PRINT_IN_JSON" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_openai_codex_provider_is_selectable_without_storing_output(
    tmp_path, capfd, monkeypatch
):
    root = tmp_path / "sessions"

    class CliFakeOpenAICodexProvider:
        name = "openai-codex"
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
                final_text="OPENAI_CODEX_OUTPUT_SHOULD_PRINT_ONLY",
                usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                metadata={
                    "provider_response_store_requested": False,
                    "response_status": "completed",
                },
            )

    monkeypatch.setattr("pipy_harness.cli.OpenAICodexResponsesProvider", CliFakeOpenAICodexProvider)

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-provider",
            "openai-codex",
            "--native-model",
            "gpt-test",
            "--slug",
            "openai-codex-smoke",
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
    assert captured.out == "OPENAI_CODEX_OUTPUT_SHOULD_PRINT_ONLY\n"
    assert_no_structured_status_stdout(captured.out)
    assert "OPENAI_CODEX_OUTPUT_SHOULD_PRINT_ONLY" not in captured.err
    assert "session finalized" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    events = read_jsonl(finalized[0])
    provider_completed = [event for event in events if event["type"] == "native.provider.completed"][0]
    assert provider_completed["payload"]["provider"] == "openai-codex"
    assert provider_completed["payload"]["model_id"] == "gpt-test"
    assert provider_completed["payload"]["usage"] == {
        "input_tokens": 1,
        "output_tokens": 2,
        "total_tokens": 3,
    }
    assert provider_completed["payload"]["provider_metadata"] == {
        "provider_response_store_requested": False,
        "response_status": "completed",
    }
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "OPENAI_CODEX_OUTPUT_SHOULD_PRINT_ONLY" not in combined
    assert "You are the native pipy runtime bootstrap" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_openai_codex_provider_json_mode_omits_provider_final_text(
    tmp_path, capfd, monkeypatch
):
    root = tmp_path / "sessions"

    class CliFakeOpenAICodexProvider:
        name = "openai-codex"

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
                final_text="OPENAI_CODEX_OUTPUT_SHOULD_NOT_PRINT_IN_JSON",
                usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                metadata={"provider_response_store_requested": False},
            )

    monkeypatch.setattr("pipy_harness.cli.OpenAICodexResponsesProvider", CliFakeOpenAICodexProvider)

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-provider",
            "openai-codex",
            "--native-model",
            "gpt-test",
            "--native-output",
            "json",
            "--slug",
            "openai-codex-json",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--goal",
            "Say hello briefly",
        ]
    )

    captured = capfd.readouterr()
    output = parse_single_json_stdout(captured.out)
    assert exit_code == 0
    assert output["status"] == "succeeded"
    assert output["provider"] == "openai-codex"
    assert output["model_id"] == "gpt-test"
    assert output["usage"] == {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
    assert "OPENAI_CODEX_OUTPUT_SHOULD_NOT_PRINT_IN_JSON" not in captured.out
    assert "OPENAI_CODEX_OUTPUT_SHOULD_NOT_PRINT_IN_JSON" not in captured.err
    finalized = Path(output["record"]["jsonl_path"])
    combined = finalized.read_text(encoding="utf-8") + finalized.with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "OPENAI_CODEX_OUTPUT_SHOULD_NOT_PRINT_IN_JSON" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_openai_codex_provider_is_selectable(tmp_path, capfd, monkeypatch):
    root = tmp_path / "sessions"

    class CliFakeOpenAICodexProvider:
        name = "openai-codex"

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
                final_text="OPENAI_CODEX_REPL_OUTPUT",
            )

    monkeypatch.setattr("pipy_harness.cli.OpenAICodexResponsesProvider", CliFakeOpenAICodexProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("hello\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--native-provider",
            "openai-codex",
            "--native-model",
            "gpt-test",
            "--slug",
            "openai-codex-repl",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.out == "OPENAI_CODEX_REPL_OUTPUT\n"
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    provider_completed = [event for event in events if event["type"] == "native.provider.completed"][0]
    assert provider_completed["payload"]["provider"] == "openai-codex"
    assert provider_completed["payload"]["model_id"] == "gpt-test"
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "OPENAI_CODEX_REPL_OUTPUT" not in combined
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


def test_cli_native_openrouter_failure_does_not_print_or_store_provider_final_text(
    tmp_path, capfd, monkeypatch
):
    root = tmp_path / "sessions"

    class CliFailingOpenRouterProvider:
        name = "openrouter"

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
                final_text="OPENROUTER_OUTPUT_SHOULD_NOT_PRINT_ON_FAILURE",
                metadata={"provider_response_store_requested": False},
                error_type="OpenRouterTestFailure",
                error_message="provider failed safely",
            )

    monkeypatch.setattr(
        "pipy_harness.cli.OpenRouterChatCompletionsProvider",
        CliFailingOpenRouterProvider,
    )

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-provider",
            "openrouter",
            "--native-model",
            "openai/gpt-test",
            "--slug",
            "openrouter-provider-failed",
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
    assert "OpenRouterTestFailure" in captured.err
    assert "OPENROUTER_OUTPUT_SHOULD_NOT_PRINT_ON_FAILURE" not in captured.err
    assert "session finalized" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert "native.provider.failed" in event_types
    assert "native.tool.skipped" in event_types
    provider_failed = [event for event in events if event["type"] == "native.provider.failed"][0]
    assert provider_failed["payload"]["provider"] == "openrouter"
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "OPENROUTER_OUTPUT_SHOULD_NOT_PRINT_ON_FAILURE" not in combined
    assert not search_finalized_sessions("OPENROUTER_OUTPUT_SHOULD_NOT_PRINT_ON_FAILURE", root=root)
    assert verify_session_archive(root=root).ok is True


def test_cli_native_provider_failure_json_mode_emits_metadata_only_json(
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
                final_text="OPENAI_OUTPUT_SHOULD_NOT_PRINT_ON_JSON_FAILURE",
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
            "--native-output",
            "json",
            "--slug",
            "openai-json-failed",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--goal",
            "Say hello briefly",
        ]
    )

    captured = capfd.readouterr()
    output = parse_single_json_stdout(captured.out)
    assert exit_code == 1
    assert output["status"] == "failed"
    assert output["exit_code"] == 1
    assert output["provider"] == "openai"
    assert output["model_id"] == "gpt-test"
    assert "OPENAI_OUTPUT_SHOULD_NOT_PRINT_ON_JSON_FAILURE" not in captured.out
    assert "OPENAI_OUTPUT_SHOULD_NOT_PRINT_ON_JSON_FAILURE" not in captured.err
    assert "OpenAITestFailure" in captured.err
    finalized = Path(output["record"]["jsonl_path"])
    events = read_jsonl(finalized)
    event_types = [event["type"] for event in events]
    assert "native.provider.failed" in event_types
    assert "native.tool.skipped" in event_types
    combined = finalized.read_text(encoding="utf-8") + finalized.with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "OPENAI_OUTPUT_SHOULD_NOT_PRINT_ON_JSON_FAILURE" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_openrouter_missing_credentials_finalizes_failed_record(
    tmp_path, capfd, monkeypatch
):
    root = tmp_path / "sessions"
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-provider",
            "openrouter",
            "--native-model",
            "openai/gpt-test",
            "--slug",
            "openrouter-missing-key",
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
    assert "OpenRouterAuthError" in captured.err
    assert "API key is required" in captured.err
    assert "session finalized" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    events = read_jsonl(finalized[0])
    provider_failed = [event for event in events if event["type"] == "native.provider.failed"][0]
    assert provider_failed["payload"]["provider"] == "openrouter"
    assert provider_failed["payload"]["model_id"] == "openai/gpt-test"
    assert provider_failed["payload"]["error_type"] == "OpenRouterAuthError"
    assert "API key is required" in provider_failed["payload"]["error_message"]
    assert "OPENROUTER_API_KEY" not in finalized[0].read_text(encoding="utf-8")
    tool_skipped = [event for event in events if event["type"] == "native.tool.skipped"][0]
    assert tool_skipped["payload"]["reason"] == "provider_not_succeeded"
    assert verify_session_archive(root=root).ok is True


def test_cli_native_openai_codex_missing_credentials_finalizes_failed_record(
    tmp_path, capfd, monkeypatch
):
    root = tmp_path / "sessions"
    monkeypatch.setenv("PIPY_AUTH_DIR", str(tmp_path / "empty-auth"))

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-provider",
            "openai-codex",
            "--native-model",
            "gpt-test",
            "--slug",
            "openai-codex-missing-auth",
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
    assert "OpenAICodexAuthError" in captured.err
    assert "OpenAI Codex login is required" in captured.err
    assert "session finalized" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    events = read_jsonl(finalized[0])
    provider_failed = [event for event in events if event["type"] == "native.provider.failed"][0]
    assert provider_failed["payload"]["provider"] == "openai-codex"
    assert provider_failed["payload"]["model_id"] == "gpt-test"
    assert provider_failed["payload"]["error_type"] == "OpenAICodexAuthError"
    assert "OpenAI Codex login is required" in provider_failed["payload"]["error_message"]
    serialized = finalized[0].read_text(encoding="utf-8")
    assert "access_token" not in serialized
    assert "refresh_token" not in serialized
    tool_skipped = [event for event in events if event["type"] == "native.tool.skipped"][0]
    assert tool_skipped["payload"]["reason"] == "provider_not_succeeded"
    assert verify_session_archive(root=root).ok is True


def test_cli_native_openai_codex_requires_model_before_creating_record(tmp_path, capsys):
    root = tmp_path / "sessions"

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-provider",
            "openai-codex",
            "--slug",
            "openai-codex-missing-model",
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


def test_cli_native_openrouter_requires_model_before_creating_record(tmp_path, capsys):
    root = tmp_path / "sessions"

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-provider",
            "openrouter",
            "--slug",
            "openrouter-missing-model",
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


def test_cli_native_output_rejects_non_native_agent_before_creating_record(tmp_path, capsys):
    root = tmp_path / "sessions"

    exit_code = main(
        [
            "run",
            "--agent",
            "custom",
            "--native-output",
            "json",
            "--slug",
            "custom-json",
            "--root",
            str(root),
            "--",
            sys.executable,
            "-c",
            "print('should not run')",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "--native-output requires --agent pipy-native" in captured.err
    assert captured.out == ""
    assert not root.exists()
