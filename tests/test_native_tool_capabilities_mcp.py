"""The MCP tools of a capability value (MCP parity plan S17, section 4.4).

A session's MCP tools are a third registry of the capability value. It is
replaced as a whole while the session runs, without a new extension
generation. Rows SES-02 and SES-03 (a server's tools become available, change
and go away), EXP-05 (a ``direct`` MCP tool is declared and callable from
scripts) and the MCP part of EXT-03 (``get_all_tools``) at the capability
level; the session level is in ``test_native_mcp_session.py``.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from test_native_tool_filter_patterns import (
    _callable,
    _declared,
    _registered,
    _Tool,
)

from pipy_harness.native.agent import AgentToolCall, ProductContent
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    ToolCapabilityState,
    ToolFilterOptions,
)
from pipy_harness.native.tools import ToolAnnotations, ToolNamespace, ToolPort
from pipy_harness.native.tools.base import ToolDefinition, ToolExposure
from pipy_harness.native.tools.registry import ProductionToolRegistry


def _session(
    tmp_path: Path,
    options: ToolFilterOptions | None = None,
    *,
    extensions: dict[str, ToolPort] | None = None,
    production: bool = False,
) -> NativeToolCapabilities:
    builtins: dict[str, ToolPort] = {"read": _Tool("read"), "bash": _Tool("bash")}
    tools = NativeToolCapabilities(
        ProductionToolRegistry(builtins) if production else builtins,
        {},
        workspace_root=tmp_path,
        filter_options=options or ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.1,
    )
    tools.publish(tools.prepare_extensions(extensions or {}))
    return tools


def _mcp(**exposures: ToolExposure) -> dict[str, ToolPort]:
    return {
        f"mcp__docs__{name}": _Tool(f"mcp__docs__{name}", exposure)
        for name, exposure in exposures.items()
    }


def _run(tools: NativeToolCapabilities, name: str) -> str:
    outcome = tools.execute(
        AgentToolCall(
            provider_correlation_id="call-1",
            tool_name=name,
            arguments_json=ProductContent("{}"),
        )
    )
    return outcome.result.content.value


def test_mcp_tools_follow_the_builtin_and_extension_tools(tmp_path: Path) -> None:
    tools = _session(tmp_path, extensions={"lookup": _Tool("lookup")})

    assert tools.publish_mcp_tools(_mcp(search="direct", fetch="codemode")) == ()

    assert tools.registered_names == (
        "read",
        "bash",
        "lookup",
        "mcp__docs__search",
        "mcp__docs__fetch",
    )
    assert tuple(tools.state.mcp_registry) == ("mcp__docs__search", "mcp__docs__fetch")
    assert _run(tools, "mcp__docs__search") == "ran mcp__docs__search"


def test_direct_mcp_tool_is_declared_and_callable_and_others_only_callable(
    tmp_path: Path,
) -> None:
    # Pi: "declares directly exposed MCP tools to the model" (EXP-05) and
    # "exposes codemode-only MCP tools through codemode and hides them from
    # the model".
    tools = _session(tmp_path)

    tools.publish_mcp_tools(
        _mcp(search="direct", fetch="codemode", find="deferred", gone="hidden")
    )

    assert _declared(tools) == ("read", "bash", "mcp__docs__search")
    assert _callable(tools) == {
        "read",
        "bash",
        "mcp__docs__search",
        "mcp__docs__fetch",
        "mcp__docs__find",
    }


def test_a_new_direct_mcp_tool_joins_a_selection_that_was_made_before(
    tmp_path: Path,
) -> None:
    tools = _session(tmp_path)
    assert tools.set_active_tools(("read",))

    tools.publish_mcp_tools(_mcp(search="direct", fetch="codemode"))

    assert _declared(tools) == ("read", "mcp__docs__search")
    assert tools.state.active_selection_customized is True


def test_republishing_does_not_reactivate_a_tool_that_was_deactivated(
    tmp_path: Path,
) -> None:
    tools = _session(tmp_path)
    tools.publish_mcp_tools(_mcp(search="direct"))
    assert tools.set_active_tools(("read",))

    tools.publish_mcp_tools(_mcp(search="direct", fetch="direct"))

    assert _declared(tools) == ("read", "mcp__docs__fetch")


def test_indirect_tools_leave_a_selection_and_direct_ones_stay(
    tmp_path: Path,
) -> None:
    # Pi `setExposure`: tools no longer exposed directly leave the declared set.
    tools = _session(tmp_path)
    tools.publish_mcp_tools(_mcp(search="direct", fetch="direct", find="deferred"))
    tools.activate_tools(["mcp__docs__find"])
    assert _declared(tools) == (
        "read",
        "bash",
        "mcp__docs__search",
        "mcp__docs__fetch",
        "mcp__docs__find",
    )
    tools.publish_mcp_tools(_mcp(search="direct", fetch="codemode", find="deferred"))
    assert "mcp__docs__fetch" in _declared(tools)

    tools.deactivate_indirect_mcp_tools(
        ["mcp__docs__search", "mcp__docs__fetch", "mcp__docs__gone", "read"]
    )

    assert _declared(tools) == ("read", "bash", "mcp__docs__search", "mcp__docs__find")
    assert tools.state.active_selection_customized is False
    # The tool is still callable from scripts.
    assert "mcp__docs__fetch" in tools.callable_snapshot().callable_names()


def test_deactivating_leaves_a_tool_that_has_the_name_of_an_mcp_tool(
    tmp_path: Path,
) -> None:
    """An extension tool with the name an MCP tool would get is not that MCP
    tool, so a change of the server's exposure leaves it active."""

    extension = _Tool("mcp__docs__search", "deferred")
    tools = _session(tmp_path, extensions={"mcp__docs__search": extension})
    assert tools.publish_mcp_tools(_mcp(search="codemode")) == ("mcp__docs__search",)
    tools.activate_tools(["mcp__docs__search"])
    assert "mcp__docs__search" in _declared(tools)

    tools.deactivate_indirect_mcp_tools(["mcp__docs__search"])

    assert "mcp__docs__search" in _declared(tools)
    assert tools.state.registry["mcp__docs__search"] is extension


