"""Terminal-independent state and transitions for the product TUI editor.

``EditorState`` is the single owner for the editable buffer, cursor, prompt
recall, undo/redo, paste hand-off, slash/completion popup, rehydration, and
terminal steering/follow-up queue.  It deliberately performs no terminal I/O,
filesystem completion lookup, clipboard access, rendering, or extension code
execution; the TUI facade translates those effects into these transitions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal


# Editing is intentionally operation-granular: one typed character, delete,
# kill-to-start, or whole bracketed paste is one undo step. Bound both stacks so
# one long input line cannot retain unbounded copies of prompt text.
_MAX_UNDO_DEPTH = 200
# Recall is session-local and bounded independently of undo. It remains only in
# memory unless the separate, explicit PromptHistoryStore setting is enabled;
# it never enters the metadata-first workflow archive, whose privacy contract
# excludes prompt and pasted bodies.
_MAX_HISTORY_DEPTH = 500

CompletionMode = Literal["at", "path"]
QueuedInputKind = Literal["steering", "follow_up"]


@dataclass(frozen=True, slots=True)
class CompletionItem:
    """One completion row for built-in and extension autocomplete providers.

    ``value`` is the literal splice text inserted into the editor when the row
    is accepted (already ``@``-prefixed and/or double-quoted as needed, with a
    trailing ``/`` for directories). ``label`` is display text for the popup
    row (typically the basename, with a trailing ``/`` for directories).
    """

    value: str
    label: str


@dataclass(frozen=True, slots=True)
class CompletionSelection:
    """Immutable acceptance snapshot captured before extension code executes."""

    text: str
    cursor: int
    mode: CompletionMode
    token_start: int
    prefix: str
    item: CompletionItem
    active_provider: object | None

    def span_is_valid(self) -> bool:
        return 0 <= self.token_start <= self.cursor <= len(self.text)


@dataclass(frozen=True, slots=True)
class QueuedInput:
    """One provider-bound editor message with its closed delivery kind."""

    kind: QueuedInputKind
    content: str


@dataclass(slots=True)
class EditorState:
    """Cohesive mutable owner for one product editor session.

    All transitions are synchronous and terminal-independent. Public fields are
    readable for immutable rendering snapshots and narrow facade compatibility;
    mutation code should prefer transitions so cursor bounds, popup exclusivity,
    history, and queue entries remain coherent.
    """

    text: str = ""
    cursor: int | None = None
    # Prompt bodies stay in this in-memory session buffer. The metadata archive
    # is summary-safe and must never receive them; optional durable recall is a
    # separate, explicitly enabled owner outside EditorState.
    input_history: list[str] = field(default_factory=list)
    history_nav_index: int | None = None
    history_draft: str = ""
    # Snapshots are per edit operation and per line, with redo invalidated by
    # every fresh edit. The constants above cap retained prompt copies.
    undo_stack: list[tuple[str, int]] = field(default_factory=list)
    redo_stack: list[tuple[str, int]] = field(default_factory=list)
    pending_paste: str = ""
    # Stages drafts across hotkey/queue hand-offs and is also the /tree user-
    # message prefill channel for editable new-branch input.
    pending_initial_text: str | None = None
    # A leading slash menu has priority and closes autocomplete. Completion's
    # token_start is the immutable replacement anchor for the active candidate;
    # caret movement closes the popup so a stale anchor cannot splice text.
    slash_menu_open: bool = False
    slash_menu_selection: int = 0
    autocomplete_open: bool = False
    autocomplete_items: tuple[CompletionItem, ...] = ()
    autocomplete_selection: int = 0
    autocomplete_mode: CompletionMode = "at"
    autocomplete_token_start: int = 0
    autocomplete_prefix: str = ""
    autocomplete_active_provider: object | None = None
    autocomplete_provider_factories: list[object] = field(default_factory=list)
    # Normal Enter queues steering; Alt+Enter queues follow-up. Pending display,
    # promotion, and abort/Alt+Up restoration group steering before follow-up,
    # while already-promoted drain entries remain first. Each entry carries its
    # kind structurally so content and delivery classification cannot diverge.
    pending_inputs: list[QueuedInput] = field(default_factory=list)
    pending_drain: list[QueuedInput] = field(default_factory=list)
    last_drain_kind: QueuedInputKind | None = None
    # Mid-turn /... and !... submissions take the local-command hand-off before
    # either provider queue. They are never represented as QueuedInput, so they
    # cannot accidentally drain into a model request.
    pending_command: str | None = None

    def effective_cursor(self) -> int:
        """Return the logical cursor clamped to the current buffer."""

        if self.cursor is None:
            return len(self.text)
        return min(len(self.text), max(0, self.cursor))

    def set_buffer(self, text: str, *, cursor: int | None = None) -> None:
        """Replace the buffer and place the cursor at ``cursor`` or its end."""

        self.text = text
        self.cursor = len(text) if cursor is None else min(len(text), max(0, cursor))

    def begin_line(self) -> str:
        """Start a fresh ``read_line``, consuming any rehydrated draft."""

        text = self.pending_initial_text
        self.pending_initial_text = None
        self.set_buffer("" if text is None else text)
        self.close_slash_menu()
        self.close_autocomplete()
        self.reset_line_editor_state()
        return self.text

    def submit_line(self) -> str:
        """Record and clear the current line, returning its exact text."""

        submitted = self.text
        self.record_history(submitted)
        self.reset_mid_turn_input()
        self.reset_line_editor_state()
        return submitted

    def preserve_for_next_line(self) -> None:
        """Keep a non-empty draft across an editor hotkey sentinel."""

        if self.text:
            self.pending_initial_text = self.text
        self.reset_mid_turn_input()
        self.reset_line_editor_state()

    def reset_mid_turn_input(self) -> None:
        self.set_buffer("")
        self.close_slash_menu()
        self.close_autocomplete()

    def stage_initial_text(self, text: str) -> None:
        self.pending_initial_text = text
        self.set_buffer(text)
        self.close_autocomplete()

    def clear_initial_text(self) -> None:
        self.pending_initial_text = None

    def stage_paste(self, text: str) -> None:
        self.pending_paste = text

    def consume_paste(self) -> str:
        text = self.pending_paste
        self.pending_paste = ""
        return text

    def insert(self, text: str, command_names: tuple[str, ...]) -> None:
        """Record and apply one insertion operation, including an empty one.

        The pre-extraction character-insert path recorded an edit boundary,
        cleared redo/history navigation, and refreshed menus even for ``""``.
        Preserve that observable transition rather than treating it as a no-op.
        """

        self.snapshot_for_undo()
        self.reset_history_nav()
        cursor = self.effective_cursor()
        self.text = self.text[:cursor] + text + self.text[cursor:]
        self.cursor = cursor + len(text)
        self.refresh_slash_menu(command_names)

    def delete_before_cursor(self, command_names: tuple[str, ...]) -> bool:
        cursor = self.effective_cursor()
        if cursor <= 0:
            return False
        self.snapshot_for_undo()
        self.reset_history_nav()
        self.text = self.text[: cursor - 1] + self.text[cursor:]
        self.cursor = cursor - 1
        self.refresh_slash_menu(command_names)
        return True

    def kill_to_line_start(self, command_names: tuple[str, ...]) -> bool:
        cursor = self.effective_cursor()
        if cursor <= 0:
            return False
        self.snapshot_for_undo()
        self.reset_history_nav()
        self.text = self.text[cursor:]
        self.cursor = 0
        self.refresh_slash_menu(command_names)
        return True

    def move_cursor(self, key: str) -> None:
        cursor = self.effective_cursor()
        if key == "left":
            self.cursor = max(0, cursor - 1)
        elif key == "right":
            self.cursor = min(len(self.text), cursor + 1)
        elif key == "home":
            self.cursor = 0
        elif key == "end":
            self.cursor = len(self.text)
        self.close_autocomplete()

    def reset_line_editor_state(self) -> None:
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.reset_history_nav()

    def reset_history_nav(self) -> None:
        self.history_nav_index = None
        self.history_draft = ""

    def snapshot_for_undo(self) -> None:
        self.undo_stack.append((self.text, self.effective_cursor()))
        if len(self.undo_stack) > _MAX_UNDO_DEPTH:
            del self.undo_stack[0]
        self.redo_stack.clear()

    def undo(self, command_names: tuple[str, ...]) -> bool:
        if not self.undo_stack:
            return False
        self.redo_stack.append((self.text, self.effective_cursor()))
        text, cursor = self.undo_stack.pop()
        self.set_buffer(text, cursor=cursor)
        self.reset_history_nav()
        self.refresh_slash_menu(command_names)
        return True

    def redo(self, command_names: tuple[str, ...]) -> bool:
        if not self.redo_stack:
            return False
        self.undo_stack.append((self.text, self.effective_cursor()))
        text, cursor = self.redo_stack.pop()
        self.set_buffer(text, cursor=cursor)
        self.reset_history_nav()
        self.refresh_slash_menu(command_names)
        return True

    def record_history(self, submitted: str) -> None:
        if not submitted.strip():
            return
        if self.input_history and self.input_history[-1] == submitted:
            return
        self.input_history.append(submitted)
        if len(self.input_history) > _MAX_HISTORY_DEPTH:
            del self.input_history[0]

    def navigate_history(self, key: str) -> bool:
        if not self.input_history:
            return False
        if key == "up":
            if self.history_nav_index is None:
                self.history_draft = self.text
                self.history_nav_index = len(self.input_history) - 1
            else:
                self.history_nav_index = max(0, self.history_nav_index - 1)
            self.load_history_entry(self.input_history[self.history_nav_index])
            return True
        if self.history_nav_index is None:
            return False
        self.history_nav_index += 1
        if self.history_nav_index >= len(self.input_history):
            self.history_nav_index = None
            self.load_history_entry(self.history_draft)
            self.history_draft = ""
        else:
            self.load_history_entry(self.input_history[self.history_nav_index])
        return True

    def load_history_entry(self, text: str) -> None:
        self.set_buffer(text)
        # Keep subsequent arrows in history recall instead of command completion.
        self.close_slash_menu()

    def filtered_commands(self, command_names: tuple[str, ...]) -> tuple[str, ...]:
        if not self.slash_menu_open:
            return ()
        prefix = self.text[: self.effective_cursor()]
        return tuple(command for command in command_names if command.startswith(prefix))

    def refresh_slash_menu(self, command_names: tuple[str, ...]) -> None:
        before_cursor = self.text[: self.effective_cursor()]
        if before_cursor.startswith("/") and not any(
            char.isspace() for char in before_cursor
        ):
            self.slash_menu_open = True
            matches = self.filtered_commands(command_names)
            if not matches:
                self.close_slash_menu()
            else:
                self.close_autocomplete()
                if self.slash_menu_selection >= len(matches):
                    self.slash_menu_selection = 0
        else:
            self.close_slash_menu()

    def close_slash_menu(self) -> None:
        self.slash_menu_open = False
        self.slash_menu_selection = 0

    def navigate_slash_menu(self, key: str, command_names: tuple[str, ...]) -> bool:
        matches = self.filtered_commands(command_names)
        if not self.slash_menu_open or not matches:
            return False
        previous = self.slash_menu_selection
        delta = -1 if key == "up" else 1
        self.slash_menu_selection = (previous + delta) % len(matches)
        return self.slash_menu_selection != previous

    def accept_slash_menu(self, command_names: tuple[str, ...]) -> bool:
        matches = self.filtered_commands(command_names)
        if not matches:
            return False
        self.set_buffer(matches[self.slash_menu_selection])
        self.close_slash_menu()
        return True

    def open_autocomplete(
        self,
        *,
        items: tuple[CompletionItem, ...],
        mode: CompletionMode,
        token_start: int,
        prefix: str,
        active_provider: object | None = None,
        reset_selection: bool = False,
    ) -> None:
        # Empty candidates are closed at the coercion/owner boundary, preserving
        # the pre-extraction provider contract and leaving no provider binding.
        # Acceptance still guards an empty tuple so malformed direct state cannot
        # index into it.
        if not items or self.slash_menu_open:
            self.close_autocomplete()
            return
        self.autocomplete_open = True
        self.autocomplete_items = items
        self.autocomplete_mode = mode
        self.autocomplete_token_start = token_start
        self.autocomplete_prefix = prefix
        self.autocomplete_active_provider = active_provider
        if reset_selection or self.autocomplete_selection >= len(items):
            self.autocomplete_selection = 0

    def close_autocomplete(self) -> None:
        self.autocomplete_open = False
        self.autocomplete_items = ()
        self.autocomplete_selection = 0
        self.autocomplete_prefix = ""
        self.autocomplete_active_provider = None

    def navigate_autocomplete(self, key: str) -> bool:
        if not self.autocomplete_open or not self.autocomplete_items:
            return False
        previous = self.autocomplete_selection
        delta = -1 if key == "up" else 1
        self.autocomplete_selection = (previous + delta) % len(self.autocomplete_items)
        return self.autocomplete_selection != previous

    def completion_selection(self) -> CompletionSelection | None:
        """Capture one immutable selection from the current editor state."""

        if not self.autocomplete_open or not self.autocomplete_items:
            return None
        if not 0 <= self.autocomplete_selection < len(self.autocomplete_items):
            return None
        return CompletionSelection(
            text=self.text,
            cursor=self.effective_cursor(),
            mode=self.autocomplete_mode,
            token_start=self.autocomplete_token_start,
            prefix=self.autocomplete_prefix,
            item=self.autocomplete_items[self.autocomplete_selection],
            active_provider=self.autocomplete_active_provider,
        )

    def apply_completion_result(self, text: str, cursor: int) -> None:
        """Apply an adapter-produced completion and close its popup."""

        self.set_buffer(text, cursor=cursor)
        self.close_autocomplete()

    def enqueue_steering(self, text: str) -> None:
        if text.strip():
            self.pending_inputs.append(QueuedInput("steering", text))

    def enqueue_follow_up(self, text: str) -> None:
        if text.strip():
            self.pending_inputs.append(QueuedInput("follow_up", text))

    def has_pending_messages(self) -> bool:
        return bool(self.pending_inputs)

    def pending_messages(self) -> tuple[QueuedInput, ...]:
        """Return pending entries in steering-before-follow-up order."""

        return tuple(
            entry
            for kind in ("steering", "follow_up")
            for entry in self.pending_inputs
            if entry.kind == kind
        )

    def promote_pending_to_drain(self) -> None:
        self.pending_drain.extend(self.pending_messages())
        self.pending_inputs.clear()

    def restore_pending_to_editor(
        self, *, custom_text_supplier: Callable[[], str] | None = None
    ) -> bool:
        """Restore queued input using the sole draft-source precedence policy."""

        queued = [*self.pending_drain, *self.pending_messages()]
        self.pending_drain.clear()
        self.last_drain_kind = None
        self.pending_inputs.clear()
        if not queued:
            return False

        if self.pending_initial_text is not None:
            existing = self.pending_initial_text
        elif custom_text_supplier is not None:
            existing = custom_text_supplier()
        else:
            existing = self.text

        joined = "\n\n".join(entry.content for entry in queued)
        combined = f"{joined}\n\n{existing}" if existing else joined
        self.pending_initial_text = combined
        self.set_buffer(combined)
        return True

    def take_next_drain(self) -> str | None:
        if not self.pending_drain:
            self.last_drain_kind = None
            return None
        entry = self.pending_drain.pop(0)
        self.last_drain_kind = entry.kind
        return entry.content

    def take_last_drain_kind(self) -> QueuedInputKind | None:
        kind = self.last_drain_kind
        self.last_drain_kind = None
        return kind

    def set_pending_command(self, text: str) -> None:
        self.pending_command = text

    def take_pending_command(self) -> str | None:
        command = self.pending_command
        self.pending_command = None
        return command
