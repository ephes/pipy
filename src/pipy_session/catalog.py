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


@dataclass(frozen=True)
class FinalizedSessionSearchMatch:
    """Privacy-safe match detail for one searchable finalized-session field."""

    field: str
    snippet: str
    event_type: str | None = None
    line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "event_type": self.event_type,
            "line": self.line,
            "snippet": self.snippet,
        }


@dataclass(frozen=True)
class FinalizedSessionSearchResult:
    """Search result for one finalized session record."""

    listing: FinalizedSessionListing
    matches: list[FinalizedSessionSearchMatch]

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
        data["matches"] = [match.to_dict() for match in self.matches]
        return data


@dataclass(frozen=True)
class VerificationIssue:
    """Privacy-safe structural issue found in the local session archive."""

    severity: str
    kind: str
    path: Path
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "kind": self.kind,
            "path": str(self.path),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SessionArchiveVerification:
    """Read-only verification result for the local session archive."""

    root: Path
    issues: list[VerificationIssue]

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issue_count": self.issue_count,
            "root": str(self.root),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class SessionReflectionItem:
    """One summary-safe event or Markdown signal for archive reflection."""

    category: str
    listing: FinalizedSessionListing
    summary: str
    event_type: str | None = None
    line: int | None = None

    @property
    def started(self) -> str:
        return self.listing.started

    @property
    def agent(self) -> str:
        return self.listing.agent

    @property
    def slug(self) -> str:
        return self.listing.slug

    @property
    def jsonl_path(self) -> Path:
        return self.listing.jsonl_path

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "started": self.listing.started,
            "machine": self.listing.machine,
            "agent": self.listing.agent,
            "slug": self.listing.slug,
            "capture": self.listing.capture,
            "event_type": self.event_type,
            "line": self.line,
            "summary": self.summary,
            "jsonl_path": str(self.listing.jsonl_path),
        }


@dataclass(frozen=True)
class SessionReflection:
    """Summary-safe learning report over finalized session records."""

    root: Path
    session_count: int
    sessions_with_markdown: int
    agent_counts: dict[str, int]
    capture_counts: dict[str, int]
    event_type_counts: dict[str, int]
    summary_event_count: int
    low_signal_session_count: int
    items: list[SessionReflectionItem]

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "session_count": self.session_count,
            "sessions_with_markdown": self.sessions_with_markdown,
            "agent_counts": dict(self.agent_counts),
            "capture_counts": dict(self.capture_counts),
            "event_type_counts": dict(self.event_type_counts),
            "summary_event_count": self.summary_event_count,
            "low_signal_session_count": self.low_signal_session_count,
            "items": [item.to_dict() for item in self.items],
        }


REFLECTION_EVENT_CATEGORIES = {
    "decision.recorded": "decisions",
    "lesson.learned": "lessons",
    "recommendation.recorded": "recommendations",
    "review.findings": "review-findings",
    "review.assessed": "review-findings",
    "review.performed": "review-findings",
    "review.followup.completed": "review-followups",
    "implementation.completed": "implementation",
    "file.changed": "implementation",
    "commit.created": "implementation",
    "git.push": "implementation",
    "verification.performed": "verification",
    "research.performed": "research",
}

REFLECTION_CATEGORY_ORDER = (
    "decisions",
    "lessons",
    "recommendations",
    "review-findings",
    "review-followups",
    "research",
    "implementation",
    "verification",
    "session-summaries",
)

LOW_SIGNAL_EVENT_TYPES = {
    "auto_capture.ended",
    "auto_capture.started",
    "capture.limitations",
    "session.started",
}


@dataclass(frozen=True, slots=True)
class _ReflectionJsonlSignals:
    event_type_counts: Counter[str]
    items: list[SessionReflectionItem]
    summary_event_count: int
    record_event_types: set[str]


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


def search_finalized_sessions(
    query: str,
    *,
    root: str | Path | None = None,
) -> list[FinalizedSessionSearchResult]:
    """Search finalized session metadata, summaries, and event summaries."""

    if not query.strip():
        raise ValueError("search query must not be empty")

    normalized_query = query.casefold()
    results: list[FinalizedSessionSearchResult] = []
    for listing in list_finalized_sessions(root=root):
        matches = _search_finalized_listing(listing, normalized_query)
        if matches:
            results.append(FinalizedSessionSearchResult(listing=listing, matches=matches))
    return results


