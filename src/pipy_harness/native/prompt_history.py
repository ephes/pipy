"""Local-only persistent prompt-history store for the native REPL.

Holds submitted user prompts so a fresh product-TUI session can recall them
with Up/Down. This is local pipy state under the user's state dir — it is
deliberately **not** the metadata-first session archive (which never stores
prompt bodies), and the two are independent.

Persistence is opt-in and controlled from the product-TUI ``/settings`` dialog:

- When disabled (the default), no prompts are written and a fresh session does
  not seed its recall buffer from disk. In-memory per-session recall still
  works regardless.
- When enabled, submitted prompts are appended (blank and consecutive
  duplicates suppressed, capped to a bounded depth) and a fresh session seeds
  recall from the saved entries.
- ``clear()`` wipes the saved entries (keeping the enabled flag) so a later
  fresh session recalls nothing.

The file is written atomically with private (owner-only) permissions, mirroring
``NativeDefaultsStore``.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

_SCHEMA = "pipy.prompt-history"
_SCHEMA_VERSION = 1
_DEFAULT_MAX_ENTRIES = 500


def default_prompt_history_path() -> Path:
    configured = os.environ.get("PIPY_PROMPT_HISTORY_PATH")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "state" / "pipy" / "prompt-history.json"


class PromptHistoryStore:
    """Private JSON store for opt-in cross-session prompt recall."""

    def __init__(
        self, path: Path | None = None, *, max_entries: int = _DEFAULT_MAX_ENTRIES
    ) -> None:
        self.path = path or default_prompt_history_path()
        self._max_entries = max(1, max_entries)
        self._enabled = False
        self._entries: list[str] = []
        self._load()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def entries(self) -> list[str]:
        return list(self._entries)

    def set_enabled(self, value: bool) -> None:
        value = bool(value)
        if value == self._enabled:
            return
        previous = self._enabled
        self._enabled = value
        if not self._save():
            # Keep the in-memory state consistent with what is actually on
            # disk: if we could not persist a disable, a fresh session would
            # still recall, so do not pretend the toggle took effect.
            self._enabled = previous

    def record(self, prompt: str) -> None:
        """Persist ``prompt`` when enabled, suppressing blanks/duplicates.

        Mirrors the in-memory recall contract: the literal prompt is stored (so
        a multi-line prompt round-trips), the blank check is on the stripped
        form, and a prompt identical to the most recent entry is dropped.
        """

        if not self._enabled:
            return
        if not prompt.strip():
            return
        if self._entries and self._entries[-1] == prompt:
            return
        snapshot = list(self._entries)
        self._entries.append(prompt)
        self._cap()
        if not self._save():
            self._entries = snapshot

    def clear(self) -> None:
        if not self._entries:
            return
        snapshot = list(self._entries)
        self._entries = []
        if not self._save():
            # A failed clear must not leave the live store claiming "0 saved"
            # while the on-disk file still recalls the prompts.
            self._entries = snapshot

    def _cap(self) -> None:
        overflow = len(self._entries) - self._max_entries
        if overflow > 0:
            del self._entries[0:overflow]

    def _load(self) -> None:
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(body, dict):
            return
        if body.get("schema") != _SCHEMA or body.get("schema_version") != _SCHEMA_VERSION:
            return
        # Opt-in is strict: only a literal JSON boolean ``true`` enables the
        # feature. A truthy-but-non-boolean value (e.g. the string "false", or
        # 1) from a hand-edited/foreign file must not silently opt in.
        self._enabled = body.get("enabled") is True
        raw = body.get("entries")
        if isinstance(raw, list):
            self._entries = [
                entry for entry in raw if isinstance(entry, str) and entry.strip()
            ]
            self._cap()

    def _save(self) -> bool:
        """Persist the current state atomically; return whether it succeeded.

        Prompt bodies are sensitive, so the temp file is created owner-only
        (``mkstemp`` opens with mode ``0o600`` before any bytes are written, so
        there is no permissive-umask window) under a unique name (no fixed
        ``.partial`` path that a symlink or concurrent writer could race), then
        atomically renamed into place. A read-only or unwritable state dir must
        never crash the REPL: on failure the caller reverts in-memory state so
        it stays consistent with what is on disk.
        """

        payload = {
            "schema": _SCHEMA,
            "schema_version": _SCHEMA_VERSION,
            "enabled": self._enabled,
            "entries": self._entries,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.path.parent.chmod(0o700)
            except OSError:
                pass
            fd, temporary_name = tempfile.mkstemp(
                dir=str(self.path.parent),
                prefix=f".{self.path.name}.",
                suffix=".partial",
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(
                        payload, handle, ensure_ascii=False, separators=(",", ":")
                    )
                    handle.write("\n")
                os.replace(temporary_path, self.path)
            except OSError:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
                return False
            try:
                self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            return True
        except OSError:
            return False
