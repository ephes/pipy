"""Usage stored on every assistant message and summed from the session (USAGE1).

Pi (``1b347794e``) stores ``usage`` (``packages/ai/src/types.ts`` ``Usage``,
priced by ``calculateCost``) and ``provider``/``model`` on each assistant
message. ``getSessionStats``, the footer and ``/session`` sum every stored
message of the session file -- all branches, compacted history included --
so the totals survive resume and a model switch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from test_native_agent_loop import _make_loop, _provider_result, _run_input

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentCancellationReason,
    AgentMessageUsage,
    AgentStopReason,
    AgentToolResultMessage,
    AgentUsageCost,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.agent.usage import (
    AgentProviderUsageSample,
    AgentTokenPricing,
    AgentTokenPricingTier,
    AgentUsageAccumulator,
    message_usage,
)
from pipy_harness.native.agent.usage_json import usage_from_json, usage_to_json
from pipy_harness.native.automation.agent_events import AutomationAgentEventAdapter
from pipy_harness.native.automation.serialize import serialize_message
from pipy_harness.native.session_tree import MessageEntry, NativeSessionTree
from pipy_harness.native.session_usage import (
    cache_waste,
    format_session_info,
    format_tokens,
    session_stats,
    to_fixed,
    usage_cost_breakdown,
    usage_totals,
)

_PRICING = AgentTokenPricing(
    input_per_million=2.0,
    output_per_million=10.0,
    cache_read_per_million=0.1,
    cache_write_per_million=2.5,
)


def _usage(
    input_tokens: int = 0,
    output: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
    *,
    pricing: AgentTokenPricing | None = _PRICING,
) -> AgentMessageUsage:
    return message_usage(
        AgentProviderUsageSample(
            input_tokens=input_tokens,
            output_tokens=output,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            # Anthropic-style: cache counters beside input.
            total_tokens=input_tokens + output + cache_read + cache_write,
        ),
        pricing,
    )


def _answer(
    text: str,
    usage: AgentMessageUsage | None,
    provider: str | None = "openai-codex",
    model: str | None = "gpt-6.1-sol",
) -> AgentAssistantMessage:
    return AgentAssistantMessage(
        ProductContent(text), usage=usage, provider=provider, model=model
    )


# -- per-message usage (Pi Usage + calculateCost) -------------------------------


def test_message_usage_splits_an_inclusive_prompt_into_uncached_and_cached() -> None:
    usage = message_usage(
        AgentProviderUsageSample.from_mapping(
            {
                "input_tokens": 1_000,
                "output_tokens": 200,
                "reasoning_tokens": 50,
                "cached_tokens": 600,
                "total_tokens": 1_200,
            }
        ),
        _PRICING,
    )

    assert (usage.input, usage.output, usage.cache_read, usage.cache_write) == (
        400,
        200,
        600,
        0,
    )
    assert usage.total_tokens == 1_200
    assert usage.reasoning == 50
    assert usage.cache_write_1h is None
    assert usage.cost.input == pytest.approx(400 * 2.0 / 1e6)
    assert usage.cost.output == pytest.approx(200 * 10.0 / 1e6)
    assert usage.cost.cache_read == pytest.approx(600 * 0.1 / 1e6)
    assert usage.cost.total == pytest.approx(
        usage.cost.input + usage.cost.output + usage.cost.cache_read
    )


def test_message_usage_keeps_separate_cache_counters_and_prices_1h_writes() -> None:
    usage = message_usage(
        AgentProviderUsageSample.from_mapping(
            {
                "input_tokens": 100,
                "output_tokens": 10,
                "cached_tokens": 1_000,
                "cache_write_tokens": 500,
                "cache_write_1h_tokens": 200,
                "total_tokens": 1_610,
            }
        ),
        _PRICING,
    )

    assert (usage.input, usage.cache_read, usage.cache_write) == (100, 1_000, 500)
    assert usage.cache_write_1h == 200
    assert usage.reasoning is None
    # Pi: short writes at cacheWrite, 1h writes at 2x input.
    assert usage.cost.cache_write == pytest.approx((300 * 2.5 + 200 * 2.0 * 2) / 1e6)


def test_message_usage_uses_the_tier_of_the_whole_prompt() -> None:
    pricing = AgentTokenPricing(
        1.0,
        2.0,
        tiers=(AgentTokenPricingTier(1_000, 5.0, 6.0),),
    )
    below = message_usage(
        AgentProviderUsageSample(input_tokens=1_000, output_tokens=1), pricing
    )
    above = message_usage(
        AgentProviderUsageSample(input_tokens=1_001, output_tokens=1), pricing
    )
    assert below.cost.input == pytest.approx(1_000 * 1.0 / 1e6)
    assert above.cost.input == pytest.approx(1_001 * 5.0 / 1e6)


def test_message_usage_without_pricing_or_total() -> None:
    usage = message_usage(
        AgentProviderUsageSample(input_tokens=7, output_tokens=3), None
    )
    assert usage.total_tokens == 10
    assert usage.cost == AgentUsageCost()


def test_accumulator_cost_is_the_sum_of_message_costs() -> None:
    samples = [
        AgentProviderUsageSample(
            input_tokens=1_000,
            output_tokens=5,
            cache_read_tokens=600,
            total_tokens=1_005,
        ),
        AgentProviderUsageSample(input_tokens=30, output_tokens=40),
    ]
    accumulator = AgentUsageAccumulator(_PRICING)
    for sample in samples:
        accumulator.absorb(sample)
    assert accumulator.cost_usd == pytest.approx(
        sum(message_usage(s, _PRICING).cost.total for s in samples)
    )


def test_usage_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        AgentMessageUsage(input=-1)
    with pytest.raises(ValueError):
        AgentUsageCost(total=float("nan"))
    with pytest.raises(TypeError):
        AgentAssistantMessage(ProductContent("x"), usage=cast(Any, {}))
    with pytest.raises(ValueError):
        AgentAssistantMessage(ProductContent("x"), provider="")


def test_usage_metadata_takes_no_part_in_message_equality() -> None:
    assert _answer("a", _usage(1, 2)) == AgentAssistantMessage(ProductContent("a"))


# -- the agent loop ------------------------------------------------------------


def test_loop_stores_usage_and_attribution_on_the_answer() -> None:
    loop, _provider, _events, _usage_publisher = _make_loop(
        [],
        [
            ProviderTurnOutcome(
                result=_provider_result(
                    usage={"input_tokens": 1_000_000, "output_tokens": 10}
                )
            )
        ],
    )

    outcome = loop.run(_run_input())

    answer = outcome.final_history[-1]
    assert isinstance(answer, AgentAssistantMessage)
    assert answer.usage is not None
    assert answer.usage.input == 1_000_000
    # _run_input prices input at $1/M.
    assert answer.usage.cost.input == pytest.approx(1.0)
    assert (answer.provider, answer.model) == ("fake", "fake-model")


def test_loop_stores_usage_on_a_failed_turn_and_zero_on_an_aborted_one() -> None:
    loop, _provider, _events, _usage_publisher = _make_loop(
        [],
        [
            ProviderTurnOutcome(
                result=_provider_result(
                    status=HarnessStatus.FAILED,
                    usage={"input_tokens": 50, "output_tokens": 5},
                )
            )
        ],
    )
    failed = loop.run(_run_input()).final_history[-1]
    assert isinstance(failed, AgentAssistantMessage)
    assert failed.stop_reason is AgentStopReason.ERROR
    assert failed.usage is not None and failed.usage.input == 50
    assert failed.provider == "fake"

    loop, _provider, _events, _usage_publisher = _make_loop(
        [],
        [
            ProviderTurnOutcome(
                cancellation_reason=AgentCancellationReason.OPERATOR_ABORT
            )
        ],
    )
    aborted = loop.run(_run_input()).final_history[-1]
    assert isinstance(aborted, AgentAssistantMessage)
    assert aborted.stop_reason is AgentStopReason.ABORTED
    assert aborted.usage == AgentMessageUsage()
    assert aborted.model == "fake-model"


# -- persistence ---------------------------------------------------------------


def test_usage_round_trips_through_the_session_file(tmp_path: Path) -> None:
    tree = NativeSessionTree.create(tmp_path, state_root=tmp_path / "state")
    usage = _usage(10, 20, 30, 40)
    tree.append_message(AgentUserMessage(ProductContent("q")))
    tree.append_message(_answer("a", usage))
    assert tree.path is not None

    reopened = NativeSessionTree.open(tree.path)
    stored = reopened.get_entries()[-1].message  # type: ignore[union-attr]
    assert isinstance(stored, AgentAssistantMessage)
    assert stored.usage == usage
    assert (stored.provider, stored.model) == ("openai-codex", "gpt-6.1-sol")
    raw = json.loads(tree.path.read_text().splitlines()[-1])
    assert raw["message"]["usage"] == usage_to_json(usage)
    assert set(raw["message"]["usage"]) == {
        "input",
        "output",
        "cacheRead",
        "cacheWrite",
        "totalTokens",
        "cost",
    }


def test_an_old_entry_without_usage_loads_unchanged(tmp_path: Path) -> None:
    tree = NativeSessionTree.create(tmp_path, state_root=tmp_path / "state")
    tree.append_message(AgentUserMessage(ProductContent("q")))
    tree.append_message(AgentAssistantMessage(ProductContent("old")))
    assert tree.path is not None
    raw = json.loads(tree.path.read_text().splitlines()[-1])
    assert "usage" not in raw["message"] and "provider" not in raw["message"]

    reopened = NativeSessionTree.open(tree.path)
    stored = reopened.get_entries()[-1].message  # type: ignore[union-attr]
    assert isinstance(stored, AgentAssistantMessage)
    assert stored.usage is None and stored.provider is None
    assert session_stats(reopened)["cost"] == 0.0


@pytest.mark.parametrize(
    "bad",
    [
        {"input": -1},
        {"input": "1"},
        {"cost": None},
        {"cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}},
    ],
)
def test_malformed_usage_is_rejected(bad: dict[str, Any]) -> None:
    body = usage_to_json(_usage(1, 1))
    body.update(bad)
    with pytest.raises(ValueError):
        usage_from_json(body)


# -- totals, stats, breakdown --------------------------------------------------


def _two_branch_tree(tmp_path: Path) -> NativeSessionTree:
    """Two turns on one model, a model switch, then a sibling branch."""

    tree = NativeSessionTree.create(tmp_path, persist=False)
    root = tree.append_message(AgentUserMessage(ProductContent("one")))
    tree.append_message(_answer("a1", _usage(100, 10, 1_000, 0)))
    tree.append_message(AgentUserMessage(ProductContent("two")))
    tree.append_message(
        AgentAssistantMessage(
            ProductContent(""),
            (),
            stop_reason=AgentStopReason.ERROR,
            error_message="boom",
            usage=_usage(5, 0),
            provider="openai-codex",
            model="gpt-6.1-sol",
        )
    )
    tree.append_model_change("anthropic", "claude-x")
    tree.append_message(AgentUserMessage(ProductContent("three")))
    tree.append_message(_answer("a3", _usage(20, 30, 0, 400), "anthropic", "claude-x"))
    # A sibling branch from the first user message: Pi counts every entry.
    tree.branch(root.id)
    tree.append_message(_answer("b1", _usage(1, 1), "anthropic", "claude-x"))
    tree.append_message(
        AgentToolResultMessage(
            "pipy-tool-request-1", "read", ProductContent("r"), "call-1"
        )
    )
    return tree


def test_totals_sum_every_branch_and_survive_a_model_switch(tmp_path: Path) -> None:
    tree = _two_branch_tree(tmp_path)
    entries = tree.get_entries()
    totals = usage_totals(entries)

    assert (totals.input, totals.output, totals.cache_read, totals.cache_write) == (
        126,
        41,
        1_000,
        400,
    )
    costs = [
        e.message.usage.cost.total
        for e in entries
        if isinstance(e, MessageEntry)
        and isinstance(e.message, AgentAssistantMessage)
        and e.message.usage is not None
    ]
    assert len(costs) == 4
    assert totals.cost == pytest.approx(sum(costs))
    # Latest message (the sibling branch's): no cache activity.
    assert totals.latest_cache_hit_rate == 0.0

    stats = session_stats(tree)
    assert stats["sessionFile"] is None
    assert stats["totalMessages"] == 8
    assert (stats["userMessages"], stats["assistantMessages"]) == (3, 4)
    assert (stats["toolCalls"], stats["toolResults"]) == (0, 1)
    assert stats["tokens"] == {
        "input": 126,
        "output": 41,
        "cacheRead": 1_000,
        "cacheWrite": 400,
        "total": 1_567,
    }


def test_breakdown_groups_by_provider_and_model_sorted_by_cost(
    tmp_path: Path,
) -> None:
    breakdown = usage_cost_breakdown(_two_branch_tree(tmp_path).get_entries())
    assert [entry.key for entry in breakdown] == [
        "anthropic/claude-x",
        "openai-codex/gpt-6.1-sol",
    ]
    assert breakdown[0].tokens == 20 + 30 + 400 + 1 + 1
    assert breakdown[1].tokens == 100 + 10 + 1_000 + 5


def test_latest_cache_hit_rate_is_the_last_messages() -> None:
    tree = NativeSessionTree.create(Path("/tmp"), persist=False)
    tree.append_message(_answer("a", _usage(100, 1, 900)))
    assert usage_totals(tree.get_entries()).latest_cache_hit_rate == 90.0
    tree.append_message(_answer("b", _usage(0, 1)))
    assert usage_totals(tree.get_entries()).latest_cache_hit_rate is None


# -- cache waste (Pi computeCacheWaste) ----------------------------------------


def test_cache_waste_counts_rebilled_prompt_tokens(tmp_path: Path) -> None:
    tree = NativeSessionTree.create(tmp_path, persist=False)
    tree.append_message(_answer("1", _usage(100, 1, 10_000)))
    # Prompt grew but only 2,000 were read back: 8,100 re-billed.
    tree.append_message(_answer("2", _usage(8_200, 1, 2_000)))
    # A miss at the noise floor is not counted.
    tree.append_message(_answer("3", _usage(1_024, 1, 9_176)))

    waste = cache_waste(tree.get_entries(), lambda _p, _m: 0.1)
    assert waste.miss_count == 1
    assert waste.missed_tokens == 10_100 - 2_000
    assert waste.missed_cost == pytest.approx(8_100 * (2.0 - 0.1) / 1e6)


def test_cache_waste_uses_the_row_rate_after_a_total_miss(tmp_path: Path) -> None:
    tree = NativeSessionTree.create(tmp_path, persist=False)
    tree.append_message(_answer("1", _usage(10, 1, 5_000)))
    tree.append_message(_answer("2", _usage(5_010, 1)))
    rates: list[tuple[str, str]] = []

    def rate(provider: str, model: str) -> float:
        rates.append((provider, model))
        return 0.5

    waste = cache_waste(tree.get_entries(), rate)
    assert rates == [("openai-codex", "gpt-6.1-sol")]
    assert waste.missed_tokens == 5_010
    assert waste.missed_cost == pytest.approx(5_010 * (2.0 - 0.5) / 1e6)


def test_cache_waste_resets_at_a_compaction_and_needs_reported_cache(
    tmp_path: Path,
) -> None:
    tree = NativeSessionTree.create(tmp_path, persist=False)
    # A provider that never reports caching never counts a miss.
    tree.append_message(_answer("1", _usage(5_000, 1)))
    tree.append_message(_answer("2", _usage(9_000, 1)))
    assert cache_waste(tree.get_entries(), lambda _p, _m: 0.0).miss_count == 0

    tree.append_message(_answer("3", _usage(10, 1, 9_000)))
    tree.append_compaction(summary="s", first_kept_entry_id=None, tokens_before=1)
    tree.append_message(_answer("4", _usage(9_010, 1)))
    assert cache_waste(tree.get_entries(), lambda _p, _m: 0.0).miss_count == 0


# -- Pi number formatting ------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "text"),
    [
        (0, "0"),
        (999, "999"),
        (1_000, "1.0k"),
        (1_050, "1.1k"),
        (9_999, "10.0k"),
        (10_000, "10k"),
        (11_500, "12k"),
        (999_499, "999k"),
        (1_000_000, "1.0M"),
        (3_500_000, "3.5M"),
        (12_500_000, "13M"),
    ],
)
def test_format_tokens_matches_pi(count: int, text: str) -> None:
    assert format_tokens(count) == text


def test_to_fixed_rounds_the_binary_value_like_javascript() -> None:
    assert to_fixed(0.0045, 3) == "0.004"  # stored just below the tie
    assert to_fixed(0.0625, 3) == "0.063"  # an exact tie rounds up
    assert to_fixed(0.0, 3) == "0.000"
    assert to_fixed(99.95, 1) == "100.0"  # 99.95 is stored above the tie


# -- /session ------------------------------------------------------------------


def test_session_info_renders_pi_sections(tmp_path: Path) -> None:
    tree = _two_branch_tree(tmp_path)
    tree.append_session_info("usage-demo")

    text = format_session_info(
        tree,
        selected_model=("anthropic", "claude-x"),
        cache_read_rate=lambda _p, _m: 0.0,
    )
    lines = text.splitlines()

    assert lines[:4] == ["Session Info", "", "Name: usage-demo", "File: In-memory"]
    assert f"ID: {tree.session_id}" in lines
    assert lines[lines.index("Messages") + 1 :][:4] == [
        "Total: 8",
        "User: 3",
        "Assistant: 4",
        "Tools: 0 calls, 1 results",
    ]
    tokens = lines[lines.index("Tokens") + 1 :]
    assert tokens[:5] == [
        "Input: 1,526",
        "  Cached: 1,000 (65.5%)",
        "  Uncached: 526 (400 written to cache)",
        "Output: 41",
        "Total: 1,567",
    ]
    cost = lines[lines.index("Cost") + 1 :]
    total = session_stats(tree)["cost"]
    assert cost[0] == f"Total: ${to_fixed(total, 3)}"
    assert cost[1].startswith("  anthropic/claude-x: $")
    assert cost[1].endswith("(452 tokens)")
    assert cost[2].startswith("  openai-codex/gpt-6.1-sol: $")
    assert "Cache Warming" not in text


def test_session_info_hides_a_breakdown_that_repeats_the_selected_model(
    tmp_path: Path,
) -> None:
    tree = NativeSessionTree.create(tmp_path, persist=False)
    tree.append_message(_answer("a", _usage(10, 1)))

    same = format_session_info(
        tree,
        selected_model=("openai-codex", "gpt-6.1-sol"),
        cache_read_rate=lambda _p, _m: 0.0,
    )
    other = format_session_info(
        tree,
        selected_model=("anthropic", "claude-x"),
        cache_read_rate=lambda _p, _m: 0.0,
    )
    assert same.splitlines()[-1].startswith("Total: $")
    assert other.splitlines()[-1].startswith("  openai-codex/gpt-6.1-sol: $")


def test_session_info_has_no_cost_section_without_cost(tmp_path: Path) -> None:
    tree = NativeSessionTree.create(tmp_path, persist=False)
    tree.append_message(_answer("a", _usage(10, 1, pricing=None)))
    text = format_session_info(
        tree, selected_model=("p", "m"), cache_read_rate=lambda _p, _m: 0.0
    )
    assert "Cost" not in text.splitlines()
    assert "  Cached:" not in text


# -- RPC -----------------------------------------------------------------------


def test_rpc_stats_and_messages_come_from_the_stored_session(tmp_path: Path) -> None:
    from test_native_automation_rpc import _RpcClient
    from test_native_coding_session import _UsageScriptProvider

    from pipy_harness.native.provider import ProviderPort

    usage = {"input_tokens": 1_000, "output_tokens": 100, "total_tokens": 1_100}
    provider = _UsageScriptProvider(
        ((usage, ()), (usage, ())), name="openai-codex", model_id="gpt-6.1-sol"
    )
    client = _RpcClient(
        tmp_path, provider=cast(ProviderPort, provider), persist_tree=True
    )
    try:
        for index in range(2):
            client.send({"id": f"p{index}", "type": "prompt", "message": "go"})
            client.wait_for(lambda record, i=index: record.get("id") == f"p{i}")
            client.wait_for(lambda record: record.get("type") == "agent_settled")
        client.send({"id": "stats", "type": "get_session_stats"})
        stats = client.wait_for(lambda record: record.get("id") == "stats")["data"]
        client.send({"id": "msgs", "type": "get_messages"})
        messages = client.wait_for(lambda record: record.get("id") == "msgs")["data"]
    finally:
        client.close()

    assert stats["assistantMessages"] == 2
    assert stats["tokens"]["input"] == 2_000
    assert stats["cost"] > 0
    # A resumed session reads the same totals from the file.
    assert client.tree.path is not None
    reopened = NativeSessionTree.open(client.tree.path)
    assert session_stats(reopened) == stats
    answers = [m for m in messages["messages"] if m["role"] == "assistant"]
    assert [m["usage"]["input"] for m in answers] == [1_000, 1_000]
    assert answers[0]["model"] == "gpt-6.1-sol"
    assert sum(m["usage"]["cost"]["total"] for m in answers) == pytest.approx(
        stats["cost"]
    )


# -- JSON ----------------------------------------------------------------------


def test_json_assistant_messages_carry_pi_usage() -> None:
    usage = _usage(1, 2, 3, 4)
    body = serialize_message(_answer("hi", usage))
    assert body["provider"] == "openai-codex"
    assert body["model"] == "gpt-6.1-sol"
    assert body["usage"] == usage_to_json(usage)
    assert body["stopReason"] == "stop"

    old = serialize_message(AgentAssistantMessage(ProductContent("old")))
    assert old["usage"] == usage_to_json(AgentMessageUsage())
    assert "provider" not in old


def test_streamed_partial_carries_a_usage_object() -> None:
    from pipy_harness.native.agent import AssistantTextDelta

    records: list[dict[str, Any]] = []

    class _Sink:
        def emit(self, event: dict[str, Any]) -> None:
            records.append(event)

    adapter = AutomationAgentEventAdapter(cast(Any, _Sink()))
    adapter.emit(AssistantTextDelta(0, ProductContent("x")))
    partial = records[-1]["assistantMessageEvent"]["partial"]
    assert set(partial["usage"]) == {
        "input",
        "output",
        "cacheRead",
        "cacheWrite",
        "totalTokens",
        "cost",
    }
