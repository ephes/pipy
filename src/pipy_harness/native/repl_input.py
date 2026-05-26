"""Input adapters for the native pipy REPL."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, AsyncIterator, Iterable, Protocol, TextIO

from pipy_harness.capture import looks_sensitive
from pipy_harness.native.read_only_tool import _is_ignored_or_generated, _is_relative_to

REPL_INPUT_RUNTIME_AUTO = "auto"
REPL_INPUT_RUNTIME_PLAIN = "plain"
REPL_INPUT_RUNTIME_PROMPT_TOOLKIT = "prompt-toolkit"
REPL_INPUT_RUNTIME_READLINE = "readline"
SUPPORTED_REPL_INPUT_RUNTIMES = (
    REPL_INPUT_RUNTIME_AUTO,
    REPL_INPUT_RUNTIME_PLAIN,
    REPL_INPUT_RUNTIME_PROMPT_TOOLKIT,
    REPL_INPUT_RUNTIME_READLINE,
)
DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS = (
    "/help",
    "/clear",
    "/status",
    "/settings",
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
DEFAULT_REPL_COMMAND_DESCRIPTIONS: dict[str, str] = {
    "/help": "Show pipy command reference",
    "/clear": "Clear local conversation context",
    "/status": "Show REPL state (read-only)",
    "/settings": "Show provider settings (read-only)",
    "/login": "Log in (openai-codex OAuth)",
    "/logout": "Log out (openai-codex OAuth)",
    "/model": "Select provider/model",
    "/read": "Read a workspace file excerpt",
    "/ask-file": "Ask provider about a file (read-only)",
    "/propose-file": "Propose a patch for a file",
    "/apply-proposal": "Apply the pending proposal",
    "/verify": "Run a pre-approved verification",
    "/exit": "Exit the REPL",
    "/quit": "Exit the REPL (alias)",
}
DEFAULT_REPL_FILE_PATH_COMPLETION_COMMANDS = (
    "/read",
    "/ask-file",
    "/propose-file",
    "/apply-proposal",
)
_FILE_CONTEXT_COMMANDS_WITH_SEPARATOR = frozenset({"/ask-file", "/propose-file"})
DEFAULT_REPL_FILE_REFERENCE_COMPLETION_COMMANDS = tuple(
    sorted(_FILE_CONTEXT_COMMANDS_WITH_SEPARATOR)
)


class ReplInputUnavailableError(RuntimeError):
    """Raised when an explicitly requested REPL input runtime cannot be used."""


class NativeReplInput(Protocol):
    """Small line-input boundary for the native REPL."""

    runtime_label: str

    def read_line(self, prompt_label: str, *, footer: str | None = None) -> str:
        """Read one logical input line, returning ``""`` for EOF.

        ``footer`` is rendered alongside the input as a status strip
        (bottom toolbar for the prompt-toolkit runtime, printed after
        the input line for the plain runtime).
        """

    def close(self) -> None:
        """Release any process-global resources the adapter owns.

        Default: no-op. Implementations that mutate process-global state
        (e.g. the stdlib readline hooks) must restore it here so the
        embedding process keeps its prior configuration.
        """


@dataclass(slots=True)
class PlainNativeReplInput:
    """Plain stdin/stderr REPL input compatible with captured streams."""

    input_stream: TextIO
    error_stream: TextIO
    runtime_label: str = REPL_INPUT_RUNTIME_PLAIN

    def read_line(self, prompt_label: str, *, footer: str | None = None) -> str:
        del footer  # the plain runtime emits the footer through the session loop instead
        print(f"{prompt_label} ", end="", file=self.error_stream, flush=True)
        return self.input_stream.readline()

    def close(self) -> None:
        return None


@dataclass(slots=True)
class PromptToolkitReplCompleter:
    """Prompt-toolkit completions for slash commands and workspace path labels."""

    completion_cls: Any
    command_names: tuple[str, ...] = DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS
    workspace: Path | None = None
    file_path_commands: tuple[str, ...] = DEFAULT_REPL_FILE_PATH_COMPLETION_COMMANDS
    file_reference_commands: tuple[str, ...] = (
        DEFAULT_REPL_FILE_REFERENCE_COMPLETION_COMMANDS
    )
    command_descriptions: Mapping[str, str] = field(
        default_factory=lambda: dict(DEFAULT_REPL_COMMAND_DESCRIPTIONS)
    )

    def get_completions(self, document: Any, complete_event: Any) -> Iterable[Any]:
        text_before_cursor = str(getattr(document, "text_before_cursor", ""))
        command_prefix = text_before_cursor.lstrip()
        if command_prefix == "" and text_before_cursor == "":
            for command_name in self.command_names:
                yield self._command_completion(command_name, start_position=0)
            return
        if not command_prefix.startswith("/"):
            yield from self._file_reference_completions(text_before_cursor)
            return
        if any(char.isspace() for char in command_prefix):
            path_request = _path_completion_request(
                text_before_cursor,
                file_path_commands=self.file_path_commands,
            )
            if path_request is not None:
                yield from self._path_completions(path_request)
                return
            yield from self._file_reference_completions(text_before_cursor)
            return

        for command_name in self.command_names:
            if command_name.startswith(command_prefix):
                yield self._command_completion(
                    command_name,
                    start_position=-len(command_prefix),
                )

    def _command_completion(self, command_name: str, *, start_position: int) -> Any:
        description = self.command_descriptions.get(command_name, "")
        try:
            return self.completion_cls(
                command_name,
                start_position=start_position,
                display_meta=description,
            )
        except TypeError:
            return self.completion_cls(
                command_name,
                start_position=start_position,
            )

    def _path_completions(self, path_prefix: str) -> Iterable[Any]:
        if self.workspace is None:
            return
        for candidate in _workspace_path_completion_labels(self.workspace, path_prefix):
            yield self.completion_cls(
                candidate,
                start_position=-len(path_prefix),
            )

    def _file_reference_completions(self, text_before_cursor: str) -> Iterable[Any]:
        if self.workspace is None:
            return
        path_prefix = _file_reference_completion_request(
            text_before_cursor,
            file_reference_commands=self.file_reference_commands,
        )
        if path_prefix is None:
            return
        for candidate in _workspace_path_completion_labels(self.workspace, path_prefix):
            yield self.completion_cls(
                f"@{candidate}",
                start_position=-(len(path_prefix) + 1),
            )

    async def get_completions_async(
        self, document: Any, complete_event: Any
    ) -> AsyncIterator[Any]:
        for completion in self.get_completions(document, complete_event):
            yield completion


PromptToolkitSlashCommandCompleter = PromptToolkitReplCompleter


@dataclass(slots=True)
class ReadlineNativeReplInput:
    """Stdlib-readline REPL input enabling Tab discovery without runtime deps.

    Used when prompt-toolkit is unavailable but stdin/stderr are real TTYs.
    Provides empty-input Tab to surface the full slash-command menu and
    `/`-prefix completion with description metadata in the matches display.

    The readline module exposes a single set of process-global hooks
    (completer, completer delims, display-matches hook), so this adapter
    captures the previous values on creation and restores them when the
    REPL exits via :py:meth:`restore_global_state`. The session loop
    keeps a single adapter instance per REPL run, so ``_current_matches``
    is private per instance and not shared across REPLs.
    """

    error_stream: TextIO
    command_names: tuple[str, ...] = DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS
    command_descriptions: Mapping[str, str] = field(
        default_factory=lambda: dict(DEFAULT_REPL_COMMAND_DESCRIPTIONS)
    )
    runtime_label: str = REPL_INPUT_RUNTIME_READLINE
    _current_matches: list[str] = field(default_factory=list)
    _saved_state: dict[str, Any] | None = None
    _readline_module: Any | None = None

    @classmethod
    def create(
        cls,
        *,
        input_stream: TextIO,
        error_stream: TextIO,
        workspace: Path | None = None,
    ) -> "ReadlineNativeReplInput":
        del workspace
        if not _readline_streams_supported(input_stream, error_stream):
            raise ReplInputUnavailableError(
                "readline input requires the process stdin and stderr TTY streams"
            )
        try:
            readline = importlib.import_module("readline")
        except ImportError as exc:
            raise ReplInputUnavailableError(
                "readline input requires the readline stdlib module"
            ) from exc
        instance = cls(error_stream=error_stream)
        instance._readline_module = readline
        instance._saved_state = {
            "completer": readline.get_completer(),
            "completer_delims": readline.get_completer_delims(),
        }
        try:
            if _readline_backend_is_libedit(readline):
                readline.parse_and_bind("bind ^I rl_complete")
            else:
                readline.parse_and_bind("tab: complete")
            readline.set_completer_delims(" \t\n")
            readline.set_completer(instance._completer)
        except Exception as exc:
            raise ReplInputUnavailableError(
                "readline input could not be initialized"
            ) from exc
        display_hook = getattr(
            readline, "set_completion_display_matches_hook", None
        )
        if callable(display_hook):
            try:
                display_hook(instance._display_matches)
                instance._saved_state["display_matches_hook_installed"] = True
            except (NotImplementedError, ValueError):
                pass
        return instance

    def restore_global_state(self) -> None:
        """Restore the readline hooks the adapter overwrote at create time.

        Safe to call multiple times. The adapter takes ownership of the
        process-global readline configuration only while the REPL session
        is running; this method gives that ownership back so an embedding
        Python process can keep its prior completer (e.g. ``rlcompleter``
        from ``PYTHONSTARTUP``) afterward.
        """

        readline = self._readline_module
        saved = self._saved_state
        if readline is None or saved is None:
            return
        try:
            readline.set_completer(saved.get("completer"))
            delims = saved.get("completer_delims")
            if isinstance(delims, str):
                readline.set_completer_delims(delims)
        except Exception:
            pass
        if saved.get("display_matches_hook_installed"):
            display_hook = getattr(
                readline, "set_completion_display_matches_hook", None
            )
            if callable(display_hook):
                try:
                    display_hook(None)
                except (NotImplementedError, ValueError, TypeError):
                    pass
        self._saved_state = None

    def matches_for(self, text: str) -> list[str]:
        if text == "" or text.startswith("/"):
            return [
                command_name
                for command_name in self.command_names
                if command_name.startswith(text)
            ]
        return []

    def _completer(self, text: str, state: int) -> str | None:
        if state == 0:
            self._current_matches = self.matches_for(text)
        if 0 <= state < len(self._current_matches):
            return self._current_matches[state]
        return None

    def _display_matches(
        self,
        substitution: str,
        matches: list[str],
        longest_match_length: int,
    ) -> None:
        del substitution, longest_match_length
        self.error_stream.write("\n")
        for match in matches:
            description = self.command_descriptions.get(match, "")
            self.error_stream.write(f"  {match:<20} {description}\n")
        self.error_stream.flush()

    def read_line(self, prompt_label: str, *, footer: str | None = None) -> str:
        del footer
        prompt = f"{prompt_label} "
        try:
            return f"{input(prompt)}\n"
        except EOFError:
            return ""

    def close(self) -> None:
        self.restore_global_state()


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
                output=_prompt_toolkit_output(output_defaults, error_stream),
                completer=PromptToolkitReplCompleter(
                    completion.Completion,
                    workspace=workspace,
                ),
                complete_while_typing=True,
                multiline=True,
                prompt_continuation=_prompt_toolkit_multiline_continuation,
                key_bindings=_prompt_toolkit_multiline_key_bindings(
                    key_binding.KeyBindings
                ),
            )
        except Exception as exc:
            raise ReplInputUnavailableError(
                "prompt-toolkit input could not be initialized"
            ) from exc
        return cls(session=session)

    def read_line(self, prompt_label: str, *, footer: str | None = None) -> str:
        kwargs: dict[str, Any] = {}
        if footer:
            kwargs["bottom_toolbar"] = lambda: footer
        try:
            return f"{self.session.prompt(f'{prompt_label} ', **kwargs)}\n"
        except EOFError:
            return ""

    def close(self) -> None:
        return None


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
        return PlainNativeReplInput(
            input_stream=input_stream, error_stream=error_stream
        )
    if input_runtime == REPL_INPUT_RUNTIME_PROMPT_TOOLKIT:
        return PromptToolkitNativeReplInput.create(
            input_stream=input_stream,
            error_stream=error_stream,
            workspace=workspace,
        )
    if input_runtime == REPL_INPUT_RUNTIME_READLINE:
        return ReadlineNativeReplInput.create(
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
    if _readline_streams_supported(input_stream, error_stream):
        try:
            return ReadlineNativeReplInput.create(
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
    if input_runtime == REPL_INPUT_RUNTIME_READLINE:
        ReadlineNativeReplInput.create(
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


def _prompt_toolkit_output(output_defaults: Any, error_stream: TextIO) -> Any:
    output = output_defaults.create_output(stdout=error_stream)
    if hasattr(output, "enable_cpr"):
        output.enable_cpr = False
    return output


def _prompt_toolkit_multiline_key_bindings(key_bindings_cls: Any) -> Any:
    key_bindings = key_bindings_cls()

    @key_bindings.add("enter")
    def _submit_input(event: Any) -> None:
        event.current_buffer.validate_and_handle()

    @key_bindings.add("c-j")
    def _submit_lf_encoded_input(event: Any) -> None:
        event.current_buffer.validate_and_handle()

    @key_bindings.add("escape", "enter")
    def _insert_newline(event: Any) -> None:
        event.current_buffer.insert_text("\n")

    @key_bindings.add("escape", "c-j")
    def _insert_lf_encoded_newline(event: Any) -> None:
        event.current_buffer.insert_text("\n")

    return key_bindings


def _prompt_toolkit_multiline_continuation(
    width: int, line_number: int, wrap_count: int
) -> str:
    return f"{' ' * max(0, width - 2)}| "


def _prompt_toolkit_streams_supported(
    input_stream: TextIO, error_stream: TextIO
) -> bool:
    return (
        input_stream is sys.stdin
        and error_stream is sys.stderr
        and bool(getattr(input_stream, "isatty", lambda: False)())
        and bool(getattr(error_stream, "isatty", lambda: False)())
    )


def _readline_streams_supported(
    input_stream: TextIO, error_stream: TextIO
) -> bool:
    return (
        input_stream is sys.stdin
        and error_stream is sys.stderr
        and bool(getattr(input_stream, "isatty", lambda: False)())
        and bool(getattr(error_stream, "isatty", lambda: False)())
    )


def _readline_backend_is_libedit(readline_module: Any) -> bool:
    doc = getattr(readline_module, "__doc__", "") or ""
    return "libedit" in doc.lower()


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
        if (
            command_name in _FILE_CONTEXT_COMMANDS_WITH_SEPARATOR
            and _has_file_context_separator(argument_text)
        ):
            return None
        return argument_text
    return None


def _has_file_context_separator(argument_text: str) -> bool:
    # Stop completing as soon as the user appears to be entering the `--`
    # separator; the command parser remains authoritative for final validity.
    return " --" in argument_text or "-- " in argument_text


def _file_reference_completion_request(
    text_before_cursor: str,
    *,
    file_reference_commands: tuple[str, ...],
) -> str | None:
    command_line = text_before_cursor.lstrip()
    if command_line.startswith("/") and not _command_allows_file_reference_completion(
        command_line,
        file_reference_commands=file_reference_commands,
    ):
        return None

    token = _current_completion_token(text_before_cursor)
    if not token.startswith("@"):
        return None
    return token[1:]


def _command_allows_file_reference_completion(
    command_line: str,
    *,
    file_reference_commands: tuple[str, ...],
) -> bool:
    for command_name in file_reference_commands:
        if command_line == command_name:
            return False
        if not command_line.startswith(f"{command_name} "):
            continue
        argument_text = command_line[len(command_name) :].lstrip()
        return _has_file_context_separator(argument_text)
    return False


def _current_completion_token(text_before_cursor: str) -> str:
    stripped_right = text_before_cursor.rstrip()
    if stripped_right != text_before_cursor:
        return ""
    parts = text_before_cursor.rsplit(maxsplit=1)
    if not parts:
        return ""
    return parts[-1]


def _workspace_path_completion_labels(
    workspace: Path, path_prefix: str
) -> tuple[str, ...]:
    directory_prefix, name_prefix = _path_completion_parts(path_prefix)
    if not _safe_path_completion_prefix(directory_prefix, allow_empty=True):
        return ()
    if not _safe_path_completion_prefix(path_prefix, allow_empty=True):
        return ()

    try:
        workspace_root = workspace.expanduser().resolve()
        directory = (
            (workspace_root / directory_prefix).resolve()
            if directory_prefix
            else workspace_root
        )
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
    if (
        path_prefix != path_prefix.strip()
        or "\\" in path_prefix
        or "\x00" in path_prefix
    ):
        return False
    if any(ord(char) < 32 for char in path_prefix):
        return False
    if any(
        char in path_prefix
        for char in (
            "~",
            "$",
            "`",
            "*",
            "?",
            "[",
            "]",
            "{",
            "}",
            "|",
            ";",
            "&",
            "<",
            ">",
        )
    ):
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
