"""Native owner for in-lifetime product-session transitions.

This deliberately contains no facade or transport imports.  It owns canonical
durable-path claims and composes the existing tree, extension-gate and history
rebuild callbacks supplied by the REPL wiring root.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pipy_harness.native.session_tree import NativeSessionTree


@dataclass(frozen=True, slots=True)
class ProductSessionTarget:
    session_id: str
    session_path: Path | None
    leaf_id: str | None


@dataclass(frozen=True, slots=True)
class ProductSessionTransitionResult:
    operation: Literal["fork", "clone", "new", "switch"]
    status: Literal["completed", "refused"]
    active: ProductSessionTarget
    previous: ProductSessionTarget | None
    refusal: (
        Literal[
            "ephemeral_source",
            "missing_leaf",
            "unknown_entry",
            "extension_refusal",
            "lease_conflict",
        ]
        | None
    )


@dataclass(frozen=True, slots=True)
class ProductSessionTransitionFailure:
    operation: Literal["fork", "clone", "new", "switch"]
    retained: ProductSessionTarget
    stage: Literal["load", "create", "publish", "rebuild"]
    published: bool


class ProductSessionTransitionError(RuntimeError):
    def __init__(self, failure: ProductSessionTransitionFailure) -> None:
        self.failure = failure
        super().__init__(
            f"product session {failure.operation} failed during {failure.stage}"
        )


_LEASE_LOCK = threading.Lock()
_LEASES: dict[Path, object] = {}


class _CanonicalSessionLease:
    __slots__ = ("path", "_token")

    def __init__(self, path: Path, token: object) -> None:
        self.path = path
        self._token: object | None = token

    def finish(self) -> None:
        with _LEASE_LOCK:
            token = self._token
            if token is None:
                return
            self._token = None
            if _LEASES.get(self.path) is token:
                del _LEASES[self.path]


class CanonicalSessionLeaseRegistry:
    @staticmethod
    def claim(path: Path) -> _CanonicalSessionLease:
        key = path.expanduser().resolve()
        token = object()
        with _LEASE_LOCK:
            if key in _LEASES:
                raise RuntimeError("persistent product session is already active")
            _LEASES[key] = token
        return _CanonicalSessionLease(key, token)


class CanonicalSessionLeaseSlot:
    """The sole mutable current-lease owner for one public lifetime."""

    __slots__ = ("_initial_bound", "_lock", "_current")

    def __init__(self, lease: _CanonicalSessionLease | None = None) -> None:
        self._lock = threading.Lock()
        self._current = lease
        self._initial_bound = lease is not None

    def bind_initial(self, lease: _CanonicalSessionLease | None) -> None:
        """Install the construction-time lease before a lifetime can start."""

        with self._lock:
            if self._initial_bound:
                raise RuntimeError("initial session lease is already bound")
            self._initial_bound = True
            self._current = lease

    def current_path(self) -> Path | None:
        with self._lock:
            return None if self._current is None else self._current.path

    def prepare(self, path: Path) -> "_LeaseHandoff":
        return _LeaseHandoff(self, CanonicalSessionLeaseRegistry.claim(path))

    def _publish(self, candidate: _CanonicalSessionLease) -> None:
        with self._lock:
            old = self._current
            self._current = candidate
        if old is not None:
            old.finish()

    def finish(self) -> None:
        with self._lock:
            current = self._current
            self._current = None
        if current is not None:
            current.finish()


class _LeaseHandoff:
    """Construction-thread, single-writer candidate claim before publication."""

    __slots__ = ("_slot", "lease", "_done")

    def __init__(
        self, slot: CanonicalSessionLeaseSlot, lease: _CanonicalSessionLease
    ) -> None:
        self._slot = slot
        self.lease = lease
        self._done = False

    def publish(self) -> None:
        if self._done:
            raise RuntimeError("candidate lease handoff is already complete")
        self._done = True
        self._slot._publish(self.lease)

    def abort(self) -> None:
        if not self._done:
            self._done = True
            self.lease.finish()


def target_for(tree: NativeSessionTree) -> ProductSessionTarget:
    return ProductSessionTarget(tree.header.id, tree.path, tree.leaf_id)


@dataclass(frozen=True, slots=True)
class SessionTransitionCoordinator:
    """Typed true-idle transition port, composed from existing owners."""

    workspace: Path
    get_tree: Callable[[], NativeSessionTree]
    set_tree: Callable[[NativeSessionTree], None]
    session_before_fork: Callable[[str | None], bool]
    session_before_switch: Callable[[str], bool]
    rebuild: Callable[[], None]
    clear_extension_inputs: Callable[[], None]

    def new_external(
        self, *, leases: CanonicalSessionLeaseSlot
    ) -> ProductSessionTransitionResult:
        """Create and adopt one empty persistent tree at external true idle."""

        return self._new(leases=leases)

    def new_admitted(  # pragma: no cover - future D6b3 private seam
        self, *, leases: CanonicalSessionLeaseSlot
    ) -> ProductSessionTransitionResult:
        """Use after a future transport has retained its exact control claim."""

        return self._new(leases=leases)

    def _new(
        self, *, leases: CanonicalSessionLeaseSlot
    ) -> ProductSessionTransitionResult:

        box: list[ProductSessionTransitionResult] = []
        error: list[BaseException] = []

        def work() -> None:  # noqa: C901
            try:
                source = self.get_tree()
                previous = target_for(source)
                if source.path is None or not source.persist:
                    box.append(
                        ProductSessionTransitionResult(
                            "new", "refused", previous, None, "ephemeral_source"
                        )
                    )
                    return
                source_path = source.path.expanduser().resolve()
                if leases.current_path() != source_path:
                    raise ProductSessionTransitionError(
                        ProductSessionTransitionFailure(
                            "new", previous, "publish", False
                        )
                    )
                if not self.session_before_switch("new"):
                    box.append(
                        ProductSessionTransitionResult(
                            "new", "refused", previous, None, "extension_refusal"
                        )
                    )
                    return
                try:
                    candidate = NativeSessionTree.create(
                        self.workspace, session_dir=source_path.parent
                    )
                except BaseException:  # noqa: BLE001 - translate public failure
                    raise ProductSessionTransitionError(
                        ProductSessionTransitionFailure(
                            "new", previous, "create", False
                        )
                    ) from None
                candidate_path = candidate.path
                if candidate_path is None:
                    raise ProductSessionTransitionError(
                        ProductSessionTransitionFailure(
                            "new", previous, "create", False
                        )
                    )
                try:
                    handoff = leases.prepare(candidate_path)
                except RuntimeError:
                    box.append(
                        ProductSessionTransitionResult(
                            "new", "refused", previous, None, "lease_conflict"
                        )
                    )
                    return
                self._publish_and_rebuild(
                    candidate, previous, operation="new", handoff=handoff
                )
                box.append(
                    ProductSessionTransitionResult(
                        "new", "completed", target_for(candidate), previous, None
                    )
                )
            except BaseException as exc:  # noqa: BLE001 - preserve typed error
                error.append(exc)

        work()
        if error:
            raise error[0]
        return box[0]

    def switch_external(
        self, session_path: Path, *, leases: CanonicalSessionLeaseSlot
    ) -> ProductSessionTransitionResult:
        """Strict-load and adopt one exact durable target at external true idle."""

        return self._switch(
            session_path, leases=leases, before_switch=self.session_before_switch
        )

    def switch_terminal(
        self,
        session_path: Path,
        *,
        leases: CanonicalSessionLeaseSlot,
        before_switch: Callable[[str], bool],
    ) -> ProductSessionTransitionResult:
        """Adopt a terminal-resolved target with its presentation-aware gate."""

        return self._switch(session_path, leases=leases, before_switch=before_switch)

    def switch_admitted(  # pragma: no cover - future D6b3 private seam
        self, session_path: Path, *, leases: CanonicalSessionLeaseSlot
    ) -> ProductSessionTransitionResult:
        """Use after a future transport has retained its exact control claim."""

        return self._switch(
            session_path, leases=leases, before_switch=self.session_before_switch
        )

    def _switch(
        self,
        session_path: Path,
        *,
        leases: CanonicalSessionLeaseSlot,
        before_switch: Callable[[str], bool],
    ) -> ProductSessionTransitionResult:

        target_path = session_path.expanduser().resolve()
        box: list[ProductSessionTransitionResult] = []
        error: list[BaseException] = []

        def work() -> None:  # noqa: C901
            try:
                source = self.get_tree()
                previous = target_for(source)
                source_path = (
                    source.path.expanduser().resolve()
                    if source.path is not None and source.persist
                    else None
                )
                if source_path == target_path:
                    box.append(
                        ProductSessionTransitionResult(
                            "switch", "completed", previous, previous, None
                        )
                    )
                    return
                if leases.current_path() != source_path:
                    raise ProductSessionTransitionError(
                        ProductSessionTransitionFailure(
                            "switch", previous, "publish", False
                        )
                    )
                if not before_switch(str(target_path)):
                    box.append(
                        ProductSessionTransitionResult(
                            "switch", "refused", previous, None, "extension_refusal"
                        )
                    )
                    return
                try:
                    handoff = leases.prepare(target_path)
                except RuntimeError:
                    box.append(
                        ProductSessionTransitionResult(
                            "switch", "refused", previous, None, "lease_conflict"
                        )
                    )
                    return
                try:
                    candidate = NativeSessionTree.open(handoff.lease.path, strict=True)
                    header_cwd = Path(candidate.header.cwd)
                    if (
                        not header_cwd.is_absolute()
                        or header_cwd.expanduser().resolve() != self.workspace
                    ):
                        raise ValueError("native session workspace does not match")
                except BaseException:  # noqa: BLE001 - translate public failure
                    handoff.abort()
                    raise ProductSessionTransitionError(
                        ProductSessionTransitionFailure(
                            "switch", previous, "load", False
                        )
                    ) from None
                self._publish_and_rebuild(
                    candidate, previous, operation="switch", handoff=handoff
                )
                box.append(
                    ProductSessionTransitionResult(
                        "switch", "completed", target_for(candidate), previous, None
                    )
                )
            except BaseException as exc:  # noqa: BLE001 - preserve typed error
                error.append(exc)

        work()
        if error:
            raise error[0]
        return box[0]

    def _publish_and_rebuild(
        self,
        candidate: NativeSessionTree,
        previous: ProductSessionTarget,
        *,
        operation: Literal["new", "switch"],
        handoff: _LeaseHandoff,
    ) -> None:
        try:
            self.set_tree(candidate)
        except BaseException:  # noqa: BLE001 - translate public failure
            handoff.abort()
            raise ProductSessionTransitionError(
                ProductSessionTransitionFailure(operation, previous, "publish", False)
            ) from None
        handoff.publish()
        try:
            self.rebuild()
            self.clear_extension_inputs()
        except BaseException:  # noqa: BLE001 - translate public failure
            raise ProductSessionTransitionError(
                ProductSessionTransitionFailure(
                    operation, target_for(candidate), "rebuild", True
                )
            ) from None

    def fork_external(
        self,
        entry_id: str | None,
        *,
        operation: Literal["fork", "clone"],
        leases: CanonicalSessionLeaseSlot,
    ) -> ProductSessionTransitionResult:
        """Use the facade's completed synchronous drive as the idle proof.

        No queue/admission guard is held while callbacks, I/O, lease work, tree
        publication, or history rebuild occur.
        """
        return self._fork(entry_id, operation=operation, leases=leases)

    def fork_admitted(  # pragma: no cover - future D6b3 private seam
        self,
        entry_id: str | None,
        *,
        operation: Literal["fork", "clone"],
        leases: CanonicalSessionLeaseSlot,
    ) -> ProductSessionTransitionResult:
        """Use after a future transport has retained its exact control claim."""
        return self._fork(entry_id, operation=operation, leases=leases)

    def _fork(  # noqa: C901
        self,
        entry_id: str | None,
        *,
        operation: Literal["fork", "clone"],
        leases: CanonicalSessionLeaseSlot,
    ) -> ProductSessionTransitionResult:
        box: list[ProductSessionTransitionResult] = []
        error: list[BaseException] = []

        def work() -> None:  # noqa: C901
            try:
                source = self.get_tree()
                previous = target_for(source)
                if source.path is None or not source.persist:
                    box.append(
                        ProductSessionTransitionResult(
                            operation, "refused", previous, None, "ephemeral_source"
                        )
                    )
                    return
                source_path = source.path.expanduser().resolve()
                if leases.current_path() != source_path:
                    raise ProductSessionTransitionError(
                        ProductSessionTransitionFailure(
                            operation, previous, "publish", False
                        )
                    )
                if entry_id is None:
                    selected = source.leaf_id
                    if selected is None:
                        box.append(
                            ProductSessionTransitionResult(
                                operation, "refused", previous, None, "missing_leaf"
                            )
                        )
                        return
                else:
                    selected = entry_id
                    if not source.has_entry(selected):
                        box.append(
                            ProductSessionTransitionResult(
                                operation, "refused", previous, None, "unknown_entry"
                            )
                        )
                        return
                if not self.session_before_fork(selected):
                    box.append(
                        ProductSessionTransitionResult(
                            operation, "refused", previous, None, "extension_refusal"
                        )
                    )
                    return
                try:
                    child = NativeSessionTree.fork_from_snapshot(
                        source,
                        self.workspace,
                        leaf_id=selected,
                        session_dir=source_path.parent,
                    )
                except BaseException:  # noqa: BLE001 - translated at public boundary
                    raise ProductSessionTransitionError(
                        ProductSessionTransitionFailure(
                            operation, previous, "create", False
                        )
                    ) from None
                child_path = child.path
                if child_path is None:
                    raise ProductSessionTransitionError(
                        ProductSessionTransitionFailure(
                            operation, previous, "create", False
                        )
                    )
                try:
                    handoff = leases.prepare(child_path)
                except RuntimeError:
                    box.append(
                        ProductSessionTransitionResult(
                            operation, "refused", previous, None, "lease_conflict"
                        )
                    )
                    return
                try:
                    self.set_tree(child)
                except BaseException:  # noqa: BLE001 - translated at public boundary
                    handoff.abort()
                    raise ProductSessionTransitionError(
                        ProductSessionTransitionFailure(
                            operation, previous, "publish", False
                        )
                    ) from None
                handoff.publish()
                try:
                    self.rebuild()
                    self.clear_extension_inputs()
                except BaseException:  # noqa: BLE001 - translated at public boundary
                    raise ProductSessionTransitionError(
                        ProductSessionTransitionFailure(
                            operation, target_for(child), "rebuild", True
                        )
                    ) from None
                box.append(
                    ProductSessionTransitionResult(
                        operation, "completed", target_for(child), previous, None
                    )
                )
            except BaseException as exc:  # noqa: BLE001 - preserve callback error
                error.append(exc)

        work()
        if error:
            raise error[0]
        return box[0]
