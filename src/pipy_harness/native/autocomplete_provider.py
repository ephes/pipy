"""Pi-shaped autocomplete provider helpers for the product TUI."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from pipy_harness.native.editor_completion import CompletionItem


@dataclass(frozen=True, slots=True)
class AutocompleteSuggestion:
    items: tuple[CompletionItem, ...]
    prefix: str
    token_start: int
    mode: str


@dataclass(frozen=True, slots=True)
class AutocompleteApplyResult:
    text: str
    cursor: int


@dataclass(frozen=True, slots=True)
class AutocompleteContext:
    force: bool = False
    signal: object | None = None


def call_provider_method(provider: object, snake: str, camel: str, *args):
    method = getattr(provider, snake, None) or getattr(provider, camel, None)
    if not callable(method):
        raise AttributeError(snake)
    return method(*args)


def coerce_completion_item(value: object) -> CompletionItem | None:
    if isinstance(value, CompletionItem):
        return value
    if isinstance(value, dict):
        raw_value = value.get("value")
        raw_label = value.get("label", raw_value)
    elif isinstance(value, tuple) and value:
        raw_value = value[0]
        raw_label = value[1] if len(value) > 1 else value[0]
    else:
        raw_value = value
        raw_label = value
    text = str(raw_value)[:4096]
    label = str(raw_label)[:512]
    if not text or not label:
        return None
    return CompletionItem(text, label)


def coerce_completion_items(values: object) -> tuple[CompletionItem, ...]:
    if values is None:
        return ()
    try:
        iterator = iter(cast(Iterable[object], values))
    except TypeError:
        item = coerce_completion_item(values)
        return () if item is None else (item,)
    items: list[CompletionItem] = []
    for raw in iterator:
        item = coerce_completion_item(raw)
        if item is not None:
            items.append(item)
    return tuple(items)


def coerce_suggestion(value: object) -> AutocompleteSuggestion | None:
    if isinstance(value, AutocompleteSuggestion):
        return value if value.items else None
    if value is None:
        return None
    if isinstance(value, dict):
        items = coerce_completion_items(value.get("items"))
        prefix = str(value.get("prefix", ""))
        token_start = int(cast(Any, value.get("token_start", value.get("tokenStart", -1))))
        mode = str(value.get("mode", "at"))
    else:
        items = coerce_completion_items(getattr(value, "items", None))
        prefix = str(getattr(value, "prefix", ""))
        token_start = int(getattr(value, "token_start", getattr(value, "tokenStart", -1)))
        mode = str(getattr(value, "mode", "at"))
    if not items or token_start < 0:
        return None
    return AutocompleteSuggestion(items, prefix, token_start, mode)


def coerce_apply_result(value: object) -> AutocompleteApplyResult | None:
    if isinstance(value, AutocompleteApplyResult):
        return value
    if isinstance(value, dict):
        lines = value.get("lines")
        cursor_line = value.get("cursorLine", value.get("cursor_line", 0))
        cursor_col = value.get("cursorCol", value.get("cursor_col", 0))
        if isinstance(lines, list):
            rendered_lines = [str(line) for line in lines]
            text = "\n".join(rendered_lines)
            offset = sum(len(line) + 1 for line in rendered_lines[: int(cast(Any, cursor_line))]) + int(cast(Any, cursor_col))
            return AutocompleteApplyResult(text, max(0, min(len(text), offset)))
        if "text" in value:
            text_value = str(value["text"])
            return AutocompleteApplyResult(text_value, int(cast(Any, value.get("cursor", len(text_value)))))
    attr_text = getattr(value, "text", None)
    if attr_text is not None:
        rendered = str(attr_text)
        return AutocompleteApplyResult(rendered, int(cast(Any, getattr(value, "cursor", len(rendered)))))
    return None


def cursor_to_line_col(text: str, cursor: int) -> tuple[tuple[str, ...], int, int]:
    safe_cursor = max(0, min(len(text), cursor))
    before = text[:safe_cursor]
    lines = tuple(text.split("\n"))
    return lines, before.count("\n"), len(before.rsplit("\n", 1)[-1])


def line_col_to_cursor(lines: Sequence[str], cursor_line: int, cursor_col: int) -> int:
    return sum(len(line) + 1 for line in lines[:cursor_line]) + cursor_col
