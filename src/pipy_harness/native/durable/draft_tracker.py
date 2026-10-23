"""Private operation provenance; no Session, Storage or candidate-value policy.

Only ``Tracker.prepare`` publishes owned operations and their explicitly named
replay. Views are callback-local, copied on placement, and revoked together.
Array baseline ranges and lazy container overlays avoid per-element metadata for
untouched values. All value walks are iterative, as in OwnedJson's admission.
"""

from __future__ import annotations

import threading
from bisect import bisect_left
from collections.abc import Callable, Iterator, MutableMapping, MutableSequence
from dataclasses import dataclass
from typing import Any, cast

from .codec import JsonValueError, OwnedJson
from .delta_ops import apply_operations, own_operations

_RESERVED = frozenset({"__proto__", "constructor", "prototype"})
_MISSING = object()


def _units(value: str) -> str:
    raw = value.encode("utf-16-le", "surrogatepass")
    return "".join(chr(raw[i] | raw[i + 1] << 8) for i in range(0, len(raw), 2))


def _scalar_equal(left: Any, right: Any) -> bool:
    if type(left) in (int, float) and type(right) in (int, float):
        return bool(left == right)
    if type(left) is not type(right) or type(left) in (dict, list, _Node):
        return False
    if type(left) is str:
        return _units(left) == _units(right)
    return left is right or bool(left == right)


def _equal(left: Any, right: Any) -> bool:
    """Chord JSON equality, on already owned plain trees, without recursion."""
    pending = [(left, right)]
    while pending:
        a, b = pending.pop()
        if _scalar_equal(a, b):
            continue
        if type(a) is not type(b):
            return False
        if type(a) is list and len(a) == len(b):
            pending.extend(zip(a, b, strict=True))
        elif type(a) is dict and len(a) == len(b):
            other = {_units(k): v for k, v in b.items()}
            for key, value in a.items():
                units = _units(key)
                if units not in other:
                    return False
                pending.append((value, other[units]))
        else:
            return False
    return True


@dataclass(eq=False, slots=True)
class _Entry:
    base_index: int | None
    value: Any


class _Array:
    """Nonoverlapping baseline runs, inserted runs, and sparse observed entries."""

    __slots__ = ("base", "entries", "length", "pieces", "structural")

    def __init__(self, base: list[Any]) -> None:
        self.base = base
        self.entries: dict[int, _Entry] = {}
        self.length = len(base)
        self.pieces: list[range | list[_Entry]] = [range(len(base))] if base else []
        self.structural = False

    def entry(self, index: int) -> _Entry:
        if index < 0:
            index += self.length
        if not 0 <= index < self.length:
            raise IndexError("Draft list index out of range")
        for piece in self.pieces:
            if index < len(piece):
                if type(piece) is list:
                    return piece[index]
                assert isinstance(piece, range)
                original = piece[index]
                entry = self.entries.get(original)
                if entry is None:
                    entry = self.entries[original] = _Entry(
                        original, self.base[original]
                    )
                return entry
            index -= len(piece)
        raise AssertionError("Array length invariant")

    def values(self) -> Iterator[Any]:
        for piece in self.pieces:
            if type(piece) is list:
                yield from (entry.value for entry in piece)
            else:
                assert isinstance(piece, range)
                for index in piece:
                    entry = self.entries.get(index)
                    yield self.base[index] if entry is None else entry.value

    def positions(self) -> dict[_Entry, int]:
        """Index only observed identities, once per preparation, not per path."""
        positions: dict[_Entry, int] = {}
        offset = 0
        observed = sorted(self.entries)
        for piece in self.pieces:
            if type(piece) is list:
                positions.update((entry, offset + i) for i, entry in enumerate(piece))
            else:
                assert isinstance(piece, range)
                lower, upper = min(piece[0], piece[-1]), max(piece[0], piece[-1]) + 1
                for at in range(
                    bisect_left(observed, lower), bisect_left(observed, upper)
                ):
                    original = observed[at]
                    positions[self.entries[original]] = (
                        offset + (original - piece.start) // piece.step
                    )
            offset += len(piece)
        return positions

    def cut(self, index: int) -> int:
        """Return a piece boundary, splitting at an owned integer coordinate."""
        offset = 0
        for i, piece in enumerate(self.pieces):
            end = offset + len(piece)
            if index == offset:
                return i
            if index < end:
                at = index - offset
                self.pieces[i : i + 1] = [piece[:at], piece[at:]]
                return i + 1
            offset = end
        return len(self.pieces)

    def splice(self, start: int, remove: int, values: list[Any]) -> list[_Entry]:
        if not remove and not values:
            return []
        inserted = [_Entry(None, value) for value in values]
        if start == self.length and not remove:
            if self.pieces and type(self.pieces[-1]) is list:
                tail = self.pieces[-1]
                assert isinstance(tail, list)
                tail.extend(inserted)
            else:
                self.pieces.append(inserted)
        else:
            first = self.cut(start)
            last = self.cut(start + remove)
            self.pieces[first:last] = [inserted] if inserted else []
        self.length += len(values) - remove
        self.structural = True
        return inserted

    def reverse(self) -> None:
        self.pieces = [piece[::-1] for piece in reversed(self.pieces)]
        self.structural = True


