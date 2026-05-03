"""Adapter helpers for conservative automatic session capture."""

from __future__ import annotations

import json
import re
import subprocess
from hashlib import sha256
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from pipy_session.recorder import (
    PROJECT_NAME,
    SessionRecord,
    append_event,
    finalize_session,
    init_session,
    resolve_active_path,
    resolve_session_root,
)

STATE_VERSION = 1

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class AutoCaptureState:
    """Persistent mapping from a platform session id to a pipy active record."""

    agent: str
    platform_session_id: str
    active_path: Path
    state_path: Path
    partial: bool


@dataclass(frozen=True)
class HookResult:
    """Result from handling one platform hook payload."""

    message: str | None = None
    record: SessionRecord | None = None
    active_path: Path | None = None


@dataclass(frozen=True)
class PrunedState:
    """One stale automatic-capture state file found by prune."""

    path: Path
    reason: str
    removed: bool


def reference_pi_session(
    pi_session_path: str | Path,
    *,
    root: str | Path | None = None,
    slug: str | None = None,
    summary: str | None = None,
    machine: str | None = None,
    now: datetime | None = None,
) -> SessionRecord:
    """Create a finalized partial record referencing a Pi-native session file."""

    source_path = Path(pi_session_path).expanduser()
    if not source_path.exists():
        raise FileNotFoundError(f"Pi session file not found: {source_path}")
    if not source_path.is_file():
        raise ValueError(f"Pi session path must be a file: {source_path}")

    resolved_path = source_path.resolve()
    stat = source_path.stat()
    path_hash = sha256(str(resolved_path).encode("utf-8")).hexdigest()
    active_path = init_session(
        agent="pi",
        slug=slug or f"pi-reference-{path_hash[:12]}",
        root=root,
        goal="Reference a Pi-native session file without importing transcript content.",
        partial=True,
        machine=machine,
        now=now,
    )
    append_event(
        active_path,
        root=root,
        event_type="pi.session_reference",
        agent="pi",
        summary="Referenced a Pi-native session file without copying transcript content.",
        payload=_sanitize_metadata(
            {
                "adapter": "pi-session-reference",
                "source_filename": source_path.name,
                "source_file_size_bytes": stat.st_size,
                "source_mtime": _timestamp(datetime.fromtimestamp(stat.st_mtime, UTC)),
                "source_absolute_path_sha256": path_hash,
                "source_path_stored": False,
                "raw_content_imported": False,
            }
        ),
        now=now,
    )
    return finalize_session(
        active_path,
        root=root,
        summary_text=_pi_reference_summary(source_path.name, stat.st_size, summary),
    )


def state_dir(root: str | Path | None = None) -> Path:
    """Return the excluded directory used for automatic-capture state."""

    return resolve_session_root(root) / ".in-progress" / PROJECT_NAME / ".state"


def prune_auto_capture_state(
    *,
    root: str | Path | None = None,
    dry_run: bool = False,
) -> list[PrunedState]:
    """Remove stale automatic-capture state files without touching records."""

    directory = state_dir(root)
    if not directory.exists():
        return []

    pruned: list[PrunedState] = []
    for path in sorted(directory.glob("*.json")):
        if not path.is_file():
            continue
        reason = _stale_state_reason(path, root=root)
        if reason is None:
            continue
        if not dry_run:
            path.unlink(missing_ok=True)
        pruned.append(PrunedState(path=path, reason=reason, removed=not dry_run))
    return pruned


def start_auto_capture(
    *,
    agent: str,
    slug: str,
    platform_session_id: str | None = None,
    root: str | Path | None = None,
    goal: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    partial: bool = True,
    machine: str | None = None,
    now: datetime | None = None,
) -> AutoCaptureState:
    """Create an active partial record and state mapping for an adapter."""

    safe_agent = _safe_component(agent, "agent")
    if platform_session_id is not None:
        existing_state_path = _state_path(root, safe_agent, platform_session_id)
        existing = _existing_live_state(
            existing_state_path,
            platform_session_id=platform_session_id,
            root=root,
        )
        if existing is not None:
            append_event(
                existing.active_path,
                root=root,
                event_type="auto_capture.resumed",
                agent=safe_agent,
                summary=f"Automatic capture reused existing state for {safe_agent}.",
                payload={
                    "capture_complete": not existing.partial,
                    "metadata": _sanitize_metadata(metadata or {}),
                    "platform_session_id": _public_session_id(existing.platform_session_id),
                },
                now=now,
            )
            return existing
        if existing_state_path.exists():
            existing_state_path.unlink()

    active_path = init_session(
        agent=agent,
        slug=slug,
        root=root,
        goal=goal,
        partial=partial,
        machine=machine,
        now=now,
    )
    session_id = platform_session_id or active_path.stem
    public_session_id = _public_session_id(session_id)

    append_event(
        active_path,
        root=root,
        event_type="auto_capture.started",
        agent=safe_agent,
        summary=f"Automatic capture started for {safe_agent}.",
        payload={
            "capture_complete": not partial,
            "metadata": _sanitize_metadata(metadata or {}),
            "platform_session_id": public_session_id,
        },
        now=now,
    )

    path = _state_path(root, safe_agent, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "version": STATE_VERSION,
        "agent": safe_agent,
        "platform_session_id": public_session_id,
        "active_path": str(active_path),
        "partial": partial,
        "updated_at": _timestamp(now),
    }
    _write_state(path, state)
    return AutoCaptureState(
        agent=safe_agent,
        platform_session_id=session_id,
        active_path=active_path,
        state_path=path,
        partial=partial,
    )


