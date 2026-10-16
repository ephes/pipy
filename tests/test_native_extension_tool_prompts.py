"""Tool prompt snippets and guidelines (goal-check finding M7; Pi
``promptSnippet`` and ``promptGuidelines``).

A tool's definition carries the line the default system prompt lists it with
and the bullets it adds to the prompt's rules while it is declared. A script
that reaches the tool sees its guidelines with its declaration.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from test_native_extension_tool_metadata import _result, _StubProvider

from pipy_harness.extensions import ExtensionTool, ToolResult
from pipy_harness.models import HarnessStatus
from pipy_harness.native.coding.codemode_description import (
    CodemodeLoadout,
    codemode_tool_guidelines,
)
from pipy_harness.native.coding.codemode_discovery import CodemodeDiscovery
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.extension_types import _ActivationError
from pipy_harness.native.extensions.tool_normalization import (
    _normalize_extension_tool,
)
from pipy_harness.native.system_prompt_sections import (
    SystemPromptTemplate,
    render_system_prompt,
)
from pipy_harness.native.tools import ToolDefinition
from pipy_harness.native.tools.loadout import ToolLoadout
from pipy_harness.native.tools.prompts import (
    BUILTIN_TOOL_PROMPTS,
    normalized_prompt_guidelines,
    normalized_prompt_snippet,
    prompt_guidelines,
    prompt_snippets,
)
from pipy_harness.native.tools.registry import production_tool_registry

_EXTENSION = """from pipy_harness.extensions import ExtensionTool, ToolResult

_NONE = {'type': 'object', 'properties': {}, 'additionalProperties': False}


def activate(api):
    api.register_tool(ExtensionTool(
        name='deploy', description='Deploys the site.', input_schema=_NONE,
        handler=lambda ctx, params: ToolResult(content='deployed'),
        prompt_snippet='Deploy the site\\n  to production',
        prompt_guidelines=['  Run the tests before deploy.', 'Run the tests before deploy.', ' '],
    ))
    api.register_tool(ExtensionTool(
        name='quiet', description='No prompt text.', input_schema=_NONE,
        handler=lambda ctx, params: ToolResult(content='ok'),
    ))
    api.register_tool(ExtensionTool(
        name='later', description='Inactive.', input_schema=_NONE,
        default_active=False, prompt_snippet='Only when active',
        prompt_guidelines=['Never shown while inactive.'],
        handler=lambda ctx, params: ToolResult(content='ok'),
    ))

    @api.on('before_agent_start')
    def tools(event, ctx):
        import json
        from pathlib import Path
        info = {t.name: list(t.prompt_guidelines) for t in ctx.get_all_tools()}
        Path(ctx.cwd, 'tools.json').write_text(json.dumps(info))
"""


def _run(tmp_path: Path, source: str = _EXTENSION):
    directory = tmp_path / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "prompts.py").write_text(source, encoding="utf-8")
    template = SystemPromptTemplate(cwd=str(tmp_path))
    provider = _StubProvider([_result(final_text="done")])
    errors = io.StringIO()
    session = CodingSession(
        provider=provider,
        tool_registry=production_tool_registry(),
        tool_budget=8,
        system_prompt_sections=template,
    )
    result = session.run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=errors,
        system_prompt=render_system_prompt(template.sections(())),
    )
    assert result.status is HarnessStatus.SUCCEEDED, errors.getvalue()
    tools = tmp_path / "tools.json"
    return provider.requests[0].system_prompt, (
        tools.read_text() if tools.exists() else ""
    )


def _section(prompt: str, name: str) -> str:
    return prompt.split(f"<{name}>\n", 1)[1].split(f"\n</{name}>", 1)[0]


def test_an_extension_tool_adds_its_snippet_and_guidelines_to_the_prompt(
    tmp_path, monkeypatch
) -> None:
    # Pi `_rebuildSystemPrompt`: snippets of the registered definitions,
    # normalized to one line; guidelines of the selected tools, trimmed and
    # without repeats. A tool without a snippet is not listed.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))

    prompt, tools = _run(tmp_path)

    listed = _section(prompt, "tools")
    assert "- deploy: Deploy the site to production" in listed
    assert "quiet" not in listed
    assert "later" not in listed
    rules = _section(prompt, "rules")
    assert rules.count("- Run the tests before deploy.") == 1
    assert "Never shown while inactive." not in rules
    # The built-in tools keep theirs.
    assert "- read: Read file contents" in listed
    assert "- Use read to examine files instead of cat or sed." in rules
    # `ctx.get_all_tools()` reports the guidelines as registered (Pi `ToolInfo`).
    assert '"deploy": ["  Run the tests before deploy.", ' in tools
    assert '"quiet": []' in tools
    assert '"read": ["Use read to examine files instead of cat or sed."]' in tools


_REPLACEMENTS = """from pipy_harness.extensions import ExtensionTool, ToolResult

