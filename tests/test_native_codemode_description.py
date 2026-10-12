"""The codemode description, its catalog and `codemode.mode` (MCP parity plan S08).

Rows CMD-18 to CMD-22 and CMD-27. The catalog cases mirror Pi's "codemode
description catalog" group (`test/tool-search.test.ts`), the capability and
session cases mirror "presents callable tools per codemode.mode" and "shows
the guidelines of tools that do not fit the inline budget through
describeTool()" (`test/suite/agent-session-codemode.test.ts`). Declarations
are in pipy's Python notation.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from test_native_codemode_delivery import (
    provider as codemode_provider,
)
from test_native_codemode_delivery import (
    runtime,  # noqa: F401 - the real-runtime fixture
)
from test_native_coding_session import _CollectingAgentEventSink
from test_native_extension_tool_metadata import _result, _StubProvider

from pipy_harness.native.agent import ToolCallCompleted
from pipy_harness.native.codemode import selftest
from pipy_harness.native.codemode.selftest import Availability
from pipy_harness.native.coding.codemode_description import (
    DEFAULT_CODEMODE_INLINE_BUDGET,
    CodemodeLoadout,
    codemode_tool_guidelines,
    create_codemode_description,
    describe_script_call,
)
from pipy_harness.native.coding.codemode_discovery import CodemodeDiscovery
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.providers.transcript import (
    declared_tools,
    resolve_request_transcript,
)
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.system_prompt_sections import (
    BUILTIN_TOOL_PROMPTS,
    SystemPromptTemplate,
)
from pipy_harness.native.tool_capabilities import (
    NativeToolCapabilities,
    ToolFilterOptions,
)
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolExposure,
    ToolNamespace,
    ToolRequest,
)
from pipy_harness.native.tools.codemode import CODEMODE_DESCRIPTION, CodemodeTool
from pipy_harness.native.tools.loadout import ToolLoadout

_READ_GUIDELINE = "- Use read to examine files instead of cat or sed."


def _tool(
    name: str,
    description: str,
    *,
    namespace: ToolNamespace | None = None,
    exposure: ToolExposure = "codemode",
    output_schema: dict[str, object] | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name,
        description,
        {"type": "object", "properties": {}},
        namespace=namespace,
        exposure=exposure,
        output_schema=output_schema,
        schema_mode="foreign",
    )


def _dashed(name: str, description: str, **metadata: object) -> ToolDefinition:
    """A tool whose name has a `-`, as an MCP tool's can (S16 allows it)."""

    tool = _tool("placeholder", description, **metadata)  # type: ignore[arg-type]
    object.__setattr__(tool, "name", name)
    return tool


_GITHUB_NS = ToolNamespace(name="mcp__github", description="GitHub server")
_DOCS_NS = ToolNamespace(name="mcp__docs")
_PLAIN = _tool("read_notes", "Read notes.")
_GITHUB = [
    _tool(f"mcp__github__{suffix}", f"GitHub {suffix}.", namespace=_GITHUB_NS)
    for suffix in "abc"
]
_DOCS = [
    _tool("mcp__docs__search", "Search docs.", namespace=_DOCS_NS),
    _tool("mcp__docs__long", "Long " * 200, namespace=_DOCS_NS),
]
_ALL = [_PLAIN, *_GITHUB, *_DOCS]


# -- CMD-18: the call contract and the helpers ------------------------------


def test_description_explains_the_call_contract_and_names_the_helpers() -> None:
    assert create_codemode_description([]) == CODEMODE_DESCRIPTION
    assert (
        "`tools.<name>(args)` returns a string, or a structured value if the "
        "tool's declaration says so, and raises `ToolError` on failure."
    ) in CODEMODE_DESCRIPTION
    assert (
        "A tool whose declaration returns a structured value returns it on "
        "failure too when the failed result carries one; check its fields."
    ) in CODEMODE_DESCRIPTION
    assert (
        "- `ALL_TOOLS`, `search_tools(query, limit=8, namespace=None)`, "
        "`describe_tool(name)`, `describe_namespace(name)`: find unlisted tools, "
        "such as MCP tools."
    ) in CODEMODE_DESCRIPTION
    # No tool is named until a request lists some.
    assert "Nested tools:" not in CODEMODE_DESCRIPTION
    assert "Callable tools:" not in CODEMODE_DESCRIPTION


