"""The `input` hooks of a line typed into a session (goal check r2, S9).

Pi's interactive mode hands a typed line to ``AgentSession.prompt``: the
extension commands first, then the ``input`` handlers on the text as typed,
then skill and template expansion of what they left. A skill or template
line no longer skips the hooks.
"""

from __future__ import annotations

import io
from pathlib import Path

from test_native_automation_prompt_dispatch import _HOOKS, _resource
from test_native_extension_tool_metadata import _result, _StubProvider
from test_native_mcp_session import _extension

from pipy_harness.models import HarnessStatus
from pipy_harness.native.coding.session import (
    CodingSession,
    production_tool_registry,
)


def _run(workspace: Path, provider: _StubProvider, lines: str) -> HarnessStatus:
    session = CodingSession(
        provider=provider, tool_registry=production_tool_registry(), tool_budget=5
    )
    return session.run(
        workspace_root=workspace,
        input_stream=io.StringIO(lines),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    ).status


def test_a_typed_template_line_meets_the_input_hooks(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    workspace = tmp_path / "work"
    directory = _extension(workspace, "hooks", _HOOKS)
    _resource(
        workspace / ".pipy" / "templates", "review", "Please review $ARGUMENTS.\n"
    )
    _resource(workspace / ".pipy" / "skills", "lint", "Apply the lint rules.\n")
    provider = _StubProvider([_result(final_text="one"), _result(final_text="two")])

    status = _run(workspace, provider, "/review this\nskip\n/skill:lint now\n")

    assert status is HarnessStatus.SUCCEEDED
    # Each line met the hooks once, as typed; `skip` was handled.
    assert (directory / "seen.txt").read_text().splitlines() == [
        "/review this",
        "skip",
        "/skill:lint now",
    ]
    prompts = [request.user_prompt.strip() for request in provider.requests]
    assert prompts[0] == "Please review this."
    assert prompts[1].startswith('<skill name="lint"')
    assert len(prompts) == 2