def reflect_on_finalized_sessions(root: str | Path | None = None) -> SessionReflection:
    """Build a read-only learning report from summary-safe archive fields."""

    root_path = resolve_session_root(root)
    records = list_finalized_sessions(root=root_path)
    agent_counts: Counter[str] = Counter()
    capture_counts: Counter[str] = Counter()
    event_type_counts: Counter[str] = Counter()
    items: list[SessionReflectionItem] = []
    summary_event_count = 0
    low_signal_session_count = 0

    for listing in records:
        agent_counts[listing.agent] += 1
        capture_counts[listing.capture] += 1

        had_reflection_signal = False
        record_event_types: set[str] = set()
        jsonl_signals = _read_reflection_jsonl_signals(listing)
        if jsonl_signals is not None:
            event_type_counts.update(jsonl_signals.event_type_counts)
            items.extend(jsonl_signals.items)
            summary_event_count += jsonl_signals.summary_event_count
            record_event_types = jsonl_signals.record_event_types
            had_reflection_signal = bool(jsonl_signals.items)

        if listing.markdown_path is not None:
            markdown_summary = _markdown_summary_snippet(listing.markdown_path)
            if markdown_summary:
                if not _is_generic_auto_summary(markdown_summary):
                    had_reflection_signal = True
                    items.append(
                        SessionReflectionItem(
                            category="session-summaries",
                            listing=listing,
                            summary=markdown_summary,
                        )
                    )

        if (
            listing.partial
            and record_event_types
            and record_event_types.issubset(
                LOW_SIGNAL_EVENT_TYPES | {"claude.userpromptsubmit", "claude.posttooluse"}
            )
            and not had_reflection_signal
        ):
            low_signal_session_count += 1

    return SessionReflection(
        root=root_path,
        session_count=len(records),
        sessions_with_markdown=sum(1 for record in records if record.has_summary),
        agent_counts=dict(sorted(agent_counts.items())),
        capture_counts=dict(sorted(capture_counts.items())),
        event_type_counts=dict(sorted(event_type_counts.items())),
        summary_event_count=summary_event_count,
        low_signal_session_count=low_signal_session_count,
        items=items,
    )


def format_session_table(records: list[FinalizedSessionListing]) -> str:
    """Format finalized session records as a compact tab-separated table."""

    lines = ["started\tmachine\tagent\tslug\tcapture\tsummary\tpath"]
    for record in records:
        summary = "yes" if record.has_summary else "no"
        lines.append(
            "\t".join(
                [
                    _table_cell(record.started),
                    _table_cell(record.machine),
                    _table_cell(record.agent),
                    _table_cell(record.slug),
                    _table_cell(record.capture),
                    _table_cell(summary),
                    _table_cell(str(record.jsonl_path)),
                ]
            )
        )
    return "\n".join(lines)


def format_session_reflection(reflection: SessionReflection) -> str:
    """Format a summary-safe archive reflection as Markdown."""

    lines = [
        "# Session Reflection",
        "",
        f"- root: {_table_cell(str(reflection.root))}",
        f"- sessions: {reflection.session_count}",
        f"- sessions_with_markdown: {reflection.sessions_with_markdown}",
        f"- low_signal_partial_sessions: {reflection.low_signal_session_count}",
        f"- summary_events: {reflection.summary_event_count}",
        f"- captures: {_format_counts(reflection.capture_counts)}",
        f"- agents: {_format_counts(reflection.agent_counts)}",
        "",
        "## Event Types",
        "",
    ]

    if reflection.event_type_counts:
        for event_type, count in sorted(
            reflection.event_type_counts.items(),
            key=lambda item: (-item[1], item[0]),
        ):
            lines.append(f"- {_table_cell(event_type)}: {count}")
    else:
        lines.append("- none")

    grouped: dict[str, list[SessionReflectionItem]] = {}
    for item in reflection.items:
        grouped.setdefault(item.category, []).append(item)

    categories = list(REFLECTION_CATEGORY_ORDER)
    ordered_categories = set(categories)
    categories.extend(sorted(category for category in grouped if category not in ordered_categories))
    for category in categories:
        category_items = grouped.get(category, [])
        lines.extend(["", f"## {_section_title(category)}", ""])
        if not category_items:
            lines.append("- none")
            continue

        for item in category_items:
            event_label = f" {item.event_type}" if item.event_type else ""
            line_label = f":{item.line}" if item.line is not None else ""
            lines.append(
                "- "
                f"[{_table_cell(item.started)} {_table_cell(item.agent)} "
                f"{_table_cell(item.slug)}{event_label}{line_label}] "
                f"{_table_cell(item.summary)}"
            )

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Uses finalized session metadata, event types, event summaries, and Markdown summary snippets only.",
            "- Does not print raw JSONL event bodies, payload values, or transcript bodies.",
            "- Skips malformed or unreadable finalized records; run `pipy-session verify` for archive health.",
        ]
    )
    return "\n".join(lines)


