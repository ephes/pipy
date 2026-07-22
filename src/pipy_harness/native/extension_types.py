"""Fail-closed extension vocabulary and error primitives.

This is the stdlib-only leaf that both the extension runtime and the later
extension loader depend on. It owns the safe, enumerable activation reason
codes, the internal `_ActivationError` used to disable one extension with a
reason code, the `_safe_diagnostic` type-name projection, the Pi command-name
character rules (`_is_valid_command_name` / `is_valid_custom_entry_type`), the
reserved-shortcut layer (`RESERVED_SHORTCUT_KEYS`, `_SHORTCUT_MODIFIERS`,
`normalize_shortcut_key`), and the bounded-length constants they rely on.

It has no project imports, so it can never participate in an import cycle with
the runtime or loader that import it. `normalize_shortcut_key` remains
re-exported from `pipy_harness.extensions` (via `extension_runtime`) unchanged.
"""

from __future__ import annotations

# Activation reason codes (safe, enumerable labels).
REASON_IMPORT_ERROR: str = "import_error"
REASON_NO_ACTIVATE: str = "no_activate"
REASON_ACTIVATION_ERROR: str = "activation_error"
REASON_INVALID_COMMAND_NAME: str = "invalid_command_name"
REASON_RESERVED_COMMAND: str = "reserved_command"
REASON_DUPLICATE_COMMAND: str = "duplicate_command"
REASON_INVALID_HOOK: str = "invalid_hook"
REASON_INVALID_TOOL: str = "invalid_tool"
REASON_RESERVED_TOOL: str = "reserved_tool"
REASON_DUPLICATE_TOOL: str = "duplicate_tool"
REASON_INVALID_PROVIDER: str = "invalid_provider"
REASON_DUPLICATE_PROVIDER: str = "duplicate_provider"
REASON_INVALID_SHORTCUT: str = "invalid_shortcut"
REASON_RESERVED_SHORTCUT: str = "reserved_shortcut"
REASON_DUPLICATE_SHORTCUT: str = "duplicate_shortcut"
REASON_INVALID_FLAG: str = "invalid_flag"
REASON_DUPLICATE_FLAG: str = "duplicate_flag"
REASON_INVALID_MESSAGE_RENDERER: str = "invalid_message_renderer"
REASON_DUPLICATE_MESSAGE_RENDERER: str = "duplicate_message_renderer"
REASON_INVALID_ENTRY_RENDERER: str = "invalid_entry_renderer"
REASON_DUPLICATE_ENTRY_RENDERER: str = "duplicate_entry_renderer"

# Built-in hotkey / editor keys an extension shortcut may never claim, so a
# binding can never shadow core input editing or the app hotkeys. Compared
# against the normalized key string (see `normalize_shortcut_key`).
RESERVED_SHORTCUT_KEYS: frozenset[str] = frozenset(
    {
        "enter",
        "tab",
        "shift-tab",
        "backspace",
        "esc",
        "ctrl-c",
        "ctrl-d",
        "ctrl-u",
        "ctrl-y",
        "ctrl-z",
        # Pi reserves the default app.editor.external key. Dynamic reservation
        # for user-rebound app.editor.external is deferred with the rest of the
        # shortcut/keybindings integration; when a user binds the editor action
        # to a key that an extension also registers, the live editor branch wins,
        # while Ctrl-G remains reserved even if the user moves the editor action.
        "ctrl-g",
        "ctrl-o",
        "ctrl-p",
        "ctrl-t",
        "ctrl-v",
        "shift-ctrl-p",
        "alt-enter",
        "alt-up",
        "home",
        "end",
        "up",
        "down",
        "left",
        "right",
        "paste",
        # Defensive: common editor keys that are not decoded to a named form
        # today but must never be claimable if the decoder grows to emit them.
        "delete",
        "insert",
        "pageup",
        "pagedown",
    }
)

# Canonical modifier order for a normalized shortcut key, matching pipy's
# decoded forms (e.g. "shift-ctrl-p", "shift-tab"). Modifiers are re-emitted in
# this order so "Ctrl+Shift+P" and "Shift+Ctrl+P" canonicalize identically and a
# reserved hotkey cannot be bypassed by reordering its modifiers.
_SHORTCUT_MODIFIERS: tuple[str, ...] = ("shift", "ctrl", "alt", "meta")


def normalize_shortcut_key(key: str) -> str:
    """Normalize a Pi-style shortcut key to pipy's internal key string.

    Accepts ``"Ctrl+."`` / ``"ctrl+x"`` (Pi's ``+``-joined form), lowercases and
    ``+``→``-`` it to pipy's decoded form (``"ctrl-."`` / ``"ctrl-x"``), and
    re-emits leading modifiers in a canonical order (``shift`` before ``ctrl``
    before ``alt``), so modifier reordering can neither bypass a reserved key
    nor create duplicate bindings. A single character is returned as-is.
    """

    raw = key.strip().lower().replace("+", "-")
    if not raw:
        return raw
    parts = raw.split("-")
    modifiers: list[str] = []
    index = 0
    # Leading tokens that are modifiers (but never the final base token) are
    # collected; the remainder is the base key (which may itself contain "-").
    while index < len(parts) - 1 and parts[index] in _SHORTCUT_MODIFIERS:
        modifiers.append(parts[index])
        index += 1
    base = "-".join(parts[index:])
    ordered = [mod for mod in _SHORTCUT_MODIFIERS if mod in modifiers]
    return "-".join([*ordered, base]) if ordered else base


_COMMAND_START_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")
_COMMAND_BODY_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")
_DIAGNOSTIC_MAX_LENGTH: int = 200
_CUSTOM_ENTRY_TYPE_MAX_CHARS: int = 200


def is_valid_custom_entry_type(name: str) -> bool:
    """Bounded lowercase ASCII custom-entry identifier."""

    return len(name) <= _CUSTOM_ENTRY_TYPE_MAX_CHARS and _is_valid_command_name(name)


def _is_valid_command_name(name: str) -> bool:
    """Lowercase ASCII identifier with optional `-` (Pi command rule)."""

    if not name:
        return False
    if name[0] not in _COMMAND_START_CHARS:
        return False
    return all(ch in _COMMAND_BODY_CHARS for ch in name)


class _ActivationError(Exception):
    """Raised internally to disable one extension with a reason code."""

    def __init__(self, reason: str, diagnostic: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.diagnostic = diagnostic


def _safe_diagnostic(err: BaseException) -> str:
    """Return a safe diagnostic label from an exception.

    Only the exception *type name* is kept (for example `RuntimeError`,
    `ModuleNotFoundError`). The raw exception message is deliberately
    dropped: it can carry absolute paths, prompts, or secrets from the
    extension, which must never enter a diagnostic. The type name is
    enough to distinguish failure modes without leaking content.
    """

    kind = type(err).__name__
    if len(kind) > _DIAGNOSTIC_MAX_LENGTH:
        return kind[:_DIAGNOSTIC_MAX_LENGTH]
    return kind
