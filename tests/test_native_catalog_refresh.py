"""RF-P3 pinned metadata through pricing, selection, admission and footer."""

from __future__ import annotations

import io
from dataclasses import replace

import pytest
from test_native_catalog_pricing import _catalog, _fields, _footer, _provider_state
from test_native_coding_session_resume_compact import _RecordingToolProvider
from test_native_request_budget_admission import _settings, _size

from pipy_harness.native import chrome
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.messages import AgentMessageUsage
from pipy_harness.native.agent.usage import (
    AgentProviderUsageSample,
    AgentUsageAccumulator,
)
from pipy_harness.native.catalog import build_builtin_catalog
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.repl.turn_leaves import pricing_for
from pipy_harness.native.repl_state import (
    ModelRuntime,
    NativeModelSelection,
    NativeReplProviderState,
)
from pipy_harness.native.session_tree import NativeSessionTree

CONTEXT_ROWS = [
    ("anthropic", "claude-sonnet-4-5", 200_000, 64_000, "200k"),
    ("anthropic", "claude-sonnet-4-5-20250929", 200_000, 64_000, "200k"),
    ("mistral", "mistral-small-latest", 262_144, 256_000, "262k"),
]
PRICE_ROWS = [
    ("anthropic", "claude-sonnet-5-5"),
    ("github-copilot", "claude-sonnet-5.5"),
]


@pytest.mark.parametrize("provider,model", PRICE_ROWS)
@pytest.mark.parametrize("override", [None, 0.7])
def test_selected_sonnet_cache_price_and_subscription_marker(
    tmp_path, provider, model, override
):
    overrides = (
        None
        if override is None
        else {
            "providers": {
                provider: {"modelOverrides": {model: {"cost": {"cacheRead": override}}}}
            }
        }
    )
    state = _provider_state(
        tmp_path, NativeModelSelection(provider, model), models_json=overrides
    )
    catalog = state.model_runtime.catalog
    assert catalog.auth_store is not None
    catalog.auth_store.set(provider, {"type": "oauth", "access": "test-only"})
    usage = AgentUsageAccumulator(pricing_for(state, provider, model))
    usage.absorb(
        AgentProviderUsageSample(cache_read_tokens=1_000_000, total_tokens=1_000_000)
    )
    expected = 0.1 if override is None else override
    assert usage.cost_usd == pytest.approx(expected)
    using_subscription = _footer(tmp_path, state)._using_subscription(provider)
    assert using_subscription
    fields = replace(
        _fields(usage.cost_usd, using_subscription),
        provider_name=provider,
        model_id=model,
    )
    assert chrome.format_bottom_status_line(140, fields).startswith(
        f"${expected:.3f} (sub) "
    )
    catalog.auth_store.set(provider, {"type": "api_key", "key": "test-only"})
    assert not _footer(tmp_path, state)._using_subscription(provider)
    assert chrome.format_bottom_status_line(
        140, replace(fields, using_subscription=False)
    ).startswith(f"${expected:.3f} ")


@pytest.mark.parametrize("provider,model,context,output,label", CONTEXT_ROWS)
@pytest.mark.parametrize("override", [None, 333_333])
def test_selected_context_reaches_footer_and_override_wins(
    tmp_path, monkeypatch, provider, model, context, output, label, override
):
    overrides = (
        None
        if override is None
        else {
            "providers": {
                provider: {"modelOverrides": {model: {"contextWindow": override}}}
            }
        }
    )
    state = _provider_state(
        tmp_path, NativeModelSelection(provider, model), models_json=overrides
    )
    resolved = state.model_runtime.resolve_spec(state.selection)
    assert resolved is not None and resolved.max_tokens == output
    assert resolved.declared_context_window == (
        context if override is None else override
    )
    effects = _footer(tmp_path, state)
    effects.coding_state.rebind_provider(
        _RecordingToolProvider(),
        provider_name=provider,
        model_id=model,
        usage_accumulator=AgentUsageAccumulator(),
    )
    effects.coding_state.append_message(
        AgentAssistantMessage(
            ProductContent("answer"),
            usage=AgentMessageUsage(input=1000, total_tokens=1000),
        )
    )
    seen = []
    monkeypatch.setattr(
        chrome,
        "format_bottom_status_line",
        lambda width, fields: seen.append(fields) or "footer",
    )
    effects.coding_footer_text()
    assert seen[-1].context_used_pct == pytest.approx(
        100 * 1000 / (context if override is None else override)
    )
    assert seen[-1].context_budget_label == (label if override is None else "333k")


