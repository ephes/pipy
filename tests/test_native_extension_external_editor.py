from __future__ import annotations

import io
import os
import stat
import subprocess
import termios
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from pathlib import Path
from typing import Any, TextIO

import pytest

from pipy_harness.native.ui.components import extension_prompts as _extension_prompts
from pipy_harness.native.ui.components.extension_prompts import (
    ExtensionExternalEditor,
)

extension_prompts: Any = _extension_prompts


def _owner(
    *,
    suspension: Callable[[], AbstractContextManager[None]] | None = None,
    write: Callable[[str], object] | None = None,
    input_stream: TextIO | None = None,
    terminal_stream: TextIO | None = None,
) -> ExtensionExternalEditor:
    return ExtensionExternalEditor(
        external_io_suspension=suspension or nullcontext,
        terminal_write=write or (lambda _text: None),
        input_stream=input_stream or io.StringIO(),
        terminal_stream=terminal_stream or io.StringIO(),
    )


def test_external_editor_command_prefers_visual_and_callback_tracks_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VISUAL", "visual-editor")
    monkeypatch.setenv("EDITOR", "fallback-editor")
    owner = _owner()

    assert owner.command() == "visual-editor"
    callback = owner.callback()
    assert callback is not None
    assert getattr(callback, "__self__", None) is owner

    monkeypatch.delenv("VISUAL")
    assert owner.command() == "fallback-editor"
    monkeypatch.delenv("EDITOR")
    assert owner.command() is None
    assert owner.callback() is None
    assert owner.run_configured("seed") is None