def format_session_search_results(results: list[FinalizedSessionSearchResult]) -> str:
    """Format finalized session search results as a compact tab-separated table."""

    lines = ["started\tmachine\tagent\tslug\tcapture\tmatches\tpath"]
    for result in results:
        lines.append(
            "\t".join(
                [
                    _table_cell(result.started),
                    _table_cell(result.machine),
                    _table_cell(result.agent),
                    _table_cell(result.slug),
                    _table_cell(result.capture),
                    ", ".join(_search_match_labels(result.matches)),
                    _table_cell(str(result.jsonl_path)),
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


def verify_session_archive(root: str | Path | None = None) -> SessionArchiveVerification:
    """Verify finalized session archive structure without exposing raw event bodies."""

    root_path = resolve_session_root(root)
    archive_dir = root_path / PROJECT_NAME
    issues: list[VerificationIssue] = []

    if root_path.exists():
        for path in sorted(root_path.rglob("*.partial")):
            if path.is_file():
                issues.append(
                    VerificationIssue(
                        severity="warning",
                        kind="partial-file",
                        path=path,
                        detail="sync-excluded partial file exists",
                    )
                )

    finalized_jsonl_paths: list[Path] = []
    if archive_dir.exists():
        for path in sorted(archive_dir.rglob("*")):
            if not path.is_file() or path.name.endswith(".partial"):
                continue

            relative = path.relative_to(archive_dir)
            if not _is_year_month_archive_file(relative):
                issues.append(
                    VerificationIssue(
                        severity="error",
                        kind="unexpected-archive-file",
                        path=path,
                        detail="expected finalized files directly under pipy/YYYY/MM/",
                    )
                )
                continue

            if path.suffix == ".jsonl":
                if FILENAME_RE.match(path.name) is None:
                    issues.append(
                        VerificationIssue(
                            severity="error",
                            kind="malformed-filename",
                            path=path,
                            detail=(
                                "filename must match "
                                "YYYY-MM-DDTHHMMSSZ-<machine>-<agent>-<slug>.jsonl"
                            ),
                        )
                    )
                    continue

                finalized_jsonl_paths.append(path)
                issue = _first_event_verification_issue(path)
                if issue is not None:
                    issues.append(issue)
                continue

            if path.suffix == ".md":
                if not path.with_suffix(".jsonl").exists():
                    issues.append(
                        VerificationIssue(
                            severity="warning",
                            kind="orphan-summary",
                            path=path,
                            detail="missing sibling JSONL",
                        )
                    )
                continue

            issues.append(
                VerificationIssue(
                    severity="warning",
                    kind="unsupported-archive-file",
                    path=path,
                    detail="unsupported file suffix under finalized archive",
                )
            )

    issues.extend(_ambiguous_name_issues(finalized_jsonl_paths))
    issues.sort(key=_verification_issue_sort_key)
    return SessionArchiveVerification(root=root_path, issues=issues)


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
        f"started: {_table_cell(inspection.started)}",
        f"machine: {_table_cell(inspection.machine)}",
        f"agent: {_table_cell(inspection.agent)}",
        f"slug: {_table_cell(inspection.slug)}",
        f"capture: {_table_cell(inspection.capture)}",
        f"jsonl_path: {_table_cell(str(inspection.jsonl_path))}",
        (
            f"markdown_path: {_table_cell(str(inspection.markdown_path))}"
            if inspection.markdown_path
            else "summary: no"
        ),
        f"event_count: {inspection.event_count}",
        "event_types:",
    ]
    for event_type, count in inspection.event_types.items():
        lines.append(f"  {_table_cell(event_type)}: {count}")

    if inspection.summary_text is not None:
        lines.extend(["summary_text:", inspection.summary_text.rstrip("\n")])

    return "\n".join(lines)


def format_archive_verification(verification: SessionArchiveVerification) -> str:
    """Format archive verification as stable tab-separated text."""

    lines = ["status\tissue\tpath\tdetail"]
    if verification.ok:
        lines.append("ok")
        return "\n".join(lines)

    for issue in verification.issues:
        lines.append(
            "\t".join(
                [
                    issue.severity,
                    issue.kind,
                    str(issue.path),
                    issue.detail,
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
    except (OSError, UnicodeError):
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


def _search_finalized_listing(
    listing: FinalizedSessionListing,
    normalized_query: str,
) -> list[FinalizedSessionSearchMatch]:
    matches: list[FinalizedSessionSearchMatch] = []

    metadata = {
        "started": listing.started,
        "machine": listing.machine,
        "agent": listing.agent,
        "slug": listing.slug,
        "capture": listing.capture,
        "jsonl_path": str(listing.jsonl_path),
    }
    if listing.markdown_path is not None:
        metadata["markdown_path"] = str(listing.markdown_path)

    for field, value in metadata.items():
        if _matches_query(value, normalized_query):
            matches.append(
                FinalizedSessionSearchMatch(
                    field=f"metadata.{field}",
                    snippet=_snippet(value, normalized_query),
                )
            )

    try:
        with listing.jsonl_path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                event = json.loads(line)
                if not isinstance(event, dict):
                    return []

                event_type_value = event.get("type")
                event_type = str(event_type_value) if event_type_value else None
                if isinstance(event_type_value, str) and _matches_query(
                    event_type_value,
                    normalized_query,
                ):
                    matches.append(
                        FinalizedSessionSearchMatch(
                            field="event.type",
                            event_type=event_type,
                            line=line_number,
                            snippet=_snippet(event_type_value, normalized_query),
                        )
                    )

                summary = event.get("summary")
                if isinstance(summary, str) and _matches_query(summary, normalized_query):
                    matches.append(
                        FinalizedSessionSearchMatch(
                            field="event.summary",
                            event_type=event_type,
                            line=line_number,
                            snippet=_snippet(summary, normalized_query),
                        )
                    )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []

    if listing.markdown_path is not None:
        try:
            markdown_text = listing.markdown_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            markdown_text = ""

        if _matches_query(markdown_text, normalized_query):
            matches.append(
                FinalizedSessionSearchMatch(
                    field="markdown.summary",
                    line=_line_number_for_match(markdown_text, normalized_query),
                    snippet=_snippet(markdown_text, normalized_query),
                )
            )

    return matches


def _read_reflection_jsonl_signals(listing: FinalizedSessionListing) -> _ReflectionJsonlSignals | None:
    event_type_counts: Counter[str] = Counter()
    items: list[SessionReflectionItem] = []
    summary_event_count = 0
    record_event_types: set[str] = set()

    try:
        with listing.jsonl_path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                event = json.loads(line)
                if not isinstance(event, dict):
                    return None

                event_type_value = event.get("type")
                event_type = str(event_type_value) if event_type_value else "unknown"
                event_type_counts[event_type] += 1
                record_event_types.add(event_type)

                summary = event.get("summary")
                if isinstance(summary, str):
                    summary_event_count += 1
                    category = REFLECTION_EVENT_CATEGORIES.get(event_type)
                    if category is not None:
                        items.append(
                            SessionReflectionItem(
                                category=category,
                                listing=listing,
                                event_type=event_type,
                                line=line_number,
                                summary=_snippet(summary, "", width=240),
                            )
                        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None

    return _ReflectionJsonlSignals(
        event_type_counts=event_type_counts,
        items=items,
        summary_event_count=summary_event_count,
        record_event_types=record_event_types,
    )


def _matches_query(value: str, normalized_query: str) -> bool:
    return normalized_query in value.casefold()


def _snippet(value: str, normalized_query: str, *, width: int = 160) -> str:
    if not value:
        return ""

    normalized_value = value.casefold()
    index = normalized_value.find(normalized_query) if normalized_query else 0
    if index < 0:
        index = 0

    if normalized_query:
        start = max(0, index - 60)
        end = min(len(value), index + len(normalized_query) + 60)
    else:
        start = 0
        end = min(len(value), width)

    snippet = " ".join(value[start:end].split())
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(value):
        snippet = f"{snippet}..."
    if len(snippet) <= width:
        return snippet
    return f"{snippet[: max(0, width - 3)].rstrip()}..."


def _line_number_for_match(value: str, normalized_query: str) -> int | None:
    index = value.casefold().find(normalized_query)
    if index < 0:
        return None
    return value.count("\n", 0, index) + 1


def _search_match_labels(matches: list[FinalizedSessionSearchMatch]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for match in matches:
        label = _search_match_label(match)
        if label not in seen:
            seen.add(label)
            labels.append(label)
    return labels


def _search_match_label(match: FinalizedSessionSearchMatch) -> str:
    if match.field == "event.type":
        return f"event:{_table_cell(match.event_type or 'unknown') or 'unknown'}"
    if match.field == "event.summary":
        return "summary"
    if match.field == "markdown.summary":
        return "markdown"
    if match.field.startswith("metadata."):
        return match.field.removeprefix("metadata.")
    return match.field


def _table_cell(value: str) -> str:
    return " ".join(value.split())


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{_table_cell(key)}={value}" for key, value in counts.items())


def _section_title(category: str) -> str:
    return " ".join(part.capitalize() for part in category.split("-"))


def _markdown_summary_snippet(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""

    return _snippet(text, "", width=240)


def _is_generic_auto_summary(summary: str) -> bool:
    normalized = _table_cell(summary).casefold()
    if normalized.startswith("# summary "):
        normalized = normalized.removeprefix("# summary ").lstrip()
    return (
        normalized.startswith("automatic codex capture finalized")
        or normalized.startswith("automatic claude capture finalized")
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


def _is_year_month_archive_file(relative: Path) -> bool:
    if len(relative.parts) != 3:
        return False
    year, month, _filename = relative.parts
    return year.isdigit() and len(year) == 4 and month.isdigit() and len(month) == 2


def _first_event_verification_issue(path: Path) -> VerificationIssue | None:
    try:
        with path.open(encoding="utf-8") as handle:
            first_line = handle.readline()
    except OSError:
        return VerificationIssue(
            severity="error",
            kind="unreadable-jsonl",
            path=path,
            detail="could not read first line",
        )
    except UnicodeError:
        return VerificationIssue(
            severity="error",
            kind="malformed-jsonl",
            path=path,
            detail="first line is not valid UTF-8",
        )

    if not first_line.strip():
        return VerificationIssue(
            severity="error",
            kind="malformed-jsonl",
            path=path,
            detail="empty first line",
        )

    try:
        first_event = json.loads(first_line)
    except json.JSONDecodeError:
        return VerificationIssue(
            severity="error",
            kind="malformed-jsonl",
            path=path,
            detail="invalid JSON first line",
        )

    if not isinstance(first_event, dict):
        return VerificationIssue(
            severity="error",
            kind="malformed-jsonl",
            path=path,
            detail="first line is not a JSON object",
        )

    if first_event.get("type") != "session.started":
        return VerificationIssue(
            severity="error",
            kind="malformed-jsonl",
            path=path,
            detail="first event is not session.started",
        )

    return None


def _ambiguous_name_issues(paths: list[Path]) -> list[VerificationIssue]:
    issues: list[VerificationIssue] = []
    by_basename: dict[str, list[Path]] = {}
    by_stem: dict[str, list[Path]] = {}
    for path in paths:
        by_basename.setdefault(path.name, []).append(path)
        by_stem.setdefault(path.stem, []).append(path)

    for basename, matches in sorted(by_basename.items()):
        if len(matches) > 1:
            sorted_matches = sorted(matches)
            issues.append(
                VerificationIssue(
                    severity="warning",
                    kind="ambiguous-basename",
                    path=sorted_matches[0],
                    detail=_ambiguity_detail("basename", basename, sorted_matches),
                )
            )

    for stem, matches in sorted(by_stem.items()):
        if len(matches) > 1:
            sorted_matches = sorted(matches)
            issues.append(
                VerificationIssue(
                    severity="warning",
                    kind="ambiguous-stem",
                    path=sorted_matches[0],
                    detail=_ambiguity_detail("stem", stem, sorted_matches),
                )
            )

    return issues


def _ambiguity_detail(label: str, value: str, matches: list[Path]) -> str:
    paths = ", ".join(str(path) for path in matches)
    return f"duplicate {label} {value!r} appears in {len(matches)} finalized records: {paths}"


def _verification_issue_sort_key(issue: VerificationIssue) -> tuple[int, str, str, str]:
    severity_rank = {"error": 0, "warning": 1}
    return (severity_rank.get(issue.severity, 99), issue.kind, str(issue.path), issue.detail)


def _filename_stamp(path: Path) -> str:
    match = FILENAME_RE.match(path.name)
    return match.group("stamp") if match else ""
