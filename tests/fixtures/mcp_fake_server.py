"""A scripted MCP server for tests.

Standard library only, and no code shared with ``pipy_harness``: the server
must not check the client with the client's own parser. Run it as

    python -I tests/fixtures/mcp_fake_server.py --scenario <file>

It then speaks newline-delimited JSON-RPC on stdin and stdout. The scenario
engine (``ScenarioEngine``) writes through a channel object, so another
front end can wrap it.

The scenario file is one JSON object. Every key is optional:

- ``protocolVersion``, ``serverInfo``, ``capabilities``, ``instructions``:
  the answer to ``initialize``.
- ``tools``: the tool definitions of ``tools/list``. ``tools/call`` answers
  ``toolResults[name]`` when given, else one text block with the ``text``
  argument (or the arguments as JSON).
- ``resources``, ``resourceTemplates``: the lists. ``resources/read``
  answers ``{"contents": resourceContents[uri]}``.
- ``record``: path of a file that gets one JSON line per received message
  (``{"t": <monotonic seconds>, "received": <message>}``, or ``"raw"`` for a
  line that is not JSON) and per event (``{"t": ..., "event": <name>}``).
- ``startup``: steps run before the first message is read.
- ``methods``: ``{<method>: [steps]}``, run instead of the built-in answer.
- ``onStdinClose``: steps run when stdin ends. Default ``[{"exit": 0}]``. The
  server keeps running when the steps do not exit, so ``[]`` ignores it.
- ``onSigterm``: steps run on SIGTERM; ``[]`` ignores the signal. Without the
  key the signal ends the process as usual.
- ``orphanGraceSeconds``: how long the server lives on after its parent
  process ended or its stdin ended. Default 300. See "Orphans" below.

A step is an object with one of these keys:

- ``{"stdout": text}``, ``{"stderr": text}``: written as is; with
  ``"times": n`` the text is repeated.
- ``{"send": message}``: one JSON-RPC message (a notification such as
  ``notifications/tools/list_changed`` or ``notifications/message``, or a
  request to the client).
- ``{"progress": params}``: ``notifications/progress`` with the progress
  token of the current request.
- ``{"result": value}``, ``{"error": {"code", "message"}}``: the answer to the
  current request.
- ``{"default": true}``: the built-in answer to the current request.
- ``{"sleep": seconds}``.
- ``{"record": name}``: an event line in the record file.
- ``{"spawn": {"ignoreSigterm": bool, "inheritStdio": bool, "newSession":
  bool}}``: start a child process that sleeps for ever and print ``child
  <pid>`` on stderr once it is set up. Without ``inheritStdio`` the child's
  stdio is ``/dev/null``. With ``newSession`` the child leaves the server's
  process group (a session of its own), so group signals miss it. The
  child is this file run with ``--sleeper``, so its command line names the
  fixture and the scenario file too.
- ``{"exit": code}``: end the process at once.

The built-in method ``fixture/process`` answers with the server's ``pid``,
``pgid``, ``sid``, ``cwd`` and ``env``.

Orphans. A scenario can make the server ignore the end of stdin and SIGTERM,
and a step can sleep for minutes. When the process that started such a server
is killed, nothing ends the server. So the server ends itself
``orphanGraceSeconds`` after the first of these, with the event ``orphaned``
in the record:

- its parent process changed, which is how the end of the parent shows;
- its parent process was already pid 1 when it started (the parent ended
  before the server looked);
- its stdin ended. A client that closes stdin and is still there ends the
  server with signals long before the time is over. This also covers a parent
  that ended before the server looked where orphans do not go to pid 1; a
  step that sleeps delays it, since stdin is not read meanwhile.

A ``spawn`` child ends itself by the first two rules. The time is long, so a
test still sees whether the client ended the server; it only keeps a failed
or interrupted run from leaving processes behind for good.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

_DEFAULT_ORPHAN_GRACE_SECONDS = 300.0


class OrphanWatch:
    """Ends this process ``grace`` seconds after it was left alone.

    A thread watches the parent process; ``left_alone()`` is for what the
    process notices itself. The time runs from the first of them.
    """

    def __init__(self, grace: float, on_orphaned: Callable[[], None]) -> None:
        self._grace = grace
        self._on_orphaned = on_orphaned
        self._lock = threading.Lock()
        self._counting = False
        parent = os.getppid()

        def watch() -> None:
            # Pid 1 as the parent: the parent ended before this started.
            while parent != 1 and os.getppid() == parent:
                time.sleep(0.2)
            self.left_alone()

        threading.Thread(target=watch, name="orphan-watch", daemon=True).start()

    def left_alone(self) -> None:
        with self._lock:
            if self._counting:
                return
            self._counting = True
        threading.Thread(target=self._end, name="orphan-end", daemon=True).start()

    def _end(self) -> None:
        time.sleep(self._grace)
        self._on_orphaned()
        os._exit(0)


def sleep_for_ever(ignore_sigterm: bool, ready: int, grace: float) -> None:
    """The child of a ``spawn`` step."""

    if ignore_sigterm:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    OrphanWatch(grace, lambda: None)
    os.write(ready, b"1")
    os.close(ready)
    while True:
        time.sleep(1)


class StdioChannel:
    """Writes to the process's own stdout and stderr, unbuffered."""

    def send(self, message: dict[str, Any]) -> None:
        self.stdout(json.dumps(message, separators=(",", ":")) + "\n")

    def stdout(self, text: str) -> None:
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.buffer.flush()

    def stderr(self, text: str) -> None:
        sys.stderr.buffer.write(text.encode("utf-8"))
        sys.stderr.buffer.flush()


