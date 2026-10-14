"""The stdio transport: an MCP server run as a child process.

Messages are newline-delimited JSON on the server's stdin and stdout. The
server runs in its own session, so its process group can be signalled as a
whole and wrappers such as ``npx`` or ``uvx`` do not leave the real server
behind. POSIX only.

Threads, all daemons: one reads stdout and calls the message and error
listeners, one drains stderr into a bounded tail, one waits for the process
and reports the close. ``send`` writes on its caller's thread under a write
lock. Listeners are called with no transport lock held.

Limits of what is promised:

- ``close()`` sends its signals to the process group only while the server
  has not been reaped, so they cannot reach an unrelated group that reused
  the id. Where the platform can wait without reaping (``os.waitid``), an
  exited server stays unreaped until its stdout and stderr closed. So, as
  with Pi, ``close()`` keeps signalling the group (SIGTERM, then SIGKILL)
  while a child that outlived the server holds them, also when the server
  had exited before ``close()`` was called. The one signal sent after the
  pipes closed (to end children that do not hold them) is sent before the
  reaping. Without ``os.waitid`` the watcher polls for the exit and reaps
  the server in the same locked step that untracks it; from then on neither
  ``close()`` nor the exit hook signals the group, so children that outlive
  the server are not ended there, and ``close()`` does not wait for them.
- A descendant that left the process group is not signalled. When such a
  descendant keeps the server's stdout open after the server exited, the
  transport stays open until ``close()`` is called; ``close()`` then returns
  after its SIGKILL without waiting for that descendant. Pi's ``close()``
  waits for it without end.
- ``close()`` called from a message, error or stderr listener does not wait
  for the pipe of the reader it runs on, which cannot be read meanwhile.
- After ``close()`` the readers go on delivering until the pipes close, or
  until they are stopped: after the SIGKILL, or at once without
  ``os.waitid``. A message that is already being delivered, and the error
  for a partial message read before the stop, may reach their listeners
  after the close listeners ran.
- A ``send`` that is blocked because the server does not read its stdin ends
  when the server exits or is killed by ``close()``.
- Servers that still run when the interpreter exits are ended by an
  ``atexit`` hook: SIGTERM, then SIGKILL when the server's close timeout is
  over; once a server exited, what is left of its group is sent SIGKILL. The
  hook waits for them, at most a second longer than the longest close
  timeout. Nothing runs when the process is killed with SIGKILL.
"""

from __future__ import annotations

import atexit
import codecs
import json
import os
import selectors
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import IO, Final, NoReturn

from pipy_harness.native.mcp.protocol import (
    JsonRpcMessage,
    McpConnectionClosedError,
    McpProtocolError,
    parse_json_rpc_message,
)
from pipy_harness.native.mcp.transport import DEFAULT_MAX_MESSAGE_BYTES, TransportEvents

DEFAULT_MAX_STDERR_BYTES: Final = 64 * 1024
DEFAULT_CLOSE_TIMEOUT_MS: Final = 2_000
# How long a server gets to exit on its own after its stdin closed, before it
# is sent SIGTERM.
STDIN_CLOSE_GRACE_MS: Final = 500
_READ_BYTES: Final = 64 * 1024

# Process groups of running servers, ended if pipy exits without closing
# them. The value is the time in seconds a group gets after SIGTERM.
_live_groups_lock = threading.Lock()
_live_process_groups: dict[int, float] = {}
_exit_hook_installed = False
# Set by the exit hook. From then on a server that exits takes its group
# with it.
_exiting = False
# How long the exit hook waits for a server it killed to be reaped.
_EXIT_REAP_SECONDS: Final = 1.0
_EXIT_POLL_SECONDS: Final = 0.01
# How often a watcher looks for the server's exit where ``os.waitid`` is
# missing.
_REAP_POLL_SECONDS: Final = 0.05


def _signal_group(pid: int, signum: signal.Signals) -> None:
    try:
        os.killpg(pid, signum)
    except OSError:
        pass  # The group is gone, or holds nothing that can be signalled.