# -- CMD-19 to CMD-21: Pi "codemode description catalog" --------------------


def test_lists_everything_without_a_budget() -> None:
    # Pi: "lists everything without a budget".
    description = create_codemode_description(_ALL)

    assert description.startswith(CODEMODE_DESCRIPTION + "\n\nNested tools:\n\n")
    assert "## mcp__github\nGitHub server" in description
    assert "## mcp__docs\n\n### `mcp__docs" in description
    # The search guidance is always there, so tools that appear later do not
    # change it.
    assert "find unlisted tools, such as MCP tools" in description
    # Tools without a namespace first, then the namespaces by name.
    order = [description.index(text) for text in ("### `read_notes`", "## mcp__docs")]
    assert order == sorted(order)
    assert description.index("## mcp__docs") < description.index("## mcp__github")
    # Each tool with its description and its declaration.
    assert (
        "### `mcp__github__a`\nGitHub a.\n\ncodemode tool declaration:\n"
        "```python\ntools.mcp__github__a(args: dict[str, Any]) -> str\n```"
    ) in description


def test_fills_the_budget_round_robin_cheapest_first() -> None:
    # Pi: "fills the budget round-robin, cheapest first, and says what is
    # missing". The small sections cost 29 to 33 estimated tokens: one tool
    # per group, then one more.
    description = create_codemode_description(_ALL, inline_budget=130)

    assert "### `read_notes`" in description
    assert "## mcp__docs (some tools not listed)" in description
    assert "### `mcp__docs__search`" in description
    assert "### `mcp__docs__long`" not in description
    assert "## mcp__github (some tools not listed)" in description
    assert "### `mcp__github__a`" in description
    assert "### `mcp__github__b`" in description
    assert "### `mcp__github__c`" not in description
    assert "find unlisted tools, such as MCP tools" in description
    # Deterministic: the same input gives the same description.
    assert create_codemode_description(_ALL, inline_budget=130) == description


def test_cost_counts_utf16_units_also_of_a_lone_surrogate() -> None:
    # A description may hold a lone surrogate; it must not fail the request.
    odd = _tool("odd", "x\ud800 \U0001f600")
    assert "### `odd`" in create_codemode_description([odd], inline_budget=1000)
    assert "### `odd`" not in create_codemode_description([odd], inline_budget=1)


def test_a_group_whose_next_tool_does_not_fit_drops_out() -> None:
    # 29 + 33 + 31 fills the first round; the second github tool needs 31.
    description = create_codemode_description(_ALL, inline_budget=123)

    assert "### `mcp__github__a`" in description
    assert "### `mcp__github__b`" not in description
    assert create_codemode_description(_ALL, inline_budget=124).count("### `") == 4


def test_leaves_deferred_tools_and_their_namespaces_out_entirely() -> None:
    # Pi: "leaves deferred tools and their namespaces out entirely" (CMD-21).
    deferred = [
        _tool(tool.name, tool.description, namespace=_GITHUB_NS, exposure="deferred")
        for tool in _GITHUB
    ]
    description = create_codemode_description([_PLAIN, *deferred, *_DOCS])

    assert "mcp__github" not in description
    # Deferred tools, such as those of a server that connects later, do not
    # change the description.
    assert description == create_codemode_description([_PLAIN, *_DOCS])


def test_leaves_namespace_instructions_out() -> None:
    # Pi: "leaves namespace instructions out" (CMD-21).
    namespace = ToolNamespace(name="mcp__github", instructions="Long usage guide.")
    tools = [
        _tool(tool.name, tool.description, namespace=namespace) for tool in _GITHUB
    ]
    description = create_codemode_description(tools)

    assert "## mcp__github\n\n### `mcp__github__a`" in description
    assert "Long usage guide." not in description


def test_lists_only_namespaces_with_a_zero_budget() -> None:
    # Pi: "lists only namespaces with a zero budget".
    description = create_codemode_description(_ALL, inline_budget=0)

    assert "## mcp__docs (tools not listed)" in description
    assert "## mcp__github (tools not listed)\nGitHub server" in description
    assert "codemode tool declaration:" not in description


