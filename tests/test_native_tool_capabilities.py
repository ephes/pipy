"""Contracts for the product adapter to the canonical agent tool port."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import FrozenInstanceError, dataclass, field
from pathlib import Path
from typing import cast

import pytest

from pipy_harness.native.agent import (
    AGENT_TOOL_REQUEST_ID_PREFIX,
    AgentToolCall,
    AgentToolResultMessage,
    ProductContent,
)
from pipy_harness.native.agent.tools import (
    AgentToolCapabilities,
    ToolExecutionOutcome,
    ToolInterruptWaiter,
)
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    ToolFilterOptions,
)
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolPort,
    ToolRequest,
)


_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class _RecordingTool:
    name: str
    invoke_hook: Callable[[ToolRequest, ToolContext], None] | None = None
    is_error: bool = False
    requests: list[ToolRequest] = field(default_factory=list)
    contexts: list[ToolContext] = field(default_factory=list)

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=f"Fixture {self.name} tool.",
            input_schema=_SCHEMA,
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        self.requests.append(request)
        self.contexts.append(context)
        if self.invoke_hook is not None:
            self.invoke_hook(request, context)
        if context.output_sink is not None:
            context.output_sink(f"live:{self.name}")
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"result:{self.name}",
            is_error=self.is_error,
            provider_correlation_id=request.provider_correlation_id,
        )


def _capabilities(
    tmp_path: Path,
    *,
    builtins: tuple[_RecordingTool, ...] = (),
    extensions: tuple[_RecordingTool, ...] = (),
    options: ToolFilterOptions | None = None,
    reference_roots: tuple[Path, ...] = (),
    stderr_sink: Callable[[str], None] | None = None,
) -> NativeToolCapabilities:
    return NativeToolCapabilities(
        {tool.name: tool for tool in builtins},
        {tool.name: tool for tool in extensions},
        workspace_root=tmp_path,
        reference_roots=reference_roots,
        stderr_sink=stderr_sink or (lambda _text: None),
        filter_options=options or ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.1,
    )


def _call(name: str = "echo") -> AgentToolCall:
    return AgentToolCall(
        provider_correlation_id=f"provider-{name}",
        tool_name=name,
        arguments_json=ProductContent('{"text":"hello"}'),
    )


def _names(capabilities: NativeToolCapabilities) -> tuple[str, ...]:
    return tuple(definition.name for definition in capabilities.definitions())


def test_filter_options_are_frozen_and_runtime_validated() -> None:
    options = ToolFilterOptions(allow=("read",), exclude=("write",))

    with pytest.raises(FrozenInstanceError):
        setattr(options, "allow", ("bash",))
    with pytest.raises(TypeError, match="allow must be a tuple of strings"):
        ToolFilterOptions(allow=cast(tuple[str, ...], ["read"]))
    with pytest.raises(TypeError, match="allow must be a tuple of strings"):
        ToolFilterOptions(allow=(cast(str, 7),))
    with pytest.raises(TypeError, match="exclude must be a tuple of strings"):
        ToolFilterOptions(exclude=cast(tuple[str, ...], ["write"]))
    with pytest.raises(TypeError, match="no_tools must be a bool"):
        ToolFilterOptions(no_tools=cast(bool, 1))
    with pytest.raises(TypeError, match="no_builtin_tools must be a bool"):
        ToolFilterOptions(no_builtin_tools=cast(bool, 0))


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        (ToolFilterOptions.empty(), frozenset({"read", "extension"})),
        (ToolFilterOptions(no_tools=True), frozenset()),
        (ToolFilterOptions(no_builtin_tools=True), frozenset({"extension"})),
        (ToolFilterOptions(allow=("read",)), frozenset({"read"})),
        (
            ToolFilterOptions(exclude=("read",)),
            frozenset({"extension"}),
        ),
    ],
)
def test_filter_options_are_the_canonical_provider_visibility_policy(
    options: ToolFilterOptions,
    expected: frozenset[str],
) -> None:
    assert (
        options.provider_visible_names(
            builtin_names=("read",),
            registered_names=("read", "extension"),
        )
        == expected
    )


def test_definitions_preserve_merged_registry_order_and_report_unknown_filters(
    tmp_path: Path,
) -> None:
    capabilities = _capabilities(
        tmp_path,
        builtins=(_RecordingTool("second"), _RecordingTool("first")),
        extensions=(_RecordingTool("extension"), _RecordingTool("later")),
        options=ToolFilterOptions(
            allow=("first", "extension", "missing-allow"),
            exclude=("extension", "missing-exclude"),
        ),
    )

    assert capabilities.builtin_names == ("second", "first")
    assert capabilities.registered_names == (
        "second",
        "first",
        "extension",
        "later",
    )
    assert _names(capabilities) == ("first",)
    assert capabilities.unknown_filter_names == (
        "missing-allow",
        "missing-exclude",
    )


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        (ToolFilterOptions(no_tools=True, allow=("extension",)), ()),
        (ToolFilterOptions(no_builtin_tools=True), ("extension",)),
        (
            ToolFilterOptions(
                no_builtin_tools=True,
                allow=("builtin", "extension"),
                exclude=("extension",),
            ),
            (),
        ),
    ],
)
def test_filter_precedence_is_no_tools_then_builtin_then_allow_then_exclude(
    tmp_path: Path,
    options: ToolFilterOptions,
    expected: tuple[str, ...],
) -> None:
    capabilities = _capabilities(
        tmp_path,
        builtins=(_RecordingTool("builtin"),),
        extensions=(_RecordingTool("extension"),),
        options=options,
    )

    assert _names(capabilities) == expected


def test_active_tool_replacement_accepts_empty_and_rejects_unknown_atomically(
    tmp_path: Path,
) -> None:
    capabilities = _capabilities(
        tmp_path,
        builtins=(_RecordingTool("first"), _RecordingTool("second")),
    )

    assert capabilities.set_active_tools([]) is True
    assert _names(capabilities) == ()
    assert capabilities.set_active_tools(["missing"]) is False
    assert _names(capabilities) == ()
    assert capabilities.set_active_tools(["second", "second", ""]) is True
    assert _names(capabilities) == ("second",)
    assert capabilities.set_active_tools(["first", "missing"]) is False
    assert _names(capabilities) == ("second",)


def test_extension_replacement_preserves_unfiltered_sentinel(
    tmp_path: Path,
) -> None:
    capabilities = _capabilities(
        tmp_path,
        builtins=(_RecordingTool("builtin"),),
        extensions=(_RecordingTool("old_extension"),),
    )

    capabilities.publish(
        capabilities.prepare_extensions(
            {"new_extension": _RecordingTool("new_extension")}
        )
    )

    assert _names(capabilities) == ("builtin", "new_extension")


def test_extension_replacement_recomputes_configured_filters(
    tmp_path: Path,
) -> None:
    capabilities = _capabilities(
        tmp_path,
        builtins=(_RecordingTool("builtin"),),
        extensions=(_RecordingTool("old_extension"),),
        options=ToolFilterOptions(
            allow=("builtin", "old_extension", "new_extension"),
            exclude=("builtin",),
        ),
    )

    capabilities.publish(
        capabilities.prepare_extensions(
            {"new_extension": _RecordingTool("new_extension")}
        )
    )

    assert _names(capabilities) == ("new_extension",)
    assert capabilities.unknown_filter_names == ("old_extension",)


def test_extension_replacement_preserves_explicit_active_narrowing(
    tmp_path: Path,
) -> None:
    capabilities = _capabilities(
        tmp_path,
        builtins=(_RecordingTool("builtin"),),
        extensions=(_RecordingTool("old_extension"),),
    )
    assert capabilities.set_active_tools(("builtin", "old_extension")) is True

    capabilities.publish(
        capabilities.prepare_extensions(
            {"new_extension": _RecordingTool("new_extension")}
        )
    )

    assert _names(capabilities) == ("builtin",)


def test_execution_forwards_product_context_live_output_and_identity_domains(
    tmp_path: Path,
) -> None:
    workspace = tmp_path.resolve()
    reference_root = (tmp_path / "reference").resolve()
    stderr_chunks: list[str] = []
    live_chunks: list[str] = []
    stderr_sink = stderr_chunks.append
    tool = _RecordingTool("echo")
    capabilities = _capabilities(
        workspace,
        builtins=(tool,),
        reference_roots=(reference_root,),
        stderr_sink=stderr_sink,
    )

    outcome = capabilities.execute(_call(), output_sink=live_chunks.append)

    assert outcome.result.content == ProductContent("result:echo")
    assert outcome.result.provider_correlation_id == "provider-echo"
    assert outcome.result.tool_request_id.startswith(AGENT_TOOL_REQUEST_ID_PREFIX)
    assert tool.requests[0].tool_request_id == outcome.result.tool_request_id
    assert tool.contexts[0].workspace_root == workspace
    assert tool.contexts[0].reference_roots == (reference_root,)
    assert tool.contexts[0].stderr_sink is stderr_sink
    assert live_chunks == ["live:echo"]

    error = capabilities.error_result(_call("missing"), "bounded failure")
    assert error.tool_request_id.startswith(AGENT_TOOL_REQUEST_ID_PREFIX)
    assert error.tool_name == "missing"
    assert error.content == ProductContent("bounded failure")
    assert error.is_error is True
    assert error.provider_correlation_id == "provider-missing"


@pytest.mark.parametrize(
    ("extension_error", "next_names", "expected_added"),
    [
        (False, ("loader", "late"), ("late",)),
        (False, ("late",), ()),
        (True, ("loader", "late"), ()),
    ],
)
def test_extension_results_mark_only_successful_additive_visibility(
    tmp_path: Path,
    extension_error: bool,
    next_names: tuple[str, ...],
    expected_added: tuple[str, ...],
) -> None:
    holder: list[NativeToolCapabilities] = []

    def activate(_request: ToolRequest, _context: ToolContext) -> None:
        assert holder[0].set_active_tools(next_names) is True

    loader = _RecordingTool("loader", invoke_hook=activate, is_error=extension_error)
    capabilities = _capabilities(
        tmp_path,
        extensions=(loader, _RecordingTool("late")),
    )
    holder.append(capabilities)
    assert capabilities.set_active_tools(("loader",)) is True

    outcome = capabilities.execute(_call("loader"))

    assert outcome.result.added_tool_names == expected_added


def test_builtin_results_do_not_announce_additive_visibility(tmp_path: Path) -> None:
    holder: list[NativeToolCapabilities] = []

    def activate(_request: ToolRequest, _context: ToolContext) -> None:
        assert holder[0].set_active_tools(("loader", "late")) is True

    loader = _RecordingTool("loader", invoke_hook=activate)
    capabilities = _capabilities(
        tmp_path,
        builtins=(loader,),
        extensions=(_RecordingTool("late"),),
    )
    holder.append(capabilities)
    assert capabilities.set_active_tools(("loader",)) is True

    outcome = capabilities.execute(_call("loader"))

    assert outcome.result.added_tool_names == ()


def test_agent_protocol_accepts_a_headless_fake_implementation() -> None:
    class _HeadlessCapabilities:
        def definitions(
            self, allowed_names: Sequence[str] | None = None, /
        ) -> tuple[ToolDefinition, ...]:
            del allowed_names
            return ()

        def execute(
            self,
            call: AgentToolCall,
            *,
            output_sink: Callable[[str], None] | None = None,
            wait_for_interrupt: ToolInterruptWaiter | None = None,
        ) -> ToolExecutionOutcome:
            del call, output_sink, wait_for_interrupt
            raise AssertionError("execution is not required for protocol discovery")

        def error_result(
            self, call: AgentToolCall, output_text: str, /
        ) -> AgentToolResultMessage:
            del call, output_text
            raise AssertionError("errors are not required for protocol discovery")

    capabilities: AgentToolCapabilities = _HeadlessCapabilities()
    assert isinstance(capabilities, AgentToolCapabilities)
    assert capabilities.definitions() == ()


def test_request_override_selects_from_full_registry_pending_request_seam(
    tmp_path: Path,
) -> None:
    capabilities = _capabilities(
        tmp_path,
        builtins=(_RecordingTool("visible"), _RecordingTool("filtered_out")),
        options=ToolFilterOptions(allow=("visible",)),
    )

    assert _names(capabilities) == ("visible",)
    assert tuple(
        definition.name for definition in capabilities.definitions(("filtered_out",))
    ) == ("filtered_out",)


def test_filtered_out_registered_calls_execute_pending_request_seam(
    tmp_path: Path,
) -> None:
    hidden = _RecordingTool("hidden")
    capabilities = _capabilities(
        tmp_path,
        builtins=(hidden,),
        options=ToolFilterOptions(no_tools=True),
    )

    assert capabilities.definitions() == ()
    outcome = capabilities.execute(_call("hidden"))

    assert outcome.result.content == ProductContent("result:hidden")
    assert len(hidden.requests) == 1


def test_prepared_extensions_leave_the_live_generation_untouched(
    tmp_path: Path,
) -> None:
    """Candidate preparation must deliver nothing until it is published."""

    capabilities = _capabilities(
        tmp_path,
        builtins=(_RecordingTool("builtin"),),
        extensions=(_RecordingTool("old_extension"),),
    )
    live_before = capabilities.state

    candidate = capabilities.prepare_extensions(
        {"new_extension": _RecordingTool("new_extension")}
    )

    assert capabilities.state is live_before
    assert _names(capabilities) == ("builtin", "old_extension")
    assert tuple(candidate.registry) == ("builtin", "new_extension")
    assert candidate.executor is not live_before.executor
    assert tuple(candidate.registry) != tuple(live_before.registry)

    capabilities.publish(candidate)

    # Publication rebinds the carried selection to whatever is live at the
    # swap, so the published value equals the candidate rather than being it.
    assert capabilities.state.executor is candidate.executor
    assert tuple(capabilities.state.registry) == tuple(candidate.registry)
    assert _names(capabilities) == ("builtin", "new_extension")


def test_retained_capability_state_is_unchanged_by_later_publication(
    tmp_path: Path,
) -> None:
    """A retained generation must not follow the live one."""

    capabilities = _capabilities(
        tmp_path,
        builtins=(_RecordingTool("builtin"),),
        extensions=(_RecordingTool("old_extension"),),
    )
    retained = capabilities.state

    capabilities.publish(
        capabilities.prepare_extensions(
            {"new_extension": _RecordingTool("new_extension")}
        )
    )
    assert capabilities.set_active_tools(("builtin",)) is True

    assert tuple(retained.registry) == ("builtin", "old_extension")
    assert retained.active_tool_names is None
    assert tuple(retained.extension_registry) == ("old_extension",)


def test_set_active_tools_replaces_state_without_mutating_the_old_value(
    tmp_path: Path,
) -> None:
    capabilities = _capabilities(
        tmp_path,
        builtins=(_RecordingTool("first"), _RecordingTool("second")),
    )
    before = capabilities.state

    assert capabilities.set_active_tools(("second",)) is True

    assert capabilities.state is not before
    assert before.active_tool_names is None
    assert capabilities.state.active_tool_names == frozenset({"second"})
    # A rejected selection leaves the live value in place entirely.
    rejected_from = capabilities.state
    assert capabilities.set_active_tools(("missing",)) is False
    assert capabilities.state is rejected_from


def test_published_state_mappings_are_read_only(tmp_path: Path) -> None:
    capabilities = _capabilities(
        tmp_path,
        builtins=(_RecordingTool("builtin"),),
        extensions=(_RecordingTool("extension"),),
    )
    state = capabilities.state

    for mapping in (state.registry, state.builtin_registry, state.extension_registry):
        with pytest.raises(TypeError):
            cast(dict[str, object], mapping)["injected"] = _RecordingTool("injected")


def test_reload_during_a_call_is_not_reported_as_added_tools(
    tmp_path: Path,
) -> None:
    """A generation swap mid-call must not masquerade as tool widening."""

    reloading = _RecordingTool("reloading")
    capabilities = _capabilities(
        tmp_path,
        builtins=(_RecordingTool("builtin"),),
        extensions=(reloading,),
    )
    assert capabilities.set_active_tools(("reloading",)) is True

    def _publish_new_generation(_request: ToolRequest, _context: ToolContext) -> None:
        capabilities.publish(
            capabilities.prepare_extensions(
                {
                    "reloading": reloading,
                    "arrived_on_reload": _RecordingTool("arrived_on_reload"),
                }
            )
        )

    reloading.invoke_hook = _publish_new_generation

    outcome = capabilities.execute(_call("reloading"))

    assert outcome.result.added_tool_names == ()


def test_active_tool_widening_during_a_call_is_still_reported(
    tmp_path: Path,
) -> None:
    """The generation check must not suppress the real widening case."""

    widening = _RecordingTool("widening")
    capabilities = _capabilities(
        tmp_path,
        builtins=(_RecordingTool("builtin"),),
        extensions=(widening,),
    )
    assert capabilities.set_active_tools(("widening",)) is True

    def _widen(_request: ToolRequest, _context: ToolContext) -> None:
        assert capabilities.set_active_tools(("widening", "builtin")) is True

    widening.invoke_hook = _widen

    outcome = capabilities.execute(_call("widening"))

    assert outcome.result.added_tool_names == ("builtin",)


def test_publication_does_not_overwrite_a_selection_accepted_while_preparing(
    tmp_path: Path,
) -> None:
    """A reload must not restore a selection sampled before it was superseded."""

    capabilities = _capabilities(
        tmp_path,
        builtins=(_RecordingTool("builtin"),),
        extensions=(_RecordingTool("old_extension"),),
    )
    assert capabilities.set_active_tools(("builtin", "old_extension")) is True

    candidate = capabilities.prepare_extensions(
        {"new_extension": _RecordingTool("new_extension")}
    )
    # An extension handler narrows the selection while the reload is prepared.
    assert capabilities.set_active_tools(("builtin",)) is True

    capabilities.publish(candidate)

    assert capabilities.state.active_tool_names == frozenset({"builtin"})
    assert _names(capabilities) == ("builtin",)


def test_configured_filters_still_re_derive_visibility_on_publication(
    tmp_path: Path,
) -> None:
    """The live-rebind must not defeat an explicitly configured filter."""

    capabilities = _capabilities(
        tmp_path,
        builtins=(_RecordingTool("builtin"),),
        extensions=(_RecordingTool("old_extension"),),
        options=ToolFilterOptions(
            allow=("builtin", "old_extension", "new_extension"),
            exclude=("builtin",),
        ),
    )

    candidate = capabilities.prepare_extensions(
        {"new_extension": _RecordingTool("new_extension")}
    )
    capabilities.publish(candidate)

    assert _names(capabilities) == ("new_extension",)


def test_capability_state_normalizes_registries_built_from_plain_dicts() -> None:
    """The immutability invariant belongs to the type, not just to `build`."""

    from pipy_harness.native.tool_capabilities import ToolCapabilityState
    from pipy_harness.native.agent.tools import ToolExecutor

    registry: dict[str, ToolPort] = {"tool": _RecordingTool("tool")}
    state = ToolCapabilityState(
        builtin_registry=dict(registry),
        extension_registry={},
        registry=dict(registry),
        executor=ToolExecutor({}, cancel_join_timeout_seconds=1.0),
        filter_options=ToolFilterOptions.empty(),
        active_tool_names=None,
    )

    for mapping in (state.registry, state.builtin_registry, state.extension_registry):
        with pytest.raises(TypeError):
            cast(dict[str, object], mapping)["injected"] = _RecordingTool("injected")