class _Node:
    __slots__ = (
        "array",
        "base",
        "change",
        "deletes",
        "dirty",
        "edge",
        "members",
        "overrides",
        "parent",
        "placement",
        "readded",
        "writes",
    )

    def __init__(
        self,
        change: Tracker,
        base: Any,
        parent: _Node | None = None,
        edge: str | _Entry | None = None,
        placement: bool = False,
    ) -> None:
        self.change, self.base, self.parent, self.edge = change, base, parent, edge
        self.placement = placement
        self.dirty = False
        self.members: dict[str, Any] = dict(base) if type(base) is dict else {}
        self.array = _Array(base) if type(base) is list else None
        self.writes: dict[str, None] = {}
        self.deletes: dict[str, None] = {}
        self.readded: dict[str, None] = {}
        self.overrides: dict[int, None] = {}

    def mark(self) -> None:
        if not self.dirty:
            self.dirty = True
            self.change._dirty.append(self)

    def child(self, key: Any) -> Any:
        edge = key if self.array is None else self.array.entry(key)
        value = self.members[edge] if type(edge) is str else edge.value
        if type(value) in (dict, list):
            value = _Node(self.change, value, self, edge)
            if type(edge) is str:
                self.members[edge] = value
            else:
                edge.value = value
        return value

    def set(self, key: Any, value: Any) -> None:
        if self.array is None:
            old = self.members.get(key, _MISSING)
            if _scalar_equal(old, value):
                return
            if key in self.deletes and key in self.base:
                self.readded[key] = None
            self.deletes.pop(key, None)
            self.writes[key] = None
            self.members[key] = self.placed(value, key)
        else:
            entry = self.array.entry(key)
            if _scalar_equal(entry.value, value):
                return
            entry.value = self.placed(value, entry)
            original = entry.base_index
            if original is not None:
                if _scalar_equal(self.base[original], value):
                    self.overrides.pop(original, None)
                else:
                    self.overrides[original] = None
        self.mark()

    def placed(self, value: Any, edge: str | _Entry) -> Any:
        if type(value) in (dict, list):
            return _Node(self.change, value, self, edge, True)
        return value

    def delete(self, key: str) -> None:
        del self.members[key]
        self.writes.pop(key, None)
        self.readded.pop(key, None)
        self.deletes[key] = None
        self.mark()

    def splice(self, start: int, remove: int, values: list[Any]) -> None:
        assert self.array is not None
        if not remove and not values:
            return
        inserted = self.array.splice(start, remove, values)
        for entry in inserted:
            entry.value = self.placed(entry.value, entry)
        self.mark()

    def ancestors(self, *, include_self: bool = False) -> Iterator[_Node]:
        current = self if include_self else self.parent
        while current is not None:
            yield current
            current = current.parent


