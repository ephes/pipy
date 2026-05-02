"""Read-only catalog helpers for finalized pipy session records."""

from __future__ import annotations

import json
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


def _filename_stamp(path: Path) -> str:
    match = FILENAME_RE.match(path.name)
    return match.group("stamp") if match else ""
