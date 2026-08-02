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


@dataclass(frozen=True, slots=True)
class ToolFilterOptions:
    """Pi-style per-run tool visibility controls."""

    allow: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
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
        return self.filter_options != ToolFilterOptions.empty()

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

    def execute(
        self,
        call: AgentToolCall,
        *,
        output_sink: Callable[[str], None] | None = None,
        wait_for_interrupt: ToolInterruptWaiter | None = None,
    ) -> ToolExecutionOutcome:
        visible_before = tuple(
            definition.name for definition in _definitions_for(self.state, None)
        )
        outcome = self.state.executor.execute(
            call,
            replace(self.owner._context, output_sink=output_sink),
            wait_for_interrupt=wait_for_interrupt,
        )
        state_after = self.owner.state
        visible_after = tuple(
            definition.name for definition in _definitions_for(state_after, None)
        )
        if (
            state_after.executor is self.state.executor
            and call.tool_name in self.state.extension_registry
            and not outcome.result.is_error
            and set(visible_before).issubset(visible_after)
        ):
            before_names = set(visible_before)
            added_tool_names = tuple(
                name for name in visible_after if name not in before_names
            )
            if added_tool_names:
                outcome = replace(
                    outcome,
                    result=replace(
                        outcome.result,
                        added_tool_names=added_tool_names,
                    ),
                )
        return outcome

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
    mutable member lives inside one :class:`ToolCapabilityState` value that is
    replaced wholesale rather than edited in place.

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
        reference_roots: tuple[Path, ...],
        stderr_sink: Callable[[str], None],
        filter_options: ToolFilterOptions,
        cancel_join_timeout_seconds: float,
        state_lock: "threading.RLock | None" = None,
    ) -> None:
        self._context = ToolContext(
            workspace_root=workspace_root,
            stderr_sink=stderr_sink,
            reference_roots=reference_roots,
        )
        self._cancel_join_timeout_seconds = cancel_join_timeout_seconds
        self._state_lock = state_lock if state_lock is not None else threading.RLock()
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
        return tuple(sorted(configured_names.difference(state.registry)))

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
            self._state = replace(state, active_tool_names=normalized)
        return True

    def prepare_extensions(
        self, mapping: Mapping[str, ToolPort]
    ) -> ToolCapabilityState:
        """Build the capability value a reload would publish, changing nothing.

        Candidate-only: the returned value is unreachable from the live state
        until :meth:`publish` assigns it, so a reload that fails afterwards
        leaves the previous generation complete.

        The carried selection here is a preview only. Where it is carried rather
        than re-derived from a filter, :meth:`publish` rebinds it to whatever is
        live at the swap, so a selection accepted while this candidate was being
        built is not overwritten.
        """

        state = self.state
        return ToolCapabilityState.build(
            state.builtin_registry,
            mapping,
            filter_options=state.filter_options,
            cancel_join_timeout_seconds=self._cancel_join_timeout_seconds,
            carried_active_tool_names=state.active_tool_names,
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
                state = replace(state, active_tool_names=self._state.active_tool_names)
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
