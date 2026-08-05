"""Characterization contracts for the native ``/trust`` command."""

from __future__ import annotations

import io
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest, ProviderResult
from pipy_harness.native.coding.input_queue import CodingInputQueue
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.project_trust import (
    ProjectTrustEntry,
    ProjectTrustError,
    ProjectTrustOption,
    get_project_trust_options,
)
from pipy_harness.native.prompt_history import PromptHistoryStore
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tui import ToolLoopTerminalUi


class _RecordingProvider:
    name = "fake"
    supports_tool_calls = True
    model_id = "fake-native-bootstrap"

    def __init__(self) -> None:
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


class _ScriptedCapturedInput:
    runtime_label = "plain"

    def __init__(self, lines: Sequence[str]) -> None:
        self._lines = iter(lines)

    def read_line(self, prompt_label: str, *, footer: str | None = None) -> str:
        del prompt_label, footer
        return next(self._lines, "")


class _TrustStore:
    def __init__(
        self,
        trace: list[str],
        *,
        saved: ProjectTrustEntry | None = None,
        read_failure: BaseException | None = None,
        write_failure: BaseException | None = None,
    ) -> None:
        self.trace = trace
        self.saved = saved
        self.read_failure = read_failure
        self.write_failure = write_failure
        self.updates: tuple[tuple[Path, bool | None], ...] | None = None

    def get_entry(self, cwd: Path | str) -> ProjectTrustEntry | None:
        del cwd
        self.trace.append("read")
        if self.read_failure is not None:
            raise self.read_failure
        return self.saved

    def set_many(self, updates: tuple[tuple[Path, bool | None], ...]) -> None:
        self.trace.append("write")
        if self.write_failure is not None:
            raise self.write_failure
        self.updates = updates


def _workspace(tmp_path: Path) -> Path:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    return cwd


def _settings(tmp_path: Path, cwd: Path, *, trusted: bool) -> SettingsManager:
    return SettingsManager(
        global_path=tmp_path / "config" / "settings.json",
        project_path=cwd / ".pipy" / "settings.json",
        env={},
        overrides={"quietStartup": True},
        project_trusted=trusted,
    )


def _install_terminal(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cwd: Path,
    commands: Sequence[str],
    trace: list[str],
) -> ToolLoopTerminalUi:
    terminal = ToolLoopTerminalUi(
        input_stream=io.StringIO(), terminal_stream=io.StringIO(), cwd=cwd
    )
    scripted = iter(commands)

    def build(self: CodingSession, **_kwargs: object) -> ToolLoopTerminalUi:
        del self
        return terminal

    def read_line(
        self: ToolLoopTerminalUi, prompt_label: str, *, footer: str | None = None
    ) -> str:
        del self, prompt_label, footer
        trace.append("footer")
        return next(scripted)

    @contextmanager
    def suspend(self: ToolLoopTerminalUi) -> Iterator[None]:
        del self
        trace.append("external-io-suspend")
        try:
            yield
        finally:
            trace.append("external-io-resume")

    monkeypatch.setattr(CodingSession, "_build_terminal_ui", build)
    monkeypatch.setattr(ToolLoopTerminalUi, "read_line", read_line)
    monkeypatch.setattr(ToolLoopTerminalUi, "external_io_suspension", suspend)
    return terminal


def _run_live(session: CodingSession, cwd: Path) -> tuple[str, str]:
    output = io.StringIO()
    error = io.StringIO()
    session.run(
        workspace_root=cwd,
        input_stream=io.StringIO("must remain unread"),
        output_stream=output,
        error_stream=error,
    )
    return output.getvalue(), error.getvalue()


def _notice_text(terminal: ToolLoopTerminalUi) -> list[str]:
    return [
        "\n".join(lines)
        for kind, lines in terminal._transcript.history_blocks
        if kind == "notice"
    ]