def test_deactivating_without_a_selection_changes_nothing(tmp_path: Path) -> None:
    tools = _session(tmp_path)
    tools.publish_mcp_tools(_mcp(search="direct", fetch="codemode"))
    before = tools.state

    tools.deactivate_indirect_mcp_tools(["mcp__docs__search", "mcp__docs__fetch"])

    assert tools.state is before
    assert before.active_tool_names is None


def test_a_withdrawn_tool_is_gone_and_active_again_when_it_comes_back(
    tmp_path: Path,
) -> None:
    # SES-03, for a tool that `tool_search` or an extension had activated.
    tools = _session(tmp_path)
    tools.publish_mcp_tools(_mcp(search="direct", find="deferred"))
    assert tools.load_tools(["mcp__docs__find"]) == ("mcp__docs__find",)
    assert _declared(tools) == ("read", "bash", "mcp__docs__search", "mcp__docs__find")

    tools.publish_mcp_tools(_mcp(search="direct"))

    assert _declared(tools) == ("read", "bash", "mcp__docs__search")
    assert "mcp__docs__find" not in tools.registered_names
    assert "mcp__docs__find" not in _callable(tools)
    assert _run(tools, "mcp__docs__find") == "unknown tool: mcp__docs__find"

    tools.publish_mcp_tools(_mcp(search="direct", find="deferred"))

    assert _declared(tools) == ("read", "bash", "mcp__docs__search", "mcp__docs__find")


def test_a_tool_whose_exposure_becomes_direct_is_activated(tmp_path: Path) -> None:
    tools = _session(tmp_path)
    assert tools.set_active_tools(("read",))
    tools.publish_mcp_tools(_mcp(search="codemode"))
    assert _declared(tools) == ("read",)

    tools.publish_mcp_tools(_mcp(search="direct"))

    assert _declared(tools) == ("read", "mcp__docs__search")


