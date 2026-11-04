"""Immutable host restart policy; no stored schema or attempt-history authority."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

from .generation_tasks import LIMITS_RESTARTS


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

    def reached(self, row: dict[str, Any]) -> bool:
        """Whether the task was cut off at least `limit` times in a row."""
        # TaskRecord already owns the wider finite numeric counter domain.
        # Do not apply the narrower interrupted-cause counter validator here.
        return bool(
            self.limit is not None
            and "restarts" in row
            and row["restarts"] >= self.limit
        )

    def parked(self, row: dict[str, Any]) -> bool:
        # Built-in phases end their task at the limit instead (R15-2b).
        return bool(
            row["kind"] not in LIMITS_RESTARTS
            and row["state"]["status"] not in ("running", "completing", "terminal")
            and not row["abortRequested"]
            and self.reached(row)
        )


def number_text(value: int | float) -> str:
    """A finite stored count as JavaScript's ``String(number)`` spells it."""
    try:
        number = float(value)
    except OverflowError:
        return str(value)
    if number == 0:
        return "0"
    if number < 0:
        return "-" + number_text(-number)
    _, raw, exponent = Decimal(repr(number)).normalize().as_tuple()
    digits = "".join(str(digit) for digit in raw)
    size = len(digits)
    point = cast(int, exponent) + size
    if size <= point <= 21:
        return digits + "0" * (point - size)
    if 0 < point <= 21:
        return digits[:point] + "." + digits[point:]
    if -6 < point <= 0:
        return "0." + "0" * -point + digits
    shown = digits[0] + ("." + digits[1:] if size > 1 else "")
    power = point - 1
    return shown + "e" + ("+" if power >= 0 else "-") + str(abs(power))


def cut_off_count(restarts: int | float) -> str:
    """How often a task was cut off, for a message: "once" or "N times in a row"."""
    return "once" if restarts == 1 else number_text(restarts) + " times in a row"
