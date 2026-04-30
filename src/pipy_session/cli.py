"""Command-line interface for the pipy session recorder."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

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


if __name__ == "__main__":
    raise SystemExit(main())
