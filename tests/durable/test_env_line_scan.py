"""Exact scanner fields, ingress, lifetime and allocation bounds."""

import tracemalloc
from dataclasses import FrozenInstanceError

import pytest

from pipy_harness.native.durable.env.line_scan import LineScan, LineScanner


def scan(
    data: bytes, start: int = 0, end: int | None = None, size: int = 1
) -> LineScan:
    scanner = LineScanner(start, end)
    scanner.push(b"")
    for offset in range(0, len(data), size):
        scanner.push(data[offset : offset + size])
        scanner.push(b"")
    return scanner.finish()


def expected(data: bytes, start: int, end: int | None) -> LineScan:
    starts = [0] + [i + 1 for i, byte in enumerate(data) if byte == 10]
    count = len(starts) - 1
    if start >= len(starts):
        return LineScan(count, len(data), len(data), len(data), len(data), 0, 0)
    last = min(end if end is not None else len(starts), len(starts)) - 1
    stop = starts[last + 1] - 1 if last < count else len(data)
    first_stop = starts[start + 1] - 1 if start < count else len(data)

    def size(a: int, b: int) -> int:
        raw = data[a:b]
        if a == 0 and raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        return len(raw.decode("utf-8", "replace").encode("utf-8"))

    return LineScan(
        count,
        starts[start],
        stop,
        first_stop,
        starts[last],
        size(starts[start], stop),
        size(starts[start], first_stop),
    )


GOLDENS = [
    b"abc\xef\xbb\xbf",
    b"abc\n\xef\xbb\xbf",
    b"",
    b"a",
    b"\n",
    b"\n\n",
    b"a\nb\nc\n",
    b"a\r\nb\r\n",
    b"\xef\xbb\xbf",
    b"\xef",
    b"\xef\xbb",
    b"\xef\xbb\xbfa\n",
    b"\xe2\x82\xef\xbb\xbfa",
    b"a\n\xef\xbb\xbf",
    b"\xff\n\xe2\x82",
    b"\xf0\x9f\x98\x80\na",
    b"\xe2\n\x80\n\xf0\x9f",
]


@pytest.mark.parametrize("data", GOLDENS)
@pytest.mark.parametrize("size", [1, 2, 3, 7, 65536])
def test_all_fields_golden_ranges(data, size):
    for start in range(data.count(b"\n") + 3):
        for end in [None, start + 1, start + 2, start + 9]:
            assert scan(data, start, end, size) == expected(data, start, end)


@pytest.mark.parametrize("value", [True, -1, 1.5, float("inf"), 2**53, "0", object()])
def test_invalid_start(value):
    with pytest.raises(ValueError):
        LineScanner(value)


@pytest.mark.parametrize(
    "value", [True, -1, 0, 1.5, float("inf"), 2**53, "1", object()]
)
def test_invalid_end(value):
    with pytest.raises(ValueError):
        LineScanner(0, value)


def test_lifecycle_and_immutable_result():
    scanner = LineScanner(0)
    scanner.push(b"\xef")
    result = scanner.finish()
    assert result.selected_bytes == 3
    with pytest.raises(FrozenInstanceError):
        result.start = 1
    with pytest.raises(RuntimeError):
        scanner.finish()
    with pytest.raises(RuntimeError):
        scanner.push(b"")


def test_foreign_ingress_never_calls_hooks_or_changes_state():
    class Foreign(int):
        def __lt__(self, other):
            raise AssertionError("foreign hook")

    class Binary(bytes):
        def __len__(self):
            raise AssertionError("foreign hook")

    for args in [(Foreign(0),), (0, Foreign(1))]:
        with pytest.raises(ValueError):
            LineScanner(*args)
    scanner = LineScanner(0)
    for chunk in [Binary(b"x"), bytearray(b"x"), memoryview(b"x"), object(), None]:
        with pytest.raises(TypeError):
            scanner.push(chunk)
    scanner.push(b"ok")
    assert scanner.finish() == expected(b"ok", 0, None)
    assert LineScanner(2**53 - 1).finish().start == 0


@pytest.mark.parametrize("selected", [True, False])
def test_generator_memory_bound(selected):
    # 32 MiB emitted from a reused 8 KiB chunk, never assembled as one input.
    chunk = b"a" * 8191 + b"\n"
    scanner = LineScanner(0 if selected else 4096)
    tracemalloc.start()
    try:
        for _ in range(4096):
            scanner.push(chunk)
        scanner.push(b"\xe2\x82")
        result = scanner.finish()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 128 * 1024
    assert result.newlines == 4096
    assert result.selected_bytes == (4096 * 8192 + 3 if selected else 3)
    assert result.last_line_start == 4096 * 8192


def test_giant_chunk_has_bounded_decoded_temporaries():
    chunk = b"\xff" * (8 * 1024 * 1024)
    scanner = LineScanner(0)
    tracemalloc.start()
    try:
        scanner.push(chunk)
        result = scanner.finish()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.selected_bytes == len(chunk) * 3
    assert peak < 128 * 1024
