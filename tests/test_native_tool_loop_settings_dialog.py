"""Characterization contracts for the live product ``/settings`` route.

These tests protect the composition boundary while settings dispatch moves into
the typed coding-command kernel.  Selector rendering and persistence primitives
have their own focused suites; this module pins the dialog's orchestration,
partial-effect ordering, and privacy behavior.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO, cast

import pytest

from pipy_harness.adapters import PipyNativeToolReplAdapter
from pipy_harness.models import HarnessStatus, RunRequest
from pipy_harness.native import NativeToolReplSession
from pipy_harness.native.agent.usage import AgentUsageAccumulator
from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.catalog_state import ProviderCatalogState
from pipy_harness.native.coding.state import CodingSessionState
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.prompt_history import PromptHistoryStore
from pipy_harness.native.provider import ProviderPort, StreamChunkSink
from pipy_harness.native.repl.settings_actions import drive_settings_dialog
from pipy_harness.native.repl_state import (
    ModelRuntime,
    NativeModelSelection,
    NativeReplProviderState,
)
from pipy_harness.native.session_tree import (
    ModelChangeEntry,
    NativeSessionTree,
    ThinkingLevelChangeEntry,
)
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.themes import NativeThemeStore, available_theme_names
from pipy_harness.native.tui import (
    ModelSelectorOption,
    ScopedModelRow,
    SettingsRow,
    ToolLoopTerminalUi,
)
from pipy_harness.runner import HarnessRunner

_SETTINGS_BODY_MARKER = "PIPY_PRIVATE_SETTINGS_MARKER_58a7f4d2"
_PROMPT_HISTORY_MARKER = "PIPY_PRIVATE_PROMPT_HISTORY_MARKER_9c12e6b5"
_AUTH_STORE_MARKER = "PIPY_PRIVATE_AUTH_STORE_MARKER_3d8f20a1"
_OAUTH_OUTPUT_MARKER = "PIPY_PRIVATE_OAUTH_OUTPUT_MARKER_7b41c9e6"
_PRIVATE_SETTINGS_MARKERS = (
    _SETTINGS_BODY_MARKER,
    _PROMPT_HISTORY_MARKER,
    _AUTH_STORE_MARKER,
    _OAUTH_OUTPUT_MARKER,
)


class _TerminalBuffer:
    def __init__(self) -> None:
        self.buffer = io.StringIO()

    def write(self, text: str) -> int:
        return self.buffer.write(text)

    def flush(self) -> None:
        self.buffer.flush()

    def isatty(self) -> bool:
        return True


class _RecordingProvider:
    supports_tool_calls = True

    def __init__(self, name: str = "fake", model_id: str = "fake-tools") -> None:
        self.name = name
        self.model_id = model_id
        self.completions = 0

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        del request, stream_sink, reasoning_sink, cancel_token
        self.completions += 1
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


class _ScriptedSettingsUi(ToolLoopTerminalUi):
    def __init__(self, tmp_path: Path, actions: Sequence[str] = ()) -> None:
        super().__init__(
            input_stream=cast(TextIO, io.StringIO()),
            terminal_stream=cast(TextIO, _TerminalBuffer()),
            cwd=tmp_path,
        )
        self.actions = list(actions)
        self.dialog_calls = 0
        self.dialog_rows: list[tuple[SettingsRow, ...]] = []
        self.dialog_exit_actions: list[frozenset[str]] = []
        self.rebuilt_rows: list[tuple[SettingsRow, ...]] = []
        self.selector_titles: list[str | None] = []
        self.selector_target: str | None = None
        self.selector_cancelled = False
        self.scope_calls = 0
        self.scope_selection: frozenset[str] | None = None
        self.suspend_calls = 0
        self.resume_calls = 0
        self.footer_updates = 0
        self.trace: list[str] = []
        self._lines = ["/settings\n", ""]

    def paint(self) -> None:
        return

    def start(self) -> None:
        return

    def close(self) -> None:
        return

    def read_line(self, prompt_label: str, *, footer: str | None = None) -> str:
        del prompt_label, footer
        return self._lines.pop(0)

    def set_footer_text(self, text: str) -> None:
        assert text
        self.footer_updates += 1
        self.trace.append("footer")

    @contextmanager
    def external_io_suspension(self) -> Iterator[None]:
        self.suspend_calls += 1
        self.trace.append("suspend")
        try:
            yield
        finally:
            self.resume_calls += 1
            self.trace.append("resume")

    def add_notice(self, text: str) -> None:
        self.trace.append(f"notice:{text}")
        super().add_notice(text)

    def run_settings_dialog(
        self,
        rows: Sequence[SettingsRow],
        *,
        on_local_action: Callable[[str], Sequence[SettingsRow]],
        exit_actions: frozenset[str] = frozenset(),
        current_index: int | None = None,
        title: str = "Settings",
        overlay_kind: str = "settings",
    ) -> str | None:
        del current_index, title, overlay_kind
        self.dialog_calls += 1
        self.trace.append("dialog")
        self.dialog_rows.append(tuple(rows))
        self.dialog_exit_actions.append(exit_actions)
        while self.actions:
            action = self.actions.pop(0)
            if action in exit_actions:
                return action
            rebuilt = tuple(on_local_action(action))
            self.rebuilt_rows.append(rebuilt)
        return None

    def run_model_selector(
        self,
        options: Sequence[ModelSelectorOption],
        *,
        current_index: int = 0,
        title: str | None = None,
    ) -> int | None:
        del current_index
        self.selector_titles.append(title)
        if self.selector_cancelled:
            return None
        if self.selector_target is None:
            return 0
        return next(
            index
            for index, option in enumerate(options)
            if self.selector_target in option.label
        )

    def run_scoped_models_selector(
        self,
        rows: Sequence[ScopedModelRow],
        *,
        checked: Iterable[int] = (),
    ) -> frozenset[str] | None:
        del rows, checked
        self.scope_calls += 1
        return self.scope_selection


def _settings(tmp_path: Path) -> SettingsManager:
    return SettingsManager(
        global_path=tmp_path / "config" / "settings.json",
        project_path=tmp_path / ".pipy" / "settings.json",
        env={},
    )


class _RecordingReplState(NativeReplProviderState):
    """State whose provider build is an observable ``_RecordingProvider``.

    Provider construction is otherwise runtime-owned; this double lets the
    settings-dialog tests inspect the built provider (``completions``) and its
    rebinds while ``/model`` listing/availability run through the bound catalog.
    """

    def __init__(self, built: list[_RecordingProvider], **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._built = built

    def provider_for(self, selection: NativeModelSelection) -> ProviderPort:
        provider = _RecordingProvider(selection.provider_name, selection.model_id)
        self._built.append(provider)
        return provider


def _cycle_thinking(
    state: NativeReplProviderState, tree: NativeSessionTree
) -> str | None:
    level = state.cycle_thinking_level()
    if level is not None:
        tree.append_thinking_level_change(level)
    return level


def _native_state(
    tmp_path: Path, built: list[_RecordingProvider] | None = None
) -> NativeReplProviderState:
    providers = built if built is not None else []
    catalog = ProviderCatalogState(
        models_json_path=tmp_path / "models.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={"OPENAI_API_KEY": "test-only"},
        openai_codex_auth_path=tmp_path / "missing-codex.json",
    )
    return _RecordingReplState(
        providers,
        selection=NativeModelSelection("fake", "fake-tools"),
        model_runtime=ModelRuntime(catalog=catalog),
        persist_defaults=False,
    )


def _install_ui(
    monkeypatch: pytest.MonkeyPatch,
    ui: _ScriptedSettingsUi,
) -> None:
    def build_ui(
        self: NativeToolReplSession,
        input_stream: TextIO,
        error_stream: TextIO,
        workspace: Path,
        resources: object = None,
        **kwargs: object,
    ) -> ToolLoopTerminalUi:
        del self, input_stream, error_stream, workspace, resources, kwargs
        return ui

    monkeypatch.setattr(NativeToolReplSession, "_build_terminal_ui", build_ui)


def _notices(ui: _ScriptedSettingsUi) -> list[str]:
    return [
        line
        for kind, lines in ui._transcript.history_blocks
        if kind == "notice"
        for line in lines
    ]


def _assert_private_markers_present(text: str, markers: tuple[str, ...]) -> None:
    if not all(marker in text for marker in markers):
        raise AssertionError("private settings fixture source omitted its marker")


def _assert_private_markers_absent(markers: tuple[str, ...], *texts: str) -> None:
    if any(marker in text for text in texts for marker in markers):
        raise AssertionError(
            "metadata-only workflow surface leaked private settings data"
        )


def _jsonl_events(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _seed_private_settings_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[SettingsManager, Path, Path, Path]:
    config_home = tmp_path / "config"
    settings_path = config_home / "settings.json"
    history_path = tmp_path / "private" / "history.json"
    theme_path = tmp_path / "private" / "theme.json"
    auth_path = tmp_path / "auth.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "hideThinkingBlock": True,
                "privateSettingsFixture": _SETTINGS_BODY_MARKER,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("PIPY_PROMPT_HISTORY_PATH", str(history_path))
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(theme_path))
    monkeypatch.delenv("PIPY_THEME", raising=False)
    history = PromptHistoryStore()
    history.set_enabled(True)
    history.record(_PROMPT_HISTORY_MARKER)
    AuthStore(path=auth_path).set(
        "openai", {"type": "api_key", "key": _AUTH_STORE_MARKER}
    )
    settings = SettingsManager.for_workspace(
        tmp_path,
        env={"PIPY_CONFIG_HOME": str(config_home)},
        project_trusted=False,
    )
    return settings, settings_path, history_path, auth_path


def test_settings_cancel_is_local_and_creates_no_product_tree_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _RecordingProvider()
    tree = NativeSessionTree.create(tmp_path, persist=False)
    ui = _ScriptedSettingsUi(tmp_path)
    _install_ui(monkeypatch, ui)

    result = NativeToolReplSession(
        provider=provider,
        tool_registry={},
        native_session=tree,
        settings_manager=_settings(tmp_path),
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert (result.user_turn_count, result.tool_invocation_count) == (0, 0)
    assert provider.completions == 0
    assert ui.dialog_calls == 1
    assert ui.footer_updates == 1
    assert tree.entries == []
    assert not any(kind == "settings" for kind, _lines in ui._transcript.history_blocks)


def test_settings_native_and_static_states_expose_distinct_exit_actions(
    tmp_path: Path,
) -> None:
    store = PromptHistoryStore(tmp_path / "history.json")
    settings = _settings(tmp_path)
    provider = _RecordingProvider()

    native_ui = _ScriptedSettingsUi(tmp_path)
    drive_settings_dialog(
        native_ui,
        store,
        provider=provider,
        provider_state=_native_state(tmp_path),
        apply_model_selection=lambda reference: (True, reference),
        apply_auth_change=lambda action, argument: f"{action}:{argument}",
        cycle_thinking_level=lambda: None,
        settings=settings,
        error_stream=io.StringIO(),
    )
    static_ui = _ScriptedSettingsUi(tmp_path)
    drive_settings_dialog(
        static_ui,
        store,
        provider=provider,
        provider_state=None,
        apply_model_selection=lambda reference: (True, reference),
        apply_auth_change=lambda action, argument: f"{action}:{argument}",
        cycle_thinking_level=lambda: None,
        settings=settings,
        error_stream=io.StringIO(),
    )

    native_actions = {
        row.action for row in native_ui.dialog_rows[0] if row.action is not None
    }
    static_actions = {
        row.action for row in static_ui.dialog_rows[0] if row.action is not None
    }
    assert native_ui.dialog_exit_actions == [
        frozenset(
            {
                "model",
                "login",
                "logout",
                "scoped_models",
                "theme",
                "project_trust_default",
            }
        )
    ]
    assert static_ui.dialog_exit_actions == [
        frozenset({"theme", "project_trust_default"})
    ]
    assert {"model", "scoped_models"}.issubset(native_actions)
    assert bool({"login", "logout"} & native_actions)
    assert not ({"model", "login", "logout", "scoped_models"} & static_actions)


def test_settings_local_actions_rebuild_in_place_and_keep_partial_effects(
    tmp_path: Path,
) -> None:
    store = PromptHistoryStore(tmp_path / "history.json")
    store.set_enabled(True)
    store.record("private saved prompt")
    settings = _settings(tmp_path)
    state = _native_state(tmp_path)
    state.replace_selection(NativeModelSelection("openai", "gpt-5.5"))
    tree = NativeSessionTree.create(tmp_path, persist=False)
    ui = _ScriptedSettingsUi(
        tmp_path,
        actions=(
            "toggle_history",
            "clear_history",
            "toggle_tools",
            "toggle_thinking",
            "cycle_thinking",
        ),
    )
    ui.input_editor.input_history = ["private saved prompt"]
    drive_settings_dialog(
        ui,
        store,
        provider=state.current_provider(),
        provider_state=state,
        apply_model_selection=lambda reference: (True, reference),
        apply_auth_change=lambda action, argument: f"{action}:{argument}",
        cycle_thinking_level=lambda: _cycle_thinking(state, tree),
        settings=settings,
        error_stream=io.StringIO(),
    )

    assert ui.dialog_calls == 1
    assert len(ui.rebuilt_rows) == 5
    assert store.enabled is False
    assert store.entries() == []
    assert ui.input_editor.input_history == ["private saved prompt"]
    assert ui.tools_expanded is True
    assert ui.thinking_hidden is True
    assert settings.get_hide_thinking_block() is True
    assert state.current_thinking_level() == "minimal"
    thinking_entries = [
        entry for entry in tree.entries if isinstance(entry, ThinkingLevelChangeEntry)
    ]
    assert [entry.thinking_level for entry in thinking_entries] == ["minimal"]
    assert not any(isinstance(entry, ModelChangeEntry) for entry in tree.entries)
    # Characterized gap: the dialog currently updates only the history cache;
    # the settings source-of-truth remains unchanged.
    assert settings.get_prompt_history_enabled() is False


@pytest.mark.parametrize(
    "action",
    ["model", "scoped_models", "theme", "project_trust_default"],
)
def test_settings_selector_exit_actions_cancel_then_reopen_dialog(
    tmp_path: Path,
    action: str,
) -> None:
    state = _native_state(tmp_path)
    provider = cast(_RecordingProvider, state.current_provider())
    ui = _ScriptedSettingsUi(tmp_path, actions=(action,))
    ui.selector_cancelled = True

    drive_settings_dialog(
        ui,
        PromptHistoryStore(tmp_path / "history.json"),
        provider=provider,
        provider_state=state,
        apply_model_selection=lambda reference: (True, reference),
        apply_auth_change=lambda selected, argument: f"{selected}:{argument}",
        cycle_thinking_level=lambda: None,
        settings=_settings(tmp_path),
        error_stream=io.StringIO(),
    )

    assert ui.dialog_calls == 2
    if action == "model":
        assert ui.selector_titles == [None]
    elif action == "scoped_models":
        assert ui.scope_calls == 1
    elif action == "theme":
        assert ui.selector_titles == ["Select theme"]
    else:
        assert ui.selector_titles == ["Default project trust"]
    assert _notices(ui) == []


def test_settings_fold_and_theme_write_failures_keep_live_partial_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_theme = next(name for name in available_theme_names() if name != "pi")
    monkeypatch.setenv("PIPY_NATIVE_THEME_PATH", str(tmp_path / "theme.json"))
    monkeypatch.delenv("PIPY_THEME", raising=False)
    state = _native_state(tmp_path)
    provider = cast(_RecordingProvider, state.current_provider())
    settings = _settings(tmp_path)
    ui = _ScriptedSettingsUi(tmp_path, actions=("toggle_thinking", "theme"))
    ui.selector_target = target_theme

    def fail_write(path: str, value: object, *, scope: str = "global") -> None:
        del path, value, scope
        raise RuntimeError("settings are read-only")

    monkeypatch.setattr(settings, "set_value", fail_write)
    drive_settings_dialog(
        ui,
        PromptHistoryStore(tmp_path / "history.json"),
        provider=provider,
        provider_state=state,
        apply_model_selection=lambda reference: (True, reference),
        apply_auth_change=lambda action, argument: f"{action}:{argument}",
        cycle_thinking_level=lambda: None,
        settings=settings,
        error_stream=io.StringIO(),
    )

    assert ui.dialog_calls == 2
    assert ui.thinking_hidden is True
    assert settings.get_hide_thinking_block() is False
    assert settings.get_theme() is None
    assert NativeThemeStore(tmp_path / "theme.json").load() == target_theme
    assert provider.completions == 0


@pytest.mark.parametrize("action", ["scoped_models", "project_trust_default"])
def test_settings_selector_write_failures_notice_and_reopen_without_provider_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    state = _native_state(tmp_path)
    provider = cast(_RecordingProvider, state.current_provider())
    settings = _settings(tmp_path)
    ui = _ScriptedSettingsUi(tmp_path, actions=(action,))
    if action == "scoped_models":
        ui.scope_selection = frozenset({"openai/gpt-5.5"})

        def fail_scope(models: list[str], *, scope: str = "global") -> None:
            del models, scope
            raise RuntimeError("scope store unavailable")

        monkeypatch.setattr(settings, "set_enabled_models", fail_scope)
    else:
        ui.selector_target = "Trust"

        def fail_trust(value: str, *, scope: str = "global") -> None:
            del value, scope
            raise RuntimeError("trust store unavailable")

        monkeypatch.setattr(settings, "set_default_project_trust", fail_trust)

    drive_settings_dialog(
        ui,
        PromptHistoryStore(tmp_path / "history.json"),
        provider=provider,
        provider_state=state,
        apply_model_selection=lambda reference: (True, reference),
        apply_auth_change=lambda selected, argument: f"{selected}:{argument}",
        cycle_thinking_level=lambda: None,
        settings=settings,
        error_stream=io.StringIO(),
    )

    assert ui.dialog_calls == 2
    notices = _notices(ui)
    assert len(notices) == 1
    assert "unavailable" in notices[0]
    assert settings.get_enabled_models() == []
    assert settings.get_default_project_trust() == "ask"
    assert provider.completions == 0


def test_settings_model_selection_rebinds_without_deferred_tree_or_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    proof = tmp_path / "model-events.txt"
    (extension_dir / "model_observer.py").write_text(
        "from pathlib import Path\n"
        f"PROOF = Path({str(proof)!r})\n"
        "def activate(api):\n"
        "    @api.on('session_start')\n"
        "    def started(event, ctx):\n"
        "        del event, ctx\n"
        "        PROOF.write_text('session-start\\n', encoding='utf-8')\n"
        "    @api.on('model_select')\n"
        "    def selected(event, ctx):\n"
        "        del event, ctx\n"
        "        with PROOF.open('a', encoding='utf-8') as handle:\n"
        "            handle.write('model-select\\n')\n",
        encoding="utf-8",
    )
    built: list[_RecordingProvider] = []
    state = _native_state(tmp_path, built)
    provider = cast(_RecordingProvider, state.current_provider())
    tree = NativeSessionTree.create(tmp_path, persist=False)
    ui = _ScriptedSettingsUi(tmp_path, actions=("model",))
    ui.selector_target = "openai/gpt-5.5"
    _install_ui(monkeypatch, ui)

    result = NativeToolReplSession(
        provider=provider,
        provider_state=state,
        native_session=tree,
        tool_registry={},
        settings_manager=_settings(tmp_path),
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert state.current_selection().reference == "openai/gpt-5.5"
    assert result.provider_name == "openai"
    assert result.model_id == "gpt-5.5"
    assert ui.dialog_calls == 2
    assert any("selected model" in notice for notice in _notices(ui))
    assert not any(isinstance(entry, ModelChangeEntry) for entry in tree.entries)
    assert proof.read_text(encoding="utf-8").splitlines() == ["session-start"]
    assert all(provider.completions == 0 for provider in built)


@pytest.mark.parametrize("action", ["login", "logout"])
def test_settings_auth_failure_orders_suspend_rebind_notice_and_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    state = _native_state(tmp_path)
    provider = cast(_RecordingProvider, state.current_provider())
    ui = _ScriptedSettingsUi(tmp_path, actions=(action,))
    _install_ui(monkeypatch, ui)
    original_rebind = CodingSessionState.rebind_provider

    def fail_login(
        self: NativeReplProviderState,
        provider_name: str,
        *,
        input_stream: TextIO,
        output_stream: TextIO,
    ) -> tuple[bool, str]:
        del self, provider_name, input_stream, output_stream
        ui.trace.append("auth")
        raise RuntimeError("token=sk-private\x1b[31m")

    def fail_logout(
        self: NativeReplProviderState, provider_name: str
    ) -> tuple[bool, str]:
        del self, provider_name
        ui.trace.append("auth")
        raise RuntimeError("token=sk-private\x1b[31m")

    def record_rebind(
        self: CodingSessionState,
        rebound_provider: ProviderPort,
        *,
        provider_name: str,
        model_id: str,
        usage_accumulator: AgentUsageAccumulator,
    ) -> None:
        ui.trace.append("rebind")
        original_rebind(
            self,
            rebound_provider,
            provider_name=provider_name,
            model_id=model_id,
            usage_accumulator=usage_accumulator,
        )

    monkeypatch.setattr(NativeReplProviderState, "login", fail_login)
    monkeypatch.setattr(NativeReplProviderState, "logout", fail_logout)
    monkeypatch.setattr(CodingSessionState, "rebind_provider", record_rebind)

    result = NativeToolReplSession(
        provider=provider,
        provider_state=state,
        tool_registry={},
        settings_manager=_settings(tmp_path),
    ).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )

    expected_middle = ["auth", "rebind", "footer"]
    if action == "login":
        expected_middle = ["suspend", "auth", "resume", "rebind", "footer"]
        assert ui.suspend_calls == 1
        assert ui.resume_calls == 1
    else:
        assert ui.suspend_calls == 0
        assert ui.resume_calls == 0
    start = ui.trace.index(expected_middle[0])
    assert ui.trace[start : start + len(expected_middle)] == expected_middle
    assert ui.trace[-1] == "dialog"
    assert ui.dialog_calls == 2
    notices = _notices(ui)
    assert notices == [
        f"pipy: openai-codex {action} failed with RuntimeError: [REDACTED]"
    ]
    assert "sk-private" not in repr(result)
    assert provider.completions == 0
    assert result.user_turn_count == 0


@pytest.mark.parametrize("action", ["login", "logout"])
@pytest.mark.parametrize("failure_type", [KeyboardInterrupt, SystemExit])
def test_settings_auth_fatal_cuts_off_rebind_notice_reopen_and_outer_footer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    failure_type: type[BaseException],
) -> None:
    state = _native_state(tmp_path)
    provider = cast(_RecordingProvider, state.current_provider())
    ui = _ScriptedSettingsUi(tmp_path, actions=(action,))
    _install_ui(monkeypatch, ui)
    initial_footer_updates = 0

    def fatal_login(
        self: NativeReplProviderState,
        provider_name: str,
        *,
        input_stream: TextIO,
        output_stream: TextIO,
    ) -> tuple[bool, str]:
        del self, provider_name, input_stream, output_stream
        ui.trace.append("auth")
        raise failure_type("stop settings auth")

    def fatal_logout(
        self: NativeReplProviderState, provider_name: str
    ) -> tuple[bool, str]:
        del self, provider_name
        ui.trace.append("auth")
        raise failure_type("stop settings auth")

    monkeypatch.setattr(NativeReplProviderState, "login", fatal_login)
    monkeypatch.setattr(NativeReplProviderState, "logout", fatal_logout)

    with pytest.raises(failure_type, match="stop settings auth"):
        NativeToolReplSession(
            provider=provider,
            provider_state=state,
            tool_registry={},
            settings_manager=_settings(tmp_path),
        ).run(
            workspace_root=tmp_path,
            input_stream=io.StringIO(),
            output_stream=io.StringIO(),
            error_stream=io.StringIO(),
        )

    assert ui.dialog_calls == 1
    assert not any(item == "rebind" for item in ui.trace)
    assert not any(item.startswith("notice:") for item in ui.trace)
    assert ui.footer_updates == initial_footer_updates + 1
    if action == "login":
        assert ui.trace[-3:] == ["suspend", "auth", "resume"]
        assert ui.suspend_calls == 1
        assert ui.resume_calls == 1
    else:
        assert ui.trace[-1] == "auth"
        assert ui.suspend_calls == 0
        assert ui.resume_calls == 0
    assert provider.completions == 0


def test_live_settings_private_sources_stay_out_of_finalized_metadata_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, settings_path, history_path, auth_path = _seed_private_settings_sources(
        tmp_path, monkeypatch
    )
    state = _native_state(tmp_path)
    provider = cast(_RecordingProvider, state.current_provider())
    tree = NativeSessionTree.create(tmp_path, persist=False)
    ui = _ScriptedSettingsUi(tmp_path, actions=("login",))
    _install_ui(monkeypatch, ui)
    auth_reads: list[str] = []
    original_auth_get = AuthStore.get

    def recording_auth_get(
        self: AuthStore, provider_name: str
    ) -> dict[str, object] | None:
        auth_reads.append(provider_name)
        return original_auth_get(self, provider_name)

    monkeypatch.setattr(AuthStore, "get", recording_auth_get)

    def login(
        self: NativeReplProviderState,
        provider_name: str,
        *,
        input_stream: TextIO,
        output_stream: TextIO,
    ) -> tuple[bool, str]:
        del self, provider_name, input_stream
        print(_OAUTH_OUTPUT_MARKER, file=output_stream)
        return False, "pipy: login cancelled."

    monkeypatch.setattr(NativeReplProviderState, "login", login)
    error_stream = io.StringIO()
    adapter = PipyNativeToolReplAdapter(
        provider_state=state,
        tool_registry={},
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
        error_stream=error_stream,
        native_session=tree,
        settings_manager=settings,
    )
    result = HarnessRunner(
        adapter=adapter,
        id_factory=lambda: "settings-private-sources",
    ).run(
        RunRequest(
            agent="pipy-native",
            slug="settings-private-sources",
            command=[],
            cwd=tmp_path,
            goal="settings archive privacy characterization",
            root=tmp_path / "workflow-archive",
        )
    )

    _assert_private_markers_present(
        settings_path.read_text(encoding="utf-8"), (_SETTINGS_BODY_MARKER,)
    )
    _assert_private_markers_present(
        history_path.read_text(encoding="utf-8"), (_PROMPT_HISTORY_MARKER,)
    )
    _assert_private_markers_present(
        auth_path.read_text(encoding="utf-8"), (_AUTH_STORE_MARKER,)
    )
    _assert_private_markers_present(error_stream.getvalue(), (_OAUTH_OUTPUT_MARKER,))
    _assert_private_markers_present(
        "\n".join(ui.input_editor.input_history), (_PROMPT_HISTORY_MARKER,)
    )
    assert settings.get_hide_thinking_block() is True
    assert ui.thinking_hidden is True
    assert "openai" in auth_reads

    result_metadata = json.dumps(result.metadata, sort_keys=True)
    result_repr = repr(result)
    assert result.record.markdown_path is not None
    archive_jsonl = result.record.jsonl_path.read_text(encoding="utf-8")
    archive_markdown = result.record.markdown_path.read_text(encoding="utf-8")
    _assert_private_markers_absent(
        _PRIVATE_SETTINGS_MARKERS,
        result_metadata,
        result_repr,
        archive_jsonl,
        archive_markdown,
    )

    assert result.status is HarnessStatus.SUCCEEDED
    assert result.run_id == "settings-private-sources"
    assert isinstance(result.metadata, dict)
    assert result.metadata["tool_invocation_count"] == 0
    assert result.metadata["user_turn_count"] == 0
    assert ".in-progress" not in result.record.jsonl_path.parts
    assert ".in-progress" not in result.record.markdown_path.parts
    events = _jsonl_events(result.record.jsonl_path)
    assert events[-1]["type"] == "session.finalized"
    assert events[-1]["run_id"] == result.run_id
    assert tree.entries == []
    assert provider.completions == 0
