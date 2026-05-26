"""Tests for the dynamic provider/model swap helper."""

from __future__ import annotations

from pipy_harness.native.dynamic_provider import (
    SwapOutcome,
    swap_model,
    swap_provider,
)
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.repl_state import (
    DEFAULT_NATIVE_MODELS,
    NativeModelSelection,
    NativeReplProviderState,
)


class _NoOpAuthManager:
    """Stand-in for OpenAICodexAuthManager that never logs in."""

    def login_interactive(self, **_kwargs: object) -> None:  # pragma: no cover - unused
        raise AssertionError("login should not run in swap tests")

    def logout(self) -> bool:  # pragma: no cover - unused
        return False


def _factory(selection: NativeModelSelection) -> ProviderPort:
    return FakeNativeProvider(model_id=selection.model_id)


def _state(
    *,
    selection: NativeModelSelection | None = None,
    env: dict[str, str] | None = None,
) -> NativeReplProviderState:
    initial = selection or NativeModelSelection(
        "fake", DEFAULT_NATIVE_MODELS["fake"]
    )
    return NativeReplProviderState(
        selection=initial,
        provider_factory=_factory,
        defaults_store=None,
        auth_manager_factory=lambda: _NoOpAuthManager(),  # type: ignore[arg-type,return-value]
        env=env or {},
        persist_defaults=False,
    )


def test_swap_provider_succeeds_when_provider_available() -> None:
    state = _state(env={"ANTHROPIC_API_KEY": "k"})

    outcome = swap_provider(state, "anthropic")

    assert outcome.success is True
    assert outcome.previous_selection.provider_name == "fake"
    assert outcome.selection.provider_name == "anthropic"
    assert outcome.selection.model_id == DEFAULT_NATIVE_MODELS["anthropic"]


def test_swap_provider_with_explicit_model() -> None:
    state = _state(env={"OPENAI_API_KEY": "k"})

    outcome = swap_provider(state, "openai", model_id="gpt-test-1")

    assert outcome.success is True
    assert outcome.selection.provider_name == "openai"
    assert outcome.selection.model_id == "gpt-test-1"


def test_swap_provider_rejects_unsupported_name() -> None:
    state = _state()

    outcome = swap_provider(state, "not-a-provider")

    assert outcome.success is False
    assert "unsupported" in outcome.message
    assert outcome.selection == outcome.previous_selection


def test_swap_provider_rejects_empty_name() -> None:
    state = _state()

    outcome = swap_provider(state, "   ")

    assert outcome.success is False
    assert "non-empty" in outcome.message
    assert outcome.selection == outcome.previous_selection


def test_swap_provider_fails_when_provider_unavailable() -> None:
    state = _state(env={})  # no anthropic key

    outcome = swap_provider(state, "anthropic")

    assert outcome.success is False
    assert "anthropic" in outcome.message
    assert outcome.selection == outcome.previous_selection


def test_swap_provider_keeps_state_unchanged_on_failure() -> None:
    starting = NativeModelSelection("fake", "fake-native-bootstrap")
    state = _state(selection=starting)

    swap_provider(state, "anthropic")  # no env

    assert state.current_selection() == starting


def test_swap_model_changes_model_for_current_provider() -> None:
    state = _state(
        selection=NativeModelSelection("fake", "fake-native-bootstrap"),
    )

    outcome = swap_model(state, "fake-other-build")

    assert outcome.success is True
    assert outcome.selection.provider_name == "fake"
    assert outcome.selection.model_id == "fake-other-build"


def test_swap_model_rejects_empty_id() -> None:
    state = _state()

    outcome = swap_model(state, "  ")

    assert outcome.success is False
    assert "non-empty" in outcome.message


def test_swap_outcome_carries_both_selections() -> None:
    state = _state(env={"MISTRAL_API_KEY": "k"})
    initial = state.current_selection()

    outcome = swap_provider(state, "mistral")

    assert isinstance(outcome, SwapOutcome)
    assert outcome.previous_selection == initial
    assert outcome.selection.provider_name == "mistral"