def _contents(value: Any) -> tuple[bool, Iterator[tuple[Any, Any]]] | None:
    if type(value) is _Node:
        if value.array is None:
            return True, iter(value.members.items())
        return False, enumerate(value.array.values())
    if type(value) is dict:
        return True, iter(value.items())
    if type(value) is list:
        return False, enumerate(value)
    return None


def _materialize(value: Any) -> Any:
    """Copy raw owned values and overlay nodes without recursion or view creation."""
    contents = _contents(value)
    if contents is None:
        return value
    is_object, children = contents
    root: Any = {} if is_object else []
    stack = [(root, children)]
    while stack:
        target, children = stack[-1]
        try:
            key, child = next(children)
        except StopIteration:
            stack.pop()
            continue
        contents = _contents(child)
        result: Any = child if contents is None else ({} if contents[0] else [])
        if type(target) is dict:
            target[key] = result
        else:
            target.append(result)
        if contents is not None:
            stack.append((result, contents[1]))
    return root


class TrackerRevoked(TypeError):
    """The private tracking facade was used after preparation or discard."""


@dataclass(frozen=True, slots=True)
class PreparedOperations:
    ops: OwnedJson
    replayed_value: OwnedJson


class Tracker:
    """One pure working change; J supplies its existing driver guard at integration.

    ``replayed_value`` is the exact normalized replay. Owner policy266 selects
    it as the loaded runtime candidate in ``tx``; this helper remains pure.
    """

    __slots__ = ("_base", "_check", "_dirty", "_open", "_root", "_thread", "_views")

    def __init__(
        self, base: OwnedJson, check: Callable[[], None] | None = None
    ) -> None:
        if type(base) is not OwnedJson:
            raise TypeError("Tracker requires owned JSON")
        tree = base.thaw()
        if type(tree) not in (dict, list):
            raise TypeError("Tracker root must be a container")
        self._base, self._check = base, check
        self._thread = threading.get_ident()
        self._open = True
        self._dirty: list[_Node] = []
        self._views: dict[_Node, _Map | _List] = {}
        self._root = _Node(self, tree)

    def _guard(self) -> None:
        if not self._open:
            raise TrackerRevoked("Draft is revoked")
        if self._thread != threading.get_ident():
            raise RuntimeError("Draft used outside its owning thread")
        if self._check is not None:
            self._check()
        if not self._open:
            raise TrackerRevoked("Draft is revoked")

    @property
    def root(self) -> Any:
        self._guard()
        return self._view(self._root)

    def _view(self, value: Any) -> Any:
        if type(value) is not _Node:
            return value
        cached = self._views.get(value)
        if cached is None:
            cached = _Map(value) if value.array is None else _List(value)
            self._views[value] = cached
        return cached

    def _place(self, value: Any) -> Any:
        if type(value) in (_Map, _List):
            value._node.change._guard()
            value = _materialize(value._node)
        result = OwnedJson(value).thaw()
        self._guard()
        return result

    def prepare(self) -> PreparedOperations:
        self._guard()
        self._open = False  # Revoke before validation/replay can fail or escape.
        self._views.clear()
        ops = own_operations(_emit(self))
        return PreparedOperations(ops, apply_operations(self._base, ops))

    def discard(self) -> None:
        if self._thread != threading.get_ident():
            raise RuntimeError("Draft used outside its owning thread")
        self._open = False
        self._views.clear()


def _overlap(before: str, after: str) -> int:
    if not before or not after:
        return 0
    tail = before[-65536:]
    for head_length in (min(64, len(after)), 1):
        head = after[:head_length]
        at = tail.find(head)
        for _ in range(8):
            if at < 0:
                break
            length = len(tail) - at
            if length <= len(after) and tail[at:] == after[:length]:
                return length
            at = tail.find(head, at + 1)
        if head_length == 1:
            break
    return 0


