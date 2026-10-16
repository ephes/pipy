"""A prompt of ``--print`` and ``--mode json`` is dispatched as Pi's
``AgentSession.prompt`` dispatches it (MCP parity plan X1, rows SLH-07 to
SLH-10).

A text that names an extension command runs it and starts no run; ``/mcp``
is such a command. A skill command and a prompt template are expanded.
Everything else is the prompt: the built-in commands of the interactive
session, an unknown ``/name`` and a ``!`` line go to the model as they are.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from test_native_extension_tool_metadata import _result
from test_native_mcp_session import _extension, _Provider, _server, _settings

from pipy_harness.adapters.native import CodingSessionAdapter
from pipy_harness.native.automation.run_modes import run_json_mode, run_print_mode
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tools.registry import production_tool_registry

_COMMANDS = """from pathlib import Path


def activate(api):
    def note(ctx, args):
        Path(__file__).with_name('note.txt').write_text(repr(args))
        ctx.ui.notify('noted ' + args)

    def broken(ctx, args):
        raise RuntimeError('no luck')

    api.register_command('note', 'Write a note', note)
    api.register_command('broken', 'Fails', broken)
"""


def _adapter(workspace: Path, provider: _Provider) -> CodingSessionAdapter:
    return CodingSessionAdapter(
        provider=provider,
        tool_registry=production_tool_registry(),
        settings_manager=_settings(workspace),
    )


def _print(workspace: Path, provider: _Provider, prompt: str) -> tuple[int, str, str]:
    stdout, errors = io.StringIO(), io.StringIO()
    code = run_print_mode(
        adapter=_adapter(workspace, provider),
        prompt=prompt,
        cwd=workspace,
        stdout=stdout,
        error_stream=errors,
    )
    return code, stdout.getvalue(), errors.getvalue()


def _json(
    workspace: Path, provider: _Provider, prompt: str
) -> tuple[int, list[dict[str, Any]], str]:
    stdout, errors = io.BytesIO(), io.StringIO()
    code = run_json_mode(
        adapter=_adapter(workspace, provider),
        prompt=prompt,
        cwd=workspace,
        native_session=NativeSessionTree.create(workspace, persist=False),
        stdout_buffer=stdout,
        error_stream=errors,
    )
    records = [json.loads(line) for line in stdout.getvalue().decode().splitlines()]
    return code, records, errors.getvalue()


def _mcp_json(workspace: Path, servers: dict[str, Any]) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "mcp.json").write_text(json.dumps({"mcpServers": servers}))


def _resource(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: {name} resource\n---\n{body}",
        encoding="utf-8",
    )


def test_print_mode_runs_mcp_and_starts_no_run(tmp_path: Path) -> None:
    # SLH-02, SLH-07, SLH-11: the command runs, the model is not asked, and
    # nothing is printed: print mode has no UI, so the status notice is
    # dropped (Pi's `noOpUIContext`).
    workspace = tmp_path / "work"
    entry, _record = _server(tmp_path / "servers")
    _mcp_json(workspace, {"docs": {**entry, "exposure": "direct"}})
    provider = _Provider([])

    code, stdout, errors = _print(workspace, provider, "/mcp")

    assert code == 0, errors
    assert stdout == ""
    assert provider.requests == []
    assert "docs:" not in errors


def test_json_mode_runs_mcp_and_emits_only_the_header(tmp_path: Path) -> None:
    # SLH-10: no run, so no event and no `agent_settled`.
    workspace = tmp_path / "work"
    entry, _record = _server(tmp_path / "servers")
    _mcp_json(workspace, {"docs": entry})
    provider = _Provider([])

    code, records, errors = _json(workspace, provider, "/mcp")

    assert code == 0, errors
    assert [record["type"] for record in records] == ["session"]
    assert provider.requests == []
    assert "docs:" not in errors


def test_mcp_subcommand_runs_from_a_prompt(tmp_path: Path) -> None:
    # SLH-06 through the same port: no run, and the usage notice is dropped
    # like every notice of print mode.
    workspace = tmp_path / "work"
    workspace.mkdir()
    provider = _Provider([])

    code, stdout, errors = _print(workspace, provider, "/mcp frobnicate")

    assert (code, stdout, provider.requests) == (0, "", [])
    assert "Usage" not in errors


@pytest.mark.parametrize("mode", ["print", "json"])
def test_an_extension_command_runs_with_its_arguments(
    tmp_path: Path, mode: str
) -> None:
    # SLH-07.
    workspace = tmp_path / "work"
    directory = _extension(workspace, "commands", _COMMANDS)
    provider = _Provider([])

    if mode == "print":
        code, stdout, errors = _print(workspace, provider, "/note two words")
        assert stdout == ""
    else:
        code, records, errors = _json(workspace, provider, "/note two words")
        assert [record["type"] for record in records] == ["session"]

    assert code == 0, errors
    assert provider.requests == []
    assert (directory / "note.txt").read_text() == repr("two words")
    # SLH-11: `ctx.ui.notify` does nothing without a UI.
    assert "noted" not in errors


@pytest.mark.parametrize("mode", ["print", "json"])
def test_a_failing_extension_command_is_still_handled(
    tmp_path: Path, mode: str
) -> None:
    # SLH-07: Pi's `onError` of print and JSON mode, and there is no run.
    workspace = tmp_path / "work"
    _extension(workspace, "commands", _COMMANDS)
    provider = _Provider([])

    if mode == "print":
        code, stdout, errors = _print(workspace, provider, "/broken")
        assert stdout == ""
    else:
        code, records, errors = _json(workspace, provider, "/broken")
        assert [record["type"] for record in records] == ["session"]

    assert (code, provider.requests) == (0, [])
    assert errors == "Extension error (command:broken): no luck\n"


@pytest.mark.parametrize(
    "prompt",
    [
        "/model stub/other",
        "/exit",
        "/compact",
        "/nosuch thing",
        "!echo not-run",
        " /mcp",
        "/skill:nosuch do it",
    ],
)
def test_other_slash_texts_are_the_prompt(tmp_path: Path, prompt: str) -> None:
    # SLH-08: the built-in commands belong to the interactive session; an
    # unknown name, a shell line and an unknown skill are not commands
    # either.
    workspace = tmp_path / "work"
    _extension(workspace, "commands", _COMMANDS)
    provider = _Provider([_result(final_text="answer")])

    code, stdout, errors = _print(workspace, provider, prompt)

    assert (code, stdout) == (0, "answer\n"), errors
    assert [request.user_prompt for request in provider.requests] == [prompt]
    assert "not handled" not in errors


def test_json_mode_sends_an_unknown_slash_name_to_the_model(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    provider = _Provider([_result(final_text="answer")])

    code, records, errors = _json(workspace, provider, "/nosuch thing")

    assert code == 0, errors
    types = [record["type"] for record in records]
    assert types[:2] == ["session", "agent_start"]
    assert types[-2:] == ["agent_end", "agent_settled"]
    user = [
        record["message"]
        for record in records
        if record["type"] == "message_end" and record["message"]["role"] == "user"
    ]
    assert user == [
        {"role": "user", "content": [{"type": "text", "text": "/nosuch thing"}]}
    ]


def test_a_prompt_template_and_a_skill_are_expanded(tmp_path: Path) -> None:
    # SLH-09.
    workspace = tmp_path / "work"
    _resource(
        workspace / ".pipy" / "templates", "review", "Please review $ARGUMENTS.\n"
    )
    _resource(workspace / ".pipy" / "skills", "lint", "Apply the lint rules.\n")

    provider = _Provider([_result(final_text="reviewed")])
    code, stdout, errors = _print(workspace, provider, "/review the diff")
    assert (code, stdout) == (0, "reviewed\n"), errors
    assert provider.requests[0].user_prompt.strip() == "Please review the diff."

    provider = _Provider([_result(final_text="linted")])
    code, stdout, errors = _print(workspace, provider, "/skill:lint now")
    assert (code, stdout) == (0, "linted\n"), errors
    sent = provider.requests[0].user_prompt
    assert sent.startswith('<skill name="lint"')
    assert "Apply the lint rules." in sent
    assert sent.endswith("now")


_HOOKS = """from pathlib import Path
from pipy_harness.extensions import InputHandled, InputTransform


