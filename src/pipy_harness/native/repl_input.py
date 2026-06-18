"""Input adapters for the native pipy REPL."""

from __future__ import annotations

import importlib
import select
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, AsyncIterator, Iterable, Protocol, TextIO

from pipy_harness.capture import looks_sensitive
from pipy_harness.native.read_only_tool import _is_ignored_or_generated, _is_relative_to
from pipy_harness.native.terminal_input import read_terminal_utf8_char

REPL_INPUT_RUNTIME_AUTO = "auto"
REPL_INPUT_RUNTIME_PLAIN = "plain"
REPL_INPUT_RUNTIME_PROMPT_TOOLKIT = "prompt-toolkit"
REPL_INPUT_RUNTIME_READLINE = "readline"
REPL_INPUT_RUNTIME_SLASH_MENU = "slash-menu"
SUPPORTED_REPL_INPUT_RUNTIMES = (
    REPL_INPUT_RUNTIME_AUTO,
    REPL_INPUT_RUNTIME_PLAIN,
    REPL_INPUT_RUNTIME_PROMPT_TOOLKIT,
    REPL_INPUT_RUNTIME_READLINE,
    REPL_INPUT_RUNTIME_SLASH_MENU,
)
DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS = (
    "/help",
    "/hotkeys",
    "/clear",
    "/compact",
    "/status",
    "/settings",
    "/login",
    "/logout",
    "/model",
    "/theme",
    "/skill",
    "/template",
    "/read",
    "/ask-file",
    "/propose-file",
    "/apply-proposal",
    "/reload",
    "/changelog",
    "/exit",
    "/quit",
)
DEFAULT_REPL_COMMAND_DESCRIPTIONS: dict[str, str] = {
    "/help": "Show pipy command reference",
    "/hotkeys": "Show keyboard shortcuts",
    "/reload": "Reload settings, keybindings, and resources",
    "/changelog": "Show the changelog (What's New)",
    "/clear": "Clear local conversation context",
    "/compact": "Compact context, keep a safe summary",
    "/export": "Export the native session to HTML or active-branch JSONL",
    "/import": "Import a native session JSONL file",
    "/share": "Upload the native session as a secret GitHub gist",
    "/status": "Show REPL state (read-only)",
    "/settings": "Settings and status",
    "/copy": "Copy the last answer to the clipboard (local)",
    "/login": "Log in (openai-codex OAuth)",
    "/logout": "Log out (openai-codex OAuth)",
    "/model": "Select provider/model",
    "/scoped-models": "View/set the Ctrl+P model cycle set",
    "/theme": "Select chrome color theme",
    "/skill": "List or load a workspace/global skill",
    "/template": "List or run a prompt template",
    "/read": "Read a workspace file excerpt",
    "/ask-file": "Ask provider about a file (read-only)",
    "/propose-file": "Propose a patch for a file",
    "/apply-proposal": "Apply the pending proposal",
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
class SlashMenuNativeReplInput:
    """Stdlib raw-mode line editor with a Pi-like ``/`` command menu.

    Provides the centerpiece of pipy's default Pi-parity REPL experience:
    a `/` keystroke on an empty line opens a popup command menu below the
    prompt with names, descriptions, and a reverse-video selection
    highlight. Up/Down navigate; Enter (or Tab) accepts the selection;
    Esc closes the menu while preserving typed text. No runtime
    dependencies — uses ``termios``/``tty`` for cbreak mode and ANSI
    escapes for rendering.

    Streams must be real TTYs; otherwise raises
    :class:`ReplInputUnavailableError` so the auto resolver falls back to
    the readline or plain adapter. The adapter restores the prior
    terminal attributes on close.
    """

    input_stream: TextIO
    error_stream: TextIO
    command_names: tuple[str, ...] = DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS
    command_descriptions: Mapping[str, str] = field(
        default_factory=lambda: dict(DEFAULT_REPL_COMMAND_DESCRIPTIONS)
    )
    # Max rows shown in the slash-command menu (Pi autocompleteMaxVisible).
    autocomplete_max_visible: int = 8
    runtime_label: str = REPL_INPUT_RUNTIME_SLASH_MENU
    _termios_module: Any | None = None
    _tty_module: Any | None = None
    _input_fd: int = -1

    @classmethod
    def create(
        cls,
        *,
        input_stream: TextIO,
        error_stream: TextIO,
        workspace: Path | None = None,
        command_names: tuple[str, ...] | None = None,
        command_descriptions: Mapping[str, str] | None = None,
        autocomplete_max_visible: int = 8,
    ) -> "SlashMenuNativeReplInput":
        del workspace
        if not _slash_menu_streams_supported(input_stream, error_stream):
            raise ReplInputUnavailableError(
                "slash-menu input requires the process stdin and stderr TTY streams"
            )
        try:
            termios_module = importlib.import_module("termios")
            tty_module = importlib.import_module("tty")
        except ImportError as exc:
            raise ReplInputUnavailableError(
                "slash-menu input requires termios and tty stdlib modules"
            ) from exc
        try:
            fd = input_stream.fileno()
            termios_module.tcgetattr(fd)
        except (OSError, ValueError, AttributeError) as exc:
            raise ReplInputUnavailableError(
                "slash-menu input requires a stdin file descriptor with termios access"
            ) from exc
        instance = cls(
            input_stream=input_stream,
            error_stream=error_stream,
            command_names=command_names or DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS,
            command_descriptions=(
                dict(command_descriptions)
                if command_descriptions is not None
                else dict(DEFAULT_REPL_COMMAND_DESCRIPTIONS)
            ),
            autocomplete_max_visible=autocomplete_max_visible,
        )
        instance._termios_module = termios_module
        instance._tty_module = tty_module
        instance._input_fd = fd
        return instance

    def read_line(self, prompt_label: str, *, footer: str | None = None) -> str:
        editor = _SlashMenuLineEditor(
            input_stream=self.input_stream,
            error_stream=self.error_stream,
            command_names=self.command_names,
            command_descriptions=self.command_descriptions,
            termios_module=self._termios_module,
            tty_module=self._tty_module,
            input_fd=self._input_fd,
            prompt_label=prompt_label,
            footer=footer,
            autocomplete_max_visible=self.autocomplete_max_visible,
        )
        return editor.run()

    def close(self) -> None:
        return None


_SLASH_MENU_MAX_ITEMS = 8


@dataclass(slots=True)
class _SlashMenuLineEditor:
    """Stateful per-call helper that owns one read_line invocation."""

    input_stream: TextIO
    error_stream: TextIO
    command_names: tuple[str, ...]
    command_descriptions: Mapping[str, str]
    termios_module: Any
    tty_module: Any
    input_fd: int
    prompt_label: str
    footer: str | None
    autocomplete_max_visible: int = _SLASH_MENU_MAX_ITEMS
    _buffer: str = ""
    _cursor: int = 0
    _menu_open: bool = False
    _menu_selection: int = 0
    _last_drawn_rows: int = 0
    _pending_input_bytes: bytearray = field(default_factory=bytearray)

    def run(self) -> str:
        if self.termios_module is None or self.tty_module is None:
            raise ReplInputUnavailableError(
                "slash-menu input requires termios and tty stdlib modules"
            )
        old_state = self.termios_module.tcgetattr(self.input_fd)
        try:
            self.tty_module.setcbreak(self.input_fd, self.termios_module.TCSANOW)
            new_state = self.termios_module.tcgetattr(self.input_fd)
            new_state[3] &= ~self.termios_module.ECHO
            self.termios_module.tcsetattr(
                self.input_fd, self.termios_module.TCSANOW, new_state
            )
            self._render()
            while True:
                key = self._read_key()
                if key is None:
                    self._finalize_and_print_buffer(submitted=False)
                    return ""
                if key == "ctrl-c":
                    self._finalize_and_print_buffer(submitted=False)
                    raise KeyboardInterrupt
                if key == "ctrl-d" and not self._buffer:
                    self._finalize_and_print_buffer(submitted=False)
                    return ""
                if key == "enter":
                    if self._menu_open and self._filtered_commands():
                        matches = self._filtered_commands()
                        if self._buffer not in matches:
                            self._accept_menu_selection()
                    self._finalize_and_print_buffer(submitted=True)
                    return self._buffer + "\n"
                if key == "shift-enter":
                    self._insert_char("\n")
                    continue
                if key in ("up", "down"):
                    self._navigate_menu(key)
                    continue
                if key == "tab":
                    if self._menu_open and self._filtered_commands():
                        self._accept_menu_selection()
                    continue
                if key == "esc":
                    if self._menu_open:
                        self._menu_open = False
                        self._render()
                    continue
                if key == "backspace":
                    self._handle_backspace()
                    continue
                if key in ("left", "right", "home", "end"):
                    self._handle_cursor_move(key)
                    continue
                if key == "ctrl-u":
                    self._buffer = self._buffer[self._cursor :]
                    self._cursor = 0
                    self._refresh_menu_state()
                    self._render()
                    continue
                if len(key) == 1 and key.isprintable():
                    self._insert_char(key)
                    continue
        finally:
            self.termios_module.tcsetattr(
                self.input_fd, self.termios_module.TCSADRAIN, old_state
            )

    def _read_key(self) -> str | None:
        ch = self._read_byte()
        if ch == "":
            return None
        if ch == "\x1b":
            next1 = self._read_byte_with_timeout(0.05)
            if next1 == "":
                return "esc"
            # Alt/shift + Enter is reported by most terminals as
            # `ESC \r` or `ESC \n`; treat it as a newline insertion
            # request to match pi's "Shift+Enter = newline" UX.
            if next1 == "\r" or next1 == "\n":
                return "shift-enter"
            if next1 != "[":
                return "esc"
            next2 = self._read_byte_with_timeout(0.05)
            if next2 == "A":
                return "up"
            if next2 == "B":
                return "down"
            if next2 == "C":
                return "right"
            if next2 == "D":
                return "left"
            if next2 == "H":
                return "home"
            if next2 == "F":
                return "end"
            # xterm `modifyOtherKeys=2` encodes Shift+Enter as
            # `ESC [ 27 ; 2 ; 13 ~`. Drain the params and return
            # shift-enter when the trailing key matches Enter (13).
            if next2 == "2":
                rest = self._read_csi_remainder()
                if rest == "7;2;13~":
                    return "shift-enter"
                return "esc"
            # kitty keyboard protocol encodes Shift+Enter as
            # `ESC [ 13 ; 2 u`. Detect the `13;2u` suffix.
            if next2 == "1":
                rest = self._read_csi_remainder()
                if rest == "3;2u":
                    return "shift-enter"
                return "esc"
            return "esc"
        if ch == "\r" or ch == "\n":
            return "enter"
        if ch == "\t":
            return "tab"
        if ch == "\x7f" or ch == "\x08":
            return "backspace"
        if ch == "\x03":
            return "ctrl-c"
        if ch == "\x04":
            return "ctrl-d"
        if ch == "\x15":
            return "ctrl-u"
        if ch == "\x01":
            return "home"
        if ch == "\x05":
            return "end"
        return ch

    def _read_byte(self) -> str:
        return read_terminal_utf8_char(
            self.input_fd,
            pending_bytes=self._pending_input_bytes,
        )

    def _read_byte_with_timeout(self, timeout: float) -> str:
        if self._pending_input_bytes:
            return self._read_byte()
        r, _, _ = select.select([self.input_fd], [], [], timeout)
        if self.input_fd not in r:
            return ""
        return self._read_byte()

    def _read_csi_remainder(self) -> str:
        """Drain the rest of a CSI parameter+intermediate+final sequence.

        Reads up to a small bounded number of bytes after the initial
        `ESC [` prefix and the first parameter digit, stopping when a
        final byte (`~`, `u`, alphabetic, etc.) arrives or the read
        times out. Used to detect modifyOtherKeys / kitty encodings
        for Shift+Enter without growing the main escape switch.
        """

        out = ""
        for _ in range(16):
            byte = self._read_byte_with_timeout(0.05)
            if not byte:
                break
            out += byte
            if byte == "~" or byte.isalpha():
                break
        return out

    def _insert_char(self, ch: str) -> None:
        self._buffer = self._buffer[: self._cursor] + ch + self._buffer[self._cursor :]
        self._cursor += len(ch)
        self._refresh_menu_state()
        self._render()

    def _handle_backspace(self) -> None:
        if self._cursor == 0:
            return
        self._buffer = self._buffer[: self._cursor - 1] + self._buffer[self._cursor :]
        self._cursor -= 1
        self._refresh_menu_state()
        self._render()

    def _handle_cursor_move(self, key: str) -> None:
        if key == "left" and self._cursor > 0:
            self._cursor -= 1
        elif key == "right" and self._cursor < len(self._buffer):
            self._cursor += 1
        elif key == "home":
            self._cursor = 0
        elif key == "end":
            self._cursor = len(self._buffer)
        self._render()

    def _refresh_menu_state(self) -> None:
        if self._buffer.startswith("/"):
            self._menu_open = True
            matches = self._filtered_commands()
            if not matches:
                self._menu_open = False
                self._menu_selection = 0
            elif self._menu_selection >= len(matches):
                self._menu_selection = 0
        else:
            self._menu_open = False
            self._menu_selection = 0

    def _filtered_commands(self) -> tuple[str, ...]:
        if not self._menu_open:
            return ()
        prefix = self._buffer
        return tuple(name for name in self.command_names if name.startswith(prefix))

    def _accept_menu_selection(self) -> None:
        matches = self._filtered_commands()
        if not matches:
            return
        selected = matches[self._menu_selection]
        self._buffer = selected
        self._cursor = len(selected)
        self._menu_open = False
        self._menu_selection = 0
        self._render()

    def _navigate_menu(self, key: str) -> None:
        matches = self._filtered_commands()
        if not self._menu_open or not matches:
            return
        n = len(matches)
        delta = -1 if key == "up" else 1
        self._menu_selection = (self._menu_selection + delta) % n
        self._render()

    def _render(self) -> None:
        out = self.error_stream
        out.write("\r")
        if self._last_drawn_rows > 0:
            out.write(f"\x1b[{self._last_drawn_rows}B")
            out.write("\r")
            out.write(f"\x1b[{self._last_drawn_rows}A")
        out.write("\x1b[J")
        # When the prompt label is empty (pi-parity: no leading glyph
        # before the input cursor), don't append a separator space —
        # otherwise the cursor sits one column to the right of pi's
        # column-1 input position.
        prompt_text = (
            f"{self.prompt_label} " if self.prompt_label else ""
        )
        out.write(prompt_text)
        out.write(self._buffer)
        # Pi's TUI paints an explicit block-cursor character at the
        # input position, so the cursor stays visible even when the
        # terminal pane loses focus. The native terminal cursor
        # blinks off in unfocused panes, so we burn a reverse-video
        # space at the input column and let the absolute
        # `\x1b[{target_col}G` move below place the real cursor back
        # over it. When the cursor is in the middle of the buffer we
        # already have a character there, so the reverse-video paint
        # is unnecessary.
        if self._cursor == len(self._buffer):
            out.write("\x1b[7m \x1b[0m")
        rows_below = 0
        matches = self._filtered_commands()
        if self._menu_open and matches:
            menu_cap = self.autocomplete_max_visible if self.autocomplete_max_visible > 0 else _SLASH_MENU_MAX_ITEMS
            visible = matches[:menu_cap]
            total = len(matches)
            for idx, name in enumerate(visible):
                description = self.command_descriptions.get(name, "")
                position = f"({idx + 1}/{total})" if idx == self._menu_selection else ""
                line = f"  {name:<16} {description}"
                if position:
                    pad = max(1, 60 - len(line))
                    line = f"{line}{' ' * pad}{position}"
                if idx == self._menu_selection:
                    line = f"\x1b[7m{line}\x1b[0m"
                out.write("\r\n" + line)
                rows_below += 1
            if total > len(visible):
                more = f"  … {total - len(visible)} more"
                out.write("\r\n" + f"\x1b[2m{more}\x1b[0m")
                rows_below += 1
        if self.footer:
            footer_rows = self._render_bottom_frame(out)
            rows_below += footer_rows
        if rows_below > 0:
            out.write(f"\x1b[{rows_below}A")
        out.write("\r")
        target_col = len(prompt_text) + self._cursor + 1
        out.write(f"\x1b[{target_col}G")
        out.flush()
        self._last_drawn_rows = rows_below

    def _render_bottom_frame(self, out: TextIO) -> int:
        """Render separator + cwd + status line below the prompt area.

        Returns the number of rows printed. Cursor is left on the last
        printed row; the caller moves it back up to the prompt input.

        Trailing blank row: pi's TUI leaves one row of vertical
        breathing room between the status line and the pane's bottom
        edge so the status never butts up against the pane border.
        Pipy mirrors that here by appending one extra `\\r\\n` past
        the status row — the caller's `\\x1b[Nrows_below A` already
        moves the cursor back to the input row, so the extra row just
        renders as an empty bottom-frame buffer.
        """

        if not self.footer:
            return 0
        from pipy_harness.native.chrome import chrome_style_for, chrome_width

        style = chrome_style_for(out)
        width = chrome_width(out)
        rows = 0
        separator = "─" * width
        out.write("\r\n" + style.separator(separator))
        rows += 1
        for line in self.footer.splitlines():
            out.write("\r\n" + style.dim(line))
            rows += 1
        out.write("\r\n")
        rows += 1
        return rows

    def _finalize_and_print_buffer(self, *, submitted: bool) -> None:
        del submitted
        out = self.error_stream
        if self._last_drawn_rows > 0:
            out.write(f"\x1b[{self._last_drawn_rows}B")
            out.write("\r")
            out.write(f"\x1b[{self._last_drawn_rows}A")
        out.write("\r\x1b[J")
        out.write(f"{self.prompt_label} {self._buffer}\n")
        out.flush()
        self._last_drawn_rows = 0


def _slash_menu_streams_supported(
    input_stream: TextIO, error_stream: TextIO
) -> bool:
    if input_stream is not sys.stdin or error_stream is not sys.stderr:
        return False
    if not bool(getattr(input_stream, "isatty", lambda: False)()):
        return False
    if not bool(getattr(error_stream, "isatty", lambda: False)()):
        return False
    if sys.platform.startswith("win"):
        return False
    return True


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
    command_names: tuple[str, ...] | None = None,
    command_descriptions: Mapping[str, str] | None = None,
    autocomplete_max_visible: int = 8,
) -> NativeReplInput:
    """Choose the REPL input adapter while preserving plain-stream fallback.

    ``command_names`` / ``command_descriptions`` override the slash-menu
    completion set so the discovered workspace/global custom commands (and
    the ``/skill`` / ``/template`` entry points) appear in completion. Only
    the slash-menu adapter (pipy's default TTY runtime) honours the override
    today; the readline / prompt-toolkit fallbacks keep the static built-in
    set and still execute custom commands through the dispatcher.
    """

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
    if input_runtime == REPL_INPUT_RUNTIME_SLASH_MENU:
        return SlashMenuNativeReplInput.create(
            input_stream=input_stream,
            error_stream=error_stream,
            workspace=workspace,
            command_names=command_names,
            command_descriptions=command_descriptions,
            autocomplete_max_visible=autocomplete_max_visible,
        )

    if _slash_menu_streams_supported(input_stream, error_stream):
        try:
            return SlashMenuNativeReplInput.create(
                input_stream=input_stream,
                error_stream=error_stream,
                workspace=workspace,
                command_names=command_names,
                command_descriptions=command_descriptions,
            autocomplete_max_visible=autocomplete_max_visible,
            )
        except Exception:
            pass
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
    if input_runtime == REPL_INPUT_RUNTIME_SLASH_MENU:
        SlashMenuNativeReplInput.create(
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
