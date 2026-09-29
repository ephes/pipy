"""Unit tests for the catalog drift check (scripts/catalog_drift.py)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.catalog import (
    ContextWindowSource,
    NativeModelCost,
    NativeModelSpec,
)

_MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "catalog_drift.py"
_spec = importlib.util.spec_from_file_location("catalog_drift", _MOD_PATH)
assert _spec is not None and _spec.loader is not None
catalog_drift: Any = importlib.util.module_from_spec(_spec)
# dataclasses resolve annotations through sys.modules, so register first.
sys.modules.setdefault("catalog_drift", catalog_drift)
_spec.loader.exec_module(catalog_drift)

ABSENT = catalog_drift.ABSENT


def _row(provider: str, model_id: str, **overrides: Any) -> NativeModelSpec:
    values: dict[str, Any] = {
        "provider_name": provider,
        "model_id": model_id,
        "display_name": model_id,
        "api": "openai-responses",
        "base_url": "https://api.example",
        "reasoning": False,
        "thinking_level_map": {},
        "input": ("text",),
        "cost": NativeModelCost(1.0, 2.0, 0.1, 0.0),
        "context_window": 100_000,
        "max_tokens": 8_000,
        "compat": None,
        "context_window_source": ContextWindowSource.BUILTIN,
    }
    values.update(overrides)
    return NativeModelSpec(**values)


def _pi(provider: str, model_id: str, **overrides: Any) -> dict[str, Any]:
    model: dict[str, Any] = {
        "id": model_id,
        "name": model_id,
        "api": "openai-responses",
        "provider": provider,
        "baseUrl": "https://api.example",
        "reasoning": False,
        "input": ["text"],
        "cost": {"input": 1, "output": 2, "cacheRead": 0.1, "cacheWrite": 0},
        "contextWindow": 100_000,
        "maxTokens": 8_000,
    }
    model.update(overrides)
    return model


def _fields(findings: list[Any]) -> set[tuple[str, str]]:
    return {(f.ref, f.field) for f in findings}


# ---- thinking normalization -----------------------------------------------------


def test_empty_pipy_map_on_reasoning_row_matches_pi_row_without_map() -> None:
    row = _row("openai", "m", reasoning=True)
    pi = {"openai": {"m": _pi("openai", "m", reasoning=True)}}

    assert catalog_drift.compare([row], pi) == []


def test_pi_partial_map_matches_pipy_spelled_out_identity_levels() -> None:
    row = _row(
        "openai",
        "m",
        reasoning=True,
        thinking_level_map={
            "off": None,
            "minimal": "minimal",
            "low": "low",
            "medium": "medium",
            "high": "high",
            "xhigh": "xhigh",
        },
    )
    pi_model = _pi(
        "openai", "m", reasoning=True, thinkingLevelMap={"off": None, "xhigh": "xhigh"}
    )

    assert catalog_drift.compare([row], {"openai": {"m": pi_model}}) == []


def test_pipy_partial_map_missing_an_identity_level_is_drift() -> None:
    # pipy reads map keys, so a partial map that omits "low" drops the level,
    # while Pi keeps it identity-supported.
    row = _row(
        "openai",
        "m",
        reasoning=True,
        thinking_level_map={"minimal": "minimal", "medium": "medium", "high": "high"},
    )
    pi_model = _pi("openai", "m", reasoning=True, thinkingLevelMap={"xhigh": None})

    [finding] = catalog_drift.compare([row], {"openai": {"m": pi_model}})
    assert finding.field == "thinkingLevelMap"
    assert finding.pipy["low"] == ABSENT
    assert finding.pi["low"] == "low"
    assert "'low': 'low'" in finding.proposal


def test_off_null_differs_from_absent_off() -> None:
    row = _row("openai", "m", reasoning=True)
    pi_model = _pi("openai", "m", reasoning=True, thinkingLevelMap={"off": None})

    [finding] = catalog_drift.compare([row], {"openai": {"m": pi_model}})
    assert finding.field == "thinkingLevelMap"
    assert (finding.pipy["off"], finding.pi["off"]) == (ABSENT, None)


def test_non_reasoning_rows_have_no_levels_on_either_side() -> None:
    row = _row("openai", "m", thinking_level_map={"high": "high"})
    pi_model = _pi("openai", "m", thinkingLevelMap={"high": "high"})

    assert catalog_drift.compare([row], {"openai": {"m": pi_model}}) == []


# ---- value fields ---------------------------------------------------------------


def test_value_drift_carries_both_values_and_a_proposal() -> None:
    row = _row("openai", "m", context_window=1_000_000)
    pi_model = _pi(
        "openai",
        "m",
        contextWindow=1_047_576,
        cost={"input": 1, "output": 2, "cacheRead": 0.125, "cacheWrite": 0},
    )

    findings = {
        f.field: f for f in catalog_drift.compare([row], {"openai": {"m": pi_model}})
    }
    assert set(findings) == {"contextWindow", "cost"}
    assert findings["contextWindow"].proposal == "context_window=1047576"
    assert findings["cost"].proposal == "cost=(1.0, 2.0, 0.125, 0.0)"


def test_row_headers_are_compared() -> None:
    # Pi rows can carry static request headers (Copilot's editor headers).
    headers = {"Editor-Version": "vscode/1.107.0"}
    pi_model = _pi("openai", "m", headers=headers)

    assert (
        catalog_drift.compare(
            [_row("openai", "m", headers=headers)], {"openai": {"m": pi_model}}
        )
        == []
    )
    findings = catalog_drift.compare([_row("openai", "m")], {"openai": {"m": pi_model}})
    assert [(f.field, f.pipy, f.pi) for f in findings] == [("headers", {}, headers)]
    assert findings[0].proposal == f"headers={headers!r}"


def test_input_capabilities_compare_order_independently() -> None:
    row = _row("openai", "m", input=("text", "image"))
    pi_model = _pi("openai", "m", input=["image", "text"])

    assert catalog_drift.compare([row], {"openai": {"m": pi_model}}) == []


def test_compat_missing_key_is_distinct_from_false() -> None:
    row = _row("openai", "m")
    pi_model = _pi("openai", "m", compat={"supportsLongCacheRetention": False})

    [finding] = catalog_drift.compare([row], {"openai": {"m": pi_model}})
    assert finding.field == "compat.supportsLongCacheRetention"
    assert (finding.pipy, finding.pi) == (ABSENT, False)


def test_untracked_compat_keys_and_display_names_are_ignored() -> None:
    row = _row("openai", "m", display_name="pipy label")
    pi_model = _pi("openai", "m", name="Pi label", compat={"supportsStrictMode": True})

    assert catalog_drift.compare([row], {"openai": {"m": pi_model}}) == []


def test_compat_proposal_for_a_key_pi_does_not_set() -> None:
    row = _row("openai", "m", compat={"supportsToolSearch": True})

    [finding] = catalog_drift.compare([row], {"openai": {"m": _pi("openai", "m")}})
    assert finding.proposal == "drop compat['supportsToolSearch']"


# ---- row sets and providers -----------------------------------------------------


def test_row_sets_use_pi_provider_names_and_skip_mirrors() -> None:
    rows = [
        _row("azure-openai", "a", api="azure-openai-responses"),
        _row("openai", "shared"),
        _row("openai-completions", "shared", api="openai-completions"),
        _row("openai", "legacy"),
        _row("fake", "fake-native-bootstrap", api="fake"),
    ]
    pi = {
        "azure-openai-responses": {
            "a": _pi("azure-openai-responses", "a", api="azure-openai-responses"),
            "b": _pi("azure-openai-responses", "b"),
        },
        "openai": {"shared": _pi("openai", "shared"), "new": _pi("openai", "new")},
    }

    assert _fields(catalog_drift.compare(rows, pi)) == {
        ("openai-completions/shared", "api"),
        ("openai/legacy", "pipy-only"),
        ("azure-openai/b", "not-carried"),
        ("openai/new", "not-carried"),
    }


# ---- allowlist ------------------------------------------------------------------


def _entries(tmp_path: Path, items: list[dict[str, Any]]) -> list[Any]:
    path = tmp_path / "allow.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    return catalog_drift.load_allowlist(path)


def test_allowlist_splits_drift_pending_deviation_and_stale(tmp_path: Path) -> None:
    Finding = catalog_drift.Finding
    findings = [
        Finding("mistral/a", "api", "mistral", "mistral-conversations"),
        Finding("mistral/a", "cost", [1.0], [2.0]),
        Finding("amazon-bedrock/eu.x", "not-carried"),
        Finding("amazon-bedrock/us.anthropic.claude-new", "not-carried"),
        Finding("openai/b", "maxTokens", 1, 2),
    ]
    entries = _entries(
        tmp_path,
        [
            {"kind": "deviation", "match": "mistral/*", "field": "api", "reason": "r"},
            {"kind": "pending", "match": "mistral/*", "field": "cost", "reason": "r"},
            {
                "kind": "deviation",
                "match": "amazon-bedrock/*",
                "except": ["amazon-bedrock/us.anthropic.claude-*"],
                "field": "not-carried",
                "reason": "r",
            },
            {
                "kind": "deviation",
                "match": "gone/*",
                "field": "pipy-only",
                "reason": "r",
            },
        ],
    )

    report = catalog_drift.build_report(
        findings, entries, shared_rows=2, pi_source="x", generated_at=None
    )

    assert _fields(report.drift) == {
        ("amazon-bedrock/us.anthropic.claude-new", "not-carried"),
        ("openai/b", "maxTokens"),
    }
    assert [e.match for e in report.stale] == ["gone/*"]
    assert [f.field for _, f in report.by_kind("pending")] == ["cost"]
    assert not report.ok
    text = catalog_drift.render_text(report, verbose=True)
    assert "DRIFT: not allowlisted (2)" in text
    assert "STALE allowlist entries: match nothing (1)" in text
    assert "Result: DRIFT" in text


def test_clean_report_with_only_allowlisted_findings(tmp_path: Path) -> None:
    Finding = catalog_drift.Finding
    entries = _entries(
        tmp_path,
        [{"kind": "deviation", "match": "a/*", "field": "pipy-only", "reason": "r"}],
    )
    report = catalog_drift.build_report(
        [Finding("a/b", "pipy-only")],
        entries,
        shared_rows=0,
        pi_source="x",
        generated_at=None,
    )

    assert report.ok
    assert json.loads(catalog_drift.render_json(report))["ok"] is True


@pytest.mark.parametrize(
    "item",
    [
        {"kind": "maybe", "match": "a/*", "field": "api", "reason": "r"},
        {"kind": "deviation", "match": "a/*", "field": "name", "reason": "r"},
        {"kind": "deviation", "match": "", "field": "api", "reason": "r"},
        {"kind": "deviation", "match": "a/*", "field": "api", "reason": " "},
        {
            "kind": "deviation",
            "match": "a/*",
            "field": "api",
            "reason": "r",
            "except": "b",
        },
        {
            "kind": "deviation",
            "match": "a/*",
            "field": "api",
            "reason": "r",
            "extra": 1,
        },
    ],
)
def test_invalid_allowlist_entries_are_rejected(
    tmp_path: Path, item: dict[str, Any]
) -> None:
    with pytest.raises(catalog_drift.InputError):
        _entries(tmp_path, [item])


def test_repository_allowlist_is_valid() -> None:
    entries = catalog_drift.load_allowlist(catalog_drift.DEFAULT_ALLOWLIST)
    assert entries
    assert all(entry.reason.strip() for entry in entries)


# ---- Pi data layouts ------------------------------------------------------------


def _models_json(path: Path) -> None:
    payload = {
        "openai": {
            "m": _pi("openai", "m"),
            "img": {**_pi("openai", "img"), "type": "image"},
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_loads_models_json_file_and_json_output_directory(tmp_path: Path) -> None:
    _models_json(tmp_path / "models.json")

    for source in (tmp_path / "models.json", tmp_path):
        models, stamp = catalog_drift.load_pi_models(source)
        assert list(models["openai"]) == ["m"]
        assert stamp is None


def test_loads_hydrated_data_directory(tmp_path: Path) -> None:
    (tmp_path / ".manifest.json").write_text(
        json.dumps({"generatedAt": "2026-09-29T00:00:00Z"}), encoding="utf-8"
    )
    (tmp_path / "openai.json").write_text(
        json.dumps(
            {
                "openai-responses": {
                    "chat:m": _pi("openai", "m"),
                    "image:img": _pi("openai", "img"),
                }
            }
        ),
        encoding="utf-8",
    )

    models, stamp = catalog_drift.load_pi_models(tmp_path)
    assert list(models["openai"]) == ["m"]
    assert stamp == "2026-09-29T00:00:00Z"


def test_missing_or_unrecognized_input_is_an_input_error(tmp_path: Path) -> None:
    with pytest.raises(catalog_drift.InputError):
        catalog_drift.load_pi_models(tmp_path / "missing")
    with pytest.raises(catalog_drift.InputError):
        catalog_drift.load_pi_models(tmp_path)


# ---- CLI ------------------------------------------------------------------------


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    allow = tmp_path / "allow.json"
    allow.write_text("[]", encoding="utf-8")

    assert (
        catalog_drift.main(
            ["--pi-data", str(tmp_path / "none"), "--allowlist", str(allow)]
        )
        == 2
    )
    assert "hydrate:model-data" in capsys.readouterr().err

    # The real built-in rows against a Pi table that ships none of them: every
    # row is pipy-only, so the check reports drift.
    _models_json(tmp_path / "models.json")
    code = catalog_drift.main(
        [
            "--pi-data",
            str(tmp_path / "models.json"),
            "--allowlist",
            str(allow),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert {item["field"] for item in payload["drift"]} >= {"pipy-only"}