def test_captured_trust_refuses_exactly_without_reading_underlying_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.repl.selector_actions as selector_module

    cwd = _workspace(tmp_path)
    scripted = _ScriptedCapturedInput(("/trust\n", "/exit\n"))

    def build_input(self: CodingSession, **_kwargs: object) -> _ScriptedCapturedInput:
        del self
        return scripted

    def construct_store() -> _TrustStore:
        raise AssertionError("captured /trust must not construct the trust store")

    monkeypatch.setattr(CodingSession, "_build_repl_input", build_input)
    monkeypatch.setattr(selector_module, "ProjectTrustStore", construct_store)
    provider = _RecordingProvider()
    error = io.StringIO()
    underlying_input = io.StringIO("PRIVATE CAPTURED INPUT")

    CodingSession(provider=provider).run(
        workspace_root=cwd,
        input_stream=underlying_input,
        output_stream=io.StringIO(),
        error_stream=error,
    )

    assert (
        "pipy: /trust requires the interactive product TUI; use --approve for this run."
    ) in error.getvalue()
    assert underlying_input.tell() == 0
    assert provider.requests == []


def test_live_trust_success_orders_effects_and_keeps_runtime_state_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.repl.selector_actions as selector_module

    cwd = _workspace(tmp_path)
    settings = _settings(tmp_path, cwd, trusted=False)
    trace: list[str] = []
    terminal = _install_terminal(
        monkeypatch, cwd=cwd, commands=("/trust\n", "/exit\n"), trace=trace
    )
    saved = ProjectTrustEntry(path=cwd.parent, decision=False)
    store = _TrustStore(trace, saved=saved)
    selected = get_project_trust_options(cwd)[0]

    def selector(
        ui: ToolLoopTerminalUi,
        *,
        cwd: Path,
        options: Sequence[ProjectTrustOption],
        saved_decision: ProjectTrustEntry | None = None,
        current_trusted: bool | None = None,
        startup: bool = False,
    ) -> ProjectTrustOption:
        assert ui is terminal
        assert cwd == cwd_path
        assert tuple(options) == get_project_trust_options(cwd_path)
        assert saved_decision == saved
        assert current_trusted is False
        assert startup is False
        trace.append("selector")
        return selected

    cwd_path = cwd
    original_notice = ToolLoopTerminalUi.add_notice

    def notice(self: ToolLoopTerminalUi, text: str) -> None:
        if text.startswith("pipy: saved trust decision"):
            trace.append("success")
        original_notice(self, text)

    monkeypatch.setattr(selector_module, "ProjectTrustStore", lambda: store)
    monkeypatch.setattr(selector_module, "run_project_trust_selector", selector)
    monkeypatch.setattr(ToolLoopTerminalUi, "add_notice", notice)
    provider = _RecordingProvider()

    output, error = _run_live(
        CodingSession(
            provider=provider,
            settings_manager=settings,
        ),
        cwd,
    )

    command_start = trace.index("read")
    assert trace[command_start : command_start + 5] == [
        "read",
        "selector",
        "write",
        "success",
        "footer",
    ]
    assert store.updates == selected.updates
    assert settings.project_trusted is False
    assert provider.requests == []
    assert "saved trust decision" not in output
    assert "saved trust decision" not in error
    assert any(
        "saved trust decision: trusted" in text for text in _notice_text(terminal)
    )


def test_live_trust_cancel_skips_write_and_success_then_refreshes_footer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.repl.selector_actions as selector_module

    cwd = _workspace(tmp_path)
    trace: list[str] = []
    terminal = _install_terminal(
        monkeypatch, cwd=cwd, commands=("/trust\n", "/exit\n"), trace=trace
    )
    store = _TrustStore(trace)

    def selector(*_args: object, **_kwargs: object) -> None:
        trace.append("selector")
        return None

    monkeypatch.setattr(selector_module, "ProjectTrustStore", lambda: store)
    monkeypatch.setattr(selector_module, "run_project_trust_selector", selector)

    _run_live(
        CodingSession(
            provider=_RecordingProvider(),
            settings_manager=_settings(tmp_path, cwd, trusted=True),
        ),
        cwd,
    )

    command_start = trace.index("read")
    assert trace[command_start:] == ["read", "selector", "footer"]
    assert store.updates is None
    assert not any("saved trust decision" in text for text in _notice_text(terminal))


