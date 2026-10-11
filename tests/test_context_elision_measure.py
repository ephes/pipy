"""The evaluation must actually alter admission without rewriting product history."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentStopReason,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.models import ProviderRequest

scripts = Path(__file__).resolve().parents[1] / "scripts"
_cm_spec = importlib.util.spec_from_file_location(
    "codemode_measure", scripts / "codemode_measure.py"
)
assert _cm_spec and _cm_spec.loader
_cm = importlib.util.module_from_spec(_cm_spec)
sys.modules["codemode_measure"] = _cm
_cm_spec.loader.exec_module(_cm)
_spec = importlib.util.spec_from_file_location(
    "context_elision_measure", scripts / "context_elision_measure.py"
)
assert _spec and _spec.loader
measure: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(measure)


def request(root, seed=9100):
    tree, _, _ = measure.fixture(root, "reread", seed)
    return ProviderRequest(
        "instructions",
        "",
        "fake",
        "fake",
        root,
        messages=tree.build_coding_context().messages,
    )


def test_projection_preserves_recent_cycles_prose_pairing_and_source(tmp_path):
    original = request(tmp_path / "fixture")
    projected = measure.elide(original)
    assert measure.estimated_input(original) > measure.CEILING
    assert measure.estimated_input(projected) + measure.RESERVE < measure.CEILING
    results = [m for m in projected.messages if isinstance(m, AgentToolResultMessage)]
    assert sum(m.content.value.startswith(measure.STUB) for m in results) == 6
    assert all(not m.content.value.startswith(measure.STUB) for m in results[-2:])
    for old, new in zip(original.messages, projected.messages, strict=True):
        if isinstance(old, AgentToolResultMessage):
            assert old.provider_correlation_id == new.provider_correlation_id
            assert old.tool_request_id == new.tool_request_id
            assert not old.content.value.startswith(measure.STUB)
        else:
            assert old is new


def test_small_errors_and_incomplete_cycles_are_kept(tmp_path):
    original = request(tmp_path / "fixture")
    messages = list(original.messages)
    messages[2] = replace(messages[2], is_error=True)
    messages[4] = replace(messages[4], content=ProductContent("small"))
    projected = measure.elide(replace(original, messages=tuple(messages)))
    assert projected.messages[2] is messages[2]
    assert projected.messages[4] is messages[4]
    incomplete = replace(original, messages=original.messages[:2])
    assert measure.elide(incomplete) is incomplete


@pytest.mark.parametrize("arm,summary_count", [("current", 1), ("elision", 0)])
def test_real_session_admission_and_durable_invariants(
    tmp_path, monkeypatch, arm, summary_count
):
    requests = []

    class Provider(FakeNativeProvider):
        def __init__(self, client):
            super().__init__(
                model_id="gpt-6.1-sol",
                supports_tool_calls=True,
                final_text='{"release":null,"mode":null}',
            )
            self.rows = []
            self.phase = 0

        def complete(self, value, **kwargs):
            requests.append(value)
            return super().complete(value, **kwargs)

    monkeypatch.setattr(measure, "ObservedProvider", Provider)
    hashes = measure.theme_hashes()
    with patch.dict(os.environ):
        result = measure.measure(tmp_path / "fixture", arm, "bulk", 9100)
    assert result["failure"] is None
    assert (
        sum(
            r.system_prompt.startswith("Summarize conversation context")
            for r in requests
        )
        == summary_count
    )
    assert all(not r.available_tools for r in requests)
    assert all(
        measure.estimated_input(r) + measure.RESERVE <= measure.CEILING
        for r in requests
    )
    assert (
        result["original_history_preserved"]
        and result["no_elision_persisted"]
        and result["strict_reopen"]
    )
    assert result["phases"][-1]["compaction_count"] == summary_count
    assert not result["all_exact"]
    assert measure.theme_hashes() == hashes
    if arm == "elision":
        assert any(
            m.content.value.startswith(measure.STUB)
            for r in requests
            for m in r.messages
            if isinstance(m, AgentToolResultMessage)
        )


def test_seed_matches_across_arms_and_answer_is_absent_from_question(tmp_path):
    a, expected, question = measure.fixture(tmp_path / "a", "reread", 9100)
    b, other, other_question = measure.fixture(tmp_path / "b", "reread", 9100)
    assert expected == other and question == other_question
    assert a.build_coding_context().messages == b.build_coding_context().messages
    assert expected["recovery_code"] not in question
    assert (tmp_path / "a/log-0.txt").read_text().splitlines()[0] == (
        "ARCHIVED_FACT recovery_code=" + expected["recovery_code"]
    )


def test_counter_completeness_does_not_invent_unavailable_usage():
    rows = [{"attempts": [{"usage": {"input_tokens": 12, "cached_tokens": None}}]}]
    assert measure.usage_totals(rows) == {
        "counters": {"input_tokens": 12},
        "all_counters_reported": False,
    }


@pytest.mark.parametrize(
    "ending,exact,unknown,present",
    [
        ('{"fact":"answer"}', True, False, True),
        ('{"fact":null}', False, True, True),
        ("invalid-json", False, False, True),
        (None, False, False, False),
        ("stopped", False, False, False),
        ("tool-only", False, False, False),
    ],
)
def test_scoring_requires_this_submissions_final(ending, exact, unknown, present):
    messages = [
        AgentUserMessage(ProductContent("old question")),
        AgentAssistantMessage(ProductContent('{"fact":"answer"}')),
        AgentUserMessage(ProductContent("new question")),
    ]
    if ending is not None:
        messages.append(
            AgentAssistantMessage(
                ProductContent(ending),
                stop_reason=AgentStopReason.ABORTED if ending == "stopped" else None,
                tool_calls=(AgentToolCall("last", "read", ProductContent("{}")),)
                if ending == "tool-only"
                else (),
            )
        )
    snapshot = SimpleNamespace(
        messages=tuple(messages),
        compaction_count=0,
        preparation_failure=None,
        provider_failure=None,
    )
    result = measure.phase_result(snapshot, {"fact": "answer"})
    assert result["exact_facts"] == exact
    assert result["reported_unknown"] == unknown
    assert result["final_present"] == present


@pytest.mark.parametrize("case", ["bulk", "reread"])
def test_report_aggregation_and_privacy_through_session(tmp_path, monkeypatch, case):
    _, expected, question = measure.fixture(tmp_path / "oracle", case, 9100)
    answer = json.dumps(expected)

    class Provider(FakeNativeProvider):
        def __init__(self, client):
            super().__init__(
                model_id="gpt-6.1-sol", supports_tool_calls=True, final_text=answer
            )
            self.rows = []
            self.phase = 0

    monkeypatch.setattr(measure, "ObservedProvider", Provider)
    with patch.dict(os.environ):
        run = measure.measure(tmp_path / f"0-{case}-current", "current", case, 9100)
    assert run["all_exact"]
    run["usage"] = {
        "counters": {
            "input_tokens": 100,
            "cached_tokens": 25,
            "output_tokens": 7,
            "reasoning_tokens": 0,
            "total_tokens": 107,
        },
        "all_counters_reported": True,
    }
    public = measure.public_report(
        {"runs": [run], "private_evidence_root": str(tmp_path)}
    )
    group = public["aggregates"][0]
    assert (
        group["retention_correct"],
        group["repeat_correct"],
        group["correct_answers"],
    ) == (1, 1, 2)
    assert group["usage"]["input_tokens"] == 100
    assert group["uncached_input_tokens"] == 75
    assert group["usage"]["reasoning_tokens"] == 0
    serialized = json.dumps(public)
    assert all(
        value not in serialized
        for value in (str(tmp_path), *expected.values(), question, answer)
        if value != "dry-run"
    )
    assert "private_evidence_root" not in public
    monkeypatch.setattr(
        measure,
        "ObservedProvider",
        lambda *_: pytest.fail("reanalyze made a provider call"),
    )
    reanalyzed = measure.reanalyze(
        {"runs": [run], "private_evidence_root": str(tmp_path)}
    )
    assert reanalyzed["runs"][0]["scoring_verified_from_saved_session"]
    assert reanalyzed["aggregates"] == public["aggregates"]
    assert str(tmp_path) not in json.dumps(reanalyzed)
    newer = measure.reanalyze(public, evidence_root=tmp_path)
    assert newer["aggregates"] == public["aggregates"]
    with pytest.raises(ValueError, match="--evidence-root"):
        measure.reanalyze(public)


def test_partial_attempt_counter_is_not_reported_as_group_total():
    attempts = [
        {
            "attempts": [
                {"usage": {"input_tokens": 10, "cached_tokens": None}},
                {"usage": {"input_tokens": 5, "cached_tokens": 3}},
            ]
        }
    ]
    usage = measure.usage_totals(attempts)
    assert usage["counters"] == {"input_tokens": 15, "cached_tokens": 3}
    assert not usage["all_counters_reported"]
    run = {
        "case": "bulk",
        "arm": "current",
        "usage": usage,
        "phases": [],
        "wall_seconds": 1,
        "summary_calls": 0,
        "ordinary_calls": 2,
        "reread_calls": 0,
    }
    group = measure.aggregate_runs([run])[0]
    assert all(value is None for value in group["usage"].values())
    assert group["uncached_input_tokens"] is None


def test_public_report_reanalysis_requires_explicit_root(tmp_path, monkeypatch, capsys):
    report = tmp_path / "public.json"
    report.write_text(json.dumps({"runs": []}))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "context_elision_measure",
            "--analyze-existing",
            str(report),
            "--report",
            str(tmp_path / "out.json"),
        ],
    )
    with pytest.raises(SystemExit) as raised:
        measure.main()
    assert raised.value.code == 2
    assert "--evidence-root is required" in capsys.readouterr().err
    assert not (tmp_path / "out.json").exists()
