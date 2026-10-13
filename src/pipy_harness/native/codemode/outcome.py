"""Value types of one codemode script run (spike result §4.5, §4.6).

These are plain data shared by the host runner (:mod:`.host`) and whoever
calls it. They carry no behaviour that touches a process, a pipe or a thread.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Any

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

    ``line_bytes`` bounds each line the guest sends. ``reply_bytes`` bounds
    each tool reply the host sends, as its encoded JSON line: 16 MiB, the size
    of the largest message an MCP transport accepts, so an untruncated
    structured result can reach the script. A larger reply becomes a
    ``ToolError`` in the script.

    ``output_bytes`` bounds the text a script emits and ``image_bytes`` the
    base64 data of the images it shows with ``image()``; ``output_messages``
    counts both. An image line may be as long as ``image_bytes`` allows.

    ``wall_seconds`` covers the whole run, nested tool time included. The
    guest's epoch backstop is set ``cpu_margin_seconds`` above it, because the
    epoch counts wall time, time blocked on a tool reply included, so the
    host's wall deadline must fire first.
    """

    wall_seconds: float = 120.0
    memory_bytes: int = 256 * MIB
    code_bytes: int = 256 * KIB
    line_bytes: int = 1 * MIB
    reply_bytes: int = 16 * MIB
    output_bytes: int = 1 * MIB
    output_messages: int = 100_000
    image_bytes: int = 16 * MIB
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

    A result with a ``value`` (any JSON value but ``None``, which means "no
    value") makes the call return that value, whether or not ``ok``: a tool
    that declares structured results returns them for its failures too.
    Otherwise ``ok`` with ``text`` becomes the script's return value, and a
    result that is not ``ok`` raises ``ToolError(text)``. ``ok`` also decides
    how the call is recorded. ``cancelled`` means the tool was stopped (by the
    operator's cancel or the run's stop signal): the run then ends and the
    call is recorded as cancelled.
    """

    ok: bool
    text: str
    cancelled: bool = False
    value: Any = None


@dataclass(frozen=True, slots=True)
class ScriptTool:
    """One tool a script may call.

    ``name`` is the tool's own name; the script reaches it as
    ``tools.<identifier>`` and by the name as a key. ``description`` is the
    text of the tool's ``ALL_TOOLS`` entry.
    """

    name: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """What a discovery callback returns for one ``search_tools``,
    ``describe_tool`` or ``describe_namespace`` call.

    The call returns ``value`` (any JSON value, ``None`` included), or raises
    ``ValueError(error)`` in the script when ``error`` is set.
    """

    value: Any = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ScriptImage:
    """One image a script showed with ``image()``: base64 data of a PNG,
    JPEG, GIF or WebP image and its type, both checked by the host."""

    data: str
    mime_type: str

    def __repr__(self) -> str:
        return f"ScriptImage({self.mime_type}, {len(self.data)} characters)"


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

    ``output`` holds the script's ``text``/``print`` messages and its
    ``image()`` images in order (at most the output caps). ``calls`` lists every tool call the callback ran,
    in order. ``diagnostics`` is the worker's capped stderr and exit report,
    for humans only; it is never parsed and never shown to the model.
    """

    error: ScriptError | None
    output: tuple[str | ScriptImage, ...] = ()
    calls: tuple[ToolCallRecord, ...] = ()
    wall_seconds: float = 0.0
    diagnostics: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def texts(self) -> tuple[str, ...]:
        """The text messages of ``output``, without its images."""

        return tuple(item for item in self.output if isinstance(item, str))
