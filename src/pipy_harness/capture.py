"""Conservative capture helpers for the pipy harness."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class CapturePolicy:
    """Privacy policy for one harness run."""

    record_argv: bool = False
    record_stdout: bool = False
    record_stderr: bool = False
    record_file_paths: bool = False
    import_raw_transcript: bool = False
    workspace_path_mode: str = "basename_and_hash"


@dataclass(frozen=True, slots=True)
class WorkspaceDisplay:
    """Display-safe workspace identity."""

    name: str
    sha256: str


def workspace_display(path: Path) -> WorkspaceDisplay:
    """Return a basename plus hash without storing the full workspace path."""

    resolved = path.expanduser().resolve()
    name = sanitize_text(resolved.name or "workspace")
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()
    return WorkspaceDisplay(name=name, sha256=digest)


def safe_command_executable(command: Sequence[str]) -> str:
    """Return a display-safe executable name without preserving argv."""

    if not command:
        return ""
    name = Path(str(command[0])).name or str(command[0])
    return sanitize_text(name)


def sanitize_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    """Redact secret-looking keys and values before writing metadata."""

    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text == "paths" and isinstance(item, list | tuple):
            sanitized[key_text] = [sanitize_path(str(path)) for path in item]
            continue
        if looks_sensitive(key_text):
            sanitized[key_text] = "[REDACTED]"
            continue
        if key_text == "argv" and isinstance(item, list | tuple):
            sanitized[key_text] = redacted_argv([str(part) for part in item])
            continue
        sanitized[key_text] = sanitize_value(item)
    return sanitized


def sanitize_value(value: Any) -> Any:
    """Redact nested secret-looking values."""

    if isinstance(value, Mapping):
        return sanitize_metadata(value)
    if isinstance(value, list | tuple):
        return [sanitize_value(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def sanitize_text(value: str) -> str:
    """Collapse control whitespace and redact secret-looking text."""

    cleaned = " ".join(value.split())
    if looks_sensitive(cleaned):
        return "[REDACTED]"
    return cleaned


def sanitize_path(value: str) -> str:
    """Collapse control whitespace in an explicitly recorded path."""

    return " ".join(value.split())


def redacted_argv(command: Sequence[str]) -> list[str]:
    """Redact secret-looking argv values while preserving command shape."""

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


def collect_changed_file_paths(cwd: Path) -> tuple[str, ...]:
    """Collect changed git paths with porcelain output, returning paths only."""

    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return ()

    if completed.returncode != 0:
        return ()

    return _parse_git_status_z(completed.stdout)


def _parse_git_status_z(output: bytes) -> tuple[str, ...]:
    entries = [entry for entry in output.split(b"\0") if entry]
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        if len(entry) < 4:
            index += 1
            continue
        status = entry[:2]
        raw_path = entry[3:]
        paths.append(_safe_relative_path(raw_path))
        index += 1
        if status[:1] in {b"R", b"C"} or status[1:2] in {b"R", b"C"}:
            index += 1
    return tuple(sorted(dict.fromkeys(path for path in paths if path)))


def _safe_relative_path(raw_path: bytes) -> str:
    path = raw_path.decode("utf-8", errors="replace")
    return sanitize_path(path)


def looks_sensitive(value: str) -> bool:
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in ("api_key", "apikey", "secret", "token", "password", "credential")
    )


def _arg_assigns_sensitive_value(value: str) -> bool:
    if "=" not in value:
        return False
    key, _ = value.split("=", 1)
    return looks_sensitive(key)


def _arg_requests_sensitive_value(value: str) -> bool:
    return value.startswith("-") and looks_sensitive(value)
