"""Opt-in transcript sidecar for the native tool-loop REPL.

`TranscriptSink` writes raw loop turns to
`~/.local/state/pipy/transcripts/<session-id>.jsonl`. The sidecar lives
outside the pipy session archive (`~/.local/state/pipy/sessions`) and is
explicitly excluded from `pipy-session list/search/inspect`. The
metadata-first archive contracts are unaffected; the sink only writes
when `--archive-transcript` is supplied.

The file is treated as sensitive content. Each line is a JSON object
with a stable `type` discriminator and a `recorded_at` UTC timestamp.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

TRANSCRIPT_DIR_ENV = "PIPY_TRANSCRIPT_DIR"
DEFAULT_TRANSCRIPT_DIR = Path("~/.local/state/pipy/transcripts")
SENSITIVE_MARKER = "pipy-transcript-sidecar"
_SAFE_TRANSCRIPT_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def default_transcript_dir() -> Path:
    override = os.environ.get(TRANSCRIPT_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return DEFAULT_TRANSCRIPT_DIR.expanduser()


def new_transcript_id() -> str:
    return f"pipy-transcript-{uuid.uuid4()}"


@dataclass
class TranscriptSink:
    """Append-only JSONL writer for the native tool-loop sidecar.

    The sink is opened lazily on the first `append`; if no event ever
    fires, no file is created. Closing flushes and closes the underlying
    handle so callers can use `with` semantics through `close()`.
    """

    transcript_id: str = field(default_factory=new_transcript_id)
    directory: Path = field(default_factory=default_transcript_dir)
    _handle: Any = field(default=None, init=False, repr=False)
    _path: Path | None = field(default=None, init=False, repr=False)
    _created: bool = field(default=False, init=False, repr=False)

    SUPPORTED_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset(
        {"user", "assistant", "tool_result", "diff", "session"}
    )

    @property
    def path(self) -> Path:
        if self._path is None:
            self._path = self.directory / _safe_transcript_filename(self.transcript_id)
        return self._path

    def append(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type not in self.SUPPORTED_EVENT_TYPES:
            raise ValueError(
                f"unsupported transcript event type: {event_type!r}"
            )
        if not isinstance(payload, dict):
            raise ValueError("transcript payload must be a dict")
        record = {
            "type": event_type,
            "recorded_at": datetime.now(UTC).isoformat(timespec="microseconds"),
            "sensitive_marker": SENSITIVE_MARKER,
            "payload": payload,
        }
        self._ensure_open()
        assert self._handle is not None
        self._handle.write(json.dumps(record, sort_keys=True) + "\n")
        self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def _ensure_open(self) -> None:
        if self._handle is not None:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        if not self._created:
            flags |= os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.path, flags, 0o600)
        self._created = True
        self._handle = os.fdopen(fd, "a", encoding="utf-8")


def _safe_transcript_filename(transcript_id: str) -> str:
    if (
        not transcript_id
        or transcript_id in {".", ".."}
        or not _SAFE_TRANSCRIPT_ID_RE.fullmatch(transcript_id)
    ):
        raise ValueError("transcript_id must be a filename-safe identifier")
    return f"{transcript_id}.jsonl"


__all__ = [
    "DEFAULT_TRANSCRIPT_DIR",
    "SENSITIVE_MARKER",
    "TRANSCRIPT_DIR_ENV",
    "TranscriptSink",
    "default_transcript_dir",
    "new_transcript_id",
]
