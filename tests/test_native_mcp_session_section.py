"""The ``mcp_servers`` prompt section in a session, and prompt sections for
``before_agent_start`` hooks (MCP parity plan S20; rows SEC-01, SEC-04,
SEC-06, SEC-07 and EXT-16).

A coding session with a scripted provider and the fake stdio server as a real
child process, as in ``test_native_mcp_session.py``. The servers here have
``deferred`` tools unless a test says otherwise, so that no codemode runtime
is needed; the reach a server is listed with is covered in
``test_native_mcp_servers_section.py``.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from test_native_extension_tool_metadata import _result, _tool_names
from test_native_mcp_session import (
    _LATE,
    _WAIT_SECONDS,
    _extension,
    _Provider,
    _recorded_managers,
    _server,
    _settings,
    _write_mcp_json,
)

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent.messages import AgentAssistantMessage
from pipy_harness.native.coding.session import (
    CodingSession,
    production_tool_registry,
)
from pipy_harness.native.models import ProviderRequest
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.system_prompt_sections import (
    SystemPromptTemplate,
    render_system_prompt,
)
from pipy_harness.native.tool_capabilities import ToolFilterOptions

_INTRO = (
    "MCP servers whose tools are not declared to you."
    " Load the tools of `tool_search` servers with `tool_search`."
)


class _Prompts(io.StringIO):
    """Prompt lines; ``before`` runs before each line is handed out."""

    def __init__(self, prompts: str, before: Callable[[], object]) -> None:
        super().__init__(prompts)
        self._before = before

    def readline(self, size: int | None = -1, /) -> str:  # type: ignore[override]
        self._before()
        return super().readline(-1 if size is None else size)


def _run(
    workspace: Path,
    provider: _Provider,
    prompts: str | io.StringIO = "go\n",
    *,
    resource_options: RuntimeResourceOptions | None = None,
) -> tuple[HarnessStatus, str]:
    errors = io.StringIO()
    session = CodingSession(
        provider=provider,
        tool_registry=production_tool_registry(),
        tool_budget=8,
        settings_manager=_settings(workspace),
        resource_options=resource_options or RuntimeResourceOptions.empty(),
        tool_filter_options=ToolFilterOptions.empty(),
    )
    result = session.run(
        workspace_root=workspace,
        input_stream=io.StringIO(prompts) if isinstance(prompts, str) else prompts,
        output_stream=io.StringIO(),
        error_stream=errors,
    )
    return result.status, errors.getvalue()


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "work"
    workspace.mkdir()
    return workspace


def _section(request: ProviderRequest) -> str | None:
    """The ``mcp_servers`` section of a request's prompt, without its tag."""

    prompt = request.system_prompt
    if "<mcp_servers>\n" not in prompt:
        return None
    return prompt.split("<mcp_servers>\n", 1)[1].split("\n</mcp_servers>", 1)[0]


