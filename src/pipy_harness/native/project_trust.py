"""Project trust storage, detection, and core decision resolution.

Trust gates project-owned settings and executable/configurable resources.  It
is deliberately not a sandbox: once a project is trusted, the normal pipy
runtime still has the permissions of the hosting process.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .settings import resolve_config_home

TRUST_FILENAME = "trust.json"
PROTECTED_PROJECT_ENTRIES = (
    "settings.json",
    "extensions",
    "skills",
    "templates",
    "commands",
    "SYSTEM.md",
    "APPEND_SYSTEM.md",
)

_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_INITIAL_BACKOFF_SECONDS = 0.02
_LOCK_MAX_BACKOFF_SECONDS = 0.2


class ProjectTrustError(RuntimeError):
    """A trust store could not be read or updated safely."""


@dataclass(frozen=True, slots=True)
class ProjectTrustEntry:
    path: Path
    decision: bool


def canonical_project_path(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def default_project_trust_path(
    *,
    env: dict[str, str] | os._Environ[str] | None = None,
    home_dir: Path | None = None,
) -> Path:
    return resolve_config_home(env=env, home_dir=home_dir) / TRUST_FILENAME


class _TrustFileLock:
    _UNKNOWN_OWNER_STALE_SECONDS = 5.0

    def __init__(self, target: Path) -> None:
        self._path = target.with_name(target.name + ".lock")
        self._fd: int | None = None

    def __enter__(self) -> "_TrustFileLock":
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise ProjectTrustError(
                f"could not create trust store directory {self._path.parent}: {exc}"
            ) from exc
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        delay = _LOCK_INITIAL_BACKOFF_SECONDS
        while True:
            try:
                self._fd = os.open(
                    str(self._path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
                try:
                    os.write(
                        self._fd,
                        json.dumps(
                            {"pid": os.getpid(), "created": time.time()}
                        ).encode("utf-8"),
                    )
                except OSError as exc:
                    os.close(self._fd)
                    self._fd = None
                    try:
                        self._path.unlink()
                    except OSError:
                        pass
                    raise ProjectTrustError(
                        f"could not initialize trust store lock {self._path}: {exc}"
                    ) from exc
                return self
            except FileExistsError as exc:
                if self._remove_stale_lock():
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProjectTrustError(
                        f"could not acquire trust store lock {self._path}"
                    ) from exc
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, _LOCK_MAX_BACKOFF_SECONDS)
            except OSError as exc:
                raise ProjectTrustError(
                    f"could not acquire trust store lock {self._path}: {exc}"
                ) from exc
    def _remove_stale_lock(self) -> bool:
        """Best-effort recovery for a lock orphaned by a dead writer."""

        try:
            raw = self._path.read_text(encoding="utf-8")
            body = json.loads(raw)
            pid = body.get("pid") if isinstance(body, dict) else None
        except (OSError, UnicodeError, json.JSONDecodeError):
            pid = None
        stale = False
        if isinstance(pid, int) and pid > 0:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                stale = True
            except (PermissionError, OSError):
                stale = False
        else:
            try:
                stale = (
                    time.time() - self._path.stat().st_mtime
                    > self._UNKNOWN_OWNER_STALE_SECONDS
                )
            except OSError:
                return True
        if not stale:
            return False
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            return False
        return True

    def __exit__(self, *_exc: object) -> None:
        if self._fd is None:
            return
        try:
            os.close(self._fd)
        finally:
            try:
                self._path.unlink()
            except OSError:
                pass


def _read_store(path: Path) -> dict[str, bool | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ProjectTrustError(f"failed to read trust store {path}: {exc}") from exc
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ProjectTrustError(f"failed to read trust store {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProjectTrustError(f"invalid trust store {path}: expected an object")
    data: dict[str, bool | None] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or (
            not isinstance(value, bool) and value is not None
        ):
            raise ProjectTrustError(
                f"invalid trust store {path}: value for {key!r} must be true, false, or null"
            )
        data[key] = value
    return data


def _write_store(path: Path, data: dict[str, bool | None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    payload = {key: data[key] for key in sorted(data)}
    fd, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".partial"
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temp_path, path)
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
    except OSError as exc:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise ProjectTrustError(f"failed to write trust store {path}: {exc}") from exc


class ProjectTrustStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_project_trust_path()

    def get_entry(self, cwd: Path | str) -> ProjectTrustEntry | None:
        # Writers publish with atomic os.replace, so a reader observes either
        # the old complete JSON file or the new one. Avoiding the writer lock
        # means concurrent startups and an orphaned writer lock cannot force a
        # saved decision to fail closed spuriously.
        data = _read_store(self.path)
        current = canonical_project_path(cwd)
        while True:
            decision = data.get(str(current))
            if isinstance(decision, bool):
                return ProjectTrustEntry(path=current, decision=decision)
            if current.parent == current:
                return None
            current = current.parent

    def get(self, cwd: Path | str) -> bool | None:
        entry = self.get_entry(cwd)
        return None if entry is None else entry.decision

    def set(self, cwd: Path | str, decision: bool | None) -> None:
        self.set_many(((cwd, decision),))

    def set_many(
        self, updates: tuple[tuple[Path | str, bool | None], ...]
    ) -> None:
        for _path, decision in updates:
            if not isinstance(decision, bool) and decision is not None:
                raise TypeError("project trust decisions must be bool or None")
        with _TrustFileLock(self.path):
            data = _read_store(self.path)
            for raw_path, decision in updates:
                key = str(canonical_project_path(raw_path))
                if decision is None:
                    data.pop(key, None)
                else:
                    data[key] = decision
            _write_store(self.path, data)


def has_trust_requiring_project_resources(cwd: Path | str) -> bool:
    config_dir = canonical_project_path(cwd) / ".pipy"
    for name in PROTECTED_PROJECT_ENTRIES:
        try:
            (config_dir / name).stat()
        except FileNotFoundError:
            continue
        except OSError:
            # A protected source that cannot be inspected must never become
            # loadable through the no-resource short circuit.
            return True
        return True
    return False


DefaultProjectTrust = Literal["ask", "always", "never"]


def resolve_project_trusted(
    cwd: Path | str,
    *,
    trust_store: ProjectTrustStore,
    trust_override: bool | None = None,
    default_project_trust: DefaultProjectTrust = "ask",
    select: Callable[[Path], bool | None] | None = None,
    on_diagnostic: Callable[[str], None] | None = None,
) -> bool:
    """Resolve the core trust order for one final runtime directory.

    Extension-owned decisions and the product TUI selector are later slices;
    ``select`` is an injected synchronous seam used by direct callers/tests.
    """

    resolved = canonical_project_path(cwd)
    if trust_override is not None:
        return trust_override
    if not has_trust_requiring_project_resources(resolved):
        return True
    try:
        saved = trust_store.get(resolved)
    except ProjectTrustError as exc:
        if on_diagnostic is not None:
            on_diagnostic(str(exc))
        return False
    if saved is not None:
        return saved
    if default_project_trust == "always":
        return True
    if default_project_trust == "never":
        return False
    if select is not None:
        selected = select(resolved)
        return selected is True
    return False
