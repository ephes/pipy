"""Private caller-loop carrier for Product run and retirement ownership."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .product_values import DurableRunResult, DurableRunSelection


@dataclass(slots=True)
class RunOperation:
    # Constructed behind a closed start gate. All readers/writers use the
    # Product's checked caller loop; native threads never see this carrier.
    selection: DurableRunSelection | None
    task: asyncio.Task[DurableRunResult]
    # Set on the caller loop only after this Product actually requested close.
    close_requested: bool = False
