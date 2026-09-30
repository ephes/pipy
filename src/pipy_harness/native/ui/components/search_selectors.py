"""Pi's searchable model and thinking selectors.

``ModelSearchSelector`` ports ``ModelSelectorComponent`` and
``ThinkingSearchSelector`` ports ``ThinkingSelectorComponent`` (pi-mono
``1b347794e``, ``packages/coding-agent/src/modes/interactive/components``).
Each is a pure state machine: :meth:`handle_key` applies one decoded key and
returns a :class:`SearchSelectorClose` when the selector finishes,
:meth:`paste` inserts pasted text into the search input, and :meth:`render`
draws Pi's rows for a width and a :class:`ChromeStyle`. The modal driver owns
the paint lock, the repaint and the key loop.

The model selector lists only the models it is given (the caller passes the
available models, Pi ``getAvailableSnapshot``), sorts them current first,
then the saved default, then by provider, and filters them with
:func:`fuzzy_filter` over Pi's ``getModelSelectorSearchText``. With scoped
models, Tab switches between the scoped and the full list. Pi's background
catalog refresh is not ported (backlog DF1-F7b).
"""

from __future__ import annotations

import functools
from collections.abc import Sequence
from dataclasses import dataclass

from pipy_harness.native.ansi_wrap import wrap_text_with_ansi
from pipy_harness.native.chrome import ChromeStyle
from pipy_harness.native.frame_renderer import FrameLine, clip_custom_text, clip_text
from pipy_harness.native.fuzzy import fuzzy_filter, js_trim
from pipy_harness.native.keybindings import KeybindingsManager, key_display_text
from pipy_harness.native.overlay_state import OverlayState
from pipy_harness.native.thinking import THINKING_LEVEL_DESCRIPTIONS
from pipy_harness.native.tools.collation import locale_compare_key
from pipy_harness.native.ui.components.search_input import SearchInput
from pipy_harness.native.ui.components.select_list import (
    SelectItem,
    SelectList,
    SelectListLayout,
)
from pipy_harness.native.ui.key_specs import matches_key_specs, resolved_key_specs

_MODEL_MAX_VISIBLE = 10
_THINKING_LAYOUT = SelectListLayout(
    min_primary_column_width=12, max_primary_column_width=32
)


@dataclass(frozen=True, slots=True)
class SearchSelectorKeys:
    """Resolved key specs and their display texts for one selector."""

    up: Sequence[str]
    down: Sequence[str]
    confirm: Sequence[str]
    cancel: Sequence[str]
    tab: Sequence[str]
    save: Sequence[str]
    confirm_text: str
    cancel_text: str
    save_text: str
    tab_text: str
    cycle_text: str = ""


def search_selector_keys(
    save_action: str, manager: KeybindingsManager | None
) -> SearchSelectorKeys:
    """Resolve the selector keys and Pi's ``keyDisplayText`` hint texts."""

    def specs(action: str) -> list[str]:
        return resolved_key_specs(action, manager)

    return SearchSelectorKeys(
        up=specs("tui.select.up"),
        down=specs("tui.select.down"),
        confirm=specs("tui.select.confirm"),
        cancel=specs("tui.select.cancel"),
        tab=specs("tui.input.tab"),
        save=specs(save_action),
        confirm_text=key_display_text(specs("tui.select.confirm")),
        cancel_text=key_display_text(specs("tui.select.cancel")),
        save_text=key_display_text(specs(save_action)),
        # Pi `keyHint` uses `keyText`, which is not capitalized.
        tab_text=key_display_text(specs("tui.input.tab"), capitalize=False),
        cycle_text=key_display_text(specs("app.thinking.cycle")),
    )


@dataclass(frozen=True, slots=True)
class SearchSelectorClose:
    """The selector finished: ``value`` is the choice (``None``: cancelled).

    ``save`` is ``True`` when it was chosen with the save key (Pi
    ``app.models.save`` / ``app.thinking.save``), which also persists it as
    the default.
    """

    value: object | None
    save: bool = False


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """One model row: Pi ``ModelItem`` (``provider``, ``id``, the model name)."""

    provider: str
    model_id: str
    name: str

    @property
    def reference(self) -> str:
        return f"{self.provider}/{self.model_id}"


