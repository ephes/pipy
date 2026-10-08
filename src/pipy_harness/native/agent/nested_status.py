"""Content-free nested lifecycle status shared by events and settlement."""

from enum import StrEnum


class NestedCallStatus(StrEnum):
    UNFINISHED = "unfinished"
    SETTLED = "settled"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    UNAUTHORIZED = "unauthorized"
    BUDGET_EXHAUSTED = "budget_exhausted"
    MALFORMED = "malformed"
    INTERRUPTED = "interrupted"
    REFUSED = "refused"
