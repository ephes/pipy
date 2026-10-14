"""A product session's ``submit()`` is Pi's SDK ``session.prompt()``
(goal-check E18): an extension command (``/mcp`` among them) runs and no
run starts, the ``input`` hooks see the text, and a skill command or prompt
template is expanded. Built-in commands of the interactive session and
``!`` stay text for the model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from test_native_automation_prompt_dispatch import _COMMANDS, _resource
from test_native_extension_tool_metadata import _result
from test_native_mcp_session import (
    _extension,
    _Provider,
    _server,
    _settings,
    _wait_until,
)

from pipy_harness.native.mcp.config import LoadedMcpConfig, McpServerEntry
from pipy_harness.native.mcp.options import McpOptions
from pipy_harness.sdk import create_product_session

_HANDLING = """from pipy_harness.extensions import InputHandled


def activate(api):
    def on_input(event, ctx):
        if event.text == 'skip':
            return InputHandled()
        return None

    api.on('input', on_input)
"""


def _session(workspace: Path, provider: _Provider, **options: Any):
    return create_product_session(
        workspace=workspace, provider=provider, settings=_settings(workspace), **options
    )


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "work"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def test_submit_runs_an_extension_command_and_starts_no_run(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    directory = _extension(workspace, "commands", _COMMANDS)
    provider = _Provider([_result(final_text="later")])
    diagnostics: list[str] = []

    with _session(workspace, provider, diagnostic_sink=diagnostics.append) as session:
        state = session.submit("/note from the sdk")
        assert (directory / "note.txt").read_text() == repr("from the sdk")
        assert state.user_turn_count == 0
        assert provider.requests == []
        # The session goes on as before.
        assert session.submit("hello").user_turn_count == 1

    assert [request.user_prompt for request in provider.requests] == ["hello"]


def test_a_failing_command_is_an_extension_error(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _extension(workspace, "commands", _COMMANDS)
    provider = _Provider([])
    diagnostics: list[str] = []

    with _session(workspace, provider, diagnostic_sink=diagnostics.append) as session:
        assert session.submit("/broken").user_turn_count == 0

    assert provider.requests == []
    assert 'Extension "command:broken" error: no luck' in "".join(diagnostics)


def test_submit_expands_templates_and_skills(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _resource(
        workspace / ".pipy" / "templates", "review", "Please review $ARGUMENTS.\n"
    )
    _resource(workspace / ".pipy" / "skills", "lint", "Apply the lint rules.\n")
    provider = _Provider([_result(final_text="one"), _result(final_text="two")])

    with _session(workspace, provider) as session:
        session.submit("/review the diff")
        session.submit("/skill:lint now")

    first, second = (request.user_prompt for request in provider.requests)
    assert first.strip() == "Please review the diff."
    assert second.startswith('<skill name="lint"')
    assert second.rstrip().endswith("now")


def test_an_input_hook_can_handle_a_submitted_text(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _extension(workspace, "handling", _HANDLING)
    provider = _Provider([_result(final_text="one")])

    with _session(workspace, provider) as session:
        assert session.submit("skip").user_turn_count == 0
        assert session.submit("go").user_turn_count == 1

    assert [request.user_prompt for request in provider.requests] == ["go"]


def _initializations(record: Path) -> int:
    if not record.exists():
        return 0
    lines = [json.loads(line) for line in record.read_text().splitlines()]
    return sum(
        1
        for line in lines
        if isinstance(line.get("received"), dict)
        and line["received"].get("method") == "initialize"
    )


def test_submit_runs_the_mcp_command(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    entry, record = _server(tmp_path / "servers")

    def load_config(**_context: Any) -> LoadedMcpConfig:
        return LoadedMcpConfig(
            servers=[
                McpServerEntry(
                    name="docs",
                    config={**entry, "lifecycle": "eager"},
                    source=str(tmp_path / "mcp.json"),
                )
            ],
            errors=[],
        )

    provider = _Provider([])
    diagnostics: list[str] = []
    with create_product_session(
        workspace=workspace,
        provider=provider,
        diagnostic_sink=diagnostics.append,
        mcp=McpOptions(load_config=load_config),
    ) as session:
        _wait_until(lambda: _initializations(record) == 1)
        session.submit("/mcp reconnect docs")
        _wait_until(lambda: _initializations(record) == 2)
        # Without a UI the status is a notice for the diagnostic sink.
        diagnostics.clear()
        assert session.submit("/mcp").user_turn_count == 0
        assert "docs" in "".join(diagnostics)

    assert provider.requests == []
