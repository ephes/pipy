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
    reference_pi_session,
    read_hook_json,
    run_wrapped_agent,
    start_auto_capture,
    stop_auto_capture,
)
from pipy_session.catalog import (
    format_archive_verification,
    format_session_reflection,
    format_session_inspection,
    format_session_search_results,
    format_session_table,
    inspect_finalized_session,
    list_finalized_sessions,
    reflect_on_finalized_sessions,
    search_finalized_sessions,
    verify_session_archive,
)
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
    _add_root_option(init_parser)
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
    _add_root_option(append_parser)
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
    _add_root_option(finalize_parser)
    finalize_parser.add_argument("active", help="Active session path, filename, or stem.")
    finalize_summary = finalize_parser.add_mutually_exclusive_group()
    finalize_summary.add_argument("--summary-file", type=Path, help="Markdown summary file to finalize.")
    finalize_summary.add_argument("--summary", help="Markdown summary text to finalize.")

    list_parser = subparsers.add_parser("list", help="List finalized session records.")
    _add_root_option(list_parser)
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a tab-separated table.",
    )

    search_parser = subparsers.add_parser("search", help="Search finalized session records.")
    _add_root_option(search_parser)
    search_parser.add_argument("query", help="Case-insensitive search query.")
    search_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a tab-separated table.",
    )

    inspect_parser = subparsers.add_parser("inspect", help="Inspect one finalized session record.")
    _add_root_option(inspect_parser)
    inspect_parser.add_argument("record", help="Finalized record path, basename, or stem.")
    inspect_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of labeled text.",
    )

    verify_parser = subparsers.add_parser("verify", help="Verify finalized session archive health.")
    _add_root_option(verify_parser)
    verify_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a tab-separated report.",
    )

    reflect_parser = subparsers.add_parser("reflect", help="Summarize learnings from finalized records.")
    _add_root_option(reflect_parser)
    reflect_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a Markdown report.",
    )

    workflow_parser = subparsers.add_parser(
        "workflow",
        help="Append summary-safe workflow learning events to an active session.",
    )
    _add_root_option(workflow_parser)
    workflow_subparsers = workflow_parser.add_subparsers(dest="workflow_command", required=True)

    workflow_role = workflow_subparsers.add_parser(
        "role",
        help="Record which agent/model filled a workflow role.",
    )
    workflow_role.add_argument("active", help="Active session path, filename, or stem.")
    workflow_role.add_argument("--role", required=True, help="Role, for example implementer or reviewer.")
    workflow_role.add_argument("--agent", required=True, help="Agent, for example codex or claude.")
    workflow_role.add_argument("--model", help="Model identifier, for example claude-opus.")
    workflow_role.add_argument("--phase", help="Workflow phase, for example implementation or review.")
    workflow_role.add_argument("--summary", help="Optional short extra note.")

    workflow_subagent = workflow_subparsers.add_parser(
        "subagent",
        help="Record privacy-safe subagent usage.",
    )
    workflow_subagent.add_argument("active", help="Active session path, filename, or stem.")
    workflow_subagent.add_argument("--role", required=True, help="Subagent role, for example explorer.")
    workflow_subagent.add_argument("--agent", help="Subagent agent, for example codex.")
    workflow_subagent.add_argument("--model", help="Subagent model identifier.")
    workflow_subagent.add_argument("--task-kind", help="Task kind, for example codebase-review.")
    workflow_subagent.add_argument("--outcome", help="Outcome, for example findings-used.")
    workflow_subagent.add_argument("--summary", help="Optional short extra note.")

    workflow_review = workflow_subparsers.add_parser(
        "review-outcome",
        help="Record review findings and closure counts.",
    )
    workflow_review.add_argument("active", help="Active session path, filename, or stem.")
    workflow_review.add_argument("--implementer-agent", help="Implementer agent.")
    workflow_review.add_argument("--implementer-model", help="Implementer model identifier.")
    workflow_review.add_argument("--reviewer-agent", help="Reviewer agent.")
    workflow_review.add_argument("--reviewer-model", help="Reviewer model identifier.")
    workflow_review.add_argument("--high", type=int, default=0, help="High-severity finding count.")
    workflow_review.add_argument("--medium", type=int, default=0, help="Medium-severity finding count.")
    workflow_review.add_argument("--low", type=int, default=0, help="Low-severity or polish finding count.")
    workflow_review.add_argument("--accepted", type=int, default=0, help="Accepted finding count.")
    workflow_review.add_argument("--fixed", type=int, default=0, help="Fixed finding count.")
    workflow_review.add_argument("--rejected", type=int, default=0, help="Rejected finding count.")
    workflow_review.add_argument("--deferred", type=int, default=0, help="Deferred finding count.")
    workflow_review.add_argument("--summary", help="Optional short extra note.")

    workflow_evaluation = workflow_subparsers.add_parser(
        "evaluation",
        help="Record a human or agent judgment about a workflow pattern.",
    )
    workflow_evaluation.add_argument("active", help="Active session path, filename, or stem.")
    workflow_evaluation.add_argument("--pattern", required=True, help="Pattern being evaluated.")
    workflow_evaluation.add_argument(
        "--confidence",
        choices=("low", "medium", "high"),
        help="Confidence in the evaluation.",
    )
    workflow_evaluation.add_argument(
        "--recommendation",
        help="Recommendation, for example keep, switch, or compare.",
    )
    workflow_evaluation.add_argument("--summary", required=True, help="Short evaluation summary.")

    auto_parser = subparsers.add_parser("auto", help="Scriptable automatic-capture adapter commands.")
    _add_root_option(auto_parser)
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

    auto_reference_pi = auto_subparsers.add_parser(
        "reference-pi",
        help="Create a partial pipy record that references a Pi-native session file.",
    )
    auto_reference_pi.add_argument("pi_session_path", type=Path, help="Pi-native session file path.")
    auto_reference_pi.add_argument("--slug", help="Short topic slug for the filename.")
    auto_reference_pi.add_argument(
        "--summary",
        help="Optional Markdown summary to include after the reference notice.",
    )
    auto_reference_pi.add_argument("--machine", help="Machine name override, mainly for tests.")

    auto_hook = auto_subparsers.add_parser("hook", help="Handle a platform hook JSON payload from stdin.")
    auto_hook_subparsers = auto_hook.add_subparsers(dest="platform", required=True)
    claude_hook = auto_hook_subparsers.add_parser("claude", help="Handle Claude Code hook JSON from stdin.")
    claude_hook.add_argument("--machine", help="Machine name override, mainly for tests.")

    wrap_parser = subparsers.add_parser("wrap", help="Run an agent command with partial lifecycle capture.")
    _add_root_option(wrap_parser)
    wrap_parser.add_argument("--agent", required=True, help="Agent name, for example codex or pi.")
    wrap_parser.add_argument("--slug", required=True, help="Short topic slug for the filename.")
    wrap_parser.add_argument("--goal", help="Optional session goal.")
    wrap_parser.add_argument("wrapped_command", nargs=argparse.REMAINDER, help="Command to run after --.")

    return parser


