"""Commands in the queued input of an RPC client, and ``get_commands``
(goal-check E17, row SLH-13).

Pi's ``steer`` and ``follow_up`` refuse a text that names an extension
command (``_throwIfExtensionCommand``) and expand skills and templates when
they queue a message (``_queueUserInput``); a ``prompt`` that names an
extension command runs it at once, also during a run (``prompt``); and
``get_commands`` lists the extension commands, prompt templates and skills
(``modes/rpc/rpc-mode.ts``).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from test_native_automation_prompt_dispatch import _COMMANDS, _mcp_json, _resource
from test_native_automation_rpc_prompt_dispatch import _Client
from test_native_extension_tool_metadata import _result
from test_native_mcp_session import _extension, _Provider, _server

from pipy_harness.native.models import ProviderResult


@pytest.fixture()
def connect(tmp_path: Path) -> Iterator[Callable[[_Provider], _Client]]:
    clients: list[_Client] = []

    def start(provider: _Provider) -> _Client:
        clients.append(_Client(tmp_path / "work", provider))
        return clients[-1]

    yield start
    for client in clients:
        if client._thread.is_alive():
            client.close()


def _holding_provider(
    *rest: ProviderResult,
) -> tuple[_Provider, threading.Event, threading.Event]:
    entered, release = threading.Event(), threading.Event()

    def hold() -> None:
        entered.set()
        assert release.wait(10.0)

    return _Provider([hold, *rest]), entered, release


@pytest.mark.parametrize("kind", ["steer", "follow_up"])
@pytest.mark.parametrize("text", ["/note now", "/mcp", "/broken"])
def test_queued_input_that_names_an_extension_command_is_refused(
    tmp_path: Path, connect, kind: str, text: str
) -> None:
    workspace = tmp_path / "work"
    directory = _extension(workspace, "commands", _COMMANDS)
    entry, _record = _server(tmp_path / "servers")
    _mcp_json(workspace, {"docs": entry})
    provider = _Provider([])
    client = connect(provider)

    client.send(id="q", type=kind, message=text)
    response = client.response("q")
    client.close()

    name = text[1:].split(" ")[0]
    assert response == {
        "id": "q",
        "type": "response",
        "command": kind,
        "success": False,
        "error": (
            f'Extension command "/{name}" cannot be queued. Use prompt() or '
            "execute the command when not streaming."
        ),
    }
    assert provider.requests == []
    assert not (directory / "note.txt").exists()


def test_a_prompt_that_names_a_command_runs_at_once_during_a_run(
    tmp_path: Path, connect
) -> None:
    workspace = tmp_path / "work"
    directory = _extension(workspace, "commands", _COMMANDS)
    provider, entered, release = _holding_provider(_result(final_text="one"))
    client = connect(provider)

    client.send(id="run", type="prompt", message="first")
    assert client.response("run")["data"] == {"disposition": "started"}
    assert entered.wait(10.0)
    client.send(id="c", type="prompt", message="/note while running")
    records = client.until(lambda record: record.get("id") == "c")
    # The command ran while the run was still held.
    assert (directory / "note.txt").read_text() == repr("while running")
    assert records[-1]["data"] == {"disposition": "handled"}
    assert {"type": "extension_ui_request", "method": "notify"}.items() <= next(
        record for record in records if record.get("method") == "notify"
    ).items()
    release.set()
    client.until(lambda record: record.get("type") == "agent_settled")
    client.close()

    assert [request.user_prompt for request in provider.requests] == ["first"]


def test_a_failing_command_during_a_run_is_an_extension_error(
    tmp_path: Path, connect
) -> None:
    workspace = tmp_path / "work"
    _extension(workspace, "commands", _COMMANDS)
    provider, entered, release = _holding_provider(_result(final_text="one"))
    client = connect(provider)

    client.send(id="run", type="prompt", message="first")
    client.response("run")
    assert entered.wait(10.0)
    client.send(id="c", type="prompt", message="/broken")
    records = client.until(lambda record: record.get("id") == "c")
    release.set()
    client.close()

    assert records[-2:] == [
        {
            "type": "extension_error",
            "extensionPath": "command:broken",
            "event": "command",
            "error": "no luck",
        },
        {
            "id": "c",
            "type": "response",
            "command": "prompt",
            "success": True,
            "data": {"disposition": "handled"},
        },
    ]


_ASKING = """import time
from pathlib import Path


