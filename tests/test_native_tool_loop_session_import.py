"""Characterization contracts for the native ``/import`` command."""

from __future__ import annotations

import io
import json
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO, cast

import pytest

from pipy_harness.adapters import PipyNativeToolReplAdapter
from pipy_harness.models import HarnessStatus, RunRequest
from pipy_harness.native import (
    NativeToolReplResult,
    NativeToolReplSession,
    ProviderRequest,
    ProviderResult,
)
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentEvent,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.coding.input_queue import CodingInputQueue
from pipy_harness.native.coding.product_session import CodingProductSessionCoordinator
from pipy_harness.native.export_distribution import (
    NativeExportError,
    export_native_branch_to_jsonl,
)
from pipy_harness.native.extension_hooks import (
    _ExtensionLifecycleAgentEventAdapter,
)
from pipy_harness.native.extension_runtime import SessionDecision
from pipy_harness.native.prompt_history import PromptHistoryStore
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager
from pipy_harness.runner import HarnessRunner


_TRANSCRIPT_MARKER = "PIPY_PRIVATE_IMPORT_TRANSCRIPT_f41e909b"
_PATH_MARKER = "PIPY_PRIVATE_IMPORT_PATH_72f05e1a"


class _RecordingProvider:
    name = "fake"
    model_id = "fake-native-bootstrap"
    supports_tool_calls = True

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
            final_text="provider response after import",
            tool_calls=(),
        )


class _RecordingAgentSink:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


class _FailingPromptStream:
    def __init__(self, command: str, error_factory: Callable[[], Exception]) -> None:
        self._command = command
        self._error_factory = error_factory
        self._read_count = 0

    def readline(self) -> str:
        self._read_count += 1
        if self._read_count == 1:
            return self._command
        if self._read_count == 2:
            raise self._error_factory()
        return ""


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
        project_trusted=False,
    )


def _active_tree(
    tmp_path: Path,
    cwd: Path,
    *,
    persist: bool = True,
) -> NativeSessionTree:
    return NativeSessionTree.create(
        cwd,
        session_dir=tmp_path / "destination-store" if persist else None,
        persist=persist,
        session_id="active-before-import",
    )


def _import_source(
    tmp_path: Path,
    cwd: Path,
    *,
    name: str = "portable-session.data",
    marker: str = _TRANSCRIPT_MARKER,
) -> tuple[Path, NativeSessionTree]:
    source_tree = NativeSessionTree.create(
        cwd,
        session_dir=tmp_path / "source-store",
    )
    source_tree.append_message(AgentUserMessage(content=ProductContent(marker)))
    source_tree.append_message(
        AgentAssistantMessage(content=ProductContent("imported assistant history"))
    )
    source = cwd / name
    export_native_branch_to_jsonl(source_tree, source)
    return source, source_tree


def _run(
    session: NativeToolReplSession,
    cwd: Path,
    commands: str | _FailingPromptStream,
) -> tuple[NativeToolReplResult, str, str]:
    output = io.StringIO()
    error = io.StringIO()
    result = session.run(
        workspace_root=cwd,
        input_stream=(
            io.StringIO(commands)
            if isinstance(commands, str)
            else cast(TextIO, commands)
        ),
        output_stream=output,
        error_stream=error,
    )
    return result, output.getvalue(), error.getvalue()


def _destination_paths(tree: NativeSessionTree) -> list[Path]:
    assert tree.path is not None
    return sorted(path for path in tree.path.parent.glob("*") if path != tree.path)


def _user_texts(request: ProviderRequest) -> list[str]:
    return [
        message.content.value
        for message in request.messages
        if isinstance(message, AgentUserMessage)
    ]


def _jsonl_objects(path: Path) -> list[dict[str, object]]:
    result = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert all(isinstance(item, dict) for item in result)
    return result