def test_sections_carry_guidelines_and_the_key_that_reaches_the_tool() -> None:
    # CMD-19 "guidelines"; a tool that lost its identifier is declared by key.
    first = _tool("read_file", "First.")
    second = _dashed("read-file", "Second.")
    description = create_codemode_description(
        [first, second], guidelines={"read_file": ("Prefer it.", "  ")}
    )

    assert "### `read_file`\nFirst.\n\n- Prefer it.\n\ncodemode tool" in description
    assert (
        '### `read-file`\nSecond.\n\ncodemode tool declaration:\n```python\ntools["read-file"]('
    ) in description


def test_listed_tool_is_named_as_the_whole_callable_set_reaches_it() -> None:
    # A direct tool that is not listed still owns the identifier.
    direct = _tool("read_file", "Direct.", exposure="direct")
    listed = _dashed("read-file", "Listed.")

    description = create_codemode_description([listed], callable_tools=[direct, listed])

    assert "### `read-file`\n" in description
    assert 'tools["read-file"](args:' in description
    assert "Direct." not in description


# -- CMD-22: the per-tool line ----------------------------------------------


def test_script_call_line_says_what_a_call_resolves_to() -> None:
    assert describe_script_call(_tool("echo", " Echo text.\n"), "tools.echo") == (
        "Echo text.\n\nCodemode: `tools.echo(args)` resolves to a string."
    )
    fields: dict[str, object] = {
        "type": "object",
        "properties": {
            "content": {"type": "array"},
            "structuredContent": {
                "type": "object",
                "properties": {"results": {"type": "array"}},
                "required": ["results"],
            },
            "isError": {"type": "boolean"},
        },
        "required": ["content"],
    }
    assert describe_script_call(
        _tool("search", "Search.", output_schema=fields), "tools.search"
    ).endswith(
        "Codemode: `tools.search(args)` resolves to "
        "`{ content, structuredContent?: { results }, isError? }`."
    )
    # Anything else is the rendered type on one line.
    assert describe_script_call(
        _tool(
            "names",
            "Names.",
            output_schema={"type": "array", "items": {"type": "string"}},
        ),
        'tools["names"]',
    ).endswith('Codemode: `tools["names"](args)` resolves to `list[str]`.')


# -- CMD-21, CMD-22: the capability value -----------------------------------


class _Port:
    def __init__(
        self,
        name: str,
        exposure: ToolExposure = "direct",
        description: str | None = None,
        namespace: ToolNamespace | None = None,
    ) -> None:
        self.definition = ToolDefinition(
            name,
            description or f"Fixture {name} tool.\n\nSecond paragraph.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            exposure=exposure,
            namespace=namespace,
        )

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        raise AssertionError("not executed")


class _Settings:
    def __init__(self) -> None:
        self.mode: str = "on"
        self.budget: float = DEFAULT_CODEMODE_INLINE_BUDGET

    def loadout(self) -> CodemodeLoadout:
        return CodemodeLoadout(
            mode="only" if self.mode == "only" else "on", inline_budget=self.budget
        )


def _capabilities(
    tmp_path: Path,
    settings: _Settings,
    *,
    extensions: tuple[_Port, ...] = (),
    codemode: bool = True,
) -> NativeToolCapabilities:
    builtins: dict[str, object] = {"read": _Port("read")}
    if codemode:
        builtins["codemode"] = CodemodeTool()
    capabilities = NativeToolCapabilities(
        builtins,  # type: ignore[arg-type]
        {},
        workspace_root=tmp_path,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.1,
        codemode_loadout=settings.loadout,
    )
    _publish(capabilities, extensions)
    return capabilities


def _publish(capabilities: NativeToolCapabilities, extensions: tuple[_Port, ...]):
    capabilities.publish(
        capabilities.prepare_extensions(
            {port.definition.name: port for port in extensions}  # type: ignore[misc]
        )
    )


_EXTENSIONS = (
    _Port("echo"),
    _Port("stats", "codemode"),
    _Port("archive", "deferred"),
)


def _descriptions(capabilities: NativeToolCapabilities) -> dict[str, str]:
    return {
        definition.name: definition.description
        for definition in capabilities.definitions()
    }