def activate(api):
    def ask(ctx, args):
        answer = ctx.ui.input('Name?')
        time.sleep(0.3)
        Path(__file__).with_name('asked.txt').write_text(repr(answer))

    api.register_command('ask', 'Ask for a name', ask)
"""


def test_a_command_run_during_a_run_is_answered_before_the_session_ends(
    tmp_path: Path, connect
) -> None:
    workspace = tmp_path / "work"
    directory = _extension(workspace, "asking", _ASKING)
    provider, entered, release = _holding_provider(_result(final_text="one"))
    client = connect(provider)

    client.send(id="run", type="prompt", message="first")
    client.response("run")
    assert entered.wait(10.0)
    client.send(id="c", type="prompt", message="/ask")
    client.until(lambda record: record.get("method") == "input")
    release.set()
    # The input ends while the command waits for its dialog.
    rest = client.close()

    assert (directory / "asked.txt").read_text() == "None"
    assert {
        "id": "c",
        "type": "response",
        "command": "prompt",
        "success": True,
        "data": {"disposition": "handled"},
    } in rest


def test_queued_skills_and_templates_are_expanded(tmp_path: Path, connect) -> None:
    workspace = tmp_path / "work"
    _resource(
        workspace / ".pipy" / "templates", "review", "Please review $ARGUMENTS.\n"
    )
    _resource(workspace / ".pipy" / "skills", "lint", "Apply the lint rules.\n")
    provider, entered, release = _holding_provider(
        _result(final_text="two"),
        _result(final_text="three"),
        _result(final_text="four"),
    )
    client = connect(provider)

    client.send(id="run", type="prompt", message="first")
    client.response("run")
    assert entered.wait(10.0)
    client.send(id="s", type="steer", message="/review the diff")
    steered = client.until(lambda record: record.get("type") == "queue_update")[-1]
    client.send(id="f", type="follow_up", message="/skill:lint now")
    client.send(id="p", type="prompt", message="/review again")
    assert client.response("p")["data"] == {"disposition": "queued"}
    release.set()
    rest = client.close()

    assert [text.strip() for text in steered["steering"]] == ["Please review the diff."]
    sent = [request.user_prompt.strip() for request in provider.requests[1:]]
    assert "Please review the diff." in sent
    assert "Please review again." in sent
    assert any(text.startswith('<skill name="lint"') for text in sent)
    assert [record for record in rest if record.get("success") is False] == []


def test_queued_text_with_a_leading_space_is_not_expanded(
    tmp_path: Path, connect
) -> None:
    workspace = tmp_path / "work"
    _resource(workspace / ".pipy" / "templates", "review", "Please review.\n")
    provider, entered, release = _holding_provider(
        _result(final_text="two"), _result(final_text="three")
    )
    client = connect(provider)

    client.send(id="run", type="prompt", message="first")
    client.response("run")
    assert entered.wait(10.0)
    client.send(id="s", type="steer", message=" /review")
    steered = client.until(lambda record: record.get("type") == "queue_update")[-1]
    client.send(id="p", type="prompt", message=" /review")
    assert client.response("p")["data"] == {"disposition": "queued"}
    release.set()
    client.close()

    assert steered["steering"] == [" /review"]
    assert [request.user_prompt for request in provider.requests[1:]].count(
        " /review"
    ) == 2


def test_get_commands_lists_commands_templates_and_skills(
    tmp_path: Path, connect
) -> None:
    workspace = tmp_path / "work"
    _extension(workspace, "commands", _COMMANDS)
    entry, _record = _server(tmp_path / "servers")
    _mcp_json(workspace, {"docs": entry})
    _resource(workspace / ".pipy" / "templates", "review", "Please review.\n")
    _resource(workspace / ".pipy" / "skills", "lint", "Apply the lint rules.\n")
    client = connect(_Provider([]))

    client.send(id="g", type="get_commands")
    commands = client.response("g")["data"]["commands"]
    client.close()

    by_name = {command["name"]: command for command in commands}
    assert by_name["note"]["source"] == "extension"
    assert by_name["note"]["description"] == "Write a note"
    assert by_name["broken"]["source"] == "extension"
    assert by_name["mcp"]["source"] == "extension"
    assert by_name["review"] == {
        "name": "review",
        "description": "review resource",
        "source": "prompt",
        "sourceInfo": {"path": ".pipy/templates/review.md"},
    }
    assert by_name["skill:lint"]["source"] == "skill"
    assert by_name["skill:lint"]["description"] == "lint resource"
    # Pi lists them in this order: extension commands, templates, skills.
    sources = [command["source"] for command in commands]
    assert sources == sorted(sources, key=["extension", "prompt", "skill"].index)


# -- R3 (goal check r2, S6, S7): the `input` hooks of queued input ----------

_QUEUE_HOOKS = """from pathlib import Path
from pipy_harness.extensions import InputHandled, InputTransform


