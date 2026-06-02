"""Pi-style keybindings for the native runtime.

Mirrors Pi's ``keybindings.ts`` through pipy-owned Python: the default binding
table (the editor/select/input ``tui.*`` base layered with 35+ ``app.*``
bindings), ``keybindings.json`` loading (each action bound to a single key spec
or an array of **alternative** specs), in-memory legacy-name migration and
canonical re-ordering (never written back to disk), malformed-file fallback to
the built-in defaults (not the previously-loaded bindings), a context-agnostic
resolved lookup, and the ``/hotkeys`` renderer built from the resolved manager.

``keybindings.json`` lives at ``<config>/keybindings.json`` (see
``settings.resolve_config_home``). It is not a TypeScript port and does not wrap
the ``pi-tui`` keybindings library; the default tables are transcribed from Pi's
documented defaults (``packages/coding-agent/docs/keybindings.md``).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .settings import resolve_config_home

KEYBINDINGS_FILENAME = "keybindings.json"


@dataclass(frozen=True, slots=True)
class KeybindingDefault:
    """A default binding: its alternative key specs and a description."""

    default_keys: list[str] = field(default_factory=list)
    description: str = ""


def _kb(keys: str | list[str], description: str) -> KeybindingDefault:
    return KeybindingDefault(
        default_keys=[keys] if isinstance(keys, str) else list(keys),
        description=description,
    )


# Editor/select/input base bindings (Pi's pi-tui TUI_KEYBINDINGS equivalent).
TUI_KEYBINDINGS: dict[str, KeybindingDefault] = {
    "tui.editor.cursorUp": _kb("up", "Move cursor up"),
    "tui.editor.cursorDown": _kb("down", "Move cursor down"),
    "tui.editor.cursorLeft": _kb(["left", "ctrl+b"], "Move cursor left"),
    "tui.editor.cursorRight": _kb(["right", "ctrl+f"], "Move cursor right"),
    "tui.editor.cursorWordLeft": _kb(
        ["alt+left", "ctrl+left", "alt+b"], "Move cursor word left"
    ),
    "tui.editor.cursorWordRight": _kb(
        ["alt+right", "ctrl+right", "alt+f"], "Move cursor word right"
    ),
    "tui.editor.cursorLineStart": _kb(["home", "ctrl+a"], "Move to line start"),
    "tui.editor.cursorLineEnd": _kb(["end", "ctrl+e"], "Move to line end"),
    "tui.editor.jumpForward": _kb("ctrl+]", "Jump forward to character"),
    "tui.editor.jumpBackward": _kb("ctrl+alt+]", "Jump backward to character"),
    "tui.editor.pageUp": _kb("pageUp", "Scroll up by page"),
    "tui.editor.pageDown": _kb("pageDown", "Scroll down by page"),
    "tui.editor.deleteCharBackward": _kb("backspace", "Delete character backward"),
    "tui.editor.deleteCharForward": _kb(
        ["delete", "ctrl+d"], "Delete character forward"
    ),
    "tui.editor.deleteWordBackward": _kb(
        ["ctrl+w", "alt+backspace"], "Delete word backward"
    ),
    "tui.editor.deleteWordForward": _kb(
        ["alt+d", "alt+delete"], "Delete word forward"
    ),
    "tui.editor.deleteToLineStart": _kb("ctrl+u", "Delete to line start"),
    "tui.editor.deleteToLineEnd": _kb("ctrl+k", "Delete to line end"),
    "tui.editor.yank": _kb("ctrl+y", "Paste most recently deleted text"),
    "tui.editor.yankPop": _kb("alt+y", "Cycle through deleted text after yank"),
    "tui.editor.undo": _kb("ctrl+-", "Undo last edit"),
    "tui.input.newLine": _kb("shift+enter", "Insert new line"),
    "tui.input.submit": _kb("enter", "Submit input"),
    "tui.input.tab": _kb("tab", "Tab / autocomplete"),
    "tui.input.copy": _kb("ctrl+c", "Copy selection"),
    "tui.select.up": _kb("up", "Move selection up"),
    "tui.select.down": _kb("down", "Move selection down"),
    "tui.select.pageUp": _kb("pageUp", "Page up in list"),
    "tui.select.pageDown": _kb("pageDown", "Page down in list"),
    "tui.select.confirm": _kb("enter", "Confirm selection"),
    "tui.select.cancel": _kb(["escape", "ctrl+c"], "Cancel selection"),
}

# Application bindings (Pi's KEYBINDINGS const). This is the canonical order
# used by orderKeybindingsConfig; ``app.suspend`` is unbound on Windows but pipy
# ships the Unix default and lets the platform decide at use time.
APP_KEYBINDINGS: dict[str, KeybindingDefault] = {
    "app.interrupt": _kb("escape", "Cancel or abort"),
    "app.clear": _kb("ctrl+c", "Clear editor"),
    "app.exit": _kb("ctrl+d", "Exit when editor is empty"),
    "app.suspend": _kb("ctrl+z", "Suspend to background"),
    "app.thinking.cycle": _kb("shift+tab", "Cycle thinking level"),
    "app.model.cycleForward": _kb("ctrl+p", "Cycle to next model"),
    "app.model.cycleBackward": _kb("shift+ctrl+p", "Cycle to previous model"),
    "app.model.select": _kb("ctrl+l", "Open model selector"),
    "app.tools.expand": _kb("ctrl+o", "Toggle tool output"),
    "app.thinking.toggle": _kb("ctrl+t", "Toggle thinking blocks"),
    "app.session.toggleNamedFilter": _kb("ctrl+n", "Toggle named session filter"),
    "app.editor.external": _kb("ctrl+g", "Open external editor"),
    "app.message.followUp": _kb("alt+enter", "Queue follow-up message"),
    "app.message.dequeue": _kb("alt+up", "Restore queued messages"),
    "app.clipboard.pasteImage": _kb("ctrl+v", "Paste image from clipboard"),
    "app.session.new": _kb([], "Start a new session"),
    "app.session.tree": _kb([], "Open session tree"),
    "app.session.fork": _kb([], "Fork current session"),
    "app.session.resume": _kb([], "Resume a session"),
    "app.tree.foldOrUp": _kb(["ctrl+left", "alt+left"], "Fold tree branch or move up"),
    "app.tree.unfoldOrDown": _kb(
        ["ctrl+right", "alt+right"], "Unfold tree branch or move down"
    ),
    "app.tree.editLabel": _kb("shift+l", "Edit tree label"),
    "app.tree.toggleLabelTimestamp": _kb("shift+t", "Toggle tree label timestamps"),
    "app.session.togglePath": _kb("ctrl+p", "Toggle session path display"),
    "app.session.toggleSort": _kb("ctrl+s", "Toggle session sort mode"),
    "app.session.rename": _kb("ctrl+r", "Rename session"),
    "app.session.delete": _kb("ctrl+d", "Delete session"),
    "app.session.deleteNoninvasive": _kb(
        "ctrl+backspace", "Delete session when query is empty"
    ),
    "app.models.save": _kb("ctrl+s", "Save model selection"),
    "app.models.enableAll": _kb("ctrl+a", "Enable all models"),
    "app.models.clearAll": _kb("ctrl+x", "Clear all models"),
    "app.models.toggleProvider": _kb("ctrl+p", "Toggle all models for provider"),
    "app.models.reorderUp": _kb("alt+up", "Move model up in order"),
    "app.models.reorderDown": _kb("alt+down", "Move model down in order"),
    "app.tree.filter.default": _kb("ctrl+d", "Tree filter: default view"),
    "app.tree.filter.noTools": _kb("ctrl+t", "Tree filter: hide tool results"),
    "app.tree.filter.userOnly": _kb("ctrl+u", "Tree filter: user messages only"),
    "app.tree.filter.labeledOnly": _kb("ctrl+l", "Tree filter: labeled entries only"),
    "app.tree.filter.all": _kb("ctrl+a", "Tree filter: show all entries"),
    "app.tree.filter.cycleForward": _kb("ctrl+o", "Tree filter: cycle forward"),
    "app.tree.filter.cycleBackward": _kb("shift+ctrl+o", "Tree filter: cycle backward"),
}

# Full default table for resolution (base then app).
DEFAULT_KEYBINDINGS: dict[str, KeybindingDefault] = {**TUI_KEYBINDINGS, **APP_KEYBINDINGS}

# Legacy flat names -> namespaced ids (Pi KEYBINDING_NAME_MIGRATIONS).
KEYBINDING_NAME_MIGRATIONS: dict[str, str] = {
    "cursorUp": "tui.editor.cursorUp",
    "cursorDown": "tui.editor.cursorDown",
    "cursorLeft": "tui.editor.cursorLeft",
    "cursorRight": "tui.editor.cursorRight",
    "cursorWordLeft": "tui.editor.cursorWordLeft",
    "cursorWordRight": "tui.editor.cursorWordRight",
    "cursorLineStart": "tui.editor.cursorLineStart",
    "cursorLineEnd": "tui.editor.cursorLineEnd",
    "jumpForward": "tui.editor.jumpForward",
    "jumpBackward": "tui.editor.jumpBackward",
    "pageUp": "tui.editor.pageUp",
    "pageDown": "tui.editor.pageDown",
    "deleteCharBackward": "tui.editor.deleteCharBackward",
    "deleteCharForward": "tui.editor.deleteCharForward",
    "deleteWordBackward": "tui.editor.deleteWordBackward",
    "deleteWordForward": "tui.editor.deleteWordForward",
    "deleteToLineStart": "tui.editor.deleteToLineStart",
    "deleteToLineEnd": "tui.editor.deleteToLineEnd",
    "yank": "tui.editor.yank",
    "yankPop": "tui.editor.yankPop",
    "undo": "tui.editor.undo",
    "newLine": "tui.input.newLine",
    "submit": "tui.input.submit",
    "tab": "tui.input.tab",
    "copy": "tui.input.copy",
    "selectUp": "tui.select.up",
    "selectDown": "tui.select.down",
    "selectPageUp": "tui.select.pageUp",
    "selectPageDown": "tui.select.pageDown",
    "selectConfirm": "tui.select.confirm",
    "selectCancel": "tui.select.cancel",
    "interrupt": "app.interrupt",
    "clear": "app.clear",
    "exit": "app.exit",
    "suspend": "app.suspend",
    "cycleThinkingLevel": "app.thinking.cycle",
    "cycleModelForward": "app.model.cycleForward",
    "cycleModelBackward": "app.model.cycleBackward",
    "selectModel": "app.model.select",
    "expandTools": "app.tools.expand",
    "toggleThinking": "app.thinking.toggle",
    "toggleSessionNamedFilter": "app.session.toggleNamedFilter",
    "externalEditor": "app.editor.external",
    "followUp": "app.message.followUp",
    "dequeue": "app.message.dequeue",
    "pasteImage": "app.clipboard.pasteImage",
    "newSession": "app.session.new",
    "tree": "app.session.tree",
    "fork": "app.session.fork",
    "resume": "app.session.resume",
    "treeFoldOrUp": "app.tree.foldOrUp",
    "treeUnfoldOrDown": "app.tree.unfoldOrDown",
    "treeEditLabel": "app.tree.editLabel",
    "treeToggleLabelTimestamp": "app.tree.toggleLabelTimestamp",
    "toggleSessionPath": "app.session.togglePath",
    "toggleSessionSort": "app.session.toggleSort",
    "renameSession": "app.session.rename",
    "deleteSession": "app.session.delete",
    "deleteSessionNoninvasive": "app.session.deleteNoninvasive",
}


def _order_config(config: dict[str, Any]) -> dict[str, Any]:
    """Re-order to canonical app order, then unknown extras sorted (Pi)."""

    ordered: dict[str, Any] = {}
    for action in APP_KEYBINDINGS:
        if action in config:
            ordered[action] = config[action]
    for key in sorted(k for k in config if k not in ordered):
        ordered[key] = config[key]
    return ordered


def migrate_keybindings_config(raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Migrate legacy flat names to namespaced ids and re-order (Pi parity).

    Renames legacy names; when both the legacy and the new name are present the
    new name wins and the legacy is dropped. Returns ``(config, migrated)`` with
    the config re-ordered to the canonical app order followed by unknown extras
    sorted alphabetically. Applied in memory only — never written back to disk.
    """

    config: dict[str, Any] = {}
    migrated = False
    for key, value in raw.items():
        next_key = KEYBINDING_NAME_MIGRATIONS.get(key, key)
        if next_key != key:
            migrated = True
            if key in raw and next_key in raw:
                # The new name already exists; drop the legacy entry.
                continue
        config[next_key] = value
    return _order_config(config), migrated