def test_mcp_tool_never_replaces_a_builtin_or_extension_tool(tmp_path: Path) -> None:
    extension = _Tool("mcp__docs__search", description="The extension's tool.")
    tools = _session(tmp_path, extensions={"mcp__docs__search": extension})
    read = _Tool("read", description="A server's read.")

    left_out = tools.publish_mcp_tools(
        {
            "mcp__docs__search": _Tool("mcp__docs__search"),
            "read": read,
            "mcp__docs__fetch": _Tool("mcp__docs__fetch"),
        }
    )

    assert left_out == ("mcp__docs__search", "read")
    assert tools.state.registry["mcp__docs__search"] is extension
    assert tools.state.registry["read"] is not read
    assert tools.registered_names == (
        "read",
        "bash",
        "mcp__docs__search",
        "mcp__docs__fetch",
    )


def test_a_left_out_mcp_tool_does_not_activate_the_tool_that_has_its_name(
    tmp_path: Path,
) -> None:
    """Only the MCP tools that are registered take part in activation."""

    for options in (
        ToolFilterOptions.empty(),
        ToolFilterOptions(allow=("read", "mcp__docs__*")),
    ):
        extension = _Tool("mcp__docs__search")
        tools = _session(tmp_path, options, extensions={"mcp__docs__search": extension})
        assert tools.set_active_tools(("read",))

        left_out = tools.publish_mcp_tools(_mcp(search="direct", fetch="direct"))

        assert left_out == ("mcp__docs__search",)
        assert _declared(tools) == ("read", "mcp__docs__fetch")


def test_a_reload_does_not_activate_a_tool_by_a_left_out_mcp_tools_exposure(
    tmp_path: Path,
) -> None:
    extensions: dict[str, ToolPort] = {
        "mcp__docs__search": _Tool("mcp__docs__search", "codemode")
    }
    tools = _session(
        tmp_path,
        ToolFilterOptions(exclude=("bash",), modifiers=("+read",)),
        extensions=extensions,
    )
    tools.publish_mcp_tools(_mcp(search="direct", fetch="direct"))
    assert _declared(tools) == ("read", "mcp__docs__fetch")

    tools.publish(tools.prepare_extensions(extensions))

    assert _declared(tools) == ("read", "mcp__docs__fetch")


def test_an_allowlist_of_plain_names_keeps_mcp_tools_for_scripts_only(
    tmp_path: Path,
) -> None:
    # Pi: "keeps MCP tools when --tools names no MCP tool", with tools that
    # register after the session started.
    tools = _session(tmp_path, ToolFilterOptions(allow=("read",)))

    tools.publish_mcp_tools(_mcp(search="direct", fetch="codemode"))

    assert _declared(tools) == ("read",)
    assert _registered(tools) == ("read", "mcp__docs__search", "mcp__docs__fetch")
    assert _callable(tools) == {"read", "mcp__docs__fetch"}


def test_an_allowlist_that_names_mcp_tools_activates_the_declarable_ones(
    tmp_path: Path,
) -> None:
    # Pi: "filters MCP tools by the mcp__ entries of --tools".
    tools = _session(tmp_path, ToolFilterOptions(allow=("read", "mcp__docs__s*")))

    tools.publish_mcp_tools(_mcp(search="direct", scan="codemode", fetch="direct"))

    assert _declared(tools) == ("read", "mcp__docs__search")
    assert _registered(tools) == ("read", "mcp__docs__search", "mcp__docs__scan")
    assert _callable(tools) == {"read", "mcp__docs__search", "mcp__docs__scan"}


def test_exclusion_patterns_remove_mcp_tools_that_register_later(
    tmp_path: Path,
) -> None:
    # Pi: "removes MCP tools matching --exclude-tools patterns".
    tools = _session(tmp_path, ToolFilterOptions(exclude=("mcp__docs__f*",)))

    tools.publish_mcp_tools(_mcp(search="direct", fetch="direct", find="codemode"))

    assert _declared(tools) == ("read", "bash", "mcp__docs__search")
    assert _callable(tools) == {"read", "bash", "mcp__docs__search"}