def _add_root_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        type=Path,
        default=argparse.SUPPRESS,
        help="Session root. Defaults to PIPY_SESSION_DIR or ~/.local/state/pipy/sessions.",
    )


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

        if args.command == "search":
            search_results = search_finalized_sessions(args.query, root=args.root)
            if args.json:
                print(json.dumps([result.to_dict() for result in search_results], sort_keys=True))
            else:
                print(format_session_search_results(search_results))
            return 0

        if args.command == "inspect":
            inspection = inspect_finalized_session(args.record, root=args.root)
            if args.json:
                print(json.dumps(inspection.to_dict(), sort_keys=True))
            else:
                print(format_session_inspection(inspection))
            return 0

        if args.command == "verify":
            verification = verify_session_archive(root=args.root)
            if args.json:
                print(json.dumps(verification.to_dict(), sort_keys=True))
            else:
                print(format_archive_verification(verification))
            return 0

        if args.command == "reflect":
            reflection = reflect_on_finalized_sessions(root=args.root)
            if args.json:
                print(json.dumps(reflection.to_dict(), sort_keys=True))
            else:
                print(format_session_reflection(reflection))
            return 0

        if args.command == "workflow":
            path = _append_workflow_event(args)
            print(path)
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
                prune_results = prune_auto_capture_state(root=args.root, dry_run=args.dry_run)
                action = "would-remove" if args.dry_run else "removed"
                for prune_result in prune_results:
                    print(f"{action}\t{prune_result.path}\t{prune_result.reason}")
                print(f"summary\t{action}\t{len(prune_results)}")
                return 0

            if args.auto_command == "reference-pi":
                record = reference_pi_session(
                    args.pi_session_path,
                    root=args.root,
                    slug=args.slug,
                    summary=args.summary,
                    machine=args.machine,
                )
                print(record.jsonl_path)
                if record.markdown_path is not None:
                    print(record.markdown_path)
                return 0

            if args.auto_command == "hook" and args.platform == "claude":
                payload = read_hook_json(sys.stdin.read())
                hook_result = handle_claude_hook(payload, root=args.root, machine=args.machine)
                if hook_result.message:
                    print(f"pipy-session: {hook_result.message}", file=sys.stderr)
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


