"""Extension terminal-input listener registration and ordered application."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pipy_harness.native.extension_chrome_state import ExtensionChromeState
from pipy_harness.native.ui.paint_lock import PaintLock


@dataclass(frozen=True, slots=True)
class _ListenerResult:
    consume: bool
    has_replacement: bool
    replacement: object = None


def _coerce_listener_result(result: object) -> _ListenerResult:
    """Preserve the exact Mapping/object result vocabulary of the old loop."""

    if isinstance(result, Mapping):
        consume = bool(result.get("consume"))
        if "data" in result:
            return _ListenerResult(consume, True, result.get("data"))
        return _ListenerResult(consume, False)
    if result is None:
        return _ListenerResult(False, False)
    consume = bool(getattr(result, "consume", False))
    if hasattr(result, "data"):
        return _ListenerResult(consume, True, getattr(result, "data"))
    return _ListenerResult(consume, False)


class TerminalInputListeners:
    """Own listener-ledger transitions and fail-soft ordered dispatch."""

    def __init__(
        self,
        record: ExtensionChromeState,
        paint_lock: PaintLock,
        repaint: Callable[[], None],
    ) -> None:
        self._record = record
        self._paint_lock = paint_lock
        self._repaint = repaint

    @property
    def last_replaced(self) -> bool:
        with self._paint_lock:
            return self._record.terminal_input_last_replaced

    def add(self, handler: Callable[[str], object]) -> Callable[[], None]:
        if not callable(handler):
            return lambda: None
        with self._paint_lock:
            generation, listener_id = self._record.register_terminal_input_listener(
                handler
            )

        disposed = False

        def dispose() -> None:
            nonlocal disposed
            if disposed:
                return
            disposed = True
            with self._paint_lock:
                self._record.remove_terminal_input_listener(generation, listener_id)

        return dispose

    def apply(self, key: str) -> str | None:
        """Apply a stable listener snapshot in registration order.

        Handler exceptions fail soft. Mapping and attribute-shaped results keep
        their prior consume/replacement semantics, including consume winning
        over a replacement from the same result and replacements feeding the
        next listener. Callbacks run outside ``PaintLock``; the observable
        ``last_replaced`` transition is committed once after dispatch.
        """

        with self._paint_lock:
            handlers = tuple(self._record.terminal_input_listeners.values())
        current = key
        replaced = False
        for handler in handlers:
            try:
                result = handler(current)
            except Exception:  # noqa: BLE001 - extension handlers fail soft
                continue
            outcome = _coerce_listener_result(result)
            if outcome.consume:
                self._commit_last_replaced(replaced)
                return None
            if outcome.has_replacement:
                current = (
                    "" if outcome.replacement is None else str(outcome.replacement)
                )
                replaced = True
        self._commit_last_replaced(replaced)
        return None if current == "" else current

    def _commit_last_replaced(self, replaced: bool) -> None:
        with self._paint_lock:
            self._record.terminal_input_last_replaced = replaced
