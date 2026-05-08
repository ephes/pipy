from __future__ import annotations

import json
import sys
from io import StringIO
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.cli import main
from pipy_harness.models import HarnessStatus
from pipy_harness.native import (
    PROVIDER_PATCH_PROPOSAL_METADATA_KEY,
    PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY,
    PROVIDER_TOOL_INTENT_METADATA_KEY,
    ProviderRequest,
    ProviderResult,
)
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


def parse_single_json_stdout(stdout: str) -> dict[str, object]:
    lines = stdout.splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert isinstance(parsed, dict)
    return parsed


def test_cli_native_repl_repeats_no_tool_provider_turns_and_finalizes_record(
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
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["status"] == "aborted"
    assert completed_payload["exit_code"] == 130
    assert completed_payload["exit_reason"] == "interrupt"
    assert completed_payload["read_command_used"] is False
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_skips_blank_lines_and_accepts_quit(tmp_path, capfd, monkeypatch):
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
    assert "  /read <workspace-relative-path>" in captured.err
    assert "  /ask-file <workspace-relative-path> -- <question>" in captured.err
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


def test_cli_native_repl_read_command_requires_approval_and_prints_excerpt_only_to_stdout(
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
    monkeypatch.setattr(sys, "stdin", StringIO("/read docs/visible.txt\nyes\n/exit\n"))

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
    assert "pipy approval required" in captured.err
    assert "workspace_read_allowed=true" in captured.err
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
        StringIO("/read\n/read docs/after-malformed-read.txt\nyes\n/exit\n"),
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


def test_cli_native_repl_ask_file_requires_approval_and_sends_excerpt_only_to_provider(
    tmp_path,
    capfd,
    monkeypatch,
):
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
        StringIO("/ask-file docs/context.txt -- What does this say?\nyes\n/exit\n"),
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
    assert "pipy approval required" in captured.err
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
):
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
        StringIO("/ask-file\tdocs/context.txt\t--\tWhat does this say?\nyes\n/exit\n"),
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
    assert "pipy approval required" in captured.err
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


def test_cli_native_repl_ask_file_denied_does_not_call_provider(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    source = tmp_path / "docs" / "denied-context.txt"
    source.parent.mkdir()
    source.write_text("DENIED_PROVIDER_CONTEXT\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeAskFileProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("denied ask-file command should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeAskFileProvider)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO("/ask-file docs/denied-context.txt -- Summarize it\nno\n/exit\n"),
    )

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-ask-file-denied",
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
    assert "ask-file command skipped: approval_not_allowed" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert "native.provider.started" not in event_types
    assert "native.tool.observation.recorded" not in event_types
    assert event_types.count("native.tool.skipped") == 1
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["read_command_used"] is True
    assert completed_payload["ask_file_command_used"] is True
    assert completed_payload["provider_visible_context_used"] is False
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "DENIED_PROVIDER_CONTEXT" not in combined
    assert "Summarize it" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_read_command_denied_does_not_read(tmp_path, capfd, monkeypatch):
    root = tmp_path / "sessions"
    source = tmp_path / "docs" / "denied.txt"
    source.parent.mkdir()
    source.write_text("DENIED_EXCERPT_TEXT\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("denied read command should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/read docs/denied.txt\nno\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-read-denied",
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
    assert "read command skipped: approval_not_allowed" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert "native.provider.started" not in event_types
    assert event_types.count("native.tool.started") == 1
    assert event_types.count("native.tool.skipped") == 1
    tool_payload = [event["payload"] for event in events if event["type"] == "native.tool.skipped"][0]
    assert tool_payload["tool_metadata"]["approval_decision"] == "denied"
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["read_command_used"] is True
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "DENIED_EXCERPT_TEXT" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_read_command_unavailable_prompt_fails_closed(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    source = tmp_path / "docs" / "unavailable.txt"
    source.parent.mkdir()
    source.write_text("UNAVAILABLE_EXCERPT_TEXT\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("unavailable read command should not call provider")

    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", CliFakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO("/read docs/unavailable.txt\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-read-unavailable",
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
    assert "Approve? [y/N]:" in captured.err
    assert "read command skipped: approval_not_allowed" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    tool_payload = [event["payload"] for event in events if event["type"] == "native.tool.skipped"][0]
    assert tool_payload["tool_metadata"]["approval_decision"] == "skipped"
    completed_payload = [
        event["payload"] for event in events if event["type"] == "native.session.completed"
    ][0]
    assert completed_payload["exit_reason"] == "eof"
    assert completed_payload["read_command_used"] is True
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "UNAVAILABLE_EXCERPT_TEXT" not in combined
    assert verify_session_archive(root=root).ok is True


def test_cli_native_repl_read_command_rejects_unsafe_target_before_prompt(
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


def test_cli_native_repl_unsafe_read_target_consumes_the_one_read_limit(
    tmp_path,
    capfd,
    monkeypatch,
):
    root = tmp_path / "sessions"
    valid = tmp_path / "docs" / "valid.txt"
    valid.parent.mkdir()
    valid.write_text("VALID_EXCERPT_SHOULD_NOT_PRINT\n", encoding="utf-8")
    provider_calls = 0

    class CliFakeReplProvider:
        name = "fake"

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("unsafe read target limit should not call provider")

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
    assert captured.out == ""
    assert "unsafe_repl_read_target" in captured.err
    assert "read_command_limit_reached" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.tool.skipped") == 1
    assert "native.tool.completed" not in event_types
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
    assert "VALID_EXCERPT_SHOULD_NOT_PRINT" not in combined
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
        StringIO("/read docs/first.txt\nyes\n/read docs/second.txt\n/exit\n"),
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
    assert "read_command_limit_reached" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    events = read_jsonl(finalized[0])
    event_types = [event["type"] for event in events]
    assert event_types.count("native.tool.completed") == 1
    combined = finalized[0].read_text(encoding="utf-8") + finalized[0].with_suffix(".md").read_text(
        encoding="utf-8"
    )
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
        StringIO("/read docs/first.txt\nyes\n/ask-file docs/second.txt -- Use this\n/exit\n"),
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


def test_cli_native_repl_ask_file_command_blocks_later_read(tmp_path, capfd, monkeypatch):
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
        StringIO("/ask-file docs/first.txt -- Use this\nyes\n/read docs/second.txt\n/exit\n"),
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
        StringIO("/ask-file docs/after-malformed.txt -- \n/read docs/after-malformed.txt\nyes\n/exit\n"),
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
