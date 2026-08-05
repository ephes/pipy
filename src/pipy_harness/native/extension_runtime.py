"""Extension activation sandbox boundary + runtime dispatch surface.

This module imports an explicit, already-inventoried *loadable* extension
module (from `pipy_harness.native.extensions.packages`), calls its `activate(api)` entry
point, and exposes the registered contributions to the live session. The
activation API supports command and keyboard-shortcut registration, event
hooks, tool and provider registration/unregistration, and `send_user_message`.

It is fail-closed per extension: an import error, a missing or non-callable
`activate`, an exception during activation, or an invalid / duplicate /
reserved command / tool / provider / shortcut registration disables that one
extension with a safe reason code — it never crashes the session and never lets
a bad extension take down the others. Disabled discovery descriptors are never
imported, and a partial registration set is never committed.

Command/shortcut handlers run with a mode-aware `CommandContext` (workspace
root, `ui` with `notify` + `custom` interactive overlays, a read-only
`conversation` view, and a bounded `complete`); the context lives in
`pipy_harness.native.extensions.command_context` and its read-only session
views in `pipy_harness.native.extensions.session_views`. Command output,
handlers, and source code never enter the default archive; project activation
results through `safe_activation_metadata`.

The activation contracts live in `pipy_harness.native.extensions.contracts`,
contribution/outbox collectors in `pipy_harness.native.extensions.collectors`,
flag parsing in `pipy_harness.native.extensions.flag_tokens`, and provider
normalization in `pipy_harness.native.extensions.provider_normalization`.
This module retains activation lifecycle ownership, `activate_extensions`, and
`safe_activation_metadata`. The deliberate public facade remains
`pipy_harness.extensions`.
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import (
    Callable,
    Container,
    Iterable,
    Mapping,
    Sequence,
)
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import (
    Literal,
    NoReturn,
    TypeAlias,
    TypeVar,
    cast,
)

from pipy_harness.native.extension_loader import (
    _import_entry_module,
    _run_awaitable,
)
from pipy_harness.native.extension_types import (
    REASON_ACTIVATION_ERROR,
    REASON_DUPLICATE_COMMAND,
    REASON_DUPLICATE_ENTRY_RENDERER,
    REASON_DUPLICATE_FLAG,
    REASON_DUPLICATE_MESSAGE_RENDERER,
    REASON_DUPLICATE_PROVIDER,
    REASON_DUPLICATE_SHORTCUT,
    REASON_DUPLICATE_TOOL,
    REASON_INVALID_COMMAND_NAME,
    REASON_INVALID_ENTRY_RENDERER,
    REASON_INVALID_FLAG,
    REASON_INVALID_HOOK,
    REASON_INVALID_MESSAGE_RENDERER,
    REASON_INVALID_PROVIDER,
    REASON_INVALID_SHORTCUT,
    REASON_INVALID_TOOL,
    REASON_NO_ACTIVATE,
    REASON_RESERVED_COMMAND,
    REASON_RESERVED_SHORTCUT,
    _ActivationError,
    _is_valid_command_name,
    _safe_diagnostic,
)
from pipy_harness.native.extension_types import (
    REASON_RESERVED_TOOL as REASON_RESERVED_TOOL,
)
from pipy_harness.native.extension_types import (
    RESERVED_SHORTCUT_KEYS as RESERVED_SHORTCUT_KEYS,
)
from pipy_harness.native.extension_types import (
    BeforeAgentStartEvent as BeforeAgentStartEvent,
)
from pipy_harness.native.extension_types import (
    BeforeAgentStartResult as BeforeAgentStartResult,
)
from pipy_harness.native.extension_types import (
    BeforeProviderHeadersEvent as BeforeProviderHeadersEvent,
)
from pipy_harness.native.extension_types import (
    BeforeProviderRequestEvent as BeforeProviderRequestEvent,
)
from pipy_harness.native.extension_types import (
    ChromeComponent as ChromeComponent,
)
from pipy_harness.native.extension_types import (
    CompletionFn as CompletionFn,
)
from pipy_harness.native.extension_types import (
    CustomComponent as CustomComponent,
)
from pipy_harness.native.extension_types import (
    CustomComponentFactory as CustomComponentFactory,
)
from pipy_harness.native.extension_types import (
    EntryRenderContext as EntryRenderContext,
)
from pipy_harness.native.extension_types import (
    ExtensionCodingSessionControl as ExtensionCodingSessionControl,
)
from pipy_harness.native.extension_types import (
    ExtensionFlag as ExtensionFlag,
)
from pipy_harness.native.extension_types import (
    ExtensionModelRuntimeControl as ExtensionModelRuntimeControl,
)
from pipy_harness.native.extension_types import (
    ExtensionOAuthConfig as ExtensionOAuthConfig,
)
from pipy_harness.native.extension_types import (
    ExtensionProvider as ExtensionProvider,
)
from pipy_harness.native.extension_types import (
    ExtensionTool as ExtensionTool,
)
from pipy_harness.native.extension_types import (
    ExtensionUi as ExtensionUi,
)
from pipy_harness.native.extension_types import (
    ExtensionUiDriver as ExtensionUiDriver,
)
from pipy_harness.native.extension_types import (
    FooterData as FooterData,
)
from pipy_harness.native.extension_types import (
    InputEvent as InputEvent,
)
from pipy_harness.native.extension_types import (
    InputTransform as InputTransform,
)
from pipy_harness.native.extension_types import (
    LifecycleEvent as LifecycleEvent,
)
from pipy_harness.native.extension_types import (
    MessageRenderComponent as MessageRenderComponent,
)
from pipy_harness.native.extension_types import (
    MessageRenderContext as MessageRenderContext,
)
from pipy_harness.native.extension_types import (
    ProviderContext as ProviderContext,
)
from pipy_harness.native.extension_types import (
    ProviderRequestTransform as ProviderRequestTransform,
)
from pipy_harness.native.extension_types import (
    QueuedCustomMessage as QueuedCustomMessage,
)
from pipy_harness.native.extension_types import (
    QueuedUserMessage as QueuedUserMessage,
)
from pipy_harness.native.extension_types import (
    RegisteredFlag as RegisteredFlag,
)
from pipy_harness.native.extension_types import (
    RegisteredProvider as RegisteredProvider,
)
from pipy_harness.native.extension_types import (
    RegisteredTool as RegisteredTool,
)
from pipy_harness.native.extension_types import (
    RenderedCustomEntry as RenderedCustomEntry,
)
from pipy_harness.native.extension_types import (
    SessionBeforeEvent as SessionBeforeEvent,
)
from pipy_harness.native.extension_types import (
    SessionDecision as SessionDecision,
)
from pipy_harness.native.extension_types import (
    ThemeColor as ThemeColor,
)
from pipy_harness.native.extension_types import (
    ToolBlock as ToolBlock,
)
from pipy_harness.native.extension_types import (
    ToolCallEvent as ToolCallEvent,
)
from pipy_harness.native.extension_types import (
    ToolRenderComponent as ToolRenderComponent,
)
from pipy_harness.native.extension_types import (
    ToolRenderContext as ToolRenderContext,
)
from pipy_harness.native.extension_types import (
    ToolRenderTheme as ToolRenderTheme,
)
from pipy_harness.native.extension_types import (
    ToolResult as ToolResult,
)
from pipy_harness.native.extension_types import (
    ToolResultEvent as ToolResultEvent,
)
from pipy_harness.native.extension_types import (
    ToolResultTransform as ToolResultTransform,
)
from pipy_harness.native.extension_types import (
    UserBashDecision as UserBashDecision,
)
from pipy_harness.native.extension_types import (
    UserBashDispatch as UserBashDispatch,
)
from pipy_harness.native.extension_types import (
    UserBashEvent as UserBashEvent,
)
from pipy_harness.native.extension_types import (
    WidgetPlacement as WidgetPlacement,
)
from pipy_harness.native.extension_types import (
    is_valid_custom_entry_type as is_valid_custom_entry_type,
)
from pipy_harness.native.extension_types import (
    normalize_shortcut_key as normalize_shortcut_key,
)
from pipy_harness.native.extension_ui import (
    _CollectingUi as _CollectingUi,
)
from pipy_harness.native.extension_ui import (
    coerce_tool_render_lines as coerce_tool_render_lines,
)
from pipy_harness.native.extension_ui import (
    lines_component as lines_component,
)
from pipy_harness.native.extensions import contracts as _contracts
from pipy_harness.native.extensions import custom_payloads as _custom_payloads
from pipy_harness.native.extensions import message_routing as _message_routing
from pipy_harness.native.extensions import (
    provider_normalization as _provider_normalization,
)
from pipy_harness.native.extensions.command_context import ExtensionCapabilityError
from pipy_harness.native.extensions.contribution_names import (
    _ContributionNames,
    _normalize_contribution_names,
    _preloaded_collision_reason,
    _prepare_contribution_names_commit,
    _TakenContributionState,
)
from pipy_harness.native.extensions.packages import ExtensionDescriptor
from pipy_harness.native.tools.base import ToolDefinition

# Event names (the dispatched subset grows per slice).
EVENT_TOOL_CALL: str = "tool_call"
EVENT_PROJECT_TRUST: str = "project_trust"
EVENT_SESSION_START: str = "session_start"
EVENT_SESSION_SHUTDOWN: str = "session_shutdown"
EVENT_AGENT_START: str = "agent_start"
EVENT_AGENT_END: str = "agent_end"
EVENT_AGENT_SETTLED: str = "agent_settled"
EVENT_TURN_START: str = "turn_start"
EVENT_TURN_END: str = "turn_end"
EVENT_INPUT: str = "input"
EVENT_BEFORE_AGENT_START: str = "before_agent_start"
EVENT_TOOL_RESULT: str = "tool_result"
EVENT_USER_BASH: str = "user_bash"
EVENT_BEFORE_PROVIDER_REQUEST: str = "before_provider_request"
EVENT_BEFORE_PROVIDER_HEADERS: str = "before_provider_headers"
EVENT_SESSION_BEFORE_SWITCH: str = "session_before_switch"
EVENT_SESSION_BEFORE_FORK: str = "session_before_fork"
EVENT_SESSION_BEFORE_COMPACT: str = "session_before_compact"
EVENT_SESSION_BEFORE_TREE: str = "session_before_tree"

LIFECYCLE_EVENTS: tuple[str, ...] = (
    EVENT_SESSION_START,
    EVENT_SESSION_SHUTDOWN,
    EVENT_AGENT_START,
    EVENT_AGENT_END,
    EVENT_AGENT_SETTLED,
    EVENT_TURN_START,
    EVENT_TURN_END,
)


@dataclass(frozen=True, slots=True)
class _FrozenActivation:
    """One atomic, immutable view of a sealed activation host."""

    commands: tuple[_contracts.RegisteredCommand, ...]
    tools: tuple[RegisteredTool, ...]
    providers: tuple[RegisteredProvider, ...]
    unregistered_providers: tuple[str, ...]
    shortcuts: tuple[_contracts.RegisteredShortcut, ...]
    flags: tuple[RegisteredFlag, ...]
    message_renderers: tuple[_contracts.RegisteredMessageRenderer, ...]
    entry_renderers: tuple[_contracts.RegisteredEntryRenderer, ...]
    hooks: Mapping[str, tuple[_contracts.HookHandler, ...]]
    user_messages: tuple[QueuedUserMessage, ...]
    custom_messages: tuple[QueuedCustomMessage, ...]
    failure: tuple[str, str | None] | None


@dataclass(frozen=True, slots=True)
class _NormalizedFlagRegistration:
    """A normalized flag definition and its optional initial value."""

    registered: RegisteredFlag
    default: bool | str | None


_RegistrationFamily: TypeAlias = Literal[
    "command",
    "shortcut",
    "tool",
    "provider",
    "flag",
    "message_renderer",
    "entry_renderer",
]
_RegistrationValue: TypeAlias = (
    _contracts.RegisteredCommand
    | _contracts.RegisteredShortcut
    | RegisteredTool
    | RegisteredProvider
    | _NormalizedFlagRegistration
    | _contracts.RegisteredMessageRenderer
    | _contracts.RegisteredEntryRenderer
)
_RegistrationOrdering: TypeAlias = Literal[
    "availability_before_value",
    "value_before_availability",
]
_RegistrationResult = TypeVar("_RegistrationResult")

_REGISTRATION_INVALID_REASONS: Mapping[_RegistrationFamily, str] = MappingProxyType(
    {
        "command": REASON_INVALID_COMMAND_NAME,
        "shortcut": REASON_INVALID_SHORTCUT,
        "tool": REASON_INVALID_TOOL,
        "provider": REASON_INVALID_PROVIDER,
        "flag": REASON_INVALID_FLAG,
        "message_renderer": REASON_INVALID_MESSAGE_RENDERER,
        "entry_renderer": REASON_INVALID_ENTRY_RENDERER,
    }
)
_REGISTRATION_ORDERING: Mapping[_RegistrationFamily, _RegistrationOrdering] = (
    MappingProxyType(
        {
            "command": "availability_before_value",
            "shortcut": "availability_before_value",
            "tool": "availability_before_value",
            "provider": "value_before_availability",
            "flag": "availability_before_value",
            "message_renderer": "value_before_availability",
            "entry_renderer": "value_before_availability",
        }
    )
)


@dataclass(frozen=True, slots=True)
class _ProviderCatalogFinalization:
    """Bounded outcome from terminally finalizing accepted catalog hosts."""

    finalized: int = 0
    refused_disposed: int = 0
    refused_published: int = 0
    refused_already_terminal: int = 0
    inaccessible: int = 0

    @property
    def anomaly_diagnostic(self) -> str | None:
        if not (
            self.refused_disposed
            or self.refused_published
            or self.refused_already_terminal
            or self.inaccessible
        ):
            return None
        return (
            "pipy: extension provider catalog finalization anomalies: "
            f"{self.refused_disposed} refused host(s) disposed, "
            f"{self.refused_published} published host(s) skipped live, "
            f"{self.refused_already_terminal} refused host(s) already terminal, "
            f"and {self.inaccessible} inaccessible/failing host guard(s)."
        )


class _PendingActivation:
    """Minimum one-shot holder used before a pre-trust host is composed."""

    def __init__(self, host: "_ActivationApi") -> None:
        self._host: _ActivationApi | None = host

    def claim(self) -> "_ActivationApi | None":
        """Transfer this session-thread-owned pending host exactly once."""

        host = self._host
        self._host = None
        return host

    def dispose(self) -> "_ActivationCleanup":
        """Dispose an unclaimed host, retaining it only when cleanup cannot enter."""

        host = self._host
        if host is None:
            return _ActivationCleanup()
        cleanup = _dispose_activation_hosts((host,))
        if cleanup.failed == 0:
            self._host = None
        return cleanup


_ACTIVATION_LIFECYCLE_TOKEN = object()
_ACTIVATION_PUBLICATION_TOKEN = object()


@dataclass(frozen=True, slots=True)
class _ActivationCleanup:
    """Structured result from bounded per-host candidate cleanup."""

    disposed: int = 0
    skipped_published: int = 0
    failed: int = 0

    def merge(self, other: "_ActivationCleanup") -> "_ActivationCleanup":
        return _ActivationCleanup(
            disposed=self.disposed + other.disposed,
            skipped_published=self.skipped_published + other.skipped_published,
            failed=self.failed + other.failed,
        )

    @property
    def anomaly_diagnostic(self) -> str | None:
        if not self.skipped_published and not self.failed:
            return None
        return (
            "pipy: extension candidate cleanup skipped "
            f"{self.skipped_published} published and {self.failed} inaccessible "
            "activation host(s)."
        )


def _report_activation_cleanup(
    cleanup: _ActivationCleanup,
    diagnostic: Callable[[str], None] | None,
) -> None:
    """Route cleanup anomalies through an existing diagnostic sink when present."""

    message = cleanup.anomaly_diagnostic
    if diagnostic is not None and message is not None:
        diagnostic(message)


class _ActivationApi:
    """Concrete `PipyExtensionAPI` for one extension's activation.

    Command registrations are validated eagerly and staged on this api;
    they are only committed to the global registry once `activate`
    completes without error, so a later failure never leaves a partial
    registration behind.
    """

    def __init__(
        self,
        extension_name: str,
        *,
        reserved: frozenset[str],
        taken: frozenset[str],
        outbox: list[QueuedUserMessage],
        custom_outbox: list[QueuedCustomMessage],
        reserved_tools: frozenset[str] = frozenset(),
        taken_tools: frozenset[str] = frozenset(),
        taken_providers: frozenset[str] = frozenset(),
        taken_shortcuts: frozenset[str] = frozenset(),
        taken_flags: frozenset[str] = frozenset(),
        message_routing: _message_routing.GenerationMessageRouting,
        taken_message_renderers: frozenset[str] = frozenset(),
        taken_entry_renderers: frozenset[str] = frozenset(),
        guard: AbstractContextManager[object] | None = None,
        boundary_observer: Callable[[str], None] | None = None,
    ) -> None:
        self._extension_name = extension_name
        self._guard: AbstractContextManager[object] = (
            guard if guard is not None else threading.RLock()
        )
        self._state: Literal[
            "open",
            "sealed",
            "committed",
            "published",
            "catalog_finalized",
            "disposed",
        ] = "open"
        self._publication_token: object | None = None
        self._reserved = reserved
        self._taken = taken
        self._reserved_tools = reserved_tools
        self._taken_tools = taken_tools
        self._taken_providers = taken_providers
        self._taken_shortcuts = taken_shortcuts
        self._taken_flags = taken_flags
        self._taken_message_renderers = taken_message_renderers
        self._taken_entry_renderers = taken_entry_renderers
        self._outbox = outbox
        self._custom_outbox = custom_outbox
        if (
            message_routing.user_outbox is not outbox
            or message_routing.custom_outbox is not custom_outbox
        ):
            raise ValueError("activation host routing must match its exact lists")
        self._message_routing = message_routing
        self._message_route_authority: object | None = None
        self._boundary_observer = boundary_observer
        self._staged: dict[str, _contracts.RegisteredCommand] = {}
        self._staged_shortcuts: dict[str, _contracts.RegisteredShortcut] = {}
        self._staged_tools: dict[str, RegisteredTool] = {}
        self._staged_providers: dict[str, RegisteredProvider] = {}
        self._staged_unregistered: list[str] = []
        self._staged_flags: dict[str, RegisteredFlag] = {}
        self._flag_values: dict[str, object] = {}
        self._staged_message_renderers: dict[
            str, _contracts.RegisteredMessageRenderer
        ] = {}
        self._staged_entry_renderers: dict[str, _contracts.RegisteredEntryRenderer] = {}
        self._hooks: dict[str, list[_contracts.HookHandler]] = {}
        self._failure: tuple[str, str | None] | None = None
        # Messages are staged during activation and only committed to the
        # shared outbox once activation succeeds, so a disabled extension
        # never leaves a queued prompt behind. After activation commits,
        # runtime calls (from command handlers / hooks) append directly.
        self._staged_messages: list[QueuedUserMessage] = []
        self._staged_custom_messages: list[QueuedCustomMessage] = []
        self._frozen_activation: _FrozenActivation | None = None
        self._activated = False

    def _observe_boundary(self, event: str) -> None:
        observer = self._boundary_observer
        if observer is not None:
            observer(event)

    def _accept_message_route(self) -> None:
        with self._guard:
            if self._state != "sealed" or self._message_route_authority is not None:
                raise ExtensionCapabilityError("extension activation is unavailable")
            self._message_route_authority = object()

    _STATE_TRANSITIONS: Mapping[str, frozenset[str]] = MappingProxyType(
        {
            "open": frozenset(("sealed", "disposed")),
            "sealed": frozenset(("committed", "disposed")),
            "committed": frozenset(("published", "catalog_finalized", "disposed")),
            "published": frozenset(),
            "catalog_finalized": frozenset(),
            "disposed": frozenset(),
        }
    )

    def _require_open_registration(self) -> None:
        if self._state != "open":
            raise ExtensionCapabilityError(
                "extension contribution registration is closed"
            )

    def _allows_transition_locked(
        self,
        target: str,
        *,
        publication_token: object | None = None,
    ) -> bool:
        if (
            target == "published"
            and publication_token is not _ACTIVATION_PUBLICATION_TOKEN
        ):
            return False
        return target in self._STATE_TRANSITIONS.get(self._state, frozenset())

    def _transition_locked(
        self,
        target: str,
        *,
        publication_token: object | None = None,
    ) -> bool:
        """Apply one host-authored lifecycle edge while ``_guard`` is held."""

        if not self._allows_transition_locked(
            target,
            publication_token=publication_token,
        ):
            return False
        self._state = cast(
            Literal[
                "open",
                "sealed",
                "committed",
                "published",
                "catalog_finalized",
                "disposed",
            ],
            target,
        )
        if target == "published":
            self._publication_token = publication_token
        return True

    def _is_published_locked(self) -> bool:
        return (
            self._state == "published"
            and self._publication_token is _ACTIVATION_PUBLICATION_TOKEN
        )

    def _record_failure(self, err: _ActivationError) -> None:
        if self._failure is None:
            self._failure = (err.reason, err.diagnostic)

    def _check_registration_open(self) -> None:
        """Check lifecycle state without running extension-controlled code."""

        with self._guard:
            self._require_open_registration()

    def _raise_registration_failure(self, err: _ActivationError) -> NoReturn:
        """Record a validation failure only if registration is still open."""

        with self._guard:
            self._require_open_registration()
            self._record_failure(err)
        raise err

    def _validate_registration_unlocked(
        self,
        validate: Callable[[], _RegistrationResult],
        *,
        invalid_reason: str,
    ) -> _RegistrationResult:
        """Bound extension-controlled validation without holding the host guard."""

        try:
            return validate()
        except _ActivationError as err:
            self._raise_registration_failure(err)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as err:  # noqa: BLE001 - hostile extension value
            self._raise_registration_failure(
                _ActivationError(invalid_reason, _safe_diagnostic(err))
            )

    def _stage_registration(
        self,
        family: _RegistrationFamily,
        normalize_name: Callable[[], str],
        normalize_value: Callable[[str], _RegistrationValue],
    ) -> None:
        """Validate unlocked in the family's historical order and commit atomically."""

        self._check_registration_open()
        invalid_reason = _REGISTRATION_INVALID_REASONS[family]
        name = self._validate_registration_unlocked(
            normalize_name,
            invalid_reason=invalid_reason,
        )
        ordering = _REGISTRATION_ORDERING[family]
        if ordering == "availability_before_value":
            with self._guard:
                self._require_open_registration()
                self._check_registration_available_locked(family, name)
        value = self._validate_registration_unlocked(
            lambda: normalize_value(name),
            invalid_reason=invalid_reason,
        )
        with self._guard:
            self._require_open_registration()
            self._check_registration_available_locked(family, name)
            self._commit_registration_locked(family, name, value)

    def _check_registration_available_locked(
        self,
        family: _RegistrationFamily,
        name: str,
    ) -> None:
        registries: Mapping[
            _RegistrationFamily,
            tuple[Container[str], Container[str], str],
        ] = {
            "command": (self._taken, self._staged, REASON_DUPLICATE_COMMAND),
            "shortcut": (
                self._taken_shortcuts,
                self._staged_shortcuts,
                REASON_DUPLICATE_SHORTCUT,
            ),
            "tool": (self._taken_tools, self._staged_tools, REASON_DUPLICATE_TOOL),
            "provider": (
                self._taken_providers,
                self._staged_providers,
                REASON_DUPLICATE_PROVIDER,
            ),
            "flag": (self._taken_flags, self._staged_flags, REASON_DUPLICATE_FLAG),
            "message_renderer": (
                self._taken_message_renderers,
                self._staged_message_renderers,
                REASON_DUPLICATE_MESSAGE_RENDERER,
            ),
            "entry_renderer": (
                self._taken_entry_renderers,
                self._staged_entry_renderers,
                REASON_DUPLICATE_ENTRY_RENDERER,
            ),
        }
        reserved_reason: str | None = None
        if family == "command" and name in self._reserved:
            reserved_reason = REASON_RESERVED_COMMAND
        elif family == "shortcut" and name in RESERVED_SHORTCUT_KEYS:
            reserved_reason = REASON_RESERVED_SHORTCUT
        elif family == "tool" and name in self._reserved_tools:
            reserved_reason = REASON_RESERVED_TOOL
        taken, staged, duplicate_reason = registries[family]
        failure_reason = reserved_reason
        if failure_reason is None and (name in taken or name in staged):
            failure_reason = duplicate_reason
        if failure_reason is not None:
            failure = _ActivationError(failure_reason)
            self._record_failure(failure)
            raise failure

    def _commit_registration_locked(
        self,
        family: _RegistrationFamily,
        name: str,
        value: _RegistrationValue,
    ) -> None:
        if family == "command" and isinstance(value, _contracts.RegisteredCommand):
            self._staged[name] = value
        elif family == "shortcut" and isinstance(value, _contracts.RegisteredShortcut):
            self._staged_shortcuts[name] = value
        elif family == "tool" and isinstance(value, RegisteredTool):
            self._staged_tools[name] = value
        elif family == "provider" and isinstance(value, RegisteredProvider):
            self._staged_providers[name] = value
        elif family == "flag" and isinstance(value, _NormalizedFlagRegistration):
            self._staged_flags[name] = value.registered
            if value.default is not None:
                self._flag_values[name] = value.default
        elif family == "message_renderer" and isinstance(
            value, _contracts.RegisteredMessageRenderer
        ):
            self._staged_message_renderers[name] = value
        elif family == "entry_renderer" and isinstance(
            value, _contracts.RegisteredEntryRenderer
        ):
            self._staged_entry_renderers[name] = value
        else:
            raise AssertionError(f"invalid normalized {family} registration")

    def _reserve_message(
        self,
        message: QueuedUserMessage | QueuedCustomMessage,
    ) -> _message_routing.GenerationMessageReservation | None:
        self._observe_boundary("host_guard_enter")
        with self._guard:
            if self._state == "open":
                if isinstance(message, QueuedUserMessage):
                    self._staged_messages.append(message)
                else:
                    self._staged_custom_messages.append(message)
                reservation = None
            elif (
                self._message_route_authority is not None
                and self._state in ("sealed", "committed", "published")
                and self._message_routing.user_outbox is self._outbox
                and self._message_routing.custom_outbox is self._custom_outbox
            ):
                allow_fallback = (
                    self._state in ("committed", "published") and self._activated
                )
                if isinstance(message, QueuedUserMessage):
                    delivery, forwarding, live_forwarding = (
                        _message_routing._reserved_message_delivery(
                            self._message_routing,
                            self._outbox,
                            message,
                            self._message_routing._append_live_user,
                        )
                    )
                else:
                    delivery, forwarding, live_forwarding = (
                        _message_routing._reserved_message_delivery(
                            self._message_routing,
                            self._custom_outbox,
                            message,
                            self._message_routing._append_live_custom,
                        )
                    )
                reservation = _message_routing.GenerationMessageReservation(
                    self._message_routing,
                    delivery,
                    forwarding,
                    live_forwarding,
                    allow_fallback,
                )
            else:
                reservation = None
        self._observe_boundary("host_guard_exit")
        return reservation

    def send_user_message(
        self,
        content: str,
        options: Mapping[str, object] | None = None,
    ) -> None:
        """Enqueue a deterministic user turn (drained by the session loop)."""

        reservation = self._reserve_message(
            QueuedUserMessage(content=str(content), options=dict(options or {}))
        )
        if reservation is not None:
            reservation.owner.accept(reservation)

    def send_message(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> None:
        """Stage a custom session message until activation succeeds."""

        reservation = self._reserve_message(
            _custom_payloads.coerce_custom_message(message, options)
        )
        if reservation is not None:
            reservation.owner.accept(reservation)

    def sendMessage(
        self,
        message: Mapping[str, object],
        options: Mapping[str, object] | None = None,
    ) -> None:
        self.send_message(message, options)

    def _commit_activation(
        self,
        *,
        _lifecycle_token: object | None = None,
    ) -> tuple[QueuedCustomMessage, ...]:
        """Host-internal commit of a sealed activation exactly once."""

        if _lifecycle_token is not _ACTIVATION_LIFECYCLE_TOKEN:
            raise ExtensionCapabilityError("extension activation is unavailable")
        with self._guard:
            snapshot = self._frozen_activation
            if snapshot is None or not self._transition_locked("committed"):
                raise ExtensionCapabilityError("extension activation is unavailable")
            self._activated = True
        # The one seal-time snapshot is authoritative. Sends racing after seal
        # are silent no-ops, so finalization flushes exactly these messages once.
        self._observe_boundary("frozen_commit_flush")
        self._outbox.extend(snapshot.user_messages)
        return snapshot.custom_messages

    def register_tool(self, tool: ExtensionTool) -> None:
        self._stage_registration(
            "tool",
            lambda: self._normalize_tool_name(tool),
            lambda name: self._normalize_tool(tool, name=name),
        )

    @staticmethod
    def _normalize_tool_name(tool: ExtensionTool) -> str:
        if not isinstance(tool, ExtensionTool):
            raise _ActivationError(REASON_INVALID_TOOL)
        name = _provider_normalization._coerce_activation_string(
            tool.name, REASON_INVALID_TOOL
        )
        if not name:
            raise _ActivationError(REASON_INVALID_TOOL)
        return name

    def _normalize_tool(self, tool: ExtensionTool, *, name: str) -> RegisteredTool:
        """Validate extension-controlled non-name values without the host guard."""

        description = tool.description
        input_schema = tool.input_schema
        handler = tool.handler
        if not callable(handler) or not isinstance(input_schema, Mapping):
            raise _ActivationError(REASON_INVALID_TOOL)
        try:
            normalized_description = str(description)
            normalized_schema = dict(input_schema)
            ToolDefinition(
                name=name,
                description=normalized_description,
                input_schema=normalized_schema,
            )
        except (ValueError, TypeError) as exc:
            raise _ActivationError(REASON_INVALID_TOOL, _safe_diagnostic(exc)) from None
        normalized = replace(
            tool,
            name=name,
            description=normalized_description,
            input_schema=normalized_schema,
        )
        return RegisteredTool(tool=normalized, extension=self._extension_name)

    def register_provider(self, provider: ExtensionProvider) -> None:
        self._stage_registration(
            "provider",
            lambda: self._normalize_provider_registration_name(provider),
            lambda name: self._normalize_provider(provider, name=name),
        )

    @staticmethod
    def _normalize_provider_registration_name(provider: ExtensionProvider) -> str:
        if not isinstance(provider, ExtensionProvider):
            raise _ActivationError(REASON_INVALID_PROVIDER)
        return _provider_normalization._normalize_provider_name(provider.name)

    def _normalize_provider(
        self,
        provider: ExtensionProvider,
        *,
        name: str,
    ) -> RegisteredProvider:
        """Validate extension-controlled provider values without the guard."""

        factory = provider.factory
        raw_models = provider.models
        raw_default = provider.default_model
        raw_oauth = provider.oauth
        if not callable(factory):
            raise _ActivationError(REASON_INVALID_PROVIDER)
        model_ids = _provider_normalization._normalize_provider_models(raw_models)
        default_model = _provider_normalization._normalize_default_model(
            raw_default, model_ids
        )
        oauth = _provider_normalization._normalize_provider_oauth(raw_oauth)
        normalized = replace(
            provider,
            name=name,
            default_model=default_model,
            models=model_ids,
            oauth=oauth,
        )
        return RegisteredProvider(
            provider=normalized,
            extension=self._extension_name,
        )

    def unregister_provider(self, name: str) -> None:
        self._check_registration_open()
        try:
            normalized = _provider_normalization._normalize_provider_name(name)
        except _ActivationError as err:
            self._raise_registration_failure(err)
        with self._guard:
            self._require_open_registration()
            if normalized not in self._staged_unregistered:
                self._staged_unregistered.append(normalized)

    def register_flag(self, flag: ExtensionFlag) -> None:
        self._stage_registration(
            "flag",
            lambda: self._normalize_flag_name(flag),
            lambda name: self._normalize_flag(flag, name=name),
        )

    @staticmethod
    def _normalize_flag_name(flag: ExtensionFlag) -> str:
        if not isinstance(flag, ExtensionFlag):
            raise _ActivationError(REASON_INVALID_FLAG)
        name = _provider_normalization._coerce_activation_string(
            flag.name, REASON_INVALID_FLAG
        ).strip()
        if not _is_valid_command_name(name):
            raise _ActivationError(REASON_INVALID_FLAG)
        return name

    def _normalize_flag(
        self,
        flag: ExtensionFlag,
        *,
        name: str,
    ) -> _NormalizedFlagRegistration:
        raw_flag_type = flag.flag_type
        description = flag.description
        default = flag.default
        flag_type = _provider_normalization._coerce_activation_string(
            raw_flag_type, REASON_INVALID_FLAG
        )
        if flag_type not in ("boolean", "string"):
            raise _ActivationError(REASON_INVALID_FLAG)
        flag_type = cast(Literal["boolean", "string"], flag_type)
        if flag_type == "boolean" and default is not None and type(default) is not bool:
            raise _ActivationError(REASON_INVALID_FLAG)
        if flag_type == "string" and default is not None:
            default = _provider_normalization._coerce_activation_string(
                default, REASON_INVALID_FLAG
            )
        normalized_description = (
            description
            if description is None or type(description) is str
            else str(description)
        )
        definition = replace(
            flag,
            name=name,
            flag_type=flag_type,
            description=normalized_description,
            default=default,
        )
        return _NormalizedFlagRegistration(
            registered=RegisteredFlag(
                flag=definition,
                extension=self._extension_name,
                _get_value=self._get_flag_value,
                _set_value=self._set_flag_value,
            ),
            default=default,
        )

    def get_flag(self, name: str) -> object | None:
        try:
            normalized = _provider_normalization._coerce_activation_string(
                name, REASON_INVALID_FLAG
            )
        except _ActivationError:
            return None
        return self._get_flag_value(normalized)

    def _get_flag_value(self, name: str) -> object | None:
        with self._guard:
            return self._flag_values.get(name)

    def _set_flag_value(self, name: str, value: object) -> None:
        with self._guard:
            if self._state in ("catalog_finalized", "disposed"):
                return
            self._flag_values[name] = value

    def register_message_renderer(
        self,
        custom_type: str,
        renderer: Callable[..., object],
    ) -> None:
        self._stage_registration(
            "message_renderer",
            lambda: self._normalize_renderer_name(
                custom_type, reason=REASON_INVALID_MESSAGE_RENDERER
            ),
            lambda name: self._normalize_message_renderer(name, renderer),
        )

    @staticmethod
    def _normalize_renderer_name(custom_type: str, *, reason: str) -> str:
        name = _provider_normalization._coerce_activation_string(
            custom_type, reason
        ).strip()
        if not is_valid_custom_entry_type(name):
            raise _ActivationError(reason)
        return name

    def _normalize_message_renderer(
        self,
        name: str,
        renderer: Callable[..., object],
    ) -> _contracts.RegisteredMessageRenderer:
        if not callable(renderer):
            raise _ActivationError(REASON_INVALID_MESSAGE_RENDERER)
        return _contracts.RegisteredMessageRenderer(
            custom_type=name,
            renderer=renderer,
            extension=self._extension_name,
        )

    def register_entry_renderer(
        self,
        custom_type: str,
        renderer: Callable[..., object],
    ) -> None:
        self._stage_registration(
            "entry_renderer",
            lambda: self._normalize_renderer_name(
                custom_type, reason=REASON_INVALID_ENTRY_RENDERER
            ),
            lambda name: self._normalize_entry_renderer(name, renderer),
        )

    def _normalize_entry_renderer(
        self,
        name: str,
        renderer: Callable[..., object],
    ) -> _contracts.RegisteredEntryRenderer:
        if not callable(renderer):
            raise _ActivationError(REASON_INVALID_ENTRY_RENDERER)
        return _contracts.RegisteredEntryRenderer(
            custom_type=name,
            renderer=renderer,
            extension=self._extension_name,
        )

    def register_command(
        self,
        name: str,
        description: str,
        handler: _contracts.CommandHandler,
    ) -> None:
        self._stage_registration(
            "command",
            lambda: self._normalize_command_name(name),
            lambda normalized: self._normalize_command(
                normalized, description, handler
            ),
        )

    @staticmethod
    def _normalize_command_name(name: str) -> str:
        normalized = _provider_normalization._coerce_activation_string(
            name, REASON_INVALID_COMMAND_NAME
        )
        if not _is_valid_command_name(normalized):
            raise _ActivationError(REASON_INVALID_COMMAND_NAME)
        return normalized

    def _normalize_command(
        self,
        name: str,
        description: str,
        handler: _contracts.CommandHandler,
    ) -> _contracts.RegisteredCommand:
        if not callable(handler):
            raise _ActivationError(REASON_INVALID_COMMAND_NAME)
        return _contracts.RegisteredCommand(
            name=name,
            description=str(description),
            handler=handler,
            extension=self._extension_name,
        )

    def register_shortcut(self, key: str, handler: _contracts.CommandHandler) -> None:
        self._stage_registration(
            "shortcut",
            lambda: self._normalize_shortcut_name(key, handler),
            lambda normalized: self._normalize_shortcut(normalized, handler),
        )

    @staticmethod
    def _normalize_shortcut_name(key: str, handler: _contracts.CommandHandler) -> str:
        plain_key = _provider_normalization._coerce_activation_string(
            key, REASON_INVALID_SHORTCUT
        )
        if not plain_key.strip() or not callable(handler):
            raise _ActivationError(REASON_INVALID_SHORTCUT)
        normalized = normalize_shortcut_key(plain_key)
        if len(normalized) <= 1 or normalized.endswith("-"):
            raise _ActivationError(REASON_INVALID_SHORTCUT)
        return normalized

    def _normalize_shortcut(
        self,
        normalized: str,
        handler: _contracts.CommandHandler,
    ) -> _contracts.RegisteredShortcut:
        return _contracts.RegisteredShortcut(
            key=normalized,
            handler=handler,
            extension=self._extension_name,
        )

    def on(
        self,
        event: str,
        handler: _contracts.HookHandler | None = None,
    ) -> object:
        """Register an event hook. Supports decorator and direct forms.

        `api.on("tool_call", handler)` registers directly;
        `@api.on("tool_call")` returns a decorator. Any non-empty event
        name is accepted (only dispatched events fire); an invalid event
        or non-callable handler records a failure and re-raises, so the
        extension is disabled even if it swallows the error.
        """

        self._check_registration_open()
        try:
            normalized_event = self._normalize_hook_event(event)
        except _ActivationError as err:
            self._raise_registration_failure(err)
        if handler is None:
            # Refuse if seal won while the event was being normalized.
            self._check_registration_open()

            def _decorator(func: _contracts.HookHandler) -> _contracts.HookHandler:
                self._register_hook(normalized_event, func)
                return func

            return _decorator
        self._register_hook(normalized_event, handler)
        return handler

    @staticmethod
    def _normalize_hook_event(event: str) -> str:
        normalized = _provider_normalization._coerce_activation_string(
            event, REASON_INVALID_HOOK
        )
        if not normalized:
            raise _ActivationError(REASON_INVALID_HOOK)
        return normalized

    def _register_hook(self, event: str, handler: _contracts.HookHandler) -> None:
        self._check_registration_open()
        if not callable(handler):
            self._raise_registration_failure(_ActivationError(REASON_INVALID_HOOK))
        with self._guard:
            self._require_open_registration()
            self._hooks.setdefault(event, []).append(handler)

    def _seal_and_freeze(
        self,
        *,
        _lifecycle_token: object | None = None,
    ) -> _FrozenActivation:
        """Host-internally seal registration and return one complete snapshot."""

        if _lifecycle_token is not _ACTIVATION_LIFECYCLE_TOKEN:
            raise ExtensionCapabilityError("extension activation is unavailable")
        with self._guard:
            if not self._transition_locked("sealed"):
                raise ExtensionCapabilityError("extension activation is unavailable")
            self._activated = False
            user_messages = tuple(self._staged_messages)
            custom_messages = tuple(self._staged_custom_messages)
            snapshot = _FrozenActivation(
                commands=tuple(self._staged.values()),
                tools=tuple(self._staged_tools.values()),
                providers=tuple(self._staged_providers.values()),
                unregistered_providers=tuple(self._staged_unregistered),
                shortcuts=tuple(self._staged_shortcuts.values()),
                flags=tuple(self._staged_flags.values()),
                message_renderers=tuple(self._staged_message_renderers.values()),
                entry_renderers=tuple(self._staged_entry_renderers.values()),
                hooks=MappingProxyType(
                    {event: tuple(handlers) for event, handlers in self._hooks.items()}
                ),
                user_messages=user_messages,
                custom_messages=custom_messages,
                failure=self._failure,
            )
            self._frozen_activation = snapshot
            # Detach mutable staging queues at the seal boundary. Commit reads
            # only ``_frozen_activation``; no later send can enter this value.
            self._staged_messages = []
            self._staged_custom_messages = []
        return snapshot

    def _clear_terminal_storage_locked(self, *, retain_flag_values: bool) -> None:
        """Drop all staging/outbox reachability while ``_guard`` is held."""

        self._activated = False
        self._message_route_authority = None
        self._reserved = frozenset()
        self._taken = frozenset()
        self._reserved_tools = frozenset()
        self._taken_tools = frozenset()
        self._taken_providers = frozenset()
        self._taken_shortcuts = frozenset()
        self._taken_flags = frozenset()
        self._taken_message_renderers = frozenset()
        self._taken_entry_renderers = frozenset()
        # Replace rather than call extension-corruptible container methods.
        self._staged = {}
        self._staged_shortcuts = {}
        self._hooks = {}
        self._staged_tools = {}
        self._staged_providers = {}
        self._staged_unregistered = []
        self._staged_flags = {}
        if not retain_flag_values:
            self._flag_values = {}
        self._staged_message_renderers = {}
        self._staged_entry_renderers = {}
        self._staged_messages = []
        self._staged_custom_messages = []
        self._frozen_activation = None
        self._failure = None
        self._publication_token = None
        self._outbox = []
        self._custom_outbox = []

    def _finalize_provider_catalog_locked(self) -> bool:
        """Enter the accepted-catalog terminal state while retaining flag reads."""

        if not self._transition_locked("catalog_finalized"):
            return False
        self._clear_terminal_storage_locked(retain_flag_values=True)
        return True

    def _dispose_locked(self) -> bool:
        """Fail closed while ``_guard`` is held, including corrupted states."""

        if self._is_published_locked() or self._state == "catalog_finalized":
            return False
        changed = self._state != "disposed"
        if changed and not self._transition_locked("disposed"):
            # Trusted Python can corrupt private attributes. Such a value is
            # not a lifecycle state; terminal disposal is the bounded recovery.
            self._state = "disposed"
        self._clear_terminal_storage_locked(retain_flag_values=False)
        return changed

    def _dispose(self) -> bool:
        """Host-internally clear candidate state without disposing a live host."""

        with self._guard:
            return self._dispose_locked()


def _publish_activation_hosts_atomically(
    hosts: tuple[_ActivationApi, ...],
) -> bool:
    """Validate all host-authored transitions, then publish the complete set."""

    with ExitStack() as guards:
        for host in hosts:
            guards.enter_context(host._guard)
        if any(
            not _ActivationApi._allows_transition_locked(
                host,
                "published",
                publication_token=_ACTIVATION_PUBLICATION_TOKEN,
            )
            for host in hosts
        ):
            return False
        for host in hosts:
            if not _ActivationApi._transition_locked(
                host,
                "published",
                publication_token=_ACTIVATION_PUBLICATION_TOKEN,
            ):
                raise AssertionError("validated activation host did not publish")
    return True


def _dispose_activation_hosts(
    hosts: tuple[_ActivationApi, ...],
) -> _ActivationCleanup:
    """Dispose every unpublished host and return bounded anomaly details."""

    disposed = 0
    skipped_published = 0
    failed = 0
    for host in hosts:
        try:
            with host._guard:
                if _ActivationApi._is_published_locked(host):
                    skipped_published += 1
                    continue
                if _ActivationApi._dispose_locked(host):
                    disposed += 1
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - continue bounded sibling cleanup
            failed += 1
    return _ActivationCleanup(
        disposed=disposed,
        skipped_published=skipped_published,
        failed=failed,
    )


def _dispose_activation_host_with_diagnostic(
    api: _ActivationApi,
    diagnostic: Callable[[str], None] | None,
) -> None:
    cleanup = _dispose_activation_hosts((api,))
    _report_activation_cleanup(cleanup, diagnostic)


def activate_extensions(
    descriptors: Sequence[ExtensionDescriptor],
    *,
    reserved_command_names: Sequence[str] = (),
    reserved_tool_names: Sequence[str] = (),
    message_outbox: list[QueuedUserMessage] | None = None,
    custom_message_outbox: list[QueuedCustomMessage] | None = None,
    message_routing: _message_routing.GenerationMessageRouting | None = None,
    diagnostic: Callable[[str], None] | None = None,
) -> list[_contracts.ActivatedExtension]:
    """Activate the loadable descriptors, in order.

    Disabled discovery descriptors are passed through unchanged (never
    imported). Each loadable descriptor is imported and activated in
    isolation; any failure disables only that extension. Command names
    are deduplicated across all extensions in this pass (first
    registration wins; a later collision disables the later extension).

    `message_outbox` is the shared list that `api.send_user_message`
    appends to; the session drains it with `drain_user_messages`. When
    omitted, a private outbox is used (messages are simply unread).
    """

    batch = activate_extension_batch(
        descriptors,
        reserved_command_names=reserved_command_names,
        reserved_tool_names=reserved_tool_names,
        message_outbox=message_outbox,
        custom_message_outbox=custom_message_outbox,
        message_routing=message_routing,
        diagnostic=diagnostic,
    )
    return list(batch.activated)


def _dispose_activation_results(
    activated: Iterable[_contracts.ActivatedExtension],
) -> _ActivationCleanup:
    """Dispose every host still owned by uncomposed activation results."""

    cleanup = _ActivationCleanup()
    for extension in activated:
        pending_activation = extension._pending_activation
        if pending_activation is not None:
            cleanup = cleanup.merge(
                cast(_PendingActivation, pending_activation).dispose()
            )
        activation_host = extension._activation_host
        if activation_host is not None:
            cleanup = cleanup.merge(
                _dispose_activation_hosts((cast(_ActivationApi, activation_host),))
            )
    return cleanup


def _finalize_provider_catalog_results(
    activated: Iterable[_contracts.ActivatedExtension],
) -> _ProviderCatalogFinalization:
    """Terminally retain only guarded flag reads needed by accepted factories."""

    finalized = 0
    refused_disposed = 0
    refused_published = 0
    refused_already_terminal = 0
    inaccessible = 0
    for extension in activated:
        activation_host = extension._activation_host
        if activation_host is None:
            continue
        host = cast(_ActivationApi, activation_host)
        try:
            with host._guard:
                if host._finalize_provider_catalog_locked():
                    finalized += 1
                elif host._state == "published":
                    # A published host may be live even if its private marker was
                    # corrupted. Catalog harvesting never takes that risk.
                    refused_published += 1
                elif host._dispose_locked():
                    refused_disposed += 1
                else:
                    # Refusal can be an idempotent revisit of an existing
                    # catalog-finalized/disposed terminal host.
                    refused_already_terminal += 1
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - continue bounded sibling finalization
            inaccessible += 1
    return _ProviderCatalogFinalization(
        finalized=finalized,
        refused_disposed=refused_disposed,
        refused_published=refused_published,
        refused_already_terminal=refused_already_terminal,
        inaccessible=inaccessible,
    )


def _report_provider_catalog_finalization(
    result: _ProviderCatalogFinalization,
    diagnostic: Callable[[str], None],
) -> None:
    message = result.anomaly_diagnostic
    if message is not None:
        diagnostic(message)


def activate_extension_batch(
    descriptors: Sequence[ExtensionDescriptor],
    *,
    reserved_command_names: Sequence[str] = (),
    reserved_tool_names: Sequence[str] = (),
    message_outbox: list[QueuedUserMessage] | None = None,
    custom_message_outbox: list[QueuedCustomMessage] | None = None,
    message_routing: _message_routing.GenerationMessageRouting | None = None,
    preloaded: _contracts.ExtensionActivationBatch | None = None,
    pending: bool = False,
    diagnostic: Callable[[str], None] | None = None,
) -> _contracts.ExtensionActivationBatch:
    """Activate once, or finalize a pending pre-trust batch in final order."""

    if preloaded is not None and not preloaded.pending:
        raise ValueError("preloaded extension batch is already finalized")
    if preloaded is not None and pending:
        raise ValueError("a final merge cannot remain pending")

    reserved = frozenset(reserved_command_names)
    reserved_tools = frozenset(reserved_tool_names)
    taken = _TakenContributionState()
    outbox = (
        preloaded.message_outbox
        if preloaded is not None
        else (message_outbox if message_outbox is not None else [])
    )
    custom_outbox = (
        preloaded.custom_message_outbox
        if preloaded is not None
        else (custom_message_outbox if custom_message_outbox is not None else [])
    )
    retained_routing = preloaded.message_routing if preloaded is not None else None
    routing = _message_routing._routing_for_activation_batch(
        _contracts._activation_message_routings(
            preloaded.activated if preloaded is not None else ()
        ),
        outbox,
        custom_outbox,
        supplied=message_routing or retained_routing,
        required=retained_routing,
    )
    results: list[_contracts.ActivatedExtension] = []
    preloaded_by_key = (
        {
            item._activation_key: item
            for item in preloaded.activated
            if item._activation_key is not None
        }
        if preloaded is not None
        else {}
    )

    try:
        for descriptor in descriptors:
            if descriptor.status != "loadable":
                # Discovery already disabled this; never import it.
                results.append(_passthrough_disabled(descriptor))
                continue
            key = _descriptor_activation_key(descriptor)
            existing = preloaded_by_key.get(key)
            if existing is not None:
                reused, taken = _finalize_preloaded_extension(
                    existing,
                    descriptor=descriptor,
                    reserved=reserved,
                    reserved_tools=reserved_tools,
                    taken=taken,
                    diagnostic=diagnostic,
                )
                results.append(reused)
                continue
            # Pending pre-trust activation stages each extension independently.
            # Cross-extension collisions are provisional because the final
            # reserved set can disable an earlier extension and free its names
            # for a later one. Resolve those collisions only once, in final
            # descriptor order.
            activation_taken = _TakenContributionState() if pending else taken
            activated, successor = _activate_one(
                descriptor,
                reserved=reserved,
                reserved_tools=reserved_tools,
                taken=activation_taken,
                outbox=outbox,
                custom_outbox=custom_outbox,
                message_routing=routing,
                commit_activation=not pending,
                diagnostic=diagnostic,
            )
            results.append(activated)
            if not pending:
                taken = successor
    except BaseException:
        cleanup = _dispose_activation_results(results)
        if preloaded is not None:
            cleanup = cleanup.merge(_dispose_activation_results(preloaded.activated))
        _report_activation_cleanup(cleanup, diagnostic)
        raise
    if preloaded is not None:
        # A pending descriptor omitted from the final trusted set is abandoned.
        # Claimed tokens are already empty, so this cannot dispose a transferee.
        cleanup = _dispose_activation_results(preloaded.activated)
        _report_activation_cleanup(cleanup, diagnostic)
    return _contracts.ExtensionActivationBatch(
        activated=tuple(results),
        message_outbox=outbox,
        custom_message_outbox=custom_outbox,
        pending=pending,
        message_routing=routing,
    )


def _descriptor_activation_key(descriptor: ExtensionDescriptor) -> str:
    if descriptor.entry_path:
        try:
            return str(Path(descriptor.entry_path).expanduser().resolve())
        except OSError:
            return str(Path(descriptor.entry_path).expanduser().absolute())
    return f"{descriptor.source_kind}:{descriptor.path_label}"


def _activated_contribution_names(
    existing: _contracts.ActivatedExtension,
) -> _ContributionNames:
    return _ContributionNames(
        commands=tuple(command.name for command in existing.commands),
        tools=tuple(registered.tool.name for registered in existing.tools),
        providers=tuple(registered.provider.name for registered in existing.providers),
        shortcuts=tuple(shortcut.key for shortcut in existing.shortcuts),
        flags=tuple(registered.flag.name for registered in existing.flags),
        message_renderers=tuple(
            renderer.custom_type for renderer in existing.message_renderers
        ),
        entry_renderers=tuple(
            renderer.custom_type for renderer in existing.entry_renderers
        ),
    )


def _staged_contribution_names(staged: _FrozenActivation) -> _ContributionNames:
    return _ContributionNames(
        commands=tuple(command.name for command in staged.commands),
        tools=tuple(registered.tool.name for registered in staged.tools),
        providers=tuple(registered.provider.name for registered in staged.providers),
        shortcuts=tuple(shortcut.key for shortcut in staged.shortcuts),
        flags=tuple(registered.flag.name for registered in staged.flags),
        message_renderers=tuple(
            renderer.custom_type for renderer in staged.message_renderers
        ),
        entry_renderers=tuple(
            renderer.custom_type for renderer in staged.entry_renderers
        ),
    )


def _finalize_preloaded_extension(
    existing: _contracts.ActivatedExtension,
    *,
    descriptor: ExtensionDescriptor,
    reserved: frozenset[str],
    reserved_tools: frozenset[str],
    taken: _TakenContributionState,
    diagnostic: Callable[[str], None] | None,
) -> tuple[_contracts.ActivatedExtension, _TakenContributionState]:
    """Validate and commit one pending preload without running it again."""

    if existing.status != "activated":
        return existing, taken
    pending_activation = existing._pending_activation
    if pending_activation is None:
        return (
            _disabled(descriptor, REASON_ACTIVATION_ERROR, "invalid preload state"),
            taken,
        )
    api = cast(_PendingActivation, pending_activation).claim()
    if api is None:
        # Re-finalizing the same pending batch is a bounded disabled outcome.
        # The first final candidate owns the transferred host, so this path
        # deliberately has nothing to dispose.
        return (
            _disabled(descriptor, REASON_ACTIVATION_ERROR, "invalid preload state"),
            taken,
        )
    ownership_transferred = False
    try:
        names = _normalize_contribution_names(_activated_contribution_names(existing))
        collision = _preloaded_collision_reason(
            names,
            reserved=reserved,
            reserved_tools=reserved_tools,
            taken=taken,
        )
        if collision is not None:
            return _disabled(descriptor, collision, None), taken
        # Complete every potentially fallible hash/equality operation before
        # activation commit can flush the frozen messages.
        prepared_names = _prepare_contribution_names_commit(names, taken)
        # Build the immutable result before commit flushes staged messages, so
        # a malformed/corrupted finalization cannot leak candidate effects.
        finalized = replace(
            existing,
            _pending_activation=None,
            _activation_host=api,
        )
        api._accept_message_route()
        _ActivationApi._commit_activation(
            api,
            _lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN,
        )
        ownership_transferred = True
        return finalized, prepared_names
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as err:  # noqa: BLE001 - bound a corrupted host
        return (
            _disabled(
                descriptor,
                REASON_ACTIVATION_ERROR,
                _safe_diagnostic(err),
            ),
            taken,
        )
    finally:
        if not ownership_transferred:
            _dispose_activation_host_with_diagnostic(api, diagnostic)


@dataclass(frozen=True, slots=True)
class _ResolvedActivationEntry:
    activate: Callable[..., object]


@dataclass(frozen=True, slots=True)
class _FailedActivationEntry:
    disabled: _contracts.ActivatedExtension


_ActivationEntryResolution: TypeAlias = (
    _ResolvedActivationEntry | _FailedActivationEntry
)


def _resolve_activation_entry(
    descriptor: ExtensionDescriptor,
) -> _ActivationEntryResolution:
    try:
        module = _import_entry_module(descriptor)
    except _ActivationError as err:
        return _FailedActivationEntry(_disabled(descriptor, err.reason, err.diagnostic))

    # A module-level `__getattr__` can execute code, so resolution remains
    # inside the same fail-closed boundary as extension execution.
    try:
        activate: object = getattr(module, descriptor.entry_function, None)
        if activate is None or not callable(activate):
            return _FailedActivationEntry(
                _disabled(descriptor, REASON_NO_ACTIVATE, None)
            )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as err:  # noqa: BLE001 - bound a bad extension
        return _FailedActivationEntry(
            _disabled(
                descriptor,
                REASON_ACTIVATION_ERROR,
                _safe_diagnostic(err),
            )
        )
    return _ResolvedActivationEntry(activate)


def _execute_activation_entry(
    descriptor: ExtensionDescriptor,
    activate: Callable[..., object],
    api: _ActivationApi,
    diagnostic: Callable[[str], None] | None,
) -> _contracts.ActivatedExtension | None:
    try:
        result = activate(api)
        if inspect.isawaitable(result):
            _run_awaitable(
                result,
                abandon=lambda: _dispose_activation_host_with_diagnostic(
                    api, diagnostic
                ),
            )
    except _ActivationError as err:
        return _disabled(descriptor, err.reason, err.diagnostic)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as err:  # noqa: BLE001 - bound a bad extension
        return _disabled(descriptor, REASON_ACTIVATION_ERROR, _safe_diagnostic(err))

    return None


def _activate_one(
    descriptor: ExtensionDescriptor,
    *,
    reserved: frozenset[str],
    reserved_tools: frozenset[str],
    taken: _TakenContributionState,
    outbox: list[QueuedUserMessage],
    custom_outbox: list[QueuedCustomMessage],
    message_routing: _message_routing.GenerationMessageRouting,
    diagnostic: Callable[[str], None] | None,
    commit_activation: bool = True,
) -> tuple[_contracts.ActivatedExtension, _TakenContributionState]:
    resolution = _resolve_activation_entry(descriptor)
    if isinstance(resolution, _FailedActivationEntry):
        return resolution.disabled, taken

    api = _ActivationApi(
        descriptor.name,
        reserved=reserved,
        taken=frozenset(taken.commands),
        reserved_tools=reserved_tools,
        taken_tools=frozenset(taken.tools),
        taken_providers=frozenset(taken.providers),
        taken_shortcuts=frozenset(taken.shortcuts),
        taken_flags=frozenset(taken.flags),
        taken_message_renderers=frozenset(taken.message_renderers),
        taken_entry_renderers=frozenset(taken.entry_renderers),
        outbox=outbox,
        custom_outbox=custom_outbox,
        message_routing=message_routing,
    )
    ownership_transferred = False
    try:
        disabled = _execute_activation_entry(
            descriptor, resolution.activate, api, diagnostic
        )
        if disabled is not None:
            return disabled, taken

        staged = _ActivationApi._seal_and_freeze(
            api,
            _lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN,
        )
        if staged.failure is not None:
            failure_reason, failure_diagnostic = staged.failure
            return _disabled(descriptor, failure_reason, failure_diagnostic), taken
        names = _normalize_contribution_names(_staged_contribution_names(staged))
        collision = _preloaded_collision_reason(
            names,
            reserved=reserved,
            reserved_tools=reserved_tools,
            taken=taken,
        )
        if collision is not None:
            return _disabled(descriptor, collision, None), taken
        prepared_names = _prepare_contribution_names_commit(names, taken)
        # Construct the immutable result before commit can flush staged user
        # messages. Any bad host/result shape is then disabled without effects.
        activated = _contracts.ActivatedExtension(
            name=descriptor.name,
            version=descriptor.version,
            path_label=descriptor.path_label,
            status="activated",
            reason=None,
            commands=staged.commands,
            diagnostic=None,
            hooks=staged.hooks,
            tools=staged.tools,
            providers=staged.providers,
            unregistered_providers=staged.unregistered_providers,
            shortcuts=staged.shortcuts,
            flags=staged.flags,
            message_renderers=staged.message_renderers,
            entry_renderers=staged.entry_renderers,
            custom_messages=staged.custom_messages,
            _activation_key=_descriptor_activation_key(descriptor),
            _pending_activation=(
                None
                if commit_activation
                else cast(_contracts._PendingActivationHost, _PendingActivation(api))
            ),
            _activation_host=(api if commit_activation else None),
        )
        if commit_activation:
            api._accept_message_route()
            _ActivationApi._commit_activation(
                api,
                _lifecycle_token=_ACTIVATION_LIFECYCLE_TOKEN,
            )
        ownership_transferred = True
        return activated, prepared_names
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as err:  # noqa: BLE001 - bound a corrupted host
        return (
            _disabled(
                descriptor,
                REASON_ACTIVATION_ERROR,
                _safe_diagnostic(err),
            ),
            taken,
        )
    finally:
        if not ownership_transferred:
            _dispose_activation_host_with_diagnostic(api, diagnostic)


@dataclass(slots=True)
class _ExtensionCandidate:
    """Optional runtime holder before reload/startup ownership is transferred."""

    runtime: _contracts._ExtensionRuntime | None = None

    def adopt(
        self,
        runtime: _contracts._ExtensionRuntime,
        diagnostic: Callable[[str], None],
    ) -> None:
        """Take ownership immediately after the composition seam returns."""

        if self.runtime is None:
            self.runtime = runtime
            return
        _report_activation_cleanup(
            _dispose_activation_hosts(
                cast(tuple[_ActivationApi, ...], runtime.activation_hosts)
            ),
            diagnostic,
        )
        raise ExtensionCapabilityError("extension candidate already owns a runtime")

    def publish(self) -> bool:
        """Transfer the held runtime's hosts and clear the optional holder."""

        runtime = self.runtime
        if runtime is None or not _publish_activation_hosts_atomically(
            cast(tuple[_ActivationApi, ...], runtime.activation_hosts)
        ):
            return False
        self.runtime = None
        return True

    def dispose(self) -> _ActivationCleanup:
        """Dispose unpublished hosts, retaining only an inaccessible runtime."""

        runtime = self.runtime
        if runtime is None:
            return _ActivationCleanup()
        cleanup = _dispose_activation_hosts(
            cast(tuple[_ActivationApi, ...], runtime.activation_hosts)
        )
        if cleanup.failed == 0:
            self.runtime = None
        return cleanup


def _passthrough_disabled(
    descriptor: ExtensionDescriptor,
) -> _contracts.ActivatedExtension:
    return _contracts.ActivatedExtension(
        name=descriptor.name,
        version=descriptor.version,
        path_label=descriptor.path_label,
        status="disabled",
        reason=descriptor.reason,
        commands=(),
        diagnostic=None,
        _activation_key=_descriptor_activation_key(descriptor),
    )


def _disabled(
    descriptor: ExtensionDescriptor,
    reason: str,
    diagnostic: str | None,
) -> _contracts.ActivatedExtension:
    return _contracts.ActivatedExtension(
        name=descriptor.name,
        version=descriptor.version,
        path_label=descriptor.path_label,
        status="disabled",
        reason=reason,
        commands=(),
        diagnostic=diagnostic,
        _activation_key=_descriptor_activation_key(descriptor),
    )


def safe_activation_metadata(
    activated: Sequence[_contracts.ActivatedExtension],
) -> list[dict[str, object]]:
    """Project activation results to archive-safe metadata.

    Only safe labels are emitted: name, version, path label, status,
    reason code, and the registered command names. Command handlers,
    descriptions, source code, and diagnostics are excluded.
    """

    return [
        {
            "name": item.name,
            "version": item.version,
            "path_label": item.path_label,
            "status": item.status,
            "reason": item.reason,
            "commands": [command.name for command in item.commands],
            "message_renderers": [
                renderer.custom_type for renderer in item.message_renderers
            ],
            "entry_renderers": [
                renderer.custom_type for renderer in item.entry_renderers
            ],
        }
        for item in activated
    ]
