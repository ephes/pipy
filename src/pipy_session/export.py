"""Metadata-first export for finalized pipy session records.

The export keeps the same archive-privacy contract as the rest of
``pipy_session``: by default it surfaces only the safe metadata that
``pipy-session inspect`` would expose, plus a curated allowlist of per-event
metadata fields. Raw prompt text, model output, tool payloads, file contents,
diffs, secrets, and any other sensitive transcript content are never included
in the default export.

The opt-in ``include_transcript`` flag additionally attaches raw transcript
events from the opt-in sidecar at
``~/.local/state/pipy/transcripts/<stem>.jsonl`` (or
``$PIPY_TRANSCRIPT_DIR/<stem>.jsonl``). The sidecar is only consulted when the
caller explicitly opts in, and a missing sidecar raises ``FileNotFoundError``
rather than fabricating empty transcript content.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from pipy_session.catalog import resolve_finalized_record
from pipy_session.recorder import FILENAME_RE, resolve_session_root

SCHEMA_NAME = "pipy.session-export"
SCHEMA_VERSION = 1

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


def export_session(
    record_path_or_stem: str,
    *,
    include_transcript: bool = False,
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
    include_transcript:
        When true, also include raw transcript events from the opt-in sidecar
        at ``~/.local/state/pipy/transcripts/<stem>.jsonl`` (or
        ``$PIPY_TRANSCRIPT_DIR/<stem>.jsonl``). Raises ``FileNotFoundError``
        if the sidecar is missing.
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
        "transcript_events": None,
    }

    if include_transcript:
        sidecar_path = _transcript_sidecar_path(record_path.stem)
        export["transcript_path_label"] = str(sidecar_path)
        export["transcript_events"] = _read_transcript_sidecar(sidecar_path)

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
    }
    return metadata


def _transcript_sidecar_path(stem: str) -> Path:
    env_dir = os.environ.get("PIPY_TRANSCRIPT_DIR")
    if env_dir:
        base = Path(env_dir).expanduser()
    else:
        base = Path.home() / ".local" / "state" / "pipy" / "transcripts"
    return base / f"{stem}.jsonl"


def _read_transcript_sidecar(sidecar_path: Path) -> list[dict[str, Any]]:
    if not sidecar_path.exists():
        raise FileNotFoundError(
            f"transcript sidecar not found: {sidecar_path}"
        )
    if sidecar_path.is_symlink():
        raise ValueError(
            f"refusing to read symbolic-link transcript sidecar: {sidecar_path}"
        )
    if not sidecar_path.is_file():
        raise ValueError(
            f"transcript sidecar is not a regular file: {sidecar_path}"
        )

    events: list[dict[str, Any]] = []
    with sidecar_path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"malformed transcript line {line_number}: {sidecar_path}"
                ) from exc
            if not isinstance(parsed, dict):
                raise ValueError(
                    f"malformed transcript line {line_number}: {sidecar_path}"
                )
            events.append(parsed)
    return events


def export_session_from_args(
    record: str,
    *,
    include_transcript: bool,
    root: str | Path | None,
) -> dict[str, object]:
    """CLI helper: resolve the session root and call :func:`export_session`."""

    session_root = resolve_session_root(root)
    return export_session(
        record,
        include_transcript=include_transcript,
        session_root=session_root,
    )
