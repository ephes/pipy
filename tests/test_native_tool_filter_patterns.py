"""Tool patterns and the MCP rules of `--tools` (MCP parity plan S10).

Rows EXP-06 to EXP-09. The six `--tools` cases of Pi's
``agent-session-mcp.test.ts`` are mirrored at the capability level, with
registry entries named ``mcp__docs__<tool>`` in the place of a connected
server: "keeps MCP tools when --tools names no MCP tool", "removes MCP tools
with --no-tools", "does not declare unnamed MCP tools restored from the
transcript", "lets tool_search declare unnamed MCP tools when --tools names
it", "filters MCP tools by the mcp__ entries of --tools" and "removes MCP
tools matching --exclude-tools patterns".
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipy_harness import cli
from pipy_harness.native.agent import AgentToolCall, ProductContent
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
from pipy_harness.native.tools.base import ToolExposure
from pipy_harness.native.tools.name_patterns import (
    create_tool_name_matcher,
    is_mcp_tool_name,
)
from pipy_harness.native.tools.registry import ProductionToolRegistry

_RESOURCE_TOOLS = ("list_mcp_resources", "read_mcp_resource")


class _Tool:
    def __init__(
        self, name: str, exposure: ToolExposure = "direct", description: str = ""
    ) -> None:
        self._definition = ToolDefinition(
            name=name,
            description=description or f"Fixture {name} tool.",
            input_schema={"type": "object"},
            exposure=exposure,
            schema_mode="foreign",
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=f"ran {self._definition.name}",
        )


def _server_tools(
    exposure: ToolExposure, overrides: dict[str, ToolExposure] | None = None
) -> tuple[_Tool, ...]:
    """The tools of a server named ``docs``, as Pi's fake server lists them."""

    descriptions = {
        "search": "Search the docs.",
        "fail": "Always fails.",
        "shot": "Returns an image.",
    }
    return tuple(
        _Tool(f"mcp__docs__{name}", (overrides or {}).get(name, exposure), description)
        for name, description in descriptions.items()
    )


def _session(
    tmp_path: Path,
    options: ToolFilterOptions,
    mcp_tools: Sequence[ToolPort],
    *,
    production: bool = False,
) -> NativeToolCapabilities:
    """Built-ins `read`, `bash` and a `codemode` stand-in, plus MCP-named tools.

    With ``production`` the built-in registry is the production kind, which
    registers the real `tool_search`; no `codemode` is involved then.
    """

    builtins: dict[str, ToolPort] = {"read": _Tool("read"), "bash": _Tool("bash")}
    if not production:
        builtins["codemode"] = _Tool("codemode", "model-only")
    tools = NativeToolCapabilities(
        ProductionToolRegistry(builtins) if production else builtins,
        {},
        workspace_root=tmp_path,
        filter_options=options,
        cancel_join_timeout_seconds=0.1,
    )
    tools.publish(
        tools.prepare_extensions({tool.definition.name: tool for tool in mcp_tools})
    )
    return tools


def _declared(tools: NativeToolCapabilities) -> tuple[str, ...]:
    return tuple(definition.name for definition in tools.definitions())


def _registered(tools: NativeToolCapabilities) -> tuple[str, ...]:
    return tuple(tool.name for tool in tools.all_tools())


def _callable(tools: NativeToolCapabilities) -> frozenset[str]:
    return tools.callable_snapshot().callable_names()


# --- EXP-08: the matcher -----------------------------------------------------


@pytest.mark.parametrize(
    ("entries", "name", "expected"),
    [
        (("read",), "read", True),
        (("read",), "read_file", False),
        ((), "read", False),
        (("mcp__docs__*",), "mcp__docs__search", True),
        (("mcp__docs__*",), "mcp__docs__", True),
        (("mcp__docs__*",), "mcp__other__search", False),
        (("mcp__*__search",), "mcp__docs__search", True),
        (("*",), "anything", True),
        (("*",), "", True),
        (("*search*",), "mcp__docs__search_all", True),
        (("m*p", "bash"), "bash", True),
        # Only `*` is special; everything else is literal.
        (("a.c*",), "abc", False),
        (("a.c*",), "a.cd", True),
        (("re?d*",), "read", False),
        (("[rb]*",), "read", False),
        (("(read|bash)*",), "read", False),
        (("a\\*",), "a\\b", True),
        # The whole name has to match, and a pattern is case-sensitive.
        (("ead*",), "read", False),
        (("*rea",), "read", False),
        (("READ*",), "read", False),
        # As in a JavaScript pattern, `*` does not cross a line terminator.
        (("a*",), "a\nb", False),
        (("a*",), "a b", False),
        (("a*",), "a\n", False),
    ],
)
def test_matcher_matches_exact_names_and_star_patterns(
    entries: tuple[str, ...], name: str, expected: bool
) -> None:
    assert create_tool_name_matcher(entries)(name) is expected


