"""Small file-based session recorder for pipy."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

PROJECT_NAME = "pipy"

_FILENAME_RE = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2}T\d{6}Z)-"
    r"(?P<machine>[A-Za-z0-9._-]+)-"
    r"(?P<agent>[A-Za-z0-9._-]+)-"
    r"(?P<slug>[A-Za-z0-9._-]+)\.jsonl$"
)
_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


class FinalizedRecordError(ValueError):
    """Raised when a mutating API is asked to modify a finalized record."""


@dataclass(frozen=True)
class SessionRecord:
    """Finalized session record paths."""

    jsonl_path: Path
    markdown_path: Path | None = None


def resolve_session_root(
    root: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the session root from an explicit path, environment, or default."""

    if root is not None:
        return Path(root).expanduser()

    environ = os.environ if env is None else env
    configured = environ.get("PIPY_SESSION_DIR")
    if configured:
        return Path(configured).expanduser()

    return Path.home() / ".local" / "state" / "pipy" / "sessions"


def init_session(
    *,
    agent: str,
    slug: str,
    root: str | Path | None = None,
    goal: str | None = None,
    partial: bool = False,
    machine: str | None = None,
    now: datetime | None = None,
) -> Path:
    """Create an active JSONL session file under .in-progress/pipy."""

    root_path = resolve_session_root(root)
    active_dir = _active_dir(root_path)
    active_dir.mkdir(parents=True, exist_ok=True)

    started_at = _utc_now(now)
    safe_agent = _safe_component(agent, "agent")
    safe_slug = _safe_component(slug, "slug")
    safe_machine = _safe_component(machine or socket.gethostname().split(".")[0], "machine")

    basename = f"{_filename_stamp(started_at)}-{safe_machine}-{safe_agent}-{safe_slug}.jsonl"
    active_path = _unique_path(active_dir / basename)

    session_started: dict[str, Any] = {
        "type": "session.started",
        "timestamp": _event_timestamp(started_at),
        "project": PROJECT_NAME,
        "agent": safe_agent,
        "machine": safe_machine,
        "slug": safe_slug,
    }
    if goal:
        session_started["goal"] = goal
    if partial:
        session_started["partial"] = True

    events = [session_started]
    if partial:
        events.append(
            {
                "type": "capture.limitations",
                "timestamp": _event_timestamp(started_at),
                "agent": safe_agent,
                "summary": (
                    "Partial reconstruction from visible conversation context; "
                    "no raw platform transcript export was available."
                ),
            }
        )

    with active_path.open("x", encoding="utf-8") as handle:
        for event in events:
            _write_jsonl_event(handle, event)

    return active_path


