"""Metadata-only session resume for pipy.

Resume reads a finalized JSONL session record and returns a value object that
describes the prior run using only safe metadata. It never reads or returns
raw prompts, model text, tool payloads, file contents, or other sensitive
transcript content from the archive (the archive never stores those anyway,
but this module additionally enforces a strict allowlist on the keys it
extracts).

The resumed session is intended to be a brand new finalized record. Resume
itself does not mutate the prior record, append events to it, or copy raw
transcript content from any sidecar.

Wiring a runtime hook that seeds a future provider turn with the value
returned by :func:`compose_resume_system_block` is a follow-up: it requires a
small change to the native session entry point. This module ships the
metadata-only reader and a ``pipy-session resume-info`` CLI hook that exposes
the projection so the surface can be reviewed and tested in isolation first.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from pipy_harness.capture import sanitize_text
from pipy_session.catalog import resolve_finalized_record
from pipy_session.recorder import FILENAME_RE

if TYPE_CHECKING:
    from pipy_harness.models import SessionLineage

# Allowlist of top-level event keys that resume may read. Any other top-level
# key is ignored, so payload-shaped or hostile keys like ``prompt``,
# ``model_output``, ``tool_result``, ``diff``, ``raw_response``, or
# ``secret_token`` never enter the ``ResumeContext`` even if a malformed or
# forged event includes them. See ``docs/session-storage.md`` for the archive
# privacy policy this allowlist enforces.
SAFE_TOP_LEVEL_EVENT_KEYS: tuple[str, ...] = (
    "type",
    "timestamp",
    "agent",
    "machine",
    "slug",
    "project",
    "partial",
)

# Allowlist of payload keys that resume may read from native lifecycle event
# payloads. ``provider`` and ``model_id`` are the names emitted by the native
# session's ``_safe_context`` projection; ``provider_name`` is accepted as a
# documented fallback for adapters that prefer the long-form key. ``turn_count``
# is the conversation turn counter recorded on ``native.session.completed``.
# ``cwd_sha256`` is the workspace path hash recorded on
# ``harness.run.started`` and related lifecycle events.
SAFE_PAYLOAD_KEYS: tuple[str, ...] = (
    "provider",
    "provider_name",
    "model_id",
    "turn_count",
    "cwd_sha256",
)

# Allowlist of keys read from the ``resume`` object recorded on a finalized
# child session's ``session.started`` event. Anything outside this allowlist is
# ignored, so a forged ``resume`` object cannot smuggle raw content into the
# resume projection.
SAFE_LINEAGE_KEYS: tuple[str, ...] = (
    "parent_session_id",
    "relationship",
    "branch_label",
    "fork_timestamp",
)

COMPACTION_EVENT_TYPE = "native.session.compacted"


@dataclass(frozen=True, slots=True)
class ResumeContext:
    """Metadata-only continuation context for a prior finalized session.

    ``ResumeContext`` carries only allowlisted labels and counters extracted
    from the finalized JSONL record (plus the optional sibling Markdown
    summary, which is itself a deliberate human-review artifact). It never
    carries raw prompts, model text, tool payloads, file contents, diffs,
    secrets, or any other sensitive transcript content.
    """

    prior_session_id: str
    prior_provider_name: str | None
    prior_model_id: str | None
    prior_turn_count: int
    prior_workspace_hash: str | None
    prior_started_at: str
    prior_ended_at: str
    prior_summary: str | None
    prior_relationship: str | None = None
    prior_parent_session_id: str | None = None
    prior_branch_label: str | None = None
    prior_fork_timestamp: str | None = None
    prior_compaction_event_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable projection of this context.

        This is the *only* place ``prior_summary`` (the prior run's Markdown
        summary — a deliberate human-review artifact) escapes the value object.
        It is safe for ``pipy-session resume-info`` and ``export`` (which both
        already surface the sibling ``.md``), but it must never be folded into
        the seeded system prompt, the resumed-state banner, or a metadata-first
        JSONL archive event. Callers that record archive events must project a
        safe subset, not this dict.
        """

        return asdict(self)