def test_no_tools_removes_mcp_tools_too(tmp_path: Path) -> None:
    # Pi: "removes MCP tools with --no-tools".
    tools = _session(tmp_path, ToolFilterOptions(no_tools=True))

    tools.publish_mcp_tools(_mcp(search="direct", fetch="codemode"))

    assert _declared(tools) == ()
    assert _registered(tools) == ()
    assert _callable(tools) == frozenset()


def test_an_allowlist_after_no_tools_declares_tool_search(tmp_path: Path) -> None:
    # Pi ``createAgentSession``: ``--no-tools --tools tool_search`` and
    # ``--no-builtin-tools --tools tool_search`` are the allowlist
    # ``tool_search`` (the command line folds the flags in), which keeps the
    # MCP tools registered for it.
    tools = _session(
        tmp_path, ToolFilterOptions(allow=("tool_search",)), production=True
    )
    tools.publish_mcp_tools(_mcp(search="direct", find="deferred"))

    assert _declared(tools) == ("tool_search",)
    assert [definition.name for definition in tools.loadable_tools()] == [
        "mcp__docs__find"
    ]


def test_no_builtin_tools_keeps_builtins_registered_and_inactive(
    tmp_path: Path,
) -> None:
    # Pi ``noTools: "builtin"`` empties the default selection only: the
    # built-in tools stay registered (an extension may select one), extension
    # and MCP tools are activated by registering, and ``tool_search``, an
    # extension tool in Pi, stays available for MCP to activate.
    tools = _session(
        tmp_path,
        ToolFilterOptions(no_builtin_tools=True),
        extensions={"lookup": _Tool("lookup")},
        production=True,
    )
    tools.publish_mcp_tools(_mcp(search="direct", find="deferred"))

    assert _declared(tools) == ("lookup", "mcp__docs__search")
    assert "read" in _registered(tools)
    tools.activate_tools(("tool_search",))
    assert _declared(tools) == ("tool_search", "lookup", "mcp__docs__search")
    assert tools.set_active_tools(("read", "lookup"))
    assert _declared(tools) == ("read", "lookup")


def test_modifiers_after_no_builtin_tools_start_from_nothing(tmp_path: Path) -> None:
    # Pi: ``--no-builtin-tools --tools +read`` activates read and keeps the
    # extension tools; the defaultTools setting is not read.
    tools = _session(
        tmp_path,
        ToolFilterOptions(no_builtin_tools=True, modifiers=("+read",)),
        extensions={"lookup": _Tool("lookup")},
    )

    assert _declared(tools) == ("read", "lookup")
    tools.publish(tools.prepare_extensions({"lookup": _Tool("lookup")}))
    assert _declared(tools) == ("read", "lookup")


@pytest.mark.parametrize(
    "options",
    [
        ToolFilterOptions.empty(),
        ToolFilterOptions(exclude=("bash",)),
        ToolFilterOptions(allow=("read", "mcp__docs__*")),
        ToolFilterOptions(modifiers=("-bash",)),
    ],
)
def test_a_reload_keeps_the_mcp_tools(
    tmp_path: Path, options: ToolFilterOptions
) -> None:
    tools = _session(tmp_path, options)
    mcp = _mcp(search="direct", fetch="codemode")
    tools.publish_mcp_tools(mcp)
    declared = _declared(tools)
    assert "mcp__docs__search" in declared

    candidate = tools.prepare_extensions({"lookup": _Tool("lookup")})
    tools.publish(candidate)

    assert tools.state.mcp_registry == mcp
    assert tools.state.registry["mcp__docs__fetch"] is mcp["mcp__docs__fetch"]
    assert "lookup" in tools.registered_names
    assert [name for name in _declared(tools) if name != "lookup"] == list(declared)
    assert _run(tools, "mcp__docs__search") == "ran mcp__docs__search"