def test_matcher_accepts_any_iterable_of_entries() -> None:
    matches = create_tool_name_matcher(entry for entry in ["read", "mcp__*"])

    assert [name for name in ("read", "mcp__a__b", "write") if matches(name)] == [
        "read",
        "mcp__a__b",
    ]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("mcp__docs__search", True),
        ("mcp__", True),
        ("list_mcp_resources", True),
        ("list_mcp_resource_templates", True),
        ("read_mcp_resource", True),
        ("mcp_docs_search", False),
        ("xmcp__docs__search", False),
        ("read", False),
    ],
)
def test_server_tools_and_resource_tools_count_as_mcp_tools(
    name: str, expected: bool
) -> None:
    assert is_mcp_tool_name(name) is expected


def test_allow_and_exclude_entries_may_be_patterns(tmp_path: Path) -> None:
    tools = NativeToolCapabilities(
        {name: _Tool(name) for name in ("read", "grep", "find", "write", "bash")},
        {"ext_search": _Tool("ext_search"), "ext_fetch": _Tool("ext_fetch")},
        workspace_root=tmp_path,
        filter_options=ToolFilterOptions(
            allow=("read", "ext_*", "*r*"), exclude=("*fetch", "write")
        ),
        cancel_join_timeout_seconds=0.1,
    )

    assert _declared(tools) == ("read", "grep", "ext_search")
    assert _registered(tools) == ("read", "grep", "ext_search")
    assert tools.unknown_filter_names == ()


def test_modifier_entries_take_exact_names_only() -> None:
    with pytest.raises(ValueError) as error:
        cli._tool_filter_options_from_args(SimpleNamespace(tools="+read,-mcp__docs__*"))

    assert str(error.value) == (
        "+name and -name entries take exact tool names, not patterns: -mcp__docs__*"
    )
    with pytest.raises(TypeError, match="exact"):
        ToolFilterOptions(modifiers=("+mcp__*",))
    options = cli._tool_filter_options_from_args(
        SimpleNamespace(tools="read, mcp__docs__*", exclude_tools="mcp__docs__f*")
    )
    assert options.allow == ("read", "mcp__docs__*")
    assert options.exclude == ("mcp__docs__f*",)


def test_patterns_and_mcp_names_are_not_reported_as_unknown_tools(
    tmp_path: Path,
) -> None:
    # A pattern names no tool, and a server's tools are registered when it
    # connects, which may be after the names are checked.
    tools = _session(
        tmp_path,
        ToolFilterOptions(
            allow=("read", "nothing*", "mcp__later__tool", "typo"),
            exclude=("mcp__*__drop", "read_mcp_resource", "other_typo"),
        ),
        (),
    )

    assert tools.unknown_filter_names == ("other_typo", "typo")


def test_a_pattern_selects_the_optional_codemode_builtin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probes: list[bool] = []

    def probe(self: NativeToolCapabilities) -> bool:
        probes.append(True)
        return False

    monkeypatch.setattr(NativeToolCapabilities, "_probe_codemode", probe)

    def build(options: ToolFilterOptions) -> None:
        tools = NativeToolCapabilities(
            ProductionToolRegistry({"read": _Tool("read")}),
            {},
            workspace_root=tmp_path,
            filter_options=options,
            cancel_join_timeout_seconds=0.1,
        )
        tools.prepare_extensions({})

    build(ToolFilterOptions(allow=("read", "code*")))
    assert probes == [True]
    build(ToolFilterOptions(allow=("read", "code*"), exclude=("*mode",)))
    build(ToolFilterOptions(allow=("read", "mcp__*")))
    assert probes == [True]


# --- EXP-06: an allowlist of plain names keeps MCP tools ----------------------


@pytest.mark.parametrize("exposure", ["codemode", "deferred"])
def test_keeps_mcp_tools_when_tools_names_no_mcp_tool(
    tmp_path: Path, exposure: ToolExposure
) -> None:
    tools = _session(
        tmp_path,
        ToolFilterOptions(allow=("read", "codemode")),
        (
            *_server_tools(exposure),
            *(_Tool(name, exposure) for name in _RESOURCE_TOOLS),
        ),
    )

    assert _declared(tools) == ("read", "codemode")
    assert _callable(tools) >= {
        "mcp__docs__search",
        "mcp__docs__fail",
        "list_mcp_resources",
        "read_mcp_resource",
    }
    assert "bash" not in _registered(tools)
    assert "bash" not in _callable(tools)