def _changed(out: list[Any], path: list[Any], before: Any, after: Any) -> None:
    if _equal(before, after):
        return
    if type(before) is str and type(after) is str:
        a, b = _units(before), _units(after)
        if len(b) > len(a) and b.startswith(a):
            out.append(["a", path, b[len(a) :]])
            return
        overlap = _overlap(a, b)
        if overlap:
            out.append(["t", path, len(a) - overlap])
            if len(b) > overlap:
                out.append(["a", path, b[overlap:]])
            return
    out.append(["s", path, after])


class _Paths:
    def __init__(self) -> None:
        self.paths: dict[_Node, list[Any] | None] = {}
        self.positions: dict[_Node, dict[_Entry, int]] = {}

    def indices(self, node: _Node) -> dict[_Entry, int]:
        if node not in self.positions:
            assert node.array is not None
            self.positions[node] = node.array.positions()
        return self.positions[node]

    def get(self, node: _Node) -> list[Any] | None:
        if node in self.paths:
            return self.paths[node]
        reversed_path: list[Any] = []
        current = node
        while current.parent is not None:
            parent, edge = current.parent, current.edge
            if parent.array is None:
                assert type(edge) is str
                if parent.members.get(edge) is not current:
                    self.paths[node] = None
                    return None
                reversed_path.append(edge)
            else:
                assert type(edge) is _Entry
                index = self.indices(parent).get(edge)
                if index is None or edge.value is not current:
                    self.paths[node] = None
                    return None
                reversed_path.append(index)
            current = parent
        result = list(reversed(reversed_path))
        self.paths[node] = result
        return result


def _dense_regions(
    change: Tracker, paths: _Paths
) -> dict[_Node, list[tuple[int, int]]]:
    candidates: dict[_Node, set[int]] = {}
    for node in change._dirty:
        child = node
        for parent in node.ancestors():
            if parent.array is not None:
                edge = child.edge
                if (
                    not parent.array.structural
                    and type(edge) is _Entry
                    and edge.base_index is not None
                    and edge.value is child
                ):
                    candidates.setdefault(parent, set()).add(edge.base_index)
                break
            child = parent
        if node.array is not None and not node.array.structural:
            candidates.setdefault(node, set()).update(node.overrides)
    result = {}
    for node, indices in candidates.items():
        path = paths.get(node)
        if len(indices) < 256 or path is None or any(k in _RESERVED for k in path):
            continue
        regions = _regions(sorted(indices))
        if regions:
            result[node] = regions
    return result


def _regions(indices: list[int]) -> list[tuple[int, int]]:
    result = []
    start = end = indices[0]
    count = 1
    for index in indices[1:] + [indices[-1] + 3]:
        if index - end > 2:
            length = end - start + 1
            if count >= 256 and count * 2 >= length:
                result.append((start, length))
            start, count = index, 0
        end = index
        count += 1
    return result


def _inside(index: int, regions: list[tuple[int, int]]) -> bool:
    return any(start <= index < start + length for start, length in regions)


def _covered(
    node: _Node,
    path: list[Any],
    regions: dict[_Node, list[tuple[int, int]]],
    paths: _Paths,
) -> bool:
    for parent in node.ancestors():
        if parent not in regions:
            continue
        parent_path = paths.get(parent)
        if parent_path is None or len(path) <= len(parent_path):
            continue
        index = path[len(parent_path)]
        if path[: len(parent_path)] == parent_path and type(index) is int:
            if _inside(index, regions[parent]):
                return True
    return False


def _object_ops(node: _Node, path: list[Any], out: list[Any]) -> None:
    for key in node.readded:
        if key in node.base:
            out.append(["d", path + [key]])
    for key in node.writes:
        if len(out) > 4096:
            return
        before = _MISSING if key in node.readded else node.base.get(key, _MISSING)
        _changed(out, path + [key], before, _materialize(node.members[key]))
    for key in node.deletes:
        if len(out) > 4096:
            return
        if key in node.base:
            out.append(["d", path + [key]])


