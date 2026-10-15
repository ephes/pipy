"""Product composition for the canonical agent tool-capability port."""

from __future__ import annotations

import threading
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pipy_harness.native.agent.messages import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.agent.tools import (
    ToolExecutionOutcome,
    ToolExecutor,
    ToolInterruptWaiter,
)
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolInfo,
    ToolPort,
    tool_info,
)
from pipy_harness.native.tools.base import NestedToolCalls
from pipy_harness.native.tools.loadout import (
    PrepareToolLoadout,
    ToolLoadout,
    ToolLoadoutChanges,
    checked_loadout_changes,
)
from pipy_harness.native.tools.name_patterns import (
    MCP_TOOL_NAME_PREFIX,
    create_tool_name_matcher,
    is_mcp_tool_name,
)

DEFAULT_TOOL_NAMES = ("read", "ls", "grep", "find", "write", "edit", "bash")


def declarable(registry: Mapping[str, ToolPort]) -> frozenset[str]:
    """Names whose activation declares them to the model (Pi ``_isDeclarable``)."""

    return frozenset(
        name
        for name, port in registry.items()
        if port.definition.exposure in ("direct", "model-only")
    )


def activated_on_registration(registry: Mapping[str, ToolPort]) -> frozenset[str]:
    """Names whose registration activates them (Pi ``_isActivatedOnRegistration``).

    Only ``direct`` and ``model-only`` tools are declared without being named:
    a ``codemode`` or ``deferred`` tool becomes active when something names it
    (``set_active_tools``, a restored transcript), and a ``hidden`` tool never
    is declared. A declarable tool registered with ``default_active=False``,
    such as ``tool_search``, also waits to be named.
    """

    return frozenset(
        name
        for name in declarable(registry)
        if registry[name].definition.default_active
    )


def apply_tool_modifiers(
    base: Sequence[str], entries: Sequence[str]
) -> tuple[str, ...]:
    """Apply exact Pi modifiers in order."""
    names = list(base)
    for entry in entries:
        name = entry[1:]
        if entry.startswith("+") and name and name not in names:
            names.append(name)
        elif entry.startswith("-") and name in names:
            names.remove(name)
    return tuple(names)


def resolve_default_tools(entries: Sequence[str]) -> tuple[str, ...]:
    plain = tuple(entry for entry in entries if not entry.startswith(("+", "-")))
    return apply_tool_modifiers(
        plain if plain or not entries else DEFAULT_TOOL_NAMES, entries
    )


@dataclass(frozen=True, slots=True)
class ToolFilterOptions:
    """Pi-style per-run tool visibility controls.

    ``allow`` and ``exclude`` entries are exact tool names or patterns in
    which ``*`` matches any characters; ``modifiers`` take exact names only.

    ``no_tools`` is Pi's empty allowlist (``--tools ''``, or ``--no-tools``
    without ``--tools``): no tool is registered. ``no_builtin_tools`` is Pi's
    ``noTools: "builtin"``: the default selection starts empty, so built-in
    tools stay registered but only those the ``modifiers`` add are active,
    and the ``defaultTools`` setting is not read. A plain allowlist replaces
    the default selection, so neither flag goes with ``allow``; the command
    line folds them in (``--no-tools --tools tool_search`` is an allowlist).
    """

    allow: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    modifiers: tuple[str, ...] = ()
    no_tools: bool = False
    no_builtin_tools: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.allow, tuple) or not all(
            isinstance(name, str) for name in self.allow
        ):
            raise TypeError("ToolFilterOptions.allow must be a tuple of strings")
        if not isinstance(self.exclude, tuple) or not all(
            isinstance(name, str) for name in self.exclude
        ):
            raise TypeError("ToolFilterOptions.exclude must be a tuple of strings")
        if not isinstance(self.modifiers, tuple) or not all(
            isinstance(entry, str) and entry.startswith(("+", "-")) and "*" not in entry
            for entry in self.modifiers
        ):
            raise TypeError(
                "ToolFilterOptions.modifiers must be exact +name/-name entries"
            )
        if self.modifiers and self.allow:
            raise ValueError("tool names cannot be mixed with +name or -name entries")
        if not isinstance(self.no_tools, bool):
            raise TypeError("ToolFilterOptions.no_tools must be a bool")
        if not isinstance(self.no_builtin_tools, bool):
            raise TypeError("ToolFilterOptions.no_builtin_tools must be a bool")
        if self.no_tools and (self.allow or self.modifiers or self.no_builtin_tools):
            raise ValueError("no_tools is the empty allowlist and takes no tool names")
        if self.no_builtin_tools and self.allow:
            raise ValueError(
                "an allowlist replaces the default selection no_builtin_tools empties"
            )

    @classmethod
    def empty(cls) -> ToolFilterOptions:
        return cls()

    def allows(self, name: str) -> bool:
        """Whether an allowlist entry names or matches the tool."""

        return create_tool_name_matcher(self.allow)(name)

    def excludes(self, name: str) -> bool:
        """Whether an exclusion entry names or matches the tool."""

        return create_tool_name_matcher(self.exclude)(name)

    @property
    def allowlist_filters_mcp(self) -> bool:
        """Whether the allowlist decides which MCP tools stay registered.

        Pi ``_allowlistFiltersMcp``: an allowlist of plain names selects the
        tools declared to the model and leaves MCP tools registered. Once an
        entry starts with ``mcp__`` it filters MCP tools like other tools.
        (Pi's empty allowlist is ``no_tools`` here, which filters MCP tools
        as well.)
        """

        return any(entry.startswith(MCP_TOOL_NAME_PREFIX) for entry in self.allow)

    def provider_visible_names(
        self,
        *,
        registered_names: Collection[str],
    ) -> frozenset[str]:
        """Return the names the filters keep registered (Pi ``_isAllowedTool``).

        An MCP tool an allowlist keeps without naming or matching it is
        registered for scripts and ``tool_search`` only; whether a kept tool
        may be declared is decided with the capability value.
        """

        if self.no_tools:
            return frozenset()
        names = set(registered_names)
        if self.allow:
            keeps_mcp = not self.allowlist_filters_mcp
            names = {
                name
                for name in names
                if self.allows(name) or (keeps_mcp and is_mcp_tool_name(name))
            }
        if self.exclude:
            names = {name for name in names if not self.excludes(name)}
        return frozenset(names)


