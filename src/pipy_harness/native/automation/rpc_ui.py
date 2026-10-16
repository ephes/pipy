"""The UI requests of RPC mode (Pi ``createExtensionUIContext`` in
``modes/rpc/rpc-mode.ts``, protocol in ``docs/rpc-extension-ui.md``).

A request is one ``extension_ui_request`` line on stdout. ``notify``,
``setStatus``, ``setWidget``, ``setTitle`` and ``set_editor_text`` need no
answer. ``select``, ``confirm``, ``input`` and ``editor`` block their caller
until the client sends an ``extension_ui_response`` line with the request's
``id``, the wait is cancelled, its ``timeout`` (milliseconds, Pi's dialog
option) passes, or the session's input ends; each of those gives the
dialog's default (``None``, or ``False`` for ``confirm``). The reader thread
delivers the responses (``respond``); the callers are the session thread and,
for the redirect URL of an MCP sign-in, a thread of the sign-in.

``extension_driver()`` is the ``ExtensionUiDriver`` an extension's ``ctx.ui``
uses in RPC mode, so ``ctx.has_ui`` is true there as in Pi.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Sequence
from typing import Any

from pipy_harness.native.automation.jsonl import JsonlWriter

_POLL_SECONDS = 0.05

# Pi's camel-case placements of `setWidget`.
_WIDGET_PLACEMENTS = {"above_editor": "aboveEditor", "below_editor": "belowEditor"}


class _Asked:
    """One request that waits for its response."""

    __slots__ = ("answered", "deadline", "response")

    def __init__(self, deadline: float | None) -> None:
        self.answered = threading.Event()
        # When its timeout passes (monotonic clock): an answer after it is
        # no answer, as Pi's timer has already resolved the dialog.
        self.deadline = deadline
        self.response: dict[str, Any] | None = None

    def expired(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline


class RpcUi:
    """Sends UI requests to the RPC client and waits for its answers."""

    def __init__(self, writer: JsonlWriter) -> None:
        self._writer = writer
        self._lock = threading.Lock()
        self._asked: dict[str, _Asked] = {}
        self._closed = False

    def extension_driver(self) -> RpcExtensionUiDriver:
        """The driver of an extension's ``ctx.ui`` in RPC mode."""

        return RpcExtensionUiDriver(self)

    def notify(self, message: str, kind: str = "info") -> None:
        self.send("notify", message=message, notifyType=kind)

    def send(self, method: str, **fields: Any) -> None:
        """A request that needs no answer. A field that is ``None`` is left
        out, as JSON leaves out Pi's ``undefined``."""

        self._writer.write_line(
            {
                "type": "extension_ui_request",
                "id": str(uuid.uuid4()),
                "method": method,
                **{key: value for key, value in fields.items() if value is not None},
            }
        )

    def extension_error(self, extension_path: str, event: str, error: str) -> None:
        """Pi's ``onError`` of RPC mode: an ``extension_error`` object."""

        self._writer.write_line(
            {
                "type": "extension_error",
                "extensionPath": extension_path,
                "event": event,
                "error": error,
            }
        )

    def select(
        self,
        title: str,
        options: Sequence[str],
        cancel_event: threading.Event | None = None,
        timeout: int | None = None,
    ) -> str | None:
        """The option the client chose, or ``None``."""

        return _value(
            self._ask(
                {"method": "select", "title": title, "options": list(options)},
                cancel_event,
                timeout,
            )
        )

    def confirm(self, title: str, message: str, timeout: int | None = None) -> bool:
        """Whether the client confirmed; ``False`` when it did not answer."""

        response = self._ask(
            {"method": "confirm", "title": title, "message": message}, None, timeout
        )
        if response is None or response.get("cancelled"):
            return False
        return response.get("confirmed") is True

    def input(
        self,
        title: str,
        placeholder: str | None = None,
        cancel_event: threading.Event | None = None,
        timeout: int | None = None,
    ) -> str | None:
        """The text the client entered, or ``None``."""

        request: dict[str, Any] = {"method": "input", "title": title}
        if placeholder is not None:
            request["placeholder"] = placeholder
        return _value(self._ask(request, cancel_event, timeout))

    def editor(self, title: str, prefill: str | None = None) -> str | None:
        """The text the client edited, or ``None`` (Pi's editor has no
        timeout)."""

        request: dict[str, Any] = {"method": "editor", "title": title}
        if prefill is not None:
            request["prefill"] = prefill
        return _value(self._ask(request, None, None))

    def respond(self, response: dict[str, Any]) -> None:
        """Deliver an ``extension_ui_response`` line. One that answers no
        waiting request is ignored, as in Pi."""

        request_id = response.get("id")
        if not isinstance(request_id, str):
            return
        with self._lock:
            asked = self._asked.pop(request_id, None)
        if asked is not None:
            if not asked.expired():
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
        self,
        request: dict[str, Any],
        cancel_event: threading.Event | None,
        timeout: int | None,
    ) -> dict[str, Any] | None:
        if cancel_event is not None and cancel_event.is_set():
            return None
        request_id = str(uuid.uuid4())
        if timeout:
            request = {**request, "timeout": timeout}
        asked = _Asked(None if not timeout else time.monotonic() + timeout / 1000)
        with self._lock:
            if self._closed:
                return None
            self._asked[request_id] = asked
        self._writer.write_line(
            {"type": "extension_ui_request", "id": request_id, **request}
        )
        while not asked.answered.wait(self._wait_seconds(asked)):
            if (cancel_event is not None and cancel_event.is_set()) or asked.expired():
                with self._lock:
                    self._asked.pop(request_id, None)
                return None
        return asked.response

    @staticmethod
    def _wait_seconds(asked: _Asked) -> float:
        if asked.deadline is None:
            return _POLL_SECONDS
        return max(0.0, min(_POLL_SECONDS, asked.deadline - time.monotonic()))