def activate(api):
    @api.on('input')
    def hook(event, ctx):
        with open(Path(__file__).with_name('seen.txt'), 'a') as f:
            f.write(event.text + '\\n')
        if event.text == 'skip':
            return InputHandled()
        if event.text == 'secret':
            return InputTransform(text='/review the redacted diff')
        if event.text == 'ask':
            answer = ctx.ui.confirm('Queue it?', 'really')
            return InputTransform(text='asked ' + repr(answer))
"""


@pytest.mark.parametrize("kind", ["steer", "follow_up", "prompt"])
def test_queued_input_meets_the_input_hooks_before_expansion(
    tmp_path: Path, connect, kind: str
) -> None:
    # Pi `_queueUserInput` and `prompt`: the hooks see the text as sent, a
    # hook may handle it, and what the hooks return is expanded and queued;
    # the hooks do not run again when the message runs.
    workspace = tmp_path / "work"
    directory = _extension(workspace, "hooks", _QUEUE_HOOKS)
    _resource(
        workspace / ".pipy" / "templates", "review", "Please review $ARGUMENTS.\n"
    )
    provider, entered, release = _holding_provider(
        _result(final_text="one"), _result(final_text="two")
    )
    client = connect(provider)

    client.send(id="run", type="prompt", message="first")
    client.response("run")
    assert entered.wait(10.0)
    client.send(id="handled", type=kind, message="skip")
    assert client.response("handled")["data"] == {"disposition": "handled"}
    client.send(id="q", type=kind, message="secret")
    assert client.response("q")["data"] == {"disposition": "queued"}
    release.set()
    client.until(lambda record: record.get("type") == "agent_settled")
    rest = client.close()

    assert client.errors.getvalue() == ""
    assert [record for record in rest if record.get("success") is False] == []
    sent = [request.user_prompt.strip() for request in provider.requests]
    assert len(sent) == 2
    assert sent[-1] == "Please review the redacted diff."
    assert "skip" not in " ".join(sent)
    assert (directory / "seen.txt").read_text().splitlines() == [
        "first",
        "skip",
        "secret",
    ]


def test_a_dialog_of_a_queued_input_hook_gets_its_answer(
    tmp_path: Path, connect
) -> None:
    # The hooks of queued input run beside the command thread, which goes on
    # reading the client's lines: the answer reaches the dialog.
    workspace = tmp_path / "work"
    _extension(workspace, "hooks", _QUEUE_HOOKS)
    provider, entered, release = _holding_provider(
        _result(final_text="one"), _result(final_text="two")
    )
    client = connect(provider)

    client.send(id="run", type="prompt", message="first")
    client.response("run")
    assert entered.wait(10.0)
    client.send(id="s", type="steer", message="ask")
    request = client.until(lambda record: record.get("method") == "confirm")[-1]
    client.send(type="extension_ui_response", id=request["id"], confirmed=True)
    assert client.response("s")["data"] == {"disposition": "queued"}
    release.set()
    client.until(lambda record: record.get("type") == "agent_settled")
    client.close()

    assert client.errors.getvalue() == ""
    assert provider.requests[-1].user_prompt.strip() == "asked True"


def test_input_after_a_message_that_meets_the_hooks_waits_for_it(
    tmp_path: Path, connect
) -> None:
    # While a queued message's hook waits for a dialog, a later prompt does
    # not overtake it, and a session transition waits until it is queued.
    workspace = tmp_path / "work"
    _extension(workspace, "hooks", _QUEUE_HOOKS)
    provider, entered, release = _holding_provider(
        _result(final_text="one"), _result(final_text="two"), _result(final_text="3")
    )
    client = connect(provider)

    client.send(id="run", type="prompt", message="first")
    client.response("run")
    assert entered.wait(10.0)
    client.send(id="f", type="follow_up", message="ask")
    request = client.until(lambda record: record.get("method") == "confirm")[-1]
    release.set()
    client.until(lambda record: record.get("type") == "agent_settled")
    client.send(id="n", type="new_session")
    client.send(id="p", type="prompt", message="later")
    client.send(type="extension_ui_response", id=request["id"], confirmed=True)
    records = client.until(lambda record: record.get("id") == "p")
    client.until(lambda record: record.get("type") == "agent_settled")
    client.close()

    answered = [record["id"] for record in records if record.get("type") == "response"]
    assert answered.index("f") < answered.index("n")
    assert [request.user_prompt.strip() for request in provider.requests] == [
        "first",
        "asked True",
        "later",
    ]


def test_a_prompt_admitted_as_the_run_ends_is_expanded_once(
    tmp_path: Path, connect, monkeypatch
) -> None:
    # S7: the run ends between the prompt's queueing and its admission, so
    # the prompt runs at once; it was expanded when it was queued and is not
    # expanded (or shown to the hooks) again.
    workspace = tmp_path / "work"
    directory = _extension(workspace, "hooks", _QUEUE_HOOKS)
    _resource(workspace / ".pipy" / "templates", "outer", "/inner $ARGUMENTS\n")
    _resource(workspace / ".pipy" / "templates", "inner", "Inner $ARGUMENTS.\n")
    provider, entered, release = _holding_provider(
        _result(final_text="one"), _result(final_text="two")
    )
    client = connect(provider)
    client.send(id="run", type="prompt", message="first")
    client.response("run")
    assert entered.wait(10.0)
    bridge_type = type(client._server._bridge)
    admit = bridge_type.admit_follow_up

    def admit_after_the_run_ended(self, content):  # type: ignore[no-untyped-def]
        release.set()
        deadline = time.monotonic() + 10.0
        while self.snapshot().reservation is not None:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        return admit(self, content)

    monkeypatch.setattr(bridge_type, "admit_follow_up", admit_after_the_run_ended)
    client.send(id="p", type="prompt", message="/outer x")
    assert client.response("p")["data"] == {"disposition": "queued"}
    client.until(lambda record: record.get("type") == "agent_settled")
    monkeypatch.undo()
    client.close()

    assert provider.requests[-1].user_prompt.strip() == "/inner x"
    assert (directory / "seen.txt").read_text().splitlines() == [
        "first",
        "/outer x",
    ]


def test_a_command_prompt_does_not_wait_for_a_queued_message_s_hooks(
    tmp_path: Path, connect
) -> None:
    # Pi `prompt` runs an extension command at once, also while a queued
    # message's hook waits for a dialog.
    workspace = tmp_path / "work"
    _extension(workspace, "hooks", _QUEUE_HOOKS)
    directory = _extension(workspace, "commands", _COMMANDS)
    provider, entered, release = _holding_provider(
        _result(final_text="one"), _result(final_text="two")
    )
    client = connect(provider)

    client.send(id="run", type="prompt", message="first")
    client.response("run")
    assert entered.wait(10.0)
    client.send(id="f", type="follow_up", message="ask")
    request = client.until(lambda record: record.get("method") == "confirm")[-1]
    client.send(id="c", type="prompt", message="/note meanwhile")
    assert client.response("c")["data"] == {"disposition": "handled"}
    assert (directory / "note.txt").read_text() == repr("meanwhile")
    client.send(type="extension_ui_response", id=request["id"], confirmed=False)
    assert client.response("f")["data"] == {"disposition": "queued"}
    release.set()
    client.until(lambda record: record.get("type") == "agent_settled")
    client.close()

    assert client.errors.getvalue() == ""