def append_event(
    active: str | Path,
    *,
    root: str | Path | None = None,
    event_type: str | None = None,
    summary: str | None = None,
    agent: str | None = None,
    payload: Mapping[str, Any] | None = None,
    event: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> Path:
    """Append one JSON object to an active session JSONL file."""

    active_path = resolve_active_path(active, root=root)

    if event is not None:
        if event_type or summary or agent or payload:
            raise ValueError("--event-json cannot be combined with event field options")
        next_event = dict(event)
        if "type" not in next_event:
            raise ValueError("event JSON must include a type field")
    else:
        if not event_type:
            raise ValueError("event_type is required when event is not provided")
        next_event = {"type": event_type}
        if summary is not None:
            next_event["summary"] = summary
        if agent is not None:
            next_event["agent"] = _safe_component(agent, "agent")
        if payload is not None:
            next_event["payload"] = dict(payload)

    next_event.setdefault("timestamp", _event_timestamp(_utc_now(now)))

    with active_path.open("a", encoding="utf-8") as handle:
        _write_jsonl_event(handle, next_event)

    return active_path


def finalize_session(
    active: str | Path,
    *,
    root: str | Path | None = None,
    summary_file: str | Path | None = None,
    summary_text: str | None = None,
) -> SessionRecord:
    """Move an active session JSONL file into the finalized YYYY/MM archive."""

    if summary_file is not None and summary_text is not None:
        raise ValueError("summary_file and summary_text are mutually exclusive")

    root_path = resolve_session_root(root)
    active_path = resolve_active_path(active, root=root_path)
    match = _FILENAME_RE.match(active_path.name)
    if match is None:
        raise ValueError(
            "active session filename must match "
            "YYYY-MM-DDTHHMMSSZ-<machine>-<agent>-<slug>.jsonl"
        )

    stamp = datetime.strptime(match.group("stamp"), "%Y-%m-%dT%H%M%SZ").replace(tzinfo=UTC)
    final_dir = root_path / PROJECT_NAME / f"{stamp:%Y}" / f"{stamp:%m}"
    final_dir.mkdir(parents=True, exist_ok=True)

    final_jsonl = final_dir / active_path.name
    final_markdown = (
        final_jsonl.with_suffix(".md")
        if (summary_file is not None or summary_text is not None)
        else None
    )

    if final_jsonl.exists():
        raise FileExistsError(f"finalized JSONL already exists: {final_jsonl}")
    if final_markdown is not None and final_markdown.exists():
        raise FileExistsError(f"finalized Markdown summary already exists: {final_markdown}")

    temp_markdown: Path | None = None
    if final_markdown is not None:
        temp_markdown = final_markdown.with_name(f"{final_markdown.name}.partial")
        if summary_file is not None:
            with Path(summary_file).expanduser().open("rb") as source:
                with temp_markdown.open("xb") as target:
                    shutil.copyfileobj(source, target)
        else:
            with temp_markdown.open("x", encoding="utf-8") as target:
                target.write(_markdown_text(summary_text))

    try:
        active_path.rename(final_jsonl)
        if temp_markdown is not None and final_markdown is not None:
            temp_markdown.rename(final_markdown)
    except Exception:
        if temp_markdown is not None and temp_markdown.exists():
            temp_markdown.unlink()
        raise

    return SessionRecord(jsonl_path=final_jsonl, markdown_path=final_markdown)


def resolve_active_path(active: str | Path, *, root: str | Path | None = None) -> Path:
    """Resolve an active session path and reject finalized archive paths."""

    root_path = resolve_session_root(root)
    active_dir = _active_dir(root_path)
    resolved_active_dir = active_dir.resolve()
    candidate = Path(active).expanduser()

    candidates: list[Path]
    if candidate.is_absolute() or candidate.parent != Path("."):
        candidates = [candidate]
    else:
        candidates = [
            active_dir / candidate,
            active_dir / f"{candidate}.jsonl",
        ]

    existing: Path | None = None
    for possible in candidates:
        if possible.exists():
            existing = possible
            resolved = possible.resolve()
            break
    else:
        raise FileNotFoundError(f"active session not found: {active}")

    if resolved.parent != resolved_active_dir:
        raise FinalizedRecordError(f"refusing to modify non-active session record: {resolved}")
    if existing.suffix != ".jsonl":
        raise ValueError(f"active session must be a .jsonl file: {existing}")

    return existing


def _active_dir(root: Path) -> Path:
    return root / ".in-progress" / PROJECT_NAME


def _filename_stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H%M%SZ")


def _event_timestamp(value: datetime) -> str:
    return value.isoformat()


def _markdown_text(summary_text: str | None) -> str:
    text = "" if summary_text is None else summary_text
    return text if text.endswith("\n") else f"{text}\n"


def _safe_component(value: str, name: str) -> str:
    normalized = _SAFE_COMPONENT_RE.sub("-", value.strip()).strip("-._")
    if not normalized:
        raise ValueError(f"{name} must contain at least one filename-safe character")
    return normalized


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    for index in range(2, 1000):
        # The suffix becomes part of the slug component parsed during finalize.
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate

    raise FileExistsError(f"could not create a unique session path near {path}")


def _utc_now(value: datetime | None = None) -> datetime:
    current = datetime.now(UTC) if value is None else value
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC)


def _write_jsonl_event(handle: Any, event: Mapping[str, Any]) -> None:
    handle.write(json.dumps(dict(event), sort_keys=True, separators=(",", ":")))
    handle.write("\n")