def test_an_unnamed_direct_mcp_tool_stays_registered_but_is_not_declared(
    tmp_path: Path,
) -> None:
    tools = _session(
        tmp_path,
        ToolFilterOptions(allow=("codemode",)),
        _server_tools("codemode", {"fail": "direct"}),
    )

    assert "mcp__docs__fail" in _registered(tools)
    assert _declared(tools) == ("codemode",)
    # A direct tool is callable from scripts only while it is active.
    assert _callable(tools) == {"mcp__docs__search", "mcp__docs__shot"}


@pytest.mark.parametrize(
    ("exposure", "options"),
    [
        # Whatever the exposure: without `tool_search` nothing could load it.
        ("direct", ToolFilterOptions(allow=("read",))),
        ("model-only", ToolFilterOptions(allow=("read",))),
        ("codemode", ToolFilterOptions(allow=("read",))),
        ("deferred", ToolFilterOptions(allow=("read",))),
        # A direct tool is never one `tool_search` loads.
        ("direct", ToolFilterOptions(allow=("read", "tool_search"))),
        # `tool_search` is registered only while the filters keep it.
        (
            "deferred",
            ToolFilterOptions(allow=("read", "tool_search"), exclude=("tool_*",)),
        ),
    ],
)
def test_an_extension_cannot_activate_an_mcp_tool_the_allowlist_does_not_name(
    tmp_path: Path, exposure: ToolExposure, options: ToolFilterOptions
) -> None:
    tools = _session(tmp_path, options, _server_tools(exposure), production=True)

    assert tools.set_active_tools(["read", "mcp__docs__search"])

    assert _declared(tools) == ("read",)
    assert "mcp__docs__search" in _registered(tools)
    assert tools.loadable_tools() == ()
    assert tools.load_tools(["mcp__docs__search"]) == ()
    # A direct tool that is not declared is not callable either.
    assert ("mcp__docs__search" in _callable(tools)) is (
        exposure in ("codemode", "deferred")
    )


@pytest.mark.parametrize("exposure", ["codemode", "deferred", "model-only"])
def test_an_unnamed_mcp_tool_may_be_activated_when_tool_search_could_load_it(
    tmp_path: Path, exposure: ToolExposure
) -> None:
    tools = _session(
        tmp_path,
        ToolFilterOptions(allow=("read", "tool_search")),
        _server_tools(exposure),
        production=True,
    )

    assert _declared(tools) == ("read", "tool_search")
    assert tools.set_active_tools(["read", "mcp__docs__search"])
    assert _declared(tools) == ("read", "mcp__docs__search")


def test_a_named_mcp_tool_is_declared_like_any_other_tool(tmp_path: Path) -> None:
    tools = _session(
        tmp_path,
        ToolFilterOptions(allow=("read", "mcp__docs__search")),
        _server_tools("direct"),
    )

    assert _declared(tools) == ("read", "mcp__docs__search")
    assert _registered(tools) == ("read", "mcp__docs__search")


def test_tools_without_an_allowlist_declare_direct_mcp_tools(tmp_path: Path) -> None:
    # The rule belongs to the allowlist: an exclusion alone changes nothing
    # for the MCP tools it does not match.
    tools = _session(
        tmp_path, ToolFilterOptions(exclude=("bash",)), _server_tools("direct")
    )

    assert _declared(tools) == (
        "read",
        "codemode",
        "mcp__docs__search",
        "mcp__docs__fail",
        "mcp__docs__shot",
    )


def test_does_not_declare_unnamed_mcp_tools_restored_from_the_transcript(
    tmp_path: Path,
) -> None:
    first = _session(tmp_path, ToolFilterOptions.empty(), _server_tools("direct"))
    recorded = _declared(first)
    assert "mcp__docs__search" in recorded

    # Like `pipy --tools read,codemode -c` followed by /tree.
    second = _session(
        tmp_path, ToolFilterOptions(allow=("read", "codemode")), _server_tools("direct")
    )
    second.restore_active_tools(recorded)

    assert _declared(second) == ("read", "codemode")
    assert "mcp__docs__search" in _registered(second)
    assert _callable(second) == {"read"}

    second.restore_active_tools([name for name in recorded if name.startswith("mcp__")])
    assert _declared(second) == ()