def test_presents_callable_tools_per_codemode_mode(tmp_path: Path) -> None:
    # Pi: "presents callable tools per codemode.mode".
    settings = _Settings()
    capabilities = _capabilities(tmp_path, settings, extensions=_EXTENSIONS)
    snapshot = capabilities.callable_snapshot

    # on: declared tools say how scripts call them and are not listed again
    # in codemode, which lists the tools that are not direct.
    described = _descriptions(capabilities)
    assert list(described) == ["read", "codemode", "echo"]
    assert described["echo"] == (
        "Fixture echo tool.\n\nSecond paragraph.\n\n"
        "Codemode: `tools.echo(args)` resolves to a string."
    )
    assert "codemode tool declaration:" not in described["echo"]
    assert "### `echo`" not in described["codemode"]
    assert "### `read`" not in described["codemode"]
    assert "### `stats`" in described["codemode"]
    assert "Codemode:" not in described["codemode"]
    assert snapshot().hidden_declarations() == frozenset()

    # only: codemode lists echo and read, which stay active and callable but
    # are left out of requests. The setting is read when a value is built.
    settings.mode = "only"
    assert _descriptions(capabilities) == described
    _publish(capabilities, _EXTENSIONS)
    described = _descriptions(capabilities)
    assert list(described) == ["read", "codemode", "echo"]
    assert described["echo"] == "Fixture echo tool.\n\nSecond paragraph."
    assert "### `echo`" in described["codemode"]
    assert "### `stats`" in described["codemode"]
    # Hidden tools' guidelines move from the rules to their codemode sections.
    assert (
        f"### `read`\nFixture read tool.\n\nSecond paragraph.\n\n{_READ_GUIDELINE}"
        in (described["codemode"])
    )
    assert "archive" not in described["codemode"]
    assert snapshot().hidden_declarations() == {"read", "echo"}
    assert snapshot().callable_names() == {"read", "echo", "stats", "archive"}

    # Without codemode, tools keep their plain descriptions and stay declared.
    assert capabilities.set_active_tools(["echo"])
    assert _descriptions(capabilities) == {
        "echo": "Fixture echo tool.\n\nSecond paragraph."
    }
    assert snapshot().hidden_declarations() == frozenset()


def test_description_is_byte_identical_when_a_deferred_tool_registers(
    tmp_path: Path,
) -> None:
    # Pi: "keeps the codemode description unchanged when the server connects"
    # (CMD-21): a deferred tool, its namespace and its instructions change
    # nothing, in either mode, and neither does activating it.
    for mode in ("on", "only"):
        settings = _Settings()
        settings.mode = mode
        capabilities = _capabilities(tmp_path, settings, extensions=_EXTENSIONS[:2])
        before = _descriptions(capabilities)["codemode"]
        assert "### `stats`" in before

        server = ToolNamespace(
            name="mcp__docs", description="Docs", instructions="Search first."
        )
        late = _Port("mcp__docs__search", "deferred", namespace=server)
        _publish(capabilities, (*_EXTENSIONS, late))

        assert _descriptions(capabilities)["codemode"] == before
        # tool_search loading the tool declares it; codemode is not redeclared.
        assert capabilities.set_active_tools(
            ["read", "codemode", "echo", "mcp__docs__search"]
        )
        described = _descriptions(capabilities)
        assert "mcp__docs__search" in described
        assert described["codemode"] == before


def test_hidden_declarations_survive_a_transcript_restore(tmp_path: Path) -> None:
    # The transcript declares the tools a request leaves out, so /tree
    # navigation restores them as active and scripts keep reaching them.
    settings = _Settings()
    settings.mode = "only"
    capabilities = _capabilities(tmp_path, settings, extensions=_EXTENSIONS)

    capabilities.restore_active_tools(list(_descriptions(capabilities)))

    snapshot = capabilities.callable_snapshot()
    assert snapshot.hidden_declarations() == {"read", "echo"}
    assert {"read", "echo"} <= snapshot.callable_names()


