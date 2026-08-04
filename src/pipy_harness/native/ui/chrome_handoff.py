"""The value types of the extension-chrome ownership transaction.

Chrome ownership changes hands when a reload candidate attaches: writes that
arrive while ownership is undecided are queued on a handoff, then either replayed
onto the accepted sink or discarded with the refused candidate. These five
records are that transaction's vocabulary, and they are declaration-only -- no
behaviour, no terminal, no session.

They are public here because the transaction that consumes them is moving out of
the terminal-UI shell; a leading underscore would only mean "private to a file
they no longer live in".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pipy_harness.native.extension_chrome_state import ExtensionChromeSink


@dataclass(frozen=True, slots=True)
class ChromeAcceptanceResult:
    """Ownership result for one post-commit chrome acceptance attempt."""

    accepted: bool
    diagnostic: str | None = None
    retired_sink: ExtensionChromeSink | None = None
    candidate_closed: bool = False


@dataclass(slots=True)
class ChromeHandoffOperation:
    """One retained write admitted while chrome ownership is undecided."""

    kind: str
    values: tuple[object, ...]
    cancelled: bool = False
    live_disposer: Callable[[], None] | None = None


@dataclass(slots=True)
class ChromeHandoff:
    """Short-guard state that queues writes until acceptance selects an owner."""

    candidate: ExtensionChromeSink
    pending: list[ChromeHandoffOperation] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ChromeHandoffLease:
    """Exact installed handoff and the owner retained during acquisition."""

    previous: ExtensionChromeSink
    handoff: ChromeHandoff


@dataclass(frozen=True, slots=True)
class ChromeRoutingLease:
    """Explicit source route for synchronous reentrant chrome writes."""

    source: str
    sink: ExtensionChromeSink