def test_lists_a_server_before_it_connects_and_appends_its_summary_later(
    tmp_path: Path, monkeypatch
) -> None:
    # SEC-06, SEC-07; Pi: "lists a server before it connects and appends its
    # summary with the next prompt".
    workspace = _workspace(tmp_path)
    entry, _record = _server(
        tmp_path / "servers", "slow", methods=_LATE, instructions="Slow docs.\nMore."
    )
    _write_mcp_json(workspace / "mcp.json", {"slow": {**entry, "exposure": "deferred"}})
    managers = _recorded_managers(monkeypatch)
    provider = _Provider(
        [
            # The server connects while the first prompt is answered.
            lambda: managers[0].wait_until_settled(_WAIT_SECONDS),
            _result(final_text="one"),
            _result(final_text="two"),
            _result(final_text="three"),
        ]
    )

    status, errors = _run(workspace, provider, "first\nsecond\nthird\n")

    assert status is HarnessStatus.SUCCEEDED, errors
    first, second, third = provider.requests
    # The first request lists the server by name only.
    assert _section(first) == f"{_INTRO}\n- mcp__slow (tool_search)"
    assert len(first.system_messages) == 1
    assert dict(first.system_messages[0].message.sections)["mcp_servers"] == (
        f"<mcp_servers>\n{_INTRO}\n- mcp__slow (tool_search)\n</mcp_servers>"
    )
    # Its summary follows as an appended patch of that one section, after the
    # first answer; no tool declaration changes with it.
    assert _section(second) == f"{_INTRO}\n- mcp__slow (tool_search): Slow docs."
    assert len(second.system_messages) == 2
    patch = second.system_messages[1]
    assert patch.message.sections == (
        (
            "mcp_servers",
            f"<mcp_servers>\n{_INTRO}\n- mcp__slow (tool_search): Slow docs.\n"
            "</mcp_servers>",
        ),
    )
    assert (patch.message.tools_added, patch.message.tools_removed) == ((), ())
    assert isinstance(second.messages[patch.position - 1], AgentAssistantMessage)
    assert _tool_names(second) == _tool_names(first)
    # An unchanged section is not appended again.
    assert len(third.system_messages) == 2
    assert _section(third) == _section(second)


def test_waits_for_servers_with_direct_tools_before_listing_the_servers(
    tmp_path: Path, monkeypatch
) -> None:
    # SEC-06; Pi: "waits for servers with direct tools before listing the
    # servers".
    workspace = _workspace(tmp_path)
    entry, _record = _server(
        tmp_path / "servers", "slow", methods=_LATE, instructions="Slow docs."
    )
    _write_mcp_json(
        workspace / "mcp.json",
        {"slow": {**entry, "exposure": "direct", "toolExposure": {"wipe": "deferred"}}},
    )
    provider = _Provider([_result(final_text="ready")])

    status, errors = _run(workspace, provider)

    assert status is HarnessStatus.SUCCEEDED, errors
    assert _section(provider.requests[0]) == (
        f"{_INTRO}\n- mcp__slow (tool_search): Slow docs."
    )
    assert "mcp__slow__search" in _tool_names(provider.requests[0])


def test_leaves_servers_with_only_direct_tools_out_of_the_servers_section(
    tmp_path: Path, monkeypatch
) -> None:
    # SEC-01, SEC-07; Pi: "leaves servers with only direct tools out of the
    # servers section".
    workspace = _workspace(tmp_path)
    entry, _record = _server(tmp_path / "servers", "slow", instructions="Slow docs.")
    _write_mcp_json(workspace / "mcp.json", {"slow": {**entry, "exposure": "direct"}})
    provider = _Provider([_result(final_text="ready")])

    status, errors = _run(workspace, provider)

    assert status is HarnessStatus.SUCCEEDED, errors
    assert "mcp__slow__search" in _tool_names(provider.requests[0])
    assert "mcp_servers" not in provider.requests[0].system_prompt
    assert "mcp_servers" not in dict(
        provider.requests[0].system_messages[0].message.sections
    )


def _connected_first(monkeypatch) -> Callable[[], object]:
    """Holds the first prompt until the servers connected."""

    managers = _recorded_managers(monkeypatch)
    return lambda: managers[0].wait_until_settled(_WAIT_SECONDS)


