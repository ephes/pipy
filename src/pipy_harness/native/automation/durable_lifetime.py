"""One caller-loop owner for durable CLI signals and physical cleanup."""

from __future__ import annotations

import asyncio
import os
import signal
import stat
import threading
from collections.abc import Callable, Coroutine
from types import FrameType
from typing import TYPE_CHECKING, Any, TypeVar

from ..durable.generation_transport import owned_call
from .durable_args import DurableCLIError

if TYPE_CHECKING:
    from pipy_harness.durable_product_api import DurableProductSession

T = TypeVar("T")


class CommandInterrupted(Exception):
    pass


def signal_domain() -> dict[signal.Signals, Any]:
    if threading.current_thread() is not threading.main_thread():
        raise DurableCLIError("unsupported")
    prior = {}
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        handler = signal.getsignal(sig)
        allowed: tuple[Any, ...] = (signal.SIG_DFL, signal.SIG_IGN)
        if sig == signal.SIGINT:
            allowed += (signal.default_int_handler,)
        if handler not in allowed:
            raise DurableCLIError("unsupported")
        prior[sig] = handler
    return prior


class SignalOwner:
    """Signal callbacks only record a fixed disposition and wake the loop.

    All reads/writes occur on the main thread. setdefault latches the first
    recorded notification even if another Python signal interrupts a handler.
    No raising handler is restored until after Runner.close.
    """

    def __init__(self, prior: dict[signal.Signals, Any]) -> None:
        self.prior = prior
        self.first: dict[str, int] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self.changed = asyncio.Event()

    @property
    def exit_code(self) -> int | None:
        return self.first.get("exit")

    def notify(self, signum: int, frame: FrameType | None) -> None:
        self.first.setdefault("exit", 128 + signum)
        loop = self.loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(self.changed.set)
            except RuntimeError:
                # Only loop destruction can close it: accepted work is joined
                # first. The fixed disposition remains available synchronously.
                pass

    def install(self) -> None:
        for sig, prior in self.prior.items():
            if prior != signal.SIG_IGN:
                signal.signal(sig, self.notify)

    def restore(self) -> None:
        for sig, prior in self.prior.items():
            signal.signal(sig, prior)


class CommandOwner:
    def __init__(self, signals: SignalOwner) -> None:
        self.signals = signals
        self.work: list[asyncio.Task[Any]] = []
        self.product: DurableProductSession | None = None
        self.factory: asyncio.Task[DurableProductSession] | None = None
        # All readers/writers belong to this one caller loop. An advisory
        # readiness check cannot promise that a shared stream stays ready.
        self.io_work: asyncio.Task[Any] | None = None
        self.stopping = False

    def check(self) -> None:
        if self.stopping or self.signals.exit_code is not None:
            raise CommandInterrupted

    async def io_call(self, run: Callable[[], T]) -> T:
        self.check()
        assert self.io_work is None, "CLI I/O is sequential"
        work = asyncio.create_task(owned_call(run))
        self.io_work = work
        try:
            return await asyncio.shield(work)
        finally:
            # A canceled waiter retains the live call for cleanup's I/O-first
            # join. Only owned_call's completion proves its real worker exited.
            if work.done():
                if not work.cancelled():
                    work.exception()
                if self.io_work is work:
                    self.io_work = None

    def accept(self, offered: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        task = asyncio.create_task(offered)
        self.work.append(task)
        return task

    async def stage(self, work: asyncio.Task[T]) -> T:
        self.check()
        notification = asyncio.create_task(self.signals.changed.wait())
        try:
            await asyncio.wait(
                (work, notification), return_when=asyncio.FIRST_COMPLETED
            )
            self.check()
            return work.result()
        finally:
            notification.cancel()
            await asyncio.gather(notification, return_exceptions=True)

    async def cleanup(self) -> None:
        self.stopping = True
        work = self.io_work
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
            if self.io_work is work:
                self.io_work = None
        if self.factory is not None and self.product is None:
            try:
                self.product = await self.factory
            except Exception:  # noqa: BLE001 - retrieve failed acquisition privately
                pass
        try:
            if self.product is not None:
                await self.product.close()
        finally:
            # Close precedes joining resume/control waiters: it seals the
            # Session and waits actual workers without taking their control lock.
            await asyncio.gather(*self.work, return_exceptions=True)


def stdin_fd() -> int:
    import sys

    try:
        fd = sys.stdin.fileno()
        mode = os.fstat(fd).st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISFIFO(mode)):
            raise DurableCLIError("unsupported")
        return fd
    except (OSError, ValueError):
        raise DurableCLIError("unsupported") from None


async def read_input(fd: int, owner: CommandOwner, maximum: int) -> str:
    import select

    chunks = bytearray()
    try:
        while True:
            owner.check()
            if not select.select([fd], [], [], 0)[0]:
                await asyncio.sleep(0.01)
                continue
            try:
                size = min(65536, maximum + 1 - len(chunks))
                data = await owner.io_call(lambda: os.read(fd, size))
            except BlockingIOError:
                await asyncio.sleep(0.01)
                continue
            if not data:
                return chunks.decode("utf-8", errors="strict")
            chunks.extend(data)
            if len(chunks) > maximum:
                raise DurableCLIError
            await asyncio.sleep(0)
    except (OSError, UnicodeError):
        raise DurableCLIError from None


async def write_output(text: str, owner: CommandOwner) -> None:
    """Never mutate shared description flags; join actual I/O through close."""
    import select
    import sys

    stream = sys.stdout
    try:
        fd = stream.fileno()
    except (AttributeError, OSError, ValueError):
        await owner.io_call(lambda: _write_and_flush(stream, text))
        return
    await owner.io_call(stream.flush)
    remaining = memoryview(text.encode("utf-8"))
    while remaining:
        owner.check()
        if not select.select([], [fd], [], 0)[1]:
            await asyncio.sleep(0.01)
            continue
        try:
            chunk = remaining[: select.PIPE_BUF]
            count = await owner.io_call(lambda: os.write(fd, chunk))
        except BlockingIOError:
            await asyncio.sleep(0.01)
            continue
        if count <= 0:
            raise OSError("CLI output made no progress")
        remaining = remaining[count:]
        await asyncio.sleep(0)
    await owner.io_call(stream.flush)


def _write_and_flush(stream: Any, text: str) -> None:
    stream.write(text)
    stream.flush()
