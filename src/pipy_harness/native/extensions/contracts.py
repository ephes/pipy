"""Activation contracts and runtime contribution value objects."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Protocol

from pipy_harness.native.extension_types import (
    ExtensionFlag,
    ExtensionProvider,
    ExtensionTool,
    QueuedCustomMessage,
    QueuedUserMessage,
    RegisteredFlag,
    RegisteredProvider,
    RegisteredTool,
)
from pipy_harness.native.extensions import message_routing as _message_routing

CommandHandler = Callable[..., object]
HookHandler = Callable[..., object]
ActivationStatus = Literal["activated", "disabled"]


class PipyExtensionAPI(Protocol):
    """The activation-time API handed to an extension's ``activate``."""

    def register_command(
        self,
        name: str,
        description: str,
        handler: CommandHandler,
    ) -> None: ...

    def register_shortcut(self, key: str, handler: CommandHandler) -> None: ...

    def on(
        self,
        event: str,
        handler: HookHandler | None = None,
    ) -> object: ...

    def send_user_message(
        self,
        content: str,
        options: Mapping[str, object] | None = None,
    ) -> None: ...

    def send_message(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> None: ...

    def sendMessage(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> None: ...

    def register_tool(self, tool: ExtensionTool) -> None: ...

    def register_provider(self, provider: ExtensionProvider) -> None: ...

    def unregister_provider(self, name: str) -> None: ...

    def register_flag(self, flag: ExtensionFlag) -> None: ...

    def get_flag(self, name: str) -> object | None: ...

    def register_message_renderer(
        self,
        custom_type: str,
        renderer: Callable[..., object],
    ) -> None: ...

    def register_entry_renderer(
        self,
        custom_type: str,
        renderer: Callable[..., object],
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class RegisteredCommand:
    """One command an extension registered during activation."""

    name: str
    description: str
    handler: CommandHandler
    extension: str


@dataclass(frozen=True, slots=True)
class RegisteredMessageRenderer:
    """A renderer for extension custom messages of one type."""

    custom_type: str
    renderer: Callable[..., object]
    extension: str


@dataclass(frozen=True, slots=True)
class RegisteredEntryRenderer:
    """A TUI renderer for durable extension custom entries of one type."""

    custom_type: str
    renderer: Callable[..., object]
    extension: str


@dataclass(frozen=True, slots=True)
class RegisteredShortcut:
    """One normalized keyboard shortcut bound during activation."""

    key: str
    handler: CommandHandler
    extension: str


class _ActivationHost(Protocol):
    """Host surface retained by activation results and runtime contracts."""

    _message_routing: _message_routing.GenerationMessageRouting
    _outbox: list[QueuedUserMessage]
    _custom_outbox: list[QueuedCustomMessage]

    def register_command(
        self,
        name: str,
        description: str,
        handler: CommandHandler,
    ) -> None: ...

    def send_user_message(
        self,
        content: str,
        options: Mapping[str, object] | None = None,
    ) -> None: ...

    def send_message(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> None: ...

    def sendMessage(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> None: ...

    def get_flag(self, name: str) -> object | None: ...

    def _dispose(self) -> bool: ...


class _PendingActivationHost(Protocol):
    """Narrow pending holder shape needed to discover message routing."""

    _host: _ActivationHost | None


_EMPTY_HOOKS: Mapping[str, tuple[HookHandler, ...]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class ActivatedExtension:
    """The immutable outcome of attempting to activate one extension."""

    name: str
    version: str
    path_label: str
    status: ActivationStatus
    reason: str | None
    commands: tuple[RegisteredCommand, ...]
    diagnostic: str | None
    hooks: Mapping[str, tuple[HookHandler, ...]] = field(
        default_factory=lambda: _EMPTY_HOOKS
    )
    tools: tuple[RegisteredTool, ...] = ()
    providers: tuple[RegisteredProvider, ...] = ()
    unregistered_providers: tuple[str, ...] = ()
    shortcuts: tuple[RegisteredShortcut, ...] = ()
    flags: tuple[RegisteredFlag, ...] = ()
    message_renderers: tuple[RegisteredMessageRenderer, ...] = ()
    entry_renderers: tuple[RegisteredEntryRenderer, ...] = ()
    custom_messages: tuple[QueuedCustomMessage, ...] = ()
    _activation_key: str | None = field(default=None, repr=False, compare=False)
    _pending_activation: _PendingActivationHost | None = field(
        default=None, repr=False, compare=False
    )
    _activation_host: _ActivationHost | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        """Freeze the hook map for activated, disabled, and passthrough results."""

        object.__setattr__(self, "hooks", MappingProxyType(dict(self.hooks)))


class ExtensionActivationBatch:
    def __init__(
        self,
        activated: tuple[ActivatedExtension, ...],
        message_outbox: list[QueuedUserMessage],
        custom_message_outbox: list[QueuedCustomMessage],
        pending: bool = False,
        message_routing: _message_routing.GenerationMessageRouting | None = None,
    ) -> None:
        self.activated, self.pending = activated, pending
        self.message_outbox, self.custom_message_outbox = (
            message_outbox,
            custom_message_outbox,
        )
        self.message_routing = _message_routing._routing_for_activation_batch(
            _activation_message_routings(activated),
            message_outbox,
            custom_message_outbox,
            supplied=message_routing,
        )


@dataclass(frozen=True, slots=True)
class _ExtensionRuntime:
    """The activated-extension contributions wired into one session run."""

    commands: dict[str, RegisteredCommand]
    menu_names: tuple[str, ...]
    descriptions: dict[str, str]
    tool_call_hooks: tuple[HookHandler, ...]
    lifecycle_hooks: dict[str, tuple[HookHandler, ...]]
    input_hooks: tuple[HookHandler, ...]
    before_agent_start_hooks: tuple[HookHandler, ...]
    tool_result_hooks: tuple[HookHandler, ...]
    user_bash_hooks: tuple[HookHandler, ...]
    before_provider_headers_hooks: tuple[HookHandler, ...]
    before_provider_request_hooks: tuple[HookHandler, ...]
    session_before_switch_hooks: tuple[HookHandler, ...]
    session_before_fork_hooks: tuple[HookHandler, ...]
    session_before_compact_hooks: tuple[HookHandler, ...]
    session_before_tree_hooks: tuple[HookHandler, ...]
    outbox: list[QueuedUserMessage]
    custom_outbox: list[QueuedCustomMessage]
    tools: tuple[RegisteredTool, ...]
    shortcuts: dict[str, RegisteredShortcut]
    flags: tuple[RegisteredFlag, ...]
    providers: tuple[RegisteredProvider, ...]
    unregistered_providers: tuple[str, ...]
    message_renderers: dict[str, RegisteredMessageRenderer]
    entry_renderers: dict[str, RegisteredEntryRenderer]
    custom_messages: tuple[QueuedCustomMessage, ...]
    activation_hosts: tuple[_ActivationHost, ...]
    message_routing: _message_routing.GenerationMessageRouting

    def __post_init__(self) -> None:
        if (
            self.message_routing.user_outbox is not self.outbox
            or self.message_routing.custom_outbox is not self.custom_outbox
        ):
            raise ValueError("runtime routing must own its exact outboxes")
        if any(
            host._message_routing is not self.message_routing
            for host in self.activation_hosts
        ):
            raise ValueError("runtime hosts must share its exact message routing")


def _activation_message_routings(
    activated: Iterable[ActivatedExtension],
) -> tuple[_message_routing.GenerationMessageRouting, ...]:
    """Collect dependency-neutral route records at the activation-owned seam."""

    routings: list[_message_routing.GenerationMessageRouting] = []
    for item in activated:
        host = item._activation_host or (
            item._pending_activation._host if item._pending_activation else None
        )
        if host is not None:
            routings.append(host._message_routing)
    return tuple(routings)
