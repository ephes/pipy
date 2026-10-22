"""Private decoded Chord operations over detached finite JSON (DE04b1a).

No tracker, wire dictionary, document-root policy or runtime integration lives
here. Every batch is owned before application; only its final owned result is
exposed. Thus even a late invalid path cannot mutate a caller or prior revision.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .codec import OwnedJson

_RESERVED = frozenset({"__proto__", "constructor", "prototype"})


class DeltaPathError(ValueError):
    """An operation cannot resolve its target in the private candidate."""


class DeltaUnsafePath(ValueError):
    """A path segment is reserved, nonintegral or invalid for an array."""


def _ordinal(value: Any) -> bool:
    return type(value) in (int, float) and value >= 0 and int(value) == value


def _validate(operation: Any) -> None:
    if type(operation) is not list or not operation:
        raise TypeError("op is not a tuple")
    tag = operation[0]
    if type(tag) is not str or tag not in {"r", "s", "d", "a", "t", "p", "m"}:
        raise TypeError("unknown op verb")
    arity = {"r": 2, "s": 3, "d": 2, "a": 3, "t": 3, "p": 5, "m": 3}[tag]
    if len(operation) != arity:
        raise TypeError(f"{tag} arity")
    if tag == "r":
        return
    _path(operation[1], tag in {"s", "d", "a", "t"})
    _payload(operation)


def _path(path: Any, nonempty: bool) -> None:
    if type(path) is not list:
        raise TypeError("path is not an array")
    if nonempty and not path:
        raise TypeError("path is empty")
    for segment in path:
        if type(segment) is str:
            if segment in _RESERVED:
                raise DeltaUnsafePath("reserved path segment")
        elif not _ordinal(segment):
            raise DeltaUnsafePath("invalid path segment")


def _permutation(permutation: Any) -> None:
    if type(permutation) is not list:
        raise TypeError("m permutation is not an array")
    seen: set[int] = set()
    for index in permutation:
        if not _ordinal(index) or index >= len(permutation) or int(index) in seen:
            raise TypeError("m permutation is not a bijection")
        seen.add(int(index))


def _payload(operation: Any) -> None:
    tag = operation[0]
    if tag == "a" and type(operation[2]) is not str:
        raise TypeError("a shape")
    if tag == "t" and not _ordinal(operation[2]):
        raise TypeError("t shape")
    if tag == "p":
        if not _ordinal(operation[2]):
            raise TypeError("p index")
        if not _ordinal(operation[3]):
            raise TypeError("p remove")
        if type(operation[4]) is not list:
            raise TypeError("p items")
    if tag == "m":
        _permutation(operation[2])


def own_operations(operations: object) -> OwnedJson:
    """Validate an exact decoded batch and retain no mutable caller alias.

    Numeric coordinates follow Number.isInteger, including huge finite integers;
    OwnedJson first enforces exact finite binary64 representation for Python ints.
    """
    owned = OwnedJson(operations)
    value = owned.thaw()
    if type(value) is not list:
        raise TypeError("operations is not an array")
    for operation in value:
        _validate(operation)
    return owned


def _property(segment: Any) -> str:
    if type(segment) is str:
        return segment
    # ECMAScript uses fixed notation in [1e-6, 1e21), and a shortest binary64
    # decimal elsewhere. Coordinates are integral and nonnegative here.
    number = float(segment)
    if number == 0:
        return "0"
    text = repr(number)
    if number < 1e21:
        return format(Decimal(text), "f").split(".")[0]
    mantissa, exponent = text.lower().split("e")
    return mantissa.removesuffix(".0") + "e+" + str(int(exponent))


def _key(container: Any, segment: Any) -> Any:
    if type(container) is list:
        if type(segment) is str:
            raise DeltaUnsafePath("array path requires a numeric segment")
        return int(segment)
    if type(container) is not dict:
        raise DeltaPathError("unresolvable path")
    key = _property(segment)
    if key in container:
        return key
    units = key.encode("utf-16-be", "surrogatepass")
    # JSON keys have UTF-16 identities, including scalar/surrogate spellings.
    return next(
        (
            candidate
            for candidate in container
            if candidate.encode("utf-16-be", "surrogatepass") == units
        ),
        key,
    )


def _walk(root: Any, path: list[Any]) -> Any:
    node = root
    for segment in path:
        key = _key(node, segment)
        if (type(node) is list and key >= len(node)) or (
            type(node) is dict and key not in node
        ):
            raise DeltaPathError("unresolvable path")
        node = node[key]
    if type(node) not in (dict, list):
        raise DeltaPathError("unresolvable path")
    return node


def apply_operations(value: OwnedJson, operations: OwnedJson) -> OwnedJson:
    """Apply a validated decoded batch atomically to any owned JSON root.

    The final root may be any JSON value. Future document Storage must enforce
    its separate object-root invariant. Inputs and output share no mutable tree.
    """
    if type(value) is not OwnedJson or type(operations) is not OwnedJson:
        raise TypeError("expected owned JSON")
    batch = operations.thaw()
    if type(batch) is not list:
        raise TypeError("operations is not an array")
    root: Any = value.thaw()
    for operation in batch:
        _validate(operation)
        tag = operation[0]
        if tag == "r":
            root = operation[1]
            continue
        if tag in {"p", "m"}:
            _array_operation(root, operation)
        else:
            _member_operation(root, operation)
    return OwnedJson(root)


def _array_operation(root: Any, operation: Any) -> None:
    target = _walk(root, operation[1])
    if type(target) is not list:
        raise DeltaPathError("unresolvable path")
    if operation[0] == "p":
        index = min(int(operation[2]), len(target))
        remove = min(int(operation[3]), len(target) - index)
        target[index : index + remove] = operation[4]
    else:
        if len(target) != len(operation[2]):
            raise DeltaPathError("unresolvable path")
        target[:] = [target[int(index)] for index in operation[2]]


def _member_operation(root: Any, operation: Any) -> None:
    tag, path = operation[:2]
    parent = _walk(root, path[:-1])
    key = _key(parent, path[-1])
    if type(parent) is list and key > len(parent):
        raise DeltaUnsafePath("array index exceeds length")
    present = key < len(parent) if type(parent) is list else key in parent
    if tag == "d":
        if type(parent) is list:
            if not present:
                raise DeltaPathError("unresolvable path")
            del parent[key]
        else:
            parent.pop(key, None)
        return
    if tag == "s":
        replacement = operation[2]
    else:
        if not present or type(parent[key]) is not str:
            raise DeltaPathError("unresolvable path")
        current = parent[key]
        units = (current + operation[2] if tag == "a" else current).encode(
            "utf-16-be", "surrogatepass"
        )
        if tag == "t":
            units = units[min(int(operation[2]), len(units) // 2) * 2 :]
        replacement = units.decode("utf-16-be", "surrogatepass")
    if type(parent) is list and key == len(parent):
        parent.append(replacement)
    else:
        parent[key] = replacement