class Recorder:
    """Appends JSON lines to the record file with single writes.

    No lock, so it can be used from a signal handler.
    """

    def __init__(self, path: str | None) -> None:
        self._fd = (
            os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            if path
            else None
        )

    def write(self, **fields: Any) -> None:
        if self._fd is None:
            return
        line = json.dumps({"t": time.monotonic(), **fields}) + "\n"
        os.write(self._fd, line.encode("utf-8"))


class ScenarioEngine:
    def __init__(
        self,
        scenario: dict[str, Any],
        channel: StdioChannel,
        recorder: Recorder,
        scenario_path: str | None = None,
    ) -> None:
        self.scenario = scenario
        self.scenario_path = scenario_path
        self.channel = channel
        self.recorder = recorder
        self._steps = {
            "stdout": self._step_stdout,
            "stderr": self._step_stderr,
            "send": self._step_send,
            "progress": self._step_progress,
            "result": self._step_result,
            "error": self._step_error,
            "default": self._step_default,
            "sleep": self._step_sleep,
            "record": self._step_record,
            "spawn": self._step_spawn,
            "exit": self._step_exit,
        }
        self._defaults = {
            "initialize": self._initialize,
            "ping": lambda params: {},
            "tools/list": lambda params: {"tools": scenario.get("tools", [])},
            "tools/call": self._call_tool,
            "resources/list": lambda params: {
                "resources": scenario.get("resources", [])
            },
            "resources/templates/list": lambda params: {
                "resourceTemplates": scenario.get("resourceTemplates", [])
            },
            "resources/read": self._read_resource,
            "fixture/process": self._process_info,
        }

    def run(
        self, steps: list[dict[str, Any]], request: dict[str, Any] | None = None
    ) -> None:
        for step in steps:
            for key, handler in self._steps.items():
                if key in step:
                    handler(step, request)
                    break
            else:
                raise ValueError(f"Unknown scenario step: {step!r}")

    def receive(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if not isinstance(method, str):
            return  # The client's answer to a request of ours.
        steps = self.scenario.get("methods", {}).get(method)
        if steps is None:
            self._answer_default(message)
        else:
            self.run(steps, message)

    # -- steps ------------------------------------------------------------

    def _step_stdout(self, step: dict[str, Any], request: Any) -> None:
        self.channel.stdout(step["stdout"] * step.get("times", 1))

    def _step_stderr(self, step: dict[str, Any], request: Any) -> None:
        self.channel.stderr(step["stderr"] * step.get("times", 1))

    def _step_send(self, step: dict[str, Any], request: Any) -> None:
        self.channel.send(step["send"])

    def _step_progress(self, step: dict[str, Any], request: Any) -> None:
        meta = ((request or {}).get("params") or {}).get("_meta") or {}
        if "progressToken" in meta:
            params = {"progressToken": meta["progressToken"], **step["progress"]}
            self.channel.send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": params,
                }
            )

    def _step_result(self, step: dict[str, Any], request: Any) -> None:
        self._respond(request, result=step["result"])

    def _step_error(self, step: dict[str, Any], request: Any) -> None:
        self._respond(request, error=step["error"])

    def _step_default(self, step: dict[str, Any], request: Any) -> None:
        if request is not None:
            self._answer_default(request)

    def _step_sleep(self, step: dict[str, Any], request: Any) -> None:
        time.sleep(step["sleep"])

    def _step_record(self, step: dict[str, Any], request: Any) -> None:
        self.recorder.write(event=step["record"])

    def _step_spawn(self, step: dict[str, Any], request: Any) -> None:
        options = step["spawn"] or {}
        ready_read, ready_write = os.pipe()
        stdio = None if options.get("inheritStdio") else subprocess.DEVNULL
        child = subprocess.Popen(
            [
                sys.executable,
                "-I",
                os.path.abspath(__file__),
                "--scenario",
                self.scenario_path or os.devnull,
                "--sleeper",
                "1" if options.get("ignoreSigterm") else "0",
                str(ready_write),
            ],
            stdin=subprocess.DEVNULL,
            stdout=stdio,
            stderr=stdio,
            pass_fds=(ready_write,),
            start_new_session=bool(options.get("newSession")),
        )
        os.close(ready_write)
        os.read(ready_read, 1)
        os.close(ready_read)
        self.channel.stderr(f"child {child.pid}\n")

    def _step_exit(self, step: dict[str, Any], request: Any) -> None:
        os._exit(step["exit"])

    # -- built-in answers -------------------------------------------------

    def _respond(self, request: dict[str, Any] | None, **answer: Any) -> None:
        if request is not None and "id" in request:
            self.channel.send({"jsonrpc": "2.0", "id": request["id"], **answer})

    def _answer_default(self, request: dict[str, Any]) -> None:
        if "id" not in request:
            return
        handler = self._defaults.get(request["method"])
        if handler is None:
            self._respond(
                request, error={"code": -32601, "message": "Method not found"}
            )
            return
        try:
            result = handler(request.get("params") or {})
        except LookupError as error:
            self._respond(
                request, error={"code": -32602, "message": str(error.args[0])}
            )
            return
        self._respond(request, result=result)

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        scenario = self.scenario
        result = {
            "protocolVersion": scenario.get("protocolVersion", "2025-06-18"),
            "capabilities": scenario.get(
                "capabilities", {"tools": {}} if "tools" in scenario else {}
            ),
            "serverInfo": scenario.get(
                "serverInfo", {"name": "fake-mcp-server", "version": "1.0.0"}
            ),
        }
        if "instructions" in scenario:
            result["instructions"] = scenario["instructions"]
        return result

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not any(tool.get("name") == name for tool in self.scenario.get("tools", [])):
            raise LookupError(f"Unknown tool: {name}")
        results = self.scenario.get("toolResults", {})
        if name in results:
            return results[name]
        arguments = params.get("arguments") or {}
        text = arguments.get("text")
        if not isinstance(text, str):
            text = json.dumps(arguments)
        return {"content": [{"type": "text", "text": text}]}

    def _read_resource(self, params: dict[str, Any]) -> dict[str, Any]:
        contents = self.scenario.get("resourceContents", {})
        uri = params.get("uri")
        if uri not in contents:
            raise LookupError(f"Resource not found: {uri}")
        return {"contents": contents[uri]}

    def _process_info(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "pid": os.getpid(),
            "pgid": os.getpgrp(),
            "sid": os.getsid(0),
            "cwd": os.getcwd(),
            "env": dict(os.environ),
        }