@pytest.mark.parametrize("stage", ["read", "write"])
def test_project_trust_errors_are_live_only_sanitized_and_cut_off_later_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    import pipy_harness.native.repl.selector_actions as selector_module

    cwd = _workspace(tmp_path)
    trace: list[str] = []
    terminal = _install_terminal(
        monkeypatch, cwd=cwd, commands=("/trust\n", "/exit\n"), trace=trace
    )
    failure = ProjectTrustError(f"{stage} EV\x1bIL\x07 /private/path")
    store = _TrustStore(
        trace,
        read_failure=failure if stage == "read" else None,
        write_failure=failure if stage == "write" else None,
    )
    selected = get_project_trust_options(cwd)[0]

    def selector(*_args: object, **_kwargs: object) -> ProjectTrustOption:
        trace.append("selector")
        return selected

    monkeypatch.setattr(selector_module, "ProjectTrustStore", lambda: store)
    monkeypatch.setattr(selector_module, "run_project_trust_selector", selector)

    output, error = _run_live(
        CodingSession(
            provider=_RecordingProvider(),
            settings_manager=_settings(tmp_path, cwd, trusted=False),
        ),
        cwd,
    )

    command_start = trace.index("read")
    expected = ["read", "footer"]
    if stage == "write":
        expected = ["read", "selector", "write", "footer"]
    assert trace[command_start:] == expected
    notices = _notice_text(terminal)
    prefix = "could not read" if stage == "read" else "could not save"
    diagnostic = next(text for text in notices if prefix in text)
    assert "EV IL  /private/path" in diagnostic
    assert "\x1b" not in diagnostic and "\x07" not in diagnostic
    assert "EV" not in output and "EV" not in error
    assert not any("saved trust decision" in text for text in notices)


@pytest.mark.parametrize("stage", ["read", "selector", "write"])
@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
def test_interrupts_propagate_before_later_trust_effects_or_footer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    exception_type: type[BaseException],
) -> None:
    import pipy_harness.native.repl.selector_actions as selector_module

    cwd = _workspace(tmp_path)
    trace: list[str] = []
    _install_terminal(monkeypatch, cwd=cwd, commands=("/trust\n",), trace=trace)
    failure = exception_type("stop")
    store = _TrustStore(
        trace,
        read_failure=failure if stage == "read" else None,
        write_failure=failure if stage == "write" else None,
    )
    selected = get_project_trust_options(cwd)[0]

    def selector(*_args: object, **_kwargs: object) -> ProjectTrustOption:
        trace.append("selector")
        if stage == "selector":
            raise failure
        return selected

    monkeypatch.setattr(selector_module, "ProjectTrustStore", lambda: store)
    monkeypatch.setattr(selector_module, "run_project_trust_selector", selector)

    with pytest.raises(exception_type):
        _run_live(
            CodingSession(
                provider=_RecordingProvider(),
                settings_manager=_settings(tmp_path, cwd, trusted=False),
            ),
            cwd,
        )

    command_start = trace.index("read")
    expected = ["read"]
    if stage in {"selector", "write"}:
        expected.append("selector")
    if stage == "write":
        expected.append("write")
    assert trace[command_start:] == expected


def test_trust_has_no_session_extension_history_archive_or_provider_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.repl.selector_actions as selector_module

    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")
    assert tree.path is not None
    tree_before = tree.path.read_bytes()
    history = PromptHistoryStore(tmp_path / "prompt-history.json")
    history.set_enabled(True)
    history_before = history.path.read_bytes()
    archive = tmp_path / "metadata-archive.jsonl"
    archive.write_bytes(b'{"safe":"sentinel"}\n')
    archive_before = archive.read_bytes()
    trace: list[str] = []
    _install_terminal(
        monkeypatch, cwd=cwd, commands=("/trust\n", "/exit\n"), trace=trace
    )
    store = _TrustStore(trace)
    selected = get_project_trust_options(cwd)[0]

    def selector(*_args: object, **_kwargs: object) -> ProjectTrustOption:
        trace.append("selector")
        return selected

    def clear_extension_inputs(self: CodingInputQueue) -> None:
        del self
        raise AssertionError("/trust must not clear extension inputs")

    monkeypatch.setattr(selector_module, "ProjectTrustStore", lambda: store)
    monkeypatch.setattr(selector_module, "run_project_trust_selector", selector)
    monkeypatch.setattr(
        CodingInputQueue, "clear_extension_inputs", clear_extension_inputs
    )
    provider = _RecordingProvider()

    _run_live(
        CodingSession(
            provider=provider,
            native_session=tree,
            prompt_history_store=history,
            settings_manager=_settings(tmp_path, cwd, trusted=False),
        ),
        cwd,
    )

    assert provider.requests == []
    assert tree.get_entries() == []
    assert tree.path.read_bytes() == tree_before
    assert history.entries() == []
    assert history.path.read_bytes() == history_before
    assert archive.read_bytes() == archive_before
