"""Task structural equality and UTF-16 identity; document equality is unchanged."""

from __future__ import annotations

from typing import Any


def identity(value: str) -> bytes:
    if type(value) is not str:
        raise TypeError("Expected an exact string")
    return value.encode("utf-16-be", "surrogatepass")


def json_equal(left: Any, right: Any) -> bool:
    """Compare already-owned finite JSON without recursion or foreign hooks."""
    pending = [(left, right)]
    while pending:
        a, b = pending.pop()
        if type(a) in (int, float) and type(b) in (int, float):
            if a != b:
                return False
        elif type(a) is not type(b):
            return False
        elif type(a) is dict:
            if not _objects(a, b, pending):
                return False
        elif not _leaves(a, b, pending):
            return False
    return True


def _objects(
    a: dict[str, Any], b: dict[str, Any], pending: list[tuple[Any, Any]]
) -> bool:
    aa = {identity(k): v for k, v in a.items()}
    bb = {identity(k): v for k, v in b.items()}
    if aa.keys() != bb.keys():
        return False
    pending.extend((v, bb[k]) for k, v in aa.items())
    return True


def _leaves(a: Any, b: Any, pending: list[tuple[Any, Any]]) -> bool:
    if type(a) is list:
        if len(a) != len(b):
            return False
        pending.extend(zip(a, b, strict=True))
        return True
    if type(a) is str:
        return identity(a) == identity(b)
    return bool(a == b)