def test_a_reload_publishes_the_mcp_tools_that_are_live_at_the_swap(
    tmp_path: Path,
) -> None:
    """Tools a server announced while a reload was prepared are not lost."""

    tools = _session(tmp_path)
    tools.publish_mcp_tools(_mcp(search="direct"))
    candidate = tools.prepare_extensions({"lookup": _Tool("lookup")})
    later = _mcp(fetch="direct", find="codemode")

    tools.publish_mcp_tools(later)
    tools.publish(candidate)

    assert tools.state.mcp_registry == later
    assert tools.registered_names == (
        "read",
        "bash",
        "lookup",
        "mcp__docs__fetch",
        "mcp__docs__find",
    )
    assert _declared(tools) == ("read", "bash", "lookup", "mcp__docs__fetch")
    assert _run(tools, "mcp__docs__fetch") == "ran mcp__docs__fetch"
    assert tools.state.extension_generation is candidate.extension_generation


def test_other_mcp_tools_keep_the_extension_generation_of_the_value(
    tmp_path: Path,
) -> None:
    """A provider turn binds to the generation that was published, also when
    a server's tools changed since: the registry and its executor are new,
    the extension generation is not."""

    tools = _session(tmp_path)
    published = tools.state

    tools.publish_mcp_tools(_mcp(search="direct"))

    live = tools.state
    assert live.executor is not published.executor
    assert live.extension_generation is published.extension_generation
    snapshot = tools.snapshot_for_projection(published)
    assert snapshot.state is live
    assert [definition.name for definition in snapshot.definitions()] == [
        "read",
        "bash",
        "mcp__docs__search",
    ]

    reloaded = tools.prepare_extensions({})
    assert reloaded.extension_generation is not published.extension_generation
    tools.publish(reloaded)
    with pytest.raises(RuntimeError, match="generation is incoherent"):
        tools.snapshot_for_projection(published)


def test_a_pinned_snapshot_is_unchanged_by_later_mcp_tools(tmp_path: Path) -> None:
    tools = _session(tmp_path)
    tools.publish_mcp_tools(_mcp(search="direct", fetch="codemode"))
    pinned = tools.callable_snapshot()

    tools.publish_mcp_tools({})

    assert "mcp__docs__fetch" in pinned.callable_names()
    assert [definition.name for definition in pinned.definitions()] == [
        "read",
        "bash",
        "mcp__docs__search",
    ]
    assert tools.registered_names == ("read", "bash")
    assert _callable(tools) == {"read", "bash"}


class _Labeled(_Tool):
    """A server tool's port: it has the title of its calls."""

    def __init__(self, name: str, label: str) -> None:
        super().__init__(name)
        self.label = label


def test_a_snapshot_has_the_titles_of_its_mcp_server_tools(tmp_path: Path) -> None:
    # RND-01: a registered tool's calls are titled with its exact names.
    tools = _session(tmp_path)
    tools.publish_mcp_tools(
        {
            "mcp__my_docs__search": _Labeled("mcp__my_docs__search", "my-docs/search"),
            # The resource tools have no title of their own.
            "list_mcp_resources": _Tool("list_mcp_resources"),
            # A built-in tool has the name: the MCP tool is left out.
            "read": _Labeled("read", "docs/read"),
        }
    )
    pinned = tools.callable_snapshot()

    tools.publish_mcp_tools({})

    assert pinned.mcp_tool_labels() == {"mcp__my_docs__search": "my-docs/search"}
    assert tools.callable_snapshot().mcp_tool_labels() == {}


def test_published_mcp_registry_is_a_read_only_copy(tmp_path: Path) -> None:
    tools = _session(tmp_path)
    mcp = _mcp(search="direct")
    tools.publish_mcp_tools(mcp)

    mcp["mcp__docs__other"] = _Tool("mcp__docs__other")

    assert tuple(tools.state.mcp_registry) == ("mcp__docs__search",)
    with pytest.raises(TypeError):
        tools.state.mcp_registry["x"] = _Tool("x")  # type: ignore[index]


