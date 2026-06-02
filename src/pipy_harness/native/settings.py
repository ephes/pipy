"""Pi-style layered settings for the native runtime.

This module mirrors Pi's ``settings-manager.ts`` through pipy-owned Python
boundaries (dataclasses, stdlib JSON, the existing owner-private store
patterns). It is not a TypeScript port.

The settings system layers a global ``<config>/settings.json`` under the
``PIPY_CONFIG_HOME`` → ``${XDG_CONFIG_HOME}/pipy`` → ``~/.config/pipy`` chain and
a project ``<cwd>/.pipy/settings.json`` file. Loading is parse → migrate → cast
to typed accessors (no load-time JSON-schema validation); the project layer
overrides the global with a one-level deep merge, and CLI/env overrides apply as
a final layer. Unknown and not-yet-honored keys round-trip untouched.

See ``docs/settings-config.md`` for the full target specification.
"""

from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

from .workspace_context import resolve_global_instruction_root

SCOPE_GLOBAL = "global"
SCOPE_PROJECT = "project"

PROJECT_CONFIG_DIR_NAME = ".pipy"
SETTINGS_FILENAME = "settings.json"

# Lock acquisition is best-effort and must never deadlock a non-interactive run.
_LOCK_RETRIES = 10
_LOCK_BACKOFF_SECONDS = 0.02


