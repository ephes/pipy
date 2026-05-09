"""Command-line interface for the pipy product harness."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.adapters import PipyNativeAdapter, PipyNativeReplAdapter, SubprocessAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import RunRequest, RunResult
from pipy_harness.native import (
    OpenAICodexAuthManager,
    OpenAICodexProviderError,
    OpenAICodexResponsesProvider,
    FakeNativeProvider,
    OpenAIResponsesProvider,
    OpenRouterChatCompletionsProvider,
)
from pipy_harness.runner import HarnessRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipy",
        description="Run coding-agent tasks through the pipy harness.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth_parser = subparsers.add_parser("auth", help="Manage provider authentication.")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_provider", required=True)
    openai_codex_auth = auth_subparsers.add_parser(
        "openai-codex",
        help="Manage OpenAI Codex subscription OAuth credentials.",
    )
    openai_codex_auth_subparsers = openai_codex_auth.add_subparsers(dest="auth_action", required=True)
    openai_codex_login = openai_codex_auth_subparsers.add_parser(
        "login",
        help="Run OpenAI Codex OAuth login and store pipy-owned credentials.",
    )
    openai_codex_login.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the OAuth URL without attempting to open a browser.",
    )

    run_parser = subparsers.add_parser("run", help="Run one agent command with partial capture.")
    run_parser.add_argument("--agent", required=True, help="Logical agent name, for example codex.")
    run_parser.add_argument("--slug", required=True, help="Short run label for the session filename.")
    run_parser.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="Working directory for the child process. Defaults to the current directory.",
    )
    run_parser.add_argument("--goal", help="Optional short goal for the run record.")
    run_parser.add_argument(
        "--native-provider",
        choices=["fake", "openai", "openai-codex", "openrouter"],
        help=(
            "Native provider for --agent pipy-native. Defaults to the deterministic fake provider."
        ),
    )
    run_parser.add_argument(
        "--native-model",
        help=(
            "Native model identifier for --agent pipy-native. Defaults to fake-native-bootstrap "
            "for --native-provider fake and is required for real native providers."
        ),
    )
    run_parser.add_argument(
        "--native-output",
        choices=["json"],
        help="Native stdout mode for --agent pipy-native. Only json is supported.",
    )
    run_parser.add_argument(
        "--record-files",
        action="store_true",
        help="Record changed git file paths only, not diffs or contents.",
    )
    run_parser.add_argument(
        "--root",
        type=Path,
        help="Session root. Defaults to PIPY_SESSION_DIR or ~/.local/state/pipy/sessions.",
    )
    run_parser.add_argument("native_command", nargs=argparse.REMAINDER, help="Command to run after --.")

    repl_parser = subparsers.add_parser(
        "repl",
        help="Run the bounded pipy-native REPL.",
    )
    repl_parser.add_argument("--agent", required=True, help="Logical agent name. Only pipy-native is supported.")
    repl_parser.add_argument("--slug", required=True, help="Short run label for the session filename.")
    repl_parser.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="Working directory for the native provider. Defaults to the current directory.",
    )
    repl_parser.add_argument(
        "--goal",
        help="Optional short goal for the REPL run record. Conversation turns are not archived.",
    )
    repl_parser.add_argument(
        "--native-provider",
        choices=["fake", "openai", "openai-codex", "openrouter"],
        help=(
            "Native provider for --agent pipy-native. Defaults to the deterministic fake provider."
        ),
    )
    repl_parser.add_argument(
        "--native-model",
        help=(
            "Native model identifier for --agent pipy-native. Defaults to fake-native-bootstrap "
            "for --native-provider fake and is required for real native providers."
        ),
    )
    repl_parser.add_argument(
        "--root",
        type=Path,
        help="Session root. Defaults to PIPY_SESSION_DIR or ~/.local/state/pipy/sessions.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "auth":
            if args.auth_provider == "openai-codex" and args.auth_action == "login":
                OpenAICodexAuthManager().login_interactive(
                    input_stream=sys.stdin,
                    output_stream=sys.stderr,
                    open_browser=not args.no_browser,
                )
                print(
                    "pipy: openai-codex OAuth login stored.",
                    file=sys.stderr,
                )
                return 0
        if args.command == "run":
            _validate_native_output(args.agent, args.native_output)
            command = _native_command(args.native_command, agent=args.agent)
            if args.agent == "pipy-native" and not args.goal:
                raise ValueError("pipy-native runs require --goal")
            adapter = _adapter_for(args.agent, args.native_provider, args.native_model)
            request = RunRequest(
                agent=args.agent,
                slug=args.slug,
                command=command,
                cwd=args.cwd,
                goal=args.goal,
                root=args.root,
                capture_policy=CapturePolicy(record_file_paths=args.record_files),
                native_provider=args.native_provider,
                native_model=args.native_model,
                native_output=args.native_output,
            )
            result = HarnessRunner(adapter=adapter).run(request)
            if result.error_type is not None:
                detail = f": {result.error_message}" if result.error_message else ""
                print(
                    f"pipy: run ended with {result.error_type}{detail}; session finalized at "
                    f"{result.record.jsonl_path}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"pipy: session finalized at {result.record.jsonl_path}",
                    file=sys.stderr,
                )
            if args.native_output == "json":
                print(json.dumps(_native_json_output(request, result), sort_keys=True))
            return result.exit_code
        if args.command == "repl":
            if args.agent != "pipy-native":
                raise ValueError("pipy repl currently requires --agent pipy-native")
            repl_adapter = _repl_adapter_for(args.native_provider, args.native_model)
            request = RunRequest(
                agent=args.agent,
                slug=args.slug,
                command=[],
                cwd=args.cwd,
                goal=args.goal or "Native REPL",
                root=args.root,
                capture_policy=CapturePolicy(),
                native_provider=args.native_provider,
                native_model=args.native_model,
            )
            result = HarnessRunner(adapter=repl_adapter).run(request)
            if result.error_type is not None:
                detail = f": {result.error_message}" if result.error_message else ""
                print(
                    f"pipy: repl ended with {result.error_type}{detail}; session finalized at "
                    f"{result.record.jsonl_path}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"pipy: session finalized at {result.record.jsonl_path}",
                    file=sys.stderr,
                )
            return result.exit_code
    except ValueError as exc:
        print(f"pipy: {exc}", file=sys.stderr)
        return 2
    except OpenAICodexProviderError as exc:
        print(
            f"pipy: openai-codex auth failed with {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    except OSError as exc:
        print(f"pipy: {exc}", file=sys.stderr)
        return 1

    parser.error("unknown command")
    return 2


def _validate_native_output(agent: str, native_output: str | None) -> None:
    if native_output is not None and agent != "pipy-native":
        raise ValueError("--native-output requires --agent pipy-native")


def _native_command(command: list[str], *, agent: str) -> list[str]:
    if command and command[0] == "--":
        command = command[1:]
    if agent == "pipy-native":
        if command:
            raise ValueError("pipy-native runs do not accept a command after --")
        return []
    if not command:
        raise ValueError("run requires a command after --")
    return command


def _native_json_output(request: RunRequest, result: RunResult) -> dict[str, Any]:
    metadata = result.metadata or {}
    output: dict[str, Any] = {
        "schema": "pipy.native_output",
        "schema_version": 1,
        "run_id": result.run_id,
        "status": result.status.value,
        "exit_code": result.exit_code,
        "agent": request.agent,
        "adapter": _metadata_text(metadata, "adapter") or "pipy-native",
        "record": {
            "jsonl_path": str(result.record.jsonl_path),
            "markdown_path": str(result.record.markdown_path)
            if result.record.markdown_path is not None
            else None,
        },
        "capture": {
            "partial": True,
            "stdout_stored": request.capture_policy.record_stdout,
            "stderr_stored": request.capture_policy.record_stderr,
            "prompt_stored": False,
            "model_output_stored": False,
            "tool_payloads_stored": False,
            "raw_transcript_imported": request.capture_policy.import_raw_transcript,
        },
    }
    provider = _metadata_text(metadata, "provider") or request.native_provider or "fake"
    model_id = (
        _metadata_text(metadata, "model_id")
        or request.native_model
        or ("fake-native-bootstrap" if provider == "fake" else None)
    )
    if provider is not None:
        output["provider"] = provider
    if model_id is not None:
        output["model_id"] = model_id
    if result.duration_seconds is not None:
        output["duration_seconds"] = result.duration_seconds
    usage = metadata.get("usage")
    if isinstance(usage, Mapping) and usage:
        output["usage"] = dict(usage)
    return output


def _metadata_text(metadata: Mapping[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) and value else None


def _adapter_for(
    agent: str,
    native_provider: str | None,
    native_model: str | None,
) -> SubprocessAdapter | PipyNativeAdapter:
    if agent == "pipy-native":
        if native_provider not in (None, "fake", "openai", "openai-codex", "openrouter"):
            raise ValueError(f"unsupported native provider: {native_provider}")
        if native_provider == "openai":
            if not native_model:
                raise ValueError("--native-model is required for --native-provider openai")
            return PipyNativeAdapter(provider=OpenAIResponsesProvider(model_id=native_model))
        if native_provider == "openai-codex":
            if not native_model:
                raise ValueError("--native-model is required for --native-provider openai-codex")
            return PipyNativeAdapter(provider=OpenAICodexResponsesProvider(model_id=native_model))
        if native_provider == "openrouter":
            if not native_model:
                raise ValueError("--native-model is required for --native-provider openrouter")
            return PipyNativeAdapter(
                provider=OpenRouterChatCompletionsProvider(model_id=native_model)
            )
        return PipyNativeAdapter(
            provider=FakeNativeProvider(model_id=native_model or "fake-native-bootstrap")
        )
    if native_provider is not None or native_model is not None:
        raise ValueError("--native-provider and --native-model require --agent pipy-native")
    return SubprocessAdapter()


def _repl_adapter_for(
    native_provider: str | None,
    native_model: str | None,
) -> PipyNativeReplAdapter:
    if native_provider not in (None, "fake", "openai", "openai-codex", "openrouter"):
        raise ValueError(f"unsupported native provider: {native_provider}")
    if native_provider == "openai":
        if not native_model:
            raise ValueError("--native-model is required for --native-provider openai")
        return PipyNativeReplAdapter(provider=OpenAIResponsesProvider(model_id=native_model))
    if native_provider == "openai-codex":
        if not native_model:
            raise ValueError("--native-model is required for --native-provider openai-codex")
        return PipyNativeReplAdapter(provider=OpenAICodexResponsesProvider(model_id=native_model))
    if native_provider == "openrouter":
        if not native_model:
            raise ValueError("--native-model is required for --native-provider openrouter")
        return PipyNativeReplAdapter(
            provider=OpenRouterChatCompletionsProvider(model_id=native_model)
        )
    return PipyNativeReplAdapter(
        provider=FakeNativeProvider(model_id=native_model or "fake-native-bootstrap")
    )


if __name__ == "__main__":
    raise SystemExit(main())
