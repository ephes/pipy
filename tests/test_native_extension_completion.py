"""Slice B: a bounded one-shot LLM completion for extension command handlers.

A `/answer`-style extension extracts structured data (questions) from the last
assistant message by asking a model. `ctx.complete(system_prompt, user_text)`
runs exactly one provider completion (no tools) and returns its text. When no
completion backend is wired (deterministic/non-interactive context), it raises
`ExtensionCapabilityError` so a handler degrades predictably.
"""

from __future__ import annotations

from pipy_harness.native.extension_runtime import (
    ExtensionCapabilityError,
    RegisteredCommand,
    dispatch_extension_command,
)


def _command_map(handler):
    return {
        "probe": RegisteredCommand(
            name="probe", description="probe", handler=handler, extension="t"
        )
    }


def test_complete_delegates_to_the_wired_backend() -> None:
    calls: list[tuple[str, str]] = []
    seen: dict[str, object] = {}

    def backend(system_prompt: str, user_text: str) -> str:
        calls.append((system_prompt, user_text))
        return '{"questions": []}'

    def handler(ctx, args):
        seen["out"] = ctx.complete("SYS", "extract from this")

    dispatch = dispatch_extension_command(
        "/probe",
        _command_map(handler),
        cwd="/tmp",
        has_ui=True,
        complete_fn=backend,
    )
    assert dispatch is not None and dispatch.ran
    assert seen["out"] == '{"questions": []}'
    assert calls == [("SYS", "extract from this")]


def test_complete_raises_when_unwired() -> None:
    captured: dict[str, object] = {}

    def handler(ctx, args):
        try:
            ctx.complete("SYS", "x")
        except ExtensionCapabilityError as exc:
            captured["err"] = str(exc)

    dispatch = dispatch_extension_command(
        "/probe", _command_map(handler), cwd="/tmp", has_ui=True
    )
    assert dispatch is not None and dispatch.ran
    assert "err" in captured
