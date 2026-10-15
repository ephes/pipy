"""An extension's ``ctx.ui`` in RPC mode (goal-check E13, row SLH-11).

Pi gives every extension an RPC UI context (``createExtensionUIContext`` in
``modes/rpc/rpc-mode.ts``, ``docs/rpc-extension-ui.md``): ``ctx.hasUI`` is
true, the dialogs ``select``, ``confirm``, ``input`` and ``editor`` are
``extension_ui_request`` lines answered by ``extension_ui_response`` lines,
a dialog's ``timeout`` ends it with its default, and ``setStatus``,
``setWidget``, ``setTitle`` and ``set_editor_text`` are requests that need
no answer.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from test_native_automation_rpc import _RpcClient
from test_native_automation_rpc_prompt_dispatch import _Client, _without_id
from test_native_automation_rpc_ui import (
    _ask_on_a_thread,
    _ui,
    _wait_for_request,
    _written,
)
from test_native_mcp_session import _extension, _Provider

from pipy_harness.native.extension_ui import _CollectingUi
from pipy_harness.native.resource_loading import RuntimeResourceOptions


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


_ASK = """import json
from pathlib import Path


def activate(api):
    def ask(ctx, args):
        answers = {
            'has_ui': ctx.has_ui,
            'select': ctx.ui.select('Pick', ['a', 'b']),
            'confirm': ctx.ui.confirm('Sure?', 'Really', timeout=60000),
            'input': ctx.ui.input('Name', 'type here'),
            'editor': ctx.ui.editor('Edit', 'old'),
        }
        ctx.ui.set_status('ask', 'asked')
        ctx.ui.set_widget('ask', ['line 1', 'line 2'], placement='below_editor')
        ctx.ui.set_widget('gone', None)
        ctx.ui.set_title('pipy - asked')
        ctx.ui.set_editor_text('draft')
        ctx.ui.set_working_message('busy')
        Path(__file__).with_name('answers.json').write_text(json.dumps(answers))

    api.register_command('ask', 'Ask the client', ask)
"""

_ANSWERS: dict[str, dict[str, Any]] = {
    "select": {"value": "b"},
    "confirm": {"confirmed": True},
    "input": {"value": "Ada"},
    "editor": {"cancelled": True},
}


def test_an_extension_command_uses_the_client_as_its_ui(
    tmp_path: Path, connect
) -> None:
    workspace = tmp_path / "work"
    directory = _extension(workspace, "ask", _ASK)
    client = connect(_Provider([]))

    client.send(id="c", type="prompt", message="/ask")
    requests: list[dict[str, Any]] = []
    while True:
        record = client.until(lambda record: True)[-1]
        if record.get("id") == "c":
            assert record["data"] == {"disposition": "handled"}
            break
        assert record["type"] == "extension_ui_request", record
        requests.append(_without_id(record))
        answer = _ANSWERS.get(record["method"])
        if answer is not None:
            client.send(type="extension_ui_response", id=record["id"], **answer)
    client.close()

    assert json.loads((directory / "answers.json").read_text()) == {
        "has_ui": True,
        "select": "b",
        "confirm": True,
        "input": "Ada",
        "editor": None,
    }
    request = {"type": "extension_ui_request"}
    assert requests == [
        {**request, "method": "select", "title": "Pick", "options": ["a", "b"]},
        {
            **request,
            "method": "confirm",
            "title": "Sure?",
            "message": "Really",
            "timeout": 60000,
        },
        {**request, "method": "input", "title": "Name", "placeholder": "type here"},
        {**request, "method": "editor", "title": "Edit", "prefill": "old"},
        {**request, "method": "setStatus", "statusKey": "ask", "statusText": "asked"},
        {
            **request,
            "method": "setWidget",
            "widgetKey": "ask",
            "widgetLines": ["line 1", "line 2"],
            "widgetPlacement": "belowEditor",
        },
        {
            **request,
            "method": "setWidget",
            "widgetKey": "gone",
            "widgetPlacement": "aboveEditor",
        },
        {**request, "method": "setTitle", "title": "pipy - asked"},
        {**request, "method": "set_editor_text", "text": "draft"},
    ]


def test_a_dialog_timeout_ends_it_with_its_default() -> None:
    # Pi `createDialogPromise`: the timeout resolves with the default value,
    # and a late response has nobody waiting.
    ui, stream = _ui()
    driver = ui.extension_driver()
    collecting = _CollectingUi(True, ui_driver=driver)
    thread, answers = _ask_on_a_thread(
        lambda: collecting.confirm("Sure?", "Really", timeout=100)
    )
    request = _wait_for_request(stream)
    thread.join(5.0)

    assert not thread.is_alive()
    assert answers == [False]
    assert request["timeout"] == 100
    ui.respond(
        {"type": "extension_ui_response", "id": request["id"], "confirmed": True}
    )
    assert collecting.select("Pick", ["a"], timeout=50) is None
    assert _written(stream)[-1]["timeout"] == 50


def test_a_widget_that_is_not_lines_of_text_is_not_sent() -> None:
    ui, stream = _ui()
    driver = ui.extension_driver()

    driver.set_widget("w", lambda tui, theme: None, "above_editor")
    driver.set_status("s", None)

    assert [_without_id(record) for record in _written(stream)] == [
        {"type": "extension_ui_request", "method": "setStatus", "statusKey": "s"}
    ]
    assert driver.get_editor_text() == ""
    assert driver.apply_theme("dark") == (
        False,
        "Theme switching not supported in RPC mode",
    )


def test_the_terminal_dialogs_take_no_timeout() -> None:
    # A driver without `dialog_timeouts` (the terminal's) is asked as before.
    asked: list[tuple[Any, ...]] = []

    class _Terminal:
        def select(self, title: str, options: Any) -> str:
            asked.append((title, tuple(options)))
            return "a"

    collecting = _CollectingUi(True, ui_driver=_Terminal())  # type: ignore[arg-type]

    assert collecting.select("Pick", ["a"], timeout=10) == "a"
    assert asked == [("Pick", ("a",))]


_START = """from pathlib import Path