def append_auto_event(
    *,
    event_type: str,
    root: str | Path | None = None,
    active: str | Path | None = None,
    agent: str | None = None,
    platform_session_id: str | None = None,
    summary: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> Path:
    """Append one conservative automatic-capture event."""

    active_path = _active_from_args(root=root, active=active, agent=agent, session_id=platform_session_id)
    payload = _sanitize_metadata(metadata or {})
    return append_event(
        active_path,
        root=root,
        event_type=event_type,
        agent=agent,
        summary=summary,
        payload=payload,
        now=now,
    )


def stop_auto_capture(
    *,
    root: str | Path | None = None,
    active: str | Path | None = None,
    agent: str | None = None,
    platform_session_id: str | None = None,
    summary: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> SessionRecord:
    """Append an end marker, finalize the active record, and remove state."""

    active_path, state_path = _active_and_state_from_args(
        root=root,
        active=active,
        agent=agent,
        session_id=platform_session_id,
    )
    event_agent = agent
    if event_agent is None and state_path is not None:
        state = _read_state(state_path)
        event_agent = str(state.get("agent") or "")

    append_event(
        active_path,
        root=root,
        event_type="auto_capture.ended",
        agent=event_agent or None,
        summary="Automatic capture ended.",
        payload=_sanitize_metadata(metadata or {}),
        now=now,
    )

    record = finalize_session(
        active_path,
        root=root,
        summary_text=summary if summary is not None else _default_summary(event_agent, metadata),
    )
    if state_path is not None and state_path.exists():
        state_path.unlink()
    return record


def handle_claude_hook(
    payload: Mapping[str, Any],
    *,
    root: str | Path | None = None,
    machine: str | None = None,
    now: datetime | None = None,
) -> HookResult:
    """Handle a Claude Code hook payload without emitting raw prompt/tool data."""

    event_name = str(payload.get("hook_event_name") or "")
    session_id = str(payload.get("session_id") or "")
    if not event_name or not session_id:
        return HookResult(message="ignored Claude hook without hook_event_name or session_id")

    if event_name == "SessionStart":
        cwd = _path_name(payload.get("cwd")) or "claude"
        state = start_auto_capture(
            agent="claude",
            slug=f"{cwd}-{_short_id(session_id)}",
            platform_session_id=session_id,
            root=root,
            goal=f"Claude Code session in {cwd}",
            metadata=_metadata_for_hook(payload),
            partial=True,
            machine=machine,
            now=now,
        )
        return HookResult(active_path=state.active_path)

    if event_name == "SessionEnd":
        metadata = _metadata_for_hook(payload)
        try:
            record = stop_auto_capture(
                root=root,
                agent="claude",
                platform_session_id=session_id,
                metadata=metadata,
                summary=_default_summary("claude", metadata),
                now=now,
            )
        except FileNotFoundError:
            return HookResult(message=f"ignored Claude SessionEnd with no active state: {session_id}")
        return HookResult(record=record)

    try:
        active_path = append_auto_event(
            root=root,
            agent="claude",
            platform_session_id=session_id,
            event_type=f"claude.{_safe_component(event_name, 'hook event').lower()}",
            summary=f"Claude Code hook observed: {event_name}.",
            metadata=_metadata_for_hook(payload),
            now=now,
        )
    except FileNotFoundError:
        return HookResult(message=f"ignored Claude {event_name} with no active state: {session_id}")
    return HookResult(active_path=active_path)


def run_wrapped_agent(
    *,
    agent: str,
    slug: str,
    command: Sequence[str],
    root: str | Path | None = None,
    goal: str | None = None,
) -> int:
    """Run a command while recording partial start/end metadata."""

    if not command:
        raise ValueError("wrap requires a command after --")

    state = start_auto_capture(
        agent=agent,
        slug=slug,
        root=root,
        goal=goal,
        metadata={"adapter": "wrapper", "argv": _redacted_argv(command)},
        partial=True,
    )
    return_code = 1
    try:
        completed = subprocess.run(command, check=False)
        return_code = completed.returncode
        return return_code
    finally:
        stop_auto_capture(
            root=root,
            agent=agent,
            platform_session_id=state.platform_session_id,
            metadata={"adapter": "wrapper", "return_code": return_code},
        )


def read_hook_json(text: str) -> dict[str, Any]:
    """Parse hook stdin as a JSON object."""

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"hook input must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("hook input must be a JSON object")
    return parsed


def _active_from_args(
    *,
    root: str | Path | None,
    active: str | Path | None,
    agent: str | None,
    session_id: str | None,
) -> Path:
    active_path, _ = _active_and_state_from_args(
        root=root,
        active=active,
        agent=agent,
        session_id=session_id,
    )
    return active_path


def _active_and_state_from_args(
    *,
    root: str | Path | None,
    active: str | Path | None,
    agent: str | None,
    session_id: str | None,
) -> tuple[Path, Path | None]:
    if active is not None:
        return resolve_active_path(active, root=root), None
    if not agent or not session_id:
        raise ValueError("provide either --active or both --agent and --session-id")

    path = _state_path(root, agent, session_id)
    try:
        state = _read_state(path)
    except (ValueError, json.JSONDecodeError) as exc:
        if path.exists():
            path.unlink()
        raise FileNotFoundError(f"invalid auto-capture state removed: {path}") from exc

    active_path_value = state.get("active_path")
    if not active_path_value:
        path.unlink()
        raise FileNotFoundError(f"invalid auto-capture state removed: {path}")

    active_path = Path(str(active_path_value))
    if not active_path.exists():
        path.unlink()
        raise FileNotFoundError(f"active session not found from state: {active_path}")
    return resolve_active_path(active_path, root=root), path


def _state_path(root: str | Path | None, agent: str, session_id: str) -> Path:
    return state_dir(root) / f"{_safe_component(agent, 'agent')}-{_session_id_storage_key(session_id)}.json"


def _read_state(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        parsed = json.load(handle)
    if not isinstance(parsed, dict):
        raise ValueError(f"invalid auto-capture state file: {path}")
    return parsed


def _stale_state_reason(path: Path, *, root: str | Path | None) -> str | None:
    try:
        state = _read_state(path)
    except OSError:
        return "unreadable-state"
    except json.JSONDecodeError:
        return "invalid-json"
    except ValueError:
        return "invalid-state"

    active_path_value = state.get("active_path")
    if not active_path_value:
        return "missing-active-path"

    active_path = Path(str(active_path_value))
    if not active_path.exists():
        return "active-not-found"

    try:
        resolve_active_path(active_path, root=root)
    except (FileNotFoundError, ValueError):
        return "non-active-record"

    return None


def _existing_live_state(
    path: Path,
    *,
    platform_session_id: str,
    root: str | Path | None,
) -> AutoCaptureState | None:
    if not path.exists():
        return None
    try:
        state = _read_state(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None

    active_path = Path(str(state.get("active_path") or ""))
    agent = str(state.get("agent") or "")
    if not agent:
        return None

    try:
        active_path = resolve_active_path(active_path, root=root)
    except (OSError, ValueError):
        return None

    return AutoCaptureState(
        agent=agent,
        platform_session_id=platform_session_id,
        active_path=active_path,
        state_path=path,
        partial=bool(state.get("partial", True)),
    )


def _write_state(path: Path, state: Mapping[str, Any]) -> None:
    temp_path = path.with_name(f"{path.name}.partial")
    if temp_path.exists():
        temp_path.unlink()
    with temp_path.open("x", encoding="utf-8") as handle:
        json.dump(dict(state), handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
    temp_path.replace(path)


def _metadata_for_hook(payload: Mapping[str, Any]) -> dict[str, Any]:
    event_name = str(payload.get("hook_event_name") or "")
    metadata: dict[str, Any] = {
        "adapter": "claude-hook",
        "hook_event_name": event_name,
        "session_id": payload.get("session_id"),
        "transcript_file": _path_name(payload.get("transcript_path")),
        "cwd_name": _path_name(payload.get("cwd")),
    }

    for key in ("source", "model", "agent_type", "permission_mode", "reason", "tool_name", "tool_use_id"):
        if key in payload:
            metadata[key] = payload[key]

    if "prompt" in payload:
        metadata["prompt"] = {"redacted": True, "characters": len(str(payload["prompt"]))}
    if "last_assistant_message" in payload:
        metadata["last_assistant_message"] = {
            "redacted": True,
            "characters": len(str(payload["last_assistant_message"])),
        }
    if isinstance(payload.get("tool_input"), Mapping):
        metadata["tool_input_keys"] = sorted(str(key) for key in payload["tool_input"].keys())
    if isinstance(payload.get("tool_response"), Mapping):
        metadata["tool_response_keys"] = sorted(str(key) for key in payload["tool_response"].keys())

    return metadata


def _sanitize_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if _looks_sensitive(key_text):
            sanitized[key_text] = "[REDACTED]"
            continue
        if key_text == "argv" and isinstance(item, list | tuple):
            sanitized[key_text] = _redacted_argv([str(part) for part in item])
            continue
        sanitized[key_text] = _sanitize_value(item)
    return sanitized


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _sanitize_metadata(value)
    if isinstance(value, list | tuple):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str) and _looks_sensitive(value):
        return "[REDACTED]"
    return value


def _looks_sensitive(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in ("api_key", "apikey", "secret", "token", "password", "credential"))


def _redacted_argv(command: Sequence[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for arg in command:
        if redact_next:
            redacted.append("[REDACTED]")
            redact_next = False
            continue
        if _arg_assigns_sensitive_value(arg):
            key, _ = arg.split("=", 1)
            redacted.append(f"{key}=[REDACTED]")
            continue
        if _arg_requests_sensitive_value(arg):
            redacted.append(arg)
            redact_next = True
            continue
        redacted.append(arg)
    return redacted


def _arg_assigns_sensitive_value(value: str) -> bool:
    if "=" not in value:
        return False
    key, _ = value.split("=", 1)
    return _looks_sensitive(key)


def _arg_requests_sensitive_value(value: str) -> bool:
    return value.startswith("-") and _looks_sensitive(value)


def _default_summary(agent: str | None, metadata: Mapping[str, Any] | None = None) -> str:
    agent_name = agent or "agent"
    reason = ""
    if metadata and metadata.get("reason"):
        reason = f"\n\nEnd reason: {metadata['reason']}."
    return (
        "# Summary\n\n"
        f"Automatic {agent_name} capture finalized.\n\n"
        "This record is partial: the adapter captured lifecycle metadata, not a complete raw transcript."
        f"{reason}\n"
    )


def _pi_reference_summary(filename: str, size: int, summary: str | None = None) -> str:
    safe_filename = str(_sanitize_value(_table_safe(filename)))
    safe_summary = _sanitize_summary_lines(summary)
    extra = f"\n\n{safe_summary}\n" if safe_summary else "\n"
    return (
        "# Summary\n\n"
        "This record is a reference to a Pi-native session file, not a transcript import.\n\n"
        f"- Source filename: {safe_filename}\n"
        f"- Source file size: {size} bytes\n"
        "- Raw Pi session content copied: no\n"
        f"{extra}"
    )


def _sanitize_summary_lines(summary: str | None) -> str:
    if not summary or not summary.strip():
        return ""
    return "\n".join(str(_sanitize_value(line)) for line in summary.strip().splitlines())


def _table_safe(value: str) -> str:
    return " ".join(value.split())


def _safe_component(value: str, name: str) -> str:
    normalized = _SAFE_ID_RE.sub("-", value.strip()).strip("-._")
    if not normalized:
        raise ValueError(f"{name} must contain at least one filename-safe character")
    return normalized


def _path_name(value: Any) -> str | None:
    if not value:
        return None
    return Path(str(value)).name


def _short_id(value: str) -> str:
    return _session_id_storage_key(value)[:12]


def _session_id_storage_key(value: str) -> str:
    if _looks_sensitive(value):
        return f"redacted-{sha256(value.encode('utf-8')).hexdigest()[:16]}"
    return _safe_component(value, "session id")


def _public_session_id(value: str) -> str:
    if _looks_sensitive(value):
        return f"[REDACTED:{sha256(value.encode('utf-8')).hexdigest()[:16]}]"
    return value


def _timestamp(now: datetime | None = None) -> str:
    current = datetime.now(UTC) if now is None else now
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat()
