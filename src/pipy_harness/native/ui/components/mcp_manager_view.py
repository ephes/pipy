"""The view of the ``/mcp`` manager (Pi ``McpManagerView`` in ``ext/ui.ts``).

One component shows, one after another, the screens of the manager: a menu
that is built again while servers connect, a status line while an operation
runs, and the sign-in screen with the authorization URL and an input for the
pasted redirect URL. The flow (``native/mcp/manager_flow.py``) tells it what
to show and is called back with what the user chose; the modal driver feeds
it keys and pasted text and asks it for its lines.

Pi's screens are promises that an async function awaits. Here a screen takes
the function to call with its outcome, and calls it at most once. The
callback runs inside ``handle_key``, after the view let go of its lock, so it
may show the next screen and may block.

Threads. Keys, pastes and the calls of the flow arrive on the session
thread. ``render`` may run on any thread that paints the frame, so the
view's state is guarded by a lock of its own, which is never held while a
callback runs or a menu is built.

Different from Pi:

- The authorization URL is plain text in the accent colour, not an OSC 8
  hyperlink: pipy's frame carries no hyperlinks. A terminal that recognises
  URLs makes it clickable as long as it fits on one line; the copy key is
  for the rest. There is no "click to open" hint for the same reason.
- The copy key is ``app.message.copy`` as in Pi, but pipy binds that action
  to nothing else: in Pi it also copies a message in the editor.
- The title is in the accent colour and not bold as well, and the note
  after a copy is not in a colour of its own: the chrome style has neither.
- The input is the selectors' search input (``search_input.py``), with the
  editing keys that has.
- A live menu is drawn again only when what it shows changed.
- pipy's live region shows only as many rows as the terminal has and drops
  the rest from the bottom, where Pi's output scrolls. So a screen that is
  taller than the rows it is given (``render_within``) shows a window of
  the text above its controls, with a line that says which lines are shown,
  and ``tui.select.pageUp``/``pageDown`` scroll that window; a long list
  shows fewer items. The list or the input and the key hints stay on the
  screen.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from pipy_harness.native.ansi_wrap import wrap_text_with_ansi
from pipy_harness.native.chrome import ChromeStyle
from pipy_harness.native.keybindings import KeybindingsManager, key_display_text
from pipy_harness.native.mcp.manager_menus import McpMenu
from pipy_harness.native.ui.components.search_input import SearchInput
from pipy_harness.native.ui.components.select_list import SelectItem, SelectList
from pipy_harness.native.ui.key_specs import matches_key_specs, resolved_key_specs

_MAX_VISIBLE_ITEMS: Final = 12
# The rows a menu's text above a long list keeps: two lines and the line
# that says which lines are shown.
_TEXT_ROWS_KEPT: Final = 3
_LOADING_TITLE: Final = "MCP servers"
_LOADING_TEXT: Final = "Loading…"
_NOTHING_TO_SHOW: Final = "Nothing to show."
_APPROVE_TEXT: Final = "Approve access in your browser. If it did not open, visit:"
_PASTE_TEXT: Final = (
    "If the browser runs on another machine, paste the URL it was redirected to:"
)
_COPIED_TEXT: Final = "Copied URL to clipboard"

# Copies a text to the clipboard; returns ``None``, or why it could not.
CopyText = Callable[[str], str | None]
Effect = Callable[[], None]


@dataclass(frozen=True, slots=True)
class McpManagerKeys:
    """The key specs of the view and how the hints name them."""

    up: tuple[str, ...]
    down: tuple[str, ...]
    confirm: tuple[str, ...]
    cancel: tuple[str, ...]
    copy: tuple[str, ...]
    page_up: tuple[str, ...]
    page_down: tuple[str, ...]
    confirm_text: str
    cancel_text: str
    copy_text: str
    page_text: str


def mcp_manager_keys(manager: KeybindingsManager | None) -> McpManagerKeys:
    """The view's keys as they are bound, with Pi's ``keyText`` names."""

    def specs(action: str) -> tuple[str, ...]:
        return tuple(resolved_key_specs(action, manager))

    def text(keys: tuple[str, ...]) -> str:
        return key_display_text(list(keys), capitalize=False)

    confirm = specs("tui.select.confirm")
    cancel = specs("tui.select.cancel")
    copy = specs("app.message.copy")
    page_up = specs("tui.select.pageUp")
    page_down = specs("tui.select.pageDown")
    return McpManagerKeys(
        up=specs("tui.select.up"),
        down=specs("tui.select.down"),
        confirm=confirm,
        cancel=cancel,
        copy=copy,
        page_up=page_up,
        page_down=page_down,
        confirm_text=text(confirm),
        cancel_text=text(cancel),
        copy_text=text(copy),
        page_text=f"{text(page_up)}/{text(page_down)}",
    )


class _Scroll:
    """Where the window over a screen's text starts, when the text does not
    fit (``render_within``), and how many lines the window had when it was
    last drawn."""

    scroll: int = 0
    page: int = 0


class _Menu(_Scroll):
    """A menu on the screen: the value it was built from and the list."""

    def __init__(
        self,
        build: Callable[[], McpMenu],
        on_choice: Callable[[str | None], None],
        live: bool,
        menu: McpMenu,
    ) -> None:
        self.build = build
        self.on_choice = on_choice
        self.live = live
        self.settled = False
        # The value of the selected item, kept when the menu is built again.
        self.selected: str | None = None
        self.show(menu)

    def show(self, menu: McpMenu) -> None:
        self.menu = menu
        wanted = self.selected if self.selected is not None else menu.selected
        self.list = SelectList(
            [SelectItem(i.value, i.label, i.description) for i in menu.items],
            min(len(menu.items), _MAX_VISIBLE_ITEMS),
        )
        for index, item in enumerate(menu.items):
            if item.value == wanted:
                self.list.set_selected_index(index)
                break
        self.note_selection()

    def note_selection(self) -> None:
        item = self.list.selected_item()
        self.selected = None if item is None else item.value


class _Status(_Scroll):
    def __init__(
        self, title: str, message: str, on_cancel: Callable[[], None] | None
    ) -> None:
        self.title = title
        self.message = message
        self.on_cancel = on_cancel


class _RedirectUrl(_Scroll):
    """The sign-in screen: the URL, and the input for the redirect URL."""

    def __init__(
        self, title: str, url: str, on_answer: Callable[[str | None], None]
    ) -> None:
        self.title = title
        self.url = url
        self.on_answer = on_answer
        self.settled = False
        self.input = SearchInput()
        # What a copy said; ``None`` shows the hint for the copy key.
        self.copied: str | None = None


class McpManagerView:
    """The manager's one component: ``menu``, ``status`` and
    ``redirect_url`` replace what it shows."""

    def __init__(self, keys: McpManagerKeys, *, copy: CopyText) -> None:
        self._keys = keys
        self._copy = copy
        self._lock = threading.Lock()
        self._screen: _Menu | _Status | _RedirectUrl = _Status(
            _LOADING_TITLE, _LOADING_TEXT, None
        )
        self._closed = False
        self._dirty = True

    # -- what the flow shows -------------------------------------------------

    def menu(
        self,
        build: Callable[[], McpMenu],
        on_choice: Callable[[str | None], None],
        *,
        live: bool = False,
    ) -> None:
        """Show the menu ``build`` returns. ``on_choice`` gets the value of
        the chosen item, or ``None`` when the user cancelled. A ``live``
        menu is built again by every ``refresh`` and keeps the selected
        item."""

        self._show(_Menu(build, on_choice, live, build()))

    def status(
        self, title: str, message: str, on_cancel: Callable[[], None] | None = None
    ) -> None:
        """Show a message while an operation runs. With ``on_cancel`` the
        cancel key calls it, each time it is pressed."""

        self._show(_Status(title, message, on_cancel))

    def redirect_url(
        self,
        title: str,
        authorization_url: str,
        on_answer: Callable[[str | None], None],
    ) -> None:
        """Show the authorization URL and an input for the redirect URL.
        ``on_answer`` gets the text the user submitted, or ``None`` when the
        user cancelled. Showing another screen ends this one without an
        answer, which is what happens when the browser arrives first."""

        self._show(_RedirectUrl(title, authorization_url, on_answer))

    def refresh(self) -> None:
        """Build a live menu again and show it if it changed."""

        with self._lock:
            screen = self._screen
        if not isinstance(screen, _Menu) or not screen.live or screen.settled:
            return
        menu = screen.build()
        with self._lock:
            if self._screen is screen and menu != screen.menu:
                screen.show(menu)
                self._dirty = True

    def close(self) -> None:
        """End the view; the key loop stops."""

        with self._lock:
            self._closed = True

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def take_dirty(self) -> bool:
        """Whether what the view shows changed since this was last asked."""

        with self._lock:
            dirty, self._dirty = self._dirty, False
            return dirty

    def _show(self, screen: _Menu | _Status | _RedirectUrl) -> None:
        with self._lock:
            self._screen = screen
            self._dirty = True

    # -- input ---------------------------------------------------------------

    def handle_key(self, key: str) -> None:
        """Apply one decoded key. A callback of the screen runs here, after
        the view's state was updated."""

        with self._lock:
            effect = self._key_effect(key)
            self._dirty = True
        if effect is not None:
            effect()

    def paste(self, text: str) -> None:
        """Insert pasted text into the input of the sign-in screen."""

        with self._lock:
            screen = self._screen
            if isinstance(screen, _RedirectUrl):
                screen.input.insert(text)
                self._dirty = True

    def _key_effect(self, key: str) -> Effect | None:
        screen = self._screen
        if self._scrolled(screen, key):
            return None
        if isinstance(screen, _Menu):
            return self._menu_key(screen, key)
        if isinstance(screen, _Status):
            if screen.on_cancel is not None and self._is(key, self._keys.cancel):
                return screen.on_cancel
            return None
        return self._redirect_key(screen, key)

    def _is(self, key: str, specs: tuple[str, ...]) -> bool:
        return matches_key_specs(key, specs)

    def _scrolled(self, screen: _Scroll, key: str) -> bool:
        """Move the window over the text by a page; whether ``key`` did.
        Only while the text does not fit, and never for a key the screen
        uses (a user may bind ``app.message.copy`` to a page key). The next
        drawing keeps the window inside the text."""

        keys = self._keys
        if isinstance(screen, _Menu):
            own = (*keys.up, *keys.down, *keys.confirm, *keys.cancel)
        elif isinstance(screen, _Status):
            own = keys.cancel
        else:
            own = (*keys.confirm, *keys.cancel, *keys.copy)
        if screen.page == 0 or self._is(key, own):
            return False
        if self._is(key, keys.page_down):
            screen.scroll += screen.page
        elif self._is(key, self._keys.page_up):
            screen.scroll = max(0, screen.scroll - screen.page)
        else:
            return False
        return True

    def _menu_key(self, screen: _Menu, key: str) -> Effect | None:
        keys = self._keys
        if screen.settled:
            return None
        if self._is(key, keys.cancel):
            screen.settled = True
            return lambda: screen.on_choice(None)
        if not screen.menu.items:
            return None
        if self._is(key, keys.up):
            screen.list.move(-1)
        elif self._is(key, keys.down):
            screen.list.move(1)
        elif self._is(key, keys.confirm):
            screen.settled = True
            value = screen.selected
            return lambda: screen.on_choice(value)
        screen.note_selection()
        return None

    def _redirect_key(self, screen: _RedirectUrl, key: str) -> Effect | None:
        keys = self._keys
        if self._is(key, keys.confirm):
            value = screen.input.value.strip()
            if not value or screen.settled:
                return None
            screen.settled = True
            return lambda: screen.on_answer(value)
        if self._is(key, keys.cancel):
            if screen.settled:
                return None
            screen.settled = True
            return lambda: screen.on_answer(None)
        if self._is(key, keys.copy):
            return lambda: self._copy_url(screen)
        screen.input.handle_key(key)
        return None

    def _copy_url(self, screen: _RedirectUrl) -> None:
        failure = self._copy(screen.url)
        with self._lock:
            screen.copied = _COPIED_TEXT if failure is None else failure
            self._dirty = True

    # -- drawing -------------------------------------------------------------

    def render(self, width: int, style: ChromeStyle) -> list[str]:
        """The lines of the screen that is shown, none wider than ``width``."""

        return self.render_within(width, None, style)

    def render_within(
        self, width: int, height: int | None, style: ChromeStyle
    ) -> list[str]:
        """The screen in at most ``height`` lines where that can be done:
        the text above the controls (a server's error, the authorization
        URL) is cut short and a long list shows fewer items, so that the
        list or the input and the key hints stay on the screen."""

        with self._lock:
            screen = self._screen
            if isinstance(screen, _Menu):
                return self._render_menu(screen, width, height, style)
            if isinstance(screen, _Status):
                return self._render_status(screen, width, height, style)
            return self._render_redirect(screen, width, height, style)

    def _hint(self, key_text: str, description: str, style: ChromeStyle) -> str:
        """Pi ``keyHint``."""

        return style.dim(key_text) + style.muted(f" {description}")

    def _render_menu(
        self, screen: _Menu, width: int, height: int | None, style: ChromeStyle
    ) -> list[str]:
        menu = screen.menu
        keys = self._keys
        above: list[str] = []
        if menu.details:
            above += _text(menu.details, width, style.muted)
        if menu.error:
            above += _text(menu.error, width, style.error)
        cancel = self._hint(keys.cancel_text, menu.cancel_label, style)
        if not menu.items:
            empty = _text(menu.empty or _NOTHING_TO_SHOW, width, style.muted)
            return _frame(
                menu.title,
                [],
                above,
                ["", *empty],
                cancel,
                *(width, height, style),
                window=(screen, keys.page_text),
            )
        confirm = self._hint(keys.confirm_text, menu.confirm_label, style)
        footer = f"{confirm}{style.dim(' • ')}{cancel}"
        visible = min(len(menu.items), _MAX_VISIBLE_ITEMS)
        if height is not None:
            # The rows left for the list. One that does not show every item
            # needs a row for its position line.
            room = height - len(
                _frame(menu.title, [], [], [""], footer, width, None, style)
            )
            # The text above keeps a few rows, so that its window can be
            # scrolled however long the list is.
            room -= min(len(above), _TEXT_ROWS_KEPT)
            if visible + (visible < len(menu.items)) > room:
                visible = max(1, room - 1)
        screen.list.max_visible = visible
        rows = screen.list.render(width, style)
        return _frame(
            menu.title,
            [],
            above,
            ["", *rows],
            footer,
            width,
            height,
            style,
            window=(screen, keys.page_text),
        )

    def _render_status(
        self, screen: _Status, width: int, height: int | None, style: ChromeStyle
    ) -> list[str]:
        message = _text(screen.message, width, style.muted)
        footer = (
            None
            if screen.on_cancel is None
            else self._hint(self._keys.cancel_text, "cancel", style)
        )
        return _frame(
            screen.title,
            [""],
            message,
            [],
            footer,
            width,
            height,
            style,
            window=(screen, self._keys.page_text),
        )

    def _render_redirect(
        self, screen: _RedirectUrl, width: int, height: int | None, style: ChromeStyle
    ) -> list[str]:
        keys = self._keys
        copied = (
            self._hint(keys.copy_text, "to copy", style)
            if screen.copied is None
            else style.muted(screen.copied)
        )
        before = ["", *_text(_APPROVE_TEXT, width, style.muted)]
        url = _text(screen.url, width, style.accent)
        after = [
            # One line, whatever a failed copy has to say: the rows below
            # are the controls.
            *_wrapped(copied, width)[:1],
            "",
            *_text(_PASTE_TEXT, width, style.muted),
            screen.input.render(width, style),
        ]
        footer = (
            f"{self._hint(keys.confirm_text, 'submit', style)}{style.dim(' • ')}"
            f"{self._hint(keys.cancel_text, 'cancel', style)}"
        )
        return _frame(
            screen.title,
            before,
            url,
            after,
            footer,
            width,
            height,
            style,
            window=(screen, keys.page_text),
        )


