"""pipy's built-in `@`-mention and path autocomplete, in Pi's provider shape.

Implements the same duck-typed provider interface an extension supplies to
`api.registerAutocompleteProvider`, so the built-in completion and an extension's
travel the same code path in the shell -- there is no privileged built-in branch.
Both snake_case and camelCase spellings are bound because Pi's interface is
camelCase and pipy's own callers are not.

It takes the workspace root by value rather than a handle to the terminal UI: the
root is the only thing completion needs, and `ToolLoopTerminalUi.cwd` is assigned
once at construction and never reassigned, so a copy cannot drift.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pipy_harness.native.autocomplete_provider import (
    AutocompleteApplyResult,
    AutocompleteContext,
    AutocompleteSuggestion,
    line_col_to_cursor,
)
from pipy_harness.native.editor_completion import (
    at_candidates,
    extract_at_token,
    extract_path_prefix,
    path_candidates,
)
from pipy_harness.native.editor_state import CompletionItem


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
