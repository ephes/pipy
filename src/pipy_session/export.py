"""Metadata-first export for finalized pipy session records.

The export keeps the same archive-privacy contract as the rest of
``pipy_session``: it surfaces only the safe metadata that
``pipy-session inspect`` would expose, plus a curated allowlist of per-event
metadata fields. Raw prompt text, model output, tool payloads, file contents,
diffs, secrets, and any other sensitive transcript content are never included
in the export.

Schema history:
- v1: included opt-in ``transcript_events`` / ``transcript_path_label`` fields
  sourced from the ``--archive-transcript`` sidecar.
- v2: the transcript sidecar (writer and reader) was removed; the
  ``transcript_events`` and ``transcript_path_label`` fields are gone. The
  native session tree is the transcript.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from pipy_session.catalog import resolve_finalized_record
from pipy_session.recorder import FILENAME_RE, resolve_session_root

SCHEMA_NAME = "pipy.session-export"
SCHEMA_VERSION = 2

# Allowlisted top-level event keys carried into the metadata-only export. Any
# event key not listed here is dropped, which keeps payload bodies, raw model
# output, raw tool arguments, command stdout/stderr, diffs, file contents, and
# similar sensitive fields out of the export by construction.
SAFE_EVENT_KEYS: tuple[str, ...] = (
    "type",
    "timestamp",
    "summary",
    "agent",
    "machine",
    "project",
    "slug",
    "partial",
    "sequence",
)

# Allowlisted keys read from the ``resume`` object on a child session's
# ``session.started`` event. Anything else (including any forged payload-shaped
# key) is dropped, keeping export lineage to safe labels only.
SAFE_LINEAGE_KEYS: tuple[str, ...] = (
    "parent_session_id",
    "relationship",
    "branch_label",
    "fork_timestamp",
)


def safe_resume_lineage(first_event: Mapping[str, Any]) -> dict[str, str] | None:
    """Return allowlisted resume/branch lineage labels, or None if absent."""

    resume_field = first_event.get("resume")
    if not isinstance(resume_field, Mapping):
        return None
    # Reuse the catalog's terminal-safe, secret-free, bounded label filter so a
    # forged or foreign record cannot smuggle control bytes or secret-shaped
    # content into the export metadata.
    from pipy_session.catalog import _safe_lineage_label

    safe: dict[str, str] = {}
    for key in SAFE_LINEAGE_KEYS:
        label = _safe_lineage_label(resume_field.get(key))
        if label is not None:
            safe[key] = label
    return safe or None


def export_session(
    record_path_or_stem: str,
    *,
    session_root: Path,
) -> dict[str, object]:
    """Return a structured, metadata-only export of one finalized session.

    Parameters
    ----------
    record_path_or_stem:
        Finalized record basename or stem (as accepted by
        ``pipy-session inspect``). Absolute paths are rejected to keep the
        command on the same name-resolution surface as the other catalog
        commands.
    session_root:
        Resolved session root directory (already passed through
        ``resolve_session_root``). Required for symmetry with the rest of the
        catalog API.
    """

    candidate = Path(record_path_or_stem)
    if candidate.is_absolute():
        raise ValueError(
            "export accepts a basename or stem, not an absolute path: "
            f"{record_path_or_stem}"
        )
    if candidate.parent != Path("."):
        raise ValueError(
            "export accepts a basename or stem, not a path with directories: "
            f"{record_path_or_stem}"
        )

    root_path = Path(session_root)
    try:
        record_path = resolve_finalized_record(record_path_or_stem, root=root_path)
    except FileNotFoundError as exc:
        raise LookupError(str(exc)) from exc

    if record_path.is_symlink():
        raise ValueError(f"refusing to export a symbolic-link record: {record_path}")

    resolved_record = record_path.resolve(strict=True)
    resolved_root = (root_path / "pipy").resolve(strict=True)
    try:
        resolved_record.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"finalized record escapes session archive root: {record_path}"
        ) from exc

    first_event, events = _read_record_events(record_path)
    if first_event.get("type") != "session.started":
        raise ValueError(
            f"first event is not session.started: {record_path}"
        )

    match = FILENAME_RE.match(record_path.name)
    if match is None:
        raise ValueError(f"finalized session filename is malformed: {record_path.name}")

    started_at = str(first_event.get("timestamp") or match.group("stamp"))
    stat_result = record_path.stat()

    record_meta: dict[str, object] = {
        "stem": record_path.stem,
        "basename": record_path.name,
        "path_label": str(record_path.relative_to(root_path)),
        "size_bytes": stat_result.st_size,
        "started_at": started_at,
        "machine": str(first_event.get("machine") or match.group("machine")),
        "agent": str(first_event.get("agent") or match.group("agent")),
        "slug": str(first_event.get("slug") or match.group("slug")),
        "partial": bool(first_event.get("partial", False)),
    }

    markdown_path = record_path.with_suffix(".md")
    markdown_summary: str | None = None
    markdown_path_label: str | None = None
    if markdown_path.is_file() and not markdown_path.is_symlink():
        markdown_summary = markdown_path.read_text(encoding="utf-8")
        markdown_path_label = str(markdown_path.relative_to(root_path))

    safe_events = [_safe_event(event) for event in events]
    metadata = _metadata_block(first_event, safe_events)

    export: dict[str, object] = {
        "schema": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "record": record_meta,
        "metadata": metadata,
        "events": safe_events,
        "markdown_summary": markdown_summary,
        "markdown_path_label": markdown_path_label,
    }

    return export


def _read_record_events(
    record_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    with record_path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"malformed JSONL event at line {line_number}: {record_path}"
                ) from exc
            if not isinstance(parsed, dict):
                raise ValueError(
                    f"malformed JSONL event at line {line_number}: {record_path}"
                )
            events.append(parsed)

    if not events:
        raise ValueError(f"finalized record is empty: {record_path}")
    return events[0], events


def _safe_event(event: Mapping[str, Any]) -> dict[str, object]:
    """Return a metadata-only projection of one event.

    Only keys in :data:`SAFE_EVENT_KEYS` are carried through. Notably,
    ``payload`` is intentionally dropped, so even bespoke per-event
    ``payload`` shapes used inside pipy (which may carry tool intent metadata
    or normalized counters) never leak into the default export.
    """

    safe: dict[str, object] = {}
    for key in SAFE_EVENT_KEYS:
        if key in event:
            safe[key] = event[key]
    if "type" not in safe:
        safe["type"] = event.get("type")
    return safe


def _metadata_block(
    first_event: Mapping[str, Any],
    safe_events: list[dict[str, object]],
) -> dict[str, object]:
    event_type_counts: dict[str, int] = {}
    for safe_event in safe_events:
        event_type_value = safe_event.get("type")
        event_type = str(event_type_value) if event_type_value else "unknown"
        event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1

    metadata: dict[str, object] = {
        "started_at": str(first_event.get("timestamp", "")),
        "agent": str(first_event.get("agent", "")),
        "machine": str(first_event.get("machine", "")),
        "slug": str(first_event.get("slug", "")),
        "project": str(first_event.get("project", "")),
        "partial": bool(first_event.get("partial", False)),
        "goal": first_event.get("goal"),
        "event_count": len(safe_events),
        "event_type_counts": dict(sorted(event_type_counts.items())),
        "resume": safe_resume_lineage(first_event),
        "compaction_event_count": event_type_counts.get(
            "native.session.compacted", 0
        ),
    }
    return metadata


def export_session_from_args(
    record: str,
    *,
    root: str | Path | None,
) -> dict[str, object]:
    """CLI helper: resolve the session root and call :func:`export_session`."""

    session_root = resolve_session_root(root)
    return export_session(
        record,
        session_root=session_root,
    )
