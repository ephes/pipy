from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.cli import main
from pipy_harness.models import HarnessStatus
from pipy_harness.native import (
    OpenAICodexProviderError,
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


@pytest.fixture(autouse=True)
def isolate_native_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PIPY_NATIVE_DEFAULTS_PATH", str(tmp_path / "native-defaults.json"))
    # The auto provider picker probes the openai-codex OAuth credential file
    # and conventional API-key env vars. Point them at empty tmp paths /
    # unset them so tests get the deterministic fake fallback unless they
    # opt in explicitly.
    monkeypatch.setenv("PIPY_AUTH_DIR", str(tmp_path / "isolated-auth"))
    for env_name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "MISTRAL_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_KEY",
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
        "GOOGLE_ACCESS_TOKEN",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_PROJECT_ID",
    ):
        monkeypatch.delenv(env_name, raising=False)


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


def test_repl_mode_flag_is_removed() -> None:
    import subprocess

    from pipy_harness.cli import build_parser

    # The repl subparser no longer defines --repl-mode.
    parser = build_parser()
    args = parser.parse_args(["repl"])
    assert not hasattr(args, "repl_mode")

    # argparse rejects the retired flag rather than silently accepting it.
    with pytest.raises(SystemExit):
        parser.parse_args(["repl", "--repl-mode", "no-tool"])

    # The flag is gone from the repl help surface too.
    help_proc = subprocess.run(
        [sys.executable, "-m", "pipy_harness.cli", "repl", "--help"],
        capture_output=True,
        text=True,
    )
    assert help_proc.returncode == 0
    assert "--repl-mode" not in help_proc.stdout


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


