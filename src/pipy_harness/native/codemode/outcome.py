"""Value types of one codemode script run (spike result §4.5, §4.6).

These are plain data shared by the host runner (:mod:`.host`) and whoever
calls it. They carry no behaviour that touches a process, a pipe or a thread.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import Enum, StrEnum
from typing import Any, Final

KIB = 1024
MIB = 1024 * KIB

# The worker's own bounds on its run request (worker.WASM32_MEMORY_MAX and
# worker.CPU_SECONDS_MAX; the host never imports the worker, a test pins that
# they match). Limits beyond them are refused here, not by the worker.
MEMORY_BYTES_MAX = 4 * 1024 * MIB
# Above Pi's longest ``timeout_ms`` (2^31 - 1 ms) plus the CPU margin; also
# the backstop of a script without a deadline.
CPU_SECONDS_MAX = 25 * 24 * 3600.0


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

    ``output_chars`` bounds what a script emits, its text and the base64
    data of the images it shows with ``image()`` together, in characters
    (Pi ``MAX_OUTPUT_CHARS``); ``output_messages`` counts its ``text()``,
    ``print()`` and ``image()`` calls (Pi ``MAX_OUTPUT_ITEMS``). Past either
    the script fails. An image line may be as long as ``output_chars``
    allows; longer text is sent in pieces that fit ``line_bytes``.

    ``wall_seconds`` covers the whole run, nested tool time included; as in
    Pi there is none by default (``None``), and a script sets one with its
    ``timeout_ms`` option. The guest's epoch backstop is set
    ``cpu_margin_seconds`` above it, because the epoch counts wall time, time
    blocked on a tool reply included, so the host's wall deadline must fire
    first. Without a deadline the backstop is :data:`CPU_SECONDS_MAX`.
    """

    wall_seconds: float | None = None
    memory_bytes: int = 256 * MIB
    code_bytes: int = 256 * KIB
    line_bytes: int = 1 * MIB
    reply_bytes: int = 16 * MIB
    output_chars: int = 16 * MIB
    output_messages: int = 100_000
    stderr_bytes: int = 64 * KIB
    cpu_margin_seconds: float = 5.0

    def __post_init__(self) -> None:
        for limit in fields(self):
            value = getattr(self, limit.name)
            if limit.name == "wall_seconds" and value is None:
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{limit.name} must be a positive finite number")
        if not isinstance(self.memory_bytes, int):
            raise ValueError("memory_bytes must be an int")
        if self.memory_bytes > MEMORY_BYTES_MAX:
            raise ValueError(f"memory_bytes must be at most {MEMORY_BYTES_MAX}")
        if self.cpu_seconds > CPU_SECONDS_MAX:
            raise ValueError(
                "wall_seconds + cpu_margin_seconds must be at most "
                f"{CPU_SECONDS_MAX:.0f} s"
            )

    @property
    def cpu_seconds(self) -> float:
        if self.wall_seconds is None:
            return CPU_SECONDS_MAX
        return self.wall_seconds + self.cpu_margin_seconds


class NoValue(Enum):
    """The type of :data:`NO_VALUE`."""

    NO_VALUE = "NO_VALUE"

    def __repr__(self) -> str:
        return "NO_VALUE"


NO_VALUE: Final = NoValue.NO_VALUE
"""A call result without a structured value (Pi ``undefined``); ``None``
is a value, the JSON ``null``."""


@dataclass(frozen=True, slots=True)
class CallResult:
    """What a tool callback returns for one ``tools.<name>(args)`` call.

    A result with a ``value`` (any JSON value, ``None`` included; the default
    :data:`NO_VALUE` means none) makes the call return that value, whether
    or not ``ok``: a tool
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
    value: Any = NO_VALUE


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
class StoreWrites:
    """What a successful script changed with ``store()`` (Pi
    ``CodemodeStoreWrites``): the new value of each key it set, as a JSON
    value, and the keys it deleted."""

    values: Mapping[str, Any] = field(default_factory=dict)
    deleted: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.values or self.deleted)


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
    ``store_writes`` is what the script changed with ``store()``; only a run
    without an error has any (Pi drops a failed script's writes).
    """

    error: ScriptError | None
    output: tuple[str | ScriptImage, ...] = ()
    calls: tuple[ToolCallRecord, ...] = ()
    wall_seconds: float = 0.0
    diagnostics: str = ""
    store_writes: StoreWrites = field(default_factory=StoreWrites)

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def texts(self) -> tuple[str, ...]:
        """The text messages of ``output``, without its images."""

        return tuple(item for item in self.output if isinstance(item, str))
