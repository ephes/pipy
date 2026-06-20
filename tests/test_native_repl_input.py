from __future__ import annotations

import asyncio
import os
import sys
import types
from io import StringIO
from pathlib import Path

import pytest

from pipy_harness.native.repl_input import (
    DEFAULT_REPL_COMMAND_DESCRIPTIONS,
    DEFAULT_REPL_FILE_REFERENCE_COMPLETION_COMMANDS,
    DEFAULT_REPL_FILE_PATH_COMPLETION_COMMANDS,
    DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS,
    REPL_INPUT_RUNTIME_AUTO,
    REPL_INPUT_RUNTIME_PLAIN,
    REPL_INPUT_RUNTIME_PROMPT_TOOLKIT,
    REPL_INPUT_RUNTIME_READLINE,
    REPL_INPUT_RUNTIME_SLASH_MENU,
    PlainNativeReplInput,
    PromptToolkitNativeReplInput,
    PromptToolkitReplCompleter,
    PromptToolkitSlashCommandCompleter,
    ReadlineNativeReplInput,
    ReplInputUnavailableError,
    _prompt_toolkit_multiline_key_bindings,
    _readline_backend_is_libedit,
    _SlashMenuLineEditor,
    native_repl_input_for,
)


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


class FakePromptToolkitOutput:
    def __init__(self) -> None:
        self.enable_cpr = True


def test_proposal_commands_absent_from_completions() -> None:
    for gone in ("/read", "/ask-file", "/propose-file", "/apply-proposal"):
        assert gone not in DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS


def test_plain_repl_input_prints_prompt_to_stderr_and_reads_line() -> None:
    input_stream = StringIO("hello\n")
    error_stream = StringIO()
    repl_input = PlainNativeReplInput(
        input_stream=input_stream, error_stream=error_stream
    )

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
            complete_while_typing,
            multiline,
            prompt_continuation,
            key_bindings,
        ) -> None:
            created["input"] = input
            created["output"] = output
            created["completer"] = completer
            created["complete_while_typing"] = complete_while_typing
            created["multiline"] = multiline
            created["prompt_continuation"] = prompt_continuation
            created["key_bindings"] = key_bindings

        def prompt(self, prompt_label: str, **kwargs) -> str:
            created["prompt_label"] = prompt_label
            existing = created.get("bottom_toolbar_history")
            history: list[object] = (
                list(existing) if isinstance(existing, list) else []
            )
            history.append(kwargs.get("bottom_toolbar"))
            created["bottom_toolbar_history"] = history
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
    monkeypatch.setitem(
        sys.modules, "prompt_toolkit.input.defaults", input_defaults_module
    )
    monkeypatch.setitem(
        sys.modules, "prompt_toolkit.output.defaults", output_defaults_module
    )
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
    assert (
        created["completer"].file_path_commands
        == DEFAULT_REPL_FILE_PATH_COMPLETION_COMMANDS
    )
    assert (
        created["completer"].file_reference_commands
        == DEFAULT_REPL_FILE_REFERENCE_COMPLETION_COMMANDS
    )
    assert created["completer"].workspace == tmp_path
    assert created["multiline"] is True
    assert callable(created["prompt_continuation"])
    assert isinstance(created["key_bindings"], FakeKeyBindings)
    assert {keys for keys, _handler in created["key_bindings"].bindings} == {
        ("enter",),
        ("c-j",),
        ("escape", "enter"),
        ("escape", "c-j"),
    }
    assert (
        repl_input.read_line("pipy-native [fake/model turns:0/8]>") == "edited\ninput\n"
    )
    assert created["input"] == ("input", tty_input)
    assert created["output"] == ("output", tty_error)
    assert created["prompt_label"] == "pipy-native [fake/model turns:0/8]> "