@dataclass(frozen=True, slots=True)
class ToolCapabilityState:
    """One complete, immutable tool-capability generation.

    Registries, the merged view, the executor bound to that view, and the
    provider-visible selection are built together and never mutated afterwards.
    A reload prepares a whole replacement value and publishes it with a single
    assignment, so no reader can observe a registry that has been rebuilt while
    its executor or visibility selection still belongs to the previous
    generation.

    ``registry`` is the built-in tools, then the extension tools, then the
    MCP tools whose names neither of them uses. The MCP tools change while a
    session runs (a server connects or announces another list); such a value
    keeps the ``extension_generation`` of the value it was made from.
    """

    builtin_registry: Mapping[str, ToolPort]
    extension_registry: Mapping[str, ToolPort]
    registry: Mapping[str, ToolPort]
    executor: ToolExecutor
    filter_options: ToolFilterOptions
    active_tool_names: frozenset[str] | None
    default_tool_names: frozenset[str] | None = None
    selected_names: tuple[str, ...] = ()
    recognized_names: frozenset[str] = frozenset()
    selection_initialized: bool = False
    active_selection_customized: bool = False
    # How the builtin ``codemode`` presents the tools while it is declared
    # (Pi ``prepareLoadout``): part of the value, so one value always gives
    # the same declarations. ``None`` leaves every description as registered.
    # An extension tool's presenter is its definition's ``prepare_loadout``.
    codemode_loadout: PrepareToolLoadout | None = None
    # The tools of the connected MCP servers, by pipy tool name.
    mcp_registry: Mapping[str, ToolPort] = field(default_factory=dict)
    # Identity of the extension generation this value was built for; a new
    # one unless given. A value that differs only in its MCP tools or its
    # selection keeps it (``dataclasses.replace`` passes it on).
    extension_generation: object | None = None
    # MCP tool names that were active before their servers were started
    # again (``/reload``) or that a restored transcript declares, and that
    # are not registered now (Pi ``_pendingToolNames``). A name becomes
    # active when its tool registers and is dropped when the next run starts.
    pending_tool_names: frozenset[str] = frozenset()
    # The optional built-in tools (``codemode``, ``tool_search``) this value
    # may register: the session offers them and their built-in extensions
    # (``builtin:codemode``, ``builtin:tool-search``) are loaded. Decided
    # when the value is prepared, so a reload reads the settings again.
    offered_builtins: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        # Enforce immutability on the type, not just in `build`. The copy is
        # unconditional: a `MappingProxyType` is only a read-only *view*, so
        # wrapping a caller's dict without copying would still let whoever
        # retains that dict edit a published registry outside the lock and
        # desynchronize it from the executor built over it.
        for field_name in (
            "builtin_registry",
            "extension_registry",
            "mcp_registry",
            "registry",
        ):
            object.__setattr__(
                self, field_name, MappingProxyType(dict(getattr(self, field_name)))
            )
        if self.extension_generation is None:
            object.__setattr__(self, "extension_generation", object())

    @property
    def filter_configured(self) -> bool:
        """Whether ``--tools``, ``--exclude-tools`` or ``--no-tools`` decide
        which tools stay registered. Modifiers and ``no_builtin_tools`` only
        change the default selection."""

        return (
            replace(self.filter_options, modifiers=(), no_builtin_tools=False)
            != ToolFilterOptions.empty()
        )

    @classmethod
    def build(
        cls,
        builtin_registry: Mapping[str, ToolPort],
        extension_registry: Mapping[str, ToolPort],
        *,
        filter_options: ToolFilterOptions,
        cancel_join_timeout_seconds: float,
        carried_active_tool_names: frozenset[str] | None = None,
        codemode_loadout: PrepareToolLoadout | None = None,
        mcp_registry: Mapping[str, ToolPort] | None = None,
    ) -> "ToolCapabilityState":
        """Build a complete capability value without touching any live state.

        A configured `--allow`/`--exclude` filter re-derives the visible set
        from the new registry. Without one, an extension's `set_active_tools`
        selection is carried across unchanged, which is the established
        behavior.
        """

        builtin = dict(builtin_registry)
        extensions = dict(extension_registry)
        mcp = dict(mcp_registry or {})
        registry = _merged_registry(builtin, extensions, mcp)
        active = carried_active_tool_names
        if filter_options != ToolFilterOptions.empty():
            # Pi ``_refreshToolRegistry``: a filter keeps a tool registered,
            # and only a declarable tool is activated by being kept. An
            # allowlist activates the tools it names or matches, also one
            # that is inactive by default; an MCP tool it keeps without
            # naming it stays inactive.
            kept = filter_options.provider_visible_names(registered_names=registry)
            if filter_options.allow:
                active = frozenset(
                    name
                    for name in kept & declarable(registry)
                    if filter_options.allows(name)
                )
            else:
                active = kept & activated_on_registration(registry)
                if filter_options.no_builtin_tools:
                    # Pi ``noTools: "builtin"``: the built-in tools stay
                    # registered, and only those the modifiers add are active.
                    added = apply_tool_modifiers((), filter_options.modifiers)
                    active = (active - builtin.keys()) | (kept & frozenset(added))
        executor = ToolExecutor(
            registry,
            cancel_join_timeout_seconds=cancel_join_timeout_seconds,
        )
        # `__post_init__` copies and freezes the mappings, so this hands over
        # plain dicts and lets the type enforce its own invariant in one place.
        return cls(
            builtin_registry=builtin,
            extension_registry=extensions,
            registry=registry,
            executor=executor,
            filter_options=filter_options,
            active_tool_names=active,
            codemode_loadout=codemode_loadout,
            mcp_registry=mcp,
        )

    def with_mcp_registry(
        self,
        mcp_registry: Mapping[str, ToolPort],
        *,
        cancel_join_timeout_seconds: float,
    ) -> "ToolCapabilityState":
        """This value with other MCP tools, as a new complete value.

        The selection is kept and extended as registering the tools would
        extend it (Pi ``_refreshToolRegistry``): with an allowlist, every
        declarable MCP tool it names or matches is active; otherwise a tool
        becomes active when registration activates it and it was not
        registered that way before. A selection of ``None`` already means
        "as registration left it". An MCP tool that is gone leaves the
        selection (Pi re-registers it hidden, and applying the loadout drops
        it): when its server offers it again it is a new tool, so a
        ``direct`` one is activated by registering and one ``tool_search``
        had loaded has to be loaded again. A pending name whose tool is
        offered now becomes active.
        """

        mcp = dict(mcp_registry)
        registry = _merged_registry(self.builtin_registry, self.extension_registry, mcp)
        active = self.active_tool_names
        pending = self.pending_tool_names
        if active is not None:
            # A name a built-in or extension tool has is not an MCP tool.
            offered = frozenset(_accepted_mcp_tools(registry, mcp))
            if self.filter_options.allow:
                activated = frozenset(
                    name
                    for name in offered & declarable(registry)
                    if self.filter_options.allows(name)
                )
            else:
                activated = (
                    offered & activated_on_registration(registry)
                ) - activated_on_registration(self.registry)
            arrived = pending & offered
            withdrawn = frozenset(
                _accepted_mcp_tools(self.registry, self.mcp_registry)
            ).difference(registry)
            active = (active - withdrawn) | activated | arrived
            pending = pending - arrived
        return replace(
            self,
            mcp_registry=mcp,
            registry=registry,
            executor=ToolExecutor(
                registry,
                cancel_join_timeout_seconds=cancel_join_timeout_seconds,
            ),
            active_tool_names=active,
            pending_tool_names=pending,
        )

    def with_extension_registry(
        self,
        extension_registry: Mapping[str, ToolPort],
        *,
        cancel_join_timeout_seconds: float,
    ) -> "ToolCapabilityState":
        """This value with other extension tools, as a new complete value of
        the same extension generation (Pi ``_refreshToolRegistry`` after an
        extension registered a tool).

        With an allowlist every declarable tool it names or matches is
        active; otherwise a tool becomes active when registration activates
        it and did not before, so a tool that changed from ``hidden`` to
        ``direct`` is activated like a new one. A selection of ``None``
        already means "as registration left it". A tool registered again as
        ``hidden`` keeps its name in the selection and is never declared.
        """

        extensions = dict(extension_registry)
        registry = _merged_registry(
            self.builtin_registry, extensions, self.mcp_registry
        )
        active = self.active_tool_names
        if active is not None:
            if self.filter_options.allow:
                activated = frozenset(
                    name
                    for name in frozenset(extensions) & declarable(registry)
                    if self.filter_options.allows(name)
                )
            else:
                activated = activated_on_registration(
                    extensions
                ) - activated_on_registration(self.extension_registry)
            active = active | activated
        return replace(
            self,
            extension_registry=extensions,
            registry=registry,
            executor=ToolExecutor(
                registry,
                cancel_join_timeout_seconds=cancel_join_timeout_seconds,
            ),
            active_tool_names=active,
        )


