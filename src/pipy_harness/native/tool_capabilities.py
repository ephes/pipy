"""Product composition for the canonical agent tool-capability port."""

from __future__ import annotations

import threading
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from pipy_harness.native.agent.messages import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.agent.tools import (
    ToolExecutionOutcome,
    ToolExecutor,
    ToolInterruptWaiter,
)
from pipy_harness.native.tools import ToolContext, ToolDefinition, ToolPort

DEFAULT_TOOL_NAMES = ("read", "ls", "grep", "find", "write", "edit", "bash")


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
    """Pi-style per-run tool visibility controls."""

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

    @classmethod
    def empty(cls) -> ToolFilterOptions:
        return cls()

    def provider_visible_names(
        self,
        *,
        builtin_names: Collection[str],
        registered_names: Collection[str],
    ) -> frozenset[str]:
        """Return names visible after applying the product filter policy."""

        if self.no_tools:
            return frozenset()
        names = set(registered_names)
        if self.no_builtin_tools:
            names.difference_update(builtin_names)
        if self.allow:
            names.intersection_update(self.allow)
        if self.exclude:
            names.difference_update(self.exclude)
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

    def __post_init__(self) -> None:
        # Enforce immutability on the type, not just in `build`. The copy is
        # unconditional: a `MappingProxyType` is only a read-only *view*, so
        # wrapping a caller's dict without copying would still let whoever
        # retains that dict edit a published registry outside the lock and
        # desynchronize it from the executor built over it.
        for field_name in ("builtin_registry", "extension_registry", "registry"):
            object.__setattr__(
                self, field_name, MappingProxyType(dict(getattr(self, field_name)))
            )

    @property
    def filter_configured(self) -> bool:
        return replace(self.filter_options, modifiers=()) != ToolFilterOptions.empty()

    @classmethod
    def build(
        cls,
        builtin_registry: Mapping[str, ToolPort],
        extension_registry: Mapping[str, ToolPort],
        *,
        filter_options: ToolFilterOptions,
        cancel_join_timeout_seconds: float,
        carried_active_tool_names: frozenset[str] | None = None,
    ) -> "ToolCapabilityState":
        """Build a complete capability value without touching any live state.

        A configured `--allow`/`--exclude` filter re-derives the visible set
        from the new registry. Without one, an extension's `set_active_tools`
        selection is carried across unchanged, which is the established
        behavior.
        """

        builtin = dict(builtin_registry)
        extensions = dict(extension_registry)
        registry = dict(builtin)
        registry.update(extensions)
        active = carried_active_tool_names
        if filter_options != ToolFilterOptions.empty():
            active = filter_options.provider_visible_names(
                builtin_names=builtin,
                registered_names=registry,
            )
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
        )


