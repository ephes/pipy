"""Command and shortcut collection and dispatch for activated extensions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pipy_harness.native.extension_types import (
    CustomComponentDriver,
    ExtensionCodingSessionControl,
    ExtensionModelRuntimeControl,
    ExtensionUiDriver,
    _safe_diagnostic,
    normalize_shortcut_key,
)
from pipy_harness.native.extension_ui import _CollectingUi
from pipy_harness.native.extensions.command_context import _CommandContext

if TYPE_CHECKING:
    from pipy_harness.native.extensions.contracts import (
        ActivatedExtension,
        RegisteredCommand,
        RegisteredShortcut,
    )

CommandHandler = Callable[..., object]


@dataclass(frozen=True, slots=True)
class ExtensionCommandDispatch:
    """Outcome of dispatching one extension `/command`.

    `ran` is True when the handler completed; `error` carries a safe,
    bounded label (exception type name only) when it raised. `messages`
    are the `(kind, text)` notifications the handler emitted, for the
    caller to render as live UI output. No provider turn is implied.
    """

    name: str
    ran: bool
    error: str | None
    messages: tuple[tuple[str, str], ...]


def extension_command_map(
    activated: Sequence[ActivatedExtension],
) -> dict[str, RegisteredCommand]:
    """Build a `name -> RegisteredCommand` map from activated extensions.

    Only `activated` extensions contribute; a name registered by an
    earlier extension wins (duplicates were already disabled during
    activation, so this is deterministic).
    """

    command_map: dict[str, RegisteredCommand] = {}
    for extension in activated:
        if extension.status != "activated":
            continue
        for command in extension.commands:
            command_map.setdefault(command.name, command)
    return command_map


def extension_shortcuts(
    activated: Sequence[ActivatedExtension],
) -> dict[str, RegisteredShortcut]:
    """Build a `key -> RegisteredShortcut` map from activated extensions.

    Only `activated` extensions contribute; a key bound by an earlier
    extension wins (duplicate keys were already disabled during activation,
    so this is deterministic).
    """

    shortcut_map: dict[str, RegisteredShortcut] = {}
    for extension in activated:
        if extension.status != "activated":
            continue
        for shortcut in extension.shortcuts:
            shortcut_map.setdefault(shortcut.key, shortcut)
    return shortcut_map


def dispatch_extension_command(
    command_text: str,
    command_map: Mapping[str, RegisteredCommand],
    *,
    cwd: str,
    has_ui: bool,
    coding_session: "ExtensionCodingSessionControl | None" = None,
    notify_sink: "Callable[[str, str], None] | None" = None,
    ui_custom_driver: "CustomComponentDriver | None" = None,
    ui_driver: "ExtensionUiDriver | None" = None,
    model_runtime: "ExtensionModelRuntimeControl | None" = None,
    flags: Mapping[str, object] | None = None,
    project_trusted: bool = False,
) -> ExtensionCommandDispatch | None:
    """Dispatch `command_text` to an extension command, or return None.

    Returns None when `command_text` is not a `/<name>` form or names no
    registered extension command, so the caller falls through to its
    normal handling (built-ins run earlier, so extensions can never
    shadow them). When it matches, the handler runs locally with a
    mode-aware context and the raw argument string; it triggers no
    provider turn. A handler exception is bounded into a safe `error`.
    """

    if not command_text.startswith("/"):
        return None
    body = command_text[1:]
    # Split only on the first space: the command name, then the raw
    # argument string verbatim (intentional leading/trailing whitespace
    # is preserved, per the handler contract).
    name, _, args = body.partition(" ")
    command = command_map.get(name)
    if command is None:
        return None

    return _run_extension_handler(
        name,
        command.handler,
        args,
        cwd=cwd,
        has_ui=has_ui,
        coding_session=coding_session,
        notify_sink=notify_sink,
        ui_custom_driver=ui_custom_driver,
        ui_driver=ui_driver,
        model_runtime=model_runtime,
        flags=flags,
        project_trusted=project_trusted,
    )


def dispatch_extension_shortcut(
    key: str,
    shortcut_map: Mapping[str, RegisteredShortcut],
    *,
    cwd: str,
    has_ui: bool,
    coding_session: "ExtensionCodingSessionControl | None" = None,
    notify_sink: "Callable[[str, str], None] | None" = None,
    ui_custom_driver: "CustomComponentDriver | None" = None,
    ui_driver: "ExtensionUiDriver | None" = None,
    model_runtime: "ExtensionModelRuntimeControl | None" = None,
    flags: Mapping[str, object] | None = None,
    project_trusted: bool = False,
) -> ExtensionCommandDispatch | None:
    """Dispatch a registered extension shortcut `key`, or return None.

    Returns None when `key` (already normalized by the caller) names no
    registered shortcut. When it matches, the bound handler runs locally with
    the same mode-aware context as a command and an empty argument string; it
    triggers no provider turn and a handler exception is bounded into a safe
    `error`.
    """

    shortcut = shortcut_map.get(normalize_shortcut_key(key))
    if shortcut is None:
        return None
    return _run_extension_handler(
        shortcut.key,
        shortcut.handler,
        "",
        cwd=cwd,
        has_ui=has_ui,
        coding_session=coding_session,
        notify_sink=notify_sink,
        ui_custom_driver=ui_custom_driver,
        ui_driver=ui_driver,
        model_runtime=model_runtime,
        flags=flags,
        project_trusted=project_trusted,
    )


def _run_extension_handler(
    name: str,
    handler: CommandHandler,
    args: str,
    *,
    cwd: str,
    has_ui: bool,
    coding_session: "ExtensionCodingSessionControl | None",
    notify_sink: "Callable[[str, str], None] | None",
    ui_custom_driver: "CustomComponentDriver | None",
    ui_driver: "ExtensionUiDriver | None",
    model_runtime: "ExtensionModelRuntimeControl | None",
    flags: Mapping[str, object] | None,
    project_trusted: bool,
) -> ExtensionCommandDispatch:
    """Run a command/shortcut handler with a mode-aware context; bound errors."""

    ui = _CollectingUi(has_ui, notify_sink, ui_custom_driver, ui_driver)
    ctx = _CommandContext(
        cwd,
        ui,
        coding_session,
        model_runtime=model_runtime,
        flags=flags,
        project_trusted=project_trusted,
    )
    try:
        handler(ctx, args)
    except (KeyboardInterrupt, SystemExit):
        # A genuine user abort / interpreter exit is control flow, not an
        # extension failure: never swallow it into a bounded error.
        raise
    except BaseException as err:  # noqa: BLE001 - bound a bad handler
        return ExtensionCommandDispatch(
            name=name,
            ran=False,
            error=_safe_diagnostic(err),
            messages=tuple(ui.messages),
        )
    return ExtensionCommandDispatch(
        name=name,
        ran=True,
        error=None,
        messages=tuple(ui.messages),
    )