def test_loadout_applies_only_while_the_builtin_codemode_is_declared(
    tmp_path: Path,
) -> None:
    settings = _Settings()
    settings.mode = "only"
    plain = "Fixture echo tool.\n\nSecond paragraph."

    # No codemode registered.
    without = _capabilities(tmp_path, settings, extensions=_EXTENSIONS, codemode=False)
    assert _descriptions(without)["echo"] == plain
    assert without.callable_snapshot().hidden_declarations() == frozenset()

    # An extension's own `codemode` tool is not the builtin.
    replaced = _capabilities(
        tmp_path, settings, extensions=(*_EXTENSIONS, _Port("codemode"))
    )
    assert _descriptions(replaced)["codemode"].startswith("Fixture codemode tool.")
    assert replaced.callable_snapshot().hidden_declarations() == frozenset()

    # A request that does not declare codemode (an explicit name list).
    capabilities = _capabilities(tmp_path, settings, extensions=_EXTENSIONS)
    assert [d.description for d in capabilities.definitions(["echo"])] == [plain]


def test_long_descriptions_are_declared_without_the_strict_cap(tmp_path: Path) -> None:
    # The strict subset caps a description at 4 KiB; a listing or an appended
    # line may exceed it, and is then declared as a foreign schema.
    settings = _Settings()
    long = _Port("wide", description="w" * ToolDefinition.DESCRIPTION_MAX_LENGTH)
    listed = tuple(_Port(f"listed{n}", "codemode", "d" * 3000) for n in range(3))
    capabilities = _capabilities(tmp_path, settings, extensions=(long, *listed))

    declared = {
        definition.name: definition for definition in capabilities.definitions()
    }
    assert declared["wide"].description.endswith(
        "\n\nCodemode: `tools.wide(args)` resolves to a string."
    )
    assert declared["wide"].schema_mode == "foreign"
    assert declared["read"].schema_mode == "strict"
    assert len(declared["codemode"].description) > 3 * 3000
    assert declared["codemode"].schema_mode == "foreign"
    # The registered definitions are untouched.
    assert long.definition.schema_mode == "strict"
    assert CodemodeTool().definition.description == CODEMODE_DESCRIPTION


def test_inline_budget_bounds_the_listing_of_a_capability_value(tmp_path: Path) -> None:
    settings = _Settings()
    settings.budget = 0
    namespace = ToolNamespace(name="records", description="Record store")
    capabilities = _capabilities(
        tmp_path,
        settings,
        extensions=(_Port("lookup", "codemode", namespace=namespace),),
    )

    assert _descriptions(capabilities)["codemode"] == (
        CODEMODE_DESCRIPTION
        + "\n\nNested tools:\n\n## records (tools not listed)\nRecord store"
    )


def test_a_tool_no_key_reaches_is_neither_described_nor_hidden() -> None:
    # `read-file` and `read_file` share the identifier; the third tool's own
    # name is the identifier the first one holds.
    first = _dashed("read-file", "First.", exposure="direct")
    lost = _tool("read_file", "Lost.", exposure="direct")
    for mode in ("on", "only"):
        changes = CodemodeLoadout(mode="only" if mode == "only" else "on")(
            ToolLoadout((first, lost), (first, lost))
        )
        if mode == "on":
            assert set(changes.descriptions) == {"read-file", "codemode"}
        else:
            assert changes.hidden_declarations == {"read-file"}
            assert "Lost." not in changes.descriptions["codemode"]


# -- CMD-06 with guidelines: discovery --------------------------------------


def test_discovery_descriptions_carry_the_prompt_guidelines() -> None:
    assert codemode_tool_guidelines()["read"] == BUILTIN_TOOL_PROMPTS["read"].guidelines
    assert "bash" not in codemode_tool_guidelines()
    read = _tool("read", "Read a file.", exposure="direct")
    discovery = CodemodeDiscovery([read], guidelines=codemode_tool_guidelines())

    described = discovery("describe_tool", {"name": "read"}).value
    assert described.startswith(f"Read a file.\n\n{_READ_GUIDELINE}\n\ncodemode tool")
    assert discovery.script_tools()[0].description == described
    assert discovery.sample("missing") is None
    assert discovery.call_target("missing") is None


# -- CMD-27: the system prompt contribution ---------------------------------


