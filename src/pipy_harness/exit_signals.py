"""SIGTERM and SIGHUP end pipy like an exit, not like a kill.

Python's default action for both signals ends the process at once: no
``finally`` block and no ``atexit`` hook runs. A session would then leave its
terminal in raw mode and its MCP server processes running. While
``exit_on_termination_signals()`` is active, the first of these signals
raises ``SystemExit`` on the main thread instead, so the session unwinds as
it does for any other exit.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType

_SIGNAL_NAMES = ("SIGTERM", "SIGHUP")  # SIGHUP does not exist on Windows.


@contextmanager
def exit_on_termination_signals() -> Iterator[None]:
    """Turn the first SIGTERM or SIGHUP into ``SystemExit(128 + signal)``.

    A signal that does not have its default action is left alone: one that is
    ignored (``nohup``) stays ignored, and a handler of the embedding program
    stays in charge. Off the main thread, where no handler can be installed,
    nothing changes.

    Later signals are ignored, so they cannot cut the shutdown short. For the
    same reason the handlers stay installed once a signal was received; the
    process is on its way out. Otherwise the previous actions are restored on
    leaving.
    """

    if threading.current_thread() is not threading.main_thread():
        yield
        return
    received = False

    def handle(signum: int, frame: FrameType | None) -> None:
        nonlocal received
        if received:
            return
        received = True
        raise SystemExit(128 + signum)

    installed: list[signal.Signals] = []
    for name in _SIGNAL_NAMES:
        signum = getattr(signal, name, None)
        if signum is None or signal.getsignal(signum) is not signal.SIG_DFL:
            continue
        signal.signal(signum, handle)
        installed.append(signum)
    try:
        yield
    finally:
        if not received:
            for signum in installed:
                signal.signal(signum, signal.SIG_DFL)
