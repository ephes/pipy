"""Characterization contracts for the native ``/settings`` command."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import (
    NativeToolReplResult,
    NativeToolReplSession,
    ProviderRequest,
    ProviderResult,
)
from pipy_harness.native.agent import ProductContent
from pipy_harness.native.coding.commands import CodingCommandOutcome
from pipy_harness.native.coding.input_queue import CodingInputQueue
from pipy_harness.native.prompt_history import PromptHistoryStore
from pipy_harness.native.repl_state import StaticNativeReplProviderState
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager


class _RecordingProvider:
    supports_tool_calls = True

    def __init__(
        self,
        *,
        name: str = "fake",
        model_id: str = "fake-native-bootstrap",
    ) -> None:
        self.name = name
        self.model_id = model_id
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="unexpected provider turn",
            tool_calls=(),
        )


class _FailingSettingsStream(io.StringIO):
    def write(self, value: str) -> int:
        if "FIRST SETTINGS LINE" in value:
            raise OSError("unexpected print failure")
        return super().write(value)


def _workspace(tmp_path: Path) -> Path:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    return cwd


def _settings(tmp_path: Path, cwd: Path) -> SettingsManager:
    return SettingsManager(
        global_path=tmp_path / "config" / "settings.json",
        project_path=cwd / ".pipy" / "settings.json",
        env={},
        overrides={"quietStartup": True},
        project_trusted=True,
    )


def _run_captured(
    session: NativeToolReplSession,
    cwd: Path,
    inputs: str,
    *,
    error_stream: TextIO | None = None,
) -> tuple[NativeToolReplResult, str]:
    errors = error_stream or io.StringIO()
    result = session.run(
        workspace_root=cwd,
        input_stream=io.StringIO(inputs),
        output_stream=io.StringIO(),
        error_stream=errors,
    )
    rendered = errors.getvalue() if isinstance(errors, io.StringIO) else ""
    return result, rendered


def test_captured_settings_projects_current_provider_in_sanitized_order(
    tmp_path: Path,
) -> None:
    cwd = _workspace(tmp_path)
    provider = _RecordingProvider(name="api_key=private", model_id="current-model")
    state = StaticNativeReplProviderState(provider)
    session = NativeToolReplSession(
        provider=provider,
        provider_state=state,
        settings_manager=_settings(tmp_path, cwd),
        tool_registry={},
    )

    result, rendered = _run_captured(session, cwd, "/settings\n/exit\n")

    expected_lines = (
        "pipy native REPL settings:",
        "  active: [REDACTED]/current-model",
        "  registered providers:",
        "    [REDACTED]/current-model [available]",
        "  read-only view; /model, /login, and /logout are not available ",
    )
    positions = [rendered.index(line) for line in expected_lines]
    assert positions == sorted(positions)
    overlay_start = rendered.index(expected_lines[0])
    overlay_end = rendered.index("─", overlay_start)
    assert "api_key=private" not in rendered[overlay_start:overlay_end]
    assert session.provider_port is provider
    assert provider.requests == []
    assert result.user_turn_count == 0
    assert result.tool_invocation_count == 0


def test_composition_trims_for_classification_but_preserves_user_bubble(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.coding.session_controller as controller_module
    import pipy_harness.native.tool_loop_session as loop_module

    cwd = _workspace(tmp_path)
    classified: list[ProductContent] = []
    bubbles: list[str] = []
    footer_usage: list[bool] = []
    overlays: list[object] = []
    # Built-in classification now lives in the headless controller.
    original_classifier = controller_module.classify_coding_command

    def classify(content: ProductContent) -> CodingCommandOutcome:
        classified.append(content)
        return original_classifier(content)

    def render_user_message(_renderer: object, text: str) -> None:
        bubbles.append(text)

    def overlay_lines(
        _settings_manager: object = None,
        *,
        provider: object,
        provider_state: object = None,
    ) -> list[str]:
        overlays.append(provider)
        return ["settings overlay"]

    def footer(
        _session: NativeToolReplSession,
        _error_stream: TextIO,
        **kwargs: object,
    ) -> None:
        footer_usage.append(kwargs.get("usage_snapshot") is not None)

    monkeypatch.setattr(controller_module, "classify_coding_command", classify)
    monkeypatch.setattr(
        loop_module._ToolLoopRenderer, "render_user_message", render_user_message
    )
    # `_ProviderConfigurationCommandEffects` still lives at the composition
    # root, so the overlay builder binds in `loop_module`'s namespace; patching
    # the definition site would leave the real builder running.
    monkeypatch.setattr(loop_module, "tool_loop_settings_overlay_lines", overlay_lines)
    monkeypatch.setattr(NativeToolReplSession, "_print_footer", footer)
    provider = _RecordingProvider()

    _run_captured(
        NativeToolReplSession(
            provider=provider,
            settings_manager=_settings(tmp_path, cwd),
        ),
        cwd,
        " \t/settings \t\n",
    )

    assert classified == [ProductContent("/settings")]
    assert bubbles == [" \t/settings \t"]
    assert overlays == [provider]
    assert footer_usage == [True, False]
    assert provider.requests == []


def test_settings_effect_is_owned_by_the_typed_interpreter() -> None:
    source = (
        Path(__file__).parents[1]
        / "src"
        / "pipy_harness"
        / "native"
        / "tool_loop_session.py"
    ).read_text(encoding="utf-8")

    assert 'if command_text == "/settings":' not in source


@pytest.mark.parametrize("failure_stage", ["overlay", "print"])
def test_unexpected_settings_projection_failures_cut_off_footer_and_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    cwd = _workspace(tmp_path)
    footer_calls: list[str] = []
    provider = _RecordingProvider()

    def footer(
        _session: NativeToolReplSession,
        _error_stream: TextIO,
        **_kwargs: object,
    ) -> None:
        footer_calls.append("footer")

    def overlay_lines(
        _settings_manager: object = None,
        *,
        provider: object,
        provider_state: object = None,
    ) -> list[str]:
        del provider
        if failure_stage == "overlay":
            raise RuntimeError("unexpected overlay failure")
        return ["FIRST SETTINGS LINE", "PRIVATE LATER LINE"]

    monkeypatch.setattr(NativeToolReplSession, "_print_footer", footer)
    # `_ProviderConfigurationCommandEffects` still lives at the composition
    # root, so the overlay builder binds in `loop_module`'s namespace; patching
    # the definition site would leave the real builder running.
    monkeypatch.setattr(loop_module, "tool_loop_settings_overlay_lines", overlay_lines)
    session = NativeToolReplSession(
        provider=provider,
        settings_manager=_settings(tmp_path, cwd),
    )
    error_stream = _FailingSettingsStream() if failure_stage == "print" else None

    expected = RuntimeError if failure_stage == "overlay" else OSError
    with pytest.raises(expected, match=f"unexpected {failure_stage} failure"):
        _run_captured(session, cwd, "/settings\n", error_stream=error_stream)

    assert footer_calls == ["footer"]
    assert provider.requests == []


def test_settings_has_no_input_session_history_or_provider_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.repl.extension_operations as ops_module

    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")
    assert tree.path is not None
    tree_before = tree.path.read_bytes()
    history = PromptHistoryStore(tmp_path / "prompt-history.json")
    history.set_enabled(True)
    history_before = history.path.read_bytes()

    def reject_input_hook(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("/settings must not dispatch extension input hooks")

    def reject_input_clear(_queue: CodingInputQueue) -> None:
        raise AssertionError("/settings must not clear extension input")

    monkeypatch.setattr(ops_module, "dispatch_input_hooks", reject_input_hook)
    monkeypatch.setattr(CodingInputQueue, "clear_extension_inputs", reject_input_clear)
    provider = _RecordingProvider()
    result, _rendered = _run_captured(
        NativeToolReplSession(
            provider=provider,
            native_session=tree,
            prompt_history_store=history,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        "/settings\n/exit\n",
    )

    assert provider.requests == []
    assert result.user_turn_count == 0
    assert result.tool_invocation_count == 0
    assert tree.get_entries() == []
    assert tree.path.read_bytes() == tree_before
    assert history.entries() == []
    assert history.path.read_bytes() == history_before