def model_search_text(choice: ModelChoice) -> str:
    """Pi ``getModelSearchText`` (argument completion)."""

    name = f" {choice.name}" if choice.name else ""
    return (
        f"{choice.model_id} {choice.provider} {choice.provider}/{choice.model_id} "
        f"{choice.provider} {choice.model_id}{name}"
    )


def model_selector_search_text(choice: ModelChoice) -> str:
    """Pi ``getModelSelectorSearchText``: the bare id is not in front, so a
    provider-prefixed query ranks the provider's own model before a proxy
    provider's ``openrouter/openai/...`` id."""

    name = f" {choice.name}" if choice.name else ""
    return (
        f"{choice.provider} {choice.provider}/{choice.model_id} "
        f"{choice.provider} {choice.model_id}{name}"
    )


def _same_model(a: ModelChoice | None, b: ModelChoice) -> bool:
    """Pi ``modelsAreEqual``: provider and id."""

    return a is not None and a.provider == b.provider and a.model_id == b.model_id


def _locale_compare(a: str, b: str) -> int:
    """``String.prototype.localeCompare`` (the ``ls`` ICU approximation)."""

    left, right = locale_compare_key(a), locale_compare_key(b)
    return (left > right) - (left < right)


class ModelSearchSelector:
    """Pi ``ModelSelectorComponent`` without the catalog refresh."""

    def __init__(
        self,
        *,
        models: Sequence[ModelChoice],
        scoped_models: Sequence[ModelChoice],
        current: ModelChoice | None,
        default: ModelChoice | None,
        keys: SearchSelectorKeys,
        initial_search: str = "",
    ) -> None:
        self._current = current
        self._default = default
        self._keys = keys
        self._scoped = tuple(scoped_models)
        self._all = tuple(self._sort(models))
        self._scope = "scoped" if self._scoped else "all"
        self._active = self._scoped if self._scope == "scoped" else self._all
        self._filtered: tuple[ModelChoice, ...] = self._active
        self._selected = 0
        current_index = self._index_of_current(self._filtered)
        if current_index >= 0:
            self._selected = current_index
        self._input = SearchInput(initial_search)
        if initial_search:
            self._filter(initial_search)

    # -- state -----------------------------------------------------------------

    @property
    def query(self) -> str:
        return self._input.value

    @property
    def scope(self) -> str:
        return self._scope

    def filtered(self) -> tuple[ModelChoice, ...]:
        return self._filtered

    @property
    def selected_index(self) -> int:
        return self._selected

    def _sort(self, models: Sequence[ModelChoice]) -> list[ModelChoice]:
        def compare(a: ModelChoice, b: ModelChoice) -> int:
            a_current = _same_model(self._current, a)
            b_current = _same_model(self._current, b)
            if a_current and not b_current:
                return -1
            if b_current and not a_current:
                return 1
            a_default = _same_model(self._default, a)
            b_default = _same_model(self._default, b)
            if a_default and not b_default:
                return -1
            if b_default and not a_default:
                return 1
            return _locale_compare(a.provider, b.provider)

        return sorted(models, key=functools.cmp_to_key(compare))

    def _index_of_current(self, items: Sequence[ModelChoice]) -> int:
        for index, item in enumerate(items):
            if _same_model(self._current, item):
                return index
        return -1

    def _is_default(self, choice: ModelChoice) -> bool:
        return _same_model(self._default, choice)

    def _is_default_search(self, query: str) -> bool:
        normalized = js_trim(query).lower()
        return bool(normalized) and "default".startswith(normalized)

    def _filter(self, query: str) -> None:
        if query:
            filtered = fuzzy_filter(
                self._active,
                query,
                lambda item: (
                    model_selector_search_text(item)
                    + (" default" if self._is_default(item) else "")
                ),
            )
            if self._is_default_search(query):
                defaults = [item for item in self._active if self._is_default(item)]
                keys = {(item.provider, item.model_id) for item in defaults}
                filtered = [
                    *defaults,
                    *(
                        item
                        for item in filtered
                        if (item.provider, item.model_id) not in keys
                    ),
                ]
            self._filtered = tuple(filtered)
            self._selected = 0
        else:
            self._filtered = self._active
            self._selected = min(self._selected, max(0, len(self._filtered) - 1))

    def _set_scope(self, scope: str) -> None:
        if self._scope == scope:
            return
        self._scope = scope
        self._active = self._scoped if scope == "scoped" else self._all
        current_index = self._index_of_current(self._active)
        self._selected = current_index if current_index >= 0 else 0
        self._filter(self._input.value)

    # -- input -----------------------------------------------------------------

    def handle_key(self, key: str) -> SearchSelectorClose | None:
        """Pi ``handleInput``, in Pi's order of key checks."""

        keys = self._keys
        if matches_key_specs(key, keys.tab):
            if self._scoped:
                self._set_scope("scoped" if self._scope == "all" else "all")
            return None
        if matches_key_specs(key, keys.up):
            self._move(-1)
            return None
        if matches_key_specs(key, keys.down):
            self._move(1)
            return None
        if matches_key_specs(key, keys.confirm):
            return self._choose(save=False)
        if matches_key_specs(key, keys.cancel):
            return SearchSelectorClose(None)
        if matches_key_specs(key, keys.save):
            return self._choose(save=True)
        self._input.handle_key(key)
        self._filter(self._input.value)
        return None

    def _move(self, delta: int) -> None:
        """Up/Down wrap at the ends; nothing happens on an empty list."""

        count = len(self._filtered)
        if count == 0:
            return
        if delta < 0:
            self._selected = count - 1 if self._selected == 0 else self._selected - 1
        else:
            self._selected = 0 if self._selected == count - 1 else self._selected + 1

    def _choose(self, *, save: bool) -> SearchSelectorClose | None:
        if 0 <= self._selected < len(self._filtered):
            return SearchSelectorClose(self._filtered[self._selected], save=save)
        return None

    def paste(self, text: str) -> None:
        self._input.insert(text)
        self._filter(self._input.value)

    # -- rendering -------------------------------------------------------------

    def render(self, width: int, style: ChromeStyle) -> list[str]:
        lines: list[str] = [style.border("─" * max(1, width)), ""]
        if self._scoped:
            all_text = (
                style.accent("all") if self._scope == "all" else style.muted("all")
            )
            scoped_text = (
                style.accent("scoped")
                if self._scope == "scoped"
                else style.muted("scoped")
            )
            lines += _text(
                f"{style.muted('Scope: ')}{all_text}{style.muted(' | ')}{scoped_text}",
                width,
            )
            lines += _text(
                style.dim(self._keys.tab_text) + style.muted(" scope (all/scoped)"),
                width,
            )
        else:
            lines += _text(
                style.warning(
                    "Only showing models from configured providers. "
                    "Use /login to add providers."
                ),
                width,
            )
        lines.append("")
        lines.append(self._input.render(width, style))
        lines.append("")
        lines += self._list_lines(width, style)
        lines.append("")
        lines += _text(
            style.dim(
                f"  {self._keys.confirm_text} to select · {self._keys.save_text} to "
                f"set as default · {self._keys.cancel_text} to cancel"
            ),
            width,
        )
        lines.append(style.border("─" * max(1, width)))
        return lines

    def _list_lines(self, width: int, style: ChromeStyle) -> list[str]:
        items = self._filtered
        start = max(
            0,
            min(
                self._selected - _MODEL_MAX_VISIBLE // 2,
                len(items) - _MODEL_MAX_VISIBLE,
            ),
        )
        end = min(start + _MODEL_MAX_VISIBLE, len(items))
        lines: list[str] = []
        for index in range(start, end):
            item = items[index]
            selected = index == self._selected
            cursor = style.accent("→ ") if selected else "  "
            marker = style.accent("✓ ") if _same_model(self._current, item) else "  "
            model_text = style.accent(item.model_id) if selected else item.model_id
            badge = style.muted(f"[{item.provider}]")
            default_badge = style.muted(" · default") if self._is_default(item) else ""
            lines += _text(
                f"{cursor}{marker}{model_text} {badge}{default_badge}", width
            )
        if start > 0 or end < len(items):
            lines += _text(style.muted(f"  ({self._selected + 1}/{len(items)})"), width)
        if not items:
            lines += _text(style.muted("  No matching models"), width)
        else:
            lines.append("")
            lines += _text(
                style.muted(f"  Model Name: {items[self._selected].name}"), width
            )
        return lines


