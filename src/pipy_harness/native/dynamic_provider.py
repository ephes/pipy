"""Mid-session provider/model swapping for the native REPL.

This module exposes a small wrapper around the existing
`NativeReplProviderState.select_model` flow so a future `/provider` slash
command (and the existing `/model` command) can swap the active
`ProviderPort` in the middle of a REPL session without restarting the
process. The swap reuses the same `NativeModelSelection` storage, the
same `NativeProviderFactory`, and the same persistence in
`NativeDefaultsStore` — there is no new identity layer.

Privacy and architectural invariants:

- No new runtime dependencies. Stdlib only.
- The wrapper does not call any provider, run tools, or read files; it
  only mutates the REPL's selection state and persists the safe
  non-secret default. Provider construction happens lazily through the
  injected `provider_factory`.
- Auth material is never touched; the underlying `select_model` path
  still routes credentials through environment lookups and the existing
  `OpenAICodexAuthManager`.
- The previously-active provider instance is dropped on swap. Callers
  that hold the prior instance must rebind via
  `state.current_provider()`.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipy_harness.native.repl_state import (
    DEFAULT_NATIVE_MODELS,
    SUPPORTED_NATIVE_PROVIDERS,
    NativeModelSelection,
    NativeReplProviderState,
)


@dataclass(frozen=True, slots=True)
class SwapOutcome:
    """Result of a swap attempt.

    `selection` is the active selection after the swap — equal to the
    previous selection when `success` is `False`.
    """

    success: bool
    message: str
    previous_selection: NativeModelSelection
    selection: NativeModelSelection


def swap_provider(
    state: NativeReplProviderState,
    provider_name: str,
    *,
    model_id: str | None = None,
) -> SwapOutcome:
    """Switch the REPL's active provider mid-session.

    `provider_name` must be one of `SUPPORTED_NATIVE_PROVIDERS`.
    When `model_id` is omitted, the registered `DEFAULT_NATIVE_MODELS`
    entry for the new provider is used. The swap delegates to
    `state.select_model` so the existing availability checks
    (env-var presence, OAuth credentials) and persistence semantics
    apply unchanged.
    """

    previous = state.current_selection()
    requested = (provider_name or "").strip()
    if not requested:
        return SwapOutcome(
            success=False,
            message="pipy: swap_provider requires a non-empty provider name.",
            previous_selection=previous,
            selection=previous,
        )
    if requested not in SUPPORTED_NATIVE_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_NATIVE_PROVIDERS))
        return SwapOutcome(
            success=False,
            message=(
                f"pipy: unsupported native provider {requested!r}. "
                f"Supported: {supported}."
            ),
            previous_selection=previous,
            selection=previous,
        )
    resolved_model = (model_id or "").strip() or DEFAULT_NATIVE_MODELS.get(
        requested
    )
    if not resolved_model:
        return SwapOutcome(
            success=False,
            message=(
                "pipy: no default model registered for "
                f"{requested!r}; pass --native-model or use /model."
            ),
            previous_selection=previous,
            selection=previous,
        )
    success, message = state.select_model(f"{requested}/{resolved_model}")
    new_selection = state.current_selection() if success else previous
    return SwapOutcome(
        success=success,
        message=message,
        previous_selection=previous,
        selection=new_selection,
    )


def swap_model(
    state: NativeReplProviderState, model_id: str
) -> SwapOutcome:
    """Switch the model for the currently selected provider.

    Equivalent to `/model <current-provider>/<model_id>` but with the
    swap-outcome wrapper.
    """

    previous = state.current_selection()
    candidate = (model_id or "").strip()
    if not candidate:
        return SwapOutcome(
            success=False,
            message="pipy: swap_model requires a non-empty model id.",
            previous_selection=previous,
            selection=previous,
        )
    reference = f"{previous.provider_name}/{candidate}"
    success, message = state.select_model(reference)
    new_selection = state.current_selection() if success else previous
    return SwapOutcome(
        success=success,
        message=message,
        previous_selection=previous,
        selection=new_selection,
    )


__all__ = ["SwapOutcome", "swap_provider", "swap_model"]
