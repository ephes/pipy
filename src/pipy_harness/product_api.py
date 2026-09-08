"""Synchronous, full-content product embedding over the native coding lifetime.

Use ``create_product_session`` to construct a session. Conversation and lifecycle
remain native-owned; this facade confines entry and bridges active cancellation.
"""

from __future__ import annotations

import io
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from types import TracebackType
from typing import Never, TextIO, cast

from pipy_harness.adapters.native import CodingSessionAdapter
from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import AgentEventSink, ProductContent
from pipy_harness.native.cancellation import _AcceptedAbortSignal
from pipy_harness.native.coding.state import CodingSessionResultSnapshot
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.repl.wiring import _PreparedCodingSession
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tools import ToolPort
from pipy_harness.native.workspace_context import (
    default_workspace_instruction_loader,
    empty_workspace_instruction_loader,
)


class _OperationAbortBridge:
    """One lock protects every access to the current operation's fresh latch."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: _AcceptedAbortSignal | None = None

    def begin(self) -> _AcceptedAbortSignal:
        latch = _AcceptedAbortSignal()
        with self._lock:
            if self._active is not None:
                raise RuntimeError("product session operation already active")
            self._active = latch
        return latch

    def retire(self, latch: _AcceptedAbortSignal) -> None:
        with self._lock:
            if self._active is latch:
                self._active = None

    def _capture(self) -> _AcceptedAbortSignal | None:
        with self._lock:
            return self._active

    def cancel(self) -> None:
        latch = self._capture()
        if latch is not None:
            latch.set()

    def is_set(self) -> bool:
        latch = self._capture()
        return latch is not None and latch.is_set()

    def register_cancel_callback(
        self, callback: Callable[[], None]
    ) -> Callable[[], None]:
        latch = self._capture()
        if latch is None:
            return lambda: None
        return latch.register_cancel_callback(callback)


class _HeadlessStream(io.TextIOBase):
    """Discard presentation or forward diagnostic write fragments, without storage."""

    def __init__(self, sink: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self._sink = sink

    def write(self, text: str) -> int:
        if text and self._sink is not None:
            self._sink(text)
        return len(text)

    def readline(self, size: int | None = -1) -> Never:
        raise RuntimeError("product session must not read frontend input")


class ProductSession:
    """A persistent coding session; construct with ``create_product_session``.

    All operations except ``cancel`` run on the construction thread and refuse
    callback reentry. Use a context manager or explicitly close the idle session.
    """

    def __init__(
        self,
        adapter: CodingSessionAdapter,
        workspace: Path,
        abort: _OperationAbortBridge,
        diagnostic_sink: Callable[[str], None] | None,
    ) -> None:
        self._thread = threading.current_thread()
        self._entry_in_progress = True
        self._abort = abort
        self._scope: AbstractContextManager[_PreparedCodingSession] | None = None
        try:
            context = adapter.prepare_session_context(workspace)
            self._session = adapter.build_session(context)
            scope = self._session._open_lifetime(
                workspace_root=context.cwd,
                system_prompt=context.system_prompt,
                input_stream=cast(TextIO, _HeadlessStream()),
                output_stream=cast(TextIO, _HeadlessStream()),
                error_stream=cast(TextIO, _HeadlessStream(diagnostic_sink)),
            )
            self._prepared = scope.__enter__()
            self._scope = scope
            failure = self._prepared.wiring.startup_failure
            if failure is not None:
                raise RuntimeError(
                    failure.error_message or "product session startup failed"
                )
        except BaseException as error:
            self._exit_scope(type(error), error, error.__traceback__)
            raise
        finally:
            self._entry_in_progress = False

    @contextmanager
    def _entry(self) -> Iterator[None]:
        if threading.current_thread() is not self._thread:
            raise RuntimeError(
                "product session operation requires its construction thread"
            )
        if self._entry_in_progress:
            raise RuntimeError("product session operations are not reentrant")
        self._entry_in_progress = True
        try:
            yield
        finally:
            self._entry_in_progress = False

    def submit(self, content: str) -> CodingSessionResultSnapshot:
        """Run literal provider-visible content through continuations to true idle."""

        with self._entry():
            if self._scope is None:
                raise RuntimeError("product session is closed")
            if not isinstance(content, str):
                raise TypeError("content must be a str")
            if not content.strip():
                raise ValueError("content must be nonempty")
            latch = self._abort.begin()
            try:
                self._prepared.enqueue_seed(ProductContent(content))
                result = self._prepared.drive()
                if result is not None:
                    self._exit_scope()
                    if result.status is HarnessStatus.FAILED:
                        raise RuntimeError(
                            f"{result.error_type or 'CodingSessionFailed'}: "
                            f"{result.error_message or 'product session terminated'}"
                        )
                return self._session._coding_state.result_snapshot()
            except BaseException as error:
                self._exit_scope(type(error), error, error.__traceback__)
                raise
            finally:
                self._abort.retire(latch)

    def cancel(self) -> None:
        """Signal the accepted operation from any thread; idle cancellation is inert."""

        self._abort.cancel()

    def snapshot(self) -> CodingSessionResultSnapshot:
        """Read the native immutable projection while idle, including after close."""

        with self._entry():
            return self._session._coding_state.result_snapshot()

    def close(self) -> None:
        """Dispose once on the construction thread while idle."""

        with self._entry():
            self._exit_scope()

    def _exit_scope(
        self,
        exc_type: type[BaseException] | None = None,
        exc: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        scope = self._scope
        self._scope = None
        if scope is not None:
            scope.__exit__(exc_type, exc, traceback)

    def __enter__(self) -> ProductSession:
        with self._entry():
            if self._scope is None:
                raise RuntimeError("product session is closed")
            return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        with self._entry():
            self._exit_scope(exc_type, exc, traceback)


def create_product_session(
    *,
    workspace: Path,
    provider: ProviderPort,
    tools: dict[str, ToolPort] | None = None,
    settings: SettingsManager | None = None,
    resources: RuntimeResourceOptions | None = None,
    tree: NativeSessionTree | None = None,
    observer: AgentEventSink | None = None,
    diagnostic_sink: Callable[[str], None] | None = None,
    load_context_files: bool = True,
) -> ProductSession:
    """Start an ephemeral product lifetime with an explicit tool-capable provider.

    Inject a private tree for persistence. Mutable injected settings and other
    resources must not be shared across simultaneously active lifetimes.
    Diagnostic sinks receive individual nonempty write fragments, synchronously.
    """

    if not isinstance(workspace, Path):
        raise TypeError("workspace must be a Path")
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace is not a directory: {workspace}")
    if not isinstance(load_context_files, bool):
        raise TypeError("load_context_files must be a bool")
    if diagnostic_sink is not None and not callable(diagnostic_sink):
        raise TypeError("diagnostic_sink must be callable")
    abort = _OperationAbortBridge()
    adapter = CodingSessionAdapter(
        provider=provider,
        tool_registry=tools,
        settings_manager=settings,
        resource_options=resources,
        native_session=tree,
        agent_event_sink=observer,
        abort_event=abort,
        instruction_loader=(
            default_workspace_instruction_loader
            if load_context_files
            else empty_workspace_instruction_loader
        ),
    )
    return ProductSession(adapter, workspace, abort, diagnostic_sink)
