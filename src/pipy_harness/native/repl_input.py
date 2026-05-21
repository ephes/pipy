"""Input adapters for the native pipy REPL."""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Protocol, TextIO

from pipy_harness.capture import looks_sensitive
from pipy_harness.native.read_only_tool import _is_ignored_or_generated, _is_relative_to

REPL_INPUT_RUNTIME_AUTO = "auto"
REPL_INPUT_RUNTIME_PLAIN = "plain"
REPL_INPUT_RUNTIME_PROMPT_TOOLKIT = "prompt-toolkit"
SUPPORTED_REPL_INPUT_RUNTIMES = (
    REPL_INPUT_RUNTIME_AUTO,
    REPL_INPUT_RUNTIME_PLAIN,
    REPL_INPUT_RUNTIME_PROMPT_TOOLKIT,
)
DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS = (
    "/help",
    "/clear",
    "/status",
    "/login",
    "/logout",
    "/model",
    "/read",
    "/ask-file",
    "/propose-file",
    "/apply-proposal",
    "/verify",
    "/exit",
    "/quit",
)
DEFAULT_REPL_FILE_PATH_COMPLETION_COMMANDS = (
    "/read",
    "/ask-file",
    "/propose-file",
    "/apply-proposal",
)
_FILE_CONTEXT_COMMANDS_WITH_SEPARATOR = frozenset({"/ask-file", "/propose-file"})


class ReplInputUnavailableError(RuntimeError):
    """Raised when an explicitly requested REPL input runtime cannot be used."""


class NativeReplInput(Protocol):
    """Small line-input boundary for the native REPL."""

    runtime_label: str

    def read_line(self, prompt_label: str) -> str:
        """Read one logical input line, returning ``""`` for EOF."""


@dataclass(slots=True)
class PlainNativeReplInput:
    """Plain stdin/stderr REPL input compatible with captured streams."""

    input_stream: TextIO
    error_stream: TextIO
    runtime_label: str = REPL_INPUT_RUNTIME_PLAIN

    def read_line(self, prompt_label: str) -> str:
        print(f"{prompt_label} ", end="", file=self.error_stream, flush=True)
        return self.input_stream.readline()


@dataclass(slots=True)
class PromptToolkitReplCompleter:
    """Prompt-toolkit completions for slash commands and explicit file paths."""

    completion_cls: Any
    command_names: tuple[str, ...] = DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS
    workspace: Path | None = None
    file_path_commands: tuple[str, ...] = DEFAULT_REPL_FILE_PATH_COMPLETION_COMMANDS

    def get_completions(self, document: Any, complete_event: Any) -> Iterable[Any]:
        text_before_cursor = str(getattr(document, "text_before_cursor", ""))
        command_prefix = text_before_cursor.lstrip()
        if not command_prefix.startswith("/"):
            return
        if any(char.isspace() for char in command_prefix):
            yield from self._path_completions(text_before_cursor)
            return
        for command_name in self.command_names:
            if command_name.startswith(command_prefix):
                yield self.completion_cls(
                    command_name,
                    start_position=-len(command_prefix),
                )

    def _path_completions(self, text_before_cursor: str) -> Iterable[Any]:
        if self.workspace is None:
            return
        path_request = _path_completion_request(
            text_before_cursor,
            file_path_commands=self.file_path_commands,
        )
        if path_request is None:
            return
        path_prefix = path_request
        for candidate in _workspace_path_completion_labels(self.workspace, path_prefix):
            yield self.completion_cls(
                candidate,
                start_position=-len(path_prefix),
            )


PromptToolkitSlashCommandCompleter = PromptToolkitReplCompleter