class _SelectedFixtureState(NativeReplProviderState):
    """Keep real catalog resolution; replace only the network provider port."""

    def __init__(self, provider, **kwargs):
        super().__init__(**kwargs)
        self.fixture_provider = provider

    def provider_for(self, selection):
        return self.fixture_provider


@pytest.mark.parametrize("provider,model,context,output,label", CONTEXT_ROWS)
@pytest.mark.parametrize(
    "override,ceiling,admitted",
    [(None, None, None), (333_333, None, True), (333_333, 190_000, False)],
)
def test_selected_catalog_limit_controls_real_request_admission(
    tmp_path, provider, model, context, output, label, override, ceiling, admitted
):
    del output, label
    recording = _RecordingToolProvider()
    recording.name, recording.model_id = provider, model
    overrides = (
        None
        if override is None
        else {
            "providers": {
                provider: {"modelOverrides": {model: {"contextWindow": override}}}
            }
        }
    )
    catalog = _catalog(tmp_path, models_json=overrides)
    state = _SelectedFixtureState(
        recording,
        selection=NativeModelSelection(provider, model),
        model_runtime=ModelRuntime(catalog),
    )
    policy = {} if ceiling is None else {"contextWindow": ceiling}
    settings = _settings(tmp_path, enabled=False, **policy)
    tree = NativeSessionTree.create(tmp_path, persist=False)
    # Keep each canonical message below its existing per-message limit.
    for _ in range(3):
        tree.append_message(
            AgentUserMessage(
                ProductContent("x" * (200_000 if provider == "anthropic" else 206_667))
            )
        )
        tree.append_message(AgentAssistantMessage(ProductContent("retained")))
    errors = io.StringIO()
    session = CodingSession(
        provider=recording,
        provider_state=state,
        tool_registry={},
        settings_manager=settings,
        native_session=tree,
    )
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("continue\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=errors,
    )
    expected = (provider == "mistral") if admitted is None else admitted
    assert len(recording.requests) == int(expected), errors.getvalue()[-1000:]
    if expected:
        estimate = _size(recording.requests[0])
        assert (
            (200_000 if provider == "anthropic" else 256_000)
            < estimate
            <= (context if override is None else override)
        )
    else:
        assert "context" in errors.getvalue().lower()


def test_refresh_preserves_selected_output_effort_and_compatibility():
    catalog = build_builtin_catalog()
    for provider, model, context, output, _ in CONTEXT_ROWS:
        row = catalog.find(provider, model)
        assert (
            row is not None
            and row.context_window == context
            and row.max_tokens == output
        )
        assert row.reasoning and row.compat is None
        assert row.thinking_level_map == (
            {
                "off": "none",
                "minimal": None,
                "low": None,
                "medium": None,
                "high": "high",
                "xhigh": None,
                "max": None,
            }
            if provider == "mistral"
            else {}
        )
    for provider, model in PRICE_ROWS:
        row = catalog.find(provider, model)
        assert row is not None and (
            row.cost.input,
            row.cost.output,
            row.cost.cache_read,
            row.cost.cache_write,
        ) == (2.0, 10.0, 0.1, 2.5)
        assert row.context_window == 1_000_000 and row.max_tokens == 128_000
        assert (
            row.thinking_level_map["high"] == "high"
            and row.compat["forceAdaptiveThinking"]
        )