def _filters_keep_codemode(options: ToolFilterOptions) -> bool:
    """Whether the filters would keep a built-in ``codemode`` registered."""

    return not (
        options.no_tools
        or options.excludes("codemode")
        or (bool(options.allow) and not options.allows("codemode"))
    )


def _merged_registry(
    builtin: Mapping[str, ToolPort],
    extensions: Mapping[str, ToolPort],
    mcp: Mapping[str, ToolPort],
) -> dict[str, ToolPort]:
    """Built-in tools, extension tools over them, then the other MCP tools.

    An MCP tool never replaces a built-in or extension tool of its name.
    """

    registry = dict(builtin)
    registry.update(extensions)
    for name, port in mcp.items():
        registry.setdefault(name, port)
    return registry


def _accepted_mcp_tools(
    registry: Mapping[str, ToolPort], mcp: Mapping[str, ToolPort]
) -> dict[str, ToolPort]:
    """The MCP tools that are in the merged registry under their names."""

    return {name: port for name, port in mcp.items() if registry.get(name) is port}


@dataclass(frozen=True, slots=True)
class NativeToolCapabilitySnapshot:
    """One provider turn's advertised and executable tool generation."""

    owner: NativeToolCapabilities
    state: ToolCapabilityState
    # This snapshot's loadout changes by declared names, so one provider
    # turn's declarations and hidden set agree also when the live value
    # changed in between.
    _loadouts: dict[tuple[str, ...], ToolLoadoutChanges | None] = field(
        default_factory=dict, compare=False, repr=False
    )

    def definitions(
        self,
        allowed_names: Sequence[str] | None = None,
        /,
    ) -> tuple[ToolDefinition, ...]:
        """The declared tools, as the model is to see them.

        The loadout of each declared tool that presents other tools (the
        builtin ``codemode``, an extension tool with ``prepare_loadout``)
        replaces descriptions. Declarations they hide stay in this tuple,
        because the transcript records them as declared; a request leaves
        out :meth:`hidden_declarations`.
        """

        declared = _definitions_for(self.state, allowed_names)
        changes = self._loadout_changes(declared)
        if changes is None:
            return declared
        return tuple(
            _redescribed(definition, changes.descriptions[definition.name])
            if definition.name in changes.descriptions
            else definition
            for definition in declared
        )

    def mcp_tool_labels(self) -> dict[str, str]:
        """The transcript title of each registered MCP server tool, by name.

        A port says it with ``label`` (``McpToolPort``: ``<server>/<tool>``);
        the resource tools have none.
        """

        labels: dict[str, str] = {}
        accepted = _accepted_mcp_tools(self.state.registry, self.state.mcp_registry)
        for name, port in accepted.items():
            label = getattr(port, "label", None)
            if isinstance(label, str):
                labels[name] = label
        return labels

    def hidden_declarations(self) -> frozenset[str]:
        """Declared tools whose declarations requests leave out (Pi
        ``_hiddenDeclarations``). They stay active and callable from scripts."""

        changes = self._loadout_changes(_definitions_for(self.state, None))
        return frozenset() if changes is None else changes.hidden_declarations

    def _loadout_changes(
        self, declared: tuple[ToolDefinition, ...]
    ) -> ToolLoadoutChanges | None:
        """The changes of every declared tool that presents the loadout,
        merged in the order the tools are declared (Pi ``_applyToolLoadout``):
        a later tool's description of a name replaces an earlier one's, and
        the hidden declarations add up. A tool whose function fails, or
        returns something that is no ``ToolLoadoutChanges``, changes nothing
        and is reported once.

        The functions run once per capability value and declared set, and
        the owner keeps their result (Pi keeps it until the loadout is
        applied again): the descriptions, the hidden declarations and the
        system prompt's tool list of one value come from one call."""

        key = tuple(definition.name for definition in declared)
        if key not in self._loadouts:
            changes = self.owner.kept_loadout_changes(
                self.state, declared, lambda: self._compute_loadout_changes(declared)
            )
            self._loadouts.setdefault(key, changes)
        return self._loadouts[key]

    def _compute_loadout_changes(
        self, declared: tuple[ToolDefinition, ...]
    ) -> ToolLoadoutChanges | None:
        state = self.state
        builtin_codemode = state.codemode_loadout
        if builtin_codemode is not None and not _is_builtin_codemode(state):
            builtin_codemode = None
        presenters: list[tuple[str, Callable[[ToolLoadout], object]]] = []
        for definition in declared:
            if definition.name == "codemode" and builtin_codemode is not None:
                presenters.append((definition.name, builtin_codemode))
            elif definition.prepare_loadout is not None:
                presenters.append((definition.name, definition.prepare_loadout))
        if not presenters:
            return None
        kept = _kept_names(state)
        loadout = ToolLoadout(
            declared,
            self.callable_tools(),
            tuple(
                port.definition for name, port in state.registry.items() if name in kept
            ),
        )
        descriptions: dict[str, str] = {}
        hidden: set[str] = set()
        for name, prepare in presenters:
            try:
                changes = checked_loadout_changes(prepare(loadout))
            except Exception as error:  # noqa: BLE001 - an extension's function; the other tools' changes still apply
                self.owner.report_loadout_error(name, error)
                continue
            if changes is not None:
                descriptions.update(changes.descriptions)
                hidden.update(changes.hidden_declarations)
        return ToolLoadoutChanges(descriptions, frozenset(hidden))

    def callable_names(self) -> frozenset[str]:
        """Names a composite tool's nested calls may reach in this value."""
        return _callable_names(self.state)

    def callable_tools(self) -> tuple[ToolDefinition, ...]:
        """The definitions of the callable names, in registration order."""
        names = _callable_names(self.state)
        return tuple(
            port.definition
            for name, port in self.state.registry.items()
            if name in names
        )

    def composite_enabled(self) -> bool:
        """Only a visible builtin identity may activate internal dispatch."""
        return _is_builtin_codemode(self.state) and any(
            definition.name == "codemode"
            for definition in _definitions_for(self.state, None)
        )

    def execute(
        self,
        call: AgentToolCall,
        *,
        output_sink: Callable[[str], None] | None = None,
        wait_for_interrupt: ToolInterruptWaiter | None = None,
        extension_generation_id: int | None = None,
        nested_tools: NestedToolCalls | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> ToolExecutionOutcome:
        return self.state.executor.execute(
            call,
            replace(
                self.owner._context,
                output_sink=output_sink,
                extension_generation_id=extension_generation_id,
                nested_tools=nested_tools,
            ),
            wait_for_interrupt=wait_for_interrupt,
            arguments=arguments,
        )

    def validated_arguments(
        self, call: AgentToolCall, /
    ) -> dict[str, Any] | ToolExecutionOutcome:
        return self.state.executor.validated_arguments(call)

    def is_extension_tool(self, name: str) -> bool:
        """Whether the registered tool of this name is an extension's."""
        port = self.state.extension_registry.get(name)
        return port is not None and self.state.registry.get(name) is port

    def error_result(
        self,
        call: AgentToolCall,
        output_text: str,
        /,
    ) -> AgentToolResultMessage:
        return self.state.executor.error_result(call, output_text)


class NativeToolCapabilities:
    """Compose product tool registries, visibility policy, and execution.

    The instance identity is caller-owned and stable for the whole run; every
    published registry and selection live inside one :class:`ToolCapabilityState`
    value replaced wholesale rather than edited in place. The optional runtime
    probe result is a separate run-owned cache guarded by the same state lock.

    The live state pointer is guarded state: an extension tool handler running
    on a worker thread can reach ``set_active_tools`` while the session thread
    publishes a reloaded generation. Every read and write of the pointer
    therefore takes ``state_lock``, and validation and assignment happen inside
    one critical section so neither writer can resurrect the other's superseded
    value. ``state_lock`` is injectable precisely so the session can pass its
    single mutex once that exists; the default is only for callers that own no
    session.
    """

    def __init__(
        self,
        builtin_registry: Mapping[str, ToolPort],
        extension_registry: Mapping[str, ToolPort],
        *,
        workspace_root: Path,
        filter_options: ToolFilterOptions,
        cancel_join_timeout_seconds: float,
        state_lock: "threading.RLock | None" = None,
        default_tools: Callable[[], Sequence[str] | None] | None = None,
        diagnostic: Callable[[str], None] | None = None,
        codemode_loadout: Callable[[], PrepareToolLoadout] | None = None,
        optional_builtins: Collection[str] | None = None,
        builtin_extension_enabled: Callable[[str], bool] | None = None,
    ) -> None:
        """``optional_builtins`` names the optional built-in tools
        (``codemode``, ``tool_search``) this session may register, whatever
        its registry is. Without it a session of the production registry may
        register both and another session neither.

        ``builtin_extension_enabled`` says whether a built-in extension
        (``codemode``, ``tool-search``) is loaded now, as the ``extensions``
        settings and the resource flags say (Pi loads both tools as built-in
        extensions). It is asked whenever a capability value is prepared;
        without it both are loaded."""

        self._context = ToolContext(workspace_root=workspace_root)
        self._cancel_join_timeout_seconds = cancel_join_timeout_seconds
        self._state_lock = state_lock if state_lock is not None else threading.RLock()
        from pipy_harness.native.tools.registry import ProductionToolRegistry

        self._production_defaults = isinstance(builtin_registry, ProductionToolRegistry)
        self._offers_codemode = (
            self._production_defaults
            if optional_builtins is None
            else "codemode" in optional_builtins
        )
        self._offers_tool_search = (
            self._production_defaults
            if optional_builtins is None
            else "tool_search" in optional_builtins
        )
        self._builtin_extension_enabled = builtin_extension_enabled
        self._base_registry = dict(builtin_registry)
        self._default_tools = default_tools
        self._diagnostic = diagnostic
        # Reads the settings: called when a capability value is built, at the
        # start of a session and on reload, like ``default_tools``.
        self._codemode_loadout = codemode_loadout
        # Run-owned result; all accesses take _state_lock.
        self._codemode_available: bool | None = None
        # Detached preparations share a probe without waiting under the session lock.
        self._codemode_probe_lock = threading.Lock()
        # The loadout failures that were reported; guarded by _state_lock.
        self._reported_loadout_errors: set[str] = set()
        # The loadout changes of the latest capability value, by declared
        # names; guarded by _state_lock. A new value replaces them.
        self._loadout_value: ToolCapabilityState | None = None
        self._loadout_kept: dict[tuple[str, ...], ToolLoadoutChanges | None] = {}
        self._state = replace(
            ToolCapabilityState.build(
                builtin_registry,
                extension_registry,
                filter_options=filter_options,
                cancel_join_timeout_seconds=cancel_join_timeout_seconds,
                codemode_loadout=codemode_loadout() if codemode_loadout else None,
            ),
            offered_builtins=self._offered_builtins(),
        )

    def _offered_builtins(self) -> frozenset[str]:
        """The optional built-in tools a value prepared now may register."""

        enabled = self._builtin_extension_enabled or (lambda _name: True)
        names: set[str] = set()
        if self._offers_codemode and enabled("codemode"):
            names.add("codemode")
        if self._offers_tool_search and enabled("tool-search"):
            names.add("tool_search")
        return frozenset(names)

    @property
    def state(self) -> ToolCapabilityState:
        """The live capability value. Read once per operation."""

        with self._state_lock:
            return self._state

    @property
    def builtin_names(self) -> tuple[str, ...]:
        return tuple(self.state.builtin_registry)

    @property
    def registered_names(self) -> tuple[str, ...]:
        return tuple(self.state.registry)

    @property
    def unknown_filter_names(self) -> tuple[str, ...]:
        state = self.state
        configured_names = set(state.filter_options.allow) | set(
            state.filter_options.exclude
        )
        configured_names.update(state.selected_names)
        configured_names.update(
            entry[1:] for entry in state.filter_options.modifiers if entry[1:]
        )
        # A pattern names no tool, and an MCP tool is registered when its
        # server connects, which may be after the names are checked.
        return tuple(
            sorted(
                name
                for name in configured_names.difference(state.registry).difference(
                    state.recognized_names
                )
                if "*" not in name and not is_mcp_tool_name(name)
            )
        )

    def all_tools(self) -> tuple[ToolInfo, ...]:
        """Pi ``getAllTools``: every registered tool of the live value.

        Hidden and inactive tools are included; the entries report what is
        registered, not what the next request declares. A tool that
        ``--tools``, ``--exclude-tools`` or ``--no-tools`` removed is not
        registered in Pi and is left out.
        """

        state = self.state
        kept = _kept_names(state)
        return tuple(
            tool_info(port.definition)
            for name, port in state.registry.items()
            if name in kept
        )

    def kept_loadout_changes(
        self,
        state: ToolCapabilityState,
        declared: tuple[ToolDefinition, ...],
        compute: Callable[[], ToolLoadoutChanges | None],
        /,
    ) -> ToolLoadoutChanges | None:
        """The loadout changes of ``state`` for ``declared``, computed once.

        ``compute`` runs the tools' ``prepare_loadout`` functions, which are
        extension code, so it runs without the state lock. Should two threads
        read the declarations of a new value at the same time, both compute
        and the first result is kept and returned to both. Only the live
        value's changes are kept here; a snapshot keeps its own.
        """

        key = tuple(definition.name for definition in declared)
        with self._state_lock:
            if self._loadout_value is state and key in self._loadout_kept:
                return self._loadout_kept[key]
        changes = compute()
        with self._state_lock:
            if self._loadout_value is not state:
                if state is not self._state:
                    # A value that is no longer live (a provider turn's
                    # pinned one) does not displace the live value's changes.
                    return changes
                self._loadout_value = state
                self._loadout_kept = {}
            return self._loadout_kept.setdefault(key, changes)

    def report_loadout_error(self, tool_name: str, error: BaseException) -> None:
        """Tell the user once that a tool's ``prepare_loadout`` failed.

        The function runs again for every new capability value, so the same
        failure can come again; it is shown the first time.
        Only the type of the error is shown: its text is the extension's and
        may hold paths or secrets, and reading it runs the extension's code.
        """

        kind = "".join(c if c.isprintable() else " " for c in type(error).__name__)
        message = f'pipy: prepare_loadout of tool "{tool_name}" failed: {kind[:80]}'
        with self._state_lock:
            if message in self._reported_loadout_errors:
                return
            self._reported_loadout_errors.add(message)
        if self._diagnostic is not None:
            self._diagnostic(message)

    def set_active_tools(self, names: Sequence[str]) -> bool:
        """Atomically replace the provider-visible tool-name selection.

        Validation and assignment share one critical section, so a concurrent
        publication can neither be undone by a selection derived from the
        superseded registry nor slip a name past the registry check.

        A selection that deactivates a tool replaces a restored one, whose
        pending names are dropped; one that only adds tools keeps them (Pi
        ``setActiveToolsByName``).
        """

        normalized = frozenset(str(name) for name in names if str(name))
        with self._state_lock:
            state = self._state
            if any(name not in state.registry for name in normalized):
                return False
            # What counts is what was declared: a name the filters hide was
            # not active for the model, so dropping it deactivates nothing.
            deactivates = any(
                name in state.registry and name not in normalized
                for name in _declared_names(state)
            )
            self._state = replace(
                state,
                active_tool_names=normalized,
                selection_initialized=True,
                active_selection_customized=True,
                pending_tool_names=frozenset()
                if deactivates
                else state.pending_tool_names,
            )
        return True

    def loadable_tools(self) -> tuple[ToolDefinition, ...]:
        """The tools ``tool_search`` may load, in registration order."""

        return _loadable_tools(self.state)

    def load_tools(self, names: Sequence[str], /) -> tuple[str, ...]:
        """Activate the named loadable tools; return the names it activated.

        The names are added to the selection that is live now, in the same
        critical section that checks them, so a selection or a publication
        made since the caller ranked the tools is kept. A name that is no
        longer loadable is left out. Like any change of the active tools,
        the next request's system message records it. Loading only adds, so
        it does not count as a selection an extension made.
        """

        with self._state_lock:
            state = self._state
            loadable = {definition.name for definition in _loadable_tools(state)}
            loaded = tuple(dict.fromkeys(name for name in names if name in loadable))
            if loaded:
                self._state = replace(
                    state,
                    active_tool_names=_active_names(state) | frozenset(loaded),
                    selection_initialized=True,
                )
        return loaded

    def declared_tool_names(self) -> frozenset[str]:
        """The names the next request may declare: the active tools the
        filters let be declared (Pi ``getActiveTools``)."""

        return _declared_names(self.state)

    def activate_tools(self, names: Sequence[str], /) -> None:
        """Add registered tools to the active selection (Pi
        ``setActiveTools([...active, ...names])``).

        Like :meth:`load_tools` the names are added to the selection that is
        live now, and adding does not count as a selection an extension made.
        Unlike it, any registered tool can be named. A selection that already
        has the names, also the unset one that means "as registration left
        it", is not replaced.
        """

        with self._state_lock:
            state = self._state
            current = _active_names(state)
            added = frozenset(name for name in names if name in state.registry)
            if not added <= current:
                self._state = replace(
                    state,
                    active_tool_names=current | added,
                    selection_initialized=True,
                )

    def deactivate_indirect_mcp_tools(self, names: Collection[str], /) -> None:
        """Take the named MCP tools out of the active selection unless they
        are ``direct`` (Pi ``setExposure``: tools that are no longer exposed
        directly leave the declared set).

        Only a name that an MCP tool has in the merged registry counts: a
        built-in or extension tool that has the name an MCP tool would get
        keeps its place. A name that is not registered stays, as in Pi.
        Without a selection nothing changes: only tools that registration
        activates are active then, and those are not indirect. Like
        :meth:`activate_tools` this does not count as a selection an
        extension made. Taking a tool out drops the pending names, as any
        selection that deactivates a tool does.
        """

        with self._state_lock:
            state = self._state
            active = state.active_tool_names
            if active is None:
                return
            accepted = _accepted_mcp_tools(state.registry, state.mcp_registry)
            indirect = frozenset(
                name
                for name in names
                if name in accepted and accepted[name].definition.exposure != "direct"
            )
            if active & indirect:
                self._state = replace(
                    state,
                    active_tool_names=active - indirect,
                    pending_tool_names=frozenset(),
                )

    def codemode_selectable(self) -> bool:
        """Whether ``codemode`` is registered, or the built-in could be.

        The built-in can be registered when the tool filters keep it. This
        does not look at the runtime, so it costs nothing.
        """

        state = self.state
        if "codemode" in state.registry:
            return "codemode" in _kept_names(state)
        return "codemode" in state.offered_builtins and _filters_keep_codemode(
            state.filter_options
        )

    def register_codemode(self) -> bool:
        """Register the optional built-in ``codemode`` for this run.

        Something other than the tool selection wants it (an MCP server whose
        tools scripts call). Returns whether a ``codemode`` tool is registered
        afterwards. Nothing is registered when the filters remove it or its
        runtime is unavailable; the probe reports that once, as when the
        selection names the tool. An extension's ``codemode`` is left alone
        and never probed. The new value keeps the selection, the other tools
        and the extension generation.
        """

        state = self.state
        if "codemode" in state.registry:
            return True
        if not (
            "codemode" in state.offered_builtins
            and _filters_keep_codemode(state.filter_options)
        ):
            return False
        if not self._probe_codemode():
            return False
        from pipy_harness.native.tools.codemode import CodemodeTool

        with self._state_lock:
            state = self._state
            if "codemode" in state.registry:
                return True
            # A reload published during the probe may have turned the
            # built-in extension off: decide on the live value.
            if not (
                "codemode" in state.offered_builtins
                and _filters_keep_codemode(state.filter_options)
            ):
                return False
            builtin = {**state.builtin_registry, "codemode": CodemodeTool()}
            registry = _merged_registry(
                builtin, state.extension_registry, state.mcp_registry
            )
            self._state = replace(
                state,
                builtin_registry=builtin,
                registry=registry,
                executor=ToolExecutor(
                    registry,
                    cancel_join_timeout_seconds=self._cancel_join_timeout_seconds,
                ),
            )
        return True

    def restore_active_tools(self, names: Sequence[str]) -> None:
        """Pi ``setActiveToolsByName`` from a transcript's declared tools.

        Unlike :meth:`set_active_tools`, unknown names are dropped rather
        than refused: Pi keeps only registered tools, and its registry
        already excludes names a ``--allow``/``--exclude`` filter hides, so a
        configured filter narrows the restored set too. Filtering and
        assignment share one critical section.

        An MCP tool the transcript declares may not be registered yet,
        because its server is still connecting. Such a name is pending: it
        becomes active when the tool registers before the next run starts
        (Pi ``_restoreToolsFromTranscript``).
        """

        with self._state_lock:
            state = self._state
            visible = _kept_names(state)
            restored = frozenset(name for name in names if name in visible)
            self._state = replace(
                state,
                active_tool_names=restored,
                selection_initialized=True,
                active_selection_customized=True,
                pending_tool_names=_unregistered_mcp_names(state, names),
            )

    def restart_mcp_tools(self, *, keep_declared: bool = True) -> None:
        """Unregister the MCP tools: their servers are started again.

        With ``keep_declared`` (``/reload``) the MCP tools that are declared
        now are pending afterwards, with the names that were pending already
        (the reload that restarts the servers kept what was declared before
        it, see :meth:`publish`). A pending name is active again when its
        tool registers before the next run starts (Pi ``reload``). Without it
        (another session replaced this one) nothing is pending: a new Pi
        session starts from the configured tools, so a tool that
        ``tool_search`` loaded has to be loaded again. Every name of an MCP
        tool leaves the selection, so a tool that registers later is treated
        as a new one. One critical section: no publication of a server can
        fall between reading what is declared and unregistering the tools.
        """

        with self._state_lock:
            declared = _declared_names(self._state)
            state = self._state.with_mcp_registry(
                {}, cancel_join_timeout_seconds=self._cancel_join_timeout_seconds
            )
            if not keep_declared:
                state = replace(state, pending_tool_names=frozenset())
                declared = frozenset()
            active = state.active_tool_names
            if active is not None:
                gone = frozenset(
                    name
                    for name in active
                    if is_mcp_tool_name(name) and name not in state.registry
                )
                state = replace(
                    state,
                    active_tool_names=active - gone,
                    pending_tool_names=state.pending_tool_names
                    | _unregistered_mcp_names(state, declared),
                )
            self._state = state

    def drop_pending_tools(self) -> None:
        """A run starts: restored tools that did not register by now stay
        inactive, so a tool that never registers does not stay pending (Pi
        ``_startRun``)."""

        with self._state_lock:
            if self._state.pending_tool_names:
                self._state = replace(self._state, pending_tool_names=frozenset())

    def _probe_codemode(self) -> bool:
        """Cache one probe; present its warning outside both coordination locks."""
        warning = None
        with self._codemode_probe_lock:
            with self._state_lock:
                cached = self._codemode_available
            if cached is not None:
                return cached
            from pipy_harness.native.codemode.selftest import availability

            result = availability()
            with self._state_lock:
                # Publishing the cache also claims the sole warning: later
                # callers return the cached result without presenting it.
                self._codemode_available = result.available
            if not result.available and self._diagnostic is not None:
                reason = " ".join((result.reason or "self-test failed").split())[:300]
                reason = "".join(
                    character if character.isprintable() else " "
                    for character in reason
                )
                warning = f"pipy: codemode unavailable: {reason}"
        if warning is not None and self._diagnostic is not None:
            self._diagnostic(warning)
        return result.available

    def prepare_extensions(
        self, mapping: Mapping[str, ToolPort]
    ) -> ToolCapabilityState:
        """Build the capability value a reload would publish, changing nothing.

        Candidate-only: the returned value is unreachable from the live state
        until :meth:`publish` assigns it, so a reload that fails afterwards
        leaves the previous generation complete. Selected optional builtin
        availability is probed lazily outside the shared state lock; its cached
        result is guarded by that lock and concurrent probes are serialized;
        extension overrides never trigger that probe.

        The carried selection here is a preview only. Where it is carried rather
        than re-derived from a filter, :meth:`publish` rebinds it to whatever is
        live at the swap, so a selection accepted while this candidate was being
        built is not overwritten.
        """

        with self._state_lock:
            state = self._state
            options = state.filter_options
            # Pi reads the ``defaultTools`` setting only when no ``noTools``
            # mode empties the default selection.
            configured = (
                self._default_tools()
                if self._production_defaults
                and self._default_tools
                and not options.no_tools
                and not options.no_builtin_tools
                else None
            )
            codemode_loadout = (
                self._codemode_loadout() if self._codemode_loadout else None
            )
            offered = self._offered_builtins()
        selected: tuple[str, ...]
        if options.no_builtin_tools:
            selected = ()
        elif configured is not None:
            selected = tuple(configured)
        else:
            selected = tuple(self._base_registry)
        selected = apply_tool_modifiers(selected, options.modifiers)
        explicit = (
            configured is not None
            or bool(options.modifiers)
            or options.no_builtin_tools
        )
        builtin = dict(self._base_registry)
        # Registered optional ports persist for this lifetime, like Pi's
        # registry; removing a default does not deactivate an active tool.
        # A built-in extension turned off on reload takes its tool away.
        if "codemode" in offered and "codemode" in state.builtin_registry:
            builtin["codemode"] = state.builtin_registry["codemode"]
        if "tool_search" in offered:
            from pipy_harness.native.tool_search import (
                TOOL_SEARCH_TOOL_NAME,
                ToolSearchTool,
            )

            # Registered inactive (Pi ``defaultActive: false``): naming it in
            # a tool selection activates it.
            builtin[TOOL_SEARCH_TOOL_NAME] = state.builtin_registry.get(
                TOOL_SEARCH_TOOL_NAME
            ) or ToolSearchTool(self)
        wants_codemode = (
            options.allows("codemode") if options.allow else "codemode" in selected
        )
        wants_codemode = (
            wants_codemode and not options.no_tools and not options.excludes("codemode")
        )
        if "codemode" in offered and wants_codemode and "codemode" not in mapping:
            if self._probe_codemode():
                from pipy_harness.native.tools.codemode import CodemodeTool

                builtin["codemode"] = (
                    state.builtin_registry.get("codemode") or CodemodeTool()
                )
        candidate = ToolCapabilityState.build(
            builtin,
            mapping,
            filter_options=options,
            cancel_join_timeout_seconds=self._cancel_join_timeout_seconds,
            carried_active_tool_names=state.active_tool_names,
            codemode_loadout=codemode_loadout,
            mcp_registry=state.mcp_registry,
        )
        defaults = frozenset(selected) if explicit else None
        active = candidate.active_tool_names
        if explicit and not options.allow:
            active = (
                frozenset(selected)
                | activated_on_registration(mapping)
                | activated_on_registration(
                    _accepted_mcp_tools(candidate.registry, candidate.mcp_registry)
                )
            )
            active &= options.provider_visible_names(
                registered_names=candidate.registry
            )
        return replace(
            candidate,
            active_tool_names=active,
            default_tool_names=defaults,
            selected_names=tuple(configured or ()) if not options.allow else (),
            # Names the settings may select although nothing has them: the
            # runtime of ``codemode`` is unavailable, or the session was not
            # given the tool.
            recognized_names=frozenset({"codemode", "tool_search"})
            if self._production_defaults
            else frozenset(),
            selection_initialized=True,
            active_selection_customized=state.active_selection_customized,
            offered_builtins=offered,
        )

    def publish(self, state: ToolCapabilityState) -> None:
        """Make a prepared capability value live. Never fails.

        Without a configured `--allow`/`--exclude` filter the visible selection
        is carried across a reload, and it is rebound to the **live** selection
        here rather than to the one sampled during preparation. An extension
        handler may narrow the active tools while a reload is being prepared;
        publishing the earlier sample would silently discard that update. The
        rebind is a reference assignment inside the same critical section as the
        swap, so no accepted selection can be lost.
        """

        with self._state_lock:
            if not _same_ports(self._state.mcp_registry, state.mcp_registry):
                # A server's tools changed while this value was prepared.
                state = state.with_mcp_registry(
                    self._state.mcp_registry,
                    cancel_join_timeout_seconds=self._cancel_join_timeout_seconds,
                )
            if not state.filter_configured:
                live = self._state
                active = (
                    live.active_tool_names
                    if live.selection_initialized
                    else state.active_tool_names
                )
                if live.selection_initialized and (
                    state.default_tool_names is not None
                    or live.default_tool_names is not None
                ):
                    previous_defaults = (
                        live.default_tool_names
                        if live.default_tool_names is not None
                        else frozenset(self._base_registry)
                    )
                    next_defaults = (
                        state.default_tool_names
                        if state.default_tool_names is not None
                        else frozenset(self._base_registry)
                    )
                    if active is None:
                        active = activated_on_registration(live.registry) | (
                            next_defaults - previous_defaults
                        )
                    else:
                        active = (
                            active | (next_defaults - previous_defaults)
                        ) & frozenset(state.registry)
                if (
                    live.selection_initialized
                    and not live.active_selection_customized
                    and active is not None
                ):
                    # Pi ``_refreshToolRegistry``: new tools, and tools whose
                    # exposure became declarable, are activated like new ones.
                    # Also without configured defaults: ``tool_search`` and
                    # the MCP discovery only add to the selection.
                    active |= activated_on_registration(
                        state.extension_registry
                    ).difference(activated_on_registration(live.extension_registry))
                state = replace(
                    state,
                    active_tool_names=active,
                    active_selection_customized=live.active_selection_customized,
                )
            live = self._state
            if live.active_tool_names is not None:
                # Pi ``reload``: what was active is pending, with what was
                # pending already. Here that is the declared MCP tools, read
                # in this critical section because a configured filter
                # derives the selection again and drops what ``tool_search``
                # loaded. While their servers keep running the names are
                # active as well and only wait for the next run to start;
                # :meth:`restart_mcp_tools` is what unregisters the tools.
                restorable = live.pending_tool_names | frozenset(
                    name for name in _declared_names(live) if is_mcp_tool_name(name)
                )
                if restorable:
                    state = replace(state, pending_tool_names=restorable)
            self._state = state

    def publish_extension_tools(
        self,
        ports: Callable[[], Mapping[str, ToolPort]],
        *,
        extension_generation: object,
    ) -> bool:
        """Replace the extension tools of the live value with ``ports()``,
        which runs under the state lock, so of two calls the later one
        publishes what was registered last. Nothing changes, and the answer
        is False, when the live value belongs to another extension
        generation (a ``/reload`` replaced the one that registered)."""

        with self._state_lock:
            live = self._state
            if live.extension_generation is not extension_generation:
                return False
            self._state = live.with_extension_registry(
                ports(),
                cancel_join_timeout_seconds=self._cancel_join_timeout_seconds,
            )
            return True

    def publish_mcp_tools(
        self, mcp_registry: Mapping[str, ToolPort]
    ) -> tuple[str, ...]:
        """Replace the MCP tools of the live value; return the names left out.

        A name is left out when a built-in or extension tool has it. The new
        value is built from the live one and assigned in one critical
        section, so it keeps a selection or a reload published just before,
        and a reader sees either the old tools or the new ones. May be called
        from any thread.
        """

        with self._state_lock:
            state = self._state.with_mcp_registry(
                mcp_registry,
                cancel_join_timeout_seconds=self._cancel_join_timeout_seconds,
            )
            self._state = state
        accepted = _accepted_mcp_tools(state.registry, state.mcp_registry)
        return tuple(name for name in state.mcp_registry if name not in accepted)

    def snapshot_for_projection(
        self, projection_state: ToolCapabilityState
    ) -> NativeToolCapabilitySnapshot:
        """Bind one immutable live selection to its projected extension generation.

        The live value may have other MCP tools, and so another registry and
        executor, than the value the generation was published with.
        """

        with self._state_lock:
            state = self._state
            if state.extension_generation is not projection_state.extension_generation:
                raise RuntimeError("tool capability generation is incoherent")
            return NativeToolCapabilitySnapshot(self, state)

    def callable_snapshot(self) -> NativeToolCapabilitySnapshot:
        """Pin the live value for one composite parent's nested calls."""

        return NativeToolCapabilitySnapshot(self, self.state)

    def composite_enabled(self) -> bool:
        return NativeToolCapabilitySnapshot(self, self.state).composite_enabled()

    def definitions(
        self,
        allowed_names: Sequence[str] | None = None,
        /,
    ) -> tuple[ToolDefinition, ...]:
        return NativeToolCapabilitySnapshot(self, self.state).definitions(allowed_names)

    def execute(
        self,
        call: AgentToolCall,
        *,
        output_sink: Callable[[str], None] | None = None,
        wait_for_interrupt: ToolInterruptWaiter | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> ToolExecutionOutcome:
        return NativeToolCapabilitySnapshot(self, self.state).execute(
            call,
            output_sink=output_sink,
            wait_for_interrupt=wait_for_interrupt,
            arguments=arguments,
        )

    def error_result(
        self,
        call: AgentToolCall,
        output_text: str,
        /,
    ) -> AgentToolResultMessage:
        return NativeToolCapabilitySnapshot(self, self.state).error_result(
            call, output_text
        )

    def validated_arguments(
        self, call: AgentToolCall, /
    ) -> dict[str, Any] | ToolExecutionOutcome:
        return NativeToolCapabilitySnapshot(self, self.state).validated_arguments(call)


def _same_ports(left: Mapping[str, ToolPort], right: Mapping[str, ToolPort]) -> bool:
    """Whether two registries hold the same ports under the same names, in order."""

    return len(left) == len(right) and all(
        name == other and port is right[other]
        for (name, port), other in zip(left.items(), right, strict=True)
    )


def _is_builtin_codemode(state: ToolCapabilityState) -> bool:
    """Whether the registered ``codemode`` is the builtin, not an extension's."""

    builtin = state.builtin_registry.get("codemode")
    return (
        builtin is not None
        and state.registry.get("codemode") is builtin
        and "codemode" not in state.extension_registry
    )


def _redescribed(definition: ToolDefinition, description: str) -> ToolDefinition:
    """The declared copy of a definition with another description.

    The strict subset caps a description at 4 KiB, which a tool listing or an
    appended note may exceed. Such a copy is declared as a foreign schema,
    which has no cap; the strict subset is valid there. Arguments are still
    checked against the registered definition.
    """

    if not description:
        return definition
    oversized = len(description) > ToolDefinition.DESCRIPTION_MAX_LENGTH
    return replace(
        definition,
        description=description,
        schema_mode="foreign" if oversized else definition.schema_mode,
    )


def _unregistered_mcp_names(
    state: ToolCapabilityState, names: Collection[str]
) -> frozenset[str]:
    """The names of MCP tools among ``names`` that no tool has in ``state``
    and that the filters would keep registered. Pure."""

    missing = frozenset(
        name for name in names if is_mcp_tool_name(name) and name not in state.registry
    )
    if not missing or not state.filter_configured:
        return missing
    return state.filter_options.provider_visible_names(registered_names=missing)


def _active_names(state: ToolCapabilityState) -> frozenset[str]:
    """Resolve the active set; no selection means "as registration left it"."""

    if state.active_tool_names is not None:
        return state.active_tool_names
    return activated_on_registration(state.registry)


def _definitions_for(
    state: ToolCapabilityState,
    allowed_names: Sequence[str] | None,
) -> tuple[ToolDefinition, ...]:
    """Project one capability value's declared definitions. Pure.

    The declared tools are the active ones the filters let be declared (Pi
    ``_applyToolLoadout``). A tool with exposure ``hidden`` is registered but
    unreachable: it is never declared, also when it is named as active, and a
    model call to it is therefore an unknown tool. A ``codemode`` or
    ``deferred`` tool is declared only when it was activated by name. Explicit
    ``allowed_names`` select from the whole registry.
    """

    allowed = (
        frozenset(str(name) for name in allowed_names)
        if allowed_names is not None
        else _declared_names(state)
    )
    definitions = (
        port.definition for name, port in state.registry.items() if name in allowed
    )
    return tuple(
        definition for definition in definitions if definition.exposure != "hidden"
    )


def _kept_names(state: ToolCapabilityState) -> Collection[str]:
    """Registered names the ``--tools``/``--exclude-tools`` filter keeps.

    A tool the filter does not keep is not registered in Pi.
    """

    if not state.filter_configured:
        return state.registry
    return state.filter_options.provider_visible_names(registered_names=state.registry)


def _is_activatable(
    state: ToolCapabilityState, name: str, kept: Collection[str]
) -> bool:
    """Whether a kept tool may be active, which declares it. Pure.

    Pi ``_isActivatable``: an MCP tool that an allowlist keeps without naming
    or matching it is for scripts and ``tool_search`` only. It may be declared
    only when ``tool_search`` can load it: its exposure is not ``direct`` and
    ``tool_search`` is registered, which here means kept by the filters.
    """

    options = state.filter_options
    if not options.allow or options.allows(name) or not is_mcp_tool_name(name):
        return True
    return state.registry[name].definition.exposure != "direct" and (
        "tool_search" in kept
    )


def _declared_names(state: ToolCapabilityState) -> frozenset[str]:
    """The active names that the filters let be declared. Pure.

    Pi drops such names when it applies a loadout, whoever named them: a
    selection, a restored transcript or an extension. Here the active set is
    kept as it was given and narrowed whenever it is read, so a reload that
    changes an exposure needs no second bookkeeping.
    """

    active = _active_names(state)
    if not state.filter_configured:
        return active
    kept = _kept_names(state)
    return frozenset(
        name for name in active if name in kept and _is_activatable(state, name, kept)
    )


def _loadable_tools(state: ToolCapabilityState) -> tuple[ToolDefinition, ...]:
    """The tools ``tool_search`` may load from one capability value. Pure.

    Pi ``searchAndLoad``: the registered ``codemode`` and ``deferred`` tools
    that are not active yet. A tool that may not be declared is left out,
    because loading it would declare nothing.
    """

    kept = _kept_names(state)
    declared = _declared_names(state)
    return tuple(
        port.definition
        for name, port in state.registry.items()
        if name in kept
        and name not in declared
        and port.definition.exposure in ("codemode", "deferred")
        and _is_activatable(state, name, kept)
    )


def _callable_names(state: ToolCapabilityState) -> frozenset[str]:
    """Names other tools may call in one capability value. Pure.

    Pi ``_getCallableTools``: the active ``direct`` tools and every registered
    ``codemode`` or ``deferred`` tool, whether or not it is active. A tool the
    ``--tools``/``--exclude-tools`` filter does not keep is not registered in
    Pi, so it is not callable here either. ``model-only`` and ``hidden`` tools
    are never callable.
    """

    kept = _kept_names(state)
    active = _declared_names(state)
    names: set[str] = set()
    for name, port in state.registry.items():
        if name not in kept:
            continue
        exposure = port.definition.exposure
        if exposure in ("codemode", "deferred") or (
            exposure == "direct" and name in active
        ):
            names.add(name)
    return frozenset(names)
