"""Read-only catalog helpers for finalized pipy session records."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipy_session.recorder import FILENAME_RE, PROJECT_NAME, resolve_session_root


@dataclass(frozen=True)
class FinalizedSessionListing:
    """Summary of one finalized session record."""

    started: str
    machine: str
    agent: str
    slug: str
    partial: bool
    jsonl_path: Path
    markdown_path: Path | None

    @property
    def capture(self) -> str:
        return "partial" if self.partial else "complete"

    @property
    def has_summary(self) -> bool:
        return self.markdown_path is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "machine": self.machine,
            "agent": self.agent,
            "slug": self.slug,
            "capture": self.capture,
            "partial": self.partial,
            "has_summary": self.has_summary,
            "jsonl_path": str(self.jsonl_path),
            "markdown_path": str(self.markdown_path) if self.markdown_path else None,
        }


@dataclass(frozen=True)
class FinalizedSessionInspection:
    """Read-only inspection details for one finalized session record."""

    listing: FinalizedSessionListing
    event_count: int
    event_types: dict[str, int]
    summary_text: str | None

    @property
    def started(self) -> str:
        return self.listing.started

    @property
    def machine(self) -> str:
        return self.listing.machine

    @property
    def agent(self) -> str:
        return self.listing.agent

    @property
    def slug(self) -> str:
        return self.listing.slug

    @property
    def capture(self) -> str:
        return self.listing.capture

    @property
    def partial(self) -> bool:
        return self.listing.partial

    @property
    def jsonl_path(self) -> Path:
        return self.listing.jsonl_path

    @property
    def markdown_path(self) -> Path | None:
        return self.listing.markdown_path

    @property
    def has_summary(self) -> bool:
        return self.listing.has_summary

    def to_dict(self) -> dict[str, Any]:
        data = self.listing.to_dict()
        data.update(
            {
                "event_count": self.event_count,
                "event_types": dict(self.event_types),
                "summary_path": str(self.markdown_path) if self.markdown_path else None,
                "summary_text": self.summary_text,
            }
        )
        return data


def list_finalized_sessions(root: str | Path | None = None) -> list[FinalizedSessionListing]:
    """Return finalized session records sorted newest first."""

    root_path = resolve_session_root(root)
    archive_dir = root_path / PROJECT_NAME
    if not archive_dir.exists():
        return []

    records: list[FinalizedSessionListing] = []
    for path in archive_dir.glob("*/*/*.jsonl"):
        if not path.is_file() or path.name.endswith(".partial"):
            continue
        record = _read_finalized_listing(path)
        if record is not None:
            records.append(record)

    return sorted(
        records,
        key=lambda record: (_filename_stamp(record.jsonl_path), str(record.jsonl_path)),
        reverse=True,
    )


def format_session_table(records: list[FinalizedSessionListing]) -> str:
    """Format finalized session records as a compact tab-separated table."""

    lines = ["started\tmachine\tagent\tslug\tcapture\tsummary\tpath"]
    for record in records:
        summary = "yes" if record.has_summary else "no"
        lines.append(
            "\t".join(
                [
                    record.started,
                    record.machine,
                    record.agent,
                    record.slug,
                    record.capture,
                    summary,
                    str(record.jsonl_path),
                ]
            )
        )
    return "\n".join(lines)


def inspect_finalized_session(
    record: str | Path,
    *,
    root: str | Path | None = None,
) -> FinalizedSessionInspection:
    """Return read-only inspection details for one finalized session record."""

    root_path = resolve_session_root(root)
    path = resolve_finalized_record(record, root=root_path)
    listing, event_count, event_types = _read_finalized_inspection(path)
    summary_text = (
        listing.markdown_path.read_text(encoding="utf-8")
        if listing.markdown_path is not None
        else None
    )
    return FinalizedSessionInspection(
        listing=listing,
        event_count=event_count,
        event_types=event_types,
        summary_text=summary_text,
    )


def resolve_finalized_record(record: str | Path, *, root: str | Path | None = None) -> Path:
    """Resolve a path, basename, or stem to exactly one finalized JSONL record."""

    root_path = resolve_session_root(root)
    candidate = Path(record).expanduser()

    if candidate.is_absolute() or candidate.parent != Path("."):
        if not candidate.is_absolute() and not candidate.exists():
            candidate = root_path / candidate
        if not candidate.exists():
            raise FileNotFoundError(f"finalized session not found: {record}")
        if not _is_finalized_archive_jsonl(candidate, root_path):
            raise ValueError(f"not a finalized archive JSONL record: {candidate}")
        return candidate

    archive_dir = root_path / PROJECT_NAME
    matches: list[Path] = []
    if archive_dir.exists():
        query = candidate.name
        for path in archive_dir.glob("*/*/*.jsonl"):
            if not path.is_file() or path.name.endswith(".partial"):
                continue
            if query.endswith(".jsonl"):
                matched = path.name == query
            else:
                matched = path.stem == query
            if matched and _is_finalized_archive_jsonl(path, root_path):
                matches.append(path)

    if not matches:
        raise FileNotFoundError(f"finalized session not found: {record}")
    if len(matches) > 1:
        formatted = ", ".join(str(path) for path in sorted(matches))
        raise ValueError(f"ambiguous finalized session record {record!s}: {formatted}")
    return matches[0]


def format_session_inspection(inspection: FinalizedSessionInspection) -> str:
    """Format one finalized session inspection as stable labeled text."""

    lines = [
        f"started: {inspection.started}",
        f"machine: {inspection.machine}",
        f"agent: {inspection.agent}",
        f"slug: {inspection.slug}",
        f"capture: {inspection.capture}",
        f"jsonl_path: {inspection.jsonl_path}",
        f"markdown_path: {inspection.markdown_path}" if inspection.markdown_path else "summary: no",
        f"event_count: {inspection.event_count}",
        "event_types:",
    ]
    for event_type, count in inspection.event_types.items():
        lines.append(f"  {event_type}: {count}")

    if inspection.summary_text is not None:
        lines.extend(["summary_text:", inspection.summary_text.rstrip("\n")])

    return "\n".join(lines)


def _read_finalized_listing(path: Path) -> FinalizedSessionListing | None:
    match = FILENAME_RE.match(path.name)
    if match is None:
        return None

    try:
        with path.open(encoding="utf-8") as handle:
            first_line = handle.readline()
    except OSError:
        return None

    try:
        first_event = json.loads(first_line)
    except json.JSONDecodeError:
        return None

    if not isinstance(first_event, dict) or first_event.get("type") != "session.started":
        return None

    markdown = path.with_suffix(".md")
    return FinalizedSessionListing(
        started=str(first_event.get("timestamp") or match.group("stamp")),
        machine=str(first_event.get("machine") or match.group("machine")),
        agent=str(first_event.get("agent") or match.group("agent")),
        slug=str(first_event.get("slug") or match.group("slug")),
        partial=bool(first_event.get("partial", False)),
        jsonl_path=path,
        markdown_path=markdown if markdown.exists() else None,
    )


def _read_finalized_inspection(path: Path) -> tuple[FinalizedSessionListing, int, dict[str, int]]:
    match = FILENAME_RE.match(path.name)
    if match is None:
        raise ValueError(f"finalized session filename is malformed: {path.name}")

    event_types: Counter[str] = Counter()
    first_event: dict[str, Any] | None = None

    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"malformed JSONL event at line {line_number}: {path}") from exc
            if not isinstance(event, dict):
                raise ValueError(f"malformed JSONL event at line {line_number}: {path}")
            if line_number == 1:
                first_event = event
            event_type = event.get("type")
            event_types[str(event_type) if event_type else "unknown"] += 1

    if first_event is None:
        raise ValueError(f"malformed finalized session record: empty file: {path}")
    if first_event.get("type") != "session.started":
        raise ValueError(f"malformed finalized session record: first event is not session.started: {path}")

    markdown = path.with_suffix(".md")
    listing = FinalizedSessionListing(
        started=str(first_event.get("timestamp") or match.group("stamp")),
        machine=str(first_event.get("machine") or match.group("machine")),
        agent=str(first_event.get("agent") or match.group("agent")),
        slug=str(first_event.get("slug") or match.group("slug")),
        partial=bool(first_event.get("partial", False)),
        jsonl_path=path,
        markdown_path=markdown if markdown.exists() else None,
    )
    return listing, sum(event_types.values()), dict(sorted(event_types.items()))


def _is_finalized_archive_jsonl(path: Path, root: Path) -> bool:
    if path.suffix != ".jsonl" or path.name.endswith(".partial"):
        return False
    if FILENAME_RE.match(path.name) is None:
        return False

    try:
        relative = path.resolve().relative_to((root / PROJECT_NAME).resolve())
    except ValueError:
        return False

    if len(relative.parts) != 3:
        return False
    year, month, _filename = relative.parts
    if not (year.isdigit() and len(year) == 4 and month.isdigit() and len(month) == 2):
        return False

    return True


def _filename_stamp(path: Path) -> str:
    match = FILENAME_RE.match(path.name)
    return match.group("stamp") if match else ""
