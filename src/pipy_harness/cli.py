"""Command-line interface for the pipy product harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipy_harness.adapters import PipyNativeAdapter, SubprocessAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import RunRequest
from pipy_harness.native import FakeNativeProvider
from pipy_harness.runner import HarnessRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipy",
        description="Run coding-agent tasks through the pipy harness.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

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
        choices=["fake"],
        help=(
            "Native provider for --agent pipy-native. The only bootstrap provider is "
            "the deterministic fake provider."
        ),
    )
    run_parser.add_argument(
        "--native-model",
        help="Native model identifier for --agent pipy-native. Defaults to fake-native-bootstrap.",
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "run":
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
            return result.exit_code
    except ValueError as exc:
        print(f"pipy: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"pipy: {exc}", file=sys.stderr)
        return 1

    parser.error("unknown command")
    return 2


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


def _adapter_for(
    agent: str,
    native_provider: str | None,
    native_model: str | None,
) -> SubprocessAdapter | PipyNativeAdapter:
    if agent == "pipy-native":
        if native_provider not in (None, "fake"):
            raise ValueError(f"unsupported native provider: {native_provider}")
        return PipyNativeAdapter(
            provider=FakeNativeProvider(model_id=native_model or "fake-native-bootstrap")
        )
    if native_provider is not None or native_model is not None:
        raise ValueError("--native-provider and --native-model require --agent pipy-native")
    return SubprocessAdapter()


if __name__ == "__main__":
    raise SystemExit(main())