def _orphan_grace(scenario: dict[str, Any]) -> float:
    return float(scenario.get("orphanGraceSeconds", _DEFAULT_ORPHAN_GRACE_SECONDS))


def serve_stdio(scenario: dict[str, Any], scenario_path: str | None = None) -> None:
    recorder = Recorder(scenario.get("record"))
    engine = ScenarioEngine(scenario, StdioChannel(), recorder, scenario_path)
    orphan_watch = OrphanWatch(
        _orphan_grace(scenario), lambda: recorder.write(event="orphaned")
    )
    if "onSigterm" in scenario:
        steps = scenario["onSigterm"]

        def on_sigterm(signum: int, frame: Any) -> None:
            recorder.write(event="sigterm")
            engine.run(steps)

        signal.signal(signal.SIGTERM, on_sigterm)
    engine.run(scenario.get("startup", []))
    while True:
        raw = sys.stdin.buffer.readline()
        if not raw:
            break
        if not raw.strip():
            continue
        try:
            message = json.loads(raw)
        except ValueError:
            recorder.write(raw=raw.decode("utf-8", errors="replace"))
            continue
        recorder.write(received=message)
        if isinstance(message, dict):
            engine.receive(message)
    recorder.write(event="stdin-closed")
    orphan_watch.left_alone()
    engine.run(scenario.get("onStdinClose", [{"exit": 0}]))
    while True:
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", required=True)
    parser.add_argument(
        "--sleeper",
        nargs=2,
        metavar=("IGNORE_SIGTERM", "READY_FD"),
        help="Run as the child of a spawn step instead of as a server.",
    )
    arguments = parser.parse_args()
    scenario: dict[str, Any] = {}
    if arguments.scenario != os.devnull:
        with open(arguments.scenario, encoding="utf-8") as scenario_file:
            scenario = json.load(scenario_file)
    if arguments.sleeper:
        ignore_sigterm, ready = arguments.sleeper
        sleep_for_ever(ignore_sigterm == "1", int(ready), _orphan_grace(scenario))
    else:
        serve_stdio(scenario, arguments.scenario)


if __name__ == "__main__":
    main()