def activate(api):
    @api.on('input')
    def hook(event, ctx):
        with open(Path(__file__).with_name('seen.txt'), 'a') as f:
            f.write(event.text + '\\n')
        if event.text == 'skip':
            return InputHandled()
        if event.text == 'go':
            return InputTransform(text='/review the diff')
"""


def test_input_hooks_run_before_expansion(tmp_path: Path) -> None:
    # SLH-09: the `input` hooks see the template invocation as sent, and a
    # template their text names is expanded.
    workspace = tmp_path / "work"
    directory = _extension(workspace, "hooks", _HOOKS)
    _resource(
        workspace / ".pipy" / "templates", "review", "Please review $ARGUMENTS.\n"
    )

    provider = _Provider([_result(final_text="one"), _result(final_text="two")])
    code, stdout, errors = _print(workspace, provider, "/review this")
    assert (code, stdout) == (0, "one\n"), errors
    code, stdout, errors = _print(workspace, provider, "go")
    assert (code, stdout) == (0, "two\n"), errors

    assert [request.user_prompt.strip() for request in provider.requests] == [
        "Please review this.",
        "Please review the diff.",
    ]
    assert (directory / "seen.txt").read_text() == "/review this\ngo\n"


@pytest.mark.parametrize("mode", ["print", "json"])
def test_a_prompt_an_input_hook_handled_starts_no_run(
    tmp_path: Path, mode: str
) -> None:
    # SLH-10 for Pi's `handled` input result: as a handled command.
    workspace = tmp_path / "work"
    _extension(workspace, "hooks", _HOOKS)
    provider = _Provider([])

    if mode == "print":
        code, stdout, errors = _print(workspace, provider, "skip")
        assert stdout == ""
    else:
        code, records, errors = _json(workspace, provider, "skip")
        assert [record["type"] for record in records] == ["session"]

    assert (code, provider.requests) == (0, []), errors


def test_a_template_named_like_a_builtin_command_is_expanded(tmp_path: Path) -> None:
    # SLH-09: a prompt knows no built-in commands, so nothing hides the
    # template (Pi `expandPromptTemplate`).
    workspace = tmp_path / "work"
    _resource(workspace / ".pipy" / "templates", "model", "Describe $ARGUMENTS.\n")
    provider = _Provider([_result(final_text="described")])

    code, stdout, errors = _print(workspace, provider, "/model the diff")

    assert (code, stdout) == (0, "described\n"), errors
    assert provider.requests[0].user_prompt.strip() == "Describe the diff."


def test_an_extension_command_comes_before_a_template_of_its_name(
    tmp_path: Path,
) -> None:
    # SLH-07, SLH-09: Pi tries the extension commands first.
    workspace = tmp_path / "work"
    directory = _extension(workspace, "commands", _COMMANDS)
    _resource(workspace / ".pipy" / "templates", "note", "A template.\n")
    provider = _Provider([])

    code, stdout, errors = _print(workspace, provider, "/note x")

    assert (code, stdout, provider.requests) == (0, "", []), errors
    assert (directory / "note.txt").read_text() == repr("x")


def test_mcp_is_no_command_when_mcp_is_off(tmp_path: Path) -> None:
    # EXP-11 for a prompt: the name is nothing special, so the model gets it.
    from pipy_harness.native.resource_loading import RuntimeResourceOptions

    workspace = tmp_path / "work"
    workspace.mkdir()
    provider = _Provider([_result(final_text="answer")])
    adapter = CodingSessionAdapter(
        provider=provider,
        tool_registry=production_tool_registry(),
        settings_manager=_settings(workspace),
        resource_options=RuntimeResourceOptions(no_mcp=True),
    )
    stdout, errors = io.StringIO(), io.StringIO()

    code = run_print_mode(
        adapter=adapter,
        prompt="/mcp",
        cwd=workspace,
        stdout=stdout,
        error_stream=errors,
    )

    assert (code, stdout.getvalue()) == (0, "answer\n"), errors.getvalue()
    assert [request.user_prompt for request in provider.requests] == ["/mcp"]
