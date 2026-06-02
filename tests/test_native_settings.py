"""Tests for the Pi-style layered settings core (`pipy_harness.native.settings`).

Covers the pure building blocks first: the one-level deep-merge precedence
(`deep_merge_settings`) and the load-time migration pass (`migrate_settings`)
that mirrors Pi's `migrateSettings` with its three distinct deletion behaviors.
Loader precedence, parse-error isolation, field-scoped writes, and unknown-key
round-trip are covered alongside in the same module.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from pipy_harness.native.settings import (
    SettingsManager,
    deep_merge_settings,
    migrate_settings,
    resolve_config_home,
)


# --- deep_merge_settings: one-level shallow merge ---------------------------


def test_deep_merge_overrides_top_level_scalar() -> None:
    base = {"defaultProvider": "anthropic", "theme": "dark"}
    override = {"defaultProvider": "openai"}
    assert deep_merge_settings(base, override) == {
        "defaultProvider": "openai",
        "theme": "dark",
    }


def test_deep_merge_shallow_merges_top_level_objects_one_level() -> None:
    # compaction is a top-level object: the two dicts are shallow-merged
    # key-by-key, so a key absent from the override survives from the base.
    base = {"compaction": {"enabled": True, "reserveTokens": 16384}}
    override = {"compaction": {"reserveTokens": 8000}}
    assert deep_merge_settings(base, override) == {
        "compaction": {"enabled": True, "reserveTokens": 8000}
    }


def test_deep_merge_replaces_deeper_nested_object_wholesale() -> None:
    # retry is shallow-merged one level, but retry.provider (a deeper object)
    # is replaced wholesale by the higher-precedence layer, not recursed into.
    base = {"retry": {"enabled": True, "provider": {"timeoutMs": 1000, "maxRetries": 5}}}
    override = {"retry": {"provider": {"timeoutMs": 2000}}}
    assert deep_merge_settings(base, override) == {
        "retry": {"enabled": True, "provider": {"timeoutMs": 2000}}
    }


def test_deep_merge_replaces_arrays_wholesale() -> None:
    base = {"enabledModels": ["a", "b", "c"]}
    override = {"enabledModels": ["x"]}
    assert deep_merge_settings(base, override) == {"enabledModels": ["x"]}


def test_deep_merge_object_replaces_scalar_and_vice_versa() -> None:
    # When the types differ across layers, the override value replaces wholesale.
    assert deep_merge_settings({"x": {"a": 1}}, {"x": 5}) == {"x": 5}
    assert deep_merge_settings({"x": 5}, {"x": {"a": 1}}) == {"x": {"a": 1}}


def test_deep_merge_does_not_mutate_inputs() -> None:
    base = {"compaction": {"enabled": True}}
    override = {"compaction": {"reserveTokens": 10}}
    deep_merge_settings(base, override)
    assert base == {"compaction": {"enabled": True}}
    assert override == {"compaction": {"reserveTokens": 10}}


# --- migrate_settings: rename keys (replacement-absent guard) ----------------


def test_migrate_renames_queue_mode_when_replacement_absent() -> None:
    out = migrate_settings({"queueMode": "all"})
    assert out == {"steeringMode": "all"}


def test_migrate_leaves_legacy_queue_mode_untouched_when_replacement_present() -> None:
    # Pi only renames when the replacement key is absent; if steeringMode
    # already exists, queueMode is left in place and the new value wins on read.
    out = migrate_settings({"queueMode": "all", "steeringMode": "one-at-a-time"})
    assert out == {"queueMode": "all", "steeringMode": "one-at-a-time"}


def test_migrate_websockets_true_to_transport_websocket() -> None:
    assert migrate_settings({"websockets": True}) == {"transport": "websocket"}


def test_migrate_websockets_false_to_transport_sse() -> None:
    assert migrate_settings({"websockets": False}) == {"transport": "sse"}


def test_migrate_leaves_legacy_websockets_when_transport_present() -> None:
    out = migrate_settings({"websockets": True, "transport": "auto"})
    assert out == {"websockets": True, "transport": "auto"}


# --- migrate_settings: retry.maxDelayMs unconditional deletion ---------------


def test_migrate_retry_max_delay_copied_when_replacement_absent() -> None:
    out = migrate_settings({"retry": {"maxDelayMs": 5000}})
    assert out == {"retry": {"provider": {"maxRetryDelayMs": 5000}}}


def test_migrate_retry_max_delay_deleted_even_when_replacement_present() -> None:
    # maxDelayMs is deleted unconditionally whenever retry is an object, even
    # if retry.provider.maxRetryDelayMs already exists (the existing value wins).
    out = migrate_settings(
        {"retry": {"maxDelayMs": 5000, "provider": {"maxRetryDelayMs": 9000}}}
    )
    assert out == {"retry": {"provider": {"maxRetryDelayMs": 9000}}}


# --- migrate_settings: skills object always replaced -------------------------


def test_migrate_skills_object_replaced_with_custom_directories() -> None:
    out = migrate_settings(
        {"skills": {"enableSkillCommands": False, "customDirectories": ["/a", "/b"]}}
    )
    assert out == {"skills": ["/a", "/b"], "enableSkillCommands": False}


def test_migrate_skills_object_deleted_when_no_custom_directories() -> None:
    out = migrate_settings({"skills": {"enableSkillCommands": True}})
    assert out == {"enableSkillCommands": True}


def test_migrate_skills_object_hoist_skipped_when_top_level_already_set() -> None:
    # The enableSkillCommands hoist is conditional; the skills-object replacement
    # is not. So the object is gone but the pre-existing top-level value stays.
    out = migrate_settings(
        {"skills": {"enableSkillCommands": False}, "enableSkillCommands": True}
    )
    assert out == {"enableSkillCommands": True}


def test_migrate_preserves_unknown_keys_and_is_idempotent() -> None:
    raw = {"queueMode": "all", "somethingFuture": {"nested": 1}}
    once = migrate_settings(raw)
    twice = migrate_settings(once)
    assert once == twice
    assert once["somethingFuture"] == {"nested": 1}


def test_migrate_does_not_mutate_input() -> None:
    raw = {"queueMode": "all", "retry": {"maxDelayMs": 1}}
    migrate_settings(raw)
    assert raw == {"queueMode": "all", "retry": {"maxDelayMs": 1}}


# --- SettingsManager: discovery, precedence, isolation, writes --------------


def _write_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def _manager(tmp_path: Path, **kwargs) -> SettingsManager:
    return SettingsManager(
        global_path=tmp_path / "config" / "settings.json",
        project_path=tmp_path / "proj" / ".pipy" / "settings.json",
        **kwargs,
    )


def test_missing_files_load_as_empty_without_error(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    assert mgr.effective() == {}
    assert mgr.load_errors() == {}


def test_global_settings_discovered(tmp_path: Path) -> None:
    _write_json(tmp_path / "config" / "settings.json", {"theme": "dark"})
    mgr = _manager(tmp_path)
    assert mgr.effective()["theme"] == "dark"


def test_project_overrides_global_with_one_level_merge(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "config" / "settings.json",
        {"theme": "dark", "compaction": {"enabled": True, "reserveTokens": 16384}},
    )
    _write_json(
        tmp_path / "proj" / ".pipy" / "settings.json",
        {"theme": "light", "compaction": {"reserveTokens": 8000}},
    )
    mgr = _manager(tmp_path)
    eff = mgr.effective()
    assert eff["theme"] == "light"
    # one-level shallow merge: enabled survives from global, reserveTokens wins.
    assert eff["compaction"] == {"enabled": True, "reserveTokens": 8000}


def test_cli_env_overrides_apply_as_final_layer(tmp_path: Path) -> None:
    _write_json(tmp_path / "config" / "settings.json", {"theme": "dark"})
    _write_json(tmp_path / "proj" / ".pipy" / "settings.json", {"theme": "light"})
    mgr = _manager(tmp_path, overrides={"theme": "solarized"})
    assert mgr.effective()["theme"] == "solarized"


def test_migration_applied_on_load(tmp_path: Path) -> None:
    _write_json(tmp_path / "config" / "settings.json", {"queueMode": "all"})
    mgr = _manager(tmp_path)
    assert mgr.effective().get("steeringMode") == "all"
    assert "queueMode" not in mgr.effective()


def test_malformed_scope_is_isolated_and_recorded(tmp_path: Path) -> None:
    gpath = tmp_path / "config" / "settings.json"
    gpath.parent.mkdir(parents=True, exist_ok=True)
    gpath.write_text("{not json", encoding="utf-8")
    _write_json(tmp_path / "proj" / ".pipy" / "settings.json", {"theme": "light"})
    mgr = _manager(tmp_path)
    # global scope fell back to {} but project still loads.
    assert mgr.effective() == {"theme": "light"}
    assert "global" in mgr.load_errors()


def test_unknown_keys_round_trip_in_effective(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "config" / "settings.json",
        {"theme": "dark", "futureKey": {"deep": [1, 2]}},
    )
    mgr = _manager(tmp_path)
    assert mgr.effective()["futureKey"] == {"deep": [1, 2]}


def test_field_scoped_write_preserves_unknown_keys(tmp_path: Path) -> None:
    gpath = tmp_path / "config" / "settings.json"
    _write_json(gpath, {"theme": "dark", "unknownKept": 7, "futureObj": {"a": 1}})
    mgr = _manager(tmp_path)
    mgr.set_value("theme", "light", scope="global")
    on_disk = json.loads(gpath.read_text(encoding="utf-8"))
    assert on_disk["theme"] == "light"
    assert on_disk["unknownKept"] == 7
    assert on_disk["futureObj"] == {"a": 1}


def test_nested_field_scoped_write_preserves_sibling_subkeys(tmp_path: Path) -> None:
    gpath = tmp_path / "config" / "settings.json"
    _write_json(gpath, {"compaction": {"enabled": True, "reserveTokens": 16384}})
    mgr = _manager(tmp_path)
    mgr.set_value("compaction.reserveTokens", 9000, scope="global")
    on_disk = json.loads(gpath.read_text(encoding="utf-8"))
    assert on_disk["compaction"] == {"enabled": True, "reserveTokens": 9000}


def test_write_merges_concurrent_on_disk_change(tmp_path: Path) -> None:
    gpath = tmp_path / "config" / "settings.json"
    _write_json(gpath, {"theme": "dark"})
    mgr = _manager(tmp_path)
    # Another writer adds a key after the manager loaded.
    _write_json(gpath, {"theme": "dark", "addedConcurrently": "x"})
    mgr.set_value("theme", "light", scope="global")
    on_disk = json.loads(gpath.read_text(encoding="utf-8"))
    assert on_disk["theme"] == "light"
    assert on_disk["addedConcurrently"] == "x"


def test_errored_scope_is_never_written_over(tmp_path: Path) -> None:
    gpath = tmp_path / "config" / "settings.json"
    gpath.parent.mkdir(parents=True, exist_ok=True)
    gpath.write_text("{broken", encoding="utf-8")
    mgr = _manager(tmp_path)
    with pytest.raises(Exception):
        mgr.set_value("theme", "light", scope="global")
    # The malformed file is untouched.
    assert gpath.read_text(encoding="utf-8") == "{broken"


def test_write_is_pretty_printed_two_space(tmp_path: Path) -> None:
    gpath = tmp_path / "config" / "settings.json"
    mgr = _manager(tmp_path)
    mgr.set_value("theme", "light", scope="global")
    text = gpath.read_text(encoding="utf-8")
    assert '  "theme": "light"' in text


def test_written_file_is_owner_private(tmp_path: Path) -> None:
    gpath = tmp_path / "config" / "settings.json"
    mgr = _manager(tmp_path)
    mgr.set_value("theme", "light", scope="global")
    mode = stat.S_IMODE(gpath.stat().st_mode)
    assert mode == 0o600


def test_resolve_config_home_prefers_pipy_config_home(tmp_path: Path) -> None:
    home = resolve_config_home(env={"PIPY_CONFIG_HOME": str(tmp_path / "cfg")})
    assert home == tmp_path / "cfg"


# --- migration fidelity on atypical/malformed input (Pi parity) -------------


def test_migrate_retry_max_delay_non_number_deleted_not_copied() -> None:
    # Pi deletes maxDelayMs unconditionally but only copies a numeric value.
    out = migrate_settings({"retry": {"maxDelayMs": "oops"}})
    assert out == {"retry": {"provider": {}}}


def test_migrate_retry_max_delay_bool_is_not_a_number() -> None:
    out = migrate_settings({"retry": {"maxDelayMs": True}})
    assert out == {"retry": {"provider": {}}}


def test_migrate_retry_explicit_null_replacement_treated_as_absent() -> None:
    # Pi treats undefined AND null as "replacement absent", so the legacy value
    # is copied over an explicit null.
    out = migrate_settings(
        {"retry": {"maxDelayMs": 5, "provider": {"maxRetryDelayMs": None}}}
    )
    assert out == {"retry": {"provider": {"maxRetryDelayMs": 5}}}


def test_migrate_websockets_non_boolean_left_untouched() -> None:
    # Pi type-guards on boolean; a non-boolean websockets is not migrated.
    assert migrate_settings({"websockets": 1}) == {"websockets": 1}
    assert migrate_settings({"websockets": None}) == {"websockets": None}
    assert migrate_settings({"websockets": "true"}) == {"websockets": "true"}


def test_nested_write_replaces_non_dict_intermediate_preserving_siblings(
    tmp_path: Path,
) -> None:
    # A scalar cannot hold a nested key; it is replaced with a fresh object,
    # but unrelated sibling top-level keys are preserved.
    gpath = tmp_path / "config" / "settings.json"
    _write_json(gpath, {"compaction": "notadict", "keep": 1})
    mgr = _manager(tmp_path)
    mgr.set_value("compaction.reserveTokens", 9000, scope="global")
    on_disk = json.loads(gpath.read_text(encoding="utf-8"))
    assert on_disk == {"compaction": {"reserveTokens": 9000}, "keep": 1}
