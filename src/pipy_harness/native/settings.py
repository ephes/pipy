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
    # Pi type-guards on `typeof === "boolean"`, so a non-boolean websockets
    # value is left untouched rather than minting a fabricated transport.
    if (
        "websockets" in out
        and "transport" not in out
        and isinstance(out["websockets"], bool)
    ):
        websockets = out.pop("websockets")
        out["transport"] = "websocket" if websockets else "sse"

    # retry.maxDelayMs -> retry.provider.maxRetryDelayMs. The legacy key is
    # deleted unconditionally whenever retry is an object; the value is copied
    # only when it is numeric (Pi type-guards on `typeof === "number"`) and the
    # replacement is absent (Pi treats both undefined and null as absent).
    retry = out.get("retry")
    if isinstance(retry, dict) and "maxDelayMs" in retry:
        legacy = retry.pop("maxDelayMs")
        provider = retry.get("provider")
        if not isinstance(provider, dict):
            provider = {}
            retry["provider"] = provider
        is_number = isinstance(legacy, (int, float)) and not isinstance(legacy, bool)
        if is_number and provider.get("maxRetryDelayMs") is None:
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
        # A scalar (or absent) intermediate cannot hold a nested key, so it is
        # replaced with a fresh object. This is the one place a nested write may
        # drop a pre-existing non-dict value at the same key; unrelated sibling
        # top-level keys are still preserved by the surrounding re-read-and-merge.
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
        base_defaults: dict[str, Any] | None = None,
    ) -> None:
        self.global_path = Path(global_path)
        self.project_path = Path(project_path) if project_path is not None else None
        self._env = env if env is not None else os.environ
        self._overrides = copy.deepcopy(overrides) if overrides else {}
        # Lowest-precedence layer (below the global file). Imported local-state
        # values live here so they surface through settings when a key is unset
        # while never overriding a value the user set in a settings file.
        self._base_defaults = copy.deepcopy(base_defaults) if base_defaults else {}
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
        base_defaults: dict[str, Any] | None = None,
    ) -> "SettingsManager":
        return cls(
            global_path=global_settings_path(env=env, home_dir=home_dir),
            project_path=project_settings_path(workspace_root),
            env=env,
            overrides=overrides,
            base_defaults=base_defaults,
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
        merged = deep_merge_settings({}, self._base_defaults)
        merged = deep_merge_settings(merged, self._raw.get(SCOPE_GLOBAL, {}))
        merged = deep_merge_settings(merged, self._raw.get(SCOPE_PROJECT, {}))
        if self._overrides:
            merged = deep_merge_settings(merged, self._overrides)
        return merged

    def raw_scope(self, scope: str) -> dict[str, Any]:
        return copy.deepcopy(self._raw.get(scope, {}))

    def merged_file_settings(self) -> dict[str, Any]:
        """Merge of the global+project settings *files* only.

        Excludes both the ``base_defaults`` (imported local-state) layer and the
        CLI/env override layer, so callers can read what the user actually wrote
        in a ``settings.json`` — used by the CLI to resolve provider/model/theme
        precedence (file value wins over the legacy store; store remains the
        fallback) without the store value masking an unset file key.
        """

        merged = deep_merge_settings({}, self._raw.get(SCOPE_GLOBAL, {}))
        return deep_merge_settings(merged, self._raw.get(SCOPE_PROJECT, {}))

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

    # --- typed accessors ---------------------------------------------------

    def _get(self, key: str) -> Any:
        return self.effective().get(key)

    def _get_str(self, key: str) -> str | None:
        value = self._get(key)
        return value if isinstance(value, str) else None

    def _get_bool(self, key: str, *, default: bool = False) -> bool:
        value = self._get(key)
        return value if isinstance(value, bool) else default

    def _get_int_in_range(
        self, key: str, *, low: int, high: int, default: int
    ) -> int:
        value = self._get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            return default
        return value if low <= value <= high else default

    def get_default_provider(self) -> str | None:
        return self._get_str("defaultProvider")

    def get_default_model(self) -> str | None:
        return self._get_str("defaultModel")

    def get_theme(self) -> str | None:
        return self._get_str("theme")

    def get_quiet_startup(self) -> bool:
        return self._get_bool("quietStartup")

    def get_hide_thinking_block(self) -> bool:
        return self._get_bool("hideThinkingBlock")

    def get_default_thinking_level(self) -> str | None:
        value = self._get_str("defaultThinkingLevel")
        if value in {"off", "minimal", "low", "medium", "high", "xhigh"}:
            return value
        return None

    def get_enabled_models(self) -> list[str]:
        value = self._get("enabledModels")
        if isinstance(value, list):
            return [entry for entry in value if isinstance(entry, str)]
        return []

    def get_editor_padding_x(self) -> int:
        return self._get_int_in_range("editorPaddingX", low=0, high=3, default=0)

    def get_autocomplete_max_visible(self) -> int:
        return self._get_int_in_range("autocompleteMaxVisible", low=3, high=20, default=5)

    def get_session_dir(self) -> str | None:
        return self._get_str("sessionDir")

    def get_http_idle_timeout_ms(self) -> int | None:
        """Return the HTTP idle timeout in ms (``0`` disables); ``None`` if unset.

        Mirrors Pi's ``getHttpIdleTimeoutMs``: an invalid (non-int or negative)
        value raises a clear error at read time rather than at load.
        """

        value = self._get("httpIdleTimeoutMs")
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(
                f"httpIdleTimeoutMs must be a non-negative integer; got {value!r}"
            )
        return value

    def get_prompt_history_enabled(self) -> bool:
        value = self._get("promptHistory")
        if isinstance(value, dict):
            return value.get("enabled") is True
        return False

    def set_default_provider(self, value: str, *, scope: str = SCOPE_GLOBAL) -> None:
        self.set_value("defaultProvider", value, scope=scope)

    def set_default_model(self, value: str, *, scope: str = SCOPE_GLOBAL) -> None:
        self.set_value("defaultModel", value, scope=scope)

    def set_theme(self, value: str, *, scope: str = SCOPE_GLOBAL) -> None:
        self.set_value("theme", value, scope=scope)

    def set_prompt_history_enabled(
        self, value: bool, *, scope: str = SCOPE_GLOBAL
    ) -> None:
        self.set_value("promptHistory.enabled", bool(value), scope=scope)

    def set_enabled_models(
        self, models: list[str], *, scope: str = SCOPE_GLOBAL
    ) -> None:
        self.set_value("enabledModels", list(models), scope=scope)

    # --- delivery / transport ---------------------------------------------

    def _get_choice(self, key: str, choices: set[str], default: str) -> str:
        value = self._get_str(key)
        return value if value in choices else default

    def get_transport(self) -> str:
        return self._get_choice("transport", {"auto", "sse", "websocket"}, "auto")

    def get_steering_mode(self) -> str:
        return self._get_choice("steeringMode", {"all", "one-at-a-time"}, "one-at-a-time")

    def get_follow_up_mode(self) -> str:
        return self._get_choice("followUpMode", {"all", "one-at-a-time"}, "one-at-a-time")

    # --- nested objects (compaction / retry / branchSummary) --------------

    def _get_nested(self, top: str, key: str) -> Any:
        node = self._get(top)
        return node.get(key) if isinstance(node, dict) else None

    def _nested_bool(self, top: str, key: str, *, default: bool) -> bool:
        value = self._get_nested(top, key)
        return value if isinstance(value, bool) else default

    def _nested_int(self, top: str, key: str, *, default: int) -> int:
        value = self._get_nested(top, key)
        if isinstance(value, bool) or not isinstance(value, int):
            return default
        return value

    def get_compaction_enabled(self) -> bool:
        return self._nested_bool("compaction", "enabled", default=True)

    def get_compaction_reserve_tokens(self) -> int:
        return self._nested_int("compaction", "reserveTokens", default=16384)

    def get_compaction_keep_recent_tokens(self) -> int:
        return self._nested_int("compaction", "keepRecentTokens", default=20000)

    def get_retry_enabled(self) -> bool:
        return self._nested_bool("retry", "enabled", default=True)

    def get_retry_max_retries(self) -> int:
        return self._nested_int("retry", "maxRetries", default=3)

    def get_retry_base_delay_ms(self) -> int:
        return self._nested_int("retry", "baseDelayMs", default=2000)

    def get_retry_provider_max_retry_delay_ms(self) -> int:
        retry = self._get("retry")
        provider = retry.get("provider") if isinstance(retry, dict) else None
        if isinstance(provider, dict):
            value = provider.get("maxRetryDelayMs")
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return 60000

    def get_branch_summary_reserve_tokens(self) -> int:
        return self._nested_int("branchSummary", "reserveTokens", default=16384)

    def get_branch_summary_skip_prompt(self) -> bool:
        return self._nested_bool("branchSummary", "skipPrompt", default=False)


def settings_report_lines(manager: SettingsManager) -> list[str]:
    """Safe, human-readable resolved-settings lines for the ``/settings`` view.

    Reports the resolved (effective) values for the delivery/transport,
    compaction/retry/branch-summary, and display settings so the surface shows
    what is in effect — including keys pipy accepts and round-trips but does not
    yet honor. Contains no secrets (auth stays in the dedicated auth stores).
    """

    theme = manager.get_theme() or "(default)"
    enabled_models = manager.get_enabled_models()
    models_text = ", ".join(enabled_models) if enabled_models else "(full catalog)"
    return [
        "  settings (resolved):",
        f"    theme: {theme}",
        f"    quietStartup: {manager.get_quiet_startup()}",
        f"    hideThinkingBlock: {manager.get_hide_thinking_block()}",
        f"    promptHistory.enabled: {manager.get_prompt_history_enabled()}",
        f"    transport: {manager.get_transport()}",
        f"    steering: {manager.get_steering_mode()}",
        f"    followUp: {manager.get_follow_up_mode()}",
        f"    scopedModels: {models_text}",
        "    compaction: "
        f"enabled={manager.get_compaction_enabled()}, "
        f"reserveTokens={manager.get_compaction_reserve_tokens()}, "
        f"keepRecentTokens={manager.get_compaction_keep_recent_tokens()}",
        "    retry: "
        f"enabled={manager.get_retry_enabled()}, "
        f"maxRetries={manager.get_retry_max_retries()}, "
        f"baseDelayMs={manager.get_retry_base_delay_ms()}, "
        f"provider.maxRetryDelayMs={manager.get_retry_provider_max_retry_delay_ms()}",
        "    branchSummary: "
        f"reserveTokens={manager.get_branch_summary_reserve_tokens()}, "
        f"skipPrompt={manager.get_branch_summary_skip_prompt()}",
    ]


def local_state_base_defaults(
    *,
    provider: str | None = None,
    model: str | None = None,
    theme: str | None = None,
    prompt_history_enabled: bool | None = None,
) -> dict[str, Any]:
    """Build a base-defaults dict from existing local-state store values.

    Used to surface ``NativeDefaultsStore`` / ``NativeThemeStore`` /
    ``PromptHistoryStore`` values through the settings system as the lowest
    layer, so they show through when no settings file sets the key — without
    rewriting or breaking the runtime-state files. Absent values are omitted so
    they do not mask higher layers.
    """

    base: dict[str, Any] = {}
    if provider is not None:
        base["defaultProvider"] = provider
    if model is not None:
        base["defaultModel"] = model
    if theme is not None:
        base["theme"] = theme
    if prompt_history_enabled is not None:
        base["promptHistory"] = {"enabled": bool(prompt_history_enabled)}
    return base