def _terminate_live_process_groups() -> None:
    """End the servers that are still running. The exit hook.

    Every group is sent SIGTERM. One that is still there when its close
    timeout is over is sent SIGKILL, and the hook returns when the servers
    are reaped, or ``_EXIT_REAP_SECONDS`` after the last SIGKILL. A group is
    signalled only while it is tracked; a watcher thread stops tracking a
    server, under the same lock, before it reaps it, so a signal cannot reach
    an unrelated group that reused the id.
    """

    global _exiting
    with _live_groups_lock:
        _exiting = True
        waits = dict(_live_process_groups)
        for pid in waits:
            _signal_group(pid, signal.SIGTERM)
    started = time.monotonic()
    killed: set[int] = set()
    give_up_at: float | None = None
    while True:
        now = time.monotonic()
        with _live_groups_lock:
            left = [pid for pid in waits if pid in _live_process_groups]
            for pid in left:
                if pid not in killed and now - started >= waits[pid]:
                    _signal_group(pid, signal.SIGKILL)
                    killed.add(pid)
        if not left:
            return
        if all(pid in killed for pid in left):
            if give_up_at is None:
                give_up_at = now + _EXIT_REAP_SECONDS
            elif now >= give_up_at:
                return
        time.sleep(_EXIT_POLL_SECONDS)


def _track_process_group(pid: int, kill_after_seconds: float) -> None:
    global _exit_hook_installed
    with _live_groups_lock:
        if not _exit_hook_installed:
            atexit.register(_terminate_live_process_groups)
            _exit_hook_installed = True
        _live_process_groups[pid] = kill_after_seconds


def _untrack_process_group(pid: int) -> None:
    """Stop tracking a server that exited and is not reaped yet.

    While the exit hook runs, the rest of the server's group is sent SIGKILL
    here: children that outlived the server would otherwise stay. The id
    cannot have been reused, because the server is still unreaped.
    """

    with _live_groups_lock:
        _live_process_groups.pop(pid, None)
        if _exiting:
            _signal_group(pid, signal.SIGKILL)


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"Invalid JSON constant {name}")


class _LineSplitter:
    """Cut a byte stream into lines, dropping any line over ``max_bytes``.

    ``feed`` yields each complete line without its newline, and ``None`` once
    for each line that was dropped. A dropped line is skipped up to its
    newline, so at most ``max_bytes`` are held.
    """

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._buffer = bytearray()
        self._discarding = False

    @property
    def has_partial_line(self) -> bool:
        return self._discarding or bool(self._buffer.strip())

    def feed(self, chunk: bytes) -> Iterator[bytes | None]:
        start = 0
        while True:
            newline = chunk.find(b"\n", start)
            if newline < 0:
                break
            piece = chunk[start:newline]
            start = newline + 1
            if self._discarding:
                self._discarding = False
            elif len(self._buffer) + len(piece) > self._max_bytes:
                self._buffer.clear()
                yield None
            else:
                line = bytes(self._buffer) + piece
                self._buffer.clear()
                yield line
        if self._discarding:
            return
        self._buffer += chunk[start:]
        if len(self._buffer) > self._max_bytes:
            self._buffer.clear()
            self._discarding = True
            yield None


@dataclass(frozen=True, slots=True)
class StdioTransportOptions:
    """How to run a stdio server.

    ``env`` is added to the parent environment, or replaces it when
    ``inherit_env`` is false. ``on_stderr`` receives the server's stderr as
    decoded text, on the thread that reads it. ``close_timeout_ms`` is the
    time the server gets to exit after SIGTERM before it is sent SIGKILL.
    """

    command: str
    args: Sequence[str] = ()
    cwd: str | None = None
    env: Mapping[str, str] | None = None
    inherit_env: bool = True
    on_stderr: Callable[[str], None] | None = None
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES
    max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES
    close_timeout_ms: int | float = DEFAULT_CLOSE_TIMEOUT_MS