def _coerce_config(value: Any) -> dict[str, list[str]]:
    """Keep only string or array-of-strings values (Pi toKeybindingsConfig)."""

    if not isinstance(value, Mapping):
        return {}
    config: dict[str, list[str]] = {}
    for key, binding in value.items():
        if isinstance(binding, str):
            config[key] = [binding]
        elif isinstance(binding, list) and all(isinstance(e, str) for e in binding):
            config[key] = list(binding)
    return config


def _load_raw_config(path: Path) -> dict[str, Any] | None:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def load_keybindings_file(path: Path) -> dict[str, list[str]]:
    """Load, migrate, order, and coerce a ``keybindings.json`` to user bindings.

    A missing or malformed file (parse error or non-object) yields ``{}`` so the
    manager falls back to the built-in defaults.
    """

    raw = _load_raw_config(path)
    if raw is None:
        return {}
    migrated, _ = migrate_keybindings_config(raw)
    return _coerce_config(migrated)


class KeybindingsManager:
    """Resolve actions to key specs, with user overrides and reloadable file."""

    def __init__(
        self,
        user_bindings: Mapping[str, list[str] | str] | None = None,
        *,
        config_path: Path | None = None,
    ) -> None:
        # Accept a single key spec or an array of alternatives per action and
        # normalize to a list, so callers may pass either form.
        self._user = _coerce_config(user_bindings or {})
        self._config_path = config_path

    @classmethod
    def from_file(cls, path: Path) -> "KeybindingsManager":
        return cls(load_keybindings_file(path), config_path=path)

    @classmethod
    def create(
        cls,
        *,
        env: dict[str, str] | None = None,
        home_dir: Path | None = None,
    ) -> "KeybindingsManager":
        path = resolve_config_home(env=env, home_dir=home_dir) / KEYBINDINGS_FILENAME
        return cls.from_file(path)

    @property
    def config_path(self) -> Path | None:
        return self._config_path

    def reload(self) -> None:
        if self._config_path is None:
            return
        self._user = load_keybindings_file(self._config_path)

    def keys_for(self, action: str) -> list[str]:
        """Resolved key specs for ``action`` (user override, else default)."""

        if action in self._user:
            return list(self._user[action])
        default = DEFAULT_KEYBINDINGS.get(action)
        return list(default.default_keys) if default is not None else []

    def resolved_config(self) -> dict[str, list[str]]:
        """All known actions mapped to their resolved key specs."""

        return {action: self.keys_for(action) for action in DEFAULT_KEYBINDINGS}