def deep_merge_settings(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge ``override`` onto ``base`` with Pi's one-level semantics.

    Mirrors Pi's ``deepMergeSettings``: a top-level key whose value is a plain
    object in **both** layers is shallow-merged one level
    (``{**base[key], **override[key]}``); it is not recursed into. Top-level
    scalars, arrays, and deeper nested objects (e.g. ``retry.provider``) are
    replaced wholesale by the override. Neither input is mutated.
    """

    merged: dict[str, Any] = copy.deepcopy(base)
    for key, override_value in override.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(override_value, dict):
            merged[key] = {**base_value, **copy.deepcopy(override_value)}
        else:
            merged[key] = copy.deepcopy(override_value)
    return merged


def migrate_settings(raw: dict[str, Any]) -> dict[str, Any]:
    """Apply Pi's ``migrateSettings`` legacy migrations to a parsed settings dict.

    Three distinct deletion behaviors, mirrored exactly (see
    ``docs/settings-config.md``):

    - **Rename keys** (``queueMode`` → ``steeringMode``; ``websockets`` boolean
      → ``transport`` with ``True`` → ``"websocket"`` / ``False`` → ``"sse"``):
      applied only when the replacement key is absent. If the replacement
      already exists, the legacy key is left untouched.
    - **``retry.maxDelayMs``** → ``retry.provider.maxRetryDelayMs``: whenever
      ``retry`` is an object, ``maxDelayMs`` is deleted unconditionally; the
      legacy value is copied into ``retry.provider.maxRetryDelayMs`` only when
      that replacement is absent.
    - **``skills`` object form**: whenever ``skills`` is an object it is always
      replaced — with its ``customDirectories`` list when that list is present
      and non-empty, otherwise the ``skills`` key is deleted. Only the
      ``enableSkillCommands`` hoist is conditional (skipped when a top-level
      ``enableSkillCommands`` already exists).

    Idempotent; never drops unknown keys; does not mutate the input.
    """

    out = copy.deepcopy(raw)

    # Rename: queueMode -> steeringMode (only when replacement absent).
    if "queueMode" in out and "steeringMode" not in out:
        out["steeringMode"] = out.pop("queueMode")

    # Rename: websockets boolean -> transport (only when replacement absent).
    if "websockets" in out and "transport" not in out:
        websockets = out.pop("websockets")
        out["transport"] = "websocket" if websockets else "sse"

    # retry.maxDelayMs -> retry.provider.maxRetryDelayMs (unconditional delete).
    retry = out.get("retry")
    if isinstance(retry, dict) and "maxDelayMs" in retry:
        legacy = retry.pop("maxDelayMs")
        provider = retry.get("provider")
        if not isinstance(provider, dict):
            provider = {}
            retry["provider"] = provider
        if "maxRetryDelayMs" not in provider:
            provider["maxRetryDelayMs"] = legacy

    # skills object form: always replaced; enableSkillCommands hoist conditional.
    skills = out.get("skills")
    if isinstance(skills, dict):
        if "enableSkillCommands" in skills and "enableSkillCommands" not in out:
            out["enableSkillCommands"] = skills["enableSkillCommands"]
        custom = skills.get("customDirectories")
        if isinstance(custom, list) and custom:
            out["skills"] = list(custom)
        else:
            del out["skills"]

    return out


def resolve_config_home(
    *,
    env: dict[str, str] | os._Environ[str] | None = None,
    home_dir: Path | None = None,
) -> Path:
    """Return the global pipy config home for settings/keybindings files.

    Reuses the single shared config-home chain already used by
    ``workspace_context`` and the extension loader
    (``PIPY_CONFIG_HOME`` → ``${XDG_CONFIG_HOME}/pipy`` → ``~/.pipy`` when
    present → ``~/.config/pipy``) rather than inventing a second incompatible
    config root. Under the conformance gate ``PIPY_CONFIG_HOME`` is set, so the
    documented three-step chain and this shared resolver coincide.
    """

    return resolve_global_instruction_root(env=env, home_dir=home_dir)


def global_settings_path(
    *,
    env: dict[str, str] | os._Environ[str] | None = None,
    home_dir: Path | None = None,
) -> Path:
    return resolve_config_home(env=env, home_dir=home_dir) / SETTINGS_FILENAME


def project_settings_path(workspace_root: Path) -> Path:
    return workspace_root / PROJECT_CONFIG_DIR_NAME / SETTINGS_FILENAME


def _load_scope(path: Path) -> tuple[dict[str, Any], str | None]:
    """Load and migrate one settings scope.

    Returns ``(migrated_dict, error)``. A missing file is ``({}, None)``. A
    parse error or non-object body falls back to ``({}, message)`` so the scope
    is isolated and a safe diagnostic is available, matching Pi's
    ``tryLoadFromStorage`` / ``drainErrors``.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        return {}, f"could not read {path}: {exc}"
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, f"could not parse {path}: {exc}"
    if not isinstance(body, dict):
        return {}, f"settings file {path} is not a JSON object"
    return migrate_settings(body), None


class _FileLock:
    """Best-effort advisory lock via an exclusive sidecar file.

    Mirrors Pi's ``proper-lockfile`` posture (bounded retry on contention) with
    stdlib only. Acquisition never deadlocks a non-interactive run: after the
    bounded retries it proceeds without the lock rather than blocking forever.
    """

    def __init__(self, target: Path) -> None:
        self._lock_path = target.with_name(target.name + ".lock")
        self._fd: int | None = None

    def __enter__(self) -> "_FileLock":
        for _ in range(_LOCK_RETRIES):
            try:
                self._fd = os.open(
                    str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
                return self
            except FileExistsError:
                time.sleep(_LOCK_BACKOFF_SECONDS)
            except OSError:
                return self
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
        try:
            self._lock_path.unlink()
        except OSError:
            pass


def _apply_path(target: dict[str, Any], parts: list[str], value: Any) -> None:
    key = parts[0]
    if len(parts) == 1:
        target[key] = value
        return
    node = target.get(key)
    if not isinstance(node, dict):
        node = {}
        target[key] = node
    _apply_path(node, parts[1:], value)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    fd, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".partial"
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        try:
            temp_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        os.replace(temp_path, path)
    except OSError:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


class SettingsManager:
    """Layered global+project settings with Pi-equivalent semantics.

    Loads a global ``<config>/settings.json`` and a project
    ``<cwd>/.pipy/settings.json`` (no ``.claude/settings.json``), migrates each
    scope, deep-merges with project precedence, and applies a final CLI/env
    override layer. Writes are field-scoped, lock-guarded, preserve unknown and
    concurrently-written keys, and refuse to clobber a scope that failed to
    load.
    """

    def __init__(
        self,
        *,
        global_path: Path,
        project_path: Path | None = None,
        env: dict[str, str] | os._Environ[str] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> None:
        self.global_path = Path(global_path)
        self.project_path = Path(project_path) if project_path is not None else None
        self._env = env if env is not None else os.environ
        self._overrides = copy.deepcopy(overrides) if overrides else {}
        self._raw: dict[str, dict[str, Any]] = {}
        self._errors: dict[str, str] = {}
        self.reload()

    @classmethod
    def for_workspace(
        cls,
        workspace_root: Path,
        *,
        env: dict[str, str] | os._Environ[str] | None = None,
        home_dir: Path | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> "SettingsManager":
        return cls(
            global_path=global_settings_path(env=env, home_dir=home_dir),
            project_path=project_settings_path(workspace_root),
            env=env,
            overrides=overrides,
        )

    # --- loading -----------------------------------------------------------

    def reload(self) -> None:
        self._raw = {}
        self._errors = {}
        raw_global, err_global = _load_scope(self.global_path)
        self._raw[SCOPE_GLOBAL] = raw_global
        if err_global is not None:
            self._errors[SCOPE_GLOBAL] = err_global
        if self.project_path is not None:
            raw_project, err_project = _load_scope(self.project_path)
            self._raw[SCOPE_PROJECT] = raw_project
            if err_project is not None:
                self._errors[SCOPE_PROJECT] = err_project

    def load_errors(self) -> dict[str, str]:
        return dict(self._errors)

    def effective(self) -> dict[str, Any]:
        merged = deep_merge_settings({}, self._raw.get(SCOPE_GLOBAL, {}))
        merged = deep_merge_settings(merged, self._raw.get(SCOPE_PROJECT, {}))
        if self._overrides:
            merged = deep_merge_settings(merged, self._overrides)
        return merged

    def raw_scope(self, scope: str) -> dict[str, Any]:
        return copy.deepcopy(self._raw.get(scope, {}))

    # --- writing -----------------------------------------------------------

    def _path_for_scope(self, scope: str) -> Path:
        if scope == SCOPE_GLOBAL:
            return self.global_path
        if scope == SCOPE_PROJECT:
            if self.project_path is None:
                raise ValueError("no project settings path configured")
            return self.project_path
        raise ValueError(f"unknown settings scope: {scope!r}")

    def set_value(self, dotted_key: str, value: Any, *, scope: str = SCOPE_GLOBAL) -> None:
        """Persist one field (``key`` or ``top.nested``) into ``scope``.

        Re-reads the current on-disk file under a lock, merges only the modified
        field/nested key (preserving unknown and concurrently-written keys), and
        re-serializes pretty-printed. A scope that failed to load is never
        written back over.
        """

        if scope in self._errors:
            raise RuntimeError(
                f"refusing to write {scope} settings: scope failed to load "
                f"({self._errors[scope]})"
            )
        parts = dotted_key.split(".")
        path = self._path_for_scope(scope)
        with _FileLock(path):
            current, error = _load_scope(path)
            if error is not None:
                raise RuntimeError(
                    f"refusing to write {scope} settings: on-disk file failed "
                    f"to load ({error})"
                )
            _apply_path(current, parts, value)
            _atomic_write_json(path, current)
        # Reflect the change in memory so effective() is consistent.
        scope_raw = self._raw.setdefault(scope, {})
        _apply_path(scope_raw, parts, value)
