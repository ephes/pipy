"""Bounded progress admission, distinct from unrestricted historical JSON.

These limits are provisional native progress policy, not the canonical value
domain or a bound on process memory, Session graph scans or database traffic.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

from pipy_harness.native.agent.json_values import _normal_scalar
from pipy_harness.native.agent.tool_details import (
    LEGACY_TOOL_DETAILS_NONE,
    NO_TOOL_DETAILS,
    _forward_owned_tool_details,
)
from pipy_harness.native.tools.truncate import truncate_tail

from .codec import OwnedJson
from .generation_tasks import tool_input

SNAPSHOT_BYTES = 512 * 1024
SNAPSHOT_NODES = 32768
CAPTURE_PERMITS = 16
OUTPUT_BYTES = 50 * 1024
OUTPUT_LINES = 2000
PERIOD_SECONDS = 0.2
PERIOD_BYTES_PER_SECOND = 128 * 1024
# Reserve the error/truncation trailer independently of caller diagnostics.
TRAILER_CHARACTERS = 1024
DIAGNOSTIC_CHARACTERS = 64 * 1024 - OUTPUT_BYTES - TRAILER_CHARACTERS

FailureCategory = Literal[
    "invalid_progress", "progress_capacity", "invocation_exception", "invalid_result"
]
FAILURE_CATEGORIES = frozenset(
    ("invalid_progress", "progress_capacity", "invocation_exception", "invalid_result")
)


class ProgressRefused(ValueError):
    """A fixed, payload-free refusal before a progress epoch is installed."""

    def __init__(self, category: FailureCategory) -> None:
        self.category = category
        super().__init__("Durable tool progress refused: " + category)


def _string_bytes(value: str, remaining: int) -> int:
    # At least one ASCII byte per code point. Reject huge input before encoding
    # or slicing it; each temporary escape buffer is at most 6 * 1024 bytes.
    if len(value) + 2 > remaining:
        raise ProgressRefused("progress_capacity")
    size = 2
    for start in range(0, len(value), 1024):
        size += len(json.dumps(value[start : start + 1024], ensure_ascii=True)) - 2
        if size > remaining:
            raise ProgressRefused("progress_capacity")
    return size


@dataclass(frozen=True, slots=True)
class _CapturedTree:
    """Private detached preparation, never an installed reporter snapshot."""

    tree: object
    encoded_bytes: int
    nodes: int


@dataclass(frozen=True, slots=True)
class CapturedJson:
    value: OwnedJson
    encoded_bytes: int
    nodes: int


@dataclass(slots=True)
class _Capture:
    max_bytes: int
    max_nodes: int
    size: int = 0
    nodes: int = 0

    def check(self) -> None:
        if self.size > self.max_bytes or self.nodes > self.max_nodes:
            raise ProgressRefused("progress_capacity")

    def member(self, parent: Any, key: Any, root: list[Any]) -> Any:
        if type(parent) is dict:
            if type(key) is not str:
                raise ProgressRefused("invalid_progress")
            self.nodes += 1
            self.size += _string_bytes(key, self.max_bytes - self.size) + 1
        if parent is not root and len(parent):
            self.size += 1
        return key

    def node(self, item: object) -> tuple[Any, Iterator[tuple[Any, object]] | None]:
        self.nodes += 1
        self.check()
        kind = type(item)
        if type(item) is dict or type(item) is list:
            count = len(item)
            # Lower bounds before child expansion, including object keys.
            if self.nodes + count * (2 if kind is dict else 1) > self.max_nodes:
                raise ProgressRefused("progress_capacity")
            if self.size + 2 + max(0, count - 1) + count > self.max_bytes:
                raise ProgressRefused("progress_capacity")
            self.size += 2
            return (
                ({}, iter(item.items()))
                if type(item) is dict
                else ([], enumerate(item))
            )
        if type(item) is str:
            self.size += _string_bytes(item, self.max_bytes - self.size)
        elif item is None or kind in (bool, int, float):
            # Strict numeric admission precedes formatting giant integers.
            OwnedJson(item)
            self.size += len(json.dumps(item, allow_nan=False))
        else:
            raise ProgressRefused("invalid_progress")
        self.check()
        return item, None


def _copy_bounded(value: object, budget: _Capture) -> object:
    """Only bounded detached plain trees leave this lazy traversal."""
    root: list[Any] = [None]
    # iterator, destination, source identity; a frame advances one child only.
    frames: list[tuple[Iterator[tuple[Any, object]], Any, int | None]] = [
        (iter(((0, value),)), root, None)
    ]
    active: set[int] = set()
    try:
        while frames:
            rows, parent, identity = frames[-1]
            try:
                key, item = next(rows)
            except StopIteration:
                frames.pop()
                if identity is not None:
                    active.remove(identity)
                continue
            key = budget.member(parent, key, root)
            copied, children = budget.node(item)
            if children is not None:
                if id(item) in active:
                    raise ProgressRefused("invalid_progress")
                active.add(id(item))
                frames.append((children, copied, id(item)))
            if type(parent) is dict or parent is root:
                parent[key] = copied
            else:
                parent.append(copied)
        return root[0]
    except ProgressRefused:
        raise
    except (ValueError, TypeError, RuntimeError, OverflowError, RecursionError):
        raise ProgressRefused("invalid_progress") from None


def _capture_tree(
    value: object,
    *,
    max_bytes: int = SNAPSHOT_BYTES,
    max_nodes: int = SNAPSHOT_NODES,
) -> _CapturedTree:
    budget = _Capture(max_bytes, max_nodes)
    tree = _copy_bounded(value, budget)
    return _CapturedTree(tree, budget.size, budget.nodes)


def capture_json(
    value: object,
    *,
    max_bytes: int = SNAPSHOT_BYTES,
    max_nodes: int = SNAPSHOT_NODES,
) -> CapturedJson:
    """Bound lazy copying before strict ownership; never encode a whole input."""
    captured = _capture_tree(value, max_bytes=max_bytes, max_nodes=max_nodes)
    try:
        return CapturedJson(
            OwnedJson(captured.tree), captured.encoded_bytes, captured.nodes
        )
    except (ValueError, TypeError):
        raise ProgressRefused("invalid_progress") from None


class _NormalCapture(_Capture):
    """Pre-bound only new extension forwarding, preserving its normal domain."""

    def member(self, parent: Any, key: Any, root: list[Any]) -> Any:
        if type(parent) is dict and isinstance(key, str):
            if str.__len__(key) + 2 > self.max_bytes - self.size:
                raise ProgressRefused("progress_capacity")
            key = str.__str__(key)
        return super().member(parent, key, root)

    def node(self, item: object) -> tuple[Any, Iterator[tuple[Any, object]] | None]:
        if isinstance(item, (dict, list, tuple)):
            self.nodes += 1
            count = len(item)
            mapping = isinstance(item, dict)
            if self.nodes + count * (2 if mapping else 1) > self.max_nodes:
                raise ProgressRefused("progress_capacity")
            if self.size + 2 + max(0, count - 1) + count > self.max_bytes:
                raise ProgressRefused("progress_capacity")
            self.size += 2
            return (
                ({}, iter(item.items()))
                if isinstance(item, dict)
                else ([], enumerate(item))
            )
        if isinstance(item, str) and str.__len__(item) + 2 > self.max_bytes - self.size:
            raise ProgressRefused("progress_capacity")
        if (
            isinstance(item, int)
            and int.bit_length(item) // 4 > self.max_bytes - self.size
        ):
            raise ProgressRefused("progress_capacity")
        scalar = _normal_scalar(item)
        if type(scalar) is int:
            # A decimal digit carries fewer than four binary bits. This lower
            # bound refuses huge integers before any decimal formatting.
            if scalar.bit_length() // 4 > self.max_bytes - self.size:
                raise ProgressRefused("progress_capacity")
            self.nodes += 1
            self.size += len(json.dumps(scalar, allow_nan=False))
            self.check()
            return scalar, None
        return super().node(scalar)


def capture_forwarded_details(value: object) -> object:
    """Bound normal detachment, then use the unchanged JSON-text normalizer.

    Unsupported normal values keep the existing absent fallback. Capacity is
    a reporting refusal, never a silent loss of previously admitted details.
    UTF-16 identity validation belongs AFTER the established text roundtrip.
    """
    if value is NO_TOOL_DETAILS or value is LEGACY_TOOL_DETAILS_NONE:
        return value
    try:
        return _forward_owned_tool_details(
            _copy_bounded(value, _NormalCapture(SNAPSHOT_BYTES, SNAPSHOT_NODES))
        )
    except ProgressRefused as error:
        if error.category == "progress_capacity":
            raise
        return NO_TOOL_DETAILS
    except ValueError:
        return NO_TOOL_DETAILS


@dataclass(frozen=True, slots=True)
class Diagnostic:
    severity: Literal["info", "warning", "error"]
    message: str
    code: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.severity) is not str
            or self.severity not in ("info", "warning", "error")
            or type(self.message) is not str
            or self.code is not None
            and type(self.code) is not str
        ):
            raise ProgressRefused("invalid_progress")
        if len(self.message) + (0 if self.code is None else len(self.code)) > (
            DIAGNOSTIC_CHARACTERS
        ):
            raise ProgressRefused("progress_capacity")
        # Diagnostics become model text. Refuse unrepresentable code points
        # before admission, without a whole-message encoding workspace.
        try:
            for value in (self.message, self.code or ""):
                for start in range(0, len(value), 1024):
                    value[start : start + 1024].encode("utf-8")
        except UnicodeError:
            raise ProgressRefused("invalid_progress") from None

    def tree(self) -> dict[str, Any]:
        return {"severity": self.severity, "message": self.message, "code": self.code}

    def render(self) -> str:
        code = "" if self.code is None else " (" + self.code + ")"
        return "[" + self.severity + code + "] " + self.message


def diagnostics(value: object) -> tuple[Diagnostic, ...]:
    if type(value) is not list:
        raise ProgressRefused("invalid_progress")
    result = []
    characters = 0
    for row in value:
        if type(row) is not dict or set(row) != {"severity", "message", "code"}:
            raise ProgressRefused("invalid_progress")
        item = Diagnostic(**row)
        characters += len(item.render()) + 1
        if characters > DIAGNOSTIC_CHARACTERS:
            raise ProgressRefused("progress_capacity")
        result.append(item)
    return tuple(result)


def detail_tree(value: object) -> dict[str, Any]:
    return (
        {"present": False}
        if value is NO_TOOL_DETAILS
        else {
            "present": True,
            "value": _copy_bounded(value, _Capture(SNAPSHOT_BYTES, SNAPSHOT_NODES)),
        }
    )


def detail_value(value: object) -> object:
    if type(value) is not dict or type(value.get("present")) is not bool:
        raise ProgressRefused("invalid_progress")
    if value["present"]:
        if set(value) != {"present", "value"}:
            raise ProgressRefused("invalid_progress")
        return OwnedJson(value["value"]).thaw()
    if set(value) != {"present"}:
        raise ProgressRefused("invalid_progress")
    return NO_TOOL_DETAILS


def progress(value: object) -> OwnedJson:
    """Closed current-attempt progress; only committed instances recover."""
    captured = capture_json(value)
    tree = captured.value.thaw()
    keys = {
        "version",
        "call",
        "key",
        "attempts",
        "restarts",
        "output",
        "details",
        "diagnostics",
        "droppedBytes",
        "droppedLines",
    }
    if type(tree) is not dict or set(tree) != keys:
        raise ProgressRefused("invalid_progress")
    if type(tree["version"]) is not int or tree["version"] != 1:
        raise ProgressRefused("invalid_progress")
    tool_input(tree["call"])
    if type(tree["key"]) is not str or not tree["key"]:
        raise ProgressRefused("invalid_progress")
    for name in ("attempts", "restarts", "droppedBytes", "droppedLines"):
        number = tree[name]
        if name in ("attempts", "restarts") and number is None:
            continue
        if type(number) is not int or number < 0:
            raise ProgressRefused("invalid_progress")
    if type(tree["output"]) is not str:
        raise ProgressRefused("invalid_progress")
    if Tail().append(tree["output"]).text != tree["output"]:
        raise ProgressRefused("invalid_progress")
    detail_value(tree["details"])
    diagnostics(tree["diagnostics"])
    return captured.value


def initial_progress(
    call: object, key: str, *, attempts: int | None, restarts: int | None
) -> OwnedJson:
    return progress(
        {
            "version": 1,
            "call": call,
            "key": key,
            "attempts": attempts,
            "restarts": restarts,
            "output": "",
            "details": {"present": False},
            "diagnostics": [],
            "droppedBytes": 0,
            "droppedLines": 0,
        }
    )


@dataclass(frozen=True, slots=True)
class Tail:
    text: str = ""
    dropped_bytes: int = 0
    dropped_lines: int = 0
    _rolling: str = field(default="", repr=False)
    _total_bytes: int = 0
    _completed_lines: int = 0
    _open_line: bool = False
    _boundary: bool = True

    def append(self, value: str) -> Tail:
        """Bound temporary UTF-8/split work even for a huge caller-owned chunk."""
        if type(value) is not str:
            raise ProgressRefused("invalid_progress")
        # Keep rolling input separately from the displayed tail. truncate_tail
        # can omit a trailing newline; feeding its display back as input would
        # concatenate the next chunk onto the wrong line.
        rolling, total = self._rolling, self._total_bytes
        completed, opened, boundary = (
            self._completed_lines,
            self._open_line,
            self._boundary,
        )
        try:
            for start in range(0, len(value), 1024):
                chunk = value[start : start + 1024]
                total += len(chunk.encode("utf-8"))
                completed += chunk.count("\n")
                opened = not chunk.endswith("\n")
                encoded = (rolling + chunk).encode("utf-8")
                cut = max(0, len(encoded) - 2 * OUTPUT_BYTES)
                while cut < len(encoded) and encoded[cut] & 0xC0 == 0x80:
                    cut += 1
                if cut:
                    boundary = encoded[cut - 1] == 0x0A
                rolling = encoded[cut:].decode("utf-8")
            visible = rolling
            if not boundary and "\n" in visible:
                visible = visible[visible.index("\n") + 1 :]
            result = truncate_tail(
                visible, max_bytes=OUTPUT_BYTES, max_lines=OUTPUT_LINES
            )
        except UnicodeError:
            raise ProgressRefused("invalid_progress") from None
        return Tail(
            result.content,
            total - result.output_bytes,
            completed + int(opened) - result.output_lines,
            rolling,
            total,
            completed,
            opened,
            boundary,
        )