def test_prompt_contribution_is_one_snippet_and_does_not_depend_on_tools() -> None:
    # pipy's contribution: Pi's snippet and guideline in Python terms, and
    # one more guideline from the adherence slice. Tools add nothing to it.
    contribution = BUILTIN_TOOL_PROMPTS["codemode"]
    assert contribution.snippet == "Run Python that calls other tools"
    assert len(contribution.guidelines) == 2
    template = SystemPromptTemplate(cwd="/w")
    sections = dict(template.sections(("read", "codemode")))
    assert "\n- codemode: Run Python that calls other tools\n" in sections["tools"]
    assert dict(template.sections(("read", "codemode", "mcp__docs__search"))) == (
        sections
    )


# -- Sessions ---------------------------------------------------------------

_SESSION_TOOLS = """from pipy_harness.extensions import ExtensionTool, ToolNamespace, ToolResult

_KEY = {'type': 'object', 'properties': {'key': {'type': 'string'}},
        'required': ['key'], 'additionalProperties': False}


def activate(api):
    api.register_tool(ExtensionTool(
        name='echo', description='Echo a key.', input_schema=_KEY,
        handler=lambda ctx, params: ToolResult(content='echo:' + params['key'])))
    api.register_tool(ExtensionTool(
        name='lookup', description='Look a record up.', input_schema=_KEY,
        handler=lambda ctx, params: ToolResult(content='record:' + params['key']),
        exposure='codemode',
        namespace=ToolNamespace(name='records', description='Record store',
                                instructions='Look up before writing.')))
    api.register_tool(ExtensionTool(
        name='archive', description='Search the archive.', input_schema=_KEY,
        handler=lambda ctx, params: ToolResult(content='archived'),
        exposure='deferred'))
"""


def _write_session_tools(workspace: Path) -> None:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "tools.py").write_text(_SESSION_TOOLS, encoding="utf-8")


def _settings(root: Path, **codemode: object) -> SettingsManager:
    return SettingsManager(
        global_path=root / "global.json",
        project_path=root / "project.json",
        overrides={
            "defaultTools": ["+codemode"],
            "theme": "pi",
            **({"codemode": codemode} if codemode else {}),
        },
    )


def _first_request(tmp_path: Path, monkeypatch, **codemode: object):
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    monkeypatch.setattr(selftest, "availability", lambda: Availability(True))
    _write_session_tools(tmp_path)
    provider = _StubProvider([_result(final_text="done")])
    session = CodingSession(
        provider=provider,
        settings_manager=_settings(tmp_path, **codemode),
        system_prompt_sections=SystemPromptTemplate(cwd=str(tmp_path)),
    )
    session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    return provider.requests[0]


def test_session_request_with_mode_on(tmp_path: Path, monkeypatch) -> None:
    request = _first_request(tmp_path, monkeypatch)

    tools = {tool.name: tool.description for tool in request.available_tools}
    assert {"read", "bash", "echo", "codemode"} <= set(tools)
    assert not {"lookup", "archive"} & set(tools)
    assert tools["echo"] == (
        "Echo a key.\n\nCodemode: `tools.echo(args)` resolves to a string."
    )
    assert tools["read"].endswith("Codemode: `tools.read(args)` resolves to a string.")
    assert tools["codemode"].startswith(CODEMODE_DESCRIPTION + "\n\nNested tools:\n\n")
    assert (
        "## records\nRecord store\n\n### `lookup`\nLook a record up."
        in (tools["codemode"])
    )
    assert "Look up before writing." not in tools["codemode"]
    assert "### `echo`" not in tools["codemode"]
    assert "archive" not in tools["codemode"]
    assert request.hidden_tool_names == request.undeclared_tool_names == ()
    assert "\n- read: " in request.system_prompt
    assert _READ_GUIDELINE in request.system_prompt


