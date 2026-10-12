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
- ``{"spawn": {"ignoreSigterm": bool, "inheritStdio": bool}}``: start a child
  process that sleeps for ever and print ``child <pid>`` on stderr once it
  is set up. Without ``inheritStdio`` the child's stdio is ``/dev/null``.
- ``{"exit": code}``: end the process at once.

The built-in method ``fixture/process`` answers with the server's ``pid``,
``pgid``, ``sid``, ``cwd`` and ``env``.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from typing import Any

_CHILD_CODE = """
import os, signal, sys, time
if sys.argv[1] == "1":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
ready = int(sys.argv[2])
os.write(ready, b"1")
os.close(ready)
while True:
    time.sleep(1)
"""


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
        self, scenario: dict[str, Any], channel: StdioChannel, recorder: Recorder
    ) -> None:
        self.scenario = scenario
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
                "-c",
                _CHILD_CODE,
                "1" if options.get("ignoreSigterm") else "0",
                str(ready_write),
            ],
            stdin=subprocess.DEVNULL,
            stdout=stdio,
            stderr=stdio,
            pass_fds=(ready_write,),
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


def serve_stdio(scenario: dict[str, Any]) -> None:
    recorder = Recorder(scenario.get("record"))
    engine = ScenarioEngine(scenario, StdioChannel(), recorder)
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
    engine.run(scenario.get("onStdinClose", [{"exit": 0}]))
    while True:
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", required=True)
    arguments = parser.parse_args()
    with open(arguments.scenario, encoding="utf-8") as scenario_file:
        scenario = json.load(scenario_file)
    serve_stdio(scenario)


if __name__ == "__main__":
    main()
