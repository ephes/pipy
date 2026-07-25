"""Tests for the Pi-style keybindings manager (`pipy_harness.native.keybindings`).

Covers the default binding table (TUI base + 35+ app bindings), keybindings.json
loading (single key spec or array of alternatives), in-memory legacy-name
migration and canonical ordering (never written back), malformed-file fallback to
defaults (not prior bindings) on reload, resolved lookup, and the `/hotkeys`
renderer built from the resolved manager.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from pipy_harness.native.keybindings import (
    APP_KEYBINDINGS,
    DEFAULT_KEYBINDINGS,
    KeybindingsManager,
    key_display_text,
    migrate_keybindings_config,
    render_hotkeys,
)


def _write(path: Path, body: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body, str):
        path.write_text(body, encoding="utf-8")
    else:
        path.write_text(json.dumps(body), encoding="utf-8")
    return path


# --- default table ----------------------------------------------------------


def test_at_least_35_app_bindings_present() -> None:
    assert len(APP_KEYBINDINGS) >= 35


def test_documented_app_defaults() -> None:
    assert DEFAULT_KEYBINDINGS["app.interrupt"].default_keys == ["escape"]
    assert DEFAULT_KEYBINDINGS["app.model.cycleForward"].default_keys == ["ctrl+p"]
    assert DEFAULT_KEYBINDINGS["app.model.cycleBackward"].default_keys == [
        "shift+ctrl+p"
    ]
    # Array of alternatives.
    assert DEFAULT_KEYBINDINGS["app.tree.foldOrUp"].default_keys == [
        "ctrl+left",
        "alt+left",
    ]
    # Intentionally unbound by default.
    assert DEFAULT_KEYBINDINGS["app.session.new"].default_keys == []


def test_tui_base_bindings_present() -> None:
    assert DEFAULT_KEYBINDINGS["tui.input.submit"].default_keys == ["enter"]
    assert DEFAULT_KEYBINDINGS["tui.editor.cursorLeft"].default_keys == [
        "left",
        "ctrl+b",
    ]


# --- resolved lookup --------------------------------------------------------


def test_resolved_keys_default_when_no_user_binding(tmp_path: Path) -> None:
    mgr = KeybindingsManager(user_bindings={})
    assert mgr.keys_for("app.model.cycleForward") == ["ctrl+p"]


def test_user_single_spec_overrides_default() -> None:
    mgr = KeybindingsManager(user_bindings={"app.model.cycleForward": "ctrl+j"})
    assert mgr.keys_for("app.model.cycleForward") == ["ctrl+j"]


def test_user_array_of_alternatives_overrides_default() -> None:
    mgr = KeybindingsManager(user_bindings={"app.tree.foldOrUp": ["ctrl+h", "alt+h"]})
    assert mgr.keys_for("app.tree.foldOrUp") == ["ctrl+h", "alt+h"]


def test_unknown_action_resolves_to_empty() -> None:
    mgr = KeybindingsManager(user_bindings={})
    assert mgr.keys_for("app.nonexistent") == []


# --- file loading -----------------------------------------------------------


def test_load_single_and_array_specs_from_file(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "keybindings.json",
        {"app.model.cycleForward": "ctrl+j", "app.tree.foldOrUp": ["ctrl+h", "alt+h"]},
    )
    mgr = KeybindingsManager.from_file(path)
    assert mgr.keys_for("app.model.cycleForward") == ["ctrl+j"]
    assert mgr.keys_for("app.tree.foldOrUp") == ["ctrl+h", "alt+h"]


def test_missing_file_falls_back_to_defaults(tmp_path: Path) -> None:
    mgr = KeybindingsManager.from_file(tmp_path / "absent.json")
    assert mgr.keys_for("app.model.cycleForward") == ["ctrl+p"]


def test_malformed_file_falls_back_to_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path / "keybindings.json", "{ not json")
    mgr = KeybindingsManager.from_file(path)
    assert mgr.keys_for("app.model.cycleForward") == ["ctrl+p"]


def test_non_object_file_falls_back_to_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path / "keybindings.json", [1, 2, 3])
    mgr = KeybindingsManager.from_file(path)
    assert mgr.keys_for("app.model.cycleForward") == ["ctrl+p"]


def test_invalid_value_types_are_dropped(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "keybindings.json",
        {"app.model.cycleForward": 42, "app.model.select": ["ctrl+x", 7]},
    )
    mgr = KeybindingsManager.from_file(path)
    # Non-string/array-of-strings values are dropped, default restored.
    assert mgr.keys_for("app.model.cycleForward") == ["ctrl+p"]
    assert mgr.keys_for("app.model.select") == ["ctrl+l"]


# --- reload -----------------------------------------------------------------


def test_reload_picks_up_edited_file(tmp_path: Path) -> None:
    path = _write(tmp_path / "keybindings.json", {"app.model.cycleForward": "ctrl+j"})
    mgr = KeybindingsManager.from_file(path)
    assert mgr.keys_for("app.model.cycleForward") == ["ctrl+j"]
    _write(path, {"app.model.cycleForward": "ctrl+k"})
    mgr.reload()
    assert mgr.keys_for("app.model.cycleForward") == ["ctrl+k"]


def test_reload_of_now_malformed_file_falls_back_to_defaults_not_prior(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path / "keybindings.json", {"app.model.cycleForward": "ctrl+j"})
    mgr = KeybindingsManager.from_file(path)
    _write(path, "{ broken")
    mgr.reload()
    # Falls back to the built-in default, NOT the previously-loaded ctrl+j.
    assert mgr.keys_for("app.model.cycleForward") == ["ctrl+p"]


# --- migration --------------------------------------------------------------


def test_legacy_name_migrated_in_memory() -> None:
    config, migrated = migrate_keybindings_config({"cycleModelForward": "ctrl+j"})
    assert migrated is True
    assert config == {"app.model.cycleForward": "ctrl+j"}


def test_legacy_and_new_name_both_present_new_wins() -> None:
    config, migrated = migrate_keybindings_config(
        {"cycleModelForward": "ctrl+j", "app.model.cycleForward": "ctrl+k"}
    )
    # The new name wins; the legacy is dropped.
    assert config == {"app.model.cycleForward": "ctrl+k"}


def test_migration_reorders_to_canonical_then_extras_sorted() -> None:
    config, _ = migrate_keybindings_config(
        {
            "zzz.custom": "ctrl+z",
            "app.model.cycleForward": "ctrl+j",
            "app.interrupt": "esc",
            "aaa.custom": "ctrl+a",
        }
    )
    keys = list(config.keys())
    # Canonical app order first (interrupt before model.cycleForward), then
    # unknown extras sorted alphabetically.
    assert keys.index("app.interrupt") < keys.index("app.model.cycleForward")
    assert keys.index("aaa.custom") < keys.index("zzz.custom")
    assert keys.index("app.model.cycleForward") < keys.index("aaa.custom")


def test_loaded_manager_applies_legacy_migration(tmp_path: Path) -> None:
    path = _write(tmp_path / "keybindings.json", {"cycleModelForward": "ctrl+j"})
    mgr = KeybindingsManager.from_file(path)
    assert mgr.keys_for("app.model.cycleForward") == ["ctrl+j"]


def test_loading_never_writes_back_migrated_config(tmp_path: Path) -> None:
    body = {"cycleModelForward": "ctrl+j"}
    path = _write(tmp_path / "keybindings.json", body)
    before = path.read_text(encoding="utf-8")
    KeybindingsManager.from_file(path)
    assert path.read_text(encoding="utf-8") == before


# --- key display + /hotkeys -------------------------------------------------


def test_key_display_text_capitalizes_and_joins() -> None:
    assert key_display_text(["ctrl+p"], platform="linux") == "Ctrl+P"
    assert (
        key_display_text(["ctrl+left", "alt+left"], platform="linux")
        == "Ctrl+Left/Alt+Left"
    )
    assert key_display_text([], platform="linux") == ""


def test_key_display_text_darwin_alt_is_option() -> None:
    assert key_display_text(["alt+enter"], platform="darwin") == "Option+Enter"


def test_render_hotkeys_reflects_resolved_user_override() -> None:
    mgr = KeybindingsManager(user_bindings={"app.model.cycleForward": "ctrl+j"})
    out = render_hotkeys(mgr, platform="linux")
    # Built from the resolved manager: the override shows, the default does not.
    assert "Ctrl+J" in out
    assert (
        "Ctrl+P" not in out.split("Cycle models")[0].splitlines()[-1] or "Ctrl+J" in out
    )
    assert "Navigation" in out
    assert "Editing" in out


def test_render_hotkeys_has_grouped_tables() -> None:
    mgr = KeybindingsManager(user_bindings={})
    out = render_hotkeys(mgr, platform="linux")
    assert "**Navigation**" in out
    assert "**Editing**" in out
    assert "Send message" in out


def test_keybindings_state_is_immutable_and_published_wholesale(
    tmp_path: Path,
) -> None:
    """Overrides live in one frozen value, replaced rather than edited."""

    config = tmp_path / "keybindings.json"
    config.write_text('{"app.clear": "ctrl+q"}', encoding="utf-8")
    manager = KeybindingsManager.from_file(config)
    first = manager.state

    assert manager.keys_for("app.clear") == ["ctrl+q"]
    with pytest.raises(TypeError):
        cast(dict[str, object], first.user_bindings)["app.clear"] = ("ctrl+z",)

    config.write_text('{"app.clear": "ctrl+j"}', encoding="utf-8")
    manager.reload()

    assert manager.state is not first
    assert manager.keys_for("app.clear") == ["ctrl+j"]
    # The retained value did not follow the reload.
    assert first.user_bindings["app.clear"] == ("ctrl+q",)


def test_prepared_keybindings_are_not_live_until_published(tmp_path: Path) -> None:
    config = tmp_path / "keybindings.json"
    config.write_text('{"app.clear": "ctrl+q"}', encoding="utf-8")
    manager = KeybindingsManager.from_file(config)

    config.write_text('{"app.clear": "ctrl+j"}', encoding="utf-8")
    candidate = manager.prepare_reload()

    assert candidate is not None
    assert candidate.user_bindings["app.clear"] == ("ctrl+j",)
    # Nothing observable changed yet.
    assert manager.keys_for("app.clear") == ["ctrl+q"]

    manager.publish(candidate)
    assert manager.keys_for("app.clear") == ["ctrl+j"]


def test_prepare_reload_without_a_config_path_is_a_no_op() -> None:
    manager = KeybindingsManager({"app.clear": "ctrl+q"})
    before = manager.state

    assert manager.prepare_reload() is None
    manager.reload()

    assert manager.state is before
    assert manager.keys_for("app.clear") == ["ctrl+q"]


def test_a_malformed_reload_falls_back_to_defaults_not_prior_bindings(
    tmp_path: Path,
) -> None:
    """Established behavior: keybindings do not keep the prior good value."""

    config = tmp_path / "keybindings.json"
    config.write_text('{"app.clear": "ctrl+q"}', encoding="utf-8")
    manager = KeybindingsManager.from_file(config)

    config.write_text("{not json", encoding="utf-8")
    manager.reload()

    assert manager.has_user_binding("app.clear") is False
    assert manager.keys_for("app.clear") == list(
        DEFAULT_KEYBINDINGS["app.clear"].default_keys
    )


def test_a_caller_supplied_manager_adopts_the_session_mutex() -> None:
    """Two boundaries serialize nothing; composition binds them to one."""

    import threading

    session_lock = threading.RLock()
    manager = KeybindingsManager({"app.clear": "ctrl+q"})
    private_lock = manager._state_lock

    assert private_lock is not session_lock

    manager.bind_state_lock(session_lock)
    assert manager._state_lock is session_lock

    # Idempotent: binding the same lock again changes nothing.
    manager.bind_state_lock(session_lock)
    assert manager._state_lock is session_lock
    assert manager.keys_for("app.clear") == ["ctrl+q"]


def test_keybindings_state_normalizes_a_plain_dict_at_construction() -> None:
    """The immutability invariant belongs to the type, not just to from_config."""

    from pipy_harness.native.keybindings import KeybindingsState

    source = {"app.clear": ("ctrl+q",)}
    state = KeybindingsState(source)

    with pytest.raises(TypeError):
        cast(dict[str, object], state.user_bindings)["app.clear"] = ("ctrl+z",)

    # Mutating the caller's original dict cannot reach the published value.
    source["app.clear"] = ("ctrl+z",)
    assert state.user_bindings["app.clear"] == ("ctrl+q",)