def test_session_request_with_mode_only_leaves_direct_declarations_out(
    tmp_path: Path, monkeypatch
) -> None:
    # Pi: "presents callable tools per codemode.mode", the `only` half.
    request = _first_request(tmp_path, monkeypatch, mode="only")

    tools = {tool.name: tool.description for tool in request.available_tools}
    assert list(tools) == ["codemode"]
    assert request.hidden_tool_names == ()
    assert set(request.undeclared_tool_names) == {
        "read",
        "ls",
        "grep",
        "find",
        "write",
        "edit",
        "bash",
        "echo",
    }
    assert "### `echo`\nEcho a key." in tools["codemode"]
    assert "### `lookup`" in tools["codemode"]
    assert "archive" not in tools["codemode"]
    assert "Codemode:" not in tools["codemode"]
    # The prompt's tool list matches the declarations: hidden tools are not
    # listed, and their guidelines move to their codemode sections.
    assert "\n- read: " not in request.system_prompt
    assert "\n- codemode: " in request.system_prompt
    assert _READ_GUIDELINE not in request.system_prompt
    assert _READ_GUIDELINE in tools["codemode"]
    # The transcript declares the hidden tools; an adapter that sends system
    # messages mid-conversation leaves them out.
    recorded = declared_tools(tuple(a.message for a in request.system_messages))
    assert {"read", "echo", "codemode"} <= {tool.name for tool in recorded}
    sent = resolve_request_transcript(request, supports_mid_convo=True)
    assert [tool.name for tool in declared_tools(sent.system_messages)] == ["codemode"]


def test_session_inline_budget_setting(tmp_path: Path, monkeypatch) -> None:
    request = _first_request(tmp_path, monkeypatch, inlineBudget=0)

    codemode = next(t for t in request.available_tools if t.name == "codemode")
    assert codemode.description == (
        CODEMODE_DESCRIPTION
        + "\n\nNested tools:\n\n## records (tools not listed)\nRecord store"
    )


def _run_script(workspace: Path, code: str, monkeypatch, **codemode: object):
    _write_session_tools(workspace)
    sink = _CollectingAgentEventSink()
    fake = codemode_provider(code)
    requests = []
    complete = FakeNativeProvider.complete

    def capture(self, request, **kwargs):
        requests.append(request)
        return complete(self, request, **kwargs)

    monkeypatch.setattr(FakeNativeProvider, "complete", capture)
    session = CodingSession(
        provider=fake,
        settings_manager=_settings(workspace, **codemode),
        agent_event_sink=sink,
    )
    session.run(
        workspace_root=workspace,
        input_stream=io.StringIO("run\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    parent = next(e.result for e in sink.events if isinstance(e, ToolCallCompleted))
    return parent, requests


def _output(parent) -> str:
    return parent.content.value.split("Output:\n", 1)[1].split("\nTool calls", 1)[0]


def test_tools_hidden_by_mode_only_stay_callable_from_a_script(
    tmp_path: Path,
    monkeypatch,
    runtime,  # noqa: F811
) -> None:
    (tmp_path / "note.txt").write_text("KEEP me\n", encoding="utf-8")
    code = """text({'echo': tools.echo(key='k'), 'read': tools.read(path='note.txt'),
      'lookup': tools.lookup(key='r'), 'names': [tool['name'] for tool in ALL_TOOLS]})
"""

    parent, requests = _run_script(tmp_path, code, monkeypatch, mode="only")

    assert [tool.name for tool in requests[0].available_tools] == ["codemode"]
    assert not parent.is_error
    output = json.loads(_output(parent))
    assert output["echo"] == "echo:k"
    assert "KEEP me" in output["read"]
    assert output["lookup"] == "record:r"
    assert {"read", "bash", "echo", "lookup", "archive"} <= set(output["names"])
    # A direct model call to a tool the request left out is an unknown tool.
    assert "codemode" in {tool.name for tool in requests[1].available_tools}
    assert "read" not in {tool.name for tool in requests[1].available_tools}


def test_guidelines_of_tools_outside_the_budget_show_through_describe_tool(
    tmp_path: Path,
    monkeypatch,
    runtime,  # noqa: F811
) -> None:
    # Pi: "shows the guidelines of tools that do not fit the inline budget
    # through describeTool()".
    parent, requests = _run_script(
        tmp_path,
        "text(describe_tool('read'))",
        monkeypatch,
        mode="only",
        inlineBudget=0,
    )

    codemode = next(t for t in requests[0].available_tools if t.name == "codemode")
    assert "### `read`" not in codemode.description
    assert not parent.is_error
    assert _READ_GUIDELINE in _output(parent)