def _structural_ops(node: _Node, path: list[Any], out: list[Any]) -> None:
    assert node.array is not None
    pieces = node.array.pieces
    ranges = [piece for piece in pieces if type(piece) is range]
    intervals = sorted(
        (min(run[0], run[-1]), max(run[0], run[-1]) + 1) for run in ranges
    )
    gaps = []
    end = 0
    for start, stop in intervals:
        if start > end:
            gaps.append((end, start - end))
        end = stop
    if end < len(node.base):
        gaps.append((end, len(node.base) - end))
    for start, length in reversed(gaps):
        out.append(["p", path, start, length, []])
        if len(out) > 4096:
            return
    ordered = all(run.step == 1 or len(run) == 1 for run in ranges) and all(
        a[-1] < b[0] for a, b in zip(ranges, ranges[1:], strict=False)
    )
    if not ordered:
        retained = [index for run in ranges for index in run]
        ranks = {index: rank for rank, index in enumerate(sorted(retained))}
        out.append(["m", path, [ranks[index] for index in retained]])
    _insert_ops(pieces, path, out)


def _insert_ops(
    pieces: list[range | list[_Entry]], path: list[Any], out: list[Any]
) -> None:
    offset, start = 0, 0
    inserted: list[Any] = []
    for piece in [*pieces, range(0)]:
        if type(piece) is list:
            if not inserted:
                start = offset
            inserted.extend(_materialize(entry.value) for entry in piece)
        elif inserted:
            out.append(["p", path, start, 0, inserted])
            inserted = []
            if len(out) > 4096:
                return
        offset += len(piece)


def _array_ops(
    node: _Node,
    path: list[Any],
    out: list[Any],
    regions: list[tuple[int, int]],
    paths: _Paths,
) -> None:
    assert node.array is not None
    if regions:
        values = list(node.array.values())
        for start, length in regions:
            out.append(
                ["p", path, start, length, _materialize(values[start : start + length])]
            )
            if len(out) > 4096:
                return
    if node.array.structural:
        _structural_ops(node, path, out)
    indices = paths.indices(node)
    for original in node.overrides:
        if len(out) > 4096:
            return
        entry = node.array.entries[original]
        index = indices.get(entry)
        if index is not None and not _inside(index, regions):
            _changed(
                out, path + [index], node.base[original], _materialize(entry.value)
            )


def _emission_paths(
    change: Tracker,
    paths: _Paths,
    regions: dict[_Node, list[tuple[int, int]]],
) -> tuple[dict[_Node, list[Any]], dict[_Node, list[Any]]]:
    emissions: dict[_Node, list[Any]] = {}
    folds: dict[_Node, list[Any]] = {}
    for node in change._dirty:
        path = paths.get(node)
        if path is None or _covered(node, path, regions, paths):
            continue
        emissions[node] = path
        if node.array is None and any(
            k in _RESERVED for k in (*node.writes, *node.deletes)
        ):
            folds[node] = path
        for index, key in enumerate(path):
            if key in _RESERVED:
                ancestor = node
                for _ in range(len(path) - index):
                    assert ancestor.parent is not None
                    ancestor = ancestor.parent
                folds[ancestor] = path[:index]
                break
    emissions.update(folds)
    for node in regions:
        path = paths.get(node)
        if path is not None and not _covered(node, path, regions, paths):
            emissions[node] = path
    return emissions, folds


def _emit(change: Tracker) -> list[Any]:
    paths = _Paths()
    regions = _dense_regions(change, paths)
    emissions, folds = _emission_paths(change, paths, regions)
    out: list[Any] = []
    folded = set()
    for node, path in sorted(emissions.items(), key=lambda pair: len(pair[1])):
        if any(a.placement for a in node.ancestors(include_self=True)):
            continue
        if any(a in folded for a in node.ancestors()):
            continue
        if node in folds:
            value = _materialize(node)
            out.append(["s", path, value] if path else ["r", value])
            folded.add(node)
            continue
        if node.array is None:
            _object_ops(node, path, out)
        else:
            _array_ops(node, path, out, regions.get(node, []), paths)
        if len(out) > 4096:
            return [["r", _materialize(change._root)]]
    return out