def _wrapped(text: str, width: int) -> list[str]:
    """Styled text wrapped with a margin of one column."""

    return [f" {line}" for line in wrap_text_with_ansi(text, max(1, width - 2))]


def _text(text: str, width: int, colour: Callable[[str], str]) -> list[str]:
    """Pi ``Text(text, 1, 0)``: wrapped with a margin of one column."""

    return [f" {colour(line)}" for line in wrap_text_with_ansi(text, max(1, width - 2))]


def _window(
    lines: list[str], room: int, at: _Scroll, page_text: str, style: ChromeStyle
) -> list[str]:
    """``lines`` in at most ``room`` lines: when they do not fit, the lines
    of a window that starts at ``at.scroll``, and a line that says which
    ones they are. Keeps ``at`` inside the text and notes its page."""

    if len(lines) <= room or room <= 0:
        at.scroll = at.page = 0
        return lines if len(lines) <= room else []
    at.page = page = room - 1
    if page == 0:
        at.scroll = 0
        return [f" {style.muted(f'… {len(lines)} more lines')}"]
    at.scroll = start = min(at.scroll, len(lines) - page)
    note = f"… lines {start + 1}-{start + page} of {len(lines)}, {page_text} to scroll"
    return [*lines[start : start + page], f" {style.muted(note)}"]


def _frame(
    title: str,
    before: list[str],
    flexible: list[str],
    after: list[str],
    footer: str | None,
    width: int,
    height: int | None,
    style: ChromeStyle,
    *,
    window: tuple[_Scroll, str] | None = None,
) -> list[str]:
    """Pi ``frame``: accent borders around the title, the body and the key
    hints. The body is ``before``, ``flexible`` and ``after``; with a
    ``height``, ``flexible`` is shown through the ``window`` (the screen's
    scroll state and how the scroll keys are named) to make the frame
    fit."""

    border = style.accent("─" * max(1, width))
    head = [border, *_text(title, width, style.accent), *before]
    tail = [*after, *(["", *_wrapped(footer, width)] if footer else []), border]
    if height is not None and window is not None:
        at, page_text = window
        room = height - len(head) - len(tail)
        flexible = _window(flexible, room, at, page_text, style)
    return [*head, *flexible, *tail]
