"""Value types of one codemode script run (spike result §4.5, §4.6).

These are plain data shared by the host runner (:mod:`.host`) and whoever
calls it. They carry no behaviour that touches a process, a pipe or a thread.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from enum import StrEnum

KIB = 1024
MIB = 1024 * KIB

# The worker's own bounds on its run request (worker.WASM32_MEMORY_MAX and
# worker.CPU_SECONDS_MAX; the host never imports the worker, a test pins that
# they match). Limits beyond them are refused here, not by the worker.
MEMORY_BYTES_MAX = 4 * 1024 * MIB
CPU_SECONDS_MAX = 24 * 3600.0


class ErrorKind(StrEnum):
    """Why a script run failed; a protocol violation is ``SANDBOX``."""

    SCRIPT = "script"
    TIMEOUT = "timeout"
    ABORTED = "aborted"
    SANDBOX = "sandbox"


class CallStatus(StrEnum):
    """How one nested tool call ended, as the "not undone" summary names it."""

    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ScriptLimits:
    """Per-script limits (all provisional, spike result §4.5).

    ``wall_seconds`` covers the whole run, nested tool time included. The
    guest's epoch backstop is set ``cpu_margin_seconds`` above it, because the
    epoch counts wall time, time blocked on a tool reply included, so the
    host's wall deadline must fire first.
    """

    wall_seconds: float = 120.0
    memory_bytes: int = 256 * MIB
    code_bytes: int = 256 * KIB
    line_bytes: int = 1 * MIB
    output_bytes: int = 1 * MIB
    output_messages: int = 100_000
    stderr_bytes: int = 64 * KIB
    cpu_margin_seconds: float = 5.0

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{field.name} must be a positive finite number")
        if not isinstance(self.memory_bytes, int):
            raise ValueError("memory_bytes must be an int")
        if self.memory_bytes > MEMORY_BYTES_MAX:
            raise ValueError(f"memory_bytes must be at most {MEMORY_BYTES_MAX}")
        if self.cpu_seconds > CPU_SECONDS_MAX:
            raise ValueError(
                "wall_seconds + cpu_margin_seconds must be at most "
                f"{CPU_SECONDS_MAX:g} s"
            )

    @property
    def cpu_seconds(self) -> float:
        return self.wall_seconds + self.cpu_margin_seconds


@dataclass(frozen=True, slots=True)
class CallResult:
    """What a tool callback returns for one ``tools.<name>(args)`` call.

    ``ok`` with ``text`` becomes the script's return value; otherwise the
    script gets ``ToolError(text)``. ``cancelled`` means the tool was stopped
    (by the operator's cancel or the run's stop signal): the run then ends and
    the call is recorded as cancelled.
    """

    ok: bool
    text: str
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    name: str
    status: CallStatus


@dataclass(frozen=True, slots=True)
class ScriptError:
    """A failed run. For ``SCRIPT`` errors raised by the script, ``message`` is
    the trimmed traceback (or ``Type: message`` when there is none)."""

    kind: ErrorKind
    message: str


@dataclass(frozen=True, slots=True)
class ScriptOutcome:
    """The result of one run; the runner never raises, it returns this.

    ``output`` holds the script's ``text``/``print`` messages in order (at
    most the output caps). ``calls`` lists every tool call the callback ran,
    in order. ``diagnostics`` is the worker's capped stderr and exit report,
    for humans only; it is never parsed and never shown to the model.
    """

    error: ScriptError | None
    output: tuple[str, ...] = ()
    calls: tuple[ToolCallRecord, ...] = ()
    wall_seconds: float = 0.0
    diagnostics: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None
