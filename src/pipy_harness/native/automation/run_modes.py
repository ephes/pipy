"""One-shot automation drivers: mode resolution, `--mode json`, `--print`.

These drive the *real* tool-loop adapter for a single prompt and exit, mirroring
Pi's ``runPrintMode`` (`packages/coding-agent/src/modes/print-mode.ts`):

- ``--mode json`` emits the native session header line, then the full Pi-shaped
  event stream (full assistant/tool/bash content), then exits.
- ``--print``/``-p`` emits only the final assistant text to stdout; failures go
  to stderr with a non-zero exit.

Mode resolution (`resolve_app_mode`) matches Pi's ``resolveAppMode``
(`packages/coding-agent/src/main.ts`): ``--mode rpc`` > ``--mode json`` >
(``--print`` or non-TTY stdin) > interactive.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from pipy_harness.adapters.native import PipyNativeToolReplAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import RunRequest
from pipy_harness.native.automation.jsonl import JsonlWriter


def resolve_app_mode(
    *, mode: str | None, print_flag: bool, stdin_is_tty: bool
) -> str:
    """Resolve the headless app mode, matching Pi's ``resolveAppMode``."""

    if mode == "rpc":
        return "rpc"
    if mode == "json":
        return "json"
    if print_flag or not stdin_is_tty:
        return "print"
    return "interactive"


def session_header_event(tree: Any) -> dict[str, Any]:
    """Build the first JSONL line: the native session header.

    The ``version`` is the pipy native-session-tree format version (see
    ``docs/session-tree.md``), independent of Pi's session version namespace.
    """

    header = tree.header
    return {
        "type": "session",
        "version": header.version,
        "id": header.id,
        "timestamp": header.timestamp,
        "cwd": header.cwd,
    }


class _SinglePromptStream:
    """Feed the entire prompt as ONE non-interactive turn.

    ``io.StringIO(prompt + "\\n").readline()`` would split a multiline prompt on
    its embedded newlines into multiple REPL turns; this stream returns the whole
    prompt (newlines intact) on the first ``readline`` and EOF (``""``) after, so
    a one-shot ``--mode json``/``--print`` run is exactly one agent turn.
    """

    def __init__(self, prompt: str) -> None:
        self._lines = [prompt + "\n", ""]
        self._index = 0

    def readline(self, *_args: Any) -> str:
        if self._index < len(self._lines):
            value = self._lines[self._index]
            self._index += 1
            return value
        return ""

    def read(self, *_args: Any) -> str:
        return self.readline()

    def isatty(self) -> bool:
        return False

    def readable(self) -> bool:
        return True

    def close(self) -> None:
        return None

    @property
    def closed(self) -> bool:
        return False


class JsonlEventSink:
    """Forward each Pi-shaped session event to the serialized JSONL writer."""

    def __init__(self, writer: JsonlWriter) -> None:
        self._writer = writer

    def emit(self, event: dict[str, Any]) -> None:
        self._writer.write_line(event)


class _FinalAssistantTextSink:
    """Capture the final assistant text for ``--print`` text mode."""

    def __init__(self) -> None:
        self.last_text: str | None = None

    def emit(self, event: dict[str, Any]) -> None:
        if event.get("type") != "message_end":
            return
        message = event.get("message") or {}
        if message.get("role") != "assistant":
            return
        text = "".join(
            block.get("text", "")
            for block in message.get("content", [])
            if block.get("type") == "text"
        )
        if text:
            self.last_text = text


class _NullEventSink:
    """No-op metadata telemetry sink for the direct adapter drive."""

    def emit(
        self, event_type: str, *, summary: str, payload: Any | None = None
    ) -> None:  # noqa: D401 - protocol stub
        return None


def _run_oneshot(adapter: PipyNativeToolReplAdapter, cwd: Path) -> Any:
    request = RunRequest(
        agent="pipy-native",
        slug="automation",
        command=[],
        cwd=cwd,
        capture_policy=CapturePolicy(),
    )
    prepared = adapter.prepare(request)
    return adapter.run(
        prepared, event_sink=_NullEventSink(), capture_policy=CapturePolicy()
    )


def run_json_mode(
    *,
    adapter: PipyNativeToolReplAdapter,
    prompt: str,
    cwd: Path,
    native_session: Any,
    stdout_buffer: BinaryIO,
    error_stream: TextIO,
) -> int:
    """Emit the session header + full Pi-shaped event stream for one prompt."""

    writer = JsonlWriter(stdout_buffer)
    writer.write_line(session_header_event(native_session))
    adapter.native_session = native_session
    adapter.automation_observer = JsonlEventSink(writer)
    adapter.input_stream = _SinglePromptStream(prompt)
    # The events carry full assistant content; discard the renderer's plain-text
    # final-answer print so stdout stays pure JSONL.
    adapter.output_stream = io.StringIO()
    adapter.error_stream = error_stream
    result = _run_oneshot(adapter, cwd)
    return result.exit_code


def run_print_mode(
    *,
    adapter: PipyNativeToolReplAdapter,
    prompt: str,
    cwd: Path,
    stdout: TextIO,
    error_stream: TextIO,
) -> int:
    """Print only the final assistant text; failures go to stderr."""

    sink = _FinalAssistantTextSink()
    adapter.automation_observer = sink
    adapter.input_stream = _SinglePromptStream(prompt)
    adapter.output_stream = io.StringIO()
    adapter.error_stream = error_stream
    result = _run_oneshot(adapter, cwd)
    metadata = result.metadata or {}
    error_type = metadata.get("error_type")
    if error_type:
        error_message = metadata.get("error_message")
        detail = f": {error_message}" if error_message else ""
        print(f"pipy: run failed with {error_type}{detail}", file=error_stream)
        return result.exit_code or 1
    if sink.last_text is not None:
        print(sink.last_text, file=stdout)
    return result.exit_code