@pytest.mark.parametrize("command", ("'unterminated", "   "))
def test_external_editor_rejects_malformed_or_empty_command_before_tempfile(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.setattr(
        extension_prompts.tempfile,
        "mkstemp",
        lambda **_kwargs: pytest.fail("invalid command must not create a tempfile"),
    )

    assert _owner().run(command, "seed") is None


def test_external_editor_round_trip_preserves_wiring_timing_and_one_newline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    notices: list[str] = []
    input_stream = io.StringIO()
    terminal_stream = io.StringIO()
    launched_path: Path | None = None

    @contextmanager
    def injected_suspension() -> Iterator[None]:
        events.append("suspend-enter")
        try:
            yield
        finally:
            events.append("suspend-exit")

    def write_notice(text: str) -> None:
        events.append("notice")
        notices.append(text)

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal launched_path
        events.append("launch")
        assert events == ["suspend-enter", "notice", "launch"]
        assert argv[:2] == ["fake editor", "--wait"]
        launched_path = Path(argv[-1])
        assert launched_path.read_text(encoding="utf-8") == "seed\n"
        assert stat.S_IMODE(launched_path.stat().st_mode) == 0o600
        assert kwargs == {
            "stdin": input_stream,
            "stdout": terminal_stream,
            "stderr": terminal_stream,
            "check": False,
        }
        launched_path.write_text("edited\n\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(extension_prompts.subprocess, "run", fake_run)
    owner = _owner(
        suspension=injected_suspension,
        write=write_notice,
        input_stream=input_stream,
        terminal_stream=terminal_stream,
    )

    assert owner._external_io_suspension is injected_suspension
    assert owner.run("'fake editor' --wait", "seed\n") == "edited\n"
    assert events == ["suspend-enter", "notice", "launch", "suspend-exit"]
    assert notices == [
        "Launching external editor: 'fake editor' --wait\n"
        "Pipy will resume when the editor exits.\n"
    ]
    assert launched_path is not None
    assert not launched_path.exists()


def test_external_editor_mkstemp_failure_does_not_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched = False

    def fail_mkstemp(**_kwargs: object) -> tuple[int, str]:
        raise OSError("mkstemp failed")

    def fake_run(*_args: object, **_kwargs: object) -> object:
        nonlocal launched
        launched = True
        return object()

    monkeypatch.setattr(extension_prompts.tempfile, "mkstemp", fail_mkstemp)
    monkeypatch.setattr(extension_prompts.subprocess, "run", fake_run)

    assert _owner().run("fake-editor", "seed") is None
    assert launched is False


def test_external_editor_write_failure_closes_and_unlinks_tempfile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "editor.md"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    launched = False

    class FailingWriter:
        def __enter__(self) -> FailingWriter:
            return self

        def write(self, text: str) -> None:
            assert text == "seed"
            raise OSError("write failed")

        def __exit__(self, *_args: object) -> None:
            os.close(fd)

    monkeypatch.setattr(
        extension_prompts.tempfile, "mkstemp", lambda **_kwargs: (fd, str(path))
    )
    monkeypatch.setattr(
        extension_prompts.os, "fdopen", lambda *_args, **_kwargs: FailingWriter()
    )

    def fake_run(*_args: object, **_kwargs: object) -> object:
        nonlocal launched
        launched = True
        return object()

    monkeypatch.setattr(extension_prompts.subprocess, "run", fake_run)

    assert _owner().run("fake-editor", "seed") is None
    assert launched is False
    assert not path.exists()


def test_external_editor_chmod_failure_is_tolerated_and_cleaned_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path: Path | None = None

    def fail_chmod(_fd: int, mode: int) -> None:
        assert mode == 0o600
        raise OSError("chmod failed")

    def fake_run(
        argv: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal path
        path = Path(argv[-1])
        path.write_text("edited\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(extension_prompts.os, "fchmod", fail_chmod)
    monkeypatch.setattr(extension_prompts.subprocess, "run", fake_run)

    assert _owner().run("fake-editor", "seed") == "edited"
    assert path is not None
    assert not path.exists()


@pytest.mark.parametrize(
    "error_type", (OSError, termios.error, ValueError), ids=("os", "termios", "value")
)
def test_external_editor_failed_cooked_handoff_never_launches(
    monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    launched = False
    notices: list[str] = []

    class FailedSuspension:
        def __enter__(self) -> None:
            raise error_type("cooked handoff failed")

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_run(*_args: object, **_kwargs: object) -> object:
        nonlocal launched
        launched = True
        return object()

    monkeypatch.setattr(extension_prompts.subprocess, "run", fake_run)
    owner = _owner(
        suspension=lambda: FailedSuspension(),
        write=lambda text: notices.append(text),
    )

    assert owner.run("fake-editor", "seed") is None
    assert launched is False
    assert notices == []


@pytest.mark.parametrize("failure", ("return-code", "launch-error"))
def test_external_editor_subprocess_failure_discards_edit_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    path: Path | None = None

    def fake_run(
        argv: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal path
        path = Path(argv[-1])
        path.write_text("must not load\n", encoding="utf-8")
        if failure == "launch-error":
            raise OSError("launch failed")
        return subprocess.CompletedProcess(argv, 7)

    monkeypatch.setattr(extension_prompts.subprocess, "run", fake_run)

    assert _owner().run("fake-editor", "seed") is None
    assert path is not None
    assert not path.exists()


def test_external_editor_keeps_successful_read_when_raw_resume_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextmanager
    def failed_resume() -> Iterator[None]:
        try:
            yield
        finally:
            raise OSError("raw resume failed")

    def fake_run(
        argv: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        Path(argv[-1]).write_text("completed edit\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(extension_prompts.subprocess, "run", fake_run)

    assert (
        _owner(suspension=failed_resume).run("fake-editor", "seed") == "completed edit"
    )


@pytest.mark.parametrize("failure", ("read", "unicode"))
def test_external_editor_read_failure_discards_edit_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    path: Path | None = None

    def fake_run(
        argv: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal path
        path = Path(argv[-1])
        if failure == "read":
            path.unlink()
        else:
            path.write_bytes(b"\xff")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(extension_prompts.subprocess, "run", fake_run)

    assert _owner().run("fake-editor", "seed") is None
    assert path is not None
    assert not path.exists()


@pytest.mark.parametrize("error_type", (KeyboardInterrupt, SystemExit))
def test_external_editor_control_flow_exceptions_propagate_after_cleanup(
    monkeypatch: pytest.MonkeyPatch, error_type: type[BaseException]
) -> None:
    path: Path | None = None

    def fake_run(argv: list[str], **_kwargs: object) -> object:
        nonlocal path
        path = Path(argv[-1])
        raise error_type()

    monkeypatch.setattr(extension_prompts.subprocess, "run", fake_run)

    with pytest.raises(error_type):
        _owner().run("fake-editor", "seed")
    assert path is not None
    assert not path.exists()
