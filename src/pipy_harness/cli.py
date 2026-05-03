"""Command-line interface for the pipy product harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipy_harness.adapters import SubprocessAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import RunRequest
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
            command = _native_command(args.native_command)
            request = RunRequest(
                agent=args.agent,
                slug=args.slug,
                command=command,
                cwd=args.cwd,
                goal=args.goal,
                root=args.root,
                capture_policy=CapturePolicy(record_file_paths=args.record_files),
            )
            result = HarnessRunner(adapter=SubprocessAdapter()).run(request)
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


def _native_command(command: list[str]) -> list[str]:
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise ValueError("run requires a command after --")
    return command


if __name__ == "__main__":
    raise SystemExit(main())
