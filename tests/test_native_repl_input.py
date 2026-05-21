from __future__ import annotations

import sys
import types
from io import StringIO
from pathlib import Path

import pytest

from pipy_harness.native.repl_input import (
    DEFAULT_REPL_FILE_PATH_COMPLETION_COMMANDS,
    DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS,
    REPL_INPUT_RUNTIME_AUTO,
    REPL_INPUT_RUNTIME_PLAIN,
    REPL_INPUT_RUNTIME_PROMPT_TOOLKIT,
    PlainNativeReplInput,
    PromptToolkitNativeReplInput,
    PromptToolkitReplCompleter,
    PromptToolkitSlashCommandCompleter,
    ReplInputUnavailableError,
    _prompt_toolkit_multiline_key_bindings,
    native_repl_input_for,
)
from pipy_harness.native.session import _REPL_COMMAND_GROUPS


class TtyStringIO(StringIO):
    def isatty(self) -> bool:
        return True


class FakeCompletion:
    def __init__(self, text: str, *, start_position: int) -> None:
        self.text = text
        self.start_position = start_position


class FakeDocument:
    def __init__(self, text_before_cursor: str) -> None:
        self.text_before_cursor = text_before_cursor


class FakeKeyBindings:
    def __init__(self) -> None:
        self.bindings: list[tuple[tuple[str, ...], object]] = []

    def add(self, *keys: str):
        def decorator(func):
            self.bindings.append((keys, func))
            return func

        return decorator


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
    monkeypatch, tmp_path
) -> None:
    tty_input = TtyStringIO()
    tty_error = TtyStringIO()
    created: dict[str, object] = {}

    class FakePromptSession:
        def __init__(
            self,
            *,
            input,
            output,
            completer,
            multiline,
            prompt_continuation,
            key_bindings,
        ) -> None:
            created["input"] = input
            created["output"] = output
            created["completer"] = completer
            created["multiline"] = multiline
            created["prompt_continuation"] = prompt_continuation
            created["key_bindings"] = key_bindings

        def prompt(self, prompt_label: str) -> str:
            created["prompt_label"] = prompt_label
            return "edited\ninput"

    prompt_toolkit_module = types.SimpleNamespace(PromptSession=FakePromptSession)
    input_defaults_module = types.SimpleNamespace(
        create_input=lambda *, stdin: ("input", stdin)
    )
    output_defaults_module = types.SimpleNamespace(
        create_output=lambda *, stdout: ("output", stdout if stdout.isatty() else None)
    )
    completion_module = types.SimpleNamespace(Completion=FakeCompletion)
    key_binding_module = types.SimpleNamespace(KeyBindings=FakeKeyBindings)
    monkeypatch.setitem(sys.modules, "prompt_toolkit", prompt_toolkit_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.input.defaults", input_defaults_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.output.defaults", output_defaults_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.completion", completion_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.key_binding", key_binding_module)
    monkeypatch.setattr(sys, "stdin", tty_input)
    monkeypatch.setattr(sys, "stderr", tty_error)

    repl_input = native_repl_input_for(
        input_stream=tty_input,
        error_stream=tty_error,
        input_runtime=REPL_INPUT_RUNTIME_PROMPT_TOOLKIT,
        workspace=tmp_path,
    )

    assert isinstance(repl_input, PromptToolkitNativeReplInput)
    assert repl_input.runtime_label == REPL_INPUT_RUNTIME_PROMPT_TOOLKIT
    assert isinstance(created["completer"], PromptToolkitReplCompleter)
    assert created["completer"].command_names == DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS
    assert created["completer"].file_path_commands == DEFAULT_REPL_FILE_PATH_COMPLETION_COMMANDS
    assert created["completer"].workspace == tmp_path
    assert created["multiline"] is True
    assert callable(created["prompt_continuation"])
    assert isinstance(created["key_bindings"], FakeKeyBindings)
    assert {keys for keys, _handler in created["key_bindings"].bindings} == {
        ("enter",),
        ("escape", "enter"),
    }
    assert repl_input.read_line("pipy-native [fake/model turns:0/8]>") == "edited\ninput\n"
    assert created["input"] == ("input", tty_input)
    assert created["output"] == ("output", tty_error)
    assert created["prompt_label"] == "pipy-native [fake/model turns:0/8]> "


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
    completion_module = types.SimpleNamespace(Completion=object)

    def fail_create_output(*, stdout):
        raise RuntimeError("terminal output unavailable")

    output_defaults_module = types.SimpleNamespace(create_output=fail_create_output)
    key_binding_module = types.SimpleNamespace(KeyBindings=FakeKeyBindings)
    monkeypatch.setitem(sys.modules, "prompt_toolkit", prompt_toolkit_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.input.defaults", input_defaults_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.output.defaults", output_defaults_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.completion", completion_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.key_binding", key_binding_module)
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
    completion_module = types.SimpleNamespace(Completion=object)
    key_binding_module = types.SimpleNamespace(KeyBindings=FakeKeyBindings)
    monkeypatch.setitem(sys.modules, "prompt_toolkit", prompt_toolkit_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.input.defaults", input_defaults_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.output.defaults", output_defaults_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.completion", completion_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.key_binding", key_binding_module)
    monkeypatch.setattr(sys, "stdin", tty_input)
    monkeypatch.setattr(sys, "stderr", tty_error)

    with pytest.raises(ReplInputUnavailableError, match="could not be initialized"):
        native_repl_input_for(
            input_stream=tty_input,
            error_stream=tty_error,
            input_runtime=REPL_INPUT_RUNTIME_PROMPT_TOOLKIT,
        )