def test_cli_native_repl_explicit_prompt_toolkit_rejects_captured_stream_before_record(
    tmp_path,
    capfd,
    monkeypatch,
) -> None:
    root = tmp_path / "sessions"
    monkeypatch.setattr(sys, "stdin", StringIO("/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-prompt-toolkit-captured",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--input-runtime",
            "prompt-toolkit",
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "pipy: prompt-toolkit input requires the process stdin and stderr TTY streams" in captured.err
    assert not list(root.glob("**/*.jsonl"))


def test_cli_bare_pipy_starts_native_repl_with_default_slug(tmp_path, capfd, monkeypatch) -> None:
    root = tmp_path / "sessions"
    captured_requests: list = []

    monkeypatch.setenv("PIPY_SESSION_DIR", str(root))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "pipy_harness.cli.AutomationFakeProvider",
        _capturing_repl_provider(captured_requests),
    )
    monkeypatch.setattr(sys, "stdin", StringIO("hello there\n/exit\n"))

    exit_code = main([])

    captured = capfd.readouterr()
    assert exit_code == 0
    # The bare invocation drives the tool-loop product REPL with the default slug.
    assert "pipy v" in captured.err
    assert captured.err.count("pipy v") == 1
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    assert "native-repl" in finalized[0].name


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

        def __init__(self, model_id=None, **_kwargs) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
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

        def __init__(self, model_id=None, **_kwargs) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
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

    monkeypatch.setattr(
        "pipy_harness.native.openai_provider.OpenAIResponsesProvider", CliFakeOpenAIProvider
    )

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

        def __init__(self, model_id=None, **_kwargs) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
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

    monkeypatch.setattr(
        "pipy_harness.native.openai_provider.OpenAIResponsesProvider", CliFakeOpenAIProvider
    )

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

        def __init__(self, model_id=None, **_kwargs) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
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
        "pipy_harness.native.openai_completions_provider.OpenAIChatCompletionsProvider",
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

        def __init__(self, model_id=None, **_kwargs) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
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
        "pipy_harness.native.openai_completions_provider.OpenAIChatCompletionsProvider",
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

        def __init__(self, model_id=None, **_kwargs) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
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

        def __init__(self, model_id: str, retry_policy: object = None) -> None:
            self.model_id = model_id
            self.retry_policy = retry_policy

        def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
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


def test_cli_native_openai_failure_does_not_print_or_store_provider_final_text(
    tmp_path, capfd, monkeypatch
):
    root = tmp_path / "sessions"

    class CliFailingOpenAIProvider:
        name = "openai"

        def __init__(self, model_id=None, **_kwargs) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
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

    monkeypatch.setattr(
        "pipy_harness.native.openai_provider.OpenAIResponsesProvider", CliFailingOpenAIProvider
    )

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

        def __init__(self, model_id=None, **_kwargs) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
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
        "pipy_harness.native.openai_completions_provider.OpenAIChatCompletionsProvider",
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

        def __init__(self, model_id=None, **_kwargs) -> None:
            self.model_id = model_id

        def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
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

    monkeypatch.setattr(
        "pipy_harness.native.openai_provider.OpenAIResponsesProvider", CliFailingOpenAIProvider
    )

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
    # openrouter is the openai-completions family, so catalog construction (run
    # now uses it, matching the REPL) builds the completions adapter; its auth
    # error type is OpenAICompletionsAuthError, reported under provider=openrouter.
    assert "OpenAICompletionsAuthError" in captured.err
    assert "API key is required" in captured.err
    assert "session finalized" in captured.err
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    events = read_jsonl(finalized[0])
    provider_failed = [event for event in events if event["type"] == "native.provider.failed"][0]
    assert provider_failed["payload"]["provider"] == "openrouter"
    assert provider_failed["payload"]["model_id"] == "openai/gpt-test"
    assert provider_failed["payload"]["error_type"] == "OpenAICompletionsAuthError"
    assert "API key is required" in provider_failed["payload"]["error_message"]
    assert "OPENROUTER_API_KEY" not in finalized[0].read_text(encoding="utf-8")
    tool_skipped = [event for event in events if event["type"] == "native.tool.skipped"][0]
    assert tool_skipped["payload"]["reason"] == "provider_not_succeeded"
    assert verify_session_archive(root=root).ok is True


def test_cli_native_run_bare_model_resolves_provider_at_launch(
    tmp_path, capfd, monkeypatch
):
    # Slice: startup CLI resolution. A bare --native-model (no --native-provider)
    # now resolves its provider through the catalog (anthropic), instead of the
    # old fake/<ref> behavior. With no key the anthropic adapter fails closed, so
    # the failed record names provider=anthropic (proving it did NOT become fake).
    root = tmp_path / "sessions"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_OAUTH_TOKEN", raising=False)

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-model",
            "claude-opus-4-7",
            "--slug",
            "bare-model-resolves",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--goal",
            "Say hello briefly",
        ]
    )

    assert exit_code == 1
    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    events = read_jsonl(finalized[0])
    provider_failed = [
        event for event in events if event["type"] == "native.provider.failed"
    ][0]
    # resolved to anthropic (NOT fake) from the bare model reference
    assert provider_failed["payload"]["provider"] == "anthropic"
    assert provider_failed["payload"]["model_id"] == "claude-opus-4-7"
    assert verify_session_archive(root=root).ok is True


def test_cli_native_run_stream_validates_resolved_provider(tmp_path, capfd):
    # --stream + a bare --native-model that resolves to a non-streaming provider
    # (anthropic) must be rejected: stream validation now uses the RESOLVED
    # provider, not the raw (None -> "fake") one.
    root = tmp_path / "sessions"
    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-model",
            "claude-opus-4-7",
            "--stream",
            "--slug",
            "stream-bare-model",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--goal",
            "Say hello briefly",
        ]
    )
    captured = capfd.readouterr()
    assert exit_code != 0
    assert "streaming-capable native provider" in captured.err


def test_cli_native_run_stream_bare_fake_model_ok(tmp_path, capfd):
    # A bare --native-model that resolves to fake (a streaming provider) still
    # passes stream validation.
    root = tmp_path / "sessions"
    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-model",
            "fake-native-bootstrap",
            "--stream",
            "--slug",
            "stream-bare-fake",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--goal",
            "Say hello briefly",
        ]
    )
    assert exit_code == 0


def test_cli_native_run_requires_model_for_case_variant_provider(tmp_path, capfd):
    # A case-variant of a built-in real provider must still trigger the run-only
    # "explicit model required" rule (checked against the RESOLVED canonical
    # provider, so native_provider_spec("OpenAI") -> None no longer bypasses it).
    root = tmp_path / "sessions"
    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-provider",
            "OpenAI",
            "--slug",
            "case-variant-requires-model",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--goal",
            "Say hello briefly",
        ]
    )
    captured = capfd.readouterr()
    assert exit_code != 0
    assert "--native-model is required" in captured.err


