"""Slice 5 tests: the `--repl-mode tool-loop` adapter and CLI flag.

These tests pin that `--repl-mode` defaults to `no-tool`, that
`--repl-mode tool-loop` is rejected when the selected provider has
`supports_tool_calls=False`, that `--tool-budget` is honored, and that the
adapter wires `NativeToolReplSession` against the production tool registry.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from pathlib import Path

import pytest

from pipy_harness.adapters import (
    PipyNativeReplAdapter,
    PipyNativeToolReplAdapter,
)
from pipy_harness.capture import CapturePolicy
from pipy_harness.cli import build_parser
from pipy_harness.models import RunRequest
from pipy_harness.native import (
    FakeNativeProvider,
    OpenAIResponsesProvider,
    ProviderToolCall,
)


class _NullEventSink:
    def emit(
        self,
        event_type: str,
        *,
        summary: str,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        return None


def test_repl_mode_defaults_to_auto():
    parser = build_parser()
    args = parser.parse_args(["repl"])

    assert args.repl_mode == "auto"
    assert args.tool_budget == 10


def test_repl_mode_tool_loop_flag_round_trips():
    parser = build_parser()
    args = parser.parse_args([
        "repl",
        "--repl-mode",
        "tool-loop",
        "--tool-budget",
        "5",
    ])

    assert args.repl_mode == "tool-loop"
    assert args.tool_budget == 5


def test_pipy_native_tool_repl_adapter_requires_tool_capable_provider(
    tmp_path: Path,
):
    adapter = PipyNativeToolReplAdapter(
        provider=OpenAIResponsesProvider(model_id="gpt-test"),
        input_stream=io.StringIO(""),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    prepared = adapter.prepare(
        RunRequest(
            agent="pipy-native",
            slug="test",
            command=[],
            cwd=tmp_path,
            goal="t",
            capture_policy=CapturePolicy(),
        )
    )

    with pytest.raises(ValueError, match="supports_tool_calls"):
        adapter.run(prepared, event_sink=_NullEventSink(), capture_policy=CapturePolicy())


def test_pipy_native_tool_repl_adapter_runs_with_fake_provider(tmp_path: Path):
    call = ProviderToolCall(
        provider_correlation_id="call_test",
        tool_name="read",
        arguments_json='{"path": "notes.txt"}',
    )
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_tool_calls=((call,), ()),
        final_text="done",
    )
    output_stream = io.StringIO()
    adapter = PipyNativeToolReplAdapter(
        provider=provider,
        input_stream=io.StringIO("read notes\n"),
        output_stream=output_stream,
        error_stream=io.StringIO(),
        tool_budget=3,
    )
    prepared = adapter.prepare(
        RunRequest(
            agent="pipy-native",
            slug="test",
            command=[],
            cwd=tmp_path,
            goal="t",
            capture_policy=CapturePolicy(),
        )
    )

    result = adapter.run(prepared, event_sink=_NullEventSink(), capture_policy=CapturePolicy())

    assert result.exit_code == 0
    metadata = result.metadata or {}
    assert metadata["adapter"] == "pipy-native"
    assert metadata["repl_mode"] == "tool-loop"
    assert metadata["tool_budget"] == 3
    assert metadata["tool_invocation_count"] == 1
    assert metadata["malformed_argument_count"] == 0


def test_pipy_native_tool_repl_adapter_metadata_is_metadata_only(tmp_path: Path):
    provider = FakeNativeProvider(supports_tool_calls=True)
    adapter = PipyNativeToolReplAdapter(
        provider=provider,
        input_stream=io.StringIO(""),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    prepared = adapter.prepare(
        RunRequest(
            agent="pipy-native",
            slug="test",
            command=[],
            cwd=tmp_path,
            goal="t",
            capture_policy=CapturePolicy(),
        )
    )

    result = adapter.run(prepared, event_sink=_NullEventSink(), capture_policy=CapturePolicy())

    metadata = result.metadata or {}
    forbidden = {
        "arguments",
        "diff",
        "diffs",
        "file_content",
        "file_contents",
        "model_output",
        "patch",
        "payload",
        "prompt",
        "provider_response",
        "stderr",
        "stdout",
        "tool_payload",
    }
    assert forbidden.isdisjoint(metadata.keys())


def test_pipy_native_repl_adapter_no_tool_mode_unaffected_by_new_flag():
    """The existing no-tool REPL adapter does not gain a tool registry."""

    assert not hasattr(PipyNativeReplAdapter, "tool_registry")


# --------------------- slice 12: --repl-mode auto resolver -----------------


def test_resolve_repl_mode_auto_falls_back_to_no_tool_for_real_providers():
    from pipy_harness.cli import _resolve_repl_mode

    # Real providers all carry supports_tool_calls=False at slice 12.
    resolved = _resolve_repl_mode(
        "auto", native_provider="openai", native_model="gpt-test"
    )

    assert resolved == "no-tool"


def test_resolve_repl_mode_auto_routes_to_tool_loop_for_tool_capable_provider(
    monkeypatch,
):
    from pipy_harness import cli

    class _Stub:
        supports_tool_calls = True

    monkeypatch.setattr(cli, "_native_provider_for_selection", lambda _s: _Stub())

    resolved = cli._resolve_repl_mode(
        "auto", native_provider="fake", native_model="fake-native-bootstrap"
    )

    assert resolved == "tool-loop"


def test_resolve_repl_mode_explicit_no_tool_overrides_auto(monkeypatch):
    from pipy_harness import cli

    class _Stub:
        supports_tool_calls = True

    monkeypatch.setattr(cli, "_native_provider_for_selection", lambda _s: _Stub())

    resolved = cli._resolve_repl_mode(
        "no-tool", native_provider="fake", native_model="fake-native-bootstrap"
    )

    assert resolved == "no-tool"


def test_resolve_repl_mode_explicit_tool_loop_overrides_auto():
    from pipy_harness.cli import _resolve_repl_mode

    resolved = _resolve_repl_mode(
        "tool-loop", native_provider="openai", native_model="gpt-test"
    )

    assert resolved == "tool-loop"