def test_prompt_toolkit_multiline_key_bindings_submit_and_insert_newline() -> None:
    key_bindings = _prompt_toolkit_multiline_key_bindings(FakeKeyBindings)
    handlers = {keys: handler for keys, handler in key_bindings.bindings}

    class FakeBuffer:
        def __init__(self) -> None:
            self.handled = False
            self.text = ""

        def validate_and_handle(self) -> None:
            self.handled = True

        def insert_text(self, text: str) -> None:
            self.text += text

    buffer = FakeBuffer()
    event = types.SimpleNamespace(current_buffer=buffer)

    handlers[("enter",)](event)
    handlers[("escape", "enter")](event)

    assert buffer.handled is True
    assert buffer.text == "\n"


def test_prompt_toolkit_slash_command_completer_suggests_only_leading_commands() -> None:
    completer = PromptToolkitSlashCommandCompleter(FakeCompletion)

    model_matches = list(completer.get_completions(FakeDocument("/m"), None))
    assert [(match.text, match.start_position) for match in model_matches] == [
        ("/model", -2)
    ]

    all_matches = list(completer.get_completions(FakeDocument("/"), None))
    assert [match.text for match in all_matches] == list(DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS)
    assert {match.start_position for match in all_matches} == {-1}

    assert list(completer.get_completions(FakeDocument("ordinary /m"), None)) == []
    assert list(completer.get_completions(FakeDocument("/model "), None)) == []


def test_prompt_toolkit_repl_completer_suggests_workspace_paths_for_file_commands(
    tmp_path,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "backlog.md").write_text("safe\n", encoding="utf-8")
    (docs / "harness-spec.md").write_text("safe\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / ".gitignore").write_text("ignored.txt\nignored-dir/\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("safe\n", encoding="utf-8")
    ignored_dir = tmp_path / "ignored-dir"
    ignored_dir.mkdir()
    (ignored_dir / "safe.txt").write_text("safe\n", encoding="utf-8")
    (tmp_path / "bundle.min.js").write_text("generated\n", encoding="utf-8")
    (tmp_path / "secret_token.py").write_text("sensitive\n", encoding="utf-8")

    completer = PromptToolkitReplCompleter(FakeCompletion, workspace=tmp_path)

    root_matches = list(completer.get_completions(FakeDocument("/read "), None))
    assert [(match.text, match.start_position) for match in root_matches] == [
        (".gitignore", 0),
        ("docs/", 0),
        ("src/", 0),
    ]

    docs_matches = list(completer.get_completions(FakeDocument("/read docs/h"), None))
    assert [(match.text, match.start_position) for match in docs_matches] == [
        ("docs/harness-spec.md", -6)
    ]


def test_prompt_toolkit_repl_completer_limits_path_completion_to_path_argument(
    tmp_path,
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "backlog.md").write_text("safe\n", encoding="utf-8")
    completer = PromptToolkitReplCompleter(FakeCompletion, workspace=tmp_path)

    apply_matches = list(
        completer.get_completions(FakeDocument("/apply-proposal docs/b"), None)
    )
    assert [(match.text, match.start_position) for match in apply_matches] == [
        ("docs/backlog.md", -6)
    ]

    assert (
        list(
            completer.get_completions(
                FakeDocument("/ask-file docs/b -- what changed?"),
                None,
            )
        )
        == []
    )
    assert list(completer.get_completions(FakeDocument("read docs/b"), None)) == []
    assert list(completer.get_completions(FakeDocument("/verify just"), None)) == []


@pytest.mark.parametrize(
    "text_before_cursor",
    (
        "/read /etc/passwd",
        "/read ~/notes",
        "/read ../",
        "/read docs\\backlog.md",
        "/read docs/*",
    ),
)
def test_prompt_toolkit_repl_completer_rejects_unsafe_path_prefixes(
    tmp_path: Path,
    text_before_cursor: str,
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "backlog.md").write_text("safe\n", encoding="utf-8")
    completer = PromptToolkitReplCompleter(FakeCompletion, workspace=tmp_path)

    assert list(completer.get_completions(FakeDocument(text_before_cursor), None)) == []


def test_prompt_toolkit_repl_completer_lists_trailing_slash_directory_contents(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "backlog.md").write_text("safe\n", encoding="utf-8")
    (docs / "session-storage.md").write_text("safe\n", encoding="utf-8")
    completer = PromptToolkitReplCompleter(FakeCompletion, workspace=tmp_path)

    matches = list(completer.get_completions(FakeDocument("/read docs/"), None))

    assert [(match.text, match.start_position) for match in matches] == [
        ("docs/backlog.md", -5),
        ("docs/session-storage.md", -5),
    ]


def test_prompt_toolkit_repl_completer_skips_symlinks_outside_workspace(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("safe\n", encoding="utf-8")
    (tmp_path / "outside-link.txt").symlink_to(outside)
    (tmp_path / "visible.txt").write_text("safe\n", encoding="utf-8")
    completer = PromptToolkitReplCompleter(FakeCompletion, workspace=tmp_path)

    matches = list(completer.get_completions(FakeDocument("/read "), None))

    assert [(match.text, match.start_position) for match in matches] == [
        ("visible.txt", 0)
    ]


def test_prompt_toolkit_repl_completer_skips_path_completion_without_workspace() -> None:
    completer = PromptToolkitReplCompleter(FakeCompletion)

    command_matches = list(completer.get_completions(FakeDocument("/m"), None))
    assert [(match.text, match.start_position) for match in command_matches] == [
        ("/model", -2)
    ]
    assert list(completer.get_completions(FakeDocument("/read docs/b"), None)) == []


def test_prompt_toolkit_slash_command_completions_match_repl_help_commands() -> None:
    expected_commands = tuple(
        usage.split(maxsplit=1)[0]
        for group in _REPL_COMMAND_GROUPS
        for usage in group.usages
    )

    assert DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS == expected_commands
