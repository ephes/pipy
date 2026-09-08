"""Live model-budget admission through existing product and compaction owners."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_native_coding_session_resume_compact import _RecordingToolProvider
from test_native_semantic_compaction import _fixture, _published
from test_product_session_api import Sink

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.events import (
    MessageStarted,
    ProviderFailed,
    RunCancelled,
    ToolCallCompleted,
)
from pipy_harness.native.agent.request import (
    freeze_provider_request,
    snapshot_provider_request,
)
from pipy_harness.native.agent.results import AgentCancellationReason
from pipy_harness.native.agent_loop_policy import NativeAgentProviderRequestPolicy
from pipy_harness.native.coding.compaction import compaction_request
from pipy_harness.native.coding.request_budget import estimate_request
from pipy_harness.native.coding.state import CodingContextChangedError
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.repl.provider_selection import ProviderMutationEffects
from pipy_harness.native.session_tree import CompactionEntry, NativeSessionTree
from pipy_harness.native.settings import SettingsManager
from pipy_harness.sdk import create_product_session


def _settings(tmp_path: Path, **policy: Any) -> SettingsManager:
    manager = SettingsManager(global_path=tmp_path / "settings.json", env={})
    manager.set_value("compaction", {"reserveTokens": 0, **policy})
    return manager


def _size(request: ProviderRequest, reserve: int = 0) -> int:
    return estimate_request(
        freeze_provider_request(request),
        image_count=len(request.attachments),
        output_reserve=reserve,
    ).total_tokens


def _tree(
    tmp_path: Path, groups: int = 3, text: str = "facts " * 100, *, persist: bool = True
) -> NativeSessionTree:
    tree = NativeSessionTree.create(
        tmp_path, session_dir=tmp_path / "sessions", persist=persist
    )
    for i in range(groups):
        tree.append_message(AgentUserMessage(ProductContent(f"group {i}: {text}")))
        tree.append_message(AgentAssistantMessage(ProductContent("acknowledged")))
    return tree


def _measure(
    tmp_path: Path, *, tree: NativeSessionTree | None = None, prompt: str = "continue"
) -> ProviderRequest:
    provider = _RecordingToolProvider()
    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        tools={},
        settings=_settings(tmp_path),
        tree=tree,
        load_context_files=False,
    ) as session:
        session.submit(prompt)
    return provider.requests[-1]


@pytest.mark.parametrize("offset", [0, -1])
def test_final_exact_threshold_and_refusal_keeps_lifetime(tmp_path, offset):
    request = _measure(tmp_path)
    ceiling = _size(request) + offset
    settings = _settings(tmp_path, enabled=False, contextWindow=ceiling)
    provider = _RecordingToolProvider()
    sink = Sink()
    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        tools={},
        settings=settings,
        observer=sink,
        load_context_files=False,
    ) as session:
        result = session.submit("continue")
        assert (result.preparation_failure is None) == (offset == 0)
        assert len(provider.requests) == (1 if offset == 0 else 0)
        assert len([m for m in result.messages if isinstance(m, AgentUserMessage)]) == 1
        if offset:
            assert not any(
                isinstance(e, (ProviderFailed, RunCancelled)) for e in sink.events
            )
            assert not any(
                isinstance(e, MessageStarted)
                and isinstance(e.message, AgentAssistantMessage)
                for e in sink.events
            )
            settings.set_value("compaction.contextWindow", ceiling + 10000)
            recovered = session.submit("next")
            assert recovered.preparation_failure is None and len(provider.requests) == 1
            assert recovered.messages[: len(result.messages)] == result.messages


@pytest.mark.parametrize(
    "key,value",
    [
        ("reserveTokens", True),
        ("reserveTokens", None),
        ("contextWindow", None),
        ("contextWindow", False),
        ("reserveTokens", 10000),
    ],
)
def test_invalid_policy_refuses_without_provider_or_hooks(
    tmp_path, monkeypatch, key, value
):
    settings = _settings(
        tmp_path, contextWindow=5000, **({key: value} if key != "contextWindow" else {})
    )
    settings.set_value(f"compaction.{key}", value)
    provider = _RecordingToolProvider()

    def forbidden(*args, **kwargs):
        raise AssertionError("ordinary hooks ran for invalid configuration")

    monkeypatch.setattr(NativeAgentProviderRequestPolicy, "prepare", forbidden)
    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        tools={},
        settings=settings,
        load_context_files=False,
    ) as session:
        result = session.submit("private prompt")
        assert (
            result.preparation_failure is not None and result.provider_failure is None
        )
        assert provider.requests == []


def test_known_limit_replaces_count_trigger_but_unknown_keeps_it(tmp_path, monkeypatch):
    for known in (True, False):
        ws = tmp_path / str(known)
        ws.mkdir()
        provider = _RecordingToolProvider()
        tree = _tree(ws, groups=24, text="small")
        settings = _settings(ws, **({"contextWindow": 100000} if known else {}))
        with create_product_session(
            workspace=ws,
            provider=provider,
            tools={},
            settings=settings,
            tree=tree,
            load_context_files=False,
        ) as session:
            result = session.submit("continue")
            assert result.preparation_failure is None
        summaries = [
            r
            for r in provider.requests
            if r.system_prompt.startswith("Summarize conversation context")
        ]
        assert len(summaries) == (0 if known else 1)
        ordinary_users = [
            m for m in provider.requests[-1].messages if isinstance(m, AgentUserMessage)
        ]
        assert len(ordinary_users) == (25 if known else 2)


def test_first_iteration_durable_anchor_refusal_settles_user_and_can_recover(tmp_path):
    prompt = "continue " + "latest facts " * 100
    ceiling = _size(_measure(tmp_path, tree=_tree(tmp_path), prompt=prompt)) - 1
    provider = _RecordingToolProvider()
    tree = _tree(tmp_path)
    settings = _settings(tmp_path, contextWindow=ceiling)
    before = tree.build_coding_context().messages
    diagnostics = []
    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        tools={},
        settings=settings,
        tree=tree,
        load_context_files=False,
        diagnostic_sink=diagnostics.append,
    ) as session:
        result = session.submit(prompt)
        assert result.preparation_failure is not None
        assert provider.requests == []
        assert "retained history has no durable origin" in "".join(diagnostics)
        assert not any(isinstance(e, CompactionEntry) for e in tree.get_entries())
        assert result.messages == (*before, AgentUserMessage(ProductContent(prompt)))
        assert tree.build_coding_context().messages == result.messages
        settings.set_value("compaction.contextWindow", ceiling + 10000)
        assert session.submit("next").preparation_failure is None
    assert len(provider.requests) == 1


@pytest.mark.parametrize("overflow", [False, True])
def test_later_iteration_latest_group_summary_retains_tools_and_reopens(
    tmp_path, monkeypatch, overflow
):
    from pipy_harness.native.models import ProviderToolCall

    (tmp_path / "notes.txt").write_text("latest tool evidence " * 600)
    script = ((ProviderToolCall("read-notes", "read", '{"path":"notes.txt"}'),),)
    calibration = _RecordingToolProvider(call_script=script)
    with create_product_session(
        workspace=tmp_path,
        provider=calibration,
        settings=_settings(tmp_path),
        tree=_tree(tmp_path),
        load_context_files=False,
    ) as session:
        session.submit("inspect notes")
    assert len(calibration.requests) == 2
    ceiling = _size(calibration.requests[-1]) - 1
    assert _size(calibration.requests[0]) < ceiling
    provider = _RecordingToolProvider(call_script=script)
    original = provider.complete

    def complete(request, **kwargs):
        result = original(request, **kwargs)
        return (
            replace(result, final_text="Combined goals and verified results.")
            if request.system_prompt.startswith("Summarize conversation context")
            else result
        )

    provider.complete = complete
    tree = _tree(tmp_path)
    prepared_requests = []
    original_prepare = NativeAgentProviderRequestPolicy.prepare

    def prepare(self, policy_input):
        snapshot = original_prepare(self, policy_input)
        if prepared_requests and overflow:
            snapshot = snapshot_provider_request(
                snapshot.request, system_prompt="Combined goals " * 10000
            )
        prepared_requests.append(snapshot.request)
        return snapshot

    monkeypatch.setattr(NativeAgentProviderRequestPolicy, "prepare", prepare)
    sink = Sink()
    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        settings=_settings(tmp_path, contextWindow=ceiling),
        tree=tree,
        observer=sink,
        load_context_files=False,
    ) as session:
        result = session.submit("inspect notes")
        assert (result.preparation_failure is not None) == overflow
        assert len(provider.requests) == (2 if overflow else 3)
        assert len(prepared_requests) == 2
        assert provider.requests[1].system_prompt.startswith(
            "Summarize conversation context"
        )
        assert "Combined goals" in prepared_requests[1].system_prompt
        assert [
            m.content.value
            for m in prepared_requests[1].messages
            if isinstance(m, AgentUserMessage)
        ] == ["inspect notes"]
        kept = prepared_requests[1].messages[-1]
        completed = [
            event.result
            for event in sink.events
            if isinstance(event, ToolCallCompleted)
        ]
        assert len(completed) == 1 and kept is completed[0]
        assert kept.content == calibration.requests[1].messages[-1].content
        assert prepared_requests[1].messages[-2] == calibration.requests[1].messages[-2]
        assert kept.provider_correlation_id == "read-notes"

        assert all(_size(request) <= ceiling for request in provider.requests)
        assert (_size(prepared_requests[1]) > ceiling) == overflow
    assert len([e for e in tree.get_entries() if isinstance(e, CompactionEntry)]) == 1
    reopened = NativeSessionTree.open(tree.path).build_coding_context()
    assert reopened.messages == result.messages
    assert reopened.prior_summary == "Combined goals and verified results."


@pytest.mark.parametrize("trigger", ["manual", "auto"])
def test_auxiliary_exact_preflight_and_abort_precedence(tmp_path, trigger):
    effects, provider = _fixture(tmp_path, previous_summary="prior " * 100)
    before = _published(effects)
    context = effects.coding_state.compaction_snapshot()
    request = compaction_request(
        binding=context.binding,
        cwd=tmp_path,
        dropped_messages=context.messages[:3],
        prior_summary=context.summary_suffix.strip(),
        header_callback=None,
    )
    ceiling = _size(request) - 1
    effects.settings.set_value(
        "compaction", {"reserveTokens": 0, "contextWindow": ceiling}
    )
    outcome = effects.compact_context(trigger)
    assert (
        outcome.notice
        == "pipy: compact refused: estimated summary request exceeds the context window; context unchanged."
    )
    assert provider.requests == [] and _published(effects) == before
    abort = threading.Event()
    abort.set()
    outcome = replace(effects, abort_event=abort).compact_context(trigger)
    assert outcome.cancellation_reason is AgentCancellationReason.OPERATOR_ABORT
    assert abort.is_set() and provider.requests == [] and _published(effects) == before
    effects.settings.set_value("compaction.contextWindow", ceiling + 1)
    assert "compacted conversation" in effects.compact_context(trigger).notice
    assert len(provider.requests) == 1


@pytest.mark.parametrize("persist", [False, True])
def test_hooks_once_can_narrow_after_refused_summary_attempt(
    tmp_path, monkeypatch, persist
):
    tree = _tree(tmp_path, text="private earlier facts " * 1000, persist=persist)
    settings = _settings(tmp_path, contextWindow=2000)
    provider = _RecordingToolProvider()
    calls = []
    original = NativeAgentProviderRequestPolicy.prepare

    def prepare(self, policy_input):
        calls.append(True)
        snapshot = original(self, policy_input)
        # Existing request-only context transform retains the accepted anchor.
        return snapshot_provider_request(
            snapshot.request,
            system_prompt="small",
            messages=(policy_input.active_input.accepted_message,),
        )

    monkeypatch.setattr(NativeAgentProviderRequestPolicy, "prepare", prepare)
    diagnostics = []
    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        tools={},
        settings=settings,
        tree=tree,
        diagnostic_sink=diagnostics.append,
        load_context_files=False,
    ) as session:
        result = session.submit("continue")
        assert result.preparation_failure is None
        assert len(result.messages) == 8
    reason = (
        "retained history has no durable origin"
        if persist
        else "estimated summary request exceeds the context window"
    )
    assert reason in "".join(diagnostics)
    assert calls == [True] and len(provider.requests) == 1
    assert not any(isinstance(e, CompactionEntry) for e in tree.get_entries())
    assert len(provider.requests[0].messages) == 1


def test_hook_overflow_and_settings_change_apply_only_next_attempt(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path, enabled=False, contextWindow=5000)
    provider = _RecordingToolProvider()
    original = NativeAgentProviderRequestPolicy.prepare
    calls = []

    def prepare(self, policy_input):
        calls.append(True)
        snapshot = original(self, policy_input)
        if len(calls) == 1:
            settings.set_value("compaction.contextWindow", 100000)
            return snapshot_provider_request(
                snapshot.request, system_prompt="PRIVATE_TOOL_CONTEXT " * 1000
            )
        return snapshot

    monkeypatch.setattr(NativeAgentProviderRequestPolicy, "prepare", prepare)
    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        tools={},
        settings=settings,
        load_context_files=False,
    ) as session:
        refused = session.submit("first")
        assert refused.preparation_failure is not None
        assert "PRIVATE" not in refused.preparation_failure.message.value
        assert provider.requests == []
        assert session.submit("second").preparation_failure is None
    assert calls == [True, True] and len(provider.requests) == 1


def test_later_budget_refusal_keeps_real_tool_effect_and_usage_then_recovers(
    tmp_path, monkeypatch
):
    from test_native_preparation_refusal import _PriorToolProvider

    settings = _settings(tmp_path, enabled=False, contextWindow=10000)
    provider = _PriorToolProvider()
    original = NativeAgentProviderRequestPolicy.prepare
    calls = []

    def prepare(self, policy_input):
        calls.append(policy_input.baseline.user_prompt)
        snapshot = original(self, policy_input)
        if len(calls) == 2:
            return snapshot_provider_request(
                snapshot.request, system_prompt="large " * 10000
            )
        return snapshot

    monkeypatch.setattr(NativeAgentProviderRequestPolicy, "prepare", prepare)
    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        settings=settings,
        load_context_files=False,
    ) as session:
        refused = session.submit("first")
        assert refused.preparation_failure is not None
        assert refused.tool_invocation_count == 1 and refused.usage.input_tokens == 7
        assert (tmp_path / "effect.txt").read_text() == "kept"
        assert len(provider.requests) == 1
        assert (
            len([m for m in refused.messages if isinstance(m, AgentToolResultMessage)])
            == 1
        )
        recovered = session.submit("second")
        assert recovered.preparation_failure is None and len(provider.requests) == 2
        assert recovered.tool_invocation_count == 1
        assert recovered.messages[: len(refused.messages)] == refused.messages
    assert calls == ["first", "first", "second"]


@pytest.mark.parametrize("stale", [False, True])
def test_lookup_effect_revalidation_and_external_abort_before_refusal(
    tmp_path, monkeypatch, stale
):
    settings = _settings(tmp_path, contextWindow=1)
    provider = _RecordingToolProvider()
    effects_seen = []
    original = ProviderMutationEffects.declared_context_window
    sink = Sink()

    def lookup(self, binding):
        assert not self.coding_state.state_lock._is_owned()
        assert binding is self.coding_state.provider_binding
        effects_seen.append(self)
        assert session is not None
        session.cancel()
        if stale:
            self.coding_state.clear_history()
        return original(self, binding)

    monkeypatch.setattr(ProviderMutationEffects, "declared_context_window", lookup)
    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        tools={},
        settings=settings,
        observer=sink,
        load_context_files=False,
    ) as session:
        if stale:
            with pytest.raises(CodingContextChangedError):
                session.submit("first")
            assert effects_seen[0].ctl.coding_effects.terminal
        else:
            result = session.submit("first")
            assert result.preparation_failure is None
            assert any(isinstance(e, RunCancelled) for e in sink.events)
            monkeypatch.setattr(
                ProviderMutationEffects, "declared_context_window", original
            )
            settings.set_value("compaction.contextWindow", 100000)
            assert session.submit("second").preparation_failure is None
    assert len(provider.requests) == (0 if stale else 1)


def test_settings_and_context_share_one_capture_guard_and_lookup_uses_captured_labels(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path, enabled=False, contextWindow=10000)
    provider = _RecordingToolProvider()
    original_capture = SettingsManager.capture_compaction_budget_settings
    original_lookup = ProviderMutationEffects.declared_context_window
    captures = []
    lookups = []

    def capture(self):
        assert self._state_lock._is_owned()
        captures.append(self)
        return original_capture(self)

    def lookup(self, binding):
        assert captures[-1] is settings
        assert settings._state_lock is self.coding_state.state_lock
        assert not self.coding_state.state_lock._is_owned()
        lookups.append((binding.provider_name, binding.model_id))
        # This in-memory attempt keeps its old coherent policy; the new setting
        # applies only to the next attempt and has no run-long lock to block it.
        settings.set_value("compaction.contextWindow", 1)
        return original_lookup(self, binding)

    monkeypatch.setattr(SettingsManager, "capture_compaction_budget_settings", capture)
    monkeypatch.setattr(ProviderMutationEffects, "declared_context_window", lookup)
    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        tools={},
        settings=settings,
        load_context_files=False,
    ) as session:
        assert session.submit("first").preparation_failure is None
        assert session.submit("second").preparation_failure is not None
    assert lookups == [(provider.name, provider.model_id)] * 2
    assert len(provider.requests) == 1


def test_declared_lookup_does_not_use_live_provider_selection(tmp_path):
    effects, _provider = _fixture(tmp_path)
    assert effects.provider_state.current_selection().provider_name == "openai"
    captured = effects.coding_state.provider_binding
    assert effects.declared_context_window(captured) is None
    known = replace(captured, provider_name="openai", model_id="gpt-5.5")
    expected = effects.provider_state.model_runtime.resolve_spec(
        effects.provider_state.current_selection()
    )
    assert effects.declared_context_window(known) == expected.declared_context_window
    assert (
        effects.declared_context_window(replace(known, model_id="future-unknown"))
        is None
    )


@pytest.mark.parametrize(
    "declared,ceiling,refused",
    [(1, None, True), (1, 100000, True), (100000, 1, True), (100000, None, False)],
)
def test_live_declared_catalog_limit_and_explicit_ceiling_compose(
    tmp_path, monkeypatch, declared, ceiling, refused
):
    import io
    import json

    from test_native_coding_effects import _provider_mutation_fixture
    from test_native_semantic_compaction import _result

    from pipy_harness.native.coding.session import CodingSession

    (tmp_path / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "openai": {
                        "modelOverrides": {"gpt-5.5": {"contextWindow": declared}}
                    }
                }
            }
        )
    )
    _effects, provider_state, *_rest = _provider_mutation_fixture(tmp_path)
    provider = provider_state.current_provider()
    requests = []

    def complete(self, request, **kwargs):
        requests.append(request)
        return replace(
            _result(), provider_name=request.provider_name, model_id=request.model_id
        )

    monkeypatch.setattr(type(provider), "complete", complete)
    settings = _settings(
        tmp_path,
        enabled=False,
        **({"contextWindow": ceiling} if ceiling is not None else {}),
    )
    result = CodingSession(
        provider=provider,
        provider_state=provider_state,
        tool_registry={},
        settings_manager=settings,
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("continue\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    assert (result.preparation_failure_type is not None) == refused
    assert len(requests) == (0 if refused else 1)


@pytest.mark.parametrize("component", ["image", "tool"])
def test_large_image_or_tool_definition_is_counted_before_provider(tmp_path, component):
    from test_native_agent_request_policy_integration import _NamedTool
    from test_native_image_attachment import _PNG

    provider = _RecordingToolProvider()
    tools = {}
    prompt = "continue"
    if component == "image":
        (tmp_path / "pixel.png").write_bytes(_PNG)
        prompt = "inspect @image:pixel.png"
    else:

        class LargeTool(_NamedTool):
            @property
            def definition(self):
                return replace(
                    super().definition, description="tool documentation " * 200
                )

        tools["large"] = LargeTool("large", [])
    calibration = _RecordingToolProvider()
    with create_product_session(
        workspace=tmp_path,
        provider=calibration,
        tools=tools,
        settings=_settings(tmp_path, enabled=False),
        load_context_files=False,
    ) as session:
        assert session.submit(prompt).preparation_failure is None
    full = calibration.requests[0]
    baseline = replace(
        full, **{("attachments" if component == "image" else "available_tools"): ()}
    )
    ceiling = _size(baseline)
    assert _size(full) > ceiling
    settings = _settings(tmp_path, enabled=False, contextWindow=ceiling)
    with create_product_session(
        workspace=tmp_path,
        provider=provider,
        tools=tools,
        settings=settings,
        load_context_files=False,
    ) as session:
        result = session.submit(prompt)
        assert result.preparation_failure is not None
        assert result.tool_invocation_count == 0 and result.usage.input_tokens == 0
    assert provider.requests == []


def test_request_only_overlay_counts_toward_admission_without_entering_history(
    tmp_path,
):
    import io

    from test_native_agent_active_input_integration import (
        _TRANSIENTS,
        _activation_batch,
    )

    from pipy_harness.native.coding.session import CodingSession

    calibration = _RecordingToolProvider()
    CodingSession(
        provider=calibration, tool_registry={}, settings_manager=_settings(tmp_path)
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("same\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    limit = _size(calibration.requests[0])
    provider = _RecordingToolProvider()
    session = CodingSession(
        provider=provider,
        tool_registry={},
        initial_extension_batch=_activation_batch(),
        settings_manager=_settings(tmp_path, enabled=False, contextWindow=limit),
    )
    diagnostics = io.StringIO()
    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("same\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=diagnostics,
    )
    assert result.preparation_failure_type == "request_preparation_refused"
    assert provider.requests == []
    assert [m.content.value for m in session._coding_state.messages] == ["same"]
    assert all(marker not in diagnostics.getvalue() for marker in _TRANSIENTS)


def test_live_budget_refusal_archive_contains_no_private_request_body(tmp_path):
    import io

    from pipy_harness.adapters.native import CodingSessionAdapter
    from pipy_harness.models import RunRequest
    from pipy_harness.runner import HarnessRunner

    private = "PRIVATE_BUDGET_REQUEST_BODY"
    provider = _RecordingToolProvider()
    adapter = CodingSessionAdapter(
        provider=provider,
        settings_manager=_settings(tmp_path, enabled=False, contextWindow=1000),
        input_stream=io.StringIO(private * 1000 + "\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    result = HarnessRunner(adapter=adapter).run(
        RunRequest(
            agent="pipy-native",
            slug="budget-refusal",
            command=[],
            cwd=tmp_path,
            root=tmp_path / "archive",
            goal="bounded admission fixture",
        )
    )
    assert provider.requests == []
    assert result.metadata["preparation_failure_type"] == "request_preparation_refused"
    assert result.record.markdown_path is not None
    assert all(
        private not in text
        for text in (
            repr(result),
            result.record.jsonl_path.read_text(),
            result.record.markdown_path.read_text(),
        )
    )