def test_lets_tool_search_declare_unnamed_mcp_tools_when_tools_names_it(
    tmp_path: Path,
) -> None:
    tools = _session(
        tmp_path,
        ToolFilterOptions(allow=("tool_search",)),
        _server_tools("deferred"),
        production=True,
    )
    assert _declared(tools) == ("tool_search",)
    assert [tool.name for tool in tools.loadable_tools()] == [
        "mcp__docs__search",
        "mcp__docs__fail",
        "mcp__docs__shot",
    ]

    outcome = tools.execute(
        AgentToolCall(
            provider_correlation_id="provider-search",
            tool_name="tool_search",
            arguments_json=ProductContent(
                json.dumps({"query": "search the docs", "limit": 1})
            ),
        )
    )

    assert outcome.result is not None and not outcome.result.is_error
    assert _declared(tools) == ("tool_search", "mcp__docs__search")
    # A reload derives the selection from the filters again.
    tools.publish(tools.prepare_extensions(dict(tools.state.extension_registry)))
    assert _declared(tools) == ("tool_search",)


# --- EXP-07: an `mcp__` entry makes the allowlist filter MCP tools ------------


def test_filters_mcp_tools_by_the_mcp_entries_of_tools(tmp_path: Path) -> None:
    tools = _session(
        tmp_path,
        ToolFilterOptions(allow=("codemode", "mcp__docs__s*")),
        (
            *_server_tools("codemode", {"shot": "direct"}),
            *(_Tool(name, "codemode") for name in _RESOURCE_TOOLS),
        ),
    )

    assert _declared(tools) == ("codemode", "mcp__docs__shot")
    assert _registered(tools) == ("codemode", "mcp__docs__search", "mcp__docs__shot")
    assert _callable(tools) == {"mcp__docs__search", "mcp__docs__shot"}


def test_an_mcp_entry_keeps_only_the_tools_of_the_servers_it_names(
    tmp_path: Path,
) -> None:
    # `--tools read,bash,codemode,'mcp__radius__*'`
    tools = _session(
        tmp_path,
        ToolFilterOptions(allow=("read", "bash", "codemode", "mcp__radius__*")),
        (
            _Tool("mcp__radius__ask", "codemode"),
            _Tool("mcp__radius__list", "deferred"),
            *_server_tools("codemode"),
        ),
    )

    assert _declared(tools) == ("read", "bash", "codemode")
    assert _registered(tools) == (
        "read",
        "bash",
        "codemode",
        "mcp__radius__ask",
        "mcp__radius__list",
    )


def test_an_entry_has_to_start_with_the_mcp_prefix_to_filter_mcp_tools(
    tmp_path: Path,
) -> None:
    # `*search` matches an MCP tool but does not start with `mcp__`: the list
    # still keeps the other MCP tools, and the matched one is declarable.
    tools = _session(
        tmp_path,
        ToolFilterOptions(allow=("read", "*search")),
        _server_tools("direct"),
    )

    assert _declared(tools) == ("read", "mcp__docs__search")
    assert _registered(tools) == (
        "read",
        "mcp__docs__search",
        "mcp__docs__fail",
        "mcp__docs__shot",
    )


# --- EXP-09: exclusions and `--no-tools` remove MCP tools ---------------------


def test_removes_mcp_tools_matching_exclude_tools_patterns(tmp_path: Path) -> None:
    tools = _session(
        tmp_path,
        ToolFilterOptions(allow=("codemode",), exclude=("mcp__docs__f*",)),
        _server_tools("codemode"),
    )

    assert "mcp__docs__search" in _registered(tools)
    assert "mcp__docs__fail" not in _registered(tools)
    assert _callable(tools) == {"mcp__docs__search", "mcp__docs__shot"}


@pytest.mark.parametrize("exposure", ["direct", "codemode"])
def test_excluding_the_mcp_prefix_removes_every_server_tool(
    tmp_path: Path, exposure: ToolExposure
) -> None:
    tools = _session(
        tmp_path, ToolFilterOptions(exclude=("mcp__*",)), _server_tools(exposure)
    )

    assert _registered(tools) == ("read", "bash", "codemode")
    assert _declared(tools) == ("read", "bash", "codemode")
    assert _callable(tools) == {"read", "bash"}
    assert tools.set_active_tools(["read", "mcp__docs__search"])
    assert _declared(tools) == ("read",)


@pytest.mark.parametrize("exposure", ["direct", "codemode"])
def test_removes_mcp_tools_with_no_tools(
    tmp_path: Path, exposure: ToolExposure
) -> None:
    tools = _session(
        tmp_path, ToolFilterOptions(no_tools=True), _server_tools(exposure)
    )

    assert _registered(tools) == ()
    assert _declared(tools) == ()
    assert _callable(tools) == frozenset()
    assert tools.set_active_tools(["mcp__docs__search"])
    assert _declared(tools) == ()