def _key(key: object, *, write: bool = False) -> str:
    if type(key) is not str:
        if write:
            raise TypeError("Draft object keys must be exact strings")
        raise KeyError("Draft object keys are exact strings")
    return key


def _distinct_key(members: dict[str, Any], key: str) -> None:
    if key in members or all(ord(c) < 0xD800 or 0xDFFF < ord(c) < 0x10000 for c in key):
        return
    units = _units(key)
    if any(_units(other) == units for other in members):
        raise JsonValueError("JSON object keys need distinct UTF-16 identities")


def _index(index: object) -> int | slice:
    if type(index) is slice and all(
        part is None or type(part) is int
        for part in (index.start, index.stop, index.step)
    ):
        return index
    if type(index) is not int:
        raise TypeError("Draft list indices must be exact integers or slices")
    return index


class _View:
    __slots__ = ("_node",)
    _node: _Node

    def __init__(self, node: _Node) -> None:
        if hasattr(self, "_node"):
            raise TypeError("Draft view is already initialized")
        object.__setattr__(self, "_node", node)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Drafts expose items, not attributes")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("Drafts expose items, not attributes")

    def __copy__(self) -> Any:
        raise TypeError("Drafts cannot be copied; use snapshot()")

    def __deepcopy__(self, memo: object) -> Any:
        raise TypeError("Drafts cannot be copied; use snapshot()")

    def __reduce_ex__(self, protocol: object) -> Any:
        raise TypeError("Drafts cannot be pickled; use snapshot()")

    def __reduce__(self) -> Any:
        raise TypeError("Drafts cannot be pickled; use snapshot()")

    def __getstate__(self) -> Any:
        raise TypeError("Drafts expose no state; use snapshot()")

    def __repr__(self) -> str:
        return "Draft(<open>)" if self._node.change._open else "Draft(<revoked>)"

    def snapshot(self) -> OwnedJson:
        self._node.change._guard()
        return OwnedJson(_materialize(self._node))

    def __eq__(self, other: object) -> bool:
        mine = self.snapshot().thaw()
        theirs = (
            cast(_View, other).snapshot().thaw()
            if type(other) in (_Map, _List)
            else other
        )
        return bool(mine == theirs)


def _keys(node: _Node, keys: list[str]) -> Iterator[str]:
    for key in keys:
        node.change._guard()
        yield key


class _Map(_View, MutableMapping[str, Any]):
    __slots__ = ()
    __hash__ = None  # type: ignore[assignment]

    def __getitem__(self, key: str) -> Any:
        self._node.change._guard()
        return self._node.change._view(self._node.child(_key(key)))

    def __setitem__(self, key: str, value: object) -> None:
        node = self._node
        node.change._guard()
        _key(key, write=True)
        _distinct_key(node.members, key)
        value = node.change._place(value)
        node.set(key, value)

    def __delitem__(self, key: str) -> None:
        self._node.change._guard()
        self._node.delete(_key(key))

    def __len__(self) -> int:
        self._node.change._guard()
        return len(self._node.members)

    def __iter__(self) -> Iterator[str]:
        self._node.change._guard()
        return _keys(self._node, list(self._node.members))

    def __contains__(self, key: object) -> bool:
        self._node.change._guard()
        return type(key) is str and key in self._node.members

    def pop(self, key: str, *default: object) -> Any:
        self._node.change._guard()
        if len(default) > 1:
            raise TypeError("pop expected at most 2 arguments")
        if type(key) is not str and default:
            return default[0]
        key = _key(key)
        if key not in self._node.members:
            if default:
                return default[0]
            raise KeyError(key)
        value = _materialize(self._node.members[key])
        self._node.delete(key)
        return value

    def popitem(self) -> tuple[str, Any]:
        self._node.change._guard()
        if not self._node.members:
            raise KeyError("popitem(): dictionary is empty")
        key = next(reversed(self._node.members))
        return key, self.pop(key)

    def clear(self) -> None:
        self._node.change._guard()
        for key in list(self._node.members):
            self._node.delete(key)

    def setdefault(self, key: str, default: object = None) -> Any:
        self._node.change._guard()
        if _key(key) not in self._node.members:
            self[key] = default
        return self[key]