def test_cli_native_run_unknown_provider_errors(tmp_path, capfd):
    # argparse no longer rejects custom names; an unknown provider now surfaces
    # the catalog resolver's clear error instead of an argparse choice failure.
    root = tmp_path / "sessions"
    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-provider",
            "definitely-not-a-provider",
            "--slug",
            "unknown-provider",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
            "--goal",
            "Say hello briefly",
        ]
    )
    captured = capfd.readouterr()
    assert exit_code != 0
    assert 'Unknown provider "definitely-not-a-provider"' in captured.err


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


def test_cli_stream_requires_pipy_native_agent(tmp_path, capsys):
    root = tmp_path / "sessions"

    exit_code = main(
        [
            "run",
            "--agent",
            "custom",
            "--stream",
            "--slug",
            "custom-stream",
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
    assert "--stream requires --agent pipy-native" in captured.err
    assert captured.out == ""
    assert not root.exists()


def test_cli_stream_rejects_non_streaming_native_provider(tmp_path, capsys):
    root = tmp_path / "sessions"

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-provider",
            "openrouter",
            "--native-model",
            "openrouter/auto",
            "--stream",
            "--goal",
            "GOAL",
            "--slug",
            "openrouter-stream",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "streaming-capable native provider" in captured.err
    assert captured.out == ""
    assert not root.exists()


def test_cli_stream_with_fake_provider_streams_chunks_to_stdout_and_keeps_archive_metadata_only(
    tmp_path, capsys, monkeypatch
):
    root = tmp_path / "sessions"

    class CliStreamingFakeProvider:
        name = "fake"

        def __init__(self, model_id=None, **_kwargs) -> None:
            self.model_id = model_id
            self.programmable_text_chunks = ("STREAM_", "CHUNK_", "ABC")

        def complete(
            self,
            request: ProviderRequest,
            *,
            stream_sink=None,
            **_kwargs: object,
        ) -> ProviderResult:
            now = datetime.now(UTC)
            final = "".join(self.programmable_text_chunks)
            if stream_sink is not None:
                for piece in self.programmable_text_chunks:
                    stream_sink(piece)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text=final,
                usage={"input_tokens": 1, "output_tokens": 3, "total_tokens": 4},
                metadata=None,
            )

    monkeypatch.setattr(
        "pipy_harness.cli.FakeNativeProvider", CliStreamingFakeProvider
    )

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--stream",
            "--goal",
            "stream smoke",
            "--slug",
            "fake-stream",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "STREAM_CHUNK_ABC\n"
    assert "session finalized" in captured.err

    finalized = list((root / "pipy").glob("*/*/*.jsonl"))
    assert len(finalized) == 1
    events = read_jsonl(finalized[0])
    serialized = json.dumps(events)
    assert "STREAM_" not in serialized
    assert "CHUNK_" not in serialized
    assert "STREAM_CHUNK_ABC" not in serialized


def test_cli_stream_in_json_output_mode_routes_chunks_to_stderr(
    tmp_path, capsys, monkeypatch
):
    root = tmp_path / "sessions"

    class CliStreamingFakeProvider:
        name = "fake"

        def __init__(self, model_id=None, **_kwargs) -> None:
            self.model_id = model_id
            self.programmable_text_chunks = ("ALPHA", "BETA")

        def complete(
            self,
            request: ProviderRequest,
            *,
            stream_sink=None,
            **_kwargs: object,
        ) -> ProviderResult:
            now = datetime.now(UTC)
            final = "".join(self.programmable_text_chunks)
            if stream_sink is not None:
                for piece in self.programmable_text_chunks:
                    stream_sink(piece)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text=final,
                usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                metadata=None,
            )

    monkeypatch.setattr(
        "pipy_harness.cli.FakeNativeProvider", CliStreamingFakeProvider
    )

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--stream",
            "--native-output",
            "json",
            "--goal",
            "GOAL",
            "--slug",
            "fake-stream-json",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "ALPHABETA" in captured.err
    json_lines = [line for line in captured.out.splitlines() if line.startswith("{")]
    assert len(json_lines) == 1
    parsed = json.loads(json_lines[0])
    assert parsed.get("schema") == "pipy.native_output"