@dataclass(slots=True)
class PromptToolkitNativeReplInput:
    """Prompt-toolkit backed multiline editor for real TTY input."""

    session: Any
    runtime_label: str = REPL_INPUT_RUNTIME_PROMPT_TOOLKIT

    @classmethod
    def create(
        cls,
        *,
        input_stream: TextIO,
        error_stream: TextIO,
        workspace: Path | None = None,
    ) -> "PromptToolkitNativeReplInput":
        if not _prompt_toolkit_streams_supported(input_stream, error_stream):
            raise ReplInputUnavailableError(
                "prompt-toolkit input requires the process stdin and stderr TTY streams"
            )
        (
            prompt_toolkit,
            input_defaults,
            output_defaults,
            completion,
            key_binding,
        ) = _load_prompt_toolkit()

        try:
            session = prompt_toolkit.PromptSession(
                input=input_defaults.create_input(stdin=input_stream),
                output=output_defaults.create_output(stdout=error_stream),
                completer=PromptToolkitReplCompleter(
                    completion.Completion,
                    workspace=workspace,
                ),
                multiline=True,
                prompt_continuation=_prompt_toolkit_multiline_continuation,
                key_bindings=_prompt_toolkit_multiline_key_bindings(key_binding.KeyBindings),
            )
        except Exception as exc:
            raise ReplInputUnavailableError(
                "prompt-toolkit input could not be initialized"
            ) from exc
        return cls(session=session)

    def read_line(self, prompt_label: str) -> str:
        try:
            return f"{self.session.prompt(f'{prompt_label} ')}\n"
        except EOFError:
            return ""


def native_repl_input_for(
    *,
    input_stream: TextIO,
    error_stream: TextIO,
    input_runtime: str = REPL_INPUT_RUNTIME_AUTO,
    workspace: Path | None = None,
) -> NativeReplInput:
    """Choose the REPL input adapter while preserving plain-stream fallback."""

    if input_runtime not in SUPPORTED_REPL_INPUT_RUNTIMES:
        raise ValueError(f"unsupported native REPL input runtime: {input_runtime}")
    if input_runtime == REPL_INPUT_RUNTIME_PLAIN:
        return PlainNativeReplInput(input_stream=input_stream, error_stream=error_stream)
    if input_runtime == REPL_INPUT_RUNTIME_PROMPT_TOOLKIT:
        return PromptToolkitNativeReplInput.create(
            input_stream=input_stream,
            error_stream=error_stream,
            workspace=workspace,
        )

    if _prompt_toolkit_streams_supported(input_stream, error_stream):
        try:
            return PromptToolkitNativeReplInput.create(
                input_stream=input_stream,
                error_stream=error_stream,
                workspace=workspace,
            )
        except Exception:
            pass
    return PlainNativeReplInput(input_stream=input_stream, error_stream=error_stream)


def validate_native_repl_input_runtime(
    *,
    input_stream: TextIO,
    error_stream: TextIO,
    input_runtime: str,
    workspace: Path | None = None,
) -> None:
    """Validate explicit REPL input runtime choices before session creation."""

    if input_runtime not in SUPPORTED_REPL_INPUT_RUNTIMES:
        raise ValueError(f"unsupported native REPL input runtime: {input_runtime}")
    if input_runtime == REPL_INPUT_RUNTIME_PROMPT_TOOLKIT:
        PromptToolkitNativeReplInput.create(
            input_stream=input_stream,
            error_stream=error_stream,
            workspace=workspace,
        )


def _load_prompt_toolkit() -> tuple[Any, Any, Any, Any, Any]:
    try:
        prompt_toolkit = importlib.import_module("prompt_toolkit")
        input_defaults = importlib.import_module("prompt_toolkit.input.defaults")
        output_defaults = importlib.import_module("prompt_toolkit.output.defaults")
        completion = importlib.import_module("prompt_toolkit.completion")
        key_binding = importlib.import_module("prompt_toolkit.key_binding")
    except ImportError as exc:
        raise ReplInputUnavailableError(
            "prompt-toolkit input requires the optional prompt_toolkit package"
        ) from exc
    return prompt_toolkit, input_defaults, output_defaults, completion, key_binding


def _prompt_toolkit_multiline_key_bindings(key_bindings_cls: Any) -> Any:
    key_bindings = key_bindings_cls()

    @key_bindings.add("enter")
    def _submit_input(event: Any) -> None:
        event.current_buffer.validate_and_handle()

    @key_bindings.add("escape", "enter")
    def _insert_newline(event: Any) -> None:
        event.current_buffer.insert_text("\n")

    return key_bindings


