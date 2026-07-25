"""The session's live extension generation and its synchronization boundary.

This module is session-owned on purpose. The generation value will grow to
carry capability, renderer, provider, and chrome projections as the
transactional reload rebuild proceeds, and none of that may be dragged into
`extension_runtime.py` — the extension boundary must not import the
composition surface. Keeping the generation here lets it grow without
inverting that direction.

See `docs/specs/2026-07-25-transactional-extension-reload-rebuild.md` for the
concurrency contract this implements.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from pipy_harness.native.extension_runtime import _ExtensionRuntime


@dataclass(frozen=True, slots=True)
class SessionExtensionGeneration:
    """Canonical live extension state for one session generation.

    The activated runtime owns every registered contribution and the outbox
    lists extensions retain after activation. Parsed flag values belong beside
    that runtime because every command, hook, provider, tool, renderer, and UI
    dispatch must observe flags from the same live generation.
    """

    runtime: _ExtensionRuntime
    flag_values: dict[str, object]


@dataclass(frozen=True, slots=True)
class SessionGenerationSnapshot:
    """One operation's consistent view of the live generation.

    An operation reads this once at its start and reads extension-owned state
    from it for its whole duration, so a reload landing mid-operation cannot
    show it a mixture of two generations. ``generation_id`` identifies which
    generation the view came from; it is what later slices compare to reject a
    stale mutation.
    """

    generation: SessionExtensionGeneration
    generation_id: int


class SessionGenerationRef:
    """The session's single synchronization boundary and generation pointer.

    One `NativeToolReplSession.run()` owns exactly one of these. Its ``lock``
    is *the* session mutex: every field that a detached worker can reach — the
    live generation pointer here, and the tool-capability state pointer that is
    constructed with this same lock — is read and written under it. A lock that
    only one side of a race takes excludes nobody, so both sides take this one.

    The lock is reentrant because a mutation port may be invoked from inside
    another port on the session thread.

    Nothing slow runs inside the critical sections here: they read or assign a
    pointer and nothing else. Publication also hands back the value it replaced
    rather than letting it die under the lock, so a finalizer or weakref
    callback on a retired generation cannot run inside the critical section.
    """

    __slots__ = ("_lock", "_generation", "_generation_id", "_publication_pending")

    def __init__(
        self,
        generation: SessionExtensionGeneration,
        *,
        lock: "threading.RLock | None" = None,
    ) -> None:
        # The session creates the mutex before the first guarded owner exists
        # and hands the same object to each of them. Accepting it here keeps
        # this reference one *user* of the boundary rather than its owner.
        self._lock = lock if lock is not None else threading.RLock()
        self._generation = generation
        self._generation_id = 0
        self._publication_pending = False

    @property
    def lock(self) -> "threading.RLock":
        """The session mutex, shared with every owner of guarded state."""

        return self._lock

    @property
    def current(self) -> SessionExtensionGeneration:
        """The live generation. Prefer :meth:`snapshot` inside an operation."""

        with self._lock:
            return self._generation

    def snapshot(self) -> SessionGenerationSnapshot:
        """Take one consistent view for the whole of an operation.

        Not yet adopted by the composition root, which still reads
        :attr:`current` per access. That is sound *today* only because the sole
        publisher is `/reload`, which runs on the session thread: there is no
        concurrent writer for a multi-read operation to interleave with. It
        stops being sound the moment a detached worker can publish, so the
        consumer conversion is required by the slice that introduces
        generation-bound mutation ports, not deferred past it.
        """

        with self._lock:
            return SessionGenerationSnapshot(self._generation, self._generation_id)

    def publish(
        self, generation: SessionExtensionGeneration
    ) -> SessionExtensionGeneration:
        """Make ``generation`` live and return the one it replaced.

        Non-fallible by construction: a pointer assignment and an integer
        increment. The retired generation is returned rather than dropped so
        the caller holds it until after the lock is released.

        Publishing deliberately does **not** close the publication gate. A
        reload swaps this pointer partway through — before the provider
        selection, tool visibility, and renderer projections derived from it
        are republished — so clearing the gate here would reopen mutations for
        the rest of the reload and let an accepted change be overwritten by the
        projections still to come. :meth:`publishing` owns the gate for the
        whole publication.
        """

        with self._lock:
            retired = self._generation
            self._generation = generation
            self._generation_id += 1
        return retired

    @property
    def publication_pending(self) -> bool:
        """Whether a reload is between reading live state and publishing it."""

        with self._lock:
            return self._publication_pending

    @contextmanager
    def publishing(self) -> "Iterator[None]":
        """Open the publication gate for the duration of a reload.

        Generation-bound mutation ports fail closed while this is open. The
        window exists because a reload reads live provider selection, thinking
        level, and tool visibility, then republishes values derived from them
        some time later; a mutation accepted in between would be silently
        overwritten at the swap. Refusing it instead is the fail-closed
        direction, and the only callers that can hit the refusal are stragglers
        from an already-cancelled operation.

        The gate is opened and closed under the lock but is **not** held across
        the body, so no fallible or slow work runs inside a critical section.
        Closing is guaranteed even if the body raises: a reload whose candidate
        preparation fails must not leave every extension mutation refused for
        the rest of the session.
        """

        with self._lock:
            self._publication_pending = True
        try:
            yield
        finally:
            with self._lock:
                self._publication_pending = False
