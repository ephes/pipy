"""The UI requests of RPC mode (Pi ``createExtensionUIContext`` in
``modes/rpc/rpc-mode.ts``).

A request is one ``extension_ui_request`` line on stdout. ``notify`` needs no
answer. ``select`` and ``input`` block their caller until the client sends
an ``extension_ui_response`` line with the request's ``id``, the wait is
cancelled, or the session's input ends. The reader thread delivers the
responses (``respond``); the callers are the session thread and, for the
redirect URL of an MCP sign-in, a thread of the sign-in.

Only what ``/mcp`` and an extension's notices need is here. Pi's other
methods (``confirm``, ``editor``, ``setStatus``, ``setWidget``, ``setTitle``,
``set_editor_text``) and its ``timeout`` option are not built.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Sequence
from typing import Any

from pipy_harness.native.automation.jsonl import JsonlWriter

_POLL_SECONDS = 0.05


class _Asked:
    """One request that waits for its response."""

    __slots__ = ("answered", "response")

    def __init__(self) -> None:
        self.answered = threading.Event()
        self.response: dict[str, Any] | None = None


class RpcUi:
    """Sends UI requests to the RPC client and waits for its answers."""

    def __init__(self, writer: JsonlWriter) -> None:
        self._writer = writer
        self._lock = threading.Lock()
        self._asked: dict[str, _Asked] = {}
        self._closed = False

    def notify(self, message: str, kind: str = "info") -> None:
        self._writer.write_line(
            {
                "type": "extension_ui_request",
                "id": str(uuid.uuid4()),
                "method": "notify",
                "message": message,
                "notifyType": kind,
            }
        )

    def select(
        self,
        title: str,
        options: Sequence[str],
        cancel_event: threading.Event | None = None,
    ) -> str | None:
        """The option the client chose, or ``None``."""

        return _value(
            self._ask(
                {"method": "select", "title": title, "options": list(options)},
                cancel_event,
            )
        )

    def input(
        self,
        title: str,
        placeholder: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str | None:
        """The text the client entered, or ``None``."""

        request: dict[str, Any] = {"method": "input", "title": title}
        if placeholder is not None:
            request["placeholder"] = placeholder
        return _value(self._ask(request, cancel_event))

    def respond(self, response: dict[str, Any]) -> None:
        """Deliver an ``extension_ui_response`` line. One that answers no
        waiting request is ignored, as in Pi."""

        request_id = response.get("id")
        if not isinstance(request_id, str):
            return
        with self._lock:
            asked = self._asked.pop(request_id, None)
        if asked is not None:
            asked.response = response
            asked.answered.set()

    def close(self) -> None:
        """The client's input ended: every waiting request ends unanswered,
        and so does every later one."""

        with self._lock:
            self._closed = True
            waiting = list(self._asked.values())
            self._asked.clear()
        for asked in waiting:
            asked.answered.set()

    def _ask(
        self, request: dict[str, Any], cancel_event: threading.Event | None
    ) -> dict[str, Any] | None:
        if cancel_event is not None and cancel_event.is_set():
            return None
        request_id = str(uuid.uuid4())
        asked = _Asked()
        with self._lock:
            if self._closed:
                return None
            self._asked[request_id] = asked
        self._writer.write_line(
            {"type": "extension_ui_request", "id": request_id, **request}
        )
        while not asked.answered.wait(_POLL_SECONDS):
            if cancel_event is not None and cancel_event.is_set():
                with self._lock:
                    self._asked.pop(request_id, None)
                return None
        return asked.response


def _value(response: dict[str, Any] | None) -> str | None:
    if response is None or response.get("cancelled"):
        return None
    value = response.get("value")
    return value if isinstance(value, str) else None