class StdioTransport(TransportEvents):
    """A connection to one MCP server process. Started at most once."""

    def __init__(self, options: StdioTransportOptions) -> None:
        super().__init__()
        self.options = options
        self._argv = [options.command, *options.args]
        self._extra_env = dict(options.env or {})
        # Guards every field below except the stderr tail, and is the lock of
        # ``_changed``. Held only for short, non-blocking sections.
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._process: subprocess.Popen[bytes] | None = None
        self._started = False
        self._closed = False  # close() was called
        self._reaped = False  # the server's pid is no longer ours to signal
        # The stdout and stderr readers, and those of them that have not
        # finished. A reader finishes when its pipe closes, or when it is
        # stopped after the close was decided.
        self._readers: tuple[threading.Thread, ...] = ()
        self._open_readers: set[threading.Thread] = set()
        # The reader thread close() was called on, from a listener. Its pipe
        # is not read while it waits, so its closing is not waited for.
        self._closing_reader: threading.Thread | None = None
        self._killed = False  # close() sent SIGKILL; the pipes are not waited for
        self._ended = False  # the close is being, or was, reported
        self._finished = threading.Event()  # the close listeners returned
        self._watcher: threading.Thread | None = None
        self._writing = False  # a write to the server's stdin is in progress
        # Held for a whole write to the server's stdin, so messages do not mix.
        self._write_lock = threading.Lock()
        self._stderr_lock = threading.Lock()
        self._stderr_tail = bytearray()

    @property
    def pid(self) -> int | None:
        """The server's process id while it runs."""

        with self._lock:
            if self._process is None or self._ended:
                return None
            return self._process.pid

    @property
    def stderr(self) -> str:
        """The last ``max_stderr_bytes`` of the server's stderr.

        Stays readable after the server exited and after ``close()``.
        """

        with self._stderr_lock:
            return self._stderr_tail.decode("utf-8", errors="replace")

    def start(self) -> None:
        """Start the server. Raises ``OSError`` when it cannot be started."""

        with self._lock:
            if self._started:
                raise RuntimeError("MCP stdio transport already started")
            if self._closed:
                raise McpConnectionClosedError()
            self._started = True
            env = dict(os.environ) if self.options.inherit_env else {}
            env.update(self._extra_env)
            # Written once to stop the reader threads; never drained.
            wake_read, wake_write = os.pipe()
            try:
                process = subprocess.Popen(  # noqa: S603 - the configured server command, run without a shell
                    self._argv,
                    cwd=self.options.cwd,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                    # Own session and process group, so closing the transport
                    # can end the server's children too.
                    start_new_session=True,
                )
            except BaseException:
                os.close(wake_read)
                os.close(wake_write)
                raise
            assert process.stdout is not None and process.stderr is not None
            readers = [
                threading.Thread(
                    target=self._read_stdout,
                    args=(process.stdout, wake_read),
                    name="mcp-stdio-stdout",
                    daemon=True,
                ),
                threading.Thread(
                    target=self._read_stderr,
                    args=(process.stderr, wake_read),
                    name="mcp-stdio-stderr",
                    daemon=True,
                ),
            ]
            watcher = threading.Thread(
                target=self._watch_process,
                args=(process, wake_read, wake_write, readers),
                name="mcp-stdio-wait",
                daemon=True,
            )
            self._process = process
            self._watcher = watcher
            self._readers = tuple(readers)
            self._open_readers = set(readers)
            _track_process_group(
                process.pid, max(0.0, self.options.close_timeout_ms / 1000)
            )
            # The watcher starts first: once it runs, the server is reaped and
            # the close reported even if a reader thread cannot be started.
            watcher.start()
            for reader in readers:
                reader.start()

    def send(self, message: JsonRpcMessage) -> None:
        """Write one message to the server's stdin.

        Raises ``McpConnectionClosedError`` when the transport was not
        started, is closed, or the server no longer reads.
        """

        payload = json.dumps(message, allow_nan=False, separators=(",", ":"))
        data = memoryview(payload.encode("utf-8") + b"\n")
        with self._write_lock:
            with self._lock:
                process = self._process
                if process is None or self._closed or self._ended:
                    raise McpConnectionClosedError()
                self._writing = True
            assert process.stdin is not None
            try:
                while data:
                    data = data[process.stdin.write(data) :]
            except BrokenPipeError:
                raise McpConnectionClosedError() from None
            finally:
                with self._lock:
                    self._writing = False
                    if self._closed or self._ended:
                        # The close found this write in progress and left
                        # the closing of stdin to it.
                        self._close_stdin_locked(process)

    def close(self) -> None:
        """Shut the server down and report the close.

        Closes the server's stdin and lets it exit; sends SIGTERM to its
        process group after ``STDIN_CLOSE_GRACE_MS`` and SIGKILL after a
        further ``close_timeout_ms``. The close is done when the server
        exited and its stdout and stderr closed, so the signals also reach a
        child in the group that holds them after the server exited, as with
        Pi. Returns when the close listeners ran. A second call, from any
        thread, returns at once.
        """

        with self._changed:
            if self._closed:
                return
            self._closed = True
            process = self._process
            ended = self._ended
            if threading.current_thread() in self._readers:
                self._closing_reader = threading.current_thread()
            self._changed.notify_all()
        if process is None:
            self.emit_close()
            return
        if ended:
            # The server is gone and the watcher thread reports the close. A
            # close listener that calls close() runs on that thread.
            if threading.current_thread() is not self._watcher:
                self._finished.wait()
            return
        self._close_stdin(process)
        close_timeout = max(0.0, self.options.close_timeout_ms / 1000)
        grace = min(STDIN_CLOSE_GRACE_MS / 1000, close_timeout)
        if self._finished.wait(grace):
            return
        self._signal_server(process, signal.SIGTERM)
        if self._finished.wait(close_timeout):
            return
        with self._changed:
            if not self._reaped:
                _signal_group(process.pid, signal.SIGKILL)
            # SIGKILL ends the group; anything else that holds the pipes left
            # the group and is not waited for.
            self._killed = True
            self._changed.notify_all()
        self._finished.wait()

    def _signal_server(
        self, process: subprocess.Popen[bytes], signum: signal.Signals
    ) -> None:
        with self._lock:
            if not self._reaped:
                _signal_group(process.pid, signum)

    def _close_stdin(self, process: subprocess.Popen[bytes]) -> None:
        """Close the server's stdin, unless a write is in progress.

        A write in progress may be blocked on a server that does not read, so
        it is not waited for; ``send`` closes stdin when that write ends.
        """

        with self._lock:
            if not self._writing:
                self._close_stdin_locked(process)

    def _close_stdin_locked(self, process: subprocess.Popen[bytes]) -> None:
        """Close stdin. The caller holds ``_lock`` and no write is in progress."""

        assert process.stdin is not None
        try:
            process.stdin.close()
        except OSError:
            pass  # The server is gone; there is nothing left to flush.

    def _watch_process(
        self,
        process: subprocess.Popen[bytes],
        wake_read: int,
        wake_write: int,
        readers: list[threading.Thread],
    ) -> None:
        """Wait for the server to exit, then report the close and clean up."""

        unreaped = self._wait_for_exit(process)
        with self._changed:
            # Wait for stdout and stderr to close: everything the server wrote
            # is delivered before the close is reported. While a child in the
            # server's group holds them, the server stays unreaped, so the
            # signals of close() reach the group. Not waited for: the pipe of
            # a listener that called close(), the pipes after close() sent
            # SIGKILL, and the pipes after close() once the server was reaped.
            while (
                self._open_readers - {self._closing_reader}
                and not self._killed
                and not (self._closed and self._reaped)
            ):
                self._changed.wait()
            if unreaped:
                self._release_server_locked(process)
            self._ended = True
        os.write(wake_write, b"\0")
        self._close_stdin(process)
        try:
            self.emit_close()
        except Exception as error:  # noqa: BLE001 - cross-thread capture: reported, close() must still return
            self._report(error)
        finally:
            self._finished.set()
        for reader in readers:
            if reader.ident is not None:
                reader.join()
        os.close(wake_read)
        os.close(wake_write)

    def _wait_for_exit(self, process: subprocess.Popen[bytes]) -> bool:
        """Block until the server exited. Returns whether it is still unreaped.

        With ``os.waitid`` the server is left unreaped, so its id stays
        reserved. Without it the server is polled for, and reaped while
        ``_lock`` and the tracking lock are held and its group is untracked
        in the same step, so no signal of ``close()`` or of the exit hook
        follows the reaping. A server the system reaped by itself (SIGCHLD
        ignored) is only untracked; the group is not signalled after that.
        """

        if hasattr(os, "waitid"):
            try:
                os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
            except ChildProcessError:
                pass  # Already reaped by the system.
            else:
                return True
        while True:
            with self._lock, _live_groups_lock:
                if process.poll() is not None:
                    _live_process_groups.pop(process.pid, None)
                    self._reaped = True
                    return False
            time.sleep(_REAP_POLL_SECONDS)

    def _release_server_locked(self, process: subprocess.Popen[bytes]) -> None:
        """Stop tracking the exited, unreaped server and reap it. The caller
        holds ``_lock``.

        After ``close()`` its group is sent SIGTERM first: children of the
        server that ignored stdin closing would otherwise outlive it.
        """

        if self._closed:
            _signal_group(process.pid, signal.SIGTERM)
        _untrack_process_group(process.pid)
        process.wait()
        self._reaped = True

    def _pump(
        self, stream: IO[bytes], wake_read: int, handle: Callable[[bytes], None]
    ) -> None:
        """Feed ``handle`` from ``stream`` until it ends or the transport does."""

        descriptor = stream.fileno()
        with selectors.DefaultSelector() as selector:
            selector.register(descriptor, selectors.EVENT_READ)
            selector.register(wake_read, selectors.EVENT_READ)
            while True:
                ready = {key.fd for key, _ in selector.select()}
                if wake_read in ready:
                    return
                try:
                    chunk = os.read(descriptor, _READ_BYTES)
                except OSError as error:
                    self._report(error)
                    return
                if not chunk:
                    return
                handle(chunk)

    def _read_stdout(self, stream: IO[bytes], wake_read: int) -> None:
        lines = _LineSplitter(self.options.max_message_bytes)

        def handle(chunk: bytes) -> None:
            for line in lines.feed(chunk):
                self._handle_line(line)

        try:
            with stream:
                self._pump(stream, wake_read, handle)
            if lines.has_partial_line:
                self._report(
                    McpProtocolError(
                        "MCP stdio server closed with an incomplete JSON-RPC message"
                    )
                )
        finally:
            self._stream_finished()

    def _handle_line(self, line: bytes | None) -> None:
        if line is None:
            self._report(
                McpProtocolError(
                    f"MCP stdio message exceeds {self.options.max_message_bytes} bytes"
                )
            )
            return
        text = line.decode("utf-8", errors="replace").removesuffix("\r")
        if not text.strip():
            return
        try:
            self.emit_message(
                parse_json_rpc_message(
                    json.loads(text, parse_constant=_reject_constant)
                )
            )
        except Exception as error:  # noqa: BLE001 - cross-thread capture: reported, the stream continues
            self._report(error)

    def _read_stderr(self, stream: IO[bytes], wake_read: int) -> None:
        max_bytes = self.options.max_stderr_bytes
        on_stderr = self.options.on_stderr
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        def handle(chunk: bytes) -> None:
            with self._stderr_lock:
                self._stderr_tail += chunk
                del self._stderr_tail[: max(0, len(self._stderr_tail) - max_bytes)]
            deliver(decoder.decode(chunk))

        def deliver(text: str) -> None:
            if on_stderr is None or not text:
                return
            try:
                on_stderr(text)
            except Exception as error:  # noqa: BLE001 - cross-thread capture: reported, stderr is still drained
                self._report(error)

        try:
            with stream:
                self._pump(stream, wake_read, handle)
            # A character cut off by the end of the stream.
            deliver(decoder.decode(b"", final=True))
        finally:
            self._stream_finished()

    def _stream_finished(self) -> None:
        with self._changed:
            self._open_readers.discard(threading.current_thread())
            self._changed.notify_all()

    def _report(self, error: Exception) -> None:
        try:
            self.emit_error(error)
        except Exception:  # noqa: BLE001 - a raising error listener must not end the calling thread
            pass