_NONE = {'type': 'object', 'properties': {}, 'additionalProperties': False}


def activate(api):
    api.register_tool(ExtensionTool(
        name='codemode', description='My scripts.', input_schema=_NONE,
        exposure='model-only', prompt_snippet='Run my own scripts',
        prompt_guidelines=['Prefer my scripts.'],
        handler=lambda ctx, params: ToolResult(content='ran'),
    ))
    api.register_tool(ExtensionTool(
        name='tool_search', description='My search.', input_schema=_NONE,
        prompt_snippet='Search my way',
        handler=lambda ctx, params: ToolResult(content='found'),
    ))
"""


def test_a_replacement_codemode_or_tool_search_brings_its_own_prompt_text(
    tmp_path, monkeypatch
) -> None:
    # Pi: the registered definition's snippet and guidelines, whichever
    # extension registered the tool.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))

    prompt, _tools = _run(tmp_path, _REPLACEMENTS)

    listed = _section(prompt, "tools")
    assert "- codemode: Run my own scripts" in listed
    assert "- tool_search: Search my way" in listed
    assert "Run Python that calls other tools" not in listed
    rules = _section(prompt, "rules")
    assert "- Prefer my scripts." in rules
    assert "Use codemode to batch" not in rules


def test_scripts_see_an_extension_tools_guidelines() -> None:
    # Pi `getToolGuidelines` and `prepareCodemodeLoadout`
    # (`loadout.getPromptGuidelines`): ALL_TOOLS, describe_tool and the
    # codemode description list a callable tool's guidelines.
    helper = ToolDefinition(
        "helper",
        "Helps.",
        {"type": "object", "properties": {}},
        exposure="codemode",
        prompt_guidelines=("Call helper first.", " Call helper first. "),
    )
    discovery = CodemodeDiscovery(
        [helper], guidelines=codemode_tool_guidelines([helper])
    )

    described = discovery("describe_tool", {"name": "helper"}).value
    assert described.startswith("Helps.\n\n- Call helper first.\n")
    listed = CodemodeLoadout()(ToolLoadout((), (helper,), (helper,)))
    assert (
        "Helps.\n\n- Call helper first.\n\ncodemode"
        in (listed.descriptions["codemode"])
    )
    assert listed.descriptions["codemode"].count("- Call helper first.") == 1


def test_the_loadout_reports_exposure_namespace_and_guidelines() -> None:
    # Pi `ToolLoadout.getExposure`, `getNamespace`, `getPromptGuidelines`.
    helper = ToolDefinition(
        "helper",
        "Helps.",
        {"type": "object", "properties": {}},
        exposure="codemode",
        prompt_guidelines=(" a ", "a", "", "b"),
    )
    loadout = ToolLoadout((), (helper,), (helper,))

    assert loadout.get_exposure("helper") == "codemode"
    assert loadout.get_exposure("missing") == "direct"
    assert loadout.get_namespace("helper") is None
    assert loadout.get_prompt_guidelines("helper") == ("a", "b")
    assert loadout.get_prompt_guidelines("missing") == ()


def test_prompt_text_is_normalized_as_pi_normalizes_it() -> None:
    assert normalized_prompt_snippet(" two\r\nlines\t here ") == "two lines here"
    assert normalized_prompt_snippet(" \n ") is None
    assert normalized_prompt_snippet(None) is None
    assert normalized_prompt_guidelines([" x ", "x", "", "y"]) == ("x", "y")
    definitions = [port.definition for port in production_tool_registry().values()]
    assert prompt_snippets(definitions) == {
        name: prompt.snippet
        for name, prompt in BUILTIN_TOOL_PROMPTS.items()
        if name not in {"codemode", "tool_search"}
    }
    assert prompt_guidelines(definitions) == {
        name: prompt.guidelines
        for name, prompt in BUILTIN_TOOL_PROMPTS.items()
        if prompt.guidelines and name not in {"codemode", "tool_search"}
    }


@pytest.mark.parametrize(
    "fields",
    [
        {"prompt_snippet": 3},
        {"prompt_guidelines": "one string"},
        {"prompt_guidelines": [1]},
        {"prompt_guidelines": None},
    ],
)
def test_prompt_text_of_the_wrong_kind_is_refused_at_registration(fields) -> None:
    tool = ExtensionTool(
        name="bad",
        description="Bad.",
        input_schema={"type": "object", "properties": {}},
        handler=lambda ctx, params: ToolResult(content="x"),
        **fields,
    )

    with pytest.raises(_ActivationError):
        _normalize_extension_tool(tool, name="bad", extension="x")
