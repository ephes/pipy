"""The autocomplete owner: `@`/path completion, the slash menu, and providers.

`BuiltinAutocompleteProvider` implements the same duck-typed provider interface
an extension supplies to `api.registerAutocompleteProvider`, so the built-in
completion and an extension's travel the same code path -- there is no
privileged built-in branch. Both snake_case and camelCase spellings are bound
because Pi's interface is camelCase and pipy's own callers are not.

`AutocompleteComponent` owns everything the popup and menu need beyond the
editor buffer itself: the published :class:`CommandSurface` (slash-command
names, descriptions, and extension shortcut keys), the settings-driven
``max_visible`` row cap, the extension provider registry effects, suggestion
resolution, forced Tab path completion, and the popup frame rendering. The
mutable open/items/selection state stays on the dependency-neutral
:class:`EditorState` record, which the component receives whole -- it *is* the
buffer accessor.

Ownership decisions, recorded:

- ``CommandSurface`` is one frozen record because its three parts are always
  replaced together: both writers (session startup and ``/reload``) derive
  names, descriptions, and shortcut keys from the same workspace-resources +
  extension-generation projection in one motion. ``max_visible`` is *not* in
  the record -- it is settings-only display state with its own replacement
  schedule (construction at startup, ``/reload`` when settings change) and
  would force both writers to thread a value they do not own.
- The component takes no ``PaintLock``. Editor completion state is mutated on
  the key-loop thread and snapshotted by the paint path exactly as the
  pre-extraction facade did; the injected ``repaint`` callable performs its
  own locking. The overlay components lock because their records are shared
  with concurrent painters mid-transition; nothing here changes that split.
- ``custom_editor_component`` is an injected zero-argument callable because
  the live custom-editor component is (for now) owned by the terminal-UI
  shell; the callable retires when ``ui/components/custom_editor.py`` owns
  that record (decomposition plan slice 24).

The workspace root is taken by value rather than as a handle to the terminal
UI: the root is the only thing completion needs, and the shell's ``cwd`` is
assigned once at construction and never reassigned, so a copy cannot drift.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from pipy_harness.native.autocomplete_provider import (
    AutocompleteApplyResult,
    AutocompleteContext,
    AutocompleteSuggestion,
    call_provider_method,
    coerce_apply_result,
    coerce_suggestion,
    cursor_to_line_col,
    line_col_to_cursor,
)
from pipy_harness.native.editor_completion import (
    at_candidates,
    extract_at_token,
    extract_path_prefix,
    path_candidates,
)
from pipy_harness.native.editor_state import CompletionItem, EditorState
from pipy_harness.native.frame_renderer import FrameLine, clip_text


class BuiltinAutocompleteProvider:
    """Pi-shaped wrapper around pipy's built-in @ and path completion."""

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd

    def get_suggestions(
        self,
        lines: Sequence[str],
        cursor_line: int,
        cursor_col: int,
        context: AutocompleteContext,
    ) -> AutocompleteSuggestion | None:
        text_before_cursor = "\n".join(lines[:cursor_line])
        if cursor_line > 0:
            text_before_cursor += "\n"
        text_before_cursor += lines[cursor_line][:cursor_col]
        if context.force:
            extracted = extract_path_prefix(text_before_cursor, force=True)
            if extracted is None:
                return None
            start, prefix = extracted
            if prefix == "":
                return None
            items = tuple(path_candidates(self._cwd, prefix))
            if not items:
                return None
            return AutocompleteSuggestion(items, prefix, start, "path")
        token = extract_at_token(text_before_cursor)
        if token is None:
            return None
        start, query = token
        items = tuple(at_candidates(self._cwd, query))
        if not items:
            return None
        return AutocompleteSuggestion(items, "@" + query, start, "at")

    getSuggestions = get_suggestions

    def apply_completion(
        self,
        lines: Sequence[str],
        cursor_line: int,
        cursor_col: int,
        item: CompletionItem,
        prefix: str,
    ) -> AutocompleteApplyResult:
        text = "\n".join(lines)
        cursor = line_col_to_cursor(lines, cursor_line, cursor_col)
        start = max(0, cursor - len(prefix))
        new_text = text[:start] + item.value + text[cursor:]
        return AutocompleteApplyResult(new_text, start + len(item.value))

    applyCompletion = apply_completion

    def should_trigger_file_completion(
        self, lines: Sequence[str], cursor_line: int, cursor_col: int
    ) -> bool:
        del lines, cursor_line, cursor_col
        return True

    shouldTriggerFileCompletion = should_trigger_file_completion


