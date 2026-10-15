"""Where a session's extension notices and extension errors go.

Pi binds one UI context and one ``onError`` listener per mode
(``core/extensions/runner.ts`` ``noOpUIContext``, ``modes/print-mode.ts``,
``modes/rpc/rpc-mode.ts``, ``modes/interactive/interactive-mode.ts``). pipy
has the same three cases:

- RPC mode: a notice is an ``extension_ui_request`` of method ``notify`` and
  an error an ``extension_error`` object, both on stdout.
- Print and JSON mode (a session that reads one prompt): there is no UI, so a
  notice is dropped, and an error is the line
  ``Extension error (<path>): <message>`` on stderr.
- A terminal or the plain REPL: both are shown as local diagnostics, an error
  as ``Extension "<path>" error: <message>``.

pipy's own diagnostics (the ``pipy:`` lines, tool warnings) do not come here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TextIO

from pipy_harness.native.automation.events import AutomationUi


class ExtensionReports:
    """The notice sink and the error sink of one session."""

    __slots__ = ("_error_stream", "_local", "_prompt_mode", "_transport")

    def __init__(
        self,
        *,
        transport_ui: AutomationUi | None,
        local_notify: Callable[[str, str], None],
        error_stream: TextIO,
        prompt_mode: bool,
    ) -> None:
        self._transport = transport_ui
        self._local = local_notify
        self._error_stream = error_stream
        self._prompt_mode = prompt_mode

    def notify(self, kind: str, message: str) -> None:
        """What Pi sends through ``ctx.ui.notify``."""

        if self._transport is not None:
            self._transport.notify(str(message), kind)
        elif not self._prompt_mode:
            self._local(kind, message)

    def error(self, extension_path: str, event: str, error: str) -> None:
        """What Pi reports through ``ExtensionRunner.emitError``."""

        if self._transport is not None:
            self._transport.extension_error(extension_path, event, error)
        elif self._prompt_mode:
            print(
                f"Extension error ({extension_path}): {error}", file=self._error_stream
            )
        else:
            self._local("error", f'Extension "{extension_path}" error: {error}')


__all__ = ["ExtensionReports"]
