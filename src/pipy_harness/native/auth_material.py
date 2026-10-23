"""Immutable private request material under the existing provider auth authority.

This companion never changes auth.json's provider schema. Its random references
carry no content-derived information; all operations share auth_file_lock.
"""

from __future__ import annotations

import json
import math
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from .auth_store import auth_file_lock


class RequestMaterialError(ValueError):
    """Fixed diagnostics safe for persistent task faults."""


def _fail() -> NoReturn:
    raise RequestMaterialError(
        "Private request material is unavailable or incompatible"
    )


def _copy(value: object, active: set[int] | None = None) -> Any:
    active = set() if active is None else active
    if value is None or type(value) in (bool, str, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) not in (dict, list) or id(value) in active:
        _fail()
    active.add(id(value))
    try:
        if type(value) is list:
            return [_copy(item, active) for item in value]
        if type(value) is dict:
            if any(type(key) is not str for key in value):
                _fail()
            return {key: _copy(item, active) for key, item in value.items()}
        _fail()
    finally:
        active.remove(id(value))


def _same(left: object, right: object) -> bool:
    # Both values are owned finite private JSON. Python == conflates signed zero
    # and numeric kinds; immutable capture must retain the serialized value.
    return json.dumps(left, ensure_ascii=True, allow_nan=False) == json.dumps(
        right, ensure_ascii=True, allow_nan=False
    )


def _id(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 32
        and all(char in "0123456789abcdef" for char in value)
    )


@dataclass(frozen=True, slots=True)
class RequestMaterialRef:
    store_id: str
    material_id: str

    def __post_init__(self) -> None:
        if not _id(self.store_id) or not _id(self.material_id):
            _fail()

    def to_tree(self) -> dict[str, object]:
        if not _id(self.store_id) or not _id(self.material_id):
            _fail()
        return {
            "version": 1,
            "store_id": self.store_id,
            "material_id": self.material_id,
        }

    @classmethod
    def from_tree(cls, value: object) -> RequestMaterialRef:
        if type(value) is not dict or set(value) != {
            "version",
            "store_id",
            "material_id",
        }:
            _fail()
        if type(value["version"]) is not int or value["version"] != 1:
            _fail()
        return cls(value["store_id"], value["material_id"])


def _path(auth_path: Path) -> Path:
    return auth_path.with_name(f"{auth_path.name}.request-material")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            _fail()
        out[key] = value
    return out


def _read(path: Path, *, create: bool) -> dict[str, Any]:
    if path.is_symlink():
        _fail()
    try:
        body = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs)
    except FileNotFoundError:
        if not create:
            _fail()
        return {"version": 1, "store_id": uuid.uuid4().hex, "materials": {}}
    except (OSError, ValueError, RecursionError, UnicodeError):
        _fail()
    if (
        type(body) is not dict
        or set(body) != {"version", "store_id", "materials"}
        or type(body["version"]) is not int
        or body["version"] != 1
        or not _id(body["store_id"])
        or type(body["materials"]) is not dict
    ):
        _fail()
    for key, record in body["materials"].items():
        if (
            not _id(key)
            or type(record) is not dict
            or set(record) != {"binding", "value"}
        ):
            _fail()
    return dict(_copy(body))


def _write(path: Path, body: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.partial")
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(body, handle, ensure_ascii=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def protect_request_material(
    auth_path: Path,
    binding: object,
    material: object,
    *,
    material_id: str | None = None,
) -> RequestMaterialRef:
    """Put immutable private material before adopting its ref in Session.

    A persisted task memo may supply the random material_id. A losing candidate
    cannot overwrite its private winner. No credential resolution runs here.
    """
    try:
        record = {"binding": _copy(binding), "value": _copy(material)}
        key = uuid.uuid4().hex if material_id is None else material_id
        if not _id(key):
            _fail()
        with auth_file_lock(auth_path):
            path = _path(auth_path)
            body = _read(path, create=True)
            previous = body["materials"].get(key)
            if previous is not None and not _same(previous, record):
                _fail()
            if previous is None:
                body["materials"][key] = record
                _write(path, body)
            return RequestMaterialRef(body["store_id"], key)
    except (OSError, ValueError, RecursionError, UnicodeError):
        _fail()


def read_request_material(
    auth_path: Path, ref: RequestMaterialRef, binding: object
) -> object:
    """Read a detached bound record, never recreate a missing private ref."""
    try:
        if (
            type(ref) is not RequestMaterialRef
            or not _id(ref.store_id)
            or not _id(ref.material_id)
        ):
            _fail()
        expected = _copy(binding)
        with auth_file_lock(auth_path):
            body = _read(_path(auth_path), create=False)
            if body["store_id"] != ref.store_id:
                _fail()
            record = body["materials"].get(ref.material_id)
            if record is None or not _same(record["binding"], expected):
                _fail()
            return _copy(record["value"])
    except (OSError, ValueError, RecursionError, UnicodeError):
        _fail()
