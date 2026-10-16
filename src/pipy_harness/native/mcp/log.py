"""The file MCP servers' log messages go to.

Servers send log messages with ``notifications/message``. Each one is
appended to ``mcp.log`` in the config home as one line, with one ``write``
call on a descriptor opened for appending, because several pipy processes may
write to the same file. Once the file is larger than ``MAX_LOG_BYTES`` it is
renamed to ``mcp.log.1`` before the next line is written.

The text comes from the server and is untrusted. The file is created readable
by its owner only.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from pipy_harness.native.mcp.protocol import is_object

MCP_LOG_FILENAME: Final = "mcp.log"
MAX_LOG_BYTES: Final = 5 * 1024 * 1024

_LINE_BREAK: Final = re.compile(r"\r?\n")


def mcp_log_path(config_home: Path | str) -> Path:
    return Path(config_home) / MCP_LOG_FILENAME


def _format_data(data: object) -> str:
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def format_mcp_log_message(
    server: str, params: object, now: datetime | None = None
) -> str:
    """One ``notifications/message`` from ``server`` as a log line.

    The line is ``<ISO time> [<server>] <level> <logger>: <message>``. The
    level defaults to ``info``, the logger part is left out when the message
    names none, data that is not a string is written as JSON, and the lines
    after the first are indented by four spaces. A message without ``data``
    is written as ``undefined``.
    """

    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    # Params that are not an object are the data, as in Pi. The client hands
    # over ``None`` for a notification without params.
    message = params if is_object(params) else {}
    if not is_object(params) and params is not None:
        message = {"data": params}
    level = message.get("level")
    if not isinstance(level, str):
        level = "info"
    logger = message.get("logger")
    logger_part = f" {logger}:" if isinstance(logger, str) and logger else ""
    text = _format_data(message["data"]) if "data" in message else "undefined"
    text = _LINE_BREAK.sub("\n    ", text)
    return f"{stamp} [{server}] {level}{logger_part} {text}\n"


def _encode(line: str) -> bytes:
    # A lone surrogate from a JSON escape cannot be written as UTF-8; it
    # becomes U+FFFD.
    try:
        return line.encode("utf-8")
    except UnicodeEncodeError:
        repaired = line.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
        return repaired.encode("utf-8")


class McpServerLog:
    """Appends server log messages to one file.

    ``write`` may be called from any thread. A failure to format or write a
    message is ignored: logging must not break a connection.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._size: int | None = None

    def write(self, server: str, params: object) -> None:
        try:
            data = _encode(format_mcp_log_message(server, params))
            with self._lock:
                if self._size is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self._size = self._current_size()
                if self._size > MAX_LOG_BYTES:
                    # Another process may have rotated the file already.
                    if self._current_size() > MAX_LOG_BYTES:
                        os.replace(self.path, f"{self.path}.1")
                    self._size = self._current_size()
                self._append(data)
                self._size += len(data)
        except Exception:  # noqa: BLE001 - the log is best effort
            pass

    def _append(self, data: bytes) -> None:
        descriptor = os.open(
            self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC, 0o600
        )
        try:
            os.write(descriptor, data)
        finally:
            os.close(descriptor)

    def _current_size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0