class RpcExtensionUiDriver:
    """An extension's ``ctx.ui`` over the RPC client (Pi
    ``createExtensionUIContext``). What needs the terminal does nothing,
    as in Pi: the working indicator, header and footer, the hidden-thinking
    label, the theme."""

    # `_CollectingUi` hands a dialog's `timeout` only to a driver that has it.
    dialog_timeouts = True

    def __init__(self, ui: RpcUi) -> None:
        self._ui = ui

    def select(
        self, title: str, options: Sequence[str], timeout: int | None = None
    ) -> str | None:
        return self._ui.select(title, options, timeout=timeout)

    def input(
        self, title: str, placeholder: str | None = None, timeout: int | None = None
    ) -> str | None:
        return self._ui.input(title, placeholder, timeout=timeout)

    def confirm(self, title: str, message: str, timeout: int | None = None) -> bool:
        return self._ui.confirm(title, message, timeout=timeout)

    def editor(self, title: str, prefill: str | None = None) -> str | None:
        return self._ui.editor(title, prefill)

    def set_status(self, key: str, text: str | None) -> None:
        self._ui.send("setStatus", statusKey=key, statusText=text)

    def set_widget(self, key: str, content: object, placement: str) -> None:
        # Only lines of text are sent; a component factory needs the
        # terminal (Pi sends only string arrays and `undefined`).
        if content is None:
            lines = None
        elif isinstance(content, (list, tuple)) and all(
            isinstance(line, str) for line in content
        ):
            lines = list(content)
        else:
            return
        self._ui.send(
            "setWidget",
            widgetKey=key,
            widgetLines=lines,
            widgetPlacement=_WIDGET_PLACEMENTS.get(placement),
        )

    def set_title(self, title: str) -> None:
        self._ui.send("setTitle", title=title)

    def set_editor_text(self, text: str) -> None:
        self._ui.send("set_editor_text", text=text)

    def paste_to_editor(self, text: str) -> None:
        self.set_editor_text(text)

    def get_editor_text(self) -> str:
        return ""

    def set_working_message(self, message: str | None = None) -> None:
        return None

    def set_working_visible(self, visible: bool) -> None:
        return None

    def set_working_indicator(
        self, frames: Sequence[str] | None, interval_ms: int | None
    ) -> None:
        return None

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        return None

    def set_header(self, factory: object | None) -> None:
        return None

    def set_footer(self, factory: object | None) -> None:
        return None

    def apply_theme(self, name: str) -> tuple[bool, str | None]:
        return False, "Theme switching not supported in RPC mode"


def _value(response: dict[str, Any] | None) -> str | None:
    if response is None or response.get("cancelled"):
        return None
    value = response.get("value")
    return value if isinstance(value, str) else None
