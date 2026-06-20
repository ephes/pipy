"""Tests for the tool-loop product REPL adapter.

These tests pin that `PipyNativeToolReplAdapter` is rejected when the selected
provider has `supports_tool_calls=False`, that `--tool-budget` is honored, and
that the adapter wires `NativeToolReplSession` against the production tool
registry while keeping its archive metadata metadata-only.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from pathlib import Path

import pytest

from pipy_harness.adapters import PipyNativeToolReplAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import RunRequest
from pipy_harness.native import (
    FakeNativeProvider,
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


def test_repl_explicit_fake_provider_resolves_to_tool_capable_selection(
    tmp_path: Path, monkeypatch,
):
    """``pipy repl --native-provider fake`` must yield a tool-capable provider.

    The product REPL always builds the tool-loop session. Whenever the resolved
    provider is ``fake`` — from an explicit ``--native-provider fake`` (with or
    without ``fake-native-bootstrap``), the no-provider fallback, or a stored
    default — the REPL must normalize to the tool-capable ``fake-tools`` model.
    The test does NOT inject a tool-capable provider; it proves the resolver
    upgrades it.
    """

    from pipy_harness.cli import _tool_repl_adapter_for

    monkeypatch.setenv("PIPY_NATIVE_DEFAULTS_PATH", str(tmp_path / "defaults.json"))

    for native_model in (None, "fake-native-bootstrap"):
        adapter = _tool_repl_adapter_for(
            "fake",
            native_model,
            cwd=tmp_path,
            tool_budget=5,
        )
        selection = adapter._current_selection()
        assert selection.provider_name == "fake"
        provider = adapter._current_provider()
        assert provider.supports_tool_calls is True, (
            f"native_model={native_model!r} resolved to a non-tool-capable provider"
        )


def test_pipy_native_tool_repl_adapter_requires_tool_capable_provider(
    tmp_path: Path,
):
    adapter = PipyNativeToolReplAdapter(
        provider=FakeNativeProvider(supports_tool_calls=False),
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
