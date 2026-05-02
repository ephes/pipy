"""Command-line interface for the pipy session recorder."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pipy_session.auto_capture import (
    append_auto_event,
    handle_claude_hook,
    prune_auto_capture_state,
    read_hook_json,
    run_wrapped_agent,
    start_auto_capture,
    stop_auto_capture,
)
from pipy_session.catalog import format_session_table, list_finalized_sessions
from pipy_session.recorder import append_event, finalize_session, init_session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipy-session",
        description="Create and finalize pipy coding-agent session records.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        help="Session root. Defaults to PIPY_SESSION_DIR or ~/.local/state/pipy/sessions.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create an active session JSONL file.")
    init_parser.add_argument("--agent", required=True, help="Agent name, for example codex.")
    init_parser.add_argument("--slug", required=True, help="Short topic slug for the filename.")
    init_parser.add_argument("--goal", help="Optional session goal for the session.started event.")
    init_parser.add_argument(
        "--partial",
        action="store_true",
        help="Mark this record as a partial reconstruction.",
    )
    init_parser.add_argument("--machine", help="Machine name override, mainly for tests.")

    append_parser = subparsers.add_parser("append", help="Append one JSONL event to an active session.")
    append_parser.add_argument("active", help="Active session path, filename, or stem.")
    append_parser.add_argument("--type", dest="event_type", help="Event type, for example decision.recorded.")
    append_parser.add_argument("--summary", help="Concise human-readable event summary.")
    append_parser.add_argument("--agent", help="Agent name to include on the event.")
    append_parser.add_argument("--payload-json", help="JSON object to store as the event payload.")
    append_parser.add_argument(
        "--event-json",
        help="Complete JSON object to append. Missing timestamp is filled automatically.",
    )

    finalize_parser = subparsers.add_parser("finalize", help="Move an active session to the archive.")
    finalize_parser.add_argument("active", help="Active session path, filename, or stem.")
    finalize_summary = finalize_parser.add_mutually_exclusive_group()
    finalize_summary.add_argument("--summary-file", type=Path, help="Markdown summary file to finalize.")
    finalize_summary.add_argument("--summary", help="Markdown summary text to finalize.")

    list_parser = subparsers.add_parser("list", help="List finalized session records.")
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a tab-separated table.",
    )

    auto_parser = subparsers.add_parser("auto", help="Scriptable automatic-capture adapter commands.")
    auto_subparsers = auto_parser.add_subparsers(dest="auto_command", required=True)

    auto_start = auto_subparsers.add_parser("start", help="Start an automatic partial capture.")
    auto_start.add_argument("--agent", required=True, help="Agent name, for example claude.")
    auto_start.add_argument("--slug", required=True, help="Short topic slug for the filename.")
    auto_start.add_argument("--session-id", help="Platform session id for later event/stop commands.")
    auto_start.add_argument("--goal", help="Optional session goal.")
    auto_start.add_argument("--metadata-json", help="Metadata JSON object to store on the start event.")
    auto_start.add_argument(
        "--complete",
        action="store_true",
        help="Mark capture complete. Use only for adapters that truly capture full transcripts.",
    )
    auto_start.add_argument("--machine", help="Machine name override, mainly for tests.")

    auto_event = auto_subparsers.add_parser("event", help="Append a conservative automatic-capture event.")
    auto_event.add_argument("--active", help="Active session path, filename, or stem.")
    auto_event.add_argument("--agent", help="Agent name for state lookup and event metadata.")
    auto_event.add_argument("--session-id", help="Platform session id for state lookup.")
    auto_event.add_argument("--type", dest="event_type", required=True, help="Event type to append.")
    auto_event.add_argument("--summary", help="Concise human-readable event summary.")
    auto_event.add_argument("--metadata-json", help="Metadata JSON object to store as payload.")

    auto_stop = auto_subparsers.add_parser("stop", help="Finalize an automatic capture.")
    auto_stop.add_argument("--active", help="Active session path, filename, or stem.")
    auto_stop.add_argument("--agent", help="Agent name for state lookup and event metadata.")
    auto_stop.add_argument("--session-id", help="Platform session id for state lookup.")
    auto_stop.add_argument("--summary", help="Markdown summary text to finalize.")
    auto_stop.add_argument("--metadata-json", help="Metadata JSON object to store on the end event.")

    auto_prune = auto_subparsers.add_parser("prune", help="Remove stale automatic-capture state files.")
    auto_prune.add_argument(
        "--dry-run",
        action="store_true",
        help="Report stale state files without removing them.",
    )

    auto_hook = auto_subparsers.add_parser("hook", help="Handle a platform hook JSON payload from stdin.")
    auto_hook_subparsers = auto_hook.add_subparsers(dest="platform", required=True)
    claude_hook = auto_hook_subparsers.add_parser("claude", help="Handle Claude Code hook JSON from stdin.")
    claude_hook.add_argument("--machine", help="Machine name override, mainly for tests.")

    wrap_parser = subparsers.add_parser("wrap", help="Run an agent command with partial lifecycle capture.")
    wrap_parser.add_argument("--agent", required=True, help="Agent name, for example codex or pi.")
    wrap_parser.add_argument("--slug", required=True, help="Short topic slug for the filename.")
    wrap_parser.add_argument("--goal", help="Optional session goal.")
    wrap_parser.add_argument("wrapped_command", nargs=argparse.REMAINDER, help="Command to run after --.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            path = init_session(
                agent=args.agent,
                slug=args.slug,
                root=args.root,
                goal=args.goal,
                partial=args.partial,
                machine=args.machine,
            )
            print(path)
            return 0

        if args.command == "append":
            payload = _parse_optional_json_object(args.payload_json, "--payload-json")
            event = _parse_optional_json_object(args.event_json, "--event-json")
            path = append_event(
                args.active,
                root=args.root,
                event_type=args.event_type,
                summary=args.summary,
                agent=args.agent,
                payload=payload,
                event=event,
            )
            print(path)
            return 0

        if args.command == "finalize":
            record = finalize_session(
                args.active,
                root=args.root,
                summary_file=args.summary_file,
                summary_text=args.summary,
            )
            print(record.jsonl_path)
            if record.markdown_path is not None:
                print(record.markdown_path)
            return 0

        if args.command == "list":
            records = list_finalized_sessions(root=args.root)
            if args.json:
                print(json.dumps([record.to_dict() for record in records], sort_keys=True))
            else:
                print(format_session_table(records))
            return 0

        if args.command == "auto":
            if args.auto_command == "start":
                metadata = _parse_optional_json_object(args.metadata_json, "--metadata-json")
                state = start_auto_capture(
                    agent=args.agent,
                    slug=args.slug,
                    platform_session_id=args.session_id,
                    root=args.root,
                    goal=args.goal,
                    metadata=metadata,
                    partial=not args.complete,
                    machine=args.machine,
                )
                print(state.active_path)
                return 0

            if args.auto_command == "event":
                metadata = _parse_optional_json_object(args.metadata_json, "--metadata-json")
                path = append_auto_event(
                    root=args.root,
                    active=args.active,
                    agent=args.agent,
                    platform_session_id=args.session_id,
                    event_type=args.event_type,
                    summary=args.summary,
                    metadata=metadata,
                )
                print(path)
                return 0

            if args.auto_command == "stop":
                metadata = _parse_optional_json_object(args.metadata_json, "--metadata-json")
                record = stop_auto_capture(
                    root=args.root,
                    active=args.active,
                    agent=args.agent,
                    platform_session_id=args.session_id,
                    summary=args.summary,
                    metadata=metadata,
                )
                print(record.jsonl_path)
                if record.markdown_path is not None:
                    print(record.markdown_path)
                return 0

            if args.auto_command == "prune":
                results = prune_auto_capture_state(root=args.root, dry_run=args.dry_run)
                action = "would-remove" if args.dry_run else "removed"
                for result in results:
                    print(f"{action}\t{result.path}\t{result.reason}")
                print(f"summary\t{action}\t{len(results)}")
                return 0

            if args.auto_command == "hook" and args.platform == "claude":
                payload = read_hook_json(sys.stdin.read())
                result = handle_claude_hook(payload, root=args.root, machine=args.machine)
                if result.message:
                    print(f"pipy-session: {result.message}", file=sys.stderr)
                return 0

        if args.command == "wrap":
            command = _wrapped_command(args.wrapped_command)
            return run_wrapped_agent(
                agent=args.agent,
                slug=args.slug,
                command=command,
                root=args.root,
                goal=args.goal,
            )
    except ValueError as exc:
        print(f"pipy-session: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"pipy-session: {exc}", file=sys.stderr)
        return 1

    parser.error("unknown command")
    return 2


def _parse_optional_json_object(value: str | None, option_name: str) -> dict[str, Any] | None:
    if value is None:
        return None

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{option_name} must be valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError(f"{option_name} must be a JSON object")
    return parsed


def _wrapped_command(command: list[str]) -> list[str]:
    if command and command[0] == "--":
        return command[1:]
    return command


if __name__ == "__main__":
    raise SystemExit(main())
