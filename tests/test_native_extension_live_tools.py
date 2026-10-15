"""Tools registered after the extensions loaded (goal-check finding E1;
Pi ``registerTool`` and ``refreshTools``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipy_harness.extensions import ExtensionTool, ToolResult, lines_component
from pipy_harness.native.extension_types import RegisteredTool
from pipy_harness.native.extensions.command_context import ExtensionCapabilityError
from pipy_harness.native.extensions.live_tools import (
    LiveToolRegistry,
    LiveToolRendererResolvers,
    LiveToolRenderers,
)
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    NativeToolCapabilitySnapshot,
    ToolFilterOptions,
)
from pipy_harness.native.tools import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRequest,
)


def _registered(
    name: str, extension: str = "mine", *, exposure: str = "direct", **fields: object
) -> RegisteredTool:
    return RegisteredTool(
        ExtensionTool(
            name=name,
            description=name,
            input_schema={"type": "object"},
            handler=lambda ctx, params: ToolResult(content=name),
            exposure=exposure,  # type: ignore[arg-type]
            **fields,  # type: ignore[arg-type]
        ),
        extension,
    )


def test_the_registry_keeps_owners_and_the_latest_registration() -> None:
    changes: list[int] = []
    registry = LiveToolRegistry(
        [_registered("a"), _registered("b", "other")],
        renderer_groups=[("other", ()), ("mine", ())],
    )
    registry.set_change_listener(lambda: changes.append(1))

    registry.register(_registered("a", exposure="hidden"))
    registry.register(_registered("c"))
    # `other` loaded first: its name stays its own, silently.
    registry.register(_registered("b"))

    assert changes == [1, 1]
    assert [(r.tool.name, r.tool.exposure) for r in registry.tools()] == [
        ("a", "hidden"),
        ("b", "direct"),
        ("c", "direct"),
    ]
    assert [r.tool.name for r in registry.registrations()] == ["a", "c"]


def test_the_renderers_follow_the_latest_registration() -> None:
    draw = lambda ctx: lines_component(["x"])  # noqa: E731
    registry = LiveToolRegistry([_registered("a", render_call=draw)])
    renderers = LiveToolRenderers(registry)
    assert list(renderers) == ["a"]

    registry.register(_registered("a"))
    registry.register(_registered("b", render_result=draw))

    # A tool registered again without renderers stays, without them (Pi
    # `getToolDefinition` returns the definition whatever renderers it has).
    a, b = (item.tool for item in registry.tools())
    assert dict(renderers) == {"a": a, "b": b}
    assert renderers["a"].render_call is None
    assert renderers["a"].render_result is None


class _Port:
    def __init__(self, name: str, exposure: str = "direct") -> None:
        self.definition = ToolDefinition(
            name,
            name,
            {"type": "object"},
            exposure=exposure,  # type: ignore[arg-type]
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        raise AssertionError("not called")


def _capabilities(tmp_path: Path, **options: object) -> NativeToolCapabilities:
    capabilities = NativeToolCapabilities(
        {},
        {"a": _Port("a")},
        workspace_root=tmp_path,
        filter_options=ToolFilterOptions(**options)  # type: ignore[arg-type]
        if options
        else ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.1,
    )
    assert capabilities.set_active_tools(["a"])
    return capabilities


def _declared(capabilities: NativeToolCapabilities) -> list[str]:
    snapshot = NativeToolCapabilitySnapshot(capabilities, capabilities.state)
    return [definition.name for definition in snapshot.definitions()]


def test_publishing_extension_tools_withdraws_and_activates(tmp_path: Path) -> None:
    capabilities = _capabilities(tmp_path)
    generation = capabilities.state.extension_generation

    assert capabilities.publish_extension_tools(
        lambda: {"a": _Port("a", "hidden"), "b": _Port("b")},
        extension_generation=generation,
    )
    # A hidden tool is never declared; a new tool is activated by being
    # registered (Pi `_refreshToolRegistry`); the generation stays.
    assert _declared(capabilities) == ["b"]
    assert capabilities.state.extension_generation is generation

    assert capabilities.publish_extension_tools(
        lambda: {"a": _Port("a"), "b": _Port("b")}, extension_generation=generation
    )
    # A tool that changed from hidden to direct is activated like a new one.
    assert _declared(capabilities) == ["a", "b"]


def test_a_tool_registered_again_as_hidden_leaves_the_selection(
    tmp_path: Path,
) -> None:
    # Pi `_refreshToolRegistry` applies the loadout, which drops a hidden
    # tool: a `deferred` tool that was named is not declared again when it
    # comes back, until something names it once more.
    capabilities = _capabilities(tmp_path)
    generation = capabilities.state.extension_generation
    assert capabilities.publish_extension_tools(
        lambda: {"a": _Port("a"), "b": _Port("b", "deferred")},
        extension_generation=generation,
    )
    assert capabilities.set_active_tools(["a", "b"])
    assert _declared(capabilities) == ["a", "b"]

    assert capabilities.publish_extension_tools(
        lambda: {"a": _Port("a"), "b": _Port("b", "hidden")},
        extension_generation=generation,
    )
    assert capabilities.state.active_tool_names == frozenset({"a"})

    assert capabilities.publish_extension_tools(
        lambda: {"a": _Port("a"), "b": _Port("b", "deferred")},
        extension_generation=generation,
    )
    assert _declared(capabilities) == ["a"]


def test_another_generation_publishes_nothing(tmp_path: Path) -> None:
    capabilities = _capabilities(tmp_path)
    state = capabilities.state

    assert not capabilities.publish_extension_tools(
        lambda: {"b": _Port("b")}, extension_generation=object()
    )
    assert capabilities.state is state


def test_an_allowlist_activates_only_what_it_names(tmp_path: Path) -> None:
    capabilities = _capabilities(tmp_path, allow=("a", "b"))
    generation = capabilities.state.extension_generation

    capabilities.publish_extension_tools(
        lambda: {"a": _Port("a"), "b": _Port("b"), "c": _Port("c")},
        extension_generation=generation,
    )

    assert _declared(capabilities) == ["a", "b"]


class _Flipping(LiveToolRenderers):
    """A view whose registry changes between any two readings."""

    def __init__(self) -> None:
        self.readings = 0

    def _current(self) -> dict[str, ExtensionTool]:  # type: ignore[override]
        self.readings += 1
        tool = _registered("a").tool
        return {"a": tool} if self.readings % 2 else {}


def test_renderers_are_read_once_per_name() -> None:
    # A tool registered again without renderers between two readings must
    # not break the provider turn (no KeyError between `in` and `[]`).
    from pipy_harness.native.repl.execution_projections import renderers_of

    view = _Flipping()

    chosen = renderers_of(view, ["a", "b"])

    assert list(chosen) == ["a"]
    assert view.readings == 2


def test_an_earlier_loaded_extension_takes_a_tool_name_over() -> None:
    # Pi `getAllRegisteredTools` walks the extensions in load order, so the
    # earlier one's registration wins whenever it comes.
    registry = LiveToolRegistry(
        [_registered("shared", "later")],
        renderer_groups=[("earlier", ()), ("later", ())],
    )

    registry.register(_registered("shared", "earlier", exposure="hidden"))
    registry.register(_registered("shared", "later"))
    registry.register(_registered("new", "unknown"))
    registry.register(_registered("new", "later"))

    assert [(r.tool.name, r.extension, r.tool.exposure) for r in registry.tools()] == [
        ("shared", "earlier", "hidden"),
        ("new", "later", "direct"),
    ]


def test_resolvers_registered_late_join_their_extension_in_load_order() -> None:
    def resolver(label: str):  # type: ignore[no-untyped-def]
        def resolve(name, next):  # type: ignore[no-untyped-def]
            return label

        resolve.label = label  # type: ignore[attr-defined]
        return resolve

    first, second = resolver("first"), resolver("second")
    registry = LiveToolRegistry(
        renderer_groups=[("one", (first,)), ("two", (second,)), ("three", ())]
    )
    view = LiveToolRendererResolvers(registry)

    registry.register_tool_renderer("three", resolver("three"))
    registry.register_tool_renderer("one", resolver("one-late"))
    registry.register_tool_renderer("unknown", resolver("unknown"))

    assert [r.label for r in view] == [  # type: ignore[attr-defined]
        "first",
        "one-late",
        "second",
        "three",
        "unknown",
    ]
    registry.close()
    with pytest.raises(ExtensionCapabilityError):
        registry.register_tool_renderer("one", first)


def test_another_session_keeps_only_what_the_extensions_registered_loading() -> None:
    # INT4: the extensions stay loaded across `/new`, `/resume` and `/fork`,
    # where Pi's new runtime loads them again. The reset forgets what was
    # registered after loading, so a `session_start` handler that registers
    # a resolver again leaves one, not one per session.
    def resolver(label: str):  # type: ignore[no-untyped-def]
        def resolve(name, next):  # type: ignore[no-untyped-def]
            return label

        resolve.label = label  # type: ignore[attr-defined]
        return resolve

    changes: list[int] = []
    loaded = resolver("loaded")
    registry = LiveToolRegistry(
        [_registered("shared", "later")],
        renderer_groups=[("earlier", (loaded,)), ("later", ())],
    )
    registry.set_change_listener(lambda: changes.append(1))
    view = LiveToolRendererResolvers(registry)
    registry.register_tool_renderer("later", resolver("session"))
    registry.register(_registered("shared", "earlier", exposure="hidden"))
    registry.register(_registered("late"))
    assert len(changes) == 2

    registry.reset()

    assert len(changes) == 3
    assert [r.label for r in view] == ["loaded"]  # type: ignore[attr-defined]
    assert registry.registrations() == ()
    assert [(r.tool.name, r.extension) for r in registry.tools()] == [
        ("shared", "later")
    ]
    # The new session's handler registers its resolver once more.
    registry.register_tool_renderer("later", resolver("session"))
    assert [r.label for r in view] == ["loaded", "session"]  # type: ignore[attr-defined]
    # Without a tool registered after loading nothing is published again.
    registry.reset()
    assert len(changes) == 3
    assert [r.label for r in view] == ["loaded"]  # type: ignore[attr-defined]
    # A closed registry (`/reload`) stays as it is.
    registry.register_tool_renderer("later", resolver("session"))
    registry.close()
    registry.reset()
    assert len(view) == 2
