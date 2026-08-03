"""Key-spec canonicalization and matching for the native terminal UI.

Pure functions over key strings: no terminal I/O, no session state, and no
reference to the terminal-UI shell. They translate between three vocabularies
that would otherwise be compared directly and wrongly -- the raw key a terminal
delivers, the ``shift-ctrl-x`` shortcut form the keybindings layer stores, and
the ``shift+ctrl+x`` canonical spec form rendered in hints -- so a caller never
has to know which of the three it is holding.

``escape`` and ``esc`` are deliberately aliased in both directions: terminals,
Pi's defaults, and user settings each pick one, and a mismatch would silently
drop a binding rather than fail loudly.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from pipy_harness.native.extension_types import normalize_shortcut_key
from pipy_harness.native.keybindings import DEFAULT_KEYBINDINGS, KeybindingsManager

# Actions a user may rebind through settings. Every other action resolves from
# `DEFAULT_KEYBINDINGS` alone, so an unrecognized user binding cannot silently
# take over a key the product owns.
USER_KEYBINDING_ACTIONS = frozenset({"app.editor.external"})


def canonical_key_spec(key: str) -> str:
    """Render one key as its canonical ``modifier+base`` display spec."""

    normalized = normalize_shortcut_key(key)
    parts = normalized.split("-")
    modifiers: list[str] = []
    index = 0
    while index < len(parts) - 1 and parts[index] in {"shift", "ctrl", "alt", "meta"}:
        modifiers.append(parts[index])
        index += 1
    base = "-".join(parts[index:])
    return "+".join([*modifiers, base]) if modifiers else base


def default_keys_for_action(action: str) -> tuple[str, ...]:
    """The product's own keys for ``action``, before any user binding."""

    if action == "app.clipboard.pasteImage" and sys.platform == "win32":
        return ("alt+v",)
    default = DEFAULT_KEYBINDINGS.get(action)
    return tuple(default.default_keys) if default is not None else ()


def resolved_key_specs(
    action: str, keybindings_manager: KeybindingsManager | None
) -> list[str]:
    """Canonical specs for ``action``, preferring a user binding when allowed."""

    if (
        keybindings_manager is not None
        and action in USER_KEYBINDING_ACTIONS
        and action in DEFAULT_KEYBINDINGS
    ):
        if keybindings_manager.has_user_binding(action):
            return [
                canonical_key_spec(key) for key in keybindings_manager.keys_for(action)
            ]
    return [canonical_key_spec(key) for key in default_keys_for_action(action)]


def matches_key_specs(key: str, specs: Sequence[str]) -> bool:
    """Whether a pressed ``key`` matches any of ``specs``."""

    normalized = normalize_shortcut_key(key)
    aliases = {
        candidate for spec in specs if (candidate := normalize_shortcut_key(spec))
    }
    if "escape" in aliases:
        aliases.add("esc")
    if "esc" in aliases:
        aliases.add("escape")
    return bool(normalized) and normalized in aliases