def test_describes_the_server_with_its_configured_description(
    tmp_path: Path, monkeypatch
) -> None:
    # SEC-04; Pi: "describes the server with its configured description and
    # returns its instructions to scripts" (the section part).
    workspace = _workspace(tmp_path)
    entry, _record = _server(
        tmp_path / "servers", instructions="Always search before reading."
    )
    _write_mcp_json(
        workspace / "mcp.json",
        {
            "docs": {
                **entry,
                "exposure": "deferred",
                "description": "Search the product docs",
            }
        },
    )
    provider = _Provider([_result(final_text="done")])

    status, errors = _run(
        workspace, provider, _Prompts("go\n", _connected_first(monkeypatch))
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    section = _section(provider.requests[0])
    assert section == f"{_INTRO}\n- mcp__docs (tool_search): Search the product docs"
    # The instructions are in no tool description either.
    assert not [
        tool.name
        for tool in provider.requests[0].available_tools
        if "Always search" in tool.description
    ]


def test_lists_servers_by_the_first_line_of_their_instructions(
    tmp_path: Path, monkeypatch
) -> None:
    # SEC-04; Pi: "lists servers by the first line of their instructions
    # without a configured description".
    workspace = _workspace(tmp_path)
    entry, _record = _server(
        tmp_path / "servers", instructions="Docs search.\nLong guidance."
    )
    _write_mcp_json(workspace / "mcp.json", {"docs": {**entry, "exposure": "deferred"}})
    provider = _Provider([_result(final_text="done")])

    status, errors = _run(
        workspace, provider, _Prompts("go\n", _connected_first(monkeypatch))
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    assert _section(provider.requests[0]) == (
        f"{_INTRO}\n- mcp__docs (tool_search): Docs search."
    )
    assert "Long guidance" not in provider.requests[0].system_prompt


def test_a_disabled_server_and_mcp_turned_off_give_no_section(
    tmp_path: Path, monkeypatch
) -> None:
    # SEC-01.
    workspace = _workspace(tmp_path)
    entry, _record = _server(tmp_path / "servers")
    _write_mcp_json(workspace / "mcp.json", {"docs": {**entry, "enabled": False}})
    disabled = _Provider([_result(final_text="done")])

    status, errors = _run(workspace, disabled)

    assert status is HarnessStatus.SUCCEEDED, errors
    assert "mcp_servers" not in disabled.requests[0].system_prompt

    _write_mcp_json(workspace / "mcp.json", {"docs": entry})
    off = _Provider([_result(final_text="done")])

    status, errors = _run(
        workspace,
        off,
        resource_options=replace(RuntimeResourceOptions.empty(), no_mcp=True),
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    assert "mcp_servers" not in off.requests[0].system_prompt


# -- EXT-16: sections for before_agent_start hooks ---------------------------

_SECTIONS_HOOK = """import json
from pathlib import Path

RUNS = []


def activate(api):
    @api.on('before_agent_start')
    def first(event, ctx):
        sections = event.system_prompt_options.sections
        RUNS.append(len(RUNS))
        seen = dict(sections)
        if len(RUNS) == 1:
            sections['notes'] = 'Answer in German.'
            sections['mcp_servers'] = sections['mcp_servers'].upper()
        elif len(RUNS) == 3:
            sections['notes'] = 'Answer in French.'
            del sections['mcp_servers']
        else:
            sections['notes'] = 'Answer in German.'
        Path(ctx.cwd, 'seen.jsonl').open('a').write(json.dumps(seen) + '\\n')

    @api.on('before_agent_start')
    def second(event, ctx):
        sections = event.system_prompt_options.sections
        Path(ctx.cwd, 'second.jsonl').open('a').write(json.dumps({
            'notes': sections.get('notes'),
            'in_prompt': '<notes>\\n' + sections['notes'] + '\\n</notes>'
                         in event.system_prompt,
        }) + '\\n')
"""


def _json_lines(path: Path) -> list[Any]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_a_before_agent_start_hook_reads_and_changes_prompt_sections(
    tmp_path: Path, monkeypatch
) -> None:
    # EXT-16.
    workspace = _workspace(tmp_path)
    _extension(workspace, "sections", _SECTIONS_HOOK)
    entry, _record = _server(tmp_path / "servers")
    _write_mcp_json(workspace / "mcp.json", {"docs": {**entry, "exposure": "deferred"}})
    provider = _Provider(
        [
            _result(final_text="one"),
            _result(final_text="two"),
            _result(final_text="three"),
        ]
    )
    listed = f"{_INTRO}\n- mcp__docs (tool_search)"

    status, errors = _run(
        workspace,
        provider,
        _Prompts("first\nsecond\nthird\n", _connected_first(monkeypatch)),
    )

    assert status is HarnessStatus.SUCCEEDED, errors
    first, second, third = provider.requests
    # A hook sees the session's sections, and each run starts from them
    # again: what a hook did for one run does not reach the next.
    assert _json_lines(workspace / "seen.jsonl") == [{"mcp_servers": listed}] * 3
    # A later hook sees what an earlier one set, also in the prompt text.
    assert _json_lines(workspace / "second.jsonl") == [
        {"notes": "Answer in German.", "in_prompt": True},
        {"notes": "Answer in German.", "in_prompt": True},
        {"notes": "Answer in French.", "in_prompt": True},
    ]
    # The first request has the new section and the changed one, recorded as
    # prompt sections and not as a forced prompt.
    sections = dict(first.system_messages[0].message.sections)
    assert sections["notes"] == "<notes>\nAnswer in German.\n</notes>"
    assert sections["mcp_servers"] == f"<mcp_servers>\n{listed.upper()}\n</mcp_servers>"
    assert first.system_prompt.endswith(
        f"<mcp_servers>\n{listed.upper()}\n</mcp_servers>\n\n"
        "<notes>\nAnswer in German.\n</notes>"
    )
    # Second run: only the section that differs is appended.
    assert second.system_messages[1].message.sections == (
        ("mcp_servers", f"<mcp_servers>\n{listed}\n</mcp_servers>"),
    )
    # Third run: one changed, and the one the hook deleted is removed.
    assert third.system_messages[2].message.sections == (
        ("notes", "<notes>\nAnswer in French.\n</notes>"),
        ("mcp_servers", None),
    )
    assert "mcp_servers" not in third.system_prompt
    assert "Answer in French." in third.system_prompt


_OVERRIDE_HOOK = """from pipy_harness.extensions import BeforeAgentStartResult


def activate(api):
    @api.on('before_agent_start')
    def change(event, ctx):
        sections = event.system_prompt_options.sections
        sections['cwd'] = '/somewhere/else'
        sections['Bad Name'] = 'x'
        sections['preamble'] = 'x'
        sections['number'] = 7
        sections['empty'] = ''
        return BeforeAgentStartResult(append_system_prompt='SUFFIX')
"""


def test_a_hook_replaces_a_builtin_section_and_bad_entries_are_reported(
    tmp_path: Path, monkeypatch
) -> None:
    # EXT-16: a name the prompt already has replaces that section in place;
    # an entry that cannot be a section is left out and reported. A suffix
    # is appended to the prompt with the changed sections.
    workspace = _workspace(tmp_path)
    _extension(workspace, "override", _OVERRIDE_HOOK)
    template = SystemPromptTemplate(cwd="/the/workspace")
    provider = _Provider([_result(final_text="one")])
    errors = io.StringIO()
    session = CodingSession(
        provider=provider,
        tool_registry=production_tool_registry(),
        tool_budget=8,
        settings_manager=_settings(workspace),
        resource_options=RuntimeResourceOptions.empty(),
        tool_filter_options=ToolFilterOptions.empty(),
        system_prompt_sections=template,
    )

    result = session.run(
        workspace_root=workspace,
        input_stream=io.StringIO("go\n"),
        output_stream=io.StringIO(),
        error_stream=errors,
        system_prompt=render_system_prompt(template.sections(())),
    )

    assert result.status is HarnessStatus.SUCCEEDED, errors.getvalue()
    request = provider.requests[0]
    assert request.system_prompt.endswith("<cwd>\n/somewhere/else\n</cwd>\nSUFFIX")
    assert "/the/workspace" not in request.system_prompt
    for name in ("Bad Name", "number", "empty"):
        assert f"<{name}>" not in request.system_prompt
    for name in ("Bad Name", "preamble", "number"):
        assert (
            f"pipy: ignored invalid system prompt section {name!r} set by a "
            "before_agent_start hook"
        ) in errors.getvalue()
    assert "'empty'" not in errors.getvalue()