def _items(view: _List) -> Iterator[Any]:
    index = 0
    while True:
        view._node.change._guard()
        if index >= len(view):
            return
        yield view[index]
        index += 1


class _List(_View, MutableSequence[Any]):
    __slots__ = ()
    __hash__ = None  # type: ignore[assignment]

    @property
    def _array(self) -> _Array:
        assert self._node.array is not None
        return self._node.array

    def __getitem__(self, index: Any) -> Any:
        self._node.change._guard()
        position = _index(index)
        if type(position) is slice:
            return _materialize(list(self._array.values())[position])
        return self._node.change._view(self._node.child(position))

    def __setitem__(self, index: Any, value: Any) -> None:
        node = self._node
        node.change._guard()
        position = _index(index)
        if type(position) is int:
            value = node.change._place(value)
            node.set(position, value)
            return
        # Foreign iteration is outside mutation. Reentry may settle/change the
        # draft; verify liveness again and resolve coordinates only afterwards.
        values = [node.change._place(item) for item in value]
        node.change._guard()
        assert isinstance(position, slice)
        start, stop, step = position.indices(self._array.length)
        if step == 1:
            node.splice(start, max(0, stop - start), values)
            return
        positions = range(start, stop, step)
        if len(positions) != len(values):
            raise ValueError("Extended slice assignment length mismatch")
        for at, item in zip(positions, values, strict=True):
            node.set(at, item)

    def __delitem__(self, index: Any) -> None:
        self._node.change._guard()
        position = _index(index)
        if type(position) is int:
            self._array.entry(position)  # Validate before negative-index translation.
            start = position if position >= 0 else self._array.length + position
            self._node.splice(start, 1, [])
            return
        assert isinstance(position, slice)
        start, stop, step = position.indices(self._array.length)
        if step == 1:
            self._node.splice(start, max(0, stop - start), [])
        else:
            for at in sorted(range(start, stop, step), reverse=True):
                self._node.splice(at, 1, [])

    def __len__(self) -> int:
        self._node.change._guard()
        return self._array.length

    def __iter__(self) -> Iterator[Any]:
        self._node.change._guard()
        return _items(self)

    def __contains__(self, value: object) -> bool:
        return value in cast(list[Any], self.snapshot().thaw())

    def index(self, value: Any, start: int = 0, stop: int | None = None) -> int:
        self._node.change._guard()
        if type(start) is not int or (stop is not None and type(stop) is not int):
            raise TypeError("Draft list indices must be exact integers")
        items = cast(list[Any], self.snapshot().thaw())
        return int(items.index(value, start, len(items) if stop is None else stop))

    def count(self, value: Any) -> int:
        return cast(list[Any], self.snapshot().thaw()).count(value)

    def insert(self, index: int, value: object) -> None:
        self._node.change._guard()
        if type(index) is not int:
            raise TypeError("Draft list indices must be exact integers")
        value = self._node.change._place(value)
        length = self._array.length
        start = max(0, length + index) if index < 0 else min(index, length)
        self._node.splice(start, 0, [value])

    def pop(self, index: int = -1) -> Any:
        self._node.change._guard()
        if type(index) is not int:
            raise TypeError("Draft list indices must be exact integers")
        value = _materialize(self._array.entry(index).value)
        del self[index]
        return value

    def reverse(self) -> None:
        self._node.change._guard()
        if self._array.length > 1:
            self._array.reverse()
            self._node.mark()

    def clear(self) -> None:
        self._node.change._guard()
        self._node.splice(0, self._array.length, [])
