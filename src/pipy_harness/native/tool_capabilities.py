"""Product composition for the canonical agent tool-capability port."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

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


class NativeToolCapabilities:
    """Compose product tool registries, visibility policy, and execution."""

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
    ) -> None:
        self._builtin_registry = dict(builtin_registry)
        self._extension_registry = dict(extension_registry)
        self._filter_options = filter_options
        self._filter_configured = filter_options != ToolFilterOptions.empty()
        self._active_tool_names: set[str] | None = None
        self._context = ToolContext(
            workspace_root=workspace_root,
            stderr_sink=stderr_sink,
            reference_roots=reference_roots,
        )
        self._cancel_join_timeout_seconds = cancel_join_timeout_seconds
        self._registry: dict[str, ToolPort] = {}
        self._executor: ToolExecutor
        self._rebuild_registry()
        if self._filter_configured:
            self._active_tool_names = self._configured_tool_names()

    @property
    def builtin_names(self) -> tuple[str, ...]:
        return tuple(self._builtin_registry)

    @property
    def registered_names(self) -> tuple[str, ...]:
        return tuple(self._registry)

    @property
    def unknown_filter_names(self) -> tuple[str, ...]:
        configured_names = set(self._filter_options.allow) | set(
            self._filter_options.exclude
        )
        return tuple(sorted(configured_names.difference(self._registry)))

    def set_active_tools(self, names: Sequence[str]) -> bool:
        """Atomically replace the provider-visible tool-name selection."""

        normalized = {str(name) for name in names if str(name)}
        if any(name not in self._registry for name in normalized):
            return False
        self._active_tool_names = normalized
        return True

    def replace_extensions(self, mapping: Mapping[str, ToolPort]) -> None:
        """Replace extension tools while preserving the run's visibility mode."""

        self._extension_registry = dict(mapping)
        self._rebuild_registry()
        if self._filter_configured:
            self._active_tool_names = self._configured_tool_names()

    def definitions(
        self,
        allowed_names: Sequence[str] | None = None,
        /,
    ) -> tuple[ToolDefinition, ...]:
        allowed = (
            {str(name) for name in allowed_names}
            if allowed_names is not None
            else self._active_tool_names
        )
        return tuple(
            port.definition
            for name, port in self._registry.items()
            if allowed is None or name in allowed
        )

    def execute(
        self,
        call: AgentToolCall,
        *,
        output_sink: Callable[[str], None] | None = None,
        wait_for_interrupt: ToolInterruptWaiter | None = None,
    ) -> ToolExecutionOutcome:
        visible_before = tuple(definition.name for definition in self.definitions(None))
        outcome = self._executor.execute(
            call,
            replace(self._context, output_sink=output_sink),
            wait_for_interrupt=wait_for_interrupt,
        )
        visible_after = tuple(definition.name for definition in self.definitions(None))
        if (
            call.tool_name in self._extension_registry
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
        return self._executor.error_result(call, output_text)

    def _configured_tool_names(self) -> set[str]:
        return set(
            self._filter_options.provider_visible_names(
                builtin_names=self._builtin_registry,
                registered_names=self._registry,
            )
        )

    def _rebuild_registry(self) -> None:
        self._registry = dict(self._builtin_registry)
        self._registry.update(self._extension_registry)
        self._executor = ToolExecutor(
            self._registry,
            cancel_join_timeout_seconds=self._cancel_join_timeout_seconds,
        )