class ThinkingSearchSelector:
    """Pi ``ThinkingSelectorComponent``: a searchable thinking-level list."""

    def __init__(
        self,
        *,
        levels: Sequence[str],
        current: str,
        default: str | None,
        keys: SearchSelectorKeys,
    ) -> None:
        self._keys = keys
        self._all = tuple(
            SelectItem(
                value=level,
                label=f"{'✓ ' if level == current else '  '}{level}",
                description=(
                    f"{THINKING_LEVEL_DESCRIPTIONS.get(level, '')} · default"
                    if level == default
                    else THINKING_LEVEL_DESCRIPTIONS.get(level, "")
                ),
            )
            for level in levels
        )
        self._input = SearchInput()
        self._list = self._build_list(self._all, current)

    @property
    def query(self) -> str:
        return self._input.value

    def items(self) -> tuple[SelectItem, ...]:
        return self._list.items

    def selected_value(self) -> str | None:
        item = self._list.selected_item()
        return item.value if item is not None else None

    def _build_list(
        self, items: Sequence[SelectItem], preselect: str | None
    ) -> SelectList:
        select_list = SelectList(items, max(1, len(items)), _THINKING_LAYOUT)
        for index, item in enumerate(items):
            if item.value == preselect:
                select_list.set_selected_index(index)
                break
        return select_list

    def _apply_filter(self, query: str) -> None:
        filtered = (
            fuzzy_filter(
                self._all,
                query,
                lambda item: f"{item.value} {item.description or ''}",
            )
            if query
            else list(self._all)
        )
        self._list = self._build_list(filtered, self.selected_value())

    def handle_key(self, key: str) -> SearchSelectorClose | None:
        keys = self._keys
        if keys.save and matches_key_specs(key, keys.save):
            item = self._list.selected_item()
            return SearchSelectorClose(item.value, save=True) if item else None
        if matches_key_specs(key, keys.up):
            self._list.move(-1)
            return None
        if matches_key_specs(key, keys.down):
            self._list.move(1)
            return None
        if matches_key_specs(key, keys.confirm):
            item = self._list.selected_item()
            return SearchSelectorClose(item.value) if item else None
        if matches_key_specs(key, keys.cancel):
            return SearchSelectorClose(None)
        self._input.handle_key(key)
        self._apply_filter(self._input.value)
        return None

    def paste(self, text: str) -> None:
        self._input.insert(text)
        self._apply_filter(self._input.value)

    def render(self, width: int, style: ChromeStyle) -> list[str]:
        lines: list[str] = [style.border("─" * max(1, width)), ""]
        lines += _text("Thinking Level", width)
        lines.append("")
        lines += _text(
            f"{self._keys.cycle_text} cycles thinking levels in-session", width
        )
        lines.append("")
        lines.append(self._input.render(width, style))
        lines.append("")
        lines += self._list.render(width, style)
        lines.append("")
        lines += _text(
            style.dim(
                f"  {self._keys.confirm_text} to select · {self._keys.save_text} to "
                f"set as default · {self._keys.cancel_text} to cancel"
            ),
            width,
        )
        lines.append(style.border("─" * max(1, width)))
        return lines


def _text(text: str, width: int) -> list[str]:
    """Pi ``Text(text, 0, 0)``: the text wrapped at the width."""

    return wrap_text_with_ansi(text, max(1, width)) or [""]


SearchSelector = ModelSearchSelector | ThinkingSearchSelector


def search_selector_region_lines(
    overlays: OverlayState,
    *,
    width: int,
    height: int,
    footer_lines: tuple[str, str],
    style: ChromeStyle,
) -> list[FrameLine]:
    """The active selector in place of the editor, above the two footer rows.

    Pi swaps the selector into the editor container, so the footer stays
    below it. Rows beyond the live region's height are dropped from the
    bottom, like the other overlays' clipping.
    """

    component = overlays.search_selector
    if not isinstance(component, ModelSearchSelector | ThinkingSearchSelector):
        return []
    body = component.render(width, style)[: max(1, height - 2)]
    return [
        *(FrameLine(clip_custom_text(line, width), "normal") for line in body),
        FrameLine(clip_text(footer_lines[0], width), "footer"),
        FrameLine(clip_text(footer_lines[1], width), "footer"),
    ]
