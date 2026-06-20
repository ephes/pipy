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


class _RecordingSession:
    """Spy that captures the constructor kwargs and the run() system prompt."""

    last_init_kwargs: dict[str, object] | None = None
    last_run_kwargs: dict[str, object] | None = None

    def __init__(self, **kwargs: object) -> None:
        type(self).last_init_kwargs = dict(kwargs)

    def run(self, **kwargs: object):
        from datetime import UTC, datetime

        from pipy_harness.models import HarnessStatus
        from pipy_harness.native.tool_loop_session import NativeToolReplResult

        type(self).last_run_kwargs = dict(kwargs)
        now = datetime.now(UTC)
        return NativeToolReplResult(
            status=HarnessStatus.SUCCEEDED,
            exit_code=0,
            started_at=now,
            ended_at=now,
            provider_name=str(kwargs.get("provider_name") or "fake"),
            model_id=str(kwargs.get("model_id") or "fake"),
        )


def _write_skill(skills_dir: Path, *, name: str, description: str, body: str) -> Path:
    skills_dir.mkdir(parents=True, exist_ok=True)
    path = skills_dir / f"{name}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )
    return path


def _run_adapter_with_spy(adapter, prepared, monkeypatch):
    import pipy_harness.adapters.native as native_mod

    monkeypatch.setattr(native_mod, "NativeToolReplSession", _RecordingSession)
    _RecordingSession.last_init_kwargs = None
    _RecordingSession.last_run_kwargs = None
    adapter.run(prepared, event_sink=_NullEventSink(), capture_policy=CapturePolicy())
    assert _RecordingSession.last_run_kwargs is not None
    assert _RecordingSession.last_init_kwargs is not None
    return _RecordingSession.last_init_kwargs, _RecordingSession.last_run_kwargs


def _prepared_for(adapter, tmp_path: Path):
    return adapter.prepare(
        RunRequest(
            agent="pipy-native",
            slug="test",
            command=[],
            cwd=tmp_path,
            goal="t",
            capture_policy=CapturePolicy(),
        )
    )


def test_system_prompt_includes_skill_block_when_read_available(
    tmp_path: Path, monkeypatch
):
    from pipy_harness.native.tools.read import ReadTool

    skills_dir = tmp_path / ".pipy" / "skills"
    skill_path = _write_skill(
        skills_dir, name="lint", description="Lint the code", body="lint body"
    )
    adapter = PipyNativeToolReplAdapter(
        provider=FakeNativeProvider(supports_tool_calls=True),
        tool_registry={"read": ReadTool()},
        input_stream=io.StringIO(""),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    prepared = _prepared_for(adapter, tmp_path)
    _, run_kwargs = _run_adapter_with_spy(adapter, prepared, monkeypatch)

    system_prompt = run_kwargs["system_prompt"]
    assert "<available_skills>" in system_prompt
    assert "<name>lint</name>" in system_prompt
    assert f"<location>{skill_path.resolve()}</location>" in system_prompt


def test_system_prompt_omits_skill_block_when_read_excluded(
    tmp_path: Path, monkeypatch
):
    skills_dir = tmp_path / ".pipy" / "skills"
    _write_skill(
        skills_dir, name="lint", description="Lint the code", body="lint body"
    )
    adapter = PipyNativeToolReplAdapter(
        provider=FakeNativeProvider(supports_tool_calls=True),
        tool_registry={},
        input_stream=io.StringIO(""),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    prepared = _prepared_for(adapter, tmp_path)
    _, run_kwargs = _run_adapter_with_spy(adapter, prepared, monkeypatch)

    assert "<available_skills>" not in run_kwargs["system_prompt"]


def test_skill_dirs_added_to_reference_roots(tmp_path: Path, monkeypatch):
    from pipy_harness.native.tools.read import ReadTool

    skills_dir = tmp_path / ".pipy" / "skills"
    _write_skill(
        skills_dir, name="lint", description="Lint the code", body="lint body"
    )
    adapter = PipyNativeToolReplAdapter(
        provider=FakeNativeProvider(supports_tool_calls=True),
        tool_registry={"read": ReadTool()},
        input_stream=io.StringIO(""),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    prepared = _prepared_for(adapter, tmp_path)
    init_kwargs, _ = _run_adapter_with_spy(adapter, prepared, monkeypatch)

    reference_roots = init_kwargs["reference_roots"]
    assert skills_dir.resolve() in reference_roots


def test_model_can_read_global_skill_body_via_reference_roots(
    tmp_path: Path, monkeypatch
):
    """A1-A4 integration: the model loads an outside-cwd skill body via read.

    The skill lives in a global skill dir outside the workspace. Its parent
    directory enters the session reference roots, so the read tool can open the
    skill body by absolute path. A non-skill path outside cwd is still refused.
    The archive-safe skill metadata stays path_label/sha256/byte_length/truncated.
    """

    from pipy_harness.native.skills import safe_skill_metadata
    from pipy_harness.native.tools import ToolContext, ToolRequest
    from pipy_harness.native.tools.base import ToolArgumentError
    from pipy_harness.native.tools.read import ReadTool

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Global skill dir OUTSIDE the workspace (resolved via PIPY_CONFIG_HOME).
    config_home = tmp_path / "global_cfg"
    global_skills = config_home / "skills"
    skill_path = _write_skill(
        global_skills,
        name="deploy",
        description="Deploy the service",
        body="GLOBAL SKILL BODY: run the deploy steps",
    )
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config_home))

    adapter = PipyNativeToolReplAdapter(
        provider=FakeNativeProvider(supports_tool_calls=True),
        tool_registry={"read": ReadTool()},
        input_stream=io.StringIO(""),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    skills = adapter._discover_skill_files(workspace)
    assert any(s.name == "deploy" for s in skills)
    reference_roots = adapter._reference_roots_with_skill_dirs(skills)
    assert global_skills.resolve() in reference_roots

    tool = ReadTool()
    context = ToolContext(
        workspace_root=workspace.resolve(),
        reference_roots=reference_roots,
    )
    allowed = tool.invoke(
        ToolRequest(
            tool_request_id="pipy-tool-skill-read",
            tool_name="read",
            arguments={"path": str(skill_path.resolve())},
        ),
        context,
    )
    assert allowed.is_error is False
    assert "GLOBAL SKILL BODY" in allowed.output_text

    # A non-skill path outside cwd is still refused (no reference root covers it).
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "secret.txt").write_text("nope", encoding="utf-8")
    with pytest.raises(ToolArgumentError, match="outside the workspace"):
        tool.invoke(
            ToolRequest(
                tool_request_id="pipy-tool-outside-read",
                tool_name="read",
                arguments={"path": str((other / "secret.txt").resolve())},
            ),
            context,
        )

    # Archive boundary unchanged: only the four safe keys; no body/abs path.
    safe = safe_skill_metadata(skills)
    for entry in safe:
        assert set(entry.keys()) == {
            "path_label",
            "sha256",
            "byte_length",
            "truncated",
        }
    flat = str(safe)
    assert "GLOBAL SKILL BODY" not in flat
    assert str(skill_path.resolve()) not in flat


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