def resume_session_from_archive(
    record_path_or_stem: str,
    *,
    session_root: Path,
) -> ResumeContext:
    """Resolve and read a finalized record; return a metadata-only context.

    Parameters
    ----------
    record_path_or_stem:
        Finalized record basename or stem (as accepted by
        ``pipy-session inspect`` and ``pipy-session export``).
    session_root:
        Resolved session root directory (already passed through
        ``resolve_session_root``).

    Raises
    ------
    LookupError:
        The named record could not be resolved.
    ValueError:
        The record exists but its first event is missing, malformed, not a
        JSON object, or not a ``session.started`` event.
    """

    root_path = Path(session_root)
    try:
        record_path = resolve_finalized_record(record_path_or_stem, root=root_path)
    except FileNotFoundError as exc:
        raise LookupError(str(exc)) from exc

    events = _read_record_events(record_path)
    first_event = events[0]
    if not isinstance(first_event, dict) or first_event.get("type") != "session.started":
        raise ValueError(
            f"first event is not session.started: {record_path}"
        )

    match = FILENAME_RE.match(record_path.name)
    if match is None:
        raise ValueError(f"finalized session filename is malformed: {record_path.name}")

    # Timestamps reach the banner and seeded system prompt, so they are
    # sanitized like every other label; an unsafe forged timestamp falls back
    # to the (regex-validated) filename stamp.
    started_at = _safe_label(first_event.get("timestamp")) or match.group("stamp")

    provider_name, model_id, turn_count, workspace_hash, ended_at = (
        _scan_lifecycle_metadata(events, fallback_started_at=started_at)
    )

    lineage = _safe_lineage(first_event.get("resume"))
    compaction_event_count = sum(
        1
        for event in events
        if isinstance(event, dict) and event.get("type") == COMPACTION_EVENT_TYPE
    )

    markdown_path = record_path.with_suffix(".md")
    summary_text: str | None = None
    if markdown_path.is_file() and not markdown_path.is_symlink():
        summary_text = markdown_path.read_text(encoding="utf-8")

    return ResumeContext(
        prior_session_id=record_path.stem,
        prior_provider_name=provider_name,
        prior_model_id=model_id,
        prior_turn_count=turn_count,
        prior_workspace_hash=workspace_hash,
        prior_started_at=started_at,
        prior_ended_at=ended_at,
        prior_summary=summary_text,
        prior_relationship=lineage.get("relationship"),
        prior_parent_session_id=lineage.get("parent_session_id"),
        prior_branch_label=lineage.get("branch_label"),
        prior_fork_timestamp=lineage.get("fork_timestamp"),
        prior_compaction_event_count=compaction_event_count,
    )


def build_session_lineage(
    context: ResumeContext,
    *,
    relationship: str,
    fork_timestamp: str,
    branch_label: str | None = None,
) -> "SessionLineage":
    """Build the metadata-only :class:`SessionLineage` for a resumed/forked run.

    Carries only safe parent/provider/model/turn labels from ``context``; never
    summary text, prompts, or model output.
    """

    from pipy_harness.models import SessionLineage

    return SessionLineage(
        parent_session_id=context.prior_session_id,
        relationship=relationship,
        fork_timestamp=fork_timestamp,
        branch_label=branch_label,
        prior_provider_name=context.prior_provider_name,
        prior_model_id=context.prior_model_id,
        prior_turn_count=context.prior_turn_count,
    )


def compose_resume_system_block(context: ResumeContext) -> str:
    """Return a short system-prompt insert describing the prior run.

    The block carries only safe labels: the prior session id, the prior
    provider/model selection, the prior turn count, and the finalized
    timestamp. It never includes user text, model text, tool payloads, or
    summary content from the prior run.
    """

    provider_label = context.prior_provider_name or "unknown"
    model_label = context.prior_model_id or "unknown"
    finalized_label = context.prior_ended_at or context.prior_started_at or "unknown"
    return (
        f"Resumed from session {context.prior_session_id} "
        f"(provider={provider_label}, model={model_label}, "
        f"{context.prior_turn_count} prior turns, "
        f"finalized {finalized_label})."
    )