def _append_workflow_event(args: argparse.Namespace) -> Path:
    if args.workflow_command == "role":
        fields = _compact_fields(
            {
                "role": args.role,
                "agent": args.agent,
                "model": args.model,
                "phase": args.phase,
                "note": args.summary,
            }
        )
        summary = _summary_sentence(
            "Workflow role",
            fields,
            field_order=("role", "agent", "model", "phase", "note"),
        )
        return append_event(
            args.active,
            root=args.root,
            event_type="workflow.role",
            summary=summary,
            agent=args.agent,
            payload=fields,
        )

    if args.workflow_command == "subagent":
        fields = _compact_fields(
            {
                "role": args.role,
                "agent": args.agent,
                "model": args.model,
                "task_kind": args.task_kind,
                "outcome": args.outcome,
                "note": args.summary,
            }
        )
        summary = _summary_sentence(
            "Subagent used",
            fields,
            field_order=("role", "agent", "model", "task_kind", "outcome", "note"),
        )
        return append_event(
            args.active,
            root=args.root,
            event_type="subagent.used",
            summary=summary,
            agent=args.agent,
            payload=fields,
        )

    if args.workflow_command == "review-outcome":
        _validate_non_negative_counts(
            {
                "high": args.high,
                "medium": args.medium,
                "low": args.low,
                "accepted": args.accepted,
                "fixed": args.fixed,
                "rejected": args.rejected,
                "deferred": args.deferred,
            }
        )
        fields = _compact_fields(
            {
                "implementer_agent": args.implementer_agent,
                "implementer_model": args.implementer_model,
                "reviewer_agent": args.reviewer_agent,
                "reviewer_model": args.reviewer_model,
                "high": args.high,
                "medium": args.medium,
                "low": args.low,
                "accepted": args.accepted,
                "fixed": args.fixed,
                "rejected": args.rejected,
                "deferred": args.deferred,
                "note": args.summary,
            }
        )
        summary = _summary_sentence(
            "Review outcome",
            fields,
            field_order=(
                "implementer_agent",
                "implementer_model",
                "reviewer_agent",
                "reviewer_model",
                "high",
                "medium",
                "low",
                "accepted",
                "fixed",
                "rejected",
                "deferred",
                "note",
            ),
        )
        return append_event(
            args.active,
            root=args.root,
            event_type="review.outcome",
            summary=summary,
            agent=args.reviewer_agent,
            payload=fields,
        )

    if args.workflow_command == "evaluation":
        fields = _compact_fields(
            {
                "pattern": args.pattern,
                "confidence": args.confidence,
                "recommendation": args.recommendation,
                "note": args.summary,
            }
        )
        summary = _summary_sentence(
            "Workflow evaluation",
            fields,
            field_order=("pattern", "confidence", "recommendation", "note"),
        )
        return append_event(
            args.active,
            root=args.root,
            event_type="workflow.evaluation",
            summary=summary,
            payload=fields,
        )

    raise ValueError(f"unknown workflow command: {args.workflow_command}")


def _compact_fields(fields: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, str):
            cleaned = " ".join(value.split())
            if not cleaned:
                continue
            compact[key] = cleaned
            continue
        compact[key] = value
    return compact


def _summary_sentence(prefix: str, fields: dict[str, Any], *, field_order: tuple[str, ...]) -> str:
    details = ", ".join(
        f"{field}={fields[field]}" for field in field_order if field in fields
    )
    return f"{prefix}: {details}."


def _validate_non_negative_counts(counts: dict[str, int]) -> None:
    negative = [name for name, value in counts.items() if value < 0]
    if negative:
        formatted = ", ".join(negative)
        raise ValueError(f"workflow count fields must be non-negative: {formatted}")


if __name__ == "__main__":
    raise SystemExit(main())