def test_prompt_toolkit_repl_input_disables_cursor_position_requests(
    monkeypatch,
) -> None:
    tty_input = TtyStringIO()
    tty_error = TtyStringIO()
    fake_output = FakePromptToolkitOutput()
    created: dict[str, object] = {}

    class FakePromptSession:
        def __init__(
            self,
            *,
            input,
            output,
            completer,
            complete_while_typing,
            multiline,
            prompt_continuation,
            key_bindings,
        ) -> None:
            created["output"] = output

    prompt_toolkit_module = types.SimpleNamespace(PromptSession=FakePromptSession)
    input_defaults_module = types.SimpleNamespace(
        create_input=lambda *, stdin: ("input", stdin)
    )
    output_defaults_module = types.SimpleNamespace(
        create_output=lambda *, stdout: fake_output
    )
    completion_module = types.SimpleNamespace(Completion=FakeCompletion)
    key_binding_module = types.SimpleNamespace(KeyBindings=FakeKeyBindings)
    monkeypatch.setitem(sys.modules, "prompt_toolkit", prompt_toolkit_module)
    monkeypatch.setitem(
        sys.modules, "prompt_toolkit.input.defaults", input_defaults_module
    )
    monkeypatch.setitem(
        sys.modules, "prompt_toolkit.output.defaults", output_defaults_module
    )
    monkeypatch.setitem(sys.modules, "prompt_toolkit.completion", completion_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.key_binding", key_binding_module)
    monkeypatch.setattr(sys, "stdin", tty_input)
    monkeypatch.setattr(sys, "stderr", tty_error)

    repl_input = native_repl_input_for(
        input_stream=tty_input,
        error_stream=tty_error,
        input_runtime=REPL_INPUT_RUNTIME_PROMPT_TOOLKIT,
    )

    assert isinstance(repl_input, PromptToolkitNativeReplInput)
    assert created["output"] is fake_output
    assert fake_output.enable_cpr is False


def test_auto_repl_input_falls_back_to_readline_when_prompt_toolkit_initialization_fails(
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
    monkeypatch.setitem(
        sys.modules, "prompt_toolkit.input.defaults", input_defaults_module
    )
    monkeypatch.setitem(
        sys.modules, "prompt_toolkit.output.defaults", output_defaults_module
    )
    monkeypatch.setitem(sys.modules, "prompt_toolkit.completion", completion_module)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.key_binding", key_binding_module)
    monkeypatch.setattr(sys, "stdin", tty_input)
    monkeypatch.setattr(sys, "stderr", tty_error)

    repl_input = native_repl_input_for(
        input_stream=tty_input,
        error_stream=tty_error,
        input_runtime=REPL_INPUT_RUNTIME_AUTO,
    )

    assert isinstance(repl_input, ReadlineNativeReplInput)
    assert repl_input.runtime_label == REPL_INPUT_RUNTIME_READLINE


def test_auto_repl_input_falls_back_to_plain_when_readline_streams_unavailable(
    monkeypatch,
) -> None:
    captured_input = StringIO("/exit\n")
    captured_error = StringIO()
    monkeypatch.delitem(sys.modules, "prompt_toolkit", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "prompt_toolkit",
        types.SimpleNamespace(PromptSession=object),
    )

    repl_input = native_repl_input_for(
        input_stream=captured_input,
        error_stream=captured_error,
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
    monkeypatch.setitem(
        sys.modules, "prompt_toolkit.input.defaults", input_defaults_module
    )
    monkeypatch.setitem(
        sys.modules, "prompt_toolkit.output.defaults", output_defaults_module
    )
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
    handlers[("c-j",)](event)
    handlers[("escape", "enter")](event)
    handlers[("escape", "c-j")](event)

    assert buffer.handled is True
    assert buffer.text == "\n\n"


def test_prompt_toolkit_slash_command_completer_suggests_only_leading_commands() -> (
    None
):
    completer = PromptToolkitSlashCommandCompleter(FakeCompletion)

    model_matches = list(completer.get_completions(FakeDocument("/m"), None))
    assert [(match.text, match.start_position) for match in model_matches] == [
        ("/model", -2)
    ]

    all_matches = list(completer.get_completions(FakeDocument("/"), None))
    assert [match.text for match in all_matches] == list(
        DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS
    )
    assert {match.start_position for match in all_matches} == {-1}

    assert list(completer.get_completions(FakeDocument("ordinary /m"), None)) == []
    assert list(completer.get_completions(FakeDocument("/model "), None)) == []


def test_prompt_toolkit_repl_completer_supports_async_completion_protocol() -> None:
    async def collect_matches() -> list[FakeCompletion]:
        completer = PromptToolkitSlashCommandCompleter(FakeCompletion)
        return [
            match
            async for match in completer.get_completions_async(
                FakeDocument("/st"), None
            )
        ]

    matches = asyncio.run(collect_matches())

    assert [(match.text, match.start_position) for match in matches] == [
        ("/status", -3)
    ]


def test_prompt_toolkit_repl_completer_suggests_file_references_in_provider_prompts(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "backlog.md").write_text("safe\n", encoding="utf-8")
    (docs / "harness-spec.md").write_text("safe\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("safe\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("safe\n", encoding="utf-8")
    (tmp_path / "bundle.min.js").write_text("generated\n", encoding="utf-8")
    (tmp_path / "secret_token.py").write_text("sensitive\n", encoding="utf-8")
    completer = PromptToolkitReplCompleter(FakeCompletion, workspace=tmp_path)

    root_matches = list(completer.get_completions(FakeDocument("compare @"), None))
    assert [(match.text, match.start_position) for match in root_matches] == [
        ("@.gitignore", -1),
        ("@README.md", -1),
        ("@docs/", -1),
    ]

    docs_matches = list(
        completer.get_completions(FakeDocument("compare @docs/h"), None)
    )
    assert [(match.text, match.start_position) for match in docs_matches] == [
        ("@docs/harness-spec.md", -7)
    ]


def test_prompt_toolkit_repl_completer_restricts_file_references_to_safe_contexts(
    tmp_path: Path,
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "backlog.md").write_text("safe\n", encoding="utf-8")
    completer = PromptToolkitReplCompleter(FakeCompletion, workspace=tmp_path)

    assert list(completer.get_completions(FakeDocument("/verify @docs/b"), None)) == []
    assert list(completer.get_completions(FakeDocument("compare @docs/b "), None)) == []


@pytest.mark.parametrize(
    "text_before_cursor",
    (
        "compare @/etc/passwd",
        "compare @~/notes",
        "compare @../",
        "compare @docs\\backlog.md",
        "compare @docs/*",
        "compare @secret_token.py",
    ),
)
def test_prompt_toolkit_repl_completer_rejects_unsafe_file_reference_prefixes(
    tmp_path: Path,
    text_before_cursor: str,
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "backlog.md").write_text("safe\n", encoding="utf-8")
    (tmp_path / "secret_token.py").write_text("sensitive\n", encoding="utf-8")
    completer = PromptToolkitReplCompleter(FakeCompletion, workspace=tmp_path)

    assert list(completer.get_completions(FakeDocument(text_before_cursor), None)) == []


def test_prompt_toolkit_repl_completer_skips_path_completion_without_workspace() -> (
    None
):
    completer = PromptToolkitReplCompleter(FakeCompletion)

    command_matches = list(completer.get_completions(FakeDocument("/m"), None))
    assert [(match.text, match.start_position) for match in command_matches] == [
        ("/model", -2)
    ]


class FakeCompletionWithMeta:
    """Completion stub that records the description metadata."""

    def __init__(
        self,
        text: str,
        *,
        start_position: int,
        display_meta: str = "",
    ) -> None:
        self.text = text
        self.start_position = start_position
        self.display_meta = display_meta


def test_repl_completer_offers_full_menu_on_empty_input_with_descriptions() -> None:
    """Empty input must surface the full slash-command menu with descriptions."""

    completer = PromptToolkitReplCompleter(FakeCompletionWithMeta)

    matches = list(completer.get_completions(FakeDocument(""), None))

    assert [match.text for match in matches] == list(
        DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS
    )
    assert {match.start_position for match in matches} == {0}
    for match in matches:
        assert match.display_meta == DEFAULT_REPL_COMMAND_DESCRIPTIONS[match.text]


def test_repl_completer_attaches_descriptions_to_slash_command_completions() -> None:
    completer = PromptToolkitReplCompleter(FakeCompletionWithMeta)

    matches = list(completer.get_completions(FakeDocument("/r"), None))

    assert [match.text for match in matches] == ["/reload"]
    assert matches[0].display_meta == DEFAULT_REPL_COMMAND_DESCRIPTIONS["/reload"]


def test_repl_completer_descriptions_cover_every_default_command() -> None:
    for command_name in DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS:
        description = DEFAULT_REPL_COMMAND_DESCRIPTIONS[command_name]
        assert description, f"missing description for {command_name}"
        assert command_name.strip("/") not in description.lower() or len(description) > 5


def test_readline_repl_input_matches_for_empty_input_returns_all_commands() -> None:
    instance = ReadlineNativeReplInput(error_stream=StringIO())
    matches = instance.matches_for("")

    assert matches == list(DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS)


def test_readline_repl_input_matches_for_slash_prefix_filters_commands() -> None:
    instance = ReadlineNativeReplInput(error_stream=StringIO())
    matches = instance.matches_for("/r")

    assert matches == ["/reload"]


def test_readline_repl_input_matches_for_non_command_text_returns_empty() -> None:
    instance = ReadlineNativeReplInput(error_stream=StringIO())

    assert instance.matches_for("hello") == []


def test_readline_repl_input_completer_returns_matches_sequentially() -> None:
    instance = ReadlineNativeReplInput(error_stream=StringIO())

    first = instance._completer("/e", 0)
    second = instance._completer("/e", 1)

    # /exit is the only match for /e — second call must return None.
    assert first == "/exit"
    assert second is None


def test_readline_repl_input_display_matches_writes_command_descriptions() -> None:
    error_stream = StringIO()
    instance = ReadlineNativeReplInput(error_stream=error_stream)

    instance._display_matches("/", ["/help", "/reload"], longest_match_length=5)

    rendered = error_stream.getvalue()
    assert "/help" in rendered
    assert DEFAULT_REPL_COMMAND_DESCRIPTIONS["/help"] in rendered
    assert "/reload" in rendered
    assert DEFAULT_REPL_COMMAND_DESCRIPTIONS["/reload"] in rendered


def test_readline_native_repl_input_rejects_captured_streams() -> None:
    with pytest.raises(ReplInputUnavailableError, match="TTY streams"):
        ReadlineNativeReplInput.create(
            input_stream=StringIO(),
            error_stream=StringIO(),
        )


@pytest.mark.parametrize(
    ("docstring", "expected_libedit"),
    [
        (
            "Importing this module enables command line editing using libedit readline.",
            True,
        ),
        (
            "GNU readline library; importing this module enables command line editing.",
            False,
        ),
        ("", False),
        (None, False),
    ],
)
def test_readline_backend_libedit_detection(docstring, expected_libedit) -> None:
    module = types.SimpleNamespace(__doc__=docstring)

    assert _readline_backend_is_libedit(module) is expected_libedit


def test_readline_native_repl_input_close_restores_saved_completer() -> None:
    """``close()`` must restore the readline completer the adapter overwrote."""

    captured_calls: dict[str, object] = {}

    def fake_completer(_text: str, _state: int) -> str | None:
        return None

    fake_module = types.SimpleNamespace(
        __doc__="GNU readline",
        get_completer=lambda: fake_completer,
        get_completer_delims=lambda: " \t",
        set_completer=lambda value: captured_calls.__setitem__("completer", value),
        set_completer_delims=lambda value: captured_calls.__setitem__("delims", value),
        parse_and_bind=lambda value: captured_calls.__setitem__("bind", value),
        set_completion_display_matches_hook=lambda value: captured_calls.__setitem__(
            "display_hook", value
        ),
    )
    instance = ReadlineNativeReplInput(error_stream=StringIO())
    instance._readline_module = fake_module
    instance._saved_state = {
        "completer": fake_completer,
        "completer_delims": " \t",
        "display_matches_hook_installed": True,
    }

    instance.close()

    assert captured_calls["completer"] is fake_completer
    assert captured_calls["delims"] == " \t"
    assert captured_calls["display_hook"] is None
    # Calling close again must be a safe no-op.
    captured_calls.clear()
    instance.close()
    assert captured_calls == {}


def test_explicit_readline_runtime_rejects_captured_streams() -> None:
    with pytest.raises(ReplInputUnavailableError, match="TTY streams"):
        native_repl_input_for(
            input_stream=StringIO(),
            error_stream=StringIO(),
            input_runtime=REPL_INPUT_RUNTIME_READLINE,
        )


def test_auto_runtime_falls_back_to_readline_on_real_tty_when_prompt_toolkit_missing(
    monkeypatch,
) -> None:
    tty_input = TtyStringIO()
    tty_error = TtyStringIO()
    monkeypatch.setattr(sys, "stdin", tty_input)
    monkeypatch.setattr(sys, "stderr", tty_error)
    monkeypatch.setitem(sys.modules, "prompt_toolkit", None)

    repl_input = native_repl_input_for(
        input_stream=tty_input,
        error_stream=tty_error,
        input_runtime=REPL_INPUT_RUNTIME_AUTO,
    )

    assert isinstance(repl_input, ReadlineNativeReplInput)
    assert repl_input.runtime_label == REPL_INPUT_RUNTIME_READLINE


def test_prompt_toolkit_read_line_forwards_footer_as_bottom_toolbar(
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
            complete_while_typing,
            multiline,
            prompt_continuation,
            key_bindings,
        ) -> None:
            created["complete_while_typing"] = complete_while_typing

        def prompt(self, prompt_label: str, **kwargs) -> str:
            created["prompt_label"] = prompt_label
            toolbar = kwargs.get("bottom_toolbar")
            created["bottom_toolbar_value"] = toolbar() if callable(toolbar) else toolbar
            return "ok"

    prompt_toolkit_module = types.SimpleNamespace(PromptSession=FakePromptSession)
    input_defaults_module = types.SimpleNamespace(
        create_input=lambda *, stdin: ("input", stdin)
    )
    output_defaults_module = types.SimpleNamespace(
        create_output=lambda *, stdout: FakePromptToolkitOutput()
    )
    completion_module = types.SimpleNamespace(Completion=FakeCompletion)
    key_binding_module = types.SimpleNamespace(KeyBindings=FakeKeyBindings)
    monkeypatch.setitem(sys.modules, "prompt_toolkit", prompt_toolkit_module)
    monkeypatch.setitem(
        sys.modules, "prompt_toolkit.input.defaults", input_defaults_module
    )
    monkeypatch.setitem(
        sys.modules, "prompt_toolkit.output.defaults", output_defaults_module
    )
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

    assert created["complete_while_typing"] is True
    repl_input.read_line(">", footer="cwd\nstatus")

    assert created["bottom_toolbar_value"] == "cwd\nstatus"


# ---------------------- slash-menu input runtime tests ----------------------


def _make_slash_menu_editor(
    *,
    initial_buffer: str = "",
    error_stream=None,
    command_names: tuple[str, ...] | None = None,
) -> _SlashMenuLineEditor:
    """Build an editor without entering raw mode for unit-level state tests."""

    editor = _SlashMenuLineEditor(
        input_stream=StringIO(),
        error_stream=error_stream or StringIO(),
        command_names=command_names or DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS,
        command_descriptions=dict(DEFAULT_REPL_COMMAND_DESCRIPTIONS),
        termios_module=None,
        tty_module=None,
        input_fd=-1,
        prompt_label=">",
        footer=None,
    )
    if initial_buffer:
        for ch in initial_buffer:
            editor._insert_char(ch)
    return editor


def test_slash_menu_runtime_rejects_captured_streams() -> None:
    with pytest.raises(ReplInputUnavailableError, match="TTY"):
        native_repl_input_for(
            input_stream=StringIO("/exit\n"),
            error_stream=StringIO(),
            input_runtime=REPL_INPUT_RUNTIME_SLASH_MENU,
        )


def test_slash_menu_key_decoder_reads_complete_utf8_character() -> None:
    editor = _make_slash_menu_editor()
    read_fd, write_fd = os.pipe()
    os.write(write_fd, "ö".encode("utf-8"))
    os.close(write_fd)
    try:
        editor.input_fd = read_fd

        assert editor._read_key() == "ö"
    finally:
        os.close(read_fd)


def test_slash_menu_timeout_reader_reads_pending_byte_without_fd_activity() -> None:
    editor = _make_slash_menu_editor()
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"\xc3(")
    try:
        editor.input_fd = read_fd

        assert editor._read_key() == "�"
        assert editor._read_byte_with_timeout(0.0) == "("
    finally:
        os.close(write_fd)
        os.close(read_fd)


def test_slash_menu_typing_slash_opens_menu_with_all_commands() -> None:
    editor = _make_slash_menu_editor(initial_buffer="/")
    matches = editor._filtered_commands()

    assert editor._menu_open is True
    assert matches == DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS


def test_slash_menu_typing_filters_to_matching_prefix() -> None:
    editor = _make_slash_menu_editor(initial_buffer="/he")
    matches = editor._filtered_commands()

    assert editor._menu_open is True
    assert matches == ("/help",)


def test_slash_menu_arrow_navigation_wraps_within_filtered_commands() -> None:
    editor = _make_slash_menu_editor(initial_buffer="/")
    editor.error_stream = StringIO()  # suppress render output
    n = len(editor._filtered_commands())

    editor._navigate_menu("down")
    editor._navigate_menu("down")
    assert editor._menu_selection == 2

    editor._navigate_menu("up")
    editor._navigate_menu("up")
    editor._navigate_menu("up")
    assert editor._menu_selection == (2 - 3) % n


def test_slash_menu_accept_replaces_buffer_with_selected_command() -> None:
    editor = _make_slash_menu_editor(initial_buffer="/h")
    matches_before = editor._filtered_commands()
    assert matches_before, "/h must have matches"

    editor._accept_menu_selection()

    assert editor._buffer == matches_before[0]
    assert editor._cursor == len(editor._buffer)
    assert editor._menu_open is False


def test_slash_menu_backspace_leaving_slash_keeps_menu_open() -> None:
    editor = _make_slash_menu_editor(initial_buffer="/help")
    editor._handle_backspace()  # `/hel`
    assert editor._menu_open is True
    editor._handle_backspace()  # `/he`
    editor._handle_backspace()  # `/h`
    editor._handle_backspace()  # `/`
    assert editor._menu_open is True
    editor._handle_backspace()  # ``
    assert editor._menu_open is False


def test_slash_menu_renders_descriptions_below_input() -> None:
    error_stream = StringIO()
    editor = _make_slash_menu_editor(initial_buffer="", error_stream=error_stream)
    editor._buffer = "/"
    editor._cursor = 1
    editor._refresh_menu_state()
    editor._render()

    output = error_stream.getvalue()
    assert "/help" in output
    assert DEFAULT_REPL_COMMAND_DESCRIPTIONS["/help"] in output
    # Selection highlight: the first item is rendered in reverse video.
    assert "\x1b[7m" in output


def test_slash_menu_render_clears_old_menu_before_redrawing() -> None:
    error_stream = StringIO()
    editor = _make_slash_menu_editor(initial_buffer="", error_stream=error_stream)
    editor._buffer = "/"
    editor._cursor = 1
    editor._refresh_menu_state()
    editor._render()
    first_rows = editor._last_drawn_rows
    assert first_rows > 0

    # Now narrow the buffer so fewer matches remain; the redraw must shrink.
    editor._buffer = "/he"
    editor._cursor = 3
    editor._refresh_menu_state()
    editor._render()

    assert editor._last_drawn_rows < first_rows
    # Clear-to-end-of-screen must appear at least once across renders.
    assert "\x1b[J" in error_stream.getvalue()


def test_slash_menu_typing_non_slash_text_keeps_menu_closed() -> None:
    editor = _make_slash_menu_editor(initial_buffer="hello")
    assert editor._menu_open is False
    assert editor._filtered_commands() == ()


def test_slash_menu_runtime_label_constant_is_pinned() -> None:
    assert REPL_INPUT_RUNTIME_SLASH_MENU == "slash-menu"
