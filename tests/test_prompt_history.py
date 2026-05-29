"""Tests for the local-only persistent prompt-history store.

The store backs the product-TUI ``/settings`` "persistent prompt history"
toggle. It is local pipy state under the user's state dir — independent of the
metadata-first session archive (which never holds prompt bodies). Persistence
is opt-in, capped, blank/duplicate-suppressed, and clearable.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

from pipy_harness.native.prompt_history import (
    PromptHistoryStore,
    default_prompt_history_path,
)


def _store(tmp_path: Path, **kwargs: int) -> PromptHistoryStore:
    return PromptHistoryStore(tmp_path / "prompt-history.json", **kwargs)


def test_store_defaults_to_disabled_and_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.enabled is False
    assert store.entries() == []
    # Reading state alone never writes a file.
    assert not (tmp_path / "prompt-history.json").exists()


def test_disabled_store_does_not_persist_prompts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record("secret thought")
    assert store.entries() == []
    assert not (tmp_path / "prompt-history.json").exists()


def test_enabled_store_persists_prompts_across_instances(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_enabled(True)
    store.record("first prompt")
    store.record("second prompt")

    fresh = _store(tmp_path)
    assert fresh.enabled is True
    assert fresh.entries() == ["first prompt", "second prompt"]


def test_record_suppresses_blank_and_consecutive_duplicates(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_enabled(True)
    store.record("   ")
    store.record("")
    store.record("repeat")
    store.record("repeat")
    store.record("different")
    assert store.entries() == ["repeat", "different"]


def test_record_caps_history_depth(tmp_path: Path) -> None:
    store = _store(tmp_path, max_entries=3)
    store.set_enabled(True)
    for index in range(5):
        store.record(f"prompt-{index}")
    assert store.entries() == ["prompt-2", "prompt-3", "prompt-4"]


def test_clear_wipes_entries_but_keeps_enabled_flag(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_enabled(True)
    store.record("alpha")
    store.clear()
    assert store.entries() == []
    assert store.enabled is True

    fresh = _store(tmp_path)
    assert fresh.entries() == []
    assert fresh.enabled is True


def test_disable_then_fresh_instance_does_not_recall(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_enabled(True)
    store.record("remembered")
    store.set_enabled(False)
    # Disabling stops new persistence; clearing wipes what was saved.
    store.clear()

    fresh = _store(tmp_path)
    assert fresh.enabled is False
    assert fresh.entries() == []


def test_persisted_file_is_private(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_enabled(True)
    store.record("alpha")
    mode = stat.S_IMODE((tmp_path / "prompt-history.json").stat().st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR


def test_persisted_file_uses_versioned_schema(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_enabled(True)
    store.record("alpha")
    body = json.loads((tmp_path / "prompt-history.json").read_text(encoding="utf-8"))
    assert body["schema"] == "pipy.prompt-history"
    assert body["schema_version"] == 1
    assert body["enabled"] is True
    assert body["entries"] == ["alpha"]


def test_corrupt_or_foreign_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "prompt-history.json"
    path.write_text("{ not json", encoding="utf-8")
    store = PromptHistoryStore(path)
    assert store.enabled is False
    assert store.entries() == []

    path.write_text(json.dumps({"schema": "other", "entries": ["x"]}), encoding="utf-8")
    store = PromptHistoryStore(path)
    assert store.entries() == []


def test_save_failure_reverts_in_memory_state(tmp_path: Path) -> None:
    """A failed persist must not leave the live store out of sync with disk.

    Otherwise a failed clear/disable could show "off"/"0 saved" while the
    on-disk file still recalls — a privacy gap. On write failure the in-memory
    state reverts so it matches what a fresh session would read.
    """

    # Parent path is a *file*, so mkdir/write under it always fails.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    store = PromptHistoryStore(blocker / "nested" / "history.json")

    store.set_enabled(True)
    assert store.enabled is False, "enable must revert when it cannot persist"

    store._enabled = True  # pretend it was enabled on disk
    store.record("x")
    assert store.entries() == [], "record must revert when it cannot persist"


def test_clear_preserves_unrelated_disk_but_wipes_saved(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_enabled(True)
    store.record("a")
    store.record("b")
    store.clear()
    # The saved entries are gone, but the file still records the enabled flag.
    fresh = _store(tmp_path)
    assert fresh.entries() == []
    assert fresh.enabled is True


def test_non_boolean_enabled_is_treated_as_disabled(tmp_path: Path) -> None:
    """A non-boolean ``enabled`` must not silently opt in.

    The feature is opt-in for privacy; a hand-edited or foreign file with a
    truthy-but-non-boolean ``enabled`` (e.g. the string ``"false"``) must not
    enable persistence/seeding.
    """

    path = tmp_path / "prompt-history.json"
    bad_values: tuple[object, ...] = ("false", 1, "yes", {})
    for bad_value in bad_values:
        path.write_text(
            json.dumps(
                {
                    "schema": "pipy.prompt-history",
                    "schema_version": 1,
                    "enabled": bad_value,
                    "entries": ["leaked"],
                }
            ),
            encoding="utf-8",
        )
        assert PromptHistoryStore(path).enabled is False, bad_value


def test_default_path_honors_env_override(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "custom" / "history.json"
    monkeypatch.setenv("PIPY_PROMPT_HISTORY_PATH", str(target))
    assert default_prompt_history_path() == target


def test_default_path_falls_back_to_state_dir(monkeypatch) -> None:
    monkeypatch.delenv("PIPY_PROMPT_HISTORY_PATH", raising=False)
    path = default_prompt_history_path()
    assert path.name == "prompt-history.json"
    assert path.parent.name == "pipy"
