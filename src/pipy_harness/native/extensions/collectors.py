"""Ordered collectors for activated extension contributions and outboxes."""

from __future__ import annotations

from collections.abc import Sequence

from pipy_harness.native.extension_types import (
    QueuedCustomMessage,
    QueuedUserMessage,
    RegisteredFlag,
    RegisteredProvider,
    RegisteredTool,
)
from pipy_harness.native.extensions.contracts import (
    ActivatedExtension,
    RegisteredEntryRenderer,
    RegisteredMessageRenderer,
)


def extension_providers(
    activated: Sequence[ActivatedExtension],
) -> tuple[RegisteredProvider, ...]:
    """Collect registered providers from activated extensions, in order."""

    providers: list[RegisteredProvider] = []
    for extension in activated:
        if extension.status != "activated":
            continue
        providers.extend(extension.providers)
    return tuple(providers)


def extension_oauth_providers(
    activated: Sequence[ActivatedExtension],
) -> dict[str, RegisteredProvider]:
    """Collect OAuth-capable providers keyed by their derived provider id."""

    return {
        registered.provider.name.lower(): registered
        for registered in extension_providers(activated)
        if registered.provider.oauth is not None
    }


def extension_unregistered_providers(
    activated: Sequence[ActivatedExtension],
) -> tuple[str, ...]:
    """Collect provider names extensions asked to unregister, in order."""

    names: list[str] = []
    for extension in activated:
        if extension.status != "activated":
            continue
        for name in extension.unregistered_providers:
            if name not in names:
                names.append(name)
    return tuple(names)


def extension_tools(
    activated: Sequence[ActivatedExtension],
) -> tuple[RegisteredTool, ...]:
    """Collect registered tools from activated extensions, in order."""

    tools: list[RegisteredTool] = []
    for extension in activated:
        if extension.status != "activated":
            continue
        tools.extend(extension.tools)
    return tuple(tools)


def extension_flags(
    activated: Sequence[ActivatedExtension],
) -> tuple[RegisteredFlag, ...]:
    """Collect registered CLI flags from activated extensions, in order."""

    flags: list[RegisteredFlag] = []
    for extension in activated:
        if extension.status != "activated":
            continue
        flags.extend(extension.flags)
    return tuple(flags)


def extension_message_renderers(
    activated: Sequence[ActivatedExtension],
) -> dict[str, RegisteredMessageRenderer]:
    """Collect custom-message renderers with first registration winning."""

    renderers: dict[str, RegisteredMessageRenderer] = {}
    for extension in activated:
        if extension.status != "activated":
            continue
        for renderer in extension.message_renderers:
            renderers.setdefault(renderer.custom_type, renderer)
    return renderers


def extension_entry_renderers(
    activated: Sequence[ActivatedExtension],
) -> dict[str, RegisteredEntryRenderer]:
    """Collect durable-entry renderers with first registration winning."""

    renderers: dict[str, RegisteredEntryRenderer] = {}
    for extension in activated:
        if extension.status != "activated":
            continue
        for renderer in extension.entry_renderers:
            renderers.setdefault(renderer.custom_type, renderer)
    return renderers


def drain_user_messages(
    outbox: list[QueuedUserMessage],
) -> list[QueuedUserMessage]:
    """Return and clear queued user messages, in order."""

    drained = list(outbox)
    outbox.clear()
    return drained


def drain_custom_messages(
    outbox: list[QueuedCustomMessage],
) -> list[QueuedCustomMessage]:
    """Return and clear queued custom messages, in order."""

    drained = list(outbox)
    outbox.clear()
    return drained
