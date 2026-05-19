from __future__ import annotations

import sys
import types
from io import StringIO

import pytest

from pipy_harness.native.repl_input import (
    REPL_INPUT_RUNTIME_AUTO,
    REPL_INPUT_RUNTIME_PLAIN,
    REPL_INPUT_RUNTIME_PROMPT_TOOLKIT,
    PlainNativeReplInput,
    PromptToolkitNativeReplInput,
    ReplInputUnavailableError,
    native_repl_input_for,
)


class TtyStringIO(StringIO):
    def isatty(self) -> bool:
        return True


def test_plain_repl_input_prints_prompt_to_stderr_and_reads_line() -> None:
    input_stream = StringIO("hello\n")
    error_stream = StringIO()
    repl_input = PlainNativeReplInput(input_stream=input_stream, error_stream=error_stream)

    assert repl_input.read_line("pipy-native [fake/model turns:0/8]>") == "hello\n"
    assert error_stream.getvalue() == "pipy-native [fake/model turns:0/8]> "


def test_auto_repl_input_uses_plain_for_captured_streams() -> None:
    repl_input = native_repl_input_for(
        input_stream=StringIO("/exit\n"),
        error_stream=StringIO(),
        input_runtime=REPL_INPUT_RUNTIME_AUTO,
    )

    assert isinstance(repl_input, PlainNativeReplInput)
    assert repl_input.runtime_label == REPL_INPUT_RUNTIME_PLAIN


def test_explicit_prompt_toolkit_repl_input_rejects_captured_streams() -> None:
    with pytest.raises(ReplInputUnavailableError, match="TTY streams"):
        native_repl_input_for(
            input_stream=StringIO("/exit\n"),
            error_stream=StringIO(),
            input_runtime=REPL_INPUT_RUNTIME_PROMPT_TOOLKIT,
        )


def test_prompt_toolkit_repl_input_uses_optional_line_editor_when_available(
    monkeypatch,
) -> None:
    tty_input = TtyStringIO()
    tty_error = TtyStringIO()
    created: dict[str, object] = {}

    class FakePromptSession:
        def __init__(self, *, input, output) -> None:
            created["input"] = input
            created["output"] = output

        def prompt(self, prompt_label: str) -> str:
            created["prompt_label"] = prompt_label
            return "edited input"

    prompt_toolkit_module = types.SimpleNamespace(PromptSession=FakePromptSession)
    input_defaults_module = types.SimpleNamespace(
        create_input=lambda *, stdin: ("input", stdin)
    )
    output_defaults_module = types.SimpleNamespace(
        create_output=lambda *, stdout: ("output", stdout if stdout.isatty() else None)
    )
    monkeypatch.setitem(sys.modules, "prompt_toolkit", prompt_toolkit_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.input.defaults", input_defaults_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.output.defaults", output_defaults_module)
    monkeypatch.setattr(sys, "stdin", tty_input)
    monkeypatch.setattr(sys, "stderr", tty_error)

    repl_input = native_repl_input_for(
        input_stream=tty_input,
        error_stream=tty_error,
        input_runtime=REPL_INPUT_RUNTIME_PROMPT_TOOLKIT,
    )

    assert isinstance(repl_input, PromptToolkitNativeReplInput)
    assert repl_input.runtime_label == REPL_INPUT_RUNTIME_PROMPT_TOOLKIT
    assert repl_input.read_line("pipy-native [fake/model turns:0/8]>") == "edited input\n"
    assert created == {
        "input": ("input", tty_input),
        "output": ("output", tty_error),
        "prompt_label": "pipy-native [fake/model turns:0/8]> ",
    }


def test_auto_repl_input_falls_back_to_plain_when_prompt_toolkit_initialization_fails(
    monkeypatch,
) -> None:
    tty_input = TtyStringIO("/exit\n")
    tty_error = TtyStringIO()

    class FakePromptSession:
        def __init__(self, *, input, output) -> None:
            raise AssertionError("session construction should not be reached")

    prompt_toolkit_module = types.SimpleNamespace(PromptSession=FakePromptSession)
    input_defaults_module = types.SimpleNamespace(
        create_input=lambda *, stdin: ("input", stdin)
    )

    def fail_create_output(*, stdout):
        raise RuntimeError("terminal output unavailable")

    output_defaults_module = types.SimpleNamespace(create_output=fail_create_output)
    monkeypatch.setitem(sys.modules, "prompt_toolkit", prompt_toolkit_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.input.defaults", input_defaults_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.output.defaults", output_defaults_module)
    monkeypatch.setattr(sys, "stdin", tty_input)
    monkeypatch.setattr(sys, "stderr", tty_error)

    repl_input = native_repl_input_for(
        input_stream=tty_input,
        error_stream=tty_error,
        input_runtime=REPL_INPUT_RUNTIME_AUTO,
    )

    assert isinstance(repl_input, PlainNativeReplInput)
    assert repl_input.runtime_label == REPL_INPUT_RUNTIME_PLAIN


def test_explicit_prompt_toolkit_wraps_initialization_failure(monkeypatch) -> None:
    tty_input = TtyStringIO()
    tty_error = TtyStringIO()
    prompt_toolkit_module = types.SimpleNamespace(PromptSession=object)
    input_defaults_module = types.SimpleNamespace(
        create_input=lambda *, stdin: ("input", stdin)
    )
    output_defaults_module = types.SimpleNamespace(
        create_output=lambda *, stdout: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    monkeypatch.setitem(sys.modules, "prompt_toolkit", prompt_toolkit_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.input.defaults", input_defaults_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.output.defaults", output_defaults_module)
    monkeypatch.setattr(sys, "stdin", tty_input)
    monkeypatch.setattr(sys, "stderr", tty_error)

    with pytest.raises(ReplInputUnavailableError, match="could not be initialized"):
        native_repl_input_for(
            input_stream=tty_input,
            error_stream=tty_error,
            input_runtime=REPL_INPUT_RUNTIME_PROMPT_TOOLKIT,
        )