def compose_resume_status_line(
    context: ResumeContext,
    *,
    branch_label: str | None = None,
) -> str:
    """Return a safe one-line resumed-state banner for startup/status UI.

    Carries only safe labels — the prior session id, provider/model selection,
    prior turn count, finalized timestamp, and (for a branch) the branch label.
    It never includes prompts, model output, tool payloads, or summary text.
    """

    provider_label = context.prior_provider_name or "unknown"
    model_label = context.prior_model_id or "unknown"
    finalized_label = context.prior_ended_at or context.prior_started_at or "unknown"
    kind = f"branch {branch_label}" if branch_label else "resume"
    return (
        f"Resumed ({kind}) from session {context.prior_session_id}: "
        f"provider={provider_label}, model={model_label}, "
        f"{context.prior_turn_count} prior turns, finalized {finalized_label}."
    )


def _read_record_events(record_path: Path) -> list[dict[str, Any]]:
    if record_path.is_symlink():
        raise ValueError(f"refusing to read symbolic-link record: {record_path}")

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
    return events


def _scan_lifecycle_metadata(
    events: list[dict[str, Any]],
    *,
    fallback_started_at: str,
) -> tuple[str | None, str | None, int, str | None, str]:
    provider_name: str | None = None
    model_id: str | None = None
    turn_count = 0
    workspace_hash: str | None = None
    last_timestamp: str = fallback_started_at

    for event in events:
        safe_event = _safe_top_level(event)
        safe_payload = _safe_payload(event.get("payload"))

        event_type = safe_event.get("type")
        safe_timestamp = _safe_label(safe_event.get("timestamp"))
        if safe_timestamp:
            last_timestamp = safe_timestamp

        if provider_name is None:
            candidate = safe_payload.get("provider") or safe_payload.get("provider_name")
            provider_name = _safe_label(candidate)

        if model_id is None:
            model_id = _safe_label(safe_payload.get("model_id"))

        if workspace_hash is None:
            workspace_hash = _safe_label(safe_payload.get("cwd_sha256"))

        candidate_turns = safe_payload.get("turn_count")
        if (
            isinstance(candidate_turns, int)
            and not isinstance(candidate_turns, bool)
            and candidate_turns >= 0
        ):
            if event_type == "native.session.completed":
                turn_count = candidate_turns
            elif turn_count == 0 and candidate_turns > 0:
                # Best-effort fallback when an earlier lifecycle event already
                # carries a non-zero turn counter (for example mid-session
                # snapshots) and no terminal completion event is present.
                turn_count = candidate_turns

    return provider_name, model_id, turn_count, workspace_hash, last_timestamp


def _safe_top_level(event: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key in SAFE_TOP_LEVEL_EVENT_KEYS:
        if key in event:
            safe[key] = event[key]
    return safe


def _safe_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    safe: dict[str, Any] = {}
    for key in SAFE_PAYLOAD_KEYS:
        if key in payload:
            safe[key] = payload[key]
    return safe


def _safe_lineage(resume_field: Any) -> dict[str, str]:
    """Extract allowlisted lineage labels from a child session's ``resume`` map.

    Returns only string-typed allowlisted keys; non-string and out-of-allowlist
    values (including any forged payload-shaped keys) are dropped.
    """

    if not isinstance(resume_field, Mapping):
        return {}
    safe: dict[str, str] = {}
    for key in SAFE_LINEAGE_KEYS:
        label = _safe_label(resume_field.get(key))
        if label is not None:
            safe[key] = label
    return safe


# Bound on the labels we will carry out of a (possibly forged or foreign)
# finalized record into the resume context, banner, and seeded system prompt.
_SAFE_LABEL_MAX_LENGTH = 128


def _safe_label(value: Any) -> str | None:
    """Return ``value`` only if it is a terminal-safe, secret-free short label.

    A finalized record may be corrupt, hand-edited, or written by a foreign
    tool. Provider/model/lineage labels read from it flow into the new child
    archive, the resumed-state banner, and the seeded provider system prompt,
    so they must be sanitized at this boundary — not merely key-allowlisted.
    Control bytes, over-long values, and secret-shaped content are dropped
    (the field falls back to ``None``/``"unknown"``), which fails closed.
    """

    if not isinstance(value, str) or not value:
        return None
    if len(value) > _SAFE_LABEL_MAX_LENGTH:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    if sanitize_text(value) == "[REDACTED]":
        return None
    return value
