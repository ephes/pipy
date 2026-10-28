"""Immutable host restart policy; no stored schema or attempt-history authority."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, cast


@dataclass(frozen=True, slots=True, init=False)
class RestartPolicy:
    limit: int | None

    def __init__(self, value: object = 5) -> None:
        limit: int | None = 5
        if type(value) is RestartPolicy:
            limit = value.limit
        elif type(value) in (int, float):
            try:
                number = float(cast(int | float, value))
            except OverflowError:
                number = math.nan
            if type(value) is float and number == math.inf:
                limit = None
            elif (
                math.isfinite(number)
                and number >= 1
                and (type(value) is float or int(number) == value)
            ):
                limit = math.floor(number)
        object.__setattr__(self, "limit", limit)

    def parked(self, row: dict[str, Any]) -> bool:
        # TaskRecord already owns the wider finite numeric counter domain.
        # Do not apply the narrower interrupted-cause counter validator here.
        return bool(
            self.limit is not None
            and row["state"]["status"] not in ("running", "completing", "terminal")
            and not row["abortRequested"]
            and "restarts" in row
            and row["restarts"] >= self.limit
        )

    def cleared(self, row: dict[str, Any]) -> dict[str, Any] | None:
        if not self.parked(row):
            return None
        result = dict(row)
        del result["restarts"]
        return result