# --- display + /hotkeys -----------------------------------------------------


def _format_part(part: str, *, capitalize: bool, platform: str) -> str:
    display = part
    if platform == "darwin" and part.lower() == "alt":
        display = "option"
    if capitalize and display:
        display = display[0].upper() + display[1:]
    return display


def key_display_text(
    keys: list[str], *, capitalize: bool = True, platform: str | None = None
) -> str:
    """Capitalized, ``/``-joined display string for a list of key specs.

    Mirrors Pi's ``keyDisplayText``: alternatives are joined with ``/``, each
    ``+``-combination's parts are capitalized, and on macOS ``alt`` renders as
    ``option``. Returns ``""`` for an unbound action.
    """

    if not keys:
        return ""
    # Pi joins alternatives with "/" then splits on both "/" and "+"; pipy
    # splits each alternative on "+" and joins alternatives with "/". The two
    # coincide for every shipped binding (none binds a literal "/"); the
    # simplification only differs for a hypothetical spec containing "/".
    plat = platform if platform is not None else sys.platform
    rendered = [
        "+".join(_format_part(p, capitalize=capitalize, platform=plat) for p in key.split("+"))
        for key in keys
    ]
    return "/".join(rendered)


# The /hotkeys layout, mirroring Pi's handleHotkeysCommand grouping. Each row is
# (list-of-action-ids, label); a row with an empty action list and a literal in
# the label renders a static key (slash/bang commands).
_HOTKEY_GROUPS: list[tuple[str, list[tuple[list[str], str]]]] = [
    (
        "Navigation",
        [
            (
                [
                    "tui.editor.cursorUp",
                    "tui.editor.cursorDown",
                    "tui.editor.cursorLeft",
                    "tui.editor.cursorRight",
                ],
                "Move cursor / browse history (Up when empty)",
            ),
            (["tui.editor.cursorWordLeft", "tui.editor.cursorWordRight"], "Move by word"),
            (["tui.editor.cursorLineStart"], "Start of line"),
            (["tui.editor.cursorLineEnd"], "End of line"),
            (["tui.editor.jumpForward"], "Jump forward to character"),
            (["tui.editor.jumpBackward"], "Jump backward to character"),
            (["tui.editor.pageUp", "tui.editor.pageDown"], "Scroll by page"),
        ],
    ),
    (
        "Editing",
        [
            (["tui.input.submit"], "Send message"),
            (["tui.input.newLine"], "New line"),
            (["tui.editor.deleteWordBackward"], "Delete word backwards"),
            (["tui.editor.deleteWordForward"], "Delete word forwards"),
            (["tui.editor.deleteToLineStart"], "Delete to start of line"),
            (["tui.editor.deleteToLineEnd"], "Delete to end of line"),
            (["tui.editor.yank"], "Paste the most-recently-deleted text"),
            (["tui.editor.yankPop"], "Cycle through the deleted text after pasting"),
            (["tui.editor.undo"], "Undo"),
        ],
    ),
    (
        "Other",
        [
            (["tui.input.tab"], "Path completion / accept autocomplete"),
            (["app.interrupt"], "Cancel autocomplete / abort streaming"),
            (["app.clear"], "Clear editor (first) / exit (second)"),
            (["app.exit"], "Exit (when editor is empty)"),
            (["app.suspend"], "Suspend to background"),
            (["app.thinking.cycle"], "Cycle thinking level"),
            (["app.model.cycleForward", "app.model.cycleBackward"], "Cycle models"),
            (["app.model.select"], "Open model selector"),
            (["app.tools.expand"], "Toggle tool output expansion"),
            (["app.thinking.toggle"], "Toggle thinking block visibility"),
            (["app.editor.external"], "Edit message in external editor"),
            (["app.message.followUp"], "Queue follow-up message"),
            (["app.message.dequeue"], "Restore queued messages"),
            (["app.clipboard.pasteImage"], "Paste image from clipboard"),
        ],
    ),
]

_STATIC_OTHER_ROWS: list[tuple[str, str]] = [
    ("/", "Slash commands"),
    ("!", "Run bash command"),
    ("!!", "Run bash command (excluded from context)"),
]


def render_hotkeys(manager: KeybindingsManager, *, platform: str | None = None) -> str:
    """Render the grouped ``/hotkeys`` markdown table from resolved bindings.

    Display strings come from the live manager (never a hardcoded table), so
    user overrides are reflected. Runs no provider turn; degrades to plain
    markdown in the captured-stream / non-TTY fallback.
    """

    lines: list[str] = ["**Keyboard Shortcuts**", ""]
    for title, rows in _HOTKEY_GROUPS:
        lines.append(f"**{title}**")
        lines.append("| Key | Action |")
        lines.append("|-----|--------|")
        for actions, label in rows:
            display = " / ".join(
                f"`{key_display_text(manager.keys_for(a), platform=platform)}`"
                for a in actions
            )
            lines.append(f"| {display} | {label} |")
        if title == "Other":
            for literal, label in _STATIC_OTHER_ROWS:
                lines.append(f"| `{literal}` | {label} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
