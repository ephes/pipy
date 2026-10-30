"""Private caller-loop carriers for Product run and retirement ownership."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from .product_values import (
    DurableProductError,
    DurableRunResult,
    DurableRunSelection,
    DurableTaskRetry,
)


@dataclass(frozen=True, slots=True)
class RunOutcome:
    kind: Literal["success", "refused", "continuation_failed", "own_close"]
    code: str | None = None


@dataclass(slots=True)
class RunOperation:
    # Constructed behind a closed start gate. All readers/writers use the
    # Product's checked caller loop; native threads never see these carriers.
    selection: DurableRunSelection | None
    task: asyncio.Task[DurableRunResult]
    ack: asyncio.Future[DurableTaskRetry] | None
    outcome: RunOutcome | None = None
    handoff: ResumeHandoff | None = None
    # Set on the caller loop only after this Product actually requested close.
    close_requested: bool = False

    @property
    def acknowledged(self) -> bool:
        return (
            self.ack is not None
            and self.ack.done()
            and not self.ack.cancelled()
            and self.ack.exception() is None
        )

    def refuse_ack(self, error: DurableProductError) -> None:
        if self.ack is not None and not self.ack.done():
            self.ack.set_exception(error)


@dataclass(slots=True)
class ResumeHandoff:
    task: asyncio.Task[DurableRunResult]
    pending: bool = True
    target: RunOperation | None = None


@dataclass(frozen=True, slots=True)
class CloseOutcome:
    # Native cleanup exceptions propagate from the close task before this
    # optional continuation-latch report is produced.
    continuation_failed: bool = False

    def raise_error(self, *, body_failed: bool = False) -> None:
        if self.continuation_failed and not body_failed:
            raise DurableProductError("continuation_failed")
