"""Synchronous, full-content product embedding over the native coding lifetime.

Use ``create_product_session`` to construct a session. Conversation and lifecycle
remain native-owned; this facade confines entry and delegates cancellation to the
native managed-operation owner.
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
from pipy_harness.native.coding.input_queue import _ExternalAbortSignalView
from pipy_harness.native.coding.state import CodingSessionResultSnapshot
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.repl.session_transition import (
    CanonicalSessionLeaseRegistry,
    CanonicalSessionLeaseSlot,
    ProductSessionTarget,
    ProductSessionTransitionError,
    ProductSessionTransitionFailure,
    ProductSessionTransitionResult,
)
from pipy_harness.native.repl.wiring import _PreparedCodingSession
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tools import ToolPort
from pipy_harness.native.workspace_context import (
    default_workspace_instruction_loader,
    empty_workspace_instruction_loader,
)

__all__ = [
    "ProductSession",
    "ProductSessionTarget",
    "ProductSessionTransitionResult",
    "ProductSessionTransitionFailure",
    "ProductSessionTransitionError",
    "create_product_session",
    "open_product_session",
]


def _persistent_tree_path(tree: NativeSessionTree | None) -> Path | None:
    if tree is None or not tree.persist or tree.path is None:
        return None
    return tree.path


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
        abort: _ExternalAbortSignalView,
        diagnostic_sink: Callable[[str], None] | None,
        *,
        lease_slot: CanonicalSessionLeaseSlot | None = None,
    ) -> None:
        self._thread = threading.current_thread()
        self._entry_in_progress = True
        self._abort = abort
        # Attach before startup can invoke extension/observer callbacks.
        self._lease_slot = lease_slot or CanonicalSessionLeaseSlot()
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
            self._prepared.bind_external_abort_signal(abort)
            self._prepared.bind_transition_lease_slot(self._lease_slot)
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
            try:
                result = self._prepared.drive_external_operation(
                    ProductContent(content)
                )
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

    def cancel(self) -> None:
        """Signal the accepted operation from any thread; idle cancellation is inert."""

        self._abort.cancel()

    def fork(self, entry_id: str | None = None) -> ProductSessionTransitionResult:
        """Fork one exact active-tree branch into a fresh persistent child."""

        if entry_id is not None:
            if not isinstance(entry_id, str):
                raise TypeError("entry_id must be a str or None")
            if not entry_id:
                raise ValueError("entry_id must be nonempty")
        with self._entry():
            if self._scope is None:
                raise RuntimeError("product session is closed")
            try:
                return self._prepared.fork_product_session(entry_id, clone=False)
            except ProductSessionTransitionError as error:
                if error.failure.published:
                    self._exit_scope(type(error), error, error.__traceback__)
                raise

    def clone(self) -> ProductSessionTransitionResult:
        """Fork the active leaf while recording a distinct public operation."""

        with self._entry():
            if self._scope is None:
                raise RuntimeError("product session is closed")
            try:
                return self._prepared.fork_product_session(None, clone=True)
            except ProductSessionTransitionError as error:
                if error.failure.published:
                    self._exit_scope(type(error), error, error.__traceback__)
                raise

    def new_session(self) -> ProductSessionTransitionResult:
        """Replace the active persistent tree with one empty sibling tree."""

        with self._entry():
            if self._scope is None:
                raise RuntimeError("product session is closed")
            try:
                return self._prepared.new_product_session()
            except ProductSessionTransitionError as error:
                if error.failure.published:
                    self._exit_scope(type(error), error, error.__traceback__)
                raise

    def switch_session(self, session_path: Path) -> ProductSessionTransitionResult:
        """Strictly replace the active tree with one exact durable target."""

        with self._entry():
            if self._scope is None:
                raise RuntimeError("product session is closed")
            if not isinstance(session_path, Path):
                raise TypeError("session_path must be a Path")
            try:
                return self._prepared.switch_product_session(session_path)
            except ProductSessionTransitionError as error:
                if error.failure.published:
                    self._exit_scope(type(error), error, error.__traceback__)
                raise

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
        try:
            if scope is not None:
                scope.__exit__(exc_type, exc, traceback)
        finally:
            self._lease_slot.finish()

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


def _validate_product_session_inputs(
    *,
    workspace: Path,
    load_context_files: bool,
    diagnostic_sink: Callable[[str], None] | None,
) -> Path:
    if not isinstance(workspace, Path):
        raise TypeError("workspace must be a Path")
    resolved_workspace = workspace.expanduser().resolve()
    if not resolved_workspace.is_dir():
        raise ValueError(f"workspace is not a directory: {resolved_workspace}")
    if not isinstance(load_context_files, bool):
        raise TypeError("load_context_files must be a bool")
    if diagnostic_sink is not None and not callable(diagnostic_sink):
        raise TypeError("diagnostic_sink must be callable")
    return resolved_workspace


def _create_product_session(
    *,
    workspace: Path,
    provider: ProviderPort,
    tools: dict[str, ToolPort] | None,
    settings: SettingsManager | None,
    resources: RuntimeResourceOptions | None,
    tree: NativeSessionTree | None,
    observer: AgentEventSink | None,
    diagnostic_sink: Callable[[str], None] | None,
    load_context_files: bool,
    lease_slot: CanonicalSessionLeaseSlot | None,
) -> ProductSession:
    abort = _ExternalAbortSignalView()
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
    return ProductSession(
        adapter, workspace, abort, diagnostic_sink, lease_slot=lease_slot
    )


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

    resolved_workspace = _validate_product_session_inputs(
        workspace=workspace,
        load_context_files=load_context_files,
        diagnostic_sink=diagnostic_sink,
    )
    persistent_path = _persistent_tree_path(tree)
    lease = (
        CanonicalSessionLeaseRegistry.claim(persistent_path)
        if persistent_path
        else None
    )
    lease_slot = CanonicalSessionLeaseSlot(lease)
    try:
        if tree is not None and lease is not None:
            # The public lifetime must persist through the exact resolved target
            # it claimed, never a later-retargeted caller alias.
            tree.path = lease.path
        return _create_product_session(
            workspace=resolved_workspace,
            provider=provider,
            tools=tools,
            settings=settings,
            resources=resources,
            tree=tree,
            observer=observer,
            diagnostic_sink=diagnostic_sink,
            load_context_files=load_context_files,
            lease_slot=lease_slot,
        )
    except BaseException:
        if lease is not None:
            lease_slot.finish()
        raise


def open_product_session(
    *,
    workspace: Path,
    session_path: Path,
    provider: ProviderPort,
    tools: dict[str, ToolPort] | None = None,
    settings: SettingsManager | None = None,
    resources: RuntimeResourceOptions | None = None,
    observer: AgentEventSink | None = None,
    diagnostic_sink: Callable[[str], None] | None = None,
    load_context_files: bool = True,
) -> ProductSession:
    """Open one exact persistent native product session under ``workspace``.

    This is deliberately a fresh facade/lifetime, not an in-place resume.  The
    durable tree is strict-loaded only after its process-local public lease is
    claimed, then handed to the ordinary creation composition unchanged.
    """

    resolved_workspace = _validate_product_session_inputs(
        workspace=workspace,
        load_context_files=load_context_files,
        diagnostic_sink=diagnostic_sink,
    )
    if not isinstance(session_path, Path):
        raise TypeError("session_path must be a Path")
    lease = CanonicalSessionLeaseRegistry.claim(session_path)
    lease_slot = CanonicalSessionLeaseSlot(lease)
    try:
        tree = NativeSessionTree.open(lease.path, strict=True)
        header_cwd = Path(tree.header.cwd)
        if (
            not header_cwd.is_absolute()
            or header_cwd.expanduser().resolve() != resolved_workspace
        ):
            raise ValueError("native session workspace does not match")
        return _create_product_session(
            workspace=resolved_workspace,
            provider=provider,
            tools=tools,
            settings=settings,
            resources=resources,
            tree=tree,
            observer=observer,
            diagnostic_sink=diagnostic_sink,
            load_context_files=load_context_files,
            lease_slot=lease_slot,
        )
    except BaseException:
        lease_slot.finish()
        raise