def test_get_all_tools_reports_the_metadata_of_mcp_tools(tmp_path: Path) -> None:
    # EXT-03, MCP part.
    class _Described(_Tool):
        def __init__(self) -> None:
            self._definition = ToolDefinition(
                name="mcp__docs__search",
                description="Search the docs.",
                input_schema={"type": "object", "properties": {}},
                exposure="codemode",
                namespace=ToolNamespace(
                    name="mcp__docs", aliases=("docs",), instructions="Be brief."
                ),
                annotations=ToolAnnotations(read_only_hint=True),
                schema_mode="foreign",
            )

    tools = _session(tmp_path)
    tools.publish_mcp_tools({"mcp__docs__search": _Described()})

    info = next(tool for tool in tools.all_tools() if tool.name.startswith("mcp__"))
    assert (info.name, info.exposure) == ("mcp__docs__search", "codemode")
    assert info.namespace == ToolNamespace(
        name="mcp__docs", aliases=("docs",), instructions="Be brief."
    )
    assert info.annotations == ToolAnnotations(read_only_hint=True)
    assert info.reaches_tools is None


def test_tool_search_loads_mcp_tools_that_registered_later(tmp_path: Path) -> None:
    tools = _session(tmp_path, production=True)
    tools.publish_mcp_tools(_mcp(search="direct", fetch="codemode", find="deferred"))

    assert [definition.name for definition in tools.loadable_tools()] == [
        "mcp__docs__fetch",
        "mcp__docs__find",
    ]
    assert tools.load_tools(["mcp__docs__find"]) == ("mcp__docs__find",)
    assert "mcp__docs__find" in _declared(tools)