def test_composition_preserves_raw_bubbles_outer_trim_and_path_routing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    cwd = _workspace(tmp_path)
    active = _active_tree(tmp_path, cwd)
    quoted, _ = _import_source(tmp_path, cwd, name="quoted source.bundle")
    unquoted, _ = _import_source(tmp_path, cwd, name="unquoted.data")
    absolute, _ = _import_source(tmp_path, cwd, name="absolute.JSONL.backup")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    home_source, _ = _import_source(tmp_path, cwd, name="home-source.anything")
    moved_home_source = fake_home / home_source.name
    moved_home_source.write_bytes(home_source.read_bytes())
    home_source.unlink()
    bubbles: list[str] = []
    footer_usage: list[bool] = []

    def render_user_message(_renderer: object, text: str) -> None:
        bubbles.append(text)

    def footer(
        _session: NativeToolReplSession,
        _error_stream: TextIO,
        **kwargs: object,
    ) -> None:
        footer_usage.append(kwargs.get("usage_snapshot") is not None)

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(
        loop_module._ToolLoopRenderer, "render_user_message", render_user_message
    )
    monkeypatch.setattr(NativeToolReplSession, "_print_footer", footer)
    commands = (
        ' \t/import "quoted source.bundle" --yes ignored \t\n'
        "/import unquoted.data --yes trailing\n"
        f"/import {absolute} --yes\n"
        f"/import ~/{moved_home_source.name} --yes\n"
        "/exit\n"
    )

    result, _output, error = _run(
        NativeToolReplSession(
            provider=_RecordingProvider(),
            native_session=active,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        commands,
    )

    assert bubbles == commands.splitlines()
    assert footer_usage == [True] + [False] * 4
    assert error.count("imported native session") == 4
    assert result.user_turn_count == 0
    assert result.tool_invocation_count == 0
    assert active.path is not None
    destination_names = {path.name for path in active.path.parent.glob("*")}
    assert quoted.name in destination_names
    assert unquoted.name in destination_names
    assert absolute.name in destination_names
    assert moved_home_source.name in destination_names


def test_import_empty_argument_reports_usage_without_confirmation(
    tmp_path: Path,
) -> None:
    cwd = _workspace(tmp_path)
    active = _active_tree(tmp_path, cwd)

    result, _output, error = _run(
        NativeToolReplSession(
            provider=_RecordingProvider(),
            native_session=active,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        "/import\n/import    \n/exit\n",
    )

    assert error.count("pipy: Usage: /import <path.jsonl>") == 2
    assert "Replace current session" not in error
    assert result.user_turn_count == 0
    assert _destination_paths(active) == []


@pytest.mark.parametrize(
    ("yes_token", "response", "expected_imports", "expects_prompt"),
    [
        ("--yes", "", 1, False),
        ("\t--yes\t", "", 1, False),
        ("--YES", "n\n", 0, True),
        ("prefix--yes", "n\n", 0, True),
        ("--yes-suffix", "n\n", 0, True),
    ],
)
def test_yes_is_an_exact_case_sensitive_whitespace_token(
    tmp_path: Path,
    yes_token: str,
    response: str,
    expected_imports: int,
    expects_prompt: bool,
) -> None:
    cwd = _workspace(tmp_path)
    active = _active_tree(tmp_path, cwd)
    source, _ = _import_source(tmp_path, cwd)

    _result, _output, error = _run(
        NativeToolReplSession(
            provider=_RecordingProvider(),
            native_session=active,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        f"/import {source.name} {yes_token}\n{response}/exit\n",
    )

    assert ("Replace current session" in error) is expects_prompt
    assert len(_destination_paths(active)) == expected_imports


@pytest.mark.parametrize("response", ["y\n", "Y\n", "yes\n", "YeS\n"])
def test_confirmation_accepts_y_or_yes_case_insensitively(
    tmp_path: Path,
    response: str,
) -> None:
    cwd = _workspace(tmp_path)
    active = _active_tree(tmp_path, cwd)
    source, _ = _import_source(tmp_path, cwd)

    _result, _output, error = _run(
        NativeToolReplSession(
            provider=_RecordingProvider(),
            native_session=active,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        f"/import {source}\n{response}/exit\n",
    )

    assert f"Replace current session with {source}? [y/N] " in error
    assert "imported native session" in error
    assert len(_destination_paths(active)) == 1


@pytest.mark.parametrize("response", ["n\n", "no\n", "\n", "later\n", ""])
def test_confirmation_decline_blank_and_eof_cancel(
    tmp_path: Path,
    response: str,
) -> None:
    cwd = _workspace(tmp_path)
    active = _active_tree(tmp_path, cwd)
    source, _ = _import_source(tmp_path, cwd)

    _result, _output, error = _run(
        NativeToolReplSession(
            provider=_RecordingProvider(),
            native_session=active,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        f"/import {source}\n{response}",
    )

    assert f"Replace current session with {source}? [y/N] " in error
    assert "pipy: /import cancelled." in error
    assert _destination_paths(active) == []


@pytest.mark.parametrize("error_factory", [OSError, ValueError])
def test_confirmation_read_errors_cancel(
    tmp_path: Path,
    error_factory: type[Exception],
) -> None:
    cwd = _workspace(tmp_path)
    active = _active_tree(tmp_path, cwd)
    source, _ = _import_source(tmp_path, cwd)
    stream = _FailingPromptStream(
        f"/import {source}\n", lambda: error_factory("prompt failed")
    )

    _result, _output, error = _run(
        NativeToolReplSession(
            provider=_RecordingProvider(),
            native_session=active,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        stream,
    )

    assert "pipy: /import cancelled." in error
    assert _destination_paths(active) == []


def test_missing_cwd_requires_second_prompt_even_with_yes_and_rewrites_copy_only(
    tmp_path: Path,
) -> None:
    cwd = _workspace(tmp_path)
    missing_cwd = tmp_path / "removed-source-workspace"
    missing_cwd.mkdir()
    source, _ = _import_source(tmp_path, missing_cwd, name="missing-cwd.payload")
    relocated_source = tmp_path / source.name
    source.replace(relocated_source)
    source = relocated_source
    source_before = source.read_bytes()
    missing_cwd.rmdir()
    active = _active_tree(tmp_path, cwd)

    _result, _output, error = _run(
        NativeToolReplSession(
            provider=_RecordingProvider(),
            native_session=active,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        f"/import {source} --yes\ny\n/exit\n",
    )

    assert "Replace current session with" not in error
    assert (
        f"imported session cwd does not exist: {missing_cwd} "
        f"Use current workspace {cwd}? [y/N] "
    ) in error
    assert source.read_bytes() == source_before
    destinations = _destination_paths(active)
    assert len(destinations) == 1
    header = json.loads(destinations[0].read_text(encoding="utf-8").splitlines()[0])
    assert header["cwd"] == str(cwd)


def test_missing_cwd_second_prompt_decline_cancels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd = _workspace(tmp_path)
    active = _active_tree(tmp_path, cwd)
    source, _ = _import_source(tmp_path, cwd)

    import pipy_harness.native.tool_loop_session as loop_module

    original_import = loop_module.import_native_session_jsonl

    def missing_cwd(
        _source: Path, *, session_dir: Path, missing_cwd: Path | None = None
    ) -> NativeSessionTree:
        del session_dir
        if missing_cwd is None:
            raise NativeExportError("imported session cwd does not exist: removed")
        assert active.path is not None
        return original_import(source, session_dir=active.path.parent)

    monkeypatch.setattr(loop_module, "import_native_session_jsonl", missing_cwd)
    _result, _output, error = _run(
        NativeToolReplSession(
            provider=_RecordingProvider(),
            native_session=active,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        f"/import {source} --yes\nn\n/exit\n",
    )

    assert "Use current workspace" in error
    assert "pipy: /import cancelled." in error
    assert _destination_paths(active) == []


@pytest.mark.parametrize("error_factory", [OSError, ValueError])
def test_missing_cwd_second_prompt_read_errors_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_factory: type[Exception],
) -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    cwd = _workspace(tmp_path)
    active = _active_tree(tmp_path, cwd)
    source, _ = _import_source(tmp_path, cwd)

    def missing_cwd(
        _source: Path, *, session_dir: Path, missing_cwd: Path | None = None
    ) -> NativeSessionTree:
        del session_dir
        assert missing_cwd is None
        raise NativeExportError("imported session cwd does not exist: removed")

    monkeypatch.setattr(loop_module, "import_native_session_jsonl", missing_cwd)
    stream = _FailingPromptStream(
        f"/import {source} --yes\n", lambda: error_factory("prompt failed")
    )
    _result, _output, error = _run(
        NativeToolReplSession(
            provider=_RecordingProvider(),
            native_session=active,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        stream,
    )

    assert "Use current workspace" in error
    assert "pipy: /import cancelled." in error
    assert _destination_paths(active) == []


def test_missing_cwd_second_import_error_is_controlled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    cwd = _workspace(tmp_path)
    active = _active_tree(tmp_path, cwd)
    source, _ = _import_source(tmp_path, cwd)
    calls: list[Path | None] = []

    def fail_twice(
        _source: Path, *, session_dir: Path, missing_cwd: Path | None = None
    ) -> NativeSessionTree:
        del session_dir
        calls.append(missing_cwd)
        if missing_cwd is None:
            raise NativeExportError("imported session cwd does not exist: removed")
        raise NativeExportError("replacement failed")

    monkeypatch.setattr(loop_module, "import_native_session_jsonl", fail_twice)
    _result, _output, error = _run(
        NativeToolReplSession(
            provider=_RecordingProvider(),
            native_session=active,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        f"/import {source} --yes\ny\n/exit\n",
    )

    assert calls == [None, cwd]
    assert "pipy: replacement failed" in error
    assert _destination_paths(active) == []


@pytest.mark.parametrize("allowed", [True, False])
def test_session_before_switch_runs_once_before_import_and_can_veto(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allowed: bool,
) -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    cwd = _workspace(tmp_path)
    active = _active_tree(tmp_path, cwd)
    source, _ = _import_source(tmp_path, cwd)
    trace: list[str] = []
    original_import = loop_module.import_native_session_jsonl

    def gate(_hooks: object, **kwargs: object) -> SessionDecision:
        trace.append(f"hook:{kwargs['operation']}:{kwargs['target']}")
        return SessionDecision(allow=allowed, reason=None if allowed else "stay here")

    def import_session(
        source_path: Path, *, session_dir: Path, missing_cwd: Path | None = None
    ) -> NativeSessionTree:
        trace.append("import")
        return original_import(
            source_path, session_dir=session_dir, missing_cwd=missing_cwd
        )

    monkeypatch.setattr(loop_module, "dispatch_session_before_hooks", gate)
    monkeypatch.setattr(loop_module, "import_native_session_jsonl", import_session)
    _result, _output, error = _run(
        NativeToolReplSession(
            provider=_RecordingProvider(),
            native_session=active,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        f"/import {source} --yes\n/exit\n",
    )

    expected_hook = f"hook:switch:{source}"
    assert trace == ([expected_hook, "import"] if allowed else [expected_hook])
    assert ("switch blocked by extension: stay here" in error) is (not allowed)
    assert len(_destination_paths(active)) == int(allowed)


def test_success_assigns_then_rebuilds_clears_extension_input_and_next_turn_uses_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    cwd = _workspace(tmp_path)
    active = _active_tree(tmp_path, cwd)
    active.append_message(AgentUserMessage(content=ProductContent("old history")))
    source, _ = _import_source(tmp_path, cwd)
    provider = _RecordingProvider()
    trace: list[str] = []
    lifecycle: list[tuple[str, str | None]] = []
    original_import = loop_module.import_native_session_jsonl
    original_rebuild = CodingProductSessionCoordinator.rebuild_active_history
    original_clear = CodingInputQueue.clear_extension_inputs

    def import_session(
        source_path: Path, *, session_dir: Path, missing_cwd: Path | None = None
    ) -> NativeSessionTree:
        trace.append("import")
        return original_import(
            source_path, session_dir=session_dir, missing_cwd=missing_cwd
        )

    def rebuild(coordinator: CodingProductSessionCoordinator) -> None:
        original_rebuild(coordinator)
        trace.append("rebuild")

    def clear(queue: CodingInputQueue) -> None:
        original_clear(queue)
        trace.append("clear")

    def diagnostic(_terminal_ui: object, _error_stream: TextIO, message: str) -> None:
        trace.append(f"diagnostic:{message}")

    footer_count = 0

    def footer(
        _session: NativeToolReplSession, _error_stream: TextIO, **_kwargs: object
    ) -> None:
        nonlocal footer_count
        footer_count += 1
        trace.append(f"footer:{footer_count}")

    def fire_lifecycle(
        _emitter: object, name: str, *, reason: str | None = None
    ) -> None:
        lifecycle.append((name, reason))

    monkeypatch.setattr(loop_module, "import_native_session_jsonl", import_session)
    monkeypatch.setattr(
        CodingProductSessionCoordinator, "rebuild_active_history", rebuild
    )
    monkeypatch.setattr(CodingInputQueue, "clear_extension_inputs", clear)
    monkeypatch.setattr(
        NativeToolReplSession, "_emit_diagnostic", staticmethod(diagnostic)
    )
    monkeypatch.setattr(NativeToolReplSession, "_print_footer", footer)
    monkeypatch.setattr(
        _ExtensionLifecycleAgentEventAdapter, "fire_lifecycle", fire_lifecycle
    )

    _result, _output, _error = _run(
        NativeToolReplSession(
            provider=provider,
            native_session=active,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        f"/import {source} --yes\nnext prompt\n/exit\n",
    )

    import_index = trace.index("import")
    post_import_rebuild = trace.index("rebuild", import_index)
    clear_index = trace.index("clear")
    diagnostic_index = next(
        index
        for index, item in enumerate(trace)
        if item.startswith("diagnostic:pipy: imported native session")
    )
    command_footer = trace.index("footer:2")
    assert (
        import_index
        < post_import_rebuild
        < clear_index
        < diagnostic_index
        < command_footer
    )
    assert len(provider.requests) == 1
    assert _user_texts(provider.requests[0]) == [_TRANSCRIPT_MARKER, "next prompt"]
    assert "old history" not in repr(provider.requests[0].messages)
    assert [item for item in lifecycle if item[0].startswith("session_")] == [
        ("session_start", "startup"),
        ("session_shutdown", None),
    ]


def test_import_uses_persistent_and_default_destination_stores_with_collisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    cwd = _workspace(tmp_path)
    source, _ = _import_source(tmp_path, cwd, name="collision.payload")
    source_before = source.read_bytes()
    persistent = _active_tree(tmp_path, cwd)

    _run(
        NativeToolReplSession(
            provider=_RecordingProvider(),
            native_session=persistent,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        f"/import {source} --yes\n/import {source} --yes\n/exit\n",
    )

    assert persistent.path is not None
    assert (persistent.path.parent / source.name).is_file()
    assert (persistent.path.parent / f"{source.stem}-1{source.suffix}").is_file()

    default_store = tmp_path / "default-product-store"
    monkeypatch.setattr(
        loop_module, "default_native_session_dir", lambda _cwd: default_store
    )
    ephemeral = _active_tree(tmp_path, cwd, persist=False)
    _run(
        NativeToolReplSession(
            provider=_RecordingProvider(),
            native_session=ephemeral,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        f"/import {source} --yes\n/exit\n",
    )

    assert (default_store / source.name).is_file()
    assert source.read_bytes() == source_before


def test_controlled_import_error_is_sanitized_and_gets_footer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    cwd = _workspace(tmp_path)
    active = _active_tree(tmp_path, cwd)
    source, _ = _import_source(tmp_path, cwd)
    footers: list[None] = []

    def fail(*_args: object, **_kwargs: object) -> NativeSessionTree:
        raise NativeExportError("unsafe\x1b[31m label\nsecond\x07 line")

    monkeypatch.setattr(loop_module, "import_native_session_jsonl", fail)
    monkeypatch.setattr(
        NativeToolReplSession,
        "_print_footer",
        lambda *_args, **_kwargs: footers.append(None),
    )
    _result, _output, error = _run(
        NativeToolReplSession(
            provider=_RecordingProvider(),
            native_session=active,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        f"/import {source} --yes\n/exit\n",
    )

    assert "pipy: unsafe" in error and "label" in error
    assert "second  line" in error
    assert "\x1b" not in error and "\x07" not in error
    assert footers == [None, None]
    assert _destination_paths(active) == []


@pytest.mark.parametrize("failure", ["copy", "open"])
def test_uncontrolled_copy_and_open_failures_propagate_with_partial_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    cwd = _workspace(tmp_path)
    active = _active_tree(tmp_path, cwd)
    source, _ = _import_source(tmp_path, cwd)
    footers: list[None] = []
    monkeypatch.setattr(
        NativeToolReplSession,
        "_print_footer",
        lambda *_args, **_kwargs: footers.append(None),
    )
    if failure == "copy":
        monkeypatch.setattr(
            shutil,
            "copyfile",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("copy failed")),
        )
    else:
        monkeypatch.setattr(
            NativeSessionTree,
            "open",
            staticmethod(
                lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("open failed"))
            ),
        )

    with pytest.raises(OSError, match=f"{failure} failed"):
        _run(
            NativeToolReplSession(
                provider=_RecordingProvider(),
                native_session=active,
                settings_manager=_settings(tmp_path, cwd),
                tool_registry={},
            ),
            cwd,
            f"/import {source} --yes\n",
        )

    assert footers == [None]
    assert active.path is not None
    destination = active.path.parent / source.name
    assert destination.exists() is (failure == "open")


def test_uncontrolled_rebuild_failure_retains_import_copy_without_success_footer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd = _workspace(tmp_path)
    active = _active_tree(tmp_path, cwd)
    source, _ = _import_source(tmp_path, cwd)
    footers: list[None] = []
    rebuild_count = 0
    original_rebuild = CodingProductSessionCoordinator.rebuild_active_history

    def rebuild(coordinator: CodingProductSessionCoordinator) -> None:
        nonlocal rebuild_count
        rebuild_count += 1
        if rebuild_count == 2:
            raise RuntimeError("rebuild failed")
        original_rebuild(coordinator)

    monkeypatch.setattr(
        CodingProductSessionCoordinator, "rebuild_active_history", rebuild
    )
    monkeypatch.setattr(
        NativeToolReplSession,
        "_print_footer",
        lambda *_args, **_kwargs: footers.append(None),
    )

    with pytest.raises(RuntimeError, match="rebuild failed"):
        _run(
            NativeToolReplSession(
                provider=_RecordingProvider(),
                native_session=active,
                settings_manager=_settings(tmp_path, cwd),
                tool_registry={},
            ),
            cwd,
            f"/import {source} --yes\n",
        )

    assert active.path is not None
    assert (active.path.parent / source.name).is_file()
    assert footers == [None]


@pytest.mark.parametrize("failure", ["diagnostic", "footer"])
def test_uncontrolled_diagnostic_and_footer_failures_retain_completed_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    cwd = _workspace(tmp_path)
    active = _active_tree(tmp_path, cwd)
    source, _ = _import_source(tmp_path, cwd)
    diagnostic_messages: list[str] = []
    footer_count = 0

    def diagnostic(_terminal_ui: object, _error_stream: TextIO, message: str) -> None:
        diagnostic_messages.append(message)
        if failure == "diagnostic":
            raise RuntimeError("diagnostic failed")

    def footer(
        _session: NativeToolReplSession, _error_stream: TextIO, **_kwargs: object
    ) -> None:
        nonlocal footer_count
        footer_count += 1
        if failure == "footer" and footer_count == 2:
            raise RuntimeError("footer failed")

    monkeypatch.setattr(
        NativeToolReplSession, "_emit_diagnostic", staticmethod(diagnostic)
    )
    monkeypatch.setattr(NativeToolReplSession, "_print_footer", footer)

    with pytest.raises(RuntimeError, match=f"{failure} failed"):
        _run(
            NativeToolReplSession(
                provider=_RecordingProvider(),
                native_session=active,
                settings_manager=_settings(tmp_path, cwd),
                tool_registry={},
            ),
            cwd,
            f"/import {source} --yes\n",
        )

    assert active.path is not None
    assert (active.path.parent / source.name).is_file()
    assert len(diagnostic_messages) == 1
    assert diagnostic_messages[0].startswith("pipy: imported native session")
    assert footer_count == (1 if failure == "diagnostic" else 2)


def test_import_has_no_provider_tool_prompt_history_agent_or_archive_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    cwd = _workspace(tmp_path)
    active = _active_tree(tmp_path, cwd)
    source, source_tree = _import_source(tmp_path, cwd)
    assert source_tree.path is not None
    source_tree_before = source_tree.path.read_bytes()
    source_before = source.read_bytes()
    history = PromptHistoryStore(tmp_path / "prompt-history.json")
    history.set_enabled(True)
    history_before = history.path.read_bytes()
    provider = _RecordingProvider()
    agent_sink = _RecordingAgentSink()

    def reject_input_hook(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("/import must not dispatch extension input hooks")

    monkeypatch.setattr(loop_module, "dispatch_input_hooks", reject_input_hook)
    result, _output, _error = _run(
        NativeToolReplSession(
            provider=provider,
            native_session=active,
            prompt_history_store=history,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
            agent_event_sink=agent_sink,
        ),
        cwd,
        f"/import {source} --yes\n/exit\n",
    )

    assert provider.requests == []
    assert agent_sink.events == []
    assert result.user_turn_count == 0
    assert result.tool_invocation_count == 0
    assert history.entries() == []
    assert history.path.read_bytes() == history_before
    assert source.read_bytes() == source_before
    assert source_tree.path.read_bytes() == source_tree_before


def test_live_import_path_and_transcript_stay_out_of_finalized_metadata_archive(
    tmp_path: Path,
) -> None:
    cwd = _workspace(tmp_path)
    active = _active_tree(tmp_path, cwd)
    source, _ = _import_source(
        tmp_path,
        cwd,
        name=f"{_PATH_MARKER}.payload",
        marker=_TRANSCRIPT_MARKER,
    )
    provider = _RecordingProvider()
    errors = io.StringIO()
    adapter = PipyNativeToolReplAdapter(
        provider=provider,
        tool_registry={},
        input_stream=io.StringIO(f'/import "{source}" --yes\n/exit\n'),
        output_stream=io.StringIO(),
        error_stream=errors,
        native_session=active,
        settings_manager=_settings(tmp_path, cwd),
    )
    result = HarnessRunner(
        adapter=adapter,
        id_factory=lambda: "import-archive-privacy",
    ).run(
        RunRequest(
            agent="pipy-native",
            slug="import-archive-privacy",
            command=[],
            cwd=cwd,
            goal="import archive privacy characterization",
            root=tmp_path / "workflow-archive",
        )
    )

    assert active.path is not None
    imported_copy = active.path.parent / source.name
    assert imported_copy.is_file()
    assert _TRANSCRIPT_MARKER in imported_copy.read_text(encoding="utf-8")
    assert _PATH_MARKER in str(source)
    assert _PATH_MARKER in errors.getvalue()
    assert provider.requests == []
    result_metadata = json.dumps(result.metadata, sort_keys=True)
    result_repr = repr(result)
    assert result.record.markdown_path is not None
    archive_jsonl = result.record.jsonl_path.read_text(encoding="utf-8")
    archive_markdown = result.record.markdown_path.read_text(encoding="utf-8")
    for marker in (_PATH_MARKER, _TRANSCRIPT_MARKER):
        assert marker not in result_metadata
        assert marker not in result_repr
        assert marker not in archive_jsonl
        assert marker not in archive_markdown

    assert result.status is HarnessStatus.SUCCEEDED
    assert result.metadata is not None
    assert result.metadata["adapter"] == "pipy-native"
    assert result.metadata["provider"] == provider.name
    assert result.metadata["model_id"] == provider.model_id
    assert result.metadata["tool_invocation_count"] == 0
    assert result.metadata["user_turn_count"] == 0
    assert ".in-progress" not in result.record.jsonl_path.parts
    assert ".in-progress" not in result.record.markdown_path.parts
    events = _jsonl_objects(result.record.jsonl_path)
    assert events[-1]["type"] == "session.finalized"
    assert events[-1]["run_id"] == result.run_id


def test_uncontrolled_import_failure_is_bounded_in_finalized_metadata_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    cwd = _workspace(tmp_path)
    active = _active_tree(tmp_path, cwd)
    source, _ = _import_source(
        tmp_path,
        cwd,
        name=f"{_PATH_MARKER}.failure-source",
    )
    raw_message = f"permission denied while opening private import path: {source}"

    def fail_import(*_args: object, **_kwargs: object) -> NativeSessionTree:
        raise PermissionError(raw_message)

    monkeypatch.setattr(loop_module, "import_native_session_jsonl", fail_import)
    direct_session = NativeToolReplSession(
        provider=_RecordingProvider(),
        native_session=active,
        settings_manager=_settings(tmp_path, cwd),
        tool_registry={},
    )
    with pytest.raises(PermissionError) as exc_info:
        _run(direct_session, cwd, f'/import "{source}" --yes\n')
    assert str(exc_info.value) == raw_message
    assert str(source) in str(exc_info.value)

    adapter = PipyNativeToolReplAdapter(
        provider=_RecordingProvider(),
        tool_registry={},
        input_stream=io.StringIO(f'/import "{source}" --yes\n'),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
        native_session=active,
        settings_manager=_settings(tmp_path, cwd),
    )
    result = HarnessRunner(
        adapter=adapter,
        id_factory=lambda: "import-private-failure",
    ).run(
        RunRequest(
            agent="pipy-native",
            slug="import-private-failure",
            command=[],
            cwd=cwd,
            goal="import failure archive privacy characterization",
            root=tmp_path / "workflow-archive",
        )
    )

    assert result.status is HarnessStatus.FAILED
    assert result.error_type == "PermissionError"
    assert result.error_message == raw_message
    events = _jsonl_objects(result.record.jsonl_path)
    exception_event = next(
        event for event in events if event["type"] == "harness.run.exception"
    )
    exception_payload = cast(dict[str, object], exception_event["payload"])
    assert exception_payload["error_type"] == "PermissionError"
    assert exception_payload["error_message"] == "PermissionError"
    assert result.record.markdown_path is not None
    archive_jsonl = result.record.jsonl_path.read_text(encoding="utf-8")
    archive_markdown = result.record.markdown_path.read_text(encoding="utf-8")
    assert "- Error type: PermissionError" in archive_markdown
    assert "- Error detail: PermissionError" in archive_markdown
    for private_detail in (_PATH_MARKER, str(source), raw_message):
        assert private_detail not in archive_jsonl
        assert private_detail not in archive_markdown


def test_import_effect_is_owned_by_the_typed_interpreter() -> None:
    source = (
        Path(__file__).parents[1]
        / "src"
        / "pipy_harness"
        / "native"
        / "tool_loop_session.py"
    ).read_text(encoding="utf-8")

    assert 'if command_text == "/import"' not in source