def test_cli_stream_off_keeps_existing_buffered_stdout_behavior(
    tmp_path, capsys, monkeypatch
):
    root = tmp_path / "sessions"

    class CliBufferedFakeProvider:
        name = "fake"

        def __init__(self, model_id=None, **_kwargs) -> None:
            self.model_id = model_id

        def complete(
            self,
            request: ProviderRequest,
            *,
            stream_sink=None,
            **_kwargs: object,
        ) -> ProviderResult:
            assert stream_sink is None, "stream_sink should not be passed when --stream is off"
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="BUFFERED_TEXT",
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                metadata=None,
            )

    monkeypatch.setattr(
        "pipy_harness.cli.FakeNativeProvider", CliBufferedFakeProvider
    )

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--goal",
            "GOAL",
            "--slug",
            "fake-buffered",
            "--root",
            str(root),
            "--cwd",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "BUFFERED_TEXT\n"


def test_cli_run_selects_workspace_extension_provider(
    tmp_path, capsys, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    extension_dir = workspace / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "one_shot_provider.py").write_text(
        "from datetime import datetime, timezone\n"
        "from pipy_harness.extensions import ExtensionProvider\n"
        "from pipy_harness.models import HarnessStatus\n"
        "from pipy_harness.native.models import ProviderResult\n"
        "class _Port:\n"
        "    name = 'oneshot'\n"
        "    supports_tool_calls = False\n"
        "    def __init__(self, ctx): self.model_id = ctx.model_id\n"
        "    def complete(self, request, **kwargs):\n"
        "        now = datetime(2026, 6, 18, tzinfo=timezone.utc)\n"
        "        return ProviderResult(status=HarnessStatus.SUCCEEDED,\n"
        "            provider_name=self.name, model_id=self.model_id,\n"
        "            started_at=now, ended_at=now,\n"
        "            final_text='one-shot:' + self.model_id, tool_calls=())\n"
        "def activate(api):\n"
        "    api.register_provider(ExtensionProvider(name='oneshot',\n"
        "        default_model='default', models=('default',),\n"
        "        factory=lambda ctx: _Port(ctx)))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))

    exit_code = main(
        [
            "run",
            "--agent",
            "pipy-native",
            "--native-provider",
            "oneshot",
            "--goal",
            "Use extension provider",
            "--slug",
            "extension-one-shot",
            "--root",
            str(tmp_path / "sessions"),
            "--cwd",
            str(workspace),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "one-shot:default\n"


def test_cli_list_models_prints_table_and_exits(tmp_path, capsys, monkeypatch):
    # Isolate auth + config so only the env-keyed provider is available.
    monkeypatch.setenv("PIPY_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("PIPY_OPENAI_CODEX_AUTH_PATH", str(tmp_path / "absent-codex.json"))
    for var in ("ANTHROPIC_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    exit_code = main(["repl", "--list-models"])
    assert exit_code == 0
    out = capsys.readouterr().out
    header = out.splitlines()[0].split()
    assert header == ["provider", "model", "context", "max-out", "thinking", "images"]
    assert "openai" in out
    assert "fake" in out


def test_cli_list_models_fuzzy_search(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PIPY_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("MISTRAL_API_KEY", "sk-test")

    exit_code = main(["repl", "--list-models", "mistral"])
    assert exit_code == 0
    body = [
        line
        for line in capsys.readouterr().out.splitlines()[1:]
        if line.strip()
    ]
    # Fuzzy filter over "provider id": only mistral rows match "mistral".
    assert body
    assert all(line.split()[0] == "mistral" for line in body)


def test_cli_repl_accepts_extension_registered_flags(
    tmp_path, capfd, monkeypatch
) -> None:
    ext = tmp_path / "flagger.py"
    ext.write_text(
        "from pipy_harness.extensions import ExtensionFlag\n"
        "def activate(api):\n"
        "    api.register_flag(ExtensionFlag('plan', 'boolean', default=False))\n"
        "    api.register_flag(ExtensionFlag('ticket', 'string'))\n"
        "    def show(ctx, args):\n"
        "        ctx.ui.notify(str(ctx.flags.get('plan')) + ':' + ctx.flags.get('ticket', ''))\n"
        "    api.register_command('show-flags', 'show flags', show)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(sys, "stdin", StringIO("/show-flags\n/exit\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "PIPY-123",
            "--root",
            str(tmp_path / "sessions"),
            "--cwd",
            str(tmp_path),
            "--native-provider",
            "fake",
            "--native-model",
            "fake-tools",
            "--no-session",
            "--extension",
            str(ext),
            "--ticket",
            "PIPY-123",
            "--plan",
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert "True:PIPY-123" in captured.err


def test_cli_unknown_extension_flag_fails_before_provider_turn(
    tmp_path, capfd, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(sys, "stdin", StringIO("should not run\n"))

    exit_code = main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            "native-repl-extension-flag-error",
            "--root",
            str(tmp_path / "sessions"),
            "--cwd",
            str(tmp_path),
            "--native-provider",
            "fake",
            "--native-model",
            "fake-tools",
            "--no-session",
            "--unknown-ext-flag",
        ]
    )

    captured = capfd.readouterr()
    assert exit_code == 2
    assert "unknown extension flag: --unknown-ext-flag" in captured.err


def _capturing_repl_provider(captured: list) -> type:
    class _Provider:
        name = "fake"
        supports_tool_calls = True

        def __init__(self, model_id=None, **_kwargs) -> None:
            self.model_id = model_id

        def complete(
            self,
            request,
            *,
            stream_sink=None,
            reasoning_sink=None,
            cancel_token=None,
        ):
            captured.append(request)
            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="OK",
                tool_calls=(),
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                metadata={},
            )

    return _Provider


def test_cli_system_prompt_flag_replaces_base_prompt(tmp_path, monkeypatch) -> None:
    captured: list = []
    monkeypatch.setattr("pipy_harness.cli.AutomationFakeProvider", _capturing_repl_provider(captured))
    monkeypatch.setattr(sys, "stdin", StringIO("hello\n/exit\n"))
    main(
        [
            "repl", "--agent", "pipy-native", "--slug", "sp", "--no-session",
            "--root", str(tmp_path / "s"), "--cwd", str(tmp_path),
            "--system-prompt", "REPLACED SYSTEM PROMPT BODY",
        ]
    )
    assert captured, "no provider request captured"
    sp = captured[0].system_prompt
    assert "REPLACED SYSTEM PROMPT BODY" in sp
    assert "pipy" not in sp.split("REPLACED")[0]  # default bootstrap text gone before custom


def test_cli_append_system_prompt_flag_appends(tmp_path, monkeypatch) -> None:
    captured: list = []
    monkeypatch.setattr("pipy_harness.cli.AutomationFakeProvider", _capturing_repl_provider(captured))
    monkeypatch.setattr(sys, "stdin", StringIO("hello\n/exit\n"))
    main(
        [
            "repl", "--agent", "pipy-native", "--slug", "ap", "--no-session",
            "--root", str(tmp_path / "s"), "--cwd", str(tmp_path),
            "--append-system-prompt", "APPENDED ONE",
            "--append-system-prompt", "APPENDED TWO",
        ]
    )
    sp = captured[0].system_prompt
    assert "APPENDED ONE" in sp
    assert "APPENDED TWO" in sp


def test_positional_prompt_seeds_interactive_first_message(
    tmp_path, monkeypatch
) -> None:
    captured: list = []
    monkeypatch.setattr(
        "pipy_harness.cli.AutomationFakeProvider", _capturing_repl_provider(captured)
    )
    # Only `/exit` on stdin: the seeded positional prompt must be the first user
    # turn, so the provider sees it before the interactive loop reads stdin.
    monkeypatch.setattr(sys, "stdin", StringIO("/exit\n"))
    exit_code = main(
        [
            "repl", "--agent", "pipy-native", "--slug", "seed", "--no-session",
            "--root", str(tmp_path / "s"), "--cwd", str(tmp_path),
            "hello there",
        ]
    )
    assert exit_code == 0
    assert captured, "no provider request captured; the prompt was not seeded"
    assert captured[0].user_prompt == "hello there"


def test_bare_positional_prompt_routes_and_seeds_interactive(
    tmp_path, monkeypatch
) -> None:
    # The router turns `pipy "<prompt>"` into `repl <prompt>` and the prompt
    # seeds the interactive first message (no explicit subcommand).
    root = tmp_path / "sessions"
    captured: list = []
    monkeypatch.setenv("PIPY_SESSION_DIR", str(root))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "pipy_harness.cli.AutomationFakeProvider", _capturing_repl_provider(captured)
    )
    monkeypatch.setattr(sys, "stdin", StringIO("/exit\n"))
    exit_code = main(["summarize this repo"])
    assert exit_code == 0
    assert captured, "no provider request captured; bare prompt was not seeded"
    assert captured[0].user_prompt == "summarize this repo"


def test_mode_rpc_still_rejects_positional_prompt(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "pipy_harness.cli.AutomationFakeProvider", _capturing_repl_provider([])
    )
    exit_code = main(
        [
            "repl", "--agent", "pipy-native", "--slug", "rpc", "--no-session",
            "--root", str(tmp_path / "s"), "--cwd", str(tmp_path),
            "--mode", "rpc",
            "do X",
        ]
    )
    assert exit_code == 2


def test_router_does_not_swallow_version(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "pipy " in (captured.out + captured.err)


def test_router_does_not_swallow_help_and_mentions_interactive(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "interactive" in out.lower()
    assert "[prompt]" in out


def test_router_does_not_swallow_export(tmp_path, capsys) -> None:
    # `--export` is a root-only flag: it reaches the export handler (which fails
    # on a missing file) rather than being re-routed into the repl.
    missing = tmp_path / "missing-session.jsonl"
    exit_code = main(["--export", str(missing)])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "pipy:" in err


def test_router_does_not_swallow_export_equals_form(tmp_path, capsys) -> None:
    # The argparse `--export=FILE` form is also top-level: it must reach the
    # export handler, not the repl unknown-extension-flag path.
    missing = tmp_path / "missing-session.jsonl"
    exit_code = main([f"--export={missing}"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "pipy:" in err
    assert "unknown extension flag" not in err


def test_router_list_models_routes_to_repl_flag(tmp_path, monkeypatch, capsys) -> None:
    # `--list-models` is a repl/run flag, so the router injects `repl` and the
    # catalog table prints (exit 0), rather than argparse rejecting it.
    monkeypatch.chdir(tmp_path)
    exit_code = main(["--list-models"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert out.strip()


def test_cli_system_md_auto_discovery_replaces(tmp_path, monkeypatch) -> None:
    (tmp_path / ".pipy").mkdir()
    (tmp_path / ".pipy" / "SYSTEM.md").write_text("DISCOVERED SYSTEM MD", encoding="utf-8")
    captured: list = []
    monkeypatch.setattr("pipy_harness.cli.AutomationFakeProvider", _capturing_repl_provider(captured))
    monkeypatch.setattr(sys, "stdin", StringIO("hi\n/exit\n"))
    main(
        [
            "repl", "--agent", "pipy-native", "--slug", "smd", "--no-session",
            "--root", str(tmp_path / "s"), "--cwd", str(tmp_path),
        ]
    )
    assert "DISCOVERED SYSTEM MD" in captured[0].system_prompt


def test_cli_no_context_files_disables_discovery(tmp_path, monkeypatch) -> None:
    (tmp_path / "AGENTS.md").write_text("SECRET PROJECT INSTRUCTIONS", encoding="utf-8")
    captured: list = []
    monkeypatch.setattr("pipy_harness.cli.AutomationFakeProvider", _capturing_repl_provider(captured))
    monkeypatch.setattr(sys, "stdin", StringIO("hi\n/exit\n"))
    main(
        [
            "repl", "--agent", "pipy-native", "--slug", "nc", "--no-session",
            "--root", str(tmp_path / "s"), "--cwd", str(tmp_path),
            "--no-context-files",
        ]
    )
    # AGENTS.md content must not be injected into the system prompt.
    assert "SECRET PROJECT INSTRUCTIONS" not in captured[0].system_prompt


def test_provider_factory_applies_retry_settings_to_openai_codex(tmp_path) -> None:
    from pipy_harness.cli import _provider_factory_for
    from pipy_harness.native import NativeModelSelection
    from pipy_harness.native.settings import SettingsManager

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.json").write_text(
        json.dumps(
            {"retry": {"maxRetries": 5, "baseDelayMs": 500, "provider": {"maxRetryDelayMs": 30000}}}
        ),
        encoding="utf-8",
    )
    manager = SettingsManager(
        global_path=tmp_path / "config" / "settings.json",
        project_path=tmp_path / ".pipy" / "settings.json",
    )
    factory = _provider_factory_for(manager)
    provider = factory(NativeModelSelection("openai-codex", "gpt-5.5"))
    policy = provider.retry_policy  # type: ignore[attr-defined]
    assert policy.max_attempts == 6
    assert policy.initial_delay_seconds == 0.5
    assert policy.max_delay_seconds == 30.0


def test_provider_factory_without_settings_keeps_provider_default(tmp_path) -> None:
    from pipy_harness.cli import _provider_factory_for
    from pipy_harness.native import NativeModelSelection

    provider = _provider_factory_for(None)(NativeModelSelection("openai-codex", "gpt-5.5"))
    # Built-in openai-codex default policy (unchanged when no settings).
    policy = provider.retry_policy  # type: ignore[attr-defined]
    assert policy.max_attempts == 4
    assert policy.initial_delay_seconds == 1.0


def _make_skill(cwd, name: str) -> None:
    skills_dir = cwd / ".pipy" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / f"{name}.md").write_text(f"# {name}\n\nbody\n", encoding="utf-8")


def test_cli_config_disable_then_enable_skill_writes_patterns(tmp_path, capfd, monkeypatch) -> None:
    config_home = tmp_path / "cfg"
    config_home.mkdir()
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config_home))
    _make_skill(tmp_path, "review")

    assert main(["config", "disable", "skill", "review", "--cwd", str(tmp_path)]) == 0
    on_disk = json.loads((config_home / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["skills"] == ["-review"]

    assert main(["config", "enable", "skill", "review", "--cwd", str(tmp_path)]) == 0
    on_disk = json.loads((config_home / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["skills"] == ["+review"]


def test_cli_config_list_shows_disabled_package_theme(tmp_path, capfd, monkeypatch) -> None:
    # A disabled package-contributed theme must still appear in `config list`
    # with enabled=false (like skills/prompts/extensions), not vanish.
    config_home = tmp_path / "cfg"
    config_home.mkdir()
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config_home))
    ws = tmp_path / "ws"
    (ws / ".pipy").mkdir(parents=True)
    pkg = tmp_path / "pkg"
    (pkg / "themes").mkdir(parents=True)
    (pkg / "themes" / "midnight.toml").write_text(
        'name = "midnight"\naccent_truecolor = "38;2;1;2;3"\n', encoding="utf-8"
    )

    assert main(["install", str(pkg), "-l", "--cwd", str(ws)]) == 0
    assert main(["config", "disable", "theme", "midnight", "--cwd", str(ws)]) == 0
    capfd.readouterr()

    assert main(["config", "list", "--json", "--cwd", str(ws)]) == 0
    report = json.loads(capfd.readouterr().out)
    midnight = next(t for t in report["themes"] if t["name"] == "midnight")
    assert midnight["enabled"] is False


def test_cli_config_list_builtin_theme_stays_enabled(tmp_path, capfd, monkeypatch) -> None:
    # Runtime theme filters apply only to package themes; built-ins remain
    # selectable. `config list` must report built-ins as enabled even with a
    # `-builtin` filter present, so the report matches runtime behavior.
    config_home = tmp_path / "cfg"
    config_home.mkdir()
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config_home))

    assert main(["config", "disable", "theme", "ocean", "--cwd", str(tmp_path)]) == 0
    capfd.readouterr()

    assert main(["config", "list", "--json", "--cwd", str(tmp_path)]) == 0
    report = json.loads(capfd.readouterr().out)
    ocean = next(t for t in report["themes"] if t["name"] == "ocean")
    assert ocean["enabled"] is True


def test_cli_config_list_json_reports_enabled_state(tmp_path, capfd, monkeypatch) -> None:
    config_home = tmp_path / "cfg"
    config_home.mkdir()
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config_home))
    _make_skill(tmp_path, "review")
    (config_home / "settings.json").write_text(
        json.dumps({"skills": ["-review"]}), encoding="utf-8"
    )

    assert main(["config", "list", "--json", "--cwd", str(tmp_path)]) == 0
    out = capfd.readouterr().out
    report = json.loads(out)
    review = next(s for s in report["skills"] if s["name"] == "review")
    assert review["enabled"] is False


def test_cli_config_disabled_skill_dropped_from_registration(tmp_path) -> None:
    from pipy_harness.native.resources import WorkspaceResources
    from pipy_harness.native.settings import SettingsManager

    _make_skill(tmp_path, "review")
    _make_skill(tmp_path, "draft")
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "settings.json").write_text(
        json.dumps({"skills": ["-review"]}), encoding="utf-8"
    )
    manager = SettingsManager(
        global_path=tmp_path / "cfg" / "settings.json",
        project_path=tmp_path / ".pipy" / "settings.json",
    )
    resources = WorkspaceResources.discover(tmp_path).with_enablement(
        skills_patterns=manager.get_skills_patterns(),
        prompts_patterns=manager.get_prompts_patterns(),
        enable_skill_commands=manager.get_enable_skill_commands(),
    )
    names = resources.skill_names()
    assert "draft" in names
    assert "review" not in names


def test_cli_config_enable_skill_commands_false_drops_all_skills(tmp_path) -> None:
    from pipy_harness.native.resources import WorkspaceResources

    _make_skill(tmp_path, "review")
    resources = WorkspaceResources.discover(tmp_path).with_enablement(
        enable_skill_commands=False,
    )
    assert resources.skill_names() == ()


def test_cli_version_prints_version(capfd) -> None:
    import pytest as _pytest

    with _pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capfd.readouterr().out
    assert out.startswith("pipy ")
    assert any(ch.isdigit() for ch in out)


def test_cli_models_flag_constrains_scoped_set(tmp_path) -> None:
    from pipy_harness.cli import _build_runtime_settings, _parse_models_flag

    # --models patterns apply as a CLI override of enabledModels (CLI > file).
    (tmp_path / "cfg").mkdir()
    settings_path = tmp_path / "cfg" / "settings.json"
    settings_path.write_text(
        json.dumps({"enabledModels": ["anthropic/*"]}), encoding="utf-8"
    )
    monkeypatch_env = {"PIPY_CONFIG_HOME": str(tmp_path / "cfg")}
    import os as _os

    saved = _os.environ.get("PIPY_CONFIG_HOME")
    _os.environ["PIPY_CONFIG_HOME"] = monkeypatch_env["PIPY_CONFIG_HOME"]
    try:
        mgr = _build_runtime_settings(
            tmp_path, scoped_models=_parse_models_flag("openai/*:high,openai/gpt-4")
        )
    finally:
        if saved is None:
            _os.environ.pop("PIPY_CONFIG_HOME", None)
        else:
            _os.environ["PIPY_CONFIG_HOME"] = saved
    # CLI override wins over the settings file; :level suffix is stripped.
    assert mgr.get_enabled_models() == ["openai/*", "openai/gpt-4"]


def test_parse_models_flag_handles_empty_and_levels() -> None:
    from pipy_harness.cli import _parse_models_flag

    assert _parse_models_flag(None) is None
    assert _parse_models_flag("") is None
    assert _parse_models_flag("  ") is None
    assert _parse_models_flag("openai/*:high, anthropic/claude ") == [
        "openai/*",
        "anthropic/claude",
    ]
    # A colon inside a model id (e.g. Bedrock ...v1:0) is NOT a thinking-level
    # suffix and must be preserved; only a trailing known level is stripped.
    assert _parse_models_flag(
        "amazon-bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
    ) == ["amazon-bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"]
    assert _parse_models_flag(
        "amazon-bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0:high"
    ) == ["amazon-bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"]
