"""Real Bash selector/finalizer/path ownership through retained output faults."""

import os
import shlex
import sys
import threading
from dataclasses import replace

import pytest
from generation_support import Wire, admit, definition, seed
from test_tool_lifecycle import independent_opener

from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.scheduler import TaskScheduler
from pipy_harness.native.durable.session_core import Session
from pipy_harness.native.durable.sqlite import open_sqlite_storage
from pipy_harness.native.durable.storage import EntryQuery
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)
from pipy_harness.native.durable.tool_progress import RetainedToolProgress
from pipy_harness.native.tools import bash
from pipy_harness.native.tools.base import ToolDefinition, ToolRequest


def test_actual_bash_void_fault_keeps_selector_finalizer_and_path_owned(
    tmp_path, monkeypatch
):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    fault, later, finalizer, release, finalized = (threading.Event() for _ in range(5))
    processes, epochs = [], []
    original_popen = bash.subprocess.Popen
    original_update = RetainedToolProgress._capture_update
    original_dispatch = RetainedToolProgress._dispatch

    def popen(argv, *args, **kwargs):
        process = original_popen(argv, *args, **kwargs)
        if argv[0] == "/bin/bash":
            processes.append(process)
        return process

    def injected(self, change, **kwargs):
        if not fault.is_set():
            assert len(processes) == 1 and processes[0].poll() is None
            assert (tmp_path / "effect").read_text() == "one\n"
            with self._condition:
                epochs.append(self._epoch)
            fault.set()
            raise RuntimeError("injected private gate failure while child is alive")
        return original_update(self, change, **kwargs)

    def observed_dispatch(self, capture):
        was_later = fault.is_set()
        try:
            return original_dispatch(self, capture)
        finally:
            if was_later:
                with self._condition:
                    epochs.append(self._epoch)
                later.set()

    monkeypatch.setattr(RetainedToolProgress, "_dispatch", observed_dispatch)
    monkeypatch.setattr(bash.subprocess, "Popen", popen)
    monkeypatch.setattr(RetainedToolProgress, "_capture_update", injected)
    script = (
        "import os,time; from pathlib import Path; "
        "Path('effect').write_text('one\\n'); "
        "Path('pid').write_text(str(os.getpid())); "
        "print('live output',flush=True)\n"
        "while not Path('again').exists(): time.sleep(0.01)\n"
        "print('later output',flush=True); time.sleep(60)"
    )
    command = "exec " + shlex.quote(sys.executable) + " -c " + shlex.quote(script)

    class Shell:
        definition = ToolDefinition(
            "shell",
            "shell",
            {"type": "object", "properties": {"x": {"type": "integer"}}},
        )

        def invoke(self, request, context):
            try:
                return bash.BashTool(shell_path="/bin/bash").invoke(
                    ToolRequest(request.tool_request_id, "bash", {"command": command}),
                    context,
                )
            finally:
                finalizer.set()
                assert release.wait(10)
                finalized.set()

    path = tmp_path / "session.sqlite"
    session = Session(open_sqlite_storage(str(path), mode="create"))
    port = Shell()
    tool = define_tool_task(
        {"shell": ToolRegistration(port, "fixture.shell", "1")}, workspace_root=tmp_path
    )
    generation = definition(tmp_path, Wire(("shell",)))
    cid, _, _ = admit(
        session,
        generation,
        snapshot_provider_request(
            replace(seed(tmp_path).request, available_tools=(port.definition,))
        ),
    )
    scheduler = TaskScheduler(session, [generation, tool])
    try:
        scheduler.open()
        scheduler.resume()
        assert fault.wait(10)
        assert int((tmp_path / "pid").read_text()) == processes[0].pid
        assert processes[0].poll() is None
        independent_opener(
            path, tmp_path, "alive-gate-fault", "refused", invoke_held=True
        )
        (tmp_path / "again").touch()
        assert later.wait(10)
        assert processes[0].poll() is None
        session.request_close()
        assert finalizer.wait(10)
        # This is the actual Popen reaped by Bash, not the executor future.
        assert processes[0].returncode is not None
        assert not finalized.is_set()
        independent_opener(path, tmp_path, "bash-finalizer", "refused")
        release.set()
        session.wait_closed(timeout=10)
        assert finalized.is_set() and not scheduler._thread.is_alive()
        assert len(epochs) >= 2 and set(epochs) == {0}
        assert (tmp_path / "effect").read_text() == "one\n"
        independent_opener(path, tmp_path, "bash-drained", "opened")
        with pytest.raises(ProcessLookupError):
            os.killpg(processes[0].pid, 0)
        reopened = open_sqlite_storage(str(path), mode="open")
        try:
            entries = reopened.scan_entries(
                EntryQuery({"conversationId": cid}), 100
            ).items
            assert not any(e.to_tree()["kind"] == "pi.tool-result" for e in entries)
        finally:
            reopened.close()
    finally:
        release.set()
        session.close(timeout=10)