def test_concurrent_publications_and_selections_lose_nothing(tmp_path: Path) -> None:
    """Every writer builds on the live value inside the state lock."""

    tools = _session(tmp_path)
    tools.publish_mcp_tools(_mcp(search="direct"))
    registries = [
        {**_mcp(search="direct"), f"mcp__docs__t{index}": _Tool(f"mcp__docs__t{index}")}
        for index in range(20)
    ]
    start = threading.Barrier(3)

    def publish() -> None:
        start.wait()
        for registry in registries:
            tools.publish_mcp_tools(registry)

    def select() -> None:
        start.wait()
        for _ in registries:
            tools.set_active_tools(("read",))

    def reload() -> None:
        start.wait()
        for _ in registries:
            tools.publish(tools.prepare_extensions({}))

    threads = [threading.Thread(target=target) for target in (publish, select, reload)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    state: ToolCapabilityState = tools.state
    assert state.mcp_registry == registries[-1]
    assert tuple(state.registry) == ("read", "bash", *registries[-1])
    assert _run(tools, "mcp__docs__t19") == "ran mcp__docs__t19"


# -- S22: tools restored by a reload or a transcript (TSR-10) ---------------


def _loaded(tmp_path: Path, options: ToolFilterOptions | None = None):
    """A session whose `deferred` tool `find` was loaded by `tool_search`."""

    tools = _session(tmp_path, options)
    tools.publish_mcp_tools(_mcp(search="direct", find="deferred", fetch="deferred"))
    assert tools.load_tools(("mcp__docs__find",)) == ("mcp__docs__find",)
    return tools


def _reload(tools: NativeToolCapabilities) -> None:
    """What an accepted `/reload` does to the capability value."""

    tools.publish(tools.prepare_extensions({}))
    tools.restart_mcp_tools()


def test_a_reload_declares_loaded_tools_again_once_their_server_connects(
    tmp_path: Path,
) -> None:
    # Pi: "declares tools tool_search loaded again after /reload".
    tools = _loaded(tmp_path)
    assert _declared(tools) == ("read", "bash", "mcp__docs__search", "mcp__docs__find")

    _reload(tools)

    assert _registered(tools) == ("read", "bash")
    assert _declared(tools) == ("read", "bash")
    assert tools.state.pending_tool_names == {"mcp__docs__search", "mcp__docs__find"}
    tools.publish_mcp_tools(_mcp(search="direct", find="deferred", fetch="deferred"))
    assert _declared(tools) == ("read", "bash", "mcp__docs__search", "mcp__docs__find")
    assert tools.state.pending_tool_names == frozenset()


def test_a_restored_tool_that_registers_after_the_run_started_is_not_activated(
    tmp_path: Path,
) -> None:
    # Pi: "does not activate restored tools that register after the next
    # prompt starts". A `direct` tool is activated by registering, as ever.
    tools = _loaded(tmp_path)
    _reload(tools)

    tools.drop_pending_tools()
    tools.publish_mcp_tools(_mcp(search="direct", find="deferred", fetch="deferred"))

    assert tools.state.pending_tool_names == frozenset()
    assert _declared(tools) == ("read", "bash", "mcp__docs__search")
    assert "mcp__docs__find" in _callable(tools)


def test_pending_tools_arrive_one_server_at_a_time(tmp_path: Path) -> None:
    tools = _loaded(tmp_path)
    _reload(tools)

    tools.publish_mcp_tools(_mcp(search="direct"))
    assert tools.state.pending_tool_names == {"mcp__docs__find"}
    tools.publish_mcp_tools(_mcp(search="direct", find="deferred"))

    assert _declared(tools) == ("read", "bash", "mcp__docs__search", "mcp__docs__find")


def test_a_second_reload_keeps_the_pending_tools(tmp_path: Path) -> None:
    tools = _loaded(tmp_path)
    _reload(tools)

    _reload(tools)

    assert tools.state.pending_tool_names == {"mcp__docs__search", "mcp__docs__find"}
    tools.publish_mcp_tools(_mcp(find="deferred"))
    assert _declared(tools) == ("read", "bash", "mcp__docs__find")


def test_a_reload_with_a_tool_filter_keeps_what_tool_search_loaded(
    tmp_path: Path,
) -> None:
    # The filter derives the selection again on every reload, which drops a
    # loaded tool; the names are read before that.
    tools = _loaded(tmp_path, ToolFilterOptions(exclude=("bash", "mcp__docs__fetch")))
    assert _declared(tools) == ("read", "mcp__docs__search", "mcp__docs__find")

    _reload(tools)
    tools.publish_mcp_tools(_mcp(search="direct", find="deferred", fetch="deferred"))

    assert _declared(tools) == ("read", "mcp__docs__search", "mcp__docs__find")


def test_a_reload_without_a_selection_leaves_nothing_pending(tmp_path: Path) -> None:
    # No selection means "as registration left it": only tools registration
    # activates were declared, and registering activates them again.
    tools = NativeToolCapabilities(
        {"read": _Tool("read")},
        {},
        workspace_root=tmp_path,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.1,
    )
    tools.publish_mcp_tools(_mcp(search="direct"))
    assert tools.state.active_tool_names is None

    tools.restart_mcp_tools()

    assert tools.state.pending_tool_names == frozenset()
    assert tools.state.active_tool_names is None
    assert _declared(tools) == ("read",)
    tools.publish_mcp_tools(_mcp(search="direct"))
    assert _declared(tools) == ("read", "mcp__docs__search")


def test_a_reload_treats_a_deactivated_direct_tool_as_new(tmp_path: Path) -> None:
    # Pi builds a new registry on reload, so the tool registers anew and
    # registering activates it.
    tools = _session(tmp_path)
    tools.publish_mcp_tools(_mcp(search="direct"))
    assert tools.set_active_tools(("read",))

    _reload(tools)
    tools.drop_pending_tools()
    tools.publish_mcp_tools(_mcp(search="direct"))

    assert _declared(tools) == ("read", "mcp__docs__search")


@pytest.mark.parametrize("kept", [False, True])
def test_a_selection_made_before_restored_tools_register(
    tmp_path: Path, kept: bool
) -> None:
    # Pi: "drops/keeps restored tools when an extension sets the loadout
    # before they register". A selection that deactivates a tool replaces
    # the restored one; a selection that only adds keeps it.
    tools = _session(tmp_path, extensions={"lookup": _Tool("lookup")})
    tools.restore_active_tools(("read", "mcp__docs__find", "mcp__docs__gone"))
    assert _declared(tools) == ("read",)
    assert tools.state.pending_tool_names == {"mcp__docs__find", "mcp__docs__gone"}

    assert tools.set_active_tools(("read", "lookup") if kept else ("lookup",))
    tools.publish_mcp_tools(_mcp(find="deferred"))

    assert ("mcp__docs__find" in _declared(tools)) is kept
    assert tools.state.pending_tool_names == (
        {"mcp__docs__gone"} if kept else frozenset()
    )


def test_a_restored_transcript_declares_its_tools_once_their_server_connects(
    tmp_path: Path,
) -> None:
    # Pi: "declares tools tool_search loaded again on resume once their
    # server connects". Only names of MCP tools wait; the filters apply.
    tools = _session(tmp_path, ToolFilterOptions(exclude=("mcp__docs__fetch",)))

    tools.restore_active_tools(
        ("read", "vanished", "mcp__docs__find", "mcp__docs__fetch")
    )

    assert tools.state.pending_tool_names == {"mcp__docs__find"}
    tools.publish_mcp_tools(_mcp(find="deferred", fetch="deferred"))
    assert _declared(tools) == ("read", "mcp__docs__find")
    # A later restore replaces what was pending.
    tools.restore_active_tools(("read",))
    assert tools.state.pending_tool_names == frozenset()


def test_adding_tools_keeps_the_pending_ones_and_an_exposure_change_drops_them(
    tmp_path: Path,
) -> None:
    tools = _loaded(tmp_path)
    _reload(tools)
    tools.activate_tools(("read",))
    tools.publish_mcp_tools(_mcp(fetch="deferred"))
    assert tools.load_tools(("mcp__docs__fetch",)) == ("mcp__docs__fetch",)
    assert tools.state.pending_tool_names == {"mcp__docs__search", "mcp__docs__find"}

    tools.deactivate_indirect_mcp_tools(("mcp__docs__fetch",))

    assert tools.state.pending_tool_names == frozenset()


def test_a_pending_tool_that_registers_while_a_reload_is_under_way_stays_restored(
    tmp_path: Path,
) -> None:
    # The old servers may still publish before the reload is published and
    # between that and their end. A pending name they consume is declared
    # then, and what is declared is what the restart keeps.
    tools = _loaded(tmp_path)
    _reload(tools)
    assert tools.state.pending_tool_names == {"mcp__docs__search", "mcp__docs__find"}

    tools.publish_mcp_tools(_mcp(find="deferred"))
    tools.publish(tools.prepare_extensions({}))
    tools.publish_mcp_tools(_mcp(find="deferred", search="direct"))
    tools.restart_mcp_tools()

    assert _registered(tools) == ("read", "bash")
    assert tools.state.pending_tool_names == {"mcp__docs__search", "mcp__docs__find"}
    tools.publish_mcp_tools(_mcp(find="deferred"))
    assert _declared(tools) == ("read", "bash", "mcp__docs__find")


def test_dropping_a_tool_the_filters_hide_keeps_the_pending_tools(
    tmp_path: Path,
) -> None:
    tools = _session(
        tmp_path,
        ToolFilterOptions(exclude=("bash",)),
        extensions={"lookup": _Tool("lookup")},
    )
    assert tools.set_active_tools(("read", "bash"))
    tools.restore_active_tools(("read", "bash", "mcp__docs__find"))
    assert _declared(tools) == ("read",)

    assert tools.set_active_tools(("read", "lookup"))

    assert tools.state.pending_tool_names == {"mcp__docs__find"}
    tools.publish_mcp_tools(_mcp(find="deferred"))
    assert _declared(tools) == ("read", "lookup", "mcp__docs__find")
