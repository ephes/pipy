"""Owned finite JSON values, independent of the verification trace codec.

OwnedJson accepts already parsed values with an iterative copier and no fixed
nesting cutoff. Normal details have a separate iterative native JSON snapshot.
No product payload is rendered in diagnostics.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Any, TypeAlias

JsonScalar: TypeAlias = str | bool | int | float | None
Token: TypeAlias = tuple[str, JsonScalar]
IdentityToken: TypeAlias = tuple[str, JsonScalar | bytes]


def has_only_string_keys(value: object) -> bool:
    """Check descendants after stdlib JSON validation has excluded cycles."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if any(type(key) is not str for key in item):
                return False
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return True


def _normal_scalar(item: Any) -> object:
    """Use builtin scalar values without invoking subclass presentation hooks."""
    kind = type(item)
    if item is None or kind is bool:
        return item
    if issubclass(kind, str):
        return str.__str__(item)
    if issubclass(kind, int):
        return int.__int__(item)
    if issubclass(kind, float):
        result = float.__float__(item)
        if math.isfinite(result):
            return result
    raise ValueError("Unsupported or nonfinite JSON scalar")


def copy_normal_json(value: object) -> object:
    """Detach native JSON values without a lossy text roundtrip or depth gate.

    Tuple descendants become arrays; strings keep their Python representation.
    Durable ingress separately applies OwnedJson's strict binary64 domain.
    """
    root: list[Any] = [None]
    work: list[tuple[bool, Any, Any, Any]] = [(False, value, root, 0)]
    active: set[int] = set()
    try:
        while work:
            leaving, item, parent, key = work.pop()
            if leaving:
                active.remove(id(item))
                continue
            kind = type(item)
            copied: Any
            if issubclass(kind, (dict, list, tuple)):
                if id(item) in active:
                    raise ValueError("JSON contains an ancestor cycle")
                if issubclass(kind, dict):
                    rows = list(item.items())
                    if any(not issubclass(type(k), str) for k, _ in rows):
                        raise ValueError("JSON object keys must be strings")
                    rows = [(str.__str__(k), child) for k, child in rows]
                    copied = dict.fromkeys(k for k, _ in rows)
                else:
                    rows = list(enumerate(item))
                    copied = [None] * len(rows)
                parent[key] = copied
                active.add(id(item))
                work.append((True, item, None, None))
                work.extend(
                    (False, child, copied, child_key)
                    for child_key, child in reversed(rows)
                )
                continue
            parent[key] = _normal_scalar(item)
        return root[0]
    except (TypeError, ValueError, RecursionError, OverflowError):
        raise ValueError("Tool details must be finite JSON") from None


class JsonValueError(ValueError):
    """A value cannot be represented as owned finite binary64 JSON."""


def _number(value: int | float) -> None:
    try:
        number = float(value)
    except OverflowError as error:
        raise JsonValueError("JSON number exceeds finite binary64") from error
    if not math.isfinite(number) or (type(value) is int and int(number) != value):
        raise JsonValueError("JSON number has no exact finite binary64 value")


def _tokens(value: object) -> tuple[Token, ...]:
    output: list[Token] = []
    active: set[int] = set()
    work: list[tuple[str, object]] = [("value", value)]
    while work:
        operation, item = work.pop()
        if operation == "end":
            active.remove(id(item))
            output.append(("end", None))
        elif operation == "key":
            assert type(item) is str
            output.append(("key", item))
        elif item is None:
            output.append(("null", None))
        elif type(item) is bool:
            output.append(("bool", item))
        elif type(item) is str:
            output.append(("string", item))
        elif type(item) is int or type(item) is float:
            _number(item)
            output.append(("number", item))
        elif type(item) is list or type(item) is dict:
            _container(item, active, output, work)
        else:
            raise JsonValueError("JSON requires plain containers and scalar values")
    return tuple(output)


def _container(
    item: list[object] | dict[str, object],
    active: set[int],
    output: list[Token],
    work: list[tuple[str, object]],
) -> None:
    identity = id(item)
    if identity in active:
        raise JsonValueError("JSON contains an ancestor cycle")
    active.add(identity)
    output.append(("array" if type(item) is list else "object", None))
    work.append(("end", item))
    if isinstance(item, list):
        work.extend(("value", child) for child in reversed(item))
    else:
        keys: set[bytes] = set()
        for key, child in reversed(list(item.items())):
            if type(key) is not str:
                raise JsonValueError("JSON object requires exact string keys")
            units = key.encode("utf-16-be", "surrogatepass")
            if units in keys:
                raise JsonValueError("JSON object keys need distinct UTF-16 identities")
            keys.add(units)
            work.append(("value", child))
            work.append(("key", key))


@dataclass(frozen=True, slots=True, init=False, repr=False)
class OwnedJson:
    """Owned representation equality: ordered tokens, Python types, float bits."""

    _tokens: tuple[Token, ...] = field(repr=False, compare=False)
    _identity: tuple[IdentityToken, ...] = field(repr=False)

    def __init__(self, value: object) -> None:
        tokens = _tokens(value)
        object.__setattr__(self, "_tokens", tokens)
        # Derive only from validated exact scalars, without revisiting caller data.
        object.__setattr__(
            self,
            "_identity",
            tuple(
                (kind, struct.pack(">d", scalar) if type(scalar) is float else scalar)
                for kind, scalar in tokens
            ),
        )

    def thaw(self) -> object:
        """Return an independently owned plain tree on every call."""
        root: object = None
        stack: list[list[object]] = []
        for kind, value in self._tokens:
            if kind == "key":
                stack[-1][1] = value
                continue
            if kind == "end":
                stack.pop()
                continue
            child: object = (
                [] if kind == "array" else ({} if kind == "object" else value)
            )
            if stack:
                container, key = stack[-1]
                if type(container) is list:
                    container.append(child)
                else:
                    assert type(container) is dict and type(key) is str
                    container[key] = child
            else:
                root = child
            if kind in {"array", "object"}:
                stack.append([child, None])
        return root

    def __repr__(self) -> str:
        return "OwnedJson(<private value>)"


def encode_value(value: OwnedJson) -> dict[str, object]:
    """Versioned tree encoding; no JSON text parser or wire-compatibility claim."""
    if type(value) is not OwnedJson:
        raise JsonValueError("Expected an owned JSON value")
    return {"schema_version": 1, "value": value.thaw()}


def decode_value(tree: object) -> OwnedJson:
    """Validate version and complete value domain while taking ownership."""
    if type(tree) is dict and any(type(key) is not str for key in tree):
        raise JsonValueError("JSON tree envelope requires exact string keys")
    if (
        type(tree) is not dict
        or set(tree) != {"schema_version", "value"}
        or type(tree["schema_version"]) is not int
        or tree["schema_version"] != 1
    ):
        raise JsonValueError("Unsupported owned JSON tree envelope")
    return OwnedJson(tree["value"])
