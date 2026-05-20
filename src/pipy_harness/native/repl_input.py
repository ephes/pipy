"""Input adapters for the native pipy REPL."""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Protocol, TextIO

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
class PromptToolkitSlashCommandCompleter:
    """Leading-slash command-name completion for prompt-toolkit input."""

    completion_cls: Any
    command_names: tuple[str, ...] = DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS

    def get_completions(self, document: Any, complete_event: Any) -> Iterable[Any]:
        text_before_cursor = str(getattr(document, "text_before_cursor", ""))
        command_prefix = text_before_cursor.lstrip()
        if not command_prefix.startswith("/"):
            return
        if any(char.isspace() for char in command_prefix):
            return
        for command_name in self.command_names:
            if command_name.startswith(command_prefix):
                yield self.completion_cls(
                    command_name,
                    start_position=-len(command_prefix),
                )


@dataclass(slots=True)
class PromptToolkitNativeReplInput:
    """Prompt-toolkit backed line editor, scoped to one-line input."""

    session: Any
    runtime_label: str = REPL_INPUT_RUNTIME_PROMPT_TOOLKIT

    @classmethod
    def create(cls, *, input_stream: TextIO, error_stream: TextIO) -> "PromptToolkitNativeReplInput":
        if not _prompt_toolkit_streams_supported(input_stream, error_stream):
            raise ReplInputUnavailableError(
                "prompt-toolkit input requires the process stdin and stderr TTY streams"
            )
        prompt_toolkit, input_defaults, output_defaults, completion = _load_prompt_toolkit()

        try:
            session = prompt_toolkit.PromptSession(
                input=input_defaults.create_input(stdin=input_stream),
                output=output_defaults.create_output(stdout=error_stream),
                completer=PromptToolkitSlashCommandCompleter(completion.Completion),
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
        )

    if _prompt_toolkit_streams_supported(input_stream, error_stream):
        try:
            return PromptToolkitNativeReplInput.create(
                input_stream=input_stream,
                error_stream=error_stream,
            )
        except Exception:
            pass
    return PlainNativeReplInput(input_stream=input_stream, error_stream=error_stream)


def validate_native_repl_input_runtime(
    *,
    input_stream: TextIO,
    error_stream: TextIO,
    input_runtime: str,
) -> None:
    """Validate explicit REPL input runtime choices before session creation."""

    if input_runtime not in SUPPORTED_REPL_INPUT_RUNTIMES:
        raise ValueError(f"unsupported native REPL input runtime: {input_runtime}")
    if input_runtime == REPL_INPUT_RUNTIME_PROMPT_TOOLKIT:
        PromptToolkitNativeReplInput.create(
            input_stream=input_stream,
            error_stream=error_stream,
        )


def _load_prompt_toolkit() -> tuple[Any, Any, Any, Any]:
    try:
        prompt_toolkit = importlib.import_module("prompt_toolkit")
        input_defaults = importlib.import_module("prompt_toolkit.input.defaults")
        output_defaults = importlib.import_module("prompt_toolkit.output.defaults")
        completion = importlib.import_module("prompt_toolkit.completion")
    except ImportError as exc:
        raise ReplInputUnavailableError(
            "prompt-toolkit input requires the optional prompt_toolkit package"
        ) from exc
    return prompt_toolkit, input_defaults, output_defaults, completion


def _prompt_toolkit_streams_supported(input_stream: TextIO, error_stream: TextIO) -> bool:
    return (
        input_stream is sys.stdin
        and error_stream is sys.stderr
        and bool(getattr(input_stream, "isatty", lambda: False)())
        and bool(getattr(error_stream, "isatty", lambda: False)())
    )
