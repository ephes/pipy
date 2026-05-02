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
