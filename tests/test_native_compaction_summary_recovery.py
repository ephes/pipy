"""DF1-F5b: recover oversized auxiliary input through product sessions."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from test_native_coding_session_resume_compact import _RecordingToolProvider
from test_native_compaction_oversized_turn import _summary_requests
from test_native_request_budget_admission import _measure, _settings, _size
from test_native_semantic_compaction import _fixture, _published, _result

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.results import AgentCancellationReason
from pipy_harness.native.cancellation import ProviderCancelledError
from pipy_harness.native.coding.compaction import compaction_request
from pipy_harness.native.coding.product_session import CodingProductSessionCoordinator
from pipy_harness.native.coding.request_budget import RequestBudget
from pipy_harness.native.coding.state import CodingContextChangedError
from pipy_harness.native.coding.summary_recovery import (
    RECOVERY_WARNING,
    recover_summary_request,
)
from pipy_harness.native.repl import provider_selection
from pipy_harness.native.session_tree import CompactionEntry, NativeSessionTree
from pipy_harness.sdk import create_product_session


def _pressure_tree(tmp_path: Path, kind: str) -> NativeSessionTree:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "sessions")
    tree.append_message(
        AgentUserMessage(
            ProductContent(
                "Task: fix parser. " + ("é" * 24000 if kind == "paste" else "")
            )
        )
    )
    for index in range(80 if kind == "aggregate" else 1):
        call = AgentToolCall(
            f"call-{index}",
            "write",
            ProductContent(
                json.dumps(
                    {
                        "path": "parser.py",
                        "content": "x" * (24000 if kind == "arguments" else 1),
                    }
                )
            ),
        )
        tree.append_message(
            AgentAssistantMessage(ProductContent("Inspect parser"), (call,))
        )
        tree.append_message(
            AgentToolResultMessage(
                f"pipy-tool-{index}",
                "write",
                ProductContent("r" * 1500),
                f"call-{index}",
                details={
                    "nestedCalls": {
                        "complete": True,
                        "calls": [
                            {
                                "id": "parent/1",
                                "name": "edit",
                                "status": "error",
                                "durationMs": 1,
                                "arguments": {"path": "nested.py"},
                            }
                        ],
                    }
                },
            )
        )
    return tree


@pytest.mark.parametrize("kind", ["paste", "arguments", "aggregate"])
def test_recovery_continues_reopens_and_preserves_raw_tree(tmp_path: Path, kind: str):
    ceiling = _size(_measure(tmp_path)) + 5000
    settings = _settings(tmp_path, contextWindow=ceiling, reserveTokens=300)
    tree = _pressure_tree(tmp_path, kind)
    old_context = tree.build_coding_context()
    old_leaf = tree.leaf_id
    assert old_leaf is not None
    assert tree.path is not None
    raw_before = tree.path.read_bytes()
    provider = _RecordingToolProvider()
    diagnostics: list[str] = []
    with create_product_session(
        workspace=tmp_path,
        tree=tree,
        provider=provider,
        settings=settings,
        load_context_files=False,
        diagnostic_sink=diagnostics.append,
    ) as session:
        recovered = session.submit("continue")
        if recovered.preparation_failure is not None:
            assert not _summary_requests(provider)
            assert any(
                "estimated summary request exceeds" in notice for notice in diagnostics
            )
        assert recovered.preparation_failure is None
        assert recovered.provider_failure is None
        assert len(_summary_requests(provider)) == 1
        request = _summary_requests(provider)[0]
        assert _size(request, 300) <= ceiling
        assert request.available_tools == request.attachments == ()
        assert all(type(m) is AgentUserMessage for m in request.messages)
        assert RECOVERY_WARNING in request.messages[0].content.value
        if kind != "aggregate":
            assert "nested.py" in request.messages[0].content.value
        again = session.submit("next fitting prompt")
        assert again.preparation_failure is None
        assert len(_summary_requests(provider)) == 1
    assert any("incomplete excerpts" in notice for notice in diagnostics)
    assert tree.path.read_bytes().startswith(raw_before)
    compactions = [e for e in tree.get_entries() if isinstance(e, CompactionEntry)]
    assert len(compactions) == 1
    assert compactions[0].summary.startswith(RECOVERY_WARNING)
    reopened = NativeSessionTree.open(tree.path, strict=True)
    assert reopened.build_coding_context().messages == again.messages
    assert reopened.build_coding_context().prior_summary == compactions[0].summary
    # Resume through the real public session composition, then navigate the old
    # branch as /tree does. The complete saved exchanges remain available.
    resume_provider = _RecordingToolProvider()
    with create_product_session(
        workspace=tmp_path,
        tree=reopened,
        provider=resume_provider,
        settings=settings,
        load_context_files=False,
    ) as resumed:
        assert resumed.submit("resumed").preparation_failure is None
    assert RECOVERY_WARNING in resume_provider.requests[-1].system_prompt
    reopened.branch(old_leaf)
    assert reopened.build_coding_context() == old_context
    forked = NativeSessionTree.fork_from(
        tree.path, tmp_path, session_dir=tmp_path / "forks"
    )
    latest = NativeSessionTree.open(tree.path).build_coding_context()
    assert forked.build_coding_context().messages == latest.messages
    assert forked.build_coding_context().prior_summary == latest.prior_summary


def _oversized_effects(tmp_path: Path):
    effects, provider = _fixture(tmp_path, previous_summary="prior " * 10000)
    return effects, provider, RequestBudget(2400, 300)


@pytest.mark.parametrize("trigger", ["manual", "auto"])
@pytest.mark.parametrize("failure", ["failure", "cancel", "stale"])
def test_recovery_failure_cancel_and_stale_publish_nothing(
    tmp_path: Path, trigger: str, failure: str
):
    effects, provider, budget = _oversized_effects(tmp_path)
    before = _published(effects)
    after = []

    def complete(request):
        assert RECOVERY_WARNING in request.messages[0].content.value
        assert _size(request, 300) <= 2400
        if failure == "cancel":
            raise ProviderCancelledError()
        if failure == "stale":
            effects.ctl.session_tree.set_leaf(effects.ctl.session_tree.leaf_id)
            after.append(_published(effects))
            return _result()
        raise RuntimeError("private failure")

    provider.on_complete = complete
    if failure == "stale" and trigger == "auto":
        with pytest.raises(CodingContextChangedError):
            effects.compact_context(trigger, budget)
    else:
        outcome = effects.compact_context(trigger, budget)
        assert outcome.result is None
        assert (outcome.cancellation_reason is not None) == (failure == "cancel")
    assert len(provider.requests) == 1
    assert not provider.probe_failures
    assert _published(effects) == (after[0] if after else before)


def test_recovery_persistence_failure_remains_state_first(tmp_path: Path):
    effects, provider, budget = _oversized_effects(tmp_path)
    before = _published(effects)
    attempted = []

    def fail(action):
        attempted.append(action)
        raise OSError("disk full")

    effects = replace(
        effects,
        product_session=CodingProductSessionCoordinator(
            state=effects.coding_state,
            port=replace(effects.product_session._port, apply_compaction_callback=fail),
        ),
    )
    provider.effects = effects
    with pytest.raises(OSError, match="disk full"):
        effects.compact_context("manual", budget)
    assert len(attempted) == len(provider.requests) == 1
    assert effects.coding_state.compaction_count == 1
    assert effects.coding_state.compaction_suffix.startswith("\n\n" + RECOVERY_WARNING)
    assert _published(effects)[1:] == before[1:]


def test_recovery_revalidates_after_excerpt_work(tmp_path: Path, monkeypatch):
    effects, provider, budget = _oversized_effects(tmp_path)
    original = recover_summary_request
    after = []

    def mutate(request, budget):
        candidate = original(request, budget)
        assert candidate is not None
        effects.coding_state.mirror_history(effects.coding_state.messages)
        after.append(_published(effects))
        return candidate

    monkeypatch.setattr(provider_selection, "recover_summary_request", mutate)
    outcome = effects.compact_context("manual", budget)
    assert "context changed" in outcome.notice
    assert not provider.requests
    assert _published(effects) == after[0]


def test_recovery_tiny_allowance_terminates_without_provider(
    tmp_path: Path, monkeypatch
):
    effects, provider, _budget = _oversized_effects(tmp_path)
    before = _published(effects)
    for _ in range(3):
        outcome = effects.compact_context("manual", RequestBudget(310, 300))
        assert "even with incomplete excerpts" in outcome.notice
    assert not provider.requests and _published(effects) == before
    abort = threading.Event()
    abort.set()
    effects = replace(effects, abort_event=abort)
    assert (
        effects.compact_context("manual", RequestBudget(310, 300)).cancellation_reason
        is AgentCancellationReason.OPERATOR_ABORT
    )


def test_excerpt_tool_exchange_is_paired_or_omitted_in_full(tmp_path: Path):
    effects, _provider = _fixture(tmp_path)
    source = compaction_request(
        binding=effects.coding_state.provider_binding,
        cwd=tmp_path,
        dropped_messages=effects.coding_state.messages,
        prior_summary="p" * 24000,
        header_callback=None,
    )
    for ceiling in (4000, 1000):
        request = recover_summary_request(source, RequestBudget(ceiling, 200))
        assert request is not None
        data = json.loads(request.messages[0].content.value.split("\n\n", 1)[1])
        for excerpt in data["excerpts"]:
            evidence = excerpt["evidence"]
            if not isinstance(evidence, list):
                continue
            calls = [call["id"] for row in evidence for call in row.get("calls", [])]
            results = [row["id"] for row in evidence if row["role"] == "tool"]
            assert calls == results
        assert _size(request, 200) <= ceiling


def test_repeated_oversized_prompts_then_fitting_prompt_recover(tmp_path: Path):
    ceiling = _size(_measure(tmp_path)) + 5000
    provider = _RecordingToolProvider()
    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        settings=_settings(tmp_path, contextWindow=ceiling),
        load_context_files=False,
    ) as session:
        for _ in range(3):
            before = len(_summary_requests(provider))
            result = session.submit("intrinsically oversized paste " + "x" * 40000)
            assert result.preparation_failure is not None
            assert len(_summary_requests(provider)) - before <= 1
        assert session.submit("small enough now").preparation_failure is None
        assert all(_size(request) <= ceiling for request in provider.requests)
        assert session.submit("another fitting prompt").preparation_failure is None


def test_recovery_bounds_even_an_oversized_generated_summary(tmp_path: Path):
    effects, provider, budget = _oversized_effects(tmp_path)
    provider.on_complete = lambda _request: _result("😀" * 100000)
    outcome = effects.compact_context("manual", budget)
    assert outcome.result is not None
    assert len(outcome.result.summary) < len(RECOVERY_WARNING) + 400
    assert "omitted" in outcome.result.summary
    assert len(provider.requests) == 1
    assert effects.ctl.session_tree.get_entries()[-1].summary == outcome.result.summary


def test_recovery_warning_survives_later_complete_summary(tmp_path: Path):
    effects, provider, budget = _oversized_effects(tmp_path)
    assert effects.compact_context("manual", budget).result is not None
    effects.product_session.append_message(AgentUserMessage(ProductContent("next")))
    provider.on_complete = lambda _request: _result("Only supported facts.")
    outcome = effects.compact_context("manual", RequestBudget(10000, 300))
    assert outcome.result is not None
    assert outcome.result.summary == RECOVERY_WARNING + "\n\nOnly supported facts."


def test_recovery_allocates_prior_continuity_instead_of_warning_prefix(tmp_path: Path):
    effects, _provider = _fixture(tmp_path)
    source = compaction_request(
        binding=effects.coding_state.provider_binding,
        cwd=tmp_path,
        dropped_messages=effects.coding_state.messages,
        prior_summary=RECOVERY_WARNING + "\n\n" + "IMPORTANT_PRIOR_FACT " * 2000,
        header_callback=None,
    )
    assert _size(source, 300) > 10000
    request = recover_summary_request(source, RequestBudget(10000, 300))
    assert request is not None
    data = json.loads(request.messages[0].content.value.split("\n\n", 1)[1])
    prior = data["excerpts"][1]["evidence"][0]["text"]
    assert prior.startswith("Previous context summary:\nIMPORTANT_PRIOR_FACT")
    assert prior.count("IMPORTANT_PRIOR_FACT") > 20
    assert RECOVERY_WARNING not in prior
    assert _size(request, 300) <= 10000


@pytest.mark.parametrize("reduced", [False, True])
def test_accepted_summary_deduplicates_provider_warning(tmp_path: Path, reduced: bool):
    effects, provider, budget = _oversized_effects(tmp_path)
    effects.coding_state.rebuild_history(
        effects.coding_state.messages,
        summary_suffix="\n\n" + RECOVERY_WARNING + "\n\n" + "prior " * 10000,
    )
    provider.on_complete = lambda _request: _result(
        RECOVERY_WARNING + "\n\n" + RECOVERY_WARNING + "\n\nKeep meaningful facts."
    )
    outcome = effects.compact_context(
        "manual", budget if reduced else RequestBudget(100000, 300)
    )
    assert outcome.result is not None
    assert outcome.result.used_recovery_excerpts == reduced
    assert outcome.result.summary == RECOVERY_WARNING + "\n\nKeep meaningful facts."


def test_recovery_managed_retry_keeps_one_private_request(tmp_path: Path):
    from test_native_semantic_compaction_retry import _prepared_fixture, _transient

    effects, provider = _prepared_fixture(tmp_path, [_transient(), _result("summary")])
    effects.coding_state.rebuild_history(
        effects.coding_state.messages, summary_suffix="prior " * 10000
    )
    effects.settings.set_value("retry.maxRetries", 1)
    effects.settings.set_value("retry.baseDelayMs", 1)
    outcome = effects.compact_context("manual", RequestBudget(2400, 300))
    assert outcome.result is not None and outcome.result.used_recovery_excerpts
    assert len(provider.requests) == 1
    assert [a.attempt for a in provider.allowances] == [1, 2]
    assert provider.sinks == [(None, None)]
    assert _size(provider.requests[0], 300) <= 2400