@dataclass(frozen=True, slots=True)
class CommandSurface:
    """The slash-command surface one extension generation publishes.

    ``names`` is the ordered advertised completion list, ``descriptions`` the
    menu's per-command detail column, and ``shortcut_keys`` the decoded key
    strings (e.g. ``"ctrl-g"``) bound by activated extensions via
    ``api.register_shortcut``. When the editor reads one of these keys it
    returns the HOTKEY_EXTENSION_SHORTCUT sentinel so the session dispatches
    the bound handler; keys the decoder cannot produce simply never fire.
    Frozen because the three parts share one derivation and one replacement
    schedule -- see the module docstring.
    """

    names: tuple[str, ...] = ()
    descriptions: Mapping[str, str] = field(default_factory=dict)
    shortcut_keys: frozenset[str] = frozenset()


class AutocompleteComponent:
    """Owner of slash-menu and autocomplete behavior over the editor buffer."""

    def __init__(
        self,
        editor: EditorState,
        *,
        cwd: Path,
        repaint: Callable[[], None],
        custom_editor_component: Callable[[], object | None],
        surface: CommandSurface | None = None,
        max_visible: int = 5,
    ) -> None:
        self._editor = editor
        self._cwd = cwd
        self._repaint = repaint
        self._custom_editor_component = custom_editor_component
        self._surface = surface if surface is not None else CommandSurface()
        self._max_visible = max_visible

    # --- the command surface -------------------------------------------------

    @property
    def command_surface(self) -> CommandSurface:
        return self._surface

    @property
    def command_names(self) -> tuple[str, ...]:
        return self._surface.names

    @property
    def command_descriptions(self) -> Mapping[str, str]:
        return self._surface.descriptions

    @property
    def shortcut_keys(self) -> frozenset[str]:
        return self._surface.shortcut_keys

    @property
    def max_visible(self) -> int:
        return self._max_visible

    def replace_command_surface(self, surface: CommandSurface) -> None:
        """Atomically publish a new command surface (startup / ``/reload``)."""

        self._surface = surface

    def set_max_visible(self, value: int) -> None:
        """Adopt the settings-driven popup row cap (Pi autocompleteMaxVisible)."""

        self._max_visible = value

    # --- provider registry ---------------------------------------------------

    def add_extension_provider(self, factory: object) -> None:
        if callable(factory):
            self._editor.autocomplete_provider_factories.append(factory)
            component = self._custom_editor_component()
            if component is not None:
                self.forward_to_custom_editor(component)

    def forward_to_custom_editor(self, component: object) -> None:
        setter = getattr(component, "set_autocomplete_provider", None) or getattr(
            component, "setAutocompleteProvider", None
        )
        if not callable(setter) or not self._editor.autocomplete_provider_factories:
            return
        try:
            setter(self._provider())
        except Exception:  # noqa: BLE001 - fail-soft extension UI adapter
            pass

    def _provider(self) -> object:
        provider: object = BuiltinAutocompleteProvider(self._cwd)
        for factory in self._editor.autocomplete_provider_factories:
            try:
                wrapped = cast(Callable[[object], object], factory)(provider)
            except Exception:  # noqa: BLE001 - extension provider must fail soft
                continue
            if wrapped is not None:
                provider = wrapped
        return provider

    # --- suggestion resolution ----------------------------------------------

    def _suggestions(self, *, force: bool) -> AutocompleteSuggestion | None:
        cursor = self._editor.effective_cursor()
        lines, cursor_line, cursor_col = cursor_to_line_col(self._editor.text, cursor)
        provider = self._provider()
        if force:
            try:
                should = call_provider_method(
                    provider,
                    "should_trigger_file_completion",
                    "shouldTriggerFileCompletion",
                    lines,
                    cursor_line,
                    cursor_col,
                )
            except AttributeError:
                should = True
            except Exception:  # noqa: BLE001 - extension provider must fail soft
                should = True
            if not bool(should):
                return None
        try:
            raw = call_provider_method(
                provider,
                "get_suggestions",
                "getSuggestions",
                lines,
                cursor_line,
                cursor_col,
                AutocompleteContext(force=force, signal=None),
            )
        except Exception:  # noqa: BLE001 - extension provider must fail soft
            provider = BuiltinAutocompleteProvider(self._cwd)
            raw = provider.get_suggestions(
                lines,
                cursor_line,
                cursor_col,
                AutocompleteContext(force=force, signal=None),
            )
        suggestion = coerce_suggestion(raw)
        self._editor.autocomplete_active_provider = (
            provider if suggestion is not None else None
        )
        return suggestion

    # --- popup transitions ---------------------------------------------------

    def refresh_slash_menu(self) -> None:
        self._editor.refresh_slash_menu(self._surface.names)
        self.refresh()

    def refresh(self) -> None:
        """Open/refresh the ``@`` file picker as the editor content changes.

        The slash menu keeps priority for a leading ``/``; while it is open the
        autocomplete popup stays closed so the two never co-open. Otherwise an
        ``@``-prefixed token at the cursor opens a scored, workspace-bounded
        file picker (Pi's content trigger). Tab path completion is forced (not
        auto), so it is not opened here.
        """

        if self._editor.slash_menu_open:
            self.close()
            return
        suggestion = self._suggestions(force=False)
        if suggestion is None:
            self.close()
            return
        self._editor.open_autocomplete(
            items=tuple(suggestion.items),
            mode=suggestion.mode,
            token_start=suggestion.token_start,
            prefix=suggestion.prefix,
            active_provider=self._editor.autocomplete_active_provider,
        )

    def close(self) -> None:
        self._editor.close_autocomplete()

    def navigate(self, key: str) -> None:
        if self._editor.navigate_autocomplete(key):
            self._repaint()

    def accept_selection(self) -> None:
        """Replace the active ``@``/path token with the highlighted candidate.

        Accepting an ``@`` candidate leaves a literal ``@path`` in the buffer so
        the existing ``file_references`` resolver loads its bounded excerpt on
        submit. Accepting a directory in path mode re-opens the popup for the
        next segment, mirroring Pi's progressive Tab completion.
        """

        selection = self._editor.completion_selection()
        if selection is None:
            return
        # Capture and validate one immutable owner snapshot before trusted
        # extension code runs. The callback may synchronously mutate the editor
        # or popup through its UI context; provider arguments, fallback splice,
        # accepted mode, and directory behavior must all use this same snapshot.
        if not selection.span_is_valid():
            self._editor.close_autocomplete()
            self._repaint()
            return
        self._editor.snapshot_for_undo()
        self._editor.reset_history_nav()
        provider = selection.active_provider or BuiltinAutocompleteProvider(self._cwd)
        lines, cursor_line, cursor_col = cursor_to_line_col(
            selection.text, selection.cursor
        )
        try:
            raw_result = call_provider_method(
                provider,
                "apply_completion",
                "applyCompletion",
                lines,
                cursor_line,
                cursor_col,
                selection.item,
                selection.prefix
                or selection.text[selection.token_start : selection.cursor],
            )
            result = coerce_apply_result(raw_result)
        except Exception:  # noqa: BLE001 - extension provider must fail soft
            result = None
        if result is None:
            result = AutocompleteApplyResult(
                selection.text[: selection.token_start]
                + selection.item.value
                + selection.text[selection.cursor :],
                selection.token_start + len(selection.item.value),
            )
        self._editor.apply_completion_result(result.text, result.cursor)
        if selection.mode == "path" and selection.item.value.rstrip('"').endswith("/"):
            # Directory accepted: re-open the popup for the next segment.
            self.attempt_path_completion()
        self._repaint()

    def attempt_path_completion(self) -> bool:
        """Forced Tab path completion against the prefix before the cursor.

        Returns ``True`` when the prefix produced candidates (and the editor was
        updated/opened), ``False`` for a no-op. Uses the forced-Tab prefix so
        bare workspace prefixes (``README``, ``scr``) complete, not just
        path-like ones; Tab stays a no-op in prose because the empty-token case
        (e.g. after a trailing space) is skipped and a non-path word that
        matches no workspace entry yields no candidates. Completes the longest
        unambiguous prefix and opens the popup when more than one remains.
        """

        # Key dispatch gives an open slash menu first refusal (Tab accepts its
        # selected command). Keep the completion adapter honest when called
        # directly too: do not execute provider/filesystem lookup only to have
        # the owner reject the mutually exclusive autocomplete popup.
        if self._editor.slash_menu_open:
            return False
        suggestion = self._suggestions(force=True)
        if suggestion is None:
            return False
        start = suggestion.token_start
        prefix = suggestion.prefix
        items = suggestion.items
        cursor = self._editor.effective_cursor()
        common = self._longest_common_value(items)
        if common and len(common) > len(prefix):
            self._editor.snapshot_for_undo()
            self._editor.reset_history_nav()
            self._editor.set_buffer(
                self._editor.text[:start] + common + self._editor.text[cursor:],
                cursor=start + len(common),
            )
            cursor = self._editor.effective_cursor()
            prefix = common
        if len(items) == 1:
            single = items[0].value
            self._editor.snapshot_for_undo()
            self._editor.reset_history_nav()
            self._editor.set_buffer(
                self._editor.text[:start] + single + self._editor.text[cursor:],
                cursor=start + len(single),
            )
            self._editor.close_autocomplete()
            return True
        self._editor.open_autocomplete(
            items=tuple(items),
            mode="path",
            token_start=start,
            prefix=prefix,
            active_provider=self._editor.autocomplete_active_provider,
            reset_selection=True,
        )
        return True

    @staticmethod
    def _longest_common_value(items: Sequence[CompletionItem]) -> str:
        values = [item.value for item in items]
        if not values:
            return ""
        shortest = min(values, key=len)
        for index, char in enumerate(shortest):
            if any(value[index] != char for value in values):
                return shortest[:index]
        return shortest

    # --- slash menu ----------------------------------------------------------

    def filtered_commands(self) -> tuple[str, ...]:
        return self._editor.filtered_commands(self._surface.names)

    def accept_slash_menu_selection(self) -> None:
        if self._editor.accept_slash_menu(self._surface.names):
            self._repaint()

    def navigate_slash_menu(self, key: str) -> None:
        if self._editor.navigate_slash_menu(key, self._surface.names):
            self._repaint()

    # --- popup frame rendering -----------------------------------------------

    def popup_menu_frame_lines(self, *, width: int, max_rows: int) -> list[FrameLine]:
        """Return the active in-frame completion popup (slash menu or editor).

        The slash menu keeps priority when it is open; otherwise the editor
        autocomplete popup (``@`` file picker or Tab path completion) draws in
        the same rows. The two never co-open, mirroring Pi.
        """

        if self._editor.slash_menu_open:
            return self._slash_menu_frame_lines(width=width, max_rows=max_rows)
        if self._editor.autocomplete_open:
            return self._autocomplete_frame_lines(width=width, max_rows=max_rows)
        return []

    def _autocomplete_frame_lines(
        self, *, width: int, max_rows: int
    ) -> list[FrameLine]:
        items = self._editor.autocomplete_items
        if not self._editor.autocomplete_open or not items or max_rows <= 0:
            return []
        menu_cap = self._max_visible if self._max_visible > 0 else 5
        visible_count = min(len(items), max_rows, menu_cap)
        start = max(
            0,
            min(
                self._editor.autocomplete_selection - (visible_count // 2),
                max(0, len(items) - visible_count),
            ),
        )
        visible = items[start : start + visible_count]
        total = len(items)
        lines: list[FrameLine] = []
        for offset, item in enumerate(visible, start=start):
            prefix = "→ " if offset == self._editor.autocomplete_selection else "  "
            label = item.label
            description_start = len(prefix) + len(label)
            line = f"{prefix}{label}"
            # Show the full inserted value (dimmed) when it differs from the
            # short label and the row has room, so a scoped/quoted path is
            # legible before acceptance.
            if item.value not in {label, f"@{label}"} and width > 40:
                spacing = " " * max(1, 24 - len(line))
                remaining = width - len(line) - len(spacing) - 2
                if remaining > 6:
                    line = f"{line}{spacing}{item.value[:remaining]}"
                    description_start = len(prefix) + len(label) + len(spacing)
            lines.append(
                FrameLine(
                    clip_text(line, width),
                    "slash_menu_selected"
                    if offset == self._editor.autocomplete_selection
                    else "slash_menu",
                    {"description_start": description_start},
                )
            )
        if start > 0 or start + visible_count < total:
            lines.append(
                FrameLine(
                    clip_text(
                        f"  ({self._editor.autocomplete_selection + 1}/{total})", width
                    ),
                    "slash_menu_scroll",
                )
            )
        return lines

    def _slash_menu_frame_lines(self, *, width: int, max_rows: int) -> list[FrameLine]:
        matches = self.filtered_commands()
        if not self._editor.slash_menu_open or not matches or max_rows <= 0:
            return []
        menu_cap = self._max_visible if self._max_visible > 0 else 5
        visible_count = min(len(matches), max_rows, menu_cap)
        start = max(
            0,
            min(
                self._editor.slash_menu_selection - (visible_count // 2),
                max(0, len(matches) - visible_count),
            ),
        )
        visible = matches[start : start + visible_count]
        lines: list[FrameLine] = []
        total = len(matches)
        primary_width = self._slash_menu_primary_column_width(matches)
        for offset, command in enumerate(visible, start=start):
            description = self._surface.descriptions.get(command, "")
            display_command = command[1:] if command.startswith("/") else command
            prefix = "→ " if offset == self._editor.slash_menu_selection else "  "
            max_primary_width = max(1, primary_width - 2)
            display_command = display_command[:max_primary_width]
            spacing = " " * max(1, primary_width - len(display_command))
            description_start = len(prefix) + len(display_command)
            line = f"{prefix}{display_command}{spacing}"
            if description and width > 40:
                remaining = width - len(line) - 2
                if remaining > 10:
                    line = f"{line}{description[:remaining]}"
            lines.append(
                FrameLine(
                    clip_text(line, width),
                    "slash_menu_selected"
                    if offset == self._editor.slash_menu_selection
                    else "slash_menu",
                    {"description_start": description_start},
                )
            )
        if start > 0 or start + visible_count < total:
            lines.append(
                FrameLine(
                    clip_text(
                        f"  ({self._editor.slash_menu_selection + 1}/{total})", width
                    ),
                    "slash_menu_scroll",
                )
            )
        return lines

    @staticmethod
    def _slash_menu_primary_column_width(matches: tuple[str, ...]) -> int:
        widest = 0
        for command in matches:
            display_command = command[1:] if command.startswith("/") else command
            widest = max(widest, len(display_command) + 2)
        return max(12, min(32, widest))