def _prompt_toolkit_multiline_continuation(width: int, line_number: int, wrap_count: int) -> str:
    return f"{' ' * max(0, width - 2)}| "


def _prompt_toolkit_streams_supported(input_stream: TextIO, error_stream: TextIO) -> bool:
    return (
        input_stream is sys.stdin
        and error_stream is sys.stderr
        and bool(getattr(input_stream, "isatty", lambda: False)())
        and bool(getattr(error_stream, "isatty", lambda: False)())
    )


def _path_completion_request(
    text_before_cursor: str,
    *,
    file_path_commands: tuple[str, ...],
) -> str | None:
    command_line = text_before_cursor.lstrip()
    for command_name in file_path_commands:
        if command_line == command_name:
            return None
        if not command_line.startswith(f"{command_name} "):
            continue
        argument_text = command_line[len(command_name) :].lstrip()
        if command_name in _FILE_CONTEXT_COMMANDS_WITH_SEPARATOR and _has_file_context_separator(
            argument_text
        ):
            return None
        return argument_text
    return None


def _has_file_context_separator(argument_text: str) -> bool:
    # Stop completing as soon as the user appears to be entering the `--`
    # separator; the command parser remains authoritative for final validity.
    return " --" in argument_text or "-- " in argument_text


def _workspace_path_completion_labels(workspace: Path, path_prefix: str) -> tuple[str, ...]:
    directory_prefix, name_prefix = _path_completion_parts(path_prefix)
    if not _safe_path_completion_prefix(directory_prefix, allow_empty=True):
        return ()
    if not _safe_path_completion_prefix(path_prefix, allow_empty=True):
        return ()

    try:
        workspace_root = workspace.expanduser().resolve()
        directory = (workspace_root / directory_prefix).resolve() if directory_prefix else workspace_root
    except OSError:
        return ()
    if not _is_relative_to(directory, workspace_root):
        return ()
    if not directory.is_dir():
        return ()

    matches: list[str] = []
    try:
        children = sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError:
        return ()
    for child in children:
        if not child.name.startswith(name_prefix):
            continue
        try:
            relative_label = child.relative_to(workspace_root).as_posix()
            resolved_child = child.resolve()
        except (OSError, ValueError):
            continue
        if not _is_relative_to(resolved_child, workspace_root):
            continue
        if not _safe_workspace_completion_label(relative_label):
            continue
        if _is_ignored_or_generated(relative_label, workspace_root):
            continue
        if child.is_dir():
            matches.append(f"{relative_label}/")
        elif child.is_file():
            matches.append(relative_label)
    return tuple(matches)


def _path_completion_parts(path_prefix: str) -> tuple[str, str]:
    if path_prefix.endswith("/"):
        return path_prefix.rstrip("/"), ""
    if "/" not in path_prefix:
        return "", path_prefix
    directory_prefix, name_prefix = path_prefix.rsplit("/", 1)
    return directory_prefix, name_prefix


def _safe_path_completion_prefix(path_prefix: str, *, allow_empty: bool) -> bool:
    if path_prefix == "":
        return allow_empty
    if path_prefix != path_prefix.strip() or "\\" in path_prefix or "\x00" in path_prefix:
        return False
    if any(ord(char) < 32 for char in path_prefix):
        return False
    if any(char in path_prefix for char in ("~", "$", "`", "*", "?", "[", "]", "{", "}", "|", ";", "&", "<", ">")):
        return False
    posix_path = PurePosixPath(path_prefix)
    windows_path = PureWindowsPath(path_prefix)
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        return False
    if path_prefix.startswith("./"):
        return False
    return not any(part in {".."} or looks_sensitive(part) for part in posix_path.parts)


def _safe_workspace_completion_label(relative_label: str) -> bool:
    if not _safe_path_completion_prefix(relative_label, allow_empty=False):
        return False
    parts = PurePosixPath(relative_label).parts
    return bool(parts) and not any(part in {"", "."} for part in parts)