@dataclass(frozen=True, slots=True)
class NativeToolCapabilitySnapshot:
    """One provider turn's advertised and executable tool generation."""

    owner: NativeToolCapabilities
    state: ToolCapabilityState

    def definitions(
        self,
        allowed_names: Sequence[str] | None = None,
        /,
    ) -> tuple[ToolDefinition, ...]:
        return _definitions_for(self.state, allowed_names)

    def eligible_names(self) -> frozenset[str]:
        """Builtin identities in this frozen registry, excluding replacements."""
        return frozenset(
            name
            for name, port in self.state.builtin_registry.items()
            if self.state.registry.get(name) is port
            and name not in self.state.extension_registry
        )

    def composite_enabled(self) -> bool:
        """Only a visible builtin identity may activate internal dispatch."""
        return "codemode" in self.eligible_names() and any(
            definition.name == "codemode" for definition in self.definitions()
        )

    def execute(
        self,
        call: AgentToolCall,
        *,
        output_sink: Callable[[str], None] | None = None,
        wait_for_interrupt: ToolInterruptWaiter | None = None,
        extension_generation_id: int | None = None,
    ) -> ToolExecutionOutcome:
        return self.state.executor.execute(
            call,
            replace(
                self.owner._context,
                output_sink=output_sink,
                extension_generation_id=extension_generation_id,
            ),
            wait_for_interrupt=wait_for_interrupt,
        )

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
    ) -> None:
        self._context = ToolContext(workspace_root=workspace_root)
        self._cancel_join_timeout_seconds = cancel_join_timeout_seconds
        self._state_lock = state_lock if state_lock is not None else threading.RLock()
        from pipy_harness.native.tools.registry import ProductionToolRegistry

        self._production_defaults = isinstance(builtin_registry, ProductionToolRegistry)
        self._base_registry = dict(builtin_registry)
        self._default_tools = default_tools
        self._diagnostic = diagnostic
        # Run-owned result; all accesses take _state_lock.
        self._codemode_available: bool | None = None
        # Detached preparations share a probe without waiting under the session lock.
        self._codemode_probe_lock = threading.Lock()
        self._state = ToolCapabilityState.build(
            builtin_registry,
            extension_registry,
            filter_options=filter_options,
            cancel_join_timeout_seconds=cancel_join_timeout_seconds,
        )

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
        return tuple(
            sorted(
                configured_names.difference(state.registry).difference(
                    state.recognized_names
                )
            )
        )

    def set_active_tools(self, names: Sequence[str]) -> bool:
        """Atomically replace the provider-visible tool-name selection.

        Validation and assignment share one critical section, so a concurrent
        publication can neither be undone by a selection derived from the
        superseded registry nor slip a name past the registry check.
        """

        normalized = frozenset(str(name) for name in names if str(name))
        with self._state_lock:
            state = self._state
            if any(name not in state.registry for name in normalized):
                return False
            self._state = replace(
                state,
                active_tool_names=normalized,
                selection_initialized=True,
                active_selection_customized=True,
            )
        return True

    def restore_active_tools(self, names: Sequence[str]) -> None:
        """Pi ``setActiveToolsByName`` from a transcript's declared tools.

        Unlike :meth:`set_active_tools`, unknown names are dropped rather
        than refused: Pi keeps only registered tools, and its registry
        already excludes names a ``--allow``/``--exclude`` filter hides, so a
        configured filter narrows the restored set too. Filtering and
        assignment share one critical section.
        """

        with self._state_lock:
            state = self._state
            visible: Collection[str] = state.registry
            if state.filter_configured:
                visible = state.filter_options.provider_visible_names(
                    builtin_names=state.builtin_registry,
                    registered_names=state.registry,
                )
            restored = frozenset(name for name in names if name in visible)
            self._state = replace(
                state,
                active_tool_names=restored,
                selection_initialized=True,
                active_selection_customized=True,
            )

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
            configured = (
                self._default_tools()
                if self._production_defaults and self._default_tools
                else None
            )
        selected = (
            tuple(configured) if configured is not None else tuple(self._base_registry)
        )
        selected = apply_tool_modifiers(selected, options.modifiers)
        explicit = configured is not None or bool(options.modifiers)
        builtin = dict(self._base_registry)
        # Registered optional ports persist for this lifetime, like Pi's
        # registry; removing a default does not deactivate an active tool.
        if self._production_defaults and "codemode" in state.builtin_registry:
            builtin["codemode"] = state.builtin_registry["codemode"]
        wants_codemode = "codemode" in (options.allow or selected)
        wants_codemode = (
            wants_codemode
            and not options.no_tools
            and not options.no_builtin_tools
            and "codemode" not in options.exclude
        )
        if self._production_defaults and wants_codemode and "codemode" not in mapping:
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
        )
        defaults = frozenset(selected) if explicit else None
        active = candidate.active_tool_names
        if explicit and not options.allow:
            active = frozenset(selected) | frozenset(mapping)
            active &= options.provider_visible_names(
                builtin_names=builtin, registered_names=candidate.registry
            )
        return replace(
            candidate,
            active_tool_names=active,
            default_tool_names=defaults,
            selected_names=tuple(configured or ()) if not options.allow else (),
            recognized_names=frozenset({"codemode"})
            if self._production_defaults
            else frozenset(),
            selection_initialized=True,
            active_selection_customized=state.active_selection_customized,
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
                        active = frozenset(live.registry) | (
                            next_defaults - previous_defaults
                        )
                    else:
                        active = (
                            active | (next_defaults - previous_defaults)
                        ) & frozenset(state.registry)
                if (
                    live.selection_initialized
                    and not live.active_selection_customized
                    and state.default_tool_names is not None
                    and active is not None
                ):
                    active |= frozenset(state.extension_registry).difference(
                        live.extension_registry
                    )
                state = replace(
                    state,
                    active_tool_names=active,
                    active_selection_customized=live.active_selection_customized,
                )
            self._state = state

    def snapshot_for_projection(
        self, projection_state: ToolCapabilityState
    ) -> NativeToolCapabilitySnapshot:
        """Bind one immutable live selection to its projected registry/executor."""

        with self._state_lock:
            state = self._state
            if state.executor is not projection_state.executor:
                raise RuntimeError("tool capability generation is incoherent")
            return NativeToolCapabilitySnapshot(self, state)

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
    ) -> ToolExecutionOutcome:
        return NativeToolCapabilitySnapshot(self, self.state).execute(
            call,
            output_sink=output_sink,
            wait_for_interrupt=wait_for_interrupt,
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


def _definitions_for(
    state: ToolCapabilityState,
    allowed_names: Sequence[str] | None,
) -> tuple[ToolDefinition, ...]:
    """Project one capability value's visible definitions. Pure."""

    allowed: frozenset[str] | None = (
        frozenset(str(name) for name in allowed_names)
        if allowed_names is not None
        else state.active_tool_names
    )
    return tuple(
        port.definition
        for name, port in state.registry.items()
        if allowed is None or name in allowed
    )