def activate(api):
    @api.on('session_start')
    def started(event, ctx):
        Path(__file__).with_name('has_ui.txt').write_text(repr(ctx.has_ui))
        ctx.ui.set_status('start', 'ready')
"""


def test_a_session_start_hook_has_the_client_as_its_ui(tmp_path: Path, connect) -> None:
    workspace = tmp_path / "work"
    directory = _extension(workspace, "start", _START)
    client = connect(_Provider([]))

    status = client.until(lambda record: record.get("method") == "setStatus")[-1]
    client.close()

    assert (directory / "has_ui.txt").read_text() == "True"
    assert (status["statusKey"], status["statusText"]) == ("start", "ready")


_STARTUP_CONFIRM = """from pathlib import Path


def activate(api):
    @api.on('session_start')
    def started(event, ctx):
        answer = ctx.ui.confirm('Start?', 'Go on')
        Path(__file__).with_name('start.txt').write_text(repr(answer))

    @api.on('session_before_switch')
    def switching(event, ctx):
        answer = ctx.ui.confirm('Switch?', 'Leave')
        Path(__file__).with_name('switch.txt').write_text(repr(answer))
"""


def _answer_next_confirm(client: _RpcClient, title: str, confirmed: bool) -> None:
    request = client.wait_for(lambda record: record.get("method") == "confirm")
    assert request["title"] == title
    client.send(
        {"type": "extension_ui_response", "id": request["id"], "confirmed": confirmed}
    )


def test_dialogs_of_startup_and_transition_hooks_get_their_answers(
    tmp_path: Path,
) -> None:
    # The answers are read while `session_start` runs, before the session is
    # ready, and while a transition hook runs on the thread of the commands.
    extension = tmp_path / ".pipy" / "extensions" / "hooks.py"
    extension.parent.mkdir(parents=True)
    extension.write_text(_STARTUP_CONFIRM, encoding="utf-8")
    client = _RpcClient(
        tmp_path,
        persist_tree=True,
        resource_options=RuntimeResourceOptions(extension_paths=(extension,)),
    )
    try:
        _answer_next_confirm(client, "Start?", True)
        client.send({"id": "n", "type": "new_session"})
        _answer_next_confirm(client, "Switch?", False)
        response = client.wait_for(lambda record: record.get("id") == "n")
    finally:
        client.close()

    assert response["success"] is True, response
    assert (extension.parent / "start.txt").read_text() == "True"
    assert (extension.parent / "switch.txt").read_text() == "False"


def test_an_answer_after_the_timeout_is_no_answer() -> None:
    ui, stream = _ui()
    thread, answers = _ask_on_a_thread(
        lambda: ui.confirm("Sure?", "Really", timeout=20)
    )
    request = _wait_for_request(stream)
    time.sleep(0.05)
    ui.respond(
        {"type": "extension_ui_response", "id": request["id"], "confirmed": True}
    )
    thread.join(5.0)

    assert answers == [False]
